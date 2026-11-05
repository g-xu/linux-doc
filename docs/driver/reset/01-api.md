---
title: API 参考
---

# 01 代码地图与对外 API：reset controller 框架

> **本篇定位**：「我要找某个文件」和「我要找某个 API」是同一次查询，所以放在同一篇。
> 三张表就是全部入口：§0.1 文件表、§2.2 消费者获取 API 表、§5.1 控制器注册 API 表。
>
> **格式约定**（全篇统一）：
> - 表格固定三列 **API / 说明 / 备注**；签名只写「函数名 + 形参名」，不带类型。
> - 位置只精确到**文件**，不到行号。行号集中在 `03-internals.md` 和 `99-evidence.md` 维护。
> - 例外：C 代码块里保留原样行号注释。
> - `core.c` 一律指 `drivers/reset/core.c`；`reset.h` 指 `include/linux/reset.h`；
>   `reset-controller.h` 指 `include/linux/reset-controller.h`。
> - arm64-only。整个框架**只有一处** ACPI 触点 —— `__device_reset` 里的 `_RST` 方法调用
>   （`core.c:1111-1121`，`#ifdef CONFIG_ACPI` 包着）。本文档集按 arm64-only 把它裁掉，
>   §3.5 只做一句提示，裁剪理由记在 `99-evidence.md` §2.7。

---

## §0 代码地图

### 0.1 文件与职责

| 路径 | 职责 |
|---|---|
| **`drivers/reset/core.c`** | 框架的**全部**核心逻辑。查表、引用计数、四个动作的语义判定、devres 挂钩、DT 解析、lookup 表，全在这一个文件里 |
| **`include/linux/reset.h`** | 消费者头文件。只有 **23 个真函数**声明（`core.c` 里 27 个 `EXPORT_SYMBOL_GPL` 减去控制器侧的 4 个，正好对上），其余 **41 个是 `static inline` 包装**（§1） |
| **`include/linux/reset-controller.h`** | 控制器头文件。`reset_control_ops` / `reset_controller_dev` / `reset_control_lookup` 三个结构 + 注册 API |
| `drivers/reset/Kconfig` | `RESET_CONTROLLER` 主开关 + 每个控制器驱动一个 config |
| `drivers/reset/Makefile` | 编译规则 |
| `drivers/reset/reset-*.c` | **39 个**控制器驱动（本目录直接下辖），加上 `hisilicon/`、`sti/`、`tegra/` 三个子目录共 **44 个** |
| `include/linux/reset/reset-simple.h` | 通用「一个寄存器一位」实现的共享头，全树 **6 个文件**在 include 它（4 个在本目录，2 个在 `drivers/clk/`，见 `04-extensions.md` §1.3） |
| `include/linux/reset/{bcm63xx_pmb,socfpga,sunxi}.h` | 三个平台私有头，与框架核心无关 |
| `Documentation/driver-api/reset.rst` | 唯一的概念文档。**它把 API 明确分成"消费者侧"和"控制器侧"两部分**，本篇沿用这个划分 |
| `Documentation/devicetree/bindings/reset/` | DT 绑定（`06-devicetree.md`） |

**注意这个子系统没有第二个核心文件**。没有 `reset-core-of.c`、没有 `reset-sysfs.c`、
没有 debugfs 文件。整个框架就是 **一个 .c + 两个 .h**。这个规模决定了两件事：

1. 读完这三个文件你就读完了框架的 100%（`99-evidence.md` §2.1）。
2. 但也意味着**框架自己不提供任何观测手段** —— 零 tracepoint、零 sysfs、零 debugfs
   （`07-debug.md` §3）。

### 0.2 两个头文件的分工

| 你的角色 | 该 include 哪个 | 里面有什么 |
|---|---|---|
| **消费者**（要复位自己的外设） | `<linux/reset.h>` | 获取/释放 + 四个动作 + bulk + array |
| **控制器驱动**（提供复位线） | `<linux/reset-controller.h>` | `reset_control_ops` 四个回调 + 注册函数 |

**两个头文件互不 include。** 控制器驱动如果同时也想当消费者
（例如 `reset-uniphier-glue.c` 自己也要 `devm_reset_control_get_shared`），
必须两个都 include。

`reset.h` **不能**给控制器驱动用：它连 `struct reset_controller_dev` 的定义都看不到
（`reset.h` 只在开头前向声明了 `struct device` / `struct device_node` / `struct reset_control`）。

### 0.3 Kconfig

```
# drivers/reset/Kconfig:2-3
config ARCH_HAS_RESET_CONTROLLER
	bool                                  # 由各 arch/SoC 的 Kconfig select

# :5-9
menuconfig RESET_CONTROLLER
	bool "Reset Controller Support"        # bool，不是 tristate
	default y if ARCH_HAS_RESET_CONTROLLER
```

