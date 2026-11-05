---
title: API 参考
---

# 01 代码地图与对外 API：regulator

> 基线 Linux 6.1.158（`f6e38ae624cf`），arm64-only。
> 本篇是"找文件"和"找 API"的唯一入口。
> **签名只写函数名 + 形参名**，类型信息在「备注」列；
> **位置只到文件**，每节开头统一交代头文件与实现文件。

## 本篇怎么用

| 你想做的事 | 去哪一节 |
|---|---|
| 找某个文件是干什么的 | §0.1 / §0.2 |
| 打开/关闭一路电源 | §2.2 |
| 调电压 | §2.3 |
| 一次要好几路电源 | §2.7（bulk） |
| 写一个 PMIC 驱动 | §3.1（注册）+ §3.3（现成 ops 助手） |
| 硬件出错想收到通知 | §2.6（消费者侧）+ §3.4（驱动侧 irq helper） |
| 看到一个错误码不知道什么意思 | §5 |

---

## §0 代码地图

### §0.1 核心文件

`drivers/regulator/` 下与"框架本身"有关的文件（其余 180+ 个是具体芯片驱动）：

| 文件 | 行数 | 职责 |
|---|---|---|
| `core.c` | 6310 | 框架主体：注册/注销、消费者句柄、约束校验、开关引用计数、电压设置与耦合、sysfs、suspend |
| `of_regulator.c` | 807 | 从 DT 解析约束（`regulator-min-microvolt` 那一族）、`regulator-coupled-with`、`of_regulator_bulk_get_all` |
| `devres.c` | 662 | 全部 `devm_*` 变体的实现（独立文件，不在 core.c 里） |
| `helpers.c` | 968 | 给驱动直接填进 `regulator_ops` 的现成实现（`*_regmap` / `*_linear_range` 那一族） |
| `irq_helpers.c` | 439 | 通用中断→通知转换助手（`regulator_irq_helper`） |
| `fixed.c` | 356 | `regulator-fixed` 驱动（GPIO 开关的固定电压，arm64 DTS 上最常见） |
| `fixed-helper.c` | 61 | 用代码而非 DT 造一个固定电压 regulator |
| `dummy.c` | 90 | 假 regulator，`regulator_get` 找不到真的时候顶上（§5.2） |
| `virtual.c` | 374 | 调试用的假消费者，全部通过 sysfs 驱动（`CONFIG_REGULATOR_VIRTUAL_CONSUMER`） |
| `userspace-consumer.c` | 181 | 让用户态用 sysfs 控制一路电源的消费者驱动 |
| `internal.h` | 125 | 框架内部共享声明（`struct regulator` 的真身在这里，**不对外**） |
| `dummy.h` | 23 | dummy regulator 的内部声明 |

**`struct regulator` 的定义在 `internal.h`，不在任何对外头文件里** ——
消费者拿到的是一个不透明指针。这一点在 02 篇 §1.0 展开。

### §0.2 对外头文件

| 头文件 | 行数 | 给谁看 |
|---|---|---|
| `include/linux/regulator/consumer.h` | 723 | **消费者**（用电的驱动）。§2 全部 API 在这里 |
| `include/linux/regulator/driver.h` | 783 | **提供者**（PMIC/LDO 驱动）。§3 全部 API 在这里 |
| `include/linux/regulator/machine.h` | 292 | 板级约束（`struct regulation_constraints`、`struct regulator_init_data`） |
| `include/linux/regulator/coupler.h` | 100 | 耦合调压器（§4） |
| `include/linux/regulator/of_regulator.h` | 46 | DT 解析辅助，主要给多路 PMIC 驱动用 |
| `include/linux/regulator/fixed.h` | 60 | `regulator-fixed` 的平台数据 |
| `include/linux/regulator/*.h` 其余 30 个 | 各 18–236 | 单芯片平台数据，**arm64 上基本都走 DT，用不到** |

**三个主头文件的分工是硬边界**：`consumer.h` 里的函数几乎都接受
`struct regulator *`（唯一例外是 §2.8 的两个 suspend API），
`driver.h` 里的函数几乎都接受 `struct regulator_dev *`。
**用错头文件在编译期就会暴露**，这是设计上有意为之。

### §0.3 Kconfig

| 符号 | 位置 | 说明 |
|---|---|---|
| `REGULATOR` | `drivers/regulator/Kconfig:2` | 总开关，`menuconfig`。**关掉之后所有消费者 API 变成 `consumer.h` 里的 static inline 桩**（§5.3） |
| | | `select LINEAR_RANGES`（`:4`）—— 线性区间查表是框架强依赖 |
| `REGULATOR_DEBUG` | `:28` | 调试开关，帮助文本只有一句 "Say yes here to enable debugging support"（07 篇查它到底开了什么） |
| `REGULATOR_FIXED_VOLTAGE` | `:33` | `fixed.c`，tristate |
| `REGULATOR_VIRTUAL_CONSUMER` | `:40` | `virtual.c`，帮助文本明说 "mainly useful for test purposes" |
| `REGULATOR_USERSPACE_CONSUMER` | `:50` | `userspace-consumer.c` |
| `CONFIG_OF` | — | `of_regulator.o` 由它挂上（`drivers/regulator/Makefile:7`），**不是**由 `REGULATOR` |

