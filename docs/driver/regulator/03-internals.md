---
title: 内部实现
---

# 03 实现剖析：regulator 核心

> 基线 Linux 6.1.158（`f6e38ae624cf`），arm64-only。
> 全部行号指向 `drivers/regulator/core.c`（6310 行），
> 其他文件写全路径。

## §0 本篇怎么读

### §0.1 三条轴，各自独立

本篇只承诺**一条主轴**：Part A 是**时间线**（一个 rdev
从注册到注销都经历了什么）。另外两部分是横切内容，
章节号带 B / C 前缀，**引用时请连前缀一起写**。

| 轴 | 章节 | 组织方式 | 什么时候读 |
|---|---|---|---|
| **Part A** | §A1–§A8 | 按**时间顺序**：注册 → 解析 supply → 施加约束 → get → enable → 调压 → 收尾 → 注销 | 想知道"开机时发生了什么" |
| **Part B** | §B1–§B5 | 按**机制**：锁、仲裁算法、通知、耦合、suspend | 想知道"某个机制怎么实现的" |
| **Part C** | §C1–§C3 | 评价：设计取舍、源码里的设计债、arm64 上真正会走到的路径 | 想知道"这个设计好不好" |

### §0.2 分层图

```
  ┌────────────────────────────────────────────────────────────────┐
  │ 消费者驱动         mmc / uart / cpufreq / panel / camera        │
  │                    regulator_get / _enable / _set_voltage       │
  └───────────────────────────────┬────────────────────────────────┘
                                  │ consumer.h
  ┌───────────────────────────────▼────────────────────────────────┐
  │ 【仲裁层】 core.c 的 regulator_xxx（不带下划线）                 │
  │   · 加锁（ww_mutex，可能锁整条供电链）                          │
  │   · 查 constraints 授权 → -EPERM                               │
  │   · 记账到 regulator->voltage[] / enable_count                  │
  ├────────────────────────────────────────────────────────────────┤
  │ 【协商层】 _regulator_xxx（带下划线，假定锁已持有）              │
  │   · 引用计数：use_count / open_count / bypass_count             │
  │   · 递归上游：rdev->supply                                      │
  │   · 求所有消费者电压区间的交集                                   │
  │   · 耦合平衡                                                    │
  ├────────────────────────────────────────────────────────────────┤
  │ 【执行层】 _regulator_do_xxx                                    │
  │   · 时序：off_on_delay / enable_time / ramp_delay               │
  │   · ena_pin ? GPIO : ops->enable                               │
  │   · tracepoint 打点                                            │
  └───────────────────────────────┬────────────────────────────────┘
                                  │ driver.h / regulator_ops
  ┌───────────────────────────────▼────────────────────────────────┐
  │ PMIC 驱动          填 regulator_desc + regulator_ops            │
  │                    多数直接用 helpers.c 的 *_regmap             │
  └───────────────────────────────┬────────────────────────────────┘
                                  │ regmap / gpio
                             硬件寄存器
```

**三层的命名是有规律的**（02 篇 §9）：
`regulator_enable` → `_regulator_enable` → `_regulator_do_enable`。
**看到函数名里有几个下划线就知道它在第几层。**

### §0.3 一个 rdev 的生命周期时间线

```
core_initcall                             (core.c:6213)
  └─ regulator_init：建 class + debugfs 根
        │
        ▼
PMIC 驱动 probe
  └─ regulator_register() × N            (core.c:5508)  ── §A1
        ├─ 参数校验（6 道门）                             ── §A1.1
        ├─ 建 rdev + 解析 DT 得 init_data                ── §A1.2
        ├─ kmemdup constraints                          ── §A1.3
        ├─ [可能] 提前解析 supply                        ── §A2
        ├─ set_machine_constraints()   这里会写硬件   ── §A3
        ├─ regulator_init_coupling()                     ── §B4
        ├─ device_add() + debugfs                        ── §A1.4
        └─ 回头帮别人解析 supply / coupling
        │
        ▼
消费者驱动 probe
  └─ regulator_get()                    (core.c:2196)  ── §A4
        ├─ 三张表查找 → 找不到给 dummy
        ├─ 检查 exclusive / 耦合是否解析完
        ├─ regulator_resolve_supply()                    ── §A2
        └─ create_regulator() → struct regulator         (core.c:1868)
        │
        ▼
  └─ regulator_enable()                 (core.c:2969)  ── §A5
  └─ regulator_set_voltage()            (core.c:4191)  ── §A6
        │
        ▼
late_initcall_sync + 30 秒延迟工作      (core.c:6310)  ── §A7
  └─ 关掉所有"没人用且不是 always-on"的 regulator
        │
        ▼
（可选）regulator_unregister()          (core.c:5767)  ── §A8
```

**`§A7` 那个 30 秒是本子系统最容易吃惊的一个行为** ——
开机后半分钟，日志里会突然冒出一批 `<名字>: disabling`。
详见 §A7。

### §0.4 哪些内容不在本篇

| 你要找的 | 去哪 |
|---|---|
| API 清单和错误码语义 | `01-api.md` |
| 结构体字段逐个解释 | `02-datastructures.md` |
| coupler / irq helper / dummy / userspace-consumer 的**内部实现** | `04-extensions.md`（本篇只讲它们在时间线上的挂点） |
| helpers.c 里那 28 个函数各自怎么算 | `04-extensions.md` §5 |
| DT 属性 → constraints 字段的完整映射 | `06-devicetree.md` §1.1 |
| tracepoint / sysfs / debugfs 的**用法** | `07-debug.md`（本篇只说打点在哪一行） |

---

# Part A：时间线

## §A1 `regulator_register`：一路输出的诞生

入口 `core.c:5508`，函数体一直到 `:5758`，**250 行、五个错误标签**。
先看整体骨架：

```
regulator_register(dev, regulator_desc, cfg)
  │
  ├─ [A1.1] 参数校验 ────────── 失败 → goto rinse
  ├─ [A1.2] kzalloc rdev + 解析 DT ── 失败 → goto clean
  ├─ [A1.3] kmemdup constraints ──── 失败 → goto wash
  ├─ [A2]   可能提前解析 supply
  ├─ [A3]   set_machine_constraints ── 失败 → goto wash
  ├─         regulator_init_coupling ── 失败 → goto wash
  ├─         set_consumer_device_supply × N ── 失败 → goto unset_supplies
  ├─ [A1.4] device_add + debugfs
  └─         回头帮别人解析
```

**五个错误标签是倒序嵌套的**（`:5738` / `:5743` / `:5749` / `:5754`）：
`unset_supplies` → `wash` → `clean` → `rinse`。
这是内核标准的"清理到哪一步"写法，**从下往上读就是完整的资源清单**。

### §A1.1 六道参数校验门

| 门 | 行 | 条件 | 返回 |
|---|---|---|---|
| 1 | `:5521` | `cfg == NULL` | `-EINVAL` |
| 2 | `:5525-5527` | `regulator_desc == NULL` | `-EINVAL` |
| 3 | `:5530` | `!dev \|\| !cfg->dev` | **只 `WARN_ON`，不返回错误** |
| 4 | `:5532-5534` | `desc->name == NULL \|\| desc->ops == NULL` | `-EINVAL` |
| 5 | `:5537-5540` | `type` 既不是 `REGULATOR_VOLTAGE` 也不是 `REGULATOR_CURRENT` | `-EINVAL` |
| 6 | `:5550-5558` | 实现了 `get_voltage_sel`/`set_voltage_sel` 却 `n_voltages == 0` | `-EINVAL` |

**第 3 道门只 warn 不拦** —— `dev` 为 NULL 时后面
`dev_get_regmap(dev, NULL)`（`:5616`）会直接解引用。
这是一个**真实存在的空指针路径**，靠"没人这么写"来保证。

另外两条只 `WARN_ON` 的（`:5544` / `:5546`）：
同时实现了 `get_voltage` 和 `get_voltage_sel`（或两个 setter）。
**警告但允许**，运行期以不带 `_sel` 的那个为准（§A6.3）。

### §A1.2 建 rdev 与解析 DT

```c
/* :5561 */ rdev = kzalloc(sizeof(struct regulator_dev), GFP_KERNEL);
/* :5566 */ device_initialize(&rdev->dev);
/* :5568 */ rdev->dev.class = &regulator_class;
/* :5569 */ spin_lock_init(&rdev->err_lock);
```

`device_initialize` 和 `device_add`（`:5721`）**隔了 160 行** ——
中间所有失败路径都靠 `put_device(&rdev->dev)`（`:5753`）释放，
不能 `kfree`。这是 kobject 生命期的标准写法。

