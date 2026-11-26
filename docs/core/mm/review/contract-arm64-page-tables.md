# arm64页表与TLB：写前约定

本地Linux6.1.158、arm64、RT补丁前提；目标页大小/VA/PA/功能未知，位段模型显式选择4KiB/48位VA/4级、未标记普通地址。边界同[本轮队列](completion-mm-series.md)，不执行寄存器或页表修改。

| 任务 | 应能完成的判断 |
| --- | --- |
| Q0 | 区分VMA、页表数据结构、描述符、硬件遍历与TLB；折叠层不增加真实表页。 |
| Q1 | 将索引1/2/3/4与偏移0x120组成VA，逐级取表再落到数据页，改变为PMD block时判断早停和对齐。 |
| Q2 | 区分none、软件非驻留、present/protnone、硬件valid、可访问；解释PTE_WRITE/RDONLY/dirty及硬件并发变化。 |
| Q3 | 推演普通后备替换中准备、锁下重检、清旧/失效、新关系安装、旧关系结束或放弃，不能只覆盖后再flush。 |
| Q4 | 用写保护与硬件dirty竞争推演原子助手保留最新状态；合法访问标志更新不套用全部BBM，也不拿它替换PFN。 |
| Q5 | 区分TTBR根与ASID来源，解释软件generation不自动清硬件旧tag和本地reservation/flush_pending协调。 |
| Q6 | 判断TLB page/range/mm/kernel不同范围、DSB/TLBI/ISB与缓存维护，数据页和表页回收/RCU不是同一责任。 |
| Q7 | 正确定位页表接口、保护与配对，不给模块随意改用户页表；变页大小/PA/contiguous/MTE时保留必要边界。 |

图计划：4级位段/表遍历、条目状态决策、后备替换与原子写保护对照。重点为模型、条件和具体失败/竞争，源码清单不代替讲解。新独立只读挑战者最多三轮，整篇复验含title/desc；用户后续统一review。
