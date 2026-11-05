---
title: 扩展机制
---

# clk 子框架剖析

> 基线：Linux 6.1.158（`linux-6.1.y`），架构 arm64。
> 前置阅读：`02-datastructures.md`（`clk_hw` / `clk_ops` 的含义）、`03-internals.md`（注册与调速主流程）。

## 本篇的组织轴：一节一个独立子框架，互不调用

`03-internals.md` 的主轴是**时间**（一次注册、一次调速从头走到尾）。本篇的主轴是**并列**：

> **每一节讲一个可以独立存在的子框架。节与节之间没有调用关系，可以任意跳读。**

判据很硬：把任意一节里的文件从树里删掉，其他节讲的东西照样能编译、能工作（`clk-composite.c` 是唯一的例外，它显式依赖别的类型提供 ops 表，见 §9）。这条轴决定了两件事：

- **不要在本篇找"先后顺序"**。八种基本时钟类型之间没有先后，`clk-bulk.c` 和 `clk-conf.c` 之间也没有。要顺序请回 `03-internals.md` §0 的生命周期时间线
- **每节都会交代"它挂在 03 时间线的哪一点上"**。这是本篇和 03 唯一的接缝

### §0.1 分诊表：arm64 上你真会碰到哪几个

内核树里挂在 clk 框架上的"子框架"比多数人想的多。但**在 arm64 平台上，绝大多数厂商时钟驱动根本不用基本类型的 ops 表**——这条结论在 §0.2 有数据支撑。先看分诊：

| # | 子框架 | 文件 | 行数 | arm64 相关度 | 你什么时候会碰到 |
|---|---|---|---|---|---|
| §2 | gate（门控） | `drivers/clk/clk-gate.c` | 259 | | 写外设时钟树、读厂商驱动 |
| §3 | divider（分频器） | `drivers/clk/clk-divider.c` | 689 | | 同上；**helper 函数被复用得比 ops 表广得多** |
| §4 | mux（多路选择） | `drivers/clk/clk-mux.c` | 285 | | 同上 |
| §5 | fixed-rate（定频） | `drivers/clk/clk-fixed-rate.c` | 240 | | **每块 arm64 板子的 DTS 里都有**（晶振） |
| §6 | fixed-factor（定比） | `drivers/clk/clk-fixed-factor.c` | 342 | | DTS 里的 `fixed-factor-clock` 节点 |
| §7 | fractional-divider（小数分频） | `drivers/clk/clk-fractional-divider.c` | 259 | ☆☆ | 只有 rockchip / imx 两家在用（§7.3） |
| §8 | multiplier（倍频） | `drivers/clk/clk-multiplier.c` | 156 | ☆☆☆ | **只有 sunxi（arm32）两个文件在用，且无注册函数**（§8.3） |
| §9 | composite（复合） | `drivers/clk/clk-composite.c` | 489 | ☆ | mediatek / imx / rockchip 组装 mux+div+gate 时 |
| §10 | clkdev（非 DT 查找表） | `drivers/clk/clkdev.c` | 381 | ☆☆ | arm64 上是 `clk_get` 的**兜底**分支，正常走 DT |
| §11 | clk-bulk（批量） | `drivers/clk/clk-bulk.c` | 248 | | 消费者驱动一次拿多路时钟 |
| §12 | clk-devres（devm 生命周期） | `drivers/clk/clk-devres.c` | 225 | | 消费者驱动几乎只用这一套 |
| §13 | clk-conf（`assigned-clocks`） | `drivers/clk/clk-conf.c` | 152 | | DTS 里预设初始父/频率 |
| §14 | 跨子系统提供方（GPIO / PWM 当时钟） | `clk-gpio.c` 249 / `clk-pwm.c` 154 | — | ☆☆ | 板级黑科技，少见但会遇到 |
| §15 | 固件代理时钟（SCMI / SCPI） | `clk-scmi.c` 267 / `clk-scpi.c` | — | | **arm64 服务器/新 SoC 的主流形态** |
| §16 | KUnit 测试 | `clk_test.c` 2411 / `clk-gate_test.c` 464 | — | ☆ | 改核心逻辑时的回归网 |

三个必须先说清的边界：

- **§15 的 SCMI 是 arm64 特有的重点，不是边角**。它是唯一一个"时钟寄存器不在本 CPU 地址空间里"的形态，`clk_ops` 的选择会随传输层能不能原子化而变（§15.1）——**按"运行期探测到的传输层能力"挑 ops 表，全树只有它**（同目录的 clk-scpi 也有两张表，但它是按 DT `compatible` 挑的，属于普通的 `of_match` 数据分派，见 §15.2 的对比）
- **§8 multiplier 在 arm64 上等于不存在**。它连注册函数都没有（`grep -rn 'clk_hw_register_multiplier' .` 全树空），只能由驱动自己 `kzalloc` + 填 `init.ops = &clk_multiplier_ops`。全树两个使用者都在 `drivers/clk/sunxi/`（arm32 的 sunxi 老驱动），arm64 的 sunxi 用 `sunxi-ng/` 那一套自己的 CCU 抽象
- **debugfs 不在本篇**。它不是子框架而是核心自带的观测面，见 `07-debug.md`

### §0.2 一条推翻直觉的统计：arm64 大厂根本不用基本类型的 ops 表

写文档时最容易顺手写下的一句话是"厂商时钟驱动用 `clk_gate_ops` / `clk_divider_ops` / `clk_mux_ops` 搭出时钟树"。这句话在 arm32 时代大致成立，在 arm64 上**大面积不成立**。逐目录数"哪些文件里出现了这三个 ops 表"：

| 厂商目录 | `clk_gate_ops` | `clk_divider_ops` | `clk_mux_ops` | 实际做法 |
|---|---|---|---|---|
| `drivers/clk/qcom/` | 0 文件 | 0 文件 | 0 文件 | 自造 `clk_regmap` 分层 + `clk_branch2_ops` / `clk_rcg2_ops` / 十几种 `clk_alpha_pll_*_ops` |
| `drivers/clk/sunxi-ng/` | 0 | 0 | 0 | 自造 CCU 抽象（`ccu_gate` / `ccu_div` / `ccu_mux`） |
| `drivers/clk/tegra/` | 0 | 0 | 0 | 自造 `tegra_clk_periph` 等 |
| `drivers/clk/imx/` | 3 | 5 | 5 | 混用：基本类型 + 自造 `imx_clk_*` 包装 |
| `drivers/clk/rockchip/` | 2 | 1 | 3 | 混用 |
| `drivers/clk/renesas/` | 2 | 2 | 0 | 混用 |
| `drivers/clk/mediatek/` | 1 | 1 | 2 | 混用 |
| `drivers/clk/samsung/` | 1 | 0 | 1 | 混用 |

重跑命令（本表用的就是它）：

```bash
for d in qcom sunxi-ng mediatek imx rockchip renesas samsung tegra bcm; do
  for o in clk_gate_ops clk_divider_ops clk_mux_ops; do
    printf '%s %s -> ' "$d" "$o"
    grep -rl --include='*.c' -w "$o" drivers/clk/$d 2>/dev/null | wc -l
  done
done
```

**那么基本类型到底被谁用？答案是「算法 helper 函数」而不是「ops 表」。** 厂商自造 ops 表，但表里的回调实现会去调基本类型导出的算法函数。这是本篇最重要的一条认知——**复用发生在函数级，不是在类型级**：

| 导出的算法 helper | 声明处 | 树内使用者（排除 `drivers/clk/clk*.c` 自身） |
|---|---|---|
| `divider_recalc_rate` | `include/linux/clk-provider.h` | 18 行 / 15 文件 |
| `divider_get_val` | 同上 | 16 行 / 16 文件 |
| `clk_mux_val_to_index` | 同上 | 10 行 / 9 文件 |
| `clk_mux_index_to_val` | 同上 | 9 行 / 9 文件 |
| `clk_mux_determine_rate_flags` | 同上（实现在 `drivers/clk/clk.c`） | 5 行 / 5 文件 |
| `divider_round_rate_parent` | 同上 | 3 行 / 2 文件 |
| `clk_gate_is_enabled` | 同上 | 2 行 / 2 文件 |
| `clk_fractional_divider_general_approximation` | 同上 | 1 行 / 1 文件 |
| `divider_determine_rate` | 同上 | 1 行 / 1 文件 |
| `divider_ro_determine_rate` | 同上 | **0** —— 树内除定义处无使用者 |
| `divider_ro_round_rate_parent` | 同上 | **0** —— 同上 |

重跑命令：

```bash
for f in divider_recalc_rate divider_get_val clk_mux_val_to_index clk_mux_index_to_val \
         clk_mux_determine_rate_flags divider_round_rate_parent clk_gate_is_enabled \
         clk_fractional_divider_general_approximation divider_determine_rate \
         divider_ro_determine_rate divider_ro_round_rate_parent; do
  printf '%s -> 行 %s / 文件 %s\n' "$f" \
    "$(grep -rn --include='*.c' -w "$f" . | grep -vc '^./drivers/clk/clk-\|^./drivers/clk/clk\.c')" \
    "$(grep -rln --include='*.c' -w "$f" . | grep -vc '^./drivers/clk/clk-\|^./drivers/clk/clk\.c')"
done
```

对读者的实用结论：**看厂商时钟驱动时，不要去找 `clk_gate_ops`，要去找 `divider_recalc_rate` 和 `clk_mux_val_to_index` 这类 helper——那才是基本类型真正的复用面。**

---

## §1 八种基本时钟类型：一张总览

八种类型住在八个 `drivers/clk/clk-<type>.c` 文件里，形状高度一致，都是同一个三件套：

```
  struct clk_<type> {            ← 硬件描述（寄存器地址、位偏移、类型专属 flags）
        struct clk_hw   hw;      ← 必须是成员，用来做 container_of
        void __iomem   *reg;
        u8              ...;
        u8              flags;   ← 类型专属 flags，和 clk_hw->init->flags 不是一回事
        spinlock_t     *lock;    ← 可选，多数类型有
  };
  #define to_clk_<type>(_hw)  container_of(_hw, struct clk_<type>, hw)
  const struct clk_ops clk_<type>_ops = { ... };   ← 只填这个类型真正需要的回调
  struct clk_hw *clk_hw_register_<type>(...);      ← kzalloc + 填结构 + clk_hw_register
```

