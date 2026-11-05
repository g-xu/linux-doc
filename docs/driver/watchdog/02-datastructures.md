---
title: 数据结构
---

# watchdog 02：数据结构

## §1.0 先分清六组关系

**图画得再全，也补不上一个没被显式否掉的错误类比。**
**本节六组，前两组是 watchdog 最容易想错的地方。**

### §1.0.A ⚠️⚠️ 组 1：`WDOG_ACTIVE` ≠ `WDOG_HW_RUNNING`（本子系统第一号易错点）

**先否掉这个类比**：
> ❌ *"看门狗要么在跑要么不在跑，一个布尔量就够了。"*

**不够。watchdog 有两个独立的"在跑"，四种组合都真实存在。**

| | **`WDOG_ACTIVE`（位 0）** | **`WDOG_HW_RUNNING`（位 3）** |
|---|---|---|
| 位置 | `include/linux/watchdog.h:115` | `include/linux/watchdog.h:118` |
| 含义 | **"用户态视角在跑"** | **"硬件计时器在转"** |
| 谁设 | **核心**（用户态打开设备并启动时） | **驱动**（或核心在"不实现 `stop`"时代设） |
| 查询 inline | `watchdog_active(wdd)`（`:128`） | `watchdog_hw_running(wdd)`（`:137`） |
| **谁负责喂狗** | **用户态** | **内核的工作队列** |

**四种组合的真实场景**：

| `WDOG_ACTIVE` | `WDOG_HW_RUNNING` | 场景 |
|---|---|---|
| 0 | 0 | **最常见的初始状态**：看门狗注册了但没人用 |
| **0** | **1** | **两种情况**：**① 启动前 bootloader 就开了表，用户态还没接手**（`WATCHDOG_HANDLE_BOOT_ENABLED`）；**② 硬件不能停表（驱动没实现 `stop`），用户态已经关闭了设备** ⇒ **这时核心自己在喂狗** |
| 1 | 1 | **正常工作态**：用户态在喂，硬件在转 |
| ⚠️ **1** | ⚠️ **0** | **短暂的中间态**（启动过程中） |

**官方文档 `watchdog-kernel-api.rst` 对 `WDOG_HW_RUNNING` 的原话**：
> *"If set, the watchdog infrastructure will send keepalives to the watchdog hardware while
> WDOG_ACTIVE is not set. Note: when you register the watchdog timer device with this bit set,
> then opening /dev/watchdog will skip the start operation but send a keepalive request instead."*

⇒ **这是 watchdog 框架最核心的一条机制**：
**它让"系统启动到用户态守护进程就绪"这段空窗期有人管**（03 篇 / 08 §2）。

### §1.0.B ⚠️ 组 2：公共状态位 `WDOG_*` ≠ 私有状态位 `_WDOG_*`（只差一个下划线）

**先否掉这个类比**：
> ❌ *"`WDOG_ACTIVE` 和 `_WDOG_DEV_OPEN` 应该在同一个 `status` 字段里吧？"*

**不是。它们在两个不同结构的两个不同 `status` 字段里。**

| | **`WDOG_*`（6 个）** | **`_WDOG_*`（3 个）** |
|---|---|---|
| 定义在 | **`include/linux/watchdog.h:115-120`** | **`drivers/watchdog/watchdog_core.h:54-56`** |
| 住在哪个字段 | **`struct watchdog_device.status`**（`:113`） | **`struct watchdog_core_data.status`**（`watchdog_core.h:53`） |
| **驱动看得到吗** | **看得到**（公共头） | ⚠️ **看不到**（`watchdog_core.h` 不在 `include/` 下） |
| 具体位 | `WDOG_ACTIVE` · `WDOG_NO_WAY_OUT` · `WDOG_STOP_ON_REBOOT` · `WDOG_HW_RUNNING` · `WDOG_STOP_ON_UNREGISTER` · `WDOG_NO_PING_ON_SUSPEND` | **`_WDOG_DEV_OPEN`（0）· `_WDOG_ALLOW_RELEASE`（1）· `_WDOG_KEEPALIVE`（2）** |
| 管什么 | **设备的能力与策略** | **字符设备的打开状态与 magic close 状态机** |

