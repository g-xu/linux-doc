---
title: 费曼学习法
---

# drivers/base 的九个"为什么"

这一篇不讲怎么用，讲**为什么长这样**。每个主题四段：先摆出问题、用日常例子引入、回到源码、最后说清这个例子在什么地方会把你带偏。类比是脚手架，看完就该拆掉——所以第四段永远存在。

主题清单：

| # | 问题 | 关键词 |
|---|---|---|
| §1 | 为什么内核需要"驱动模型"这一层？ | 分层的理由 |
| §2 | `bus_type` 和 `class` 长得几乎一样，为什么是两回事？ | 最容易类比错的一组 |
| §3 | 为什么 `struct device` 是嵌进去的，不是指过去的？ | `container_of` / 引用计数 |
| §4 | probe 为什么会 defer？内核为什么不干脆排好顺序？ | 依赖与时序 |
| §5 | `devm_*` 凭什么可以"忘了释放"？ | devres |
| §6 | `fw_devlink` 凭一份设备树怎么"猜"出依赖？ | 静态推断 |
| §7 | `platform` 为什么被叫"假总线"，又为什么是最忙的一条？ | 抽象的复用 |
| §8 | 为什么 remove 比 probe 难写得多？ | 对称性幻觉 |
| §9 | 异步探测为什么能加速启动，代价是什么？ | 并发与顺序 |

---

## §1 为什么内核需要"驱动模型"这一层？

### 先举个例子

想象一个大型写字楼的物业。楼里有几千件设备：电梯、空调、门禁、消防栓。每件设备都有厂家、都有维修工。

最原始的做法是：每个维修工自己拿一个本子，记着"我负责三号楼 12 层的那两台空调"。这样能跑，但只要问一句稍微横向的问题就崩了：**"整栋楼现在有多少设备是通电的？断电检修前要通知哪些维修工？这台空调坏了会影响哪些别的设备？"** 没人能回答，因为信息全散在各人的本子里。

物业的解法是建一个统一台账：每件设备一张卡，写清它在哪、谁负责、现在什么状态、上游供电来自哪。**维修工还是各干各的，但台账是共用的。**

驱动模型就是这个台账。

### 回到代码

台账的"一张卡"是 `struct device`（`include/linux/device.h:557`）。它不描述任何具体硬件——里面没有寄存器地址、没有波特率、没有中断号的语义。它装的全是**横向问题的答案**：

```c
struct device {
	struct kobject kobj;              /* sysfs 里的那个目录 */
	struct device		*parent;      /* 挂在谁下面 */
	struct bus_type	*bus;             /* 属于哪条总线 */
	struct device_driver *driver;     /* 现在被谁驱动 */
	struct dev_links_info	links;    /* 上下游依赖 */
	struct dev_pm_info	power;        /* 电源状态 */
	...
};
```

`links`（`include/linux/device.h:577`，类型定义在 `:370-375`）就是"上游供电来自哪"，`power` 就是"现在通不通电"。有了这张卡，之前那三个问题各有一条实现：

- "有多少设备通电" → 遍历 `bus_type->p->klist_devices`
- "断电前通知谁" → `device_shutdown()`（`drivers/base/core.c:4808` 起）按注册的逆序走一遍
- "坏了影响谁" → 顺着 `links.consumers` 链表走

代价是每个驱动都必须交出一部分自主权：不能自己 `kmalloc` 一个设备就开始干活，得走 `device_add()`（`drivers/base/core.c:3578`）登记。

### 这个类比什么时候会误导你？

物业台账是**被动记录**——设备先存在，然后有人去登记。驱动模型不是：`device_add()` 会**主动触发**匹配和 probe（`drivers/base/core.c:3697` 调 `bus_probe_device()`）。所以在内核里"登记"这个动作本身有副作用，而且副作用可能很大——`device_add()` 返回之前，这个设备的驱动可能已经跑完 `probe()`、申请了 DMA 内存、注册了中断。