一张表把八种类型的 ops 表内容摊开——**这张表就是"这个类型能被 clk 核心怎么用"的完整答案**（回调语义见 `02-datastructures.md` §4）：

| 类型 | ops 表符号 | 表里有哪些回调 | 声明处（`include/linux/clk-provider.h`） |
|---|---|---|---|
| gate | `clk_gate_ops` | `enable` / `disable` / `is_enabled` | `:524` |
| divider | `clk_divider_ops` | `recalc_rate` / `round_rate` / `determine_rate` / `set_rate` | `:680` |
| divider（只读） | `clk_divider_ro_ops` | `recalc_rate` / `round_rate` / `determine_rate` | `:681` |
| mux | `clk_mux_ops` | `get_parent` / `set_parent` / `determine_rate` | `:965` |
| mux（只读） | `clk_mux_ro_ops` | **只有 `get_parent`** | `:966` |
| fixed-rate | `clk_fixed_rate_ops` | `recalc_rate` / `recalc_accuracy` | `:359` |
| fixed-factor | `clk_fixed_factor_ops` | `round_rate` / `set_rate` / `recalc_rate` | `:1080` |
| fractional-divider | `clk_fractional_divider_ops` | `recalc_rate` / `round_rate` / `set_rate` | **不在这个头文件里**，见 `drivers/clk/clk-fractional-divider.h:7`（结构体 `:1132`） |
| multiplier | `clk_multiplier_ops` | `recalc_rate` / `round_rate` / `set_rate` | `:1203`（结构体 `:1188`） |
| composite | 无固定符号 | **运行期拼装**，见 §9 | 结构体 `:1216` |

三条从这张表直接读出来、但很少有人明说的事实：

1. **`clk_gate_ops` 里没有 `prepare`/`unprepare`**。所以 gate 类型是纯原子上下文的——`clk_enable()` 拿 `enable_lock`（自旋锁）就能开门。这不是"gate 恰好不需要睡眠"，而是"内存映射寄存器写一下就完事"。反例在 §14.1：GPIO 当门控时必须换成 `prepare`/`unprepare`，因为 GPIO 可能挂在 I2C 扩展器上
2. **`clk_mux_ro_ops` 只有一个回调，连 `determine_rate` 都没有**。后果直接、且容易踩：`clk_core_can_round()` 的判据是 `core->ops->determine_rate || core->ops->round_rate`（`drivers/clk/clk.c:1623-1626`），只读 mux 两个都没有 → 它**不能参与频率协商**。对它调 `clk_round_rate` 只会拿到当前父频率，不会去问父亲能给什么
3. **`clk_divider_ro_ops` 有 `round_rate` 和 `determine_rate` 却没有 `set_rate`**。这不是漏写：它让只读分频器**仍然能参与协商**（算出"以我现在这个固定分频比，父亲给 X 我就输出 X/N"），只是最后落不下去。结合 `03-internals.md` §A7.1 的静默返回 0 规则读——`clk_set_rate` 在这类节点上不报错、不生效

### §1.1 挂在 03 时间线的哪一点

八种基本类型全部挂在 **`03-internals.md` §A2 的第 2 步之前**：驱动 `kzalloc` 出 `struct clk_<type>`、填好寄存器地址和 flags、把 `&clk_<type>_ops` 塞进 `init.ops`，然后调 `clk_hw_register()` 进入核心。**类型的全部作用就是"提供一张 ops 表和一段硬件描述"**，此后所有流程都是核心的，跟类型无关。

唯一的例外是 §9 的 composite：它在 `clk_hw_register()` **之前**多做一步——把别人的 ops 表拆开、按有没有对应回调逐字段拼出一张新表。

---

## §2 gate：门控

**文件**：`drivers/clk/clk-gate.c`（259 行）。**结构体**：`include/linux/clk-provider.h:510-517`，`to_clk_gate` 在 `:518`。

### §2.1 三个 flags

| flag | 值 | 语义 | 注释处 |
|---|---|---|---|
| `CLK_GATE_SET_TO_DISABLE` | `BIT(0)`（`:520`） | 默认"置位=开"；设了这个则"置位=关" | `:499-501` |
| `CLK_GATE_HIWORD_MASK` | `BIT(1)`（`:521`） | 低 16 位是数据、高 16 位是写使能掩码 | `:502-505` |
| `CLK_GATE_BIG_ENDIAN` | `BIT(2)`（`:522`） | 用大端寄存器访问 | `:506-508` |

### §2.2 一个异或搞定极性：`clk_gate_endisable`

`drivers/clk/clk-gate.c:56-88`。整个 gate 类型的算法就这一个函数，`enable` 和 `disable` 只是传 1 和 0（`:91-101`）：

```c
static void clk_gate_endisable(struct clk_hw *hw, int enable)
{
        struct clk_gate *gate = to_clk_gate(hw);
        int set = gate->flags & CLK_GATE_SET_TO_DISABLE ? 1 : 0;
        ...
        set ^= enable;                          /* ← 极性折叠成一次异或 */
```

真值表（`set` 的最终含义 = "这一位要不要写 1"）：

| `CLK_GATE_SET_TO_DISABLE` | 调用 | `set` 初值 | `enable` | `set ^= enable` | 写入位 |
|---|---|---|---|---|---|
| 未设（置位=开） | enable | 0 | 1 | 1 | 1 |
| 未设 | disable | 0 | 0 | 0 | 0 |
| 已设（置位=关） | enable | 1 | 1 | 0 | 0 |
| 已设 | disable | 1 | 0 | 1 | 1 |

**`CLK_GATE_HIWORD_MASK` 分支根本不读寄存器**（`:70-76`）：

```c
        if (gate->flags & CLK_GATE_HIWORD_MASK) {
                reg = BIT(gate->bit_idx + 16);   /* 只写使能掩码 */
                if (set)
                        reg |= BIT(gate->bit_idx);
        } else {
                reg = clk_gate_readl(gate);      /* ← 只有这条分支是读-改-写 */
```

这条差异对读者有实际后果：**hiword-mask 硬件上，一次写入不会扰动同寄存器里的其他位，所以 `gate->lock` 在语义上是多余的**（读-改-写才需要互斥）；但代码仍然照样加锁（`:66-69` / `:84-87`），因为锁是共享给同寄存器上其他类型（分频器、mux）用的。

### §2.3 `gate->lock` 可以是 NULL

`:66-69` 和 `:84-87` 那对 `if (gate->lock)` / `else __acquire(gate->lock)`：`__acquire`/`__release` 是 sparse 的静态分析注解，编译出来是空。**允许不传锁**——单独占一个寄存器的门控不需要。

### §2.4 `clk_gate_is_enabled` 是唯一被导出的算法

`drivers/clk/clk-gate.c:103-118`（非 `static`，`EXPORT_SYMBOL_GPL` 在 `:119`）。它先按 `CLK_GATE_SET_TO_DISABLE` 异或翻转再掩位（`:112-115`），所以厂商驱动可以在自己的 ops 表里直接复用这一个回调。全树使用者 2 行 / 2 文件（§0.2）。

### §2.5 注册函数族

`__clk_hw_register_gate` 在 `:127`，`__devm_clk_hw_register_gate` 在 `:232`（release 回调 `:227`），拆除是 `clk_hw_unregister_gate`（`:216`）/ 已废弃的 `clk_unregister_gate`（`:200`）。头文件里有一圈把这两个 `__` 函数包起来的宏（`clk_hw_register_gate` / `clk_hw_register_gate_parent_hw` / …），宏的位置对阅读无价值，直接在 `include/linux/clk-provider.h` 里搜 `register_gate` 即可。

全树使用统计：`clk_hw_register_gate` 29 行 / 13 文件；`devm_clk_hw_register_gate` 3 行 / 1 文件（排除 `drivers/clk/clk-*.c` 自身）。

---

## §3 divider：分频器

**文件**：`drivers/clk/clk-divider.c`（**689 行，八种类型里最大的一个**）。**结构体**：`include/linux/clk-provider.h:658-667`，`to_clk_divider` 在 `:669`；分频比表 `struct clk_div_table` 在 `:613`。

### §3.1 八个 flags——两组语义完全不同的东西

| flag | 值 | 语义 | 注释处 |
|---|---|---|---|
| `CLK_DIVIDER_ONE_BASED` | `BIT(0)`（`:671`） | 寄存器值**就是**分频比（默认是值+1） | `:632-635` |
| `CLK_DIVIDER_POWER_OF_TWO` | `BIT(1)`（`:672`） | 分频比 = 2^寄存器值 | `:636-637` |
| `CLK_DIVIDER_ALLOW_ZERO` | `BIT(2)`（`:673`） | 允许分频比为 0（配合 ONE_BASED） | `:638-642` |
| `CLK_DIVIDER_HIWORD_MASK` | `BIT(3)`（`:674`） | 高 16 位写使能掩码 | `:643-646` |
| `CLK_DIVIDER_ROUND_CLOSEST` | `BIT(4)`（`:675`） | 取最接近的分频比（默认向上取整=不超过目标） | `:647-648` |
| `CLK_DIVIDER_READ_ONLY` | `BIT(5)`（`:676`） | 预配置好，不要改 | `:649-650` |
| `CLK_DIVIDER_MAX_AT_ZERO` | `BIT(6)`（`:677`） | 寄存器 0 表示最大分频比 | `:651-653` |
| `CLK_DIVIDER_BIG_ENDIAN` | `BIT(7)`（`:678`） | 大端访问 | `:654-656` |

前三个和 `MAX_AT_ZERO` 回答的是**"寄存器值怎么变成分频比"**，`ROUND_CLOSEST` 回答的是**"目标频率怎么变成寄存器值"**，`HIWORD_MASK`/`BIG_ENDIAN` 回答的是**"怎么访问寄存器"**，`READ_ONLY` 是**声明意图**。把它们当成一组同类东西看，就会在读厂商驱动时反复卡住。

**`CLK_DIVIDER_READ_ONLY` 这个 flag 本身不阻止写入**——它只是给驱动作者的一个标记，真正的只读靠**换 ops 表**（用 `clk_divider_ro_ops` 而不是 `clk_divider_ops`，见 §1 那张表：前者没有 `set_rate`）。

### §3.2 两条并行的"值 ↔ 分频比"换算路径

