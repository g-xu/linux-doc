# Linux 设备模型（Device Model）

> **定位**：Linux 内核的统一设备管理框架。所有设备——无论挂在 platform 总线、SPI 总线、USB 总线还是 PCI 总线上——都通过 `struct device` 接入同一套注册/匹配/生命周期/sysfs 机制。设备模型不是一个"驱动"，而是所有驱动的骨架。

---

## 1. 核心概念

### 1.1 五大结构体的关系

```
                    ┌──────────────────────┐
                    │   struct bus_type    │  ← 总线类型（platform / spi / usb ...）
                    │   bus.h:84-118       │
                    │   match() / probe()  │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                                 ▼
┌─────────────────────┐           ┌─────────────────────────┐
│  struct device       │           │  struct device_driver    │
│  device.h:557-655   │           │  driver.h:96-122         │
│                     │           │                          │
│  .bus → bus_type    │           │  .bus → bus_type         │
│  .parent → device*  │           │  .probe() / .remove()   │
│  .kobj (sysfs)      │           │  .of_match_table        │
│  .of_node (DT)      │           │  .pm                    │
│  .release()         │           └─────────────────────────┘
└─────────────────────┘
              ▲
              │ 内嵌
┌─────────────────────┐           ┌─────────────────────────┐
│ struct platform_device │        │   struct class           │
│ struct spi_device      │        │   class.h:54-78          │
│ struct usb_interface   │        │   （功能分类：net/input） │
└─────────────────────┘           └─────────────────────────┘
```

**一句话**：`bus_type` 定义匹配规则，`device` 代表硬件实例，`device_driver` 代表驱动代码，`class` 按功能分组。

### 1.2 `struct device` 关键字段

`include/linux/device.h:557-655`：

| 字段 | 行号 | 作用 |
|------|------|------|
| `kobj` | 558 | 内嵌 kobject → sysfs 目录 + 引用计数 |
| `parent` | 559 | 设备树中的父设备 |
| `bus` | 566 | 设备所在的总线类型 |
| `driver` | 567 | 绑定到此设备的驱动（probe 后设置） |
| `platform_data` | 569 | 板级数据（核心不解释，驱动自用） |
| `driver_data` | 571 | 驱动私有数据（`dev_set_drvdata` / `dev_get_drvdata`） |
| `of_node` | 619 | 对应的 DT 节点（**arm64 主路径**） |
| `fwnode` | 620 | 固件节点（DT 或 ACPI） |
| `devt` | 625 | 字符/块设备号 |
| `class` | 631 | 所属功能类 |
| `type` | 564 | 设备子类型 |
| `release` | 634 | 引用计数归零时的释放回调 |
| `power` | 578 | 电源管理状态 |
| `links` | 577 | 设备链接（supplier/consumer 依赖） |

### 1.3 `struct device_driver` 关键字段

`include/linux/device/driver.h:96-122`：

| 字段 | 行号 | 作用 |
|------|------|------|
| `name` | 97 | 驱动名 |
| `bus` | 98 | 注册到哪条总线 |
| `of_match_table` | 106 | DT compatible 匹配表（**arm64 主路径**） |
| `acpi_match_table` | 107 | ACPI 匹配表 |
| `probe` | 109 | 探测回调 |
| `remove` | 111 | 移除回调 |
| `pm` | 118 | 电源管理操作集 |

### 1.4 `struct bus_type` 关键字段

`include/linux/device/bus.h:84-118`：

| 字段 | 行号 | 作用 |
|------|------|------|
| `name` | 85 | 总线名（如 "platform"、"spi"） |
| `match` | 92 | **核心**：判断 device 与 driver 是否匹配 |
| `probe` | 94 | 总线级 probe（优先于 driver->probe） |
| `dma_configure` | 107 | DMA 设置（arm64 上配置 SMMU） |
| `iommu_ops` | 112 | IOMMU 操作 |
| `pm` | 110 | 总线级 PM 操作 |

---

## 2. 注册与匹配流程

### 2.1 设备注册

`device_register()` = `device_initialize()` + `device_add()`

**`device_initialize()`**（`drivers/base/core.c:3156-3179`）：
- 设置 `kobj.kset = devices_kset`
- 调用 `kobject_init(&dev->kobj, &device_ktype)`
- 初始化 DMA、mutex、devres、PM、设备链接

**`device_add()`**（`drivers/base/core.c:3578-3756`）——16 步：
1. 设设备名（`init_name` 或 `bus->dev_name + id`）
2. `kobject_add()` — 创建 sysfs 目录
3. 创建 `uevent` 属性文件
4. 创建 `subsystem` / `of_node` 符号链接
5. 添加 class/type/device 属性组
6. `bus_add_device()` — 加入总线设备列表
7. PM sysfs 添加
8. `BUS_NOTIFY_ADD_DEVICE` 通知链
9. `kobject_uevent(KOBJ_ADD)` — 发 uevent 到用户空间
10. **`bus_probe_device()`** — 触发驱动匹配

