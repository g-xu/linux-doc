## §3 重要交互叙事

★ 从 §2 矩阵的 37 个非空格子中,选出 **7 个最值得展开的交互**,用叙事体讲清设计意图和反直觉事实。其余 30 个格子的耦合细节已在矩阵中注明,不再逐一展开。

### §3.1 sched_class 回调解耦:core 与所有类的统一接口

core 不知道也不关心具体调度类的存在。它通过 `sched_class` 函数指针表与所有类交互:

```c
// core.c:6002-6003
for_each_class(class) {
    p = class->pick_next_task(rq);
}
```

5 个类(stop / dl / rt / fair / idle)各自用 `DEFINE_SCHED_CLASS` 宏填充这张表,链接器按段序排列(stop → dl → rt → fair → idle),决定了优先级遍历顺序(`core.c:5959` `for_class_range`)。

**传递语义**:core 只问"你有任务要跑吗",不知道 CFS 的红黑树、RT 的 bitmap、DL 的 rb_root。解耦代价是函数指针不可内联,且索引工具无法追踪——这也是本文档集所有回调关系必须手工 grep 取证的原因。

**RT 差异**:`resched_curr_lazy`(8 处调用在 `fair.c`)由 `CONFIG_PREEMPT_LAZY` 保护,置 `TIF_NEED_RESCHED_LAZY` 而非 `TIF_NEED_RESCHED`。这是 RT 补丁引入的真实差异,不是推测(`kernel/sched/sched.h:2353`)。

### §3.2 fair → pelt-load:CFS 侧代码驱动四类 PELT

★★ **反直觉事实**:`__update_blocked_others`(`fair.c:8691`)在 `fair.c` 里,却更新 rt / dl / thermal / irq 四类 PELT:

```c
// fair.c:8706-8709
decayed = update_rt_rq_load_avg(now, rq, curr_class == &rt_sched_class) |
          update_dl_rq_load_avg(now, rq, curr_class == &dl_sched_class) |
          update_thermal_load_avg(rq_clock_thermal(rq), rq, thermal_pressure) |
          update_irq_load_avg(rq, 0);
```

即 **CFS 侧的代码是 rq 级四类 PELT 的实际驱动者**,而不是 `rt.c` / `deadline.c`。rt/dl 自己只在 4 个钩子里更新(见 §2.2 表),而**阻塞态**的四类 PELT 由 fair 驱动。

**顺序约束**(一等证据,`fair.c:8698-8701` 内核注释):

> update_load_avg() can call cpufreq_update_util(). Make sure that RT, DL and IRQ signals have been updated before updating CFS.

rt/dl/irq 必须先于 CFS 更新,否则 `cpufreq_update_util` 读到不完整的利用率。这条约束是 `05-concurrency.md` 的输入(它不是锁序,是**调用序**)。

**两级 PELT**(见 §2.2 详表):CFS 用 per-task PELT(`se->avg`)支撑公平性;rt/dl 只用 rq 级 PELT(`avg_rt`/`avg_dl`),因为严格优先级类内无需公平分配。只读 `fair.c` 会以为 PELT 是全局统一机制。

### §3.3 deadline ↔ rt:共享带宽的过渡方案

★★ **双向关系**,共享字段是 `rt_rq->rt_time`(不是 `rd->dl_bw`——后者属 deadline ↔ topology)。

**deadline → rt**:`update_curr_dl`(`deadline.c:1309`)取 `struct rt_rq *rt_rq = &rq->rt`(`:1396`),持 `rt_rq->rt_runtime_lock`(`:1397`),调 `sched_rt_bandwidth_account`(`rt.c:720`)@ `deadline.c:1404`,写 `rt_rq->rt_time += delta_exec`@`:1405`。

**rt → deadline**:`sched_rt_handler`(`rt.c:2990`)调 `sched_dl_global_validate`/`sched_dl_do_global`(`:3014`/`:3023`),修改 RT sysctl 时验证 DL 带宽约束。

**一等注释**(双向):
- `deadline.c:1384-1394`:"Because -- for now -- we share the rt bandwidth, we need to account our runtime there too, otherwise actual rt tasks would be able to exceed the shared quota."
- `rt.c:110-117`:"SCHED_DEADLINE updates the bandwidth, as a run away RT task with a DL task could hog a CPU."

★ 注释说明这是**过渡方案**,目标是"having the RT groups scheduled using deadline servers"。当前 RT 和 DL 共享带宽池,DL 记账到 RT 配额,防止 DL 任务饿死 RT 任务。