⇒ **magic close 的全部状态就是那 3 个私有位**：
**`_WDOG_DEV_OPEN` = 有人打开了；`_WDOG_ALLOW_RELEASE` = 收到过 `'V'`；
`_WDOG_KEEPALIVE` = 收到过喂狗**（03 篇讲状态机）。

⚠️ **命名规则**：**前导下划线 = 核心私有。**
**驱动里出现 `_WDOG_` 开头的标识符就是错的**（编译期就会失败，因为头文件不可见）。

### §1.0.C ⚠️ 组 3：`/dev/watchdog` ≠ `/dev/watchdog0`（同一个设备，两个入口）

**先否掉这个类比**：
> ❌ *"`/dev/watchdog` 是所有看门狗的总入口，`/dev/watchdogN` 是各自的。"*

**不是。`/dev/watchdog` 就是 `id == 0` 那一个看门狗，别的看门狗它管不了。**

| | **`/dev/watchdog`（旧）** | **`/dev/watchdogN`（新）** |
|---|---|---|
| 注册代码 | **`misc_register(&watchdog_miscdev)`**（`drivers/watchdog/watchdog_dev.c:1040`） | **`cdev` + `alloc_chrdev_region(…, MAX_DOGS, "watchdog")`**（`:1224`） |
| 设备号 | **主 10（misc）+ 次 `WATCHDOG_MINOR`**（`:980`） | **动态主设备号 + 次设备号 = `id`** |
| **条件** | **`if (wdd->id == 0)`**（`drivers/watchdog/watchdog_dev.c:1037`） | **每个都建** |
| 数量 | **最多 1 个** | **最多 `MAX_DOGS = 32` 个**（`drivers/watchdog/watchdog_core.h:30`） |
| **`fops` 是同一套吗** | **是**：**`watchdog_miscdev.fops = &watchdog_fops`（`:982`）** | **同一个 `watchdog_fops`** |

**"为什么不会冲突"也要有依据**：
**`id == 0` 时核心把 `wd_data` 存进全局 `old_wd_data`**（`drivers/watchdog/watchdog_dev.c:1038`）
⇒ **misc 设备通过这个全局变量找到自己对应的看门狗。**
⇒ **两个入口最终操作的是同一个 `struct watchdog_core_data`**
⇒ **所以同时打开两个会拿到 `-EBUSY`**（01 §8.3 / 03 篇）。

**源码注释就是这条设计的说明**（`drivers/watchdog/watchdog_dev.c:995-997`，逐字）：
> *"Register a watchdog character device including handling the legacy /dev/watchdog node.
> /dev/watchdog is actually a miscdevice and thus we set it up like that."*

### §1.0.D 组 4：`timeout`（秒）≠ `max_hw_heartbeat_ms`（毫秒）

**先否掉这个类比**：
> ❌ *"超时就是超时，硬件超时和用户设的超时应该是一回事。"*

**不是。这是 watchdog 框架里最重要的一层解耦。**

| | **`timeout`** | **`max_hw_heartbeat_ms`** |
|---|---|---|
| 位置 | `include/linux/watchdog.h:102` | `include/linux/watchdog.h:107` |
| 单位 | **秒** | ⚠️ **毫秒** |
| 谁看 | **用户态**（`WDIOC_SETTIMEOUT` / `WDIOC_GETTIMEOUT`） | **只有核心** |
| 含义 | **"用户态多久不喂狗就重启"** | **"硬件最长能撑多久"** |
| **谁大** | **可以比后者大得多** | — |

**两者不等时核心做的事（官方文档原话）**：
> *"If set, the infrastructure will send heartbeats to the watchdog driver if 'timeout' is larger
> than max_hw_heartbeat_ms, unless WDOG_ACTIVE is set and userspace failed to send a heartbeat
> for at least 'timeout' seconds."*

