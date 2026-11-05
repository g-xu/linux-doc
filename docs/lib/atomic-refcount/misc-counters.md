# percpu_counter 与 u64_stats_sync

> 两种面向统计场景的计数器：percpu_counter 通过批量刷新避免缓存行颠簸；u64_stats_sync 在 32 位平台上保护 64 位统计值的撕裂读（arm64 上全部退化为空操作）。

---

## percpu_counter — 批量 per-CPU 计数器

### 定位

**功能**：高并发场景下的可扩展计数器。每个 CPU 维护本地 `s32` 累加器，只在累加器超过批量阈值（默认 32）时才刷新到全局 `s64 count`，大幅减少缓存行跨 CPU 颠簸。读操作可选快速近似读（直接返回全局值，最多偏差 batch×nr_cpus）或慢速精确求和（遍历所有 CPU）。

**实现方式**：核心结构体 `struct percpu_counter`（`include/linux/percpu_counter.h:23-30`）包含保护全局 count 的 `raw_spinlock_t`、全局精确值 `s64 count`、全局链表 `list`（CPU hotplug 用）和 per-CPU 本地累加器 `s32 __percpu *counters`。`percpu_counter_add_batch()`（`lib/percpu_counter.c:82-99`）快速路径：`preempt_disable()` 后直接加到 per-CPU 计数器，在 batch 范围内则写回并 `preempt_enable()`；慢路径：取锁刷新到全局 `fbc->count`，清零本地累加器。`percpu_counter_read()` 返回 `fbc->count`（快速但不精确），`percpu_counter_sum()` 加锁遍历所有 CPU 求和（精确但慢），`percpu_counter_compare()` 先用快速值比较、不确定时走精确求和。

**应用场景**：463 处 API 调用、129 处结构体引用。主要用户：文件系统——ext4（`fs/ext4/super.c`，inode/块计数）、btrfs（`fs/btrfs/disk-io.c`）、f2fs（`fs/f2fs/super.c`）、NFS server 统计（`fs/nfsd/stats.c`）、jbd2 日志（`fs/jbd2/journal.c`）；内存管理——shmem（`mm/shmem.c`）、mmap（`mm/mmap.c`）、nommu（`mm/nommu.c`）；网络——TCP（`net/ipv4/tcp.c`）、MPTCP（`net/mptcp/protocol.c`）、SCTP（`net/sctp/protocol.c`）；块层——BFQ（`block/bfq-cgroup.c`）。

### 实现方式

核心结构体（`include/linux/percpu_counter.h:23-30`）：

```c
struct percpu_counter {
    raw_spinlock_t lock;          // 保护全局 count
    s64 count;                    // 全局精确值（含已刷新的）
    struct list_head list;        // 全局链表（CPU hotplug 用）
    s32 __percpu *counters;       // per-CPU 本地累加器
};
```

**批量刷新逻辑**（`lib/percpu_counter.c:82-99`）：

```c
void percpu_counter_add_batch(struct percpu_counter *fbc, s64 amount, s32 batch)
{
    preempt_disable();
    // 快速路径：直接加到 per-CPU 计数器
    count = __this_cpu_read(*fbc->counters) + amount;
    if (count < batch && count > -batch) {
        __this_cpu_write(*fbc->counters, count);
        preempt_enable();
        return;
    }
    // 慢路径：刷新到全局
    raw_spin_lock(&fbc->lock);
    fbc->count += count;
    __this_cpu_write(*fbc->counters, 0);
    raw_spin_unlock(&fbc->lock);
    preempt_enable();
}
```

**读取**：
- `percpu_counter_read(fbc)` → 直接返回 `fbc->count`（快速但不精确，最多差 `batch * nr_cpus`）
- `percpu_counter_sum(fbc)` → 加锁遍历所有 CPU 求和（精确但慢）
- `percpu_counter_compare(fbc, rhs)` → 先用快速值比较，不确定时走精确求和

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `percpu_counter_init(fbc, value, gfp)` | 初始化 | 分配 per-CPU 内存 |
| `percpu_counter_destroy(fbc)` | 销毁 | 释放 per-CPU 内存 |
| `percpu_counter_inc(fbc)` | 加 1 | 快速路径无锁 |
| `percpu_counter_dec(fbc)` | 减 1 | |
| `percpu_counter_add(fbc, amount)` | 加 amount | 默认 batch |
| `percpu_counter_add_batch(fbc, amount, batch)` | 加 amount 用自定义 batch | |
| `percpu_counter_read(fbc)` | 快速近似读 | 可能偏差 batch×nr_cpus |
| `percpu_counter_read_positive(fbc)` | 近似读，保证 ≥ 0 | |
| `percpu_counter_sum(fbc)` | 精确求和 | 慢，遍历所有 CPU |
| `percpu_counter_sum_positive(fbc)` | 精确求和，保证 ≥ 0 | |
| `percpu_counter_compare(fbc, rhs)` | 比较 | 快速路径用近似值 |
| `percpu_counter_set(fbc, amount)` | 设置全局值 | 清零所有 per-CPU |
| `percpu_counter_sync(fbc)` | 刷新当前 CPU | |

