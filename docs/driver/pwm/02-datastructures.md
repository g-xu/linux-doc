---
title: 数据结构
---

# pwm 子系统数据结构

> **基线**：`linux-6.1.158`，**arm64-only**。
> 本篇只讲"有哪些结构、字段是什么、彼此什么关系"。接口清单在 01 篇，流程在 03 篇。
> pwm 的结构总量很小——**8 个结构、2 个枚举、6 个全局变量**，
> 但**关系比结构本身难**，所以本篇把关系放在最前面。

---

## §1.0 先分清这六组关系

读 pwm 代码走弯路，九成不是因为某个结构没看懂，而是因为下面六组关系搞混了。
这一节的六张小表是本篇的核心，其余章节只是把字段补全。

### §1.0.A `pwm_chip` 与 `pwm_device`：一对多，而且是**内嵌数组**

```
struct pwm_chip                      ← 驱动自己分配（通常内嵌在驱动私有结构里）
   ├── npwm  = 4                     ← 驱动填
   ├── base  = 8                     ← 核心填（输出！）
   └── pwms  ──→ [ pwm_device × 4 ]  ← 核心用 kcalloc 一次性分配
                    ↑ ↑ ↑ ↑
                 hwpwm 0 1 2 3
                 pwm   8 9 10 11
```

| 事实 | 后果 |
|---|---|
| `chip->pwms` 是**一个数组**，`kcalloc(chip->npwm, ...)`（`drivers/pwm/core.c:278`） | 单个 `pwm_device` **不能单独释放**，也没有自己的引用计数 |
| 数组由核心分配、核心释放（`free_pwms()`，`core.c:53`） | 驱动不许动 `chip->pwms`，注释写着 "only used internally" |
| `pwm_device` 里没有 `kref`、没有 `refcount` | **消费者手里的 `struct pwm_device *` 没有任何生命周期保护** |

⚠️ 最后一行是 pwm 与多数子系统最大的结构性差异。`clk` 的 `clk` 句柄、
`regulator` 的 `regulator` 句柄都是**每个消费者一份的独立对象**，
pwm 的 `pwm_device` 却是**provider 数组里的一个格子**，消费者拿到的是裸指针。
⇒ `pwmchip_remove()` 一执行，`kfree(chip->pwms)` 就让所有消费者的指针悬空。
这条设计债的完整评价在 03 篇 Part C。

### §1.0.B 三份"配置"：`state` / `args` / `last`

`struct pwm_device` 里同时挂着三份配置数据，**含义完全不同**：

| 字段 | 类型 | 谁写 | 什么时候写 | 含义 |
|---|---|---|---|---|
| `pwm->args` | `struct pwm_args`（**2 字段**） | 核心 | `pwm_get()` 时从 DT / 查表填一次 | **板级参考值**：这块板子上"应该"用什么周期和极性 |
| `pwm->state` | `struct pwm_state`（**5 字段**） | 核心 | 每次 `pwm_apply_state()` 成功后 | **上次请求值**：消费者最后一次要求的配置 |
| `pwm->last` | `struct pwm_state` | 核心 | **只在 `CONFIG_PWM_DEBUG`** 下 | **上次实现值**：驱动 `.get_state()` 回读出来的 |

三份的关系可以画成：

```
DT / pwm_lookup ──→ pwm->args   （参考值，只读，永不变）
                        │  pwm_init_state() 拿它做起点
                        ▼
消费者构造的 state ──→ pwm->state （请求值，每次 apply 后更新）
                        │  只有 CONFIG_PWM_DEBUG 才继续往下
                        ▼
           驱动 .get_state() ──→ pwm->last （实现值，默认配置下永远是空的）
```

⚠️ **默认配置下 `pwm->last` 是死字段**——`CONFIG_PWM_DEBUG=n` 时没有任何代码写它。
`struct pwm_device` 的 kerneldoc 直接写明 `@last: last implemented state (for PWM_DEBUG)`。
所以 `struct pwm_device` 有 9 个字段，但**生产内核里实际用 8 个**。

⚠️ `pwm_args` 的 kerneldoc 专门留了一句警告
（`include/linux/pwm.h:30-33`）：

> **Do not confuse this with the PWM state**: PWM arguments represent the initial
> configuration that users want to use on this PWM device rather than the current
> PWM hardware state.

内核自己都觉得这里容易混——说明这组关系确实是头号陷阱。

### §1.0.C 三个编号：`chip->base` / `pwm->pwm` / `pwm->hwpwm`

| 名字 | 口径 | 范围 | 谁用它 |
|---|---|---|---|
| `chip->base` | 本 chip 的**全局起始编号** | 0 .. 1023 | sysfs 目录名 `pwmchip<base>`；核心的位图与 radix tree |
| `pwm->pwm` | 该通道的**全局编号** = `base + hwpwm` | 0 .. 1023 | radix tree 的 key；`pwm_request(id)` 的入参 |
| `pwm->hwpwm` | 该通道在**本 chip 内**的序号 | 0 .. `npwm-1` | ⚠️ **驱动的寄存器索引只能用这个**；sysfs 目录名 `pwm<hwpwm>` |

⚠️ 系统里只有一个 PWM 控制器时 `base == 0`，于是 `pwm->pwm == pwm->hwpwm`，
用错也测不出来。插上第二个控制器立刻错位。判据：
**`pwm->pwm` 出现在 `.apply` / `.request` / 寄存器地址计算里就是 bug。**

