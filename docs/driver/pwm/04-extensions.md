---
title: 扩展机制
---

# pwm 04：子框架与扩展面

## §0 组织轴与一个诚实的开场

**组织轴：一节一个独立框架，互不调用。** 每节交代它挂在
[03 篇](03-internals.md) 时间线的哪一点。

但 pwm 的情况需要先说清楚一件事：

⚠️ **pwm 没有 mux / slave / target 这类子框架。**
`drivers/pwm/` 下 64 个 `.c` 里，除了 `core.c`（1188 行）和
`sysfs.c`（546 行），**其余 62 个全部是控制器驱动**，
不构成任何中间层。所以本篇的"扩展面"实际由三块完全不同性质的东西组成：

| 块 | 是什么 | 是不是真框架 | 节 |
|---|---|---|---|
| **sysfs/class 层** | 核心内的可裁分层（`CONFIG_PWM_SYSFS`），546 行 | ✅ **是**，唯一的 | §1 |
| **消费者侧再封装** | backlight / leds / regulator / hwmon / clk / input / rc 各自把 PWM 包成自己子系统的对象 | ❌ 不是 pwm 的子框架，是**别的子系统的驱动** | §2 · §3 |
| **住在别处的 provider** | 4 个不在 `drivers/pwm/` 里却调 `pwmchip_add()` 的驱动 | ❌ 是 provider 的特例 | §4 |

⇒ **本篇的真实价值不在"框架清单"，而在两件事**：
（a）把唯一的可裁层讲透（§1）；
（b）用**全树使用统计**回答"这个框架的每个能力到底有没有人用"——
其中两个能力的答案是"几乎没有"（§5），这是读文档读不出来的。

### §0.1 arm64 分诊表

| 块 | arm64 上会碰到吗 | 依据 |
|---|---|---|
| sysfs/class 层 | ✅ **几乎一定**（`CONFIG_PWM_SYSFS` 在有 SYSFS 时 `default y`，`drivers/pwm/Kconfig:32-34`） | 默认开 |
| pwm-backlight | ✅ **最常见**，`pwms =` 的 112 个 arm64 DTS 里占比最大 | 07 篇/06 篇 |
| leds-pwm / leds-pwm-multicolor | ✅ 常见 | — |
| pwm-regulator | ✅ Rockchip 板子上常见（PWM 调压） | — |
| pwm-fan | ✅ 常见（带 thermal cooling device） | — |
| clk-pwm | ⚠️ 少见但存在 | — |
| pwm-beeper / pwm-vibra / pwm-ir-tx | ⚠️ 少见，且**都是时序敏感型**（§3.6） | — |
| ts-nbus | ❌ arm32 (TS-4600) 专用 | §3.7 |
| `.capture` 能力 | ❌ **arm64 上零 provider**（§5.1） | 2 个实现都是 arm32 |
| `usage_power` 能力 | ❌ **全树只有 1 个 provider 读它，0 个 consumer 设它**（§5.2） | — |
| i915 背光 / `pwm-lpss` / `pwm-crc` | ❌ x86 专用，本文档集裁掉 | 99 §2.7 |

---

## §1 sysfs/class 层（`drivers/pwm/sysfs.c`，546 行）

### §1.1 它是什么，挂在哪

一句话：**把每个 `pwm_chip` 变成 `/sys/class/pwm/pwmchipN/` 目录，
把每条被 export 的通道变成 `pwmchipN/pwmX/` 子目录，
让用户态不写代码就能操作 PWM。**

挂载点（对应 03 篇时间线）：

| 时机 | 调用 | 位置 |
|---|---|---|
| `pwmchip_add()` **第 8 步** | `pwmchip_sysfs_export(chip)` | `drivers/pwm/sysfs.c:505` |
| `pwmchip_remove()` **第 1 步** | `pwmchip_sysfs_unexport(chip)` | `drivers/pwm/sysfs.c:521` |
| 子系统起来时 | `class_register(&pwm_class)` | `drivers/pwm/sysfs.c:544`（`subsys_initcall` 在 `:546`） |

⚠️ **它是唯一一个"核心主动回调进来"的分层**。
`core.c` 直接调这两个函数（`CONFIG_PWM_SYSFS=n` 时是
`include/linux/pwm.h` 里的空桩），所以 `sysfs.c` 里
**0 个 `EXPORT_SYMBOL`**——它不对外，只对核心。

### §1.2 546 行怎么分层

从符号表看，它是**三层结构**：

```
层 3  class 级：  pwm_class（:493）+ PM ops（:491）+ init（:542）
层 2  chip 级：   pwmchipN/ 的 3 个属性 + export/unexport 逻辑
层 1  channel 级：pwmX/ 的 5 个属性 + pwm_export 生命周期
```

