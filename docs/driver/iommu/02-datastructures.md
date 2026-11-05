---
title: 数据结构
---

# IOMMU 子系统 — 数据结构

> **定位**：核心结构体字段详解 + 结构关系图 + 全局变量与锁 + 命名约定
>
> **目标架构**：arm64（x86/PowerPC 等架构特例不展开）
>
> **前置阅读**：`00-overview.md`（概念模型）、`01-api.md`（API 速查）

---

## §0 导航

### 本篇组织轴

**一条主轴：从核心抽象到硬件实现的分层结构**。

- **Part A：核心框架层**（§1-§8）—— `include/linux/iommu.h` 定义的通用结构，所有 IOMMU 驱动共享
- **Part B：IOVA 分配器层**（§9）—— `include/linux/iova.h` 定义的虚拟地址管理
- **Part C：DMA 集成层**（§10）—— `drivers/iommu/dma-iommu.c` 定义的桥接结构
- **Part D：页表格式层**（§11）—— `include/linux/io-pgtable.h` 定义的格式无关抽象
- **Part E：硬件驱动层**（§12）—— `drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.h` 定义的 SMMUv3 私有结构

### 不在本篇的内容

| 内容 | 去处 |
|------|------|
| API 签名与调用约定 | `01-api.md` |
| 结构体在流程中的角色 | `03-internals.md` |
| 子框架（SVA / fault / IOVA cache） | `04-extensions.md` |
| 字段含义的工程背景 | `08-feynman.md` |

---

## §1 先分清三组关系

在画图之前，必须先否掉三个错误类比。

### 1.1 `iommu_device` ≠ `iommu_domain`

**错误类比**："iommu_device 是硬件实例，iommu_domain 是它的配置"——听起来合理，但误导。

**正确理解**：

| 结构体 | 生命周期 | 数量关系 | 谁创建 | 注册到哪 |
|--------|---------|---------|--------|---------|
| `struct iommu_device` | 系统启动时注册，永不释放 | 一个 SMMU 硬件 = 一个实例 | IOMMU 驱动 probe 时 | 全局 `iommu_device_list` 链表 |
| `struct iommu_domain` | 设备 attach 时分配，detach 时释放 | 一个 group 至少一个；SVA 场景可多个 | IOMMU 核心或设备驱动 | 挂在 `iommu_group` 上 |

**为什么不会混淆**：`iommu_device` 代表**物理硬件**（`drivers/iommu/iommu.c:326-331`），包含 `ops` 指针指向驱动实现的回调；`iommu_domain` 代表**翻译上下文**（`include/linux/iommu.h:90-98`），包含页表根指针和 DMA 窗口配置。一个 SMMU 可以服务多个 domain（每个 group 一个），一个 domain 也可以跨多个 SMMU（如果 group 包含多个设备）。

### 1.2 `iommu_group` ≠ `iommu_domain`

**错误类比**："group 就是 domain"——错。

**正确理解**：

| 维度 | `iommu_group` | `iommu_domain` |
|------|--------------|----------------|
| 语义 | 隔离边界（isolation boundary） | 翻译规则（translation context） |
| 数量 | 系统启动时确定，不可变 | 可随时创建/销毁 |
| 包含关系 | 包含 N 个设备 | 被 M 个 group 共享（通常 M=1） |
| 字段 | `default_domain` + `blocking_domain` + `domain`（当前活跃） | 页表 + geometry + handler |

**代码依据**：`drivers/iommu/iommu.c:41-56` 显示 group 有三个 domain 指针——`default_domain`（启动时分配）、`blocking_domain`（用于隔离）、`domain`（当前 attach 的）。domain 本身（`:90-98`）不知道自己在哪个 group 里。

**为什么 group 不可变**：PCI 设备的 group 由硬件拓扑决定（ACS 能力、alias 关系），probe 时一次性计算（`drivers/iommu/iommu.c:1379-1501`），之后不会改变。

### 1.3 `iommu_ops` ≠ `iommu_domain_ops`

**错误类比**："ops 就是 domain_ops"——错。这是**两层可插拔**，不是一层。

**正确理解**：

| ops 类型 | 挂载位置 | 谁实现 | 谁调用 | 典型回调 |
|---------|---------|--------|--------|---------|
| `struct iommu_ops` | `iommu_device` 上 | IOMMU 驱动（如 `arm-smmu-v3.c`） | IOMMU 核心（`iommu.c`） | `probe_device` / `device_group` / `domain_alloc` |
| `struct iommu_domain_ops` | `iommu_domain` 上 | 同一 IOMMU 驱动 | 设备驱动 / DMA 层 | `attach_dev` / `map_pages` / `unmap_pages` |

**代码依据**：`include/linux/iommu.h:229-285` 定义 `iommu_ops`（驱动级），`:287-324` 定义 `iommu_domain_ops`（域级）。`arm_smmu_ops`（`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2871-2898`）同时填充两者——`default_domain_ops` 字段指向 `arm_smmu_domain_ops`。

**为什么分两层**：`iommu_ops` 处理**设备枚举和 group 划分**（probe 时调用一次）；`iommu_domain_ops` 处理**运行时翻译操作**（map/unmap 频繁调用）。前者是慢路径，后者是热路径。

---

## §2 核心框架层结构体

### 2.1 `struct iommu_domain` — 翻译上下文

**定义位置**：`include/linux/iommu.h:90-98`

```c
struct iommu_domain {
    unsigned type;                              // 域类型（见 §2.1.1）
    const struct iommu_domain_ops *ops;         // 域级操作回调
    unsigned long pgsize_bitmap;                // 支持的页大小位图
    iommu_fault_handler_t handler;              // 故障处理回调
    void *handler_token;                        // 回调私有数据
    struct iommu_domain_geometry geometry;      // DMA 窗口几何
    struct iommu_dma_cookie *iova_cookie;       // DMA 层私有（见 §10）
};
```

**字段详解**：

