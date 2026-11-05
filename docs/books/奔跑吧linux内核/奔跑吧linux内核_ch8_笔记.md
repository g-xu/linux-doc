# 📖 《奔跑吧Linux内核》卷1 第8章 进程管理之进程调度 — 读书笔记

> 📅 2026-09-09 | 🏷️ CFS / vruntime / PELT / 负载均衡 / EAS / 实时调度 | 📊 难度：极高（全书最重要、最难的一章）

---

## 🎯 一句话概括

这一章回答了"多个进程如何公平又高效地共享有限的 CPU"这条主线：**CFS 用虚拟运行时间 vruntime 把"公平"量化成一棵红黑树（谁的 vruntime 最小谁先跑），PELT 用几何衰减把每个进程/队列的负载和算力量化成可预测的数字，SMP 负载均衡按调度域层次把这些负载在多核间搬平，EAS 再在"搬平"之上叠一层"选最省电的核"**——而这一切最终都落到 `schedule() → pick_next_task() → context_switch()` 这一次进程切换上。nice 值、权重、调度类、调度域、性能域,本质都是围绕"vruntime 谁最小"和"负载往哪搬"这两个问题的量化工具。

---

## 📚 内容梳理

> 本章内容极多且环环相扣,按"框架(调度类)→ 公平性(CFS/vruntime)→ 量化(PELT)→ 多核(SMP 负载均衡)→ 节能与实时(EAS/RT)→ 落地(进程切换)"六大板块组织。

### 板块一:调度框架与调度类

#### 1.1 优先级与权重

Linux 用一套 **0~139 的内核优先级**统一描述所有进程,数字越小优先级越高:

| 优先级范围 | 对应策略 | 用户接口 |
|---|---|---|
| 0~99 | 实时进程(SCHED_FIFO/RR) | `sched_setscheduler` 的 rt_priority(1~99) |
| 100~139 | 普通进程(SCHED_NORMAL/CFS) | nice 值 -20~19,映射到 100~139 |

- **nice 值**:-20(最高)~ 19(最低),共 40 级。`nice 0` 对应内核优先级 120。
- **权重(weight)**:CFS 不直接用 nice,而是把 nice 换算成权重。`NICE_0_LOAD = 1024`(nice 0 的权重)。**每差一个 nice 级,CPU 时间约变化 10%**,所以相邻权重比约为 1.25(1.25^10≈10.7)。
- **查表而非计算**:内核用两张预建表(`kernel/sched/core.c`)完成换算,避免运行时做除法:

```c
// kernel/sched/core.c
const int sched_prio_to_weight[40] = {   // nice[-20..19] → weight
    /* -20 */ 88761, 71755, 56483, ...
    /*   0 */  1024, ...
    /*  19 */    15,
};
const u32 sched_prio_to_wmult[40] = {    // inv_weight = 2^32 / weight,预算好
    /* -20 */ 48388, ...
    /*   0 */ 4194304, ...   // 2^32/1024
};
```

`inv_weight = 2^32 / weight` 把"除以 weight"变成"乘 inv_weight 再右移 32 位",这是 CFS 里所有权重换算的基础技巧(乘法+移位远快于除法)。

#### 1.2 五个调度类(优先级从高到低)

内核把调度策略抽象成**调度类(sched_class)**,用一条链表串起来,`pick_next_task` 从高到低遍历,谁有可运行进程就用谁:

```
stop_sched_class      // 最高,停机类,用于 CPU 热插拔/迁移线程,不可被抢占
  → dl_sched_class    // deadline 实时调度(EDF,Linux 3.14 引入)
  → rt_sched_class    // 传统实时(SCHED_FIFO/RR)
  → fair_sched_class  // ★ CFS,普通进程,本章主角
  → idle_sched_class  // 最低,空闲类,无事可做时跑 idle
```

**调度类接口(struct sched_class)关键方法:**

```c
// kernel/sched/sched.h
struct sched_class {
    void (*enqueue_task)(struct rq *rq, struct task_struct *p, int flags); // 入队
    void (*dequeue_task)(struct rq *rq, struct task_struct *p, int flags); // 出队
    void (*check_preempt_curr)(struct rq *rq, struct task_struct *p, int); // 唤醒时判抢占
    struct task_struct *(*pick_next_task)(struct rq *rq, ...);             // 选下一个
    void (*put_prev_task)(struct rq *rq, struct task_struct *p);
    void (*task_tick)(struct rq *rq, struct task_struct *p, int queued);   // 时钟节拍
#ifdef CONFIG_SMP
    int  (*select_task_rq)(struct task_struct *p, int cpu, int sd_flag, int flags); // 选 CPU
    void (*migrate_task_rq)(struct task_struct *p);
#endif
};
```

#### 1.3 就绪队列 rq —— 每个 CPU 一个

