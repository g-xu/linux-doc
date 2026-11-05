## 块设备
Linux内核块设备（Block Device）子系统是操作系统中负责管理存储设备（如硬盘、SSD、NVMe、SD卡、USB闪存）的核心组件。

与字符设备（Character Device，如串口、键盘）只能按顺序逐字节访问不同，**块设备的主要特征是支持随机访问（Random Access）和以固定大小的数据块（Block/Sector）为单位进行传输**。

以下是对Linux内核块设备模块的详细介绍，涵盖架构、核心数据结构、关键机制（blk-mq）以及驱动开发流程。

---

### 1. 块设备子系统架构分层

Linux块设备子系统位于虚拟文件系统（VFS）和硬件驱动之间，形成了一个处理I/O请求的流水线。

| 层级 | 名称 | 描述 |
| :--- | :--- | :--- |
| **L1** | **虚拟文件系统 (VFS)** | 用户进程调用 `read/write`。VFS处理文件缓存（Page Cache），如果是缓存命中则不产生磁盘I/O。 |
| **L2** | **块设备映射层 (Block Mapping)** | 将文件系统的“文件+偏移量”转换为“逻辑块号（LBA）”。核心函数是 `submit_bio`。 |
| **L3** | **通用块层 (Generic Block Layer)** | 处理所有块设备的通用逻辑，如请求合并（Merging）、排序、统计等。这是 `struct bio` 转换为 `struct request` 的地方。 |
| **L4** | **I/O 调度层 (I/O Scheduler)** | 对请求进行重排和合并以优化性能（如电梯算法）。现代内核使用多队列调度（mq-deadline, bfq, kyber）。 |
| **L5** | **块设备驱动层 (Device Driver)** | 硬件特定的驱动程序（如 nvme, sd_mod, virtio-blk）。负责从队列取出请求并发送给硬件。 |

---

### 2. 核心数据结构

理解块设备，必须理解以下四个核心结构体及其关系：

#### A. `struct gendisk` (通用磁盘)
代表一个物理磁盘或逻辑磁盘（如 `/dev/sda`）。
*   **功能**: 包含设备的主/次设备号、分区信息、对应的请求队列指针等。
*   **关键成员**: `major`, `first_minor`, `disk_name`, `queue`。

#### B. `struct request_queue` (请求队列)
**这是驱动和内核交互的枢纽**。
*   **功能**: 存放待处理I/O请求的链表/队列。包含调度算法配置、队列深度限制、硬件限制（最大段数、最大扇区数）。
*   **演进**: 在旧内核中是单队列（spinlock瓶颈）；在Linux 3.13+引入了 **blk-mq (Multi-Queue)**，成为现在的默认标准。

#### C. `struct bio` (Block I/O)
**文件系统/上层视角**的I/O请求。
*   **功能**: 描述了一次I/O操作（读/写、内存页数组、扇区偏移）。一个 `bio` 对应文件系统中的一次逻辑读写。
*   **特点**: 轻量级，可能会被拆分或合并。

#### D. `struct request` (硬件请求)
**驱动/底层视角**的I/O请求。
*   **功能**: 为了提高效率，内核会将物理地址相邻的多个 `bio` 合并成一个 `request`。驱动处理的是 `request` 而不是 `bio`。
*   **关系**: `1 Request = N * bio`。

---

### 3. 核心机制：Multi-Queue (blk-mq)

在传统的块设备层中，所有CPU共享一个请求队列（Single Queue），导致在高性能存储（如百万级IOPS的NVMe SSD）上锁竞争严重。

**blk-mq (Block Multi-Queue)** 架构解决了这个问题，它引入了两级队列模型：

1.  **软件队列 (Software Queues)**:
    *   每个CPU核心（或NUMA节点）对应一个软件队列。
    *   I/O提交时无锁（或极低开销）。
    *   负责初步的合并和I/O调度。

2.  **硬件队列 (Hardware Dispatch Queues)**:
    *   对应网卡/存储控制器的实际硬件队列（DMA通道）。
    *   **映射关系**: 多个软件队列映射到少数几个硬件队列（M:N 映射）。

**优势**: 极大提升了多核系统下高性能存储设备的并发吞吐量。

---

### 4. 块设备驱动开发流程

编写一个基于 `blk-mq` 的块设备驱动，通常包含以下步骤：

