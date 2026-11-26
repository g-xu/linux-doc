# Linux 内存管理

第一次阅读，先看[总览](00-overview.md)：它介绍内存管理各部分如何协作，并提供 [arm64 虚拟地址空间全景](00-overview.md#kernel-mappings)和[子模块入口总表](00-overview.md#module-index)。查具体机制或接口，可直接进入对应专题。

本系列以本地 Linux 6.1.158、arm64 和指定的 RT 分析前提为基线。页大小、地址位宽、拓扑和可选功能按实际配置区分；源码分析不等于目标机器运行验证。

| 专题 | 主要问题 |
| --- | --- |
| [01 物理页分配](01-page-allocation.md) | 请求怎样经过 Zone、PCP 和 Buddy，何时进入慢路径 |
| [02 物理内存组织与初始化](02-physical-memory.md) | 启动时怎样描述、保留和移交物理内存 |
| [03 内核对象分配](03-kernel-objects.md) | SLUB 怎样从物理页提供小对象，以及怎样复用和释放 |
| [04 内核虚拟空间与分配](04-kernel-virtual.md) | `vmalloc()`、`vmap()` 和 `ioremap()` 的用途与责任 |
| [05 进程地址空间](05-process-address.md) | 程序怎样组织地址范围，VMA 怎样记录用途和权限 |
| [06 arm64 页表与 TLB](06-arm64-page-tables.md) | 地址怎样翻译，何时更新页表和失效 TLB |
| [07 缺页与 COW](07-page-faults.md) | 访问如何触发按需映射和写时复制 |
| [08 文件页缓存与回写](08-page-cache-writeback.md) | 文件内容怎样进入缓存、被修改并写回 |
| [09 回收与交换](09-reclaim-swap.md) | 哪些驻留页能被释放，之后怎样重新取回内容 |
| [10 迁移与规整](10-migration-compaction.md) | 怎样搬移数据，以及怎样整理出连续空闲空间 |
| [11 页生命周期与反向映射](11-page-lifetime-rmap.md) | 怎样从页反查映射，引用和 pin 如何约束回收与迁移 |
| [12 内存记账、限制与 OOM](12-memcg-oom.md) | 内存向谁记账，保护、限制及 OOM 如何确定作用范围 |
| [13 NUMA 内存放置与策略](13-numa-policy.md) | 怎样选择内存节点，已有数据怎样换位置 |
| [14 观测与诊断](14-observability-extensions.md) | 怎样用统计、PSI 和 DAMON 缩小问题范围 |

延伸阅读：[外部图解对照与阅读路线](reference-reading.md) · [撰写与审阅记录](review/index.md) · [旧版存档](old/index.md)。