| 层 | 符号 | 位置 |
|---|---|---|
| **channel 属性** | `period_show/store` | `:36` / `:48` |
| | `duty_cycle_show/store` | `:71` / `:83` |
| | `enable_show/store` | `:106` / `:118` |
| | `polarity_show/store` | `:154` / `:177` |
| | `capture_show`（**只读**） | `:203` |
| | `pwm_attrs[]` / `ATTRIBUTE_GROUPS(pwm)` | `:224` / `:232` |
| **channel 生命周期** | `pwm_export_release` | `:234` |
| | `pwm_export_child` | `:241` |
| | `pwm_unexport_match` / `pwm_unexport_child` | `:280` / `:285` |
| **chip 属性** | `export_store`（**只写**） | `:310` |
| | `unexport_store`（**只写**） | `:338` |
| | `npwm_show`（**只读**） | `:359` |
| | `pwm_chip_attrs[]` / `ATTRIBUTE_GROUPS(pwm_chip)` | `:368` / `:374` |
| **PM** | `pwm_class_get_state` | `:377` |
| | `pwm_class_apply_state` | `:400` |
| | `pwm_class_resume_npwm` | `:412` |
| | `pwm_class_suspend` / `pwm_class_resume` | `:443` / `:484` |
| **class** | `pwm_class` | `:493` |
| | `pwmchip_sysfs_match` | `:500` |
| | `pwmchip_sysfs_export` / `_unexport` | `:505` / `:521` |
| | `pwm_sysfs_init` | `:542` |

两个辅助访问器把 `struct device *child` 换回对象：
`child_to_pwm_export()`（`:24`）和 `child_to_pwm_device()`（`:29`）。
这是 sysfs 层的标准套路——**所有属性函数拿到的都是 `child`，
第一行必然是这两个之一**。

### §1.3 八个属性一览

**chip 级 3 个**（在 `pwmchipN/` 下）：

| 属性 | 权限 | 语义 |
|---|---|---|
| `npwm` | R | `chip->npwm`，本控制器通道数 |
| `export` | **W only** | 写 hwpwm 编号 → 创建 `pwmX/` |
| `unexport` | **W only** | 写 hwpwm 编号 → 删除 `pwmX/` |

**channel 级 5 个**（在 `pwmchipN/pwmX/` 下）：

| 属性 | 权限 | 语义 | 官方 rst 有没有写 |
|---|---|---|---|
| `period` | RW | 纳秒 | ✅ `pwm.rst:110` |
| `duty_cycle` | RW | 纳秒，必须 < period | ✅ `pwm.rst:115` |
| `polarity` | RW | 字符串 `normal` / `inversed` | ✅ `pwm.rst:119` |
| `enable` | RW | `0` / `1` | ✅ `pwm.rst:125` |
| `capture` | **R only** | ⚠️ **读它会触发一次硬件测量** | ❌ **没写**（03 §C2.5） |

### §1.4 PM 只覆盖 export 出来的通道

`pwm_class_suspend()`（`:443`）遍历 `npwm` 条通道，
只处理**已 export** 的（靠 `pwm_class_get_state()` `:377` 取，
拿不到就跳过），把 state 存进 `pwm_export->suspend`（02 §8）；
`pwm_class_resume()`（`:484`）经 `pwm_class_resume_npwm()`（`:412`）
和 `pwm_class_apply_state()`（`:400`）恢复。
挂在 `DEFINE_SIMPLE_DEV_PM_OPS(pwm_class_pm_ops, ...)`（`:491`）上，
即 **class 的 PM，不是 chip 驱动的 PM**。

⇒ 03 §A10 的结论在这里得到解释：**内核消费者的通道没有任何 PM 处理**，
因为它们不在 `pwm_export` 列表里。

### §1.5 五条陷阱

1. ⚠️ **`pwmchipN` 的 N 是全局 `base`，`pwmX` 的 X 是 `hwpwm`。**
   `pwmchip_sysfs_export()` 用 `dev_set_name(..., "pwmchip%d", chip->base)`
   （`:514`），而 `pwm_export_child()` 用 `"pwm%u", pwm->hwpwm`（`:263`）。
   **同一条路径上两套编号**（03 §B2、02 §1.0.C）。
2. ⚠️ **`export` 会失败于两个不同原因，errno 相同。**
   `export_store()`（`:310`）先 `pwm_request_from_chip()`（`:326`）
   再 `pwm_export_child()`（`:330`），前者被 `PWMF_REQUESTED` 挡
   （内核驱动已占用），后者被 `PWMF_EXPORTED` 挡（已 export 过），
   **两者都返回 `-EBUSY`**（03 §A9.1）。
3. ⚠️ **`unexport` 不停输出。** `pwm_unexport_child()`（`:285`）
   只做 `pwm_put()`（`:305`），而 `pwm_put()` 本身不 disable（03 §A8）。
   ⇒ 用 sysfs 点亮的 LED，unexport 之后还亮着，且再也没有句柄能关它
   （只能重新 export）。
4. ⚠️ **`capture` 有读副作用且默认必然报错。**
   `capture_show()`（`:203`）调 `pwm_capture()`，
   后者在无 `.capture` 时返回 `-ENOSYS`（03 §C2.3），
   arm64 上没有任何 provider 实现它（§5.1）
   ⇒ **`cat capture` 在 arm64 上恒定失败，且 errno 是"系统调用未实现"**。
