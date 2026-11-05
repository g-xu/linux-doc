## timer
Linux 内核的时间管理和定时功能是内核运转的基石。无论是进程调度、TCP 超时重传，还是驱动程序的延时操作，都离不开它。

Linux 内核中的定时机制主要分为三大类：
1.  **延时机制 (Delays)**：忙等待或睡眠等待（不使用回调）。
2.  **低精度定时器 (Legacy/Standard Timer)**：基于时间轮，用于一般的超时处理。
3.  **高精度定时器 (HRTimer)**：基于红黑树，用于纳秒级精度的场景。

---

### 1. 时间的基础：Jiffies 与 HZ

在介绍定时器之前，必须理解内核的时间基准。

*   **HZ**：内核配置的每秒时钟中断次数（节拍率）。
    *   常见值：100, 250, 1000。
    *   如果 HZ=1000，则每 1ms 产生一次时钟中断。
*   **Jiffies**：一个全局变量（`unsigned long`），记录了系统启动以来产生的节拍总数。
    *   `jiffies` 是低精度定时器的基准。
    *   **时间比较宏**：为了处理计数器溢出回绕问题，必须使用内核提供的宏：
        ```c
        time_after(a, b)   // a > b ?
        time_before(a, b)  // a < b ?
        ```

---

### 2. 延时机制 (Delay Functions)

这部分不是“定时器”（到点回调），而是让代码执行“暂停”一会儿。根据是否释放 CPU，分为两类：

#### 2.1 忙等待 (Busy Wait)
**特点**：**占用 CPU 空转**，不发生上下文切换。
**适用场景**：**原子上下文**（中断、自旋锁内），或者延时极短（< 1ms）。

| API | 描述 | 原理 |
| :--- | :--- | :--- |
| `ndelay(ns)` | 纳秒级延时 | 循环指令 |
| `udelay(us)` | 微秒级延时 | 循环指令 |
| `mdelay(ms)` | 毫秒级延时 | 基于 udelay 实现 (**慎用**，长时间占 CPU 不调度是恶习) |

#### 2.2 睡眠等待 (Sleep Wait)
**特点**：**让出 CPU**，进入睡眠状态，由调度器在时间到后唤醒。
**适用场景**：**进程上下文**，延时较长（> 1ms）。

| API | 描述 | 上下文要求 |
| :--- | :--- | :--- |
| `msleep(ms)` | 不可中断睡眠 (`TASK_UNINTERRUPTIBLE`) | 进程上下文 |
| `msleep_interruptible(ms)` | 可被信号中断 | 进程上下文 |
| `usleep_range(min, max)` | 微秒级睡眠 (**推荐**) | 进程上下文 (比 udelay 友好，允许内核合并唤醒以省电) |

---

### 3. 标准定时器 (Standard Timer / Timer Wheel)

这是 Linux 最经典的定时器机制，基于 `struct timer_list`。

#### 3.1 核心特点
*   **精度**：**低精度**。依赖于 HZ（通常是 ms 级别）。
*   **执行上下文**：**软中断上下文 (TIMER_SOFTIRQ)**。
    *   **严禁睡眠**。
    *   可以使用 `spinlock`，但不能使用 `mutex`。
    *   同一 CPU 上的定时器串行执行。
*   **实现原理**：**时间轮 (Timer Wheel)** 算法。通过多级哈希桶（级联数组）实现 $O(1)$ 的插入和删除，性能极高。



---

### 4. 高精度定时器 (HRTimer)

随着多媒体和实时应用的需求，jiffies 的精度不够用了，Linux 引入了 **High-Resolution Timer**。

#### 4.1 核心特点
*   **精度**：**纳秒级 (ns)**。不依赖 jiffies，而是依赖硬件的高精度时钟源（如 TSC, HPET）。
*   **数据结构**：**红黑树 (Red-Black Tree)**。按过期时间排序。
*   **执行上下文**：通常在 **硬中断上下文** 中执行（为了保证即时性），但也支持配置为在软中断中运行。
    *   **注意**：在硬中断模式下，回调函数必须执行得非常快，严禁任何形式的阻塞。

---

### 5. 对比与总结

