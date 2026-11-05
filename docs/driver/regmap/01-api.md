---
title: API 参考
---

# regmap 01：代码地图与对外 API

本篇回答两件事：**代码在哪里**（§0）和**我该调哪个函数**（§1 之后）。

格式约定：
- 表格固定三列 `API / 说明 / 备注`；**签名只写函数名 + 形参名**，类型信息进「备注」
- **位置只精确到文件**，不逐 API 给行号（行号在 03 和 99 里维护）
- 标记重要度，⚠️ 标记陷阱

---

## §0 代码地图

### §0.1 基线：树是干净的

```bash
git status --porcelain -- drivers/base/regmap include/linux/regmap.h
# → 无输出
```

**无输出 ⇒ regmap 在这棵树里是未经改动的 6.1.158 上游。**

⚠️ **这一条必须每套单独查** ——同一棵树里 **timer 是改过的**
（3 处 PREEMPT_RT）而 of / virtio / regmap 都是干净的。

### §0.2 一句话定位

**regmap 把"读写设备寄存器"这件事从"每个驱动自己写一遍"抽成一层框架。**

**它解决的是"同一颗芯片可能挂在 I2C 上也可能挂在 SPI 上"**：
驱动只说"读 0x10 号寄存器"，regmap 负责翻译成具体总线上的字节序列。

**顺带解决了四件事**（00 §2 展开）：寄存器缓存、字节序、
易失/可缓存寄存器的区分、以及**把中断控制器也抽象掉**（regmap-irq）。

### §0.3 文件与职责

`drivers/base/regmap/` 共 **22 个文件、10998 行**（逐文件行数见 99 §1.2）。按职责分五组：

| 组 | 文件 | 职责 |
|---|---|---|
| **核心** | `regmap.c`(3529) | **格式化、锁、范围检查、读写主路径**——**47 个导出符号** |
| **缓存** | `regcache.c`(827) · `regcache-rbtree.c`(554) · `regcache-lzo.c`(368) · `regcache-flat.c`(83) | **三种缓存后端 + 一个调度层** |
| **中断** | `regmap-irq.c`(1319) | **把"寄存器里的中断状态位"变成一个 irq_chip**——**11 个导出符号** |
| **总线适配** | `regmap-i2c.c`(400) · `regmap-mmio.c`(636) · `regmap-spi.c`(167) · `regmap-spi-avmm.c`(713) · `regmap-spmi.c`(225) · `regmap-w1.c`(237) · `regmap-mdio.c`(116) · `regmap-sccb.c`(128) · `regmap-sdw.c`(89) · `regmap-sdw-mbq.c`(101) · `regmap-slimbus.c`(71) · `regmap-i3c.c`(60) · `regmap-ac97.c`(89) | **12 个文件，22 个 `regmap_bus` 实例**（§1.2） |
| **可观测性** | `regmap-debugfs.c`(692) · `trace.h`(284) | **debugfs + tracepoint 都有**（07 篇） |
| 内部 | `internal.h`(310) | 内部结构声明 |

⚠️ **`regmap.c` 一个文件占 32%**（3529/10998），**加上头文件是本子系统的绝对主体**。

**两个值得注意的规模**：
- `regmap-irq.c`(1319) **比所有缓存后端加起来还大** ——它其实是个独立子框架（04 篇）
- `regmap-spi-avmm.c`(713) **是最大的总线适配** ——一个很特殊的 Intel FPGA 协议

### §0.4 ⚠️ 头文件比核心还夸张：1977 行

```bash
wc -l include/linux/regmap.h    # → 1977
```

**`include/linux/regmap.h` 有 1977 行，接近 `regmap.c`(3529) 的六成。**

**为什么这么大**：
| 原因 | 说明 |
|---|---|
| **`struct regmap_config` 有 54 个字段** | **本子系统全部"可配置性"都塞在这一个结构里**（§2、02 §5.1） |
| **11 种总线各有 `init` + `devm_init` 两个声明** | 加上 `__` 前缀的底层实体，共 **14 个 `__regmap_init*` + 15 个 `__devm_regmap_init*`**（去重计）⇒ **29 个入口** |
| **大量 `static inline` 桩** | **`CONFIG_REGMAP=n` 时的空实现**（§7.3） |
| 一批便利宏 | `regmap_read_poll_timeout()` 之类 |

⇒ **读 regmap 一定要先读头文件**（skill 阶段 2 步骤 4 的通用建议在这里格外成立）。

### §0.5 导出符号：94 个

| 文件 | `EXPORT_SYMBOL*` |
|---|---|
| `regmap.c` | **47** |
| `regmap-irq.c` | **11** |
| `regcache.c` | **6** |
| `regmap-mmio.c` · `regmap-spmi.c` | 4 · 4 |
| `regmap-ac97.c` | 3 |
| `regmap-i2c.c` · `regmap-spi.c` · `regmap-w1.c` · `regmap-mdio.c` · `regmap-sccb.c` · `regmap-sdw.c` · `regmap-sdw-mbq.c` · `regmap-slimbus.c` · `regmap-spi-avmm.c` | **各 2** |
| `regmap-i3c.c` | 1 |
| `regcache-rbtree.c` · `regcache-lzo.c` · `regcache-flat.c` · `regmap-debugfs.c` | **全部 0** |
| **合计** | **94** |

