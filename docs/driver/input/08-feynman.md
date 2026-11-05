---
title: 费曼学习法
---

# input 子系统：大白话解释十个绕不过去的概念

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
>
> 本篇的写法：每个概念四段——**问题**（只说要解释什么）、**先举个例子**（生活类比）、**回到代码**（源码行号，一条不省）、**这个类比什么时候会误导你**（类比和代码的真实差异）。
>
> 第四段是本篇的重点。类比只是脚手架，脚手架总有拆掉的那一刻；不说清它在哪一点上失真，读者会拿类比去推结论，然后得到一个错的结论。

## §0 十个概念，按"读了会少踩哪个坑"排

| # | 问题 | 读懂它能避免 |
|---|------|-------------|
| §1 | `input_dev` 为什么不注册在任何总线上 | 到 `/sys/bus/` 下面去找输入设备，找不到就以为设备没起来 |
| §2 | `input_dev` / `input_handler` / `input_handle` 这个三角在描述什么 | 把 handler 当成 driver，去找"为什么没绑上" |
| §3 | 为什么一个键盘会被三四个 handler 同时接管 | 以为 `/dev/input/event0` 和 `/dev/input/mouse0` 是两台设备 |
| §4 | 那个 `SYN_REPORT` 到底是什么 | 驱动里忘了 `input_sync()`，事件一个都收不到 |
| §5 | 核心什么时候会替你劈开一帧 | 上层看到一帧被切成两半，怀疑是自己丢了包 |
| §6 | 多点触控的 slot 是什么 | 把 slot 当成"第几根手指" |
| §7 | 为什么 `capabilities/abs` 是十六进制，坐标范围却只能用 ioctl 拿 | 在 sysfs 里翻遍了也找不到 min/max |
| §8 | `EVIOCGRAB` 之后为什么别人什么都收不到 | 跑着 X/Wayland 的机器上用 evtest，看到"设备没事件" |
| §9 | `inhibited` 是"静音"不是"关机" | 写完 `inhibited` 忘了写回去，设备看起来彻底坏了 |
| §10 | 为什么核心一个 tracepoint 都没有 | 花半天时间找一个不存在的 trace event |

---

## §1 `input_dev` 为什么不注册在任何总线上？

### 先举个例子

想象一栋写字楼。**总线**是楼里的一条走廊：走廊两侧是一间间办公室（设备），走廊尽头挂着一张招租告示板（driver 列表）。有人搬进一间办公室，物业就拿房间的门牌号去比对告示板上每一条"我要什么样的房间"，比对上就把租客（driver）领进去。这是 Linux 驱动模型的标准形状——PCI、SPI、I2C、USB 都是这样的走廊。

**input 不是走廊。** 它更像楼里的**广播系统**。一台输入设备接进来，它不是"搬进某间办公室等着招租"，而是"接了一路广播线，然后楼里所有装了喇叭的人都过来听一下，觉得内容跟自己有关的就把线接过去"。喇叭不是租客，接线不是签租约——没有"绑定"这回事，一路广播线可以同时接到好几个喇叭上。

### 回到代码

`input_dev` 内嵌了一个 `struct device`（`include/linux/input.h:198`），所以它确实是驱动模型里的一个设备节点。但注册的时候，只填了 `type` 和 `class`，**`bus` 那一格空着**：

```c
/* drivers/input/input.c:2017-2018，在 input_allocate_device() 里 */
		dev->dev.type = &input_dev_type;
		dev->dev.class = &input_class;
```

`input_dev_type` 只装了 sysfs 属性组、release 和 uevent（`drivers/input/input.c:1981-1988`），`input_class` 只装了名字和 devnode 回调（`drivers/input/input.c:1995-1999`）。**全树没有 `struct bus_type input_bus_type`** —— 这句是否定性断言，验证命令：

```bash
grep -rn 'struct bus_type' drivers/input/ include/linux/input.h include/linux/input/
```

输出是 5 行，**没有一行叫 `input_bus_type`**——三个子框架各有自己的走廊，`input_dev` 一条都不在上面：

| 行 | 符号 | 是什么 |
| --- | --- | --- |
| `drivers/input/serio/serio.c:1010` | `serio_bus` | serio（PS/2）总线的定义，见 `04-extensions.md` §2 |
| `drivers/input/gameport/gameport.c:42` | `gameport_bus` | 前向声明 |
| `drivers/input/gameport/gameport.c:786` | `gameport_bus` | 定义，见 `04-extensions.md` §12 |
| `drivers/input/rmi4/rmi_bus.h:188` | `rmi_bus_type` | `extern` 声明 |
| `drivers/input/rmi4/rmi_bus.c:347` | `rmi_bus_type` | 定义，见 `04-extensions.md` §5 |

其中 rmi4 那个长这样：

```c
struct bus_type rmi_bus_type = {
	.match		= rmi_bus_match,
	.name		= "rmi4",
};
```

—— 那是 rmi4 自己的走廊，跟 `input_dev` 无关；serio / gameport 两条走廊上跑的是**端口**，也不是 `input_dev`。所以 `/sys/class/input/` 有内容，`/sys/bus/input/` **在任何配置下都不存在**；而 `/sys/bus/serio/`、`/sys/bus/gameport/`、`/sys/bus/rmi4/` 三条只要对应模块加载了就会出现（各自的 `bus_register()` 在 `drivers/input/serio/serio.c:1028`、`drivers/input/gameport/gameport.c:833`、`drivers/input/rmi4/rmi_bus.c:436`）。

