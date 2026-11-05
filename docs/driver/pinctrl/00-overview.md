---
title: 总览
---

# pinctrl 子系统总览

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 **arm64**。
> 本篇定位是**复习入口**——隔几个月回来只读这一篇就能恢复全貌，需要细节再往后翻。
> 子系统类型：**框架型**（无自己的 `bus_type`，通过 API 被其他驱动消费）。判定过程见 `99-evidence.md` §1.3。

---

## §1 一句话定位

**pinctrl 是"引脚归属权"的仲裁者。** 它把 SoC 上每一个可复用引脚（pin）建模成一个带主人的资源，替你做四件事：

| 帮你省掉了什么 | 没有 pinctrl 时你得自己做 |
|---|---|
| **引脚冲突检测** | 手工核对"UART2 的 TX 和 SPI0 的 MOSI 是不是同一根引脚"，写错了两个外设一起哑掉且没有任何报错 |
| **复用寄存器的具体写法** | 每个消费者驱动（i2c、spi、uart…）都得知道 SoC 的 pinmux 寄存器地址和位域 |
| **电气参数的统一词汇** | "上拉""驱动强度""施密特触发"在每家 SoC 里叫法和编码都不同，消费者要按 SoC 分支 |
| **状态切换与 PM 挂钩** | 睡眠前把引脚拉低省电、唤醒后再复用回来——这套时序要在每个驱动里重写一遍 |

一句话：**消费者驱动只声明"我要 default 状态"，剩下的谁跟谁抢引脚、寄存器怎么写、什么时候写，全归 pinctrl 核心和 pin controller 驱动。**

关键认知：pinctrl **不是** GPIO。GPIO 是"把某根线当成 1 bit 数据来读写"，pinctrl 是"决定这根线归谁、以什么电气特性工作"。两者的分界线在官方文档里专门有一节讲（`Documentation/driver-api/pin-control.rst:789-985`「GPIO mode pitfalls」），详见 `08-feynman.md` §3。

---

## §2 背景：问题域与核心抽象

> 本节走**路径 B（框架型的问题背景）**。pinctrl 没有物理协议、没有波形可画——它的约束来自 SoC 引脚复用这件事本身。后面大量设计选择（引脚所有权、first-come first-serve、`-EPROBE_DEFER` 满天飞、strict 标志）都是这些约束的直接后果。

### §2.1 问题域：为什么 SoC 需要引脚复用

一颗 arm64 SoC 内部集成的外设控制器数量，远远超过它的封装能提供的引脚数量。一个典型数量级：

- SoC 内部有 UART ×6、I2C ×8、SPI ×5、SDIO ×3、I2S ×2、CAN ×2、以太网 RGMII ×1、若干 PWM……
- 而封装上留给这些外设的**通用引脚只有 100~200 根**

厂商的解法是**硬件多路复用器**：一根物理焊盘（pad）后面接一个选择器，选择器的输入端接着若干个内部控制器的信号线，一个寄存器位域决定"这一刻这根 pad 到底连到哪个控制器"。

```
   芯片封装外部                    芯片内部
                     ┌──────────┐
                     │          ├──── UART2_TX
   焊盘 pad ─────────┤  MUX     ├──── SPI0_MOSI      ← 同一时刻只能选一路
   (物理引脚 GPIO12) │  选择器  ├──── I2S1_SDOUT
                     │          ├──── GPIO12（当普通 IO 用）
                     └────▲─────┘
                          │
                    MUX 寄存器某个 3-bit 位域 = 0/1/2/3
```

于是产生了三个必须由软件解决的问题：

1. **谁来写这个位域？** 消费者驱动（比如 `drivers/tty/serial/`）不该知道 SoC 的 pinmux 寄存器长什么样
2. **怎么防止两个驱动都想要同一根 pad？** UART2 和 SPI0 都 probe 成功、都以为自己拿到了 pad，实际上后写的那个赢，前一个静默失效
3. **电气参数怎么描述？** 同一根 pad 在做 I2C 时要开漏 + 上拉，做 SPI 时要推挽，做睡眠态时要输出低电平。这些参数的寄存器编码每家 SoC 都不一样

pinctrl 的三层 ops（`pinctrl_ops` / `pinmux_ops` / `pinconf_ops`）正好一一对应这三个问题的第 1、2、3 项拆分方式。

### §2.2 核心抽象：五个名词

pinctrl 的整个模型可以用五个名词讲完。**这五个名词的层级关系是理解本子系统的关键**：

```
  pin controller (一颗 SoC 上的一个引脚控制器硬件块)
   │  内核对象 = struct pinctrl_dev  (drivers/pinctrl/core.h:42)
   │  在 platform 总线上以 platform_device 出现（arm64 上一律来自 DT）
   │
   ├── pin           编号 0..npins-1，控制器**局部**编号，可以稀疏
   │                 内核对象 = struct pin_desc (drivers/pinctrl/core.h:159)
   │                 描述符 = struct pinctrl_pin_desc (include/linux/pinctrl/pinctrl.h:56)
   │
   ├── group         一组 pin 的命名集合，如 "i2c0_grp" = {24, 25}
   │                 组名在**单个控制器内**必须唯一
   │
   └── function      一个命名的复用功能，如 "i2c0"，可作用于若干 group
                     function 和 group 永远成对出现：set_mux(func, group)

  ---------- 以上是"控制器提供什么"，以下是"消费者要什么" ----------

  mapping entry     一条 <消费者设备, 状态名, 控制器, function, group> 的元组
   │                内核对象 = struct pinctrl_map (include/linux/pinctrl/machine.h:67)
   │                arm64 上由 DT 解析生成（devicetree.c），不手写数组
   │
   └── state         消费者的一个命名状态，"default" / "sleep" / "idle" / "init"
        │            内核对象 = struct pinctrl_state (drivers/pinctrl/core.h:92)
        │            状态名常量：include/linux/pinctrl/pinctrl-state.h:33-36
        │
        └── setting  状态里的一条具体动作（一次 mux 或一批 config）
                     内核对象 = struct pinctrl_setting (drivers/pinctrl/core.h:131)
```