第二处误导更要紧：台账里一件设备只有一张卡，而内核里一个物理硬件常常对应**多张卡**。一颗 SPI NOR flash 至少有两个 `struct device`：一个是 SPI 总线上的 `spi_device`，一个是块层的 `mtd` 设备。它们不是父子关系那么简单，是同一块硅片在不同抽象层各留了一张卡。

---

## §2 `bus_type` 和 `class` 长得几乎一样，为什么是两回事？

这是 `drivers/base` 里**最容易类比错的一组关系**，所以放在前面。

### 先举个例子

学校里有两种把学生分组的方式。

一种是**按班级**：一个学生只能在一个班。班里有班主任、有课表、有教室。班级决定了"这个学生每天几点去哪、由谁管"。

另一种是**按社团**：合唱团、篮球队、机器人社。一个学生可以同时在三个社团里，社团有名册、有活动海报，但社团不决定这个学生的作息——社团**不管人**，只是把一批人按共同特征聚起来，方便外面找。

`bus_type` 是班级，`class` 是社团。

### 回到代码

把两个结构体摊开对比，差别一眼就出来了。`struct bus_type`（`include/linux/device/bus.h:84-118`）有这些回调：

```c
	int (*match)(struct device *dev, struct device_driver *drv);
	int (*probe)(struct device *dev);
	void (*remove)(struct device *dev);
	void (*shutdown)(struct device *dev);
	int (*dma_configure)(struct device *dev);
	const struct iommu_ops *iommu_ops;
```

`struct class`（`include/linux/device/class.h:54-78`）一个都没有：

```c
	int (*dev_uevent)(struct device *dev, struct kobj_uevent_env *env);
	char *(*devnode)(struct device *dev, umode_t *mode);
	void (*dev_release)(struct device *dev);
	int (*shutdown_pre)(struct device *dev);
```

关键差别就三条，而且全能在结构体定义里读出来：

| | `bus_type` | `class` |
|---|---|---|
| 有 `match()` 吗 | 有（`include/linux/device/bus.h:92`） | 没有 |
| 能触发 probe 吗 | 能（`.probe`，`:94`） | 不能 |
| 一个 device 能属于几个 | 一个（`dev->bus` 是单指针） | 一个（`dev->class` 也是单指针） |
| 管什么 | 谁来驱动这个设备 | 怎么把这个设备暴露给用户态 |

⚠️ 最后一行是这一节最值得记的事：**`class` 也是单指针**，社团那个类比在"一个学生能进多个社团"这一点上不成立。内核里一个 `struct device` 只能有一个 class。想同时出现在两个 class 里，就得再造一个 `struct device`——这正是为什么 `/sys/class/net/eth0` 和 `/sys/class/tty/ttyS0` 背后是两套完全不同的设备对象。

`class` 真正的用处是 `devnode()`（`include/linux/device/class.h:63`）：它决定 `/dev/` 下的名字和权限。也就是说 `class` 面向用户态，`bus_type` 面向驱动。同一个物理设备既在 `bus` 上又在 `class` 里是常态。

### 这个类比什么时候会误导你？

三处。

**第一处：`class` 有 `shutdown_pre()`（`include/linux/device/class.h:68`），社团不该有关机钩子。** `device_shutdown()` 在 `drivers/base/core.c:4850-4863` 里的顺序是：先试 `class->shutdown_pre`，再试 `bus->shutdown`，最后试 `driver->shutdown`。所以 class 在关机路径上确实有话语权，社团比喻在这里断掉。

**第二处：`class` 有 `dev_release()`，而 `bus_type` 没有。** 这是很反直觉的一处不对称——负责释放内存的居然是"不管事"的那一方。原因是 class 设备（`device_create()` 造出来的那些）往往没有自己的容器结构体，只有一个裸 `struct device`，得有人负责 `kfree`。

**第三处：现实里有些 `bus_type` 根本不做匹配。** 见 §7。

---

## §3 为什么 `struct device` 是嵌进去的，不是指过去的？

### 先举个例子

寄快递要填一张面单。面单上有寄件人、收件人、单号。