★ 想只验证「没有 input 自己的总线」这一句，用 `02-datastructures.md` §1.0 那条更窄的命令（只扫 `drivers/input/*.c` 和 `include/linux/input*.h`，输出为空）；本篇这条命令故意扫了子目录，就是为了把三个子框架的走廊一起摆出来对照。

匹配这件事由核心自己做，不走 `bus_type.match`：设备注册的最后一步是遍历全局 handler 链表，逐个试（`drivers/input/input.c:2469-2470`）：

```c
	list_for_each_entry(handler, &input_handler_list, node)
		input_attach_handler(dev, handler);
```

注意这里**没有 `break`** —— 匹配上一个不会停下来，这正是 §3 要讲的事。

### 这个类比什么时候会误导你？

"广播 + 喇叭"会让你以为**设备侧完全被动、只管往外喊**。实际上 handler 可以往回捅两下：

- `input_handler.filter` 回调（`include/linux/input.h:317`）返回 true 就能**吃掉**一个事件，后面的 handler 收不到。这不是"听广播"，这是站在线路上剪线。真正的分发循环在 `input_pass_values()`（`drivers/input/input.c:140-178`），返回值 `count` 被逐个 handler 改小，`if (!count) break;`（`:161`）。
- handler 可以**独占**整条广播线：`dev->grab`（`include/linux/input.h:190`）一旦被设上，`input_pass_values()` 只走抢占者那一条（`drivers/input/input.c:153-155`）。详见 §8。

还有一点：广播类比暗示"设备起来了就有广播"。不对——`input_event()` 在 `input_register_device()` 之前调用是**合法但静音**的，`dev->vals` 还是 NULL，事件在 `input_event_dispose()` 的 `if (!dev->vals) return;`（`drivers/input/input.c:353-354`）处被丢掉。这是官方注释里明确写着的用法（`drivers/input/input.c:416-421`），用来"播种"开关的初始状态。

> 相关：`02-datastructures.md` §1.0.1 有一张"是不是 struct device / 注册到哪条总线 / 会不会绑 driver"的逐行对比表；父总线关系图在 `02-datastructures.md` §1.1.1。

---

## §2 `input_dev` / `input_handler` / `input_handle` 这个三角在描述什么？

### 先举个例子

想象一个会议的**同声传译**场面。

- **`input_dev`** 是台上的讲者。他只管说，不管谁在听。
- **`input_handler`** 是一位译员——比如"英语译员"、"日语译员"。译员是一种**能力**，不是一次工作：一位英语译员这一整天可能给三场会都做了翻译。
- **`input_handle`** 是"这位译员正在给这场会翻译"这件**具体的连线**。它两头各连一个：一头讲者，一头译员。同一位译员在三场会上有三条连线，同一场会上如果有英语和日语两位译员，那这位讲者身上也挂着两条连线。

连线上还有一个开关：译员可能挂着线但耳机没插（`open == 0`），这时候讲者说什么都不会传过去。

### 回到代码

三个结构体，字段一一对上：

```c
/* include/linux/input.h:345-357 */
struct input_handle {

	void *private;

	int open;
	const char *name;

	struct input_dev *dev;
	struct input_handler *handler;

	struct list_head	d_node;
	struct list_head	h_node;
};
```

`dev` 和 `handler` 是两头（`:352-353`），`d_node` 挂到设备的链表上、`h_node` 挂到 handler 的链表上（`:355-356`）。挂的动作在 `input_register_handle()`（`drivers/input/input.c:2619-2656`）。

设备侧和 handler 侧各有一个 `h_list`，名字一样但含义不同：

| 字段 | 位置 | 装的是 |
|------|------|--------|
| `input_dev.h_list` | `include/linux/input.h:200` | 这台设备上所有连线（遍历得到"谁在听我"） |
| `input_dev.node` | `include/linux/input.h:201` | 把设备本身挂到 `input_dev_list` 上 |
| `input_handler.h_list` | `include/linux/input.h:329` | 这个 handler 的所有连线（遍历得到"我在听谁"） |
| `input_handler.node` | `include/linux/input.h:330` | 把 handler 本身挂到 `input_handler_list` 上 |

"耳机没插"就是 `handle->open`。分发时逐条检查（`drivers/input/input.c:157-162`）：

```c
		list_for_each_entry_rcu(handle, &dev->h_list, d_node)
			if (handle->open) {
				count = input_to_handler(handle, vals, count);
				if (!count)
					break;
			}
```

`open` 由 `input_open_device()` 加一（`drivers/input/input.c:639`），也就是用户态 `open("/dev/input/event0")` 那一刻才发生。**连线建好 ≠ 有事件流过**：一台注册好、被 evdev 接管、`/dev/input/event3` 已经存在的键盘，在没有任何进程打开它之前，`input_pass_values()` 里那个 `if (handle->open)` 全是假，事件走到这里就停了。

### 这个类比什么时候会误导你？

译员类比在**一个关键点上是反的**：现实里译员先被雇来、再分配到会场；代码里 `input_handle` 不是核心分配的，**是 handler 自己 `kzalloc` 出来再交给核心登记的**。核心从不拥有 handle 的内存。看 `struct evdev` 的定义：`struct input_handle handle;` 是一个**内嵌字段**（`drivers/input/evdev.c:30`），跟着 `struct evdev` 一起分配、一起释放。所以"译员离场"时，必须由 handler 自己在 `disconnect()` 里调 `input_unregister_handle()`（`drivers/input/input.c:2669-2683`）把连线摘掉——核心不会替你摘，而且它在拆设备时会 `WARN_ON(!list_empty(&dev->h_list))`（`drivers/input/input.c:2297`）来抓这种漏。

