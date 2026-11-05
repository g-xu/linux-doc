---
title: 总览
---

# regmap 00：总览（复习入口）

> **这一篇的定位是"复习入口"**：隔几个月回来，只读这一篇就能恢复对 regmap 的全貌认识。
> 它是对 01–08 篇的**浓缩**，不引入任何新论断。
> **基线**：`linux-6.1.158`（`git log -1` = `f6e38ae624cf`），
> **arm64-only**，**`drivers/base/regmap/` 与 `include/linux/regmap.h` 均未被本树补丁修改**（99 §1）。

---

## §1 定位：它在内核里是什么

**一句话**：**regmap 是"寄存器访问"的通用抽象层** ——
**驱动说"给第 0x10 号寄存器写 0x1"，regmap 负责把这句话变成一次 MMIO 写、一次 I2C 传输，
或者一串 SPI 字节。**

| 维度 | 结论 | 依据 |
|---|---|---|
| **子系统类型** | **框架型**（不是总线型） | **门 1（`struct bus_type`）无输出 + 门 2（`include/uapi/linux/regmap*`）0 文件**（99 §1 / 08 §3） |
| 代码位置 | `drivers/base/regmap/`（**22 文件 / 10998 行**）+ `include/linux/regmap.h`（**1977 行**） | 01 §0 |
| 对外符号 | **94 个导出符号** | 01 §0.5 |
| **可插拔层数** | **三层**：总线（61 实现，完全开放）· 缓存（3 实现，完全封闭）· 锁（7 内置 + 可自定义） | 01 §1 / 02 §1.0.F |
| **复杂度在哪** | ⚠️ **不在代码里，在配置里** ——`struct regmap_config` 一个结构 **54 个字段** | 03 §C5 / 01 §2 |
| 官方概念文档 | ⚠️ **没有**（四种路径全试过）；**但 DT 绑定存在**（`Documentation/devicetree/bindings/regmap/regmap.txt`，29 行） | 99 §1 / 06 §0 |
| 可观测性 | **好**：debugfs 692 行 + 17 个 tracepoint | 07 §0 / 03 §C4 |

**它不做什么**（同样重要）：

| 不做的事 | 归谁 |
|---|---|
| **不管设备枚举、不管驱动绑定** | **驱动模型 + 各总线子系统**（regmap 没有 `bus_type`） |
| **不定义寄存器语义** | **芯片驱动**（通过 54 个字段的 `regmap_config` 描述） |
| **不提供电源管理策略** | **驱动的 `suspend`/`resume`**（regmap 只提供缓存工具，05 §6） |
| **不注入故障** | **没有任何注入设施**（07 §6） |

---

## §2 背景：这个抽象在解决什么问题

**本节走"问题背景"路径**（框架型子系统适用）：**先说没有 regmap 时驱动长什么样，
再说 regmap 消掉了哪些重复，最后说它为此付出了什么代价。**

### §2.1 问题的起点：同一颗芯片、两条总线

**硬件世界的一个常见事实**：**同一颗外设芯片经常有多种总线接口的型号。**
一颗 audio codec，A 板用 I2C 接，B 板用 SPI 接；
一颗 PMIC，某些封装走 I2C，某些走 SPMI。
**寄存器布局、位定义、上电时序完全一样，只有"字节怎么送过去"不一样。**

**没有抽象层时，驱动作者的选择只有两个，两个都不好**：

| 做法 | 后果 |
|---|---|
| **写两份驱动** | **寄存器定义、状态机、电源序列全部重复一遍**；修一个 bug 要改两处，而且一定会漏 |
| **写一份驱动 + 自己抽一层** | **每个驱动都发明一次同样的抽象**；树里会出现几十套互不兼容的"寄存器访问层" |

⇒ **regmap 就是把第二种做法提取成公共设施。**

### §2.2 第二个问题：慢总线上的重复读写

**I2C 上读一个寄存器要几十到几百微秒。**
**一颗 codec 上电时可能要配置上百个寄存器，很多值在整个运行期间不会变。**

**驱动作者自然会想"我在内存里记一份，避免重复读"** ——
**但这件事一做就会踩三个坑**：

