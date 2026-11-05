---
title: 数据结构
---

# IIO 数据结构与关系

> 基线：Linux 6.1.158（`f6e38ae624cf`），本文所有行号取自该版本。
> 目标架构 arm64。裁掉的路径记在 `99-evidence.md` §2.7。

## 篇首格式约定

- 结构体字段表固定四列：`字段 / 行号 / 含义 / 谁写谁读`。「谁写谁读」是本篇的主线——IIO 的结构体绝大多数是三方共享的（核心 / 驱动 / 用户态），不写清所有权就没法判断哪个字段能碰。
- 行号一律带文件名。同名文件（内核树里 `iio.h`、`buffer.h`、`types.h` 都不止一个）写全路径。
- ASCII 图里的 `──▶` 表示指针指向，`═══` 表示内存内嵌（同一块分配），`⋯⋯` 表示只有名字上的关联、运行期没有指针。
- 「为什么不会发生」的否定性论断也给行号或给可重跑的命令，不留空口断言。

---

## §1.0 先分清五组关系

IIO 是内核里少见的「命名极其对称、语义完全不对称」的子系统。下面五组关系，每一组都有一个很自然但错误的类比。**先把错的否掉，再看后面的图，否则图会被读成另一个意思。**

### 关系一：`iio_dev` 和 `iio_dev_opaque` —— 谁包含谁

**错误类比：** 「`iio_dev_opaque` 是 `iio_dev` 里的一个私有指针，像 `struct device` 的 `driver_data` 那样挂在旁边。」

**实际是反的。** `struct iio_dev` 是 `struct iio_dev_opaque` 的**第一个成员**，两者在同一块分配里，起始地址相同：

```c
/* include/linux/iio/iio-opaque.h:41-42 */
struct iio_dev_opaque {
	struct iio_dev			indio_dev;
	int				currentmode;
	...
```

```
一次 kzalloc 分配的内存：
┌──────────────────────────────────────────────────┐
│ struct iio_dev_opaque                            │
│ ┌──────────────────────────────┐                 │
│ │ struct iio_dev indio_dev     │ ← 驱动拿到的指针 │
│ │  （公开契约，iio.h:571-596） │   就是这个地址   │
│ └──────────────────────────────┘                 │
│   currentmode / id / driver_module               │
│   info_exist_lock / event_interface              │
│   attached_buffers[] / buffer_list / ...         │ ← 核心私有
│   （iio-opaque.h 的 :43 到 :73）                 │
└──────────────────────────────────────────────────┘
 ▲
 └── 两个地址相同：to_iio_dev_opaque() 是 container_of，
     偏移为 0，编译后是一条空操作
```

转换宏两个方向都存在，但形态不同：

| 方向 | 手段 | 位置 |
| --- | --- | --- |
| `iio_dev` → `iio_dev_opaque` | `to_iio_dev_opaque()` 宏，`container_of` | `include/linux/iio/iio-opaque.h:77-78` |
| `iio_dev_opaque` → `iio_dev` | 直接取 `&opaque->indio_dev` | 核心代码里到处是，无宏 |

**为什么这个设计存在：** 让驱动只能看见 `iio.h` 里那 19 个字段（见 §2.1）。核心想加字段、想改私有状态的表示，不必动公开头文件、不必重编所有驱动。**代价是驱动不能自己 `kzalloc(sizeof(struct iio_dev))`** —— 那样分配出来的对象后面没有 opaque 部分，核心第一次访问 `currentmode` 就越界。这就是为什么 `iio_device_alloc()` 是唯一合法的构造入口（见 `01-api.md` §1）。

**为什么"驱动私自分配"不会悄悄跑通：** `iio_device_alloc()` 之外没有第二条构造路径 —— 全树 `driver_module` 字段的赋值只在核心里，且 `iio_device_register()` 的宏形态强制走 `__iio_device_register(indio_dev, THIS_MODULE)`（`include/linux/iio/iio.h`，见 `01-api.md` §8.1）。私自分配的对象在 `iio_device_register()` 里写 `iio_dev_opaque->driver_module` 时就已经踩到了自己栈上或堆上的别的东西。

### 关系二：`iio_bus_type` 和真正的父总线 —— IIO 不是一条会 probe 的总线

**错误类比：** 「IIO 有 `iio_bus_type`，所以和 i2c 一样：设备注册到 IIO 总线上，然后 IIO 驱动被匹配上来 probe。」

**IIO 总线是退化的。** 它只填了名字：

```c
/* drivers/iio/industrialio-core.c:42-45 */
static struct bus_type iio_bus_type = {
	.name = "iio",
};
```

没有 `.match`、没有 `.probe`、没有 `.uevent`。整个 `drivers/iio/` 里 **`driver_register()` 出现 0 次、`struct device_driver` 定义 0 次**：

```bash
grep -rn --include=*.c --include=*.h 'driver_register\|struct device_driver' drivers/iio/ | wc -l   # → 0
```

传感器驱动是 **i2c/spi/platform 驱动**，`iio_dev` 只是它们在 probe 里额外创建的一个对象：

```
              ┌────────────────────────────────────────────┐
   真正做匹配 │  i2c_bus_type / spi_bus_type /             │
   和 probe   │  platform_bus_type                        │
   的总线     │      │                                     │
              │      │ .probe → 传感器驱动的 probe()      │
              └──────┼─────────────────────────────────────┘
                     │
                     │ iio_device_alloc(dev) 里
                     │ indio_dev->dev.parent = dev
                     ▼
              ┌────────────────────────────────────────────┐
   只做命名   │  iio_bus_type                              │
   和查找     │   ├─ iio:deviceN     ┐                     │
   的"总线"   │   ├─ triggerN        │ 四类注册者          │
              │   ├─ iio_evgen       │ 行号见下表          │
              │   └─ iio_sysfs_trig  ┘                     │
              └────────────────────────────────────────────┘
                     ▲
                     │ 唯一的功能性用途：按 fwnode 反查设备
                     │ bus_find_device_by_fwnode(&iio_bus_type, ...)
                     │ drivers/iio/inkern.c:158
```

四个 `.bus = &iio_bus_type` 赋值点：

| 注册的设备 | `.bus` 赋值点 |
| --- | --- |
| `iio:deviceN` | `drivers/iio/industrialio-core.c:1685` |
| `triggerN` | `drivers/iio/industrialio-trigger.c:563` |
| `iio_evgen`（仅 dummy 模块） | `drivers/iio/dummy/iio_dummy_evgen.c:194` |
| `iio_sysfs_trig` | `drivers/iio/trigger/iio-trig-sysfs.c:87` |

四个赋值点、零个驱动、一个查找调用点。**所以 `iio_bus_type` 的作用是"命名空间 + fwnode 反查索引"，不是绑定机制。**

对比表：

| 问题 | i2c 设备 | IIO 设备（`iio:deviceN`） |
| --- | --- | --- |
| 是 `struct device` 吗 | 是 | 是（`iio.h:573`，内嵌不是指针） |
| 注册在哪条总线 | `i2c_bus_type` | `iio_bus_type` |
| 会被匹配上一个 driver 吗 | 会 | **不会**（IIO 侧 0 个 driver） |
| 谁调用它的 probe | i2c 核心 | **没有 probe** |
| 父设备是谁 | i2c adapter | 创建它的那个 i2c/spi/platform 设备 |
| DT 节点由谁转成设备 | i2c 核心扫子节点 | 已经是 i2c/spi/platform 设备，IIO 不扫 DT 节点 |