第二个失真点：译员是"一种语言一位"，暗示 handler 之间互斥。它们不互斥，而且**匹配表可以写成"匹配一切"**——`evbug` 的 `id_table` 只有 `{ .driver_info = 1 }`（`drivers/input/evbug.c:74-77`），`flags` 为 0 意味着一个匹配条件都不检查。`input_match_device()` 的循环条件是 `id->flags || id->driver_info`（`drivers/input/input.c:1041`），`driver_info = 1` 纯粹是为了让这一项不被当成表结尾的哨兵。

第三个：`open` 是**计数**不是布尔（`include/linux/input.h:349` 是 `int`）。同一个 `/dev/input/event0` 被十个进程打开，`handle->open` 是 1 还是 10？是 1 —— evdev 自己维护 client 链表，handle 只在 evdev 的第一个 client 到来时开一次。真正被多次加减的是 `dev->users`（`include/linux/input.h:195`），它在 `input_open_device()` 里是 `dev->users++`（`drivers/input/input.c:641`）。两个计数，两个层次，别混。

> 相关：三个结构体逐字段拆解在 `02-datastructures.md` §2.1 / §2.2 / §2.3；连线的建立与拆除时序在 `03-internals.md` §A6 与 §A11。

---

## §3 为什么一个键盘会被三四个 handler 同时接管，而不是像 SPI 那样只绑一个驱动？

### 先举个例子

一场足球赛，场上只有一场比赛，但同时有好几路人在"消费"它：电视台在直播、电台在解说、场馆大屏在放比分、还有一个记分员在本子上记。他们互相不认识，谁也不需要征得谁的同意，加一路少一路都不影响比赛本身。

驱动模型的"绑定"是另一回事：一间办公室只能租给一个租客，租出去了别人就进不来。**input 走的是前者**。

### 回到代码

设备注册的最后一步，遍历全局 handler 链表，**没有 break**：

```c
/* drivers/input/input.c:2469-2470 */
	list_for_each_entry(handler, &input_handler_list, node)
		input_attach_handler(dev, handler);
```

`input_attach_handler()` 的返回值**两个调用点都不检查**（`drivers/input/input.c:2470` 和 `:2542`）。函数自己内部把"不匹配"和"真出错"区分开（`drivers/input/input.c:1051-1066`）：

```c
	id = input_match_device(handler, dev);
	if (!id)
		return -ENODEV;

	error = handler->connect(handler, dev, id);
	if (error && error != -ENODEV)
		pr_err("failed to attach handler %s to device %s, error: %d\n",
		       handler->name, kobject_name(&dev->dev.kobj), error);
```

`-ENODEV` 的含义是"这台设备不是我的"，所以**不打日志**（`:1061` 那个 `error != -ENODEV`）。这也解释了 §2 里说的：一台键盘上通常同时挂着 evdev 的 handle、`input-leds` 的 handle（因为它有 `EV_LED`），如果编了 `CONFIG_INPUT_EVBUG` 还有 evbug 的 handle，如果它上报 `REL_*` 还会被 mousedev 认领。

反过来，handler 注册时也要遍历所有已存在的设备（`drivers/input/input.c:2541-2542`）。次序是**注册次序**——两个链表都用 `list_add_tail`（`drivers/input/input.c:2467` 设备侧、`:2539` handler 侧），先注册的先分发。

### 这个类比什么时候会误导你？

"多路转播"暗示这些消费者都在**输出**同一份内容。三个偏差：

1. **不是每个 handler 都造设备节点。** `input-leds`、`apm-power`、`evbug` 一个字符设备都不建——它们是纯消费者。所以"这台设备被 4 个 handler 接管"和"`/dev/input/` 下多了 4 个节点"完全不是一件事。全树 12 个 handler 注册点的逐个交代在 `02-datastructures.md` §3。

2. **转播者能改动比赛。** `filter` 回调（`include/linux/input.h:317`）返回 true，后面的 handler 就收不到这个事件——这是有序的、有副作用的链，不是并行的旁路。而且既然次序是注册次序，一个装 `filter` 的 handler 能不能拦住某个特定 handler，取决于**谁先 `input_register_handler()`**，这在 modprobe 次序里就定了。

3. **evbug 不只是"多一路观众"。** 它的 `event` 回调直接 `printk`（`drivers/input/evbug.c:24`），而这一整段是在 `dev->event_lock` 这把 spinlock 里跑的（`drivers/input/input.c:146` 的 `lockdep_assert_held`）。加了它，输入路径的延迟会变；它是观察者，同时也是扰动源。详见 `07-debug.md` §1.2。

> 相关：匹配规则的三件反直觉之处在 `05-usage.md` §6.3；`-ENODEV` 在错误码分诊里的位置见 `07-debug.md` §8.4。

---

## §4 那个 `SYN_REPORT` 到底是什么？

### 先举个例子

网购下单，你把三件东西加进购物车：一件衬衫、一条裤子、一双袜子。仓库不会分三次发货，它等你**点"结算"**，然后一起打包成一个箱子发出来。

`SYN_REPORT` 就是"结算"这个动作。鼠标斜着动一下，驱动上报的是 `REL_X`、`REL_Y` 两个事件——它们必须一起到达，否则上层会先看到"只往右动了"，再看到"只往下动了"，画出一条锯齿。所以驱动报完这一次采样的所有轴，最后调一次 `input_sync()`，核心才把整箱交给 handler。