`core.o dummy.o fixed-helper.o helpers.o devres.o irq_helpers.o` 六个
**一起由 `CONFIG_REGULATOR` 编进去**（`drivers/regulator/Makefile:6`）——
所以 `helpers.c` 和 `irq_helpers.c` 里的东西**不需要额外开关**。

---

## §1 两侧 API 的总览

regulator 是**框架型**子系统：没有自己的 `bus_type`，
只有一个 `struct class regulator_class`（`core.c:5857`）。
它的 API 严格分两侧：

```
   用电的驱动（消费者）                        供电的芯片驱动（提供者）
   MMC / UART / 摄像头 / CPU 调频              PMIC / LDO / DC-DC / regulator-fixed
          │                                              │
          │ consumer.h                       driver.h    │
          │ regulator_get / _enable                      │ regulator_register
          │ regulator_set_voltage                        │ 填 regulator_ops
          ▼                                              ▼
   ┌──────────────────────────────────────────────────────────────┐
   │              drivers/regulator/core.c（框架核心）             │
   │  引用计数 · 约束校验 · 电压协商 · 通知链 · 耦合 · sysfs       │
   └──────────────────────────────────────────────────────────────┘
```

**核心的真正职责不是"转发"，而是"仲裁"**：
多个消费者对同一路电源的开关请求要引用计数、
电压请求要取交集、板级约束要拦截越界值。
所以消费者调 `regulator_set_voltage(3.3V)` **不等于**驱动的
`set_voltage` 会被调到 3.3V —— 中间有 §5 里那一串 `-EPERM`。

---

## §2 消费者 API（`consumer.h`）

> 头文件：`include/linux/regulator/consumer.h`；
> 实现：`drivers/regulator/core.c`（非 devm 版）+ `drivers/regulator/devres.c`（devm 版）。

### §2.1 获取与释放

| API | 说明 | 备注 |
|---|---|---|
| `regulator_get(dev, id)` | 按名字拿一路电源的句柄 | 返回 `struct regulator *`，**失败返回 ERR_PTR，不是 NULL**；带 `__must_check`。找不到时**可能返回 dummy 而不是错误**，见 §5.2 |
| `devm_regulator_get(dev, id)` | 同上，`dev` 销毁时自动 put | 绝大多数驱动该用这个 |
| `regulator_get_optional(dev, id)` | 同上，但**找不到就返回 `-ENODEV`**，不给 dummy | 语义是"这路电源可有可无" |
| `devm_regulator_get_optional(dev, id)` | 上面的 devm 版 | |
| `regulator_get_exclusive(dev, id)` | 独占：拿到之后别人再 get 会失败 | 独占者可以假定"没人和我抢"；已被独占时返回 `-EPERM`（`core.c:2245`） |
| `devm_regulator_get_exclusive(dev, id)` | 上面的 devm 版 | |
| `devm_regulator_get_enable(dev, id)` | get + enable 一步到位，销毁时自动 disable + put | 适合"上电之后一直开着"的场景 |
| `devm_regulator_get_enable_optional(dev, id)` | 同上，找不到不报错 | |
| `regulator_put(regulator)` | 释放句柄 | 不检查是否还处于 enable 状态，**忘了 disable 会让引用计数永久泄漏** |
| `devm_regulator_put(regulator)` | 提前释放 devm 管理的句柄 | 少用；正常靠自动释放 |

**`id` 是"消费者视角的名字"**，不是 regulator 自己的名字。
DT 上对应 `<id>-supply = <&某个 regulator>` 这条属性（06 篇 §3）。

### §2.2 开关

| API | 说明 | 备注 |
|---|---|---|
| `regulator_enable(regulator)` | 请求打开 | 带 `__must_check`。**是引用计数的**：N 个消费者各调一次，硬件只开一次 |
| `regulator_disable(regulator)` | 撤销自己的那一次 enable | **不保证硬件真的关掉** —— 别的消费者还开着，或者约束是 `always-on` |
| `regulator_force_disable(regulator)` | 无视别人，强行关 | 危险 API：会给所有消费者发 `REGULATOR_EVENT_FORCE_DISABLE` 通知。只该在故障处理里用 |
| `regulator_disable_deferred(regulator, ms)` | ms 毫秒后再 disable | 用于"关掉前要保持一段时间"的时序要求 |
| `regulator_is_enabled(regulator)` | 硬件当前是否输出 | 返回 **>0 表示开、0 表示关、<0 是错误码**，不是 bool。问的是**硬件状态**，不是"我 enable 过没有" |

### §2.3 电压