一种做法是：面单单独放一叠，上面写"对应第 47 号包裹"，包裹上贴个编号。快递员想知道 47 号包裹寄给谁，得去翻那叠面单。

另一种做法：**面单直接贴在包裹上**。看到面单就等于摸到包裹，撕下面单包裹就找不到了——两者共命运。

内核选了第二种。

### 回到代码

`struct device` 总是作为**成员**嵌在具体设备的结构体里：

```c
struct platform_device {
	const char	*name;
	int		id;
	...
	struct device	dev;      /* 面单贴在这里 */
	...
};
```

从面单找回包裹靠 `container_of`，通常包装成一个 `to_xxx()` 宏。`drivers/base/platform.c:1333-1334` 就是标准写法：

```c
	struct platform_device *pdev = to_platform_device(dev);
	struct platform_driver *pdrv = to_platform_driver(drv);
```

为什么非要"共命运"？因为**引用计数**。`struct device` 里那个 `kobject kobj`（`include/linux/device.h:558`）带引用计数，`put_device()` 只做一件事（`drivers/base/core.c:3801-3806`）：

```c
void put_device(struct device *dev)
{
	/* might_sleep(); */
	if (dev)
		kobject_put(&dev->kobj);
}
```

计数归零时 `kobject` 调 `release()`，而那个 `release()` 会 `container_of` 回去 `kfree` 整个 `platform_device`。**如果面单和包裹是两块内存，就得维护两个生命周期，而其中一个的释放时机由外部持有者决定——这是必然出错的形状。** 嵌进去之后只有一个生命周期，只有一个计数。

`devres` 用的是同一招（`drivers/base/devres.c:26-36`）：

```c
struct devres {
	struct devres_node		node;
	u8 __aligned(ARCH_KMALLOC_MINALIGN) data[];
};
```

`devm_kzalloc()` 返回的是 `data` 的地址，而 `devres_add()` 拿到这个地址后靠 `container_of` 反推回管理头（`drivers/base/devres.c:242`）：

```c
	struct devres *dr = container_of(res, struct devres, data);
```

这就是为什么 `devm_kfree()` 只能传给它 `devm_kzalloc()` 返回的那个指针，偏一个字节都不行。

### 这个类比什么时候会误导你？

面单贴在包裹上是"一个包裹一张面单"。但内核里**一个容器结构体可以嵌多个 `struct device`** —— 少见但存在，而且 `container_of` 在这种情况下靠的是"从哪个成员反推"，写错成员就悄悄拿到错误的对象，编译器一句话不说。

更常踩的是反向：`container_of` 的正确性**完全依赖调用方知道这个 device 的真实容器类型**。`to_platform_device(dev)` 对一个 `spi_device` 调用会返回一个指向随机偏移的指针，编译通过、运行时读出垃圾。所以 `bus_type` 的 `match()`/`probe()` 回调里可以放心 `container_of`（能进来的必然是本总线的设备），而通用代码里绝对不行。这是内核里"类型安全靠约定而非语言"的典型场景。

---

## §4 probe 为什么会 defer？内核为什么不干脆排好顺序？

### 先举个例子

装修房子。要装的东西有：吊灯、空调、地暖、智能门锁。它们之间有依赖——吊灯得先有电，空调得先有电和排水，门锁得先有 WiFi。

理想做法是画一张甘特图，按依赖排好顺序，一步步来。这是"排序"方案。

实际装修队的做法是：**每个工人到场就先试着干，干不了就说"我等等再来"，然后去干下一件。** 转几圈之后，能干的都干完了。这是"重试"方案。

内核选了重试。

### 回到代码

驱动 `probe()` 里发现依赖没就绪，返回 `-EPROBE_DEFER`。核心把它挂进一条链表（`drivers/base/dd.c:132-143`）：

```c
void driver_deferred_probe_add(struct device *dev)
{
	if (!dev->can_match)
		return;

	mutex_lock(&deferred_probe_mutex);
	if (list_empty(&dev->p->deferred_probe)) {
		dev_dbg(dev, "Added to deferred list\n");
		list_add_tail(&dev->p->deferred_probe, &deferred_probe_pending_list);
	}
	mutex_unlock(&deferred_probe_mutex);
}
```

