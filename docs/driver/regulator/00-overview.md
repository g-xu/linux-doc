---
title: 总览
---

# 00 总览：regulator 子系统全貌

> **本篇的定位是「复习入口」。**
> 隔几个月回来，只读这一篇就能恢复对整个子系统的全貌认识；
> 需要细节再按 §8 的导读表跳到对应篇目。
>
> 基线：Linux **v6.1.158**（`git log -1` = `f6e38ae624cf`）。
> 目标架构：**arm64 only** —— x86 / powerpc / arm32 特有的路径一律不写，
> ACPI 相关也不写（裁剪清单见 99 篇 §2.7）。

---

## §1 这个子系统是干什么的

### §1.1 一句话

**regulator 子系统把"给某个硬件供电"这件事从各个驱动里抽出来，
变成一套引用计数 + 约束检查的公共服务。**

没有它的世界是这样的：MMC 驱动直接往 PMIC 的 I2C 寄存器里写一个字节把
VCC 打开；摄像头驱动也要用同一路 1.8V，它也去写那个寄存器；
两个驱动谁先卸载谁就把对方的电断了。而且两个驱动都得知道
"这块板子上用的是 RK808，那一路是 LDO2，寄存器是 0x2B" ——
**板级知识被抄进了通用驱动里**。

有了它之后：

- 驱动只说「我要名字叫 `vdd` 的那路电，请打开」
- 「`vdd` 是哪颗 PMIC 的哪一路」写在 **DTS** 里
- 「那一路怎么开」写在 **PMIC 驱动**里
- 「同时有几个人要它开着」由**核心**记账
- 「这一路允许调到什么范围、允不允许关」由 **DTS 里的约束**授权

### §1.2 三个角色

```
      ┌──────────────────────────────────────────────────┐
      │  消费者（consumer）                              │
      │  MMC / 摄像头 / 网卡 / 传感器 / CPU 调频 …        │
      │  只用 include/linux/regulator/consumer.h         │
      │  只关心"名字 + 开/关/调压"                       │
      └────────────────────┬─────────────────────────────┘
                           │ regulator_get / _enable / _set_voltage
                           ▼
      ┌──────────────────────────────────────────────────┐
      │  核心（core）  drivers/regulator/core.c  6310 行  │
      │  · 名字 → rdev 的解析（DTS 或 platform data）     │
      │  · 引用计数（enable_count / use_count）           │
      │  · 约束检查（valid_ops_mask / 电压区间交集）      │
      │  · 供电树 + 耦合组的加锁（ww_mutex）              │
      │  · sysfs / debugfs / tracepoint                   │
      └────────────────────┬─────────────────────────────┘
                           │ rdev->desc->ops->enable() 等回调
                           ▼
      ┌──────────────────────────────────────────────────┐
      │  提供者（provider / PMIC 驱动）  184 个 .c 文件    │
      │  只用 include/linux/regulator/driver.h            │
      │  只关心"怎么往寄存器里写"                        │
      └──────────────────────────────────────────────────┘
                           ▲
                           │ 约束（constraints）
      ┌────────────────────┴─────────────────────────────┐
      │  板级配置：**DTS**（arm64 上唯一来源）            │
      │  drivers/regulator/of_regulator.c  807 行         │
      └──────────────────────────────────────────────────┘
```

**注意约束的箭头方向**：DTS 的内容不是给消费者的，
也不主要是给驱动的 —— 它是给**核心**的，用来决定
"允许消费者对这一路做什么"。这条是整个子系统最反直觉的地方，
06 篇 §1 和 08 篇 §4 各讲了一遍。

### §1.3 规模

| 文件 | 行数 | 角色 |
|---|---|---|
| `drivers/regulator/core.c` | 6310 | 核心。全篇逐行读过 |
| `drivers/regulator/helpers.c` | 968 | 给驱动用的 regmap 通用回调（04 篇 §1） |
| `drivers/regulator/of_regulator.c` | 807 | DTS 解析（06 篇的主角） |
| `drivers/regulator/devres.c` | 662 | `devm_*` 包装（01 篇 §3） |
| `drivers/regulator/irq_helpers.c` | 439 | 错误中断上报框架（04 篇 §3） |
| `drivers/regulator/fixed.c` | 356 | `regulator-fixed` 驱动（06 篇 §7） |
| `drivers/regulator/virtual.c` | 374 | 调试用的假消费者（07 篇 §5.2） |
| `drivers/regulator/userspace-consumer.c` | 181 | 用户态控开关（07 篇 §5.1） |
| `drivers/regulator/dummy.c` | 90 | 兜底的"空电源"（08 篇 §1） |
| `drivers/regulator/fixed-helper.c` | 61 | 死代码，无调用者（04 篇 §7） |

头文件（消费者/驱动的契约面）：

| 头文件 | 行数 | 给谁看 |
|---|---|---|
| `include/linux/regulator/driver.h` | 783 | 提供者 |
| `include/linux/regulator/consumer.h` | 723 | 消费者，99% 的人只需要这个 |
| `include/linux/regulator/machine.h` | 292 | 板级约束的定义（arm64 上由 DTS 填） |
| `include/linux/regulator/coupler.h` | 100 | 联动调压策略（04 篇 §4） |

`drivers/regulator/` 下共 **184** 个 `.c`，除上面列的核心/框架文件外
全是具体芯片驱动 —— 本文档集**只讲契约，不逐个读**（选样规则见 99 篇 §2.2）。

---

## §2 硬件背景：电源芯片到底是什么

> 这一节讲的是"软件抽象为什么长成这样"。不熟悉硬件的话，
> 后面所有篇目里的 `vsel_reg` / `linear_ranges` / `enable_time` /
> `min_dropout_uV` 都会像是凭空冒出来的字段。
> 每个概念后面都注明它在代码里对应哪个字段。

### §2.1 板子上到底有多少路电

一块 arm64 开发板，典型的供电情况是：

- 一个 **12V 或 5V 的输入**（DC 插头或 USB-C）
- 几颗**分立的降压芯片**，把它降到 5V / 3.3V
- 一颗或几颗 **PMIC**（Power Management IC），从 3.3V/5V 再分出十几到几十路
- 每路输出接到一个具体的耗电体：CPU 核、GPU、DDR、SD 卡槽、
  摄像头模组、WiFi 模块、HDMI PHY……

数量级感受：一颗中端 PMIC（如 RK808）提供 **4 路 buck + 8 路 LDO +
2 路开关**，共 14 路可控输出。一块板子上再挂几个分立芯片，
`/sys/class/regulator/` 下有 **20~40** 个 `regulator.N` 是很正常的。

这些路之间不是并列的，是**一棵树**：

```
  DC 12V
    └── vcc5v0_sys（分立 buck）
          ├── vcc3v3_sys（分立 buck）
          │     ├── RK808 的 LDO 输入
          │     │     ├── vcc_1v8   → DDR、各种 IO
          │     │     ├── vcc_sd    → SD 卡槽
          │     │     └── vcc_3v0   → 摄像头
          │     └── vcc3v3_pcie（GPIO 控制的开关）
          └── vdd_center（RK808 的 buck） → SoC 内部逻辑
```

