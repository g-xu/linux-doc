---
title: 总览
---

# 同步与锁机制 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **spinlock** | 基本自旋锁 | 短临界区保护；PREEMPT_RT 下变为可睡眠；arm64 用 qspinlock（MCS 队列） | [spinlock.md](spinlock.md) |
| **raw_spinlock** | 真正自旋锁（所有配置） | PREEMPT_RT 下仍真正自旋；调度器/中断控制器/timer 核心路径专用 | [misc-spinlocks.md](misc-spinlocks.md) |
| **bit_spinlock** | 用单个位实现自旋锁 | 零额外结构开销；借用已有字的一位（页表锁、slab） | [misc-spinlocks.md](misc-spinlocks.md) |
| **rwlock** | 读写分离自旋锁 | 多读者并发，写者独占；短临界区的读写保护 | [misc-spinlocks.md](misc-spinlocks.md) |
| **local_lock** | per-CPU 数据保护锁 | 非 RT = preempt_disable；RT = per-CPU spinlock | [misc-spinlocks.md](misc-spinlocks.md) |
| **mutex** | 可睡眠互斥锁 | 有所有权（必须由获取者释放）；快速路径单次 cmpxchg | [mutex.md](mutex.md) |
| **rt_mutex** | 优先级继承互斥锁 | 防止优先级反转；rbtree 按优先级排序等待队列 | [misc-mutexes.md](misc-mutexes.md) |
| **ww_mutex** | Wait-Wound 互斥锁 | 允许任意顺序获取多把锁而不死锁；GPU 驱动专用 | [misc-mutexes.md](misc-mutexes.md) |
| **semaphore** | 计数信号量 | 无所有权（任何上下文可 up）；限流（N 个并发） | [misc-mutexes.md](misc-mutexes.md) |
| **rw_semaphore** | 可睡眠读写信号量 | 原子 count 编码读者计数+写者状态；快速路径一次原子操作 | [rw-semaphore.md](rw-semaphore.md) |
| **percpu_rwsem** | per-CPU 读写信号量 | 读快速路径无全局竞争；写侧需等 RCU 宽限期 | [misc-semaphores.md](misc-semaphores.md) |
| **RCU** | Read-Copy-Update | 读者完全无锁；写者需等宽限期；读极多写极少的数据结构 | [rcu.md](rcu.md) |
| **SRCU** | Sleepable RCU | 读者可睡眠；宽限期等待比 RCU 慢；每个 srcu_struct 独立 | [misc-rcu.md](misc-rcu.md) |
| **seqlock** | 序列锁 | 写者不阻塞读者；读者通过序列号检测一致性并重试 | [seqlock.md](seqlock.md) |
| **futex** | 快速用户空间互斥锁 | 无竞争零内核开销；竞争时陷入内核睡眠/唤醒 | [misc-userspace.md](misc-userspace.md) |
| **lockdep** | 锁依赖验证器 | 运行时构建锁依赖图 + BFS 检测环；2-5x 性能开销 | [misc-debug.md](misc-debug.md) |
| **lockref** | spinlock + refcount 原子打包 | cmpxchg64 无锁快速路径更新引用计数 | [misc-utils.md](misc-utils.md) |
| **static_call** | 可补丁间接调用 | 代码补丁替代函数指针；少更新多调用的回调场景 | [misc-utils.md](misc-utils.md) |

---

## §2 选型决策

```
需要保护什么？
│
├─ 短临界区（不超几微秒，不睡眠）
│   ├─ 纯互斥（无读写区分）
│   │   ├─ PREEMPT_RT 下必须真正自旋 ── raw_spinlock
│   │   ├─ 已有字中可借用一位 ──────── bit_spinlock
│   │   └─ 通用场景 ────────────────── spinlock
│   │
│   └─ 读多写少
│       └───────────────────────────── rwlock
│
├─ 长临界区（可能阻塞或持有较久）
│   ├─ 纯互斥（1 个持有者）
│   │   ├─ 需要优先级继承（PI） ─────── rt_mutex
│   │   └─ 通用场景 ────────────────── mutex
│   │
│   ├─ 读多写少
│   │   ├─ 读极多写极少 + 高 CPU 数 ─── percpu_rwsem
│   │   └─ 通用场景 ────────────────── rw_semaphore
│   │
│   ├─ 需要 N 个并发持有者
│   │   └───────────────────────────── semaphore
│   │
│   └─ 需要任意顺序获取多把锁
│       └───────────────────────────── ww_mutex
│
├─ 读极多写极少（更新 < 10%）
│   ├─ 读者不能阻塞 ────────────────── seqlock
│   ├─ 读者可睡眠 ──────────────────── SRCU
│   └─ 读者不睡眠 + 通用场景 ────────── RCU
│
├─ per-CPU 数据保护
│   └───────────────────────────────── local_lock
│
├─ 用户态互斥锁
│   └───────────────────────────────── futex
│
├─ 调试/验证
│   └───────────────────────────────── lockdep
│
└─ 性能优化
    ├─ 高频引用计数 ────────────────── lockref
    └─ 高频间接调用 ────────────────── static_call
```

