---
title: API 参考
---

# IOMMU 子系统 — 01 API 与代码地图

> 头文件：`include/linux/iommu.h`（1103 行）；内部头：`drivers/iommu/dma-iommu.h`（42 行）、`include/linux/io-pgtable.h`（268 行）
> 核心实现：`drivers/iommu/iommu.c`（3276 行）、`drivers/iommu/dma-iommu.c`（1710 行）、`drivers/iommu/iova.c`（953 行）、`drivers/iommu/io-pgtable.c`（78 行）、`drivers/iommu/of_iommu.c`（180 行）

---

## §0 代码地图

### 核心文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `include/linux/iommu.h` | 1103 | 公共头文件：结构体定义、API 声明、`CONFIG_IOMMU_API` stub |
| `drivers/iommu/iommu.c` | 3276 | 核心框架：组/域管理、设备探测、总线通知、sysfs、默认域切换 |
| `drivers/iommu/dma-iommu.c` | 1710 | DMA-API 桥接层：IOVA 分配、flush queue、`dma_map_ops` 实现 |
| `drivers/iommu/iova.c` | 953 | IOVA 地址分配器：红黑树 + per-CPU 范围缓存 |
| `drivers/iommu/io-pgtable.c` | 78 | 页表格式分发：根据 `io_pgtable_fmt` 分发到具体分配器 |
| `drivers/iommu/of_iommu.c` | 180 | DT 集成：解析 `iommus` 属性和 `iommu-map` |
| `drivers/iommu/dma-iommu.h` | 42 | 内部头文件：DMA 层的跨文件声明（`iommu_get_dma_cookie` 等） |
| `include/linux/io-pgtable.h` | 268 | 页表配置结构体、flush 回调、格式枚举、quirk 定义 |
| `drivers/iommu/Kconfig` | 508 | 编译选项 |
| `drivers/iommu/iommu-traces.c` | 28 | tracepoint 定义（7 个 tracepoint） |
| `drivers/iommu/iommu-sysfs.c` | 135 | IOMMU 组/设备 sysfs 属性 |
| `drivers/iommu/iommu-sva-lib.c` | 71 | SVA 辅助函数（PASID 分配/查找） |

### arm64 IOMMU 驱动

| 驱动 | 文件 | 行数 | 硬件 |
|------|------|------|------|
| arm-smmu-v3 | `drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c` | 3971 | ARM SMMUv3 |
| arm-smmu | `drivers/iommu/arm/arm-smmu/arm-smmu.c` | 2327 | ARM SMMUv1/v2 |
| apple-dart | `drivers/iommu/apple-dart.c` | 1064 | Apple DART |
| mtk_iommu | `drivers/iommu/mtk_iommu.c` | 1587 | MediaTek IOMMU |
| ipmmu-vmsa | `drivers/iommu/ipmmu-vmsa.c` | 1166 | Renesas IPMMU |

验证命令：`wc -l drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c drivers/iommu/arm/arm-smmu/arm-smmu.c drivers/iommu/apple-dart.c drivers/iommu/mtk_iommu.c drivers/iommu/ipmmu-vmsa.c`

### 官方文档

| 路径 | 存在 |
|------|------|
| `Documentation/driver-api/iommu*` | 不存在 |
| `Documentation/devicetree/bindings/iommu/` | 11 个 YAML 文件 |

验证命令：`ls Documentation/driver-api/iommu*`（空）；`ls Documentation/devicetree/bindings/iommu/*.yaml | wc -l`（11）

---

## §1 IOMMU 硬件注册（驱动 → 核心）

