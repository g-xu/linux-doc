# R3 证据:11 子模块跨模块交互矩阵(供 04-interactions)

## 模块代号
CORE=core.c / FAIR=fair.c:1-8008 / RT=rt.c+cpupri.c / DL=deadline.c+cpudeadline.c / IDST=idle.c+stop_task.c / PELT=pelt.c/h / LB=fair.c:8009-11760 / TOPO=topology.c / SU=cpufreq_schedutil.c / CT=cputime.c+cpuacct.c / PSI=psi.c

## 矩阵(行 A→列 B)
| A＼B | CORE | FAIR | RT | DL | IDST | PELT | LB | TOPO | SU | CT | PSI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CORE | — | pick_next_task_fair 5986;分派 2099/2119;名引用 4753 | 分派 2099/2119+for_each_class 6003;名引用 3692/4751 | 分派 2099/2119+6003 | pick_next_task_idle 5993;for_class_range 5959;stop 3668 | 读 avg_* 经 effective_cpu_util 7404 | — | rq->sd 1122/3734;partition_sched_domains 9532/9555 | —(0,解耦) | —(0,经 task_tick 5636→class) | psi_enqueue 2095/psi_dequeue 2115/psi_task_switch 6673/psi_account_irqtime 5631,6672 |
| FAIR | resched_curr(11处) | — | —(0) | —(0) | update_idle_core idle.c:436 | update_cfs_rq_load_avg 841/4017/4186;__update_load_avg_se 4184;update_load_avg 4174 | 同文件 | sd_numa 2180;wake_affine 6426;sd_llc 6621 | cpufreq_update_util 3507/6114 | cgroup_account_cputime 915;account_group_exec_runtime 916 | (经 CORE) |
| RT | resched_curr(13处) | —(0) | cpupri(内部,8) | sched_dl_global_validate 3014;global_rt_runtime 2795 | — | update_rt_rq_load_avg 1768/1838/2551/2652 | — | rq->rd->cpupri 1490域 | cpufreq_update_util 610/1127 | update_current_exec_runtime 1069 | (经 CORE) |
| DL | resched_curr(10处) | —(0) | 写 rt_rq->rt_time 1405+sched_rt_bandwidth_account 1404;cpupri_set 1490;global_rt_runtime 504/2816 | cpudeadline(内部,7) | — | update_dl_rq_load_avg 2002/2054/2071/2698 | — | rq->rd | cpufreq_update_util 243/257 | update_current_exec_runtime 1341 | (经 CORE) |
| IDST | — | update_idle_core idle.c:436 | — | — | — | — | — | update_idle_core(SMT) | — | update_current_exec_runtime stop_task.c:84(仅 stop) | — |
| PELT | —(叶子) | — | — | — | — | — | — | — | — | — | — |
| LB | active_balance/stop_one_cpu | 同文件 | (经 8706) | (经 8707) | — | cfs_rq->avg.load_avg 8646;se->avg 8792/8805/8823;rt/dl/thermal/irq PELT 8706-8709 | — | sd->imbalance_pct 9018/9086/9332/9590/9882/10246/10486 等20处 | cpufreq_update_util 8842 | — | — |
| TOPO | (被 CORE 调) | — | — | — | — | —(0) | — | — | —(0) | —(0) | —(0) |
| SU | rq_clock/cpu_rq | cpu_util_cfs 186 | 经 effective_cpu_util core.c:7413/7439 | cpu_bw_dl 185/331 | — | 读 avg_* 经 cpu_util_rt/dl/irq | — | rebuild_sched_domains_energy 874 | — | —(0) | — |
| CT | task_rq_lock 296 | — | — | — | — | —(0) | — | — | — | cpuacct_charge cpuacct.c:334 | — |
| PSI | 读 rq->psi_irq_time 1022/1025;task_rq_lock 1170 | — | — | — | — | — | — | — | — | — | — |

## 关键耦合详解
### 1. CORE→各 class(分派解耦)
入队/出队:core.c:2099 sched_class->enqueue_task;2119 dequeue_task。选任务:快路径 5986 pick_next_task_fair;通用 6002-6003 for_each_class class->pick_next_task;idle 兜底 5993。均衡回调先于 put_prev:5959 for_class_range。具名类指针:3668/3692(stop↔rt)、4751/4753(rt/fair)。

### 2. FAIR→PELT(update_*_load_avg 全族)
族(pelt.h:4-8,11,31):__update_load_avg_blocked_se/__update_load_avg_se/__update_load_avg_cfs_rq/update_rt_rq_load_avg/update_dl_rq_load_avg/update_thermal_load_avg/update_irq_load_avg。FAIR 调用:4017 定义 update_cfs_rq_load_avg(内 4073 __update_load_avg_cfs_rq);4184 __update_load_avg_se;3640/3995/4226 __update_load_avg_blocked_se;封装 4174 update_load_avg() 在 4772/4866/4959/5063/5079 驱动。