然后每当**有任何一个新驱动注册成功**，就把整条链表重新试一遍（`drivers/base/driver.c:255` 在 `driver_register()` 末尾调 `deferred_probe_extend_timeout()`；真正的重试触发在 `drivers/base/dd.c:122` 的 "Retrying from deferred list"）。

为什么不排序？因为**依赖关系在启动时是不可知的**。要排序就得先知道"吊灯需要电"，而这个信息只有吊灯的驱动自己知道，而驱动代码要等到它被调用才有机会说话。设备树里能读出一部分（这就是 §6 的 `fw_devlink` 在做的事），但读不出全部——一个驱动可能在 `probe()` 里根据读到的芯片 ID 决定要不要一个额外的 regulator。

重试方案的美妙之处在于它**不需要任何全局知识**。代价是最坏情况 O(n²) 次 probe 尝试，以及"永远等不到"的情况需要一个超时来收尾（`deferred_probe_timeout=`，见 `07-debug.md` §3.2）。

### 这个类比什么时候会误导你？

装修工人"等等再来"是自己决定的。内核里 `-EPROBE_DEFER` 之后驱动**完全被动**——它不能自己选什么时候重试，只能等核心叫它。而核心的触发条件只有两个：有新驱动注册，或者超时定时器到点。

这带来一个类比想不到的后果：**如果你等的那个东西不是通过"注册驱动"就绪的，你就永远不会被重试**。典型例子是等一个 GPIO 芯片的某个 pin——GPIO 芯片的驱动早就注册好了，只是那个 pin 还没配置。这种情况下 defer 会一直挂到超时，日志里看不出任何"等谁"的信息。这是 `07-debug.md` §9.2 那条路线存在的原因。

另一处：`dev->can_match` 那个提前返回（`drivers/base/dd.c:134-135`）意味着**从来没匹配上的设备不会进链表**。装修类比里那个工人至少到场过；内核里这个设备是"根本没工人来"。两种状态在 `/sys/kernel/debug/devices_deferred` 里长得一模一样——都是不出现。

---

## §5 `devm_*` 凭什么可以"忘了释放"？

### 先举个例子

租一间带家具的短租公寓，签合同时选了"打包退租"服务。住的期间你买了台灯、加了个书架、办了张健身卡。退租那天你不用一件件退——**跟前台说一声"我走了"，他们按你入住时那份清单，从后往前逐条撤销**：先退健身卡，再搬走书架，最后拿掉台灯。

顺序是反的，这不是巧合：书架可能是靠在台灯插座那面墙上装的，先拆台灯会碰倒书架。

### 回到代码

每个 `devm_*` 申请都在设备上挂一个节点（`drivers/base/devres.c:19-24`）：

```c
struct devres_node {
	struct list_head		entry;
	dr_release_t			release;
	const char			*name;
	size_t				size;
};
```

`release` 是"怎么退掉这一项"。解绑时 `devres_release_all()` 走一遍链表，注意方向（`drivers/base/devres.c:493-505`）：

```c
static void release_nodes(struct device *dev, struct list_head *todo)
{
	struct devres *dr, *tmp;

	list_for_each_entry_safe_reverse(dr, tmp, todo, node.entry) {
		devres_log(dev, &dr->node, "REL");
		dr->node.release(dev, dr->data);
		kfree(dr);
	}
}
```

`_safe_reverse` —— **逆序**。这一个词就是整个 devres 的正确性根基：申请顺序天然编码了依赖顺序（你不会在申请到寄存器之前就注册中断处理函数），逆序释放就自动满足了依赖。驱动作者什么都不用想。

调用点是 `device_unbind_cleanup()`（`drivers/base/dd.c:528-536`），它在 probe 失败和正常解绑两条路上都会走到——所以 `probe()` 里申请到一半失败时，前面申请的也会被清掉。这是 `devm_*` 最实用的性质：**它让 probe 的错误处理从"逐层 goto 标签"变成"直接 return"**。

