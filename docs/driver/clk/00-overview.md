---
title: 总览
---

# 00 · clk 子系统总览（复习入口）

> 目标内核：linux-6.1.y，`git log -1` = `f6e38ae624cf`（Linux 6.1.158），树干净未打补丁。
> 目标架构：**arm64 only**。举例一律取自 `arch/arm64/boot/dts/`。ACPI 相关内容不在范围内（裁剪清单见 99 §2.7）。
> 本篇定位：**隔几个月回来只读这一篇就能恢复全貌**。要细节再往后翻，导航见 §8。

---

## §1 这个子系统解决什么问题

### 1.1 一句话

Common Clock Framework（CCF，`CONFIG_COMMON_CLK`）把 SoC 里成百上千个"时钟节点"抽象成**一棵有向树**，然后向两边各提供一套接口：

- 朝**消费者**（普通设备驱动）：`include/linux/clk.h`。语义是「我要用这个时钟 / 我要它跑多快」，看不见寄存器。
- 朝**提供方**（时钟控制器驱动）：`include/linux/clk-provider.h`。语义是「这一个节点怎么开关、怎么改频率、它的父节点是谁」，只管自己那几个寄存器位。

中间那层公共代码在 `drivers/clk/clk.c`（5463 行），负责树的拓扑、引用计数、两把全局锁、频率变更的传播和回滚。

### 1.2 为什么必须有这一层

三件事单个驱动做不了：

| 问题 | 为什么单个驱动解决不了 | CCF 怎么解决 |
|---|---|---|
| **共享**：一个 PLL 同时喂 5 个外设 | 各驱动各自关闭寄存器位，第 1 个 `disable` 就把另外 4 个搞死 | 引用计数在 `clk_core` 上（`drivers/clk/clk.c:80`、`:81`），归零才真关 |
| **级联**：改 UART 波特率要动上游分频，可能要动 PLL | 驱动不知道自己上游是谁，也不知道上游还挂了谁 | 树 + `CLK_SET_RATE_PARENT`（`clk-provider.h:21`）逐级上抛 |
| **省电**：启动后没人用的时钟要关掉 | 没有全局视角，不知道"没人用" | `late_initcall_sync(clk_disable_unused)`（`drivers/clk/clk.c:1499`）遍历全树 |

### 1.3 子系统类型判定：**框架型**（无 `bus_type`）

判定命令与结果（登记在 99 §1）：

```bash
/usr/bin/grep -rn 'struct bus_type' drivers/clk/ include/linux/clk*.h include/linux/clk/
# 无输出
```

所以本文档集不套用"控制器在 platform 总线、设备在子系统总线"那套模板。clk 的形状是：

- 时钟控制器本身**是一个普通 platform 设备**（或 CLK_OF_DECLARE 早期初始化，见 §5.6），走 platform 总线的 probe。
- 时钟节点**不是 `struct device`**，不注册到任何总线，不绑 driver。它只是 `clk_core`（`drivers/clk/clk.c:60-97`）挂在 `clk_root_list`/`clk_orphan_list`（`drivers/clk/clk.c:36`、`:37`）上的树节点。
- 消费者**不通过总线匹配拿到时钟**，而是靠 DT phandle + `clk_get` 家族按名字查（§6.1）。

这一点是 clk 最容易被类比错的地方：`struct clk` 长得像个设备句柄，但它连 `struct device` 都不是。02 §1.0 会把 `clk` / `clk_core` / `clk_hw` 三者的关系逐行摊开。

### 1.4 代码规模与"读全 / 只读符号表"的分界

| 范围 | 行数 | 本文档集的处理 |
|---|---|---|
| `drivers/clk/clk.c` | 5463 | **逐行读**（03 的主要素材） |
| `drivers/clk/clk.h` | 38 | 逐行读（核心内部私有原型） |
| `clkdev.c` / `clk-devres.c` / `clk-bulk.c` / `clk-conf.c` | 381 / 225 / 248 / 152 | **逐行读**（04 的素材） |
| 8 个基本时钟类型 `clk-{fixed-rate,fixed-factor,gate,divider,mux,composite,multiplier,fractional-divider}.c` | 合计约 2723 | **逐行读**（04 §2） |
| `include/linux/clk.h` + `clk-provider.h` | 1161 + 1598 | 逐行读（01 的素材） |
| `drivers/clk/` 全目录（含 48 个厂商子目录） | 47434（仅顶层 `.c`/`.h`）+ 各子目录 | **只讲契约**，代表驱动选法见 99 §2.2 |

`drivers/clk/` 顶层有 128 个条目、48 个厂商子目录（`actions/ … zynqmp/`）。**不可能读全，也不需要**——它们全部只是在填 `clk_ops`（`clk-provider.h:232-267`）里的那二十几个回调。

---

## §2 硬件背景：SoC 里的时钟长什么样

读 CCF 之前必须先有硬件图像，否则 `prepare`/`enable` 分离、`round_rate`/`set_rate` 分离这些设计看起来都是多余的。本节不引用代码，只讲硬件；对应的代码在 §3 之后。