### §3.4 load-balance → core:CPU stopper 机制,不是 double_rq_lock

★★ **更正**:原写"调用 `double_rq_lock`"是**错的**。`double_rq_lock` 定义在 `core.c:588`,但 **fair.c 内 0 处调用**;它的 3 个调用方全在 `core.c`。load-balance 的迁移路径不用它。

**真实调用点**:`load_balance`(`fair.c:10540`)在主动均衡分支调 `stop_one_cpu_nowait(cpu_of(busiest), active_load_balance_cpu_stop, busiest, ...)`(`:10745`),该函数属 **stop_machine 子系统**(`include/linux/stop_machine.h:34`),回调回到 fair 的 `active_load_balance_cpu_stop`(`:10849`)。

**锁用法**(单 rq,非双 rq):`raw_spin_rq_lock_irqsave(busiest, flags)`(`:10716`)、`rq_lock_irqsave(busiest, &rf)`(`:10604`)。

**一等注释**(`fair.c:10860-10864`,说明排队与执行之间的竞态窗口):

> Between queueing the stop-work and running it is a hole in which CPUs can become inactive. We should not move tasks from or to inactive CPUs.

故 `active_load_balance_cpu_stop` 开头必须复查 `!cpu_active(busiest_cpu) || !cpu_active(target_cpu)`(`:10866`)。

### §3.5 topology → core:sched_domain 用 RCU,root_domain 用 spinlock

★★ 同一模块(topology)向 core 暴露的两个共享字段,同步手段截然不同:

| 字段 | 同步手段 | 证据 |
|---|---|---|
| `rq->sd` | ★ **RCU** | `rcu_assign_pointer`(`topology.c:758`);声明为 `__rcu`(`sched.h:1040`) |
| `rq->rd` | ★ **`rq->__lock`** | `raw_spin_rq_lock_irqsave`(`topology.c:490`);★ **非 `__rcu`** |

**为什么不同**:`sd` 被 fair/load-balance/rt/deadline 频繁只读(每次 `for_each_domain` 遍历),用 RCU 避免锁开销;`rd` 只在 topology 重建时写、rt/dl 带宽验证时读,频率低,用 spinlock 足够。

**core 调用 topology 的时机**:仅两个入口——boot 阶段 `sched_init_smp()`(`core.c:9781`/`:9789`)和 CPU 热插拔 `cpuset_cpu_active/inactive()`(`:9532`/`:9555`)。topology 从不回调 core(`topology.c` 里不引用 `__schedule` 等入口)。

### §3.6 rt / deadline / fair → schedutil:RT 调频被短路

★★ **反直觉事实**:rt 和 deadline 都调用 `cpufreq_update_util` 贡献 PELT 信号,但 rt 的信号在调频时被**短路丢弃**。

`effective_cpu_util`(`core.c:7404`)在 `type == FREQUENCY_UTIL` 时检查 `rt_rq_is_runnable(&rq->rt)`(`:7413`),若真**直接返回 `max`**,PELT 累加结果被丢弃。即有可运行 RT 任务就立刻拉满频率,不再看 PELT。

**三个类的调频路径**:
- **fair**:调用 `cpufreq_update_util`(4 处),信号被正常使用
- **rt**:调用 `cpufreq_update_util`(`rt.c:610`/`:1127`),但有 RT 任务时被短路
- **deadline**:调用 `cpufreq_update_util`(`deadline.c:243`/`:257`),DL 用 PELT 做饱和检查(`util + dl_util >= max`,`core.c:7453`)

★ 这是"贡献信号但不用信号"的特例。不读 `core.c:7413` 就发现不了。

### §3.7 其余 30 个格子的模式

除上述 7 个重要交互外,矩阵中其余 30 个格子遵循几个简单模式:

1. **core → 各 class**(4 格):全是 `sched_class` 回调解耦,机制同 §3.1
2. **各 class → core**(3 格):全是共享 `rq->rt/dl/idle` + 调用 `resched_curr`,请求抢占
3. **各 class → pelt-load**(3 格):rt/dl 各调 4 个钩子更新 rq 级 PELT(见 §2.2 表)
4. **各 class → topology**(3 格):共享 `sched_domain`,只读
5. **schedutil → fair/pelt**(2 格):读 `cpu_util_cfs` 和 `sched_avg_*` 获取利用率

这些关系的耦合细节已在 §2 矩阵中注明(函数名 + 行号),无需展开叙事。

