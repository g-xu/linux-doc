---
title: 设备树
---

# RTC 子系统 · 设备树

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇覆盖 RTC 的 DT 绑定、通用属性、arm64 平台实例、`start-year` 机制的完整代码路径。
> 行号不在本篇维护——需要精确到行的地方去 `03-internals.md`，取证命令去 `99-evidence.md`。

## §1 绑定文件概况

`Documentation/devicetree/bindings/rtc/` 下有 **82 个绑定文件**（大部分是 `.yaml`，少量遗留 `.txt`）。

| 文件 | 内容 |
|---|---|
| `rtc.yaml` | **通用绑定**——定义所有 RTC 共享的属性（`start-year`、`wakeup-source`、`quartz-load-femtofarads` 等） |
| `trivial-rtc.yaml` | 不需要特殊属性的简单 RTC 列表 |
| `arm,pl031.yaml` | ARM PrimeCell PL031（arm64 最常见的内置 RTC） |
| 其余 ~79 个 | 各芯片专用绑定（`nxp,pcf8563.yaml`、`rtc-ds1307.yaml`、`qcom-pm8xxx-rtc.yaml` 等） |

---

## §2 通用属性（`rtc.yaml`）

`rtc.yaml` 定义了所有 RTC 节点共享的属性：

| 属性 | 类型 | 必需？ | 含义 | 框架/驱动 |
|---|---|---|---|---|
| `$nodename` | pattern | 是 | 必须匹配 `^rtc(@.*\|-[0-9a-f])*$` | — |
| `start-year` | `uint32` | 否 | 硬件范围的起始年（§4 详解） | **框架**直接读 |
| `wakeup-source` | flag | 否 | 声明可以作为唤醒源 | `device_may_wakeup()` |
| `quartz-load-femtofarads` | uint32 | 否 | 石英晶体负载电容（fF） | 驱动 |
| `aux-voltage-chargeable` | 0/1 | 否 | 备份电池/超级电容是否可充电 | 驱动 |
| `trickle-resistor-ohms` | uint32 | 否 | trickle charger 电阻值 | 驱动 |
| `trickle-voltage-millivolt` | uint32 | 否 | trickle charger 电压 | 驱动 |
| `trickle-diode-disable` | flag | **已弃用** | 禁用 trickle charger 二极管 | 驱动 |

⚠️ `trickle-diode-disable` 已标记为 `deprecated`。`start-year` 是唯一的**框架级**属性——`rtc_device_get_offset()` 直接通过 `device_property_read_u32()` 读取，不需要驱动参与。其余属性都是驱动级的。

---

## §3 arm64 平台实例

arm64 DTS 里提到 `rtc` 的有 **217 个文件**。以下是几个典型配置，覆盖不同的总线类型和使用场景。

### §3.1 ARM PL031（Juno 开发板）—— Platform 类

```dts
rtc@170000 {
    compatible = "arm,pl031", "arm,primecell";
    reg = <0x170000 0x10000>;
    interrupts = <GIC_SPI 0 IRQ_TYPE_LEVEL_HIGH>;
    clocks = <&soc_smc50mhz>;
    clock-names = "apb_pclk";
};
```

PL031 是 ARM 标准 PrimeCell 外设，直接映射寄存器，不需要 I2C/SPI。`clock-names = "apb_pclk"` 是 PrimeCell 框架的标准命名。PL031 没有 `wakeup-source`——在 Juno 板上它不用作唤醒源。

### §3.2 I2C RTC（典型外挂芯片）

```dts
&i2c1 {
    rtc@51 {
        compatible = "nxp,pcf8563";
        reg = <0x51>;
        interrupt-parent = <&gpio1>;
        interrupts = <15 IRQ_TYPE_EDGE_FALLING>;
        wakeup-source;
    };
};
```

`wakeup-source` 告诉框架这个 RTC 可以作为休眠唤醒源。alarmtimer 的 `class_interface` 回调（`kernel/time/alarmtimer.c:97`）检查 `device_may_wakeup()` 来决定是否选中这个 RTC。

⚠️ `interrupt-parent` 指向 GPIO 控制器而非 GIC——因为 I2C RTC 的中断线通常接在 GPIO 上，不是 SoC 内部中断。

### §3.3 带 `start-year` 的 RTC