| API | 说明 | 备注 |
|---|---|---|
| `regulator_set_voltage(regulator, min_uV, max_uV)` | 请求把电压设在一个**区间**内 | 单位是 **微伏**。传区间不是传值：核心要和其他消费者的区间取交集 |
| `regulator_set_voltage_time(regulator, old_uV, new_uV)` | 查"从 old 变到 new 要等多久"（微秒） | 只是查询，不改电压 |
| `regulator_get_voltage(regulator)` | 读当前电压（微伏） | 返回值 <0 是错误码，所以**合法电压永远 >0**；`-ENOTRECOVERABLE` 有特殊含义（§5.1） |
| `regulator_sync_voltage(regulator)` | 把软件记录的电压重新写一遍硬件 | 用于 bootloader 和内核不一致、或硬件被外部改过 |
| `regulator_count_voltages(regulator)` | 这路电源有几个可选档位 | 返回 0 表示"不可枚举" |
| `regulator_list_voltage(regulator, selector)` | 第 selector 档对应多少微伏 | 配合上一条做枚举；返回 <0 表示该档无效 |
| `regulator_is_supported_voltage(regulator, min_uV, max_uV)` | 这个区间能不能达到 | 返回 bool 语义的 int |
| `regulator_get_linear_step(regulator)` | 线性步长（微伏） | 返回 `unsigned int`；0 表示不是线性的 |
| `regulator_set_voltage_tol(regulator, target_uV, tol_uV)` | 以"目标值 ± 容差"的形式设电压 | `static inline`，包装 `set_voltage`（`consumer.h:705`） |
| `regulator_set_voltage_triplet(regulator, min_uV, target_uV, max_uV)` | 先试窄区间，失败再试宽区间 | `static inline`（`consumer.h:695`）；给"最好是 X，不行就 X±Y"的场景 |
| `regulator_is_supported_voltage_tol(regulator, target_uV, tol_uV)` | 容差形式的支持性查询 | `static inline`（`consumer.h:715`） |

### §2.4 电流

| API | 说明 | 备注 |
|---|---|---|
| `regulator_set_current_limit(regulator, min_uA, max_uA)` | 设电流上限区间 | 单位微安；和电压一样是区间语义 |
| `regulator_get_current_limit(regulator)` | 读当前电流上限 | <0 为错误码 |

### §2.5 模式、负载、bypass

| API | 说明 | 备注 |
|---|---|---|
| `regulator_set_mode(regulator, mode)` | 直接指定工作模式 | mode 取 `REGULATOR_MODE_FAST/NORMAL/IDLE/STANDBY`（`consumer.h:82-85`）。硬件不支持该模式时核心会**自动往"更耗电"的模式退**（`core.c:598-601` 的注释） |
| `regulator_get_mode(regulator)` | 读当前模式 | 返回 `unsigned int`；`REGULATOR_MODE_INVALID`（`consumer.h:81`）为 0 |
| `regulator_set_load(regulator, load_uA)` | 告诉核心"我大概吃多少电"，由核心选模式 | 这是**推荐用法**，比 `set_mode` 好：核心汇总所有消费者的负载再决定。需要约束里有 `REGULATOR_CHANGE_DRMS`（`machine.h:39`） |
| `regulator_allow_bypass(regulator, allow)` | 允许/禁止旁路模式（LDO 直通） | 也是引用计数式的："所有消费者都允许"才会真的旁路 |
| `regulator_get_error_flags(regulator, flags)` | 读硬件当前的错误标志 | 位定义是 `REGULATOR_ERROR_*`（`consumer.h:145-154`），和通知事件的 `REGULATOR_EVENT_*`（`:109-131`）**是两套不同的位**，见 07 篇 |

### §2.6 通知

| API | 说明 | 备注 |
|---|---|---|
| `regulator_register_notifier(regulator, nb)` | 注册通知回调 | 事件位 `REGULATOR_EVENT_*`（`consumer.h:109-131`）；`REGULATOR_EVENT_WARN_MASK`（`:131`）是四个 warn 位的合集 |
| `regulator_unregister_notifier(regulator, nb)` | 注销 | |
| `devm_regulator_register_notifier(regulator, nb)` | devm 版注册 | |
| `devm_regulator_unregister_notifier(regulator, nb)` | 提前注销 devm 版 | |

电压变更前的通知带一个 `struct pre_voltage_change_data`
（`consumer.h:163-167`）作为参数。

### §2.7 bulk：一次要多路

