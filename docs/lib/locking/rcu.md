# RCU — Read-Copy-Update

> 内核最强大的读侧同步机制。读者完全无锁——不需要原子操作、不需要内存屏障、不需要任何写者协作。代价是写者必须等待"宽限期"（grace period）结束后才能回收旧数据。适用于读极多写极少（更新 < 10%）的场景。

---

## §1 核心设计洞察

### 1.1 基本思想

RCU 的核心思想极其简洁：

```
1. 读者：rcu_read_lock() → 读取数据 → rcu_read_unlock()
   （无锁、无原子操作、无内存屏障）

2. 写者：
   a. 制作数据的新副本
   b. 原子地发布新指针（rcu_assign_pointer）
   c. 等待宽限期结束（synchronize_rcu / call_rcu）
   d. 回收旧副本
```

**宽限期**（grace period）：从写者发布新指针开始，到**所有 CPU** 都至少经过一次"静止状态"（quiescent state）的时间间隔。静止状态 = 不在任何 RCU 读侧临界区内的时刻。

### 1.2 rcu_read_lock 的实际实现

RCU 的读侧在不同配置下实现完全不同：

**PREEMPT_RCU（大多数发行版、PREEMPT_RT）**（`kernel/rcu/tree_plugin.h:400-408`）：

```c
void __rcu_read_lock(void)
{
    // 只是递增一个 per-task 计数器
    current->rcu_read_lock_nesting++;
}

void __rcu_read_unlock(void)
{
    // 递减计数器
    if (--current->rcu_read_lock_nesting == 0 &&
        unlikely(READ_ONCE(current->rcu_read_unlock_special.s)))
        rcu_read_unlock_special();  // 处理被抢占的情况
}
```

**非 PREEMPT_RCU**（`include/linux/rcupdate.h:93-103`）：

```c
static inline void __rcu_read_lock(void)  { preempt_disable(); }
static inline void __rcu_read_unlock(void) { preempt_enable(); }
```

**关键洞察**：在非 PREEMPT_RCU 内核中，`rcu_read_lock()` 就是 `preempt_disable()`。这意味着自 v5.0 起，`synchronize_rcu()` 也会等待所有 `preempt_disable()` 区域结束（`include/linux/rcupdate.h:751-755`）。

### 1.3 三级层次结构

RCU 的可扩展性来自三级数据结构（`kernel/rcu/tree.h`）：

```
struct rcu_state (全局唯一, kernel/rcu/tree.c:85-96)
    │
    ├── gp_seq: 全局宽限期序列号 (line 317)
    ├── gp_kthread: 宽限期内核线程 (line 321)
    ├── node[]: rcu_node 数组（树形结构）
    │
    ├── struct rcu_node (每个 CPU 组一个, kernel/rcu/tree.h:45-140)
    │   ├── lock: 保护本节点的 raw_spinlock (line 46)
    │   ├── qsmask: 哪些 CPU 还没报告静止状态 (lines 52-57)
    │   ├── parent: 指向父节点 (line 88)
    │   └── grplo/grphi: 覆盖的 CPU 范围 (lines 80-81)
    │
    └── struct rcu_data (每个 CPU 一个, kernel/rcu/tree.h:162-266)
        ├── gp_seq: 本 CPU 的宽限期跟踪 (lines 164-165)
        ├── mynode: 指向所属叶节点 (line 171)
        ├── grpmask: 本 CPU 在叶节点 qsmask 中的位 (line 172)
        └── cblist: 分段回调链表 (line 182)
```

**为什么用树？** 避免全局锁竞争。每个 `rcu_node` 有自己的锁。CPU 只在叶节点竞争。静止状态报告沿树向上传播：CPU 清除叶节点中的自己的位 → 叶节点所有位清除后向父节点传播 → 直到根节点。O(log N) 的可扩展性。

**树几何参数**（`include/linux/rcu_node_tree.h`）：
- `RCU_FANOUT` = 64（64 位系统）
- `RCU_FANOUT_LEAF` = 16
- arm64 NR_CPUS=256：2 级（根 + 叶），`RCU_NUM_LVLS = 2`

### 1.4 宽限期生命周期

宽限期内核线程（`kernel/rcu/tree.c:1879-1912`）的循环：

