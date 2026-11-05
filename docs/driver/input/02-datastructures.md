---
title: 数据结构
---

# input 子系统 · 数据结构与全局状态

- 内核版本：Linux 6.1.158（`f6e38ae624cf`），目标架构 **arm64**
- 本篇管「这些结构体分别是什么、谁指向谁、哪把锁保护哪块字段」。**行号在本篇是硬要求**，每个字段和赋值点都给出处
- 想知道「该调哪个函数」去 `01-api.md`；想知道「这些结构在时间上怎么被建起来」去 `03-internals.md`
- 本篇不讲 evdev/joydev/mousedev 各自的私有结构（`evdev_client` 之类）——那些在 `04-extensions.md`

---

## §1.0 先分清四组关系

input 子系统的结构名字长得都很像（`input_dev` / `input_handler` / `input_handle`），而且它们之间的关系**和你在 i2c、spi、platform 上养成的直觉正好错开一格**。这一节先把四个错误类比否掉。

### §1.0.1 input **不是**一条总线

| 判据 | i2c | spi | **input** |
|---|---|---|---|
| 有 `struct bus_type` 吗 | 有（`i2c_bus_type`） | 有（`spi_bus_type`） | **没有** |
| 有 `struct class` 吗 | 有（`i2c_adapter_class`） | 有 | **有**：`input_class`，`drivers/input/input.c:1995` |
| 设备会不会被 match 到一个 driver | 会（`i2c_device_match`） | 会 | **不会**——input 设备身上永远绑不上 `struct device_driver` |
| 谁来「驱动」它 | `i2c_driver` | `spi_driver` | 没有人。**它自己就是驱动的产物** |

判定命令与输出（99 §1 有完整记录）：

```bash
grep 'struct bus_type' drivers/input/*.c include/linux/input*.h   # 无输出
```

★ **`input_dev` 是「一个已经写好的驱动向内核登记的一个能力」，不是「一个等着被驱动认领的硬件」。** 你的触摸屏驱动是 `i2c_driver`，它 probe 成功之后**造出**一个 `input_dev`。所以在设备模型里，`input_dev` 更像 `led_classdev` 或 `thermal_zone_device`——是 class 设备，不是 bus 设备。

为什么不会发生「input 设备被匹配 driver」：`input_dev` 内嵌的 `struct device dev`（`include/linux/input.h:198`）在 `input_register_device()` 里被赋值成 `dev->dev.class = &input_class`（见 `03-internals.md` Part A），**`dev.bus` 从头到尾没人写**。驱动核心的 `device_add()` 只在 `dev->bus` 非空时才走匹配流程。

### §1.0.2 `input_handler` **不是** driver，`input_handle` **不是**「打开的设备」

这两个名字最容易被套上驱动模型的壳。实际对应关系：

| 你可能以为它是 | 它实际是 | 关键区别 |
|---|---|---|
| `input_handler` ≈ `struct device_driver` | **事件消费者的注册项** | 它没有 `probe`/`remove`，有 `connect`/`disconnect`；它不出现在任何 `bus->drivers` 链表里，只在 `input_handler_list` 上 |
| `input_handle` ≈ 「用户打开了这个设备」 | **一条 device↔handler 的连线** | 它在**内核内部**建立，和用户态的 `open()` 无关。一个 `input_handle` 可能对应零个或成百个用户态 fd |
| `handler->id_table` ≈ `of_match_table` | **能力位图的子集判定** | 它匹配的是「设备会发哪些事件」，不是「设备是什么型号」。见 §2.8 |

★ 一条 `input_handle` 上的 `open` 字段（`include/linux/input.h:349`）是个 `int` 计数，但它**不是用户态 fd 数**：evdev 是在自己那层数 fd 的，只在 0↔非 0 翻转时才动 handle 的 `open`。所以 `/proc/bus/input/devices` 里看不出「有几个进程在读」。

### §1.0.3 一个设备连着多个 handler 是**常态**，不是异常

在 i2c/spi 上，一个设备只绑一个 driver。input 反过来：一个键盘同时被 evdev、VT 键盘层、SysRq、input-leds 四个 handler 连着，**每个事件被复制四份分发**。

```
                            dev->h_list（按 filter 优先排序）
                                  │
  ┌──────────────┐                ├──▶ input_handle ──▶ sysrq_handler   （filter，可吞掉事件）
  │  input_dev   │────────────────┤
  │  「键盘」     │                ├──▶ input_handle ──▶ kbd_handler     （VT 层）
  └──────────────┘                ├──▶ input_handle ──▶ evdev_handler   （/dev/input/eventN）
                                  └──▶ input_handle ──▶ input_leds_handler
```

反方向也是多对多：`evdev_handler` 一个实例，通过 `handler->h_list` 挂着系统里**所有**输入设备的 handle。所以：

- `input_handle` 同时挂在两条链表上：`d_node` 进设备的 `h_list`，`h_node` 进 handler 的 `h_list`（`include/linux/input.h:355-356`）。**两个字段名和两条链表名交叉，读代码时极易看反**
- 记法：**`d_node` 是「我在设备那边的挂钩」，`h_node` 是「我在 handler 那边的挂钩」**；而两边的链表头都叫 `h_list`（设备的 `h_list` 装 handle，handler 的 `h_list` 也装 handle）

### §1.0.4 `input_mt_slot` **不是**「一个触点对象」

它是**一个槽位**，槽位可以是空的。区别在生命周期：

| | 触点（手指） | 槽位（`input_mt_slot`） |
|---|---|---|
| 数量 | 变化的，0 到 N | **固定**，`input_mt_init_slots()` 时定死 |
| 手指抬起时 | 消失 | 还在，只是 `ABS_MT_TRACKING_ID` 变成 -1 |
| 判活 | — | `input_mt_is_active()`，`include/linux/input/mt.h:65` |

★ 所以「设备支持 10 点触控」在代码里的意思是 `num_slots == 10`，一次分配、终生不变。用户态看到的「手指 3 号消失了」实际是「3 号槽位的 tracking id 被设成 -1」。

### §1.0.5 本子系统有几层可插拔

| 层 | 抽象 | 实现者住在哪 | 本树实现数 |
|---|---|---|---|
| 事件生产者 | `struct input_dev` + 它的 `open`/`close`/`event`/`flush` 回调 | `drivers/input/*/`、`drivers/hid/`、`drivers/platform/`、`sound/` 等 | **474 处 `input_register_device()` 调用，分布在 412 个文件**（不含 `input.c` 自己） |
| 事件消费者 | `struct input_handler` | `drivers/input/`（6 个）+ 树里其他五个子系统（6 个） | **12 个注册点**（11 个静态实例 + 1 个动态注册），名单见 §3 |
| 力反馈后端 | `struct ff_device` 的 `upload`/`erase`/`playback`/`set_gain`/`set_autocenter` | 力反馈驱动，多数在 `drivers/hid/` | 见 `04-extensions.md` §3 |

