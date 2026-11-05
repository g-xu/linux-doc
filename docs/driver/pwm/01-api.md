---
title: API 参考
---

# pwm 子系统 API 全景

> **基线**：`linux-6.1.158`（`f6e38ae624cf`），**arm64-only**。
> `drivers/pwm` 与 `include/linux/pwm.h` 在本树里**完全干净**（`git status --porcelain` 零输出），
> 所以本套文档描述的就是上游 6.1 的原貌，没有本树补丁的干扰。
> 本篇只回答"有哪些接口、各自的契约是什么"。为什么这样设计在 02 篇，怎么实现在 03 篇。

---

## §0 代码地图

### §0.1 子系统类型判定

两道门都要过一遍，结论决定后面九篇的写法：

| 判据 | 命令 | 结果 | 结论 |
|---|---|---|---|
| 有没有自己的总线 | `grep -rn 'struct bus_type' drivers/pwm include/linux/pwm.h` | **零输出** | **框架型**，不是总线型。没有 `probe`、没有 `match`、没有 `device_driver` |
| 有没有 uapi 头 | `ls include/uapi/linux/pwm*` | **不存在** | **没有 uapi ABI**。用户态面全部经由 sysfs |
| 有没有 class | `grep -n 'class_register' drivers/pwm/*.c` | `sysfs.c:544` | 有一个 `pwm_class`，用来撑起 `/sys/class/pwm/` |

⚠️ 这三条合起来给出一个不太常见的组合：**框架型 + 有 class + 无 uapi**。
和 watchdog、tty 那种"框架型 + 有 uapi ioctl"不同，pwm 的用户态契约是**纯文本 sysfs**，
所以"改一个数值就破 ABI"的风险点全在 `/sys/class/pwm/` 的属性名与取值上，不在头文件里。

### §0.2 文件清单

`drivers/pwm/` 一共 **64 个 `.c` / 22949 行**，但**核心只有两个文件、1734 行**：

| 文件 | 行数 | 职责 | 导出符号数 |
|---|---|---|---|
| `drivers/pwm/core.c` | **1188** | 全部框架逻辑：注册、查找、状态应用、DT/静态查表、debugfs | **17**（全部 `EXPORT_SYMBOL_GPL`） |
| `drivers/pwm/sysfs.c` | **546** | `/sys/class/pwm/` 全部实现 + 导出通道的 PM | **0** |
| `drivers/pwm/Kconfig` | 678 | 3 个核心选项 + 约 60 个控制器驱动选项 | — |
| `drivers/pwm/Makefile` | 65 | — | — |
| 其余 62 个 `.c` | ~21200 | 具体控制器驱动，本套文档只当"契约的使用样本"看 | — |

⚠️ `sysfs.c` **一个符号都不导出**。它靠 `include/linux/pwm.h` 里 `CONFIG_PWM_SYSFS` 段
声明的两个原型被 `core.c` 直接调用（同一个模块内），所以外部驱动**无法**参与
sysfs 层——想给某个通道加属性是做不到的。

### §0.3 头文件清单

| 头文件 | 行数 | 内容 |
|---|---|---|
| `include/linux/pwm.h` | **608** | 唯一的公共头：8 个结构、2 个枚举、17 个导出函数的原型、约 15 个内联辅助、`CONFIG_PWM=n` 的全套桩函数 |
| `include/trace/events/pwm.h` | 58 | 两个 tracepoint 的定义 |
| `include/dt-bindings/pwm/pwm.h` | 15 | **只有一个宏** `PWM_POLARITY_INVERTED` |
| `include/linux/pwm_backlight.h` | — | 只服务 `pwm_bl` 这一个消费者的平台数据，不属框架 |

⚠️ `include/linux/pwm.h` 有一个结构上的特点：**同一批函数出现两次**——
`CONFIG_PWM=y` 下是原型声明，`#else` 下是一整套 `static inline` 桩。
两段的返回值并不总是一致（§8.3 给了逐条对照），改这个头文件时两边都要动。

### §0.4 Kconfig

| 选项 | 位置 | 类型 | 默认 | 作用 |
|---|---|---|---|---|
| `PWM` | `drivers/pwm/Kconfig:2-3` | `menuconfig bool` | — | 关掉之后 `pwm.h` 全部走桩函数分支 |
| `PWM_SYSFS` | `drivers/pwm/Kconfig:32-34` | `bool` | **`default y if SYSFS`** | 决定 `/sys/class/pwm/` 存不存在 |
| `PWM_DEBUG` | `drivers/pwm/Kconfig:36-43` | `bool`，依赖 `DEBUG_KERNEL` | n | 打开一整套**驱动一致性检查**，见 07 篇 |

⚠️ `PWM_SYSFS` 是**隐式打开的**（`default y if SYSFS`），没有 prompt 字符串，
在 menuconfig 里翻不到。所以"我没主动开 sysfs 为什么有 `/sys/class/pwm/`"的答案在这里。

⚠️ `PWM_DEBUG` 不是"多打点日志"那种调试选项，它会**改变执行路径**：
每次 apply 之后核心会**回读并重新 apply 一次**来验证驱动的幂等性。
生产内核不能开——它把每次配置变成两到三次硬件访问。

### §0.5 官方文档

`Documentation/driver-api/pwm.rst`，**179 行，只有这一篇**。
七节：Identifying PWMs / Using PWMs / sysfs interface / Implementing a PWM driver /
**Locking** / Helpers。DT 绑定在 `Documentation/devicetree/bindings/pwm/`（51 项 / 25 个 `.yaml`）。

⚠️ 这 179 行值得先读原文再读本套文档，原因是**它自陈了两处设计债**：
Locking 一节（`pwm.rst:165-172`）承认核心不为 `pwm_enable`/`disable`/`config` 加锁，
并写着"should be fixed soon"；Using PWMs 一节（`:68-72`）承认消费者**拿不到硬件的实际配置**。
这两句在代码里找不到对应的 `FIXME`（核心两个 `.c` 加头文件的 `FIXME/TODO/XXX` 命中数为 **0**），
是本子系统"设计债只登记在文档里"的特殊之处。

---

## §1 API 全景：五族

`include/linux/pwm.h` 里的接口按**谁调用**分成五族，这是不迷路的第一层索引：

