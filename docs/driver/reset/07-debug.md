---
title: 调试方法
---

# reset 07 —— 调试与观测

arm64-only。本篇讲"东西不对的时候去看什么"。

## §0 先看这张分诊入口表

| 现象 | 先去 |
|---|---|
| 驱动 probe 一直不成功 | §4 |
| 拿到了错误码，想知道是哪一类问题 | §5 |
| `reset_control_status()` 返回负数 | §5.2 |
| 句柄拿到了、调用也返回 0，但硬件没反应 | §6 |
| 想看每次 assert/deassert 都发生了什么 | §3（**没有 tracepoint**，看替代办法） |
| 系统卡死在某个 reset 调用上 | §7 |
| 只想要一页速查 | §8 |

DTS 配错的定位在 `06-devicetree.md` §8；API 语义在 `01-api.md`。

---

## §1 编译期：三道门

要让一次 `reset_control_deassert()` 真的动到硬件，编译配置上要过三道门。
**任何一道关着，症状都不是编译错误，而是运行期静默无效。**

| 门 | 配置 | 关掉之后 |
|---|---|---|
| 1. 框架本身 | `CONFIG_RESET_CONTROLLER`（`drivers/reset/Kconfig:5-7`） | `include/linux/reset.h` 的桩函数全部生效：**所有函数返回 0 或 NULL，一句日志都没有**（清单见 `01-api.md` §6） |
| 2. 你平台的控制器驱动 | `CONFIG_RESET_<VENDOR>`（`drivers/reset/Kconfig` 里 33 处带 `COMPILE_TEST`） | 控制器不注册 → 消费者永远 `-EPROBE_DEFER`（这个**有**痕迹，见 §4） |
| 3. 消费者驱动自己 | `CONFIG_<你的驱动>` | 复位线没人去 deassert，但这块硬件本来也没人用 —— 不构成故障 |

### 1.1 第 1 道门是本子系统最危险的一个开关

```
CONFIG_RESET_CONTROLLER=n
  → reset_control_get_*()  返回 NULL（`_optional_` 变体）或 -ENOTSUPP
  → reset_control_deassert(NULL) 返回 0
  → 驱动的每一处错误检查都通过
  → 硬件从来没被解除复位
  → 零日志、零 WARN、零 defer
```

判据（`drivers/reset/Kconfig:7`）：`default y if ARCH_HAS_RESET_CONTROLLER`。
**arm64 上要确认的是你的平台 Kconfig 有没有 `select ARCH_HAS_RESET_CONTROLLER`**，
不能假定它默认打开。桩函数的完整清单和各自的返回值在 `01-api.md` §6。

⚠️ 这一条和 `06-devicetree.md` §8.1 最后一段是同一件事的两面：
DT 侧写得再对，框架被编译掉了也没人读。

---

## §2 日志可见性

### 2.1 `drivers/reset/core.c` 到底会打印什么

全部产出只有三类，**没有一条是 `dev_err` / `pr_err`**：

| 形式 | 数量 | 可见性 | 位置 |
|---|---:|---|---|
| `WARN_ON(...)` （无消息） | 多数 | **默认可见**，一条栈回溯 + `WARNING: ... at drivers/reset/core.c:NNN` | 21 处 WARN 的清单在 `03-internals.md` §C3 |
| `WARN(1, "reset %s (ID: %u) is not acquired\n", ...)` | 2 | 默认可见，带一句话 | `drivers/reset/core.c:479`、`:549` |
| `pr_warn("%s(): reset lookup entry badly specified, skipping\n", ...)` | 1 | 默认可见 | `drivers/reset/core.c:183`（非 DT 路径，arm64 不会遇到） |

**注意这里没有 `_once` / `_ratelimited` 变体，也没有任何 debug 级别的日志。**
后果是两头都不理想：

- **好的一面**：出现过的 WARN 一定在 `dmesg` 里，不会被限流吃掉。
- **坏的一面**：`WARN_ON` 在循环里被触发会**每次都打一份完整栈回溯**。
  典型场景是某个驱动在 runtime PM 回调里反复 deassert 一个没 acquire 的句柄
  —— `dmesg` 会被同一条回溯灌满。看到重复回溯不要以为是不同的 bug。