### 这个类比什么时候会误导你？

退租类比暗示"所有东西都能打包退"。devres 有一条硬边界：**它只能在设备解绑时释放**。如果你的资源需要在别的时刻放掉（比如收到一个用户态请求就要关掉某个时钟），devres 帮不了你——那份资源会一直占着直到 unbind。

第二处更隐蔽：`devres_group`（`drivers/base/devres.c:38-43`）看着像"打包里的小包"，但它的 `node[2]`（头尾两个节点）意味着 group 在链表里占**两个位置**，而 §11 那些计数如果按节点数算就会把一个 group 数成两条。这也是为什么 `07-debug.md` §6.4 那张表里 `REM` 有两个来源。

第三处：**没有任何机制阻止你混用。** `devm_kzalloc()` 分配的内存用 `kfree()` 释放，编译通过、运行时不立刻崩，直到 unbind 时 `release()` 去 `kfree` 一块已经被 free 的地址。这类 bug 的现场离案发点很远，是 §8 要讲的问题的一部分。

---

## §6 `fw_devlink` 凭一份设备树怎么"猜"出依赖？

### 先举个例子

拿到一份陌生城市的地铁线路图，图上只画了站和线，没有任何"换乘顺序"的说明。但你能推出很多东西：**看到两条线在某站交叉，就知道这站是换乘站；看到某条线是单向环线，就知道有些方向去不了。** 你没有任何运营手册，只靠图的结构。

再具体一点：图上写"某站出口接机场大巴"，你就知道这站的运营必须依赖机场大巴公司，哪怕图上没写"依赖"这个词。**因为"引用了别人的名字"本身就是依赖的证据。**

`fw_devlink` 做的正是这件事：**在设备树里凡是引用了别人 phandle 的地方，都推定为一条依赖。**

### 回到代码

设备树里的引用长这样：

```dts
	i2c0: i2c@ff020000 {
		clocks = <&clkc 38>;          /* 引用了时钟控制器 */
		interrupt-parent = <&gic>;    /* 引用了中断控制器 */
		pinctrl-0 = <&i2c0_default>;  /* 引用了引脚配置 */
	};
```

`fw_devlink` 不认识 `clocks` 这个属性的语义，它只知道"这个属性里有 phandle"。`drivers/of/property.c:1341-1376` 那张表就是全部规则——`of_supplier_bindings[]`，34 个 `parse_prop` 条目（`awk 'NR>=1341 && NR<=1376' drivers/of/property.c | grep -c 'parse_prop = '`）：

```c
static const struct supplier_bindings of_supplier_bindings[] = {
	{ .parse_prop = parse_clocks, },
	{ .parse_prop = parse_interconnects, },
	{ .parse_prop = parse_iommus, .optional = true, },
	...
	{ .parse_prop = parse_pinctrl0, },
	...
	{ .parse_prop = parse_regulators, },
	{}
};
```

注意 `parse_pinctrl0` 到 `parse_pinctrl8` 占了九个条目——`pinctrl-0` 到 `pinctrl-8`，不是循环而是九个宏展开出来的函数。这是"表驱动"和"硬编码"混在一起的产物。

推断出来的关系先记成 fwnode link，等对应的 `struct device` 真被创建出来，再升级成真正的 device link。三个关键函数：

| 函数 | 位置 | 干什么 |
|---|---|---|
| `fw_devlink_parse_fwtree()` | `drivers/base/core.c:1774-1782` | 递归遍历整棵设备树，把所有引用记下来 |
| `fw_devlink_create_devlink()` | `drivers/base/core.c:2051` | 一条 fwnode link → 一条 device link |
| `__fw_devlink_link_to_consumers()` | `drivers/base/core.c:2162` | 新设备出现时，回头连上等它的那些 |

结果就是启动时 probe 的顺序**在第一次 probe 之前就大致排好了**，defer 的次数大幅下降。

