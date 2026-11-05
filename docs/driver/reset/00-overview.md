---
title: 总览
---

# reset 00 —— 总览与复习入口

**这一篇的定位是"复习入口"**：隔几个月回来，只读这一篇就能恢复对 reset 子系统的全貌认识；
需要细节再按 §8 的导读跳到对应篇目。

范围：`drivers/reset/` + `include/linux/reset.h` + `include/linux/reset-controller.h`，
Linux 6.1.158（`f6e38ae624cf`），**arm64-only**。

---

## §1 这个子系统是干什么的

### 1.1 一句话

**把"让某个 IP 块进入/退出复位状态"这件事，从"直接写寄存器"抽象成一组
不透明句柄上的四个动作**，从而让消费者驱动不需要知道复位位在哪个寄存器的第几位。

### 1.2 它解决的三个具体问题

| 问题 | 没有框架时会怎样 | 框架的答案 |
|---|---|---|
| **知识分散** | 每个消费者驱动自己 `ioremap` CRU 寄存器、自己记住位号 | 位号的知识收在控制器驱动 + DTS 里，消费者只拿句柄 |
| **误伤邻居** | 一个 32 位寄存器里塞了 32 条线，读-改-写没加锁就会互相冲掉 | 串行化收在控制器驱动里（`04-extensions.md` §1.3） |
| **共享的线谁来放** | 两个驱动共用一条线，先跑完的那个把还在用的另一个复位了 | `shared` 语义 + 引用计数（§5.2） |

### 1.3 规模：一个"很小的框架 + 很大的驱动面"

| 部件 | 行数 | 说明 |
|---|---:|---|
| `drivers/reset/core.c` | 1289 | **整个框架**。里面没有一行碰寄存器 |
| `include/linux/reset.h` | 904 | 对外 API，其中 **69 个 `static inline`** —— 大部分 API 是包装 |
| `include/linux/reset-controller.h` | 114 | 控制器侧契约，三个结构体 |
| `drivers/reset/` 全部 `.c` | 8132（39 个文件） | 各家控制器驱动 |

**`drivers/reset/` 的大小严重低估了真实规模**：`drivers/clk/` 下有
**33 个文件**在注册复位控制器（`04-extensions.md` §6），
因为 SoC 里时钟和复位往往在同一片寄存器区（`08-feynman.md` §6）。
想找某个平台的复位控制器实现，第一步是 `git grep -l reset_controller_register`，
不是 `ls drivers/reset/`。

### 1.4 类型判定：框架型，不是总线型

`include/linux/reset*.h` 里**没有 `bus_type`**，`drivers/reset/core.c` 里
也没有 `struct bus_type`。所以：

- **没有** `struct device` 层面的 reset 设备，**没有**驱动匹配、**没有** sysfs 目录；
- 控制器驱动通常是 platform 驱动，注册复位能力只是它 probe 里的一步；
- 消费者通过**函数调用**获取服务，不通过设备模型。

⇒ 这个判定直接决定了后面几篇的形状：02 画的是"核心抽象 ↔ 消费者"关系而不是总线图，
03 的主轴是"注册 / 获取 / 动作"而不是"传输"。

### 1.5 一张全景图

```
   ┌──────────────────── 消费者驱动（几百个，遍布全树）────────────────────┐
   │  devm_reset_control_get_*()  →  句柄                                   │
   │  reset_control_assert/deassert/reset/status/acquire/release/rearm()     │
   └────────────────────────────────┬───────────────────────────────────────┘
                                    │  include/linux/reset.h（69 个 inline 包装）
   ┌────────────────────────────────▼───────────────────────────────────────┐
   │            reset 核心   drivers/reset/core.c   1289 行                  │
   │  · reset_controller_list（一条全局链表）+ reset_list_mutex               │
   │  · DT 解析：resets / reset-names → (rcdev, id)                          │
   │  · 三个计数器：deassert_count / triggered_count / acquired               │
   │  · 不碰寄存器、不解释 cell 语义、无 tracepoint / debugfs / sysfs         │
   └────────────────────────────────┬───────────────────────────────────────┘
                                    │  struct reset_control_ops（四个可选回调）
   ┌────────────────────────────────▼───────────────────────────────────────┐
   │  控制器驱动                                                             │
   │   MMIO（reset-simple 及其复用者） │ syscon/regmap │ 固件（SCMI/bpmp/rpi）│
   │   住在 drivers/reset/、drivers/clk/、drivers/soc/ …                     │
   └────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
                            SoC 里的 CRU / PMU 寄存器
```

