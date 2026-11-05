---
title: 费曼学习法
---

# regmap 08：大白话解释

**本篇的目标不是完整，是"讲透最容易想错的那几处"。**
每个主题四段式：**问题 → 先举个例子 → 回到代码 → 这个类比什么时候会误导你。**
**类比后面一定跟源码行号** ——只讲故事不给依据的段落在本套文档里不算通过。

**九个主题**，其中 §2 和 §3 是本子系统**最容易被类比错的那一组关系**。

---

## §1 regmap 到底解决什么问题？为什么不能直接 `writel()`？

### 先举个例子

想象你要给十个国家的供货商发同一份订单。**订单内容是一样的**：
"把第 3 号货架的库存改成 100"。**但送达方式完全不同**：
一家只收传真，一家只收电子邮件，一家要你打电话口述，还有一家要求所有数字必须倒着写。

**笨办法**：为每家供货商写一份完整的下单流程。
**十家供货商 × 二十种订单 = 两百份文档，改一种订单要改十处。**

**regmap 就是那个"你只写订单内容，我负责按对方的规矩送出去"的中间人。**
你说"第 0x10 号寄存器写 0x1"，**它自己决定是发一次 I2C 传输、写一次 MMIO、还是发一串 SPI 字节。**

### 回到代码

同一段业务代码，换一行初始化就换了总线（05 §1.4）：

```c
	/* MMIO 芯片 */
	map = devm_regmap_init_mmio(&pdev->dev, base, &foo_config);
	/* I2C 芯片 —— 下面所有读写代码一个字都不用改 */
	map = devm_regmap_init_i2c(client, &foo_config);

	regmap_write(map, 0x10, 0x1);		/* 与总线无关 */
```

**树里有 12 种通用适配、22 个 `regmap_bus` 实例，加上目录外自定义的共 61 个**（04 §6）。
**本树 94 个导出符号里，绝大多数是这套"与总线无关"的读写 API**（01 §1）。

### ⚠️ 这个类比什么时候会误导你？

**"中间人"这个说法会让你以为 regmap 只是转发，没有自己的状态。** **它有，而且很重要**：

| regmap 自己持有的状态 | 后果 |
|---|---|
| **寄存器缓存** | **你的"写"可能根本没到硬件**（04 §2 / §6 本篇） |
| **一把锁** | **两个 regmap 指向同一段寄存器时锁不互斥**（05 §3.2） |
| **分页选择器的当前值** | **地址会被悄悄改写**（§8 本篇） |

⇒ **regmap 不是"透明代理"，是"有记忆、有主见的代理"。**
**90% 的 regmap 疑难杂症都源于把它当成了透明的。**

---

## §2 `regmap_config` 和 `regmap_bus` 为什么是两个东西？

**这是本子系统第一号易错类比。**

### 先举个例子

回到 §1 的订单。**要把订单送出去，你需要两份完全不同的知识**：

**知识 A：这家供货商的货架是怎么编号的。**
货架号是三位数还是五位数？编号从 0 开始还是从 1 开始？
第 7 号货架是只读的（写了会被无视）？第 12 号货架每次看都会变（不能记在小本子上）？

**知识 B：怎么把一句话送到这家供货商手里。**
传真还是邮件？一次能送几条？数字要不要倒着写？

**这两份知识没有任何交集**：
**换供货商（A→B）不改送法**（同一家 SoC 的两颗不同芯片，都挂在同一条 I2C 上）；
**换送法不改货架编号**（同一颗芯片，有的板子用 I2C 接、有的板子用 SPI 接）。

⇒ **所以内核把它们拆成两个结构：`regmap_config` 是知识 A，`regmap_bus` 是知识 B。**

### 回到代码