⇒ **核心变成了用户态与硬件之间的"节拍转换器"**：
**硬件要每 1.5 秒喂一次，用户态想 60 秒喂一次 ⇒ 核心每 <1.5 秒喂一次，
并且自己记账"用户态上次喂是什么时候"，超过 60 秒才放手。**

**记账靠 `watchdog_core_data` 里的两个时间戳**（`drivers/watchdog/watchdog_core.h:45-46`）：
**`last_keepalive`（用户态那次）和 `last_hw_keepalive`（硬件那次）** ——
**两个字段的存在本身就是这条机制的证据**（§5.2）。

⚠️ **另一个后果**：**`max_hw_heartbeat_ms` 非 0 时 `max_timeout` 不生效**
（官方文档 `max_timeout` 条原话：*"Not used if max_hw_heartbeat_ms is non-zero."*）。

### §1.0.E 组 5：`watchdog_device`（公共）≠ `watchdog_core_data`（私有）

| | **`struct watchdog_device`** | **`struct watchdog_core_data`** |
|---|---|---|
| 位置 | **`include/linux/watchdog.h:94-122`** | **`drivers/watchdog/watchdog_core.h:40-57`** |
| 字段数 | **20** | **11**（其中 1 个带 `#if`） |
| **谁分配** | **驱动**（通常是驱动私有结构里的一个成员） | **核心**（在 `watchdog_cdev_register()` 里） |
| **是 `struct device` 吗** | ⚠️ **不是** ——**只有一个 `parent` 指针**（`:96`） | **是** ——**第一个字段就是 `struct device dev`**（`watchdog_core.h:41`） |
| 互指 | **`wdd->wd_data`**（`include/linux/watchdog.h:112`） | **`wd_data->wdd`**（`watchdog_core.h:43`） |

⇒ **注意方向**：**真正在 sysfs / devtmpfs 里出现的设备是 `watchdog_core_data.dev`，
不是驱动填的那个 `watchdog_device`。**
⇒ **`watchdog_device.parent` 是"硬件的父设备"，而 `watchdog_core_data.dev.parent` 才是设备树上的挂点。**

**"为什么要分两个"**：**驱动的 `watchdog_device` 生命周期由驱动控制，
而字符设备必须活到最后一个 `open` 关闭为止** ⇒ **两者寿命不同，必须分开。**

### §1.0.F 组 6：本子系统有几层可插拔 —— **两层**

| 层 | ops 结构 | 位置 | 实现者 | 实现数 |
|---|---|---|---|---|
| **层 1：看门狗操作** | **`struct watchdog_ops`**（10 字段） | `include/linux/watchdog.h:43-56` | **看门狗驱动** | **约 170 个驱动** |
| **层 2：pretimeout 治理** | **`struct watchdog_governor`**（**只有 2 个字段**） | `drivers/watchdog/watchdog_pretimeout.h:9-12` | **governor 模块** | **2 个内置** |

```c
/* drivers/watchdog/watchdog_pretimeout.h:9-12 —— 层 2 的全部内容 */
struct watchdog_governor {
	const char	name[WATCHDOG_GOV_NAME_MAXLEN];
	void		(*pretimeout)(struct watchdog_device *wdd);
};
```

⚠️ **层 2 的头文件在 `drivers/watchdog/` 下，不在 `include/` 下**
⇒ **树外模块写不了 governor**（尽管注册函数是导出的，01 §0.5）。
⚠️ **`name` 是定长数组不是指针** ⇒ **governor 名字有长度上限
`WATCHDOG_GOV_NAME_MAXLEN = 20`**（`drivers/watchdog/watchdog_pretimeout.h:5`）。

**对照**：**regmap 三层 · watchdog 两层 · phy 一层。**

---

## §2 核心抽象与四方的关系

**watchdog 有一个别的框架型子系统没有的角色：用户态。**

