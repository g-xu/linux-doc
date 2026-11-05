---
title: 内部实现
---

# regmap 03：内部机制逐段剖析

## §0 导航

**本篇只承诺一条主轴：Part A 是"一个 regmap 从创建到销毁"的时间线。**
不按时间线组织的内容划到 Part B（横切机制）和 Part C（设计评价），
章节号带 `A`/`B`/`C` 前缀。

### §0.1 分层图

```
  驱动 probe()
    │ devm_regmap_init_i2c(client, &my_config)
    ▼
  ┌──────────────────────────────────────────────┐
  │ __regmap_init()  regmap.c:729                │
  │  ① 建 struct regmap，选锁（层 3）              │
  │  ② 算 format.*_bytes                         │
  │  ③ 从硬编码 switch 里挑格式化函数（§A3）    │
  │  ④ 建缓存（层 2）                              │
  │  ⑤ 建 ranges（分页）                           │
  │  ⑥ 建 debugfs                                 │
  └──────────────────┬───────────────────────────┘
                     ▼  稳态
  regmap_read/write → 范围检查 → 缓存 → 格式化 → 层 1（bus）
```

### §0.2 生命周期时间线（Part A 的骨架）

```
  驱动 probe()
    │
  devm_regmap_init_<bus>(...)      ← 宏，展开成 __devm_regmap_init_<bus>()
    └─ __regmap_init(dev, bus, ctx, config, key, name)   regmap.c:729   §A2
         ① 分配 struct regmap
         ② 选锁（层 3）                     :758–:807            §A2.1
         ③ 算 format 的字节数                    :827 一带
         ④ map->bus = bus                        :853
         ⑤ 挑格式化函数（两个大 switch）      :919–:1130          §A3
         ⑥ 拷 config 的表和回调                  :855 一带
         ⑦ regcache_init（层 2）                                        §A4
         ⑧ 建 ranges（分页）                                            §B4
         ⑨ 建 debugfs
         ✗ 任何一步失败 ⇒ goto err_* → 返回 ERR_PTR(-EINVAL)
    │
  稳态：regmap_read / regmap_write / regmap_field_* / …                 §A5
    │
  电源管理：cache_only → mark_dirty → …→ cache_sync                      §A6
    │
  拆除：devm 自动 regmap_exit()                                          §A7
```

### §0.3 哪些内容不在本篇

| 你想找 | 去哪 |
|---|---|
| 某个 API 怎么调 | 01 篇 |
| 结构体字段 | 02 §5 |
| 六组容易混的概念 | 02 §1.0 |
| 三层可插拔的全貌 | 01 §1 · 02 §1.0.F |
| 缓存后端 / regmap-irq / 分页的细节 | 04 篇 |
| 驱动代码骨架 | 05 篇 |
| DT 的三个字节序属性 | 06 篇 |
| 怎么观测 | 07 篇 |
| 哪些结论没验证过 | 99 §5 |

---

# Part A：一个 regmap 从创建到销毁

## §A1 入口是宏，不是函数

**驱动写的 `devm_regmap_init_i2c(client, &config)` 是一个宏**，
展开后调 `__devm_regmap_init_i2c(client, &config, &lock_key, "name")`。

**头文件里的注释逐字说明了为什么**
（`include/linux/regmap.h:565-571` 那段）：

> These functions take a lock key and name parameter, and should not be called
> directly. Instead, use the regmap_init macros that generate a key and name
> for each call.

⇒ **宏的唯一作用是给每个调用点生成一个独立的 lockdep key** ——
**这样 lockdep 能区分"不同 regmap 的锁"**，
否则所有 regmap 共用一个 key，会报大量假的死锁警告。

⚠️ **两个后果**：
1. **在索引工具里搜 `devm_regmap_init_i2c` 找不到定义**（99 §4）
2. **真实符号是 14 个 `__regmap_init*` + 15 个 `__devm_regmap_init*`**（01 §3.1）——
   **`__regmap_init_i3c` 不存在，I3C 只有 devm 版**