### 2.1 一切的起点：晶体振荡器

SoC 板子上通常焊一颗或几颗石英晶体（crystal），典型 24 MHz / 25 MHz / 26 MHz，另有一颗 32.768 kHz 用于 RTC。晶体本身只是一块压电材料，配合 SoC 内部的振荡电路（OSC）产生一个方波。

特点：

- **频率固定**，由晶体的物理切割决定，软件改不了。
- **精度有限**，用 ppm（百万分之一）或 ppb（十亿分之一）描述。一颗 ±20 ppm 的 24 MHz 晶体，实际可能是 24.00048 MHz。这个误差会**沿时钟树一路继承下去**，这就是为什么 CCF 有 `accuracy` 概念（§3.5）。
- **起振需要时间**，典型几百微秒到几毫秒。这是 `prepare` 存在的第一个理由。

在设备树里，晶振就是一个 `fixed-clock`：

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:214-219 */
xin24m: xin24m {
        compatible = "fixed-clock";
        clock-frequency = <24000000>;
        clock-output-names = "xin24m";
        #clock-cells = <0>;
};
```

`#clock-cells = <0>` 表示"我只输出一个时钟，引用我的时候不用带索引"。这个节点在 CCF 里会变成一棵树的**根**（root clock，没有父节点）。

### 2.2 PLL：把 24 MHz 变成 1.8 GHz

CPU 要跑 1.8 GHz，晶振只有 24 MHz。中间靠 **PLL（Phase-Locked Loop，锁相环）**。

PLL 的基本结构是一个反馈环：

```
                  ┌──────────────────────────────────────┐
                  │                                      │
 24MHz ──▶ [÷ M ] ──▶ [相位比较器] ──▶ [环路滤波] ──▶ [VCO] ──┬──▶ 输出
              (预分频)       ▲                              │
                             │                              │
                             └────────── [÷ N ] ◀───────────┘
                                         (反馈分频)
```

输出频率 = 24 MHz × N / M。软件要做的就是往寄存器里写 M、N（有些 PLL 还有小数部分和后分频）。

对软件的三个后果：

1. **锁定需要时间。** 改完 M/N 之后，环路要重新稳定，典型几十到几百微秒。期间输出频率是乱的。所以 PLL 驱动的 `set_rate` 里几乎一定有一个"等 lock 位置 1"的轮询。这是 `prepare` 和"改频率必须先关掉"（`CLK_SET_RATE_GATE`）存在的理由。
2. **不是任意频率都能出。** M、N 是整数且有取值范围，所以 PLL 只能输出一个离散集合。消费者说"我要 100 MHz"，硬件可能只能给 99.84 MHz。这就是 `round_rate` / `determine_rate` 必须和 `set_rate` 分开的理由：**先问"你最接近能给多少"，再决定要不要提交**。
3. **改 PLL 会连带影响所有下游。** 一个 PLL 通常喂十几条支路，改它等于同时改十几个外设的时钟。所以频率变更必须是"先算整棵子树、发通知、再提交"的三段式（§5.3）。

### 2.3 分频器（divider）

PLL 输出往下走，每一级用分频器降频。硬件上就是一个计数器：输入 N 个周期，输出 1 个周期。

寄存器里存的是一个位域，但**位域的值和实际除数之间的映射五花八门**，这就是 `clk_divider` 那一堆 flag 的来源（`clk-provider.h:671-678`）：

| 硬件行为 | 对应 flag |
|---|---|
| 寄存器值 `v` → 除数 `v+1`（默认） | 无 flag |
| 寄存器值 `v` → 除数 `v` | `CLK_DIVIDER_ONE_BASED` |
| 寄存器值 `v` → 除数 `2^v` | `CLK_DIVIDER_POWER_OF_TWO` |
| 值 0 表示除数 = `2^width` | `CLK_DIVIDER_MAX_AT_ZERO` |
| 完全不规则，要查表 | `table` 字段（`clk_div_table`，`clk-provider.h:613-616`） |
| 写低 16 位时高 16 位要同时写掩码 | `CLK_DIVIDER_HIWORD_MASK` |
| 只能读不能改 | `CLK_DIVIDER_READ_ONLY` |

这张表是理解 04 §2.4 的钥匙：`clk-divider.c` 里 689 行代码大部分就是在处理这七种映射的组合。

分频器只能整数分频（少数支持小数，见 `clk-fractional-divider.c`），所以"能出哪些频率"进一步受限。

### 2.4 多路选择器（mux）

一条支路的输入可能有多个候选：PLL0、PLL1、晶振直通、或者一个外部输入。硬件上是一个多路开关，寄存器里存"选第几路"。

对软件的后果：