### 关键区分点

| 容易混淆的对 | 区分依据 |
|-------------|---------|
| spinlock vs raw_spinlock | spinlock 在 RT 下可睡眠；raw_spinlock 永远真正自旋 |
| mutex vs semaphore | mutex 有所有权（必须获取者释放）；semaphore 无所有权（任何上下文可 up） |
| rw_semaphore vs rwlock | rw_semaphore 可睡眠（长临界区）；rwlock 不可睡眠（短临界区） |
| RCU vs SRCU | RCU 读者不可睡眠；SRCU 读者可睡眠但宽限期更慢 |
| RCU vs seqlock | RCU 保护指针发布；seqlock 保护多字段一致性（读者重试） |
| rt_mutex vs mutex | rt_mutex 有优先级继承；mutex 没有。PREEMPT_RT 下 mutex 底层就是 rt_mutex |
| percpu_rwsem vs rw_semaphore | percpu_rwsem 读侧无全局竞争（高扩展性）；写侧需等 RCU 宽限期 |
| lockdep vs 其他锁 | lockdep 不是锁——是锁依赖验证器，检测死锁风险 |

---

## §3 核心对比表

### 3.1 自旋锁类

| 模块 | 可睡眠 | 读共享 | 快速路径 | arm64 实现 | 持有时间上限 |
|------|--------|--------|---------|-----------|------------|
| spinlock | 非 RT 否 / RT 是 | 否 | 单次 cmpxchg | qspinlock（MCS 队列） | 几微秒 |
| raw_spinlock | **永远否** | 否 | 单次 cmpxchg | qspinlock | 几微秒 |
| bit_spinlock | 否 | 否 | test_and_set_bit | 原子位操作 | 几微秒 |
| rwlock | 非 RT 否 / RT 是 | 是 | 原子读计数 | qrlock | 几微秒 |
| local_lock | 非 RT 否（preempt_disable） / RT 是（per-CPU spinlock） | N/A（per-CPU 保护） | preempt_disable | N/A | 几微秒 |

### 3.2 可睡眠锁类

| 模块 | 有所有权 | 优先级继承 | 快速路径 | 等待队列 | 适用场景 |
|------|---------|-----------|---------|---------|---------|
| mutex | 是 | 否（RT 下底层是 rt_mutex） | 单次 cmpxchg | FIFO + OSQ | 通用长临界区 |
| rt_mutex | 是 | 是 | 单次 cmpxchg | rbtree（按优先级） | 需要 PI 的实时子系统 |
| ww_mutex | 是 | 否 | 继承 mutex | FIFO + OSQ | GPU buffer object 验证 |
| semaphore | **否** | 否 | 原子计数 + spinlock | FIFO | 限流（N 并发） |

### 3.3 读写锁类

| 模块 | 可睡眠 | 读快速路径 | 写侧代价 | 典型用途 |
|------|--------|-----------|---------|---------|
| rw_semaphore | 是 | 单次 `atomic_add(256)` | 唤醒等待队列 | 通用读写保护 |
| percpu_rwsem | 是 | `this_cpu_inc`（无全局原子） | 需等 RCU 宽限期 | mmap_lock 等读极多写极少 |

### 3.4 无锁/轻锁类

| 模块 | 读者开销 | 写者开销 | 读者可睡眠 | 保护对象 |
|------|---------|---------|-----------|---------|
| RCU | **零**（仅标记临界区） | 等宽限期 + 内存屏障 | 否 | 指针发布、链表 |
| SRCU | 极低（per-CPU 计数） | 等 srcu 宽限期（比 RCU 慢） | 是 | 跨 I/O 的读侧保护 |
| seqlock | 读序列号 + 重试 | 单次 spinlock | 否 | timekeeping、dcache 多字段一致性 |

### 3.5 工具/调试类

