---
title: 扩展机制
---

# 04 周边框架：helpers / devres / irq / coupler / dummy / fixed / 用户态消费者

> arm64-only。本篇的组织轴是「**一节一个独立框架，互不调用**」——
> 你可以只读你需要的那一节，不用从头读。
> 03 篇是"核心内部怎么跑"，本篇是"核心之外还有哪些层可以用"。

## §0 分诊：哪几个在 arm64 上真会碰到

| # | 框架 | 文件 | 行数 | Kconfig | arm64 相关性 |
|---|---|---|---|---|---|
| §1 | **regmap helpers** | `drivers/regulator/helpers.c` | 968 | 跟着 `CONFIG_REGULATOR` 恒编译 | **几乎每个 PMIC 驱动都用**。不了解它等于不会写驱动 |
| §2 | **devres（devm_ 系列）** | `drivers/regulator/devres.c` | 662 | 恒编译 | **新代码一律用 devm_ 版本**。16 个导出 |
| §3 | **irq helpers** | `drivers/regulator/irq_helpers.c` | 439 | 恒编译 | 只有带过压/过温中断的 PMIC 用。用者少但机制独特（含**主动关机**） |
| §4 | **coupler** | 在 `core.c` 里（无独立文件） | — | 恒编译 | 只有需要联动调压的 SoC 用（Tegra 等）。arm64 上罕见 |
| §5 | **dummy regulator** | `drivers/regulator/dummy.c` | 90 | 恒编译 | **每块 arm64 板子都在跑**，而且你大概不知道 |
| §6 | **fixed regulator** | `drivers/regulator/fixed.c` | 356 | `REGULATOR_FIXED_VOLTAGE` | **arm64 DTS 里最常见的调压器节点**（GPIO 控制的固定电压） |
| §7 | **fixed-helper** | `drivers/regulator/fixed-helper.c` | 61 | 恒编译 | ✗ 只给板级 C 代码用。**arm64 上没有使用者** |
| §8 | **userspace-consumer** | `drivers/regulator/userspace-consumer.c` | 181 | `REGULATOR_USERSPACE_CONSUMER` | 调试/特殊场景。让用户态开关某一路 |
| §9 | **virtual consumer** | `drivers/regulator/virtual.c` | 374 | `REGULATOR_VIRTUAL_CONSUMER` | **纯调试工具**，源码自己说了"仅供开发"。见 07 篇 §5 |
| — | **of_regulator** | `drivers/regulator/of_regulator.c` | 807 | `CONFIG_OF` | 完整内容在 **06 篇**，本篇不重复 |

**只有 §1 §2 §5 §6 是"每块板子都在跑"的**，其余四个按需。
如果时间有限，读 §1 §2 §5 §6 就够。

Makefile 的分组（`drivers/regulator/Makefile:6-11`）正好印证这张表：

```makefile
obj-$(CONFIG_REGULATOR) += core.o dummy.o fixed-helper.o helpers.o devres.o irq_helpers.o
obj-$(CONFIG_OF) += of_regulator.o
obj-$(CONFIG_REGULATOR_FIXED_VOLTAGE) += fixed.o
obj-$(CONFIG_REGULATOR_VIRTUAL_CONSUMER) += virtual.o
obj-$(CONFIG_REGULATOR_USERSPACE_CONSUMER) += userspace-consumer.o
```

`dummy.o` / `fixed-helper.o` / `helpers.o` / `devres.o` / `irq_helpers.o`
**没有自己的 Kconfig 项** —— 开了 `CONFIG_REGULATOR` 就都在。

## §1 regmap helpers：28 个函数替你写完寄存器操作

### §1.1 它挂在 03 时间线的哪一点

**不挂在时间线上任何一点** —— helpers.c 里的函数
**全都是被填进 `regulator_ops` 的回调**，
所以它们在 03 篇 Part A 的**每一个"调 `ops->xxx`"处**被间接执行：
`_regulator_do_enable` 的 `:2777`、
`_regulator_do_set_voltage` 的 `:3663`/`:3672`、
`_regulator_is_enabled` 的 `:3236` 等。

换个说法：**helpers.c 是 `ops` 表的现成填充物**。
你的驱动如果是"一个 regmap + 几个位域"这种最常见形态，
`ops` 里可以一行自己的代码都不写。

