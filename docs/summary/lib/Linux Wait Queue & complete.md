## 参考
Linux-Wait-Queue-等待队列
Linux-中的-waitqueue-机制详解 这个文章比较详细的梳理了 wait/wake_up相关内容，但是接口很多已经过时了

## 进程状态：
参考 linux-中进程的状态

| 进程状态                 | 说明                            |
| -------------------- | ----------------------------- |
| TASK_RUNNING         | 可运行状态。未必正在使用CPU，也许是在等待调度      |
| TASK_INTERRUPTIBLE   | 可中断的睡眠状态。正在等待某个条件满足           |
| TASK_UNINTERRUPTIBLE | 不可中断的睡眠状态。不会被信号中断             |
| __TASK_STOPPED       | 暂停状态。收到某种信号，运行被停止             |
| __TASK_TRACED        | 被跟踪状态。进程停止，被另一个进程跟踪           |
| EXIT_ZOMBIE          | 僵尸状态。进程已经退出，但尚未被父进程或者init进程收尸 |
| EXIT_DEAD            | 真正的死亡状态                       |

Linux的进程有两种睡眠状态：TASK_INTERRUPTIBLE和TASK_UNINTERRUPTIBLE，这两种状态的区别是能否响应收到的信号。处于TASK_INTERRUPTIBLE状态的进程遇到下面两种情况会返回到TASK_RUNNING状态：

- ==等待条件满足；==
- ==收到未被屏蔽的信号==。

收到信号时，会返回EINTR，需要检测返回值以作出正确处理。==对于TASK_UNINTERRUPTIBLE，只有等待条件满足才有可能返回运行状态，任何信号都无法打断它。==如果这种状态的进程出错，无法杀死，只能重启。

TASK_UNINTERRUPTIBLE的存在是因为内核中某些处理是不能被打断的，比如read系统调用正在操作磁盘，就要用TASK_UNINTERRUPTIBLE将其保护起来以免受到打扰而陷入不可控的状态。

## waitqueue
### 等待唤醒整体流程
<!-- 图片缺失: 等待唤醒整体流程 -->
### 常用API

| API                                                    | 类似API                                                                                              | 描述                                                                                               |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| DECLARE_WAIT_QUEUE_HEAD                                |                                                                                                    | 静态初始化等待队列头                                                                                       |
| init_waitqueue_head                                    |                                                                                                    | 初始化等待队列头                                                                                         |
| DECLARE_WAITQUEUE                                      |                                                                                                    | 静态声明等待entry                                                                                      |
| init_wait_entry                                        |                                                                                                    | 初始化wait entry                                                                                    |
| init_wait                                              |                                                                                                    | 带自删除的wait entry                                                                                  |
| add_wait_queue<br>__add_wait_queue                     |                                                                                                    | 添加entry到等待队列                                                                                     |
| add_wait_queue_exclusive<br>__add_wait_queue_exclusive |                                                                                                    | 添加EXCLUSIVE类型entry到等待队列                                                                          |
| add_wait_queue_priority<br>                            |                                                                                                    | 添加优先级的entry                                                                                      |
| __remove_wait_queue                                    |                                                                                                    |                                                                                                  |
| ·`___wait_event`                                       | wait_event<br>io_wait_event<br>wait_event_cmd<br>wait_event_killable<br>wait_event_xxxx<br>        | 后面都是对`___wait_event`的不同封装，根据参数不同进行不同封装                                                           |
|                                                        |                                                                                                    |                                                                                                  |
| __wake_up                                              | wake_up<br>wake_up_nr<br>wake_up_all<br>wake_up_locked<br>wake_up_sync<br>wake_up_interruptible_xx | 后面这些都是对`__wake_up`的封装，而`__wake_up`又是调用 `__wake_up_common` / `__wake_up_common_lock`，根据参数不同进行不同封装 |
|                                                        |                                                                                                    |                                                                                                  |
- 等待队列的wait event 有很多种，按照进程将要进入的状态分为：
    - **INTERRUPTIBLE** 等待事件；
    - **UNINTERRUPTIBLE** 等待事件；
    - **KILLABLE** 等待事件；
    - **IDLE** 等待事件；
    - 
