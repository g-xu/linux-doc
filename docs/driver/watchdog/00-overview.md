---
title: 总览
---

# watchdog 00：总览（复习入口）

> **这一篇的定位是"复习入口"**：隔几个月回来，只读这一篇就能恢复对看门狗子系统的全貌认识。
> 它是对 01–08 篇的**浓缩**，不引入任何新论断。
> **基线**：`linux-6.1.158`（`git log -1` = `f6e38ae624cf`），
> **arm64-only**，**`drivers/watchdog/`、`include/linux/watchdog.h`、
> `include/uapi/linux/watchdog.h` 均未被本树补丁修改**（99 §1）。

---

## §1 定位：它在内核里是什么

**一句话**：**watchdog 子系统把"一块会在软件失控时复位系统的硬件电路"
包装成统一的用户态接口，并在用户态还没接手的空窗期自己替它喂狗。**

| 维度 | 结论 | 依据 |
|---|---|---|
| **子系统类型** | **框架型 + 带用户态 ABI** | **门 1（`bus_type`）无输出；门 2 有 1 个 uapi 头文件**（99 §1.2） |
| 代码位置 | **`drivers/watchdog/`（180 个 `.c`/`.h` / 59266 行）** | 01 §0.2 |
| ⚠️ **核心有多小** | **5 个文件 2132 行，占目录的 3.6%** | 01 §0.2 |
| **两个头文件** | **`include/linux/watchdog.h` 228 行（内核 API）+ `include/uapi/linux/watchdog.h` 58 行（用户态 ABI）** | 01 §0.1 |
| 对外符号 | **9 个导出符号**（不是 10，见 99 §6 表 3 的口径） | 01 §0.5 |
| **可插拔层数** | **两层**：`watchdog_ops`（10 字段）+ `watchdog_governor`（2 字段） | 01 §1.2 / 02 §1.0.F |
| **官方文档** | **10 个文件 / 1904 行 ——本项目做过的子系统里最多** | 01 §0.3 / 99 §1.3 |
| **可观测性** | **好**：12 个 sysfs 属性 + 4 个 tracepoint + 11 个 ioctl | 07 §0 |
| **DT 参与度** | **低** ——**核心只消费一个属性 `timeout-sec`** | 06 §1 |

**它不做什么**：

| 不做的事 | 归谁 |
|---|---|
| **不"触发"复位** | **它只是"停止喂狗"，硬件自己复位**（§2.3 / 08 §4） |
| **不代管任何资源** | **`clocks` / `resets` / `interrupts` 全由驱动处理**（与 phy 代管 `phy-supply` 相反，06 §2.3） |
| **不做故障注入** | **0 个设施**（但"停止喂狗"本身就是最真实的注入，07 §6） |
| **不提供 debugfs** | **0**（sysfs 已经很全） |

---

## §2 背景：这个框架在解决什么问题

### §2.1 硬件的形状决定了框架的形状

**看门狗硬件的三条特性**（08 §1）：
**① 它是独立电路，不受 CPU 死锁影响；② 默认会复位，除非被主动阻止；
③ 阻止的动作极简（往一个寄存器写值）。**

⇒ **所以框架的核心工作只有一件：管好"谁在什么时候喂狗"。**

**官方 `watchdog-api.rst` 的定位句（原话）**：
> *"A Watchdog Timer (WDT) is a hardware circuit that can reset the computer system in case of a
> software fault."*

### §2.2 问题一：几十种硬件、一套用户态接口

**看门狗硬件的差异极大**：
**有的能停有的不能停 · 有的能改超时有的固定 · 有的有"提前中断"有的没有 ·
有的超时上限 2 秒有的 1 小时。**

**而用户态守护进程只想做一件事**：**打开一个设备、定期写一个字节。**

⇒ **框架把差异收进 `struct watchdog_ops`（10 个字段，只有 `start` 必需）**，
**对上暴露统一的 11 个 ioctl + 一个 `write()` 语义**（01 §2 / §7）。

**官方 `watchdog-kernel-api.rst` 的自我定位（原话）**：
> *"This framework provides all interfacing towards user space so that the same code does not have
> to be reproduced each time."*