---

## §2 硬件背景：复位到底是什么

**这一节是理解全篇的前提，内容属于通用硬件知识 + 绑定文档的归纳，
不是从本树代码读出来的**（证据等级见 `99-evidence.md` §4）。

### 2.1 一条复位线在做什么

数字电路里的复位信号是一根接到 IP 块 `reset` 引脚上的线。它有效（asserted）时，
IP 块内部的**所有触发器被强制回到已知初始值**：状态机回到 IDLE、
计数器归零、寄存器回到手册上写的 reset value、输出被拉到确定电平。

为什么必须有这个东西：**上电瞬间触发器的值是随机的**。
没有复位，一个状态机可能停在一个非法状态里，永远出不来。

两个方向的术语，全篇统一：

| 术语 | 含义 | 框架里对应 |
|---|---|---|
| **assert（断言 / 有效）** | 复位信号生效，IP 块**被按住不工作** | `reset_control_assert()` |
| **deassert（解除）** | 复位信号撤销，IP 块**开始正常工作** | `reset_control_deassert()` |

⚠️ 一个常见的语言陷阱：**"assert reset" = 让设备停住**，不是"启动设备"。
中文里说"复位它"通常指的是 assert+deassert 一整套动作，
而框架里 `assert` 只做前一半。

### 2.2 极性：为什么"高电平"和"低电平"都可能是有效

硬件上复位引脚分两种：

- **低电平有效**（`nRESET`、`RESET_N`，名字带 n 或横线）：引脚为 0 时复位。
  这是最常见的形式，因为上电时引脚天然被拉低，等于默认处于复位态。
- **高电平有效**（`RESET`）：引脚为 1 时复位。

这个差异**在 reset 框架的 API 层面完全不可见**：`assert()` 表达的是
"逻辑上让它进入复位"，寄存器里到底要写 1 还是写 0，
是控制器驱动 + DTS flags 的事。ti-syscon 就把它做成 DTS 里的一格 flag
（`ASSERT_SET` / `ASSERT_CLEAR`，`06-devicetree.md` §4.3）。

⇒ 所以调试时"我 assert 了但寄存器里是 0"不一定是错的，得看极性。

### 2.3 同步复位与异步复位

| | 异步复位 | 同步复位 |
|---|---|---|
| 生效时机 | 信号一变立刻生效，**不需要时钟** | 需要时钟边沿才生效 |
| 常见于 | 全局上电复位 | IP 内部的功能复位 |
| 对软件的影响 | assert 可以在时钟关着的时候做 | **必须先有时钟** |

这是软件侧最容易踩的一个硬件坑：**很多 IP 的复位是同步的，
必须先把时钟开起来，复位才会真正被采样进去。** 于是标准顺序是

```
   clk_prepare_enable()  →  reset_control_deassert()  →  访问寄存器
```

反过来（先 deassert 再开时钟）在这类 IP 上会读到全 0 或直接挂住总线。
**reset 框架完全不知道时钟的存在**，这个顺序 100% 是消费者驱动的责任
（`08-feynman.md` §6）。

### 2.4 复位的层级：三种不同的"复位"

| 层级 | 触发者 | 范围 | 软件能控制吗 |
|---|---|---|---|
| **POR（Power-On Reset）** | 上电电路 / 电压监测 | 整颗 SoC | ✗ 纯硬件 |
| **系统复位 / 看门狗复位** | PMIC、看门狗、软件写 PMU 寄存器 | 整颗 SoC 或一个大域 | 有，但不走 reset 框架（走 `reboot`/watchdog 子系统） |
| **模块复位（IP reset）** | 软件写 CRU 寄存器 | **一个 IP 块** | **这才是 reset 框架管的东西** |

⇒ 一句话划界：**reset 子系统只管第三层**。
"重启整个系统"是 `kernel/reboot.c` + `arm_smccc` 的事，
"看门狗超时复位"是 `drivers/watchdog/` 的事，两者都和本子系统无关。

### 2.5 寄存器形态：两种，直接决定用哪对 API

