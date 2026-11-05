---
title: 费曼学习法
---

# IIO 概念费曼解释

> 基线：Linux 6.1.158（`f6e38ae624cf`），架构 arm64。
> 本篇用大白话解释 IIO 里最容易理解错的几组概念。每个类比后面都跟源码行号——类比只是入口，判据永远是代码。

---

## §0 本篇讲什么

前面七篇讲的是「是什么、在哪里、怎么用」。这一篇讲「为什么是这样」。八个主题，按「读者最容易在哪里绕不出来」排序：

| 主题 | 一句话问题 | 相关篇章 |
|---|---|---|
| §1 | 通道和扫描元素为什么是两回事？ | `02-datastructures.md` §2、`07-debug.md` §2.2 |
| §2 | 触发器为什么是一个独立的设备，而不是设备的一个属性？ | `03-internals.md` Part A、`04-extensions.md` |
| §3 | `in_voltage0_raw` 里的数字为什么不是电压？ | `05-usage.md` §4 |
| §4 | 为什么读一个值有两条完全不同的路？ | `03-internals.md` Part B |
| §5 | 一个设备为什么有两套文件描述符？ | `07-debug.md` §5 |
| §6 | 消费者为什么不能直接调 provider 驱动的函数？ | `01-api.md` §4、`06-devicetree.md` §3 |
| §7 | `claim_direct_mode` 为什么叫「声明」而不是「加锁」？ | `03-internals.md` Part B |
| §8 | 为什么内核要把驱动刚推进来的数据再拆一遍？ | `03-internals.md` Part A |

每个主题四段式：**问题** → **先举个例子** → **回到代码** → **这个类比什么时候会误导你**。第四段是重点，别跳。

---

## §1 通道和扫描元素为什么是两回事？

### 先举个例子

想象一台老式的多路记录仪，面板上有 8 个输入端子，每个端子接一路信号。这 8 个端子是**硬件上存在的、你能用手指数的东西**——这是「通道」。

但记录仪的走纸只有那么宽，一次走纸只能画 3 条曲线。所以面板下面还有一排拨杆，你得挑出这次要画哪 3 路，再把拨杆拨上去。**这排拨杆和端子是一一对应的，但它们不是同一个东西**：端子是「这台机器有什么」，拨杆是「这次记录要用什么」。

IIO 里也一样。「通道」（channel）是驱动声明的「这颗芯片有什么」；「扫描元素」（scan element）是「这次缓冲区采集要带哪几路」。

### 回到代码

通道就是 `struct iio_chan_spec`（`include/linux/iio/iio.h:238-270`）。驱动在数组里把每个通道声明一遍：类型（`:239`）、编号（`:240`）、要暴露哪些属性（`info_mask_*`，`:252-259`）。这些信息生成的是 `/sys/bus/iio/devices/iio:device0/` 根目录下的 `in_voltage0_raw` 之类的文件。

扫描元素是同一个 `iio_chan_spec` 的**另一半字段**：

- `scan_index`（`:243`）—— 这个通道在缓冲区的一次采样里排第几位。
- `scan_type`（`:244-251`）—— 它在缓冲区里占多少位、有效位多少、要移几位、什么字节序。

这半边字段生成的是 `bufferN/` 目录下的 `in_voltage0_en` / `_index` / `_type` 三件套（`07-debug.md` §2.2）。**同一个通道，在两个目录下各有一套文件，语义完全不同**：根目录下的 `in_voltage0_raw` 是「现在给我读一个值」，`bufferN/in_voltage0_en` 是「下次采集带上这一路」。

「拨杆的当前状态」在内核里是一个位图：`indio_dev->active_scan_mask`（`:581`），长度 `masklength`（`:580`）。写 `bufferN/in_voltage0_en` 最终落到 `iio_scan_mask_set()`（`drivers/iio/industrialio-buffer.c:448`），它做的就是在这个位图上置一位。缓冲区启动时，`iio_enable_buffers()` 把配置里算好的掩码装到 `indio_dev->active_scan_mask`（`:1066`），并根据它算出一次采样一共多少字节（`scan_bytes`，`:1068`，由 `iio_compute_scan_bytes()` 在 `:705` 算出）。

**`scan_index` 为负意味着这个通道根本没有拨杆。** 有些通道只能单次读（比如芯片内部温度），驱动就把 `scan_index` 设成 -1。两处跳过它的判断分别是 `industrialio-buffer.c:1622`（不给它建扫描元素文件，所以 `bufferN/` 下没有它的 `_en`）和 `industrialio-core.c:1892`（重复索引检查也把它排除，否则多个 -1 会互相撞）。

### 这个类比什么时候会误导你？

三处：

