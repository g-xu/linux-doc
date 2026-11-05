---
title: 总览
---

# I/O 与设备模型 — 总结与对比

---

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **device model** | 所有设备的统一骨架——bus/device/driver/class 五大对象构成注册、匹配、probe/remove 的完整生命周期框架 | 唯一将 bus、device、driver、class 四类对象统一到一个框架中的机制；`release` 回调保证引用计数归零时安全释放；`probe` 返回 `-EPROBE_DEFER` 自动触发延迟重试 | [device-model.md](device-model.md) |
| **of/device tree** | arm64 上硬件描述的唯一来源——bootloader 传入 DTB，内核解包为 device_node 树，据此创建 platform_device | 唯一从静态硬件描述（DTB 二进制）动态生成内核设备对象的模块；`compatible` 属性是驱动匹配核心；`#address-cells`/`#size-cells` 决定子节点 `reg` 解释方式 | [of-device-tree.md](of-device-tree.md) |
| **kobject** | 设备模型的 sysfs 目录与引用计数基石——每个 device/class/bus 背后都有 kobject | 唯一同时提供 sysfs 目录创建和通用引用计数（kref）的基础设施；kset 扩展为同类 kobject 的容器并支持 uevent 过滤 | [misc-kobject.md](misc-kobject.md) |
| **platform_device** | 最重要的 bus_type——承载几乎所有非自动枚举设备（DT 节点、ACPI、MFD 子设备） | 唯一同时支持 DT compatible 匹配、ACPI 匹配、ID 表匹配和名字匹配的 bus_type；5 级匹配优先级覆盖所有匹配场景 | [misc-platform.md](misc-platform.md) |
| **regmap** | 寄存器访问跨总线统一抽象——屏蔽 I2C/SPI/MMIO 差异，提供缓存和位域操作 | 唯一在单一 API 层同时支持多总线后端（I2C/SPI/MMIO/MDIO）、可选寄存器缓存（4 种类型）和原子位域操作（`regmap_update_bits`）的模块 | [regmap.md](regmap.md) |
| **dma-mapping** | CPU 地址与设备总线地址的翻译层——解决 DMA 映射和 cache 同步 | 唯一处理 CPU 虚拟地址 ↔ 设备 DMA 总线地址翻译的模块；统一封装一致性映射、流式映射、IOMMU 路径和 swiotlb 弹跳 | [dma-mapping.md](dma-mapping.md) |
| **devres** | 设备资源自动管理——probe 阶段申请的资源挂到设备链表，detach 时 LIFO 自动释放 | 唯一将资源生命周期绑定到设备（而非调用者）的机制；消除手动 error path 和 remove 路径的泄漏风险；`devm_add_action_or_reset` 可把任意清理函数纳入管理 | [misc-devres.md](misc-devres.md) |
| **iomap** | 物理寄存器地址到内核虚拟地址的映射——通过指针读写硬件寄存器 | 唯一提供物理地址 → 虚拟地址映射（ioremap）和带内存屏障的寄存器读写原语（readl/writel）的模块；arm64 上 `readl`/`writel` 自带内存屏障 | [misc-io.md](misc-io.md) |
| **firmware loader** | 统一固件文件加载接口——从文件系统或内建表加载二进制固件供硬件使用 | 唯一提供标准化固件搜索路径（内建表 → `/lib/firmware/`）和同步/异步两种加载模式的模块 | [misc-firmware.md](misc-firmware.md) |

---

## §2 选型决策