这是本子系统**最重要的一条硬件知识**，因为它决定了消费者驱动该调哪些函数。

**形态 A：电平保持型（level-hold）**

```
   写 1 到 CRU_SOFTRST0 的 bit3   →  这条线一直保持断言，设备一直停着
   写 0 到 CRU_SOFTRST0 的 bit3   →  解除，设备开始工作
```

寄存器里的位**写进去就一直是那个值**，软件必须自己写回去才能解除。
对应 API：**`assert()` / `deassert()` 成对使用**。

**形态 B：自解除脉冲型（self-deasserting / pulse）**

```
   写 1 到 CRU_SOFTRST_CON 的 bit3  →  硬件内部产生一个几个周期的复位脉冲，
                                        然后**自动**把这一位清掉
   读回来永远是 0
```

软件只能"触发一次"，没法保持。对应 API：**`reset()`（+ shared 场景下的 `rearm()`）**。

**这两种形态和"独占/共享"是两条完全正交的轴**——这是全子系统最容易混淆的一处，
`08-feynman.md` §2 专门用一节讲它。速记：

```
                  电平保持型              自解除脉冲型
   exclusive   assert / deassert        reset  （+ rearm 视需要）
   shared      assert / deassert        reset + rearm
               （必须配平）              （必须配平）
```

### 2.6 arm64 SoC 上复位控制器的四种实现形态

| 形态 | 硬件位置 | 软件路径 | 能睡眠？ | 实例 |
|---|---|---|---|---|
| **裸 MMIO** | CRU 里一片连续寄存器 | 驱动直接 `readl`/`writel` | 不睡 | Amlogic、Rockchip（`reset-simple` 家族） |
| **syscon/regmap 里的几位** | 复位位和别的控制位挤在同一片 syscon | 走 regmap | 看 regmap 类型 | ti-syscon、hi3660 |
| **固件代理** | 复位寄存器在 SCP/BPMP 侧，AP 不可直接访问 | 发一条消息给协处理器 | **会睡眠** | SCMI、Tegra BPMP、RPi firmware |
| **PMU/电源域一体** | 每个电源域自带一条复位 | 平台自己的驱动 | 视实现 | Apple M1 pmgr |

第三种是**平台移植时的高发问题**：同一份消费者驱动在 MMIO 平台上跑得好，
换到固件复位的平台上、又恰好在中断上下文里调用，就会
`BUG: scheduling while atomic`。框架里**没有 `might_sleep()`**，不会提前警告
（`07-debug.md` §7.2）。

### 2.7 时序要求：手册里那几个"至少 N 个周期"

硬件手册通常会规定：

- **复位脉冲的最小宽度**（例如"至少 4 个 APB 周期"）；
- **解除复位后到可以访问寄存器之间的等待时间**；
- **复位期间时钟必须处于什么状态**。

**框架不提供任何延时或时序保证**：`assert()` 返回时只代表"寄存器写下去了"。
需要延时就得消费者自己 `udelay()`/`usleep_range()`。
`reset()` 的脉冲宽度完全由硬件决定，软件既不能配也不能查。

⇒ 这解释了为什么很多消费者驱动会写成

```
   reset_control_assert(rstc);
   usleep_range(10, 20);          /* 手册要求的最小脉冲宽度 */
   reset_control_deassert(rstc);
```

而不是直接调 `reset_control_reset()` —— **手写电平型脉冲，宽度自己控制**。

### 2.8 与相邻硬件资源的关系

一个 IP 块能工作，通常要同时满足四件事，缺一不可：

```
   电源域上电 (power domain)  →  时钟就绪 (clk)  →  解除复位 (reset)  →  可访问
        genpd                      clk 框架          reset 框架
```

三个框架**互不知道对方存在**，顺序完全由消费者驱动负责。
但有一个重要的例外要知道：**runtime PM 会把电源域和时钟串起来**
（`pm_runtime_get_sync()` 内部走 genpd + clk），**而复位不在里面**
——所以 reset 通常是消费者驱动里唯一需要手写顺序的那一环。

---

## §3 概念模型：五个词

把这五个词的关系想清楚，整个子系统就通了。

