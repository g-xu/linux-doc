## DMAEngine
Linux 内核的 **DMA Engine (Direct Memory Access Engine)** 子系统是一个为了解决不同硬件架构下 DMA 控制器操作差异巨大而设计的**通用框架**。

在没有 DMA Engine 子系统之前，比如 ARM 架构下的各个 SoC 厂商（Samsung, TI, NXP 等）都有自己私有的 DMA API。这意味着如果你写一个 SPI 驱动，想用 DMA，你就得为每个平台写一套特定的 DMA 代码，移植性极差。

**DMA Engine 的核心目标是：** 提供一套统一的、通用的 API，把 **DMA 客户（Client/Consumer）** 和 **DMA 控制器驱动（Provider/Controller）** 解耦。

---

### 1. 总体架构

DMA Engine 子系统主要由三部分组成：

1.  **Slave Clients (消费者)**：需要使用 DMA 传输数据的设备驱动，如 SPI, UART, I2S (Audio), MMC (SD Card) 等。
2.  **DMA Engine Core (核心层)**：位于 `drivers/dma/dmaengine.c`。负责管理 DMA 设备注册、通道管理、提供通用的 API 给消费者。
3.  **DMA Controller Drivers (提供者)**：SoC 内部 DMA 控制器的实际驱动，负责对接硬件寄存器。

```text
  +---------------------+      +---------------------+
  |     UART Driver     |      |     Audio Driver    |  <-- Consumers (Clients)
  +----------+----------+      +----------+----------+
             |                            |
             |  (统一的 dmaengine API)    |
             v                            v
  +--------------------------------------------------+
  |              Linux DMA Engine Core               |  <-- Framework
  +------------------------+-------------------------+
                           |
             +-------------+-------------+
             |                           |
  +----------v----------+     +----------v----------+
  |  Intel DMA Driver   |     |  Samsung DMA Driver |  <-- Providers
  +---------------------+     +---------------------+
```

---

### 2. 核心数据结构

理解这几个结构体是理解代码的关键：

1.  **`struct dma_device`**
    *   代表一个 **DMA 控制器实例**。
    *   包含控制器的能力（支持内存复制？支持异或运算？）、支持的通道列表、以及设备相关的回调函数指针（如 `device_alloc_chan_resources`）。

2.  **`struct dma_chan`**
    *   代表一个 **DMA 通道**。
    *   这是 Client 申请资源的最小单位。
    *   通道可以是物理的，也可以是虚拟的（由驱动软件模拟多路复用）。

3.  **`struct dma_async_tx_descriptor` (TX Descriptor)**
    *   代表一次 **传输事务描述符**。
    *   当 Client 准备好数据后，会生成一个描述符，里面包含了源地址、目的地址、长度、回调函数等信息。
    *   Client 提交这个描述符给 Engine，Engine 把它放入队列等待执行。

4.  **`struct dma_slave_config`**
    *   **从设备配置**。
    *   这是 Client 告诉 DMA 控制器关于外设硬件特性的结构体。
    *   包含：源/目的的总线宽度（8/16/32位）、突发传输大小（Burst Size）、流控信号等。

---

### 3. 主要功能分类

DMA Engine 主要支持以下几种传输模式：

#### A. DMA_MEMCPY (Memory to Memory)
*   **用途**：内存拷贝。
*   **场景**：虽然 CPU 拷贝通常利用 Cache 很快，但在处理大数据量且不希望占用 CPU 流水线时（如 RAID 校验、视频 buffer 搬移），会用到这种模式。有些硬件支持硬件 XOR/PQ 计算，也归类于此。

#### B. DMA_SLAVE (Memory to Peripheral / Peripheral to Memory)
*   **用途**：最常用的模式。外设（Slave）和内存之间的数据传输。
*   **场景**：
    *   **RX**: UART 接收 FIFO -> DMA -> RAM。
    *   **TX**: RAM -> DMA -> SPI 发送 FIFO。
*   **关键点**：需要硬件握手信号（Request Line/DRQ）。DMA 控制器必须知道外设什么时候“饿了”（TX FIFO 空）或者“撑了”（RX FIFO 满），不能像 Memcpy 那样无脑发。

