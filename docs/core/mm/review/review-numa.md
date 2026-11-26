# NUMA：本次独立审阅

正文[13](../13-numa-policy.md)，[写前约定](contract-numa.md)。主作者当前主agent；新独立只读挑战者Confucius，ID `01a20e0e-7c27-7f81-98ca-256e35875ce9`，旧Plato不沿用。

## 第1轮挑战与回应

初稿完整hash `4d3c3fed9332d28692e3b06726d40030493905ec933b960ff4b332bef096a5bc`，机制hash `3813e15ce8920301c97853dd8d4c55c578de27d1613a79a2765ae1c7e88a24d1`。技术/教学暂不通过。

| 编号 | 障碍/证据 | 作者回应/修改 | 状态 |
| --- | --- | --- | --- |
| C1 | 策略MOF准入/迁移拒绝不够具体；fair/mempolicy | 接受，3.3默认节点策略MOF、显式LOCAL/BIND及flag准入，两采样节点拒绝例，图3同步 | 待复核 |
| C2 | ADDR查询不回退任务，与有效策略区别不明；do_get_mempolicy | 接受，6任务BIND/VMA DEFAULT/页N0五种查询与分配对照，ADDR+NODE GUP可触页 | 待复核 |
| C3 | BIND zone动态阈值无法判断；apply_policy_zone/nodemask | 接受，3.2阈值来源、只有Movable例外、条件表/假设Normal与低DMA，NULL不取消cpuset | 待复核 |
| C4 | 重绑无组合状态/序列与重试，未限模式；cpuset/pagealloc/mempolicy | 接受，四节点例限BIND/INTERLEAVE，preferred/many另一回调，旧∪新/重绑/最终新及失败前cookie重试 | 待复核 |
| C5 | move_pages数组有效范围及正返回不明；migrate | 接受，6查询与移动返回分类/部分写入，三页批次两成功一失败、status未知与重新查询，不读未写0 | 待复核 |

作者逐项核目标源码后修订，不自动resolved。作者21引用、三图及1280/480临时预览无错；挑战者实际图渲染未见文字越界/连线穿字，未正式站点窄屏。未改策略/配置或运行迁移调度实验。待同一挑战者重读整篇/约定/三图并给技术教学结论。

## 第1轮完整复验

修订完整hash `5f0da57fd13f98ad027a3be142824ca2ac88fa9858cbf25f85e2eb09f2795317`，机制hash `1932a9fa4a050cc25f47da60d78bfb3f62b2fa3be8e5a267e017b02cbffa61ca`。图01 `8b7e6139c79ed1a9dddb7605e77f69a8d862ab896c2bee7456c64f7a5c086854`、02 `ce504e91f5048e72a18dabbcc54272d95b18ad65ba416b6d90ec283a93db82e1`、03 `94c60fca414be9f0bfb620b00dec458ce2f0c1a41f31ef6c5af4d37fc677bcfd`。

Confucius完整重读正文/约定/三图：C1–C5全resolved，无新增，技术与教学整体分别通过，第1轮结束。Q0在2，Q1在3.2及6查询，Q2在3.2两轮，Q3在交错段，Q4在重绑段，Q5在修改阶段表，Q6在3.3图3，Q7在6。独立复验DEFAULT查询/任务回退、低zone、不相交重试、采样拒绝及未写status均成立。无待裁决项，用户未验收；之后仅改文末状态。作者工具21引用及三图、1280/480渲染无错，独立检查计入的引用口径23不同，不改写为同一工具数量。
