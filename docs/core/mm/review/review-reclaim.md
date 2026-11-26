# 回收与交换：本次独立审阅

交付前仅补全范围图支路箭头，正文机制未变；新图hash与独立渲染复核见[图示终检](review-figures-final.md)，原版本记录保留。

正文[09](../09-reclaim-swap.md)，[写前约定](contract-reclaim.md)。主作者当前主agent；新的独立只读挑战者Ramanujan，ID `01a20dda-0c3b-7950-8c9c-9029a0eec200`。不沿用旧Hypatia。

## 第1轮首审及作者回应

初稿完整hash `3d3808d51da09fc4fae6441dd83bdc3e7f9b9ba3013f5ad4ae77a9d828a5e721`，机制hash `ceb04271ed9feef036bf7803798cca0d3a1f0c4a9be6e74a5d3867014740a91a`。技术/教学均暂不通过。

| 编号 | 障碍与证据 | 作者回应/修改 | 状态 |
| --- | --- | --- | --- |
| C1 | demotion无swap可回收/累计量不等整机释放；vmscan | 接受，阅读说明排除主例demotion，1补启用与当前节点/整机差别 | 待复核 |
| C2 | 范围/保护不能判断；shrink_node_memcgs | 接受，2补T/L/M有效low/min、首跳过与low放宽，不等物理预留 | 待复核 |
| C3 | 脏B缺直接/后台实际分流；dirty/may_enter_fs | 接受，3.1补kswapd/reclaim/PGDAT_DIRTY等门控及NOFS/NOIO区别 | 待复核 |
| C4 | 成本变化/全局0不swap缺推演；get_scan_count/workingset | 接受，3.3补file_is_tiny强制匿名及成本10得到147/281权重 | 待复核 |
| C5 | tier反馈/窗口槽复用不足；mm_inline/mmzone/vmscan | 接受，4.1补N/tier、tier_idx变条件及10–13到14的槽复用，图2同步 | 待复核 |
| C6 | 成功/竞争恢复没有结账；memory/swapfile | 接受，5.1分别PTE项使用、swapcache、fault持有，缓存身份/原项双重检，图3同步 | 待复核 |
| C7 | shrinker计数/部分扫描/STOP合同缺失；shrinker.h/do_shrink_slab | 接受，7.1加对象单位、EMPTY与0、预算/实际、部分释放与死锁STOP | 待复核 |

作者查目标源码后接受修订，不自动resolved，Q0–Q7保留。初稿29链接、三图XML与文字/连线、1280/480临时预览无错；挑战者预览访问拒绝，未实际渲染。机械与内容验证分开，未运行内核/交换/调参。

## 第1轮复验与第2轮回应

第1轮修订完整hash `6ef62447c86362a04ed54bfe505db966bd2ce135c8e0d1e15aa1ef3057a632a4`，机制hash `8875fef7cb207048701c1ef64c575706d2600388c6b9b57ace0380c7a2aef819`，作者34链接及三图、1280/480临时预览无错。

Ramanujan整篇复验：C1/C2/C3/C4/C6/C7 resolved，C5的tier已满足但窗口条件仍open；新增C8，技术/教学暂不通过。

| 编号 | 剩余障碍 | 第2轮作者回应 | 状态 |
| --- | --- | --- | --- |
| C5 | can_swap=false匿名可推进min而不搬槽内容，原窗口概括太宽 | 接受并核inc_min/max_seq，4.1限定原模型，另说明受限匿名逻辑边界/物理链表不同；图2同步 | 待复核 |
| C8 | 内部接口表“允许递归依赖”误导为可递归直接回收 | 接受，7改为合法内部路径建状态、避免递归/依赖冲突；仅造scan_control不足以授权调用 | 待复核 |

第2轮待同一挑战者整篇重读，旧关闭项仍需复核无回归，不提前宣布通过。

## 第2轮完整复验

完整被审正文hash `7cfc0c5a8ad3dec322f7a606f7c370b4c989de722c9422d20e6b3902c5b6d563`，图01 `dac2fe99bf0d6e660722f8b6f16e2601fed7cd390814f34591d1f499b7adea52`、02 `98f7257cb8f774d04c95a3bbaef9bd6919d0d41163b4bfc5da5a32913a17791c`、03 `61f5eb7f192f6408a41921942eccb50be1d4a46ad36f8a8f3cfa7f446b2729c2`。

Ramanujan重读全部正文/约定及三SVG/title/desc：C1–C8全resolved，无回归/新增，技术与教学整体分别通过，第2轮结束。Q0在1/2/6.2，Q1在3.1，Q2在3.2，Q3在3.3/4.1，Q4在5/5.1，Q5在5/6，Q6在6，Q7在7/7.1。独立复核两种窗口推进、成本与全局0、恢复结账及shrink合同均成立。此后仅更改文末状态；无待裁决项，用户未验收。