| 族 | 谁用 | 数量 | 命名特征 | 详见 |
|---|---|---|---|---|
| **消费者 API** | 要输出 PWM 波形的驱动（背光、马达、蜂鸣器、风扇） | 10 个导出 + 约 12 个内联 | `pwm_get` / `pwm_put` / `pwm_apply_state` / `pwm_*_state` | §2 |
| **provider API** | PWM 控制器驱动 | 7 个导出 | `pwmchip_*` / `pwm_set_chip_data` / `of_pwm_*_xlate` | §3 |
| **`pwm_ops` 回调** | 控制器驱动**实现**，核心调用 | 6 个成员 | `.request/.free/.capture/.apply/.get_state/.owner` | §4 |
| **静态查表** | 板级代码（非 DT 平台） | 2 个函数 + 2 个宏 | `pwm_add_table` / `PWM_LOOKUP` | §5 |
| **sysfs ABI** | 用户态 | 3 + 5 个属性 | `/sys/class/pwm/pwmchipN/` | §6 |

**17 个导出符号一览**（全部在 `core.c`，全部 `EXPORT_SYMBOL_GPL`，按族归类）：

```
消费者（10）: pwm_request  pwm_free  pwm_apply_state  pwm_capture  pwm_adjust_config
              pwm_get  pwm_put  devm_pwm_get  devm_fwnode_pwm_get  pwm_request_from_chip
provider（7）: pwmchip_add  pwmchip_remove  devm_pwmchip_add
              pwm_set_chip_data  pwm_get_chip_data
              of_pwm_xlate_with_flags  of_pwm_single_xlate
```

⚠️ `pwm_request_from_chip` 归在消费者一栏是因为它的调用方是消费者语义，
但它**只被控制器驱动自己用**（一个 chip 想给自己的某条通道找个 `pwm_device`）。
`pwm_add_table` / `pwm_remove_table` 在头文件里有声明（`include/linux/pwm.h:583-584`）
但**没有 `EXPORT_SYMBOL`** ⇒ 只能被内建代码调用，模块用不了。这是一处真实的不对称。

---

## §2 消费者 API

### §2.1 获取与释放

| API | 说明 | 备注 |
|---|---|---|
| `pwm_get(dev, con_id)` | 现代入口。按 DT → ACPI → 静态查表三条路依次尝试 | 位于 `core.c`。**推荐入口**。返回 `ERR_PTR` 而非 NULL |
| `pwm_put(pwm)` | 释放 `pwm_get` 拿到的引用 | 位于 `core.c`。⚠️ **不会关闭输出**，见 §2.5 |
| `devm_pwm_get(dev, con_id)` | `pwm_get` 的 devm 版，随 `dev` 自动释放 | 位于 `core.c`。**新驱动一律用这个** |
| `devm_fwnode_pwm_get(dev, fwnode, con_id)` | 从**指定的 fwnode**（不一定是 `dev` 自己的）取 PWM | 位于 `core.c`。用于一个 dev 下挂多个子节点、每个子节点各有 `pwms` 的场景（如多路 LED） |
| `pwm_request(pwm_id, label)` | **已废弃**。按全局整数 ID 直接取 | 位于 `core.c`。⚠️ 依赖全局编号空间，见 §9.2 |
| `pwm_free(pwm)` | 与 `pwm_request` 配对 | 位于 `core.c`。⚠️ **它和 `pwm_put` 是同一件事**，见 §9.1 |
| `pwm_request_from_chip(chip, index, label)` | 从**指定 chip 的指定通道**取，绕过全部查找逻辑 | 位于 `core.c`。给控制器驱动自用 |

⚠️ **`con_id` 可以传 `NULL`**。传 `NULL` 时 DT 路径取 `pwms` 的第 0 项，
静态查表路径只按 `dev_id` 匹配。只有一路 PWM 的消费者通常就传 `NULL`。

### §2.2 状态应用：唯一的正路

| API | 说明 | 备注 |
|---|---|---|
| `pwm_apply_state(pwm, state)` | **唯一真正改硬件的接口**。把一个完整的 `pwm_state` 一次性下发 | 位于 `core.c`。⚠️ `might_sleep()`，**不能在原子上下文调**；有全等短路，见 §2.6 |
| `pwm_config(pwm, duty_ns, period_ns)` | 旧式包装：读当前 state、改两个字段、再 apply | `pwm.h` 的内联。⚠️ 官方文档明确劝退，见 §9.3 |
| `pwm_enable(pwm)` | 旧式包装：把 `enabled` 置 1 再 apply | `pwm.h` 的内联。已 enabled 时直接返回 0 |
| `pwm_disable(pwm)` | 旧式包装：把 `enabled` 清 0 再 apply | `pwm.h` 的内联。⚠️ **返回类型是 `void`，无法报错** |
| `pwm_adjust_config(pwm)` | 用板级参考配置（`pwm_args`）把当前配置"校正"一遍 | 位于 `core.c`。保留占空比**比例**，见 §2.7 |
| `pwm_apply_args(pwm)` | 把 `pwm_args` 里的周期与极性直接写下去 | `pwm.h` 的内联。⚠️ **会先强制关闭输出**，见 §2.7 |

### §2.3 查询

| API | 说明 | 备注 |
|---|---|---|
| `pwm_get_state(pwm, state)` | 取"上一次成功 apply 下去的 state" | `pwm.h` 的内联。⚠️⚠️ **不是硬件实际值**，见 §2.4 |
| `pwm_is_enabled(pwm)` | 上次 apply 的 `enabled` 字段 | `pwm.h` 的内联。同上警告 |
| `pwm_get_period(pwm)` | 上次 apply 的 `period` | `pwm.h` 的内联 |
| `pwm_get_duty_cycle(pwm)` | 上次 apply 的 `duty_cycle` | `pwm.h` 的内联 |
| `pwm_get_polarity(pwm)` | 上次 apply 的 `polarity` | `pwm.h` 的内联 |
| `pwm_get_args(pwm)` | 取板级参考配置（**只有 2 个字段**：周期、极性） | `pwm.h` 的内联。这是 DT/查表里写的值，不是当前值 |
| `pwm_capture(pwm, result, timeout)` | 反向：**测量**外部输入信号的周期与占空比 | 位于 `core.c`。⚠️ 不支持时返回 `-ENOSYS`；持全局锁，见 §9.5 |

### §2.4 头号陷阱：`pwm_get_state()` 给的不是硬件状态

官方文档把这件事说得毫不含糊（`Documentation/driver-api/pwm.rst:68-72`）：