复现：

```bash
echo "总计 → $(grep -hE '^EXPORT_SYMBOL' drivers/base/regmap/*.c | wc -l)"
```

**两条能直接读出来的结论**：
1. **三个缓存后端 0 导出** ⇒ **它们通过一张内部表被 `regcache.c` 选中**，
   不是给外部用的（04 篇）
2. **`regmap-debugfs.c`(692) 也是 0 导出** ⇒ **纯内部可观测性**，
   驱动不需要（也不能）主动调它

### §0.6 官方文档：⚠️ **没有概念文档，但有 DT 绑定**

**按四种变体全试**（99 §1.4 有完整命令）：

| 试法 | 结果 |
|---|---|
| `Documentation/regmap` / `regmaps` / `driver-api/regmap*` | **三个都不存在** |
| `ls Documentation/ \| grep -i regmap` | **空** |
| `ls Documentation/driver-api/ \| grep -i regmap` | **空** |
| `find Documentation -iname '*regmap*'` | **只有 `Documentation/devicetree/bindings/regmap/regmap.txt`** |

⇒ **概念文档：处置类别 (d)，真的没有**（和 virtio / cdev 同类）。
**regmap 的"设计意图"只能从代码注释和 kerneldoc 里读。**

⚠️ **但 DT 绑定是存在的**：

```bash
wc -l Documentation/devicetree/bindings/regmap/regmap.txt    # → 29
```

**29 行，只讲三个字节序属性**（`little-endian` / `big-endian` / `native-endian`）
⇒ **06 篇有真实内容可写**（06 §1 有逐条消费者）。

### §0.7 Kconfig

| 选项 | 作用 |
|---|---|
| `CONFIG_REGMAP` | **核心**；**由各总线适配 `select` 进来，不直接选** |
| `CONFIG_REGMAP_I2C` / `_SPI` / `_MMIO` / `_SPMI` / `_W1` / `_MDIO` / `_SCCB` / `_SOUNDWIRE` / `_SOUNDWIRE_MBQ` / `_SLIMBUS` / `_I3C` / `_AC97` / `_SPI_AVMM` | **一种总线一个** |
| `CONFIG_REGCACHE_COMPRESSED` | **LZO 缓存后端**（04 篇） |
| `CONFIG_REGMAP_IRQ` | **regmap-irq 子框架** |

**16 个 Kconfig 符号的完整表见 07 §1.1**（文件是 `drivers/base/regmap/Kconfig`）。

⚠️ **一个和别的子系统不同的地方**：**regmap 没有"用户可见"的 Kconfig 项** ——
全部由消费者 `select`。⇒ **所以 `.config` 里看到 `CONFIG_REGMAP_I2C=y`
说明"有某个驱动要用 I2C regmap"，不是"有人手工开了它"。**

---

## §1 先分清：regmap 的**三层**可插拔

**这是本子系统最要紧的一张表**：

| 层 | 抽象 | 谁实现 | 实现数 | 开放程度 |
|---|---|---|---|---|
| **1. 总线适配层** | `struct regmap_bus`（**18 个字段**，`include/linux/regmap.h:544-563`） | **总线适配 + 各驱动自定义** | **61 个** | **完全开放** ——39 个在 regmap 目录之外（§1.2） |
| **2. 缓存后端层** | `struct regcache_ops`（`drivers/base/regmap/internal.h:179-191`） | **只有 regmap 自己** | **3 个** | **完全封闭** ——0 个导出符号（§0.5） |
| **3. 锁策略层** | **两个函数指针** `regmap_lock` / `regmap_unlock`（`include/linux/regmap.h:227-228`） | **regmap 自己 7 种 + 调用者可自定义** | **7 内置 + 任意自定义** | **半开放**（§1.4） |

⚠️ **三层的开放程度完全不同**，这是本子系统一个很有个性的设计：

| 层 | 想加一个新实现要做什么 |
|---|---|
| 层 1（总线） | **在自己的驱动里填一个 `struct regmap_bus` 就行** ——常规操作 |
| 层 2（缓存） | **必须改 regmap 自己**（加进内部的后端表） |
| 层 3（锁） | **在 `regmap_config` 里填 `lock`/`unlock`/`lock_arg` 三个字段**（§2.6） |

### §1.4 层 3：7 种内置锁策略

**`struct regmap` 里的锁是两个函数指针**（`drivers/base/regmap/internal.h:62-63`），
**不是一把具体的锁** ⇒ 所以"用什么锁"是可插拔的。

**7 种内置实现**（全在 `drivers/base/regmap/regmap.c`）：