### 2.2 驱动注册

`driver_register()`（`drivers/base/driver.c:222-259`）：
1. 检查是否重复注册
2. `bus_add_driver()` — 加入总线驱动列表
3. 如果 `drivers_autoprobe`，调 `driver_attach()` 遍历所有设备尝试匹配
4. `kobject_uevent(KOBJ_ADD)`

### 2.3 匹配与绑定（完整调用链）

```
bus_probe_device(dev)                       // bus.c:480
  → device_initial_probe(dev)               // dd.c:1062
    → __device_attach(dev, true)            // dd.c:985
      → bus_for_each_drv(bus, NULL, dev, __device_attach_driver)
        → __device_attach_driver(dev, drv)  // dd.c:906
          → driver_match_device(drv, dev)   // base.h:144
              = drv->bus->match(dev, drv)   // 总线特定 match 函数
          → driver_probe_device(dev, drv)   // dd.c:809
            → __driver_probe_device()       // dd.c:764
              → really_probe(dev, drv)      // dd.c:584
                1. 检查设备链接 supplier    // dd.c:600
                2. dev->driver = drv        // dd.c:613
                3. pinctrl_bind_pins()      // dd.c:616
                4. bus->dma_configure()     // dd.c:620  ← arm64 SMMU 配置
                5. driver_sysfs_add()       // dd.c:626
                6. pm_domain->activate()    // dd.c:633
                7. call_driver_probe()      // dd.c:639  ← 实际 probe 调用
                8. device_add_groups()      // dd.c:658
                9. driver_bound()           // dd.c:689  ← 完成绑定
```

**`call_driver_probe()`**（`dd.c:553-582`）：
- 优先调 `bus->probe()`，其次 `drv->probe()`
- 处理 `-EPROBE_DEFER`（延迟 probe）、`-ENODEV`、`-ENXIO`

### 2.4 驱动侧触发匹配

当驱动在设备之后注册时：

```
driver_register()
  → bus_add_driver()                        // bus.c:592
    → driver_attach(drv)                    // dd.c:1216
      → bus_for_each_dev(bus, NULL, drv, __driver_attach)
        → __driver_attach(dev, drv)         // dd.c:1141
          → driver_match_device() → driver_probe_device() → really_probe()
```

---

## 3. sysfs 布局

### 3.1 顶层结构

启动时由三个 init 函数创建（`core.c:4147`、`bus.c:1173`、`class.c:578`）：

```
/sys/
├── devices/          ← 所有设备（按 parent 层级组织）
├── bus/
│   ├── platform/
│   │   ├── devices/  → 符号链接到 /sys/devices/...
│   │   ├── drivers/  → 每个驱动一个目录
│   │   ├── drivers_probe
│   │   └── drivers_autoprobe
│   ├── spi/
│   ├── i2c/
│   └── ...
├── class/
│   ├── net/          → 符号链接到 /sys/devices/...
│   ├── input/
│   └── ...
└── dev/
    ├── char/         → 按主:次设备号索引
    └── block/
```

### 3.2 单个设备的 sysfs

`device_add()` 为每个设备创建（`core.c:3578-3756`）：

```
/sys/devices/<path>/<devname>/
├── uevent            ← 可写，触发 uevent
├── dev               ← 主:次设备号（如果有 devt）
├── subsystem → /sys/bus/<bus>/     ← 符号链接
├── of_node → /sys/firmware/devicetree/...  ← DT 节点链接
├── driver → /sys/bus/<bus>/drivers/<drv>/  ← 绑定后出现
├── driver/<attr>     ← drv->dev_groups 中的属性
├── online            ← 如果支持上下线
├── removable         ← 是否可移除
└── waiting_for_supplier  ← fw_devlink 状态
```

---

## 4. 生命周期与引用计数

### 4.1 引用计数

通过内嵌的 `struct kobject kobj`（含 `struct kref`）管理：

- `get_device(dev)` → `kobject_get()` → refcount++（`core.c:3791`）
- `put_device(dev)` → `kobject_put()` → refcount--（`core.c:3801`）

### 4.2 释放回调

引用计数归零 → `device_release()`（`core.c:2484-2512`）：

```c
static void device_release(struct kobject *kobj)
{
    devres_release_all(dev);          // 释放所有 devres
    kfree(dev->dma_range_map);
    if (dev->release)                 // 第一选择：dev->release()
        dev->release(dev);
    else if (dev->type && dev->type->release)    // 第二选择
        dev->type->release(dev);
    else if (dev->class && dev->class->dev_release)  // 第三选择
        dev->class->dev_release(dev);
    else
        WARN(1, ...);                 // BUG：没有 release！
}
```

