---
title: 使用指南
---

# regmap 05：各角色代码骨架

## §0 篇首声明（必读）

⚠️ **本篇所有代码骨架都未经编译、未上机运行。** 它们的用途是**说明调用次序和参数关系**，
不是可直接粘贴的驱动。每个骨架里出现的符号都已在 `linux-6.1.158` 树上逐个 `grep` 确认存在
（清单见 99 §3），但**结构体字段的完整初始化、错误路径的资源回收、Kconfig 依赖都做了删减**。

### §0.1 本子系统有哪些角色

regmap 是**框架型**子系统（无 `bus_type`，见 99 §1），所以角色不是"控制器 / 设备"两分，
而是按**"谁建 regmap、谁用 regmap"**切开。**六个角色，本篇每个一节**：

| # | 角色 | 它要做的事 | 树内数量级 | 本篇 |
|---|---|---|---|---|
| 1 | **自建 regmap 的消费者** | 自己 `devm_regmap_init_mmio()` 建，自己读写 | **绝大多数**（pinctrl/clk/reset 都是） | §1 |
| 2 | **MFD 父设备** | 建 regmap + regmap-irq，供子设备共享 | `drivers/mfd/` **8 个文件**有自定义 bus | §2 |
| 3 | **MFD 子设备驱动** | **不建**，向上拿父设备的 regmap | 很多 | §3 |
| 4 | **`regmap_bus` 实现者** | 提供一种新的"寄存器怎么搬"后端 | **全树 61 个实例**（目录外 39） | §4 |
| 5 | **`regmap_field` 使用者** | 把位域抽成句柄，不再手写掩码 | 中等 | §5 |
| 6 | **带缓存 + 电源管理的驱动** | suspend 走缓存、resume 回灌 | **PMIC / codec 的标配** | §6 |

**角色 1 和角色 3 的区别是本篇最要紧的一条**：
**同一个芯片只能有一个 regmap 建立者**，其余都必须走 `dev_get_regmap()` 拿。
**两个驱动各建一个 regmap 指向同一段寄存器 ⇒ 两份缓存互相不知道对方写了什么**（§3.2）。

### §0.2 按任务找章节

| 我要… | 去 |
|---|---|
| 给一个 MMIO 外设加寄存器访问 | §1 |
| 给一个 I2C 芯片加寄存器访问 | §1.4 |
| 写 MFD 父驱动 | §2 |
| 从父设备拿 regmap | §3 |
| 实现一种新总线的 regmap 后端 | §4 |
| 不想手写 `<< shift & mask` | §5 |
| 芯片掉电后寄存器要恢复 | §6 |
| **两个名字很像的函数该用哪个** | **§7（独立一节）** |
| 常见错误现象 | §8 |

---

## §1 角色 1：自建 regmap 的消费者

**这是最常见的形态**，`drivers/pinctrl/`、`drivers/clk/`、`drivers/reset/` 里大量存在。
真实参照：`drivers/pinctrl/pinctrl-rockchip.c:3430`、`drivers/pinctrl/pinctrl-ocelot.c:2047`
（都是 `devm_regmap_init_mmio`）。

### §1.1 三步骨架

```c
#include <linux/regmap.h>

/* 第 1 步：静态描述这块寄存器长什么样 */
static const struct regmap_config foo_regmap_config = {
	.name		= "foo",	/* 出现在 debugfs 目录名里，见 04 §4.1 */
	.reg_bits	= 32,		/* 寄存器地址占几位 */
	.val_bits	= 32,		/* 一个寄存器的数据宽度 */
	.reg_stride	= 4,		/* 相邻寄存器地址差 4；地址 % 4 != 0 直接 -EINVAL */
	.max_register	= 0x1000,	/* 上界；越界写返回 -EIO 而不是 -EINVAL，见 §8 */
	.cache_type	= REGCACHE_NONE,/* MMIO 通常不需要缓存 */
};

static int foo_probe(struct platform_device *pdev)
{
	struct foo *foo;
	void __iomem *base;

	foo = devm_kzalloc(&pdev->dev, sizeof(*foo), GFP_KERNEL);
	if (!foo)
		return -ENOMEM;

	/* 第 2 步：先拿到 iomem，再交给 regmap */
	base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(base))
		return PTR_ERR(base);

	foo->map = devm_regmap_init_mmio(&pdev->dev, base, &foo_regmap_config);
	if (IS_ERR(foo->map))
		return PTR_ERR(foo->map);	/* 返回 ERR_PTR，不是 NULL */

	/* 第 3 步：正常读写 */
	return regmap_write(foo->map, 0x10, 0xdeadbeef);
}
```

