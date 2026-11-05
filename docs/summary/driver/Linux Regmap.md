## Regmap
Linux 内核的 **Regmap (Register Map)** 模块是内核中用于减少代码重复、统一驱动接口的一个极其重要的基础设施。它的出现彻底改变了 I2C、SPI 等慢速总线设备的驱动编写方式。

以下是对 Regmap 模块的详细介绍及 API 汇总。

---

### 1. 什么是 Regmap？

在 Regmap 出现之前，如果你写一个 I2C 设备驱动，你需要手动调用 `i2c_transfer` 来读写寄存器；如果写一个 SPI 驱动，你需要调用 `spi_sync`。如果设备同时支持 I2C 和 SPI，你需要写两套底层读写逻辑。此外，你还需要自己手动去处理寄存器缓存（Cache）、读写锁、字节序转换等繁琐工作。

**Regmap 的核心目的就是将“寄存器访问”逻辑与“底层总线传输”逻辑解耦。**

它位于**具体的设备驱动**（如 Audio Codec, PMIC 驱动）和**总线驱动**（I2C, SPI, MMIO）之间。

### 2. 核心概念与架构

#### 2.1 核心组件
1.  **Map (映射层)**：对外提供统一的读写接口（`regmap_read`, `regmap_write`）。
2.  **Bus (总线适配层)**：后端支持多种总线，包括 I2C, SPI, MMIO (Memory Mapped IO), SPMI, AC97, SLIMbus 等。
3.  **Cache (缓存机制)**：这是 Regmap 最强大的功能之一。它在内存中维护一份寄存器副本。
    *   **读缓存**：直接读内存，减少慢速 I/O。
    *   **写缓冲**：支持延迟写入。
4.  **Debugfs**：Regmap 会自动在 `/sys/kernel/debug/regmap/` 下创建节点，允许用户在不写代码的情况下查看所有寄存器值，非常利于调试。

#### 2.2 关键数据结构：`struct regmap_config`
这是使用 Regmap 的灵魂，驱动通过配置这个结构体告诉内核硬件的特性：

```c
static const struct regmap_config my_regmap_config = {
    .reg_bits = 8,        // 寄存器地址宽度 (如 8位地址)
    .val_bits = 16,       // 寄存器值宽度 (如 16位数据)
    
    .max_register = 0xFF, // 最大寄存器地址
    
    .cache_type = REGCACHE_RBTREE, // 缓存类型 (红黑树，适合稀疏寄存器)
    
    .volatile_reg = is_volatile_reg, // 回调：判断寄存器是否易变 (必须读硬件)
    .writeable_reg = is_writeable_reg, // 回调：判断寄存器是否可写
    .readable_reg = is_readable_reg,   // 回调：判断寄存器是否可读
};
```

### 3. 工作原理

当驱动调用 `regmap_write(map, reg, val)` 时：
1.  **锁保护**：Regmap 自动获取互斥锁（Mutex）或自旋锁（Spinlock），防止并发冲突。
2.  **格式化**：根据 `.reg_bits` 和 `.val_bits` 以及端序（Endianness）配置，将数据打包成字节流。
3.  **缓存更新**：如果开启了 Cache，先更新内存中的副本。
4.  **总线传输**：调用底层的 I2C 或 SPI 发送函数将数据发往硬件。

当驱动调用 `regmap_read(map, reg, val)` 时：
1.  **查缓存**：如果该寄存器不在 `volatile_table` 中且缓存中有值，直接返回内存数据（速度极快）。
2.  **透传硬件**：如果是易变寄存器（如状态寄存器），则真正发起总线读取。

### 4. 缓存类型 (Cache Types)

选择合适的缓存类型对性能至关重要：
*   **REGCACHE_NONE**：不使用缓存。每次都访问硬件。
*   **REGCACHE_RBTREE**：**最常用**。使用红黑树存储。适合**稀疏**的寄存器表（地址不连续）。
*   **REGCACHE_FLAT**：使用数组存储。适合寄存器地址紧密排列且范围较小的设备（访问速度最快，但占内存）。
*   **REGCACHE_LZO/MAPLE**：压缩存储，用于寄存器非常多的超大型设备。

### 5. 高级功能：Regmap-IRQ

Regmap 自带了一个 **IRQ 控制器框架**。很多 PMIC 芯片内部有一个中断状态寄存器（Status Register）和掩码寄存器（Mask Register）。
通过 `regmap_add_irq_chip`，Regmap 可以自动帮你把这些寄存器位映射成 Linux 的虚拟中断号。你在写驱动时，就像申请普通 GPIO 中断一样申请这些位中断，极大地简化了中断处理逻辑。

---

### 6. 适用场景与注意事项

**适用场景：**
*   慢速总线设备（I2C/SPI）。
*   拥有大量寄存器的设备（Audio Codec, PMIC, Sensor）。
*   需要频繁读写，但大部分读操作可以被缓存优化的场景。
*   混合使用 I2C/SPI 接口的驱动复用。

**注意事项：**
1.  **Volatile vs Non-Volatile**：务必正确配置 `volatile_reg` 回调。
    *   **Volatile**: 硬件会自动改变值的寄存器（如状态位、ADC值）。**永远不缓存，每次必读硬件。**
    *   **Non-Volatile**: 只有软件写才会变的寄存器（如控制位）。**可以缓存。**
2.  **Precious Registers**：有些特定的“读即清”（Read-to-clear）寄存器，配置为 `precious_reg`，读取后不要放入缓存。
3.  **性能开销**：对于极高速的 MMIO（内存映射 IO），Regmap 引入的锁和函数调用开销可能不划算，但在 I2C/SPI 场景下，这层开销相对于总线传输时间可以忽略不计。

