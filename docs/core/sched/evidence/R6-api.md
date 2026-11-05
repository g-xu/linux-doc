# R6 证据:调度器 API(供 01-api)

## 1. Syscall ABI(kernel/sched/core.c,除注明)
| syscall | 定义 | 形态 |
|---|---|---|
| nice | core.c:7288 | SYSCALL_DEFINE1 |
| sched_setscheduler | core.c:8059 | DEFINE3 |
| sched_setparam | core.c:8074 | DEFINE2 |
| sched_setattr | core.c:8085 | DEFINE3 |
| sched_getscheduler | core.c:8128 | DEFINE1 |
| sched_getparam | core.c:8157 | DEFINE2 |
| sched_getattr | core.c:8238 | DEFINE4 |
| sched_setaffinity | core.c:8413 | DEFINE3 |
| sched_getaffinity | core.c:8465 | DEFINE3 |
| sched_yield | core.c:8518 | DEFINE0 |
| sched_get_priority_max | core.c:8956 | DEFINE1 |
| sched_get_priority_min | core.c:8983 | DEFINE1 |
| sched_rr_get_interval | core.c:9048 | DEFINE2 |
| sched_rr_get_interval_time32 | core.c:9061 | DEFINE2(32compat) |
| setpriority | kernel/sys.c:211 | DEFINE3(非 sched/) |
| getpriority | kernel/sys.c:281 | DEFINE2(非 sched/) |

## 2. uapi
- 策略常量 include/uapi/linux/sched.h:SCHED_NORMAL 0@114/FIFO 1@115/RR 2@116/BATCH 3@117/(ISO 保留注释@118)/IDLE 5@119/DEADLINE 6@120;SCHED_RESET_ON_FORK 0x40000000@123
- SCHED_FLAG_* @128-142:RESET_ON_FORK 0x01/RECLAIM 0x02/DL_OVERRUN 0x04/KEEP_POLICY 0x08/KEEP_PARAMS 0x10/UTIL_CLAMP_MIN 0x20/UTIL_CLAMP_MAX 0x40;复合 KEEP_ALL@136/UTIL_CLAMP@139/ALL@142
- struct sched_param@types.h:7(sched_priority int@8)
- struct sched_attr@types.h:102(size@103/policy@105/flags@106/nice@109/priority@112/runtime@115/deadline@116/period@117/util_min@120/util_max@121);SCHED_ATTR_SIZE_VER0 48@11/VER1 56@12

## 3. 内核内 API(EXPORT,core.c;行=EXPORT 处,定义在其上)
wake_up_process 4476 / kick_process 3527(GPL) / set_cpus_allowed_ptr 3019(GPL) / schedule 6759 / preempt_schedule 6900 / preempt_schedule_notrace 6976(GPL) / default_wake_function 7033 / set_user_nice 7251 / sched_setscheduler_nocheck(def)7918 / sched_setattr_nocheck 7903(GPL) / sched_set_fifo 7947(GPL) / sched_set_fifo_low 7957(GPL) / sched_set_normal 7967(GPL) / __cond_resched 8547 / __cond_resched_lock 8606 / yield 8831 / yield_to 8905(GPL) / io_schedule 8946 / io_schedule_timeout 8936 / single_task_running 5399 / preempt_count_add 5834 / preempt_count_sub 5866 / sched_show_task 9102(GPL) / __might_sleep 10045 / __might_resched 10117 / __cant_sleep 10145(GPL) / __cant_migrate 10177(GPL)

## 4. RT/抢占差异(core.c)
- migrate_disable def 2254/EXPORT 2269、migrate_enable def 2271/EXPORT 2301(仅 CONFIG_SMP@2231)——RT 起源、已上游,RT 下抢占保持开启的关键
- __cond_resched:guard `#if !defined(CONFIG_PREEMPTION)||defined(CONFIG_PREEMPT_DYNAMIC)`@8523;full PREEMPTION(RT 选 PREEMPT)无 DYNAMIC 时整块编译掉,cond_resched 变 no-op;rcu_all_qs 路径 `#ifndef CONFIG_PREEMPT_RCU`@8544
- PREEMPT_DYNAMIC:dynamic_cond_resched 8565/EXPORT 8569、dynamic_might_resched 8572/8578;static-call tramp cond_resched@8556/might_resched@8560(HAVE_PREEMPT_DYNAMIC_CALL@8552);preempt_dynamic_enable/disable@8719-8731;preempt_model_##mode EXPORT@8792
- RT 专属块:core.c:3325/4048/6817(#ifdef CONFIG_PREEMPT_RT);6512(#ifndef)

## 5. 索引盲区
- Group1 全是 SYSCALL_DEFINEn 宏,真实符号 sys_*/__se_sys_*/__do_sys_* 不字面出现;行号指宏调用点
- setpriority/getpriority 在 kernel/sys.c 非 sched/;nice 逻辑走 set_user_nice@core.c:7251
- Group3 行号是 EXPORT 处,定义在其上数行;sched_setscheduler_nocheck 无本文件 EXPORT(内部用 3666/7945/7955)
- cond_resched/might_resched 可调符号随 config 是宏/static-call;__cond_resched(8547)是 fallback
- SCHED_ISO 是注释占位(保留值 4),非真 #define
