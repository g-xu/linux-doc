---
title: 扩展机制
---

# IOMMU 子系统 — 子框架

> **定位**：核心框架之上的独立子框架——SVA、故障处理、IOVA 缓存、页故障、PASID 管理
>
> **目标架构**：arm64（x86/PowerPC 等架构特例不展开）
>
> **前置阅读**：`01-api.md`（API 速查）、`02-datastructures.md`（数据结构）、`03-internals.md`（核心流程）

---

## §0 导航

### 本篇组织轴

**一节一个独立框架，互不调用**。

| 子框架 | 文件 | 职责 | arm64 相关性 |
|--------|------|------|-------------|
| SVA（共享虚拟地址） | `drivers/iommu/iommu-sva-lib.c` | 设备绑定进程地址空间 | ★ 高 |
| 故障处理 | `drivers/iommu/iommu.c:1174-1310` | 设备故障注册与报告 | ★ 高 |
| IOVA 范围缓存 | `drivers/iommu/iova.c:624-650` | 缓存小 IOVA 分配 | ★ 高 |
| I/O 页故障 | `drivers/iommu/io-pgfault.c` | SVA 页请求处理 | ★ 高 |
| PASID 管理 | `drivers/iommu/ioasid.c` | 进程地址空间 ID 分配 | ★ 高 |
| PCI Group 划分 | `drivers/iommu/iommu.c:1379-1565` | PCI 设备隔离边界 | ★ 高 |
| Sysfs 接口 | `drivers/iommu/iommu.c:662-667` | 用户态查询 | ★ 高 |

### 不在本篇的内容

| 内容 | 去处 |
|------|------|
| 核心流程 | `03-internals.md` |
| DMA 集成 | `03-internals.md` §6-§7 |
| 页表格式 | `02-datastructures.md` §6 |
| 硬件驱动实现 | 各驱动文档 |

---

## §1 SVA（共享虚拟地址）

### §1.1 定位

SVA 允许设备直接使用进程虚拟地址，无需显式 DMA 映射。设备看到的 IOVA = 进程 VA，IOMMU 翻译为 PA。

**核心机制**：

1. 设备绑定 `mm_struct`（进程地址空间）
2. IOMMU 驱动配置页表，使设备 IOVA = 进程 VA
3. 缺页时触发 I/O 页故障，内核分配物理页后重试

### §1.2 核心 API

**位置**：`drivers/iommu/iommu.c:2788-2830`

```c
struct iommu_sva *iommu_sva_bind_device(struct device *dev,
                                        struct mm_struct *mm,
                                        void *drvdata);
void iommu_sva_unbind_device(struct iommu_sva *handle);
u32 iommu_sva_get_pasid(struct iommu_sva *handle);
```

**调用者**：设备驱动（如 `ioatdma`、`idxd`）

**限制**：

- **单设备 group**（`:2808-2813`）：SVA 当前不支持多设备 group
  - 代码注释："Existing SVA-capable systems are not affected by the problems that required IOMMU groups"
  - 未来可能解除

### §1.3 PASID 分配

**位置**：`drivers/iommu/iommu-sva-lib.c:25-60`

```c
int iommu_sva_alloc_pasid(struct mm_struct *mm, ioasid_t min, ioasid_t max);
```

**机制**：

1. 检查 `mm->pasid` 是否已分配
2. 如果已分配，检查是否在 `[min, max]` 范围内
3. 否则调用 `ioasid_alloc()` 分配新 PASID
4. 保存到 `mm->pasid`

**全局状态**：

- `iommu_sva_lock`：保护 PASID 分配
- `iommu_sva_pasid`：IOASID 集合

### §1.4 PASID 查找

**位置**：`drivers/iommu/iommu-sva-lib.c:62-71`

```c
struct mm_struct *iommu_sva_find(ioasid_t pasid);
```

**机制**：

- `ioasid_find()` → 查找 PASID 对应的 `mm_struct`
- 返回时持有 `mm` 引用，调用者需 `mmput()` 释放

### §1.5 挂载点

SVA 挂在 `03-internals.md` §5（attach）之后：

1. 设备 attach domain（§5）
2. 设备驱动调用 `iommu_sva_bind_device()`（本节）
3. IOMMU 驱动配置 Stage-1 页表，使 IOVA = 进程 VA
4. 缺页触发 I/O 页故障（§4）

---

## §2 故障处理

### §2.1 定位

IOMMU 检测到设备 DMA 故障（访问未映射地址、权限违规）时，通过故障处理机制通知设备驱动。