| API | 说明 | 备注 |
|---|---|---|
| `regulator_bulk_get(dev, num_consumers, consumers)` | 按数组一次 get 多路 | 数组元素是 `struct regulator_bulk_data`（`consumer.h:186-193`），你只填 `supply`，核心回填 `consumer` |
| `devm_regulator_bulk_get(dev, num_consumers, consumers)` | devm 版 | |
| `devm_regulator_bulk_get_const(dev, num_consumers, in_consumers, out_consumers)` | 从 **const** 数组 get（自己复制一份） | 用于把 supply 名字放在 `const` 静态数组里的驱动 |
| `of_regulator_bulk_get_all(dev, np, consumers)` | **不给名字**，把 DT 节点上所有 `*-supply` 全 get 回来 | 实现在 `drivers/regulator/of_regulator.c`；返回个数。适合"我也不知道板子接了几路" |
| `devm_regulator_bulk_get_enable(dev, num_consumers, id)` | get + enable 多路，自动释放 | 参数是**名字数组**而不是 bulk_data 数组 |
| `regulator_bulk_enable(num_consumers, consumers)` | 一起开 | 失败会回滚已经开的那些 |
| `regulator_bulk_disable(num_consumers, consumers)` | 一起关 | 按**逆序**关 |
| `regulator_bulk_force_disable(num_consumers, consumers)` | 一起强关 | 危险性同 `force_disable` |
| `regulator_bulk_free(num_consumers, consumers)` | 一起 put | |
| `devm_regulator_bulk_put(consumers)` | 提前释放 devm bulk | |
| `regulator_bulk_set_supply_names(consumers, supply_names, num_supplies)` | 批量填 `supply` 字段 | `static inline`（`consumer.h:681`） |

### §2.8 supply 别名与其他

| API | 说明 | 备注 |
|---|---|---|
| `regulator_register_supply_alias(dev, id, alias_dev, alias_id)` | 给 (dev, id) 建一个指向别处的别名 | 用于 MFD：子设备的 supply 实际挂在父设备的名字上 |
| `regulator_unregister_supply_alias(dev, id)` | 注销别名 | |
| `regulator_bulk_register_supply_alias(dev, id, alias_dev, alias_id, num_id)` | 批量版 | |
| `regulator_bulk_unregister_supply_alias(dev, id, num_id)` | 批量注销 | |
| `devm_regulator_register_supply_alias(dev, id, alias_dev, alias_id)` | devm 版 | |
| `devm_regulator_bulk_register_supply_alias(dev, id, alias_dev, alias_id, num_id)` | devm 批量版 | |
| `regulator_get_drvdata(regulator)` / `regulator_set_drvdata(regulator, data)` | 在句柄上挂私有数据 | 挂的是 **rdev 的** drvdata，不是每个句柄独立的 |
| `regulator_get_regmap(regulator)` | 拿到底层 regmap | **越过框架直接碰硬件**的口子；驱动没用 regmap 时返回 `-EOPNOTSUPP` 的 ERR_PTR |
| `regulator_get_hardware_vsel_register(regulator, vsel_reg, vsel_mask)` | 拿到电压选择寄存器的地址和掩码 | 给"要在中断上下文/固件里改电压"的场景（如 cpufreq 快速切换） |
| `regulator_list_hardware_vsel(regulator, selector)` | 第 selector 档对应的**寄存器原始值** | 和 `regulator_list_voltage`（返回微伏）配对使用 |
| `regulator_is_equal(reg1, reg2)` | 两个句柄是否指同一路电源 | 返回 bool |
| `regulator_suspend_enable(rdev, state)` | 设"进入某个 suspend 态时要开" | 参数是 **`regulator_dev *`**，是 `consumer.h` 里少数越界的 API（`consumer.h:307`） |
| `regulator_suspend_disable(rdev, state)` | 反之 | 同上（`consumer.h:309`） |
| `regulator_set_suspend_voltage(regulator, min_uV, max_uV, state)` | 设 suspend 态下的电压 | 约束里那一档必须可变，否则 `-EPERM`（`core.c:4264`） |

---

## §3 提供者 API（`driver.h`）

> 头文件：`include/linux/regulator/driver.h`；
> 实现：`drivers/regulator/core.c`（注册与访问器）、
> `drivers/regulator/helpers.c`（现成 ops 实现）、
> `drivers/regulator/irq_helpers.c`（中断助手）、
> `drivers/regulator/devres.c`（devm 变体）。

### §3.1 注册与注销

| API | 说明 | 备注 |
|---|---|---|
| `regulator_register(dev, regulator_desc, config)` | 注册一路 regulator | 返回 `struct regulator_dev *`，失败是 ERR_PTR。一颗 PMIC 有 N 路就调 N 次 |
| `devm_regulator_register(dev, regulator_desc, config)` | devm 版 | **实现在 `devres.c`**，不在 core.c |
| `regulator_unregister(rdev)` | 注销 | 还有消费者持有句柄时不会真的释放（引用计数） |
| `regulator_notifier_call_chain(rdev, event, data)` | 驱动主动发一个事件给所有消费者 | event 取 `REGULATOR_EVENT_*`（`include/linux/regulator/consumer.h:109-131`）—— **驱动侧要用消费者头文件里的常量** |
| `regulator_register_fixed_name(...)` | 用代码造一个固定电压 regulator | 在 `drivers/regulator/fixed-helper.c`（61 行），**不在 `driver.h` 里声明**，给板级代码用 |

**注册需要填的两个结构**（02 篇展开）：

| 结构 | 位置 | 谁提供 | 生命期 |
|---|---|---|---|
| `struct regulator_desc` | `driver.h:361-434` | 驱动，通常是 `static const` | **必须比 rdev 活得久**，核心只存指针 |
| `struct regulator_config` | `driver.h:452-460` | 驱动，栈上临时变量即可 | 只在 `regulator_register` 调用期间有效 |

