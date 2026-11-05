---
title: 设备树
---

# 06 设备树：属性怎么写、由谁消费、配错了怎么查

> arm64-only。本篇的取证主力是 `drivers/regulator/of_regulator.c`（807 行）
> 和 `drivers/regulator/core.c` 里的 DT 钩子，
> 实例一律取自 `arch/arm64/boot/dts/`，读者能直接 diff。

## §0 按任务找章节

| 你要做的事 | 去哪一节 |
|---|---|
| 给我的设备接一路电（消费者侧） | **§2** |
| 我写了 PMIC 驱动，DTS 该长什么样 | **§3** + **§10** |
| 板子上有个 GPIO 控制的固定电压 | **§4** |
| 让某一路在 suspend 里关掉 / 降压 | **§5** |
| 配过压/过温保护阈值 | **§6** |
| 多路联动调压 | **§7**（arm64 上零使用者） |
| 我 DTS 写了但好像没生效 | **§8** 三层定位 + **§9** 错误消息表 |
| 想看一个完整的真实例子 | **§10** |

**本子系统的 DTS 有两侧，很多人只知道一侧**：

```
消费者节点                              提供者节点
┌──────────────────────┐               ┌──────────────────────────┐
│ mmc@fe320000 {       │               │ pmic@1b {                │
│   vmmc-supply =      │──── phandle ─→│   regulators {           │
│        <&vcc3v0_sd>; │               │     vcc3v0_sd: LDO_REG4 {│
│ };                   │               │       regulator-name=…;  │
└──────────────────────┘               │       regulator-min-…;   │
   ↑ §2：属性名是                      │     };                   │
     "<你在驱动里 get 的名字>-supply"  │   };                     │
                                       │ };                       │
                                       └──────────────────────────┘
                                          ↑ §3：节点名要和驱动的
                                            `of_match` 逐字相同
```

这两侧由**完全不同的代码**消费：
消费者侧在 `core.c` 的 `of_get_regulator`（`:471`），
提供者侧在 `of_regulator.c` 的 `regulator_of_get_init_node`（`:463`）。
两边写错的症状完全不同 —— 这是本篇分章的依据。

## §1 核心真正会读的属性总表

### §1.1 总表

**下面这张表是本篇的主干**，全部由
`of_get_regulation_constraints`（`of_regulator.c:80`）读取，
后面每一节是其中一行的展开。
「写在哪个节点」一列是最容易搞错的地方。

| 属性 | 写在哪个节点 | 落到 `constraints` 的哪个字段 | 行 |
|---|---|---|---|
| `regulator-name` | 提供者（每一路） | `name`；决定 `dmesg` 和 sysfs 里显示的名字 | `:97` |
| `regulator-min-microvolt` | 提供者 | `min_uV` | `:99-100` |
| `regulator-max-microvolt` | 提供者 | `max_uV` | `:102-103` |
| `regulator-microvolt-offset` | 提供者 | `uV_offset` | `:113-114` |
| `regulator-min-microamp` | 提供者 | `min_uA` | `:115-116` |
| `regulator-max-microamp` | 提供者 | `max_uA` | `:117-118` |
| `regulator-input-current-limit-microamp` | 提供者 | `ilim_uA` | `:120-122` |
| `regulator-boot-on` | 提供者 | `boot_on`（bool） | `:128` |
| `regulator-always-on` | 提供者 | `always_on`（bool）见 §1.2 | `:129` |
| `regulator-pull-down` | 提供者 | `pull_down`（bool） | `:133` |
| `regulator-allow-bypass` | 提供者 | 只置 `valid_ops_mask` 的位 | `:135-136` |
| `regulator-allow-set-load` | 提供者 | 只置 `valid_ops_mask` 的位 | `:138-139` |
| `regulator-ramp-delay` | 提供者 | `ramp_delay`；**写 0 = `ramp_disable = true`** | `:141-147` |
| `regulator-settling-time-us` | 提供者 | `settling_time` | `:149-151` |
| `regulator-settling-time-up-us` | 提供者 | `settling_time_up`；和上一条冲突时**被忽略并 warn** | `:153-160` |
| `regulator-settling-time-down-us` | 提供者 | `settling_time_down`；同上 | `:162-170` |
| `regulator-enable-ramp-delay` | 提供者 | `enable_time` | `:172-174` |
| `regulator-soft-start` | 提供者 | `soft_start`（bool） | `:176-177` |
| `regulator-active-discharge` | 提供者 | `active_discharge`（0/非0 → DISABLE/ENABLE） | `:178-183` |
| `regulator-initial-mode` | 提供者 | `initial_mode`；**必须驱动填了 `of_map_mode`** | `:185-196` |
| `regulator-allowed-modes` | 提供者 | `valid_modes_mask` + `CHANGE_MODE`；同上 | `:198-223` |
| `regulator-system-load` | 提供者 | `system_load`（DRMS 用） | `:225-226` |
| `regulator-max-step-microvolt` | 提供者 | `max_uV_step` | `:240-242` |
| `regulator-over-current-protection` | 提供者 | `over_current_protection`（bool） | `:244-245` |
| `regulator-{oc,ov,uv,temp}-*` 12 个 | 提供者 | 四组 `notification_limit`；见 §6 | `:247` |
| `regulator-coupled-with` | 提供者 | phandle 数组；见 §7 | `:93-94`、`:228-238` |
| `regulator-coupled-max-spread` | 提供者 | `max_spread[]` | `:236-237` |
| `regulator-state-{standby,mem,disk}` | 提供者的**子节点** | `state_standby` / `state_mem` / `state_disk`；见 §5 | `:249-320` |
| `<名字>-supply` | **消费者节点**（也可以是提供者节点，表示级联） | 不进 `constraints`；见 §2 | `core.c:478-479` |

这张表**不包含** `regulator-fixed` 专属的
`gpio` / `enable-active-high` / `startup-delay-us` / `off-on-delay-us` /
`vin-supply` / `clocks` / `required-opps` ——
它们由 `fixed.c` 自己读，见 §4。

也不包含 `of_match` 匹配用的 `regulator-compatible`（已废弃），见 §3.2。

### §1.2 `valid_ops_mask`：授权是从 DTS 长出来的

03 篇 §B2 讲过"核心的授权判据只看 `valid_ops_mask`，完全不看 `ops`"。
那 `valid_ops_mask` 从哪来？**全部在这里**：

