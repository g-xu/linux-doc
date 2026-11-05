## DMA
Linux 内核的 **DMA (Direct Memory Access)** 模块是驱动开发中最复杂但也最重要的部分之一。它允许硬件设备直接读取或写入系统内存，而无需 CPU 的持续干预，从而极大地提高了系统性能。

Linux 内核中的 DMA 子系统主要可以分为两大部分来理解：
1.  **DMA API (Mapping API)**：供设备驱动程序使用，用于管理内存缓存一致性、总线地址映射等。这是绝大多数驱动开发者接触的部分。
2.  **DMA Engine (Slave DMA)**：一套用于控制嵌入式 SoC 内部通用 DMA 控制器的框架（如用于 SPI、I2S 音频传输或内存拷贝加速）。

以下是对 Linux 内核 DMA 模块的详细介绍。

---

### 1. 核心概念与挑战

在深入 API 之前，必须理解为什么 DMA 如此复杂：

#### 1.1 地址视角的不一致
*   **CPU 视角**：看到的是 **虚拟地址 (Virtual Address, VA)**。
*   **物理内存视角**：使用的是 **物理地址 (Physical Address, PA)**。
*   **设备视角**：看到的是 **总线地址 (Bus Address / DMA Address)**。
    *   在没有 IOMMU 的简单系统中，总线地址通常等于物理地址。
    *   在有 **IOMMU** 的系统中，设备看到的是 IOVA (I/O Virtual Address)，IOMMU 负责将其翻译为物理地址。
    *   **结论**：驱动程序绝对不能直接把 `kmalloc` 返回的虚拟地址传给硬件寄存器，必须经过 DMA API 的映射转换。

#### 1.2 缓存一致性 (Cache Coherency)
这是 DMA 最头疼的问题。CPU 有 Cache，内存（RAM）是共享资源。
*   **DMA To Device**：CPU 写数据到内存，但数据可能还停留在 Cache 中，没刷入 RAM。此时 DMA 从 RAM 搬运数据，搬走的是旧的垃圾数据。
*   **DMA From Device**：设备把数据写入 RAM。但 CPU 读数据时，可能直接从 Cache 读到了旧数据，而不知道 RAM 已经变了。

Linux DMA API 的核心作用就是**处理地址转换**和**维护缓存一致性**。

---

### 2. DMA 映射的两种类型

根据生命周期和使用场景，DMA 映射分为两类：**一致性映射 (Coherent/Consistent)** 和 **流式映射 (Streaming)**。

#### 2.1 一致性映射 (Coherent Mapping)
*   **特点**：
    *   映射建立后，CPU 和设备对该内存区域的访问是**同步**的。CPU 写完，设备立刻能看见；设备写完，CPU 立刻能看见。
    *   通常通过禁用该区域的 Cache 或配置硬件 Snooping 协议来实现。
    *   生命周期长，通常在驱动初始化时分配，卸载时释放。
*   **适用场景**：
    *   命令描述符环 (Descriptor Rings)。
    *   控制结构体。
    *   设备的状态寄存器镜像。
*   **核心 API**：
    ```c
    // 分配并映射
    void *dma_alloc_coherent(struct device *dev, size_t size, dma_addr_t *dma_handle, gfp_t flag);
    
    // 释放
    void dma_free_coherent(struct device *dev, size_t size, void *cpu_addr, dma_addr_t dma_handle);
    ```
    *   `cpu_addr`: CPU 访问用的虚拟地址。
    *   `dma_handle`: 设备访问用的总线地址。

#### 2.2 流式映射 (Streaming Mapping)
*   **特点**：
    *   **非一致性**。映射建立后，CPU 和设备存在“所有权”移交的问题。
    *   为了性能，依然开启 Cache。
    *   在映射（Map）时，内核会根据数据流向执行 Cache Flush (刷出) 或 Invalidate (作废) 操作。
    *   生命周期短，通常针对一次 I/O 请求（如一个网络包、一次磁盘读写）。
*   **适用场景**：
    *   网络数据包缓冲区 (sk_buff)。
    *   文件系统数据块 (bio)。
*   **核心 API**：
    ```c
    // 1. 单块连续内存映射
    dma_addr_t dma_map_single(struct device *dev, void *ptr, size_t size, enum dma_data_direction dir);
    void dma_unmap_single(struct device *dev, dma_addr_t addr, size_t size, enum dma_data_direction dir);

    // 2. 散列-聚集 (Scatter-Gather) 映射
    // 物理内存往往是碎片化的，SG 映射允许 DMA 引擎一次处理多个不连续的物理块
    int dma_map_sg(struct device *dev, struct scatterlist *sg, int nents, enum dma_data_direction dir);
    void dma_unmap_sg(struct device *dev, struct scatterlist *sg, int nents, enum dma_data_direction dir);
    ```