DT 解析是**一次调用拿三样东西**：

```c
/* :5581-5582 */ init_data = regulator_of_get_init_data(dev, regulator_desc, config,
                                                       &rdev->dev.of_node);
```

| 拿到什么 | 去哪 |
|---|---|
| `init_data`（含 constraints） | 返回值 |
| `rdev->dev.of_node` | 第四个参数是**出参** |
| `config->ena_gpiod` | `:5602` 把它挪进 cfg（DT 里写了 `gpio` 属性时） |

实现在 `drivers/regulator/of_regulator.c:512`。
它返回 `-EPROBE_DEFER` 时（`:5589-5591`）直接 `goto clean` ——
**DT 里的 supply 还没就绪，整个注册重来**。

没有 DT 时走 `:5604-5606`：直接用 `cfg->init_data`
和 `cfg->of_node`。arm64 上基本不走这条。

后面一段是纯赋值（`:5609-5632`），值得注意的三条：

| 行 | 内容 | |
|---|---|---|
| `:5609` | `ww_mutex_init(&rdev->mutex, &regulator_ww_class)` | 锁在这里才初始化，之前的失败路径不能加锁 |
| `:5613-5618` | regmap 三级回退：`cfg->regmap` → `dev_get_regmap(dev)` → `dev_get_regmap(dev->parent)` | **驱动不填 regmap 也可能自动拿到** —— 这是 i2c PMIC 能直接用 `*_regmap` 助手的原因 |
| `:5624-5627` | supply 名字两级回退：`init_data->supply_regulator` → `desc->supply_name` | DT 里写的优先于驱动写死的 |

### §A1.3 约束的来源

```c
/* :5635-5641 */
if (init_data)
	rdev->constraints = kmemdup(&init_data->constraints, ...);
else
	rdev->constraints = kzalloc(sizeof(*rdev->constraints), ...);
```

**`kmemdup` 而不是存指针** —— 和 `desc`（只存指针）正好相反。
原因是 `constraints` 后面会被核心**改写**：
比如 `:1648-1657` 会自动把 `always_on` 改成 true（§A3.2）。
02 篇 §1.0 第二组那张对比表的"生命期"一行说的就是这件事。

没有 `init_data` 时是 **kzalloc 一份全零约束** ——
不是 NULL。所以 `regulator_ops_is_valid` 里
"no constraints" 那条分支（`:118` 起）在正常注册流程里**打不到**，
它防的是别的路径。全零约束的效果是 `valid_ops_mask == 0`，
**什么都不许改**。

### §A1.4 收尾：`is_switch` 与两次回头

```c
/* :5716-5719 */
if (!rdev->desc->ops->get_voltage &&
    !rdev->desc->ops->list_voltage &&
    !rdev->desc->fixed_uV)
	rdev->is_switch = true;
```

**`is_switch` 是推断出来的，不是驱动声明的**：
读不出电压、列不出电压、也没有固定电压 ⇒ 它只是个开关。
07 篇 §3 里 sysfs 的 `type` 属性会反映这个判断。

`device_add(&rdev->dev)`（`:5721`）之后 sysfs 才出现，
紧跟着 `rdev_init_debugfs(rdev)`（`:5725`）。

**最后两步是"回头帮别人"**，很容易忽略：

```c
/* :5727-5730 */  regulator_resolve_coupling(rdev);        // 我来了，帮耦合伙伴凑数
/* :5732-5734 */  class_for_each_device(&regulator_class, NULL, NULL,
                                       regulator_register_resolve_supply);
```

`:5733` 是**遍历所有已注册 rdev，看谁的 supply 现在能解析了**。
于是"注册一路新的 regulator"会触发**全局的一次重扫**。
一颗 20 路的 PMIC 注册完，这个 O(N²) 的重扫跑了 20 遍。

## §A2 `regulator_resolve_supply`：找到我的上游

入口 `core.c:2069`。**regulator 自己也要用电**，
它的上游也是一个 regulator（02 篇 §4.1 的 `supply` 字段）。

```
regulator_resolve_supply(rdev)
  ├─ :2077  没有 supply_name → return 0        不需要上游
  ├─ :2081  已经解析过 → return 0
  ├─         regulator_dev_lookup(...)
  │    ├─ :2089  -EPROBE_DEFER → 原样返回
  │    ├─ :2092-2102  have_full_constraints()  arm64 恒真
  │    │       ├─ 拿到 dummy → 用它
  │    │       └─ dummy 还没就绪 → -EPROBE_DEFER (:2095)
  │    │       └─ 否则 dev_err "Failed to resolve %s-supply for %s" + -EPROBE_DEFER (:2100-2102)
  │    └─ :2107-2116  解析到自己 → dev_err "resolved to itself" → 改用 dummy
  ├─ :2128-2131  上游的 parent 还没 bind → -EPROBE_DEFER
  ├─ :2137  递归解析上游的上游
  └─         set_supply() + 建 device_link
```

**两条最值得记住的**：

1. **"resolved to itself"**（`:2108`）是一个真实会发生的 DTS 错误 ——
   某一路的 `xxx-supply` 指向了自己。核心**不会拒绝注册**，
   而是打一条 `dev_err` 然后**换成 dummy 继续跑**（`:2110-2116`）。
   症状：这一路的电压设置全部无效，但没有任何"失败"。
2. **上游 parent 没 bind 也要 defer**（`:2128-2131`）：
   `device_is_bound(r->dev.parent)` 为假就 `-EPROBE_DEFER`。
   这解释了为什么 PMIC 驱动的 probe 顺序会影响一大片设备 ——
   **不是"regulator 没注册"，而是"注册了但它的 parent 驱动还没 probe 完"**。

递归（`:2137`）意味着**整条供电链会被一次性解析到根**。
一条 DCDC → LDO → 消费者 的链上，中间任何一环 defer，
整条都 defer。

## §A3 `set_machine_constraints`：注册期唯一会写硬件的地方

入口 `core.c:1471`，到 `:1698`。**这是本子系统最反直觉的一段代码** ——
名字听起来是"设置软件约束"，实际上它**按约束逐项去写硬件寄存器**。

### §A3.1 十一个动作，按顺序

| # | 行 | 条件 | 动作 | 失败怎么办 |
|---|---|---|---|---|
| 1 | `:1476` | 总是 | `machine_constraints_voltage`（`core.c:1246`）：校验电压范围，`apply_uV` 时**写电压** | return |
| 2 | `:1480` | 总是 | `machine_constraints_current`（`core.c:1383`）：校验电流范围 | return |
| 3 | `:1484-1490` | `ilim_uA` 且有回调 | 写输入电流限制 | `rdev_err "failed to set input limit"` + return |
| 4 | `:1494-1500` | `initial_state` | `suspend_set_initial_state`（`core.c:1148`） | `rdev_err "failed to set suspend state"` + return |
| 5 | `:1502-1512` | `initial_mode` | 写工作模式 | **没有 `set_mode` 回调直接 `rdev_err "no set_mode operation"`** |
| 6 | `:1513-1518` | 没有 `initial_mode` 但有 `system_load` | `drms_uA_update`（`core.c:1015`）自动挑模式 | 忽略返回值 |
| 7 | `:1521-1528` | `ramp_delay` 或 `ramp_disable` | 写 ramp 寄存器 | `rdev_err` + return |
| 8 | `:1530-1536` | `pull_down` | 写下拉电阻 | `rdev_err` + return |
| 9 | `:1538-1544` | `soft_start` | 写软启动 | `rdev_err` + return |
| 10 | `:1560-1572` | `over_current_protection` | 写硬保护（`prot` 那一档） | `rdev_err` + return |
| 11 | `:1574-1628` | 四个 `*_detection` 标志 | `handle_notify_limits`（`core.c:1434`）×4 | **`-EOPNOTSUPP` 只 warn，其他错误 return** |
| 12 | `:1630-1638` | `active_discharge` | 写主动放电 | `rdev_err` + return |

**第 5 项和第 11 项的处理方式不一样，这是有意的**：
`initial_mode` 写不进去是**配置错误**（DTS 要求了硬件不支持的东西），
而 `*_detection` 写不进去可能只是**这颗芯片没有这个保护功能** ——
所以后者的 `-EOPNOTSUPP` 降级成 `rdev_warn`（`:1584` / `:1598` /
`:1612` / `:1626`）继续跑。

第 6 项那个 `else if` 很容易漏：**DTS 里同时写了
`regulator-initial-mode` 和 `regulator-system-load` 时，
后者被忽略**。06 篇 §4 会再提。

