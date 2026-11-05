---
title: 内部实现
---

# IOMMU 子系统 — 内部流程

> **定位**：核心流程逐段剖析——注册、probe、attach、map/unmap、flush queue
>
> **目标架构**：arm64（x86/PowerPC 等架构特例不展开）
>
> **前置阅读**：`01-api.md`（API 速查）、`02-datastructures.md`（数据结构）

---

## §0 导航

### 本篇组织轴

**三条轴，互不重叠**：

- **Part A：时间线**（§1-§7）—— 从 IOMMU 驱动注册到设备 DMA 的完整生命周期
- **Part B：横切面**（§8-§10）—— 锁、错误处理、性能优化
- **Part C：评价**（§11）—— 设计债、REVISIT/FIXME、架构限制

### 时间线概览

```
IOMMU 驱动 probe
    │
    ├─► iommu_device_register()          §1
    │       └─► bus_iommu_probe()
    │               └─► __iommu_probe_device()    §2
    │                       ├─► ops->probe_device()
    │                       ├─► iommu_group_get_for_dev()    §3
    │                       └─► iommu_alloc_default_domain() §4
    │
    ├─► 设备 attach domain                 §5
    │       ├─► iommu_attach_group()
    │       └─► ops->attach_dev()
    │
    ├─► DMA 映射                          §6
    │       ├─► dma_map_page()
    │       ├─► iommu_map()
    │       └─► ops->map_pages()
    │
    └─► DMA 解映射 + flush queue          §7
            ├─► iommu_unmap()
            ├─► queue_iova()
            └─► fq_flush_timeout()
```

### 不在本篇的内容

| 内容 | 去处 |
|------|------|
| API 签名 | `01-api.md` |
| 结构体字段 | `02-datastructures.md` |
| SVA / fault / IOVA cache | `04-extensions.md` |
| 设备树配置 | `06-devicetree.md` |

---

## Part A：时间线

### §1 IOMMU 驱动注册 — `iommu_device_register()`

**位置**：`drivers/iommu/iommu.c:208-240`

**调用者**：IOMMU 驱动 probe 函数（如 `arm_smmu_device_probe`）

**时间线**：

1. **参数校验**（`:213-218`）
   - 检查 `ops->owner` 是否有效（模块地址必须有 owner）
   - 强制全局单驱动限制（`iommu_buses[0]->iommu_ops` 只能有一个）

2. **填充 `iommu_device`**（`:220-222`）
   - `iommu->ops = ops`
   - `iommu->fwnode = dev_fwnode(hwdev)`（如果有父设备）

3. **加入全局链表**（`:224-227`）
   - `spin_lock(&iommu_device_lock)`
   - `list_add_tail(&iommu->list, &iommu_device_list)`
   - `spin_unlock(&iommu_device_lock)`

4. **遍历总线 probe 设备**（`:229-233`）
   - 对 `iommu_buses[]` 中每条总线（platform、PCI、AMBA 等）
   - 设置 `bus->iommu_ops = ops`
   - 调用 `bus_iommu_probe(bus)` → 遍历总线上所有设备，调用 `__iommu_probe_device()`

5. **错误回滚**（`:234-236`）
   - 如果任一总线 probe 失败，调用 `iommu_device_unregister()` 清理

**关键设计**：

- **全局单驱动**（`:220-222`）：当前只允许一个 IOMMU 驱动注册。这是临时限制，代码注释说"existing drivers would compete for at least the PCI or platform bus"
- **bus_iommu_probe 批量 probe**（`:229-233`）：注册时立即 probe 所有已枚举设备，后加入的设备由 bus notifier 处理

**返回值**：0 成功，`-EBUSY`（已有驱动），`-EINVAL`（owner 无效），或其他 probe 错误

---

### §2 设备 Probe — `__iommu_probe_device()`

**位置**：`drivers/iommu/iommu.c:283-343`

**调用者**：`iommu_device_register()`（批量 probe）、`iommu_bus_notifier()`（热插拔）

**时间线**：

1. **获取 ops**（`:285`）
   - `ops = dev->bus->iommu_ops`
   - 如果为 NULL，返回 `-ENODEV`（设备不在 IOMMU 管理的总线上）

2. **序列化 probe**（`:293-295`）
   - `lockdep_assert_held(&iommu_probe_device_lock)`
   - 避免 IOMMU 驱动注册与设备 probe 并发