**pin 编号是控制器局部的、而且可以稀疏**——这一条最容易被忽略，官方文档 `Documentation/driver-api/pin-control.rst:41-70` 专门强调过。它导致核心必须用 `radix_tree` 而不是数组来存 `pin_desc`（`drivers/pinctrl/core.c:2014` 初始化 `pin_desc_tree`，`core.c:238` 用 `radix_tree_insert` 插入）。

### §2.3 为什么消费者不能直接写 MUX 寄存器

框架替消费者屏蔽了四类复杂性，每一类都有明确的代码落点：

| 屏蔽了什么 | 不用框架的话你得写 | 框架里的实现 |
|---|---|---|
| **引脚所有权仲裁** | 一张全局表记录"每根 pin 现在归谁"，还要处理并发 | `pin_request()` `drivers/pinctrl/pinmux.c:108`；冲突检查在 `:127-140` |
| **状态的原子切换** | 先拆旧状态再装新状态，中途失败要能回滚 | `pinctrl_commit_state()` `drivers/pinctrl/core.c:1247`；回滚 `pinmux_enable_setting()` 的 `err_pin_request:` `pinmux.c:478` |
| **探测顺序依赖** | 消费者比控制器先 probe 时要自己重试 | 一律返回 `-EPROBE_DEFER`：`add_setting()` `pinmux.c` 侧的 `core.c:987`、`pinctrl_get_device_gpio_range()` `core.c:377` |
| **设备核心自动接管** | 每个驱动 probe 开头抄一段 get/lookup/select | `pinctrl_bind_pins()` `drivers/base/pinctrl.c:21`，由 `drivers/base/dd.c:616` 在 probe 之前调用 |

最后一条是 pinctrl 与其他框架最不一样的地方：**绝大多数消费者驱动一行 pinctrl 代码都不用写**。设备核心在 `really_probe()` 里替你把 default 状态选上了。这也是为什么全树 377 个 pinctrl 驱动文件里、消费者侧的 `devm_pinctrl_get()` 调用点却寥寥无几——详见 `05-usage.md` §2。

### §2.4 引脚所有权模型：first-come first-serve

这是 pinctrl 最核心的一条规则，官方文档 `Documentation/driver-api/pin-control.rst:512-524` 原文写的是：pin 的占用是**先到先得**，**新的 setting 生效之前必须先把旧的 setting 停用**。

代码里落在两个字段上（`struct pin_desc`，`drivers/pinctrl/core.h:159`）：

```
struct pin_desc {
        ...
        const char *mux_owner;      ← 谁把它复用走了（消费者的 dev_name）
        const char *gpio_owner;     ← 谁把它当 GPIO 用了
        unsigned int mux_usecount;  ← 同一个 owner 重复请求的计数
        ...  (mux 相关字段整体在 #ifdef CONFIG_PINMUX 之下)
};
```

`pin_request()`（`drivers/pinctrl/pinmux.c:108`）的仲裁逻辑：

```
  请求方是 GPIO（gpio_range 非空）？
    ├── 是 ──→ gpio_owner 已有主人 → 冲突，返回 -EINVAL
    │          strict 且 mux_usecount 非 0 且 owner 不同 → 冲突，返回 -EINVAL
    └── 否 ──→ mux_usecount 已 >0 且 mux_owner 与请求者不同 → 冲突，返回 -EINVAL
               mux_usecount 已 >0 且 owner 相同 → mux_usecount++ 后直接返回 0
               strict 且 gpio_owner 非空 → 冲突，返回 -EINVAL
```

**冲突返回的是 `-EINVAL` 而不是 `-EBUSY`**——`pin_request()` 开头就是 `int status = -EINVAL;`（`drivers/pinctrl/pinmux.c:114`），三个冲突分支都是直接 `goto out`，从不改写 `status`。这是一处很容易凭直觉写错的返回值（`-EBUSY` 看起来更合语义），排障时按错的码去 grep 会一无所获。同一函数里 `try_module_get()` 失败也返回 `-EINVAL`。

三个反直觉的后果，全部有代码依据：

1. **同一个消费者重复请求同一根 pin 不报错**，只是 `mux_usecount++` 后提前 `return 0`（`pinmux.c:146-147`）——所以"一个 state 里两条 mapping 覆盖同一根 pin"是合法的
2. **`strict` 标志决定 GPIO 和 mux 能否共存**。`struct pinmux_ops` 的 `strict` 字段（`include/linux/pinctrl/pinmux.h:60` 起）由驱动自己声明，核心只负责执行。硬件形态 (A)/(B) 的区别见 `pin-control.rst:810-864`
3. **停用一个 setting 时，核心只释放"`mux_setting` 仍指向自己"的那些 pin**，否则打印 `dev_warn` 并跳过（`pinmux_disable_setting()` `drivers/pinctrl/pinmux.c:486`）——这是为了防止踩掉别人后来抢走的 pin

