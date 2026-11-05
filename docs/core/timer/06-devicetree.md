---
title: 设备树
---

# timer 06：设备树与其它发现路径

## §0 发现路径分诊表

⚠️ **别先问"timer 有没有 DT 绑定"** —— 这个子系统有**五条并存的发现路径**，
DT 只是其中最重要的一条。

| # | 发现路径 | 谁扫描 | 走 DT 吗 | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|---|
| 1 | **`TIMER_OF_DECLARE()`** | `timer_probe()`，在 `time_init()` 里（03 §A5） | **走** | **主路径** | **§1–§9 全覆盖** |
| 2 | **普通 `platform_driver`** | 驱动模型的正常 probe | **走** | 少数外设定时器 | §0.3 说明差别，不展开 |
| 3 | **`TIMER_ACPI_DECLARE()`** | ACPI GTDT 表 | **不走** | **arm64 可以，但本套裁掉** | ⚠️ **不覆盖**（§12.3 登记） |
| 4 | **硬编码 `core_initcall`** | 内核自己 | 不走 | **一定会有** | §0.4 |
| 5 | **CPU 热插拔回调** | `cpuhp` 状态机 | 不走 | **每 CPU 定时器靠它** | §0.5 |

### §0.1 路径 1 和路径 2 的规模

```bash
grep -rlw 'TIMER_OF_DECLARE' --include=*.c . | wc -l      # → 72
grep -rlw 'platform_driver' drivers/clocksource/*.c | wc -l  # → 13
```

⇒ **72 : 13**，`TIMER_OF_DECLARE()` 是绝对主流。

### §0.2 为什么大多数定时器不能用普通 `platform_driver`

**因为它们要在驱动模型存在之前就工作**。
03 §A5 的时间线里 `time_init()` 在 `init/main.c:1064`，
而 platform 总线、`driver_register()` 那一整套要等到 `subsys_initcall` 之后。
⇒ **系统需要在"还没有设备模型"的时候就有时钟**，所以有了
`TIMER_OF_DECLARE()` 这条绕过驱动模型的早期路径。

### §0.3 那 13 个用 `platform_driver` 的是什么

```bash
grep -rlw 'platform_driver' drivers/clocksource/*.c
```

`timer-gxp.c` · `ingenic-ost.c` · `sh_tmu.c` · `renesas-ostm.c` · `sh_cmt.c` ·
`em_sti.c` · `ingenic-timer.c` · `timer-tegra186.c` · `timer-stm32-lp.c` ·
`timer-cadence-ttc.c` · `sh_mtu2.c` · `timer-ixp4xx.c` · `timer-ti-dm.c`

**共同点：它们都不是"系统主定时器"**——是额外的外设定时器，
系统已经有别的时钟源在跑了，所以可以等到正常 probe 时机。
⚠️ **判据**：如果你的定时器要承担周期 tick 或系统时钟源，
**必须走 `TIMER_OF_DECLARE()`**。

### §0.4 路径 4：永远存在的兜底

`jiffies` 时钟源用 `core_initcall`（`kernel/time/jiffies.c:69`）注册，
**不需要任何 DT 节点**。这是 03 §A4 讲的那个 rating=1 的兜底时钟源。
⇒ **所以"DT 里一个定时器都没写"的系统也能起来**，只是时间精度只有 `1/HZ`。

### §0.5 路径 5：每 CPU 定时器靠热插拔回调

**一个 DT 节点，N 个 CPU 的定时器**。arm64 的架构定时器就是这样：
DT 里只有一个节点，但每个 CPU 都有自己的 `CNTV_CVAL_EL0`。
实现方式是注册 cpuhp 回调：

```
arch_timer_starting_cpu()     drivers/clocksource/arm_arch_timer.c:984
arch_timer_dying_cpu()
     ↑ 由 cpuhp_setup_state() 登记（同文件 :1240）
```

⇒ **CPU 上线时才真正配置那个 CPU 的定时器**。
⚠️ 所以"DT 节点解析成功"≠"所有 CPU 的定时器都好了"。

### §0.6 按任务找章节

| 我想… | 去 |
|---|---|
| 抄一个能用的 arm64 timer 节点 | §9 |
| 知道核心到底读哪些属性 | §1 |
| 搞明白 `always-on` 是干什么的 | §5（**本篇最有价值的一节**） |
| DTS 改了但定时器没起来 | §10 |
| 看到一句报错想知道什么意思 | §11 |
| 写一个新平台的定时器绑定 | §2 + §8 |

---

## §1 核心真正会读的属性总表

