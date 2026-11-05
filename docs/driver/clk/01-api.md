---
title: API 参考
---

# 01 · clk 代码地图与对外 API

> 本篇是「**找文件**」和「**找 API**」的唯一入口。
> 格式约定：API 表固定三列 **API / 说明 / 备注**；签名只写函数名 + 形参名，不写类型；位置只精确到文件，不给行号（行号在 03 和 99 维护）。

---

## §0 代码地图

### 0.1 核心文件（本文档集逐行读过的）

| 文件 | 职责 |
|---|---|
| `drivers/clk/clk.c` | **框架主体**。树拓扑、引用计数、两把锁、频率变更三段式、注册/注销、DT provider 表、debugfs、通知链 |
| `drivers/clk/clk.h` | 核心内部私有原型（`of_clk_get_hw` / `clk_find_hw` / `clk_hw_create_clk` / `__clk_put`）。**不对外暴露** |
| `drivers/clk/clkdev.c` | `clk_get` / `clk_put` 的实际入口 + 非 DT 的静态查找表（`clk_lookup`） |
| `drivers/clk/clk-devres.c` | 所有 `devm_clk_*` 变体的 devres 封装 |
| `drivers/clk/clk-bulk.c` | `clk_bulk_*` 批量操作 |
| `drivers/clk/clk-conf.c` | 消费 DT 里的 `assigned-clocks` / `assigned-clock-parents` / `assigned-clock-rates` |

### 0.2 八个基本时钟类型（不用自己写驱动就能拼出大部分 SoC）

| 文件 | 结构 | 提供的 ops |
|---|---|---|
| `drivers/clk/clk-fixed-rate.c` | `clk_fixed_rate` | `recalc_rate` / `recalc_accuracy` |
| `drivers/clk/clk-fixed-factor.c` | `clk_fixed_factor` | 固定 M/N 倍频分频 |
| `drivers/clk/clk-gate.c` | `clk_gate` | `enable` / `disable` / `is_enabled` |
| `drivers/clk/clk-divider.c` | `clk_divider` | `recalc_rate` / `round_rate` / `determine_rate` / `set_rate` |
| `drivers/clk/clk-mux.c` | `clk_mux` | `get_parent` / `set_parent` / `determine_rate` |
| `drivers/clk/clk-multiplier.c` | `clk_multiplier` | 倍频器 |
| `drivers/clk/clk-fractional-divider.c` | `clk_fractional_divider` | 小数分频（M/N） |
| `drivers/clk/clk-composite.c` | `clk_composite` | **把上面几个组合成一个节点**（mux + divider + gate） |

`clk-composite.c` 是 arm64 厂商驱动最常用的一块：一条外设时钟支路在硬件上往往同时有选择位、分频位、使能位，用 composite 一次注册成一个 `clk_core`，消费者只看到一个时钟。

### 0.3 独立设备型时钟（不是 SoC 片内）

| 文件 | 说明 |
|---|---|
| `drivers/clk/clk-gpio.c` | 用一根 GPIO 当门控/选择器 |
| `drivers/clk/clk-scmi.c` | 时钟由 firmware（SCMI 协议）管理，**arm64 服务器/新 SoC 常见** |
| `drivers/clk/clk-scpi.c` | 同上，SCPI 协议（较旧） |
| `drivers/clk/clk-si5341.c`、`clk-si5351.c`、`clk-cdce925.c`、`clk-versaclock5.c` 等 | I²C/SPI 挂的时钟发生器芯片。这类节点的操作**会睡眠** |

### 0.4 厂商目录（只讲契约，不逐行读）

`drivers/clk/` 下有 48 个厂商子目录。arm64 上会实际碰到的主要是：

`imx/`（NXP i.MX8）、`qcom/`（高通）、`mediatek/`、`rockchip/`、`sunxi-ng/`（全志）、`renesas/`、`samsung/`（Exynos）、`meson/`（Amlogic）、`hisilicon/`、`tegra/`（NVIDIA）、`bcm/`（Broadcom/树莓派）、`sprd/`、`visconti/`、`starfive/`、`zynqmp/`、`xilinx/`、`microchip/`、`uniphier/`、`actions/`、`mvebu/`、`baikal-t1/`、`bitmain/`（`clk-bm1880.c` 在顶层）。

代表驱动的选法登记在 99 §2.2。

### 0.5 头文件

| 头文件 | 给谁 | 内容 |
|---|---|---|
| `include/linux/clk.h` | **消费者** | `clk_get` / `clk_prepare_enable` / `clk_set_rate` / 通知 / bulk / devm 变体 |
| `include/linux/clk-provider.h` | **提供方** | `clk_ops`、`clk_hw`、`clk_init_data`、注册函数、8 个基本类型、DT provider 注册 |
| `include/linux/clkdev.h` | 平台代码 | `clkdev_create` / `clk_register_clkdev` 静态查找表 |
| `include/linux/of_clk.h` | 早期初始化 | `of_clk_init` / `of_clk_get_parent_count` / `of_clk_get_parent_name` |
| `include/linux/clk/clk-conf.h` | 核心内部 | `of_clk_set_defaults` |
| `include/linux/clk/*.h`（15 个） | 特定厂商 | `imx.h` / `renesas.h` / `samsung.h` / `tegra.h` / `at91_pmc.h` 等，**不是通用 API** |
| `include/trace/events/clk.h` | 调试 | tracepoint 定义（07 §2） |
| `include/dt-bindings/clock/*.h` | DT + 驱动共用 | 每个 SoC 的时钟 ID 常量 |

**`include/linux/clk.h` 和 `clk-provider.h` 的边界是硬的**：消费者驱动只 include 前者，就算写错也编不过（`struct clk_core` 根本不在头文件里）。反过来提供方驱动要 include 后者，而后者不自动 include 前者。

### 0.6 Kconfig

