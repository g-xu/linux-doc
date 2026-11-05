---
title: 使用指南
---

# IOMMU 子系统 — 使用示例

> **定位**：各角色的代码骨架——IOMMU 驱动、设备驱动（DMA-API）、VFIO、SVA
>
> **目标架构**：arm64（x86/PowerPC 等架构特例不展开）
>
> **前置阅读**：`01-api.md`（API 速查）、`02-datastructures.md`（数据结构）
>
> **声明**：以下骨架**未编译**，仅示意 API 用法。实际代码请参考引用的源文件。

---

## §0 角色枚举

| 角色 | 职责 | 典型实现 | 本篇章节 |
|------|------|---------|---------|
| IOMMU 驱动 | 实现 `iommu_ops`，管理硬件 | `arm-smmu-v3.c` | §1 |
| 设备驱动（DMA-API） | 使用 `dma_map_*` 进行 DMA | 网卡、块设备驱动 | §2 |
| VFIO | 用户态 DMA 映射 | `vfio_iommu_type1.c` | §3 |
| SVA 设备驱动 | 绑定进程地址空间 | `idxd`、`uacce` | §4 |

---

## §1 IOMMU 驱动 — arm-smmu-v3

### §1.1 模块注册

**位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:3961`

```c
static struct platform_driver arm_smmu_driver = {
    .driver = {
        .name   = "arm-smmu-v3",
        .of_match_table = of_match_ptr(arm_smmu_of_match),
    },
    .probe  = arm_smmu_device_probe,
    .remove = arm_smmu_device_remove,
};
module_platform_driver(arm_smmu_driver);
```

### §1.2 Probe 流程

**位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:3810-3950`

```c
static int arm_smmu_device_probe(struct platform_device *pdev)
{
    struct arm_smmu_device *smmu;
    struct device *dev = &pdev->dev;
    int ret;

    // 1. 分配私有数据
    smmu = devm_kzalloc(dev, sizeof(*smmu), GFP_KERNEL);
    if (!smmu)
        return -ENOMEM;
    smmu->dev = dev;

    // 2. DT/ACPI probe
    if (dev->of_node)
        ret = arm_smmu_device_dt_probe(pdev, smmu);
    else
        ret = arm_smmu_device_acpi_probe(pdev, smmu);

    // 3. 映射 MMIO
    res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    smmu->base = arm_smmu_ioremap(dev, res->start, ARM_SMMU_REG_SZ);

    // 4. 获取中断
    irq = platform_get_irq_byname_optional(pdev, "combined");
    if (irq > 0)
        smmu->combined_irq = irq;

    // 5. 初始化队列
    ret = arm_smmu_init_structures(smmu);
    if (ret)
        return ret;

    // 6. 注册到 IOMMU 核心
    ret = iommu_device_sysfs_add(&smmu->iommu, dev, NULL, "smmu3.%s",
                                  dev_name(dev));
    ret = iommu_device_register(&smmu->iommu, &arm_smmu_ops, dev);

    return 0;
}
```

### §1.3 填充 iommu_ops

**位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2871-2898`

```c
static struct iommu_ops arm_smmu_ops = {
    .capable        = arm_smmu_capable,
    .domain_alloc   = arm_smmu_domain_alloc,
    .probe_device   = arm_smmu_probe_device,
    .release_device = arm_smmu_release_device,
    .device_group   = arm_smmu_device_group,
    .of_xlate       = arm_smmu_of_xlate,
    .get_resv_regions = arm_smmu_get_resv_regions,
    .sva_bind       = arm_smmu_sva_bind,
    .sva_unbind     = arm_smmu_sva_unbind,
    .sva_get_pasid  = arm_smmu_sva_get_pasid,
    .page_response  = arm_smmu_page_response,
    .def_domain_type = arm_smmu_def_domain_type,
    .pgsize_bitmap  = -1UL,  // 在 attach 时限制
    .owner          = THIS_MODULE,
    .default_domain_ops = &(const struct iommu_domain_ops) {
        .attach_dev     = arm_smmu_attach_dev,
        .map_pages      = arm_smmu_map_pages,
        .unmap_pages    = arm_smmu_unmap_pages,
        .flush_iotlb_all = arm_smmu_flush_iotlb_all,
        .iotlb_sync     = arm_smmu_iotlb_sync,
        .iova_to_phys   = arm_smmu_iova_to_phys,
        .enable_nesting = arm_smmu_enable_nesting,
        .free           = arm_smmu_domain_free,
    },
};
```

### §1.4 关键回调实现

#### probe_device

**位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2650-2730`（推断）