### 2.2 判断 WARN 是谁触发的

`WARN_ON` 不带消息，所以**唯一的线索是栈回溯的第一行行号**。
对照表（挑最常见的四个）：

| 回溯里的行号 | 含义 | 详见 |
|---|---|---|
| `core.c:454` 或 `:457` | shared 句柄上混用了两对 API | `05-usage.md` §3.3 |
| `core.c:766` | 同一条线被第二次 exclusive get | `05-usage.md` §2.4 |
| `core.c:859` | `#reset-cells` 与驱动的 `of_reset_n_cells` 不一致 | `06-devicetree.md` §2.2 |
| `core.c:946` | 驱动同时要求 `shared` 和 `acquired`（API 用法错） | `01-api.md` §2 |

栈回溯的**第二行**（调用者）才是要改的地方——core.c 里的行号只告诉你违反了哪条规则。

---

## §3 观测面：三个零

### 3.1 没有 tracepoint、没有 debugfs、没有 sysfs

验证（三条命令都是空输出）：

```sh
grep -rn 'debugfs\|sysfs\|DEVICE_ATTR' drivers/reset/     # 无输出
grep -rn 'TRACE_EVENT\|trace_' drivers/reset/             # 无输出
ls /sys/kernel/debug/reset*                               # 不存在
```

这和 clk、regulator、pinctrl 形成鲜明对照——那三个都有相当完整的 debugfs 树。
reset 框架**一个观测点都没有**：

| 你想知道 | clk / regulator 有 | reset 有 |
|---|---|---|
| 系统里注册了哪些控制器 | `/sys/kernel/debug/clk/clk_summary` | ✗ |
| 每条线现在是什么状态 | debugfs 逐项 | ✗（只能靠驱动自己调 `status()`） |
| 谁持有这个资源 | `clk_summary` 的 `enable_cnt` | ✗ |
| 每次操作的时间线 | tracepoint | ✗ |

### 3.2 那怎么办 —— 三个替代手段

按侵入性从低到高：

**(a) ftrace function tracer**（不用改代码，最先试这个）

```sh
cd /sys/kernel/debug/tracing
echo 'reset_control_*' > set_ftrace_filter
echo function > current_tracer
echo 1 > tracing_on
# ... 触发问题 ...
cat trace
```

能看到调用序列和调用者，**看不到参数**（哪条线、返回值）。

**(b) kprobe 取参数和返回值**

```sh
cd /sys/kernel/debug/tracing
# 入口：拿到 rstc 指针和它的 id 字段（偏移随内核配置变，用 pahole 确认）
echo 'p:rst_assert reset_control_assert rstc=%x0' >> kprobe_events
# 返回值
echo 'r:rst_assert_ret reset_control_assert ret=$retval' >> kprobe_events
echo 1 > events/kprobes/enable
```

⚠️ **`%x0` 是 arm64 的第一个参数寄存器**；`rstc->id` 要靠结构体偏移取，
偏移不是稳定 ABI（结构体定义见 `02-datastructures.md` §1.1）。
这条命令没有在本树上实测过——按 `99-evidence.md` §5.1 的分级，它属于"未运行"。

**(c) 直接读寄存器**

MMIO 控制器可以用 `devmem`/`busybox devmem` 读那一位。
这是**唯一能回答"硬件当前到底是什么状态"**的手段，
因为 §5.2 会讲到 `reset_control_status()` 在很多控制器上根本不可用。

一个反直觉的结论：**reset 框架的可观测性差，恰恰因为它足够简单**
——没有状态机、没有缓存、没有异步。出问题几乎总是"调用顺序"或"DT 配置"，
这两类都不需要 tracepoint 也能定位（§4、§5）。

---

## §4 probe 期定位

绝大多数 reset 相关的故障都发生在 probe 阶段。

### 4.1 `devices_deferred` 是最有用的一个文件

```sh
cat /sys/kernel/debug/devices_deferred
```