| | `struct regmap_config` | `struct regmap_bus` |
|---|---|---|
| 在哪 | `include/linux/regmap.h:370` | `include/linux/regmap.h:544` |
| 字段数 | **54** | **19** |
| 谁写 | **芯片驱动的作者**（每颗芯片一份） | **总线适配的作者**（每种总线一份，全树 61 个实例） |
| 内容 | `reg_bits` / `val_bits` / `max_register` / `volatile_reg` / `cache_type` / `ranges`… | **`reg_read` / `reg_write` / `read` / `write` 一堆函数指针** |
| **回答的问题** | **"这块寄存器长什么样"** | **"字节怎么搬过去"** |

**两者在 `__regmap_init()` 里第一次相遇**，而且是**config 压过 bus**：

```c
/* drivers/base/regmap/regmap.c:717-722 —— bus 只提供"默认值"，config 说了算 */
	if (bus && bus->val_format_endian_default)
		endian = bus->val_format_endian_default;
```
**这段代码在 `config->val_format_endian` 已经被检查过（`:698-702`）之后才执行**（06 §2.2）。

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"两份知识没有交集"这句话在字段名上不成立** ——**有三对同名字段**：

| 字段名 | 在 config 里 | 在 bus 里 | 怎么合并 |
|---|---|---|---|
| `fast_io` | `include/linux/regmap.h:403` | `:545` | **取或**（`drivers/base/regmap/regmap.c:789-790` 的 `(bus && bus->fast_io) \|\| config->fast_io`）⇒ **总线说了要 spinlock，驱动无法退回 mutex** |
| `reg_format_endian` / `..._default` | `:428` | `:558` | config 优先（`regmap.c:672-687`） |
| `val_format_endian` / `..._default` | `:429` | `:559` | config 优先（`regmap.c:698-722`） |

⇒ **看到同名字段不要以为是重复定义**，**它们的合并规则各不相同，必须逐个查。**

---

## §3 为什么 regmap 没有自己的"总线"，`i2c_adapter` 却有？

**这是本子系统第二号易错类比，而且比 §2 更常见。**

### 先举个例子

很多人第一次看 regmap 会这样类推：
> "I2C 有 `i2c_bus_type`，设备注册在上面；**那 regmap 应该也有个 `regmap_bus_type`，
> 我的芯片会作为一个 `regmap_device` 注册上去吧？"**

**这个类推是错的，而且方向完全反了。**

**换个例子理解**：**"总线"像一条真实的街道** ——
街上有门牌号，邮递员按门牌送信，每栋房子只能在一条街上。
**`i2c_bus_type` 是街道**，你的芯片是街上的一栋房子，有确定的地址（`0x4b`）。

**regmap 不是街道，是"写信的模板 + 一支会写各种字体的笔"。**
**它不给任何人分配地址，也不管谁住在哪** ——
**它只是被住在街上的房子拿来用的一个工具。**

### 回到代码

**判定过的两道门**（99 §1 登记了实际输出）：

| 门 | 命令 | regmap 的结果 |
|---|---|---|
| **门 1：有没有 `bus_type`** | `rg 'struct bus_type' drivers/base/regmap/*.c include/linux/regmap.h` | **无输出** |
| 门 2：ABI 在不在 uapi | `ls include/uapi/linux/regmap*` | **0 个文件** |

⇒ **regmap 是框架型，不是总线型**（对比：i2c / spi / virtio 门 1 都有输出）。

**所以规模上看到的这些数字，含义和总线型子系统不一样**：
**94 个导出符号全是"给别人调的函数"**，**一个 `xxx_register_driver()` 都没有**（01 §1）。

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"regmap 只是个工具，不参与设备模型"这句话有一个例外**：

```c
/* drivers/base/regmap/regmap.c:653 处导出 —— regmap 确实会挂到 device 上 */
	regmap_attach_dev(dev, map, config);
```
**它用 devres 把 map 挂在 `struct device` 上**，
**这样别人才能用 `dev_get_regmap(dev, name)`（`regmap.c:1615` 处导出）反查**（05 §3.1）。

⇒ **regmap 不是总线，但它确实是"设备的一个属性"。**
**这就是 MFD 父子设备共享 regmap 的机制**（05 §3）——
**不要因为"regmap 不在设备模型里"就以为拿不到别人的 regmap。**