#### 第一步：定义 `blk_mq_tag_set`
这是多队列的配置结构，定义了有多少个硬件队列、队列深度、操作函数表等。

```c
struct blk_mq_tag_set tag_set;
tag_set.ops = &my_mq_ops; // 核心操作函数表
tag_set.nr_hw_queues = 1; // 硬件队列数量
tag_set.queue_depth = 128; // 队列深度
tag_set.numa_node = NUMA_NO_NODE;
tag_set.flags = BLK_MQ_F_SHOULD_MERGE;
```

#### 第二步：实现 `queue_rq` 回调
这是驱动最关键的函数。内核调度器会调用它将 `request` 发送给驱动。

```c
static blk_status_t my_queue_rq(struct blk_mq_hw_ctx *hctx,
                                const struct blk_mq_queue_data *bd)
{
    struct request *rq = bd->rq;
    
    // 1. 解析 request (获取扇区、长度、DMA地址)
    blk_mq_start_request(rq);

    // 2. 将数据写入硬件寄存器或通过DMA传输
    // ... 硬件操作 ...

    // 3. 如果是异步操作，直接返回 OK；如果是同步，处理完调用 end_request
    return BLK_STS_OK;
}

static const struct blk_mq_ops my_mq_ops = {
    .queue_rq = my_queue_rq,
};
```

#### 第三步：初始化队列与磁盘 (Probe阶段)

```c
// 1. 注册主设备号
register_blkdev(MY_MAJOR, "my_disk");

// 2. 初始化 tag_set
blk_mq_alloc_tag_set(&tag_set);

// 3. 创建请求队列
struct request_queue *q = blk_mq_init_queue(&tag_set);

// 4. 分配 gendisk
struct gendisk *disk = blk_mq_alloc_disk(&tag_set, NULL);
disk->major = MY_MAJOR;
disk->first_minor = 0;
disk->queue = q;
sprintf(disk->disk_name, "mydisk0");
set_capacity(disk, sector_count); // 设置容量

// 5. 向系统添加磁盘
add_disk(disk);
```

---

### 5. 关键 API 汇总表

| API 函数 | 功能描述 | 备注 |
| :--- | :--- | :--- |
| **注册与初始化** | | |
| `register_blkdev` | 注册块设备的主设备号。 | |
| `blk_mq_alloc_tag_set` | 分配并初始化多队列标签集。 | blk-mq 核心初始化。 |
| `blk_mq_init_queue` | 基于 tag_set 创建 `request_queue`。 | 必须在 add_disk 之前调用。 |
| `blk_mq_alloc_disk` | 分配 `gendisk` 结构体。 | Linux 5.x 新引入的辅助函数。 |
| **磁盘操作** | | |
| `set_capacity` | 设置磁盘容量（以512字节扇区为单位）。 | |
| `add_disk` | 激活磁盘，使其在 `/dev` 下可见并可被挂载。 | 驱动初始化的最后一步。 |
| `del_gendisk` | 从系统移除磁盘。 | 卸载模块时调用。 |
| **请求处理** | | |
| `blk_mq_start_request` | 告诉内核驱动已经开始处理该请求。 | 在 `queue_rq` 中调用。 |
| `blk_mq_end_request` | 告诉内核请求处理完毕。 | 通常在中断处理函数中调用。 |
| `rq_for_each_segment` | 遍历 request 中的所有物理段（Scatter-Gather）。 | 用于DMA映射。 |

---

### 6. 数据流向总结 (Life of an I/O)

当用户执行 `write(fd, buf, 4096)` 时：

1.  **VFS**: 检查Page Cache。如果需要写盘，调用文件系统的 `writepages`。
2.  **文件系统**: 构造 `bio` 结构体，描述要写入的物理块号，调用 `submit_bio(bio)`。
3.  **通用块层**: `submit_bio` 可能会进行 **Generic Make Request** 处理，根据分区重映射扇区号。
4.  **I/O调度器**: 将 `bio` 放入软件队列。尝试将其与队列中现有的 `request` 合并（Merge），或者创建一个新 `request`。
5.  **blk-mq**: 按照策略将软件队列的 `request` 派发（Dispatch）到硬件队列。
6.  **驱动 (`queue_rq`)**: 从硬件队列拿到 `request`，解析其中的 `bio` 链表，建立DMA映射，操作硬件控制器发起传输。
7.  **硬件中断**: 传输完成，触发中断。
8.  **软中断/完成回调**: 驱动调用 `blk_mq_end_request`，唤醒等待的进程或释放相关内存。