| 策略 | lock / unlock 的位置 | 什么时候选 |
|---|---|---|
| hwlock（普通） | `:448` / `:470` | `use_hwlock` 且不需要关中断 |
| hwlock + `_irq` | `:455` / `:477` | `use_hwlock` + `fast_io` |
| hwlock + `_irqsave` | `:462` / `:484` | `use_hwlock` + 需要保存中断状态 |
| **none** | `:491`（**同一个函数当 lock 也当 unlock**） | `disable_locking` |
| **mutex** | `:496` / `:502` | **默认** |
| **spinlock** | `:508` / `:518` | `fast_io`（MMIO 类） |
| **raw_spinlock** | `:525` / `:535` | `use_raw_spinlock` ——⚠️ **PREEMPT_RT 上才有实质差别** |

**选择逻辑在 `drivers/base/regmap/regmap.c:774-807`**，
优先级是：`use_hwlock` → `fast_io`（再分 raw 与否）→ 默认 mutex。

⚠️ **`regmap_lock_unlock_none()` 只有一个函数**（`:491`）——
**它同时被赋给 `lock` 和 `unlock`**，因为"什么都不做"两边一样。
**这是"函数指针化"带来的一个小便利。**

### §1.1 一个驱动看到的三层

```
  你的驱动
    │  regmap_read(map, 0x10, &val)          只说"读 0x10"
    ▼
  ┌──────────────────────────────────────────┐
  │  regmap 核心（regmap.c 3529 行）            │
  │  ① 范围检查（max_register / wr_table …）    │
  │  ② 查缓存（cache_type != NONE 时）          │
  │  ③ 格式化：寄存器号 + 值 → 字节序列          │
  │     （reg_bits / val_bits / pad_bits /     │
  │      *_format_endian / *_flag_mask）       │
  │  ④ 加锁（fast_io ? spinlock : mutex）      │
  └──────────────────┬───────────────────────┘
                     │ regmap_bus 的 read/write/gather_write
                     ▼
  ┌──────────────────────────────────────────┐
  │  总线适配（regmap-i2c.c / -spi.c / …）      │  ← 层 1，61 个实现
  └──────────────────┬───────────────────────┘
                     ▼
              i2c_transfer / spi_sync / readl …
```

**核心那四步就是 regmap 的全部价值**：
**驱动写"读 0x10"，核心把它变成"往 I2C 从地址 0x1a 发一个字节 0x10，再读两个字节，
高字节在前"** ——而这些细节全部来自 `struct regmap_config`（§2）。

### §1.2 61 个 `regmap_bus`：22 通用 + 39 自定义

```bash
grep -rn 'struct regmap_bus [a-z_0-9]* = {' --include=*.c . | wc -l            # → 61
grep -rn 'struct regmap_bus [a-z_0-9]* = {' drivers/base/regmap/*.c | wc -l    # → 22
```

⚠️ **两个不同口径要分清**（99 §6 记了这条）：

| 口径 | 值 |
|---|---|
| **`regmap_bus` 实例数（`drivers/base/regmap/` 内）** | **22** |
| **含实例的文件数（同目录）** | **13** |
| **全树实例总数** | **61** |
| **regmap 目录之外的自定义实例** | **39** |

**为什么通用适配有 22 个实例但只有 12 个总线**：**一种总线可能有多个变体**：

| 文件 | 实例数 | 变体 |
|---|---|---|
| `regmap-i2c.c` | **6** | `regmap_i2c` · `regmap_smbus_byte` · `regmap_smbus_word` · `regmap_smbus_word_swapped` · `regmap_i2c_smbus_i2c_block` · `regmap_i2c_smbus_i2c_block_reg16` |
| `regmap-w1.c` | **3** | 按"地址位宽 × 值位宽"分：`a8_v8` · `a8_v16` · `a16_v16` |
| `regmap-mdio.c` | 2 | `c22`（clause 22）· `c45`（clause 45） |
| `regmap-spmi.c` | 2 | `base` · `ext` |
| 其余 8 个文件 | 各 1 | — |

⇒ **`regmap-i2c.c` 那 6 个变体是本子系统"配置复杂性"的最好例子**：
**I2C/SMBus 有很多种传输形态，regmap 用"选哪个 `regmap_bus`"来表达**，
而不是在一个 bus 里加 if。

**39 个自定义实例的目录分布**（前几名）：

| 目录 | 文件数 |
|---|---|
| `drivers/mfd` | **8** |
| `drivers/rtc` · `drivers/iio/accel` | 3 · 3 |
| `drivers/media/dvb-frontends` · `drivers/iio/chemical` | 2 · 2 |
| `sound/soc/codecs` · `drivers/pinctrl` · `drivers/net/*`（4 个不同子目录） · `drivers/bus` · `drivers/clk` · `drivers/hwmon` · `drivers/bluetooth` · `drivers/iio/*` 等 | 各 1 |

⇒ **`drivers/mfd` 排第一很合理** ——MFD 芯片常有私有的寄存器访问协议。

### §1.3 按你要做的事跳转

| 我想… | 去 |
|---|---|
| 给我的驱动建一个 regmap | §2（配置）+ §3（初始化） |
| 读写寄存器 | §4 |
| 只操作寄存器里的几个位 | §5（`regmap_field`） |
| 用缓存（省总线流量 / 支持挂起恢复） | §6 |
| 把芯片的中断状态位变成 irq_chip | §7（regmap-irq） |
| 给一种新总线写适配 | §8 |
| 查错误码 | §9 |