---

## §4 为什么字节序要分"地址"和"数据"两套？

### 先举个例子

你给国外的仓库寄一张明信片。**明信片上有两样东西**：
**信封上的门牌号**，和 **卡片正面写的库存数字**。

**这两样东西的书写规范可以不一样。**
门牌号是这个国家的邮政系统定的（不能改），
**库存数字的写法是你和仓库管理员约定的**（可以在明信片上注明"本卡数字倒着读"）。

⇒ **所以 regmap 有两个字节序，而且只有其中一个能在设备树里配。**

### 回到代码

| | 地址的字节序 | 数据的字节序 |
|---|---|---|
| 函数 | `regmap_get_reg_endian()`（`drivers/base/regmap/regmap.c:666`） | `regmap_get_val_endian()`（`regmap.c:690`） |
| 读设备树吗 | ⚠️ **不读**（`:672-687` 只看 config 和 bus） | **读**（`:705-710` 的三个 `fwnode_property_read_bool`） |
| 兜底值 | `REGMAP_ENDIAN_BIG`（`:687`） | `REGMAP_ENDIAN_BIG`（`:725`） |

⇒ **DTS 里写 `big-endian;` 只改数据，不改地址**（06 §1 第 2 条）。

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"门牌号规范不能改"暗示地址字节序是硬件定死的，其实不是** ——
**它只是"不能在设备树里改"，驱动仍然可以在 `config.reg_format_endian` 里定**
（`include/linux/regmap.h:428`）。

⚠️ **更要紧的是：兜底值是大端，这和"arm64 是小端"的直觉相反。**
**MMIO 之所以表现成小端，是因为 MMIO 总线在第三级把默认值改掉了**
（`drivers/base/regmap/regmap-mmio.c:428` 的 `.val_format_endian_default = REGMAP_ENDIAN_LITTLE`）。
⇒ **换成一个没设这个默认值的总线（如 `regmap-i2c.c:327` 那条传 `NULL` 的路），就落回大端了**（06 §2.2）。

---

## §5 缓存为什么要驱动手动标 `volatile_reg`？内核不能自己知道吗？

### 先举个例子

你在小本子上抄下仓库的库存，避免每次都跑一趟。**哪些数字能抄，哪些不能？**

- **"3 号货架的容量上限"** ——**抄下来永远对**，除了你自己去改
- **"3 号货架当前的实际库存"** ——⚠️ **别人也会动它**，抄下来立刻过期
- ⚠️ **"最近一次报警的原因"** ——**看一眼这个数字，它就自动清零了**（看的动作有副作用）

**小本子（缓存）只能抄第一类。**
**而"这个数字属于哪一类"，只有读过硬件手册的人知道** ——**内核读不到手册。**

### 回到代码

**所以 `regmap_config` 里有四个"分类回调"**：

| 回调 | 位置 | 含义 | 不标的后果 |
|---|---|---|---|
| `writeable_reg` | `include/linux/regmap.h:380` | 能不能写 | **写非法寄存器返回 `-EIO`**（07 §5.3） |
| `readable_reg` | `:381` | 能不能读 | |
| `volatile_reg` | `:382` | **"别人也会动它"** | ⚠️ **状态寄存器被缓存住，永远读到旧值**（04 §2.4 / 05 §1.2） |
| `precious_reg` | `:383` | **"读一下就变"** | **debugfs 转储时把状态读没了**（07 §2.4） |

**`volatile` 的判断在读写路径的最核心处被用到**：

```c
/* drivers/base/regmap/regmap.c:3253 —— volatile 的寄存器走完全不同的分支 */
	if (regmap_volatile(map, reg) && map->reg_update_bits) {
```

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"小本子只能抄第一类"暗示 `volatile` 就是"不缓存"，但它做的事更多。**
**上面那行 `regmap.c:3253` 说明**：**`volatile` 还决定了读-改-写走哪条实现**（§7 本篇）。