**以 `arm,arch_timer.yaml`（128 行）为准**——这是 arm64 上唯一必须懂的绑定。
**每条都定位到消费代码的行号**：

| 属性 | 写在哪个节点 | 必需 | 谁消费 | 消费位置 |
|---|---|---|---|---|
| `compatible` | 根下的 `timer` 节点 | **是** | `TIMER_OF_DECLARE()` 的匹配表 | `drivers/clocksource/arm_arch_timer.c:1434-1435` |
| `interrupts` | 同上 | **是**（或 `interrupts-extended`） | 驱动 | 通过 `irq_of_parse_and_map()` |
| `interrupt-names` | 同上 | 否 | 驱动 | `drivers/clocksource/arm_arch_timer.c:1385` |
| `clock-frequency` | 同上 | 否 | **覆盖硬件寄存器里的频率** | `drivers/clocksource/arm_arch_timer.c:1029` |
| `always-on` | 同上 | 否 | **决定要不要 tick 广播** | `drivers/clocksource/arm_arch_timer.c:1401` |
| `arm,no-tick-in-suspend` | 同上 | 否 | 驱动 | `drivers/clocksource/arm_arch_timer.c:1423` |
| `arm,cpu-registers-not-fw-configured` | 同上 | 否 | 驱动 | `drivers/clocksource/arm_arch_timer.c:1411` |
| `fsl,erratum-a008585` | 同上 | 否 | 驱动的勘误表 | `drivers/clocksource/arm_arch_timer.c:443-444` 一带 |
| `hisilicon,erratum-161010101` | 同上 | 否 | 同上 | `drivers/clocksource/arm_arch_timer.c:454-455` 一带 |
| `allwinner,erratum-unknown1` | 同上 | 否 | 同上 | 同一张勘误表 |

**`required` 段只有一条**（绑定文档 `Documentation/devicetree/bindings/timer/` 下那份架构定时器 yaml 的 `required:` 与 `oneOf:` 两段）：

```yaml
required:
  - compatible

oneOf:
  - required:
      - interrupts
  - required:
      - interrupts-extended
```

⇒ **只有 `compatible` 是硬性必需**，中断二选一。
⚠️ **`additionalProperties: false`** ⇒ 写了绑定里没有的属性，
`make dtbs_check` 会报错（§10.1）。

---

## §2 `compatible`

```dts
	timer {
		compatible = "arm,armv8-timer";
	};
```

**驱动侧的匹配表**（`drivers/clocksource/arm_arch_timer.c`）：

| compatible | 位置 |
|---|---|
| `"arm,armv8-timer"` | `drivers/clocksource/arm_arch_timer.c:1435` |
| `"arm,armv7-timer"` | `drivers/clocksource/arm_arch_timer.c:1434` |
| `"arm,armv7-timer-mem"` | `drivers/clocksource/arm_arch_timer.c:1619`（MMIO 变体，不同的 init 函数） |

⇒ **arm64 上就用 `arm,armv8-timer`**。
两个 CPU 版共用同一个 `arch_timer_of_init()`（`:1372`），
MMIO 版走另一个入口。

⚠️ **`compatible` 列表可以写多项**（06 通用规则），
但架构定时器一般只写一项——因为它不是"某个 SoC 的实现"，而是架构规定的。

---

## §3 `interrupts`：四个 PPI

```dts
	timer {
		compatible = "arm,armv8-timer";
		interrupts = <GIC_PPI 13 IRQ_TYPE_LEVEL_LOW>, /* cntpsirq  安全物理  */
			     <GIC_PPI 14 IRQ_TYPE_LEVEL_LOW>, /* cntpnsirq 非安全物理 */
			     <GIC_PPI 11 IRQ_TYPE_LEVEL_LOW>, /* cntvirq   虚拟       */
			     <GIC_PPI 10 IRQ_TYPE_LEVEL_LOW>; /* cnthpirq  hyp        */
	};
```

**顺序是绑定规定的，不能换**
（同一份 yaml 的 `interrupts:` 段的 `items:` 列表）：

| # | 名字 | 用途 |
|---|---|---|
| 0 | `phys` (secure) | 安全世界的物理定时器 |
| 1 | `phys` (non-secure) | 非安全物理 |
| 2 | `virt` | **虚拟定时器——Linux 跑在 EL1 时用的就是它** |
| 3 | `hyp` | hypervisor 定时器 |

**必须是 PPI（Per-Processor Interrupt）**，不是 SPI——
因为每个 CPU 有自己的定时器（§0.5）。