三个必须知道的点：

| 事实 | 后果 |
|---|---|
| `RESET_CONTROLLER` 是 **`bool`** | 核心永远编进 vmlinux，**没有 `reset` 这个模块名**可以挂 `dyndbg=` 参数（`07-debug.md` §2.2） |
| arm64 上 `ARCH_HAS_RESET_CONTROLLER` 基本必被 select ⇒ `default y` | 你几乎不用担心 `=n` 的情形，但 §6 的桩函数仍然必须理解 |
| 每个控制器驱动是独立 config（`RESET_A10SR`、`RESET_IMX7`、`RESET_SCMI` …） | 「复位不生效」的第一个怀疑对象是**控制器驱动没编进去**，不是核心 |

### 0.4 全篇最重要的一件事：41 个包装函数背后只有 3 个真函数

reset 的 API 表看起来有六十多个函数，**这是一种错觉**。
消费者侧所有「获取」类 API 最终都落到下面三个真函数之一：

```
__reset_control_get(dev, id, index, shared, optional, acquired)        /* core.c:942 */
__devm_reset_control_get(dev, id, index, shared, optional, acquired)   /* core.c:1034 */
__of_reset_control_get(node, id, index, shared, optional, acquired)    /* core.c:816 */
```

⇒ **看到一个陌生的 `xxx_reset_control_get_yyy`，不要去查它做什么 ——
去 `reset.h` 里看它把这三个 `bool` 填成了什么。** 这是本篇 §1 的全部内容。

---

## §1 命名法：把函数名翻译成三个 bool

> 头文件：`include/linux/reset.h`。本节所有函数都是 `static inline`，**没有实现文件**。

### 1.1 六个命名维度

| 名字里的部分 | 含义 | 落到哪个实参 |
|---|---|---|
| `devm_` 前缀 | 走 devres，驱动 detach 时自动 put | 选 `__devm_reset_control_get` |
| `of_` 前缀 | 从 **`device_node`** 而不是 `device` 查 | 选 `__of_reset_control_get` |
| 无前缀 | 从 `device` 查，**要自己 put** | 选 `__reset_control_get` |
| `_exclusive` | 独占语义 | `shared = false` |
| `_shared` | 引用计数语义 | `shared = true` |
| `_optional` | DTS 里没写就返回 `NULL` 而不是错误 | `optional = true` |
| `_released` | 拿到手是**未 acquire** 状态，用前必须 `reset_control_acquire()` | `acquired = false` |
| 无 `_released`（且 exclusive） | 拿到手就已 acquire | `acquired = true` |
| `_by_index` | 用**下标**而不是名字定位 | `id = NULL, index = n` |
| `_bulk_` | 一次拿多条，用 `reset_control_bulk_data` 数组 | 走 `__reset_control_bulk_get` 系列 |
| `_array_` | 一次拿**全部**（DTS 里有几条拿几条），返回**一个**句柄 | 走 `of_reset_control_array_get` |

### 1.2 完整的实参映射表（本篇最该收藏的一张表）

| 消费者 API | shared | optional | acquired | 落到 |
|---|---|---|---|---|
| `reset_control_get_exclusive` | false | false | **true** | `__reset_control_get` |
| `reset_control_get_exclusive_released` | false | false | **false** | `__reset_control_get` |
| `reset_control_get_optional_exclusive` | false | **true** | true | `__reset_control_get` |
| `reset_control_get_shared` | **true** | false | false | `__reset_control_get` |
| `reset_control_get_optional_shared` | **true** | **true** | false | `__reset_control_get` |
| `devm_reset_control_get_exclusive` | false | false | true | `__devm_reset_control_get` |
| `devm_reset_control_get_exclusive_released` | false | false | **false** | `__devm_reset_control_get` |
| `devm_reset_control_get_optional_exclusive` | false | **true** | true | `__devm_reset_control_get` |
| `devm_reset_control_get_optional_exclusive_released` | false | **true** | **false** | `__devm_reset_control_get` |
| `devm_reset_control_get_shared` | **true** | false | false | `__devm_reset_control_get` |
| `devm_reset_control_get_optional_shared` | **true** | **true** | false | `__devm_reset_control_get` |
| `devm_reset_control_get_exclusive_by_index` | false | false | true | `__devm_reset_control_get`（`id=NULL`） |
| `devm_reset_control_get_shared_by_index` | **true** | false | false | `__devm_reset_control_get`（`id=NULL`） |
| `of_reset_control_get_exclusive` | false | false | true | `__of_reset_control_get` |
| `of_reset_control_get_optional_exclusive` | false | **true** | true | `__of_reset_control_get` |
| `of_reset_control_get_shared` | **true** | false | false | `__of_reset_control_get` |
| `of_reset_control_get_exclusive_by_index` | false | false | true | `__of_reset_control_get`（`id=NULL`） |
| `of_reset_control_get_shared_by_index` | **true** | false | false | `__of_reset_control_get`（`id=NULL`） |