```c
static struct iommu_device *arm_smmu_probe_device(struct device *dev)
{
    struct arm_smmu_device *smmu;
    struct arm_smmu_master *master;
    int ret;

    // 1. 查找 SMMU（通过 fwspec）
    smmu = arm_smmu_get_by_fwnode(dev_iommu_fwspec_get(dev)->iommu_fwnode);
    if (!smmu)
        return ERR_PTR(-ENODEV);

    // 2. 分配 master
    master = kzalloc(sizeof(*master), GFP_KERNEL);
    master->smmu = smmu;
    master->dev = dev;

    // 3. 插入 stream 表
    ret = arm_smmu_insert_master(smmu, master);
    if (ret)
        goto err_free_master;

    // 4. 保存私有数据
    dev_iommu_priv_set(dev, master);

    return &smmu->iommu;

err_free_master:
    kfree(master);
    return ERR_PTR(ret);
}
```

#### domain_alloc

**位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:1880-1920`（推断）

```c
static struct iommu_domain *arm_smmu_domain_alloc(unsigned type)
{
    struct arm_smmu_domain *smmu_domain;

    if (type != IOMMU_DOMAIN_UNMANAGED &&
        type != IOMMU_DOMAIN_DMA &&
        type != IOMMU_DOMAIN_IDENTITY &&
        type != IOMMU_DOMAIN_DMA_FQ)
        return NULL;

    smmu_domain = kzalloc(sizeof(*smmu_domain), GFP_KERNEL);
    mutex_init(&smmu_domain->init_mutex);
    spin_lock_init(&smmu_domain->devices_lock);
    INIT_LIST_HEAD(&smmu_domain->devices);

    smmu_domain->domain.type = type;
    smmu_domain->domain.ops = arm_smmu_ops.default_domain_ops;

    return &smmu_domain->domain;
}
```

---

## §2 设备驱动（DMA-API）

### §2.1 典型用法

**参考**：`drivers/net/ethernet/intel/e1000/e1000_main.c:4521`

```c
// 映射单个页面
dma_addr = dma_map_page(&pdev->dev, page, offset, size, DMA_TO_DEVICE);
if (dma_mapping_error(&pdev->dev, dma_addr)) {
    dev_err(&pdev->dev, "dma_map_page failed\n");
    return -ENOMEM;
}

// 配置描述符
tx_desc->buffer_addr = cpu_to_le64(dma_addr);

// ... 设备 DMA ...

// 解映射
dma_unmap_page(&pdev->dev, dma_addr, size, DMA_TO_DEVICE);
```

### §2.2 一致性 DMA

**参考**：`drivers/net/ethernet/intel/e1000/e1000_main.c:1514`

```c
// 分配一致性内存
txdr->desc = dma_alloc_coherent(&pdev->dev, txdr->size, &txdr->dma, GFP_KERNEL);
if (!txdr->desc)
    return -ENOMEM;

// ... 使用 txdr->desc（CPU）和 txdr->dma（设备）...

// 释放
dma_free_coherent(&pdev->dev, txdr->size, txdr->desc, txdr->dma);
```

### §2.3 流式 DMA（scatterlist）

```c
struct scatterlist *sg;
int nents, mapped_nents;

// 映射 scatterlist
mapped_nents = dma_map_sg(&pdev->dev, sg, nents, DMA_TO_DEVICE);
if (mapped_nents == 0) {
    dev_err(&pdev->dev, "dma_map_sg failed\n");
    return -ENOMEM;
}

// 遍历映射后的 sg
for_each_sg(sg, s, mapped_nents, i) {
    dma_addr_t addr = sg_dma_address(s);
    size_t len = sg_dma_len(s);
    // 配置描述符
}

// ... 设备 DMA ...

// 解映射
dma_unmap_sg(&pdev->dev, sg, nents, DMA_TO_DEVICE);
```

### §2.4 近邻符号区分

| 符号 | 用途 | 错用后果 |
|------|------|---------|
| `dma_map_page` | 映射单个页面 | — |
| `dma_map_single` | 映射虚拟地址（已废弃） | 编译警告 |
| `dma_map_sg` | 映射 scatterlist | — |
| `dma_alloc_coherent` | 分配一致性内存 | — |
| `dma_map_page_attrs` | 带属性的映射 | 通常不需要 |

---

## §3 VFIO — 用户态 DMA 映射

### §3.1 分配 Domain

**位置**：`drivers/vfio/vfio_iommu_type1.c:2236-2240`

```c
static int vfio_iommu_domain_alloc(struct device *dev, void *data)
{
    struct iommu_domain **domain = data;
    *domain = iommu_domain_alloc(dev->bus);
    return 0;
}
```

### §3.2 Attach Group

**位置**：`drivers/vfio/vfio_iommu_type1.c:2309`

```c
ret = iommu_attach_group(domain->domain, group->iommu_group);
if (ret) {
    iommu_domain_free(domain->domain);
    return ret;
}
```

### §3.3 映射 DMA

**位置**：`drivers/vfio/vfio_iommu_type1.c:1519`

```c
ret = iommu_map(d->domain, iova, (phys_addr_t)pfn << PAGE_SHIFT,
                PAGE_SIZE, prot, GFP_KERNEL);
