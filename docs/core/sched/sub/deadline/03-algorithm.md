# deadline · 03 算法与策略

> 本篇回答"DL 的算法是什么、什么时候退化、哪个参数能调"。流程"谁调用谁"在 [`02-internals.md`](02-internals.md);跨模块关系(push/pull 与 cpudeadline、DL 挤压 RT 带宽)上引 [`../../04-interactions.md`](../../04-interactions.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §0 设计故事:EDF + CBS + 准入,三件套缺一不可

RT 类的静态优先级回答不了一个问题:"这个任务必须在 5ms 内跑完 2ms"这种**带时限的实时需求**。SCHED_DEADLINE 用三参数模型 `(runtime, deadline, period)` 直接描述它——每 period 周期内,保证在 deadline 前拿到 runtime 的 CPU 时间。

要兑现这个保证,需要三样东西协同:

1. **EDF(最早截止优先)** 回答"该跑谁":谁的绝对 deadline 最近就跑谁。EDF 在单核上是**最优**的——只要一组任务存在可行调度,EDF 就能调度成功。这是它相对固定优先级的根本优势。
2. **CBS(常量带宽服务器)** 回答"跑多久":给每个任务一个"预算 + 补充规则",防止一个任务超支拖垮别人。任务跑满 runtime 就被限流,到下个 period 才补充。CBS 把"一个任务的过错"隔离在它自己的带宽内。
3. **准入控制** 回答"能不能收":新 DL 任务加入前,先检查所有 DL 任务的带宽和 `Σ(runtime/period)` 是否超过 CPU 容量。超了就拒绝(`-EBUSY`)。没有准入,EDF 的最优性保证会因为过载而崩溃。

三者的关系:准入保证"系统不过载"→ CBS 保证"单任务不越界"→ EDF 在这两个前提下给出可行调度。少任何一个,硬实时保证都不成立。这就是为什么 DL 的代码比 RT 重得多——它要维护一个可证明的调度保证,而不只是"优先级高的先跑"。

## §1 算法本体

### §1.1 数据结构(算法直接依赖)

| 结构 | 作用 | 定义位置 |
|---|---|---|
| `dl_rq->root`(rb_root_cached) | 按绝对 deadline 排序,最左=最紧急 | `kernel/sched/sched.h:704`(dl_rq 内) |
| `sched_dl_entity` 三参数 | `dl_runtime/dl_deadline/dl_period` | `include/linux/sched.h:601` |
| `dl_se->runtime` / `deadline` | 运行时剩余预算 / 当前绝对截止 | 同上 |
| `struct dl_bw` | 准入带宽账本 `Σbw` 与总容量 | `kernel/sched/sched.h:318` |

### §1.2 关键不变式

| # | 不变式 | 维护点 | 违反后果 |
|---|---|---|---|
| 1 | 红黑树按绝对 deadline 有序,最左最紧急 | `__enqueue_dl_entity`@deadline.c:1616(`rb_add_cached`) / 出队 `rb_erase_cached` | 选错任务,EDF 语义破坏,可能错过截止 |
| 2 | `runtime > 0` 才可运行,`≤0` 立即限流 | `update_curr_dl`@deadline.c:1309 扣减 + `dl_runtime_exceeded`@deadline.c:1259 判定 | 任务超支仍跑,挤占他人带宽,CBS 失效 |
| 3 | 全域 `Σ(runtime/period) ≤ 容量` 恒成立 | 准入 `sched_dl_overflow`@deadline.c:2865 + `__dl_overflow`@deadline.c:227 | 过载,EDF 最优性失效,必有任务错过截止 |
| 4 | deadline 只在 CBS 规则允许时刷新 | `update_dl_entity`@deadline.c:1018 / 周期补充 `dl_task_timer`@deadline.c:1110 | 随意刷新 deadline = 无限续命,饿死其他 DL 任务 |
| 5 | `running_bw ≤ this_bw ≤ total_bw` 恒成立 | 活跃带宽 `__add/__sub_running_bw`@deadline.c:234/247;保留带宽 `__add/__sub_rq_bw`@261/271;准入 `total_bw` `__dl_add/__dl_sub`@220/213 | 带宽双重计数/泄漏,准入超收,可调度性保证失效 |

不变式取证:红黑树增删、超支判定、准入判据、CBS 刷新均为代码显式函数(行号见表);带宽三层关系见 §1.4。

### §1.3 算法主体

选任务(EDF,`pick_next_task_dl`@deadline.c:2033):
```
dl_se = rb_first_cached(&dl_rq->root)   // 最左=最早绝对 deadline,O(1)
return dl_task_of(dl_se)
```

CBS 记账与补充(`update_curr_dl`@deadline.c:1309 + `dl_task_timer`@deadline.c:1110):
```
// tick / dequeue 时:
runtime -= delta_exec
if dl_runtime_exceeded():                // runtime <= 0
    dl_throttled = 1
    start_dl_timer()                     // arm 到 (deadline) 或下周期
// 定时器到点:
runtime = dl_runtime                     // 补满预算
deadline += dl_period                    // 推进一个周期
dl_throttled = 0; 重新入队
```

CBS 唤醒判定(`update_dl_entity`@deadline.c:1018):新唤醒或"剩余预算相对 deadline 太多"时,重置 `deadline = now + dl_deadline`、`runtime = dl_runtime`,防止长睡任务用旧 deadline 抢占一切。

准入(`sched_dl_overflow`@deadline.c:2865 → `__dl_overflow`@deadline.c:227):
```
new_bw = to_ratio(period, runtime)       // = runtime/period
if Σexisting_bw - old_bw + new_bw > 容量: return -EBUSY   // 拒绝
```

### §1.4 三层带宽、0-lag 与 GRUB(高级机制的算法内核)

DL 的准入(不变式 3)是"静态"保证;要在**运行时**兑现它,内核维护三层带宽,层层收紧:

```
   total_bw   ── 准入账本:所有已收 DL 任务的 Σ(dl_runtime/period),准入时校验(不变式 3)
     ≥
   this_bw    ── 本 rq 保留带宽:入队在此的实体带宽和(含睡着但未到 0-lag 的)
     ≥
   running_bw ── 本 rq 活跃带宽:此刻真在争 CPU 的带宽和(schedutil 调频输入)
```

**0-lag 推论(为什么带宽不随睡眠立即释放,实现见 [`02-internals.md`](02-internals.md) §6):** 一个任务在 deadline 前提前睡,它已"预支"的带宽要到 0-lag 时刻才归零。0-lag 时刻按带宽比例把剩余预算折算回时间:

```
zerolag = deadline − runtime × dl_period / dl_runtime
```

到此刻前,该带宽仍计入 `running_bw`;到点由 `inactive_timer` 扣除。**算法意义**:这保证"任意时刻 `running_bw` = 真实占用",既不提前放行(否则准入超收→错过截止)也不滞后(否则空闲带宽浪费)。

**GRUB 推论(空闲带宽可被回收,实现见 [`02-internals.md`](02-internals.md) §7):** 带 `SCHED_FLAG_RECLAIM` 的任务按有效活跃带宽 `u_act` 放慢预算消耗:

```
u_inact = this_bw − running_bw          // 睡着但未释放 = 系统空闲带宽
u_act   = clamp(running_bw + 补偿, 下界 u_act_min, 上界 = CPU 容量)
扣减量  = delta_exec × u_act / 2^BW_SHIFT   // u_act 越小 → 消耗越慢 → 蹭到更多空闲
```

下界 `u_act_min = dl_bw × bw_ratio` 保证回收任务**至少按自己的配额**消耗,不会把别人的保证吃掉;上界卡在 CPU 容量。这三层带宽正是不变式 5 的对象,GRUB 与 0-lag 都是它的直接推论。

### §1.5 CBS 唤醒:原始规则 vs 修订规则(密度判据)

唤醒时是否重置 deadline,取决于**密度溢出判据** `dl_entity_overflow`(交叉相乘避除法,`deadline.c:909`):

```
runtime / (deadline − now)  >  dl_runtime / dl_deadline    ⟹ 溢出,需新 deadline
```

左边是"要在剩余时间里花完剩余预算所需的带宽",右边是"配置带宽"。溢出即旧 deadline 太紧、会过度抢占。两种应对:

- **原始规则**:`deadline = now + dl_deadline`,`runtime = dl_runtime`(整段重置)。
- **修订规则**(受约束任务 deadline<period,`update_dl_revised_wakeup`@deadline.c:957):不整段重置,按密度给新预算 `runtime = dl_density × (deadline − now)`,其中 `dl_density = dl_runtime/dl_deadline`。防止受约束任务用原始规则拿到超密度预算、短时挤占过多。

## §2 复杂度

| 操作 | 平均 | 最坏 | 最坏触发 |
|---|---|---|---|
| pick(选最早 deadline) | O(1) | O(1) | `rb_first_cached` 读缓存最左(deadline.c:2033) |
| enqueue / dequeue | O(log n) | O(log n) | n=本 dl_rq 就绪 DL 数;红黑树深度 |
| CBS 记账 | O(1) | O(1) | `update_curr_dl` 定长运算 |
| 准入 | O(1)~O(cpus) | O(cpus) | 遍历调度域 CPU 求带宽和 |
| push / pull | O(log n) | O(log n) | cpudeadline 堆查目标(deadline.c 见 04) |
| 带宽记账(running/this/total 增减) | O(1) | O(1) | `__add/__sub_*_bw` 定长;每次踢一次 cpufreq |
| GRUB 缩放 / 0-lag 计算 | O(1) | O(1) | `grub_reclaim`@1283 / `task_non_contending`@392 定长运算 |
| inactive_timer 到点释放 | O(1) | O(1) | `inactive_task_timer`@1410 扣一次 running_bw |

DL 与 CFS 同为 O(log n) 插删,但排序键是"绝对 deadline"而非"vruntime",且多了 CBS、准入、三层带宽记账三层定长开销——每层都是 O(1),所以不改变整体 O(log n) 量级,只是常数比 RT 大得多。

## §3 退化条件

| 退化场景 | 触发条件 | 表现 | 可观测指标 | 缓解手段 |
|---|---|---|---|---|
| 准入拒绝 | 新任务使带宽和超容量 | `sched_setattr` 返回 `-EBUSY` | 设参失败 | 减小 runtime / 增大 period |
| 频繁限流 | runtime 相对负载偏小 | 任务反复 `dl_throttled`,响应抖动 | `dl_se->dl_throttled` 置位率 | 增大 runtime 或放宽 deadline |
| 短周期风暴 | period 极小、任务多 | 定时器/记账开销放大 | 定时器中断率、`update_curr_dl` 热点 | 合并任务 / 增大 period |
| DL 挤压 RT/fair | DL 带宽占比高 | RT 可用带宽被扣、fair 被挤 | RT `rt_throttled`、fair 延迟 | 控制 DL 总带宽,见 §4 / [`../../04-interactions.md`](../../04-interactions.md) |
| 跨核迁移抖动 | 多 CPU 频繁 push/pull | 缓存失效 | `sched_migrate_task` 频率 | 绑核 / 亲和性 |
| 带宽被非连续态"卡住" | 任务频繁短睡再醒 | `running_bw` 长期高于真实占用,准入余量虚低 | `running_bw` vs `this_bw` 差(/proc/sched_debug) | 属正确行为(0-lag 保证);仅在误判准入时关注 |

前三行触发均从代码/准入判据取证;后两行跨模块,标注了性质。

## §4 参数敏感性

| 参数 | 类型 | 默认值 | 影响 | 调大 | 调小 | 定义位置 |
|---|---|---|---|---|---|---|
| `sched_deadline_period_max_us` | sysctl | (源码取证) | DL period 上界 | 允许更慢周期 | 限制周期范围 | `kernel/sched/deadline.c`(sysctl 表@29) |
| `sched_deadline_period_min_us` | sysctl | (源码取证) | DL period 下界 | 限制最短周期 | 允许更急周期 | 同上(注册@:51) |
| 全域 DL 带宽上限 | 派生自 RT | `sched_rt_runtime_us`/`period` | 准入容量:DL 复用 RT 带宽预算 | DL 可收更多任务 | 准入更易拒绝 | 见 §5 耦合 |
| 单任务三参数 | `sched_attr` | 用户设定 | `runtime/deadline/period` | — | — | `include/uapi/linux/sched/types.h` |
| `SCHED_FLAG_RECLAIM` | `sched_attr` 标志 | 关 | 开启 GRUB:回收空闲带宽跑更久(§1.4) | 更充分利用空闲 CPU | 严格按配额 | `include/uapi/linux/sched.h` |

sysctl 表注册取证:`deadline.c:29`(注册@:51)。运维视角(如何用 `chrt`/`sched_setattr` 设、cgroup 落点)见 [`../../06-tuning.md`](../../06-tuning.md) §1.1;本篇讲机理。

## §5 设计权衡与 DL↔RT 带宽耦合

| 候选方案 | 优 | 劣 | 内核为什么选/没选 |
|---|---|---|---|
| EDF(绝对 deadline) | 单核最优,支持时限语义 | 需 CBS+准入配套,实现重 | 选了:唯一能给硬实时保证的机制 |
| 固定优先级(RT) | 简单、O(1) | 表达不了"何时前完成多少" | RT 已提供,DL 补其空缺 |
| 无准入的 EDF | 实现轻 | 过载即崩,最优性失效 | 没选:准入是保证的前提 |

**DL↔RT 耦合(本套文档的关键发现)**:DL 的可用带宽复用 RT 的 `sched_rt_runtime_us`/`sched_rt_period_us` 预算——DL 任务运行会先从这份预算扣减,准入容量也据此计算。含义:调 RT 带宽会连带改变 DL 的准入余量,反之 DL 收得多会挤压 RT 实际可用带宽。完整论证在 [`../../04-interactions.md`](../../04-interactions.md) §3(dl×rt 格子)与 [`08-feynman.md`](../../08-feynman.md) 概念四。

EDF 最优性、CBS 隔离性属可调度性理论结论,`Documentation/scheduler/sched-deadline.rst` 是一等参考(含带宽和判据的形式化)。代码只实现机制,不含最优性证明——这部分标策略型证据(见 [`99-evidence.md`](99-evidence.md))。