⚠️ **三个必看点**：
1. **`devm_regmap_init_mmio` 是宏不是函数**（`include/linux/regmap.h:1063`），
   它展开成 `__devm_regmap_init_mmio_clk(dev, NULL, regs, config, ...)`，
   ⇒ **`grep devm_regmap_init_mmio` 在 `.c` 里找不到定义是正常的**（索引工具盲区，99 §4）
2. **返回值判断必须用 `IS_ERR()`**，不是 `!foo->map`
3. **`config` 必须是 `static const`**：regmap 核心**不深拷贝整个 config**，
   `writeable_reg` 等函数指针和 `wr_table` 指针都是直接存指针（02 §2）

### §1.2 `regmap_config` 里最容易漏的四个字段

| 字段 | 位置 | 漏了会怎样 |
|---|---|---|
| `max_register` | `include/linux/regmap.h:406` | ⚠️ **不填 = 0 = "不检查上界"**；**但填了之后越界写返回 `-EIO`（像硬件错），不是 `-EINVAL`** ——`drivers/base/regmap/regmap.c:1994-1995` |
| `reg_stride` | `:374` | **不填 = 0，核心当 1 处理**；填 4 之后地址不对齐直接失败 |
| `volatile_reg` | `:382` | ⚠️ **开了缓存却不标 volatile ⇒ 状态寄存器被缓存住，永远读到旧值**（04 §2.4） |
| `fast_io` | `:403` | **决定用 spinlock 还是 mutex**；**中断上下文里读写必须置 true**（03 Part B） |

### §1.3 常用读写四件套

```c
	unsigned int val;

	regmap_read(map, FOO_STATUS, &val);		/* 值走出参，返回值是错误码 */
	regmap_write(map, FOO_CTRL, 0x1);
	regmap_update_bits(map, FOO_CTRL, FOO_EN_MASK, FOO_EN_MASK);  /* 读-改-写 */

	/* 轮询等硬件就绪：宏，不是函数（include/linux/regmap.h:104） */
	ret = regmap_read_poll_timeout(map, FOO_STATUS, val,
				       val & FOO_READY, 100, 10000);
	if (ret)			/* 超时返回 -ETIMEDOUT */
		return ret;
```

⚠️ **`regmap_read_poll_timeout` 会 sleep**（内部 `usleep_range`）。
**原子上下文用 `regmap_read_poll_timeout_atomic`**（`:134`），
**它要求 `fast_io = true`**（否则 mutex 在原子上下文里睡死）。

### §1.4 换成 I2C 芯片：只改一行

```c
static int foo_i2c_probe(struct i2c_client *client)
{
	struct regmap *map;

	map = devm_regmap_init_i2c(client, &foo_i2c_regmap_config);
	/* ↑ include/linux/regmap.h:949；其余读写代码一个字不用改 */
	if (IS_ERR(map))
		return PTR_ERR(map);
	...
}
```

**这就是 regmap 存在的全部意义**：`regmap_write()` 之后的所有代码与总线无关。
**本树共 12 种通用适配 / 22 个 `regmap_bus` 实例**（04 §6），
**初始化入口 14 个 `regmap_init_*` + 15 个 `devm_regmap_init_*`**。

⚠️ **唯一的不对称：`devm_regmap_init_i3c` 存在（`:1132`），但 `regmap_init_i3c` 不存在。**
**I3C 只有 devm 版**，写非 devm 版编译不过（99 §3 已复核）。

---

## §2 角色 2：MFD 父设备（建 regmap + 中断）

真实参照：`drivers/mfd/rk808.c`（arm64 Rockchip 板上大量使用）。

### §2.1 中断部分的骨架

