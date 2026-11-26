# 10迁移与规整：教学补全与独立复验

本轮当前状态：第2轮技术/教学审阅通过，待用户确认。以下保留写前与阶段快照，最终逐项复验见末尾。

保留contract-migration全部任务、普通文件P/Q的映射/索引/锁/引用、等待者不额外get、全映射检查、模式/force/fallback、八页空间/水位及大folio计数。按真实机制重组：3单页交接，4双扫描规整，5批次结账，删除重复的概述章节。

新增读者任务：区分PFN游标与已隔离列表；沿compaction_alloc/free推演freepages及nr_freepages；批内folios/ret_folios/返回from不是整批同时发布；A/B/C与Q0/1/2成员及所有权去向；无捕获再申请与实际capture page输出不同。新图4双扫描，图3重画实际列表状态。核对migrate_pages/batch、compact_zone/alloc/free/release及page_alloc捕获。新独立只读挑战待执行，最多三轮全文四图复验；未运行或编译实验。

## 第1轮

Bernoulli，ID `01a210b6-f8a2-7f81-ade0-006d2250f18a`，首审全文四图/原任务。技术在声明模型内通过，教学待修改；机制hash `57d6ef4fee0ea0634cff060927c0140798157d7d868081783465ddd803685888`。

| 编号 | 障碍/依据 | 作者回应与修改 | 状态 |
| --- | --- | --- | --- |
| C1 | 冻结未定义、0不释放及缓存取引用配合不明；page_ref/migrate/filemap | 接受，3.2在使用前定义比较冻结非put、P2→0→1→最终put、取非零引用/重验索引，图2同改 | 待复核 |
| C2 | 扫描入选与执行模式混在一起；compaction/Kconfig | 接受，4.3源资格表、NOFS/unevictable/writeback/脏无callback、非完整SYNC的异步隔离约束，RT默认与未知运行值条件；图4同改 | 待复核 |

作者核对源码后回应，不由作者单方面resolved；原主体/计数/失败范围不变。初稿26引用/四图/1280与480机械检查无失败，修改稿待同一人全文四图复验。

第1轮同一挑战者完整复读后C1/C2均resolved，教学通过，主干技术通过，但新增C3源码定位错误进入第2轮。被审机制hash `24edc328a4420f20040e469c20bb764ff2e84cb5da7fb36fb1364a52aea558a4`。修改稿29引用/四图/1280与480检查无失败。

## 第2轮

C3：来源第8节把缓存助手误写为filemap_get_entry，本地实际为mapping_get_entry（filemap.c:1873）。作者接受，搜索确认后更正名称，不改变冻结模型或图。待同一挑战者整篇四图复验，不因只是一处名称便由作者宣布通过。

第2轮同一Bernoulli完整重读正文/四图与原Q0–Q7，C3 resolved，C1/C2保持resolved，无新增实质问题，技术/教学分别通过。机制hash `92a44372d5dd9ee55ce823ece14c611ee8e539c70c12115930c5f1f125c1fb53`；四图未变。最后修改仅函数名称，最终机械复查与全套新快照一起保存。未编译、运行规整/迁移或目标机实验；尚待用户确认。