```
rcu_gp_kthread() 无限循环:
    │
    ├─ 1. 等待 GP 请求
    │     睡眠在 rcu_state.gp_wq 上
    │     直到 RCU_GP_FLAG_INIT 被设置
    │
    ├─ 2. 初始化 GP (rcu_gp_init, tree.c:1471)
    │     递增 gp_seq
    │     为所有在线 CPU 设置 qsmask 位
    │
    ├─ 3. 强制静止状态 (rcu_gp_fqs_loop, tree.c:1675)
    │     循环调用 force_qs_rnp()
    │     向未报告的 CPU 发送 IPI
    │     直到根节点 qsmask 清零
    │
    └─ 4. 清理 GP (rcu_gp_cleanup, tree.c:1761)
          向所有 rcu_node 传播新 gp_seq
          加速回调 (rcu_accelerate_cbs)
          如果需要更多 GP → 设置 RCU_GP_FLAG_INIT
```

### 1.5 静止状态的检测

CPU 通过以下方式报告静止状态：

| 触发点 | 机制 |
|--------|------|
| 上下文切换 | `rcu_note_context_switch()` — 调度器调用 |
| 进入用户态 | 用户态执行天然是静止状态 |
| 进入空闲 | `rcu_report_dead()` / dyntick-idle 跟踪 |
| `rcu_read_unlock()` | PREEMPT_RCU 下，如果被抢占过则报告 |
| 强制 QS | `force_qs_rnp()` 发送 IPI 到未报告的 CPU |

### 1.6 指针管理：发布与读取

**发布指针**（`include/linux/rcupdate.h:536`）：

```c
#define rcu_assign_pointer(p, v)                        \
do {                                                    \
    smp_store_release(&p, RCU_INITIALIZER(v));          \
} while (0)
```

使用 `smp_store_release` 保证：指针赋值之前的所有写操作在指针可见之前对读者可见。

**读取指针**（`include/linux/rcupdate.h:700`）：

```c
#define rcu_dereference(p)  rcu_dereference_check(p, rcu_read_lock_held())
```

内部使用 `READ_ONCE` 防止编译器缓存指针值。必须在 `rcu_read_lock()` 区域内使用。

**相关变体**：

| API | 说明 |
|-----|------|
| `rcu_access_pointer(p)` | 只读取指针值，不解引用。不需要 `rcu_read_lock` |
| `rcu_dereference_protected(p, c)` | 当更新被其他机制阻止时使用（不需要 `rcu_read_lock`） |
| `rcu_replace_pointer(rcu_ptr, ptr, c)` | 原子替换，返回旧值 |
| `RCU_INIT_POINTER(p, v)` | 静态初始化（无排序保证） |

### 1.7 回调与 kfree_rcu

**call_rcu**（`kernel/rcu/tree.c:2820`）：

```c
void call_rcu(struct rcu_head *head, rcu_callback_t func);
```

注册回调函数，在宽限期结束后由 RCU 软中断/kthread 调用。回调在 `rcu_do_batch()`（`kernel/rcu/tree.c:2234`）中批量执行。

**kfree_rcu**（`include/linux/rcupdate.h:1003`）：

```c
#define kfree_rcu(ptr, rhf...) kvfree_rcu(ptr, ## rhf)
```

两种形式：
- **双参数** `kfree_rcu(ptr, rcu_head_field)`：通过结构体中嵌入的 `rcu_head` 字段定位回调。高效批量释放
- **单参数** `kfree_rcu(ptr)`：不需要嵌入 `rcu_head`。需要 `might_sleep()` 上下文。失败时回退到 `synchronize_rcu()` + `kvfree()`

实际释放由 `kfree_rcu_work()`（`kernel/rcu/tree.c:3054`）在工作队列中执行。

### 1.8 rcu_barrier

`rcu_barrier()`（`kernel/rcu/tree.c:4012-4106`）：

等待**所有**已注册的 `call_rcu()` 回调完成——不只是等一个宽限期，而是等所有 CPU 上的所有待处理回调。

- 通过 `barrier_mutex` 串行化并发 barrier
- 向每个有待处理回调的 CPU 发送 IPI，注册特殊的 barrier 回调
- barrier 回调递减 `barrier_cpu_count`，到零时 barrier 完成
- **模块卸载前必须调用**——确保模块代码不会被回调引用

### 1.9 回调卸载 (NOCB)

`kernel/rcu/tree_nocb.h`（1590 行）实现了回调卸载：

- 指定 CPU 的回调不由本 CPU 处理，而是由专用 kthread 处理
- 通过 `rcu_nocb_cpu_offload()`/`rcu_nocb_cpu_deoffload()` 动态控制
- 适用于 HPC / 实时场景——避免 CPU 被回调处理打断

