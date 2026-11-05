# RCU 杂项 — SRCU

> Sleepable RCU。允许读者在任意上下文（包括可睡眠上下文）中持有读锁。代价是 `synchronize_srcu` 比 `synchronize_rcu` 更重。用于设备驱动中需要跨睡眠操作保护的读侧遍历。

---

## SRCU

### 定位

**功能**：可睡眠的 RCU 变体——读者可以在读侧临界区内调用可能睡眠的函数（如 `mutex_lock`、`kmalloc(GFP_KERNEL)`、`copy_to_user`）。标准 RCU 要求读侧不可睡眠（PREEMPT_RT 除外），SRCU 解除了这一限制。

**实现方式**：每个 `struct srcu_struct` 拥有独立的 per-CPU 计数器（双缓冲 `srcu_lock_count[2]`）和 combining tree（`srcu_node`，与 RCU 的树形结构类似）。`synchronize_srcu` 通过主动轮询所有 CPU 的读计数归零来等待宽限期，不依赖上下文切换检测。每个 `srcu_struct` 独立管理，不同保护对象使用不同的实例。实现在 `kernel/rcu/srcutree.c`（1,854 行）。

**应用场景**：设备驱动中跨 I/O 操作的读侧保护、KVM 中跨 vCPU 运行的保护（`virt/kvm/kvm_main.c`、`virt/kvm/eventfd.c`）、安全模块（`security/tomoyo/`）、MMU notifier（`mm/mmu_notifier.c`）、KASAN 隔离区（`mm/kasan/quarantine.c`）、网络 netpoll（`net/core/netpoll.c`）等。全树 `srcu_read_lock` 约 200+ 处。

### 核心结构

`include/linux/srcutree.h:62-92`：

```c
struct srcu_struct {
    struct srcu_node node[];       // combining tree（与 RCU 类似）
    struct srcu_node *level[];     // 树层级指针
    unsigned int srcu_size_state;  // 树扩展状态
    // per-CPU data: srcu_data
    //   per_cpu_ptr(ss->sda, cpu)->srcu_lock_count[2]  // 双缓冲计数
    //   per_cpu_ptr(ss->sda, cpu)->srcu_lock_idx       // 当前索引
};
```

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_SRCU(name)` | 静态定义 | |
| `DEFINE_STATIC_SRCU(name)` | 静态定义（文件静态） | |
| `init_srcu_struct(ssp)` | 动态初始化 | |
| `cleanup_srcu_struct(ssp)` | 销毁 | |
| `srcu_read_lock(ssp)` | 注册读者 | 返回 index（必须传给 unlock） |
| `srcu_read_unlock(ssp, idx)` | 注销读者 | 传入 read_lock 返回的 index |
| `synchronize_srcu(ssp)` | 等待宽限期 | **可睡眠**；比 synchronize_rcu 重 |
| `synchronize_srcu_expedited(ssp)` | 加速宽限期 | |
| `call_srcu(ssp, head, func)` | 注册回调 | 宽限期后调用 func |
| `srcu_batches_completed(ssp)` | 查询已完成的批次数 | 调试用 |

头文件：`include/linux/srcu.h`；实现：`kernel/rcu/srcutree.c`（1,854 行）

### 与标准 RCU 的区别

| 维度 | RCU | SRCU |
|------|-----|------|
| 读侧可睡眠 | 否（PREEMPT_RT 除外） | **是** |
| 读侧开销 | 几乎为零 | per-CPU 计数 + 内存屏障 |
| 宽限期等待 | 快（利用上下文切换） | **慢**（需主动轮询读计数） |
| 全局 vs 局部 | 一个全局 RCU 状态 | 每个 `srcu_struct` 独立 |
| 典型用户 | 内核核心（链表、路由表） | 驱动、KVM |

### 使用模式

```c
static struct srcu_struct my_srcu;
static struct my_data __rcu *shared_data;

// 读者：可以睡眠
ssize_t my_read(struct file *file, char __user *buf, size_t count, loff_t *ppos)
{
    int idx = srcu_read_lock(&my_srcu);
    struct my_data *d = rcu_dereference_srcu(shared_data);
    ret = copy_to_user(buf, d->buffer, count);  // 可能睡眠！
    srcu_read_unlock(&my_srcu, idx);
    return ret;
}

// 写者
void update(struct my_data *new)
{
    struct my_data *old = rcu_replace_pointer(shared_data, new, lockdep_is_held(&write_mutex));
    synchronize_srcu(&my_srcu);  // 等待所有 SRCU 读者完成
    kfree(old);
}
```

### 使用注意

- `srcu_read_lock` 返回的 index **必须**传给对应的 `srcu_read_unlock`
- `synchronize_srcu` 比 `synchronize_rcu` 慢得多——不要在热路径上频繁调用
- 每个 `srcu_struct` 独立管理——不同保护对象用不同的 `srcu_struct`
- 模块卸载前必须确保所有 SRCU 读者完成（`cleanup_srcu_struct` 内部会等待）