```
   ┌─ 控制器（provider）────────────────────────────────────┐
   │  struct reset_controller_dev                            │
   │  = 一片能控制 N 条复位线的硬件 + 四个可选回调            │
   │  nr_resets 条线，用 id（0..N-1）区分                     │
   └────────────────────┬────────────────────────────────────┘
                        │  一个控制器 : 多条线
   ┌────────────────────▼────────────────────────────────────┐
   │  复位线（reset line）= (rcdev, id) 这一对                │
   │  框架里**没有**代表"一条线"的结构体                    │
   └────────────────────┬────────────────────────────────────┘
                        │  一条线 : 可以有多个句柄（仅 shared）
   ┌────────────────────▼────────────────────────────────────┐
   │  句柄（struct reset_control）= 某个消费者对某条线的引用   │
   │  身上带三个计数器，是全部记账的载体                       │
   └────────────────────┬────────────────────────────────────┘
                        │
   ┌────────────────────▼────────────────────────────────────┐
   │  消费者（consumer）= 拿句柄、调四个动作的那个驱动         │
   └─────────────────────────────────────────────────────────┘
```

第五个词是 **DTS 里的 cell**：它是"消费者怎么说出自己要哪条线"的语法，
经过控制器的 `of_xlate` 翻译成 `id`（§5.1）。

三个必须记住的"没有"：

1. **没有"复位线"这个对象**。线只是 `(rcdev, id)` 二元组，
   所以"这条线现在什么状态"这个问题框架答不上来（除了调 `.status` 回调）。
2. **没有全局注册表**。只有一条 `reset_controller_list`
   （`drivers/reset/core.c:21`）挂控制器，句柄挂在各自 rcdev 的
   `reset_control_head` 上。
3. **没有设备模型参与**。句柄不是 `struct device`，不出现在 sysfs 里。

---

## §4 数据结构速览

三个结构体，一眼过完。逐字段解释见 `02-datastructures.md`。

### 4.1 `struct reset_control` —— 句柄（定义在 `drivers/reset/core.c`，对消费者不透明）

```c
struct reset_control {
	struct reset_controller_dev *rcdev;   /* 属于哪个控制器 */
	struct list_head list;                /* 挂在 rcdev->reset_control_head */
	unsigned int id;                      /* 哪条线 */
	struct kref refcnt;                   /* 同一 (rcdev,id) 的 shared 句柄共用 */
	bool acquired;                        /* 是否持有所有权（_released 语义用） */
	bool shared;                          /* 共享 or 独占 */
	bool array;                           /* 这其实是个 array 壳（§4.4） */
	atomic_t deassert_count;              /* 电平型：有几个消费者要求它工作 */
	atomic_t triggered_count;             /* 脉冲型：发过几次还没 rearm */
};
```

**三个计数器就是这个框架的全部状态**：

| 字段 | 谁改它 | 归零/置位时的效果 |
|---|---|---|
| `deassert_count` | `deassert()` 加、`assert()` 减 | 0→1 时才真调 `.deassert`；1→0 时才真调 `.assert` |
| `triggered_count` | `reset()` 加、`rearm()` 减 | 0→1 时才真调 `.reset`；其余调用静默返回 0 |
| `acquired` | `acquire()` 置、`release()` 清 | false 时四个动作里的三个直接 `-EPERM` |

⚠️ 前两个**只在 `shared` 时参与判断**；exclusive 句柄走另一条分支。

### 4.2 `struct reset_controller_dev` —— 控制器（`include/linux/reset-controller.h`）

```c
struct reset_controller_dev {
	const struct reset_control_ops *ops;      /* 四个回调 */
	struct module *owner;                     /* 不填 → 模块可被卸载 */
	struct list_head list;                    /* 挂在全局 reset_controller_list */
	struct list_head reset_control_head;      /* 这个控制器上的所有句柄 */
	struct device *dev;
	struct device_node *of_node;              /* 不填 → 消费者永远 defer */
	int of_reset_n_cells;                     /* 必须等于 DTS 的 #reset-cells */
	int (*of_xlate)(...);                     /* cell → id 的翻译 */
	unsigned int nr_resets;                   /* 不填 → id 不受上界保护 */
};
```

带 的三个字段是**控制器驱动最常漏填的**，后果都是静默的
（`05-usage.md` §6.2 那张五漏表）。

### 4.3 `struct reset_control_ops` —— 四个回调，**全部可选**

