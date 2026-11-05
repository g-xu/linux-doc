---
title: 使用指南
---

# iio 使用示例：各角色的代码骨架

## §0 读前必读

### §0.1 骨架未编译

**本篇所有代码骨架都没有编译过，也没有在硬件上运行过。** 它们的用途是"告诉你哪些字段必须填、哪些函数必须成对调用、顺序不能反"，不是"复制粘贴就能工作"。

具体地说，本篇保证的和不保证的：

| 保证 | 不保证 |
|---|---|
| 出现的每个函数名/宏名/标志名都在 6.1.158 里存在（逐个 grep 过，口径见 §8） | 参数个数与顺序完全正确（部分 API 有 `_ext` 变体，形参数量不同） |
| 调用顺序与成对关系符合核心的要求（依据是 03 的时间线） | `#include` 清单完整 |
| 每个符号的"读者能不能用"结论正确（判据见 §0.3） | 错误处理路径覆盖全部分支 |
| 提到的 sysfs 路径和内核打印字符串都逐字回源码核对过 | 骨架能通过 `checkpatch.pl` |

**未编译这件事在 `99-evidence.md` §5.1 也登记了一遍**，两处都写是故意的——只放在 99 里等于没人看见。

### §0.2 本子系统有哪些角色

IIO 的角色比多数子系统多，因为它同时是"设备提供方框架"和"内核内服务框架"。七个角色，每个一节：

| 角色 | 你在写什么 | 章节 | 典型代码量 |
|---|---|---|---|
| A | **sysfs-only 传感器驱动**（一次读一个值，无缓冲区） | §1 | 150–300 行 |
| B | **触发缓冲区传感器驱动**（连续采样） | §2 | A + 100 行 |
| C | **内核内消费者**：读别人的一个通道 | §3 | 20 行 |
| D | **内核内消费者**：收别人的缓冲区数据流 | §4 | 60 行 |
| E | **触发器提供方** | §5 | 100–200 行 |
| F | **缓冲区后端实现者** | §6 | 300+ 行（全树只有 4 个，你大概不需要） |
| G | **用户态程序**（sysfs + chrdev） | §7 | — |

**A 和 B 不是二选一**：B 是 A 加上缓冲区那一层，A 的全部内容在 B 里都还要写。C 和 D 也不是二选一——D 用于"我要连续的数据流"，C 用于"我要现在这一个值"。

### §0.3 "符号存在"和"你能用"是两条判据

本篇每个符号都过了两道检查：

1. **存在**：全树 grep 有定义
2. **能用**：`EXPORT_SYMBOL*` **且**在 `include/linux/iio/` 下有声明——或者它本身就是头文件里的宏 / `static inline`

第二条比第一条严。IIO 里有一批"看起来像公开 API 但不是"的东西，还有四个**只有声明、根本没有定义**的幽灵函数（`04-extensions.md` §5.4 的 D7）。**这四个名字本篇不会出现在任何骨架里，但会在 §8.4 点名**，因为它们就躺在你会 include 的头文件里。

### §0.4 一个必须先说的坑：一半的入口是宏

IIO 把 `THIS_MODULE` / `dev` 这类参数藏在宏里，所以你写的名字和 `EXPORT_SYMBOL` 的名字不一样。**这直接决定了你 grep 不到调用者、索引工具也查不到**（`04-extensions.md` §5.5 有同类案例）：

| 你写的 | 实际是 | 定义位置 | 真正导出的符号 |
|---|---|---|---|
| `iio_device_register(indio_dev)` | 宏 | `include/linux/iio/iio.h:608` | `__iio_device_register` |
| `devm_iio_device_register(dev, indio_dev)` | 宏 | `include/linux/iio/iio.h:625` | `__devm_iio_device_register` |
| `iio_trigger_alloc(parent, fmt, ...)` | 宏 | `include/linux/iio/trigger.h:165` | `__iio_trigger_alloc` |
| `devm_iio_trigger_alloc(parent, fmt, ...)` | 宏 | **`include/linux/iio/iio.h:735`** | `__devm_iio_trigger_alloc` |
| `iio_triggered_buffer_setup(...)` | 宏 | `include/linux/iio/triggered_buffer.h:20` | `iio_triggered_buffer_setup_ext` |
| `devm_iio_triggered_buffer_setup(...)` | 宏 | `include/linux/iio/triggered_buffer.h:33` | `devm_iio_triggered_buffer_setup_ext` |
| `devm_iio_kfifo_buffer_setup(...)` | 宏 | `include/linux/iio/kfifo_buf.h:18` | `devm_iio_kfifo_buffer_setup_ext` |

**第四行是个真实的踩坑点**：`iio_trigger_alloc` 在 `trigger.h`，`devm_iio_trigger_alloc` 在 `iio.h`。一对同族 API 拆在两个头文件里，只 include 了 `trigger.h` 时报的是"未声明"而不是"找不到头文件"。

另外四个是 `static inline`（不是宏，也没有 `EXPORT_SYMBOL`，但可以放心用）：

| 符号 | 位置 |
|---|---|
| `iio_priv()` | `include/linux/iio/iio.h:727-730` |
| `iio_push_to_buffers_with_timestamp()` | `include/linux/iio/buffer.h:37` |
| `iio_trigger_set_drvdata()` | `include/linux/iio/trigger.h:114-117` |
| `iio_trigger_get_drvdata()` | `include/linux/iio/trigger.h:125-128` |

### §0.5 按任务找章节

| 我要做的事 | 去 |
|---|---|
| 写一个只需要 `cat /sys/.../in_voltage0_raw` 的驱动 | §1 |
| 让这个驱动支持连续采样 | §2（先读完 §1） |
| 在我的 thermal / power 驱动里读一个 ADC 通道 | §3 |
| 我要拿到连续的数据流，不是单次值 | §4 |
| 我有个中断/定时器想当触发源 | §5 |
| 搞清楚哪两个名字很像的 API 不能混用 | **§8** |
| 用户态怎么读 | §7 |

---

## §1 角色 A：sysfs-only 传感器驱动

### §1.1 最小清单

一个只支持"读一个值"的 IIO 驱动，必须做完这七件事，缺一件的后果都写在右列：

| # | 动作 | 用什么 | 漏了会怎样 |
|---|---|---|---|
| 1 | 分配 `iio_dev` | `devm_iio_device_alloc(dev, sizeof(*st))` | — |
| 2 | 取私有数据指针 | `iio_priv(indio_dev)` | — |
| 3 | 填 `indio_dev->name` | 直接赋值 | sysfs 里 `name` 属性缺失，用户态按名字找设备就找不到 |
| 4 | 填 `indio_dev->info` | 指向你的 `struct iio_info` | **注册就会失败**（03 时间线里核心会检查） |
| 5 | 填 `channels` / `num_channels` | 指向你的 `iio_chan_spec[]` | 注册成功但一个通道属性都没有，sysfs 目录是空的 |
| 6 | 实现 `.read_raw` | `struct iio_info` 的成员 | 通道属性文件存在，读它返回错误 |
| 7 | 注册 | `devm_iio_device_register(dev, indio_dev)` | 设备不出现 |

第 3 步有个容易忽略的细节：`indio_dev->modes` **不需要**你填，sysfs-only 场景下核心自己有默认值（`INDIO_DIRECT_MODE`，定义在 `include/linux/iio/iio.h:367-372` 那组里）。角色 B 才需要动它，而且是由 `iio_triggered_buffer_setup` 代填（`04-extensions.md` §2.1）。

### §1.2 骨架

```c
#include <linux/iio/iio.h>
#include <linux/iio/types.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>

struct mysensor {
	struct regmap	*regmap;
	struct mutex	lock;	/* 保护对硬件的一次多寄存器读 */
};

/* 通道表：两个单端电压 + 一个温度 */
static const struct iio_chan_spec mysensor_channels[] = {
	{
		.type = IIO_VOLTAGE,
		.indexed = 1,
		.channel = 0,
		.info_mask_separate    = BIT(IIO_CHAN_INFO_RAW),
		.info_mask_shared_by_type = BIT(IIO_CHAN_INFO_SCALE),
	}, {
		.type = IIO_VOLTAGE,
		.indexed = 1,
		.channel = 1,
		.info_mask_separate    = BIT(IIO_CHAN_INFO_RAW),
		.info_mask_shared_by_type = BIT(IIO_CHAN_INFO_SCALE),
	}, {
		.type = IIO_TEMP,
		.info_mask_separate = BIT(IIO_CHAN_INFO_PROCESSED),
	},
};

static int mysensor_read_raw(struct iio_dev *indio_dev,
			     struct iio_chan_spec const *chan,
			     int *val, int *val2, long mask)
{
	struct mysensor *st = iio_priv(indio_dev);
	unsigned int reg;
	int ret;

	switch (mask) {
	case IIO_CHAN_INFO_RAW:
		mutex_lock(&st->lock);
		ret = regmap_read(st->regmap, chan->channel, &reg);
		mutex_unlock(&st->lock);
		if (ret)
			return ret;
		*val = reg;
		return IIO_VAL_INT;

	case IIO_CHAN_INFO_SCALE:
		/* 1.8V 参考电压 / 12 bit：写成分数，核心自己算 */
		*val = 1800;
		*val2 = 12;
		return IIO_VAL_FRACTIONAL_LOG2;

	case IIO_CHAN_INFO_PROCESSED:
		/* 温度：已经是 milli-degree，直接给整数 */
		mutex_lock(&st->lock);
		ret = regmap_read(st->regmap, 0x10, &reg);
		mutex_unlock(&st->lock);
		if (ret)
			return ret;
		*val = reg * 125;
		return IIO_VAL_INT;

	default:
		return -EINVAL;
	}
}

static const struct iio_info mysensor_info = {
	.read_raw = mysensor_read_raw,
};

static int mysensor_probe(struct platform_device *pdev)
{
	struct iio_dev *indio_dev;
	struct mysensor *st;

	indio_dev = devm_iio_device_alloc(&pdev->dev, sizeof(*st));
	if (!indio_dev)
		return -ENOMEM;

	st = iio_priv(indio_dev);
	mutex_init(&st->lock);
	/* st->regmap = ... 你自己的初始化 */

	indio_dev->name = "mysensor";
	indio_dev->info = &mysensor_info;
	indio_dev->channels = mysensor_channels;
	indio_dev->num_channels = ARRAY_SIZE(mysensor_channels);

	return devm_iio_device_register(&pdev->dev, indio_dev);
}
```

