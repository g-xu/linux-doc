---
title: 扩展机制
---

# reset 04 —— 控制器驱动的几个"家族"

## 本篇的组织轴（先看这一段）

reset 框架**没有子框架**。它不像 i2c 有 mux/slave/smbus 那样的"核心之上再长一层"的东西：
`drivers/reset/core.c` 只有一层，`01-api.md` §0 已经把这一点量化过（一个核心文件 + 四个函数指针）。

所以本篇按模板的替代口径写：**把 39 个控制器驱动按"它们怎么把一个复位位变成寄存器动作"分成几个家族，
一节一个家族，各家族之间互不调用。** 这一点很重要，请当成阅读前提：

> **本篇每一节都是独立的实现家族，彼此没有调用关系。**
> 唯一的共同点是它们都填 `struct reset_control_ops` 并调用 `(devm_)reset_controller_register()`，
> 也就是都挂在 `03-internals.md` §A1 那一个时间点上。跨节的相似写法是"抄同一个套路"，不是"调用"。

还有一条前提：**本篇讲的全是"provider 侧"**。消费者侧（谁去 get/assert）在 `05-usage.md`；
DTS 怎么写在 `06-devicetree.md`。

### 0.1 哪几个家族在 arm64 上真会碰到（分诊表）

判据是"这个家族的 compatible 在 `arch/arm64/boot/dts/` 里有没有真实节点"。
统计命令记在 `99-evidence.md` §3.4，结论如下：

| 家族 | 代表文件 | arm64 相关性 | 为什么 |
|---|---|---|---|
| §1 `reset-simple` 通用位操作 | `drivers/reset/reset-simple.c` | 相关 | `snps,dw-low-reset`（arm64 DTS 里 2 个文件 10 处）、`altr,stratix10-rst-mgr`（2 个文件）、`bitmain,bm1880-reset`、`brcm,bcm4908-misc-pcie-reset` 都走它 |
| §2 syscon / regmap 家族 | `reset-ti-syscon.c`、`hisilicon/reset-hi3660.c` | 最相关 | 39 个驱动里 14 个 `#include <linux/regmap.h>`、11 个提到 syscon；`hisilicon,hi3660-reset` 在 arm64 DTS 里 4 处 |
| §3 固件家族 | `reset-scmi.c`、`reset-ti-sci.c`、`tegra/reset-bpmp.c`、`reset-zynqmp.c` | 最相关 | 这是 arm64 服务器/新 SoC 的主流形态：复位不由内核写寄存器，而是发消息给固件。`ti,sci-reset` 在 arm64 DTS 里 7 处 |
| §4 非平凡挂载点 | `reset-scmi.c`（`scmi_driver`）、`reset-a10sr.c`（MFD 子设备）、3 个 initcall 驱动 | 局部相关 | 这一节不是新家族，是"上面几个家族怎么被 probe 出来"的补充维度 |
| §5 三个子目录 | `hisilicon/`、`sti/`、`tegra/` | 部分相关 | `hisilicon/` 是 `obj-y` 无条件编译、`tegra/` 是 arm64 平台；`sti/` 是 arm32（`CONFIG_ARCH_STI`），**本篇只把它当对照** |
| §6 一硬件双身份 | `drivers/clk/` 下 33 个文件 | 最相关 | 全树 90 个注册复位控制器的文件里，只有 43 个在 `drivers/reset/`；剩下 47 个里 33 个在 `drivers/clk/` |

**一句话分诊**：如果你手上是一块新的 arm64 SoC，先去 §3 和 §6 找答案，
其次 §2；`reset-simple`（§1）反而更像"老的、直接映射寄存器的板子"。

### 0.2 不在本篇的内容去哪找

| 你想问的 | 去处 |
|---|---|
| 这些 `ops` 回调被谁调用、什么时候调用 | `03-internals.md` §A4（五个动作的统一骨架） |
| 注册函数本身怎么实现、devres 顺序 | `03-internals.md` §A1 |
| 我要**写**一个控制器驱动，骨架长什么样 | `05-usage.md` §4 |
| 这些 compatible 对应的 DTS 怎么写 | `06-devicetree.md` |

---

## §1 `reset-simple` —— 通用位操作家族

### 1.1 它解决的问题：一片连续寄存器，一位一条复位线

绝大多数老式 SoC 的复位控制器就是一段 MMIO：第 N 位置 1 = 第 N 条复位线断言。
`drivers/reset/reset-simple.c`（205 行）把这个形状做成了通用实现。

数据结构在**独立头文件**里，这是它和其他驱动最大的不同：
`include/linux/reset/reset-simple.h`（48 行）的 `struct reset_simple_data`（`include/linux/reset/reset-simple.h:37-44`）：