**只有两层是必须的**（生产者、消费者），力反馈是可选的第三层。这比 i2c（adapter + client 两层）多不了多少，但比 virtio（传输 ops + 设备驱动 ops）的耦合方式不同：input 的两层**互不知道对方的类型**，只通过能力位图握手。

重跑生产者计数：

```bash
grep -rn --include=*.c 'input_register_device(' . | grep -v 'drivers/input/input.c' | wc -l   # 474
grep -rl --include=*.c 'input_register_device(' . | grep -v 'drivers/input/input.c' | wc -l   # 412
```

---

## §1.1 结构关系全图

图中 `Lnnn` 形式的裸行号一律指 `include/linux/input.h`；其余行号写全路径。

```
                       ┌────────────────────────────────────────┐
                       │  input_mutex（drivers/input/input.c:47）│
                       │  同时保护下面两条全局链表，             │
                       │  并让 register_device 与                │
                       │  register_handler 互斥                  │
                       └───────────┬────────────────────────────┘
                                   │
        input_dev_list ◀───────────┴───────────▶ input_handler_list
        (drivers/input/input.c:38)         (同一文件 :39)
             │ node                                    │ node
             ▼                                         ▼
   ┌─────────────────────────┐              ┌──────────────────────────┐
   │ struct input_dev        │              │ struct input_handler     │
   │ L137-212                │              │ L310-331                 │
   │                         │              │                          │
   │ ─ 身份 ────────────     │              │ ─ 回调 ────────────       │
   │  name / phys / uniq     │              │  event / events          │
   │  struct input_id id     │              │  filter / match          │
   │                         │              │  connect / disconnect    │
   │ ─ 能力（9 张位图）───    │              │  start                   │
   │  evbit keybit relbit    │              │                          │
   │  absbit mscbit ledbit   │◀── 匹配 ────▶│ ─ 匹配 ────────────       │
   │  sndbit ffbit swbit     │  bitmap_subset│  id_table：             │
   │  propbit                │              │  struct input_device_id[]│
   │                         │              │                          │
   │ ─ 当前状态（4 张）───    │              │ ─ 字符设备 ───────       │
   │  key led snd sw         │              │  legacy_minors / minor   │
   │                         │              │                          │
   │ ─ 扩展 ────────────     │              │  h_list ─────┐           │
   │  ff → ff_device         │              └──────────────┼───────────┘
   │  mt → input_mt          │                             │
   │  poller→input_dev_poller│                             │ h_node
   │  absinfo→input_absinfo[]│                             ▼
   │  keycode（变长表）      │        ┌────────────────────────────────┐
   │                         │        │ struct input_handle            │
   │ ─ 分发 ────────────     │        │ L345-357                       │
   │  vals/num_vals/max_vals │        │  private / open / name         │
   │  grab（RCU）            │◀───────│  dev ──▶ input_dev             │
   │  event_lock（自旋）      │ d_node │  handler ──▶ input_handler     │
   │  mutex（睡眠）           │        │  d_node / h_node               │
   │  h_list ────────────────┼───────▶└────────────────────────────────┘
   │                         │
   │ ─ 设备模型 ─────────    │
   │  struct device dev  ────┼──▶ .class = &input_class（drivers/input/input.c:1995）
   │   （.bus 永远是 NULL）  │    .type  = &input_dev_type
   │  devres_managed         │    .parent = 造它的那个 platform/i2c/spi 设备
   └─────────────────────────┘
```

### §1.1.1 和父总线的关系

input 没有自己的总线，所以「父总线关系」在这里的形态是：**`input_dev.dev.parent` 指向真正在某条总线上的那个设备**。

```
   i2c_bus_type                              input_class（不是总线）
        │                                          │
   ┌────▼────────────┐   dev.parent   ┌────────────▼──────────────┐
   │ struct i2c_client│◀──────────────│ struct input_dev          │
   │ 「触摸屏芯片」   │               │ 「触摸屏输入设备」         │
   │ 绑了 i2c_driver  │               │ 永远不绑 driver            │
   └─────────────────┘               └───────────────────────────┘
        ▲                                          ▲
        │ probe() 时创建 ───────────────────────────┘
        └── 驱动代码：drivers/input/touchscreen/xxx.c
            同一个 .c 文件既是 i2c_driver 也是 input_dev 的生产者
```

三个必查点的答案：

1. **控制器 DT 节点由谁变成设备**：input 没有控制器。设备节点由它所在的父总线（i2c/spi/platform）的核心变成 `i2c_client`/`spi_device`/`platform_device`
2. **子节点会不会被 platform 层继续下钻**：不适用——input 设备没有 DT 子节点语义
3. **两条总线的连接点是哪一行**：`devm_input_allocate_device()` 里的 `input->dev.parent = dev`（`drivers/input/input.c:2091`）。用 `input_allocate_device()` 的驱动要自己赋值，**忘了赋 parent 的后果**见 `01-api.md` §5.2 里 `matrix_keypad_build_keymap()` 那一条

★ 这个形状带来一个实际后果：**`/sys/class/input/input5/device` 指向的是那个 i2c_client，不是别的 input 设备**。排查「这个 event 节点是哪块硬件」时，读 `/sys/class/input/eventN/device/device/name` 要往上跳两级——一级到 `inputN`，再一级到真实硬件。

---

## §1.2 命名约定速查