| 条件 | 置的位 | 行 |
|---|---|---|
| `min_uV != max_uV` | `REGULATOR_CHANGE_VOLTAGE` | `of_regulator.c:106-107` |
| `min_uA != max_uA` | `REGULATOR_CHANGE_CURRENT` | `:125-126` |
| **`always_on == false`** | `REGULATOR_CHANGE_STATUS` | `:130-131` |
| 有 `regulator-allow-bypass` | `REGULATOR_CHANGE_BYPASS` | `:135-136` |
| 有 `regulator-allow-set-load` | `REGULATOR_CHANGE_DRMS` | `:138-139` |
| `regulator-allowed-modes` 解析出至少一个合法 mode | `REGULATOR_CHANGE_MODE` | `:217-219` |

**三条反直觉的推论，每一条都会咬人**：

1. **`CHANGE_STATUS` 是"默认给"的。**
   判据是 `!always_on`（`:130-131`），**和硬件有没有开关能力无关**。
   所以一个只写了 min/max 的 `regulator-fixed`（完全不可控）
   照样能被 `regulator_enable` 调 —— 返回 0，但什么都没发生
   （`ops->enable` 为 NULL ⇒ `core.c:2782` 返回 `-EINVAL`，
   被 `:1685` 容忍，03 篇 §A3.3）。
2. **写了 `regulator-always-on` 就拿不到 `CHANGE_STATUS`。**
   这是对的语义（"这一路不许关"），但很多人以为
   always-on 只是"开机就开"—— 不是，它同时**吊销了关的权限**。
   `regulator_disable` 会返回 `-EPERM`。
3. **`min == max` 拿不到 `CHANGE_VOLTAGE`。**
   `regulator-min-microvolt = <1800000>; regulator-max-microvolt = <1800000>;`
   是"钉死 1.8V"，`regulator_set_voltage` 会 `-EPERM`（03 篇 §B2）。
   想让消费者能调压，**必须写一个真的区间**。

还有一个"顺带"的开关：`min_uV && max_uV`（两者都非 0）
⇒ `apply_uV = true`（`:110-111`）——
意思是"注册时把电压真的写进硬件"（03 篇 §A3）。
`fixed.c:137` 会把它清回 0（固定电压不需要写）。

### §1.3 三方分工：谁消费什么

| 消费方 | 消费什么 | 代码位置 |
|---|---|---|
| **通用 DT / 驱动模型** | `compatible` / `reg` / `status` / `interrupts` / `pinctrl-*` | `drivers/of/`、`drivers/base/` |
| **regulator 核心** | §1.1 那张表里的全部；`<名字>-supply` 的解析 | `of_regulator.c`、`core.c:471` |
| **具体驱动** | `of_map_mode`（mode 数值的含义）、`of_parse_cb`（自定义属性）、`regulator-fixed` 的 gpio/delay 属性 | 各驱动 + `fixed.c` |

**`regulator-initial-mode` 和 `regulator-allowed-modes` 里的数字
不是内核常量** —— 它们是**芯片特有的编号**，
必须由驱动的 `desc->of_map_mode` 翻译
（`of_regulator.c:186-187` / `:210`）。
驱动没填 `of_map_mode` ⇒ 只有一条
`mapping for mode %d not defined` 的 `pr_warn`（`:193-194`），
**属性被完全忽略**。这是本篇最典型的静默失效。

举个真的：`rk8xx_regulator_of_map_mode`
（`drivers/regulator/rk808-regulator.c:532-542`）只认 1 和 2 ——
`1` = `REGULATOR_MODE_FAST`、`2` = `REGULATOR_MODE_NORMAL`，
其余返回 `REGULATOR_MODE_INVALID` ⇒
`invalid mode %u` 的 `pr_err`（`of_regulator.c:189`）。
**所以 rk808 的 DTS 里写 `regulator-initial-mode = <3>;` 会被拒绝**，
而同一个数字在别的 PMIC 上可能完全合法。

## §2 消费者侧：`<名字>-supply`

### §2.1 命名规则

**属性名 = 你在驱动里 `regulator_get` 的第二个参数 + `-supply`**。
拼接就在一行：

```c
/* core.c:478 */ snprintf(prop_name, 64, "%s-supply", supply);
/* core.c:479 */ regnode = of_parse_phandle(dev->of_node, prop_name, 0);
```

所以 `devm_regulator_get(dev, "vdd")` 找的是 `vdd-supply`。
`prop_name` 缓冲区是 64 字节（`:474`）—— supply 名字别超过 57 字符。

值是一个 **phandle**，指向提供者那一路的节点：

```dts
&sdmmc {
	vmmc-supply = <&vcc3v0_sd>;    /* 驱动里 get 的是 "vmmc" */
	vqmmc-supply = <&vcc_sdio>;    /* get 的是 "vqmmc" */
};
```

### §2.2 一个少有人知的回退：会往子节点里找

`of_get_regulator`（`core.c:471`）在**本节点**没找到时，
会调 `of_get_child_regulator`（`:438`）——
它**递归遍历所有子孙节点**（`:444` 的 `for_each_child_of_node`
+ `:448` 的自我递归），找到第一个带这个属性的就返回。

```
of_get_regulator(dev, "vdd")                       [core.c:471]
  ├─ of_parse_phandle(dev->of_node, "vdd-supply")  [:479]  ← 先查自己
  └─ 没有 → of_get_child_regulator(...)            [:482]  递归查子孙
```

**实际后果**：父节点漏写 `vdd-supply`，
但某个子节点（比如一个 port/endpoint 子节点）恰好有同名属性，
父设备会**静默拿到子节点那一路电**。
这在多路 MIPI/USB 的设备树里真的会发生。
定位方法：`dev_dbg` 那两条（`:476` 和 `:486-487`）
在 `CONFIG_DYNAMIC_DEBUG` 下能开出来，见 §8.3。

### §2.3 漏写的后果：不报错

arm64 上 `have_full_constraints()` 恒真（`core.c:112-114` 的
`of_have_populated_dt()`），所以漏写 `xxx-supply` 的结果是
一条 `dev_warn` + 一个**能用的 dummy**（`core.c:2230`，
04 篇 §5、05 篇 §1.3）。

反过来，**写了 phandle 但那一路还没注册**是
`-EPROBE_DEFER`（`core.c:2032-2036`）——
这是正常的，driver core 会重试。
只有 `of_get_regulator` 返回 NULL（属性根本不存在）才落到 dummy。

**所以"probe 卡在 defer"和"拿到 dummy"是两种完全不同的错**：
前者 DTS 写对了只是时序问题，后者 DTS 漏了。**别混着查。**

### §2.4 批量：一次拿走节点上所有 `*-supply`

