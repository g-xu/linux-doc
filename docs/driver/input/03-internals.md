---
title: 内部实现
---

# input 子系统内部实现

- 版本：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
- 本篇管什么：把 input 核心的每一条主流程按"谁调用谁"的真实次序摊开，逐段对着源码讲。**每个 Part A 章节标题后面都带该函数的完整行号区间**，可以直接拿去 `sed -n` 打开。
- 本篇只承诺一条主轴 —— **Part A 是时间线**（从 `input_init()` 到设备释放），**Part B 是横切机制**（不属于任何单一时刻的东西：锁、去抖、重复、键码、sysfs），**Part C 是评价**（源码里的设计债、官方文档与代码冲突、arm64 上不相关的部分）。引用本篇时请带上 A/B/C 前缀，这样"在哪条轴上"这个信息就自带了。
- 结构体字段的含义和行号在 `02-datastructures.md`；对外 API 的签名和错误码在 `01-api.md`。本篇不重复它们，只讲"这些字段在什么时刻被谁改成什么值"。

---

## §0 导航

### §0.1 分层图

```
        用户态
   ┌──────────────────────────────────────────────────────┐
   │  /dev/input/eventN   /dev/input/mouseN   /dev/input/jsN │
   └──────────────────────────────────────────────────────┘
                          ↑ 字符设备（04 篇）
   ┌──────────────────────────────────────────────────────┐
   │  handler 层：evdev / mousedev / joydev / input-leds /  │
   │              keyboard / sysrq / rfkill-input / …      │
   │              （全树 12 个 input_handler，见 02 §3）    │
   └──────────────────────────────────────────────────────┘
              ↑ input_handle（连线）      ↓ handler->connect()
   ┌──────────────────────────────────────────────────────┐
   │  核心：drivers/input/input.c                          │
   │    两张全局链表 + 一把全局互斥锁                       │
   │    事件路由（disposition 决策 → 批量投递）             │
   │    状态镜像（key/led/snd/sw 四张位图）                 │
   │    sysfs / procfs / uevent / modalias                 │
   └──────────────────────────────────────────────────────┘
              ↑ input_event()            ↓ dev->open/close/event/flush
   ┌──────────────────────────────────────────────────────┐
   │  设备驱动层：474 处 input_register_device() 调用        │
   │    （触摸屏 / 键盘 / 鼠标 / serio / 遥控 / 传感器按键） │
   └──────────────────────────────────────────────────────┘
                          ↑ 真实硬件中断 / 轮询
```

★ 图里唯一一条**双向**的边是核心和 handler 之间：核心通过 `handler->connect/disconnect/events/filter/start` 往上调，handler 通过 `input_register_handle/input_open_device/input_grab_device` 往下调。设备驱动那一侧是单向的：驱动只往上报事件，核心只往下调那四个回调。

### §0.2 生命周期时间线

```
 ①  input_init()                         subsys_initcall，注册 class + procfs + 字符设备号区间
       │
 ②  input_allocate_device()              驱动分配 input_dev，此刻 dev->vals 还是 NULL
       │
 ③  input_set_capability() / _abs_params  驱动声明能力位图
       │
 ④  input_register_device()               ← 分水岭：清洗位图、算 max_vals、device_add、上全局链表
       │                                     此后 input_event() 才可能到达 handler
       │
 ⑤  input_attach_handler() × N            对每个已注册 handler 跑一次匹配
       │
 ⑥  handler->connect() → input_register_handle()   建立 input_handle，挂两条链
       │
 ⑦  input_open_device()                   用户 open() 触发，第一个用户才调 dev->open()
       │
 ⑧  input_event() … input_pass_values()   稳态：事件在这里循环
       │
 ⑨  input_close_device()                  最后一个用户走了才调 dev->close()
       │
 ⑩  input_unregister_device()             逆序：标 going_away、放 key、断所有连线、下链表、device_del
       │
 ⑪  input_dev_release()                   最后一个引用消失才真正 kfree
```

②→④ 之间驱动**可以**调 `input_event()`（`drivers/input/input.c:416-421` 的注释明确允许，用来播种开关初值），但事件到不了任何 handler —— 因为 `dev->vals` 是 NULL，`input_event_dispose()` 在 `drivers/input/input.c:353-354` 直接返回。状态位图（`dev->key`/`dev->sw`）却已经被更新了，这就是"播种"能起作用的原因。

### §0.3 哪些内容不在本篇

| 你想找的 | 去哪篇 |
| --- | --- |
| 某个 API 的签名、参数、返回什么错误码 | `01-api.md` |
| `input_dev` 某个字段是什么意思、在第几行 | `02-datastructures.md` |
| evdev / mousedev / joydev / uinput 内部怎么缓冲和唤醒 | `04-extensions.md` |
| 我要写一个输入驱动，代码骨架长什么样 | `05-usage.md` |
| DTS 里的属性谁消费 | `06-devicetree.md` |
| 出问题了看哪个 sysfs / dmesg / tracepoint | `07-debug.md` |
| 为什么要这么设计（大白话） | `08-feynman.md` |
| 每个数字和行号是怎么取的 | `99-evidence.md` |

---

# Part A 时间线

## A1 `input_init()` —— 子系统自己怎么起来（`drivers/input/input.c:2733-2759`）

三件事，顺序固定，任一失败就逆序回滚：

| 步骤 | 代码 | 失败去哪 |
| --- | --- | --- |
| 注册 `input_class` | `class_register(&input_class)` | 直接返回 |
| 建 procfs 目录 | `input_proc_init()` | `fail1`：`class_unregister()` |
| 占字符设备号区间 | `register_chrdev_region(MKDEV(INPUT_MAJOR, 0), INPUT_MAX_CHAR_DEVICES, "input")` | `fail2`：`input_proc_exit()` 后落到 `fail1` |

关键常量：主设备号 `INPUT_MAJOR` = 13，一次占满 `INPUT_MAX_CHAR_DEVICES` = 1024 个次设备号（`drivers/input/input.c:34`）。也就是说 input 子系统一上来就把 13:0 到 13:1023 全部占下，后面 evdev / mousedev / joydev 分配次设备号是在这个区间内部再分（见 B4）。

初始化等级是 `subsys_initcall`（`drivers/input/input.c:2769`）。★ 这一点解掉了一个老问题：`Documentation/input/input.rst` 里说 input 模块"必须在其他 input 模块之前加载"，在 6.1 上这句话已经不需要读者操心了 —— 编进内核时 `subsys_initcall` 保证核心先跑，编成模块时符号依赖保证 `input.ko` 先加载。这条冲突记在 C2。

`input_class` 本身在 `drivers/input/input.c:1995-1998`：只有 `.name = "input"` 和 `.devnode = input_devnode`。`input_devnode()`（`drivers/input/input.c:1990-1993`）返回 `kasprintf(GFP_KERNEL, "input/%s", dev_name(dev))`，这是 `/dev/input/` 这一层子目录的唯一来源 —— 不是 udev 规则，是内核给的。

## A2 分配：`input_allocate_device()` / `devm_input_allocate_device()`（`drivers/input/input.c:2010-2033` / `2075-2098`）

两个入口的差别只在"谁负责释放"，初始化动作是同一套（`drivers/input/input.c:2015-2029`）：

| 动作 | 行号 | 备注 |
| --- | --- | --- |
| `kzalloc(sizeof(*dev))` | `:2015` | 全零起步，所以所有位图和计数天然是空的 |
| `dev.type = &input_dev_type`；`dev.class = &input_class` | `:2017-2018` | ★ **`dev.bus` 从头到尾没人写**，见 `02-datastructures.md` §1.0.1 |
| `device_initialize(&dev->dev)` | `:2019` | 引用计数从 1 开始 |
| `mutex_init(&dev->mutex)`；`spin_lock_init(&dev->event_lock)` | `:2020-2021` | 两把锁的分工见 B1 |
| `timer_setup(&dev->timer, NULL, 0)` | `:2022` | ★ 回调是 NULL，见下 |
| `INIT_LIST_HEAD(&dev->h_list)`；`INIT_LIST_HEAD(&dev->node)` | `:2023-2024` | 一条挂 handle，一条挂全局设备链表 |
| `dev_set_name(&dev->dev, "input%lu", atomic_inc_return(&input_no))` | `:2026-2027` | 静态 `atomic_t input_no` 从 −1 起，所以第一个设备叫 `input0`。这就是 sysfs 里 `/sys/class/input/inputN/` 那个 N，**和 `eventN` 的 N 不是一回事**（后者是次设备号推的，见 B4） |
| `__module_get(THIS_MODULE)` | `:2029` | 与 `input_dev_release()` 里的 `module_put()`（`drivers/input/input.c:1670`）配对：只要还有一个 input_dev 活着，`input.ko` 就卸不掉 |

★ `timer_setup(&dev->timer, NULL, 0)` 在 `drivers/input/input.c:2022` 传的是 **NULL 回调**。定时器在这里就被初始化好了，但没有函数；`dev->timer.function` 要等 `input_enable_softrepeat()` 才被填上（`drivers/input/input.c:2357`）。这是 `input_start_autorepeat()` 要检查 `dev->timer.function` 非空的原因（`drivers/input/input.c:88`）—— 否则会去调一个 NULL。

devm 版本额外做三件事，都在 `drivers/input/input.c:2075-2098` 里：

| 动作 | 效果 |
| --- | --- |
| `devres_alloc(devm_input_device_release, …)` | 挂第一个 devres，负责 `input_put_device()` |
| `input->dev.parent = dev`（`drivers/input/input.c:2091`） | 这是 input_dev 和父总线设备唯一的连接点 |
| `input->devres_managed = true` | 后面 `input_register_device()` 会再挂第二个 devres |

★ 非 devm 路径下 `dev.parent` 要驱动自己赋值，忘了赋值不会报错 —— `/sys/class/input/eventN/device/device/` 那一级就不存在，`dev_warn()` 打印会退化成打在 input_dev 自己身上（`drivers/input/input.c:975` 的 `dev->dev.parent ?: &dev->dev` 就是在兜这个）。

`input_free_device()`（`drivers/input/input.c:2115-2125`）只在**没注册过**的路径上用。注册过的设备要走 `input_unregister_device()`，不能用 free。

## A3 声明能力：`input_set_capability()` / `input_set_abs_params()`（`drivers/input/input.c:2177-2232` / `494-511`）

`input_set_capability()` 的骨架是一个 switch，按 `type` 往对应位图里 `__set_bit(code, …)`，出 switch 时统一 `__set_bit(type, dev->evbit)`（`drivers/input/input.c:2231`）。四个细节值得记：