> There is currently no way for consumers to get the actually implemented settings.

代码上的机制是：`pwm_apply_state()` 成功后把**调用者传进来的那份 `state`**
原样存进 `pwm->state`（`core.c:583`），`pwm_get_state()` 只是把这份副本拷出来。
中间没有任何"回读硬件"的动作。

后果三条，排查时要一并记住：

1. 请求 `period = 3333 ns`、硬件只能做 `3300 ns`，`pwm_get_state()` 仍然返回 **3333**。
2. 驱动的 `.get_state()` 回调**只在 request 时被调用一次**（`core.c:110-111`，
   用来初始化 `pwm->state`），之后每次查询都不会再走它。
3. ⇒ **想知道硬件真实值只有两条路**：读控制器的寄存器（`devmem`/驱动自己的 debugfs），
   或者开 `CONFIG_PWM_DEBUG` 让核心替你做回读比对（07 篇给了完整方法）。

### §2.5 第二个陷阱：`pwm_put()` 不关输出

`pwm_put()` 做的事是清 `PWMF_REQUESTED` 标志、调驱动的 `.free`、放模块引用。
**它不调 `pwm_disable()`**。

⇒ 一个模块 `rmmod` 之后，风扇可能还在转、背光可能还亮着。
正确写法是**显式 disable 再 put**：

```c
/* 消费者卸载路径的标准写法 */
pwm_disable(my->pwm);      /* 必须自己写这一行 */
pwm_put(my->pwm);          /* 用 devm_pwm_get 时这一行也不用写 */
```

⚠️ 用 `devm_pwm_get()` 也**不会**自动 disable——devm 只负责 put。
这是本子系统最容易造成"现象诡异"的一处：卸载了驱动，硬件还在动。

### §2.6 第三个陷阱：apply 有一条全等短路

`pwm_apply_state()` 在调驱动之前会比较 `pwm_state` 的**全部 5 个字段**
（`core.c:566-571`）：`period` / `duty_cycle` / `enabled` / `polarity` / `usage_power`。
全等就 **直接 `return 0`**，驱动的 `.apply` 根本不会被调到。

⇒ 两个实际影响：

1. 想用"apply 同一个 state"来**强制刷新硬件寄存器**是做不到的。
   （硬件被别人改过、或从低功耗状态回来时，核心并不知道自己的缓存已经过期。）
2. 排查"我调了 apply 但 ftrace 里没看到驱动函数"时，先怀疑这条短路，
   而不是怀疑函数指针没填——`trace_pwm_apply` 也在短路之后，所以 tracepoint 同样不触发。

### §2.7 `pwm_apply_args()` 与 `pwm_adjust_config()` 的分工

两个都是"用板级参考配置修正当前配置"，但动作差别很大：

| | `pwm_apply_args(pwm)` | `pwm_adjust_config(pwm)` |
|---|---|---|
| 位置 | `pwm.h` 的内联 | `core.c` 的导出函数 |
| 做什么 | 把 `pwm_args` 的 `period` 和 `polarity` 直接写下去 | 保留**占空比的比例**，换算到 `pwm_args` 的周期上 |
| 对输出的影响 | ⚠️ **先强制 `enabled = false`** | 尽量不打断输出 |
| 什么时候用 | 老驱动的"接管硬件时先归零"语义 | 需要从 bootloader 留下的配置平滑接管 |

⚠️ `pwm_apply_args()` 上方有一段 20 行的注释（`include/linux/pwm.h:526-545`）专门解释
"为什么要先关"：因为它无法知道当前极性下的占空比换算是否安全，
关掉是唯一确定安全的做法。这段注释同时把这个函数标成了历史包容措施——
**新代码不该用它**，应该自己构造 `pwm_state` 走 `pwm_apply_state()`。

### §2.8 换算辅助（纯计算，不碰硬件）

| API | 说明 | 备注 |
|---|---|---|
| `pwm_init_state(pwm, state)` | 用 `pwm_args` 初始化一个 `pwm_state`：取板级周期与极性，占空比清零 | `pwm.h` 的内联。**构造 state 的标准起点** |
| `pwm_set_period(state, period)` | 写 `state->period` | `pwm.h` 的内联。作用于 state，不碰硬件 |
| `pwm_set_duty_cycle(state, duty)` | 写 `state->duty_cycle` | 同上 |
| `pwm_get_relative_duty_cycle(state, scale)` | 把占空比换成"scale 分之几"（如 scale=100 得百分比） | `pwm.h` 的内联 |
| `pwm_set_relative_duty_cycle(state, duty, scale)` | 反向：按比例设占空比 | `pwm.h` 的内联。⚠️ 有返回值，`scale` 为 0 或 `duty > scale` 时返回 `-EINVAL` |
| `pwm_set_chip_data` / `pwm_get_chip_data` | 给单条通道挂/取驱动私有指针 | 位于 `core.c`。属 provider 侧，列此处便于对照 |

⚠️ 前五个是**纯 state 操作**，名字里没有 "apply" 就一定不碰硬件。
`pwm_set_period(state, x)` 之后必须自己调 `pwm_apply_state()`，
这是新手最常漏的一步——因为 `pwm_config()` 那个旧接口是"设完就生效"的。

### §2.9 标准使用骨架

```c
/* 现代写法：三步 */
struct pwm_state state;

pwm = devm_pwm_get(dev, NULL);          /* 1. 取 */
if (IS_ERR(pwm))
        return dev_err_probe(dev, PTR_ERR(pwm), "no pwm\n");

pwm_init_state(pwm, &state);            /* 2. 以板级参考配置为起点 */
pwm_set_relative_duty_cycle(&state, 50, 100);   /* 50% */
state.enabled = true;

return pwm_apply_state(pwm, &state);    /* 3. 一次性下发 */
```

⚠️ `pwm_get` 家族返回 `ERR_PTR`，**不是 NULL**。必须用 `IS_ERR()` 判。
其中 `-EPROBE_DEFER` 在 `core.c` 里有 4 处返回点，必须原样往上返（不能吞掉、
也不能翻译成别的错误码），否则控制器晚于消费者注册时消费者永远起不来。

---

## §3 provider API：控制器驱动用的 7 个

### §3.1 注册与注销