**两种故障类型**：

- **不可恢复**：DMA 访问违规，设备必须停止
- **可恢复（页请求）**：SVA 场景，设备请求页面，内核分配后重试

### §2.2 注册故障处理器

**位置**：`drivers/iommu/iommu.c:1174-1217`

```c
int iommu_register_device_fault_handler(struct device *dev,
                                        iommu_dev_fault_handler_t handler,
                                        void *data);
```

**机制**：

1. 检查 `dev->iommu` 是否存在
2. 加锁 `dev->iommu->lock`
3. 检查是否已注册（`fault_param != NULL`）→ `-EBUSY`
4. 分配 `iommu_fault_param`
5. 填充 `handler` 和 `data`
6. 初始化 `faults` 链表（待处理故障）

**限制**：每个设备只能注册一个故障处理器

### §2.3 注销故障处理器

**位置**：`drivers/iommu/iommu.c:1219-1257`

```c
int iommu_unregister_device_fault_handler(struct device *dev);
```

**机制**：

1. 检查 `fault_param` 是否存在
2. 释放 `faults` 链表中所有待处理故障
3. `kfree(fault_param)`
4. `put_device(dev)`（平衡 `register` 时的 `get_device`）

### §2.4 报告故障

**位置**：`drivers/iommu/iommu.c:1259-1310`

```c
int iommu_report_device_fault(struct device *dev, struct iommu_fault_event *evt);
```

**调用者**：IOMMU 驱动中断处理函数

**机制**：

1. 检查 `fault_param` 和 `handler` 是否存在
2. 如果是页请求（`IOMMU_FAULT_PAGE_REQ`）且是最后一页（`LAST_PAGE`）：
   - 复制 `evt` 到 `evt_pending`
   - 加入 `fault_param->faults` 链表（等待响应）
3. 调用 `handler(&evt->fault, fparam->data)`
4. 如果 handler 返回错误且是页请求：
   - 从链表移除 `evt_pending`
   - `kfree(evt_pending)`

**关键设计**：

- **页请求跟踪**：只有 `LAST_PAGE` 的页请求加入链表，等待用户态响应
- **错误回滚**：handler 失败时清理链表

### §2.5 页响应

**位置**：`drivers/iommu/iommu.c:1312-1370`（推断）

```c
int iommu_page_response(struct device *dev, struct iommu_page_response *msg);
```

**调用者**：用户态故障处理器（通过 ioctl）

**机制**：

1. 查找 `faults` 链表中匹配的页请求（按 `pasid` + `grpid`）
2. 调用 `ops->page_response(dev, evt, msg)`
3. 从链表移除并释放 `evt_pending`

### §2.6 挂载点

故障处理挂在 `03-internals.md` §2（probe）之后：

1. 设备 probe（§2）
2. 设备驱动注册故障处理器（本节 §2.2）
3. IOMMU 硬件检测到故障 → 中断
4. IOMMU 驱动调用 `iommu_report_device_fault()`（本节 §2.4）
5. 用户态处理器调用 `iommu_page_response()`（本节 §2.5）

---

## §3 IOVA 范围缓存

### §3.1 定位

缓存小 IOVA 分配（≤ 64 页），避免频繁操作红黑树。

**性能收益**：

- 红黑树操作 O(log N)，缓存命中 O(1)
- per-CPU magazine，无锁快速路径

### §3.2 数据结构

**位置**：`drivers/iommu/iova.c:630-646`

```c
struct iova_magazine {
    unsigned long size;                       // 当前 PFN 数量
    unsigned long pfns[IOVA_MAG_SIZE];        // PFN 数组
};

struct iova_cpu_rcache {
    spinlock_t lock;
    struct iova_magazine *loaded;             // 当前加载
    struct iova_magazine *prev;               // 备用
};

struct iova_rcache {
    spinlock_t lock;
    unsigned long depot_size;
    struct iova_magazine *depot[MAX_GLOBAL_MAGS];  // 全局仓库
    struct iova_cpu_rcache __percpu *cpu_rcaches;
};
```

**常量**：

- `IOVA_MAG_SIZE = 127`（`:627`）：每个 magazine 127 个 PFN
- `IOVA_RANGE_CACHE_MAX_SIZE = 6`（`:18`）：缓存 2^0 到 2^5 页

### §3.3 分配流程

**位置**：`drivers/iommu/iova.c:454-490`（推断）