### 3. RT/DL→PELT(§B.1 著名误判点,已核实非空)
RT:rt.c:1768/1838、2551/2652 调 update_rt_rq_load_avg(rq_clock_pelt(rq),rq,running)。DL:deadline.c:2002/2054/2071/2698 update_dl_rq_load_avg。LB 汇总:fair.c:8706-8709 一次性 rt/dl/thermal/irq。**结论:RT、DL 均参与 PELT,非"只有 fair 用 PELT"**。

### 4. FAIR/RT/DL→SCHEDUTIL(cpufreq_update_util 回调解耦)
触发点:fair.c:3507、6114(IOWAIT)、8842(LB)、rt.c:610/1127、deadline.c:243/257。回调注册 cpufreq_schedutil.c:799-808(sugov_update_* → cpufreq_add_update_util_hook);桥 kernel/sched/sched.h:2864-2872 cpufreq_update_util()→data->func();per-cpu 指针 cpufreq.c:9/29-42。RT 调用是注释所述"防 RT 独占时频率停滞"band-aid(kernel/sched/sched.h:2855-2864)。

### 5. SCHEDUTIL→FAIR/RT/DL/PELT(反向共享字段读)
cpufreq_schedutil.c:185 sg_cpu->bw_dl=cpu_bw_dl(rq)(kernel/sched/sched.h:2925);186 effective_cpu_util(cpu,cpu_util_cfs(cpu),...)。effective_cpu_util 定义 core.c:7404,聚合:7439 util_cfs+cpu_util_rt、7443 cpu_util_dl、7422 cpu_util_irq,7413 检查 rt_rq_is_runnable。cpu_util_* 读 rq->avg_rt/avg_dl/avg_irq/cfs.avg(PELT 产出)。874 rebuild_sched_domains_energy() 反向触达 TOPO。

### 6. DL↔RT(带宽挤压+准入,双向)
DL→RT:deadline.c:1395-1406 update_curr_dl,1398 持 rt_rq->rt_runtime_lock,1404 sched_rt_bandwidth_account(rt.c:720),1405 rt_rq->rt_time+=delta_exec — DL 运行时间计入 RT 带宽账。1490 cpupri_set(rq->rd->cpupri)。DL 总带宽用 RT 全局参数:504-507、2816-2836 to_ratio(global_rt_period(),global_rt_runtime())。
RT→DL:rt.c:3014 sched_dl_global_validate()准入;2795 to_ratio 带宽上限。共享字段:rt_rq->rt_time、rt_rq->rt_runtime、global_rt_runtime()。

### 7. LB→TOPO(读 sched_domain/imbalance_pct)
sd->imbalance_pct 共 20 处:fair.c:9018、9081/9106、9332/9590 group_classify、9882、10246、10486。域遍历依赖 env->sd(8182/8224/8395);update_group_capacity(sd,...)8955。

### 8. 各 class→CPUTIME/CPUACCT(账户)
统一 kernel/sched/sched.h:3231 update_current_exec_runtime()→3235 account_group_exec_runtime+3238 cgroup_account_cputime:RT rt.c:1069、DL deadline.c:1341、STOP stop_task.c:84。FAIR 内联:fair.c:915 cgroup_account_cputime+916 account_group_exec_runtime。最终落 cpuacct.c:334 cpuacct_charge。IDLE 不计账(idle.c 0 命中)。CORE 无直接 account_*(0),仅经 5636 task_tick 分派。

## 空格子取证(0 命中双向)
- RT→FAIR/DL→FAIR:grep enqueue_task_fair|pick_next_task_fair|update_curr_fair|select_task_rq_fair rt.c=0;deadline.c=0
- PELT→任何类:grep pelt.c 仅命中 ___update_load_* 内部数学(348/353/374/379)+注释(208),无外向调用 → 纯叶子
- CPUTIME→PELT:grep load_avg|->avg|cfs_rq cputime.c=0
- TOPO→PSI/CPUTIME/SCHEDUTIL:grep topology.c=0(单向被 CORE/SU 调)
- SCHEDUTIL→CPUTIME:grep account_*_time cpufreq_schedutil.c=0
- CORE→SCHEDUTIL(直接):grep schedutil|sugov|sched_cpufreq core.c=0(完全经 cpufreq_update_util per-cpu 回调解耦)

补充:FAIR 与 LB 同属 fair.c(8008/8009 切分),标"同文件";RT 内 cpupri、DL 内 cpudeadline 视为模块内部(rt.c 命中 cpupri 8 处、deadline.c cpudl 7 处)。