### 回到代码

`input_sync()` 不是什么特殊 API，就是发一个事件：

```c
/* include/linux/input.h:448-451 */
static inline void input_sync(struct input_dev *dev)
{
	input_event(dev, EV_SYN, SYN_REPORT, 0);
}
```

核心在 `input_get_disposition()` 里给它挂上 `INPUT_FLUSH` 标志（`drivers/input/input.c:255-257`）：

```c
		case SYN_REPORT:
			disposition = INPUT_PASS_TO_HANDLERS | INPUT_FLUSH;
			break;
```

"箱子"是 `dev->vals` 这个数组（`include/linux/input.h:205`），`dev->num_vals` 是箱子里已经放了几件（`:203`）。事件一件件放进去（`drivers/input/input.c:366-369`），看到 `INPUT_FLUSH` 就整箱发出（`:372-375`）：

```c
	if (disposition & INPUT_FLUSH) {
		if (dev->num_vals >= 2)
			input_pass_values(dev, dev->vals, dev->num_vals);
		dev->num_vals = 0;
```

### 这个类比什么时候会误导你？

★★ **"点结算总会发出一个箱子"是错的。** 看 `:373` 那个 `if (dev->num_vals >= 2)`：`SYN_REPORT` 自己也占一格，所以 `num_vals == 1` 意味着"箱子里只有那张结算单，没有货"。**空帧被直接丢掉，一个字节都不会到用户态。** 连着调两次 `input_sync()`，第二次是无声的。

而且这道门有**两层**，第二层在 evdev 里（`drivers/input/evdev.c:267-270`）：

```c
		if (v->type == EV_SYN && v->code == SYN_REPORT) {
			/* drop empty SYN_REPORT */
			if (client->packet_head == client->head)
				continue;
```

—— 核心那层看的是"这一帧有没有货"，evdev 这层看的是"**这个 client** 有没有收到货"。区别在于 evdev 支持按类型过滤（`__evdev_is_filtered()`，`:264`），一帧货可能对 A client 全被滤掉、对 B client 全都留下；这时核心那层放行了，evdev 这层还要再判一次。

第二个失真点：结算类比暗示"没结算就一直攒着"。不是——攒到快满的时候核心会自己劈开，见 §5。

第三个：`input_mt_sync()`（`include/linux/input.h:453-456`）发的是 `SYN_MT_REPORT`，它**不带** `INPUT_FLUSH`（`drivers/input/input.c:258-260`），所以它不是"结算"，只是"这件货说完了，下一件"。名字像，作用不一样，见 `05-usage.md` §11.4。

---

## §5 核心什么时候会替你劈开一帧？

### 先举个例子

接着 §4 的箱子。仓库的箱子有固定尺寸。你往购物车里加了 60 件东西还没点结算，仓库不能无限等——箱子塞满了它就**自己封一箱先发走**，剩下的放到下一箱。

于是你在收货端看到两个箱子，而你只下了一单。

### 回到代码

箱子容量在注册时定死（`drivers/input/input.c:2426-2430`）：

```c
	packet_size = input_estimate_events_per_packet(dev);
	if (dev->hint_events_per_packet < packet_size)
		dev->hint_events_per_packet = packet_size;

	dev->max_vals = dev->hint_events_per_packet + 2;
```

塞满的判定和自动封箱在 `input_event_dispose()` 的 else 分支（`drivers/input/input.c:383-386`）：

```c
	} else if (dev->num_vals >= dev->max_vals - 2) {
		dev->vals[dev->num_vals++] = input_value_sync;
		input_pass_values(dev, dev->vals, dev->num_vals);
		dev->num_vals = 0;
	}
```

`input_value_sync` 是一个文件级常量（`drivers/input/input.c:49`）：

```c
static const struct input_value input_value_sync = { EV_SYN, SYN_REPORT, 1 };
```

★★ **注意第三个字段是 `1`，不是 `0`。** 驱动自己调 `input_sync()` 发的是 `value = 0`（`include/linux/input.h:450`）；核心劈帧时补的这个 `SYN_REPORT` 带 `value = 1`。而 evdev 是逐字段原样拷贝的（`drivers/input/evdev.c:275-277`）：

```c
		event.type = v->type;
		event.code = v->code;
		event.value = v->value;
```

所以**用户态能分辨这一帧是驱动结的还是核心劈的**——`SYN_REPORT` 的 `value` 是 1 就是后者。

### 这个类比什么时候会误导你？

1. **"箱子塞满才劈"里的"满"不是 `max_vals`，是 `max_vals - 2`。** 留出的 2 格一格给马上要写的这个事件、一格给补进去的 `SYN_REPORT`。所以 `max_vals` 加的那个 `+ 2`（`:2430`）和这里减的 `- 2`（`:383`）是同一件事的两头。

2. **箱子尺寸不是驱动想设多大就多大。** `input_estimate_events_per_packet()`（`drivers/input/input.c:2235`）会按设备声明的能力算一个下限，驱动用 `input_set_events_per_packet()`（`include/linux/input.h:470-473`）给的值只有**更大**才生效（`:2427-2428` 那个 `if (dev->hint_events_per_packet < packet_size)`）。给小了会被静默抬上去。

3. ★ **劈帧和"丢事件"是两件不同的事，症状却像。** 劈帧不丢任何事件，只是把一帧变成两帧；真丢事件是 evdev 环形缓冲满了，那时它会插一个 `SYN_DROPPED`（`drivers/input/evdev.c:152`、`:231`）。看到帧变多先查 `value == 1`，看到 `SYN_DROPPED` 才是真丢。uinput 那一侧更要小心：它的 16 格环满了**不发** `SYN_DROPPED`，见 `04-extensions.md` §6.1 和 `07-debug.md` §0.1。