5. ⚠️ **`polarity` 只能在未 enable 时改**（`pwm.rst:122-123`）。
   `polarity_store()`（`:177`）会拒绝 enabled 状态下的修改。

### §1.6 全树使用统计

| 项 | 数字 | 重跑命令 |
|---|---|---|
| 导出符号 | **0** | `grep -c EXPORT_SYMBOL drivers/pwm/sysfs.c` |
| 被核心调用的入口 | **2**（`pwmchip_sysfs_export` / `_unexport`） | `grep -n 'pwmchip_sysfs_' drivers/pwm/core.c` |
| 通道属性 | **5** | `sed -n '224,231p' drivers/pwm/sysfs.c` |
| chip 属性 | **3** | `sed -n '368,373p' drivers/pwm/sysfs.c` |
| 可否编译裁掉 | ✅ `CONFIG_PWM_SYSFS=n`（`drivers/pwm/Kconfig:32-34`），核心侧留空桩 | — |

---

## §2 消费者侧的分布表

**先给分布，再挑代表。** 判据统一用"包含 `#include <linux/pwm.h>`"，
这是唯一不会被同名局部符号污染的门（`pwm_enable(` 之类会在 hwmon
里大量假命中——那些是它们自己的 `pwm1_enable` sysfs 属性）。

```bash
grep -rln --include=*.c --include=*.h '#include <linux/pwm.h>' . | wc -l      # 115
grep -rln --include=*.c --include=*.h '#include <linux/pwm.h>' . \
    | grep -v '^./drivers/pwm/' | wc -l                                       # 53
```

115 个文件里：

| 分区 | 文件数 | 说明 |
|---|---|---|
| `drivers/pwm/` | **62** | provider + 核心（§1 与 03 篇的对象） |
| `arch/arm/mach-pxa/` + `mach-s3c/` | **22** | ⚠️ **arm32 板级文件**，用的是静态查表路径（03 §A3.3），arm64 无对应物，本篇裁掉 |
| `include/` 三个头 | **3** | `include/trace/events/pwm.h` · `include/linux/mfd/lp3943.h` · `include/linux/mfd/lp8788.h` |
| **真正的跨子系统消费者** | **28** | 下表 |

28 个消费者按顶层分区：

| 分区 | 文件数 | 文件 | arm64 相关 |
|---|---|---|---|
| `drivers/video/backlight/` | **4** | `pwm_bl.c`（707 行，代表）· `lm3630a_bl.c` · `lp855x_bl.c` · `lp8788_bl.c` | ✅ |
| `drivers/input/misc/` | **5** | `pwm-beeper.c` · `pwm-vibra.c` · `da7280.c` · `max77693-haptic.c` · `max8997_haptic.c` | ⚠️ |
| `drivers/gpu/drm/` | **5** | `i915/display/`3 个（**x86，裁**）· `bridge/ti-sn65dsi86.c`（是 provider，§4）· `solomon/ssd130x.c` | 部分 |
| `drivers/leds/` | **3** | `leds-pwm.c` · `rgb/leds-pwm-multicolor.c` · `rgb/leds-qcom-lpg.c`（是 provider，§4） | ✅ |
| `drivers/media/rc/` | **2** | `pwm-ir-tx.c` · `ir-rx51.c` | ⚠️ |
| `drivers/hwmon/` | **1** | `pwm-fan.c`（682 行） | ✅ |
| `drivers/regulator/` | **1** | `pwm-regulator.c`（409 行） | ✅ |
| `drivers/clk/` | **1** | `clk-pwm.c`（158 行） | ⚠️ |
| `drivers/bus/` | **1** | `ts-nbus.c`（366 行） | ❌ arm32 |
| `drivers/gpio/` | **1** | `gpio-mvebu.c`（是 provider，§4） | ✅ |
| `drivers/staging/greybus/` | **1** | `pwm.c`（是 provider，§4） | ⚠️ |
| `drivers/video/fbdev/` | **1** | `ssd1307fb.c` | ⚠️ |
| `drivers/acpi/` | **1** | `acpi_lpss.c` ⇒ **ACPI，裁掉**（99 §2.7） | ❌ |
| `drivers/mfd/` | **1** | `intel_soc_pmic_crc.c` ⇒ x86 | ❌ |

⚠️ **三个口径别混**：顶层分区 **13 个**说明 PWM 铺到内核哪些大区，
文件数 **28** 是消费者实现数的上界，
其中 **4 个其实是 provider**（§4）⇒ **纯消费者是 24 个**。

---

## §3 代表消费者逐个展开

选法：**每种"PWM 被当成什么"选一个代表**——
亮度 / 电压 / 转速 / 时钟 / 波形。挂载点全部是 03 §A3（发现）
+ 03 §A5（配置），所以下面只标各自的**特殊动作**。

### §3.1 pwm-backlight：最标准的消费者（`drivers/video/backlight/pwm_bl.c`，707 行）