## §A2 `__regmap_init()` 的九步（`drivers/base/regmap/regmap.c:729`）

### §A2.1 第 ② 步：选锁（层 3）

**优先级从高到低**（`drivers/base/regmap/regmap.c:758-807`）：

| 优先级 | 条件 | 选中的 lock/unlock | 行 |
|---|---|---|---|
| 1 | `config->disable_locking` | `regmap_lock_unlock_none` **两边同一个函数** | `:758` |
| 2 | `config->lock` 非空 | **用调用者给的** | `:762` |
| 3 | `config->use_hwlock` | 按 `hwlock_mode` 三选一 | `:773`–`:783` |
| 4 | `config->fast_io` + `use_raw_spinlock` | `regmap_lock_raw_spinlock` | `:793-794` |
| 5 | `config->fast_io` | `regmap_lock_spinlock` | `:799-800` |
| 6 | **默认** | `regmap_lock_mutex` | `:806-807` |

⚠️ **第 1 步还顺手关掉了 debugfs**：`drivers/base/regmap/regmap.c:760` 一带的
`regmap_debugfs_disable(map)` ⇒ **`disable_locking` 的 regmap 在 debugfs 里看不到**
（07 篇）——因为 debugfs 读寄存器需要加锁。

⚠️ **`use_raw_spinlock` 在这棵树上格外重要**：
**本树可开 `CONFIG_PREEMPT_RT`**（timer 那套 99 §1.1 确认过），
RT 上普通 `spinlock_t` 变可睡 ⇒
**硬中断上下文用的 regmap 必须 `use_raw_spinlock = true`**（05 §1.4）。

### §A2.2 第 ③ 步：算字节数

```c
	map->format.reg_bytes = DIV_ROUND_UP(config->reg_bits, 8);
	map->format.pad_bytes = config->pad_bits / 8;
	map->format.reg_downshift = config->reg_downshift;
	map->format.val_bytes = DIV_ROUND_UP(config->val_bits, 8);
	map->format.buf_size = DIV_ROUND_UP(config->reg_bits + ...);
```
—— `drivers/base/regmap/regmap.c:827-831` 一带

⚠️ **`pad_bits` 用整除而 `reg_bits`/`val_bits` 用向上取整** ——
**但整除的余数没有被丢弃**：

```c
	map->format.pad_bytes = config->pad_bits / 8;
	...
	map->reg_shift = config->pad_bits % 8;
```
—— `drivers/base/regmap/regmap.c:827` 和 `:832`

⇒ **`pad_bits` 被拆成两半**：整字节部分进 `pad_bytes`，
**不足一字节的余数变成 `map->reg_shift`。**

**这就解释了 §A3 那个 switch 为什么是 `switch (config->reg_bits + map->reg_shift)`**：
**"地址位数 + 填充的零头"才是地址字段在总线上真正占的位数。**

⇒ **举例**：`reg_bits=2, pad_bits=0` ⇒ switch 走 `case 2`（配 `val_bits=6`）；
**`reg_bits=7, pad_bits=1` ⇒ `reg_shift=1` ⇒ switch 走 `case 8`** ——
**变成"地址单独一字节"那条路，而不是打包路径。**

⚠️ **所以 `pad_bits` 不是"随便填个对齐值"** ——
**它会改变走哪条格式化路径**（05 §8）。

## §A3 ⚠️ 第 ⑤ 步：格式化函数从硬编码 switch 里挑（本篇最有实用价值的一节）

**`drivers/base/regmap/regmap.c:919-1130` 是两个巨大的 switch**，
**决定"这个位宽组合支不支持"** ——不支持就 `goto err_hwlock` ⇒
**`regmap_init` 返回 `ERR_PTR(-EINVAL)`**。

### §A3.1 第一张表：地址+值打包进一次传输（6 种组合）

**`switch (config->reg_bits + map->reg_shift)` 的前 5 个 case**
—— 这些是"地址和值挤在同一个字里"的芯片：