```c
// kernel/sched/sched.h
struct rq {
    unsigned int nr_running;      // 本 CPU 可运行进程总数
    struct cfs_rq cfs;            // ★ CFS 就绪队列(内嵌)
    struct rt_rq  rt;             // 实时就绪队列
    struct dl_rq  dl;             // deadline 就绪队列
    struct task_struct *curr;     // 当前运行进程
    struct task_struct *idle;     // idle 进程
    struct task_struct *stop;     // stop 进程
    u64 clock;                    // 就绪队列时钟(每次 tick/调度更新)
    u64 clock_task;               // 扣除中断处理时间的时钟(给 PELT/vruntime 用)
    unsigned long cpu_capacity;      // CFS 可用算力(额定算力扣掉 RT/DL/irq 后)
    unsigned long cpu_capacity_orig; // 处理器额定算力(大核 1024,小核 <1024)
    struct root_domain *rd;       // 根调度域
    struct sched_domain *sd;      // 本 CPU 最底层调度域
};
```

> `clock` 与 `clock_task` 的区别很关键:`clock_task` 扣掉了硬中断/软中断偷走的时间,CFS 的 vruntime 和 PELT 负载都基于 `clock_task`,这样进程不会为"被中断打断"的时间买单。

> **[6.1 变化]** 调度类的组织方式变了:5.0 用 `next` 指针手工串成链表(`sched_class_highest` 起头);6.1 起改用**链接器段**排列(`kernel/sched/sched.h` 里 `SCHED_DATA` 段 + `__begin_sched_classes`/`__end_sched_classes`),遍历时直接用指针加减在段内移动,`for_class_range` 宏据此实现,省掉了每个类里的 `next` 字段。此外 6.1 的 `select_task_rq` 方法签名去掉了 `sd_flag` 之外的部分参数,`check_preempt_curr` 更名思路一致但内部逻辑增强。

---

### 板块二:完全公平调度器 CFS

#### 2.1 核心思想:虚拟运行时间 vruntime

CFS(Completely Fair Scheduler)的公平不是"每个进程跑一样久",而是"**每个进程按权重比例分到 CPU**"。它引入一个虚拟时钟 `vruntime`:

$$\text{vruntime} += \text{delta\_exec} \times \frac{\text{NICE\_0\_LOAD}}{\text{weight}}$$

- `delta_exec` 是进程本次实际运行的物理时间(纳秒)。
- 权重越大(优先级越高),vruntime 走得越慢 → 同样的物理时间,高优先级进程 vruntime 涨得少 → 更容易保持"最小"→ 更容易被再次选中。
- **nice 0 的进程 vruntime = 物理时间**(因为 weight=NICE_0_LOAD,系数为 1)。

**CFS 的选择规则极简单:永远选 vruntime 最小的进程运行。** 用一棵红黑树按 vruntime 排序,最左节点就是下一个要跑的。

#### 2.2 关键数据结构

```c
// include/linux/sched.h  —— 调度实体(进程或组的调度单位)
struct sched_entity {
    struct load_weight  load;       // 权重(weight + inv_weight)
    struct rb_node      run_node;   // ★ 挂到 cfs_rq 红黑树的节点
    unsigned int        on_rq;      // 是否在就绪队列上
    u64  exec_start;                // 本次开始运行的时刻
    u64  sum_exec_runtime;          // 累计运行的物理时间
    u64  vruntime;                  // ★ 虚拟运行时间(红黑树的 key)
    u64  prev_sum_exec_runtime;
    struct sched_avg    avg;        // ★ PELT 负载(见板块三)
};

// kernel/sched/sched.h  —— CFS 就绪队列
struct cfs_rq {
    struct load_weight  load;       // 队列总权重(所有 se 权重之和)
    unsigned int        nr_running; // 可运行调度实体数
    u64  min_vruntime;              // ★ 队列的 vruntime 基准(单调递增)
    struct rb_root_cached tasks_timeline; // ★ 红黑树(缓存了最左节点)
    struct sched_entity *curr, *next, *last, *skip; // 当前/指定下一个/上一个/跳过
    struct sched_avg    avg;        // 队列级 PELT 负载
};
```

- **min_vruntime**:队列里所有进程 vruntime 的"地板",单调递增。新进程、被唤醒进程的 vruntime 都以它为基准,防止一个睡了很久(vruntime 极小)的进程醒来后独占 CPU。
- **rb_root_cached**:红黑树额外缓存最左节点指针,取"vruntime 最小者"是 O(1)。

#### 2.3 vruntime 的计算 —— calc_delta_fair / __calc_delta

```c
// kernel/sched/fair.c
static u64 calc_delta_fair(u64 delta, struct sched_entity *se)
{
    if (unlikely(se->load.weight != NICE_0_LOAD))
        delta = __calc_delta(delta, NICE_0_LOAD, &se->load);
    return delta;
}
```

`__calc_delta()` 用 `inv_weight` 把除法变乘法+移位:`delta_exec * NICE_0_LOAD * inv_weight >> 32`。nice 0 进程直接跳过计算(delta 不变)。

#### 2.4 调度周期与最小粒度 —— sched_slice / __sched_period

CFS 要保证"一个调度周期内每个进程都跑一次",这个周期叫 **sched_period**:

```c
// kernel/sched/fair.c 关键 sysctl(6.1 实测值,与书中 5.0 一致)
sysctl_sched_latency          = 6000000ns  = 6ms    // 目标调度周期
sysctl_sched_min_granularity  = 750000ns   = 0.75ms // 单进程最小运行粒度
sched_nr_latency = 6ms / 0.75ms = 8                 // 周期内最多容纳的进程数
```

