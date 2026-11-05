# regmap（寄存器映射抽象）

> **定位**：为硬件寄存器访问提供统一抽象层——屏蔽底层总线差异（I2C / SPI / MMIO / MDIO ...），提供寄存器缓存、位域操作、IRQ 域桥接等通用功能。是 SoC 外设驱动（codec、PMIC、sensor）最核心的基础设施之一。

---

## 1. 核心架构

```
                    驱动代码
                      │
                      │ regmap_write(map, reg, val)
                      │ regmap_read(map, reg, &val)
                      │ regmap_update_bits(map, reg, mask, val)
                      ▼
              ┌───────────────┐
              │  struct regmap │  ← 核心抽象
              │  regmap.c      │
              │                │
              │  锁 + 缓存 +  │
              │  访问检查 +    │
              │  格式化        │
              └───────┬───────┘
                      │
              ┌───────┴───────┐
              │ struct regmap_bus │  ← 总线后端（vtable）
              │ .read / .write    │
              └───────┬───────┘
                      │
        ┌─────────┬───┴───┬──────────┬────────┐
        ▼         ▼       ▼          ▼        ▼
      I2C       SPI     MMIO      MDIO    SoundWire ...
```

---

## 2. 核心结构体

头文件：`include/linux/regmap.h`；核心实现：`drivers/base/regmap/regmap.c`（3529 行）

### 2.1 `struct regmap_config`

`regmap.h:370-440`——驱动在初始化 regmap 时填写的配置：

| 字段 | 作用 | 必填 |
|------|------|------|
| `reg_bits` | 寄存器地址位数 | 是 |
| `val_bits` | 寄存器值位数 | 是 |
| `max_register` | 最大有效寄存器地址 | 推荐 |
| `reg_stride` | 地址步长（有效地址是的倍数） | 否 |
| `cache_type` | 缓存类型（NONE / RBTREE / FLAT / COMPRESSED） | 否 |
| `reg_defaults` | 复位默认值数组（用于缓存同步） | 否 |
| `writeable_reg` / `readable_reg` | 可写/可读判断回调 | 否 |
| `volatile_reg` | 易失寄存器判断（不可缓存） | 否 |
| `precious_reg` | 珍贵寄存器判断（不可读回，如 clear-on-read 状态寄存器） | 否 |
| `wr_table` / `rd_table` / `volatile_table` | 范围表版本（替代回调） | 否 |
| `read_flag_mask` / `write_flag_mask` | 读/写操作的地址高位掩码 | 否 |
| `fast_io` | 使用 spinlock 代替 mutex（MMIO 场景） | 否 |
| `ranges` | 间接寻址/分页配置 | 否 |
| `can_sleep` | 操作是否可以睡眠 | 否 |

### 2.2 `struct regmap_bus`

总线后端 vtable（每种总线一个实例）：

| 回调 | 作用 |
|------|------|
| `reg_write` | 单寄存器写 |
| `reg_read` | 单寄存器读 |
| `write` | 批量写（数据含地址 + 值） |
| `read` | 批量读 |
| `gather_write` | 分离地址和数据的批量写 |

---

## 3. 总线后端

每种物理总线对应一个 `regmap-<bus>.c` 文件：

| 后端 | 文件 | 初始化函数 |
|------|------|-----------|
| I2C | `regmap-i2c.c` | `__regmap_init_i2c` / `__devm_regmap_init_i2c` |
| SPI | `regmap-spi.c` | `__regmap_init_spi` / `__devm_regmap_init_spi` |
| MMIO | `regmap-mmio.c` | `__regmap_init_mmio_clk` / `__devm_regmap_init_mmio_clk` |
| MDIO | `regmap-mdio.c` | `__regmap_init_mdio` / `__devm_regmap_init_mdio` |
| SoundWire | `regmap-sdw.c` | `__regmap_init_sdw` / `__devm_regmap_init_sdw` |
| SLIMbus | `regmap-slimbus.c` | `__regmap_init_slimbus` |
| SPMI | `regmap-spmi.c` | |
| I3C | `regmap-i3c.c` | `__devm_regmap_init_i3c` |
| AC97 | `regmap-ac97.c` | `__regmap_init_ac97` |
| W1 | `regmap-w1.c` | |
| SPI AVMM | `regmap-spi-avmm.c` | |
| SCCB | `regmap-sccb.c` | `__regmap_init_sccb` |

驱动通常不直接调这些函数——通过宏 `regmap_init_i2c()` / `devm_regmap_init_i2c()` 等间接调用（宏自动传 `THIS_MODULE` 和锁类名）。