| 坑 | 表现 |
|---|---|
| **哪些能缓存？** | **状态寄存器缓存住 ⇒ 永远读到旧值** |
| **哪些不能主动读？** | **有些寄存器读一下就清零** ⇒ 调试时打印一遍就破坏了状态 |
| **掉电后怎么恢复？** | **suspend/resume 要把整份配置重放一次，顺序还不能错** |

⇒ **regmap 把缓存做成内置能力（三个后端），但把"哪些能缓存"的判断交回给驱动**
（`volatile_reg` / `precious_reg` 两个回调）——**因为只有芯片手册的读者知道答案**（08 §5）。

### §2.3 第三个问题：分页寄存器

**有些芯片的寄存器数量远超地址位宽能表达的范围。**
**硬件的解法是"翻页"**：先往一个选择器寄存器写页号，
**然后固定的那个小窗口就代表那一页的内容。**

**驱动作者手写这件事，每次访问都要**：算页号 → 写选择器 → 算窗口内偏移 → 访问 → （可能还要恢复页号）。
⇒ **regmap 把这四步收进核心，驱动只写"逻辑地址"**（08 §8）。

### §2.4 第四个问题：PMIC 的"寄存器位就是中断"

**PMIC / codec 上常见的形态**：**芯片只有一根中断引脚，
但内部有几十个中断源，靠一组状态寄存器的位来区分。**

**每个 PMIC 驱动都要写一遍**：读状态寄存器 → 逐位判断 → 分发 → 写 ack 寄存器 → 处理屏蔽。
⇒ **regmap-irq（1319 行）把这套变成"填一张表"**（04 §1）。

### §2.5 于是 regmap 长成了现在这样

**四个问题对应四块能力，加上一个贯穿的"锁"：**

| 问题（§2.x） | regmap 的答案 | 规模 | 在哪篇 |
|---|---|---|---|
| §2.1 总线无关 | **层 1 `regmap_bus`** | **61 个实例**（12 种通用适配 / 22 实例在目录内，39 个在目录外） | 04 §6 |
| §2.2 缓存 | **层 2 `regcache_ops`** | **3 个后端，0 个导出符号（完全封闭）** | 04 §2 |
| §2.3 分页 | **`regmap_config.ranges`** | **不可插拔，核心固定实现** | 04 §3 |
| §2.4 位变中断 | **regmap-irq** | **1319 行 / 11 个导出符号** | 04 §1 |
| 贯穿 | **层 3 锁策略** | **7 种内置 + 可自定义** | 01 §1.4 / 03 §B1 |

### §2.6 ⚠️ 它为此付出的代价（读 regmap 一定要知道的四条）

**这四条是本套文档反复出现的主线**，也是 regmap 最常被骂的地方。

#### 代价 1：复杂度全部搬进了配置

**`struct regmap_config` 有 54 个字段**（按分号计，`include/linux/regmap.h:370` 起）。
⇒ **regmap 的代码不难读，难的是"这 54 个字段每个填什么"。**
⇒ **读 regmap 的正确顺序是"先逐字段读 `regmap_config`"（01 §2），不是先读 `regmap.c`**（03 §C5）。

#### ⚠️ 代价 2：大量"配错了不报错"

**抽象层的通病**：**它必须容忍不填的字段，于是"不填"和"填 0"无法区分。**

| 不填 | 后果 | 有提示吗 |
|---|---|---|
| `max_register` | **不检查上界** | **无** |
| `volatile_reg`（且开了缓存） | **状态寄存器读不到新值** | **无** |
| `reg_defaults` | **`regcache_sync` 少回灌** | **有一条 `dev_warn`**（07 §4.1） |
| DTS 里的 `big-endian` | ⚠️ **五种情形下彻底无效** | **无**（06 §4 决策树） |

#### ⚠️ 代价 3：一个错误码对应多个原因

| 码 | 原因数 | 最坑的那个 |
|---|---|---|
| `-EINVAL`（**102 处，占 52%**） | **三类** | **`CONFIG_REGMAP=n` 的空桩也返回它**（头文件里 45 处 `WARN_ONCE`） |
| ⚠️`-EIO`（**7 处**） | **两类** | **"写不可写寄存器"返回一个像硬件故障的码**（`drivers/base/regmap/regmap.c:1994-1995`） |

完整分诊见 01 §9 和 07 §5。

#### 代价 4：位宽组合是硬编码的

