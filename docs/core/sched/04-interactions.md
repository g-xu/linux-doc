# 04 · 子模块交互矩阵(空间维度)

> 本篇是 sched 两层文档集的空间维度骨架,回答"哪个子模块依赖哪个、通过什么依赖"。它是所有子模块篇的约束条件:凡涉及 ≥2 个子模块的关系,一律落在本篇的某个格子,子模块篇只向上引用格子编号,不重复展开。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。共享字段的同步语义见 [`05-concurrency.md`](05-concurrency.md);对象字段归属见 [`02-objects.md`](02-objects.md)。

## §1 子模块清单

11 个子模块分 4 层。行数为 `wc -l` 实测(fair 按 8008/8009 行界一分为二:CFS 主体 + 负载均衡)。

| 子模块 | 主文件 | 层 | 一句话职责 | 顶层入口函数 |
|---|---|---|---|---|
| core | `kernel/sched/core.c` | 核心 | 调度主循环、唤醒、上下文切换、向各调度类分派 | `__schedule` / `try_to_wake_up` / `scheduler_tick` |
| fair | `kernel/sched/fair.c:1-8008` | 调度类 | CFS 公平类:vruntime + 红黑树 | `enqueue_task_fair` / `pick_next_task_fair` / `task_tick_fair` |
| rt | `kernel/sched/rt.c` + `cpupri.c` | 调度类 | 实时类:FIFO/RR + 位图优先级 | `enqueue_task_rt` / `pick_next_task_rt` |
| deadline | `kernel/sched/deadline.c` + `cpudeadline.c` | 调度类 | EDF 截止期类 + 带宽准入 | `enqueue_task_dl` / `pick_next_task_dl` |
| idle-stop | `kernel/sched/idle.c` + `stop_task.c` | 调度类 | 两个退化类:idle 兜底 + stop 最高优先级迁移 | `pick_next_task_idle` / `pick_next_task_stop` |
| pelt-load | `kernel/sched/pelt.c` / `pelt.h` | 负载均衡 | PELT 指数加权移动平均,估计利用率(横切叶子) | `__update_load_avg_cfs_rq` / `update_rt_rq_load_avg` |
| load-balance | `kernel/sched/fair.c:8009-11760` | 负载均衡 | SMP 跨 CPU 迁移与均衡决策 | `load_balance` / `run_rebalance_domains` |
| topology | `kernel/sched/topology.c` | 负载均衡 | 从 CPU 拓扑构建 sched_domain 层次 | `build_sched_domains` / `partition_sched_domains` |
| schedutil | `kernel/sched/cpufreq_schedutil.c` | 外围 | 基于利用率的调频策略(cpufreq governor) | `sugov_update_shared` / `sugov_update_single_freq` |
| cputime | `kernel/sched/cputime.c` + `cpuacct.c` | 外围 | CPU 时间记账(user/sys/steal、cgroup) | `cpuacct_charge` / `account_*_time` |
| psi | `kernel/sched/psi.c` | 外围 | 压力失速信息(PSI)统计 | `psi_task_change` / `psi_task_switch` |

行界依据见 [`references/methodology.md`](../../.qoder/skills/kernel-core-subsystem-docs/references/methodology.md) §A(load-balance 从 fair 拆出、idle-stop 合并的判据)。

四层的主依赖方向(实线=直接调用,虚线=共享字段/回调解耦),读懂这张图再看 §2 的 11×11 矩阵就有了骨架:

```
   ┌──────────────────────────── 核心层 ────────────────────────────┐
   │  core (core.c)  __schedule / try_to_wake_up / scheduler_tick    │
   │  只经 sched_class 回调表分派, 不认识具体类的算法(§3.1)          │
   └───┬───────────────┬───────────────┬───────────────┬────────────┘
       │回调分派        │               │置 need_resched(反向, §3 欠债)
       ▼ enqueue/pick   ▼               ▼               ▲
   ┌────────────── 调度类层(优先级 stop>dl>rt>fair>idle) ───────────┐
   │  fair(CFS)   rt(FIFO/RR)   deadline(EDF)   idle-stop            │
   │    │  ▲          │  ▲           │                               │
   │    │  │          │  │  DL↔RT 带宽挤压+准入(依赖环, §3.8/§6)    │
   │    │  │          └──┼───────────┘                               │
   └────┼──┼─────────────┼───────────────────────────────────────────┘
        │  │ 各类经专属入口喂/回读 PELT(§3.2/§3.3)
        ▼  │                       ┌─────────── 外围层 ───────────┐
   ┌─────── 负载均衡层 ───────┐    │ schedutil  cputime  psi       │
   │ pelt-load(纯计算叶子)   │◄───┤ schedutil 无锁读 PELT 快照调频 │
   │ load-balance(fair.c 后段)│    │ (通知↔回读闭环, §3.4/§3.5)    │
   │ topology(建 sched_domain)│    │ core 打点 psi(§3.7)          │
   └──────────┬───────────────┘    └───────────────────────────────┘
              │ LB 只读 rq.sd(RCU), topology 持 mutex 重建(§3.6)
              ▼
        [ rq.sd / rd 域树 ]
```