**树是代码里真实存在的结构**，不是画图方便 ——
`rdev->supply` 就是"我的上游是谁"这个指针。
开一路必须先开它的上游，关一路可能顺带把上游关掉
（`drivers/regulator/core.c:3061-3062`，08 篇 §3）。

DTS 里用 `xxx-supply = <&上游>` 表达这条边（06 篇 §4）。

### §2.2 两种降压方式：LDO 和 DCDC

这是理解 PMIC 数据手册的第一道门，也解释了为什么有些路能调压、
有些路不能，为什么有的路发热。

**LDO（Low-DropOut regulator，低压差线性稳压器）**

原理上就是一个**可调的电阻**串在输入和输出之间，
靠反馈回路调整这个电阻，让输出稳在目标值。

```
   3.3V ──[ 可调电阻 ]── 1.8V ──▶ 负载 100mA
              │
         这里消耗掉 (3.3-1.8) × 0.1A = 150mW，全变成热
```

特点：

| 特性 | 说明 |
|---|---|
| 只能降压 | 输出必须低于输入 |
| 效率 = Vout/Vin | 3.3→1.8 效率只有 55%，剩下 45% 是热 |
| 输出很干净 | 没有开关噪声，适合 PLL / ADC / 射频 |
| 需要压差余量 | 输入必须比输出高一点点才能稳压 |
| 电流能力小 | 通常几十到几百 mA |

**最后两条在代码里各有一个字段**：

- 「需要压差余量」= **dropout voltage**，对应
  `desc->min_dropout_uV`（`include/linux/regulator/driver.h:384`，
  文档在 `:293`）。核心在计算供电树上游该给多少电压时会加上它 ——
  这就是为什么 `vcc3v3_sys` 不能只给到 1.85V 就指望 LDO 输出 1.8V。
- 「效率就是分压比」解释了为什么**大电流的路一定不用 LDO**。

**DCDC（开关式，buck / boost）**

原理是**高速开关 + 电感储能**：把输入斩成方波，用电感和电容滤成直流。
能量不是被烧掉的，是被搬运的。

| 特性 | 说明 |
|---|---|
| 效率高 | 典型 85%~95%，和压差关系不大 |
| 电流能力大 | 几百 mA 到几 A，CPU / GPU / DDR 都用它 |
| 有开关噪声 | 开关频率（几百 kHz ~ 几 MHz）的纹波会串到输出 |
| 需要外部电感 | 占板面积，成本高 |
| 有多种工作模式 | 见 §2.5 |

buck 是降压，boost 是升压（比如 5V 升到 LED 背光要的 20V），
buck-boost 两个方向都行。

**在软件里怎么区分？** **区分不了，也不需要区分。**
`regulator_desc` 里**没有"这是 LDO 还是 buck"这个字段**。
核心只认 `desc->type`，而它只有两个值：
`REGULATOR_VOLTAGE` 和 `REGULATOR_CURRENT`（电流源，用于 LED 驱动）。

LDO / buck 的差别只体现在**驱动填的参数**上：
buck 的 `n_voltages` 大、档位密、`ramp_delay` 有意义；
LDO 的 `min_dropout_uV` 有意义。核心一视同仁。

唯一的例外是**命名习惯**：DTS 节点名常常直接写
`DCDC_REG1` / `LDO_REG2`（06 篇 §10 的 rk808 例子），
这是给人看的，核心不解析这个名字的含义。

### §2.3 「怎么控制它」= 三种硬件接口

**(a) 寄存器（绝大多数 PMIC）**

PMIC 挂在 **I2C** 或 **SPI** 上，每一路对应几个寄存器位：

```
  0x2B  LDO2_ON_VSEL   [7]   使能位
                       [5:0] 电压选择子（selector）
```

于是"开这一路"= 往 0x2B 的 bit7 写 1；
"调到 1.8V" = 算出 1.8V 对应的选择子，写进 [5:0]。

这就是 `regulator_desc` 里那一大堆寄存器字段的来源：

| 字段 | 行 | 含义 |
|---|---|---|
| `enable_reg` / `enable_mask` / `enable_val` | `driver.h:403` / `:404` / `:405` | 使能位在哪、掩码、写什么值算开 |
| `enable_is_inverted` | 文档 `driver.h:323-324` | 有些芯片是"写 1 关闭" |
| `vsel_reg` / `vsel_mask` | `driver.h:396` / `:397` | 电压选择子在哪 |
| `apply_reg` / `apply_bit` | `driver.h:401` / `:402` | 有些芯片写完 vsel 还要再敲一下"生效" |
| `csel_reg` | `driver.h:399` | 电流限制选择子（电流源型） |
| `bypass_reg` | `driver.h:408` | 旁路模式（§2.5） |
| `active_discharge_reg` / `_mask` | `driver.h:415` / `:414` | 关闭时主动放电 |
| `soft_start_reg` / `_mask` / `_val_on` | `driver.h:416`–`:418` | 缓启动 |
| `pull_down_reg` / `_mask` / `_val_on` | `driver.h:419`–`:421` | 关闭时下拉输出 |
| `ramp_reg` / `ramp_mask` / `ramp_delay_table` / `n_ramp_values` | `driver.h:422`–`:425` | 压摆率档位 |

**正因为这个模式高度重复，核心提供了一整套 regmap 通用回调**
（`drivers/regulator/helpers.c`，28 个函数，04 篇 §1）。
一个典型 PMIC 驱动**一行自己的读写代码都不用写** ——
只填这些字段 + 指向 `regulator_enable_regmap` 之类的现成函数
（05 篇 §2.1 有完整骨架）。

**(b) 一根 GPIO（最简单的"电源"）**

板子上常见一颗 load switch 或 LDO，只有一个使能引脚，
电压是电阻分压定死的、不可调。这类不需要专门驱动 ——
DTS 里写 `compatible = "regulator-fixed"` 加一个 `gpio`，
由 `drivers/regulator/fixed.c` 统一处理（06 篇 §7）。

更极端的情况：连 GPIO 都没有，硬连线常开。
这时 DTS 里仍然可以写一个 `regulator-fixed` 节点（只有电压、
没有 gpio、加 `regulator-always-on`）—— 目的是**让供电树完整**，
消费者驱动照常 get/enable。

**(c) 一路完全不受软件控制**

比如直接从 DC 插头分出来的 5V。这种可以干脆不在 DTS 里描述 ——
消费者去 get 会拿到 dummy（08 篇 §1），照样跑通。

### §2.4 电压不是连续的：选择子（selector）与档位表

硬件只接受**整数选择子**，不接受"1.813V"。所以每一路都有
一张"选择子 ↔ 电压"的对应关系。绝大多数芯片是**线性**的：