`of_regulator_bulk_get_all`（`of_regulator.c:755`）会
**遍历节点的所有 property**（`:771`），
用 `is_supply_name`（`:722`）挑出名字形如 `x-supply` 的，
逐个 `regulator_get`（`:781`）。

`is_supply_name` 的判据（`:726-738`）：长度 ≥ 8、
从右往左找第一个 `-`、右半必须逐字等于 `"supply"`。
所以 `vin-supply-b` **不会**被当成 supply（右半是 `supply-b`），
而 `a-b-supply` 会被解析成名字 `a-b`。

实现是"两趟"（注释在 `:766-769`）：第一趟数个数、
第二趟填 —— 中间 `goto restart`（`:800`）。

## §3 提供者侧：核心怎么找到你的节点

### §3.1 匹配流程

`regulator_of_get_init_node`（`of_regulator.c:463`）
是"从 PMIC 的 `dev->of_node` 找到某一路的子节点"的全部逻辑，
只有 48 行，但四个分支都有坑：

```
regulator_of_get_init_node(dev, desc)                    [:463]
  │
  ├─ !dev->of_node || !desc->of_match → return NULL      [:469-470]
  │     驱动没填 of_match ⇒ 这一路拿不到任何 DTS 约束
  │
  ├─ desc->regulators_node 非空？
  │    ├─ 是：search = 该名字的子节点                    [:472-474]
  │    │        找不到 → dev_dbg + NULL                  [:482-485]
  │    └─ 否：search = PMIC 节点自己                     [:476]
  │            如果 PMIC 节点自己的 name 就等于 of_match
  │              → 直接返回它                            [:478-479]
  │
  └─ 遍历 search 的**可用**子节点                        [:488]
       每个子节点的匹配名字按优先级取：
         1. "regulator-compatible" 属性（已废弃）      [:489]
         2. of_match_full_name ? child->full_name        [:494]
                              : child->name             [:492]
       逐字 strcmp(desc->of_match, name)                 [:497]
```

**`:488` 用的是 `for_each_available_child_of_node`** ——
子节点写了 `status = "disabled";` 就**不会被匹配**，
结果是这一路没有任何约束（等价于驱动没填 `of_match`）。

### §3.2 四个字段的分工

| `regulator_desc` 字段 | 作用 | 声明 |
|---|---|---|
| `of_match` | **要匹配的子节点名字**。不填 ⇒ 整条 DTS 解析被跳过 | `include/linux/regulator/driver.h:364` |
| `regulators_node` | 装这些子节点的**容器节点名**。不填 ⇒ 直接在 PMIC 节点下找 | `:366` |
| `of_match_full_name` | 用 `full_name`（带 `@unit-address`）而不是 `name` 去比 | 见 `of_regulator.c:491-494` |
| `of_parse_cb` | 驱动自己再读一遍这个节点，读自定义属性 | `:367`，kerneldoc `:268` |

真实例子（`drivers/regulator/rk808-regulator.c:689-690`）：

```c
.of_match = of_match_ptr("DCDC_REG1"),
.regulators_node = of_match_ptr("regulators"),
```

对应的 DTS（`arch/arm64/boot/dts/rockchip/rk3399-rock-pi-4.dtsi:265-266`）：

```dts
regulators {
	vdd_center: DCDC_REG1 {
```

**`DCDC_REG1` 这个名字在两边逐字相同，这不是巧合，是唯一的匹配依据。**
DTS 里写成 `dcdc_reg1`（小写）就静默失效。

`of_parse_cb` 的调用点是 `of_regulator.c:530-544`：
它在约束解析**之后**跑，可以改 `config`；
返回 `-EPROBE_DEFER` 会被原样传出去（`:535-537`），
其他非 0 值变成
`driver callback failed to parse DT for regulator %pOFn` 的
`dev_err`（`:539-541`）+ 整条解析失败。

### §3.3 写错了会怎样

**四种写错，症状各不相同**：

| 写错的地方 | 症状 | 唯一的线索 |
|---|---|---|
| 驱动漏填 `of_match` | 这一路**完全没有约束** —— `set_voltage`/`enable` 全 `-EPERM` | 无任何日志。只能看 `/sys/class/regulator/*/min_microvolts` 是 0 |
| DTS 子节点名和 `of_match` 不一致 | 同上 | 无日志。**这是本子系统最难查的一类错** |
| `regulators_node` 名字对不上 | 同上，但有一条 `dev_dbg` | `Failed to find regulator container node '%s'`（`of_regulator.c:483-484`）—— 需要开 dynamic debug |
| 子节点写了 `status = "disabled"` | 同上 | 无日志（`:488` 直接跳过） |

**注意前四种的最终症状是同一个**：约束全空。
所以定位顺序应该是"先看 sysfs 里约束在不在"，
而不是"先猜哪个名字写错了"。决策树在 §8.2。

另有一种不同的错：`of_get_regulator_init_data` 返回 NULL
（内存不足或约束解析失败）⇒
`failed to parse DT for regulator %pOFn` 的
`dev_err`（`of_regulator.c:526`）——**这个是有日志的**。

## §4 `regulator-fixed`：arm64 DTS 里最常见的那个节点

机制见 04 篇 §6。本节只讲 DTS 怎么写。

### §4.1 三种形态

**同一个驱动，靠 `compatible` 分出三种开关方式**
（`fixed.c:314-329`）：

```dts
/* 形态 1：完全不可控的固定电压（最常见） */
vcc12v_dcin: dc-12v {
	compatible = "regulator-fixed";
	regulator-name = "vcc12v_dcin";
	regulator-always-on;
	regulator-boot-on;
	regulator-min-microvolt = <12000000>;
	regulator-max-microvolt = <12000000>;
};

/* 形态 2：GPIO 开关 */
vcc3v3_pcie: vcc3v3-pcie-regulator {
	compatible = "regulator-fixed";
	enable-active-high;                              /* 见 §4.2 */
	gpio = <&gpio2 RK_PD2 GPIO_ACTIVE_HIGH>;
	pinctrl-names = "default";
	pinctrl-0 = <&pcie_pwr_en>;
	regulator-name = "vcc3v3_pcie";
	regulator-always-on;
	regulator-boot-on;
	vin-supply = <&vcc5v0_sys>;                      /* 级联到上游 */
};
```

以上两段逐字取自
`arch/arm64/boot/dts/rockchip/rk3399-rock-pi-4.dtsi:88-95`
和 `:107-117`。