| 符号 | 位置 | 说明 |
|---|---|---|
| `HAVE_CLK` | `drivers/clk/Kconfig:3` | 「`<linux/clk.h>` 的调用是可用的」。**没有菜单项**，由平台 select |
| `HAVE_CLK_PREPARE` | `drivers/clk/Kconfig:9` | prepare/unprepare 可用 |
| `HAVE_LEGACY_CLK` | `drivers/clk/Kconfig:12` | 旧的、平台自己实现 `struct clk` 的方式。**已废弃**，帮助文本里写着 TODO: Remove |
| `COMMON_CLK` | `drivers/clk/Kconfig:21` | **CCF 主开关**。`depends on !HAVE_LEGACY_CLK`，select `HAVE_CLK_PREPARE`/`HAVE_CLK`/`SRCU`/`RATIONAL` |
| `CLK_KUNIT_TEST` | `drivers/clk/Kconfig:476` | `clk_test.c`（2411 行）的单元测试 |
| `CLK_GATE_KUNIT_TEST` | `drivers/clk/Kconfig:483` | `clk-gate_test.c` |
| `COMMON_CLK_SCMI` | `drivers/clk/Kconfig:102` | firmware 管理的时钟 |
| `COMMON_CLK_SCPI` | `drivers/clk/Kconfig:112` | 同上，旧协议 |
| `CONFIG_DEBUG_FS` | 内核通用 | 控制 `/sys/kernel/debug/clk/`（`drivers/clk/clk.c:3191` 起整段） |
| `CONFIG_OF` | 内核通用 | 控制 `clk-conf.o` 是否编译（`drivers/clk/Makefile:16-18`） |

**两个编译边界很容易看错**（07 §1.1 会再确认一次）：

1. `clkdev.o` / `clk-devres.o` / `clk-bulk.o` 挂在 **`CONFIG_HAVE_CLK`** 下（`drivers/clk/Makefile:3`），而 `clk.o` 挂在 `CONFIG_COMMON_CLK` 下（`:4`）。也就是说 `clk_get` / `devm_clk_get` / `clk_bulk_get` 这三族函数在只有 legacy clk 的平台上也存在。
2. arm64 defconfig 里 `COMMON_CLK=y`，所以**本文档集之后一律假定 `CONFIG_COMMON_CLK=y`**。`clk.h` 里那一大堆 `#else` 分支的空实现（返回 `NULL` 或 `-EOPNOTSUPP`）在 arm64 上不生效——但 01 §4 仍然登记了它们的返回值，因为**这是"某个 API 悄悄什么都没做"这类 bug 的常见来源**。

### 0.7 Documentation

| 文件 | 行数 | 本文档集怎么用 |
|---|---|---|
| `Documentation/driver-api/clk.rst` | 307 | **唯一的框架级官方文档**。设计意图、必选回调矩阵、锁的规则、`clk_ignore_unused`。有 3 处已落后于 6.1 代码，逐条列在 99 §6 |
| `Documentation/devicetree/bindings/clock/` | 223 个 `.yaml` + 91 个 `.txt` | 06 的素材。注意 `.txt` 还占 29%，说明 clock 绑定的 yaml 化没做完 |
| `Documentation/devicetree/bindings/clock/clock-bindings.txt` | 2 | **别指望它**：本树里这个文件只剩 2 行，内容是一条指向树外 `dt-schema` 仓库的 URL。也就是说 `clocks` / `clock-names` / `#clock-cells` / `assigned-clocks` 的**通用**定义在 6.1 树里**根本不存在**。06 §1.1 那张属性表是从 `drivers/clk/` 的消费代码反查出来的，用来补这个洞 |

---

## §1 消费者 API

**头文件：`include/linux/clk.h`。实现：`drivers/clk/clk.c`（大部分）、`drivers/clk/clkdev.c`（`clk_get`/`clk_put`）、`drivers/clk/clk-devres.c`（`devm_*`）、`drivers/clk/clk-bulk.c`（`clk_bulk_*`）。**

### 1.1 获取与释放

| API | 说明 | 备注 |
|---|---|---|
| `clk_get(dev, id)` | 按名字拿一个时钟句柄 | **DT 优先、clkdev 兜底**（实现在 `clkdev.c`）。`id` 对应 DT 的 `clock-names` 里的一项；`dev` 为 NULL 时只走 clkdev。失败返回 `ERR_PTR` |
| `clk_get_optional(dev, id)` | 同上，但"没有这个时钟"不算错 | `static inline`。找不到时返回 **NULL 而不是 `ERR_PTR(-ENOENT)`**，其余错误照常返回。后续所有 API 都接受 NULL 并安全地什么都不做 |
| `clk_put(clk)` | 释放句柄 | 接受 NULL。释放的是 `struct clk`，`clk_core` 由 `kref` 管 |
| `clk_get_sys(dev_id, con_id)` | **不经过 `struct device`**，直接按两个字符串在 clkdev 表里查 | 给没有 device 的早期代码用。arm64 上几乎不用 |
| `of_clk_get(np, index)` | 按 DT 索引取 | 头文件同上，实现在 `clk.c`。要自己数 `clocks` 里第几个 |
| `of_clk_get_by_name(np, name)` | 按 DT 的 `clock-names` 取 | 比 `of_clk_get` 好用；`clk_get` 内部走的就是同一套解析 |
| `of_clk_get_from_provider(clkspec)` | 已经解析好 phandle args 了，直接换句柄 | 给自己解析 DT 的代码用 |
| `devm_get_clk_from_child(dev, np, con_id)` | 从**子节点**取时钟，但绑到父 device 的 devres 上 | 少见。典型是一个 device 有多个子节点各自声明时钟 |

### 1.2 devm 变体（推荐默认用法）