- **进 switch 之前先查越界**（`drivers/input/input.c:2179-2185`）：`type < EV_CNT && input_max_code[type] && code > input_max_code[type]` 时 `pr_err("%s: invalid code %u for type %u\n", …)` + `dump_stack()` 后**直接 return**，位图一位都不设。★ 但 `input_max_code[]`（`drivers/input/input.c:51-60`）只填了 KEY / REL / ABS / MSC / SW / LED / SND / FF 八项，`EV_SYN` / `EV_REP` / `EV_PWR` 那几项是 0，`&& input_max_code[type]` 这一半就把它们整个跳过了 —— 这三类的 code 越界查不出来。
- `EV_ABS` 分支（`drivers/input/input.c:2196-2199`）**会**顺手调 `input_alloc_absinfo()`，只是不填量程。所以 `input_set_capability(dev, EV_ABS, ABS_X)` 也能让 A4 第 1 步的 `-EINVAL` 检查通过，只是所有轴的 min/max/fuzz/flat 全是 0。
- `EV_PWR` 分支（`drivers/input/input.c:2221-2223`）是空的（注释就写着 `/* do nothing */`），因为 `EV_PWR` 没有对应的能力位图，但出 switch 后 `evbit` 里那一位照样会被设上。
- `default` 分支 `pr_err("%s: unknown type %u (code %u)\n", …)` + `dump_stack()` —— 传错 type 会在 dmesg 里留一份调用栈，不是静默失败。

`input_set_abs_params()`（`drivers/input/input.c:494-511`）的顺序是：先 `__set_bit(EV_ABS, dev->evbit)` 和 `__set_bit(axis, dev->absbit)`（`:499-500`），再 `input_alloc_absinfo()`（`:502`），分配失败就**静默返回**（`:503-504`），成功才填 min/max/fuzz/flat（`:506-510`）。所以调了 `input_set_abs_params()` 就不必再调 `input_set_capability(dev, EV_ABS, axis)`；反过来能过检查但拿不到量程。

`input_alloc_absinfo()`（`drivers/input/input.c:476-491`）按 `ABS_CNT` 个 `struct input_absinfo` 一次性 `kcalloc`，已分配则直接返回。它**没有返回值**：分配失败只 `dev_err()`，源码注释（`:485-489`）明说"留给 `input_register_device()` 去拒绝"。

★ **这是 input 里最容易踩的静默陷阱的一半**：如果驱动直接 `__set_bit(EV_ABS, dev->evbit)`（绕过上面两个 API）而从不给出 absinfo，`input_register_device()` 会在 `drivers/input/input.c:2402-2406` 打印 `Absolute device without dev->absinfo, refusing to register` 并返回 `-EINVAL`。这是核心里唯一一处"能力位图自洽性"的硬检查。另一半（位图被无声清洗）见 A4 第 5 步。

## A4 `input_register_device()` —— 分水岭（`drivers/input/input.c:2394-2491`）

按源码顺序，这个函数做 13 件事：

| # | 行号 | 动作 | 说明 |
| --- | --- | --- | --- |
| 1 | `:2402-2406` | `EV_ABS` 已置但 `dev->absinfo` 为 NULL → `-EINVAL` | 唯一的能力自洽性硬检查 |
| 2 | `:2408-2416` | `devres_managed` 时分配第二个 devres | 失败 `-ENOMEM` |
| 3 | `:2418` | `__set_bit(EV_SYN, dev->evbit)` | 核心替你加上，驱动不用声明 |
| 4 | `:2421` | `__clear_bit(KEY_RESERVED, dev->keybit)` | `KEY_RESERVED` 永远不许出现 |
| 5 | `:2424` | `input_cleanse_bitmasks(dev)` | ★ 见下方 |
| 6 | `:2426-2429` | `input_estimate_events_per_packet()` 估出 `packet_size`，取它和驱动给的 `hint_events_per_packet` 中的**较大者** | 驱动给小了会被抬上去，给大了保留 |
| 7 | `:2430-2431` | `dev->max_vals = dev->hint_events_per_packet + 2`；`kcalloc(max_vals, sizeof(struct input_value))` | 失败跳 `err_devres_free` |
| 8 | `:2441-2442` | `rep[REP_DELAY]` 和 `rep[REP_PERIOD]` **都**为 0 才 `input_enable_softrepeat(dev, 250, 33)` | 默认 250ms 延迟 / 33ms 周期 |
| 9 | `:2444-2448` | `getkeycode`/`setkeycode` 为空时填默认实现 | |
| 10 | `:2450-2451` | 有 poller 时 `input_dev_poller_finalize()` | |
| 11 | `:2453` | `device_add(&dev->dev)` | 失败跳 `err_free_vals`；这一步之后 sysfs 和 uevent 就都出来了 |
| 12 | `:2458` | `pr_info("%s as %s\n", dev->name ?: "Unspecified device", path ?: "N/A")` | 这就是 dmesg 里的 `input: <名字> as <路径>`，`pr_fmt` 在 `drivers/input/input.c:9` 定义为 `KBUILD_BASENAME ": "` |
| 13 | `:2463-2472` | 拿 `input_mutex`（可中断）→ `list_add_tail(&dev->node, &input_dev_list)` → 对每个 handler 跑 `input_attach_handler()` → `input_wakeup_procfs_readers()` | 拿锁失败跳 `err_device_del` |

最后 `:2479` 在 devres 路径上 `devres_add()`。三个错误标签的语义是严格逆序的：`err_device_del` → `device_del()`，`err_free_vals`（`kfree(dev->vals)` 并把 `dev->vals` 置 NULL），`err_devres_free` → `devres_free()`。

★ **第 5 步是另一半静默陷阱。** `input_cleanse_bitmasks()`（`drivers/input/input.c:2275-2285`）通过 `INPUT_CLEANSE_BITMASK` 宏（`drivers/input/input.c:2268-2273`）对 KEY / REL / ABS / MSC / LED / SND / FF / SW 八个类别各跑一次：**如果 `dev->evbit` 里没有 `EV_X` 这一位，就把整张 `dev->xbit` 位图 `memset` 成 0**。也就是说驱动如果 `__set_bit(KEY_A, dev->keybit)` 却忘了 `__set_bit(EV_KEY, dev->evbit)`，注册**照样成功**，只是那张位图在注册的一瞬间被抹平了 —— 没有警告，没有返回码，只有后来"这个键怎么按了没反应"。`input_set_capability()` 之所以每次都顺手设 `evbit`，就是为了让驱动作者不可能忘。

★ **第 7 步的 `+2` 决定了一帧最多能装多少事件。** `max_vals = hint_events_per_packet + 2`，多出来的两格分别留给 `ABS_MT_SLOT` 注入（见 A8）和收尾的 `SYN_REPORT`。当一帧的事件数逼近这个上限时，核心会**劈帧**而不是丢事件 —— 细节在 A8。

★ **第 13 步说明"注册设备"和"注册 handler"是互斥的。** 两者都要拿同一把 `input_mutex`，而 `drivers/input/input.c:41-46` 的注释明确写了这是**故意**的设计：让 register_device 和 register_handler 不可能并发，handler 内部的加锁就能简化。代价是设备注册风暴时会串行化。

## A5 匹配：一个设备该被哪些 handler 接管（`drivers/input/input.c:1000-1066`）

三层嵌套，从里往外读最清楚。

### A5.1 `input_match_device_id()`（`drivers/input/input.c:1000-1033`）

先是四个**由 flags 开关的** id 字段比较（`:1003-1017`）：`MATCH_BUS` → `bustype`、`MATCH_VENDOR` → `vendor`、`MATCH_PRODUCT` → `product`、`MATCH_VERSION` → `version`。没置对应 flag 的字段根本不看。

然后是十个**无条件**的位图子集判断（`:1019-1028`），任一不成立就整体 false：

```c
if (!bitmap_subset(id->evbit,   dev->evbit,   EV_MAX) ||
    !bitmap_subset(id->keybit,  dev->keybit,  KEY_MAX) ||
    ...
    !bitmap_subset(id->propbit, dev->propbit, INPUT_PROP_MAX)) {
        return false;
}
```

语义是"**id 要求的每一位，设备都得有**"。★ 注意这十行**没有** flags 开关 —— 那 10 个 `INPUT_DEVICE_ID_MATCH_*BIT` 标志（`include/linux/mod_devicetable.h:332-346`）在核心的匹配逻辑里**一个都没用到**，它们只被 `scripts/mod/file2alias.c` 拿去生成 modalias 字符串。位图全零的 id 条目对所有设备都是子集，所以"空条目匹配一切"是子集语义的自然结果，不是特例。

★ **一个真实的 off-by-one**：`bitmap_subset()` 的第三个参数是要比较的位数，传进来的是 `X_MAX` 而不是 `X_CNT`，所以只比较 bit 0 到 `X_MAX-1`，**每一类最后那个 code（bit `X_MAX` 自己）不参与比较**。如果有 handler 想靠 `KEY_MAX`（0x2ff）这一位筛设备，那一位会被无声忽略、匹配照样通过。本树 12 个 handler 的 id_table 都没踩到这条边界：

```bash
H="drivers/input/evdev.c drivers/input/mousedev.c drivers/input/joydev.c \
   drivers/input/input-leds.c drivers/input/evbug.c drivers/input/apm-power.c \
   drivers/tty/vt/keyboard.c drivers/tty/sysrq.c drivers/tty/serial/kgdboc.c \
   drivers/macintosh/mac_hid.c net/rfkill/input.c"
grep -n 'BIT_MASK([A-Z]*_MAX)' $H | wc -l      # → 0
```

`input_match_device_id()` 是 `EXPORT_SYMBOL(input_match_device_id)`，驱动/handler 可以自己拿去复用。

### A5.2 `input_match_device()`（`drivers/input/input.c:1036-1049`）

```c
for (id = handler->id_table; id->flags || id->driver_info; id++) {
        if (input_match_device_id(dev, id) &&
            (!handler->match || handler->match(handler, dev))) {
                return id;
        }
}
return NULL;
```

★ **循环的终止条件是"`flags` 和 `driver_info` 同时为 0"**，不是 NULL 指针，也不是数组长度。所以 id_table 必须以 `{ }` 收尾，而且**一个 flags 为 0 但 driver_info 非 0 的条目是合法的、并且匹配一切设备** —— evdev 就是这么写的：

```c
static const struct input_device_id evdev_ids[] = {
        { .driver_info = 1 },   /* Matches all devices */
        { },                    /* Terminating zero entry */
};
```

`drivers/input/evdev.c:1413-1416`。这也解释了为什么每一个 input 设备都会长出一个 `/dev/input/eventN`：evdev 的 id_table 第一条就无条件命中。

`handler->match` 是**位图匹配之后的第二道门**，只有前一道过了才会调（全树只有 `joydev` 提供 `match`，用来把不像手柄的 ABS 设备踢掉）。

### A5.3 `input_attach_handler()`（`drivers/input/input.c:1051-1066`）

匹配不上返回 `-ENODEV`；匹配上就调 `handler->connect(handler, dev, id)`。**`-ENODEV` 之外的错误会 `pr_err("failed to attach handler %s to device %s, error: %d\n", …)`**，但返回值在 `input_register_device()` 那一侧（`drivers/input/input.c:2469-2470`）**被完全丢弃** —— 一个 handler 的 connect 失败不会让设备注册失败，只会让这个设备少一个接入点。

★ 这一条是 input 里"部分失败"的典型形态：`/dev/input/eventN` 没出来，但 `dmesg` 里有 `failed to attach handler evdev to device input5, error: -12`，而驱动的 probe 返回 0。排查路径见 `07-debug.md`。