### §3.2 要填的回调表：`struct regulator_ops`

`struct regulator_ops`（`driver.h:159-244`）是驱动的主要工作量。
**没有一个回调是全体必填的** —— 填哪些取决于硬件能做什么。分组：

| 组 | 典型回调 | 什么时候必须有 |
|---|---|---|
| 开关 | `enable` / `disable` / `is_enabled` | 约束里带 `REGULATOR_CHANGE_STATUS` 时 |
| 电压 | `set_voltage` **或** `set_voltage_sel`；`get_voltage` 或 `get_voltage_sel`；`list_voltage`；`map_voltage` | 约束里带 `REGULATOR_CHANGE_VOLTAGE` 时 |
| 电流 | `set_current_limit` / `get_current_limit` | 约束里带 `REGULATOR_CHANGE_CURRENT` 时 |
| 模式 | `set_mode` / `get_mode` / `get_status` | 约束里带 `REGULATOR_CHANGE_MODE` 时 |
| 负载 | `set_load` **或** `get_optimum_mode` | 约束里带 `REGULATOR_CHANGE_DRMS` 时 |
| bypass | `set_bypass` / `get_bypass` | 约束里带 `REGULATOR_CHANGE_BYPASS` 时 |
| 时序 | `set_ramp_delay` / `set_voltage_time` / `set_voltage_time_sel` / `enable_time` | 电压/开关有建立时间要求时 |
| 保护 | `set_over_current_protection` / `set_over_voltage_protection` / `set_under_voltage_protection` / `set_thermal_protection` | 硬件有保护寄存器时 |
| suspend | `set_suspend_voltage` / `set_suspend_enable` / `set_suspend_disable` / `set_suspend_mode` | 硬件有独立的 suspend 寄存器组时 |

**"约束里带某个 CHANGE 位"和"回调实现了"是两件独立的事**，
两边不匹配的后果是 `-EPERM` 或空指针 —— 这是 03 篇 §B 和 05 篇的重点。

`get_status` 的返回值是 `enum regulator_status`（`driver.h:28-41`），
九个值，注释明确说 `FAST/NORMAL/IDLE/STANDBY` 都是 "flavors of on"
（`driver.h:31`），`BYPASS` 是 "enabled but not regulating"（`driver.h:37`）。

### §3.3 现成的 ops 实现（`helpers.c`）

**这是写 PMIC 驱动最省事的一节**：如果芯片是"regmap + 一个电压选择字段"
这种常规形状，下面这些函数可以**直接填进 `regulator_ops`**，一行代码都不用写。
`helpers.c` 一共导出 28 个（全部实现在 `drivers/regulator/helpers.c`）。

**regmap 直填组**（要求 `regulator_desc` 里填好 `enable_reg`/`vsel_reg` 等字段）：

| API | 填到哪个 ops 字段 | 备注 |
|---|---|---|
| `regulator_is_enabled_regmap(rdev)` | `.is_enabled` | 用 `enable_reg` / `enable_mask` |
| `regulator_enable_regmap(rdev)` | `.enable` | 支持 `enable_is_inverted` |
| `regulator_disable_regmap(rdev)` | `.disable` | |
| `regulator_get_voltage_sel_regmap(rdev)` | `.get_voltage_sel` | 用 `vsel_reg` / `vsel_mask` |
| `regulator_set_voltage_sel_regmap(rdev, sel)` | `.set_voltage_sel` | 会顺带处理 `apply_reg`/`apply_bit`（写完要"确认"的硬件） |
| `regulator_get_voltage_sel_pickable_regmap(rdev)` | `.get_voltage_sel` | "pickable"= 多组区间寄存器（`vsel_range_reg`） |
| `regulator_set_voltage_sel_pickable_regmap(rdev, sel)` | `.set_voltage_sel` | 同上 |
| `regulator_set_bypass_regmap(rdev, enable)` | `.set_bypass` | |
| `regulator_get_bypass_regmap(rdev, enable)` | `.get_bypass` | |
| `regulator_set_soft_start_regmap(rdev)` | `.set_soft_start` | |
| `regulator_set_pull_down_regmap(rdev)` | `.set_pull_down` | |
| `regulator_set_active_discharge_regmap(rdev, enable)` | `.set_active_discharge` | |
| `regulator_set_current_limit_regmap(rdev, min_uA, max_uA)` | `.set_current_limit` | 用 `curr_table` |
| `regulator_get_current_limit_regmap(rdev)` | `.get_current_limit` | |
| `regulator_set_ramp_delay_regmap(rdev, ramp_delay)` | `.set_ramp_delay` | 用 `ramp_reg`/`ramp_mask`/`ramp_delay_table` |
| `regulator_set_voltage_time_sel(rdev, old_selector, new_selector)` | `.set_voltage_time_sel` | 按 `ramp_delay` 算等待时间 |

