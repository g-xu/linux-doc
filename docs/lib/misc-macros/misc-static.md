# 杂项：static_key / jump_label — 零开销条件分支

> B 级文档。通过运行时自修改代码实现零开销的条件分支。

## static_key / jump_label — 零开销条件分支

### 定位

**功能**：通过运行时自修改代码实现零开销的条件分支。默认路径（key 为 false）在代码中只有一条 NOP 指令，不产生任何分支开销；启用路径时 NOP 被替换为跳转指令，跳转到 out-of-line 代码块。适用于"绝大多数时间关闭、偶尔启用"的特性开关。

**实现方式**：`kernel/jump_label.c` 实现运行时补丁引擎——`static_branch_enable` 将 NOP 替换为跳转指令，`static_branch_disable` 反向操作。支持引用计数（`static_branch_inc`/`dec`），多个使用者可共享同一 key。arm64 上 NOP 和 JMP 都是 4 字节指令，可直接原地替换；修改时需要全系统 `stop_machine()` 确保所有 CPU 不正在执行被修改的指令。

**应用场景**：全树约 279 个文件使用。典型消费者包括：tracepoint 开关（tracepoint 未激活时零开销）、调度器特性开关（`sched_feat`）、cgroup 特性开关、网络快速路径优化等。是内核中最重要的性能优化机制之一。

### 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `include/linux/jump_label.h` | 537 | 公开 API 和数据结构 |
| `kernel/jump_label.c` | 902 | 运行时补丁引擎 |

总计 1439 行。

### 核心概念

jump_label 允许在运行时将 NOP 指令替换为跳转指令（或反之），实现**零开销的条件分支**：

- 默认路径（key 为 false）：代码中只有一条 NOP，零分支开销
- 启用路径（key 为 true）：NOP 被替换为跳转到 out-of-line 代码块

### 核心 API

| API | 行号 | 说明 |
|-----|------|------|
| `DEFINE_STATIC_KEY_TRUE(name)` | 370 | 定义初始为 true 的 key |
| `DEFINE_STATIC_KEY_FALSE(name)` | 379 | 定义初始为 false 的 key |
| `static_branch_likely(&key)` | 482 | 预期为 true 的分支检查 |
| `static_branch_unlikely(&key)` | 494 | 预期为 false 的分支检查 |
| `static_branch_enable(&key)` | 530 | 启用（NOP → JMP） |
| `static_branch_disable(&key)` | 531 | 禁用（JMP → NOP） |
| `static_branch_inc(&key)` | 521 | 引用计数 +1 |
| `static_branch_dec(&key)` | 522 | 引用计数 -1 |

### 使用示例

```c
DEFINE_STATIC_KEY_FALSE(feature_enabled);

void do_work(void)
{
    // 默认路径：只有一条 NOP，零开销
    if (static_branch_unlikely(&feature_enabled)) {
        // 不常执行的代码，放在 out-of-line 位置
        expensive_feature();
    }
}

// 运行时启用
void enable_feature(void)
{
    static_branch_enable(&feature_enabled);
}
```

### arm64 实现

arm64 使用 `arch_jump_label_transform()` 修改指令。NOP 和 JMP 都是 4 字节指令，可以直接原地替换。需要全系统 `stop_machine()` 确保所有 CPU 不正在执行被修改的指令。

### 使用统计

全树约 279 个文件使用 static_key/jump_label。典型使用者：tracepoint、调度器特性开关、cgroup 特性开关。