689 行里有一半是这组小函数，全部 `static`，成对出现：

| 方向 | 有 `table` 时 | 无 `table`（按 width + flags） |
|---|---|---|
| 寄存器值 → 分频比 | `_get_table_div`（`:80`） | `_get_div`（`:91`） |
| 分频比 → 寄存器值 | `_get_table_val`（`:105`） | `_get_val`（`:116`） |
| 最大分频比 | `_get_table_maxdiv`（`:45`） | `_get_maxdiv`（`:68`） |
| 最小分频比 | `_get_table_mindiv`（`:57`） | —（无表时最小恒为 1） |
| 合法性 | `_is_valid_table_div`（`:162`） | `_is_valid_div`（`:173`） |

**读驱动时第一件要确认的事就是"这个分频器有没有 `table`"**——有表则 flags 里的 ONE_BASED / POWER_OF_TWO / MAX_AT_ZERO 全部不参与换算。

### §3.3 取整策略：三个 `_div_round_*`

`_round_up_table`（`:183`）/ `_round_down_table`（`:201`）/ `_div_round_up`（`:219`）/ `_div_round_closest`（`:233`），由 `_div_round`（`:257`）按 `CLK_DIVIDER_ROUND_CLOSEST` 二选一分派。搜索主循环是 `clk_divider_bestdiv`（`:289`，57 行），配套 `_is_best_div`（`:267`）和 `_next_div`（`:276`）。

**默认策略是"向上取整"，即宁可输出频率低于目标也不超过**。这对时钟树是正确的保守方向（超频可能烧硬件），但它意味着 `clk_set_rate(clk, 100000000)` 后 `clk_get_rate` 返回 96 MHz 是**正常行为不是 bug**。要"最接近"必须显式加 `CLK_DIVIDER_ROUND_CLOSEST`。

### §3.4 六个导出的算法 helper——本类型真正的复用面

这六个是**非 `static`、`EXPORT_SYMBOL_GPL`、头文件有声明**的，所以厂商驱动可以直接拿去填自己的 ops 表：

| helper | 定义处（`drivers/clk/clk-divider.c`） | 干什么 |
|---|---|---|
| `divider_recalc_rate` | `:130` | 寄存器值 + parent_rate → 输出频率 |
| `divider_determine_rate` | `:346` | 可写分频器的 `determine_rate` 实现 |
| `divider_ro_determine_rate` | `:361` | 只读分频器的 `determine_rate` 实现 |
| `divider_round_rate_parent` | `:384` | 允许同时协商父频率的 `round_rate` |
| `divider_ro_round_rate_parent` | `:406` | 只读版本 |
| `divider_get_val` | `:470` | 目标频率 + parent_rate → 该写的寄存器值 |

对照 §0.2 的统计：`divider_recalc_rate`（15 文件）和 `divider_get_val`（16 文件）是全套基本类型里复用最广的两个符号，而 `divider_ro_determine_rate` 和 `divider_ro_round_rate_parent` **树内除定义处一个使用者都没有**。这两个是"为对称而存在"的 API。

### §3.5 ops 表里的四个回调

`clk_divider_ops`（`:522-527`）与 `clk_divider_ro_ops`（`:530-534`）的差别只有 `set_rate` 一项。四个回调的实现：`clk_divider_recalc_rate`（`:149`）、`clk_divider_round_rate`（`:428`）、`clk_divider_determine_rate`（`:449`）、`clk_divider_set_rate`（`:487`）。

**同时提供 `round_rate` 和 `determine_rate` 是有意的冗余**：`03-internals.md` §A7.1 说过核心优先用 `determine_rate`，`round_rate` 只在没有前者时才被调。分频器两个都填，是为了让"被别人（如 composite）拆出来单用"时两条路都通。

### §3.6 注册函数族

`__clk_hw_register_divider`（`:537`）、`__devm_clk_hw_register_divider`（`:663`，release 回调 `devm_clk_hw_release_divider` 在 `:658`）。

头文件里的宏包装群是全套基本类型里最庞大的一组，**九个宏**：`clk_hw_register_divider`（`include/linux/clk-provider.h:750`）、`_parent_hw`（`:768`）、`_parent_data`（`:787`）、`_table`（`:807`）、`_table_parent_hw`（`:827`）、`_table_parent_data`（`:848`），以及三个 devm 版本（`:868` / `:885` / `:906`）。命名规律是**两个正交维度的笛卡儿积**：「怎么描述父亲」（名字 / `clk_hw` / `clk_parent_data`）× 「有没有分频比表」，再乘 devm。看到一个陌生的 `clk_hw_register_divider_*` 名字，按这两个维度拆开就能读懂。

全树使用统计：`clk_hw_register_divider` 21 行 / 14 文件（排除 `drivers/clk/clk-*.c` 自身）。

---

## §4 mux：多路选择器

**文件**：`drivers/clk/clk-mux.c`（285 行）。**结构体**：`include/linux/clk-provider.h:946-954`，`to_clk_mux` 在 `:956`。

### §4.1 六个 flags

| flag | 值 | 语义 | 注释处 |
|---|---|---|---|
| `CLK_MUX_INDEX_ONE` | `BIT(0)`（`:958`） | 寄存器编号从 1 开始 | `:932` |
| `CLK_MUX_INDEX_BIT` | `BIT(1)`（`:959`） | 寄存器编号是"独热"位（2 的幂） | `:933` |
| `CLK_MUX_HIWORD_MASK` | `BIT(2)`（`:960`） | 高 16 位写使能掩码 | `:934-937` |
| `CLK_MUX_READ_ONLY` | `BIT(3)`（`:961`） | 寄存器不可写，只能读 | `:938-939` |
| `CLK_MUX_ROUND_CLOSEST` | `BIT(4)`（`:962`） | 挑最接近目标的父亲（默认挑不超过目标的） | `:940-941` |
| `CLK_MUX_BIG_ENDIAN` | `BIT(5)`（`:963`） | 大端访问 | `:942-944` |

前两个和 `table` 三者是**同一个问题的三种答案**："第 index 个父亲对应寄存器里写什么值"。`clk_mux_val_to_index`（`:43`）和 `clk_mux_index_to_val`（`:70`）就是这两个方向的换算，都是**非 `static` + `EXPORT_SYMBOL_GPL` + 头文件有声明**，全树各 9 个文件在用（§0.2）。

### §4.2 `clk_mux_set_parent` 和 gate 一样的 hiword 分支

`drivers/clk/clk-mux.c:99-127`。和 §2.2 的 gate 完全同构：`CLK_MUX_HIWORD_MASK` 时 `reg = mux->mask << (mux->shift + 16)`（`:111-112`）不读寄存器；否则读-改-写（`:113-115`）。锁同样可选（`:105-108` / `:121-124`）。

**注意 `clk_mux_set_parent` 无条件 `return 0`（`:126`）**——它不检查 `index` 是否越界，也不回读确认。越界检查在核心侧（`clk_set_parent` 的 `index >= num_parents` 判断），类型侧信任调用者。

### §4.3 两张 ops 表的不对称是本类型最容易踩的坑

```
clk_mux_ops    (:137-142) = { get_parent, set_parent, determine_rate }
clk_mux_ro_ops (:144-147) = { get_parent }                    ← 只有一个！
```

只读 mux **没有 `determine_rate` 也没有 `round_rate`**，于是 `clk_core_can_round()`（`drivers/clk/clk.c:1623-1626`，判据是 `determine_rate || round_rate`）对它返回 false。后果：

| 你做的事 | 只读 mux 上的实际结果 |
|---|---|
| `clk_round_rate(clk, X)` | 不会向父亲转发协商请求 |
| `clk_set_rate(clk, X)` | 无 `set_rate` 且无 `CLK_SET_RATE_PARENT` → **静默返回 0**（见 `03-internals.md` §A7.1） |
| `clk_get_parent(clk)` | 正常工作（`get_parent` 在表里） |

想要"父亲不能换、但频率请求要能穿透到父亲"，正确做法不是用 `clk_mux_ro_ops`，而是用 `clk_mux_ops` + 给节点加 `CLK_SET_RATE_NO_REPARENT` 标志。

### §4.4 `determine_rate` 的实现其实住在核心里

`clk_mux_determine_rate`（`:129-135`）只是一层转发，真正的搜索算法是 `clk_mux_determine_rate_flags`，实现在 **`drivers/clk/clk.c:677`**（`EXPORT_SYMBOL_GPL` 在 `:751`）。核心还包了两个便捷入口给厂商用：

| 便捷入口 | 定义处 | 等价于 |
|---|---|---|
| `__clk_mux_determine_rate` | `drivers/clk/clk.c:831` | `clk_mux_determine_rate_flags(hw, req, 0)` |
| `__clk_mux_determine_rate_closest` | `drivers/clk/clk.c:838` | `clk_mux_determine_rate_flags(hw, req, CLK_MUX_ROUND_CLOSEST)` |

**这是"mux 类型"和"核心"的分界线画在了意想不到的地方**：换父亲的**寄存器操作**在 `clk-mux.c`，而"该换哪个父亲"的**搜索策略**在 `clk.c`。原因是搜索需要遍历 `core->parents[]` 并递归问每个父亲能给什么频率——那是核心的私有数据。所以厂商自造 mux 时，通常只自己写 `get_parent`/`set_parent`，`determine_rate` 直接填 `__clk_mux_determine_rate`。

### §4.5 注册函数族

`__clk_hw_register_mux`（`:149`）、`__devm_clk_hw_register_mux`（`:215`，release 回调 `:210`）。头文件宏群：`clk_hw_register_mux_table`（`include/linux/clk-provider.h:992`）、`_table_parent_data`（`:999`）、`clk_hw_register_mux`（`:1006`）、`_hws`（`:1012`）、`_parent_data`（`:1017`）、`_parent_data_table`（`:1023`）。

全树使用统计：`clk_hw_register_mux` 22 行 / 9 文件。

---

## §5 fixed-rate：定频时钟

**文件**：`drivers/clk/clk-fixed-rate.c`（240 行）。**结构体**：`include/linux/clk-provider.h:350-357`。

**这是 arm64 上出现频率最高的一种时钟类型，而且它的入口不是 C 代码而是 DTS。** 每块板子的 `arch/arm64/boot/dts/` 里都有若干个 `compatible = "fixed-clock"` 节点（晶振、外部时钟输入），DTS 写法见 `06-devicetree.md`。