```c
/* 一、把每根中断线映射到 (寄存器偏移, 位掩码) */
static const struct regmap_irq rk808_irqs[] = {
	REGMAP_IRQ_REG_LINE(0, 8),	/* include/linux/regmap.h:1440 */
	REGMAP_IRQ_REG_LINE(1, 8),
	/* … 真实实例见 drivers/mfd/rk808.c:471-490 */
};

/* 二、描述这颗芯片的中断寄存器组 */
static const struct regmap_irq_chip rk808_irq_chip = {
	.name		= "rk808",
	.irqs		= rk808_irqs,
	.num_irqs	= ARRAY_SIZE(rk808_irqs),
	.num_regs	= 1,
	.status_base	= RK808_INT_STS_REG1,
	.mask_base	= RK808_INT_STS_MSK_REG1,
	.ack_base	= RK808_INT_STS_REG1,
	.init_ack_masked = true,	/* 上电先清一遍，避免开机就炸中断 */
};

/* 三、probe 里注册 */
	ret = devm_regmap_add_irq_chip(&client->dev, map, client->irq,
				       IRQF_ONESHOT, -1,	/* -1 = 动态分配 irq_base */
				       &rk808_irq_chip, &irq_data);
	/* ↑ drivers/base/regmap/regmap-irq.c:1245 处导出 */
	if (ret)
		return ret;
```

⚠️ **`REGMAP_IRQ_REG_LINE(_id, _reg_bits)` 的两个字段是算出来的**
（`include/linux/regmap.h:1440-1444`）：`.mask = BIT(_id % _reg_bits)`、`.reg_offset = _id / _reg_bits`。
⇒ **它只适用于"中断位在寄存器里连续密排"的芯片**；
**有空洞的芯片必须用 `REGMAP_IRQ_REG(_irq, _off, _mask)`（`:1437`）手写掩码**。

### §2.2 子设备怎么拿到 virq

```c
	/* 父设备把 irq_data 存起来，或者子设备自己查 */
	int virq = regmap_irq_get_virq(irq_data, RK808_IRQ_RTC_ALARM);
	/* ↑ drivers/base/regmap/regmap-irq.c:1300 处导出 */
	if (virq < 0)
		return virq;
```

**更常见的做法是走 `irq_domain`**：`regmap_irq_get_domain(irq_data)`
（`regmap-irq.c:1319`）交给 MFD 子设备的 `mfd_cell.domain`，
子设备就能用标准的 `platform_get_irq()` 拿号，完全看不见 regmap-irq 的存在。

⚠️ **`regmap_irq_chip_get_base()`（`:1282`）是遗留接口**：它返回 `irq_base`，
**只在 `irq_base` 静态分配（不是 -1）时有意义**，新驱动不要用。

---

## §3 角色 3：MFD 子设备驱动（不建，只拿）

### §3.1 两种拿法

```c
static int foo_sub_probe(struct platform_device *pdev)
{
	struct regmap *map;

	/* 拿法 A：父设备把 regmap 放在 drvdata 里（最常见） */
	struct rk808 *rk808 = dev_get_drvdata(pdev->dev.parent);
	map = rk808->regmap;

	/* 拿法 B：按名字向上查（regmap 需先 regmap_attach_dev 过） */
	map = dev_get_regmap(pdev->dev.parent, NULL);
	/* ↑ drivers/base/regmap/regmap.c:1615 处导出；第二参数是 config.name */
	if (!map)
		return -ENODEV;		/* 返回 NULL，不是 ERR_PTR —— 和 §1 相反 */
	...
}
```

⚠️ **返回值语义在这里翻转了，是本篇最容易写错的一处**：

| 函数 | 失败返回 | 判断方式 |
|---|---|---|
| `devm_regmap_init_*` | **`ERR_PTR(-E…)`** | `IS_ERR(map)` |
| `dev_get_regmap` | **`NULL`** | **`!map`** |

**对 `dev_get_regmap` 的返回值用 `IS_ERR()` ⇒ NULL 通过检查 ⇒ 下一次 `regmap_read` 空指针崩溃。**

### §3.2 ⚠️ 子设备绝对不要自己再建一个 regmap

**同一段寄存器建两个 regmap 会出现的现象**：