```dts
rtc@0 {
    compatible = "maxim,ds1339";
    reg = <0x68>;
    start-year = <2000>;
    trickle-resistor-ohms = <250>;
};
```

`start-year = <2000>` 让框架把硬件的 0 年映射到公元 2000 年。如果硬件只存 2 位年份（00–99），这个属性让框架知道 0 代表 2000 而不是 1900。

### §3.4 PMIC 内置 RTC（Qualcomm）

```dts
&spmi_bus {
    pm8916 {
        rtc@6000 {
            compatible = "qcom,pm8916-rtc";
            reg = <0x6000 0x100>;
            interrupts = <0x0 0x61 0x0 IRQ_TYPE_EDGE_RISING>,
                         <0x0 0x61 0x1 IRQ_TYPE_EDGE_RISING>;
            interrupt-names = "alarm", "trigger";
            allow-set-time;
        };
    };
};
```

Qualcomm PMIC 的 RTC 通过 SPMI 总线访问。`allow-set-time` 是 Qualcomm 特有的属性，控制是否允许用户态修改时间。注意它有两个中断——`alarm` 是闹钟中断，`trigger` 是周期触发中断。

### §3.5 多 RTC 系统

```dts
aliases {
    rtc0 = &rtc_pmic;       /* PMIC 内置 RTC → alarmtimer 优先选它 */
    rtc1 = &rtc_external;   /* 外挂 I2C RTC → 备用 */
};

rtc_pmic: rtc@6000 { ... };
rtc_external: rtc@51 { ... };
```

⚠️ DT 里的 `rtc` alias 编号**直接影响注册顺序**。alarmtimer 选第一个满足条件的 RTC——如果 `rtc0` 支持闹钟且 `device_may_wakeup()`，它就是 alarmtimer 的 `rtcdev`。如果两个 RTC 都满足条件，`rtc0` 获胜。

---

## §4 `start-year` 机制

`start-year` 是框架级属性——`rtc_device_get_offset()`（`drivers/rtc/class.c:270-335`）直接通过 `device_property_read_u32()` 读取，不需要驱动参与。

### §4.1 完整代码路径

```
__devm_rtc_register_device()                // class.c:389
  └─ rtc_device_get_offset(rtc)             // class.c:270
       ├─ device_property_read_u32(rtc->dev.parent, "start-year", &start_year)
       │   ├─ 成功 → start_secs = mktime64(start_year, 1, 1, 0, 0, 0)
       │   │         rtc->set_start_time = true
       │   └─ 失败 → return（offset_secs 保持 0）
       │
       ├─ 如果 start_secs < range_min:
       │   └─ offset_secs = start_secs - range_min（负值，反向平移）
       │
       ├─ 如果 start_secs > range_max:
       │   └─ offset_secs = start_secs - range_min（正值，正向平移）
       │
       └─ 如果 range_min <= start_secs <= range_max:
           └─ offset_secs = 0（无需偏移）
```

### §4.2 映射示例

**场景 A**：硬件是 8-bit 年份（2000–2099），`start-year = <2000>`

| 字段 | 值 |
|---|---|
| `range_min` | `RTC_TIMESTAMP_BEGIN_2000` = 946684800 |
| `range_max` | `RTC_TIMESTAMP_END_2099` = 4102444799 |
| `start_secs` | mktime64(2000,1,1) = 946684800 |
| `start_secs == range_min`？ | **是** |
| `offset_secs` | **0**（无需偏移） |

效果：硬件的 00 年 = 2000 年，99 年 = 2099 年。最直接的情况。

**场景 B**：硬件是 8-bit 年份（2000–2099），`start-year = <2100>`

| 字段 | 值 |
|---|---|
| `start_secs` | mktime64(2100,1,1) = 4102444800 |
| `start_secs > range_max`？ | **是**（4102444800 > 4102444799） |
| `offset_secs` | `start_secs - range_min` = 3155760000（100 年的秒数） |

效果：硬件的 00 年 = 2100 年，99 年 = 2199 年。系统可以用 2100–2199 的范围。

**场景 C**：硬件是 8-bit 年份（2000–2099），`start-year = <1900>`

| 字段 | 值 |
|---|---|
| `start_secs` | mktime64(1900,1,1) = -2208988800 |
| `start_secs < range_min`？ | **是** |
| `offset_secs` | `start_secs - range_min` = -3155673600 |

