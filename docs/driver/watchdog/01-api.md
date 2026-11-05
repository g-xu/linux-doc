---
title: API 参考
---

# watchdog 01：代码地图与对外 API

> **基线**：`linux-6.1.158`（`f6e38ae624cf`），**arm64-only**，
> **`drivers/watchdog/`、`include/linux/watchdog.h`、`include/uapi/linux/watchdog.h` 均未被本树补丁修改**（99 §1）。

## §0 代码地图

### §0.1 这个子系统最特殊的一点：它有用户态 ABI

⚠️ **watchdog 与本项目做过的其他框架型子系统（regmap / phy / clk …）有一个根本差别**：

| | **watchdog** | regmap / phy |
|---|---|---|
| **有 `include/uapi/` 头文件吗** | **有**：`include/uapi/linux/watchdog.h`（58 行） | **都没有** |
| **有字符设备吗** | **有，而且是两套**（§0.4） | **都没有** |
| **主要使用者是谁** | ⚠️ **用户态守护进程**（`watchdogd` 之类） | **其他内核驱动** |
| **"消费者"在哪** | **内核外** | 内核内 |

⇒ **所以 watchdog 的"对外 API"有两套，必须分开讲**：
**① 给驱动的内核 API（9 个导出符号 + 一堆 `static inline`）；
② 给用户态的 ioctl ABI（11 个 ioctl + 13 个 `WDIOF_*` 标志）。**
**本篇 §1–§6 讲第一套，§7 讲第二套。**

### §0.2 文件在哪

| 角色 | 文件 | 行数 | 职责 |
|---|---|---|---|
| **字符设备与用户态接口** | **`drivers/watchdog/watchdog_dev.c`** | **1306** | **最大的核心文件**：ioctl / read / write / open / release · 12 个 sysfs 属性 · 心跳工作队列 |
| **注册与生命周期** | **`drivers/watchdog/watchdog_core.c`** | **485** | **`watchdog_register_device` 及其延迟注册机制 · reboot/restart/pm 三个 notifier** |
| **pretimeout 治理器框架** | `drivers/watchdog/watchdog_pretimeout.c` | 216 | **governor 注册与选择**（§6） |
| **hrtimer 软 pretimeout** | `drivers/watchdog/watchdog_hrtimer_pretimeout.c` | **44** | **给硬件不支持 pretimeout 的看门狗补一个软的**（04 篇） |
| **核心私有头** | `drivers/watchdog/watchdog_core.h` | 81 | **`struct watchdog_core_data` 等，驱动看不到** |
| **内核 API 头** | **`include/linux/watchdog.h`** | **228** | **`watchdog_device` / `watchdog_ops` / 12 个 inline** |
| **用户态 ABI 头** | **`include/uapi/linux/watchdog.h`** | **58** | **11 个 ioctl + 13 个 `WDIOF_*` + 3 个 `WDIOS_*`**（§7） |
| **驱动 + 治理器** | **`drivers/watchdog/` 其余 ~170 个文件** | **约 57000 行** | **本篇只讲契约**（99 §2.4） |

⚠️ **规模的形状**：**`drivers/watchdog/` 共 180 个 `.c`/`.h`、59266 行，
核心 5 个文件占 2132 行（3.6%）。**
⇒ **读 watchdog 框架只需要读 `watchdog_dev.c` + `watchdog_core.c` + 两个头文件，共 2077 行。**

### §0.3 官方文档：本项目做过的子系统里最丰富的

**`Documentation/watchdog/` 共 10 个文件 / 1904 行。**

| 文件 | 行数 | 讲什么 | 本文档读了吗 |
|---|---|---|---|
| **`watchdog-kernel-api.rst`** | **350** | **给驱动作者的内核 API 指南** | **全文读过**，**抓到三处过时**（03 §C2） |
| **`watchdog-api.rst`** | **271** | **给用户态的 ABI 说明**（magic close / nowayout / ioctl 逐个） | **全文读过** |
| `watchdog-parameters.rst` | **740** | **逐驱动的模块参数字典** | ⚠️ **只查不读**（99 §1.3 已登记） |
| `convert_drivers_to_kernel_api.rst` | 219 | **老驱动迁移到 core 的指南** | **只读了标题结构**（99 §2.2） |
| `watchdog-pm.rst` | **22** | **一条规则：resume 时必须重置定时器** | **全文读过** |
| `hpwdt.rst` / `pcwd-watchdog.rst` / `mlx-wdt.rst` / `wdt.rst` | 77 / 71 / 66 / 63 | **各厂商驱动专属** | **未读**（99 §1.3 登记为刻意不覆盖） |
| `index.rst` | 25 | toctree | 读过 |

### §0.4 ⚠️ 两套字符设备并存（本子系统第一号易错点）