| API | 说明 | 备注 |
|---|---|---|
| `devm_clk_get(dev, id)` | `clk_get` + 自动 `clk_put` | 实现在 `clk-devres.c` |
| `devm_clk_get_optional(dev, id)` | 加上"可选"语义 | 找不到返回 NULL |
| `devm_clk_get_prepared(dev, id)` | get + **prepare**，自动 unprepare + put | 6.x 新增的省事写法 |
| `devm_clk_get_enabled(dev, id)` | get + **prepare + enable**，自动反向收尾 | **这是新驱动的首选**：一行替代 get/prepare_enable/错误路径/remove 四段代码 |
| `devm_clk_get_optional_prepared(dev, id)` | 可选 + prepared | |
| `devm_clk_get_optional_enabled(dev, id)` | 可选 + enabled | |
| `devm_clk_put(dev, clk)` | 提前释放一个 devm 管的句柄 | 极少用；用了说明生命周期设计有问题 |
| `devm_clk_notifier_register(dev, clk, nb)` | 通知块的 devm 版 | |

### 1.3 prepare / enable

| API | 说明 | 备注 |
|---|---|---|
| `clk_prepare(clk)` | 做"可能睡眠"的准备工作 | **不能在原子上下文调用**。递归 prepare 所有上游 |
| `clk_unprepare(clk)` | 反向 | 无返回值 |
| `clk_enable(clk)` | 做"绝不睡眠"的使能 | **允许原子上下文**。若未 prepare，返回 **`-ESHUTDOWN`** 并 `WARN` |
| `clk_disable(clk)` | 反向 | 无返回值。计数已为 0 时 `WARN`；对 `CLK_IS_CRITICAL` 的最后一次 disable 也 `WARN` 并拒绝 |
| `clk_prepare_enable(clk)` | 两者的组合 | `static inline`，在 `clk.h` 里。**日常唯一该用的** |
| `clk_disable_unprepare(clk)` | 反向组合 | `static inline` |
| `clk_is_enabled_when_prepared(clk)` | 「这个时钟 prepare 完就已经在跑了吗」 | 返回 bool。判据非常直白：**节点没有 `.enable`/`.disable` 回调**（`drivers/clk/clk.c:1359-1362`）——那 `clk_enable` 就是空操作，真正的开关全在 prepare 里。主要给 PM 代码用：它据此知道"想真关掉必须 unprepare"。但不管返回什么，**调用者仍然必须照常配对调 enable/disable**，否则计数会错（kerneldoc `drivers/clk/clk.c:1355-1357`） |

**`clk_prepare` 有一个不显眼的副作用**：如果节点带 `CLK_SET_RATE_GATE`，prepare 成功后核心会自动给它加上**频率保护**（相当于提供方代替消费者拿了独占权）。所以这类节点一旦 prepare，任何人的 `clk_set_rate` 都会拿到 `-EBUSY`——想改频率必须先全部 unprepare。设计意图见 `drivers/clk/clk.c:1072-1080` 的注释。

### 1.4 频率

| API | 说明 | 备注 |
|---|---|---|
| `clk_get_rate(clk)` | 读当前频率（Hz） | **返回缓存值**，除非节点带 `CLK_GET_RATE_NOCACHE`。clk 为 NULL 时返回 0 |
| `clk_round_rate(clk, rate)` | 「如果我请求这个频率，你实际会给多少」**只问不做** | 返回 long。它会走完整的 determine_rate 链，**包括上游**，所以比自己算靠谱 |
| `clk_set_rate(clk, rate)` | 提交频率变更 | 三段式（算/通知/提交）。**不保证拿到精确值**，要验就 `clk_get_rate` 再读一次 |
| `clk_set_rate_exclusive(clk, rate)` | 设频率并**同时取得独占权** | 相当于 `clk_rate_exclusive_get` + `clk_set_rate`，但是原子的 |
| `clk_set_rate_range(clk, min, max)` | 声明「我只能接受这个区间」 | 是 **per-consumer** 约束：多个消费者的区间会求交集。区间冲突返回 `-EINVAL` |
| `clk_set_min_rate(clk, rate)` | 只设下界 | 上界保持不变 |
| `clk_set_max_rate(clk, rate)` | 只设上界 | |
| `clk_drop_range(clk)` | 撤销自己的区间约束 | `static inline`，等价于 `clk_set_rate_range(clk, 0, ULONG_MAX)` |
| `clk_rate_exclusive_get(clk)` | 取得独占权：期间别人不能改这个时钟的频率 | 引用计数式，可嵌套。**必须配对 put** |
| `clk_rate_exclusive_put(clk)` | 释放独占权 | 无返回值 |

**`clk_set_rate` 成功 ≠ 拿到你要的频率。** 硬件只能输出离散集合（00 §2.2）。想知道会得到什么，先 `clk_round_rate`。想强制不接受偏差，用 `clk_set_rate_range` 把区间收窄——那样拿不到就会报错而不是静默凑近。

### 1.5 父节点

| API | 说明 | 备注 |
|---|---|---|
| `clk_get_parent(clk)` | 拿当前父节点的句柄 | 返回的是**父节点的 `clk`**，不需要（也不应该）`clk_put` |
| `clk_set_parent(clk, parent)` | 切换父节点 | 只有实现了 `.set_parent` 的 mux 型节点能成功，否则 `-ENOSYS`。带 `CLK_SET_PARENT_GATE` 的节点在 enable 状态下切换会 `-EBUSY` |
| `clk_has_parent(clk, parent)` | 「这个节点的候选父列表里有它吗」 | bool。切换前先问，避免白试 |
| `clk_is_match(p, q)` | 「这两个句柄是不是同一个时钟」 | **不能用 `p == q` 比较**！两次 `clk_get` 同一个时钟得到两个不同的 `struct clk`。这个函数比的是底下的 `clk_core` |

### 1.6 通知

| API | 说明 | 备注 |
|---|---|---|
| `clk_notifier_register(clk, nb)` | 注册频率变更通知 | 收到三种消息：`PRE_RATE_CHANGE` / `POST_RATE_CHANGE` / `ABORT_RATE_CHANGE`（宏在 `clk.h`） |
| `clk_notifier_unregister(clk, nb)` | 注销 | |
| `devm_clk_notifier_register(dev, clk, nb)` | devm 版 | |