⚠️ 还有一处口径混用会让人困惑：sysfs 里的 `pwmchip4/pwm0`，
**4 是全局编号、0 是本地编号**——同一条路径上两个数字口径不同
（`drivers/pwm/sysfs.c:514` 用 `chip->base`，`:263` 用 `hwpwm`）。

### §1.0.D 三层嵌套：驱动私有结构 → `pwm_chip` → `pwm_ops`

```c
struct foo_pwm {              /* 驱动自己的结构，驱动分配 */
        void __iomem   *base;
        struct clk     *clk;
        struct pwm_chip chip;         /* ← 内嵌，不是指针 */
};

static const struct pwm_ops foo_ops = { ... };   /* ← 通常是 static const 全局 */
```

| 方向 | 怎么走 |
|---|---|
| `pwm_chip` → 驱动私有结构 | `container_of(chip, struct foo_pwm, chip)` |
| `pwm_device` → `pwm_chip` | `pwm->chip`（直接字段） |
| `pwm_device` → 驱动的**通道级**私有数据 | `pwm_get_chip_data(pwm)`（对应 `pwm->chip_data`） |
| `pwm_chip` → `pwm_ops` | `chip->ops`（`const` 指针） |

⚠️ **`pwm_chip` 里没有 `void *priv` 字段**，所以驱动**必须**用内嵌 + `container_of`。
想用 `dev_get_drvdata(chip->dev)` 也可以，但前提是驱动确实把私有结构设成了 drvdata。
树内两种写法都有，`container_of` 是主流。

⚠️ **通道级私有数据（`chip_data`）与 chip 级私有数据是两件事**，
而且 `pwm_set_chip_data()` **名字里的 "chip" 是误导**——它设的是
`pwm->chip_data`，即**单条通道**的私有指针，不是整个 chip 的。
这是本子系统命名最差的一处，见 §11.2。

### §1.0.E `pwm_device` 与消费者：谁的生命周期长

```
     provider 驱动                          消费者驱动
   probe                                  probe
     └─ pwmchip_add()                       └─ devm_pwm_get()  ──→ 拿到裸指针
          分配 chip->pwms[]                        置 PWMF_REQUESTED
          ...                                      ...
   remove                                  remove
     └─ pwmchip_remove()                     └─ devm 自动 pwm_put()
          kfree(chip->pwms)  ⚠️                    清 PWMF_REQUESTED
```

| 保护机制 | 挡得住什么 | 挡不住什么 |
|---|---|---|
| `pwm_ops.owner` + `try_module_get()` | provider 模块被 `rmmod` | provider 驱动自己在 `remove()` 里调 `pwmchip_remove()` |
| `PWMF_REQUESTED` 标志 | 第二个消费者抢同一条通道（`-EBUSY`） | **不挡 `pwmchip_remove()`**——它根本不检查这个标志 |
| `device_link_add()`（`core.c:707`） | **PM 顺序**：suspend 时消费者先于 provider | 不影响 remove 顺序（用的是 `DL_FLAG_AUTOREMOVE_CONSUMER`，不是 `DL_FLAG_STATELESS` 之外的强制解绑） |

⚠️ 结论要记住：**pwm 只有"模块级"的生命周期保护，没有"对象级"的**。
消费者能安全持有 `struct pwm_device *` 的唯一理由是"provider 模块被引用计数钉住了"。
provider 是内建的（`bool`，不是 `tristate`）时，这层保护也不存在。

### §1.0.F 两个占用标志：`PWMF_REQUESTED` 与 `PWMF_EXPORTED`

```c
enum {
        PWMF_REQUESTED = 0,
        PWMF_EXPORTED = 1,
};       /* include/linux/pwm.h:43-46 */
```

| 标志 | 谁置 | 含义 | 与另一个的关系 |
|---|---|---|---|
| `PWMF_REQUESTED`（bit 0） | `pwm_device_request()`（`core.c:92`） | 有人（内核消费者**或** sysfs）在用这条通道 | — |
| `PWMF_EXPORTED`（bit 1） | `pwm_export_child()`（`sysfs.c:241`） | 这条通道被 sysfs export 出去了 | ⚠️ **export 会先 request** ⇒ 两位同时置上 |

⚠️ 两位**不是互斥的**，而是**嵌套的**：`export_store()` 先调
`pwm_request_from_chip(chip, hwpwm, "sysfs")`（`sysfs.c:326`）拿到占用权，
**再**调 `pwm_export_child()`（`:330`）置 `PWMF_EXPORTED`（`:247`）。
所以 `PWMF_EXPORTED` 置上时 `PWMF_REQUESTED` 必然也置上；
反过来不成立（内核消费者只置 `REQUESTED`）。

⇒ 实际后果：**一条通道被内核驱动占着的时候，sysfs `echo N > export` 会失败
（`-EBUSY`），反之亦然。** 这是"背光在用的 PWM 无法从用户态调"的机制来源。

⚠️ 这是个 **匿名 `enum`，不是 `#define`**。
grep `#define PWMF_` 一无所获——找它要 grep `PWMF_`，别加 `define`。

---

## §1.1 核心抽象与消费者的全景图

pwm 是**框架型**子系统（没有 `bus_type`、没有 `probe`、没有 `match`），
所以全景图的形状不是"总线两侧"，而是"框架居中、两边各是一族驱动"：