### §2.5 三层 ops：一个控制器要实现哪些回调

pinctrl 的可插拔层只有 **1 层**（对比 virtio 的 2 层），但这一层被切成三块 ops，**分别由三个 Kconfig 开关控制、可以只实现一部分**：

| ops | 头文件 | 管什么 | 必选回调 | 核心的校验函数 | Kconfig |
|---|---|---|---|---|---|
| `pinctrl_ops` | `include/linux/pinctrl/pinctrl.h:108` | 枚举 pin group、把 DT 节点翻译成 mapping | `get_groups_count` + `get_group_name` | `pinctrl_check_ops()` `core.c:1976` | 总是编译 |
| `pinmux_ops` | `include/linux/pinctrl/pinmux.h:60` | 复用：选 function 到 group | `get_functions_count` + `get_function_name` + `get_function_groups` + `set_mux`，且每个 function 都必须有名字 | `pinmux_check_ops()` `pinmux.c:32` | `CONFIG_PINMUX` |
| `pinconf_ops` | `include/linux/pinctrl/pinconf.h:38` | 电气配置：上下拉、驱动强度… | `pin_config_set` **或** `pin_config_group_set`（二者其一即可） | `pinconf_check_ops()` `pinconf.c:25` | `CONFIG_PINCONF` |

**`pmxops` / `confops` 为 NULL 是完全合法的**：`pinctrl_init_controller()`（`core.c:1994`）只在指针非空时才调用对应的 `*_check_ops()`，debugfs 里也只在非空时才创建对应文件（`core.c:1932-1935`）。所以存在"只能复用不能配置"和"只能配置不能复用"的控制器。

**全树普查**（本基线实测，命令见 `99-evidence.md` §2.3）：

| 结构体赋值点 | 数量 | 说明 |
|---|---|---|
| `struct pinctrl_ops X = {` | 106 | 每个控制器至少一个 |
| `struct pinmux_ops X = {` | 100 | 6 个控制器不做复用 |
| `struct pinconf_ops X = {` | 89 | 17 个控制器不做电气配置 |
| `struct pinctrl_desc X = {` | 63 | 静态 desc；其余驱动把 desc 嵌在私有结构里动态填（如 `pinctrl-msm.c:63`） |

### §2.6 泛型 group/function 存储：驱动可以不自己管表

核心提供两套**可选的**泛型实现，驱动只要 `select` 对应 Kconfig 就能把 `get_groups_count` 一类回调直接指向核心函数：

| Kconfig | 提供的存储 | 驱动侧只需调用 | 核心侧回调可直接用 |
|---|---|---|---|
| `GENERIC_PINCTRL_GROUPS` | `pctldev->pin_group_tree`（radix tree，`core.c:2016` 初始化） | `pinctrl_generic_add_group()` `core.c:635` | `pinctrl_generic_get_group_count/name/pins` `core.c:532/543/564` |
| `GENERIC_PINMUX_FUNCTIONS` | `pctldev->pin_function_tree`（`core.c:2019` 初始化） | `pinmux_generic_add_function()` `pinmux.c:876` | `pinmux_generic_get_function_count/name/groups` `pinmux.c:795/806/828` |

`drivers/pinctrl/pinctrl-apple-gpio.c:160-166,182-188` 是这两套的教科书用法：五个枚举回调全部指向核心的泛型实现，驱动只写了 `set_mux` 和 `dt_node_to_map`。

### §2.7 与相近框架的职责边界

| 框架 | 管什么 | 与 pinctrl 的接缝 |
|---|---|---|
| **gpiolib**（`drivers/gpio/`） | 把一根线当 1 bit 数据读写 + 中断 | gpiolib 驱动在自己的 `request`/`direction_*` 里回调 `pinctrl_gpio_request()` 等（`core.c:785` 起）。范围映射靠 `struct pinctrl_gpio_range`（`pinctrl.h:78`） |
| **pinconf-generic**（`drivers/pinctrl/pinconf-generic.c`） | pinctrl **内部**的一层可选实现：把标准 DT 属性翻译成 `PIN_CONFIG_*` | 不是独立框架，是 `pinctrl_ops.dt_node_to_map` 的现成实现。见 `04-extensions.md` §2 |
| **regmap** | 寄存器访问抽象 | 正交。有的 pinctrl 驱动用 regmap 访问 syscon 里的 mux 寄存器（如 rockchip 的 `rockchip,grf`） |
| **PM 核心** | suspend/resume 时序 | pinctrl 提供 `pinctrl_pm_select_sleep_state()` 等（`core.c:1600-1626`，`#ifdef CONFIG_PM`），驱动在自己的 PM 回调里调 |

**pinctrl 和 gpiolib 的关系是本子系统最容易搞混的一处**，而且官方文档明确要求：如果 pin controller 和 GPIO controller 管着同一批引脚且涉及复用，**必须**把 pin controller 做成 GPIO 驱动的后端（`pin-control.rst:1339-1344`）。完整解释见 `08-feynman.md` §3、§4。

