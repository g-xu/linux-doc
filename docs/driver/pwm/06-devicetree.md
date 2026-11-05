---
title: 设备树
---

# pwm 06：设备树

## §0 本篇范围

pwm 的设备树面很小——**provider 侧只有一个必需属性（`#pwm-cells`），
consumer 侧只有两个属性（`pwms` / `pwm-names`）**。
但这三个属性背后有一条完整的解析链、三种 cell 语义、
两个几乎同名的极性宏，以及一个"绑定文件名和 compatible 反着写"的坑。

### §0.1 arm64 上的实际数据面

全部用 `arch/arm64/boot/dts/` 复算（命令见 §8）：

| 项 | 数量 |
|---|---|
| 出现 `pwms =` 的文件 | **112** |
| 出现 `#pwm-cells` 的文件 | **71** |
| 出现 `pwm-names` 的文件 | **仅 4** |
| `#pwm-cells = <3>` | **132** 处 |
| `#pwm-cells = <2>` | **119** 处 |
| `#pwm-cells = <1>` | **8** 处（全是 `google,cros-ec-pwm`） |
| 用符号名 `PWM_POLARITY_INVERTED` 的文件 | **5** |
| 第三 cell 直接写裸 `1` 的地方 | **16** 处 |

消费者 compatible 的分布（`pwms =` 前 4 行内的 compatible）：

| compatible | 处数 | 对应驱动 |
|---|---|---|
| `pwm-backlight` | **36** | `drivers/video/backlight/pwm_bl.c` |
| `pwm-regulator` | **23** | `drivers/regulator/pwm-regulator.c` |
| `pwm-clock` | **22** | `drivers/clk/clk-pwm.c` |
| `pwm-fan` | **9** | `drivers/hwmon/pwm-fan.c` |
| `pwm-leds` | 4 | `drivers/leds/leds-pwm.c` |
| `pwm-beeper` | 3 | `drivers/input/misc/pwm-beeper.c` |
| `pwm-vibrator` | 2 | `drivers/input/misc/pwm-vibra.c` |
| `pwm-ir-tx` | 2 | `drivers/media/rc/pwm-ir-tx.c` |
| `maxim,max77843-haptic` | 1 | `drivers/input/misc/max77693-haptic.c` |

⇒ **四种消费者吃掉了 90 处中的 90%**：背光、调压、时钟源、风扇。
这四个也正是 05 §2 骨架和 04 §3 代表驱动挑的那几个。

### §0.2 官方文档只有两篇

| 文件 | 行数 | 管什么 |
|---|---|---|
| `Documentation/devicetree/bindings/pwm/pwm.yaml` | 34 | **provider 侧**：节点名模式 + `#pwm-cells` 必需 |
| `Documentation/devicetree/bindings/pwm/pwm.txt` | 60 | **consumer 侧**：`pwms` / `pwm-names` 语法（未 yaml 化） |

⚠️ **consumer 侧那篇还是 `.txt`**，没有 schema，
所以 `make dtbs_check` **不会校验你的 `pwms` 写法**——
写错 cell 数、写错极性常量，只有运行时报错。

---

## §1 provider 侧：控制器节点

### §1.1 唯一必需的属性

```dts
pwm0: pwm@ff420000 {
	compatible = "rockchip,rk3399-pwm", "rockchip,rk3288-pwm";
	reg = <0x0 0xff420000 0x0 0x10>;
	#pwm-cells = <3>;			/* ← 唯一被通用绑定强制要求的 */
	pinctrl-names = "default";
	pinctrl-0 = <&pwm0_pin>;
	clocks = <&pmucru PCLK_RKPWM_PMU>;
	status = "disabled";
};
```
（真实节点：`arch/arm64/boot/dts/rockchip/rk3399.dtsi:1269-1277`）

通用绑定只规定两件事：

- **节点名要匹配 `^pwm(@.*|-[0-9a-f])*$`**
  （`Documentation/devicetree/bindings/pwm/pwm.yaml:16`）
  ⇒ 节点名必须叫 `pwm`、`pwm@xxxx` 或 `pwm-0`…`pwm-f`；
  叫 `pwmchip@…` 或 `my-pwm@…` 过不了 schema。
- **`#pwm-cells` 必需**（`:18-20` 描述 + `:22-23` required）。

⚠️ **`pinctrl-0` 几乎总是要写。** PWM 输出脚通常和 GPIO 复用，
不复用到 PWM 功能就没有波形——而这一层 pwm 框架完全不管
（`pwm_apply_state()` 只写控制器寄存器）。
现象是"apply 成功、`pwm_get_state()` 也对、示波器无输出"。

### §1.2 三种 `#pwm-cells` 语义