### 1.10 PREEMPT_RT 下的行为变化

| 特性 | 非 RT | PREEMPT_RT |
|------|-------|------------|
| RCU 核心处理 | softirq | 专用 per-CPU kthread（`rcuc`） |
| 读侧临界区 | 不可抢占（preempt_disable） | **可抢占**，可阻塞在睡眠 spinlock 上 |
| 静止状态 | 需要显式报告 | 自然从 `schedule()` 获得 |

`kernel/rcu/tree.c:102`：`static bool use_softirq = !IS_ENABLED(CONFIG_PREEMPT_RT);`

### 1.11 arm64 特定

RCU 核心实现与架构无关——`arch/arm64/` 下没有 RCU 特定代码。arm64 使用 RCU 的标准方式：
- CPU 热插拔：`rcu_cpu_starting()` / `rcu_report_dead()`
- 调试断点 hook：`list_add_rcu()` / `list_for_each_entry_rcu()`
- KVM vCPU 运行：进入 RCU 扩展静止状态

---

## §2 API 速查

### 2.1 读侧原语

头文件：`include/linux/rcupdate.h`

| API | 说明 | 备注 |
|-----|------|------|
| `rcu_read_lock()` | 开始 RCU 读侧临界区 | 无锁；递增 per-task 计数器 |
| `rcu_read_unlock()` | 结束 RCU 读侧临界区 | 必须与 lock 配对 |
| `rcu_read_lock_bh()` | 开始 RCU-bh 区域 | 禁止 softirq |
| `rcu_read_unlock_bh()` | 结束 RCU-bh 区域 | |
| `rcu_read_lock_sched()` | 开始 RCU-sched 区域 | 禁止抢占 |
| `rcu_read_unlock_sched()` | 结束 RCU-sched 区域 | |
| `rcu_read_lock_held()` | 检查是否在 RCU 读侧区域内 | lockdep 用 |
| `rcu_preempt_depth()` | 返回嵌套深度 | 仅 PREEMPT_RCU |

### 2.2 更新侧原语

| API | 说明 | 备注 |
|-----|------|------|
| `synchronize_rcu()` | 等待宽限期结束 | 阻塞调用；`kernel/rcu/tree.c:3569` |
| `synchronize_rcu_expedited()` | 加速宽限期 | 基于 IPI，更快但更扰；`kernel/rcu/tree_exp.h:921` |
| `call_rcu(head, func)` | 注册宽限期后回调 | 异步；`kernel/rcu/tree.c:2820` |
| `kfree_rcu(ptr, rhf)` | 宽限期后释放 | 宏 → `kvfree_rcu`；`include/linux/rcupdate.h:1003` |
| `rcu_barrier()` | 等待所有回调完成 | 模块卸载用；`kernel/rcu/tree.c:4012` |

### 2.3 指针管理

| API | 说明 | 备注 |
|-----|------|------|
| `rcu_assign_pointer(p, v)` | 发布新指针 | `smp_store_release`；写者用 |
| `rcu_dereference(p)` | 安全解引用 RCU 指针 | `READ_ONCE`；读者用，需在 `rcu_read_lock` 内 |
| `rcu_dereference_check(p, c)` | 解引用 + 条件检查 | |
| `rcu_dereference_protected(p, c)` | 更新被阻止时解引用 | 不需要 `rcu_read_lock` |
| `rcu_access_pointer(p)` | 读取指针值（不解引用） | 不需要 `rcu_read_lock` |
| `rcu_replace_pointer(rcu_ptr, ptr, c)` | 原子替换，返回旧值 | |
| `RCU_INIT_POINTER(p, v)` | 静态初始化 | 无排序保证 |

### 2.4 RCU 链表操作

头文件：`include/linux/rculist.h`

| API | 说明 | 备注 |
|-----|------|------|
| `list_add_rcu(new, head)` | 在 head 后添加 | 原子发布新节点 |
| `list_add_tail_rcu(new, head)` | 在 head 前添加 | |
| `list_del_rcu(entry)` | 删除（不重新初始化） | 读者可能仍在遍历 |
| `list_replace_rcu(old, new)` | 原子替换节点 | |
| `list_for_each_entry_rcu(pos, head, member)` | RCU 保护下遍历 | **必须**用此而非 `list_for_each_entry` |
| `hlist_add_head_rcu(n, h)` | 哈希链表头添加 | 原子发布 |
| `hlist_del_rcu(n)` | 哈希链表删除 | |
| `hlist_replace_rcu(old, new)` | 哈希链表替换 | |
| `hlist_for_each_entry_rcu(pos, head, member)` | RCU 保护下遍历哈希链表 | |
| `list_for_each_entry_srcu(pos, head, member, cond)` | SRCU 保护下遍历 | |
| `hlist_for_each_entry_srcu(pos, head, member, cond)` | SRCU 保护下遍历哈希 | |