---

## §2 `struct regmap_config`：54 个字段

`include/linux/regmap.h:370` 起。**它是本子系统的核心** ——
**所有"这颗芯片怎么访问"的知识都在这一个结构里。**

**54 个字段（按分号计，99 §6 有口径说明）分七组**：

### §2.1 必填的三个

| 字段 | 说明 | 备注 |
|---|---|---|
| `reg_bits` | **寄存器地址有多少位** | **必填**，没有默认值 |
| `val_bits` | **寄存器值有多少位** | **必填** |
| `max_register` | **最大合法寄存器号** | 严格说不是必填，⚠️ **但不填就没有范围检查**（§9.2） |

⚠️ **`reg_bits` + `val_bits` 决定了总线上的字节数**：
比如 `reg_bits=8, val_bits=16` 在 I2C 上就是"写 1 字节地址，读 2 字节值"。

### §2.2 地址变换四个

| 字段 | 说明 |
|---|---|
| `reg_stride` | **寄存器号的步长** ——MMIO 上常是 4（32 位寄存器按字节编址） |
| `reg_downshift` | 读到的地址先右移几位 |
| `reg_base` | 所有地址加一个基址 |
| `pad_bits` | **地址和值之间的填充位** ——某些 SPI 芯片需要 |

⚠️ **`reg_stride` 是 MMIO 上最容易忘的一个**：
**不填（=0，核心当 1 用）会让"寄存器 1"访问到字节偏移 1 而不是 4**（05 §8）。

### §2.3 六张寄存器表（范围与属性）

| 字段 | 说明 |
|---|---|
| `wr_table` / `rd_table` | **哪些寄存器可写 / 可读** |
| `volatile_table` | **哪些寄存器是易失的（不能缓存）** |
| `precious_table` | **哪些寄存器"读一次就变"** ——⚠️ **debugfs 不会读它们**（07 篇） |
| `wr_noinc_table` / `rd_noinc_table` | 哪些寄存器不支持地址自增批量访问 |

**还有四个等价的回调形式**：`writeable_reg` / `readable_reg` /
`volatile_reg` / `precious_reg` ——**表和回调二选一**，
表适合稀疏范围，回调适合能用公式表达的。

⚠️ **`precious` 和 `volatile` 是两件不同的事**（05 §7 有独立一节）：
**`volatile` = "不能缓存"；`precious` = "读它有副作用"。**

### §2.4 缓存三个

| 字段 | 说明 |
|---|---|
| `cache_type` | **`REGCACHE_NONE` / `_RBTREE` / `_FLAT` / `_COMPRESSED`** 四选一（04 篇） |
| `reg_defaults` / `num_reg_defaults` | **上电默认值表**（`reg`/`def` 对） |
| `reg_defaults_raw` / `num_reg_defaults_raw` | 原始字节形式的默认值 |

⚠️ **`cache_type != REGCACHE_NONE` 时几乎一定要给 `reg_defaults`** ——
否则缓存"不知道芯片上电后是什么值"（06 §… 见 04 篇）。

### §2.5 格式与字节序四个

| 字段 | 说明 |
|---|---|
| `reg_format_endian` / `val_format_endian` | **地址/值在总线上的字节序** ——**DT 的三个属性影响的就是这里**（06 §1） |
| `read_flag_mask` / `write_flag_mask` | **地址字节上要 OR 的读/写标志** ——**绝大多数 SPI 芯片需要** |
| `zero_flag_mask` | 显式声明"标志掩码就是 0" |

⚠️ **`read_flag_mask` 是 SPI 驱动最常忘的一个**：
**很多 SPI 芯片用地址字节的最高位区分读写**（如读时置 0x80）
⇒ 不设就会一直读到垃圾（05 §8）。

**`zero_flag_mask` 存在的原因很有意思**：因为"0"和"没填"在 C 里一样，
**需要一个显式字段来表达"我确实要 0"**（某些总线适配会给非零默认值）。

### §2.6 锁与并发五个

| 字段 | 说明 |
|---|---|
| `fast_io` | **用 spinlock 而不是 mutex** ——**MMIO 类必须开**（03 §B） |
| `disable_locking` | **完全不加锁**（调用者自己保证） |
| `lock` / `unlock` / `lock_arg` | **自定义锁函数** |
| `use_hwlock` / `hwlock_id` / `hwlock_mode` | 用硬件自旋锁（多核/多处理器共享寄存器） |
| `use_raw_spinlock` | **用 `raw_spinlock_t`** ——⚠️ **PREEMPT_RT 上有实质差别**（03 §B） |
| `can_sleep` | 访问路径可以睡 |

⚠️ **`use_raw_spinlock` 在这棵树上格外重要**：
**本树可开 `CONFIG_PREEMPT_RT`**（timer 那套 99 §1.1 确认过），
**RT 上普通 `spinlock_t` 变成可睡的** ⇒
**在硬中断上下文访问的 regmap 必须用 `use_raw_spinlock`**（05 §1.4）。