### §1.2 四类，28 个导出

头文件：`include/linux/regulator/driver.h`。全部 `EXPORT_SYMBOL_GPL`。

**(a) 开关类（3 个）** —— 直接填 `ops->enable/disable/is_enabled`

| 函数 | 说明 | 备注 |
|---|---|---|
| `regulator_is_enabled_regmap` | 读 `enable_reg`，按 `enable_mask` 判位 | `helpers.c:27`。`enable_is_inverted` 为真时反过来判 |
| `regulator_enable_regmap` | 往 `enable_reg` 写 `enable_val` | `:59` |
| `regulator_disable_regmap` | 写 `disable_val` | `:85` |

这三个用到的 `desc` 字段是同一组：
`enable_reg` / `enable_mask` / `enable_val` / `disable_val` / `enable_is_inverted`
（02 篇 §4.2）。**填了这组字段就能用这三个 helper**。

**(b) 选择子读写（4 个）** —— 填 `ops->get_voltage_sel` / `set_voltage_sel`

| 函数 | 说明 | 备注 |
|---|---|---|
| `regulator_get_voltage_sel_regmap` | 读 `vsel_reg` + `vsel_mask` + 右移 | `:235` |
| `regulator_set_voltage_sel_regmap` | 写 `vsel_reg`；若有 `apply_reg` 再写一次 `apply_bit` | `:261`。**两次寄存器写**的形态 |
| `regulator_get_voltage_sel_pickable_regmap` | 先读 `vsel_range_reg` 判在哪个 range，再读 `vsel_reg` | `:129`。给"分段选择子"硬件用 |
| `regulator_set_voltage_sel_pickable_regmap` | 同上，写两个寄存器 | `:173` |

`pickable` 的含义：**同一个 vsel 值在不同 range 下代表不同电压**，
所以必须先选 range。识别方法：`desc->vsel_range_reg` 非 0。

**(c) 电压 ↔ 选择子映射（9 个）** —— 填 `ops->map_voltage` / `list_voltage`

| 函数 | 适用硬件 | 备注 |
|---|---|---|
| `regulator_map_voltage_linear` | 单一线性：`min_uV + sel * uV_step` | `:361` |
| `regulator_map_voltage_linear_range` | 多段线性 | `:407`。见下面的 BUG_ON |
| `regulator_map_voltage_pickable_linear_range` | 多段 + 需选 range | `:456` |
| `regulator_map_voltage_iterate` | 兜底：**逐个 sel 调 `list_voltage` 试** | `:292`。O(n)，但任何硬件都能用 |
| `regulator_map_voltage_ascend` | 同上但假设单调递增，可提前退出 | `:330` |
| `regulator_list_voltage_linear` | 反向：sel → 电压 | `:548` |
| `regulator_list_voltage_linear_range` | | `:645` |
| `regulator_list_voltage_pickable_linear_range` | | `:564` |
| `regulator_list_voltage_table` | 查 `desc->volt_table[]` 数组 | `:662`。不规则电压表用这个 |

另有两个 `_desc_` 前缀的变体（`regulator_desc_list_voltage_linear` `:523`、
`regulator_desc_list_voltage_linear_range` `:617`）——
参数是 `const struct regulator_desc *` 而不是 `rdev`，
给"还没注册就要算电压"的场景用。

**`regulator_map_voltage_linear_range` 里有一个 `BUG_ON`**：

```c
/* helpers.c:416 */ if (!rdev->desc->n_linear_ranges) {
/* helpers.c:417 */         BUG_ON(!rdev->desc->n_linear_ranges);
/* helpers.c:418 */         return -EINVAL;
```

**驱动把这个函数填进 `ops` 却忘了填 `linear_ranges` ⇒ 内核 panic**，
不是返回错误。注意 `:416` 的 `if` 和 `:417` 的 `BUG_ON` 判据完全一样 ——
这是"先 BUG 再兜底"的写法，`:418` 那行在 `CONFIG_BUG=n` 时才有用。

这个函数还有一处值得看：`:430-436` 那段注释和代码 ——
`linear_range_get_selector_high` 找到 sel 之后
**再调 `list_voltage` 反算回电压验证是否还在 `[min,max]` 内**，
不在就继续试下一个 range。**map 和 list 必须自洽**，
否则这里会静默选到错的 range。