**`__regmap_init()` 里用一串 `switch (config->reg_bits + map->reg_shift)` 挑格式化函数**
（03 §A3）⇒ **不在表里的位宽组合直接 `-EINVAL`，加一种要改核心。**
**这是本子系统最大的设计约束**（03 §C2），也是 `reg-io-width = <8>` 在本版本必然失败的原因（06 §3.4）。

### §2.7 本文档裁掉了什么（别当成遗漏）

| 裁掉的 | 为什么 |
|---|---|
| **ACPI 路径** | **arm64-only 约定**；**代码是共用的**（`fwnode_property_read_bool`，06 §2.1 消费点 1/2/3） |
| **红黑树 / LZO 缓存的内部算法** | **通用数据结构问题**，不是 regmap 特有；只讲"怎么选"（04 §2.2） |
| **regmap-irq 的中断分发主循环** | **本质是一个 `irq_chip` 实现**，属 irq 子系统知识；只读了结构和 11 个导出符号 |
| **SPI-AVMM 协议细节** | **Intel FPGA 专属**，arm64 通用场景不碰（04 §7） |
| **12 个适配里 10 个的实现** | **只详读了 `regmap-mmio.c` 和 `regmap-i2c.c` 的关键部分**（99 §2.2） |
| **AC97 / SLIMbus / SoundWire / W1 / SPMI / MDIO / SCCB / I3C 这些总线本身** | 各属自己的子系统 |
| **PREEMPT_RT 上的 `use_raw_spinlock` 实测** | **未在 RT 内核上验证**（99 §5 第三层 / 08 §9） |

---

## §3 概念模型：五个词就能讲完

**记住这五个词和它们的关系，regmap 的骨架就有了。**

| 词 | 是什么 | 一句话 |
|---|---|---|
| **`regmap_config`** | **静态描述**（54 字段） | **"这块寄存器长什么样"** ——芯片驱动作者写，`static const` |
| **`regmap_bus`** | **一组函数指针**（18 字段） | **"字节怎么搬过去"** ——总线适配作者写 |
| **`struct regmap`** | **运行期实例**（私有，在 `internal.h`） | **config + bus + 缓存 + 锁 + 分页树，缝在一起的结果** |
| **`regmap_field`** | **位域句柄** | **"寄存器里的第 4–6 位"这件事的对象化** |
| **`regmap_irq_chip`** | **另一张静态描述表**（40 字段） | **"哪些寄存器位是哪些中断"** |

**三层可插拔的形状（02 §1.0.F 的浓缩）**：

```
   消费者驱动（pinctrl / clk / codec / PMIC …）
              │  regmap_read() / regmap_write() / regmap_update_bits()
              ▼
   ┌────────────────── struct regmap（核心）──────────────────┐
   │                                                          │
   │  层 3：锁          层 2：缓存           格式化（不可插拔） │
   │  7 种内置          3 个后端             按位宽算出来的     │
   │  可自定义          完全封闭             switch 硬编码      │
   │                                                          │
   └──────────────────────── 层 1：regmap_bus ────────────────┘
                     61 个实现，完全开放
                              │
                              ▼
              MMIO / I2C / SPI / SPMI / SoundWire / …
```

⚠️ **三个必须记住的"不是"**：

1. **三层是并列的，不是叠起来的**（02 §1.0）：**总线管"怎么发"、缓存管"要不要发"、锁管"谁能发"。**
2. **格式化不在三层里** ——**它是核心按 `reg_bits`/`val_bits` 算出来的，驱动换不掉**（03 §A3）。
3. **regmap 不是总线**（08 §3）：**没有 `bus_type`、没有 `regmap_device`、没有 `regmap_driver`。**
   **但它确实会通过 `regmap_attach_dev()` 挂到 `struct device` 上**，
   **所以别人能用 `dev_get_regmap()` 反查**（05 §3.1）。

---

## §4 数据结构速览

**完整版在 02 篇，这里只留"复习时需要的最少信息"。**