3. **分配 `dev_iommu`**（`:296-298`）
   - `dev_iommu_get(dev)` → 分配 `dev->iommu`
   - 失败返回 `-ENOMEM`

4. **模块引用**（`:300-303`）
   - `try_module_get(ops->owner)` → 防止驱动卸载
   - 失败返回 `-EINVAL`

5. **调用驱动 probe**（`:305-309`）
   - `iommu_dev = ops->probe_device(dev)`
   - IOMMU 驱动初始化硬件、分配私有数据
   - 返回 `iommu_device` 指针或错误

6. **保存 iommu_dev**（`:311`）
   - `dev->iommu->iommu_dev = iommu_dev`

7. **获取或创建 group**（`:313-320`）
   - `iommu_group_get_for_dev(dev)` → 见 §3
   - 如果 group 刚创建且无 default_domain，加入 `group_list`（延迟分配）

8. **链接设备**（`:322`）
   - `iommu_device_link(iommu_dev, dev)` → sysfs 链接

9. **返回 0**

**错误路径**：

- `:325-327`：probe 失败 → `ops->release_device(dev)`
- `:329-331`：模块引用失败 → `module_put(ops->owner)`
- `:333-335`：dev_iommu 分配失败 → `dev_iommu_free(dev)`

**关键设计**：

- **延迟 default_domain 分配**（`:317-319`）：group 刚创建时不立即分配 domain，而是加入 `group_list`，由 `iommu_device_register()` 统一处理。避免每个设备单独分配

---

### §3 Group 获取/创建 — `iommu_group_get_for_dev()`

**位置**：`drivers/iommu/iommu.c:1637-1665`

**调用者**：`__iommu_probe_device()`

**时间线**：

1. **检查已有 group**（`:1643-1645`）
   - `iommu_group_get(dev)` → 如果设备已在 group，直接返回
   - 避免重复创建

2. **调用驱动 device_group**（`:1647-1652`）
   - `group = ops->device_group(dev)`
   - IOMMU 驱动根据硬件拓扑决定 group 划分
   - PCI 设备：`pci_device_group()`（`drivers/iommu/iommu.c:1501-1565`）
   - platform 设备：`generic_device_group()`（`:1491-1499`）→ 一设备一 group

3. **添加设备到 group**（`:1654-1656`）
   - `iommu_group_add_device(group, dev)` → 见 §3.1

4. **返回 group**

**错误路径**：

- `:1659-1662`：添加失败 → `iommu_group_put(group)` → 返回错误

#### §3.1 添加设备到 Group — `iommu_group_add_device()`

**位置**：`drivers/iommu/iommu.c:944-1023`

**时间线**：

1. **分配 `group_device`**（`:948-952`）
   - `kzalloc(sizeof(*device), GFP_KERNEL)`

2. **填充字段**（`:954-956`）
   - `device->dev = dev`
   - `device->name = kstrdup(dev_name(dev), GFP_KERNEL)`

3. **获取 reserved regions**（`:958-963`）
   - `ops->get_resv_regions(dev, &group->reserved_regions)`
   - 如果失败，回滚

4. **创建 direct mappings**（`:965-970`）
   - `iommu_create_device_direct_mappings(group, dev)`
   - 为 reserved regions 创建 1:1 映射

5. **加入链表**（`:972-975`）
   - `mutex_lock(&group->mutex)`
   - `list_add_tail(&device->list, &group->devices)`
   - `mutex_unlock(&group->mutex)`

6. **设置 `dev->iommu_group`**（`:977`）
   - `dev->iommu_group = group`

7. **创建 sysfs 链接**（`:979-985`）
   - `sysfs_create_link()` → `/sys/kernel/iommu_groups/<id>/devices/<dev>`

8. **返回 0**

---

### §4 Default Domain 分配 — `iommu_alloc_default_domain()`

**位置**：`drivers/iommu/iommu.c:1614-1625`

**调用者**：`iommu_probe_device()`

**时间线**：

1. **检查已有 domain**（`:1619-1621`）
   - `if (group->default_domain) return 0`
   - 避免重复分配

2. **确定域类型**（`:1623`）
   - `type = iommu_get_def_domain_type(dev) ? : iommu_def_domain_type`
   - 优先用设备特定类型（`def_domain_type` 回调），否则用全局默认