| 特性 | **Standard Timer (`timer_list`)** | **HRTimer (`hrtimer`)** | **Delay (`udelay`/`msleep`)** |
| :--- | :--- | :--- | :--- |
| **用途** | 通用超时处理 (TCP超时, 驱动IO超时) | 实时任务, 多媒体同步, 极短周期的任务 | 简单的代码暂停 |
| **精度** | HZ (通常 1ms - 10ms) | 纳秒 (ns) | 取决于具体函数 |
| **底层实现** | 时间轮 (Timer Wheel) | 红黑树 (RB-Tree) | 忙循环 或 调度睡眠 |
| **执行上下文** | **软中断** (Softirq) | 通常 **硬中断** (Hardirq) | 调用者的上下文 |
| **能否睡眠** | **否** | **否** | `msleep`可, `udelay`不可 |
| **开销** | 极低 ($O(1)$) | 中等 ($O(\log N)$) | - |
| **阻塞性** | 异步回调 (不阻塞当前流) | 异步回调 (不阻塞当前流) | **同步阻塞** |

### 6. 开发中的最佳实践

1.  **能用 msleep 就别用 udelay**：
    *   在进程上下文中，如果你需要延时 5ms，用 `udelay(5000)` 会白白浪费 CPU 5ms 的算力，用 `msleep(5)` 则会把 CPU 让给别人。
    *   **例外**：如果延时只有 10us，用 `usleep_range` 或 `udelay`，因为 `msleep` 涉及调度，开销可能超过 10us。

2.  **标准定时器足够应付 99% 的场景**：
    *   不要盲目追求高精度。红黑树的操作开销比时间轮大。除非你有明确的“准时性”要求（误差不能超过几十微秒），否则请使用 `timer_list`。

3.  **并发安全**：
    *   在使用 `del_timer` 时要非常小心竞态。
    *   如果模块要卸载，或者结构体要释放，**必须使用 `del_timer_sync()`**，否则可能出现“结构体释放了，但定时器刚好在另一个 CPU 上执行回调访问该结构体”的 **UAF (Use-After-Free)** 崩溃。

4.  **Timer 上下文锁的选择**：
    *   因为 Timer 回调在软中断（或硬中断）中运行，所以保护共享数据时：
        *   **必须用 `spin_lock_bh` 或 `spin_lock_irqsave`**。
        *   **绝对不能用 `mutex`**。

## APIs
以下是 Linux 内核中 **Jiffies（时间基准）** 和 **Standard Timer（低精度定时器）** 主要 API 的功能对照表。

在使用之前，请确保包含以下头文件：
```c
#include <linux/jiffies.h>
#include <linux/timer.h>
```

### 1. Jiffies 与 时间转换 API
`jiffies` 是内核的全局变量，记录了系统启动以来的时钟节拍数。由于它会溢出（回绕），必须使用特定的宏进行比较。

| API / 宏 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| **全局变量** | | |
| `jiffies` | `unsigned long` 类型的全局变量，记录当前节拍数。 | 32位系统上约 49.7 天溢出一次。**不要直接用 `>` 或 `<` 比较**。 |
| `jiffies_64` | `u64` 类型的全局变量，即使在 32 位系统上也不会溢出。 | 访问它可能需要特殊锁（在 32 位系统上），通常用于统计。 |
| **时间比较** | **(处理溢出回绕的关键宏)** | **如果不使用这些宏，系统运行 49 天后可能会崩溃。** |
| `time_after(a, b)` | 判断时间点 `a` 是否在 `b` **之后** (a > b)。 | 返回 true/false。通常用法：`time_after(jiffies, timeout)`。 |
| `time_before(a, b)` | 判断时间点 `a` 是否在 `b` **之前** (a < b)。 | 返回 true/false。 |
| `time_after_eq(a, b)` | 判断 `a >= b`。 | |
| `time_before_eq(a, b)` | 判断 `a <= b`。 | |
| **时间转换** | **(人类时间 &lt;-&gt; 节拍数)** | 依赖内核配置 `HZ`。 |
| `msecs_to_jiffies(ms)` | 将毫秒 (ms) 转换为 jiffies 数。 | 最常用的宏。例如设置 1 秒超时：`jiffies + msecs_to_jiffies(1000)`。 |
| `usecs_to_jiffies(us)` | 将微秒 (us) 转换为 jiffies 数。 | |
| `jiffies_to_msecs(j)` | 将 jiffies 数转换为毫秒。 | 用于日志打印或统计。 |
| `jiffies_to_usecs(j)` | 将 jiffies 数转换为微秒。 | |

---

### 2. Timer List (标准定时器) API
标准定时器基于时间轮实现，运行在 **软中断上下文 (Softirq)**。