⚠️ **另一个误导**：**`precious` 和 `volatile` 不是同一件事，也不能互相替代。**
**`volatile` = "不要缓存"；`precious` = "不要主动读"。**
**一个中断状态寄存器通常两个都要标。**

---

## §6 `cache_only` 和 `cache_bypass` 为什么是两个开关，不是一个三态？

### 先举个例子

还是小本子和仓库。**有两种反常状态**：

**状态 A：仓库锁门了（设备断电）。**
**你还得继续接单**，所以：**改动只记在小本子上，等仓库开门再一次性搬过去。**

**状态 B：你正在把小本子上的内容往仓库搬。**
这时候的每一次"写"都必须真的落到仓库，
**而且不能再写回小本子**（会和正在遍历的内容打架）。

**两个状态都不是"正常"，但它们是反方向的**：
**A 是"只写本子不去仓库"，B 是"只去仓库不写本子"。**
⇒ **所以不能压成一个三态开关 —— 它们在语义上是两根独立的轴。**

### 回到代码

| | `regcache_cache_only(map, true)` | `regcache_cache_bypass(map, true)` |
|---|---|---|
| 位置 | `drivers/base/regmap/regcache.c:536` | `drivers/base/regmap/regcache.c:580` |
| 写去哪 | **只进缓存** | **只进硬件** |
| 谁用 | **驱动的 suspend**（05 §6.1） | **`regcache_sync()` 内部自用** |

**核心确实认为"两个同时开"是错的，而且用 `WARN_ON` 抓**：

```c
/* drivers/base/regmap/regcache.c:539-540 */
	WARN_ON(map->cache_type != REGCACHE_NONE &&
		map->cache_bypass && enable);
/* drivers/base/regmap/regcache.c:583 —— 对称的一条 */
	WARN_ON(map->cache_only && enable);
```

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"核心会用 `WARN_ON` 抓住你"这个安全感是假的** ——**debugfs 那条路绕过了它。**

**两个 debugfs writer 直接赋值，一个 `WARN_ON` 都没有**：
`drivers/base/regmap/regmap-debugfs.c:486` 的 `map->cache_only = new_val;` ·
`regmap-debugfs.c:532` 的 `map->cache_bypass = new_val;`

⇒ **`echo Y > cache_only; echo Y > cache_bypass` 会静默进入"写既不进硬件也不进缓存"的状态**（07 §2.3）。
**调试时开过这两个开关，一定要记得复位。**

⚠️ **第二个误导**：**"关掉 cache_only 就恢复正常"是错的。**
**通过 debugfs 关掉会触发一次真实的 `regcache_sync()`**
（`drivers/base/regmap/regmap-debugfs.c:491-492`）⇒ **设备没上电时会打出一串总线错误。**

---

## §7 `regmap_update_bits` 为什么"值没变就不写"？

### 先举个例子

你的任务是"把 3 号货架的标签改成蓝色"。**两种做法**：

**做法一（老实人）**：不管现在什么颜色，撕掉旧标签，贴一张蓝的。
**做法二（省事人）**：先看一眼，已经是蓝的就什么都不做。

**绝大多数时候做法二更好**：省一次操作、省一次总线传输。
⚠️ **但有一种货架，标签是"撕下来才算处理完"的** ——
**比如"报警标签"：它现在是红的，你要贴一张红的上去，才代表"我确认了这个报警"。**
**做法二看到"已经是红的"，就什么都不做 ⇒ 报警永远确认不掉。**

### 回到代码

```c
/* drivers/base/regmap/regmap.c:3259-3271 —— 做法二就在这几行 */
	} else {
		ret = _regmap_read(map, reg, &orig);
		if (ret != 0)
			return ret;

		tmp = orig & ~mask;
		tmp |= val & mask;

		if (force_write || (tmp != orig)) {	/* 关键的这一行 */
			ret = _regmap_write(map, reg, tmp);
			...
		}
	}
```