**第一，拨杆不一定能任意组合。** 记录仪上 8 个拨杆你想拨哪 3 个都行，硬件不管。但很多 IIO 芯片只支持固定的几种组合（比如「只能全开」或「只能开 X/Y/Z 前 N 个」）。驱动通过 `indio_dev->available_scan_masks` 声明「我只认这几种掩码」，核心用 `iio_scan_mask_match()`（`industrialio-buffer.c:409`）把你要的掩码往上匹配到最近的一个受支持组合。所以**你开了 1 路，硬件可能实际在采 3 路**——这就是 §8 要讲的 demux 存在的根本原因。

**第二，「一次采样」在记录仪上是连续的墨迹，在 IIO 里是一段有明确字节布局的二进制。** 这段布局由 `scan_type` 逐字段决定，不是「按通道顺序排好就行」：`storagebits` 决定占几字节，`shift` 决定有效位在哪，还有对齐规则。搞错了就是读出来的数全是乱的——`07-debug.md` §7.4 里那条 `Channel %d storagebits (%d) < shifted realbits` 就是核心在帮你抓这类错。

**第三，「时间戳」是一路没有对应端子的拨杆。** `scan_timestamp`（`:582`）是缓冲区级别的开关，打开后核心会在每次采样末尾追加一个 64 位时间戳。它在 `bufferN/` 下有 `in_timestamp_en` 文件，但**在设备根目录下没有对应的 `in_timestamp_raw`**。这是「拨杆比端子多一个」的情况，类比里没有对应物。

---

## §2 触发器为什么是一个独立的设备，而不是设备的一个属性？

### 先举个例子

一间摄影棚里有三台相机和一盏闪光灯。闪光灯不属于任何一台相机——它是独立的一件器材，插在自己的电源上。三台相机可以各自决定「我跟这盏灯同步」，闪光灯一亮，跟它同步的相机同时按下快门。

如果闪光灯是「相机 A 的一个开关」，那相机 B 和 C 就没法用它了。器材要能被多台设备共用，它就必须是独立的一件器材，有自己的名字，让别人能指名道姓地挂上去。

IIO 的触发器就是这盏闪光灯。

### 回到代码

触发器是 `struct iio_trigger`（`include/linux/iio/trigger.h:60`），**它自己就是一个注册在 `iio_bus_type` 上的设备**：

```c
trig->dev.type = &iio_trig_type;      // industrialio-trigger.c:562
trig->dev.bus  = &iio_bus_type;       // :563
```

注册走的是 `iio_trigger_register()`（`:67`），里面一句 `device_add(&trig_info->dev)`（`:78`）。所以你在 `/sys/bus/iio/devices/` 下能同时看到 `iio:device0` 和 `trigger0` —— **它们是同一条总线上的两类设备**（这一点在 `02-datastructures.md` §1.0 里作为「最容易类比错的关系」展开过）。

「挂上去」的动作是往 `trigger/current_trigger` 写触发器的名字。核心据名字找到那个 `iio_trigger`，然后调 `iio_trigger_attach_poll_func()`（`:274`）把设备的采集函数挂到触发器上。挂的方式是**分配一个软中断号**：

```c
pf->irq = iio_trigger_get_irq(trig);              // :286
ret = request_threaded_irq(pf->irq, pf->h, pf->thread, ...);  // :294-296
```

触发器内部预留了 `CONFIG_IIO_CONSUMERS_PER_TRIGGER` 个子中断槽位（`trigger.h:74` 的 `subirqs[]` 数组、`:75` 的 `pool` 位图），`subirq_base`（`:72`）是这批中断号的起点。**这就是 §1.1 那个 Kconfig 上限的物理来源**：槽位是编译期定长数组，用满了就 `pr_err`（`industrialio-trigger.c:288-289`）。

触发时刻的动作在 `iio_trigger_poll()`（`:195-209`）：

```c
atomic_set(&trig->use_count, CONFIG_IIO_CONSUMERS_PER_TRIGGER);   // :200
for (i = 0; i < CONFIG_IIO_CONSUMERS_PER_TRIGGER; i++) {
	if (trig->subirqs[i].enabled)
		generic_handle_irq(trig->subirq_base + i);            // :203
	else
		iio_trigger_notify_done_atomic(trig);                 // :206
}
```

一次闪光，所有挂上来的槽位一起点火。每个消费者干完活调 `iio_trigger_notify_done()`（`:236`），它 `atomic_dec_and_test(&trig->use_count)`（`:238`），减到 0 才调 `trig->ops->reenable`（`:240`）—— **闪光灯要等所有相机都拍完才重新充电**。

### 这个类比什么时候会误导你？

四处：

**第一，闪光灯不知道有几台相机，IIO 触发器也不知道。** 注意 `:200` 那行：`use_count` 一上来就设成**编译期常量** `CONFIG_IIO_CONSUMERS_PER_TRIGGER`，不是实际挂了几个。空槽位靠 `:206` 的 `notify_done_atomic` 立即减掉。所以「减到 0」这件事对空槽位和忙槽位是同一个计数器——理解 `reenable` 的时机必须看到这一层，否则会以为 `use_count` 是「挂了几个消费者」。