| | **旧接口 `/dev/watchdog`** | **新接口 `/dev/watchdogN`** |
|---|---|---|
| 注册方式 | **`misc_register(&watchdog_miscdev)`**（`drivers/watchdog/watchdog_dev.c:1040`） | **`alloc_chrdev_region(&watchdog_devt, 0, MAX_DOGS, "watchdog")`**（`:1224`） |
| 主设备号 | **10（misc）**，**次设备号 130** | **动态分配** |
| 有几个 | **只有一个** | **每个注册的看门狗一个** |
| **哪个看门狗对应它** | ⚠️ **只有 `id == 0` 那一个** | **`id` 就是 N** |

**官方文档 `watchdog-kernel-api.rst` 原话**：
> *"id: set by watchdog_register_device, id 0 is special. It has both a /dev/watchdog0 cdev
> (dynamic major, minor 0) as well as the old /dev/watchdog miscdev."*

⇒ **第一个注册的看门狗同时出现在 `/dev/watchdog` 和 `/dev/watchdog0`，指向同一个设备。**
⇒ **第二个及以后的只有 `/dev/watchdogN`。**
**详细的关系图和"为什么不会冲突"见 02 §1.0。**

### §0.5 9 个导出符号（不是 10，见口径）

| 组 | 符号 | 位置 | 导出方式 |
|---|---|---|---|
| **注册（3）** | **`watchdog_register_device`** | `drivers/watchdog/watchdog_core.c:371` | `_GPL` |
| | **`watchdog_unregister_device`** | `:406` | `_GPL` |
| | **`devm_watchdog_register_device`** | `:443` | `_GPL` |
| **初始化辅助（2）** | **`watchdog_init_timeout`** | `:156` | `_GPL` |
| | **`watchdog_set_restart_priority`** | `:238` | `_GPL` |
| **心跳（1）** | **`watchdog_set_last_hw_keepalive`** | `drivers/watchdog/watchdog_dev.c:1198` | `_GPL` |
| **pretimeout（3）** | **`watchdog_notify_pretimeout`** | `drivers/watchdog/watchdog_pretimeout.c:113` | `_GPL` |
| | ⚠️ **`watchdog_register_governor`** | `:150` | ⚠️ **裸 `EXPORT_SYMBOL`** |
| | ⚠️ **`watchdog_unregister_governor`** | `:175` | ⚠️ **裸 `EXPORT_SYMBOL`** |

⚠️⚠️ **计数口径（本篇最要紧的一条数字纪律）**：
**`grep -c 'EXPORT_SYMBOL' …` 会给出 10，多算了一个。**
**多出来的那一行是注释**：
```c
/* drivers/watchdog/watchdog_core.c:27 */
#include <linux/module.h>	/* For EXPORT_SYMBOL/module stuff/... */
```
⇒ **正确口径是 `grep -cE '^EXPORT_SYMBOL' …` ⇒ 9**（99 §6 表 3）。

⚠️ **两个 governor 注册函数不带 `_GPL`** ——**其余 7 个都带。**
⇒ **这意味着非 GPL 模块理论上可以注册 pretimeout governor**（本文档不评价这个选择的动机）。

### §0.6 12 个 `static inline` 辅助（不是导出符号，但同样是 API）

**全部在 `include/linux/watchdog.h`**：

| inline | 位置 | 做什么 |
|---|---|---|
| `watchdog_active(wdd)` | `:128` | **测 `WDOG_ACTIVE` 位** ——**"用户态视角的运行中"** |
| `watchdog_hw_running(wdd)` | `:137` | **测 `WDOG_HW_RUNNING` 位** ——**"硬件视角的运行中"**（两者不同，02 §1.0） |
| `watchdog_set_nowayout(wdd, nowayout)` | `:143` | 设 `WDOG_NO_WAY_OUT` |
| `watchdog_stop_on_reboot(wdd)` | `:150` | 设 `WDOG_STOP_ON_REBOOT` |
| `watchdog_stop_on_unregister(wdd)` | `:156` | 设 `WDOG_STOP_ON_UNREGISTER` |
| `watchdog_stop_ping_on_suspend(wdd)` | `:162` | 设 `WDOG_NO_PING_ON_SUSPEND` |
| `watchdog_timeout_invalid(wdd, t)` | `:168` | **范围校验**（上下界都查，§4.2） |
| `watchdog_pretimeout_invalid(wdd, t)` | `:187` | 同上 |
| `watchdog_set_drvdata(wdd, data)` | `:194` | 私有数据 |
| `watchdog_get_drvdata(wdd)` | `:199` | 同上 |
| `watchdog_notify_pretimeout(wdd)`（空桩版） | `:208` | **`CONFIG_WATCHDOG_PRETIMEOUT_GOV=n` 时的版本**（真实现是导出符号） |