### §2.8 arm64 上的实际形态

**驱动规模**（`drivers/pinctrl/`）：377 个 `.c` 文件、419,438 行；其中核心（`core.c` / `pinmux.c` / `pinconf.c` / `pinconf-generic.c` / `devicetree.c` / `pinctrl-utils.c` + 私有头）共 **5,283 行**，其余全是厂商驱动。28 个厂商子目录，arm64 上最常见的是 `qcom/`、`mediatek/`、`meson/`、`renesas/`、`samsung/`、`tegra/`、`sunxi/`、`freescale/`、`uniphier/`、`sprd/`、`visconti/`、`starfive/`。

**DT 使用面**：`arch/arm64/boot/dts/` 下有 **556** 个文件出现 `pinctrl-0`、**546** 个出现 `pinctrl-names`。

**一个真实的 in-tree arm64 例子**（Qualcomm MSM8916，`arch/arm64/boot/dts/qcom/`）——控制器侧：

```dts
/* arch/arm64/boot/dts/qcom/msm8916.dtsi:922 */
msmgpio: pinctrl@1000000 {
        compatible = "qcom,msm8916-pinctrl";
        reg = <0x01000000 0x300000>;
        interrupts = <GIC_SPI 208 IRQ_TYPE_LEVEL_HIGH>;
        gpio-controller;
        gpio-ranges = <&msmgpio 0 0 122>;   ← 122 根 pin 同时是 GPIO
        #gpio-cells = <2>;
        interrupt-controller;
        #interrupt-cells = <2>;
};
```

状态节点侧（`arch/arm64/boot/dts/qcom/msm8916-pins.dtsi:8-23`，挂在 `&msmgpio` 下）：

```dts
blsp1_uart1_default: blsp1-uart1-default {
        pins = "gpio0", "gpio1", "gpio2", "gpio3";
        function = "blsp_uart1";        ← 复用（pinmux_ops.set_mux）
        drive-strength = <16>;          ← 配置（pinconf_ops）
        bias-disable;
};

blsp1_uart1_sleep: blsp1-uart1-sleep {
        pins = "gpio0", "gpio1", "gpio2", "gpio3";
        function = "gpio";              ← 睡眠时切回 GPIO 功能
        drive-strength = <2>;
        bias-pull-down;
};
```

消费者侧（`arch/arm64/boot/dts/qcom/msm8916.dtsi:1590-1603`）：

```dts
blsp_i2c2: i2c@78b6000 {
        compatible = "qcom,i2c-qup-v2.2.1";
        ...
        pinctrl-names = "default", "sleep";
        pinctrl-0 = <&i2c2_default>;
        pinctrl-1 = <&i2c2_sleep>;
};
```

注意消费者节点里**没有任何 pinctrl 代码**：`pinctrl-names`/`pinctrl-0`/`pinctrl-1` 由 pinctrl 核心的 `pinctrl_dt_to_map()`（`drivers/pinctrl/devicetree.c:200`）解析，`default` 状态由设备核心自动选上。DTS 写法的完整规则见 `06-devicetree.md`。

**另一种风格**：厂商可以完全不用标准属性。Rockchip 用一条自定义属性表达全部信息（`arch/arm64/boot/dts/rockchip/rk3399.dtsi:2334-2340`）：

```dts
i2c1 {
        i2c1_xfer: i2c1-xfer {
                rockchip,pins =
                        <4 RK_PA2 1 &pcfg_pull_none>,   /* bank, pin, mux, config-phandle */
                        <4 RK_PA1 1 &pcfg_pull_none>;
        };
};
```

这是合法的——`pinctrl.yaml` 明确说明"子节点的内容完全由各控制器自己的 binding 定义，不存在通用标准"（`Documentation/devicetree/bindings/pinctrl/pinctrl.yaml` description 段）。属性由谁消费的完整分工见 `06-devicetree.md` §1。

---

## §3 概念模型：三方角色

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ 消费者驱动（i2c / spi / uart / mmc …）                                │
 │  只声明状态名，绝大多数一行 pinctrl 代码都不写                          │
 └───────────────────────────────┬──────────────────────────────────────┘
                                 │ ① 设备核心代劳（默认路径）
                                 │    drivers/base/dd.c:616 → pinctrl_bind_pins()
                                 │ ② 驱动自己调（需要非 default 状态时）
                                 │    pinctrl_get / lookup_state / select_state
 ┌───────────────────────────────▼──────────────────────────────────────┐
 │ pinctrl 核心                                                          │
 │                                                                       │
 │  全局 mapping 表 pinctrl_maps (core.c:58)  ← DT 解析或板级数组注册      │
 │  全局 handle 表 pinctrl_list (core.c:55)                              │
 │  全局控制器表 pinctrldev_list (core.c:52)                             │
 │                                                                       │
 │  仲裁：pin_request/pin_free (pinmux.c:108/207)                        │
 │  状态切换：pinctrl_commit_state (core.c:1247)                         │
 └───────────────────────────────┬──────────────────────────────────────┘
                                 │ 三层 ops 回调
                                 │  pctlops → 枚举 group / 翻译 DT
                                 │  pmxops  → set_mux(func, group)
                                 │  confops → pin_config_(group_)set
 ┌───────────────────────────────▼──────────────────────────────────────┐
 │ pin controller 驱动（drivers/pinctrl/<vendor>/）                      │
 │  自身是 platform_device，写 SoC 的 MUX / PAD 寄存器                    │
 └──────────────────────────────────────────────────────────────────────┘