- **父节点是运行时可变的。** 这是 clk 树和一般设备树最大的区别——`clk_core->parent`（`drivers/clk/clk.c:68`）是会变的。
- **索引和寄存器值不是一回事。** 硬件可能用 `0b00/0b01/0b11` 表示三路，中间跳号。所以 `clk_ops` 里的 `.get_parent` 返回的是**数组索引**，由驱动负责把寄存器值翻译成索引（`clk-provider.h:152-160` 的 kerneldoc 明确说了这一点）。
- **切换瞬间可能有毛刺（glitch）。** 有些 mux 支持无缝切换，有些必须先关掉。后者对应 `CLK_SET_PARENT_GATE`（`clk-provider.h:20`）。

### 2.5 门控（gate）

最简单也最常见：一个使能位，置 1 出时钟，置 0 停时钟。省电的主力。

两个硬件细节直接决定了 CCF 的接口形状：

- **门控通常是纯寄存器写，不需要等待，也不会睡眠。** 所以 `clk_enable` 被设计成**允许在原子上下文调用**（`Documentation/driver-api/clk.rst:270-273`），`.enable` 回调不许睡眠。
- **但有些"门控"不是寄存器。** 比如时钟来自一颗 I²C 挂的时钟芯片（`clk-si5351.c`、`clk-cdce925.c`），开关它要发 I²C 事务，一定会睡眠。这类节点只能把工作放到 `.prepare` 里。

**`prepare`/`enable` 分离的完整理由就在这里**：CCF 需要一个"可能睡眠"的阶段和一个"绝不睡眠"的阶段，前者给 PLL 起振、I²C 事务、稳压器上电；后者给单纯的寄存器位翻转。`clk_ops` 的 kerneldoc 在 `include/linux/clk-provider.h:221-230` 讲的是同一件事。

### 2.6 相位与占空比

两个只有部分硬件支持的能力：

- **相位（phase）**：同频率的两路时钟之间的相对延迟，单位是度（0–359）。典型用途是 SDIO/eMMC 的采样时钟要相对数据时钟偏移 90°，以及以太网 RGMII 的 TX/RX 延迟调整。对应 `.get_phase`/`.set_phase`（`clk-provider.h:258`、`:259`）。
- **占空比（duty cycle）**：高电平时间占一个周期的比例，用分数 `num/den` 表示（`clk_duty`，`clk-provider.h:80-83`）。典型用途是 PWM 型的时钟输出。对应 `.get_duty_cycle`/`.set_duty_cycle`（`:260-263`）。

这两个能力在 arm64 SoC 上并不普遍。绝大多数节点这两个回调是 NULL，核心层会回退到默认值（相位 0、占空比 1/2）。

### 2.7 精度（accuracy）

沿树继承的误差，单位 ppb。规则很简单：**分频和倍频不改变相对精度**（分频器把频率和绝对误差同时缩小，比值不变），所以子节点默认继承父节点的精度（`clk-provider.h:179-184` 的 kerneldoc 明确写了"if this op is not set then clock accuracy will be initialized to parent accuracy or 0"）。

只有两类节点会改变精度：晶振（起点，自己声明一个值）和 PLL（分数分频引入额外抖动）。所以 `.recalc_accuracy` 极少被实现。

### 2.8 arm64 上的实际形状：一个 CRU 管全片

arm64 SoC 的典型做法是**一个（或几个）集中的时钟控制器**，厂商叫法不同：Rockchip 叫 CRU（Clock & Reset Unit）、瑞芯微还额外分出 PMUCRU、NXP i.MX 叫 CCM、高通叫 GCC、联发科叫 topckgen/infracfg。

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:1449-1456 */
cru: clock-controller@ff760000 {
        compatible = "rockchip,rk3399-cru";
        reg = <0x0 0xff760000 0x0 0x1000>;
        clocks = <&xin24m>;
        clock-names = "xin24m";
        rockchip,grf = <&grf>;
        #clock-cells = <1>;
        #reset-cells = <1>;
        ...
};
```

要点：

- **`reg` 只有 4 KB。** 一个 CRU 里就那么几十个寄存器，却承载几百个时钟节点——每个节点只占几个位。所以"一个 `clk_core` 对应一个寄存器位域"，不是"一个设备"。
- **`clocks = <&xin24m>`**：CRU 自己也是消费者，它要拿到晶振作为整棵树的输入。
- **`#clock-cells = <1>`**：引用它的时候要带一个索引，即 `<&cru CLK_UART0>`。索引常量定义在 `include/dt-bindings/clock/` 下（DT 侧和驱动侧共用同一个头文件）。
- **`#reset-cells = <1>`**：同一个硬件单元同时是复位控制器。这是 arm64 上极常见的组合——**同一个 `platform_device` 同时向 clk 和 reset 两个子系统注册**。（reset 子系统的文档在本批次的后面。）
- **`assigned-clocks` / `assigned-clock-rates`**：DT 里直接指定"注册完就把这些时钟设成这个频率/这个父节点"。这条路径由 `clk-conf.c`（152 行）在 probe 时消费，详见 06 §3。

### 2.9 硬件背景小结：五个约束 → 五个接口决定