```dts
/* 形态 3：时钟开关 */
some_reg: some-regulator {
	compatible = "regulator-fixed-clock";
	clocks = <&cru SCLK_SOMETHING>;
	regulator-name = "vcc_x";
	regulator-min-microvolt = <1800000>;
	regulator-max-microvolt = <1800000>;
};

/* 形态 4：power domain 的 performance state */
another_reg: another-regulator {
	compatible = "regulator-fixed-domain";
	power-domains = <&pd_something>;
	required-opps = <&opp_high>;
	regulator-name = "vcc_y";
};
```

形态 3 拿不到 clock ⇒
`Can't get enable-clock from devicetree`（`fixed.c:217`）+ probe 失败。
形态 4 拿不到 performance state ⇒
`Can't get performance state from devicetree`（`:225`）+ probe 失败。
**这两条和形态 1/2 不同：它们是硬失败，有日志。**

### §4.2 `enable-active-high`：极性不由 phandle flag 决定

这是本篇最反直觉的一条。`gpio = <&gpio2 RK_PD2 GPIO_ACTIVE_HIGH>;`
里那个 `GPIO_ACTIVE_HIGH` **不起作用** ——
极性完全由**有没有写 `enable-active-high` 这个 bool 属性**决定。

判据在 gpiolib 里，不在 regulator 里
（`drivers/gpio/gpiolib-of.c:142-163`）：

```c
/* gpiolib-of.c:148 */ bool active_low = !of_property_read_bool(np,
/* gpiolib-of.c:149 */                          "enable-active-high");
/* gpiolib-of.c:156 */ if ((*flags & OF_GPIO_ACTIVE_LOW) && !active_low) {
/* gpiolib-of.c:157 */         pr_warn("%s GPIO handle specifies active low - ignored\n",
/* gpiolib-of.c:161 */ if (active_low)
/* gpiolib-of.c:162 */         *flags |= OF_GPIO_ACTIVE_LOW;
```

三条推论：

1. **默认是 active-low**（`:148` 那个取反 + `:140` 的注释
   "Note that active low is the default"）。
   想要高有效**必须显式写 `enable-active-high`**。
2. **phandle 里写 `GPIO_ACTIVE_LOW` 又写了 `enable-active-high`**
   ⇒ phandle flag 被**主动丢弃** + 一条
   `GPIO handle specifies active low - ignored` 的 `pr_warn`（`:157`）。
   注释 `:150-155` 说得很直白：这两者的关系是"属性说了算"。
3. 这个特殊处理只对三种 `compatible` 生效（`:143-147`）：
   `regulator-fixed`、`reg-fixed-voltage`、
   以及 `regulator-gpio` 的 `enable-gpio(s)`。
   **别的驱动的 GPIO 不吃这一套。**

`gpio-open-drain`（`:169`）**只对老的
`reg-fixed-voltage` compatible 生效**（`:168` 的判据）——
写在 `regulator-fixed` 节点上是**死属性**。
命中时会打一条 `uses legacy open drain flag - update the DTS if you can`
的 `pr_info`（`:171-172`）。

### §4.3 `fixed.c` 自己读的四个属性

| 属性 | 落到哪 | 行 |
|---|---|---|
| `startup-delay-us` | `desc.enable_time`（`:232`） | `fixed.c:151` |
| `off-on-delay-us` | `desc.off_on_delay`（`:233`） | `:152` |
| `vin-supply` | 只判"存不存在"，然后把 `desc.supply_name` 设成字符串 `"vin"` | `:154-155` + `:235-241` |
| `gpio`（通过 gpiolib） | `cfg.ena_gpiod` | `:274` |

**`vin-supply` 的处理很特别**（`:154-155`）：
代码只检查属性**存在**，不看值；
真正的 phandle 解析是后面 `regulator_resolve_supply` 干的
（03 篇 §A2），靠的是 `desc.supply_name = "vin"`
再拼成 `"vin-supply"`（`core.c:478`）绕回来。
所以**属性名必须逐字是 `vin-supply`**，不能是别的名字。

`enabled_at_boot`（来自 `regulator-boot-on`，`:148-149`）
决定初始 GPIO 输出方向（`:252-255`）——
`GPIOD_OUT_HIGH` 还是 `GPIOD_OUT_LOW`。

**`:268` 无条件加上 `GPIOD_FLAGS_BIT_NONEXCLUSIVE`**，
注释 `:257-267` 解释了原因：**有些板子两路 fixed regulator
共用一根 enable 线**，所以必须允许两个消费者拿同一个 gpiod。
代价写在同一段注释里：**只有第一个调用者的 flag 生效**，
第二个的极性/open-drain 设置被静默忽略。
这里就是 `fixed.c:266` 那条 `FIXME`（03 篇 §C2）。

`regulator-min-microvolt != regulator-max-microvolt` ⇒
`Fixed regulator specified with variable voltages`（`:143-144`）
+ `-EINVAL`。**固定电压必须写两个相等的值**（或都不写）。

## §5 suspend 状态：三个子节点

### §5.1 写法

**这是本子系统唯一用"子节点"表达的属性组**
（`of_regulator.c:249-320`）：

```dts
vdd_center: DCDC_REG1 {
	regulator-name = "vdd_center";
	regulator-always-on;
	regulator-boot-on;
	regulator-min-microvolt = <750000>;
	regulator-max-microvolt = <1350000>;
	regulator-ramp-delay = <6001>;
	regulator-state-mem {
		regulator-off-in-suspend;
	};
};
```

逐字取自 `arch/arm64/boot/dts/rockchip/rk3399-rock-pi-4.dtsi:266-276`。

三个子节点名（`of_regulator.c:18-22`）：

| 子节点名 | 对应 `suspend_state_t` | 落到 |
|---|---|---|
| `regulator-state-standby` | `PM_SUSPEND_STANDBY` | `constraints->state_standby`（`:257-258`） |
| `regulator-state-mem` | `PM_SUSPEND_MEM` | `constraints->state_mem`（`:251-252`） |
| `regulator-state-disk` | `PM_SUSPEND_MAX` | `constraints->state_disk`（`:254-255`） |

`PM_SUSPEND_ON` 和 `PM_SUSPEND_TO_IDLE` 被显式跳过
（`:260-263` 的 `continue`）—— **s2idle 下这套配置不生效**。
arm64 上 s2idle 是常见的挂起方式，这一点值得记住。

### §5.2 子节点里的六个属性