| 现象 | 原因 |
|---|---|
| **写了不生效 / 读到旧值** | **两份独立缓存**，A 的写只更新 A 的缓存，B 读自己的缓存 |
| **锁失效** | **regmap 的锁是 per-map 的**（03 Part B），两个 map 互不排斥 ⇒ 读-改-写竞争 |
| **debugfs 里出现两个同名目录** | 04 §4.1 |

**判据很简单：一颗芯片、一个 regmap。** 需要区分不同页 / 不同地址段时，
**用 `regmap_config.ranges` 分页**（04 §3），**不是**建第二个 regmap。

---

## §4 角色 4：`regmap_bus` 实现者

这是**层 1 可插拔点**（02 §1.0）。**全树 61 个实例，目录外 39 个**——
**说明这是一个真正开放的扩展点**，不是"只有核心自己实现"。

### §4.1 最简实现只需两个回调

真实参照 `drivers/base/regmap/regmap-i2c.c:14-49`，**完整照抄的骨架**：

```c
static int foo_reg_read(void *context, unsigned int reg, unsigned int *val)
{
	struct device *dev = context;		/* context 是建立时传进来的 bus_context */
	int ret;

	if (reg > 0xff)
		return -EINVAL;

	ret = foo_hw_read(dev, reg);
	if (ret < 0)
		return ret;
	*val = ret;
	return 0;
}

static int foo_reg_write(void *context, unsigned int reg, unsigned int val)
{
	struct device *dev = context;

	if (val > 0xff || reg > 0xff)
		return -EINVAL;
	return foo_hw_write(dev, reg, val);
}

static const struct regmap_bus foo_regmap_bus = {
	.reg_write = foo_reg_write,
	.reg_read  = foo_reg_read,
};

/* 用它建 map：走通用入口，不是某个具体总线的入口 */
	map = devm_regmap_init(dev, &foo_regmap_bus, dev, &foo_config);
	/*                                            ↑ 这个 dev 就是回调里的 context */
```

`struct regmap_bus` 共 **18 个字段**（`include/linux/regmap.h:545-562`），
**但只有 `reg_read`/`reg_write` 这一对是"最小可用集"**。

### §4.2 18 个字段分成六组（2+3+2+1+4+6，填满对得上）

| 组 | 字段 | 什么时候需要 |
|---|---|---|
| **单寄存器（最小集）** | `reg_read` `reg_write` | **必填其一**；SMBus 这类"一次一个寄存器"的总线只填这两个 |
| **批量 / 裸传输** | `write` `gather_write` `read` | **总线支持连续多寄存器传输时填**；不填 ⇒ `regmap_bulk_read` 退化成循环单读 |
| **不自增（FIFO）** | `reg_noinc_read` `reg_noinc_write` | **地址不自增的 FIFO 寄存器**；不填则 `regmap_noinc_*` 返回失败 |
| **硬件原子读改写** | `reg_update_bits` | **总线本身支持原子位修改时填**；⚠️ **填了它会改变 `regmap_update_bits_check` 的语义**（08 §7） |
| **异步 / 生命周期** | `async_write` `async_alloc` `free_context` `free_on_exit` | SPI 这类支持异步提交的总线（01 §6） |
| **格式默认值** | `read_flag_mask` `reg_format_endian_default` `val_format_endian_default` `max_raw_read` `max_raw_write` `fast_io` | **它们是"总线级默认"，会被 `regmap_config` 里的同名字段覆盖**（03 Part A） |

⚠️ **`bus->fast_io`（`:545`）和 `config->fast_io`（`:403`）是两个字段。**
**两者取或**——总线说自己是 fast_io，驱动就无法退回 mutex。

---

## §5 角色 5：`regmap_field` 使用者（位域）

### §5.1 问题：手写掩码很容易错

```c
	/* 不用 field 的写法：每处都要重复 shift 和 mask */
	regmap_update_bits(map, FOO_CTRL, GENMASK(6, 4), 0x3 << 4);
```

**用 field 之后**：

