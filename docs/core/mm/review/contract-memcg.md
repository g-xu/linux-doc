# memcg与OOM：写前约定

本地6.1.158、arm64、RT补丁；启用适用cgroup v2内存控制条件化，普通收费请求不走特权force。校准01/03/04解释能力。

| 任务 | 应能推演 |
| --- | --- |
| Q0 | 物理位置/组归属、folio/objcg/page_counter/css/stock及共享页/RSS区别。 |
| Q1 | 子94父98申请4的层次取消、内部重试成功、最终失败和准备页寿命。 |
| Q2 | stock/批量缩小、GFP/force条件及成功绑定到最终uncharge。 |
| Q3 | high/max、祖先额度、延后回收/惩罚、min/low有效层次保护。 |
| Q4 | commit/RSS/charge阶段分开，mmap成功不保证所有后续供给。 |
| Q5 | OOM范围/资格/评分例，组杀例外及reaper/异步持有不即时释放。 |
| Q6 | oom/oom_kill/group事件、父子events/local及全局杀组内任务不同。 |
| Q7 | high/max更新后残留、reclaim部分成功失败、读取单位/范围及接口边界。 |

三SVG：层次/位置、charge回滚与重试、OOM过程。需处理参数失败与更新后退出不同，不写控制文件或运行耗尽。新只读挑战最多三轮，每轮完整复验技术与教学。
