---
title: 数据结构
---

# regmap 02：数据结构与关系

本篇先破歧义（§1.0），再画关系（§2、§3、§7），最后逐个看结构体（§5）。

---

## §1.0 先分清六组关系

### §1.0.A regmap 不是总线，也不是设备

**最常见的错误类比**：以为 regmap 像 I2C/SPI 那样是一条总线，
或者以为 `struct regmap` 是一个设备。

| | regmap | I2C / SPI（真总线） |
|---|---|---|
| 有 `struct bus_type` 吗 | **没有**（99 §1.3 的门 1 无输出） | 有 |
| 有 `struct device` 吗 | **没有** ——`struct regmap` 不内嵌 device | 有 |
| 会被 probe 吗 | **不会** | 会 |
| 谁创建它 | **驱动自己在 `probe()` 里创建** | 总线核心枚举出来 |
| 一个设备有几个 | **可以有多个**（不同寄存器区各一个） | 一个 |

**准确的定位**：`struct regmap` 是**一个驱动持有的"寄存器访问句柄"**，
**和 `struct file` 或 `struct clk` 是同一类东西** ——不是设备，是**对某种资源的引用**。

⇒ **所以 regmap 是框架型子系统**（99 §1.3），
**它 `select` 进来、被驱动调用，从不主动 probe 任何东西。**

### §1.0.B `regmap_config` vs `struct regmap` —— 输入 vs 状态

**错误类比**：以为它们是同一个东西的"公开版和内部版"。

| | `struct regmap_config` | `struct regmap` |
|---|---|---|
| 谁填 | **驱动作者**（通常是个 `static const`） | **核心在 `regmap_init` 里填** |
| 位置 | `include/linux/regmap.h:370`（**公开**） | `drivers/base/regmap/internal.h:50`（**私有**） |
| 字段数 | **54**（按分号计） | **74**（按分号计） |
| 生命周期 | **只在 `regmap_init` 时被读一次** | **整个生命周期** |
| 能改吗 | **init 之后改它没有任何效果** | 核心内部改（如 `cache_only`） |

⚠️ **"init 之后改 config 没效果"是一个真实的陷阱**：
**核心把 config 的字段逐个拷进 `struct regmap`**
（`drivers/base/regmap/regmap.c:855` 一带就是在拷 `wr_table`、`writeable_reg` 等）
⇒ **config 通常声明成 `static const` 正是为了防止误改。**

**74 > 54 的差额是什么**：核心额外维护的运行期状态 ——
`work_buf`（格式化用的临时缓冲）、`format`（格式化函数集）、
`bus` / `bus_context`、`cache_ops` / `cache` / `cache_only` / `cache_bypass` / `cache_dirty`、
`lock` / `unlock` / `lock_arg`（§1.0.F）、`async_lock`、`cache_lock` …

### §1.0.C `volatile` vs `precious` —— 两件完全不同的事

⚠️ **这是本子系统最容易混的一组**，而且两个词在 C 语言里都有别的含义。

| | `volatile_reg` / `volatile_table` | `precious_reg` / `precious_table` |
|---|---|---|
| 含义 | **"这个寄存器的值会自己变"** | **"读这个寄存器有副作用"** |
| 影响谁 | **缓存** ——volatile 的不进缓存 | **debugfs** ——precious 的不被 dump |
| 典型例子 | 状态寄存器、FIFO 计数 | **读清（read-to-clear）的中断状态寄存器** |
| 判定函数 | `regmap_volatile()`（`drivers/base/regmap/internal.h:196`） | `regmap_precious()`（`drivers/base/regmap/internal.h:197`） |

**一个寄存器可以同时是两者**（读清的中断状态寄存器就是），
**也可以只是其中之一**（一个只增不减的计数器是 volatile 但不 precious）。

⚠️ **搞混的后果**：
- **该标 volatile 没标** ⇒ **缓存返回过期值**，驱动以为硬件没变
- **该标 precious 没标** ⇒ **有人 `cat` 一下 debugfs 就把中断状态清了**
  ⇒ **中断丢失，而且是"看一眼就坏"的那种**（07 篇）

### §1.0.D `cache_only` vs `cache_bypass` —— 名字都带 cache，方向相反