| 前缀 / 模式 | 含义 | 例 |
|---|---|---|
| `input_dev_*` | 操作 `input_dev` 或它内嵌 `struct device` 的内部函数 | `input_dev_release`、`input_dev_uevent`、`input_dev_poller_start` |
| `input_report_*` | 上报一个事件的内联包装，**驱动侧** | `input_report_key`、`input_report_abs` |
| `input_set_*` | 注册前的声明动作，**驱动侧** | `input_set_capability`、`input_set_abs_params` |
| `input_mt_*` | 多点触控库 | `input_mt_slot`、`input_mt_sync_frame` |
| `input_ff_*` | 力反馈 | `input_ff_create`、`input_ff_upload` |
| `*bit`（字段） | **能力**位图：「我支持不支持」 | `evbit`、`keybit`、`absbit` |
| 无 `bit` 后缀的同名字段 | **当前状态**位图：「现在是按下还是抬起」 | `key`、`led`、`snd`、`sw` |
| `EV_*` | 事件大类 | `EV_KEY`、`EV_ABS`、`EV_SYN` |
| `*_CNT` / `*_MAX` | 码空间上界。★ **`_CNT` 是 `_MAX + 1`**，位图尺寸用 `_CNT`，越界判断用 `_MAX` | `KEY_MAX` 0x2ff / `KEY_CNT` 0x300 |
| `EVIOC*` | evdev 的 ioctl | `EVIOCGRAB`、`EVIOCSFF` |
| `UI_*` | uinput 的 ioctl 与常量 | `UI_DEV_CREATE`、`UI_SET_EVBIT` |
| `INPUT_PROP_*` | 设备**属性**（不是能力）：指针型/直接型/按钮板… | `INPUT_PROP_DIRECT` |
| `INPUT_MT_*` | `input_mt_init_slots()` 的 flags | `INPUT_MT_DROP_UNUSED` |

★ **`keybit` 和 `key` 这一对是本子系统最险的命名**：差一个后缀，一个是「支持哪些键」、一个是「现在按着哪些键」，类型完全一样（`unsigned long[BITS_TO_LONGS(KEY_CNT)]`），写错编译器一句话都不会说。9 张能力位图（`include/linux/input.h:145-153`）对 4 张状态位图（`:180-183`），只有 `key`/`led`/`snd`/`sw` 四类有状态。

---

## §2 核心结构体逐个拆

### §2.1 `struct input_dev` — 一台输入设备

`include/linux/input.h:137-212`，紧跟着 `:213` 的 `#define to_input_dev(d) container_of(d, struct input_dev, dev)`。76 行、约 30 个字段，是本子系统最大的结构。**按功能分七组读**，字段次序就是源码次序：

| 组 | 字段 | 行 | 说明 |
|---|---|---|---|
| 身份 | `name` / `phys` / `uniq` | 138-140 | 三个 `const char *`，分别对应 `EVIOCGNAME` / `EVIOCGPHYS` / `EVIOCGUNIQ`。**都可以是 NULL**，此时 ioctl 返回 `-ENOENT`（见 `01-api.md` §6） |
| 身份 | `id` | 141 | `struct input_id`，四个 `__u16`：总线类型/厂商/产品/版本。见 §2.5 |
| 属性 | `propbit` | 143 | `INPUT_PROP_*` 位图，32 位空间实际只用了 7 个（`INPUT_PROP_POINTER` 0x00 到 `INPUT_PROP_ACCELEROMETER` 0x06） |
| 能力 | `evbit` `keybit` `relbit` `absbit` `mscbit` `ledbit` `sndbit` `ffbit` `swbit` | 145-153 | 9 张位图，每张尺寸 `BITS_TO_LONGS(<类型>_CNT)`。「我支持哪些事件」 |
| 能力 | `hint_events_per_packet` | 155 | 给 handler 的缓冲区尺寸提示，不是硬限制。evdev 用它算默认 buffer 大小 |
| 键盘映射 | `keycodemax` / `keycodesize` / `keycode` | 157-159 | 变长扫描码表：元素个数、单元素字节数、表指针。`keycode` 是 `void *` 因为元素可能是 `u8`/`u16`/`u32` |
| 键盘映射 | `setkeycode` / `getkeycode` | 161-165 | 驱动可选覆盖。**不实现时核心用 `keycode` 表做默认实现** |
| 扩展 | `ff` | 167 | `struct ff_device *`，`input_ff_create()` 之后才非 NULL。见 §2.7 |
| 扩展 | `poller` | 169 | `struct input_dev_poller *`。★ **这是个前向声明的不完整类型**，定义藏在 `drivers/input/input-poller.c` 里，头文件外看不到它有什么字段 |
| 扩展 | `mt` | 176 | `struct input_mt *`，`input_mt_init_slots()` 之后非 NULL。见 §2.6 |
| 扩展 | `absinfo` | 178 | `struct input_absinfo *`，按 `ABS_CNT` 一次分配整个数组（不是稀疏的）。第一次 `input_set_abs_params()` 触发分配 |
| 自动重复 | `repeat_key` / `timer` / `rep` | 171-174 | 内核软件自动重复：当前正在重复哪个键、定时器、`rep[REP_CNT]` 存 delay/period |
| 当前状态 | `key` `led` `snd` `sw` | 180-183 | 4 张位图。★ **和上面的 `keybit`/`ledbit`/`sndbit`/`swbit` 只差一个后缀**，语义完全不同（见 §1.2 篇末的 ★） |
| 驱动回调 | `open` / `close` | 185-186 | 第一个使用者打开 / 最后一个关闭时调用。**可以睡眠**（在 `dev->mutex` 下调用） |
| 驱动回调 | `flush` | 187 | 每个用户态 `close()` 都会调一次，参数带 `struct file *`。力反馈用它清掉该 fd 拥有的效果 |
| 驱动回调 | `event` | 188 | 核心把「输出类」事件（LED/SND/FF/REP）交给驱动。★ **在 `event_lock` 自旋锁 + 关中断下调用，不能睡眠** |
| 分发 | `grab` | 190 | `struct input_handle __rcu *`。非 NULL 时事件只发给它一个。**唯一用 RCU 保护的字段** |
| 分发 | `event_lock` | 192 | `spinlock_t`。见 §4 |
| 分发 | `mutex` | 193 | `struct mutex`。见 §4 |
| 分发 | `users` / `going_away` | 195-196 | 打开计数（handle 层面，不是 fd）/ 正在注销的标记 |
| 分发 | `num_vals` / `max_vals` / `vals` | 203-205 | **事件批处理缓冲**：核心先把一整包事件攒在 `vals[]` 里，遇到 `EV_SYN/SYN_REPORT` 才一次性推给 handler 的 `events()` 回调 |
| 分发 | `h_list` / `node` | 200-201 | `h_list` 装本设备的所有 `input_handle`；`node` 是本设备在全局 `input_dev_list` 上的挂钩 |
| 设备模型 | `dev` | 198 | 内嵌 `struct device`。★ **`.class` 被赋成 `&input_class`，`.bus` 永远 NULL**（§1.0.1） |
| 设备模型 | `devres_managed` | 207 | `devm_input_allocate_device()` 置真，决定注销时走不走 devres 拆除路径 |
| 时间戳 | `timestamp` | 209 | `ktime_t timestamp[INPUT_CLK_MAX]`，三个时钟域各存一份：`INPUT_CLK_REAL` / `_MONO` / `_BOOT`（`:38-43` 的 `enum input_clock_type`）。用户态可用 `EVIOCSCLOCKID` 选一个 |
| 抑制 | `inhibited` | 211 | sysfs `inhibit` 属性置真后，设备物理上仍在采样但事件不再分发 |