| 硬件事实 | CCF 的接口决定 |
|---|---|
| PLL 起振/I²C 事务会睡眠；门控位翻转不会 | `prepare`（可睡眠，mutex）与 `enable`（不可睡眠，spinlock）分离 |
| 硬件只能输出离散频率集合 | `round_rate`/`determine_rate` 与 `set_rate` 分离，先问后做 |
| 一个上游喂多个下游 | 引用计数 + 三段式频率变更（算 → 通知 → 提交） |
| mux 让父节点运行时可变 | 树的父指针可变；索引与寄存器值分离翻译 |
| 有些节点改频率前必须先停 | `CLK_SET_RATE_GATE` / `CLK_SET_PARENT_GATE` / `CLK_SET_RATE_UNGATE` |

---

## §3 概念模型：四层 + 两组分离

### 3.1 四层结构

```
 ┌──────────────────────────────────────────────────────────────┐
 │ 消费者驱动（uart / mmc / gpu / …）                             │
 │   只见 struct clk *，只调 include/linux/clk.h 里的 API          │
 └───────────────────────────┬──────────────────────────────────┘
                             │  clk_get / clk_prepare_enable / clk_set_rate
 ┌───────────────────────────▼──────────────────────────────────┐
 │ ① struct clk           —— per-consumer 句柄                   │
 │    drivers/clk/clk.c:102-111                                 │
 │    每次 clk_get 新建一个；记录"这一个使用者"的                    │
 │    min_rate/max_rate/exclusive_count                          │
 └───────────────────────────┬──────────────────────────────────┘
                             │  clk->core
 ┌───────────────────────────▼──────────────────────────────────┐
 │ ② struct clk_core      —— 树节点，全局唯一                     │
 │    drivers/clk/clk.c:60-97                                   │
 │    拓扑（parent/children）、引用计数（prepare/enable/protect）、  │
 │    缓存（rate/accuracy/phase/duty）、通知链                     │
 │    核心层的所有算法都只操作这一层                              │
 └───────────────────────────┬──────────────────────────────────┘
                             │  core->hw / core->ops
 ┌───────────────────────────▼──────────────────────────────────┐
 │ ③ struct clk_hw        —— 硬件侧的锚点（三个指针）              │
 │    include/linux/clk-provider.h:324-328                      │
 │    嵌在 struct clk_foo 里，靠 container_of 反查                 │
 │ ④ struct clk_ops       —— 25 个回调                            │
 │    include/linux/clk-provider.h:232-267                      │
 └───────────────────────────┬──────────────────────────────────┘
                             │
 ┌───────────────────────────▼──────────────────────────────────┐
 │ 硬件寄存器（CRU / CCM / GCC …）                                │
 └──────────────────────────────────────────────────────────────┘
```

三个"clk"名字很像，职责完全不同。一句话区分：

| 结构 | 每个什么有一个 | 谁分配 | 谁能看见 |
|---|---|---|---|
| `struct clk` | 每次 `clk_get` 一个 | 核心层（`clk_hw_create_clk`，`drivers/clk/clk.c:4041`） | 消费者 |
| `struct clk_core` | 每个时钟节点一个 | 核心层（`__clk_register`，`drivers/clk/clk.c:4198`） | **只有核心层** |
| `struct clk_hw` | 每个时钟节点一个 | **提供方驱动**（嵌在自己的结构里） | 提供方 + 核心层 |

`clk_core` 的定义在 `.c` 文件里而不是头文件里（`drivers/clk/clk.c:60`），这是故意的——**提供方驱动在编译期就看不到它的字段**，只能通过 `clk_hw_get_rate()` 这类访问器。02 §1.0 会展开这个设计的后果。

### 3.2 第一组分离：prepare / enable

|  | prepare | enable |
|---|---|---|
| 锁 | `prepare_lock`，mutex（`drivers/clk/clk.c:28`） | `enable_lock`，spinlock（`drivers/clk/clk.c:27`） |
| 能否睡眠 | **可以** | **绝对不行** |
| 调用上下文 | 进程上下文 | 允许原子上下文/中断 |
| 计数字段 | `prepare_count`（`drivers/clk/clk.c:81`） | `enable_count`（`drivers/clk/clk.c:80`） |
| 典型工作 | PLL 起振并等 lock、I²C 事务、上电 | 翻一个使能位 |

顺序是**强制**的：`clk_core_enable()` 一开头就检查 `prepare_count == 0` 并返回 `-ESHUTDOWN`（`drivers/clk/clk.c:1188-1190`）。所以「先 prepare 再 enable」不是建议而是断言。

两者都是**递归向上**的：`clk_core_prepare()` 在自己计数从 0 变 1 时先递归 prepare 父节点（`drivers/clk/clk.c:1055`），`clk_core_enable()` 同理（`drivers/clk/clk.c:1193`）。所以消费者只 enable 叶子节点，整条上游链会自动打开。

