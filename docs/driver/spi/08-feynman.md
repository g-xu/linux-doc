---
title: 费曼学习法
---

# SPI 关键概念的大白话解释

> 这一篇专治"代码我看懂了，但不知道为什么要这么设计"。
> 每个主题四段：**问题** → **先举个例子**（生活类比）→ **回到代码**（带行号）
> → **这个类比什么时候会误导你？**
>
> 最后一段是重点。类比是脚手架，用完要拆——不指出类比的边界，
> 你会带着一个错误的心智模型去读代码，那比不打类比更糟。

---

## §0 主题一览

| # | 问题 | 难在哪 |
|---|-----|-------|
| 1 | [`spi_controller` 和 `spi_device` 为什么不在同一条总线上？](#1) | 本子系统最容易被类比错的一组关系，而且**和 i2c 的做法不一样** |
| 2 | [一条 `spi_message` 为什么要装多个 `spi_transfer`？分几次发不行吗？](#2) | "消息"这一层看着多余 |
| 3 | [`spi_sync()` 明明是同步的，为什么有时要绕一个内核线程？](#3) | 同一个函数两条完全不同的路径 |
| 4 | [DTS 里明明写了 `spi-cs-high`，为什么片选极性没变？](#4) | 一个"配置了但被无视"的真实陷阱 |
| 5 | [我只想读 4 个字节，为什么内核还要往外发 4 个字节？](#5) | 哑缓冲的存在理由 |
| 6 | [`bits_per_word` 改成 16 之后，为什么 `len` 也得跟着改？](#6) | 两个字段的隐藏耦合 |
| 7 | [为什么不能像 I2C 那样扫一遍总线看挂了什么设备？](#7) | 协议层面的根本差异 |
| 8 | [为什么 SPI 核心几乎不打日志？](#8) | 不是遗漏，是设计取向 |

---

<a id="1"></a>
## 1. `spi_controller` 和 `spi_device` 为什么不在同一条总线上？

### 先举个例子

想象一栋写字楼里的**快递代收点**。

- **楼**是 `platform` 总线：楼里的固定设施（电梯、配电房、代收点）都是楼的一部分，
  由物业按图纸安装。
- **代收点**就是 `spi_controller`。它是楼里的一个设施，物业（platform 总线）
  负责把它装起来、通水通电。
- **各家公司**是 `spi_device`。它们**不是楼的设施**——它们是代收点的客户。
  谁家在 3 号柜、谁家在 7 号柜，这是**代收点自己的账本**，跟楼的图纸没关系。

所以"公司"不可能登记在物业的设施清单上，只能登记在代收点的客户名册上。
这个名册就是 `spi_bus_type`。

**但这个例子还漏了一层，而且正是最反直觉的一层**：代收点本身
**既不在物业的设施清单上，也不在自己的客户名册上**——它是第三种东西。
下面就说这个。

### 回到代码

三层，各自的登记方式**完全不同**：

| 层 | 是什么 | 怎么登记 | 行号 |
|---|-------|---------|-----|
| SPI 控制器**硬件节点** | `platform_device` | DT 节点由 `of_platform_populate()` 变成 platform 设备，走 `platform_bus_type` | 不在 `spi.c` 里 |
| **`spi_controller`** | **纯 class 设备**，`dev.bus` **从不赋值** | `ctlr->dev.class = &spi_master_class`（或 `&spi_slave_class`） | `drivers/spi/spi.c:2926` / `:2928` |
| **`spi_device`** | 挂在 SPI 总线上的设备 | `spi->dev.bus = &spi_bus_type` | `drivers/spi/spi.c:598` |

三层之间靠 `dev.parent` 串起来：

```
platform_device (SPI 控制器硬件, 在 platform 总线上)
        ↑ ctlr->dev.parent = dev          drivers/spi/spi.c:2929
spi_controller (class 设备，不在任何总线上)
        ↑ spi->dev.parent = &ctlr->dev
spi_device (在 spi_bus_type 上)           drivers/spi/spi.c:598
```

**为什么 `spi_controller` 不上总线？** 因为"总线"在驱动模型里的意义是
**给设备配驱动**（`bus_type.match` + `probe`）。而 `spi_controller`
**不需要被匹配驱动**——它是被控制器驱动**创建出来的**，创建它的
`platform_driver` 就是它的驱动。再给它挂一条总线，就得凭空发明一个
"谁来 probe spi_controller"的问题。所以内核只给它一个 `class`
（提供 `/sys/class/spi_master/`，让用户态能枚举），不给它 `bus`。

**这里有个真实的对照，而且结论相反**：I2C 做了另一种选择。

```c
	adap->dev.bus = &i2c_bus_type;              /* drivers/i2c/i2c-core-base.c:1554 */
	adap->dev.type = &i2c_adapter_type;         /* :1555 */
```

i2c 把 adapter **也放在 i2c 总线上**，靠 `dev.type` 区分"这是适配器还是客户端"，
`i2c_device_match()` 里再按 type 分流。SPI 直接用 class 把两者分开，
**不需要在 match 里做类型判断**。

| | SPI | I2C |
|---|-----|-----|
| 控制器/适配器在哪 | 只有 `class`，不上总线 | `dev.bus = &i2c_bus_type` + `dev.type` 区分 |
| 设备在哪 | `dev.bus = &spi_bus_type` | `dev.bus = &i2c_bus_type` |
| match 要不要判类型 | **不要**，总线上只有一种东西 | **要**，靠 `dev.type` |
| 依据 | `spi.c:2926`/`:2928`/`:598` | `i2c-core-base.c:1554`/`:1555` |

**所以"总线子系统都是这么做的"是个错觉——两个最像的子系统就做了两种。**

### 这个类比什么时候会误导你？

1. **"代收点是楼的设施，所以代收点是 platform_device"——错。**
   `spi_controller` 和那个 `platform_device` 是**两个不同的 `struct device`**，
   前者的 `parent` 指向后者（`:2929`）。所以 `/sys/devices/platform/.../spi_master/spi0/`
   这个路径里有两级，不是一级。写驱动时 `ctlr->dev.parent` 才是那个
   platform 设备（`spi.c` 里用了十几次，如 `:640`、`:1151`、`:1648`），
   要拿 DMA、时钟、pinctrl、runtime PM 都得用 parent，**不能用 `&ctlr->dev`**。

2. **"客户名册就是一张表，能列出所有客户"——不对。**
   `spi_bus_type` 上挂的是**所有控制器的所有设备**，不是一条总线的。
   所以查"这个片选号被占了吗"时，`spi_dev_check()` 必须先比较
   `spi->controller == new_spi->controller`（`:625`）才能比片选号（`:626`）——
   因为 `bus_for_each_dev()`（`:648`）扫的是整条 SPI 总线。
   **不同控制器上的 `reg = <0>` 互不冲突。**

3. **"公司是客户，所以随时可以来一个新公司"——协议上不行。**
   SPI 设备不能被探测出来（见主题 7），必须提前写在 DTS 里。
   "客户名册"是**开机时按图纸抄进去的**，不是运营中登记的。

---

<a id="2"></a>
## 2. 一条 `spi_message` 为什么要装多个 `spi_transfer`？分几次发不行吗？

### 先举个例子

想象你去银行柜台办事，要做三个动作：出示身份证、填表、取钱。

如果你把这三件事拆成**三次排队**，那么每次都要重新排队，而且**中间可能插进别人**——
你出示了身份证，柜员转头给下一个人办了业务，再回来时你的身份已经"过期"了，
得重新出示。

`spi_message` 就是"**我要一次性占着这个柜台，把这三件事连着做完**"。
`spi_transfer` 是三件事本身。

**为什么 SPI 特别需要这个？** 因为 SPI 外设几乎全是"**先发命令，再收数据**"
的两段式：第一段发 `0x03 <地址>`，第二段读回内容。这两段中间
**片选不能松**——片选一松，外设就认为这次会话结束了，地址状态清零。
下一次发数据它会当成新命令来解析。

### 回到代码

"片选默认不松"这件事就写在 `spi_transfer_one_message()` 的循环里
（`drivers/spi/spi.c:1585-1599`）：

```c
		if (xfer->cs_change) {                      /* :1585 —— 只有显式要求才动 */
			if (list_is_last(&xfer->transfer_list,
					 &msg->transfers)) {
				keep_cs = true;                     /* :1588 最后一段：留着不松 */
			} else {
				if (!xfer->cs_off)
					spi_set_cs(msg->spi, false, false);   /* :1591 松 */
				_spi_transfer_cs_change_delay(msg, xfer);  /* :1592 */
				if (!list_next_entry(xfer, transfer_list)->cs_off)
					spi_set_cs(msg->spi, true, false);     /* :1594 再拉 */
			}
		}
```

| 事实 | 依据 | 含义 |
|-----|-----|------|
| 默认（`cs_change == 0`）**段与段之间片选不动** | `:1585` 的 `if` 不成立就什么都不做 | 这就是"一条消息 = 一次会话"的实现 |
| `cs_change` 在**最后一段**的含义是反的 | `:1586-1588` → `keep_cs = true` | 不是"松一下"，而是"**消息结束后也别松**"（为下一条消息省一次片选） |
| 中间段的 `cs_change` = 松 → 等 → 再拉 | `:1591`-`:1594` | 有些外设要求命令和数据之间必须掉一次片选 |
| 那个"等"如果单位不支持，只 `dev_err_once` 然后用默认值 | `:1493-1497` | 只打一次，见 `07-debug.md` §2.3 |

**更高一层的"占住柜台"是 `spi_bus_lock()`**：`spi_message` 只保证
**一条消息内部**不被打断；要保证**多条消息之间**也不被别人插进来
（比如"发命令 → 等外设忙完 → 读结果"这种跨消息的时序），就得用
`spi_bus_lock()` / `spi_bus_unlock()`（见 `01-api.md` §5）。
锁期间 `spi_async()` 一律返 `-EBUSY`（`:4073`，`07-debug.md` §5.4）。

### 这个类比什么时候会误导你？

1. **"一条消息就是一个原子操作"——不是。**
   `spi_message` 只保证**片选连续**和**这条消息内部不插入别的消息**，
   不保证时间上连续。段与段之间可以有 `delay`（`:1583` 的
   `spi_transfer_delay_exec()`），CPU 可以被抢占，中断可以发生。
   需要严格的微秒级时序，`spi_message` 给不了你。

2. **"柜台一直是我的，所以我可以慢慢想"——不行。**
   一条消息里的所有 `spi_transfer` 和缓冲区，在 `spi_sync()` 返回前
   **都不能释放**（`spi_sync()` 的 kerneldoc 明确写了
   "the caller is guaranteeing that the memory associated with the message
   will not be freed before this call returns"，`:4253-4254`）。
   异步接口更严：直到 `complete` 回调被调用才算完。

3. **"多段比多条消息一定快"——不一定。**
   核心会为超长的段做拆分（`transfers_split_maxsize` 统计，
   `07-debug.md` §4.3），拆出来的段是新分配的。段数多到一定程度，
   每段的固定开销（`spi_set_cs`、延时、统计）反而占主导——
   这正是 `07-debug.md` §4.5 直方图集中在 `0-1`/`2-3` 桶时要警惕的事。

---

<a id="3"></a>
## 3. `spi_sync()` 明明是同步的，为什么有时要绕一个内核线程？

### 先举个例子

想象一家只有一个窗口的小吃店。

- **没人排队**的时候，你走过去说"一碗面"，老板当场就给你做。这叫**直接执行**。
- **前面有人排队**的时候，你必须**去队尾排着**，然后站在那儿等叫号。
  你还是"等到吃上面才走"（同步），但你的面是**按队列顺序**做的。

为什么不能"没人排队时插队，有人排队时也插队"？因为**如果你插队，
排在你前面的人的面就后出锅了**——顺序乱了。而 SPI 的顺序是有意义的：
前面那条消息可能是"发写使能命令"，你这条是"写数据"，颠倒了就废了。

### 回到代码

`__spi_sync()` 的分岔就是这个（`drivers/spi/spi.c:4204`）：

```c
	if (READ_ONCE(ctlr->queue_empty) && !ctlr->must_async) {
		...
		SPI_STATISTICS_INCREMENT_FIELD(ctlr->pcpu_statistics, spi_sync_immediate);   /* :4210 */
		__spi_transfer_message_noqueue(ctlr, message);                              /* :4213 */
		return message->status;                                                     /* :4215 */
	}
	/* 否则：进队列，然后等 */
	message->complete = spi_complete;      /* :4224 */
	message->context = &done;              /* :4225 */
	status = spi_async_locked(spi, message);  /* :4226 */
	if (status == 0) {
		wait_for_completion(&done);        /* :4228 */
```

源码注释（`:4198-4203`）自己解释了为什么这个检查是安全的：

> Checking queue_empty here only guarantees async/sync message ordering
> when coming from the same context. It does not need to guard against
> reentrancy from a different context. The io_mutex will catch those cases.

| 分支 | 条件 | 代价 | 统计 |
|-----|-----|-----|-----|
| **快路径** | 队列空 **且** `!must_async` | 无线程切换，延迟最低 | `spi_sync_immediate` +1（`:4210`/`:4211`） |
| **慢路径** | 队列非空 **或** `must_async` | 一次入队 + 一次唤醒 + 一次 `wait_for_completion` | 只有 `spi_sync` +1（`:4195`/`:4196`） |

**`must_async` 全树只在一个地方被设为 true**：

```c
	ctlr->must_async = true;         /* drivers/spi/spi-mux.c:165 */
```

即**只有 SPI mux**（一个控制器后面挂多路复用器）强制禁用快路径——因为
切换 mux 本身要发 SPI 命令，快路径里会递归。见 `04-extensions.md`。

**怎么知道自己走的哪条？** 这就是 `07-debug.md` §4.4 那一个比值：

```bash
cd /sys/class/spi_master/spi0/statistics
echo "$(cat spi_sync_immediate) / $(cat spi_sync)"
```

### 这个类比什么时候会误导你？

1. **"排队 = 慢，直接做 = 快，所以要想办法总走快路径"——方向对，但你控制不了。**
   `queue_empty` 取决于**别人**在不在用这条总线。你唯一能做的是
   **减少这条总线上的其他使用者**，或者接受抖动。
   把 `spi->rt` 设上**不会**让你走快路径——RT 优先级作用在 pump 线程上，
   而快路径**根本不经过 pump 线程**（`03-internals.md` §B9）。
   比值 ≈ 1.0 的时候提优先级完全无效，这是 `07-debug.md` §4.4 的第一条。

2. **"排队的人是别的进程"——不一定。**
   队列里也可能是**你自己**刚才发的异步消息（`spi_async()`）。
   注释里的 "async/sync message ordering when coming from the same context"
   说的就是这个：混用 `spi_async()` 和 `spi_sync()` 时，
   `spi_sync()` 必须去排队才能保证你自己的两条消息不乱序。

3. **"同步就是有超时保护"——两条路径不一样。**
   `spi_sync()` 本身的 kerneldoc 写得很清楚：
   "The sleep is non-interruptible, and has no timeout."
   **`spi_sync()` 这一层没有超时**。超时是在更下面一层
   **每一段传输**上做的（`spi_transfer_wait()`，`:1366-1396`，
   `07-debug.md` §6.1）。所以控制器如果连"传输开始"都没做到，
   `spi_sync()` 可以卡到天荒地老——而且 `wait_for_completion()`（`:4228`）
   **不可打断**，`Ctrl-C` 也救不了。原话在 `:4244`。

---

<a id="4"></a>
## 4. DTS 里明明写了 `spi-cs-high`，为什么片选极性没变？

### 先举个例子

想象你租房，房东给了你一把钥匙，还给了一张纸条："**这把锁是反的，
往左转是开**"。

- 如果这门是**房东自己装的老锁**，那张纸条有用——你照着做就对了。
- 但如果这门后来被**物业统一换成了智能锁**，物业那边**已经在系统里
  登记了开锁方向**。你手里那张纸条还在，但**没人会去读它**了。
  你还照着纸条转，可能反而开不了。

`spi-cs-high` 就是那张纸条。**原生片选**（控制器自己的引脚）时它有用；
**GPIO 片选**时，gpiolib 已经从 `cs-gpios` 的标志位里知道了极性，
**核心不再看这张纸条**。

### 回到代码

`spi_set_cs()` 里有一个变量的用法值得逐字看（`drivers/spi/spi.c:976`）：

```c
static void spi_set_cs(struct spi_device *spi, bool enable, bool force)
{
	bool activate = enable;                    /* :976 —— 原始意图，此后永不修改 */
	...
	if (spi->mode & SPI_CS_HIGH)               /* :996 */
		enable = !enable;                  /* :997 —— 只翻转 enable，不动 activate */
```

于是四个出口用的是**两个不同的变量**：

| 出口 | 场景 | 用哪个变量 | 行号 |
|-----|-----|----------|-----|
| `gpiod_set_value_cansleep(...cs_gpiod[idx], activate)` | **DT 的 GPIO 片选** | **`activate`** | `:1015` |
| `gpiod_set_value_cansleep(..., !enable)` | ACPI 的 GPIO 片选 | `!enable` | `:1012` |
| `ctlr->set_cs(spi, !enable)`（额外原生片选） | `SPI_MASTER_GPIO_SS` | `!enable` | `:1020` |
| `ctlr->set_cs(spi, !enable)` | 纯原生片选 | `!enable` | `:1022` |

`:1014-1015` 上面就是那句解释：

```c
			/* Polarity handled by GPIO library */
```

**所以 `SPI_CS_HIGH`（也就是 `spi-cs-high`）在 DT + GPIO 片选下
完全不参与决定电平。** 极性 100% 来自 `cs-gpios` 里的
`GPIO_ACTIVE_HIGH` / `GPIO_ACTIVE_LOW` 标志。

**而且不会有任何报错**：`spi.c:3174` 一带无条件把 `SPI_CS_HIGH` 加进
控制器的 `mode_bits`，所以 `spi_setup()` 的能力检查（`:3742-3745`）
永远不会拒绝它。**配了，被吃掉了，一声不响。**

更有意思的是，**绑定文档说这里应该有个警告**
（`Documentation/devicetree/bindings/spi/spi-controller.yaml:63-70`
的 Note 1 / Note 2："Should print a warning about polarity inversion"），
而这个警告**不存在**：

```bash
grep -rni "polarity" drivers/spi/*.c | grep -i "warn\|dev_err\|dev_info"
# 空
```

完整的极性真值表和决策树在 `06-devicetree.md` §4。

### 这个类比什么时候会误导你？

1. **"纸条没用，所以可以随便写"——不行。**
   `spi-cs-high` 虽然不决定 GPIO 片选的电平，但它**确实改了
   `spi->mode`**，而 `spi->mode` 会被 tracepoint 打出来
   （`07-debug.md` §3.4 的 `, cs_high` 后缀）。**于是 trace 显示的
   和实际引脚上发生的不一致**——这会让你在排查时被自己的日志骗一次。
   结论：GPIO 片选就**只写 `cs-gpios` 的标志**，别写 `spi-cs-high`。

2. **"那 `spi-cs-high` 是个废属性"——不是。**
   原生片选（`reg` 指向控制器自己的片选引脚，DTS 里**没有** `cs-gpios`）
   时它是**唯一**的极性开关，`:1022` 用的就是被它翻转过的 `enable`。
   全 `arch/arm64/boot/dts/` 里只有一处用了它
   （`k3-am642-evm.dts:424-435`），而那正是原生片选，**所以确实生效**。

3. **"智能锁登记了方向，所以一定对"——gpiolib 也可能被骗。**
   `cs-gpios` 的标志位是**你在 DTS 里写的**，gpiolib 只是照着执行。
   写错了照样反。区别只在于：**写在 `cs-gpios` 里的会生效（可能错），
   写在 `spi-cs-high` 里的连生效的机会都没有。**

---

<a id="5"></a>
## 5. 我只想读 4 个字节，为什么内核还要往外发 4 个字节？

### 先举个例子

SPI 不是"对讲机"，是**两条并行的传送带**，由同一个马达（时钟）驱动。

马达一转，**两条带子同时动**：一条把你的东西送出去，一条把对方的东西送进来。
你想"只收不发"，物理上做不到——**马达一转，出去的那条带子上必然有东西**，
哪怕是空箱子。

所以"只读 4 字节"在 SPI 上的真实含义是：**转 32 个时钟，出去的内容我不在乎，
进来的 4 个字节我要。** 而"我不在乎"在代码里必须变成一个具体的缓冲区——
因为控制器的发送 FIFO 得有东西喂。这个"空箱子"就是 `dummy_tx`。

### 回到代码

`spi_map_msg()`，`drivers/spi/spi.c:1300-1348`：

```c
	if ((ctlr->flags & (SPI_CONTROLLER_MUST_RX | SPI_CONTROLLER_MUST_TX))
		&& !(msg->spi->mode & SPI_3WIRE)) {          /* :1306-1307 两个前提 */
		...
		if (max_tx) {
			tmp = krealloc(ctlr->dummy_tx, max_tx,
				       GFP_KERNEL | GFP_DMA | __GFP_ZERO);   /* :1321-1322 */
			ctlr->dummy_tx = tmp;
		}
		if (max_rx) {
			tmp = krealloc(ctlr->dummy_rx, max_rx,
				       GFP_KERNEL | GFP_DMA);                /* :1329-1330 */
			ctlr->dummy_rx = tmp;
		}
		if (max_tx || max_rx) {
			list_for_each_entry(xfer, &msg->transfers, transfer_list) {
				if (!xfer->len)
					continue;                    /* :1340 零长度段跳过 */
				if (!xfer->tx_buf)
					xfer->tx_buf = ctlr->dummy_tx;   /* :1342 替你填上 */
				if (!xfer->rx_buf)
					xfer->rx_buf = ctlr->dummy_rx;   /* :1344 */
			}
		}
	}
```

| 事实 | 依据 | 为什么重要 |
|-----|-----|----------|
| 只有控制器声明了 `MUST_TX` / `MUST_RX` 才分配 | `:1306` | **不是所有控制器都需要**。能自己产生空数据的控制器不设这两个标志，你的 `tx_buf` 就一直是 `NULL` |
| `SPI_3WIRE` 模式下**完全跳过** | `:1307` | 3wire 只有一根数据线，本来就是半双工，没有"同时"这回事 |
| `dummy_tx` 带 `__GFP_ZERO`，`dummy_rx` **不带** | `:1322` vs `:1330` | 发出去的是**全 0**（确定值，外设能预期）；收进来的垃圾**没人看**，不必清 |
| 两个都带 `GFP_DMA` | `:1322`/`:1330` | 要能直接做 DMA |
| 是 `ctlr` 级别的、`krealloc` 复用的**共享**缓冲 | `:1325`/`:1333` | 同一条总线上所有设备共用一块。所以它**不能**用来传有意义的数据 |
| 你的 `xfer->tx_buf` 被**原地改写**了 | `:1342` | 见下面的第 2 条 |

**核心自己也知道这是"假数据"**，所以在两个地方要把它们排除：

- **解映射时还原成 `NULL`**（`:1291`/`:1293`）——否则下次这条消息被复用会出错；
- **统计时不计入 `bytes_tx`/`bytes_rx`**（`:331`/`:334`），这就是
  `07-debug.md` §4.3 里 `bytes ≠ bytes_tx + bytes_rx` 的根源；
- **tracepoint 里不 dump**（`spi_valid_txbuf`/`spi_valid_rxbuf`，
  `include/trace/events/spi.h:174-177`，`07-debug.md` §3.5）。

**三处独立的排除逻辑指向同一个判断**：`xfer->tx_buf == ctlr->dummy_tx`。
这是"哑缓冲是核心的实现细节，不是你的数据"这个设计意图在代码里的三次落地。

### 这个类比什么时候会误导你？

1. **"传送带上必须有空箱子，所以内核总会分配哑缓冲"——不总会。**
   `:1306` 的前提是控制器设了 `MUST_TX`/`MUST_RX`。很多控制器
   （尤其是 PIO 的）自己往 FIFO 里写 0 就完了，不需要一块内存。
   所以**你不能假设 `xfer->tx_buf` 在传输后一定非空**。

2. **"空箱子是空的，所以发出去的是随机值"——tx 方向不是。**
   `__GFP_ZERO`（`:1322`）保证发出去的是 **0x00**。这对某些外设有实际意义：
   一些 SPI flash 用 `0x00` 作为 dummy 字节，如果发的是随机值可能被误解析。
   但 **rx 方向的哑缓冲确实是垃圾**（`:1330` 没有 `__GFP_ZERO`）——
   只是没人读它。

3. **"我设了 `tx_buf = NULL` 表示只读，核心会尊重"——它会改掉。**
   `:1342` 是**原地写你的 `spi_transfer`**。所以如果你把同一个
   `spi_transfer` 结构复用于多次传输，第二次它的 `tx_buf`
   可能已经指向 `dummy_tx` 了。核心在 `spi_unmap_msg()` 里会还原
   （`:1291`/`:1293`），但那只在走了映射路径时才发生。
   **复用 `spi_transfer` 结构前重新初始化，别指望核心帮你擦干净。**

---

<a id="6"></a>
## 6. `bits_per_word` 改成 16 之后，为什么 `len` 也得跟着改？

### 先举个例子

想象你在寄快递，快递公司规定"**只按整箱收，不拆箱**"。

- 一箱装 1 件的时候，你有 7 件就寄 7 箱，没问题。
- 换成一箱装 2 件之后，你还是 7 件——**寄不了**。要么 6 件（3 箱），
  要么 8 件（4 箱）。**7 是奇数，凑不出整箱。**

`bits_per_word` 就是"一箱装几件"，`len` 是"你有几件"。关键是
**`len` 的单位是字节，不是"字"**——所以两者之间有个换算，而这个换算
必须除得尽。

### 回到代码

`__spi_validate()` 里的三行（`drivers/spi/spi.c:3937-3946`）：

```c
		if (xfer->bits_per_word <= 8)
			w_size = 1;
		else if (xfer->bits_per_word <= 16)
			w_size = 2;
		else
			w_size = 4;

		/* No partial transfers accepted */
		if (xfer->len % w_size)
			return -EINVAL;                  /* :3946 零日志 */
```

| `bits_per_word` | `w_size`（字节） | `len` 必须是 |
|----------------|----------------|-------------|
| 1 – 8 | 1 | 任意 |
| 9 – 16 | 2 | **偶数** |
| 17 – 32 | 4 | **4 的倍数** |

**注意这个表是"向上取整到 1/2/4"，不是精确的位数换算**：
`bits_per_word = 12` 时 `w_size` 也是 2，也就是说 **12 位的字在内存里
占 2 个字节**（高 4 位空着），`len` 还是必须是偶数。

**而这条检查一句日志都没有**（`__spi_validate()` 整个函数零日志，
`07-debug.md` §5.2）。症状就是：把 `bits_per_word` 从 8 改成 16 之后，
原来跑得好好的代码开始返 `-EINVAL`，`dmesg` 干净。

**还有一层耦合容易漏**：`bits_per_word` 如果你没在 `xfer` 里填，
核心会用**设备级**的值（`:3921-3922`）：

```c
		if (!xfer->bits_per_word)
			xfer->bits_per_word = spi->bits_per_word;
```

所以**改 `spi->bits_per_word` 会影响所有没显式指定的段**。
一次 `spi_setup()` 就能让远处一段代码开始返 `-EINVAL`。

### 这个类比什么时候会误导你？

1. **"箱子装不满就报错"——不是装不满，是装不整。**
   `bits_per_word = 12`、`len = 2` 是合法的（1 个字，2 字节），
   虽然那 2 个字节里有 4 位没用。核心不检查"有没有浪费"，
   只检查 `len % w_size`（`:3946`）。

2. **"快递公司还规定了箱子尺寸"——是的，而且是另一道独立的检查。**
   `bits_per_word` 本身还要过控制器的 `bits_per_word_mask`
   （`__spi_validate_bits_per_word()`，`:3640`-`:3645`），
   这是**另一个** `-EINVAL`（`:3931`）。两道检查的失败症状一模一样，
   查法完全不同——见 `07-debug.md` §5.2.1。
   那里还有个反直觉的语义：**`bits_per_word_mask == 0` 表示"不限制"**，
   不是"什么都不支持"。

3. **"这是 SPI 协议的要求"——不是，是 Linux 的要求。**
   SPI 协议本身对一次会话传多少位没有任何约束（时钟转几下就是几位）。
   `w_size` 这个概念是**内核为了让驱动能按 `u8`/`u16`/`u32` 数组
   来处理缓冲区**而引入的软件约束。所以某些控制器驱动会绕过它
   （自己实现 `transfer_one` 里做拆包），但**核心这一关绕不过**。

---

<a id="7"></a>
## 7. 为什么不能像 I2C 那样扫一遍总线看挂了什么设备？

### 先举个例子

对比两种"喊人"的方式：

- **I2C 像在教室里点名。** 老师喊"**37 号在吗**"，所有人都听见了，
  37 号举手（ACK）。老师从 1 号喊到 127 号，就知道班里有谁。
  **关键是"举手"这个动作是协议规定的**——地址和应答都在同一根线上。
- **SPI 像给每个人单独接一根拉铃线。** 你要找谁，就拉谁的铃（片选），
  然后开始说话。**但对方不会应答"我在"**——它要么回你数据，要么
  什么都不回，而"什么都不回"在电气上和"回全 0"或"回全 1"没法区分。

所以在 SPI 上"扫总线"是这样的：你拉一根铃，说一句话，听到一串
`0xFF`。**这可能是"没人"，也可能是"有人但这条命令它不认"，
也可能是"有人而且答案真的是 0xFF"。** 你分不出来。

### 回到代码

这个协议差异直接体现在两个子系统的 `driver` 结构里：

| | SPI | I2C |
|---|-----|-----|
| 有 `detect` 回调吗 | **没有** | `int (*detect)(...)`，`include/linux/i2c.h:305` |
| 有"要试哪些地址"的列表吗 | **没有** | `address_list`，`include/linux/i2c.h:306` |

```bash
grep -n "detect" include/linux/spi/spi.h        # 空
```

所以 SPI 的匹配**纯粹是字符串比较**，一个字节都不往总线上发
（`spi_match_device()`，`drivers/spi/spi.c:375`）：

```c
	if (spi->driver_override)                          /* 1. 手工指定，最高优先 */
		return strcmp(spi->driver_override, drv->name) == 0;
	if (of_driver_match_device(dev, drv))              /* 2. DT compatible */
		return 1;
	if (acpi_driver_match_device(dev, drv))            /* 3. ACPI（arm64 不涉及） */
		return 1;
	if (sdrv->id_table)                                /* 4. id_table vs modalias */
		return !!spi_match_id(sdrv->id_table, spi->modalias);
	return strcmp(spi->modalias, drv->name) == 0;      /* 5. 兜底：驱动名 */
```

**五条路，全是 `strcmp`。** 于是"总线上有什么设备"这个信息只能来自
**你写的 DTS**——这就是为什么 SPI 的 DTS 必须把每个设备都列出来，
而 I2C 可以（在某些场合）少写点。

**一个直接的推论**：SPI 设备在 `/sys/bus/spi/devices/` 里出现，
**只证明 DTS 里写了它，不证明那颗芯片焊上了**。设备出现、驱动绑上、
`probe` 成功，芯片仍然可能不在板子上——只要驱动的 `probe` 没去读芯片 ID。
所以 `07-debug.md` §8 的第 0 步（`ls /sys/bus/spi/devices/`）
**只能证伪不能证实**。

### 这个类比什么时候会误导你？

1. **"所以 SPI 完全没法探测"——协议层没有，设备层经常有。**
   绝大多数 SPI 芯片自己提供一条"读 ID"命令（flash 的 `0x9F` JEDEC ID、
   很多传感器的 `WHO_AM_I` 寄存器）。但那是**驱动 probe 里做的事**，
   不是内核框架做的事——**每个驱动自己实现，各不相同**。
   `spi-nor` 就是靠这个来识别具体型号的（`04-extensions.md` §2）。

2. **"拉铃线各自独立，所以设备之间互不影响"——不成立。**
   MOSI/MISO/SCK 是**共享**的。一个设备如果片选没被正确释放
   （见主题 4 的极性问题），它会一直往 MISO 上驱动电平，
   **把整条总线上其他设备的读全部污染**。这就是
   `06-devicetree.md` §7 里"单设备配错 → 总线级症状"的成因。

3. **"点名 vs 拉铃只是形式差别"——它决定了热插拔能力。**
   I2C 可以在运行时通过 `new_device` sysfs 接口加设备并 detect。
   SPI 也有运行时创建设备的路径（`spi_add_device()`、
   `CONFIG_SPI_DYNAMIC`，`03-internals.md` §B10），但**你必须自己
   把片选号、速率、模式全部说清楚**——没有任何一步能靠"问一下硬件"。

---

<a id="8"></a>
## 8. 为什么 SPI 核心几乎不打日志？

### 先举个例子

对比两种记账方式：

- **写日记**：每发生一件事写一行。信息全，但**如果一天发生十万件事，
  日记本本身就成了灾难**——写日记的时间超过做事的时间，而且你翻不完。
- **记计数器**：墙上挂一排计数器（今天成功几次、失败几次、超时几次），
  每件事拨一下。**信息少了，但代价近乎为零，而且随时能看总量。**

SPI 传输是典型的高频路径——一个 1kHz 采样的 ADC，一秒钟一千条消息。
在这条路上写日记，**日志本身会成为性能问题，而且会把 dmesg 冲掉**。
所以 SPI 核心的选择是：**热路径上只记计数器，日志留给注册路径。**

### 回到代码

这个取向在数字上非常清楚（`07-debug.md` §2.1）：

| | 数量 | 分布 |
|---|-----|-----|
| `dev_err` | 42 | **绝大多数在注册 / 校验 / 参数设置路径**（`spi_setup`、`spi_register_controller`、`of_spi_parse_dt`） |
| `dev_dbg` | **4** | 整个 `spi.c` 只有 4 条，见 `07-debug.md` §1.3 |
| `dev_err_ratelimited` | **0** | 一条都没有——因为热路径干脆不打 |
| `__spi_validate()` 里的日志 | **0** | `:3850`-`:3995`，约 20 个 `-EINVAL` 出口，一条不打 |
| 每设备 / 每控制器的统计项 | **各 30 项** | `07-debug.md` §4.3 |

```bash
sed -n '3845,3995p' drivers/spi/spi.c | grep -c "dev_err\|dev_warn\|dev_dbg"
# 0
```

**"计数器代替日记"落实到了什么程度**：统计用的是 per-CPU 的
`u64_stats`（`pcpu_statistics`），累加时连锁都不加——这就是为了让
它在热路径上的代价可以忽略。

**注意这是一个真实的权衡，不是纯粹的优点。** 它的代价就是
`07-debug.md` §5.2 那一整节的存在理由：**`__spi_validate()` 拒绝你的传输时，
你得不到任何提示。** 内核选择了"快"，把"可诊断"这件事外包给了：

1. **统计计数器**（知道"发生了多少次"）；
2. **tracepoint**（知道"发生了什么"，而且可以按需开关，`07-debug.md` §3）；
3. **`-DDEBUG` / dynamic_debug**（需要时才打开，`07-debug.md` §1.4）。

**三者合起来才补上那 20 个静默出口。** 这也是为什么 07 篇的组织顺序是
"开关 → 日志 → tracepoint → 统计 → 错误码"——**越往后越是这个设计
留下的缺口**。

### 这个类比什么时候会误导你？

1. **"不打日志是为了性能，所以打了日志的地方都是慢路径"——大致对，但有例外。**
   `:1393-1394` 的 `SPI transfer timed out` 就在热路径上，而且是
   **无条件 `dev_err`**。理由很直白：能超时说明这条总线已经废了
   （`07-debug.md` §6.1 说了预算有 2 倍余量），**这时候刷日志的代价
   已经不重要了**。

2. **"计数器等价于日志的聚合"——丢了两样东西。**
   计数器没有**时间信息**（不知道什么时候发生的）和**上下文**
   （不知道是哪条消息、哪个调用者）。所以 `errors` 从 3 涨到 4 时，
   你知道"又错了一次"，但不知道错在哪——必须开 tracepoint 重现。

3. **"`_once` 也是为了省开销，所以和计数器一个道理"——不是。**
   `dev_err_once`（`:1493`）省的不是**性能**，是**噪声**：它假定
   "这类配置错误说一次就够了"。但它的副作用是
   **第一个触发的设备打完，后面所有设备都不会再打**
   （`07-debug.md` §2.3）——这不是聚合，是**丢失**。
   要数次数只能回去看计数器。

---

## §9 读完这篇之后去哪

| 你现在想做什么 | 去哪一篇 |
|--------------|---------|
| 想按顺序把整个子系统过一遍 | `00-overview.md`（复习入口，隔几个月回来只读它） |
| 要写代码，找 API | `01-api.md` |
| 想看主题 1 里三层设备关系的完整图 | `02-datastructures.md` §1.0 和 §2 |
| 想看主题 2/3 在完整时间线上的位置 | `03-internals.md` Part A |
| 想知道主题 5 的哑缓冲在拆分/映射流程里的位置 | `03-internals.md` §B3.1、§B4 |
| 想看 spi-mem / spi-mux / loopback-test 这些子框架 | `04-extensions.md` |
| 要写一个设备驱动或控制器驱动 | `05-usage.md` |
| 主题 4 的完整极性真值表和决策树 | `06-devicetree.md` §4 |
| 主题 6 和主题 8 提到的"静默 `-EINVAL`"逐条查法 | `07-debug.md` §5.2 |
| 想知道这些结论各自的证据等级 | `99-evidence.md` |

**如果只记一件事**：这一篇里出现四次的那个模式——
**"配置了、被接受了、但不生效，而且不报错"**（主题 4 的 `spi-cs-high`、
主题 5 的 `tx_buf` 被改写、主题 6 的 `len` 检查、主题 8 的 20 个静默出口）
——是 SPI 子系统最主要的踩坑来源。**遇到"我明明配了"的时候，
先怀疑"它是不是根本没被读"。**

---
