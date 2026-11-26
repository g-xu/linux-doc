# memcg与OOM：本次独立审阅

正文[12](../12-memcg-oom.md)，[写前约定](contract-memcg.md)。主作者当前主agent；新独立只读挑战者Averroes，ID `01a20dff-9bcc-74c3-b8cb-df14f10ba4db`，不沿用旧Plato。

## 第1轮挑战及回应

初稿完整hash `25ddd45cc981b0adc5c87a278df9e4068e07c9e6968db891770d574dd98ac53f`，机制hash `8abddb05397911c7ec3c64dea2865672afa048a02fc169e33197f69739c3e091`。技术/教学暂不通过。

| 编号 | 障碍/证据 | 作者回应与修改 | 状态 |
| --- | --- | --- | --- |
| C1 | events祖先传播缺memory_localevents条件；memcontrol.h/cgroup-v2 | 接受，5/6相关段落与表格全部限定挂载选项，不拿父层无事件否定子层 | 待复核 |
| C2 | 保护缺回收根/80-20计算/min-low闭环；effective/calculate_protection/vmscan | 接受，3.3 R→T→A/B、根R预算100两场景50/50与80/20，根T直接子组采用设置、递归条件、min不按low放宽 | 待复核 |
| C3 | CSS/objcg/stock名称未形成状态账本；memcontrol stock/charge/objcharge | 接受，2定义CSS与对象O→objcg→memcg，3.1批量64申请1/再命中/余62排空/最终folio释放，分引用与额度及local_lock | 待复核 |
| C4 | 父子oom.group选择不知是否含兄弟；get_oom_group/oomkill | 接受，5 T/A标记、受害A，全局根R选择T含B，子域A不扩到T/B；保护与异步移域条件，图3同步 | 待复核 |

作者逐项核目标源码后修订，不自动resolved。作者20链接、三图解析/文字连线及1280/480临时预览无错；挑战者未实际浏览器渲染。未写控制文件、运行内核/耗尽/改配置。待同一挑战者完整重读正文/约定/三图，技术教学分别复验。

## 第1轮复验与第2轮修订

修订完整hash `65a83315208b4530cb6ff0b47eba235d86904e5d348ff4b1df7605e96b4b253a`，机制hash `9dc7a776831f61f39dd43889b315f9a23e8e214a4b4d0a80a7a00d88b6bc9e29`，作者20链接、三图及1280/480临时渲染无错。

Averroes完整复验C1–C4 resolved，技术范围通过，但新增C5为open：Q7没有具体输出单位，教学尚不通过。作者接受核memory_current_read/stat及PSI本地文档，6.2补容量字节、事件计数、PSI百分比/微秒，stock64P→2P及实际页大小条件；其他正文图不改。等待同一挑战者第2轮完整复验，不能用技术通过代替两项通过。

## 第2轮完整复验

完整被审正文hash `51729cc8d0f9635a20deff4744658d19c3f63f8981dfe4eb4ff30f22be0a3dd3`，图01 `21c603766e307eb5236a5dd716350dc1531f5abf1318108105c5d7d5a829b709`、02 `6787b68c2a124c6709cd2c134242e29441693c37bb88e4a0ec3f26dd26cfaffd`、03 `c9ace59c5ecf9ff3f66ac237d0589a538d56ea31a07f824fff2c32c6caea4a6d`。

Averroes完整重读正文/约定及三图，C1–C5全resolved，无新增，技术与教学整体分别通过，第2轮结束。Q0在2，Q1/Q2在3.1图2，Q3在3.2/3.3，Q4在3.4，Q5在4/5图3，Q6在5/6，Q7在6。额外变条件F1先释放再排stock、80/40保护分摊66/33、撤T标记全局只选A和单位换算均成立。无待裁决项，用户未验收；此后只改文末状态。