---

## 4. 寄存器缓存

`drivers/base/regmap/regcache.c`（827 行）+ 三种缓存实现：

| 缓存类型 | 文件 | 特点 |
|----------|------|------|
| `REGCACHE_NONE` | — | 不缓存，每次直接访问硬件 |
| `REGCACHE_FLAT` | `regcache-flat.c`（83 行） | 连续数组，O(1) 查找，适合寄存器密集的小设备 |
| `REGCACHE_RBTREE` | `regcache-rbtree.c`（554 行） | 红黑树，适合寄存器稀疏的大设备 |
| `REGCACHE_COMPRESSED` | `regcache-lzo.c`（368 行） | LZO 压缩，适合大量默认值 |

缓存的作用：
- **减少总线访问**：读操作直接返回缓存值（非 volatile 寄存器）
- **suspend/resume 同步**：`regcache_sync()` 把缓存写回硬件，恢复被修改的寄存器
- **默认值管理**：`reg_defaults` 数组提供 POR 值，缓存初始化时用来填充

---

## 5. 核心 API

### 5.1 初始化

| API | 说明 | 备注 |
|-----|------|------|
| `devm_regmap_init_i2c(i2c, config)` | I2C 设备初始化 regmap | **最常用** |
| `devm_regmap_init_spi(spi, config)` | SPI 设备初始化 regmap | |
| `devm_regmap_init_mmio(dev, regs, config)` | MMIO 初始化 regmap | `regs` 是已 ioremap 的地址 |

所有 `devm_regmap_init_*` 都是 devres 管理的，detach 时自动释放。

### 5.2 读写操作

| API | 说明 | 备注 |
|-----|------|------|
| `regmap_read(map, reg, &val)` | 读单个寄存器 | 4,356 处 |
| `regmap_write(map, reg, val)` | 写单个寄存器 | 7,401 处 |
| `regmap_update_bits(map, reg, mask, val)` | 读-改-写（RMW） | 只改 mask 覆盖的位 |
| `regmap_update_bits_base(map, reg, mask, val, &change, async, force)` | 底层 RMW | 可获取是否改变 |
| `regmap_bulk_read(map, reg, buf, val_count)` | 批量读 | |
| `regmap_bulk_write(map, reg, buf, val_count)` | 批量写 | |
| `regmap_multi_reg_write(map, reg, regs, num)` | 多寄存器写（不同地址） | |
| `regmap_raw_read(map, reg, buf, buf_len)` | 原始读（不格式化） | |
| `regmap_raw_write(map, reg, buf, buf_len)` | 原始写 | |

### 5.3 位域操作

`struct regmap_field`（`regmap.h` 声明）——对一个寄存器的特定位域提供独立操作：

| API | 说明 |
|-----|------|
| `regmap_field_alloc(map, reg, field)` | 分配位域（`reg` + 位移 + 宽度） |
| `devm_regmap_field_alloc(dev, map, reg, field)` | devres 版本 |
| `regmap_field_read(field, &val)` | 读位域值 |
| `regmap_field_write(field, val)` | 写位域值 |
| `regmap_field_update_bits(field, mask, val)` | RMW 位域 |

### 5.4 轮询等待

| API | 说明 |
|-----|------|
| `regmap_read_poll_timeout(map, addr, val, cond, sleep_us, timeout_us)` | 轮询直到条件满足或超时 |
| `regmap_read_poll_timeout_atomic(map, addr, val, cond, delay_us, timeout_us)` | 原子上下文版本 |
| `regmap_field_read_poll_timeout(field, val, cond, sleep_us, timeout_us)` | 位域版本 |

---

## 6. IRQ 域桥接

`drivers/base/regmap/regmap-irq.c`（1319 行）——把 regmap 设备的中断寄存器桥接为标准 Linux IRQ 域：

```
硬件中断引脚 → regmap_irq_thread() → 读中断状态寄存器
                                        │
                                        ▼
                                  识别哪个子中断触发
                                        │
                                        ▼
                                  handle_irq() → 上层驱动 ISR
```

| API | 说明 |
|-----|------|
| `devm_regmap_add_irq_chip(dev, map, irq, irq_flags, config, &data)` | 注册 IRQ 域 |
| `devm_regmap_del_irq_chip(dev, irq, data)` | 注销 |

`struct regmap_irq_chip` 描述设备的中断寄存器布局：哪些寄存器是 mask、哪些是 status、每个子中断对应哪一位。