### §5.1 唯一一个 flag，语义是"精度从哪来"

`CLK_FIXED_RATE_PARENT_ACCURACY` = `BIT(0)`（`include/linux/clk-provider.h:357`，注释 `:347`）。

`clk_fixed_rate_recalc_accuracy`（`drivers/clk/clk-fixed-rate.c:35-44`）：设了这个 flag 就**继承父亲的精度**，否则返回自己记录的 `fixed_accuracy`。

结合 `03-internals.md` §B3 的"精度是缓存值"和 `__clk_core_init` 里"精度 0 = 完美时钟"一起读：一个不设这个 flag、也不填 `fixed_accuracy` 的定频时钟，会向整棵树宣称自己**零误差**。

### §5.2 ops 表只有两个回调，而且没有 `enable`

`clk_fixed_rate_ops`（`:46-50`）= `{ recalc_rate, recalc_accuracy }`。`recalc_rate` 就一行 `return to_clk_fixed_rate(hw)->fixed_rate;`（`:29-33`）。

**没有 `enable`/`disable`/`is_enabled` 的直接后果**：`clk_is_enabled_when_prepared()` 的判据正是"没有 `.enable`/`.disable` 回调"（`drivers/clk/clk.c:1359-1362`），所以定频时钟**一旦 prepare 就视为已使能**。这也是 `03-internals.md` §C1 里"prepare/enable 分离在很多类型上是冗余的"那条评价的最直接例证。

### §5.3 DT 入口：一个函数、两条注册路径

`_of_fixed_clk_setup`（`:160-188`）是真正干活的，它读三个属性：

| DT 属性 | 消费行 | 缺失时 |
|---|---|---|
| `clock-frequency` | `:167-168` | **返回 `-EIO`，注册失败** |
| `clock-accuracy` | `:170` | 默认 0（= 完美时钟，见 §5.1） |
| `clock-output-names` | `:172` | 退回用 DT 节点名（`:162`） |

两条路径都调它：

1. **早期路径**：`of_fixed_clk_setup`（`:195`）+ `CLK_OF_DECLARE(fixed_clk, "fixed-clock", ...)`（`:198`）——在 `of_clk_init()` 阶段就注册，赶在任何设备 probe 之前
2. **平台驱动路径**：`of_fixed_clk_probe`（`:209`）/ `of_fixed_clk_remove`（`:199`），`of_device_id` 表在 `:226`，`platform_driver` 在 `:231`

**同一个 `compatible` 同时挂了 `CLK_OF_DECLARE` 和 platform driver，这不是重复注册。** 机制见 `03-internals.md` §A1：`CLK_OF_DECLARE` 展开出的 `of_clk_init` 阶段处理完节点后会置上 `OF_POPULATED`，platform 层就不会再把它变成设备。platform 驱动分支存在的意义是为**运行期动态添加**的节点（overlay）兜底。

### §5.4 注册函数

`__clk_hw_register_fixed_rate`（`:64`）、`devm_clk_hw_register_fixed_rate_release`（`:52`）；拆除 `clk_hw_unregister_fixed_rate`（`:148`）/ 废弃的 `clk_unregister_fixed_rate`（`:135`）。头文件里有一大圈宏，其中 `:480` 那一处显式带上了 `CLK_FIXED_RATE_PARENT_ACCURACY`。

全树使用统计：`clk_hw_register_fixed_rate` 28 行 / 19 文件——**文件数是八种类型里最多的**。

---

## §6 fixed-factor：定比时钟

**文件**：`drivers/clk/clk-fixed-factor.c`（342 行）。**结构体**：`include/linux/clk-provider.h:1072-1076`，`to_clk_fixed_factor` 在 `:1078`。就两个数字：`mult` 和 `div`，输出 = 父频率 × mult ÷ div。

### §6.1 `set_rate` 是个空函数，而且注释解释了为什么这样是对的

`clk_factor_set_rate`（`drivers/clk/clk-fixed-factor.c:48-57`）整个函数体只有 `return 0`，注释逐字如下（`:49-53`）：

```c
        /*
         * We must report success but we can do so unconditionally because
         * clk_factor_round_rate returns values that ensure this call is a
         * nop.
         */
```

翻成一句话：**"我必须返回成功，而我可以无条件返回成功，因为 `round_rate` 已经保证了传进来的 rate 必然是我本来就能输出的那个值。"**

这是全套基本类型里最值得记住的一个设计模式：**用 `round_rate` 的返回值把 `set_rate` 的合法输入收窄到"只有一个"，`set_rate` 就退化成空函数。** 之所以不能干脆不提供 `set_rate`，是因为注册期校验要求"有 `set_rate` 才能参与频率变更提交"（`drivers/clk/clk.c:3808-3814`），而定比时钟需要出现在调速路径上——它得把请求转发给父亲。

### §6.2 `round_rate` 的两段式

`clk_factor_round_rate`（`:33-46`）：

```c
        if (clk_hw_get_flags(hw) & CLK_SET_RATE_PARENT) {
                unsigned long best_parent;
                best_parent = (rate / fix->mult) * fix->div;
                *prate = clk_hw_round_rate(clk_hw_get_parent(hw), best_parent);
        }
        return (*prate / fix->div) * fix->mult;
```

**没有 `CLK_SET_RATE_PARENT` 时它连 `rate` 参数都不看**，直接按当前父频率算输出。这解释了一类现场问题："我的定比时钟怎么调都不动"——答案通常是节点忘了加 `CLK_SET_RATE_PARENT`。

注意 `clk_hw_round_rate` 这个调用：`03-internals.md` §C2 已登记它的设计债——`include/linux/clk-provider.h:1350-1352` 有 FIXME 说它不加锁。

### §6.3 注册函数族——八种类型里最庞杂的一组

| 函数 | 定义处 | 差别 |
|---|---|---|
| `clk_hw_register_fixed_factor` | `:191` | 用父亲名字 |
| `clk_hw_register_fixed_factor_parent_hw` | `:181` | 用父亲的 `clk_hw` |
| `devm_clk_hw_register_fixed_factor` | `:238` | devm 版 |
| `devm_clk_hw_register_fixed_factor_index` | `:150` | 用 **DT 里 `clocks` 的下标**指定父亲 |
| `devm_clk_hw_register_fixed_factor_parent_hw` | `:172` | devm + `clk_hw` |
| `clk_unregister_fixed_factor` | `:214` | 已废弃 |
| `clk_hw_unregister_fixed_factor` | `:227` | 拆除 |

内部共同实现在 `:79` 的那个匿名换行的 `static struct clk_hw *`，release 回调 `:67`。

`devm_clk_hw_register_fixed_factor_index` 是唯一一个把 **DT 下标**做进签名的注册函数，专给"父亲只在 DT 里有、C 代码里拿不到名字"的场景。

### §6.4 DT 入口和 §5.3 同构

`_of_fixed_factor_clk_setup`（`:248`）；`CLK_OF_DECLARE(fixed_factor_clk, "fixed-factor-clock", ...)` 在 `:297`；平台驱动侧 `of_fixed_factor_clk_probe`（`:310`）/ `_remove`（`:300`）/ `of_device_id`（`:327`）/ `platform_driver`（`:333`），并且用的是 `builtin_platform_driver`（`:341`）——**不能做模块，只能编进内核**。

全树使用统计：`clk_hw_register_fixed_factor` 32 行 / 16 文件；`clk_fixed_factor_ops` 被直接引用 **134 行**——是全部 ops 表里被引用最多的一个（因为 `include/linux/clk-provider.h:1471` / `:1482` / `:1497` / `:1508` 那四个宏把它硬编码进去了，厂商每用一次宏就算一次）。

---

## §7 fractional-divider：小数分频器

**文件**：`drivers/clk/clk-fractional-divider.c`（259 行）。**结构体**：`include/linux/clk-provider.h:1132-1146`，`to_clk_fd` 在 `:1148`。输出 = 父频率 × m ÷ n，m/n 各占寄存器里一个位域。

### §7.1 这个类型的 ops 表不在公共头文件里

```
extern const struct clk_ops clk_fractional_divider_ops;   ← drivers/clk/clk-fractional-divider.h:7
```

**它在 `drivers/clk/` 下的私有头文件里，不在 `include/linux/clk-provider.h`。** 判据（`05-usage.md` 用的同一条）是"`EXPORT_SYMBOL*` **且**公共头文件里有声明"——这个符号只满足前一半（`drivers/clk/clk-fractional-divider.c:191`）。后果：**只有住在 `drivers/clk/` 下的驱动能用它**，别的子系统或树外驱动没有合法的声明可 include。全树两个使用者确实都在 `drivers/clk/` 内（`drivers/clk/rockchip/clk.c:248`、`drivers/clk/imx/clk-composite-7ulp.c:149`）。

对比：`clk_multiplier_ops` 在公共头文件 `:1203` 有声明，`clk_gate_ops` 在 `:524`——只有小数分频器这一个是"半私有"的。

### §7.2 三个 flags 和一个可替换的近似算法

| flag | 值 | 语义 | 注释处 |
|---|---|---|---|
| `CLK_FRAC_DIVIDER_ZERO_BASED` | `BIT(0)`（`:1150`） | m/n 是寄存器值 **+1** | `:1118-1121` |
| `CLK_FRAC_DIVIDER_BIG_ENDIAN` | `BIT(1)`（`:1151`） | 大端访问 | `:1122-1124` |
| `CLK_FRAC_DIVIDER_POWER_OF_TWO_PS` | `BIT(2)`（`:1152`） | 允许左移几位再取近似，前提是调用方有 2 的幂预分频器 | `:1125-1131` |

注意 `ZERO_BASED` 这个名字**和它的语义是反的**：flag 名叫"零基"，行为却是"读到的值 **加一**"。头文件注释（`:1118-1121`）写得很清楚，但只看名字必然读反。

**`struct clk_fractional_divider` 里有一个函数指针 `approximation`**（`include/linux/clk-provider.h:1141-1143`）：驱动可以插自己的近似算法。分派点在 `clk_fd_round_rate`（`drivers/clk/clk-fractional-divider.c:130-149`）：