### §A3.2 `always_on` 会被核心自动改写

```c
/* :1648-1657 */
if (!rdev->ena_pin && !ops->enable) {
	if (rdev->supply_name && !rdev->supply)
		return -EPROBE_DEFER;
	if (rdev->supply)
		rdev->constraints->always_on =
			rdev->supply->rdev->constraints->always_on;
	else
		rdev->constraints->always_on = true;
}
```

**既没有 enable GPIO、也没有 `ops->enable` 的 regulator
被核心强行标成 `always_on`** —— 逻辑很直白：**关不掉的东西就是常开的**。
如果它有上游，就**继承上游的 `always_on`**。

这是 §A1.3 里"约束必须 kmemdup"的直接原因：
核心真的会改写它。

### §A3.3 注册期就打开电源

```c
/* :1662-1691 */
if (rdev->constraints->always_on || rdev->constraints->boot_on) {
	if (rdev->supply_name && !rdev->supply)
		return -EPROBE_DEFER;                    /* :1667 */
	if (rdev->supply && (always_on || ...))
		ret = regulator_enable(rdev->supply);     /* :1676 */
	ret = _regulator_do_enable(rdev);             /* :1684 */
	if (ret < 0 && ret != -EINVAL) { ... return ret; }
}
```

**`regulator-always-on` 或 `regulator-boot-on` 的实现就在这里**：
注册的时候直接开。**不需要任何消费者**。

三个细节：

1. `:1676` 先开上游 —— 而且用的是**消费者 API**
   `regulator_enable`（不是 `_regulator_do_enable`），
   所以上游的 `use_count` 真的加了一次
2. `:1685` 容忍 `-EINVAL`：`_regulator_do_enable` 在
   "既没 ena_pin 又没 `ops->enable`" 时返回 `-EINVAL`（`:2782`），
   这种情况**不算失败**（本来就打不开）
3. `:1692-1693` 的 `else if (rdev->desc->off_on_delay)` 分支：
   不需要开机就开的、但有 `off_on_delay` 的，
   要把 `last_off` 初始化成当前时刻

最后 `print_constraints(rdev)`（`:1696`）—— 这是
`dmesg` 里那行 `<名字>: 3300 mV` 的来源（07 篇 §2.1）。

### §A3.4 `set_machine_constraints` 会被调两次

回到 `regulator_register`：

```c
/* :5677 */ ret = set_machine_constraints(rdev);
/* :5678 */ if (ret == -EPROBE_DEFER && !resolved_early) {
/* :5682 */     /* FIXME: this currently triggers a chicken-and-egg problem
/* :5683 */      * when creating -SUPPLY symlink in sysfs to a regulator
/* :5684 */      * that is just being created
/* :5685 */      */
/* :5688 */     ret = regulator_resolve_supply(rdev);
/* :5690 */     if (!ret) ret = set_machine_constraints(rdev);
```

**`-EPROBE_DEFER` 时它会先去解析 supply 再重试一次**。
`:5682-5685` 的 `FIXME` 是 `core.c` 里**唯一一条**设计债注释
（见 §C2），
它承认这条重试路径和 sysfs symlink 创建之间有鸡生蛋问题。

判据是 `resolved_early` 这个局部标志 —— §A1 骨架里
"[A2] 可能提前解析 supply"（`:5647-5651`）那一步如果已经跑过，
这里就不重试。

## §A4 `regulator_get`：消费者拿到句柄

真正的实现是 `_regulator_get`（`core.c:2196`，声明在
`drivers/regulator/internal.h:123-124`）。三个对外入口
（`regulator_get` / `_get_exclusive` / `_get_optional`）
只是传不同的 `enum regulator_get_type`（`internal.h:116-121`）。

### §A4.1 七道门

| # | 行 | 检查 | 失败返回 |
|---|---|---|---|
| 1 | `:2198-2200` | `id == NULL` | `-EINVAL` |
| 2 | `:2203-2242` | 查找 + dummy 兜底（见下） | `-ENODEV` / `-EPROBE_DEFER` |
| 3 | `:2244-2247` | `rdev->exclusive` 非 0 | `-EPERM` |
| 4 | `:2250-2253` | 要独占但 `open_count` 非 0 | **`-EBUSY`** |
| 5 | `:2257-2263` | 耦合还没解析完（`n_resolved != n_coupled`） | `-EPROBE_DEFER` |
| 6 | `:2266-2270` | `regulator_resolve_supply` 失败 | 原样返回 |
| 7 | `:2273-2276` | `try_module_get(rdev->owner)` 失败 | `-EPROBE_DEFER` |

**第 3 门和第 4 门返回不同的错误码，含义也不同**：
`-EPERM` = "已经被别人独占了，你别想要"；
`-EBUSY` = "你想独占，但已经有非独占的持有者了"。
**两个方向的冲突。**

第 7 门用 `try_module_get` 失败返回 `-EPROBE_DEFER` 而不是
`-ENODEV` —— 意思是"PMIC 模块正在卸载中，等会儿再来"。

### §A4.2 dummy 兜底的三条分支

这段（`:2203-2242`）是 01 篇 §5.2 那条结论的实现：

```
regulator_dev_lookup(dev, id)
  │
  ├─ 返回非 -ENODEV 的错误 → :2211-2212 原样返回（含 -EPROBE_DEFER）
  │
  └─ 返回 -ENODEV
       ├─ :2214  !have_full_constraints()
       │      → dev_warn "incomplete constraints, dummy supplies not allowed"
       │      → -ENODEV                                        (:2217)
       │
       └─ have_full_constraints()   arm64 上恒真
            ├─ NORMAL_GET    (:2221) → rdev = dummy_regulator_rdev
            │                            NULL 则 -EPROBE_DEFER  (:2229)
            │                            dev_warn "supply %s not found,
            │                                      using dummy regulator" (:2230)
            ├─ EXCLUSIVE_GET (:2234) → dev_warn "dummy supplies not allowed
            │                                    for exclusive requests"
            │                          ↓ fallthrough
            └─ 其他（OPTIONAL_GET）    → -ENODEV                 (:2240)
```

**`switch` 的 `EXCLUSIVE_GET` 分支是 fallthrough 到 `default`** ——
所以它既打了独占专用的那条 warn，又走了 `-ENODEV`。
`OPTIONAL_GET` 只有 `-ENODEV`，一条日志都没有。

**这就是"DTS 漏写 supply"三种不同表现的全部来源**（01 篇 §5.2 那张表）。

### §A4.3 独占 get 会读硬件

```c
/* :2289 */ rdev->open_count++;
/* :2290-2298 */
if (get_type == EXCLUSIVE_GET) {
	rdev->exclusive = 1;
	ret = _regulator_is_enabled(rdev);
	if (ret > 0)
		rdev->use_count = 1;
	else
		rdev->use_count = 0;
}
```

**独占 get 会把 `use_count` 直接设成硬件的实际状态** ——
这是全核心**唯一一处不通过 enable/disable 就改 `use_count`** 的地方。
理由：独占者需要"我 get 到的时候它是开着的"这个事实被记进账里，
否则它调一次 `regulator_disable` 会 underflow。

普通 get 不做这件事，所以**普通消费者 get 到一个已经开着的
regulator 时，`use_count` 里没有它的份** ——
它必须自己 `enable` 一次才算。

`create_regulator`（`core.c:1868`）在锁内调用（`:2279-2281`），
负责 kzalloc `struct regulator`、串进 `consumer_list`、建 sysfs 属性和 debugfs。

## §A5 `regulator_enable`：三层各自干什么

三层的分工在这条路径上看得最清楚：

```
regulator_enable(regulator)                              [:2969]
  ├─ regulator_lock_dependent(rdev, &ww_ctx)             [:2975]  ← 锁整条供电链
  ├─ _regulator_enable(regulator)                        [:2976]
  └─ regulator_unlock_dependent(rdev, &ww_ctx)           [:2977]
```

`regulator_enable` 本体**只有三行**（`:2975-2977`）——
所有逻辑都在 `_regulator_enable`（`:2893`）里。

### §A5.1 `_regulator_enable` 的五步

