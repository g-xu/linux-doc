# 调度 API 详细参考

本页面向使用查询，基线为Linux 6.1.158、arm64、RT补丁与`PREEMPT_RT=y`。先选接口看 [API 指南](api.md)。下面的C代码是未编译、未运行的教学骨架；明确标出的业务、初始化和停机协议须由调用者实现，不冒充可直接加载的完整驱动。

这里以调用契约组织，而非沿源码调用链组织。每族接口先给声明，再解释作用对象、参数与单位、返回/失败和配对清理；例子只展示该契约所需的完整局部协议。机制说明由对应专题承接。

<a id="quick"></a>

## 速查

| 使用场景 | 直接查询 |
| --- | --- |
| 用户线程的权重、策略和预算 | [nice](#nice) · [基本策略/优先级](#policy) · [扩展属性 Q/D/P、uclamp](#attr) |
| 用户线程或资源组的范围/上限 | [亲和性](#affinity) · [cgroup](#cgroup) |
| 内核条件通知或异步完成 | [waitqueue](#waitqueue) · [completion](#completion) · [异步收尾示例](#completion-example) |
| 内核任务管理 | [kthread](#kthread) · [直接唤醒](#wakeup) · [CPU 约束](#kernel-affinity) · [策略辅助](#kernel-policy) |
| 延迟等待与长循环 | [timeout、延迟与让出](#delay) |

| 接口族 | 返回成功 | 失败/其他结果 | 单位或配对 |
| --- | --- | --- | --- |
| `setpriority` / `getpriority` | 0 / nice 值 | `-1`，查询先清 errno | nice -20～19 |
| `sched_setscheduler` / `sched_getscheduler` | 0 / policy | `-1/errno` | TID；RT 优先级单独查询 |
| `sched_setattr/getattr` 的 syscall 包装 | 0 | `-1/errno` | size 字节；Q/D/P 纳秒 |
| libc `sched_setaffinity/getaffinity` | 0 | `-1/errno` | 掩码字节数 |
| `wait_event_interruptible` | 0 | `-ERESTARTSYS` | 条件由调用者维护 |
| `wait_event_interruptible_timeout` | 正数 | 0超时；负数信号 | 剩余 jiffies |
| `wait_for_completion_timeout` | 正数 | 0超时 | 消费完成状态；对象不能提前释放 |
| `wait_for_completion_interruptible_timeout` | 正数 | 0超时；负数信号 | 剩余 jiffies |
| `kthread_create/run` | task 指针 | `ERR_PTR(...)` | stop 协议、私有数据存活 |
| `kthread_stop` | threadfn 的返回值 | 未启动相应情况可为 `-EINTR` | 同步等待，不能持有线程所需锁 |
| `wake_up_process` | 1表示本次唤醒成功 | 0表示未作相应唤醒 | 不等于线程已经执行 |
| `set_cpus_allowed_ptr` | 0 | 负 errno | 借用掩码；GPL 导出 |
| `sched_set_fifo/low/normal` | void | 内部 WARN 处理，不提供调用者错误码 | GPL 导出 |

<a id="nice"></a>

## 1. Nice：`setpriority`、`getpriority`、`nice`

### 声明与作用对象

用户态声明：

```c
#include <sys/resource.h>
#include <unistd.h>

int setpriority(int which, id_t who, int prio);
int getpriority(int which, id_t who);
int nice(int inc);
```

`setpriority` 设置绝对 nice，`nice` 对调用线程增减 nice。较小 nice 对适用普通竞争提供更大权重，不是 RT 优先级，也不保证单独获得 CPU。

### 返回、权限与恢复

Libc的 `nice()` 成功返回新的nice值，-1也可能是合法结果，所以同样先清 `errno` 再检查。Raw Linux nice系统调用的成功值不同，不将下述libc查询规则套到它。`setpriority()`成功返回0。

`which` 可为 `PRIO_PROCESS`、`PRIO_PGRP`、`PRIO_USER`。为某线程使用前者和 TID；`who=0` 的意义随 `which` 改变。针对多个任务的选择没有提供“一起修改的事务”保证，查询又可能返回其中最高优先级，不是每线程列表。

降低 nice 数值通常需要相应 `CAP_SYS_NICE` 或 `RLIMIT_NICE` 条件；修改其他任务还受身份/权限约束。检查 `EACCES/EPERM`、`ESRCH` 和参数错误。提高 nice 后再恢复较小值可能权限不足，不能把临时改 nice 的恢复当必然成功。

### 查询示例

查询必须区分合法 -1 与失败：

```c
#include <errno.h>
#include <sys/resource.h>

int read_thread_nice(id_t tid, int *out)
{
    int value;

    errno = 0;
    value = getpriority(PRIO_PROCESS, tid);
    if (value == -1 && errno != 0)
        return -1;
    *out = value;
    return 0;
}
```

`out` 必须非空；代码采用 libc 的 nice 值，不采用 raw syscall 的编码值。Linux 当前 nice 按线程生效，不能认为一次指定主线程会修改所有兄弟线程。

依据：`kernel/sys.c` 的 priority 系统调用、`kernel/sched/core.c` 的 `set_user_nice`；[CFS 权重](sub/fair/01-cfs.md#shares)。

<a id="policy"></a>

## 2. 策略与实时优先级

### 声明与参数选择

```c
#include <sched.h>

int sched_setscheduler(pid_t pid, int policy,
                       const struct sched_param *param);
int sched_getscheduler(pid_t pid);
int sched_setparam(pid_t pid, const struct sched_param *param);
int sched_getparam(pid_t pid, struct sched_param *param);
int sched_get_priority_min(int policy);
int sched_get_priority_max(int policy);
int sched_rr_get_interval(pid_t pid, struct timespec *interval);
```

`pid=0` 作用于调用线程。`sched_setscheduler` 同时设置策略/priority，`sched_setparam` 不选择新策略；`sched_getparam` 返回基本参数，不能查 Deadline 的完整 Q/D/P。

| 策略 | `sched_priority` | 附加理解 |
| --- | --- | --- |
| OTHER/BATCH/IDLE | 0 | 普通权重与 idle 特例另处理 |
| FIFO/RR | Linux为1～99，实际查询 min/max | 数字越大越高；RR 间隔另查 |
| DEADLINE | 不经本组 setters 设置完整预约 | 使用下一节扩展 ABI |

### 返回、flags 与权限

设置成功在 Linux 返回0，**不是旧策略**。查询策略返回非负 policy；`sched_getparam`、`sched_rr_get_interval` 成功返回0并写输出。失败为 `-1/errno`，常见 `EINVAL`、`EPERM`、`ESRCH`。

旧策略接口可在policy中按位加入`SCHED_RESET_ON_FORK`，查询结果也可能带这一位。比较纯策略或向`sched_get_priority_min/max`传策略时先分离它；扩展属性接口使用的是`attr.sched_flags`中的`SCHED_FLAG_RESET_ON_FORK`，两者常量不同。

FIFO/RR 使用受身份、能力、`RLIMIT_RTPRIO` 和安全钩子等约束；必须规划线程阻塞/让出及系统恢复。不要为了让示例执行而无条件设99或关闭RT带宽。

### 查询与设置骨架

用于查询的骨架：

```c
#include <sched.h>

int read_basic_policy(pid_t tid, int *policy, int *priority)
{
    struct sched_param p = {0};
    int result = sched_getscheduler(tid);

    if (result == -1)
        return -1;
    if (sched_getparam(tid, &p) == -1)
        return -1;
    *policy = result;
    *priority = p.sched_priority;
    return 0;
}
```

两个输出指针须有效。两次查询不是原子快照，若其他管理者同时改属性，可得到跨时刻组合。设置实例应先检查请求范围，再用 `struct sched_param p = {.sched_priority = desired}` 调用并检查结果；恢复策略也是新一次有权限/失败条件的设置，不能忽略返回。

`pthread_setschedparam` 等采用 `pthread_t`，其失败通常直接返回错误号，不套 `-1/errno`。线程创建时还要考虑属性继承，超出本页 syscall 契约范围。

依据：`kernel/sched/core.c` 的系统调用/`__sched_setscheduler`，机制见 [FIFO/RR](sub/realtime/01-fifo-rr.md)。

<a id="attr"></a>

## 3. 扩展属性：`sched_setattr`、`sched_getattr`

### ABI 与结构

本节给 **Linux系统调用 ABI 包装形式**，不是保证当前 libc 已提供同名包装，也不是内核里接收 `task_struct *` 的同名函数：

```c
int sched_setattr(pid_t tid, const struct sched_attr *attr,
                  unsigned int flags);
int sched_getattr(pid_t tid, struct sched_attr *attr,
                  unsigned int size, unsigned int flags);
```

目标 UAPI 结构见 `include/uapi/linux/sched/types.h`：

```c
struct sched_attr {
    __u32 size;
    __u32 sched_policy;
    __u64 sched_flags;
    __s32 sched_nice;
    __u32 sched_priority;
    __u64 sched_runtime;
    __u64 sched_deadline;
    __u64 sched_period;
    __u32 sched_util_min;
    __u32 sched_util_max;
};
```

### 参数、单位与 flags

- `attr.size` 是请求结构字节数，本地版本有48/56字节 ABI 代际。清零结构，不把未初始化尾部传给兼容检查。
- 外部 `flags` 在本目标 ABI 中传0；`sched_flags` 是结构内的另一组位，不能混用。
- 普通策略设置 nice，FIFO/RR 设置 priority；Deadline 的 Q/D/P 对应后三个64位时间字段，单位全部为纳秒。
- 本地允许 period=0 时按 deadline 解释，但新代码最好明确填写有效 P。满足参数关系不代表准入通过。
- Deadline还要求Q不小于本地`1 << DL_SCALE`，当前为1024ns；D/P不能设置第63位，有效P必须处于当前`sched_deadline_period_min_us`与`sched_deadline_period_max_us`范围。这两个sysctl用微秒，属性字段仍用纳秒；不把源码初始范围当成所有设备上的当前设置。
- Uclamp 字段标度0～1024，要设置相应 `SCHED_FLAG_UTIL_CLAMP_MIN/MAX` 才表达更新。相应字段以 `UINT32_MAX` 请求恢复默认有目标版本语义；它不是一个普通util值。
- Uclamp 的结构标度与 cgroup 文件的百分数文本不同，不直接复制数值。

常用 `sched_flags` 也按用途区分：`SCHED_FLAG_RESET_ON_FORK`控制孩子继承限制，`SCHED_FLAG_RECLAIM`请求DL带宽回收，`SCHED_FLAG_DL_OVERRUN`请求超额通知。它们与外部flags=0不是同一参数；重调度策略与进程信号处理也需要调用者另行设计。只将整个结构清零再设置新policy，并不表达“重置所有现存uclamp请求”。

### 失败与输出结构兼容

失败常见：`EINVAL`（参数/flags）、`E2BIG`（结构兼容）、`EPERM`（权限或适用DL范围）、`EBUSY`（带宽准入）、`ESRCH`、`EFAULT`，以及相关特性不支持的错误。基线源码缺少能力或配置时，不能把参数错误一律当权限问题。

`sched_getattr` 的 size 是输出缓冲字节数。本地`sched_attr_copy_to_user`将返回结构size设为`min(用户缓冲大小, 内核结构大小)`：传48字节可以成功，但不会返回后面的uclamp字段；传56字节也不能证明目标内核启用了uclamp。清零输出缓冲并检查返回size，不将未返回字段当已配置提示；查询没有锁住目标属性直到后续设置。

### 查询与设置骨架

查询骨架使用独立本地 ABI 类型，避免系统头文件的同名结构冲突；syscall 编号来自目标用户态头文件，**不硬编码 arm64 数字**：

```c
#define _GNU_SOURCE
#include <stdint.h>
#include <sys/syscall.h>
#include <unistd.h>

struct sched_attr_61 {
    uint32_t size, sched_policy;
    uint64_t sched_flags;
    int32_t sched_nice;
    uint32_t sched_priority;
    uint64_t sched_runtime, sched_deadline, sched_period;
    uint32_t sched_util_min, sched_util_max;
};

int read_extended_policy(pid_t tid, struct sched_attr_61 *out)
{
    struct sched_attr_61 value = {0};

    if (syscall(SYS_sched_getattr, tid, &value, sizeof(value), 0) == -1)
        return -1;
    *out = value;
    return 0;
}
```

使用端应确认类型布局和可用 syscall 宏，`out`有效。设置使用同布局结构清零并填 `size=sizeof(attr)`，再调用 `syscall(SYS_sched_setattr, tid, &attr, 0)` 检查 `-1/errno`。例如模型Q=2、D=5、P=10 ms分别填2,000,000、5,000,000、10,000,000 ns，不在本机执行该策略变更。

预约参数修改、退出和线程销毁会由内核处理带宽关系；调用者不手工写 rq 预算。不应临时改变其他线程后假设“原样写回就必然恢复”，权限、竞争和准入都可能变化。

依据：`core.c` 的 `sched_copy_attr`、`__sched_setscheduler`、`sys_sched_getattr`，`deadline.c` 的参数/准入；[预算机制](sub/realtime/02-deadline.md)。

<a id="affinity"></a>

## 4. 用户 CPU 亲和性

### 声明与掩码

```c
#define _GNU_SOURCE
#include <sched.h>

int sched_setaffinity(pid_t pid, size_t cpusetsize,
                      const cpu_set_t *mask);
int sched_getaffinity(pid_t pid, size_t cpusetsize, cpu_set_t *mask);
```

此处是 libc 声明，两者成功返回0，失败 `-1/errno`；raw `sched_getaffinity` 的成功字节数不同，不混用。

`cpusetsize` 是缓冲字节数，`CPU_ZERO/CPU_SET` 初始化集合。固定 `cpu_set_t` 有边界，大掩码用 `CPU_ALLOC/CPU_ALLOC_SIZE/CPU_FREE` 等动态集合，不只看当前在线 CPU 数猜内核掩码长度。

请求受实际允许/在线和 cpuset 等条件限制。空合法交集可能 `EINVAL`，query 缓冲过小也可能 `EINVAL`；权限不足 `EPERM`，目标不存在 `ESRCH`。设置后可再查询有效范围，不能假定请求的每个位都保留。

### 设置示例与恢复

设置骨架不代表 CPU 独占：

```c
#define _GNU_SOURCE
#include <errno.h>
#include <sched.h>

int limit_thread_cpu(pid_t tid, int cpu)
{
    cpu_set_t mask;

    if (cpu < 0 || cpu >= CPU_SETSIZE) {
        errno = EINVAL;
        return -1;
    }
    CPU_ZERO(&mask);
    CPU_SET(cpu, &mask);
    return sched_setaffinity(tid, sizeof(mask), &mask);
}
```

这是固定集合版本，不能用于超边界CPU。若需临时绑定，先保存足够大的原掩码，执行后检查恢复结果；热插拔和cpuset变化可使恢复失败，不忽略错误。

依据：`kernel/sched/core.c` 的 affinity syscall、`Documentation/admin-guide/cgroup-v2.rst` 的 cpuset；[放置约束](sub/placement/02-placement.md#constraints)。

<a id="waitqueue"></a>

## 5. 等待队列：`wait_event*` 与 `wake_up*`

### 调用形式与配对

头文件 `<linux/wait.h>`。它们主要是宏，参数传 **队列对象**，不是全部都传指针：

```c
wait_event(wq, condition);                         /* void */
wait_event_idle(wq, condition);                    /* void, TASK_NOLOAD */
wait_event_interruptible(wq, condition);           /* int */
wait_event_timeout(wq, condition, timeout);        /* long */
wait_event_interruptible_timeout(wq, condition, timeout); /* long */

init_waitqueue_head(&wq);
wake_up(&wq);
wake_up_all(&wq);
wake_up_interruptible(&wq);
```

`timeout` 为 jiffies，常用 `msecs_to_jiffies` 转换。阻塞等待只能在可睡眠上下文；不能持有不允许睡眠的锁，也不能依赖宏替自己发布业务数据。

### 上下文、结果与通知范围

**RT通知方的上下文也要检查。** 普通`wait_queue_head`的保护锁是`spinlock_t`，`wake_up*`会获取它；在本基线下锁竞争可能调度。因此通常从任务或线程化中断处理调用，不能直接从真实hardirq、NMI、关硬中断或持raw锁现场取得这类锁。需要从硬中断发起通知时，按事件选择允许该现场的机制或将操作交给线程上下文，而不是把`wake_up_process`当作等待队列API的无条件替换。

| 等待形式 | 条件满足 | 终止 |
| --- | --- | --- |
| 普通 wait_event | 继续，void | 无本接口超时或信号返回 |
| wait_event_idle | 继续，void；不计入相应系统负载 | 不被普通信号中断，无本接口超时 |
| interruptible | 0 | 信号 `-ERESTARTSYS` |
| timeout | 至少1，通常为剩余 jiffies | 条件仍假且到期返回0 |
| interruptible_timeout | 同上正数 | 0超时，负数信号 |

在超时边界条件为真可返回1，不将“只返回1”判作失败。条件可被反复求值，通常不带不可重复的消费副作用。

`wake_up` 处理适用TASK_NORMAL等待；`wake_up_interruptible`不唤醒普通uninterruptible等待，必须与等待形式匹配。普通 `wake_up` 的“一个”约束针对独占等待者数量，非独占项不等价于只唤醒一个；`wake_up_all`扩展范围仍不替消费者预留资源。

### 一次发布示例及存活前提

一次性发布骨架，只适合一次producer、约定好的单次读取且对象持续存活；producer位于允许获取RT队列锁的任务/线程化中断上下文：

```c
#include <linux/errno.h>
#include <linux/jiffies.h>
#include <linux/wait.h>
#include <asm/barrier.h>

struct published_result {
    wait_queue_head_t wait;
    int ready;
    int value;
};

void init_published_result(struct published_result *r)
{
    init_waitqueue_head(&r->wait);
    r->ready = 0;
    r->value = 0;
}

void publish_result(struct published_result *r, int value)
{
    r->value = value;
    smp_store_release(&r->ready, 1);
    wake_up_interruptible(&r->wait);
}

int await_result(struct published_result *r, unsigned int ms, int *out)
{
    long ret;

    ret = wait_event_interruptible_timeout(
        r->wait, smp_load_acquire(&r->ready), msecs_to_jiffies(ms));
    if (ret < 0)
        return (int)ret;
    if (ret == 0)
        return -ETIMEDOUT;
    *out = r->value;
    return 0;
}
```

调用者在发布对象给任一使用者前调用`init_published_result`。未展示分配与业务启动：必须保证结果对象到producer/consumer都结束后才释放，输出指针有效。

此例不重置ready、不并发多次写value。可复用多消费者队列应另用对象锁或对应协议；超时/信号返回时 producer 可能继续，不能立即释放 `r`。通知竞态解释见 [等待机制](sub/core/02-wait-wakeup.md#protocol)。

<a id="completion"></a>

## 6. Completion：完成状态、超时和异步对象存活

### 声明与状态消费

头文件 `<linux/completion.h>`，本地对象使用 `swait_queue_head`。主要形式：

```c
void init_completion(struct completion *x);       /* inline */
void reinit_completion(struct completion *x);     /* inline */
void complete(struct completion *x);
void complete_all(struct completion *x);
void wait_for_completion(struct completion *x);
int wait_for_completion_interruptible(struct completion *x);
unsigned long wait_for_completion_timeout(struct completion *x,
                                         unsigned long timeout);
long wait_for_completion_interruptible_timeout(struct completion *x,
                                              unsigned long timeout);
bool try_wait_for_completion(struct completion *x);
bool completion_done(struct completion *x);
```

`complete`累计可消费状态，普通成功wait消费一次；`complete_all`使相应后来等待也可通过，直到安全reinit。`try_wait_for_completion`非阻塞，true表示消费到完成状态；`completion_done`只查询，不取得消费资格，不能用“先查再wait”保证无阻塞。

### 上下文、返回与复用

`wait_for_completion`在进程可睡眠上下文使用，IRQ应开启；不能在硬中断、关IRQ或禁止睡眠现场等待。这里内部swait使用`raw_spinlock_t`，`complete`及适用非阻塞try可用于相应原子/硬中断上下文，但仍要遵守锁序和对象存活，不能推广到任意NMI现场。

本地`complete_all`明确有`lockdep_assert_RT_in_threaded_ctx()`：按RT的任务/线程化中断上下文调用，不把普通`complete`的硬中断用法原样套给它。

返回：普通wait为void，interruptible成功0或负信号错误，timeout正数剩余jiffies/边界1或0超时，组合形式还可负数。超时不是取消异步工作。

对象初始化一次，复用前确认旧完成者和等待者都结束。`reinit_completion`只重置done，不替调用者排空等待队列；不能与旧一轮`complete`竞争。局部变量用 `DECLARE_COMPLETION_ONSTACK`，且不能在仍有异步访问时返回。

<a id="completion-example"></a>

### 有限等待，但安全退出：先结束异步使用

用工作队列回调演示完成协议；业务结果以常量代替，非完整驱动。对象留在函数栈上，所以无论等待如何结束，都先同步结束唯一回调：

此骨架从独立、可睡眠且IRQ开启的进程上下文调用，不持有回调需要的锁，也不占住回调所依赖的有限工作队列执行名额。否则“提交后同步等待”可能形成依赖死锁；completion不替调用者解除这类依赖。

```c
#include <linux/completion.h>
#include <linux/errno.h>
#include <linux/jiffies.h>
#include <linux/workqueue.h>

struct result_job {
    struct work_struct work;
    struct completion done;
    int result;
};

static void result_work(struct work_struct *work)
{
    struct result_job *job = container_of(work, struct result_job, work);

    job->result = 42;
    complete(&job->done);
}

static int wait_result_job(unsigned int ms, int *out)
{
    struct result_job job;
    unsigned long left;

    INIT_WORK_ONSTACK(&job.work, result_work);
    init_completion(&job.done);
    job.result = 0;
    if (!schedule_work(&job.work)) {
        destroy_work_on_stack(&job.work);
        return -EBUSY;
    }
    left = wait_for_completion_timeout(&job.done, msecs_to_jiffies(ms));
    flush_work(&job.work);
    destroy_work_on_stack(&job.work);
    if (!left)
        return -ETIMEDOUT;
    *out = job.result;
    return 0;
}
```

这里显式 `init_completion` 初始化栈上的嵌入对象，work以`INIT_WORK_ONSTACK`初始化，并在确认不再使用后`destroy_work_on_stack`；queue只提交一次，回调不会重排自己，`out`有效。`schedule_work=false`表明未成功排入；对这里新初始化、无其他提交者的对象不是预期正常分支。

**这个函数不保证总耗时最多ms。** 超时后`flush_work`仍可能等待真正完成，代价是换取栈对象安全。若业务要求超时即返回，应改为长生命周期/引用管理对象和清晰取消协议，不能删掉flush后直接返回。

成功wait也不保证回调函数已经完全退出，因`complete`后仍有收尾，所以仍flush。若使用cancel机制，还要确认未执行、正在执行及可能再次提交的分支；取消工作并不替你消费/释放任意外部资源。

依据：`kernel/sched/completion.c` 的计数、wait和同步；目标树工作队列的queue/flush契约。机制见 [completion为何接住早到通知](sub/core/02-wait-wakeup.md#completion)。

<a id="kthread"></a>

## 7. Kthread：创建、运行和停止

### 声明与启动/停止结果

头文件 `<linux/kthread.h>`：

```c
struct task_struct *kthread_create_on_node(int (*threadfn)(void *),
    void *data, int node, const char namefmt[], ...);
/* kthread_create(threadfn, data, namefmt, ...) 是宏 */
/* kthread_run(threadfn, data, namefmt, ...) 是创建并唤醒宏 */
bool kthread_should_stop(void);
int kthread_stop(struct task_struct *k);
```

Create成功给尚未进入threadfn的任务；run还唤醒。失败为ERR_PTR，不是NULL：`IS_ERR`、`PTR_ERR`处理。线程名格式串与data指针都按自己的约束使用，`data`只是借给线程，不转移任意对象的释放责任。

`kthread_stop`设置停止标志、唤醒并同步等退出，返回threadfn结果。Create后从未唤醒就stop，可使threadfn不运行并返回相应`-EINTR`。它不强杀任意死循环，必须检查stop并结束；不从线程自身调用stop，也不能持有退出路径需要的锁。

### 长期线程骨架

最小长期线程骨架，工作通过pending合并，私有业务队列仍需自己的同步：

```c
#include <linux/atomic.h>
#include <linux/err.h>
#include <linux/kthread.h>
#include <linux/wait.h>

struct service {
    wait_queue_head_t wait;
    atomic_t pending;
    struct task_struct *task;
};

static int service_thread(void *data)
{
    struct service *s = data;

    while (!kthread_should_stop()) {
        wait_event_idle(s->wait,
                        kthread_should_stop() || atomic_read(&s->pending));
        if (kthread_should_stop())
            break;
        if (atomic_xchg(&s->pending, 0)) {
            /* Drain published work under the service's own data protocol. */
        }
    }
    return 0;
}

static int service_start(struct service *s)
{
    struct task_struct *task;

    init_waitqueue_head(&s->wait);
    atomic_set(&s->pending, 0);
    s->task = NULL;
    task = kthread_run(service_thread, s, "sched-doc-service");
    if (IS_ERR(task))
        return PTR_ERR(task);
    s->task = task;
    return 0;
}

static void service_stop(struct service *s)
{
    if (!s->task)
        return;
    kthread_stop(s->task);
    s->task = NULL;
}
```

此例仅stop退出，不自发返回；`wait_event_idle`使用`TASK_IDLE`，不引入普通信号返回，也避免长期无事可做的线程被计入系统load average。它仍可由适用普通唤醒恢复，stop条件必须在等待表达式中。`TASK_IDLE`是等待状态，不是改变为`SCHED_IDLE`策略。

提交者发布工作后设置pending，再`wake_up(&s->wait)`；本RT骨架的提交者位于允许获取普通队列锁的上下文。pending只合并通知，不是工作计数，工作实体另有可靠队列。`kthread_should_stop()`在threadfn内查询当前kthread的标志，不是任意任务的外部状态查询器。

### 提交者停机与资源清理

Start/stop由同一管理协议串行调用。Stop前先禁止/排空新的提交和外部回调，再stop，再释放s及私有资源；运行中的业务必须有界或可取消。若threadfn会自行退出，则保存task指针的一方需要另设明确引用/退出协议，不能多年保存一个可能已释放的裸指针。

Stop只保证线程按协议退出，不自动处理完所有已经提交的业务。这份骨架在发现stop时直接退出；剩余队列项的取消和资源释放应由停机者承担。需要先排空业务再退出时，要另有明确的drain协议，不能把`kthread_stop`当成队列flush。

依据：`kernel/kthread.c` 的create、线程主循环及`:698`的stop。机制见 [任务存活](sub/core/01-task-rq.md#lifetime)。

<a id="wakeup"></a>

## 8. 直接唤醒：`wake_up_process`

```c
#include <linux/sched.h>
int wake_up_process(struct task_struct *tsk);
```

本地普通导出。调用者须持有有效任务及可用生命周期协议；可在相应不允许睡眠的现场发起唤醒，但它仍执行锁/状态交接，不能当无成本函数。

1表示本次匹配普通等待状态并作相应唤醒，0表示没有相应唤醒；已经runnable时0也可能正常。不返回目标运行完成，不取得目标结果，不通知任意停止/跟踪状态。

在PREEMPT_RT的保存状态分支中，1也可能表示普通唤醒已记录到`saved_state`，目标仍需等待锁唤醒。不能从返回1就推断目标已经重新入rq或已得到锁，见 [RT保存状态](sub/synchronization/01-pi-preempt.md#saved)。
适合create后启动专用线程、与任务持有协议结合的内部机制。一般数据等待使用waitqueue，完成阶段使用completion，避免手工维护条件与裸任务指针。

依据：`core.c:4474`包装与`try_to_wake_up`；[唤醒分支](sub/core/02-wait-wakeup.md#ttwu)。

<a id="kernel-affinity"></a>

## 9. 内核 CPU 约束与新线程绑定

### 声明、上下文与结果

```c
#include <linux/cpumask.h>
#include <linux/kthread.h>
#include <linux/sched.h>

int set_cpus_allowed_ptr(struct task_struct *p,
                         const struct cpumask *new_mask);
void kthread_bind(struct task_struct *k, unsigned int cpu);
```

`set_cpus_allowed_ptr`为GPL导出，借用输入掩码，不把该地址变成由调用者长期托管的任务掩码指针。调用可能等待迁移完成，要求task存活、非原子可睡眠上下文，不能持spinlock调用。

返回0或负errno，如非法CPU范围/无有效目标`-EINVAL`、适用migration-disabled冲突`-EBUSY`。配置为非SMP时有对应inline行为，不能把SMP迁移结论推广到它。

### 新线程绑定与失败清理

`kthread_bind`是普通导出void，用于刚create、尚未唤醒且满足其inactive前提的线程；CPU必须possible，但可暂未online。它设置绑定掩码及`PF_NO_SETAFFINITY`，不等于单凭此调用就建立完整per-CPU线程生命周期；热插拔仍需管理。不能当任意运行中线程的同义affinity setter。

需要先设掩码再运行，可以使用：create → 检查ERR_PTR → `set_cpus_allowed_ptr`检查结果 → 成功后`wake_up_process`。若设掩码失败，对尚未启动任务调用stop再清理data，不能启动一半后释放data。

不要将用户态 `cpu_set_t` 地址传入内核cpumask API，也不要无引用查到task后跨阻塞调用。依据：`core.c:2996`附近调用注释、`:3015`实现；`kernel/kthread.c`的bind。

<a id="kernel-policy"></a>

## 10. 内核策略辅助接口

### 声明与结果

```c
#include <linux/sched.h>
#include <linux/sched/types.h>

void sched_set_normal(struct task_struct *p, int nice);
void sched_set_fifo_low(struct task_struct *p);
void sched_set_fifo(struct task_struct *p);
int sched_setattr_nocheck(struct task_struct *p,
                         const struct sched_attr *attr);
```

本地均为GPL导出。Normal设置相应普通策略和nice；fifo_low使用用户RT优先级1，fifo使用本地`MAX_RT_PRIO/2`。后二者是受管理内核用途的辅助选择，不是让任意工作自动变快的建议。

前三个返回void，内部对失败WARN；不能写成`ret=sched_set_fifo(p)`再检查。需要明确处理设置错误的内核用途，可比较`nocheck`：它绕过用户权限检查，不绕过全部合法性、同步和准入，返回0或负errno。

### 上下文与恢复前提

要求稳定task和适用上下文；这些操作会触发PI/队列和可能的cpuset等管理，不能从中断/关IRQ或持有不允许阻塞的锁现场调用。使用前确认优先级规划和恢复协议；不借nocheck规避系统资源安全设计。

依据：`core.c:7942`、`:7952`、`:7959`及`__sched_setscheduler`。内核同名`sched_setattr(task, attr)`与用户ABI参数不同，且头文件声明不自动表示同样导出。

<a id="delay"></a>

## 11. 延迟等待与主动让出

```c
#include <linux/sched.h>
long schedule_timeout_interruptible(long timeout);
long schedule_timeout_uninterruptible(long timeout);
/* cond_resched() 是适用配置下的包装/宏入口 */
```

Timeout以jiffies计，返回未用完时间，0常表示到期；不是`-ETIMEDOUT`或毫秒。包装会设置相应任务状态再进入timeout，不能把裸`schedule_timeout`当作一定睡眠的普通延迟函数。

在等待条件时优先用`wait_event*_timeout`，否则仍要自行解决条件重检和唤醒竞态。睡眠延迟不保证精确唤醒时刻，唤醒后仍等待调度。时间API的hres或更精确延迟属于相邻时间子系统契约。

`cond_resched`给适用长期可睡眠循环检查/让出机会，受配置和上下文影响，不是同步屏障，也不保证每次切换。它不能使任意raw锁临界区变成可睡眠区域；持锁专用变体要按相应锁协议另查。

用户`sched_yield`则按策略主动让出，不应用于替代条件等待或实现通用busy-poll。FIFO/RR只改变适用同级关系，也不保证较低级马上执行。

<a id="cgroup"></a>

## 12. Cgroup v2 CPU 接口

### 作用范围与文件格式

这是文件ABI，不是C函数。控制器、委派、层级、根/非根组和权限决定文件是否可用；本页不自动创建或修改系统组。

`cpu.weight`、`cpu.max`和`cpu.max.burst`管理本地公平类竞争/带宽，不给FIFO/RR或Deadline任务设置同一份CFS配额。`cpu.stat`的基础使用时间又可以包含其他策略的任务，基础使用量与CFS限流统计不是同一个对象口径。

| 文件 | 格式/单位 | 用法与边界 |
| --- | --- | --- |
| `cpu.weight` | 整数1～10000 | 同层公平权重，目标默认100；不是固定份额预约 |
| `cpu.max` | `MAX PERIOD`，微秒；MAX可为`max` | 聚合CPU上限；单数字更新MAX；祖先仍限制 |
| `cpu.max.burst` | 微秒，受配额约束 | 允许限定积累突发，非每周期自动附加 |
| `cpu.stat` | key-value；时间微秒，次数为计数 | 同一组两次采样取增量，不混v1纳秒字段 |
| `cpu.uclamp.min/max` | 百分比文本；max可为`max` | 与任务0～1024结构标度不同，不限CPU时间 |
| `cpu.pressure` | PSI格式 | 描述适用资源停顿，非CPU利用率 |

例如文本 `20000 100000` 表达每100000微秒周期最多20000微秒适用执行额度，可由多个CPU共同消费。写入成功不保证业务得到最低执行量，也不解除父组上限。

### 错误、回读与恢复

读写使用普通文件错误处理，检查打开/完整写入/关闭以及实际回读；权限、控制器未启用、非法参数、层级条件可能使操作失败。保存原值并恢复也是可能失败的操作，不能在示例中默默改全机设置。

机制见 [CFS 带宽](sub/fair/02-bandwidth.md)，统计解释见 [观察篇](sub/observation/01-observe.md#limits)。V2的RT管理限制需结合`CONFIG_RT_GROUP_SCHED`等，不将v1的RT文件名搬到本表。

接口与示例的核对范围见 [依据附录](99-evidence.md#api)。这里完成静态契约核对，不代表目标工具链编译、驱动加载或设备运行验证。