⚠️ **`watchdog_dev_suspend` / `watchdog_dev_resume`（`include/linux/watchdog.h:220-221`）
是声明但不导出** ——**它们是 `watchdog_dev.c` 提供给 `watchdog_core.c` 的内部接口**
（由 `pm_nb` 调，03 篇）⇒ **驱动不该调它们。**

### §0.7 Kconfig：一整套行为开关（与 phy/regmap 完全不同）

| 符号 | 类型 | 影响什么 |
|---|---|---|
| `WATCHDOG` | **`menuconfig bool`** | **整个子系统** |
| `WATCHDOG_CORE` | **`tristate`** | **框架本身**（可编成模块） |
| ⚠️ **`WATCHDOG_NOWAYOUT`** | **`bool`** | **关掉 `/dev/watchdog` 后能不能停表** ——**默认 N（能停）** |
| ⚠️ **`WATCHDOG_HANDLE_BOOT_ENABLED`** | **`bool`，默认 y** | **接管"启动前就已经在跑"的看门狗，在用户态接手前由内核喂狗** |
| ⚠️ **`WATCHDOG_OPEN_TIMEOUT`** | **`int`，默认 0（无限）** | **内核最多替用户态喂多久** ——**可用启动参数 `watchdog.open_timeout` 覆盖** |
| `WATCHDOG_SYSFS` | `bool` | **12 个 sysfs 属性的开关**（07 篇） |
| `WATCHDOG_HRTIMER_PRETIMEOUT` | `bool` | **软 pretimeout**（04 篇） |
| `WATCHDOG_PRETIMEOUT_GOV` | `bool` | **governor 框架** |
| `WATCHDOG_PRETIMEOUT_GOV_NOOP` / `_PANIC` | **`tristate`，默认跟随 `WATCHDOG_CORE`** | **两个内置 governor**（§6） |
| `WATCHDOG_PRETIMEOUT_DEFAULT_GOV_NOOP` / `_PANIC` | **`choice`，默认 `_PANIC`** | **默认用哪个 governor** |

⚠️ **`WATCHDOG_NOWAYOUT` 是编译期开关，但它有运行期对应物**：
**多数驱动还提供 `nowayout` 模块参数**，**而且有一个 sysfs 属性 `nowayout` 是可写的**
（`drivers/watchdog/watchdog_dev.c:469`，07 篇）。

---

## §1 先分清：三方角色 + 两层可插拔

### §1.1 三方角色

| 角色 | 是什么 | 代码上体现为 |
|---|---|---|
| **看门狗驱动** | **驱动一颗看门狗硬件** | **填 `struct watchdog_device` + `struct watchdog_ops`，调 `watchdog_register_device()`** |
| **用户态守护进程** | **真正的"消费者"** | **打开 `/dev/watchdog`，定期 `write()` 或 `ioctl(WDIOC_KEEPALIVE)`** |
| **pretimeout governor** | **"快超时了"这个事件的处理策略** | **填 `struct watchdog_governor`，调 `watchdog_register_governor()`** |

⚠️ **还有一个隐形的第四方：内核自己。**
**`WATCHDOG_HANDLE_BOOT_ENABLED` 打开时，在用户态接手之前由内核的工作队列喂狗**（03 篇）。
⇒ **这是 watchdog 最特殊的一点：框架自己会替用户态干活，而且有时限（`WATCHDOG_OPEN_TIMEOUT`）。**

### §1.2 两层可插拔

| 层 | 抽象 | 谁实现 | 实现数 | 开放程度 |
|---|---|---|---|---|
| **层 1：看门狗操作** | **`struct watchdog_ops`**（**10 个字段**，`include/linux/watchdog.h:43-56`） | **每个看门狗驱动** | **约 170 个驱动** | **完全开放** |
| **层 2：pretimeout 治理** | **`struct watchdog_governor`** | **governor 模块** | **2 个内置**（noop / panic） | **开放但用得少**（04 篇） |

**对照**：**regmap 三层 · phy 一层 · watchdog 两层。**

---

## §2 `struct watchdog_ops`：10 个字段，只有 1 个必需

**位置：`include/linux/watchdog.h:43-56`。**