| API | 说明 | 备注 |
|---|---|---|
| `pwmchip_add(chip)` | 注册一个控制器。分配通道数组、占全局编号、挂链表、建 sysfs | 位于 `core.c`。返回 0 或负错误码 |
| `pwmchip_remove(chip)` | 注销。拆 sysfs、拆链表、释放通道数组 | 位于 `core.c`。⚠️ **返回类型是 `void`**，见 §3.3 |
| `devm_pwmchip_add(dev, chip)` | `pwmchip_add` 的 devm 版 | 位于 `core.c`。**新驱动一律用这个**（树内 32 处用它、44 处用裸版） |

`pwmchip_add()` 的入参契约（`core.c:263-269` 的两道门）：

| 字段 | 谁填 | 必填 | 说明 |
|---|---|---|---|
| `chip->dev` | 驱动 | **是** | 空则 `-EINVAL`。同时决定 DT 解析、device link、sysfs 父设备 |
| `chip->ops` | 驱动 | **是** | 空则 `-EINVAL`；`.apply` 为空也 `-EINVAL` |
| `chip->npwm` | 驱动 | **是** | 为 0 则 `-EINVAL` |
| `chip->of_xlate` | 可选 | 否 | 不填则核心自动填 `of_pwm_xlate_with_flags` |
| `chip->of_pwm_n_cells` | 可选 | 否 | 不填则从 DT 的 `#pwm-cells` 读，**读不到默认 2** |
| `chip->base` | ⚠️ **不要填** | — | **它是输出**，见 §3.2 |
| `chip->list` / `chip->pwms` | 核心 | — | 注释里写明 "only used internally by the PWM framework" |

### §3.2 `chip->base` 是输出而不是输入

这是一处容易被老代码经验带偏的地方。`pwmchip_add()` 里的分配是：

```c
ret = alloc_pwms(chip->npwm);   /* core.c:272，只传 count，不传期望的 base */
if (ret < 0)
        goto out;
chip->base = ret;               /* core.c:276，无条件覆盖 */
```

`alloc_pwms()` 的签名就是 `static int alloc_pwms(unsigned int count)`（`core.c:40`）——
**它根本不接受"我想要哪个 base"这个参数**。它在 `allocated_pwms` 位图里找第一段
连续的 `count` 个空位，找不到就 `-ENOSPC`。

⇒ 三个后果：

1. **驱动无法固定自己的全局编号。** 想让某个控制器永远是 `pwmchip0` 是做不到的。
2. 编号取决于**注册顺序**，注册顺序又取决于 probe 顺序，所以 `/sys/class/pwm/` 下的
   `pwmchipN` 编号在不同启动之间**可能不同**（尤其模块化的控制器）。
3. 位图总容量是 `MAX_PWMS = 1024`（`core.c:26`），是整个系统所有 PWM 通道的**总上限**。

⚠️ 用户态脚本里硬编码 `pwmchip0` 是常见的现场故障源。稳定的做法是遍历
`/sys/class/pwm/pwmchip*/` 反查 `../of_node` 或父设备名。

### §3.3 `pwmchip_remove()` 的 kerneldoc 是过期的

`core.c:314-321` 的 kerneldoc 写着：

> Removes a PWM chip. This function **may return busy** if the PWM chip provides
> a PWM device that is still requested.
> Returns: **0 on success or a negative error code on failure.**

而紧接着的函数原型是（`core.c:323`）：

```c
void pwmchip_remove(struct pwm_chip *chip)
```

**函数体里也没有任何 `PWMF_REQUESTED` 检查。** 它直接摘链表、`free_pwms()`、`kfree(chip->pwms)`。

⇒ 以代码为准的结论：**移除一个仍被消费者持有的 chip 不会失败，而是直接把
`chip->pwms` 数组释放掉**，消费者手里的 `struct pwm_device *` 立刻变成悬空指针。
"消费者还活着"这件事只由 `pwm_ops.owner` 挡着一半——它挡的是 `rmmod`，
挡不了驱动自己在 `remove()` 里主动调 `pwmchip_remove()`。

⚠️ 排查"卸载 PWM 控制器之后消费者崩了"时，方向就在这里，不要去查消费者。
这条设计债的完整评价在 03 篇 Part C。

### §3.4 通道私有数据与 DT 翻译

| API | 说明 | 备注 |
|---|---|---|
| `pwm_set_chip_data(pwm, data)` | 给**单条通道**挂驱动私有指针 | 位于 `core.c`。⚠️ **只有 `!pwm` 时返回 `-EINVAL`**，其余情况恒成功 |
| `pwm_get_chip_data(pwm)` | 取回上面挂的指针 | 位于 `core.c`。`pwm` 为 NULL 时返回 NULL |
| `of_pwm_xlate_with_flags(pc, args)` | 默认的 DT 翻译器：**第 2 个 cell 是周期，第 3 个 cell 是 flags** | 位于 `core.c`。核心在不填 `of_xlate` 时自动用它 |
| `of_pwm_single_xlate(pc, args)` | 单通道控制器专用：**没有通道号 cell**，第 1 个 cell 就是周期 | 位于 `core.c`。树内只有 4 处使用 |

⚠️ `pwm_set_chip_data()` 在 6.1 里**没有任何时机限制**——函数体只有三行
（`core.c:213-221`）：`!pwm` 返回 `-EINVAL`，否则 `pwm->chip_data = data; return 0;`。
**没有 `PWMF_REQUESTED` 检查**（老内核里有过，6.1 已经去掉了）。
⇒ 在 `probe()` 里直接对 `&chip->pwms[i]` 调它也能成功。
⚠️ 但 kerneldoc（`core.c:206-212`）只写 "Returns: 0 on success or a negative
error code on failure"，没说什么时候会失败——**唯一的失败条件是空指针**。

⚠️ 一个必须知道的清理动作：`pwm_put()` 会调 `pwm_set_chip_data(pwm, NULL)`
（`core.c:1030`）⇒ **释放通道时私有指针被清零**。所以驱动的 `.request`
必须每次重新设置它，不能指望上次的值还在。

要挂"整个 chip 的私有数据"用 `container_of(chip, ...)` 或 `dev_get_drvdata()`，
不要用这个函数——名字里的 "chip" 是误导，它是**通道级**的。

---

## §4 `pwm_ops`：驱动要填的 6 个成员