## A6 连线：`input_register_handle()`（`drivers/input/input.c:2619-2656`）

由 handler 的 `connect()` 回调调用，把一个 `input_handle` 同时挂到两条链上：

| 步骤 | 行号 | 锁 | 说明 |
| --- | --- | --- | --- |
| 拿 `dev->mutex`（可中断） | `:2629-2631` | — | 源码注释：防和 `input_release_device()` 竞争 |
| `handler->filter` 非空 → `list_add_rcu(&handle->d_node, &dev->h_list)`（**头**） | `:2637-2638` | `dev->mutex` | filter 型必须先跑 |
| 否则 `list_add_tail_rcu(…)`（**尾**） | `:2639-2640` | `dev->mutex` | |
| 放 `dev->mutex` | `:2642` | — | |
| `list_add_tail_rcu(&handle->h_node, &handler->h_list)` | `:2650` | **无锁** | 源码注释：connect 和 disconnect 互斥（都在 `input_mutex` 下），所以不需要另一把锁 |
| `handler->start(handle)` | `:2652-2653` | 无锁 | 让 handler 把设备当前状态同步一遍 |

★ **"filter 进链表头"是 sysrq 和 mac_hid 能够吃掉事件的全部机制**。`input_pass_values()` 顺着 `dev->h_list` 从头往尾走，filter 型 handler 在 `input_to_handler()` 里就地压缩 `vals[]`（见 A8），被压掉的事件后面的普通 handler 根本看不到。全树只有 2 个 handler 有 `filter`（`drivers/tty/sysrq.c:1029-1035` 和 `drivers/macintosh/mac_hid.c:154-160`），见 `02-datastructures.md` §3。

★ 注意两条链的挂载**不在同一把锁下**，而且 `h_node` 那一步压根没有锁。这个设计成立的唯一理由是 `input_mutex` 把 connect/disconnect 串行化了 —— 也就是 A4 第 13 步那个"故意的"串行化在这里被兑现成了简化。

## A7 打开：`input_open_device()`（`drivers/input/input.c:625-669`）

```
mutex_lock_interruptible(&dev->mutex)
  ├─ dev->going_away        → -ENODEV
  ├─ handle->open++                          ← 先自增，且不会回滚（除下面一种情况）
  ├─ dev->users++ || dev->inhibited → 直接成功返回
  ├─ dev->open ? dev->open(dev) : (跳过)
  │     └─ 失败：dev->users--; handle->open--; synchronize_rcu(); 返回错误码
  └─ dev->poller → input_dev_poller_start()
mutex_unlock(&dev->mutex)
```

四个要点：

- **`dev->open()` 只在第一个用户到来时被调**（`dev->users++` 的**后置**自增：原值为 0 才继续往下走）。第二个 open 直接成功返回。对称地 `dev->close()` 只在最后一个用户离开时被调（A10）。
- ★ **被抑制的设备 open 会"成功"但硬件不上电**：`:641` 的条件是 `dev->users++ || dev->inhibited`，所以 `inhibited` 为真时**跳过 `dev->open()`**，返回 0。用户态 `open("/dev/input/event3")` 拿到 fd、`users` 也加上了，但设备一个事件都不会来。解除抑制时 `input_uninhibit_device()` 会补调 `dev->open()`（`drivers/input/input.c:1885-1889`），见 B5。
- **失败路径里的 `synchronize_rcu()`**（`:658`）不是为了这次打开，是为了保证"通过这个 handle 投递的事件"都已经跑完 —— `input_pass_values()` 在 RCU 读侧检查 `handle->open`（`drivers/input/input.c:158`），把 `handle->open` 减回去之后必须等一个 grace period 才能确定没有并发投递还在看着旧值。
- `handle->open` 是"这个 handle 被打开了几次"的计数，**不是 fd 数** —— evdev 只在第一个 client 到来时调一次 `input_open_device()`。这一点在 `02-datastructures.md` §1.0.2 里已经用对比表摊开了。

## A8 事件路径：从 `input_event()` 到 handler（`drivers/input/input.c:105-434`）

这是整个子系统的热路径，六个函数一层套一层。

### A8.1 `input_event()`（`drivers/input/input.c:423-434`）

```c
if (is_event_supported(type, dev->evbit, EV_MAX)) {
        spin_lock_irqsave(&dev->event_lock, flags);
        input_handle_event(dev, type, code, value);
        spin_unlock_irqrestore(&dev->event_lock, flags);
}
```

★ **整条事件路径从这里到 handler 的 `events()` 回调，全程在 `dev->event_lock` 自旋锁下、中断关闭。** 这一句是后面所有约束的根源：`dev->event()`、`handler->filter()`、`handler->events()`、`ff` 的三个回调，一个都不许睡。`is_event_supported(type, dev->evbit, EV_MAX)`（`drivers/input/input.c:62-66`）是 `code <= max && test_bit(code, bm)` —— 这里同样用 `EV_MAX` 做上界，但因为是 `<=` 而不是 `bitmap_subset` 的 nbits 语义，`EV_MAX` 本身是包含的，不存在 A5.1 那个 off-by-one。

`input_inject_event()`（`drivers/input/input.c:448-466`）是同一条路的另一个入口，供 handler 反向注入（如 evdev 的 write、LED 状态回写）。区别是它在 RCU 读侧检查 `handle->open`，未打开就什么也不做。

### A8.2 `input_handle_event()`（`drivers/input/input.c:390-404`）

只有三步，但它是"决策"和"执行"的分界：

| 行号 | 动作 |
| --- | --- |
| `:395` | `lockdep_assert_held(&dev->event_lock)` —— 谁绕过 `input_event()` 直接调它，开了 lockdep 就会被抓 |
| `:397` | `input_get_disposition(dev, type, code, &value)` 算出 disposition，**并且可能改写 value** |
| `:398-401` | 非 IGNORE 且 `type != EV_SYN` 时 `add_input_randomness(type, code, value)` —— 输入事件是内核熵池的来源之一 |
| `:402` | `input_event_dispose(dev, disposition, type, code, value)` |

### A8.3 `input_get_disposition()` —— 唯一的决策点（`drivers/input/input.c:237-345`）

四个 disposition 位（`drivers/input/input.c:180-185`）：

```c
#define INPUT_IGNORE_EVENT      0
#define INPUT_PASS_TO_HANDLERS  1
#define INPUT_PASS_TO_DEVICE    2
#define INPUT_SLOT              4
#define INPUT_FLUSH             8
#define INPUT_PASS_TO_ALL       (INPUT_PASS_TO_HANDLERS | INPUT_PASS_TO_DEVICE)
```

**第一件事是抑制检查**（`:244-245`）：`dev->inhibited` 为真直接 `INPUT_IGNORE_EVENT`，整条路走不下去。

然后按 `type` 分派，逐类的判据（默认值是 `INPUT_IGNORE_EVENT`，也就是"没进任何分支就丢掉"）：

| type | 行号 | 判据 | disposition |
| --- | --- | --- | --- |
| `EV_SYN` / `SYN_CONFIG` | `:251-253` | 无条件 | `PASS_TO_ALL` |
| `EV_SYN` / `SYN_REPORT` | `:255-257` | 无条件 | `PASS_TO_HANDLERS \| FLUSH` |
| `EV_SYN` / `SYN_MT_REPORT` | `:258-260` | 无条件 | `PASS_TO_HANDLERS` |
| `EV_SYN` / 其他 code | `:250-261` | 落不进任何 case | `IGNORE`（例如 `SYN_DROPPED` 由 evdev 自己产生，从不走这条路） |
| `EV_KEY` | `:264-278` | 位图支持；`value == 2` 直接过（**自动重复绕过状态更新**）；否则只有状态**变化**时 `__change_bit(code, dev->key)` 并放行 | `PASS_TO_HANDLERS` |
| `EV_SW` | `:281-288` | 同 KEY，但没有 value==2 的旁路 | `PASS_TO_HANDLERS` |
| `EV_ABS` | `:290-294` | 位图支持 → 交给 `input_handle_abs_event()` 决定（见 B2） | 由被调者定 |
| `EV_REL` | `:296-300` | 位图支持**且 `value` 非 0** | `PASS_TO_HANDLERS` |
| `EV_MSC` | `:302-306` | 位图支持 | `PASS_TO_ALL` |
| `EV_LED` | `:308-315` | 位图支持且状态变化 → `__change_bit(code, dev->led)` | `PASS_TO_ALL` |
| `EV_SND` | `:317-324` | 位图支持（**状态变化只影响是否 `__change_bit`，放行与否无关**） | `PASS_TO_ALL` |
| `EV_REP` | `:326-331` | `code <= REP_MAX && value >= 0 && dev->rep[code] != value` → 写 `dev->rep[code]` | `PASS_TO_ALL` |
| `EV_FF` | `:333-336` | `value >= 0` | `PASS_TO_ALL` |
| `EV_PWR` | `:338-340` | 无条件 | `PASS_TO_ALL` |

★ **KEY / SW / LED 三类做去重，SND 不做，REL 只过滤 0，ABS 走去抖。** 这张表是"我明明 `input_report_key()` 了但用户态没收到"的第一查处：重复按下（同一 code 连续两次 value=1）在 `:273` 就被吃掉了，因为 `dev->key` 里那一位已经是 1。

★ **`EV_SND` 那一格是四类里唯一不对称的**：`:320-321` 只在状态变化时 `__change_bit`，但 `disposition` 在 `:322` 无条件赋值，也就是说重复的 `SND_BELL` 会被一路投递下去。这不是笔误 —— 蜂鸣器"再响一次"是有意义的动作，而 LED"再亮一次"不是。

★ **`PASS_TO_ALL` 的那个 "DEVICE" 是往下调驱动的 `dev->event()`**，也就是 LED / SND / REP / FF / MSC / SYN_CONFIG / PWR 这七类会回到驱动手里。KEY / SW / ABS / REL 这四个"输入方向"的类永远不会调 `dev->event()`。

### A8.4 `input_event_dispose()` —— 攒帧和劈帧（`drivers/input/input.c:347-388`）

| 行号 | 动作 | 说明 |
| --- | --- | --- |
| `:350-351` | `(disposition & PASS_TO_DEVICE) && dev->event` → `dev->event(dev, type, code, value)` | ★ 在 `event_lock` 自旋锁下、中断关闭 |
| `:353-354` | `!dev->vals` → **直接 return** | 这是 §0.2 里"注册前的事件到不了 handler"的实现 |
| `:359-364` | `disposition & INPUT_SLOT` → 先往 `vals[]` 塞一条合成的 `EV_ABS/ABS_MT_SLOT/mt->slot` | 多点触控的 slot 切换是核心补出来的，驱动不用自己发 |
| `:366-369` | 把本次事件写进 `dev->vals[dev->num_vals++]` | |
| `:372-375` | `disposition & INPUT_FLUSH` → **`num_vals >= 2` 才** `input_pass_values()`；然后 `num_vals = 0` | ★ 见下 |
| `:382` | `dev->timestamp[INPUT_CLK_MONO] = ktime_set(0, 0)` | 每帧结束清掉单调时间戳，下一帧没人显式设就现取一个 |
| `:383-386` | 否则若 `num_vals >= max_vals - 2` → 追加一条 `input_value_sync` 再 `input_pass_values()`，`num_vals = 0` | ★ **劈帧，不是丢事件** |