```
   ┌──────────────────────────────────────────────────────────┐
   │ 用户态守护进程（watchdogd / systemd 等）                  │
   │   open("/dev/watchdog") → write("x") 或 ioctl(KEEPALIVE) │
   │   关闭前 write("V")  ← magic close                     │
   └──────────────────────┬───────────────────────────────────┘
                          │ 唯一的用户态入口：watchdog_fops
   ┌──────────────────────▼───────────────────────────────────┐
   │ drivers/watchdog/watchdog_dev.c（1306 行）                │
   │                                                           │
   │  ① 字符设备两套：misc(/dev/watchdog) + cdev(/dev/watchdogN)│
   │  ② watchdog_core_data：dev + cdev + mutex + 3 个私有位     │
   │  ③ 心跳工作队列：kthread_worker + hrtimer                 │
   │  ④ 12 个 sysfs 属性（wdt_groups）                         │
   └──────────────────────┬───────────────────────────────────┘
                          │
   ┌──────────────────────▼───────────────────────────────────┐
   │ drivers/watchdog/watchdog_core.c（485 行）                │
   │  ① watchdog_register_device + 延迟注册链表                │
   │  ② 三个 notifier：reboot / restart / pm                   │
   │  ③ watchdog_ida 分配 id                                   │
   └──────────────────────┬───────────────────────────────────┘
                          │ 层 1：struct watchdog_ops（10 字段）
   ┌──────────────────────▼───────────────────────────────────┐
   │ 看门狗驱动（约 170 个）                                    │
   │   只有 start 是必需的                                      │
   └───────────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────────┐
   │ pretimeout governor（层 2，2 个内置）                      │
   │   驱动中断 → watchdog_notify_pretimeout() → gov->pretimeout│
   └───────────────────────────────────────────────────────────┘
```

**四条边要记住**：
1. **用户态是主要消费者** ——**`/dev/watchdog` 的 `write()` 就是喂狗。**
2. **`watchdog_dev.c` 与 `watchdog_core.c` 的分工**：**前者管"对用户态的面"，后者管"注册与系统事件"。**
3. **驱动只填两个结构、实现 1–9 个回调，不碰字符设备。**
4. **governor 是旁路**：**它不参与喂狗，只在"快超时"事件上做决定。**

---

## §3 核心操作数据流：一次喂狗

```
用户态: write(fd, "x", 1)                     drivers/watchdog/watchdog_dev.c（watchdog_fops.write）
  │
  ├─ ① 逐字节扫描，看有没有 'V'
  │     └─ 有 ⇒ set_bit(_WDOG_ALLOW_RELEASE)  magic close 的记账
  │     └─ 其他字符 ⇒ clear_bit(_WDOG_ALLOW_RELEASE)  写过别的就作废
  │
  ├─ ② set_bit(_WDOG_KEEPALIVE)
  │
  └─ ③ watchdog_ping(wdd)                     :190
        ├─ 不是 WDOG_ACTIVE 也不是 HW_RUNNING ⇒ 直接返回
        └─ __watchdog_ping(wdd)                :143
              ├─ 算距上次硬件喂狗多久
              ├─ 没到 min_hw_heartbeat_ms ⇒ 不真喂，只更新记账
              └─ ops->ping ? ops->ping(wdd) : ops->start(wdd)   层 1
                    └─ 驱动写硬件寄存器
```

**另一条并行的路：内核自己喂狗**

```
kthread_worker（全局 watchdog_kworker，watchdog_dev.c:57）
  │
  └─ wd_data->work（kthread_work）
        └─ watchdog_worker_should_ping(wd_data)    :203
              ├─ WDOG_HW_RUNNING 置了？
              ├─ WDOG_ACTIVE 未置 ⇒ 内核代喂（组 1 的第二种组合）
              ├─ WDOG_ACTIVE 置了 ⇒ 看用户态是不是已经超过 timeout 没喂
              └─ 还要看 open_deadline（WATCHDOG_OPEN_TIMEOUT）
        └─ 该喂 ⇒ __watchdog_ping()；不该喂 ⇒ 停止重排 hrtimer ⇒ 硬件超时 ⇒ 复位
```