| `reg_bits` | `val_bits` | 总位数 | 格式化函数 | 位置 |
|---|---|---|---|---|
| 2 | 6 | 8 | `regmap_format_2_6_write` | `drivers/base/regmap/regmap.c:923` |
| 4 | 12 | 16 | `regmap_format_4_12_write` | `:933` |
| 7 | 9 | 16 | `regmap_format_7_9_write` | `:943` |
| 7 | 17 | 24 | `regmap_format_7_17_write` | `:946` |
| 10 | 14 | 24 | `regmap_format_10_14_write` | `:956` |
| 12 | 20 | 32 | `regmap_format_12_20_write` | `:966` |

⇒ **只有这 6 种组合** ——**每个 case 的 `default` 都是 `goto err_hwlock`**
（`:925-926` · `:935-936` · `:948-949` · `:958-959` · `:968-969`）。
**总位数都是 8/16/24/32 之一**（凑整字节）。

⚠️ **这类芯片只能写不能读**：这几个函数都叫 `format_write`，
**没有对应的 `parse_val`** ⇒ **"地址和值打包"的芯片是只写的**
（典型是老的音频编解码器）。

### §A3.2 第二张表：地址单独格式化（5 种位宽）

**`switch (config->reg_bits + map->reg_shift)` 的后 5 个 case**：

| `reg_bits` | 支持的字节序 | 位置 |
|---|---|---|
| **8** | **无字节序问题**（`regmap_format_8`） | `drivers/base/regmap/regmap.c:974` |
| **16** | **BE / LE / NATIVE** 三种全有 | `:980` / `:983` / `:986` |
| ⚠️ **24** | ⚠️ **只有 BE！** | `:996`（`default: goto err_hwlock` 在 `:998-999`） |
| **32** | BE / LE / NATIVE | `:1006` / `:1009` / `:1012` |
| **64** | BE / LE / NATIVE | `:1023` / `:1026` / `:1029` |
| 其它 | **`default: goto err_hwlock`** | `:1037-1038` |

⚠️ **24 位地址只支持大端**是一个真实的不对称 ——
**想用 24 位小端地址，`regmap_init` 直接返回 `-EINVAL`**，
而且**没有任何日志说明原因**（07 §… 见 §C3）。

### §A3.3 第三张表：值的格式化 + 解析（`drivers/base/regmap/regmap.c:1044`）

| `val_bits` | 字节序 | `format_val` | `parse_val` | ⚠️`parse_inplace` |
|---|---|---|---|---|
| **8** | — | ✓ `:1046` | ✓ `:1047` | **✓（noop）`:1048`** |
| **16** BE | BE | ✓ `:1053` | ✓ `:1054` | **✓ `:1055`** |
| **16** LE | LE | ✓ `:1058` | ✓ `:1059` | **✓ `:1060`** |
| ⚠️ **16** NATIVE | NATIVE | ✓ `:1063` | ✓ `:1064` | ⚠️ **✗ 没有！** |
| ⚠️ **24** | **只有 BE** `:1073` | ✓ | ✓ `:1074` | ⚠️ **✗ 没有！** |
| **32** BE / LE | BE `:1083` / LE `:1088` | ✓ | ✓ | **✓ `:1085` / `:1090`** |
| ⚠️ **32** NATIVE | NATIVE `:1093` | ✓ | ✓ `:1094` | ⚠️ **✗** |
| **64** BE / LE | BE `:1104` / LE `:1109` | ✓ | ✓ | **✓ `:1106` / `:1111`** |
| ⚠️ **64** NATIVE | NATIVE `:1114` | ✓ | ✓ `:1115` | ⚠️ **✗** |

⇒ **`parse_inplace` 只在 7 种组合上存在**：
8 · 16BE · 16LE · 32BE · 32LE · 64BE · 64LE。
⚠️ **所有 NATIVE 变体和 24 位都是 NULL** ——
**这个"缺失"有实质后果，见 §B5。**

**复现整张表**：

```bash
awk 'NR>=919 && NR<=1130' drivers/base/regmap/regmap.c \
  | grep -E 'case|format_write|format_reg|format_val|parse_val|parse_inplace|goto err'
```