**电压表/区间组**（不依赖 regmap，只依赖 `regulator_desc` 里的表）：

| API | 填到哪个 ops 字段 | 备注 |
|---|---|---|
| `regulator_list_voltage_linear(rdev, selector)` | `.list_voltage` | 单一线性：`min_uV + selector * uV_step` |
| `regulator_list_voltage_linear_range(rdev, selector)` | `.list_voltage` | 多段线性：用 `linear_ranges` |
| `regulator_list_voltage_pickable_linear_range(rdev, selector)` | `.list_voltage` | 多组寄存器 + 多段线性 |
| `regulator_list_voltage_table(rdev, selector)` | `.list_voltage` | 任意查表：用 `volt_table` |
| `regulator_map_voltage_linear(rdev, min_uV, max_uV)` | `.map_voltage` | 由电压反算 selector |
| `regulator_map_voltage_linear_range(rdev, min_uV, max_uV)` | `.map_voltage` | |
| `regulator_map_voltage_pickable_linear_range(rdev, min_uV, max_uV)` | `.map_voltage` | |
| `regulator_map_voltage_iterate(rdev, min_uV, max_uV)` | `.map_voltage` | 逐档遍历，**O(n)**，给不规则表用 |
| `regulator_map_voltage_ascend(rdev, min_uV, max_uV)` | `.map_voltage` | 要求表**升序**，遍历时可提前退出 |
| `regulator_desc_list_voltage_linear(desc, selector)` | — | 只吃 `desc` **不吃 rdev**：可以在 `regulator_register` **之前**算电压 |
| `regulator_desc_list_voltage_linear_range(desc, selector)` | — | 同上 |
| `regulator_sync_voltage_rdev(rdev)` | — | 驱动侧主动重写电压 |

另外两个也在 `helpers.c` 但属于消费者 API：
`regulator_bulk_set_supply_names`、`regulator_is_equal`（§2.7 / §2.8）。

### §3.4 中断转通知：`irq_helpers.c`

硬件报了一个过流/过温中断，怎么变成消费者收到的通知？
框架提供了一个通用状态机（`drivers/regulator/irq_helpers.c`，439 行，导出 3 个）：

| API | 说明 | 备注 |
|---|---|---|
| `regulator_irq_helper(dev, d, irq, irq_flags, common_errs, per_rdev_errs, rdev, rdev_amount)` | 注册一个"中断→通知"处理器 | 返回 `void *` 句柄（不是 int）；`d` 是 `struct regulator_irq_desc`（`driver.h:562-575`） |
| `devm_regulator_irq_helper(...)` | devm 版 | 实现在 `devres.c` |
| `regulator_irq_helper_cancel(handle)` | 注销 | 参数是 `void **` |
| `regulator_irq_map_event_simple(irq, rid, dev_mask)` | 最简的 `map_event` 实现，可直接填进 `regulator_irq_desc` | 适合"一个中断对应一种错误"的芯片 |

配套结构：`struct regulator_irq_data`（`driver.h:494-499`）、
`struct regulator_err_state`（`driver.h:470-475`）。
严重性分三级 `enum regulator_detection_severity`（`driver.h:43-50`）：
`PROT`（硬件自己已经关输出）/ `ERR`（硬件可能已损坏）/ `WARN`（还能救）。

三级的区别不是"报警声大小"，而是**框架要不要替你关电源**：
注释在 `driver.h:45`/`:47`/`:49`。

### §3.5 访问器与杂项

| API | 说明 | 备注 |
|---|---|---|
| `rdev_get_drvdata(rdev)` | 取驱动私有数据 | 返回 `void *` |
| `rdev_get_dev(rdev)` | 取 `struct device *` | 拿到的是 **regulator 自己那个 device**（class 设备），不是 PMIC 的 |
| `rdev_get_regmap(rdev)` | 取 regmap | |
| `rdev_get_id(rdev)` | 取 `desc->id` | |
| `rdev_get_name(rdev)` | 取名字 | 声明在 `driver.h:775`，**空名字返回 `""` 不是 NULL**（`core.c:108-110`） |
| `regulator_get_init_drvdata(reg_init_data)` | 从 `regulator_init_data` 取 drvdata | 给板级代码用 |
| `regulator_mode_to_status(mode)` | `REGULATOR_MODE_*` → `REGULATOR_STATUS_*` | 给只实现了 `get_mode` 却想支持 `get_status` 的驱动 |

---

## §4 耦合调压器 API（`coupler.h`）

> 头文件：`include/linux/regulator/coupler.h`（100 行）；实现：`drivers/regulator/core.c`。

"耦合"指两路电源的电压必须保持某种关系（典型：CPU 核心电压和 SRAM 电压
压差不能超过 X mV）。DT 上用 `regulator-coupled-with`（06 篇 §5）。