| 字段 | 作用 | 备注 |
|---|---|---|
| `lock` | spinlock | 保护"读-改-写"这个序列，不是保护 rcdev |
| `membase` | `void __iomem *` | probe 时已经加过 `reg_offset` 偏移（见 1.4） |
| `rcdev` | 内嵌的 `reset_controller_dev` | `container_of` 反查的锚点 |
| `active_low` | true = 写 0 表示断言 | 决定 assert 时写什么值 |
| `status_active_low` | true = 位为 0 表示"正在复位" | **和上一个字段独立**，硬件真的会不一致 |
| `reset_us` | `reset()` 里 assert 与 deassert 之间的延时（微秒） | kernel-doc（`include/linux/reset/reset-simple.h:19-36`）明确写 **0 表示未知，此时 `reset()` 不被支持** |

### 1.2 四个回调：一个函数干完三件事

核心是 `reset_simple_update()`（`drivers/reset/reset-simple.c:31-53`），一个函数被 assert / deassert 共用：

```c
	struct reset_simple_data *data = to_reset_simple_data(rcdev);
	int reg_width = sizeof(u32);
	int bank = id / (reg_width * BITS_PER_BYTE);
	int offset = id % (reg_width * BITS_PER_BYTE);
	unsigned long flags;
	u32 reg;

	spin_lock_irqsave(&data->lock, flags);

	reg = readl(data->membase + (bank * reg_width));
	if (assert ^ data->active_low)
		reg |= BIT(offset);
	else
		reg &= ~BIT(offset);
	writel(reg, data->membase + (bank * reg_width));

	spin_unlock_irqrestore(&data->lock, flags);

	return 0;
```

三个细节值得记住：

1. **id 到 (bank, bit) 的换算是硬编码的 32 位分组**：`reg_width` 是函数内的局部量
   `sizeof(u32)`（`drivers/reset/reset-simple.c:35`），既不来自 DTS 也不来自 devdata，
   于是 `bank = id / 32`、`offset = id % 32`（`:36-37`）。这就是为什么这个家族的
   复位 id 经常是"一个大数字"——它是 `bank * 32 + bit`。`reset_simple_status()` 里
   把同样的三行又抄了一遍（`:89-91`）。
2. **`assert ^ data->active_low`**（`drivers/reset/reset-simple.c:44`）是整个家族里唯一处理极性的地方。
   一个异或表达式替掉了本来要写四遍的 if。
3. **它恒返回 0**——MMIO 写没有失败通道。所以这个家族的 assert/deassert
   永远不会给核心返回错误码，`03-internals.md` §B3 那张错误改写表在这里全部走不到。

`reset_simple_assert()`（`drivers/reset/reset-simple.c:55-59`）和 `reset_simple_deassert()`
（`:61-65`）各自只是带不同 `assert` 参数调一次上面的函数。

**`reset_simple_reset()`（`drivers/reset/reset-simple.c:67-83`）是这个家族唯一会返回错误的动作：**

```c
	if (!data->reset_us)
		return -ENOTSUPP;
```

（`drivers/reset/reset-simple.c:73-74`）—— 然后才是 assert、`usleep_range(reset_us, reset_us * 2)`
（`:80`）、deassert。这里就是 `01-api.md` §7 里 `-ENOTSUPP` 的一个真实来源：
**DTS 和绑定都对、驱动也匹配上了，但 `reset_control_reset()` 就是返回 -524，
原因是这块硬件的复位脉冲宽度没人知道。**

`reset_simple_status()`（`drivers/reset/reset-simple.c:85-97`）的返回表达式是
`!(reg & BIT(offset)) ^ !data->status_active_low`（`:96`）——两个逻辑非再异或，
这是内核里把"位值"归一化成 0/1 再比较极性的常见写法，不是笔误。

### 1.3 它的 `ops` 被导出，所以家族成员不止本文件

`reset_simple_ops` 定义在 `drivers/reset/reset-simple.c:99-104`，**四个回调全都实现**，
紧跟着一行 `EXPORT_SYMBOL_GPL(reset_simple_ops)`（`:105`）。
头文件里也有对应的 `extern`（`include/linux/reset/reset-simple.h:46`）。

这条导出的后果是：**别的驱动可以直接借用这套 ops，只要自己内嵌一个 `reset_simple_data`。**
全树 `#include <linux/reset/reset-simple.h>` 的文件是 **6 个**：

| 文件 | 属于 | 说明 |
|---|---|---|
| `drivers/reset/reset-simple.c` | 本身 | 定义方 |
| `drivers/reset/reset-socfpga.c` | 同目录复用 | SoCFPGA 早期复位 |
| `drivers/reset/reset-sunxi.c` | 同目录复用 | Allwinner |
| `drivers/reset/reset-uniphier-glue.c` | 同目录复用 | Socionext glue 层 |
| `drivers/clk/bcm/clk-bcm2711-dvp.c` | 时钟驱动 | 一个 clk provider 顺手当 reset provider |
| `drivers/clk/sifive/sifive-prci.h` | 时钟驱动 | 同上 |