### 这个类比什么时候会误导你？

地铁图是准确的——图上有的线现实里就有。设备树不是：**它是一份"硬件长什么样"的描述，不是"软件依赖什么"的描述。**

三处具体的偏差：

**一、引用了不等于依赖。** 一个驱动完全可以在 DTS 里声明 `clocks = <&clkc 38>` 却在某些配置下根本不去 `clk_get()`。`fw_devlink` 照样给它建一条依赖，于是它白等。这就是 `parse_iommus` / `parse_dmas` 那几个条目带 `.optional = true` 的原因（`drivers/of/property.c:1343`、`:1348`）——有些属性明知可能是假依赖，就降级处理。

**二、依赖了不一定有引用。** 驱动内部通过全局查表拿到的资源（按名字找一个 regulator、按 ID 找一个 phy）在设备树里没有 phandle，`fw_devlink` 完全看不见。所以 `fw_devlink` 减少 defer，不消除 defer。

**三、这个推断本身可能造成死锁式的循环等待。** 这也是 `fw_devlink=` 有那么多档位、以及 `fw_devlink=off` 至今没被删掉的原因（见 `07-debug.md` §3.4）。地铁图不会自相矛盾，设备树会。

---

## §7 `platform` 为什么被叫"假总线"，又为什么是最忙的一条？

### 先举个例子

一栋楼里的房间大多有明确用途：会议室、机房、洗手间。但总有几间叫"多功能厅"——**它的定义就是"没有专门定义"**。堆杂物、临时办公、放服务器，什么都往里塞。

结果是：楼里最大、用得最多、最乱的那间房，恰恰是那间"没有用途"的房。

### 回到代码

`platform` 总线的定义（`drivers/base/platform.c:1478-1490`）看着跟任何一条真总线一样齐全：

```c
struct bus_type platform_bus_type = {
	.name		= "platform",
	.dev_groups	= platform_dev_groups,
	.match		= platform_match,
	.uevent		= platform_uevent,
	.probe		= platform_probe,
	.remove		= platform_remove,
	.shutdown	= platform_shutdown,
	.dma_configure	= platform_dma_configure,
	.dma_cleanup	= platform_dma_cleanup,
	.pm		= &platform_dev_pm_ops,
```

"假"的地方在于它**没有电气层**。I2C 总线上真的有两根线、有地址、有时钟；platform 总线上什么都没有——它只是"这个设备的存在由固件/代码直接声明，不需要枚举"的意思。

`platform_match()`（`drivers/base/platform.c:1331-1354`）体现了这种无定义带来的复杂：它得依次试**四种**匹配方式：

```c
	/* When driver_override is set, only bind to the matching driver */
	if (pdev->driver_override)
		return !strcmp(pdev->driver_override, drv->name);

	/* Attempt an OF style match first */
	if (of_driver_match_device(dev, drv))
		return 1;
	...
	/* Then try to match against the id table */
	if (pdrv->id_table)
		return platform_match_id(pdrv->id_table, pdev) != NULL;

	/* fall-back to driver name match */
	return (strcmp(pdev->name, drv->name) == 0);
```

最后那个"按名字字符串比"是最原始的手段，也是最容易撞车的。一条真总线不需要这么多档——I2C 只按地址和 compatible。

至于"最忙"，两个可复核的数字：全树 `struct bus_type` 的实例有 146 个（`grep -rn 'struct bus_type [a-z_]* = {' --include=*.c . | wc -l`），而使用 `platform_driver_register()` 或 `module_platform_driver()` 的 .c 文件有 4019 个（`grep -rlw 'platform_driver_register\|module_platform_driver' --include=*.c . | wc -l`）。**在 arm64 上，一个 SoC 上的绝大多数外设都挂在 platform 总线上**——因为它们既不是 PCI 也不是 USB，就是一段固定地址的寄存器。

### 这个类比什么时候会误导你？