头文件：`include/linux/iommu.h:382-392`；实现：`drivers/iommu/iommu.c:208-260`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_device_register(iommu, ops, dev)` | 注册 IOMMU 硬件实例 | 触发 `bus_iommu_probe()` 扫描所有总线 |
| `iommu_device_unregister(iommu)` | 注销 IOMMU 硬件实例 | |
| `iommu_device_sysfs_add(iommu, parent, groups, name, ...)` | 创建 sysfs 节点 | 通常在 register 之前调用 |
| `iommu_device_sysfs_remove(iommu)` | 删除 sysfs 节点 | |
| `iommu_device_link(iommu, dev)` | 创建 IOMMU ↔ 设备 sysfs 链接 | |
| `iommu_device_unlink(iommu, dev)` | 删除链接 | |

导出：`EXPORT_SYMBOL_GPL`，共 2 个（`iommu.c:240,251`）。

---

## §2 设备探测与生命周期

头文件：`include/linux/iommu.h:661-665`；实现：`drivers/iommu/iommu.c:283-460`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_probe_device(dev)` | 完整探测 | 由总线通知回调触发；调用 `ops->probe_device`，分配默认域，附加设备 |
| `iommu_release_device(dev)` | 释放设备 | 分离域、释放组引用；设备移除时调用 |
| `iommu_dev_enable_feature(dev, f)` | 启用设备特性 | `f` 为 `enum iommu_dev_features`（`IOMMU_DEV_FEAT_SVA`/`IOMMU_DEV_FEAT_IOPF`） |
| `iommu_dev_disable_feature(dev, f)` | 禁用设备特性 | |
| `iommu_deferred_attach(dev, domain)` | 延迟附加 | 设备驱动需要确保域已就绪时调用 |

---

## §3 域（domain）管理

头文件：`include/linux/iommu.h:422-498`；实现：`drivers/iommu/iommu.c:1890-2600`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_domain_alloc(bus)` | 分配非默认域 | 内部调用 `ops->domain_alloc`；返回 `iommu_domain *` 或 NULL |
| `iommu_domain_free(domain)` | 释放域 | 调用 `domain->ops->free` |
| `iommu_attach_device(domain, dev)` | 将设备附加到域 | 设备必须已在组中 |
| `iommu_detach_device(domain, dev)` | 从域分离设备 | 分离后设备回到默认域 |
| `iommu_attach_group(domain, group)` | 将整个组附加到域 | 组内所有设备一起切换 |
| `iommu_detach_group(domain, group)` | 从域分离组 | |
| `iommu_get_domain_for_dev(dev)` | 获取设备当前域 | 返回 `iommu_domain *` 或 NULL |
| `iommu_get_dma_domain(dev)` | 获取设备 DMA 域 | 始终返回 DMA 类型域 |
| `iommu_enable_nesting(domain)` | 启用嵌套翻译 | 调用 `domain->ops->enable_nesting` |
| `iommu_set_pgtable_quirks(domain, quirks)` | 设置页表 quirk | 必须在首次映射前调用 |
| `iommu_set_fault_handler(domain, handler, data)` | 注册域故障回调 | |
| `iommu_set_dma_strict()` | 全局切换严格 TLB 失效 | 由 `iommu.strict=1` 触发 |
| `iommu_default_passthrough()` | 查询默认域是否直通 | 返回 bool |

导出：`EXPORT_SYMBOL_GPL`，共 10 个（`iommu.c:1902-2593`）。

---

## §4 映射 / 解映射

头文件：`include/linux/iommu.h:433-447`；实现：`drivers/iommu/iommu.c:2197-2560`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_map(domain, iova, paddr, size, prot, gfp)` | 创建映射 | `prot` 用 `IOMMU_READ/WRITE/CACHE/NOEXEC/MMIO/PRIV` 组合 |
| `iommu_map_atomic(domain, iova, paddr, size, prot)` | 原子上下文映射 | 等价于 `gfp=GFP_ATOMIC` |
| `iommu_unmap(domain, iova, size)` | 删除映射并同步失效 TLB | 返回实际解映射字节数 |
| `iommu_unmap_fast(domain, iova, size, gather)` | 延迟 TLB 失效 | 需配合 `iotlb_sync` 使用 |
| `iommu_map_sg(domain, iova, sglist, nents, prot, gfp)` | 映射 scatterlist | 返回映射总字节数或负错误码 |
| `iommu_map_sg_atomic(domain, iova, sglist, nents, prot)` | 原子上下文映射 scatterlist | |
| `iommu_iova_to_phys(domain, iova)` | 查询 IOVA 对应物理地址 | 调试用 |

导出：`EXPORT_SYMBOL_GPL`，共 7 个（`iommu.c:2197-2560`）。

### 保护标志

定义位于 `include/linux/iommu.h:19-33`。

