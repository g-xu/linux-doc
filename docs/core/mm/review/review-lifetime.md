# 页寿命与rmap：本次独立审阅

正文[11](../11-page-lifetime-rmap.md)，[写前约定](contract-lifetime.md)。主作者当前主agent，新独立只读挑战者Euler，ID `01a20df2-88cd-7592-9c5e-533eace84e48`，旧Plato结论不沿用。

## 第1轮挑战与回应

初稿完整hash `804f0cefb9da43bf91dd7429a2e5c8cf892a642aa780bacf3afe23ef2489717b`，机制hash `9d214b7dc73982153570223b54ae2d20a06f14ccb80ae726624147fc1e70b3d1`。技术/教学均待修改。

| 编号 | 障碍与证据 | 作者回应/修改 | 状态 |
| --- | --- | --- | --- |
| C1 | 匿名chain/root及取得寿命不够具体；rmap fork/保护/来源 | 接受，3.3以A/B、Av0/Av1/P/Q关联表，root锁与对象树区别，RCU/映射复检与迁移预取得引用分开 | 待复核 |
| C2 | 真实撤PTE为黑箱；rmap/page_vma_mapped/vmscan | 接受，3.1即时失效普通文件例、dirty/RSS/rmap/put，TTU_SYNC竞争与mlock退出，批量失效条件化 | 待复核 |
| C3 | 读pin unshare/已pin父fork只结论；gup/rmap.h/memory | 接受，3.5两前后表：子只读共享P隔离Q，已pin父保持P而复制子Q，不升级权限/不精确证明pin | 待复核 |
| C4 | S未定义、SVG多子路径仅最后箭头可见 | 接受，图2文本/desc定义S=PAGE_SIZE，三图每条有向支路独立path | 待复核 |

作者逐项查本地源码后修订，接受不自动resolved。作者23链接、三图解析/文字/连线、1280/480临时渲染无错；Euler独立渲染发现箭头不完整，已据此修正，不能用机械交叉检查替代语义审阅。未编译例子、运行内核/pin/DMA或修改配置。待同一挑战者完整重读正文、三图及约定再给技术/教学结论。

## 第1轮复验与第2轮修订

修订完整hash `7d3cb6cec0804708a230f30a6c4b678b8439fd36290c509efee6e272280724ba`，机制hash `1f32e9a73c0ec77bf0e0b333f2665bbd857dc1ea2b9ab02797c320dd1f6d4aef`；作者23引用及三图、1280/480临时预览无错。Euler完整重读，C1/C3/C4 resolved，C2仍open，技术/教学暂不通过。

C2新增撤项通知顺序错误：range_start在PTL外，适用回调可睡眠，不应写PTL内开始通知。作者接受并核rmap.c/mmu_notifier.h，第2轮改为候选/范围→PTL外start→walker取PTL重检→清项/dirty/RSS/rmap/put→释放PTL→范围end，并说明阻塞期间变化仍要重检。其余正文及三图不改，待同一挑战者完整复验。

## 第2轮完整验收

完整被审正文hash `77a393a1ef1b4f7f6530e42bae07a0aa5cff796c8e1c95e5d27207ff499cca6a`，图01 `10d7f0c0a5ebf5e20e0791c7f5eccbc54c002415ad8c77c33a60005cd148a25a`、02 `2eb50c0fb7c56daee87f4fc4ffafb51213cbbe2709d27dd26b6f0cab7995c1d3`、03 `88de07d790fecb94c6997612d4e9aa5d8d1dc4b03706b6a24b9c418080a4734b`。

Euler完整重读正文/约定/三图：C1–C4全resolved，无回归或新增，技术与教学整体分别通过，第2轮结束。Q0在2/3.3保护，Q1在3.1，Q2在3.2图2，Q3在3.3，Q4在3.4，Q5在3.5，Q6在4图3，Q7在6。独立复验start期间项变化、mlock拒绝、保守独占复用与fast有效前缀都成立。无待裁决项，用户未验收；此后仅改文末状态。
