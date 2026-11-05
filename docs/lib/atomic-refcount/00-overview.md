---
title: 总览
---

# 原子操作与引用计数 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **atomic_t** | 硬件原子操作（加减、CMPXCHG、位运算） | 所有原子原语的基础；有符号 32 位；不做引用计数 | [atomic.md](atomic.md) |
| **refcount_t** | 安全引用计数（溢出/下溢保护） | 饱和语义防止 UAF；宁泄漏不溢出；`refcount_inc_not_zero` 安全获取 | [refcount.md](refcount.md) |
| **kref** | 带释放回调的引用计数 | `kref_put(kref, release)` 到 0 自动调回调；底层是 refcount_t 包装 | [misc-refcount.md](misc-refcount.md) |
| **atomic_bitops** | 位图的原子位操作 | `set_bit` 无返回值=relaxed；`test_and_set_bit` 有返回值=全序 | [misc-atomic.md](misc-atomic.md) |
| **local_t** | 单 CPU 原子计数器 | arm64 上退化为 `atomic_long_t` 包装，无性能优势；需关抢占或关中断 | [misc-atomic.md](misc-atomic.md) |
| **percpu_counter** | 批量 per-CPU 计数器 | 高频更新+允许近似值；快速读偏差最大 `batch × nr_cpus` | [misc-counters.md](misc-counters.md) |
| **u64_stats_sync** | 64 位统计值撕裂读保护 | arm64 上全部退化为空操作（64 位原生原子）；仅 32 位平台需要 seqcount | [misc-counters.md](misc-counters.md) |

---

## §2 选型决策

```
需要跨 CPU 并发安全的数值操作？
│
├─ 是"引用计数"（对象生命周期管理）？
│  │
│  ├─ 是 → 需要标准化释放回调？
│  │       │
│  │       ├─ 是 → kref
│  │       │        （kref_put(kref, release) 自动调用回调）
│  │       │
│  │       └─ 否 → refcount_t
│  │                （refcount_dec_and_test + 手动 kfree）
│  │
│  └─ 否 → 是"通用计数器/状态标志"？
│          │
│          ├─ 是 → 需要位操作？
│          │       │
│          │       ├─ 是 → atomic_bitops
│          │       │        （set_bit / test_and_set_bit）
│          │       │
│          │       └─ 否 → 需要 CMPXCHG / 无锁算法？
│          │               │
│          │               ├─ 是 → atomic_t
│          │               │        （atomic_cmpxchg / atomic_xchg）
│          │               │
│          │               └─ 否 → 高频更新 + 允许近似值？
│          │                       │
│          │                       ├─ 是 → percpu_counter
│          │                       │        （批量刷新，快速近似读）
│          │                       │
│          │                       └─ 否 → atomic_t
│          │                                （简单原子计数器）
│          │
│          └─ 是"64 位统计值保护"？
│                  │
│                  └─ u64_stats_sync
│                       （arm64 上为空操作，仅 32 位需要）
│
└─ 否 → 仅单 CPU 访问（关中断/关抢占）？
        │
        └─ local_t
             （arm64 上退化为 atomic_long_t，无额外优势）
```

### 关键区分点

| 容易混淆的对 | 区分依据 |
|-------------|---------|
| atomic_t vs refcount_t | atomic_t 通用计数（可环绕）；refcount_t 引用计数（饱和保护，宁泄漏不 UAF） |
| refcount_t vs kref | refcount_t 手动 dec_and_test + kfree；kref 自动调 release 回调 |
| atomic_t vs atomic_bitops | atomic_t 操作整个 32 位值；atomic_bitops 操作值中的单个位 |
| atomic_t vs percpu_counter | atomic_t 精确但高竞争；percpu_counter 近似但高吞吐 |
| local_t vs atomic_t | local_t 仅单 CPU（关抢占保证）；atomic_t 跨 CPU 安全 |
| u64_stats_sync vs atomic64_t | u64_stats_sync 保护 64 位统计值读一致性；atomic64_t 提供 64 位原子操作 |

---

## §3 核心对比表

### 3.1 类型与操作对比