### 2.5 SRCU API

头文件：`include/linux/srcu.h`

| API | 说明 | 备注 |
|-----|------|------|
| `init_srcu_struct(ssp)` | 初始化 SRCU 结构 | |
| `cleanup_srcu_struct(ssp)` | 清理 SRCU 结构 | |
| `srcu_read_lock(ssp)` | 注册 SRCU 读者 | 返回 index |
| `srcu_read_unlock(ssp, idx)` | 注销 SRCU 读者 | 传入 index |
| `synchronize_srcu(ssp)` | 等待 SRCU 宽限期 | 可阻塞 |
| `call_srcu(ssp, head, func)` | SRCU 回调 | |

**SRCU vs RCU**：SRCU 允许读者在任意上下文（包括可睡眠上下文）中持有读锁。代价是 `synchronize_srcu` 比 `synchronize_rcu` 更重。典型用户：设备驱动中需要跨睡眠操作保护的读侧遍历。

---

## §3 使用模式

### 3.1 基本 RCU 保护的数据读取

```c
struct my_config {
    int param1;
    int param2;
};

static struct my_config __rcu *global_config;

// 读者
int read_param1(void)
{
    struct my_config *cfg;
    int val;

    rcu_read_lock();
    cfg = rcu_dereference(global_config);
    val = cfg->param1;       // 安全：在宽限期内 cfg 不会被释放
    rcu_read_unlock();

    return val;
}

// 写者
void update_config(int new_param1)
{
    struct my_config *old, *new;

    new = kmalloc(sizeof(*new), GFP_KERNEL);
    // 复制旧数据
    rcu_read_lock();
    old = rcu_dereference(global_config);
    *new = *old;
    rcu_read_unlock();

    // 修改新副本
    new->param1 = new_param1;

    // 原子发布
    rcu_assign_pointer(global_config, new);

    // 等待所有读者完成
    synchronize_rcu();

    // 安全释放旧副本
    kfree(old);
}
```

### 3.2 call_rcu 异步释放

```c
struct my_entry {
    struct hlist_node node;
    struct rcu_head rcu;    // 嵌入 rcu_head
    int key;
    char value[64];
};

// 回调函数
static void free_entry_rcu(struct rcu_head *head)
{
    struct my_entry *entry = container_of(head, struct my_entry, rcu);
    kfree(entry);
}

// 删除并异步释放
void remove_entry(struct my_entry *entry)
{
    hlist_del_rcu(&entry->node);    // 从链表移除（读者可能仍在遍历）
    call_rcu(&entry->rcu, free_entry_rcu);  // 宽限期后释放
    // 立即返回——释放由 RCU 回调异步完成
}
```

### 3.3 kfree_rcu 简化释放

```c
struct my_data {
    struct rcu_head rcu;
    int value;
};

static struct my_data __rcu *shared;

void replace_data(int new_value)
{
    struct my_data *old, *new;

    new = kmalloc(sizeof(*new), GFP_KERNEL);
    new->value = new_value;

    old = rcu_replace_pointer(shared, new, lockdep_is_held(&update_lock));

    // 一行搞定：宽限期后自动 kfree(old)
    kfree_rcu(old, rcu);
}
```

### 3.4 RCU 保护的哈希表遍历

```c
#define HASH_SIZE 256
static struct hlist_head hash_table[HASH_SIZE];
static DEFINE_SPINLOCK(hash_lock);  // 写者互斥

// 查找（无锁）
struct my_entry *lookup(int key)
{
    struct my_entry *entry;
    u32 hash = hash_fn(key) % HASH_SIZE;

    rcu_read_lock();
    hlist_for_each_entry_rcu(entry, &hash_table[hash], node) {
        if (entry->key == key) {
            // 注意：不能安全地返回 entry 指针！
            // 必须在 rcu_read_lock 内完成所有操作
            refcount_inc(&entry->refcnt);
            rcu_read_unlock();
            return entry;
        }
    }
    rcu_read_unlock();
    return NULL;
}

// 插入（需要锁）
void insert(struct my_entry *new)
{
    u32 hash = hash_fn(new->key) % HASH_SIZE;

    spin_lock(&hash_lock);
    hlist_add_head_rcu(&new->node, &hash_table[hash]);
    spin_unlock(&hash_lock);
}
```