### 总结
Linux块设备模块从传统的单队列进化到了blk-mq多队列架构，以适应现代高速存储设备。对于驱动开发者来说，核心任务不再是管理复杂的电梯算法，而是正确配置 `blk_mq_tag_set` 并高效实现 `queue_rq` 回调来喂饱硬件的DMA引擎。

## 内部细节
剥离表层，深入到 **Linux 内核通用块层 (General Block Layer)** 和 **多队列机制 (blk-mq)** 的内部实现细节。

我们将从**数据结构的微观组成**、**I/O 请求的生命周期全链路分析**、**IO调度与合并策略**、以及**底层DMA映射细节**四个维度进行深度剖析。

---

### 一、 微观视角：核心数据结构的内存布局

之前的回答只提到了结构体的名字，这里我们看它们的内部机制，特别是它们如何描述内存。

#### 1. `struct bio` 与 `struct bio_vec` (内存描述的本质)
`bio` 不仅仅是一个请求头，它通过 **Scatter-Gather (分散/聚集)** 列表来描述物理内存中不连续的页。

*   **`struct bio`**: 描述逻辑上的连续数据（比如：读磁盘的第100-108扇区）。
*   **`struct bio_vec` (bvec)**: 描述物理内存片段。
    ```c
    struct bio_vec {
        struct page *bv_page;   // 对应的物理页
        unsigned int bv_len;    // 这一段的长度
        unsigned int bv_offset; // 页内的偏移量
    };
    ```
*   **关系**: 一个 `bio` 包含一个 `bio_vec` 数组（内嵌在 `bio` 末尾或动态分配）。这意味着，一个逻辑上连续的 4KB 磁盘读写，在物理内存中可能是由 4 个 1KB 的碎片（位于不同的物理页）组成的。

#### 2. `struct request` (bio 的容器)
驱动层不直接看 bio，看的是 request。
*   **链表结构**: `struct request` 内部维护了一个 `bio` 链表。
    *   `rq->bio`: 指向链表头。
    *   `rq->biotail`: 指向链表尾。
*   **物理段 (Segments)**: 虽然 request 里的 bio 逻辑地址是连续的，但物理内存是碎片的。内核会计算这个 request 总共包含多少个物理段（phys_segments），硬件的 DMA 引擎必须支持这么多段，否则内核必须进行 **Bio Splitting (切分)**。

---

### 二、 深度链路：从 submit_bio 到 硬件寄存器

这是一个 I/O 请求在内核中漫游的详细路径（基于 Linux 5.x+ blk-mq）：

#### 阶段 1：提交与检查 (`submit_bio`)
入口函数是 `submit_bio(bio)`。
1.  **Generic Make Request**: 进入 `blk_mq_submit_bio`。
2.  **Bounce Buffer (跳板缓冲)**: 如果硬件只能寻址 32位地址（DMA掩码限制），而数据在高端内存（HighMem），内核会在此处分配低端内存并拷贝数据。
3.  **Bio Splitting (切分)**:
    *   调用 `blk_queue_split()`。
    *   **原因**: 硬件限制。例如，硬件一次 DMA 最大只能传 128KB，或者 scatter-gather list 最多 64 个。如果 `bio` 超过这些限制，内核会把它切成两个 `bio`，通过链表连接。

#### 阶段 2：蓄水池与合并 (Plugging & Merging)
为了减少锁竞争和提高吞吐，内核不会每来一个 bio 就发给驱动。
1.  **Plug (塞子)**:
    *   每个进程有一个 `struct blk_plug` (在 `task_struct` 中)。
    *   内核将 bio 暂时挂在这个进程私有的链表上（无锁操作），这叫 "Plugging"。
2.  **Merging (合并)**:
    *   **Bio Merge**: 在加入队列前，内核会检查：当前这个 bio 的起始扇区，是不是紧挨着队列里某个已有 request 的结束扇区？
    *   如果是，直接修改那个 request 的长度，把 bio 挂过去。这叫 **Back Merge**。
    *   合并极大减少了硬件交互次数（从100次 4KB 变为 1次 400KB）。
3.  **Unplug (拔塞子)**:
    *   当显式调用 `schedule()` 或链表太长时，触发 Unplug。
    *   此时，bio 被正式转换为 request，送入调度器。