*   **所有权同步 (Sync)**：
    如果在 `dma_map` 和 `dma_unmap` 之间，CPU 需要临时访问数据，必须手动同步：
    ```c
    dma_sync_single_for_cpu(...);    // 交给 CPU 读写
    dma_sync_single_for_device(...); // 交还给设备
    ```

#### 2.3 映射类型对比

| 特性 | 一致性映射 (Coherent) | 流式映射 (Streaming) |
| :--- | :--- | :--- |
| **API** | `dma_alloc_coherent` | `dma_map_single` / `dma_map_sg` |
| **开销** | 建立开销大，访问无需同步指令 | 建立开销小，需 Cache 同步操作 |
| **内存来源** | 内核自己分配并映射 | 驱动提供现有的内存 (如 kmalloc/alloc_pages) |
| **生命周期** | 长 (Long-lived) | 短 (One-shot) |
| **典型用途** | 描述符、控制结构 | 实际数据 Payload |

---

### 3. DMA 方向 (Direction)

在流式映射中，必须指定数据流向，以便内核优化 Cache 操作：

*   `DMA_TO_DEVICE`: 数据从内存流向设备（如网卡发包）。内核会 Clean Cache（回写）。
*   `DMA_FROM_DEVICE`: 数据从设备流向内存（如网卡收包）。内核会 Invalidate Cache（作废，强迫 CPU 下次从 RAM 读）。
*   `DMA_BIDIRECTIONAL`: 双向。性能开销最大。
*   `DMA_NONE`: 调试用。

---

### 4. DMA Mask (寻址能力)

并不是所有设备都能访问系统的全部内存地址。例如，一些老旧的 PCI 设备只能访问 32位地址空间（4GB 以下），而系统内存可能有 16GB。

驱动必须告知内核其寻址能力：

```c
// 尝试设置 64 位掩码
if (dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64))) {
    dev_warn(dev, "No 64-bit DMA, trying 32-bit\n");
    // 回退到 32 位
    if (dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32))) {
        dev_err(dev, "No usable DMA configuration\n");
        return -EIO;
    }
}
```
如果设置了 32 位掩码，`dma_alloc_coherent` 或 `dma_map_single` 会自动使用 SWIOTLB (Bounce Buffer) 机制，在低地址分配一块中转区，保证设备能访问到数据。

---

### 5. DMA Engine 子系统 (Async TX)

除了上述供驱动使用的 API，Linux 还有一个 **DMA Engine Framework**。这主要用于嵌入式系统（如 ARM SoC）。

*   **场景**：SoC 内部有一个通用的 DMA 控制器硬件。你想用它来搬运内存（Memcpy Offload），或者让它负责把 SPI FIFO 的数据搬到内存。
*   **角色**：
    *   **DMA Controller Driver**：芯片厂商提供，实现底层控制。
    *   **DMA Client Driver**：外设驱动（如 SPI 驱动）向 DMA Engine 申请通道。
*   **工作流程**：
    1.  `dma_request_channel()`: 申请一个 DMA 通道。
    2.  `dmaengine_prep_slave_sg()`: 准备传输描述符。
    3.  `dmaengine_submit()`: 提交传输请求。
    4.  `dma_async_issue_pending()`: 启动传输。
    5.  通过回调函数 (Callback) 通知传输完成。

---

### 6. IOMMU 的影响

现代服务器和高端嵌入式系统通常配备 **IOMMU (Input-Output Memory Management Unit)**。

*   **没有 IOMMU**：DMA API 返回的 `dma_addr_t` 通常就是物理地址。连续的 `dma_alloc_coherent` 需要占用连续的物理页（难以满足大内存请求）。
*   **有 IOMMU**：
    *   **地址虚拟化**：设备看到的是虚拟地址 (IOVA)。IOMMU 硬件负责将 IOVA 翻译成 PA。
    *   **分散聚合**：驱动可以申请一大块 `vmalloc` (物理不连续) 的内存，IOMMU 可以将其映射为设备看来连续的一段 IOVA。
    *   **安全性**：防止设备访问非法的内存区域（类似进程的内存保护），主要用于虚拟化直通 (Passthrough)。

对于驱动开发者，**DMA API 屏蔽了 IOMMU 的存在**。只要正确使用 `dma_map_*` 系列函数，内核会自动处理 IOMMU 的页表映射。

---

### 7. 常见陷阱与最佳实践