后两行是 §6 那个结论的第一手证据：**复位控制器的实现方经常根本不住在 `drivers/reset/`。**
（全称断言的完整 grep 见 `99-evidence.md` §3.4。）

### 1.4 probe：`nr_resets` 是从 `resource_size` 算出来的

`reset_simple_probe()`（`drivers/reset/reset-simple.c:158-196`）的顺序值得逐步看，
因为它是"最标准"的一个控制器 probe，`05-usage.md` §4 的骨架就以它为原型：

| 步骤 | 行 | 做什么 |
|---|---|---|
| ① | `:173-174` | `platform_get_resource()` 拿 `res`，再 `devm_ioremap_resource()` 拿 `membase`（两步，不是一步的 `devm_platform_*` 变体） |
| ② | `:178` | `spin_lock_init(&data->lock)` |
| ③ | `:180` | `data->rcdev.owner = THIS_MODULE` ← 这一行决定了 `03-internals.md` §A3 里的 `try_module_get` 能不能起作用 |
| ④ | `:181` | **`nr_resets = resource_size(res) * BITS_PER_BYTE`** —— 有多少字节就有多少位，就有多少条复位线 |
| ⑤ | `:182-183` | 填 `ops` 和 `of_node` |
| ⑥ | `:185-191` | 有 devdata 就用 devdata 覆盖前面的默认值 |
| ⑦ | `:193` | `data->membase += reg_offset` —— **偏移在这里一次性吃掉**，回调里不再关心 |
| ⑧ | `:195` | `devm_reset_controller_register()` |

④ 这一步是这个家族的签名动作：**`nr_resets` 不来自 DTS 也不来自 devdata，
而是从 `reg` 属性的长度反推**（devdata 只在非 0 时覆盖它，`drivers/reset/reset-simple.c:187-188`）。
所以 DTS 把 `reg` 的 size 写小了，后半段复位线在 get 阶段就会失败。

但**这个越界检查不在核心的通用路径上**，值得单独记一笔。全 `core.c` 里
`nr_resets` 只被读过一次，就在默认 xlate 里：

```c
	if (reset_spec->args[0] >= rcdev->nr_resets)
```

（`drivers/reset/core.c:90`，位于 `of_reset_simple_xlate()`）。而这个默认 xlate
只在控制器**没有**自己的 `of_xlate` 时才被补上（`drivers/reset/core.c:102-105`，见 `02-datastructures.md` §2.3）。
两个直接后果：

- **自己写了 `of_xlate` 的驱动必须自己查上界，核心不会替它查。** 而实际情况是：
  `drivers/reset/` 下有 **11 个驱动文件**自带 `of_xlate`，其中 **5 个**（`reset-hi3660.c`、
  `reset-berlin.c`、`reset-intel-gw.c`、`reset-npcm.c`、`reset-ti-sci.c`）
  通篇一次都没提 `nr_resets`。§2.3 会拿 hi3660 当实例展开。
- **lookup 表路径（非 DT）从头到尾没有任何 `nr_resets` 检查**，id 由 lookup 表直接给到驱动回调。

### 1.5 devdata：同一份代码怎么伺候 11 个 compatible

`struct reset_simple_devdata`（`drivers/reset/reset-simple.c:117-122`）只有四个字段
（`reg_offset` / `nr_resets` / `active_low` / `status_active_low`），
两个实例分别是 `reset_simple_socfpga`（`:126-130`，`reg_offset = 0x20`、`nr_resets = 8 * 32`、
`status_active_low`）和 `reset_simple_active_low`（`:132-135`）。

`reset_simple_dt_ids[]`（`drivers/reset/reset-simple.c:137-156`）有 **11 条 compatible**。
按 arm64 DTS 里是否出现分成两组：

| 在 arm64 DTS 里出现 | compatible |
|---|---|
| 有（你会碰到） | `snps,dw-low-reset`(2 文件)、`altr,stratix10-rst-mgr`(2)、`bitmain,bm1880-reset`(1)、`brcm,bcm4908-misc-pcie-reset`(1) |
| 无（arm32 / 已淡出） | `st,stm32-rcc`、`allwinner,sun6i-a31-clock-reset`、`zte,zx296718-reset`、`aspeed,ast2400/2500/2600-lpc-reset`、`snps,dw-high-reset` |

最后一行：`builtin_platform_driver(reset_simple_driver)`（`drivers/reset/reset-simple.c:205`），
驱动名 `"simple-reset"`。**`builtin_` 意味着它不能是模块**——这一点在 §4.2 会归纳成一条规律。

---

## §2 syscon / regmap 家族 —— arm64 上最常见的寄存器形态

### 2.0 这个家族为什么存在

arm64 SoC 上"复位位"几乎从来不是一片独占的 MMIO，而是散落在一大块
**系统控制寄存器（syscon / CRU / PMU / MISC）** 里的若干个 bit，而那块寄存器同时还被
时钟、电源、pinmux、USB PHY 等好几个驱动瓜分。两个驱动各自 `ioremap` 同一段地址
就会打架，于是内核给出的答案是 syscon + regmap：**一个 `mfd/syscon` 节点持有 regmap，
复位驱动通过 phandle 借用它。**