#### C. DMA_CYCLIC (循环模式)
*   **用途**：音频驱动（ALSA）专用。
*   **场景**：音频播放通常使用环形缓冲区（Ring Buffer）。DMA 需要在这个 Buffer 上不停地转圈传输，并每隔一段（Period）产生一次中断通知 CPU 填充新数据。

#### D. DMA_INTERLEAVED (交织模式)
*   **用途**：处理不连续的、但在二维空间上有规律的数据。
*   **场景**：视频处理（如从图像中抠出一个矩形区域），涉及 Stride（步幅）和 Skip。

---

### 4. 驱动开发流程 (Client 视角)

如果你编写一个 SPI 驱动并想使用 DMA，你需要遵循以下标准流程：

#### 第一步：申请通道
在 Probe 阶段或打开设备时申请。现在通常结合 Device Tree 使用。
```c
struct dma_chan *chan;
// "tx" 是设备树中 dma-names 属性定义的名字
chan = dma_request_chan(dev, "tx");
if (IS_ERR(chan))
    return PTR_ERR(chan);
```

#### 第二步：配置从设备参数 (Slave Config)
告诉 DMA 控制器外设的 FIFO 地址在哪里，宽度是多少。
```c
struct dma_slave_config config;
memset(&config, 0, sizeof(config));
config.direction = DMA_MEM_TO_DEV; // 内存到外设
config.dst_addr = spi_tx_fifo_phys_addr; // 外设 FIFO 物理地址
config.dst_addr_width = DMA_SLAVE_BUSWIDTH_1_BYTE; // 宽度
config.dst_maxburst = 1; // 突发长度

dmaengine_slave_config(chan, &config);
```

#### 第三步：准备传输描述符 (Prepare)
把内存数据（通常是 Scatter-Gather list，即散列表，因为物理内存可能不连续）转换为 DMA 能够理解的描述符。
```c
struct dma_async_tx_descriptor *tx;

// sg 是 scatterlist，包含内存地址和长度
tx = dmaengine_prep_slave_sg(chan, sg, nents, DMA_MEM_TO_DEV, flags);

// 设置回调函数，传输完成后调用
tx->callback = my_dma_callback;
tx->callback_param = my_context;
```

#### 第四步：提交并触发 (Submit & Issue)
`submit` 只是把描述符挂到了驱动的软件队列里，`issue_pending` 才是真正推给硬件执行。
```c
dma_cookie_t cookie;
cookie = dmaengine_submit(tx); // 提交到队列
dma_async_issue_pending(chan); // 踢一脚，让硬件开始跑
```

---

### 5. 设备树 (Device Tree) 的关联

DMA Engine 极大地依赖设备树来建立 Client 和 Controller 之间的连接。

**Controller 端 (Provider):**
```dts
dma_controller: dma@12340000 {
    compatible = "vendor,soc-dma";
    reg = <0x12340000 0x1000>;
    #dma-cells = <2>; // 表示引用该 DMA 需要带 2 个参数
};
```

**Client 端 (Consumer, 如 UART):**
```dts
uart0: serial@56780000 {
    compatible = "vendor,soc-uart";
    // 请求两个 DMA 通道：rx 和 tx
    // <&dma_controller 12 0> : 12 可能代表 Request Line ID，0 代表优先级等
    dmas = <&dma_controller 12 0>, <&dma_controller 13 0>;
    dma-names = "rx", "tx";
};
```
函数 `dma_request_chan(dev, "rx")` 会自动解析设备树，找到对应的 `dmas` 属性，解析参数，并匹配到正确的 DMA 控制器驱动。

---

### 6. 进阶概念：Virt-DMA (虚拟 DMA)

很多现代 SoC 的 DMA 硬件通道有限（比如只有 8 个物理通道），但外设可能有几十个。
内核实现了一个辅助层 **Virt-DMA**。
*   它允许驱动创建任意数量的 `virt_chan`（虚拟通道）。
*   上层 Client 感觉自己独占了一个通道。
*   底层驱动负责调度：当物理通道空闲时，从虚拟通道队列中取出一个任务放在物理通道上跑。
*   这大大简化了资源管理，防止 Client 申请不到 DMA 通道导致初始化失败。

### 总结