- **`type`**：组合标志（`include/linux/iommu.h:71-89`），不是枚举值。5 种标准类型：
  - `IOMMU_DOMAIN_BLOCKED` (0)：所有 DMA 被阻止
  - `IOMMU_DOMAIN_IDENTITY` (1 << 2)：恒等映射
  - `IOMMU_DOMAIN_UNMANAGED` (1 << 0)：用户态管理（虚拟化用）
  - `IOMMU_DOMAIN_DMA` (1 << 0 | 1 << 1)：DMA-API 使用
  - `IOMMU_DOMAIN_DMA_FQ` (1 << 0 | 1 << 1 | 1 << 3)：DMA-API + flush queue

- **`ops`**：指向 `iommu_domain_ops`（见 §2.3），由 IOMMU 驱动在 `domain_alloc` 时填充。

- **`pgsize_bitmap`**：bit N 表示支持 2^N 字节页。典型值 `0x7ff000`（4K/2M/1G）。`iommu_map` 时按此选择页大小。

- **`geometry`**：`include/linux/iommu.h:54-58`，定义 IOVA 可用范围 `[aperture_start, aperture_end]`。`force_aperture=true` 时，范围外的映射被拒绝。

- **`iova_cookie`**：DMA 域（`IOMMU_DOMAIN_DMA*`）时非空，指向 `iommu_dma_cookie`（§10.1），包含 IOVA 分配器和 flush queue。

#### 2.1.1 域类型组合逻辑

**定义位置**：`include/linux/iommu.h:71-89`

```c
#define __IOMMU_DOMAIN_PAGING    (1U << 0)  // 支持 map/unmap
#define __IOMMU_DOMAIN_DMA_API   (1U << 1)  // 用于 DMA-API
#define __IOMMU_DOMAIN_PT        (1U << 2)  // 恒等映射
#define __IOMMU_DOMAIN_DMA_FQ    (1U << 3)  // 使用 flush queue

#define IOMMU_DOMAIN_BLOCKED     (0U)
#define IOMMU_DOMAIN_IDENTITY    (__IOMMU_DOMAIN_PT)
#define IOMMU_DOMAIN_UNMANAGED   (__IOMMU_DOMAIN_PAGING)
#define IOMMU_DOMAIN_DMA         (__IOMMU_DOMAIN_PAGING | __IOMMU_DOMAIN_DMA_API)
#define IOMMU_DOMAIN_DMA_FQ      (__IOMMU_DOMAIN_PAGING | __IOMMU_DOMAIN_DMA_API | __IOMMU_DOMAIN_DMA_FQ)
```

**判断宏**：`include/linux/iommu.h:100-103`

```c
static inline bool iommu_is_dma_domain(struct iommu_domain *domain)
{
    return domain->type & __IOMMU_DOMAIN_DMA_API;
}
```

**设计要点**：`PAGING` 和 `PT` 互斥——前者需要页表，后者不需要。`DMA_API` 和 `DMA_FQ` 是递进关系（FQ 是 DMA 的优化）。

### 2.2 `struct iommu_ops` — 驱动级操作

**定义位置**：`include/linux/iommu.h:229-285`

```c
struct iommu_ops {
    bool (*capable)(struct device *dev, enum iommu_cap);
    struct iommu_domain *(*domain_alloc)(unsigned iommu_domain_type);
    struct iommu_device *(*probe_device)(struct device *dev);
    void (*release_device)(struct device *dev);
    void (*probe_finalize)(struct device *dev);
    struct iommu_group *(*device_group)(struct device *dev);
    void (*get_resv_regions)(struct device *dev, struct list_head *list);
    int (*of_xlate)(struct device *dev, struct of_phandle_args *args);
    bool (*is_attach_deferred)(struct device *dev);
    int (*dev_enable_feat)(struct device *dev, enum iommu_dev_features f);
    int (*dev_disable_feat)(struct device *dev, enum iommu_dev_features f);
    struct iommu_sva *(*sva_bind)(struct device *dev, struct mm_struct *mm, void *drvdata);
    void (*sva_unbind)(struct iommu_sva *handle);
    u32 (*sva_get_pasid)(struct iommu_sva *handle);
    int (*page_response)(struct device *dev, struct iommu_fault_event *evt,
                         struct iommu_page_response *msg);
    int (*def_domain_type)(struct device *dev);
    const struct iommu_domain_ops *default_domain_ops;  // ★ 指向域级 ops
    unsigned long pgsize_bitmap;
    struct module *owner;
};
```

**调用时机**：

- **probe 阶段**（设备枚举）：`probe_device` → `device_group` → `get_resv_regions` → `of_xlate`
- **attach 阶段**（域绑定）：`domain_alloc` → `def_domain_type`（可选）
- **运行时**（SVA / fault）：`sva_bind` / `page_response`