DT 里的 cell 数由 **provider 节点的 `#pwm-cells`** 决定，
解析发生在 `of_parse_phandle_with_args(np, "pwms", "#pwm-cells", index, &args)`
（`core.c:753-754`）——**它读的是 provider 的属性，不是 consumer 的**。

| cells | 语义 | 谁在用 |
|---|---|---|
| **2** | `<通道号 周期ns>` | 默认；`pwm.txt:43-44` 的"典型编码" |
| **3** | `<通道号 周期ns 标志>` | arm64 最常见（132 处） |
| **1** | `<通道号>`（周期由驱动/EC 自己定） | `google,cros-ec-pwm`（8 处）· `pwm-pxa` · `pwm-clps711x` · `ti-sn65dsi86` |

⚠️ **`#pwm-cells = <1>` 只有在 provider 驱动装了非默认 `of_xlate` 时才能用**，
原因见下一节。arm64 上这 8 处全是 cros-ec，不是巧合。

### §1.3 默认值是怎么填上的：`of_pwmchip_add()`

`of_pwmchip_add()`（`core.c:181-198`，由 `pwmchip_add()` 调用，03 §A1.3）
逻辑只有五行，但每一行都是坑：

```
:183-184   没有 chip->dev 或 dev->of_node → 直接 return（DT 面完全不建立）
:186       if (!chip->of_xlate) {              ← ⚠️ 只有驱动没设 of_xlate 时才进
:189-191       读 provider 节点的 "#pwm-cells"；读不到就用 2
:193           chip->of_xlate = of_pwm_xlate_with_flags
:194           chip->of_pwm_n_cells = pwm_cells   ← 来自 DT，不是来自驱动
           }
:197       of_node_get(chip->dev->of_node)     ← 拿一份 of_node 引用
```

三条推论：

1. **`of_pwm_n_cells` 的默认值来自 DT，不是驱动。**
   驱动什么都不设时，`chip->of_pwm_n_cells` 等于 DT 里的 `#pwm-cells`
   ⇒ 二者天然一致，**这条路径不可能不匹配**。
2. ⚠️ **驱动一旦设了 `of_xlate`，`of_pwm_n_cells` 就必须自己设。**
   `:186` 的 `if` 把两个赋值绑在一起了 ⇒ 只设 `of_xlate` 忘了 cells 数，
   字段保持 `devm_kzalloc` 的 **0**，而两个通用 xlate 开头都是
   `if (cells < N) return -EINVAL`（`core.c:129` / `:160`）
   ⇒ **每次 `pwm_get()` 都返回 `-EINVAL`，且没有任何日志**。
   树内 4 个自定义 xlate 的驱动全都成对设了（§1.4），照抄它们。
3. **`#pwm-cells = <1>` 配默认 xlate 必挂。**
   默认是 `of_pwm_xlate_with_flags`，它第一句就是
   `if (pc->of_pwm_n_cells < 2) return ERR_PTR(-EINVAL)`（`core.c:129-130`）
   ⇒ 想用单 cell 必须换 xlate。

### §1.4 两个通用 xlate 与四个自定义者

**`of_pwm_xlate_with_flags()`**（`core.c:124-152`，导出 `:153`）——默认：

| 行 | 检查/动作 |
|---|---|
| `:129-130` | `of_pwm_n_cells < 2` → `-EINVAL` |
| `:133-134` | `args_count < 2` → `-EINVAL`（**DT 少给 cell** 报在这里） |
| `:136-137` | `args[0] >= npwm` → `-EINVAL`（**通道号越界**） |
| `:139` | `pwm_request_from_chip(pc, args[0], NULL)`（label 传 NULL，后面补） |
| `:143-144` | `pwm->args.period = args[1]`；`polarity = PWM_POLARITY_NORMAL` |
| `:146-148` | 仅当 `of_pwm_n_cells >= 3` **且** `args_count > 2` **且** `args[2] & PWM_POLARITY_INVERTED` 才置 `PWM_POLARITY_INVERSED` |

**`of_pwm_single_xlate()`**（`core.c:155-178`，导出 `:179`）——单 cell 版：

| 行 | 差异 |
|---|---|
| `:160-161` | 只要求 `of_pwm_n_cells >= 1` |
| `:164-165` | `args_count` 必须**恰好是 1 或 2**（不是 `>=`）⇒ 给 3 个 cell 反而报 `-EINVAL` |
| `:167` | **通道号硬编码 0**——不看 `args[0]`，所以它只适合 `npwm == 1` 的芯片 |
| `:171` | `args.period = args[0]`（第一个 cell 是**周期**，不是通道号！） |
| `:174-175` | `args_count == 2` 时用 `args[1]` 解极性 |

⚠️⚠️ **`of_pwm_single_xlate` 的第一个 cell 是周期而不是通道号。**
它和默认 xlate 的 cell 语义**完全错位**。所以 `#pwm-cells = <1>` 有两种互不兼容的含义：