### 3.5 SRCU 跨睡眠读取

```c
static struct srcu_struct my_srcu;
static struct my_data __rcu *data;

int init(void)
{
    return init_srcu_struct(&my_srcu);
}

// 读者：可以睡眠
ssize_t my_read(struct file *file, char __user *buf, size_t count, loff_t *ppos)
{
    int idx;
    ssize_t ret;

    idx = srcu_read_lock(&my_srcu);
    // 在 SRCU 读侧可以安全地睡眠
    struct my_data *d = rcu_dereference_srcu(data);
    ret = copy_to_user(buf, d->buffer, count);  // 可能睡眠
    srcu_read_unlock(&my_srcu, idx);

    return ret;
}

// 写者
void update_data(struct my_data *new)
{
    struct my_data *old;

    old = rcu_replace_pointer(data, new, lockdep_is_held(&write_mutex));
    synchronize_srcu(&my_srcu);  // 等待所有 SRCU 读者完成
    kfree(old);
}
```

---

## §4 常见陷阱

### 4.1 写者不使用互斥

**错误**：两个写者同时修改 RCU 保护的数据结构 → 链表损坏。

**正确**：RCU 只保护读者。写者**必须**使用自己的互斥机制（spinlock、mutex、单线程等）。

### 4.2 在 rcu_read_lock 内睡眠

**错误**：`rcu_read_lock()` → `kmalloc(GFP_KERNEL)` → 可能睡眠 → 宽限期无法结束 → 系统挂起。

**正确**：标准 RCU 读侧临界区内不可睡眠。需要跨睡眠保护时用 SRCU。PREEMPT_RT 下可以阻塞在睡眠 spinlock 上。

### 4.3 在 rcu_read_lock 外使用 rcu_dereference

**错误**：没有 `rcu_read_lock()` 保护就调用 `rcu_dereference()` → lockdep 告警 → 数据可能在解引用期间被释放。

**正确**：`rcu_dereference()` 必须在 `rcu_read_lock()` 区域内，或使用 `rcu_dereference_protected()` 声明有其他保护。

### 4.4 返回 RCU 保护的指针到临界区外

**错误**：
```c
struct my_entry *lookup(int key) {
    rcu_read_lock();
    entry = find_entry(key);
    rcu_read_unlock();
    return entry;  // 危险！entry 可能已被释放
}
```

**正确**：在 `rcu_read_lock()` 内完成所有操作，或增加引用计数后再返回。

### 4.5 用 list_for_each_entry 遍历 RCU 链表

**错误**：用 `list_for_each_entry()` 遍历 RCU 保护的链表 → 遍历期间节点被删除 → 访问已释放内存。

**正确**：必须用 `list_for_each_entry_rcu()`。

### 4.6 模块卸载不调用 rcu_barrier

**错误**：模块卸载后，之前注册的 `call_rcu()` 回调仍然指向已卸载的模块代码 → 执行已释放的函数指针 → kernel panic。

**正确**：模块退出函数中必须调用 `rcu_barrier()` 等待所有回调完成。

### 4.7 混淆 RCU 变体

**错误**：用 `rcu_read_lock()` 保护数据，但用 `synchronize_rcu_bh()` 等待（或反过来）。

**正确**：自 v5.0 起，`synchronize_rcu()` 统一等待所有变体（RCU、RCU-bh、RCU-sched）的临界区。不需要区分。但读侧仍应使用匹配的变体：
- 数据在 softirq 中读取 → `rcu_read_lock_bh()`
- 数据在禁止抢占区域中读取 → `rcu_read_lock_sched()`
- 其他情况 → `rcu_read_lock()`

---

## §5 代码示例

### 5.1 设备驱动中的 RCU 配置表