★ **空帧被无声吃掉**：`:373` 的 `num_vals >= 2` 意味着一个孤零零的 `SYN_REPORT`（`num_vals == 1`）不会调 `input_pass_values()`，`num_vals` 却照样被清零。后果有两条：(a) 连续调两次 `input_sync()`，第二次是空操作；(b) 一帧里所有事件都被去重吃掉时，那个 `SYN_REPORT` 也不会发出去，用户态不会看到一个空帧。

★ **劈帧的语义**：`max_vals = hint_events_per_packet + 2`（A4 第 7 步）。当攒到 `max_vals - 2` 条时，核心自己补一条 `SYN_REPORT` 把这一帧收掉并投递，然后从 0 重新攒。**没有事件被丢弃**，但用户态会看到本该是一帧的数据变成两帧。对多点触控这类"一帧内的坐标必须一起解释"的协议，`hint_events_per_packet` 给小了就会看到坐标跨帧撕裂。`input_value_sync` 是文件级常量 `{ EV_SYN, SYN_REPORT, 1 }`（`drivers/input/input.c:49`）。

### A8.5 `input_pass_values()`（`drivers/input/input.c:140-178`）

```
lockdep_assert_held(&dev->event_lock)
count == 0 → return
rcu_read_lock()
  ├─ handle = rcu_dereference(dev->grab)
  │    ├─ 非 NULL：count = input_to_handler(handle, vals, count)      ← 只投给抢占者
  │    └─ NULL：   list_for_each_entry_rcu(handle, &dev->h_list, d_node)
  │                   if (handle->open) {
  │                           count = input_to_handler(handle, vals, count);
  │                           if (!count) break;              ← 被 filter 吃干净就停
  │                   }
rcu_read_unlock()
EV_REP && EV_KEY 都支持 → 扫 vals[0..count)，EV_KEY 且 value != 2：
        value 非 0 → input_start_autorepeat(dev, v->code)
        value 为 0 → input_stop_autorepeat(dev)
```

★ **自动重复的触发点在这里，不在 `input_get_disposition()` 里。** 也就是说"按下→开始重复"这件事发生在事件已经投递给 handler **之后**。这有一个可观察的后果，见下一条。

★ **`count` 被 `input_to_handler()` 的返回值覆盖了**（`:155` 和 `:159`），而末尾的自动重复扫描用的正是这个被改过的 `count`（`:169`）。所以：filter 型 handler 压掉的事件，**不会**参与自动重复判断；全部被压掉（`count` 归 0）时，`break` 让循环提前结束，自动重复整段跳过。反过来，如果一个设备**没有任何 handle 处于 open 状态**，循环体一次都不执行，`count` 保持原值，自动重复照样被触发 —— 定时器会在没人听的情况下空转。

★ 抢占（grab）分支和普通分支是**互斥**的：一旦 `dev->grab` 非空，`h_list` 整条链都被跳过，包括 filter 型 handler。所以 evdev 的 `EVIOCGRAB` 会连 sysrq 一起挡掉。

### A8.6 `input_to_handler()`（`drivers/input/input.c:105-133`）

```c
if (handler->filter) {
        for (v = vals; v != vals + count; v++) {
                if (handler->filter(handle, v->type, v->code, v->value))
                        continue;                  /* 吃掉：不复制 */
                if (end != v)
                        *end = *v;                 /* 就地前移 */
                end++;
        }
        count = end - vals;
}
if (!count)
        return 0;
if (handler->events)
        handler->events(handle, vals, count);
else if (handler->event)
        for (v = vals; v != vals + count; v++)
                handler->event(handle, v->type, v->code, v->value);
return count;
```

★ **filter 是就地压缩 `dev->vals[]` 这块共享缓冲区**，改动对后面所有 handler 可见。这是"filter 能吃事件"的物理机制，也意味着 filter 回调**绝对不能修改 `v->value` 之外的东西**并期望别人看不到。

★ **`events` 优先于 `event`，两者只会走一个。** 全树只有 evdev 同时提供了这两个回调（`drivers/input/evdev.c:1421-1422`），而它的 `event`（`drivers/input/evdev.c:314-320`，把单个事件包成 1 元数组再转调 `evdev_events`）因此**是死代码**。另一端 `kgdboc_reset_handler`（`drivers/tty/serial/kgdboc.c:96-101`）两个回调都没有 —— 它只用 `connect`/`disconnect` 做钩子，一个事件也不处理。见 `02-datastructures.md` §3。

## A9 抢占：`input_grab_device()` / `input_release_device()`（`drivers/input/input.c:559-615`）

`input_grab_device()`（`:559-578`）：拿 `dev->mutex`（可中断）→ `dev->grab` 已非空则 `-EBUSY` → `rcu_assign_pointer(dev->grab, handle)`。**先到先得，没有优先级，也没有强制抢占**。

`__input_release_device()`（`:581-597`）三步，顺序是关键：

| 行号 | 动作 |
| --- | --- |
| `:586-588` | `rcu_dereference_protected(dev->grab, lockdep_is_held(&dev->mutex))`，且**只有当前 handle 就是抢占者**才继续 |
| `:589` | `rcu_assign_pointer(dev->grab, NULL)` |
| `:591` | `synchronize_rcu()` —— 源码注释：让 `input_pass_values()` 注意到 grab 已经没了 |
| `:593-595` | 遍历 `dev->h_list`，对每个 `handle->open` 且有 `start` 的 handler 调 `handler->start(handle)` |

★ 最后那一步是"抢占结束后让所有 handler 重新同步设备状态"—— 因为抢占期间它们错过了所有事件，键盘可能残留按下状态。这也是 `handler->start` 的第二个调用点（第一个在 A6）。

`input_release_device()`（`:608-615`）只是加了 `dev->mutex` 的外壳。注意它用的是**不可中断**的 `mutex_lock()`，而 grab 用的是可中断版本。

## A10 关闭：`input_close_device()` / `input_flush_device()`（`drivers/input/input.c:696-721` / `672-686`）

`input_close_device()` 四步（全程 `dev->mutex`，不可中断）：

| 行号 | 动作 | 备注 |
| --- | --- | --- |
| `:702` | `__input_release_device(handle)` | 关闭时顺手放掉抢占，不需要用户态显式 `EVIOCGRAB(0)` |
| `:704-709` | `!--dev->users && !dev->inhibited` → 停 poller、调 `dev->close(dev)` | ★ 抑制状态下**不**调 `close()`，因为 `input_inhibit_device()` 已经调过了 |
| `:711-718` | `!--handle->open` → `synchronize_rcu()` | 保证没有并发投递还在看这个 handle |
| `:720` | 放锁 | |

`input_flush_device()`（`:672-686`）只是在 `dev->mutex` 下转调 `dev->flush(dev, file)`。它的存在是给 force feedback 用的：进程退出时要把它上传的效果清掉。**这是唯一一个把 `struct file *` 传到驱动手里的核心接口**。

★ `dev->close()` 的契约是"必须成功"（`Documentation/input/input-programming.rst` 明确要求），因为 `input_close_device()` 没有返回值也没有回滚路径。`dev->open()` 可以失败，`dev->close()` 不可以。

## A11 拆连线：`input_unregister_handle()`（`drivers/input/input.c:2669-2683`）

```c
list_del_rcu(&handle->h_node);          /* 无锁，靠 input_mutex 串行化 */
mutex_lock(&dev->mutex);
list_del_rcu(&handle->d_node);          /* 有锁，防和 input_release_device() 竞争 */
mutex_unlock(&dev->mutex);
synchronize_rcu();
```

和 A6 严格对称：两条链、一把锁、一个 grace period。★ `synchronize_rcu()` 在**放锁之后**，所以它等的是 `input_pass_values()` 的读侧临界区，不是 `dev->mutex`。这之后 handler 才能安全 `kfree` 掉包含这个 handle 的结构体。

## A12 注销设备（`drivers/input/input.c:2287-2307` / `2501-2517` / `748-776` / `1659-1671`）

四个函数，从外到里。

### A12.1 `input_unregister_device()`（`drivers/input/input.c:2501-2517`）

只做一件事：分流。devres 托管的先 `devres_destroy()` 掉那条 unregister 记录再 `__input_unregister_device()`，**并且不调 `input_put_device()`**（源码注释 `:2509-2512`：留给第二条 devres）。非托管的 `__input_unregister_device()` 之后自己 `input_put_device()`。

### A12.2 `__input_unregister_device()`（`drivers/input/input.c:2287-2307`）

| 行号 | 动作 |
| --- | --- |
| `:2291` | `input_disconnect_device(dev)` —— 见 A12.3 |
| `:2293` | `mutex_lock(&input_mutex)` |
| `:2295-2296` | `list_for_each_entry_safe(handle, next, &dev->h_list, d_node)` → `handle->handler->disconnect(handle)` |
| `:2297` | `WARN_ON(!list_empty(&dev->h_list))` —— handler 的 disconnect 里必须调 `input_unregister_handle()`，否则这里会喊 |
| `:2299` | `del_timer_sync(&dev->timer)` |
| `:2300` | `list_del_init(&dev->node)` |
| `:2302` | `input_wakeup_procfs_readers()` |
| `:2306` | `device_del(&dev->dev)` |

★ `del_timer_sync()` 放在 `input_disconnect_device()` **之后**是有必要的：自动重复定时器的回调 `input_repeat_key()`（`drivers/input/input.c:2324-2345`）要拿 `event_lock` 并调 `input_handle_event()`，必须确认它彻底停了才能往下拆。

### A12.3 `input_disconnect_device()`（`drivers/input/input.c:748-776`）

| 行号 | 动作 | 注释里的理由 |
| --- | --- | --- |
| `:757-759` | `dev->mutex` 下 `dev->going_away = true` | ★ 拿锁**不是**为了保护这个 bool，而是为了确保没有线程正卡在 `input_open_device()` 中间 |
| `:761` | `spin_lock_irq(&dev->event_lock)` | |
| `:769-770` | `input_dev_release_keys(dev)` 有动作则补一个 `SYN_REPORT` | 给所有按下的键补 keyup，否则 handler 会留下"卡住的键" |
| `:772-773` | 遍历 `dev->h_list` 把每个 `handle->open` 置 0 | 这之后 `input_pass_values()` 的 `if (handle->open)` 一律为假，驱动继续报事件也到不了任何人 |

`input_dev_release_keys()`（`drivers/input/input.c:728-743`）自己也是一个可复用件：`lockdep_assert_held(&dev->event_lock)`，`for_each_set_bit(code, dev->key, KEY_CNT)` 逐个 `input_handle_event(dev, EV_KEY, code, 0)`，返回"有没有真的放过键"。它在 5 个地方被调：disconnect、suspend、freeze、inhibit、`input_reset_device()`。