### 使用规模

463 处 API 调用，129 处结构体引用。主要用于文件系统（ext4、btrfs、xfs）和内存管理。

---

## u64_stats_sync — 64 位统计同步

### 定位

**功能**：保护 64 位统计值在 32 位平台上的撕裂读。写入方用序列计数器（seqcount）标记写入区间，读取方在序列号偶数（稳定）时读取，奇数（写入中）时重试。**在 arm64（64 位）上整个机制退化为空操作**——原生 64 位读写本身就是原子的，无需额外同步。

**实现方式**：`include/linux/u64_stats_sync.h:64-68` 定义 `struct u64_stats_sync`：32 位平台包含 `seqcount_t seq`，64 位平台（`BITS_PER_LONG == 64`，即 arm64）结构体为空（0 字节）。32 位平台上的使用模式：写入方调 `u64_stats_update_begin()` 递增 seqcount、写入 64 位值、调 `u64_stats_update_end()` 再递增；读取方在 do-while 循环中调 `u64_stats_fetch_begin()` 获取序列号、读值、调 `u64_stats_fetch_retry()` 检查序列号是否变化。arm64 上所有操作都是 no-op：`fetch_begin` 返回 0、`fetch_retry` 返回 false、`update_begin/end` 空操作、`read` 直接 `READ_ONCE`。

**应用场景**：1,286 处 API 调用、127 处结构体引用。广泛用于网络驱动的 `ethtool_ops->get_stats64` 回调（`drivers/net/veth.c`、`drivers/net/virtio_net.c`、`drivers/net/xen-netfront.c` 等）、Open vSwitch 数据路径统计（`net/openvswitch/datapath.c`、`net/openvswitch/flow_table.c`）、网桥统计（`net/bridge/br_vlan.c`、`net/bridge/br_multicast.c`）、VLAN（`net/8021q/vlan_dev.c`）、VRF（`drivers/net/vrf.c`）和 drop monitor（`net/core/drop_monitor.c`）。在 arm64 上零开销。

### 实现方式

`include/linux/u64_stats_sync.h:64-68`：

```c
struct u64_stats_sync {
#if BITS_PER_LONG == 32
    seqcount_t seq;
#endif
};
```

arm64 上 `BITS_PER_LONG == 64`，结构体为空（0 字节）。

**使用模式**（32 位平台上的行为）：

```c
// 写入方
u64_stats_update_begin(&stats->syncp);
stats->rx_bytes += len;    // 64 位写，在 32 位上可能撕裂
stats->rx_packets++;
u64_stats_update_end(&stats->syncp);

// 读取方
do {
    start = u64_stats_fetch_begin(&stats->syncp);
    bytes = u64_stats_read(&stats->rx_bytes);
    packets = u64_stats_read(&stats->rx_packets);
} while (u64_stats_fetch_retry(&stats->syncp, start));
```

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `u64_stats_init(syncp)` | 初始化 | arm64 上为空 |
| `u64_stats_read(p)` | 读 u64_stats_t 值 | |
| `u64_stats_set(p, val)` | 设置值 | |
| `u64_stats_add(p, val)` | 加值 | |
| `u64_stats_update_begin(syncp)` | 开始写入区 | 32 位递增 seqcount |
| `u64_stats_update_end(syncp)` | 结束写入区 | 32 位递增 seqcount |
| `u64_stats_fetch_begin(syncp)` | 开始读取 | 返回序列号 |
| `u64_stats_fetch_retry(syncp, start)` | 检查是否需要重试 | arm64 上永远返回 false |
| `u64_stats_update_begin_irqsave(syncp, flags)` | 写入 + 关中断 | 写入方可能被中断打断时用 |
| `u64_stats_update_end_irqrestore(syncp, flags)` | 结束 + 恢复中断 | |

### arm64 上的行为

所有操作都是 no-op 或直接读写：
- `u64_stats_fetch_begin` → 返回 0
- `u64_stats_fetch_retry` → 返回 false（永不重试）
- `u64_stats_update_begin/end` → 空操作
- `u64_stats_read` → 直接 `READ_ONCE`

### 使用规模

1,286 处 API 调用，127 处结构体引用。广泛用于网络驱动（`ethtool_ops->get_stats64`）、块设备统计、文件系统 I/O 统计。