| 属性 | 落到 | 行 |
|---|---|---|
| `regulator-mode` | `suspend_state->mode`；同样要驱动的 `of_map_mode` | `:274-287` |
| `regulator-on-in-suspend` | `enabled = ENABLE_IN_SUSPEND` | `:289-291` |
| `regulator-off-in-suspend` | `enabled = DISABLE_IN_SUSPEND` | `:292-294` |
| `regulator-suspend-min-microvolt` | `min_uV` | `:296-298` |
| `regulator-suspend-max-microvolt` | `max_uV` | `:300-302` |
| `regulator-suspend-microvolt` | `uV`；**不写时默认取 `min_uV`** | `:304-308` |
| `regulator-changeable-in-suspend` | `changeable = true` | `:310-312` |

**两个 bool 是 if/else if（`:289-294`）**：
同时写 `on-in-suspend` 和 `off-in-suspend` ⇒ **`on` 赢**，
另一个被静默忽略。**都不写 ⇒ `enabled` 保持 0，
也就是 `DO_NOTHING_IN_SUSPEND`** —— 03 篇 §B5.2 讲过它的后果：
`regulator_suspend` 对这一路**什么都不做**。

`:307-308` 那个 else 很容易漏：只写了
`regulator-suspend-min-microvolt` 而没写
`regulator-suspend-microvolt`，`uV` 会**等于 min**。
所以"挂起时降到某个区间"的写法实际是"降到区间下沿"。

`:314-315`：**只有 `regulator-state-mem` 存在时**
才会设 `constraints->initial_state = PM_SUSPEND_MEM`。
standby / disk 不会设。

## §6 保护限值：12 个属性，grep 找不到

### §6.1 属性名是 snprintf 拼出来的

这是本篇最需要提醒的一条**取证陷阱**：

```c
/* of_regulator.c:41 */         "regulator-oc-%s-microamp",
/* of_regulator.c:42 */         "regulator-ov-%s-microvolt",
/* of_regulator.c:43 */         "regulator-temp-%s-kelvin",
/* of_regulator.c:44 */         "regulator-uv-%s-microvolt",
/* of_regulator.c:59 */                 "protection", "error", "warn"
/* of_regulator.c:67 */                 snprintf(prop, 255, props[i], lvl[j]);
/* of_regulator.c:68 */                 found = !of_property_read_u32(np, prop, &pval);
```

**4 个模板 × 3 个等级 = 12 个属性名，
但这 12 个字符串在整棵树里一次都没有以字面形式出现。**
验证：

```bash
grep -rn '"regulator-oc-protection-microamp"' drivers/ include/   # → 0 行
```

**所以"grep 不到消费者"在这一组属性上不等于"死字"** ——
这正好是索引工具和 grep 的共同盲区。
本篇其他所有属性都能 grep 到字面量，只有这 12 个不能。

### §6.2 12 个属性的组织

| 属性模板 | 落到 `constraints` | 三个等级 |
|---|---|---|
| `regulator-oc-<lvl>-microamp` | `over_curr_limits`（`:47`） | `protection` / `error` / `warn` |
| `regulator-ov-<lvl>-microvolt` | `over_voltage_limits`（`:48`） | 同上 |
| `regulator-temp-<lvl>-kelvin` | `temp_limits`（`:49`） | 同上 |
| `regulator-uv-<lvl>-microvolt` | `under_voltage_limits`（`:50`） | 同上 |

三个等级分别落到 `notification_limit` 的
`prot` / `err` / `warn` 三个字段（`:62-64`）。

**只要一组里写了任意一个属性，对应的 detection 标志就置位**
（`:71` 的 `set[i] |=`，`:74-77`）：
`over_current_detection` / `over_voltage_detection` /
`over_temp_detection` / `under_voltage_detection`。

`fill_limit`（`:24-33`）有一个特殊约定：

| 属性值 | 落到 `*limit` |
|---|---|
| `0`（或属性不存在） | `REGULATOR_NOTIF_LIMIT_DISABLE` |
| **`1`** | `REGULATOR_NOTIF_LIMIT_ENABLE` —— "**开启但用硬件默认阈值**" |
| 其他 | 就是这个数值 |

**`= <1>` 不是"1 微安"**，是"启用这个保护，阈值我不指定"。
这是一个很容易写错的魔数。

另有一个独立的 bool：`regulator-over-current-protection`
（`:244-245`）→ `constraints->over_current_protection`，
和上面那组 `oc-*` 是**两回事**。

## §7 耦合：`regulator-coupled-with`

### §7.1 arm64 上零使用者

```bash
grep -rn 'regulator-coupled-with' arch/arm64/boot/dts/    # → 0 行
```

全树只有 `arch/arm/boot/dts/tegra*` 在用（tegra20/tegra30 那一批）。
**所以 arm64 读者可以整节跳过** ——
写这一节只是为了在你 review 别人的 DTS 时能认出来。

### §7.2 两个属性必须成对

```dts
vdd_core: regulator@1 {
	regulator-coupled-with = <&vdd_cpu>;
	regulator-coupled-max-spread = <300000>;
};
vdd_cpu: regulator@2 {
	regulator-coupled-with = <&vdd_core>;      /* 必须双向 */
	regulator-coupled-max-spread = <300000>;   /* 数值必须相同 */
};
```

`of_check_coupling_data`（`of_regulator.c:627`）逐条校验，
kerneldoc `:619-623` 列了四条：

| 校验 | 失败时的 `dev_err` | 行 |
|---|---|---|
| `max_spread > 0` | `max_spread value invalid` | `:641-643` |
| phandle 个数两边相同 | `number of coupled reg phandles mismatch` | `:656-659` |
| 对方的 `regulator-coupled-with` 里也有我 | `missing 2-way linking for coupled regulators` | `:662-665` |
| 两边的 `max-spread` 数值相同 | `coupled regulators max_spread mismatch` | `:674-678` |

第 4 条的取法是**按索引对齐**：
`of_coupling_find_node`（`:581`）先找出"我在对方数组里的下标 index"
（`:606`），再用这个 index 去读对方的
`regulator-coupled-max-spread`（`:668-669`）。
**所以两个数组的顺序必须互相对应**，不能只保证集合相同。

校验失败的后果在 `core.c:5459` —— `regulator_init_coupling`
里直接返回 `-EPERM`（03 篇 §B4.2）。
上面四条**每一条都有 `dev_err`**，这是本篇里
"配错了一定有日志"的少数几处之一。

### §7.3 时序