### A12.4 `input_dev_release()`（`drivers/input/input.c:1659-1671`）

`input_dev_type.release`，只在最后一个引用消失时才跑：

```c
input_ff_destroy(dev);
input_mt_destroy_slots(dev);
kfree(dev->poller);
kfree(dev->absinfo);
kfree(dev->vals);
kfree(dev);
module_put(THIS_MODULE);
```

★ 六个 `kfree` 加一个 `module_put` 就是 `input_dev` 全部的动态内存。注意 `dev->name` / `phys` / `uniq` **不在这里释放** —— 核心从不复制这三个字符串，谁分配谁释放。`module_put(THIS_MODULE)` 和 `input_allocate_device()` 里的 `__module_get()`（`drivers/input/input.c:2029`）配对。

## A13 handler 侧的注册与注销（`drivers/input/input.c:2528-2573`）

`input_register_handler()`（`:2528-2548`）和 `input_register_device()` 是对偶的：拿 `input_mutex`（可中断）→ `INIT_LIST_HEAD(&handler->h_list)` → `list_add_tail(&handler->node, &input_handler_list)` → 遍历 `input_dev_list` 对每个设备跑 `input_attach_handler()` → `input_wakeup_procfs_readers()`。**同样丢弃 attach 的返回值，永远返回 0**（除了拿锁被打断）。

★ 这就是"注册顺序无关"的实现：设备先来，handler 注册时补扫一遍设备；handler 先来，设备注册时补扫一遍 handler。两边都在同一把 `input_mutex` 下，所以不会漏、也不会重。

`input_unregister_handler()`（`:2558-2573`）：`input_mutex`（**不可中断**）→ 遍历 `handler->h_list` 逐个 `handler->disconnect(handle)` → `WARN_ON(!list_empty(&handler->h_list))` → `list_del_init(&handler->node)` → `input_wakeup_procfs_readers()`。

以 evdev 为例，两侧回调的实际内容（`drivers/input/evdev.c:1337-1411`）：

| 阶段 | 动作 | 行号 |
| --- | --- | --- |
| connect | `input_get_new_minor(EVDEV_MINOR_BASE, EVDEV_MINORS, true)`，失败 `pr_err("failed to reserve new minor: %d\n", …)` | `:1345-1350` |
| | `kzalloc` + `INIT_LIST_HEAD(&evdev->client_list)` + 两把锁 + `exist = true` | `:1352-1361` |
| | 次设备号落在 legacy 区间内则归一化，`dev_set_name(&evdev->dev, "event%d", dev_no)` | `:1363-1367` |
| | `evdev->handle.dev = input_get_device(dev)` —— **这是 handle 持有设备引用的地方** | `:1369` |
| | `evdev->dev.parent = &dev->dev`；`dev.class = &input_class`；`dev.devt = MKDEV(INPUT_MAJOR, minor)` | `:1374-1376` |
| | `input_register_handle()` → `cdev_init` → `cdev_device_add()` | `:1380-1386` |
| disconnect | `cdev_device_del()` → `evdev_cleanup()` → `input_free_minor()` → `input_unregister_handle()` → `put_device()` | `:1402-1411` |

★ `evdev_cleanup()`（`drivers/input/evdev.c:1319-1331`）里 `if (evdev->open) { input_flush_device(); input_close_device(); }` —— **拆除时替用户态把设备关掉**。这是 `input_flush_device()` 唯一一处传 `NULL` 当 `struct file *` 的调用点，驱动的 `flush` 回调必须能应付这个 NULL。

---

# Part B 横切机制

Part A 是按时间排的，这一段里的东西**不属于任何单一时刻**：它们贯穿整条时间线，或者只在某个侧面出现。

## B1 锁与上下文

### B1.1 四把锁的分工

| 锁 | 定义 | 保护什么 | 谁在这把锁下 |
| --- | --- | --- | --- |
| `input_mutex` | `drivers/input/input.c:47` | `input_dev_list` + `input_handler_list` 两张全局链表 | register/unregister device、register/unregister handler、以及被它们调起的**全部** connect/disconnect |
| `dev->mutex` | `include/linux/input.h:193` | `users` / `going_away` / `inhibited` / `grab` 的写侧 / `h_list` 的**写**侧 | open / close / flush / grab / release / inhibit / uninhibit / register_handle / unregister_handle |
| `dev->event_lock` | `include/linux/input.h:192`（`spinlock_t`，一律 IRQ-off） | 四张状态位图、`vals[]`/`num_vals`、`rep[]`、`absinfo[].value`、`mt->slots[]`、`timestamp[]`、`h_list` 的**读**侧遍历 | 整条事件路径、键码 get/set、四个 PM 回调、autorepeat 定时器回调 |
| `ff->mutex` | `include/linux/input.h:561` | force feedback 的 effect 表 | `ff-core.c` 的上传/擦除/播放 |

外加三份**各自独立**的字符设备锁（evdev / mousedev / joydev 每家一套 `client_lock` + `mutex` + 每 client 一把 buffer 锁），见 `04-extensions.md`。

★ **全子系统一条 `rwlock_t` / `rw_semaphore` 都没有。** 判定命令和它的完整 10 行输出在 `02-datastructures.md` §4.2。

### B1.2 加锁顺序

源码里能观察到的嵌套只有一条链，从外到内：

```
input_mutex  →  dev->mutex  →  dev->event_lock
```

三处证据：

| 嵌套 | 出现在 | 行号 |
| --- | --- | --- |
| `input_mutex` → `dev->mutex` | `input_register_device()` 持锁调 `input_attach_handler()` → `connect()` → `input_register_handle()` | `drivers/input/input.c:2463` + `:2629` |
| `input_mutex` → `dev->mutex` | `__input_unregister_device()` 持锁调 `disconnect()` → `input_unregister_handle()` | `drivers/input/input.c:2293` + `:2678` |
| `dev->mutex` → `event_lock` | `input_reset_device()`、`input_inhibit_device()` | `drivers/input/input.c:1835-1836`、`:1849` + `:1861` |

★ **反向嵌套一处都没有**，所以这个顺序是硬约束：写 handler 时若在 `event_lock` 下去拿 `dev->mutex`（比如在 `events()` 回调里调 `input_open_device()`），就是死锁 + 在原子上下文里睡眠的双重错误。

### B1.3 每个回调的上下文约束

| 回调 | 谁调 | 上下文 | 能睡吗 |
| --- | --- | --- | --- |
| `dev->open` / `dev->close` | `input_open_device` / `input_close_device` / `input_uninhibit_device` / `input_inhibit_device` | `dev->mutex` 下，进程上下文 | **能** |
| `dev->flush` | `input_flush_device` | `dev->mutex` 下，进程上下文 | **能**（`file` 参数可能是 NULL，见 A13） |
| `dev->event` | `input_event_dispose`、`input_dev_toggle` | `event_lock` 下，中断关闭 | **不能** |
| `handler->filter` | `input_to_handler` | `event_lock` 下，中断关闭 | **不能** |
| `handler->event` / `handler->events` | `input_to_handler` | `event_lock` 下，中断关闭 | **不能** |
| `handler->connect` / `disconnect` | `input_attach_handler` / `__input_unregister_device` / `input_unregister_handler` | `input_mutex` 下，进程上下文 | **能** |
| `handler->start` | `input_register_handle`、`__input_release_device` | 无锁 / `dev->mutex` 下 | 看调用点，保守当作**不能**（后者在 `dev->mutex` 下但 `__input_release_device` 也可能从 `input_close_device` 路径来） |
| `dev->getkeycode` / `setkeycode` | `input_get_keycode` / `input_set_keycode` | `event_lock` 下，中断关闭 | **不能** |
| `ff->upload` / `erase` / `playback` | `ff-core.c` | 见 `include/linux/input.h:542-544` 的注释 | **不能** |

★ **`Documentation/input/input-programming.rst` 对 `event` 回调的说法是"可能从中断或 BH 调用，不能睡"，实际约束更严：它在 `dev->event_lock` 自旋锁下、中断关闭。** 也就是说连 `mutex_trylock` 之外的任何可能重调度的动作都不行，`GFP_KERNEL` 分配也不行。这条冲突记在 C2。

★ **驱动的 `input_report_*()` 可以在硬中断里调**（`input_event()` 用的是 `spin_lock_irqsave`，天生可重入到中断上下文），这是 input 和许多"必须在线程上下文"的子系统的显著差别。

## B2 去重、去抖、越界（`drivers/input/input.c:62-82` / `187-235`）

三层筛子，一个事件要全部通过才会进 `vals[]`。

### B2.1 越界与能力：`is_event_supported()`（`drivers/input/input.c:62-66`）

```c
return code <= max && test_bit(code, bm);
```

一行两件事：先查上界（`<=`，包含 `max` 本身），再查位图。事件路径里它被用了 8 次（EV_KEY / EV_SW / EV_ABS / EV_REL / EV_MSC / EV_LED / EV_SND 各一次 + `input_event()` 查 type）。★ 注意这里的 `<=` 语义和 A5.1 的 `bitmap_subset(…, X_MAX)` 语义**不一致**：同一个 `X_MAX` 在这里是"含"，在那里是"不含"。

### B2.2 状态去重

`EV_KEY` / `EV_SW` / `EV_LED` 三类做 `!!test_bit(code, 状态位图) != !!value` 的比较，只有真的变了才 `__change_bit()` 并放行（`drivers/input/input.c:273`、`:283`、`:310`）。`EV_SND` 只 `__change_bit` 不影响放行，`EV_REL` 只过滤 `value == 0`。详表见 A8.3。

### B2.3 去抖：`input_defuzz_abs_event()`（`drivers/input/input.c:68-82`）

```c
if (fuzz) {
        if (value > old_val - fuzz / 2 && value < old_val + fuzz / 2)
                return old_val;                        /* 完全当没动 */
        if (value > old_val - fuzz && value < old_val + fuzz)
                return (old_val * 3 + value) / 4;      /* 3:1 加权 */
        if (value > old_val - fuzz * 2 && value < old_val + fuzz * 2)
                return (old_val + value) / 2;          /* 1:1 平均 */
}
return value;                                          /* 原样通过 */
```

四档，`fuzz == 0` 时整段跳过。★ 三个区间都是**严格不等**，所以恰好落在边界上（`value == old_val ± fuzz/2`）会掉进下一档。★ 这不是一个简单的死区，是一个**分段低通滤波器** —— 中间两档会返回一个既不是新值也不是旧值的中间值，这是"我明明报了 100，用户态读到 97"的来源。

★ **`absinfo->flat` 在核心里一次也没被读过。** 唯一的赋值点是 `drivers/input/input.c:510`，唯一的消费者是 joydev（`drivers/input/joydev.c:978-982` 的 `input_abs_get_flat()`）。也就是说 `flat` 对 evdev 用户来说纯粹是一个"透传给用户态自己去用"的元数据。重跑：

```bash
grep -rn 'flat' drivers/input/*.c | grep -v flatten
```

### B2.4 ABS 事件与 MT slot：`input_handle_abs_event()`（`drivers/input/input.c:187-235`）