图中三个依赖环(core↔fair、deadline↔rt、schedutil↔fair)均有意为之,靠回调或共享字段解耦,无环形持锁——详见 §6。

## §2 11×11 交互矩阵

行 = 调用方,列 = 被调方。格子内容:`调用 f():行号` / `共享 对象.字段` / `回调 ops.成员`。对角线一律 `—`。空格子取证见 §2.1。

代号:CO=core FA=fair RT=rt DL=deadline IS=idle-stop PE=pelt-load LB=load-balance TO=topology SU=schedutil CT=cputime PS=psi。

| ↓A＼B→ | CO | FA | RT | DL | IS | PE | LB | TO | SU | CT | PS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **CO** | — | 调用 `pick_next_task_fair`:5986<br>回调 `sched_class.enqueue_task`:2099 | 回调分派:2099<br>`for_each_class`:6003 | 回调分派:2099/6003 | 调用 `pick_next_task_idle`:5993 | 共享 `rq.cfs.avg` 经 `effective_cpu_util`:7404 | — | 共享 `rq.sd`:1122;`partition_sched_domains`:9532 | — | — | 调用 `psi_task_switch`:6673;`psi_enqueue`:2095 |
| **FA** | 调用 `resched_curr_lazy`(11处) | — | — | — | 调用 `update_idle_core`:idle.c:436 | 调用 `update_cfs_rq_load_avg`:4017;`__update_load_avg_se`:4184 | 同文件 | 共享 `rq.sd` 经 `wake_affine`:6426;`sd_llc`:6621 | 回调 `cpufreq_update_util`:3507/6114 | 调用 `cgroup_account_cputime`:915 | — |
| **RT** | 调用 `resched_curr`(13处) | — | *(cpupri 内部)* | 调用 `sched_dl_global_validate`:3014 | — | 调用 `update_rt_rq_load_avg`:1768/2551 | — | 共享 `rq.rd.cpupri`:1490 | 回调 `cpufreq_update_util`:610/1127 | 调用 `update_current_exec_runtime`:1069 | — |
| **DL** | 调用 `resched_curr`(10处) | — | 写 `rt_rq.rt_time`:1405;`cpupri_set`:1490 | *(cpudeadline 内部)* | — | 调用 `update_dl_rq_load_avg`:2002/2698 | — | 共享 `rq.rd` | 回调 `cpufreq_update_util`:243/257 | 调用 `update_current_exec_runtime`:1341 | — |
| **IS** | — | 调用 `update_idle_core`:idle.c:436 | — | — | — | — | — | 共享 `sd`(SMT idle core) | — | 调用 `update_current_exec_runtime`:stop_task.c:84 | — |
| **PE** | — | — | — | — | — | — | — | — | — | — | — |
| **LB** | 调用 `stop_one_cpu`(active_balance) | 同文件 | — | — | — | 共享 `cfs_rq.avg.load_avg`:8646;`update_*_load_avg`:8706-8709 | — | 共享 `sd.imbalance_pct`:9018/9332/9882(20处) | 回调 `cpufreq_update_util`:8842 | — | — |
| **TO** | — | — | — | — | — | — | — | — | — | — | — |
| **SU** | 调用 `cpu_rq`/`rq_clock` | 调用 `cpu_util_cfs`:186 | 共享经 `effective_cpu_util`:7413/7439 | 调用 `cpu_bw_dl`:185/331 | — | 共享 `rq.avg_rt/avg_dl/avg_irq`(经 `cpu_util_*`) | — | 调用 `rebuild_sched_domains_energy`:874 | — | — | — |
| **CT** | 调用 `task_rq_lock`:296 | — | — | — | — | — | — | — | — | *(cpuacct 内部)* | — |
| **PS** | 共享 `rq.psi_irq_time`:1022;`task_rq_lock`:1170 | — | — | — | — | — | — | — | — | — | — |