### §2.7 其余（性能与能力声明）

| 字段 | 说明 |
|---|---|
| `max_raw_read` / `max_raw_write` | **单次原始传输的字节上限** |
| `use_single_read` / `use_single_write` | **禁止批量** ——芯片不支持地址自增时用 |
| `can_multi_write` | 支持一次写多个不连续寄存器 |
| `use_relaxed_mmio` | MMIO 用 `readl_relaxed` |
| `io_port` | 是 I/O port 而不是内存映射 |
| `ranges` / `num_ranges` | **分页寄存器**（`regmap_range_cfg`）——04 篇 |
| `name` | debugfs 目录名（07 篇） |

---

## §3 初始化：11 种总线 × 2 个变体

### §3.1 命名规律

**每种总线都有两个入口**：

| 形式 | 说明 |
|---|---|
| `regmap_init_<bus>(dev, ..., config)` | **要自己 `regmap_exit()`** |
| `devm_regmap_init_<bus>(dev, ..., config)` | **跟着 device 自动释放** ——**绝大多数驱动用这个** |

**11 种总线**（`include/linux/regmap.h` 里各有一对声明）：

| 总线 | 入口 |
|---|---|
| I2C | `devm_regmap_init_i2c()` |
| SPI | `devm_regmap_init_spi()` |
| MMIO | `devm_regmap_init_mmio()` / `devm_regmap_init_mmio_clk()` |
| SPMI | `devm_regmap_init_spmi_base()` / `_spmi_ext()` |
| MDIO | `devm_regmap_init_mdio()` |
| SCCB | `devm_regmap_init_sccb()` |
| SoundWire | `devm_regmap_init_sdw()` / `_sdw_mbq()` |
| SLIMbus | `devm_regmap_init_slimbus()` |
| I3C | ⚠️ **只有 `devm_regmap_init_i3c()`** ——**没有非 devm 版**（§3.1 的不对称） |
| AC97 | `devm_regmap_init_ac97()` |
| SPI-AVMM | `devm_regmap_init_spi_avmm()` |
| **自定义** | `devm_regmap_init(dev, bus, bus_context, config)` ——**自己给 `regmap_bus`**（§8） |

⚠️ **这些"函数"其实都是宏**：真实符号是 `__regmap_init_<bus>()` 和
`__devm_regmap_init_<bus>()`（**14 个 + 15 个**，去重计），
**宏多包一层是为了传 lockdep 的 key**（03 §B）。
⇒ **所以在索引工具里搜 `devm_regmap_init_i2c` 找不到定义**（99 §4）。

⚠️ **14 vs 15 这个不对称有一处实据**：**`__regmap_init_i3c` 不存在，
只有 `__devm_regmap_init_i3c`** ⇒ **I3C 只提供 devm 版本**。
复现：`grep -oE '\b__(devm_)?regmap_init[a-z_0-9]*\(' include/linux/regmap.h | sort -u`
⇒ **这类"某种总线少一个变体"的不对称在 01 §3.1 的表里一定要标出来**，
否则读者照着别的总线的写法去找非 devm 版会找不到。

### §3.2 生命周期与附属操作

| API | 说明 | 备注 |
|---|---|---|
| `regmap_exit(map)` | 释放 | **只有非 devm 版才需要** |
| `regmap_attach_dev(dev, map, config)` | **事后把 regmap 关联到一个 device** | 建 regmap 时还没有 device 的场景 |
| `regmap_get_device(map)` | 反查 device | — |
| `dev_get_regmap(dev, name)` | **按名字从 device 上取 regmap** | **MFD 子设备取父设备的 regmap 靠它** |

### §3.3 查询能力

| API | 说明 |
|---|---|
| `regmap_get_val_bytes(map)` | 一个值占几字节 |
| `regmap_get_max_register(map)` | `max_register` 的值 |
| `regmap_get_reg_stride(map)` | `reg_stride` 的值 |
| `regmap_get_raw_read_max(map)` / `_raw_write_max()` | 单次原始传输上限 |
| `regmap_can_raw_write(map)` | 支持 raw 写吗 |
| `regmap_check_range_table(map, reg, table)` | 手工查一张范围表 |

---

## §4 读写寄存器（用得最多的一组）

### §4.1 基本读写

| API | 说明 | 备注 |
|---|---|---|
| `regmap_read(map, reg, val)` | **读一个寄存器** | `val` 是 `unsigned int *` |
| `regmap_write(map, reg, val)` | **写一个寄存器** | — |
| `regmap_update_bits(map, reg, mask, val)` | **读-改-写** | **最常用的一个**；值没变时**不写** |
| `regmap_set_bits(map, reg, bits)` | 置位 | 是 `update_bits(map, reg, bits, bits)` |
| `regmap_clear_bits(map, reg, bits)` | 清位 | 是 `update_bits(map, reg, bits, 0)` |
| `regmap_test_bits(map, reg, bits)` | **测位** | 返回 1/0，**负数是错误** |
| `regmap_write_bits(map, reg, mask, val)` | **不管有没有变都写** | 对比 `update_bits` |
| `regmap_update_bits_base(...)` | 底层，带 `change` / `async` / `force` 三个开关 | 上面那几个都是它的包装 |

