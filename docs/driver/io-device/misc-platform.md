# platform_device（平台设备）

### 定位

**功能**：Linux 设备模型中最重要的 `bus_type`——几乎所有不能由总线自动枚举的设备（DT 节点、ACPI 设备、MFD 子设备）都注册为 platform_device，由 platform_driver 通过 `probe` 驱动。它是 SoC 片上外设与内核驱动框架之间的桥梁。

**实现方式**：`struct platform_device`（`include/linux/platform_device.h:23-45`）内嵌 `struct device`，通过 `dev.of_node` 指向 DT 节点（arm64 主路径），携带 `resource[]` 数组描述 MMIO/IRQ/DMA 资源。匹配机制 `platform_match()` 按优先级尝试：`driver_override` → DT `of_match_table` 匹配 → ACPI 匹配 → `id_table` 名字匹配 → `driver.name` 匹配。资源获取通过 `platform_get_resource`/`platform_get_irq`，映射通过 `devm_platform_ioremap_resource`（一步完成资源取址 + request_mem_region + ioremap）。

**应用场景**：`platform_driver_register` 全树 1,070 处调用，`devm_platform_ioremap_resource` 1,231 处——绝大多数 SoC 外设驱动（UART、SPI、I2C、GPIO、PWM、ADC、DMA 控制器等）都基于 platform_device。arm64 上 platform_device 主要由 DT 节点创建（`drivers/of/platform.c` 的 `of_platform_device_create_pdata`），MFD 子设备也注册为 platform_device。

---

## 1. 核心结构

头文件：`include/linux/platform_device.h`；实现：`drivers/base/platform.c`

### 1.1 `struct platform_device`

```c
struct platform_device {                  // platform_device.h:23-45
    const char        *name;              // 设备名（用于匹配）
    int               id;                 // 实例 ID（-1 = 无 ID）
    bool              id_auto;            // 自动分配 ID
    struct device     dev;                // **内嵌**通用 device（核心！）
    u64               platform_dma_mask;  // 默认 DMA 掩码
    struct device_dma_parameters dma_parms;
    u32               num_resources;      // 资源数量
    struct resource   *resource;          // 资源数组（MMIO / IRQ / DMA）
    const struct platform_device_id *id_entry;  // 匹配到的 ID 表项
    const char        *driver_override;   // 强制绑定驱动名
    struct mfd_cell   *mfd_cell;         // MFD 子设备指针
    struct pdev_archdata archdata;        // 架构相关（arm64 为空）
};
```

**关键**：`platform_device` 通过内嵌 `struct device` 接入设备模型。`dev.of_node` 指向对应的 DT 节点（arm64 上这是主要设备发现路径）。

### 1.2 `struct platform_driver`

```c
struct platform_driver {                  // platform_device.h:208-236
    int (*probe)(struct platform_device *);
    int (*remove)(struct platform_device *);     // 传统返回 int（实际被忽略）
    void (*remove_new)(struct platform_device *); // 新版返回 void
    void (*shutdown)(struct platform_device *);
    int (*suspend)(struct platform_device *, pm_message_t state);
    int (*resume)(struct platform_device *);
    struct device_driver driver;                  // 内嵌通用 driver
    const struct platform_device_id *id_table;    // 名字匹配表
    bool prevent_deferred_probe;
    bool driver_managed_dma;
};
```

### 1.3 匹配机制

`platform_match()`（`drivers/base/platform.c`）按优先级依次尝试：

1. **`driver_override`**：如果设置了，直接比较名字
2. **DT 匹配**：`of_driver_match_device()` — 比较 `drv->driver.of_match_table` 与 `dev->of_node->compatible`（**arm64 主路径**）
3. **ACPI 匹配**：`acpi_driver_match_device()`
4. **ID 表匹配**：比较 `drv->id_table` 与 `pdev->name`
5. **名字匹配**：直接比较 `drv->driver.name` 与 `pdev->name`

---

## 2. 注册与生命周期

### 2.1 设备注册

| API | 说明 | 备注 |
|-----|------|------|
| `platform_device_register` | 注册完整 platform_device | init + add |
| `platform_device_alloc` | 分配 + 初始化 | 需后续 `platform_device_add` |
| `platform_device_add` | 添加到设备模型 | 设置 parent、资源、触发 probe |
| `platform_device_register_full` | 从 `platform_device_info` 一步创建 | 最完整的接口 |
| `platform_device_register_simple` | 简化版：名字 + ID + 资源 | 遗留驱动用 |
| `platform_device_register_data` | 简化版：名字 + 数据 | |
| `platform_device_unregister` | 注销 | del + put |
| `platform_device_put` | 释放引用 | |

### 2.2 驱动注册

```c
// 标准宏（推荐）
module_platform_driver(my_driver);
// 展开为 module_init + module_exit

// 或手动：
platform_driver_register(&my_driver);  // 宏，自动传 THIS_MODULE
platform_driver_unregister(&my_driver);
```

| API | 说明 | 备注 |
|-----|------|------|
| `platform_driver_register` | 注册驱动 | 宏，包装 `__platform_driver_register` |
| `platform_driver_unregister` | 注销驱动 | |
| `platform_driver_probe` | 注册 + 立即 probe | probe 可放 `__init`，节省内存 |
| `module_platform_driver` | 宏：module_init + module_exit | **最常用** |
| `builtin_platform_driver` | 宏：builtin 版本 | |

### 2.3 资源获取

驱动 probe 中最常用的资源获取 API：

| API | 说明 | 备注 |
|-----|------|------|
| `platform_get_resource(pdev, IORESOURCE_MEM, 0)` | 获取第 0 个 MMIO 资源 | 返回 `struct resource *` |
| `platform_get_resource(pdev, IORESOURCE_IRQ, 0)` | 获取第 0 个 IRQ 资源 | |
| `platform_get_irq(pdev, 0)` | 获取 IRQ 号 | 直接返回 int |
| `platform_get_irq_byname(pdev, "tx")` | 按名字获取 IRQ | |
| `devm_platform_ioremap_resource(pdev, 0)` | 获取资源 + 申请 + ioremap 合一 | **最常用**，1,231 处 |
| `devm_platform_get_and_ioremap_resource` | 同上，额外返回 resource 指针 | |

---

## 3. bus_type 与 sysfs

`platform_bus_type`（`drivers/base/platform.c`）是全局定义的总线类型：

```
/sys/bus/platform/
    devices/          → 所有 platform_device 的符号链接
    drivers/          → 所有 platform_driver 的目录
    drivers_probe     → 写 1 触发重新匹配
    drivers_autoprobe → 自动 probe 开关
```

每个 platform_device 在 `/sys/devices/` 下有实际目录，`/sys/bus/platform/devices/` 下是指向它的符号链接。

---

## 4. arm64 特殊性

- arm64 上 platform_device 主要由 DT 节点创建（`drivers/of/platform.c` 的 `of_platform_device_create_pdata`）
- `pdev_archdata` 在 arm64 上为空结构体（`arch/arm64/include/asm/device.h:11-12`）
- DMA 配置通过 `bus->dma_configure` 回调走 `of_dma_configure()`
- arm64 不使用 `MACHINE_START` / `DT_MACHINE_START`（全在 `arch/arm/`）

---

## 5. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `platform_driver_register` | 1,070 | 驱动注册 |
| `devm_platform_ioremap_resource` | 1,231 | 资源映射 |
| `devm_ioremap_resource` | 798 | 通用资源映射 |

platform_device 是内核中使用最广泛的 bus_type，绝大多数 SoC 外设驱动都基于它。