### §2.3 问题二：硬件撑不了那么久

**很多 SoC 内置看门狗的最长超时只有一两秒**，
**但用户态守护进程的巡检周期通常是几十秒。**

⇒ **框架的解法是当"节拍转换器"**：
**两个字段（`timeout` 秒 / `max_hw_heartbeat_ms` 毫秒）+ 一个内核工作队列。**

```
   用户态每 60 秒喂一次
        │
        ▼
   核心记账 last_keepalive
        │
        ├─ 核心每 <1 秒喂一次硬件（keepalive_interval = hw_heartbeat_ms / 2）
        │
        └─ 用户态超过 60 秒没露面 ⇒ 核心停止喂 ⇒ 硬件在 ≤hw_heartbeat_ms 内复位
```

**算法在 `drivers/watchdog/watchdog_dev.c:98-126`**，**源码注释解释了为什么要提前一个心跳**
（03 §B2.3 / 08 §3）。

### §2.4 问题三：用户态还没起来的那几秒

**bootloader 常常已经把看门狗打开了**（防止内核自己起不来）。
**但从内核启动到守护进程就绪，可能有几十秒。**

⇒ **框架用 `WDOG_HW_RUNNING` + `WATCHDOG_HANDLE_BOOT_ENABLED` 接管这段空窗**（02 §1.0.A）：
**内核自己喂狗，直到用户态打开 `/dev/watchdog`。**
⇒ **而 `WATCHDOG_OPEN_TIMEOUT` 给这个"代喂"设了上限** ——
**超时就放手，让机器重启**（03 §B2.4 / 08 §3）。

### §2.5 问题四：怎么区分"守护进程正常退出"和"守护进程崩了"

**两者在硬件层面都表现为"没人喂了"。**
⇒ **框架要求一个"签字"动作：关闭前往设备写一个 `'V'`**（magic close，01 §7.5 / 08 §5）。

**三层优先级**：**`nowayout` > magic close > 默认（关闭即停表）。**

### §2.6 ⚠️ 它为此付出的代价（读 watchdog 一定要知道的五条）

#### ⚠️ 代价 1：两个"在跑"，四种组合

**`WDOG_ACTIVE`（用户态视角）与 `WDOG_HW_RUNNING`（硬件视角）是两个独立的位**
（02 §1.0.A / 08 §2）。
⚠️ **而 sysfs 的 `state` 只反映前者** ⇒ **`inactive` 不代表硬件停了**（07 §2.2）。

#### ⚠️ 代价 2：延迟注册会吞掉失败

**框架的 initcall 是 `subsys_initcall_sync`，可能晚于驱动 probe**
⇒ **早注册的进队列，`watchdog_register_device()` 直接返回 0。**
⚠️ **而队列被消费时 `__watchdog_register_device()` 的返回值被丢弃**
（`drivers/watchdog/watchdog_core.c:455`）
⇒ **驱动 probe"成功"，看门狗却不存在**（03 §A2 / §C3 第 1 类 / 07 §5.2）。

#### ⚠️ 代价 3：一条 `pr_crit` 描述正常路径

**守护进程不写 `'V'` 就退出（这正是 magic close 想要的）会打印**：
```
watchdog0: watchdog did not stop!
```
**级别是 `pr_crit`**（`drivers/watchdog/watchdog_dev.c:946`）
⇒ **看起来像故障，其实是设计行为**（07 §4.3 / 03 §C4 第 3 条）。

#### ⚠️ 代价 4：精度损失

**实际复位时刻比 `timeout` 晚最多一个 `max_hw_heartbeat_ms`**（03 §B2.3 / 08 §3）
⇒ **需要"精确 N 秒复位"的场景不能依赖内核代喂机制。**

#### ⚠️ 代价 5：官方文档三处过时

| # | 文档说 | 代码是 |
|---|---|---|
| 1 | **`restart` 一个参数** | **三个参数**（`include/linux/watchdog.h:54`） |
| 2 | **`watchdog_device` 字段列表** | **漏了 `pm_nb`**（`:110`） |
| 3 | **只讲 3 个 `WDOG_*` 位** | **代码有 6 个**（`:115-120`） |