| 字段 | 签名（形参名） | 必需？ | 说明 | 备注 |
|---|---|---|---|---|
| `owner` | **`struct module *`** | **实际上必需** | **看门狗活动时锁住模块** | **官方文档原话：*"This to avoid a system crash when you unload the module and /dev/watchdog is still open"*** |
| `start` | `(wdd)` | **唯一的强制项**（源码注释就写着 `/* mandatory operations */`，`:45`） | **启动看门狗** | 返回 0 或负错误码 |
| `stop` | `(wdd)` | **可选** | **停止看门狗** | ⚠️ **不实现的后果很大**：**核心会置 `WDOG_HW_RUNNING` 并在设备关闭后继续喂狗**；**而且必须设 `max_hw_heartbeat_ms`**（§3.3） |
| `ping` | `(wdd)` | 可选 | **喂狗** | **不实现时核心用 `start` 代替**（官方文档明说） |
| `status` | `(wdd)` | 可选 | **返回 `WDIOF_*` 位** | **`WDIOF_MAGICCLOSE` 和 `WDIOF_KEEPALIVEPING` 由核心报，驱动不用管**；**不实现则核心报 `bootstatus`** |
| `set_timeout` | `(wdd, timeout)` | 可选 | **改超时** | **官方文档规定返回值：0 / `-EINVAL`（越界）/ `-EIO`（写不进去）**；⚠️ **成功时驱动应把实际值写回 `wdd->timeout`** |
| `set_pretimeout` | `(wdd, pretimeout)` | 可选 | **改 pretimeout** | **0 表示关闭 pretimeout 通知** |
| `get_timeleft` | `(wdd)` | 可选 | **返回还剩多久** | **`unsigned int` 返回，没有错误码** |
| ⚠️`restart` | **`(wdd, action, data)`** | 可选 | **用看门狗重启机器** | ⚠️ **官方文档写成一个参数，代码是三个**（`include/linux/watchdog.h:54`，03 §C2 第 1 条） |
| `ioctl` | `(wdd, cmd, arg)` | 可选 | **驱动私有 ioctl** | **在核心自己处理之前调用**；**不支持的命令必须返回 `-ENOIOCTLCMD`** |

⚠️ **"只有 `start` 必需"是这个框架最宽松的地方**，**也是很多"看起来能用但行为奇怪"的根源**：
**不实现 `stop` ⇒ 关掉 `/dev/watchdog` 也停不下来（§3.3）；
不实现 `ping` ⇒ 每次喂狗都走 `start`；
不实现 `set_timeout` 但设了 `WDIOF_SETTIMEOUT` ⇒ 核心只改软字段，硬件不变（§4.3）。**

---

## §3 `struct watchdog_device`：20 个字段

**位置：`include/linux/watchdog.h:94-122`。按用途分五组。**

### §3.1 组 1：身份（4 个）

| 字段 | 谁填 | 说明 |
|---|---|---|
| `id` | **`watchdog_register_device()` 自动分配** | **`id == 0` 特殊：同时有 `/dev/watchdog` 和 `/dev/watchdog0`**（§0.4） |
| `parent` | **驱动，注册前填** | 父设备 |
| `groups` | 驱动 | **驱动想额外加的 sysfs 属性组** |
| `info` | **驱动，指向 `const struct watchdog_info`** | **这是 uapi 结构！`WDIOC_GETSUPPORT` 直接返回它**（§7.2） |

### §3.2 组 2：能力与回调（2 个）

| 字段 | 说明 |
|---|---|
| `ops` | **层 1**（§2） |
| `gov` | **层 2**：**指向本设备的 pretimeout governor，可为 NULL**（§6） |

### §3.3 组 3：时间参数（6 个，本篇最要紧的一组）

| 字段 | 单位 | 谁填 | 说明 |
|---|---|---|---|
| `timeout` | **秒** | **驱动填默认值，用户态可改** | **"用户态多久不喂狗就重启"** |
| `pretimeout` | 秒 | 同上 | **超时前多久发通知**；**0 = 关闭** |
| `min_timeout` / `max_timeout` | 秒 | 驱动 | **用户态可配范围**；⚠️ **`max_hw_heartbeat_ms` 非 0 时 `max_timeout` 不生效**（官方文档明说） |
| ⚠️`min_hw_heartbeat_ms` | **毫秒** | 驱动 | **硬件要求的最小喂狗间隔**；**通常 0** |
| ⚠️`max_hw_heartbeat_ms` | **毫秒** | 驱动 | **硬件能撑的最长间隔** ——**这是整个框架里最关键的一个字段**（见下） |

⚠️ **`max_hw_heartbeat_ms` 为什么关键**（官方文档原话，逐字）：
> *"If set, the infrastructure will send heartbeats to the watchdog driver if 'timeout' is larger
> than max_hw_heartbeat_ms, unless WDOG_ACTIVE is set and userspace failed to send a heartbeat
> for at least 'timeout' seconds. max_hw_heartbeat_ms must be set if a driver does not implement
> the stop function."*

⇒ **它让"硬件超时"和"用户态超时"解耦**：
**硬件只能撑 2 秒，但用户态想要 60 秒的超时 ⇒ 核心自己每 <2 秒喂一次，
只有用户态真的 60 秒没动静才停止喂狗、让硬件复位。**
⇒ **这就是 watchdog 框架里那个"内核帮你喂狗"的工作队列存在的理由**（03 篇）。