★ **`max_vals` 是「一次分配了多大」，算法是 `hint_events_per_packet + 2`**（`drivers/input/input.c:2430-2431`，`kcalloc` 一次，之后不变）。驱动一帧内报的事件数逼近它时**事件不会被丢，而是这一帧被劈开**：`drivers/input/input.c:383-386` 在 `num_vals >= max_vals - 2` 时自己追加一个 `SYN_REPORT` 并提前刷出去。后果是用户态看到两帧而不是一帧——比丢事件更难查，因为每个事件都在，只是帧边界多了一个。`hint_events_per_packet` 给小了就会踩到（`03-internals.md` Part A 展开）。

★ 表里没有 `dev->users` 之外的「引用计数」字段：`input_dev` 的生命周期完全靠内嵌 `struct device` 的 kref，取放用 `get_device(&dev->dev)` / `put_device(&dev->dev)`。**没有 `input_dev_get()` 这个函数**。

### §2.2 `struct input_handler` — 一个事件消费者

`include/linux/input.h:310-331`，22 行，全树只有 12 个注册点（§3）。

| 字段 | 行 | 说明 |
|---|---|---|
| `private` | 312 | handler 自己的数据。★ 和 `input_handle->private`（:347）**不是一回事**：前者是 handler 全局的，后者是每条连线的 |
| `event` | 314 | 单事件回调，`(handle, type, code, value)` |
| `events` | 315-316 | **批量**回调，`(handle, vals, count)`。★ **同时实现两个时只有 `events` 生效**——核心优先调 `events`，`event` 变成死代码 |
| `filter` | 317 | 返回 `true` 表示**吞掉这个事件**，后面的 handler 收不到。SysRq 和 rfkill 用这个 |
| `match` | 318 | 在 `id_table` 匹配通过之后再问一次。返回 `false` 就不连。**可选，NULL 表示无条件同意** |
| `connect` | 319 | 匹配成功时调用，handler 在这里 `kzalloc` 一个 `input_handle` 并 `input_register_handle()` |
| `disconnect` | 320 | 参数是 `input_handle *`（不是 dev），handler 在这里 unregister + free |
| `start` | 321 | `connect` 之后、设备已经可用时调用。用来「把当前状态同步一遍」（如 input-leds 点亮已亮的灯） |
| `legacy_minors` | 323 | 真值表示这个 handler 要占用 0-255 那段传统次设备号 |
| `minor` | 324 | 传统次设备号的基址。见 `01-api.md` §2.3 |
| `name` | 325 | 显示在 `/proc/bus/input/handlers` 里 |
| `id_table` | 327 | `const struct input_device_id *`，以全零项结尾。见 §2.8 |
| `h_list` / `node` | 329-330 | `h_list` 装本 handler 连出去的所有 handle；`node` 是本 handler 在全局 `input_handler_list` 上的挂钩 |

★ **`filter` 和 `event`/`events` 是互斥角色而不是叠加**：`input_register_handle()` 看 `handler->filter` 是否非 NULL 来决定把 handle 插到设备 `h_list` 的头部还是尾部（`01-api.md` §2.1 的 ★）。一个 handler 把两者都填上，语义是「我既优先又参与」，树里没有这么用的。

### §2.3 `struct input_handle` — 一条连线

`include/linux/input.h:345-357`，13 行、7 个字段，是三个结构里最小的：

| 字段 | 行 | 说明 |
|---|---|---|
| `private` | 347 | 这条连线上的私有数据（evdev 在这里放 `struct evdev *`） |
| `open` | 349 | 打开计数。★ 见 §1.0.2 的 ★：不是 fd 数 |
| `name` | 350 | 这条连线的名字（如 `"event3"`），`/proc/bus/input/devices` 的 `H:` 行列出来的就是它 |
| `dev` | 352 | 指回设备 |
| `handler` | 353 | 指回 handler |
| `d_node` | 355 | 挂进 `dev->h_list` |
| `h_node` | 356 | 挂进 `handler->h_list` |

★ **它没有锁、没有引用计数**。安全性完全由「两端的链表都在 `dev->mutex` + `input_mutex` 下改」保证。这也是为什么 handler 的 `connect`/`disconnect` 不允许自己去动这两条链表——必须走 `input_register_handle()` / `input_unregister_handle()`。

### §2.4 `struct input_value` — 分发的最小单元

`include/linux/input.h:32-36`，三个字段 `type`(`__u16`) / `code`(`__u16`) / `value`(`__s32`)，**8 字节**。

它和用户态的 `struct input_event`（`include/uapi/linux/input.h:28-47`）的关系是：**后者 = 时间戳 + 前者**。时间戳不存在 `input_value` 里，而是每个设备一份存在 `dev->timestamp[]`（§2.1），evdev 在拷给用户态时才拼上。

★ 所以「内核里传的事件不带时间戳」——一整包事件共享同一个时间戳，那是 `input_event()` 进核心时记下的，不是逐事件记的。想要「事件 A 和事件 B 之间隔了多久」的精度，input 层给不了。

核心里还有一个只读的常量实例：

```c
/* drivers/input/input.c:49 */
static const struct input_value input_value_sync = { EV_SYN, SYN_REPORT, 1 };
```

它就是「帧结束」这一个事件的单例——核心刷缓冲时直接把它追加到 `vals[]` 尾巴上，省一次构造。

### §2.5 uapi 结构：用户态看到的四个

都在 `include/uapi/linux/input.h`，**改这些等于改 ABI**：

| 结构 | 行 | 用在哪 | 备注 |
|---|---|---|---|
| `input_event` | 28-47 | `read()` / `write()` `/dev/input/eventN` | ★ 时间字段有三套布局（`struct timeval` / `__kernel_ulong_t` 对 / sparc64 特例），靠 `__USE_TIME_BITS64` 和 `__KERNEL__` 分支。内核侧永远走 `:34-42` 那支，`input_event_sec`/`input_event_usec` 两个宏是唯一该用的访问方式 |
| `input_id` | 59-64 | `EVIOCGID`；也内嵌在 `input_dev.id` | `bustype` 取值是 `BUS_*`（同文件 `:254` 起，`BUS_PCI` 0x01） |
| `input_absinfo` | 93-100 | `EVIOCGABS`/`EVIOCSABS`；也是 `dev->absinfo[]` 的元素类型 | 6 个 `__s32`：`value`（当前值）/ `minimum` / `maximum` / `fuzz`（去抖阈值）/ `flat`（死区）/ `resolution`（单位每 mm 或每度） |
| `input_keymap_entry` | 117-124 | `EVIOCGKEYCODE_V2`/`EVIOCSKEYCODE_V2` | `flags`/`len`/`index`/`keycode` + `__u8 scancode[32]`。`INPUT_KEYMAP_BY_INDEX`（`:118`）表示按 index 查而不是按 scancode |
| `input_mask` | 126-130 | `EVIOCGMASK`/`EVIOCSMASK` | `type` + `codes_size` + `__u64 codes_ptr`。★ **指针写成 `__u64` 而不是指针类型**，正是为了让 32/64 位共用一个 ioctl 号——input 里少见的「不需要 compat 处理」的例子 |