"多功能厅"暗示"什么都能塞、塞进去也没关系"。platform 总线有一条硬性代价：**因为没有枚举，它无法发现设备**。设备必须被别人造出来。arm64 上造它的是 `of_platform_populate()` 那条路径（见 `06-devicetree.md` §2），DTS 里没写的设备在 platform 总线上永远不会出现。

第二处：`platform_device` 的 `name` 字段既参与匹配又参与 sysfs 命名，而 `driver_override`（`drivers/base/platform.c:1337-1338`）能绕过全部匹配逻辑直接指定驱动。这个字段在真总线上很少见，是 platform "什么都可以" 的另一个体现——也是一个可写的 sysfs 入口，写错了会让设备绑上完全不合适的驱动。

第三处也是最容易误解的：**"假总线"不代表"低级"或"临时"**。它是内核里最稳定的抽象之一，`drivers/base/platform.c` 在 6.1 里有超过一千五百行，比多数真总线的核心代码都长。

---

## §8 为什么 remove 比 probe 难写得多？

### 先举个例子

搬进一间办公室：接网线、装显示器、连打印机、加入部门群、把工位号告诉快递。**每一步都是"我主动去做"，做的时候环境是稳定的。**

搬出去：拔网线的时候，可能正有人在给你发文件；退部门群的时候，可能正有人 @你；快递可能已经在路上。**每一步都要考虑"有没有人正在用我"**，而这个"有没有"你自己不知道。

probe 是搬进来，remove 是搬出去。不对称的根源不是步骤多少，而是**probe 期间没有别人在用你，remove 期间有**。

### 回到代码

`__device_release_driver()`（`drivers/base/dd.c:1226-1277`）整个函数只有 52 行，但开头那个循环把问题说尽了（`drivers/base/dd.c:1234-1249`）：

```c
		while (device_links_busy(dev)) {
			__device_driver_unlock(dev, parent);

			device_links_unbind_consumers(dev);

			__device_driver_lock(dev, parent);
			/*
			 * A concurrent invocation of the same function might
			 * have released the driver successfully while this one
			 * was waiting, so check for that.
			 */
			if (dev->driver != drv) {
				pm_runtime_put(dev);
				return;
			}
		}
```

读这段的三个要点：

- 它是 `while` 不是 `if`：解绑一批消费者的过程中可能又冒出新的消费者。
- 中途**要放掉锁**（`__device_driver_unlock`）才能去解绑消费者，因为解绑消费者会走到同一套加锁路径上。
- 放锁之后必须重新检查 `dev->driver != drv`——另一个 CPU 可能已经把活干完了。这个检查如果漏掉，就是一次 double-free。

probe 那一侧完全没有对应的东西。这就是"不对称"的具体形状：一个 52 行的函数里有 16 行专门处理"放锁重查"，而 probe 主流程里一行都没有。

剩下的顺序也不能乱（`drivers/base/dd.c:1251-1274`）：先 `driver_sysfs_remove()`（断掉用户态入口），再发 `BUS_NOTIFY_UNBIND_DRIVER` 通知，再 `device_remove()` 调驱动自己的 `remove()`，再 `dma_cleanup`，**然后**才 `device_unbind_cleanup()` 释放 devres。也就是说驱动的 `remove()` 跑的时候，它的 `devm_*` 资源还都在——这是 devres 能工作的前提。

### 这个类比什么时候会误导你？

搬办公室你可以慢慢来，可以中途改主意说"算了我不搬了"。**`remove()` 不能失败。** 看 `platform_remove()` 的原型（`drivers/base/platform.c:1414`）和 `bus_type` 的 `.remove`（`include/linux/device/bus.h:96`）——都是 `void`。

这条约束的后果被严重低估：如果 `remove()` 里某一步失败了（DMA 还没停、固件还没响应），驱动**没有任何办法把这个失败报出去**，只能自己想办法处理或者干脆忽略。这是内核里返回值黑洞的经典案例（`07-debug.md` §6.4 里 devres 那条同类）。