**从这张表能直接读出三条不写在任何文档里的规律**：

1. **`shared = true` 的行，`acquired` 一律是 `false`。** 一个反例都没有。
   核心还专门为此加了一道 `WARN_ON(shared && acquired)`（`core.c:946`）⇒
   **「shared + acquired」是被禁止的组合**，所以你**找不到** `reset_control_get_shared_released`
   这个函数 —— shared 天生就是 released 的。
   但注意：shared 句柄的动作路径**从来不检查 `acquired`**（`03-internals.md` §B3），
   所以「released」对 shared 毫无实际影响。
2. **`_released` 只和 exclusive 组合。** 全树共 3 个：`reset_control_get_exclusive_released`、
   `devm_reset_control_get_exclusive_released`、`devm_reset_control_get_optional_exclusive_released`。
3. **`_by_index` 没有 optional 变体，也没有非 devm/of 的变体。**
   想要「按下标 + optional」，只能自己调 `__devm_reset_control_get`（下划线开头，但它**是导出符号**）。

### 1.3 过渡期遗留 API（不要在新代码里用）

`include/linux/reset.h:809-816` 有一段注释明确写着 `TEMPORARY calls to use during transition`：

| API | 等价于 | 备注 |
|---|---|---|
| `of_reset_control_get(node, id)` | `of_reset_control_get_exclusive` | 名字里不带语义 —— 这正是当年被拆掉的原因 |
| `of_reset_control_get_by_index(node, index)` | `of_reset_control_get_exclusive_by_index` | 同上 |
| `devm_reset_control_get(dev, id)` | `devm_reset_control_get_exclusive` | 全树用量仍然很大，不要以为它已经没人用 |
| `devm_reset_control_get_optional(dev, id)` | `devm_reset_control_get_optional_exclusive` | 同上 |
| `devm_reset_control_get_by_index(dev, index)` | `devm_reset_control_get_exclusive_by_index` | 同上 |

**为什么它们还在？** 注释说「等所有消费者都迁移完就删」，
而这个注释在 6.1 里已经存在多年 —— 属于 `03-internals.md` §C 记录的设计债。
**读老驱动时看到不带 `_exclusive` 的名字，要自己补上「这是独占的」这个信息。**

---

## §2 消费者侧：获取与释放

> 头文件：`include/linux/reset.h`。真实现：`drivers/reset/core.c`。

### 2.1 三个真函数（导出符号）

| API | 说明 | 备注 |
|---|---|---|
| `__reset_control_get(dev, id, index, shared, optional, acquired)` | 按 `dev` 查一条复位控制并返回句柄 | 内部先试 DT（`dev->of_node`），DT 走不通再查 lookup 表（§4）。**要自己 `reset_control_put`** |
| `__devm_reset_control_get(dev, id, index, shared, optional, acquired)` | 同上，但登记进 devres | **绝大多数驱动应该用它的包装形式**，不要直接调 |
| `__of_reset_control_get(node, id, index, shared, optional, acquired)` | 按 `device_node` 查，**不经过 lookup 表** | 用于「我要复位的不是我自己」的场景（如 power domain 驱动代管别的节点）。**没有 devm 版本** |

三者都返回 `struct reset_control *`，失败时是 `ERR_PTR()`；
`optional = true` 且 DTS 里没写时返回的是 **`NULL`（不是 `ERR_PTR`）**。
⇒ 判空必须用 `IS_ERR()`，**不能用 `!rstc`** —— 后者会把「合法的可选缺省」误判成失败。

### 2.2 推荐给消费者用的包装（按使用频率排）