★ `input_absinfo` 里的 `value` 字段在**写**方向（`EVIOCSABS`）是被核心接受并直接覆盖当前值的。这是用户态能够「不经过硬件就改变一个绝对轴的当前值」的唯一入口，也是 uinput 之外唯一能污染真实设备状态的路子。

### §2.6 `struct input_mt` / `struct input_mt_slot` — 多点触控状态

`include/linux/input/mt.h`：

```c
/* mt.h:27-31 */
struct input_mt_slot {
	int abs[ABS_MT_LAST - ABS_MT_FIRST + 1];
	unsigned int frame;
	unsigned int key;
};

/* mt.h:43-50 */
struct input_mt {
	int trkid;
	int num_slots;
	int slot;
	unsigned int flags;
	unsigned int frame;
	int *red;
	struct input_mt_slot slots[];
};
```

`abs[]` 的尺寸算出来是 **14**：`ABS_MT_FIRST` = `ABS_MT_TOUCH_MAJOR` = 0x30，`ABS_MT_LAST` = `ABS_MT_TOOL_Y` = 0x3d（两个别名定义在 `include/linux/input.h:12-13`，轴本体在 `include/uapi/linux/input-event-codes.h:881` 和 `:894`）。**索引方式是 `abs[code - ABS_MT_FIRST]`**，见 `mt.h:62`。

| 字段 | 说明 |
|---|---|
| `trkid` | 下一个要发的 tracking id。单调递增，到 `TRKID_MAX`（0xffff，`mt.h:13`）回绕 |
| `num_slots` | 槽位数，`input_mt_init_slots()` 定死，终生不变（§1.0.4） |
| `slot` | **当前正在传输的槽位号**，不是「活动槽位数」。每次 `input_mt_slot()` 改它 |
| `flags` | `INPUT_MT_POINTER` 0x0001 / `_DIRECT` 0x0002 / `_DROP_UNUSED` 0x0004 / `_TRACK` 0x0008 / `_SEMI_MT` 0x0010（`mt.h:15-19`） |
| `frame` | `input_mt_sync_frame()` 每调一次加一。槽位的 `frame` 和它相等就表示「这一帧被更新过」（`input_mt_is_used()`，`mt.h:70`） |
| `red` | 内核内轨迹跟踪用的代价矩阵，只在 `INPUT_MT_TRACK` 时分配 |
| `slots[]` | 柔性数组，和 `input_mt` 本体一次 `kzalloc` 出来 |

★ **`frame` 这个字段在两个结构里各有一个，含义不同**：`mt->frame` 是「现在是第几帧」，`slot->frame` 是「这个槽位上次被更新是第几帧」。两者相等 = 活跃于本帧。这套「用帧号代替显式的 dirty 标记」的写法省掉了每帧清标记的循环，代价是回绕（`unsigned int`，实际不会碰到）。

★ `slot->key` 是**驱动私有的槽位标签**，核心只在 `input_mt_get_slot_by_key()` 里比较它，从不解释它的含义。硬件自己带触点 ID 的设备（多数 I2C 触摸屏）用它把硬件 ID 映射到内核槽位。

### §2.7 `struct ff_device` — 力反馈后端

`include/linux/input.h:546-566`。它挂在 `dev->ff` 上，由 `input_ff_create()` 一次分配（本体 + `effects[]` + `effect_owners[]`）：

| 字段 | 行 | 说明 |
|---|---|---|
| `upload` / `erase` | 547-549 | 装载 / 擦除一个效果。**可以睡眠**（在 `ff->mutex` 下调用） |
| `playback` / `set_gain` / `set_autocenter` | 551-553 | ★ **`:542-544` 的注释明确写了这三个在 `dev->event_lock` 自旋锁 + 关中断下调用，不许睡眠**——这是本结构最容易违反的契约 |
| `destroy` | 555 | `input_ff_destroy()` 时回调，用来释放 `private` |
| `private` | 557 | 后端私有数据（ff-memless 在这里放它的定时器状态） |
| `ffbit` | 559 | ★ **和 `dev->ffbit` 是两张位图，而且拷贝是单向的**：`input_ff_create()` 把 `dev->ffbit` 逐位拷进 `ff->ffbit`（`ff-core.c:344-345`），**不清空 `dev->ffbit`**；接着如果 `ff->ffbit` 里有 `FF_PERIODIC`，它会往 `dev->ffbit` 里**加一位** `FF_RUMBLE`（`:348-349`，用周期效果模拟震动）。所以调完之后 `dev->ffbit` 可能比你声明的多一位，`ff->ffbit` 反而是你声明的原样 |
| `mutex` | 561 | 保护 `effects[]` 和 `effect_owners[]` |
| `max_effects` | 563 | 槽位数 |
| `effects` | 564 | `struct ff_effect *`，效果表 |
| `effect_owners[]` | 565 | 柔性数组 `struct file *`。★ **全子系统唯一按 `struct file *` 记所有权的地方**（`01-api.md` §4 的 ★） |

### §2.8 `struct input_device_id` — 匹配表项

`include/linux/mod_devicetable.h:348-369`。它不在 `input.h` 里，因为 `scripts/mod/file2alias.c` 也要用它生成模块别名。

结构 = `flags`（哪些字段参与匹配）+ 四个 id 字段（bustype/vendor/product/version，`:352-355`）+ **10 张位图**（`:357-366`，和 `input_dev` 的 9 张能力 + 1 张属性一一对应）+ `driver_info`（`:368`）。

`flags` 的取值在 `:332-346`，14 个 `INPUT_DEVICE_ID_MATCH_*`：BUS 1 / VENDOR 2 / PRODUCT 4 / VERSION 8 / EVBIT 0x0010 / KEYBIT 0x0020 / RELBIT 0x0040 / ABSBIT 0x0080 / **MSCIT 0x0100** / LEDBIT 0x0200 / SNDBIT 0x0400 / FFBIT 0x0800 / SWBIT 0x1000 / PROPBIT 0x2000。