```c
	static const struct reg_field foo_mode = REG_FIELD(FOO_CTRL, 4, 6);
	struct regmap_field *fmode;

	fmode = devm_regmap_field_alloc(dev, map, foo_mode);
	/* ↑ include/linux/regmap.h:1304；实现在 regmap.c:1344 处导出 */
	if (IS_ERR(fmode))
		return PTR_ERR(fmode);

	regmap_field_write(fmode, 0x3);		/* 值就是位域里的值，不用 shift */
	regmap_field_read(fmode, &val);		/* 读出来也已经右移过 */
```

### §5.2 批量分配（一次分配一组 field）

```c
	static const struct reg_field foo_fields[] = {
		[FOO_F_MODE]  = REG_FIELD(FOO_CTRL, 4, 6),
		[FOO_F_ENABLE]= REG_FIELD(FOO_CTRL, 0, 0),
	};
	struct regmap_field **fields;

	ret = devm_regmap_field_bulk_alloc(dev, map, &fields, foo_fields,
					  ARRAY_SIZE(foo_fields));
	/* ↑ include/linux/regmap.h:1313；实现在 regmap.c:1414 处导出 */
	if (ret)
		return ret;

	regmap_field_write(fields[FOO_F_MODE], 0x3);
```

⚠️ **`fields` 是 `struct regmap_field **`（二级指针）**，传的是 `&fields`。
**传成一级指针能编译过但会写坏栈**——这是 field 家族最阴的一个坑。

### §5.3 `id_offset` / `id_size`：一组同构位域

`struct reg_field` 有两个专门给"同一位域在 N 个寄存器里重复出现"用的字段（02 §3）：

```c
	/* 8 个通道，每个通道一个寄存器，寄存器间隔 4，位域都在 [3:0] */
	static const struct reg_field ch_gain =
		REG_FIELD_ID(FOO_CH0_GAIN, 0, 3, /* id_size */ 8, /* id_offset */ 4);

	regmap_fields_write(fgain, /* id */ 3, 0x7);	/* 操作第 3 个通道 */
```

⚠️ **单数 / 复数是两套 API**：`regmap_field_write()` 不带 id，
**`regmap_fields_write()` 带 id**（多一个 `s`）。详见 §7 组 4。

---

## §6 角色 6：缓存 + 电源管理

**这是 PMIC / audio codec 驱动的标配形态**，也是缓存机制唯一的正当用途。

### §6.1 四步时序

```c
/* 0. config 里开缓存 + 标出 volatile 寄存器 */
static const struct regmap_config foo_config = {
	...
	.cache_type	= REGCACHE_RBTREE,	/* 本版本只有 4 个取值，见下方警告 */
	.reg_defaults	= foo_reg_defaults,	/* 上电默认值表，不填的后果见 04 §2.4 */
	.num_reg_defaults = ARRAY_SIZE(foo_reg_defaults),
	.volatile_reg	= foo_volatile_reg,	/* 状态寄存器必须在这里返回 true */
};

/* 1. suspend：切成"只写缓存不碰硬件" */
static int foo_suspend(struct device *dev)
{
	struct foo *foo = dev_get_drvdata(dev);

	regcache_cache_only(foo->map, true);	/* regcache.c:536 */
	regcache_mark_dirty(foo->map);		/* regcache.c:560 —— 别漏 */
	foo_power_off(foo);
	return 0;
}

/* 2. resume：先出缓存模式，再回灌 */
static int foo_resume(struct device *dev)
{
	struct foo *foo = dev_get_drvdata(dev);

	foo_power_on(foo);
	regcache_cache_only(foo->map, false);	/* 顺序：先关 cache_only */
	return regcache_sync(foo->map);		/* regcache.c:350 —— 把脏值写回硬件 */
}
```

⚠️ **`cache_type` 在本版本只有四个合法取值**（`include/linux/regmap.h:41-46`）：
`REGCACHE_NONE` / `REGCACHE_RBTREE` / `REGCACHE_COMPRESSED` / `REGCACHE_FLAT`。
**抄新内核的代码时注意：更高版本引入的 `REGCACHE_MAPLE` 在 6.1 里不存在**
（全树 `grep REGCACHE_MAPLE` 命中 **0**），写了编译不过。三个后端怎么选见 04 §2.2。

### §6.2 ⚠️`regcache_mark_dirty()` 漏了会怎样