⚠️ **单位不统一是本组的陷阱**：**前四个是秒，后两个是毫秒。**

### §3.4 组 4：内部使用（5 个，驱动别碰）

| 字段 | 说明 |
|---|---|
| `reboot_nb` | **reboot notifier** ——**`watchdog_stop_on_reboot()` 才会注册** |
| `restart_nb` | **restart handler** ——**驱动实现了 `ops->restart` 就注册**；优先级用 `watchdog_set_restart_priority()` 改 |
| ⚠️`pm_nb` | **PM notifier**（`include/linux/watchdog.h:110`） | ⚠️ **官方文档的字段列表里没有它**（03 §C2 第 2 条） |
| `wd_data` | **指向核心私有的 `struct watchdog_core_data`** ——**驱动不可见**（02 篇） |
| `deferred` | **延迟注册链表节点**（03 篇） |

### §3.5 组 5：状态位（`status` 一个字段 + 6 个位）

**位定义在 `include/linux/watchdog.h:115-120`**：

| 位 | 值 | 谁设 | 含义 |
|---|---|---|---|
| `WDOG_ACTIVE` | 0 | **核心** | **"用户态视角在跑"** ——**用户态被期望持续喂狗** |
| `WDOG_NO_WAY_OUT` | 1 | **驱动/配置** | **停不下来** |
| `WDOG_STOP_ON_REBOOT` | 2 | **`watchdog_stop_on_reboot()`** | 重启时停 |
| `WDOG_HW_RUNNING` | 3 | **驱动** | **"硬件视角在跑"** ——**核心会在 `WDOG_ACTIVE` 未置时也喂狗** |
| `WDOG_STOP_ON_UNREGISTER` | 4 | **`watchdog_stop_on_unregister()`** | **注销时停**（`nowayout` 会压制它） |
| `WDOG_NO_PING_ON_SUSPEND` | 5 | **`watchdog_stop_ping_on_suspend()`** | suspend 时停止喂狗工作 |

⚠️⚠️ **`WDOG_ACTIVE` 与 `WDOG_HW_RUNNING` 是本子系统最容易混的一组**（02 §1.0 会单独破歧义）：
**前者是"用户态打开了设备"，后者是"硬件计时器在转"。**
**四种组合都真实存在。**

⚠️ **官方文档只讲了 3 个位**（`WDOG_ACTIVE` / `WDOG_NO_WAY_OUT` / `WDOG_HW_RUNNING`），
**漏了 `WDOG_STOP_ON_REBOOT` / `WDOG_STOP_ON_UNREGISTER` / `WDOG_NO_PING_ON_SUSPEND`**
（03 §C2 第 3 条）。

---

## §4 注册与初始化 API

### §4.1 三个注册函数

| API | 位置（导出行） | 说明 |
|---|---|---|
| `watchdog_register_device(wdd)` | `drivers/watchdog/watchdog_core.c:371` | **返回 0 或负错误码**（不是 ERR_PTR） |
| `watchdog_unregister_device(wdd)` | `:406` | **`void` 返回** |
| `devm_watchdog_register_device(dev, wdd)` | `:443` | **推荐用它** |

⚠️ **框架有"延迟注册"机制**（官方文档原话）：
> *"The watchdog subsystem includes an registration deferral mechanism, which allows you to
> register an watchdog as early as you wish during the boot process."*

⇒ **`struct watchdog_device.deferred` 这个链表节点就是为它准备的**（§3.4，03 篇讲流程）。

### §4.2 超时初始化：`watchdog_init_timeout`

| API | 位置 | 说明 |
|---|---|---|
| `watchdog_init_timeout(wdd, timeout_parm, dev)` | `drivers/watchdog/watchdog_core.c:156` 处导出 | **优先用模块参数；参数无效则读 DT 的 `timeout-sec` 属性** |

**官方文档给的最佳实践**（原话）：
> *"Best practice is to set the default timeout value as timeout value in the watchdog_device and
> then use this function to set the user 'preferred' timeout value."*

⇒ **DT 属性 `timeout-sec` 就是这里被消费的**（06 篇）。

**范围校验由两个 inline 提供**：
**`watchdog_timeout_invalid()`（`include/linux/watchdog.h:168`）·
`watchdog_pretimeout_invalid()`（`:187`）** ——**驱动可以在自己的 `set_timeout` 里先调它们。**

### §4.3 ⚠️ 四个"不实现回调也能工作"的情形（官方文档明确规定）