```
我在写一个外设驱动，需要用到哪些模块？

1. 设备从哪里来？
   ├── arm64 设备树节点 → of/device tree 解析 DT → 自动创建 platform_device
   ├── 代码中手动创建 → platform_device_alloc + platform_device_add
   └── 总线自动枚举（SPI/USB/PCI）→ 总线核心自动创建 device

2. 驱动如何匹配和探测？
   ├── 定义 of_match_table（compatible 列表）→ device model 自动匹配
   ├── probe 中获取资源：
   │   ├── MMIO 寄存器 → devm_platform_ioremap_resource（iomap）
   │   ├── IRQ → platform_get_irq
   │   ├── 时钟/复位/GPIO → 各自子系统 API
   │   └── 私有数据 → devm_kzalloc（devres 自动管理）
   └── probe 返回 0 成功 / -EPROBE_DEFER 延迟 / 负值失败

3. 如何访问硬件寄存器？
   ├── 简单 MMIO（直接读写） → ioremap + readl/writel
   ├── 寄存器通过 I2C/SPI 间接访问 → regmap（统一抽象）
   └── 需要寄存器缓存/位域操作 → regmap + cache_type

4. 如何做 DMA？
   ├── 设备需要持续共享内存 → dma_alloc_coherent（一致性映射）
   ├── 设备需要临时传输 → dma_map_single/unmap_single（流式映射）
   │   └── 传输前后需 dma_sync_single_for_cpu/for_device
   ├── 大量 scatterlist → dma_map_sg/unmap_map_sg
   └── 设置 DMA 掩码 → dma_set_mask_and_coherent（probe 早期调用）

5. 如何加载固件？
   ├── probe 中必须等到固件 → request_firmware（同步）
   ├── 固件可选或可延迟 → request_firmware_nowait（异步+回调）
   └── 需要加载到指定缓冲区 → request_firmware_into_buf

6. 如何向用户态暴露信息？
   ├── 只读状态 → sysfs 属性（device_create_file 或 attribute_group）
   ├── 大量调试输出 → debugfs + seq_file
   └── 设备状态变化通知 → kobject_uevent（自动发，通常不需要手动调）
```

---

## §3 核心对比表

### 3.1 按职责分组

| 分组 | 模块 | 核心职责 | 输入 | 输出 |
|------|------|---------|------|------|
| **设备骨架** | device model | 统一注册/匹配/生命周期 | bus_type + device + driver | probe 调用、sysfs 目录、uevent |
| | kobject | sysfs 目录 + 引用计数 | kobject_init + kobject_add | /sys 下目录项、kref 管理 |
| **硬件描述** | of/device tree | DTB → device_node → platform_device | DTB 二进制 | device_node 树、platform_device |
| **总线实例** | platform_device | 最重要的 bus_type，承载非自动枚举设备 | DT 节点 / 手动创建 | resource[]、dev.of_node |
| **寄存器访问** | iomap | 物理地址 → 虚拟地址映射 | 物理地址（resource） | 可读写指针（void __iomem *） |
| | regmap | 跨总线寄存器抽象 | I2C/SPI/MMIO 后端 | 统一 read/write/update_bits API |
| **DMA** | dma-mapping | CPU 地址 ↔ 设备 DMA 地址翻译 | CPU 虚拟/物理地址 | DMA 总线地址 + cache 同步 |
| **资源管理** | devres | 自动释放 probe 阶段资源 | devm_* 分配调用 | detach 时 LIFO 释放 |
| **固件** | firmware loader | 标准化固件加载 | 固件文件名 | struct firmware（数据+大小） |

### 3.2 regmap vs iomap 详细对比

| 维度 | iomap | regmap |
|------|-------|--------|
| 访问方式 | 指针读写（`readl`/`writel`） | 抽象 API（`regmap_read`/`regmap_write`） |
| 适用总线 | MMIO（物理地址直接映射） | I2C、SPI、MMIO、MDIO 等 |
| 缓存 | 无 | 可选（NONE/RBTREE/FLAT/COMPRESSED） |
| 位域操作 | 手动读-改-写 | `regmap_update_bits` 原子操作 |
| 锁 | 调用者负责 | regmap 内部 mutex/spinlock |
| 初始化开销 | ioremap 一次 | 需填 regmap_config（reg_bits/val_bits 等） |
| 典型场景 | 简单 SoC 外设、DMA 描述符 | codec、PMIC、sensor 等寄存器密集型设备 |

### 3.3 DMA 映射方式对比

| 映射方式 | API | 一致性 | 适用场景 | 需手动 sync |
|---------|-----|--------|---------|------------|
| 一致性映射 | `dma_alloc_coherent` | CPU 和设备始终一致 | 长期共享缓冲区（描述符环） | 否 |
| 流式映射（单页） | `dma_map_single` | 方向性一致 | 临时传输（单次 I/O） | 是 |
| 流式映射（scatterlist） | `dma_map_sg` | 方向性一致 | 大块非连续内存 | 是 |