- 进程数 ≤ 8:调度周期固定 6ms,每个进程按权重比例分 `sched_slice = period × se.weight / cfs_rq.weight`。
- 进程数 > 8:周期 = `nr_running × 0.75ms`(每个至少跑 0.75ms,否则切换开销占比过高)。

**min_granularity 的意义:防止进程太多时切换过于频繁**——宁可拉长调度周期,也要保证每次跑够 0.75ms。

#### 2.5 新进程与唤醒进程的 vruntime —— place_entity

```c
// kernel/sched/fair.c  place_entity(cfs_rq, se, initial)
```

- **新 fork 的进程(initial=1)**:vruntime = min_vruntime + 一个"惩罚"(`sched_vslice`,约一个 slice)。防止不停 fork 新进程来抢占 CPU(新进程 vruntime=0 会一直最小)。
- **被唤醒的进程(initial=0)**:vruntime = max(se.vruntime, min_vruntime - `sysctl_sched_latency/2`)。给睡眠者一点补偿(最多 3ms),让它醒来能较快被调度(交互性),但又不能补偿过头独占 CPU。

#### 2.6 核心流程:update_curr 与进程调度

**update_curr() —— vruntime 的唯一更新点:**

```
update_curr(cfs_rq):
  ① delta_exec = now - curr->exec_start   // 本次运行的物理时间
  ② curr->sum_exec_runtime += delta_exec
  ③ curr->vruntime += calc_delta_fair(delta_exec, curr)  // ★ 累加 vruntime
  ④ update_min_vruntime(cfs_rq)            // 更新队列基准
  ⑤ 更新 PELT 负载(见板块三)
```

在时钟节拍、进程入队/出队、进程切换等所有关键时刻都会调用它。

**主调度器 schedule() 的核心:**

```
schedule()
  → __schedule(preempt)
    → pick_next_task(rq, prev)      // ★ 选下一个进程
        └─ 从高到低遍历调度类,fair 类走 pick_next_task_fair()
             → 取红黑树最左节点(vruntime 最小)
    → context_switch(rq, prev, next) // ★ 切换(见板块六)
```

#### 2.7 调度节拍与抢占

时钟中断 → `scheduler_tick()` → 调度类的 `task_tick` 方法 → CFS 的 `task_tick_fair()` → `entity_tick()`:

```
entity_tick():
  → update_curr()          // 更新当前进程 vruntime
  → check_preempt_tick()   // 判断是否该抢占当前进程
       若 curr 本次已运行时间 > ideal_runtime(它的 slice) → 置 TIF_NEED_RESCHED
       或 curr.vruntime - 最左节点.vruntime > ideal_runtime → 抢占
```

置位 `TIF_NEED_RESCHED` 后,在中断返回/抢占点触发真正的 `schedule()`。

#### 2.8 组调度(task_group)

`CONFIG_FAIR_GROUP_SCHED` 让"公平"可以按**组**而非按进程分配:比如两个用户 A、B,不管 A 起了多少进程,A 和 B 各分 50% CPU。

```c
struct task_group {
    struct sched_entity **se;    // 每个 CPU 上代表本组的调度实体
    struct cfs_rq **cfs_rq;      // 每个 CPU 上本组自己的子 cfs_rq
    unsigned long shares;        // 组权重(可通过 cpu.shares cgroup 接口调)
};
```

组调度形成**层次结构**:顶层 cfs_rq 里挂的可能不是进程,而是代表某个组的调度实体,该实体下面又挂着组内进程的子 cfs_rq。vruntime 的公平在每一层分别成立。这是 cgroup CPU 子系统(容器/Docker CPU 限额)的内核基础。

> **[6.1 变化]** CFS 框架在 6.1 依然是默认调度器(EEVDF 要到 6.6 才替换 CFS,6.1 尚未引入 `vlag`/`sysctl_sched_base_slice` 等 EEVDF 字段,已实测确认)。`sysctl_sched_latency=6ms`、`min_granularity=0.75ms` 与书中 5.0 完全一致。主要差异在负载字段:`sched_entity.avg`/`cfs_rq.avg` 里的 PELT 成员从 5.0 的 `runnable_load_avg` 演进为 `runnable_avg`(见板块三),`cfs_rq` 不再有 `runnable_load_avg` 字段。

---

### 板块三:PELT —— 量化负载与算力

#### 3.1 为什么需要 PELT

vruntime 解决"公平",但**负载均衡和调频**需要另一个问题的答案:"这个进程/这个 CPU 到底有多忙?"PELT(Per-Entity Load Tracking,Linux 3.8 引入)给出可预测的量化答案:把历史负载按**几何级数衰减**求和,越久远的贡献权重越低。

#### 3.2 几何衰减的核心

- 时间被切成 **1024μs(≈1ms)一个周期**。
- 每往前一个周期,历史贡献乘以衰减因子 `y`,其中 **y^32 ≈ 0.5**(即约 32ms 前的负载衰减一半)。
- 内核不做浮点运算,用预建表 `runnable_avg_yN_inv[]`(每个 y^n 乘了 2^32,查表后右移 32 位):