```
   ┌───────────────────────── 用户态 ─────────────────────────┐
   │  /sys/class/pwm/pwmchipN/{npwm,export,unexport}          │
   │  /sys/class/pwm/pwmchipN/pwmX/{period,duty_cycle,        │
   │                                enable,polarity,capture}  │
   │  /sys/kernel/debug/pwm   （只读，全系统一览）              │
   └────────────────────────────┬─────────────────────────────┘
                                │  sysfs.c（546 行，0 导出）
   ┌────────────────────────────▼─────────────────────────────┐
   │  内核消费者（35 处调用点，跨 12 个子系统）                  │
   │   backlight(pwm_bl) · input/misc(蜂鸣器/振动) · leds       │
   │   regulator(pwm-regulator) · hwmon(pwm-fan) · clk · bus   │
   └────────────────────────────┬─────────────────────────────┘
                                │ pwm_get / pwm_apply_state / pwm_put
   ╔════════════════════════════▼═════════════════════════════╗
   ║  pwm 核心（core.c 1188 行，17 个导出符号）                  ║
   ║                                                           ║
   ║   全局状态：pwm_chips 链表 · allocated_pwms 位图(1024)      ║
   ║             pwm_tree radix · pwm_lookup_list 查表          ║
   ║             两把锁：pwm_lock · pwm_lookup_lock             ║
   ║                                                           ║
   ║   对象：pwm_chip ──1:N──→ pwm_device                      ║
   ║              │                 ├── args  （板级参考）       ║
   ║              │                 ├── state （上次请求）       ║
   ║              │                 └── last  （PWM_DEBUG 专用） ║
   ║              ▼                                            ║
   ║   ══ 唯一的可插拔层：struct pwm_ops（6 成员，只 .apply 必填）══ ║
   ╚════════════════════════════┬═════════════════════════════╝
                                │ ops->apply / ops->get_state / ...
   ┌────────────────────────────▼─────────────────────────────┐
   │  PWM 控制器驱动（62 个 .c，约 21200 行）                    │
   │   SoC 内建定时器 · I2C 扩展芯片(pca9685) · regulator 联动   │
   │   arm64 常见：pwm-meson / pwm-rockchip / pwm-sun4i /       │
   │              pwm-mediatek / pwm-imx27 / pwm-tegra         │
   └───────────────────────────────────────────────────────────┘
```

⚠️ 图上有两条**不存在**的连线，要特别记住它们不存在：

1. **消费者与 sysfs 之间没有连线。** 两者是**互斥的占用者**（§1.0.F）：
   一条通道被内核消费者 request 了，sysfs 就 export 不了。
   ⇒ "为什么我 `echo 0 > export` 得到 Device or resource busy" 的答案在这里。
2. **`pwm_chip` 与 driver core 之间没有 `device_driver`。** 控制器驱动是
   platform/i2c/spi 驱动，它们**自己的**总线负责 probe；`pwm_chip` 只是这些驱动
   向 pwm 框架登记的一张表，不参与任何匹配。

---

## §1.2 本子系统有几层可插拔

**只有一层：`struct pwm_ops`。**

| 可插拔点 | 结构 | 成员数 | 必填 | 运行时可换 |
|---|---|---|---|---|
| 控制器操作 | `struct pwm_ops` | 6 | **只有 `.apply`** | ✘ 注册时定死 |
| DT 翻译 | `chip->of_xlate` 函数指针 | 1 | 否（核心自动填默认值） | ✘ |

⚠️ 和 tty（两层：ldisc + driver）、clk（多层）比，pwm 的可插拔面**异常简单**：
**一层，一个必填回调。** 这是它 1188 行核心就能撑起 62 个驱动的原因。

⚠️ `chip->of_xlate` 严格说也是一个可插拔点，但它只有两个现成实现
（`of_pwm_xlate_with_flags` 和 `of_pwm_single_xlate`），而且
**不填就自动填第一个**（`of_pwmchip_add()`，`core.c:181-196`）。
树内自定义 xlate 的驱动极少，所以实践上按"一层"理解就够。

---

## §2 `struct pwm_chip`：控制器

`include/linux/pwm.h:295-308`，**8 个字段**。

| 字段 | 类型 | 谁填 | 说明 |
|---|---|---|---|
| `dev` | `struct device *` | **驱动，必填** | 空则 `pwmchip_add()` 返回 `-EINVAL`。同时决定 DT 节点、sysfs 父设备、device link 目标 |
| `ops` | `const struct pwm_ops *` | **驱动，必填** | 空或 `.apply` 空则 `-EINVAL` |
| `base` | `int` | ⚠️ **核心（输出）** | 全局起始编号，由 `alloc_pwms()` 分配后**无条件覆盖**驱动写的值 |
| `npwm` | `unsigned int` | **驱动，必填** | 通道数；为 0 则 `-EINVAL` |
| `of_xlate` | 函数指针 | 可选 | 不填则核心填 `of_pwm_xlate_with_flags` |
| `of_pwm_n_cells` | `unsigned int` | 可选 | 不填则从 DT 的 `#pwm-cells` 读，**读不到默认 2** |
| `list` | `struct list_head` | 核心 | 挂在全局 `pwm_chips` 上。注释：`only used internally` |
| `pwms` | `struct pwm_device *` | 核心 | `kcalloc` 出来的通道数组。注释：`allocated by the framework` |