- 配 `of_pwm_single_xlate` ⇒ `<周期ns>`；
- 配 cros-ec 自己的 xlate ⇒ `<通道号>`（`drivers/pwm/pwm-cros-ec.c:221-227`，周期由 EC 固定）。

**看到 `#pwm-cells = <1>` 必须去读 provider 驱动**，不能靠猜。

树内全部自定义 xlate（`grep -rn '\.of_xlate\s*=' drivers/`）：

| 驱动 | of_xlate | cells | 第一个 cell 是 | arm64 |
|---|---|---|---|---|
| `drivers/pwm/pwm-cros-ec.c:305-306` | 自己的 `cros_ec_pwm_xlate` | 1 | 通道号（`:225` 查 `npwm`） | ✅ 8 处 DTS |
| `drivers/pwm/pwm-clps711x.c:106-107` | 自己的 `clps711x_pwm_xlate` | 1 | 通道号 | ❌ arm32 |
| `drivers/pwm/pwm-pxa.c:199-200` | `of_pwm_single_xlate` | 1 | **周期** | ❌ arm32 |
| `drivers/gpu/drm/bridge/ti-sn65dsi86.c:1623-1624` | `of_pwm_single_xlate` | 1 | **周期** | ✅ 桥片 |

⇒ 其余 **60 个控制器驱动全部走默认路径**，cells 由 DT 说了算。

### §1.5 一份完整的 provider 绑定（照抄模板）

`Documentation/devicetree/bindings/pwm/toshiba,pwm-visconti.yaml`（45 行，
对应 05 §1 的样板驱动）：

```yaml
allOf:
  - $ref: pwm.yaml#          # ← 必须 $ref 通用绑定（:12-13）

properties:
  compatible:
    items:
      - const: toshiba,visconti-pwm
  reg:
    maxItems: 1
  '#pwm-cells':
    const: 2                 # ← 固定住 cell 数（:23-24）

required:
  - compatible
  - reg

additionalProperties: false  # ← :30
```

三条约定：**`$ref: pwm.yaml#`**、**`'#pwm-cells'` 用 `const` 钉死**、
**`additionalProperties: false`**。写新绑定照这个来。

---

## §2 consumer 侧：`pwms` 与 `pwm-names`

### §2.1 语法

`Documentation/devicetree/bindings/pwm/pwm.txt:10-14` 的形式定义：

```
	pwm-list      ::= <single-pwm> [pwm-list]
	single-pwm    ::= <pwm-phandle> <pwm-specifier>
	pwm-phandle   : phandle to PWM controller node
	pwm-specifier : array of #pwm-cells specifying the given PWM
```

即 `pwms` 是**可以放多组**的：

```dts
vibrator {
	compatible = "pwm-vibrator";
	pwms = <&pwm1 0 1000000000 0>;
	pwm-names = "enable";
};
```
（`arch/arm64/boot/dts/freescale/imx8mq-librem5.dtsi:279-282`）

### §2.2 `pwm-names` 什么时候是必需的

规则只有一条，但两头都要看：

| 消费者调用 | `pwm-names` | 取哪一组 |
|---|---|---|
| `devm_pwm_get(dev, NULL)` | **可选** | `pwms` 的**第 0 组** |
| `devm_pwm_get(dev, "enable")` | ⚠️ **必需** | `pwm-names` 里 `"enable"` 的下标对应那一组 |

代码依据在 `of_pwm_get()`（`core.c:737-799`）开头：

```
:747-751   if (con_id) {
               index = of_property_match_string(np, "pwm-names", con_id);
               if (index < 0) return ERR_PTR(index);     ← 直接把错误码抛回去
           }
:753-754   of_parse_phandle_with_args(np, "pwms", "#pwm-cells", index, &args)
```

⚠️ **`pwm-names` 属性根本不存在时，`of_property_match_string()` 返回 `-EINVAL`**
（`drivers/of/property.c:472-473`）⇒ 消费者驱动看到的是
`devm_pwm_get()` 失败于 `-EINVAL`，**报错信息里不会提"缺 pwm-names"**。
这是 pwm DT 最常见的一类误判：**`-EINVAL` 既可能是 cell 数不对、
通道号越界，也可能是漏了 `pwm-names`**（排错表见 §7）。

kerneldoc 把这条说清楚了（`core.c:730-732`）：
"this also means that the `pwm-names` property becomes mandatory
for devices that look up the PWM device via the con_id parameter"。

树内传非 NULL `con_id` 的消费者极少——`grep` 全树只有一个：