| API | 说明 | 备注 |
|---|---|---|
| `devm_reset_control_get_exclusive(dev, id)` | 拿一条独占复位线，driver detach 自动释放 | **默认就用这个**。`__must_check` |
| `devm_reset_control_get_optional_exclusive(dev, id)` | 同上，但 DTS 没写时返回 `NULL` 而非错误 | 「有的板子有这条线、有的没有」时用。后续所有动作 API 对 `NULL` 都直接返回 0（§3.1） |
| `devm_reset_control_get_shared(dev, id)` | 拿一条**引用计数**复位线 | 多个外设共用一条线时**必须**用它，否则你的 assert 会打断别人 |
| `devm_reset_control_get_optional_shared(dev, id)` | 上面两个的组合 | — |
| `devm_reset_control_get_exclusive_by_index(dev, index)` | 按 `resets` 属性里的**下标**取 | DTS 没写 `reset-names` 时只能这样 |
| `devm_reset_control_get_shared_by_index(dev, index)` | 同上，shared 语义 | — |
| `devm_reset_control_get_exclusive_released(dev, id)` | 拿独占句柄，但**先不占用** | 用于多个消费者轮流独占同一条线的协议，见 §3.4 |
| `devm_reset_control_get_optional_exclusive_released(dev, id)` | 同上 + optional | — |
| `reset_control_get_exclusive(dev, id)` / `_shared` / `_optional_*` | 非 devres 版本 | **必须自己 `reset_control_put`**。新代码没有理由用它 |
| `of_reset_control_get_exclusive(node, id)` / `_shared` / `_by_index` | 按 `device_node` 取 | **没有 devm 版本** ⇒ 一定要自己 put |
| `device_reset(dev)` | 一步完成「取 + 复位一次 + 放」 | 见 §3.5。`__must_check` |
| `device_reset_optional(dev)` | 同上，DTS 没写时返回 0 | 注意**它不带 `__must_check`** |

### 2.3 释放

| API | 说明 | 备注 |
|---|---|---|
| `reset_control_put(rstc)` | 递减引用计数，归零时销毁句柄 | 对 `NULL` 和 `ERR_PTR` 都安全（`core.c:718` 先判 `!rstc \|\| WARN_ON(IS_ERR(rstc))`）。**devm 取的句柄不要手动 put** |
| `reset_control_bulk_put(num_rstcs, rstcs)` | 批量版本 | — |

**数组句柄（§4.2）也用 `reset_control_put` 释放** —— 核心内部会认出
`rstc->array` 标志并走 `reset_control_array_put`（`core.c:984`）。
⇒ **消费者不需要区分「这是普通句柄还是数组句柄」**。

---

## §3 消费者侧：四个动作 + 两个占用管理

> 头文件：`include/linux/reset.h`（只有声明）。实现全在 `drivers/reset/core.c`。
> 本节六个函数**全部是导出符号**，全部有完整 kernel-doc。

### 3.1 四个动作

| API | 说明 | 备注 |
|---|---|---|
| `reset_control_assert(rstc)` | 让复位线进入**有效**（复位）状态 | shared 句柄上**不保证真的会拉低** —— 只递减计数，归零才落到硬件。exclusive 句柄若控制器没实现 `.assert` 回调，返回 **`-ENOTSUPP`** |
| `reset_control_deassert(rstc)` | 让复位线**解除**复位 | 与 assert 不对称：控制器没实现 `.deassert` 时返回 **`0`（成功）** 而不是 `-ENOTSUPP`（`core.c:555-563` 有整段注释解释原因）。这是全框架最容易踩的一处不对称 |
| `reset_control_reset(rstc)` | 触发一次**复位脉冲**（自清零型硬件） | 控制器没实现 `.reset` 时返回 `-ENOTSUPP`。shared 句柄上**一生只真发一次**（`triggered_count` 从 0 变 1 时才落硬件） |
| `reset_control_status(rstc)` | 查询复位线当前状态 | 返回值不是 0/负 二分：**正数 = 处于复位中**，0 = 未复位，负 = 出错。**不接受数组句柄**（直接 `-EINVAL`），这是 `Documentation/driver-api/reset.rst:131-132` 明确写下的限制 |

**`assert/deassert` 和 `reset` 这两组是互斥的用法**，不是可以混着来的：

```
/* drivers/reset/core.c:321-322（reset_control_reset 的 kernel-doc） */
 * Consumers must not use reset_control_(de)assert on shared reset lines when
 * reset_control_reset has been used.

/* :436-437（reset_control_assert 的 kernel-doc） */
 * Consumers must not use reset_control_reset on shared reset lines when
 * reset_control_(de)assert has been used.
```

而核心**真的会拦**（只在 shared 句柄上）：

| 你干了什么 | 哪一行拦住你 | 返回 |
|---|---|---|
| shared 句柄先 `deassert` 再 `reset` | `core.c:344` `WARN_ON(deassert_count != 0)` | `-EINVAL` |
| shared 句柄先 `reset` 再 `assert` | `core.c:454` `WARN_ON(triggered_count != 0)` | `-EINVAL` |
| shared 句柄先 `reset` 再 `deassert` | `core.c:542` `WARN_ON(triggered_count != 0)` | `-EINVAL` |
| shared 句柄 `assert` 但没先 `deassert` | `core.c:457` `WARN_ON(deassert_count == 0)` | `-EINVAL` |