> 相关：攒帧与劈帧的完整逐行剖析在 `03-internals.md` §A8.4。

---

## §6 多点触控的 slot 是什么？

### 先举个例子

想象一个有 10 个储物柜的更衣室，柜子编号 0 到 9。你进来，管理员给你 3 号柜，往里放你的东西；你走了，柜子腾空，下一个人可能还是拿到 3 号柜。

**slot 就是柜子，不是人。** 柜子的编号是复用的，"3 号柜"这个说法不指任何一个具体的人。要指人，得看柜门上贴的那张便签——上面写着一个流水号，每来一个新客人就换一张新便签。

### 回到代码

柜子是一个定长数组（`include/linux/input/mt.h:43-51`）：

```c
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

每个柜子里放的就是那一路接触点当前的各个 `ABS_MT_*` 轴值（`include/linux/input/mt.h:27-31`）：

```c
struct input_mt_slot {
	int abs[ABS_MT_LAST - ABS_MT_FIRST + 1];
	unsigned int frame;
	unsigned int key;
};
```

"柜门上的便签"是 `ABS_MT_TRACKING_ID`。`input_mt_report_slot_state()`（`drivers/input/input-mt.c:138-164`）负责发它：

```c
	if (!active) {
		input_event(dev, EV_ABS, ABS_MT_TRACKING_ID, -1);
		return false;
	}

	id = input_mt_get_value(slot, ABS_MT_TRACKING_ID);
	if (id < 0)
		id = input_mt_new_trkid(mt);
```

手指抬起 → 发 `-1`（`:152`）；柜子原来是空的（`id < 0`）→ 分配一个新流水号（`:156-158`）。**同一个 slot 在两次触摸之间，tracking id 一定不同**；上层就是靠这个判断"还是那根手指"还是"换了一根"。

驱动侧选柜子用 `input_mt_slot()`（`include/linux/input/mt.h:85-88`），它发的是 `ABS_MT_SLOT`。这个事件被核心**暂存而不下发**（`drivers/input/input.c:194-203`）：

```c
	if (code == ABS_MT_SLOT) {
		/*
		 * "Stage" the event; we'll flush it later, when we
		 * get actual touch data.
		 */
		if (mt && *pval >= 0 && *pval < mt->num_slots)
			mt->slot = *pval;

		return INPUT_IGNORE_EVENT;
	}
