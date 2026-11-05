---
title: 费曼学习法
---

# IOMMU 子系统 — 费曼解释

> **定位**：用大白话 + 生活类比解释 IOMMU 里最难理解的概念
>
> **目标架构**：arm64
>
> **前置阅读**：`00-overview.md`（概念模型）

---

## §1 `iommu_device` 和 `iommu_domain` 为什么分两个结构体？

### 先举个例子

想象一个快递公司：

- **`iommu_device`** = 快递分拣中心（物理硬件）
  - 一个城市有一个
  - 负责处理所有包裹
  - 永远在那里，不会消失

- **`iommu_domain`** = 快递路线规划（翻译规则）
  - 每个客户有一套
  - 决定包裹怎么走
  - 客户走了就销毁

**关键区别**：分拣中心是**固定资产**，路线规划是**临时方案**。一个分拣中心可以同时服务多个客户（多个 domain），一个客户也可以在不同时间用不同的路线（切换 domain）。

### 回到代码

**位置**：`include/linux/iommu.h:326-331`（`iommu_device`）、`include/linux/iommu.h:90-98`（`iommu_domain`）

```c
struct iommu_device {
    struct list_head list;          // 全局链表
    const struct iommu_ops *ops;    // 驱动回调
    struct device *dev;             // 父设备
};

struct iommu_domain {
    unsigned type;                  // 域类型
    const struct iommu_domain_ops *ops;  // 域操作
    struct iommu_dma_cookie *iova_cookie;  // IOVA 分配器
};
```

**生命周期差异**：

- `iommu_device`：SMMU probe 时创建（`arm_smmu_device_probe`），系统关闭时销毁
- `iommu_domain`：设备 attach 时分配（`iommu_attach_group`），detach 时释放

### 这个类比什么时候会误导你？

快递公司类比忽略了**硬件寄存器**。真实的 `iommu_device` 包含 MMIO 基址、中断号、队列配置等硬件状态，不是纯逻辑概念。`iommu_domain` 则包含页表根指针、geometry 等翻译上下文。两者都有硬件对应的状态，只是生命周期不同。

---

## §2 `iommu_group` 为什么不是 `iommu_domain`？

### 先举个例子

想象一栋公寓楼：

- **`iommu_group`** = 公寓单元（隔离边界）
  - 每个单元有独立的门
  - 单元内的房间共享走廊
  - 单元之间完全隔离

- **`iommu_domain`** = 装修风格（翻译规则）
  - 每个单元可以选择不同的装修
  - 可以换装修，但单元结构不变

**关键区别**：单元是**物理隔离**，装修是**逻辑规则**。你不能把两个单元打通（group 不可变），但可以换装修风格（domain 可切换）。

### 回到代码

**位置**：`drivers/iommu/iommu.c:41-56`

```c
struct iommu_group {
    struct list_head devices;               // 设备列表
    struct iommu_domain *default_domain;    // 默认域
    struct iommu_domain *blocking_domain;   // 隔离域
    struct iommu_domain *domain;            // 当前域
};
```

**为什么 group 不可变**：PCI 设备的 group 由硬件拓扑决定（ACS 能力、alias 关系），probe 时一次性计算（`pci_device_group`），之后不会改变。

**为什么 domain 可变**：VFIO 可以在运行时切换 domain（从 DMA 域切换到 blocking 域），实现设备隔离。

### 这个类比什么时候会误导你？

公寓楼类比忽略了**硬件隔离粒度**。真实的 group 划分依赖 ACS（Access Control Services）能力，某些硬件的隔离粒度很粗（整个 PCIe switch 下游设备共享一个 group），不是完美的"单元隔离"。

---

## §3 IOVA 分配器为什么用红黑树 + 缓存？

### 先举个例子

想象一个停车场：

- **红黑树** = 停车场的电子地图
  - 记录每个车位是否占用
  - 找空车位：O(log N)
  - 精确但慢

- **范围缓存** = 门口的快速通道
  - 记住最近常用的几个车位
  - 找车位：O(1)
  - 快速但只缓存小的

**关键区别**：电子地图覆盖所有车位，快速通道只缓存小车（≤ 64 页）。大车直接查地图，小车先查快速通道。

### 回到代码

**位置**：`include/linux/iova.h:28-42`（`iova_domain`）、`drivers/iommu/iova.c:630-646`（缓存结构）

```c
struct iova_domain {
    struct rb_root rbroot;              // 红黑树
    struct iova_rcache *rcaches;        // 范围缓存
};

struct iova_rcache {
    struct iova_magazine *depot[MAX_GLOBAL_MAGS];  // 全局仓库
    struct iova_cpu_rcache __percpu *cpu_rcaches;  // per-CPU 缓存
};
```

**性能收益**：

- 红黑树：O(log N)，适合大分配
- 缓存：O(1)，适合小分配（≤ 64 页）
- per-CPU：无锁快速路径

### 这个类比什么时候会误导你？

停车场类比忽略了**对齐约束**。真实的 IOVA 分配必须按页大小对齐（4K/2M/1G），不是任意位置。红黑树节点存的是 `[pfn_lo, pfn_hi]` 范围，不是单个车位。

---

## §4 Flush Queue 为什么延迟刷新 TLB？

### 先举个例子

想象一个图书馆：