```c
// kernel/sched/pelt.c
static u64 decay_load(u64 val, u64 n)   // 计算 val * y^n
{
    if (unlikely(n > LOAD_AVG_PERIOD * 63))  // LOAD_AVG_PERIOD=32,超 63*32 直接归 0
        return 0;
    // 用 runnable_avg_yN_inv[] 查表 + 移位完成 val*y^n
}
#define LOAD_AVG_MAX 47742   // 无穷级数 sum(y^n) 收敛的最大值
```

#### 3.3 三段式累加 —— accumulate_sum

一次负载更新跨越的时间被拆成三段(`__accumulate_pelt_segments`):

```
   d1        d2 (若干个完整周期)        d3
[上个周期剩余][=== 完整周期 ===][当前周期已过部分]
   c1              c2                    c3
```

- **c1**:上个周期未满部分的衰减贡献。
- **c2**:中间若干完整周期,用等比数列求和公式一次算出(避免逐周期循环)。
- **c3**:当前新周期已经过去的部分。

`period_contrib` 记录"当前周期已经积累了多少",跨周期时用它算 d1。

#### 3.4 三个负载信号:load / runnable / util

`sched_avg` 里同时维护三个"平均值",分子是衰减和,分母是 `LOAD_AVG_MAX - 1024 + period_contrib`:

```c
// include/linux/sched.h(6.1 实测)
struct sched_avg {
    u64  last_update_time;   // 上次更新时刻
    u64  load_sum;           // 权重负载的衰减和
    u64  runnable_sum;       // ★ 6.1:可运行状态的衰减和
    u32  util_sum;           // 算力使用的衰减和
    u32  period_contrib;     // 当前周期已积累部分
    unsigned long load_avg;      // 负载均衡用(考虑权重)
    unsigned long runnable_avg;  // ★ 6.1:队列拥塞程度(不考虑权重)
    unsigned long util_avg;      // ★ 实际算力(0~1024,CPU 有多忙)
    struct util_est util_est;    // ★ 6.1:算力估计(见下)
};
```

- **load_avg**:进程/队列的加权负载,SMP 负载均衡的主要依据(权重大的进程"更重")。
- **util_avg**:实际算力占用(0 ~ SCHED_CAPACITY_SCALE=1024),不看权重,只看"CPU 有多少时间在为它干活"。EAS 选核、schedutil 调频都用它。
- **running vs runnable**:util 只在进程**真正运行**时累加(running);load/runnable 在进程**可运行**(在队列上,含等待)时累加。区别在于:一个 CPU 上跑 2 个进程,每个 util 只有 50%,但队列 runnable 反映"有人在排队"。

#### 3.5 util_est —— 弥补 PELT 的滞后

**PELT 的软肋**:一个进程周期性突发(如每帧渲染),PELT 平均后会低估它的峰值需求,导致调频/选核偏保守。`util_est`(Linux 5.0 引入,6.1 保留)记录进程**出队时的 util_avg**作为"这个进程通常需要多少算力"的估计值,取 `max(util_avg, util_est)` 参与决策,响应更及时。

> **[6.1 变化]** 这是 PELT 最大的版本差异。书中(5.0)讲的是 `runnable_load_avg`/`runnable_load_sum`(加权的可运行负载,用于负载均衡)。6.1 中(commit 9f68395,5.7 合入)引入了**不加权的 `runnable_avg`/`runnable_sum`**,反映队列的"拥塞程度"(有多少东西在排队,与权重无关),并配合负载均衡的重写(见板块四)使用;原 `runnable_load_avg` 概念被移除。另外 5.0 里的 `runnable_avg_yN_sum[]` 表在 4.12 已被删除并用 `__accumulate_pelt_segments` 的公式替代——书中脚注[7]也提到了这一点。RT/DL/irq 也各有独立的 PELT(`rq->avg_rt`/`avg_dl`/`avg_irq`),用于从额定算力里扣除它们,得到 CFS 可用算力。

---

### 板块四:SMP 负载均衡(与 Thor 隔离场景强相关)

#### 4.1 CPU 位图与拓扑

内核用四个 cpumask 描述 CPU 状态:`cpu_possible_mask`(可能存在)⊇ `cpu_present_mask`(已插入)⊇ `cpu_online_mask`(在线)⊇ `cpu_active_mask`(可参与负载均衡)。**这里埋着 isolcpus 的第一个作用点:被隔离的 CPU 仍然 online,但会被移出负载均衡的考量范围(不在均衡用的 span 里)。**

#### 4.2 调度域层次(sched_domain hierarchy)

负载均衡不是"所有核拉平",而是**按硬件拓扑分层就近均衡**。默认拓扑 `default_topology[]` 分三层(从下到上):

```
        DIE domain(整个 socket / 跨 cluster)         ← 迁移代价最高
       /                                    \
   MC domain(共享 LLC/L2 的一簇 CPU)    MC domain
    /        \                          /        \
  SMT        SMT                      SMT        SMT   ← 超线程,共享执行单元(x86)
 /   \      /   \
CPU0 CPU1  CPU2 CPU3   ...

层级越低,CPU 越"亲近"(共享缓存),迁移代价越小,均衡越频繁;
层级越高,迁移代价越大,均衡越保守(要攒够不平衡才动)。
```