| 成员 | 必填 | 核心什么时候调 | 契约 |
|---|---|---|---|
| `.apply` | **是** | 每次 `pwm_apply_state()`（除全等短路） | 原子地把整个 `state` 落到硬件。⚠️ 唯一必填项 |
| `.get_state` | 强烈建议 | ⚠️ **在 request 时，每条通道一次**（不是注册时——kerneldoc 说错了，见下） | 读出硬件当前配置，用于初始化 `pwm->state` |
| `.request` | 否 | `pwm_get`/`pwm_request` 首次拿到该通道时 | 上电、取时钟、挂私有数据 |
| `.free` | 否 | `pwm_put`/`pwm_free` 时 | 与 `.request` 对称。⚠️ **不要在这里关输出**（核心不保证语义） |
| `.capture` | 否 | `pwm_capture()` | 不填时 `pwm_capture()` 返回 `-ENOSYS` |
| `.owner` | 建议 | 核心自己 `try_module_get` | 注释：`"helps prevent removal of modules exporting active PWMs"` |

⚠️⚠️ **`.get_state` 的 kerneldoc 把调用时机写错了。** 原文
（`include/linux/pwm.h:267-269`）说：

> get_state: get the current PWM state. This function is **only called once per PWM
> device when the PWM chip is registered.**

**代码里不是这样。** `pwmchip_add()`（`core.c:258-311`）的通道初始化循环
（`:284-292`）只设 `chip`/`pwm`/`hwpwm` 三个字段并插 radix tree，
**根本没有调 `.get_state`**。真正的调用点是
`pwm_device_request()`（`core.c:110-111`）——即**每次通道被 request 时**
（`pwm_get()` 或 sysfs export）调一次。

| 说法 | 时机 | 频率 |
|---|---|---|
| kerneldoc 声称 | `pwmchip_add()` 注册时 | 每通道 1 次，开机后就固定 |
| ⚠️ **代码实际** | `pwm_device_request()`（`core.c:110`） | **每次 request 一次**——put 再 get 会再调一次 |

⇒ 两个实际后果：
1. **驱动的 `.get_state` 会被多次调用**（每次 request），必须可重入、
   不能假设"只在 probe 阶段跑一次"。
2. `pwm_get_state()` 的返回值在**刚 request 之后**确实反映硬件，
   之后就退化成"上次请求值"——这才是 §2.4 那个陷阱的准确机制。

⚠️ 同一处（`core.c:112`）还有一件与命名有关的事：
`trace_pwm_get` 这个 **tracepoint 在这里触发**，即
"从硬件读到了 state"，**不是**"消费者拿到了一个 pwm"。
⇒ `pwm_get` 这个 tracepoint 名字与 `pwm_get()` 这个函数**没有对应关系**：
驱动没有 `.get_state` 时，`pwm_get()` 调用成功也不会产生任何 trace 事件。
观测面的完整讨论在 07 篇。

⚠️ `.apply` **不是**只在"值变了"时被调。它在**全等以外的任何变化**上都被调，
包括只改 `usage_power` 这种不影响波形的字段。驱动的 `.apply` 必须能处理
"period/duty 都没变、只有别的字段变了"的调用。

⚠️ 不填 `.get_state` 时，开了 `CONFIG_PWM_DEBUG` 的内核会在注册时打一条
`dev_warn`：`"Please implement the .get_state() callback\n"`（`core.c:244-245`）。
默认配置下**这条警告不打**，所以"我的驱动没实现 get_state 也没人提醒"是正常现象。

### §4.1 三个旧回调已经不存在

官方文档（`Documentation/driver-api/pwm.rst:153-156`）说：

> Drivers are encouraged to implement `->apply()` instead of the legacy
> `->enable()`, `->disable()` and `->config()` methods.

⚠️ 但在 6.1 的 `struct pwm_ops` 里，**`.enable` / `.disable` / `.config` 这三个成员
已经彻底删掉了**——不是"不鼓励"，是根本没有这三个字段。这句文档是遗留表述。
6.1 上写驱动只有 `.apply` 一条路，`pwm_enable()`/`pwm_disable()`/`pwm_config()`
是**消费者侧**的内联包装，最终都落到同一个 `.apply`。

### §4.2 驱动骨架

```c
static int foo_pwm_apply(struct pwm_chip *chip, struct pwm_device *pwm,
                         const struct pwm_state *state)
{
        struct foo *foo = container_of(chip, struct foo, chip);

        if (!state->enabled) {
                foo_stop(foo, pwm->hwpwm);      /* 用 hwpwm，不是 pwm->pwm */
                return 0;
        }
        /* 向下取整：见 07 篇讲 CONFIG_PWM_DEBUG 的那节 */
        return foo_program(foo, pwm->hwpwm, state->period,
                           state->duty_cycle, state->polarity);
}

static const struct pwm_ops foo_pwm_ops = {
        .apply     = foo_pwm_apply,
        .get_state = foo_pwm_get_state,
        .owner     = THIS_MODULE,
};

/* probe 里 */
foo->chip.dev  = dev;
foo->chip.ops  = &foo_pwm_ops;
foo->chip.npwm = 4;
return devm_pwmchip_add(dev, &foo->chip);
```

⚠️ 索引一定用 **`pwm->hwpwm`**（本 chip 内的 0 起序号），不是 `pwm->pwm`（全局编号）。
把 `pwm->pwm` 当寄存器索引用是本子系统最经典的一个 bug——单 chip 系统上它俩相等，
所以测试能过，插上第二个控制器就错。名字辨析见 §9.2。

---

## §5 静态查表：非 DT 平台的路

| API / 宏 | 说明 | 备注 |
|---|---|---|
| `PWM_LOOKUP(provider, index, dev_id, con_id, period, polarity)` | 构造一条静态查表项 | `pwm.h` 的宏。⚠️ 展开成 `PWM_LOOKUP_WITH_MODULE(..., NULL)` |
| `PWM_LOOKUP_WITH_MODULE(...)` | 同上，多一个 `module` 参数 | `pwm.h` 的宏。给"provider 在模块里"的场景用 |
| `pwm_add_table(table, num)` | 注册一批查表项 | 位于 `core.c`。⚠️ **声明在头文件里但没有 `EXPORT_SYMBOL`** |
| `pwm_remove_table(table, num)` | 注销 | 同上警告 |

`struct pwm_lookup` 有 8 个字段（`include/linux/pwm.h:555-564`）：
`list` `provider` `index` `dev_id` `con_id` `period` `polarity` `module`。