三条要点：

1. **没有 `void *priv`。** 私有数据靠内嵌 + `container_of`（§1.0.D）。
2. **没有 `struct module *owner`。** 模块引用计数在 `pwm_ops.owner` 里，
   不在 chip 里——这是和 `tty_driver`、`watchdog_device` 等结构不同的一点。
3. **没有任何锁字段。** `pwm_chip` 完全靠核心的全局 `pwm_lock` 保护，
   驱动想给自己加锁必须加在自己的私有结构里。

⚠️ `base` 那一行值得再强调：`alloc_pwms()` 的签名是
`static int alloc_pwms(unsigned int count)`（`drivers/pwm/core.c:40`），
**只有 count 一个参数**。驱动无法请求特定编号。详见 01 篇讲 provider API 的那节。

---

## §3 `struct pwm_device`：一条通道

`include/linux/pwm.h:79-90`，**9 个字段**（生产内核实际用 8 个）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `label` | `const char *` | 请求者的名字。DT 路径填的是消费者节点全名；sysfs 路径填 `"sysfs"` |
| `flags` | `unsigned long` | 位图，只用两位：`PWMF_REQUESTED` / `PWMF_EXPORTED`（§1.0.F） |
| `hwpwm` | `unsigned int` | ⚠️ **本 chip 内序号**——驱动的寄存器索引 |
| `pwm` | `unsigned int` | 全局编号 = `chip->base + hwpwm` |
| `chip` | `struct pwm_chip *` | 回指所属控制器 |
| `chip_data` | `void *` | ⚠️ **通道级**私有数据（名字误导，见 §11.2） |
| `args` | `struct pwm_args` | 板级参考值（§1.0.B） |
| `state` | `struct pwm_state` | 上次请求值（§1.0.B） |
| `last` | `struct pwm_state` | ⚠️ **仅 `CONFIG_PWM_DEBUG`**；默认配置下是死字段 |

⚠️ 注意 `args` / `state` / `last` 是**内嵌的实体**，不是指针 ⇒
`struct pwm_device` 本身就装着三份配置的完整拷贝。
所以 `sizeof(struct pwm_device)` 不小，而 `chip->pwms` 是
`kcalloc(npwm, sizeof(*pwm))` ——npwm 很大的控制器（如 pca9685 的 16 路）
一次分配的量比看起来多。

⚠️ **没有 `kref`、没有 `refcount`、没有 `struct device`。**
`pwm_device` 不是一个 driver-core 设备，`/sys/class/pwm/pwmchipN/pwmX/`
那个目录是 sysfs 层**另外造**的（`struct pwm_export`，见 §8），
不是 `pwm_device` 自带的。

---

## §4 三份配置的具体字段

### §4.1 `struct pwm_state`：请求值（5 字段）

`include/linux/pwm.h:59-65`：

```c
struct pwm_state {
        u64 period;
        u64 duty_cycle;
        enum pwm_polarity polarity;
        bool enabled;
        bool usage_power;
};
```

| 字段 | 单位/取值 | 语义 |
|---|---|---|
| `period` | **纳秒**，`u64` | 周期。为 0 时 `.apply` 的行为由驱动决定，通常等价于关闭 |
| `duty_cycle` | **纳秒**，`u64` | 高电平时长（极性归一化后的"有效电平"时长），**不是百分比** |
| `polarity` | `PWM_POLARITY_NORMAL` / `_INVERSED` | 见 §9.1 |
| `enabled` | `bool` | 输出是否使能 |
| `usage_power` | `bool` | ⚠️ **允许驱动牺牲波形形状换取功率精度**——最容易被忽略的一个 |

⚠️ **`period` / `duty_cycle` 是 `u64` 纳秒，全篇统一。**
但 `struct pwm_capture`（§6）用的是 `unsigned int`——**同一个子系统里两种宽度**，
见 §11.3。

⚠️ `usage_power` 的完整语义（kerneldoc `include/linux/pwm.h:52-57`）：置位后
驱动**可以**改变占空比在周期内的分布，只保证平均功率对——
即输出可能不再是"一段高、一段低"的单脉冲，而是散开的多个脉冲。
对 LED 亮度、加热器这类只看平均功率的负载是纯收益；
对**伺服舵机、需要精确脉宽的传感器时序**是灾难。
⇒ **默认必须是 false。** `pwm_init_state()` 和 `pwm_apply_args()` 都显式清零它。

⚠️ `usage_power` **没有对应的 sysfs 属性**，也**不在 tracepoint 的 5 个字段里**
（07 篇 §7.1）。它只能由内核消费者设置，用户态碰不到，也观测不到。

### §4.2 `struct pwm_args`：板级参考值（2 字段）

`include/linux/pwm.h:38-41`：

```c
struct pwm_args {
        u64 period;
        enum pwm_polarity polarity;
};
```

**只有周期和极性，没有占空比、没有 enabled。** 这个字段选择本身就是设计意图：
"板子的 PWM 应该跑多快、哪个电平算有效"是**硬件属性**（写在 DT 里）；
"当前该输出多少"是**运行期决策**，不属于板级信息。