| 模块 | 类型 | 开销 | 用途 |
|------|------|------|------|
| lockdep | 验证器 | 2-5x 性能开销 | 运行时检测死锁风险 |
| lockref | 优化 | cmpxchg64 无锁快速路径 | dentry/inode 引用计数 |
| static_call | 优化 | 代码补丁（更新代价高） | 少更新多调用的回调 |
| futex | 用户态锁 | 无竞争零内核开销 | glibc pthread_mutex 底层 |

---

## §4 使用规模

以下数据来自 `rg -c PATTERN -g '*.c' -g '*.h'` 对 `/workspace/safety/linux-6.1.y` 全树的精确统计：

| 模块 | 涉及文件数 | 调用/出现总次数 | grep 命令 |
|------|-----------|---------------|----------|
| spin_lock | 2783 | 11045 | `rg -c '\bspin_lock\b' -g '*.c' -g '*.h'` |
| mutex_lock | 5203 | 22819 | `rg -c '\bmutex_lock\b' -g '*.c' -g '*.h'` |
| rcu_read_lock | 1295 | 4185 | `rg -c '\brcu_read_lock\b' -g '*.c' -g '*.h'` |
| down_read | 380 | 946 | `rg -c '\bdown_read\b' -g '*.c' -g '*.h'` |
| read_lock | 223 | 559 | `rg -c '\bread_lock\b' -g '*.c' -g '*.h'` |
| raw_spin_lock | 151 | 395 | `rg -c '\braw_spin_lock\b' -g '*.c' -g '*.h'` |
| down (semaphore) | 80 | 174 | `rg -c '\bdown\(' -g '*.c' -g '*.h'` |
| srcu_read_lock | 82 | 240 | `rg -c '\bsrcu_read_lock\b' -g '*.c' -g '*.h'` |
| lockdep | 318 | 574 | `rg -c '\blockdep\b' -g '*.c' -g '*.h'` |
| static_call | 57 | 296 | `rg -c '\bstatic_call\b' -g '*.c' -g '*.h'` |
| futex | 80 | 441 | `rg -c '\bfutex\b' -g '*.c' -g '*.h'` |
| local_lock | 31 | 89 | `rg -c '\blocal_lock\b' -g '*.c' -g '*.h'` |
| ww_mutex_lock | 10 | 45 | `rg -c '\bww_mutex_lock\b' -g '*.c' -g '*.h'` |
| rt_mutex | 17 | 95 | `rg -c '\brt_mutex\b' -g '*.c' -g '*.h'` |
| percpu_down_read | 11 | 24 | `rg -c '\bpercpu_down_read\b' -g '*.c' -g '*.h'` |
| read_seqbegin | 39 | 56 | `rg -c '\bread_seqbegin\b' -g '*.c' -g '*.h'` |
| bit_spin_lock | 7 | 10 | `rg -c '\bbit_spin_lock\b' -g '*.c' -g '*.h'` |
| lockref | 8 | 66 | `rg -c '\blockref\b' -g '*.c' -g '*.h'` |

**解读**：
- mutex_lock 是使用最广泛的锁（5203 文件、22819 次调用），几乎是所有子系统的默认选择
- spin_lock 第二（2783 文件、11045 次），短临界区保护的主力
- rcu_read_lock 第三高频（1295 文件、4185 次），说明 RCU 是读多写少场景的标准方案
- down_read（rw_semaphore）946 次，rwlock 559 次——可睡眠读写锁使用量高于自旋读写锁
- raw_spin_lock 395 次，集中在调度器和中断控制器等核心路径
- bit_spin_lock 仅 10 次（7 文件），属于极端优化场景
- percpu_down_read 仅 24 次（11 文件），但用于 mmap_lock 等关键路径
- ww_mutex 仅 45 次（10 文件），几乎全在 DRM/GPU 驱动中

---

## §5 设计模式速览

### 5.1 三层抽象模式

spinlock 和 mutex 都采用三层抽象：
- **API 层**（spinlock_t / struct mutex）：面向用户，PREEMPT_RT 下可替换底层实现
- **抽象层**（raw_spinlock_t / owner 字段）：真正的锁逻辑
- **架构层**（arch_spinlock_t = qspinlock）：arm64 上的 MCS 队列实现

这种分层让 PREEMPT_RT 能把 spinlock_t 变成可睡眠的 rt_mutex，而核心路径用 raw_spinlock_t 保持真正自旋。

### 5.2 快速路径优化