**没有 remove 函数**是故意的：两个 `devm_` 版本已经把释放挂进 devres。非 devm 路径要自己配对 `iio_device_alloc` / `iio_device_free`、`iio_device_unregister`，顺序见 §8.1。

### §1.3 `info_mask_*` 怎么变成 sysfs 文件名

这是新手最迷惑的一段：你只写了几个 `BIT(...)`，`/sys/bus/iio/devices/iio:device0/` 里就长出一堆文件。名字的组装规则在 `__iio_device_attr_init()`（`drivers/iio/industrialio-core.c:1022-1135`），拼出来的形状是：

```
<方向>_<类型><序号>_<修饰符>_<extend_name>_<后缀>
  ↑      ↑      ↑        ↑          ↑          ↑
 :49-52  :54起  .channel  :91起  .extend_name  :146起
 in/out  voltage  0       x/y/z    （可选）     raw/scale/…
```

四个字典表都是 `industrialio-core.c` 里的静态数组，可以直接查：

| 部分 | 数组 | 位置 | 由什么决定 |
|---|---|---|---|
| 方向 | `iio_direction[]` | `:49-52`（`[0]="in"`, `[1]="out"`） | `chan->output` |
| 类型 | `iio_chan_type_name_spec[]` | `:54` 起（`IIO_VOLTAGE`→"voltage" `:55`，`IIO_TEMP`→"temp" `:64`，`IIO_ACCEL`→"accel" `:58`） | `chan->type` |
| 修饰符 | `iio_modifier_names[]` | `:93` 起（`IIO_MOD_X`→"x"） | `chan->channel2`，且必须 `chan->modified = 1` |
| 后缀 | `iio_chan_info_postfix[]` | `:146` 起 | 你在 `info_mask_*` 里置的位 |

后缀表里最该记住的四个，因为它们对应四个完全不同的语义：

| 位 | 生成的后缀 | 位置 | 含义 |
|---|---|---|---|
| `IIO_CHAN_INFO_RAW` | `raw` | `:147` | 原始寄存器值，用户态要自己乘 `scale` |
| `IIO_CHAN_INFO_PROCESSED` | **`input`** | `:148` | 已换算成标准单位，用户态直接用 |
| `IIO_CHAN_INFO_SCALE` | `scale` | `:149` | 乘数 |
| `IIO_CHAN_INFO_OFFSET` | `offset` | `:150` | 加数 |

**`PROCESSED` 生成的文件名是 `input` 而不是 `processed`** —— 这是全表里唯一一处名字和枚举名对不上的，`in_temp_input` 就是这么来的。

### §1.4 三个 `info_mask_shared_by_*` 的区别是"生成几个文件"

`iio_chan_spec` 有四个 mask 字段，差别只在共享范围，而共享范围直接决定文件个数和名字（`__iio_device_attr_init` 的两个 `switch (shared_by)`，单端分支在 `:1099-1130`）：

| 字段 | `shared_by` | 单端通道生成的名字 | 文件个数 |
|---|---|---|---|
| `info_mask_separate` | `IIO_SEPARATE` | `in_voltage0_raw`、`in_voltage1_raw`（`:1116-1128`） | 每通道一个 |
| `info_mask_shared_by_type` | `IIO_SHARED_BY_TYPE` | `in_voltage_scale`（`:1109-1113`，**没有序号**） | 每 (方向,类型) 一个 |
| `info_mask_shared_by_dir` | `IIO_SHARED_BY_DIR` | `in_sampling_frequency`（`:1104-1107`） | 每方向一个 |
| `info_mask_shared_by_all` | `IIO_SHARED_BY_ALL` | `sampling_frequency`（`:1101-1102`，**连方向都没有**） | 整个设备一个 |

三个后果值得单独记：

1. **写错字段不会报错，只会让文件名变样**。把 `SCALE` 放进 `info_mask_separate`，你得到的是 `in_voltage0_scale` / `in_voltage1_scale` 两个文件，而用户态工具（`libiio`、`iio_readdev`）按 ABI 找的是 `in_voltage_scale`——**能读、但读的人找不到**。
2. **共享的属性只调你的 `.read_raw` 一次，`chan` 参数指向的是第一个匹配的通道**。所以共享属性的实现里不能用 `chan->channel` 做区分。
3. **差分通道另有一套命名**（`:1066-1098`），形状是 `in_voltage0-voltage1_raw`。这里有一处硬性约束：`.differential = 1` 且 `IIO_SEPARATE` 时**必须同时 `.indexed = 1`**，否则 `:1085` 直接 `WARN(1, "Differential channels must be indexed\n")` 并返回 `-EINVAL`，整个设备注册失败。这是本篇少数几个"填错字段会当场炸"的地方。

### §1.5 `.read_raw` 的返回值不是错误码，是格式码

新手最常犯的错：`return 0`。返回值的语义是"`*val`/`*val2` 该怎么解释成字符串"，定义在 `include/linux/iio/types.h:24-32`：

| 返回 | 值 | 用法 | 用户态看到 |
|---|---|---|---|
| `IIO_VAL_INT` | 1（`:24`） | 只用 `*val` | `1234` |
| `IIO_VAL_INT_PLUS_MICRO` | 2（`:25`） | `*val` 整数部分，`*val2` 百万分之一 | `1.234500` |
| `IIO_VAL_INT_PLUS_NANO` | 3（`:26`） | 同上，十亿分之一 | `1.234500000` |
| `IIO_VAL_INT_MULTIPLE` | 5（`:28`） | `*val` 是数组，配 `.read_raw_multi` | 多个数字 |
| `IIO_VAL_FRACTIONAL` | 10（`:30`） | `*val / *val2` | 核心做除法 |
| `IIO_VAL_FRACTIONAL_LOG2` | 11（`:31`） | `*val / 2^*val2` | 核心做移位 |
| `IIO_VAL_CHAR` | 12（`:32`） | `*val` 是单个字符 | 一个字母 |

`IIO_VAL_INT` 的值是 **1**，所以 `return 0` 落到 `iio_format_value()` 的 default 分支去，不会当成成功。**负值才是错误码**，正值全是格式码——这是 IIO 里唯一一处"返回正数有语义"的接口约定。

`IIO_VAL_FRACTIONAL_LOG2` 是 ADC 驱动最常用的：参考电压 mV 做分子、位宽做分母指数，一行搞定 scale，不用自己算浮点。§1.2 骨架里就是这么写的。

---

## §2 角色 B：触发缓冲区传感器驱动

### §2.1 B = A + 四件事

`§1` 的七件事一件都不能少。在此之上加：

| # | 动作 | 用什么 | 漏了会怎样 |
|---|---|---|---|
| 8 | 给每个通道填 `scan_index` 和 `scan_type` | `iio_chan_spec` 字段 | 缓冲区里数据错位或全 0 |
| 9 | 加一个时间戳通道 | `IIO_CHAN_SOFT_TIMESTAMP(_si)` 宏，`include/linux/iio/iio.h:306-315` | 用户态收到的样本没有时间信息 |
| 10 | 写 trigger handler（下半部） | 你自己的 `irqreturn_t` 函数 | 使能缓冲区后没有数据 |
| 11 | 装缓冲区 | `devm_iio_triggered_buffer_setup(dev, indio_dev, &iio_pollfunc_store_time, handler, NULL)` | 用户态 `buffer/enable` 写不进去 |

第 11 步的第三个参数 `&iio_pollfunc_store_time` 是核心提供的通用上半部（声明在 `include/linux/iio/trigger_consumer.h:49`），职责只有一件：把当前时间戳存到 `pf->timestamp`。**几乎所有驱动都用它，不要自己写上半部**——`04-extensions.md` §4.4 里 loop 触发器跳过上半部导致时间戳变垃圾，就是这一层的反面教材。

### §2.2 骨架（在 §1.2 基础上的增量）

```c
#include <asm/unaligned.h>		/* get_unaligned_le16 */
#include <linux/iio/buffer.h>
#include <linux/iio/trigger_consumer.h>
#include <linux/iio/triggered_buffer.h>

struct mysensor {
	struct regmap	*regmap;
	struct mutex	lock;
	/* 扫描缓冲：必须能放下所有通道 + 8 字节时间戳，且对齐 */
	struct {
		u16	channels[2];
		s64	timestamp __aligned(8);
	} scan;
	u8	buf[4] __aligned(IIO_DMA_MINALIGN);	/* 给 SPI/I2C DMA 用 */
};

static const struct iio_chan_spec mysensor_channels[] = {
	{
		.type = IIO_VOLTAGE,
		.indexed = 1,
		.channel = 0,
		.info_mask_separate = BIT(IIO_CHAN_INFO_RAW),
		.scan_index = 0,
		.scan_type = {
			.sign		= 'u',
			.realbits	= 12,
			.storagebits	= 16,
			.shift		= 0,
			.endianness	= IIO_LE,
		},
	}, {
		.type = IIO_VOLTAGE,
		.indexed = 1,
		.channel = 1,
		.info_mask_separate = BIT(IIO_CHAN_INFO_RAW),
		.scan_index = 1,
		.scan_type = {
			.sign		= 'u',
			.realbits	= 12,
			.storagebits	= 16,
			.shift		= 0,
			.endianness	= IIO_LE,
		},
	},
	IIO_CHAN_SOFT_TIMESTAMP(2),	/* scan_index 接着上面往下排 */
};

/* 下半部：跑在 IRQ 线程里，可以睡眠、可以做 I2C/SPI 传输 */
static irqreturn_t mysensor_trigger_handler(int irq, void *p)
{
	struct iio_poll_func *pf = p;
	struct iio_dev *indio_dev = pf->indio_dev;
	struct mysensor *st = iio_priv(indio_dev);
	int ret;

	mutex_lock(&st->lock);
	ret = regmap_bulk_read(st->regmap, 0x00, st->buf, 4);
	mutex_unlock(&st->lock);
	if (ret)
		goto done;

	st->scan.channels[0] = get_unaligned_le16(&st->buf[0]);
	st->scan.channels[1] = get_unaligned_le16(&st->buf[2]);

	iio_push_to_buffers_with_timestamp(indio_dev, &st->scan, pf->timestamp);

done:
	/* 无论成败都必须调用，否则触发器永远认为消费者还没做完 */
	iio_trigger_notify_done(indio_dev->trig);
	return IRQ_HANDLED;
}
```