**第二，`iio_trigger_poll()` 里那个 `if (!atomic_read(&trig->use_count))` 是个丢触发的门。** 上一轮还没全部 done 时，这一轮触发**整个被静默丢掉**，不排队、不计数、没有日志。闪光灯的类比在这里失效：真闪光灯你按第二次它就是不亮（没充电），而这里是「亮了但所有相机都没反应」。采样率上不去而没有任何报错时，先怀疑这一处。

**第三，有两个几乎一样的点火函数，用错了会死锁。** `iio_trigger_poll()`（`:195`）用 `generic_handle_irq`，给硬中断上下文用；`iio_trigger_poll_chained()`（`:219`）用 `handle_nested_irq`（`:228`），给已经在线程化中断里的场合用。两者结构逐行对称，唯一差别是那一行和收尾调的是 `notify_done` 还是 `notify_done_atomic`。名字里没有任何「上下文」的暗示——`05-usage.md` §7 的近邻符号节把它们列在一起就是因为这个。

**第四，「同步」在摄影棚里是物理的，在这里是两级软中断。** 挂上去的不是一个回调，是一对：`pf->h`（顶半部）和 `pf->thread`（底半部）。典型驱动用 `iio_pollfunc_store_time`（`:348`）当顶半部——它唯一的工作就是**在中断上下文里记下时间戳**，真正读芯片在底半部线程里做（见 `drivers/iio/buffer/industrialio-triggered-buffer.c:65-66` 的 `iio_alloc_pollfunc` 调用）。所以缓冲区里的时间戳是「触发那一刻」而不是「读到数那一刻」，这个差值在 SPI 慢设备上可以是几百微秒。

---

## §3 `in_voltage0_raw` 里的数字为什么不是电压？

### 先举个例子

你去药房买了一台电子体重秤，但它出厂时坏了一件事：显示屏只能显示内部传感器的原始读数。你站上去，屏幕显示 `13480`。

说明书告诉你两件事：「每 200 个读数等于 1 公斤」和「空秤时读数是 400」。于是你自己算：`(13480 - 400) / 200 = 65.4` 公斤。

这就是 IIO 的三件套：`_raw` 是屏幕上那个 13480，`_offset` 是那个 400，`_scale` 是那个 1/200。**内核故意不替你算**，它只把三个数摆出来。

### 回到代码

三个文件名来自同一张后缀表 `iio_chan_info_postfix[]`（`drivers/iio/industrialio-core.c:146-178`）：

| 后缀 | 表里位置 | 语义 |
|---|---|---|
| `raw` | `:147` | 传感器直出的整数 |
| `offset` | `:150` | 先加上它 |
| `scale` | `:149` | 再乘上它 |
| `input` | `:148` | 已经算好的物理量（`IIO_CHAN_INFO_PROCESSED`） |

**公式的顺序是「先加 offset，再乘 scale」，不是反过来。** 依据在 `iio_convert_raw_to_processed_unlocked()`（`drivers/iio/inkern.c:631`）：`:662` 先 `raw64 += offset_val`，然后 `:665-666` 才读 scale，`:678` 起才做乘法。顺序搞反在 offset 非 0 时结果就是错的。

麻烦在于 `_scale` 不是一个整数。sysfs 里它可能长这样：`0.000122070`、`4/1000`、`2.5`。这是因为回调返回的是 **`(val, val2, 类型码)` 三元组**，类型码决定这两个整数怎么拼成一个数。类型码定义在 `include/linux/iio/types.h:24-32`：

| 类型码 | 值 | 含义 | sysfs 长什么样 |
|---|---|---|---|
| `IIO_VAL_INT` | `:24` = 1 | `val` 就是整数 | `2` |
| `IIO_VAL_INT_PLUS_MICRO` | `:25` = 2 | `val` + `val2`×10⁻⁶ | `0.000122` |
| `IIO_VAL_INT_PLUS_NANO` | `:26` = 3 | `val` + `val2`×10⁻⁹ | `0.000000122` |
| `IIO_VAL_INT_MULTIPLE` | `:28` = 5 | 一串整数 | `1 2 3` |
| `IIO_VAL_INT_64` | `:29` = 6 | 64 位，`val` 是低 32 位 | 一个大整数 |
| `IIO_VAL_FRACTIONAL` | `:30` = 10 | `val` ÷ `val2` | 内核负责算成小数打印 |
| `IIO_VAL_FRACTIONAL_LOG2` | `:31` = 11 | `val` >> `val2` | 同上 |
| `IIO_VAL_CHAR` | `:32` = 12 | 一个字符 | 一个字母 |

把三元组变成 sysfs 里那串字符的是 `iio_format_value()`（`industrialio-core.c:727`），走 sysfs 读的入口是 `iio_read_channel_info()`（`:755`）。它先调驱动的 `read_raw_multi`（`:765-766`）或 `read_raw`（`:770-771`），再交给 `iio_format_value` 格式化。