**`interrupt-names`**（可选，`:44`）：给了名字之后驱动就按名字取而不按索引
（`drivers/clocksource/arm_arch_timer.c:1385` 的 `of_property_read_bool(np, "interrupt-names")`
先判断有没有，有就走按名字的路径）。
⇒ **按名字比按索引稳**，但架构定时器的索引是架构规定的，实际风险不大。

⚠️ **`IRQ_TYPE_LEVEL_LOW` 还是 `_HIGH` 取决于 SoC**——
本树 `arch/arm64/boot/dts/ti/k3-am65.dtsi:54-57` 用的是 `LOW`。
写错的表现是**中断风暴或完全收不到中断**。

---

## §4 `clock-frequency`：什么时候必须写

**正常情况下不该写**。架构定时器的频率应该由固件写进
`CNTFRQ_EL0` 寄存器，Linux 直接读。

**消费代码**（`drivers/clocksource/arm_arch_timer.c:1029`）：

```c
	if (of_property_read_u32(np, "clock-frequency", &arch_timer_rate))
		/* 属性不存在 ⇒ 读硬件寄存器 */
```

⚠️ **注意这里的错误码语义**：`of_property_read_u32()` 属性不存在时
返回 `-EINVAL`（**不是 `-ENOENT`**），代码这里用的是"非 0 就是没读到"的写法，
所以不受影响。⇒ of 那套文档的 API 篇专门有一小节讲这个错误码陷阱。

**绑定文档的原话**（同一份 yaml 的 `clock-frequency:` 段的 `description`）：
"Should be present only where necessary to work around broken firmware
which does not configure CNTFRQ on all CPUs to a uniform correct value."

⇒ **写了它就等于承认固件有 bug**。
⚠️ **写错的后果最隐蔽**：系统正常起来，但**所有时间都按错误比例缩放**——
`sleep 1` 可能实际睡 2 秒，`udelay()` 也全错（因为 `lpj_fine` 是由它算的，03 §A5）。

---

## §5 `always-on`：本篇最有价值的一条

### §5.1 它直接控制要不要 tick 广播

**一行代码就把 DT 属性和 04 §1 那整套广播机制连起来了**：

```c
	arch_timer_c3stop = !of_property_read_bool(np, "always-on");
```
—— `drivers/clocksource/arm_arch_timer.c:1401`

| DTS 写了 `always-on` | `arch_timer_c3stop` | clockevent 的 `features` | 后果 |
|---|---|---|---|
| **写了** | **0** | 不设 `CLOCK_EVT_FEAT_C3STOP` | **深度休眠时定时器继续跑，不需要广播** |
| **没写**（默认） | **1** | 设 `CLOCK_EVT_FEAT_C3STOP` | **核心安排 tick 广播**（04 §1.2） |

**绑定文档的定义**（同一份 yaml 的 `always-on:` 段的 `description`）：
"If present, the timer is powered through an always-on power domain,
therefore it never loses context."

### §5.2 ⚠️ 两个方向都会出问题

| 写错的方向 | 表现 |
|---|---|
| ⚠️ **该写没写** | **只是多一层广播开销**，功能正确 ⇒ **静默的性能损失**，很难发现 |
| ⚠️ **不该写却写了** | **深度休眠后定时器停了但没人代为计时** ⇒ **CPU 睡过头 / 系统看起来卡死** |

⇒ **第二种是本篇最危险的 DTS 错误**：症状是"进深度 idle 后系统响应变得极慢
或者干脆不醒"，而且**只在特定电源状态下出现**。
**判据**：`cat /proc/timer_list` 看有没有广播设备在承担 tick（07 篇）。

⇒ **不确定就别写** ——默认（不写）是安全的那一侧。

---

## §6 `arm,no-tick-in-suspend`

```c
	... of_property_read_bool(np, "arm,no-tick-in-suspend");
```
—— `drivers/clocksource/arm_arch_timer.c:1423`

**含义**（同一份 yaml 的 `arm,no-tick-in-suspend:` 段）：主计数器在系统进低功耗态时**不走**。

⇒ **它控制 clocksource 的 `CLOCK_SOURCE_SUSPEND_NONSTOP` 标志**：
- **不写**（默认）⇒ 认为计数器在 suspend 期间继续走 ⇒
  **可以用它来算"睡了多久"**，不需要 RTC
- **写了** ⇒ 核心必须另找办法补偿挂起时间（走 RTC）

⚠️ **写错的后果**：`ktime_get_boottime()` 在 resume 后不准
（05 §7.3 讲的那个"含挂起时间"的时基）。

---

## §7 `arm,cpu-registers-not-fw-configured`

