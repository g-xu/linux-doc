# 页缓存与回写：本次独立审阅

交付前仅补全两图支路箭头，正文机制未变；新图hash与独立渲染复核见[图示终检](review-figures-final.md)，原版本记录保留。

正文[08](../08-page-cache-writeback.md)，[写前约定](contract-page-cache.md)。主作者为当前主agent；独立只读挑战者Godel，ID `01a20dce-5265-70a2-9aaa-cd51b3122b82`，不沿用旧Hypatia结论。

## 第1轮首审与作者回应

首次全文SHA-256 `075fa8b716b1159d84b1eab091212ded944f696aeb2a4b86a0d0eaed3f62d249`；机制hash `3e7253c22c315f10601a6e9d651a7ed77ac5211be588aa180f7f3e725c1cc668`。技术暂不通过、教学不通过，主干正确信息不能替代完整协议。

| 编号 | 挑战与证据 | 作者回应与修改 | 状态 |
| --- | --- | --- | --- |
| C1 | 缓冲写缺write_begin/end、短接受过程；filemap/buffer | 接受，4.1沿F写64、接受32、未就绪短提交0及来源失败，解释迭代器/位置/持有责任 | 待复核 |
| C2 | wb只有名称，无法串起后台对象；backing-dev-defs/page-writeback/fs-writeback | 接受，2.1定义BDI/wb/队列/带宽及inode→mapping→F过程 | 待复核 |
| C3 | 不知后台与完整性扫描对(1,1)如何行动；write_cache_pages | 接受，5.3解释wbc、三tag、预算/范围与NONE跳过/ALL等待，不当内容快照 | 待复核 |
| C4 | dirty统计和RT任务/组域条件缺失；page-writeback | 接受，6区分总量与后台待写口径，600分布对照、计算后假设阈值及rt_task/CGroup writeback条件；图3同步 | 待复核 |
| C5 | 缓冲读范围就绪边界缺失；filemap_range_uptodate | 接受，3.1限定整folio主例并解释部分块例外，保守骨架不等通用read必然行为 | 待复核 |
| C6 | 截断端/PTE失效/等待寿命未闭合；truncate/filemap | 接受，3.2补大小先更新、双解除映射、缓存移除与块释放，页锁下等writeback及invalidate_lock分工 | 待复核 |

作者逐项查目标源码后修订，接受不自动resolved。图2删除无语义的指向框间空白箭头，其余阶段箭头保留。待同一挑战者整篇重读，包括三图/title/desc和Q0–Q7，分别给技术与教学结论。

## 初稿机械检查

26个引用有效，三图解析及文字/连线检查无错；1280/480临时Markdown无溢出或图片失败，不是正式Zensical构建。独立挑战者图形库加载失败，未完成自己的渲染；不冒充已验证。未编译C骨架、未运行内核、I/O、截断或调参实验。

## 第1轮复验与第2轮修订

第1轮修订完整正文hash `d69a602de1811d8762e95627a7602727fcd413167b47150e20d02cf22073939d`，机制hash `ec58298bff268cded6cef30e09b9067d16ba0607f1ce1a0c016336c63378788c`。作者检查29个引用、三图、1280/480临时渲染无错。

Godel完整复验：C1/C2/C4/C5/C6 resolved；C3仍open，技术/教学暂不通过。发现ALL或tagged选择TOWRITE不等同二者均等待，NONE+tagged仍跳过在途页。对应page-writeback.c两处分离判断及fs-writeback.c实际组合。

第2轮作者接受C3残留，5.3改成三组合表，分开扫描标记和等待决策，明确NONE+tagged真实可用。等待同一挑战者整篇复验，不因其余项关闭提前通过。

## 第2轮完整验收

Godel完整重读正文与三图，C3 resolved，其他五项保持resolved，无新增实质问题；技术和教学整篇分别通过，提前结束不进入第3轮。完整被审正文SHA-256 `baf3f58afd9e494097dfe80c8562705bae2029f563ad1e1372d939628123afb4`，此后仅修改文末状态。

最终图hash：01 `f5ff3a030216eb7daa397149d8cc0d699a31bb28cdd72a796ebfd870c4e1b53f`；02 `55bf55970f6fdeec83f042149f746cc3485b08429fd0f3da9147fa30fb2e31f3`；03 `4c9cd09468669b9b078ac6c33d786fd6aca251d4f1eb3a3b3fcab604904633f3`。

Q0在2/2.1，Q1在3.1，Q2在3.2，Q3在4，Q4在5.1–5.3，Q5在5.4，Q6在6，Q7在3.3/4.1/7。独立复验64/32/0写入、回退及后续失败、NONE+tagged不等待等变条件无回归。无待裁决项，用户未验收，挑战者未实际渲染。