| 结构 | 位置 | 字段数 | 谁填 | 关键字段 |
|---|---|---|---|---|
| `struct regmap_config` | `include/linux/regmap.h:370` | **54** | **芯片驱动** | `reg_bits` `val_bits` `reg_stride` `max_register` `cache_type` `volatile_reg` `fast_io` `ranges` |
| `struct regmap_bus` | `include/linux/regmap.h:545-562` | **18** | **总线适配** | **`reg_read`/`reg_write` 是最小可用集**；`read`/`write` 是批量 |
| `struct regmap` | `drivers/base/regmap/internal.h`（**私有，驱动看不到内部**） | **大**（02 §5.2 逐字段） | **核心** | `lock`/`unlock`（函数指针）· `bus`/`bus_context` · `cache_ops`/`cache_*` · `format` · `range_tree` |
| `struct regcache_ops` | `drivers/base/regmap/internal.h:179-191` | **9**（其中 `debugfs_init` 在 `#ifdef CONFIG_DEBUG_FS` 里，`:184-186`） | **只有核心自己** | `name` `type` `init` `exit` `read` `write` `sync` `drop` |
| `struct reg_field` | `include/linux/regmap.h:1278`（宏 `REG_FIELD` 在 `:1286`、`REG_FIELD_ID` 在 `:1292`） | 5 | 芯片驱动 | `reg` `lsb` `msb` **`id_size`/`id_offset`（一组同构位域）** |
| `struct reg_default` | `include/linux/regmap.h:57` | **2** | 芯片驱动 | `reg` `def` ——**缓存的上电默认值表** |
| `struct reg_sequence` | `include/linux/regmap.h:72` | **3** | 芯片驱动 | **多了 `delay_us`** ——`regmap_multi_reg_write` 用 |
| `struct regmap_irq_chip` | `include/linux/regmap.h:1563`（04 §1.2 逐组） | **40** | **PMIC 驱动** | `status_base` `mask_base` `ack_base` `irqs` `num_regs` |
| `struct regmap_range_cfg` | `include/linux/regmap.h:462`（`regmap_config.ranges` 在 `:431` 指向它） | **8** | 芯片驱动 | `selector_reg` `selector_mask` `window_start` `window_len` |

**"谁填"这一列是本表的重点**：
**9 个结构里 6 个由芯片驱动填、1 个由总线适配填、2 个只有核心自己动。**
⇒ **这就是"复杂度在配置里"的直接体现**（§2.6 代价 1）。

**两把真锁（除了可插拔的那对函数指针）**：
**`map->mutex` / `map->spinlock` / `map->raw_spinlock`（三者按 `fast_io` 三选一）
+ `map->cache_lock`（缓存专用 mutex）**（02 §6 / 03 §B1）。

---

## §5 机制速览

### §5.1 初始化：入口是宏，不是函数

**`devm_regmap_init_mmio(dev, regs, config)` 是宏**（`include/linux/regmap.h:1063`），
**展开成带 lock_key 的 `__devm_regmap_init_*()`** ——
⇒ **grep 找不到同名函数定义是正常的，这是索引工具的盲区**（99 §4）。

| 入口种类 | 数量 |
|---|---|
| `regmap_init_*`（要自己 `regmap_exit`） | **14** |
| `devm_regmap_init_*`（自动释放） | **15** |
| ⚠️ **唯一不对称** | **`devm_regmap_init_i3c` 有，`regmap_init_i3c` 没有** |

**`__regmap_init()` 九步**（03 §A2，`drivers/base/regmap/regmap.c:729` 起）：
**参数校验 → 分配 → ①选锁（三层的层 3）→ 算 format 字节数 → ⑤挑格式化函数（硬编码 switch）
→ 装 bus 回调 → ⑦初始化缓存（层 2）→ 建分页树 → attach_dev + debugfs。**

### §5.2 稳态读写：三道门

**每次 `regmap_read()` / `regmap_write()` 要过三道门**（03 §A5 / 02 §3）：

```
   ① 锁（层 3）
   ② 可访问性检查   ── regmap_writeable() / regmap_readable() / regmap_volatile()
   ③ 缓存（层 2）    ── 命中就直接返回，不碰硬件
       │  未命中 / volatile / bypass
       ▼
   ④ 分页改写（ranges）── 地址在这里被悄悄换掉
   ⑤ 格式化 + 层 1 的 bus 回调
```

⚠️ **两个最常见的"意外"都在这条链上**：
**第 ②③ 道门拦下来 ⇒ 完全看不到总线上的动作**（07 §5.3 用 tracepoint 区分）；
**第 ④ 步改写地址 ⇒ tracepoint 里 `regmap_reg_write` 和 `regmap_hw_write_start` 的 `reg` 不一样**（08 §8）。