回调参数是 `struct clk_notifier_data`，带 `old_rate` / `new_rate`。**在 `PRE_RATE_CHANGE` 里返回 `NOTIFY_BAD` 可以否决整次变更**——这是消费者阻止"频率被改坏"的唯一手段。回调在持 `prepare_lock` 的上下文里跑，**不能在里面调 `clk_set_rate`**（会自己等自己，尽管锁可重入，但语义上是递归改频率）。

### 1.7 相位、占空比、精度

| API | 说明 | 备注 |
|---|---|---|
| `clk_set_phase(clk, degrees)` | 设相位，0–359 度 | 硬件不支持返回 `-EINVAL`（无 `.set_phase` 时） |
| `clk_get_phase(clk)` | 读相位 | 返回 0–359 或负错误码 |
| `clk_set_duty_cycle(clk, num, den)` | 设占空比，分数形式 | 要求 `den > 0 && den >= num` |
| `clk_get_scaled_duty_cycle(clk, scale)` | 读占空比，按 `scale` 缩放后的整数 | 传 100 就得到百分比 |
| `clk_get_accuracy(clk)` | 读精度，ppb | 返回 long。默认继承父节点 |

### 1.8 批量操作（bulk）

**实现：`drivers/clk/clk-bulk.c`。** 一个设备要 3 个以上时钟时用它，能少写一大段错误回滚。

| API | 说明 | 备注 |
|---|---|---|
| `clk_bulk_get(dev, num_clks, clks)` | 按调用者填好的 `clk_bulk_data[].id` 逐个 get | `__must_check`。任一失败会把已拿到的全部释放 |
| `clk_bulk_get_optional(dev, num_clks, clks)` | 缺失的置 NULL，不算错 | |
| `clk_bulk_get_all(dev, clks)` | **不用自己列名字**，把 DT 里 `clocks` 的全部拿来 | 出参是 `clk_bulk_data **`（函数自己分配数组），返回值是**个数**而不是 0。配对 `clk_bulk_put_all` |
| `clk_bulk_put(num_clks, clks)` | 释放 | |
| `clk_bulk_put_all(num_clks, clks)` | 释放 `_get_all` 拿到的（连数组一起） | |
| `clk_bulk_prepare(num_clks, clks)` | 批量 prepare | `__must_check` |
| `clk_bulk_unprepare(num_clks, clks)` | 反向 | |
| `clk_bulk_enable(num_clks, clks)` | 批量 enable | `__must_check` |
| `clk_bulk_disable(num_clks, clks)` | 反向 | |
| `clk_bulk_prepare_enable(num_clks, clks)` | 组合 | `static inline`（在 `clk.h`） |
| `clk_bulk_disable_unprepare(num_clks, clks)` | 反向组合 | `static inline` |
| `devm_clk_bulk_get(dev, num_clks, clks)` | devm 版 | |
| `devm_clk_bulk_get_optional(dev, num_clks, clks)` | | |
| `devm_clk_bulk_get_all(dev, clks)` | **最省事的写法**：一行拿到设备的全部时钟 | 返回个数 |

**`clk_bulk_*` 的顺序语义**：get/prepare/enable 按数组顺序正向，put/unprepare/disable 按**反向**。如果时钟之间有开启顺序依赖，把它们按依赖顺序排进数组就够了。

### 1.9 上下文保存/恢复

| API | 说明 | 备注 |
|---|---|---|
| `clk_save_context()` | 遍历全树调 `.save_context` | **无参数、全局**。给深度掉电（suspend-to-RAM / 断电域）前用 |
| `clk_restore_context()` | 反向 | 无返回值 |

这两个是**平台级**接口，不是驱动级——由 SoC 的 PM 代码调，普通驱动不要碰。绝大多数节点没有实现这两个回调，所以它们对那些节点是空操作；`clk_gate_restore_context()`（在 `clk-provider.h`）给 gate 型节点提供了一个通用实现。

---

## §2 提供方 API

**头文件：`include/linux/clk-provider.h`。实现：`drivers/clk/clk.c` + 八个基本类型各自的 `drivers/clk/clk-*.c`。**

本节只登记**函数/宏**。`struct clk_ops` 的 25 个回调、`struct clk_init_data` 的字段、`CLK_*` 标志的语义在 02 §3–§5；回调的调用时机在 03 Part A。

### 2.1 通用注册（写自定义 `clk_ops` 时用）

| API | 说明 | 备注 |
|---|---|---|
| `clk_hw_register(dev, hw)` | **注册一个时钟节点**。这是唯一该用的通用注册入口 | `__must_check`，返回 0 或负错误码。`dev` 可为 NULL（早期 of_clk_init 阶段）。注册前必须填好 `hw->init`（指向 `clk_init_data`）；注册**成功后 `hw->init` 会被核心清空**，不要再读它 |
| `devm_clk_hw_register(dev, hw)` | devm 版，`dev` 不能为 NULL | `__must_check` |
| `of_clk_hw_register(node, hw)` | 只有 `device_node` 没有 `device` 时用 | `__must_check`。early clock（`CLK_OF_DECLARE`）路径 |
| `clk_hw_unregister(hw)` | 注销 | 注销不等于释放：`clk_core` 由 `kref` 管，还有消费者持有句柄时会**保留一个孤儿壳**，此后所有操作变成空操作（防止消费者崩） |
| `clk_register(dev, hw)` | **已废弃**的注册接口，返回 `struct clk *` | 官方文档 `Documentation/driver-api/clk.rst:248` 还在推荐它——那段已过时，逐条记在 99 §6.1。新代码一律用 `clk_hw_register` |
| `devm_clk_register(dev, hw)` | 同上，devm 版，同样已废弃 | |
| `clk_unregister(clk)` | 配对 `clk_register` | |