★ **`INPUT_DEVICE_ID_MATCH_MSCIT` 是内核里的一个拼写错误**（应为 `MSCBIT`），`include/linux/mod_devicetable.h:341`。全树只有两处出现：定义处和 `scripts/mod/file2alias.c:790`。**input 核心自己一次都没用过它**——`match` 逻辑对 `mscbit` 的判断走的是别的路径。想在 `id_table` 里按 `mscbit` 匹配的人会先被这个名字绊一下。

```bash
grep -rn 'INPUT_DEVICE_ID_MATCH_MSCIT' --include=*.c --include=*.h .   # 2 行：定义 + file2alias.c
```

★ 位图字段用的是 `kernel_ulong_t ...[<X>_MAX / BITS_PER_LONG + 1]`，**和 `input_dev` 那边的 `BITS_TO_LONGS(<X>_CNT)` 不是同一个表达式**。两者在当前所有 `_MAX` 取值下结果相同，但它们各自的上界宏也不同（`INPUT_DEVICE_ID_KEY_MAX` 对 `KEY_MAX`）。**匹配时核心是拿两张不同尺寸约定的位图做 `bitmap_subset()`**，这一点在 `03-internals.md` Part A 的匹配一节展开。

---

## §3 全树 12 个 `input_handler` 注册点

这是**整个内核里所有的事件消费者**，一个不多一个不少。★ **不能只用「找静态实例」这一条命令数**——树里有一个 handler 是内嵌在别的结构体里、逐字段赋值后注册的，designated-initializer 的 grep 抓不到它。要数全必须以「谁调了 `input_register_handler()`」为准：

```bash
# 口径 A：静态实例定义 —— 11 行，漏掉动态注册的那个
grep -rn --include=*.c 'struct input_handler [a-z_]* = {' .
# 口径 B：注册调用点 —— 13 行，其中 1 行是 input.c 里的函数定义本身，故 12 个注册点
grep -rnE --include=*.c '\binput_register_handler\(' . | grep -v EXPORT_SYMBOL
```

| 实例 | 位置 | 回调组合 | 传统次设备号 | 作用 |
|---|---|---|---|---|
| `evdev_handler` | `drivers/input/evdev.c:1420-1429` | `event` + `events` + connect/disconnect | 有，`EVDEV_MINOR_BASE` | `/dev/input/eventN`，现代用户态的唯一正路 |
| `mousedev_handler` | `drivers/input/mousedev.c:1053-1061` | `event` | 有，`MOUSEDEV_MINOR_BASE` | `/dev/input/mouseN`，老式 PS/2 协议模拟 |
| `joydev_handler` | `drivers/input/joydev.c:1076-1085` | `event` + `match` | 有，`JOYDEV_MINOR_BASE` | `/dev/input/jsN`，老式摇杆接口 |
| `input_leds_handler` | `drivers/input/input-leds.c:197-203` | `event` | 无 | 把 `EV_LED` 事件桥到 LED 子系统，注册出 `input3::capslock` 之类的 `led_classdev`（名字由 `kasprintf("%s::%s", …)` 拼成，`input-leds.c:126-128`） |
| `evbug_handler` | `drivers/input/evbug.c:81-87` | `event` | 无 | 调试用：把每个事件 `printk` 出来。见 `07-debug.md` |
| `apmpower_handler` | `drivers/input/apm-power.c:99-105` | `event` | 无 | `KEY_SUSPEND` 触发 APM 挂起。**arm64 上不涉及**（APM 是 x86 遗产） |
| `kbd_handler` | `drivers/tty/vt/keyboard.c:1643-1651` | `event` + `match` + `start` | 无 | VT 控制台键盘层：把 keycode 变成 tty 输入 |
| `sysrq_handler` | `drivers/tty/sysrq.c:1029-1035` | **`filter`** | 无 | Magic SysRq。★ 只有 `filter`，没有 `event` |
| `kgdboc_reset_handler` | `drivers/tty/serial/kgdboc.c:96-101` | **只有 connect/disconnect** | 无 | ★ 它一个事件回调都没有——它只想知道「有哪些键盘存在」，好在进 kgdb 时把它们的状态复位 |
| `mac_hid_emumouse_handler` | `drivers/macintosh/mac_hid.c:154-160` | **`filter`** | 无 | 单键 Mac 键盘模拟三键鼠标。arm64 不涉及 |
| `rfkill_handler` | `net/rfkill/input.c:308-315` | `event` + `start` | 无 | `KEY_WLAN`/`KEY_BLUETOOTH` 等切换 rfkill 开关 |
| `dualsense_mixer_elem_info.ih` | 声明 `sound/usb/mixer_quirks.c:545`，赋值 `:760-766` | `event` + `match` + `start` + connect/disconnect | 无 | PS5 DualSense 手柄的耳机/麦克风插拔检测：把 `EV_SW` 的 `SW_HEADPHONE_INSERT`/`SW_MICROPHONE_INSERT` 变成 ALSA jack 控件的通知 |

四条从这张表读出来的结论：

1. **只有 3 个 handler 有传统次设备号**，而且都在 `drivers/input/` 里——就是那三个字符设备。剩下 9 个是纯内核内消费者，用户态完全看不见它们
2. **`filter` 只有 2 个使用者**（sysrq、mac_hid），且两个都**不**同时实现 `event`。核心并不禁止同时实现（`drivers/input/input.c:112-130` 是先跑 filter 就地压缩 `vals[]`，存活的再交给 `events`/`event`），只是树里没人这么用。重跑：`grep -rnE --include=*.c '\.filter[[:space:]]*=' . | grep -vE '^\./(tools|samples)/'` 里只有 `drivers/tty/sysrq.c:1030` 和 `drivers/macintosh/mac_hid.c:155` 两行是 `input_handler` 的（其余命中属于同名字段的别的结构体）
3. **6 个 handler 住在 `drivers/input/` 之外**，跨了 tty、macintosh、net、sound 四个子系统。所以「查一个键为什么被吞了」不能只在 `drivers/input/` 里找
4. **`start` 只有 3 个使用者**：`kbd_handler`、`rfkill_handler`、DualSense 的 `ih`