Linux `dmaengine` 是一个高度抽象的子系统：
1.  **标准化**了 DMA 的使用方式（申请 -> 配置 -> 准备 -> 提交 -> 触发）。
2.  通过 **Scatter-Gather (SG)** 支持，完美解决了虚拟内存物理地址不连续的问题。
3.  通过 **Device Tree** 绑定，优雅地解决了硬件拓扑关系的描述。
4.  对于驱动开发者，只需要关注标准 API，而不需要阅读几百页的 SoC DMA 控制器手册。

## APIs

Linux 内核 DMAEngine 子系统的 API 非常丰富，主要定义在 `include/linux/dmaengine.h` 头文件中。为了方便理解，我们可以按照 **DMA 生命周期的不同阶段**（申请、配置、准备、提交、控制、释放）来分类汇总。

以下是核心 API 的详细功能汇总表：

### Linux DMAEngine API 汇总表

| 分类 | API 函数名 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- | :--- |
| **1. 通道申请与释放** | `dma_request_chan` | **推荐**。基于设备（Device Tree 或 ACPI）请求一个 DMA 通道。 | `dev`: 请求DMA的设备<br>`name`: 通道名称（对应DT中的 `dma-names`） |
| | `dma_request_channel` | **旧式**。通过掩码和过滤器函数请求通道，用于非 DT 场景。 | `mask`: 能力掩码<br>`filter_fn`: 过滤回调函数 |
| | `dma_release_channel` | 释放已申请的 DMA 通道。 | `chan`: 要释放的通道结构体指针 |
| **2. 通道配置** | `dmaengine_slave_config` | 配置 Slave DMA 通道的硬件参数。必须在传输准备前调用。 | `config`: 包含源/目的地址、总线宽度、Burst 大小等信息的结构体 |
| **3. 传输准备 (Prep)** <br> *(返回描述符)* | `dmaengine_prep_slave_sg` | **最常用**。准备一次 Scatter-Gather (SG) 列表传输。用于外设与内存间传输。 | `sgl`: 已映射的 SG 列表<br>`sg_len`: SG 长度<br>`direction`: 传输方向 (`DMA_MEM_TO_DEV` 等) |
| | `dmaengine_prep_dma_cyclic` | 准备循环 DMA 传输。通常用于 **Audio** 驱动（环形缓冲区）。 | `buf_addr`: 缓冲区地址<br>`buf_len`: 总长度<br>`period_len`: 周期长度（中断触发间隔） |
| | `dmaengine_prep_dma_memcpy` | 准备内存到内存 (Mem-to-Mem) 的拷贝传输。 | `dest`: 目的物理地址<br>`src`: 源物理地址<br>`len`: 长度 |
| | `dmaengine_prep_interleaved_dma` | 准备交错式 DMA 传输。用于复杂的 2D 数据搬运（如视频帧）。 | `xt`: 描述交错模式的模板结构体 |
| **4. 提交与启动** | `dmaengine_submit` | 将准备好的描述符（Descriptor）推入驱动的**软件挂起队列** (Pending Queue)。 | **注意**：此函数**不会**立即启动硬件传输。返回 `dma_cookie_t`。 |
| | `dma_async_issue_pending` | **激活传输**。告诉 DMA 控制器处理挂起队列中的描述符，**真正启动硬件**。 | `chan`: 目标通道。通常在 `submit` 后调用。 |
| **5. 状态查询** | `dmaengine_tx_status` | 查询指定 Cookie 的传输状态（是否完成、剩余字节数）。 | `cookie`: 交易凭证<br>`txstate`: 返回详细状态的指针 |
| | `dma_async_is_tx_complete` | 快速检查某个 Cookie 是否已经完成。 | 返回 `DMA_COMPLETE` 或 `DMA_IN_PROGRESS` |
| **6. 流程控制** | `dmaengine_terminate_sync` | **同步终止**。停止通道上的所有传输，并等待正在执行的回调结束。 | 用于驱动卸载或出错恢复。比 `terminate_all` 更安全。 |
| | `dmaengine_terminate_async` | **异步终止**。停止传输，不等待回调完成。 | 适用于原子上下文（Atomic Context）。 |
| | `dmaengine_pause` | 暂停当前传输（硬件支持时有效）。 | 数据保留在 FIFO 中，可恢复。 |
| | `dmaengine_resume` | 恢复被暂停的传输。 | 继续之前的传输。 |
| | `dmaengine_synchronize` | 等待所有在该通道上的异步操作完成。 | 用于同步上下文。 |

---

