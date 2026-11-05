# 锁工具杂项 — lockref / static_call

> lockref 将 spinlock 和引用计数打包为一个 8 字节原子单元，用 cmpxchg 无锁更新。static_call 用代码补丁替代间接函数调用，避免 retpoline 开销。

---

## lockref

### 定位

**功能**：将 spinlock + 引用计数组合为单个 8 字节结构（`struct lockref`），通过 `cmpxchg64` 在无竞争时完全绕过 spinlock 直接原子更新引用计数。消除了路径查找中极高频的 get/put 操作的锁开销。

**实现方式**：`aligned_u64 lock_count` 将 4 字节 spinlock 状态和 4 字节 count 打包为一个 8 字节原子单元。快速路径用 `try_cmpxchg64` 检查 spinlock 未被他人持有并原子递增 count——成功则无锁完成更新；失败 100 次后回退到真正的 `spin_lock` + `count++` + `spin_unlock`。`USE_CMPXCHG_LOCKREF` 在 `CONFIG_ARCH_USE_CMPXCHG_LOCKREF` + `CONFIG_SMP` + `SPINLOCK_SIZE <= 4` 时启用。实现在 `lib/lockref.c`（193 行）。

**应用场景**：dentry 引用计数（`struct dentry::d_lockref`，`fs/dcache.c`）——路径查找中每次 `/` 分隔符都触发 get/put。inode 引用计数也使用。其他用户包括 Ceph 文件系统（`fs/ceph/dir.c`）、GFS2 集群文件系统（`fs/gfs2/glock.c`、`fs/gfs2/quota.c`）、命名空间文件系统（`fs/nsfs.c`）。全树约 60 处使用。

### 核心结构

`include/linux/lockref.h:25-35`：

```c
struct lockref {
    union {
#if USE_CMPXCHG_LOCKREF
        aligned_u64 lock_count;     // 8 字节：spinlock(4) + count(4)
#endif
        struct {
            spinlock_t lock;
            int count;
        };
    };
};
```

`USE_CMPXCHG_LOCKREF` 在 `CONFIG_ARCH_USE_CMPXCHG_LOCKREF` + `CONFIG_SMP` + `SPINLOCK_SIZE <= 4` 时启用。

### API

| API | 说明 | 备注 |
|-----|------|------|
| `lockref_get(lockref)` | 无条件递增引用 | 调用者必须已持有引用 |
| `lockref_get_not_zero(lockref)` | 递增（除非 count ≤ 0） | 返回 1=成功，0=失败 |
| `lockref_put_return(lockref)` | 递减，返回新值 | 返回 -1=错误 |
| `lockref_put_or_lock(lockref)` | 递减（除非 ≤ 1） | ≤ 1 时持锁返回 0，调用者处理 |
| `lockref_mark_dead(lockref)` | 标记为死亡 | count = -128；必须持锁 |
| `lockref_get_not_dead(lockref)` | 递增（除非 < 0） | 返回 1=成功，0=失败 |

头文件：`include/linux/lockref.h`；实现：`lib/lockref.c`（193 行）

### cmpxchg 快速路径

```c
// lib/lockref.c:11-27 (CMPXCHG_LOOP 宏，简化)
for (i = 0; i < 100; i++) {
    old = READ_ONCE(lockref->lock_count);
    if (old.lock != unlocked) break;   // spinlock 被持有 → 退出快速路径
    new = old;
    new.count++;                        // 修改 count
    if (try_cmpxchg64(&lockref->lock_count, &old, new))
        return;                         // 成功！无锁更新
}
// 回退到 spin_lock + count++ + spin_unlock
```

### 使用注意

- 全树仅约 60 处使用——主要在 dentry 和 inode 的引用计数
- 需要架构支持 `cmpxchg64`（arm64 支持）
- `lockref_get_or_lock` 在 6.1 中**不存在**（已被移除）

---

## static_call

### 定位

**功能**：可补丁的间接函数调用——用直接分支替代函数指针调用，避免 retpoline 带来的性能损失。调用目标可在运行时通过代码补丁更新，适合"很少更新、频繁调用"的回调路径。

**实现方式**：每个 `static_call()` 站点通过 trampoline 调用目标函数（编译为 `call trampoline → jmp target`）。`static_call_update()` 通过代码补丁修改 trampoline 的分支目标（或在内联模式下直接补丁调用站点本身）。三级实现：Inline（`CONFIG_HAVE_STATIC_CALL_INLINE`，直接补丁调用站点，最快）、Trampoline（`CONFIG_HAVE_STATIC_CALL`，补丁 trampoline 分支）、通用（退化为函数指针）。arm64 支持 trampoline 模式。实现在 `include/linux/static_call.h`（350 行）和 `kernel/static_call.c`。

**应用场景**：调度器回调（`kernel/sched/`）、perf 事件处理（`kernel/events/core.c`）、tracepoint 回调（`kernel/tracepoint.c`）、BPF trampoline/dispatcher（`kernel/bpf/trampoline.c`、`kernel/bpf/dispatcher.c`）、kprobes（`kernel/kprobes.c`）、CPU 热插拔回调、安全模块 hook、Xen 时间回调（`drivers/xen/time.c`）、arm64 半虚拟化（`arch/arm64/kernel/paravirt.c`）等。全树约 312 处 `static_call` 使用。

### 核心概念

```c
// 定义（include/linux/static_call.h:185）
DEFINE_STATIC_CALL(my_call, default_func);

// 调用
static_call(my_call)(arg1, arg2);
// 编译为: call my_call_trampoline → jmp default_func

// 更新目标
static_call_update(my_call, new_func);
// 补丁 trampoline: jmp new_func（或补丁调用站点本身）
```

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_STATIC_CALL(name, func)` | 定义 + 初始目标 | |
| `DEFINE_STATIC_CALL_NULL(name, func)` | 定义 + NULL 初始目标 | |
| `DEFINE_STATIC_CALL_RET0(name, func)` | 定义 + return 0 初始目标 | x86 优化为清 RAX |
| `static_call(name)(args...)` | 调用当前目标 | |
| `static_call_cond(name)(args...)` | NULL 安全调用 | 目标为 NULL 时不调用 |
| `static_call_update(name, func)` | 更新目标函数 | 代码补丁 |
| `static_call_query(name)` | 查询当前目标 | `READ_ONCE(key.func)` |
| `DECLARE_STATIC_CALL(name, func)` | 声明（头文件用） | |
| `EXPORT_STATIC_CALL(name)` | 导出 | |

头文件：`include/linux/static_call.h`（350 行）；实现：`kernel/static_call.c`（8 行，仅 fallback）

### 三级实现

| 级别 | 配置 | 机制 |
|------|------|------|
| Inline | `CONFIG_HAVE_STATIC_CALL_INLINE` | 直接补丁调用站点（最快） |
| Trampoline | `CONFIG_HAVE_STATIC_CALL` | 补丁 trampoline 分支 |
| 通用 | 无特殊配置 | 函数指针（无代码补丁） |

### 使用注意

- 更新操作（`static_call_update`）代价高——涉及代码补丁和 IPI
- 适合"很少更新、频繁调用"的场景
- 不适合频繁切换目标的场景——用函数指针
- arm64 支持 `CONFIG_HAVE_STATIC_CALL`（trampoline 模式）