### §5.3 缓存与电源管理：四个 API 的配合

| API | 位置 | 什么时候调 |
|---|---|---|
| `regcache_cache_only(map, true)` | `drivers/base/regmap/regcache.c:536` | **suspend 里，断电之前** |
| `regcache_mark_dirty(map)` | `drivers/base/regmap/regcache.c:560` | **suspend 里，漏了会导致 resume 后部分功能失效**（05 §6.2） |
| `regcache_cache_only(map, false)` | 同上 | **resume 里，上电之后** |
| `regcache_sync(map)` | `drivers/base/regmap/regcache.c:350` | **resume 的最后一步，把脏值写回硬件** |

⚠️ **`regcache_cache_bypass()`（`regcache.c:580`）不属于这套时序** ——
**它是 `regcache_sync()` 内部自用的，驱动几乎不该直接调**（05 §7.1 / 08 §6）。

### §5.4 三个缓存后端 + 一个「不缓存」

| `cache_type` | 后端 | 适合 |
|---|---|---|
| `REGCACHE_NONE` | **无** | **MMIO 默认**（访问本来就快） |
| `REGCACHE_FLAT` | **一维数组** | **寄存器少且密集** ——最快，最费内存 |
| `REGCACHE_RBTREE` | **红黑树** | **寄存器稀疏**（PMIC / codec 的常规选择） |
| `REGCACHE_COMPRESSED` | **LZO 压缩** | **寄存器极多**；需 `select LZO_COMPRESS` |

⚠️ **本版本只有这四个取值**（`include/linux/regmap.h:41-46`）——
**更高版本的 `REGCACHE_MAPLE` 在 6.1 里不存在**（05 §6.1）。

### §5.5 可观测性

| 出口 | 规模 | 关键陷阱 |
|---|---|---|
| **debugfs** | **692 行 / 每 map 8 个文件** | **目录名是 `<设备名>-<config.name>`**（07 §2.6）；**`registers` 默认只读**（07 §1.2） |
| **tracepoint** | **17 个事件**（口径：`TRACE_EVENT` + `DEFINE_EVENT`，不含 `DECLARE_EVENT_CLASS` 的 22） | ⚠️ **`name` 字段是设备名，不含 `config.name`** ⇒ 同设备多 map 无法区分（07 §3.2） |
| **两个源码级开关** | **`LOG_DEVICE` + `REGMAP_ALLOW_WRITE_DEBUGFS`，都被 `#undef` 硬关** | **要改源码重编译**（07 §1.2） |
| **故障注入** | **0** | 07 §6 |

---

## §6 端到端调用路径

**两条路径，覆盖 90% 的实际使用。**

### §6.1 路径 A：一个 MMIO 消费者，从 probe 到硬件

```
① platform 层 probe 到设备（regmap 不参与）
        │
② 驱动：base = devm_platform_ioremap_resource(pdev, 0)
        │
③ 驱动：devm_regmap_init_mmio(&pdev->dev, base, &foo_config)
        │   ↑ include/linux/regmap.h:1063 —— 是宏
        ├──► __devm_regmap_init_mmio_clk()
        │        └──► regmap_init_mmio_clk（drivers/base/regmap/regmap-mmio.c）
        │                ├─ regmap_mmio_regbits_check(config->reg_bits)   :440
        │                │     只接受 8/16/32，否则 -EINVAL           :31-40
        │                ├─ regmap_mmio_get_min_stride(config->val_bits) :447
        │                │     只接受 8/16/32，val_bits=64 在此被拒    :43-63
        │                ├─ regmap_get_val_endian(dev, &regmap_mmio, config)  :465
        │                │     这一步才读 DTS 的 big-endian（06 §2.1）
        │                └──► __regmap_init()（drivers/base/regmap/regmap.c:729）
        │                         ① 选锁（层 3）        :789-811
        │                         ⑤ 挑格式化函数（硬编码 switch）:919 起
        │                         ⑦ 初始化缓存（层 2）
        │                         ⑧ 建分页树           :1151-1152 起
        │                         ⑨ attach_dev + debugfs
        │
④ 驱动：regmap_write(map, 0x10, 0x1)
        │        ├─ map->lock()                              层 3
        │        ├─ regmap_writeable(map, 0x10)?             regmap.c:90-102
        │        │     假 ⇒ 直接 return -EIO（不是 -EINVAL！）regmap.c:1994-1995
        │        ├─ 缓存分支（cache_type != NONE 时）         层 2
        │        ├─ _regmap_select_page()（有 ranges 时）      regmap.c:1630
        │        │     地址在这里被改写                     regmap.c:1673
        │        ├─ 格式化进 map->work_buf
        │        ├─ bus->reg_write() 或 bus->write()          层 1
        │        ├─ trace_regmap_reg_write(map, reg, val)     regmap.c:2012
        │        └─ map->unlock()
        ▼
⑤ writel() 落到硬件
```

