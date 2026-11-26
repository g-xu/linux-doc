# 回收与交换：写前约定

本地6.1.158、arm64、RT补丁；主例普通基本页、传统LRU，MGLRU独立条件化。对照01及校准03/04解释能力。

| 任务 | 应能推演 |
| --- | --- |
| Q0 | 回收/迁移/规整、范围及scan_control/lruvec/隔离持有。 |
| Q1 | 同轮A/B/C/D冷页，区分价值与dirty/pin/后备/上下文资格。 |
| Q2 | 两次PTE引用、referenced、active/inactive与锁竞争改变结果。 |
| Q3 | 比例分支/成本与swappiness，不推导全局0绝不swap；多代aging/eviction/tier/窗口。 |
| Q4 | P→e→交换PTE→保存→驻留释放→再次fault，区分RAM/项使用/缓存。 |
| Q5 | e加入失败、写失败redirty、额外引用与恢复竞争清理。 |
| Q6 | 冻结再脏重检理由、预期2引用条件、隔离对象归队、shadow不等内容。 |
| Q7 | GFP/作用域/shrinker合同，nr_reclaimed与compaction_ready返回及诊断口径。 |

三SVG：范围、代推进、交换稳定阶段；A/B/C/D与F冷热连续观察、P交换闭环、引用冻结反例。核心不得外包给其他专题。最多三轮新只读挑战、作者回应、整篇技术/教学复验；不运行回收/交换或调参实验。
