# kthread — 内核线程创建 API

> kthread 提供创建和管理内核线程的标准接口。内核线程运行在进程上下文但没有用户空间地址空间，用于需要持续运行的后台任务（如 per-CPU 工作线程、设备管理线程、文件系统日志刷新）。

---

## kthread

### 定位

**功能**：提供创建和管理内核线程的标准接口。内核线程运行在进程上下文但没有用户空间地址空间，用于需要持续运行的后台任务（如 per-CPU 工作线程、设备管理线程、文件系统日志刷新、加密子系统）。

**实现方式**：内部 `struct kthread`（`kernel/kthread.c:52-65`）封装 `threadfn`（线程函数）、`cpu`（绑定 CPU）、`flags`（`KTHREAD_SHOULD_STOP`/`KTHREAD_SHOULD_PARK` 等标志位）、`parked`/`exited` 完成信号。`kthread_run` = `kthread_create` + `wake_up_process`（最常用模式）。停止协议：调用者设 `KTHREAD_SHOULD_STOP` 并唤醒线程，线程函数通过 `kthread_should_stop()` 轮询检查后退出。暂停机制通过 `KTHREAD_SHOULD_PARK` + `kthread_parkme()` 实现，用于 CPU 热插拔等场景。实现在 `kernel/kthread.c`。

**应用场景**：全树广泛使用——`kthread_should_stop` 471 个文件、`kthread_stop` 390 个文件、`kthread_run` 305 个文件。典型用户包括加密子系统（`crypto/algboss.c`、`crypto/crypto_engine.c`）、网络（`net/sunrpc/svc.c`、`net/core/dev.c`）、IPVS 负载均衡（`net/netfilter/ipvs/ip_vs_sync.c`）、KUnit 测试框架（`lib/kunit/try-catch.c`）等。

内部结构体（`kernel/kthread.c:52-65`）：

```c
struct kthread {
    unsigned long flags;
    unsigned int cpu;                // 绑定 CPU
    int (*threadfn)(void *);         // 线程函数
    void *data;                      // 传递给线程函数的数据
    struct completion parked;        // 暂停完成
    struct completion exited;        // 退出完成
    char *full_name;                 // 线程名称
};
```

**标志位**（`kernel/kthread.c:67-71`）：

```c
enum KTHREAD_BITS {
    KTHREAD_IS_PER_CPU = 0,
    KTHREAD_SHOULD_STOP,
    KTHREAD_SHOULD_PARK,
};
```

### kthread_create vs kthread_run

**`kthread_create`**：创建线程但**不唤醒**。线程以 `TASK_UNINTERRUPTIBLE` 状态启动，调用者必须显式调用 `wake_up_process()`。适用于需要在启动前做额外设置（如绑定 CPU、设置调度策略）的场景。

**`kthread_run`**（`include/linux/kthread.h:51-58`）：

```c
#define kthread_run(threadfn, data, namefmt, ...) \
({ \
    struct task_struct *__k = kthread_create(threadfn, data, namefmt, ##__VA_ARGS__); \
    if (!IS_ERR(__k)) wake_up_process(__k); \
    __k; \
})
```

创建并立即唤醒——最常用的模式。

### 停止协议

线程函数内部通过 `kthread_should_stop()` 检查停止请求：

```c
int my_thread_fn(void *data)
{
    while (!kthread_should_stop()) {
        // 做工作...
    }
    return 0;  // 退出
}
```

调用者通过 `kthread_stop(task)` 请求停止并等待线程退出。

### 暂停机制

暂停允许暂时挂起线程而不终止它（用于 CPU 热插拔等场景）：

```c
// 在线程函数中
while (!kthread_should_stop()) {
    if (kthread_should_park()) {
        // 清理
        kthread_parkme();  // 阻塞直到被 unpark
        // 重新初始化
    }
    // 做工作
}
```

`kthread_park()` 设置 `KTHREAD_SHOULD_PARK` 位，唤醒线程，等待 `parked` 完成信号。`kthread_unpark()` 清除标志并唤醒线程。

### 主要 API

头文件：`include/linux/kthread.h`；实现：`kernel/kthread.c`

| API | 说明 | 备注 |
|-----|------|------|
| `kthread_run(threadfn, data, namefmt, ...)` | 创建并启动 | 最常用 |
| `kthread_create(threadfn, data, namefmt, ...)` | 创建但不唤醒 | 需手动 `wake_up_process()` |
| `kthread_create_on_node(threadfn, data, node, namefmt, ...)` | 指定 NUMA 节点创建 | |
| `kthread_stop(task)` | 请求停止并等待 | 返回线程函数的返回值 |
| `kthread_should_stop()` | 检查停止请求 | 在线程循环中使用 |
| `kthread_park(task)` | 暂停线程 | 等待 park 完成 |
| `kthread_unpark(task)` | 恢复线程 | |
| `kthread_should_park()` | 检查暂停请求 | 在线程循环中使用 |
| `kthread_parkme()` | 线程自暂停 | 阻塞直到 unpark |
| `kthread_bind(task, cpu)` | 绑定到 CPU | 必须在启动前调用 |
| `kthread_freezable_should_stop(was_frozen)` | 可冻结的停止检查 | 支持系统挂起 |

### 使用规模

| API | 使用文件数 |
|-----|-----------|
| `kthread_should_stop` | 471 |
| `kthread_stop` | 390 |
| `kthread_create_on_node` | 24 |
| `kthread_run` | 305 |
| `kthread_create` | 60 |
| `kthread_park` | 23 |
| `kthread_unpark` | 19 |