```c
struct reset_control_ops {
	int (*reset)(struct reset_controller_dev *rcdev, unsigned long id);
	int (*assert)(struct reset_controller_dev *rcdev, unsigned long id);
	int (*deassert)(struct reset_controller_dev *rcdev, unsigned long id);
	int (*status)(struct reset_controller_dev *rcdev, unsigned long id);
};
```

官方原文就一句：**All functions are optional.**
（`Documentation/driver-api/reset.rst:161`）

回调缺失时三个动作的返回码**互不相同**，这是全框架最反直觉的一处：

| 动作 | 回调缺失时 | 位置 |
|---|---|---|
| `reset` | `-ENOTSUPP`，两种语义一致 | `drivers/reset/core.c:340-341` |
| `assert` | shared → **0**；exclusive → `-ENOTSUPP` | `:467-468` / `:475-476` |
| `deassert` | **一律 0** | `:562-563`（注释 `:555-560` 解释原因） |
| `status` | `-ENOTSUPP` | `:616` |

### 4.4 两个"批量"外壳，别搞混

| | 是什么 | 结构 | 典型 API |
|---|---|---|---|
| **array** | **一个句柄**代表 DTS 里的 N 条线；对它做动作 = 逐条做 | `struct reset_control_array`（内含一个 `reset_control` 壳 + N 个真句柄） | `reset_control_array_get*()`；`status()` 对它返回 `-EINVAL` |
| **bulk** | **N 个独立句柄**放在一个数组里，方便一起 get / 一起动作 | `struct reset_control_bulk_data[]`（每项 = `id` 字符串 + 一个句柄） | `reset_control_bulk_*()` |

⚠️ 名字很像但语义完全不同（`05-usage.md` §10.1）。

### 4.5 全局变量与锁

| 符号 | 位置 | 保护什么 |
|---|---|---|
| `reset_controller_list` | `drivers/reset/core.c:21` | 所有已注册控制器 |
| `reset_list_mutex` | `drivers/reset/core.c:20` | 上面那条链表 **+ 每个 rcdev 的句柄链表** |
| `reset_lookup_list` | `drivers/reset/core.c:24` | 非 DT 的 lookup 表 |
| `reset_lookup_mutex` | `drivers/reset/core.c:23` | 上面那条链表 |

arm64 上第二对完全用不到：全树只有一个使用者，是 arm32 的 DaVinci
（`05-usage.md` §9）。

---

## §5 机制速览

### 5.1 从 DTS 到 id：三方分工

```
   DTS:  resets = <&cru SRST_A_GMAC>;  reset-names = "mac";
             │
   ①  通用 OF 层：按 #reset-cells 切出 of_phandle_args（np + args[]）
             │
   ②  reset 核心：reset-names 里查 "mac" 得到下标 index
                  → of_parse_phandle_with_args(..., index, ...)
                  → 在 reset_controller_list 里按 np 指针相等找到 rcdev
                  → 比对 args_count == of_reset_n_cells，不等就 WARN + -EINVAL
             │
   ③  控制器的 of_xlate：args[] → unsigned int id
                  （默认实现只看 args[0] 并检查 < nr_resets）
```

核心**只读三个 DT 属性**：`#reset-cells`、`resets`、`reset-names`
（`06-devicetree.md` §1）。cell 里的数字是什么意思，核心一概不问
（`08-feynman.md` §7）。

### 5.2 六种获取语义，两两组合

| 维度 | 取值 | 决定什么 |
|---|---|---|
| **独占性** | `exclusive` / `shared` | 是否记账；同一条线能否被多个消费者持有 |
| **可选性** | 普通 / `_optional_` | 拿不到时返回 `ERR_PTR` 还是 **`NULL`** |
| **所有权** | 立即 / `_released` | 拿到句柄时是否同时拿到所有权（§5.4） |
| **生命周期** | `devm_` / 裸 | 是否自动 put |

⚠️ **`_optional_` 是静默失效的头号来源**：拿不到就是 `NULL`，
而所有动作函数第一行都是 `if (!rstc) return 0;`
——**一路返回成功，硬件从没被碰过**（`07-debug.md` §6 第 2 行）。

### 5.3 四个动作 + 三个管理动作