- **TLB** = 图书馆的常用书书架
  - 放最常借的书
  - 查找快

- **页表** = 图书馆的仓库
  - 放所有书
  - 查找慢

- **Flush Queue** = 还书车
  - 读者还书时，先放还书车
  - 每隔 10 分钟，管理员把还书车推回仓库
  - 批量处理，效率高

**关键区别**：立即还书（立即刷新 TLB）每次都要跑一趟仓库，延迟高；用还书车（flush queue）批量处理，延迟低但有不一致窗口。

### 回到代码

**位置**：`drivers/iommu/dma-iommu.c:93-105`（flush queue 结构）、`drivers/iommu/dma-iommu.c:157-165`（定时器）

```c
struct iova_fq {
    struct iova_fq_entry entries[IOVA_FQ_SIZE];  // 环形缓冲区
    unsigned int head, tail;
};

// 定时器回调
static void fq_flush_timeout(struct timer_list *t)
{
    // 遍历队列，刷新 TLB，释放物理页
}

// 10ms 定时器
mod_timer(&cookie->fq_timer, jiffies + msecs_to_jiffies(10));
```

**设计权衡**：

- 立即刷新：一致性高，性能差
- 延迟 10ms：一致性好，性能好
- per-CPU 队列：减少锁竞争

### 这个类比什么时候会误导你？

图书馆类比忽略了**硬件一致性**。真实的 TLB 刷新是硬件操作，不是软件搬运。延迟刷新意味着设备可能访问到已释放的物理页（如果设备还在 DMA），所以必须在设备停止 DMA 后才能延迟刷新。

---

## §5 SVA 为什么需要 PASID？

### 先举个例子

想象一个银行：

- **进程** = 客户
- **设备** = 客户的代理人
- **PASID** = 代理人胸牌

没有 PASID：

- 代理人去银行办事，银行不知道他是谁的代理人
- 必须提前授权（显式 DMA 映射）

有 PASID：

- 代理人戴着胸牌（PASID）
- 银行看胸牌就知道他是哪个客户的代理人
- 可以直接访问客户的账户（进程地址空间）

**关键区别**：PASID 是**身份标识**，让设备可以代表进程访问内存，无需提前映射。

### 回到代码

**位置**：`drivers/iommu/iommu-sva-lib.c:25-60`（PASID 分配）、`drivers/iommu/iommu.c:2788-2830`（SVA bind）

```c
int iommu_sva_alloc_pasid(struct mm_struct *mm, ioasid_t min, ioasid_t max)
{
    // 分配 PASID
    pasid = ioasid_alloc(&iommu_sva_pasid, min, max, mm);
    mm_pasid_set(mm, pasid);
}

struct iommu_sva *iommu_sva_bind_device(struct device *dev, struct mm_struct *mm, ...)
{
    // 绑定进程地址空间
    handle = ops->sva_bind(dev, mm, drvdata);
}
```

**流程**：

1. 分配 PASID → 保存到 `mm->pasid`
2. 设备 bind mm → IOMMU 配置页表，IOVA = 进程 VA
3. 设备 DMA 时用 PASID 标识进程
4. IOMMU 根据 PASID 选择页表

### 这个类比什么时候会误导你？

银行类比忽略了**页故障处理**。真实的 SVA 场景，设备访问未映射地址时会触发页请求（Page Request），内核需要分配物理页后重试。这不是"直接访问账户"，而是"按需分配"。

---

## ★ §6 `iommu_device` 和 `struct device` 为什么容易混淆？

### 先举个例子

想象一个人：

- **`struct device`** = 人的身份证
  - 每个人都有
  - 记录基本信息（名字、地址）
  - 系统核心管理

- **`iommu_device`** = 人的 IOMMU 会员卡
  - 只有部分人有（有 IOMMU 的设备）
  - 记录 IOMMU 特定信息（ops、fwnode）
  - IOMMU 子系统管理

**关键区别**：身份证是**通用的**，会员卡是**专用的**。`struct device` 代表设备本身，`iommu_device` 代表设备在 IOMMU 子系统中的身份。

### 回到代码

**位置**：`include/linux/iommu.h:326-331`

```c
struct iommu_device {
    struct list_head list;              // IOMMU 全局链表
    const struct iommu_ops *ops;        // IOMMU 驱动回调
    struct fwnode_handle *fwnode;       // IOMMU 固件节点
    struct device *dev;                 // ★ 指向父设备
};
```

**关系**：

- `iommu_device.dev` → `struct device`（父设备）
- `struct device->driver_data` → `iommu_device`（通过 `iommu_device_register` 设置）

**为什么不会混淆**：`iommu_device` 只存在于 IOMMU 硬件本身（如 SMMU），不存在于普通设备（如网卡）。网卡有 `struct device`，但没有 `iommu_device`。

### 这个类比什么时候会误导你？

会员卡类比忽略了**层级关系**。真实的 `iommu_device` 是 IOMMU 硬件的代表，管理多个普通设备。普通设备通过 `dev->iommu->iommu_dev` 指向管理它的 `iommu_device`。这是一个**一对多**关系，不是平级关系。

---

## §7 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| 证据和取证方法 | `99-evidence.md` |
| 回顾全貌 | `00-overview.md` |
| API 速查 | `01-api.md` |