```c
	if (... || of_property_read_bool(np, "arm,cpu-registers-not-fw-configured"))
```
—— `drivers/clocksource/arm_arch_timer.c:1411`

**含义**（同一份 yaml 的 `arm,cpu-registers-not-fw-configured:` 段）：
"Firmware does not initialize any of the generic timer CPU registers."

⇒ **它让内核走"物理定时器"而不是"虚拟定时器"**：
如果固件没配好 CPU 侧寄存器（尤其 `CNTVOFF`），虚拟定时器的偏移就是垃圾，
只能退回用物理定时器。

⚠️ **这个属性通常和 `clock-frequency` 一起出现**——两者都是"固件不靠谱"的标记。
新平台不该需要它。

---

## §8 勘误（errata）属性

| 属性 | 针对 | 绑定位置 |
|---|---|---|
| `fsl,erratum-a008585` | NXP/QorIQ：读计数器可能得到错误值 | 该 yaml 的 `fsl,erratum-a008585:` 段 |
| `hisilicon,erratum-161010101` | 海思：同类问题 | 该 yaml 的 `hisilicon,erratum-161010101:` 段 |
| `allwinner,erratum-unknown1` | 全志 | 该 yaml 的 `allwinner,erratum-unknown1:` 段 |

**机制**：驱动里有一张勘误工作区表，命中的话把
`read_cntpct_el0` / `read_cntvct_el0` 换成带重读校验的版本
（`drivers/clocksource/arm_arch_timer.c:443-444` 是 fsl 的两个替换函数，
`:454-455` 是海思的）。

⇒ **只有那几个具体 SoC 需要**；写在通用 dtsi 里是错的。

---

## §9 完整的 arm64 实例（本树真实节点，可以 diff）

```bash
sed -n '52,58p' arch/arm64/boot/dts/ti/k3-am65.dtsi
```

```dts
	a53_timer0: timer-cl0-cpu0 {
		compatible = "arm,armv8-timer";
		interrupts = <GIC_PPI 13 IRQ_TYPE_LEVEL_LOW>, /* cntpsirq */
			     <GIC_PPI 14 IRQ_TYPE_LEVEL_LOW>, /* cntpnsirq */
			     <GIC_PPI 11 IRQ_TYPE_LEVEL_LOW>, /* cntvirq */
			     <GIC_PPI 10 IRQ_TYPE_LEVEL_LOW>; /* cnthpirq */
	};
```
—— `arch/arm64/boot/dts/ti/k3-am65.dtsi:52-58`

**三点值得注意**：

| 观察 | 说明 |
|---|---|
| **只有两个属性** | `compatible` + `interrupts` —— **这就是一个健康平台的样子** |
| **没有 `clock-frequency`** | **说明这块板的固件正确设置了 `CNTFRQ_EL0`**（§4） |
| **没有 `always-on`** | ⇒ `arch_timer_c3stop = 1` ⇒ **这块板会用 tick 广播**（§5.1） |
| 节点没有 `reg` | **CPU 系统寄存器访问，不占地址空间** ⇒ 也就不需要父节点的 `ranges` |
| 节点名带 label | `a53_timer0:` 只是给 overlay/引用用，核心不看 |

⚠️ **注意它挂在根节点下，不在 `soc {}` 里**——
因为它不是总线上的设备，是 CPU 自带的。

---

## §10 定位：三层排查

### §10.1 第一层：编译期

```bash
# ① 语法和绑定校验
make dtbs_check DT_SCHEMA_FILES=timer/arm,arch_timer.yaml

# ② 看编译出来的 DTB 里到底有什么
dtc -I dtb -O dts arch/arm64/boot/dts/ti/k3-am654-base-board.dtb | less
```

⚠️ **`additionalProperties: false`**（§1）意味着**拼错的属性名会被 `dtbs_check` 抓到**。
这是 timer 绑定的一个优点——很多子系统的绑定没这么严。

### §10.2 第二层：运行期静态（设备有没有建出来）

| 检查 | 命令 | 期望 |
|---|---|---|
| 时钟源选对了吗 | `cat /sys/devices/system/clocksource/clocksource0/current_clocksource` | **`arch_sys_counter`**，不是 `jiffies` |
| 有哪些候选 | `cat /sys/devices/system/clocksource/clocksource0/available_clocksource` | 至少有 arch 定时器 |
| tick 设备是谁 | `cat /proc/timer_list \| head -40` | 每 CPU 都有 `arch_sys_timer` |
| 进高精度了吗 | `grep resolution /proc/timer_list \| head -2` | **不该是 `1000000 nsecs`（=1ms，即 `1/HZ`）** |
| DT 节点解析成功了吗 | `ls /sys/firmware/devicetree/base/timer*/` | 属性都在 |