probe 里在 `devm_iio_device_register` **之前**加一行：

```c
	ret = devm_iio_triggered_buffer_setup(&pdev->dev, indio_dev,
					      &iio_pollfunc_store_time,
					      mysensor_trigger_handler,
					      NULL);
	if (ret)
		return ret;
```

顺序不能反：`iio_triggered_buffer_setup` 会改 `indio_dev->modes` 和挂 `setup_ops`（`04-extensions.md` §2.1 的三步合并表），注册之后再改就晚了。

### §2.3 `scan_type` 五个字段各自控制什么

这五个字段是驱动和用户态之间的**唯一**数据格式约定，用户态从 `scan_elements/in_voltage0_type` 里读到的字符串就是它们生成的。字段定义在 `include/linux/iio/iio.h:244-251` 的匿名结构里：

| 字段 | 含义 | 填错的后果 |
|---|---|---|
| `sign` | `'u'` 或 `'s'` | 用户态把负温度读成 65000 |
| `realbits` | 有效位数 | 用户态多算或少算高位 |
| `storagebits` | 占多少字节（必须是 8/16/32/64） | **缓冲区里所有后续通道整体错位** |
| `shift` | 右移几位才是有效值 | 数值差一个 2 的幂 |
| `endianness` | `IIO_LE` / `IIO_BE` / `IIO_CPU`（`enum iio_endian`，`include/linux/iio/iio.h:29-33`） | 高低字节反转 |

`storagebits` 是唯一一个错了会影响别的通道的字段：核心用它算 `scan_bytes`（`industrialio-buffer.c` 的扫描配置那条链），一个通道报小了，后面所有通道在缓冲区里的偏移全错。**而且不会有任何报错**——用户态读到的就是一串合法但含义错误的数字。

`repeat` 字段（同一个匿名结构里，`include/linux/iio/iio.h:249`）只有少数传感器用（同一通道连续多个同格式样本），一般留 0。

注意 `endianness` 的枚举**不在 `types.h` 里**——`IIO_VAL_*` 在 `include/linux/iio/types.h`，`IIO_CPU/IIO_BE/IIO_LE` 在 `include/linux/iio/iio.h:29-33`。两个头文件名字太像，只 include 了 `types.h` 时报的是 `IIO_LE 未声明`。

### §2.4 两个必须成对的调用

| 上半部 / 入口 | 必须配的收尾 | 位置 |
|---|---|---|
| trigger handler 被调用 | `iio_trigger_notify_done(indio_dev->trig)` | 声明 `include/linux/iio/trigger_consumer.h:51` |
| `iio_triggered_buffer_setup()` | `iio_triggered_buffer_cleanup()` | 声明 `include/linux/iio/triggered_buffer.h:18` |

第一条是**最容易漏的一条**，而且漏了的症状很难联想到原因：缓冲区只出一个样本就停住，`dmesg` 里什么都没有。原因见 03 的触发流程——触发器用完成计数判断"所有消费者都处理完了"，少一次 notify 就永远等不齐。**`goto done` 那个标签的存在就是为了保证出错路径也走到它**，骨架里刻意这么写。

第二条在 `devm_` 版本下不用你管。要注意的是它**不是 free 而是 put**（`04-extensions.md` §2.3 的四行引用计数表）：调用它之后 `iio_buffer` 结构还活着，真正释放发生在 `iio_dev_release` → `iio_device_detach_buffers`（`drivers/iio/industrialio-core.c:1643`）。所以"cleanup 之后再访问 buffer 指针"不会立刻崩，测不出问题，但仍然是错的。

### §2.5 三个陷阱

**陷阱 1：时间戳不是你想写就写。** `iio_push_to_buffers_with_timestamp()` 的定义在 `include/linux/iio/buffer.h:37-46`，它写时间戳的前提是 `indio_dev->scan_timestamp` 为真（`:40`）。这个字段由核心在使能缓冲区时从 buffer 配置里拷过来（`drivers/iio/industrialio-buffer.c:1067`），而 buffer 的那一位来自**用户态**写 `scan_elements/in_timestamp_en`（`:572`）。所以：

- 用户态没使能时间戳元素 → 你的 `pf->timestamp` 被丢弃，`:42` 那行不执行
- 你没在通道表里放 `IIO_CHAN_SOFT_TIMESTAMP` → 那个 sysfs 文件根本不存在，用户态无从使能

两件事都做了才有时间戳。**驱动侧单方面做不到。**

**陷阱 2：扫描缓冲区必须显式对齐。** `iio_push_to_buffers_with_timestamp` 按 `int64_t` 数组下标写时间戳（`include/linux/iio/buffer.h:41-42`），所以缓冲区起始地址必须 8 字节对齐。6.1.158 里有两种写法并存，**主流是 `__aligned(8)`**：

| 写法 | drivers/iio 里的次数 | 重跑 |
|---|---|---|
| 字段名叫 `timestamp` 且带 `__aligned(8)` | 31 | `grep -rn 'timestamp __aligned(8)' --include=*.c --include=*.h drivers/iio/ \| wc -l` |
| `aligned_s64`（宏，`include/linux/types.h:114`） | 9 | `grep -rnw 'aligned_s64' drivers/iio/ \| wc -l` |

骨架用了前者。两者等价，挑一个跟你参考的邻居驱动一致就行。

**这个 31 数的是字段名，不是"加了对齐标注的驱动数"。** 树里时间戳字段的名字有三种（`ts` 35 个、`timestamp` 31 个，还有一个拼错的 `timetamp`），类型也有三种（`s64` / `u64` / `int64_t`）。按类型数才是"一共有多少处给 64 位时间戳字段加了标注"：

```bash
grep -rnE '(s64|u64|int64_t|__s64|__u64) [a-z_0-9]+ __aligned\(8\)' \
  --include=*.c --include=*.h drivers/iio | wc -l      # → 67
```

**你抄邻居驱动时不必在意叫什么名字**，但要在意两件事：字段是 64 位整型、并且带了 `__aligned(8)`（或用 `aligned_s64` 一次写完）。完整口径拆解在 `99-evidence.md` §8.4。

另一件事是 DMA：给 SPI/I2C 控制器直接用的 buffer 要 `__aligned(IIO_DMA_MINALIGN)`，避免和结构体其余部分共享 cacheline。这个宏定义在 `include/linux/iio/iio.h:723`（就是 `ARCH_KMALLOC_MINALIGN` 的别名），紧跟它的注释 `:717-722` 说明了理由。**6.1.158 里 `drivers/iio/` 有 100 处使用、散在 95 个文件**，说明这是常规做法而不是特例：

```bash
grep -rnw 'IIO_DMA_MINALIGN' --include=*.c --include=*.h drivers/iio/ | wc -l   # 100
grep -rlw 'IIO_DMA_MINALIGN' --include=*.c --include=*.h drivers/iio/ | wc -l   # 95
```

顺带一个版本时效提醒：老代码里的 `____cacheline_aligned` 在 6.1.158 的 `drivers/iio/` 下已经**一处不剩**（同样两条命令换关键字，结果都是 0），照抄旧驱动会用到已经被换掉的写法。

**陷阱 3：sysfs 单次读和缓冲区不能同时进行。** 缓冲区跑起来之后，`.read_raw` 里再去动硬件会和 handler 打架。正确做法是在 `.read_raw` 开头 `iio_device_claim_direct_mode(indio_dev)`（声明 `include/linux/iio/iio.h:630`），返回 `-EBUSY` 就直接把它回给用户态；退出前配 `iio_device_release_direct_mode(indio_dev)`（`:631`）。§1.2 的骨架没写这两行，因为角色 A 没有缓冲区；**角色 B 必须写**。

---

## §3 角色 C：内核内消费者（单值读取）

这是 IIO 里代码量最少、使用最广的角色——全树 110 处、34 个文件（`04-extensions.md` §6.4 的统计表），其中 76% 在 `drivers/power/`。你写的是 thermal / power / hwmon / regulator 驱动，只是需要读一个 ADC。

### §3.1 先做一个判断：processed 还是 raw

| 你要的 | 用 | 前提 | 拿到的 |
|---|---|---|---|
| 直接能用的物理量 | `iio_read_channel_processed(chan, &val)`（`include/linux/iio/consumer.h:236`） | 提供方支持 `PROCESSED` **或** 同时有 `RAW`+`SCALE` | 标准单位（mV / milli-degC） |
| 原始寄存器值 | `iio_read_channel_raw(chan, &val)`（`:207`） | 提供方支持 `RAW` | 原始数，你自己换算 |