| 情形 | 核心怎么处理 |
|---|---|
| **不实现 `ping`** | **用 `start` 代替** |
| **不实现 `status`** | **报 `wdd->bootstatus`** |
| ⚠️ **不实现 `set_timeout` 但设了 `WDIOF_SETTIMEOUT`** | **核心只更新 `wdd->timeout` 这个软字段** ⇒ ⚠️ **硬件超时不变** |
| ⚠️ **不实现 `set_pretimeout` 但设了 `WDIOF_PRETIMEOUT`** | **同样只更新软字段** |
| ⚠️ **不实现 `stop`** | **置 `WDOG_HW_RUNNING`，关闭设备后核心继续喂狗**；**必须设 `max_hw_heartbeat_ms`** |

⚠️ **第 3、4 条是设计上的"善意"，但它让"设了标志"和"真能改"不可区分**（03 §C3 / 05 §7）。

### §4.4 六个行为开关（inline）

| API | 位置 | 设哪个位 |
|---|---|---|
| `watchdog_set_nowayout(wdd, bool)` | `include/linux/watchdog.h:143` | `WDOG_NO_WAY_OUT` |
| `watchdog_stop_on_reboot(wdd)` | `:150` | `WDOG_STOP_ON_REBOOT` |
| `watchdog_stop_on_unregister(wdd)` | `:156` | `WDOG_STOP_ON_UNREGISTER` |
| `watchdog_stop_ping_on_suspend(wdd)` | `:162` | `WDOG_NO_PING_ON_SUSPEND` |
| `watchdog_set_restart_priority(wdd, priority)` | `drivers/watchdog/watchdog_core.c:238` 处导出（不是 inline） | **改 restart handler 优先级** |
| `watchdog_set_last_hw_keepalive(wdd, last_ping_ms)` | `drivers/watchdog/watchdog_dev.c:1198` 处导出 | **告诉核心"上一次硬件喂狗是多久以前"** |

**`watchdog_set_restart_priority()` 的取值指南（官方文档原文）**：
**`0` = 最后手段 · `128` = 默认 · `255` = 最高，抢占其他所有 restart handler。**

⚠️ **`watchdog_set_last_hw_keepalive()` 有严格的调用时机要求**（官方文档原话）：
> *"This function must be called immediately after watchdog registration."*
⇒ **只在"probe 时看门狗已经在跑，且必须等 `min_hw_heartbeat_ms` 才能再喂"时需要。**

### §4.5 私有数据

| API | 位置 |
|---|---|
| `watchdog_set_drvdata(wdd, data)` | `include/linux/watchdog.h:194` |
| `watchdog_get_drvdata(wdd)` | `:199` |

⚠️ **官方文档明确要求"只能通过这两个函数访问 `driver_data`"** ——
**不要直接读写 `wdd->driver_data`。**

---

## §5 状态查询 API

| API | 位置 | 返回 |
|---|---|---|
| `watchdog_active(wdd)` | `include/linux/watchdog.h:128` | **`bool`，测 `WDOG_ACTIVE`** |
| `watchdog_hw_running(wdd)` | `:137` | **`bool`，测 `WDOG_HW_RUNNING`** |
| `watchdog_timeout_invalid(wdd, t)` | `:168` | **`bool`，`true` 表示越界** |
| `watchdog_pretimeout_invalid(wdd, t)` | `:187` | 同上 |

⚠️ **注意 `*_invalid()` 的语义是反的**：**返回 `true` 表示"不合法"。**
⇒ **`if (watchdog_timeout_invalid(wdd, t)) return -EINVAL;` 才是正确用法。**

---

## §6 pretimeout governor（层 2）

| API | 位置 | 说明 |
|---|---|---|
| `watchdog_notify_pretimeout(wdd)` | `drivers/watchdog/watchdog_pretimeout.c:113` 处导出 | **驱动在"快超时"中断里调它**；**官方文档明说可以在中断上下文调** |
| `watchdog_register_governor(gov)` | `:150`（⚠️ 裸 `EXPORT_SYMBOL`） | **注册一个 governor** |
| `watchdog_unregister_governor(gov)` | `:175`（⚠️ 裸 `EXPORT_SYMBOL`） | — |

⚠️ **`CONFIG_WATCHDOG_PRETIMEOUT_GOV=n` 时的行为**（官方文档原话）：
> *"If watchdog pretimeout governor framework is not enabled, watchdog_notify_pretimeout()
> prints a notification message to the kernel log buffer."*
**对应 `include/linux/watchdog.h:208` 的空桩版本。**

**两个内置 governor**（Kconfig）：
**`noop`（只打一条日志）· `panic`（直接 panic）** ——**默认是 `panic`**
（`WATCHDOG_PRETIMEOUT_DEFAULT_GOV_PANIC`）。
**详细机制见 04 篇。**

---

## §7 用户态 ABI（`include/uapi/linux/watchdog.h`，58 行）