| API 分类 | API 函数 / 宏 | 功能描述 | 备注 / 返回值 |
| :--- | :--- | :--- | :--- |
| **声明与初始化** | `struct timer_list` | 定时器结构体。 | **不要手动初始化内部成员**，请使用 helper 函数。 |
| | `timer_setup(timer, callback, flags)` | **初始化**定时器（Linux 4.14+ 新标准）。 | `timer`: 指针<br>`callback`: 回调函数 `void (*)(struct timer_list *)`<br>`flags`: 通常为 0。 |
| | `DEFINE_TIMER(name, callback)` | **静态**定义并初始化一个定时器。 | 适用于全局变量场景。 |
| **启动与修改** | `mod_timer(timer, expires)` | **启动**定时器 或 **修改**已激活定时器的过期时间。 | **最常用的 API**。<br>1. 如果定时器未激活，则激活它。<br>2. 如果已激活，则更新过期时间。<br>3. `expires` 是绝对时间 (如 `jiffies + 100`)。 |
| | `add_timer(timer)` | 启动定时器（使用结构体中现有的 `expires`）。 | 通常直接用 `mod_timer` 替代此函数，因为它更安全且通用。 |
| **停止与删除** | `del_timer(timer)` | **尝试删除**定时器。 | **不等待**回调函数结束。<br>返回 1：定时器被激活且被成功移除。<br>返回 0：定时器未激活（可能已经执行完了）。 |
| | `del_timer_sync(timer)` | **同步删除**定时器。 | **推荐使用**。<br>如果定时器回调正在另一个 CPU 上运行，此函数会**忙等待**直到回调结束。<br>**注意：**不能在中断上下文或持有回调所需锁的情况下调用（防止死锁）。 |
| **状态查询** | `timer_pending(timer)` | 检查定时器是否处于“挂起”状态（已加入队列且未过期）。 | 返回 1 表示正在排队等待执行，0 表示未激活。 |

---

### 3. 常见延时 (Delay/Sleep) API 对照
虽然不属于 `timer_list`，但经常配合时间使用。

| API | 类型 | 适用上下文 | 是否释放 CPU? | 场景 |
| :--- | :--- | :--- | :--- | :--- |
| `ndelay(ns)` | 忙等待 | **原子上下文** (中断/自旋锁) | **否** (空转) | 纳秒级极短延时 |
| `udelay(us)` | 忙等待 | **原子上下文** | **否** (空转) | 微秒级短延时 |
| `mdelay(ms)` | 忙等待 | 原子上下文 | **否** (空转) | **极度不推荐** (除非必须在中断中延时毫秒级) |
| `usleep_range(min, max)`| 睡眠 | **进程上下文** | **是** (睡眠) | 微秒级延时 (推荐)，允许内核合并唤醒以省电 |
| `msleep(ms)` | 睡眠 | **进程上下文** | **是** (睡眠) | 毫秒级延时，不可中断 |
| `msleep_interruptible`| 睡眠 | **进程上下文** | **是** (睡眠) | 毫秒级，可被信号打断 |
### 4.HRTimer 主要 API 列表
在使用这些 API 之前，请确保已包含头文件：
```c
#include <linux/hrtimer.h>
#include <linux/ktime.h>
```

| API 分类 | API 函数 / 宏 / 枚举 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- | :--- |
| **声明与初始化** | `struct hrtimer` | 高精度定时器核心结构体。 | 包含在 `hrtimer_init` 后必须手动设置 `.function` 成员。 |
| | `hrtimer_init(timer, clock_id, mode)` | **初始化**定时器。 | **clock_id**: 通常用 `CLOCK_MONOTONIC` (单调递增，不受改时间影响) 或 `CLOCK_REALTIME` (墙上时间)。<br>**mode**: `HRTIMER_MODE_REL` (相对时间) 或 `HRTIMER_MODE_ABS` (绝对时间)。 |
| **启动定时器** | `hrtimer_start(timer, time, mode)` | **启动**或**重启**定时器。 | **time**: `ktime_t` 类型的时间。<br>**mode**: 在此处可重新指定是相对还是绝对时间。<br>如果定时器已激活，会先移除再重新添加。 |
| | `hrtimer_start_range_ns(timer, time, delta, mode)` | 启动定时器，但允许一定的**偏差范围 (slack)**。 | **推荐用于非极度精确场景**。<br>`delta` (纳秒) 是允许内核推迟执行的最大范围，有助于内核合并中断，**降低功耗**。 |
| **停止/取消** | `hrtimer_cancel(timer)` | **取消**定时器。 | **同步操作**。<br>1. 如果定时器未运行，直接取消。<br>2. 如果回调函数正在另一个 CPU 上运行，会**忙等待**直到回调结束。<br>**返回 1**: 成功取消了挂起的定时器。<br>**返回 0**: 定时器未激活或正在运行。 |
| | `hrtimer_try_to_cancel(timer)` | **尝试取消**定时器。 | **非阻塞**。<br>如果回调函数正在运行，**立即返回 -1**，不会等待。<br>常用于中断上下文。 |
| **状态查询** | `hrtimer_active(timer)` | 检查定时器是否处于激活状态（挂在红黑树上）。 | 仅检查是否排队，不代表回调是否正在执行。 |
| **回调函数**<br>(Callback) | `enum hrtimer_restart` | 回调函数的返回值类型。 | 回调原型：`enum hrtimer_restart (*function)(struct hrtimer *)` |
| | `HRTIMER_NORESTART` | **不重启**。 | 表示这是单次定时器，执行完即止。 |
| | `HRTIMER_RESTART` | **重启**。 | 表示这是周期定时器，内核会再次将定时器加入队列。**注意：**必须在返回前更新过期时间。 |
| **时间操作**<br>(ktime_t) | `ktime_set(secs, nsecs)` | 将秒和纳秒转换为 `ktime_t` 格式。 | 构建时间参数的最常用辅助函数。 |
| | `hrtimer_forward_now(timer, interval)` | **更新过期时间**到当前时间之后。 | **周期性定时器的核心函数**。<br>通常在回调函数中调用，用于将过期时间推进一个 `interval` 周期。 |
| | `ktime_get()` | 获取当前系统的高精度单调时间。 | 返回 `ktime_t`。 |