**exclusive 句柄上这四道检查一条都没有** —— 你可以随便混用，核心不管。
⇒ 「混用会被拦住」这个直觉只在 shared 上成立（`08-feynman.md` §3）。

### 3.2 `NULL` 句柄一律是合法输入

上面四个函数**每一个**的第一句都是：

```c
	if (!rstc)
		return 0;                 /* :331-332 / :444-445 / :532-533 / :607-608 */
```

⇒ 这正是 `_optional` 变体能存在的前提：**「这块板子没这条复位线」被表达成 `rstc == NULL`，
之后所有动作都变成安静的 no-op**，消费者代码里**不需要一个 `if`**。
`Documentation/driver-api/reset.rst:143-144` 把这条设计意图写得很直白：
`Passing a NULL pointer to the reset_control functions causes them to return quietly without an error.`

但 **`ERR_PTR` 不是**合法输入：四个函数都紧接着 `WARN_ON(IS_ERR(rstc))` → `-EINVAL`。
⇒ **忘了判 `IS_ERR` 会得到一条内核栈回溯**，不是静默失败。这是本框架最主要的报错手段（§7）。

### 3.3 shared 语义的计数器

| 计数器 | 谁动它 | 备注 |
|---|---|---|
| `deassert_count` | `deassert` 加、`assert` 减 | 只有 **0→1** 的 deassert 和 **1→0** 的 assert 会落到硬件 |
| `triggered_count` | `reset` 加、`rearm` 减 | kernel-doc（`core.c:38-40`）说它「目前只用于 shared，值只会是 0 或 1」 |

| API | 说明 | 备注 |
|---|---|---|
| `reset_control_rearm(rstc)` | 把 shared 句柄的 `triggered_count` 减 1，让下一次 `reset` 能再真发一次脉冲 | **它不碰硬件**（`core.c:401-423` 整个函数里没有一次 `ops->` 调用）—— 纯粹是核心内部的计数复位。必须与 `reset_control_reset` 配平，否则 `core.c:416` 的 `WARN_ON(atomic_dec_return(...) < 0)` 会响 |

### 3.4 exclusive 的「占用」协议：acquire / release

| API | 说明 | 备注 |
|---|---|---|
| `reset_control_acquire(rstc)` | 取得对这条复位线的独占操作权 | 已经持有时直接返回 0（幂等）。别的句柄正持有时返回 **`-EBUSY`** |
| `reset_control_release(rstc)` | 交回独占操作权 | **返回 `void`** —— 无法失败。实现只有一句 `rstc->acquired = false`（`core.c:724`） |

**这一对是给「同一条 exclusive 复位线要被多个消费者轮流独占」准备的**，
和 shared 完全是两回事：

| | shared | exclusive + acquire/release |
|---|---|---|
| 同时持有句柄的消费者 | 多个 | 多个（但必须用 `_released` 变体取） |
| 同时能操作硬件的消费者 | 多个（计数合并） | **一个** |
| 语义 | 「谁都别把线拉低，除非所有人都不用了」 | 「一次只准一个人碰，用完让给下一个」 |
| 忘了配平的后果 | 计数器歪掉，`WARN_ON` | 别人永远拿不到，`-EBUSY` |

不调 `acquire` 就直接操作会怎样？exclusive 路径上有三处 `!rstc->acquired` 检查：
`reset` 返回 `-EPERM`（`core.c:350-351`，**静默**）、
`assert` 和 `deassert` 返回 `-EPERM` 并且**打一条 `WARN(1, "reset %s (ID: %u) is not acquired\n", ...)`**
（`core.c:479-481` / `:549-551`）。
⇒ **同一个错误，三个入口里两个响一个不响** —— 这是 `07-debug.md` §2.1 记录的观测缺口。

### 3.5 一步到位：`device_reset`

| API | 说明 | 备注 |
|---|---|---|
| `device_reset(dev)` | 取句柄 → `reset_control_reset` → 立刻 put，三步合一 | `__must_check`。适用于「只有一条专属复位线、只需在 probe 里踢一次」的设备 |
| `device_reset_optional(dev)` | 同上，DTS 没写复位线时返回 0 | 注意它**没有** `__must_check`（`include/linux/reset.h:216`），和 `device_reset` 不一致 |

实现是 `__device_reset(dev, optional)`（`core.c:1106-1132`），三个要点：

1. 它取的是 **exclusive + acquired** 句柄（`core.c:1123` 的实参是 `0, optional, true`），
   ⇒ 如果这条线已经被别人独占，你拿到的是 `-EBUSY`。
2. 它**用完就 put**（`core.c:1129`）⇒ 复位脉冲发完，句柄就没了。
   **不能用它做 assert/deassert 序列**。