**(d) 杂项位域（12 个）** —— 各填一个 `ops` 回调

| 函数 | 填哪个回调 | 用到的 desc 字段 | 行 |
|---|---|---|---|
| `regulator_set_bypass_regmap` | `set_bypass` | `bypass_reg`/`bypass_mask`/`bypass_val_on`/`bypass_val_off` | `:685` |
| `regulator_get_bypass_regmap` | `get_bypass` | 同上；`bypass_val_on == 0` 时按 `bypass_mask` 全 1 判 | `:744` |
| `regulator_set_soft_start_regmap` | `set_soft_start` | `soft_start_reg`/`_mask`/`_val_on` | `:707` |
| `regulator_set_pull_down_regmap` | `set_pull_down` | `pull_down_reg`/`_mask`/`_val_on` | `:725` |
| `regulator_set_active_discharge_regmap` | `set_active_discharge` | `active_discharge_reg` 等 4 个 | `:770` |
| `regulator_set_current_limit_regmap` | `set_current_limit` | `csel_reg`/`csel_mask` + `curr_table[]` | `:797` |
| `regulator_get_current_limit_regmap` | `get_current_limit` | 同上 | `:849` |
| `regulator_set_ramp_delay_regmap` | `set_ramp_delay` | `ramp_reg`/`ramp_mask`/`ramp_delay_table[]` | `:946`。用 `find_closest_bigger`（`:905`）挑**不小于**请求值的档 |
| `regulator_bulk_set_supply_names` | 不是回调 | 批量填 `regulator_bulk_data[].supply` | `:882` |
| `regulator_is_equal` | 不是回调 | 判两个 `struct regulator *` 是否指向同一个 rdev | `:899` |

`regulator_is_equal`（`:899`）只有三行，判据是 `reg1->rdev == reg2->rdev` ——
**消费者手里的 `struct regulator *` 是每次 get 都新建的**（02 篇 §3.1），
所以直接比指针会错。

`regulator_set_ramp_delay_regmap` 用的 `find_closest_bigger`（`:905`）
是**向上取整**：找不到更大的档就用最大档。
这解释了 03 篇 §A6.3 那个五级 ramp_delay 优先级里"实际生效值可能比你设的大"。

### §1.3 这一层的边界

**helpers.c 里没有一个函数会拿锁** ——
它们全部在 `rdev->mutex` 已被核心持有的上下文里跑（03 篇 §B1.4）。
所以它们也**不能**被消费者直接调用。

它们也全都不做**约束检查** —— `valid_ops_mask` 那道闸门在核心里
（03 篇 §B2），helper 拿到调用就直接写寄存器。

## §2 devres：16 个 devm_ 版本

### §2.1 它挂在时间线的哪一点

devres 包装的是**注册期和 get 期**：
`devm_regulator_register` 对应 03 篇 §A1，
`devm_regulator_get` 对应 §A4。
**拆除路径全部由 driver core 在 `remove` 之后自动跑**，
所以 03 篇 §A8 那条 `regulator_unregister` 手动路径
在用 devm 的驱动里**根本不出现**。

### §2.2 四组，16 个导出

头文件：`include/linux/regulator/consumer.h`（消费者侧）和
`include/linux/regulator/driver.h`（提供者侧）。

**(a) 消费者获取（5 个）**

| 函数 | 对应非 devm 版本 | 行 |
|---|---|---|
| `devm_regulator_get` | `regulator_get` | `devres.c:55` |
| `devm_regulator_get_optional` | `regulator_get_optional` | `:146` |
| `devm_regulator_get_exclusive` | `regulator_get_exclusive` | `:71` |
| `devm_regulator_put` | **手动提前释放**（少用） | `:175` |
| `devm_regulator_register_notifier` / `devm_regulator_unregister_notifier` | | `:583` / `:610` |

**(b) get + enable 合一（2 个）** —— 这两个是 6.1 里的新写法

| 函数 | 说明 | 行 |
|---|---|---|
| `devm_regulator_get_enable` | get 之后立刻 enable，**不返回 `struct regulator *`** | `:130` |
| `devm_regulator_get_enable_optional` | 同上，但拿不到时用 dummy | `:114` |