非空格子计数(§3 与 §6 对账用):CO 7 + FA 7 + RT 6 + DL 6 + IS 3 + PE 0 + LB 5 + TO 0 + SU 6 + CT 1 + PS 1 = **42**。计数命令见 §6 脚注。

### §2.1 空格子取证记录

只登记"经取证确认无直接依赖"的关键格子(双向 grep,命令进 [`99-evidence.md`](99-evidence.md) §3)。间接依赖(经第三方转发)也在此声明,不计入 §2 非空格子。

| 格子 | 命令 | 结果 |
|---|---|---|
| RT→FA / DL→FA | `rg -nE 'enqueue_task_fair\|pick_next_task_fair\|update_curr_fair\|select_task_rq_fair' rt.c deadline.c` | 0 命中——调度类之间不互调,全经 core 分派 |
| PE→任何类 | `rg -nE 'enqueue\|dequeue\|pick_next\|sched_class\|cpufreq_update_util\|_fair\|_rt\|_dl' pelt.c` | 仅命中 `___update_load_*` 内部数学(pelt.c:348/353/374/379)与注释,无外向调用——PELT 是纯叶子,只被调不主调 |
| CT→PE | `rg -cnE 'load_avg\|->avg\|cfs_rq' cputime.c` | 0 命中 |
| TO→PS/CT/SU | `rg -cnE 'psi_\|account_user\|account_system\|sugov\|cpufreq_update_util' topology.c` | 0 命中——topology 是域构建方,单向被 core/schedutil 调 |
| SU→CT | `rg -cnE 'account_user_time\|account_system_time\|account_process_tick' cpufreq_schedutil.c` | 0 命中 |
| CO→SU(直接) | `rg -nE 'schedutil\|sugov\|sched_cpufreq' core.c` | 0 命中——core 与 schedutil 完全经 `cpufreq_update_util` per-cpu 回调解耦 |
| FA/RT/DL→PS(直接) | 见 §3.7 | 间接:各类经 core 的 `psi_enqueue`/`psi_dequeue`/`psi_task_switch` 触发,不直接调 psi |
| LB→RT / LB→DL(直接) | fair.c:8706-8709 | 间接:LB 调的是 PELT 族 `update_rt_rq_load_avg`/`update_dl_rq_load_avg`(定义在 pelt.c),归入 LB→PE 格子,非 LB→RT/DL |

## §3 逐对关系展开

42 个非空格子本轮展开 8 个最关键的(其余登记 §3 末欠债清单,耦合形式已在 §2 确定,不影响其他节正确性——见 [`references/methodology.md`](../../.qoder/skills/kernel-core-subsystem-docs/references/methodology.md) §H)。

### §3.1 core → 各调度类(分派解耦,格子 CO→FA/RT/DL/IS)

- 耦合形式:回调解耦(`sched_class` ops 表)。
- 调用点:入队 `core.c:2099` `p->sched_class->enqueue_task(rq, p, flags)`;出队 `core.c:2119` `dequeue_task`;选任务快路径 `core.c:5986` `pick_next_task_fair`,通用路径 `core.c:6002-6003` `for_each_class(class){ p = class->pick_next_task(rq); }`,idle 兜底 `core.c:5993`;均衡回调 `core.c:5959` `for_class_range`。
- 传递语义:core 不知道具体调度类的算法,只按优先级链 stop>dl>rt>fair>idle 依次询问"你有任务可跑吗"。回调表定义见 [`02-objects.md`](02-objects.md) §5。
- 解耦手段与代价:函数指针间接调用不可内联、难静态追踪(索引工具的盲区,见 [`kernel-index-blind-spots`]);换来的是新增调度类无需改 core。快路径直呼 `pick_next_task_fair` 是对"绝大多数时候只有 CFS 任务"的优化,绕过 for_each_class 遍历。

### §3.2 fair → pelt-load(格子 FA→PE)