**为什么废弃 `clk_register`**：它返回一张**消费者句柄**给提供方，提供方于是能对自己的时钟做 `clk_set_rate` 之类的消费者操作，绕过了两层分离（00 §3）。`clk_hw_register` 只返回错误码，提供方手里永远只有 `clk_hw`。真需要句柄时用下面的 `clk_hw_get_clk`。

### 2.2 八个基本类型的注册

统一命名规律（记住这条就不用背 60 多个宏）：

```
[devm_] clk_hw_register_<类型> [_table] [_parent_hw | _parent_data] [_with_accuracy]
   │                  │            │              │                       │
   │                  │            │              │                       └ 额外传精度（只有 fixed_rate 有）
   │                  │            │              └ 父节点怎么指定：省略=父名字符串
   │                  │            │                 _parent_hw=直接给 clk_hw 指针（同一驱动内，最快）
   │                  │            │                 _parent_data=给 clk_parent_data 数组（可混合名字/DT索引/hw）
   │                  │            └ 分频/选择值不是"寄存器值就是除数"，而要查表
   │                  └ 类型：fixed_rate / gate / divider / mux / fixed_factor /
   │                          fractional_divider / composite（multiplier 用 clk_hw_register 通用路径）
   └ devm_ 前缀：自动 unregister（不是所有组合都有 devm 版）
```

| API | 说明 | 备注 |
|---|---|---|
| `clk_hw_register_fixed_rate(dev, name, parent_name, flags, fixed_rate)` | 注册一个固定频率源（晶振、外部时钟输入） | 宏，展开到 `__clk_hw_register_fixed_rate`。变体最多：`_parent_hw` / `_parent_data` / `_with_accuracy` / `_parent_accuracy` 及其组合，共 8 个 |
| `of_fixed_clk_setup(np)` | 从 DT 的 `fixed-clock` 节点直接建一个 | 给 `CLK_OF_DECLARE` 用 |
| `clk_hw_register_gate(dev, name, parent_name, flags, reg, bit_idx, clk_gate_flags, lock)` | 注册门控 | `_parent_hw` / `_parent_data` / `devm_` 变体。`lock` 是**调用者提供的 spinlock**，同一寄存器上的多个 gate 必须共用一把 |
| `clk_gate_is_enabled(hw)` | 通用 gate 的 `.is_enabled` 实现 | 提供方自己写 ops 时可以复用 |
| `clk_hw_register_divider(dev, name, parent_name, flags, reg, shift, width, clk_divider_flags, lock)` | 注册分频器 | `_table` / `_parent_hw` / `_parent_data` / `devm_` 共 9 个变体 |
| `clk_hw_register_mux(dev, name, parent_names, num_parents, flags, reg, shift, width, clk_mux_flags, lock)` | 注册选择器 | `_table` / `_hws` / `_parent_data` / `_parent_data_table` / `devm_` 共 10 个变体 |
| `clk_mux_val_to_index(hw, table, flags, val)` / `clk_mux_index_to_val(table, flags, index)` | 索引与寄存器值互转 | 自己写 mux ops 时复用 |
| `clk_hw_register_fixed_factor(dev, name, parent_name, flags, mult, div)` | 固定 M/N | `devm_` / `_index` / `_parent_hw` 变体 |
| `clk_hw_register_fractional_divider(...)` | 小数分频 | 只有非 devm 版 |
| `clk_hw_register_composite(dev, name, parent_names, num_parents, mux_hw, mux_ops, rate_hw, rate_ops, gate_hw, gate_ops, flags)` | **把 mux/rate/gate 三段拼成一个节点** | `_pdata` 变体接 `clk_parent_data`；`devm_clk_hw_register_composite_pdata`。三对 hw/ops 都可以为 NULL |
| `clk_hw_unregister_<类型>(hw)` | 每个类型都有配对的注销 | `fixed_rate` / `gate` / `divider` / `mux` / `fixed_factor` / `fractional_divider` / `composite` |

**`lock` 参数的坑**：`clk_gate` / `clk_divider` / `clk_mux` 都要求调用者传一个 `spinlock_t *`。**同一个寄存器上的所有节点必须传同一把锁**，否则读-改-写会互相踩。厂商驱动一般在 probe 里定义一个全局 `static DEFINE_SPINLOCK(xxx_clk_lock)` 全部共用。

### 2.3 提供方查询自己的节点

| API | 说明 | 备注 |
|---|---|---|
| `clk_hw_get_name(hw)` | 读节点名 | |
| `clk_hw_get_rate(hw)` | 读当前频率 | 读的是缓存 |
| `clk_hw_get_flags(hw)` | 读 `CLK_*` 标志 | |
| `clk_hw_get_num_parents(hw)` | 候选父节点个数 | |
| `clk_hw_get_parent(hw)` | 当前父节点的 `clk_hw` | 孤儿节点返回 NULL |
| `clk_hw_get_parent_by_index(hw, index)` | 按索引取候选父 | 该父还没注册时返回 NULL，**不是错误**——正常的注册顺序问题 |
| `clk_hw_get_parent_index(hw)` | 当前父是第几个候选 | 返回 index 或负错误码 |
| `clk_hw_set_parent(hw, new_parent)` | 提供方侧切父 | 给 ops 内部用 |
| `clk_hw_reparent(hw, new_parent)` | **只改拓扑记账**，不碰寄存器 | 自己实现 `.set_parent` 后必须调它把核心的 parent 指针同步过来 |
| `clk_hw_is_prepared(hw)` / `clk_hw_is_enabled(hw)` | 读状态 | bool |
| `clk_hw_rate_is_protected(hw)` | 「有人拿了独占权吗」 | bool |
| `clk_hw_get_rate_range(hw, min_rate, max_rate)` | 读出所有消费者约束求交后的区间 | 出参 |
| `clk_hw_set_rate_range(hw, min_rate, max_rate)` | 提供方声明硬件本身的能力区间 | 和消费者的 `clk_set_rate_range` 不同层：这是硬件上限，消费者约束只能在它之内 |
| `clk_hw_round_rate(hw, rate)` | 提供方侧的 round | |
| `__clk_get_name(clk)` / `__clk_get_hw(clk)` / `__clk_get_enable_count(clk)` / `__clk_is_enabled(clk)` / `__clk_lookup(name)` | 双下划线前缀 = **半内部**接口 | 只有少数厂商/PM 代码用。`__clk_lookup` 按名字全局查，绕过 DT，是遗留接口 |
| `clk_hw_get_clk(hw, con_id)` | 提供方**主动给自己造一个消费者句柄** | 用于提供方需要对自己的时钟做消费者操作的少数场景 |
| `devm_clk_hw_get_clk(dev, hw, con_id)` | devm 版 | |
| `__clk_hw_set_clk(dst, src)` | 把 `src` 的 `clk_core`/`clk` 指针复制给 `dst` | `static inline`。composite 内部把子 hw 指到同一个 core 时用 |