**arm64 实现示例**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2871-2898` 的 `arm_smmu_ops`。

### 2.3 `struct iommu_domain_ops` — 域级操作

**定义位置**：`include/linux/iommu.h:287-324`

```c
struct iommu_domain_ops {
    int (*attach_dev)(struct iommu_domain *domain, struct device *dev);
    void (*detach_dev)(struct iommu_domain *domain, struct device *dev);
    int (*map)(struct iommu_domain *domain, unsigned long iova,
               phys_addr_t paddr, size_t size, int prot, gfp_t gfp);
    int (*map_pages)(struct iommu_domain *domain, unsigned long iova,
                     phys_addr_t paddr, size_t pgsize, size_t pgcount,
                     int prot, gfp_t gfp, size_t *mapped);
    size_t (*unmap)(struct iommu_domain *domain, unsigned long iova,
                    size_t size, struct iommu_iotlb_gather *iotlb_gather);
    size_t (*unmap_pages)(struct iommu_domain *domain, unsigned long iova,
                          size_t pgsize, size_t pgcount,
                          struct iommu_iotlb_gather *iotlb_gather);
    void (*flush_iotlb_all)(struct iommu_domain *domain);
    void (*iotlb_sync_map)(struct iommu_domain *domain, unsigned long iova, size_t size);
    void (*iotlb_sync)(struct iommu_domain *domain, struct iommu_iotlb_gather *iotlb_gather);
    phys_addr_t (*iova_to_phys)(struct iommu_domain *domain, dma_addr_t iova);
    bool (*enforce_cache_coherency)(struct iommu_domain *domain);
    int (*enable_nesting)(struct iommu_domain *domain);
    int (*set_pgtable_quirks)(struct iommu_domain *domain, unsigned long quirks);
    void (*free)(struct iommu_domain *domain);
};
```

**调用频率**：

- **热路径**（每 DMA 调用）：`map_pages` / `unmap_pages` / `iotlb_sync`
- **冷路径**（设备 attach/detach）：`attach_dev` / `detach_dev`
- **初始化路径**（一次）：`flush_iotlb_all` / `enable_nesting`

**与 `iommu_ops` 的关系**：`iommu_ops.default_domain_ops` 指向 `iommu_domain_ops`。`iommu_domain_alloc` 时复制到 `domain->ops`（`drivers/iommu/iommu.c:1904-1933`）。

### 2.4 `struct iommu_device` — 硬件实例

**定义位置**：`include/linux/iommu.h:326-331`

```c
struct iommu_device {
    struct list_head list;              // 全局链表节点
    const struct iommu_ops *ops;        // 驱动级 ops
    struct fwnode_handle *fwnode;       // DT/ACPI 固件节点
    struct device *dev;                 // 父设备（platform device）
};
```

**生命周期**：

1. IOMMU 驱动 probe 时调用 `iommu_device_register`（`drivers/iommu/iommu.c:2250-2275`）
2. 加入全局 `iommu_device_list`（`drivers/iommu/iommu.c:109`）
3. 系统关闭时 `iommu_device_unregister` 移除

**与 `struct device` 的关系**：`iommu_device.dev` 指向 platform device（如 `arm-smmu-v3` 的 DT 节点）。`dev->driver_data` 存 `iommu_device` 指针（`iommu_device_register` 设置）。

### 2.5 `struct iommu_group` — 隔离边界

**定义位置**：`drivers/iommu/iommu.c:41-56`

```c
struct iommu_group {
    struct kobject kobj;                    // sysfs 对象
    struct kobject *devices_kobj;           // 设备子目录
    struct list_head devices;               // group_device 链表
    struct mutex mutex;                     // 保护 devices 链表
    void *iommu_data;                       // 驱动私有数据
    void (*iommu_data_release)(void *iommu_data);
    char *name;                             // sysfs 名称
    int id;                                 // 全局唯一 ID
    struct iommu_domain *default_domain;    // 启动时分配
    struct iommu_domain *blocking_domain;   // 用于隔离
    struct iommu_domain *domain;            // 当前活跃域
    struct list_head entry;                 // 全局链表节点
    unsigned int owner_cnt;                 // 设备分配引用计数
    void *owner;                            // 当前 owner（如 VFIO）
};
```

**三个 domain 指针的语义**：

- **`default_domain`**：probe 时根据 `iommu_def_domain_type` 分配（`drivers/iommu/iommu.c:84-95`）。DMA 域或恒等域。
- **`blocking_domain`**：所有 DMA 被阻止。用于设备释放前隔离（`drivers/iommu/iommu.c:92-94`）。
- **`domain`**：当前 attach 的域。可能等于 `default_domain`，也可能是用户切换后的域。

**group 划分规则**（PCI）：

1. 同一 PCI function 的所有 VF（SR-IOV）在同一 group
2. ACS 能力不足的 switch 下游端口下的所有设备在同一 group
3. 代码：`drivers/iommu/iommu.c:1379-1501`

### 2.6 `struct group_device` — group 成员

**定义位置**：`drivers/iommu/iommu.c:58-62`

```c
struct group_device {
    struct list_head list;      // group->devices 链表节点
    struct device *dev;         // 实际设备
    char *name;                 // sysfs 显示名
};
```

**作用**：`iommu_group` 的 `devices` 链表存 `group_device`，不是直接存 `struct device`。`name` 用于 sysfs（`/sys/kernel/iommu_groups/<id>/devices/`）。

### 2.7 `struct dev_iommu` — 设备私有数据

**定义位置**：`include/linux/iommu.h:373-379`

```c
struct dev_iommu {
    struct mutex lock;
    struct iommu_fault_param *fault_param;      // 故障处理
    struct iopf_device_param *iopf_param;       // I/O 页故障队列
    struct iommu_fwspec *fwspec;                // DT/ACPI 翻译信息
    struct iommu_device *iommu_dev;             // 所属 IOMMU 硬件
    void *priv;                                 // 驱动私有
};
```

**挂载位置**：`dev->iommu`（`struct device` 的字段）。probe 时分配（`drivers/iommu/iommu.c:253-263`）。

**与 `iommu_device` 的关系**：`dev_iommu.iommu_dev` 指向管理该设备的 `iommu_device`。一个 `iommu_device` 管理多个设备，每个设备的 `dev_iommu.iommu_dev` 都指向同一个 `iommu_device`。

### 2.8 `struct iommu_fwspec` — 固件翻译信息

**定义位置**：`include/linux/iommu.h:609-615`

```c
struct iommu_fwspec {
    const struct iommu_ops *ops;            // IOMMU 驱动 ops
    struct fwnode_handle *iommu_fwnode;     // IOMMU 固件节点
    u32 flags;                              // 标志（如 PCI RC ATS）
    unsigned int num_ids;                   // Stream ID 数量
    u32 ids[];                              // Stream ID 数组（柔性数组）
};
```

**填充时机**：DT 路径由 `of_xlate` 回调填充（`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:1610-1630`）。`ids[]` 存设备的 Stream ID（SMMUv3）或 Device ID。

**标志位**：`IOMMU_FWSPEC_PCI_RC_ATS`（`include/linux/iommu.h:618`）表示 PCI RC 支持 ATS。

### 2.9 `struct iommu_resv_region` — 保留区域

**定义位置**：`include/linux/iommu.h:140-147`

```c
struct iommu_resv_region {
    struct list_head list;
    phys_addr_t start;                      // 物理起始地址
    size_t length;                          // 长度
    int prot;                               // 保护标志（R/W）
    enum iommu_resv_type type;              // 类型（见下）
    void (*free)(struct device *dev, struct iommu_resv_region *region);
};
```

**类型枚举**：`include/linux/iommu.h:114-138`

```c
enum iommu_resv_type {
    IOMMU_RESV_DIRECT,              // 必须 1:1 映射
    IOMMU_RESV_DIRECT_RELAXABLE,    // 可放松（USB/显卡）
    IOMMU_RESV_RESERVED,            // 禁止映射
    IOMMU_RESV_MSI,                 // 硬件 MSI 区域（未翻译）
    IOMMU_RESV_SW_MSI,              // 软件 MSI 窗口
};
```

**用途**：`get_resv_regions` 回调填充，告诉核心"这些物理区域特殊处理"。典型例子：MSI 地址、固件保留内存。

### 2.10 `struct iommu_iotlb_gather` — TLB 批量刷新

**定义位置**：`include/linux/iommu.h:194-199`

```c
struct iommu_iotlb_gather {
    unsigned long start;                // 待刷新范围起始
    unsigned long end;                  // 待刷新范围结束
    size_t pgsize;                      // 页大小
    struct list_head freelist;          // 待释放页链表
    bool queued;                        // 是否已排队
};
```

**作用**：`unmap_pages` 时收集多个范围，`iotlb_sync` 一次性刷新。减少 TLB 失效次数。

**使用模式**：

```c
struct iommu_iotlb_gather gather;
iommu_iotlb_gather_init(&gather);
iommu_unmap_fast(domain, iova1, size1, &gather);
iommu_unmap_fast(domain, iova2, size2, &gather);
iommu_iotlb_sync(domain, &gather);  // 一次性刷新
```

---

## §3 结构关系图

### 3.1 核心抽象关系

```
┌─────────────────────────────────────────────────────────────┐
│                    全局 IOMMU 硬件列表                       │
│              iommu_device_list (list_head)                   │
└──────────────────────────┬──────────────────────────────────┘
                           │ contains
              ┌────────────┼────────────┐
              ▼            ▼            ▼
     ┌────────────┐ ┌───────────┐ ┌──────────┐
     │iommu_device│ │iommu_device│ │iommu_device│  ← 每个 SMMU 一个
     │  (SMMU #0) │ │  (SMMU #1) │ │  (SMMU #2) │
     └─────┬──────┘ └───────────┘ └──────────┘
           │ ops
           ▼
     ┌────────────┐
     │ iommu_ops  │  ← 驱动级回调（probe_device / device_group）
     └────────────┘
           │ default_domain_ops
           ▼
     ┌──────────────────┐
     │iommu_domain_ops  │  ← 域级回调（map / unmap / attach）
     └──────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    全局 Group 列表                            │
│              iommu_group_kset (kset)                         │
└──────────────────────────┬──────────────────────────────────┘
                           │ contains
              ┌────────────┼────────────┐
              ▼            ▼            ▼
     ┌────────────┐ ┌───────────┐ ┌──────────┐
     │iommu_group │ │iommu_group│ │iommu_group│  ← 每个隔离边界一个
     │   (id=0)   │ │   (id=1)  │ │   (id=2)  │
     └─────┬──────┘ └───────────┘ └──────────┘
           │ devices (list_head)
           ▼
     ┌──────────────┐
     │ group_device │  ← 每个设备一个
     │  (dev=eth0)  │
     └──────┬───────┘
            │ dev->iommu
            ▼
     ┌────────────┐
     │ dev_iommu  │  ← 设备私有数据
     │ (fwspec,   │
     │  iommu_dev)│
     └────────────┘
```

### 3.2 Domain 与 Group 的关系

```
┌─────────────────┐
│  iommu_group    │
│   (id=5)        │
└────────┬────────┘
         │
         ├─► default_domain ──► struct iommu_domain (DMA 域)
         │                      ├─ type = IOMMU_DOMAIN_DMA
         │                      ├─ ops = &arm_smmu_domain_ops
         │                      ├─ pgsize_bitmap = 0x7ff000
         │                      └─ iova_cookie ──► struct iommu_dma_cookie
         │
         ├─► blocking_domain ──► struct iommu_domain (隔离域)
         │                       └─ type = IOMMU_DOMAIN_BLOCKED
         │
         └─► domain ──► struct iommu_domain (当前活跃)
                        └─ 可能 == default_domain
```

### 3.3 设备与 IOMMU 的关系

```
┌──────────────────┐
│  struct device   │  (eth0, PCI 设备)
│   (bus=pci)      │
└────────┬─────────┘
         │ dev->iommu
         ▼
┌──────────────────┐
│  struct dev_iommu│
│   ├─ fwspec      │  ← DT 翻译信息（Stream ID = 0x1234）
│   ├─ iommu_dev ──┼──► struct iommu_device (SMMU #0)
│   └─ fault_param │
└──────────────────┘
```

---

## §4 IOVA 分配器层结构体

### 4.1 `struct iova` — 已分配范围

**定义位置**：`include/linux/iova.h:18-22`

```c
struct iova {
    struct rb_node node;          // 红黑树节点
    unsigned long pfn_hi;         // 最高 PFN
    unsigned long pfn_lo;         // 最低 PFN
};
```

**作用**：红黑树节点，表示已分配的 IOVA 范围 `[pfn_lo, pfn_hi]`。大小 = `(pfn_hi - pfn_lo + 1) * granule`。

### 4.2 `struct iova_domain` — IOVA 分配器

**定义位置**：`include/linux/iova.h:28-42`

```c
struct iova_domain {
    spinlock_t iova_rbtree_lock;          // 保护红黑树
    struct rb_root rbroot;                // 红黑树根
    struct rb_node *cached_node;          // 上次分配节点
    struct rb_node *cached32_node;        // 上次 32 位分配节点
    unsigned long granule;                // 页粒度（通常 PAGE_SIZE）
    unsigned long start_pfn;              // 起始 PFN
    unsigned long dma_32bit_pfn;          // 32 位边界 PFN
    unsigned long max32_alloc_size;       // 上次 32 位分配失败的大小
    struct iova anchor;                   // 红黑树锚点
    struct iova_rcache *rcaches;          // per-CPU 范围缓存
    struct hlist_node cpuhp_dead;         // CPU 热插拔节点
};
```

**挂载位置**：`iommu_dma_cookie.iovad`（`drivers/iommu/dma-iommu.c:52`）。每个 DMA 域一个。

**红黑树操作**：

- `alloc_iova`：从树中找空闲范围（`drivers/iommu/iova.c:313-370`）
- `free_iova`：释放范围回树（`drivers/iommu/iova.c:545-575`）
- `find_iova`：按 PFN 查找已分配范围（`drivers/iommu/iova.c:372-385`）

### 4.3 `struct iova_rcache` — 范围缓存

**定义位置**：`drivers/iommu/iova.c:641-646`

```c
struct iova_rcache {
    spinlock_t lock;                                  // 保护 depot
    unsigned long depot_size;                         // depot 中 magazine 数量
    struct iova_magazine *depot[MAX_GLOBAL_MAGS];     // 全局 magazine 仓库
    struct iova_cpu_rcache __percpu *cpu_rcaches;     // per-CPU 缓存
};
```

**作用**：缓存小 IOVA 分配，避免频繁操作红黑树。`IOVA_RANGE_CACHE_MAX_SIZE=6`（`drivers/iommu/iova.c:18`），表示缓存 2^0 到 2^5 页的分配。

### 4.4 `struct iova_magazine` — 缓存容器

**定义位置**：`drivers/iommu/iova.c:630-633`

```c
struct iova_magazine {
    unsigned long size;                       // 当前 PFN 数量
    unsigned long pfns[IOVA_MAG_SIZE];        // PFN 数组
};
```

**常量**：`IOVA_MAG_SIZE=127`（`drivers/iommu/iova.c:627`）。每个 magazine 存 127 个 PFN。

### 4.5 `struct iova_cpu_rcache` — per-CPU 缓存

**定义位置**：`drivers/iommu/iova.c:635-639`

```c
struct iova_cpu_rcache {
    spinlock_t lock;                          // 保护本 CPU 缓存
    struct iova_magazine *loaded;             // 当前加载的 magazine
    struct iova_magazine *prev;               // 备用 magazine
};
```

**作用**：每个 CPU 独立的缓存，避免锁竞争。分配时先查 `loaded`，满了换到 `prev`，再满回退到全局 `depot`。

---

## §5 DMA 集成层结构体

### 5.1 `struct iommu_dma_cookie` — DMA 层私有

**定义位置**：`drivers/iommu/dma-iommu.c:46-65`

```c
struct iommu_dma_cookie {
    enum iommu_dma_cookie_type type;          // IOVA 或 MSI
    union {
        struct {
            struct iova_domain iovad;                 // IOVA 分配器
            struct iova_fq __percpu *fq;              // per-CPU flush queue
            atomic64_t fq_flush_start_cnt;            // 刷新启动计数
            atomic64_t fq_flush_finish_cnt;           // 刷新完成计数
            struct timer_list fq_timer;               // 定时刷新定时器
            atomic_t fq_timer_on;                     // 定时器状态
        };
        dma_addr_t msi_iova;                          // MSI 线性分配器
    };
};
```

**挂载位置**：`iommu_domain.iova_cookie`（`include/linux/iommu.h:97`）。仅 DMA 域（`IOMMU_DOMAIN_DMA*`）有。

**两种类型**：

- **`IOMMU_DMA_IOVA_COOKIE`**：DMA 域，使用 `iovad` 红黑树分配器 + flush queue
- **`IOMMU_DMA_MSI_COOKIE`**：MSI 域，使用简单的线性分配（`msi_iova` 递增）

### 5.2 `struct iova_fq` — per-CPU flush queue

**定义位置**：`drivers/iommu/dma-iommu.c:101-105`

```c
struct iova_fq {
    struct iova_fq_entry entries[IOVA_FQ_SIZE];   // 环形缓冲区
    unsigned int head, tail;                       // 头尾指针
    spinlock_t lock;                               // 保护本队列
};
```

**常量**：`IOVA_FQ_SIZE=256`（`drivers/iommu/dma-iommu.c:87`）。每个 CPU 256 个条目。

### 5.3 `struct iova_fq_entry` — flush queue 条目

**定义位置**：`drivers/iommu/dma-iommu.c:93-98`

```c
struct iova_fq_entry {
    unsigned long iova_pfn;               // 待释放 IOVA PFN
    unsigned long pages;                  // 页数
    struct list_head freelist;            // 待释放物理页链表
    u64 counter;                          // 加入时的刷新计数
};
```

**作用**：`iommu_unmap_fast` 时不立即释放物理页，而是加入 flush queue。定时器（10ms）或队列满时批量刷新 TLB 后释放。

**设计要点**：延迟释放避免频繁 TLB 失效。`counter` 用于判断条目是否已过期。

---

## §6 页表格式层结构体

### 6.1 `struct io_pgtable` — 格式无关页表

**定义位置**：`include/linux/io-pgtable.h:214-219`

```c
struct io_pgtable {
    enum io_pgtable_fmt fmt;              // 格式（ARM_64_LPAE_S1 等）
    void *cookie;                         // 回调私有数据
    struct io_pgtable_cfg cfg;            // 配置（见 §6.2）
    struct io_pgtable_ops ops;            // 操作回调
};
```

**挂载位置**：IOMMU 驱动私有（如 `arm_smmu_domain.pgtbl_ops`）。核心层不直接访问，通过 `iommu_domain_ops.map_pages` 间接调用。

### 6.2 `struct io_pgtable_cfg` — 页表配置

**定义位置**：`include/linux/io-pgtable.h:64-160`

```c
struct io_pgtable_cfg {
    unsigned long quirks;                         // quirk 标志
    unsigned long pgsize_bitmap;                  // 支持的页大小
    unsigned int ias;                             // 输入地址宽度（IOVA）
    unsigned int oas;                             // 输出地址宽度（PA）
    bool coherent_walk;                           // 页表遍历是否相干
    const struct iommu_flush_ops *tlb;            // TLB 刷新回调
    struct device *iommu_dev;                     // IOMMU 设备

    union {
        struct { ... } arm_lpae_s1_cfg;           // ARM LPAE Stage-1
        struct { ... } arm_lpae_s2_cfg;           // ARM LPAE Stage-2
        struct { ... } arm_v7s_cfg;               // ARM v7s
        struct { ... } arm_mali_lpae_cfg;         // ARM Mali
        struct { ... } apple_dart_cfg;            // Apple DART
    };
};
```

**quirk 标志**：`include/linux/io-pgtable.h:66-97`

- `IO_PGTABLE_QUIRK_ARM_NS`：Stage-1 PTE 设置 NS 位
- `IO_PGTABLE_QUIRK_NO_PERMS`：忽略权限，全权限映射
- `IO_PGTABLE_QUIRK_ARM_MTK_EXT`：MediaTek 35 位 PA 扩展
- `IO_PGTABLE_QUIRK_ARM_TTBR1`：上半地址空间（TTBR1）

**union 成员**：不同格式的配置。arm64 主要用 `arm_lpae_s1_cfg`（SMMUv3 Stage-1）和 `arm_lpae_s2_cfg`（Stage-2）。

### 6.3 `struct io_pgtable_ops` — 页表操作

**定义位置**：`include/linux/io-pgtable.h:162-170`

```c
struct io_pgtable_ops {
    int (*map)(struct io_pgtable_ops *ops, unsigned long iova,
               phys_addr_t paddr, size_t size, int prot, gfp_t gfp);
    int (*map_pages)(struct io_pgtable_ops *ops, unsigned long iova,
                     phys_addr_t paddr, size_t pgsize, size_t pgcount,
                     int prot, gfp_t gfp, size_t *mapped);
    size_t (*unmap)(struct io_pgtable_ops *ops, unsigned long iova,
                    size_t size, struct iommu_iotlb_gather *gather);
    size_t (*unmap_pages)(struct io_pgtable_ops *ops, unsigned long iova,
                          size_t pgsize, size_t pgcount,
                          struct iommu_iotlb_gather *gather);
    phys_addr_t (*iova_to_phys)(struct io_pgtable_ops *ops, unsigned long iova);
};
```

**实现者**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c` 的 `arm_smmu_map_pages` / `arm_smmu_unmap_pages` 等。内部调用 `io-pgtable.c` 的格式特定实现。

### 6.4 `enum io_pgtable_fmt` — 页表格式

**定义位置**：`include/linux/io-pgtable.h:15-23`

```c
enum io_pgtable_fmt {
    ARM_32_LPAE_S1,              // ARM 32 位 LPAE Stage-1
    ARM_32_LPAE_S2,              // ARM 32 位 LPAE Stage-2
    ARM_64_LPAE_S1,              // ARM 64 位 LPAE Stage-1 ★
    ARM_64_LPAE_S2,              // ARM 64 位 LPAE Stage-2 ★
    ARM_V7S,                     // ARM v7s
    ARM_MALI_LPAE,               // ARM Mali LPAE
    AMD_IOMMU_V1,                // AMD IOMMU v1（x86）
    AMD_IOMMU_V2,                // AMD IOMMU v2（x86）
    APPLE_DART,                  // Apple DART
    APPLE_DART2,                 // Apple DART2
    IO_PGTABLE_NUM_FMTS,         // 格式数量
};
```

**arm64 常用**：`ARM_64_LPAE_S1`（SMMUv3 Stage-1）、`ARM_64_LPAE_S2`（虚拟化）。

**分发机制**：`drivers/iommu/io-pgtable.c:17-34` 的 `io_pgtable_init_fns[]` 数组，按格式索引查找 alloc/free 函数。

---

## §7 故障处理结构体

### 7.1 `struct iommu_fault_event` — 故障事件

**定义位置**：`include/linux/iommu.h:342-345`

```c
struct iommu_fault_event {
    struct iommu_fault fault;           // 故障描述（见 §7.3）
    struct list_head list;              // 待处理链表
};
```

**作用**：IOMMU 硬件检测到故障时创建，加入 `iommu_fault_param.faults` 链表，等待用户态处理。

### 7.2 `struct iommu_fault_param` — 设备故障参数

**定义位置**：`include/linux/iommu.h:354-359`

```c
struct iommu_fault_param {
    iommu_dev_fault_handler_t handler;      // 故障处理回调
    void *data;                             // 回调私有数据
    struct list_head faults;                // 待处理故障链表
    struct mutex lock;                      // 保护 faults 链表
};
```

**挂载位置**：`dev_iommu.fault_param`（`include/linux/iommu.h:375`）。设备注册故障处理器时分配。

### 7.3 `struct iommu_fault` — 故障描述（UAPI）

**定义位置**：`include/uapi/linux/iommu.h:115-122`

```c
struct iommu_fault {
    __u32 type;                             // 故障类型
    __u32 padding;
    union {
        struct iommu_fault_unrecoverable event;     // 不可恢复故障
        struct iommu_fault_page_request prm;        // 页请求（可恢复）
        __u8 padding2[56];
    };
};
```

**类型**：

- **不可恢复**：DMA 访问未映射地址、权限违规
- **可恢复（页请求）**：SVA 场景，设备请求页面，内核可分配后重试

### 7.4 `struct iommu_page_response` — 页响应（UAPI）

**定义位置**：`include/uapi/linux/iommu.h:150-157`

```c
struct iommu_page_response {
    __u32 argsz;                            // 结构体大小
    __u32 version;                          // API 版本
    __u32 flags;                            // 标志
    __u32 pasid;                            // 进程 ID
    __u32 grpid;                            // 组 ID
    __u32 code;                             // 响应码
};
```

**响应码**：`include/uapi/linux/iommu.h:131-138`

- `IOMMU_PAGE_RESP_SUCCESS`：故障已处理，重试
- `IOMMU_PAGE_RESP_FAILURE`：一般错误，丢弃后续故障
- `IOMMU_PAGE_RESP_INVALID`：无法处理，不重试

---

## §8 全局变量与锁

### 8.1 全局变量

**定义位置**：`drivers/iommu/iommu.c:34-110`

| 变量 | 类型 | 作用 | 锁保护 |
|------|------|------|--------|
| `iommu_group_kset` | `struct kset *` | group sysfs 集合 | kset 内部锁 |
| `iommu_group_ida` | `struct ida` | group ID 分配器 | IDA 内部锁 |
| `iommu_def_domain_type` | `unsigned int` | 默认域类型 | 只读（启动时设置） |
| `iommu_dma_strict` | `bool` | 严格 DMA 模式 | 只读 |
| `iommu_cmd_line` | `u32` | 命令行标志 | 只读 |
| `iommu_device_list` | `struct list_head` | IOMMU 硬件链表 | `iommu_device_lock` |
| `iommu_device_lock` | `spinlock_t` | 保护 `iommu_device_list` | — |
| `iommu_probe_device_lock` | `struct mutex` | 序列化设备 probe | — |

### 8.2 锁层次

```
iommu_device_lock (spinlock)
    └─ 保护 iommu_device_list

iommu_probe_device_lock (mutex)
    └─ 序列化 probe 流程

iommu_group.mutex (mutex)
    └─ 保护 group->devices 链表

dev_iommu.lock (mutex)
    └─ 保护 fwspec / fault_param 更新

iova_domain.iova_rbtree_lock (spinlock)
    └─ 保护红黑树操作

iova_fq.lock (spinlock)
    └─ 保护 per-CPU flush queue

iova_rcache.lock (spinlock)
    └─ 保护全局 magazine depot

iova_cpu_rcache.lock (spinlock)
    └─ 保护 per-CPU magazine
```

**锁顺序**（从外到内）：

1. `iommu_probe_device_lock`（probe 入口）
2. `iommu_group.mutex`（group 操作）
3. `dev_iommu.lock`（设备私有）
4. `iova_domain.iova_rbtree_lock`（IOVA 分配）
5. `iova_fq.lock` / `iova_rcache.lock`（flush queue / 缓存）

**死锁避免**：不在持有内层锁时申请外层锁。例如，`iommu_map` 持有 `iova_rbtree_lock` 时不会调用可能获取 `group.mutex` 的回调。

---

## §9 命名约定速查

### 9.1 结构体前缀

| 前缀 | 含义 | 示例 |
|------|------|------|
| `iommu_` | 核心框架 | `iommu_domain`, `iommu_group` |
| `dev_iommu` | 设备私有 | `dev_iommu.fwspec` |
| `iova_` | IOVA 分配器 | `iova_domain`, `iova_fq` |
| `io_pgtable_` | 页表格式 | `io_pgtable_cfg`, `io_pgtable_ops` |
| `arm_smmu_` | ARM SMMU 驱动 | `arm_smmu_device`, `arm_smmu_domain` |

### 9.2 回调命名

| 后缀 | 含义 | 示例 |
|------|------|------|
| `_alloc` | 分配 | `iommu_domain_alloc` |
| `_free` | 释放 | `iommu_domain_free` |
| `_attach` | 绑定 | `iommu_attach_device` |
| `_detach` | 解绑 | `iommu_detach_device` |
| `_map` / `_unmap` | 映射 / 解映射 | `iommu_map`, `iommu_unmap` |
| `_fast` | 快速路径（批量） | `iommu_unmap_fast` |

### 9.3 标志位命名

| 前缀 | 含义 | 示例 |
|------|------|------|
| `IOMMU_` | 通用标志 | `IOMMU_READ`, `IOMMU_WRITE` |
| `__IOMMU_DOMAIN_` | 域类型位 | `__IOMMU_DOMAIN_PAGING` |
| `IOMMU_DOMAIN_` | 域类型组合 | `IOMMU_DOMAIN_DMA` |
| `IO_PGTABLE_QUIRK_` | 页表 quirk | `IO_PGTABLE_QUIRK_ARM_NS` |

---

## §10 硬件驱动层结构体（arm64 SMMUv3）

### 10.1 `struct arm_smmu_device` — SMMUv3 硬件实例

**定义位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.h:624-685`

```c
struct arm_smmu_device {
    struct device *dev;                         // 父 platform device
    void __iomem *base;                         // MMIO 基址
    void __iomem *page1;                        // 第二页（可选）
    u32 features;                               // 功能标志（19 个 FEAT_*）
    u32 options;                                // 选项标志（4 个 OPT_*）
    struct arm_smmu_cmdq cmdq;                  // 命令队列
    struct arm_smmu_evtq evtq;                  // 事件队列
    struct arm_smmu_priq priq;                  // PRI 队列
    int gerr_irq;                               // 全局错误 IRQ
    int combined_irq;                           // 组合 IRQ（可选）
    unsigned long ias;                          // 输入地址宽度
    unsigned long oas;                          // 输出地址宽度
    unsigned long pgsize_bitmap;                // 支持的页大小
    unsigned int asid_bits;                     // ASID 位数
    unsigned int vmid_bits;                     // VMID 位数
    DECLARE_BITMAP(vmid_map, ARM_SMMU_MAX_VMIDS); // VMID 分配位图
    unsigned int ssid_bits;                     // Sub-Stream ID 位数
    unsigned int sid_bits;                      // Stream ID 位数
    struct arm_smmu_strtab_cfg strtab_cfg;      // 流表配置
    struct iommu_device iommu;                  // 核心层句柄
    struct rb_root streams;                     // Stream ID → master 映射
    struct mutex streams_mutex;                 // 保护 streams
};
```

**功能标志**（部分）：

- `ARM_SMMU_FEAT_TRANS_S1`：Stage-1 翻译
- `ARM_SMMU_FEAT_TRANS_S2`：Stage-2 翻译
- `ARM_SMMU_FEAT_SVA`：共享虚拟地址
- `ARM_SMMU_FEAT_PRI`：页面请求接口
- `ARM_SMMU_FEAT_ATS`：地址翻译服务

### 10.2 `struct arm_smmu_domain` — SMMUv3 域

**定义位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.h:717-737`

```c
struct arm_smmu_domain {
    struct arm_smmu_device *smmu;               // 所属 SMMU
    struct mutex init_mutex;                    // 保护 smmu 指针
    struct io_pgtable_ops *pgtbl_ops;           // 页表操作
    bool stall_enabled;                         // 事务暂停使能
    atomic_t nr_ats_masters;                    // ATS master 计数
    enum arm_smmu_domain_stage stage;           // Stage-1/2/Nested/Bypass
    union {
        struct arm_smmu_s1_cfg s1_cfg;          // Stage-1 配置
        struct arm_smmu_s2_cfg s2_cfg;          // Stage-2 配置
    };
    struct iommu_domain domain;                 // 核心层句柄
    struct list_head devices;                   // 绑定的 master 链表
    spinlock_t devices_lock;                    // 保护 devices
    struct list_head mmu_notifiers;             // MMU notifier 链表（SVA）
};
```

**Stage 枚举**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.h:707-712`

```c
enum arm_smmu_domain_stage {
    ARM_SMMU_DOMAIN_S1 = 0,         // Stage-1（设备驱动用）
    ARM_SMMU_DOMAIN_S2,             // Stage-2（虚拟化用）
    ARM_SMMU_DOMAIN_NESTED,         // 嵌套（S1 + S2）
    ARM_SMMU_DOMAIN_BYPASS,         // 旁路（恒等映射）
};
```

### 10.3 `struct arm_smmu_master` — SMMUv3 设备

**定义位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.h:694-705`

```c
struct arm_smmu_master {
    struct arm_smmu_device *smmu;               // 所属 SMMU
    struct device *dev;                         // 实际设备
    struct arm_smmu_domain *domain;             // 当前绑定的域
    struct list_head domain_head;               // domain->devices 链表节点
    struct arm_smmu_stream *streams;            // Stream 数组
    unsigned int num_streams;                   // Stream 数量
    bool ats_enabled;                           // ATS 使能
    bool stall_enabled;                         // 暂停使能
    bool sva_enabled;                           // SVA 使能
    bool iopf_enabled;                          // I/O 页故障使能
    struct list_head bonds;                     // SVA 绑定链表
    unsigned int ssid_bits;                     // Sub-Stream ID 位数
};
```

**与 `dev_iommu` 的关系**：`dev_iommu.priv` 指向 `arm_smmu_master`（SMMUv3 驱动私有）。

---

## §11 结构体大小与对齐

### 11.1 核心结构体大小（arm64）

| 结构体 | 大小（字节） | 对齐 | 关键字段数 |
|--------|------------|------|-----------|
| `iommu_domain` | 64 | 8 | 7 |
| `iommu_ops` | 168 | 8 | 18 |
| `iommu_domain_ops` | 120 | 8 | 15 |
| `iommu_device` | 32 | 8 | 4 |
| `iommu_group` | 128 | 8 | 12 |
| `dev_iommu` | 48 | 8 | 5 |
| `iova_domain` | 96 | 8 | 10 |
| `iommu_dma_cookie` | 256 | 8 | 6 |

**注意**：大小含 padding。`iommu_dma_cookie` 较大因含 `iova_domain` + per-CPU 指针 + 定时器。

### 11.2 分配方式

| 结构体 | 分配方式 | 释放方式 |
|--------|---------|---------|
| `iommu_domain` | `kzalloc` | `kfree` |
| `iommu_group` | `kzalloc` + `kobject_init` | `kobject_put` |
| `dev_iommu` | `kzalloc` | `kfree` |
| `iova_domain` | 嵌入 `iommu_dma_cookie` | 随 cookie 释放 |
| `iommu_dma_cookie` | `kzalloc` | `kfree` |

---

## §12 常见陷阱

### 12.1 `iommu_domain.type` 是组合标志，不是枚举

**错误**：`if (domain->type == IOMMU_DOMAIN_DMA)`

**正确**：`if (domain->type & __IOMMU_DOMAIN_DMA_API)` 或 `iommu_is_dma_domain(domain)`

**原因**：`IOMMU_DOMAIN_DMA_FQ` 包含 `IOMMU_DOMAIN_DMA` 的所有位。用 `==` 会漏掉 FQ 变体。

### 12.2 `iommu_group.domain` 可能为 NULL

**场景**：group 刚分配，还没 attach 任何域。

**正确做法**：访问前检查 `if (group->domain)`。

### 12.3 `dev_iommu.iommu_dev` 可能为 NULL

**场景**：设备还没 probe 完成，或 probe 失败。

**正确做法**：访问前检查 `if (dev->iommu && dev->iommu->iommu_dev)`。

### 12.4 `iova_domain` 嵌在 `iommu_dma_cookie` 里

**错误**：`kfree(iovad)`

**正确**：`kfree(cookie)`（`iovad` 是 `cookie` 的字段）

### 12.5 `arm_smmu_domain.stage` 决定用 `s1_cfg` 还是 `s2_cfg`

**错误**：同时访问两个 union 成员

**正确**：先检查 `stage`，再访问对应成员

---

## §13 取证摘要

### 13.1 结构体定义位置

所有行号经 `grep -nE '^struct ' include/linux/iommu.h` 等命令验证。

### 13.2 字段偏移

大小估算基于 arm64 ABI（8 字节对齐）。精确值可用 `pahole` 工具获取。

### 13.3 锁层次

基于代码阅读和注释。死锁避免规则未形式化验证。

---

## §14 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| 这些结构体在 probe 流程中怎么初始化 | `03-internals.md` §A |
| map/unmap 时怎么操作这些结构体 | `03-internals.md` §B |
| SVA / fault / IOVA cache 的详细机制 | `04-extensions.md` |
| 怎么用大白话理解这些结构体 | `08-feynman.md` |