⚠**这条路径上有三处"看不到动作"的地方**（07 §5.3 的分诊依据）：
**`regmap_writeable` 拦下 · 缓存命中 · `cache_only` 打开** ——
**三者都不会在总线上产生任何事件**，
**所以"开 `regmap_hw_write_start` 有没有事件"是最干脆的判据。**

### §6.2 路径 B：一个 syscon 消费者（arm64 上最常见的 DT 路径）

```
① DTS 里有一个带 "syscon" compatible 的节点
      arch/arm64/boot/dts/rockchip/rk3399.dtsi:1483-1484（grf 节点）
        │
② 消费者驱动：syscon_regmap_lookup_by_phandle(np, "rockchip,grf")
        │        ↑ drivers/mfd/syscon.c:275 处导出
        │        全树 186 个文件调用这个家族
        │
③ syscon 核心：of_syscon_register()（drivers/mfd/syscon.c:45）
        ├─ of_address_to_resource + of_iomap                 :61 / :64
        ├─ 读 big/little/native-endian → val_format_endian   :69-74
        ├─ 读 reg-io-width（缺省 4）                          :81-83
        │     一次改三个字段：reg_stride/val_bits/max_register :108-110
        ├─ 读 hwlocks → use_hwlock/hwlock_id/hwlock_mode      :85-89
        ├─ name = kasprintf("%pOFn@%pa", np, &res.start)      :103
        │     所以 debugfs 里能直接对上 DT 节点名（07 §2.6）
        └─ regmap_init_mmio(NULL, base, &syscon_config)       :112
              └──► 回到 §6.1 的第 ③ 步
        │
④ 消费者拿到 regmap，正常读写（和 §6.1 第 ④ 步完全一样）
```

**两条路径在第 ③ 步汇合** ——**这是"regmap 只有一个初始化核心"的直接体现。**

### §6.3 路径 C：suspend / resume（缓存的唯一正当用途）

```
suspend：regcache_cache_only(map, true)   regcache.c:536  ── 写只进缓存
         regcache_mark_dirty(map)         regcache.c:560  ── 漏了就完了
         驱动自己断电
                    ⋯⋯ 设备掉电 ⋯⋯
resume ：驱动自己上电
         regcache_cache_only(map, false)  ── 先关，顺序不能反
         regcache_sync(map)               regcache.c:350
              ├─ trace_regcache_sync(map, name, "start")     regcache.c:369
              ├─ if (!map->cache_dirty) goto out             regcache.c:371-372
              │     mark_dirty 漏了就在这里直接跳过
              ├─ 内部置 cache_bypass，逐个 _regmap_write() 回灌
              └─ trace_regcache_sync(map, name, "stop")      regcache.c:428
```

---

## §7 子系统关系

### §7.1 谁用 regmap（消费者方向）

| 消费者 | 怎么用 | 数量级 |
|---|---|---|
| **pinctrl / clk / reset** | **自建 MMIO regmap** | **大量**（如 `drivers/pinctrl/pinctrl-rockchip.c:3430`） |
| **MFD**（PMIC 等） | **父设备建 + regmap-irq，子设备共享** | `drivers/mfd/` **8 个文件**有自定义 `regmap_bus` |
| **syscon 的消费者** | **`syscon_regmap_lookup_by_*`** | **186 个文件** |
| **ASoC codec** | **自建 + 重度用缓存** | 大量 |
| **IIO / hwmon / regulator / rtc** | 自建 | 大量 |