3. **分配 domain**（`:1625`）
   - `iommu_group_alloc_default_domain(dev->bus, group, type)` → 见 §4.1

#### §4.1 分配 Default Domain — `iommu_group_alloc_default_domain()`

**位置**：`drivers/iommu/iommu.c:1591-1612`

**时间线**：

1. **分配 domain**（`:1595-1598`）
   - `__iommu_domain_alloc(bus, type)` → 调用 `ops->domain_alloc(type)`

2. **初始化 DMA cookie**（`:1600-1608`）
   - 如果是 DMA 域（`IOMMU_DOMAIN_DMA` 或 `IOMMU_DOMAIN_DMA_FQ`）
   - `iommu_get_dma_cookie(domain)` → 分配 `iommu_dma_cookie`
   - `iommu_dma_init_domain(domain, dma_base, dma_limit, dev)` → 初始化 IOVA 分配器

3. **保存到 group**（`:1610`）
   - `group->default_domain = domain`

4. **返回 0**

**关键设计**：

- **DMA cookie 延迟初始化**：只有 DMA 域才分配 cookie，恒等域不需要
- **IOVA 分配器初始化**：`init_iova_domain()` + `iova_domain_init_rcaches()` → 见 `02-datastructures.md` §9

---

### §5 设备 Attach Domain — `iommu_attach_group()`

**位置**：`drivers/iommu/iommu.c:2119-2128`

**调用者**：设备驱动（如 VFIO）、`iommu_probe_device()`

**时间线**：

1. **加锁**（`:2123`）
   - `mutex_lock(&group->mutex)`

2. **调用内部函数**（`:2124`）
   - `__iommu_attach_group(domain, group)` → 见 §5.1

3. **解锁**（`:2125`）
   - `mutex_unlock(&group->mutex)`

4. **返回结果**

#### §5.1 内部 Attach — `__iommu_attach_group()`

**位置**：`drivers/iommu/iommu.c:1800-1850`（推断，未直接显示）

**时间线**：

1. **遍历 group 设备**（推断）
   - 对 group 中每个设备调用 `__iommu_attach_device(domain, dev)`

2. **调用驱动 attach**（推断）
   - `domain->ops->attach_dev(domain, dev)`
   - IOMMU 驱动配置硬件页表、流表项

3. **更新 group->domain**（推断）
   - `group->domain = domain`

#### §5.2 单设备 Attach — `__iommu_attach_device()`

**位置**：`drivers/iommu/iommu.c:1988-1998`

**时间线**：

1. **检查回调**（`:1992-1994`）
   - `if (domain->ops->attach_dev == NULL) return -ENODEV`

2. **调用驱动 attach**（`:1996`）
   - `ret = domain->ops->attach_dev(domain, dev)`

3. **tracepoint**（`:1997-1998`）
   - `if (!ret) trace_attach_device_to_domain(dev)`

4. **返回结果**

**关键设计**：

- **group 原子性**：attach 是对整个 group 操作，不是单个设备。group 内所有设备共享同一个 domain
- **锁保护**：`group->mutex` 保证 attach/detach 串行

---

### §6 DMA 映射 — `iommu_map()`

**位置**：`drivers/iommu/iommu.c:2352-2357`

**调用者**：设备驱动（通过 DMA-API）、IOMMU 用户

**时间线**：

1. **睡眠检查**（`:2355`）
   - `might_sleep()` → 确保不在原子上下文

2. **调用内部函数**（`:2356`）
   - `_iommu_map(domain, iova, paddr, size, prot, GFP_KERNEL)` → 见 §6.1

3. **返回结果**

#### §6.1 内部 Map — `_iommu_map()`

**位置**：`drivers/iommu/iommu.c:2330-2340`（推断）

**时间线**：

1. **调用核心 map**（推断）
   - `ret = __iommu_map(domain, iova, paddr, size, prot, gfp)` → 见 §6.2

2. **同步 TLB**（推断）
   - `if (ret == 0 && ops->iotlb_sync_map) ops->iotlb_sync_map(domain, iova, size)`

3. **返回结果**

#### §6.2 核心 Map — `__iommu_map()`

**位置**：`drivers/iommu/iommu.c:2280-2328`（推断）

**时间线**：