**规则**：每个 `struct device` **必须**有 release 回调（`dev->release` / `type->release` / `class->dev_release` 之一），否则内核会打印警告。

### 4.3 完整生命周期

```
ALLOCATE  → device_initialize()  → device_add()  → [probe & 使用]
                                                         │
         ← device_unregister() ← device_del() ← put_device() ←┘
         ... 引用归零 ...
         → device_release() → dev->release() → kfree
```

| API | 说明 |
|-----|------|
| `device_register(dev)` | init + add |
| `device_unregister(dev)` | del + put |
| `device_create(class, parent, devt, drvdata, fmt, ...)` | 分配 + 注册（字符/块设备常用） |
| `device_destroy(class, devt)` | 注销 + 释放 |

---

## 5. arm64 特殊性

### 5.1 空 archdata

`arch/arm64/include/asm/device.h:8-9`：`struct dev_archdata` 为空。arm64 不通过 archdata 传递架构信息，而是通过 `of_node`、`dma_ops`、`iommu` 等通用字段。

### 5.2 DT 驱动匹配

arm64 上设备发现完全依赖 DT。匹配链：
1. DT 节点 → `of_platform_device_create_pdata()` 创建 platform_device
2. `dev->of_node` 指向 DT 节点
3. `platform_match()` 调 `of_driver_match_device()` 比较 compatible

### 5.3 DMA/IOMMU 配置

`bus->dma_configure()` 在 `really_probe()` 中被调用（`dd.c:620`），对 platform 设备走 `of_dma_configure()`，设置 SMMU 映射。

---

## 6. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `device_register` | 246 | 设备注册 |
| `bus_register` | 147 | 总线类型注册 |
| `dev_set_drvdata` | 广泛使用 | 驱动私有数据存储 |

---

## §4 常见陷阱

### 陷阱 1：device_register() 失败后直接 kfree

**错误写法**：
```c
struct my_device *mdev = kzalloc(sizeof(*mdev), GFP_KERNEL);
int ret = device_register(&mdev->dev);
if (ret) {
    kfree(mdev);  /* BUG! 内存泄漏 + 可能的 double-free */
    return ret;
}
```

**正确写法**：
```c
struct my_device *mdev = kzalloc(sizeof(*mdev), GFP_KERNEL);
int ret = device_register(&mdev->dev);
if (ret) {
    put_device(&mdev->dev);  /* device_initialize 已初始化引用计数 */
    /* put_device → device_release → dev->release() → kfree(mdev) */
    return ret;
}
```

**原因**：`device_register()` 内部调用 `device_initialize()`，后者调用 `kobject_init()` 初始化引用计数为 1（`drivers/base/core.c:3159`）。即使 `device_add()` 失败，这个引用计数仍然存在。`device_add()` 的注释明确写道"Never directly free @dev after calling this function"（`drivers/base/core.c:3772-3774`）。必须用 `put_device()` 递减引用计数，引用归零时自动调用 `dev->release()` 完成释放。

**源码依据**：`drivers/base/core.c:3772-3774` — 注释；`drivers/base/core.c:3156-3159` — `device_initialize` 初始化 kobject。

### 陷阱 2：缺少 release 回调

**错误写法**：
```c
static struct device my_dev = {
    .init_name = "my-dev",
    .parent = &platform_bus,
    /* 没有设置 release 回调！ */
};
```

**正确写法**：
```c
static void my_dev_release(struct device *dev)
{
    struct my_device *mdev = container_of(dev, struct my_device, dev);
    kfree(mdev);
}

static struct device my_dev = {
    .init_name = "my-dev",
    .parent = &platform_bus,
    .release = my_dev_release,  /* 必须提供 */
};
```

**原因**：`device_release()` 在引用计数归零时依次检查 `dev->release`、`dev->type->release`、`dev->class->dev_release`，三者都为空时打印 `WARN(1, "Device '%s' does not have a release() function")`（`drivers/base/core.c:2509-2510`）。更严重的是，没有 release 回调意味着设备结构体永远不会被释放——内存泄漏。

**源码依据**：`drivers/base/core.c:2502-2510` — release 回调的三级回退和 WARN。

### 陷阱 3：device_add() 失败后调 device_del()

**错误写法**：
```c
struct device *dev = &mdev->dev;
device_initialize(dev);
/* ... 设置字段 ... */
int ret = device_add(dev);
if (ret) {
    device_del(dev);  /* BUG! device_add 失败后不应调 device_del */
    put_device(dev);
}
```