probe 里的四步（这是**其他消费者应该照抄的模板**）：

| 步 | 调用 | 位置 |
|---|---|---|
| 1 取句柄 | `devm_pwm_get(&pdev->dev, NULL)` | `:508` |
| 2 建初始 state | `pwm_init_state(pb->pwm, &state)` | `:525` |
| 3 应用 | `pwm_apply_state(pb->pwm, &state)` | `:536` |
| 4 读回做初值 | `pwm_get_state(pb->pwm, &state)` | `:571` |

运行期的三条路径都遵循**"读—改—写"**：
`pwm_get_state()`（`:47` / `:71` / `:95` / `:118`）
→ 改 `duty_cycle`/`enabled` → `pwm_apply_state()`（`:56` / `:83` / `:120`）。

⚠️ 三条要点：

1. **它用 `pwm_init_state()` 而不是 `pwm_apply_args()`。**
   前者只填 4 个字段、不动 `enabled`（02 §4.3）；后者会**关掉输出**
   （03 §C3）。背光驱动不能在 probe 里闪一下黑，所以选前者。
2. **`con_id` 传 `NULL`**（`:508`）⇒ 走 `of_pwm_get()` 的 index 路径，
   DTS 只要 `pwms = <&pwm 0 5000000 0>;`，**不需要 `pwm-names`**
   （03 §A3.1；这也解释了为什么 112 个 arm64 DTS 里只有 4 个写了 `pwm-names`）。
3. **"读—改—写"是绕不开的**，因为 `pwm_state` 是整体应用的（03 §C5）：
   只想改亮度也必须先把 period/polarity 读出来一起写回。

其余三个 backlight 驱动（`lm3630a_bl.c` / `lp855x_bl.c` / `lp8788_bl.c`）
是**芯片自带调光 + PWM 作为可选输入**的混合型，
其中 `drivers/video/backlight/lp8788_bl.c:149` 还在用 `pwm_apply_args()`
（见 §3.8 的 FIXME 群）。

### §3.2 leds-pwm：把 state 缓存在自己结构里（`drivers/leds/leds-pwm.c`，217 行）

亮度设置只有一行（`:57`）：

```c
return pwm_apply_state(led_dat->pwm, &led_dat->pwmstate);
```

⇒ **它把 `pwm_state` 常驻在自己的 `led_data` 里**，
改一个字段就整体 apply，不每次读回。初始化时二选一（`:85` / `:94`）：
有 `period` 配置就 `pwm_get_state()` 取，否则 `pwm_init_state()`。

`drivers/leds/rgb/leds-pwm-multicolor.c`（193 行）是它的多通道版：
`fwnode_for_each_child_node()`（`:75`）为每个子节点取一条 PWM，
各自 `pwm_init_state()`（`:82`），
设置时循环 `pwm_apply_state()`（`:54`）。

⚠️ **多通道 apply 不是原子的**：N 条通道要 N 次
`pwm_apply_state()`，中间会出现"红色已更新、绿色还没更新"的中间色。
RGB 快速切换时能看到，**这是 pwm 框架层面无解的**（没有多通道原子 apply）。

### §3.3 pwm-regulator：唯一用 `pwm_adjust_config()` 的消费者（`drivers/regulator/pwm-regulator.c`，409 行）

两种工作模式，代码是两套：

| 模式 | 语义 | 关键行 |
|---|---|---|
| **voltage-table** | DTS 给一张"占空比 → 电压"表，查表 | `set_voltage_sel`：`pwm_init_state`（`:89`）+ `pwm_set_relative_duty_cycle`（`:90`）+ `pwm_apply_state`（`:93`）；`get_voltage_sel`：`pwm_get_state`（`:61`）+ `pwm_get_relative_duty_cycle`（`:62`） |
| **continuous** | 线性插值，任意电压 | `set_voltage`：`pwm_init_state`（`:199`）+ `pwm_set_relative_duty_cycle`（`:220`）+ `pwm_apply_state`（`:222`）；`get_voltage`：`:158` + `:160` |

⚠️ **这是全树唯一调 `pwm_adjust_config()` 的地方**（`:374`）：

```bash
grep -rn --include=*.c 'pwm_adjust_config' . | grep -v '^./drivers/pwm/core.c'
# → drivers/regulator/pwm-regulator.c   （仅此一处）
```

原因正是 03 §A6.1 说的场景：**PWM 调压器在 bootloader 里就已经在工作了**，
内核接管时**绝对不能把 duty 清零**（那等于把核心电压瞬间归零）。
所以它必须调 `pwm_adjust_config()` 而不是自己 `pwm_init_state()`。

⚠️⚠️ **而这恰好暴露了 03 §C1.2 的缺口的真实代价**：
`pwm_adjust_config()` 在 provider **没有 `.get_state`** 时会走
`period == 0` 分支把 duty 清零（03 §A6.1）
⇒ **给 PWM regulator 配一个没实现 `.get_state` 的控制器，
理论上会在 probe 时把电压拉到 0**。这是 pwm 里后果最严重的一条陷阱，
也是为什么 `pwm_ops_check()` 会（仅在 `CONFIG_PWM_DEBUG` 下）
抱怨缺 `.get_state`（03 §B4.3）。