每一层用一个 `sched_domain`,域内 CPU 又分成若干 `sched_group`(均衡是在 group 之间搬)。

```c
// include/linux/sched/topology.h
struct sched_domain {
    struct sched_domain *parent, *child; // 上下层
    struct sched_group *groups;          // 本域内的调度组(环形链表)
    unsigned long min_interval, max_interval; // 均衡间隔
    unsigned int imbalance_pct;          // 不平衡阈值(默认 125,即差 25% 才搬)
    int flags;                           // SD_* 标志
    struct sched_domain_shared *shared;
};
```

**关键 SD 标志:** `SD_BALANCE_NEWIDLE`(CPU 变空闲时拉一个过来)、`SD_BALANCE_FORK/EXEC`(fork/exec 时选核)、`SD_BALANCE_WAKE`+`SD_WAKE_AFFINE`(唤醒时的亲和性)、`SD_SHARE_PKG_RESOURCES`(共享 LLC,决定 sd_llc)、`SD_ASYM_CPUCAPACITY`(大小核不对称,EAS 的开关条件)、`SD_PREFER_SIBLING`(优先散开到兄弟组)。

> **[6.1 变化]** 5.0 里每个域都有一个 `SD_LOAD_BALANCE` 标志作总开关;6.1 已**移除 SD_LOAD_BALANCE**(commit e669ac8ab952,~5.14)——是否均衡改由域是否存在、CPU 是否 active 决定。这直接影响 isolcpus 的实现:隔离的效果是"这些 CPU 不出现在任何做均衡的调度域 span 里",而不是靠清一个标志位。`sd_flags.h` 里现在用 `SDF_*` 元标志描述每个 flag 的传播规则。

#### 4.3 均衡的触发与主流程

**三种触发时机:**
1. **周期性均衡**:时钟节拍里 `scheduler_tick() → trigger_load_balance()`,若到了本 CPU 的均衡间隔,触发软中断 `SCHED_SOFTIRQ → run_rebalance_domains() → rebalance_domains()`。
2. **newidle 均衡**:CPU 即将进入 idle 时 `pick_next_task` 找不到进程 → `newidle_balance()`,试着从别处拉一个过来(降低 CPU 空转)。
3. **nohz 均衡**:部分 CPU 处于 nohz(无节拍)空闲时,由一个"kick"CPU 代它们检查。

**rebalance_domains 主流程:**

```
rebalance_domains(rq)
  for_each_domain(cpu, sd):        // 从最底层域往上遍历
    if 到了本域的均衡间隔:
      load_balance(cpu, rq, sd, ...)
        → should_we_balance()      // 本 CPU 是否是该组里"该干活"的那个(通常是组内第一个 idle 或第一个 CPU)
        → find_busiest_group()     // 找最忙的组
            → update_sd_lb_stats() / update_sg_lb_stats()  // 统计各组负载
            → calculate_imbalance() // 算要搬多少负载(imbalance)
        → find_busiest_queue()     // 在最忙组里找最忙的 CPU 队列
        → detach_tasks()           // 从 busiest 摘下若干进程(can_migrate_task 逐个判)
        → attach_tasks()           // 挂到本 CPU
```

- **imbalance_pct(默认 125)**:两组负载差距超过 25% 才值得搬,避免频繁抖动。
- **can_migrate_task()**:逐个判断进程能不能搬——`cpus_allowed` 不允许(亲和性,**这正是绑核/隔离生效的地方**)、cache 太热(刚跑过)、正在运行,都会拒绝。
- **sched_nr_migrate(默认 32)**:一次最多搬 32 个,防止长时间持锁。

#### 4.4 group_type —— 组的"病情分类"

`update_sg_lb_stats` 给每个组定性,决定是否/如何均衡:

> **[6.1 变化]** 这是负载均衡逻辑的**重大重写**(commit 0b0695f2b34a,5.7 "sched/fair: Rework load_balance()")。
> - **5.0(书中)**:`group_other` / `group_imbalanced` / `group_misfit_task` / `group_overloaded`,均衡主要看加权 load。
> - **6.1(实测)**:`group_has_spare`(有空闲算力)/ `group_fully_busy`(刚好用满)/ `group_misfit_task`(有进程装不下,要去大核)/ `group_asym_packing` / `group_imbalanced`(亲和性导致搬不动)/ `group_overloaded`(过载)。新逻辑改用 `runnable_avg` + util + 任务数综合判断,而非单纯加权 load,对大小核(算力不对称)和空闲场景更准。

#### 4.5 唤醒时选核 —— select_task_rq_fair

进程被唤醒时不一定回原 CPU,`try_to_wake_up() → select_task_rq_fair()` 选一个最合适的:

```
select_task_rq_fair(p, prev_cpu, ...):
  ① (若 EAS 开启) find_energy_efficient_cpu()  // 见板块五,选最省电的核
  ② wake_affine 判断:醒来后靠近 waker 还是留在 prev_cpu?
       wake_wide() / record_wakee():统计唤醒关系,识别 "1:N" 唤醒风暴
  ③ 快速路径 select_idle_sibling():在 LLC 域内找一个空闲核
       select_idle_core() → select_idle_cpu() → select_idle_smt()
  ④ 慢速路径 find_idlest_cpu() / find_idlest_group():LLC 内没空闲,跨域找最闲的
```