| | `cache_only` | `cache_bypass` |
|---|---|---|
| 含义 | **只读写缓存，不碰硬件** | **只碰硬件，不用缓存** |
| 什么时候用 | **芯片掉电期间** | **要读真实硬件值时** |
| 内部字段 | `cache_only`（`drivers/base/regmap/internal.h` 的 `struct regmap` 里，注释 `if set, only the cache is modified not the HW`） | `cache_bypass`（注释 `if set, only the HW is modified not the cache`） |
| 打开的 API | `regcache_cache_only(map, true)` | `regcache_cache_bypass(map, true)` |

**两条注释是逐字对照的**（`internal.h` 里那两行紧挨着）——
**"only the cache not the HW" vs "only the HW not the cache"**。

⚠️ **同时打开两个是没有意义的**（会互相矛盾）⇒ 05 §7 有独立一节。

### §1.0.E `regmap_field`（单数）vs `regmap_fields`（复数）

⚠️ **只差一个 s，语义差一个维度。**

| | `regmap_field_*` | `regmap_fields_*` |
|---|---|---|
| 描述什么 | **一个寄存器里的一段位** | **N 个寄存器里同一段位** |
| 关键字段 | `reg` / `lsb` / `msb` | **加上 `id_size` / `id_offset`** |
| API 形状 | `regmap_field_read(field, &val)` | `regmap_fields_read(field, **id**, &val)` |
| 典型场景 | 一个增益字段 | **8 个通道各有一个增益字段** |

**`struct reg_field` 只有 5 个字段**（`include/linux/regmap.h:1278-1284`）：

```c
struct reg_field {
	unsigned int reg;
	unsigned int lsb;
	unsigned int msb;
	unsigned int id_size;
	unsigned int id_offset;
};
```

**`REG_FIELD(_reg, _lsb, _msb)` 宏**（`include/linux/regmap.h:1286-1290`）
**只填前三个** ⇒ `id_size` / `id_offset` 都是 0
⇒ **用这个宏建的 field 只能用单数 API。**

**多路的规律**：第 `id` 路的寄存器号是 `reg + id * id_offset`，
`id_size` 是上限（用于校验）。

### §1.0.F 本子系统有几层可插拔：**三层**

01 §1 已给出完整表。**这里只强调一件容易忽略的事**：

⚠️ **锁也是可插拔的** ——`struct regmap` 里的
`lock` / `unlock` 是**函数指针**（`drivers/base/regmap/internal.h:62-63`，
类型定义在 `include/linux/regmap.h:227-228`），
**不是一把具体的锁**。

⇒ **所以"regmap 用什么锁"这个问题的答案是"看 config"**，
**7 种内置策略 + 任意自定义**（01 §1.4）。

**三层的实现数对比很能说明设计意图**：

| 层 | 实现数 | 开放度 | 说明 |
|---|---|---|---|
| 总线 | **61** | 完全开放 | **这是 regmap 存在的理由** |
| 锁 | **7 + 自定义** | 半开放 | **上下文差异太大，必须让调用者选** |
| 缓存 | **3** | 完全封闭 | **缓存策略是通用问题，不该让驱动发明** |

---

## §2 核心抽象与三方的关系

**regmap 没有总线**（§1.0.A），所以这里画「**消费者 ↔ 核心 ↔ 三个可插拔层**」：

```
   ┌──────────────── 消费者（驱动）────────────────────┐
   │  regmap_read/write/update_bits                    │
   │  regmap_field_read/write                          │
   │  regcache_sync / cache_only / cache_bypass        │
   │  devm_regmap_add_irq_chip                         │
   └───────────────────────┬───────────────────────────┘
                           │ 输入：struct regmap_config（54 字段）
                           ▼
   ┌───────────────────────────────────────────────────┐
   │  regmap 核心（regmap.c 3529 行）                    │
   │  struct regmap（74 字段，internal.h:50）            │
   │                                                   │
   │   ① 范围检查   regmap_writeable/readable/          │
   │                volatile/precious                  │
   │   ② 缓存      cache_ops->read/write               │
   │   ③ 格式化    format.format_reg/format_val/       │
   │                parse_val（struct regmap_format）   │
   │   ④ 加锁      map->lock(map->lock_arg)            │
   └──┬──────────────┬───────────────┬─────────────────┘
      │ 层 1          │ 层 2           │ 层 3
      ▼              ▼               ▼
  ┌────────┐   ┌────────────┐   ┌──────────────┐
  │regmap_ │   │regcache_ops│   │regmap_lock / │
  │bus ×61 │   │    ×3      │   │_unlock ×7+   │
  └───┬────┘   └────────────┘   └──────────────┘
      ▼
  i2c_transfer / spi_sync / readl / 自定义协议
```