`of_parse_coupled_regulator`（`:699`）用
`of_find_regulator_by_node`（`:556`）把 phandle 变成 rdev ——
**对方还没注册时返回 NULL**（kerneldoc `:696-697`），
不是错误。核心靠 `regulator_resolve_coupling`
（`core.c:5350`，在 `:5729` 每注册一个 rdev 就重跑一次）
来补齐（03 篇 §B4.3）。

---

## §8 定位：DTS 写了但好像没生效

三层，**按代价从低到高**。绝大多数问题在 §8.2 就能定死。

### §8.1 编译期

| 手段 | 做法 | 能发现什么 |
|---|---|---|
| DTS 语法/绑定校验 | `make dtbs_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/regulator/` | 属性名拼错、类型写错、缺必填项 |
| 打开整目录 dbg | `CONFIG_REGULATOR_DEBUG=y` | 见下 |
| 驱动有没有 `of_map_mode` | `rg -n 'of_map_mode' drivers/regulator/<你的 PMIC>.c` | 决定 `regulator-allowed-modes` 是死字还是活的（§1.3） |

**`CONFIG_REGULATOR_DEBUG`（`Kconfig:28-31`）不是加了几条打印，
而是给整个目录加 `-DDEBUG`**：

```
Makefile:186:  ccflags-$(CONFIG_REGULATOR_DEBUG) += -DDEBUG
```

⇒ `drivers/regulator/` 下**所有** `dev_dbg` / `rdev_dbg` 一次全开，
不需要动 dynamic debug。调 DTS 时这是性价比最高的一个开关。

代价：`core.c` 里 `rdev_dbg` 很多，一颗多路 PMIC 开机会刷出上百行。
只想看某个文件用 §8.3 的 dyndbg。

顺带一条：这个宏还被 `dbx500-prcmu.c:54` 用来条件编译一套 debugfs
（`dbx500-prcmu.h:34`）—— 那是 ux500 平台的东西，**arm64 上碰不到**。

### §8.2 运行期静态：从 sysfs 反查约束到底挂上没有

**这是 §3.3 承诺的决策树。** 入口是
`/sys/class/regulator/regulator.N/`（`core.c:5860` 挂的
`regulator_dev_groups`）。

```
第 0 步：先找到你那一路
  grep -H . /sys/class/regulator/*/name | grep <你的路名>
    │
    ├─ 找不到 ────────────► 这一路根本没注册。
    │                        不是 DTS 问题，是驱动没 probe 成功。
    │                        去看 §9 里 fixed.c / 你的 PMIC 的 dev_err
    │
    └─ 找到了 regulator.N
         │
第 1 步： cat /sys/class/regulator/regulator.N/name
         │
         ├─ 输出是 DTS 子节点名（如 "vdd_center"）
         │        ⇒ of_match 匹配成功，约束大概率挂上了 → 第 2 步
         │
         └─ 输出是驱动 desc 里的 .name（如 "DCDC_REG1"）或 "regulator-N"
                  ⇒ 停。没有匹配到 DTS 节点。
                     查 §3.3 的四种写错法
         │
第 2 步： cat /sys/class/regulator/regulator.N/{min,max}_microvolts
         │
         ├─ 两个都是 0 ⇒ 约束是空的（见下面的"为什么是 0"）
         │
         ├─ 值 ≠ 你 DTS 写的 ⇒ 挂到了别的节点上，或者被 §1.2 的规则改过
         │
         └─ 值 == 你 DTS 写的 ⇒ 约束没问题，问题在消费者侧 → §8.2 第 4 步
         │
第 3 步： ls /sys/class/regulator/regulator.N/  （看有哪些属性）
         │  属性可见性由 ops 决定，不由 DTS 决定 —— 见下
         │
第 4 步： ls -l /sys/class/regulator/regulator.N/of_node
         │  ⇒ 直接告诉你核心认为这一路对应哪个 DT 节点。
         │     和你以为的不一致 ⇒ 就是 §2.2 那个隔代搜索
```

**为什么"约束空了"是 `0` 而不是 `constraint not defined`：**

```
core.c:5635-5641
	if (init_data)
		rdev->constraints = kmemdup(&init_data->constraints, ...);
	else
		rdev->constraints = kzalloc(sizeof(*rdev->constraints), ...);
```

`init_data` 为 NULL（= DTS 没匹配上）时核心**照样 `kzalloc` 一份全 0 的
约束**，不是留 NULL。而 `device_add` 在 `:5721`，比这里晚 ——
所以对**任何能在 sysfs 里看到的** rdev，`rdev->constraints` 必然非 NULL。

⇒ `min_microvolts_show` 里那句
`return sprintf(buf, "constraint not defined\n")`（`core.c:822`，
`max_` 版在 `:834`）**永远打印不出来**。
别拿它当判据 —— **判据是全 0**。

**第 3 步那条最容易误判**：属性在不在，只看驱动填了哪些 `ops`，
**和 DTS 一个字都没关系**（`regulator_attr_is_visible`，`core.c:5167`）：

| 你看到 | 判据 | 行号 |
|---|---|---|
| `name` / `num_users` / `type` | 恒在 | `core.c:5176-5179` |
| `min_microvolts` / `max_microvolts` | `ops->set_voltage \|\| ops->set_voltage_sel` | `core.c:5218-5220` |
| `microvolts` | 四种取值途径任一可用，**而且当场读一次硬件** | `core.c:5182-5189` |
| `opmode` | `ops->get_mode` | `core.c:5194-5195` |
| `state` | `rdev->ena_pin \|\| ops->is_enabled` | `core.c:5197-5198` |
| `status` | `ops->get_status` | `core.c:5200-5201` |
| `bypass` | `ops->get_bypass` | `core.c:5203-5204` |
| `suspend_*_microvolts` | `ops->set_suspend_voltage` | `core.c:5231-5234` |
| 9 个错误标志属性 | `ops->get_error_flags` | `core.c:5206-5215` |

两条推论：

1. **`min_microvolts` 存在 ≠ DTS 写了约束**，只说明驱动实现了 `set_voltage*`。
   要看约束在不在，**必须读它的内容**（第 2 步）。
2. **`microvolts` 属性的可见性判断会真的调 `ops->get_voltage(rdev)`**
   （`core.c:5183`）—— 一次 `ls` 就是一次 I2C 读。
   PMIC 驱动的 `get_voltage` 里如果有副作用或者会睡很久，这里会被撞到。

`suspend_*_state` 三个属性是**恒在**的（`core.c:5226-5229`），
所以 DTS 里没写 `regulator-state-mem` 也能 `cat` 到 —— 内容是 0。

### §8.3 运行期动态：只看 DT 解析这一段