**为什么"IIO 驱动被 probe"这件事不会发生：** 没有 `.match` 回调时，`driver_match_device()` 走到 `bus->match` 为 NULL 的分支直接返回 1（匹配一切）—— 但这条路径要求先有 driver 被 `driver_register()` 到这条总线上，而上面那条 grep 是 0。**没有 driver，`bus_for_each_drv` 遍历的是空链表。**

### 关系三：`iio_trigger` 和中断 —— 触发器是一个虚拟 irq_chip

**错误类比：** 「触发器就是把一个硬件中断包装了一下，`iio_trigger_poll()` 相当于 `handle_irq()`。」

**触发器是一个自己申请了一段 irq 描述符号段的虚拟 irq_chip。** 一个触发器可以同时驱动 `CONFIG_IIO_CONSUMERS_PER_TRIGGER`（默认 2）个消费者，每个消费者独占一个 subirq：

```c
/* include/linux/iio/trigger.h:71-75，中间 :73 是空行 */
	struct irq_chip			subirq_chip;
	int				subirq_base;

	struct iio_subirq subirqs[CONFIG_IIO_CONSUMERS_PER_TRIGGER];
	unsigned long pool[BITS_TO_LONGS(CONFIG_IIO_CONSUMERS_PER_TRIGGER)];
```

```
     真实硬件中断（可选，有的触发器根本没有）
             │
             ▼
     ┌───────────────────────────────────────┐
     │ struct iio_trigger                    │
     │  subirq_base ──┐                      │
     │  subirqs[0..N-1]  pool 位图（谁占用了）│
     └────────────────┼──────────────────────┘
                      │ irq_base + i
        ┌─────────────┴─────────────┐
        ▼                           ▼
   subirq 0                    subirq 1
   （消费者 A 的 pollfunc）     （消费者 B 的 pollfunc）
   request_threaded_irq         request_threaded_irq
```

所以「一个触发器 → 多个设备同时采样」是这个设计的目的，而不是副作用。两个投递入口的差别是上下文：

| 入口 | 底层调用 | 允许的上下文 | 位置 |
| --- | --- | --- | --- |
| `iio_trigger_poll()` | `generic_handle_irq()` | 硬中断上下文 | `drivers/iio/industrialio-trigger.c` |
| `iio_trigger_poll_chained()` | `handle_nested_irq()` | 线程/进程上下文 | 同上 |

**类比会误导的地方**在于：真实 irq_chip 的 mask/unmask 对应硬件寄存器写入，而 IIO 的 subirq_chip 的 mask/unmask 只是改 `subirqs[i].enabled` 这个 bool。触发器的"关闭"最终靠 `iio_trigger_ops.set_trigger_state()` 回调传到硬件（`trigger.h:33`），不走 irq_chip。

### 关系四：缓冲区通路和事件通路 —— 两条互不相干的 fd

**错误类比：** 「事件和缓冲区都是从设备读数据，应该是一个队列的两种读法。」

两条通路从数据结构到 ABI 完全分离，只共享 `iio:deviceN` 这个 chrdev 作为 ioctl 入口：

| 维度 | 缓冲区通路 | 事件通路 |
| --- | --- | --- |
| 队列结构 | `struct iio_buffer`，后端可插拔 | `struct iio_event_interface`，固定 16 深 kfifo |
| 结构定义 | `include/linux/iio/buffer_impl.h:81-139` | `drivers/iio/industrialio-event.c:36-45` |
| 元素类型 | 驱动自定义的 scan 布局（变长） | `struct iio_event_data`，固定 16 字节 |
| 取 fd 的 ioctl | `IIO_BUFFER_GET_FD_IOCTL` | `IIO_GET_EVENT_FD_IOCTL` |
| ioctl 码 | `_IOWR('i', 0x91, int)`（`include/uapi/linux/iio/buffer.h:8`） | `_IOR('i', 0x90, int)`（`include/uapi/linux/iio/events.h:27`） |
| 一个设备能有几个 | 多个（`attached_buffers[]`） | 一个（`event_interface` 是单指针，`iio-opaque.h:49`） |
| 满了怎么办 | 丢新数据 / 覆盖，看后端 | 丢弃并置 flag |

**为什么不能把事件当成一个缓冲区读：** `iio_event_interface` 里没有 `struct iio_buffer_access_funcs`，它的 `read` 是 `iio_event_chrdev_read()` 直接操作 kfifo，不经过缓冲区层的 demux。

### 关系五：`iio_chan_spec` 和 `iio_channel` —— 名字差一个词，方向相反

这是本子系统最容易混的一对近邻符号：

| | `struct iio_chan_spec` | `struct iio_channel` |
| --- | --- | --- |
| 定义位置 | `include/linux/iio/iio.h:238-269` | `include/linux/iio/consumer.h:24-28` |
| 字段数 | 22 | 3 |
| 谁构造 | **提供方**驱动，静态数组 | **消费者**侧，由 `iio_channel_get()` 等分配 |
| 语义 | 「我这个通道长什么样」的描述 | 「我引用了别人的某个通道」的句柄 |
| 生命周期 | 通常 `static const`，与驱动模块同寿 | 消费者 get/release 配对管理 |
| 内容 | 类型、编号、scan 布局、info 掩码 | `indio_dev` + `channel`（指向前者）+ `data` |

```c
/* include/linux/iio/consumer.h:24-28 */
struct iio_channel {
	struct iio_dev *indio_dev;
	const struct iio_chan_spec *channel;   /* ← 指向提供方的 iio_chan_spec */
	void *data;
};
```

`iio_channel` 是「`iio_dev` + `iio_chan_spec` 的二元组」，本身不含任何通道属性。**错用的后果：** 把 `iio_channel *` 传给期待 `iio_chan_spec *` 的函数不会编译失败吗——会，两者类型不同。真正的坑是**读文档时**把 `chan->channel` 的两层含义搞混：`iio_chan_spec.channel` 是一个 `int` 编号（`iio.h:240`），`iio_channel.channel` 是一个指针。同名不同类型，出现在同一段代码里就极易看错。

---

## §1.1 本子系统有几层可插拔

| 层 | ops 结构 | 谁填 | 实现者位置 | 赋值点 | 文件 |
| --- | --- | --- | --- | --- | --- |
| 设备操作层 | `struct iio_info`（`iio.h:445-518`） | 每个传感器驱动 | 各设备类目录；按赋值点 `adc/` 123、`light/` 62、`dac/` 39、`accel/` 38 | 421 | 387 |
| 缓冲区后端层 | `struct iio_buffer_access_funcs`（`buffer_impl.h:53-73`） | 缓冲区实现 | 全在 `drivers/iio/buffer/` | 4 | 4 |
| 触发器提供方层 | `struct iio_trigger_ops`（`trigger.h:32-37`） | 触发器驱动或传感器自身 | `drivers/iio/trigger/` 只占 4 个赋值点，其余散在 12 个设备类目录（`accel/` 13、`adc/` 11、`gyro/` 6…），完整分布见 `04-extensions.md` §4.0 | 53 | 52 |
| 缓冲区生命周期钩子层 | `struct iio_buffer_setup_ops`（`iio.h:530-537`） | 传感器驱动（可选，NULL 时核心替默认值） | 各设备类目录；按赋值点 `adc/` 18、`accel/` 12、`light/` 9 | 63 | 63 |
| 软件对象层 | `iio_sw_trigger_ops` / `iio_sw_device_ops` | configfs 类型注册者 | `trigger/` 2 个 + `dummy/` 1 个 | 2 / 1 | 2 / 1 |