```c
        if (fd->approximation)
                fd->approximation(hw, rate, parent_rate, &m, &n);
        else
                clk_fractional_divider_general_approximation(hw, rate, parent_rate, &m, &n);
```

通用实现 `clk_fractional_divider_general_approximation` 在 `:104`（导出，公共头文件有声明），全树 1 行 / 1 文件在用——**也就是说树里只有一家驱动自己填了 `approximation` 之外还要直接调通用版本**。这个字段在树内几乎没人填，说明"可插拔近似算法"目前是**预留能力**而不是活跃机制。

### §7.3 ops 表和注册

`clk_fractional_divider_ops`（`:186-190`）= `{ recalc_rate, round_rate, set_rate }`，实现分别在 `:66` / `:130` / `:151`。注册 `clk_hw_register_fractional_divider`（`:193`，导出 `:233`），拆除 `clk_hw_unregister_fractional_divider`（`:251`）。

全树使用统计：`clk_hw_register_fractional_divider` 排除 `drivers/clk/clk-*.c` 自身后 **0 行 / 0 文件**——树内除了同文件 `:242` 的 devm 包装，**没有任何驱动调这个注册函数**。两家使用者（rockchip / imx）都是自己 `kzalloc` 结构、直接填 `init.ops`。这条和 §0.2 的结论一致：**基本类型的注册函数比它的 ops 表和 helper 冷得多。**

---

## §8 multiplier：倍频器

**文件**：`drivers/clk/clk-multiplier.c`（**156 行，八种类型里最小**）。**结构体**：`include/linux/clk-provider.h:1188-1195`，`to_clk_multiplier` 在 `:1197`。

### §8.1 它没有注册函数

```bash
grep -rn --include='*.c' --include='*.h' -w 'clk_hw_register_multiplier' .    # → 空
grep -rn --include='*.c' --include='*.h' -w 'clk_register_multiplier'    .    # → 空
```

**全树没有这两个符号**（两条命令都返回空）。这个类型只导出一张 ops 表（`clk_multiplier_ops`，`:151-155`，公共头文件声明在 `include/linux/clk-provider.h:1203`），驱动必须自己 `kzalloc` 出 `struct clk_multiplier`、填好 `init.ops`、调 `clk_hw_register`。八种类型里**只有它和 composite 是这个形态**（composite 是因为要运行期拼装，见 §9）。

### §8.2 三个 flags

| flag | 值 | 语义 | 注释处 |
|---|---|---|---|
| `CLK_MULTIPLIER_ZERO_BYPASS` | `BIT(0)`（`:1199`） | 寄存器读到 0 时当作 ×1（否则输出会是 0） | `:1177-1181` |
| `CLK_MULTIPLIER_ROUND_CLOSEST` | `BIT(1)`（`:1200`） | 取最接近的倍数 | `:1182-1183` |
| `CLK_MULTIPLIER_BIG_ENDIAN` | `BIT(2)`（`:1201`） | 大端访问 | `:1184-1186` |

`ZERO_BYPASS` 的实现在 `clk_multiplier_recalc_rate`（`drivers/clk/clk-multiplier.c:41-54`）：`if (!val && mult->flags & CLK_MULTIPLIER_ZERO_BYPASS) val = 1;`（`:50-51`）。**不设这个 flag 时，寄存器里是 0 就真的输出 0 Hz**——不是"旁路"，是"停"。

`ROUND_CLOSEST` 的分派在 `__get_mult`（`:31-39`）：`DIV_ROUND_CLOSEST(rate, parent_rate)` 对 `rate / parent_rate`。

### §8.3 arm64 上等于不存在

`clk_multiplier_ops` 全树使用（含定义与导出行）4 行，两个真实使用者：

- `drivers/clk/sunxi/clk-a10-pll2.c:98`
- `drivers/clk/sunxi/clk-sun4i-pll3.c:60`

两者都在 `drivers/clk/sunxi/`——**arm32 时代的 sunxi 老驱动**。arm64 的 Allwinner 走 `drivers/clk/sunxi-ng/` 那一整套自造 CCU 抽象，不碰这个类型。**所以在 arm64 语境下读到 multiplier 这一节，知道"有这么个东西、没人用"就够了。**

其余函数（都 `static`）：`__is_best_rate`（`:56`）、`__bestmult`（`:65`，50 行的搜索主循环）、`clk_multiplier_round_rate`（`:115`）、`clk_multiplier_set_rate`（`:125`）。

---

## §9 composite：复合时钟——唯一一个依赖别的类型的类型

**文件**：`drivers/clk/clk-composite.c`（489 行）。**结构体**：`include/linux/clk-provider.h:1216-1227`，`to_clk_composite` 在 `:1229`。

它解决的问题很实际：**硬件上一个"外设时钟"往往同时是 mux + 分频器 + 门控三件套**，如果注册成三个 clk 节点，消费者就得知道三个名字。composite 把三者包成**一个** clk 节点。

### §9.1 它的 `clk_ops` 是内嵌的、非 const 的

```c
struct clk_composite {
        struct clk_hw   hw;
        struct clk_ops  ops;          /* ← 不是 const 指针，是内嵌的可写副本 */
        struct clk_hw   *mux_hw;  *rate_hw;  *gate_hw;
        const struct clk_ops *mux_ops; *rate_ops; *gate_ops;
};
```

`03-internals.md` §A2 那条"`init.ops` 是**指针**拷贝，所以 ops 表必须是 `static const`"的规则，**唯一的例外就在这里**：composite 的 ops 表是每个实例自带的一份可写内存（`&composite->ops`，取址在 `drivers/clk/clk-composite.c:261`），注册前逐字段填。

### §9.2 拼装规则：逐字段的"有则填、无则空"

`__clk_hw_register_composite`（`:234`）。三段拼装逻辑，**每一段都是"子 ops 表里有这个回调，我才装对应的转发回调"**：

| 段 | 硬性校验（不满足 → `-EINVAL`） | 装了什么 |
|---|---|---|
| mux（`:263-276`） | `mux_ops->get_parent` 必须有（`:265-268`） | `get_parent`（`:271`）；有 `set_parent` 才装 `set_parent`（`:272-273`）；有 `determine_rate` 才装 `determine_rate`（`:274-275`） |
| rate（`:278-303`） | `rate_ops->recalc_rate` 必须有（`:279-282`） | `recalc_rate`（`:283`）；`determine_rate` 优先、退化到 `round_rate`（`:285-290`）；有 `set_rate` 但两个协商回调都没有 → **`WARN(1, ...)` 但不失败**（`:293-300`） |
| mux+rate 都有（`:305-309`） | `mux_ops->set_parent && rate_ops->set_rate` | `set_rate_and_parent`（`:307-308`） |
| gate（`:311-324`） | `is_enabled` **且** `enable` **且** `disable` 三个都要有（`:312-315`） | `is_enabled` / `enable` / `disable`（`:321-323`） |

**三段的校验严格程度不一样，这个不对称是有意的**：mux 只强制 `get_parent`（读父亲是最低要求），rate 只强制 `recalc_rate`（读频率是最低要求），gate 却强制三个全有——因为门控没有"只读"的用法，一个不能开关的门控节点毫无意义。

**rate 段那个 `WARN(1, "%s: missing round_rate op is required\n", __func__)`（`:298-299`）是"抱怨但放过"**：它不 `goto err`，注册照样成功，只是 `set_rate` 不会被装上。这跟核心侧的注册校验（`drivers/clk/clk.c:3808-3814`，`pr_err` 后 `return -EINVAL`）不一样——**同一个"缺 round_rate"的错误，在 composite 里是警告，落到核心里是失败**。之所以不冲突：composite 没装 `set_rate`，核心那条规则就不会触发。

### §9.3 注册收尾：三个子 hw 共享同一个 `clk`

`:326-329` 把拼好的表塞进 `init.ops` 并 `clk_hw_register`，然后 `:338-346`：

```c
        if (composite->mux_hw)  composite->mux_hw->clk  = hw->clk;
        if (composite->rate_hw) composite->rate_hw->clk = hw->clk;
        if (composite->gate_hw) composite->gate_hw->clk = hw->clk;
```

**三个子 `clk_hw` 的 `clk` 指针被指向同一个 `struct clk`**。这一步很关键：子 ops 表的实现（比如 `divider_recalc_rate`）内部会调 `clk_hw_get_parent(hw)` 之类的核心 API，那些 API 要顺着 `hw->clk` 找回核心结构。不做这一步，子 ops 一调核心 API 就空指针。

反过来看，这也意味着**子 `clk_hw` 不是独立的 clk 节点**：它们没有自己的 `clk_core`、不在任何链表上、debugfs 里看不到、`clk_get` 拿不到。它们只是"寄存器描述 + ops 表"的载体。

### §9.4 六个转发回调

全部 `static`，每个都是"`to_clk_composite(hw)` 取出子 hw、换用子 ops 表调下去"：`clk_composite_get_parent`（`:11`）、`clk_composite_set_parent`（`:22`）、`clk_composite_recalc_rate`（`:33`）、`clk_composite_determine_rate`（`:68`，82 行，最复杂的一个）、`clk_composite_round_rate`（`:150`）、`clk_composite_set_rate`（`:162`）、`clk_composite_set_rate_and_parent`（`:174`）、`clk_composite_is_enabled`（`:201`）、`clk_composite_enable`（`:212`）、`clk_composite_disable`（`:223`）；`clk_composite_determine_rate` 的内层辅助是 `clk_composite_determine_rate_for_parent`（`:45`）。

`clk_composite_determine_rate` 之所以最长：它要在**换父亲**和**改分频比**这两个自由度上做二维搜索。`:82-86` 那个 `CLK_SET_RATE_NO_REPARENT` 分支是"只调分频比"的快路径。

### §9.5 注册函数族与使用统计

| 函数 | 定义处 | 备注 |
|---|---|---|
| `clk_hw_register_composite` | `:351` | 用父亲名字数组 |
| `clk_hw_register_composite_pdata` | `:365` | 用 `clk_parent_data` 数组 |
| `devm_clk_hw_register_composite_pdata` | `:476` | devm 版（release 回调 `:442`，内部 `:447`） |
| `clk_unregister_composite` | `:416` | 已废弃 |
| `clk_hw_unregister_composite` | `:431` | 拆除 |

全树使用统计（排除 `drivers/clk/clk-*.c` 自身）：