**图里最要紧的三条**：

| 观察 | 说明 |
|---|---|
| **`regmap_config` 只在建的时候进去一次** | 之后核心用的是 `struct regmap`（§1.0.B） |
| **三个可插拔层是并列的**，不是叠起来的 | 总线管"怎么发字节"、缓存管"要不要发"、锁管"谁能发" |
| **格式化（③）不可插拔** | `struct regmap_format` 是核心按 `reg_bits`/`val_bits` 等**算出来的**，驱动不能替换（§5.4） |

### §2.1 "bus 提供默认值，config 覆盖它"

⚠️ **一个容易忽略的机制**：`struct regmap_bus` 里也有三个和 config 同名的字段
（`include/linux/regmap.h:557-559`）：

| 字段 | 在 bus 里 | 在 config 里 |
|---|---|---|
| `read_flag_mask` | `regmap_bus.read_flag_mask`（`:557`） | `regmap_config.read_flag_mask` |
| 寄存器字节序 | `reg_format_endian_default`（`:558`） | `reg_format_endian` |
| 值字节序 | `val_format_endian_default`（`:559`） | `val_format_endian` |

⇒ **bus 给的是"这类总线的常规值"，config 给的是"这颗芯片的实际值"**，
**config 非默认时覆盖 bus。**

**为什么要这个机制**：比如 SPI 上"地址字节最高位表示读"是极常见的约定，
**让 `regmap-spi.c` 提供默认值，驱动就不用每次都写。**

⚠️ **但它造成一个诊断困难**：**`read_flag_mask` 最终值不在你的 config 里** ——
⇒ **要看 debugfs 或者读 bus 的代码**（07 篇）。

---

## §3 核心操作数据流

### §3.1 一次 `regmap_read()`

```
regmap_read(map, reg, &val)                        regmap.c
  │
  ├─ ① if (!IS_ALIGNED(reg, map->reg_stride)) return -EINVAL
  │      第一道门就是对齐检查（01 §9.2）
  │
  ├─ ② map->lock(map->lock_arg)                    层 3（7 种之一）
  │
  ├─ ③ _regmap_read(map, reg, &val)
  │     │
  │     ├─ if (!map->cache_bypass) {
  │     │      ret = regcache_read(map, reg, &val)  层 2
  │     │      if (ret == 0) → 命中，直接返回
  │     │   }
  │     │      trace_regmap_reg_read_cache() 在这里发
  │     │
  │     ├─ if (map->cache_only) return -EBUSY       掉电期间没缓存就失败
  │     │      ← regmap.c:2896-2897（-EBUSY 只有 2 处，这是其一）
  │     │
  │     ├─ if (!regmap_readable(map, reg)) return -EIO
  │     │      ← regmap.c:2900（和写侧的 :1995 对称，01 §9.2）
  │     │
  │     └─ _regmap_raw_read / bus->reg_read
  │          ├─ format.format_reg(work_buf, reg, shift)   格式化地址
  │          ├─ bus->read(bus_context, work_buf, ...)     层 1
  │          └─ val = format.parse_val(buf)               解析值
  │              trace_regmap_hw_read_start/done 在这里
  │
  ├─ ④ map->unlock(map->lock_arg)
  │
  └─ trace_regmap_reg_read() 发出
```

⚠️ **`cache_only` 时读不到缓存会返回 `-EBUSY`**（`drivers/base/regmap/regmap.c:2897`）——
**全子系统 `-EBUSY` 只有 2 处，另一处在 `:3080`**（01 §9.1）。

⚠️ **注意 `_regmap_read()` 里三道门的顺序**（`drivers/base/regmap/regmap.c:2884-2901`）：
**先查缓存 → 再看 `cache_only` → 最后才查 `regmap_readable()`**
⇒ **所以"读一个不可读的寄存器"如果命中了缓存，会成功返回！**
**只有缓存未命中时才会拿到 `-EIO`** ——这是一个很隐蔽的行为差异。

**两条路径的 tracepoint 不同**（07 篇）：
**命中缓存发 `regmap_reg_read_cache`，走硬件发 `regmap_hw_read_start/done`**
⇒ **所以从 trace 就能看出缓存命中率。**