规模上这已经是主流：39 个 `drivers/reset/*.c` 里 **14 个** `#include <linux/regmap.h>`、
**11 个**提到 `syscon`。本节挑三个代表，它们互不调用：

| 代表 | 拿 regmap 的方式 | 一条复位线怎么描述 | 特点 |
|---|---|---|---|
| `reset-ti-syscon.c`（230 行） | 由 MFD 父设备给 | **DTS 里 7 个 32 位数字** | 断言/解除/查状态三组 offset+bit 完全独立 |
| `hisilicon/reset-hi3660.c`（约 130 行） | `syscon_regmap_lookup_by_phandle()` | 自定义 xlate，`<offset bit>` | 零越界检查 |
| `sti/reset-syscfg.c`（约 210 行） | 编译期表 + `regmap_field` | 驱动内的静态表 | 有 ack 回读，是这一族里唯一"等硬件确认"的 |

### 2.1 `reset-ti-syscon`：把寄存器布局整个搬进 DTS

`struct ti_syscon_reset_control`（`drivers/reset/reset-ti-syscon.c:29-37`）一条复位线七个字段：
`assert_offset`、`assert_bit`、`deassert_offset`、`deassert_bit`、`status_offset`、`status_bit`、`flags`。
probe 里就是照这个宽度切 DTS 数组：

```c
	nr_controls = (size / sizeof(*list)) / 7;
```

（`drivers/reset/reset-ti-syscon.c:183`）——**7 是硬编码的**，DTS 数组元素个数不是 7 的倍数
就会被整除截断，多出来的几个数字静默丢弃。这条静默失效陷阱在 `06-devicetree.md` §4 里画成决策树。

三个回调结构完全对称，`assert`（`:66-85`）为例：

| 检查 | 行 | 返回 |
|---|---|---|
| `id >= data->nr_controls` | `:73-74` | `-EINVAL` |
| `control->flags & ASSERT_NONE` | `:78-79` | `-ENOTSUPP`（注释写明"这条线不支持断言"） |
| 正常路径 | `:84` | `regmap_write_bits()` 的返回值 |

`deassert`（`:97-116`，`DEASSERT_NONE` 在 `:109-110`）和 `status`
（`:129-151`，`STATUS_NONE` 在 `:142-143`、`regmap_read` 在 `:145`）是同一个模子。
`status` 的返回表达式是
`!(reset_state & BIT(control->status_bit)) == !(control->flags & STATUS_SET)`
（`:149-150`）——和 `reset-simple` 用异或做同一件事，只是写法换成了 `==`。

**flags 的取值来自 DT 绑定头文件**，不是驱动私有：
`include/dt-bindings/reset/ti-syscon.h` 定义了 `ASSERT_NONE`(1<<0)、`DEASSERT_NONE`(1<<1)、
`STATUS_NONE`(1<<2)、`ASSERT_SET`(1<<3)、`DEASSERT_SET`(1<<4)、`STATUS_SET`(1<<5)，
以及三个恒为 0 的对偶量 `ASSERT_CLEAR`/`DEASSERT_CLEAR`/`STATUS_CLEAR`（`0 << 3/4/5`）。
**写 `ASSERT_CLEAR` 和什么都不写完全等价**——它存在的意义只是让 DTS 可读。

`ti_syscon_reset_ops`（`drivers/reset/reset-ti-syscon.c:153-157`）只填三个回调
（`assert`/`deassert`/`status`），**没有 `.reset`**。所以这一族硬件上
`reset_control_reset()` 会拿到核心给的错误码（分诊表见 `01-api.md` §6）。
它也**没有** `of_xlate`，于是默认 xlate 生效，`nr_resets`（`:202`）真的会被核心检查——
和 `:73-74` 那一层构成双保险。收尾是 `reset_controller_register` 的 devm 版本（`:209`）
和 `module_platform_driver(ti_syscon_reset_driver)`（`:225`）。

### 2.2 `sti/reset-syscfg`：唯一会等硬件回话的一族

这一族把"一条复位线"表示成两个 `regmap_field`：
`struct syscfg_reset_channel`（`drivers/reset/sti/reset-syscfg.c:25-28`）里
`reset` 是复位位、`ack` 是可选的确认位。assert 路径写完之后会轮询 ack（`:62` 写、`:71` 回读），
`status` 也优先读 ack、没有 ack 才读 reset 位本身（`:116-127`）。

它的越界检查写在每个回调开头：`if (idx >= rcdev->nr_resets)`（`:57` 与 `:120`）。
`syscfg_reset_ops` 在 `:134-138`，注册用的是**非 devm** 版本（`:186`）。