1. 计算缓存索引：`log2(size)`
2. 如果 `size > (1 << IOVA_RANGE_CACHE_MAX_SIZE)` → 不缓存
3. 调用 `iova_rcache_get(iovad, size, limit_pfn)` → 见 §3.4
4. 如果缓存命中 → 返回
5. 否则 → 红黑树分配
6. 释放时调用 `iova_rcache_insert()` → 见 §3.5

### §3.4 缓存查找 — `iova_rcache_get()`

**位置**：`drivers/iommu/iova.c:23-26`（声明）

**机制**：

1. 获取 per-CPU `iova_cpu_rcache`
2. 检查 `loaded` magazine 是否有匹配大小
3. 如果有 → 从 `loaded->pfns[]` 取出 PFN
4. 如果 `loaded` 空 → 与 `prev` 交换
5. 如果 `prev` 也空 → 从全局 `depot` 取一个 magazine
6. 如果 `depot` 也空 → 返回失败（回退到红黑树）

### §3.5 缓存插入 — `iova_rcache_insert()`

**位置**：`drivers/iommu/iova.c:20-22`（声明）

**机制**：

1. 获取 per-CPU `iova_cpu_rcache`
2. 如果 `loaded` 未满 → 插入 `loaded->pfns[]`
3. 如果 `loaded` 满 → 与 `prev` 交换
4. 如果 `prev` 也满 → 放回全局 `depot`
5. 如果 `depot` 满 → 丢弃（回退到红黑树释放）

### §3.6 挂载点

IOVA 缓存挂在 `03-internals.md` §6-§7（map/unmap）内部：

1. `dma_map_page()` → `iommu_map()`（§6）
2. `iommu_dma_alloc_iova()` → `alloc_iova()` → 先查缓存
3. `dma_unmap_page()` → `iommu_unmap()`（§7）
4. `free_iova_fast()` → 先插缓存

---

## §4 I/O 页故障

### §4.1 定位

SVA 场景下，设备访问未映射的进程虚拟地址时，IOMMU 触发页请求（Page Request），内核分配物理页后重试。

**协议**：PCI PRI（Page Request Interface）

### §4.2 核心文件

**位置**：`drivers/iommu/io-pgfault.c`

**行数**：约 300 行（推断）

### §4.3 核心机制

1. **页请求到达**：IOMMU 硬件中断 → `iommu_report_device_fault()`（§2.4）
2. **加入工作队列**：`io-pgfault.c` 的 worker 处理
3. **调用 mm 缺页处理**：`handle_mm_fault()` → 分配物理页
4. **发送响应**：`iommu_page_response()`（§2.5）→ IOMMU 重试 DMA

### §4.4 挂载点

页故障挂在 §1（SVA）和 §2（故障处理）之间：

1. 设备 bind mm（§1）
2. 设备访问未映射地址
3. IOMMU 触发页请求
4. `io-pgfault.c` 处理（本节）
5. 调用 `handle_mm_fault()` 分配页
6. 发送响应 → 设备重试

---

## §5 PASID 管理

### §5.1 定位

PASID（Process Address Space ID）标识进程地址空间，用于 SVA 场景。IOMMU 根据 PASID 选择不同的页表。

### §5.2 核心文件

**位置**：`drivers/iommu/ioasid.c`

**行数**：约 500 行（推断）

### §5.3 核心 API

```c
ioasid_t ioasid_alloc(struct ioasid_set *set, ioasid_t min, ioasid_t max, void *private);
void ioasid_free(ioasid_t pasid);
void *ioasid_find(struct ioasid_set *set, ioasid_t pasid, bool (*getter)(void *));
```

**机制**：

- `ioasid_alloc()`：从 IDA 分配 PASID
- `ioasid_free()`：释放 PASID
- `ioasid_find()`：查找 PASID 对应的私有数据

### §5.4 挂载点

PASID 管理挂在 §1（SVA）内部：

1. `iommu_sva_bind_device()` → `iommu_sva_alloc_pasid()`（§1.3）
2. `iommu_sva_alloc_pasid()` → `ioasid_alloc()`（本节）
3. PASID 保存到 `mm->pasid`

---

## §6 PCI Group 划分

### §6.1 定位

PCI 设备的 IOMMU group 由硬件拓扑决定：ACS 能力、设备 alias、 multifunction 等。

### §6.2 核心函数

**位置**：`drivers/iommu/iommu.c:1501-1565`

```c
struct iommu_group *pci_device_group(struct device *dev);
```

**机制**：

1. **PCI alias 查找**（`:1547`）
   - `get_pci_alias_group(pdev, devfns)` → 见 §6.3
   - 检查是否有其他设备 alias 到同一 PCI function