| 标志 | 值 | 含义 |
|------|----|------|
| `IOMMU_READ` | `1 << 0` | 允许读 |
| `IOMMU_WRITE` | `1 << 1` | 允许写 |
| `IOMMU_CACHE` | `1 << 2` | DMA 缓存一致性 |
| `IOMMU_NOEXEC` | `1 << 3` | 禁止执行 |
| `IOMMU_MMIO` | `1 << 4` | MMIO 映射（如 MSI doorbell） |
| `IOMMU_PRIV` | `1 << 5` | 特权级访问 |

---

## §5 组（group）管理

头文件：`include/linux/iommu.h:466-492,595-599`；实现：`drivers/iommu/iommu.c:706-1576`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_group_alloc()` | 创建新组 | 含 kobject 引用 |
| `iommu_group_get(dev)` | 获取设备所在组 | 增加引用计数 |
| `iommu_group_put(group)` | 释放组引用 | |
| `iommu_group_ref_get(group)` | 增加组引用 | |
| `iommu_group_get_by_id(id)` | 按 ID 查找组 | sysfs：`/sys/kernel/iommu_groups/<id>/` |
| `iommu_group_id(group)` | 获取组 ID | |
| `iommu_group_add_device(group, dev)` | 将设备加入组 | 创建 sysfs 链接 |
| `iommu_group_remove_device(dev)` | 将设备从组移除 | |
| `iommu_group_for_each_dev(group, data, fn)` | 遍历组内设备 | 回调返回非 0 中止 |
| `iommu_group_get_iommudata(group)` | 获取组私有数据 | |
| `iommu_group_set_iommudata(group, data, notify)` | 设置组私有数据 | |
| `iommu_group_set_name(group, name)` | 设置组名 | |
| `iommu_group_claim_dma_owner(group, owner)` | 声明 DMA 所有权 | VFIO 使用 |
| `iommu_group_release_dma_owner(group)` | 释放 DMA 所有权 | |
| `iommu_group_dma_owner_claimed(group)` | 查询所有权状态 | 返回 bool |

导出：`EXPORT_SYMBOL_GPL`，共 18 个（`iommu.c:764-1576`）。

### 预定义组分配函数

| API | 说明 | 备注 |
|-----|------|------|
| `generic_device_group(dev)` | 每设备一个组 | platform/amba 设备使用 |
| `pci_device_group(dev)` | 按 PCI 拓扑分组 | 考虑 ACS、alias、多函数 |
| `fsl_mc_device_group(dev)` | FSL MC 总线分组 | |

---

## §6 SVA（共享虚拟地址）

头文件：`include/linux/iommu.h:667-671`；实现：`drivers/iommu/iommu.c:2773-2870`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_sva_bind_device(dev, mm)` | 绑定进程地址空间到设备 | 需先启用 `IOMMU_DEV_FEAT_SVA` |
| `iommu_sva_unbind_device(handle)` | 解绑 | |
| `iommu_sva_get_pasid(handle)` | 获取 PASID | 失败返回 `IOMMU_SVA_PASID_INVALID` |

SVA 辅助库（`drivers/iommu/iommu-sva-lib.c`）：

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_sva_alloc_pasid(mm, min, max)` | 为 mm 分配或获取已有 PASID | |
| `iommu_sva_find(pasid)` | 根据 PASID 查找 mm | 返回时持有 mm 引用，需 `mmput()` 释放 |

导出：`EXPORT_SYMBOL_GPL`，核心 3 个 + 库 2 个。

---

## §7 故障处理

头文件：`include/linux/iommu.h:480-500`；实现：`drivers/iommu/iommu.c:1164-1365,2544-2570`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_register_device_fault_handler(dev, handler, data)` | 注册设备级故障处理器 | 回调接收 `iommu_fault_event` |
| `iommu_unregister_device_fault_handler(dev)` | 注销故障处理器 | |
| `iommu_report_device_fault(dev, evt)` | 报告设备故障 | IOMMU 驱动检测到故障时调用 |
| `iommu_page_response(dev, msg)` | 回应页面故障 | PRI/SVM 场景 |
| `report_iommu_fault(domain, dev, iova, fault)` | 向域故障处理器报告 | 由 IOMMU 驱动调用 |

---

## §8 fwspec / DT 集成