```

**哪些关系是设备模型的 device↔driver 匹配，哪些只是指针**：

- pin controller 自己：**是** 一个 `platform_device`，走 platform 总线的 `of_match_table` 匹配
- `struct pinctrl_dev`：**不是** `struct device`。它只有一个 `dev` **指针**指回那个 platform_device（`core.h:42` 结构体内的 `struct device *dev`）
- 消费者 ↔ 控制器：**没有** 任何 device↔driver 匹配。二者只通过 mapping 表里的**字符串** `ctrl_dev_name` / `dev_name` 关联（`include/linux/pinctrl/machine.h:67` 起），运行时用 `strcmp` 找（`core.c` 的 `get_pinctrl_dev_from_devname()`）
- **pinctrl 没有自己的 `bus_type`**，所以不存在"pinctrl 设备"这种东西

完整的破歧义讨论（含"为什么不会发生"的行号）见 `02-datastructures.md` §1。

---

## §4 核心数据结构速览

> 完整字段、关系图、锁见 `02-datastructures.md`。

| 结构体 | 位置 | 一句话 |
|---|---|---|
| `struct pinctrl_dev` | `drivers/pinctrl/core.h:42` | 一个已注册的 pin controller 实例；**私有结构，消费者拿不到** |
| `struct pinctrl_desc` | `include/linux/pinctrl/pinctrl.h:150` | 驱动填给核心的注册描述符（pin 表 + 三层 ops + owner） |
| `struct pinctrl_pin_desc` | `include/linux/pinctrl/pinctrl.h:56` | 驱动侧的单个 pin 声明（编号 + 名字 + drv_data），用 `PINCTRL_PIN()` 宏构造（`:63`） |
| `struct pin_desc` | `drivers/pinctrl/core.h:159` | 核心侧的单个 pin 运行时状态（`mux_owner` / `gpio_owner` / `mux_usecount` / `mux_setting`） |
| `struct pingroup` | `include/linux/pinctrl/pinctrl.h:35` | 一个命名 pin 组（名字 + pin 数组） |
| `struct pinfunction` | `include/linux/pinctrl/pinctrl.h:215` | 一个命名功能（名字 + group 名数组） |
| `struct group_desc` | `drivers/pinctrl/core.h:194` | 泛型 group 存储的表项（`GENERIC_PINCTRL_GROUPS`） |
| `struct function_desc` | `drivers/pinctrl/pinmux.h` | 泛型 function 存储的表项（`GENERIC_PINMUX_FUNCTIONS`） |
| `struct pinctrl` | `drivers/pinctrl/core.h:77` | 一个消费者设备的 pinctrl **句柄**；`pinctrl_get()` 的返回值，对消费者是不透明 cookie |
| `struct pinctrl_state` | `drivers/pinctrl/core.h:92` | 句柄下的一个命名状态，内含 setting 链表 |
| `struct pinctrl_setting` | `drivers/pinctrl/core.h:131` | 状态里的一条动作；`union` 里是 `pinctrl_setting_mux`(`:103`) 或 `pinctrl_setting_configs`(`:116`) |
| `struct pinctrl_map` | `include/linux/pinctrl/machine.h:67` | 一条 mapping 表项（消费者名 + 状态名 + 控制器名 + type + data） |
| `struct pinctrl_maps` | `drivers/pinctrl/core.h:179` | 挂在全局 `pinctrl_maps` 链表上的一"批" map |
| `struct pinctrl_gpio_range` | `include/linux/pinctrl/pinctrl.h:78` | GPIO 号 ↔ pin 号 的映射区间；`pins[]` 非空时为稀疏映射，此时 `pin_base` 被忽略 |
| `struct dev_pin_info` | `include/linux/pinctrl/devinfo.h:31` | 挂在 `struct device.pins` 上的四个状态指针（default/init/sleep/idle） |

**命名约定速查**（"看到名字就知道它是什么"）：

| 前缀/后缀 | 含义 | 例 |
|---|---|---|
| `pinctrl_*` | 核心的对外 API，或 pin/group 枚举层 | `pinctrl_get`、`pinctrl_generic_add_group` |
| `pinmux_*` | 复用层。官方文档明确规定"只有复用层能用这个前缀"（`pin-control.rst:284-285`） | `pinmux_enable_setting` |
| `pinconf_*` | 电气配置层 | `pinconf_apply_setting` |
| `pinconf_generic_*` | 标准 DT 属性 ↔ `PIN_CONFIG_*` 的翻译层 | `pinconf_generic_dt_node_to_map` |
| `*_generic_*` | 核心提供的可选泛型实现，驱动可直接挂到 ops 上 | `pinctrl_generic_get_group_name` |
| `pin_desc` vs `pinctrl_pin_desc` | 前者是**核心内部运行时**状态，后者是**驱动声明**用的 | 高危近邻符号，见 `05-usage.md` §6.1 |
| `PIN_CONFIG_*` | 电气参数枚举（`pinconf-generic.h:114-142`，25 个参数） | `PIN_CONFIG_BIAS_PULL_UP` |
| `PIN_MAP_*` | 板级 mapping 表的构造宏（`machine.h:80-150`） | `PIN_MAP_MUX_GROUP` |
| `PINCTRL_STATE_*` | 四个标准状态名字符串（`pinctrl-state.h:33-36`） | `PINCTRL_STATE_DEFAULT` = `"default"` |

**锁摘要**（详见 `02-datastructures.md` §4）：

| 锁 | 位置 | 保护什么 |
|---|---|---|
| `pinctrl_list_mutex` | `drivers/pinctrl/core.c:43` | 全局 `pinctrl_list`（所有消费者句柄） |
| `pinctrl_maps_mutex` | `drivers/pinctrl/core.c:46` | 全局 `pinctrl_maps`（mapping 表） |
| `pinctrldev_list_mutex` | `drivers/pinctrl/core.c:49` | 全局 `pinctrldev_list`（所有控制器） |
| `pctldev->mutex` | `drivers/pinctrl/core.h:61` 结构体内 | 只覆盖 GPIO range 与 GPIO 桥接、debugfs 遍历、注销清理——**状态应用路径不拿它**（详见 `03-internals.md` §B1.3） |
| `p->mutex` | `drivers/pinctrl/core.h:77` 结构体内 | 单个句柄的 state 链表 |

**全部是 `mutex`，没有 spinlock**。后果：`pinctrl_select_state()` **不能在原子上下文调用**。官方文档同一处还提醒：有的 pin controller 的寄存器在慢速/中断驱动的总线上（如 I2C PMIC），消费者不能假设 select_state 是快路径（`pin-control.rst:1267-1272`）。

---

## §5 核心机制速览

> 每条一句话 + 一个行号，排列顺序与 `03-internals.md` 完全一致。要改代码去 03。

### 时间线轴（03 Part A）

| 机制 | 一句话 | 入口 | 细节 |
|---|---|---|---|
| 子系统初始化 | `core_initcall` 级别建 debugfs 根目录，注释写明"要早，因为很多驱动很早就需要 pinmux" | `core.c:2333`，`core_initcall` 在 `:2341` | 03 §A1 |
| 控制器注册 | `pinctrl_register_and_init()` 建 `pinctrl_dev` + 校验 ops + 登记 pin 表，**但先不使能** | `core.c:2172` | 03 §A2 |
| 使能与 hog | `pinctrl_enable()` 认领 hog（控制器给自己上的 mapping）后才挂进全局链表 | `core.c:2111`；hog 在 `pinctrl_claim_hogs()` `core.c:2073` | 03 §A3 |
| mapping 注册 | 板级数组走 `pinctrl_register_mappings()`；**arm64 走 DT 不用它** | `core.c:1419` | 03 §A4 |
| DT 解析 | 按 `pinctrl-N` 逐条解析、`pinctrl-names` 命名，生成 map | `devicetree.c:200` | 03 §A5 |
| 句柄创建 | `pinctrl_get()` → `create_pinctrl()` 遍历 map 建 state/setting 树 | `core.c:1122` → `:1033` | 03 §A6 |
| 设备核心自动接管 | probe 之前选 init（无则 default），probe 之后把 init 换成 default | `drivers/base/pinctrl.c:21`；`pinctrl_init_done()` `core.c:1538` | 03 §A7 |
| 状态切换 | 先拆旧 state 的全部 setting，再逐条装新的；失败要回滚 | `pinctrl_commit_state()` `core.c:1247` | 03 §A8 |
| 复用生效/失效 | 逐 pin `pin_request()` → 写 `mux_setting` → `ops->set_mux()`；反向按 owner 匹配再释放 | `pinmux.c:405` / `:486` | 03 §A9 |
| 配置生效 | 按 map type 分派到 `pin_config_set` 或 `pin_config_group_set` | `pinconf.c:148` | 03 §A10 |
| 注销 | `pinctrl_unregister()` 摘链、删 debugfs、释放泛型表 | `core.c:2200` | 03 §A11 |

### 横切轴（03 Part B）

| 机制 | 一句话 | 位置 | 细节 |
|---|---|---|---|
| 三把全局锁 + 两把对象锁 | 全 mutex，无 spinlock，所以 select_state 只能在进程上下文 | `core.c:43/46/49` | 03 §B1 |
| `-EPROBE_DEFER` 的三个来源 | 未知控制器名、GPIO range 还没注册、DT 里的 phandle 指向还没 probe 的控制器 | `core.c:987` / `core.c:377` / `devicetree.c:134` | 03 §B2 |
| hog 的自引用检测 | mapping 的消费者名 == 控制器名时走特殊路径，DT 侧直接 `return 1` 跳过 | `core.c:981-982`；`devicetree.c:153` | 03 §B3 |
| GPIO range 双形态 | `pins[]` 非空 → 稀疏映射且 `pin_base` 被忽略；否则线性 | `gpio_to_pin()` `core.c:280` | 03 §B4 |
| devres 生命周期 | `devm_*` 变体用 `devres` 挂钩，与非 devm 版本不能交叉配对 | `core.c:1370`/`:2234-2331` | 03 §B5 |
| debugfs 树 | 全局 3 个文件 + 每控制器 3~6 个文件（后 3 个取决于 ops 是否为空） | `core.c:1904-1957`；`pinmux.c:776`；`pinconf.c:370` | 03 §B6 / 07 §4 |
| PM 状态选择 | `pinctrl_pm_select_*` 只在 `CONFIG_PM` 下有实体 | `core.c:1590-1627` | 03 §B7 |

### 评价轴（03 Part C）

| 项 | 内容 | 位置 |
|---|---|---|
| 设计债 1 | `pinctrl_unregister()` 里的 `/* TODO: check that no pinmuxes are still active? */` | `core.c:2216` |
| 设计债 2 | debugfs 显示 config 的 `FIXME`：应该让驱动自己 dump 配置值 | `pinconf.c:283-286` |
| 设计债 3 | `pinmux_free_setting()` 注释自陈 "This function is currently unused" | `pinmux.c:400-403` |
| 已废弃 | `pinctrl_register()` 的 kerneldoc 自陈"已知有问题…请改用 `pinctrl_register_and_init()`" | `core.c:2142` 上方注释 |
| 文档滞后 | `pin-control.rst` 里的 `foo_pin_config_set()` 示例签名与当前 `pinconf_ops` 不符 | 见 `99-evidence.md` §2.3 |

---

## §6 端到端调用路径

一条 arm64 上最常见的完整路径：**DT 里写了 `pinctrl-0` 的 i2c 控制器被 probe**。

```
platform_device i2c@78b6000 匹配上驱动
  │
  ▼