⚠️ **"停止喂狗"就是 watchdog 触发复位的唯一方式** ——
**框架从不主动"触发"复位，它只是"不再阻止"。**
⇒ **这是理解 watchdog 全部行为的关键**（08 §3）。

---

## §4 命名约定速查

| 形态 | 含义 | 例子 |
|---|---|---|
| `watchdog_*` | **内核 API（驱动可用）** | `watchdog_register_device` `watchdog_init_timeout` |
| `devm_watchdog_*` | **自动释放版** | **只有一个：`devm_watchdog_register_device`** |
| `__watchdog_*` | **核心内部** | `__watchdog_ping`（`drivers/watchdog/watchdog_dev.c:143`）· `__watchdog_register_device`（`watchdog_core.c:240`） |
| `WDOG_*` | **公共状态位**（`watchdog_device.status`） | `WDOG_ACTIVE` `WDOG_HW_RUNNING` |
| `_WDOG_*` | **私有状态位**（`watchdog_core_data.status`）——前导下划线 | `_WDOG_DEV_OPEN` `_WDOG_ALLOW_RELEASE` `_WDOG_KEEPALIVE` |
| `WDIOC_*` | **用户态 ioctl 命令** | `WDIOC_KEEPALIVE` `WDIOC_SETTIMEOUT` |
| `WDIOF_*` | **能力/状态标志**（F = flag） | `WDIOF_MAGICCLOSE` `WDIOF_CARDRESET` |
| `WDIOS_*` | **`WDIOC_SETOPTIONS` 的参数值**（S = set） | `WDIOS_ENABLECARD` `WDIOS_DISABLECARD` |
| `WATCHDOG_*` | **Kconfig 派生的宏** | `WATCHDOG_NOWAYOUT`（`include/linux/watchdog.h:124`）· `WATCHDOG_NOWAYOUT_INIT_STATUS`（`:125`） |

⚠️ **四个名字陷阱**：

| 陷阱 | 说明 |
|---|---|
| ⚠️ **`WDOG_*` vs `_WDOG_*` 只差一个下划线** | **§1.0.B** ——**两套位，两个字段，公私分明** |
| ⚠️ **`WDIOF_*` 与 `WDIOS_*` 只差一个字母** | **前者是"能力/发生了什么"，后者是"我要你做什么"** |
| ⚠️ **`*_invalid()` 的返回语义是反的** | **`watchdog_timeout_invalid()` 返回 `true` 表示不合法**（01 §5） |
| ⚠️ **`timeout` 是秒，`*_hw_heartbeat_ms` 是毫秒** | **§1.0.D** |

---

## §5 结构体逐个看

### §5.1 `struct watchdog_device`：20 个字段

**五组分类和逐字段说明在 01 §3，本节只补两点结构性的**：
1. **它不是 `struct device`** ——**只有 `parent` 指针（`include/linux/watchdog.h:96`）。**
2. **三个 `notifier_block` 是内嵌的**（`:108-110`）⇒ **注册 notifier 不额外分配内存。**

### §5.2 `struct watchdog_core_data`：11 个字段（驱动看不到）

```c
/* drivers/watchdog/watchdog_core.h:40-57 */
struct watchdog_core_data {
	struct device dev;		/* 真正出现在 sysfs 里的设备 */
	struct cdev cdev;		/* /dev/watchdogN */
	struct watchdog_device *wdd;	/* 反指回驱动那个结构 */
	struct mutex lock;		/* 唯一的 per-device 锁 */
	ktime_t last_keepalive;		/* 用户态上次喂狗 */
	ktime_t last_hw_keepalive;	/* 硬件上次被喂 */
	ktime_t open_deadline;		/* WATCHDOG_OPEN_TIMEOUT 的截止时刻 */
	struct hrtimer timer;		/* 驱动内核代喂的节拍 */
	struct kthread_work work;	/* 实际喂狗的工作项 */
#if IS_ENABLED(CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT)
	struct hrtimer pretimeout_timer;/* 软 pretimeout（04 篇） */
#endif
	unsigned long status;		/* 三个 _WDOG_* 私有位 */
};
```

