---
title: 调试方法
---

# IOMMU 子系统 — 调试

> **定位**：编译期开关、tracepoint、sysfs、debugfs、错误码分诊
>
> **目标架构**：arm64
>
> **前置阅读**：`01-api.md`（API 速查）、`03-internals.md`（核心流程）

---

## §1 编译期开关

### §1.1 Kconfig

| 选项 | 默认 | 作用 |
|------|------|------|
| `CONFIG_IOMMU_SUPPORT` | y | IOMMU 框架总开关 |
| `CONFIG_ARM_SMMU_V3` | y | SMMUv3 驱动 |
| `CONFIG_IOMMU_DEBUGFS` | n | debugfs 接口 |
| `CONFIG_IOMMU_DEFAULT_DMA_STRICT` | y | 默认严格 DMA 模式 |
| `CONFIG_IOMMU_DEFAULT_DMA_LAZY` | n | 默认延迟 DMA 模式 |
| `CONFIG_IOMMU_DEFAULT_PASSTHROUGH` | n | 默认恒等映射 |

### §1.2 日志可见性等级

| 日志 | 级别 | 可见性 |
|------|------|--------|
| `dev_err` | KERN_ERR | 始终输出 |
| `dev_warn` | KERN_WARNING | 始终输出 |
| `dev_info` | KERN_INFO | 始终输出 |
| `pr_debug` | KERN_DEBUG | 需 `DEBUG` 或 `CONFIG_DYNAMIC_DEBUG` |
| `dev_err_ratelimited` | KERN_ERR | 限速输出 |
| `dev_warn_ratelimited` | KERN_WARNING | 限速输出 |

**限速变体**：`drivers/iommu/iommu.c:1327` 的 `dev_warn_ratelimited` 用于高频故障场景，避免日志洪泛。

---

## §2 Tracepoint

### §2.1 定义位置

**文件**：`drivers/iommu/iommu-traces.c`

**7 个 tracepoint**：

| Tracepoint | 事件 | 触发位置 |
|-----------|------|---------|
| `add_device_to_group` | 设备加入 group | `iommu_group_add_device` |
| `remove_device_from_group` | 设备移出 group | `iommu_group_remove_device` |
| `attach_device_to_domain` | 设备 attach domain | `__iommu_attach_device` |
| `detach_device_from_domain` | 设备 detach domain | `__iommu_detach_device` |
| `map` | IOVA→PA 映射 | `__iommu_map` |
| `unmap` | IOVA→PA 解映射 | `__iommu_unmap` |
| `io_page_fault` | I/O 页故障 | `iommu_report_device_fault` |

### §2.2 启用方法

```bash
# 启用所有 IOMMU tracepoint
echo 1 > /sys/kernel/debug/tracing/events/iommu/enable

# 启用特定 tracepoint
echo 1 > /sys/kernel/debug/tracing/events/iommu/map/enable

# 查看输出
cat /sys/kernel/debug/tracing/trace
```

### §2.3 示例输出

```
           <...>-1234  [001] .... 12345.678901: map: d=0000:01:00.0 iova=0x100000 pa=0x200000 size=0x1000
           <...>-1234  [001] .... 12345.678902: unmap: d=0000:01:00.0 iova=0x100000 size=0x1000
```

**字段**：

- `d`：设备 PCI BDF
- `iova`：I/O 虚拟地址
- `pa`：物理地址（仅 map）
- `size`：映射大小

### §2.4 成对 tracepoint 注意

`map` 和 `unmap` 成对出现。如果只看到 `map` 没有 `unmap`，**不一定是泄漏**——可能是：

1. 设备还在使用映射
2. 系统关闭时批量释放
3. tracepoint 条件未满足

---

## §3 Sysfs 接口

### §3.1 Group 属性

**路径**：`/sys/kernel/iommu_groups/<id>/`

| 文件 | 权限 | 内容 |
|------|------|------|
| `type` | 0644 | 当前域类型（可写切换） |
| `reserved_regions` | 0444 | 保留区域列表 |
| `name` | 0444 | group 名称（可选） |
| `devices/` | — | 链接到设备 |

### §3.2 `type` 属性

**读**：

```bash
cat /sys/kernel/iommu_groups/0/type
# 输出：DMA
```

**写**（切换域类型）：

```bash
echo DMA_FQ > /sys/kernel/iommu_groups/0/type
```

**可写值**：

- `DMA`：DMA 域
- `DMA_FQ`：DMA 域 + flush queue
- `IDENTITY`：恒等映射
- `BLOCKED`：阻止所有 DMA

**限制**：