## §A4 第 ⑦ 步：缓存初始化（层 2）

**`regcache_init()` 按 `config->cache_type` 从一张指针表里挑后端**
（`drivers/base/regmap/regcache.c:18-25` 的 `cache_types[]`，**是 `static const`**）：

| `cache_type` | 后端实例 | 位置 |
|---|---|---|
| `REGCACHE_RBTREE` | `regcache_rbtree_ops` | `drivers/base/regmap/regcache-rbtree.c:542` |
| `REGCACHE_FLAT` | `regcache_flat_ops` | `drivers/base/regmap/regcache-flat.c:76` |
| `REGCACHE_COMPRESSED` | `regcache_lzo_ops` | `drivers/base/regmap/regcache-lzo.c:360` ——⚠️ **在 `#if IS_ENABLED(CONFIG_REGCACHE_COMPRESSED)` 里**（`regcache.c:20-22`）⇒ **没开这个选项时它根本不在表里** |
| `REGCACHE_NONE` | **不建缓存**，`cache_ops` 保持 NULL | — |

**三个实例既不是 `static` 也不是 `const`**，
用 `extern` 声明在 `drivers/base/regmap/internal.h:283-285`（02 §6.1）。

⚠️ **`REGCACHE_FLAT` 会按 `max_register` 分配一整块数组** ⇒
**`max_register` 填得过大会直接 `-ENOMEM`**（01 §9.4、04 篇）。

## §A5 稳态：读写的三道门

02 §3 已画完整数据流。**这里只强调一件容易被忽略的事**：

### §A5.1 ⚠️`_regmap_read()` 里三道门的**顺序**

```
_regmap_read()                    drivers/base/regmap/regmap.c:2884
  ① if (!map->cache_bypass) { 查缓存；命中就 return 0 }   :2890-2893
  ② if (map->cache_only) return -EBUSY                    :2896-2897
  ③ if (!regmap_readable(map, reg)) return -EIO        :2900
  ④ map->reg_read(...)
```

⇒ ⚠️ **`regmap_readable()` 是最后一道门，在查缓存之后！**
**所以"读一个不可读的寄存器"如果命中了缓存，会成功返回。**
**只有缓存未命中时才拿到 `-EIO`。**

⚠️ **这是一个真实的行为不一致**：**同一个非法访问，
开缓存和不开缓存的结果不同** ——03 §C3 会作为设计评价的一项。

**写侧的顺序相反**：`_regmap_write()` 里
**`regmap_writeable()` 检查在最前面**（`drivers/base/regmap/regmap.c:1995`），
在碰缓存之前 ⇒ **写非法寄存器一定报 `-EIO`，不会被缓存掩盖。**

⇒ **读写两侧的检查顺序不对称，这是本篇最值得记的一条内部细节。**

## §A6 电源管理：四个 API 的配合

```
  suspend:
    regcache_cache_only(map, true)    之后的写只落缓存
    regcache_mark_dirty(map)          告诉缓存"硬件要掉电了"
    （芯片掉电）
  resume:
    regcache_cache_only(map, false)   恢复走硬件
    regcache_sync(map)                把脏值全部写回
```

**`cache_only` 期间的写**（02 §3.2）：
`regcache_write()` 之后 `map->cache_dirty = true; return 0;`
（`drivers/base/regmap/regmap.c:2001-2004`）⇒ **不碰硬件，标脏。**

⚠️ **`regcache_mark_dirty()` 不能省** ——
**`regcache_sync()` 只写"脏"的寄存器**；
如果芯片掉过电而缓存不脏，**sync 会认为"一致"而什么都不做** ⇒
**芯片回到上电默认值，驱动却以为配置还在**（05 §3）。

## §A7 拆除

| 场景 | 做什么 |
|---|---|
| `devm_regmap_init_*` | **device 销毁时自动 `regmap_exit()`** |
| `regmap_init_*` | **驱动必须自己 `regmap_exit()`** |
| `regmap_bus.free_context` | **总线适配自己分配的 context 在这里释放** |
| `regmap_bus.free_on_exit` | 连 `regmap_bus` 结构本身也要释放（动态分配的 bus） |