**先试 processed。** 它的实现会在提供方只有 `RAW`+`SCALE` 时自己做换算，所以适用面比 `PROCESSED` 位本身更宽。全树统计支持这个判断：45 处用 `iio_read_channel_processed`，只有 7 处用 `iio_read_channel_raw`（`04-extensions.md` §6.4 的 processed-vs-raw 表）。

### §3.2 骨架

```c
#include <linux/iio/consumer.h>

struct mythermal {
	struct iio_channel	*adc;
};

static int mythermal_probe(struct platform_device *pdev)
{
	struct mythermal *st;

	st = devm_kzalloc(&pdev->dev, sizeof(*st), GFP_KERNEL);
	if (!st)
		return -ENOMEM;

	/* "sensor" 要和 DTS 里 io-channel-names 的某一项对上 */
	st->adc = devm_iio_channel_get(&pdev->dev, "sensor");
	if (IS_ERR(st->adc))
		return dev_err_probe(&pdev->dev, PTR_ERR(st->adc),
				     "failed to get adc channel\n");

	platform_set_drvdata(pdev, st);
	return 0;
}

static int mythermal_get_temp(struct mythermal *st, int *temp_mC)
{
	int val, ret;

	ret = iio_read_channel_processed(st->adc, &val);
	if (ret < 0)
		return ret;

	*temp_mC = val;	/* processed 已经是标准单位 */
	return 0;
}
```

配套 DTS（属性怎么写、谁消费，见 `06-devicetree.md`）：

```dts
	thermal-sensor {
		compatible = "vendor,mythermal";
		io-channels = <&adc 3>;
		io-channel-names = "sensor";
	};
```

### §3.3 `EPROBE_DEFER` 是常态，不是异常

`devm_iio_channel_get()` 在提供方还没注册时返回 `-EPROBE_DEFER`。**这不是错误，是正常的启动时序**——ADC 和你的驱动谁先 probe 取决于链接顺序。所以：

- 用 `dev_err_probe()` 而不是 `dev_err()`：它对 `-EPROBE_DEFER` 会降级成 debug 级别，不会在每次启动时刷一行假错误
- **不要重试、不要 `msleep` 等**，直接把错误码 `return` 出去，驱动模型会在提供方注册后再叫你一次

### §3.4 三对必须配对的获取/释放

IIO 消费者 API 有三条独立的获取路径，**每条有自己的释放函数，三者不能交叉**：

| 获取 | 释放 | 位置 | 拿到的 |
|---|---|---|---|
| `iio_channel_get(dev, name)` | `iio_channel_release(chan)` | `:40` / `:47` | **一个**通道 |
| `iio_channel_get_all(dev)` | `iio_channel_release_all(chan)` | `:76` / `:82` | 一个**数组**，哨兵结尾 |
| `devm_iio_channel_get` / `devm_iio_channel_get_all` | 不用管 | `:65` / `:99` | 同上，挂 devres |

**交叉调用的后果分两种，都很难查**（`04-extensions.md` §6.2 有实现侧的行号）：

- 用 `iio_channel_release()` 释放 `_get_all` 拿到的数组：只放掉第一个元素，其余通道的设备引用计数永久泄漏，那个 ADC 从此**无法卸载模块**
- 用 `iio_channel_release_all()` 释放 `_get` 拿到的单个通道：它按哨兵 `chan->indio_dev == NULL` 循环（`drivers/iio/inkern.c:530`），单个通道后面没有哨兵，**会一路读过界**

第二种是真正的内存安全问题。判断规则很简单：**函数名里带 `all` 的必须两头都带 `all`。**

### §3.5 consumer.h 里的东西你全都能用

这是个少见的干净接口：`include/linux/iio/consumer.h` 和 `include/linux/iio/hw-consumer.h` 里声明的 **37 个函数全部有 `EXPORT_SYMBOL*`**，没有一个是"声明了但不导出"的陷阱（对比 `04-extensions.md` §5.4 的 D7——那四个幽灵函数在 `sw_device.h` / `sw_trigger.h` 里，不在这两个头文件里）。重跑：

```bash
for f in $(grep -oE '\b(devm_|fwnode_|devm_fwnode_)?iio_[a-z_]+\(' \
             include/linux/iio/consumer.h include/linux/iio/hw-consumer.h \
           | sed 's/.*://; s/($//; s/(//' | sort -u); do
  printf '%-42s %s\n' "$f" \
    "$(grep -rnw "EXPORT_SYMBOL[_A-Z]*($f)" --include=*.c drivers/iio/ | wc -l)"
done
```

所以在角色 C / D 里，你不需要做"这个符号能不能用"的判断——**能 include 到就能调**。这个结论只对这两个头文件成立，`iio.h` 和 `trigger.h` 里混着宏、`static inline` 和不导出的内部函数（§0.4、§8）。

### §3.6 几个容易被忽略的邻居函数

| 想做的事 | 用 | 位置 |
|---|---|---|
| 确认对方是不是我要的类型（防止 DTS 接错） | `iio_get_channel_type(chan, &type)` | `:338` |
| 只要 scale，自己算 | `iio_read_channel_scale(chan, &val, &val2)` | `:364` |
| 拿到 raw 后按对方的 scale/offset 换算 | `iio_convert_raw_to_processed(chan, raw, &processed, scale)` | `:387` |
| 读硬件自己做的多次平均 | `iio_read_channel_average_raw(chan, &val)` | `:221` |
| 读对方支持的取值范围 | `iio_read_avail_channel_raw(chan, &vals, &length)` | `:314` |
| 反向：往对方写一个值（DAC） | `iio_write_channel_raw(chan, val)` | `:287` |
| 按 fwnode 而不是自己的 dev 取通道 | `fwnode_iio_channel_get_by_name(fwnode, name)` | `:108` |

`iio_get_channel_type()` 值得默认加上：DTS 写错 phandle 时你会拿到一个合法的 `iio_channel`，只是类型不对（比如接到了电压通道而不是温度通道）。检查一行的成本换掉一类"读数一直不对但不报错"的排查。

---

## §4 角色 D：内核内消费者（数据流）

角色 C 是"我现在要一个值"，角色 D 是"我要持续收到数据"。IIO 给了两套完全不同的机制，**先选对，再写代码**。

### §4.1 两套机制的分诊

| | 回调缓冲区 | hw-consumer |
|---|---|---|
| 头文件 | `include/linux/iio/consumer.h` | `include/linux/iio/hw-consumer.h` |
| 我拿到什么 | 每来一批数据，我的回调被调一次，参数是原始扫描数据 | **什么都拿不到** |
| 用来干什么 | 在内核里处理数据流 | 让提供方"开着"，数据走硬件通路给别人 |
| 入口 | `iio_channel_get_all_cb(dev, cb, private)`（`consumer.h:141-144`） | `devm_iio_hw_consumer_alloc(dev)`（`hw-consumer.h:16`） |
| 典型用户 | 需要对 ADC 流做数字处理的驱动 | ADC 输出直连另一个硬件模块（`04-extensions.md` §1.4） |

**hw-consumer 不给你数据**，这是它最容易被误解的地方。它的全部作用是把提供方的缓冲区拉起来，好让数据从提供方流向某个硬件目的地。如果你以为它会回调你，会得到一个"能编译、能运行、什么都不发生"的驱动。

### §4.2 回调缓冲区骨架

```c
#include <linux/iio/consumer.h>
#include <linux/iio/buffer.h>

struct mydsp {
	struct iio_cb_buffer	*cb;
	bool			running;
};

/* 回调：跑在提供方的 trigger handler 上下文里 */
static int mydsp_data_cb(const void *data, void *private)
{
	struct mydsp *st = private;
	const u16 *samples = data;

	/* data 的布局 = 提供方使能了哪些扫描元素，顺序按 scan_index */
	do_something(st, samples[0]);

	return 0;	/* 非 0 会被提供方当成推送失败 */
}

static int mydsp_probe(struct platform_device *pdev)
{
	struct mydsp *st;
	int ret;

	st = devm_kzalloc(&pdev->dev, sizeof(*st), GFP_KERNEL);
	if (!st)
		return -ENOMEM;

	st->cb = iio_channel_get_all_cb(&pdev->dev, mydsp_data_cb, st);
	if (IS_ERR(st->cb))
		return dev_err_probe(&pdev->dev, PTR_ERR(st->cb),
				     "failed to register callback\n");

	/* 可选：让提供方攒到 N 个样本才推一次，减少回调频率 */
	ret = iio_channel_cb_set_buffer_watermark(st->cb, 16);
	if (ret)
		goto err_release;

	ret = iio_channel_start_all_cb(st->cb);	/* 到这里数据才开始流 */
	if (ret)
		goto err_release;

	st->running = true;
	platform_set_drvdata(pdev, st);
	return 0;

err_release:
	iio_channel_release_all_cb(st->cb);
	return ret;
}

static int mydsp_remove(struct platform_device *pdev)
{
	struct mydsp *st = platform_get_drvdata(pdev);

	if (st->running)
		iio_channel_stop_all_cb(st->cb);
	iio_channel_release_all_cb(st->cb);
	return 0;
}
```

**四个函数是两对，不是四个独立步骤**：

| 配对 | 位置 | 语义 |
|---|---|---|
| `iio_channel_get_all_cb` / `iio_channel_release_all_cb` | `consumer.h:141` / `:160` | 注册 / 注销（分配资源） |
| `iio_channel_start_all_cb` / `iio_channel_stop_all_cb` | `:166` / `:172` | 开流 / 停流（不释放） |

**只 get 不 start 就没有数据**——这是回调缓冲区最常见的"代码看着对但不工作"。反过来，只 stop 不 release 会泄漏。

### §4.3 回调里能做什么、不能做什么

回调是**由提供方的 trigger handler 直接调用**的，所以它的上下文约束由提供方决定，不由你决定：