| 来源 | 谁填 |
|---|---|
| DT：`pwms = <&pwm 0 50000 PWM_POLARITY_INVERTED>` 的第 2、3 个 cell | `of_pwm_xlate_with_flags()`（`core.c:124`） |
| 静态查表：`PWM_LOOKUP(..., period, polarity)` 的后两个参数 | `pwm_get()` 的查表分支 |

⚠️ **`pwm_args` 填完就再也不变。** 它不随 `pwm_apply_state()` 更新，
所以任何时候读 `pwm->args` 拿到的都是板级值。这是 `pwm_apply_args()`
（`include/linux/pwm.h:522`）能把通道"复位到板级默认"的原因。

### §4.3 三个操作 `state` 的内联辅助

这三个函数全在 `include/linux/pwm.h` 里，不是导出符号——它们是纯计算。

| 函数 | 位置 | 干什么 | 边界行为 |
|---|---|---|---|
| `pwm_init_state()` | `include/linux/pwm.h:179` | 用 `args` 初始化一个 `state` | 设 **4 个**字段：`period`/`polarity` 取自 args，`duty_cycle = 0`，`usage_power = false`。⚠️ **不动 `enabled`** |
| `pwm_get_relative_duty_cycle()` | `include/linux/pwm.h:209` | 纳秒占空比 → 相对刻度（如 0–255） | ⚠️ `state->period == 0` 时**返回 0**，不报错 |
| `pwm_set_relative_duty_cycle()` | `include/linux/pwm.h:237` | 相对刻度 → 纳秒占空比 | `!scale \|\| duty_cycle > scale` 时返回 **`-EINVAL`**；否则 `DIV_ROUND_CLOSEST_ULL` |

⚠️ **`pwm_init_state()` 不设置 `enabled`。**
它设 4 个字段，`enabled` 保持传入结构里的原值。
所以标准写法必须是"先 `pwm_init_state()`，再显式写 `state.enabled`"——
栈上未初始化的 `struct pwm_state` 直接交给 `pwm_init_state()` 会带一个随机的 `enabled`。
（`pwm_apply_args()` 不同，它显式设 `enabled = false`。）

⚠️ 一对函数的**取整方向不同**：`get` 用 `DIV_ROUND_CLOSEST`，`set` 也用
`DIV_ROUND_CLOSEST_ULL` ⇒ `set(x)` 之后 `get()` 不保证还是 `x`。
"设 128/255 读回来是 127" 属于正常，不是 bug。

---

## §5 `struct pwm_ops`：唯一的可插拔层（6 成员）

`include/linux/pwm.h:272-282`：

| 成员 | 必填 | 上下文 | 语义 |
|---|---|---|---|
| `.request` | 否 | 可睡眠 | 通道被 request 时调，在 `.get_state` **之前**（`core.c:102-108`）；上电、取时钟、挂 `chip_data` |
| `.free` | 否 | 可睡眠 | 通道被 put 时调 |
| `.capture` | 否 | 可睡眠 | 测量输入波形；不填则 `pwm_capture()` 返回 `-ENOSYS` |
| `.apply` | ⚠️ **是** | 可睡眠 | **唯一必填项**。整个子系统的核心 |
| `.get_state` | 否（**但强烈建议**） | 可睡眠 | ⚠️ **每次 request 时调一次**（`core.c:110`）——kerneldoc 说的"注册时"是错的 |
| `.owner` | 实际必填 | — | `THIS_MODULE`；核心用它做 `try_module_get()` |

⚠️ **只有 `.apply` 被 `pwm_ops_check()` 强制**（`drivers/pwm/core.c:236-247`）。
缺 `.get_state` 不会导致注册失败，只会在 `CONFIG_PWM_DEBUG=y` 下打一条
`"Please implement the .get_state() callback\n"`（`core.c:244`）——
**默认配置下连这条都没有，静默通过**。

⚠️⚠️ `.get_state` 的调用时机是本子系统最反直觉的一点，**而且 kerneldoc 是错的**：
`include/linux/pwm.h:267-269` 说它 "only called once per PWM device when the PWM chip
is registered"，但 `pwmchip_add()` 的通道初始化循环（`core.c:284-292`）
**根本没有调它**。唯一的调用点是 `pwm_device_request()`（`core.c:110-111`）
⇒ **每次通道被 request 时调一次**（`pwm_get()` 或 sysfs export 各算一次）。

⇒ 对驱动的要求：`.get_state` **必须可重入**，不能假设"只在开机时跑一次"。
⇒ 对消费者的意义：`pwm_get_state()` **只在刚 request 之后**反映硬件，
之后退化成"上次请求值"——核心不会再回读。这是 §1.0.B 那张图里
"默认配置下 `last` 永远是空的"的另一面。

⚠️ **`.owner` 放在 ops 里而不是 chip 里**（§2 要点 2）。
后果：多个 chip 共享一个 `static const struct pwm_ops` 时，它们共享同一个 owner——
这是正常的、也是期望的（同一个驱动模块）。

⚠️ **`.config` / `.enable` / `.disable` / `.set_polarity` 这四个旧回调在 6.1 里已经不存在。**
官方文档 `Documentation/driver-api/pwm.rst:153-156` 还在用"encouraged to implement
`->apply()` instead of the legacy ..."的措辞，读起来像"旧的还能用"——
**代码里已经没有了**，`struct pwm_ops` 就 6 个成员。以代码为准。

---

## §6 `struct pwm_capture`：输入测量（2 字段）

`include/linux/pwm.h:256-259`：

