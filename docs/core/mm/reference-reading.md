# 外部图解对照与阅读路线

资料来自[bin 的 Linux 内存管理系列分类](https://www.cnblogs.com/binlovetech/category/2213703.html)。下表列全分类的 16 篇，并指出可先看原文哪一组图、回本站看哪里。原文图用于辅助理解，不是本地实现证据。本站的机制、函数及条件以本地 Linux 6.1.158、arm64、RT 补丁源码为准，未确认的配置不写死。

可直接对照的原图：[Node 与 Zone](https://img2022.cnblogs.com/blog/2907560/202211/2907560-20221122114350768-954440987.png)、[Buddy 分配](https://img2023.cnblogs.com/blog/2907560/202302/2907560-20230203200559149-526564180.png)、[SLUB 引导](https://img2023.cnblogs.com/blog/2907560/202304/2907560-20230412100458032-49538517.png)、[单级页表对照](https://img2023.cnblogs.com/blog/2907560/202307/2907560-20230721163832468-1349846227.png)、[私有文件写缺页](https://img2023.cnblogs.com/blog/2907560/202312/2907560-20231221123202203-997020589.png)、[共享文件写缺页](https://img2023.cnblogs.com/blog/2907560/202312/2907560-20231221123236309-885777783.png)。本站新图独立绘制，未嵌入这些外部图片。

## 从总体到局部

| 想追踪的问题 | 先看整体 | 再放大局部 |
| --- | --- | --- |
| 一段 RAM 如何变成一次对象分配？ | [总览：物理资源](00-overview.md#physical-resources) → [02：追踪 PFN 26](02-physical-memory.md#pfn-to-allocator) | [01：分配与准备](01-page-allocation.md#allocation-to-compound) → [03：从大小类到槽位](03-kernel-objects.md#kmalloc-routing) → [03：自举图](03-kernel-objects.md#slub-bootstrap) |
| 一个程序地址如何找到真正的内容？ | [05：程序布局](05-process-address.md#basic-model) → [05：四种映射](05-process-address.md#mmap-four-cases) | [06：末级表页何时建立](06-arm64-page-tables.md#sparse-tables) → [07：同一文件偏移的三种缺页](07-page-faults.md#file-fault-branches) |
| 普通文件的同一片段，应用如何读到？ | [08：最小读写周期](08-page-cache-writeback.md#basic-cycle) → [08：两种交付路径图](08-page-cache-writeback.md#buffered-vs-mapped) | [08：预读](08-page-cache-writeback.md#read-ahead) → [08：共享写](08-page-cache-writeback.md#mapped-writes) → [08：回写](08-page-cache-writeback.md#writeback) |

<a id="physical"></a>
## 物理组织与页分配（3 篇）

| 原文 | 建议看图 | 本站落点与取舍 |
| --- | --- | --- |
| [物理内存管理](https://www.cnblogs.com/binlovetech/p/16914715.html) | §2 描述模型；§4–5 节点与 Zone | [02：同一 PFN 的描述、归属、移交资格](02-physical-memory.md#pfn-to-allocator)；[13：本地和远端](13-numa-policy.md)已有拓扑对照，不并入启动主线。旧模型和历史冷热 PCP 不当作本树实现。 |
| [物理内存分配全链路](https://www.cnblogs.com/binlovetech/p/17019710.html) | §3 入口与首次尝试；§5 全链路 | [01：快慢路径与交付前初始化](01-page-allocation.md#slow-path)。现有路径图按本树重查条件，不把 Linux 5.19 的分支当成 6.1.158 逐行对照。 |
| [Buddy 的设计与实现](https://www.cnblogs.com/binlovetech/p/17090846.html) | §3–4 拆分和合并的连续快照；§5.3 到手后的页描述符 | 01 的现有拆合图延用同一 PFN 范围；新补 [order 和 compound 的区别](01-page-allocation.md#allocation-to-compound)。PCP 的阶数、同步与入链规则仍以本树为准。 |

<a id="slub"></a>
## SLUB 与 kmalloc（5 篇）

| 原文 | 建议看图 | 本站落点与取舍 |
| --- | --- | --- |
| [slab 内存池设计](https://www.cnblogs.com/binlovetech/p/17288990.html) | §5 单 slab；§6 组织全景；§7–8 状态变化 | [03：slab 与槽位、组织和四槽位主线](03-kernel-objects.md)。沿用现有槽位图，不复刻整套图；本树使用 `struct slab`，不照搬旧描述字段。 |
| [slab cache 创建与初始化](https://www.cnblogs.com/binlovetech/p/17308859.html) | §5–6 创建与布局；§12 引导期先后依赖 | [03：SLUB 自举图](03-kernel-objects.md#slub-bootstrap)区分临时描述、节点对象、正式描述和大小类。 |
| [slab cache 分配全链路](https://www.cnblogs.com/binlovetech/p/17373667.html) | §2–3 从本地到共享供给；§4 填充空闲链 | [03：分配器供给与已有快慢路径图](03-kernel-objects.md)。先看 §2.4 组成再读 §4；不把不同配置的分支序列写成每个请求都会经过。 |
| [slab 释放与销毁](https://www.cnblogs.com/binlovetech/p/17434311.html) | §3 各种归还状态；§4 销毁 | [03：释放、停用与 cache 寿命](03-kernel-objects.md)。沿用第 5 节四槽位模型；业务退出、槽位归还、slab 后备归还仍是三件事。 |
| [通用 kmalloc 体系](https://www.cnblogs.com/binlovetech/p/17495824.html) | §3 cache 类别和大小类；§5 入口分流 | [03：80 字节请求的大小类查找](03-kernel-objects.md#kmalloc-routing)。以本树 arm64 初始化后的表说明为何从 96 字节类改指 128 字节类。 |

<a id="address"></a>
## 地址空间与 mmap（3 篇）

| 原文 | 建议看图 | 本站落点与取舍 |
| --- | --- | --- |
| [虚拟内存管理](https://www.cnblogs.com/binlovetech/p/16824522.html) | §3–4 程序整体布局（[原布局图](https://img2022.cnblogs.com/blog/2907560/202210/2907560-20221025125421725-601292677.png)）；§5 从全景放大到 VMA | [05：程序全景与一段 VMA](05-process-address.md#basic-model)保留原有布局图；[04：arm64 内核 VA 区域职责](04-kernel-virtual.md#arm64-region-orientation)补内核侧定位，不搬固定地址或 HIGHMEM。 |
| [mmap 原理](https://www.cnblogs.com/binlovetech/p/17712761.html) | §2–5 对比四类映射，尤其同文件的两种写入去向 | [05：匿名/文件 × 私有/共享](05-process-address.md#mmap-four-cases) → [07：从空 PTE 到 F/Q](07-page-faults.md#file-fault-branches)。特殊映射与大页不混入普通模型。 |
| [mmap 源码实现](https://www.cnblogs.com/binlovetech/p/17754173.html) | §3 建立范围全链路；§4–5 放大寻址、合并和约束 | [05：地址选择、语义与来源](05-process-address.md#operations)接续原有范围推演。区间索引按本树 Maple Tree，不照搬旧查找、插入或回滚路径。 |

<a id="translation"></a>
## 页表、缺页与交换（2 篇）

| 原文 | 建议看图 | 本站落点与取舍 |
| --- | --- | --- |
| [构建 Linux 页表体系](https://www.cnblogs.com/binlovetech/p/17571929.html) | §2–4 从映射关系放大到各层表；§5 硬件遍历 | [06：多级表的空间取舍与复用图](06-arm64-page-tables.md#sparse-tables)。不搬原文架构位段，例子按 arm64 4 KiB / 48 位并显式声明。 |
| [缺页异常图解](https://www.cnblogs.com/binlovetech/p/17918733.html) | §5–6 处理全局；§8 三种文件分支；§10 交换预读 | [07：文件缺页分流图](07-page-faults.md#file-fault-branches)；[09：交换预读的两种邻近](09-reclaim-swap.md#swap-read-ahead)。不搬其他架构的异常入口与 `vmalloc_fault` 修复路径。 |

<a id="file-io"></a>
## 文件读写与页缓存（3 篇）

| 原文 | 建议看图 | 本站落点与取舍 |
| --- | --- | --- |
| [JDK NIO 文件读写](https://www.cnblogs.com/binlovetech/p/16661323.html) | §3 缓冲与直接 I/O；§5–9 缓存和预读 | [08：预读与两种交付](08-page-cache-writeback.md#read-ahead)。本树用 XArray/folio，原文 radix tree 只作历史材料；不以运行时的缓冲区类型推断直接 I/O。 |
| [JDK MappedByteBuffer](https://www.cnblogs.com/binlovetech/p/18083709) | §1 从映射全景到私有/共享写；§4 识别去掉哪次拷贝 | [05：映射语义](05-process-address.md#mmap-four-cases) → [08：共享修改与独立副本](08-page-cache-writeback.md#mapped-writes)。不引入 JVM GC、持久内存或应用部署细节。 |
| [MappedByteBuffer 与 FileChannel](https://www.cnblogs.com/binlovetech/p/18101357) | §1–2 两条数据路径；§4 拆开缓存冷热 | [08：同偏移的 buffered read/mmap 图](08-page-cache-writeback.md#buffered-vs-mapped)。额外拆开页缓存就绪与 PTE 就绪；不引用外部 benchmark 结果当本地性能结论。 |