3. ACPI 分支（`core.c:1111-1121`）：有 `_RST` 方法就走 ACPI，没有就
   `return optional ? 0 : -ENOENT`。**arm64 DT 平台不会走到这里**，本文档集其余部分不再提。

---

## §4 批量与数组：两套不同的东西

**`_bulk_` 和 `_array_` 名字很像，是两个完全不同的机制。** 先看区别：

| | `_bulk_` | `_array_` |
|---|---|---|
| 你要提供什么 | 一个 `reset_control_bulk_data[]`，**每项填好 `id`（名字）** | 什么都不用填 |
| 拿到什么 | 数组里每项的 `rstc` 被填上，**N 个独立句柄** | **一个**句柄（内部藏着 N 个） |
| 拿到几条 | 你要几条给几条（名字对不上就失败） | DTS 里有几条就是几条（`resets` 属性的项数） |
| 后续动作 | 用 `reset_control_bulk_*()` 系列，或对单个 `rstc` 用普通 API | 用**普通** API（`reset_control_assert(rstc)` 等），核心自己识别 |
| 顺序保证 | assert 按序、deassert **按逆序**（kernel-doc `core.c:494`/`:574`） | **不保证顺序**（`Documentation/driver-api/reset.rst:152-153` 明说 `does not guarantee the order`） |
| 支持 `status` | ✅ 单个句柄可以 | ❌ **直接 `-EINVAL`**（`core.c:610`） |

### 4.1 bulk 系列

| API | 说明 | 备注 |
|---|---|---|
| `reset_control_bulk_get_exclusive(dev, num_rstcs, rstcs)` | 按名字批量取独占句柄 | 还有 `_shared` / `_optional_exclusive` / `_optional_shared` / `_exclusive_released` / `_optional_exclusive_released` 六个变体，映射规律同 §1.2 |
| `devm_reset_control_bulk_get_exclusive(dev, num_rstcs, rstcs)` | devres 版本 | 同样六个变体齐全，**比非 devm 版本多一个** `_optional_exclusive_released` |
| `reset_control_bulk_reset(num_rstcs, rstcs)` | 依次触发每条线的脉冲 | 任一条失败就对**已成功的** `rearm` 回滚 |
| `reset_control_bulk_assert(num_rstcs, rstcs)` | **按序** assert | 失败时把已 assert 的**再 deassert 回去** |
| `reset_control_bulk_deassert(num_rstcs, rstcs)` | **逆序** deassert | 失败时把已 deassert 的**再 assert 回去** |
| `reset_control_bulk_acquire(num_rstcs, rstcs)` | 批量取得独占权 | 失败回滚 |
| `reset_control_bulk_release(num_rstcs, rstcs)` | 批量交回 | `void`，不会失败 |
| `reset_control_bulk_put(num_rstcs, rstcs)` | 批量释放句柄 | devm 取的不要手动调 |

**bulk 的失败回滚是本框架里唯一有事务语义的地方**（`03-internals.md` §B4 逐个展开）。
`__reset_control_bulk_get` 自己也回滚：任一条失败就把前面 i 条全 put 掉
（`core.c:975-980`）。

### 4.2 array 系列

| API | 说明 | 备注 |
|---|---|---|
| `devm_reset_control_array_get(dev, shared, optional)` | 真函数（导出）。把 `dev` 的**所有**复位线打成一个句柄 | 四个 inline 包装：`_get_exclusive` / `_get_shared` / `_get_optional_exclusive` / `_get_optional_shared` |
| `of_reset_control_array_get(np, shared, optional, acquired)` | 真函数（导出），按 `device_node` | 五个包装，**多一个** `_get_exclusive_released`；注意 `_get_shared` 传的 `acquired` 是 **true**（`include/linux/reset.h:890`），和 §1.2 「shared 必配 false」的规律**不一致** —— 但这条路径不经过 `__reset_control_get` 的 `WARN_ON(shared && acquired)` 检查，所以不会报警 |
| `reset_control_get_count(dev)` | 数一数这个设备有几条复位线 | 返回**正数**为成功；**0 条也算错误**，返回 `-ENOENT`（`core.c:1268-1269`）。有 DT 走 DT，无 DT 查 lookup 表 |

**数组句柄怎么被认出来的？** `struct reset_control` 里有个 `bool array` 字段
（`core.c:49`），四个动作函数每个都在开头 `if (reset_control_is_array(rstc))` 分流
（`core.c:337` / `:450` / `:538` / `:610`）。
⇒ **对消费者完全透明**，但 `status` 是唯一不支持的那个（分流到 `-EINVAL` 而不是数组版本）。

---

## §5 控制器侧 API