```

真正把 `ABS_MT_SLOT` 补进流里，是等到有一个真的 MT 轴值要发、而且当前 slot 和上次发出去的不一样时（`drivers/input/input.c:228-232`）。所以"切柜子"这个动作**只在有货要放时才对上层可见**——空切一圈不产生任何事件。

### 这个类比什么时候会误导你？

★★ **这是本子系统最容易类比错的一组关系**，三个偏差都在同一处：

1. **柜号 ≠ 第几根手指。** 一只手三根手指按下，可能拿到 slot 0、1、2，也可能是 0、2、5（中间那两个柜子上一帧还被别人占着，`input_mt_drop_unused()` 还没腾）。**不能拿 slot 号当手指序号**，也不能假设活跃的 slot 是从 0 开始连续的。要数手指得数活跃 slot：`input_mt_is_active()`（`include/linux/input/mt.h:65`）。

2. **柜子数是编译期定的，不是有几根手指分配几个。** `input_mt_init_slots()`（`drivers/input/input-mt.c:39-104`）一次把 `num_slots` 个柜子全 `kzalloc` 出来，之后不增不减。所以"10 个 slot"不代表"当前有 10 根手指"，只代表"这台设备最多认 10 根"。

3. ★ **不用 slot 也能报多指。** 老的 protocol A 设备一根手指一组轴值、每组后面跟一个 `SYN_MT_REPORT`（`input_mt_sync()`，`include/linux/input.h:453-456`），根本没有 slot 概念。这时 `dev->mt` 是 NULL，`input_handle_abs_event()` 走的是 `pold = NULL` 那一支（`drivers/input/input.c:211-217`），注释写得很直白："Bypass filtering for multi-touch events when not employing slots"——**去重和去抖都被跳过**。所以 protocol A 的设备不但没有 slot，还少了核心的一层过滤。

第四个偏差不在柜子上，在便签上：`trkid` 会回绕（`TRKID_MAX` 是 `0xffff`，`include/linux/input/mt.h:13`）。类比里"每人一张新便签"暗示号码永不重复，实际上它是模 65536 的。

> 相关：slot 的两个正交布尔状态（活跃 / 本帧见过）在 `02-datastructures.md` §1.0.4 与 `04-extensions.md` §7.1；驱动侧每帧该调哪三个函数在 `05-usage.md` §3.4。

---

## §7 为什么 `capabilities/abs` 是一串十六进制，坐标范围却只能用 ioctl 拿？

### 先举个例子

一家餐馆门口贴两样东西：

- 一张**菜单目录**，只写"我们有汤、有主菜、有甜点"——是个勾选清单，你一眼能看出"有没有"。
- 一份**每道菜的详细规格**（分量多少克、多辣、多少钱）——这个不贴在门口，你得进去问服务员。

sysfs 贴的是前者，ioctl 问的是后者。`capabilities/abs` 告诉你"这台设备有 X 轴、有 Y 轴、有压力轴"；它**不告诉你 X 轴的范围是 0–1079 还是 0–4095**。

### 回到代码

sysfs 侧的九个 `capabilities/*` 文件是同一个宏批量生成的（`drivers/input/input.c:1610-1621`）：

```c
#define INPUT_DEV_CAP_ATTR(ev, bm)					\
static ssize_t input_dev_show_cap_##bm(struct device *dev,		\
				       struct device_attribute *attr,	\
				       char *buf)			\
{									\
	struct input_dev *input_dev = to_input_dev(dev);		\
	int len = input_print_bitmap(buf, PAGE_SIZE,			\
				     input_dev->bm##bit, ev##_MAX,	\
				     true);				\
	return min_t(int, len, PAGE_SIZE);				\
}									\
static DEVICE_ATTR(bm, S_IRUGO, input_dev_show_cap_##bm, NULL)
```

—— 九次展开（`:1623-1631`），挂进 `.name = "capabilities"` 的属性组（`:1646-1649`）。九个文件全部只是**位图**，`input_print_bitmap()`（`drivers/input/input.c:1581-1608`）负责格式：从**高位** `unsigned long` 往低位打（`:1588` 那个倒着走的 for），组间空格（`:1594`），前导全零组跳过（`skip_empty`，`:1586`、`:1592`），全空就打一个 `0`（`:1601-1602`）。

轴的具体参数在另一个结构里，而且它只有 uapi 一条出口（`include/uapi/linux/input.h:93-100`）：

```c
struct input_absinfo {
	__s32 value;
	__s32 minimum;
	__s32 maximum;
	__s32 fuzz;
	__s32 flat;
	__s32 resolution;
};
```

拿它的唯一办法是 `EVIOCGABS(axis)`（定义在 `include/uapi/linux/input.h:179`），实现在 `drivers/input/evdev.c:1204-1217`：

```c
		if ((_IOC_NR(cmd) & ~ABS_MAX) == _IOC_NR(EVIOCGABS(0))) {

			if (!dev->absinfo)
				return -EINVAL;

			t = _IOC_NR(cmd) & ABS_MAX;
			abs = dev->absinfo[t];
```

**sysfs 和 procfs 里都没有 min/max/fuzz/flat/resolution。** 这是否定性断言，重跑命令：

```bash
grep -rn 'minimum\|maximum\|resolution' drivers/input/input.c
```

（`drivers/input/input.c` → 4 处命中：`:507-508` 在 `input_set_abs_params()` 里写值，`:2244-2245` 在 `input_estimate_events_per_packet()` 里读 `ABS_MT_TRACKING_ID` 的范围来估算帧长。没有任何 `DEVICE_ATTR` 或 `seq_printf` 碰这三个字段。）

### 这个类比什么时候会误导你？

1. **"目录"暗示位图是只读的清单，但 ioctl 那一侧是可写的。** `EVIOCSABS(axis)`（`include/uapi/linux/input.h:180`，实现 `drivers/input/evdev.c:1222` 起）能在运行时**改写**一个轴的 min/max。这是验证"DTS 里 `touchscreen-size-x` 写错了"最快的办法——不用改 DTS、不用重启。见 `06-devicetree.md` §3.3。

2. ★ **位图不是"能不能用"，是"声明了没有"。** 一台触摸屏可以 `__set_bit(ABS_X, dev->absbit)` 却不给 `absinfo` 填范围——那样 `input_register_device()` 会直接拒绝注册并打一条日志（`drivers/input/input.c:2402-2405`，`Absolute device without dev->absinfo, refusing to register`）。反过来，`EV_KEY` 位图里勾了 200 个键码，不代表这 200 个键真的物理存在，只代表驱动声明了它们。

3. **同一份位图有两种排版，别拿去互相 diff。** `/proc/bus/input/devices` 的 `B: ABS=...` 走的是另一个函数 `input_seq_print_bitmap()`（`drivers/input/input.c:1166`），而且**只在对应的 `EV_*` 位置位时才打印那一行**（`:1219` 那一行外面套着条件）。sysfs 的 `capabilities/abs` 无条件存在，内容可能是单个 `0`。

> 相关：九个属性的逐个说明和这条"位图 vs 数值"的分界在 `07-debug.md` §3.1；`EVIOC*` 全表在 `07-debug.md` §9.2。

---

## §8 `EVIOCGRAB` 之后为什么别人什么都收不到？

### 先举个例子

一间会议室装了一路广播。平时谁都能接线来听。现在来了一个人，他掏出钥匙把广播总线切到"专线模式"——从这一刻起，这条线只通到他一个人的耳机，其他所有已经接好的线**物理上断开**。等他把钥匙拔了，所有人的线自动接回来，而且每个人都会收到一句"当前状态是这样"的补播，免得他们停在旧状态上。

`EVIOCGRAB` 就是这把钥匙。

### 回到代码

用户态那一侧就一行分支（`drivers/input/evdev.c:1086-1090`）：

```c
	case EVIOCGRAB:
		if (p)
			return evdev_grab(evdev, client);
		else
			return evdev_ungrab(evdev, client);
```

evdev 侧再往下调核心（`drivers/input/evdev.c:341-355`），核心侧只是设一个指针（`drivers/input/input.c:559-578`）：

```c
	if (dev->grab) {
		retval = -EBUSY;
		goto out;
	}

	rcu_assign_pointer(dev->grab, handle);
```

"物理断开"发生在分发路径上（`drivers/input/input.c:153-163`）：`dev->grab` 非空就只走那一条 handle，`else` 里遍历 `h_list` 的整段被跳过。

"拔钥匙后的补播"在 `__input_release_device()`（`drivers/input/input.c:581-597`）：

```c
	if (grabber == handle) {
		rcu_assign_pointer(dev->grab, NULL);
		/* Make sure input_pass_values() notices that grab is gone */
		synchronize_rcu();

		list_for_each_entry(handle, &dev->h_list, d_node)
			if (handle->open && handle->handler->start)
				handle->handler->start(handle);
	}