| 模块 | 底层类型 | 位宽 | 溢出行为 | 内存序（默认） | arm64 实现 |
|------|---------|------|---------|--------------|-----------|
| atomic_t | `int` | 32 位有符号 | 环绕 | relaxed（无返回值操作） | `stadd` (LSE) / `ldxr+stxr` (LL/SC) |
| atomic_long_t | `long` | 64 位（arm64） | 环绕 | 同 atomic_t | 同 atomic_t |
| atomic64_t | `s64` | 64 位 | 环绕 | 同 atomic_t | `caspal` (LSE) |
| refcount_t | `atomic_t` | 32 位 | 饱和到 0x80000000 | release（inc）/ acq_rel（dec_and_test） | `staddl` / `caspal+ldar` |
| kref | `refcount_t` | 32 位 | 同 refcount_t | 同 refcount_t | 同 refcount_t |
| local_t | `atomic_long_t` | 64 位（arm64） | 环绕 | 无额外保证 | 退化为 atomic_long_t |
| percpu_counter | `s64` 全局 + `s32` per-CPU | 64/32 位 | 全局不易溢出；per-CPU batch 内安全 | 取决于读/写路径 | per-CPU 快速路径无全局原子 |
| u64_stats_sync | `u64` + seqcount | 64 位 | N/A | arm64 空操作（64 位原生原子） | 空操作 |
| atomic_bitops | `unsigned long *` | 位图 | N/A | `set_bit`=relaxed; `test_and_set_bit`=全序 | `ldset` (LSE) / `ldxr+stxr` (LL/SC) |

### 3.2 内存序变体

| 层次 | 默认内存序 | arm64 实现 |
|------|-----------|-----------|
| `atomic_read`/`atomic_set` | 无保证（`READ_ONCE`/`WRITE_ONCE`） | `ldr`/`str` |
| `atomic_add`/`atomic_inc`（无返回值） | relaxed | `stadd`（LSE）/ `ldxr`+`stxr`（LL/SC） |
| `atomic_add_return`/`atomic_cmpxchg` | 全序（acquire+release） | `ldaddal`/`caspal`（LSE） |
| `refcount_inc` | release | `staddl` |
| `refcount_dec_and_test`（成功） | release + acquire | `caspal` + `ldar` |

### 3.3 饱和 vs 环绕语义

| 模块 | 语义 | 后果 |
|------|------|------|
| atomic_t | 有符号环绕 | 编译器用 `-fno-strict-overflow`；可能产生逻辑错误 |
| refcount_t | 饱和到 0x80000000 | 后续操作被拒绝 + `WARN_ONCE`；对象永不释放（宁泄漏不 UAF） |
| percpu_counter | 全局 `s64` 不易溢出 | per-CPU `s32` 在 batch 范围内安全 |

---

## §4 使用规模

以下数据来自 `rg -c PATTERN -g '*.c' -g '*.h'` 对 `/workspace/safety/linux-6.1.y` 全树的精确统计：

| 模块 | 涉及文件数 | 调用/出现总次数 | grep 命令 |
|------|-----------|---------------|----------|
| atomic_t | 1653 | 3838 | `rg -c '\batomic_t\b' -g '*.c' -g '*.h'` |
| atomic_read | 1799 | 5863 | `rg -c '\batomic_read\b' -g '*.c' -g '*.h'` |
| atomic_inc | 1137 | 2536 | `rg -c '\batomic_inc\b' -g '*.c' -g '*.h'` |
| set_bit | 1985 | 7086 | `rg -c '\bset_bit\b' -g '*.c' -g '*.h'` |
| test_and_set_bit | 781 | 1594 | `rg -c '\btest_and_set_bit\b' -g '*.c' -g '*.h'` |
| refcount_t | 373 | 576 | `rg -c '\brefcount_t\b' -g '*.c' -g '*.h'` |
| kref_put | 463 | 1182 | `rg -c '\bkref_put\b' -g '*.c' -g '*.h'` |
| kref_get | 412 | 700 | `rg -c '\bkref_get\b' -g '*.c' -g '*.h'` |
| percpu_counter | 52 | 162 | `rg -c '\bpercpu_counter\b' -g '*.c' -g '*.h'` |
| u64_stats_sync | 95 | 173 | `rg -c '\bu64_stats_sync\b' -g '*.c' -g '*.h'` |
| local_t | 27 | 130 | `rg -c '\blocal_t\b' -g '*.c' -g '*.h'` |