**`free_on_exit`（`include/linux/regmap.h:562`）是给"运行期动态构造 `regmap_bus`"用的** ——
绝大多数适配是 `static const`，不需要它。

---

# Part B：横切机制

## §B1 三套锁，各管一件事

02 §6.2 已列表。**这里补两条实现细节**：

### §B1.1 访问锁是"每次访问一对调用"

**所有对外 API 的形状都是**：

```c
	map->lock(map->lock_arg);
	ret = _regmap_xxx(map, ...);
	map->unlock(map->lock_arg);
```

⇒ **锁的粒度是"一次 API 调用"**，不是"一次总线传输" ——
**所以 `regmap_update_bits()` 的读-改-写是原子的**（相对其它 regmap 调用）。

⚠️ **但它不保护"驱动自己的两次调用之间"** ——
**`regmap_read()` 然后 `regmap_write()` 不是原子的**，
要原子就用 `regmap_update_bits()`（05 §7）。

### §B1.2 `regmap_lock_unlock_none` 一个函数当两用

`drivers/base/regmap/regmap.c:491` 定义了 `regmap_lock_unlock_none()`，
**同时被赋给 `lock` 和 `unlock`**（`:758`）。

⇒ **这是"锁函数指针化"带来的小便利** ——
"什么都不做"两个方向一样，不需要写两个空函数。

## §B2 字节序：三个来源，config 优先

**一个 regmap 的最终字节序由三层决定**：

| 层 | 来源 | 说明 |
|---|---|---|
| 1 | `regmap_config.reg_format_endian` / `val_format_endian` | **驱动明确指定** |
| 2 | `regmap_bus.reg_format_endian_default` / `val_format_endian_default`（`include/linux/regmap.h:558-559`） | **总线的常规值** |
| 3 | **DT 的 `little-endian` / `big-endian` / `native-endian`** | **只对 MMIO 生效**（06 §1） |

⇒ **config 是 `REGMAP_ENDIAN_DEFAULT` 时才看 bus 的默认值。**

⚠️ **这造成一个诊断困难**（02 §2.1）：
**最终生效的字节序不在你的 config 里** ⇒
**要么读 bus 的代码，要么看它有没有走进 §A3 那个 switch 的哪个 case。**

## §B3 缓存的三种后端各适合什么

| 后端 | 数据结构 | 适合 | 位置 |
|---|---|---|---|
| `REGCACHE_RBTREE` | **红黑树**（按"连续块"存） | **默认选择** ——寄存器稀疏也高效 | `drivers/base/regmap/regcache-rbtree.c`(554) |
| `REGCACHE_FLAT` | **一整块数组** | **寄存器号密集且 `max_register` 小** | `drivers/base/regmap/regcache-flat.c`(83) |
| `REGCACHE_COMPRESSED` | **LZO 压缩** | 寄存器极多且内存紧张 | `drivers/base/regmap/regcache-lzo.c`(368) |

**行数本身说明了复杂度**：`FLAT` 只有 83 行（就是个数组），
`RBTREE` 554 行，`LZO` 368 行。

⚠️ **`FLAT` 的内存是 `max_register × val_bytes`** ⇒
**`max_register` 填 0xFFFF 而 `val_bits=32` 就是 256KB**（04 篇）。

## §B4 分页（`ranges`）：驱动完全看不见

**`struct regmap_range_cfg`**（`include/linux/regmap.h:462`，02 §5.6）
描述"虚拟地址范围 → 页选择器 + 窗口"。

**核心在每次访问落在 `range_min..range_max` 时**：
① 算出页号；② **先写 `selector_reg`**；③ 再访问 `window_start + 偏移`。

⇒ **驱动像访问一个平坦的大地址空间一样使用它** ——
**这是 regmap 一个很有价值但很少被提及的抽象**（04 篇展开）。