### 3.4 内嵌关系链

```
struct platform_device
  └── struct device              ← 通用设备
        ├── struct kobject       ← sysfs 目录 + 引用计数
        │     └── struct kset *  ← 所属集合（devices_kset）
        ├── struct device *parent
        ├── struct bus_type *bus ← 指向 platform_bus_type
        ├── struct device_driver *driver  ← probe 后设置
        ├── struct device_node *of_node   ← DT 节点
        └── struct devres_head   ← devres 资源链表
```

**一句话**：`platform_device` 内嵌 `device`，`device` 内嵌 `kobject`。释放时引用计数归零触发 `device_release` → `devres_release_all` → `kobject_cleanup`。

---

## §4 使用规模

以下数字均为对内核源码树实时 grep 的精确结果。

### 4.1 设备模型与平台设备

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `module_platform_driver` | 2,670 | `grep -r "module_platform_driver" --include="*.c" drivers/` |
| `device_register` | 2,328 | `grep -r "device_register" --include="*.c" drivers/` |
| `platform_driver_register` | 973 | `grep -r "platform_driver_register" --include="*.c" drivers/` |
| `platform_device_alloc` | 254 | `grep -r "platform_device_alloc" --include="*.c"` |
| `bus_register` | 358 | `grep -r "bus_register\b" --include="*.c"` |
| `class_register` | 133 | `grep -r "class_register\b" --include="*.c"` |
| `device_create` | 244 | `grep -r "device_create\b" --include="*.c" drivers/` |

### 4.2 设备树 / OF

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `of_property_read_u32` | 3,013 | `grep -r "of_property_read_u32" --include="*.c"` |
| `of_device_is_compatible` | 886 | `grep -r "of_device_is_compatible" --include="*.c"` |
| `of_parse_phandle` | 695 | `grep -r "of_parse_phandle" --include="*.c"` |
| `of_property_read_string` | 513 | `grep -r "of_property_read_string" --include="*.c"` |
| `of_match_node` | 264 | `grep -r "of_match_node" --include="*.c"` |

### 4.3 寄存器访问

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `regmap_read`/`regmap_write` | 9,132 | `grep -rE "regmap_(read\|write)" --include="*.c" drivers/` |
| `regmap_update_bits` | 4,206 | `grep -r "regmap_update_bits" --include="*.c" drivers/` |
| `ioremap` | 4,882 | `grep -r "ioremap" --include="*.c" drivers/` |
| `devm_platform_ioremap_resource` | 1,455 | `grep -r "devm_platform_ioremap_resource" --include="*.c"` |
| `devm_ioremap_resource` | 762 | `grep -r "devm_ioremap_resource" --include="*.c" drivers/` |

### 4.4 DMA 映射

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `dma_alloc_coherent` | 1,799 | `grep -r "dma_alloc_coherent" --include="*.c" drivers/` |
| `dma_map_single` | 1,240 | `grep -r "dma_map_single" --include="*.c" drivers/` |
| `dma_set_mask` | 659 | `grep -r "dma_set_mask" --include="*.c" drivers/` |
| `dma_map_sg` | 470 | `grep -r "dma_map_sg" --include="*.c" drivers/` |

### 4.5 devres 资源管理

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `devm_kzalloc` | 5,353 | `grep -r "devm_kzalloc" --include="*.c" drivers/` |
| `devm_add_action_or_reset` | 636 | `grep -r "devm_add_action_or_reset" --include="*.c"` |
| `devm_request_irq` | 991 | `grep -r "devm_request_irq" --include="*.c" drivers/` |
| `devm_regmap_init` | 757 | `grep -r "devm_regmap_init" --include="*.c" drivers/` |
| `devm_kmalloc` | 259 | `grep -r "devm_kmalloc" --include="*.c"` |

### 4.6 固件加载

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `request_firmware` | 800 | `grep -r "request_firmware" --include="*.c" drivers/` |
| `request_firmware_nowait` | 64 | `grep -r "request_firmware_nowait" --include="*.c" drivers/` |

### 4.7 kobject / sysfs

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `kobject_init_and_add` | 156 | `grep -r "kobject_init_and_add" --include="*.c"` |
| `platform_get_irq` | 1,685 | `grep -r "platform_get_irq" --include="*.c" drivers/` |