| 消费者 | con_id | 后果 |
|---|---|---|
| `drivers/input/misc/pwm-vibra.c:145` | `"enable"` | ⚠️ **DT 必须写 `pwm-names = "enable"`** |
| `drivers/input/misc/pwm-vibra.c:166` | `"direction"` | 可选通道（双向马达） |
| `pwm_bl.c:508` · `pwm-fan.c:494` · `pwm-regulator.c:357` · `pwm-beeper.c:134` | `NULL` | 只取第 0 组，`pwm-names` 可省 |

⇒ arm64 上只有 4 个文件写了 `pwm-names`，其中 **2 个是 `pwm-vibrator`
（`imx8mq-librem5.dtsi:282`、`rockchip/rk3566-anbernic-rgxx3.dtsi:309`）——
那两个是真的必需**；另外 2 个（`arch/arm64/boot/dts/exynos/exynos5433-tm2-common.dtsi:928` 的 `"haptic"`、
`nvidia/tegra210-p2371-2180.dts:64` 的 `"lp8557"`）是**冗余写法**，
`pwm.txt:40-41` 自己也说这种情况"是多余的"。

### §2.3 `pwm->label` 的三层 fallback

`con_id` 为 NULL 时，核心还是要给这个通道起个名字（就是 debugfs 和
`/sys/class/pwm/*/…` 里显示的那个 label，07 会用到）。
`of_pwm_get()` 末尾（`core.c:786-793`）：

| 优先级 | 来源 | 行 |
|---|---|---|
| 1 | 调用者传的 `con_id` | `:747`（此时 `:786` 的 `if (!con_id)` 不成立） |
| 2 | `pwm-names` 里第 `index` 个字符串 | `:787-788` |
| 3 | **consumer 节点名**（`np->name`） | `:790` |

⇒ 所以 `pwm.txt:19-20` 说"没写 `pwm-names` 就用 user node 的名字兜底"。
实际效果：debugfs 里看到 `pwm-0 (backlight ...)` 一般来自节点名
`backlight`，而不是任何显式配置。
⚠️ **label 在 20 字符处被截断**（`core.c:1117` 的 `%-20.20s`，03 §B6.2）
⇒ 节点名很长时 debugfs 里认不出来。

### §2.4 完整解析链

`pwm_get()` 的 DT 分支（`core.c:914`）→ `of_pwm_get()`（`:737`）：

```
1. con_id → index          :747-751   of_property_match_string("pwm-names")
2. 解析 phandle + cells    :753-754   of_parse_phandle_with_args("pwms","#pwm-cells")
                           :755-758   失败 → pr_err("can't parse \"pwms\" property")
3. phandle → pwm_chip      :760       fwnode_to_pwmchip()
                           :761-766   找不到 → -EPROBE_DEFER（且这一支不 pr_err）
4. cells → pwm_device      :769       pc->of_xlate(pc, &args)   ← §1.4 的两个/四个实现
5. 建 device link          :773       pwm_device_link_add()
                           :776       失败要 pwm_free()（xlate 已经 request 过了）
6. 补 label                :786-791   §2.3 的三层 fallback
7. 放引用                  :796       of_node_put(args.np)
```

⚠️ **第 3 步找不到 provider 返回 `-EPROBE_DEFER` 而不是 `-ENODEV`**
（`core.c:688`）⇒ provider 驱动没编进内核时，消费者会**无限 defer**
而不是报错，现象是"设备一直不出现、dmesg 干净"。
排查看 `/sys/kernel/debug/devices_deferred`。

⚠️ **只有第 2 步失败才 `pr_err`**（`:756`），
第 4 步 xlate 的所有 `-EINVAL` **一条日志都没有**。
这是 §7 排错表存在的理由。

⚠️ 顺带一个死代码：`:762-763` 的
`if (PTR_ERR(pc) != -EPROBE_DEFER) pr_err("PWM chip not found")`
**永远不会执行**——`fwnode_to_pwmchip()` 唯一的失败返回就是
`-EPROBE_DEFER`（`core.c:688`）。所以 dmesg 里**不可能出现**
"PWM chip not found" 这句话，别去 grep 它。

---

## §3 第三个 cell：极性标志

### §3.1 两个几乎同名的宏，别用错

| 名字 | 头文件 | 值 | 用在哪 |
|---|---|---|---|
| `PWM_POLARITY_INVERTED` | `include/dt-bindings/pwm/pwm.h:13` | `(1 << 0)` | ⚠️ **只能写在 DTS 里** |
| `PWM_POLARITY_INVERSED` | `include/linux/pwm.h:22`（`enum pwm_polarity`） | 枚举值 1 | ⚠️ **只能写在 C 里** |

差一个字母（**INVERT**ed / **INVERS**ed），值也巧合地都等于 1，
所以写错了往往能编过一半：

- DTS 里写 `PWM_POLARITY_INVERSED` ⇒ dt-bindings 头里没这个宏 ⇒ **dtc 报错**（能立刻发现）；
- C 里写 `PWM_POLARITY_INVERTED` ⇒ 驱动通常没 include dt-bindings 头 ⇒ **编译报未定义**（也能发现）。