日常代码几乎不单独用它们，而是用组合宏 `clk_prepare_enable()`（`include/linux/clk.h:1068`）和 `clk_disable_unprepare()`（`:1083`）。

### 3.3 第二组分离：算（determine）/ 提交（set）

改频率是三段式，不是一次调用：

```
 clk_set_rate(clk, 100000000)
     │
     ├─ ① 算：clk_calc_new_rates()          clk.c:2197
     │     沿树往上问 round_rate/determine_rate，
     │     把结果暂存在 new_rate / new_parent / new_child 字段
     │     （clk.c:74、:75、:76 —— 注意这三个字段就是为这一步存在的）
     │
     ├─ ② 通知：clk_propagate_rate_change()  clk.c:2280
     │     向子树发 PRE_RATE_CHANGE，任何一个 notifier 返回错误就整体放弃
     │
     └─ ③ 提交：clk_change_rate()            clk.c:2318
           真正调 set_rate / set_parent / set_rate_and_parent，
           然后发 POST_RATE_CHANGE
```

**为什么必须分开**：改一个 PLL 会同时改十几条下游支路的实际频率。下游的消费者（比如正在传输的 MMC）需要一个"停一下、我先把手上的事收尾"的机会。这就是 `PRE_RATE_CHANGE` / `POST_RATE_CHANGE` / `ABORT_RATE_CHANGE`（`include/linux/clk.h:39-41`）三条通知的用途。

同一套"先问后做"也暴露给消费者：`clk_round_rate()`（`drivers/clk/clk.c:1718`）只问不做，`clk_set_rate()`（`drivers/clk/clk.c:2503`）才提交。

### 3.4 树的三种状态：root / 正常 / orphan

核心层维护两条顶层链表（`drivers/clk/clk.c:36`、`:37`）：

- **root**：没有父节点的节点（晶振、外部输入）。挂在 `clk_root_list`。
- **正常**：父节点已解析，挂在父节点的 `children` 链表（`drivers/clk/clk.c:88`、`:89`）。
- **orphan**：**声明了父节点，但那个父节点还没注册**。挂在 `clk_orphan_list`，`core->orphan = true`（`drivers/clk/clk.c:78`）。

orphan 是 clk 独有的、必须理解的状态。原因：时钟控制器的注册顺序不受控（CRU 先注册还是 PMUCRU 先注册取决于 probe 顺序），而节点是按名字/phandle 声明父子关系的。所以 CCF 允许"先注册孤儿、后来父节点出现时自动接上"。

后果对调试很关键：**一个 orphan 时钟的 `rate` 是 0**，因为它没有父节点可以 recalc。`/sys/kernel/debug/clk/clk_orphan_summary`（`drivers/clk/clk.c:3708`）专门用来看这个。07 §4 会展开。

### 3.5 缓存：rate / accuracy / phase / duty 都是缓存值

`clk_core` 里的 `rate`（`drivers/clk/clk.c:72`）、`accuracy`（`:85`）、`phase`（`:86`）、`duty`（`:87`）**都不是每次读硬件得到的**，而是注册时算一次、变更时更新的缓存。

`clk_get_rate()`（`drivers/clk/clk.c:1916`）默认直接返回缓存。只有带了 `CLK_GET_RATE_NOCACHE`（`clk-provider.h:25`）的节点才会重新调 `.recalc_rate`。同理 `CLK_GET_ACCURACY_NOCACHE`（`:27`）。

这是一个**真实的踩坑点**：如果硬件的频率被 CCF 之外的东西改了（bootloader、协处理器、firmware），CCF 的缓存就是错的，而 `clk_get_rate()` 会一直返回错的值。07 §3 给了定位方法。

---

## §4 数据结构速览

只列骨架，字段级说明和关系图在 **02**。

| 结构 | 位置 | 一句话 |
|---|---|---|
| `struct clk` | `drivers/clk/clk.c:102-111` | per-consumer 句柄；带 `dev_id`/`con_id` 便于调试 |
| `struct clk_core` | `drivers/clk/clk.c:60-97` | 树节点；核心层私有；带 `kref`（`:96`） |
| `struct clk_parent_map` | `drivers/clk/clk.c:52-58` | 父节点的"未解析形态"：可能只有名字或索引 |
| `struct clk_hw` | `include/linux/clk-provider.h:324-328` | 三个指针：`core` / `clk` / `init` |
| `struct clk_ops` | `include/linux/clk-provider.h:232-267` | 25 个回调，全部可选（组合约束见 02 §4.2） |
| `struct clk_init_data` | `include/linux/clk-provider.h:297-306` | 注册时的一次性输入；注册完 `hw->init` 会被置 NULL |
| `struct clk_parent_data` | `include/linux/clk-provider.h:276-281` | 描述一个父节点的四种方式（hw / fw_name / name / index） |
| `struct clk_rate_request` | `include/linux/clk-provider.h:57-63` | `determine_rate` 的进出参数包 |
| `struct clk_duty` | `include/linux/clk-provider.h:80-83` | 占空比分数 |
| `struct clk_notifier` / `_data` | `include/linux/clk.h:54`、`:71` | 频率变更通知 |
| `struct clk_bulk_data` | `include/linux/clk.h:87` | 批量操作的 (id, clk) 对 |
| `struct clk_hw_onecell_data` | `include/linux/clk-provider.h:1360` | 一个控制器暴露 N 个时钟时的容器 |