**详见 03 §C2 / 99 §1.4。**

### §2.7 本文档裁掉了什么（别当成遗漏）

| 裁掉的 | 为什么 |
|---|---|
| **约 170 个看门狗驱动的实现** | **只讲契约**；代表驱动的选法见 99 §2.4 |
| **`watchdog-parameters.rst`（740 行）** | **逐驱动的模块参数字典，不是机制说明** ——**登记为"只查不读"**（99 §1.3） |
| **四个厂商专属文档**（`hpwdt` / `pcwd-watchdog` / `mlx-wdt` / `wdt`，共 277 行） | **单驱动文档** |
| **`convert_drivers_to_kernel_api.rst`（219 行）** | **老驱动迁移指南**，**只读了标题结构** |
| **65 个厂商 DT 绑定** | **只讲通用绑定的两个属性**（06 §0.2） |
| **`panic()` 在关中断持锁上下文能否产出 crash dump** | **需要实机验证**（04 §2.2 / 99 §5.3） |
| **PREEMPT_RT 下两个 hrtimer 模式差异的实测** | **未在 RT 上运行**（04 §3.3 / 99 §5.4） |

---

## §3 概念模型：五样东西描述"谁在喂狗"

**这是本子系统最需要记住的一节。**

| 东西 | 位置 | 含义 |
|---|---|---|
| **`WDOG_ACTIVE`** | `include/linux/watchdog.h:115` | **用户态在用** |
| **`WDOG_HW_RUNNING`** | `include/linux/watchdog.h:118` | **硬件在转** |
| **`last_keepalive`** | `drivers/watchdog/watchdog_core.h:45` | **用户态上次喂狗的时刻** |
| **`last_hw_keepalive`** | `drivers/watchdog/watchdog_core.h:46` | **硬件上次被喂的时刻** |
| **`open_deadline`** | `drivers/watchdog/watchdog_core.h:47` | **内核代喂的截止时刻** |

⇒ **任何"看门狗行为不对"的问题，都可以化归成"这五样东西现在是什么值"。**

```
   用户态守护进程
        │ open / write("x") / write("V") / close
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ /dev/watchdog（misc，仅 id==0） │ /dev/watchdogN（cdev）       │
  │            └───── 同一个 watchdog_core_data ─────┘            │
  └──────────────────────┬───────────────────────────────────────┘
                         │  watchdog_fops
  ┌──────────────────────▼───────────────────────────────────────┐
  │ watchdog_dev.c（1306 行）                                     │
  │   3 个私有位 _WDOG_DEV_OPEN/_ALLOW_RELEASE/_KEEPALIVE          │
  │   3 个时间戳 + hrtimer + 全局 kthread_worker（内核代喂）      │
  │   12 个 sysfs 属性                                            │
  └──────────────────────┬───────────────────────────────────────┘
                         │  watchdog_dev_register/suspend/resume
  ┌──────────────────────▼───────────────────────────────────────┐
  │ watchdog_core.c（485 行）                                     │
  │   注册 + 延迟注册队列 + watchdog_ida + 3 个 notifier           │
  └──────────────────────┬───────────────────────────────────────┘
                         │  层 1：watchdog_ops（10 字段，只有 start 必需）
  ┌──────────────────────▼───────────────────────────────────────┐
  │ 看门狗驱动（约 170 个）                                        │
  └──────────────────────────────────────────────────────────────┘

  旁路（层 2）：驱动中断 → watchdog_notify_pretimeout() → gov->pretimeout
                                                          ⇒ noop 或 panic
```

⚠️ **四个必须记住的"不是"**：
1. **框架不"触发"复位** ——**只是停止喂狗**（08 §4）。
2. **`/dev/watchdog` 不是总入口** ——**它就是 `id == 0` 那一个**（02 §1.0.C / 08 §8）。
3. **`state = inactive` 不代表硬件停了**（07 §2.2）。
4. **`WDOG_*`（公共 6 个）与 `_WDOG_*`（私有 3 个）是两套位**（02 §1.0.B）。

---

## §4 数据结构速览