重跑命令。口径是「`struct X 名字 = {` 这一形式的赋值点数」和「含这种赋值点的文件数」两列，不是别的：

```bash
for s in iio_info iio_buffer_access_funcs iio_trigger_ops iio_buffer_setup_ops \
         iio_sw_trigger_ops iio_sw_device_ops; do
  printf '%-24s 赋值点 %-5s 文件 %s\n' "$s" \
    "$(grep -rnE --include=*.c "struct $s [a-z_0-9]+ = \{" drivers/ | wc -l)" \
    "$(grep -rlE --include=*.c "struct $s [a-z_0-9]+ = \{" drivers/ | wc -l)"
done
```

⚠️ **字符类里必须有 `0-9`。** 少写这三个字符，`hts221_trigger_ops`、`ad7606_trigger_ops` 这类带型号数字的名字全部漏掉，五行数字会同时缩水到 64 / 4 / 13 / 19 / 2·1——本篇初稿发布的就是那一组。机理登记在 `99-evidence.md` §6.1 A 与 Q。

⚠️ `iio_buffer_access_funcs` 的 4 个实现里，**没有一个填满全部 12 个函数指针**：

| 后端 | vtable 位置 | 填了几个回调 | `modes` | `flags` |
| --- | --- | --- | --- | --- |
| kfifo | `drivers/iio/buffer/kfifo_buf.c:188-201` | 10 | SOFTWARE\|TRIGGERED | — |
| dmaengine | `drivers/iio/buffer/industrialio-buffer-dmaengine.c:113-125` | 8 | HARDWARE | FIXED_WATERMARK |
| 回调缓冲（cb） | `drivers/iio/buffer/industrialio-buffer-cb.c:41-46` | 2（`store_to` + `release`） | SOFTWARE\|TRIGGERED | — |
| hw-consumer | `drivers/iio/buffer/industrialio-hw-consumer.c:47-50` | **1**（只有 `release`） | HARDWARE | — |

hw-consumer 只填一个回调是合理的：它的缓冲区从不搬运数据，只是用「有一个 HARDWARE 模式的缓冲区被 attach 了」这个事实去让提供方进入采样状态，数据在别处（提供方的 DMA / 下游 ADC）流走。每个回调什么时候被调、少填会不会崩，在 `04-extensions.md` 里逐个展开。

---

## §2 核心结构体逐字段

### §2.1 `struct iio_dev` —— 驱动与核心的公开契约

`include/linux/iio/iio.h:571-596`，共 19 个字段。这是唯一驱动可以直接读写的 IIO 结构体，但**能写的只有其中一部分**：

| 字段 | 行号 | 含义 | 谁写谁读 |
| --- | --- | --- | --- |
| `modes` | 572 | 支持的操作模式位掩码（`iio.h:367-372`） | 驱动写，核心读 |
| `dev` | 573 | 内嵌的 `struct device`，**不是指针** | 驱动只填 `.parent`，其余核心写 |
| `buffer` | 575 | 主缓冲区（索引 0 那个） | 核心写，驱动别碰 |
| `scan_bytes` | 576 | 使能通道打包后的字节数 | 核心算，驱动读（推数据时要用） |
| `mlock` | 577 | 模式互斥锁，见 §6.1 | 核心持有；驱动只能经 `claim/release_direct_mode` |
| `available_scan_masks` | 579 | 硬件只支持的若干种通道组合 | 驱动写（可选 NULL），核心读 |
| `masklength` | 580 | 掩码有效位数 | **核心写**（驱动写了会被覆盖） |
| `active_scan_mask` | 581 | 当前使能的通道 | 核心写，驱动读 |
| `scan_timestamp` | 582 | 当前是否要追加时间戳 | 核心写，驱动读 |
| `trig` | 583 | 当前绑定的触发器 | 核心写；驱动读时须先判空 |
| `pollfunc` | 584 | 缓冲区通路的 pollfunc | 装配函数写 |
| `pollfunc_event` | 585 | 事件通路的 pollfunc | 装配函数写 |
| `channels` | 587 | 通道描述数组 | 驱动写，核心读 |
| `num_channels` | 588 | 数组长度 | 驱动写，核心读 |
| `name` | 590 | 设备名（`/sys/.../name`） | 驱动写 |
| `label` | 591 | 设备标签（可由 DT `label` 属性来） | 驱动或核心写 |
| `info` | 592 | 设备操作 vtable | 驱动写，核心读 |
| `setup_ops` | 593 | 缓冲区生命周期钩子；NULL 时核心替 `noop_ring_setup_ops`（`industrialio-core.c:1924`，替换动作在 `:1980-1981`） | 驱动写（可选） |
| `priv` | 595 | 驱动私有区起点 | 只能经 `iio_priv()` 访问 |

**注意 `modes` 是驱动写的"能力声明"，不是"当前状态"。** 当前状态在 opaque 里的 `currentmode`，驱动只能通过 `iio_device_get_current_mode()` 读（见 §2.2）。把两者搞混是最常见的错误——`modes` 在整个生命周期里不变，`currentmode` 每次缓冲区使能/关闭都变。

`priv` 的实际布局：

```
iio_device_alloc(parent, sizeof_priv) 一次分配：
┌───────────────────────┬─────────┬──────────────────────┐
│ struct iio_dev_opaque │ 对齐填充 │ 驱动私有区 sizeof_priv│
│ （含 iio_dev 在开头） │         │                      │
└───────────────────────┴─────────┴──────────────────────┘
                                   ▲
                                   └── iio_priv(indio_dev) 返回这里
```

对齐用的是 `IIO_DMA_MINALIGN`（`iio.h:723`，等于 `ARCH_KMALLOC_MINALIGN`）。头文件在这个宏上方有 5 行注释解释原因：私有区里常放要做 DMA 的缓冲，如果它和核心结构共享一条 cacheline，在非一致性 DMA 的平台上会被 cache 维护操作破坏。**arm64 属于可能非一致性的平台，所以这条对齐在本架构上是实打实生效的，不是纸面条款。**

### §2.2 `struct iio_dev_opaque` —— 核心私有状态

`include/linux/iio/iio-opaque.h:41-75`。驱动看不到定义（头文件不在 `include/linux/iio/iio.h` 的包含链上，驱动要显式 include 才能看到），列在这里是为了读核心代码时能对上。