| 输入 | 行号 | 行为 |
| --- | --- | --- |
| `code == ABS_MT_SLOT` | `:194-203` | **暂存**：`mt && *pval >= 0 && *pval < mt->num_slots` 时写 `mt->slot`，然后一律返回 `INPUT_IGNORE_EVENT` |
| 非 MT 轴 | `:207-208` | 旧值取自 `dev->absinfo[code].value` |
| MT 轴且有 `dev->mt` | `:209-210` | 旧值取自 `mt->slots[mt->slot].abs[code - ABS_MT_FIRST]` |
| MT 轴但**没有** `dev->mt` | `:211-217` | `pold = NULL` —— 源码注释：不用 slot 的多点触控设备**绕过去抖** |
| 有旧值 | `:219-226` | 去抖 → 值没变则 `INPUT_IGNORE_EVENT`；变了就写回旧值槽 |
| MT 轴且 `mt->slot != input_abs_get_val(dev, ABS_MT_SLOT)` | `:229-232` | 补一条 slot 切换：`input_abs_set_val(dev, ABS_MT_SLOT, mt->slot)` 后返回 `PASS_TO_HANDLERS \| INPUT_SLOT` |
| 其余 | `:234` | `PASS_TO_HANDLERS` |

★ **`ABS_MT_SLOT` 这个"轴"的 `absinfo[].value` 被核心挪用成了"上一次报给用户态的 slot 号"**，和 `mt->slot`（驱动当前正在写的 slot）是两个不同的东西。两者不一致时才补发 slot 切换事件 —— 这就是驱动只需要调 `input_mt_slot()` 而不必自己发 `ABS_MT_SLOT` 的原因。`INPUT_SLOT` 那一位最终在 `input_event_dispose()` 的 `:359-364` 被兑现成一条合成事件。

★ **`ABS_MT_SLOT` 自己永远不会被直接投递**（`:202` 无条件 IGNORE），用户态看到的每一条 `ABS_MT_SLOT` 都是核心合成的。

## B3 自动重复（`drivers/input/input.c:84-98` / `2324-2360`）

| 件 | 位置 | 说明 |
| --- | --- | --- |
| 启动 | `input_start_autorepeat()` `:84-93` | 三个前置条件全满足才 `mod_timer`：`EV_REP` 在 `evbit` 里、`rep[REP_PERIOD]` 和 `rep[REP_DELAY]` **都**非 0、`dev->timer.function` 非 NULL。首次延迟用 `rep[REP_DELAY]` |
| 停止 | `input_stop_autorepeat()` `:95-98` | 只有一句 `del_timer(&dev->timer)`。★ 用的是 `del_timer` 而**不是** `del_timer_sync` —— 它在 `event_lock` 下、中断关闭时被调，sync 版本会死锁 |
| 触发点 | `input_pass_values()` `:167-177` | 见 A8.5：在事件投递**之后**扫 `vals[]`，`EV_KEY` 且 `value != 2` 时按 value 启停 |
| 定时器回调 | `input_repeat_key()` `:2324-2345` | 拿 `event_lock` → 三个条件（`!inhibited`、`test_bit(repeat_key, dev->key)`、`is_event_supported(repeat_key, keybit, KEY_MAX)`）→ `input_set_timestamp(dev, ktime_get())` → 发 `EV_KEY/repeat_key/2` + `SYN_REPORT` → `rep[REP_PERIOD]` 非 0 时重新 `mod_timer` |
| 开关 | `input_enable_softrepeat()` `:2355-2360` | 只是把 `timer.function` 和两个 `rep[]` 一次填好，无返回值 |
| 默认值 | `input_register_device()` `:2441-2442` | `rep[REP_DELAY]` 和 `rep[REP_PERIOD]` **都**为 0 才启用软重复，参数 250 / 33（毫秒） |

★ **"驱动自己设了 rep[] 就等于自己接管重复"** —— `drivers/input/input.c:2437-2440` 的注释明说了这个约定。只设其中一个（比如只设 `REP_DELAY`）会落进最坏的中间态：核心不给你装软重复（两者不都为 0），而 `input_start_autorepeat()` 又因为另一个为 0 而永不启动，硬件重复也没有。

★ **重复事件走 `value == 2`，在 `input_get_disposition()` 的 `:268-271` 被特判成"绕过状态更新"**，所以它不会被去重吃掉，也不会翻转 `dev->key` 里那一位。这是"重复"能连发的前提。

★ 用户态改重复速率的路径是 `EV_REP` 事件（不是 ioctl）：`input_inject_event(handle, EV_REP, REP_PERIOD, ms)` → `input_get_disposition()` 的 `:326-331` 写 `dev->rep[code]` 并 `PASS_TO_ALL` → 驱动的 `dev->event()` 有机会同步到硬件。

## B4 次设备号（`drivers/input/input.c:2697-2730`）

`input_init()` 一次占下 `MKDEV(13, 0)` 起的 1024 个号，然后由 `input_ida` 在这个区间内分配。

| 使用者 | legacy 基址 | legacy 个数 | 覆盖范围 | 常量出处 |
| --- | --- | --- | --- | --- |
| joydev | 0 | 16 | 0–15 | `drivers/input/joydev.c:31-32` |
| mousedev | 32 | 31 | 32–62 | `drivers/input/mousedev.c:11-12` |
| mousedev 混合设备 | — | — | 63（`MOUSEDEV_MIX`，`/dev/input/mice`） | `drivers/input/mousedev.c:13` |
| evdev | 64 | 32 | 64–95 | `drivers/input/evdev.c:10-11` |
| 动态区 | `INPUT_FIRST_DYNAMIC_DEV` = 256 | — | 256–1023 | `drivers/input/input.c:35` |

`input_get_new_minor(legacy_base, legacy_num, allow_dynamic)`（`:2697-2717`）的逻辑：`legacy_base >= 0` 时先在 legacy 区间 `ida_simple_get`，拿到就返回；拿不到且 `allow_dynamic` 为假也直接返回错误；否则退到动态区再要一个。

★ **16–31 和 96–255 这两段是空洞**，谁都不用。★ 源码注释（`:2700-2704`）说明这个函数**不加锁**，理由是"只从 handler 的 `connect()` 里调，而那是被 `input_mutex` 串行化的"—— 又一次兑现 A4 第 13 步的串行化。

★ **设备名里的编号是"归一化"后的号，不是次设备号**：`drivers/input/evdev.c:1363-1367` 里 `dev_no = minor`，落在 legacy 区间内才减去 `EVDEV_MINOR_BASE`。所以第 33 个 evdev 设备会拿到动态号 256，名字就是 `event256`，中间跳掉一大截。

★ `/dev/psaux` 不在这套体系里：它是 **misc 设备**，主设备号 10、次设备号 1（`PSMOUSE_MINOR`，`include/linux/miscdevice.h:15`），和 mousedev 共用同一套 `mousedev_fops`（`drivers/input/mousedev.c:1067` 的 `.minor = PSMOUSE_MINOR`）。

## B5 抑制（inhibit）（`drivers/input/input.c:1506-1535` / `1847-1902`）

抑制的语义是"设备还在、fd 还能开、但硬件下电且事件全丢"。它和 open/close 是**正交**的两个维度。

用户态入口只有一个 sysfs 文件：`inhibited`（`DEVICE_ATTR_RW`，`drivers/input/input.c:1537`）。写侧 `inhibited_store()`（`:1515-1535`）用 `strtobool()` 解析，非法输入 `-EINVAL`，然后分派到下面两个函数；返回值非 0 直接当成写失败。

`input_inhibit_device()`（`:1847-1873`）：

| 行号 | 动作 |
| --- | --- |
| `:1851-1852` | 已经抑制 → 直接成功（幂等） |
| `:1854-1859` | `dev->users` 非 0 时调 `dev->close()` 并停 poller —— **有人打开着才需要关** |
| `:1861-1866` | `event_lock` 下：`input_mt_release_slots()` → `input_dev_release_keys()` → `SYN_REPORT` → `input_dev_toggle(dev, false)` |
| `:1868` | `dev->inhibited = true` |

★ **`inhibited = true` 是在放完键、关完灯之后才设的**（`:1868` 在 `:1866` 之后）。这是必须的：`input_get_disposition()` 的第一件事就是 `dev->inhibited` 检查（`:244-245`），如果先置真，那些补发的 keyup 和 LED-off 事件会被自己的抑制检查吃掉，handler 就留下一堆卡住的键。

`input_uninhibit_device()`（`:1875-1902`）对称地反过来，但顺序更微妙：

| 行号 | 动作 |
| --- | --- |
| `:1881-1882` | 没抑制 → 直接成功 |
| `:1884-1892` | `dev->users` 非 0 时补调 `dev->open()`；**失败就整个放弃，`inhibited` 保持为真**并把错误码返回给 sysfs 写 |
| `:1894` | `dev->inhibited = false` |
| `:1895-1897` | `event_lock` 下 `input_dev_toggle(dev, true)` |

★ 这里 `inhibited = false` 在 `input_dev_toggle()` **之前**（`:1894` 早于 `:1896`），同样是为了让恢复 LED / SND / REP 的那几条 `dev->event()` 调用不被抑制检查挡住。两个函数一个把置位放在最后、一个放在最前，理由是同一个。

`input_dev_toggle()`（`:1809-1821`）：`dev->event` 为空直接返回；否则 `INPUT_DO_TOGGLE(dev, LED, led, activate)` + `INPUT_DO_TOGGLE(dev, SND, snd, activate)`；`activate` 且支持 `EV_REP` 时额外补发 `REP_PERIOD` 和 `REP_DELAY` 两条事件。

★ **`input_device_enabled()`（`:2363-2368`）是给驱动用的判据**：`lockdep_assert_held(&dev->mutex)` + `return !dev->inhibited && dev->users > 0`。驱动在 `open`/`close`/`inhibit` 之外的地方想知道"我现在该不该采数据"，就调它 —— 但必须自己先持 `dev->mutex`。

## B6 电源管理（`drivers/input/input.c:1905-1976`）

`input_dev_pm_ops`（`:1972-1978`）挂在 `input_dev_type.pm` 上，且整块被 `CONFIG_PM_SLEEP` 包着（`:1985-1987`）。五个入口指向四个函数：

| PM 回调 | 实现 | 放键 | 关灯/关声 | 恢复灯/声/重复率 |
| --- | --- | --- | --- | --- |
| `.suspend` | `input_dev_suspend` `:1905-1924` | ✅ | ✅ | — |
| `.resume` | `input_dev_resume` `:1926-1938` | — | — | ✅ |
| `.freeze` | `input_dev_freeze` `:1940-1956` | ✅ | — | — |
| `.poweroff` | `input_dev_poweroff` `:1958-1970` | — | ✅ | — |
| `.restore` | `input_dev_resume`（**复用**） | — | — | ✅ |

四个函数都只在 `spin_lock_irq(&input_dev->event_lock)` 下做事，都无条件 `return 0`，**一个都不会失败**。没有 `.thaw` / `.prepare` / `.complete`，也没有 runtime PM 的任何回调。