- **wake_affine**:waker 唤醒 wakee,倾向把 wakee 放到 waker 附近(共享缓存、数据热)。但要防"1:N"问题——一个 server 唤醒成百个 client,若都拉到 server 所在 CPU 会挤爆。`wake_wide()`(Linux 3.12,脚注[10])用 `wakee_flips` 统计唤醒扇出,扇出大就不做 affine,散开放置。
- **sd_llc**:共享最后一级缓存的域,`select_idle_sibling` 的搜索范围就是它。`sd_llc_size` 决定搜索开销。

#### 4.6 专题:isolcpus / CPU 隔离在负载均衡里到底发生了什么

> 这是与 Thor 平台实时优化最相关的一节,把前面各机制串起来回答"隔离一个 CPU 到底动了哪些地方"。

**① 隔离怎么让 CPU 离开负载均衡?**
`isolcpus=` 启动参数(或 cpuset 的 `sched_load_balance=0` 分区)会把这些 CPU 从**参与均衡的调度域 span** 中剔除——重建调度域时,隔离 CPU 不进入任何做均衡的 `sched_domain`(或单独成一个 `sched_load_balance=0` 的分区,该分区 root_domain 不跑周期均衡)。于是 `rebalance_domains`/`load_balance` 遍历不到它,**周期性均衡不会把 CFS 进程搬进/搬出隔离核**。

**② 为什么隔离核上还可能有"残留"CFS 均衡活动?**
- 隔离只挡住**基于调度域的周期均衡和 newidle 均衡**。但 `select_task_rq_fair` 的**唤醒选核**逻辑,以及 fork/exec 选核,仍可能把新唤醒的进程放到隔离核上——**除非该进程被 taskset/cpuset 明确限定了亲和性**。所以隔离要配合"把普通任务的 cpuset 限制在非隔离核",双管齐下才干净。
- 内核线程(per-CPU kthread、workqueue、ksoftirqd 等)仍会在隔离核上运行,timer/RCU 回调也可能残留 → 需要 `nohz_full` + RCU callback offload(`rcu_nocbs`)进一步清理。

**③ EAS 与隔离/实时的冲突:**
EAS 的 `find_energy_efficient_cpu` 只在 CFS 唤醒路径生效,且系统一旦 `overutilized`(某核 util 超额定算力 80%)就整体退出 EAS 回到普通均衡。对隔离核,应确保 EAS 的性能域 span 不覆盖它,否则 EAS 可能计算功耗后把任务派上来。**实时任务(SCHED_FIFO/RR)走 rt_sched_class,不经过 CFS 选核,不受 EAS 影响**,但 EAS 计算 CPU 可用算力时会扣除 RT 负载(`scale_rt_capacity`)。

**④ 如何验证隔离是否生效:**
- `cat /proc/sched_debug`:看隔离核的 `cfs_rq` 是否 `nr_running` 长期为 0、是否出现在别的域的 group 里。
- `cat /proc/schedstat` / `/sys/kernel/debug/sched/domains/`:确认隔离核没有 load_balance 计数增长、不属于任何均衡域。
- `trace-cmd`/kernelshark 抓 `sched_migrate_task` tracepoint:确认没有进程被迁移到隔离核。

---

### 板块五:EAS 绿色节能调度器 与 实时调度

#### 5.1 EAS 要解决的问题

大小核(big.LITTLE)系统里,同样的活放大核跑快但费电,放小核跑慢但省电。传统 SMP 均衡只追求"吞吐最大"(尽量摊开、用满所有核),对手机等功耗敏感场景不合适。**EAS(Energy Aware Scheduling)在唤醒选核时,选一个"能把活干完、又最省电"的核。**(HMP/WALT 是安卓的前身方案,EAS 由 Linaro/Quentin Perret 推动进主线。)

#### 5.2 三种"算力"

系统把 CPU 最高算力量化为 **SCHED_CAPACITY_SCALE = 1024**(HiKey960:大核 A73=1024,小核 A53=592):

- **处理器额定算力(cpu_capacity_orig)**:硬件本身的最大算力,由 `capacity-dmips-mhz` 设备树属性 × 频率归一化得到。
- **CFS 可用算力(cpu_capacity)**:额定算力扣掉 RT/DL/irq 占用后剩给 CFS 的,`scale_rt_capacity` 计算,通常留约 20% 余量给实时任务(所以 CFS 只能用到约 80%)。
- **实际算力(util_avg)**:PELT 算出的当前真实占用(见板块三)。

#### 5.3 能效模型(Energy Model)与性能域

```c
// include/linux/energy_model.h
struct em_perf_domain {          // 一个性能域(通常一个 cluster)
    struct em_cap_state *table;  // 各 OPP 档位的 {频率, 功耗, 能效系数 cost}
    int nr_cap_states;
    unsigned long cpus[];
};
```