| 字段 | 行号 | 含义 |
| --- | --- | --- |
| `indio_dev` | 42 | 内嵌的公开部分，见 §1.0 关系一 |
| `currentmode` | 43 | 当前操作模式；kerneldoc 明确写 "should be considered read-only as this is a core internal bit" |
| `id` | 44 | `iio_ida` 分出来的编号，即 `iio:deviceN` 里的 N |
| `driver_module` | 45 | 提供方模块，由 `iio_device_register()` 宏塞入 `THIS_MODULE` |
| `mlock_key` | 46 | 给 `mlock` 用的 lockdep class key，让不同设备的 mlock 在 lockdep 里不互相报环 |
| `info_exist_lock` | 47 | 保护 `indio_dev->info` 指针本身（拆除时置 NULL），见 §6.1 |
| `trig_readonly` | 48 | 触发器被驱动锁死、用户态不许改 |
| `event_interface` | 49 | 事件队列，单个；没有事件时为 NULL |
| `attached_buffers` | 50 | 缓冲区指针数组 |
| `attached_buffers_cnt` | 51 | 数组长度，决定生成几个 `bufferN` 目录 |
| `buffer_ioctl_handler` | 52 | `IIO_BUFFER_GET_FD_IOCTL` 的处理器 |
| `buffer_list` | 53 | **当前已使能**的缓冲区链表（≠ attached） |
| `channel_attr_list` | 54 | 自动合成出来的通道属性 |
| `chan_attr_group` | 55 | 上一行打包成的 attribute_group |
| `ioctl_handlers` | 56 | chrdev ioctl 处理器链 |
| `groups` | 57 | 交给 driver core 的 attribute_group 数组 |
| `groupcounter` | 58 | 上一行已填了几个 |
| `legacy_scan_el_group` | 59 | 兼容用的 `scan_elements/` 目录 |
| `legacy_buffer_group` | 60 | 兼容用的 `buffer/` 目录 |
| `bounce_buffer` | 61 | `iio_push_to_buffers_with_ts_unaligned()` 的中转区 |
| `bounce_buffer_size` | 62 | 上一行的容量 |
| `scan_index_timestamp` | 64 | 时间戳通道的 scan_index |
| `clock_id` | 65 | 时间戳时钟源，默认 `CLOCK_REALTIME`，用户态可改 |
| `chrdev` | 66 | `/dev/iio:deviceN` |
| `flags` | 67 | 目前只用了 `IIO_BUSY_BIT_POS`（独占打开标志） |
| `debugfs_dentry` / `cached_reg_addr` / `read_buf[20]` / `read_buf_len` | 70-73 | `CONFIG_DEBUG_FS` 才有，寄存器访问接口的状态 |

⚠️ **`buffer_list` 和 `attached_buffers[]` 的区别是本篇最容易看错的一处。** `attached_buffers[]` 是「这个设备一共挂了几个缓冲区」（`bufferN` 目录数），`buffer_list` 是「这些缓冲区里现在有几个开着」。使能时 `iio_buffer_activate()` 把缓冲区加进 `buffer_list` 并加一次引用（`industrialio-buffer.c:732-739`），关闭时 `iio_buffer_deactivate()` 摘出来、唤醒等待者、放引用（`:741-747`）。

### §2.3 `struct iio_chan_spec` —— 通道描述

`include/linux/iio/iio.h:238-269`，22 个字段。这是 IIO 里信息密度最高的结构：**sysfs 里绝大多数文件名是从这个结构算出来的，不是驱动写死的。**

| 字段 | 行号 | 含义 | 影响什么 |
| --- | --- | --- | --- |
| `type` | 239 | 物理量类型（`IIO_VOLTAGE` / `IIO_ACCEL` / …） | 属性名前缀（`in_voltage` / `in_accel`） |
| `channel` | 240 | 通道编号 | `indexed=1` 时拼进名字（`in_voltage3_raw`） |
| `channel2` | 241 | 第二编号或修饰符 | `modified=1` 时是修饰符（`in_accel_x_raw`）；`differential=1` 时是负端编号 |
| `address` | 242 | 驱动自用（常放寄存器地址） | 核心完全不解释 |
| `scan_index` | 243 | 在 scan 缓冲里的排序键；`<0` 表示不进缓冲 | 决定字节布局顺序，见 §3.1 |
| `scan_type.sign` | 245 | `'s'` 或 `'u'` | `*_type` 属性 |
| `scan_type.realbits` | 246 | 有效位数 | 同上 |
| `scan_type.storagebits` | 247 | 占位位数（含填充） | 同上；**同时决定对齐** |
| `scan_type.shift` | 248 | 掩码前要右移多少 | 同上 |
| `scan_type.repeat` | 249 | 一个通道重复几个样本 | `>1` 时 `*_type` 格式多一段 `X%d` |
| `scan_type.endianness` | 250 | `IIO_CPU` / `IIO_BE` / `IIO_LE` | `*_type` 前缀；`IIO_CPU` 在输出时被换成实际字节序（`industrialio-buffer.c:370-376`） |
| `info_mask_separate` | 252 | 每通道独立的属性 | 生成 `in_voltage0_scale` 这类 |
| `info_mask_separate_available` | 253 | 上一行的 `_available` 版 | 生成 `..._available` |
| `info_mask_shared_by_type` | 254 | 同 type 共享 | 生成 `in_voltage_scale` |
| `info_mask_shared_by_type_available` | 255 | — | — |
| `info_mask_shared_by_dir` | 256 | 同方向共享 | 生成 `in_scale` |
| `info_mask_shared_by_dir_available` | 257 | — | — |
| `info_mask_shared_by_all` | 258 | 全设备共享 | 生成 `scale` |
| `info_mask_shared_by_all_available` | 259 | — | — |
| `event_spec` | 260 | 事件描述数组 | 生成 `events/` 下的文件 |
| `num_event_specs` | 261 | 上一行长度 | — |
| `ext_info` | 262 | 驱动自定义属性（`iio_chan_spec_ext_info`，`iio.h:46-55`） | 直接给名字，不走合成 |
| `extend_name` | 263 | 名字里插一段 | **已被 `label` 取代，新代码别用** |
| `datasheet_name` | 264 | 数据手册上的名字 | `iio_map` 匹配的键，见 §2.8 |
| `modified` | 265 | 位域，用 `channel2` 当修饰符 | 见上 |
| `indexed` | 266 | 位域，名字里带 `channel` 编号 | 见上 |
| `output` | 267 | 位域，输出通道 | 前缀 `out_` 而不是 `in_` |
| `differential` | 268 | 位域，差分通道 | 名字变 `in_voltage0-voltage1_raw` |

**八个 info 掩码是四档 × 两版，不是八个独立开关。** 四档决定"这个属性放在名字的哪一级共享"，`_available` 版决定"要不要再生成一个列取值范围的只读文件"。掩码里的位是 `enum iio_chan_info_enum`（`iio.h` 前部），一位一个属性名。

### §2.4 `struct iio_info` —— 设备操作 vtable

`include/linux/iio/iio.h:445-518`：2 个 `attribute_group` 指针 + **16 个回调**，共 18 个成员。

| 成员 | 行号 | 何时被调 | 必需性 |
| --- | --- | --- | --- |
| `event_attrs` | 446 | 注册时并入 sysfs | 可选 |
| `attrs` | 447 | 同上（设备级自定义属性） | 可选 |
| `read_raw` | 449-453 | 读 `*_raw` / `*_scale` 等 | **几乎必需** |
| `read_raw_multi` | 455-460 | 一次返回多个值（最多 `INDIO_MAX_RAW_ELEMENTS`=4，`iio.h:382`） | 可选 |
| `read_avail` | 462-467 | 读 `*_available` | 有 `_available` 掩码时必需 |
| `write_raw` | 469-473 | 写属性 | 有可写属性时必需 |
| `read_label` | 475-477 | 读 `*_label` | 可选 |
| `write_raw_get_fmt` | 479-481 | 问「写进来的字符串按什么格式解析」 | 可选，默认 `IIO_VAL_INT_PLUS_MICRO` |
| `read_event_config` | 483-486 | 读事件使能状态 | 有事件时必需 |
| `write_event_config` | 488-492 | 写事件使能 | 同上 |
| `read_event_value` | 494-498 | 读阈值等 | 有阈值类事件时必需 |
| `write_event_value` | 500-504 | 写阈值 | 同上 |
| `validate_trigger` | 506-507 | 用户态换触发器时校验 | 可选；不填等于接受任何触发器 |
| `update_scan_mode` | 508-509 | 使能的通道组合变了 | 需要按组合重配硬件时必需 |
| `debugfs_reg_access` | 510-512 | debugfs 寄存器读写 | 可选，见 `07-debug.md` |
| `fwnode_xlate` | 513-514 | 把 DT 里的 `io-channels` 参数翻成通道号 | 可选；不填走 `__fwnode_iio_simple_xlate()`（`inkern.c:129`） |
| `hwfifo_set_watermark` | 515 | 设硬件 FIFO 水位 | 有硬件 FIFO 时 |
| `hwfifo_flush_to_buffer` | 516-517 | 把硬件 FIFO 冲进软件缓冲 | 同上 |