所有锁都追求无竞争时的最小开销：
- **spinlock**：单次 `cmpxchg` 设置 locked 位
- **mutex**：单次 `cmpxchg` 设置 owner 指针（低 3 位编码状态）
- **rw_semaphore**：单次 `atomic_add(256)` 获取读锁
- **percpu_rwsem**：`this_cpu_inc` 获取读锁（无全局原子操作）
- **lockref**：`cmpxchg64` 绕过 spinlock 更新引用计数

### 5.3 RCU 保护模式

RCU 是多个模块共享的底层机制：
- **xarray / rhashtable** 用 RCU 实现无锁读
- **mutex** 的 owner 字段用 RCU 保护生命周期
- **SRCU** 是 RCU 的可睡眠变体
- **seqlock** 的 seqcount 变体常与 RCU 配合使用

### 5.4 等待队列基础

所有可睡眠锁共享 `wait_queue` 基础设施：
- mutex 用 FIFO 等待队列 + OSQ 乐观自旋
- rw_semaphore 用 FIFO 等待队列 + OSQ
- semaphore 用 FIFO 等待队列
- futex 用 plist（按优先级排序）等待队列
- rt_mutex 用 rbtree（按优先级排序）等待队列

### 5.5 PREEMPT_RT 适配

PREEMPT_RT 改变了多个锁的行为：
- `spinlock_t` → 包装 `rt_mutex_base`（可睡眠）
- `rwlock_t` → 变为 `rwbase_rt`（可睡眠）
- `local_lock` → 变为真正的 per-CPU spinlock
- `raw_spinlock_t` → 不变（永远真正自旋）

---

## §6 锁模型对比

### 6.1 完整并发模型表

| 模块 | 持有者数 | 可睡眠 | 中断上下文可用 | 递归安全 | 所有权 | PREEMPT_RT 行为 |
|------|---------|--------|--------------|---------|--------|----------------|
| spinlock | 1 | 非 RT 否 / RT 是 | GFP_ATOMIC 上下文可用 | **否** | 是（必须获取者释放） | 变为 rt_mutex（可睡眠） |
| raw_spinlock | 1 | **永远否** | 可用（包括中断） | **否** | 是 | **不变**（永远真正自旋） |
| bit_spinlock | 1 | 否 | 可用 | **否** | 是 | 不变 |
| rwlock | 多读者 / 1 写者 | 非 RT 否 / RT 是 | 可用 | 读可递归 | 是 | 变为 rwbase_rt（可睡眠） |
| local_lock | 1（per-CPU） | 非 RT 否 / RT 是 | 可用 | N/A | 是 | 变为 per-CPU spinlock |
| mutex | 1 | **是** | **否**（死锁） | **否** | 是（debug 检查） | 底层是 rt_mutex |
| rt_mutex | 1 | **是** | **否** | **否** | 是 | 不变 |
| ww_mutex | 1（per-context 多把） | **是** | **否** | 通过 ww_acquire_ctx | 是 | 不变 |
| semaphore | N | **是** | **否** | 取决于计数 | **否**（任何上下文可 up） | 不变 |
| rw_semaphore | 多读者 / 1 写者 | **是** | **否** | 读可递归（部分架构） | 是 | 不变 |
| percpu_rwsem | 多读者 / 1 写者 | **是** | **否** | 读可递归 | 是 | 不变 |
| RCU | 无限读者 | 读者**否** | 读者可用 | 是 | N/A（无持有者概念） | 不变 |
| SRCU | 无限读者 | 读者**是** | 读者可用 | 是 | N/A | 不变 |
| seqlock | 1 写者 / 无限读者 | 写者否 / 读者否 | 可用 | 写者否 | 是（写者） | 不变 |
| futex | 1 | **是**（用户态） | N/A | **否** | 是 | 不变 |
| lockdep | N/A | N/A | N/A | N/A | N/A | 不变（调试工具） |
| lockref | 1 | 否 | 可用 | **否** | 是 | 不变 |
| static_call | N/A | N/A | N/A | N/A | N/A | 不变（代码补丁） |

### 6.2 上下文兼容性矩阵