---

## §5 设计模式速览

### 5.1 DT → platform_device → driver 数据流

```
DTB (bootloader 传入)
  │
  │  of_fdt_unflatten_tree()
  ▼
device_node 树 (of/device tree 模块)
  │
  │  of_platform_default_populate()
  │  对每个含 compatible 的节点：
  │    platform_device_alloc()
  │    platform_device_add()
  ▼
platform_device (platform_device 模块)
  │  dev.of_node = 对应 device_node
  │  resource[] = 从 reg/interrupts 属性解析
  │
  │  bus_probe_device()
  │  → platform_match() 遍历 driver
  │  → of_driver_match_device() 比较 compatible
  ▼
driver probe (device model 模块)
  │  platform_get_resource()  取 MMIO/IRQ
  │  devm_ioremap_resource()  映射寄存器
  │  devm_kzalloc()           分配私有数据
  │  regmap_init()            初始化寄存器访问
  │  dma_set_mask()           配置 DMA
  │  request_firmware()       加载固件
  ▼
设备就绪
```

### 5.2 典型 probe 函数模板

几乎所有 platform driver 的 probe 函数遵循同一模式：

```c
static int my_driver_probe(struct platform_device *pdev)
{
    struct my_priv *priv;
    void __iomem *base;
    int irq, ret;

    /* 1. devres 分配私有数据 */
    priv = devm_kzalloc(&pdev->dev, sizeof(*priv), GFP_KERNEL);

    /* 2. iomap 映射寄存器 */
    base = devm_platform_ioremap_resource(pdev, 0);

    /* 3. 获取 IRQ */
    irq = platform_get_irq(pdev, 0);

    /* 4. 初始化 regmap（如需要） */
    priv->regmap = devm_regmap_init_mmio(&pdev->dev, base, &config);

    /* 5. 设置 DMA 掩码 */
    dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));

    /* 6. 请求 IRQ */
    devm_request_irq(&pdev->dev, irq, my_handler, 0, "my_dev", priv);

    /* 7. 加载固件（如需要） */
    request_firmware(&fw, "my_fw.bin", &pdev->dev);

    return 0;
}
```

### 5.3 引用计数生命周期

kobject 的引用计数（`kref`）是设备生命周期的核心：

- `device_register()` → `kobject_add()` → kref = 1
- `get_device(dev)` → `kobject_get()` → kref++
- `put_device(dev)` → `kobject_put()` → kref--
- kref == 0 → `device_release()` → `dev->release()` 回调释放外层结构

**关键规则**：`device_initialize()` 之后，无论 `device_add()` 是否成功，都必须用 `put_device()` 释放（不能直接 kfree）。

### 5.4 devres 贯穿所有资源申请

几乎所有 probe 阶段的资源申请都有 devres 版本，确保 detach 时自动释放：

| 普通 API | devres 版本 | 调用次数 | 说明 |
|----------|------------|---------|------|
| `kmalloc` | `devm_kzalloc` | 5,353 | 最常用 |
| `ioremap` | `devm_ioremap_resource` | 762 | — |
| `platform_get_resource` + `ioremap` | `devm_platform_ioremap_resource` | 1,455 | probe 标配 |
| `request_irq` | `devm_request_irq` | 991 | 自动 free_irq |
| `regmap_init` | `devm_regmap_init` | 757 | 自动 regmap_exit |
| `dma_alloc_coherent` | `dma_alloc_coherent`（自带 devres） | 1,799 | 自动 free |
| 任意清理函数 | `devm_add_action_or_reset` | 636 | 万能胶水 |

**原则**：优先使用 `devm_` 版本，减少手动 error path 和 remove 路径代码。

---

## §6 锁模型对比

本类别模块的并发保护策略差异显著：