### 补充说明：DMAEngine 核心结构体

理解 API 的同时，需要了解几个传递数据的核心结构体：

1.  **`struct dma_chan` (DMA 通道)**
    *   代表一个物理或逻辑 DMA 通道。驱动通过它来操作硬件。
2.  **`struct dma_async_tx_descriptor` (传输描述符)**
    *   由 `prep` 系列函数返回。它代表一次定义好的传输任务。
    *   包含回调函数指针 `callback` 和参数 `callback_param`（传输完成后调用）。
3.  **`struct dma_slave_config` (从设备配置)**
    *   配合 `dmaengine_slave_config` 使用。
    *   核心字段：`src_addr_width` / `dst_addr_width` (位宽), `src_maxburst` / `dst_maxburst` (突发长度), `slave_id` (请求线号)。

### 典型使用流程 (Workflow)

一个典型的外设驱动（如 SPI）使用 DMAEngine 的代码逻辑如下：

1.  **申请**: `chan = dma_request_chan(dev, "tx");`
2.  **配置**: 填充 `struct dma_slave_config conf;` 然后调用 `dmaengine_slave_config(chan, &conf);`
3.  **映射**: 使用 DMA Mapping API (如 `dma_map_sg`) 获取物理地址。
4.  **准备**: `desc = dmaengine_prep_slave_sg(chan, sgl, ...);`
    *   *设置回调*: `desc->callback = my_dma_callback;`
5.  **入队**: `cookie = dmaengine_submit(desc);` (注意：此时硬件未动)
6.  **发射**: `dma_async_issue_pending(chan);` (硬件开始搬运)
7.  **完成**: 硬件触发中断 -> DMAEngine 驱动处理 -> 调用 `my_dma_callback` -> 外设驱动在回调中做善后工作（如 `dma_unmap_sg`）。
8.  **清理**: 卸载时调用 `dmaengine_terminate_sync(chan);` 和 `dma_release_channel(chan);`

## DMAEngine vs DMA mapping
在Linux内核中，**DMA Mapping** 和 **DMAEngine** 是两个紧密相关但侧重点完全不同的子系统。
简单的一句话总结：**DMA Mapping 负责解决“地址和缓存”的问题（数据存在哪），而 DMAEngine 负责解决“搬运”的问题（谁来搬、怎么搬）。**

---

### 1. 核心定义与职责

#### **DMA Mapping (DMA 映射子系统)**
它是Linux内核提供的一套**通用API**，用于在CPU和设备之间通过DMA共享内存。
*   **核心问题**：CPU使用的是虚拟地址（VA），而硬件设备（DMA控制器）使用的是物理地址或总线地址（Bus Address）。此外，CPU有缓存（Cache），而RAM中的数据可能不是最新的。
*   **主要职责**：
    1.  **地址转换**：将CPU可见的虚拟地址转换为设备可见的DMA地址（`dma_addr_t`）。
    2.  **缓存一致性（Cache Coherency）**：处理CPU缓存和主存之间的数据同步（Flush/Invalidate），确保设备读到的是最新数据，或者CPU读到的是设备写入的最新数据。
    3.  **IOMMU 管理**：如果有IOMMU，它负责配置IOMMU页表，实现设备地址空间的虚拟化。

#### **DMAEngine (通用DMA引擎子系统)**
它是Linux内核为**通用DMA控制器**（General Purpose DMA Controller）设计的驱动框架。
*   **核心问题**：SoC上通常有一个集成的DMA控制器，它可以服务多个外设（如SPI, UART, I2S）或进行内存到内存（Mem-to-Mem）的拷贝。不同的DMA控制器硬件接口不同，需要统一的API。
*   **主要职责**：
    1.  **硬件抽象**：提供统一的接口（API）来控制各种DMA控制器硬件（如Intel IOAT, ARM PL330等）。
    2.  **通道管理**：管理DMA通道（Channel）的分配与释放。
    3.  **传输调度**：提供异步传输API，支持分散/聚集（Scatter-Gather）传输、循环传输（Cyclic）等模式。

---

### 2. 区别 (Difference)