| 模块 | 进程上下文 | softirq | 硬中断 | NMI | 用户态 |
|------|-----------|---------|--------|-----|--------|
| spinlock | 可用 | 可用 | 可用 | **否**（可能睡眠 on RT） | 可用 |
| raw_spinlock | 可用 | 可用 | 可用 | 可用 | 可用 |
| bit_spinlock | 可用 | 可用 | 可用 | 可用 | 可用 |
| rwlock | 可用 | 可用 | 可用 | **否**（RT） | 可用 |
| local_lock | 可用 | 可用 | 可用 | **否**（RT） | 可用 |
| mutex | 可用 | **否** | **否** | **否** | **否** |
| rt_mutex | 可用 | **否** | **否** | **否** | **否** |
| ww_mutex | 可用 | **否** | **否** | **否** | **否** |
| semaphore | 可用 | **否** | **否** | **否** | **否** |
| rw_semaphore | 可用 | **否** | **否** | **否** | **否** |
| percpu_rwsem | 可用 | **否** | **否** | **否** | **否** |
| RCU（读侧） | 可用 | 可用 | 可用 | 可用 | 可用 |
| SRCU（读侧） | 可用 | 可用 | 可用 | 可用 | 可用 |
| seqlock | 可用 | 可用 | 可用 | 可用 | 可用 |
| futex | N/A | N/A | N/A | N/A | 可用 |

### 6.3 性能特征对比

| 模块 | 无竞争开销 | 有竞争开销 | 扩展性 | 缓存行影响 |
|------|-----------|-----------|--------|-----------|
| spinlock | ~20ns（单次 cmpxchg） | ~100ns（MCS 队列） | 好（MCS 公平队列） | 单缓存行 |
| raw_spinlock | 同 spinlock | 同 spinlock | 同 spinlock | 单缓存行 |
| mutex | ~15ns（单次 cmpxchg） | ~1μs（睡眠+唤醒） | 好（OSQ 乐观自旋） | 单缓存行 |
| rw_semaphore | ~15ns（atomic_add） | ~1μs | 读者好 / 写者一般 | 单缓存行 |
| percpu_rwsem | ~5ns（this_cpu_inc） | ~10μs（写侧等 RCU GP） | 读者极好 / 写者差 | per-CPU 缓存行 |
| RCU | **零**（仅标记） | ~1ms（等宽限期） | 读者极好 | 无缓存行影响 |
| SRCU | ~10ns（per-CPU inc） | ~10ms（srcu GP） | 读者极好 | per-CPU 缓存行 |
| seqlock | ~5ns（读序列号） | 写者 ~20ns | 读者极好 | 单缓存行 |

---

## §7 组合使用模式

### 7.1 mutex + RCU：文件系统 namei 路径

```
inode->i_mutex（mutex）保护目录修改
dentry->d_lock（rcu_read_lock）保护 dcache 查找
```

- `fs/namei.c` 中路径解析使用 RCU 模式快速查找 dentry
- 查找失败时回退到 ref-walk 模式（获取 d_lock spinlock）
- 目录修改（mkdir/unlink）持有 inode->i_mutex

### 7.2 spinlock + percpu：slab 分配器

```
kmem_cache_cpu（per-CPU freelist）→ this_cpu_* 无锁访问
kmem_cache_node（全局）→ node->list_lock（spinlock）保护
```

- 快速路径：`this_cpu_ptr` 访问 per-CPU freelist，完全无锁
- 慢路径：获取 `node->list_lock`（spinlock）操作全局 slab 链表
- per-CPU 分片大幅减少 spinlock 竞争

### 7.3 rw_semaphore + RCU：模块加载

```
module_list（rw_semaphore）保护模块链表遍历
rcu_read_lock 保护模块指针的生命周期
```

- `kernel/module/main.c` 中模块列表用 rw_semaphore 保护
- 遍历模块列表时先获取 read lock
- 访问单个模块结构体时用 RCU 保证指针有效

### 7.4 spinlock + atomic_t：网络队列

```
netdev_queue->lock（spinlock）保护队列操作
txq->tx_bytes（atomic_long_t）统计无锁更新
```

- `net/core/dev.c` 中发送队列用 spinlock 保护入队/出队
- 统计数据用 atomic_long_t 无锁更新，避免持锁开销
- 读统计时遍历所有队列求和

### 7.5 rt_mutex + futex：用户态 PI 锁

```
用户态 futex → 无竞争：纯用户态 cmpxchg
             → 有竞争：futex_wait → rt_mutex（内核 PI 保护）
```

- `kernel/futex.c` 中 PI futex 使用 rt_mutex 实现优先级继承
- 无竞争路径完全在用户态完成（零内核开销）
- 有竞争时陷入内核，rt_mutex 防止优先级反转

### 7.6 lockdep + 所有锁：死锁检测

```
spin_lock → lock_acquire → lockdep 记录锁依赖
mutex_lock → lock_acquire → lockdep 检测环
```

