---
title: API 参考
---

# 01 · API 与 ABI(两层接口)

> 本篇分两层:内核内 API(供其他子系统调用)+ syscall ABI(用户态接口,uapi 落点与 man 页语义)。哪个 API 命中哪个子模块,一律上引 [`04-interactions.md`](04-interactions.md);可调项的运维视角见 [`06-tuning.md`](06-tuning.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §1 syscall ABI 层

### §1.1 策略与参数设置

| syscall | 定义(SYSCALL_DEFINE) | 作用 | man 页语义要点 |
|---|---|---|---|
| `sched_setscheduler` | `kernel/sched/core.c:8059`(DEFINE3) | 设策略 + 静态优先级 | policy ∈ SCHED_*;RT 类要 CAP_SYS_NICE |
| `sched_setparam` | `core.c:8074`(DEFINE2) | 只改 `sched_param.sched_priority` | 保持当前 policy |
| `sched_setattr` | `core.c:8085`(DEFINE3) | 统一设置口,支持 DEADLINE 与 util clamp | 用 `struct sched_attr`,可扩展 size |
| `sched_getscheduler` | `core.c:8128`(DEFINE1) | 查策略 | 返回 SCHED_* |
| `sched_getparam` | `core.c:8157`(DEFINE2) | 查优先级 | — |
| `sched_getattr` | `core.c:8238`(DEFINE4) | 查完整属性 | `usize` 做版本协商 |
| `nice` | `core.c:7288`(DEFINE1) | 相对调整 nice | 走 `set_user_nice`@core.c:7251 |
| `setpriority` / `getpriority` | `kernel/sys.c:211` / `:281` | 按 who(进程/组/用户)调 nice | 不在 sched/,但落到 `set_user_nice` |

`sched_setscheduler`/`sched_setattr` 是调度类切换的总入口——它决定任务落到 fair/rt/deadline 哪个 sched_class(见 [`04-interactions.md`](04-interactions.md) §3.1 的分派)。DEADLINE 的三参数(runtime/deadline/period)只能经 `sched_setattr` 设。

### §1.2 亲和性、让出、查询

| syscall | 定义 | 作用 |
|---|---|---|
| `sched_setaffinity` | `core.c:8413`(DEFINE3) | 设 CPU 亲和掩码 → `set_cpus_allowed_ptr` |
| `sched_getaffinity` | `core.c:8465`(DEFINE3) | 查亲和掩码 |
| `sched_yield` | `core.c:8518`(DEFINE0) | 主动让出,调当前类 `yield_task` |
| `sched_get_priority_max` | `core.c:8956`(DEFINE1) | 查某策略优先级上界 |
| `sched_get_priority_min` | `core.c:8983`(DEFINE1) | 下界 |
| `sched_rr_get_interval` | `core.c:9048`(DEFINE2) | 查 RR 时间片(32 位 compat `:9061`) |

### §1.3 uapi 落点

策略常量 `include/uapi/linux/sched.h`:

| 常量 | 值 | 行 | 归属子模块 |
|---|---|---|---|
| `SCHED_NORMAL` | 0 | 114 | fair |
| `SCHED_FIFO` | 1 | 115 | rt |
| `SCHED_RR` | 2 | 116 | rt |
| `SCHED_BATCH` | 3 | 117 | fair |
| `SCHED_IDLE` | 5 | 119 | fair(SCHED_IDLE ≠ idle 类) |
| `SCHED_DEADLINE` | 6 | 120 | deadline |
| `SCHED_RESET_ON_FORK` | 0x40000000 | 123 | 可 OR 进 policy |

注意 `SCHED_IDLE`(值 5)是 fair 类里的最低权重普通任务,与内核 idle 调度类(swapper)不是一回事;`SCHED_ISO`(值 4)只是保留注释(include/uapi/linux/sched.h:118),未实现。

`SCHED_FLAG_*`(include/uapi/linux/sched.h:128-142):`RESET_ON_FORK` 0x01、`RECLAIM` 0x02、`DL_OVERRUN` 0x04、`KEEP_POLICY` 0x08、`KEEP_PARAMS` 0x10、`UTIL_CLAMP_MIN` 0x20、`UTIL_CLAMP_MAX` 0x40;复合 `KEEP_ALL`@136、`UTIL_CLAMP`@139、`ALL`@142。

结构体 `include/uapi/linux/sched/types.h`:

| 结构/字段 | 行 | 用于 |
|---|---|---|
| `struct sched_param.sched_priority` | 7 / 8 | FIFO/RR |
| `struct sched_attr` | 102 | 统一属性 |
| `.size` / `.sched_policy` / `.sched_flags` | 103 / 105 / 106 | 版本协商 + 策略 + 标志 |
| `.sched_nice` | 109 | NORMAL/BATCH |
| `.sched_priority` | 112 | FIFO/RR |
| `.sched_runtime` / `.sched_deadline` / `.sched_period` | 115 / 116 / 117 | DEADLINE 三参数 |
| `.sched_util_min` / `.sched_util_max` | 120 / 121 | util clamp |
| `SCHED_ATTR_SIZE_VER0` 48 / `VER1` 56 | 11 / 12 | ABI 扩展兼容基准 |

`sched_attr.size` + `SCHED_ATTR_SIZE_VER*` 是 ABI 前向兼容机制:老程序传 48 字节(VER0),新内核识别 56 字节(VER1,加了 util clamp),`sched_getattr` 的 `usize` 参数据此裁剪回填。

## §2 内核内 API 层(供其他子系统调用)

均在 `kernel/sched/core.c`,行号为 `EXPORT_SYMBOL*` 处(定义在其上数行)。

### §2.1 唤醒与调度

| 函数 | 行 | 导出 | 作用 |
|---|---|---|---|
| `wake_up_process` | 4476 | EXPORT_SYMBOL | 唤醒任务(承接 [`03-lifecycle.md`](03-lifecycle.md) 跳 #3) |
| `default_wake_function` | 7033 | EXPORT_SYMBOL | wait_queue 默认唤醒回调 |
| `kick_process` | 3527 | GPL | 踢一个 CPU 重调度 |
| `schedule` | 6759 | EXPORT_SYMBOL | 主动调度点 |
| `preempt_schedule` | 6900 | EXPORT_SYMBOL | 抢占调度 |
| `preempt_schedule_notrace` | 6976 | GPL | 不带 trace 的抢占 |
| `io_schedule` / `io_schedule_timeout` | 8946 / 8936 | EXPORT_SYMBOL | IO 等待中让出 |

### §2.2 优先级/策略(内核内)

| 函数 | 行 | 导出 | 作用 |
|---|---|---|---|
| `set_user_nice` | 7251 | EXPORT_SYMBOL | 改 nice(nice/setpriority 底座) |
| `sched_setscheduler_nocheck` | 7918(def) | 内部用 | 跳过权限检查设策略(kthread/stop) |
| `sched_setattr_nocheck` | 7903 | GPL | 同上,attr 版 |
| `sched_set_fifo` / `sched_set_fifo_low` / `sched_set_normal` | 7947 / 7957 / 7967 | GPL | 内核线程快捷设策略 |
| `set_cpus_allowed_ptr` | 3019 | GPL | 设亲和掩码(内核内) |

`sched_setscheduler_nocheck` 无本文件 EXPORT,内部用点 core.c:3666/7945/7955(见 §4 盲区)。内核线程要设实时优先级一律走 `sched_set_fifo*` 系列,不直接拼 `sched_param`。

### §2.3 让出与抢占控制

| 函数 | 行 | 导出 | 作用 |
|---|---|---|---|
| `yield` / `yield_to` | 8831 / 8905 | EXPORT / GPL | 让出 CPU / 让给指定任务 |
| `__cond_resched` | 8547 | EXPORT_SYMBOL | 协作式抢占点(见 §3) |
| `__cond_resched_lock` | 8606 | EXPORT_SYMBOL | 持锁版 cond_resched |
| `preempt_count_add` / `sub` | 5834 / 5866 | EXPORT_SYMBOL | 抢占计数增减 |
| `preempt_notifier_register` / `unregister` | 4901 / 4913 | GPL | KVM 等用的抢占通知 |

### §2.4 调试/断言辅助

`sched_show_task`@9102(GPL)、`single_task_running`@5399、`__might_sleep`@10045、`__might_resched`@10117、`__cant_sleep`@10145(GPL)、`__cant_migrate`@10177(GPL)。这些是"上下文正确性"断言族,与 [`05-concurrency.md`](05-concurrency.md) §6 的上下文约束表对应。

## §3 RT 树差异(强制)

| API | 非 RT | PREEMPT_RT / DYNAMIC | 出处 |
|---|---|---|---|
| `migrate_disable` / `migrate_enable` | 无(或空) | 钉住任务不迁移;RT 下抢占仍开启,这是关键原语 | def core.c:2254/2271,EXPORT 2269/2301(仅 CONFIG_SMP@2231) |
| `__cond_resched` | 协作让出 | full PREEMPTION(RT 选 PREEMPT)且无 PREEMPT_DYNAMIC 时整块编译掉 → no-op | guard @8523;rcu_all_qs 路径 `#ifndef CONFIG_PREEMPT_RCU`@8544 |
| `cond_resched` / `might_resched`(可调符号) | 普通函数 | PREEMPT_DYNAMIC 下变 static-call/static-key,运行时按 none/voluntary/full 切换 | dynamic_* def 8565/8572;tramp 8556/8560;切换 8719-8731 |
| `preempt_schedule[_notrace]` | 固定路径 | dynamic-preempt 可选路径 | 6900/6919、6976/6995 |
| 亲和/状态/schedule 路径 | — | RT 专属分支 | `#ifdef CONFIG_PREEMPT_RT` core.c:3325/4048/6817;`#ifndef` 6512 |

要点:`migrate_disable`/`migrate_enable` 是 RT 起源、已上游的原语——RT 下不能用 `preempt_disable` 来"顺便"禁迁移(那会破坏 RT 的低延迟),必须用专门的 migrate_disable。`cond_resched` 在 full-preempt 内核里可能是 no-op,因为随时可抢占,不需要协作让出点。语义详见 [`05-concurrency.md`](05-concurrency.md) §7。

## §4 索引盲区声明

- §1 所有 syscall 是 `SYSCALL_DEFINEn` 宏,真实链接符号 `sys_*`/`__se_sys_*`/`__do_sys_*` 不字面出现;表中行号指宏调用点,ABI 符号名须经 `include/linux/syscalls.h` 的宏确认。
- §2 行号是 `EXPORT_SYMBOL*` 处,函数定义在其上数行。
- `sched_setscheduler_nocheck` 在本文件无 EXPORT,内部用点 core.c:3666/7945/7955。
- `cond_resched`/`might_resched` 作为可调符号随 config 是宏或 static-call,`preempt_model_##mode`@8792 是 token-paste 宏,展开名不可 grep;fallback 是 `__cond_resched`@8547。
- `setpriority`/`getpriority` 在 `kernel/sys.c`(非 kernel/sched/),但 nice 逻辑落到 `set_user_nice`@core.c:7251。
- `SCHED_ISO`(include/uapi/linux/sched.h:118)是保留注释占位,非真 `#define`。