**这张 vtable 没有一个字段是核心强制检查非空的**，`iio_device_register()` 只要求 `info` 指针本身非空。缺回调的后果是运行期读某个属性时返回 `-EINVAL`，注册阶段不报错——**所以"掩码里点了位但没实现回调"是一类只有实际读一遍 sysfs 才能发现的错误**（定位方法见 `07-debug.md`）。

### §2.5 `struct iio_buffer` 与后端 vtable

`include/linux/iio/buffer_impl.h:81-139`。这个结构由**后端分配**（后端把它内嵌在自己的大结构里，用 `container_of` 反查），核心只填其中的公共字段：

| 字段 | 行号 | 含义 | 谁写 |
| --- | --- | --- | --- |
| `length` | 83 | 容量（样本数） | 用户态经 `length` 属性 → 后端 `set_length` |
| `flags` | 86 | 目前只有 `INDIO_BUFFER_FLAG_FIXED_WATERMARK` | 后端在 vtable 里声明，非此字段 |
| `bytes_per_datum` | 89 | 一个样本几字节 | 核心算（= `scan_bytes`） |
| `direction` | 92 | IN / OUT（`include/linux/iio/buffer.h:14-17`） | 分配者 |
| `access` | 98 | 后端 vtable 指针 | 后端 |
| `scan_mask` | 101 | 这个缓冲区要哪些通道 | 用户态经 `scan_elements/*_en` |
| `demux_list` | 104 | 解复用表 | 核心生成 |
| `pollq` | 107 | poll 等待队列 | 核心 |
| `watermark` | 110 | 唤醒水位 | 用户态 |
| `scan_timestamp` | 114 | 这个缓冲区要不要时间戳 | 核心 |
| `buffer_attr_list` | 117 | 缓冲区级属性 | 核心 + 后端追加 |
| `buffer_group` | 123 | 上一行打包的 group（`bufferN` 目录） | 核心 |
| `attrs` | 126 | 后端追加的属性数组 | 后端 |
| `demux_bounce` | 129 | demux 中转区 | 核心 |
| `attached_entry` | 132 | 挂在 `attached_buffers[]` 的关联 | 核心 |
| `buffer_list` | 135 | 挂进 `opaque->buffer_list` 的节点 | 核心 |
| `ref` | 138 | kref | 核心 |

vtable `struct iio_buffer_access_funcs`（`buffer_impl.h:53-73`）—— **12 个函数指针 + 2 个非回调字段**：

| 回调 | 行号 | 方向 | 说明 |
| --- | --- | --- | --- |
| `store_to` | 54 | 入 | 驱动推一个样本进来 |
| `read` | 55 | 出 | 用户态读 |
| `data_available` | 56 | 查 | 有多少可读 |
| `remove_from` | 57 | 出 | 内核内取一个样本（`iio_pop_from_buffer`） |
| `write` | 58 | 入 | 用户态写（输出型设备） |
| `space_available` | 59 | 查 | 还能写多少 |
| `request_update` | 61 | 配 | 参数变了，重建内部存储 |
| `set_bytes_per_datum` | 63 | 配 | — |
| `set_length` | 64 | 配 | — |
| `enable` | 66 | 生命周期 | — |
| `disable` | 67 | 生命周期 | — |
| `release` | 69 | 生命周期 | kref 归零时调 |
| `modes` | 71 | 声明 | 这个后端支持哪些 `INDIO_*` 模式 |
| `flags` | 72 | 声明 | 目前只有 FIXED_WATERMARK |

四个 in-tree 实现的填充情况见 §1.1 的表。

### §2.6 `struct iio_trigger` 与 pollfunc

`struct iio_trigger`（`include/linux/iio/trigger.h:60-79`，18 个字段）：

| 字段 | 行号 | 含义 |
| --- | --- | --- |
| `ops` | 61 | 提供方 vtable（`trigger.h:32-37`，3 个回调） |
| `owner` | 62 | 提供方模块，由 `iio_trigger_alloc()` 宏塞 `THIS_MODULE`（`trigger.h:165`） |
| `id` | 63 | `iio_trigger_ida` 分配的编号 |
| `name` | 64 | 触发器名；**全局唯一**，重名注册返回 `-EEXIST`（`industrialio-trigger.c:80-90`） |
| `dev` | 65 | 内嵌 device，注册在 `iio_bus_type` 上 |
| `list` | 67 | 挂在全局 `iio_trigger_list` |
| `alloc_list` | 68 | 供提供方自己串联多个触发器 |
| `use_count` | 69 | 本轮还有几个消费者没 `notify_done` |
| `subirq_chip` | 71 | 虚拟 irq_chip |
| `subirq_base` | 72 | 分到的 irq 号起点 |
| `subirqs[]` | 74 | 每个 subirq 的 enabled 状态 |
| `pool[]` | 75 | 哪些 subirq 已被占用 |
| `pool_lock` | 76 | 保护 `pool` |
| `attached_own_device` | 77 | 提供方和消费者是同一个设备 |
| `reenable_work` | 78 | 让 `reenable` 回调能睡觉的 workqueue 项 |

`ops` 三个回调（`trigger.h:33-36`）：`set_trigger_state`（开关硬件）、`reenable`（一轮采样结束后重新武装，可为 NULL）、`validate_device`（拒绝不兼容的消费者）。

`struct iio_poll_func`（`include/linux/iio/trigger_consumer.h:30-38`）是消费者侧的一轮采样上下文：

| 字段 | 行号 | 含义 |
| --- | --- | --- |
| `indio_dev` | 31 | 属于哪个设备 |
| `h` | 32 | 上半部（硬中断上下文） |
| `thread` | 33 | 下半部（线程上下文） |
| `type` | 34 | 传给 `request_threaded_irq` 的 flags |
| `name` | 35 | irq 名；由 `iio_alloc_pollfunc()` 的 `__printf(5,6)` 格式串生成 |
| `irq` | 36 | 从触发器 pool 里分到的 subirq 号 |
| `timestamp` | 37 | 上半部盖的时间戳，下半部读 |

`timestamp` 这一个字段就是两半部之间唯一的数据通道。标准上半部 `iio_pollfunc_store_time()`（`trigger_consumer.h:49`）除了盖时间戳什么都不做。

### §2.7 事件通路的结构

`struct iio_event_interface`（`drivers/iio/industrialio-event.c:36-45`，非公开）：