第二处误导：搬出去之后办公室是空的。设备解绑之后 `struct device` **还在**——它只是没有驱动了，`/sys/devices/` 下的目录仍然存在，`kobject` 引用计数可能还很高。真正的消失是 `device_del()` + `put_device()`（`drivers/base/core.c:3925-3930` 的 `device_unregister()`），跟解绑是两件独立的事。把"unbind"和"设备消失"混起来，是读 `drivers/base` 时最常见的一处混淆。

---

## §9 异步探测为什么能加速启动，代价是什么？

### 先举个例子

早上到公司要做四件事：烧水、开电脑、取快递、回邮件。串行做完要 20 分钟。

聪明的做法是：**先按下烧水开关和电脑电源，趁它们自己跑的时候去取快递。** 总时间压到 8 分钟。关键洞察是：烧水的 5 分钟里，你并没有在干活，你在等。

启动时大量的 probe 也在等——等固件加载、等 PLL 锁定、等复位释放、等某个 100ms 的延时。串行做这些是纯浪费。

### 回到代码

异步探测的入口是 `driver_allows_async_probing()`（`drivers/base/dd.c:853-871`）：

```c
	switch (drv->probe_type) {
	case PROBE_PREFER_ASYNCHRONOUS:
		return true;

	case PROBE_FORCE_SYNCHRONOUS:
		return false;

	default:
		if (cmdline_requested_async_probing(drv->name))
			return true;

		if (module_requested_async_probing(drv->owner))
			return true;

		return false;
	}
```

三档：驱动自己明确要、驱动自己明确不要、由外部（命令行或模块参数）决定。默认是**不要**——异步是选择加入的。

真正的丢出去在两处：`drivers/base/dd.c:1038`（设备找驱动的方向）和 `drivers/base/dd.c:1196`（驱动找设备的方向），都是 `async_schedule_dev()`。

那么"什么时候算探测完了"？`wait_for_device_probe()`（`drivers/base/dd.c:749-762`）三步等齐：

```c
	/* wait for the deferred probe workqueue to finish */
	flush_work(&deferred_probe_work);

	/* wait for the known devices to complete their probing */
	wait_event(probe_waitqueue, atomic_read(&probe_count) == 0);
	async_synchronize_full();
```

三个不同的等待机制串在一起，正好对应三条并发路径：deferred 重试队列、同步 probe 的计数、异步 probe 的 async cookie。少等任何一个都会漏。

### 这个类比什么时候会误导你？

烧水和取快递互不相干，所以能并行。**probe 之间常常相干，而且相干关系是隐式的。**

三处代价：

**一、日志会交错。** 异步 probe 的 `dev_err` 和别人的混在一起，dmesg 里"上一行"不再是同一个设备的上一步。排查时必须靠设备名前缀过滤（§2 讲的那个会变的前缀）。

**二、顺序依赖会随机地暴露。** 一个偷偷依赖"某个 GPIO 已经被别人初始化"的驱动，在同步启动下永远正常，改成异步后有几分之一的概率失败。这类 bug 极难复现——所以 `PROBE_FORCE_SYNCHRONOUS`（`drivers/base/dd.c:859-860`）存在，用来给已知有问题的驱动打钉子。

**三、`/sys/.../uevent` 的时序变了。** 用户态 udev 规则如果假设"看到 A 的 uevent 时 B 已经就绪"，异步之后这个假设就破了。这不是内核 bug，但表现为"升级内核后 udev 规则不生效"。

烧水那个例子还漏了一件事：**你可以选择不并行，但内核里这个选择权在驱动作者手上，不在你手上。** 排查时唯一能做的是从外面强制关掉——`driver_async_probe=` 的用法见 `07-debug.md` §3.3。

---

## §10 阅读下一篇

| 想知道 | 去 |
|---|---|
| 全套文档的入口和复习路径 | `00-overview.md` |
| 这些结构体的字段逐个是什么 | `02-datastructures.md` |
| probe / remove 流程逐段剖析 | `03-internals.md` Part A |
| 要写代码，需要骨架 | `05-usage.md` |
| 上面每条论断的取证等级 | `99-evidence.md` |