| 动作 | shared 时的真实行为 | exclusive 时 |
|---|---|---|
| `assert()` | 递减 `deassert_count`，**归零才落硬件** | 直接落硬件（需已 acquire） |
| `deassert()` | 递增 `deassert_count`，**0→1 才落硬件** | 直接落硬件（需已 acquire） |
| `reset()` | 递增 `triggered_count`，**0→1 才落硬件** | 直接落硬件（需已 acquire） |
| `status()` | 直接问 `.status` 回调，不看计数 | 同 |
| `rearm()` | 递减 `triggered_count` | 只检查 `acquired`，不动计数 |
| `acquire()` | 无意义（shared 恒 acquired） | 抢所有权，冲突 `-EBUSY` |
| `release()` | 同上 | 交还，**不加锁不改硬件** |

**两对 API 不能混用**：一个 shared 句柄上，
要么全程 `assert`/`deassert`，要么全程 `reset`/`rearm`。
混用会被两个 `WARN_ON` 拦下（`drivers/reset/core.c:453-458`、`:412-413`）。

### 5.4 `_released` + acquire/release：为什么存在

一条线物理上只能有一个主人，**但主人要换**（Tegra 的 2D/3D 引擎共用一条线）。
`_released` 变体让两个驱动都能 probe 成功，运行时再靠 `acquire()` 抢所有权。
⚠️ 冲突**不在 probe 期暴露**，而是在第一次 `acquire()` 时才 `-EBUSY`
（`08-feynman.md` §3）。

### 5.5 五个"框架不做的事"

| 框架不做 | 谁来做 |
|---|---|
| 时钟、电源域的先后顺序 | 消费者驱动（§2.8） |
| 任何延时 / 时序保证 | 消费者驱动（§2.7） |
| cell 语义的校验 | 无人（`08-feynman.md` §7 末） |
| 睡眠上下文检查（无 `might_sleep()`） | `CONFIG_DEBUG_ATOMIC_SLEEP` |
| 观测（无 tracepoint / debugfs / sysfs） | ftrace / kprobe / `devmem` |

**三个零**（tracepoint / debugfs / sysfs 全部为 0）是本子系统最需要提前知道的
一条事实——出问题时可用的手段比 clk、regulator 少得多
（`07-debug.md` §3、`08-feynman.md` §8）。

---

## §6 端到端调用路径

### 6.1 控制器注册（开机早期）

```
   控制器驱动 probe（platform_driver / initcall）
     │  填 struct reset_controller_dev：ops / owner / of_node
     │           / of_reset_n_cells / nr_resets [ / of_xlate ]
     ▼
   devm_reset_controller_register(dev, rcdev)      drivers/reset/core.c:143
     ▼
   reset_controller_register(rcdev)                drivers/reset/core.c:100
     │  ① of_xlate 为空 → 补默认：of_reset_n_cells = 1
     │                              of_xlate = of_reset_simple_xlate   :102-105
     │  ② INIT_LIST_HEAD(&rcdev->reset_control_head)                   :107
     │  ③ mutex_lock(&reset_list_mutex)
     │     list_add(&rcdev->list, &reset_controller_list)
     ▼
   完成。没有任何日志、没有 sysfs 节点、没有通知消费者的机制
```

三个控制器驱动用 `arch_initcall` / `postcore_initcall` 把这一步提到
`device_initcall` 之前（`07-debug.md` §4.2），因为它们管的线是"其他设备初始化时就要用"的。

### 6.2 消费者获取句柄（probe 期，最常走的一条）