#### 阶段 3：多队列调度 (Blk-mq Scheduling)
I/O 调度器（IO Scheduler）现在位于 blk-mq 的软件队列层。
*   **BFQ / Kyber / MQ-Deadline**: 这些算法接管 request。
*   **Software Queue (ctx)**: 每个 CPU 核心有一个 `struct blk_mq_ctx`。Request 暂时存放在这里。
*   **Hardware Queue (hctx)**: 真正的硬件队列。
*   **Run Queue**: 调度器决定何时将 request 从 **ctx (软队列)** 移动到 **hctx (硬队列)**。

#### 阶段 4：派发 (Dispatch)
当 request以此进入 `hctx` 时，驱动的 `queue_rq` 即将被调用。
*   **Tag 分配**: 硬件通常支持排队（Native Command Queuing, NCQ）。内核分配一个整数 **Tag** 来唯一标识这个 request。
*   **调用驱动**: 执行 `q->mq_ops->queue_rq(hctx, bd)`。

---

### 三、 驱动侧深度：DMA 映射与执行

这是驱动开发中最复杂的部分，涉及内存与总线的交互。

#### 1. Scatter-Gather Mapping (SG 映射)
驱动拿到 `request` 后，不能直接把 `bio` 的指针给硬件（因为硬件不认识内核虚拟地址，也不认识 page 结构体）。
*   **转换**: 驱动调用 `blk_rq_map_sg(q, rq, sg_list)`。
*   **作用**: 这个函数会遍历 request 中所有 bio 的所有 bio_vec，将它们填入一个 `struct scatterlist` 数组中。
*   **IOMMU 合并**: 在有 IOMMU 的系统上，多个物理上不连续的页，可能会被 IOMMU 映射为一段连续的 IO虚拟地址（IOVA）。`dma_map_sg` 会返回硬件真正需要读取的“段”数。

#### 2. 发送给硬件
```c
// 伪代码示例
int my_queue_rq(struct blk_mq_hw_ctx *hctx, const struct blk_mq_queue_data *bd)
{
    struct request *rq = bd->rq;
    
    // 1. 映射 DMA
    int n_segs = blk_rq_map_sg(q, rq, my_dev->sg_list);
    n_segs = dma_map_sg(dev, my_dev->sg_list, n_segs, DMA_TO_DEVICE);

    // 2. 填充硬件描述符 (Descriptor)
    // 告诉硬件：去这几个物理地址读写数据，Tags 是 rq->tag
    fill_hw_descriptor(my_dev, my_dev->sg_list, n_segs, rq->tag);

    // 3. 敲门铃 (Doorbell Ring)
    // 写寄存器，通知硬件干活
    writel(1, my_dev->doorbell_reg);

    return BLK_STS_OK;
}
```

#### 3. 中断与完成 (Completion)
1.  硬件完成传输，触发 MSI-X 中断。
2.  中断处理函数读取硬件返回的 Tag。
3.  通过 Tag 找到对应的 `request`。
4.  `dma_unmap_sg(...)` 解除映射。
5.  `blk_mq_complete_request(rq)`。
6.  **软中断处理**: 遍历 request 中的 bio，逐个调用 `bio_endio()`，最终通知文件系统（Page Cache 页标记为 Clean，或读操作完成）。

---

### 四、 关键技术难点解析

#### 1. 为什么需要 Tag？ (Hardware Tags)
在 NVMe 等高速协议中，硬件内部维护了一个命令队列（比如深度 64k）。
*   内核不再传递指针给硬件（指针是64位的，太占带宽）。
*   内核与硬件约定：我们用 0-65535 的整数（Tag）来代表请求。
*   `blk-mq` 核心层负责管理 Tag 的分配和回收。驱动直接使用 `rq->tag` 即可。

#### 2. Requeue (回队机制)
如果 `queue_rq` 调用时，硬件的 FIFO 满了怎么办？
*   驱动返回 `BLK_STS_RESOURCE` 或 `BLK_STS_DEV_RESOURCE`。
*   Blk-mq 核心层会暂停该硬件队列的派发，启动一个定时器或等待下一次中断，然后将该 request **重新加入队列头部**，稍后重试。

#### 3. Error Handling (超时处理)
*   每个 request 都有一个超时定时器。
*   如果超时（默认30秒），内核调用驱动的 `timeout` 回调。
*   驱动尝试复位硬件（Reset Controller）或终止命令。