⚠️ **`current_clocksource` 显示 `jiffies` 就是明确的失败信号**——
说明 DT 节点没被识别，或者驱动 init 返回了错误（03 §A4 的兜底生效了）。

### §10.3 第三层：运行期动态（行为对不对）

| 症状 | 怀疑的属性 | 判据 |
|---|---|---|
| 所有时间按固定比例偏 | `clock-frequency`（§4） | `time sleep 10` 看实际耗时；对比 `CNTFRQ_EL0` |
| 深度 idle 后响应极慢/不醒 | **`always-on` 写错了**（§5.2） | `/proc/timer_list` 看有没有广播设备 |
| resume 后 boottime 不准 | `arm,no-tick-in-suspend`（§6） | 对比 `/proc/uptime` 和真实经过时间 |
| 完全收不到定时器中断 | `interrupts` 的触发类型或 PPI 号 | `cat /proc/interrupts \| grep -i timer` |
| 中断风暴 | 同上 | 同上，看计数涨得多快 |
| 时间偶发跳变 | 需要勘误属性但没写（§8） | **看门狗日志**（07 篇） |

---

## §11 错误消息 → 原因映射表

| 消息 | 出处 | 原因 |
|---|---|---|
| `Unable to initialise architected timer.` + panic | `arch/arm64/kernel/time.c:66` | **`arch_timer_get_rate()` 返回 0** ⇒ 固件没设 `CNTFRQ_EL0` 且 DTS 也没写 `clock-frequency`（§4） |
| `current_clocksource` 是 `jiffies` | 不是消息，是状态 | **DT 节点没被识别**（§10.2） |
| `.resolution: 1000000 nsecs` | `/proc/timer_list` | **还在低精度模式**：clockevent 没设 `CLOCK_EVT_FEAT_ONESHOT`，或 `CONFIG_HIGH_RES_TIMERS=n`（05 §3.2） |
| 看门狗把时钟源标为 unstable | clocksource 看门狗（04 §5） | **需要勘误属性**（§8），或硬件真的有问题 |

⚠️ **这张表故意很短**：本子系统的 DTS 错误**大多不产生消息，只产生错误行为**
（§4 的比例缩放、§5 的睡过头）。⇒ **所以 §10.2 那几条状态检查比看日志重要。**

---

## §12 本篇覆盖情况

### §12.1 覆盖了什么

| 内容 | 覆盖 |
|---|---|
| `arm,arch_timer.yaml` 的全部 10 个属性 | **每条都有消费代码行号**（§1 的表） |
| 五条发现路径 | **§0 分诊表**，其中路径 1/4/5 展开 |
| 本树真实 arm64 节点 | §9（`arch/arm64/boot/dts/ti/k3-am65.dtsi:52-58`） |
| 三层定位 | §10 |

### §12.2 ⚠️ 没有覆盖的

| 没覆盖 | 为什么 |
|---|---|
| **其余 69 个定时器绑定** | `Documentation/devicetree/bindings/timer/` 共 **70 个文件**，本篇只讲 arm64 必须懂的那一个。其它平台的定时器绑定形状差别很大，讲了对 arm64 读者没有用 |
| 路径 2（`platform_driver`）的 DTS 写法 | 那 13 个驱动各有自己的绑定；**形状就是普通 platform 设备**（有 `reg`、有 `clocks`），没有 timer 特有的东西 |
| `arm,armv7-timer-mem`（MMIO 变体） | arm64 上极少用；它的节点形状完全不同（有 `reg` 和子节点） |

### §12.3 显式裁掉的

| 裁掉的 | 为什么 |
|---|---|
| **ACPI / GTDT 路径**（§0 的路径 3） | **arm64 服务器会用**，但本套 arm64-only 的范围限定在 DT。⚠️ **这不是"不存在"**——`TIMER_ACPI_DECLARE()` 全树只有 1 个使用者，就是 `drivers/clocksource/arm_arch_timer.c`，所以**同一个驱动同时支持两条路** |
| x86 的 HPET / PIT 绑定 | 非目标架构（04 §12.3 也裁了 `hpet.rst`） |

⚠️ **ACPI 那条要特别说明**：因为它和 DT 走**同一个驱动**，
所以 §1–§8 讲的**行为**在 ACPI 系统上大部分同样成立，
只是**参数来源**从 DT 属性换成 GTDT 表字段。
⇒ 如果你在 arm64 服务器上工作，把 §1 的属性表当作"概念清单"读，
具体字段名去查 ACPI 规范。