1.  **不要使用栈内存进行 DMA**：
    栈内存（Stack）通常没有针对 Cache 行对齐，且可能被 CPU 预取机制干扰。有些架构严禁对栈地址做 DMA 映射。应始终使用 `kmalloc` 或 `alloc_pages` 分配的堆内存。
2.  **严格检查返回值**：
    `dma_map_single` 可能失败（如 IOMMU 资源耗尽或 SWIOTLB 满了），必须使用 `dma_mapping_error(dev, addr)` 检查。
3.  **配对使用 Map/Unmap**：
    忘记 Unmap 会导致 IOMMU 资源泄漏（IOVA 耗尽）或 Bounce Buffer 泄漏。
4.  **Cacheline 对齐**：
    如果 DMA 缓冲区与 CPU 频繁访问的变量共享同一个 Cacheline，会导致严重的“Cache 颠簸”甚至数据损坏。`dma_alloc_coherent` 能够保证对齐，但自行 `kmalloc` 的流式映射要注意对齐。
5.  **不要手动转换地址**：
    永远不要假设 `virt_to_phys()` 得到的地址可以直接给硬件。始终使用 DMA API。

### 总结

Linux DMA 模块是连接软件与硬件高速通道的桥梁。对于驱动开发者：
*   如果做**控制平面**（Ring Buffer, 状态块），用 `dma_alloc_coherent`。
*   如果做**数据平面**（网络包, 磁盘块），用 `dma_map_single/sg`。
*   始终假设系统中存在 Cache 和 IOMMU，严格遵守 API 规范。

## APIs

以下是 Linux 内核 DMA 模块（主要是 DMA Mapping API）的详细汇总。这些 API 定义在 `&lt;linux/dma-mapping.h&gt;` 头文件中，是设备驱动程序进行直接内存访问的核心接口。

为了方便查阅，我将 API 分为 **一致性映射**、**流式映射**、**同步操作**、**配置与属性** 以及 **DMA 内存池** 五个类别。

### Linux DMA Mapping API 汇总表