```
  sel  0 →  0.7125 V
  sel  1 →  0.7250 V     步长 12.5 mV
  sel  2 →  0.7375 V
  ...
  sel 63 →  1.5000 V
```

代码里两种表达方式：

| 方式 | 字段 | 适用 |
|---|---|---|
| 单一线性 | `desc->min_uV` + `uV_step`（`driver.h:380`）+ `linear_min_sel`（`:381`） | 全程一个步长 |
| 分段线性 | `desc->linear_ranges`（`driver.h:386`）+ `n_linear_ranges`（`:389`） | 不同区段步长不同 |

分段线性用 `REGULATOR_LINEAR_RANGE` 宏描述
（`include/linux/regulator/driver.h:53`），四个参数是
`(_min_uV, _min_sel, _max_sel, _step_uV)`。

**一个常见的真实用法：步长填 0 表示"这些选择子都是同一个电压"**。
比如前 11 个选择子全对应 0V（也就是"输出关闭"）——
`drivers/regulator/88pg86x.c:14-18` 就是这么写的（05 篇 §2.1）。

`desc->n_voltages`（`driver.h:372`）是选择子总数，
**必须和档位表覆盖的范围对得上** —— 这是提供者最容易写错的字段之一。

这套机制解释了两个软件行为：

1. **`regulator_set_voltage` 传的是区间不是值**，
   因为核心要在你的区间里挑一个**真实存在的档位**（08 篇 §5）
2. **返回 0 不代表电压等于你要的数** —— 你要 1.20V，
   档位只有 1.1875 和 1.2000，运气好才正好命中

### §2.5 工作模式：同一路电的几种"性格"

DCDC 在**轻载**时，如果继续高频开关，开关损耗占比会很大，效率反而低。
所以芯片提供几种模式：

| 模式 | 宏 | 行 | 硬件含义 |
|---|---|---|---|
| FAST | `REGULATOR_MODE_FAST` | `include/linux/regulator/consumer.h:82` | 强制 PWM，纹波小、瞬态响应快，轻载效率差 |
| NORMAL | `REGULATOR_MODE_NORMAL` | `consumer.h:83` | 默认 |
| IDLE | `REGULATOR_MODE_IDLE` | `consumer.h:84` | 省电，允许跳周期（PFM），纹波变大 |
| STANDBY | `REGULATOR_MODE_STANDBY` | `consumer.h:85` | 最省电，输出能力最弱 |
| INVALID | `REGULATOR_MODE_INVALID` | `consumer.h:81` | 0，表示"没有有效模式" |

**这四个宏是"抽象模式"，不是寄存器值。**
每颗芯片的模式位编码都不一样，所以驱动要提供一个映射函数
`of_map_mode`，把 DTS 里写的数字翻译成这四个宏之一
（06 篇 §5、`drivers/regulator/rk808-regulator.c:532-542`）。

与模式相关的还有一个 **bypass（旁路）**：把 LDO 的调整管
整个短路，输出直接等于输入。用于"上游电压已经等于我要的值，
不必再浪费压差"的场景。对应 `desc->bypass_reg`（`driver.h:408`）和
DTS 的 `regulator-allow-bypass`。

还有一个自动模式选择机制 **DRMS**（Dynamic Regulator Mode
Switching）：消费者用 `regulator_set_load` 声明自己的电流需求，
核心把这一路上所有消费者的需求加起来，
调 `ops->set_load` 或按 `ops->get_optimum_mode` 自动切模式。
DTS 里要写 `regulator-allow-set-load` 才授权（06 篇 §1.1）。

### §2.6 时序：为什么"写完寄存器"不等于"电压到位"

硬件不是瞬间响应的，四个时间量都在代码里有字段：

| 时间量 | 字段 | 行 | 含义 |
|---|---|---|---|
| **使能建立时间** | `desc->enable_time` | `driver.h:427` | 写了使能位之后，要等多久输出才稳定 |
| **压摆率** | `desc->ramp_delay` | `driver.h:383` | 单位是 **uV/us**（文档 `driver.h:292`）—— 是速度，不是时间 |
| **关-开保护间隔** | `desc->off_on_delay` | `driver.h:429` | 关掉之后至少多久才能再开（文档 `:354`） |
| **轮询间隔** | `desc->poll_enabled_time` | `driver.h:431` | 有些芯片有"已就绪"状态位，核心按这个间隔轮询它（文档 `:356`） |

**`enable_time` 是提供者侧最容易漏填的字段，而漏填的后果非常隐蔽**：
`regulator_enable` 立刻返回、消费者立刻开始访问硬件、
硬件因为电压还没爬上来而行为异常 —— 表现是"偶发的初始化失败"，
在慢机器上正常、快机器上炸。

DTS 侧对应的属性是 `regulator-enable-ramp-delay` 和
`regulator-ramp-delay`（06 篇 §1.1）—— 板级可以**覆盖**驱动填的值，
因为实际建立时间取决于板上电容有多大。

这套时序也是 07 篇 §4.2 那个"enable 三段延迟拆解"的基础：
tracepoint 能把"等上游"、"等自己的 enable_time"、"写寄存器本身"
三段时间分开看。

### §2.7 联动：为什么有些路不能单独调

SoC 内部常有这种约束：**CPU 核电压和 SoC 逻辑电压之间的差
不能超过 X mV**，否则内部电平转换会出问题。

这两路是两颗独立的 buck，但**不能独立调**：从 0.9V 一起升到 1.1V
必须交替小步走（CPU 升一档、SoC 升一档、CPU 再升一档……），
不能一路直接冲到位。

这就是 **coupling（耦合）** 机制的来源：

- DTS 里写 `regulator-coupled-with = <&另一路>` 和
  `regulator-coupled-max-spread`（06 篇 §5.3）
- 核心把它们归成一个"耦合组"
- 调压时不再直接写寄存器，而是交给一个 **coupler** 决定每步怎么走
  （04 篇 §4）

**耦合是核心那套 `ww_mutex` 复杂加锁的直接原因**：
调一路电压要同时锁住"它自己 + 所有上游 + 所有耦合伙伴 + 那些的上游"，
而这个集合是**边走边发现**的、还可能长成菱形 —— 只能用可回滚的锁
（08 篇 §9）。

### §2.8 错误上报：PMIC 会主动告状

带保护功能的 PMIC 有一根中断引脚，出现下列情况会拉低：

- 过压（over-voltage）
- 欠压（under-voltage）
- 过流（over-current）
- 过温（over-temperature）
- 调压失败（regulation failure）

内核为这类中断提供了一个通用框架
（`drivers/regulator/irq_helpers.c`，04 篇 §3）：
驱动只写一个 `map_event` 回调把状态寄存器翻译成事件位，
框架负责去抖、重试、通知消费者，
必要时（重试超过 `fatal_cnt` 次）直接 `hw_protection_shutdown` 关机。