头文件：`include/linux/iommu.h:627-630`；实现：`drivers/iommu/iommu.c:2678-2740`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_fwspec_init(dev, iommu_fwnode, ops)` | 初始化固件规格 | 由 `of_iommu_xlate` 调用 |
| `iommu_fwspec_free(dev)` | 释放固件规格 | |
| `iommu_fwspec_add_ids(dev, ids, num_ids)` | 添加 Stream ID | |

---

## §9 DMA 集成层

头文件：`include/linux/iommu.h:1071-1076`、`drivers/iommu/dma-iommu.h`；实现：`drivers/iommu/dma-iommu.c`

| API | 说明 | 备注 |
|-----|------|------|
| `iommu_setup_dma_ops(dev, dma_base, dma_limit)` | 设置设备 `dma_ops` | 设备探测时由核心调用 |
| `iommu_get_dma_cookie(domain)` | 为域分配 DMA cookie | DMA 域初始化时调用 |
| `iommu_put_dma_cookie(domain)` | 释放 DMA cookie | |
| `iommu_dma_init_fq(domain)` | 初始化 flush queue | DMA-FQ 域使用 |
| `iommu_dma_get_resv_regions(dev, list)` | 获取 DMA 保留区域 | 驱动在 `get_resv_regions` 回调中调用 |
| `iommu_get_msi_cookie(domain, base)` | 为 MSI 分配 IOVA cookie | IRQ 子系统调用 |
| `iommu_dma_prepare_msi(desc, msi_addr)` | MSI 准备 | |
| `iommu_dma_compose_msi_msg(desc, msg)` | MSI 消息组合 | |

导出：`iommu.c` 0 个；`dma-iommu.c` 3 个（`EXPORT_SYMBOL`/`EXPORT_SYMBOL_GPL`，`dma-iommu.c:351,396,1609`）。

---

## §10 IOVA 分配器

头文件：`include/linux/iova.h`；实现：`drivers/iommu/iova.c`

| API | 说明 | 备注 |
|-----|------|------|
| `init_iova_domain(iovad, granule, start_pfn)` | 初始化 IOVA 域 | `granule` 为最小粒度 |
| `put_iova_domain(iovad)` | 销毁 IOVA 域 | |
| `alloc_iova(iovad, size, limit_pfn, size_warn)` | 分配 IOVA | `size` 以 granule 为单位 |
| `free_iova(iovad, pfn)` | 释放 IOVA | |
| `__free_iova(iovad, iova)` | 释放 `iova` 结构体 | |
| `find_iova(iovad, pfn)` | 按 PFN 查找 | |
| `alloc_iova_fast(iovad, size, limit_pfn, size_warn)` | 快速分配 | 先尝试 rcache |
| `free_iova_fast(iovad, iova)` | 快速释放 | 先尝试插入 rcache |
| `reserve_iova(iovad, pfn_lo, pfn_hi)` | 预留范围 | 用于保留区域 |
| `iova_cache_get()` / `iova_cache_put()` | 结构体缓存引用计数 | |
| `iova_domain_init_rcaches(iovad)` | 初始化 per-CPU 范围缓存 | |

导出：`EXPORT_SYMBOL_GPL`，共 12 个（`iova.c:74-761`）。

全树消费者：8 个文件。验证命令：`grep -rl 'init_iova_domain' drivers/ --include='*.c' | wc -l`

---

## §11 页表格式

头文件：`include/linux/io-pgtable.h`；实现：`drivers/iommu/io-pgtable.c`

| API | 说明 | 备注 |
|-----|------|------|
| `alloc_io_pgtable_ops(fmt, cfg, cookie)` | 分配页表操作 | `fmt` 为 `enum io_pgtable_fmt` |
| `free_io_pgtable_ops(ops)` | 释放页表 | 先调用 `tlb_flush_all` |

导出：`EXPORT_SYMBOL_GPL`，共 2 个（`io-pgtable.c:61,78`）。

全树消费者：13 个文件。验证命令：`grep -rl 'alloc_io_pgtable_ops' drivers/ --include='*.c' | wc -l`

### arm64 可用页表格式

| 格式枚举 | 描述 | 使用者 |
|----------|------|--------|
| `ARM_64_LPAE_S1` | ARMv8 长描述符 Stage-1 | arm-smmu, arm-smmu-v3 |
| `ARM_64_LPAE_S2` | ARMv8 长描述符 Stage-2 | arm-smmu-v3（嵌套） |
| `ARM_32_LPAE_S1` | ARMv7 长描述符 Stage-1 | arm-smmu（32 位） |
| `ARM_32_LPAE_S2` | ARMv7 长描述符 Stage-2 | arm-smmu（32 位嵌套） |
| `ARM_V7S` | ARMv7 短描述符 | arm-smmu（旧硬件） |
| `APPLE_DART` / `APPLE_DART2` | Apple DART 格式 | apple-dart |

---

## §12 域类型

定义位于 `include/linux/iommu.h:61-88`。

### 组合标志（内部）

| 标志 | 值 | 含义 |
|------|----|------|
| `__IOMMU_DOMAIN_PAGING` | `1U << 0` | 支持页表映射 |
| `__IOMMU_DOMAIN_DMA_API` | `1U << 1` | 由 DMA-API 使用 |
| `__IOMMU_DOMAIN_PT` | `1U << 2` | 直通（identity mapped） |
| `__IOMMU_DOMAIN_DMA_FQ` | `1U << 3` | 使用延迟失效队列 |

### 域类型

| 类型 | 值 | 组合 | 含义 |
|------|----|------|------|
| `IOMMU_DOMAIN_BLOCKED` | 0 | — | 所有 DMA 被阻止 |
| `IOMMU_DOMAIN_IDENTITY` | `__IOMMU_DOMAIN_PT` | bit 2 | 直通（IOVA = PA） |
| `IOMMU_DOMAIN_UNMANAGED` | `__IOMMU_DOMAIN_PAGING` | bit 0 | 用户管理（VFIO） |
| `IOMMU_DOMAIN_DMA` | `PAGING \| DMA_API` | bit 0+1 | DMA-API 管理（严格） |
| `IOMMU_DOMAIN_DMA_FQ` | `PAGING \| DMA_API \| DMA_FQ` | bit 0+1+3 | DMA-API 管理（延迟） |

---

## §13 错误码语义

| 错误码 | 含义 | 常见场景 |
|--------|------|----------|
| `-EINVAL` | 参数无效 | 域类型不支持、IOVA 未对齐、页大小不匹配 |
| `-EBUSY` | 资源被占用 | 组已有 owner、设备仍绑定驱动 |
| `-ENOMEM` | 内存不足 | IOVA 分配失败、页表内存分配失败 |
| `-ENODEV` | 设备无 IOMMU | 设备未关联 IOMMU 实例 |
| `-EOPNOTSUPP` | 操作不支持 | 驱动未实现该回调 |
| `-ENOSPC` | 空间不足 | IOVA 地址空间耗尽 |
| `-ERANGE` | 范围错误 | 映射超出 IOMMU 地址宽度 |

---

## §14 内核参数与初始化

| 参数 | 定义位置 | 含义 | 默认值 |
|------|----------|------|--------|
| `iommu.passthrough=` | `drivers/iommu/iommu.c:438` | 默认域类型：0=DMA，1=IDENTITY | Kconfig 决定 |
| `iommu.strict=` | `drivers/iommu/iommu.c:448` | TLB 失效策略：0=延迟，1=严格 | Kconfig 决定 |
| `iommu.forcedac=` | `drivers/iommu/dma-iommu.c:84` | PCI DAC：0=不强制，1=强制 64 位 | 0 |

### 初始化序列

| 级别 | 函数 | 位置 |
|------|------|------|
| `core_initcall` | `iommu_init` | `drivers/iommu/iommu.c:2572` |
| `subsys_initcall` | `iommu_subsys_init` | `drivers/iommu/iommu.c:190` |

---

## §15 Tracepoint

定义位于 `drivers/iommu/iommu-traces.c`，声明位于 `include/trace/events/iommu.h`。

| Tracepoint | 触发点 |
|------------|--------|
| `add_device_to_group` | 设备加入组 |
| `remove_device_from_group` | 设备移出组 |
| `attach_device_to_domain` | 设备附加到域 |
| `detach_device_from_domain` | 设备从域分离 |
| `map` | IOVA→PA 映射 |
| `unmap` | 解映射 |
| `io_page_fault` | I/O 页面故障 |

共 7 个，全部 `EXPORT_TRACEPOINT_SYMBOL_GPL`。