- 耦合形式:直接调用 PELT 函数族。
- 调用点:`fair.c:4017` 定义 `update_cfs_rq_load_avg`(内部 `4073` 调 `__update_load_avg_cfs_rq`);`fair.c:4184` `__update_load_avg_se`;`fair.c:3640/3995/4226` `__update_load_avg_blocked_se`;封装 `update_load_avg()`(`fair.c:4174`)在 `4772/4866/4959/5063/5079` 等处驱动。函数族全量枚举见 `pelt.h:4-8,11,31`。
- 传递语义:每次入队/出队/tick,fair 把 CFS 实体的运行历史喂给 PELT,PELT 回写 `cfs_rq.avg`(利用率信号),供调频(§3.4)与均衡(§3.6)读取。
- 解耦手段与代价:PELT 是纯计算叶子(§2.1 已证无外向调用),被各类共享。这里没有反向依赖,代价仅是每跳一次定点数运算。

### §3.3 rt/dl → pelt-load(格子 RT→PE、DL→PE)

- 耦合形式:直接调用各自的 PELT 入口。
- 调用点:RT `rt.c:1768/1838`(出/入队)、`rt.c:2551/2652` 调 `update_rt_rq_load_avg(rq_clock_pelt(rq), rq, running)`;DL `deadline.c:2002/2054/2071/2698` 调 `update_dl_rq_load_avg`。
- 传递语义:RT/DL 也向 PELT 贡献负载信号,写入 `rq.avg_rt`/`rq.avg_dl`,让调频能看到实时/截止期任务的算力需求。
- 取证教训:这是 [`references/methodology.md`](../../.qoder/skills/kernel-core-subsystem-docs/references/methodology.md) §B.1 记录的著名误判点——曾用 `grep update_load_avg rt.c` 得 0 命中就断言"RT 不用 PELT"。实际 PELT 是同构函数族,RT/DL 各有专属入口。否定结论前必须先枚举整族。

### §3.4 fair/rt/dl → schedutil(格子 FA/RT/DL→SU)

- 耦合形式:回调解耦(`cpufreq_update_util` per-cpu 钩子)。
- 调用点:fair `fair.c:3507`、`6114`(SCHED_CPUFREQ_IOWAIT);rt `rt.c:610/1127`;dl `deadline.c:243/257`;LB 路径 `fair.c:8842`。回调注册在 `cpufreq_schedutil.c:799-808`;触发桥 `kernel/sched/sched.h:2864-2872` `cpufreq_update_util()→data->func()`。
- 传递语义:调度类在利用率变化时"敲一下"调频器,让 governor 重算目标频率。三类都调是因为频率必须覆盖所有 CPU 消耗者。
- 解耦手段与代价:core 完全不引用 schedutil(§2.1 CO→SU 为 0),经 per-cpu 函数指针解耦,使 governor 可插拔。RT 的调用是注释所述"防 RT 独占时频率停滞"的 band-aid(`kernel/sched/sched.h:2855-2864`)。

### §3.5 schedutil → fair/rt/dl/pelt-load(反向读利用率,格子 SU→FA/RT/DL/PE)

- 耦合形式:共享字段读取。
- 调用点:`cpufreq_schedutil.c:185` `sg_cpu->bw_dl = cpu_bw_dl(rq)`;`:186` `effective_cpu_util(cpu, cpu_util_cfs(cpu), ...)`。`effective_cpu_util`(`core.c:7404`)聚合 `util_cfs + cpu_util_rt`(`7439`)、`cpu_util_dl`(`7443`)、`cpu_util_irq`(`7422`),并 `7413` 检查 `rt_rq_is_runnable`。这些 `cpu_util_*` 读 `rq.avg_rt/avg_dl/avg_irq/cfs.avg`(PELT 产出)。
- 传递语义:governor 把四类利用率合成一个"有效算力需求",据此选频率。与 §3.4 构成一次完整的"通知→回读"闭环。
- 代价:读的是无锁 PELT 快照,可能读到略旧的值,但调频对精度不敏感,故不加锁(见 [`05-concurrency.md`](05-concurrency.md) §5)。

### §3.6 load-balance → topology(格子 LB→TO)

- 耦合形式:共享字段读取(`sched_domain`)。
- 调用点:`sd->imbalance_pct` 共 20 处,如 `fair.c:9018`(容量阈值)、`9081/9106`(group 分类)、`9332/9590` `group_classify`、`9882`、`10246`、`10486`;域遍历 `fair.c:8182/8224/8395`;`update_group_capacity(struct sched_domain *sd)` `fair.c:8955`。
- 传递语义:topology 构建好域层次(哪些 CPU 共享 LLC、哪些跨 NUMA),LB 读这棵树决定"往哪搬任务、搬多少"。`imbalance_pct` 是每层域的不平衡容忍阈值。
- 解耦手段与代价:LB 只读不写,拓扑重建由 topology 持 `sched_domains_mutex` 完成(见 [`05-concurrency.md`](05-concurrency.md) §2);`rq.sd` 是 RCU 指针(见 [`02-objects.md`](02-objects.md) §4),LB 遍历时用 `rcu_dereference`,避免与热插拔重建撞车。