**但消费者收到这些通知**只能记日志或自己降级 ——
**通知链的返回值除了 `PRE_DISABLE` 之外全部被丢弃**（03 篇 §B5.1）。
过温通知阻止不了任何事。

另一条容易误解的：**通知不向上游传播**。
上游 PMIC 过温，你这一路的消费者收不到 —— 只收到自己这一路的事件。

### §2.9 硬件背景 → 软件抽象 的对照总表

| 硬件事实 | 软件里的落点 |
|---|---|
| 一路输出 | 一个 `struct regulator_dev`（rdev） |
| 板上供电是一棵树 | `rdev->supply` 指针 + DTS 的 `xxx-supply` |
| LDO 需要压差余量 | `desc->min_dropout_uV`（`driver.h:384`） |
| 电压只有离散档位 | `desc->linear_ranges`（`driver.h:386`）/ `uV_step`（`:380`） |
| 档位总数 | `desc->n_voltages`（`driver.h:372`） |
| 使能位 / 电压位在某个寄存器 | `desc->enable_reg`（`driver.h:403`）/ `vsel_reg`（`:396`） |
| 写完要敲一下才生效 | `desc->apply_reg` / `apply_bit`（`driver.h:401` / `:402`） |
| 电压爬升需要时间 | `desc->enable_time`（`driver.h:427`）/ `ramp_delay`（`:383`） |
| 关掉后不能马上再开 | `desc->off_on_delay`（`driver.h:429`） |
| 轻载可以换省电模式 | `REGULATOR_MODE_*`（`consumer.h:81-85`）+ `of_map_mode` |
| LDO 可以整个短路掉 | `desc->bypass_reg`（`driver.h:408`）+ `regulator-allow-bypass` |
| 两路电压差有上限 | coupling + coupler（04 篇 §4） |
| PMIC 有保护中断 | `irq_helpers.c` + 通知链 |
| 这一路根本不受软件控制 | 不写进 DTS，消费者拿到 dummy（08 篇 §1） |
| 这一路只有一根 GPIO | `regulator-fixed`（06 篇 §7） |
| 「这是 LDO 还是 buck」 | **没有对应字段** —— 核心不区分 |

---

## §3 概念模型：五个名词和它们的关系

### §3.1 五个核心名词

| 名词 | 代码里是 | 一句话 |
|---|---|---|
| **rdev**（provider） | `struct regulator_dev` | 一路真实的电源输出 |
| **凭证**（consumer handle） | `struct regulator` | **某个驱动对某一路电源的一次引用**，不是"一个 regulator" |
| **desc** | `struct regulator_desc` | 驱动写的"这一路的硬件能力"（档位表、寄存器地址、回调表） |
| **constraints** | `struct regulation_constraints` | DTS 写的"允许对这一路做什么" |
| **ops** | `struct regulator_ops` | 驱动实现的回调表（怎么写寄存器） |

**`desc`（驱动，讲能力）和 `constraints`（DTS，讲授权）是两套东西，
而核心的判断只看后者。** 这是本子系统最重要的一条心智模型，
08 篇 §4 用"拆开关面板"的类比专门讲了一遍。

### §3.2 关系图

```
  消费者驱动
     │  regulator_get(dev, "vdd")
     ▼
 ┌──────────────────────┐
 │ struct regulator     │  ← 凭证。每次 get 新建一张
 │   dev  = 消费者设备   │     drivers/regulator/internal.h:45
 │   enable_count       │     "我"要求开了几次   [internal.h:52]
 │   voltage[]          │     "我"要求的电压区间
 │   rdev ──────────────┼──┐  [internal.h:57]
 └──────────────────────┘  │
                           │  N 张凭证 → 1 个 rdev
     ┌─────────────────────┘
     ▼
 ┌───────────────────────────────────────────────────────┐
 │ struct regulator_dev（rdev）  driver.h:611             │
 │   use_count      有几个消费者要求它开着     [:614]   │
 │   open_count     有几张凭证存在             [:615]   │
 │   consumer_list  所有凭证挂在这里           [:622]   │
 │   mutex          ww_mutex！                   [:627]   │
 │   dev            真的 struct device           [:631]   │
 │   supply ────────┼──▶ 上游的凭证（供电树的边）[:633]   │
 │   coupling_desc  耦合组                       [:624]   │
 │   notifier       通知链                       [:626]   │
 │                                                        │
 │   desc ──────────┼──▶ struct regulator_desc  [:612]    │
 │   constraints ───┼──▶ struct regulation_constraints    │
 │                  │                            [:632]   │
 └───────────────────────────────────────────────────────┘
        │                          │
        │ 驱动写的（能力）          │ DTS 写的（授权）
        ▼                          ▼
 ┌──────────────────────┐  ┌────────────────────────────────┐
 │ regulator_desc       │  │ regulation_constraints         │
 │   driver.h:361       │  │   machine.h:157                │
 │   name / of_match    │  │   name（regulator-name）[:159] │
 │   n_voltages [:372]  │  │   min_uV / max_uV  [:162]/[:163]│
 │   linear_ranges[:386]│  │   valid_ops_mask     [:184] │
 │   vsel_reg   [:396]  │  │   valid_modes_mask      [:181] │
 │   enable_reg [:403]  │  │   always_on / boot_on           │
 │   enable_time[:427]  │  │            [:211] / [:212]     │
 │   ops ───────┐       │  │   ramp_delay / enable_time      │
 └──────────────┼───────┘  │            [:202] / [:206]     │
                ▼          │   max_spread（耦合）    [:175] │
     ┌──────────────────┐  └────────────────────────────────┘
     │ regulator_ops    │
     │   driver.h:159   │  核心调不调这些回调，
     │  .enable         │     取决于左边的 valid_ops_mask，
     │  .disable        │     **不取决于这张表填了什么**
     │  .set_voltage_sel│
     │  .get_voltage_sel│
     │  .set_mode …     │
     └──────────────────┘
```

### §3.3 三条最容易搞混的对应关系

**(a) 一个 rdev，三个计数**

| 字段 | 行 | 含义 | 从哪看 |
|---|---|---|---|
| `regulator->enable_count` | `internal.h:52` | **单个消费者**未配对的 enable 次数 | 看不到 |
| `rdev->use_count` | `driver.h:614` | 有几个消费者要求它开着 | sysfs `num_users` / debugfs `use_count` |
| `rdev->open_count` | `driver.h:615` | 有几张凭证存在 | **只有 debugfs** |

sysfs 里的 `num_users` 读的是 **`use_count`**，不是 `open_count` ——
名字和语义不一致，08 篇 §7 专门讲了这个坑。

**(b) 一路电源，三个名字**

| 名字 | 例子 | 用途 |
|---|---|---|
| DTS label | `vdd_center:` | 只给别的 DTS 节点 `<&引用>` 用 |
| DTS 节点名 | `DCDC_REG1` | **唯一参与驱动匹配的名字**（对 `desc->of_match`，`driver.h:364`） |
| `regulator-name` 属性 | `"vdd_center"` | 显示用 → `constraints->name`（`machine.h:159`）→ `rdev_get_name`（`drivers/regulator/core.c:101-109`） |