**匹配算法**是模糊匹配，优先级三档（`core.c:900` 起的 `pwm_get()`）：

| 匹配情况 | 得分 | 含义 |
|---|---|---|
| `dev_id` 与 `con_id` 都对上 | 最高 | 精确命中，立刻采用 |
| 只有 `dev_id` 对上（表项 `con_id` 为 NULL） | 中 | 该设备的默认 PWM |
| 只有 `con_id` 对上（表项 `dev_id` 为 NULL） | 低 | 全局命名的 PWM |

⚠️ `core.c:933` 附近有一条坦白的注释，说这套打分算法是
"shamelessly taken from the clock framework"——和 clk 的 `clkdev` 查表是同源设计。
读过 clk 那套的人可以直接迁移直觉。

⚠️ **`pwm_add_table` 不导出** ⇒ 静态查表只能由**内建代码**（`arch/` 下的板级文件、
`drivers/mfd/` 里内建的 cell 注册代码）使用，模块里调不了。arm64 上这条路几乎用不到
（arm64 平台一律走 DT），登记在这里主要是为了读懂 `pwm_get()` 的完整逻辑。

---

## §6 sysfs ABI：pwm 唯一的用户态面

`CONFIG_PWM_SYSFS=y` 时（默认就是 y），每个注册成功的 chip 会在
`/sys/class/pwm/pwmchip<base>/` 出现。

### §6.1 chip 级属性（3 个）

| 属性 | 权限 | 说明 |
|---|---|---|
| `npwm` | r | 本 chip 有几条通道 |
| `export` | **w** | 写入一个 hwpwm 序号，创建对应的 `pwmX` 子目录 |
| `unexport` | **w** | 写入一个 hwpwm 序号，删掉对应的 `pwmX` 子目录 |

### §6.2 通道级属性（**5 个**）

export 之后出现 `pwmchip<base>/pwm<hwpwm>/`，里面有：

| 属性 | 权限 | 单位/取值 | 说明 |
|---|---|---|---|
| `period` | rw | 纳秒 | 周期 |
| `duty_cycle` | rw | 纳秒 | 高电平时长（**不是百分比**） |
| `enable` | rw | 0 / 1 | 输出开关 |
| `polarity` | rw | `normal` / `inversed` | ⚠️ 字符串，且拼写是 **`inversed`** |
| `capture` | **r** | `"<period> <duty>"` | ⚠️ **读它会真的触发一次硬件捕获** |

⚠️ **`capture` 这个属性在官方文档里根本没写。** `pwm.rst:86-129` 那节只列了
4 个通道属性（`period`/`duty_cycle`/`polarity`/`enable`），而 `sysfs.c:224-231`
的 `pwm_attrs[]` 里明明有 5 个。文档漏了一个 ABI。

⚠️ `capture` 是"读操作有副作用"的少见例子：`cat capture` 会调驱动的 `.capture`
回调并**阻塞等待，超时 1 秒**。驱动没实现时读它得到 `-ENOSYS`（用户态看到
"Function not implemented"）。用 `watch cat capture` 之类的命令会持续触发硬件动作。

### §6.3 sysfs 层的三个额外事实

1. **目录名用的是 `base` 不是 chip 序号**（`sysfs.c:514` 的
   `device_create(..., "pwmchip%d", chip->base)`），而通道目录名用的是
   **`hwpwm`**（`sysfs.c:263`）。所以 `pwmchip4/pwm0` 里的 4 是全局编号、0 是本地编号，
   两个数字口径不同。
2. **sysfs 建失败不影响注册。** `pwmchip_sysfs_export()` 里 `device_create` 失败只打一条
   `dev_warn`：`"device_create failed for pwm_chip sysfs export\n"`（`sysfs.c:516-517`），
   `pwmchip_add()` 照样返回 0。⇒ "驱动注册成功了但 `/sys/class/pwm/` 下没东西"是可能的。
3. **只有 export 过的通道参与 suspend/resume。** `pwm_class_suspend()`（`sysfs.c:443`）
   遍历的是 export 列表，挂起时把这些通道 disable、恢复时再 apply 回去，
   中途失败会回滚。⚠️ 这和官方文档 `pwm.rst:162-163` 那句
   "Drivers should not implement any power management" 是配套的：
   电源管理由 sysfs 层做，**但只覆盖 export 过的通道**；
   内核内部消费者持有的通道，PM 责任全在消费者自己（`pwm.rst:82-84`
   明说 "All consumers should really be reconfiguring the PWM upon resume"）。

⚠️ 每次 export/unexport 还会发 uevent，`"EXPORT=pwm%u"`（`sysfs.c:272`）/
`"UNEXPORT=pwm%u"`（`sysfs.c:297`）。udev 规则可以据此做自动配置。

### §6.4 用户态最短使用序列

```sh
cd /sys/class/pwm/pwmchip0
cat npwm                      # 看有几条
echo 0 > export               # 导出第 0 条
cd pwm0
echo 1000000  > period        # 1 ms = 1 kHz
echo 500000   > duty_cycle    # 50%
echo normal   > polarity
echo 1        > enable
```

⚠️ 顺序有硬约束：**`duty_cycle` 不能大于 `period`**（核心在
`pwm_apply_state()` 里直接判 `-EINVAL`）。把频率调高时要**先写 `duty_cycle` 再写
`period`**，否则中途会出现 `duty > period` 的瞬间状态而被拒。
这是 sysfs 用法里最常见的 `write error: Invalid argument`。

---

## §7 观测面：两个 tracepoint 加一个 debugfs 文件

pwm 的观测面比同规模的框架型子系统**更全**，这一点和直觉相反，值得单独记一笔。
完整用法在 07 篇，这里只登记接口。

### §7.1 tracepoint（2 个）

| 事件 | 触发点 | 字段 |
|---|---|---|
| `pwm:pwm_get` | 每次成功取到通道（3 处触发点） | `pwm` 指针 + `period` + `duty_cycle` + `polarity` + `enabled` |
| `pwm:pwm_apply` | 每次真正调 `.apply` 之前 | 同上 5 个字段 |

两个事件同出一个 `DECLARE_EVENT_CLASS(pwm)`（`include/trace/events/pwm.h:11`），
所以字段完全一样。`TP_printk` 格式是
`"%p: period=%llu duty_cycle=%llu polarity=%d enabled=%d"`（`:33-35`）。