⚠️ **`regmap_update_bits()` 值没变就不写** ——
**对"写同样的值也有副作用"的寄存器要用 `regmap_write_bits()`**（05 §7）。

### §4.2 批量与原始

| API | 说明 | 备注 |
|---|---|---|
| `regmap_bulk_read(map, reg, val, val_count)` | **读连续多个寄存器** | **`val` 按 `val_bits` 对齐的数组**，核心做字节序转换 |
| `regmap_bulk_write(map, reg, val, val_count)` | 写连续多个 | — |
| `regmap_raw_read(map, reg, val, val_len)` | **不做字节序转换的原始读** | ⚠️ **`val_len` 是字节数不是寄存器数** |
| `regmap_raw_write(map, reg, val, val_len)` | 原始写 | — |
| `regmap_noinc_read(map, reg, val, val_len)` | **反复读同一个寄存器**（FIFO） | **地址不自增** |
| `regmap_noinc_write(map, reg, val, val_len)` | 同上，写方向 | — |
| `regmap_multi_reg_write(map, regs, num_regs)` | **写一批不连续的寄存器** | 要 `can_multi_write` |
| `regmap_multi_reg_write_bypassed(...)` | 同上但绕过缓存 | — |

⚠️ **`bulk` 和 `raw` 的区别是本子系统一组高危近邻**（05 §7）：
**`bulk` 的第 4 参数是"几个寄存器"，`raw` 的是"几个字节"。**
⇒ **搞混会读到长度不对的数据，而且不一定报错。**

### §4.3 轮询

| API | 说明 |
|---|---|
| `regmap_read_poll_timeout(map, addr, val, cond, sleep_us, timeout_us)` | **轮询直到条件成立** ——**宏** |
| `regmap_read_poll_timeout_atomic(...)` | **原子上下文版本**（用 `udelay`） |
| `regmap_field_read_poll_timeout(...)` | field 版本 |

**返回 `-ETIMEDOUT` 表示超时。**
⚠️ **它们是宏，`cond` 里可以直接用 `val`。**

### §4.4 异步

| API | 说明 |
|---|---|
| `regmap_async_complete(map)` | **等所有异步写完成** |
| `regmap_async_complete_cb(map, cb, data)` | 带回调 |

**异步写通过 `regmap_update_bits_base()` 的 `async` 参数或
`regmap_raw_write_async()` 发起** ⇒ **只有少数总线支持**（04 篇）。

---

## §5 `regmap_field`：把"寄存器里的几个位"变成一个对象

**动机**：芯片手册常说"寄存器 0x10 的第 3–5 位是增益"。
**每次都写 `regmap_update_bits(map, 0x10, GENMASK(5,3), gain << 3)` 很容易错。**

**`regmap_field` 把 `{reg, lsb, msb}` 打包**，之后只说"写这个 field = 3"。

| API | 说明 | 备注 |
|---|---|---|
| `devm_regmap_field_alloc(dev, map, reg_field)` | **分配一个 field** | `reg_field` 是 `struct reg_field`（02 §5） |
| `regmap_field_read(field, val)` | 读 | **值已经右移到位** |
| `regmap_field_write(field, val)` | 写 | **核心负责左移和掩码** |
| `regmap_field_update_bits(field, mask, val)` | 改位 | — |
| `regmap_field_set_bits()` / `_clear_bits()` / `_test_bits()` | 位操作 | 语义同 §4.1 |
| `regmap_field_force_write()` / `_force_update_bits()` | **强制写**（不管有没有变） | — |
| `devm_regmap_field_bulk_alloc(dev, map, fields, reg_fields, num)` | **一次分配一批 field** | **驱动通常有十几个 field** |
| `regmap_field_free()` / `_bulk_free()` | 非 devm 版的释放 | — |
| `regmap_field_alloc()` / `regmap_field_bulk_alloc()` | 非 devm 版 | — |

**还有一组"多路 field"**（`regmap_fields_*`，注意是复数）：

| API | 说明 |
|---|---|
| `regmap_fields_read(field, id, val)` / `regmap_fields_write(field, id, val)` | **同一个 field 定义 + 一个 id 索引到不同寄存器** |
| `regmap_fields_update_bits()` / `_force_write()` / `_force_update_bits()` / `_update_bits_base()` | 同上系列 |

**`regmap_fields_*` 解决的是"同一个字段在 N 个通道的寄存器里重复出现"** ——
`struct reg_field` 里的 `id_offset` / `id_size` 描述那个规律（02 §5）。

⚠️ **`regmap_field_*`（单数）和 `regmap_fields_*`（复数）只差一个 s** ——
**这是本子系统最阴的一组近邻符号**（05 §7）。

---

## §6 regcache：缓存