08 篇 §6 讲了写错哪个会静默失效。

**(c) 「能不能」的三层门**

想做一件事（比如关掉一路电），要连过三道**互相独立**的门：

```
 ① 驱动有没有实现回调？          desc->ops->disable != NULL
 ② DTS 有没有授权？              constraints->valid_ops_mask & CHANGE_STATUS
                                 核心只查这一道
 ③ 引用计数允许吗？              use_count 是不是只剩我一个
```

**① 和 ② 是完全解耦的。**
驱动实现了 `.disable` 但 DTS 写了 `regulator-always-on`
⇒ 那个回调**永远不会被调用**，而 `regulator_disable` **返回 0**。
反过来，DTS 授权了但驱动没实现回调 ⇒ 核心只记账、也返回 0。

**两种情况的返回值都是 0**，这是"看起来成功了但什么都没发生"的根源。

---

## §4 数据结构速览

> 这一节只列"复习时需要立刻想起来的字段"。完整字段表在 02 篇。

### §4.1 `struct regulator_dev`（`include/linux/regulator/driver.h:611`）

一路电源的运行时状态。**核心拥有它**，驱动只在注册时提供 `desc`。

| 字段 | 行 | 备注 |
|---|---|---|
| `desc` | `:612` | `const` 指针，指向驱动的静态描述 |
| `exclusive` | `:613` | `regulator_get_exclusive` 用（第二个人来就 `-EPERM`） |
| `use_count` / `open_count` / `bypass_count` | `:614`–`:616` | 三个计数，见 §3.3(a) |
| `list` | `:619` | 挂在全局 `regulator_list` 上 |
| `consumer_list` | `:622` | 所有凭证挂在这里 → `regulator_summary` 就是遍历它 |
| `coupling_desc` | `:624` | 耦合组（结构定义在 `driver.h:594`） |
| `notifier` | `:626` | 通知链头 |
| `mutex` | `:627` | `ww_mutex`，不是普通 mutex（08 篇 §9） |
| `mutex_owner` | `:628` | 配合 `ref_cnt` 做递归持锁 |
| `owner` | `:630` | 驱动模块，核心用它防 rmmod |
| `dev` | `:631` | 内嵌的真 `struct device`，注册在 `regulator_class` 上 |
| `constraints` | `:632` | DTS 来的授权 |
| `supply` | `:633` | 上游的**凭证**（不是 rdev）—— 说明"核心自己也是消费者" |
| `regmap` | `:635` | helpers.c 那套通用回调靠它 |
| `debugfs` | `:641` | 这一路的 debugfs 目录 |
| `ena_pin` | `:643` | GPIO 使能引脚（多路可共享一个） |

### §4.2 `struct regulator`（`drivers/regulator/internal.h:45`）

**内部结构，消费者拿到的是不透明指针。**
它是"凭证"而不是"一个 regulator"（§3.1）。

关键字段：`dev`（`:46`，谁借的）、`enable_count`（`:52`）、
`uA_load`（`:51`，`regulator_set_load` 声明的负载）、
`supply_name`（`:55`，DTS 里的 `"vdd"`）、`rdev`（`:57`）。

### §4.3 `struct regulator_desc`（`include/linux/regulator/driver.h:361`）

驱动写的**静态** `const` 描述。三组字段：

| 组 | 代表字段 | 作用 |
|---|---|---|
| 身份 | `name`（`:362`）、`of_match`（`:364`）、`regulators_node`（`:366`）、`id`（`:370`） | 匹配 DTS 子节点 |
| 电气能力 | `n_voltages`（`:372`）、`uV_step`（`:380`）、`linear_ranges`（`:386`）、`min_dropout_uV`（`:384`） | 档位表 |
| 寄存器与时序 | `vsel_reg`（`:396`）、`enable_reg`（`:403`）、`enable_time`（`:427`）、`off_on_delay`（`:429`） | 见 §2.3 / §2.6 的两张表 |

### §4.4 `struct regulation_constraints`（`include/linux/regulator/machine.h:157`）

DTS 解析出来的**授权**。arm64 上完全由 `of_regulator.c` 填。

| 字段 | 行 | DTS 属性 |
|---|---|---|
| `name` | `:159` | `regulator-name` |
| `min_uV` / `max_uV` | `:162` / `:163` | `regulator-min/max-microvolt` |
| `min_uA` / `max_uA` | `:168` / `:169` | `regulator-min/max-microamp` |
| `max_spread` | `:175` | `regulator-coupled-max-spread` |
| `max_uV_step` | `:178` | `regulator-max-step-microvolt` |
| `valid_modes_mask` | `:181` | `regulator-allowed-modes` + 驱动的 `of_map_mode` |
| `valid_ops_mask` | `:184` | **多条属性的组合结果**（06 篇 §1.1 有完整映射） |
| `input_uV` | `:187` | `regulator-input-current-limit-microamp` 等 |
| `ramp_delay` | `:202` | `regulator-ramp-delay` |
| `enable_time` | `:206` | `regulator-enable-ramp-delay` |
| `always_on` | `:211` | `regulator-always-on`（同时**没收** `CHANGE_STATUS`） |
| `boot_on` | `:212` | `regulator-boot-on` |
| `apply_uV` | `:213` | min == max 时是否真去写一次 |
| `soft_start` | `:215` | `regulator-soft-start` |
| `over_current_protection` / `_detection` | `:217` / `:218` | `regulator-over-current-protection` 等 |

### §4.5 `struct regulator_ops`（`include/linux/regulator/driver.h:159`）

驱动实现的回调表。**绝大多数字段可以直接指向
`drivers/regulator/helpers.c` 里的现成函数** —— 只要芯片是
"regmap + 线性档位"这种常见形态（04 篇 §1，05 篇 §2.1 有骨架）。

核心不检查这张表是否完整。回调是 NULL 时的行为因操作而异：
`enable` 是"只记账、返回 0"，`set_voltage` 是"报错"。

### §4.6 其他

| 结构 | 位置 | 用途 |
|---|---|---|
| `struct regulator_config` | `driver.h:452` | 注册时的**临时**参数包（栈上即可，核心会把需要的拷走） |
| `struct coupling_desc` | `driver.h:594` | 耦合组成员表 |
| `struct regulator_irq_desc` | `driver.h:562` | 错误中断框架的描述（04 篇 §3） |
| `struct regulator_coupler` | `include/linux/regulator/coupler.h:41` | 联动调压策略（04 篇 §4） |
| `struct regulator_state` | `machine.h:77` | 一个 suspend 状态下的目标（06 篇 §5.2） |
| `struct regulator_init_data` | `machine.h:262` | 平台数据入口。arm64 上只由 `of_regulator.c` 内部构造 |

---

## §5 机制速览

> 每条只讲"是什么 + 最反直觉的那一点"，展开在 03 篇。