⚠️ **`usage_power` 不在 tracepoint 里**——`pwm_state` 有 5 个字段，trace 只带 4 个
加一个指针。所以只改 `usage_power` 的 apply 在 trace 里看不出任何区别。

⚠️ `trace_pwm_apply` 在**全等短路之后**，所以 §2.6 那条短路生效时
tracepoint 也不会打。"trace 里没有 apply 事件"不等于"没调 `pwm_apply_state()`"。

### §7.2 debugfs（1 个文件）

| 路径 | 权限 | 内容 |
|---|---|---|
| `/sys/kernel/debug/pwm` | 0444 | **全系统所有 chip 与通道的一览**，一次性 dump |

它是**一个全局文件，不是一个目录**（`core.c:1182` 的
`debugfs_create_file("pwm", ..., NULL, NULL, &pwm_debugfs_fops)`，父目录传 NULL）。
遍历 `pwm_chips` 链表逐条打印，每 chip 一行头
（`"%s%s/%s, %d PWM device%s\n"`，`core.c:1161`）加每通道一行
（`" pwm-%-3d (%-20.20s):"`，`core.c:1117`）。

⚠️ 这是**排查 pwm 的第一条命令**，比读 sysfs 快得多：它一次给出全部 chip 的
base/npwm、每条通道的 label、当前 period/duty/polarity/enabled。
需要 `CONFIG_DEBUG_FS`。

---

## §8 错误码语义

### §8.1 核心层错误码普查

对 `core.c` + `sysfs.c` 统计 `return -E*` / `= -E*`：

| 错误码 | 次数 | 典型含义 | 消费者该怎么办 |
|---|---|---|---|
| `-EINVAL` | **15** | 参数不合法：`period == 0`、`duty > period`、`npwm == 0`、`.apply` 为空、通道未 request | 改参数。**不要重试** |
| `-ENODEV` | 7 | 找不到 provider（DT 里的 phandle 指向的节点没有注册成 chip，或查表没命中） | 检查 DT / 查表 |
| `-EPROBE_DEFER` | **4** | provider 还没注册上 | ⚠️ **必须原样往上返** |
| `-ENOMEM` | 2 | `kcalloc` 通道数组 / `kzalloc` export 结构失败 | — |
| `-EBUSY` | 2 | 通道已被别人 request，或 sysfs 里已 export 过 | 换通道 / 先 unexport |
| `-EPROTO` | 1 | DT 的 `pwms` 里 cell 数与 provider 声明的 `#pwm-cells` 不一致 | 改 DT |
| `-ENOSYS` | 1 | **只有一处**：`pwm_capture()` 而驱动没实现 `.capture` | 该硬件不支持捕获 |
| `-ENOSPC` | 1 | 全局 1024 个通道编号用完了 | 极罕见 |
| `-ENOENT` | 1 | `pwm-names` 里找不到请求的 `con_id` | 改 DT 的 `pwm-names` |

### §8.2 三个需要单独说的

**`-EPROBE_DEFER`（4 处）** 是本子系统最要紧的错误码。PWM 控制器往往挂在 I2C/SPI
扩展芯片上，注册时间晚于背光、马达这类消费者。消费者的 probe 必须写成：

```c
pwm = devm_pwm_get(dev, NULL);
if (IS_ERR(pwm))
        return dev_err_probe(dev, PTR_ERR(pwm), "failed to get pwm\n");
```

⚠️ 用 `dev_err_probe()` 而不是 `dev_err()` + `return ret`——前者对
`-EPROBE_DEFER` 只打 debug 级日志，不会在 dmesg 里刷一屏假错误。

**`-ENOSYS`** 在内核里是个"不该出现"的码（历史上只给未实现的系统调用用），
这里被 `pwm_capture()` 借用了。⇒ 看到 `Function not implemented` 不要往
"系统调用不存在"方向想，就是这个硬件不支持捕获。

**`-EPROTO`** 是 DT cell 数不匹配的专用码。它出现时说明 provider 的
`#pwm-cells` 和消费者 `pwms` 里写的 cell 个数对不上——**改 DT，不是改驱动**。

### §8.3 `CONFIG_PWM=n` 时桩函数的错误码不一致

`include/linux/pwm.h` 的 `#else` 段（`:412-520`）给出全套 `static inline` 桩，
**但返回值不成体系**：

| 桩函数 | 返回 | 数值 |
|---|---|---|
| `pwm_apply_state` · `pwm_adjust_config` | **`-ENOTSUPP`** | **524** |
| `pwm_config` · `pwm_enable` · `pwm_capture` · `pwm_set_chip_data` | `-EINVAL` | 22 |
| `pwmchip_add` · **`pwmchip_remove`** · `devm_pwmchip_add` | `-EINVAL` | 22 |
| `pwm_get` · `devm_pwm_get` · `devm_fwnode_pwm_get` · `pwm_request` · `pwm_request_from_chip` | `ERR_PTR(-ENODEV)` | 19 |
| `pwm_free` · `pwm_put` · `pwm_disable` | `void`（什么都不做，只 `might_sleep()`） | — |
| `pwm_get_chip_data` | `NULL` | — |

两处真问题：

1. ⚠️ **`-ENOTSUPP`（524）和 `-ENOIOCTLCMD` 一样是内核内部码，不允许传给用户态。**
   `CONFIG_PWM=n` 的内核上，一个消费者若把 `pwm_apply_state()` 的返回值原样
   往上传到 sysfs/ioctl 层，用户态会看到裸的 524。正确的对外码应该是
   `-EOPNOTSUPP`（95）。这是 `pwm.h` 里一处真实的粗糙处。
2. ⚠️ **`pwmchip_remove()` 的桩返回 `int`，真身返回 `void`。**
   `CONFIG_PWM=y` 时原型是 `void pwmchip_remove(struct pwm_chip *chip)`（`include/linux/pwm.h:392`），
   `CONFIG_PWM=n` 时是 `static inline int pwmchip_remove(...)` 返回 `-EINVAL`（`:476-479`）。
   ⇒ 写 `ret = pwmchip_remove(chip);` 的代码在 `CONFIG_PWM=n` 下编译通过、
   在 `CONFIG_PWM=y` 下编译失败。**两个配置的 API 签名不同**，
   这类不一致只有 allmodconfig / allnoconfig 双向编译才能抓到。