**内核里的消费者不走 sysfs，走 `iio_read_channel_processed()`（`inkern.c:790`）**——这个函数会替你把三步做完（内部转调 `iio_read_channel_processed_scale()`，`:757`，再调 `iio_convert_raw_to_processed_unlocked()`，`:779`）。所以「谁替你算」取决于你在内核里还是用户态：内核里有现成的，用户态没有。

### 这个类比什么时候会误导你？

四处，其中第二处最伤人：

**第一，`_offset` 和 `_scale` 可能一个都不存在。** 体重秤的说明书总会给你两个数，IIO 不保证。`iio_convert_raw_to_processed_unlocked()` 里读 offset 用的是 `if (offset_type >= 0)`（`:641`）——**读不到就当 0**；读不到 scale 更干脆，`:667-674` 直接把 raw 乘上消费者自己的 scale 就返回了。所以「没有 `_scale` 文件」不是坏了，是这颗芯片的 raw 值本来就已经是那个单位（或者驱动偷懒了）。

**第二，`IIO_VAL_INT_PLUS_MICRO` 的 offset 会被静默截断成整数。** 代码里带注释写明了（`inkern.c:645-651`，注释本体在 `:647-650`）：`IIO_VAL_INT_PLUS_MICRO` 和 `IIO_VAL_INT_PLUS_NANO` 两种类型的 offset，**小数部分直接丢**，只用 `val`。体重秤的类比在这里彻底失效——说明书上写「空秤 400.5」，机器只会拿 400 去算。如果你的 offset 精度重要，就不能依赖 `iio_read_channel_processed()`，得自己读三个值算。

**第三，`_input` 存在时不要再自己算。** `IIO_CHAN_INFO_PROCESSED` 生成的文件叫 `input`（不叫 `processed`，`:148`）。它已经是物理量了，**再乘一遍 scale 就是两次换算**。同一个通道上 `_raw` 和 `_input` 通常只有一个，但驱动可以两个都给——看到两个文件，读哪个决定了你要不要自己算。

**第四，负数 scale 的符号处理是特殊的。** 看 `inkern.c:680-684`：`IIO_VAL_INT_PLUS_MICRO` 类型下如果 `scale_val2 < 0`（`:681`），整数部分要**取负**再乘（`:682`）。这是因为 `(val, val2)` 这种表示法里负号只能挂在其中一个上，代码选择了「val2 携带符号」的约定。自己在用户态解析 `_scale` 字符串时不会碰到这个坑（字符串里符号是显式的），但读驱动代码时会。

---

## §4 为什么读一个值有两条完全不同的路？

### 先举个例子

你想知道现在几点。两种办法：

1. **抬头看一眼墙上的钟。** 想知道就看，看完就完，什么都不用准备。
2. **让秘书每隔一秒把时间记在本子上，你隔一会儿去翻本子。** 要先跟秘书说好频率、给她一本子、告诉她记到第几页就叫你。设置麻烦，但你能拿到一串连续的时间点，而且不会因为你走神而漏掉。

IIO 的两条读值路径就是这个区别。第一条叫 **direct 模式**（sysfs 单次读），第二条叫 **buffer 模式**（字符设备 + 触发器）。

### 回到代码

**第一条路**：`cat in_voltage0_raw` → `iio_read_channel_info()`（`industrialio-core.c:755`）→ 驱动的 `read_raw`（`:770-771`）→ 立刻返回。整条路上没有队列、没有中断、没有时间戳。同步阻塞，读多快取决于总线多快。

**第二条路**：写 `bufferN/enable` → `iio_enable_buffers()`（`industrialio-buffer.c:1059`）装配扫描掩码和字节数（`:1066-1068`）→ 触发器点火 → 驱动底半部读芯片 → `iio_push_to_buffers()`（`:1857`）把一次采样推进队列 → 用户态从 `/dev/iio:deviceN` 读走。

两条路的分岔点在 `indio_dev->modes` 和当前 `currentmode`（`:1069` 赋值）。**核心用一对语义相反的 API 来断言你在哪条路上**：`iio_device_claim_direct_mode()`（`industrialio-core.c:2070`）要求现在没在跑缓冲区，`iio_device_claim_buffer_mode()`（`:2108`）要求正在跑。这一对就是 §7 的主题。

一个关键的不对称：**第一条路上没有时间戳，第二条路上有**。缓冲区里的时间戳是触发那一刻打的（典型驱动用 `iio_pollfunc_store_time`，`industrialio-trigger.c:348`），时钟源可以通过 `current_timestamp_clock`（`industrialio-core.c:1532`）选。sysfs 单次读**没有任何时间信息**——你读到的值是什么时候采的，无从得知。

### 这个类比什么时候会误导你？

三处：