真正危险的是**两个头都被 include 进来的驱动**（DT 绑定常量和内核枚举混用），
此时两个名字都能编过，赋给 `state.polarity` 的值恰好都是 1，
**这一次错得不明显，换个平台或者以后枚举加值就炸**。

### §3.2 三个条件同时成立，极性才会被解析

`of_pwm_xlate_with_flags()` 的极性分支（`core.c:146-148`）是三重与：

```
of_pwm_n_cells >= 3       ← provider 的 #pwm-cells 必须是 3（DT 说的，§1.3）
&& args_count > 2         ← consumer 的 pwms 必须真给了第三个 cell
&& (args[2] & PWM_POLARITY_INVERTED)
```

⚠️ **任何一条不成立，极性静默退回 `PWM_POLARITY_NORMAL`（`:144`）**，
没有警告。最常见的失败是 **provider 的 `#pwm-cells = <2>` 而 consumer 写了三个 cell**：
第三个 cell 会被 `of_parse_phandle_with_args()` 当成"下一组的 phandle"，
取 index 0 时看不出问题，**只在 `pwms` 有多组时才错位**。

⚠️ **第三个 cell 是位掩码不是枚举**。6.1 里只定义了 bit0，
其余位保留 ⇒ 写 `2` 或 `3` 不会报错，`3 & 1 == 1` 也会被判成反相。

### §3.3 arm64 上的实际写法

```dts
backlight {
	compatible = "pwm-backlight";
	pwms = <&pwm3 0 6666667 PWM_POLARITY_INVERTED>;
	...
};
```
（`arch/arm64/boot/dts/freescale/imx8mp-verdin.dtsi:32`；同文件 `:43` 还有一处）

| 写法 | 数量 | 评价 |
|---|---|---|
| 符号名 `PWM_POLARITY_INVERTED` | **5** 个文件 | ✅ 推荐（需 `#include <dt-bindings/pwm/pwm.h>`） |
| 裸 `1` | **16** 处 | ⚠️ 能工作但读不懂 |
| 裸 `0` | 绝大多数 | 正常极性的显式写法 |

五个用符号名的文件：`allwinner/sun50i-a64-pinephone.dtsi:24` ·
`allwinner/sun50i-a64-pinetab.dts:28` · `freescale/imx8mp-verdin.dtsi:32` ·
`freescale/imx8mm-verdin.dtsi:30` · `rockchip/rk3368-evb.dtsi:63`
——**全部是背光**。反相背光是这个标志的主要用途
（低电平点亮的 LED 驱动）。

---

## §4 DT 里的值最终落在哪：`args`，不是 `state`

这是 pwm DT 面最容易误解的一点，也是 02 §2 和 03 §A3.1 反复强调的：

```
DTS: pwms = <&pwm0 0 25000 0>
                │   └───┴── 周期 25000ns、标志 0
                └────────── 通道号 0
                                  │
                 of_xlate（core.c:143-148）
                                  ↓
              pwm->args.period   = 25000        ← ⚠️ 落在 args
              pwm->args.polarity = NORMAL
                                  ↓
              pwm->state.*       ⚠️ 完全没动，还是 pwmchip_add() 时的零值
              硬件寄存器          ⚠️ 完全没动
```

`struct pwm_args` 的 kerneldoc 自己就写着这句
（`include/linux/pwm.h:34-36`）：
"Do not confuse this with the PWM state: PWM arguments represent the
**initial configuration that users want to use** on this PWM device
rather than the current PWM hardware state."

⇒ **DT 里的周期和极性是"建议值"，不是"已生效值"。**
要让它生效，消费者必须显式取出来：

| 取用方式 | 位置 | 会不会带上 DT 极性 |
|---|---|---|
| `pwm_init_state(pwm, &state)` | `include/linux/pwm.h:179` | ✅ 会（它内部读 `args`） |
| `pwm_get_args(pwm, &pargs)` 后自己填 | `include/linux/pwm.h:156` | 取决于你抄不抄 `pargs.polarity` |
| `pwm_apply_args(pwm)` | `include/linux/pwm.h:522` | ✅ 会，但⚠️ **5 个调用者都挂着"应删除"的 FIXME**（04 §3） |
| `pwm_adjust_config(pwm)` | `core.c:626` | ✅ 会（`:644-645`），⚠️ 但 `period == 0` 时把 duty 清零（`:642-647`，05 §2.2 的最重陷阱） |
| 自己 `memset` 一个 `pwm_state` 再 apply | — | ❌ **DT 极性被丢弃，无任何警告** |