```
_regulator_enable(regulator)                             [:2893]
  │  lockdep_assert_held_once(&rdev->mutex.base)         [:2898]  断言锁已持有
  │
  ├─ 1. use_count == 0 && 有上游 → 递归 _regulator_enable(rdev->supply)  [:2900-2904]
  │
  ├─ 2. n_coupled > 1 → regulator_balance_voltage()      [:2907-2911]
  │        失败 → goto err_disable_supply
  │
  ├─ 3. _regulator_handle_consumer_enable(regulator)     [:2913]  ← 消费者侧记账
  │        失败 → goto err_disable_supply
  │
  ├─ 4. if (rdev->use_count == 0) {                      [:2917]
  │        ret = _regulator_is_enabled(rdev)             [:2922]  先问硬件
  │        ├─ ret == -EINVAL 或 0（读不出 / 确实是关的）  [:2923]
  │        │     ├─ !regulator_ops_is_valid(CHANGE_STATUS) → -EPERM  [:2924-2928]
  │        │     ├─ _regulator_do_enable(rdev)           [:2930]  ← 唯一碰硬件
  │        │     └─ _notifier_call_chain(EVENT_ENABLE)   [:2934]
  │        ├─ ret < 0（别的错误）→ rdev_err "is_enabled() failed" [:2936-2939]
  │        └─ ret > 0（已经开着）→ 直接 fallthrough      [:2940]
  │     }
  │
  └─ 5. if (regulator->enable_count == 1) rdev->use_count++  [:2943-2944]
```

**第 4 步先读硬件，不是先看账本**（`:2922`）。
注释（`:2918-2921`）说明了原因：
"The regulator may already be enabled if it's not switchable or was left on" ——
**bootloader 留下的状态、或者根本关不掉的一路，都不需要再 enable 一次**。
`:2940` 那句 `/* Fallthrough on positive return values - already enabled */`
是这条设计的落款。

**第 5 步的条件是 `regulator->enable_count == 1`，不是无条件加一**：
同一个消费者 enable 十次，`rdev->use_count` **只涨一次**。
所以 02 篇 §4.1(b) 那张表里 `use_count` 数的是"有几个消费者要它开"，
`regulator->enable_count` 数的是"这个消费者要了几次"。

两个错误标签（`:2948` / `:2951`）是倒序回退：
先撤消费者记账，再关上游。

### §A5.2 `_regulator_do_enable`：唯一真的碰硬件

入口 `core.c:2744`。**这是全子系统唯一会改变电源状态的函数**
（除了 `_regulator_do_disable`，`:2983`）。

| 步 | 行 | 内容 |
|---|---|---|
| 1 | `:2749-2754` | `_regulator_get_enable_time`；失败只 `rdev_warn "enable_time() failed"` 然后 delay 当 0 |
| 2 | `:2757` | `trace_regulator_enable` 打点 |
| 3 | `:2759-2768` | `off_on_delay`：`ktime_add_us(rdev->last_off, ...)` 算出"最早可以再开的时刻"，还没到就 `_regulator_delay_helper` 等 |
| 4 | `:2770-2775` | `if (rdev->ena_pin)` → `regulator_ena_gpio_ctrl(rdev, true)`（`:2645`） |
| 5 | `:2777-2780` | `else if (ops->enable)` → `ops->enable(rdev)` |
| 6 | `:2782` | `else return -EINVAL` —— 两个都没有 |
| 7 | `:2789` | `trace_regulator_enable_delay` 打点 |
| 8 | `:2791-2818` | `poll_enabled_time` 轮询等硬件真的起来 |
| 9 | `:2820` | 否则死等 `enable_time` 微秒 |
| 10 | `:2823` | `trace_regulator_enable_complete` 打点 |

**第 3 步是"关了之后不能马上开"的实现** ——
`last_off` 在 `_regulator_do_disable`（`:3003-3004`）里记录。
这是给需要放电时间的 LDO 用的。

**第 8 步是唯一"不信驱动、去读回来确认"的地方**。
条件是 `desc->poll_enabled_time` 非 0，做法是每隔
`poll_enabled_time` 微秒调一次 `_regulator_check_status_enabled`
（`:2725` / 调用点 `:2804`）或 `ops->is_enabled`（`:2809`），
累计超过 `enable_time` 就 `rdev_err "Enabled check timed out"`
+ `-ETIMEDOUT`（`:2815-2817`）。
注释 `:2791-2796` 解释了它存在的理由。
01 篇 §5.1 里 `-ETIMEDOUT` 的唯一来源就是这一行。

三个 tracepoint 分别夹住"下命令前 / 命令下完 / 延时结束"，
所以 `enable` 的耗时可以拆成"硬件响应"和"我们等的时间"两段（07 篇 §4.2）。

### §A5.3 关的路径不对称

`regulator_disable`（`:3079`）同样是三行壳（`:3085-3087`），
但 `_regulator_disable`（`:3012`）和 enable **不是镜像**：

| 差异 | enable | disable |
|---|---|---|
| 记账失衡 | `_regulator_handle_consumer_enable` 里加 | `WARN(enable_count == 0, "unbalanced disables for %s")` + `-EIO`（`:3019-3021`） |
| 授权不足 | `-EPERM`（`:2926`） | **静默跳过**：`:3030` 的 `if` 不成立就什么都不做，`ret` 保持 0 |
| `always_on` | 无关 | `:3027` 直接跳过关电动作 |
| 通知 | 只有 `EVENT_ENABLE`（`:2934`） | `PRE_DISABLE`（`:3031`）→ 失败给 `ABORT_DISABLE`（`:3040`）→ 成功给 `DISABLE`（`:3045`） |
| 上游 | 递归 enable | `:3061-3062` 递归 disable |

**最需要记住的一条：`regulator_disable` 在"没权限关"和
"always_on"两种情况下都返回 0**。
它的语义是"我不再需要它了"，**不是"把它关掉"**。
真要强关得用 `regulator_force_disable`（`:3128`，
它绕过 `use_count` 和授权检查，走 `_regulator_force_disable` `:3094`）。

`PRE_DISABLE` 通知的返回值被检查（`:3034-3035`）——
监听者可以用 `NOTIFY_STOP_MASK` **否决这次关电**，返回 `-EINVAL`。
这是全子系统唯一一处通知能改变行为的地方。

还有一条延迟关电路径：`regulator_disable_deferred`（`:3198` 起的
kerneldoc）把计数记在 `regulator->deferred_disables`，
由 `regulator_disable_work`（`:3155`）批量补做（`:3183-3188`）。
`:3172` 的 `cancel_delayed_work` + 注释（`:3166-3171`）
是为了避免同一个 work 排两次。

## §A6 `regulator_set_voltage`：先记账，再协商

**这是本子系统最反直觉的第二处**：
`regulator_set_voltage` 返回 0 **不代表硬件是你要的那个值**。

```
regulator_set_voltage(regulator, min_uV, max_uV)         [:4191]
  ├─ regulator_lock_dependent()                          [:4196]
  └─ regulator_set_voltage_unlocked(..., PM_SUSPEND_ON)  [:4198]
        │
        ├─ 1. 和上次范围完全一样 → goto out（ret = 0）    [:3775-3776]
        │
        ├─ 2. 不许改电压，但当前值落在你的区间内       [:3782-3789]
        │        → 只记账，返回 0        （"你要的我已经有了"）
        │
        ├─ 3. set_voltage 和 set_voltage_sel 都没有 → -EINVAL  [:3792-3796]
        │
        ├─ 4. regulator_check_voltage(rdev, &min_uV, &max_uV)  [:3799]
        │        两个参数都是**出参**，会被约束夹紧
        │
        ├─ 5. 先把范围记进 regulator->voltage[state]  [:3804-3807]
        │        （旧值存在局部变量里备回滚）
        │
        └─ 6. regulator_balance_voltage(rdev, state)      [:3810]
                 失败 → 回滚记账                          [:3811-3814]
```

**第 2 步是"DTS 写死了电压、消费者还去 set"这个常见组合的出口** ——
只要你要的区间**包含**当前值就返回成功。
所以 `regulator_set_voltage` 成功的含义是
**"你的要求已被记录，且不与现状冲突"**。

**第 5 步在第 6 步之前** —— 记账先于执行。
理由见第 6 步：真正决定电压的是**所有消费者区间的交集**
（`regulator_check_consumers`，`:527`），
所以必须先把自己的要求放进账本，协商才看得见它。
失败时靠 `:3812-3813` 手工回滚。

### §A6.1 `regulator_balance_voltage`：协商的入口

```
regulator_balance_voltage(rdev, state)                   [:4147]
  ├─ state != PM_SUSPEND_ON → skip_coupled = true        [:4158-4159]
  ├─ n_resolved < n_coupled → rdev_err + -EPERM          [:4161-4164]
  ├─ 有自定义 coupler → coupler->balance_voltage()        [:4167-4168]
  └─ regulator_do_balance_voltage(rdev, state, skip)     [:4170]
```