- **性能域(PD)**:同一 cluster、同一 DVFS 域的 CPU(必须同架构)组成一个性能域,复用调度域拓扑。
- **能效系数 cost**:`cost = fmax × power / freq`,在 `em_create_pd` 里预算好。功耗公式:`power = dynamic-power-coefficient × freq × 电压²`(设备树 A53 系数 110,A73 系数 550)。
- OPP 表(设备树 `operating-points-v2`)描述每档的频率/电压,由 CPUfreq-dt 驱动初始化,`dev_pm_opp_of_register_em()` 注册成能效模型。

#### 5.4 EAS 唤醒选核 —— find_energy_efficient_cpu

```
select_task_rq_fair() → (EAS 开启且未 overutilized) → find_energy_efficient_cpu(p, prev_cpu):
  ① 若无性能域 或 系统 overutilized → 退出,回普通均衡
  ② 取 sd_asym_cpucapacity 域(覆盖大小核的最高层)
  ③ 每个性能域内,用 select_max_spare_cap_cpus() 选"装下 p 后剩余算力最大"的 CPU 作候选
  ④ 对每个候选,compute_energy() 预测"p 迁过去后整个系统的功耗"
  ⑤ 选功耗最小的 best_energy_cpu
  ⑥ 但若 best 与 prev_cpu 功耗差不多(差值 ≤ prev 的 1/8),优先留 prev_cpu(利用缓存热度)
```

**compute_energy** 的关键:对每个性能域,算迁移后该域所有 CPU 的 util 总和 `sum_util` 和峰值 `max_util`,用 `max_util` 反推需要的频率档,查 `em_cap_state` 拿到该档 cost,`energy = cost × sum_util / capacity`,累加所有性能域得整体功耗。

**书中例子的结论**:小核性能域功耗 ≈137mW,大核 ≈451mW → 选小核 CPU1(既装得下 p,又最省电)。

#### 5.5 overutilized —— EAS 与 SMP 均衡的切换开关

EAS(省电优先)和 SMP 均衡(吞吐优先)目标冲突,用 **overutilized("Tipping Point")** 条件切换:

```c
static inline bool cpu_overutilized(int cpu)
{   // CPU 实际算力需求 > 额定算力的 80% → 过载
    return capacity_of(cpu) * 1024 < cpu_util(cpu) * capacity_margin;
}
```

- 系统**未过载**时:走 EAS,`find_busiest_group` 直接返回(禁止周期均衡),尽量省电、把活收拢到少数核。
- 一旦有核**过载**(触发 overutilized,设 `rd->overutilized`):整体退出 EAS,恢复普通 SMP 均衡,把活摊开保吞吐。
- 判断点:负载均衡的 `update_sg_lb_stats`,以及每次时钟节拍 `task_tick_fair → update_overutilized_status`。

#### 5.6 schedutil —— 调度器驱动的调频

传统 CPUfreq governor 靠采样 CPU 空闲/活跃时间调频,滞后且失真。**schedutil**(Intel,Linux 4.7)直接用调度器的 `util_avg` 作为调频依据,调度器和调频器共享同一份负载数据:

- 通过 per-CPU 的 `cpufreq_update_util_data` 钩子,CFS 在 `update_load_avg → cfs_rq_util_change` 时调用 `cpufreq_update_util()` 请求调频。
- 目标频率 ≈ util 对应的算力需求,响应快、无额外采样。EAS 的功耗计算也依赖它选出的频率档。

#### 5.7 实时调度(8.5)

**实时 = 任务执行时间确定、可预测**。分软实时(统计意义实时,如视频)和硬实时(绝对保证,如飞控)。

**实时延时的组成(从中断发生到实时进程真正执行):**

```
T0 中断发生 ──中断延时──> T1 CPU 响应中断 ──中断处理延时──> T2 唤醒进程A
   ──调度延时──> T3 调度器选中A ──上下文切换延时──> T4 A开始执行
```

- **中断延时**:中断发生时内核可能正关中断(spinlock_irqsave 临界区),无法立即响应。
- **中断处理延时**:下半部在开中断下执行,可能被其他中断打断。
- **调度延时**:唤醒 A 后,内核可能正在自旋锁临界区(关抢占),要等 `preempt_enable()` 才能抢占;且就绪队列可能有更高优先级进程。
- **上下文切换延时**:选中后还要切换才能跑。

**内核实时性改进(表 8.15):**

| 特性 | 进入版本 | 说明 |
|---|---|---|
| Preemption support | 2.5 | 内核可抢占(Robert Love,2001) |
| PI Mutexes | — | 优先级继承互斥锁(解决优先级反转) |
| High-Resolution Timer | 2.6.24 | 高精度定时器 |
| Preemptible RCU | 2.6.25 | 可抢占 RCU |
| IRQ Threads | 2.6.30 | 中断线程化 |
| Deadline scheduler | 3.14 | dl_sched_class(EDF) |
| Full RT Preemption | rt-patch | PREEMPT_RT 补丁集 |

**抢占计数 preempt_count**(`thread_info` 里):为 0 才可安全抢占,含 preempt/softirq/hardirq/NMI/need_resched 几个域。`preempt_disable()` 加 1,`preempt_enable()` 减 1 且减到 0 时调 `__preempt_schedule()` 触发抢占。`CONFIG_PREEMPT` 下,系统调用/异常/中断返回前会检查是否需要抢占。