**解读**：
- atomic_t 是最广泛的并发原语（1653 文件、3838 次类型声明），atomic_read 达 5863 次
- set_bit（7086 次）是最高频的原子操作，广泛用于标志位管理
- refcount_t（373 文件）正在逐步替代 atomic_t 做引用计数，但迁移尚未完成
- kref（1182 次 kref_put + 700 次 kref_get）是设备模型的标准引用计数方式
- percpu_counter（52 文件）使用量较低，主要用于文件系统等需要精确统计的场景
- local_t（27 文件）在 arm64 上无性能优势，使用量持续下降
- u64_stats_sync（95 文件）主要用于网络/块设备统计

---

## §5 设计模式速览

### 5.1 底层依赖链

```
kref → refcount_t → atomic_t → arch_atomic_*（arm64 LSE/LL-SC）
```

所有模块最终依赖 arm64 的硬件原子指令。`refcount_t` 和 `kref` 是 `atomic_t` 的语义包装，不引入新的硬件原语。

### 5.2 仪器化层

所有 `atomic_t` 操作经过 `atomic-instrumented.h`（2222 行）的 KASAN/KCSAN 包装。`refcount_t` 和 `kref` 通过 `atomic_*` 间接享受同样的仪器化覆盖。

### 5.3 安全获取模式

引用计数的"安全获取"是防止 UAF 的关键：

```c
/* 正确：在对象仍有效时安全获取引用 */
if (!refcount_inc_not_zero(&obj->refcnt))
    return -ENOENT;  /* 对象已在释放中 */

/* 错误：先读后增，存在 TOCTOU 竞争 */
if (atomic_read(&obj->refcnt) > 0)
    atomic_inc(&obj->refcnt);  /* 可能对象已释放！ */
```

- `refcount_inc_not_zero`：原子地检查非零并递增
- `kref_get_unless_zero`：kref 版本，底层调 `refcount_inc_not_zero`

### 5.4 per-CPU 批量化模式

percpu_counter 的核心设计：
- 每个 CPU 维护本地 `s32` 计数器
- 本地计数超过 `batch` 阈值时批量刷入全局 `s64`
- 快速读：只读全局值 + 遍历在线 CPU 的本地值（近似）
- 精确读：获取 `percpu_counter` 锁，逐个 CPU 求和（慢）

### 5.5 最常见的误用模式

| 误用 | 正确做法 |
|------|---------|
| `atomic_t` 做引用计数 → UAF | 用 `refcount_t` |
| `atomic_read` 当同步点 | 用 `atomic_read_acquire` 或加 `smp_mb()` |
| `atomic_inc` 在对象已释放后调用 | 用 `refcount_inc_not_zero` / `kref_get_unless_zero` |
| `atomic_set` 与并发 RMW 竞争 | 用 `atomic_xchg`（全序） |

---

## §6 锁模型对比

| 模块 | 并发保护机制 | 跨 CPU 安全 | 中断上下文安全 | 性能特征 |
|------|------------|------------|--------------|---------|
| atomic_t | 硬件原子指令（LSE/LL-SC） | 是 | 是 | 高竞争时缓存行弹跳 |
| refcount_t | 硬件原子指令 + 饱和检测 | 是 | 是 | 比 atomic_t 多一次比较 |
| kref | refcount_t 包装 | 是 | 是 | 与 refcount_t 相同 |
| atomic_bitops | 硬件原子位操作 | 是 | 是 | 与 atomic_t 类似 |
| local_t | 关抢占/关中断（软件保证） | **否** | 需关中断 | arm64 无额外优势 |
| percpu_counter | per-CPU 分片 + 全局锁（精确读时） | 是（快速路径无全局原子） | 是（快速路径） | 高吞吐、近似读 |
| u64_stats_sync | seqcount（32 位）/ 空操作（arm64） | 写者需串行化 | 是 | arm64 零开销 |

---

## §7 组合使用模式

### 7.1 kref + refcount_t + atomic_t：设备模型引用计数