**第一，抬头看钟不会打扰秘书，但 sysfs 单次读会跟缓冲区抢设备。** 很多驱动在 `read_raw` 里第一句就是 `iio_device_claim_direct_mode()`，缓冲区在跑时它返回 `-EBUSY`（`:2076`），于是 `cat in_voltage0_raw` 直接失败。反过来，`07-debug.md` §7.3 的 A 组列了五处「缓冲区在跑就不许改配置」。**两条路不是并行的两个窗口，是互斥的两种状态**——这是类比里最严重的偏差。

**第二，秘书按你说的频率记，IIO 的采样率不由缓冲区决定。** 频率来自触发器（§2）。缓冲区只管「存多少」（`length`）和「攒到多少叫我」（`watermark`）。没挂触发器就开缓冲区，得到的是 `-EINVAL` 且**默认无日志**（`industrialio-buffer.c:884` 的 `dev_dbg`，还有 `:881-885` 那个连 `dev_dbg` 都没有的静默路径，见 `07-debug.md` §4.4）。

**第三，本子翻慢了会覆盖，而且覆盖是静默的。** `data_available` 告诉你现在积了多少（`:1398`），但缓冲区满了之后新数据的处置由具体缓冲区实现决定（kfifo 实现是丢新的）。**没有任何 sysfs 计数器告诉你丢了几个**。读慢导致的数据缺口只能靠时间戳的跳变自己发现——这是 IIO 观测面窄的一个具体后果。

---

## §5 一个设备为什么有两套文件描述符？

### 先举个例子

一栋写字楼只有一个大门，但门内有两条通道：一条通往仓库（走货），一条通往前台（送通知）。早期这栋楼只有一个前台窗口，货和通知都从那儿过，挤得很。后来加了一道侧门，货专走侧门，前台就只管通知了。

问题是**大门还在**，老规矩也还在——所以现在有三种进法：走大门取货（老办法）、走大门领通知、走侧门取货（新办法）。而且楼里规定：一旦有人从侧门取过货，大门那条取货通道就封了。

`/dev/iio:deviceN` 就是这栋楼。

### 回到代码

主字符设备的 fops 是**二选一**装上去的（`industrialio-core.c:1984`/`:1986`）：

| fops | 位置 | 有 `.read` 吗 | 用途 |
|---|---|---|---|
| `iio_buffer_fileops` | `industrialio-core.c:1862-1872` | 有（还有 write / poll） | 有缓冲区时装这个 |
| `iio_event_fileops` | `industrialio-core.c:1874-1881` | **没有** | 只有事件能力时装这个 |

`devt` 只在「有缓冲区」或「有事件接口」之一成立时才赋值（`industrialio-core.c:1988-1991`），两者都没有就根本不创建 `/dev/iio:deviceN`。

两条通道分别用一个 ioctl 换一个新 fd：

| ioctl | 定义位置 | 分派位置 | 换来什么 |
|---|---|---|---|
| `IIO_GET_EVENT_FD_IOCTL` | `include/uapi/linux/iio/events.h:27` | `industrialio-event.c:502-503` | 事件专用 fd |
| `IIO_BUFFER_GET_FD_IOCTL` | `include/uapi/linux/iio/buffer.h:8` | `industrialio-buffer.c:1593-1594` | 某个 `bufferN` 专用 fd |

**「大门取货通道被封」这条规矩是真的**：`industrialio-buffer.c:272` 和 `:285` 两处，注释写着 `check if buffer was opened through new API`——一旦某个缓冲区通过新 ioctl 拿过 fd，主设备上的老 `read()` / `write()` 一律返回 `-EBUSY`。

**大门本身是独占的**：`iio_chrdev_open()`（`industrialio-core.c:1768-1793`）用 `test_and_set_bit(IIO_BUSY_BIT_POS, ...)`，已被占用就 `-EBUSY`（`:1775-1776`），关闭时清位（`:1810`）。侧门也各自独占：per-buffer fd 在 `industrialio-buffer.c:1543`，事件 fd 在 `industrialio-event.c:206`。

### 这个类比什么时候会误导你？

四处：

**第一，「拿到侧门钥匙」之后大门可以关掉。** `tools/iio/iio_event_monitor.c` 就是这么干的：`:385` 打开主设备，`:392` 发 ioctl 拿事件 fd，然后 **`:406` 立刻把主 fd 关掉**。这是刻意绕开独占——它拿完钥匙就走，别的程序还能开主设备。这个模式在类比里说不通（钥匙拿了门就该锁着），但在 fd 语义下完全合理：新 fd 持有的是独立引用。

**第二，事件通道从来不能走大门。** `iio_event_fileops`（`industrialio-core.c:1874-1881`）**没有 `.read`**。「早期货和通知都从前台过」这半句对事件不成立——事件从一开始就必须先 ioctl 换 fd 才能读。类比里的「三种进法」实际只有两种半。

**第三，新拿的事件 fd 会把积压的通知全扔掉。** `iio_event_getfd()` 里一句 `kfifo_reset_out()`（`industrialio-event.c:218`）。你以为换个窗口能看到之前没取的通知，实际是清空重来。调试事件丢失时这一点必须记住：**重开 monitor 不会看到之前的事件**。