队列里面的entry类型排序：
^ priority entry > normal entry > exclusive entry $

以下是 Linux 内核模块中 **Waitqueue (等待队列)** 主要 API 的功能描述表。

在使用这些 API 之前，请确保已包含头文件：
```c
#include <linux/wait.h>
```

### Waitqueue 主要 API 列表

| API 分类 | API 函数 / 宏 | 功能描述 | 备注 / 返回值 |
| :--- | :--- | :--- | :--- |
| **声明与初始化** | `DECLARE_WAIT_QUEUE_HEAD(name)` | **静态**定义并初始化一个名为 `name` 的等待队列头。 | 用于全局变量或文件作用域变量。 |
| | `init_waitqueue_head(wait_queue_head_t *q)` | **动态**初始化一个等待队列头。 | 用于结构体成员的运行时初始化。 |
| **等待操作**<br>(消费者)<br><br>*会自动循环检查 condition* | `wait_event(wq, condition)` | 使进程进入 **不可中断睡眠** (`TASK_UNINTERRUPTIBLE`)，直到 `condition` 为真。 | **慎用**。无法被信号（如 Ctrl+C）打断。若硬件无响应会造成死锁（D状态进程）。 |
| | `wait_event_interruptible(wq, condition)` | 使进程进入 **可中断睡眠** (`TASK_INTERRUPTIBLE`)，直到 `condition` 为真或收到信号。 | **最常用**。<br>**返回 0**：成功（条件满足）。<br>**返回 -ERESTARTSYS**：被信号打断（需立即处理退出）。 |
| | `wait_event_timeout(wq, condition, timeout)` | **不可中断**等待，带超时限制。 | **返回 0**：超时且条件仍未满足。<br>**返回 >0**：条件满足，返回剩余的 jiffies 值。 |
| | `wait_event_interruptible_timeout(wq, condition, timeout)` | **可中断**等待，带超时限制。 | **返回 0**：超时。<br>**返回 >0**：条件满足。<br>**返回 -ERESTARTSYS**：被信号打断。 |
| | `wait_event_killable(wq, condition)` | 类似于 `wait_event`，但允许被**致命信号**（fatal signals，如 kill -9）中断。 | 防止产生无法杀死的僵尸进程，比 `wait_event` 更安全。 |
| **唤醒操作**<br>(生产者) | `wake_up(wait_queue_head_t *q)` | 唤醒队列上的进程（包括 `TASK_INTERRUPTIBLE` 和 `TASK_UNINTERRUPTIBLE`）。 | 通常唤醒所有非独占进程 + 1 个独占进程。 |
| | `wake_up_interruptible(wait_queue_head_t *q)` | **只唤醒**状态为 `TASK_INTERRUPTIBLE` 的进程。 | 对应 `wait_event_interruptible` 系列。无法唤醒不可中断睡眠的进程。 |
| | `wake_up_all(wait_queue_head_t *q)` | 唤醒队列上的**所有**进程（无论是否独占）。 | 此时忽略独占标志，所有人都被叫醒。 |
| **手动操作**<br>(底层/高级) | `DECLARE_WAITQUEUE(name, tsk)` | 定义一个名为 `name` 的等待队列项，并关联到任务 `tsk`（通常是 `current`）。 | 用于手动构建复杂的睡眠逻辑。 |
| | `add_wait_queue(q, wait)` | 将等待队列项 `wait` 加入到队列头 `q` 中。 | 设置为非独占等待。 |
| | `add_wait_queue_exclusive(q, wait)` | 将等待队列项 `wait` 加入到队列头 `q` 中，并设置 **独占标志**。 | 用于解决“惊群效应”。`wake_up` 只会唤醒第一个独占进程。 |
| | `remove_wait_queue(q, wait)` | 从队列头 `q` 中移除等待队列项 `wait`。 | 唤醒后必须调用，否则会导致链表损坏。 |