**`force_write` 就是"做法一"的开关**，而它由调用哪个包装函数决定（05 §7.2）：

| API | 位置 | `force` |
|---|---|---|
| `regmap_update_bits` | `include/linux/regmap.h:1184` | **false ⇒ 做法二** |
| `regmap_write_bits` | `include/linux/regmap.h:1213` | **true ⇒ 做法一** |

⇒ **"写 1 清中断"型寄存器必须用 `regmap_write_bits`**，否则中断永远清不掉（05 §7.2 / 07 §0）。

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"先看一眼"这个说法漏掉了一整条分支** ——**有些寄存器根本不走"读-比较-写"**：

```c
/* drivers/base/regmap/regmap.c:3253-3258 —— volatile 且总线支持时，交给硬件原子改 */
	if (regmap_volatile(map, reg) && map->reg_update_bits) {
		reg += map->reg_base;
		reg >>= map->format.reg_downshift;
		ret = map->reg_update_bits(map->bus_context, reg, mask, val);
		if (ret == 0 && change)
			*change = true;		/* 注意这一行 */
	} else {
```

⚠️ **这条分支上 `*change` 会说谎**：**只要总线调用成功就置 `true`**（`:3257-3258`），
**完全没有比较新旧值。**
⇒ **`regmap_update_bits_check()`（`include/linux/regmap.h:1196`）返回的"改没改"，
在"volatile 寄存器 + 总线提供了 `reg_update_bits` 回调"的组合下不可信。**
**两个条件都满足才会命中，所以这个坑不常见，但一旦命中就完全静默。**

---

## §8 分页（`ranges`）为什么驱动完全看不见？

### 先举个例子

一栋楼只有 16 个可用的信箱格子，**但楼里住了 1600 户。**
**办法是加一个"楼层拨盘"**：先把拨盘转到 37 层，那 16 个格子就代表 37 层的 16 户。

**住户不需要知道这件事。** **他只说"我要给 592 号送信"**，
**门房自己算出 592 = 37 层 × 16 + 第 0 格，把拨盘转过去，再往第 0 个格子塞。**

⇒ **regmap 的分页就是这个门房。**

### 回到代码

**算式就是两行**：

```c
/* drivers/base/regmap/regmap.c:1640-1641 —— 门房的心算 */
	win_offset = (*reg - range->range_min) % range->window_len;
	win_page   = (*reg - range->range_min) / range->window_len;
```

**然后把"楼层"写进选择器寄存器，把地址改写成窗口内的偏移**：

```c
/* drivers/base/regmap/regmap.c:1662-1665 —— 转拨盘 */
		ret = _regmap_update_bits(map, range->selector_reg,
					  range->selector_mask,
					  win_page << range->selector_shift,
					  &page_chg, false);
/* drivers/base/regmap/regmap.c:1673 —— 改写调用者给的地址 */
	*reg = range->window_start + win_offset;
```

⇒ **驱动写的是 `regmap_write(map, 592, v)`，真正上总线的地址是窗口内的偏移**
⇒ **这就是"tracepoint 里 `regmap_reg_write` 的 `reg` 和 `regmap_hw_write_start` 的 `reg`
可能完全不一样"的原因**（07 §3.6 第 3 条）。

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"住户完全不需要知道"这句话有三个例外，全都会变成 `-EINVAL`**：

| 例外 | 位置 | 为什么 |
|---|---|---|
| **批量访问不能跨 range 边界** | `drivers/base/regmap/regmap.c:1645-1646` | **一次 `regmap_bulk_write` 不能横跨两个分页区** |
| **批量访问不能跨页边界** | `regmap.c:1649-1650` | **门房没法在一次传输中途转拨盘** |
| ⚠️ **选择器寄存器落在窗口里时的特例** | `regmap.c:1653-1657`（**注释原话说明了这种硬件**） | **单独访问选择器本身不需要转页**，因为它在每一页都出现 |