**它们的返回值是 `int` 而不是指针** ——
适用于"我只要它一直开着，不需要再操作它"的驱动。
这类驱动过去要写 5 行（get + 判错 + enable + 判错 + 记住指针），
现在一行。代价是**你再也拿不到那个 `struct regulator *`**，
所以中途想调压/关掉的就不能用这两个。

**(c) 批量（4 个）**

| 函数 | 说明 | 行 |
|---|---|---|
| `devm_regulator_bulk_get` | 一次 get 多路 | `:226` |
| `devm_regulator_bulk_get_const` | 参数是 `const` 数组，内部拷一份 | `:254` |
| `devm_regulator_bulk_get_enable` | 批量 get + 批量 enable + 失败自动回滚 | `:359` |
| `devm_regulator_bulk_put` | | `:289` |

`_const` 变体存在的理由：`regulator_bulk_data` 的 `consumer` 字段
是**出参**，所以标准版本要求数组可写。
驱动里那份 supply 名字表通常想放在 `static const` 里 ——
`_const` 版本内部 `kmemdup` 一份解决这个矛盾。

**(d) 提供者侧（5 个）**

| 函数 | 说明 | 行 |
|---|---|---|
| `devm_regulator_register` | 注册 rdev | `:398` |
| `devm_regulator_register_supply_alias` | 注册一个 supply 别名 | `:460` |
| `devm_regulator_bulk_register_supply_alias` | 批量版 | `:525` |
| `devm_regulator_irq_helper` | irq helper 的 devm 版（见 §3） | `:662` |

### §2.3 一条容易忽略的规则

**`devm_regulator_get` 绑的是"你传进去的那个 `dev`"，
不是"调用它的那个驱动"。**
所以在 probe 里对**子设备**调 `devm_regulator_get(child_dev, ...)`，
释放时机跟着子设备走，不跟着你 —— 这是 use-after-free 的一个来源。

另有一条：devm 的释放顺序是**注册的逆序**。
如果你先 `devm_regulator_get` 再 `devm_clk_get`，
拆除时先放 clk 再放 regulator ——
通常正确，但如果你的硬件要求"先断电再停时钟"就反了。
**这种情况必须手动管，不能靠 devm。**

## §3 irq helpers：唯一会主动关机的一层

### §3.1 它挂在时间线的哪一点

**注册之后、任何时刻** —— 它是异步的中断路径，
和 03 篇 Part A 那条时间线**并行**。
唯一的交汇点是它会写 `rdev->cached_err`
（受 `rdev->err_lock` 保护，03 篇 §B1.1），
消费者通过 `regulator_get_error_flags`（01 篇 §2.6）读到。

### §3.2 三个导出 + 一个 devm 版

| 函数 | 说明 | 行 |
|---|---|---|
| `regulator_irq_helper` | 注册一个 IRQ → 通知链的桥 | `irq_helpers.c:338`，导出在 `:379` |
| `regulator_irq_helper_cancel` | 注销；会把你的 handle 置 NULL | `:390`，导出 `:402` |
| `regulator_irq_map_event_simple` | 给"一个中断只对应一种错误"的简单硬件用的现成 `map_event` | `:418`，导出 `:438` |
| `devm_regulator_irq_helper` | devm 版 | `devres.c:662` |

驱动要填的是 `struct regulator_irq_desc`（`include/linux/regulator/driver.h`），
关键回调三个：`map_event`（必填）、`renable`（可选）、`die`（可选）。

### §3.3 重试机制与 `hw_protection_shutdown`

`regulator_notifier_isr_work`（`:49`）是本子系统**最不寻常的一段代码**：

