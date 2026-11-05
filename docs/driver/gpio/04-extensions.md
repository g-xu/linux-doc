---
title: 扩展机制
---

# 04 · gpio 子系统的扩展框架

## §0 本篇怎么读

### §0.1 组织轴声明

**本篇只承诺一条组织轴：一节一个独立框架，各节之间互不调用。**

这一点必须先说清楚，因为它决定了本篇能怎么用：

- 你可以**只读你关心的那一节**，跳过其余全部，不会丢上下文。
- 各节内部的顺序不是时间线（时间线在 `03-internals.md` Part A），也不是分层（分层在 `00-overview.md` §3）。
- 唯一的例外是 §2（cdev v1）——它和 §1（cdev v2）住在同一个 `.c` 文件里，共享同一份 `struct gpio_device`，所以 §2 会反复回指 §1。这个例外在 §2 开头会再声明一次。

「互不调用」是可验证的断言，不是修辞。核心事实是：**这些框架彼此之间没有函数调用关系，它们各自单向依赖 `gpiolib.c` 的核心 API。** 唯一的跨框架调用是 `gpio_request_one()` 调 `gpiod_export()`（`drivers/gpio/gpiolib-legacy.c:55`），即 §5 调 §3 —— 这条边会在 §5 里点名。

### §0.2 分诊表：arm64 上你真会碰到哪几个

| # | 框架 | 文件 | 行数 | arm64 上会碰到吗 | 判据 |
|---|------|------|------|------------------|------|
| §1 | 字符设备 v2 ABI | `gpiolib-cdev.c` | 2906 | **一定会** | `CONFIG_GPIO_CDEV` 是 `default y`（`drivers/gpio/Kconfig:75`），且 libgpiod / gpioset / gpiomon 全部走它 |
| §2 | 字符设备 v1 ABI | 同一文件的 `#ifdef CONFIG_GPIO_CDEV_V1` | — | **很可能** | `CONFIG_GPIO_CDEV_V1` 也是 `default y`（`Kconfig:88`），老工具仍在用 |
| §3 | legacy sysfs | `gpiolib-sysfs.c` | 820 | 可能，但**已废弃** | Kconfig 帮助文字直接写「This ABI is deprecated」（`Kconfig:68`） |
| §4 | devres 包装 | `gpiolib-devres.c` | 484 | **一定会** | 无条件编入 `CONFIG_GPIOLIB`（`drivers/gpio/Makefile:7`），几乎所有消费者驱动都用 `devm_gpiod_get()` |
| §5 | 整数 legacy API | `gpiolib-legacy.c` | 113 | 少数老驱动会 | 无条件编入（`Makefile:8`），但新代码不该用 |
| §6 | bgpio / 通用 MMIO 库 | `gpio-mmio.c` | 822 | **很可能** | 全树 38 个 `.c` 调 `bgpio_init()`（见 §6.1 的复现命令），其中包含多个 arm64 SoC 的 GPIO 驱动 |
| §7 | gpio-regmap 垫片 | `gpio-regmap.c` | 342 | 少见 | 全树只有 3 个调用点（见 §7.4） |
| §8 | 虚拟提供方（aggregator / sim / mockup） | 550 / 1613 / 614 | 2777 | 只在测试环境 | 三者都是 `tristate` 且默认不选 |
| §9 | DT 集成 | `gpiolib-of.c` | 1032 | **一定会** | arm64 上设备树是唯一的固件描述来源；细节在 `06-devicetree.md` |
| §10 | ACPI 集成 | `gpiolib-acpi.c` | 1686 | **本文档集显式裁掉** | 见 §10 |

「一定会」= arm64 defconfig 或近似配置下必然编入且必然被走到；「很可能」= 取决于你的 SoC 驱动或用户态工具；「少见」= 需要特定硬件。

### §0.3 每个框架挂在 03 时间线的哪一点

`03-internals.md` Part A 的十三段注册时间线是所有框架的公共骨架。这张表把本篇每一节钉到那条时间线上：

| # | 框架 | 挂载点（03 Part A 的哪一段 / 哪个函数） | 挂载方式 |
|---|------|------------------------------------------|----------|
| §1 | cdev v2 | §A2 第 13 段 `gpiochip_setup_dev()` → `gpiolib_cdev_register()`（`gpiolib-cdev.c:2885`） | 核心主动调用 |
| §2 | cdev v1 | 同上，同一个 `cdev_init(&gdev->chrdev, &gpio_fileops)` | 编译期 `#ifdef` 分叉，无独立挂载点 |
| §3 | legacy sysfs | 两处：§A2 第 13 段 `gpiochip_sysfs_register()`；以及 §A1.2 的 `postcore_initcall(gpiolib_sysfs_init)` 补做 | 核心主动调用 + initcall 回扫 |
| §4 | devres | **没有挂载点** | 纯消费者侧包装，位于 §A4「三路查找」之外的一层 |
| §5 | 整数 legacy API | **没有挂载点** | 消费者侧薄封装，落到 §A4.3 `gpiod_request_commit()` |
| §6 | bgpio | 在 §A2 第 1 段**之前**——由提供方驱动先调 `bgpio_init()` 填好 `gpio_chip`，再调 `gpiochip_add_data()` | 提供方侧预填充 |
| §7 | gpio-regmap | 同 §6，且额外在 `gpiochip_add_data()` **之后**调 `gpiochip_irqchip_add_domain()`（`gpio-regmap.c:279`） | 提供方侧预填充 + 后置 irqdomain |
| §8 | 虚拟提供方 | 与真实提供方完全一样走 §A2 十三段 | 提供方 |
| §9 | gpiolib-of | §A2 第 10 段 `of_gpiochip_add()`；消费者侧 §A4.1 `of_find_gpio()` | 核心主动调用（两处） |

---

## §1 字符设备 v2 ABI（`gpiolib-cdev.c`）

### §1.1 它是什么，为什么存在

`/dev/gpiochipN` 是**当前唯一被推荐的用户态 GPIO 接口**。Kconfig 里的措辞很直白：

> Say Y here to add the character device /dev/gpiochipN interface for GPIOs. The character device allows userspace to control GPIOs using ioctl() operations.
> Only say N if you are sure that the GPIO character device is not required.
> — `drivers/gpio/Kconfig:77-82`

它存在的理由，在 §3 的 sysfs 帮助文字里给出了反面表述：

> This ABI is deprecated. If you want to use GPIO from userspace, use the character device /dev/gpiochipN with the appropriate ioctl() operations instead.
> — `drivers/gpio/Kconfig:68-70`

sysfs 的三个硬伤（每行一个 GPIO、无法原子批量、拿不到边沿时间戳）都被 cdev v2 一次性解决。

### §1.2 设备节点是怎么出现的

挂载点在 03 Part A 第 13 段。三行关键代码（`gpiolib-cdev.c:2885` 起的 `gpiolib_cdev_register()`）：

```c
cdev_init(&gdev->chrdev, &gpio_fileops);
gdev->dev.devt = MKDEV(MAJOR(devt), gdev->id);
... cdev_device_add(&gdev->chrdev, &gdev->dev) ...
chip_dbg(gdev->chip, "added GPIO chardev (%d:%d)\n", MAJOR(devt), gdev->id);
```

注意 minor 号是 `gdev->id`——也就是 03 §A2 第 3 段里 `ida_alloc()` 拿到的那个 id，因此 `/dev/gpiochipN` 的 N 与 `gpiochipN` 这个设备名里的 N 是**同一个数**，而它与 `gdev->base`（全局编号空间起点）**没有关系**。这是新手最容易混的一对数字。

注销侧只有一行：`cdev_device_del()`（`gpiolib-cdev.c:2903`）。

### §1.3 四组 `file_operations`：一个 ABI 里其实有四种 fd

这是理解整个 cdev 的钥匙。**同一个 ABI 里存在四种语义不同的文件描述符**，各有一组 `file_operations`：

| `file_operations` | 位置 | fd 从哪来 | 有 read/poll 吗 | 属于 |
|---|---|---|---|---|
| `gpio_fileops` | `gpiolib-cdev.c:2872` | `open("/dev/gpiochipN")` | 有（`lineinfo_watch_poll` / `lineinfo_watch_read`） | v1 + v2 公共 |
| `line_fileops` | `:1753` | `GPIO_V2_GET_LINE_IOCTL` | 有（线事件） | **v2** |
| `linehandle_fileops` | `:340` | `GPIO_GET_LINEHANDLE_IOCTL` | **没有** | v1（见 §2.3） |
| `lineevent_fileops` | `:2125` | `GPIO_GET_LINEEVENT_IOCTL` | 有 | v1 |