⚠️ 所以"DTS 里写了 `PWM_POLARITY_INVERTED` 但波形没反"这个现象，
**十有八九是消费者驱动没走 `args` 通道**，而不是 DT 写错了。
判断方法：`grep -n 'pwm_init_state\|pwm_get_args\|pwm_apply_args\|pwm_adjust_config' <消费者驱动>.c`
——一个都没有就是它。

---

## §5 arm64 真实节点样板（四大消费者）

### §5.1 背光（36 处，最常见）

```dts
backlight: backlight {
	compatible = "pwm-backlight";
	enable-gpios = <&gpio1 RK_PB5 GPIO_ACTIVE_HIGH>;
	pwms = <&pwm0 0 25000 0>;		/* 25µs = 40kHz */
	brightness-levels = <0 1 2 ... 255>;
	default-brightness-level = <200>;
};
```
（`arch/arm64/boot/dts/rockchip/rk3399-firefly.dts:29-32` 及后续亮度表）

绑定：`Documentation/devicetree/bindings/leds/backlight/pwm-backlight.yaml`
——`pwms` 必需（`:18`）、`pwm-names` 允许但无用（`:21`）、
`brightness-levels`（`:42`）+ `default-brightness-level`（`:51`）
+ `num-interpolated-steps`（`:57`）。

⚠️ **`enable-gpios` 和 PWM 是两条独立通路**：GPIO 拉低只是关背光使能脚，
PWM 通道仍在跑。省电要两个都关。

### §5.2 风扇（9 处）

```dts
fan: pwm-fan {
	compatible = "pwm-fan";
	cooling-levels = <0 12 18 255>;		/* 0–255，见 pwm-fan 的 MAX_PWM */
	#cooling-cells = <2>;
	fan-supply = <&vcc12v0_sys>;
	pwms = <&pwm1 0 50000 0>;		/* 50µs = 20kHz */
};
```
（`arch/arm64/boot/dts/rockchip/rk3399-nanopc-t4.dts:57-65`）

⚠️ **`cooling-levels` 的单位是 0–255 的 PWM 刻度，不是占空比百分比、
也不是纳秒**。`pwm-fan` 用 `MAX_PWM 255`
（`drivers/hwmon/pwm-fan.c:22`）把它换算成 `duty_cycle`（`:209`，04 §3.4）。

### §5.3 PWM 调压器（23 处）

```dts
vdd_log: vdd-log {
	compatible = "pwm-regulator";
	pwms = <&pwm2 0 25000 1>;
	pwm-supply = <&vcc5v0_sys>;
	pwm-dutycycle-unit = <1000>;		/* 千分比 */
	pwm-dutycycle-range = <700 300>;	/* 700‰→最低压，300‰→最高压 */
	regulator-min-microvolt = <800000>;
	regulator-max-microvolt = <1400000>;
};
```
（结构照 `Documentation/devicetree/bindings/regulator/pwm-regulator.yaml:95-106`
的 example；`pwms` 必需见 `:84`，两个 dutycycle 属性见 `:61` / `:69`）

⚠️⚠️ **这是全子系统最危险的一个 DT 组合。** `pwm-regulator` 是
`pwm_adjust_config()` 的**唯一树内调用者**
（`drivers/regulator/pwm-regulator.c:374`）⇒ **provider 驱动必须实现
`.get_state`**，否则 `pwm_adjust_config()` 走 `period == 0` 分支把
duty 清零（`core.c:642-647`），**这条电轨直接掉到最低压**。
上电前先查：`grep -n 'get_state' drivers/pwm/pwm-<你的控制器>.c`。

⚠️ 另一种模式是查表（`voltage-table`，见 yaml `:119`）——
有 `voltage-table` 就是 Voltage Table Mode，没有就是连续模式
（`:34-35`）。**两种模式的 DT 属性不能混写**。

### §5.4 PWM 当时钟源（22 处）

```dts
wifi32k: wifi32k {
	compatible = "pwm-clock";
	#clock-cells = <0>;
	clock-frequency = <32768>;
	pwms = <&pwm_ef 0 30518 0>;	/* 30518ns ≈ 32.768kHz */
};
```
（`arch/arm64/boot/dts/amlogic/meson-gxl-s905x-p212.dtsi:76-79`）

用途几乎清一色是**给 WiFi/BT 模块喂 32.768kHz 低速时钟**。

这个消费者是全子系统里 **DT 校验做得最严的一个**，
`drivers/clk/clk-pwm.c` probe 里连着四道门：

| 行 | 检查 | 失败信息 |
|---|---|---|
| `:86-89` | `args.period != 0` | `invalid PWM period` + `-EINVAL` |
| `:91-92` | **`clock-frequency` 是可选的**——没写就用 `NSEC_PER_SEC / args.period` 反推 | — |
| `:94-97` | 反推/给定的频率不能为 0 | `fixed_rate cannot be zero` + `-EINVAL` |
| `:99-104` | ⚠️ **`clock-frequency` 必须和周期严格对得上**（容许 `NSEC_PER_SEC / f` 和 `DIV_ROUND_UP` 两种取整） | `clock-frequency does not match PWM period` + `-EINVAL` |