| 模块 | 主要锁机制 | 保护对象 | 调用上下文约束 |
|------|-----------|---------|--------------|
| **device model** | `device_lock(dev)` → mutex | device 结构体字段、probe/remove 序列化 | probe/remove 在进程上下文，可睡眠 |
| **kobject** | `kobj->kref` → atomic_t | 引用计数 | `kobject_get`/`kobject_put` 可在任意上下文调用 |
| **platform_device** | 继承 device model 的 mutex + bus 层 `subsys_lock`（rw_semaphore） | 设备注册/匹配/总线遍历 | bus 操作在进程上下文 |
| **regmap** | 内部 `mutex`（默认）或 `spinlock`（`fast_io` 模式） | 寄存器读写序列化、缓存操作 | `fast_io` 模式允许原子上下文调用 |
| **dma-mapping** | `dma_iommu_lock`（mutex 或 spinlock，取决于上下文） | IOMMU IOVA 分配、页表操作 | `GFP_ATOMIC` 分配可在原子上下文 |
| **iomap** | 无内部锁——调用者负责 | — | `ioremap` 在进程上下文；`readl`/`writel` 可在任意上下文 |
| **devres** | `dev->devres_lock`（spinlock） | devres 链表操作 | `devm_kzalloc` 可在任意上下文；`devm_add_action` 需在设备生命周期内 |
| **firmware loader** | `fw_lock`（mutex）+ `fw_st` 的 completion | 固件加载状态序列化 | `request_firmware` 在进程上下文（可睡眠） |
| **of/device tree** | device_node 树只读（boot 后不修改） | — | `of_property_read_*` 可在任意上下文调用 |

**关键差异**：
- **regmap** 是唯一提供内部锁的寄存器访问模块——`fast_io=true` 时使用 spinlock（允许中断上下文），否则使用 mutex（仅进程上下文）
- **devres** 的 `devres_lock` 是 spinlock，因此 devres 链表操作本身是原子的，但 `devm_kzalloc` 的 `kmalloc` 部分可能需要睡眠
- **device model** 的 `device_lock` 是 mutex，因此 `probe`/`remove` 被序列化为进程上下文操作

---

## §7 组合使用模式

### 7.1 典型 platform driver 全栈组合

最常见的组合：device model + of/device tree + platform_device + devres + iomap

```
DT 节点 → of/device tree 创建 platform_device
         → device model 匹配 driver
         → probe 中：
            devm_kzalloc（devres）分配私有数据
            devm_platform_ioremap_resource（iomap + devres）映射寄存器
            devm_request_irq（devres）注册中断
```

全树 grep 验证：`devm_platform_ioremap_resource` 在 1,455 个文件中使用，几乎每个都同时使用 `devm_kzalloc`。

### 7.2 regmap + devres 组合

SoC 外设驱动（codec、PMIC、sensor）的标准模式：

```c
priv->regmap = devm_regmap_init_i2c(client, &regmap_config);
/* 或 */
priv->regmap = devm_regmap_init_mmio(&pdev->dev, base, &regmap_config);
```

`devm_regmap_init` 在 757 个文件中使用，自动在 detach 时调用 `regmap_exit`。

### 7.3 dma-mapping + devres 组合

`dma_alloc_coherent` 自带 devres 支持——传入 `GFP_KERNEL` 时自动挂到设备 devres 链表：

```c
buf = dma_alloc_coherent(dev, size, &dma_handle, GFP_KERNEL);
/* detach 时自动 dma_free_coherent */
```

### 7.4 firmware loader + workqueue 异步组合

驱动在 probe 中异步加载固件，完成后回调中初始化设备：

```c
request_firmware_nowait(THIS_MODULE, FW_ACTION_UEVENT,
                        "fw.bin", dev, GFP_KERNEL, priv,
                        my_fw_loaded);
/* my_fw_loaded 回调在 workqueue 进程上下文中执行 */
```

实例：`drivers/bluetooth/btusb.c` 同时使用 `request_firmware` + `schedule_work`。

### 7.5 device model + kobject uevent 组合

设备注册时自动发送 uevent 通知用户态：

```
device_register() → kobject_add() → kobject_uevent(KOBJ_ADD)
                   → netlink 广播 → udev 处理
```

`kobject_uevent` 全树 288 处调用，大部分由 device model 自动触发。

### 7.6 最容易被类比错的关系