| 情况 | 你的回调跑在哪 | 能睡吗 |
|---|---|---|
| 提供方用 `iio_triggered_buffer_setup`（角色 B 那套） | IRQ 线程 | 能 |
| 提供方在硬中断里推数据 | 硬中断 | **不能** |

**你无法从 API 上知道是哪一种**——`iio_channel_get_all_cb` 不告诉你提供方的实现方式。安全做法：**当成不能睡来写**，需要重活就把数据丢进 workqueue。这一条是本篇为数不多的"宁可保守"的建议，因为写错的症状是偶发的 `scheduling while atomic`，不是稳定可复现的失败。

另外两点：

- **`data` 的布局不由你决定**。它就是提供方缓冲区里的一帧扫描数据，字段个数和格式取决于哪些 scan element 被使能了。要精确知道布局，用 `iio_channel_cb_get_channels(cb)`（`consumer.h:185`）把通道数组拿出来，读它们的 `scan_index` / `scan_type`
- **回调返回非 0** 会被提供方那边当成推送失败向上传，不要用返回值表示"这批数据我不关心"

### §4.4 hw-consumer 骨架

```c
#include <linux/iio/hw-consumer.h>

struct myhw {
	struct iio_hw_consumer	*hwc;
};

static int myhw_probe(struct platform_device *pdev)
{
	struct myhw *st;

	st = devm_kzalloc(&pdev->dev, sizeof(*st), GFP_KERNEL);
	if (!st)
		return -ENOMEM;

	st->hwc = devm_iio_hw_consumer_alloc(&pdev->dev);
	if (IS_ERR(st->hwc))
		return dev_err_probe(&pdev->dev, PTR_ERR(st->hwc),
				     "failed to alloc hw consumer\n");

	platform_set_drvdata(pdev, st);
	return 0;
}

/* 需要数据流动时（比如你自己被 enable 了） */
static int myhw_start(struct myhw *st)
{
	return iio_hw_consumer_enable(st->hwc);	/* hw-consumer.h:17 */
}

static void myhw_stop(struct myhw *st)
{
	iio_hw_consumer_disable(st->hwc);	/* hw-consumer.h:18 */
}
```

`devm_iio_hw_consumer_alloc()` 会把 **DTS 里 `io-channels` 列出的所有通道**一次性抓过来（`04-extensions.md` §1.4），没有"只要第 3 个通道"的选项。`enable` / `disable` 也是整体的。

非 devm 路径配对是 `iio_hw_consumer_alloc()` / `iio_hw_consumer_free()`（`hw-consumer.h:14` / `:15`）。

### §4.5 角色 D 的陷阱表

| 陷阱 | 症状 | 原因 |
|---|---|---|
| 只 `get_all_cb` 没 `start_all_cb` | 回调一次都不进 | 两对函数职责不同（§4.2 表） |
| 以为 hw-consumer 会回调 | 编译运行都正常，什么都不发生 | hw-consumer 不传数据（§4.1） |
| 回调里 `mutex_lock` / 分配内存 | 偶发 `scheduling while atomic` | 上下文由提供方决定（§4.3） |
| 按固定下标解析 `data` | 用户态改了 scan_elements 后数据错乱 | 布局随使能的元素变（§4.3） |
| `remove` 里先 release 再 stop | UAF | 顺序必须是 stop → release |
| 多个提供方的通道混在一个 `io-channels` 里 | `get_all_cb` 返回错误 | `consumer.h:137-139` 明确说明：不支持跨设备复用，"if the channels requested come from different devices this will fail" |

最后一行的依据是头文件里的原话（`include/linux/iio/consumer.h:137-139`），不是推断。

---

## §5 角色 E：触发器提供方

你手里有个周期性的事件源（外部中断引脚、定时器、另一个设备的 DRDY 信号），想让 IIO 传感器按它的节拍采样。

### §5.1 最小清单

| # | 动作 | 用什么 | 备注 |
|---|---|---|---|
| 1 | 分配 | `devm_iio_trigger_alloc(dev, "mytrig%d", id)`（宏，**`include/linux/iio/iio.h:735-736`**） | 名字是 `printf` 格式串，会出现在 sysfs 里 |
| 2 | 存私有数据 | `iio_trigger_set_drvdata(trig, st)`（`include/linux/iio/trigger.h:114-117`） | `static inline` |
| 3 | 填 `trig->ops` | 指向你的 `struct iio_trigger_ops` | 可以是 NULL，见 §5.3 |
| 4 | 注册 | `devm_iio_trigger_register(dev, trig)`（`trigger.h:136`） | — |
| 5 | 事件到来时打点 | `iio_trigger_poll(trig)`（`trigger.h:160`）或 `iio_trigger_poll_chained(trig)`（`:161`） | **选错会死锁或丢时间戳**，见 §5.4 |

第 1 步的宏在 `iio.h` 而不是 `trigger.h`（§0.4 第四行那个坑）。非 devm 版本 `iio_trigger_alloc` 在 `trigger.h:165-166`，配对释放 `iio_trigger_free`（`:172`）。

### §5.2 骨架（外部中断当触发源）

```c
#include <linux/iio/iio.h>		/* devm_iio_trigger_alloc 在这里 */
#include <linux/iio/trigger.h>
#include <linux/interrupt.h>

struct mytrig {
	struct iio_trigger	*trig;
	int			irq;
	bool			enabled;
};

/* 硬中断：只负责打点 */
static irqreturn_t mytrig_isr(int irq, void *private)
{
	struct mytrig *st = private;

	iio_trigger_poll(st->trig);	/* 硬中断上下文用这个 */
	return IRQ_HANDLED;
}

static int mytrig_set_state(struct iio_trigger *trig, bool state)
{
	struct mytrig *st = iio_trigger_get_drvdata(trig);

	if (state == st->enabled)
		return 0;

	if (state)
		enable_irq(st->irq);
	else
		disable_irq(st->irq);

	st->enabled = state;
	return 0;
}

static int mytrig_validate_device(struct iio_trigger *trig,
				  struct iio_dev *indio_dev)
{
	/* 只允许特定设备用我：返回 -EINVAL 拒绝 */
	if (indio_dev->info != &expected_info)
		return -EINVAL;
	return 0;
}

static const struct iio_trigger_ops mytrig_ops = {
	.set_trigger_state = mytrig_set_state,
	.validate_device   = mytrig_validate_device,
};

static int mytrig_probe(struct platform_device *pdev)
{
	struct mytrig *st;
	int ret;

	st = devm_kzalloc(&pdev->dev, sizeof(*st), GFP_KERNEL);
	if (!st)
		return -ENOMEM;

	st->irq = platform_get_irq(pdev, 0);
	if (st->irq < 0)
		return st->irq;

	st->trig = devm_iio_trigger_alloc(&pdev->dev, "mytrig%d", pdev->id);
	if (!st->trig)
		return -ENOMEM;

	st->trig->ops = &mytrig_ops;
	iio_trigger_set_drvdata(st->trig, st);

	ret = devm_request_irq(&pdev->dev, st->irq, mytrig_isr,
			       IRQF_TRIGGER_RISING | IRQF_NO_AUTOEN,
			       "mytrig", st);
	if (ret)
		return ret;

	return devm_iio_trigger_register(&pdev->dev, st->trig);
}
```

`devm_iio_trigger_alloc` 失败返回 **NULL 而不是 ERR_PTR**（不同于 §3 的 `devm_iio_channel_get`），所以判断写 `if (!st->trig)`。

`IRQF_NO_AUTOEN`（`include/linux/interrupt.h:83`）让中断注册后处于关闭状态，配合 `.set_trigger_state` 按需开关；没有它就得在 probe 里立刻 `disable_irq` 一次，且中间有个短窗口会收到中断。`drivers/iio/` 下已有 10 处这么用（`grep -rnw IRQF_NO_AUTOEN drivers/iio/ | wc -l`）。

### §5.3 `iio_trigger_ops` 三个字段，一个都不是必填

结构体只有三个成员（`include/linux/iio/trigger.h:32-37`）：

| 字段 | 位置 | 不填的后果 | 全树填了的数量 |
|---|---|---|---|
| `.set_trigger_state` | `:33` | 触发器永远开着，浪费功耗；但**功能正常** | 47 |
| `.reenable` | `:34` | 一次性触发源不会自动重装 | 10 |
| `.validate_device` | `:35-36` | 任何设备都能挂上来 | 23 |

`trig->ops` 本身也可以是 NULL——全树 53 个 `iio_trigger_ops` 实例里（`04-extensions.md` §4.0），`drivers/iio/trigger/` 下的 4 个文件有 ops，而 `iio-trig-sysfs.c` 和 `iio-trig-interrupt.c` **一个 ops 都没有**（`04-extensions.md` §4.1 的两维判断表）。它们靠事件源本身天然的"要么有事件要么没有"，不需要开关。

kerneldoc 明确说 `.reenable` "may be NULL"（`:25`）。`.set_trigger_state` 没有这句话，但从上面两个文件的存在可知它也不是必填。**这是一处 kerneldoc 说得比实际更严的地方**，判据是代码不是注释。

### §5.4 `iio_trigger_poll` 和 `iio_trigger_poll_chained` 必须选对

两个函数只差一个后缀，语义完全不同，**选错的症状还不一样**：

| 用哪个 | 位置 | 在什么上下文调 | 后果（选错） |
|---|---|---|---|
| `iio_trigger_poll(trig)` | `trigger.h:160` | **硬中断** | 在可睡上下文调 → 消费者的上半部在错的上下文跑 |
| `iio_trigger_poll_chained(trig)` | `trigger.h:161` | **线程 / kthread / workqueue** | 在硬中断里调 → 它内部要做的事不能在硬中断做 |