| API | 说明 | 备注 |
|---|---|---|
| `regulator_coupler_register(coupler)` | 注册一个自定义耦合策略 | `struct regulator_coupler`（`coupler.h:41-51`）；一般平台**不需要**，核心自带一个默认策略 |
| `regulator_check_consumers(rdev, min_uV, max_uV, state)` | 汇总所有消费者的电压区间 | 给 coupler 实现用 |
| `regulator_check_voltage(rdev, min_uV, max_uV)` | 按约束校验一个电压区间 | 同上 |
| `regulator_get_voltage_rdev(rdev)` | 不经消费者句柄读电压 | 返回 `-ENOTRECOVERABLE` 表示"这颗硬件读不出来电压"（§5.1） |
| `regulator_set_voltage_rdev(rdev, min_uV, max_uV, state)` | 不经消费者句柄设电压 | |
| `regulator_do_balance_voltage(rdev, state, skip_coupled)` | 跑一遍默认平衡算法 | 自定义 coupler 可以复用它 |

**本树里核心自带的耦合能力有两条硬限制**，都在 `core.c` 里直接返回错误：

1. **只支持两路耦合**：`n_coupled > 2` 时 `-EPERM`，
   日志 `"Voltage balancing for multiple regulator couples is unimplemented"`
   （`core.c:5476-5482`，另一处 `core.c:5340-5347`）
2. **被耦合的 regulator 必须是 `always-on`**：否则 `-ENOTSUPP`，
   日志 `"Coupling of a non always-on regulator is unimplemented"`（`core.c:5484-5488`）

---

## §5 错误码语义

### §5.1 本子系统特有的几个

| 错误码 | 在 regulator 里的特定含义 | 典型出处 |
|---|---|---|
| `-EPERM` | **"约束不允许这个操作"** —— 不是权限问题，是板级 DT 没给这一项授权 | `core.c:499-502`（改电压未授权）、`:564-567`（改电流）、`:598-601`（改模式）、`:2926`（改开关状态） |
| | 也用于"已被独占" | `core.c:2245` |
| | 也用于耦合的两条限制 | `core.c:5347` / `:5481`（见 §4） |
| | 也用于 suspend 那一档不可改 | `core.c:4217` / `:4264` |
| `-ENOTRECOVERABLE` | **"这颗硬件的电压根本读不出来"**，不是"出错了" | `core.c:1258` 专门为它写了一条分支：先按约束写一次电压，再重读 |
| `-ENOTSUPP` | 只用在耦合的 always-on 限制上 | `core.c:5487`（注意与 `-EOPNOTSUPP` 混用，见 §5.4） |
| `-EDEADLK` / `-EDEADLOCK` | 锁竞争，**需要调用方重试** | `core.c:164`/`:231`/`:249`/`:338`/`:420`/`:6073`/`:6091`/`:6144` —— 核心自己有 `while (err == -EDEADLK)` 的重试循环（`:420`/`:6144`） |
| `-ERANGE` | 请求的区间和约束区间**没有交集** | `core.c:785` |
| `-ETIMEDOUT` | 等硬件到达目标电压超时 | `core.c:2817`，注释在 `:2795` |
| `-EPROBE_DEFER` | 供电方还没注册上来 | 18 处，最关键的是 `core.c:2036`（查找失败）、`:2229`（dummy 还没就绪） |

数量分布（`core.c` 一共 6310 行）：`-EINVAL` 56 次、`-EPROBE_DEFER` 18 次、
`-EPERM` 11 次、`-ENOMEM` 11 次、`-EOPNOTSUPP` 9 次、`-ENODEV` 7 次、`-EDEADLK`/`-EDEADLOCK` 8 次。
复现：`/usr/bin/grep -o '\-E[A-Z]*' drivers/regulator/core.c | sort | uniq -c | sort -rn`

### §5.2 最需要知道的一条：`regulator_get` 在 arm64 上几乎不会失败

`regulator_get` 找不到 supply 时，会**返回一个 dummy regulator**
而不是错误，条件是 `have_full_constraints()` 为真（`core.c:2214`）。
而这个函数的实现是（`core.c:112-115`）：

```c
static bool have_full_constraints(void)
{
	return has_full_constraints || of_have_populated_dt();
}
```

**`of_have_populated_dt()` 在所有 arm64 平台上都是真** ——
所以 arm64 上这个条件**恒成立**，`regulator_get` 拿不到真 supply 时：

| 用的哪个 API | 结果 |
|---|---|
| `regulator_get` / `devm_regulator_get` | 返回 **dummy**，只打一条 `dev_warn`：`"supply %s not found, using dummy regulator"`（`core.c:2232`） |
| `regulator_get_optional` / `devm_regulator_get_optional` | 返回 `-ENODEV`（走 `default` 分支，`core.c:2244`） |
| `regulator_get_exclusive` | 返回 `-ENODEV`，另打一条 `"dummy supplies not allowed for exclusive requests"`（`core.c:2239-2240`） |

**后果**：DT 里漏写一条 `xxx-supply`，驱动**照样 probe 成功、照样"enable 成功"**，
只是硬件那一路电压从来没被打开过。症状是"设备行为异常"而不是"probe 失败"。
唯一的线索就是那条 `dev_warn`。查法见 07 篇 §2 和 06 篇 §7。