⚠️ 归属提醒：`drivers/reset/sti/` 由 `CONFIG_ARCH_STI` 控制（见 `drivers/reset/Makefile`），
是 arm32 平台。**本节只作为"ack 回读"这个模式的唯一实例保留**，arm64 上不会加载。

### 2.3 `hisilicon/reset-hi3660`：自定义 xlate + 零边界检查

这是本篇最值得记住的一个反例，因为它把 §1.4 那个结论落到了具体硬件上。

`hi3660_reset_xlate()`（`drivers/reset/hisilicon/reset-hi3660.c:65-74`）只做打包：

```c
	offset = reset_spec->args[0];
	bit = reset_spec->args[1];

	return (offset << 8) | bit;
```

配套的 `hi3660_reset_program_hw()`（`:22-33`）再拆开：`offset = idx >> 8`、
`mask = BIT(idx & 0x1f)`，然后 `regmap_write(rc->map, offset, mask)` 断言、
写 `offset + 4` 解除（`:29-32`）。

四条观察：

1. **通篇没有 `nr_resets`**（0 次出现），也没有 `of_xlate` 之外的任何范围判断
   ⇒ DTS 里的 `args[0]` 会**原样变成 regmap 的寄存器偏移**。
2. `bit` 用 `& 0x1f` 掩到 5 位，但 `xlate` 里是 `| bit` 未掩 ⇒ 打包与解包不完全对称，
   `bit >= 32` 时会污染 offset 的低位。
3. 兜底只剩 regmap 自己：syscon 的 regmap 有 `max_register`，越界写会被 regmap 拒掉
   （返回负值，最终变成 `reset_control_assert()` 的返回码）。**但这是 regmap 的运气，
   不是复位框架的检查。**
4. probe 的另外两个细节：`syscon_regmap_lookup_by_phandle(np, "hisilicon,rst-syscon")`（`:86`），
   `-ENODEV` 时回退到已废弃的 `"hisi,rst-syscon"`（`:87-91`）；
   `of_reset_n_cells = 2` 和 `of_xlate` **成对设置**（`:99-100`）——
   这正是 `02-datastructures.md` §2.3 那条"两个字段一起填"的正面示范。
   注册用的是**非 devm** 的 `reset_controller_register()`（`:102`）。

同目录的 `hisilicon/hi6220_reset.c` 是另一种写法：**一个文件里三套 ops**
（`peripheral` `:68`、`media` `:91`、`ao` `:150`），按 compatible 选一套，
`nr_resets` 分别取 `MEDIA_MAX_INDEX` / `PERIPH_MAX_INDEX` / `AO_MAX_INDEX`（`:179-185`），
没有自定义 xlate ⇒ 走默认 xlate，越界由核心兜住。**同一个厂商目录下两个驱动、两种安全水位**，
这也是"家族只是套路相似、彼此并不调用"的最好例证。

---

## §3 固件家族 —— arm64 上真正的主流

### 3.0 这一族的共同形状

复位位不在内核能写的地址空间里，内核只能**发一条消息**给一个协处理器/固件
（SCP、DMSC、PMU、BPMP、VideoCore），由它去动寄存器。于是这一族的 `ops` 回调全都长成
"一行转发 + 返回固件的错误码"，而且必然可能睡眠。

`drivers/reset/` 下的成员：

| 驱动 | 通道 | ops 里有什么 | arm64 相关性 |
|---|---|---|---|
| `reset-scmi.c`（132 行） | ARM SCMI 协议 | assert / deassert / reset，**无 status** | 通用标准，最重要 |
| `reset-ti-sci.c`（261 行） | TI SCI（K3 系列 DMSC） | assert / deassert / status | arm64 DTS 里 `ti,sci-reset` 出现 7 处 |
| `reset-zynqmp.c` | Xilinx PM EEMI | reset / assert / deassert / status（四个全有） | `xlnx,zynqmp-reset` 1 处 |
| `tegra/reset-bpmp.c`（75 行） | Tegra BPMP MRQ | reset / assert / deassert，**无 status** | Tegra 平台 |
| `reset-raspberrypi.c` | RPi firmware property 接口 | **只有 `.reset`** 一个回调 | RPi4 专用 |

### 3.1 `reset-scmi`：最干净的一个，也是唯一不是 platform_driver 的

三个回调各只有一行有效代码，`assert` 为例（`drivers/reset/reset-scmi.c:39-45`）——
它转发给一个**文件级静态指针** `reset_ops`（`:14`），私有数据只有
`rcdev` + 协议句柄 `ph` 两个字段（`struct scmi_reset_data`，`:21-24`）。

三个必须记住的点：

1. **`scmi_reset_ops`（`drivers/reset/reset-scmi.c:83-87`）没有 `.status`。**
   SCMI 的复位域协议不提供"读回当前复位状态"，所以 `reset_control_status()`
   在 SCMI 平台上一定失败。这条在 `07-debug.md` §5 的分诊表里是高频问题。