```
struct device → kref → refcount_t → atomic_t → arm64 LSE
```

- `include/linux/device.h` 中 `struct device` 包含 `struct kref`
- `get_device()` → `kref_get()` → `refcount_inc()` → `atomic_inc()`
- `put_device()` → `kref_put()` → `refcount_dec_and_test()` → `atomic_dec_and_test()`
- 到 0 时自动调用 `device_release` 回调

### 7.2 atomic_t + percpu：网络统计计数器

```
struct pcpu_sw_netstats（per-CPU）→ atomic_t / u64_stats_sync
```

- `include/linux/netdevice.h` 中网络设备的 per-CPU 统计
- 写路径：`this_cpu_inc` 更新 per-CPU 计数器（无全局原子操作）
- 读路径：遍历所有 CPU 求和（`u64_stats_fetch_begin` 保护一致性）
- arm64 上 u64_stats_sync 为空操作，64 位写本身原子

### 7.3 atomic_bitops + spinlock：页标志管理

```
struct page → flags（unsigned long）→ set_bit / clear_bit / test_and_set_bit
```

- `include/linux/mm.h` 中 `struct page.flags` 编码多个页状态位
- `SetPageLocked` → `set_bit(PG_locked, &page->flags)`
- `TestSetPageLocked` → `test_and_set_bit(PG_locked, &page->flags)`（全序，有返回值）
- 位操作本身原子，但复合操作（如检查+修改多位）可能需要额外锁

### 7.4 percpu_counter + atomic_t：文件系统 inode 计数

```
struct super_block → s_op → percpu_counter（inode 计数）
```

- `fs/super.c` 中 `struct super_block` 包含多个 `percpu_counter`
- 快速路径：`percpu_counter_inc` 更新本地计数器
- 精确查询（如 statfs）：`percpu_counter_sum` 遍历所有 CPU
- 底层仍用 `atomic_t` 保护全局 `s64` 计数器

---

## §8 阅读顺序建议

### 路线 A：驱动开发（从最常用到最少用）

1. [atomic.md](atomic.md) — atomic_t 是所有原子操作的基础
2. [refcount.md](refcount.md) — refcount_t 安全引用计数
3. [misc-refcount.md](misc-refcount.md) — kref 带回调引用计数
4. [misc-atomic.md](misc-atomic.md) — atomic_bitops 位操作
5. [misc-counters.md](misc-counters.md) — percpu_counter + u64_stats_sync

### 路线 B：内核核心开发（并发原语深入）

1. [atomic.md](atomic.md) — arm64 LSE/LL-SC 实现细节
2. [refcount.md](refcount.md) — 饱和语义与内存序
3. [misc-atomic.md](misc-atomic.md) — 位操作内存序差异
4. [misc-counters.md](misc-counters.md) — 批量刷新机制
5. [misc-refcount.md](misc-refcount.md) — kref_put_lock 等高级模式

### 路线 C：安全审计（UAF 防护重点）

1. [refcount.md](refcount.md) — 饱和语义防止 UAF
2. [misc-refcount.md](misc-refcount.md) — kref 安全获取模式
3. [atomic.md](atomic.md) — atomic_read 无内存序保证的陷阱
4. [misc-atomic.md](misc-atomic.md) — local_t 在 arm64 的退化
5. [misc-counters.md](misc-counters.md) — u64_stats_sync 撕裂读保护

---

## §9 文档索引

| 文档 | 覆盖模块 | 主要内容 |
|------|---------|---------|
| [atomic.md](atomic.md) | atomic_t, atomic_long_t, atomic64_t | 三层架构、LSE vs LL/SC、四种内存序变体、API 速查 |
| [refcount.md](refcount.md) | refcount_t | 饱和语义、溢出/下溢检测、内存序、安全获取模式 |
| [misc-atomic.md](misc-atomic.md) | atomic_bitops, local_t | 位操作内存序差异、local_t 在 arm64 的退化 |
| [misc-counters.md](misc-counters.md) | percpu_counter, u64_stats_sync | 批量刷新机制、u64_stats 在 arm64 的空操作 |
| [misc-refcount.md](misc-refcount.md) | kref | kref_put 回调、与 refcount_t 的区别、kref_put_lock |