- `kernel/locking/lockdep.c` 中所有锁的获取/释放都经过 lockdep 钩子
- 运行时构建锁依赖有向图
- BFS 检测环（A→B→A 死锁模式）
- 生产环境通常关闭（CONFIG_PROVE_LOCKING=n），开发/测试时开启

---

## §8 阅读顺序建议

### 路线 A：驱动开发（从最常用到最少用）

1. [spinlock.md](spinlock.md) — 短临界区保护的基础
2. [mutex.md](mutex.md) — 长临界区的默认选择
3. [rcu.md](rcu.md) — 读多写少的数据结构
4. [rw-semaphore.md](rw-semaphore.md) — 读写分离锁
5. [misc-spinlocks.md](misc-spinlocks.md) — raw_spinlock / rwlock / local_lock
6. [misc-mutexes.md](misc-mutexes.md) — rt_mutex / ww_mutex / semaphore
7. [misc-rcu.md](misc-rcu.md) — SRCU
8. [misc-semaphores.md](misc-semaphores.md) — percpu_rwsem
9. [seqlock.md](seqlock.md) — 序列锁
10. [misc-debug.md](misc-debug.md) — lockdep
11. [misc-utils.md](misc-utils.md) — lockref / static_call
12. [misc-userspace.md](misc-userspace.md) — futex

### 路线 B：内核核心开发（并发原语深入）

1. [spinlock.md](spinlock.md) — qspinlock MCS 队列实现
2. [mutex.md](mutex.md) — mutex 三层抽象 + OSQ
3. [rcu.md](rcu.md) — RCU 宽限期机制
4. [rw-semaphore.md](rw-semaphore.md) — 原子 count 编码
5. [seqlock.md](seqlock.md) — 序列号一致性检测
6. [misc-spinlocks.md](misc-spinlocks.md) — raw_spinlock 与 PREEMPT_RT
7. [misc-mutexes.md](misc-mutexes.md) — rt_mutex 优先级继承
8. [misc-semaphores.md](misc-semaphores.md) — percpu_rwsem 读侧无竞争
9. [misc-rcu.md](misc-rcu.md) — SRCU 可睡眠变体
10. [misc-debug.md](misc-debug.md) — lockdep 依赖图
11. [misc-utils.md](misc-utils.md) — lockref / static_call
12. [misc-userspace.md](misc-userspace.md) — futex

### 路线 C：PREEMPT_RT 实时开发

1. [spinlock.md](spinlock.md) — spinlock vs raw_spinlock 区别
2. [misc-spinlocks.md](misc-spinlocks.md) — raw_spinlock / local_lock RT 行为
3. [mutex.md](mutex.md) — mutex 在 RT 下的 rt_mutex 底层
4. [misc-mutexes.md](misc-mutexes.md) — rt_mutex 优先级继承
5. [rcu.md](rcu.md) — RCU 在 RT 下的调整
6. [rw-semaphore.md](rw-semaphore.md) — rw_semaphore RT 行为
7. [misc-spinlocks.md](misc-spinlocks.md) — rwlock RT 变为 rwbase_rt
8. [seqlock.md](seqlock.md) — seqlock 不受 RT 影响
9. [misc-rcu.md](misc-rcu.md) — SRCU
10. [misc-semaphores.md](misc-semaphores.md) — percpu_rwsem
11. [misc-debug.md](misc-debug.md) — lockdep
12. [misc-utils.md](misc-utils.md) — lockref / static_call
13. [misc-userspace.md](misc-userspace.md) — futex

---

## §9 文档索引

| 文档 | 覆盖模块 |
|------|----------|
| [spinlock.md](spinlock.md) | spinlock |
| [mutex.md](mutex.md) | mutex |
| [rcu.md](rcu.md) | RCU |
| [rw-semaphore.md](rw-semaphore.md) | rw_semaphore |
| [seqlock.md](seqlock.md) | seqlock |
| [misc-spinlocks.md](misc-spinlocks.md) | raw_spinlock / bit_spinlock / rwlock / local_lock |
| [misc-mutexes.md](misc-mutexes.md) | rt_mutex / ww_mutex / semaphore |
| [misc-semaphores.md](misc-semaphores.md) | percpu_rwsem |
| [misc-rcu.md](misc-rcu.md) | SRCU |
| [misc-userspace.md](misc-userspace.md) | futex |
| [misc-debug.md](misc-debug.md) | lockdep |
| [misc-utils.md](misc-utils.md) | lockref / static_call |