### 2.4 determine_rate 的可复用实现

写 mux 型节点时**不要自己算**，用这些：

| API | 说明 | 备注 |
|---|---|---|
| `__clk_mux_determine_rate(hw, req)` | 遍历所有候选父，选出「**不超过**请求频率的最大值」 | 最常用。放进 `.determine_rate` 即可 |
| `__clk_mux_determine_rate_closest(hw, req)` | 选出「**最接近**请求频率」的（可以超过） | 用于对偏差方向不敏感的场景 |
| `clk_mux_determine_rate_flags(hw, req, flags)` | 上面两个的公共实现，可传 `CLK_MUX_ROUND_CLOSEST` | |
| `__clk_determine_rate(core, req)` | 对任意节点跑一次 determine 链 | 半内部 |
| `divider_determine_rate(hw, req, table, width, flags)` | 分频器的通用 determine | |
| `divider_ro_determine_rate(...)` | 只读分频器（分频比锁死）版本 | `_ro_` = read-only |
| `divider_round_rate_parent(hw, parent, rate, prate, table, width, flags)` | 允许**连带改父频率**的 round | |
| `divider_ro_round_rate_parent(...)` | 只读版 | |
| `divider_round_rate(hw, rate, prate, table, width, flags)` / `divider_ro_round_rate(...)` | `static inline` 简化版（父固定为当前父） | |
| `divider_recalc_rate(hw, parent_rate, val, table, flags, width)` | 由寄存器值算出频率 | 实现 `.recalc_rate` 时复用 |
| `divider_get_val(rate, parent_rate, table, width, flags)` | 由目标频率算出该写的寄存器值 | 实现 `.set_rate` 时复用 |
| `clk_hw_init_rate_request(hw, req, rate)` | 初始化一个 `clk_rate_request` | 自己发起 determine 时用 |
| `clk_hw_forward_rate_request(core, parent_req, parent, req, parent_rate)` | 把请求**往上游转发** | 实现"改自己不够、要连父一起改"时用 |

**`determine_rate` 与 `round_rate` 只需实现一个**（02 §4 会展开）。新代码写 `determine_rate`：它能返回"要改哪个父"和"父该是多少"，`round_rate` 只能返回一个数字。

### 2.5 DT provider 注册

| API | 说明 | 备注 |
|---|---|---|
| `of_clk_add_hw_provider(np, get, data)` | 把一个 `device_node` 登记成时钟提供者 | `get` 回调把 `of_phandle_args` 翻成 `clk_hw *` |
| `devm_of_clk_add_hw_provider(dev, get, data)` | devm 版。**这是 platform 驱动的标准写法** | 用 `dev->of_node` |
| `of_clk_del_provider(np)` | 注销 | |
| `of_clk_add_provider(np, get, data)` | 老接口，`get` 返回 `struct clk *` | 与 `clk_register` 一起废弃 |
| `of_clk_hw_onecell_get(clkspec, data)` | **最常用的 `get` 实现**：`data` 是 `clk_hw_onecell_data`（一个 `clk_hw *` 数组 + 个数），按 `clkspec->args[0]` 直接下标 | 配合 `#clock-cells = <1>` |
| `of_clk_hw_simple_get(clkspec, data)` | 只有一个时钟时用，忽略 args | 配合 `#clock-cells = <0>` |
| `of_clk_src_onecell_get` / `of_clk_src_simple_get` | 上面两个的 `struct clk *` 老版本 | |
| `of_clk_parent_fill(np, parents, size)` | 把 DT 里 `clocks` 引用的时钟名填进数组 | 返回填了几个 |
| `of_clk_get_parent_count(np)` | 数 `clocks` 有几项 | 声明在 `include/linux/of_clk.h` |
| `of_clk_get_parent_name(np, index)` | 取第 index 个父时钟的名字 | 同上 |
| `of_clk_detect_critical(np, index, flags)` | 读 DT 的 `clock-critical` 位图，给对应节点加 `CLK_IS_CRITICAL` | 这是 DT **唯一**能标"不许自动关"的手段 |
| `of_clk_init(matches)` | 早期批量初始化：扫全 DT，按 `CLK_OF_DECLARE` 表建时钟 | 声明在 `include/linux/of_clk.h`。**在 platform 总线可用之前**跑，用于 timer/中断控制器依赖的时钟 |

**两条注册路径的分工**（03 Part A 展开）：`CLK_OF_DECLARE` + `of_clk_init` 走在 `init_IRQ` 附近，只给"启动早期就要用"的时钟；绝大多数 SoC 时钟走正常的 `platform_driver` + `devm_of_clk_add_hw_provider`。

### 2.6 clkdev 静态查找表（非 DT）

**头文件：`include/linux/clkdev.h`。实现：`drivers/clk/clkdev.c`。**