### §3.7 core → psi(格子 CO→PS)

- 耦合形式:直接调用(psi 钩子)。
- 调用点:`core.c:2095` `psi_enqueue`、`core.c:2115` `psi_dequeue`、`core.c:6673` `psi_task_switch`、`core.c:5631,6672` `psi_account_irqtime`。
- 传递语义:core 在任务入队/出队/切换时通知 psi,psi 据此累计"有多少任务在等 CPU/IO/内存"的失速时间。各调度类不直接调 psi(§2.1),统一由 core 在分派前后打点。
- 代价:psi 内部用 per-cpu seqcount(见 [`05-concurrency.md`](05-concurrency.md) §5),打点在 rq 锁临界区内,开销可控;`CONFIG_PSI` 关闭时钩子编译为空。

### §3.8 deadline ↔ rt(带宽挤压 + 准入,格子 DL→RT、RT→DL,构成依赖环见 §6)

- 耦合形式:共享字段 + 直接调用,双向。
- DL→RT:`deadline.c:1395-1406` `update_curr_dl` 中,`1398` 持 `rt_rq->rt_runtime_lock`,`1404` `sched_rt_bandwidth_account(rt_rq)`(定义 `rt.c:720`),`1405` `rt_rq->rt_time += delta_exec`——DL 运行时间计入 RT 带宽账;`1490` `cpupri_set(&rq->rd->cpupri, ...)`。DL 总带宽借用 RT 全局参数:`deadline.c:504-507`、`2816-2836` `to_ratio(global_rt_period(), global_rt_runtime())`。
- RT→DL:`rt.c:3014` `sched_dl_global_validate()`(准入校验),`rt.c:2795` 以 `to_ratio(global_rt_period(), global_rt_runtime())` 做带宽上限判断。
- 传递语义:DL 优先级高于 RT,DL 消耗的 CPU 时间要从 RT 的带宽预算里扣(挤压),防止 DL 饿死 RT;反过来 RT 全局带宽参数是 DL 准入的上界。这是一处意外的强耦合,值得单独展开。
- 共享字段:`rt_rq->rt_time`、`rt_rq->rt_runtime`、`global_rt_runtime()`(见 §4)。

#### §3 欠债清单(本轮未展开的 34 格)

| 格子群 | §2 已定耦合形式 | 优先级 | 为何不影响其他节 |
|---|---|---|---|
| FA/RT/DL/LB → CO(`resched_curr`/`resched_curr_lazy`) | 直接调用,重调度置位 | 中 | 语义单一(标记 need_resched),RT 差异已在 §3 各处及 [`05-concurrency.md`](05-concurrency.md) §7 说明 |
| FA/RT/DL/IS → CT(`update_current_exec_runtime`/`cgroup_account_cputime`) | 直接调用,时间记账 | 中 | 统一入口 `kernel/sched/sched.h:3231`,落点 `cpuacct.c:334`,记账不改调度决策 |
| CO→PE、SU→PE、LB→PE(读 `rq.*.avg`) | 共享字段读 | 低 | 与 §3.2/§3.5 同机制(读 PELT 快照) |
| CO→TO、FA→TO、SU→TO | 共享 `rq.sd` / 触发重建 | 低 | 与 §3.6 同机制(RCU 读域树) |
| IS→FA、FA→IS(`update_idle_core`) | 直接调用 | 低 | SMT idle core 追踪,单函数 |
| CT→CO、PS→CO(`task_rq_lock`) | 直接调用取锁 | 低 | 加锁器,锁序见 [`05-concurrency.md`](05-concurrency.md) §2 |
| LB→CO(`stop_one_cpu` active_balance) | 直接调用 | 中 | 触发 stop 类做主动迁移,后续轮次展开 |

## §4 共享字段总表(05 篇输入)

只收跨子模块字段。同步手段具体到锁名;填"无同步"的行在 [`05-concurrency.md`](05-concurrency.md) 有解释。