| 字段 | 行号 | 含义 |
| --- | --- | --- |
| `wait` | 37 | 读等待队列 |
| `det_events` | 38 | `DECLARE_KFIFO(det_events, struct iio_event_data, 16)` —— **深度 16 写死在这里**，无处可调 |
| `dev_attr_list` | 40 | 合成出来的事件属性 |
| `flags` | 41 | 含"已被打开"和"发生过溢出"两类位 |
| `group` | 42 | `events/` 目录 |
| `read_lock` | 43 | 串行化读 |
| `ioctl_handler` | 44 | `IIO_GET_EVENT_FD_IOCTL` 处理器 |

`struct iio_event_spec`（`include/linux/iio/iio.h:166-173`）是驱动侧的事件声明，6 个字段：`type`、`dir`，加四档 mask（`mask_separate` / `shared_by_type` / `shared_by_dir` / `shared_by_all`）—— **和 `iio_chan_spec` 的四档共享是同一套语义，但这里没有 `_available` 版**。

### §2.8 消费者侧的结构

三个结构，都很小，但配合规则容易踩坑：

```c
/* include/linux/iio/machine.h:21-26 —— 板级映射表项 */
struct iio_map {
	const char *adc_channel_label;   /* 匹配提供方的 datasheet_name */
	const char *consumer_dev_name;   /* 匹配消费者的 dev_name() */
	const char *consumer_channel;    /* 消费者自己起的名字 */
	void *consumer_data;
};
```

```c
/* include/linux/iio/consumer.h:24-28 —— 消费者句柄 */
struct iio_channel {
	struct iio_dev *indio_dev;
	const struct iio_chan_spec *channel;
	void *data;
};
```

三方匹配关系：

```
提供方 iio_chan_spec.datasheet_name   ("CH0")
            ▲
            │ 字符串比较（strcmp）
            │
   iio_map.adc_channel_label          ("CH0")
   iio_map.consumer_dev_name  ────────┐ 字符串比较 dev_name(consumer)
   iio_map.consumer_channel   ("vbat")│
            │                         ▼
            │                  消费者 device
            ▼
   iio_channel_get(dev, "vbat")  ──▶  struct iio_channel { indio_dev, channel }
```

⚠️ **三处字符串全靠 strcmp，一处写错的结果是 `iio_channel_get()` 返回 `-ENODEV`，没有任何日志。** `datasheet_name` 是 IIO 里少见的「不填就静默失效」字段——不填时它是 NULL，映射永远匹配不上（见 `01-api.md` §6.3）。DT 路径（`io-channels`）不走这三个字符串，走 fwnode + `#io-channel-cells`，见 `06-devicetree.md`。

---

## §3 跨用户/内核边界的内存布局

IIO 有两处结构体是**用户态直接按字节读取**的：`/dev/iio:deviceN` 上缓冲区 fd 读出的 scan 缓冲，和事件 fd 读出的 `struct iio_event_data`。这两处的布局是 ABI，改了就破坏用户态程序，所以必须精确到字节。

### §3.1 scan 缓冲的字节布局

**布局不是驱动定义的，是核心按 `iio_compute_scan_bytes()`（`drivers/iio/industrialio-buffer.c:705-729`）算出来的。** 算法只有四条规则：

1. 按 `scan_index` **升序**遍历当前使能的通道（`for_each_set_bit`，`:711-712`）；
2. 每个通道占 `storagebits / 8` 字节（`iio_storage_bytes_for_si()`，`:684-695`；`repeat > 1` 时再乘 `repeat`）；
3. 每个通道按**自己的宽度**对齐：`bytes = ALIGN(bytes, length)`（`:714`）；
4. 末尾整体按**最宽通道的宽度**对齐：`bytes = ALIGN(bytes, largest)`（`:727`）。

时间戳作为最后一个元素单独走一遍规则 3（`:719-724`），它的宽度取自时间戳通道自己的 `storagebits`（`iio_storage_bytes_for_timestamp()`，`:697-703`）。

**真实例子：inv_mpu6050 全通道使能。** 通道数组在 `drivers/iio/imu/inv_mpu6050/inv_mpu_core.c:1105-1117`，8 项；7 个数据通道全是 `s16/16` 大端（宏本体 `:1070-1087` 和 `:1089-1103`），时间戳是 `IIO_CHAN_SOFT_TIMESTAMP`（`:1106`，即 s64）。`scan_index` 的取值来自 `drivers/iio/imu/inv_mpu6050/inv_mpu_iio.h:406-414` 那个 enum。

```
scan_bytes = 24（7×2=14 → 对齐到 8 得 16 → +8 得 24 → 对齐到 largest=8 仍是 24）

字节偏移  0    2    4    6    8   10   12   14   16                   24
         ├────┼────┼────┼────┼────┼────┼────┼────┼─────────────────────┤
         │ACCL│ACCL│ACCL│TEMP│GYRO│GYRO│GYRO│填充│  timestamp (s64)    │
         │ X  │ Y  │ Z  │    │ X  │ Y  │ Z  │2字节│  CPU 字节序          │
         └────┴────┴────┴────┴────┴────┴────┴────┴─────────────────────┘
 scan_index  0    1    2    3    4    5    6         7
 字节序      BE   BE   BE   BE   BE   BE   BE        本机
 谁写      驱动的下半部（一次 regmap 批量读后原样搬进来）  核心/驱动
 谁读      用户态程序，按 in_*_type 属性解析
```

**关键陷阱：同一个缓冲区里字节序是混的。** 7 个数据通道是 `IIO_BE`（芯片就是大端出数），时间戳是 CPU 字节序（就是一个 `int64_t` 赋值，`include/linux/iio/buffer.h:41-42`）。用户态**必须逐通道读 `in_*_type` 属性**决定怎么转，不能对整个缓冲区做一次统一的字节序转换。

`in_*_type` 属性的格式由 `iio_show_fixed_type()`（`drivers/iio/industrialio-buffer.c:363-392`）生成：

```
[be|le]:[s|u]realbits/storagebits>>shift          （repeat ≤ 1）
[be|le]:[s|u]realbits/storagebitsX repeat>>shift  （repeat > 1，中间是大写 X）
```

对上例的 accel 通道就是 `be:s16/16>>0`。⚠️ **`IIO_CPU` 不会原样出现在属性里**——`iio_show_fixed_type()` 在 `:370-376` 按编译时的 `__LITTLE_ENDIAN` 把它换成 `le` 或 `be`，用户态永远只看到 `le` / `be` 两种。arm64 常规配置是小端，所以看到的是 `le`。

格式的官方说明在 `Documentation/ABI/testing/sysfs-bus-iio:1385-1401`，其中给了一个很值得看的例子：`s48/64>>2` 是「64 位空间里的 48 位有符号数，先右移 2 再掩掉高 16 位」。**`storagebits` 同时是对齐要求**，这是规则 3 的来源。

时间戳的偏移在驱动侧是这样算出来的：

```c
/* include/linux/iio/buffer.h:41-42 */
	size_t ts_offset = indio_dev->scan_bytes / sizeof(int64_t) - 1;
	((int64_t *)data)[ts_offset] = timestamp;
```

上例里 `24 / 8 - 1 = 2`，即第 2 个 `int64_t`，字节偏移 16 ✓。**这个式子成立的前提是"时间戳恰好是最后 8 字节且整体已按 8 对齐"**——规则 4 保证了后半句。如果驱动用了非 8 字节的时间戳通道，这个 inline 函数会算错位置，这也是为什么它只适用于 `IIO_CHAN_SOFT_TIMESTAMP`。数据不满足 8 字节对齐时要改用 `iio_push_to_buffers_with_ts_unaligned()`（`include/linux/iio/buffer.h:48-50`），它走 `opaque->bounce_buffer` 中转。

