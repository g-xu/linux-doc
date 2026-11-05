# jiffies 与 ktime

> B 级文档 · jiffies：`include/linux/jiffies.h`（443 行）· ktime：`include/linux/ktime.h`（234 行）

## 1 jiffies — 内核节拍计数器

### 定位

**功能**：内核最古老的时间表示方式——全局计数器，每次时钟中断加 1。频率由 `CONFIG_HZ` 决定（arm64 默认 250 Hz，即 4ms 粒度）。所有基于 tick 的超时、定时器、延迟计算都围绕它展开。

**实现方式**：`jiffies`（`volatile unsigned long`）是低 32/64 位计数器，`jiffies_64`（`u64`）是完整 64 位版本。初始值 `INITIAL_JIFFIES = -300*HZ`，让 32 位回绕在开机约 5 分钟后发生，尽早暴露回绕 bug。直接用 `>` `<` 比较有回绕风险，必须用 `time_after`/`time_before` 等宏——它们用 `(long)(b-a) < 0` 实现回绕安全比较。`msecs_to_jiffies` 利用 `__builtin_constant_p` 做编译期常量折叠。

**应用场景**：定时器超时（`timer wheel` 对齐）、简单轮询等待（`time_is_before_jiffies` 一行搞定）、驱动中的 ms 级超时、调度器 tick 统计等。与 `HZ` 粒度匹配的场景首选 jiffies——零开销，与内核定时器子系统天然对齐。

### 核心变量

头文件：`include/linux/jiffies.h`

| 变量 | 类型 | 说明 |
|------|------|------|
| `jiffies` | `volatile unsigned long` | 低 32/64 位计数器（`:80`） |
| `jiffies_64` | `u64` | 64 位计数器（`:79`），32 位平台需通过 `get_jiffies_64()` 安全读取 |
| `INITIAL_JIFFIES` | `unsigned long` | 初始值 = `-300*HZ`，让 32 位回绕在开机 5 分钟后发生，尽早暴露 bug（`:173`） |
| `MAX_JIFFY_OFFSET` | `unsigned long` | `(LONG_MAX >> 1) - 1`，最大合法偏移量（`:187`） |

### 回绕安全比较

jiffies 是 `unsigned long`，会回绕。直接用 `>` `<` 比较会出错。必须用以下宏：

| 宏 | 说明 | 备注 |
|----|------|------|
| `time_after(a, b)` | a 在 b 之后？ | 用 `(long)(b-a) < 0` 实现回绕安全（`:104`） |
| `time_before(a, b)` | a 在 b 之前？ | `time_after(b, a)` |
| `time_after_eq(a, b)` | a ≥ b？ | `:110` |
| `time_before_eq(a, b)` | a ≤ b？ | `:114` |
| `time_in_range(a, b, c)` | b ≤ a ≤ c？ | `:119` |
| `time_in_range_open(a, b, c)` | b ≤ a < c？ | `:126`，半开区间 |
| `time_is_before_jiffies(a)` | a 在 jiffies 之前？ | `time_after(jiffies, a)`（`:154`） |
| `time_is_after_jiffies(a)` | a 在 jiffies 之后？ | `:158` |

64 位版本：`time_after64` / `time_before64` 等，用于 `jiffies_64` 的比较（`:133-147`）。

### 时间转换

| API | 说明 | 备注 |
|-----|------|------|
| `msecs_to_jiffies(ms)` | 毫秒 → jiffies | 负值返回 `MAX_JIFFY_OFFSET`（无限超时）（`:363`） |
| `usecs_to_jiffies(us)` | 微秒 → jiffies | `:410` |
| `jiffies_to_msecs(j)` | jiffies → 毫秒 | `:290` |
| `jiffies_to_usecs(j)` | jiffies → 微秒 | `:291` |
| `jiffies_to_nsecs(j)` | jiffies → 纳秒 | inline，`jiffies_to_usecs * 1000`（`:293`） |
| `nsecs_to_jiffies(n)` | 纳秒 → jiffies | `:439` |

`msecs_to_jiffies` 利用 `__builtin_constant_p` 做编译期常量折叠——传入常量时编译期直接算出结果。

### 使用模式

```c
/* 超时等待：*/
unsigned long timeout = jiffies + msecs_to_jiffies(100);
while (!condition) {
    if (time_after(jiffies, timeout))
        return -ETIMEDOUT;
    cpu_relax();
}

/* 惯用简写：*/
if (time_is_before_jiffies(last_check + interval))
    do_periodic_work();
```

---

## 2 ktime — 纳秒精度时间

### 定位