| 符号 | 行 / 文件 |
|---|---|
| `clk_hw_register_composite` | 12 / 9 |
| `clk_hw_register_composite_pdata` | **0 / 0** —— 树内只有同文件 `:408` 的 devm 包装在调 |
| `devm_clk_hw_register_composite_pdata` | 1 / 1（`drivers/clk/clk-fsl-sai.c:61`） |

`clk_hw_register_composite_pdata` 是又一个"为对称而存在、树内无直接使用者"的 API（同类还有 §3.4 的两个 `divider_ro_*`）。

---

## §10 clkdev：非 DT 的时钟查找表

**文件**：`drivers/clk/clkdev.c`（381 行）。

它是 clk 框架里**比 DT 更老**的一层：靠一张 `(dev_id, con_id) → clk_hw` 的全局链表做查找。`03-internals.md` §A5 已经讲过它在 `clk_get` 里的位置——**DT 查找失败之后的兜底分支**。本节只讲这一层自己的结构和 API。

### §10.1 全局状态就两个变量

| 变量 | 位置 | 说明 |
|---|---|---|
| `clocks` | `drivers/clk/clkdev.c:24` | `LIST_HEAD`，所有注册进来的 `clk_lookup` |
| `clocks_mutex` | `:25` | 保护上面那条链表 |

这是**第四把锁**。`02-datastructures.md` §7.1 列的是 `prepare_lock` / `enable_lock`，`03-internals.md` §A4 补了 `of_clk_mutex`，这里是 `clocks_mutex`。四把锁互不相干（`clocks_mutex` 只在 clkdev 这一层内部使用，不与 `prepare_lock` 嵌套）。

### §10.2 匹配算法：`clk_find` 的打分

`clk_find`（`:36-70`）不是简单相等比较，而是**按匹配精度打分取最优**：`dev_id` 和 `con_id` 各自可以是"精确匹配"或"通配（表项里写 NULL）"，精确匹配得分高。这让驱动能注册"任何设备的 `mclk` 都指向这个时钟"这类兜底表项。

### §10.3 API 三组

**查找组**：

| API | 定义处 | 说明 |
|---|---|---|
| `clk_get` | `:100` | 消费者主入口，先试 DT 再落到这里 |
| `clk_get_sys` | `:94` | 只按 `(dev_id, con_id)` 字符串查，**不需要 `struct device`** |
| `clk_put` | `:115` | 释放 |
| `__clk_get_sys` | `:86` | 内部实现 |

`clk_get_sys` 的价值是"我连 `struct device` 都没有"——早期板级代码里常见。

**表项管理组**：

| API | 定义处 | 说明 |
|---|---|---|
| `clkdev_add` | `:128` | 加一个表项（`__clkdev_add` 在 `:121`） |
| `clkdev_add_table` | `:136` | 批量加 |
| `clkdev_create` | `:180`（导出 `:214`） | 分配 + 加，用 `struct clk` |
| `clkdev_hw_create` | 导出在 `:237` | 同上，用 `struct clk_hw`（提供方应该用这个） |
| `clkdev_drop` | `:259` | 删表项 |
| `clk_add_alias` | `:239` | 给已有时钟起别名 |

**提供方注册组**：

| API | 定义处 | 说明 |
|---|---|---|
| `clk_register_clkdev` | `:313` | 已废弃形态（收 `struct clk *`） |
| `clk_hw_register_clkdev` | `:340` | 现代形态（收 `struct clk_hw *`） |
| `devm_clk_hw_register_clkdev` | `:369` | devm 版（release 回调 `:349`） |
| `__clk_register_clkdev` / `do_clk_register_clkdev` | `:268` / `:282` | 内部实现 |

### §10.4 挂在 03 时间线的哪一点

两头都挂：**提供方侧**挂在 §A4（`of_clk_add_hw_provider` 的平行位置——DT 提供方注册到 `of_clk_providers`，clkdev 提供方注册到 `clocks`）；**消费者侧**挂在 §A5（`clk_get` 的第二条分支）。

arm64 上正常路径不走这里。**看到 arm64 驱动在用 `clkdev_add`，基本可以断定是从 arm32 移植过来没清理干净。**

---

## §11 clk-bulk：批量操作

**文件**：`drivers/clk/clk-bulk.c`（248 行）。数据结构 `struct clk_bulk_data`（`include/linux/clk.h`）就两个字段：`id`（名字）和 `clk`。

### §11.1 API 全表

| API | 定义处 | 说明 |
|---|---|---|
| `clk_bulk_get` | `:114` | 按 `clks[i].id` 逐个 `clk_get`，任一失败就全部回滚 |
| `clk_bulk_get_optional` | `:121` | 同上，但缺失不算错误 |
| `clk_bulk_get_all` | `:139` | **不需要预先知道名字**——从 DT 里数出有几路，自己分配数组 |
| `clk_bulk_put` | `:72` | 对应前两个 |
| `clk_bulk_put_all` | `:128` | 对应 `_get_all`（还要释放数组本身） |
| `clk_bulk_prepare` | `:176` | 逐个 prepare，失败回滚已 prepare 的 |
| `clk_bulk_unprepare` | `:161` | |
| `clk_bulk_enable` | `:227` | 逐个 enable，失败回滚 |
| `clk_bulk_disable` | `:211` | |

内部实现：`__clk_bulk_get`（`:81`）、`of_clk_bulk_get`（`:15`）、`of_clk_bulk_get_all`（`:46`）。

### §11.2 三条容易搞错的语义

1. **`clk_bulk_*` 不是原子的，只是"带回滚的循环"**。`clk_bulk_enable` 失败时会把已经开的关掉（`:227-247`），但**开的过程中别人是能看到中间状态的**——它不持有任何跨调用的锁
2. **`_get_all` 和 `_get` 的释放函数不能混用**。`_get_all` 自己 `kcalloc` 了数组，必须用 `clk_bulk_put_all`（`:128-136`）才会 `kfree`；用 `clk_bulk_put` 会漏内存
3. **`clk_bulk_get_all` 拿到的顺序是 DT 里 `clocks` 属性的顺序，不带名字语义**。想按名字取就得用 `clk_bulk_get` 并自己填 `id`

### §11.3 挂在 03 时间线的哪一点

全部挂在 §A5/§A6：它就是 `clk_get` + `clk_prepare` + `clk_enable` 的循环包装，没有引入任何新机制。**所以它也继承了那三个函数的全部语义**，包括 `-ESHUTDOWN`、`CLK_SET_RATE_GATE` 自动加频率保护等。

---

## §12 clk-devres：devm 生命周期

**文件**：`drivers/clk/clk-devres.c`（225 行）。**arm64 消费者驱动实际上只用这一套。**

### §12.1 六个 `devm_clk_get*` 变体——一张 2×3 的表

`__devm_clk_get`（`:22-61`）是唯一的实现，六个导出函数都是它的不同参数组合：

| | 只 get | get + prepare | get + prepare + enable |
|---|---|---|---|
| **缺失即错误** | `devm_clk_get`（`:63`） | `devm_clk_get_prepared`（`:69`） | `devm_clk_get_enabled`（`:75`） |
| **缺失返回 NULL** | `devm_clk_get_optional`（`:82`） | `devm_clk_get_optional_prepared`（`:88`） | `devm_clk_get_optional_enabled`（`:95`） |

**`devm_clk_get_enabled` 是消费者驱动的首选**，因为它把"get / prepare / enable / 出错回滚 / remove 时反向拆除"五件事一次做完。release 回调 `devm_clk_release` 在 `:12-20`，它按当初的请求级别反向执行。

**`_optional` 系列返回 NULL 而不是错误指针，而 clk 的所有 API 都接受 NULL 并静默返回成功**——这是"可选时钟"能写得这么干净的原因，也是"我的时钟没配上但驱动一点错都不报"的原因。

### §12.2 bulk 的 devm 版

| API | 定义处 | 内部实现 |
|---|---|---|
| `devm_clk_bulk_get` | `:140` | `__devm_clk_bulk_get`（`:114`），release `:107` |
| `devm_clk_bulk_get_optional` | `:147` | 同上 |
| `devm_clk_bulk_get_all` | `:161` | release 是**另一个** `devm_clk_bulk_release_all`（`:154`） |

注意两个 release 回调是分开的（`:107` vs `:154`），原因就是 §11.2 第 2 条——`_all` 要多 `kfree` 一次数组。

### §12.3 两个特殊 API

| API | 定义处 | 说明 |
|---|---|---|
| `devm_clk_put` | `:195` | **提前**释放一个 devm 管的时钟（配套匹配函数 `devm_clk_match` 在 `:185`） |
| `devm_get_clk_from_child` | `:205` | 从**子 DT 节点**取时钟，绑到父设备的 devm 上 |

`devm_get_clk_from_child` 的用途：一个设备的 DT 节点下有若干子节点各自声明时钟（音频 codec 的多路），但只有父节点有 `struct device`。

### §12.4 挂在 03 时间线的哪一点

同 §11.3——`clk_get` / `prepare` / `enable` 的包装，额外挂钩到 driver core 的 devres 链上。

---

## §13 clk-conf：`assigned-clocks` 三属性

**文件**：`drivers/clk/clk-conf.c`（152 行）。**唯一入口是 `of_clk_set_defaults`（`:139`，导出 `:152`，kerneldoc `:128-138`）。**

### §13.1 它做的两件事

| 函数 | 定义处 | 读哪些 DT 属性 | 干什么 |
|---|---|---|---|
| `__set_clk_parents` | `:14` | `assigned-clock-parents`（`:20` / `:27`）+ `assigned-clocks`（`:49`） | 对每一路调 `clk_set_parent` |
| `__set_clk_rates` | `:81` | `assigned-clock-rates`（`:90`）+ `assigned-clocks`（`:92`） | 对每一路调 `clk_set_rate` |

`of_clk_set_defaults` 先调前者再调后者——**顺序有意义**：先定父亲，再定频率（父亲变了频率就得重算）。

### §13.2 `clk_supplier` 这个参数是全篇最容易读错的地方

`of_clk_set_defaults(np, clk_supplier)` 的第二个参数是个 `bool`，语义是**"我现在是不是正在处理这个节点自己作为时钟提供方的场合"**。两个调用点给的值不同：