```
   消费者驱动 probe
     ▼
   devm_reset_control_get_exclusive(dev, "mac")        include/linux/reset.h（inline）
     ▼
   __devm_reset_control_get(dev, id, index, shared, optional, acquired)      :1034
     │  devres_alloc 一个槽位
     ▼
   __reset_control_get(dev, id, index, shared, optional, acquired)           :942
     │  ① if (WARN_ON(shared && acquired)) return -EINVAL           :946-947
     │  ② dev->of_node 存在 → 走 DT 分支；否则走 lookup 表分支（arm64 不用）
     ▼
   __of_reset_control_get(node, id, index, shared, optional, acquired)       :816
     │  ① node 为空 → -EINVAL                                        :827
     │  ② id 非空 → of_property_match_string("reset-names", id) → index :829-836
     │     查不到：optional ? NULL : -ENOENT
     │  ③ of_parse_phandle_with_args(node, "resets", "#reset-cells",
     │                               index, &args)                    :838-839
     │  ④ mutex_lock(&reset_list_mutex)                               :845
     │  ⑤ 遍历 reset_controller_list 找 args.np == r->of_node          :847-852
     │     没找到 → -EPROBE_DEFER                                     :854-857
     │  ⑥ WARN_ON(args.args_count != rcdev->of_reset_n_cells) → -EINVAL :859-862
     │  ⑦ rstc_id = rcdev->of_xlate(rcdev, &args)                     :864
     ▼
   __reset_control_get_internal(rcdev, index, shared, acquired)          :749 起
     │  ① 遍历 rcdev->reset_control_head 找同 id 的已有句柄
     │  ② 找到且双方都 shared → kref_get，返回**同一个句柄**
     │  ③ 找到但任一方 exclusive → WARN_ON + -EBUSY
     │  ④ 找到但新请求 acquired == false（_released）→ 新建独立句柄
     │  ⑤ 没找到 → kzalloc 新句柄，try_module_get(rcdev->owner)
     ▼
   返回句柄给消费者
```

这条路径上有 **四个不同的失败出口**，且症状完全不同：
`-ENOENT`（名字不对）、`-EPROBE_DEFER`（控制器没注册）、
`-EINVAL`（cells 不匹配 / 参数非法）、`-EBUSY`（独占冲突）。
分诊表在 `07-debug.md` §5。

### 6.3 一次 deassert 落到硬件（运行期）

```
   reset_control_deassert(rstc)                        drivers/reset/core.c:530
     │  ① rstc == NULL → return 0            _optional_ 的静默出口   :532-533
     │  ② IS_ERR → WARN_ON + -EINVAL                                  :535-536
     │  ③ 是 array 壳 → 逐条递归                                       :538-539
     │  ④ shared？
     │     ├─ 是：triggered_count != 0 → WARN_ON + -EINVAL（混用两对 API）:542-543
     │     │      atomic_inc_return(&deassert_count) != 1 → return 0    :545-546
     │     └─ 否：!acquired → WARN + -EPERM                             :548-552
     │  ⑤ ops->deassert 为空 → return 0     一律成功，见 §4.3        :562-563
     ▼
   rcdev->ops->deassert(rcdev, id)      ← 此时**已放开 reset_list_mutex**
     │                                     所以回调里可以睡眠
     ▼
   控制器驱动：spin_lock → readl → writel → spin_unlock（MMIO 形态）
             或 regmap_write_bits（syscon 形态）
             或 发一条固件消息并等待（SCMI 形态，会睡）
```

这条路径上有**三个"返回 0 但什么都没做"的出口**（①、④ 的 shared 分支、⑤），
这是本子系统静默失效的根源。六种成因汇总在 `07-debug.md` §6。

---

## §7 与其他子系统的关系

### 7.1 一张关系图

```
                 ┌──────────────┐
                 │  消费者驱动   │  （网卡 / USB / DRM / SATA / …）
                 └───┬───┬───┬──┘
        clk_*()      │   │   │      reset_control_*()
      ┌──────────────┘   │   └───────────────┐
      ▼                  ▼                   ▼
  ┌────────┐      ┌────────────┐      ┌────────────┐
  │ clk 框架│      │ genpd/PM   │      │ reset 框架 │
  └────┬───┘      └─────┬──────┘      └─────┬──────┘
       │                │                   │
       └────────┬───────┴─────────┬─────────┘
                ▼                 ▼
         同一片 CRU/PMU 寄存器    平台驱动
       三个框架互不通信，顺序由消费者负责
```

### 7.2 逐个说明