它还是**少数用 `pwm_enable()` / `pwm_disable()` 这对老包装的消费者**
（`:121` / `:128`）——那两个是 `include/linux/pwm.h:351` / `:370` 的 inline，
内部转成 `pwm_apply_state()`。

### §3.4 pwm-fan：接进 thermal 框架（`drivers/hwmon/pwm-fan.c`，682 行）

它同时是 **hwmon 设备**和 **thermal cooling device**：

| 面 | 符号 | 位置 |
|---|---|---|
| thermal cooling ops | `pwm_fan_cooling_ops` | `:418` |
| | `pwm_fan_get_max_state` / `_get_cur_state` / `_set_cur_state` | `:369` / `:382` / `:396` |
| 注册 | `devm_thermal_of_cooling_device_register` | `:623` |
| PWM 侧 | `devm_pwm_get`（`:494`）· `pwm_init_state`（`:508`）· `set_pwm` 里 `pwm_apply_state`（`:210`） | — |

⚠️ 两条它独有的、值得学的防御：

1. **`MAX_PWM 255`**（`:22`）——**它自己定义了一个 0-255 的抽象刻度**，
   因为 hwmon 的 `pwm1` ABI 是 0-255，而 pwm 框架用纳秒。
   换算在 `:209`：`duty_cycle = DIV_ROUND_UP(pwm * (period - 1), MAX_PWM)`。
   ⚠️ 注意是 `period - 1`：**保证 255 时 duty < period**，
   躲开 `pwm_apply_state()` 的 `duty_cycle > period → -EINVAL` 门（03 §A5.1）。
2. ⚠️ **它显式检查乘法溢出**（`:511-516`）：

   ```c
   /* set_pwm assumes that MAX_PWM * (period - 1) fits into an unsigned long */
   if (ctx->pwm_state.period > ULONG_MAX / MAX_PWM + 1)
   ```

   ⇒ **这是 02 §11.3 那个类型宽度问题的下游后果**：`pwm_state.period` 是
   `u64`，而这个驱动的换算走 `unsigned long`。
   在 32 位上 period 超过约 16.8 ms 就会溢出，所以它 probe 时直接拒绝。
   **arm64 上 `unsigned long` 是 64 位，这个检查基本不会触发**，
   但它是"消费者必须自己盯住时间宽度"的活证据。

### §3.5 clk-pwm：把 PWM 当固定频率时钟源（`drivers/clk/clk-pwm.c`，158 行）

最反直觉的一个消费者：**它不关心占空比，只关心频率**，
把一条 PWM 注册成 clk 框架里的一个 `clk_hw`（`devm_clk_hw_register`，`:125`）。

关键逻辑：

```c
pwm_get_args(pwm, &pargs);                              /* :85  */
if (!pargs.period) { "invalid PWM period"; }            /* :86-87 */
clk_pwm->fixed_rate = div64_u64(NSEC_PER_SEC, pargs.period);   /* :92 */
...
ret = pwm_config(pwm, (pargs.period + 1) >> 1, pargs.period);  /* :111 */
```

⚠️ 四条值得记的：

1. **它读的是 `pwm_args` 而不是 `pwm_state`**（`:85`）——
   即"DTS 里写的板级参考周期"，不是当前硬件状态。
   这是 `pwm_args` 的教科书用法（02 §4.2）。
2. **`period == 0` 被当成配置错误**（`:86-87`）⇒ 与
   `pwm_apply_state()` 的 `-EINVAL` 门一致（03 §A5.1）。
3. **占空比硬编码 50%**（`:111` 的 `(period + 1) >> 1`）——
   时钟信号要方波。用 `+1` 再右移是**向上取整**，
   保证 period 为奇数纳秒时 duty 不为 0。
4. **它还实现了 `.get_duty_cycle`**（`clk_pwm_get_duty_cycle`，`:47`，
   挂在 `:64` 的 ops 上），内部 `pwm_get_state()`（`:52`）
   并把 `state.period` 当分母（`:55`）
   ⇒ ⚠️ **它把"上次请求值"当成了硬件真值上报给 clk 框架**，
   这正是 03 §C1.2 的缺口在跨子系统边界上的传播。

### §3.6 时序敏感三例：beeper / vibra / ir-tx

这三个把 PWM 当**波形发生器**用，共同点是**对波形形状敏感**，
所以它们是 `usage_power` 绝对不能被打开的场景（§5.2）。

**`drivers/input/misc/pwm-beeper.c`（262 行）** —— 蜂鸣器，
频率即音高。`pwm_beeper_event()`（`:80`）响应 `EV_SND`
（能力在 `:187-188` 声明为 `SND_TONE` + `SND_BELL`），
`pwm_get_state`（`:36`）→ 改 period → `pwm_apply_state`（`:42`）。
⚠️ **音高靠改 period 实现**，所以 provider 的周期取整误差
直接变成音准误差——而消费者读不回实际周期（03 §C1.2）。