「`linehandle_fileops` 没有 read/poll」不是遗漏，而是 v1 的设计缺陷：v1 把「读写值」和「等事件」拆成了两种互不相容的 fd，你不能对一个 line handle 既 set 值又 poll 边沿。v2 用一个 `line_fileops` 同时提供两者，这是 v2 最实质的改进。

### §1.4 ioctl 号总表

全部在 `include/uapi/linux/gpio.h`，magic 都是 `0xB4`：

| ioctl | 号 | 行 | 方向 | 版本 |
|---|---|---|---|---|
| `GPIO_GET_CHIPINFO_IOCTL` | 0x01 | `:505` | `_IOR` | 公共 |
| `GPIO_GET_LINEINFO_UNWATCH_IOCTL` | 0x0C | `:506` | `_IOWR` | 公共 |
| `GPIO_V2_GET_LINEINFO_IOCTL` | 0x05 | `:511` | `_IOWR` | v2 |
| `GPIO_V2_GET_LINEINFO_WATCH_IOCTL` | 0x06 | `:512` | `_IOWR` | v2 |
| `GPIO_V2_GET_LINE_IOCTL` | 0x07 | `:513` | `_IOWR` | v2 |
| `GPIO_V2_LINE_SET_CONFIG_IOCTL` | 0x0D | `:514` | `_IOWR` | v2 |
| `GPIO_V2_LINE_GET_VALUES_IOCTL` | 0x0E | `:515` | `_IOWR` | v2 |
| `GPIO_V2_LINE_SET_VALUES_IOCTL` | 0x0F | `:516` | `_IOWR` | v2 |
| `GPIO_GET_LINEINFO_IOCTL` | 0x02 | `:523` | `_IOWR` | v1 |
| `GPIO_GET_LINEHANDLE_IOCTL` | 0x03 | `:524` | `_IOWR` | v1 |
| `GPIO_GET_LINEEVENT_IOCTL` | 0x04 | `:525` | `_IOWR` | v1 |
| `GPIOHANDLE_GET_LINE_VALUES_IOCTL` | 0x08 | `:526` | `_IOWR` | v1 |
| `GPIOHANDLE_SET_LINE_VALUES_IOCTL` | 0x09 | `:527` | `_IOWR` | v1 |
| `GPIOHANDLE_SET_CONFIG_IOCTL` | 0x0A | `:528` | `_IOWR` | v1 |
| `GPIO_GET_LINEINFO_WATCH_IOCTL` | 0x0B | `:529` | `_IOWR` | v1 |

号段是**交错**的：0x0B/0x0C 夹在 v1 中间，0x0D–0x0F 在 v2 末尾。这说明号段是按**添加时间**而不是按版本分配的——`GPIO_GET_LINEINFO_UNWATCH_IOCTL`（0x0C）被两个版本共用，正好落在 v1 的号段里。想按号段猜版本会猜错。

### §1.5 v2 的容量常量与标志位

| 常量 | 值 | 位置 | 源码注释里的理由 |
|---|---|---|---|
| `GPIO_MAX_NAME_SIZE` | 32 | `include/uapi/linux/gpio.h:23` | 「Must be a multiple of 8 to ensure 32/64-bit alignment of structs」 |
| `GPIO_V2_LINES_MAX` | 64 | `:45` | 「Must be no greater than 64, as bitmaps are restricted here to 64-bits for simplicity」 |
| `GPIO_V2_LINE_NUM_ATTRS_MAX` | 10 | `:51` | 无注释 |

`GPIO_V2_LINES_MAX` = 64 是**硬上界，不是可调项**。原因写在注释里：`struct gpio_v2_line_values`（`:96`）用 `__aligned_u64 bits` 和 `mask` 两个 64 位位图表达「哪些线要读/写」和「值是多少」。想一次操作 65 根线，ABI 层面就做不到，必须开两个 fd。

`enum gpio_v2_line_flag` 共 13 个标志（`:72-86`）：`USED`、`ACTIVE_LOW`、`INPUT`、`OUTPUT`、`EDGE_RISING`、`EDGE_FALLING`、`OPEN_DRAIN`、`OPEN_SOURCE`、`BIAS_PULL_UP`、`BIAS_PULL_DOWN`、`BIAS_DISABLED`、`EVENT_CLOCK_REALTIME`、`EVENT_CLOCK_HTE`。

`enum gpio_v2_line_attr_id`（`:107-111`）只有 3 个：`FLAGS` = 1、`OUTPUT_VALUES` = 2、`DEBOUNCE` = 3。这解释了为什么 v2 能「按线单独设 debounce」而 v1 不能——v1 的 ABI 里根本没有承载这个参数的地方。

### §1.6 三个 `EVENT_CLOCK` 与 HTE

`EVENT_CLOCK_REALTIME` 和 `EVENT_CLOCK_HTE` 是两个标志，加上「都不设」这个默认，一共三种时间戳来源：

| 设置 | 时间戳来源 | 打的位置 |
|---|---|---|
| 都不设（默认） | `CLOCK_MONOTONIC` | 软件，硬中断上下文 |
| `EVENT_CLOCK_REALTIME` | `CLOCK_REALTIME` | 软件，硬中断上下文 |
| `EVENT_CLOCK_HTE` | 硬件时间戳引擎 | 硬件，由 HTE 子系统回调送上来 |

HTE 路径完全独立于普通中断路径：`hte_edge_setup()`（`gpiolib-cdev.c:861`）用 `hte_init_line_attr()`（`:878`）+ `hte_ts_get()`（`:881`）+ `hte_request_ts_ns()`（`:885`）挂上 `process_hw_ts`（`:826`，硬件上下文）和 `process_hw_ts_thread`（`:778`，线程上下文）这一对回调；释放侧是 `hte_ts_put()`（`:1140`）。

`CONFIG_HTE` 关掉时，`hte_edge_setup()` 有一个**空实现存根**（`:891`）。也就是说用户态设了 `EVENT_CLOCK_HTE` 但内核没编 HTE，行为由那个存根决定，而不是报「标志不支持」——这是一个容易踩的静默降级点，验证方法见 `07-debug.md`。

### §1.7 边沿事件的四段式：硬中断 / 线程 / 去抖 / kfifo

这是 cdev v2 最精巧的一段，`03-internals.md` §A6.2 讲了它在时间线上的位置，这里讲它的内部分工。

```
     PAD 电平跳变
          │
          ▼
   ┌──────────────────────────┐
   │ edge_irq_handler()  :943 │  硬中断上下文
   │  只做一件事：记时间戳     │  ← /* Just store the timestamp in hardirq
   └──────────┬───────────────┘     context so we get it as close in time
              │ IRQ_WAKE_THREAD     as possible to the actual event. */
              ▼
   ┌──────────────────────────┐
   │ edge_irq_thread()   :897 │  线程上下文
   │  组包 + 塞 kfifo + 唤醒   │
   └──────────────────────────┘

   ── 开了去抖的另一条路 ──

   ┌──────────────────────────────┐
   │ debounce_irq_handler()   :980│  硬中断，只 mod_delayed_work
   └──────────┬───────────────────┘
              ▼
   ┌──────────────────────────────┐
   │ debounce_work_func()     :990│  system_wq 上的 work
   │  读值 → 与上次比 → 决定是否发 │
   └──────────────────────────────┘
              ▲
              │ 读值用
   ┌──────────┴───────────────────┐
   │ debounced_value()        :961│
   └──────────────────────────────┘
```

三条源码注释把设计意图讲得比任何转述都清楚：

1. `/* Do not leak kernel stack to userspace */` —— 组包前必须清零整个事件结构，否则内核栈内容会经 kfifo 泄给用户态。
2. `/* We may be running from a nested threaded interrupt in which case we didn't get the timestamp from edge_irq_handler(). */` —— 嵌套线程化中断（03 §B4 的 nested-threaded 那一类 irqchip）不会走 `edge_irq_handler()`，所以时间戳可能是空的，线程里要补打。
3. `/* minor race - debouncer may be stopped here, so edge_detector_stop() must leave the value unchanged so the following will read the level from when the debouncer was last running. */` —— 这条竞态是**已知且被接受**的，代价转移成了对 `edge_detector_stop()` 的一条隐式契约：它**不许**改那个缓存值。

去抖侧的核心一行：

```c
mod_delayed_work(system_wq, &line->work,
                 usecs_to_jiffies(READ_ONCE(line->debounce_period_us)));
return IRQ_HANDLED;
```

用的是 `system_wq`，不是专用 workqueue，也不是 `system_highpri_wq`。这意味着**去抖精度受全局 workqueue 负载影响**，`debounce_period_us` 是下界而不是保证值。读值失败时打 `pr_debug_ratelimited("debouncer failed to read line value\n")`——注意是 `pr_debug` 级，默认不可见，见 `07-debug.md` 的日志可见性表。