用户态读的入口是 `iio_buffer_read()`（`drivers/iio/industrialio-buffer.c:106-...`），前四道门值得记住：`info` 为 NULL → `-ENODEV`（`:117-118`）、后端没有 `read` 回调 → `-EINVAL`（`:120-121`）、方向不是 IN → `-EPERM`（`:123-124`）、`bytes_per_datum` 为 0 → **返回 0 即 EOF 而不是错误**（`:132-133`）。最后一条最容易误诊：一个通道都没使能时 `read()` 直接返回 0，看起来像设备坏了。

### §3.2 `struct iio_event_data` 的布局

```c
/* include/uapi/linux/iio/events.h:22-25 */
struct iio_event_data {
	__u64	id;
	__s64	timestamp;
};
```

```
字节偏移  0                     8                     16
         ├─────────────────────┼─────────────────────┤
         │  id (u64, 打包字段) │ timestamp (s64, ns) │
         └─────────────────────┴─────────────────────┘
 字节序        本机                    本机
 谁写      核心 iio_push_event()   核心（驱动传进来）
 谁读      用户态，用 uapi 里的 7 个提取宏拆 id
```

固定 16 字节、无填充、全本机字节序——比 scan 缓冲简单得多，代价是没有可扩展性（这也是 uapi 头文件 `:35-36` 那句 "Perhaps review this function in the future" 的由来）。`id` 里打包了 6 个字段，位布局和 7 个提取宏见 `01-api.md` §5。

⚠️ `IIO_EVENT_CODE()` 宏（`include/linux/iio/events.h:24-29`）的 `chan` 和 `chan1` 两个参数**被或进同一段低 16 位**（`:28-29`），不是两个独立字段。驱动同时传两个非零值会得到一个错位的 id，编译器不会警告。

---

## §4 核心操作数据流图

IIO 是框架型子系统，没有"传输"这个统一操作。它有**三条互不重叠的主数据流**，读代码时先确定自己在哪一条上。

### §4.1 流一：单次读（sysfs / 内核内消费者共用终点）

```
用户态                          内核内消费者
cat in_voltage0_raw            iio_read_channel_raw(chan, &val)
     │                                  │  inkern.c
     │ sysfs show                       │
     ▼                                  ▼
iio_read_channel_info()          iio_channel_read()   inkern.c:561
（industrialio-core.c）                  │
     │                                  │
     └──────────────┬───────────────────┘
                    │  两条路都要先拿到直接模式
                    ▼
        iio_device_claim_direct_mode(indio_dev)
        ├─ currentmode 在 INDIO_ALL_BUFFER_MODES 里 → -EBUSY，直接失败
        └─ 否则：持住 mlock 返回 0   ← 注意锁是"带出来"的
                    │
                    ▼
        indio_dev->info->read_raw(indio_dev, chan, &val, &val2, mask)
                    │  ← 驱动在这里访问硬件（i2c/spi 传输）
                    ▼
        iio_device_release_direct_mode(indio_dev)   放 mlock
                    │
                    ▼
        iio_format_value()（只有 sysfs 路径要格式化成字符串）
```

**这条流的要点是 `claim_direct_mode` 返回 0 时锁是持着的**——它不是一个"检查一下"的函数，忘了配对 release 会死锁（见 `01-api.md` §1）。

### §4.2 流二：触发采样 → 缓冲区 → 用户态

```
 触发源（硬件中断 / hrtimer / sysfs 写 / 另一个设备的数据就绪）
        │
        ▼
 iio_trigger_poll()             或  iio_trigger_poll_chained()
 generic_handle_irq()               handle_nested_irq()
 【硬中断上下文】                    【线程上下文】
        │                                │
        └──────────┬─────────────────────┘
                   │ 对 pool 里每个已分配的 subirq 各投一次
                   ▼
        ┌──────────────────────────────────┐
        │ pf->h  上半部（默认是             │
        │ iio_pollfunc_store_time）        │
        │   pf->timestamp = iio_get_time_ns│  ← 两半部唯一的数据通道
        └──────────────┬───────────────────┘
                       │ IRQ_WAKE_THREAD
                       ▼
        ┌──────────────────────────────────┐
        │ pf->thread  下半部【可睡眠】      │
        │  1. 读硬件（i2c/spi 批量读）      │
        │  2. 按 active_scan_mask 打包      │
        │  3. iio_push_to_buffers_with_    │
        │     timestamp(indio_dev, buf,    │
        │               pf->timestamp)     │
        │  4. iio_trigger_notify_done(trig)│  ← 忘了这步触发器不会再武装
        └──────────────┬───────────────────┘
                       ▼
        iio_push_to_buffers()   遍历 opaque->buffer_list
                       │        （只有"已使能"的缓冲区，不是 attached 全部）
                       ▼
        每个缓冲区：iio_demux_table 按各自 scan_mask 挑字段
                       │  → demux_bounce
                       ▼
        buffer->access->store_to()   ← 后端可插拔（§1.1）
                       │
                       ▼
        wake_up(&buffer->pollq)  当可读量 ≥ watermark
                       │
                       ▼
        用户态 read(buffer_fd)  →  iio_buffer_read()  →  access->read()
```

**demux 是这条流里最容易被忽略的一层。** 驱动推的是"所有使能通道打包好的一整帧"，但多缓冲区场景下每个缓冲区的 `scan_mask` 可以不同——demux 表负责从这一帧里挑出各缓冲区要的字段（`iio_buffer_update_demux()`，`drivers/iio/industrialio-buffer.c:974-...`）。单缓冲区且 mask 一致时 demux 表是空的，走直通路径。

### §4.3 流三：内核内消费者与提供方的耦合

```
消费者驱动（如 iio-hwmon、电池驱动、iio-mux）
        │
        │ ① 建立引用
        ├──── DT 路径：  io-channels = <&adc 0>;
        │                 → devm_iio_channel_get(dev, "name")
        │                 → bus_find_device_by_fwnode(&iio_bus_type, ...)  inkern.c:158
        │                 → 找不到提供方就 -EPROBE_DEFER（不是 -ENODEV！）
        │
        └──── 板级路径： iio_map_array_register(indio_dev, map)
                          → iio_channel_get(dev, "vbat")
                          → 三处 strcmp（§2.8）匹配不上就 -ENODEV，无日志

        ② 使用（三种口径，粒度递增）
        ├── iio_read_channel_raw()        ⋯⋯ 走流一，拿原始整数
        ├── iio_read_channel_processed()  ⋯⋯ 走流一 + 自动套 scale/offset
        └── iio_channel_start_all_cb()    ⋯⋯ 走流二，回调缓冲后端

        ③ 释放：iio_channel_release() / devm 自动
```

三条流的关系：**流三不是第四条独立通路，它是流一和流二的内核内入口。** 所以 `inkern.c` 里的每个读函数最终都落在 `info->read_raw` 或某个缓冲区后端上。

---

## §5 命名约定速查

IIO 的命名极其规律，认出规律能省掉大量查找：