**`drivers/input/misc/pwm-vibra.c`（270 行）** —— 振动马达，同构。

**`drivers/media/rc/pwm-ir-tx.c`（130 行）** —— 红外发射，最苛刻：

```c
pwm_init_state(pwm, &state);                                /* :61 */
pwm_set_relative_duty_cycle(&state, pwm_ir->duty_cycle, 100);  /* :64 */
...
pwm_apply_state(pwm, &state);   /* :70 开 */
pwm_apply_state(pwm, &state);   /* :79 关 */
```

⚠️ **它在发送循环里反复 apply 来产生载波的开/关**
（`:70` 和 `:79` 一对）。这意味着：

- **`pwm_apply_state()` 的全等短路（03 §A5.2）在这里是致命的**——
  如果两次 apply 的五个字段完全相同，第二次直接 `return 0`，
  载波不会被关掉。这个驱动靠**改 `enabled`** 来保证字段不全等，
  躲过了短路，但这是"必须知道核心有短路"才写得对的代码。
- **`pwm_apply_state()` 里有 `might_sleep()`**（`core.c:558`）
  ⇒ 这个循环不能在原子上下文跑，红外时序精度受调度影响。

### §3.7 ts-nbus：把 PWM 当并行总线时钟（`drivers/bus/ts-nbus.c`，366 行）

Technologic Systems TS-4600（**arm32**）上的一条私有并行总线，
**用一条 PWM 给总线打拍**。列在这里只为一件事：
它是 §3.8 那个 FIXME 群的成员之一（`:306-309`）。

⚠️ arm64 上碰不到，但它说明**PWM 在树里被用作"任意方波源"**，
不限于亮度/转速这类模拟量。

### §3.8 一个跨五个驱动的相同 FIXME

```bash
grep -rn --include=*.c 'pwm_apply_args' . | grep -v drivers/pwm/
```

**5 处，全部带同一条注释**（首行都是
`FIXME: pwm_apply_args() should be removed when switching to`）：

| 文件 | 注释行 | 调用行 |
|---|---|---|
| `drivers/video/backlight/lp8788_bl.c` | `:146` | `:149` |
| `drivers/clk/clk-pwm.c` | `:107` | `:110` |
| `drivers/bus/ts-nbus.c` | `:306` | `:309` |
| `drivers/input/misc/max77693-haptic.c` | `:334` | `:337` |
| `drivers/input/misc/max8997_haptic.c` | `:292` | `:295` |

完整意思是"切到原子 PWM API 后应当删掉 `pwm_apply_args()`"。

⚠️ **这是 pwm 生态里唯一成规模的自陈设计债，而它全部在消费者侧。**
03 §C 说过核心（`core.c` + `sysfs.c` + `include/linux/pwm.h`）里
`FIXME/TODO/XXX` **一处都没有**——两条结论不冲突，
**但合起来才是完整图景：债被推到了消费者身上**。

⇒ 写新消费者的判据很清楚：**不要用 `pwm_apply_args()`**，
用 `pwm_init_state()`（不动输出）或 `pwm_adjust_config()`（接管现有配置）。
`pwm_apply_args()` 是 `include/linux/pwm.h:522` 的 inline、
**没有 `EXPORT_SYMBOL`**，且会把 `enabled` 设成 false（02 §11.5）。

---

## §4 反方向：住在别的子系统里的 provider

`pwmchip_add()` / `devm_pwmchip_add()` 在 `drivers/pwm/` 之外
**有 4 个调用点**：

```bash
grep -rn --include=*.c -E 'devm_pwmchip_add|pwmchip_add' . | grep -v '^./drivers/pwm/'
```

| 文件 | 行 | 主职 | 为什么顺手做了 provider | arm64 |
|---|---|---|---|---|
| `drivers/gpio/gpio-mvebu.c` | `:877` | Marvell GPIO 控制器 | 同一块寄存器既能做 GPIO 又能做 PWM ⇒ **一个驱动注册两个子系统** | ✅ |
| `drivers/leds/rgb/leds-qcom-lpg.c` | `:1040` | 高通 LPG（LED 图案发生器） | 硬件本质是 PWM 阵列，**既暴露 LED 接口也暴露 PWM 接口** | ✅ |
| `drivers/gpu/drm/bridge/ti-sn65dsi86.c` | `:1626` | eDP 桥片 | 桥片自带一路 PWM 供背光用 ⇒ **背光驱动可以把它当普通 PWM 消费** | ✅ |
| `drivers/staging/greybus/pwm.c` | `:302` | Greybus（模块化手机总线） | **把远端模块上的 PWM 桥进本机** | ⚠️ staging |

⚠️ 三条实践含义：

1. **只 grep `drivers/pwm/` 会漏掉 4 个 provider。**
   查"某块板子上有哪些 PWM 控制器"必须全树 grep。