★ **最后一行是全树唯一一个不是静态实例的 handler**，而且它有三重特殊性：(a) 它的 `id_table` 也是运行期填的（`sound/usb/mixer_quirks.c:750-758`，VID/PID 从 USB 设备现取），不是编译期常量数组，所以 `MODULE_DEVICE_TABLE(input, …)` 那条 modalias 路径对它完全不适用；(b) 整块代码包在 `#if IS_REACHABLE(CONFIG_INPUT)`（`:531`–`:788`）里，即 input 编成模块而 snd-usb-audio 编进内核时这段会被整体去掉；(c) 它的注销条件是 `if (mei->ih.event)`（`:708-709`）——注册失败时把 `event` 清成 `NULL`（`:772`）当作「没注册过」的标记。**数「全树有几个 handler」时按静态实例数会少一个，这就是本节给两条命令而不是一条的原因。**

★ **`evdev` 是唯一同时填了 `event` 和 `events` 的 handler，而它的 `event` 是死代码**：`drivers/input/input.c:126-130` 优先调 `events`；`evdev_event`（`evdev.c:314-320`）自己也只是包装成一个单元素数组再调 `evdev_events`。留着它大概是为了让这个结构体看起来完整——但读代码时不要顺着 `evdev_event` 去找主路径，主路径在 `evdev_events`（`evdev.c:291`）。

---

## §4 全局变量与锁

### §4.1 `drivers/input/input.c` 的文件级状态

| 符号 | 行 | 类型 | 说明 |
|---|---|---|---|
| `INPUT_MAX_CHAR_DEVICES` | 34 | 宏 = 1024 | 次设备号总空间 |
| `INPUT_FIRST_DYNAMIC_DEV` | 35 | 宏 = 256 | 动态分配起点。0-255 留给 `legacy_minors` |
| `input_ida` | 36 | `DEFINE_IDA` | 次设备号分配器 |
| `input_dev_list` | 38 | `LIST_HEAD` | 所有已注册设备 |
| `input_handler_list` | 39 | `LIST_HEAD` | 所有已注册 handler |
| `input_mutex` | 47 | `DEFINE_MUTEX` | 见 §4.2 |
| `input_value_sync` | 49 | `static const` | `{ EV_SYN, SYN_REPORT, 1 }` 单例，见 §2.4 |
| `input_max_code[EV_CNT]` | 51-60 | `static const` 数组 | 每个事件类型的合法 code 上界。**只填了 8 类**（KEY/REL/ABS/MSC/SW/LED/SND/FF）；没填的类型（如 `EV_SYN`、`EV_REP`、`EV_PWR`）取值 0，`input_event()` 里对它们的越界检查因此形同放行 |
| `proc_bus_input_dir` | 1105 | `proc_dir_entry *` | `/proc/bus/input/`，`input_proc_init()` 在 `:1323` 建它 |
| `input_devices_state` | 1107 | `static int` | 一个「拓扑变了」的世代号。`/proc/bus/input/devices` 的 `poll()` 靠它判断要不要唤醒 |
| `input_dev_attr_groups[]` | 1651 | 数组 | 三组 sysfs 属性：基本 / id / capabilities（分别在 `:1549`、`:1576`、`:1646`） |
| `input_dev_pm_ops` | 1972 | `dev_pm_ops` | suspend/resume/freeze/poweroff 时把设备状态复位 |
| `input_dev_type` | 1981-1988 | `device_type` | 挂 `input_dev_attr_groups`、`input_dev_release`、`input_dev_uevent` |
| `input_class` | 1995-1998 | `struct class` | `.name = "input"`，`.devnode = input_devnode`（`:1990`，拼出 `input/<devname>` → 这就是节点落在 `/dev/input/` 子目录下的原因）。`EXPORT_SYMBOL_GPL` 在 `:1999` |

★ **`input_max_code[]` 只覆盖 8 类，是一个真实的检查空洞**。`grep -c` 能看到数组只有 8 个初始化项（`drivers/input/input.c:52-59`）。想验证某个事件类型有没有 code 上界检查，看的是这张表里有没有它的条目，而不是「核心应该会检查」。

### §4.2 锁的四个层次

input 的加锁**没有读写锁、没有 `rw_semaphore`**——全树只有互斥锁、自旋锁和一处 RCU。判定命令：

```bash
grep -nE 'DEFINE_MUTEX|DEFINE_SPINLOCK|spinlock_t |struct mutex |rwlock_t |struct rw_semaphore ' drivers/input/*.c drivers/input/*.h
# 10 行输出，全部是 mutex / spinlock_t，无 rwlock_t、无 rw_semaphore
```

| 层 | 锁 | 位置 | 保护什么 | 上下文 |
|---|---|---|---|---|
| 全局 | `input_mutex` | `drivers/input/input.c:47` | `input_dev_list` + `input_handler_list` | 可睡眠 |
| 每设备 | `dev->event_lock` | `include/linux/input.h:192` | 事件路径的一切：`vals[]`/`num_vals`、状态位图、`grab`、`timestamp[]`；也是 `dev->event` 回调和 ff 的 `playback`/`set_gain`/`set_autocenter` 的执行上下文 | **自旋 + 关中断，不许睡眠** |
| 每设备 | `dev->mutex` | `include/linux/input.h:193` | 配置路径：`h_list` 增删、`users` 计数、`open`/`close` 回调、`inhibited` 切换 | 可睡眠 |
| 每 ff 设备 | `ff->mutex` | `include/linux/input.h:561` | `ff->effects[]` + `ff->effect_owners[]`；`upload`/`erase` 回调在它下面跑 | 可睡眠 |
| 每字符设备（×3） | `client_lock`（自旋）+ `mutex` | evdev `:33-34` / mousedev `:63-64` / joydev `:40-41` | `client_list`：自旋锁给事件路径读，mutex 给 open/close 写 | 见备注 |
| 每 client（×3） | `buffer_lock` / `packet_lock`（自旋） | evdev `:44` / joydev `:62` / mousedev `:101`（叫 `packet_lock`） | 环形缓冲的 head/tail | 自旋 |

★ **`input_mutex` 的注释（`drivers/input/input.c:41-46`）明确说它「顺带让 `input_[un]register_device` 和 `input_[un]register_handler` 互斥，从而简化 handler 实现的加锁」**——这是一处**故意牺牲并发换简单**的设计。后果：系统启动时几百个输入设备注册是**完全串行**的，而且任何一个驱动的 `probe` 在这把锁里睡久了，会拖住其他所有设备的注册。

★ **`event_lock` 是自旋锁 + 关中断，而 `dev->event` 回调在它下面执行**，这就是为什么「在 `event` 回调里写 I2C」是个经典 bug：I2C 传输要睡眠。需要睡眠的驱动必须把工作推到 workqueue。同一条约束以更明确的措辞写在 `include/linux/input.h:542-544`（ff 的三个回调）。