---

### 重点概念补充

#### 1. 执行上下文 (Hard IRQ)
与标准定时器 (`timer_list` 运行在软中断) 不同，**HRTimer 的回调函数默认是在硬中断 (Hard IRQ) 上下文中执行的**。
*   **严禁睡眠**：不能调用 `msleep`, `mutex_lock`, `kmalloc(GFP_KERNEL)` 等。
*   **极速执行**：回调代码必须极其精简，否则会阻塞该 CPU 的其他中断响应。
*   *注：虽然可以通过标志位将其配置为在软中断运行，但绝大多数驱动使用的是默认的硬中断模式。*

#### 2. 实现周期性定时器 (Periodic Timer)
HRTimer 没有专门的 `mod_timer` 来自动处理周期性，需要在回调函数中手动“推进”时间。

**标准写法：**
```c
/* 回调函数 */
enum hrtimer_restart my_callback(struct hrtimer *timer)
{
    /* 1. 处理业务逻辑 (要快!) */
    // do_something();

    /* 2. 设置周期时间 (例如 1ms) */
    ktime_t interval = ktime_set(0, 1000000);

    /* 3. 将过期时间向前推进一个周期 */
    /* hrtimer_forward_now 会处理“如果是上上个周期触发的，自动补齐到下一个未来时间点” */
    hrtimer_forward_now(timer, interval);

    /* 4. 返回 RESTART 告诉内核重新加入队列 */
    return HRTIMER_RESTART;
}
```
#### 3. 时钟源选择 (`CLOCK_MONOTONIC` vs `CLOCK_REALTIME`)
*   **`CLOCK_MONOTONIC` (推荐)**：系统启动后单调递增的时间。即使管理员修改了系统时间（如 NTP 同步），定时器也不会乱跳。
*   **`CLOCK_REALTIME`**：墙上时间。如果系统时间被修改（例如从 12:00 改成 11:00），可能会导致定时器触发逻辑异常（例如多等 1 小时），或者立即触发。**驱动开发一般不使用此项。**

### 5. 关键注意事项

1.  **软中断上下文**：
    *   `timer_list` 的回调函数是在 **软中断 (TIMER_SOFTIRQ)** 中执行的。
    *   这意味着回调函数中 **严禁睡眠**（不能调用 `msleep`, `mutex_lock` 等）。
    *   如果需要加锁，必须使用 `spin_lock_bh` 或 `spin_lock_irqsave`（不能用普通的 `spin_lock`，否则可能被软中断打断导致死锁，也不能用 `mutex`）。

2.  **并发安全**：
    *   在卸载模块或释放包含 `timer_list` 的结构体内存之前，务必调用 **`del_timer_sync()`**。如果只调用 `del_timer()`，可能出现“模块已经卸载，代码内存已释放，但定时器回调刚好在另一个 CPU 上执行”的严重崩溃 (Oops)。

3.  **Jiffies 回绕**：
    *   永远不要写 `if (jiffies > timeout)`，因为当 `jiffies` 溢出（从最大值变回 0）时，逻辑会出错。必须使用 `if (time_after(jiffies, timeout))`。