```
reread:                                                  [:64]
  if (d->fatal_cnt && h->retry_cnt > d->fatal_cnt) {     [:65]
      if (!d->die)                                       [:66]
          return hw_protection_shutdown(
                 "Regulator HW failure? - no IC recovery", ...)   [:67]
      ret = d->die(rid);                                 [:69]
      if (ret)                                           [:74]
          return hw_protection_shutdown(
                 "Regulator HW failure. IC recovery failed", ...)  [:75]
  }
  if (d->renable) {                                      [:85]
      ret = d->renable(rid);                             [:86]
      if (ret == REGULATOR_FAILED_RETRY) {               [:88]
          h->retry_cnt++;                                [:90]
          if (!d->reread_ms) goto reread;                [:91-92]  立刻重读
          tmo = d->reread_ms; goto reschedule;           [:94-95]
      }
      if (ret) { ... h->retry_cnt++; goto reschedule; }  [:98-118]
  }
  ... 通知各 rdev ...                                    [:128]
  h->retry_cnt = 0;                                      [:140]  成功就清零
reschedule:                                              [:147]
  if (!d->high_prio)  mod_delayed_work(system_wq, ...)   [:148-149]
  else                mod_delayed_work(system_highpri_wq, ...)  [:152]
```

**`hw_protection_shutdown` 是"内核主动关机"** ——
这是整个 regulator 子系统里**唯一**会终结系统的路径。
触发条件：`fatal_cnt` 设了、重试超过它、而且 `die` 回调
没有（`:66`）或者也失败了（`:74`）。

设计意图很清楚：**过压/过温这类故障，继续跑会烧硬件**，
所以"关机"比"报错继续"更安全。
但这意味着**驱动里 `fatal_cnt` 这个数字直接决定系统会不会突然断电** ——
填之前想清楚。

`:124` 那段注释解释了为什么成功路径不需要显式清中断：
"If problems are gone - good. If not - then the IRQ will fire again"。

`high_prio`（`:148`/`:152`）选的是 `system_wq` 还是
`system_highpri_wq` —— 过温这类要快的填 true。

### §3.4 `regulator_irq_map_event_simple`

`:418`，只有 20 行。kerneldoc（`:405-416`）明说了适用条件：
**中断状态寄存器里只有一个位、而且这个位的含义固定**。
不满足就自己写 `map_event`。

## §4 coupler：联动调压

**没有独立文件，全部在 `core.c` 里。**
机制部分已在 03 篇 §B4 讲完（注册期挂接、两条硬限制、运行期平衡），
本节只补"如果要自己写一个 coupler 该实现什么"。

### §4.1 挂在时间线的哪一点

`regulator_register` 中段：`regulator_init_coupling`（`core.c:5698`）
→ 尾段：`regulator_resolve_coupling`（`:5729`）。
运行期在 `regulator_balance_voltage`（`:4147`）被用。

### §4.2 三个回调

`struct regulator_coupler`（`include/linux/regulator/coupler.h`）：

| 回调 | 必填 | 说明 |
|---|---|---|
| `attach_regulator` | 是 | 三态返回：0 = 我管、`1` = 不是我的、`< 0` = 出错（03 篇 §B4.1） |
| `detach_regulator` | 否 | 拆除时调；`regulator_find_coupler` 的失败路径也会调（`:5341-5342`） |
| `balance_voltage` | 超过两路时必填 | 不填而又有 3+ 路耦合 ⇒ `-EPERM`（`:5322-5324`） |

注册用 `regulator_coupler_register`（`core.c:5299`），
必须在**被耦合的 rdev 注册之前**调（通常在
SoC 的 `arch_initcall` 或更早），
因为 `regulator_find_coupler` 只在 rdev 注册那一刻查一次。

**通用 coupler 在 `regulator_init` 里最先注册**（`:6207`），
而 `regulator_find_coupler` 倒序遍历（`:5319`）——
所以你后注册的 coupler 会先被试到。这个顺序是**故意的**，
注释在 `:5314-5318`。

### §4.3 arm64 上的实际情况

本树里的定制 coupler 都在 Tegra 相关代码里，
arm64 平台上**绝大多数板子走的是通用 coupler**，
而通用 coupler 只支持"两路、都 always-on"（03 篇 §B4.2）。
**实际含义：arm64 上你几乎不会遇到耦合调压。**
如果 DTS 里出现了 `regulator-coupled-with`，
先确认这两路都是 `regulator-always-on`。

## §5 dummy regulator：每块板子都在跑的那一路

### §5.1 它挂在时间线的哪一点

**比一切都早** —— `regulator_dummy_init`（`dummy.c:68`）
被 `regulator_init`（`core.c:6188`，`core_initcall`）调用。
所以它是**第一个注册的 rdev**。