---

### 7. Regmap API 汇总表

以下是 Linux 内核 Regmap 模块的主要 API 及其功能汇总。

#### 7.1 初始化与注销 (Initialization & Teardown)

| API 接口 | 功能分类 | 详细功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| **`devm_regmap_init_i2c`** | 初始化 | 为 I2C 设备创建 Regmap 实例。托管资源（Managed），驱动卸载时自动释放。 | **推荐**。最常用的 I2C 初始化方式。 |
| **`devm_regmap_init_spi`** | 初始化 | 为 SPI 设备创建 Regmap 实例。托管资源。 | **推荐**。最常用的 SPI 初始化方式。 |
| **`devm_regmap_init_mmio`** | 初始化 | 为 MMIO (Memory Mapped) 设备创建 Regmap 实例。 | 用于将内存映射的 SoC 内部寄存器也纳入 Regmap 管理。 |
| **`devm_regmap_init`** | 初始化 | 通用初始化接口。需要手动传入 `struct regmap_bus`。 | 当使用非标准总线或自定义总线时使用。 |
| **`regmap_exit`** | 注销 | 销毁 Regmap 实例。 | 只有在使用非 `devm_` 开头的初始化函数时才需要手动调用。 |

#### 7.2 基础读写操作 (Basic Access)

| API 接口 | 功能分类 | 详细功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| **`regmap_write`** | 写寄存器 | 向指定寄存器写入一个值。 | `regmap_write(map, reg, val);` |
| **`regmap_read`** | 读寄存器 | 读取指定寄存器的值。 | `regmap_read(map, reg, &val);` |
| **`regmap_raw_write`** | 裸写 | 写入原始字节流，不进行字节序转换。 | 适用于非标准格式的数据传输。 |
| **`regmap_raw_read`** | 裸读 | 读取原始字节流。 | |
| **`regmap_bulk_write`** | 批量写 | 向**同一**起始地址写入多个数据（通常用于 FIFO）。 | 类似 `memcpy` 到寄存器。 |
| **`regmap_bulk_read`** | 批量读 | 从**同一**起始地址读取多个数据。 | |
| **`regmap_multi_reg_write`**| 多寄存器写 | 一次性执行一组 `{reg, val}` 对的写入操作。 | 非常适合上电初始化序列（Sequence）。 |

#### 7.3 位操作 (Bit Manipulation) - **最常用**

这部分 API 极大地简化了“读-改-写”的操作流程。

| API 接口 | 功能分类 | 详细功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| **`regmap_update_bits`** | 读改写 | 更新寄存器的特定位。 `(val & mask) | (set & mask)` | **原子操作**。它会先读，对比是否变化，若有变化再写，非常高效且安全。 |
| **`regmap_update_bits_check`**| 读改写 | 同上，但会返回一个 bool 告诉调用者数据是否真的发生了改变。 | 用于需要根据是否发生变化来执行后续逻辑的场景。 |
| **`regmap_write_bits`** | 位写 | `regmap_update_bits` 的别名包装。 | |
| **`regmap_test_bits`** | 位测试 | 测试寄存器的特定位是否被置位。 | 相当于 `read` 后进行 `&` 运算。 |

#### 7.4 缓存管理 (Cache Management)

| API 接口 | 功能分类 | 详细功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| **`regcache_sync`** | 同步 | 将缓存中的脏数据（Dirty Data）全部同步写入硬件。 | 常用于设备从休眠（Suspend）唤醒（Resume）后恢复寄存器状态。 |
| **`regcache_cache_only`** | 模式控制 | 设置 Regmap 为“只缓存”模式或“直通”模式。 | 在设备断电或休眠期间，设为 Cache Only，防止驱动意外访问导致总线错误。 |
| **`regcache_cache_bypass`** | 模式控制 | 设置是否绕过缓存直接访问硬件。 | 调试或特殊状态下强制读写硬件。 |
| **`regcache_mark_dirty`** | 标记 | 标记整个缓存为“脏”。 | 配合 `regcache_sync` 使用。 |

#### 7.5 中断管理 (IRQ)

| API 接口 | 功能分类 | 详细功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| **`regmap_add_irq_chip`** | 中断注册 | 将寄存器位映射为虚拟中断控制器。 | 传入 `regmap_irq_chip` 结构体，描述 Status/Mask 寄存器偏移。 |
| **`regmap_del_irq_chip`** | 中断注销 | 移除中断控制器。 | |
| **`regmap_irq_get_virq`** | 获取中断号 | 根据硬件的中断 Bit 索引，获取 Linux 虚拟中断号。 | 获取到 virq 后，驱动可以用 `request_threaded_irq` 申请中断。 |

### 8. 最佳实践代码片段

```c
/* 1. 配置 */
static const struct regmap_config foo_regmap_config = {
    .reg_bits = 8,
    .val_bits = 8,
    .max_register = 0x40,
    .cache_type = REGCACHE_RBTREE,
    .volatile_reg = foo_volatile_reg,
};

/* 2. 初始化 (在 Probe 中) */
struct regmap *map = devm_regmap_init_i2c(i2c_client, &foo_regmap_config);
if (IS_ERR(map)) return PTR_ERR(map);

/* 3. 使用 */
// 简单写
regmap_write(map, 0x10, 0xAB);

// 读改写：将 0x20 寄存器的 Bit 0 和 Bit 1 置为 1，其他位不变
// 参数：map, reg, mask, val
regmap_update_bits(map, 0x20, 0x03, 0x03);
```