| 调用点 | 位置 | `clk_supplier` | 处理的是 |
|---|---|---|---|
| `of_clk_add_hw_provider` | `drivers/clk/clk.c:4934` | `true` | 提供方节点自己的 `assigned-*`（自己给自己设默认值） |
| driver core（platform 侧） | `drivers/of/platform.c` | `false` | 消费者节点的 `assigned-*` |

判据在两个 `__set_*` 函数内部：如果 `assigned-clocks` 指向的 provider 就是当前节点，而 `clk_supplier` 是 `false`，就跳过——**避免在提供方还没注册完时就去设它**。

对读者的实用结论：**DTS 里 `assigned-clocks` 写在哪个节点上，决定了它什么时候生效**。写在时钟控制器节点上 → 控制器注册时就生效；写在消费者节点上 → 消费者 probe 时才生效。DTS 写法见 `06-devicetree.md`。

### §13.3 挂在 03 时间线的哪一点

`03-internals.md` §A4 的最后一步：`of_clk_add_hw_provider` → `of_clk_set_defaults`（`drivers/clk/clk.c:4934`）。**也就是说提供方一注册完，DTS 里预设的父/频率就立刻落下去了**，早于任何消费者 `clk_get`。

---

## §14 跨子系统提供方：GPIO / PWM 当时钟

这两个是"时钟源不是时钟控制器"的场合。**它们在本篇里的价值不是"你会写一个"，而是"它们暴露了 clk_ops 的一条硬约束"。**

### §14.1 clk-gpio：同一个功能提供两张 ops 表，因为 GPIO 可能会睡眠

**文件**：`drivers/clk/clk-gpio.c`（249 行）。

它提供两种时钟：**GPIO 门控**和**GPIO 选择的 mux**。门控这一种有**两张 ops 表**：

| ops 表 | 定义处 | 用的回调 | 什么时候用 |
|---|---|---|---|
| `clk_gpio_gate_ops` | `:74-78` | `enable`（`:51`）/ `disable`（`:60`）/ `is_enabled`（`:67`） | GPIO 可在原子上下文操作 |
| `clk_sleeping_gpio_gate_ops` | `:103-107` | `prepare`（`:80`）/ `unprepare`（`:89`）/ `is_prepared`（`:96`） | GPIO 操作可能睡眠（如挂在 I2C 扩展器上） |

**这是 `02-datastructures.md` §4 里"prepare 可睡眠、enable 不可睡眠"那条规则最干净的现实例证。** 同一个逻辑功能（开关一路时钟），只因为底层 GPIO 能不能在原子上下文操作，就必须换一整张 ops 表——因为 `enable` 回调是在 `enable_lock` 自旋锁里调的（见 `03-internals.md` §B1），里面睡眠就是死锁。

mux 那种只有一张表：`clk_gpio_mux_ops`（`:133-137`）= `{ get_parent（:117）, set_parent（:124） }`。和 §4.3 的 `clk_mux_ro_ops` 一样**没有 `determine_rate`**，所以 GPIO mux 也不参与频率协商。

注册与 probe：`clk_register_gpio`（`:139`，内部）、`clk_hw_register_gpio_gate`（`:173`）、`clk_hw_register_gpio_mux`（`:187`）、`gpio_clk_driver_probe`（`:193`）、`of_device_id` 表（`:236`）、`platform_driver`（`:242`），用 `builtin_platform_driver`（`:249`）——**不能做模块**。

### §14.2 clk-pwm：唯一会填 `get_duty_cycle` 的基础驱动

**文件**：`drivers/clk/clk-pwm.c`（154 行）。`clk_pwm_ops`（`:60-65`）= `{ prepare（:25）, unprepare（:32）, recalc_rate（:39）, get_duty_cycle（:47） }`。

用 `prepare`/`unprepare` 而非 `enable`/`disable`，理由同 §14.1——PWM 子系统的 API 可能睡眠。

**`get_duty_cycle` 在整棵树上极少有人填**，PWM 当时钟是它最自然的场合（PWM 本身就是"占空比"这个概念的原产地）。`03-internals.md` §B3 说过占空比是缓存值且**没有 NOCACHE 逃生口**——对 PWM 时钟来说这意味着别人改了 PWM 占空比，clk 侧的缓存不会更新。

`to_clk_pwm` 在 `:20`；probe `:67`、remove `:132`、`of_device_id` `:139`、`platform_driver` `:145`、`module_platform_driver` `:154`（和 §14.1 相反，**这个可以做模块**）。

### §14.3 挂在 03 时间线的哪一点

都挂在 §A1 的**平台驱动 probe 分支**（不是 `CLK_OF_DECLARE` 早期分支）——因为它们要依赖 GPIO / PWM 子系统先就绪。

---

## §15 固件代理时钟：SCMI / SCPI —— arm64 上最该看懂的一节

这两个是 arm64 服务器和新 SoC 的主流形态：**时钟寄存器根本不在本 CPU 的地址空间里**，Linux 通过消息通道请求另一个处理器（SCP / 电源管理协处理器）去操作。

### §15.1 clk-scmi：运行期按传输层能力挑 ops 表

**文件**：`drivers/clk/clk-scmi.c`（267 行）。

两张 ops 表，共用前三个回调、只在开关这一对上分叉：

```c
static const struct clk_ops scmi_clk_ops = {              /* :118-124 */
        .recalc_rate = scmi_clk_recalc_rate,   /* :27 */
        .round_rate  = scmi_clk_round_rate,    /* :40 */
        .set_rate    = scmi_clk_set_rate,      /* :69 */
        .prepare     = scmi_clk_enable,        /* :77  ← 可睡眠 */
        .unprepare   = scmi_clk_disable,       /* :84 */
};

static const struct clk_ops scmi_atomic_clk_ops = {       /* :126-132 */
        ... 前三个相同 ...
        .enable  = scmi_clk_atomic_enable,     /* :91  ← 原子 */
        .disable = scmi_clk_atomic_disable,    /* :98 */
};
```

**挑哪张表是运行期决定的，判据有两层**（`scmi_clocks_probe`，`:169`）：

```c
        is_atomic = handle->is_transport_atomic(handle, &atomic_threshold);   /* :203 */
        ...
        if (is_atomic && sclk->info->enable_latency <= atomic_threshold)      /* :228-229 */
                scmi_ops = &scmi_atomic_clk_ops;                             /* :230 */
        else
                scmi_ops = &scmi_clk_ops;                                    /* :232 */
```

两层判据：**(1) 传输层本身能不能原子完成**；**(2) 这一路时钟的使能延迟够不够低**（`enable_latency <= atomic_threshold`）。第二层是关键——即使传输是原子的，一路需要 10 ms 才能起振的时钟也不该放进自旋锁里。

源码注释（`:106-117`）自己解释了为什么要**两张表而不是一张表运行期改字段**：

> *Two distinct sets of clk_ops are provided since we could have multiple SCMI instances with different underlying transport quality, so they cannot be shared.*

也就是说系统里可能有多个 SCMI 实例、传输质量不同，所以表必须是两份 `static const`——**这正好呼应 `03-internals.md` §A2 那条"ops 是指针拷贝，表必须 `static const`"**。

probe 还会把选中的模式打印出来（`:242-243`，`" (atomic ops)"` 后缀），**这是现场确认"我这路时钟走的是原子路径还是睡眠路径"最快的办法**。

其余：`scmi_proto_clk_ops`（`:16`，协议层操作集）、`scmi_clk_ops_init`（`:134`）、provider 注册用 `devm_of_clk_add_hw_provider(dev, of_clk_hw_onecell_get, ...)`（`:248`，配 `struct clk_hw_onecell_data`，`:175`）、`scmi_device_id` 表（`:252`，`SCMI_PROTOCOL_CLOCK` + `"clocks"` 在 `:253`）、`scmi_driver`（`:258`）、`module_scmi_driver`（`:263`）。

**SCMI 时钟没有 `set_parent`/`get_parent`**——两张表里都没有。固件不暴露时钟树拓扑，Linux 侧看到的是一组**扁平的、互不相干的时钟**。这跟 §0.2 里厂商 CCU 驱动那种完整树形结构是两个世界：**用 SCMI 的平台，`/sys/kernel/debug/clk/clk_summary` 会是一片扁平列表，没有层级。**

### §15.2 clk-scpi：编译期（DT）挑 ops 表

**文件**：`drivers/clk/clk-scpi.c`（314 行）。SCPI 是 SCMI 的前身。它也有两张 ops 表，但挑法完全不同：

| ops 表 | 定义处 | 回调 | 对应 `compatible` |
|---|---|---|---|
| `scpi_clk_ops` | `:56-61` | `recalc_rate`（`:28`）/ `round_rate`（`:36`）/ `set_rate`（`:48`） | `"arm,scpi-variable-clocks"`（`:134`） |
| `scpi_dvfs_ops` | `:126-130` | `recalc_rate`（`:82`）/ `round_rate`（`:96`）/ `set_rate`（`:115`） | 另一条 `of_device_id` 表项 |

挑表的机制是 `of_device_id` 的 `.data` 字段（`scpi_clk_match[]`，`:132-136`）——**DT 里写哪个 `compatible` 就用哪张表**，probe 时通过 `of_match` 拿到（`scpi_clk_ops_init` 在 `:139`，`:157` 处判断 `init.ops == &scpi_clk_ops`）。

**对比 §15.1：SCMI 按"传输层运行期能力探测"挑表，SCPI 按"DT 声明"挑表。** 前者是全树少见的形态（表的选择取决于运行期硬件/固件能力，DTS 无法预知），后者是内核里再普通不过的 `of_match` 数据驱动分派。这一对差异说明：**同一个"两张 ops 表"的表象，背后可以是完全不同的决策机制**——看到多张 ops 表时，第一件事是找出"谁在什么时候挑"。

其余：DVFS 辅助 `__scpi_dvfs_round_rate`（`:63`）/ `__scpi_find_dvfs_index`（`:104`）、`scpi_clk_add`（`:191`）、probe `:264` / remove `:249`、`scpi_clocks_ids`（`:296`）、`module_platform_driver`（`:310`）。它还会创建一个 cpufreq 平台设备（`cpufreq_dev`，`:26`）——**SCPI 时钟驱动同时是 cpufreq 的入口**。

### §15.3 挂在 03 时间线的哪一点