⇒ 例子里 `32768` 配 `30518` 不是随手写的：
`1000000000 / 32768 = 30517.578…`，`DIV_ROUND_UP` 得 **30518** ⇒ 刚好过第四道门。
⚠️ **写成 30517 会被拒绝**（`1000000000 / 32768` 向下取整是 30517，
这一支也在容许范围里；真正会挂的是 30516 或 30519 这类）。
⚠️ **最省事的写法是干脆不写 `clock-frequency`**，让 `:91-92` 自己反推。

⚠️ 占空比这一侧没得选：`clk-pwm.c:111` 硬编码
`pwm_config(pwm, (pargs.period + 1) >> 1, pargs.period)` ⇒ **恒定 50%**，
DT 无法调（04 §4.1）。

---

## §6 绑定文件地图与一个命名陷阱

### §6.1 `bindings/pwm/` 目录构成

`Documentation/devicetree/bindings/pwm/` 共 **51 个文件**：

| 类型 | 数量 | 含义 |
|---|---|---|
| `.yaml` | **25** | 已 schema 化，`make dtbs_check` 会校验 |
| `.txt` | **26** | ⚠️ **未 schema 化，不校验** |

⚠️ **超过一半还是 `.txt`**，包括 consumer 侧的总纲
`Documentation/devicetree/bindings/pwm/pwm.txt`
⇒ **`pwms` 属性本身从来没被自动校验过**。这解释了为什么 §7 里
那么多错误只能在运行时发现。

arm64 相关且已 yaml 化的 provider 绑定（挑几个常见的）：
`pwm-rockchip.yaml` · `imx-pwm.yaml` · `imx-tpm-pwm.yaml` ·
`allwinner,sun4i-a10-pwm.yaml` · `google,cros-ec-pwm.yaml` ·
`mediatek,pwm-disp.yaml` · `renesas,pwm-rcar.yaml` ·
`toshiba,pwm-visconti.yaml` · `intel,keembay-pwm.yaml` ·
`microchip,corepwm.yaml` · `sunplus,sp7021-pwm.yaml`。

⚠️ **`pwm-meson.txt` / `pwm-sprd.txt` / `pwm-hibvt.txt` 等 arm64
在用的控制器仍是 `.txt`**——改这些平台的 DTS 时没有 schema 护栏。

### §6.2 `clk-pwm` 与 `pwm-clock`：文件名和驱动名交叉互换

这是 pwm 全域最容易搞混的一组四个名字。**两个方向完全相反的桥**：

| 方向 | compatible | 绑定文件 | 驱动文件 |
|---|---|---|---|
| **clk → PWM**（吃一个时钟，对外提供 PWM） | `clk-pwm` | `Documentation/devicetree/bindings/pwm/clk-pwm.yaml:23` | `drivers/pwm/pwm-clk.c:130` |
| **PWM → clk**（吃一个 PWM，对外提供时钟） | `pwm-clock` | `Documentation/devicetree/bindings/clock/pwm-clock.txt` | `drivers/clk/clk-pwm.c:140` |

⚠️ **注意 compatible 和驱动文件名是反的**：compatible `clk-pwm`
的驱动叫 `pwm-clk.c`，compatible `pwm-clock` 的驱动叫 `clk-pwm.c`。
记忆口径只有一条能用：

> **看它在哪个目录**。`drivers/pwm/` 下的是 PWM provider，
> `drivers/clk/` 下的是 clk provider。**绑定文件的目录同理**
> （`bindings/pwm/` 里的 `clk-pwm.yaml` 描述的是 PWM 控制器）。

`clk-pwm.yaml` 的关键约束：`clocks` 必需、`#pwm-cells` 固定为 2
（`:29-30`）、`compatible` 固定为 `clk-pwm`（`:23`）。
⚠️ 而 `drivers/pwm/pwm-clk.c` **没有 `.get_state`**（04 §4.1）
⇒ 拿它去配 `pwm-regulator` 就会踩 §5.3 那个坑。

---

## §7 DT 写错了会怎样（排错对照表）

按"现象最难查"排序。⚠️ 标出的行**在 dmesg 里一个字都没有**：