2. **这 4 个都用不到 `drivers/pwm/Kconfig`**，
   它们的 PWM 能力由各自子系统的 Kconfig 控制
   ⇒ ⚠️ **`CONFIG_PWM=n` 时它们的编译要么失败要么静默丢功能**，
   取决于各自有没有 `#ifdef`。这正是 `include/linux/pwm.h` 那套
   `CONFIG_PWM=n` 桩函数存在的原因，也是 03 §C2.4
   签名不一致会咬到人的地方。
3. **`gpio-mvebu` 是"一个驱动两个身份"的标准范例**：
   读它可以学会怎么在一个 platform driver 里同时
   注册 gpiochip 和 `pwm_chip`。

### §4.1 `pwm-clk`：反方向的桥（`drivers/pwm/pwm-clk.c`，148 行）

和 §3.5 的 `clk-pwm.c` **名字几乎一样、方向完全相反**，
是 pwm 里最容易搞混的一对：

| 驱动 | 位置 | 方向 |
|---|---|---|
| **`clk-pwm.c`** | `drivers/clk/` | **PWM → clk**：消费一条 PWM，产出一个 clk |
| **`pwm-clk.c`** | `drivers/pwm/` | **clk → PWM**：消费一个 clk，产出一个 `pwm_chip` |

`pwm-clk.c` 的 `.apply`（`:38`，挂在 `:79` 的
`pwm_clk_ops`，`:78`）把请求的周期换成频率后
`clk_set_rate()`（`:68`），clk 句柄来自 `devm_clk_get()`（`:92`）；
注册在 `:105`，注销在 `:119`。

⚠️ 它**只实现 `.apply`，没有 `.get_state`**
⇒ 拿它当 PWM regulator 的 provider 会踩 §3.3 那条最严重的陷阱。
⚠️ 它用的是 `pwmchip_add()` + `pwmchip_remove()` 而非 devm 版
（`:105` / `:119`）⇒ 03 §B3 的 unbind UAF 风险在它身上是活的。

---

## §5 两个几乎没人用的能力

这一节是本篇最难从文档里得到的信息：**pwm 有两个 API 面在树里近乎空转。**

### §5.1 `.capture`：2 个 provider，arm64 上 0 个

```bash
grep -rn --include=*.c '\.capture' drivers/pwm/ | grep -v core.c
```

| provider | ops 赋值 | 实现 | 平台 |
|---|---|---|---|
| `drivers/pwm/pwm-sti.c` | `:430` | `sti_pwm_capture`（`:313`） | STMicro STi，**arm32** |
| `drivers/pwm/pwm-stm32.c` | `:492` | `stm32_pwm_capture`（`:169`） | STM32，**arm32** |

⚠️ **`pwm-stm32.c` 的赋值（`:492`）还是条件表达式**：

```c
.capture = IS_ENABLED(CONFIG_DMA_ENGINE) ? stm32_pwm_capture : NULL,
```

⇒ 连这一个 provider，**没开 `CONFIG_DMA_ENGINE` 时也是 NULL**。

两道门确认 arm64 不相关：

```bash
grep -rln 'st,sti-pwm\|st,stm32-pwm' arch/arm64/boot/dts/    # 门 1：空
grep -rn 'capture' output/pwm/*.md                            # 门 2：只在本节讨论 + 03 篇的错误码一节
```

⇒ **结论：arm64 上 `pwm_capture()` 恒返回 `-ENOSYS`，
`cat /sys/class/pwm/pwmchipN/pwmX/capture` 恒失败。**
而它是**唯一持全局 `pwm_lock` 调驱动的慢路径**（03 §B1.1、§C4）
——即"整个子系统里最贵的锁设计，服务的是一个在目标架构上不存在的能力"。

### §5.2 `usage_power`：1 个 provider 读，0 个 consumer 写

```bash
grep -rn --include=*.c 'usage_power' . | grep -v '^./drivers/pwm/core.c'
```

**全树只有一个文件命中**：`drivers/pwm/pwm-pca9685.c`
（`:152` 判断、`:154` 的注释说明它会 **phase shift** 各通道、`:198` 反向判断）。
核心侧另有 3 处：全等短路的比较（`core.c:570`）
和 debugfs 打印（`core.c:1130-1131`）。

⇒ 两条断言：

1. **只有 `pca9685` 一个 provider 真的实现了 `usage_power` 语义**
   （在周期内错开各通道相位，降低电源峰值电流）。
2. ⚠️ **全树没有任何消费者设置 `state.usage_power = true`。**
   这个字段的默认值是 0（`pwm_init_state()` 不碰它，02 §4.3）
   ⇒ **它在 in-tree 代码里是一个只被读、从不被写的字段**。

⇒ 评价（补充 03 §C4）：它是**为 pca9685 一款芯片加的接口，
而使用它的代码不在树里**。要用它只能自己写消费者，
而且必须确认负载能容忍波形重排——§3.6 的三个驱动都不行。

---

## §6 汇总表：框架 × 职责 × 挂载点 × arm64

