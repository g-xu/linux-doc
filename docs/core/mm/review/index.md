# 内存管理专题草稿索引

**外部图解增补：** [16 篇来源对照与分层阅读路线](../reference-reading.md)可按「RAM 到对象」「程序地址到文件后备」「文件内容到应用」跨专题阅读。01–09 有局部正文或图示增补，见[本轮独立复核与终检](review-reference-reading.md)。旧审阅指纹和通过状态仅对应其当时版本。

从[总览](../00-overview.md)建立模块关系，再按当前问题进入下表。本地基线为Linux 6.1.158、arm64、RT补丁；页大小、拓扑和未确认功能保留条件。每篇有缩略词与问题入口、连续模型、关键条件、源码和接口责任。

**上一轮02及05–14共11篇曾逐篇补全并完成独立整篇复验。** 该轮补的是概念模型、通用规则、连续状态、源码/接口边界与机制图，并重组重复段落。作者此前“全部达到校准深度”的判断保持收回，审阅一致也不等于已经验证真实学习效果相当。[教学补全记录](pedagogy-revision-mm-series.md)保留该轮历史状态；最新版本以本次队列和各篇新记录为准。

总览、01、03、04已获用户反馈认可的机制正文保留。03的slab/槽位澄清及04的GFP修订分别保存在其审阅记录；认可继续不等于逐项技术或目标机验收。

上一轮机械结果与指纹见[补全终检](verification-pedagogy-mm-series.md)。[原完成队列](completion-mm-series.md)、[旧图示终检](review-figures-final.md)、[旧统一终检](verification-mm-series.md)与[旧批次记录](review-mm-series.md)保留历史，不自动作为本轮版本的通过依据。

08 随后按用户反馈重写了基础概念、场景与最小读写周期，其最新状态见[入口模型重写记录](review-entry-08.md)。上述补全终检中的 08 指纹与审阅结论对应重写前版本，不自动适用于当前正文。

上一轮入口修订见[基础模型修订队列](entry-revision-mm-series.md)：02保留用户试读认可版本；05–07、09–14共九篇曾逐篇完成新入口与全文/配图独立复核。05、10在第2轮结束，其余在第1轮结束，当时均无未关闭实质挑战。历史补全指纹与通过结论不自动适用于新稿，也不宣称已经验证与校准文章的真实学习效果相等。

[基础模型修订终检](verification-entry-mm-series.md)核对的是上一轮版本的九篇被审机制指纹、链接/图片、SVG几何及1280/480临时预览；总览、01–04、08在当时与各自既有机制快照一致。此记录不是对外部图解增补的终检，也不是正式站点构建或目标机验证。

| 专题 | 主要问题 | 审阅入口 |
| --- | --- | --- |
| [01 物理页分配](../01-page-allocation.md) | 位置、类型、PCP/Buddy、水位、快慢路径和归还 | [原记录](review-page-allocation.md) |
| [02 物理组织与初始化](../02-physical-memory.md) | 启动基本过程、范围/保留、描述符、早期资源与移交 | [基础重写](review-entry-02.md) |
| [03 内核对象分配](../03-kernel-objects.md) | slab/槽位、SLUB组合状态、远程释放与对象寿命 | [校准记录](review-kernel-objects.md) |
| [04 内核虚拟空间](../04-kernel-virtual.md) | vmalloc/vmap、GFP、所有权与延迟VA复用 | [校准记录](review-kernel-virtual.md) |
| [05 进程地址空间](../05-process-address.md) | 程序整体布局与规则、VMA/Maple Tree、修改残留与 mm 寿命 | [目的重写2轮](review-entry-05.md) |
| [06 arm64页表与TLB](../06-arm64-page-tables.md) | 正常访问协作、位段/权限、更新、ASID与表页回收 | [入口复核1轮](review-entry-06.md) |
| [07 缺页与COW](../07-page-faults.md) | 按需准备与私有隔离、匿名主线、独占/复制、收费与返回锁责任 | [入口复核1轮](review-entry-07.md) |
| [08 页缓存与回写](../08-page-cache-writeback.md) | 基本场景与读写周期、块/页内容有效、共享写、限速与错误 | [入口重写](review-entry-08.md) |
| [09 回收与交换](../09-reclaim-swap.md) | 资源循环与恢复内容、扫描/多代反馈、交换恢复/冻结、shrinker | [入口复核1轮](review-entry-09.md) |
| [10 迁移与规整](../10-migration-compaction.md) | 保持内容、改善空间、冻结/等待、源资格、隔离池结账 | [入口复核2轮](review-entry-10.md) |
| [11 生命周期与rmap](../11-page-lifetime-rmap.md) | 共享数据寿命、引用/通知、匿名chain、接口选型与锁/持有清理 | [入口复核1轮](review-entry-11.md) |
| [12 memcg与OOM](../12-memcg-oom.md) | 资源域与收费周期、stock/CSS、保护与扫描、OOM域及事件归属 | [入口复核1轮](review-entry-12.md) |
| [13 NUMA](../13-numa-policy.md) | 本地/远端与新旧数据、策略/查询、重绑/管理迁移和非RT反馈 | [入口复核1轮](review-entry-13.md) |
| [14 观测与诊断](../14-observability-extensions.md) | 诊断周期与证据、指标/通道、PSI规则/加权、DAMON估计/刷新 | [入口复核1轮](review-entry-14.md) |

本文档集仍是供工程师review的草稿，不意味着用户已验收或目标机运行验证。THP/CMA/KSM/HMM等可选扩展目前定位接入和使用边界，**未宣称每种扩展均已完成独立源码深挖**。未知配置不默认启用，内核未编译或加载。

没有自动写配置、调参、压力实验、部署、提交、推送或发布。RT=y明确排除本树自动NUMA平衡/THP，hugetlb及其他功能按自身条件判断。