### §1.8 热拔安全：三个 `call_*_locked()` 垫片

用户态可以持着 fd 不放，而底下的 gpiochip 可以被卸载。cdev 用一把 `gdev->sem` 读写锁把所有用户态入口包起来：

| 垫片 | 位置 | 包住的 | 对应 typedef |
|---|---|---|---|
| `call_poll_locked()` | `gpiolib-cdev.c:67` | 所有 `.poll` | `poll_fn`（`:62-65`） |
| `call_ioctl_locked()` | `:80` | 所有 `.unlocked_ioctl` / `.compat_ioctl` | `ioctl_fn` |
| `call_read_locked()` | `:93` | 所有 `.read` | `read_fn` |

三者结构完全一样：`down_read(&gdev->sem)` → 调真正的实现 → `up_read()`。写侧在 `gpiochip_remove()`（03 §A7）里拿 `down_write()`。

第二道门在 `gpio_ioctl_unlocked()`（`:2587`）的第一行：

```c
/* We fail any subsequent ioctl():s when the chip is gone */
if (!gdev->chip)
    return -ENODEV;
```

两道门的分工要分清：**rwsem 保证 chip 不会在 ioctl 执行到一半时消失；`!gdev->chip` 检查保证 chip 已经消失后的 ioctl 干净失败**。少任何一道都不够。`default: return -EINVAL;` 兜住未知 ioctl 号。

---

## §2 字符设备 v1 ABI（`CONFIG_GPIO_CDEV_V1`）

**本节是 §0.1 声明的唯一例外**：v1 和 v2 住在同一个 `gpiolib-cdev.c` 里，共享 `gpio_fileops` 和 `struct gpio_device`，所以本节会反复回指 §1。

### §2.1 它凭什么还活着

```
config GPIO_CDEV_V1
	bool "Support GPIO ABI Version 1"
	default y
	depends on GPIO_CDEV
	help
	  Say Y here to support version 1 of the GPIO CDEV ABI.

	  This ABI version is deprecated.
	  Please use the latest ABI for new developments.

	  If unsure, say Y.
	  — drivers/gpio/Kconfig:86-96
```

注意这个组合：**「deprecated」+「default y」+「If unsure, say Y」**。这不矛盾——v1 不能删是因为已发行的用户态二进制还在用它；`default y` 是为了不破坏这些二进制。你自己写新代码时按「deprecated」办，配置内核时按 `default y` 办。

### §2.2 v1 的容量与标志

| 项 | 值 / 数量 | 位置 |
|---|---|---|
| `GPIOLINE_FLAG_*` | 8 个 | `include/uapi/linux/gpio.h:311-318` |
| `GPIOHANDLES_MAX` | 64 | `:343` |
| `GPIOHANDLE_REQUEST_*` | 8 个 | `:378-385` |
| `GPIOEVENT_REQUEST_*` | 3 个 | `:454-456` |
| `GPIOEVENT_EVENT_RISING_EDGE` | 0x01 | `:486` |
| `GPIOEVENT_EVENT_FALLING_EDGE` | 0x02 | `:487` |

对照 §1.5：v1 是 8 个标志、v2 是 13 个。差出来的 5 个正好是 v2 新增能力（三种 bias 的显式表达 + 两种 event clock）。`GPIOHANDLES_MAX` = 64 与 `GPIO_V2_LINES_MAX` = 64 数值相同，但理由不同——v1 是数组长度上限，v2 是位图宽度上限。

### §2.3 v1 的结构性缺陷：三种 fd 拆开了

§1.3 已经点过：v1 把一个 fd 该干的事拆成了两个。

| 想做的事 | v1 要哪个 fd | v2 要哪个 fd |
|---|---|---|
| 读/写值 | `GPIO_GET_LINEHANDLE_IOCTL` → `linehandle_fileops`（无 read/poll） | `GPIO_V2_GET_LINE_IOCTL` → `line_fileops` |
| 等边沿 | `GPIO_GET_LINEEVENT_IOCTL` → `lineevent_fileops`（每次只能一根线） | 同上，同一个 fd |
| 两件都做 | **做不到** | 同一个 fd |

「`lineevent` 每次只能一根线」是 v1 的第二个硬伤：`GPIO_GET_LINEEVENT_IOCTL` 的请求结构只带一个 offset。监听 16 根线要开 16 个 fd、poll 16 个 fd。v2 的一个 `line_fileops` fd 可以同时监听最多 64 根。

### §2.4 v1 的标志校验：五条拒绝规则

`gpiolib-cdev.c:125-181` 定义了 `GPIOHANDLE_REQUEST_VALID_FLAGS`（8 个标志的或）和 `GPIOHANDLE_REQUEST_DIRECTION_FLAGS`，然后按五条规则拒绝非法组合：

| # | 拒绝什么 | 为什么 |
|---|---|---|
| 1 | 出现了 `VALID_FLAGS` 之外的位 | 未来标志位不能被老内核默默吃掉 |
| 2 | `INPUT` 和 `OUTPUT` 同时设 | 方向唯一 |
| 3 | `OPEN_DRAIN` 和 `OPEN_SOURCE` 同时设 | 电气上互斥 |
| 4 | 设了 OD/OS 但没设 `OUTPUT` | OD/OS 只对输出有意义 |
| 5 | 设了 bias 但既没 `INPUT` 也没 `OUTPUT`；或两个 bias 同时设 | 与核心 `gpiod_configure_flags()` 的三路 bias 互斥（03 §A4.4）对齐 |

规则 1 是**前向兼容的关键**：它让「老内核 + 新工具」失败得干净（`-EINVAL`），而不是把新标志当 0 处理然后配出一个用户没要求的状态。这条规则在 v2 里同样存在。

---

## §3 legacy sysfs（`gpiolib-sysfs.c`，已废弃）

### §3.1 定位

`CONFIG_GPIO_SYSFS`（`drivers/gpio/Kconfig:61`）提供 `/sys/class/gpio/`。三件事要先知道：

1. **它是 `if EXPERT` 的**——普通配置界面里看不到，得先开 `CONFIG_EXPERT`。
2. **它 `select GPIO_CDEV`**，Kconfig 里的理由写得很坦白：`select GPIO_CDEV # We need to encourage the new ABI`（`Kconfig:64`）。也就是说**开 sysfs 会强制把 cdev 一起编进来**，反过来不成立。
3. 帮助文字第一句就是「Say Y here to add the **legacy** sysfs interface」。

### §3.2 三层属性

sysfs 接口有三层，各自一组属性：

| 层 | 属性数组 | 位置 | 属性 |
|---|---|---|---|
| class 级 | `gpio_class_attrs[]` | `gpiolib-sysfs.c:520-524` | `export`（`CLASS_ATTR_WO`，`:482`）、`unexport`（`CLASS_ATTR_WO`，`:518`） |
| chip 级 | `gpiochip_attrs[]` | `:415-420` | `base`（`:395`）、`label`（`:404`）、`ngpio`（`:413`） |
| line 级 | `gpio_attrs[]` | `:363-369` | `direction`（`DEVICE_ATTR_RW`，`:99`）、`edge`（`DEVICE_ATTR_RW`，`:279`）、`value`（`DEVICE_ATTR_PREALLOC`，`:143`）、`active_low`（`DEVICE_ATTR_RW`，`:339`） |

class 本体：

```c
static struct class gpio_class = {
	.name =		"gpio",
	.owner =	THIS_MODULE,
	.class_groups = gpio_class_groups,
};
	— gpiolib-sysfs.c:527-532
```

两个细节值得记：

- **`value` 用的是 `DEVICE_ATTR_PREALLOC`，另外三个用 `DEVICE_ATTR_RW`。** `PREALLOC` 变体预分配读缓冲，为的是让 `poll()` 在 `value` 上可用而不必在 poll 路径里分配内存。这是「sysfs 也能等边沿」的实现基础，也是它唯一比整数 API 强的地方。
- **line 级属性组带 `.is_visible = gpio_is_visible`（`:371-374`）。** 属性是否出现取决于运行期状态（比如输入线不显示某些项），所以 `ls` 出来的属性集在不同线上可能不同。

### §3.3 `gpiod_export()` 的四道门

`gpiod_export()`（`:550-633`）在真正干活之前要过四道检查，顺序和返回码都有讲究：

| # | 检查 | 失败返回 | 诊断输出 |
|---|---|---|---|
| 1 | `!gpio_class.p` —— class 还没注册 | `-ENOENT` | `pr_debug("%s: called too early!\n", __func__)` |
| 2 | `!desc` | `-EINVAL` | 无 |
| 3 | `!chip \|\| !gdev->mockdev`（持 `sysfs_lock`） | `-ENODEV` | 无 |
| 4 | `!FLAG_REQUESTED \|\| FLAG_EXPORT`（持 `gpio_lock`） | `-EPERM` | `gpiod_dbg(desc, "%s: unavailable (requested=%d, exported=%d)\n", ...)` |