判断规则：**你的调用点能不能睡。** kerneldoc 在 `:158` 直说 `iio_trigger_poll` 是 "Typically called in relevant hardware interrupt handler"。

真实用户对照（`04-extensions.md` §4.3 的表）：`drivers/iio/trigger/iio-trig-hrtimer.c` 用 `iio_trigger_poll`（hrtimer 回调是原子上下文），`drivers/iio/trigger/iio-trig-loop.c:49` 用 `iio_trigger_poll_chained`（它跑在 kthread 里）。

还有一个现成的 ISR 可以直接用：`iio_trigger_generic_data_rdy_poll()`（声明 `trigger.h:163`，实现 `drivers/iio/industrialio-trigger.c:212-216`）。如果你的触发源就是一根 DRDY 引脚、除了打点没别的事做，把它直接当 `request_irq` 的 handler 传进去，连 §5.2 里的 `mytrig_isr` 都不用写。

**但它的 `private` 参数必须是 `struct iio_trigger *` 本身**，不是你的私有结构——函数体只有一行 `iio_trigger_poll(private)`（`:214`），把 `void *` 原样当触发器指针用。传错了不会编译报错（形参是 `void *`），运行时直接崩在解引用上。

### §5.5 PREEMPT_RT 下的额外一条

本树打了全树 PREEMPT_RT 回移补丁，但 **iio 子系统本身一行没改**（`99-evidence.md` §1 的基线登记）。不过 `drivers/iio/trigger/` 下的现有触发器为 RT 做了适配，你写新触发器时要照着做：

| 触发器 | 适配点 | 位置 |
|---|---|---|
| sysfs | `IRQ_WORK_INIT_HARD` 而不是普通 irq_work | `drivers/iio/trigger/iio-trig-sysfs.c:158` |
| hrtimer | `HRTIMER_MODE_REL_HARD` | `drivers/iio/trigger/iio-trig-hrtimer.c:148`（`hrtimer_init`）、`:118`（`hrtimer_start`） |

**理由是同一个**：`iio_trigger_poll` 要求原子上下文，而在 PREEMPT_RT 下普通的 irq_work 和普通模式的 hrtimer 都会被推到线程里执行，那就变成"在可睡上下文调了只能在原子上下文调的函数"。**加 `_HARD` 后缀是在 RT 上保住原子性的标准手法。**

所以规则是：如果你的触发源用 irq_work 或 hrtimer，就必须用 `_HARD` 变体；如果用的是 kthread / workqueue，那就该改用 `iio_trigger_poll_chained`（§5.4）。

### §5.6 角色 E 的陷阱表

| 陷阱 | 症状 | 依据 |
|---|---|---|
| 用 `IS_ERR` 判断 `devm_iio_trigger_alloc` | 分配失败时解引用 NULL | 它返回 NULL（§5.2） |
| 只 include `trigger.h` 就用 `devm_iio_trigger_alloc` | 编译报未声明 | 宏在 `iio.h:735`（§0.4） |
| hrtimer 触发器不用 `HRTIMER_MODE_REL_HARD` | RT 内核上偶发上下文告警 | `drivers/iio/trigger/iio-trig-hrtimer.c:148` |
| kthread 里调 `iio_trigger_poll` | 上下文错 | `trigger.h:158` kerneldoc |
| `.set_trigger_state` 里做阻塞操作 | 可能死锁 | 它在使能缓冲区的路径上被调，持核心的锁 |
| 忘了 `iio_trigger_set_drvdata` | `iio_trigger_get_drvdata` 返回 NULL，ops 回调里崩 | `trigger.h:114-127` |

---

## §6 角色 F：缓冲区后端实现者

**先劝退：你大概不需要这一节。** 全树只有 4 个缓冲区后端，全部住在 `drivers/iio/buffer/`，而且已经覆盖了软件 FIFO、DMA、内核回调、硬件直连四种形态（`04-extensions.md` §1）。列在这里的原因是：读别人的后端代码时需要这张字段表，以及万一你要做一个新的 DMA 变体。

重跑确认只有 4 个：

```bash
grep -rn 'struct iio_buffer_access_funcs' --include=*.c drivers/iio/ | grep -v extern
```

### §6.1 `iio_buffer_access_funcs` 的 13 个字段，只有 1 个必填

定义在 `include/linux/iio/buffer_impl.h:53-73`。**唯一四个后端都填了的是 `.release`**：

| 字段 | 位置 | kfifo | cb | hw-consumer | dmaengine |
|---|---|---|---|---|---|
| `.store_to` | `:54` | ✅ `:189` | ✅ `:42` | — | — |
| `.read` | `:55` | ✅ `:190` | — | — | ✅ `:114` |
| `.data_available` | `:56` | ✅ `:191` | — | — | ✅ `:120` |
| `.remove_from` | `:57` | ✅ `:192` | — | — | — |
| `.write` | `:58` | ✅ `:193` | — | — | — |
| `.space_available` | `:59` | ✅ `:194` | — | — | — |
| `.request_update` | `:61` | ✅ `:195` | — | — | ✅ `:117` |
| `.set_bytes_per_datum` | `:63` | ✅ `:196` | — | — | ✅ `:115` |
| `.set_length` | `:64` | ✅ `:197` | — | — | ✅ `:116` |
| `.enable` | `:66` | — | — | — | ✅ `:118` |
| `.disable` | `:67` | — | — | — | ✅ `:119` |
| `.release` | `:69` | ✅ `:198` | ✅ `:43` | ✅ `:48` | ✅ `:121` |
| `.modes` | `:71` | ✅ `:200` | ✅ `:45` | ✅ `:49` | ✅ `:123` |
| `.flags` | `:72` | — | — | — | ✅ `:124` |

（实例位置：`drivers/iio/buffer/kfifo_buf.c:188`、`industrialio-buffer-cb.c:41`、`industrialio-hw-consumer.c:47`、`industrialio-buffer-dmaengine.c:113`）

**这张表本身就是一份说明书**：

- `hw-consumer` 只填了 `.release` 和 `.modes` 两个字段——它不搬运任何数据（§4.1 说的"什么都拿不到"，在这里得到了实现层面的确认：**连 `.store_to` 都没有**）
- `cb` 只多一个 `.store_to`——它把数据直接转给你的回调，不留副本、没有读侧接口
- `kfifo` 填满了读写两侧但没有 `.enable`/`.disable`——软件 FIFO 不需要启停硬件
- `dmaengine` 有 `.enable`/`.disable` 但没有 `.store_to`/`.write`——数据由 DMA 引擎搬，不经过 CPU

`.modes` 决定了这个后端能挂在哪种设备上，取值来自 `include/linux/iio/iio.h:367-372` 那六个 `INDIO_*`。三种组合都在上表里：`INDIO_BUFFER_SOFTWARE | INDIO_BUFFER_TRIGGERED`（kfifo、cb）、`INDIO_BUFFER_HARDWARE`（hw-consumer、dmaengine）。

### §6.2 挂载：`iio_device_attach_buffer` 有两个反直觉行为

```c
	ret = iio_device_attach_buffer(indio_dev, my_buffer);
```

实现在 `drivers/iio/industrialio-buffer.c:1974-1998`。两点必须知道：

**1. 它会给 buffer 加一次引用（`:1988`），不是接管所有权。** 所以调用方之后仍然要按自己的路径 put。这条是 `04-extensions.md` §2.3 那张引用计数表的源头。

**2. `indio_dev->buffer` 只是第一个 buffer。** `:1990-1992` 有注释直说 `/* first buffer is legacy; attach it to the IIO device directly */`。完整列表在 `iio_dev_opaque->attached_buffers[]`（`:1994`）。**读别人代码时看到 `indio_dev->buffer` 不要以为它是"当前的 buffer"**——多缓冲区场景下它是"最早挂上的那个"。

kerneldoc `:1968-1969` 还有一句关键的：buffer "stays attached to the device until the device is freed"。**没有 detach 接口**，数组由 `iio_device_detach_buffers()` 在 `iio_device_free()` 里统一释放（`drivers/iio/industrialio-core.c:1643`）。

### §6.3 角色 F 的陷阱

| 陷阱 | 后果 |
|---|---|
| 不填 `.release` | buffer 引用计数归零时没人释放你的私有内存 |
| `.modes` 填错 | 设备的 `modes` 和 buffer 的 `modes` 不交集，使能缓冲区时被拒 |
| 以为 attach 会接管所有权 | 引用计数泄漏（`:1988`） |
| 用 `indio_dev->buffer` 当"当前 buffer" | 多缓冲区场景下操作了错的对象（`:1990-1992`） |
| 期待有 detach | 找不到对应接口，只能等设备释放（`:1968-1969`） |

---

## §7 角色 G：用户态程序

### §7.1 两条读取路径

| 路径 | 入口 | 适合 | 一次拿到 |
|---|---|---|---|
| sysfs | `/sys/bus/iio/devices/iio:deviceN/in_voltage0_raw` | 偶尔读一下 | 一个值，文本 |
| 字符设备 | `/dev/iio:deviceN` | 连续采样 | 一批二进制样本 |

sysfs 路径对应角色 A，字符设备路径对应角色 B（提供方必须装了缓冲区，否则 `/dev/iio:deviceN` 存在但读不出数据）。

### §7.2 sysfs 单次读

```sh
# 找设备
cat /sys/bus/iio/devices/iio:device0/name

# 读原始值 + scale，自己算
raw=$(cat /sys/bus/iio/devices/iio:device0/in_voltage0_raw)
scale=$(cat /sys/bus/iio/devices/iio:device0/in_voltage_scale)

# 或者直接读 processed（注意文件名后缀是 input，不是 processed）
cat /sys/bus/iio/devices/iio:device0/in_temp_input
```

**`in_voltage0_raw` 有序号、`in_voltage_scale` 没有**——这不是笔误，是共享范围决定的（§1.4）。写脚本时别把两个名字用同一个拼法生成。