**不耦合的 regulator 也走这条路** ——
`n_coupled == 1` 时它只是"给一个 rdev 定电压"的普通路径。
注释 `:3809` 说得很直白：
"for not coupled regulators this will just set the voltage"。

`regulator_do_balance_voltage`（`:4064`）是一个
**贪心迭代**：每轮在所有耦合成员里找"当前值离最优值差得最多"的那一个
（`:4094-4124`，靠 `regulator_get_optimal_voltage` `:3921` 算最优值），
调一次 `regulator_set_voltage_rdev`（`:4132`）把它挪过去，
然后重新算下一轮，直到没有可改的（`:4127-4130`）。

**这是一个 O(n²) 的循环调压过程** ——
两路耦合的 CPU 电源要从 A 状态挪到 B 状态，
中间可能真的写了四五次寄存器。这就是耦合调压器
`max_spread` 语义的实现代价（04 篇 §2）。

### §A6.2 `regulator_set_voltage_rdev`：会顺手改上游

`core.c:3820`。这里有一段很容易漏的行为：

```c
/* :3827-3831 */
if (rdev->supply &&
    regulator_ops_is_valid(rdev->supply->rdev, REGULATOR_CHANGE_VOLTAGE) &&
    (rdev->desc->min_dropout_uV || !(ops->get_voltage || ops->get_voltage_sel))) {
	...
	/* :3847 */ best_supply_uV += rdev->desc->min_dropout_uV;
	...
	/* :3859 */ regulator_set_voltage_unlocked(rdev->supply, best_supply_uV, INT_MAX, state);
```

**给一路 LDO 设电压，可能会连带把它的上游 DCDC 也调高** ——
条件是上游允许改电压，且这一路声明了 `min_dropout_uV`
（`driver.h:384`）。这是"LDO 需要输入比输出高至少 X 毫伏"
这个物理事实在软件里的表达。

02 篇 §4.2 里说 `min_dropout_uV` 是"唯一会让核心主动改别人电压的
desc 字段"，实现就是 `:3847` 这一行。

### §A6.3 执行层：`_regulator_do_set_voltage`

`core.c:3631`。两套 setter 的选择在这里（对应 §A1.1 那两条
只 warn 的 `WARN_ON`）：

| 分支 | 行 | 谁负责选值 |
|---|---|---|
| `ops->set_voltage` | `:3657-3667` | **驱动自己选**，然后核心用 `list_voltage(selector)`（`:3663`）或 `regulator_get_voltage_rdev`（`:3666`）回读实际值 |
| `ops->set_voltage_sel` | `:3669-3686` | **核心选**：`regulator_map_voltage`（`:3670`）→ `list_voltage`（`:3672`）→ 范围校验（`:3673`） |
| 都没有 | `:3688` | `-EINVAL` |

`set_voltage_sel` 分支里有三个小优化：
`old_selector == selector` 时直接返回 0（`:3675-3676`）；
`desc->vsel_step` 非 0 时分步爬（`_regulator_set_voltage_sel_step`，`:3677-3679`）；
否则一次写到位（`:3681-3682`）。

时序在 `_regulator_set_voltage_time`（`:3607-3627`）里，
**ramp_delay 有五级优先级**：

```
constraints->ramp_delay                      [:3611]   ← DTS 的 regulator-ramp-delay
  → desc->ramp_delay                         [:3613]   ← 驱动写死的
  → constraints->settling_time               [:3616]
  → constraints->settling_time_up （升压）    [:3617-3619]
  → constraints->settling_time_down（降压）   [:3620-3622]
  → 都是 0 → 返回 0，不等                     [:3624-3625]
```

最后 `DIV_ROUND_UP(abs(new_uV - old_uV), ramp_delay)`（`:3627`）——
**等待时间和电压差成正比**。

失败时会回滚 selector：`(void)ops->set_voltage_sel(rdev, old_sel)`
（`:3602`）—— 注意返回值被显式丢弃，**回滚失败没人知道**。
成功时打 `trace_regulator_set_voltage_complete`（`:3730`）
并发 `REGULATOR_EVENT_VOLTAGE_CHANGE` 通知（`:3725`）。

## §A7 开机 30 秒后的那批 `disabling`

**这是 regulator 子系统最容易让人吃惊的一个行为**，
而且它只发生一次、在开机后半分钟。

### §A7.1 三级触发

```
core_initcall(regulator_init)                            [:6213]
  └─ regulator_init()                                    [:6188]
        建 regulator_class、dummy、debugfs 根

late_initcall_sync(regulator_init_complete)              [:6310]
  └─ regulator_init_complete()                           [:6283]
        ├─ if (of_have_populated_dt())
        │       has_full_constraints = true;              [:6291-6292]
        └─ schedule_delayed_work(&regulator_init_complete_work,
                                 msecs_to_jiffies(30000)) [:6305-6306]

30 秒后
  └─ regulator_init_complete_work_function()             [:6259]
        ├─ class_for_each_device(..., regulator_register_resolve_supply) [:6268-6269]
        └─ class_for_each_device(..., regulator_late_cleanup)            [:6276-6277]
```

**`:6291-6292` 那两行是 arm64 上一切"dummy regulator"行为的开关**：
只要设备树非空，`has_full_constraints` 就变成 true。
01 篇 §5.2 和 §A4.2 里那个"arm64 上恒真"就是指这里。

**30 秒（`:6305`）是硬编码的**。为什么要等：
注释 `:6294-6303` 是一段罕见的坦白：

> Ideally we'd get a notification from userspace when this happens but we
> don't so just wait a bit and hope we waited long enough. It'd be better
> if we'd only do this on systems that need it, and a kernel command line
> option might be useful.

**"等一会儿，希望等够了"** —— 这是本子系统的第二处自陈债（§C2）。

### §A7.2 `regulator_late_cleanup` 的五道门

`core.c:6216-6257`。它对**每一个**已注册的 rdev 问五个问题：

| # | 行 | 条件 | 不满足就 |
|---|---|---|---|
| 1 | `:6221-6222` | `constraints && constraints->always_on` | 放过 |
| 2 | `:6224-6225` | `!regulator_ops_is_valid(rdev, REGULATOR_CHANGE_STATUS)` | 放过（没授权关） |
| 3 | `:6229-6230` | `rdev->use_count` 非 0 | 放过（有人在用） |
| 4 | `:6233` | `_regulator_is_enabled(rdev) <= 0` | 放过（本来就是关的） |
| 5 | `:6236` | `have_full_constraints()` | 走 else 分支 |

五道全过 → **真的关掉**：

```c
/* :6237-6239 */  /* We log since this may kill the system if it goes wrong. */
/* :6240 */       rdev_info(rdev, "disabling\n");
/* :6241 */       ret = _regulator_do_disable(rdev);
/* :6243 */       if (ret != 0) rdev_err(rdev, "couldn't disable: %pe\n", ...);
```

**`:6240` 那条 `rdev_info(rdev, "disabling\n")` 就是
你在 dmesg 里看到的那一批日志**。
注释 `:6237-6239` 老实写着 "this may kill the system if it goes wrong" ——
**打日志的目的就是给"关错了导致挂机"留个现场**。

第 5 门走 else（`:6250`）时打的是
`rdev_warn(rdev, "incomplete constraints, leaving on\n")` ——
非 DT 系统上看到的是这一条。arm64 上看不到。

第 4 门的注释（`:6232`）"If reading the status failed, assume that
it's off." —— `_regulator_is_enabled` 返回负数被当成"关的"，
所以**读不出状态的 regulator 不会被误关**。

### §A7.3 实践含义

| 现象 | 解释 |
|---|---|
| 开机 30 秒后一批 `xxx: disabling` | 正常。这些是"DTS 里声明了、但没有任何驱动 get 它"的路 |
| 某个外设开机能用，半分钟后失灵 | 它的电源被这里关掉了 —— 说明该驱动**没有 get 并 enable** 那路电源，只是在蹭 bootloader 留下的状态 |
| 想临时躲开 | 给那一路加 `regulator-always-on`（走第 1 门）。**但这是掩盖问题，不是修** |

这条路径也解释了为什么"驱动忘了 `regulator_enable`"
在开发板上**能测过** —— 前 30 秒是好的。

## §A8 `regulator_unregister`：拆除

`core.c:5767-5791`（kerneldoc `:5761-5766`）。**只有 25 行**，
但顺序不能乱：