1. **对齐检查**（推断）
   - 检查 `iova`、`paddr`、`size` 是否按最小页对齐

2. **循环映射**（推断）
   - `while (size)` → 分片映射
   - `__iommu_map_pages(domain, iova, paddr, size, prot, gfp, &mapped)` → 见 §6.3
   - `iova += mapped; paddr += mapped; size -= mapped`

3. **错误回滚**（推断）
   - 如果失败，`iommu_unmap(domain, orig_iova, orig_size - size)`

4. **tracepoint**（推断）
   - `trace_map(orig_iova, orig_paddr, orig_size)`

#### §6.3 页映射 — `__iommu_map_pages()`

**位置**：`drivers/iommu/iommu.c:2250-2278`

**时间线**：

1. **计算页大小**（`:2258`）
   - `pgsize = iommu_pgsize(domain, iova, paddr, size, &count)`
   - 选择最大对齐的页大小（4K/2M/1G）

2. **调用驱动 map**（`:2263-2270`）
   - 如果 `ops->map_pages` 存在 → `ops->map_pages(domain, iova, paddr, pgsize, count, prot, gfp, mapped)`
   - 否则 → `ops->map(domain, iova, paddr, pgsize, prot, gfp)` → `*mapped = pgsize`

3. **返回结果**

**关键设计**：

- **批量映射**：`map_pages` 一次映射多个同大小页，减少回调次数
- **页大小选择**：`iommu_pgsize()` 根据 `pgsize_bitmap`、对齐、剩余大小选择最优页

---

### §7 DMA 解映射 + Flush Queue — `iommu_unmap()`

**位置**：`drivers/iommu/iommu.c:2434-2440`

**调用者**：设备驱动（通过 DMA-API）

**时间线**：

1. **调用内部函数**（`:2438`）
   - `__iommu_unmap(domain, iova, size, iotlb_gather)` → 见 §7.1

2. **返回解映射大小**

#### §7.1 内部 Unmap — `__iommu_unmap()`

**位置**：`drivers/iommu/iommu.c:2400-2432`（推断）

**时间线**：

1. **循环解映射**（推断）
   - `while (size)` → 分片解映射
   - `__iommu_unmap_pages(domain, iova, size, iotlb_gather)` → 见 §7.2
   - `iova += unmapped; size -= unmapped`

2. **同步 TLB**（推断）
   - `iommu_iotlb_sync(domain, iotlb_gather)` → 批量刷新

3. **返回解映射大小**

#### §7.2 页解映射 — `__iommu_unmap_pages()`

**位置**：`drivers/iommu/iommu.c:2360-2370`

**时间线**：

1. **计算页大小**（`:2365`）
   - `pgsize = iommu_pgsize(domain, iova, iova, size, &count)`

2. **调用驱动 unmap**（`:2366-2369`）
   - `ops->unmap_pages ? ops->unmap_pages(domain, iova, pgsize, count, iotlb_gather) : ops->unmap(domain, iova, pgsize, iotlb_gather)`

3. **返回解映射大小**

#### §7.3 Flush Queue 机制

**位置**：`drivers/iommu/dma-iommu.c:157-280`

**时间线**：

1. **unmap 时排队**（推断）
   - `iommu_unmap_fast()` → 不立即释放物理页
   - `queue_iova(cookie, iova_pfn, pages, freelist)` → 加入 per-CPU flush queue

2. **队列满或定时器触发**（`:157-165`）
   - `fq_flush_timeout()` → 定时器回调
   - `fq_ring_free()` → 遍历队列，刷新 TLB，释放物理页

3. **定时器启动**（`:216-220`）
   - `if (!atomic_read(&cookie->fq_timer_on) && !atomic_xchg(&cookie->fq_timer_on, 1))`
   - `mod_timer(&cookie->fq_timer, jiffies + msecs_to_jiffies(10))`
   - 10ms 定时器

**关键设计**：

- **延迟释放**：避免频繁 TLB 失效，批量处理
- **per-CPU 队列**：减少锁竞争
- **定时器兜底**：即使队列不满，10ms 后也会刷新

---

## Part B：横切面

### §8 锁层次

**详见**：`02-datastructures.md` §8.2

**关键锁**：