**实时测量工具:** ftrace 的 `preemptirqsoff` 跟踪器(抓关中断/关抢占的最长时长,书中例子抓到 MMC 驱动 `spin_lock_irqsave` 持锁 ~1ms)、`latencytop`(按内核栈归类延迟原因)、`cyclictest`(测量调度延迟,Thor 平台实时评估的主力工具)。

> **[6.1 变化]** ① `find_energy_efficient_cpu`/`compute_energy` 在 6.1 重构:引入 `struct energy_env`(`eenv`)封装 task busy time / pd busy time,并结合 `util_est`,选核更准(书中分析的是 Quentin Perret 的 eas-dev-v5.0-r1 分支,`select_max_spare_cap_cpus`/`find_best_target` 在主线已整合演进)。② `dev_pm_opp_of_register_em` 在 5.1 后正式合入主线(书中脚注[11])。③ 能效模型 6.1 支持"人工/固件两种 EM"和 `em_perf_state`(取代旧 `em_cap_state` 命名)。④ 实时侧最大变化是 **PREEMPT_RT 主线化**(6.x 大量 RT 补丁已进主线,可直接 `CONFIG_PREEMPT_RT`),以及 `thread_info.preempt_count` 布局细节。CFS 侧 `cpu_overutilized`/`capacity_margin` 逻辑保留但阈值实现细化。

---

### 板块六:进程切换(context_switch)

前面所有决策的最终落地:把 CPU 从 prev 进程切到 next 进程。

#### 6.1 主流程

```
__schedule() → context_switch(rq, prev, next):
  ① switch_mm_irqs_off(prev->mm, next->mm)  // 切换地址空间(页表 + ASID)
  ② switch_to(prev, next, prev)             // ★ 切换寄存器/栈(汇编)
  ③ finish_task_switch(prev)                // 切换后清理(在 next 的上下文里执行)
```

#### 6.2 地址空间切换与 ARM64 ASID

`switch_mm` 把 next 进程的页表基址写入 `TTBR0_EL1`。ARM64 用 **ASID(Address Space ID)** 避免每次切换都刷全部 TLB:

- 每个进程分配一个 ASID,TLB 表项带 ASID 标签 → 切换进程不必刷 TLB,不同进程的 TLB 项靠 ASID 区分,大幅降低切换开销。
- ASID 存在 `TTBR0_EL1` 高位(或 `TTBR1_EL1` bits[63:48],取决于 `TCR_EL1.A1`)。
- ASID 是有限资源(如 16 位),用完要"翻代":`asid_generation` 自增,`check_and_switch_context()` 检查当前 ASID 是否属于本代,不是就 `new_context()` 重新分配,必要时刷本地 TLB(`arch/arm64/mm/context.c`)。
- 切换后需要 `ISB` 屏障保证后续取指用新的地址翻译(`cpu_do_switch_mm`,`proc.S`)。

#### 6.3 寄存器与栈切换 —— switch_to

```c
// arch/arm64:switch_to 保存/恢复 cpu_context
struct cpu_context {
    unsigned long x19..x28;   // callee-saved 寄存器
    unsigned long fp;         // 帧指针
    unsigned long sp;         // ★ 栈指针
    unsigned long pc;         // ★ 返回地址
};  // 存在 task->thread.cpu_context
```

`cpu_switch_to()`(汇编,`entry.S`)把 prev 的 callee-saved 寄存器+sp+pc 存进 prev 的 `cpu_context`,再从 next 的 `cpu_context` 恢复 → CPU 的栈和执行流瞬间"变成"next。

#### 6.4 switch_to 的第三个参数 last —— 最烧脑的点

```c
switch_to(prev, next, last);
```

切换后,CPU 已经在 next 的栈上执行了,但代码里还需要知道"我是从**哪个** prev 切过来的"(用于 finish_task_switch 清理那个 prev)。问题:切换栈之后,原来的局部变量 `prev` 已经是 next 栈上的值了。

- 解法:`cpu_switch_to` 通过 **X0 寄存器**把"真正切出去的那个 prev"带过栈切换的边界,返回后写入 `last`。
- 于是每个进程被再次调度回来时,`last` 都正确指向"上一个把 CPU 让给我的进程",而不是当初调用 switch_to 时的 next。

#### 6.5 新进程的第一次运行 —— ret_from_fork

新 fork 的进程没有"之前的执行流"可恢复,它的 `cpu_context.pc` 被设为 `ret_from_fork`。第一次被调度时,`switch_to` 恢复后直接跳到 `ret_from_fork` → 完成 `finish_task_switch` → 返回用户空间(或对内核线程调用 fn)。

> **[6.1 变化]** 进程切换的整体骨架(`context_switch → switch_mm → switch_to → finish_task_switch`、`last` 参数、`ret_from_fork`)在 6.1 与 5.0 基本一致。差异主要在:ASID 机制增加了对 **KPTI/影子页表** 的配合;`finish_task_switch` 里的 `mmdrop` 路径针对 lazy TLB 做了优化(`CONFIG_MMU_LAZY_TLB_REFCOUNT`);entry 汇编在 5.x 大量 C 化(`entry-common.c`)。

---