### §5.2 全文 90 行，值得整块看

```c
/* dummy.c:24 */ static const struct regulator_init_data dummy_initdata = {
/* dummy.c:25 */         .constraints = {
/* dummy.c:26 */                 .always_on = 1,
/* dummy.c:27 */         },
/* dummy.c:28 */ };
/* dummy.c:30 */ static const struct regulator_ops dummy_ops;
/* dummy.c:32 */ static const struct regulator_desc dummy_desc = {
/* dummy.c:33 */         .name = "regulator-dummy",
/* dummy.c:34 */         .id = -1,
/* dummy.c:35 */         .type = REGULATOR_VOLTAGE,
/* dummy.c:36 */         .owner = THIS_MODULE,
/* dummy.c:37 */         .ops = &dummy_ops,
/* dummy.c:38 */ };
```

**`:30` 那行是全篇的关键**：`dummy_ops` 是一个
**没有初始化任何字段的 `static const`** ——
也就是**全零结构体，每个回调都是 NULL**。

所以：
- `_regulator_do_enable` 走到 `:2777` 的 `if (rdev->desc->ops->enable)`
  落空 → `:2782` 返回 **`-EINVAL`**，被 `:1685` 容忍（03 篇 §A3.3）
- `_regulator_is_enabled`（`core.c:3227`）没有 `is_enabled` 回调
  → 直接返回"已启用"
- 任何 `set_voltage` 都会被 `valid_ops_mask == 0` 挡在 `-EPERM`

`.constraints.always_on = 1`（`:26`）加上"没有 `enable` 回调"，
命中的正是 03 篇 §A3.2 那条自动改写（`core.c:1648-1656`）——
两条路都指向"永远是开的"。

注册走的是 `devm_regulator_register`（`:48-49`），
挂在一个 `platform_device_alloc("reg-dummy", -1)`（`:72`）上。
**所以 `/sys/class/regulator/` 里那个
`regulator-dummy` 是个真的 platform 设备**，
`dmesg` 里能看到 `reg-dummy` 的 probe。

导出的全局变量是 `dummy_regulator_rdev`（`:22`），
`core.c` 通过 `drivers/regulator/dummy.h` 引用它。

### §5.3 它带来的真实风险

见 03 篇 §C1.2 —— 这里只补**怎么确认自己中招了**：

```bash
dmesg | grep -i 'dummy regulator'
```

命中 `<设备名>: supply <名字> not found, using dummy regulator`
（`core.c:2230`）
就说明这一路是假的。**`/sys/class/regulator/` 里看不出来** ——
消费者那边的 `regulator` 结构指向的是 `regulator-dummy` 那个 rdev，
但 sysfs 里没有反向索引。

## §6 fixed regulator：arm64 DTS 里最常见的那个节点

### §6.1 它是什么

**一个"其实不能调"的调压器**：电压固定，
最多能用一个 GPIO 或一个 clock 开关它。
`compatible = "regulator-fixed"` 是 arm64 DTS 里
出现频率最高的 regulator 节点。

它是一个**普通的 platform 驱动**
（`regulator_fixed_voltage_driver`，`fixed.c:333`），
和 PMIC 驱动地位相同 —— 03 篇 Part A 那条时间线对它完全适用。

### §6.2 三种 compatible

`fixed_of_match[]`（`fixed.c:314-329`）：

| compatible | 开关方式 | `ops` | 备注 |
|---|---|---|---|
| `regulator-fixed` | GPIO（可选）或完全不可控 | `fixed_voltage_ops`（`:160-161`）**空结构体** | `:316`。最常见 |
| `regulator-fixed-clock` | 一个 clock 的 enable/disable | `fixed_voltage_clkenabled_ops`（`:163`） | `:320` |
| `regulator-fixed-domain` | 一个 power domain 的 performance state | `fixed_voltage_domain_ops`（`:169`） | `:324`。用 `required-opps` |

**`fixed_voltage_ops`（`:160-161`）是一个空的 `regulator_ops`** ——
和 dummy 的 `dummy_ops`（§5.2）一样全 NULL。
所以纯 `regulator-fixed`（无 clock 无 domain）的开关能力
**完全来自核心对 `ena_gpiod` 的处理**，不来自 `ops`。