| 容易混淆的对 | 本质区别 |
|-------------|---------|
| kobject vs device | kobject 是 sysfs 目录+引用计数的通用机制；device 是设备模型的具体对象，内嵌 kobject 并扩展了 bus/driver/PM/DMA 等字段 |
| kobject vs kset | kobject 表示单个 sysfs 目录；kset 是一组 kobject 的容器，内嵌 kobject 并添加 uevent 过滤 |
| platform_device vs device | platform_device 是 device 的"子类"——内嵌 device，增加了 resource 数组和 DT 匹配支持 |
| iomap vs regmap | iomap 是底层物理地址映射（指针读写）；regmap 是高层寄存器抽象（跨总线、带缓存、带位域操作） |
| dma_alloc_coherent vs dma_map_single | coherent 是长期映射（CPU 和设备始终一致）；streaming/map_single 是临时映射（需手动 sync） |
| of_device_tree vs platform_device | DT 是硬件描述（只读数据）；platform_device 是内核运行时对象（由 DT 节点创建，可动态添加/删除） |

---

## §8 阅读顺序建议

### 路径 A：写一个新的 platform driver

1. [of-device-tree.md](of-device-tree.md) — 理解 DT 如何描述硬件
2. [misc-platform.md](misc-platform.md) — 理解 platform_device 和匹配机制
3. [device-model.md](device-model.md) — 理解 probe/remove 生命周期
4. [misc-devres.md](misc-devres.md) — 学习资源自动管理
5. [misc-io.md](misc-io.md) — 学习 ioremap + readl/writel
6. [regmap.md](regmap.md) — 如果设备通过 I2C/SPI 访问寄存器

### 路径 B：理解设备模型的内部机制

1. [misc-kobject.md](misc-kobject.md) — 从最底层的 sysfs + 引用计数开始
2. [device-model.md](device-model.md) — 理解 bus/device/driver/class 五大对象
3. [misc-platform.md](misc-platform.md) — 看 platform_bus_type 如何实现 bus_type
4. [misc-devres.md](misc-devres.md) — 理解资源管理如何嵌入设备生命周期

### 路径 C：理解 DMA 子系统

1. [dma-mapping.md](dma-mapping.md) — 一致性/流式映射、IOMMU 路径、swiotlb
2. [misc-io.md](misc-io.md) — 理解物理地址到虚拟地址的映射
3. [device-model.md](device-model.md) — 理解 `dev->dma_mask` 和 `dev->dma_ops`

### 路径 D：理解固件加载

1. [misc-firmware.md](misc-firmware.md) — 同步/异步加载 API、搜索顺序
2. [device-model.md](device-model.md) — 理解 `dev` 参数如何影响搜索路径

---

## §9 文档索引

| 文档 | 覆盖模块 |
|------|---------|
| [device-model.md](device-model.md) | 设备模型框架：五大结构体关系、注册与匹配流程（device_register → bus_probe_device → really_probe）、uevent、sysfs、PM、设备链接 |
| [of-device-tree.md](of-device-tree.md) | OF/Device Tree：device_node/property 数据结构、DT 解析流程、compatible 匹配、reg 属性解析、phandle 引用、of_api 全集 |
| [misc-kobject.md](misc-kobject.md) | kobject 与 kset：核心结构、生命周期（init→add→put）、sysfs 属性、kset uevent 机制、全局预定义 kobject |
| [misc-platform.md](misc-platform.md) | platform_device：结构体、匹配机制（5 级优先级）、注册 API、资源获取、辅助宏 |
| [regmap.md](regmap.md) | regmap：核心架构、regmap_config、总线后端（I2C/SPI/MMIO）、寄存器缓存（4 种类型）、位域操作、IRQ 域桥接、批量操作 |
| [dma-mapping.md](dma-mapping.md) | DMA 映射：一致性/流式映射、dma_map_ops vtable、IOMMU 路径、swiotlb 弹跳、scatterlist 映射、cache 同步 |
| [misc-devres.md](misc-devres.md) | devres：内部数据结构、LIFO 释放机制、devm_kzalloc 系列、devm_add_action 万能胶水、资源组 |
| [misc-io.md](misc-io.md) | iomap：ioremap/iounmap、devm 版本、platform_device 专用 API、readl/writel 及 arm64 内存屏障 |
| [misc-firmware.md](misc-firmware.md) | firmware loader：struct firmware、同步/异步加载 API、搜索顺序、固件上传（firmware_upload） |