★ **`include/linux/input.h` 里唯一带 `__rcu` 标注的字段是 `grab`（`:190`）**。为什么给它用 RCU：事件分发路径要在**每个事件**上读一次（`drivers/input/input.c:153` 的 `rcu_dereference`），用锁代价太高，而变更极罕见（用户态 `EVIOCGRAB`）——典型的读多写极少。

★ 但**「只有一个 `__rcu` 字段」不等于「只有一处 RCU」**：`dev->h_list` 同样按 RCU 规则维护——插入用 `list_add_rcu`/`list_add_tail_rcu`（`drivers/input/input.c:2638`、`:2640`），分发路径用 `list_for_each_entry_rcu` 遍历（`:157`），配置路径则在 `dev->mutex` 下用普通遍历（`:593`、`:772`、`:1207`）。`list_head` 的 RCU 用法靠**调用的原语**表达，不靠类型标注，所以 grep `__rcu` 找不到它。evdev 另有一个自己的 `struct evdev_client __rcu *grab`（`evdev.c:31`），和 `input_dev.grab` 是两层各自独立的抢占。

★ 三个字符设备各自的 `client_lock`（自旋）+ `mutex` 这一对，形态是**同一份 `client_list` 用两把锁**：写者要同时拿 mutex（排他配置）和 client_lock（挡住并发的事件分发），读者在事件路径上只拿 client_lock。evdev 实际上更进一步用了 RCU 遍历（`evdev.c:298` 的 `rcu_read_lock()`），`client_lock` 退化成只保护写侧。**三个 handler 三份几乎相同的实现**，是这一层最明显的重复代码——`04-extensions.md` §1 会把三者逐项对照。

---

## §5 一个事件的完整数据流

从驱动调 `input_event()` 到用户态 `read()` 拿到 `struct input_event`，中间的数据形态变了三次：

```
 ① 驱动侧                    ② 核心批处理                ③ handler 侧            ④ 用户态
 ─────────                   ────────────                ──────────              ────────

 input_report_key(dev,        ┌────────────────────────────┐
   KEY_A, 1)                  │ input_handle_event()  :390 │
 input_report_key(dev,   ───▶ │  input_get_disposition()   │ 判定这个事件的去向
   KEY_B, 1)                  │      ↓ disposition         │
 input_sync(dev)              │  input_event_dispose() :347│
                              │   PASS_TO_DEVICE → dev->event()   ← LED/SND/FF 出方向
   全程持 dev->event_lock      │   PASS_TO_HANDLERS → vals[] :366  │
   （自旋 + 关中断）           │   FLUSH / 逼近 max_vals → 刷出     │
                              └──────────┬─────────────────┘
                                         │  FLUSH（SYN_REPORT）
                                         │  或 num_vals >= max_vals - 2
                                         ▼
                              ┌──────────────────────┐
                              │ input_pass_values()  │  :140
                              │  读 dev->grab（RCU）  │  :153
                              └──────────┬───────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │ grab 非 NULL？      │                    │
                    ▼ 是                 ▼ 否                 │
             只给那一条 handle      遍历 dev->h_list          │
                    └────────────────────┬────────────────────┘
                                         ▼
                              ┌──────────────────────────────┐
                              │ 逐 handle：                   │
                              │  handler->filter() 就地压缩   │  ← sysrq 在这里吞掉按键
                              │  count == 0 就 return         │
                              │  handler->events(vals, count) │  ← 优先
                              │  else handler->event(逐个)     │
                              └──────────┬───────────────────┘
                                         ▼
                              ┌──────────────────────────────┐
                              │ evdev_events()                │
                              │  input_get_timestamp() 取时戳  │  ← 时间戳在这一刻才拼上
                              │  逐 client 写环形缓冲          │
                              │  wake_up_interruptible()      │
                              └──────────┬───────────────────┘
                                         ▼
                                  read(fd, ...) 拿到
                                  struct input_event
                                  = 时间戳 + type/code/value
```

三个「形态变化点」值得单独记住：

| 变化 | 在哪 | 影响 |
|---|---|---|
| 散装参数 → `struct input_value` | `input_event_dispose()` 写 `dev->vals[]`（`drivers/input/input.c:360`、`:366`） | 从这里开始事件是**成批**的，单个事件不再是独立单位 |
| 一批 → 每 handler 一份**独立的副本语义** | `input_pass_values()` 逐 handle 调用 | ★ 但 `vals[]` 本身**不复制**：`filter` 的就地压缩会改动这份共享数组，所以 filter handler 的顺序（在 `h_list` 头部）不是排序偏好而是正确性要求 |
| 无时间戳 → 有时间戳 | `evdev_events()` 里 `input_get_timestamp()`（`evdev.c:296`） | 时间戳是**每设备一份、每帧一个**，不是每事件一个（§2.4） |

★ **空帧会被静默吃掉**：`drivers/input/input.c:372-375` 的 FLUSH 分支里是 `if (dev->num_vals >= 2)` 才真的调 `input_pass_values()`。`num_vals == 1` 意味着这一批里只有那个 `SYN_REPORT` 自己，核心直接把 `num_vals` 清零走人。所以**连着调两次 `input_sync()` 的第二次是空操作**，用户态不会看到两个 `SYN_REPORT`。想靠「多发一个同步事件」去戳醒用户态的做法在这里行不通。

★ 第二行那个 ★ 是本子系统一个不显眼的耦合：`filter` 返回 `true` 时核心把后面的元素往前搬（`drivers/input/input.c:113-120`），**改的是 `dev->vals[]` 这块共享内存**。所以排在 filter 后面的 handler 看到的是被删改过的数组。如果哪天有人把 filter handler 插到了 `h_list` 尾部，前面的 handler 已经处理完了，filter 的「吞掉」就完全失效——这就是为什么 `input_register_handle()` 必须按 `filter` 是否存在来决定插头还是插尾。

---

## 本篇引用的其他文档

| 去处 | 找什么 |
|---|---|
| `01-api.md` | 这些结构上能调哪些函数、错误码语义、代码地图 |
| `03-internals.md` | 这些结构在时间上怎么被建起来和拆掉、匹配算法、`max_vals` 的静默劈帧 |
| `04-extensions.md` | evdev / mousedev / joydev / uinput / 力反馈后端各自的私有结构 |
| `07-debug.md` | `/proc/bus/input/devices` 每一行对应本篇哪个字段、evbug 怎么用 |
| `08-feynman.md` | 「为什么 input 不是一条总线」「槽位和手指的区别」的大白话版 |