```c
/* :5769 */  if (rdev == NULL) return;
/* :5772 */  if (rdev->supply) {
/* :5773 */      while (rdev->use_count--)
/* :5774 */          regulator_disable(rdev->supply);
/* :5775 */      regulator_put(rdev->supply);
/* :5776 */  }
/* :5778 */  flush_work(&rdev->disable_work.work);
/* :5780 */  mutex_lock(&regulator_list_mutex);
/* :5782 */  WARN_ON(rdev->open_count);
/* :5783 */  regulator_remove_coupling(rdev);
/* :5784 */  unset_regulator_supplies(rdev);
/* :5785 */  list_del(&rdev->list);
/* :5786 */  regulator_ena_gpio_free(rdev);
/* :5787 */  device_unregister(&rdev->dev);
/* :5789 */  mutex_unlock(&regulator_list_mutex);
```

**`:5773-5774` 那个 `while (rdev->use_count--)` 是全子系统
最容易看错的一行**：它**不是**关掉自己，而是
**按自己被 enable 的次数，逐次去 disable 上游**。
自己那一路的硬件状态在这里**根本没被改**。

后果：`regulator_unregister` **不保证电源被关掉**。
它只保证引用计数被还给上游。真正关电要么靠 §A7 的
late cleanup（已经过了 30 秒就不会再跑），要么驱动自己在
`remove` 里 disable。

`:5782` 的 `WARN_ON(rdev->open_count)` 是**唯一**会因为
"还有消费者持有句柄"而告警的地方 ——
但它**只 warn，然后照样拆**。拆完之后消费者手里的
`struct regulator` 指向已释放的 rdev。
这是一个**设计上接受的悬空窗口**，靠 `try_module_get`（§A4.1 第 7 门）
拦住"PMIC 模块被 rmmod"这个主要来源。

`flush_work`（`:5778`）必须在拿 `regulator_list_mutex` **之前** ——
`regulator_disable_work`（`:3155`）自己要拿 rdev 锁。

`devm_regulator_register` 的自动拆除走
`devm_rdev_release`（`drivers/regulator/devres.c`），最终也是调到这里。

---

# Part B：横切机制

> 本部分不按时间排。每节独立，可以只读你关心的那一节。

## §B1 三层锁：为什么用 ww_mutex

### §B1.1 三层是哪三层

| 层 | 变量 / 字段 | 保护什么 | 定义 |
|---|---|---|---|
| 全局链表锁 | `regulator_list_mutex` | `regulator_list` / `regulator_map_list` / `regulator_coupler_list` / `regulator_supply_alias_list` | `core.c:37` 起那组全局变量（02 篇 §8.1） |
| 嵌套计数锁 | `regulator_nesting_mutex` | 只保护 `rdev->ref_cnt` 和 `rdev->mutex_owner` 这两个字段 | 在 `regulator_lock_nested` 里用（`:147` / `:169`） |
| 每 rdev 锁 | `rdev->mutex`（**ww_mutex**） | 这一路的所有状态 | `ww_mutex_init` 在 `:5609` |
| 错误缓存自旋锁 | `rdev->err_lock` | `rdev->cached_err`，因为要在中断上下文里改 | `spin_lock_init` 在 `:5569` |

**只有 `rdev->mutex` 是 ww_mutex，另外两把是普通 mutex。**

### §B1.2 为什么需要 ww_mutex

一次 `regulator_enable` 要锁的不是一个 rdev，而是
**这一路 + 它的所有上游 + 所有耦合伙伴 + 上游的耦合伙伴……**
（`regulator_lock_dependent`，`:393`）。

**两个消费者从不同的叶子出发，会以不同的顺序去锁同一组 rdev** ——
这就是经典的 ABBA 死锁场景，而且**顺序取决于运行期的供电树形状，
编译期定不下来**。

ww_mutex（wound/wait）的解法是"检测到死锁就退掉重来"：

```
regulator_lock_dependent(rdev, ww_ctx)                   [:393]
  mutex_lock(&regulator_list_mutex)                      [:400]  先锁全局
  ww_acquire_init(ww_ctx, &regulator_ww_class)           [:402]
  do {                                                   [:404]
      if (new_contended_rdev)                            [:405]
          ww_mutex_lock_slow(...)                        [:406]  ← 上一轮冲突的那个先慢慢拿
      err = regulator_lock_recursive(...)                [:412]
      if (old_contended_rdev) regulator_unlock(...)      [:417-418]
  } while (err == -EDEADLK);                             [:420]  死锁就整轮重来
  ww_acquire_done(ww_ctx)                                [:422]
  mutex_unlock(&regulator_list_mutex)                    [:424]
```

**`:420` 那个 `while (err == -EDEADLK)` 是整个机制的核心** ——
拿锁失败不是错误，是"再来一次，这次先拿那个抢不到的"。

`regulator_lock_recursive`（`:321`）是真正的递归：
对每个耦合成员（`:329-330`）先 `regulator_lock_nested`（`:336`），
然后如果它有上游、且上游不在耦合组里（`:350`），
就**递归下去锁上游那一整组**（`:351-354`）。
失败时 `regulator_unlock_recursive(rdev, i)`（`:365`）
只回退**已经拿到的前 i 个**。

### §B1.3 递归加锁：同一个任务可以锁同一把锁多次

`regulator_lock_nested`（`:141`）做的事：

```c
/* :149 */ if (!ww_mutex_trylock(&rdev->mutex, ww_ctx)) {
/* :150 */     if (rdev->mutex_owner == current)
/* :151 */         rdev->ref_cnt++;              已经是我的了，只加计数
/* :153 */     else lock = true;                 ← 别人的，老实等
/* :157 */     ret = ww_mutex_lock(&rdev->mutex, ww_ctx);
```

**`rdev->ref_cnt` 不是 kref，是"当前任务递归加了几层锁"**
（02 篇 §4.1(b) 那条已经说过）。
`regulator_unlock`（`:196`）只在 `--ref_cnt == 0` 时才真的
`ww_mutex_unlock`（`:200-203`），并且 `WARN_ON_ONCE(ref_cnt < 0)`（`:205`）。

这解释了 §A3.3 里"`set_machine_constraints` 中途调
`regulator_enable(rdev->supply)`"为什么不会自锁死。

另有一个两把锁的简化版 `regulator_lock_two`（`:218`），
只在 `regulator_resolve_supply` 里用（调用点 `:2148`）——
它的重试循环（`:236-258`）会**交换谁走慢路径**（`:251-253`）。

### §B1.4 对驱动作者的含义

**`regulator_ops` 里的任何回调被调用时，`rdev->mutex` 已经被核心持有。**
所以回调里：

| 不要做 | 原因 |
|---|---|
| 调 `regulator_*` 消费者 API | 会重入核心，`ref_cnt` 机制救得了自己这一把，救不了别的 rdev |
| 睡很久 | 整条供电链都锁着 |
| 假设自己在进程上下文 | 大部分是，但 `get_error_flags` 相关路径可能不是（`err_lock` 存在的理由） |

唯一的规范做法：**只做寄存器读写和纯计算**。
helpers.c 里那 28 个函数就是这个规范的示范（04 篇 §5）。

## §B2 授权闸门：`valid_ops_mask` 与 `-EPERM`

**本子系统的权限模型只有一个函数**：

```c
/* :117 */ static bool regulator_ops_is_valid(struct regulator_dev *rdev, int ops)
/* :119 */     if (!rdev->constraints) { rdev_err(rdev, "no constraints\n"); return false; }
/* :124 */     if (rdev->constraints->valid_ops_mask & ops) return true;
/* :127 */     return false;
```

**十一行、只看 `constraints->valid_ops_mask` 一个字段。**

**它完全不看 `desc` / `ops`** —— 所以
"驱动实现了 `set_voltage` 回调" 和 "允许改电压" 是**两件独立的事**
（01 篇 §3.2、02 篇 §1.0 第二组）。
DTS 里没写 `regulator-min-microvolt`/`regulator-max-microvolt`
就不会置 `REGULATOR_CHANGE_VOLTAGE`，于是**驱动写得再全也调不了压**。

调用点分布（`REGULATOR_CHANGE_*` 常量在
`include/linux/regulator/machine.h`）：

| 检查什么 | 调用点 | 失败 |
|---|---|---|
| `CHANGE_VOLTAGE` | `regulator_check_voltage` `:494` 起；`regulator_set_voltage_unlocked` `:3782`；`regulator_set_voltage_rdev` `:3828` | 前者 `-EPERM`，中间那个是"落在区间内就放行"（§A6 第 2 步） |
| `CHANGE_CURRENT` | `regulator_check_current_limit` `:564-567` | `-EPERM` |
| `CHANGE_MODE` | `regulator_mode_constrain` `:584` 起 | `-EPERM` |
| `CHANGE_STATUS` | `_regulator_enable` `:2924-2926`；`_regulator_disable` `:3030`；`regulator_late_cleanup` `:6224` | enable 给 `-EPERM`，disable **静默跳过**，cleanup 直接放过 |
| `CHANGE_DRMS` | `drms_uA_update` `:1015` 起 | `-EPERM` |