第 1 道门的 `pr_debug("called too early!")` 是一个**默认不可见的报错**。`gpio_request_one(..., GPIOF_EXPORT, ...)` 在 `postcore_initcall` 之前被调用时，导出会静默失败——调用者只看到 `-ENOENT`，没有任何 dmesg 输出。这正是 03 §A1.2 讲的 `gpiolib_sysfs_init()` 要回扫已注册 chip 的原因，但那个回扫只补 chip 级注册，**不补 line 级导出**。

第 4 道门要求 `FLAG_REQUESTED` 已置位：**你不能导出一根没被内核里任何人请求过的线**。这与直觉相反——很多人以为 sysfs 的 `export` 就是「从用户态请求一根线」。实际路径是 `export_store()` 内部先做请求再导出；直接调 `gpiod_export()` 的内核代码必须自己先请求。

### §3.4 为什么不要在新代码里用它

四条，每条都有代码依据：

1. **一行一个 GPIO，无法原子批量。** 对比 §1.5 的 64 位位图。
2. **全局编号空间。** sysfs 路径里的数字是 `gdev->base + offset`，而 `base` 是动态分配的（03 §C3 列了它的五个依赖方）。同一块板换个 probe 顺序，`/sys/class/gpio/gpio42` 就变成了另一根线。
3. **拿不到时间戳。** `edge` 属性能让 `poll()` 返回，但你只知道「发生过」，不知道「什么时候」。
4. **没有 owner 追踪。** 进程死了，导出还在。cdev 的 fd 关闭即释放。

---

## §4 devres 包装（`gpiolib-devres.c`）

### §4.1 定位：它不是一层抽象，是一层「记账」

`gpiolib-devres.c` 只有 484 行，做的事情单一：**把 `gpiod_get*` / `gpio_request*` 的结果登记到 `dev` 的 devres 链上，让驱动 remove 时自动释放。** 它不改变任何 GPIO 语义，不引入新概念。

因此它**没有挂载点**（§0.3）：它既不注册什么，也不被核心回调。它是纯消费者侧的一层薄封装，位置在 03 §A4「三路查找」的**上游**——`devm_gpiod_get()` 先调 `gpiod_get()`，成功了再登记。

### §4.2 14 个导出符号

`gpiolib-devres.c` 共 14 个 `EXPORT_SYMBOL_GPL`，可以分成四组：

| 组 | API | 声明行 / 导出行 |
|---|---|---|
| **单根线获取** | `devm_gpiod_get` | `:56` / `:62` |
| | `devm_gpiod_get_optional` | `:74` / `:80` |
| | `devm_gpiod_get_index` | `:93` / `:130` |
| | `devm_gpiod_get_index_optional` | `:241` / `:254` |
| | `devm_gpiod_get_from_of_node` | `:147` / `:185` |
| | `devm_fwnode_gpiod_get_index` | `:202` / `:227` |
| **数组获取** | `devm_gpiod_get_array` | `:266` / `:289` |
| | `devm_gpiod_get_array_optional` | `:303` / `:314` |
| **释放 / 脱管** | `devm_gpiod_put` | `:325` / `:330` |
| | `devm_gpiod_put_array` | `:371` / `:376` |
| | `devm_gpiod_unhinge` | `:342` / `:360` |
| **整数 API + 提供方** | `devm_gpio_request` | `:396` / `:416` |
| | `devm_gpio_request_one` | `:425` / `:446` |
| | `devm_gpiochip_add_data_with_key` | `:472` / `:484` |

内部辅助（不导出）：`devm_gpiod_release` `:18`、`devm_gpiod_match` `:25`、`devm_gpiod_release_array` `:32`、`devm_gpiod_match_array` `:39`、`devm_gpio_release` `:378`、`devm_gpio_chip_release` `:448`。

注意第四组混了两类东西：`devm_gpio_request*` 是**整数 legacy API**（§5）的 devm 版本，而 `devm_gpiochip_add_data_with_key` 是**提供方侧**的。也就是说这个文件同时服务消费者和提供方，只是提供方那边只有一个函数。

### §4.3 `devm_gpiochip_add_data_with_key()`：提供方唯一的 devm 入口

```c
int devm_gpiochip_add_data_with_key(struct device *dev, struct gpio_chip *gc,
                                    void *data, struct lock_class_key *lock_key,
                                    struct lock_class_key *request_key)
{
	int ret;

	ret = gpiochip_add_data_with_key(gc, data, lock_key, request_key);
	if (ret < 0)
		return ret;

	return devm_add_action_or_reset(dev, devm_gpio_chip_release, gc);
}
	— gpiolib-devres.c:472-484
```

用 `devm_add_action_or_reset()` 而不是 `devm_add_action()`：如果登记动作本身失败（分配 devres 节点失败），它会**立即调用一次 release**，把刚加进去的 gpiochip 摘掉。这避免了「chip 已注册但没人负责注销」的泄漏。提供方驱动只要用这个函数，`remove()` 里就不用写 `gpiochip_remove()`。

### §4.4 `devm_gpiod_unhinge()`：把一根线从 devres 里摘出来

```c
void devm_gpiod_unhinge(struct device *dev, struct gpio_desc *desc)
{
	int ret;

	if (IS_ERR_OR_NULL(desc))
		return;
	ret = devres_destroy(dev, devm_gpiod_release,
			     devm_gpiod_match, &desc);
	/*
	 * If the GPIO descriptor is requested as nonexclusive, we
	 * may call this function several times on the same descriptor
	 * so it is OK if devres_destroy() returns -ENOENT.
	 */
	if (ret == -ENOENT)
		return;
	/* Anything else we should warn about */
	WARN_ON(ret);
}
	— gpiolib-devres.c:342-360
```

这个函数的存在本身就是一条设计债的凭证。它用 `devres_destroy()`（销毁登记但**不**执行 release）把描述符的生命周期交回调用者，典型用途是把一根线交给一个活得比本设备长的对象。而那段注释里的 `-ENOENT` 容忍，直接指向 03 §C4 讲的 `GPIOD_FLAGS_BIT_NONEXCLUSIVE` hack——**同一根线可以被多个消费者拿到，于是同一个描述符可能被 unhinge 多次**。换个说法：这里的 `-ENOENT` 不是错误处理，是给一个上游 hack 打的补丁。

---

## §5 整数 legacy API（`gpiolib-legacy.c`）

### §5.1 只有 113 行，5 个导出

| API | 定义行 | 导出行 | 做什么 |
|---|---|---|---|
| `gpio_free` | `:9` | `:13` | `gpiod_free(gpio_to_desc(gpio))` |
| `gpio_request_one` | `:21` | `:66` | 请求 + 按 `GPIOF_*` 标志配置 + 可选导出 |
| `gpio_request` | `:68` | `:78` | 只请求，不配置 |
| `gpio_request_array` | `:85` | `:101` | 循环调 `gpio_request_one` |
| `gpio_free_array` | `:108` | `:113` | 循环调 `gpio_free` |

**`gpio_request()` 不是 `gpio_request_one(gpio, 0, label)` 的包装。** 两者是各自独立的函数（`:68` 和 `:21`），行为差别很实质：`gpio_request()` 只做 `gpiod_request()`，**完全不碰方向**；`gpio_request_one(gpio, 0, label)` 会因为 `flags` 里没有 `GPIOF_DIR_IN` 而走到 `gpiod_direction_output_raw(desc, 0)`，把线**设成输出低**。把前者当后者用（或反过来）会得到一个你没要求的方向。

### §5.2 `gpio_request_one()` 的处理次序

```c
	desc = gpio_to_desc(gpio);

	/* Compatibility: assume unavailable "valid" GPIOs will appear later */
	if (!desc && gpio_is_valid(gpio))
		return -EPROBE_DEFER;

	err = gpiod_request(desc, label);
	if (err)
		return err;

	if (flags & GPIOF_OPEN_DRAIN)  set_bit(FLAG_OPEN_DRAIN,  &desc->flags);
	if (flags & GPIOF_OPEN_SOURCE) set_bit(FLAG_OPEN_SOURCE, &desc->flags);
	if (flags & GPIOF_ACTIVE_LOW)  set_bit(FLAG_ACTIVE_LOW,  &desc->flags);

	if (flags & GPIOF_DIR_IN)
		err = gpiod_direction_input(desc);
	else
		err = gpiod_direction_output_raw(desc,
				(flags & GPIOF_INIT_HIGH) ? 1 : 0);

	if (err)
		goto free_gpio;

	if (flags & GPIOF_EXPORT) {
		err = gpiod_export(desc, flags & GPIOF_EXPORT_CHANGEABLE);
		if (err)
			goto free_gpio;
	}
	— gpiolib-legacy.c:26-59
```