输出形如 `ff3f0000.ethernet  platform: wait for supplier ...` 或驱动自己填的原因。
**看到你的设备在里面 = 它拿不到某个资源**，其中一种可能就是复位控制器还没注册。

区分是不是 reset 导致的：

1. 在 DTS 里确认这个节点有 `resets` 属性（没有就和 reset 无关）。
2. 确认 `resets` 指向的那个控制器节点**有驱动 probe 成功**——
   看 `/sys/bus/platform/drivers/` 下有没有对应目录、
   或者 `ls /sys/firmware/devicetree/base/...` 确认 `status` 不是 `disabled`。
3. 如果控制器节点根本没被 probe，回到 §1 第 2 道门。

### 4.2 初始化次序：三个 initcall 驱动为什么存在

有三个控制器驱动**不用 `module_platform_driver`，而是手写 `xxx_initcall()` 把注册提前**
（三者的对照表在 `04-extensions.md` §4.2）：

| 驱动 | initcall 档位 | 位置 | 源码给的理由 |
|---|---|---|---|
| `drivers/reset/reset-zynqmp.c` | `arch_initcall` —— **最早的一个** | `:141` | 无注释说明，只能从档位推断"要早于绝大多数设备" |
| `drivers/reset/reset-intel-gw.c` | `postcore_initcall` | `:262` | `:255-261` 有注释：RCU 属于 Always-On 域，很多平台设备初始化时就要复位它 |
| `drivers/reset/reset-microchip-sparx5.c` | `postcore_initcall` | `:178` | `:174-177` 有注释：这是一个**全局**复位，要在内核启动尽可能早的时候发出 |

三个档位差异是实打实的：`arch_initcall` 早于 `postcore_initcall`，
两者都远早于 `module_platform_driver` 落在的 `device_initcall`。

这解释了一个现象：**同一个消费者驱动，在有的平台上从来不 defer，
在有的平台上要 defer 好几轮**。不是消费者的问题，是控制器注册得早或晚。
所以"我这个驱动在 A 平台好的、在 B 平台一直 defer"通常不是 bug，
只要 defer 最终能收敛就没事——`-EPROBE_DEFER` 必须往上传的理由也在这里
（`05-usage.md` §2.2）。

### 4.3 defer 不收敛怎么办

`-EPROBE_DEFER` 会被 driver core 反复重试，直到 late_initcall 之后放弃并打日志。
两种不收敛的情况：

| 情况 | 判据 | 处置 |
|---|---|---|
| 控制器驱动没编进内核 | `devices_deferred` 里只有消费者，没有控制器 | §1 第 2 道门 |
| 控制器节点 `status = "disabled"` | 运行时 DT 里能确认 | 改 DTS（`06-devicetree.md` §8.2） |

reset 框架**自己不参与重试**，它只在控制器不在链表里时返回 `-EPROBE_DEFER`
（`drivers/reset/core.c:854-857`）。重试完全是 driver core 的行为。

---

## §5 错误码分诊

### 5.1 `-EINVAL` 有八个语义组 —— 不看行号分不出来

`-EINVAL` 在 `drivers/reset/core.c` 里出现 **15 次**，代表**八种完全不同的问题**。
只拿到错误码是没法定位的，必须配 `dmesg` 里的 WARN 行号（§2.2）。

| 组 | 语义 | 行号 | 有 WARN？ |
|---|---|---|---|
| G1 | **你传进来的是 `ERR_PTR`**——上一步 get 失败了没检查就往下用 | `:224`、`:335`、`:407`、`:448`、`:536`、`:611`、`:648` | ✓ 每处都有 |
| G2 | **shared 句柄的计数状态不对**（混用两对 API / 不配平） | `:228`、`:345`、`:414`、`:455`、`:458`、`:543` | ✓ 每处都有 |
| G3 | 传进来的 `device_node` / `device` 是 NULL | `:827`、`:1152`、`:1256` | ✗ |
| G4 | `#reset-cells` 与 `of_reset_n_cells` 不一致 | `:860` | ✓ |
| G5 | 默认 xlate 判定 DTS 里的 id ≥ `nr_resets` | `:91` | ✗ |
| G6 | 对 **array 句柄**调了不支持的操作（`status`） | `:611`（与 G1 共用一行的复合条件） | ✓ |
| G7 | 同时要求 `shared` 和 `acquired`（API 组合非法） | `:947` | ✓ |
| G8 | `of_parse_phandle_with_args()` 自己返回的 `-EINVAL`，原样透出 | `:840` | ✗ |