⇒ 实践建议：**依赖 pwm 的驱动应在 Kconfig 里 `depends on PWM`，不要靠桩函数**。
桩函数存在的意义是让"PWM 可选"的驱动能编译，不是让它们能正常工作。

---

## §9 近邻符号区分

本子系统的命名有几组极易混淆的近邻，单独列表。

### §9.1 `pwm_free` 与 `pwm_put`：同一件事

```c
void pwm_free(struct pwm_device *pwm)   /* core.c:434 */
{
        pwm_put(pwm);
}
EXPORT_SYMBOL_GPL(pwm_free);            /* core.c:438 */
```

⚠️ `pwm_free()` 就是 `pwm_put()` 的一行别名。历史上 `pwm_request`/`pwm_free` 是一对、
`pwm_get`/`pwm_put` 是另一对，现在底下合流了。
⇒ **`pwm_request()` 拿的可以用 `pwm_put()` 放，反过来也行**，不会出错。
新代码统一用 `pwm_get`/`pwm_put`。

### §9.2 `pwm->pwm` 与 `pwm->hwpwm`：两个编号

| 字段 | 口径 | 谁用 |
|---|---|---|
| `pwm->pwm` | **全局**编号 = `chip->base + hwpwm`，全系统唯一，上限 1024 | 核心（radix tree 的 key）、`pwm_request(id)` 的入参 |
| `pwm->hwpwm` | **本 chip 内**的 0 起序号 | ⚠️ **控制器驱动的寄存器索引只能用这个**；sysfs 的 `pwmX` 目录名 |

⚠️ 单 chip 系统上 `base == 0`，两者相等 ⇒ 用错也测不出来。
第二个控制器一注册立刻错位。这是 pwm 驱动最经典的 bug。

判据：**看到 `pwm->pwm` 出现在 `.apply` / `.request` / 寄存器计算里，就是 bug。**

### §9.3 `pwm_state` / `pwm_args` / `pwm_capture`：三个"配置"结构

| 结构 | 字段数 | 语义 | 谁写 |
|---|---|---|---|
| `struct pwm_state` | **5** | **请求值**：`period` `duty_cycle` `polarity` `enabled` `usage_power` | 消费者写，核心存副本 |
| `struct pwm_args` | **2** | **板级参考值**：`period` `polarity`（来自 DT 或查表） | 核心在 `pwm_get` 时填，消费者只读 |
| `struct pwm_capture` | **2** | **测量结果**：`period` `duty_cycle` | 驱动的 `.capture` 填 |

⚠️ 三者都有 `period` 字段但含义完全不同：`pwm_args.period` 是"DT 里建议的周期"，
`pwm_state.period` 是"我这次要的周期"，`pwm_capture.period` 是"我量到的周期"。
读代码时看清是哪个结构，不要只看字段名。

### §9.4 `pwm_config()` 与 `pwm_apply_state()`：官方劝退的一族

`Documentation/driver-api/pwm.rst:62-66` 说得很直接：旧的
`pwm_config()`/`pwm_enable()`/`pwm_disable()` 这批包装是"read-modify-write"，
**多个字段要分几次改时会在中间产生非法或不期望的中间状态**。

⚠️ 一个具体的坑：从 "1 kHz / 90%" 改到 "10 kHz / 10%"，用 `pwm_config()`
只能一次传两个值（duty 和 period 同时给），倒还好；但用
`pwm_set_period()` + `pwm_set_duty_cycle()` 分两步再 apply 就必然经过
`duty > period` 的中间态。⇒ **构造完整的 `pwm_state` 一次下发**是唯一安全写法。

### §9.5 `pwm_capture()` 的锁语义与其他 API 完全不同

| API | 加什么锁 |
|---|---|
| `pwm_apply_state()` | **完全不加锁**——并发调用同一条通道是调用者的责任 |
| `pwm_get()` / `pwm_put()` / `pwmchip_add()` | 全局 `pwm_lock` 互斥 |
| **`pwm_capture()`** | ⚠️ **持全局 `pwm_lock` 跨越整个驱动回调**（`core.c:610-612`） |

⇒ 一次 `pwm_capture()` 最长阻塞 `timeout`（sysfs 路径是 1 秒），
**这段时间里全系统所有 chip 的 `pwm_get`/`pwm_put`/注册/注销全部被挡住**。
在一个 chip 上 `watch cat capture` 能把另一个 chip 的 probe 拖慢。

这条不对称正是官方文档 Locking 一节（`pwm.rst:165-172`）承认的那笔债的一部分：
核心**不为状态变更加锁**，却为捕获加了一把最粗的全局锁。设计评价在 03 篇 Part C。

---

## §10 本篇的边界

**本篇覆盖**：`include/linux/pwm.h` 全部对外接口（17 个导出 + 约 15 个内联 + 全套桩函数）、
`pwm_ops` 的 6 个回调契约、`/sys/class/pwm/` 的 8 个属性、2 个 tracepoint、
1 个 debugfs 文件、9 个错误码的语义。

**本篇不覆盖**：
- 每个接口的**内部实现**与调用链 → 03 篇
- 结构体字段的**完整清单与相互关系** → 02 篇
- DT 属性（`pwms` / `#pwm-cells` / `pwm-names`）的写法与解析 → 06 篇
- `CONFIG_PWM_DEBUG` 那 9 条检查的逐条含义 → 07 篇
- 62 个控制器驱动各自的能力差异 → 04 篇只做统计，不逐个展开
- **ACPI 路径**（`acpi_pwm_get()`）：按 arm64-only 裁掉，登记在 99 篇

---

## §11 阅读下一篇

| 本篇讲了 | 想深入 | 去哪篇 |
|---|---|---|
| 接口清单与契约 | 8 个结构的字段与关系 | `02-datastructures.md` |
| `pwm_apply_state` 的对外行为 | 它内部的六步与短路 | `03-internals.md` |
| `pwm_ops` 6 个回调 | 62 个驱动实际填了哪些 | `04-extensions.md` |
| §2.9 / §4.2 的骨架 | 可编译的完整例子与角色分工 | `05-usage.md` |
| DT 相关的错误码 | 属性写法与解析全链 | `06-devicetree.md` |
| 两个 tracepoint 与 debugfs | 完整排查流程 | `07-debug.md` |
| 为什么是"一次下发整个 state" | 概念的费曼式讲解 | `08-feynman.md` |
| 上面任何一条的取证命令 | 逐条证据 | `99-evidence.md` |