2. **ACS 检查**（推断）
   - 遍历 PCI 拓扑，检查 ACS 能力
   - ACS 不足的 switch 下游端口下的所有设备在同一 group

3. **创建 group**（推断）
   - 如果没有找到现有 group → `iommu_group_alloc()`
   - 设置 `ops = pci_device_group`

### §6.3 PCI Alias 查找 — `get_pci_alias_group()`

**位置**：`drivers/iommu/iommu.c:1432-1466`

**机制**：

1. 遍历所有 PCI 设备
2. 检查 `pci_dev->devfns` 是否与目标设备 alias
3. 如果找到 → 返回该设备的 group
4. 否则 → 递归查找

### §6.4 其他 Group 函数

| 函数 | 位置 | 适用总线 |
|------|------|---------|
| `pci_device_group()` | `:1501-1565` | PCI |
| `generic_device_group()` | `:1491-1499` | platform（一设备一 group） |
| `fsl_mc_device_group()` | `:1566-1590` | FSL MC（NXP） |

### §6.5 挂载点

Group 划分挂在 `03-internals.md` §3（group 获取）内部：

1. `__iommu_probe_device()` → `iommu_group_get_for_dev()`（§3）
2. `iommu_group_get_for_dev()` → `ops->device_group(dev)`（本节 §6.2）
3. 根据总线类型选择 `pci_device_group` / `generic_device_group`

---

## §7 Sysfs 接口

### §7.1 定位

用户态查询 IOMMU group 信息。

### §7.2 属性

**位置**：`drivers/iommu/iommu.c:662-667`

| 属性 | 权限 | 内容 |
|------|------|------|
| `name` | `S_IRUGO` | group 名称（可选） |
| `reserved_regions` | `0444` | 保留区域列表 |
| `type` | `0644` | 当前域类型（可写切换） |

### §7.3 路径

```
/sys/kernel/iommu_groups/<id>/
├── devices/          # 链接到设备
│   ├── 0000:01:00.0 -> /sys/devices/pci0000:00/...
│   └── ...
├── reserved_regions  # 保留区域
├── type              # 域类型
└── name              # group 名称（可选）
```

### §7.4 挂载点

Sysfs 在 `03-internals.md` §3.1（group 创建）时初始化：

1. `iommu_group_alloc()` → `kobject_init_and_add()`
2. `iommu_group_create_file()` → 创建属性文件
3. 设备添加时创建 `devices/` 链接

---

## §8 汇总表

| 子框架 | 职责 | 挂载点 | arm64 相关 | 消费者数 |
|--------|------|--------|-----------|---------|
| SVA | 设备绑定进程地址空间 | `03-internals.md` §5 之后 | ★ | 4 |
| 故障处理 | 设备故障注册与报告 | `03-internals.md` §2 之后 | ★ | 7 |
| IOVA 缓存 | 缓存小 IOVA 分配 | `03-internals.md` §6-§7 内部 | ★ | 8 |
| I/O 页故障 | SVA 页请求处理 | §1 + §2 之间 | ★ | 3 |
| PASID 管理 | PASID 分配 | §1 内部 | ★ | 5 |
| PCI Group | PCI 设备隔离边界 | `03-internals.md` §3 内部 | ★ | 1 |
| Sysfs | 用户态查询 | `03-internals.md` §3.1 内部 | ★ | N/A |

**消费者统计**：

```bash
# SVA
grep -rn 'iommu_sva_bind_device' --include=*.c | wc -l  # 4

# 故障处理
grep -rn 'iommu_register_device_fault_handler' --include=*.c | wc -l  # 7

# IOVA 缓存
grep -rn 'iova_rcache' --include=*.c | wc -l  # 8

# I/O 页故障
grep -rn 'iopf_queue' --include=*.c | wc -l  # 3

# PASID
grep -rn 'ioasid_alloc' --include=*.c | wc -l  # 5
```

---

## §9 取证摘要

### §9.1 子框架独立性

每个子框架独立实现，互不调用（除 SVA → PASID）。

### §9.2 行数统计

部分文件行数基于推断，未直接 `wc -l`。

### §9.3 消费者统计

消费者数基于 `grep -rn` 全树统计，含声明行和注释。

---

## §10 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| 设备驱动怎么用这些 API | `05-usage.md` |
| 设备树怎么配置 | `06-devicetree.md` |
| 怎么调试 IOMMU 问题 | `07-debug.md` |
| 用大白话理解这些概念 | `08-feynman.md` |