分诊的两刀：

1. **有 WARN 栈回溯** → G1 / G2 / G4 / G6 / G7，看行号即可定位。
2. **无 WARN** → G3 / G5 / G8，都是 **DT 或参数问题**，去 `06-devicetree.md` §9.2。

G1 出现 7 次是有原因的：框架的**每一个对外动作函数入口**都做这个检查。
它的存在等于说"驱动不检查 get 的返回值也不会崩，但会拿到 `-EINVAL` 和一条回溯"。
反过来说：**看到 G1 的回溯，要改的是 get 之后那一行漏掉的 `IS_ERR()`**。

### 5.2 `reset_control_status()` 的三种"失败"

这是本子系统最容易误判的一个返回值。函数体很短，三条出路：

```c
int reset_control_status(struct reset_control *rstc)
{
	if (!rstc)
		return 0;

	if (WARN_ON(IS_ERR(rstc)) || reset_control_is_array(rstc))
		return -EINVAL;

	if (rstc->rcdev->ops->status)
		return rstc->rcdev->ops->status(rstc->rcdev, rstc->id);

	return -ENOTSUPP;
}
```

（`drivers/reset/core.c:605-617`）

| 返回 | 含义 | 陷阱 |
|---|---|---|
| `0` | **两种情况同码**：句柄是 NULL（`_optional_` 没拿到线），**或者**控制器报"这条线未被断言" | `if (!rstc) return 0;` 和"未复位"是同一个返回值 |
| `> 0` | 这条线**正处于被断言状态** | 非零即"被复位住了" |
| `-EINVAL` | 传了 `ERR_PTR`，或这是个 **array 句柄** | array 句柄查状态没有意义（`05-usage.md` §4.2） |
| `-ENOTSUPP` | **控制器没实现 `.status` 回调** | 见下 |

**为什么在 SCMI 平台上 `status()` 一定失败**：
`drivers/reset/reset-scmi.c` 的 ops 结构体（`:83-87`）里**只有 `.reset`、`.assert`、`.deassert`，
没有 `.status`**——SCMI 协议本身不提供"读复位状态"的命令。
同样缺 `.status` 的还有 Tegra bpmp（`drivers/reset/tegra/reset-bpmp.c:61-65`）和
只有 `.reset` 一个回调的 `drivers/reset/reset-raspberrypi.c`（`:68-71`）。
完整的回调完整性表在 `04-extensions.md` §7。

⇒ **实用结论：不要把 `reset_control_status()` 当作跨平台的探测手段。**
它在 MMIO 控制器上好用，在固件类控制器上返回 `-ENOTSUPP`。
真要确认硬件状态，只有 §3.2 (c) 那一条路。

### 5.3 其余错误码：一码一义

| 错误码 | 出现次数 | 语义 | 位置 |
|---|---:|---|---|
| `-EPERM` | 5 | **句柄没 acquire 就用了动作 API**（都在 `_released` 场景） | `:231`、`:351`、`:419`、`:481`、`:551`；其中 `:481`/`:551` 带那句 `is not acquired` |
| `-ENOTSUPP` | 3 | 控制器没实现对应回调：`reset` (`:341`)、**`assert` 且 exclusive** (`:476`)、`status` (`:616`) | `deassert` **不在这一列**，见下 |
| `-EBUSY` | 2 | 独占冲突：`acquire` 时别人占着 (`:664`)、`get_exclusive` 第二次 (`:767`) | `05-usage.md` §2.4 / §5.1 |
| `-ENODEV` | 1 | `try_module_get()` 失败 —— 控制器模块正在被卸载 | `:780` |
| `-ENOENT` | 4 | DT 里找不到：名字不在 `reset-names` (`:835`)、lookup 表没命中 (`:937`)、`resets` 属性项数为 0 (`:1156`、`:1269`) | `06-devicetree.md` §9.2 |
| `-EPROBE_DEFER` | 2 | 控制器还没注册：DT 路径 (`:855`)、lookup 路径 (`:923`) | §4 |
| `-ENOMEM` | 6 | 分配失败，无歧义 | `:152`、`:776`、`:1043`、`:1080`、`:1187`、`:1234` |
| `-EILSEQ` | 透传 | `reset-names` 属性本身编码坏了（不是名字写错） | `:832` 原样返回 |