**源码原话**（`drivers/base/regmap/regcache.c:556-558` 的注释）：
> *If this function is not called, `regcache_sync()` will assume that the hardware state
> still matches the cache state, modulo any writes that happened when cache_only was true.*

⇒ **芯片真掉过电、但没 `mark_dirty` ⇒ `regcache_sync()` 认为硬件还是老样子 ⇒ 只回灌
"cache_only 期间被写过的那几个" ⇒ 其余寄存器留在芯片的上电默认值上。**
**现象是"resume 后一部分功能失效、一部分正常"**，最难查的一类。

⚠️ **`regcache_mark_dirty()` 还顺带置了 `no_sync_defaults = true`**
（`regcache.c:564`）⇒ **和默认值相同的寄存器会被 sync 跳过**。
**芯片的真实上电值与 `reg_defaults` 表不一致时，这些寄存器就永远同步不上**。

### §6.3 局部操作

```c
	regcache_sync_region(map, 0x10, 0x1f);	/* regcache.c:446 —— 只回灌一段 */
	regcache_drop_region(map, 0x10, 0x1f);	/* regcache.c:504 —— 丢弃一段缓存，下次读走硬件 */
```

**`regcache_drop_region()` 的典型用途**：芯片某个模块被单独复位过，
**只有那一段的缓存需要作废**，不必整体 `mark_dirty`。

---

## §7 近邻符号区分（独立一节，必读）

regmap 的 API 里"名字差一两个字、语义完全不同"的组特别多。
**本节六组，每组末尾都写"错用会怎样"**。

### §7.1 组 1：`cache_only` vs `cache_bypass`（最危险的一组）

| | `regcache_cache_only(map, true)` | `regcache_cache_bypass(map, true)` |
|---|---|---|
| 写的去向 | **只进缓存，不碰硬件** | **只进硬件，不进缓存** |
| 位置 | `regcache.c:536` | `regcache.c:580` |
| 用途 | **suspend 期间假装设备还在** | **`regcache_sync()` 内部回灌时自用** |
| 名字直觉 | "只用缓存" ✔ | **"绕过缓存"** ✔ |

⚠️ **两者互斥，而且核心用 `WARN_ON` 抓**：
`regcache_cache_only()` 里 `WARN_ON(… map->cache_bypass && enable)`（`regcache.c:539-540`），
`regcache_cache_bypass()` 里 `WARN_ON(map->cache_only && enable)`（`regcache.c:583`）。

**错用会怎样**：suspend 里写成 `cache_bypass(true)` ⇒
**写请求直接打到已断电的芯片上** ⇒ 总线超时 / `-EIO` 刷屏，
**而且缓存里什么都没记下，resume 后 sync 无从恢复**。

### §7.2 组 2：`regmap_update_bits` vs `regmap_write_bits` vs `regmap_force_*`

**四个都是 `regmap_update_bits_base()`（`regmap.c:3317` 处导出）的 inline 包装**，
**差别只在最后两个布尔参数 `async` / `force`**：

| API | 位置 | 语义 |
|---|---|---|
| `regmap_update_bits` | `include/linux/regmap.h:1184` | **值没变就不写**（读-比较-写） |
| `regmap_write_bits` | `:1213` | **无条件写**（`force = true`）——**写"写 1 清零"型寄存器必须用这个** |
| `regmap_update_bits_async` | `:1190` | 异步提交，需 `regmap_async_complete()` 收尾 |
| `regmap_update_bits_check` | `:1196` | 多一个出参告诉你"到底改没改" |

⚠️ **错用会怎样**：对"写 1 清中断"的寄存器用 `regmap_update_bits()` ⇒
**待清的位读出来已经是 1、和要写的值相同 ⇒ 核心判断"无变化"⇒ 根本不发写 ⇒ 中断永远清不掉。**
**这类寄存器必须同时标 `volatile_reg` 并用 `regmap_write_bits()`。**

### §7.3 组 3：`bulk` vs `raw` vs `noinc`

| API | 位置 | 语义 |
|---|---|---|
| `regmap_bulk_read` | `include/linux/regmap.h:1178` | **读 N 个连续寄存器，按 `val_bits` 解析成主机字节序数组** |
| `regmap_raw_read` | `:1174` | **读原始字节，不做字节序转换**——**长度单位是字节，不是寄存器个数** |
| `regmap_noinc_read` | `:1176` | **地址不自增**，反复读同一个 FIFO 寄存器 |