### §3.2 一次 `regmap_write()`

```
regmap_write(map, reg, val)                        regmap.c
  ├─ ① IS_ALIGNED 检查 → -EINVAL
  ├─ ② 加锁
  ├─ ③ _regmap_write(map, reg, val)
  │     │
  │     ├─ if (!regmap_writeable(map, reg)) return -EIO
  │     │      ← regmap.c:1994-1995（01 §9.2 那个陷阱）
  │     │
  │     ├─ if (!map->cache_bypass && !map->defer_caching) {
  │     │      regcache_write(map, reg, val)        先写缓存
  │     │      if (map->cache_only) { map->cache_dirty = true; return 0; }
  │     │   }
  │     │      掉电期间只写缓存并标脏，不碰硬件
  │     │
  │     └─ format + bus->write / bus->reg_write     层 1
  ├─ ④ 解锁
  └─ trace_regmap_reg_write() 发出
```

**`cache_only` 那一行是整个电源管理机制的核心**：
**掉电期间的写全部落进缓存并标脏，resume 时 `regcache_sync()` 一次性写回**（01 §6）。

### §3.3 `regmap_update_bits()` 为什么可能不写

```
regmap_update_bits(map, reg, mask, val)
  └─ regmap_update_bits_base(map, reg, mask, val, &change, false, false)
       ├─ 读出旧值（可能命中缓存）
       ├─ tmp = (orig & ~mask) | (val & mask)
       ├─ if (tmp != orig) { 写; *change = true; }
       └─ else 什么都不做，*change = false
```

⇒ **值没变就不写** ——**对"写同样的值也有副作用"的寄存器是错的**
⇒ 那种情况用 `regmap_write_bits()`（01 §4.1、05 §7）。

**`regmap_update_bits_base()` 的三个开关**：
`change`（回报有没有变）· `async`（异步写）· `force`（强制写）
⇒ **`regmap_write_bits()` 就是 `force=true` 的包装。**

---

## §4 命名约定速查

| 前缀 | 属于 | 例子 |
|---|---|---|
| `regmap_read` / `_write` / `_update_bits` … | **核心读写** | — |
| `regmap_bulk_*` | **按"寄存器个数"批量** | `regmap_bulk_read(map, reg, val, **val_count**)` |
| `regmap_raw_*` | **按"字节数"原始访问** | `regmap_raw_read(map, reg, val, **val_len**)` |
| `regmap_noinc_*` | **地址不自增**（FIFO） | — |
| `regmap_field_*`（单数） | **一个寄存器的一段位** | §1.0.E |
| `regmap_fields_*`（复数） | **多路同名字段** | §1.0.E |
| `regcache_*` | **缓存控制** | `regcache_sync` · `regcache_cache_only` |
| `regmap_irq_*` / `regmap_add_irq_chip` | **regmap-irq 子框架** | 04 篇 |
| `devm_*` | **跟 device 自动释放的版本** | **绝大多数场景用它** |
| `__regmap_init*` / `__devm_regmap_init*` | **真实符号**（宏展开后） | 14 + 15 个（01 §3.1） |
| `regmap_writeable` / `_readable` / `_volatile` / `_precious` | **内部判定函数**（`internal.h:194-199`） | **不导出**，但 debugfs 和核心都用 |

⚠️ **三组高危近邻**（05 §7 有独立一节）：

| 容易混的 | 判据 |
|---|---|
| `bulk` vs `raw` | **第 4 个参数的单位**：`bulk` 是寄存器数，`raw` 是**字节数** |
| `field` vs `fields` | **API 里有没有 `id` 参数** |
| `cache_only` vs `cache_bypass` | **§1.0.D 的两条逐字注释** |

**一条很有用的记忆规则**：
**`regcache_` 开头的都是"控制缓存行为"，`regmap_` 开头的都是"访问寄存器"。**

---

## §5 结构体逐个看

### §5.1 `struct regmap_config`（`include/linux/regmap.h:370`，54 字段）

01 §2 已按七组列全。**这里只补两条结构性观察**：

| 观察 | 说明 |
|---|---|
| **54 个字段里"必填"只有 2 个** | `reg_bits` + `val_bits`（01 §2.1）⇒ **其余全有合理默认** |
| **有 4 组"表 or 回调"的二选一** | `wr_table`/`writeable_reg` · `rd_table`/`readable_reg` · `volatile_table`/`volatile_reg` · `precious_table`/`precious_reg` |