2. **`nr_resets` 是问固件要的**：`reset_ops->num_domains_get(ph)`
   （`drivers/reset/reset-scmi.c:111`）。不是 DTS 写的，也不是驱动写死的——
   所以 DTS 里复位 id 写超了，报错信息来自核心的默认 xlate，而上界由固件决定。
3. **它是 `scmi_driver`，不是 `platform_driver`**：末行
   `module_scmi_driver(scmi_reset_driver)`（`drivers/reset/reset-scmi.c:128`），
   匹配用 `scmi_id_table`（`:117-120`）。probe 拿的是 `struct scmi_device *sdev`，
   通过 `devm_protocol_get(sdev, SCMI_PROTOCOL_RESET, &ph)`（`:100`）握协议；
   `handle` 为空直接 `-ENODEV`（`:97-98`）。填 rcdev 的四行在 `:108-111`，
   注册在 `:114`。**这是本篇 §4 那一节存在的理由。**

### 3.2 `reset-ti-sci`：唯一"读-改-写"要跨固件两次的

TI SCI 的复位是"整个设备一个 32 位复位寄存器"，所以要改其中一位必须
**先向固件读回当前值、改位、再写回**（`drivers/reset/reset-ti-sci.c` 的 assert 注释在 `:56` 段落写明了这一点）。
私有结构 `struct ti_sci_reset_data`（`:36-44`）里带 `idr`（`#include <linux/idr.h>` 在 `:9`），
因为一个控制器要管一批"设备 id"。

`ti_sci_reset_ops`（`:164-168`）三个回调齐全（assert/deassert/status），
它**自带 `of_xlate`**（`:171` 起）。注册用的是**非 devm** 的 `reset_controller_register()`（`:235`），
所以它是 `drivers/reset/` 下少数写了 `.remove` 的驱动之一：
`reset_controller_unregister()`（`:242`）——正好落在 `03-internals.md` §A7 讨论的那条路径上。
compatible 只有一个 `"ti,sci-reset"`（`:204-208`），`module_platform_driver` 在 `:257`。

### 3.3 另外三个的一句话要点

- **`reset-zynqmp.c`**：`zynqmp_reset_ops`（`:91-96`）**四个回调全有**，
  是本篇唯一一个"assert/deassert/reset/status 全实现"的固件驱动；三个动作都落在
  同一个 `zynqmp_pm_reset_assert()` 上，只是第二个参数不同（`:39`/`:48`/`:71`），
  status 走 `zynqmp_pm_reset_get_status()`（`:59`）。它的注册时机很特殊，见 §4.2。
- **`tegra/reset-bpmp.c`**（75 行，全目录最小）：三个回调都拼一条
  `MRQ_RESET` 消息（`:27`/`:30`），`tegra_bpmp_reset_ops` 在 `:61-65`，
  **没有 `.status`**；`nr_resets = bpmp->soc->num_resets`（`:72`）来自 SoC 数据表；
  注册在 `:74`。它没有自己的 `platform_driver`——rcdev 内嵌在 BPMP 设备里，
  由 BPMP 驱动在自己 probe 的时候调这一个函数，属于 §4.1 的情形。
- **`reset-raspberrypi.c`**：`rpi_reset_ops`（`:68-71`）**只有 `.reset`**，
  连 assert/deassert 都没有——因为固件提供的语义就是"给 VL805 打一发复位并重新加载固件"
  （`:37-52` 那段注释解释了 EEPROM/固件两种加载来源）。
  `nr_resets = RASPBERRYPI_FIRMWARE_RESET_NUM_IDS`（`:98`），来自
  `include/dt-bindings/reset/raspberrypi,firmware-reset.h`。这是"一个控制器只支持一个动作"
  的极端例子：`reset_control_assert()` 在这块板子上永远失败。

---

## §4 挂载点：这些控制器是怎么被 probe 出来的

本节不是新家族，是把前三节的驱动**换一个维度**重新切一刀：
它们各自挂在设备模型的什么位置、什么时候 probe。这个维度直接决定消费者会不会吃到
`-EPROBE_DEFER`（那条路径见 `03-internals.md` §A2）。

| 挂载方式 | 例子 | probe 时机 | 对消费者的影响 |
|---|---|---|---|
| 普通 `platform_driver`（模块） | 18 个文件用 `module_platform_driver` | 正常 probe 次序 | 最常见，消费者要老实处理 `-EPROBE_DEFER` |
| 普通 `platform_driver`（内建） | 14 个文件用 `builtin_platform_driver`，如 `reset-simple.c:205` | 同上，但**不能是模块** | 少一种"模块没装"的故障模式 |
| `scmi_driver` | `reset-scmi.c:128` | SCMI 总线枚举出复位协议设备之后 | 依赖 SCMI 传输就绪，defer 窗口更长 |
| MFD 子设备 | `reset-a10sr.c:124` | 父 MFD（`include <linux/mfd/altera-a10sr.h>`，`:11`）先 probe | 它**仍然是** `platform_driver`——MFD 子设备就是 platform 设备 |
| 内嵌在别的驱动里，没有自己的 driver | `tegra/reset-bpmp.c:74`；`drivers/clk/` 下 33 个文件 | 宿主驱动 probe 的中途 | 复位控制器和宿主"同生共死" |
| 早期 initcall | 3 个文件（见 4.2） | **在设备 probe 之前** | 消费者基本不会 defer |