```

—— 逐个调 `handler->start`（`:594-595`），让每个 handler 有机会重新对齐设备状态。

### 这个类比什么时候会误导你？

1. ★★ **"别人收不到"里的"别人"包括你自己进程里的另一个 fd，但不包括同一个 evdev 的其它 client 吗？** 包括。抢占有**两层**：核心层 `dev->grab` 挡住其它 handler（mousedev、evbug、input-leds），evdev 层 `evdev->grab` 挡住同一个 `/dev/input/eventN` 上的其它 client。两层各有各的 `-EBUSY`：核心的在 `drivers/input/input.c:568-571`，evdev 的在 `drivers/input/evdev.c:345-346`（`if (evdev->grab) return -EBUSY;`）。

2. **"钥匙"暗示 grab 是排他且必须显式归还的，但它也可能被隐式归还。** 进程死掉时 `release` 路径会走到 `__input_release_device()`（`drivers/input/input.c:702` 那个调用点在 `input_close_device()` 里），所以 grab 不会永久泄漏。这也是"evtest 被 Ctrl-C 掉之后桌面又能收到键盘"的原因。

3. ★ **grab ≠ revoke。** `EVIOCREVOKE`（`drivers/input/evdev.c:1091-1095`）看着像"反向的 grab"，其实是**让某个 fd 自我作废**：它只接受 `p == 0`，传非零直接 `-EINVAL`。作废后那个 fd 上再读只会拿到错误，而设备本身对别人照旧可用。见 `05-usage.md` §9.6。

4. **补播不是重发事件，是调回调。** `handler->start` 是 handler 自己实现的（evdev 的实现是把当前按键 / 开关 / LED 状态作为事件塞进每个 client 的缓冲）。如果一个 handler 没有 `start`（`:594` 那个 `handle->handler->start` 判空），它就没有补播——**放开 grab 后它会停在旧状态上**，这是写 handler 时最容易漏的一件事。

---

## §9 `inhibited` 是"静音"，不是"关机"

### 先举个例子

开会时把手机按了静音。手机没关机——它还在收信号、还在耗电、来电还在"发生"，只是不响。把静音关掉，它立刻恢复；但**静音期间的那几个来电不会补响给你**。

`/sys/class/input/inputN/inhibited` 写 1，就是给这台设备按静音。

### 回到代码

写入口很短（`drivers/input/input.c:1515-1535`）：

```c
	if (strtobool(buf, &inhibited))
		return -EINVAL;

	if (inhibited)
		rv = input_inhibit_device(input_dev);
	else
		rv = input_uninhibit_device(input_dev);
```

"不响"落在**分发决策的最前面**（`drivers/input/input.c:243-245`）：

```c
	/* filter-out events from inhibited devices */
	if (dev->inhibited)
		return INPUT_IGNORE_EVENT;
```

这一句在 `input_get_disposition()` 的开头，**早于所有 `case`**。所以：evtest 静默、evbug 静默、`/dev/input/mouseN` 静默、`input-leds` 也收不到 `EV_LED`——一个都不漏。

"还在耗电"这一点也对得上：`input_inhibit_device()`（`drivers/input/input.c:1847-1873`）做的是"松开所有按住的键、释放所有 MT slot、补一个 `SYN_REPORT`，然后 `input_dev_toggle(dev, false)`"。设备的 `close()` 会被调，但 `open` 计数不变——`input_open_device()` 里那句 `if (dev->users++ || dev->inhibited)`（`drivers/input/input.c:641`）说明**被抑制期间新来的 open 直接成功返回**，不会去碰硬件。

### 这个类比什么时候会误导你？

1. ★★ **按静音的那一刻会发出声音。** `input_inhibit_device()` 在切断之前主动"松键"，也就是往上层**注入**了一批 `EV_KEY value=0` 和 `ABS_MT_TRACKING_ID = -1`，最后一个 `SYN_REPORT`。上层看到的是"用户把手指和所有按键都放开了"。如果你在写 handler，别假设 inhibit 是无声的。

2. ★★ **静音是全局的，不是"对某个监听者静音"。** 手机静音只影响你；`inhibited` 影响所有 handler。所以在共享设备上用它排查问题，会顺带把桌面环境也弄哑。

3. **"关静音必然成功"是错的。** `input_inhibit_device()` 返回 `int` 但**永远返回 0**；反方向 `input_uninhibit_device()`（`drivers/input/input.c:1875-1902`）会把 `dev->open()` 的失败原样传出来。也就是说**写 0 可能失败**（`echo` 报错），写 1 不会。这条不对称已登记在设计债里，见 `03-internals.md` §C1。

4. **它不是电源管理。** 真正的 suspend/resume 路径是另一套（`03-internals.md` §B6），两者都会调 `input_dev_toggle()`，但触发源和 `dev->inhibited` 这个标志各自独立。

> 相关：setter 的三个陷阱在 `07-debug.md` §3.4；完整时序在 `03-internals.md` §B5。

---

## §10 为什么 input 核心一个 tracepoint 都没有？

### 先举个例子

想象一条流水线，末端已经装了一台**带时间戳的摄像机**，而且录下来的带子对所有人开放。这时你还要不要在流水线中段再装十个记录点？

装了当然能看到更多细节，但你得为此在流水线上腾出位置、每个记录点都要通电、而且流水线本身跑得极快、位置极窄——中段那一段是不允许停下来的。

input 的情况就是这个：`/dev/input/eventN` 本身就是那台摄像机。

### 回到代码

先把结论钉死。全 `drivers/input/` 只有一个 `TRACE_EVENT` 文件：

```bash
grep -rln 'TRACE_EVENT\|DECLARE_EVENT_CLASS' drivers/input/
```

→ 只有 `drivers/input/keyboard/applespi_trace.h`。而 `CONFIG_KEYBOARD_APPLESPI` 有 `depends on X86 || COMPILE_TEST`（`drivers/input/keyboard/Kconfig:77`），所以 **arm64 上 input 子系统的 tracepoint 数量是 0**。

"位置极窄"是真的。整条事件路径跑在 `dev->event_lock` 这把 spinlock 里，而且中断是关掉的（`drivers/input/input.c:430-432`）：

```c
		spin_lock_irqsave(&dev->event_lock, flags);
		input_handle_event(dev, type, code, value);
		spin_unlock_irqrestore(&dev->event_lock, flags);