### §7.1 11 个 ioctl

**全部以 `WATCHDOG_IOCTL_BASE = 'W'` 为基**：

| ioctl | 编号 | 方向 | 参数 | 做什么 |
|---|---|---|---|---|
| `WDIOC_GETSUPPORT` | 0 | `_IOR` | **`struct watchdog_info`** | **查能力**（§7.2） |
| `WDIOC_GETSTATUS` | 1 | `_IOR` | `int` | **当前状态（`WDIOF_*` 位）** |
| `WDIOC_GETBOOTSTATUS` | 2 | `_IOR` | `int` | **上次是不是看门狗复位的**（关键取证手段，07 篇） |
| `WDIOC_GETTEMP` | 3 | `_IOR` | `int` | **温度**（老 ISA 卡的功能） |
| `WDIOC_SETOPTIONS` | 4 | **`_IOR`（⚠️ 见 §7.4）** | `int` | **`WDIOS_*`：开表/停表/温度 panic** |
| `WDIOC_KEEPALIVE` | 5 | **`_IOR`** | `int` | **喂狗**；**官方文档：参数被忽略** |
| `WDIOC_SETTIMEOUT` | 6 | **`_IOWR`** | `int` | **设超时，并把实际值写回同一个变量** |
| `WDIOC_GETTIMEOUT` | 7 | `_IOR` | `int` | 读超时 |
| `WDIOC_SETPRETIMEOUT` | 8 | `_IOWR` | `int` | 设 pretimeout |
| `WDIOC_GETPRETIMEOUT` | 9 | `_IOR` | `int` | 读 pretimeout |
| `WDIOC_GETTIMELEFT` | 10 | `_IOR` | `int` | 还剩多久 |

### §7.2 `struct watchdog_info`：3 个字段的能力描述

```c
/* include/uapi/linux/watchdog.h */
struct watchdog_info {
	__u32 options;		/* Options the card/driver supports */
	__u32 firmware_version;	/* Firmware version of the card */
	__u8  identity[32];	/* Identity of the board */
};
```
⚠️ **`identity` 是 32 字节定长数组，不保证以 NUL 结尾** ——**用户态打印时要自己限长。**
**驱动填的是 `wdd->info`（`include/linux/watchdog.h:98`），核心直接把它交给用户态。**

### §7.3 13 个 `WDIOF_*` 能力/状态标志

| 标志 | 值 | 含义 |
|---|---|---|
| `WDIOF_OVERHEAT` | `0x0001` | **上次因过热复位** |
| `WDIOF_FANFAULT` | `0x0002` | 风扇故障 |
| `WDIOF_EXTERN1` / `_EXTERN2` | `0x0004` / `0x0008` | 外部继电器 |
| `WDIOF_POWERUNDER` | `0x0010` | 电压过低 |
| `WDIOF_CARDRESET` | `0x0020` | **"上次是看门狗复位的 CPU"** ——**排查意外重启的第一手证据** |
| `WDIOF_POWEROVER` | `0x0040` | 电压过高 |
| `WDIOF_SETTIMEOUT` | `0x0080` | **支持 `WDIOC_SETTIMEOUT`** |
| `WDIOF_MAGICCLOSE` | `0x0100` | **支持 magic close**（§7.5）——**由核心报，驱动不用填** |
| `WDIOF_PRETIMEOUT` | `0x0200` | 支持 pretimeout |
| `WDIOF_ALARMONLY` | `0x0400` | **只报警不复位** |
| `WDIOF_KEEPALIVEPING` | **`0x8000`** | **支持 `WDIOC_KEEPALIVE`** ——**由核心报** |
| `WDIOF_UNKNOWN` / `WDIOS_UNKNOWN` | **`-1`** | **错误标记，不是能力位** |

⚠️ **`WDIOF_KEEPALIVEPING` 的值跳到了 `0x8000`**，**不与前面连续** ——
**`0x0800` / `0x1000` / `0x2000` / `0x4000` 未使用。**

### §7.4 ⚠️ 三个 `WDIOS_*` 与一个 ioctl 方向的历史遗留

| 标志 | 值 | 含义 |
|---|---|---|
| `WDIOS_DISABLECARD` | `0x0001` | **停表** |
| `WDIOS_ENABLECARD` | `0x0002` | **开表** |
| `WDIOS_TEMPPANIC` | `0x0004` | 温度触发时 panic |

⚠️ **`WDIOC_SETOPTIONS` 被定义成 `_IOR`（读方向），但语义是"写"**
（`include/uapi/linux/watchdog.h`）——
**同样地 `WDIOC_KEEPALIVE` 也是 `_IOR`。**
⇒ **这是 ABI 的历史遗留，不能改（改了破坏用户态兼容性）。**
**官方 `watchdog-api.rst` 的开场就承认了这一点**（原话）：
> *"The Linux watchdog API is a rather ad-hoc construction and different drivers implement
> different, and sometimes incompatible, parts of it."*