四个要点：

1. **`-EPROBE_DEFER` 的那条兼容路**：`gpio_to_desc()` 返回 NULL 但编号「看起来合法」时，假定对应的 chip 还没注册，让调用者稍后重试。注释原文：`/* Compatibility: assume unavailable "valid" GPIOs will appear later */`。这是整数 API 里唯一的探测延后机制，描述符 API 侧的对应物在 03 §A4.1（`of_find_gpiochip_by_xlate()` 返回 NULL → `-EPROBE_DEFER`）。
2. **三个标志是 `set_bit()` 直接写的，绕过了 `gpiod_configure_flags()`**（03 §A4.4）。后果：**整数 API 拿不到 bias 配置，也不做 OD/OS 与 bias 的互斥校验**。你可以在这里同时设 OD 和 OS，核心不会拦。
3. **用的是 `gpiod_direction_output_raw()`，不是 `gpiod_direction_output()`。** 也就是说 `GPIOF_INIT_HIGH` 里的 "HIGH" 指的是**寄存器值**而不是逻辑值——即使前面刚设了 `GPIOF_ACTIVE_LOW`，初值也不会被反相。整数 API 的整个值语义都是 raw 的（02/03 讲的四层值语义里，它只活在第 3 层），这一行是最硬的证据。
4. **`GPIOF_EXPORT` 是 §5 → §3 的那条唯一跨框架边**（§0.1）。`gpiod_export()` 失败会导致整个请求回滚（`goto free_gpio`），而 §3.3 讲过它有一道 `pr_debug("called too early!")` 的静默门——初期调用时你会拿到一个 `-ENOENT`，且 dmesg 里什么都没有。

---

## §6 bgpio：通用 MMIO GPIO 库（`gpio-mmio.c`）

### §6.1 定位与规模

`gpio-mmio.c` 822 行，`CONFIG_GPIO_GENERIC`（`drivers/gpio/Kconfig:98`）——注意它是个**无 prompt 的 `tristate`**，只能被别的 Kconfig `select`，用户在菜单里选不到。

Makefile 里有一处容易看错的地方：

```make
obj-$(CONFIG_GPIO_GENERIC)	+= gpio-generic.o
gpio-generic-$(CONFIG_GPIO_GENERIC) += gpio-mmio.o
```
（`drivers/gpio/Makefile:16` 与 `:19`）

**模块名（`gpio-generic`）与源文件名（`gpio-mmio.c`）不同。** 找 `.ko` 时按前者，grep 代码时按后者。

它服务谁：

```
$ grep -rln 'bgpio_init(' --include=*.c drivers/ | grep -v 'gpio/gpio-mmio.c' | wc -l
38
```

38 个文件调 `bgpio_init()`，分布：`drivers/gpio/` 34 个、`drivers/pinctrl/nuvoton/` 2 个、`drivers/pinctrl/` 1 个、`drivers/mfd/` 1 个。这条分布本身是一个结论：**bgpio 的使用者绝大多数在 gpio 目录内，但确实溢出到了 pinctrl 和 mfd**——所以「只在 drivers/gpio 里 grep 就能找齐 bgpio 用户」是错的。

### §6.2 它替提供方省掉了什么

「最简 MMIO GPIO 控制器」的形状，源码开头的 ASCII 艺术注释里讲得很清楚（`gpio-mmio.c:1-42`），要点三条：

- 最简形式：只有一个 `data` 寄存器
- 稍复杂：一对 set/clear 位寄存器影响 data 寄存器
- 支持 8 / 16 / 32 / 64 位寄存器，**GPIO 根数由寄存器宽度决定**

`bgpio_init()`（`:599`）就是把这些排列组合折叠成一次调用。它内部依次做三件事：

| 步骤 | 函数 | 位置 | 决定什么 |
|---|---|---|---|
| 1 | `bgpio_setup_io()` | `:488` | 根据 `dat`/`set`/`clr` 哪些非 NULL，挑 `gc->get` / `gc->set` / `gc->set_multiple` |
| 2 | `bgpio_setup_accessors()` | `:418` | 根据寄存器宽度和字节序，挑 `gc->read_reg` / `gc->write_reg` |
| 3 | `bgpio_setup_direction()` | `:539` | 根据 `dirout`/`dirin` 哪些非 NULL，挑 `gc->direction_*` / `gc->get_direction` |

调用者只要传进寄存器地址和一组标志，`gpio_chip` 的九个回调就全部填好了。

### §6.3 七个 `BGPIOF_*` 标志

全在 `include/linux/gpio/driver.h`：

| 标志 | 值 | 行 | 含义 |
|---|---|---|---|
| `BGPIOF_BIG_ENDIAN` | BIT(0) | `:686` | 位序大端（bit 0 是最高位） |
| `BGPIOF_UNREADABLE_REG_SET` | BIT(1) | `:687` | `reg_set` 只写 |
| `BGPIOF_UNREADABLE_REG_DIR` | BIT(2) | `:688` | 方向寄存器只写 |
| `BGPIOF_BIG_ENDIAN_BYTE_ORDER` | BIT(3) | `:689` | 字节序大端 |
| `BGPIOF_READ_OUTPUT_REG_SET` | BIT(4) | `:690` | `reg_set` 存的是输出值 |
| `BGPIOF_NO_OUTPUT` | BIT(5) | `:691` | 只能输入 |
| `BGPIOF_NO_SET_ON_INPUT` | BIT(6) | `:692` | 输入态不允许写值 |

**`BGPIOF_BIG_ENDIAN` 和 `BGPIOF_BIG_ENDIAN_BYTE_ORDER` 是两回事**，名字近到必然被混。前者管**位序**（哪个 bit 对应 GPIO 0），走 `gc->be_bits = !!(flags & BGPIOF_BIG_ENDIAN)`（`gpio-mmio.c:619`）；后者管**字节序**（多字节寄存器怎么读），只传给 `bgpio_setup_accessors()`（`:625`）。一块 FPGA 可以只有其中一种大端。

### §6.4 `bgpio_init()` 会覆盖你填的四个字段

这是最容易踩的坑。`bgpio_init()` 开头无条件写了这些（`:609-619`）：

```c
	gc->bgpio_bits = sz * 8;
	...
	raw_spin_lock_init(&gc->bgpio_lock);
	gc->parent = dev;
	gc->label = dev_name(dev);
	gc->base = -1;
	gc->ngpio = gc->bgpio_bits;
	gc->request = bgpio_request;
	gc->be_bits = !!(flags & BGPIOF_BIG_ENDIAN);
```

四条后果，都是「你在调用前填了也没用」：

1. **`gc->label` 被强制成 `dev_name(dev)`**。想要自定义 label 必须在 `bgpio_init()` **之后**再赋值。
2. **`gc->base` 被强制成 -1**（动态分配）。想固定编号同样要事后覆盖。
3. **`gc->ngpio` 被强制成 `sz * 8`**。想只用寄存器的一部分位，得事后改小。
4. **`gc->request` 被强制成 `bgpio_request`**（`:564`）。这一条最隐蔽：如果你的驱动本来打算用 `gpiochip_generic_request`（03 §B6.2，用于 pinctrl 桥接），`bgpio_init()` 会把它盖掉，pinmux 联动就失效了。

两条入口校验：`sz` 必须是 2 的幂（`!is_power_of_2(sz)` → `-EINVAL`），且 `sz * 8` 不能超过 `BITS_PER_LONG`（→ `-EINVAL`）。后者意味着 **arm64 上单个 bgpio chip 最多 64 根线**。

### §6.5 方向初值探测与两个方向寄存器的同步

`bgpio_init()` 末尾（`:641-658`）有一段注释为 `/* Inspect hardware to find initial direction setting. */` 的逻辑：

```c
	if ((gc->reg_dir_out || gc->reg_dir_in) &&
	    !(flags & BGPIOF_UNREADABLE_REG_DIR)) {
		if (gc->reg_dir_out)
			gc->bgpio_dir = gc->read_reg(gc->reg_dir_out);
		else if (gc->reg_dir_in)
			gc->bgpio_dir = ~gc->read_reg(gc->reg_dir_in);
		/*
		 * If we have two direction registers, synchronise
		 * input setting to output setting, the library
		 * can not handle a line being input and output at
		 * the same time.
		 */
		if (gc->reg_dir_out && gc->reg_dir_in)
			gc->write_reg(gc->reg_dir_in, ~gc->bgpio_dir);
	}
```