全局变量与锁：

| 符号 | 位置 | 作用 |
|---|---|---|
| `enable_lock` | `drivers/clk/clk.c:27` | spinlock，护 enable/disable |
| `prepare_lock` | `drivers/clk/clk.c:28` | mutex，护其余全部操作 |
| `prepare_owner` / `enable_owner` | `drivers/clk/clk.c:30`、`:31` | **可重入**支持：记录持有者 |
| `prepare_refcnt` / `enable_refcnt` | `drivers/clk/clk.c:33`、`:34` | 同一线程重复获取时只计数 |
| `clk_root_list` / `clk_orphan_list` | `drivers/clk/clk.c:36`、`:37` | 两条顶层链表 |
| `clk_notifier_list` | `drivers/clk/clk.c:38` | 全局通知链表 |
| `clk_rpm_list` / `clk_rpm_list_lock` | `drivers/clk/clk.c:41`、`:42` | 用了 runtime PM 的节点 |
| `of_clk_providers` / `of_clk_mutex` | `drivers/clk/clk.c:4813`、`:4814` | DT phandle → provider 的注册表 |

**可重入锁是 clk 的一个签名设计**：`clk_prepare_lock()`（`drivers/clk/clk.c:214-227`）先 `mutex_trylock`，失败了再看持有者是不是自己，是就只加计数。理由写在 `Documentation/driver-api/clk.rst:298-303`——**驱动的 `.set_rate` 回调里允许再调 CCF 的 API**。02 §4 展开。

---

## §5 机制速览

每条一句话，细节在 **03**。

| # | 机制 | 入口 | 一句话 |
|---|---|---|---|
| 5.1 | 注册 | `clk_hw_register`（`drivers/clk/clk.c:4331`）→ `__clk_register`（`:4198`） | 建 `clk_core`、解析父节点映射（`clk_core_populate_parent_map`，`:4104`）、初始化（`__clk_core_init`，`:3778`） |
| 5.2 | prepare/enable | `drivers/clk/clk.c:1113` / `:1335` | 递归向上；计数从 0 变 1 才真动硬件 |
| 5.3 | 频率变更 | `clk_set_rate`（`drivers/clk/clk.c:2503`） | 三段式：算（`:2197`）→ 通知（`:2280`）→ 提交（`:2318`） |
| 5.4 | 换父节点 | `clk_set_parent`（`drivers/clk/clk.c:2862`） | `clk_core_set_parent_nolock`（`:2774`）；可能要求先 gate |
| 5.5 | 频率区间约束 | `clk_set_rate_range`（`drivers/clk/clk.c:2653`） | per-consumer 的 min/max（`drivers/clk/clk.c:107`、`:108`）汇总成节点的有效区间 |
| 5.6 | 独占频率 | `clk_rate_exclusive_get`（`include/linux/clk.h:202`） | `protect_count`（`drivers/clk/clk.c:82`）；`CLK_SET_RATE_GATE` 复用同一机制（`drivers/clk/clk.c:1079-1080`） |
| 5.7 | DT provider 注册 | `of_clk_add_hw_provider`（`drivers/clk/clk.c:4908`） | 把 `device_node` 和一个 `.get_hw` 回调登记进 `of_clk_providers` |
| 5.8 | 消费者查找 | `clk_get`（`clkdev.c:100`）/ `of_clk_get_by_name`（`drivers/clk/clk.c:5205`） | **DT 优先，clkdev 兜底**（`clkdev.c:105-111`） |
| 5.9 | 早期时钟 | `of_clk_init`（`drivers/clk/clk.c:5397`） + `CLK_OF_DECLARE`（`clk-provider.h:1365`） | 给"比 platform 总线更早就要用"的时钟（timer 等）留的后门 |
| 5.10 | 关闭未用时钟 | `clk_disable_unused`（`drivers/clk/clk.c:1460`） | `late_initcall_sync`（`:1499`）；`clk_ignore_unused` 可绕过（`:1452`、`:1458`） |
| 5.11 | 通知 | `clk_notifier_register`（`drivers/clk/clk.c:4665`）→ `__clk_notify`（`:1761`） | 三个消息：PRE / POST / ABORT（`include/linux/clk.h:39-41`） |
| 5.12 | 上下文保存恢复 | `clk_save_context`（`include/linux/clk.h:882`）/ `clk_restore_context`（`:890`） | 深度掉电后重建硬件状态；对应 `.save_context`/`.restore_context`（`clk-provider.h:241`、`:242`） |
| 5.13 | runtime PM 协同 | `clk_pm_runtime_get`（`drivers/clk/clk.c:114`）/ `_put`（`:122`） | 访问寄存器前先把控制器唤醒（`rpm_enabled`，`drivers/clk/clk.c:79`） |
| 5.14 | debugfs | `clk_debug_init`（`late_initcall`，`drivers/clk/clk.c:3722`） | `/sys/kernel/debug/clk/`；每节点一个目录（`:3593-3631`），全局四个汇总文件（`:3702-3711`） |