**第四，`IIO_BUFFER_GET_FD_IOCTL` 的方向是 `_IOWR` 而不是 `_IOR`。** 看 `include/uapi/linux/iio/buffer.h:8`——你要**传进去**一个缓冲区索引，它把 fd **写回**同一个变量。事件那个是纯 `_IOR`（`include/uapi/linux/iio/events.h:27`）。两个 ioctl 长得像，调用约定不一样，抄错了拿到的是随机索引对应的缓冲区。

---

## §6 消费者为什么不能直接调 provider 驱动的函数？

### 先举个例子

你在公司里要问隔壁部门的老张一个数据。两种做法：

1. **直接走过去拍他肩膀。** 快，但如果老张今天调岗了、离职了、或者正在开会，你就尴尬了——而且你根本不知道他还在不在。
2. **通过工单系统提。** 你填一张单子写「要 XX 数据」，系统查现在这活谁负责，转给他，他填完回给你。麻烦一层，但老张调岗系统会自动转给接手的人，老张离职系统会告诉你「无人负责」。

内核里「老张离职」这件事叫 **provider 驱动被 rmmod 了**。IIO 的消费者 API 就是那个工单系统。

### 回到代码

消费者手里拿到的不是 provider 的函数指针，而是一张单子：`struct iio_channel`（`include/linux/iio/consumer.h:24-28`）。它只有三个字段：

```c
struct iio_channel {
	struct iio_dev *indio_dev;                 // 哪个设备
	const struct iio_chan_spec *channel;       // 哪个通道
	void *data;                                // 消费者自己的私货
};
```

拿这张单子的入口是 `iio_channel_get()`（`drivers/iio/inkern.c:381`），归还是 `iio_channel_release()`（`:398`）。**拿的时候真的加了引用计数**：`iio_channel_get_sys()` 里一句 `iio_device_get(c->indio_dev)`（`:346`），归还时 `iio_device_put()`（`:402`）。`iio_device_get` / `iio_device_put` 本身是 `include/linux/iio/iio.h:667` / `:641` 的 static inline，底层就是 `get_device` / `put_device`。

**引用计数只保证「结构体不会被释放」，不保证「驱动还在」。** 这两件事是分开的，所以还要第二道保险：每次真正去读值之前，检查 `indio_dev->info` 是不是还在。`iio_read_channel_raw()`（`inkern.c:593`）的头三行：

```c
mutex_lock(&iio_dev_opaque->info_exist_lock);      // :597
if (!chan->indio_dev->info) {                      // :598
	ret = -ENODEV;                             // :599
```

`info` 被置空是 provider 注销时干的动作。所以 rmmod provider 之后，消费者的每次读都干净地返回 `-ENODEV`，不会踩到已释放的函数指针。**这就是「老张离职，系统告诉你无人负责」**。

真正的读值发生在 `iio_channel_read()`（`:561`），它做两级分派：

```c
if (!iio_channel_has_info(chan->channel, info))
	return -EINVAL;                    // :573-574  这个通道没声明这项
if (iio_info->read_raw_multi)              // :576
	...
else if (iio_info->read_raw)               // :583
	...
else
	return -EINVAL;                    // :586-587  驱动没实现回调
```

**两个 `-EINVAL`，两个完全不同的原因**——`07-debug.md` §7.3 把这一对单独拎出来讲过：`:574` 是「provider 没在 `info_mask` 里声明」，`:587` 是「provider 声明了但回调是空的」。判据是去看 provider 的 `info_mask_*` 有没有那一位。

「工单系统查现在这活谁负责」有两条查法：DT 路径（`io-channels` / `io-channel-names`，见 `06-devicetree.md` §3）和板级映射表路径（全局链表 `iio_map_list`，`inkern.c:25`，由 `iio_map_array_register()` 在 `:43` 往里加）。两条路殊途同归，都是填出那张三字段的单子。

### 这个类比什么时候会误导你？

四处：

**第一，工单系统不会替你排队，`info_exist_lock` 会。** 那把锁（`:597`）是**每次读都要拿的互斥锁**。多个消费者同时读同一个 provider 会串行化。类比里的工单系统是并发的，这里不是——高频读多路通道时这把锁是真实的瓶颈。

**第二，`-ENODEV` 在 `inkern.c` 里有时不是错误，是「继续找下一家」的信号。** `:265`、`:280`、`:390`、`:466` 这几处的写法是 `if (!IS_ERR(chan) || PTR_ERR(chan) != -ENODEV)` 就停下——**只有 `-ENODEV` 才继续往 `io-channel-ranges` 上级节点找**。这意味着 provider 返回别的错误码会让整条查找链立刻中止。工单系统的类比在这里不成立：「无人负责」在这个系统里是「往上一级问」的意思，不是终局。