### §7.3 缓冲区连续采样的五步

```sh
D=/sys/bus/iio/devices/iio:device0

# 1. 选触发器（sysfs 触发器需要先自己造一个）
echo 1 > /sys/bus/iio/devices/iio_sysfs_trigger/add_trigger
cat /sys/bus/iio/devices/trigger0/name > $D/trigger/current_trigger

# 2. 使能要采的通道（名字来自 scan_index，不是 channel）
echo 1 > $D/scan_elements/in_voltage0_en
echo 1 > $D/scan_elements/in_voltage1_en
echo 1 > $D/scan_elements/in_timestamp_en      # 想要时间戳必须显式使能

# 3. 设缓冲区长度
echo 128 > $D/buffer/length

# 4. 开
echo 1 > $D/buffer/enable

# 5. 读（二进制，按 scan_elements/*_type 描述的格式解析）
cat /dev/iio:device0 | hexdump -C
```

第 2 步的 `in_timestamp_en` 是 §2.5 陷阱 1 的用户态另一半：**驱动放了 `IIO_CHAN_SOFT_TIMESTAMP` 通道，你不 `echo 1` 进去，样本里就没有时间戳**。

### §7.4 数据格式从哪里读

不要猜，读 `scan_elements/<通道名>_type`：

```sh
cat $D/scan_elements/in_voltage0_type
# 形如 le:u12/16>>0
#      ↑  ↑ ↑  ↑ ↑
#      |  | |  | └─ shift
#      |  | |  └─── storagebits
#      |  | └────── realbits
#      |  └──────── sign（u / s）
#      └─────────── endianness（le / be）
```

这五个字段就是驱动侧 `scan_type` 的五个成员（§2.3），一一对应。**样本里各通道的排列顺序按 `scan_index` 从小到大，不是按你 echo 使能的顺序。**

### §7.5 角色 G 的陷阱

| 陷阱 | 症状 | 说明 |
|---|---|---|
| 没设 `current_trigger` 就 `echo 1 > buffer/enable` | 写失败，返回错误 | 触发缓冲区设备必须有触发器 |
| 忘了 `in_timestamp_en` | 样本里没时间戳 | §2.5 陷阱 1 |
| 按 echo 顺序解析数据 | 通道值对不上 | 顺序按 `scan_index`（§7.4） |
| `buffer/enable` 开着的时候读 `in_voltage0_raw` | 返回 `-EBUSY` | 驱动侧 `iio_device_claim_direct_mode`（§2.5 陷阱 3） |
| 硬编码 `iio:device0` | 换个启动顺序就读错设备 | 按 `name` 属性找 |
| 用 `in_voltage_raw`（无序号） | 文件不存在 | `raw` 是 per-channel 的（§1.4） |

---

## §8 近邻符号区分

IIO 的 API 命名高度规整，代价是有大量只差一个词的函数对。本节把它们成组摊开，每组写清"错用会怎样"。**这一节独立成章是故意的**——散在各角色的陷阱表里，跨角色的读者就找不到。

### §8.1 `iio_channel_release` / `iio_channel_release_all` / `iio_channel_release_all_cb`

| 符号 | 位置 | 配对的获取 | 释放的东西 |
|---|---|---|---|
| `iio_channel_release` | `include/linux/iio/consumer.h:47` | `iio_channel_get` | 一个通道 |
| `iio_channel_release_all` | `:82` | `iio_channel_get_all` | 一个通道数组 |
| `iio_channel_release_all_cb` | `:160` | `iio_channel_get_all_cb` | 一个回调缓冲区 |

**错用会怎样：**

- `release_all` 用在 `_get` 的单通道上 → **读过界**。它按哨兵 `while (chan->indio_dev)` 循环（`drivers/iio/inkern.c:530`），单通道后面没有哨兵，会一路 `iio_device_put` 到随机内存，最后还 `kfree(channels)`（`:534`）
- `release` 用在 `_get_all` 的数组上 → 只放第一个元素，其余设备引用永久泄漏，提供方模块**从此无法卸载**
- `release_all` 用在 `_get_all_cb` 的 `iio_cb_buffer` 上 → 类型不同，编译期就会拦住（`struct iio_channel *` vs `struct iio_cb_buffer *`）

**记法：名字里带 `all` 的两头都要带 `all`；带 `cb` 的两头都要带 `cb`。** 前两条编译期无法发现（同类型指针），第三条能。

### §8.2 `iio_trigger_poll` / `iio_trigger_poll_chained`

| 符号 | 位置 | 用在 |
|---|---|---|
| `iio_trigger_poll` | `include/linux/iio/trigger.h:160` | **硬中断上下文** |
| `iio_trigger_poll_chained` | `:161` | 线程 / kthread / workqueue |

**错用会怎样：** 两个方向都是上下文错配，症状都是偶发而非稳定复现——在 kthread 里调 `iio_trigger_poll`，或在硬中断里调 `_chained`，都可能长时间看不出问题，直到某个消费者的上半部真的去睡了一次。

kerneldoc 只在 `:158` 说了前者 "Typically called in relevant hardware interrupt handler"，后者没有对应说明。**判据靠现有用户对照**（`04-extensions.md` §4.3）：hrtimer 触发器用前者，loop 触发器（跑 kthread）用后者，`drivers/iio/trigger/iio-trig-loop.c:49`。

### §8.3 `iio_push_to_buffers` 三兄弟

| 符号 | 位置 | 形态 | data 要求 |
|---|---|---|---|
| `iio_push_to_buffers` | `include/linux/iio/buffer.h:19` | 导出函数 | 不含时间戳 |
| `iio_push_to_buffers_with_timestamp` | `:37-46` | **`static inline`** | 必须 8 字节对齐，末尾预留 8 字节 |
| `iio_push_to_buffers_with_ts_unaligned` | `:48-50` | 导出函数 | 不要求对齐，需额外传 `data_sz` |

**错用会怎样：**

- 用 `_with_timestamp` 但 buffer 没对齐 → `:41-42` 按 `int64_t` 下标写，在对齐敏感的架构上是未定义行为
- 用 `_with_timestamp` 但没在通道表里放 `IIO_CHAN_SOFT_TIMESTAMP` → 时间戳被静默丢弃（`:40` 的条件不成立），**不报错**
- 用 `iio_push_to_buffers`（不带时间戳）但通道表里有时间戳通道 → 时间戳位置是未初始化的栈/堆内容

第三个变体的存在本身是个信号：**如果你发现自己在为了对齐而重排结构体，直接用 `_ts_unaligned` 版本。**

### §8.4 D7：四个只有声明、没有定义的函数

这四个名字躺在你会 include 的头文件里，**全树只有声明行那一处命中，没有实现、没有调用者**：

| 幽灵符号 | 唯一命中 |
|---|---|
| `iio_sw_device_type_configfs_register` | `include/linux/iio/sw_device.h:54` |
| `iio_sw_device_type_configfs_unregister` | `include/linux/iio/sw_device.h:55` |
| `iio_sw_trigger_type_configfs_register` | `include/linux/iio/sw_trigger.h:54` |
| `iio_sw_trigger_type_configfs_unregister` | `include/linux/iio/sw_trigger.h:55` |

重跑（四个都输出 1，即只有声明那一行）：

```bash
for f in iio_sw_device_type_configfs_register iio_sw_device_type_configfs_unregister \
         iio_sw_trigger_type_configfs_register iio_sw_trigger_type_configfs_unregister; do
  printf '%-46s %s\n' "$f" "$(grep -rnw "$f" --include=*.c --include=*.h . | wc -l)"
done
```

**错用会怎样：** 编译通过（有声明），**链接时报 undefined symbol**。它是本篇少数几个"错了会当场炸"的地方之一，代价只是浪费一次编译。危险的是它们看起来正好是你需要的东西——`04-extensions.md` §5.4 里 configfs 软件框架的正确入口是 `iio_register_sw_device_type` / `iio_register_sw_trigger_type`，名字里没有 `configfs`。

正确的入口是这两对（都有实现、都能用）：

| 用途 | 注册 | 注销 |
|---|---|---|
| 软件设备类型 | `iio_register_sw_device_type`（`include/linux/iio/sw_device.h:48`） | `iio_unregister_sw_device_type`（`:49`） |
| 软件触发器类型 | `iio_register_sw_trigger_type`（`include/linux/iio/sw_trigger.h:48`） | `iio_unregister_sw_trigger_type`（`:49`） |

而且实践上你连这两个也不直接调——头文件在 `sw_device.h:17-18` / `sw_trigger.h:17-18` 用 `module_driver()` 把它们包成了 `module_iio_sw_device_driver` / `module_iio_sw_trigger_driver` 宏。**这是个两级宏展开，索引工具查不到调用关系**（`04-extensions.md` §5.5 记了同一个盲区）。

**记法：configfs 的注册不由你调，框架自己在内部做。** 名字里出现 `configfs` 的公开 API，在 IIO 里一个都不存在。

### §8.5 `iio_priv` / `iio_device_get_drvdata` / `iio_trigger_get_drvdata`

三个都是"取回我自己的数据"，但取的**不是同一份**：

| 符号 | 位置 | 取的是 | 谁存进去的 |
|---|---|---|---|
| `iio_priv(indio_dev)` | `include/linux/iio/iio.h:727-730` | `iio_device_alloc` 时按 `sizeof_priv` 分配的那块 | 分配时自动 |
| `iio_device_get_drvdata(indio_dev)` | `:712-715` | `dev_get_drvdata(&indio_dev->dev)` | 你调 `iio_device_set_drvdata`（`:701`） |
| `iio_trigger_get_drvdata(trig)` | `include/linux/iio/trigger.h:125-128` | 触发器的私有指针 | 你调 `iio_trigger_set_drvdata`（`:114`） |