⚠️ **代价是每次跨页访问多一次总线传输** ——
**核心会缓存"当前页"以避免重复写选择器**。

## §B5 ⚠️`parse_inplace` 为 NULL 的性能悬崖（本篇第二个重要发现）

**`regmap_bulk_read()`（`drivers/base/regmap/regmap.c:3174`）有两条路**：

```
  快路径（:3186）：
    if (map->read && map->format.parse_inplace &&
        (vol || map->cache_type == REGCACHE_NONE)) {
        regmap_raw_read(...)                 ← 一次传输读全部
        for (...) map->format.parse_inplace(val + i)   ← :3192
    }
  慢路径：
    for (i = 0; i < val_count; i++)
        _regmap_read(map, reg + offset, ...)  ← N 次单寄存器读
```

⇒ ⚠️ **`parse_inplace` 为 NULL 就走慢路径** ——
**不报错、不警告，只是慢 N 倍。**

**哪些配置会踩到**（§A3.3 的表）：
**所有 NATIVE 字节序 + 所有 24 位值。**

**写侧同样**：`drivers/base/regmap/regmap.c:2422` 的
`if (!map->write || !map->format.parse_inplace)` 也是退化条件；
`drivers/base/regmap/regmap.c:2647` 的 `regmap_noinc_write` 也检查它。

⇒ **三处检查同一个字段，全部是"没有就退化"而不是"没有就报错"。**

⚠️ **快路径还有两个额外条件**（`:3186`）：
**`vol`（寄存器是 volatile）或者 `cache_type == REGCACHE_NONE`** ——
⇒ **开了缓存且寄存器不是 volatile 时，`bulk_read` 也走慢路径**
（因为要逐个查缓存）。
**这一条比 `parse_inplace` 更常见，而且同样静默。**

## §B6 可观测性：debugfs + 17 个 tracepoint

**和 virtio（0 tracepoint / 0 debugfs）形成鲜明对比**（07 篇展开）：

| 手段 | 规模 |
|---|---|
| debugfs | `drivers/base/regmap/regmap-debugfs.c`(692 行) |
| tracepoint | `drivers/base/regmap/trace.h`(284 行)，**17 个事件**（07 §3） |

**tracepoint 覆盖得很有心思**：
**`regmap_reg_read` 和 `regmap_reg_read_cache` 是两个不同事件**
⇒ **从 trace 直接能算缓存命中率**（07 §3.2）。

⚠️ **但 `disable_locking` 的 regmap 在 debugfs 里看不到**（§A2.1）。

---

# Part C：设计评价

## §C1 源码自陈的设计债：**只有 1 条**

```bash
grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' drivers/base/regmap/*.c | grep -vE '"[^"]*XXX'
```

**10998 行里只有 1 条**：

| 位置 | 内容 |
|---|---|
| `drivers/base/regmap/regmap-irq.c:633` | `FIXME: This is for backward compatibility and should be removed` |

**两条结论**：

1. **核心（`regmap.c` 3529 行 + `regcache*.c` 1832 行）零自陈债** ——
   **和 of（16297 行 1 条）同级，是本项目做过的子系统里最干净的两个**
2. **唯一那条在 `regmap-irq.c`（子框架）里，而且是向后兼容包袱** ——
   **和 timer（债在 posix-timers）、virtio（债在 virtio_ring/virtio_mem）
   是同一个模式：核心干净，债在外围**

## §C2 ⚠️ 位宽组合硬编码：本子系统最大的设计约束

**§A3 那三张表就是全部支持的组合** ——
**不在表里的组合让 `regmap_init` 返回 `ERR_PTR(-EINVAL)`。**

**三条具体的不对称**：

| 不对称 | 影响 |
|---|---|
| ⚠️ **24 位地址只支持 BE**（`:996`） | 24 位 LE 地址的芯片用不了 regmap |
| ⚠️ **24 位值只支持 BE**（`:1073`） | 同上 |
| ⚠️ **所有 NATIVE 变体没有 `parse_inplace`** | **静默退化到慢路径**（§B5） |