另一个入口：`regulator_has_full_constraints()`（`core.c:5876-5879`）
把 `has_full_constraints` 置 1。它的 kerneldoc（`core.c:5866-5875`）说的是
另一件事 —— **late_initcall 里关掉所有"没人用且不是 always-on"的 regulator**，
实现见 `core.c:6236`。

### §5.3 `CONFIG_REGULATOR=n` 时的返回值

`consumer.h` 从 `:330` 起是一整套 `static inline` 桩。**它们不是全返回 0**：

| 桩函数 | 返回 | 含义 |
|---|---|---|
| `regulator_get` / `devm_regulator_get` | 一个**非 NULL 的假指针** | 让调用者的 `IS_ERR` 检查通过，后续操作全部变成空操作 |
| `regulator_get_optional` / `_exclusive` 系列 | `-ENODEV` 的 ERR_PTR | 语义保持一致：optional 仍然"找不到" |
| `regulator_enable` / `_disable` / `_bulk_*` | 0 | 假装成功 |
| `regulator_is_enabled` | 0 | 假装"关着" |
| `regulator_get_voltage` / `_set_voltage` / `_set_mode` 等 | `-EINVAL` 或 `-EOPNOTSUPP` | **不是 0** —— 驱动如果不检查返回值，`REGULATOR=n` 时会拿到垃圾 |

计数：`consumer.h` 的桩里出现 `-EINVAL` 7 次、`-ENODEV` 4 次、`-EOPNOTSUPP` 3 次。

### §5.4 一个不一致（记入 99 §6）

`-ENOTSUPP`（`core.c:5487`）和 `-EOPNOTSUPP`（9 处）在同一个文件里混用。
二者数值不同（`ENOTSUPP` 是 524，只在内核内部使用，
`include/linux/errno.h`；`EOPNOTSUPP` 是 95，是 POSIX 的）。
耦合失败返回的是内部值 `-ENOTSUPP`，
如果被一路传到用户态（sysfs / ioctl）会变成一个 **用户态无法解释的错误号**。

---

## §6 板级 API（`machine.h`）

> 头文件：`include/linux/regulator/machine.h`（292 行）。
> arm64 上绝大多数内容由 DT 填（06 篇），这里只列**代码里仍会用到的**。

| API / 宏 | 说明 | 备注 |
|---|---|---|
| `regulator_has_full_constraints()` | 声明"本系统的约束是完整的" | 后果见 §5.2 末段：late_initcall 会关掉所有没人用的 regulator |
| `REGULATOR_SUPPLY(_name, _dev_name)` | 构造一条 `struct regulator_consumer_supply` | `machine.h:239`；只给非 DT 平台用 |

关键结构（02 篇展开）：

| 结构 | 位置 | 说明 |
|---|---|---|
| `struct regulation_constraints` | `machine.h:157-222` | 板级约束的全部内容，`of_regulator.c` 就是把 DT 翻译成它 |
| `struct regulator_init_data` | `machine.h:262-273` | 约束 + 消费者列表 |
| `struct regulator_state` | `machine.h:77-84` | 单个 suspend 态下的目标状态 |
| `struct notification_limit` | `machine.h:88-92` | 过压/过流/过温的三级阈值 |
| `struct regulator_consumer_supply` | `machine.h:233-236` | 非 DT 平台的 supply 映射表项 |

常量三组：

| 组 | 位置 | 值 |
|---|---|---|
| 允许改什么（`valid_ops_mask`） | `machine.h:35-40` | `REGULATOR_CHANGE_VOLTAGE` / `CURRENT` / `MODE` / `STATUS` / `DRMS` / `BYPASS` |
| suspend 行为 | `machine.h:48-50` | `DO_NOTHING_IN_SUSPEND` / `DISABLE_IN_SUSPEND` / `ENABLE_IN_SUSPEND` |
| 通知阈值特殊值 | `machine.h:86-87` | `REGULATOR_NOTIF_LIMIT_DISABLE`（-1）/ `REGULATOR_NOTIF_LIMIT_ENABLE`（-2） |

`valid_ops_mask` 是 §5.1 里那一串 `-EPERM` 的**唯一来源** ——
DT 里没写 `regulator-allow-set-load`，`regulator_set_load` 就永远 `-EPERM`。
这条对应关系在 06 篇 §2 有完整的属性→位表。

---

## §7 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 这些结构体的字段全貌和相互关系 | `02-datastructures.md` |
| 注册、get、enable、set_voltage 的逐段代码 | `03-internals.md` |
| 耦合、irq helper、dummy、userspace consumer 这些可选层 | `04-extensions.md` |
| 照着写一个消费者 / PMIC 驱动 | `05-usage.md` |
| DTS 怎么写、写错怎么查 | `06-devicetree.md` |
| 日志、sysfs、debugfs 在哪看 | `07-debug.md` |
| 为什么是这样设计的（大白话） | `08-feynman.md` |
| 全貌速览 / 复习入口 | `00-overview.md` |