| API | 说明 | 备注 |
|---|---|---|
| `clkdev_create(clk, con_id, dev_fmt, ...)` | 建一条「(设备名, 连接名) → clk」的映射 | `dev_fmt` 是 printf 格式，可以带通配 |
| `clkdev_hw_create(hw, con_id, dev_fmt, ...)` | 同上，从 `clk_hw` 出发 | |
| `clkdev_add(cl)` / `clkdev_drop(cl)` | 直接挂/摘一条 `struct clk_lookup` | |
| `clkdev_add_table(cl, num)` | 批量挂 | |
| `clk_register_clkdev(clk, con_id, dev_id)` | 注册 + 建映射，一步到位 | |
| `clk_hw_register_clkdev(hw, con_id, dev_id)` | 同上，`clk_hw` 版 | |
| `devm_clk_hw_register_clkdev(dev, hw, con_id, dev_id)` | devm 版 | |

**arm64 上基本用不到这一族**。它是 DT 之前的产物：靠字符串匹配设备名。留在这里是因为 (a) `clk_get` 的兜底路径会查它（00 §6.1），(b) 少数不走 DT 的场景（如某些 MFD 子设备）还在用。

---

## §3 错误码语义

### 3.1 获取阶段

| 错误码 | 出现在 | 含义与处置 |
|---|---|---|
| `-EPROBE_DEFER` | `clk_get` / `devm_clk_get` | **最重要的一个**：DT 里写了这个时钟，但提供者驱动还没 probe 完。**必须原样向上返回**，让驱动核心稍后重试。吞掉它 = 设备永远起不来。判据：`of_clk_get_hw` 找不到 provider 但 DT 节点存在 |
| `-ENOENT` | `clk_get` | DT 的 `clock-names` 里没有这个名字，且 clkdev 表里也没有。**真的没有这个时钟**。用 `clk_get_optional` 时会被转成 NULL |
| `-EINVAL` | `clk_get` / `of_clk_get` | 参数不合法（如 index 越界） |
| `-ENOMEM` | `clk_hw_create_clk` | 分配 `struct clk` 失败 |

**`-EPROBE_DEFER` 和 `-ENOENT` 的区别决定了驱动该不该报错**。标准写法：
```c
clk = devm_clk_get(dev, "core");
if (IS_ERR(clk))
        return dev_err_probe(dev, PTR_ERR(clk), "failed to get core clock\n");
```
`dev_err_probe` 会在 `-EPROBE_DEFER` 时把消息降级成 debug 级并记录 defer 原因，**不刷屏**。这是 6.x 的标准做法，比 `if (IS_ERR) { dev_err(...); return PTR_ERR(...); }` 好。

### 3.2 使能阶段

| 错误码 | 出现在 | 含义与处置 |
|---|---|---|
| `-ESHUTDOWN` | `clk_enable` | **没有先 prepare 就 enable**。伴随 `WARN_ON`。判据在 `drivers/clk/clk.c:1188-1190`。这是纯编程错误，改代码，不要重试 |
| `-EINVAL` | `clk_prepare` / `clk_enable` | 句柄无效 |
| 提供方自定义 | `.prepare` / `.enable` 回调 | 硬件层错误（如 PLL 锁定超时）会原样透出。典型是 `-ETIMEDOUT` |

### 3.3 频率阶段

| 错误码 | 出现在 | 含义与处置 |
|---|---|---|
| `-EBUSY` | `clk_set_rate` | **别人**持有独占权（`clk_rate_exclusive_get`）。判据 `drivers/clk/clk.c:2449-2452`。注意「别人」：`clk_set_rate` 会先把**自己**的 exclusive_count 临时撤保护再设，所以持有独占权的消费者自己改频率是允许的 |
| `-EINVAL` | `clk_set_rate` | `clk_calc_new_rates` 返回 NULL——算不出可行方案（如落在约束区间之外）。判据 `drivers/clk/clk.c:2454-2456` |
| `-EBUSY` | `clk_set_rate` 的 PRE 通知 | 某个消费者的通知回调返回了 `NOTIFY_BAD`。核心先给已收到 PRE 的节点补发 `ABORT_RATE_CHANGE`，**然后返回 `-EBUSY`**（不是 `-EINVAL`）。判据 `drivers/clk/clk.c:2463-2470` |
| runtime-PM 的错误码 | `clk_set_rate` | `clk_pm_runtime_get` 失败时原样透出（如 `-EACCES`）。判据 `drivers/clk/clk.c:2458-2460` |
| `-EINVAL` | `clk_set_rate_range` / `clk_set_min_rate` / `clk_set_max_rate` | min > max（`drivers/clk/clk.c:2588-2593`），或新区间与**其他消费者**的约束求交后为空（`clk_core_check_boundaries`，`drivers/clk/clk.c:2604-2607`） |

**最容易踩的不是错误码，而是"没有错误码"**：如果一个节点既没有 `.set_rate` 也没有 `CLK_SET_RATE_PARENT`，`clk_core_req_round_rate_nolock` 会把请求 round 回当前频率，于是走到 `drivers/clk/clk.c:2445-2447` 的 "bail early if nothing to do" **直接返回 0**。也就是说：

```
clk_set_rate(clk, 100000000);   /* 返回 0 —— 看起来成功了 */
clk_get_rate(clk);              /* 还是原来那个值 */
```

**`clk_set_rate` 返回 0 只意味着"没出错"，不意味着"频率变了"。** 唯一可靠的做法是设完再 `clk_get_rate` 读一次，或者事先用 `clk_round_rate` 问。

`clk_set_rate_range` 里有一段源码自带的 `FIXME`（`drivers/clk/clk.c:2618-2628`）：新区间设好之后核心会把上次请求的频率 clamp 进新区间再设一次，但这一步可能因为 `round_rate` 落到边界错误的一侧而失败——失败时区间会回滚（`drivers/clk/clk.c:2632-2636`）。这条设计债记在 03 Part C。

### 3.4 父节点阶段