**regulator 的 DT 解析全部发生在 probe 期，也就是
`/sys/kernel/debug/` 挂载之前**。所以运行期 `echo ... > control`
**看不到你想看的那几行** —— 必须走 cmdline：

```
dyndbg="file drivers/regulator/of_regulator.c +p; file drivers/regulator/core.c +p"
```

（内建时写在 kernel cmdline 里；`CONFIG_DYNAMIC_DEBUG=y` 是前提。）

值得盯的四条 `dev_dbg`：

| 消息 | 位置 | 说明什么 |
|---|---|---|
| `Looking up %s-supply from device tree` | `core.c:476` | 消费者侧开始查 phandle。**没有这行 ⇒ 你的驱动根本没调 `regulator_get`** |
| `Looking up %s property in node %pOF failed` | `core.c:486-487` | phandle 和隔代搜索都失败了 ⇒ 下一步就是 dummy（§2.3） |
| `Failed to find regulator container node '%s'` | `of_regulator.c:483-484` | `regulators_node` 写的名字在 DTS 里不存在（§3.3） |
| `%s supplying %duV` | `fixed.c:295` | 一路 `regulator-fixed` 注册成功，带实际电压 |

注意第三条是 `dev_dbg` 而不是 `dev_warn` ——
**容器节点名写错，默认日志级别下一声不响**。
这是 §3.3 那四种写错法都表现成同一个症状的根本原因。

### §8.4 症状清单（按坏掉的范围分组）

**整颗 PMIC 都不对**

| 症状 | 先查 |
|---|---|
| `/sys/class/regulator/` 里根本没有你的路 | PMIC 驱动没 probe（i2c 地址、中断、regmap），不是 DTS regulator 的问题 |
| 所有路的 `name` 都是 `DCDC_REG1` 这类 desc 名 | `regulators_node` 或容器节点名错 → §8.3 第三条消息 |
| 所有路的 `min/max_microvolts` 都是 0 | 同上 |

**单独一路不对**

| 症状 | 先查 |
|---|---|
| 只有这一路 `name` 是 desc 名 | 这一路的 `of_match` 和子节点名不一致（§3.2） |
| 这一路的子节点写了 `status = "disabled"` | 会被 `for_each_available_child_of_node` 直接跳过（§3.1） |
| 消费者拿到 dummy，但 DTS 里明明写了 supply | 隔代搜索匹配到了同名的**别的**属性（§2.2），看 `of_node` 软链 |

**某一条属性不生效**

| 症状 | 先查 |
|---|---|
| `regulator-allowed-modes` 无效 | 驱动没填 `of_map_mode`，静默（§1.3） |
| `regulator-min-microvolt` 写了但 `set_voltage` 返回 `-EPERM` | min == max ⇒ `CHANGE_VOLTAGE` 没授权（§1.2） |
| `regulator-always-on` 写了但还是被关 | 反过来了：写了它就**拿不到** `CHANGE_STATUS`；被关是别的原因（03 篇 §A7） |
| `regulator-over-voltage-protection-microvolt` 这类保护属性 grep 不到消费者 | 正常，是 snprintf 拼出来的（§6.1） |
| `enable-active-high` 好像反了 | 它不是 regulator 的属性，由 gpiolib 处理（§4.2） |

**拓扑不对**

| 症状 | 先查 |
|---|---|
| 一直 `-EPROBE_DEFER` | `vin-supply` / `vccX-supply` 指向的上游还没注册（03 篇 §A2） |
| `regulator-coupled-with` 报错 | §7.2 的四条对称性检查 |

---

## §9 错误消息 → 原因

全部来自本树，`%pOFn` 打印的是 DT 节点名。

**消费者侧（`core.c`）**

| 消息 | 位置 | 级别 | 原因 |
|---|---|---|---|
| `supply %s not found, using dummy regulator` | `core.c:2230` | warn | DTS 漏了 `<name>-supply`，或 phandle 指向的节点不是 regulator。**probe 会成功**（§2.3） |
| `Failed to resolve %s-supply for %s` | `core.c:2100-2101` | err | 提供者侧的 `vin-supply` / `<x>-supply` 解析失败，返回 `-EPROBE_DEFER`。反复出现说明上游永远不会来 |

**提供者侧（`of_regulator.c`）**

| 消息 | 位置 | 级别 | 原因 |
|---|---|---|---|
| `Failed to find regulator container node '%s'` | `:483-484` | dbg | `regulators_node` 名字在 DTS 里不存在。默认日志级别看不到（§8.3） |
| `failed to parse DT for regulator %pOFn` | `:434-436` | err | `of_regulator_match` 路径（老式平台数据用法）解析失败 |
| `driver callback failed to parse DT for regulator %pOFn` | `:539-541` | err | 驱动自己的 `of_parse_cb` 返回了错误 |
| `%pOFn: invalid mode %u` | `:189` / `:279` | err | `regulator-initial-mode` / suspend 子节点的 `regulator-mode` 值 `of_map_mode` 映射不出来 |
| `%pOFn: mapping for mode %d not defined` | `:193-194` / `:284-285` | warn | 驱动**没填** `of_map_mode`（§1.3） |
| `%pOFn: mode mapping not defined` | `:221` | warn | 同上，`regulator-allowed-modes` 分支 |
| `%pOFn: invalid regulator-allowed-modes element %u` | `:212-213` | err | 数组里某个元素映射不出来 |
| `%pOFn: couldn't read allowed modes index %d, ret=%d` | `:206-207` | err | 数组长度和实际元素数不符 |
| `%pOFn: ambiguous configuration for settling time, ignoring 'regulator-settling-time-up-us'` | `:157-158` | warn | 同时写了 `regulator-settling-time-us` 和 `-up-us`，后者被丢弃 |
| （同上，`down` 版） | `:167-168` | warn | 同上 |

**耦合（`of_regulator.c`，四条都在 `of_check_coupling_data`）**

| 消息 | 位置 | 原因 |
|---|---|---|
| `max_spread value invalid` | `:642` | `regulator-coupled-max-spread` 的元素数和 `regulator-coupled-with` 不匹配 |
| `number of coupled reg phandles mismatch` | `:657` | 对方节点的 `regulator-coupled-with` 元素数和本节点不同 |
| `missing 2-way linking for coupled regulators` | `:663` | 只写了单向：A 指了 B，B 没指回 A |
| `coupled regulators max_spread mismatch` | `:675-676` | 两边的 `max_spread` 值不一样 |

**`regulator-fixed`（`fixed.c` / `gpiolib-of.c`）**

