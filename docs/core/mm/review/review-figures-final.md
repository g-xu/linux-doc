# 本轮SVG箭头修订：独立图示复核

这是02–11共18张图的终检记录，不替代原正文深度审阅。主作者仅把同一个path里的多个有向子路径拆成独立path，使每支路都有末端箭头；不改变坐标、文字、图形及机制正文。原图hash保留在各篇历史记录，新图版本在此补充。

整套机械复查后又发现总览/01的两张旧图有文字间距或同类箭头问题，补充记录在文末；合计20图局部修订，不修改总览或01机制正文。

新独立只读挑战者Helmholtz，ID `01a20e00-bf74-78c0-9cfc-5277a10b3ff0`。第1轮读取18图、title/desc及对应正文段落，必要处核本地源码；自行Chrome渲染全部18图并逐图查看PNG，无实质挑战，不需第二轮。

## 复核结论

图文及条件模型一致；84条有向连线都有有效箭头，实际分叉/回边可见；342文字元素无已发现越界、覆盖或连线穿字。没有借用作者PNG或运行共享机械脚本；未修改文件、运行内核或验证Zensical整站嵌入。

自行渲染输出保存在任务可视化工作目录的 `independent-svg-r1-aOX6am`。审阅结束再读取，18图与对应读取正文未变。无待裁决项，结论不等用户验收。

## 修订图版本

以下路径相对于本目录images，SHA-256：

| SVG | SHA-256 |
| --- | --- |
| arm64-page-tables/01-walk | `23156f625b2088883013838d10613781f196991c803391dad4dfa2ee5df525c0` |
| arm64-page-tables/03-update | `cfa58b300dc5d98e01041440b53a381b2c988f2eaa8c46598533f9ced7052bc6` |
| arm64-page-tables/04-asid-rollover | `9427dae031899c677d6e8ac5191e8ce37fc78e4246ed8f420c0d9b7544957ccd` |
| arm64-page-tables/05-table-lifetime | `53173cd376fbcc68746002681d706caa282a7ac5c03dc885a9862ea00f18baaa` |
| kernel-objects/00-slab-slots | `22e7108f88b9684efe93af637b0b4978f83359935af59fb9e1597f8d83673823` |
| kernel-virtual/01-basic-mapping | `d99dcefa107670641ccd9381ebce0491c44c052ebda0a06aafc1eac8086e4f33` |
| kernel-virtual/04-ownership | `cad045b8d3e17a80150d8ae638110b32c35c15d4fd654fe02994b1e70d33cea1` |
| kernel-virtual/05-lazy-return | `8d56e0db19b6c72fced51839e4f604063e06bf1e794d09b50afb37dd8c0e575b` |
| migration/02-transfer | `31a3113dc1b4c827ccdf8ddfc8bb86767f3629480749d57a2f13231a4091c9fc` |
| page-cache/01-objects | `566dee19cfef317ffaf1dbbbab1b07da3fc2ccfe19aa79c2096b7a90e232eca7` |
| page-cache/02-writeback | `e8a2d1ac79d9b4cb053c86079d36f6ae3483d9e0713fc57432d8c7d0d4806696` |
| page-faults/01-decision | `09615d3dddb4a9a048d249ab9ec6ca193d0d811c36b873c49bdbb0edb6463262` |
| page-faults/02-anonymous-cow | `16e677a20da13be83c4ef88860cd991c000df7ec0cd22b6c4f0d9b97e4c23990` |
| page-faults/03-retry-lock | `78c64206006427c488014ecd2ba51b5ddaf3b80acd27cc1cddaec5e2b955a6ab` |
| physical-memory/03-handoff | `3e8aa8aa1cd40a8c4da2f55af546d38e201c62788b8437020ae27373f74d0e1d` |
| process-address/01-model | `bd2939f7f238507adba1cb71e0d1531d0f99868ec9458cd37a13464699b5c3e1` |
| process-address/03-mm-lifetime | `b79ff564b39a361eb0a85106c68e8f1aacc385b52dfb05ed566bd66292dc5e11` |
| reclaim/01-scope | `bd2e499b60026d984e5a6f54fb61cdb5a79076773301b6a45029f75b92977f07` |

## 总览与01补充

新的只读图审者Pasteur，ID `01a20e24-b6f2-7c22-ae83-0b94ac45c120`，自行Edge渲染总览旧图及2倍局部，未用共享检查脚本：06-fault-cow文字间距/返向步骤和生命线无穿字，图文一致，无实质问题。实际用图在总览5.3，任务入口误写5.1未沿用为正文位置。图hash `7511f3aa9cf16811ebb647e157e7300cd46a9bf76b03916da9daade314a52156`。

之后新增01的02a-candidates，仅拆两组多子路径为12独立有向path，坐标与文字不改。Pasteur独立首次检查该图，再整组完整重读/实际渲染两图：12条筛选箭头及总览10条箭头均完整，无文字遮挡/穿字，图文分别对应01的2.3及总览5.3，无实质问题。

02a-candidates图hash `25dda10c722cc2be01b2bbb4dec834e4cbda886d05cfe259d154a7ac8c46e048`，06-fault-cow仍为上列版本，渲染前后未变。图审不替代正文重新深度验收，不宣称其他浏览器/字体或正式站点嵌入已验证。合计20图局部修订均完成独立复核，无待裁决项。