```

### §3.4 完整流程

```c
// 1. 分配 domain
domain = iommu_domain_alloc(&platform_bus_type);

// 2. attach group
iommu_attach_group(domain, group->iommu_group);

// 3. 用户态 ioctl 映射
iommu_map(domain, iova, paddr, size, IOMMU_READ | IOMMU_WRITE, GFP_KERNEL);

// 4. 设备 DMA（通过 VFIO 字符设备）

// 5. 用户态 ioctl 解映射
iommu_unmap(domain, iova, size);

// 6. detach + 释放
iommu_detach_group(domain, group->iommu_group);
iommu_domain_free(domain);
```

---

## §4 SVA 设备驱动

### §4.1 绑定进程地址空间

**参考**：`drivers/dma/idxd/cdev.c:119`

```c
struct iommu_sva *sva;

// 绑定当前进程
sva = iommu_sva_bind_device(dev, current->mm, NULL);
if (IS_ERR(sva)) {
    dev_err(dev, "SVA bind failed: %ld\n", PTR_ERR(sva));
    return PTR_ERR(sva);
}

// 获取 PASID
u32 pasid = iommu_sva_get_pasid(sva);
dev_info(dev, "SVA bound with PASID %u\n", pasid);

// 设备使用进程虚拟地址进行 DMA
// IOVA = 进程 VA，IOMMU 翻译为 PA

// ... 设备 DMA ...

// 解绑定
iommu_sva_unbind_device(sva);
```

### §4.2 故障处理

```c
// 注册故障处理器
int my_fault_handler(struct iommu_fault *fault, void *data)
{
    if (fault->type == IOMMU_FAULT_PAGE_REQ) {
        // 处理页请求
        struct iommu_page_response resp = {
            .pasid = fault->prm.pasid,
            .grpid = fault->prm.grpid,
            .code = IOMMU_PAGE_RESP_SUCCESS,
        };
        iommu_page_response(dev, &resp);
    }
    return 0;
}

iommu_register_device_fault_handler(dev, my_fault_handler, NULL);

// ... 使用 SVA ...

// 注销
iommu_unregister_device_fault_handler(dev);
```

---

## §5 近邻符号区分

### §5.1 `iommu_map` vs `dma_map_page`

| 符号 | 调用者 | 语义 |
|------|--------|------|
| `iommu_map` | VFIO、IOMMU 用户 | 直接操作 IOMMU 页表 |
| `dma_map_page` | 设备驱动 | 通过 DMA-API，自动管理 IOVA |

**错用后果**：设备驱动直接用 `iommu_map` 会绕过 DMA-API 的 IOVA 分配器和 flush queue，导致性能下降和潜在错误。

### §5.2 `iommu_attach_device` vs `iommu_attach_group`

| 符号 | 语义 |
|------|------|
| `iommu_attach_device` | 单设备 attach（已废弃） |
| `iommu_attach_group` | 整个 group attach（推荐） |

**错用后果**：`iommu_attach_device` 要求 group 只有一个设备，多设备 group 会返回 `-EINVAL`。

### §5.3 `iommu_domain_alloc` vs `iommu_sva_bind_device`

| 符号 | 语义 |
|------|------|
| `iommu_domain_alloc` | 分配传统 DMA 域 |
| `iommu_sva_bind_device` | 绑定进程地址空间（SVA） |

**错用后果**：SVA 场景用 `iommu_domain_alloc` 无法实现设备直接使用进程 VA。

---

## §6 取证摘要

### §6.1 符号存在性

所有 API 经 `grep -rn` 全树验证存在：

- `iommu_device_register` ✓
- `iommu_sva_bind_device` ✓
- `dma_map_page` ✓
- `iommu_attach_group` ✓

### §6.2 代码示例来源

示例取自真实驱动代码，简化后保留核心逻辑。

### §6.3 未编译声明

以上骨架**未编译**，仅示意 API 用法。实际代码请参考引用的源文件。

---

## §7 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| 设备树怎么配置 IOMMU | `06-devicetree.md` |
| 怎么调试 IOMMU 问题 | `07-debug.md` |
| 用大白话理解这些概念 | `08-feynman.md` |