---

## 7. 访问检查

regmap 在每次操作前检查寄存器是否可访问（`regmap.c` 中 `regmap_writeable` / `regmap_readable` / `regmap_volatile` / `regmap_precious`）：

1. 如果 `config->writeable_reg` 回调存在，调它
2. 否则查 `config->wr_table`（`regmap_access_table`）
3. 否则检查 `max_register` 上界
4. 都没有 → 默认可访问

`CONFIG_DEBUG_REGMAP` 开启 debugfs 接口，可查看所有寄存器的访问状态和缓存内容。

---

## 8. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `regmap_write` | 7,401 | 最常用写操作 |
| `regmap_read` | 4,356 | 最常用读操作 |
| `devm_regmap_init_mmio` | 229 | MMIO 初始化 |
| `devm_regmap_init_i2c` | 广泛使用 | I2C 初始化 |

---

## §4 常见陷阱

### 陷阱 1：cache_only 模式下写操作不触及硬件

**错误写法**：
```c
/* suspend 期间尝试写寄存器 */
regmap_write(map, REG_CONFIG, new_value);
/* 以为硬件已更新，但实际上只写了缓存！ */
```

**正确写法**：
```c
/* suspend 期间：写操作只更新缓存，硬件不变 */
regmap_write(map, REG_CONFIG, new_value);
/* resume 后：regcache_sync() 把缓存差异写回硬件 */
/* 如果确实需要立即写硬件，临时退出 cache_only： */
regcache_cache_only(map, false);
regmap_write(map, REG_CONFIG, new_value);
regcache_cache_only(map, true);
```

**原因**：当 `map->cache_only` 为 true 时，`_regmap_raw_write_impl()` 在缓存写入后直接返回，不执行硬件写操作（`drivers/base/regmap/regmap.c:1735-1738`）。这个模式通常在 suspend 期间由 PM 回调设置，目的是避免访问可能不可用的总线。如果驱动不知道当前处于 cache_only 模式，写操作会被静默吞掉，硬件状态与缓存不一致。

**源码依据**：`drivers/base/regmap/regmap.c:1735-1738` — `if (map->cache_only) { map->cache_dirty = true; return 0; }`。

### 陷阱 2：cache_bypass 导致缓存与硬件不一致

**错误写法**：
```c
/* 调试时绕过缓存直接写硬件 */
regcache_cache_bypass(map, true);
regmap_write(map, REG_CONFIG, 0x42);  /* 硬件更新了，但缓存还是旧值 */
regcache_cache_bypass(map, false);

/* 后续读操作返回缓存中的旧值！ */
regmap_read(map, REG_CONFIG, &val);
/* val 是旧值，不是 0x42！ */
```

**正确写法**：
```c
/* 方法 1：bypass 后手动同步缓存 */
regcache_cache_bypass(map, true);
regmap_write(map, REG_CONFIG, 0x42);
regcache_cache_bypass(map, false);
regcache_sync(map, ...);  /* 或手动更新缓存 */

/* 方法 2：直接写（不 bypass），让 regmap 同时更新缓存和硬件 */
regmap_write(map, REG_CONFIG, 0x42);
```

**原因**：`cache_bypass` 为 true 时，写操作跳过缓存直接访问硬件（`drivers/base/regmap/regmap.c:1718` 的 `if (!map->cache_bypass ...)` 条件不满足）。读操作同样绕过缓存直接读硬件。但当 bypass 关闭后，缓存仍然是旧值，后续的普通读操作返回缓存中的旧数据，造成"写 0x42 读回旧值"的诡异现象。

**源码依据**：`drivers/base/regmap/regmap.c:1718` — `if (!map->cache_bypass && map->format.parse_val)` 条件判断。

### 陷阱 3：regmap_reinit_cache() 无锁保护

**错误写法**：
```c
/* 多线程环境中重新初始化缓存 */
void reinit_config(struct regmap *map, struct new_config *cfg)
{
    /* 没有持锁！其他线程可能正在读写 */
    regmap_reinit_cache(map, &new_config);
}
```

**正确写法**：
```c
/* 确保没有其他线程在访问 regmap */
void reinit_config(struct regmap *map, struct new_config *cfg)
{
    /* 通常需要设备级别的锁或确保设备空闲 */
    mutex_lock(&map->mutex);  /* 如果 map 使用 mutex */
    regmap_reinit_cache(map, &new_config);
    mutex_unlock(&map->mutex);
}
```