| 结构 | 位置 | 字段数 | 谁填 | 关键字段 |
|---|---|---|---|---|
| `struct watchdog_device` | `include/linux/watchdog.h:94-122` | **20** | **驱动** | `info` `ops` `gov` `bootstatus` `timeout` `pretimeout` `min/max_timeout` `min/max_hw_heartbeat_ms` · 3 个 notifier · `wd_data` · `status`（6 位） |
| `struct watchdog_ops` | `include/linux/watchdog.h:43-56` | **10** | **驱动** | **`owner` + 唯一必需的 `start` + 8 个可选** |
| `struct watchdog_core_data` | `drivers/watchdog/watchdog_core.h:40-57` | **11** | **核心（驱动看不到）** | **`dev`（真正的 device）· `cdev` · `lock` · 3 个 `ktime_t` · hrtimer + kthread_work · `status`（3 私有位）** |
| `struct watchdog_governor` | `drivers/watchdog/watchdog_pretimeout.h:9-12` | **2** | **governor 模块** | `name`（20 字节定长）· `pretimeout` 回调（`void` 返回） |
| `struct watchdog_info` | **`include/uapi/linux/watchdog.h`** | **3** | **驱动** | ⚠️ **`identity[32]` 不保证 NUL 结尾** |

**五个文件级变量 + 四把锁**（02 §6）：
**`old_wd_data` · `watchdog_kworker` · `handle_boot_enabled` · `open_timeout` · `watchdog_ida`
+ `wd_data->lock`（mutex）· `wtd_deferred_reg_mutex` · `pretimeout_lock`（**spinlock**）· `governor_lock`。**

---

## §5 机制速览

### §5.1 注册：两条路

| 路 | 条件 | 行为 |
|---|---|---|
| **立即注册** | **`wtd_deferred_reg_done == true`** | **`__watchdog_register_device()` 七步** |
| ⚠️ **延迟注册** | **框架 initcall 之前** | **入队，返回 0；真正注册的失败被丢弃** |

**七步的要点**（03 §A3）：
**① 三指针检查 → ② 能力校验（`start` 必需、无 `stop` 则须有 `max_hw_heartbeat_ms`）
→ ③ 修正 min/max → ④ id 分配（先试 DT alias）→ ⑤ 建字符设备（id 0 失败会退让）
→ ⑥ reboot notifier（失败致命）→ ⑦ restart/pm notifier（失败只警告）。**

### §5.2 喂狗：两条并行的路

```
  路 A（用户态）：write() 或 ioctl(WDIOC_KEEPALIVE)
       └─ watchdog_ping() → __watchdog_ping() → ops->ping ?: ops->start

  路 B（内核代喂）：hrtimer 到期 → kthread_work
       └─ watchdog_worker_should_ping() ? __watchdog_ping() : 不重排（⇒ 复位）
```

**两条路最终汇到同一个 `__watchdog_ping()`**（`drivers/watchdog/watchdog_dev.c:143`）。
**要不要走路 B 由 `watchdog_need_worker()` 的两个条件式决定**（03 §B2.2）。

### §5.3 关闭：三层判定

| 情形 | 结果 | 日志 |
|---|---|---|
| **没启动过** | 什么都不做 | 无 |
| **收到过 `'V'`** | **停表** | 无 |
| **驱动不支持 magic close** | **停表**（传统行为） | 无 |
| ⚠️ **支持 magic close 但没收到 `'V'`** | **不停 + 补喂一次 ⇒ 稍后复位** | **`pr_crit "watchdog did not stop!"`** |
| ⚠️ **`nowayout`** | **不停** | **`pr_info "nowayout prevents…"` + 上面那条** |

### §5.4 pretimeout：两层

**层 2 是 governor**（2 个内置：`noop` 打 `pr_alert`、`panic` 直接 panic，默认是 `panic`）。
⚠️ **governor 回调在 spinlock 内、关中断执行**（04 §1.4）。
**硬件没有 pretimeout 时可用 hrtimer 补一个软的**（04 §3）——
⚠️ **但那是"尽力而为"，且会让三个出口对 `WDIOF_PRETIMEOUT` 说法不一致**（04 §3.4 / 08 §6）。