`regulator-fixed-clock` 的 `ops` 是特殊的（`:164-165`）：

```c
/* fixed.c:164 */ .enable = reg_clock_enable,
/* fixed.c:165 */ .disable = reg_clock_disable,
```

`reg_clock_enable`（`:48`）里是
`clk_prepare_enable(priv->enable_clock)`（`:53`），
`reg_clock_disable`（`:62`）里是 `clk_disable_unprepare`（`:66`）。
**"打开这一路电"实际上是"使能一个时钟"** ——
这在电源门控由时钟树控制的 SoC 上很常见。
时钟拿不到时报 `Can't get enable-clock from devicetree` + 直接失败（`:217-218`）。

### §6.3 GPIO 那一路

`reg_fixed_voltage_probe`（`:175`）里：

```c
/* fixed.c:274 */ cfg.ena_gpiod = gpiod_get_optional(&pdev->dev, NULL, gflags);
```

`_optional` —— **DTS 里没有 `gpio` 属性也能 probe 成功**，
结果是一个完全不可控的固定电压。
拿到的 gpiod 通过 `regulator_config.ena_gpiod` 交给核心
（03 篇 §A1.2 的 `:5602` 那个"所有权移交"）。

**所以 `regulator-fixed` 节点有三种形态**，
DTS 里只差一个属性：

| DTS 里有 | 结果 | `valid_ops_mask` |
|---|---|---|
| 只有 `regulator-min/max-microvolt` | 完全不可控，等价于 dummy 但名字是你起的 | 仍然会有 `CHANGE_STATUS`（除非写了 `regulator-always-on`）—— 见下面的注 |
| 加 `gpio = <...>` | GPIO 可开关 | 和上一行一样 —— `CHANGE_STATUS` 只由"有没有写 `regulator-always-on`"决定（`of_regulator.c:130-131`，06 篇 §1.2） |
| `regulator-fixed-clock` + `clocks` | 时钟可开关 | 同上；但有真的 `enable` 回调（`fixed.c:164`） |

**注意最后一列是个陷阱**：`REGULATOR_CHANGE_STATUS`
**不看硬件有没有开关能力**，只看 DTS 里有没有 `regulator-always-on`
（`of_regulator.c:130-131`）。所以一个"完全不可控"的 `regulator-fixed`
照样会被授权 `regulator_enable` / `regulator_disable` ——
调用返回 0，但什么都没发生（`ops->enable` 为 NULL ⇒
`core.c:2782` 返回 `-EINVAL`，被 `:1685` 容忍）。
完整推演在 06 篇 §1.2。

另有一条：`fixed.c:137` 显式把 `apply_uV` 清成 0，
**撤销了 `of_regulator.c:110-111` 刚设上的那个 true** ——
固定电压不需要"启动时把电压写进硬件"这一步。

完整的属性清单和写法在 06 篇 §4。

## §7 fixed-helper：一个没有使用者的兼容层

`fixed-helper.c` 只有 61 行，只导出一个函数
`regulator_register_always_on`（`:30`）。

它做的事：`kzalloc` 一个 `fixed_regulator_data`（`:35`）,
填好 `cfg`（`:45-47`）和 `init_data`（`:49-51`），
把 `pdev.name` 设成 `"reg-fixed-voltage"`（`:53`），
然后 `platform_device_register`（`:58`）——
**手工造一个 fixed regulator 的 platform 设备**。

**arm64 上没有使用者。** 它是给"板级 C 代码"
（`arch/arm/mach-*/board-*.c` 那种年代的写法）留的口子。
DT 时代直接在 DTS 里写 `regulator-fixed` 节点就行（§6）。

它恒编译（Makefile `:6`）但零调用，是一段**死代码的活标本**。
已记入 99 §6。

## §8 userspace-consumer：让用户态开关一路电

### §8.1 定位

它是一个**消费者**，不是提供者 —— 名字里 consumer 是字面意思。
`compatible` 由平台数据或 DTS 给，
驱动是 `regulator_userspace_consumer_driver`（`userspace-consumer.c:169`），
用 `module_platform_driver`（`:177`）注册。

### §8.2 两个 sysfs 文件