**为什么会这样**：**格式化函数是手写的，每个组合一个函数** ——
`regmap_format_2_6_write` / `regmap_format_16_be` / … ⇒
**加一个组合要写一个新函数并加一个 case。**

**评价**：⚠️ **这是一个"用穷举换性能"的选择** ——
**热路径上是一次函数指针调用，没有任何分支** ⇒ 很快；
⚠️ **代价是可支持的组合被冻结在一张表里**，
而且**加新组合的成本不低**。

⇒ **对驱动作者的实际影响**：**芯片位宽不常规时先查 §A3 的表**，
**不在表里就别指望 regmap**（05 §8）。

## §C3 ⚠️"一码多因"和"顺序不对称"

**本子系统有三处诊断上的坑**：

| 坑 | 说明 |
|---|---|
| ⚠️ **`-EIO` 三类来源** | 5 处 I2C 传输异常 + **写不可写（`:1995`）+ 读不可读（`:2900`）**（01 §9.2） |
| ⚠️ **`-EINVAL` 占 52%** | 102/198，三种来源含 **`CONFIG_REGMAP=n` 的空桩**（01 §9.2） |
| ⚠️ **读写检查顺序不对称** | **写检查在最前，读检查在查缓存之后**（§A5.1）⇒ **同一个非法访问，开缓存和不开缓存结果不同** |

**第三条是真正的设计瑕疵**（不只是诊断困难）：
⚠️ **`regmap_read()` 一个不可读的寄存器，开了缓存可能成功、不开缓存必失败。**

**为什么会这样**（推测，99 §5.3 登记）：
**先查缓存是为了性能**（缓存命中就不该做任何总线相关的检查），
⚠️ **但代价是可读性检查被跳过。**

## §C4 可观测性：本项目做过的子系统里最好的之一

| 子系统 | tracepoint | debugfs | procfs | sysfs |
|---|---|---|---|---|
| **regmap** | **17** | **有（692 行）** | 无 | 无 |
| timer | **17** | 无 | **`/proc/timer_list`** | 5（3 可写） |
| ⚠️ virtio | ⚠️ **0** | ⚠️ **0** | ⚠️ **0** | 5（全只读） |
| ⚠️ of | ⚠️ **0** | ⚠️ **0** | 无 | **`/sys/firmware/devicetree/`** |

⇒ **regmap 和 timer 是两个"可观测性做得好"的**，
**而且 regmap 的 debugfs 能直接 dump 寄存器**（07 篇）——
**这是调试驱动时极其有用的能力。**

⚠️ **两个盲区**：
① **`disable_locking` 的 regmap 看不到**（§A2.1）；
② **`precious` 寄存器不被 dump**（02 §1.0.C）——**这是刻意的，不是缺陷。**

## §C5 整体评价

**做得好的四点**：

1. **三层可插拔的开放度分层得当**（02 §1.0.F）：
   总线全开放、锁半开放、缓存全封闭 ——**每一层的开放度都匹配它的通用性**
2. **零全局状态**（02 §6.1）⇒ 多 regmap 天然互不干扰
3. **只有 1 条自陈设计债**（§C1）——10998 行
4. **可观测性优秀**（§C4）

⚠️ **代价和欠债四点**：

1. ⚠️ **位宽组合被硬编码冻结**（§C2）——24 位只能 BE
2. ⚠️ **两处静默性能退化**（§B5）——`parse_inplace` NULL 和"开缓存 + 非 volatile"
3. ⚠️ **读写检查顺序不对称**（§A5.1）——真正的行为不一致
4. ⚠️ **`-EIO` / `-EINVAL` 一码多因**（§C3）

**本子系统最特殊的一点**：
**`struct regmap_config` 一个结构 54 个字段，承载了全部"芯片差异"** ——
⇒ **regmap 的复杂度不在代码里，在配置里。**
**所以读 regmap 的正确顺序是"先读 `regmap_config` 的每个字段"**（01 §2），
**而不是先读 `regmap.c`。**