### §7.5 两个只存在于用户态视角的机制

| 机制 | 是什么 | 依据 |
|---|---|---|
| **写任意数据 = 喂狗** | **`write()` 到 `/dev/watchdog` 等价于 `WDIOC_KEEPALIVE`** | `watchdog-api.rst` "The simplest API" 段 |
| **Magic Close** | **关闭设备前写一个字符 `'V'`，才允许停表**；**不写就当成"守护进程死了"，核心停止喂狗但不停表 ⇒ 稍后复位** | `watchdog-api.rst` "Magic Close feature" 段 |

⚠️ **`nowayout` 会压制 magic close**（官方文档原话）：
> *"The nowayout feature will overrule the magic close feature."*

⇒ **三层优先级**：**`nowayout` > magic close > 默认（关闭即停表）。**
**完整的状态机在 03 篇。**

---

## §8 错误码语义

### §8.1 普查（4 个核心 `.c`）

| 码 | 次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | **10** | **参数越界 / 必需回调缺失** |
| `-ENODEV` | **7** | **设备已注销** |
| `-EBUSY` | **7** | **设备已被打开**（看门狗是独占的） |
| `-EFAULT` | **5** | **`copy_to_user` / `copy_from_user` 失败**（ioctl 路径） |
| `-EOPNOTSUPP` | **5** | **对应回调没实现** |
| `-ENOMEM` | 4 | 分配失败 |
| `-ENOIOCTLCMD` | **2** | **驱动的 `ops->ioctl` 说"这个命令我不认"** ⇒ **核心接着自己处理** |
| `-ENOTTY` | 1 | **未知 ioctl 的最终返回** |
| `-EPERM` | 1 | — |
| `-EIO` / `-EAGAIN` / `-ERANGE` | **全 0** | ⚠️ **注意官方文档要求 `set_timeout` 返回 `-EIO`，那是驱动侧的约定，核心自己不用** |

重跑：`C="drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c drivers/watchdog/watchdog_pretimeout.c drivers/watchdog/watchdog_hrtimer_pretimeout.c"; for e in EINVAL ENODEV EBUSY EFAULT EOPNOTSUPP ENOMEM ENOIOCTLCMD ENOTTY EPERM EIO EAGAIN ERANGE; do echo -n "-$e "; grep -rho -- "-$e" $C | wc -l; done`

### §8.2 ⚠️`-ENOIOCTLCMD` 与 `-ENOTTY` 的分工

| 码 | 谁返回 | 谁看到 |
|---|---|---|
| `-ENOIOCTLCMD` | **驱动的 `ops->ioctl`** | **只有核心看到** ——**核心收到它就继续走自己的 switch** |
| `-ENOTTY` | **核心** | **用户态看到** ——**"这个 ioctl 不支持"** |

⇒ **驱动返回 `-ENOTTY` 是错的**：**会让核心直接把它透给用户态，跳过核心自己的处理。**
**官方文档明确要求返回 `-ENOIOCTLCMD`**（§2 表的 `ioctl` 行）。

### §8.3 `-EBUSY`：看门狗是独占设备

**7 处 `-EBUSY` 里最要紧的是"设备已被打开"** ——
**一个看门狗同一时刻只能有一个打开者。**
⇒ **`/dev/watchdog` 和 `/dev/watchdog0` 指向同一个设备（§0.4）⇒ 两个都打开，第二个拿 `-EBUSY`。**
**完整机制见 03 篇。**

### §8.4 设计债：0 条

**4 个 `watchdog_*.c` 里 `FIXME`/`TODO`/`REVISIT`/`HACK`/`XXX` 全无**
（重跑 `grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' drivers/watchdog/watchdog_*.c` 无输出）。
⚠️ **但官方 `watchdog-api.rst` 自己承认这套 ABI 是 "rather ad-hoc"**（§7.4）
⇒ **设计债在文档里，不在代码注释里。**

---

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| **`WDOG_ACTIVE` 与 `WDOG_HW_RUNNING` 的区别** | `02-datastructures.md` §1.0 |
| 注册 / 打开 / 喂狗 / 关闭的完整流程 | `03-internals.md` |
| pretimeout governor 与 hrtimer 软 pretimeout | `04-extensions.md` |
| **驱动骨架 + 用户态骨架** | `05-usage.md` |
| DT 属性（`timeout-sec` 等） | `06-devicetree.md` |
| **12 个 sysfs 属性 + 用户态工具** | `07-debug.md` |
| 为什么要有两套超时、为什么内核会替用户态喂狗 | `08-feynman.md` |
