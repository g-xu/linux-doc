# 杂项：fault-inject — 故障注入框架

> B 级文档。通用的概率性故障注入框架，用于测试内核在资源不足时的行为。

### 定位

**功能**：提供通用的概率性故障注入框架，用于测试内核在资源不足（内存分配失败、I/O 错误等）情况下的容错和恢复行为。开发者可配置故障概率、次数、间隔和调用栈过滤条件，验证错误处理路径的正确性。

**实现方式**：核心是 `struct fault_attr`（`include/linux/fault-inject.h:16-32`），记录概率、间隔、剩余次数、空间、调用地址范围等参数。`should_fail()`（`lib/fault-inject.c`）按顺序检查次数耗尽、间隔、空间、任务过滤、地址范围，最后用 `prandom_u32()` 做概率判断。通过 `fault_create_debugfs_attr()` 暴露 debugfs 控制接口，运行时可动态调整所有参数。

**应用场景**：`should_fail` 在 28 个文件的 41 处被调用，主要消费者包括 `block/blk-core.c`（块设备 I/O 故障注入）、`drivers/mmc/core/core.c`（MMC 请求故障）、`drivers/nvme/host/fault_inject.c`（NVMe 故障模拟）、`drivers/ufs/core/ufs-fault-injection.c`（UFS 故障注入）、`kernel/fail_function.c`（函数级故障注入）、`kernel/futex/core.c`（futex 故障注入）、`drivers/gpu/drm/i915/`（GPU 驱动自测试）、`drivers/gpu/drm/msm/msm_drv.c`（MSM DRM 故障模拟）。

## 1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `lib/fault-inject.c` | 240 | 核心：`should_fail()` 决策逻辑 |
| `include/linux/fault-inject.h` | 85 | 对外 API 与 `struct fault_attr` |
| `lib/Makefile` | — | `obj-$(CONFIG_FAULT_INJECTION) += fault-inject.o` |

## 2 核心数据结构

**`struct fault_attr`**（`include/linux/fault-inject.h:16-32`）：

```c
struct fault_attr {
    unsigned long probability;    // 失败概率（percent）
    unsigned long interval;       // 每次失败之间的最小间隔
    atomic_t times;               // 剩余失败次数（0 = 不再失败）
    atomic_t space;               // 剩余空间（用于大小感知注入）
    unsigned long verbose;        // 日志详细度
    bool task_filter;             // 是否只影响特定任务
    unsigned long stacktrace_depth; // 栈深度过滤
    unsigned long require_start;  // 只在该地址范围内失败
    unsigned long require_end;
    unsigned long reject_start;   // 在该地址范围内不失败
    unsigned long reject_end;

    unsigned long count;          // 已触发次数
    struct ratelimit_state ratelimit_state;
    struct dentry *dname;         // debugfs 目录项
};
```

**`FAULT_ATTR_INITIALIZER`**（`fault-inject.h:38-46`）：默认初始化器：

```c
#define FAULT_ATTR_INITIALIZER {
    .interval = 1,
    .times = ATOMIC_INIT(1),
    .require_end = ULONG_MAX,
    .stacktrace_depth = 32,
    .ratelimit_state = RATELIMIT_STATE_INIT_DISABLED,
    .verbose = 2,
    .dname = NULL,
}
```

## 3 核心 API

头文件：`include/linux/fault-inject.h`；实现：`lib/fault-inject.c`

| API | 说明 | 备注 |
|-----|------|------|
| `DECLARE_FAULT_ATTR(name)` | 声明并初始化 `fault_attr` | 使用 `FAULT_ATTR_INITIALIZER` |
| `setup_fault_attr(attr, str)` | 从字符串解析参数 | 格式：`probability,interval,times,space` |
| `should_fail(attr, size)` | 判断是否应注入故障 | 核心决策函数 |
| `should_fail_ex(attr, size, flags)` | 带标志的变体 | `FAULT_NOWARN` 抑制日志 |
| `fault_create_debugfs_attr(name, parent, attr)` | 创建 debugfs 控制接口 | 需 `CONFIG_FAULT_INJECTION_DEBUG_FS` |

## 4 决策逻辑

`should_fail()` 的判断流程（`lib/fault-inject.c`）：

```
should_fail(attr, size)
  │
  ├─ atomic_read(&attr->times) <= 0 → 返回 false（次数耗尽）
  ├─ 检查 interval：距上次失败是否足够久
  ├─ 检查 space：剩余空间是否足够
  ├─ 检查 task_filter：当前任务是否匹配
  ├─ 检查 require_start/end：调用地址是否在要求范围内
  ├─ 检查 reject_start/end：调用地址是否在排除范围内
  ├─ 概率判断：prandom_u32() % 100 < probability
  │    └─ 不满足 → 返回 false
  └─ 全部通过 → 返回 true
       ├─ atomic_dec(&attr->times)
       ├─ attr->count++
       └─ 若 verbose → 打印日志（含栈回溯）
```

## 5 预定义的故障注入点

头文件：`include/linux/fault-inject.h:72-82`

| 注入点 | 函数 | 触发场景 |
|--------|------|----------|
| 页面分配失败 | `should_fail_alloc_page(gfp_mask, order)` | `mm/page_alloc.c` 分配页面时 |
| slab 分配失败 | `should_failslab(s, gfpflags)` | `mm/slab_common.c` slab 分配时 |

这些注入点通过 `CONFIG_FAIL_PAGE_ALLOC` / `CONFIG_FAILSLAB` 控制。

## 6 debugfs 接口

`fault_create_debugfs_attr()` 在 debugfs 下创建控制文件：

```
/sys/kernel/debug/<name>/
├── probability      # 读写：失败概率
├── interval         # 读写：间隔
├── times            # 读写：剩余次数
├── space            # 读写：空间
├── verbose          # 读写：日志详细度
├── task-filter      # 读写：任务过滤
├── stacktrace-depth # 读写：栈深度
├── require-start    # 读写：要求范围起始
├── require-end      # 读写：要求范围结束
├── reject-start     # 读写：排除范围起始
├── reject-end       # 读写：排除范围结束
└── count            # 只读：已触发次数
```

## 7 使用示例

```bash
# 每 100 次页面分配失败 1 次
echo 1 > /sys/kernel/debug/fail_page/probability
echo 100 > /sys/kernel/debug/fail_page/interval

# 只失败 5 次
echo 5 > /sys/kernel/debug/fail_page/times

# 观察结果
cat /sys/kernel/debug/fail_page/count
```

## 8 使用统计

- `should_fail` 在树内约 27 处直接调用（不含 `should_fail_alloc_page` / `should_failslab` 等预定义点）
- `DECLARE_FAULT_ATTR` 约 15 处
- `fault_create_debugfs_attr` 约 12 处
- 主要使用者：`FAIL_FUNCTION`（函数级故障注入）、`FAIL_MMC_REQUEST`、`FAIL_FUTEX`、`FAIL_MAKE_REQUEST`（块设备）、驱动自定义注入点
- `CONFIG_FAULT_INJECTION` 在 arm64 defconfig 中默认关闭