| 消息 | 位置 | 原因 |
|---|---|---|
| `Fixed regulator specified with variable voltages` | `fixed.c:143-144` | `regulator-min-microvolt` != `regulator-max-microvolt`。返回 `-EINVAL`，这一路直接不注册 |
| `Can't get enable-clock from devicetree` | `fixed.c:217` | `compatible = "regulator-fixed-clock"` 但没写 `clocks` |
| `Can't get performance state from devicetree` | `fixed.c:225` | `compatible = "regulator-fixed-domain"` 但 `required-opps` / `power-domains` 不对 |
| `can't get GPIO` | `fixed.c:276-277` | `gpio` 属性指向的控制器还没注册（常见 `-EPROBE_DEFER`），或被别人独占 |
| `GPIO handle specifies active low - ignored` | `gpiolib-of.c:157` | 在 `regulator-fixed` 上用 `GPIO_ACTIVE_LOW` 表达极性 —— 被无视，用 `enable-active-high`（§4.2） |
| `uses legacy open drain flag - update the DTS if you can` | `gpiolib-of.c:171-172` | 用了 `gpio-open-drain` 而不是 `GPIO_OPEN_DRAIN` 标志 |

提供者侧那一批 `pr_warn` / `pr_err` 用的是 **`pr_`** 而不是 `dev_` ——
所以**前缀里没有设备名**，只有 `%pOFn` 的节点名。
一块板上多颗 PMIC 有同名子节点（都叫 `DCDC_REG1`）时，
这些消息**分不出是哪一颗**。

---

## §10 一个完整的真实例子

取自本树 `arch/arm64/boot/dts/rockchip/rk3399-rock-pi-4.dtsi`
（RK3399 + RK808 PMIC），把三方都串起来。

**① 提供者：PMIC 节点**

```
rk808: pmic@1b {                              [:240]
	compatible = "rockchip,rk808";
	...
	vcc1-supply = <&vcc3v3_sys>;              [:252]  ← 输入侧
	vcc2-supply = <&vcc3v3_sys>;              [:253]
	...
	vcc12-supply = <&vcc_1v8>;                [:263]

	regulators {                              [:265]  容器节点
		vdd_center: DCDC_REG1 {           [:266]  子节点名
			regulator-name = "vdd_center";
			regulator-always-on;
			regulator-boot-on;
			regulator-min-microvolt = <750000>;
			regulator-max-microvolt = <1350000>;
			regulator-ramp-delay = <6001>;
			regulator-state-mem {     [suspend 子节点]
				regulator-off-in-suspend;
			};
		};                                [:276]
		vdd_cpu_l: DCDC_REG2 { ... };     [:278-288]
		vcc_ddr:   DCDC_REG3 { ... };     [:290-297]
		vcc_1v8:   DCDC_REG4 { ... };     [:299-305]
	};
};
```

**② 驱动侧：怎么和上面对上**

```
drivers/regulator/rk808-regulator.c
	RK817_BOOST_DESC(...)                     [:69-80]  宏，展开成 regulator_desc
		.name            = (_match)       [:72]
		.supply_name     = (_supply)      [:73]   ← 对应 vccN-supply
		.of_match        = (_match)       [:74]   ← 对应子节点名 DCDC_REG1
		.regulators_node = "regulators"   [:75]   ← 对应容器节点名
	rk8xx_regulator_of_map_mode             [:532-542] ← 有它，allowed-modes 才活
	逐路 desc                                [:689-690]、[:705-706]、[:721-722]、[:751-752]
```

三个匹配点，缺一路就静默丢约束：

| DTS | 驱动 | 不一致的后果 |
|---|---|---|
| `regulators { }` 节点名 | `.regulators_node = "regulators"` | 整颗 PMIC 的约束全丢，只有一条 dbg（§8.3） |
| 子节点名 `DCDC_REG1` | `.of_match = "DCDC_REG1"` | 这一路的约束丢 |
| `vcc1-supply` 的属性前缀 | `.supply_name = "vcc1"` | 上游解析不到 ⇒ `-EPROBE_DEFER` 或走 dummy |

注意 `regulator-name = "vdd_center"` **不参与匹配** ——
它只影响 `rdev_get_name` 的显示（`core.c:101-109`）和 sysfs 的 `name`。
匹配用的是**节点名** `DCDC_REG1`，不是 label `vdd_center`，也不是
`regulator-name`。三个名字都不一样，这是本子系统最容易搞混的一处。

**③ 板级开关：一路 `regulator-fixed`**

```
vcc3v3_sys: vcc3v3-sys {                      [:119-125]
	compatible = "regulator-fixed";
	regulator-name = "vcc3v3_sys";
	regulator-always-on;
	regulator-boot-on;
	regulator-min-microvolt = <3300000>;      必须和 max 相等（§9）
	regulator-max-microvolt = <3300000>;
	vin-supply = <&vcc5v0_sys>;               上游
};
```

这一路**没有** `gpio`，所以它是纯"名义上的一路电" ——
`fixed.c` 注册它只是为了让下游有个 phandle 可以指、
让 `vin-supply` 链条完整（§4.1 的第一种形态）。

**④ 消费者：怎么用**

上面 `vcc3v3_sys` 被 `rk808` 的 `vcc1-supply`…`vcc4-supply` 引用
（`:252-255`），也被别的设备节点以 `<name>-supply` 引用。
消费者侧代码就是 `devm_regulator_get(dev, "<name>")` ——
`<name>` 拼上 `-supply` 去 DTS 里找（`core.c:478-479`），
细节在 §2.1。

**一条自检**：想确认某一路到底连到哪，不要读 DTS 反推，
直接看 `/sys/class/regulator/regulator.N/of_node` 软链
（§8.2 第 4 步）—— 那是核心的**实际**结论。

---

## §11 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 本篇提到的每个 API 的完整签名 | `01-api.md` |
| `regulator_desc` / `regulation_constraints` 的字段全貌 | `02-datastructures.md` §4 |
| `set_machine_constraints` 三次夹紧、约束怎么变成实际电压 | `03-internals.md` §A3 / §B3 |
| `fixed.c` / `of_regulator.c` 作为"扩展"的完整职责 | `04-extensions.md` §6 / §0 |
| 消费者和提供者的代码骨架 | `05-usage.md` §1 / §2 |
| sysfs 全部属性、tracepoint、dmesg 分诊的完整版 | `07-debug.md` |
| 「为什么三个名字都不一样」这类直觉纠偏 | `08-feynman.md` |
| 本篇每条论断的出处 | `99-evidence.md` |