| API 函数 | 分类 | 功能描述 | 关键参数与注意事项 |
| :--- | :--- | :--- | :--- |
| **配置与寻址能力** | | | |
| `dma_set_mask_and_coherent` | 配置 | **设置 DMA 寻址掩码**。<br>告知内核该设备支持多少位的地址空间（如 32位、64位）。同时设置流式和一致性映射的掩码。 | **dev**: 设备结构体<br>**mask**: 如 `DMA_BIT_MASK(64)`。<br>驱动初始化时**必须**调用，否则默认通常为 32 位。 |
| `dma_set_mask` | 配置 | 仅设置**流式映射**的寻址掩码。 | 通常建议用上面的 `_and_coherent` 版本替代。 |
| `dma_set_coherent_mask` | 配置 | 仅设置**一致性映射**的寻址掩码。 | 同上。 |
| **一致性映射 (Coherent)** | | | |
| `dma_alloc_coherent` | 分配 | **分配并映射一致性内存**。<br>分配物理页，并建立 CPU 和设备都能访问的映射。保证 Cache 一致性。 | **size**: 大小<br>**dma_handle**: (输出) 返回给设备的 DMA 总线地址。<br>**返回值**: CPU 访问用的虚拟地址 (VA)。<br>**注意**: 可能会睡眠（取决于 gfp 标志）。 |
| `dma_free_coherent` | 释放 | **释放一致性内存**。<br>释放由 `dma_alloc_coherent` 分配的内存并取消映射。 | **cpu_addr**: CPU 虚拟地址<br>**dma_handle**: DMA 总线地址。<br>必须成对调用。 |
| `dma_zalloc_coherent` | 分配 | 同 `dma_alloc_coherent`，但分配后会将内存**清零**。 | 相当于 `alloc` + `memset(0)`。 |
| **流式映射 (Single)** | | | |
| `dma_map_single` | 映射 | **映射单块虚拟内存**。<br>将 CPU 可访问的虚拟地址（由 `kmalloc` 等分配）映射为 DMA 地址。 | **ptr**: 数据缓冲区指针<br>**dir**: 数据流向（见下文）。<br>**返回值**: DMA 地址。<br>**注意**: 必须检查 `dma_mapping_error`。 |
| `dma_unmap_single` | 解除 | **解除单块映射**。<br>DMA 传输完成后调用，释放 IOMMU 资源或 Bounce Buffer。 | **addr**: DMA 地址。<br>必须在数据传输彻底完成后调用。 |
| `dma_map_page` | 映射 | **映射物理页 (struct page)**。<br>用于高端内存（Highmem）或网络栈（skb frag）。 | **page**: `struct page` 指针。<br>**offset/size**: 页内偏移和长度。 |
| `dma_unmap_page` | 解除 | **解除物理页映射**。 | 对应 `dma_map_page`。 |
| `dma_mapping_error` | 错误检查 | **检查映射是否成功**。<br>流式映射可能因 IOMMU 资源耗尽而失败。 | **dma_addr**: `dma_map_single` 的返回值。<br>返回非 0 表示失败。 |
| **流式映射 (Scatter-Gather)** | | | |
| `dma_map_sg` | 映射 | **映射散列-聚集列表 (SG List)**。<br>将物理不连续的多个内存块映射给 DMA 引擎。 | **sg**: `struct scatterlist` 数组<br>**nents**: 数组长度。<br>**返回值**: **映射后的段数**（可能小于 `nents`，因为 IOMMU 可能合并相邻段）。 |
| `dma_unmap_sg` | 解除 | **解除 SG 列表映射**。 | **nents**: 映射时的数组原长度。 |
| `sg_dma_address(sg)` | 宏/访问 | **获取 SG 项的 DMA 地址**。<br>用于遍历映射后的 SG 列表，传给硬件。 | 只能在 `dma_map_sg` 之后使用。 |
| `sg_dma_len(sg)` | 宏/访问 | **获取 SG 项的 DMA 长度**。 | 只能在 `dma_map_sg` 之后使用。 |
| **所有权同步 (Sync)** | | | |
| `dma_sync_single_for_cpu` | 同步 | **将所有权移交给 CPU**。<br>在 `dma_map` 期间，如果 CPU 需要读取 DMA 写回的数据，需先调用此函数（Invalidate Cache）。 | **addr**: DMA 地址<br>**dir**: 方向。<br>用于流式映射的中间同步。 |
| `dma_sync_single_for_device` | 同步 | **将所有权移交给设备**。<br>CPU 修改数据后，调用此函数让设备可见（Flush/Clean Cache）。 | **addr**: DMA 地址。<br>用于 CPU 修改数据后再次让 DMA 传输。 |
| `dma_sync_sg_for_cpu` | 同步 | **SG 列表移交给 CPU**。 | 对应 SG 列表版本。 |
| `dma_sync_sg_for_device` | 同步 | **SG 列表移交给设备**。 | 对应 SG 列表版本。 |
| **DMA 内存池 (Pool)** | | | |
| `dma_pool_create` | 创建 | **创建一个 DMA 内存池**。<br>用于频繁分配固定大小的小块一致性内存（如描述符）。 | **name**: 池名字<br>**size**: 每块大小<br>**align**: 对齐要求。<br>解决小内存分配时的内存碎片和对齐问题。 |
| `dma_pool_destroy` | 销毁 | **销毁 DMA 内存池**。 | 销毁前必须确保池内所有内存块已释放。 |
| `dma_pool_alloc` | 分配 | **从池中分配一块内存**。 | **handle**: (输出) DMA 地址。<br>原子上下文可用（视 flags 而定）。 |
| `dma_pool_free` | 释放 | **归还内存回池**。 | 释放通过 `dma_pool_alloc` 申请的内存。 |

---

### 补充说明：数据流向 (Direction)

在流式映射和同步 API 中，`enum dma_data_direction` 参数至关重要，它决定了底层 Cache 的操作方式：

| 枚举值 | 含义 | Cache 行为 (通常情况) | 典型场景 |
| :--- | :--- | :--- | :--- |
| **`DMA_TO_DEVICE`** | 数据从内存流向设备 | **Clean / Flush** (将 Cache 数据刷入 RAM) | 网卡发包、写入磁盘 |
| **`DMA_FROM_DEVICE`** | 数据从设备流向内存 | **Invalidate** (作废 Cache，强迫 CPU 下次读 RAM) | 网卡收包、读取磁盘 |
| **`DMA_BIDIRECTIONAL`** | 数据双向流动 | Flush + Invalidate (开销最大) | 某些特殊的控制协议 |
| **`DMA_NONE`** | 仅用于调试 | N/A | 驱动开发调试 |

### 快速选择指南

1.  **如果你需要长期存在的控制结构（如 Ring Buffer 描述符）：**
    *   使用 `dma_alloc_coherent`。
2.  **如果你有很多固定大小的小对象（小于一页）需要 DMA：**
    *   使用 `dma_pool_create` + `dma_pool_alloc`。
3.  **如果你要发送网络包或读写文件数据（一次性传输）：**
    *   如果是 `kmalloc` 的地址：使用 `dma_map_single`。
    *   如果是 `struct page`：使用 `dma_map_page`。
    *   如果是碎片化的多个物理页：使用 `dma_map_sg`。
4.  **如果设备只支持 32 位寻址，但你在 64 位系统上：**
    *   务必先调用 `dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32))`。