### 4.1 "没有自己的 driver"是常态，不是例外

`tegra/reset-bpmp.c` 全文 75 行，**没有 `platform_driver`、没有 `of_device_id`、没有 probe**。
它只导出一个 `tegra_bpmp_init_resets()`，由 BPMP 驱动在自己 probe 里调用，
里面填三行 rcdev（`:69`、`:72`）然后 `devm_reset_controller_register(bpmp->dev, &bpmp->rstc)`（`:74`）。

这种写法之所以合法，是因为 `reset_controller_register()` **只认一个 `rcdev`，不认设备**
（`02-datastructures.md` §1.2 已经论证过 rcdev 不是 device）。任何驱动只要手上有个
`device *` 和一段能改硬件的通道，就能顺手当复位控制器——§6 会看到这条路被用了 47 次。

### 4.2 三个早期 initcall：为了在 probe 之前就能复位

`drivers/reset/` 下有 3 个文件不用 `*_platform_driver` 宏，而是自己写 init 函数挂到早期 initcall：

| 文件 | initcall 级别 | 行 |
|---|---|---|
| `drivers/reset/reset-intel-gw.c` | `postcore_initcall` | `:262` |
| `drivers/reset/reset-microchip-sparx5.c` | `postcore_initcall` | `:178` |
| `drivers/reset/reset-zynqmp.c` | `arch_initcall` | `:141`（里面调 `platform_driver_register`，`:138`） |

sparx5 那个文件在 `:175` 附近留了注释说明理由：**它执行的是一次全局复位**，
必须早到所有外设驱动之前。`reset-zynqmp.c` 用的是更早的 `arch_initcall`。

这一列是"为什么消费者侧的 `-EPROBE_DEFER` 在某些平台上从来不出现"的答案：
控制器在 `postcore`/`arch` 阶段就已经注册进 `reset_controller_list`，
等第一个消费者 probe 时它早就在了。

---

## §5 三个子目录为什么单独放

`drivers/reset/` 下有三个子目录，各自带一份 `Kconfig` + `Makefile`：

| 子目录 | 内容 | 总行数 | 进不进编译 |
|---|---|---|---|
| `hisilicon/` | `hi6220_reset.c`、`reset-hi3660.c` | 350 | **`obj-y` 无条件进入**（`drivers/reset/Makefile`），由子目录内各自的 CONFIG 决定具体编哪个 |
| `sti/` | `reset-stih407.c`、`reset-syscfg.c`、`reset-syscfg.h` | 365 | `obj-$(CONFIG_ARCH_STI)`，arm32 |
| `tegra/` | `reset-bpmp.c` | 75 | `obj-$(CONFIG_ARCH_TEGRA)` |

三份子 `Kconfig` 由 `drivers/reset/Kconfig` 末尾的三行 `source` 拉进来。

**为什么这三家单独开目录，而 amlogic / rockchip / imx 就直接放在上层？**
从代码本身能读出来的只有一条客观差别：**这三个目录都不是"一个 SoC 一个文件"的形状**——
`sti/` 有一个共享实现 `reset-syscfg.c` + 一个 SoC 数据文件 `reset-stih407.c`
（`.h` 是二者之间的接口），`hisilicon/` 是两个代差很大的 SoC 各一套 ops，
`tegra/` 则是一个"给别人调的库"而不是驱动（见 §4.1）。
一旦一个厂商需要**多文件 + 文件间的私有头**，单独开目录就成了唯一干净的做法。

⚠️ 这一段是**推断，不是代码里的自陈**——`drivers/reset/` 下没有任何注释解释目录划分。
按规则登记进 `99-evidence.md` §5.3。

---

## §6 同一个硬件既是时钟提供方又是复位提供方

### 6.1 一条 grep 就能看到的事实

复位控制器的实现方**大多数不住在 `drivers/reset/`**。全树扫一遍
`devm_reset_controller_register` / `reset_controller_register(`：

| 位置 | 文件数 |
|---|---|
| 全树合计 | **90** |
| `drivers/reset/`（含三个子目录） | 43 |
| **`drivers/clk/`** | **33** |
| `drivers/soc/` | 5 |
| `drivers/{watchdog,usb,ufs,power,phy,memory,firmware}/` | 各 1，合计 7 |
| `arch/arm/`、`arch/mips/` | 各 1（**`arch/arm64/` 下 0 个** —— arm64 上没有任何架构代码自己当复位控制器） |