**第三，「单子」不持有对 provider 模块的引用。** 上面说的引用计数是设备引用，`iio_channel_get()` 路径里**没有 `try_module_get`**。真正持有模块引用的是触发器路径：`iio_trigger_attach_poll_func()` 里的 `__module_get(iio_dev_opaque->driver_module)`（`industrialio-trigger.c:283`），注释写明了目的是「挂着触发器时不许卸模块」。所以**挂了触发器的模块 rmmod 不掉，只是被消费者 get 过通道的模块 rmmod 得掉**——两种资源两种策略，别混。

**第四，`of_xlate` 的存在说明「通道编号」不一定是数组下标。** `include/linux/iio/iio.h:427-433` 的 kerneldoc 说明驱动可以提供自定义的 `of_xlate`，把 DT 里写的规格符翻译成通道索引。默认实现是把 DT 里的数字当 `channel` 字段去匹配，但驱动可以改。所以「DTS 里写 `<&adc 3>` 就是 `channels[3]`」这个直觉**只在默认实现下成立**——`06-devicetree.md` §3.2 里点了名的那几个驱动就不是。

---

## §7 `claim_direct_mode` 为什么叫「声明」而不是「加锁」？

### 先举个例子

一间会议室，墙上挂着一块牌子，两面：「空闲」和「使用中」。

- **加锁**的语义是：门锁着，你拿钥匙去开，开不了就在门外等，等到里面的人出来。
- **声明**的语义是：你看牌子。写着「空闲」，你翻成「使用中」然后进去；写着「使用中」，你**转身就走**，不等。

`iio_device_claim_direct_mode()` 是第二种。它不是「等到设备空闲」，它是「设备现在不空闲就告诉你不行」。

### 回到代码

`iio_device_claim_direct_mode()` 在 `drivers/iio/industrialio-core.c:2070`，kerneldoc 从 `:2060` 起。它的行为：缓冲区正在跑就返回 `-EBUSY`（`:2076`），成功则一直保持到 `iio_device_release_direct_mode()`（`:2091`）被调用。

配对的反向 API 是 `iio_device_claim_buffer_mode()`（`:2108`）：**缓冲区没在跑才返回 `-EBUSY`**（`:2116`），kerneldoc 在 `:2098` 起。

```
claim_direct_mode   → 我要求：现在没人在跑缓冲区
claim_buffer_mode   → 我要求：现在正在跑缓冲区
```

两个名字对称、返回同一个错误码 `-EBUSY`、kerneldoc 格式一样，**触发条件正好相反**。`07-debug.md` §7.3 的 D 组把这一对单独列成一组就是因为它是最容易看反的地方。

典型用法在驱动的 `read_raw` 里：先 claim direct mode，读寄存器，release。这保证了「单次读」和「缓冲区采集」不会同时碰硬件——`04-extensions.md` 里那句「一个 IIO 设备同一时刻只处于一种模式」的执行者就是这一对函数。

### 这个类比什么时候会误导你？

三处：

**第一，牌子不会自己翻回来。** claim 成功后必须显式 release，忘了就是设备永久卡在 direct 模式，之后所有缓冲区启动都失败。这不是死锁（没人在等），是**状态泄漏**——现象是「重启前都好，某次单次读之后缓冲区再也开不起来了」，而且没有任何日志。比死锁难查，因为 lockdep 抓不到。

**第二，「转身就走」意味着调用者必须处理失败，而 sysfs 层不会替你重试。** 用户态 `cat in_voltage0_raw` 在缓冲区跑着的时候拿到的就是 `-EBUSY`，一次就失败，内核不排队不重试。写脚本轮询 sysfs 值的人经常在这里被绊倒——脚本必须自己处理这个错误码。

**第三，这一对函数管的是「模式」，不是「设备被谁用」。** 会议室的类比暗示一个占用者，但 IIO 的 direct/buffer 是**全设备的状态**，多个缓冲区可以同时处于 buffer 模式（`currentmode` 在 `industrialio-buffer.c:1069` 赋值，是设备级字段）。所以 claim direct mode 失败不等于「有一个进程占着」，而是「设备整体在采集状态」——这两句话在排查时导向完全不同的动作。

---

## §8 为什么内核要把驱动刚推进来的数据再拆一遍？

### 先举个例子

你在超市门口收快递。快递员一次只能整箱送——一箱固定装 8 件货，哪怕你只订了 3 件，他也得整箱搬来（箱子是标准箱，拆不了）。

于是你在门口摆张桌子：箱子到了，你按订单从里面挑出那 3 件，装进自己的袋子，剩下 5 件扔掉。**每一位客人的袋子内容不一样，但箱子只有一种**。

这张桌子就是 demux。

### 回到代码