★ **`.freeze` 只放键不关灯**（hibernate 的镜像阶段之后系统还要继续跑），**`.poweroff` 只关灯不放键**（马上要断电，没人在意按键状态）。这对不对称是有意的，注释在各自函数里。

★ 核心的 PM 完全不碰 `dev->open`/`dev->close`，也不碰 `users`。**"suspend 时要不要把硬件停掉"整件事都在驱动手里**，核心只负责把软件状态（按下的键、亮着的灯）弄干净。

★ 放键这一步靠 `input_dev_release_keys()` + 条件性的 `SYN_REPORT`（`:1915-1916`、`:1950-1951`），和 A12.3 的 disconnect、B5 的 inhibit 用的是同一个件。

## B7 键码映射（`drivers/input/input.c:788-997`）

这一套解决的问题是：硬件给的是 scancode，用户态要的是 `KEY_*` keycode，两者之间的映射表要能在运行时被改（`EVIOCSKEYCODE`）。

### B7.1 scancode 的三种宽度

`input_scancode_to_scalar()`（`:788-809`）按 `ke->len` 取 1 / 2 / 4 字节，其他长度 `-EINVAL`。`struct input_keymap_entry`（`include/uapi/linux/input.h:117-124`）里的 `scancode` 是一个 32 字节数组，所以 uapi 层允许比 4 字节更长的 scancode，只是默认实现不支持。

### B7.2 默认实现

驱动不提供 `getkeycode`/`setkeycode` 时，`input_register_device()` 在 `:2444-2448` 填上下面两个。它们把 `dev->keycode` 当成一个"按 index 索引、每项 `dev->keycodesize` 字节、共 `dev->keycodemax` 项"的数组。

`input_default_getkeycode()`（`:832-858`）：`keycodesize` 为 0 → `-EINVAL`；按 `INPUT_KEYMAP_BY_INDEX` 决定 index 来自 `ke->index` 还是 scancode；`index >= keycodemax` → `-EINVAL`；然后回填 `ke->keycode` / `ke->index` / `ke->len` / `ke->scancode`。

`input_default_setkeycode()`（`:860-920`）多三件事：

| 行号 | 动作 |
| --- | --- |
| `:882-884` | `keycodesize < sizeof(ke->keycode) && (ke->keycode >> (keycodesize * 8))` → `-EINVAL`。**新 keycode 塞不进表项宽度就拒绝** |
| `:886-905` | 按 1 / 2 / 4 字节宽度写入，同时把原值取出来给 `*old_keycode` |
| `:907-916` | 旧 keycode 合法（`<= KEY_MAX`）时先 `__clear_bit(*old_keycode, dev->keybit)`，再**线性扫一遍整张表** —— 如果还有别的 index 映射到同一个 keycode 就把位加回去 |
| `:918` | `__set_bit(ke->keycode, dev->keybit)` |

★ 第三件事是 O(keycodemax) 的：改一个键会扫全表。这段在 `event_lock` 下、中断关闭时跑（见下），`keycodemax` 大的矩阵键盘上是一次可观的关中断时长。

### B7.3 包装层

`input_get_keycode()`（`:930-940`）和 `input_set_keycode()`（`:951-997`）都在 `spin_lock_irqsave(&dev->event_lock, flags)` 下调驱动回调 —— 所以**驱动的 `getkeycode`/`setkeycode` 不能睡**，不能 `GFP_KERNEL`，不能拿 mutex。

`input_set_keycode()` 在成功之后还有三步收尾：

| 行号 | 动作 |
| --- | --- |
| `:958-959` | `ke->keycode > KEY_MAX` → `-EINVAL`（在调驱动之前） |
| `:968` | `__clear_bit(KEY_RESERVED, dev->keybit)` —— 防止有人把某个 scancode 映射成 `KEY_RESERVED` 从而让那一位亮起来 |
| `:974-977` | 驱动返回的 `old_keycode > KEY_MAX` → `dev_warn(…, "%s: got too big old keycode %#x\n", …)` |
| `:978-991` | 旧 keycode 已经不在 `keybit` 里、而 `dev->key` 里那一位还是 1 → **直接调两次 `input_event_dispose()`** 补一个 keyup + `SYN_REPORT` |

★ 最后那一步是全核心**唯一**绕过 `input_handle_event()` 直接调 `input_event_dispose()` 的地方，源码注释（`:981-986`）解释得很清楚：这个键已经"不被设备支持"了，走正常路径会被 `input_get_disposition()` 的位图检查吃掉，所以必须绕过决策层直接投递。

## B8 sysfs / procfs / modalias

### B8.1 三个 attribute_group

`input_dev_attr_groups[]`（`drivers/input/input.c:1651-1657`）挂在 `input_dev_type.groups` 上，四项：

| group | 定义 | 内容 |
| --- | --- | --- |
| `input_dev_attr_group` | `:1549-1551` | `name` / `phys` / `uniq` / `modalias` / `properties` / `inhibited`（`:1539-1547`） |
| `input_dev_id_attr_group` | `:1576-1579` | 子目录 `id/`，内含 `bustype` / `vendor` / `product` / `version`（`:1563-1566`） |
| `input_dev_caps_attr_group` | `:1646-1649` | 子目录 `capabilities/`，内含 `ev` / `key` / `rel` / `abs` / `msc` / `led` / `snd` / `ff` / `sw` 九张位图（`:1623-1631`） |
| `input_poller_attribute_group` | `:1655` | 只在设备用了 poller 时有内容，见 `04-extensions.md` |

前三组分别由三个宏批量生成：`INPUT_DEV_STRING_ATTR_SHOW`（`:1357-1367`）、`INPUT_DEV_ID_ATTR`（`:1553-1561`）、`INPUT_DEV_CAP_ATTR`（`:1610-1621`）。★ **只有 `inhibited` 是可写的**，其余全部 `S_IRUGO`。

位图的打印统一走 `input_print_bitmap()`（`:1581-1608`），格式是从高位 long 往低位、空格分隔的十六进制。

### B8.2 procfs

`input_proc_init()`（`:1319-1342`）建 `proc_mkdir("bus/input", NULL)`（`:1323`）再挂两个文件：`devices`（`:1327`）和 `handlers`（`:1332`）。

`/proc/bus/input/devices` 每个设备一段（`input_devices_seq_show()`，`:1192-1235`）：

```
I: Bus=0018 Vendor=0000 Product=0000 Version=0000
N: Name="某个触摸屏"
P: Phys=
S: Sysfs=/devices/platform/soc/.../input/input3
U: Uniq=
H: Handlers=event3
PROP=2
EV=b
KEY=400 0 0 0 0 0
ABS=2658000 3
```

★ `H:` 那一行就是遍历 `dev->h_list` 打 `handle->name`（`:1207-1208`）——**它直接反映了 A6 建立的连线，而且顺序就是链表顺序，也就是 filter 型排在前面**。这是"这个设备被哪些 handler 接管了"最快的查法。

★ 能力位图那几行只在对应 `EV_*` 位存在时才打印（`:1214-1229`），所以缺一行不代表异常。

`/proc/bus/input/handlers`（`input_handlers_seq_show()`，`:1285-1298`）一行一个 handler：`N: Number=<序号> Name=<名字>`，filter 型追加 ` (filter)`，`legacy_minors` 为真的追加 ` Minor=<号>`。★ 全树只有 3 个 handler 设了 `legacy_minors`（见 `02-datastructures.md` §3），所以大多数行没有 `Minor=`。

两个 seq 文件的读者会在设备/handler 变动时被唤醒：`input_wakeup_procfs_readers()`（`:1109-1113`）递增 `input_devices_state` 并 `wake_up(&input_devices_poll_wait)`，`input_proc_devices_poll()`（`:1115-1124`）用它实现 `poll()` 上的 `EPOLLPRI`。★ 这是**唯一**一个能让用户态阻塞等待"input 设备列表发生变化"的接口 —— 比轮询 `/proc` 或依赖 udev 更直接。

### B8.3 modalias

`input_print_modalias()`（`:1458-1472`）跑**两遍**：第一遍 `input_print_modalias_parts(NULL, 0, 0, id)` 只算长度，第二遍带着这个长度真写。格式（`:1391-1453`）：

```
input:b<Bus>v<Vendor>p<Product>e<Version>-e<ev>k<key>r<rel>a<abs>m<msc>l<led>s<snd>f<ff>w<sw>
```

两个细节：

- **keybit 从 `KEY_MIN_INTERESTING` 开始打**（`:1406`），而 `KEY_MIN_INTERESTING` = `KEY_MUTE` = 113（`include/uapi/linux/input-event-codes.h:806` 和 `:188`）。也就是说 113 以下的普通按键（字母、数字、功能键）**不进 modalias**，因为它们对"该加载哪个 handler"没有区分度。
- **key 段可以被截断**：缓冲区放不下时 `:1418-1438` 会往回找一个 `k` 或 `,`，把后面换成 `+,`，形如 `k1,2,3,45,+,`。看到 `+` 就说明这个 modalias 是不完整的。

★ **modalias 里没有 prop 段。** `input_match_device_id()` 会比较 `propbit`（`drivers/input/input.c:1028`）、uevent 会导出 `PROP=`（`:1767`）、sysfs 有 `properties` 文件（`:1497`），但 modalias 字符串里就是没有：

```bash
grep -n "propbit" drivers/input/input.c        # → 1028 / 1211 / 1497 / 1767，无一在 modalias 路径上
```

后果是：一个只靠 `INPUT_PROP_*` 来筛设备的 handler **没法被 modalias 自动加载**，必须已经在内存里。本树的 12 个 handler 都不依赖 propbit，所以这个洞暂时不咬人。

## B9 uevent（`drivers/input/input.c:1753-1790`）

`input_dev_type.uevent` = `input_dev_uevent`，在 `device_add()`（A4 第 11 步）时被调，往环境里塞：

| 变量 | 条件 | 行号 |
| --- | --- | --- |
| `PRODUCT=<bus>/<vendor>/<product>/<version>`（十六进制，无前导零补齐） | 无条件 | `:1757-1759` |
| `NAME="…"` / `PHYS="…"` / `UNIQ="…"` | 各自字段非空 | `:1760-1765` |
| `PROP=` | 无条件 | `:1767` |
| `EV=` | 无条件 | `:1769` |
| `KEY=` / `REL=` / `ABS=` / `MSC=` / `LED=` / `SND=` / `FF=` / `SW=` | 对应 `EV_*` 位存在 | `:1770-1785` |
| `MODALIAS=` | 无条件 | `:1787`（`INPUT_ADD_HOTPLUG_MODALIAS_VAR`） |

★ `PRODUCT=` 用的是 `%x`（不是 `%04x`），和 `/proc/bus/input/devices` 里 `I:` 行的 `%04x`**格式不同** —— 同一个设备在两处的字符串长度可能不一样，写匹配规则时别混用。

★ 无条件项和条件项的划分和 `/proc/bus/input/devices` 完全一致（都是"`EV_*` 位存在才导出对应位图"），因为两处用的是同一套判据，只是宏不同。

---

## Part C 评价

