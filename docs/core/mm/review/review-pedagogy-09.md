# 09回收：教学补全与独立复验

本轮当前状态：第1轮技术/教学审阅通过，待用户确认。以下保留写前与阶段快照，最终逐项复验见末尾。

保留原contract-reclaim全部任务和A/B/C/D、范围/保护、P交换及引用冻结、shrinker/返回边界。新增解释任务：先从cost定义推出权重，再按保护/priority/各列表规模算扫描量；区分MGLRU逻辑代、flags、实际gen/type/zone列表以及tier保护。

3.2补active到inactive的临时链表/持有，3.3重组扫描方式→cost反馈→通用公式→实际扫描量，4补F/G标记先更新、sort后搬链及窗口规则。新图4显示实际槽状态，旧概念图保留历史但正文不再用其代替实现模型。源码核对swap/workingset/vmscan和mm_inline。新的只读挑战待执行，最多三轮，原任务及新任务全文三图复验；不运行回收或交换实验。

## 第1轮

Lorentz，ID `01a210ae-40e1-7ea3-9bac-a75af6a92867`，首审整篇/三图、旧约定及历史修订，技术/教学未通过；机制hash `bc7424fd4fa6fe7dd27414bb196181332034ded1dda1f9cc3aba78650246eee8`。

| 编号 | 障碍与依据 | 作者回应与修改 | 状态 |
| --- | --- | --- | --- |
| C1 | MIN2淘汰限制未解释；scan_folios | 接受，4.1两代10–11返回0，aging到12才有旧10候选；图4补门控 | 待复核 |
| C2 | file_is_tiny输入不可判断；prepare_scan_count | 接受，3.3三条件、20/10/40/64/priority4实例及三种条件变化 | 待复核 |
| C3 | workingset/shadow/refault到cost断层；workingset.c | 接受，3.2先定义历史和年龄，3.3同偏移F到G的距离30/10与历史标记三对照，分别refault/activate/restore-cost | 待复核 |
| C4 | 图3释放P后缓存命中身份不明；remove_mapping/swap_state | 接受，5正文区分释放前命中P与释放后Q；图3重画两分支及新缓存来源，desc同步 | 待复核 |

原公式、列表规模和标记/搬链推演经首审核对成立，新增不削去原任务。初稿36引用/三图/1280与480检查无失败；修改后待同一人全文三图复核。

同一Lorentz复读全文、三图及原Q0–Q7，C1–C4均resolved，无新增实质问题，第1轮结束。技术与教学分别通过；成本来源到权重、两代门控、标记到搬链、交换恢复身份的推演闭合。机制hash `150821bfd029fc6ca45b397feb7b840c169019299e0803c84c929a194a746ff1`，图3 `e38cb7cc10da87fe1c3970c50ebdabb31236a6c78c708d91f859548a7fea2960`、图4 `e3f217ef2cbaabd1fd9ef10e46bba3808b86a5ba09ef3edfd5897e1ce3b9f95a`。修改稿36引用/三图/1280与480机械检查无失败。未编译、运行回收或交换实验，不代替用户确认。