### §5.5 可观测性

| 出口 | 规模 | 头号陷阱 |
|---|---|---|
| **sysfs（12 个属性）** | 需 `CONFIG_WATCHDOG_SYSFS`（默认 N） | **`state` 只反映 `WDOG_ACTIVE`**；**`cat status` 有副作用** |
| **tracepoint（4 事件 / 6 打点）** | `include/trace/events/watchdog.h` 66 行 | **`watchdog_start` 可能只是一次喂狗** |
| **ioctl（11 个）** | `include/uapi/linux/watchdog.h` 58 行 | **`WDIOF_MAGICCLOSE` 在 GETSUPPORT 与 GETSTATUS 里含义不同** |
| **dmesg（19 条）** | 0 条需 dyndbg | **`pr_crit "did not stop"` 多数是正常的** |

---

## §6 端到端调用路径

```
① subsys_initcall_sync: watchdog_init()          watchdog_core.c:461-479
     ├─ watchdog_dev_init()                       watchdog_dev.c:1215 起
     │    ├─ kthread_create_worker(…)                   :1211 附近
     │    ├─ class_register(&watchdog_class)             :1218 附近
     │    └─ alloc_chrdev_region(…, MAX_DOGS, "watchdog") :1224
     └─ watchdog_deferred_registration()           watchdog_core.c:445-459
          └─ 逐个 __watchdog_register_device()（返回值被丢弃）:455

② 驱动 probe
     ├─ 填 watchdog_device（info/ops/timeout/min/max…）
     ├─ watchdog_set_drvdata()
     ├─ watchdog_init_timeout(wdd, 0, &pdev->dev)  watchdog_core.c:118
     │    └─ of_property_read_u32(…, "timeout-sec", &t)  :141
     └─ devm_watchdog_register_device()            watchdog_core.c:443
          └─ watchdog_register_device()                  :350
               ├─ 未过 initcall ⇒ 入队返回 0             :356-359
               └─ 已过 ⇒ __watchdog_register_device()     :240
                    ├─ 能力校验                          :248-249
                    ├─ id：of_alias_get_id ?: ida_simple_get :260-268
                    ├─ watchdog_dev_register()            :274
                    │    ├─ id==0 ⇒ misc_register()   watchdog_dev.c:1040
                    │    └─ cdev_device_add()                   :1058
                    └─ 3 个 notifier 条件注册            :294-334

③ 用户态 open("/dev/watchdog")                   watchdog_dev.c:842
     ├─ misc 走 old_wd_data / cdev 走 container_of      :850-854
     ├─ test_and_set_bit(_WDOG_DEV_OPEN) ⇒ -EBUSY       :857-858
     ├─ !hw_running ⇒ try_module_get(ops->owner)        :867
     ├─ watchdog_start(wdd)                             :247
     └─ open_deadline = KTIME_MAX                       :888

④ 喂狗（两条路，§5.2）

⑤ 用户态 close()                                 watchdog_dev.c:920
     └─ 三层判定（§5.3）→ watchdog_update_worker() → 可能放手 ⇒ 复位

⑥ 驱动 remove
     └─ watchdog_unregister_device()              watchdog_core.c:406
```

---

## §7 子系统关系

### §7.1 谁用 watchdog

| 使用者 | 怎么用 |
|---|---|
| **用户态守护进程**（`watchdog` / `systemd`） | **`/dev/watchdog` 的 `write()` 或 ioctl** ——**这是主要消费者** |
| **`ops->restart` 的使用者** | **`kernel_restart()` 走 restart handler 链** ——**看门狗可以当复位手段**（05 §2.3） |
| **kdump / panic 流程** | **`panic` governor 让 pretimeout 变成一次可分析的 panic**（04 §2.2） |

### §7.2 watchdog 用谁

| 被依赖的 | 用在哪 |
|---|---|
| **misc 设备 + cdev** | **两套字符设备**（02 §1.0.C） |
| **class / device / devres** | **`watchdog_class` + `watchdog_core_data.dev`** |
| **hrtimer + kthread_worker** | **内核代喂机制**（03 §B2） |
| **notifier**（reboot / restart / pm） | 03 §B3 |
| **IDA** | `watchdog_ida` |
| **OF** | **只用 `of_property_read_u32` 和 `of_alias_get_id` 两个**（06 §1 / §3） |