```c
struct pwm_capture {
        unsigned int period;
        unsigned int duty_cycle;
};
```

⚠️ **单位是纳秒，但类型是 `unsigned int`（32 位）** ——
与 `pwm_state` 的 `u64` 不一致。32 位纳秒的上限是
`4294967295 ns ≈ 4.295 秒` ⇒ **周期长于约 4.29 秒的信号无法用 capture 表达**。
`pwm_state` 侧完全没有这个限制。这是子系统内一处真实的类型不对称，见 §11.3。

⚠️ capture 是**唯一有超时的路径**：sysfs 侧硬编码 1 秒
（`pwm_capture()` 的 `timeout` 参数由 `drivers/pwm/sysfs.c` 传入）。
而且它是**唯一在调用驱动回调期间持有全局 `pwm_lock` 的路径**
（`core.c:610-612`）——一个慢 capture 会卡住全系统的 pwm 注册和 request。
这条锁不对称是 03 篇 Part C 的重点之一。

---

## §7 全局状态：六个变量，两把锁

pwm 的全部全局状态挤在 `drivers/pwm/core.c:26-33` 八行里：

```c
#define MAX_PWMS 1024                                   /* :26 */

static DEFINE_MUTEX(pwm_lookup_lock);                   /* :28 */
static LIST_HEAD(pwm_lookup_list);                      /* :29 */
static DEFINE_MUTEX(pwm_lock);                          /* :30 */
static LIST_HEAD(pwm_chips);                            /* :31 */
static DECLARE_BITMAP(allocated_pwms, MAX_PWMS);        /* :32 */
static RADIX_TREE(pwm_tree, GFP_KERNEL);                /* :33 */
```

| 变量 | 类型 | 保护它的锁 | 用途 |
|---|---|---|---|
| `pwm_chips` | 链表 | `pwm_lock` | 所有已注册 chip；`pwmchip_find_by_name()`（`core.c:69`）和 debugfs 遍历它 |
| `allocated_pwms` | 1024 位的位图 | `pwm_lock` | 全局编号分配。`alloc_pwms()` 在这里找连续空位 |
| `pwm_tree` | radix tree | `pwm_lock` | 全局编号 → `pwm_device *`；只服务已废弃的 `pwm_request(id)` 路径 |
| `pwm_lookup_list` | 链表 | `pwm_lookup_lock` | 静态查表项（`pwm_add_table()` 加进来的） |
| `pwm_lock` | mutex | — | 保护前三个 |
| `pwm_lookup_lock` | mutex | — | 只保护 `pwm_lookup_list` |

四条结构性事实：

1. **`MAX_PWMS` 是 1024，全系统共用一个位图。** 不是每 chip 的上限，
   而是"整机所有 PWM 通道加起来不能超过 1024"。用满了 `pwmchip_add()`
   返回 `-ENOSPC`。带 16 路 PWM 的 I2C 扩展芯片挂 64 片就到顶了。
2. **`allocated_pwms` 要的是连续空位。** `alloc_pwms()` 用
   `bitmap_find_next_zero_area()` 找 `count` 个连续的 0 位
   ⇒ 反复加载/卸载不同 `npwm` 的驱动会**产生碎片**，理论上可能在总数没满时
   就分配失败。实践中没人碰到过，但机制上确实存在。
3. **`pwm_tree` 只服务废弃路径。** 唯一的查询者是
   `pwm_to_device()`（`core.c:35`），只被 `pwm_request()`（已废弃）用到。
   现代路径（`pwm_get()` / `of_pwm_get()`）走的是 `pwm_chips` 链表 + `chip->pwms[]` 数组，
   完全不碰 radix tree。⇒ 这棵树是**为了兼容一个没人该用的 API 而维护的纯开销**。
4. **两把锁互不嵌套。** `pwm_lookup_lock` 只在 `pwm_add_table()` /
   `pwm_remove_table()` / `pwm_get()` 的查表分支里出现，从不与 `pwm_lock` 同时持有。
   ⇒ pwm 没有锁序问题，但代价是 `pwm_lock` 粒度极粗（见 §7.1）。

### §7.1 `pwm_lock` 的覆盖范围极不均匀

| 路径 | 拿 `pwm_lock` 吗 | 备注 |
|---|---|---|
| `pwmchip_add()` / `pwmchip_remove()` | ✔ 全程 | 但 `pwmchip_sysfs_export()` 被特意放在 `mutex_unlock` **之后**（`core.c:310-311`） |
| `pwm_request()` / `pwm_request_from_chip()` / `pwm_put()` | ✔ | 占用标志的置位/清位 |
| `pwm_get()` | ✔（查找期间） | 查表分支另外拿 `pwm_lookup_lock` |
| **`pwm_apply_state()`** | ⚠️ **完全不拿任何锁** | 热路径，见下 |
| **`pwm_capture()`** | ⚠️ **拿着锁调驱动回调** | `core.c:610-612` |

⚠️ 这就是官方文档 `Documentation/driver-api/pwm.rst:165-172` 那节
Locking 自陈 "should be fixed soon" 指的东西：
**最热的路径（apply）零加锁，最冷的路径（capture）拿全局锁调驱动。**

⇒ 对驱动作者的直接后果：**`.apply` 必须自己处理并发**。
两个线程同时对同一条通道 `pwm_apply_state()`，核心不会串行化它们，
`pwm->state` 的读改写也不是原子的。需要互斥就在驱动私有结构里加自己的锁。
（sysfs 路径例外：`struct pwm_export` 自带一把 `lock`，见 §8。）