| API | 说明 | 备注 |
|---|---|---|
| `regcache_sync(map)` | **把缓存里的脏值全部写回芯片** | **resume 路径的核心调用** |
| `regcache_sync_region(map, min, max)` | 只同步一段 | — |
| `regcache_mark_dirty(map)` | **把整个缓存标脏** | **suspend 里芯片掉电了就要调它** |
| `regcache_cache_only(map, enable)` | **只读写缓存，不碰总线** | **芯片掉电期间用** |
| `regcache_cache_bypass(map, enable)` | **绕过缓存直接访问** | **要读真实硬件值时用** |
| `regcache_drop_region(map, min, max)` | 丢弃一段缓存 | — |

**典型的电源管理序列**（05 §3 有骨架）：

```
  suspend:  regcache_cache_only(map, true)  →  regcache_mark_dirty(map)
  resume:   regcache_cache_only(map, false) →  regcache_sync(map)
```

⚠️ **`cache_only` 和 `cache_bypass` 是一组高危近邻**（05 §7）：
**一个是"只用缓存"，一个是"不用缓存"** ——名字都带 cache，方向相反。

⚠️ **`regcache_sync()` 只写"脏"的寄存器** ⇒
**芯片真的掉过电就必须先 `regcache_mark_dirty()`**，
否则 sync 认为"缓存和硬件一致"，什么都不写。

---

## §7 regmap-irq：把寄存器位变成 irq_chip

**`regmap-irq.c` 1319 行、11 个导出符号，是 regmap 里最大的子框架**（04 篇）。

**它解决的问题**：很多芯片的中断是"读一个状态寄存器，看哪些位置了"。
**regmap-irq 把这套东西包成一个标准的 `irq_chip`**，
于是消费者可以用普通的 `request_irq()`。

| API | 说明 | 备注 |
|---|---|---|
| `devm_regmap_add_irq_chip(dev, map, irq, irq_flags, irq_base, chip, data)` | **注册** | **`chip` 是 `struct regmap_irq_chip`**（02 §5） |
| `devm_regmap_add_irq_chip_fwnode(...)` | **带 fwnode 的版本** | DT/ACPI 场景 |
| `regmap_add_irq_chip()` / `_fwnode()` | 非 devm 版 | — |
| `regmap_del_irq_chip()` / `devm_regmap_del_irq_chip()` | 注销 | — |
| `regmap_irq_chip_get_base(data)` | **拿到分配到的 irq base** | 子设备要用它算自己的 irq 号 |
| `regmap_irq_get_virq(data, irq)` | **把芯片内的中断号换成 Linux virq** | **子设备 `request_irq()` 用它** |
| `regmap_irq_get_domain(data)` | 拿 irq_domain | — |

⚠️ **`regmap_irq_chip_get_base()` 和 `regmap_irq_get_virq()` 的区别**：
**前者给"基号"，后者给"某一个中断的 virq"** ——
**新代码应该用后者**（前者假设 irq 号连续）。

### §7.3 ⚠️`CONFIG_REGMAP=n` 时的空实现

**`include/linux/regmap.h` 里有一大批 `static inline` 桩**，
在 `CONFIG_REGMAP=n` 时提供空实现。

⚠️ **它们的行为是"`WARN_ONCE` 然后返回 `-EINVAL`"** ——
**不是编译失败** ⇒ **如果你的驱动忘了 `select REGMAP_I2C`，
它能编过但运行时全是 `-EINVAL`**（05 §8、07 篇）。

---

## §8 给一种新总线写适配

**只有"你的芯片有私有的寄存器访问协议"时才需要**（39 个自定义实例，§1.2）。

**要填 `struct regmap_bus`**（02 §5）的三组回调之一：

| 组 | 回调 | 用于 |
|---|---|---|
| **字节流式** | `write` / `read` / `gather_write` | **总线上传的是"地址字节 + 值字节"** ——I2C/SPI 都是这类 |
| **寄存器式** | `reg_write` / `reg_read` / `reg_update_bits` | **总线本身就有"寄存器号"概念** ——MMIO 是这类 |
| 混合/其它 | `max_raw_read` / `max_raw_write` / `free_context` 等 | 能力声明 |

⚠️ **两组回调二选一，不能都填** ——
**核心按"有没有 `reg_read`"决定走哪条路**（03 §A）。

**完整骨架见 05 §5。**

---

## §9 错误码语义

### §9.1 全量普查

统计范围：`drivers/base/regmap/` 下 **20 个 `.c`**（显式列文件，命令见 99 §3.3）。

| 错误码 | 次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | **102** | **占绝对多数** ——三种来源合一（§9.2） |
| `-ENOMEM` | **49** | 缓存/缓冲区分配失败 |
| `-ENOTSUPP` | **16** | **这个 regmap 不支持该操作** |
| `-EFAULT` | **12** | **几乎全在 `regmap-debugfs.c`** ——`copy_to_user()` 失败 |
| `-ENODEV` | **8** | 设备/总线不存在 |
| ⚠️`-EIO` | **7** | ⚠️ **三类来源，5 处在 I2C 适配层**（§9.2） |
| `-EBUSY` | 2 | 缓存正在同步 |
| `-EOPNOTSUPP` | 2 | 和 `-ENOTSUPP` 并存（§9.3） |
| `-ETIMEDOUT` | 1 | — |
| `-EPERM` · `-EAGAIN` · `-ERANGE` · `-ENOSPC` | **全部 0** | **本子系统不返回这四个** |