### §7.3 与本项目其他文档集的关系

| 相邻子系统 | 关系 | 去 |
|---|---|---|
| **cdev** | **watchdog 是"带字符设备的框架"的典型** | **`output/cdev/`** |
| **timer** | **内核代喂靠 hrtimer** | `output/timer/` |
| **of** | **`timeout-sec` 与 aliases** | `output/of/` |
| **phy / regmap** | **对照用**：**phy/regmap 无用户态 ABI、无 sysfs；watchdog 三样都有** | 各自文档集 |

---

## §8 导读：接下来读哪一篇

| 我现在… | 读 | 里面有什么 |
|---|---|---|
| **要写一个看门狗驱动** | **`05-usage.md` §1–§3** | **最简 / 完整 / 停不下来 三种骨架** |
| **要写守护进程** | **`05-usage.md` §4** | **in-tree 样例 + magic close 的四条规则** |
| **要查"机器为什么自己重启了"** | **`07-debug.md` §5.1** | **`bootstatus` 取证** |
| 要查 API 语义 | `01-api.md` | §2 十个回调 · §3 十九个字段 · §7 用户态 ABI · §8 错误码 |
| 要看结构体关系 | `02-datastructures.md` | **§1.0 六组易错关系** · §3 数据流 · §6 四把锁 |
| 要理解内部流程 | `03-internals.md` | Part A 时间线 · **Part B 心跳算法** · Part C 三处文档不一致 + 四类静默失效 |
| pretimeout | `04-extensions.md` | governor 框架 · 两个内置 · 软 pretimeout · **一处未验证的可疑之处** |
| DTS | `06-devicetree.md` | **`timeout-sec` 的三层优先级** · §3 aliases 是死字 · §4 六层决策树 |
| **出问题不知道看哪** | **`07-debug.md` §0 / §8** | **12 个 sysfs 属性 + 三个高频场景 + 一页速查** |
| **看不懂"为什么这样设计"** | **`08-feynman.md`** | **八个主题，每个都有"这个类比什么时候会误导你"** |
| **哪些结论不能直接信** | **`99-evidence.md` §5** | **未验证项四层 + 逐篇取证表** |

### §8.1 第一次读 watchdog 的推荐顺序

1. **`08-feynman.md` §2 + §3** ——**先搞清两个"在跑"和"内核代喂"。**
2. **`01-api.md` §3.3 + §3.5** ——**六个时间字段 + 六个状态位。**
3. **`03-internals.md` §0.2** ——**一张生命周期时间线。**
4. **`05-usage.md` §1 + §4** ——**两端各抄一个骨架。**
5. **`07-debug.md` §2.2 + §4.3** ——**两条最容易误读的观测。**

### §8.2 ⚠️ 六条"最容易记错"的事（复习时优先重看）

| 事 | 正确的是 | 在哪 |
|---|---|---|
| ⚠️ **`state = inactive` 是不是硬件停了** | ⚠️ **不是** ——**只表示用户态没在用** | 07 §2.2 / 08 §2 |
| ⚠️ **`pr_crit "watchdog did not stop!"` 是不是故障** | ⚠️ **多数不是** ——**正常的 magic close** | 07 §4.3 / 08 §5 |
| ⚠️ **`'V'` 写过一次是不是就一直有效** | ⚠️ **不是** ——**必须在最后一次 write 里** | 05 §4.2 / 08 §5 |
| ⚠️ **`timeout-sec = <0>` 是不是"用默认"** | ⚠️ **不是，那是非法值** ——**模块参数的 0 才是** | 06 §1.2 |
| ⚠️ **`nowayout` 能不能改回 0** | ⚠️ **不能** ——**sysfs 写 0 返回 `-EPERM`** | 07 §2.5 / 08 §7 |
| ⚠️ **驱动 probe 返回 0 是不是就注册成功了** | ⚠️ **不一定** ——**延迟注册路径会吞掉失败** | 03 §A2 / 07 §5.2 |