### 补充说明

1.  **condition 参数**：
    *   `wait_event` 系列宏中的 `condition` 是一个 C 语言表达式（例如 `ring_buffer_len > 0`）。
    *   宏内部包含了一个 `while` 循环。当进程被唤醒时，它会**再次检查** `condition`。如果条件不满足（即发生了**虚假唤醒**），进程会继续睡眠。这大大简化了驱动开发者的代码。

2.  **唤醒配对原则**：
    *   `wait_event` $\leftrightarrow$ `wake_up`
    *   `wait_event_interruptible` $\leftrightarrow$ `wake_up_interruptible` (也可以用 `wake_up`，但 `wake_up_interruptible` 效率略高，因为它跳过了不需要唤醒的进程)。

3.  **超时单位**：
    *   `timeout` 参数的单位是 **jiffies**。
    *   通常配合 `msecs_to_jiffies(1000)` (1秒) 或 `usecs_to_jiffies(500)` 使用。

## completion

Completion构建于 Linux 调度程序的等待队列和唤醒机制之上，其用法相对比较简单。它是基于一个简化版的waitqueue实现的，等待队列使用的都是swait.c里面的 swake_xxx

```
初始化：
	DECLARE_COMPLETION(work) 静态
	init_completion() 动态

等待：
	wait_for_completion
	wait_for_completion_io
	wait_for_completion_interruptible
	wait_for_completion_killable
	wait_for_completion_timeout
	。。。

触发：
	complete
	complete_all

检查：	
	completion_done
```

### completion vs semaphore
有两个理由想要使用完成操作而不是信号量。首先，多个线程可以等待完成操作，并且只需调用一次 `complete_all()` 即可释放所有线程。而让信号量唤醒未知数量的线程则更为复杂。
其次，如果等待线程要释放同步对象，并且使用了信号量，则存在竞争条件。也就是说，等待线程可能会在唤醒线程完成 `up()` 操作之前被唤醒并释放对象。对于完成操作，不存在这种竞争。

## kthread

### 常用API

| API                          | 类似API                                                  | 描述                                    |
| ---------------------------- | ------------------------------------------------------ | ------------------------------------- |
| kthread_create_on_node       | kthread_create<br>kthread_create_on_cpu<br>            | 创建内核线程，默认是不启动的，需要使用wake_up_proces()唤醒 |
| kthread_run                  | kthread_run_on_cpu                                     | 创建并唤醒线程                               |
| kthread_bind                 | kthread_bind_mask                                      | 绑定线程到指定cpu                            |
| kthread_stop<br>kthread_park | kthread_should_stop<br>__kthread_should_park           | 停止线程                                  |
| kthread_exit                 |                                                        | 内核线程退出                                |
| kthreadd                     |                                                        | 专门用来创建线程的线程，create是给它发消息              |
| __kthread_init_worker        | kthread_init_worker<br>DEFINE_KTHREAD_WORKER           |                                       |
| kthread_create_worker        |                                                        |                                       |
| kthread_queue_work           | kthread_queue_delayed_work<br>kthread_mod_delayed_work |                                       |
| kthread_flush_work           |                                                        |                                       |
| kthread_flush_worker         |                                                        |                                       |
目前看kthread worker用的的地方比较少，后续需要再看

以下是 Linux 内核模块中 **Waitqueue (等待队列)** 主要 API 的功能描述表。

在使用这些 API 之前，请确保已包含头文件：
```c
#include <linux/wait.h>
```

## Waitqueue 主要 API 列表