| 相邻子系统 | 关系 | 关键事实 |
|---|---|---|
| **clk** | 最紧密：`drivers/clk/` 下 **33 个文件**同时注册复位控制器 | 硬件上同一片寄存器；软件上**两个框架完全独立**，DTS 里一个节点同时声明 `#clock-cells` 和 `#reset-cells`（`06-devicetree.md` §7） |
| **genpd / runtime PM** | 并列的第三种资源 | runtime PM 会自动处理电源域和时钟，**不管复位** —— 所以复位常常是驱动里唯一手写顺序的一环 |
| **platform / DT** | 上游 | 控制器基本都是 platform 驱动；DT 属性只有三个（`06-devicetree.md` §1） |
| **regmap / syscon** | 实现手段 | 复位位和别的控制位挤在一片 syscon 时用它（`05-usage.md` §7） |
| **SCMI / BPMP / mailbox** | 实现手段 | 固件代理型控制器把请求发给协处理器；**这类回调会睡眠** |
| **watchdog / reboot** | ✗ **无关** | 整机复位不走本框架（§2.4） |
| **GPIO** | ✗ 6.1 上无关 | `drivers/reset/Kconfig:11-12` 提到 gpio 复位，但**本版本没有 `reset-gpio.c`**（`99-evidence.md` §7.3）；用 GPIO 拉复位脚的设备都是驱动自己 `gpiod_set_value()` |
| **ACPI** | 裁掉 | 全框架只有一处 ACPI 代码（`drivers/reset/core.c:1111-1121`），arm64 场景不涉及（`99-evidence.md` §2.7） |

### 7.3 一条实用的判断规则

**"我该用 reset 框架，还是自己拉 GPIO / 自己写寄存器？"**

| 情况 | 用什么 |
|---|---|
| SoC 内部 IP 的复位位在 CRU 里 | reset 框架 |
| 板上外设的复位脚接在 SoC 的一个 GPIO 上 | 6.1 上直接用 gpiod（`reset-gpios` 属性），**不是** `resets` |
| 复位需要固件参与 | reset 框架（走 SCMI 等控制器驱动） |
| 要重启整机 | reboot / watchdog 子系统 |

---

## §8 导读：按你要做的事跳转

| 你要做的事 | 去哪一篇 | 关键小节 |
|---|---|---|
| 查某个函数叫什么、参数、返回什么 | `01-api.md` | §0 代码地图 · §7 错误码语义 |
| `CONFIG_RESET_CONTROLLER=n` 时哪些 API 变桩函数 | `01-api.md` | §6 |
| 三个计数器、句柄和控制器的字段含义 | `02-datastructures.md` | §1.1 · §5 全局变量与锁 |
| 逐行读 get / assert / deassert 的实现 | `03-internals.md` | Part A 时间线 · Part B 横切 |
| 框架的设计债和评价 | `03-internals.md` | Part C |
| 各控制器家族（MMIO / syscon / 固件）的差异与回调完整性 | `04-extensions.md` | §1 · §7 |
| 抄一份控制器驱动或消费者驱动骨架 | `05-usage.md` | §2–§9 |
| 控制器驱动最常漏填的五个字段 | `05-usage.md` | §6.2 |
| DTS 怎么写、cell 什么含义、配错什么症状 | `06-devicetree.md` | §1 · §4 · §6 |
| probe 一直 defer / 拿到错误码 / 硬件没反应 | `07-debug.md` | §4 · §5 · §6 |
| 一页速查表 | `07-debug.md` | §8 |
| shared 和 exclusive 到底在共享什么 | `08-feynman.md` | §2 |
| 为什么 `deassert()` 返回 0 却什么都没做 | `08-feynman.md` | §5 |
| 为什么没有 debugfs / tracepoint | `08-feynman.md` | §8 |
| 某条结论的证据等级、哪些没验证 | `99-evidence.md` | §5 未验证项 |

### 8.1 如果只记五件事

1. **框架管的是"谁申请了什么"，不是"硬件现在是什么样"**
   —— `assert()` 返回 0 不代表线被断言（§4.3、`08-feynman.md` §1）。
2. **shared/exclusive 和 脉冲/电平 是两条正交的轴**，四种组合都合法；
   混用两对 API 才是错的（§2.5、`08-feynman.md` §2）。
3. **`_optional_` + 没写 `resets` = 全程返回 0，硬件从没被碰过**（§5.2）。
4. **`deassert()` 在回调缺失时一律返回 0**，`assert()` 只有 shared 才返回 0，
   `reset()` 一律 `-ENOTSUPP` —— 三个动作各不相同（§4.3）。
5. **零 tracepoint、零 debugfs、零 sysfs**：出问题只能靠
   `devices_deferred` + WARN 行号 + ftrace/kprobe + `devmem`（§5.5）。