---

### 五、 总结：从微观到宏观

Linux 块设备模块之所以复杂，是因为它必须处理物理内存的碎片化（**Scatter-Gather**）、多核 CPU 的锁竞争（**Blk-mq**）、以及千奇百怪的硬件限制（**Bio Splitting/Merging**）。

**核心知识图谱：**
1.  **数据**: `bio` (逻辑分片) -> `bio_vec` (物理内存页) -> `request` (驱动事务) -> `scatterlist` (DMA列表)。
2.  **流程**: `submit_bio` -> **Plugging** (蓄水) -> **Merging** (合并) -> **Scheduler** (电梯排序) -> **Dispatch** (分发到硬队列) -> **Driver** (DMA映射与寄存器操作)。
3.  **驱动任务**: 重点不再是管理队列，而是配置 `tag_set`，高效实现 `queue_rq` 来填满硬件的 Pipeline。

## APIs

Linux内核块设备子系统（Block Subsystem）在近年来经历了巨大的变革，完全转向了 **blk-mq (Multi-Queue)** 架构。

以下是基于现代 Linux 内核（5.x / 6.x）的块设备驱动开发核心 API 详细汇总。

### 1. 驱动注册与资源分配 (Registration & Allocation)
这些 API 通常在驱动的初始化 (`init` 或 `probe`) 阶段使用，用于建立驱动框架。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`register_blkdev`** | 注册块设备的主设备号 (Major Number)。 | `module_init` 阶段。<br>如果传 0，内核动态分配主设备号。 |
| **`unregister_blkdev`** | 注销主设备号。 | `module_exit` 阶段。 |
| **`blk_mq_alloc_tag_set`** | **核心函数**。分配并初始化多队列标签集 (`tag_set`)。定义队列深度、硬件队列数、操作函数表 (`ops`)。 | 必须在创建 gendisk 之前调用。<br>需填充 `struct blk_mq_tag_set`。 |
| **`blk_mq_free_tag_set`** | 释放标签集资源。 | 驱动卸载时调用。 |
| **`blk_mq_alloc_disk`** | **核心函数 (新版)**。同时分配 `gendisk` 结构和 `request_queue`。 | 推荐替代旧的 `alloc_disk` + `blk_mq_init_queue`。<br>防止未初始化完就被访问的竞争条件。 |
| **`put_disk`** | 减少磁盘对象的引用计数，如果为0则释放内存。 | 错误处理路径或卸载时配合 `del_gendisk` 使用。 |

---

### 2. 磁盘与队列配置 (Disk & Queue Configuration)
用于设置磁盘的物理属性（如容量、扇区大小）和队列限制（如最大传输长度）。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`add_disk`** | **激活磁盘**。向内核注册磁盘，使其在 `/dev` 下可见，并触发分区扫描。 | **必须是驱动初始化的最后一步**。<br>调用前必须保证设备已就绪。 |
| **`del_gendisk`** | 从系统移除磁盘，停止分区扫描，清理 sysfs 节点。 | 驱动卸载或设备拔出时。 |
| **`set_capacity`** | 设置磁盘容量（单位：512字节扇区）。 | 在 `add_disk` 之前调用。 |
| **`blk_queue_max_hw_sectors`** | 设置单个 Request 允许的最大扇区数。 | 限制单次 DMA 的最大传输数据量。 |
| **`blk_queue_max_segments`** | 设置单个 Request 允许的最大物理内存段数 (Scatter-Gather segments)。 | 取决于硬件 DMA 描述符表的大小（如 64 或 128）。 |
| **`blk_queue_logical_block_size`** | 设置逻辑块大小 (Logical Sector Size)。 | 通常是 512 或 4096 (4K native)。 |
| **`blk_queue_flag_set`** | 设置队列标志位。 | 如 `QUEUE_FLAG_NONROT` (SSD/非旋转设备), `QUEUE_FLAG_ADD_RANDOM` (贡献随机熵)。 |

---