| API 分类 | API 函数 / 宏 | 功能描述 | 备注 / 返回值 |
| :--- | :--- | :--- | :--- |
| **声明与初始化** | `DECLARE_WAIT_QUEUE_HEAD(name)` | **静态**定义并初始化一个名为 `name` 的等待队列头。 | 用于全局变量或文件作用域变量。 |
| | `init_waitqueue_head(wait_queue_head_t *q)` | **动态**初始化一个等待队列头。 | 用于结构体成员的运行时初始化。 |
| **等待操作**<br>(消费者)<br><br>*会自动循环检查 condition* | `wait_event(wq, condition)` | 使进程进入 **不可中断睡眠** (`TASK_UNINTERRUPTIBLE`)，直到 `condition` 为真。 | **慎用**。无法被信号（如 Ctrl+C）打断。若硬件无响应会造成死锁（D状态进程）。 |
| | `wait_event_interruptible(wq, condition)` | 使进程进入 **可中断睡眠** (`TASK_INTERRUPTIBLE`)，直到 `condition` 为真或收到信号。 | **最常用**。<br>**返回 0**：成功（条件满足）。<br>**返回 -ERESTARTSYS**：被信号打断（需立即处理退出）。 |
| | `wait_event_timeout(wq, condition, timeout)` | **不可中断**等待，带超时限制。 | **返回 0**：超时且条件仍未满足。<br>**返回 >0**：条件满足，返回剩余的 jiffies 值。 |
| | `wait_event_interruptible_timeout(wq, condition, timeout)` | **可中断**等待，带超时限制。 | **返回 0**：超时。<br>**返回 >0**：条件满足。<br>**返回 -ERESTARTSYS**：被信号打断。 |
| | `wait_event_killable(wq, condition)` | 类似于 `wait_event`，但允许被**致命信号**（fatal signals，如 kill -9）中断。 | 防止产生无法杀死的僵尸进程，比 `wait_event` 更安全。 |
| **唤醒操作**<br>(生产者) | `wake_up(wait_queue_head_t *q)` | 唤醒队列上的进程（包括 `TASK_INTERRUPTIBLE` 和 `TASK_UNINTERRUPTIBLE`）。 | 通常唤醒所有非独占进程 + 1 个独占进程。 |
| | `wake_up_interruptible(wait_queue_head_t *q)` | **只唤醒**状态为 `TASK_INTERRUPTIBLE` 的进程。 | 对应 `wait_event_interruptible` 系列。无法唤醒不可中断睡眠的进程。 |
| | `wake_up_all(wait_queue_head_t *q)` | 唤醒队列上的**所有**进程（无论是否独占）。 | 此时忽略独占标志，所有人都被叫醒。 |
| **手动操作**<br>(底层/高级) | `DECLARE_WAITQUEUE(name, tsk)` | 定义一个名为 `name` 的等待队列项，并关联到任务 `tsk`（通常是 `current`）。 | 用于手动构建复杂的睡眠逻辑。 |
| | `add_wait_queue(q, wait)` | 将等待队列项 `wait` 加入到队列头 `q` 中。 | 设置为非独占等待。 |
| | `add_wait_queue_exclusive(q, wait)` | 将等待队列项 `wait` 加入到队列头 `q` 中，并设置 **独占标志**。 | 用于解决“惊群效应”。`wake_up` 只会唤醒第一个独占进程。 |
| | `remove_wait_queue(q, wait)` | 从队列头 `q` 中移除等待队列项 `wait`。 | 唤醒后必须调用，否则会导致链表损坏。 |

### 补充说明

1.  **condition 参数**：
    *   `wait_event` 系列宏中的 `condition` 是一个 C 语言表达式（例如 `ring_buffer_len > 0`）。
    *   宏内部包含了一个 `while` 循环。当进程被唤醒时，它会**再次检查** `condition`。如果条件不满足（即发生了**虚假唤醒**），进程会继续睡眠。这大大简化了驱动开发者的代码。

2.  **唤醒配对原则**：
    *   `wait_event` $\leftrightarrow$ `wake_up`
    *   `wait_event_interruptible` $\leftrightarrow$ `wake_up_interruptible` (也可以用 `wake_up`，但 `wake_up_interruptible` 效率略高，因为它跳过了不需要唤醒的进程)。

3.  **超时单位**：
    *   `timeout` 参数的单位是 **jiffies**。
    *   通常配合 `msecs_to_jiffies(1000)` (1秒) 或 `usecs_to_jiffies(500)` 使用。