> 头文件：`include/linux/reset-controller.h`。实现：`drivers/reset/core.c`。
> 全部 4 个注册相关函数都是导出符号。

### 5.1 注册

| API | 说明 | 备注 |
|---|---|---|
| `devm_reset_controller_register(dev, rcdev)` | **驱动应该用这个**。注册 + 登记 devres，remove 时自动注销 | `Documentation/driver-api/reset.rst:210-211` 明确推荐它 |
| `reset_controller_register(rcdev)` | 手工注册 | 必须自己在 remove 里 `reset_controller_unregister`。如果 `rcdev->of_xlate` 为 `NULL`，注册时会填上默认实现（§5.3） |
| `reset_controller_unregister(rcdev)` | 从全局链表摘掉 | **它不检查还有没有消费者持有句柄** —— 这是 `03-internals.md` §C 记录的生命周期缺口 |
| `reset_controller_add_lookup(lookup, num_entries)` | 为**没有 DT** 的平台登记「设备名 → (控制器, 下标)」映射 | arm64 上基本用不到（DT 平台走 `of_xlate`）；`RESET_LOOKUP()` 宏在 `reset-controller.h` 里 |

### 5.2 要填的结构（详细展开在 `02-datastructures.md` §2）

`struct reset_controller_dev` 的九个字段里，**必填只有三个**：`ops`、`owner`、`nr_resets`，
再加 `of_node` 或 `dev` 至少一个（否则 `rcdev_name()` 返回 `NULL`）。

`struct reset_control_ops` 的四个回调**全部可选**（`Documentation/driver-api/reset.rst:161` 一句话：`All functions are optional.`）：

| 回调 | 不实现的后果 | 备注 |
|---|---|---|
| `.reset(rcdev, id)` | `reset_control_reset` → `-ENOTSUPP` | 自清零脉冲型硬件实现这个 |
| `.assert(rcdev, id)` | exclusive → `-ENOTSUPP`；**shared → 静默返回 0** | 两种语义下后果不同（`core.c:467-468` vs `:475-476`） |
| `.deassert(rcdev, id)` | **返回 0（成功）** | 核心假定你是自清零型硬件（`core.c:555-563`） |
| `.status(rcdev, id)` | `reset_control_status` → `-ENOTSUPP` | 大多数驱动不实现 |

**「全部可选」+「不实现 deassert 就返回成功」两条合起来，
意味着一个四个回调全空的控制器可以注册成功，而且 `deassert` 会永远返回 0。**
框架不会拦你 —— 这是 `07-debug.md` §6 里「明明返回成功但硬件没动」这一类症状的根。

### 5.3 DT 解析钩子

| 符号 | 说明 | 备注 |
|---|---|---|
| `of_reset_simple_xlate(rcdev, reset_spec)` | 把 DTS 里的第一个 cell 当复位线号，只查上界 | **`static`，不是可调用 API**（`core.c:87`）。`of_xlate` 留空即用它 |
| `rcdev->of_xlate` | 控制器自定义的 cell → 线号 转换 | `#reset-cells > 1` 时必须自己写（`06-devicetree.md` §4） |
| `rcdev->of_reset_n_cells` | 期望的 cell 个数 | 核心用它校验 DTS：不匹配就 `WARN_ON` + `-EINVAL`（`core.c:859-860`） |

`of_reset_simple_xlate` 只查 `args[0] >= nr_resets`（`core.c:90-91`）—— **不查下界**。
cell 是 `u32`，所以负值本来不可能；但**填 0 永远合法**，即使 0 号线在硬件上不存在。

---

## §6 `CONFIG_RESET_CONTROLLER=n` 时的桩函数

`reset.h` 用 `#ifdef CONFIG_RESET_CONTROLLER` 把整个 API 分成两份
（`include/linux/reset.h:28` / `:72` / `:209`）。关闭时的返回值**不统一**，必须逐个记：

| 函数族 | `=n` 时返回 | 备注 |
|---|---|---|
| 四个动作 + `rearm` + `acquire` | **`0`（成功）** | 所有 bulk 动作也是 0 |
| `release` / `put` / `bulk_release` / `bulk_put` | 空函数 | — |
| `__reset_control_get` / `__devm_*` / `__of_*` / 两个 array | `optional ? NULL : ERR_PTR(-ENOTSUPP)` | 注意是 `-ENOTSUPP`（**524**），不是 `-EOPNOTSUPP`（95） |
| `__reset_control_bulk_get` / `__devm_*_bulk_get` | `optional ? 0 : ` **`-EOPNOTSUPP`** | **和上一行不一致！** bulk 用 `-EOPNOTSUPP`，单条用 `-ENOTSUPP`（`include/linux/reset.h:167` vs `:180`） |
| `__device_reset` | `optional ? 0 : -ENOTSUPP` | — |
| `reset_control_get_count` | **`-ENOENT`** | 和 `=y` 时「0 条也返回 `-ENOENT`」一致 |