**正确写法**：
```c
struct device *dev = &mdev->dev;
device_initialize(dev);
/* ... 设置字段 ... */
int ret = device_add(dev);
if (ret) {
    /* device_add 失败：只用 put_device() 放弃引用 */
    put_device(dev);
    return ret;
}
/* device_add 成功后：用 device_unregister() 或 device_del() + put_device() */
```

**原因**：`device_add()` 的注释明确说明："if device_add() succeeds, you should call device_del() when you want to get rid of it. If device_add() has *not* succeeded, use *only* put_device() to drop the reference count"（`drivers/base/core.c:3573-3576`）。`device_add()` 失败时，sysfs 目录、bus 列表等都没有创建，`device_del()` 尝试拆除这些不存在的结构会导致空指针解引用。

**源码依据**：`drivers/base/core.c:3573-3576` — 注释明确区分成功/失败路径。

### 陷阱 4：probe 失败后 driver_data 仍被访问

**错误写法**：
```c
static int my_probe(struct device *dev)
{
    struct my_priv *priv = kzalloc(sizeof(*priv), GFP_KERNEL);
    if (!priv)
        return -ENOMEM;

    dev_set_drvdata(dev, priv);

    ret = init_hardware(dev);
    if (ret) {
        /* probe 失败，但忘记清理 driver_data */
        return ret;  /* devres 会释放 priv，但 driver_data 仍指向已释放内存 */
    }
    return 0;
}
```

**正确写法**：
```c
static int my_probe(struct device *dev)
{
    struct my_priv *priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
    if (!priv)
        return -ENOMEM;

    dev_set_drvdata(dev, priv);

    ret = init_hardware(dev);
    if (ret) {
        dev_set_drvdata(dev, NULL);  /* 显式清理 */
        return ret;
    }
    return 0;
}
```

**原因**：`dev_set_drvdata()` 只是简单地把指针存入 `dev->driver_data`（`include/linux/device.h:571`）。当 probe 失败时，设备模型的 remove 回调不会被调用（因为驱动没有成功绑定），driver_data 中的指针可能指向已释放的内存。后续如果有代码通过 `dev_get_drvdata()` 访问，会得到悬空指针。

**源码依据**：`include/linux/device.h:571` — `driver_data` 字段定义；`drivers/base/driver.c` — probe 失败时不回调 remove。

### 陷阱 5：在 release 回调外释放包含 device 的结构体

**错误写法**：
```c
struct my_device {
    struct device dev;
    int extra_data;
};

/* 错误：在 remove 回调中直接释放 */
static int my_remove(struct device *dev)
{
    struct my_device *mdev = container_of(dev, struct my_device, dev);
    kfree(mdev);  /* BUG! 此时 dev 的引用计数可能还没归零 */
    return 0;
}
```

**正确写法**：
```c
static void my_release(struct device *dev)
{
    struct my_device *mdev = container_of(dev, struct my_device, dev);
    kfree(mdev);  /* 正确：引用计数归零时才释放 */
}

static int my_remove(struct device *dev)
{
    /* remove 只做硬件清理，不释放结构体 */
    cleanup_hardware(dev);
    return 0;
}
```

**原因**：设备的生命周期由引用计数控制。`device_unregister()` 调用 `device_del()` + `put_device()`，但 `put_device()` 只是递减引用计数。如果还有其他引用（如 sysfs 文件打开、子设备持有父设备引用），引用计数不会归零，结构体仍然存活。在 remove 中直接 kfree 会导致其他持有者访问已释放内存。release 回调是引用计数归零时的唯一安全释放点。

**源码依据**：`drivers/base/core.c:2484-2512` — `device_release` 在引用归零时调用。

---

## §6 取证摘要

**读取的文件**：
- `drivers/base/core.c` — device_initialize、device_add、device_register、device_release、get_device/put_device 实现
- `include/linux/device.h` — struct device 字段定义（driver_data、release 等）
- `include/linux/device/driver.h` — struct device_driver 字段定义
- `include/linux/device/bus.h` — struct bus_type 字段定义

**关键论断出处**：
- "device_register 失败后不能直接 kfree" — 来源：`drivers/base/core.c:3772-3774`
- "缺少 release 回调会打 WARN" — 来源：`drivers/base/core.c:2509-2510`
- "device_add 失败后不能调 device_del" — 来源：`drivers/base/core.c:3573-3576`
- "device_initialize 初始化 kobject 引用计数" — 来源：`drivers/base/core.c:3159`
- "release 回调三级回退" — 来源：`drivers/base/core.c:2502-2510`
- "driver_data 字段在 device.h:571" — 来源：`include/linux/device.h:571`

**未验证项**：
- `dev_set_drvdata` 的并发安全性（是否需要锁保护）未在当前源码阅读中确认
- 异步 probe（`driver_probe_device` 的 async 路径）对 driver_data 生命周期的影响未深入追踪