```

`input_pass_values()` 开头那句 `lockdep_assert_held(&dev->event_lock)`（`drivers/input/input.c:146`）把这个约束写成了断言。evdev 的分发函数里那句注释更直白（`drivers/input/evdev.c:260`）：`/* Interrupts are disabled, just acquire the lock. */`

"末端已经有摄像机"也是真的，而且带时间戳。核心在事件进入时就打一次时间戳（`drivers/input/input.c:2157-2165`）：

```c
ktime_t *input_get_timestamp(struct input_dev *dev)
{
	const ktime_t invalid_timestamp = ktime_set(0, 0);

	if (!ktime_compare(dev->timestamp[INPUT_CLK_MONO], invalid_timestamp))
		input_set_timestamp(dev, ktime_get());

	return dev->timestamp;
}
```

evdev 把它填进每个 `struct input_event`（`drivers/input/evdev.c:256-258`），而且用户态可以用 `EVIOCSCLOCKID` 选时钟源。**一个 `evtest` 的输出，就是这条流水线的完整 trace**——带时间、带全部字段、无需重新编译内核。

### 这个类比什么时候会误导你？

1. ★★ **"末端摄像机"看不到被丢掉的事件。** 而 input 路径上丢事件的地方不止一处：`is_event_supported()` 挡掉未声明的类型/码（`drivers/input/input.c:62-66`）、状态去重挡掉"值没变"的重复（`:273-277` 那个 `!!test_bit(code, dev->key) != !!value`）、`input_defuzz_abs_event()` 挡掉抖动（`:68-82`）、`dev->inhibited` 挡掉全部（`:243-245`）。**这些事件从未到达 evdev，所以 evtest 里根本不会出现。** 一个真正的核心 tracepoint 本来能看到它们；现在看不到。这就是这个设计取舍的成本，而且它恰好落在"驱动写错了能力声明"这个最常见的 bug 上。

2. **"再装记录点"并非无法做到，只是要用别的工具。** 没有 tracepoint 不等于没有观察手段：`function_graph` 可以跟 `input_event` 的调用图，kprobe 可以取参数。arm64 上第二个参数是 `%x1`，例如
   ```
   p:ie input_event type=%x1:u32 code=%x2:u32 value=%x3:s32
   ```
   具体用法在 `07-debug.md` §5.3。

3. ★ **"核心没有 tracepoint"不等于"这条路径上一个 tracepoint 都碰不到"。** `input_handle_event()` 里会调 `add_input_randomness()`（`drivers/input/input.c:400`），那是随机数子系统的入口，它自己带 trace 点。所以你能间接确认"有事件进来了"，只是拿不到 type/code/value 的语义。

4. **摄像机是每个 client 一台，不是全局一台。** evdev 每个打开的 fd 有独立的环形缓冲（大小 `max(hint_events_per_packet * EVDEV_BUF_PACKETS, EVDEV_MIN_BUFFER_SIZE)`，`drivers/input/evdev.c:464-465`），还支持按类型过滤（`__evdev_is_filtered()`，`:264`）。所以"两个工具看到的事件流不一样"是正常的，不是矛盾——它们是两台机器，各自可能各自丢包（各自的 `SYN_DROPPED`）。

> 相关：tracepoint 普查的完整结论和 arm64 编不出来的证明在 `07-debug.md` §5.1 与 §5.2。

---

## 阅读下一篇

| 你现在想做的事 | 去哪一篇 |
|---------------|---------|
| 找某个文件、某个 API | `01-api.md` §0 与 §1 |
| 看结构体逐字段、看四组关系的对比表 | `02-datastructures.md` §1.0 与 §2 |
| 顺着时间线读注册 / 匹配 / 分发 / 拆除 | `03-internals.md` §0.2 起 |
| 查某个子框架（evdev、uinput、rmi4、力反馈……） | `04-extensions.md` §0 的分诊表 |
| 照骨架写一个驱动或 handler | `05-usage.md` §0.2 的角色清单 |
| 写 DTS、查某条属性谁消费 | `06-devicetree.md` §1.1 |
| 设备不出事件了，要定位 | `07-debug.md` §0 与 §8 |
| 想知道哪些结论没验证过、数字怎么重跑 | `99-evidence.md` |
