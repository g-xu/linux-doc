# 调度文档：基线、来源与核对范围

本附录供[总览](00-overview.md)、[深入专题](sub/index.md)和[API 参考](api-reference.md)共用。正文解释机制，这里集中说明资料如何使用、关键结论在哪里核对，以及没有验证什么。历史写作日志不作为读者必须阅读的章节。

## 1. 分析基线

<a id="rt-baseline"></a>

| 项目 | 依据与边界 |
| --- | --- |
| 版本 | 本地 `linux-6.1.158`，Makefile 的版本字段为 6/1/158，`localversion-rt` 为 `-rt58` |
| 架构 | arm64 / AArch64，架构实现只追踪本地 `arch/arm64/` |
| RT 前提 | 用户明确指定源码均已打 RT 补丁，统一假定 `PREEMPT_RT=y` |
| 独立配置 | SMP、PREEMPT_LAZY、FAIR_GROUP_SCHED、CFS_BANDWIDTH、RT_GROUP_SCHED、EAS 等按各自条件讨论 |
| 来源完整性 | 源包没有 `.git` 元数据，完整补丁来源没有逐项审计；不据缺少 `.config` 否定用户已指定的 RT 分析前提 |
| 已知缺项 | 缺少 `arch/arm64/include/asm/switch_to.h`，不把缺失包装宏作为已读源码 |
| 工作范围 | 文档与 SVG 内容重整、源码定向阅读和静态检查；不运行内核、示例、trace 或调参 |

2026-11-22 的重整借鉴了旧总览“设计思路、所有调度类、单 CPU 过程、多核协作”的整体组织。旧稿中 `.next` 类指针链、CFS 永远取最左、RT 只改抢占时机不改锁语义等说法不继承；这些结论以本地实现重新核对。旧目录保持原样。

## 2. 资料如何用于这套文档？

教学资料帮助组织问题和图示；字段、锁序、默认特性、单位与分支以目标源码为准。上游 6.1 文档也可能保留历史简化，不能只因版本 URL 相同就省略实现核对。