**功能**：提供纳秒精度的时间表示和操作。jiffies 精度取决于 HZ（250 Hz = 4ms 粒度），不够用。`ktime_t` 以纳秒为单位的 `s64` 标量，覆盖高精度定时器、性能测量、驱动超时等场景。

**实现方式**：`ktime_t` 定义为 `typedef s64`（3.18+ 简化为纯标量，早期是 union）。所有操作（`ktime_add`/`ktime_sub`/`ktime_before` 等）都是简单的 s64 算术或比较，编译器可直接内联。时间来源通过 `ktime_get`（单调时钟）、`ktime_get_real`（实时时钟）、`ktime_get_boottime`（含休眠）、`ktime_get_raw`（原始硬件时钟）等获取，底层对接 `timekeeping` 子系统。

**应用场景**：高精度性能测量（`ktime_us_delta` 计算耗时）、高精度定时器（hrtimer）、调度延迟统计（不受 HZ 量化误差影响）、驱动中的 us/ns 级超时等待等。需要 us/ns 粒度时选 ktime，ms 级且与 timer wheel 对齐时选 jiffies。

### 核心类型

头文件：`include/linux/ktime.h`

```c
typedef s64 ktime_t;                      /* :29，纳秒为单位的标量 */
```

早期版本 `ktime_t` 是 union（标量 / 秒+纳秒两段），3.18+ 简化为纯 `s64` 纳秒。

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `ktime_set(secs, nsecs)` | 从秒+纳秒构造 ktime_t | 溢出时返回 `KTIME_MAX`（`:38`） |
| `ktime_to_ns(kt)` | 转为纳秒 | 直接返回 `kt`（`:80`） |
| `ktime_to_us(kt)` | 转为微秒 | `ktime_divns(kt, 1000)`（`:159`） |
| `ktime_to_ms(kt)` | 转为毫秒 | `ktime_divns(kt, 1000000)`（`:164`） |
| `ktime_sub(lhs, rhs)` | 时间差 | `(lhs) - (rhs)`（`:47`） |
| `ktime_add(lhs, rhs)` | 时间和 | `(lhs) + (rhs)`（`:50`） |
| `ktime_add_ns(kt, ns)` | 加纳秒 | `:62` |
| `ktime_sub_ns(kt, ns)` | 减纳秒 | `:68` |
| `ktime_add_us(kt, usec)` | 加微秒 | `:179` |
| `ktime_before(cmp1, cmp2)` | cmp1 < cmp2？ | `:123` |
| `ktime_after(cmp1, cmp2)` | cmp1 > cmp2？ | `:111` |
| `ktime_compare(cmp1, cmp2)` | 三值比较 | `<0 / 0 / >0`（`:95`） |
| `ktime_us_delta(later, earlier)` | 微秒差 | `:169`，性能测量常用 |
| `ktime_ms_delta(later, earlier)` | 毫秒差 | `:174` |
| `timespec64_to_ktime(ts)` | timespec64 → ktime_t | `:71` |
| `ktime_to_timespec64(kt)` | ktime_t → timespec64 | `:77` |
| `ns_to_ktime(ns)` | 纳秒 → ktime_t | 直接返回 `ns`（`:222`） |
| `ms_to_ktime(ms)` | 毫秒 → ktime_t | `ms * NSEC_PER_MSEC`（`:227`） |

### 获取当前时间

ktime 的取值来源在 `include/linux/timekeeping.h`（由 `ktime.h:232` 包含）：

| API | 说明 |
|-----|------|
| `ktime_get()` | 单调时钟（CLOCK_MONOTONIC），纳秒 |
| `ktime_get_real()` | 实时时钟（CLOCK_REALTIME），纳秒 |
| `ktime_get_boottime()` | 含休眠时间的单调时钟 |
| `ktime_get_raw()` | 未修正的原始硬件时钟 |

### 使用模式

```c
/* 性能测量：*/
ktime_t start = ktime_get();
do_work();
s64 elapsed_us = ktime_us_delta(ktime_get(), start);

/* 超时：*/
ktime_t deadline = ktime_add_ms(ktime_get(), 100);
while (!done) {
    if (ktime_after(ktime_get(), deadline))
        return -ETIMEDOUT;
}
```

## 3 jiffies vs ktime 选择

| 场景 | 推荐 | 原因 |
|------|------|------|
| 定时器超时（ms 级） | jiffies | 与 timer wheel 对齐，零开销 |
| 高精度测量（us/ns 级） | ktime | 纳秒粒度 |
| 调度 / 延迟统计 | ktime | 不受 HZ 量化误差影响 |
| 简单轮询等待 | jiffies | `time_is_before_jiffies` 一行搞定 |