Part A 和 Part B 只描述代码"是什么样"。Part C 是对这套代码的评价：作者自己承认的欠债在哪、官方文档和代码对不上的地方在哪、以及本文档集按 arm64-only 裁掉了什么。

### C1 源码自陈的设计债

内核代码里 `REVISIT` / `FIXME` / `TODO` / `XXX` / `HACK` 注释是作者留给后人的欠债记号。对 input 核心统计的结果是 **0**：

```
grep -rnE '/\* *(REVISIT|FIXME|TODO|XXX|HACK)|(REVISIT|FIXME|TODO|XXX|HACK):' \
     drivers/input/*.c drivers/input/*.h \
     include/linux/input.h include/linux/input/*.h
→ 无输出（0 条）
```

统计范围就是上面这四组路径：`drivers/input/` 根目录的 19 个 `.c`/`.h`（含 `input.c` / `input-mt.c` / `ff-core.c` / `ff-memless.c` / `input-poller.c` / `evdev.c` / `mousedev.c` / `joydev.c` 等），加 `include/linux/input.h` 和 `include/linux/input/` 下的 17 个头文件。**核心一条欠债记号都没有。**

把同一条命令的范围扩到 `drivers/input/` 整个目录树（递归，含全部驱动子目录），结果是 **44** 条：

```
grep -rnE '/\* *(REVISIT|FIXME|TODO|XXX|HACK)|(REVISIT|FIXME|TODO|XXX|HACK):' \
     drivers/input/ include/linux/input.h include/linux/input/ | wc -l
→ 44
```

按子目录分布（同一条命令的输出按第二级路径归类）：

| 子目录 | 条数 |
| --- | --- |
| `drivers/input/touchscreen/` | 14 |
| `drivers/input/serio/` | 8 |
| `drivers/input/joystick/` | 8 |
| `drivers/input/misc/` | 7 |
| `drivers/input/keyboard/` | 5 |
| `drivers/input/mouse/` | 2 |
| `drivers/input/` 根目录 + 头文件 | 0 |

★ 这个 0 : 44 的分布本身就是一条结论：**input 的抽象层已经稳定，欠债全部沉到了具体硬件那一层。** 44 条里没有一条是"核心机制没想清楚"，全是"这块芯片的某个寄存器行为不确定""这个 quirk 应该做成 DT 属性"这类硬件适配问题。对读者的意义是：Part A / Part B 描述的机制可以当成"设计定稿"来学；真要动手写驱动，欠债会出现在你自己那块芯片的代码里，不在框架里。

★ 反过来说，**"没有 FIXME"不等于"没有设计问题"**。Part A / Part B 里标 ★ 的那些反直觉点，一条也没有对应的源码注释：`bitmap_subset(…, X_MAX)` 的位边界（A5.2）、空帧被静默吃掉（A8.3）、`input_attach_handler()` 的返回值被丢弃（A5.3）、`input_set_capability()` 对 `EV_PWR` 什么都不做（A3）、`absinfo->flat` 除 joydev 外无人读（B2.3）—— 这些是从代码结构里读出来的，作者并不认为它们是债。

### C2 官方文档与代码不一致的五处

`Documentation/input/` 下的两篇主文档（`input.rst` 281 行、`input-programming.rst` 348 行）成文很早，有五处已经和 6.1 的代码对不上。**凡冲突以代码为准**，这里逐条记下来，免得读者照文档写出编译不过或行为不对的代码。

| # | 文档出处 | 文档怎么说 | 6.1 的代码实际是什么 | 本文档集在哪纠正 |
| --- | --- | --- | --- | --- |
| 1 | `Documentation/input/input-programming.rst` | 教你直接填 `dev->absmin[]` / `absmax[]` / `absfuzz[]` / `absflat[]` 四个数组 | 这四个字段**已不存在**，唯一残迹是 `include/linux/input.h:111` 的一句注释；范围信息统一放在 `dev->absinfo[]`（`struct input_absinfo`），写入口是 `input_set_abs_params()`（`drivers/input/input.c:494-511`） | 本篇 A3；字段布局见 `02-datastructures.md` |
| 2 | `Documentation/input/input-programming.rst` | 提到 `struct input_dev` 有个 `idtype` 字段 | **没有这个字段**；标识信息在 `dev->id`（`struct input_id`：`bustype`/`vendor`/`product`/`version`） | `02-datastructures.md`；匹配用法见本篇 A5.1 |
| 3 | `Documentation/input/input.rst` | 列出的 `struct input_event` 是早期形态，时间戳无条件是一个 `struct timeval time` | uapi 里是**三分支**布局（`include/uapi/linux/input.h:28-47`）：64 位内核用 `__kernel_ulong_t`，32 位 libc 走兼容分支，y2038 之后语义不同 | `04-extensions.md`（evdev 读写协议） |
| 4 | `Documentation/input/input.rst` | input 模块"必须在其他所有 input 模块之前加载" | 6.1 用 `subsys_initcall(input_init)`（`drivers/input/input.c:2769`）保证次序，编译进内核时不存在加载顺序问题；模块场景由符号依赖自动解决 | 本篇 **A1**（已在正文写明，此处不重复） |
| 5 | `Documentation/input/input-programming.rst` | `dev->event()` 回调"可能在中断或下半部里被调用……不能睡眠" | 约束比这更严：它在 `dev->event_lock` 自旋锁里、关中断状态下被调用（`drivers/input/input.c:427` 取锁 → `:350-351` 调用），所以连 `might_sleep` 级别的操作和长耗时忙等都不行 | 本篇 **B1.3**（已在正文写明，此处不重复） |

★ 五处里前三处是**纯过时**（字段被重构掉了），第 4、5 处是**说法偏软**——文档没说错，只是给的约束比真实约束宽，照它写代码会在特定配置下才炸。第 5 处最危险：`CONFIG_DEBUG_ATOMIC_SLEEP` 关掉时，在 `event()` 里睡眠不会立刻报错，只会偶发死锁。

### C3 arm64 上不相关的部分

先说结论：**input 核心是体系结构中立的，本篇没有因为 arm64-only 裁掉任何核心逻辑。** 证据是 `drivers/input/input.c` 里只有三个条件编译块，一个都不是架构条件：

| 条件 | 行区间 | 内容 | arm64 上 |
| --- | --- | --- | --- |
| `CONFIG_COMPAT` | `1068-1101` | `input_bits_to_string()` 的两个版本 | **相关**（arm64 支持 32 位用户态，见下面的 ★） |
| `CONFIG_PROC_FS` | `1103-1355` | `/proc/bus/input/` 全部代码（含 B8.2 的两个 seq_show） | 相关（常规配置都开） |
| `CONFIG_PM_SLEEP` | `1904-1979`、`1985-1987` | `input_dev_pm_ops` 五个回调 + 把它挂进 `input_dev_type` | 相关（B6） |

架构条件的普查结果为空：

```
grep -rn 'CONFIG_X86\|CONFIG_PPC\|CONFIG_ARM\b\|CONFIG_ACPI' drivers/input/*.c drivers/input/*.h
→ 无输出

grep -rn 'CONFIG_X86\|CONFIG_PPC\|CONFIG_ARM64\|CONFIG_ACPI' \
     drivers/input/evdev.c drivers/input/mousedev.c drivers/input/joydev.c \
     drivers/input/input-leds.c drivers/input/evbug.c drivers/input/apm-power.c | wc -l
→ 0
```

★ **`CONFIG_COMPAT` 这一块在 arm64 上是活的，而且会改变用户可见的文本格式。** `input_bits_to_string()`（`drivers/input/input.c:1070-1090`）在 `in_compat_syscall()` 为真时，把一个 64 位 `unsigned long` 拆成高 32 位、低 32 位两个 `%x` 分别打印；否则整个用一个 `%lx` 打印。它的两个调用点是 `:1176`（`/proc/bus/input/devices` 的 `B:` 行）和 `:1589`（sysfs `capabilities/` 下的位图属性）。所以在 64 位 arm64 内核上，**32 位用户态程序读到的位图字符串和 64 位程序读到的不是同一个串**：同一个 `KEY=` 位图，64 位调用者看到的是每个 `unsigned long` 一段（最多 16 个十六进制数字），32 位调用者看到的是同一个 long 被拆成"高 32 位 空格 低 32 位"两段。段数因此翻倍。写解析器时不能假定"每段是一个 64 位十六进制数"，只能按空格切分后逐段拼接。（这两种输出没有在本环境实测，依据是上述代码分支；标注在 `99-evidence.md` §5「推断非观察」。）

真正在 arm64 上不会加载的，是 handler 侧的两个：

| 文件 | 编译开关 | 为什么 arm64 上碰不到 |
| --- | --- | --- |
| `drivers/input/apm-power.c` | `INPUT_APMPOWER`，`drivers/input/Kconfig:169-176`，`depends on INPUT && APM_EMULATION` | `APM_EMULATION` 是 arm32/PPC 的电源管理模拟层，arm64 不提供 |
| `drivers/macintosh/mac_hid.c` | `CONFIG_MAC_EMUMOUSEBTN`（`drivers/macintosh/Makefile:11`），在 `menuconfig MACINTOSH_DRIVERS` 之下，后者 `depends on PPC \|\| MAC \|\| X86`（`drivers/macintosh/Kconfig:3-5`） | 整个 Macintosh 驱动菜单在 arm64 上不可选，所以这个 handler 连编译都不会发生 |

★ 这两个正是 B1.3 之外唯一带 `filter` 的那对里的一半（`mac_hid` 有 `filter`），所以**在 arm64 上，全树带 `filter` 的 handler 实际只剩 `drivers/tty/sysrq.c` 一个**。A6 说的"filter 挂到链表头"这条规则在 arm64 上只有 sysrq 一个受益者。另外三个 handler —— `drivers/tty/vt/keyboard.c`、`drivers/tty/sysrq.c`、`drivers/tty/serial/kgdboc.c` —— 在 arm64 上都正常存在，不受影响。

驱动侧（不属于本篇范围，只作分诊提示）：`drivers/input/` 下有 21 个 `.c` 引用 ACPI（touchscreen 11、misc 4、keyboard 3、mouse 2、serio 1），这些多为 x86 笔记本设备；`SERIO_I8042` 依赖 `ARCH_MIGHT_HAVE_PC_SERIO`（`drivers/input/serio/Kconfig:29-32`），arm64 常规配置下不选。代表驱动的选取和裁剪理由记在 `99-evidence.md` §2.2 / §2.7。

---

## 本篇小结与去向

| 你想知道 | 去哪 |
| --- | --- |
| 某个 API 的签名和返回值 | `01-api.md` |
| 某个结构体字段的含义和布局 | `02-datastructures.md` |
| evdev / uinput / MT / force feedback / poller 怎么工作 | `04-extensions.md` |
| 我要写一个 input 驱动 / 一个 handler，代码骨架长什么样 | `05-usage.md` |
| DTS 怎么写、属性谁消费 | `06-devicetree.md` |
| 事件没出来，怎么查 | `07-debug.md` |
| 这些机制为什么要这样设计 | `08-feynman.md` |
| 本篇每个数字/行号的取证命令 | `99-evidence.md` |