| 字段 | 所属对象 | 写方(子模块) | 读方(子模块) | 同步手段 | 定义位置 |
|---|---|---|---|---|---|
| `curr` | `struct rq` | core(`RCU_INIT_POINTER` `core.c:6654`) | core / fair / rt / dl / cputime / psi | RCU + rq->__lock(写侧) | `kernel/sched/sched.h:1008` |
| `nr_running` | `struct rq` | fair / rt / dl / stop(经 `add/sub_nr_running`) | core / load-balance | rq->__lock | `kernel/sched/sched.h:962` |
| `clock` / `clock_task` | `struct rq` | core(`update_rq_clock` `core.c:729`) | 全部调度类 + pelt | rq->__lock | `kernel/sched/sched.h:1015/1017` |
| `cfs.avg` / `avg_rt` / `avg_dl` / `avg_irq` | `struct rq` | fair / rt / dl(经 pelt) | schedutil / load-balance | rq->__lock(写);无锁读(调频) | `kernel/sched/sched.h:990` 等 |
| `sd` | `struct rq` | topology(`rcu_assign_pointer`,持 `sched_domains_mutex`) | fair / load-balance / core | RCU | `kernel/sched/sched.h:1040` |
| `rt_rq.rt_time` / `rt_runtime` | `struct rt_rq` | rt / **deadline**(`deadline.c:1405`) | rt / deadline | `rt_rq->rt_runtime_lock`(raw) | `sched.h` rt_rq 内 |
| `rd.cpupri` | `struct root_domain` | rt / **deadline**(`cpupri_set` `deadline.c:1490`) | rt(选核) | 内部原子/锁 | root_domain 内 |
| `rd.dl_bw` | `struct root_domain` | deadline | deadline / core(准入) | `dl_bw->lock`(raw) | `kernel/sched/sched.h:319` |
| `psi_irq_time` | `struct rq` | core(irqtime 记账) | psi(`psi.c:1022`) | rq->__lock + seqcount | rq 内 |

无同步(高危,见 [`05-concurrency.md`](05-concurrency.md) §5):`cfs.avg`/`avg_rt`/`avg_dl` 的调频读路径为无锁 PELT 快照——只在持 rq 锁的路径写,调频侧容忍略旧值。

## §5 回调注册表

| ops 结构体 | 定义位置 | 实现者 | 注册点 | 调用点 | 实现数 |
|---|---|---|---|---|---|
| `struct sched_class` | `kernel/sched/sched.h:2148` | stop/dl/rt/fair/idle | `DEFINE_SCHED_CLASS` 链接段(`kernel/sched/sched.h:2232`) | `core.c:2099/2119/6003` 等 | 5 |
| `cpufreq_update_util` 钩子(`update_util_data.func`) | `kernel/sched/sched.h:2864` | schedutil / (intel_pstate 等 governor) | `cpufreq_add_update_util_hook`(`cpufreq_schedutil.c:799-808`) | fair/rt/dl 见 §3.4 | per-cpu 1 |

实现数命令:`rg -n 'DEFINE_SCHED_CLASS\(' kernel/sched/` → 5(stop_task.c:117 / deadline.c:2738 / rt.c:2692 / fair.c:12443 / idle.c:499)。sched_class 优先级链在链接段中逆序排列(`kernel/sched/sched.h:2228` 注释),`for_each_class`(`kernel/sched/sched.h:2244`)按 stop→dl→rt→fair→idle 遍历。

## §6 依赖环检测

在 §2 矩阵上找互相非空的对(A→B 且 B→A):

| 环 | 参与方 | 设计缺陷还是有意 | 源码依据 |
|---|---|---|---|
| core ↔ fair | CO→FA(分派 `core.c:2099/5986`)、FA→CO(`resched_curr` 11 处) | 有意 | 分派用回调打破直接类型依赖;fair 回置 need_resched 是调度协议的一部分,非循环调用 |
| deadline ↔ rt | DL→RT(带宽挤压 `deadline.c:1405`)、RT→DL(准入 `rt.c:3014`) | 有意 | DL 优先级高于 RT,消耗时间从 RT 带宽扣;RT 全局参数是 DL 准入上界。见 §3.8。无环形死锁(不同锁、不同路径) |
| schedutil ↔ fair | SU→FA(读 `cpu_util_cfs`)、FA→SU(`cpufreq_update_util`) | 有意 | 通知/回读闭环,经 per-cpu 回调解耦,无直接类型依赖(见 §3.4/§3.5) |

三处环均有意为之,靠回调或共享字段解耦,无环形持锁(锁序见 [`05-concurrency.md`](05-concurrency.md) §2)。未发现无解释的依赖环。