最后那三行是一次**开机时的硬件写操作**：两个方向寄存器都存在时，bgpio 以 `reg_dir_out` 为准，把 `reg_dir_in` 强行改成它的按位反。注释给的理由是「the library can not handle a line being input and output at the same time」——这是**库的限制，不是硬件的限制**。如果你的硬件真的允许某根线同时是输入和输出（有些器件用它做回读），bgpio 会在 `bgpio_init()` 里就把这个状态破坏掉。

`BGPIOF_UNREADABLE_REG_DIR` 会跳过整段探测，并置 `gc->bgpio_dir_unreadable = true`（`:638-639`）。

### §6.6 附带的 platform 驱动

`CONFIG_GPIO_GENERIC_PLATFORM`（`drivers/gpio/Kconfig:296`，`select GPIO_GENERIC`）在同一文件里额外提供一个即插即用的 platform 驱动：`bgpio_pdev_probe()`（`gpio-mmio.c:724`）+ `bgpio_driver`（`:807`）+ `module_platform_driver(bgpio_driver)`（`:816`）。

它的 DT 匹配表只有三个 compatible（`bgpio_of_match[]`，`:685-689`）：`brcm,bcm6345-gpio`、`wd,mbl-gpio`、`ni,169445-nand-gpio`。**这三个都不是 arm64 平台**，所以 arm64 上 bgpio 基本只以「库」的形态被别的驱动调用，而不是以这个 platform 驱动的形态直接绑定。寄存器由 `bgpio_map()`（`:666`）按 name 取 `IORESOURCE_MEM`，尺寸不符直接 `IOMEM_ERR_PTR(-EINVAL)`。

它的平台数据是 `struct bgpio_pdata`（`include/linux/gpio/driver.h:662-666`），只有三个字段：

```c
struct bgpio_pdata {
	const char *label;
	int base;
	int ngpio;
};
```

两条路进这个结构，行为不一样：

- **DT 路**：`bgpio_parse_dt()` 现场 `devm_kzalloc` 一份（`gpio-mmio.c:701`），并把 `pdata->base = -1`（`:706`）——也就是**走动态编号分配**，DTS 里没有任何办法指定 base。这个函数有**两份定义**：`:693` 是 `CONFIG_OF` 版（`#ifdef` 在 `:684`），`:717` 是 `#else` 的空壳版，直接 `return NULL`（`:716` 是 `#else`，`:722` 是 `#endif`）。所以关掉 `CONFIG_OF` 之后这个 platform 驱动只剩平台数据一条路，且不会报任何错。
- **手填平台数据路**：全树只有 3 个文件真的手填过 `bgpio_pdata`，**一个 arm64 平台都没有**——`drivers/mfd/vexpress-sysreg.c:41`/`:47`/`:53`（三份，给 LED/MCI/FLASH 三组寄存器）、`arch/arm/mach-s3c/mach-crag6410.c:262`、`arch/arm/mach-omap1/board-ams-delta.c:179`/`:217`。

```bash
grep -rn 'bgpio_pdata' --include=*.c --include=*.h .    # 12 处，含定义与两处 parse_dt
```

---

## §7 gpio-regmap：regmap 后端的提供方垫片（`gpio-regmap.c`）

### §7.1 定位

342 行，`CONFIG_GPIO_REGMAP`（`drivers/gpio/Kconfig:102`，`select REGMAP`，同样是无 prompt 的 `tristate`）。

和 §6 的 bgpio 是**同类不同源**：bgpio 直接 `readl/writel` MMIO，regmap 版把寄存器访问委托给 regmap，于是同一套逻辑能覆盖 I2C / SPI / MMIO 后端的 GPIO 扩展器。两者不互相调用，一个驱动只会选其中之一。

### §7.2 配置结构：`struct gpio_regmap_config`

`include/linux/gpio/regmap.h:63-86`，23 个字段，关键的是五个寄存器基址 + 两个几何参数：

| 字段 | 行 | 作用 |
|---|---|---|
| `reg_dat_base` | `:72` | 数据（读）寄存器基址 |
| `reg_set_base` | `:73` | 置位寄存器基址 |
| `reg_clr_base` | `:74` | 清位寄存器基址 |
| `reg_dir_in_base` | `:75` | 方向（输入语义）寄存器基址 |
| `reg_dir_out_base` | `:76` | 方向（输出语义）寄存器基址 |
| `reg_stride` | `:77` | 相邻寄存器间距 |
| `ngpio_per_reg` | `:78` | 每个寄存器管几根线 |
| `irq_domain` | `:79` | 现成的 irqdomain（见 §7.4） |
| `reg_mask_xlate` | `:81-83` | 自定义「offset → (寄存器地址, 掩码)」映射 |

头文件里有一段 kerneldoc（`:48-58`）把五条寄存器搭配规则列成了编号清单，其中第 5 条自陈了一个未决问题：

> 5. @reg_dir_in_base and @reg_dir_out_base are exclusive; is there really hardware which has redundant registers?
> — `include/linux/gpio/regmap.h:57-58`

这条自陈债和 §7.3 的第 5 条校验直接对应——代码目前**强制**两者互斥，而作者自己也不确定这个前提对不对。清单里的另外四条（`:50-56`）分别对应 §7.3 的校验 3、校验 3、校验 4 之外的合法组合，以及 `reg_clr_base` 的可选性。

另有一个特殊值要留意：注释末尾（`:60-61`）说明所有基址都可以取 `%GPIO_REGMAP_ADDR_ZERO`——**因为 0 被当作「没设」，真的要用地址 0 的硬件必须用这个哨兵值**。这是「用 0 表示未设置」这一常见做法必然要付的代价。

### §7.3 `gpio_regmap_register()` 的五道入口校验

`gpio-regmap.c:193-292`，前 20 行全是校验，每道都直接 `ERR_PTR(-EINVAL)`：

| # | 条件 | 源码注释 |
|---|---|---|
| 1 | `!config->parent` | 无 |
| 2 | `!config->ngpio` | 无 |
| 3 | `!reg_dat_base && !reg_set_base` | `/* we need at least one */` |
| 4 | 有方向寄存器但缺 `reg_dat_base` 或 `reg_set_base` | `/* if we have a direction register we need both input and output */` |
| 5 | `reg_dir_out_base && reg_dir_in_base` | `/* we don't support having both registers simultaneously for now */` |

第 5 条与 §6.5 的 bgpio 形成鲜明对比：**同一个硬件形态（两个方向寄存器），bgpio 选择「接受并强行同步」，regmap 版选择「直接拒绝」。** 两条路都在本树里活着，谁都没被统一。注释里的 `for now` 说明这是权宜之计。

### §7.4 三个默认值 + 唯一的 irqdomain 后置挂载

三个「没设就给个默认」（`:234-243`）：

```c
	/* if not set, assume there is only one register */
	if (!gpio->ngpio_per_reg)
		gpio->ngpio_per_reg = config->ngpio;

	/* if not set, assume they are consecutive */
	if (!gpio->reg_stride)
		gpio->reg_stride = 1;

	if (!gpio->reg_mask_xlate)
		gpio->reg_mask_xlate = gpio_regmap_simple_xlate;
```

`can_sleep` 的处理带一段坦白的注释（`:253-259`）：

> If our regmap is fast_io we should probably set can_sleep to false. Right now, the regmap doesn't save this property, nor is there any access function for it. The only regmap type which uses fast_io is regmap-mmio. For now, assume a safe default of true here.

结论：**gpio-regmap 注册出来的 chip 一律 `can_sleep = true`**，即使底层是 MMIO。后果很具体——这样的 chip **不能**用于 chained irqchip（03 §B4.4 讲的 `can_sleep` + chained → `-EINVAL`），也不能在原子上下文里读写。这不是硬件限制，是 regmap 没暴露 `fast_io` 属性导致的信息丢失。

回调装配（`:262-272`）按寄存器齐备程度分档：`chip->get` 总是装；`reg_set_base && reg_clr_base` → `gpio_regmap_set_with_clear`，只有 `reg_set_base` → `gpio_regmap_set`；有方向寄存器才装那三个方向回调。

irqdomain 挂载：

```c
	ret = gpiochip_add_data(chip, gpio);
	if (ret < 0)
		goto err_free_gpio;

	if (config->irq_domain) {
		ret = gpiochip_irqchip_add_domain(chip, config->irq_domain);
		if (ret)
			goto err_remove_gpiochip;
	}
	— gpio-regmap.c:274-282
```

**`gpiochip_irqchip_add_domain()` 在整棵树上只有这一个调用点。** 验证：