**错用会怎样：** 三个都返回 `void *`，**编译器一个都拦不住**。

- 拿 `iio_device_get_drvdata` 当 `iio_priv` 用（没调过 set）→ 返回 NULL，第一次解引用就崩
- 在触发器的 ops 回调里用 `iio_priv` → 参数是 `struct iio_trigger *`，类型不匹配会被编译器发现；但如果你手里正好也有 `indio_dev`，就会静默取到错的那块内存

**默认只用 `iio_priv`。** 另两个 `_drvdata` 是给"私有数据不在 `iio_dev` 里"的场合准备的（比如触发器，它没有 `iio_dev`）。角色 A/B 全程只需要 `iio_priv`。

### §8.6 `iio_triggered_buffer_setup` / `iio_triggered_event_setup`

| 符号 | 位置 | 装的是 | 全树消费者 |
|---|---|---|---|
| `iio_triggered_buffer_setup` | `include/linux/iio/triggered_buffer.h:20`（宏） | 缓冲区 + pollfunc | 大量（`04-extensions.md` §2.4） |
| `iio_triggered_event_setup` | `include/linux/iio/triggered_event.h:7-9`（函数） | **只有 pollfunc，没有缓冲区** | **1 个**：`drivers/iio/adc/hi8435.c:512` |

**错用会怎样：** `iio_triggered_event_setup` 不建缓冲区，它是给"触发器到来时产生 IIO 事件"用的，不是给采样用的。用它替代 buffer 版本，结果是 `/dev/iio:deviceN` 读不出数据而且找不到原因——**没有任何报错**，因为两个函数都成功了。

一个说明口径的细节：`hi8435` 的 Kconfig（`drivers/iio/adc/Kconfig:470-473`）只写了 `depends on SPI`，**没有任何架构限制**——"arm64 上碰不到"这个结论来自 DTS 侧的普查（arm64 DTS 里 0 个节点、arm32 DTS 里 1 个，`04-extensions.md` §3.1），不是来自 Kconfig。这两条证据的强度不一样：Kconfig 是"能不能编进去"，DTS 是"本树里实际有没有人用"。**在 arm64 上你需要的一定是 buffer 版本。**

### §8.7 两个头文件：`iio/types.h` 和 `iio/iio.h`

| 常量族 | 在哪 |
|---|---|
| `IIO_VAL_INT` / `IIO_VAL_FRACTIONAL` / … | `include/linux/iio/types.h:24-32` |
| `IIO_CHAN_INFO_RAW` / `IIO_CHAN_INFO_PROCESSED` / … | `include/linux/iio/types.h:40` 起 |
| `IIO_CPU` / `IIO_BE` / `IIO_LE`（`enum iio_endian`） | **`include/linux/iio/iio.h:29-33`** |
| `INDIO_DIRECT_MODE` / `INDIO_BUFFER_*` | `include/linux/iio/iio.h:367-372` |

**错用会怎样：** 只 include `types.h` 就用 `IIO_LE`，报 `IIO_LE 未声明`。名字带 `IIO_` 前缀不代表都在 `types.h` 里。实践上 include `iio.h` 就够了（它自己会带上 `types.h`）。

### §8.8 `iio_buffer_get` / `iio_buffer_put` 在关掉 `CONFIG_IIO_BUFFER` 时**返回类型都变了**

这是全篇最隐蔽的一组：

| `CONFIG_IIO_BUFFER` | `iio_buffer_get` 的原型 | 位置 |
|---|---|---|
| 开 | `struct iio_buffer *iio_buffer_get(struct iio_buffer *)` | `include/linux/iio/buffer_impl.h:159` |
| 关 | `static inline void iio_buffer_get(struct iio_buffer *) {}` | `:164` |

**返回值从 `struct iio_buffer *` 变成 `void`。** 所以 `buffer = iio_buffer_get(buffer);` 这种写法（核心自己在 `drivers/iio/industrialio-buffer.c:1988` 就是这么写的）在关掉 `CONFIG_IIO_BUFFER` 时**编译不过**。

对角色 A/B/C/D 这不构成问题（这些角色不直接调这两个函数）。**角色 F 要注意**：你的后端代码本来就活在 `CONFIG_IIO_BUFFER` 里，但如果你写的是同时支持有无缓冲区两种配置的驱动，用了返回值就会在某个 defconfig 上炸。安全写法是把返回值丢掉。

---

## §9 篇末速查与导航

### §9.1 各角色的最小成对表

一张表覆盖全篇所有"必须配对"的调用。**devm 列为"—"表示该路径没有 devm 版本。**

| 角色 | 获取 / 进入 | 释放 / 退出 | devm 版本 |
|---|---|---|---|
| A/B | `iio_device_alloc` | `iio_device_free` | `devm_iio_device_alloc` |
| A/B | `iio_device_register`（宏） | `iio_device_unregister` | `devm_iio_device_register`（宏） |
| B | `iio_triggered_buffer_setup`（宏） | `iio_triggered_buffer_cleanup` | `devm_iio_triggered_buffer_setup`（宏） |
| B | trigger handler 被调 | `iio_trigger_notify_done` | — |
| B | `iio_device_claim_direct_mode` | `iio_device_release_direct_mode` | — |
| C | `iio_channel_get` | `iio_channel_release` | `devm_iio_channel_get` |
| C | `iio_channel_get_all` | `iio_channel_release_all` | `devm_iio_channel_get_all` |
| D | `iio_channel_get_all_cb` | `iio_channel_release_all_cb` | — |
| D | `iio_channel_start_all_cb` | `iio_channel_stop_all_cb` | — |
| D | `iio_hw_consumer_alloc` | `iio_hw_consumer_free` | `devm_iio_hw_consumer_alloc` |
| D | `iio_hw_consumer_enable` | `iio_hw_consumer_disable` | — |
| E | `iio_trigger_alloc`（宏） | `iio_trigger_free` | `devm_iio_trigger_alloc`（宏） |
| E | `iio_trigger_register` | `iio_trigger_unregister` | `devm_iio_trigger_register` |
| F | `iio_buffer_get` | `iio_buffer_put` | — |

**角色 D 的四行里有三行没有 devm 版本**，所以回调缓冲区和 hw-consumer 的启停必须自己在 remove / suspend 路径里配对，这是角色 D 比角色 C 容易出错的根本原因。

### §9.2 "没报错但不工作"的对照表

本篇里所有**静默失败**集中在这里——它们的共同点是每一步的返回值都是 0：

| 现象 | 原因 | 去 |
|---|---|---|
| sysfs 目录里没有我期望的文件名 | `info_mask_*` 用错了共享级别 | §1.4 |
| `cat in_voltage0_raw` 返回奇怪的数 | `.read_raw` 返回了 0 而不是 `IIO_VAL_INT` | §1.5 |
| 缓冲区里数据错位 | `scan_type.storagebits` 填小了 | §2.3 |
| 缓冲区只出一个样本就停 | 漏了 `iio_trigger_notify_done` | §2.4 |
| 样本里没有时间戳 | 驱动侧或用户态少做了一半 | §2.5 陷阱 1 / §7.3 |
| 读数一直不对但不报错 | DTS phandle 接到了错的通道类型 | §3.6 |
| 回调一次都不进 | 漏了 `iio_channel_start_all_cb` | §4.2 |
| hw-consumer 什么都不发生 | 它本来就不传数据 | §4.1 |
| `/dev/iio:deviceN` 读不出东西 | 用了 `iio_triggered_event_setup` | §8.6 |
| 提供方模块无法卸载 | release 用错了变体，引用泄漏 | §8.1 |

### §9.3 编译期就会拦住你的（好消息）

作为对比，下面这些错**不会**变成运行时谜题：

| 错误 | 报什么 |
|---|---|
| 只 include `trigger.h` 用 `devm_iio_trigger_alloc` | 未声明（宏在 `iio.h:735`，§0.4） |
| 只 include `types.h` 用 `IIO_LE` | 未声明（枚举在 `iio.h:29-33`，§8.7） |
| 用了 D7 那四个幽灵函数 | 链接期 undefined symbol（§8.4） |
| `.differential = 1` 但没 `.indexed = 1` | 运行期 `WARN` + 注册失败（`industrialio-core.c:1085`，§1.4） |
| 关掉 `CONFIG_IIO_BUFFER` 后用 `iio_buffer_get` 的返回值 | 类型不匹配（§8.8） |

最后一行的 `WARN` 严格说不是编译期，但它会在 probe 时立刻打印并让注册失败，属于"当场就知道"那一类。

### §9.4 去别的篇

| 想知道 | 去 |
|---|---|
| 本篇用到的每个 API 的完整签名和错误码语义 | `01-api.md` |
| `iio_dev` / `iio_chan_spec` / `iio_buffer` 的完整字段和相互关系 | `02-datastructures.md` |
| 注册、缓冲区使能、触发这三条主流程逐段发生了什么 | `03-internals.md` |
| 七个子框架各自的实现细节和 arm64 相关性分诊 | `04-extensions.md` |
| `io-channels` / `io-channel-names` 怎么写、谁消费、配错怎么定位 | `06-devicetree.md` |
| tracepoint、debugfs、错误码分诊、用户态工具 | `07-debug.md` |
| 为什么是这个设计（触发器为什么独立于设备、raw 和 processed 为什么分开） | `08-feynman.md` |
| 本篇每个数字的重跑命令、未验证项、证据等级 | `99-evidence.md` |

### §9.5 再说一遍：骨架未编译

§0.1 的话在这里重复一次，因为读到篇末的人很可能已经开始复制粘贴了：**本篇所有代码都没有编译过。** 符号名、宏名、字段名、行号都逐个核对过；参数顺序、include 清单、错误处理完整性没有。把它当"检查清单"用，不要当"起始模板"用。