really_probe()                                    drivers/base/dd.c:616
  │
  ├─► pinctrl_bind_pins(dev)                      drivers/base/pinctrl.c:21
  │     │  这一步在 drv->probe() 之前，消费者驱动完全不参与
  │     │
  │     ├─► devm_pinctrl_get(dev)                 drivers/pinctrl/core.c:1370
  │     │     └─► pinctrl_get(dev)                drivers/pinctrl/core.c:1122
  │     │           │  【锁】pinctrl_list_mutex → 查是否已有句柄
  │     │           └─► create_pinctrl(dev)       drivers/pinctrl/core.c:1033
  │     │                 │
  │     │                 ├─► pinctrl_dt_to_map(p, pctldev)
  │     │                 │     │                 drivers/pinctrl/devicetree.c:200
  │     │                 │     │  读 pinctrl-names / pinctrl-0..N
  │     │                 │     └─► dt_to_map_one_config()
  │     │                 │           │           drivers/pinctrl/devicetree.c:109
  │     │                 │           │  找不到控制器 → -EPROBE_DEFER (:134)
  │     │                 │           └─► pctlops->dt_node_to_map()   ← 控制器驱动
  │     │                 │                 例：pinconf_generic_dt_node_to_map_group
  │     │                 │                     drivers/pinctrl/pinconf-generic.c:378
  │     │                 │
  │     │                 │  【锁】pinctrl_maps_mutex → for_each_maps 遍历
  │     │                 └─► add_setting(p, pctldev, map)
  │     │                       │           drivers/pinctrl/core.c:951
  │     │                       │  控制器未注册 → -EPROBE_DEFER (:987)
  │     │                       ├─► pinmux_map_to_setting()   pinmux.c:340
  │     │                       └─► pinconf_map_to_setting()  pinconf.c:107
  │     │
  │     ├─► pinctrl_lookup_state(p, "default")    drivers/pinctrl/core.c:1213
  │     │     （先找 "init"，没有才用 "default"；见 base/pinctrl.c:47-57）
  │     │
  │     └─► pinctrl_select_state(p, state)        drivers/pinctrl/core.c:1349
  │           └─► pinctrl_commit_state()          drivers/pinctrl/core.c:1247
  │                 │
  │                 ├─ 阶段 1：拆旧 state 的每条 setting
  │                 │     └─► pinmux_disable_setting()   pinmux.c:486
  │                 │           └─► pin_free()            pinmux.c:207
  │                 │
  │                 └─ 阶段 2：装新 state 的每条 setting
  │                       ├─► pinmux_enable_setting()     pinmux.c:405
  │                       │     ├─► pin_request() 逐 pin   pinmux.c:108  ← 【仲裁点】
  │                       │     │     └─► pmxops->request / gpio_request_enable
  │                       │     ├─  desc->mux_setting = &setting->data.mux   (:461)
  │                       │     └─► pmxops->set_mux(pctldev, func, group)   (:464)
  │                       │           │
  │                       │           ▼  控制器驱动，真正碰硬件
  │                       │           例：msm_pinmux_set_mux
  │                       │               drivers/pinctrl/qcom/pinctrl-msm.c
  │                       │               → writel() 写 TLMM MUX 寄存器
  │                       │
  │                       └─► pinconf_apply_setting()      pinconf.c:148
  │                             └─► confops->pin_config_set / pin_config_group_set
  │                                   例：msm_config_group_set
  │                                       drivers/pinctrl/qcom/pinctrl-msm.c:521 的 ops 表
  │                                       → raw_spin_lock + writel 写 PAD 配置寄存器
  │
  ├─► drv->probe(dev)          ← 消费者驱动这才开始跑，引脚已经就位
  │
  └─► pinctrl_init_done(dev)                      drivers/base/dd.c:684
        │                                          drivers/pinctrl/core.c:1538
        └─ 若刚才选的是 init 状态且存在 default → 现在切到 default