| 现象 | 真实原因 | 判据 |
|---|---|---|
| ⚠️ 消费者设备一直不出现，dmesg 干净 | `pwms` 指的 provider 还没 probe（或没编进内核） | `-EPROBE_DEFER`（`core.c:688`）；查 `/sys/kernel/debug/devices_deferred` |
| ⚠️ `devm_pwm_get()` 返回 `-EINVAL` | ①漏了 `pwm-names`（消费者传了 con_id）②通道号 ≥ `npwm` ③`#pwm-cells` 与 xlate 不匹配 | 三者 errno 完全相同，只能逐个排除；见下三行 |
| ⚠️ `-EINVAL`，`pwms` 看着没问题 | 消费者传了非 NULL `con_id` 但 DT 没写 `pwm-names` | `drivers/of/property.c:472-473`；查消费者 `devm_pwm_get(dev, "xxx")` 的第二参 |
| ⚠️ `-EINVAL`，通道号写大了 | `args[0] >= npwm` | `core.c:136-137`；`cat /sys/class/pwm/pwmchipN/npwm` |
| ⚠️ `-EINVAL`，`#pwm-cells = <1>` | provider 用的是默认 xlate，它要求 ≥ 2 | `core.c:129-130`；查 provider 有没有设 `of_xlate`（§1.4 四个例外） |
| dmesg 有 `can't parse "pwms" property` | 属性名拼错 / phandle 无效 / cell 数对不上 | `core.c:756`——**这是唯一有日志的失败** |
| ⚠️ 极性没反 | ①provider 的 `#pwm-cells` 不是 3 ②消费者没走 `args` 通道 | `core.c:146-148` 三重与；§4 的 grep 判据 |
| ⚠️ 多组 `pwms` 里第二组解析错位 | 每组的 cell 数必须等于 provider 的 `#pwm-cells`，DTB 里没有分组信息 | 数一下：`pwms` 的总 cell 数应能被 `(1 + #pwm-cells)` 整除 |
| ⚠️ apply 全部成功，示波器没波形 | 引脚没复用到 PWM 功能 | provider 节点缺 `pinctrl-0`；`cat /sys/kernel/debug/pinctrl/*/pinmux-pins` |
| ⚠️ PWM 调压轨电压掉到最低 | `pwm-regulator` + provider 缺 `.get_state` | `core.c:642-647`；`grep -n get_state drivers/pwm/pwm-<x>.c` |
| dtc 报未定义标识符 | DTS 里写了 C 端的 `PWM_POLARITY_INVERSED` | 应为 `PWM_POLARITY_INVERTED`（§3.1） |
| 极性莫名反了 | 第三 cell 写了 `2`/`3`，而它是位掩码 | `3 & PWM_POLARITY_INVERTED == 1`（§3.2） |

---

## §8 复算命令

全部只读，可直接跑（工作目录 = 内核树根）：

```bash
# §0.1 arm64 数据面
grep -rl 'pwms =' arch/arm64/boot/dts/ | wc -l                       # 112
grep -rl '#pwm-cells' arch/arm64/boot/dts/ | wc -l                   # 71
grep -rl 'pwm-names' arch/arm64/boot/dts/                            # 4 个文件
grep -rhoE '#pwm-cells = <[0-9]+>' arch/arm64/boot/dts/ | sort | uniq -c

# §0.1 消费者分布
grep -rn -B4 'pwms = <' arch/arm64/boot/dts/ \
  | grep -oE 'compatible = "[^"]+"' | sort | uniq -c | sort -rn

# §1.4 全部自定义 xlate（4 个）
grep -rn '\.of_xlate[[:space:]]*=' drivers/

# §3.3 极性写法
grep -rl 'PWM_POLARITY_INVERTED' arch/arm64/boot/dts/                # 5
grep -rhoE 'pwms = <&[a-z0-9_]+ [0-9]+ [0-9]+ 1>' arch/arm64/boot/dts/ | wc -l  # 16

# §6.1 绑定目录构成
ls Documentation/devicetree/bindings/pwm/*.yaml | wc -l              # 25
ls Documentation/devicetree/bindings/pwm/*.txt  | wc -l              # 26

# §6.2 两个反向桥
grep -n 'compatible' drivers/pwm/pwm-clk.c drivers/clk/clk-pwm.c

# §4 判断消费者有没有用 DT 的 args
grep -n 'pwm_init_state\|pwm_get_args\|pwm_apply_args\|pwm_adjust_config' \
    drivers/video/backlight/pwm_bl.c drivers/hwmon/pwm-fan.c \
    drivers/regulator/pwm-regulator.c drivers/clk/clk-pwm.c
```

---

## §9 接着读

- provider/consumer 的 C 侧骨架（含 `.get_state` 那条硬约束）→ [05-usage.md](05-usage.md)
- `of_xlate` 在注册/查找时序里的位置 → [03-internals.md](03-internals.md) 的 §A1.3 与 §A3.1
- `pwm_args` 与 `pwm_state` 的字段级对照 → [02-datastructures.md](02-datastructures.md)
- DT 配好了但波形不对，怎么用 debugfs/tracepoint 定位 → [07-debug.md](07-debug.md)