- group 只能有一个设备（`:2897-2909`）
- 设备必须已解绑驱动（`:3082`）

**错误消息**：

- `"Group not assigned to default domain"`：group 无 default_domain
- `"Cannot change default domain: Group has more than one device"`：多设备 group
- `"Device cannot be in %s domain"`：设备不支持该域类型

---

## §4 Debugfs

### §4.1 启用

**Kconfig**：`CONFIG_IOMMU_DEBUGFS=y`

**警告**：启用时启动日志有强烈 NOTICE（`drivers/iommu/iommu-debugfs.c:40-50`），提示安全风险。

### §4.2 目录结构

```
/sys/kernel/debug/iommu/
└── <driver-specific>/
    └── ...
```

**注意**：核心层只创建顶层目录，具体内容靠驱动实现。arm-smmu-v3 当前未实现 debugfs。

### §4.3 用途

- 暴露内部数据结构
- 调试页表状态
- 测试故障注入

---

## §5 内核参数

### §5.1 `iommu.passthrough`

**值**：0（默认，DMA 域）或 1（恒等映射）

**作用**：设置 `iommu_def_domain_type`

**代码**：`drivers/iommu/iommu.c:37`

### §5.2 `iommu.strict`

**值**：0（延迟）或 1（严格，默认）

**作用**：设置 `iommu_dma_strict`

**代码**：`drivers/iommu/iommu.c:38`

**影响**：严格模式下每次 unmap 立即刷新 TLB；延迟模式用 flush queue 批量刷新。

### §5.3 `iommu.forcedac`

**值**：0（默认）或 1（强制 DAC）

**作用**：强制使用双地址周期（DAC）

---

## §6 错误码分诊

### §6.1 常见错误码

| 错误码 | 含义 | 触发场景 |
|--------|------|---------|
| `-ENODEV` | 设备无 IOMMU | `ops == NULL`、probe 失败 |
| `-EBUSY` | 资源占用 | 重复注册、group 已 attach |
| `-EINVAL` | 参数无效 | 域类型不支持、对齐错误 |
| `-ENOMEM` | 内存不足 | kzalloc 失败 |
| `-EFAULT` | 地址无效 | IOVA/PA 未对齐 |
| `-EOVERFLOW` | 溢出 | PASID 超出范围 |

### §6.2 同码多源

#### `-ENODEV`

| 来源 | 函数 | 原因 |
|------|------|------|
| 1 | `__iommu_probe_device` | `dev->bus->iommu_ops == NULL` |
| 2 | `iommu_sva_bind_device` | `ops->sva_bind == NULL` |
| 3 | `__iommu_attach_device` | `ops->attach_dev == NULL` |

#### `-EINVAL`

| 来源 | 函数 | 原因 |
|------|------|------|
| 1 | `iommu_device_register` | `ops->owner` 无效 |
| 2 | `iommu_attach_group` | group 设备数 ≠ 1 |
| 3 | `__iommu_map` | IOVA/PA 未对齐 |

### §6.3 分诊流程

```
设备 DMA 失败
    │
    ├─ dmesg 有 "MMIO region too small" → DT reg 属性错误
    ├─ dmesg 有 "failed to allocate queue" → 内存不足
    ├─ dmesg 有 "unaligned" → IOVA/PA 对齐错误
    ├─ dmesg 有 "no pending PRQ" → 页请求队列溢出
    ├─ 无 dmesg 但 DMA 失败 → 检查 IOMMU group
    │   └─ cat /sys/kernel/iommu_groups/*/devices
    └─ 设备在 group 但无 domain → 检查 default_domain
        └─ cat /sys/kernel/iommu_groups/*/type
```

---

## §7 故障注入

### §7.1 当前状态

IOMMU 子系统**没有**内置故障注入框架。

### §7.2 替代方案

- **硬件故障**：通过 `io_page_fault` tracepoint 观察
- **软件模拟**：修改驱动代码强制触发错误路径
- **DMA-API 调试**：`CONFIG_DMA_API_DEBUG=y` 检查映射错误

---

## §8 取证摘要

### §8.1 Tracepoint 数量

7 个 tracepoint 经 `drivers/iommu/iommu-traces.c` 验证。

### §8.2 内核参数

3 个参数经 `drivers/iommu/iommu.c:37-39` 验证。

### §8.3 Debugfs

核心层只创建目录，arm-smmu-v3 未实现具体内容。

---

## §9 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| 用大白话理解 IOMMU 概念 | `08-feynman.md` |
| 证据和取证方法 | `99-evidence.md` |
| 回顾全貌 | `00-overview.md` |