```

这条路径上的三件事值得单独记住：

1. **锁在哪层加**：三把全局 mutex 全部在核心层（`core.c` / `devicetree.c`），控制器驱动侧自己再加 spinlock 保护寄存器（如 `pinctrl-msm.c:1484` 的 `raw_spin_lock_init`）。核心不替驱动加寄存器锁
2. **重试在哪层做**：pinctrl 自己**不重试**，一律把 `-EPROBE_DEFER` 抛给设备核心，由 deferred probe 机制重来
3. **tracepoint 在哪层埋**：**pinctrl 子系统没有任何 tracepoint**。可观测性全靠 debugfs + `dev_dbg`。这是它与 i2c/spi 最大的调试差异，详见 `07-debug.md` §1

---

## §7 与内核其它子系统的关系

| 子系统 | 关系 | 代码落点 |
|---|---|---|
| **driver model** | 设备核心在 probe 前后各调一次 pinctrl；`struct device` 里有 `pins` 字段 | `drivers/base/dd.c:616`/`:684`；`include/linux/device.h:586` |
| **platform 总线** | 所有 pin controller 都是 `platform_device`（arm64 上来自 DT）；pinctrl 自己**没有** `bus_type` | 见 `02-datastructures.md` §2 |
| **OF / devicetree** | `pinctrl-N` / `pinctrl-names` / `#pinctrl-cells` / `pinctrl-use-default` 由核心直接读 | `drivers/pinctrl/devicetree.c` 全文 |
| **gpiolib** | 双向：pinctrl 核心 `#include "../gpio/gpiolib.h"`（`core.c:30`，仅 `CONFIG_GPIOLIB`）；gpiolib 驱动回调 `pinctrl_gpio_*` | `core.c:749-920` |
| **PM / PM runtime** | `pinctrl_pm_select_{default,sleep,idle}_state()`，仅 `CONFIG_PM` | `core.c:1590-1627` |
| **devres** | 全套 `devm_` 变体；`devm_pinctrl_register()` 是驱动的主流写法（74 个文件） | `core.c:2234-2331` |
| **debugfs** | 全局 `pinctrl/` 目录 + 每控制器一个子目录 | `core.c:1904-1957` |
| **ftrace / tracepoint** | **无**。pinctrl 一个 tracepoint 都没定义 | 见 `07-debug.md` §1 |
| **regmap** | 正交；部分厂商驱动用 regmap 访问 syscon 里的 mux 寄存器 | 驱动侧，核心不涉及 |
| **PREEMPT_RT** | 本基线打了 RT 补丁，但 `drivers/pinctrl` 与 `include/linux/pinctrl` **一行未改** | 见 `99-evidence.md` §1.2 |
| **ACPI** | 本文档集**不覆盖**。pinctrl 的 ACPI 路径主要服务 x86（Intel/AMD 驱动），arm64 上不走 | 裁剪声明见 `99-evidence.md` §2.7 |