都在 §A1 的平台/总线驱动 probe 分支，而且**很晚**——要等消息通道（mailbox / SMC）先就绪。这带来一个实际后果：**SCMI 平台上大量设备会经历 `-EPROBE_DEFER`**，因为它们 probe 时时钟提供方还没上来。`03-internals.md` §A5 里那条"保留 `-EPROBE_DEFER` 不吞掉"的判断在这类平台上是刚需，不是优化。

---

## §16 KUnit 测试：改核心逻辑时的回归网

两个测试文件，**都不是子框架而是子框架的验证器**，但它们对读者有独立价值：**它们是内核自己写下的"clk 核心应该怎么表现"的可执行规格**。

| 文件 | 行数 | Kconfig | 覆盖什么 |
|---|---|---|---|
| `drivers/clk/clk_test.c` | 2411 | `CLK_KUNIT_TEST`（`drivers/clk/Kconfig:476`） | 核心行为（12 个 suite） |
| `drivers/clk/clk-gate_test.c` | 464 | `CLK_GATE_KUNIT_TEST`（`drivers/clk/Kconfig:483`） | gate 类型（5 个 suite） |

### §16.1 `clk_test.c` 的 12 个 suite——一张"核心难点清单"

按定义位置排（注册在 `:2397` 的 `kunit_test_suites(...)`）：

| suite | 定义处 | 它在钉住什么行为 |
|---|---|---|
| `clk_test_suite` | `:299` | 基本 get/rate |
| `clk_uncached_test_suite` | `:420` | `CLK_GET_RATE_NOCACHE` 的语义 |
| `clk_multiple_parents_mux_test_suite` | `:583-584` | 多父 mux 协商 |
| `clk_orphan_transparent_multiple_parent_mux_test_suite` | `:928` | 孤儿 + 多父 mux |
| `clk_single_parent_mux_test_suite` | `:1204-1205` | 单父 mux |
| `clk_orphan_transparent_single_parent_test_suite` | `:1291` | 孤儿 + 单父 |
| `clk_orphan_two_level_root_last_test_suite` | `:1420-1421` | 两级孤儿、根最后注册 |
| `clk_range_test_suite` | `:1756` | `clk_set_rate_range` |
| `clk_range_maximize_test_suite` | `:1944` | 范围上界策略 |
| `clk_range_minimize_test_suite` | `:2124` | 范围下界策略 |
| `clk_leaf_mux_set_rate_parent_test_suite` | `:2235` | `CLK_SET_RATE_PARENT` 穿透 |
| `clk_mux_notifier_test_suite` | `:2390` | 通知链 |

**12 个 suite 里有 3 个专门测孤儿**（`:928` / `:1291` / `:1420`），另外 3 个测频率范围。这个分布本身就是一条结论：**孤儿自愈和 `clk_set_rate_range` 是 clk 核心里最容易写错的两处**——和 `03-internals.md` §B4（孤儿修复）、§C2（`clk_set_rate_range` 的 FIXME 设计债，`drivers/clk/clk.c:2622-2629`）指向的是同一批代码。

### §16.2 `clk-gate_test.c` 的 5 个 suite

`clk_gate_register_test_suite`（`:125`，cases `:115`）、`clk_gate_test_suite`（`:232`，cases `:196`）、`clk_gate_test_invert_suite`（`:305`，cases `:276`，测 `CLK_GATE_SET_TO_DISABLE`）、`clk_gate_test_hiword_suite`（`:377`，cases `:349`，测 `CLK_GATE_HIWORD_MASK`）、`clk_gate_test_enabled_suite`（`:452`，cases `:444`）；注册在 `:457`。

**正好对应 §2.1 那三个 flag 里的两个**（`SET_TO_DISABLE` 和 `HIWORD_MASK`），`BIG_ENDIAN` 没有测试覆盖。

### §16.3 挂在 03 时间线的哪一点

不挂。它们用自己造的假时钟走完整的注册/调速流程，**是 03 全篇的一个可执行副本**。想验证 `03-internals.md` 里任何一条论断，最快的路径不是加 printk，是去这两个文件里找对应的 `KUNIT_ASSERT`。

---

## §17 汇总表

### §17.1 框架 × 职责 × 挂载点 × arm64 是否相关

| # | 子框架 | 核心职责 | 挂在 03 时间线的哪一点 | arm64 相关 |
|---|---|---|---|---|
| §2 | gate | 提供 `enable`/`disable`/`is_enabled` 三回调 + 极性/hiword 处理 | §A2 之前（驱动填 `init.ops`） | 但主要以 helper 形式复用 |
| §3 | divider | 值↔分频比换算、取整搜索、六个导出 helper | 同上 | `divider_recalc_rate`/`divider_get_val` 是最广的复用面 |
| §4 | mux | 父亲编号↔寄存器值换算；**搜索策略在核心里** | 同上 | |
| §5 | fixed-rate | 恒定频率 + 精度来源 | 同上；DT 侧走 §A1 两条分支 | 每块板子 DTS 都有 |
| §6 | fixed-factor | 定比换算；`set_rate` 是空函数 | 同上 | |
| §7 | fractional-divider | m/n 小数分频 + 可插拔近似算法 | 同上 | ☆☆ ops 表半私有，仅 `drivers/clk/` 内可用 |
| §8 | multiplier | 倍频；无注册函数 | 同上 | ☆☆☆ 仅 arm32 sunxi |
| §9 | composite | 运行期把三张子 ops 表拼成一张 | 同上，但多一步拼装 | ☆ |
| §10 | clkdev | 非 DT 的 `(dev_id, con_id)` 查找表 | §A4（提供方）+ §A5（消费者兜底） | ☆☆ arm64 上是遗留路径 |
| §11 | clk-bulk | `get`/`prepare`/`enable` 的带回滚循环 | §A5 + §A6 | |
| §12 | clk-devres | devm 生命周期；六个 `devm_clk_get*` 变体 | §A5 + §A6 + devres 链 | 消费者首选 |
| §13 | clk-conf | `assigned-clock-{parents,rates}` 落地 | §A4 末尾（`drivers/clk/clk.c:4934`） | |
| §14 | clk-gpio / clk-pwm | 用别的子系统当时钟源 | §A1 平台驱动分支 | ☆☆ 但暴露了 prepare/enable 的硬约束 |
| §15 | clk-scmi / clk-scpi | 固件代理时钟；两张 ops 表的两种挑法 | §A1 平台/总线驱动分支，**很晚** | 新 arm64 SoC 主流 |
| §16 | KUnit | 核心行为的可执行规格 | 不挂（自造假时钟走全流程） | ☆ |

### §17.2 每个子框架的全树使用统计（可重跑）

统计口径统一为：`grep -rn --include='*.c' -w <符号> .`，**排除 `drivers/clk/clk-*.c` 和 `drivers/clk/clk.c` 自身**（否则定义行、`EXPORT_SYMBOL` 行、同文件包装都会被算进去）。

| 符号 | 行 | 文件 |
|---|---|---|
| `clk_hw_register_fixed_factor` | 32 | 16 |
| `clk_hw_register_gate` | 29 | 13 |
| `clk_hw_register_fixed_rate` | 28 | 19 |
| `clk_hw_register_mux` | 22 | 9 |
| `clk_hw_register_divider` | 21 | 14 |
| `clk_hw_register_composite` | 12 | 9 |
| `devm_clk_hw_register_gate` | 3 | 1 |
| `devm_clk_hw_register_composite_pdata` | 1 | 1 |
| `clk_hw_register_fractional_divider` | **0** | **0** |
| `clk_hw_register_composite_pdata` | **0** | **0** |
| `clk_hw_register_multiplier` | **符号不存在** | — |

重跑命令：

```bash
for f in clk_hw_register_fixed_factor clk_hw_register_gate clk_hw_register_fixed_rate \
         clk_hw_register_mux clk_hw_register_divider clk_hw_register_composite \
         devm_clk_hw_register_gate devm_clk_hw_register_composite_pdata \
         clk_hw_register_fractional_divider clk_hw_register_composite_pdata \
         clk_hw_register_multiplier; do
  printf '%s -> 行 %s / 文件 %s\n' "$f" \
    "$(grep -rn --include='*.c' -w "$f" . | grep -vc '^./drivers/clk/clk-\|^./drivers/clk/clk\.c')" \
    "$(grep -rln --include='*.c' -w "$f" . | grep -vc '^./drivers/clk/clk-\|^./drivers/clk/clk\.c')"
done
```

**这张表和 §0.2 那张 helper 表必须一起读，否则会得出完全相反的结论。** 注册函数最多的 `clk_hw_register_fixed_factor` 才 16 个文件，而 helper `divider_get_val` 就有 16 个文件——**基本类型的"完整套餐"（结构体+ops+注册函数）在 arm64 上远不如它的"零件"（导出的算法函数）受欢迎**。

### §17.3 三个"为对称而存在、树内无直接使用者"的 API

自审时逐个 grep 出来的，列在这里以免下次又当成"我漏了"：

| 符号 | 声明处 | 树内直接使用者 |
|---|---|---|
| `divider_ro_determine_rate` | `include/linux/clk-provider.h` | 0（定义在 `drivers/clk/clk-divider.c:361`） |
| `divider_ro_round_rate_parent` | 同上 | 0（定义在 `drivers/clk/clk-divider.c:406`） |
| `clk_hw_register_composite_pdata` | 同上 | 0（定义在 `drivers/clk/clk-composite.c:365`，只被同文件 `:408` 的 devm 包装调用） |

它们不是死代码——都被导出且公共头文件有声明，树外驱动可以用。但**在本树里读到它们时不要去找调用例子，没有。**

---

## 读完本篇之后

| 下一步想知道的 | 去哪篇 |
|---|---|
| 这些 API 的完整签名和错误码 | `01-api.md` |
| `clk_ops` 每个回调的调用上下文和约束 | `02-datastructures.md` §4 |
| 注册/调速主流程逐段剖析 | `03-internals.md` |
| 怎么写一个提供方 / 消费者 | `05-usage.md` |
| `fixed-clock` / `assigned-clocks` 的 DTS 写法 | `06-devicetree.md` |
| debugfs / tracepoint / 故障分诊 | `07-debug.md` |
| 为什么要分 prepare 和 enable、孤儿是什么 | `08-feynman.md` |
| 本篇每个数字的取证与重跑记录 | `99-evidence.md` |