### §7.2 regmap 用谁（依赖方向）

| 被依赖的 | 用在哪 | 备注 |
|---|---|---|
| **各总线子系统**（I2C / SPI / SPMI / MDIO / …） | **12 个适配文件** | **Kconfig 里各自 `depends on`**（07 §1.1） |
| **irqdomain** | **regmap-irq** | `select IRQ_DOMAIN if REGMAP_IRQ` |
| **hwspinlock** | **层 3 的 hwlock 锁策略 + syscon 的 `hwlocks`** | **可选**；`-EPROBE_DEFER` 会传导（06 §3.2） |
| **LZO** | `REGCACHE_COMPRESSED` | `select LZO_COMPRESS` |
| **debugfs / ftrace** | **观测面** | 07 篇 |
| **OF / fwnode** | **只用来读三个字节序属性** | **regmap 核心对 DT 的依赖极浅**（06 §1） |

### §7.3 与本项目其他文档集的关系

| 相邻子系统 | 关系 | 去 |
|---|---|---|
| **irq** | **regmap-irq 产出的是 `irq_chip` + `irq_domain`** | **`output/irq/`** 讲那一侧 |
| **pinctrl / clk / gpio / reset / regulator** | **它们是 regmap 的典型消费者** | 各自文档集 |
| **of** | **syscon 那条路的 DT 解析** | **`output/of/`** |
| **cdev** | **regmap 没有字符设备**（07 §7）——对比用 | `output/cdev/` |

---

## §8 导读：接下来读哪一篇

**按"你现在想干什么"选**，不要顺序读。

| 我现在… | 读 | 里面有什么 |
|---|---|---|
| **要给一个新芯片加寄存器访问** | **`05-usage.md`** | **六个角色的骨架 + §7 近邻符号区分（六组）** |
| 要查某个 API 的语义 | `01-api.md` | §0 代码地图 · §2 `regmap_config` 54 字段 · §9 错误码 |
| 要看结构体之间的关系 | `02-datastructures.md` | §1.0 六组易错关系 · §3 核心操作数据流 · §4 命名约定 |
| 要理解初始化 / 读写内部流程 | `03-internals.md` | Part A 时间线 · Part B 横切（锁 / 字节序 / 分页）· Part C 评价与设计债 |
| **要用缓存、分页、regmap-irq、debugfs** | **`04-extensions.md`** | **四个子框架各一节 + §8 汇总表** |
| DTS 写了没反应 | **`06-devicetree.md` §4**（**静默失效决策树**） | §0 五条发现路径 · §3 syscon · §7 死字属性 |
| **出问题了不知道看哪** | **`07-debug.md` §0 分诊表** | debugfs 8 文件 · 17 tracepoint · 错误码分诊 · §8 一页速查 |
| **看代码看不懂"为什么这样设计"** | **`08-feynman.md`** | **九个主题，每个都有"这个类比什么时候会误导你"** |
| **想知道哪些结论不能直接信** | **`99-evidence.md` §5** | **未验证项四层分层 + 逐篇取证表** |

### §8.1 第一次读 regmap 的推荐顺序

**不要从 `regmap.c` 开始**（03 §C5 的结论）：

1. **`01-api.md` §2** ——**逐字段读 `regmap_config`。这一步做完，regmap 就懂了一半。**
2. **`08-feynman.md` §2 和 §3** ——**先把两个最易错的类比否掉**（config vs bus、regmap 不是总线）。
3. **`02-datastructures.md` §3** ——**一张读写数据流图。**
4. **`05-usage.md` §1** ——**抄一个三步骨架跑通。**
5. 之后按需查 04 / 06 / 07。

### §8.2 ⚠️ 三条"最容易记错"的事（复习时优先重看）

| 事 | 正确的是 | 在哪 |
|---|---|---|
| **写不可写寄存器返回什么** | ⚠️ **`-EIO`（不是 `-EINVAL`）** | 07 §5.3 |
| **`dev_get_regmap` 失败返回什么** | ⚠️ **`NULL`（不是 `ERR_PTR`）** ——和 `devm_regmap_init_*` 相反 | 05 §3.1 |
| **清中断该用哪个 API** | ⚠️ **`regmap_write_bits`（不是 `regmap_update_bits`）** | 05 §7.2 / 08 §7 |
