# 02 · 跨子模块共享对象(对象维度)

> 本篇是子模块篇的约束条件:凡在这里登记的字段,子模块篇不得自行解释其同步语义,一律引用 [`05-concurrency.md`](05-concurrency.md)。字段间的关系落点见 [`04-interactions.md`](04-interactions.md) §4,时间维度见 [`03-lifecycle.md`](03-lifecycle.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §0 收录门槛

只收被 ≥2 个子模块读写的对象。判据可复现:

```bash
cd kernel/sched && grep -l 'struct rq\b' *.c *.h | wc -l
```

单子模块私有对象(如 fair 的 `sched_entity` 细节字段、rt 的 `cpupri`、dl 的 `cpudl`)不在此列,归各子模块篇。未收录项及排除理由见 §5。

## §1 对象台账

| 对象 | 定义(起始行) | 引用文件数 | 主要引用文件 | 本篇处理深度 |
|---|---|---|---|---|
| `struct rq` | `kernel/sched/sched.h:954` | 11 | core.c / fair.c / rt.c / deadline.c / idle.c / stop_task.c / pelt.c / cputime.c / psi.c / topology.c / cpufreq_schedutil.c | 完整字段级(§2) |
| `struct sched_class` | `kernel/sched/sched.h:2148` | 6 | core.c + 5 个 DEFINE_SCHED_CLASS 实现文件 | 回调表(§3.4) |
| `struct root_domain` | `kernel/sched/sched.h:845` | 5 | core.c / rt.c / deadline.c / topology.c / cpudeadline.c | 字段摘要(§2.5) |
| `struct sched_domain` | `include/linux/sched/topology.h:32` | 4 | topology.c / fair.c(LB)/ core.c | 见 [`04-interactions.md`](04-interactions.md) §3.6 |
| `struct task_struct`(调度视图) | `include/linux/sched.h:737` | 全部 | 全部 | 生命周期见 [`03-lifecycle.md`](03-lifecycle.md);此处只列 per-task 载体字段(见本篇 2.6 节) |

引用文件数命令(rq 为例):`grep -l 'struct rq\b' kernel/sched/*.c kernel/sched/*.h | wc -l` → 11。定义行是结构体 `struct rq {` 起始行 954,非某字段行。

## §2 主对象 `struct rq` 字段级归属台账

本篇核心。`rq` 是 per-CPU 主结构(每 CPU 一个),被全部调度类和 core 共读写。按功能分组,逐字段回答"谁写、谁读、怎么同步"。写入方栏覆盖四种形态:直接 `=`、RCU 宏、`WRITE_ONCE`、访问器函数(漏一种就会得出"无人写入"的错误结论)。

先看 `rq` 的内存布局与字段归属分区——哪些字段属于 core、哪些内嵌子队列归各调度类、哪些是 SMP/负载信号:

```
   struct rq  (kernel/sched/sched.h:954, per-CPU, 内嵌值非指针)
   ┌──────────────────────────────────────────────────────────────────────┐
   │ §2.1 运行状态计数   nr_running:962   nr_switches:981   nr_uninterr:1006 │  归 core
   │ §2.2 双时钟         clock:1015 ── update_rq_clock ──► clock_task:1017   │  归 core
   │                                       (扣 irq/steal 后, PELT/vruntime 用)│
   │ §2.4 任务指针       curr:1008(__rcu*)  idle:1009    stop:1010          │  归 core
   ├────────────────────────────────────────────────────────────────────────┤
   │ §2.3 内嵌各类子队列(rq 直接持有, 内部字段归各子模块篇):                 │
   │   ┌────────────────┐  ┌────────────────┐  ┌────────────────┐           │
   │   │ cfs  :990      │  │ rt   :991      │  │ dl   :992      │           │
   │   │ struct cfs_rq  │  │ struct rt_rq   │  │ struct dl_rq   │           │
   │   │ ▲同名陷阱:     │  │ rt_nr_running  │  │ dl_nr_running  │           │
   │   │  curr/nr_running│  │ (独立名)       │  │ (独立名)       │           │
   │   │  /idle 见 §3.1 │  │                │  │                │           │
   │   │ 归 fair 子模块 │  │ 归 rt 子模块   │  │ 归 deadline    │           │
   │   └────────────────┘  └────────────────┘  └────────────────┘           │
   ├────────────────────────────────────────────────────────────────────────┤
   │ §2.5 拓扑/容量(SMP)  sd:1040(RCU)  rd:1041(RCU)  cpu_capacity:1042     │  归 topology/LB
   │ §2.6 PELT 负载信号   cfs.avg  avg_rt:1063  avg_dl:1064   (无锁读→调频)  │  各类写/schedutil读
   └──────────────────────────────────────────────────────────────────────┘
     全字段(除标注 RCU/无锁者)由 rq->__lock (:956, raw_spinlock_t) 保护
```

再看核心字段的"谁写→谁读"数据流,箭头即跨子模块耦合(落点见 [`04-interactions.md`](04-interactions.md) §4):

```
   写方(持 rq->__lock)                       读方
   ─────────────────                          ──────
   __schedule ──── curr(RCU 发布) ──────┬───► core / fair / rt / dl(持锁直读)
                                        └───► membarrier / cputime / 选核(rcu_dereference, 远程无锁)
   三调度类 ─ add/sub_nr_running ─ nr_running ─► core(__schedule) / load-balance
   update_rq_clock ── clock/clock_task ───────► 全部调度类 + PELT + cputime
   fair/rt/dl(经 PELT) ─ cfs.avg/avg_rt/avg_dl ─► schedutil 调频(无锁快照读, §4)
   topology(持 sched_domains_mutex) ─ sd(RCU) ─► fair / load-balance / core(遍历域)
```

### §2.1 运行状态与计数

| 字段 | 定义行 | 写入方(带行号) | 主要读方 | 同步 |
|---|---|---|---|---|
| `nr_running` | 962 | 访问器 `add_nr_running`@kernel/sched/sched.h:2411 / `sub_nr_running`@2428;调用点 stop_task.c:57/63、rt.c:1122/1103、deadline.c:1515/1529、fair.c:5470/6154/5385/6245;init 归零 core.c:9906 | core(`__schedule`)、load-balance | `rq->__lock` |
| `nr_switches` | 981 | `__schedule` 内 `++`(仅此一处) | `/proc`、调试 | `rq->__lock` |
| `nr_uninterruptible` | 1006 | core(入/出队时 `+=/-=`) | loadavg 计算 | `rq->__lock` |

取证教训:`nr_running` 若只搜 `rq->nr_running =` 会得 0 命中(除 init),因为三个调度类全经 `add_nr_running`/`sub_nr_running` 访问器写入。idle 类不增减(idle 任务不计入 `nr_running`)。这是模板第 12.3 条"访问器形态"的典型。

### §2.2 时钟

| 字段 | 定义行 | 写入方(带行号) | 主要读方 | 同步 |
|---|---|---|---|---|
| `clock` | 1015 | core `update_rq_clock`:`+= delta`@core.c:747(`lockdep_assert_rq_held`@733) | 全部调度类 | `rq->__lock` |
| `clock_task` | 1017 | core `update_rq_clock_task`@core.c:720(被 `update_rq_clock`:748 调) | PELT、vruntime、cputime | `rq->__lock` |

`clock` 是 rq 墙钟,`clock_task` 是扣除 irq/steal 后的任务时钟——PELT 和 vruntime 用后者作基准,避免把中断时间算进任务负载。两者不可混用,理由见 §3.3。注意 `clock.c:161/277` 写的是 `scd->clock`(sched_clock 载体),与 `rq->clock` 不同物。

### §2.3 内嵌各类子队列

| 字段 | 定义行 | 类型 | 归属子模块 | 同名陷阱 |
|---|---|---|---|---|
| `cfs` | 990 | `struct cfs_rq` | fair | `cfs.curr`/`cfs.nr_running` 与 rq 同名,见 §3.1 |
| `rt` | 991 | `struct rt_rq` | rt | `rt.rt_nr_running` 独立名,无陷阱 |
| `dl` | 992 | `struct dl_rq` | deadline | `dl.dl_nr_running` 独立名 |

这三个是内嵌值(非指针),rq 直接持有。子队列的内部字段归各子模块篇([`sub/fair/02-internals.md`](sub/fair/02-internals.md) 等),本篇只登记它们与 rq 同名字段的判别(见本篇 3.1 节)。

### §2.4 当前/特殊任务指针

| 字段 | 定义行 | 类型 | 写入方(带行号) | 主要读方 | 同步 |
|---|---|---|---|---|---|
| `curr` | 1008 | `task_struct __rcu *` | `RCU_INIT_POINTER`@core.c:6654(`__schedule` 切 next)、`rcu_assign_pointer`@core.c:9210(`init_idle`) | core/fair/rt/dl/cputime/psi/membarrier | RCU + `rq->__lock`(写侧),见 §3.2 |
| `idle` | 1009 | `task_struct *` | core.c:9209(`init_idle`) | idle 类、`__schedule` 兜底 | 启动期设定,后不变 |
| `stop` | 1010 | `task_struct *` | core(hotplug 迁移线程注册) | stop 类 | hotplug 路径 |

`curr` 是全篇唯一需要 RCU 的 per-task 指针(§3.2)。`curr` 直接裸 `=` 搜索为 0 命中——全经 RCU 宏发布,这是模板"RCU 宏形态"的典型。

### §2.5 拓扑与容量(SMP)

| 字段 | 定义行 | 写入方 | 主要读方 | 同步 |
|---|---|---|---|---|
| `sd` | 1040 | topology `rcu_assign_pointer`(持 `sched_domains_mutex`) | fair/load-balance/core(遍历域) | RCU,见 §3.2 |
| `rd`(root_domain) | 1041 | topology 附着 rd | rt/dl(cpupri/cpudl/dl_bw) | RCU + rd 内部锁 |
| `cpu_capacity` | 1042 | load-balance `update_cpu_capacity` | fair 选核、LB | `rq->__lock` |
| `cpu_capacity_orig` | 1043 | topology 初始化 | fair、schedutil | 启动期设定 |

6.1 已删除 `cpu_load[]` 历史负载数组;负载信号改由 PELT 落在 `avg_rt`/`avg_dl`/`avg_irq`(§2.6 下方)与 `misfit_task_load`@1050。

### §2.6 负载信号与 per-task 载体

| 字段 | 定义行 | 写入方 | 主要读方 | 同步 |
|---|---|---|---|---|
| `cfs.avg`(内嵌) | 990 内 | fair(经 PELT `__update_load_avg_cfs_rq`) | schedutil、load-balance | `rq->__lock`(写);无锁读(调频),见 §4 |
| `avg_rt` | 1063 | rt(经 `update_rt_rq_load_avg`) | schedutil(`cpu_util_rt`) | `rq->__lock`(写);无锁读 |
| `avg_dl` | 1064 | deadline(经 `update_dl_rq_load_avg`) | schedutil(`cpu_util_dl`) | `rq->__lock`(写);无锁读 |
| `psi_irq_time` | rq 内 | core(irqtime 记账) | psi(`psi.c:1022`) | `rq->__lock` + seqcount |

`avg_*` 由三个调度类各自的 PELT 入口写(RT/DL 也用 PELT,见 [`04-interactions.md`](04-interactions.md) §3.3 的著名误判点),schedutil 侧无锁读取快照做调频。无锁读的高危项登记见 §4,安全性论证见 [`05-concurrency.md`](05-concurrency.md) §5。

## §3 高危陷阱小节

### §3.1 同名字段(本篇最大的坑)

per-CPU 主结构 `rq` 与其内嵌子结构存在"名字相同、含义不同、归属不同子模块"的字段:

| 名字 | 载体 A(带行号) | 载体 B(带行号) | 判别 |
|---|---|---|---|
| `curr` | `rq->curr`@1008 `task_struct __rcu *`(当前任务) | `cfs_rq->curr`@574 裸 `sched_entity *`(当前实体) | 类型 + 读法:A 需 `rcu_dereference`,B 直读;裸 `->curr =` 只命中 B(fair.c:4963/5065) |
| `nr_running` | `rq->nr_running`@962(CPU 级总数) | `cfs_rq->nr_running`@552(CFS 级) | 只有 cfs 与 rq 真同名;rt/dl 各有独立名 `rt_nr_running`@665 / `dl_nr_running`@708 |
| `idle` | `rq->idle`@1009 `task_struct *`(idle 任务) | `cfs_rq->idle`@632 `int`(task_group idle 标志) | 类型即判别:A 写 core.c:9209,B 写 fair.c:12365 |

取证教训:`grep 'cfs_rq->curr ='` 用 shell 裸 `=` 会被 `rq->curr =` 的子串反向污染,还会把 `==` 计入。归属类取证一律用带词边界的正则或 Python `re`。本篇三组同名字段的归属均以"类型 + 定义行 + 写入点行号"三重确认,不靠肉眼扫名字。

### §3.2 RCU 字段(rq 内共 2 个)

| 字段 | 写(带行号,含宏名) | 读约定 |
|---|---|---|
| `curr` | `RCU_INIT_POINTER`@core.c:6654、`rcu_assign_pointer`@core.c:9210 | 远程读者 `rcu_dereference`:membarrier.c:474、cputime.c:999/1086、fair.c:1921/3933、core.c:3926;持锁读者可直读 |
| `sd` | `rcu_assign_pointer`(topology.c,持 `sched_domains_mutex`) | `rcu_dereference_check`(负载均衡遍历域) |

为什么 `curr` 要 RCU:写侧在 `__schedule` 持本 CPU 的 `rq->__lock` 发布新 `curr`;但有不持目标 rq 锁的远程读路径——membarrier 向别的 CPU 发 IPI 前要读其 `curr`、cputime 远程记账、选核时看目标 CPU 在跑谁。这些读者不能去抢目标 rq 锁(会造成跨 CPU 锁竞争),故用 RCU 读到一个合法快照。

裸读约 3 处(`READ_ONCE(rq->curr)`@rt.c:1616、deadline.c:1841、fair.c:2710,注释 "unlocked access")不是缺陷:唤醒抢占检查只需一致读一个指针值,不需要 RCU 宽限期保护,`READ_ONCE` 足够。持锁读者由 `rq->__lock` 提供顺序,不需要额外同步——代码不一致是错觉。详见 [`05-concurrency.md`](05-concurrency.md) §5。

### §3.3 故意不同步的同类字段:两套时钟

`rq->clock`(墙钟)与 `rq->clock_task`(任务时钟)是两套独立维护的时间基准,故意不合并:

- `clock` 含全部经过时间;`clock_task` = `clock` − irq 时间 − steal 时间(虚拟化被抢走的时间)。
- 为什么不合并:PELT 和 vruntime 要度量"任务真正占用了多少 CPU",若用含中断时间的 `clock`,一个频繁被中断打断的任务会被误判为消耗了大量 CPU,vruntime 虚高、负载估计虚高。
- 混用后果:用错基准会让调频高估需求、让 CFS 公平性失真。两者由同一次 `update_rq_clock`(core.c:729)在持锁时成对更新(`clock` 748 行调 `update_rq_clock_task` 算出 `clock_task`),保证一致。

## §4 无同步字段清单(高危项登记)

本篇只登记,安全性论证在 [`05-concurrency.md`](05-concurrency.md):

| 字段 | 无同步的原因 | 05 篇对应节 |
|---|---|---|
| `cfs.avg`(load/util) 调频读路径 | schedutil 读 PELT 快照,容忍略旧值,调频对精度不敏感 | §5(无锁 PELT 快照行) |
| `avg_rt` / `avg_dl` 调频读 | 同上,`cpu_util_rt/dl` 无锁读 | §5 |
| `rq->curr` 无锁快照 `READ_ONCE` | 唤醒抢占检查只需一致读一个指针 | §5(RCU 那行 + READ_ONCE) |

这些字段的写侧都在持 `rq->__lock` 的路径内;"无同步"仅指读侧。

## §5 本篇未覆盖项(已登记)

故意不收的对象及归属:

| 对象/字段 | 为何不收 | 去处 |
|---|---|---|
| `struct sched_entity` 内部字段(vruntime、load、rb 节点) | fair 私有,单子模块 | [`sub/fair/02-internals.md`](sub/fair/02-internals.md) |
| `struct cfs_rq` 内部(min_vruntime、tasks_timeline) | fair 私有 | [`sub/fair/02-internals.md`](sub/fair/02-internals.md) |
| `struct rt_rq` 内部(active 位图、rt_time) | rt 私有(跨 dl 的 `rt_time` 已在 04 §4 登记) | rt 子模块篇(未在本轮范围) |
| `struct dl_rq` 内部(root 红黑树、running_bw) | deadline 私有 | deadline 子模块篇 |
| `cpupri` / `cpudl` | rt/deadline 私有辅助结构 | 各自子模块篇 |
| rq 的调试/统计字段(`rq_sched_info`、`nr_load_updates` 等) | 只 core/调试读写,不满足 ≥2 子模块门槛 | 排除 |

主对象 `rq` 中未列入台账的字段(如 `hrtick_timer`、`balance_callback`、`nohz_*`)均只 core 或单一路径读写,不满足收录门槛,故排除。收录范围以"≥2 子模块读写"为唯一标准。