⚠️ **表和回调同时给了会怎样**：**回调优先** ——
`regmap_writeable()`（`drivers/base/regmap/regmap.c:90-102`）
**先查 `writeable_reg` 回调，有就直接返回，不看表**。
⇒ **所以两个都填时表被静默忽略。**

### §5.2 `struct regmap`（`drivers/base/regmap/internal.h:50`，74 字段）

**它是私有的**（在 `internal.h` 里）⇒ **驱动看不到，也不该看**。
**按用途分六组**：

| 组 | 字段 |
|---|---|
| **锁（层 3）** | `lock` / `unlock` / `lock_arg`（`internal.h:62-64`）——**函数指针** |
| **格式化** | `work_buf`（临时缓冲）· `format`（`struct regmap_format`，§5.4） |
| **总线（层 1）** | `bus` / `bus_context` |
| **范围与属性** | `max_register` · `reg_stride` / `reg_stride_order` · `volatile_reg` / `precious_reg` · `volatile_table` / `precious_table` |
| **缓存（层 2）** | `cache_ops` · `cache_type` · `cache` · `cache_size_raw` · `cache_word_size` · `cache_only` / `cache_bypass` / `cache_dirty` / `cache_free` · `cache_lock`（mutex） |
| **异步** | `async_lock`（**spinlock_t**）· 异步请求链表 |
| **debugfs** | `debugfs_off_cache`（链表） |

⚠️ **注意 `struct regmap` 里有两把"真锁"**（除了可插拔的那对函数指针）：
**`cache_lock`（mutex）和 `async_lock`（spinlock_t）** ——
**它们保护的是缓存元数据和异步链表，和"寄存器访问锁"是不同的东西**（§6）。

### §5.3 `struct regmap_bus`（`include/linux/regmap.h:544-563`，18 字段）

**层 1 的接口**。按用途分四组：

| 组 | 字段 | 说明 |
|---|---|---|
| **字节流式回调** | `write` · `gather_write` · `read` | **总线上传的是"地址字节 + 值字节"** ——I2C/SPI 这类 |
| **寄存器式回调** | `reg_write` · `reg_read` · `reg_update_bits` | **总线本身有"寄存器号"概念** ——MMIO 这类 |
| **不自增变体** | `reg_noinc_write` · `reg_noinc_read` | FIFO 类 |
| **异步** | `async_write` · `async_alloc` | 少数总线支持 |
| **默认值**（§2.1） | `read_flag_mask`(`:557`) · `reg_format_endian_default`(`:558`) · `val_format_endian_default`(`:559`) | **被 config 覆盖** |
| **能力/生命周期** | `fast_io`(`:545`) · `max_raw_read`(`:560`) · `max_raw_write`(`:561`) · `free_context` · `free_on_exit`(`:562`) | — |

⚠️ **"字节流式"和"寄存器式"二选一**（01 §8）：
**核心按"有没有 `reg_read`"决定走哪条路** ⇒ **不能都填。**

⚠️ **`regmap_bus.fast_io`（`:545`）和 `regmap_config.fast_io` 是两个字段**：
**bus 声明"这类总线本来就快"，config 声明"我这颗芯片要用 spinlock"**
⇒ **又一处"bus 给默认、config 覆盖"**（§2.1）。

### §5.4 `struct regmap_format`（`drivers/base/regmap/internal.h:30-42`）

**这是"格式化"那一层的全部内容**，10 个字段：

| 字段 | 作用 |
|---|---|
| `buf_size` · `reg_bytes` · `pad_bytes` · `val_bytes` | **各段占几字节** |
| `reg_downshift` | 地址右移量 |
| `format_write` | **一次把地址+值都写进缓冲**（单寄存器写的快路径） |
| `format_reg` | **把寄存器号写进缓冲** |
| `format_val` | **把值写进缓冲** |
| `parse_val` | **从缓冲读出值** |
| `parse_inplace` | **原地做字节序转换**（批量读用） |

⚠️ **这一层不可插拔**：**核心在 `regmap_init` 里按
`reg_bits` / `val_bits` / `pad_bits` / `*_format_endian` 从一张内部表里挑函数**
⇒ **所以"支持的位宽组合"是有限的** ——
**不在表里的组合会让 `regmap_init` 返回 `-EINVAL`**（05 §8）。

