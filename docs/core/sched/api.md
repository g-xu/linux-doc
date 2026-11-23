# 调度 API：按需求选型

这页回答“选哪个接口、为什么”，[详细参考](api-reference.md) 回答“签名、参数、返回值和清理怎样处理”。基线为Linux 6.1.158、arm64、RT补丁与`PREEMPT_RT=y`；用户态库、系统调用ABI、内核宏和导出符号分开。

<a id="user"></a>

## 1. 用户态：先决定要改变什么

| 需求 | 首选入口 | 作用范围 | 详细用法 |
| --- | --- | --- | --- |
| 普通线程在竞争时获得不同权重 | `setpriority/getpriority`，`nice` | 同层竞争的相对份额 | [nice 与线程作用域](api-reference.md#nice) |
| 设置 FIFO/RR 或普通策略 | `sched_setscheduler`、`sched_setparam` | 任务的服务规则与基本优先级 | [策略和权限](api-reference.md#policy) |
| 查询策略与 RT 优先级 | `sched_getscheduler`、`sched_getparam` | 用户可查询的基本调度属性 | [查询](api-reference.md#policy) |
| 设 Deadline 的 Q/D/P | `sched_setattr` ABI | 经准入管理的预算/期限预约 | [扩展属性](api-reference.md#attr) |
| 调整利用率提示 | `sched_setattr` 的 uclamp 字段/flags | 利用率相关输入约束 | [扩展属性](api-reference.md#attr) |
| 限定线程运行 CPU | `sched_setaffinity/getaffinity` | 任务的允许 CPU 范围 | [掩码与失败](api-reference.md#affinity) |
| 管理整组相对份额和上限 | cgroup v2 CPU 文件 | 公平资源组及其层级 | [资源控制](api-reference.md#cgroup) |

Linux 上这类 task 属性主要按线程操作。参数 `pid=0` 常表示调用线程；指定 `getpid()` 通常针对线程组主线程，不会自动遍历所有线程。使用 POSIX 线程句柄时，比较对应 `pthread_*` 接口；不要把 `pthread_t` 强转成 TID。

长期 CPU 份额问题先看 [CFS](sub/fair/01-cfs.md)，周期性停顿先看 [带宽](sub/fair/02-bandwidth.md)，有明确时限再看 [Deadline](sub/realtime/02-deadline.md)。更高优先级不是所有性能问题的通用处理。

<a id="kernel"></a>

## 2. 内核态：条件、完成事件与线程分别选

| 需求 | 首选入口 | 关键条件 | 用法与配对 |
| --- | --- | --- | --- |
| 等队列有数据、设备状态满足 | `wait_event*` + `wake_up*` | 等待可睡眠；RT下普通队列唤醒也需合适任务/线程化中断上下文 | [等待队列](api-reference.md#waitqueue) |
| 等一次异步阶段完成 | `init_completion`、`complete`、`wait_for_completion*` | complete与complete_all的RT上下文不同；超时不自动取消完成者 | [completion](api-reference.md#completion) |
| 建立专用长期执行循环 | `kthread_create/run` + `kthread_stop` | 必须能响应 stop；私有数据要存活 | [线程生命周期](api-reference.md#kthread) |
| 唤醒已持有的有效任务 | `wake_up_process` | 不包含业务条件、事件计数或通用对象引用管理 | [直接唤醒](api-reference.md#wakeup) |
| 约束任务 CPU | `set_cpus_allowed_ptr`；新线程可比较 `kthread_bind` | 前者可阻塞；后者有未运行线程等前提 | [内核亲和性](api-reference.md#kernel-affinity) |
| 设置内核任务策略 | `sched_set_normal`、`sched_set_fifo_low/fifo`，必要时 `sched_setattr_nocheck` | GPL 导出、稳定 task、适用上下文 | [辅助策略接口](api-reference.md#kernel-policy) |
| 延迟等待或长循环让出 | `schedule_timeout_*`、适用 `cond_resched` | 延迟不保证通知，条件等待优先用 wait_event | [延迟与让出](api-reference.md#delay) |

如果工作只是短时、可排队回调，通常先比较工作队列，而不是为每个事件新建 kthread。工作队列属于相邻子系统，本页不重复其完整 API；[completion 骨架](api-reference.md#completion-example) 用它展示异步完成与清理。

如果已经有数据队列，不要额外保存一个裸 `task_struct *`，再手工设置状态和反复 `wake_up_process`。等待接口让“登记、重检、睡眠、退出”更容易保持正确，机制见 [等待与唤醒](sub/core/02-wait-wakeup.md)。

<a id="contexts"></a>

## 3. 三个使用前检查

查接口时按“作用于谁 → 上下文 → 单位/结果 → 对象存活和清理”进行。需要看原理时再跳机制篇，不必为了确认一个 timeout 返回值先读全部调度器。

1. **能否睡眠？** 等待需要可睡眠进程上下文，不能跨等待持有raw spinlock或处于真实关中断现场。通知方也须查内部锁：RT下普通等待队列使用`spinlock_t`，completion使用原始swait锁，二者不是同一类硬中断安全契约。
2. **谁保证对象存在？** 等待队列、completion、私有数据和 task 指针各有生命周期；唤醒不获取任意长期引用，超时不代表异步使用者消失。
3. **返回语义属于哪种接口？** Libc 常用 `-1/errno`，pthread 常返回错误码，内核接口可能是负 errno、剩余 jiffies、0/1、ERR_PTR 或 void。

这三项直接影响能否正确使用，比先背完整内部调用链更重要。

<a id="internal"></a>

## 4. 哪些只用于读实现？

`try_to_wake_up`、`enqueue_task_fair`、`pick_next_entity`、`__schedule`、`rt_mutex_setprio` 及 rq/实体内部锁协议，在本系列中是源码入口，不是建议驱动直接调用的公开 API。

头文件存在声明不等于模块可链接；宏/inline 也可能依赖多个导出实现。详细页记录目标树的导出边界，实际模块仍需按许可证、配置和目标构建核对。

直接进入 [API 详细参考](api-reference.md)，或返回 [专题首页](sub/index.md) 按机制查。