| 文件 | 权限 | 说明 | 行 |
|---|---|---|---|
| `name` | 0444 | 显示这一组的名字 | `name_show` `:32`，`DEVICE_ATTR_RO` `:90` |
| `state` | 0644 | 读=当前状态；**写 = 开/关** | `state_show` `:40` / `state_store` `:51`，`DEVICE_ATTR_RW` `:91` |

`state_store`（`:51`）解析出布尔值之后调
`regulator_bulk_enable`（`:74`）或 `regulator_bulk_disable`（`:77`）。

**这是用户态能直接控制电源的唯一标准途径** ——
`/sys/class/regulator/` 下的属性全是只读的（07 篇 §3）。
但它只能开关，不能调压（调压要用 §9 的 virtual consumer）。

probe 里用 `devm_regulator_bulk_get`（`:125`），
如果平台数据说 `init_on` 就立刻 `regulator_bulk_enable`（`:137`）；
remove 时 `regulator_bulk_disable`（`:164`）。

## §9 virtual consumer：纯调试工具

**源码自己说了这是开发用的** —— probe 里有一个
`static bool warned`（`virtual.c:297`）+ 首次 probe 打警告的机制（`:300`）。

五个可写 sysfs 属性（`virtual.c:266-270`，都是 **0664**）：

| 文件 | 对应 API |
|---|---|
| `min_microvolts` / `max_microvolts` | `regulator_set_voltage` |
| `min_microamps` / `max_microamps` | `regulator_set_current_limit` |
| `mode` | `regulator_set_mode` |

`compatible = "regulator-virtual-consumer"`（`:287`）。

它的价值：**不写驱动就能试调压**。
用法和注意事项在 07 篇 §5.2 —— 尤其那条
"写了 min 还要写 max 才会真的生效"。

**生产内核不要开 `CONFIG_REGULATOR_VIRTUAL_CONSUMER`** ——
0664 权限的调压接口是一个明确的攻击面。

## §10 汇总：框架 × 职责 × 挂载点 × arm64

| 框架 | 职责一句话 | 挂在 03 时间线哪一点 | arm64 |
|---|---|---|---|
| **regmap helpers** | 替 PMIC 驱动写完 `ops` 里的寄存器操作 | 不在时间线上；在每个 `ops->xxx` 调用处被间接执行 | 必读 |
| **devres** | 把注册/get 挂到 `dev` 的生命期上 | 包装 §A1（register）和 §A4（get） | 必读 |
| **irq helpers** | IRQ → 通知链 + 重试 + 主动关机 | 异步，与时间线并行；写 `cached_err` | 按需 |
| **coupler** | 多路联动调压 | §A1 中段 `:5698` + 尾段 `:5729`；运行期 `:4147` | 罕见 |
| **dummy** | 缺 supply 时的兜底假 rdev | 比一切都早（`core_initcall` 里第一个注册） | 每块板 |
| **fixed** | 固定电压 + 可选 GPIO/clock 开关 | 普通 platform 驱动，走完整的 §A1 | 最常见 |
| **fixed-helper** | 板级 C 代码造 fixed 设备 | 同上，但 **零使用者** | ✗ |
| **userspace-consumer** | 用户态开关一路电（`state` 可写） | 消费者侧，走 §A4 + §A5 | 按需 |
| **virtual consumer** | 用户态调压/调模式（调试专用） | 消费者侧 | 仅调试 |

## §11 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 这些框架各自的完整 API 表 | `01-api.md`（§3 提供者 / §4 coupler） |
| `regulator_desc` 里那些 `*_reg` / `*_mask` 字段的全貌 | `02-datastructures.md` §4.2 |
| 核心内部是怎么调到这些 helper 的 | `03-internals.md` Part A |
| 锁、耦合、通知链的机制细节 | `03-internals.md` Part B |
| 照着 §1 §2 §6 写一个 PMIC 驱动 | `05-usage.md` |
| `regulator-fixed` 节点具体怎么写 | `06-devicetree.md` §4 |
| virtual / userspace consumer 怎么用来调试 | `07-debug.md` §5 |
| 「dummy 为什么让我以为电源正常」这类直觉纠偏 | `08-feynman.md` |
| 本篇每条论断的出处 | `99-evidence.md` |