**`parse_inplace` 的存在解释了 `bulk` 和 `raw` 的区别**（§4）：
**`bulk_read` 读完会调 `parse_inplace` 做字节序转换，`raw_read` 不会。**

### §5.5 `struct regcache_ops`（`drivers/base/regmap/internal.h:179-191`）

**层 2 的接口，只有 9 个字段**：

| 字段 | 作用 |
|---|---|
| `name` · `type` | 名字和 `enum regcache_type`（核心按 `cache_type` 匹配） |
| `init` / `exit` | 建/销毁缓存 |
| `read` / `write` | **单个寄存器的缓存读写** |
| `sync` | **把脏值写回硬件**（`regcache_sync()` 的底层） |
| `drop` | 丢弃一段 |
| `debugfs_init` | ⚠️ **在 `#ifdef CONFIG_DEBUG_FS` 里**（`:184-186`） |

⚠️ **只有 9 个字段，而且没有"批量读写"** ⇒
**缓存层永远是逐寄存器操作的** ——批量优化只发生在总线层。

### §5.6 `struct regmap_range_cfg`（`include/linux/regmap.h:462`）

**分页寄存器的描述**，8 个字段：

| 字段 | 作用 |
|---|---|
| `name` | 名字 |
| `range_min` / `range_max` | **虚拟地址范围** |
| `selector_reg` / `selector_mask` / `selector_shift` | **哪个寄存器是"页选择器"** |
| `window_start` / `window_len` | **实际的窗口位置和大小** |

**机制**：驱动访问 `range_min..range_max` 里的地址时，
**核心自动先写 `selector_reg` 切页，再访问 `window_start` 里的对应偏移。**
⇒ **驱动完全看不到分页** ——这是 regmap 一个很有价值的抽象（04 篇）。

### §5.7 `struct reg_field`（`include/linux/regmap.h:1278-1284`）

见 §1.0.E。**5 个字段，`REG_FIELD()` 宏只填前三个。**

---

## §6 全局变量与锁

### §6.1 全局变量：几乎没有

**`drivers/base/regmap/` 里没有需要记住的全局状态** ——
**所有状态都在 `struct regmap` 实例里。**

**唯一的例外是三个 `regcache_ops` 实例**：

| 实例 | 位置 |
|---|---|
| `regcache_rbtree_ops` | `drivers/base/regmap/regcache-rbtree.c:542` |
| `regcache_lzo_ops` | `drivers/base/regmap/regcache-lzo.c:360` |
| `regcache_flat_ops` | `drivers/base/regmap/regcache-flat.c:76` |

⚠️ **它们既不是 `static` 也不是 `const`** ——
**是文件作用域的普通变量**，在 `drivers/base/regmap/internal.h:283-285`
用 `extern` 声明，再由 `drivers/base/regmap/regcache.c:19-23` 收进一张指针表。

⇒ **这就是"层 2 完全封闭"的实现方式**（§1.0.F）：
**加一个后端要同时改三处** ——新文件、`internal.h` 的 `extern`、
`regcache.c` 的表。⇒ **所以它不可能被外部模块扩展。**

⇒ **这是本子系统一个明确的正面设计**：多个 regmap 天然互不干扰。

### §6.2 三种锁，各管一件事

⚠️ **`struct regmap` 里有三套锁机制，很容易混**：

| 锁 | 类型 | 保护什么 | 位置 |
|---|---|---|---|
| **访问锁** | **可插拔的函数指针**（7 种，01 §1.4） | **一次寄存器访问的原子性** | `lock` / `unlock` / `lock_arg`（`internal.h:62-64`） |
| **`cache_lock`** | **`struct mutex`** | **缓存的元数据** | `struct regmap` 里 |
| **`async_lock`** | **`spinlock_t`** | **异步请求链表** | 同上 |

**为什么访问锁要可插拔**（08 §7 会展开）：
**regmap 的调用上下文差异极大** ——
I2C regmap 可能睡（要 mutex），MMIO regmap 可能在硬中断里（要 spinlock），
**PREEMPT_RT 上还要区分 `spinlock_t` 和 `raw_spinlock_t`**。
⇒ **没有一种锁能适配所有场景，所以让调用者选。**