**原因**：`regmap_reinit_cache()` 的注释明确说明"No explicit locking is done here, the user needs to ensure that this function will not race with other calls to regmap"（`drivers/base/regmap/regmap.c:1511-1512`）。该函数销毁旧缓存并创建新缓存（`regcache_exit` + `regcache_init`），如果其他线程正在读写，会导致访问已释放的缓存内存。

**源码依据**：`drivers/base/regmap/regmap.c:1511-1512` — 注释；`drivers/base/regmap/regmap.c:1518-1539` — 实现。

### 陷阱 4：regmap_field 空掩码

**错误写法**：
```c
struct reg_field bad_field = REG_FIELD(0x10, 0, 0);
/* reg_bit = 0, width = 0 → mask = 0！ */
struct regmap_field *field = devm_regmap_field_alloc(dev, map, bad_field);
```

**正确写法**：
```c
/* 至少指定 1 位宽度 */
struct reg_field good_field = REG_FIELD(0x10, 0, 1);
/* reg_bit = 0, reg_bit_end = 1 → 2 位宽，mask = 0x3 */
struct regmap_field *field = devm_regmap_field_alloc(dev, map, good_field);
```

**原因**：`regmap_field` 初始化时检查 `mask == 0` 并打印 `WARN_ONCE("invalid empty mask defined\n")`（`drivers/base/regmap/regmap.c:1314`）。空掩码意味着位域操作（读-改-写）不会改变任何位，但代码仍然会执行总线访问，浪费带宽且可能产生意外行为。

**源码依据**：`drivers/base/regmap/regmap.c:1314` — `WARN_ONCE(rm_field->mask == 0, "invalid empty mask defined\n")`。

### 陷阱 5：批量写跨越分页边界

**错误写法**：
```c
/* 设备有分页寄存器，窗口大小 256 字节 */
struct reg_sequence writes[] = {
    { .reg = 0x00, .def = 0x01 },
    { .reg = 0xFF, .def = 0x02 },
    { .reg = 0x100, .def = 0x03 },  /* 跨页！ */
};
regmap_multi_reg_write(map, writes, 3);
```

**正确写法**：
```c
/* 方法 1：确保批量写不跨页 */
struct reg_sequence writes[] = {
    { .reg = 0x00, .def = 0x01 },
    { .reg = 0xFF, .def = 0x02 },
};
regmap_multi_reg_write(map, writes, 2);
/* 单独写下一页 */
regmap_write(map, 0x100, 0x03);

/* 方法 2：使用 reg_sequence 的 delay_us 强制分页处理 */
```

**原因**：`_regmap_select_page()` 检查批量写是否跨越范围边界或单页边界，如果超出则返回 `-EINVAL`（`drivers/base/regmap/regmap.c:1645-1650`）。对于有分页机制的设备（通过 `struct regmap_config.ranges` 配置），单次批量写不能跨越窗口边界。驱动需要拆分写操作或在序列中插入延迟让 regmap 自动处理分页。

**源码依据**：`drivers/base/regmap/regmap.c:1645-1650` — 批量写边界检查。

---

## §6 取证摘要

**读取的文件**：
- `drivers/base/regmap/regmap.c` — 核心实现：写操作路径、缓存控制（cache_bypass/cache_only）、regmap_reinit_cache、regmap_field 初始化、批量写分页处理
- `include/linux/regmap.h` — struct regmap_config 字段定义、API 声明
- `drivers/base/regmap/regcache.c` — 缓存子系统（未深入阅读具体实现）

**关键论断出处**：
- "cache_only 模式下写操作不触及硬件" — 来源：`drivers/base/regmap/regmap.c:1735-1738`
- "cache_bypass 跳过缓存" — 来源：`drivers/base/regmap/regmap.c:1718`
- "regmap_reinit_cache 无锁保护" — 来源：`drivers/base/regmap/regmap.c:1511-1512`
- "regmap_field 空掩码 WARN_ONCE" — 来源：`drivers/base/regmap/regmap.c:1314`
- "批量写跨页返回 -EINVAL" — 来源：`drivers/base/regmap/regmap.c:1645-1650`
- "regmap_exit 释放异步链表" — 来源：`drivers/base/regmap/regmap.c:1559-1566`

**未验证项**：
- 三种缓存实现（FLAT/RBTREE/COMPRESSED）的具体性能特征未对比验证
- `regmap_async` 的完整完成机制（回调 vs 等待）未深入追踪
- `regmap_bulk_read` 在非 MMIO 总线上的对齐要求未找到明确的 WARN_ON 或检查