**三个 `ktime_t` 是整个框架的记账核心**（§1.0.D / §3）：
**`last_keepalive` 决定"用户态死了没"，`last_hw_keepalive` 决定"该不该再喂硬件"。**

⚠️ **`hrtimer timer` + `kthread_work work` 是一对**：
**hrtimer 到期 ⇒ 排 `work` 到 `watchdog_kworker` ⇒ 在内核线程里真喂狗。**
⇒ **为什么不在 hrtimer 回调里直接喂？因为 `ops->ping` 可能睡眠（I2C 看门狗）。**

### §5.3 `struct watchdog_ops`：10 个字段

**完整表在 01 §2。结构上两点**：
1. **第一个字段是 `struct module *owner`，不是回调**（`include/linux/watchdog.h:44`）。
2. **源码用注释把必需与可选分开**（`:45` 的 `/* mandatory operations */` 和 `:47` 的 `/* optional operations */`）
   ⇒ **"只有 `start` 必需"这个结论直接来自源码注释，不是推断。**

### §5.4 `struct watchdog_governor`：2 个字段

**§1.0.F 已全文引用。唯一要补的**：
⚠️ **`pretimeout` 回调是 `void` 返回** ⇒ **governor 无法拒绝或报错。**

### §5.5 `struct watchdog_info`：uapi 结构

**3 个字段，01 §7.2 已全文引用。结构上要强调的**：
**它是 uapi 结构，字段布局不能改** ——**`WDIOC_GETSUPPORT` 直接 `copy_to_user` 它。**
⇒ **驱动填的 `wdd->info` 直接暴露给用户态，没有中间转换层。**

---

## §6 全局变量与锁

### §6.1 `watchdog_dev.c` 的四个文件级变量

| 变量 | 位置 | 用途 |
|---|---|---|
| ⚠️`old_wd_data` | `drivers/watchdog/watchdog_dev.c:55` | **`id == 0` 那个看门狗的 `wd_data`** ⇒ **`/dev/watchdog`（misc）靠它找到设备**（§1.0.C） |
| `watchdog_kworker` | `:57` | **全局的喂狗内核线程** ——**所有看门狗共用一个** |
| `handle_boot_enabled` | `:59` | **来自 `CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED`，可用模块参数覆盖** |
| `open_timeout` | `:62` | **来自 `CONFIG_WATCHDOG_OPEN_TIMEOUT`，可用 `watchdog.open_timeout` 覆盖** |

⚠️ **所有看门狗共用一个 `kworker`** ⇒ **某个驱动的 `ops->ping` 阻塞会拖累别的看门狗**
（03 Part C 的评价项）。

### §6.2 `watchdog_core.c` 的五个

| 变量 | 位置 | 用途 |
|---|---|---|
| `watchdog_ida` | `drivers/watchdog/watchdog_core.c:44` | **分配 `wdd->id`** |
| `stop_on_reboot` | `:46`（初值 `-1`） | **模块参数，三态：-1 未指定 / 0 / 1** |
| `wtd_deferred_reg_mutex` | `:63` | **保护延迟注册链表** |
| `wtd_deferred_reg_list` | `:64` | **延迟注册队列**（01 §4.1） |
| `wtd_deferred_reg_done` | `:65` | **标记"延迟注册阶段已过"** |

### §6.3 `watchdog_pretimeout.c` 的五个（两把不同类型的锁）

| 变量 | 位置 | 保护 |
|---|---|---|
| `default_gov` | `drivers/watchdog/watchdog_pretimeout.c:16` | **默认 governor** |
| ⚠️`pretimeout_lock` | **`:19`，`DEFINE_SPINLOCK`** | **保护 `pretimeout_list`** ——**是 spinlock 因为 `watchdog_notify_pretimeout()` 可在中断上下文调**（01 §6） |
| `pretimeout_list` | `:22` | **所有支持 pretimeout 的设备** |
| `governor_lock` | **`:30`，`DEFINE_MUTEX`** | **保护 `governor_list`** |
| `governor_list` | `:33` | **已注册的 governor** |