效果：读到的硬件值加上偏移后映射到 1900–1999 的范围。

⚠️ 如果 DT 没写 `start-year`，`set_start_time` 为 false，`rtc_device_get_offset()` 直接返回，`offset_secs` 保持 0。范围校验仍然生效（`rtc_valid_range()` 用 `range_min`/`range_max` 直接校验），但没有偏移映射。

### §4.3 读时间时的偏移应用

```
rtc_read_time(rtc, tm)
  ├─ __rtc_read_time(rtc, tm)        // 读硬件原始值
  └─ rtc_add_offset(rtc, tm)         // interface.c:23
       ├─ 如果 offset_secs == 0 → 不做任何事
       ├─ 如果读到的值在扩展范围与原始范围的重叠区 → 不加偏移
       └─ 否则 → tm 的秒数 += offset_secs
```

重叠区的判断是为了避免「硬件值本身已经合法，但加偏移后反而超出范围」的情况。

---

## §5 节点命名规范

`rtc.yaml` 要求节点名匹配 `^rtc(@.*|-[0-9a-f])*$`。合法的名字：

| 总线 | 合法名字 | 示例 |
|---|---|---|
| I2C | `rtc@<addr>` | `rtc@51` |
| SPI | `rtc@<cs>` | `rtc@0` |
| Platform | `rtc-<id>` 或 `rtc@<addr>` | `rtc-0`、`rtc@10000` |
| MFD 子设备 | `rtc` | `rtc`（无地址后缀） |

⚠️ 老式 DTS 里可能看到 `rtc-ds1307@68` 这种不符合规范的命名。新写的 DTS 应该用 `rtc@68`。

---

## §6 `wakeup-source` 与 alarmtimer 的交互

DT 里的 `wakeup-source` 属性影响 `device_may_wakeup()` 的返回值。alarmtimer 的 `class_interface` 回调（`kernel/time/alarmtimer.c:97`）检查：

```c
if (!device_may_wakeup(rtc->dev.parent))
    return -1;  // 不选这个 RTC
```

完整的选中条件（`alarmtimer_rtc_add_device()`，`kernel/time/alarmtimer.c:84-110`）：

1. **还没有选中过**（`rtcdev == NULL`）——只选第一个
2. **支持闹钟**（`test_bit(RTC_FEATURE_ALARM, rtc->features)`）
3. **能作为唤醒源**（`device_may_wakeup(rtc->dev.parent)`）
4. **模块引用计数能增加**（`try_module_get(rtc->owner)` 成功）

四个条件全部满足后，alarmtimer 把这个 RTC 记为 `rtcdev`。后续所有 `CLOCK_REALTIME_ALARM` 的定时器都通过这个 RTC 的硬件闹钟实现休眠唤醒。

⚠️ **第一个匹配的 RTC 获胜**。如果系统有多个 RTC，只有第一个满足条件的被选中。DT 里的 `rtc` alias 编号影响注册顺序。

```dts
aliases {
    rtc0 = &rtc_pmic;    // 优先注册 → alarmtimer 优先选它
    rtc1 = &rtc_external;
};
```

如果 `rtc0` 不满足条件（比如没有 `wakeup-source`），alarmtimer 会尝试 `rtc1`。但如果 `rtc0` 已经满足条件并被选中，后续注册的 `rtc1` 即使更合适也不会被选——alarmtimer 只看第一个。

---

## §7 绑定格式迁移

RTC 绑定正在从 `.txt` 迁移到 `.yaml`。当前状态：

| 格式 | 数量 | 状态 |
|---|---|---|
| `.yaml` | ~65 | 新标准，有 schema 验证 |
| `.txt` | ~17 | 遗留，逐步迁移中 |

`.txt` 绑定的芯片包括：`abracon,abx80x`、`armada-380-rtc`、`dallas,ds1390`、`haoyu,hym8563`、`maxim,ds3231` 等。这些芯片的驱动功能完整，只是绑定文档还没转成 yaml。

⚠️ `.yaml` 绑定会被 `dt-binding` 工具自动验证（`make dtbs_check`），`.txt` 绑定不会。如果你写的 DTS 通过了 `dtbs_check` 但运行时 RTC 不工作，问题不在绑定格式——检查驱动 probe 和 IRQ 配置。