| 锁 | 保护对象 | 持有时间 |
|----|---------|---------|
| `iommu_probe_device_lock` | 序列化 probe | 整个 probe 流程 |
| `iommu_device_lock` | `iommu_device_list` | 链表操作 |
| `group->mutex` | `group->devices` | attach/detach |
| `iova_rbtree_lock` | IOVA 红黑树 | 分配/释放 |
| `iova_fq.lock` | per-CPU flush queue | 排队/刷新 |

**锁顺序**（从外到内）：

1. `iommu_probe_device_lock`
2. `group->mutex`
3. `dev_iommu.lock`
4. `iova_rbtree_lock`
5. `iova_fq.lock`

**死锁避免**：不在持有内层锁时申请外层锁。

---

### §9 错误处理

**常见错误码**：

| 错误码 | 含义 | 触发场景 |
|--------|------|---------|
| `-ENODEV` | 设备无 IOMMU | `ops == NULL`、`iommu_dev == NULL` |
| `-EBUSY` | 已有驱动 | `iommu_device_register()` 重复注册 |
| `-EINVAL` | 参数无效 | owner 无效、group 设备数 ≠ 1 |
| `-ENOMEM` | 内存不足 | `kzalloc` 失败 |
| `-EFAULT` | 地址无效 | IOVA/PA 未对齐 |

**错误回滚**：

- **probe 失败**：`ops->release_device()` → `module_put()` → `dev_iommu_free()`
- **map 失败**：`iommu_unmap()` 已映射部分
- **attach 失败**：不更新 `group->domain`

---

### §10 性能优化

#### §10.1 批量映射

- `map_pages` / `unmap_pages`：一次操作多个同大小页
- 减少回调次数，提升吞吐

#### §10.2 Flush Queue

- 延迟 TLB 失效，批量处理
- per-CPU 队列，减少锁竞争
- 10ms 定时器兜底

#### §10.3 IOVA 缓存

- `iova_rcache`：缓存小 IOVA 分配
- 避免频繁操作红黑树
- per-CPU magazine，无锁快速路径

#### §10.4 快速路径

- `iommu_unmap_fast()`：不立即同步 TLB
- 由调用者决定何时 `iommu_iotlb_sync()`
- 适合批量解映射场景

---

## Part C：评价

### §11 设计债与限制

#### §11.1 全局单驱动限制

**位置**：`drivers/iommu/iommu.c:220-222`

**问题**：当前只允许一个 IOMMU 驱动注册

**代码注释**：
> "Temporarily enforce global restriction to a single driver. This was already the de-facto behaviour, since any possible combination of existing drivers would compete for at least the PCI or platform bus."

**影响**：无法同时使用多个 IOMMU 驱动（如 SMMUv3 + Apple DART）

**状态**：临时限制，未来可能解除

#### §11.2 Default Domain 分配时机

**位置**：`drivers/iommu/iommu.c:1614-1625`

**问题**：default_domain 在 probe 时分配，但可能不被使用

**影响**：VFIO 场景会切换到 blocking_domain，default_domain 浪费

**状态**：设计权衡，简化常见路径

#### §11.3 Flush Queue 定时器精度

**位置**：`drivers/iommu/dma-iommu.c:218`

**问题**：10ms 定时器是硬编码

**影响**：高吞吐场景可能延迟释放，低吞吐场景可能过于频繁

**状态**：可调参数，但未暴露为配置

#### §11.4 Group 划分规则

**位置**：`drivers/iommu/iommu.c:1379-1565`

**问题**：PCI group 划分依赖 ACS 能力，硬件实现不一致

**影响**：某些平台 group 过大，隔离粒度粗

**状态**：硬件限制，软件无法解决

---

## §12 取证摘要

### §12.1 函数位置

所有行号经 `grep -nE '^(int|void|static int|static void) (iommu_|__iommu_)' drivers/iommu/iommu.c` 验证。

### §12.2 流程顺序

基于代码阅读和调用图。部分内部函数（如 `__iommu_attach_group`）未直接显示，基于上下文推断。

### §12.3 设计债

基于代码注释和 REVISIT/FIXME 标记。未来版本可能已修复。

---

## §13 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| SVA / fault / IOVA cache 的详细机制 | `04-extensions.md` |
| 设备树怎么配置 IOMMU | `06-devicetree.md` |
| 怎么调试 IOMMU 问题 | `07-debug.md` |
| 用大白话理解这些流程 | `08-feynman.md` |