---

## §8 `struct pwm_export`：sysfs 层的私有结构（4 字段）

`drivers/pwm/sysfs.c:17-22`，**不在任何头文件里**，`sysfs.c` 私有：

```c
struct pwm_export {
        struct device child;
        struct pwm_device *pwm;
        struct mutex lock;
        struct pwm_state suspend;
};
```

| 字段 | 作用 |
|---|---|
| `child` | ⚠️ **内嵌的 `struct device`**——`/sys/class/pwm/pwmchipN/pwmX/` 那个目录就是它 |
| `pwm` | 指回被 export 的通道 |
| `lock` | ⚠️ **本子系统唯一一把"每通道"的锁**，只保护 sysfs 路径 |
| `suspend` | ⚠️ suspend 前保存的 state，resume 时恢复 |

四条要点：

1. **`pwm_device` 自己不是 `struct device`，`pwm_export` 才是。**
   所以通道级 sysfs 目录的生命周期与 `pwm_device` 无关——
   `pwm_export` 是 export 时 `kzalloc` 的，unexport 时靠
   `device_unregister()` + release 回调释放。
2. **这把 `lock` 只覆盖 sysfs。** 内核消费者调 `pwm_apply_state()` 完全绕过它
   ⇒ "sysfs 侧串行化了，内核侧没有"。不过两者互斥占用（§1.0.F），
   实际不会同时操作同一条通道，所以这不是 bug 而是分层的自然结果。
3. **只有 export 出去的通道有 PM。** `pwm_class_suspend()`（`sysfs.c:443`）/
   `pwm_class_resume()`（`sysfs.c:484`）挂在 class 上，遍历的是
   已注册的 `pwm_export` 子设备 ⇒ **没 export 的通道，suspend/resume 时核心什么都不做**。
4. ⚠️ 第 3 点与官方文档 `Documentation/driver-api/pwm.rst:82-84` 那句
   "All consumers should really be reconfiguring the PWM upon resume"
   是同一件事的两面：**内核消费者的 PWM 状态跨 suspend 不保证保持，
   必须自己在 resume 里重新配置。**

---

## §9 枚举与标志位

### §9.1 `enum pwm_polarity`（2 值）

`include/linux/pwm.h:20-23`：

```c
enum pwm_polarity {
        PWM_POLARITY_NORMAL,
        PWM_POLARITY_INVERSED,
};
```

⚠️ **拼写陷阱**：内核枚举是 `INVERSED`，而设备树绑定里的宏是
`PWM_POLARITY_INVERTED`（`include/dt-bindings/pwm/pwm.h:13`，值 `1 << 0`）。
**两个不同的词、两个不同的命名空间、值恰好都能表示"反相"**：

| 名字 | 在哪 | 是什么 | 值 |
|---|---|---|---|
| `PWM_POLARITY_INVERSED` | `include/linux/pwm.h:22` | C 枚举值 | 1 |
| `PWM_POLARITY_INVERTED` | `include/dt-bindings/pwm/pwm.h:13` | DT flags **位掩码** | `1 << 0` = 1 |

⇒ 写 C 代码用 `INVERSED`，写 DTS 用 `INVERTED`。写错的那个编译不过，
所以不会出错——但会浪费时间在"这个宏怎么找不到"上。

⚠️ 语义要点：极性影响的是"`duty_cycle` 描述哪个电平的时长"。
`NORMAL` 时 `duty_cycle` 是高电平时长；`INVERSED` 时是低电平时长。
⇒ 同一组 `period`/`duty_cycle` 换极性，**平均功率会反过来**。
用在背光上就是"亮度反了"。

### §9.2 `PWMF_*` 标志（2 位）

见 §1.0.F。补一条查找技巧：它是**匿名 `enum`**，
`grep '#define PWMF_'` 一无所获，要 `grep -n 'PWMF_' include/linux/pwm.h`。

### §9.3 DT flags 的完整取值

`include/dt-bindings/pwm/pwm.h` 全文只有一个宏（`:13`）：`PWM_POLARITY_INVERTED (1 << 0)`。

⇒ **DTS 里 `pwms = <&pwm 0 50000 flags>` 的第 4 个 cell 目前只有 1 个可用位。**
`of_pwm_xlate_with_flags()`（`core.c:124`）读它，把 bit 0 翻译成
`args.polarity = PWM_POLARITY_INVERSED`。
`#pwm-cells = <2>` 的旧绑定没有这个 cell，极性一律 `NORMAL`。

---

## §10 生命周期对照表

| 对象 | 谁分配 | 什么时候 | 谁释放 | 什么时候 | 有引用计数吗 |
|---|---|---|---|---|---|
| 驱动私有结构（含内嵌 `pwm_chip`） | 驱动（通常 `devm_kzalloc`） | probe | devm | remove | 由 driver core 管 |
| `chip->pwms[]` 数组 | **核心** `kcalloc` | `pwmchip_add()` | **核心** `kfree` | `pwmchip_remove()` | ✘ **无** |
| 单个 `pwm_device` | ——（数组的一格） | 同上 | ——（不能单独释放） | 同上 | ✘ **无** |
| `pwm_device` 的占用权 | 核心置 `PWMF_REQUESTED` | `pwm_get()` / sysfs export | 核心清标志 | `pwm_put()` / unexport | 布尔标志，非计数 |
| provider 模块引用 | `try_module_get(ops->owner)` | `pwm_device_request()` | `module_put` | `pwm_put()` | ✔ **模块级** |
| `pwm_export` | sysfs 层 `kzalloc` | `echo N > export` | device release | `echo N > unexport` 或 chip 注销 | ✔ `struct device` 的 kref |
| `pwm_lookup` 项 | 调用者（通常静态数组） | `pwm_add_table()` | 调用者 | `pwm_remove_table()` | ✘ 无 |