### 3. 请求处理核心 (Request Processing - Driver Side)
这些是驱动实现 `queue_rq` 回调函数时，以及在中断处理中常用的 API。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`blk_mq_start_request`** | 标记请求开始处理。 | 在 `queue_rq` 回调中，准备发送给硬件前调用。 |
| **`blk_mq_end_request`** | 标记请求完全结束，并向上传递完成状态。 | 通常在**中断处理**或**软中断**中调用。<br>状态码如 `BLK_STS_OK`。 |
| **`blk_mq_complete_request`** | 触发软中断来结束请求（用于在硬中断中快速返回）。 | 如果硬中断不能做太多工作，调用此函数将 `end_request` 推迟到软中断执行。 |
| **`rq_data_dir`** | 获取请求方向（读或写）。 | `READ` 或 `WRITE`。 |
| **`blk_rq_sectors`** | 获取请求涉及的扇区总数。 | 用于计算传输长度。 |
| **`blk_rq_pos`** | 获取请求的起始扇区号 (LBA)。 | 用于填充硬件的 LBA 寄存器。 |

---

### 4. DMA 映射与内存遍历 (DMA Mapping & Iteration)
驱动需要将内核管理的 `request` 转换为硬件认识的物理地址列表。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`blk_rq_map_sg`** | **最常用**。将一个 Request 中的所有数据（多个 bio）映射到一个散列表 (`scatterlist`) 中。 | 在 `queue_rq` 中调用。<br>返回物理段的数量。 |
| **`dma_map_sg`** | 将散列表中的虚拟地址映射为设备可访问的总线地址 (IOVA)。 | 紧接在 `blk_rq_map_sg` 之后调用。<br>属于通用 DMA API，但块设备必用。 |
| **`rq_for_each_segment`** | 宏。遍历 Request 中的每一个物理段。 | 适用于 PIO 模式（CPU 搬运数据）或不支持 Scatter-Gather 的简单硬件。 |
| **`rq_for_each_bvec`** | 宏。遍历 Request 中的每一个 `bio_vec`。 | 比 segment 更底层的遍历。 |

---

### 5. Bio 操作 (通常在通用层，但驱动有时涉及)
虽然 Blk-mq 主要操作 Request，但在某些堆叠驱动（如 DM, MD）或特殊场景下需要操作 Bio。

| API 函数名 | 功能描述 | 典型应用场景/备注 |
| :--- | :--- | :--- |
| **`submit_bio`** | **入口函数**。提交一个 Bio 给通用块层。 | 文件系统调用此函数发起 I/O。 |
| **`bio_alloc`** | 分配一个新的 Bio 结构。 | 驱动自行生成 I/O 请求时使用。 |
| **`bio_add_page`** | 向 Bio 添加内存页（Page）。 | 填充数据缓冲区。 |
| **`bio_endio`** | 标记 Bio 结束。 | 驱动如果不使用 Request 框架（极少见），需手动调用此函数通知上层。 |
| **`bio_split`** | 将一个 Bio 切割成两个。 | 当 Bio 超过硬件限制时，内核自动处理，驱动极少手动调用。 |

---

### 6. 状态码与回调结构 (Structures & Status)

| 名称 | 类型 | 描述 |
| :--- | :--- | :--- |
| **`struct blk_mq_ops`** | **结构体** | 定义驱动的行为：<br>1. `.queue_rq`: **必须实现**，处理 I/O。<br>2. `.init_request` / `.exit_request`: 初始化/清理请求私有数据。<br>3. `.timeout`: 处理请求超时。 |
| **`struct blk_mq_tag_set`** | **结构体** | 多队列配置的核心容器，连接驱动与块层。 |
| **`blk_status_t`** | **枚举** | `queue_rq` 的返回值：<br>`BLK_STS_OK`: 成功/已接受。<br>`BLK_STS_RESOURCE`: 硬件忙（需重试）。<br>`BLK_STS_IOERR`: 硬件故障。 |

### 现代块设备驱动开发流程总结

1.  **Probe**:
    *   `register_blkdev()`
    *   `blk_mq_alloc_tag_set()` (设置 ops, queue_depth, flags)
    *   `blk_mq_alloc_disk()` (获取 disk 和 queue)
    *   `blk_queue_max_hw_sectors()` 等配置
    *   `add_disk()`
2.  **Queue RQ (`.queue_rq`)**:
    *   `blk_mq_start_request()`
    *   `blk_rq_map_sg()` -> `dma_map_sg()`
    *   写硬件寄存器 (Doorbell)
    *   返回 `BLK_STS_OK`
3.  **Interrupt**:
    *   读取硬件完成状态
    *   `dma_unmap_sg()`
    *   `blk_mq_end_request()`