---

## §8 本文档集导读

| 篇 | 定位 |
|---|---|
| `00-overview.md`（本篇） | 复习入口：概念 + 数据结构速览 + 机制速览 + 端到端路径 |
| `01-api.md` | 代码地图（文件 / 头文件 / Kconfig）+ 全部对外 API + 错误码语义 |
| `02-datastructures.md` | 先破歧义（4 组易错关系），再画结构关系图、数据流图、全局变量与锁 |
| `03-internals.md` | Part A 时间线（注册→解析→切换→注销）/ Part B 横切（锁、defer、hog、GPIO range）/ Part C 评价与设计债 |
| `04-extensions.md` | 核心自带的四个可选层：pinconf-generic、泛型 group/function 存储、pinctrl-utils、GPIO 桥接 |
| `05-usage.md` | 三个角色的代码骨架：pin controller 驱动 / 消费者驱动 / gpiolib 驱动 + 近邻符号区分 |
| `06-devicetree.md` | 发现路径分诊表 + 每条属性由谁消费 + 配错了怎么定位 |
| `07-debug.md` | 编译期开关 / debugfs 全套 / 错误码分诊 / 为什么没有 tracepoint |
| `08-feynman.md` | 大白话解释 7 个最难的概念（pin 归属权、pinctrl vs GPIO、strict、hog、状态机…） |
| `99-evidence.md` | 证据分层、逐篇取证表、自陈问题、明确未验证项、自检脚本输出 |

**按角色的阅读路径**：

| 你想做什么 | 建议顺序 |
|---|---|
| 只想在 DTS 里让某个外设的引脚工作 | `06` → `00 §2.8` → 出问题看 `07 §5` |
| 要写一个新 SoC 的 pin controller 驱动 | `00 §2.5` → `01 §2`（ops 契约）→ `05 §2` → `04`（能省多少代码）→ `03 Part A` |
| 消费者驱动要做运行时切状态 | `01 §3` → `05 §3` → `03 §A8` |
| 要给 gpiolib 驱动接 pinctrl 后端 | `08 §3/§4` → `01 §4` → `05 §4` → `03 §B4` |
| 要看懂核心怎么实现的 | `02` → `03`（按 Part A → B → C 顺序）|
| 引脚"配了但没生效"要排障 | `07 §5`（症状分诊）→ `06 §5`（DTS 定位）|
| 只是想搞懂概念 | `08` → `00 §2` |
| 要复核本文档集的结论 | `99` |
| 隔几个月回来复习 | 只读本篇 |