三个动作在"控制器没实现这个回调"时的返回码**互不相同**，这是本子系统最反直觉的一处：

| 动作 | 回调缺失时 | 位置 |
|---|---|---|
| `reset` | `-ENOTSUPP`，**shared/exclusive 一样**（判断在分岔之前） | `:340-341` |
| `assert` | **shared 返回 0、exclusive 返回 `-ENOTSUPP`** | `:467-468` vs `:475-476` |
| `deassert` | **两种语义都返回 0**（注释 `:555-560` 解释：假定这条线由 `.reset()` 自解除） | `:562-563` |

⇒ 所以"同一块硬件换个 get 变体错误码就变了"只发生在 `assert` 上
（`05-usage.md` §3.3 末尾）；`deassert` 是**一律成功**，这才是更危险的那一个
——它意味着 `deassert()` 返回 0 完全不能证明硬件被解除了复位（`08-feynman.md` §5）。

---

## §6 最坏的一类：调用全部返回 0，硬件没反应

这一类没有任何错误码和日志，只能靠排除法。按发生概率排：

| # | 原因 | 一句话验证 |
|---|---|---|
| 1 | `CONFIG_RESET_CONTROLLER=n`，全套 API 是桩函数 | `zcat /proc/config.gz \| grep RESET_CONTROLLER` |
| 2 | 用了 `_optional_` 变体，DTS 里根本没写 `resets`，句柄是 NULL，动作函数走 `if (!rstc) return 0` | 在驱动里 `WARN_ON(!priv->rstc)` 一次 |
| 3 | cell 里的数字选错了线（改到了别人的位上） | `devmem` 读寄存器；对 hi3660 一类要核对手册（`06-devicetree.md` §4.2） |
| 4 | `ti,reset-bits` 的 cell 数不是 7 的倍数，后面几条线被静默丢弃 | 数 cell 总数除 7 看余数（`06-devicetree.md` §4.3） |
| 5 | 控制器**根本没实现**那个回调：`.deassert` 缺失时核心一律返回 0，`.assert` 缺失时 shared 分支返回 0（§5.3 那张三行表） | 看那个控制器驱动的 ops 表（`04-extensions.md` §7） |
| 6 | shared 句柄的 `deassert_count` 没归零，最后那次 assert 没落到硬件 | 计数语义见 `03-internals.md` §B2 |

第 6 条最阴：**shared 语义下"我调了 assert 但线没被断言"是设计行为，不是 bug**
（`Documentation/driver-api/reset.rst:98-104` 明确写了没有任何保证线真的被断言）。
如果你需要"我说断言就必须断言"，那就不该用 shared。

---

## §7 卡死与并发

### 7.1 唯一一把锁

框架有两把全局互斥锁（`drivers/reset/core.c:20-24`，详见 `02-datastructures.md` §5）：

| 锁 | 保护 | arm64 相关性 |
|---|---|---|
| `reset_list_mutex`（`:20`） | `reset_controller_list`（`:21`）+ 每个 rcdev 的 `reset_control_head` | **全部操作都要过它** |
| `reset_lookup_mutex`（`:23`） | `reset_lookup_list`（`:24`），非 DT 的 lookup 表 | 不涉及（全树只有一个 arm32 使用者，见 `05-usage.md` §9） |

实际会出问题的只有 `reset_list_mutex`，而死锁的形状只有一种：

**在控制器的回调里调用任何 `reset_control_*()` 函数。**