```c
struct device_config {
    struct rcu_head rcu;
    u32 clock_rate;
    u32 voltage;
    u32 timeout_ms;
};

static struct device_config __rcu *active_config;
static DEFINE_MUTEX(config_mutex);  // 写者互斥

static int my_probe(struct platform_device *pdev)
{
    struct device_config *cfg = kzalloc(sizeof(*cfg), GFP_KERNEL);
    cfg->clock_rate = 100000000;
    cfg->voltage = 3300;
    cfg->timeout_ms = 500;
    rcu_assign_pointer(active_config, cfg);
    return 0;
}

// 读者：快速无锁读取
u32 get_clock_rate(void)
{
    struct device_config *cfg;
    u32 rate;

    rcu_read_lock();
    cfg = rcu_dereference(active_config);
    rate = cfg->clock_rate;
    rcu_read_unlock();
    return rate;
}

// 写者：更新配置
int set_config(u32 rate, u32 voltage)
{
    struct device_config *old, *new;

    new = kmalloc(sizeof(*new), GFP_KERNEL);
    if (!new)
        return -ENOMEM;

    mutex_lock(&config_mutex);
    old = rcu_dereference_protected(active_config,
                                     lockdep_is_held(&config_mutex));
    *new = *old;               // 复制旧配置
    new->clock_rate = rate;    // 修改
    new->voltage = voltage;

    rcu_assign_pointer(active_config, new);
    mutex_unlock(&config_mutex);

    kfree_rcu(old, rcu);       // 宽限期后释放
    return 0;
}

static int my_remove(struct platform_device *pdev)
{
    struct device_config *cfg;

    mutex_lock(&config_mutex);
    cfg = rcu_dereference_protected(active_config,
                                     lockdep_is_held(&config_mutex));
    rcu_assign_pointer(active_config, NULL);
    mutex_unlock(&config_mutex);

    synchronize_rcu();          // 等待所有读者完成
    kfree(cfg);
    return 0;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| PREEMPT_RCU 下 `__rcu_read_lock` 递增 nesting | `kernel/rcu/tree_plugin.h:400-408` | 一等 |
| 非 PREEMPT_RCU 下 `rcu_read_lock` = `preempt_disable` | `include/linux/rcupdate.h:93-96` | 一等 |
| v5.0+ `synchronize_rcu` 等待所有变体 | `include/linux/rcupdate.h:751-755` | 一等 |
| `struct rcu_state` 全局定义 | `kernel/rcu/tree.c:85-96` | 一等 |
| `struct rcu_node` 定义 | `kernel/rcu/tree.h:45-140` | 一等 |
| `struct rcu_data` 定义 | `kernel/rcu/tree.h:162-266` | 一等 |
| 宽限期 kthread 循环 | `kernel/rcu/tree.c:1879-1912` | 一等 |
| GP 初始化 | `kernel/rcu/tree.c:1471` | 一等 |
| 强制静止状态循环 | `kernel/rcu/tree.c:1675` | 一等 |
| GP 清理 | `kernel/rcu/tree.c:1761` | 一等 |
| `rcu_assign_pointer` 使用 `smp_store_release` | `include/linux/rcupdate.h:536` | 一等 |
| `rcu_dereference` 使用 `READ_ONCE` | `include/linux/rcupdate.h:700` | 一等 |
| `call_rcu` 定义 | `kernel/rcu/tree.c:2820` | 一等 |
| `synchronize_rcu` 定义 | `kernel/rcu/tree.c:3569` | 一等 |
| `rcu_barrier` 定义 | `kernel/rcu/tree.c:4012-4106` | 一等 |
| `kfree_rcu` 宏定义 | `include/linux/rcupdate.h:1003` | 一等 |
| `kfree_rcu_work` 释放实现 | `kernel/rcu/tree.c:3054` | 一等 |
| `kvfree_call_rcu` 实现 | `kernel/rcu/tree.c:3374` | 一等 |
| PREEMPT_RT 下 RCU 用 kthread 不用 softirq | `kernel/rcu/tree.c:102` | 一等 |
| 树几何参数 | `include/linux/rcu_node_tree.h:33-90` | 一等 |
| `struct callback_head` = `rcu_head` | `include/linux/types.h:221-225` | 一等 |
| arm64 无架构特定 RCU 代码 | `arch/arm64/` 下无 rcu 文件 | 一等 |
| `synchronize_rcu_bh` 已合并到 `synchronize_rcu` | v5.0 变更，`include/linux/rcupdate.h:751-755` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `rcu_read_lock` | 4,185 |
| `synchronize_rcu` | 566 |
| `call_rcu` | 491 |
| `rcu_dereference` | 1,848 |

### 关键文档

| 文件 | 用途 |
|------|------|
| `Documentation/RCU/whatisRCU.rst` | RCU 基础解释 |
| `Documentation/RCU/checklist.rst` | RCU 补丁审查清单 |
| `Documentation/RCU/listRCU.rst` | RCU 链表使用指南 |
| `Documentation/RCU/rcubarrier.rst` | `rcu_barrier()` 详解 |
| `Documentation/RCU/stallwarn.rst` | RCU CPU stall 告警 |