**同一个判据、三种不同后果**（enable 报错 / disable 沉默 / cleanup 放过）——
这是 §A5.3 那张不对称表的根源。

## §B3 电压协商：三次夹紧

一次成功的调压，`min_uV`/`max_uV` 这一对值被**夹紧三次**：

```
消费者给的 [min_uV, max_uV]
  │
  ├─ 1. regulator_check_voltage(rdev, &min_uV, &max_uV)     [:494]
  │        用 constraints->min_uV / max_uV 夹
  │        授权不足 → -EPERM；夹完 min > max → -EINVAL
  │
  ├─ 2. regulator_check_consumers(rdev, &min, &max, state)  [:527]
  │        遍历 consumer_list，取所有消费者区间的**交集**
  │        ├─ 没表态的消费者（min/max 都是 0）跳过  [:540-541]
  │        ├─ max 取最小、min 取最大                [:543-546]
  │        └─ 交集为空 → rdev_err "Restricting voltage, %u-%uuV" + -EINVAL  [:549-553]
  │
  └─ 3. regulator_get_optimal_voltage(...)                  [:3921]
           耦合组内再按 max_spread 收一次
```

**第 2 步是"我 set 成功了但电压不是我要的值"的全部原因** ——
交集里可能有很多个可行值，最终选哪个由第 3 步和
`regulator_map_voltage` 决定，**不保证是你区间的哪一端**。

注释 `:536-539` 说明了"没表态就是都行"这个默认：
"Assume consumers that didn't say anything are OK with anything in
the constraint range."
所以**一个从来不调 `set_voltage` 的消费者，不会限制别人**。

`:550` 那条 `Restricting voltage, %u-%uuV` 是消费者区间冲突时
唯一的日志（07 篇 §2.2）—— 它的措辞容易误读成"已经限制了"，
实际含义是"**限制之后没有可行值了，我失败了**"。

## §B4 耦合调压器：注册期挂接 + 运行期平衡

### §B4.1 注册期：`regulator_init_coupling`

`core.c:5431`，在 `regulator_register` 的 `:5698` 被调。

```
regulator_init_coupling(rdev)                            [:5431]
  ├─ n_phandles = of_get_n_coupled(rdev)                 [:5439]   ← 数 DTS 里的 regulator-coupled-with
  ├─ kcalloc(n_phandles + 1, ...)                        [:5441]   +1 是给自己
  ├─ coupled_rdevs[0] = rdev                             [:5451]   第 0 个永远是自己
  ├─ n_coupled = n_phandles + 1; n_resolved++            [:5452-5453]
  ├─ n_phandles == 0 → return 0                          [:5456-5457]  不耦合也有这个数组
  ├─ !of_check_coupling_data(rdev) → -EPERM              [:5459-5460]
  └─ coupler = regulator_find_coupler(rdev)              [:5463]
```

**每个 rdev 的 `coupled_rdevs[]` 里都包含自己**（`:5451`，
注释 `:5447-5450` 明说 "at least pointer to itself"）——
02 篇 §4.4 提过这一点，实现就在这里。
所以 `n_coupled == 1` 表示"不耦合"，不是 0。

`regulator_find_coupler`（`:5309`）**倒序**遍历 coupler 链表
（`:5319`），依次调 `attach_regulator`。
注释 `:5314-5318` 解释了倒序的原因：
通用 coupler 在 `core_initcall` 里**最先**注册
（`regulator_coupler_register(&generic_regulator_coupler)`，`:6207`），
所以倒序遍历时它**最后**被试到 —— 定制 coupler 优先。

`attach_regulator` 的返回值是**三态**的：
0 = 我接了（`:5321`）；`< 0` = 出错，直接透传（`:5329-5330`）；
`1` = 不是我管的，试下一个（`:5332-5333`）；
其他正数 = `break` 跳出循环（`:5335`）→ `-EINVAL`（`:5338`）。

### §B4.2 两条硬限制

通用 coupler 的 `attach` 只有 15 行（`generic_coupler_attach`，`:5475`）
但它定义了 arm64 上耦合调压的**全部能力边界**：

| 限制 | 行 | 消息 | 返回 |
|---|---|---|---|
| 超过两路耦合 | `:5478-5482` | `Voltage balancing for multiple regulator couples is unimplemented` | `-EPERM` |
| 非 always-on | `:5484-5488` | `Coupling of a non always-on regulator is unimplemented` | `-ENOTSUPP` |

**通用 coupler 只支持"两路、都是 always-on"** ——
超过就必须自己写一个带 `balance_voltage` 的 coupler。
第一条限制在 `regulator_find_coupler` 里**又查了一遍**
（`:5322-5324` → `:5340-5347`，同一条消息），
因为定制 coupler 也可能没实现 `balance_voltage`。

01 篇 §4 那两条 `-EPERM` / `-ENOTSUPP` 就是这里。
两个功能同样"未实现"却用了不同错误码 —— 已记入 99 §6。

### §B4.3 运行期：解析与拆除

`regulator_resolve_coupling`（`:5350`）在
`regulator_register` 尾部（`:5729`）被调，
它会**递归去解析伙伴的耦合**（`:5377`）。
`n_resolved < n_coupled` 时任何调压都会被
`regulator_balance_voltage` 挡掉（`:4161-4164`，`-EPERM`），
`regulator_get` 也会 `-EPROBE_DEFER`（§A4.1 第 5 门）。

拆除是 `regulator_remove_coupling`（`:5381`），
两个调用点：注册失败回退（`:5741`）和 `regulator_unregister`（`:5783`）。

## §B5 通知链、bypass 与 suspend

### §B5.1 通知链只有一层

```c
/* :4875 */ static int _notifier_call_chain(struct regulator_dev *rdev,
/* :4879 */     return blocking_notifier_call_chain(&rdev->notifier, event, data);
```

**注释 `:4878` 写着 "call rdev chain first"，
但函数体只有一行 —— 没有"然后"。**
历史上这里曾经会向上游传播事件，现在不会了。
这意味着**上游过压/过温不会自动通知下游消费者**，
每个消费者只收到自己那一路的事件。

对外入口是 `regulator_notifier_call_chain`（`:5094`），
转手调 `_notifier_call_chain`（`:5097`）—— 给 PMIC 驱动用。

事件在时间线上的发出点：`EVENT_ENABLE` `:2934`；
`PRE_DISABLE` `:3031`、`ABORT_DISABLE` `:3040`、`DISABLE` `:3045`；
force 版本 `:3100` / `:3108` / `:3113`；
`VOLTAGE_CHANGE` `:3725`、`ABORT_VOLTAGE_CHANGE` `:3516` / `:3540`。

**只有 `PRE_DISABLE` 的返回值被检查**（`:3034-3035` / `:3102-3103`）——
其余事件的监听者无法影响流程（§A5.3）。

### §B5.2 suspend/resume 挂在 class 上

```c
/* :5851-5854 */ static const struct dev_pm_ops __maybe_unused regulator_pm_ops = {
                     .suspend = regulator_suspend,
                     .resume  = regulator_resume,
                 };
/* :5857-5864 */ struct class regulator_class = {
                     .name = "regulator", ...
/* :5862 */          .pm = &regulator_pm_ops,
                 };
```

**每个 rdev 都是 class 的一个 device，所以 PM 核心会
逐个调 `regulator_suspend`** —— 不需要 PMIC 驱动做任何事。

`regulator_suspend`（`:5800`）：

```
rstate = regulator_get_suspend_state_check(rdev, pm_suspend_target_state)  [:5807]
  NULL → return 0                                        [:5808-5809]   DTS 没写就不动
regulator_lock(rdev)                                     [:5811]
__suspend_set_state(rdev, rstate)                        [:5812]
```

`regulator_resume`（`:5818`）多两道门：
`rstate == NULL` → 0（`:5826-5827`）；
**`!ops->resume` → 直接返回 0**（`:5830-5831`，
注释 `:5829` "Avoid grabbing the lock if we don't need to"）；
只有 `enabled` 是 `ENABLE_IN_SUSPEND` 或 `DISABLE_IN_SUSPEND`
才调 `ops->resume`（`:5835-5837`）。

**`enabled == DO_NOTHING_IN_SUSPEND` 时 resume 什么都不做** ——
这正是 02 篇 §5.2 里"三值而不是 bool"的实际后果：
DTS 里没写 `regulator-off-in-suspend`，
挂起前后这一路的状态**完全由硬件自己决定**。

