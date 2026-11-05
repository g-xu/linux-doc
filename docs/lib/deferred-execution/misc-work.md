# delayed_work — 延迟工作队列

> delayed_work 将 work_struct 与 timer_list 组合：先启动定时器，到期后再将嵌入的 work 排入 workqueue。用于"延迟一段时间后在进程上下文执行"的场景。

---

## delayed_work

### 定位

**功能**：将 `work_struct` 与 `timer_list` 组合，实现"延迟一段时间后在进程上下文执行回调"的语义。初始化时绑定回调函数，调度时指定延迟时间，到期后回调在 workqueue 进程上下文中执行（可以睡眠、持锁）。

**实现方式**：`struct delayed_work`（`include/linux/workqueue.h:111-118`）嵌入 `struct work_struct work`（实际工作）、`struct timer_list timer`（延迟定时器）和 `struct workqueue_struct *wq`（目标 workqueue）。调度时 delay=0 直接调用 `__queue_work()`；delay>0 设置定时器，到期时由 `delayed_work_timer_fn`（`kernel/workqueue.c:1641-1647`）将嵌入的 work 排入 workqueue。取消通过 `cancel_delayed_work()`（非阻塞）或 `cancel_delayed_work_sync()`（阻塞等待完成）实现。`mod_delayed_work_on()` 可抓取已排队的工作并用新延迟重新排队。实现在 `kernel/workqueue.c`。

**应用场景**：全树广泛使用——`schedule_delayed_work` 514 个文件、`queue_delayed_work` 459 个文件、`cancel_delayed_work` 854 个文件。典型用户包括蓝牙子系统（`net/bluetooth/hci_event.c`、`mgmt.c`）、batman-adv 网状网络（`net/batman-adv/`）、Ceph 客户端（`net/ceph/mon_client.c`）、IPv6 组播（`net/ipv6/mcast.c`）、RDS 网络（`net/rds/`）等设备超时重试、连接保活、LED 闪烁等场景。

核心结构体（`include/linux/workqueue.h:111-118`）：

```c
struct delayed_work {
    struct work_struct work;           // 嵌入的工作
    struct timer_list timer;           // 延迟定时器
    struct workqueue_struct *wq;       // 目标 workqueue（定时器回调使用）
    int cpu;                           // 目标 CPU
};
```

**定时器回调**（`kernel/workqueue.c:1641-1647`）：

```c
void delayed_work_timer_fn(struct timer_list *t)
{
    struct delayed_work *dwork = from_timer(dwork, t, timer);
    __queue_work(dwork->cpu, dwork->wq, &dwork->work);
}
```

**调度路径**（`kernel/workqueue.c:1650-1690`）：
1. delay=0：直接调用 `__queue_work()` 排队
2. delay>0：设置定时器，到期时由 `delayed_work_timer_fn` 排队
3. 工作最终在 workqueue 进程上下文中执行（可睡眠）

**取消**（`kernel/workqueue.c:3287-3305`）：
- `cancel_delayed_work()`：非阻塞，返回 true 表示之前在队
- `cancel_delayed_work_sync()`：阻塞直到工作完成

**修改**（`kernel/workqueue.c:1731-1748`）：
- `mod_delayed_work_on()`：尝试抓取已排队的工作，用新延迟重新排队

### 主要 API

头文件：`include/linux/workqueue.h`；实现：`kernel/workqueue.c`

| API | 说明 | 备注 |
|-----|------|------|
| `INIT_DELAYED_WORK(dwork, func)` | 初始化 | |
| `INIT_DEFERRABLE_WORK(dwork, func)` | 初始化可延迟工作 | 节能：不唤醒空闲 CPU |
| `schedule_delayed_work(dwork, delay)` | 排到 system_wq | 514 个文件使用 |
| `queue_delayed_work(wq, dwork, delay)` | 排到指定 wq | |
| `cancel_delayed_work(dwork)` | 取消（不等待） | 返回是否之前在队 |
| `cancel_delayed_work_sync(dwork)` | 取消并等待完成 | |
| `mod_delayed_work(wq, dwork, delay)` | 修改延迟或立即排队 | |
| `flush_delayed_work(dwork)` | 立即执行 | 等待完成 |

### 使用规模

`schedule_delayed_work` 514 个文件、`queue_delayed_work` 459 个文件、`cancel_delayed_work` 854 个文件、`mod_delayed_work` 135 个文件、`flush_delayed_work` 72 个文件。