| 错误码 | 出现在 | 含义与处置 |
|---|---|---|
| `-EPERM` | `clk_set_parent` | 节点有多个候选父但**没有 `.set_parent` 回调**。判据 `drivers/clk/clk.c:2789-2790`。注意是 `-EPERM` 而不是直觉上的 `-ENOSYS`/`-EOPNOTSUPP` |
| `-EBUSY` | `clk_set_parent` | 节点带 `CLK_SET_PARENT_GATE` 且 **`prepare_count > 0`**（`drivers/clk/clk.c:2793-2795`）。判据是 prepare 计数而不是 enable 计数——`clk_unprepare` 也做了才算"没在用" |
| `-EBUSY` | `clk_set_parent` | 有别人持有独占权（`drivers/clk/clk.c:2796-2798`） |
| `-EINVAL` | `clk_set_parent` | 目标不在候选父列表里。**先用 `clk_has_parent` 问** |
| 0（无错误） | `clk_set_parent` | 目标已经是当前父，或 `clk` 为 NULL——直接返回 0（`drivers/clk/clk.c:2782-2787`） |

### 3.5 相位 / 占空比 / 精度

| 错误码 | 出现在 | 含义与处置 |
|---|---|---|
| `-EINVAL` | `clk_set_phase` | **无 `.set_phase` 回调**。实现方式是把 `ret` 初值置成 `-EINVAL`（`drivers/clk/clk.c:2888`），只有回调存在才会被覆盖（`:2900-2904`） |
| `-EBUSY` | `clk_set_phase` | 有别人持有独占权（`drivers/clk/clk.c:2896-2897`） |
| `-EINVAL` | `clk_set_duty_cycle` | `den == 0` 或 `num > den`（`drivers/clk/clk.c:3106-3108`） |
| `-EBUSY` | `clk_set_duty_cycle` | 独占权（`drivers/clk/clk.c:3055-3056`） |

**这一族有三处"静默成功"，必须记住**：

1. **`clk_set_phase` 不会因为角度越界报错**——它先做 `degrees %= 360; if (degrees < 0) degrees += 360;`（`drivers/clk/clk.c:2939-2942`）把任意整数归一化。传 720 等于传 0，传 -90 等于传 270，**不报错**。
2. **`clk_set_duty_cycle` 在节点没有 `.set_duty_cycle` 时会往父节点传播**（需要 `CLK_DUTY_CYCLE_PARENT` 或 `CLK_SET_RATE_PARENT`，`drivers/clk/clk.c:3061-3062` → `:3078-3081`）；**如果连父节点也不满足条件，`clk_core_set_duty_cycle_parent_nolock` 的 `ret` 初值是 0，于是返回 0**——什么都没做但报告成功。
3. **`clk_get_phase` 在无 `.get_phase` 时返回 0**（`drivers/clk/clk.c:2963-2965`），而 0 是一个合法相位值，无法与"不支持"区分。同理 `clk_get_accuracy` 无回调时返回**继承来的父精度**（顶到根就是 0），`clk_get_scaled_duty_cycle` 无回调时返回**默认的 50%**。

也就是说这四个"读"接口**会给你一个看起来合理但没有硬件依据的数字**。用之前先确认节点真的实现了回调——最快的办法是看 debugfs 里有没有对应的文件（`/sys/kernel/debug/clk/<name>/clk_phase`、`clk_duty_cycle`，见 07 §4）。

### 3.6 注册阶段（提供方）

| 错误码 | 出现在 | 含义与处置 |
|---|---|---|
| `-EINVAL` | `clk_hw_register` | `hw->init` 为 NULL；或 `ops` 缺少必需回调组合（如实现了 `.set_rate` 却没有 `.recalc_rate`）；或 `num_parents > 0` 但 `parent_names`/`parent_data`/`parent_hws` 全为 NULL。**必需回调矩阵见 `Documentation/driver-api/clk.rst`** |
| `-ENOMEM` | `clk_hw_register` | 分配 `clk_core` 或父名数组失败 |
| `-EEXIST` | `clk_hw_register` | **同名时钟已注册**。clk 的名字是全局唯一的字符串，厂商驱动里最常见的是复制粘贴时忘了改名 |
| `-EBUSY` | `of_clk_add_hw_provider` | 这个 `device_node` 已经登记过 provider |

注册失败**不会**让节点半挂在树上：`clk.c` 的 `__clk_core_init` 在任一校验失败时会走统一的 `out:` 出错路径把已加的链表项摘掉。所以 probe 里 `clk_hw_register` 失败直接返回错误是安全的。

---

## §4 三族"包装"接口的选择

同一件事有多种写法，选择顺序固定：

| 场景 | 首选 | 次选 | 不要用 |
|---|---|---|---|
| 消费者拿 1 个时钟并全程开着 | `devm_clk_get_enabled` | `devm_clk_get` + `clk_prepare_enable` | `clk_get`（要自己写 remove） |
| 消费者拿 1 个时钟但要动态开关 | `devm_clk_get` + `clk_prepare_enable`/`clk_disable_unprepare` | — | 只 `clk_enable`（会 `-ESHUTDOWN`） |
| 消费者拿 3 个以上时钟 | `devm_clk_bulk_get_all` | `devm_clk_bulk_get` | 逐个 `devm_clk_get`（错误路径难写对） |
| 消费者不确定时钟是否存在 | `devm_clk_get_optional*` | — | 手工判 `-ENOENT` |
| 提供方注册普通节点 | `devm_clk_hw_register_<类型>` | `clk_hw_register_<类型>` + 手工注销 | `clk_register_<类型>` |
| 提供方注册自定义 ops 节点 | `devm_clk_hw_register` | `clk_hw_register` | `clk_register` |
| 提供方登记 DT provider | `devm_of_clk_add_hw_provider` | `of_clk_add_hw_provider` | `of_clk_add_provider` |

**一条经验判据**：看到 API 名字里有 `clk_hw` 就是提供方的，只有 `clk` 就是消费者的；`clk_register` 系列返回 `struct clk *` 给提供方，这个"跨界"就是它被废弃的原因。

代码骨架见 05；`clk_ops` 的字段语义见 02 §4；注册流程逐段剖析见 03 Part A。
