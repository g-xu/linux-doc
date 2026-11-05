# 锁调试杂项 — lockdep

> 锁依赖验证器。在运行时构建锁的有向依赖图，检测潜在的死锁、锁序反转和 IRQ 上下文不安全。通过 `CONFIG_PROVE_LOCKING` 启用，是内核最重要的锁调试工具。

---

## lockdep

### 定位

**功能**：自动检测锁使用中的死锁风险——包括 AB-BA 死锁、IRQ 上下文锁序反转、读写锁不安全组合、递归获取等。在运行时构建锁类的有向依赖图，用 BFS 检测环路，是内核最重要的锁调试工具。

**实现方式**：每次 `lock_acquire`/`lock_release` 时，lockdep 在锁类（`struct lock_class`）之间构建有向依赖图（`locks_after`/`locks_before` 链表），用 BFS 检测环。通过链式哈希（`chain_key = hash(prev_chain_key, this_lock_class)`）缓存已验证的路径避免重复检查。`struct held_lock` 记录每个当前持有的锁的上下文。最大支持 8,192 个锁类（`MAX_LOCKDEP_KEYS`）。实现在 `kernel/locking/lockdep.c`（6,628 行）。

**应用场景**：内核开发和测试中通过 `CONFIG_PROVE_LOCKING` 启用全量锁序验证。`lockdep_assert_held()` 在生产代码中广泛用作前置条件断言——全树约 2,255 处，最高频用户包括无线驱动（`ath10k/mac.c` 75 处、`ath11k/mac.c` 44 处）、cgroup（46 处）、mac80211（44 处）、NFS 服务端（39 处）等。

### 核心结构

`include/linux/lockdep_types.h`：

```c
struct lock_class {                        // line 92
    struct hlist_node hash_entry;          // 全局哈希表
    struct list_head locks_after;          // 依赖图：本类之后获取的类
    struct list_head locks_before;         // 依赖图：本类之前获取的类
    unsigned long usage_mask;              // 使用场景位图（HARDIRQ、SOFTIRQ 等）
    // ...
};

struct lockdep_map {                       // line 177
    struct lock_class_key *key;            // 关联到 lock_class
    struct lock_class *class_cache[2];     // 缓存
    const char *name;                      // 锁名（用于错误消息）
};
```

`include/linux/lockdep.h`：

```c
struct held_lock {                         // line 89
    u64 prev_chain_key;                    // 链式验证缓存
    unsigned long acquire_ip;              // 获取时的返回地址
    struct lockdep_map *instance;          // 锁实例
    // ...
};
```

### API

### 断言类

| API | 说明 | 备注 |
|-----|------|------|
| `lockdep_assert_held(lock)` | 断言锁被持有 | 否则 WARN；全树约 2,255 处 |
| `lockdep_assert_held_once(lock)` | 一次性断言 | 只 WARN 一次 |
| `lockdep_assert_held_write(lock)` | 断言写锁持有 | |
| `lockdep_assert_held_read(lock)` | 断言读锁持有 | |
| `lockdep_assert_irqs_enabled()` | 断言中断已开 | |
| `lockdep_assert_irqs_disabled()` | 断言中断已关 | |
| `lockdep_assert_in_irq()` | 断言在 IRQ 上下文中 | |
| `lockdep_is_held(lock)` | 检查锁是否持有 | 返回 bool |

### 初始化类

| API | 说明 | 备注 |
|-----|------|------|
| `lockdep_init_map(map, name, key, 0)` | 初始化 lockdep 映射 | 通常由 `spin_lock_init` 等自动调用 |
| `lockdep_set_class(lock, key)` | 重新关联锁类 | 用于嵌套注解 |
| `lockdep_set_subclass(lock, sub)` | 改变子类 | |
| `lockdep_set_novalidate_class(lock)` | 跳过验证 | 用于已知安全的特殊情况 |

头文件：`include/linux/lockdep.h`（678 行）+ `include/linux/lockdep_types.h`（209 行）；实现：`kernel/locking/lockdep.c`（6,628 行）

### 检测的死锁类型

| 类型 | 示例 | 报告信息 |
|------|------|---------|
| AB-BA 死锁 | CPU0: lock(A)→lock(B); CPU1: lock(B)→lock(A) | "possible circular locking dependency" |
| IRQ 反转 | 进程: lock(A)→lock(B); IRQ: lock(B) | "inconsistent lock state" / "irq-safe->unsafe" |
| 双重获取 | lock(A)→lock(A) | "recursive locking detected" |
| 锁序违反 | lockdep 检测到与已知安全序不一致 | "WARNING: possible lock order inversion" |

### 工作原理

```
每次 lock_acquire():
    1. 计算 chain_key = hash(prev_chain_key, this_lock_class)
    2. 查找 chain_key 缓存 → 命中则跳过验证
    3. 未命中：
       a. 在依赖图中添加边：当前持有的每把锁 → 新锁
       b. BFS 搜索：从新锁出发，能否到达当前持有的任何锁？
       c. 能到达 → 存在环 → 报告死锁风险
       d. 不能 → 安全，缓存 chain_key
```

### 关键配置

| 配置项 | 作用 |
|--------|------|
| `CONFIG_PROVE_LOCKING` | 启用完整的锁序验证 |
| `CONFIG_LOCKDEP` | 启用 lockdep 基础设施 |
| `CONFIG_DEBUG_LOCK_ALLOC` | 启用锁分配跟踪 |
| `CONFIG_VALIDATE_LOCK_MANAGER` | 启用锁管理器验证 |
| `prove_locking` 内核参数 | 运行时关闭（`prove_locking=0`） |

### 使用注意

- lockdep 有显著的性能开销（约 2-5x 减慢锁操作）——仅用于开发/测试
- 最大支持 8,192 个锁类（`MAX_LOCKDEP_KEYS`）——超过后 lockdep 关闭
- `lockdep_assert_held()` 在 `CONFIG_PROVE_LOCKING` 未启用时退化为空操作
- 每个锁对象的 `lockdep_map` 通过 `spin_lock_init()` 等宏自动初始化