```
$ grep -rn 'gpiochip_irqchip_add_domain(' --include=*.c --include=*.h . | grep -v Documentation
drivers/gpio/gpiolib.c:1686: * gpiochip_irqchip_add_domain() - adds an irqdomain to a gpiochip
drivers/gpio/gpiolib.c:1692:int gpiochip_irqchip_add_domain(struct gpio_chip *gc,
drivers/gpio/gpio-regmap.c:279:		ret = gpiochip_irqchip_add_domain(chip, config->irq_domain);
include/linux/gpio/driver.h:707:int gpiochip_irqchip_add_domain(struct gpio_chip *gc,
include/linux/gpio/driver.h:710:static inline int gpiochip_irqchip_add_domain(struct gpio_chip *gc,
```

五条命中里，kerneldoc（`gpiolib.c:1686`）、定义（`:1692`）、原型（`driver.h:707`）、`CONFIG_GPIOLIB_IRQCHIP` 关掉时的内联存根（`:710`）各占一条，**真正的调用点只有 `gpio-regmap.c:279` 这一条**。

这个 API 的语义是「我已经有一个现成的 irqdomain，别帮我建」，是 03 §B4 那张 irqchip 分类表之外的**第三条路**：既不是 cascaded 也不是 hierarchical，而是「借用外部 domain」。它只服务一个使用者，属于典型的为单一场景开的后门。

### §7.5 谁在用

```
$ grep -rn 'devm_gpio_regmap_register(\|gpio_regmap_register(' --include=*.c drivers/ | grep -v 'gpio-regmap.c' | wc -l
3
```

三个文件：`drivers/pinctrl/bcm/pinctrl-bcm63xx.c`、`drivers/gpio/gpio-tn48m.c`、`drivers/gpio/gpio-sl28cpld.c`。对比 §6 的 38 个——**bgpio 是主流，gpio-regmap 是小众**。arm64 平台上基本不会碰到它。

三个导出符号：`gpio_regmap_register` `:292`、`gpio_regmap_unregister` `:303`、`devm_gpio_regmap_register` `:338`，加一个 `gpio_regmap_get_drvdata` `:185`。

---

## §8 虚拟提供方：aggregator / sim / mockup

三者共同点：**都不对应真实硬件，都走 03 §A2 完整的十三段注册流程**，因此它们是验证核心行为的最便捷手段。区别在于配置入口。

### §8.1 三者对比

| | `gpio-aggregator.c` | `gpio-sim.c` | `gpio-mockup.c` |
|---|---|---|---|
| 行数 | 550 | 1613 | 614 |
| Kconfig | `GPIO_AGGREGATOR`（`Kconfig:1673`） | `GPIO_SIM`（`:1705`） | `GPIO_MOCKUP`（`:1685`） |
| 配置入口 | driver 级 sysfs 属性 | **configfs** | **module_param** |
| 底层线 | 转发到**真实**的 GPIO 线 | 纯软件模拟 | 纯软件模拟 |
| 运行期可重配 | 可（新建/删除） | 可（configfs 目录树） | **不可**（只能改模块参数重载） |
| 额外 select | 无 | `IRQ_SIM` + `CONFIGFS_FS` | `IRQ_SIM` |

### §8.2 aggregator：把散落的真实线拼成一个虚拟 chip

Kconfig 帮助文字列了三个用途（`drivers/gpio/Kconfig:1679-1683`）：

> - Assign permissions for a collection of GPIO lines to a user,
> - Export a collection of GPIO lines to a virtual machine,
> - Provide a generic driver for a GPIO-operated device in an industrial control context, to be operated from userspace using the GPIO chardev interface.

第一条是它最实际的价值：**`/dev/gpiochipN` 的权限粒度是整个 chip**，你没法只把 chip 上的第 5 根线开给某个用户。aggregator 让你把那一根线聚成一个新 chip，然后单独 chmod 它。这是「权限粒度不匹配」这个 ABI 层缺陷的用户态绕法。

配置入口是两个 **driver 级** sysfs 属性（不是 device 级）：`new_device`（`DRIVER_ATTR_WO`，`gpio-aggregator.c:186`）和 `delete_device`（`DRIVER_ATTR_WO`，`:226`），装进 `gpio_aggregator_attrs[]`（`:228`）→ `ATTRIBUTE_GROUPS(gpio_aggregator)`（`:233`）→ `gpio_aggregator_driver`（`:526`）的 `.driver.groups`（`:530`）。路径形如 `/sys/bus/platform/drivers/gpio-aggregator/new_device`。

转发层是一整套 `gpio_fwd_*` 回调（`:269`–`:398`），装配在 `:457-464`：`get_direction` / `direction_input` / `direction_output` / `get` / `get_multiple` / `set` / `set_multiple` / `set_config` / `to_irq`。注意 `get_multiple` 和 `set_multiple` 装的是带 `_locked` 后缀的版本（`gpio_fwd_get_multiple_locked` `:325`、`gpio_fwd_set_multiple_locked` `:373`），内部再调不带后缀的（`:299` / `:355`）。分两层的原因是转发时可能需要在多个下游 chip 之间做批量拆分，而那段拆分必须在锁内。

`set_config` 是**有条件**装的（`:451`）——只有下游支持时才转发。

### §8.3 sim：configfs 驱动的测试 chip

`gpio-sim.c` 开头一句自我定义：`* GPIO testing driver based on configfs.`（`:3`）。

configfs 子系统注册在 `gpio_sim_init()`（`:1588-1597`）：

```c
	config_group_init(&gpio_sim_config_subsys.su_group);
	mutex_init(&gpio_sim_config_subsys.su_mutex);
	ret = configfs_register_subsystem(&gpio_sim_config_subsys);
	if (ret) {
		pr_err("Error %d while registering the configfs subsystem %s\n",
		       ret, gpio_sim_config_subsys.su_group.cg_item.ci_namebuf);
		mutex_destroy(&gpio_sim_config_subsys.su_mutex);
		platform_driver_unregister(&gpio_sim_driver);
		return ret;
	}
```

子系统名是 `"gpio-sim"`（`:1572` 的 `.ci_namebuf`），挂载点形如 `/sys/kernel/config/gpio-sim/`。platform device 名也是 `"gpio-sim"`（`:931` 的 `pdevinfo.name`，以及 `:478`）。

configfs 目录树有五层类型，各自一个 `config_item_type`：

| 层 | 类型变量 | 行 |
|---|---|---|
| 子系统根 | `gpio_sim_config_type` | `:1564` |
| 设备 | `gpio_sim_device_config_group_type` | `:1525` |
| bank | `gpio_sim_bank_config_group_type` | `:1464` |
| line | `gpio_sim_line_config_type` | `:1397` |
| hog | `gpio_sim_hog_config_type` | `:1339` |

核心机制是 `live` 这个属性（`CONFIGFS_ATTR(gpio_sim_device_config_, live)`，`:1001`）：**往 `live` 写 1 才真正注册 gpiochip**，写 0 则注销。这让你可以先把整棵配置树建好再一次性生效。实现是 `gpio_sim_device_activate_unlocked()`（`:891`）/ `gpio_sim_device_deactivate_unlocked()`（`:963`），入口 `gpio_sim_device_config_live_store()`（`:975`），其中 `:988-989` 的双向幂等检查保证重复写同一个值是 no-op。

`gpio_sim_bank_labels_non_unique()`（`:874`）是一条**输入校验**：多个 bank 用相同 label 会被拒。这条校验的存在反过来说明核心侧不查 label 唯一性（03 §A2 里没有这道门）。

sim 还支持 hog（`gpio_sim_add_hogs()` `:760` / `gpio_sim_remove_hogs()` `:742`），所以它也能用来测 03 §A2.10 讲的 DT hog 扫描逻辑。

有一段注释值得留着当反例：`/* configfs is stupid and calls the item's release callback after ... */`（`:556-568`）。它说明这个驱动里有一段专门对付 configfs 生命周期次序的代码，读到那里觉得别扭是正常的。

### §8.4 mockup：module_param + debugfs 的老一辈测试驱动

Kconfig 帮助文字（`drivers/gpio/Kconfig:1689-1693`）：

> This enables GPIO Testing driver, which provides a way to test GPIO subsystem through sysfs (or char device) and debugfs. User could use it through the script in `tools/testing/selftests/gpio/gpio-mockup.sh`. Reference the usage in it.

配置入口是两个模块参数（`gpio-mockup.c:62-68`）：

```c
static int gpio_mockup_ranges[GPIO_MOCKUP_MAX_RANGES];
static int gpio_mockup_num_ranges;
module_param_array(gpio_mockup_ranges, int, &gpio_mockup_num_ranges, 0400);

static bool gpio_mockup_named_lines;
module_param_named(gpio_mockup_named_lines,
		   gpio_mockup_named_lines, bool, 0400);
```

两个参数的权限都是 **0400**：只读、只有 root 能读。**加载后不可改**——这是它与 sim 的根本差别，也是 sim 被引入的原因。