⚠️ **错用会怎样**：把 `raw` 当 `bulk` 用 ⇒ 长度参数含义错（字节 vs 个数）
**+ 大小端芯片上数值全错**；**在开了缓存的 map 上用 `raw_*` 会绕过缓存**（01 §5）。

### §7.4 组 4：单数 field vs 复数 fields

| API | 位置 | 参数 |
|---|---|---|
| `regmap_field_read` | `:1320`（导出于 `regmap.c:3128`） | `(field, *val)` |
| `regmap_fields_read` | 导出于 `regmap.c:3161` | `(field, id, *val)` ——**多一个 id** |

**复数版是给 `REG_FIELD_ID()`（带 `id_size`/`id_offset`）声明的一组同构位域用的**（§5.3）。
⚠️ **错用会怎样**：对普通 field 调复数版并传 `id != 0` ⇒
**地址被加上 `id * id_offset` ⇒ 读写打到完全无关的寄存器上**（而且不报错）。

### §7.5 组 5：`regmap_init_*` vs `devm_regmap_init_*`

| | 释放方式 | 位置举例 |
|---|---|---|
| `regmap_init_i2c` | **必须自己 `regmap_exit()`** | `include/linux/regmap.h:745` |
| `devm_regmap_init_i2c` | **设备卸载时自动释放** | `:949` |

⚠️ **两个不对称点**（99 §3 已复核）：
1. **`devm_regmap_init_i3c` 存在（`:1132`），`regmap_init_i3c` 不存在**
2. **`regmap_init_slimbus`（`:784`）和 devm 版（`:1118`）都有**，别按 I3C 的印象推广

**错用会怎样**：非 devm 版忘了 `regmap_exit()` ⇒
**内存泄漏 + debugfs 目录残留**（下次 probe 出现 `foo-1` 这种带后缀的目录名）。

### §7.6 组 6：`dev_get_regmap` vs `regmap_get_device`

| API | 位置 | 方向 |
|---|---|---|
| `dev_get_regmap(dev, name)` | `regmap.c:1615` 处导出 | **device → regmap** |
| `regmap_get_device(map)` | `regmap.c:1628` 处导出 | **regmap → device**（反方向） |

**名字里 `dev_` 和 `regmap_` 哪个在前，就代表输入是哪一个**。
⚠️ **错用会怎样**：编译期类型不同，通常编不过——**这一组是本节唯一"编译器会救你"的**。

---

## §8 常见错误现象 → 原因

| 现象 | 最可能的原因 | 去哪查 |
|---|---|---|
| `devm_regmap_init_*` 返回 `-EINVAL` | **`reg_bits` 为 0 / `val_bits` 为 0 / 缓存类型与 `reg_defaults` 不匹配** | 01 §9 |
| ⚠️ **写返回 `-EIO`，但总线是好的** | **写了不可写寄存器**：`reg > max_register` 或 `wr_table` 拦了（`regmap.c:1994-1995`） | 01 §9 / 07 |
| 所有 regmap 调用 `WARN_ONCE` + `-EINVAL` | **`CONFIG_REGMAP=n`，链接到了空桩** | 01 §9 |
| 读到的值永远不变 | **开了缓存但没标 `volatile_reg`** | 04 §2.4 |
| resume 后部分功能失效 | **漏了 `regcache_mark_dirty()`** | §6.2 |
| 中断清不掉 | **用了 `regmap_update_bits` 而非 `regmap_write_bits`** | §7.2 |
| 地址不对齐报错 | **`reg_stride` 与传入地址不匹配** | §1.2 |
| debugfs 里出现两个同名目录 | **同一芯片建了两个 regmap** | §3.2 |

---

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| 这些属性在 DTS 里怎么写 | `06-devicetree.md` |
| 出问题了看什么输出 | `07-debug.md` |
| 为什么设计成这样 | `08-feynman.md` |
| `regmap_config` 每个字段的完整语义 | `01-api.md` §2 |
| 缓存 / 分页 / 中断的内部机制 | `04-extensions.md` |