suspend 期的调压走另一套 API：`regulator_set_suspend_voltage`
（`:4270` 起）→ `_regulator_set_suspend_voltage`（`:4251`）
→ 最终还是 `regulator_set_voltage_unlocked`（`:4267`），
只是 `state` 参数不是 `PM_SUSPEND_ON`。
`rstate->min_uV == rstate->max_uV` 时报
`rdev_err "The suspend voltage can't be changed!"` + `-EPERM`（`:4262-4264`）。
另有 `regulator_suspend_enable`/`_disable`（`:4224` / `:4231`）
经 `regulator_suspend_toggle`（`:4207`）改 `rstate->enabled`，
`!rstate->changeable` → `-EPERM`（`:4216-4217`）。

### §B5.3 bypass

`bypass` 是"把 LDO 当直通线用"（省掉压差损耗）。
它是唯一有**独立计数**的操作：`rdev->bypass_count`（02 篇 §4.1(b)）。
四个 tracepoint 都在这条路上（`:4810` / `:4816` / `:4823` / `:4829`）。
详细用法见 01 篇 §2.5；这里只登记它在时间线上**不属于任何阶段** ——
消费者可以在任何时刻切换。

---

# Part C：评价

## §C1 三个设计取舍

### §C1.1 "约束"与"能力"分离 —— 值得

把"硬件能做什么"（`desc` + `ops`，驱动写）和
"板子允许做什么"（`constraints`，DTS 写）彻底分开，
是本子系统**最成功的一个决定**。

好处很直接：**同一个 PMIC 驱动可以用在几十块板子上**，
而"这块板的 VDD_CORE 只许 0.9~1.1V"这条板级事实
不进任何 `.c` 文件。§B2 那十一行 `regulator_ops_is_valid`
就是这个分离的全部执行代价。

代价：**"我明明实现了回调却调不动"成为最常见的困惑**，
而错误码只有一个干巴巴的 `-EPERM`（01 篇 §5.1 列了 9 个来源）。

### §C1.2 dummy regulator 兜底 —— 有争议

arm64 上 `have_full_constraints()` 恒真（§A7.1），
所以 DTS 漏写一条 `xxx-supply` 的后果是
**一条 `dev_warn` + 驱动照样 probe 成功 + "enable 成功"**（§A4.2）。

支持这个设计的理由：不这么做，任何一块 DTS 不完整的板子都起不来，
而 regulator 恰恰是"大部分设备其实不 care"的资源。

代价：**这是本子系统最大的静默故障来源**。
症状是"能用，但省电/调压全都无效"，而且
`/sys/class/regulator/` 里看不出哪个是 dummy
（要看 `dmesg` 里那条 `using dummy regulator`）。

06 篇 §7 的定位决策树第一步就是查这条日志。

### §C1.3 30 秒延迟 cleanup —— 明确的权宜之计

见 §A7 和 §C2 —— 源码注释自己承认了。

## §C2 源码里的设计债：核心两处，都在同一个话题上

**核心五个文件里只有一条 `FIXME`，零条 `TODO`/`XXX`/`REVISIT`/`HACK`。**
普查范围是核心 + 周边框架五个文件（`core.c` / `of_regulator.c` /
`helpers.c` / `irq_helpers.c` / `devres.c`）——
**不含 `drivers/regulator/` 下的 180+ 个具体驱动**，
那里另有若干条（见本节末）：

```bash
grep -nE 'FIXME|TODO|XXX|REVISIT|HACK' drivers/regulator/core.c \
    drivers/regulator/of_regulator.c drivers/regulator/helpers.c \
    drivers/regulator/irq_helpers.c drivers/regulator/devres.c
```

| # | 位置 | 内容 | 影响 |
|---|---|---|---|
| 1 | `core.c:5682-5685` | `FIXME: this currently triggers a chicken-and-egg problem when creating -SUPPLY symlink in sysfs to a regulator that is just being created` | §A3.4 那条 `-EPROBE_DEFER` 重试路径。后果是**注册顺序敏感** |
| 2 | `core.c:6294-6303` | `Ideally we'd get a notification from userspace when this happens but we don't so just wait a bit and hope we waited long enough.` | §A7 那个 30 秒硬编码。同一段还提了两个未做的改进：只在需要的系统上做、加内核命令行选项 |

把范围放宽到整个 `drivers/regulator/` + `include/linux/regulator/`
（`grep -rn 'FIXME|TODO|XXX|REVISIT|HACK'`，30 行命中里
大部分是 `MT6358_ID_XXX` 这类占位符而非真的债），
真正的自陈债还有四条，都在具体驱动里：

| 位置 | 内容 |
|---|---|
| `drivers/regulator/fixed.c:266` | `FIXME: find a better way to deal with this` —— 两路 fixed regulator 共用一根 enable 线，只能靠 `GPIOD_FLAGS_BIT_NONEXCLUSIVE`（`:268`）绕过（06 篇 §4.3） |
| `drivers/regulator/lp8788-ldo.c:503` | `FIXME: check default mode for GPIO here: high or low?` |
| `drivers/regulator/stw481x-vmmc.c:47` | `enable_time = 200, /* FIXME: look this up */` —— 一个猜出来的延时值 |
| `drivers/regulator/axp20x-regulator.c:986` | `TODO: FLDO3 = {DCDC5, FLDOIN} / 2` |

**核心的那两条债都是关于"什么时候算初始化完成"** ——
这不是巧合：regulator 的正确性依赖"所有消费者都已 probe"，
而内核**没有这个事件**。第 2 条注释里那句
"hope we waited long enough" 是对这个缺口最诚实的表述。

相比 dmaengine（16 条自陈问题）和 clk，
regulator 的**代码自陈债极少**，但**跨层不一致**不少
（`-ENOTSUPP` vs `-EOPNOTSUPP`、`num_users` 读的是 `use_count`），
这些不在注释里，只能靠对照读出来 —— 全部登记在 99 §6。

## §C3 可观测性：11 个 tracepoint，三处盲区

**regulator 的 tracepoint 覆盖比 dmaengine 好得多** ——
`include/trace/events/regulator.h` 里 **3 个事件类、11 个事件**，
`core.c` 里 **11 个调用点，一一对应**：

| 事件类 | 定义行 | 成员 |
|---|---|---|
| `regulator_basic`（只带名字） | `include/trace/events/regulator.h:15` | 9 个：`enable` `:33` / `enable_delay` `:41` / `enable_complete` `:49` / `disable` `:57` / `disable_complete` `:65` / `bypass_enable` `:73` / `bypass_enable_complete` `:81` / `bypass_disable` `:89` / `bypass_disable_complete` `:97` |
| `regulator_range`（名字 + min/max） | `:109` | `set_voltage` `:131` |
| `regulator_value`（名字 + 单值） | `:143` | `set_voltage_complete` `:163` |

调用点：`:2757` / `:2789` / `:2823` / `:2987` / `:3006` /
`:3641` / `:3730` / `:4810` / `:4816` / `:4823` / `:4829`。

**三处盲区**：

| 盲区 | 后果 |
|---|---|
| **模式切换（`set_mode` / DRMS）零 tracepoint** | `drms_uA_update`（`:1015`）自动换模式的行为完全不可见，只能靠 sysfs 的 `opmode` 轮询 |
| **注册期完全不打点** | §A3 那十一个"注册期写硬件"的动作一个都不在 trace 里 —— 而这是最容易出问题的一段。只能靠 `dmesg` |
| **`regulator_late_cleanup` 不打点** | §A7 那批关电只有 `rdev_info "disabling"` 一条日志 |

三个 enable 相关的点（`:2757` / `:2789` / `:2823`）能把耗时拆成
"下命令" / "命令下完" / "延时结束" 三段 —— 这是本子系统
tracepoint 设计最好的一处（07 篇 §4.2）。

---

## 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| coupler / irq helper / dummy / userspace-consumer 各自怎么实现 | `04-extensions.md` |
| helpers.c 那 28 个函数怎么算电压 | `04-extensions.md` §5 |
| 照着写一个 PMIC 驱动 / 一个消费者 | `05-usage.md` |
| DTS 每条属性由谁消费、写错了怎么查 | `06-devicetree.md` |
| 本篇提到的每条日志、tracepoint、sysfs 在哪看 | `07-debug.md` |
| "为什么 `set_voltage` 成功了电压却不是我要的" 之类的直觉纠偏 | `08-feynman.md` |
| 每条论断的出处、以及已发现的自陈问题 | `99-evidence.md` |