`gpio_mockup_init()`（`:558`）的入口校验：`gpio_mockup_num_ranges` 必须是偶数且不超过 `GPIO_MOCKUP_MAX_RANGES`（否则 `-EINVAL`），注释 `/* Each chip is described by two values. */`；然后逐 chip 检查第二个值（ngpio）必须 > 0，注释 `/* The second value in the <base GPIO - number of GPIOS> pair must always be greater than 0. */`。

debugfs 侧：根目录 `debugfs_create_dir("gpio-mockup", NULL)`（`:578`），每 chip 一个子目录（`:356`），每线一个 0600 文件（`:371-372`，`gpio_mockup_debugfs_ops` `:335`）。清理走 `devm_add_action_or_reset(dev, gpio_mockup_debugfs_cleanup, chip)`（`:465`）+ 模块退出时的 `debugfs_remove_recursive()`（`:603`）。

`gpio_mockup_debugfs_ops` 上方那段注释（`:319-333`）讲的是一件必须先知道否则一定读错的事：**同一个 debugfs 文件，读和写不是同一个量。**

> Reading from the line's file yields the current \*value\*, writing to the line's file changes the current \*pull\*. Default pull for mockup lines is down.
> — `gpio-mockup.c:319-321`

注释接着给了四条例子（`:324-333`），其中两条最能说明后果：

- 「line 在输出模式下被驱动为高时，往 debugfs 写 0 会把 pull 改成 down，**但读回的值仍然是 1，直到这根线被释放**」（`:326-328`）
- 「line 在输入模式下并被监听事件时，写入**相同**的值是 no-op，写入相反的值才产生一个 dummy 中断」（`:331-333`）

这类「setter 改的不是 getter 读的那个量」正是 `07-debug.md` 要求逐个核对的对象——把它当成普通赋值用，测试会得出与预期完全相反的结论。

---

## §9 `gpiolib-of.c` 在本篇的定位

`gpiolib-of.c`（1032 行，`CONFIG_OF_GPIO`，`drivers/gpio/Makefile:9`）严格来说也是一个可裁剪的集成层，但**本篇不展开它**，原因有两条：

1. 它有两个挂载点（§0.3），且都在核心主流程上——提供方侧 `of_gpiochip_add()`（03 §A2 第 10 段）、消费者侧 `of_find_gpio()`（03 §A4.1）。这两段已经在 03 里逐行讲过。
2. 它的**真正内容是「哪条 DT 属性由谁消费」**，那是 `06-devicetree.md` 一整篇的题目。

本篇只留一句必要的定位：**arm64 上 `CONFIG_OF_GPIO` 事实上是必开的**，因为设备树是唯一的固件描述来源（§10 说明了另一条路被裁掉的理由）。四个导出符号也在 `06-devicetree.md` 里点名。

一个跨篇都会用到的反直觉事实先放在这里：**`gpio-controller` 这个属性 gpiolib 核心从来不读**。全树唯一读它的地方是 `for_each_gpiochip_node`（`include/linux/gpio/driver.h:808-810`）这个迭代宏，服务的是「遍历所有 gpiochip 节点」的场景，而不是「判定这个节点是不是 gpiochip」。判定靠的是驱动自己匹配 compatible。

---

## §10 ACPI 集成为什么不在本篇

`drivers/gpio/gpiolib-acpi.c`（1686 行，`CONFIG_GPIO_ACPI`，`drivers/gpio/Makefile:12`）是本子系统第二大的单文件，功能上与 §9 的 `gpiolib-of.c` 平行——它是 ACPI 固件描述到 gpiolib 的集成层，消费者侧入口是 `acpi_find_gpio()`（03 §A4 三路查找的第二路）。

**本文档集显式裁掉它**，理由是本文档集的目标架构限定为 arm64，而 arm64 上 GPIO 的固件描述实际走设备树。

被裁掉的具体内容清单（供需要时按此索引自行补读）：

| 内容 | 位置 | 行数 |
|---|---|---|
| ACPI 集成实现 | `drivers/gpio/gpiolib-acpi.c` | 1686 |
| 内部头 | `drivers/gpio/gpiolib-acpi.h` | 104 |
| `acpi_gpio_*` 消费者 API | `include/linux/gpio/consumer.h:653-713` | 61 |
| 固件属性指南 | `Documentation/firmware-guide/acpi/gpio-properties.rst` | — |
| 消费者文档的 ACPI 章 | `Documentation/driver-api/gpio/consumer.rst` 的 ACPI 小节 | — |
| 板级文档的 `_DSD` 示例 | `Documentation/driver-api/gpio/board.rst:66-101` | 36 |

这条裁剪同时记录在 `99-evidence.md` §2.7。一处需要留意的连带影响：03 §A4 的三路查找里，第二路（`is_acpi_node()` → `acpi_find_gpio()`）在本文档集里只画了框、没有展开。看到那里的空白不是遗漏。

---

## §11 汇总

### §11.1 框架 × 职责 × 挂载点 × arm64

| # | 框架 | 一句话职责 | 挂载点 | arm64 相关 |
|---|------|-----------|--------|-----------|
| §1 | cdev v2 | `/dev/gpiochipN` 的当代 ABI：批量、事件、时间戳、debounce | `gpiochip_setup_dev()` → `gpiolib_cdev_register()` | ✅ 一定会 |
| §2 | cdev v1 | 同一节点上的旧 ABI，值与事件拆成两种 fd | 无独立挂载点（编译期 `#ifdef`） | ✅ 很可能 |
| §3 | legacy sysfs | `/sys/class/gpio/`，一行一线，已废弃 | `gpiochip_sysfs_register()` + `postcore_initcall` 回扫 | ⚠️ 可能但别用 |
| §4 | devres | 把获取结果记到 devres 链，remove 时自动释放 | 无（消费者侧包装） | ✅ 一定会 |
| §5 | 整数 legacy API | `gpio_request_one()` 等 5 个整数入口，值语义全 raw | 无（落到 `gpiod_request_commit()`） | ⚠️ 老驱动 |
| §6 | bgpio（`gpio-mmio.c`） | 按寄存器布局自动填好 `gpio_chip` 九个回调 | `gpiochip_add_data()` **之前**由提供方调用 | ✅ 很可能（38 个使用者） |
| §7 | gpio-regmap | 同 §6，但寄存器访问走 regmap | 同 §6 + 之后的 `gpiochip_irqchip_add_domain()` | ❌ 少见（3 个使用者） |
| §8 | aggregator / sim / mockup | 虚拟提供方：重组真实线 / configfs 模拟 / 参数模拟 | 与真实提供方相同的十三段 | ❌ 仅测试 |
| §9 | `gpiolib-of.c` | DT 到 gpiolib 的集成层 | `of_gpiochip_add()` + `of_find_gpio()` | ✅ 一定会（细节见 06） |
| §10 | `gpiolib-acpi.c` | ACPI 集成层 | `acpi_find_gpio()` | ❌ **本文档集裁掉** |

### §11.2 本篇挖出来的六个坑（都带 ）

| # | 坑 | 在哪一节 |
|---|---|---|
| 1 | ioctl 号段按添加时间而非版本分配，0x0B/0x0C 夹在 v1 中间 | §1.4 |
| 2 | `CONFIG_HTE` 关掉时 `EVENT_CLOCK_HTE` 走空存根，静默降级 | §1.6 |
| 3 | `gpiod_export()` 第一道门是 `pr_debug`，早期调用失败无任何 dmesg | §3.3 |
| 4 | `GPIOF_INIT_HIGH` 走 `gpiod_direction_output_raw()`，不受 `ACTIVE_LOW` 反相 | §5.2 |
| 5 | `bgpio_init()` 覆盖 `label`/`base`/`ngpio`/`request` 四个字段，会盖掉 pinctrl 桥接 | §6.4 |
| 6 | gpio-regmap 一律 `can_sleep = true`，即使底层是 MMIO，导致不能做 chained irqchip | §7.4 |

### §11.3 下一步读什么

| 想知道什么 | 去哪 |
|---|---|
| 十三段注册时间线、锁规则、值语义分层 | `03-internals.md` |
| 上面这些框架各自暴露的 API 清单 | `01-api.md` §5–§11 |
| `struct gpio_device` / `gpio_desc` / `gpio_chip` 的字段 | `02-datastructures.md` |
| 用 cdev / sysfs / devres 各角色怎么写代码 | `05-usage.md` |
| DT 属性怎么写、谁消费、配错怎么定位 | `06-devicetree.md` |
| debugfs / tracepoint / 日志可见性 / libgpiod 工具 | `07-debug.md` |
| 「v1 和 v2 到底差在哪」这类概念性追问 | `08-feynman.md` |
| 本篇每个数字的复现命令 | `99-evidence.md` |