### §5.1 注册：DTS 约束是怎么进到 rdev 里的

```
 PMIC 驱动 probe
   └─ devm_regulator_register(dev, desc, config)     每路一次
        └─ regulator_register()                      core.c:5522 起
             ├─ 分配 rdev，dev_set_name("regulator.%d")   全局计数器
             ├─ regulator_of_get_init_data()
             │     用 desc->of_match 在 DTS 里找子节点，
             │     解析出 constraints
             ├─ 早期解析 supply（拿到上游的凭证）
             ├─ set_machine_constraints()            应用约束、可能立刻上电
             ├─ regulator_init_coupling()            找 coupler
             ├─ device_add(&rdev->dev)               sysfs 出现
             └─ rdev_init_debugfs()
```

**最反直觉的一点**：`of_match` 在 DTS 里**找不到对应子节点，
不是错误** —— 核心会 `kzalloc` 一份**全零的 constraints**
（`drivers/regulator/core.c:5635-5641`），rdev 照样注册成功。
表现是 sysfs 里 `min_microvolts` 读出 `0`。
06 篇 §8.2 有专门的决策树。

### §5.2 消费者拿电源：名字是怎么解析的

```
 regulator_get(dev, "vdd")
   └─ _regulator_get()                              core.c:2195 起
        ├─ 按 DTS：dev 的 of_node 里找 "vdd-supply" → phandle → rdev
        ├─ 找到 → create_regulator() 建一张凭证      core.c:1868
        └─ 找不到 + have_full_constraints()（arm64 恒真）
              → 返回 dummy_regulator_rdev + dev_warn  core.c:2225-2231
```

**arm64 上 `regulator_get` 几乎不会失败** ——
DTS 漏写 supply 的后果是拿到一个什么都不做的 dummy。
唯一能发现它的方法是看 `dmesg`（07 篇 §2.1）或改用
`regulator_get_optional`（05 篇 §1.3）。08 篇 §1 是完整解释。

### §5.3 开：四道门 + 递归上游

```
 regulator_enable(reg)
   └─ regulator_lock_dependent()    锁整个依赖闭包（ww_mutex）
        └─ _regulator_enable()
             ├─ 先递归 enable 上游（rdev->supply）
             ├─ regulator->enable_count++
             ├─ use_count == 0 才真去动硬件
             ├─ regulator_ops_is_valid(CHANGE_STATUS)  ← ② DTS 授权
             ├─ _regulator_do_enable()
             │    ├─ off_on_delay 保护间隔
             │    ├─ ops->enable() 或 GPIO
             │    └─ 按 enable_time 等待 / poll_enabled_time 轮询
             └─ rdev->use_count++
```

### §5.4 关：语义是"我不再需要它"

**`regulator_disable` 不等于断电。** 四道门（08 篇 §3）：
自己的 `enable_count` 归零 → 全局 `use_count` 是 1 →
DTS 没写 `always-on` → `valid_ops_mask` 有 `CHANGE_STATUS`。
任何一道不过就是**返回 0、硬件不动**。

真断电成功后会**递归 disable 上游**
（`drivers/regulator/core.c:3061-3062`）。

### §5.5 调压：区间交集 + 档位映射

```
 regulator_set_voltage(reg, min_uV, max_uV)
   ├─ 把 [min,max] 记进凭证的 voltage[]
   ├─ regulator_check_voltage()      与 constraints 求交
   ├─ regulator_check_consumers()  与**所有其他凭证**求交
   │       core.c:527 起；交集为空 → "Restricting voltage" + -EINVAL
   │       （日志措辞误导，见 07 篇 §2.2）
   ├─ 没有 CHANGE_VOLTAGE 授权但落在约束内 → 只记账，返回 0
   ├─ regulator_map_voltage()        区间 → 选择子
   │       判据是"list_voltage 填的是哪个函数"（05 篇 §2.1）
   ├─ ops->set_voltage_sel()
   └─ 按 ramp_delay 等待
```

三条结论：**参数是区间不是值**；**返回 0 不代表电压等于你写的数**；
**最终值由这一路上所有消费者的区间交集决定**。08 篇 §5 是完整解释。

### §5.6 锁：为什么是 ww_mutex

调一路电压要同时持有"它自己 + 所有上游 + 所有耦合伙伴 + 那些的上游"，
而这个集合**只能边递归边发现**、还可能是菱形 ⇒ 固定顺序取锁的办法用不了，
必须用**可回滚**的 `ww_mutex`。

三个入口：
`ww_mutex_lock` 单个（`drivers/regulator/core.c:157`）、
`regulator_lock_two`（`:218`）、
`regulator_lock_dependent` 整个闭包（`:393`）。

两条硬约束：
(a) `-EDEADLK` / `-EDEADLOCK` 是内部重试信号，**不会返回给消费者**；
(b) `rdev->mutex` 在 `ops` 回调执行期间**是持有的** ⇒
驱动回调里**不能调消费者侧 API、不能长睡眠**。

08 篇 §9 有完整解释。

### §5.7 耦合与 coupler

DTS 的 `regulator-coupled-with` 把几路归成一组，
调压时交给 coupler 决定"每步走多少"。
通用 coupler 只支持 **2 路**；3 路以上必须自己写
（04 篇 §4，`generic_coupler_attach` 在 3 路时返回 `-EPERM`）。

coupler 必须在被耦合的 rdev 注册**之前**注册
（`arch_initcall` / `subsys_initcall`，05 篇 §4）。

### §5.8 开机 30 秒后的清理

`late_initcall_sync`（`drivers/regulator/core.c:6310`）排一个
**30 秒**的延时任务（`:6305-6306`），到时遍历所有 rdev，
把"没写 always-on + 有 CHANGE_STATUS 授权 + `use_count` 为 0"
的路**关掉**，日志是 `disabling`（`:6240`）。

**判据是 `use_count` 而不是 `open_count`** ——
一个驱动 get 了但还没 enable，它的电会被关掉。
这是"开机三十几秒后设备突然挂掉"的经典成因（08 篇 §8）。

### §5.9 通知链

`regulator_register_notifier` 注册，事件有 `OVER_VOLTAGE` /
`OVER_TEMP` / `FAIL` / `PRE_DISABLE` / `DISABLE` / `VOLTAGE_CHANGE` 等。

**只有 `PRE_DISABLE` 的返回值会被看**（返回 `NOTIFY_BAD` 能否决关电），
其他事件的返回值全部丢弃。通知**不向上游传播**。

### §5.10 可观测面

| 出口 | 有什么 | 篇目 |
|---|---|---|
| dmesg | `using dummy regulator` / `Restricting voltage` / `disabling` | 07 篇 §2 |
| sysfs `/sys/class/regulator/regulator.N/` | 全部只读；`num_users` 语义有坑 | 07 篇 §3 |
| tracepoint（11 个） | `regulator:regulator_enable*` / `_set_voltage*` 等 | 07 篇 §4 |
| debugfs `regulator/` | `regulator_summary` 是最有用的一个 | 07 篇 §6 |
| 用户态 | `virtual` / `userspace-consumer` 两个调试驱动 | 07 篇 §5 |