| 前缀 / 形态 | 含义 | 例子 |
| --- | --- | --- |
| `iio_device_*` | 操作一个 `iio_dev` 对象 | `iio_device_register` / `iio_device_alloc` |
| `iio_trigger_*` | 操作一个触发器（提供方视角为主） | `iio_trigger_register` / `iio_trigger_poll` |
| `iio_channel_*` | **消费者侧**操作一个 `iio_channel` 句柄 | `iio_channel_get` / `iio_channel_release` |
| `iio_read_channel_*` / `iio_write_channel_*` | 消费者侧读写一个通道的值 | `iio_read_channel_processed` |
| `iio_push_*` | 驱动往上游送数据 | `iio_push_to_buffers` / `iio_push_event` |
| `iio_*_setup` / `_setup_ext` | 一键装配（分配 + 挂钩 + 注册） | `iio_triggered_buffer_setup` |
| `devm_` 前缀 | 绑定到 `struct device` 生命周期的版本 | `devm_iio_device_alloc` |
| `_ext` 后缀 | 参数更多的真身；不带 `_ext` 的多半是宏 | `devm_iio_kfifo_buffer_setup_ext` |
| `__` 前缀 | 真身函数，公开名是宏（塞 `THIS_MODULE`） | `__iio_device_register` |
| `iio_dev_opaque` / `to_iio_dev_opaque` | 核心私有侧 | — |
| `INDIO_*` | 操作模式位 | `INDIO_DIRECT_MODE` |
| `IIO_CHAN_INFO_*` | info 掩码里的位（决定属性名） | `IIO_CHAN_INFO_SCALE` |
| `IIO_MOD_*` | 通道修饰符（`channel2` 的取值） | `IIO_MOD_X` |
| `IIO_VAL_*` | `read_raw` 返回值告诉核心怎么格式化 | `IIO_VAL_INT_PLUS_MICRO` |
| `IIO_EV_TYPE_*` / `IIO_EV_DIR_*` | 事件类型 / 方向 | `IIO_EV_TYPE_THRESH` |

**⚠️ 命名规律的两个例外，不认出来会找错地方：**

1. **`iio_channel_*` 是消费者 API，`iio_device_*` 是提供方 API**，两者不是同一层的对称命名。`iio_channel_get()` 不是"从设备里取一个通道"，是"作为消费者引用别人的通道"。
2. **`_ext` 结尾的才是导出的真函数**，不带 `_ext` 的同名符号往往是宏（`iio_triggered_buffer_setup`、`devm_iio_kfifo_buffer_setup`、`iio_trigger_alloc`）。`grep -w` 找不带 `_ext` 的名字会命中宏定义而不是实现——这正是索引工具在 IIO 上的主要盲区（七个宏的清单在 `01-api.md` §8.1）。

---

## §6 全局变量与锁

### §6.1 每设备的锁

| 锁 | 位置 | 保护什么 | 使用点数 |
| --- | --- | --- | --- |
| `indio_dev->mlock` | `include/linux/iio/iio.h:577` | 操作模式互斥：直接读 vs 缓冲采样不能并发 | 73 |
| `opaque->info_exist_lock` | `include/linux/iio/iio-opaque.h:47` | `indio_dev->info` 指针本身（拆除时被置 NULL） | 27 |
| `buffer->access` 内部锁 | 各后端自己 | 后端存储 | 见 `04-extensions.md` |
| `trig->pool_lock` | `include/linux/iio/trigger.h:76` | 触发器的 subirq 分配位图 | 7 |
| `ev_int->read_lock` | `drivers/iio/industrialio-event.c:43` | 串行化事件 kfifo 的读 | — |

计数口径：`grep -rw --include=*.c --include=*.h '<名字>' drivers/iio include/linux/iio | wc -l`，数的是**含该符号的行数**（含声明行和注释行），不是加锁点数。

**`mlock` 和 `info_exist_lock` 分工的要点：** `mlock` 保护"设备在什么模式下"，`info_exist_lock` 保护"设备还在不在"。拆除时序是 `iio_device_unregister()` 拿 `info_exist_lock` 把 `info` 置 NULL，此后所有入口（`iio_buffer_read()` 的 `:117-118` / `:142-145`、消费者侧的读写）看到 `info == NULL` 就返回 `-ENODEV`。**这是 IIO 处理"用户态还开着 fd 但设备已经拔了"的唯一机制**，两把锁都不能省。

`mlock_key`（`iio-opaque.h:46`）是给 `mlock` 用的 lockdep class key：不同 IIO 设备的 `mlock` 会互相嵌套（比如 iio-mux 的消费者读会在持自己 mlock 的同时去拿提供方的 mlock），如果共用一个 lockdep class 就会被误报成死锁环。**所以看到 lockdep 报 IIO mlock 环时，先确认是不是真的同设备重入。**

### §6.2 全局变量

全树 `drivers/iio/` 里的模块级全局状态只有这些：

| 变量 | 位置 | 作用 | 配套锁 |
| --- | --- | --- | --- |
| `iio_bus_type` | `industrialio-core.c:42-45` | 退化的总线，见 §1.0 关系二 | driver core 内部 |
| `iio_ida` | `industrialio-core.c:37` | 分配 `iio:deviceN` 的 N | IDA 自带 |
| `iio_debugfs_dentry` | `industrialio-core.c:47` | debugfs 根目录 | 无（只在 init/exit 动） |
| `iio_trigger_ida` | `industrialio-trigger.c:33` | 分配 `triggerN` 的 N | IDA 自带 |
| `iio_trigger_list` | `industrialio-trigger.c:36` | 全部已注册触发器 | `iio_trigger_list_lock`（`:37`，9 处） |
| `iio_map_list` | `inkern.c:25` | 板级映射表 | `iio_map_list_lock`（`:26`，10 处） |
| `iio_trigger_types_list` | `industrialio-sw-trigger.c:25` | configfs 可创建的触发器类型 | `iio_trigger_types_lock`（`:26`） |
| `iio_device_types_list` | `industrialio-sw-device.c:25` | configfs 可创建的设备类型 | `iio_device_types_lock`（`:26`） |

四条链表 + 两个 IDA + 一个总线 + 一个 debugfs 根 —— **没有全局的"设备链表"**。要枚举所有 IIO 设备只能走 driver core 的 `bus_for_each_dev(&iio_bus_type, ...)`，这也是 `inkern.c:158` 那个 `bus_find_device_by_fwnode()` 存在的原因。

三个 `__init` 函数各自建自己那部分：`iio_init()`（`industrialio-core.c:345`）、`iio_sw_trigger_init()`（`industrialio-sw-trigger.c:165`）、`iio_sw_device_init()`（`industrialio-sw-device.c:161`）。初始化顺序和失败回滚在 `03-internals.md` Part A 展开。

---

## §7 阅读导航

| 想知道 | 去哪 |
| --- | --- |
| 这些结构体上有哪些函数可以调 | `01-api.md` |
| 字段是在什么时刻被填上的 | `03-internals.md` Part A（生命周期时间线） |
| 锁的实际加锁顺序和已知设计债 | `03-internals.md` Part B / Part C |
| 四个缓冲区后端各自怎么实现 vtable | `04-extensions.md` |
| 写一个驱动时这些结构体怎么填 | `05-usage.md` |
| `io-channels` 这些属性怎么变成 `iio_channel` | `06-devicetree.md` |
| 字段值在运行期怎么看 | `07-debug.md` |
| 为什么 `iio_dev` 要藏在 opaque 里（大白话版） | `08-feynman.md` |