「箱子只有一种」的原因在 §1 讲过：芯片可能只支持固定的几种扫描掩码。核心用 `iio_scan_mask_match()`（`drivers/iio/industrialio-buffer.c:409`）把每个缓冲区要的掩码往上匹配到最近的受支持组合，匹配点在 `:469` 和 `:910`。**硬件实际采的是匹配后的那个掩码，可能比你要的宽。**

而「每位客人袋子不一样」的原因是**一个 IIO 设备可以挂多个缓冲区**，每个缓冲区各有自己的 `scan_mask`。所以驱动推进来的那一坨字节（按硬件掩码布局）必须按每个缓冲区的需求各裁一份。

裁剪表是 `struct iio_demux_table`（`:936-940`，kerneldoc 在 `:929-935`），本质是一串 memcpy 指令：三个字段 `from` / `to` / `length`（`:937-939`）就是「从哪拷、拷到哪、拷几字节」。构造时传进去的局部变量叫 `in_loc` / `out_loc`，两者都在 `:1008-1009`、`:1019-1020` 按 `length` 做过 `roundup` 对齐。构造在 `iio_buffer_update_demux()`（`:974`）：逐通道比较硬件掩码和这个缓冲区的掩码，需要的就 `iio_buffer_add_demux()`（`:953`）加一条拷贝指令，不需要的就跳过（两个 `add_demux` 调用点在 `:1010` 和 `:1021`，分别对应通道数据和时间戳）。中转用的临时缓冲区是 `demux_bounce`（`:1026` 分配）。

每个缓冲区一张表，挂在 `buffer->demux_list`（`:333` 初始化）。缓冲区启动时统一重建：`iio_enable_buffers()` 里一句 `iio_update_demux(indio_dev)`（`:1071`），它遍历所有活跃缓冲区各建一张（`:1039-1046`）。

驱动推数据的入口是 `iio_push_to_buffers()`（`:1857`，声明在 `include/linux/iio/buffer.h:19`）。驱动给的**永远是按硬件掩码的完整一坨**，拆分是核心的事——驱动不需要知道有几个缓冲区、各要哪几路。

### 这个类比什么时候会误导你？

四处：

**第一，「扔掉 5 件」意味着白采了，而这个开销不出现在任何计数器里。** 你开 1 路、硬件采 8 路，总线上跑的是 8 路的数据量。sysfs 里没有任何地方告诉你「实际采了几路」——`bufferN/in_*_en` 显示的是你要的，不是硬件在干的。想知道真实开销只能去读驱动的 `available_scan_masks`。

**第二，只有一个缓冲区且掩码正好匹配时，demux 是恒等变换但仍然执行。** `iio_update_demux()` 不会因为「不用拆」而跳过——它照样建表、照样走 `demux_bounce` 中转。这是一次多余的 memcpy。类比里「订单正好是一整箱」时你会直接把箱子搬走，代码不会。

**第三，时间戳的拆法和通道不一样。** `:1021` 那处 `add_demux` 是专门给时间戳的，因为时间戳在硬件那坨数据里的位置由 `scan_bytes` 末尾决定，而且它的对齐要求（8 字节）跟普通通道不同。所以「时间戳只是第 N 路通道」这个简化理解会在读 demux 代码时卡住。

**第四，`iio_push_to_buffers_with_timestamp()` 不是 `iio_push_to_buffers()` 加个参数。** 它是 `include/linux/iio/buffer.h:37` 的 static inline，内部按 `scan_bytes / sizeof(int64_t) - 1` 算出末尾槽位（`:41`）、把时间戳写进去（`:42`），再转调 `iio_push_to_buffers()`（`:45`）。**它假定 `data` 是 8 字节对齐的**。还有第三个变体 `iio_push_to_buffers_with_ts_unaligned()`（`include/linux/iio/buffer.h:48`，实现在 `industrialio-buffer.c:1886`），给对齐没保证的场合用。**三个函数选错了后果是时间戳错位而数据看起来正常**——最难查的那类 bug。`05-usage.md` §7 的近邻符号节列了这三个。

---

## §9 读到这里之后

| 想接着看什么 | 去哪篇 |
|---|---|
| 这些概念对应的结构体长什么样、字段之间怎么连 | `02-datastructures.md` |
| 注册、启动缓冲区、挂触发器的完整时序 | `03-internals.md` Part A |
| 自己写一个 provider 驱动 / 消费者驱动的骨架 | `05-usage.md` |
| DTS 该怎么写、写错了什么现象 | `06-devicetree.md` |
| 出问题了去看哪个文件、哪条日志 | `07-debug.md` §8 一页速查 |
| 本文档集的每条论断出处、哪些没验证 | `99-evidence.md` |

**本篇八个主题的共同点**：IIO 的每一处「看起来多余的间接层」都对应一个真实的约束——硬件掩码不自由（§1、§8）、器材要共用（§2）、单位换算不能在内核里做浮点（§3）、两种读法互斥（§4、§7）、老 ABI 要兼容（§5）、provider 会消失（§6）。**理解这些约束比记住结构体字段更省事**：约束不变，字段会改。
