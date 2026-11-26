# 内存管理专题：原理与实现

这里是 `sub/` 的统一入口，承接 [内存管理总览](../00-overview.md#reading)。基线为 **Linux 6.1.158、arm64**，目标配置和运行状态未指定；文中的图和数字用于解释限定场景下的源码行为，不是目标机实验记录。

机制分篇讲清“为什么这样工作、各部分怎样联系”；这里进一步追踪算法、字段变化、真实源码分支以及并发和失败处理。可以按具体问题选择专题，不要求从第一个文件一直读到最后。

## 从总览进入专题

| 总览中的位置 | 需要先补原理时 | 可以直接进入的实现专题 |
| --- | --- | --- |
| [第 2 节：物理页](../00-overview.md#physical) | [物理内存组织](../01-mechanisms.md) | [Buddy 的空闲块组织](page-allocation/01-buddy.md) |
| [第 3 节：分配](../00-overview.md#allocation) | [分配机制](../02-allocation.md) | [第 1 组](#allocation-topics)：Buddy、PCP、慢路径、SLUB、Vmalloc |
| [第 4 节：地址与数据](../00-overview.md#address-space) | [地址空间与缺页](../03-address-space.md) | [第 2 组](#data-reclaim-topics)：缺页与 COW、页缓存与写回 |
| [第 5 节：回收](../00-overview.md#reclaim) | [回收与反向映射](../04-reclaim-rmap.md) | [第 2 组](#data-reclaim-topics)：回收执行、Rmap、传统 LRU、MGLRU |
| [第 6 节：特殊需求与限制](../00-overview.md#constraints) | [页粒度、位置与约束](../05-placement-and-limits.md) | [第 3 组](#constraint-topics)：THP、HugeTLB、CMA、NUMA、Memcg、Overcommit |

例如，想知道“一页释放后为什么没有立即参加 Buddy 合并”，可先读 Buddy 的合并规则，再看 PCP 的归还和批量回收；想知道“回收时怎样找到进程页表”，则从 Rmap 开始。术语不熟悉时，可返回 [缩略词与术语速查](../00-overview.md#glossary)。

<a id="allocation-topics"></a>

## 1. 从资源组织到分配完成

| 专题 | 重点 |
| --- | --- |
| [Buddy](page-allocation/01-buddy.md) | 空闲块描述、拆分、伙伴判断、合并、类型回退和锁 |
| [PCP](page-allocation/02-pcp.md) | 阶与类型索引、混合库存、批量换算、归还和drain |
| [分配慢路径](page-allocation/03-slowpath.md) | 水位算例、许可重建、先规整条件、回收/重试和OOM |
| [SLUB](object-allocation/01-slub.md) | 两条freelist与inuse、tid竞争、partial交接和底层回收 |
| [Vmalloc](virtual-allocation/01-vmalloc.md) | 增广空闲树、四种分裂、对齐失败、部分回滚与VA重用 |

<a id="data-reclaim-topics"></a>

## 2. 地址、内容与回收闭环

| 专题 | 重点 |
| --- | --- |
| [arm64缺页与COW](address-space/01-fault-cow.md) | 条目分派、双候选竞争、复用条件、映射/统计变化和提交 |
| [页缓存与写回](page-cache/01-cache-writeback.md) | 索引成员、截断重查、位与mark、再次脏化和有界写回 |
| [回收执行](reclaim/01-reclaim.md) | 保留/激活/退出集合、后备处理、引用冻结与refault |
| [Rmap](reclaim/02-rmap.md) | 对象位置、候选地址、PTE验证、unmap及分层保护 |
| [传统LRU](reclaim/03-lru.md) | 访问证据、active/inactive转换、成本权重和扫描预算 |
| [MGLRU](reclaim/04-mglru.md) | Seq/slot、aging、懒排序、type/tier反馈与eviction |

想知道“先回收谁”，读传统 LRU 或 MGLRU；想知道“选中的页怎样才能安全释放”，读回收执行；想知道“怎样找到并撤销用户映射”，读 Rmap。传统 LRU 和 MGLRU 按配置及运行开关选择，不应把两套实现画成同一条固定流程。

<a id="constraint-topics"></a>

## 3. 页形态、位置、计账与承诺

| 专题 | 重点 |
| --- | --- |
| [THP](page-size/02-thp.md) | PMD候选/提交、合并临时开销、失败恢复与两种拆分 |
| [HugeTLB](page-size/03-hugetlb.md) | 池/预留账、消费恢复、实例化竞争与最终归还 |
| [CMA](page-size/04-cma.md) | Bitmap粒度/对齐、隔离迁移、最终取得和busy重试 |
| [NUMA](resource-control/02-numa.md) | Policy、地址交错、node/mask、新旧页与迁移 |
| [Memcg](resource-control/03-memcg.md) | 层级charge回滚、stock、high、保护及身份 |
| [Overcommit](resource-control/04-overcommit.md) | 承诺与驻留分开、limit/保留量、策略判断和撤销 |

原 [大页组合页](page-size/01-hugepages-cma.md) 与 [资源组合页](resource-control/01-numa-memcg.md) 保留为导航；正文已经拆开，避免不同设施各只介绍一点。

## 4. 查询与核对

实际调用查 [API 选型](../api.md) 与 [API 详细参考](../api-reference.md)；想重新把专题放回整体结构，返回 [总览阅读地图](../00-overview.md#map)。

SLUB 的独立核对见 [SLUB 附录](../99-evidence.md#slub-rewrite)，其余专题的重写核对见 [核对附录](../99-evidence.md#other-rewrite)。整套文档已进行第一版内容审查，解释的是选定关键实现，不表示已覆盖 mm 的每种后端和配置，也不代替目标机验证。页面固定和内存热插拔目前只在机制分篇中介绍，尚无独立实现专题。