⇒ **分页对单寄存器访问是完全透明的，对批量访问不是。**
**"用了 ranges 之后 bulk 读写莫名 `-EINVAL`"几乎总是撞上前两条。**

---

## §9 `fast_io` 为什么是配置项，不是内核自动判断？

### 先举个例子

两种取货方式：
**一、走到隔壁房间拿**（几十纳秒）——**等着就行，不用坐下。**
**二、开车去城外的仓库**（几毫秒）——**你得先"让出位子"给别人，回来再继续。**

**内核里"让出位子"叫睡眠，"等着不动"叫自旋。**
**MMIO 是第一种（一条 `writel` 指令），I2C / SPI 是第二种（要等总线控制器）。**

**那为什么不让内核自己判断？** ——**因为它判断不了。**
同一个 `regmap_bus` 在不同板子上可能快也可能慢（`regmap-mmio` 后面可能挂着一个慢速外部总线桥），
**而"能不能睡"取决于调用者在什么上下文里调它，不取决于总线本身。**

### 回到代码

**`fast_io` 一置，改的不止是锁**：

```c
/* drivers/base/regmap/regmap.c:789-811 —— 三级锁选择 */
		if ((bus && bus->fast_io) || config->fast_io) {
			if (config->use_raw_spinlock) {
				raw_spin_lock_init(&map->raw_spinlock);	/* PREEMPT_RT 用 */
				...
			} else {
				spin_lock_init(&map->spinlock);
				...
			}
		} else {
			mutex_init(&map->mutex);
			...
			map->can_sleep = true;			/* 顺带置了这个 */
			...
		}
```

**第二个后果在下面十行**：

```c
/* drivers/base/regmap/regmap.c:819-822 —— 连内存分配标志都跟着变 */
	if ((bus && bus->fast_io) || config->fast_io)
		map->alloc_flags = GFP_ATOMIC;
	else
		map->alloc_flags = GFP_KERNEL;
```

⇒ **`fast_io = true` 意味着：spinlock + `GFP_ATOMIC` + `can_sleep = false`，三件事一起变。**

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"MMIO 快所以置 `fast_io`，I2C 慢所以不置"这个简化在两处会咬人**：

1. **`fast_io` 是"或"关系，驱动关不掉。**
   **`(bus && bus->fast_io) || config->fast_io`（`regmap.c:789-790`）**
   ⇒ **总线适配层置了 `fast_io`，驱动的 `config.fast_io = false` 没有任何作用**（§2 本篇）。
2. ⚠️ **`fast_io` 不是"性能选项"，是"上下文声明"。**
   **它真正的判据是"会不会在原子上下文里被调用"**，不是"这条总线有多快"。
   ⇒ **在中断处理里读寄存器就必须置它**（05 §1.2），
   **哪怕那条总线其实很慢** ——**慢的总线在原子上下文里本来就不该用。**

**第三级 `use_raw_spinlock`（`include/linux/regmap.h:435`，
判断在 `drivers/base/regmap/regmap.c:791`）是给 PREEMPT_RT 的**：
**RT 上普通 spinlock 会变成可睡眠的 rt_mutex，只有 `raw_spinlock` 才真自旋。**
**本文档没有在 RT 内核上验证过这条路径**（99 §5 第三层）。

---

## §10 阅读下一篇

| 想知道 | 去 |
|---|---|
| 复习整个子系统 | `00-overview.md` |
| API 完整清单 + `regmap_config` 54 个字段 | `01-api.md` |
| 结构体关系与命名约定 | `02-datastructures.md` |
| 初始化 / 读写主流程逐段剖析 | `03-internals.md` |
| 缓存 / 分页 / 中断 / debugfs 四个子框架 | `04-extensions.md` |
| **代码骨架 + 近邻符号区分** | `05-usage.md` |
| DT 属性与静默失效 | `06-devicetree.md` |
| 调试出口速查 | `07-debug.md` |
| **证据等级与未验证项** | `99-evidence.md` |