---

## §6 端到端调用路径

### 6.1 消费者拿到一个时钟

```
 驱动 probe:  devm_clk_get(dev, "baudclk")
     └─ clk_get(dev, "baudclk")                      drivers/clk/clkdev.c:100
          ├─ dev->of_node 存在？                      clkdev.c:105
          │    └─ of_clk_get_hw(np, 0, con_id)        （drivers/clk/clk.h 声明）
          │         └─ 按 clock-names 找索引 → of_clk_get_from_provider
          │                                          clk.c:5156
          │              └─ 在 of_clk_providers 里查 np → 调 provider->get_hw
          │                                          clk.c:4813
          └─ 没找到 → __clk_get_sys(dev, dev_id, con_id)
                                                     clkdev.c:86
               └─ clk_find_hw(dev_id, con_id)         （clkdev 静态表，04 §3）
     最后统一走 clk_hw_create_clk(dev, hw, ...)        clk.c:4041
          → 分配一个新的 struct clk，挂到 core->clks   clk.c:90
```

两条路径的取舍：**arm64 上基本只走 DT 路径**。clkdev 静态表是 arm32/旧平台遗产，但代码仍在（`clkdev.c` 381 行，`CONFIG_HAVE_CLK` 就编，见 `drivers/clk/Makefile:3`）。04 §3 说明它在什么情况下还会被用到。

### 6.2 打开一个时钟

```
 clk_prepare_enable(clk)                             include/linux/clk.h:1068
   ├─ clk_prepare(clk)                               clk.c:1113
   │    └─ clk_core_prepare_lock                     clk.c:1090
   │         ├─ clk_prepare_lock()  取 mutex（可重入） clk.c:214
   │         └─ clk_core_prepare(core)               clk.c:1041
   │              ├─ prepare_count == 0 ?            clk.c:1050
   │              │    ├─ clk_pm_runtime_get         clk.c:1051
   │              │    ├─ 递归 prepare 父节点          clk.c:1055
   │              │    └─ core->ops->prepare(hw)     clk.c:1061-1062
   │              ├─ prepare_count++                 clk.c:1070
   │              └─ CLK_SET_RATE_GATE → rate_protect clk.c:1079-1080
   └─ clk_enable(clk)                                clk.c:1335
        └─ clk_core_enable_lock                      clk.c:1215
             ├─ clk_enable_lock()  取 spinlock（可重入）clk.c:240
             └─ clk_core_enable(core)                clk.c:1179
                  ├─ prepare_count == 0 → -ESHUTDOWN clk.c:1188-1190
                  ├─ enable_count == 0 ?             clk.c:1192
                  │    ├─ 递归 enable 父节点           clk.c:1193
                  │    └─ core->ops->enable(hw)      clk.c:1200-1201
                  └─ enable_count++                  clk.c:1211
```

### 6.3 改一个频率

```
 clk_set_rate(clk, rate)                             clk.c:2503
   └─ clk_core_set_rate_nolock(core, rate)           clk.c:2433
        ├─ ① clk_calc_new_rates(core, rate)          clk.c:2197
        │      ├─ 问 .determine_rate 或 .round_rate
        │      ├─ CLK_SET_RATE_PARENT → 递归往上算
        │      └─ clk_calc_subtree() 填 new_rate      clk.c:2174
        ├─ ② clk_propagate_rate_change(top, PRE_RATE_CHANGE)
        │                                            clk.c:2280
        │      失败 → 再发一轮 ABORT_RATE_CHANGE，整体放弃
        └─ ③ clk_change_rate(top)                    clk.c:2318
               ├─ .set_rate / .set_parent / .set_rate_and_parent
               └─ POST_RATE_CHANGE
```

### 6.4 一个时钟控制器的注册（arm64 典型：platform driver）

```
 platform probe（CRU 的 probe）
   ├─ devm_platform_ioremap_resource() 拿到那 4 KB 寄存器
   ├─ 对每个时钟节点填 struct clk_foo + clk_init_data
   ├─ clk_hw_register(dev, &foo->hw)                 clk.c:4331
   │    └─ __clk_register(dev, np, hw)               clk.c:4198
   │         ├─ 分配 clk_core，拷贝 init 数据
   │         ├─ clk_core_populate_parent_map         clk.c:4104
   │         └─ __clk_core_init                      clk.c:3778
   │              ├─ 找父节点；找不到 → 进 clk_orphan_list
   │              ├─ 调 .init / .get_parent / .recalc_rate 建立缓存
   │              └─ 回扫 orphan 链表，把等着自己的孩子接上
   ├─ 把所有 hw 放进 clk_hw_onecell_data              clk-provider.h:1360
   └─ devm_of_clk_add_hw_provider(dev, of_clk_hw_onecell_get, data)
                                                     clk-provider.h:1521
```

