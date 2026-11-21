# 内存管理深入专题

这里承接 [总览](../00-overview.md) 与已有机制分篇，面向已有内核知识、希望从机制进入实际实现的工程师。统一基于 Linux 6.1.158 和 arm64；目标 `.config` 未指定，条件分支不等于已经启用。

本批文章均为待内容 review 的草稿，图示是原生 SVG 和源码规则推演，未执行内核实验或站点预览。每篇围绕实际对象、状态变化、正常路径与竞争 / 回滚边界展开，不声称穷举该机制所有实现。

## 1. 资源怎样到达调用者？

| 专题 | 主要问题 |
| --- | --- |
| [Buddy](page-allocation/01-buddy.md) | 空闲块怎样组织、拆分和合并？ |
| [PCP](page-allocation/02-pcp.md) | CPU 局部库存怎样与 Buddy 批量交接？ |
| [页分配慢路径](page-allocation/03-slowpath.md) | 没取得块后，怎样区分许可、供给和布局问题？ |
| [SLUB](object-allocation/01-slub.md) | 对象槽位怎样在本地、远程和节点集合间流转？ |
| [Vmalloc](virtual-allocation/01-vmalloc.md) | 区间、后备页和映射怎样分阶段建立与撤销？ |

## 2. 内容怎样进入地址空间，又怎样退出？

| 专题 | 主要问题 |
| --- | --- |
| [arm64 缺页与 COW](address-space/01-fault-cow.md) | 候选准备后怎样重查并提交页表？ |
| [页缓存与写回](page-cache/01-cache-writeback.md) | 缓存成员资格、内容有效性与再次脏化怎样协调？ |
| [回收](reclaim/01-reclaim.md) | 一个候选要跨过哪些条件才允许复用？ |
| [Rmap](reclaim/02-rmap.md) | 对象索引如何找到候选，怎样确认真实映射？ |

## 3. 哪些政策和形态约束会改变上述路径？

| 专题 | 主要问题 |
| --- | --- |
| [大页与连续区域](page-size/01-hugepages-cma.md) | THP、HugeTLB 和 CMA 的对象、映射与供给协议哪里不同？ |
| [资源约束](resource-control/01-numa-memcg.md) | NUMA 位置、memcg charge 和未来承诺如何分开判断？ |

阅读时可以沿同一条状态变化进入源码，也可以按实际问题跳转。用法查询仍由 [API 入口](../api.md) 与 [详细参考](../api-reference.md) 承担；本批没有重新扩写其他 API 族。

证据及已核对的实现边界见 [核对附录](../99-evidence.md#more-deep)。完整 MGLRU 算法、SLUB 调试与 hardening、swap 后端、NUMA balancing、启动 memblock 和热插拔等未在本批穷举，不以目录数量表示全部内存管理源码已覆盖。