| 框架/扩展 | 职责 | 挂在 03 的哪一点 | arm64 相关 | 典型用户 |
|---|---|---|---|---|
| **sysfs/class 层**（`drivers/pwm/sysfs.c`） | 用户态操作面 + export 通道的 PM | §A1 第 8 步 / §A2 第 1 步 / §A10 | ✅ 默认开 | 调试脚本、无驱动的板级点灯 |
| **pwm-backlight** | 亮度 | §A3（发现）+ §A5（配置） | ✅ 最常见 | 所有 DT 背光板 |
| **leds-pwm / -multicolor** | LED 亮度 / RGB | 同上 | ✅ | 指示灯、RGB |
| **pwm-regulator** | 电压 | §A3 + **§A6**（`pwm_adjust_config`） | ✅ | Rockchip 核心电压 |
| **pwm-fan** | 转速 + thermal cooling | §A3 + §A5 | ✅ | 风扇 |
| **clk-pwm** | 把 PWM 变成 clk | §A3 + `pwm_get_args` | ⚠️ 少见 | 需要外部方波的外设 |
| **pwm-beeper / -vibra / -ir-tx** | 波形发生 | §A3 + §A5（**反复 apply**） | ⚠️ 少见 | 蜂鸣、振动、红外 |
| **ts-nbus** | 总线时钟 | §A3 + §A5 | ❌ arm32 | TS-4600 |
| **外部 provider ×4** | 顺手注册 `pwm_chip` | §A1 | ✅（3/4） | GPIO/LED/桥片/Greybus |
| **`pwm-clk`**（反向桥） | clk → PWM | §A1 | ⚠️ | 只有 clk 可用的平台 |
| **`.capture` 能力** | 测量输入波形 | §A7 | ❌ **arm64 零 provider** | 无 |
| **`usage_power` 能力** | 允许周期内重排 | §A5 的全等短路会比较它 | ❌ **零 consumer** | 仅 pca9685 读 |

---

## §7 全树使用统计（每格都可重跑）

| 框架/能力 | 使用者数 | 文件数 | 调用点/节点数 | 重跑命令 |
|---|---|---|---|---|
| sysfs 层 | 1（核心自己） | 1 | **2** 个入口 | `grep -n 'pwmchip_sysfs_' drivers/pwm/core.c` |
| 全部消费者（含 provider 兼职） | — | **28**（arm32 板级 22 个另计） | — | `grep -rln --include=*.c '#include <linux/pwm.h>' . \| grep -v '^./drivers/pwm/'` |
| `devm_pwm_get` | — | — | 见命令 | `grep -rn --include=*.c 'devm_pwm_get(' . \| grep -v drivers/pwm/` |
| `pwm_adjust_config` | **1** | 1 | **1** 处 | `grep -rn --include=*.c 'pwm_adjust_config' . \| grep -v drivers/pwm/core.c` |
| `pwm_apply_args`（**待删**） | **5** | 5 | 5 处，全带同一 FIXME | `grep -rn --include=*.c 'pwm_apply_args' . \| grep -v drivers/pwm/` |
| `.capture` provider | **2** | 2 | 2 处 ops 赋值，其一条件编译 | `grep -rn --include=*.c '\.capture' drivers/pwm/ \| grep -v core.c` |
| `.capture` 的 arm64 DTS | **0** | 0 | 0 | `grep -rln 'st,sti-pwm\|st,stm32-pwm' arch/arm64/boot/dts/`（空） |
| `usage_power` provider | **1** | 1 | 3 处 | `grep -rn --include=*.c 'usage_power' . \| grep -v drivers/pwm/core.c` |
| `usage_power` consumer | **0** | 0 | 0 | 同上（结果里没有 `drivers/pwm/` 之外的文件） |
| `drivers/pwm/` 外的 provider | **4** | 4 | 4 处注册调用 | `grep -rn --include=*.c -E 'devm_pwmchip_add\|pwmchip_add' . \| grep -v '^./drivers/pwm/'` |
| `pwmchip_add` 家族总计 | — | — | **44** 处 | `grep -rn --include=*.c 'pwmchip_add(' . \| wc -l` |
| arm64 DTS 用 PWM | — | **112** 个 DTS 有 `pwms =` | `#pwm-cells` **71** · `pwm-names` **仅 4** | `grep -rln 'pwms =' arch/arm64/boot/dts/ \| wc -l` |

⚠️ **"0 个使用者"的两条都给了证明命令**（§5.1 门 1、§5.2 的 grep 结果），
不是靠"没找到"下的结论。

---

## §8 读下一篇

- 想照着写代码 → [05-usage.md](05-usage.md)（provider 骨架 + 各类 consumer 骨架 + 近邻符号区分）
- 想写 DTS → [06-devicetree.md](06-devicetree.md)（`#pwm-cells` 为什么必须显式写 3）
- 想排查现场 → [07-debug.md](07-debug.md)（两个 tracepoint 的名字陷阱、debugfs 读法、错误码分诊）
- 想理解为什么这么设计 → 回 [03-internals.md](03-internals.md) 的 Part C