### 6.5 启动尾声：关掉没人用的时钟

```
 late_initcall_sync(clk_disable_unused)              clk.c:1499
   └─ clk_disable_unused()                           clk.c:1460
        ├─ clk_ignore_unused ? 直接返回               clk.c:1465
        └─ 遍历 root + orphan 两条链表
             └─ clk_disable_unused_subtree(core)     clk.c:1411
                  ├─ 先递归处理孩子                    clk.c:1419
                  └─ enable_count == 0 且非 CLK_IGNORE_UNUSED/CLK_IS_CRITICAL
                       → .disable_unused 或 .disable
```

**这是 arm64 新板子最常见的"启动到某一步就挂"故障源**：驱动依赖 bootloader 开好的时钟但没有 `clk_prepare_enable`，`late_initcall_sync` 一到就被关掉。第一步永远是加 `clk_ignore_unused` 到 bootargs 验证（`Documentation/driver-api/clk.rst:255-262`，代码 `drivers/clk/clk.c:1452-1458`）。07 §2.1 给完整分诊流程。

---

## §7 和其它子系统的关系

| 对方 | 关系 | 关键点 |
|---|---|---|
| **platform 总线 / 驱动模型** | 时钟控制器是普通 platform 设备 | 但**时钟节点自己不是 device**（§1.3）。`clk_core->dev`（`drivers/clk/clk.c:65`）只是记录"是哪个设备注册的我" |
| **OF / 设备树** | 消费者靠 `clocks` + `clock-names` phandle 找提供方 | `of_clk_providers`（`drivers/clk/clk.c:4813`）是 DT node → provider 的注册表。06 全篇 |
| **reset** | 常常是**同一个硬件单元**（见 §2.8 的 `#reset-cells`） | 两个子系统各自注册，互不知道对方存在 |
| **pinctrl** | 无直接耦合 | 时钟走片内布线，不占引脚（少数 CLKOUT 引脚除外） |
| **runtime PM** | 双向依赖，需要小心 | CCF 访问寄存器前要 `pm_runtime_resume_and_get`（`drivers/clk/clk.c:119`）；而 PM domain 又可能要操作时钟。`drivers/clk/clk.c:137-140` 有一段注释专门讲这个死锁风险 |
| **cpufreq** | cpufreq 驱动是 CCF 的**消费者** | `clk_set_rate` 改 CPU 时钟。本批次后面有 cpufreq 文档 |
| **genpd（PM domain）** | 上下电时可能连带丢失时钟状态 | `.save_context`/`.restore_context`（`clk-provider.h:241-242`）+ `clk_save_context`/`clk_restore_context` |
| **regmap** | 很多非片内时钟芯片（I²C/SPI）用 regmap 访问 | 这类节点必须 `can sleep`，工作放在 `.prepare` 里（§2.5） |
| **SCMI / SCPI firmware** | 时钟由协处理器管理 | `COMMON_CLK_SCMI`（`drivers/clk/Kconfig:102`）、`COMMON_CLK_SCPI`（`:112`）。arm64 服务器/新 SoC 常见。04 §5 |
| **tracepoint** | `CREATE_TRACE_POINTS` 在 `drivers/clk/clk.c:99-100` | 事件定义在 `include/trace/events/clk.h`。07 §2 |

---

## §8 导读：我该看哪一篇

| 我想知道… | 去 |
|---|---|
| 有哪些文件、哪些 API、错误码什么意思 | **01** |
| `clk` / `clk_core` / `clk_hw` 到底怎么分工，字段级 | **02** |
| 注册、set_rate、锁、orphan 接管的逐段剖析 | **03** |
| 8 个基本时钟类型、clkdev、bulk、devres、SCMI | **04** |
| 我要写消费者 / 写一个时钟控制器驱动，代码骨架 | **05** |
| DTS 怎么写，`assigned-clocks` 谁消费，配错了怎么查 | **06** |
| 时钟没开 / 频率不对 / 启动挂住，怎么定位 | **07** |
| 「为什么 prepare 和 enable 要分两个」这类问题的大白话解释 | **08** |
| 上面这些结论是怎么验证的，哪些没验证 | **99** |

### 8.1 本篇的三个 结论（最容易记错的）

1. **时钟节点不是 device，不上任何总线。** clk 是框架型子系统，`drivers/clk/` 里没有 `struct bus_type`（§1.3 给了验证命令）。
2. **prepare/enable 分离的根因是"能不能睡眠"，不是"快慢"。** 判据是 `.prepare` 持 mutex、`.enable` 持 spinlock（`drivers/clk/clk.c:28`、`:27`）。
3. **`clk_get_rate()` 返回的是缓存。** 不是每次读硬件（§3.5）。