⚠️ **两把锁类型不同是有意的**（04 篇会展开）：
**`pretimeout_lock` 是 spinlock（中断上下文要用），`governor_lock` 是 mutex（只在注册/切换时用）。**

### §6.4 全框架的锁总表

| 锁 | 类型 | 位置 | 保护什么 |
|---|---|---|---|
| **`wd_data->lock`** | **mutex** | `drivers/watchdog/watchdog_core.h:44` | **per-device：一切对这个看门狗的操作** |
| `wtd_deferred_reg_mutex` | mutex | `watchdog_core.c:63` | 延迟注册链表 |
| `pretimeout_lock` | **spinlock** | `watchdog_pretimeout.c:19` | pretimeout 设备链表 |
| `governor_lock` | mutex | `watchdog_pretimeout.c:30` | governor 链表 |
| `watchdog_ida` | **IDA 自带** | `watchdog_core.c:44` | id 分配 |

⇒ **共 4 把显式锁 + 1 个 IDA**，**其中只有一把是 spinlock。**

---

## §7 结构关系总图

```
   用户态
     │ open/write/ioctl
     ▼
  ┌──────────────────────┐        ┌────────────────────────┐
  │ /dev/watchdog（misc） │        │ /dev/watchdogN（cdev）  │
  │ 只有 id==0           │        │ 每个都有                │
  └──────────┬───────────┘        └───────────┬────────────┘
             │ old_wd_data（全局）             │ cdev → container_of
             └──────────────┬─────────────────┘
                            ▼
        ┌──────────────────────────────────────────────┐
        │ struct watchdog_core_data （11 字段，私有）    │
        │  dev（真正的 struct device，在 watchdog_class）│
        │  cdev · mutex lock                            │
        │  last_keepalive ┐                             │
        │  last_hw_keepalive ├ 三个 ktime 记账        │
        │  open_deadline  ┘                             │
        │  hrtimer timer + kthread_work work            │
        │  status → _WDOG_DEV_OPEN/_ALLOW_RELEASE/_KEEPALIVE│
        │  wdd ──────────┐                              │
        └────────────────│──────────────────────────────┘
                         ▼
        ┌──────────────────────────────────────────────┐
        │ struct watchdog_device （20 字段，驱动填）      │
        │  id · parent · groups                         │
        │  info ─────────► struct watchdog_info（uapi）  │
        │  ops  ─────────► struct watchdog_ops（10）─────┼──► 驱动实现
        │  gov  ─────────► struct watchdog_governor（2）─┼──► governor 模块
        │  timeout/pretimeout/min_/max_（秒）            │
        │  min_hw_heartbeat_ms/max_hw_heartbeat_ms（毫秒）│
        │  reboot_nb · restart_nb · pm_nb（内嵌）         │
        │  driver_data ──► 驱动私有                      │
        │  wd_data ──────► 回指上面那个                   │
        │  status → 6 个 WDOG_* 位                       │
        │  deferred ─────► wtd_deferred_reg_list         │
        └──────────────────────────────────────────────┘

  旁路：driver 中断 → watchdog_notify_pretimeout(wdd)
                          → 查 pretimeout_list（spinlock）
                          → wdd->gov->pretimeout(wdd)  ⇒ noop 或 panic
```

## §8 阅读下一篇

| 想知道 | 去 |
|---|---|
| API 完整清单（含 11 个 ioctl） | `01-api.md` |
| 注册 / 打开 / 喂狗 / magic close 状态机 | `03-internals.md` |
| pretimeout governor 与软 pretimeout | `04-extensions.md` |
| 驱动骨架 + 用户态骨架 | `05-usage.md` |
| DT 属性 | `06-devicetree.md` |
| 12 个 sysfs 属性 | `07-debug.md` |
| 为什么两个"在跑"、为什么内核代喂 | `08-feynman.md` |