⚠️ **`use_raw_spinlock` 在这棵树上格外重要**：
**本树可开 `CONFIG_PREEMPT_RT`**（timer 那套 99 §1.1 确认过），
**RT 上普通 `spinlock_t` 变成可睡的** ⇒
**在硬中断上下文用的 regmap 必须 `use_raw_spinlock = true`**（05 §1.4）。

**`disable_locking` 的用法**：调用者自己已经在更大的临界区里
（如某个 MFD 已经持了自己的锁）⇒ **让 regmap 不要再加一层。**
⚠️ **用错就是数据竞争，而且不会有任何警告。**

---

## §7 结构关系总图

```
  驱动的 static const                      核心私有
  ┌──────────────────────┐    init 时     ┌────────────────────────────┐
  │ struct regmap_config │ ──拷贝一次──►  │ struct regmap              │
  │ include/linux/       │                │ internal.h:50              │
  │   regmap.h:370       │                │ 74 字段                     │
  │ 54 字段               │                │                            │
  │  reg_bits 必填       │                │  lock/unlock/lock_arg ──┐  │
  │  val_bits 必填       │                │  work_buf               │  │
  │  max_register        │                │  format ────────────┐   │  │
  │  6 张寄存器表         │                │  bus/bus_context ─┐ │   │  │
  │  cache_type          │                │  cache_ops ─────┐ │ │   │  │
  │  fast_io/use_raw_... │                │  cache          │ │ │   │  │
  │  lock/unlock(可选)   │                │  cache_only     │ │ │   │  │
  │  ranges              │                │  cache_bypass   │ │ │   │  │
  └──────────────────────┘                │  cache_dirty    │ │ │   │  │
                                          │  cache_lock(mutex)│ │   │  │
                                          │  async_lock(spin) │ │   │  │
                                          └─────────────────┼─┼─┼───┼──┘
        ┌─────────────────────────────────────────────────┘ │ │   │
        │  层 2                       层 1 ─────────────────┘ │   │
        ▼                              ▼                     │   │
  ┌──────────────┐          ┌────────────────────┐          │   │
  │regcache_ops  │          │ struct regmap_bus  │          │   │
  │internal.h:179│          │ regmap.h:544-563   │          │   │
  │ 9 字段        │          │ 18 字段             │          │   │
  │ init/exit    │          │ write/gather_write │          │   │
  │ read/write   │          │ read     ← 字节流式 │          │   │
  │ sync/drop    │          │ reg_write/reg_read │          │   │
  └──────────────┘          │          ← 寄存器式 │          │   │
   ×3（封闭）                │ read_flag_mask 默认│          │   │
   rbtree/flat/lzo          │ *_endian_default │          │   │
                            └────────────────────┘          │   │
                             ×61（开放：39 个在树外目录）      │   │
                                                            │   │
        ┌───────────────────────────────────────────────────┘   │
        ▼  层 3（也是可插拔的）                                │
  ┌────────────────────────────────┐                            │
  │ regmap_lock / regmap_unlock    │                            │
  │ 函数指针（regmap.h:227-228）     │                            │
  │ 7 种内置（regmap.c:448-535）：    │                            │
  │  hwlock ×3 / none / mutex /    │                            │
  │  spinlock / raw_spinlock       │                            │
  │ + config 可自定义                │                            │
  └────────────────────────────────┘                            │
                                                                │
        ┌───────────────────────────────────────────────────────┘
        ▼  不可插拔
  ┌────────────────────────────────┐
  │ struct regmap_format           │
  │ internal.h:30-42，10 字段        │
  │ format_reg/format_val/         │
  │ parse_val/parse_inplace        │
  │ 核心按位宽从内部表里挑         │
  └────────────────────────────────┘
```

**这张图最该记住的四件事**：

1. **`regmap_config` → `struct regmap` 是"拷贝一次"** ——
   **之后改 config 无效**（§1.0.B）
2. **三层可插拔是并列的**（总线 / 缓存 / 锁），
   **开放度从"完全开放"到"完全封闭"递减**（§1.0.F）
3. **格式化层不可插拔** ⇒ **支持的位宽组合是有限的**（§5.4）
4. ⚠️ **`struct regmap` 里有三套锁**（可插拔的访问锁 + `cache_lock` + `async_lock`），
   **"regmap 的锁"这句话必须说清是哪一套**（§6.2）