| 资料 | 借鉴内容 | 回源码时重点校正 |
| --- | --- | --- |
| [CFS 设计](https://www.kernel.org/doc/html/v6.1/scheduler/sched-design-CFS.html)、[nice 设计](https://www.kernel.org/doc/html/v6.1/scheduler/sched-nice-design.html) | 理想共享、带权服务、虚拟进度 | current/树、buddy、粒度、组层级与实际权重表示 |
| [调度域](https://www.kernel.org/doc/html/v6.1/scheduler/sched-domains.html) | 每 CPU 队列与分层平衡 | 分阶段 detach/attach、不同迁移类型和扫描预算 |
| [容量感知](https://www.kernel.org/doc/html/v6.1/scheduler/sched-capacity.html)、[schedutil](https://www.kernel.org/doc/html/v6.1/scheduler/schedutil.html) | 需求/供给、归一化、PELT 与调频 | 精确分段单位、util_est 激活结束、平台支持条件 |
| [Deadline](https://www.kernel.org/doc/html/v6.1/scheduler/sched-deadline.html) | EDF/CBS、准入、GRUB 状态及 0-lag | D<P 的下一补充时刻、负预算、revised wakeup 与定时器状态 |
| [CFS bandwidth](https://www.kernel.org/doc/html/v6.1/scheduler/sched-bwc.html)、本地 `Documentation/admin-guide/cgroup-v2.rst` | 共享/本地额度、层级、burst、统计 | v1/v2 ABI 与单位，已结束限流区间和周期计数 |
| [锁类型](https://www.kernel.org/doc/html/v6.1/locking/locktypes.html)、[RT-mutex 设计](https://www.kernel.org/doc/html/v6.1/locking/rt-mutex-design.html) | RT 锁环境、两棵树、继承链、owner 标记 | raw/普通锁、saved_state、top 替换、锁序与有效类 |
| [Completion](https://www.kernel.org/doc/html/v6.1/scheduler/completion.html) | 完成状态、初始化、超时及存活 | 本地 swait、complete_all 的 RT 上下文与回调收尾 |
| OSTEP 的[调度](https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-sched.pdf)、[比例共享](https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-sched-lottery.pdf)、[多核调度](https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-sched-multi.pdf)章节 | 给定假设后算模型，用时间线说明局部性与服务份额 | 只借教学方法，不把基础课程、其他版本/架构写成当前实现 |

用户态接口曾参照 Linux man-pages 的参数与返回说明，并回到 `kernel/sys.c`、`core.c`、UAPI 头文件检查目标行为。持续更新的 man-pages 不作为“Linux 6.1 实现版本”的证明。

## 3. 核心对象与运行协议

| 核对问题 | 本地定位 | 保留的关键区别 |
| --- | --- | --- |
| task、实体与 rq | `include/linux/sched.h`；`kernel/sched/sched.h` | task/类成员、current、树节点、on_cpu 各有语义 |
| 类排序 | `include/asm-generic/vmlinux.lds.h:127`；`kernel/sched/sched.h` 的类遍历 | 链接段顺序，不是 `.next` 指针链 |
| 公共入退队 | `kernel/sched/core.c:2088`、`:2122` | enqueue/dequeue 包装与 activate/deactivate 的资格发布不同 |
| rq 时钟 | `kernel/sched/core.c:673` 附近 | 调度时间、任务执行时间与 PELT 时钟不同 |
| 等待循环 | `include/linux/wait.h`；`kernel/sched/wait.c:305` | 登记后重检、信号/事件竞争、清理栈等待项 |
| 唤醒遍历 | `kernel/sched/wait.c:80` 附近 | 非独占项不消耗独占唤醒预算 |
| TTWU | `kernel/sched/core.c:4191`；`features.h:49` | state→on_rq→on_cpu，RT 默认 TTWU_QUEUE=false |
| RT 保存状态 | `kernel/sched/core.c:4036`；`include/linux/sched.h` | saved_state 成功匹配可只记录通知，不执行新入队 |
| 选择与调度 | `kernel/sched/core.c:5972`、`:6557` | fair 快路径/RETRY，主动阻塞与抢占，prev==next |
| 地址空间与低层切换 | `kernel/sched/core.c:5308`；`arch/arm64/kernel/process.c:522`、`entry.S:829` | mm 与线程栈独立；共享/借用 mm 与寄存器切换不同 |
| 旧任务收尾 | `kernel/sched/core.c:4975`、`:5196` | release on_cpu、引用收尾；恢复 A 的旧栈时实际 last 可是 C |

<a id="cfs"></a>

## 4. 调度策略的核对

| 策略 | 本地入口 | 阅读时跟踪 |
| --- | --- | --- |
| 权重来源 | `core.c` 的 `set_load_weight`、`sched_prio_to_weight` | nice 逻辑值与 arm64 缩放表示、idle 特例 |
| CFS 记账/基准 | `fair.c:694`、`:882`、`:592` | exec 增量、vruntime、min_vruntime 单调基准 |
| CFS current/候选 | `fair.c:4946`、`:5044`、`:4994` | 资格仍在、current 在树外、buddy 的有界选择 |
| CFS 粒度与放置 | `fair.c:726`、`:4909`、`:4648`、`:4740`、`:7529` | slice、tick、唤醒阈值、组层级与迁移 flags |
| RT 队列/选择 | `rt.c:1434`、`:1747`、`:1773` | 位图、链表、current、on_list 与 on_rq |
| RT 轮转/带宽 | `rt.c:2647`、`:905`、`:1002`、`:1049` | RR 剩余 tick 与 rt_time/runtime 是两种记账 |
| RT 多核 | `rt.c:2061` 附近；`cpupri.c` | pushable 与 current、临时引用、目标重检 |
| DL 对象/预算 | `include/linux/sched.h`；`deadline.c:837`、`:1037`、`:1052`、`:1309`、`:1385` | Q/D/P 与 q/d，耗尽、负预算、下一周期、根 RT 共享记账 |
| DL 唤醒/回调 | `deadline.c:909`、`:957`、`:1110`、`:1243` | 密度检查、D<P、业务资格与补参数分离 |
| DL 准入/回收 | `deadline.c:227`、`:392`、`:448`、`:1283`、`:2865` | total-old+new、active/total 带宽、0-lag、取消/回调引用 |

源码表是回读入口，不代替专题中的完整解释。数字都指这份本地源包；更换源码后以符号重新定位。

<a id="mechanism-depth"></a>

## 5. 信号、多核、资源与同步

| 机制 | 本地入口 | 核对重点 |
| --- | --- | --- |
| PELT | `pelt.c:57`、`:102`、`:180`、`:257`、`:306`、`:320`；`pelt.h:95` | 时间计量、三段累积、固定点衰减、输入/除数、归一化 |
| 历史归属/util_est | `fair.c:4174`、`:4283`、`:4299`、`:4330` | attach/detach、blocked/removed、聚合与样本更新 |
| 调频 | `core.c` 的 `effective_cpu_util`；`cpufreq_schedutil.c` | 类需求、IRQ、预约、rate limit、共享 policy 与驱动落实 |
| 放置/拓扑 | `fair.c:7459`；`topology.c` | EAS/affine/idle/慢路径是分支，先满足允许范围 |
| 平衡/迁移 | `fair.c:8137`、`:9971`、`:8408`、`:8459`、`:8624`、`:10540` | 迁移类型的单位、目标量、候选尺寸、锁与迁移窗口 |
| EAS | `fair.c:4416`、`:7260` | util_fits 的 0/1/-1，能量增量，overutilized 与平台条件 |
| CFS 带宽 | `fair.c:5182`、`:5222`、`:5310`、`:5397`、`:5480`、`:5527` | 领额、限流重检、祖先可见性、锁序、恢复/统计 |
| PI | `kernel/locking/rtmutex.c:1180`、`:655`、`:1064`、`:1288`；`core.c:7075` | 两棵树、top 替换、传播/撤销、trylock 重试、通知与取得锁 |
| RT 锁环境 | `include/linux/spinlock_rt.h`、`local_lock_internal.h`、`kernel/locking/spinlock_rt.c`、`rtmutex_api.c` | 普通锁与 raw 锁、迁移限制、状态保存、嵌套上下文 |
| 观察 | `fs/proc/base.c:510`；`kernel/sched/stats.h:210`、`:229`；`include/trace/events/sched.h` | schedstat 字段、事件记录点、TID 生命周期和延迟分段 |

<a id="api"></a>

## 6. API 契约与示例

| 接口族 | 核对来源 | 主要风险 |
| --- | --- | --- |
| nice/基本策略 | `kernel/sys.c`，`core.c` 的系统调用与属性修改 | 合法 -1、线程作用域、权限、reset 标志 |
| sched_attr | `include/uapi/linux/sched/types.h`，`core.c` 的复制/设置/查询 | size、flags、纳秒、uclamp 标度、合法性与准入 |
| affinity | `core.c:2996`、`:3015`；`kernel/kthread.c` 的 bind | 字节数、task 存活、可能阻塞、inactive/possible CPU |
| waitqueue | `include/linux/wait.h`、`kernel/sched/wait.c` | 反复重检、jiffies、信号、RT 通知上下文、发布/存活 |
| completion | `include/linux/completion.h`、`kernel/sched/completion.c` | done 消费、超时非取消、complete_all 的 RT 条件 |
| kthread | `include/linux/kthread.h`、`kernel/kthread.c:698` 及创建/退出 | ERR_PTR、停止响应、提交者停机、退出不自动 drain |
| 内核策略/直接唤醒 | `core.c:4474`、`:7903`、`:7942`、`:7952`、`:7959` | void/负 errno/0-1、导出与上下文、PI 保存状态 |
| cgroup CPU | `Documentation/admin-guide/cgroup-v2.rst` 和 CPU 控制实现 | 时间微秒、层级、策略范围、累计与区间 |
| 栈 work 完成示例 | `include/linux/workqueue.h`、`kernel/workqueue.c` | on-stack 初始化/销毁，成功和超时都 flush，避免等待依赖死锁 |

宏、inline、声明和模块导出分别核对。示例没有编译/运行；真实项目仍需接上资源、错误和停机协议。

## 7. 教学模型的范围

每个模型显式给定输入；不把各图拼成一份设备 trace。主要模型包括：

| 模型 | 应得到的结果 |
| --- | --- |
| CFS A/B=1024/2048，六个 1 ms 教学步 | A、B、B、A、B、B；实际 2/4 ms，虚拟均 2 ms |
| 两级组 G/H 同权，G 内 A/B=1:2，H 内 C | 份额 1/6、1/3、1/2；给定 period=12 ms 且无下限修正，slice=2/4/6 ms |
| 唤醒基准100、旧50、有效阈值6 | 新位置94；阈值不是设备默认 |
| 迁移源基准100、位置110，目标基准500 | 在需重表达的分支得到510 |
| PELT 段内600，新增2448计量 | 跨2段，旧剩424，中间1024，新段1000 |
| util_est 旧400，新200，下降权重1/4 | 350；满足 fastup 时新600可升为600 |
| CFS 本地-1、目标5、共享池3 ms | 需6，领3，最终本地2 ms |
| RT 已用4.1、runtime4 ms，周期扣一次 | 剩余记账0.1 ms，不总清零 |
| DL A(2,10,10)、B(1,6,6)，同起点且已准入 | B先运行至q=0，A随后；B在6、A在10恢复相应预算 |
| DL d=5、D=5、P=10 ms | 下一补充目标10 ms，而非5 |
| DL q=-4.3、Q=2、P=10、d=10 ms | 正常补充循环三次后q=1.7、d=40 |
| GRUB Q4/P8、d8/q2，在2阻塞 | 0-lag为4；指定双任务模型在4之后扣减系数0.5 |
| PI L20，X 的 H80/M70，Z 的 J60 | H取消→70，释放X→60，释放Z→20 |
| 观察0～20 ms给定区间 | 执行6、业务阻塞8、可运行等待6 ms |

其他 buddy、tick、能量和带宽守恒模型在各专题就地解释。计算正确只证明给定模型自洽，不证明目标机具有这些输入。

<a id="coverage"></a>

## 8. 覆盖边界

| 范围 | 当前内容 | 进一步下钻 |
| --- | --- | --- |
| 全局 | 架构/设计、五类、对象、单核/多核过程、约束与入口 | 具体平台和业务的端到端路径 |
| 核心 | 资格、等待/唤醒、mm 与 arm64 交接 | 完整进程销毁、缺失包装与架构扩展 |
| 策略 | CFS、FIFO/RR、DL 的模型与主要运行状态 | 全 feature 组合、严格可调度性证明 |
| 多核 | PELT、util_est、容量、放置/平衡、EAS 与调频协作 | 完整 nohz、隔离/热插拔、DL push/pull、平台能量模型 |
| 资源/同步 | CFS 配额、层级与统计，PI 树与关键链传播 | rt-mutex 全量死锁检测与具体锁依赖分析 |
| 使用/观察 | 主要 API 契约、局部示例、事件与统计解释 | 目标工具链编译、设备采集和负载验证 |

<a id="self-review"></a>

## 9. 内容自审与静态验证

自审按三个层次进行：全局是否能解释各部分协作；单篇是否先建立模型再走完整状态变化；跨篇的基线、术语、例子、图示和接口边界是否一致。尤其检查 RT 普通/raw 锁、业务通知/锁通知、候选资格/实际执行、不同时间和负载口径。

2026-11-22 本轮检查结果：

- 17 份 Markdown 分别经 Python-Markdown 与 CommonMark 解析；每种模式检查 241 项本地引用和 117 项片段目标，未发现缺文件、缺锚点或重复 id。
- 原有 109 个显式章节锚点全部保留；数字章节/小节顺序检查通过。
- 27 份 SVG 的 XML、viewBox、id 与 marker 引用，以及 8 份导航 YAML 检查通过。新增/改动图的文本做了静态尺寸余量检查，没有做字体渲染验证。
- 检查 63 项去重的显式完整源码路径/行号引用，没有意外缺失或越界；已声明缺少的 arm64 包装头文件单列为例外。
- 30 项模型检查通过，覆盖公平份额/层级、时钟/PELT、额度、RT/DL、GRUB、PI、能量增量和观察区间。它们验证的是教学输入下的数值关系。
- 旧调度稿 25 个文件及其余核心文档的已有文件哈希未变化，`git diff --check` 与新文档尾部空白检查通过；mm 与 skill 的已有修改未被本轮改动。

这些结果证明文档结构与给定模型自洽，不证明整份源码已经逐行审计，也不证明芯片上的实际行为。未做网站预览、构建、内核/示例编译运行、trace、调参或 Git 提交/推送。