命令记在 `99-evidence.md` §3.4。**33 : 43** 这个比例是本篇最该记住的数字：
你要找某条复位线是谁实现的，去 `drivers/clk/` 找的命中率和去 `drivers/reset/` 差不多。

### 6.2 为什么是时钟驱动

硬件上，SoC 的 **CRU / CCU / PRCI / clock-reset unit** 就是一个寄存器块：
低地址一片是时钟门控和分频，高地址一片是复位位。DT 上它也只有一个节点、一个
`platform_driver`。于是这个驱动在 probe 里注册两次：

```
             一个 platform_driver 的 probe
                        │
        ┌───────────────┴───────────────┐
        ▼                               ▼
 devm_clk_hw_register 系列        devm_reset_controller_register
        │                               │
        ▼                               ▼
   clk 框架的链表              reset_controller_list
   （of_clk_providers）        （drivers/reset/core.c:20-24）
```

两个框架**互不知道对方存在**：没有任何数据结构把这两次注册关联起来，
一个消费者 `clk_get()` 和 `reset_control_get()` 走的是两条完全独立的 phandle 解析。
DTS 上则表现为同一个 phandle 出现在两个属性里（`clocks = <&cru N>` 和 `resets = <&cru M>`），
`06-devicetree.md` §7 有真实节点。

具体到 `reset-simple` 这一族，§1.3 那张表里的
`drivers/clk/bcm/clk-bcm2711-dvp.c` 和 `drivers/clk/sifive/sifive-prci.h`
就是"时钟驱动直接借用复位框架的通用 ops"的两个实例：
它们连自己的 `reset_control_ops` 都不写，内嵌一个 `reset_simple_data` 就完事。

反过来的方向不存在：**复位框架没有任何一处引用 clk 框架**。
`drivers/reset/core.c` 里没有 `clk_` 调用，两者的耦合完全发生在驱动这一层。

---

## §7 汇总表（家族 × 职责 × 挂载点 × arm64 是否相关）

| 家族 | 代表文件 | 怎么改硬件 | 挂在哪 | 回调完整度 | arm64 |
|---|---|---|---|---|---|
| `reset-simple` 通用位操作 | `reset-simple.c` | `readl`/`writel` + 自旋锁 | `builtin_platform_driver` | 四个全有 | |
| `reset-simple` 复用者 | `reset-socfpga.c`、`reset-sunxi.c`、`reset-uniphier-glue.c` | 同上（借 `reset_simple_ops`） | 各自的 platform_driver | 同上 | |
| syscon / regmap（DTS 描述布局） | `reset-ti-syscon.c` | `regmap_write_bits` | `module_platform_driver` | 无 `.reset` | |
| syscon / regmap（自定义 xlate） | `hisilicon/reset-hi3660.c` | `regmap_write` | platform_driver（早期 init 注册） | 无 `.status` | |
| syscon / regmap（ack 回读） | `sti/reset-syscfg.c` | `regmap_field_*` + 轮询 ack | platform_driver | 三个 | ✗（arm32） |
| 固件 · SCMI | `reset-scmi.c` | SCMI 消息 | **`scmi_driver`** | 无 `.status` | |
| 固件 · TI SCI | `reset-ti-sci.c` | TI SCI 读-改-写 | `module_platform_driver` | 无 `.reset` | |
| 固件 · Xilinx EEMI | `reset-zynqmp.c` | `zynqmp_pm_reset_*` | `arch_initcall` | **四个全有** | |
| 固件 · Tegra BPMP | `tegra/reset-bpmp.c` | `MRQ_RESET` 消息 | **无自己的 driver** | 无 `.status` | |
| 固件 · RPi | `reset-raspberrypi.c` | firmware property | `module_platform_driver` | **只有 `.reset`** | |
| 时钟驱动兼任 | `drivers/clk/` 下 33 个文件 | 各自的 regmap/MMIO | 宿主 clk driver 的 probe | 各异 | |

**读这张表的方式**：最右两列合起来就是"你在 arm64 上会遇到的坑"。
`回调完整度` 一列的每一个"无 X"都意味着对应的 `reset_control_X()`
在那块硬件上必然失败——而这个失败**不是 bug**，是框架允许的
（`Documentation/driver-api/reset.rst:161` 明确写了回调全都是可选的）。

---

## §8 接下来看哪一篇

| 你接着想做的事 | 去处 |
|---|---|
| 我要自己写一个控制器驱动 | `05-usage.md` §4 |
| 我要在消费者驱动里用复位 | `05-usage.md` §2 |
| 这些 compatible 的 DTS 怎么写、`#reset-cells` 填几 | `06-devicetree.md` |
| `reset_control_status()` 返回 `-ENOTSUPP`/`-EINVAL` 怎么查 | `07-debug.md` §5 |
| 这些回调被核心怎么调用的 | `03-internals.md` §A4 |
| 为什么"控制器"不是一个 `struct device` | `02-datastructures.md` §1.2 |