⚠️ 表里唯一有真正对象级引用计数的是 `pwm_export`——**因为它内嵌了 `struct device`**。
`pwm_device` 和 `pwm_chip` 都没有。这解释了 §1.0.E 的结论：
**pwm 的生命周期安全完全依赖"provider 模块被钉住"这一层间接保护。**

⚠️ `pwm_put()` 的行为要单记：它**只清占用标志、调 `.free`、`module_put`，
不关闭输出**。同理 devm 版本也不关。所以"驱动 remove 之后灯还亮着"
是完全符合实现的——消费者必须自己先 `pwm_disable()`。

---

## §11 命名陷阱

pwm 的结构不多，但名字起得相当糟。这四处每一处都真实地绕过人：

### §11.1 `pwm->pwm`

`struct pwm_device` 里有个字段就叫 `pwm`。于是代码里出现
`pwm->pwm`、`pwm->pwm == pwm->hwpwm` 这种读起来像绕口令的表达式。
含义见 §1.0.C：它是**全局编号**。

### §11.2 `pwm_set_chip_data()` 设的不是 chip 的数据

| 名字 | 实际操作的字段 | 粒度 |
|---|---|---|
| `pwm_set_chip_data(pwm, data)` | `pwm->chip_data` | ⚠️ **单条通道** |
| `pwm_get_chip_data(pwm)` | `pwm->chip_data` | ⚠️ **单条通道** |
| chip 级私有数据 | ——**没有 API**，靠 `container_of` | 整个 chip |

⚠️ 函数体只有三行（`core.c:213-221`）：`!pwm` 返回 `-EINVAL`，
否则直接赋值返回 0。**没有任何状态检查**——老内核里有过
`PWMF_REQUESTED` 检查，6.1 已经去掉了。⇒ 在 `probe()` 里对
`&chip->pwms[i]` 调它也能成功。

⚠️ 但 `pwm_put()` 会调 `pwm_set_chip_data(pwm, NULL)`（`core.c:1030`）
⇒ **释放通道时这个指针被清零**。驱动的 `.request` 必须每次重新设置。

### §11.3 两种时间宽度

| 结构 | 字段 | 类型 | 上限 |
|---|---|---|---|
| `struct pwm_state` | `period` / `duty_cycle` | `u64` | 约 584 年 |
| `struct pwm_capture` | `period` / `duty_cycle` | `unsigned int` | ⚠️ 约 **4.295 秒** |

单位都是纳秒，宽度不同。输出侧能表达的周期，输入侧未必能表达。

### §11.4 `pwm_free()` 与 `pwm_put()`

两个导出符号，**做完全一样的事**——`pwm_free()`（`core.c:434-437`）的函数体
就一行 `pwm_put(pwm)`。`pwm_free` 是旧名字，配 `pwm_request` 那套废弃 API；
新代码一律用 `pwm_put`。⚠️ 但两者都在导出表里，grep 时会同时命中。

### §11.5 `pwm_apply_args()` 与 `pwm_adjust_config()`

名字都像"调整配置"，做的事完全不同：

| 函数 | 位置 | 干什么 |
|---|---|---|
| `pwm_apply_args()` | `include/linux/pwm.h:522`（内联，非导出） | **复位到板级默认**：`enabled = false`，period/polarity 取自 `args`，`usage_power = false`，然后 apply |
| `pwm_adjust_config()` | `drivers/pwm/core.c:626`（导出） | **保留当前占空比比例**，把周期换算到 `args.period` 上 |

⇒ 前者会**关掉输出**，后者不会。boot 阶段接管 bootloader 留下的 PWM 时，
选错这两个的差别就是"屏幕黑一下"和"屏幕不黑"。

---

## §12 本篇的边界

- **只讲结构与关系**。函数签名、返回值、错误码在 01 篇；
  调用时序、锁的实际用法、设计评价在 03 篇。
- **不讲具体驱动的私有结构**。62 个控制器驱动各有自己的私有结构，
  它们与本篇无关——本篇只说清"驱动私有结构要内嵌 `pwm_chip`"这一条约定。
- **`struct pwm_lookup` 只列了字段**（§7 表内），完整的查表匹配算法
  （dev_id/con_id 的模糊打分与优先级）在 03 篇讲发现路径的那部分。
- **ACPI 相关字段与路径一律裁掉**（arm64-only），取证与理由记在 99 篇 §2.7。
- **`struct pwm_capture` 的驱动侧实现**（哪些驱动真的填了 `.capture`）在 04 篇。

**下一篇**：03 篇讲机制——注册流程、`pwm_apply_state()` 的完整判定链、
三条发现路径、`CONFIG_PWM_DEBUG` 改变的执行路径，以及 Part C 的设计债清单。