| 特性 | DMA Mapping | DMAEngine |
| :--- | :--- | :--- |
| **关注点** | **内存与地址**。关注数据在哪里，以及数据的一致性。 | **执行与控制**。关注如何配置硬件引擎把数据从A搬到B。 |
| **使用者** | **所有**需要进行DMA操作的驱动程序（网卡、显卡、USB、声卡等）。 | **没有独立DMA能力**的外设驱动（如SoC内部的UART, SPI）或需要硬件加速内存拷贝的场景。 |
| **硬件依赖** | 依赖于CPU架构（ARM, x86）和总线架构。 | 依赖于具体的DMA控制器硬件型号。 |
| **关键API** | `dma_map_single`, `dma_alloc_coherent`, `dma_unmap_sg` | `dma_request_channel`, `dmaengine_prep_slave_sg`, `dma_async_issue_pending` |
| **是否有替代** | 无。只要做DMA，必须用它来获取物理地址。 | 有。如果是PCIe网卡等自带Master能力的设备，它自己就有DMA逻辑，不需要用到内核的DMAEngine子系统。 |

**重要区分点：**
*   如果你写一个 **PCIe 网卡驱动**，你会大量使用 **DMA Mapping** 来获取物理地址交给网卡，但你通常**不会**使用 DMAEngine，因为网卡内部集成了DMA控制器，驱动直接写网卡寄存器即可启动传输。
*   如果你写一个 **SoC 上的 SPI 驱动**，SPI控制器很弱，没有DMA功能，它依赖SoC内部那个共享的DMA控制器。这时你既要用 **DMA Mapping** 准备内存，又要用 **DMAEngine** 来指挥那个共享的DMA控制器去搬运数据。

---

### 3. 联系 (Connection)

这两个子系统通常是**上下游**或**协作**的关系。在基于DMAEngine的传输流程中，DMA Mapping是必不可少的前置步骤。

#### 协作流程（以SPI设备使用DMA发送数据为例）：

1.  **准备数据 (Driver)**: SPI驱动有一个存放数据的缓冲区（虚拟地址）。
2.  **映射内存 (DMA Mapping)**:
    *   SPI驱动调用 `dma_map_single()` 或 `dma_map_sg()`。
    *   **DMA Mapping子系统**负责刷Cache，并返回一个物理地址（`dma_addr_t`）。
3.  **配置传输 (DMAEngine)**:
    *   SPI驱动将上一步得到的物理地址（`dma_addr_t`）填入 `dma_slave_config` 结构体，或者传递给 `dmaengine_prep_slave_sg()`。
    *   **DMAEngine子系统**接收到请求，将其翻译成特定DMA控制器的寄存器操作。
4.  **执行搬运 (Hardware)**:
    *   DMA控制器硬件读取该物理地址，将数据搬运到SPI控制器的FIFO中。
5.  **结束清理 (Both)**:
    *   DMAEngine通知驱动传输完成。
    *   驱动调用 `dma_unmap_single()`（属于DMA Mapping），由其负责让CPU看到最新的内存状态（如果是接收数据）。

**结论：DMAEngine 也是 DMA Mapping 的用户。** DMAEngine 驱动本身在配置硬件描述符链表时，也需要使用 DMA Mapping API 来分配和映射描述符内存。

---

### 4. 通俗类比

假设你要寄送一箱货物：

*   **数据** = 货物。
*   **内存 (RAM)** = 你的仓库。
*   **外设 (Device)** = 收货地址。

在此场景下：

1.  **DMA Mapping 是“打包员和地址标签”**：
    *   他负责把货物打包好（Cache Coherency）。
    *   他负责写一个快递员能看懂的**绝对坐标/GPS地址**（物理地址），而不是写“我家仓库第三个架子”（虚拟地址）。
    *   没有他，快递员找不到货，或者拿到的是空箱子。

2.  **DMAEngine 是“快递公司调度中心”**：
    *   你打电话给他：“派个车把这箱货送到那个地址”。
    *   他负责调度一辆卡车（DMA Channel），告诉司机去刚才那个GPS地址取货，然后送到目的地。
    *   如果你自己有卡车（自带DMA Master的高端设备），你就不用找快递公司（DMAEngine），但你依然需要打包员（DMA Mapping）给你写好地址标签。

### 总结

*   **DMA Mapping** 是基础，解决了**地址翻译**和**数据一致性**，只要涉及DMA必用。
*   **DMAEngine** 是框架，解决了**通用DMA硬件的复用**，主要用于SoC内部外设的数据搬运。