---

## §6 端到端调用路径：从 DTS 一行到 I2C 一个字节

这一节把前面所有机制串成一条真实的链。场景：
**SD 卡槽要 3.0V，这一路是 RK808 的 LDO4，PMIC 挂在 I2C4 上。**

### §6.1 DTS 侧长什么样

```dts
/* PMIC 节点，挂在 i2c4 上 */
rk808: pmic@1b {
        compatible = "rockchip,rk808";
        reg = <0x1b>;
        vcc6-supply = <&vcc3v3_sys>;     /* ← LDO 的输入 */

        regulators {
                vcc_sd: LDO_REG4 {                    /* ← 节点名参与匹配 */
                        regulator-name = "vcc_sd";
                        regulator-min-microvolt = <1800000>;
                        regulator-max-microvolt = <3300000>;
                };
        };
};

/* 消费者节点 */
&sdmmc {
        vmmc-supply = <&vcc_sd>;          /* ← label 在这里被引用 */
};
```

### §6.2 五个阶段

```
┌─ 阶段① 启动：PMIC 驱动注册这一路 ───────────────────────────────┐
│                                                                  │
│  i2c 总线枚举 → rk808 probe                                      │
│    └─ devm_regulator_register(dev, &desc[LDO4], &config)          │
│         └─ regulator_register()             core.c:5522 起        │
│              ├─ dev_set_name("regulator.%d")   全局计数器       │
│              │     core.c:5631-5632                               │
│              ├─ regulator_of_get_init_data()                      │
│              │     用 desc->of_match = "LDO_REG4" 在              │
│              │     regulators 容器里找到那个子节点                │
│              │     → of_regulator.c 解析出：                      │
│              │         min_uV = 1800000, max_uV = 3300000         │
│              │         min != max ⇒ valid_ops_mask 得到        │
│              │            CHANGE_VOLTAGE                          │
│              │         没写 always-on ⇒ 也得到 CHANGE_STATUS   │
│              │            of_regulator.c:130-131                  │
│              ├─ 解析 vcc6-supply → rdev->supply 指向 vcc3v3_sys   │
│              ├─ set_machine_constraints()                         │
│              ├─ device_add()   /sys/class/regulator/regulator.N │
│              └─ rdev_init_debugfs()                               │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 阶段② sdmmc 驱动 probe：拿凭证 ─────────────────────────────────┐
│                                                                  │
│  devm_regulator_get(&pdev->dev, "vmmc")                           │
│    └─ _regulator_get()                      core.c:2195 起        │
│         ├─ 在 sdmmc 的 of_node 里找 "vmmc-supply"                 │
│         ├─ phandle → vcc_sd 那个 device_node → 反查到 rdev        │
│         │    没找到会走 dummy 兜底（core.c:2225-2231）          │
│         └─ create_regulator()               core.c:1868           │
│              ├─ 新建 struct regulator（凭证）                     │
│              ├─ 挂到 rdev->consumer_list                          │
│              ├─ rdev->open_count++                                │
│              ├─ sysfs_create_link_nowarn()  core.c:1910-1911      │
│              │    regulator.N/ 下出现 "mmc0-vmmc" 符号链接      │
│              └─ 建这张凭证自己的 debugfs 目录                     │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 阶段③ 调压 ─────────────────────────────────────────────────────┐
│                                                                  │
│  regulator_set_voltage(reg, 2900000, 3100000)                     │
│    └─ regulator_lock_dependent()   锁 vcc_sd + vcc3v3_sys       │
│         ├─ 记进凭证的 voltage[]                                   │
│         ├─ regulator_check_voltage()   与 [1.8V,3.3V] 求交 → OK   │
│         ├─ regulator_check_consumers() core.c:527                 │
│         │    与这一路上**其他凭证**的区间求交                  │
│         ├─ regulator_map_voltage()                                │
│         │    判据是"list_voltage 填的是哪个函数"                │
│         │    → 3.0V 对应选择子 sel = 20（举例）                   │
│         └─ ops->set_voltage_sel(rdev, 20)                         │
│              = regulator_set_voltage_sel_regmap  helpers.c        │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 阶段④ 落到硬件 ─────────────────────────────────────────────────┐
│                                                                  │
│  regmap_update_bits(rdev->regmap,                                 │
│                     desc->vsel_reg,   /* driver.h:396 */          │
│                     desc->vsel_mask,  /* driver.h:397 */          │
│                     sel)                                          │
│    └─ regmap-i2c                                                  │
│         └─ i2c_transfer() → I2C4 控制器 → 总线上两个字节           │
│              [写] 地址 0x1b, 寄存器 0x3B, 值 0x14                 │
│                                                                  │
│  这里是**跨子系统的边界**：regulator → regmap → i2c。          │
│     regulator 核心对 I2C 一无所知。                              │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 阶段⑤ 上电 ─────────────────────────────────────────────────────┐
│                                                                  │
│  regulator_enable(reg)                                            │
│    └─ _regulator_enable()                                         │
│         ├─ 先递归 enable 上游 vcc3v3_sys                        │
│         ├─ regulator->enable_count++       internal.h:52          │
│         ├─ use_count == 0 ⇒ 真去动硬件                            │
│         ├─ regulator_ops_is_valid(CHANGE_STATUS)  core.c:117-127   │
│         ├─ _regulator_do_enable()                                 │
│         │    ├─ off_on_delay 保护间隔       driver.h:429           │
│         │    ├─ ops->enable() → regmap → I2C 又一次写              │
│         │    └─ 按 desc->enable_time 等待  driver.h:427         │
│         └─ rdev->use_count++                                      │
│                                                                  │
│  这时候 sdmmc 才可以开始访问 SD 卡。                              │
└──────────────────────────────────────────────────────────────────┘
```

### §6.3 这条链上四个"看起来成功了但没有"的点

| 位置 | 现象 | 去哪查 |
|---|---|---|
| 阶段① `of_match` 写错 | rdev 注册成功、约束全 0、sysfs 里 `min_microvolts` 读出 `0` | 06 篇 §8.2 决策树 |
| 阶段② DTS 漏写 `vmmc-supply` | 拿到 dummy，后面全部"成功"，电压从没变过 | 07 篇 §2.1 / 08 篇 §1 |
| 阶段③ 没有 `CHANGE_VOLTAGE` 授权 | `set_voltage` 返回 0，**一个寄存器都没写** | 06 篇 §1.1 / 08 篇 §5 |
| 阶段⑤ `enable_time` 漏填 | `enable` 立刻返回，SD 卡初始化偶发失败 | 06 篇 §1.1（`regulator-enable-ramp-delay`） |