复现：

```bash
CORE=$(ls drivers/base/regmap/*.c | tr '\n' ' ')
for e in EINVAL ENOMEM ENOTSUPP EFAULT ENODEV EIO EBUSY EOPNOTSUPP ETIMEDOUT \
         EPERM EAGAIN ERANGE ENOSPC; do
  printf '%-14s %s\n' "-$e" "$(grep -how -- "-$e" $CORE | wc -l)"
done
```

⚠️ **`-EINVAL` 占 102/198 ≈ 52%** ——**这是本子系统最大的诊断障碍**：
一半的错误都是同一个码。§9.2 把它的三种来源拆开。

### §9.2 ⚠️ 两个"一码多因"的陷阱

#### `-EINVAL` 的三种来源

| 来源 | 判据 |
|---|---|
| ① **寄存器号没按 `reg_stride` 对齐** | `regmap_write()` 开头的 `if (!IS_ALIGNED(reg, map->reg_stride)) return -EINVAL;` |
| ② **批量访问里有不可写/不支持 noinc 的寄存器** | `drivers/base/regmap/regmap.c:1714` |
| ⚠️ ③ **`CONFIG_REGMAP=n` 的空桩** | **`WARN_ONCE` + `-EINVAL`**（§7.3）⇒ **看 `dmesg` 有没有那条 `WARN`** |

⇒ **第 ③ 种最容易误诊**：你会以为寄存器号写错了，
**实际是驱动的 Kconfig 忘了 `select REGMAP_I2C`**（05 §8）。

#### ⚠️`-EIO` 的三类来源，七处全在这里（本篇最该记住的一条）

**`-EIO` 在全子系统只有 7 处，可以逐个列完**：

```bash
grep -n -- '-EIO' drivers/base/regmap/*.c
```

| 类 | 处数 | 位置 | 含义 |
|---|---|---|---|
| ① **I2C 传输异常** | **5** | `drivers/base/regmap/regmap-i2c.c:135` · `:169` · `:197` · `:239` · `:296` | **传输返回的字节数不对** ——真正的"I/O 错误" |
| ⚠️ ② **写了不可写的寄存器** | **1** | `drivers/base/regmap/regmap.c:1995` | `if (!regmap_writeable(map, reg)) return -EIO;` |
| ⚠️ ③ **读了不可读的寄存器** | **1** | `drivers/base/regmap/regmap.c:2900` | `if (!regmap_readable(map, reg)) return -EIO;`（在 `_regmap_read()` 里，`:2884` 起） |

**`regmap_writeable()`（`drivers/base/regmap/regmap.c:90-102`）在三种情况下返回假**：
① **`max_register` 非 0 且 `reg > max_register`**；
② `writeable_reg()` 回调说不行；
③ `wr_table` 说不行。
**`regmap_readable()` 对称。**

⇒ ⚠️ **所以"寄存器号超出 `max_register`"这个纯配置错误，
表现出来是 `-EIO`（一个听起来像硬件故障的码）。**
**拿到 `-EIO` 先别怀疑硬件** ——
**判据：只有 I2C regmap 才可能是真的 I/O 错误**（类 ①），
**别的总线上的 `-EIO` 一定是类 ②③（配置问题）。**

### §9.3 ⚠️`-ENOTSUPP`(16) 和 `-EOPNOTSUPP`(2) 并存

**两个语义几乎相同的码在同一个子系统里并用**，而且数量悬殊（16 : 2）。

⚠️ **`-ENOTSUPP` 是 Linux 内部码（524），不该返回给用户态**；
`-EOPNOTSUPP` 才是 POSIX 的。
⇒ **regmap 主要用内部码是合理的**（它是内核内部框架），
但两者并存说明**没有统一约定**（03 Part C 会作为设计评价的一项）。

⚠️ **子串陷阱**：`grep -o '\-ENOTSUPP'` **不带 `-w` 会被 `-EOPNOTSUPP` 命中**
⇒ **§9.1 的命令必须带 `-w`**（99 §6 记了这条口径）。

### §9.4 驱动作者速查

| 拿到 | 先查 |
|---|---|
| ⚠️`-EIO` | **非 I2C 总线上一定是配置问题** ——先查 `max_register` / `wr_table` / `rd_table`（§9.2） |
| `-EINVAL` | **① `reg_stride` 对齐 ② `dmesg` 有没有 `WARN_ONCE`（Kconfig 没开）** |
| `-ENOTSUPP` | **这个 `regmap_bus` 没实现你要的操作**（如原子改位） |
| `-ETIMEDOUT` | `regmap_read_poll_timeout()` 超时（§4.3） |
| `-ENOMEM` | 缓存分配失败 ——检查 `max_register` 是不是填得过大（`REGCACHE_FLAT` 会按它分配整块） |
| **读到的值全是 0 或 0xff，但不报错** | ⚠️ **不是错误码问题** ——查 `read_flag_mask`（§2.5）和 `reg_bits`/`val_bits` |