- `of_xlate` 是**持锁**调用的（`drivers/reset/core.c:864`，锁在 `:845` 拿的），
  在里面调 `reset_control_get()` → 自己等自己 → **确定死锁**。
- 四个动作回调（`assert`/`deassert`/`reset`/`status`）是**放锁之后**调用的
  （四个调用点在 `03-internals.md` §B1.4），在里面调 reset API **不会死锁**，
  但会重入框架，语义上仍然应当避免。

`lockdep` 能抓到前者（开 `CONFIG_PROVE_LOCKING` 会打出 `possible recursive locking`）。

### 7.2 睡眠上下文

⚠️ **框架里没有 `might_sleep()`，也没有任何上下文检查**（`05-usage.md` §6.3）。
在中断上下文里调 `reset_control_assert()`：

| 控制器类型 | 结果 |
|---|---|
| MMIO（`reset-simple` 等） | 正常工作 |
| syscon/regmap | 取决于 regmap 类型；MMIO regmap 可以 |
| **SCMI / ti-sci / bpmp / rpi 固件** | **在原子上下文里睡眠** → `BUG: scheduling while atomic` |

开 `CONFIG_DEBUG_ATOMIC_SLEEP` 才能在第一时间抓到。
这条是**平台移植时的高发问题**：同一份消费者驱动，在 MMIO 平台上跑得好，
换到固件复位的平台上就炸。

---

## §8 一页速查

```
┌─ 编译期 ────────────────────────────────────────────────┐
│ CONFIG_RESET_CONTROLLER=y ?   （=n → 全部 API 变桩函数） │
│ CONFIG_RESET_<你的平台> =y ?   （=n → 永远 defer）       │
│ 想抓上下文错误：CONFIG_DEBUG_ATOMIC_SLEEP + PROVE_LOCKING│
└──────────────────────────────────────────────────────────┘

┌─ 观测面 ────────────────────────────────────────────────┐
│ tracepoint  ✗    debugfs  ✗    sysfs  ✗                 │
│ 替代：ftrace set_ftrace_filter='reset_control_*'         │
│       kprobe_events（取参数/返回值）                     │
│       devmem（唯一能看硬件真实状态的手段）               │
└──────────────────────────────────────────────────────────┘

┌─ probe 不成功 ──────────────────────────────────────────┐
│ cat /sys/kernel/debug/devices_deferred                  │
│ → 在里面：控制器没注册（驱动没编 / status=disabled）    │
│ → 不在：看驱动自己打的 dev_err_probe 消息               │
└──────────────────────────────────────────────────────────┘

┌─ 拿到错误码 ────────────────────────────────────────────┐
│ -EPROBE_DEFER  控制器还没注册                → 往上传    │
│ -ENOENT        reset-names 没这名字 / 没 resets 属性     │
│ -EINVAL + WARN 看 core.c 行号，八个语义组（§5.1）        │
│ -EINVAL 无WARN DT / 参数问题                             │
│ -EBUSY         独占冲突（谁先拿了）                      │
│ -EPERM         没 acquire 就用（_released 场景）          │
│ -ENOTSUPP      控制器没实现这个回调（固件类常见）        │
│ -ENODEV        控制器模块正在卸载                        │
└──────────────────────────────────────────────────────────┘

┌─ 全部返回 0 但硬件没动 ─────────────────────────────────┐
│ 1. 框架被编译掉  2. 句柄是 NULL（_optional_）            │
│ 3. cell 数字错   4. ti,reset-bits 截断                   │
│ 5. 回调是空实现  6. shared 计数没归零（设计行为）      │
└──────────────────────────────────────────────────────────┘
```

### 8.1 去哪一篇

| 你接着想做的事 | 去处 |
|---|---|
| DTS 侧的定位 | `06-devicetree.md` §8、§9 |
| 错误码的完整语义表 | `01-api.md` §7 |
| 计数、锁、回调调用点的逐行 | `03-internals.md` |
| 为什么 shared 的 assert 可能不落到硬件 | `08-feynman.md` §2 |
| 各控制器家族缺哪个回调 | `04-extensions.md` §7 |