**四个点的共同特征：返回值都是 0。**
所以排查 regulator 问题的第一步永远不是看返回码，
而是看 `dmesg` + `regulator_summary`（07 篇）。

---

## §7 和其他子系统的关系

### §7.1 关系图

```
                          ┌──────────────┐
                          │  DT / OF     │  DTS 解析基础设施
                          └──────┬───────┘
                                 │ of_parse_phandle 等
                                 ▼
   ┌────────────┐  regmap  ┌──────────────┐  ena_gpiod  ┌──────────┐
   │  i2c / spi │◀─────────│  regulator   │────────────▶│  gpiolib │
   │  （PMIC 挂 │          │    core      │             │          │
   │   在这里） │          └──┬───┬───┬───┘             └──────────┘
   └────────────┘             │   │   │
                              │   │   └──────────────┐
        ┌─────────────────────┘   │                  │
        ▼                         ▼                  ▼
   ┌──────────┐          ┌────────────────┐   ┌──────────────┐
   │ driver   │          │  消费者驱动    │   │  PM / suspend│
   │  model   │          │  mmc/媒体/net… │   │  （suspend   │
   │ (class,  │          └────────────────┘   │   状态约束） │
   │  sysfs)  │                  ▲            └──────────────┘
   └──────────┘                  │
                        ┌────────┴────────┐
                        │  cpufreq / OPP  │  DVFS：调频同时调压
                        └─────────────────┘
```

### §7.2 逐条说明

| 子系统 | 关系 | 关键点 |
|---|---|---|
| **DT / OF** | 依赖 | arm64 上约束的**唯一来源**。`of_regulator.c` 是全部接口 |
| **driver model** | 依赖 | rdev 内嵌 `struct device`（`driver.h:631`），注册在 `regulator_class` 上 ⇒ `/sys/class/regulator/` |
| **regmap** | 依赖（可选） | `helpers.c` 那套通用回调全靠它。三级回退找 regmap（05 篇 §2.1） |
| **i2c / spi** | 间接 | PMIC 驱动是 i2c/spi 设备，但 regulator 核心**对总线一无所知** |
| **gpiolib** | 依赖（可选） | `regulator-fixed` 的使能引脚；`config.ena_gpiod` 所有权移交给核心 |
| **PM / suspend** | 双向 | DTS 的 `regulator-state-mem/standby` 等子节点描述 suspend 目标（06 篇 §5.2） |
| **cpufreq / OPP** | 消费者 | DVFS 的"调压"那一半就是 regulator。`opp-microvolt` 属性最终落到 `regulator_set_voltage` |
| **thermal** | 间接 | PMIC 过温经通知链上报，但**消费者不能否决**（03 篇 §B5.1） |
| **clk** | 无直接关系 | 两者形状很像（都是"树 + 引用计数 + DT phandle"），但代码上不互相调用。**别把 clk 的 prepare/enable 两阶段模型套过来** |
| **pinctrl** | 无直接关系 | PMIC 的 I2C 引脚由 pinctrl 配，但那是 i2c 控制器的事 |

### §7.3 一个容易混的对比：regulator vs clk

两者都是"框架型子系统 + 树形依赖 + DT phandle 引用 + 引用计数"，
所以很容易把 clk 的模型套到 regulator 上。
官方文档自己就是这么类比的 ——
`Documentation/driver-api/regulator.rst:60` 说消费者接口
「提供了一套和内核 clock 框架相似的 API」。
但差异有三处，而且都在关键位置：

| 维度 | clk | regulator |
|---|---|---|
| 使能阶段 | **两阶段**：`prepare`（可睡眠）+ `enable`（原子） | **一阶段**：只有 `enable`，且**允许睡眠** |
| 谁提供参数范围 | 驱动（`round_rate`） | **DTS 的 constraints** 授权，驱动只提供档位表 |
| 关的语义 | `clk_disable` 引用计数归零就真关 | 同样是引用计数，但多一层 `always_on` 和 `valid_ops_mask` 授权门 |
| 锁 | 全局 `prepare_lock` + `enable_lock` | 每 rdev 一把 `ww_mutex`，按依赖闭包批量取 |

相同的那部分是真的相同：**"我 disable 了但它还开着"在两个子系统里
都是正常现象**，因为都是引用计数。

---

## §8 导读：接下来看哪一篇

### §8.1 按"我想做什么"

| 我想… | 去哪 |
|---|---|
| 写一个用电的设备驱动 | **05 篇 §1**（骨架）→ 01 篇（API 签名） |
| 写一颗 PMIC 的驱动 | **05 篇 §2**（骨架）→ 04 篇 §1（helpers 清单）→ 02 篇 §4（desc 字段） |
| 在 DTS 里配一路电源 | **06 篇**（整篇） |
| 查"为什么电没通" | **07 篇 §0**（症状→工具表）→ 07 篇 §2 |
| 查"为什么电压不对" | **07 篇 §6.2**（`regulator_summary`）→ 08 篇 §5 |
| 查"为什么关不掉" | **08 篇 §3** → 07 篇 §6.2 |
| 理解某个反直觉行为 | **08 篇**（九个主题） |
| 读懂核心某段代码 | **03 篇**（Part A 时间线 / Part B 横切） |
| 确认某条论断的出处 | **99 篇**（逐篇取证表） |

### §8.2 按篇目

| 篇 | 内容 | 行数量级 | 什么时候读 |
|---|---|---|---|
| **00**（本篇） | 全貌 + 硬件背景 + 端到端路径 | 中 | 复习入口，隔几个月回来先读这篇 |
| **01** | 代码地图 + 对外 API + 错误码语义 | 中 | 查函数签名、查某个错误码什么意思 |
| **02** | 核心结构体 + 关系图 + 全局变量与锁 | 大 | 要读核心代码之前 |
| **03** | 注册/get/enable/disable/调压/锁 逐段剖析 | 最大 | 要改核心、或要彻底搞懂某个流程 |
| **04** | helpers / coupler / irq_helper / fixed 等扩展框架 | 中 | 写 PMIC 驱动、或要写 coupler |
| **05** | 五种角色的代码骨架 | 中 | 动手写代码时 |
| **06** | DTS 每条属性 + 配错怎么定位 | 大 | 板级调试时用得最多 |
| **07** | dmesg / sysfs / tracepoint / debugfs / 错误码分诊 | 大 | 出问题时的第一站 |
| **08** | 九个最容易想错的地方（费曼式） | 中 | 觉得"行为和预期不一致"时 |
| **99** | 证据等级 / 未验证项 / 逐篇取证表 / 自陈问题 | 中 | 要引用本文档集的结论到别处时 |

### §8.3 如果只有十分钟

按这个顺序读四段，能覆盖 80% 的实际问题：

1. **本篇 §3.3**（三条最容易搞混的对应关系）
2. **08 篇 §1**（dummy —— "成功"是假的）
3. **08 篇 §4**（`always-on` 会没收开关权）
4. **07 篇 §6.2**（`regulator_summary` 怎么读）