**`reset-controller.h` 也有一份桩**（`:93-111`，判据是
`#if IS_ENABLED(CONFIG_RESET_CONTROLLER)`），其中
**`reset_controller_register` 返回 `0`（假装注册成功）** ——
⇒ 控制器驱动在 `=n` 时能正常 probe 完，只是没人能拿到它的复位线。

---

## §7 错误码语义（排查时先看这张表）

| 错误码 | 数值 | 什么情况 | 产生点 |
|---|---|---|---|
| **`-EINVAL`** | 22 | 传了 `ERR_PTR` 句柄（四个动作都会）；shared 语义误用（§3.1 那四道检查）；`status` 收到数组句柄；`shared && acquired` 组合；DTS cell 个数与 `of_reset_n_cells` 不符；`of_reset_simple_xlate` 线号越界 | `core.c` 共 **13 处** |
| **`-EPERM`** | 1 | exclusive 句柄**未 acquire** 就 `reset` / `assert` / `deassert` / `rearm` | `core.c:351` / `:481` / `:551` / `:419` |
| **`-ENOTSUPP`** | 524 | 控制器没实现对应回调：`.reset` 缺 → `reset`；`.assert` 缺 → exclusive `assert`；`.status` 缺 → `status` | `core.c:341` / `:476` / `:616` |
| **`-EBUSY`** | 16 | 这条线已被别的 exclusive 句柄占着 | `core.c:767`（取句柄时）/ `:664`（`acquire` 时） |
| **`-EPROBE_DEFER`** | 517 | 控制器驱动**还没注册** | `core.c:855`（DT 路径）/ `:923`（lookup 路径） |
| **`-ENOENT`** | 2 | DTS 里根本没有这条复位线，且**不是** optional；`reset_control_get_count` 数到 0 | `core.c:835` / `:937` / `:1156` / `:1269` |
| **`-ENOMEM`** | 12 | 分配句柄/数组失败 | `core.c:152` / `:776` / `:1043` / `:1080` / `:1187` / `:1234` |
| **`-ENODEV`** | 19 | `try_module_get(rcdev->owner)` 失败（控制器模块正在卸载） | `core.c:778-780` |
| **`-EIO`** | 5 | ACPI `_RST` 执行失败（arm64 不涉及） | `core.c:1119` |
| **`NULL`（不是错误）** | — | `optional` 变体 + DTS 没写这条线 | `core.c:835` / `:937` 的 `optional ? NULL : ...` |

**三条最容易搞错的**：

1. **`-EPROBE_DEFER` 不是错误**，是「等一下再来」。
   驱动 probe 里必须原样往上返回，**不能打 `dev_err`**
   （会在每次 defer 重试时刷屏；日志可见性、以及框架自己「没有 `_ratelimited` 变体」
   这件事见 `07-debug.md` §2.1）。
2. **`-ENOTSUPP`（524）不是 `-EOPNOTSUPP`（95）。**
   `-ENOTSUPP` 是内核内部专用码，**不该泄漏到用户态** —— 但 reset 的所有
   「回调没实现」路径都返回它，而消费者驱动经常把它直接返回给自己的调用者。
3. **成功也可能不为 0**：`reset_control_status` 返回**正数**表示「正处于复位中」。
   写 `if (reset_control_status(rstc))` 当错误判断是 bug。

---

## §8 去哪一篇

| 你现在想干什么 | 去哪一篇 |
|---|---|
| 搞清 `reset_control` / `reset_controller_dev` 每个字段是干什么的 | `02-datastructures.md` §2 |
| 想知道 shared / exclusive / acquired 三个 bool 到底如何改变行为 | `02-datastructures.md` §1、`03-internals.md` §B3 |
| 看「取句柄」这条路从 DTS 到句柄的完整流程 | `03-internals.md` §A2 |
| 看四个动作在核心里逐行怎么走 | `03-internals.md` §A4 |
| 看 `reset-simple` 这类通用实现分了几层 | `04-extensions.md` §1 |
| 抄一份能编的消费者/控制器骨架 | `05-usage.md` |
| DTS 里 `resets` / `reset-names` / `#reset-cells` 怎么写 | `06-devicetree.md` |
| 拿到一个错误码想反查是哪一行产生的 | `07-debug.md` §6 |
| 想用大白话理解 shared 到底和 exclusive 差在哪 | `08-feynman.md` §2 |
| 隔几个月回来复习整个子系统 | `00-overview.md` |
