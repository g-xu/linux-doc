---
title: 扩展机制
---

# 04 · IIO 子框架

## §0 本篇的组织轴与分诊表

### §0.1 组织轴声明

**本篇的组织轴是「一节一个独立框架，框架之间互不调用」。**

这条轴要求把每个子框架当成一个可以单独装卸的模块来讲：它自己的 Kconfig、自己的入口函数、自己的数据结构、自己的消费者群体。**判据是"抽掉它，其余框架能不能编译通过、能不能跑"**——IIO 里这一点体现得比多数子系统干净，因为七个子框架里有六个是独立的 `tristate` 模块（见 §0.2）。

三个必须说明的例外：

1. **§1 内部的四个缓冲区后端不是四个框架，是同一个契约的四个实现。** 它们共享 `struct iio_buffer_access_funcs`（`include/linux/iio/buffer_impl.h:53-73`），互相不调用，但都挂在核心的同一个挂载点上。所以 §1 是一节，四个后端是四个小节，外加一张填充度对比表——这正是 skill 里"消费者散得很开时先给分布表再挑代表"的同构做法。
2. **§2 的 triggered-buffer 便利层依赖 §1.1 的 kfifo 后端**（`drivers/iio/buffer/industrialio-triggered-buffer.c:59` 直接调 `iio_kfifo_allocate()`），Kconfig 里也写成了 `select IIO_KFIFO_BUF`（`drivers/iio/buffer/Kconfig:53`）。这是全篇唯一一处"框架 A 硬依赖框架 B"，其余六处都是平行关系。
3. **§5 的 sw-device / sw-trigger 都依赖 §5.1 的 configfs 根**（`drivers/iio/Kconfig:51`、`:59` 两处 `select IIO_CONFIGFS`）。所以 §5 是一节讲三个模块，不拆成三节。

### §0.2 分诊表：哪几个在 arm64 上真会碰到

| 子框架 | 本篇章节 | Kconfig | 模块形态 | arm64 上会碰到吗 | 依据 |
|---|---|---|---|---|---|
| kfifo 缓冲区后端 | §1.1 | `IIO_KFIFO_BUF` | tristate | **一定会** | 被 §2 `select`，而 §2 有 117 个消费者（§8.2） |
| DMA 缓冲区后端 | §1.2 | `IIO_BUFFER_DMA` / `IIO_BUFFER_DMAENGINE` | tristate | **几乎不会** | 全树只有 `drivers/iio/adc/adi-axi-adc.c:116` 一个消费者 |
| callback 缓冲区 | §1.3 | `IIO_BUFFER_CB` | tristate | 会（间接） | 5 个消费者里 3 个在 `drivers/input/`、1 个在 `sound/soc/` |
| hw-consumer 缓冲区 | §1.4 | `IIO_BUFFER_HW_CONSUMER` | tristate | **不会** | 唯一消费者 `drivers/iio/adc/stm32-dfsdm-adc.c` 是 STM32（arm32） |
| triggered-buffer 便利层 | §2 | `IIO_TRIGGERED_BUFFER` | tristate | **一定会** | 117 个消费者，是 IIO 驱动的默认写法 |
| triggered-event 便利层 | §3 | `IIO_TRIGGERED_EVENT` | tristate | **不会** | 全树 2 个消费者，都不在 arm64 路径上（§8.2） |
| 触发器提供方 | §4 | `IIO_*_TRIGGER` 五个 | tristate | 会（sysfs / hrtimer） | 两个通用提供方与平台无关；STM32 两个是 arm32 |
| configfs 软件框架 | §5 | `IIO_CONFIGFS` / `IIO_SW_DEVICE` / `IIO_SW_TRIGGER` | tristate | 会（hrtimer 触发器要它） | `IIO_HRTIMER_TRIGGER` 有 `depends on IIO_SW_TRIGGER`（`drivers/iio/trigger/Kconfig:11`） |
| 内核内消费者框架 | §6 | 无独立开关（编进 `industrialio`） | 内建 | **一定会** | 111 处调用点，散在 7 个非 IIO 目录（§8.2） |
| 通道变换层 iio-mux | §7.1 | `IIO_MUX` | tristate | 会（1 个 arm64 节点） | `compatible = "io-channel-mux"` 在 arm64 DTS 命中 1 处、arm32 命中 34 处 |
| 通道变换层 iio-rescale | §7.2 | `IIO_RESCALE` | tristate | **不会** | 五个 compatible 在 `arch/arm64/boot/dts/` 全部命中 0 处 |

分诊表要读出来的结论：**IIO 的子框架大部分是"契约齐备但消费者稀疏"**。真正在 arm64 上必然被拉起来的只有三条——kfifo 后端、triggered-buffer 便利层、内核内消费者框架。剩下八个要么零消费者、要么消费者只在 arm32 平台上。这个判断会在 §8.2 用带重跑命令的统计表逐格坐实。

### §0.3 挂载点全景图：七个框架钉在 03 时间线的哪一点

`03-internals.md` 的 Part A 把核心生命周期拆成 A1–A14。本篇每个框架都必须交代它挂在哪一格上：

```
 A1  iio_init                    ← §5.1 configfs 根是独立 module_init，不挂在这里
 A2  iio_device_alloc
     ↓
 A4  【驱动 probe 中段】          ← §2 triggered-buffer  在此分配 kfifo + pollfunc
                                 ← §3 triggered-event   在此分配 pollfunc_event
                                 ← §1.2 DMA 后端        在此 devm_iio_dmaengine_buffer_setup
                                 ← §6 iio_map           在此 devm_iio_map_array_register
     ↓
 A3  iio_device_register          ← §1.x 后端已挂上 indio_dev->buffer，核心在这里
     │                              为它建 buffer sysfs（03 §A3.2）
     ↓
 A5  iio_trigger_register         ← §4 触发器提供方的注册点
 A6  current_trigger 写入
     ↓
 A7  buffer/enable 写 1           ← §1.x 后端的 ops 在这里被逐个回调
                                 ← §1.3 callback / §1.4 hw-consumer 走的不是这条路，
                                    而是 A9 的 iio_update_buffers（见 §1.3.2）
     ↓
 A8  数据到达
     ↓
 A9  iio_update_buffers           ← §1.3 iio_channel_start_all_cb 的实现体
                                 ← §1.4 iio_hw_consumer_enable 的实现体
     ↓
 A11 iio_device_unregister
 A12 iio_triggered_buffer_cleanup ← §2 的拆除点
 A13 iio_dev_release
```

**§6 和 §7 不在这张图上。** §6 的内核内消费者框架跑在别的设备的 probe 里，通过 `iio_bus_type` 反查 provider（`drivers/iio/inkern.c:159`），它和被消费的 IIO 设备之间只有 `struct device` 引用计数这一条联系。§7 的 mux / rescale 是**同时既是消费者又是 provider** 的 IIO 设备——它们走的是完整的 A2→A3 路径，只不过 `read_raw` 回调里再去调 §6 的 API。这个"两头都在 IIO 里"的形状在 §7.0 单独说。

### §0.4 哪些内容不在本篇

| 想查的东西 | 去哪篇 |
|---|---|
| `iio_buffer_access_funcs` 每个字段的语义、谁调它 | `02-datastructures.md` §5；调用时序在 `03-internals.md` §A7.4 |
| `iio_update_buffers` / `iio_enable_buffers` 内部怎么走 | `03-internals.md` §A7、§A9 |
| 触发器的 subirq 池、`use_count`、`iio_trigger_poll` 两个变体 | `03-internals.md` §A6、§A8 |
| `mlock` / `info_exist_lock` 的锁序 | `03-internals.md` §B1 |
| 三个引用计数分别保护什么 | `03-internals.md` §B2 |
| `io-channels` / `#io-channel-cells` 怎么写在 DTS 里 | `06-devicetree.md` |
| 各框架的入口函数签名与错误码 | `01-api.md` |
| 写一个用 triggered-buffer 的驱动的骨架 | `05-usage.md` |
| 本篇每个数字的重跑命令 | §8.2；证据等级登记在 `99-evidence.md` |

---

## §1 缓冲区后端族：`iio_buffer_access_funcs` 的四个实现

### §1.0 契约与四个实现的填充度

契约本体是 `struct iio_buffer_access_funcs`（`include/linux/iio/buffer_impl.h:53-73`）。全树只有四个初始化点，一条命令可以查全：

```bash
grep -rn --include=*.c 'struct iio_buffer_access_funcs [a-z_]* = {' .
```

四处命中（本版本实测）：

| 后端 | 初始化器位置 | 填了几个函数指针 | `.modes` | `.flags` |
|---|---|---|---|---|
| kfifo | `drivers/iio/buffer/kfifo_buf.c:188-201` | **10** | `INDIO_BUFFER_SOFTWARE \| INDIO_BUFFER_TRIGGERED` | 未填 |
| dmaengine | `drivers/iio/buffer/industrialio-buffer-dmaengine.c:113-125` | **8** | `INDIO_BUFFER_HARDWARE` | `INDIO_BUFFER_FLAG_FIXED_WATERMARK` |
| callback | `drivers/iio/buffer/industrialio-buffer-cb.c:41-46` | **2** | `INDIO_BUFFER_SOFTWARE \| INDIO_BUFFER_TRIGGERED` | 未填 |
| hw-consumer | `drivers/iio/buffer/industrialio-hw-consumer.c:47-50` | **1** | `INDIO_BUFFER_HARDWARE` | 未填 |

从 10 掉到 1，跨度是一个数量级。**这不是四个"完整度不同"的实现，而是四种根本不同的用法**：

- kfifo 是**给用户态送数据**的后端：读、写、空间查询、长度设置全要有，因为 `/dev/iio:deviceN` 的每一种系统调用都要落到某个 ops 上。
- dmaengine 是**硬件直接填内存**的后端：不需要 `.store_to`（数据不经过 CPU 逐样本推送），但需要 `.enable` / `.disable`（要启停 DMA 通道）——kfifo 反过来，有 `.store_to` 没有 `.enable`/`.disable`。这两个后端的 ops 集合**不是包含关系而是交叉关系**，这是本节最容易看错的一点。
- callback 只要 `.store_to`：它的全部工作就是"数据来了，调消费者给的回调"。
- hw-consumer 连 `.store_to` 都不要：它根本不搬数据。它存在的唯一目的是**借用核心的 buffer 使能机制去打开 provider 的硬件通路**，数据在硬件里直接流到下一级，压根不进内核内存。所以它只需要一个 `.release` 来释放自己那点管理结构。

**逐格对照 ops 的差集**（依据上面四个初始化器逐行读出）：

| ops 字段 | kfifo | dmaengine | callback | hw |
|---|---|---|---|---|
| `.store_to` | ✅ `:191` | — | ✅ `:42` | — |
| `.read` | ✅ `:192` | ✅ `:114` | — | — |
| `.write` | ✅ `:195` | — | — | — |
| `.data_available` | ✅ `:193` | ✅ `:120` | — | — |
| `.space_available` | ✅ `:196` | — | — | — |
| `.remove_from` | ✅ `:194` | — | — | — |
| `.request_update` | ✅ `:197` | ✅ `:117` | — | — |
| `.set_bytes_per_datum` | ✅ `:198` | ✅ `:115` | — | — |
| `.set_length` | ✅ `:199` | ✅ `:116` | — | — |
| `.enable` | — | ✅ `:118` | — | — |
| `.disable` | — | ✅ `:119` | — | — |
| `.release` | ✅ `:200` | ✅ `:121` | ✅ `:43` | ✅ `:48` |

（表内行号：kfifo 列属于 `drivers/iio/buffer/kfifo_buf.c`，dmaengine 列属于 `drivers/iio/buffer/industrialio-buffer-dmaengine.c`，callback 列属于 `drivers/iio/buffer/industrialio-buffer-cb.c`，hw 列属于 `drivers/iio/buffer/industrialio-hw-consumer.c`。）

`.release` 是**唯一四家都填**的字段。这不是巧合：`iio_buffer_put`（`drivers/iio/industrialio-buffer.c:1954-1958`）在 kref 归零时走 `iio_buffer_release`（`:1928`），后者无条件调 `.release`。四个后端的 `struct iio_buffer` 都是嵌在自己更大的结构体里的，不填 `.release` 就必然泄漏外层结构。

**挂载点**：四个后端都通过 `iio_buffer_init()`（`drivers/iio/industrialio-buffer.c:331`）初始化，然后把 `&buffer->access` 指向自己的 ops。核心在 03 §A3.2 建 buffer sysfs 时读 `.modes`，在 03 §A7.4 使能时逐个回调。**后端自己不知道 A3/A7 的存在**——这是这一族能做到"互不调用"的原因。

### §1.1 kfifo 后端（唯一在 arm64 上必然被加载的后端）

代码：`drivers/iio/buffer/kfifo_buf.c`（290 行）。数据结构是 `struct iio_kfifo`，内含 `struct kfifo kf` + `struct mutex user_lock`（`:213` `mutex_init`，`:135` `mutex_destroy`）。

**三个入口，三种生命周期管理**：

| 入口 | 位置 | 谁负责释放 | 全树调用点 |
|---|---|---|---|
| `iio_kfifo_allocate()` | `:203-219` | 调用者自己调 `iio_kfifo_free()` | 树内只有 2 处调用：`drivers/iio/buffer/industrialio-triggered-buffer.c:59`（§2）和本文件 `:247`（下一行的 devm 版本） |
| `devm_iio_kfifo_allocate()` | `:239-259` | devres（`devm_iio_kfifo_release` `:227-230`） | `static`，只被下一行调用 |
| `devm_iio_kfifo_buffer_setup_ext()` | `:270-288` | devres | **16 处**：2 处直接调 `_ext`（`drivers/iio/common/cros_ec_sensors/cros_ec_sensors_core.c:339`、`drivers/iio/accel/adxl367.c:1554`），14 处走宏 `devm_iio_kfifo_buffer_setup`（`include/linux/iio/kfifo_buf.h:18-19`） |

`devm_iio_kfifo_buffer_setup_ext` 是**推荐给驱动的写法**，它把"分配 + attach 到 indio_dev"合成一步。而 §2 的 triggered-buffer 用的是**裸的** `iio_kfifo_allocate()`——因为它还要在中间插入 pollfunc 的分配，做不成一步。

**`.write` 和 `.remove_from` 是这个后端独有的两个 ops**（对照 §1.0 的差集表），它们服务的是 IIO 的**输出方向缓冲区**（DAC 类设备，用户态往缓冲区写、硬件从缓冲区取）。两个函数各有一处值得点出来：

- `iio_kfifo_write()`（`:170-186`）在 `mutex_lock(&kf->user_lock)` 保护下做 `kfifo_from_user`。**这把 `user_lock` 是 kfifo 后端自己的锁，和核心的 `mlock` 无关**——03 §B1 的锁表只覆盖核心的两把锁，这是第三把，属于后端私有。
- `iio_kfifo_remove_from()`（`:153-168`）第一行的检查是 `if (kfifo_size(&kf->kf) < 1)`（`:158`）。`kfifo_size()` 返回的是**容量**而不是**当前元素数**（后者是 `kfifo_len()`）。容量的下界有两道保证：`iio_kfifo_allocate()` 在 `:212` 就把 `buffer.length` 初始化成 2，`iio_set_length_kfifo()`（`:78-88`）又把任何小于 2 的请求抬回 2，而 `__iio_allocate_kfifo()`（`:25-40`）在 `length == 0` 时直接返回 `-EINVAL`。所以只要缓冲区分配成功，容量就一定 ≥2，这个检查**永远为假**。真正拦住"空缓冲区取数据"的是紧跟的 `:160-162`：`kfifo_out` 返回值不等于 1 就返回 `-EBUSY`。结论：`:158` 是死代码，不是错误行为（缺陷登记见 §8.3-D1，和 `03-internals.md` §C3.9 是同一条，这里补上"为什么它无害"的推导）。

**另一处静默行为**：`iio_set_length_kfifo()`（`:78-88`）把小于 2 的 length 直接抬到 2，不报错、不打印。用户态写 `buffer/length` 为 1，读回来会是 2。这是"约束靠悄悄改写而不是靠返回错误"的做法，`Documentation/ABI/testing/sysfs-bus-iio` 里没有记录（登记见 §8.3-D2）。

**`iio_kfifo_free()` 名不副实**：它的全部函数体只有一行 `iio_buffer_put(r);`（`drivers/iio/buffer/kfifo_buf.c:221-224`）。也就是说它是**减引用而不是释放**，只有在这是最后一个引用时才真的走到 `iio_kfifo_buffer_release()`。这一点在 §2 的拆除路径里会变成一个具体的时序问题（见 §2.3）。

### §1.2 DMA 后端：两层 ops，唯一的 spinlock，arm64 零消费者

DMA 后端是**两个模块**：

| 模块 | 文件 | 行数 | 职责 |
|---|---|---|---|
| 通用 DMA 缓冲区基础设施 | `drivers/iio/buffer/industrialio-buffer-dma.c` | 682 | 块（block）的分配/排队/完成/回收，不碰任何具体 DMA 引擎 |
| dmaengine 绑定层 | `drivers/iio/buffer/industrialio-buffer-dmaengine.c` | 297 | 把上面的抽象接到 `dmaengine` 子系统的 `dma_async_*` API 上 |

Kconfig 也是两层：`IIO_BUFFER_DMAENGINE` 有 `select IIO_BUFFER_DMA`（`drivers/iio/buffer/Kconfig:24`），反过来不成立——`IIO_BUFFER_DMA` 可以单独开，给树外的其他绑定层用。

**这是本篇唯一一处"框架内部还有一层可插拔"的地方。** 除了 §1.0 表里的 `iio_buffer_access_funcs`，DMA 后端还定义了第二个 ops 结构 `struct iio_dma_buffer_ops`，全树只有一个实现：

```
drivers/iio/buffer/industrialio-buffer-dmaengine.c:127-130
    static const struct iio_dma_buffer_ops iio_dmaengine_default_ops = {
        .submit = iio_dmaengine_buffer_submit_block,
        .abort  = iio_dmaengine_buffer_abort,
    };
```

它在 `:202` 作为 `iio_dma_buffer_init()` 的第三个参数传进去。所以完整的调用链是**三层**：

```
核心 (industrialio-buffer.c)
  └─ iio_buffer_access_funcs = iio_dmaengine_buffer_ops   （industrialio-buffer-dmaengine.c:113-125）
       └─ 每个 ops 转调 iio_dma_buffer_*                   （industrialio-buffer-dma.c 的导出符号群）
            └─ iio_dma_buffer_ops = iio_dmaengine_default_ops （industrialio-buffer-dmaengine.c:127-130）
                 └─ dmaengine 子系统
```

**中间那一层为什么存在**：`iio_dma_buffer_read()`（`drivers/iio/buffer/industrialio-buffer-dma.c:481-528`）这类函数做的是"从已完成的块里往用户态拷贝、把空块还回队列"，这套逻辑和用哪个 DMA 引擎无关；而 `.submit` / `.abort` 是和引擎强绑定的两个动作。分层的切口就在这里。

**核心唯一的 spinlock 例外在这个模块里。** `03-internals.md` §B5 已经登记过这条修正（核心是纯 mutex，DMA 后端是唯一例外），这里给出完整位置：

| 锁 | 位置 | 类型 | 保护什么 | 为什么必须是 spinlock |
|---|---|---|---|---|
| `queue->list_lock` | 声明在 `include/linux/iio/buffer-dma.h:106` | `spinlock_t` | 块的 incoming 队列 | `iio_dma_buffer_block_done()`（`drivers/iio/buffer/industrialio-buffer-dma.c:213-224`）是 DMA 完成回调，可能在中断上下文 |
| `iio_dma_buffer_dead_blocks_lock` | `drivers/iio/buffer/industrialio-buffer-dma.c:122` | `DEFINE_SPINLOCK` | 全局 dead-block 链表 | 同上：`iio_buffer_block_put_atomic()`（`:155-158`）要在原子上下文里挂链 |
| `queue->lock` | `mutex_init` 在 `drivers/iio/buffer/industrialio-buffer-dma.c:622` | `struct mutex` | 队列的其余全部状态 | 这一把仍是 mutex，和核心一致 |

**`iio_buffer_block_release_atomic`（`:138-153`）+ 全局工作队列这套设计**是这里的关键花活：kref 归零时如果发现自己在原子上下文，不能直接 `dma_free_coherent`（会睡），于是把块挂到 `iio_dma_buffer_dead_blocks` 上，交给 `iio_dma_buffer_cleanup_worker`（`:124-134`，通过 `DECLARE_WORK` 在 `:136` 静态声明）去释放。这是 03 §A8 里 `iio_reenable_work_fn` 那个模式的第二次出现——**"回调可能睡但调用点是原子的"在 IIO 里两次都用 workqueue 解决**，没有第三种手法。

**arm64 相关性：零。** 全树消费者只有一个：

```bash
grep -rn --include=*.c 'devm_iio_dmaengine_buffer_setup(\|iio_dma_buffer_init(' . | grep -v 'drivers/iio/buffer/'
# → drivers/iio/adc/adi-axi-adc.c:116
```

`adi-axi-adc.c` 是 Analog Devices 的 AXI 总线 ADC（FPGA 软核），`drivers/iio/adc/Kconfig` 里它的 `depends` 没有把它限死在某个 arch 上，但 `arch/arm64/boot/dts/` 里没有任何节点用它的 compatible。所以这套 979 行的基础设施在 arm64 上是**契约齐备但无人使用**——本篇把它讲清楚的理由是"读者会在 `buffer/` 目录里看到它并想知道要不要读"，答案是 arm64 场景下可以跳过，只需记住它是 spinlock 例外的所在地。


### §1.3 callback 缓冲区：内核内的推模式

代码：`drivers/iio/buffer/industrialio-buffer-cb.c`（156 行，本篇最短的框架）。它是 IIO **唯一的"推"式内核内接口**——数据一到就调消费者的回调，和 §6 的"拉"式（消费者主动 `iio_read_channel_raw`）是两种相反的方向。

#### §1.3.1 数据结构与五个 API

`struct iio_cb_buffer`（`:14-20`）把四样东西包在一起：内嵌的 `struct iio_buffer`、消费者给的 `cb` 函数指针 + `private` 指针、`iio_channel` 数组、provider 的 `indio_dev`。

| API | 位置 | 做什么 |
|---|---|---|
| `iio_channel_get_all_cb()` | `:48-107` | 分配 + 拿全部通道 + 建 scan_mask，**不启动** |
| `iio_channel_cb_set_buffer_watermark()` | `:109-118` | 设 watermark |
| `iio_channel_start_all_cb()` | `:120-125` | 一行：`iio_update_buffers(indio_dev, &cb_buff->buffer, NULL)` |
| `iio_channel_stop_all_cb()` | `:127-131` | 一行：`iio_update_buffers(indio_dev, NULL, &cb_buff->buffer)` |
| `iio_channel_release_all_cb()` | `:133-138` | `iio_channel_release_all` + `iio_buffer_put` |

另外两个纯 getter：`iio_channel_cb_get_channels()`（`:141-145`）和 `iio_channel_cb_get_iio_dev()`（`:148-152`），存在的理由是 `struct iio_cb_buffer` 对消费者是不透明的。

#### §1.3.2 挂载点：绕过 sysfs，直接走 A9

**这是 callback 缓冲区最重要的一条结构信息。** 03 §A7 讲的那条路是"用户态写 `buffer/enable` → `enable_store` → `iio_update_buffers`"。callback 缓冲区**没有 sysfs 这一段**：`iio_channel_start_all_cb()` 直接就是 `iio_update_buffers()` 的一层薄包装（`:122-123`）。

对照 03 §A9 那张 `enable_store` vs `iio_update_buffers` 的差异表，这意味着：

| 差异点 | callback 消费者得到什么 |
|---|---|
| 锁 | `iio_update_buffers` 自己取 `info_exist_lock` + `mlock`（`drivers/iio/industrialio-buffer.c:1261-1262`），消费者不需要管 |
| `info == NULL` 检查 | 有——`iio_update_buffers` 里检查，provider 已死会返回 `-ENODEV` |
| OUT 方向缓冲区 | 会被 `iio_update_buffers` 拒掉，callback 只能收不能发 |

也就是说 callback 消费者拿到的是**和用户态写 sysfs 完全等价的语义**，除了不经过文件系统。

#### §1.3.3 全树四个消费者：两个在 input，一个在 sound，一个在 IIO 内部

```bash
grep -rn --include=*.c 'iio_channel_get_all_cb(' . | grep -v 'buffer-cb.c'   # → 5 行
```

命中 5 行但**调用点只有 4 个**——第 5 行（`sound/soc/stm/stm32_adfsdm.c:300`）是一条注释，见下文。这就是 skill 里"被引用的地方 ≠ 调用者"那条纪律的一个现成例子。

| 消费者 | 位置 | 做什么 |
|---|---|---|
| `lmp91000` 恒电位仪 | `drivers/iio/potentiostat/lmp91000.c:355` | IIO 内部消费另一个 IIO 设备 |
| 电阻式触摸屏 | `drivers/input/touchscreen/resistive-adc-touch.c:270` | ADC 采样 → input 事件 |
| ADC 摇杆 | `drivers/input/joystick/adc-joystick.c:263` | 同上 |
| STM32 ADFSDM 声卡 | `sound/soc/stm/stm32_adfsdm.c:354` | ADC → ALSA |

第四个消费者暴露了一个**接口设计上的强制项**：`sound/soc/stm/stm32_adfsdm.c:300` 的注释明确写了 "This dummy callback is requested by `iio_channel_get_all_cb()` API"——它根本不想要回调（数据走别的路），但 API 在 `:54-57` 强制 `if (!cb) return ERR_PTR(-EINVAL)`，于是它只能塞一个空函数进去。这是一个被消费者显式抱怨、且抱怨被留在源码注释里的接口摩擦点（登记见 §8.3-D3）。

四个消费者里三个在 `drivers/input/` 和 `sound/soc/`——**这是"框架的使用者不住在框架自己的目录下"的典型例子**，也是 skill 里全称断言必须跨出子系统目录 grep 的原因。

#### §1.3.4 一个可疑点：构造失败路径与 release 回调的职责重叠

`iio_channel_get_all_cb()` 的失败回滚是三个标签（`:100-105`）：

```
error_free_scan_mask:   bitmap_free(cb_buff->buffer.scan_mask);
error_release_channels: iio_channel_release_all(cb_buff->channels);
error_free_cb_buff:     kfree(cb_buff);
```

而 `.release` 回调 `iio_buffer_cb_release()`（`:33-39`）做的是 `bitmap_free(scan_mask)` + `kfree(cb_buff)`——**和前两个标签的动作重叠**。之所以不会双重释放，是因为构造路径**从来不走 `iio_buffer_put`，而是直接 `kfree`**：`iio_buffer_init()`（`drivers/iio/industrialio-buffer.c:331`）在 `:53` 已经被调过，引用计数已经建立，但失败路径绕过它直接 kfree。这和 `03-internals.md` §A2 记的 `iio_device_alloc` 失败路径"`kfree` 一个已 `device_initialize` 的 `struct device`"是**同一个模式的第二次出现**——IIO 里构造失败时绕过引用计数机制直接 kfree 是一贯做法，不是偶发。

现在它是自洽的（kref 初值为 1 且无人持有第二个引用），但它意味着：**如果将来有人在 `iio_channel_get_all_cb` 的中段插入任何会把 buffer 引用传出去的调用，这三个标签就全都变成 use-after-free**。这是设计脆弱性而不是当前缺陷（登记见 §8.3-D4）。

### §1.4 hw-consumer：只填 `.release` 的"假缓冲区"

代码：`drivers/iio/buffer/industrialio-hw-consumer.c`（213 行）。这是四个后端里最反直觉的一个：**它注册了一个 buffer，但这个 buffer 永远不装数据。**

#### §1.4.1 它到底在干什么

`struct hw_consumer_buffer`（`:28-33`）里除了内嵌的 `struct iio_buffer`，只有 `indio_dev` 和一个柔性数组 `scan_mask[]`。没有任何数据存储。

`iio_hw_consumer_alloc()`（`:83-123`）的流程：

1. `iio_channel_get_all(dev)`（`:103`）——复用 §6 的消费者框架拿到全部通道
2. 遍历通道（`:110-117`），**按 provider 分组**：每个不同的 `chan->indio_dev` 对应一个 `hw_consumer_buffer`（`iio_hw_consumer_get_buffer()` `:52-81` 先查链表，没有才新建）
3. 每个通道的 `scan_index` 位置进对应 provider 的 `scan_mask`（`:116`）

**"按 provider 分组"这一步是这个模块存在的核心理由**：一个消费者可能同时从两个不同的 ADC 芯片取通道，每个芯片要单独使能。链表 `hwc->buffers` 就是"我要打开的 provider 清单"。

然后 `iio_hw_consumer_enable()`（`:178-196`）逐个调 `iio_update_buffers(buf->indio_dev, &buf->buffer, NULL)`——**和 §1.3 callback 走的是同一个挂载点（A9）**，但目的完全不同：callback 是为了收数据，hw-consumer 是为了**让 provider 的硬件通路打开**，数据在硬件里直接流向下一级（典型是模拟前端连到另一个 ADC 的输入脚）。

因为 buffer 里永远不会有数据，所以 `.store_to` / `.read` / `.data_available` 全都不需要——这就解释了 §1.0 表里那个孤零零的"1"。

#### §1.4.2 回滚用的是核心里同一个惯用法

`iio_hw_consumer_enable()` 的失败回滚（`:189-192`）：

```
err_disable_buffers:
    list_for_each_entry_continue_reverse(buf, &hwc->buffers, head)
        iio_update_buffers(buf->indio_dev, NULL, &buf->buffer);
```

`list_for_each_entry_continue_reverse` 这个宏在 `03-internals.md` §A7.4 已经出现过一次（`drivers/iio/industrialio-buffer.c:1131-1134` 的部分回滚）。**IIO 里"多步使能、失败后精确回滚已完成的那几步"两次都用这个宏**，两处都不用计数器。区别是核心那一处需要先 `list_prepare_entry`，这里不需要——因为这里的 `buf` 在 goto 时正好停在失败的那一项上，而核心那一处是从头开始的。

#### §1.4.3 唯一消费者：STM32，arm64 上不会碰到

```bash
grep -rn --include=*.c 'iio_hw_consumer_alloc(\|devm_iio_hw_consumer_alloc(\|iio_hw_consumer_enable(' . | grep -v 'hw-consumer.c'
```

三处命中，全在同一个文件：`drivers/iio/adc/stm32-dfsdm-adc.c:1010`、`:1233`、`:1453`。`drivers/iio/adc/Kconfig` 里 STM32 DFSDM 的依赖链落在 `MFD_STM32_*` 上，STM32 是 Cortex-M / arm32 平台。**结论：arm64 上这个模块不会被加载。**

**一个 kerneldoc 冲突**：`drivers/iio/buffer/industrialio-hw-consumer.c:152` 附近的 `devm_iio_hw_consumer_alloc()` kerneldoc 写的是失败返回 NULL，但函数体 `:154-170` 返回的是 `ERR_PTR`（它转调 `iio_hw_consumer_alloc()`，后者在 `:117`、`:121` 都是 `ERR_PTR`，且自己的 kerneldoc 在 `:79` 正确写了 `ERR_PTR()`）。同一个文件里相邻两个函数的 kerneldoc 一对一错（登记见 §8.3-D5，并入 99 §rule-6 冲突表）。

### §1.5 四个后端的释放路径对比：一处不对称

| 后端 | `.release` 实现 | 释放了什么 | 谁触发 |
|---|---|---|---|
| kfifo | `drivers/iio/buffer/kfifo_buf.c:132-139` | `mutex_destroy` + `kfifo_free` + `kfree(kf)` | `iio_kfifo_free()` → `iio_buffer_put` |
| dmaengine | `drivers/iio/buffer/industrialio-buffer-dmaengine.c:104-111` | 转调 `iio_dma_buffer_exit` / `_release` 那一族 | devres |
| callback | `drivers/iio/buffer/industrialio-buffer-cb.c:33-39` | `bitmap_free(scan_mask)` + `kfree(cb_buff)` | `iio_channel_release_all_cb()` |
| hw-consumer | `drivers/iio/buffer/industrialio-hw-consumer.c:40-45` | `kfree(hw_buf)` | `iio_hw_consumer_free()` |

**不对称点：只有 callback 后端的 `scan_mask` 是单独 `bitmap_zalloc` 的**（`drivers/iio/buffer/industrialio-buffer-cb.c:90-91`），所以它的 `.release` 里要多一步 `bitmap_free`。hw-consumer 把 scan_mask 做成了结构体尾部的柔性数组（`:32` 声明、`:76-77` 用 `struct_size()` 一次 `kzalloc`），所以一个 `kfree` 就够。**两个功能相近、同一时期由不同作者写的模块，在同一个字段上做了不同的内存布局决定**——这不影响正确性，但它是"IIO 的子框架各写各的"的一个具体证据。

---

## §2 triggered-buffer 便利层：117 个消费者的默认写法

代码：`drivers/iio/buffer/industrialio-triggered-buffer.c`（139 行）。**它是本篇消费者最多的框架**，也是唯一硬依赖另一个框架（§1.1 kfifo）的框架。

官方原文见 `Documentation/driver-api/iio/triggered-buffers.rst`（69 行），它给了一份 top half / bottom half 的完整骨架示例（`:17-48`）和三条要点（`:52-65`），本节的分工说明与之一致。**但该文档有一处与代码不符**，读它时要绕开：

- `Documentation/driver-api/iio/triggered-buffers.rst:54-55` 说「若不指定 `setup_ops`，IIO 核心会用缺省的 `iio_triggered_buffer_setup_ops`」。**这个符号在核心里不存在**——全树只有三个驱动各自定义了一个同名的 `static` 结构（`drivers/iio/adc/ad7266.c:75`、`drivers/iio/adc/vf610_adc.c:765`、`drivers/iio/light/vcnl4035.c:363`）。
- 真实的兜底在别处：`industrialio-triggered-buffer.c:78` **无条件**把参数赋给 `indio_dev->setup_ops`（传 NULL 就是 NULL），兜底发生在注册时——`industrialio-core.c:1979-1981` 在「有缓冲区模式且 `setup_ops == NULL`」时指向 `noop_ring_setup_ops`，而后者在 `:1924` 是一个**没有初始化式的 `static const`**，即全零、四个回调全空。
- 这个兜底是承重的：`industrialio-buffer.c:432`、`:1074`、`:1114`、`:1136`、`:1163`、`:1180` 都直接解引用 `indio_dev->setup_ops`，没有一处判空。

结论：`setup_ops` 可以传 NULL，行为是「四个回调全不调」，而不是文档暗示的「有一套缺省实现」。

### §2.1 它合并了哪三步

`iio_triggered_buffer_setup_ext()`（`:39-98`）替驱动做三件本来要分开写的事：

| 步骤 | 位置 | 等价的手写代码 |
|---|---|---|
| 分配 kfifo 缓冲区 | `:59` | `iio_kfifo_allocate()` |
| 分配 pollfunc（触发器消费者侧的 bottom half） | `:65-71` | `iio_alloc_pollfunc(h, thread, IRQF_ONESHOT, ...)` |
| 声明能力 + attach | `:78`、`:81`、`:86` | `indio_dev->setup_ops = setup_ops` + `modes \|= INDIO_BUFFER_TRIGGERED` + `iio_device_attach_buffer()` |

中间还顺手接了两个只有 `_ext` 变体才用得上的字段：`buffer->direction = direction`（`:83`）、`buffer->attrs = buffer_attrs`（`:84`）。

**挂载点是 03 时间线的 A4**——驱动 probe 里，`iio_device_register()` 之前。这个顺序是硬性的：`indio_dev->modes` 必须在 A3 之前设好，否则核心在 03 §A3.5 检查 `INDIO_ALL_TRIGGERED_MODES` 那道门时会认为这个设备不需要 pollfunc 相关的属性。

### §2.2 `-EADDRINUSE`：一句注释解释了整个设计约束

函数体的第一件事是 `if (indio_dev->buffer) return -EADDRINUSE;`（`:56-57`），前面挂了 5 行注释（`:49-55`）说明理由：`iio_triggered_buffer_cleanup()` 假设"这里分配的 buffer 就是 `indio_dev->buffer`"，而这个假设只在**本函数是第一个 `iio_device_attach_buffer()` 调用者**时成立。

为什么"第一个"重要，要回到 `iio_device_attach_buffer()`（`drivers/iio/industrialio-buffer.c:1974-1998`）：

```
:1990    /* first buffer is legacy; attach it to the IIO device directly */
:1991    if (!indio_dev->buffer)
:1992        indio_dev->buffer = buffer;
```

`indio_dev->buffer` 是**遗留字段，只记第一个**；真正的完整清单在 `iio_dev_opaque->attached_buffers[]` 里。而 `iio_triggered_buffer_cleanup()`（`:105-109`）只认 `indio_dev->buffer`。所以如果驱动先自己 attach 了一个别的 buffer，再调 triggered-buffer setup，cleanup 时就会去 free 一个不属于它的 buffer——`-EADDRINUSE` 是把这条路堵死的最省力办法。

这是 IIO 里**"用错误码替代重构"的一个干净例子**：正确的修法是让 setup 记住自己分配的那个指针，但那要改结构体；返回 `-EADDRINUSE` 只要两行。

### §2.3 cleanup 不释放缓冲区，只降引用

`iio_triggered_buffer_cleanup()` 的全部函数体是两行（`:105-109`）：

```
iio_dealloc_pollfunc(indio_dev->pollfunc);
iio_kfifo_free(indio_dev->buffer);
```

**第二行的名字骗人。** 追一遍引用计数：

| 时刻 | 动作 | kfifo 的 `buffer->ref` |
|---|---|---|
| `iio_kfifo_allocate()` `:59` | `iio_buffer_init()` 里 `kref_init`（`drivers/iio/industrialio-buffer.c:337`） | 1 |
| `iio_device_attach_buffer()` `:86` | `buffer = iio_buffer_get(buffer)`（`drivers/iio/industrialio-buffer.c:1988`） | **2** |
| `iio_triggered_buffer_cleanup()` | `iio_kfifo_free` → `iio_buffer_put`（`drivers/iio/buffer/kfifo_buf.c:221-224`） | 1 |
| A13 `iio_dev_release` → `iio_device_detach_buffers()`（`drivers/iio/industrialio-core.c:1643`） | 逐个 `iio_buffer_put` | 0 → `.release` 真的跑 |

**结论：kfifo 的内存在 `iio_triggered_buffer_cleanup()` 返回后依然活着**，它要等到 `struct iio_dev` 的最后一个 `put_device` 才真的消失。这解释了 `iio_device_attach_buffer` 的 kerneldoc 为什么写 "The buffer stays attached to the device until the device is freed"（`drivers/iio/industrialio-buffer.c:1968-1969`）——那句话是字面意思。

**这条时序对驱动作者的实际影响**：`devm_iio_triggered_buffer_setup()` 的 devres 回调（`drivers/iio/buffer/industrialio-triggered-buffer.c:111-114`）跑在 probe 失败或 remove 时，但它并不能保证缓冲区内存已归还。真正的释放点和 `iio_device_free` 绑在一起，两者的顺序由 devres 的注册次序决定。

### §2.4 两个 `_ext` 与两个宏：四个名字，两个实现

| 名字 | 形态 | 定义位置 |
|---|---|---|
| `iio_triggered_buffer_setup_ext()` | 函数 | `drivers/iio/buffer/industrialio-triggered-buffer.c:39` |
| `iio_triggered_buffer_setup()` | 宏 → 上一行，`direction=IIO_BUFFER_DIRECTION_IN`、`buffer_attrs=NULL` | `include/linux/iio/triggered_buffer.h:20-23` |
| `devm_iio_triggered_buffer_setup_ext()` | 函数 → 调 `_ext` + `devm_add_action_or_reset` | `drivers/iio/buffer/industrialio-triggered-buffer.c:116-137` |
| `devm_iio_triggered_buffer_setup()` | 宏 → 上一行 | `include/linux/iio/triggered_buffer.h:33-36` |

**四个名字里两个是宏，这是 cbm 索引在 IIO 上的一个具体盲区**：查 `iio_triggered_buffer_setup_ext` 的调用者只能查到 6 处，而实际使用点是 45（宏）+ 6（直接）。数消费者时必须两个名字都数（重跑命令见 §8.2）。

`_ext` 后缀多出来的两个参数是 `direction`（IN / OUT）和 `buffer_attrs`（额外的 sysfs 属性）。**绝大多数驱动用宏**，也就是默认 IN 方向、无额外属性。

---

## §3 triggered-event 便利层：64 行，两个消费者

代码：`drivers/iio/industrialio-triggered-event.c`（64 行，本篇最小的框架，也是唯一住在 `drivers/iio/` 顶层而不是子目录里的框架）。

它是 §2 的**事件版孪生体**，两个函数各做一件事：

| 函数 | 位置 | 做什么 |
|---|---|---|
| `iio_triggered_event_setup()` | `:30-48` | 分配 `pollfunc_event` + `modes \|= INDIO_EVENT_TRIGGERED` |
| `iio_triggered_event_cleanup()` | `:55-59` | 清 `modes` 位 + `iio_dealloc_pollfunc` |

**和 §2 的三个差异**：

| 差异 | §2 triggered-buffer | §3 triggered-event |
|---|---|---|
| 分配缓冲区吗 | 是（kfifo） | **不分配**——事件不走缓冲区，走 `industrialio-event.c` 的独立事件队列 |
| 挂哪个字段 | `indio_dev->pollfunc` | `indio_dev->pollfunc_event`（`:34`） |
| cleanup 清 modes 位吗 | 不清 | **清**（`:57` `modes &= ~INDIO_EVENT_TRIGGERED`） |

最后一行差异值得停一下：§2 的 cleanup 不还原 `modes`，§3 的 cleanup 还原。这在正常拆除路径上无所谓（设备马上就要没了），但它是**两个孪生框架在同一个动作上做了不同决定**的又一例（和 §1.5 的 scan_mask 布局差异同类）。

**挂载点也是 A4**，但它的下游不同：`pollfunc_event` 由 03 §A6.3 的 `iio_trigger_attach_poll_func` 在 `current_trigger` 写入时挂上（这一点和 `pollfunc` 相反——`pollfunc` 要等到 buffer 使能才挂）。

### §3.1 全树只有一个消费者

```bash
grep -rn --include=*.c 'iio_triggered_event_setup(' . | grep -v 'industrialio-triggered-event.c'
# → drivers/iio/adc/hi8435.c:512
```

`setup` 和 `cleanup` 各只有一处调用点（`drivers/iio/adc/hi8435.c:512` 与 `:459`），来自同一个驱动：Holt HI-8435 阈值检测器（`drivers/iio/adc/Kconfig:470-473`，`select IIO_TRIGGERED_EVENT`、`depends on SPI`）。

**也就是说这个 64 行的框架是为一个驱动写的。** 它在 arm64 上不涉及：`grep -rn hi8435 arch/arm64/boot/dts/` 为 0，arm32 有 1 处。按 §0.2 分诊表的口径，**这个框架在 arm64 上可以完全跳过**——本节保留的理由是"框架清单要完整"，不是"读者会用到"。

这也回答了一个自然会冒出来的疑问：为什么 §2 的 triggered-buffer 有 117 个消费者、结构却比 §3 复杂那么多？因为 §3 不需要缓冲区。**IIO 的事件通道（`industrialio-event.c`）本身就自带队列**，触发器在这里只负责"什么时候去读一次阈值状态"，读出来的东西直接进事件队列。§2 那 117 个消费者是采样数据流，必须有 kfifo 承接。

---

## §4 触发器提供方：六个在 `trigger/`，十个在别处

### §4.0 一个反直觉的分布：六个文件在 `trigger/`，四十九个在别处

`drivers/iio/trigger/` 目录有 6 个文件，看起来触发器提供方就在这里。**但 `struct iio_trigger_ops` 的全树实现有 53 个，其中只有 4 个在这个目录里。**

```bash
grep -rn --include=*.c -E 'struct iio_trigger_ops [a-z_0-9]+ = \{' . | wc -l   # → 53
```

53 个的目录分布：

| 目录 | 实现数 | 说明 |
|---|---|---|
| `drivers/iio/accel/` | 13 | 加速度计的 data-ready 触发器 |
| `drivers/iio/adc/` | 11 | ADC 转换完成中断 |
| `drivers/iio/gyro/` | 6 | 陀螺仪 data-ready |
| **`drivers/iio/trigger/`** | **4** | 本节要讲的通用提供方 |
| `drivers/iio/light/` | 4 | 光传感器 |
| `drivers/iio/imu/` | 4 | 组合惯性单元 |
| `proximity/` `pressure/` `magnetometer/` `chemical/` | 各 2 | |
| `humidity/` `common/` `addac/` | 各 1 | |
| **合计** | **53** | 跨 13 个目录 |

**那 49 个"住在传感器驱动里"的触发器是所谓 data-ready 触发器**：芯片自己有个"数据准备好了"的中断引脚，驱动把它包成一个 IIO 触发器，好让同一个芯片（或别的芯片）的缓冲区能被它驱动。它们不是独立模块，随传感器驱动一起编译。

**所以"触发器提供方"这个框架的边界是 `iio_trigger_alloc` + `iio_trigger_register` 这一对 API，而不是 `trigger/` 这个目录。** 目录级的统计（6 个文件）会把这个框架的规模低估一个数量级。

**这一节踩过一个必须记下来的计数陷阱。** 第一次统计时用的模式是：

```bash
grep -rn --include=*.c 'struct iio_trigger_ops [a-z_]* = {' .     # → 13（错）
```

`[a-z_]*` 不含数字，于是所有名字里带型号数字的实现被整批漏掉——`adxl372_trigger_ops`、`stk8312_trigger_ops`、`kxcjk1013_trigger_ops`、`mcp3911_trigger_ops`、`ad7768_trigger_ops` 等等。**而传感器驱动的命名习惯恰恰就是"芯片型号 + 后缀"，所以漏掉的不是零散几个，而是一整类。** 13 和 53 之间差了 4 倍，而错的那个数看起来完全合理（"通用的 3 个 + 少量驱动自带的 10 个"），不会引起怀疑。

正确写法是把数字纳进字符类（`[a-z_0-9]+`）。这条已按 skill 第 30 条登记进 `99-evidence.md` §6 的交叉验证实例。

### §4.0.1 三个字段的填充分布

`struct iio_trigger_ops`（`include/linux/iio/trigger.h`）只有 3 个字段，53 个实现的填充情况：

| 字段 | 填了的实现数 | 语义 |
|---|---|---|
| `.set_trigger_state` | 47 | 使能/禁用触发源。**这是主字段** |
| `.validate_device` | 23 | 准入检查：这个触发器允许驱动哪个设备 |
| `.reenable` | 10 | 一次触发处理完之后重新武装（03 §A8） |

重跑命令（三条，注意范围限定在 `drivers/iio/`）：

```bash
grep -rn --include=*.c '\.set_trigger_state\s*=' drivers/iio/ | wc -l   # → 47
grep -rn --include=*.c '\.validate_device\s*=' drivers/iio/   | wc -l   # → 23
grep -rn --include=*.c '\.reenable\s*=' drivers/iio/          | wc -l   # → 10
```

**6 个实现没有 `.set_trigger_state`**，它们的 ops 只为准入而存在。`drivers/iio/trigger/stm32-lptimer-trigger.c:40-42` 是最干净的例子：整个 ops 只有一个 `.validate_device`，函数体（`:31-38`）只做一件事——检查 `indio_dev->modes & INDIO_HARDWARE_TRIGGERED`，不是就返回 `-EINVAL`。

这解释了 §4.2 会讨论的那个问题：**"有没有 ops"和"能不能被关掉"是两个独立的维度**，`iio_trigger_ops` 同时承载了"开关"和"准入"两种职责。

### §4.1 `trigger/` 六个文件的三种创建机制

| 文件 | 行数 | 创建入口 | 有 `iio_trigger_ops` 吗 | arm64 |
|---|---|---|---|---|
| `iio-trig-sysfs.c` | 225 | **sysfs 属性**：往 `add_trigger` 写一个 id | 无 | 与平台无关，会用到 |
| `iio-trig-hrtimer.c` | 200 | **configfs**（§5 的 sw-trigger） | 有（`:125`） | 与平台无关，会用到 |
| `iio-trig-loop.c` | 141 | **configfs**（§5 的 sw-trigger） | 有（`:74`） | 实验性，见 §4.4 |
| `iio-trig-interrupt.c` | 111 | **platform_driver**（要 IRQ 资源） | **无** | 需板级代码注册 platform_device，arm64 DTS 无对应 |
| `stm32-timer-trigger.c` | 918 | platform_driver | 有（`:388`） | 不涉及（`depends on ARCH_STM32`，`drivers/iio/trigger/Kconfig:41`） |
| `stm32-lptimer-trigger.c` | 118 | platform_driver | 有（`:40`，**只有 `.validate_device`**） | 不涉及（`depends on MFD_STM32_LPTIMER`，`:30`） |

**三种创建机制对应三种"谁决定这个触发器什么时候出现"**：

1. **sysfs（`iio-trig-sysfs.c`）**：用户态决定。`echo 0 > /sys/bus/iio/devices/iio_sysfs_trigger/add_trigger` 就生出一个 `sysfstrig0`。
2. **configfs（hrtimer / loop）**：用户态决定，但走 configfs 的 mkdir 语义（§5.3）。
3. **platform_driver（interrupt / stm32 两个）**：驱动模型决定，要么 DT 里有节点，要么板级代码手工注册 `platform_device`。

`iio-trig-interrupt.c` 的实际可用性要单独说：它**没有 `of_device_id` 表**（对照 `:99-105` 的 `platform_driver` 定义，只有 `.driver.name = "iio_interrupt_trigger"`），所以它不可能由 DT 匹配上，只能由板级代码显式注册。arm64 的板级代码路径已经基本消失（全部走 DT），**所以这个文件在 arm64 上是死代码**。

### §4.2 `iio-trig-interrupt.c` 为什么不需要 `iio_trigger_ops`

对照 §4.1 的表，`trigger/` 下只有两个文件完全没有 `iio_trigger_ops`（`iio-trig-sysfs.c` 与 `iio-trig-interrupt.c`，各 0 处命中）。原因在 03 §A6.1：核心在使能触发器时会调 `trig->ops->set_trigger_state`，而这个调用点是有 NULL 保护的。**中断触发器的"使能"没有意义**——`request_irq` 在 probe 时就做了（`iio-trig-interrupt.c:59-60`），中断线一直是开的，来了就 `iio_trigger_poll`（`:22-26`，三行函数体）。

这和 hrtimer 恰好相反：hrtimer 的 `set_trigger_state`（`iio-trig-hrtimer.c:110-123`）就是 `hrtimer_start` / `hrtimer_cancel` 的开关。

**所以判据要分两条**（§4.0.1 已给出全树分布）：

| 问题 | 决定 |
|---|---|
| 这个触发源能不能被关掉？ | 决定要不要 `.set_trigger_state`（47/53 要） |
| 这个触发器是否只对某类设备有意义？ | 决定要不要 `.validate_device`（23/53 要） |

两条都是"否"才真的不需要 ops——`trigger/` 下满足这个条件的只有 sysfs 和 interrupt 两个。

### §4.3 三个提供方与 PREEMPT_RT 的关系

这一节值得单列，因为本树打了全树 PREEMPT_RT 回移补丁（`99-evidence.md` §1 记了 IIO 自身未被改动）。三个通用提供方都显式选择了 hard-irq 语义：

| 提供方 | 关键调用 | 位置 | 为什么 |
|---|---|---|---|
| sysfs | `IRQ_WORK_INIT_HARD(iio_sysfs_trigger_work)` | `drivers/iio/trigger/iio-trig-sysfs.c:158` | `iio_trigger_poll()` 用 `generic_handle_irq`（03 §A8），必须在硬中断上下文里跑 |
| hrtimer | `hrtimer_init(..., HRTIMER_MODE_REL_HARD)` + `hrtimer_start(..., HRTIMER_MODE_REL_HARD)` | `drivers/iio/trigger/iio-trig-hrtimer.c:151`、`:117-118` | 同上：回调 `iio_hrtimer_trig_handler`（`:98-108`）里直接调 `iio_trigger_poll` |
| loop | 用 `iio_trigger_poll_chained()` 而不是 `iio_trigger_poll()` | `drivers/iio/trigger/iio-trig-loop.c:49` | 它跑在 kthread（进程上下文）里，只能用 `handle_nested_irq` 那个变体 |

**这张表把 03 §A8 的两个 poll 变体和它们的实际使用者对上了**：`iio_trigger_poll` 的三个使用者全部保证自己在硬中断上下文（两个靠 `_HARD` 标志、一个靠真实中断），`iio_trigger_poll_chained` 的使用者在进程上下文。**没有一个提供方是"随便选一个"的**——这是 IIO 触发器机制里少见的、约束被显式表达在代码里的地方。

`_HARD` 这两处在 PREEMPT_RT 下的含义：RT 内核默认把 `irq_work` 和 `hrtimer` 都推到线程里跑，`_HARD` 变体是"我确实要硬中断上下文"的显式声明。这两处已经写对了，**IIO 在 RT 下的触发器路径不需要额外改动**——但这个结论只覆盖这三个通用提供方，那 10 个住在传感器驱动里的 data-ready 触发器各自用的是自己的 `request_irq` 标志，不在本篇的验证范围内（未验证项登记进 99 §5）。

### §4.4 loop 触发器：源码自陈的实验品

`drivers/iio/trigger/iio-trig-loop.c` 的文件头注释（`:1-19`）是整个 IIO 里最坦白的一段：

- `:8` — "Note this is still rather experimental and may eat babies."
- `:10-14` — 两条 Todo：防止连接"需要 top half 处理器"的设备；搞清楚怎么在这个上下文里跑 top half（比如取时间戳）
- `:16-18` — 记了实测数据：在 max1363 上，线程占 33% CPU、`generic_buffer` 管到 `/dev/null` 占 20%，watermark 64 / kfifo 128 元素

第一条 Todo 是一个**当前就存在的功能缺口**：`iio_trigger_poll_chained()` 走 `handle_nested_irq`，它只会跑 threaded handler，不跑 top half（`h`）。所以一个用 `iio_triggered_buffer_setup(indio_dev, h, thread, ops)` 且 `h` 里有实质工作（典型是 `iio_pollfunc_store_time`，03 §A8.2）的驱动，接上 loop 触发器后**时间戳会是垃圾**，而且没有任何检查会拦住这个组合。

这条已在 `03-internals.md` §C2 的自陈债务表里登记过位置，本节补上"缺口的具体后果是什么"。

---

## §5 configfs 软件框架：三个文件、一对孪生实现、四个幽灵声明

三个文件构成这个框架，总共 316 行：

| 文件 | 行数 | 职责 |
|---|---|---|
| `drivers/iio/industrialio-configfs.c` | 48 | 只做一件事：在 configfs 里挂出 `/config/iio/` 这个根 |
| `drivers/iio/industrialio-sw-device.c` | 172 | `/config/iio/devices/<type>/` 下的 mkdir → 造一个 IIO 设备 |
| `drivers/iio/industrialio-sw-trigger.c` | 176 | `/config/iio/triggers/<type>/` 下的 mkdir → 造一个 IIO 触发器 |

Kconfig 依赖是单向的：`IIO_SW_DEVICE`（`drivers/iio/Kconfig:49-51`）和 `IIO_SW_TRIGGER`（`:57-59`）各自 `select IIO_CONFIGFS`。**两个 sw-* 之间没有依赖**，可以只开一个。

### §5.1 根节点：48 行里最值得看的是那个静态初始化的 mutex

`industrialio-configfs.c` 的全部实质内容是一个静态变量（`:21-29`）：

```c
struct configfs_subsystem iio_configfs_subsys = {
	.su_group = { .cg_item = { .ci_namebuf = "iio",
				   .ci_type = &iio_root_group_type, } },
	.su_mutex = __MUTEX_INITIALIZER(iio_configfs_subsys.su_mutex),
};
EXPORT_SYMBOL(iio_configfs_subsys);
```

三个点：

1. **`iio_root_group_type`（`:17-19`）只填了 `.ct_owner`**——没有 `ct_group_ops`，所以 `/config/iio/` 这一级**不能 mkdir**。目录只能由内核用 `configfs_register_default_group()` 挂进来，这就是 `devices/` 和 `triggers/` 的来路。
2. **`__MUTEX_INITIALIZER` 而不是 `mutex_init`**：这个 mutex 必须在 `configfs_register_subsystem()` 之前就可用，而后者在 `module_init` 里跑（`:32-37`）。用静态初始化就不需要一个"init 之前的 init"。
3. **它是 `EXPORT_SYMBOL` 的（`:30`）**——两个 sw-* 模块要拿这个符号去挂自己的默认组（`industrialio-sw-device.c:164`、`industrialio-sw-trigger.c` 对应位置）。这是 IIO 里少见的**导出一个变量而不是函数**的地方。

### §5.2 device 与 trigger：结构完全对称的一对孪生体

两个文件是逐函数对应的。这张表把它们并排放，**注意最后一行**：

| 职责 | sw-device.c | sw-trigger.c |
|---|---|---|
| 注册类型（把 `iio_sw_*_type` 挂上链表 + 建 configfs 目录） | `:43-65` | `:43-69` |
| 注销类型 | `:68-79` | `:72-83` |
| 按名字创建一个实例（调 `dt->ops->probe`） | `:96-116` | 对应位置 |
| 销毁一个实例（调 `dt->ops->remove`） | `:119-125` | 对应位置 |
| configfs `mkdir` 钩子 | `device_make_group` `:128-140` | `trigger_make_group` |
| configfs `rmdir` 钩子 | `device_drop_group` `:142-149` | `trigger_drop_group` |
| `configfs_group_operations` | `:151-154` | 对应位置 |
| **注册失败时回滚链表** | **没有**（`:61-62`） | **有**（`:61-66`） |

结构体定义也是对称的：`include/linux/iio/sw_device.h:22-28` 与 `sw_trigger.h:22-28` 字段名逐个一致（`name` / `owner` / `ops` / `list` / `group`）。

**类型引用计数走 `try_module_get`。** `iio_get_sw_device_type()`（`:87-91`）在持锁的情况下 `try_module_get(dt->owner)`，`iio_sw_device_destroy()`（`:124`）`module_put`。这是"用户态 mkdir 出来的对象持有实现模块的引用"——有一个 hrtimer 触发器活着，`iio-trig-hrtimer.ko` 就 rmmod 不掉。

### §5.3 D6：`iio_register_sw_device_type` 注册失败时把自己留在链表上

这是 §5.2 表格最后一行的展开，也是本篇发现的第一个**内存安全级别**的缺陷。

两段代码并排（唯一的差别就是那对花括号里的三行）：

```c
/* industrialio-sw-device.c:59-64 */          /* industrialio-sw-trigger.c:59-68 */
d->group = configfs_register_...              t->group = configfs_register_...
if (IS_ERR(d->group))                         if (IS_ERR(t->group)) {
	ret = PTR_ERR(d->group);                      mutex_lock(&iio_trigger_types_lock);
                                                  list_del(&t->list);
return ret;                                       mutex_unlock(&iio_trigger_types_lock);
                                                  ret = PTR_ERR(t->group);
                                              }
                                              return ret;
```

**后果链**（每一步都可查）：

1. `list_add_tail(&d->list, &iio_device_types_list)` 已经在 `:53` 执行过了
2. `configfs_register_default_group()` 失败 → `ret != 0`，但 `d->list` 还挂在全局链表上，且 `d->group` 是一个 ERR_PTR
3. 调用者是宏 `module_iio_sw_device_driver`（`include/linux/iio/sw_device.h:16-18`）展开出来的 `module_init`，它拿到非零返回值就让**模块加载失败**
4. 模块被卸载 → `struct iio_sw_device_type` 所在的内存（在模块的 `.data` 段里）被释放
5. **`iio_device_types_list` 上留下一个指向已释放模块内存的表项**

任何后续对这个链表的遍历（`__iio_find_sw_device_type()`，`:32-41`，被 `iio_register_sw_device_type` 和 `iio_get_sw_device_type` 调用）都会踩到它。**触发条件是 configfs 目录创建失败**——重名或 `-ENOMEM`。

**为什么说这确实是缺陷而不是我看漏了**：孪生文件在同一个位置做了回滚。两份代码是同一个作者在同一年提交的（`industrialio-configfs.c:5` 与 `sw_device.h:5` 分别是 2015 / 2016 Intel），sw-device 是照着 sw-trigger 抄的、抄漏了三行。这不是设计选择的差异。

**可利用性很低**（要模块加载期的 configfs 失败），但它是本篇唯一一处"错误路径留下悬垂指针"的地方，登记为 D6。

### §5.4 D7：四个只有声明、没有定义的函数

两个头文件各声明了一对 configfs 注册函数：

| 声明 | 位置 | 全树定义数 |
|---|---|---|
| `iio_sw_device_type_configfs_register` | `include/linux/iio/sw_device.h:54` | **0** |
| `iio_sw_device_type_configfs_unregister` | `include/linux/iio/sw_device.h:55` | **0** |
| `iio_sw_trigger_type_configfs_register` | `include/linux/iio/sw_trigger.h:54` | **0** |
| `iio_sw_trigger_type_configfs_unregister` | `include/linux/iio/sw_trigger.h:55` | **0** |

重跑命令（四个名字各跑一次，命中数应当恰好是 1，即声明本身）：

```bash
grep -rn --include=*.c --include=*.h 'iio_sw_device_type_configfs_register' .
```

**这四行是幻影 API**：它们长得像公开接口（在导出头文件里、名字规整、和真实的 `iio_register_sw_device_type` 只差一个中缀），但树里没有任何定义。照着头文件写代码的驱动作者会拿到一个**链接错误**而不是编译错误——错误信息里只有符号名，看不出"这个函数从来没存在过"。

这正好是 skill 里"符号存在 ≠ 读者能用"那条纪律的反向案例：这里连"存在"都不成立，而**唯一能发现它的动作就是对头文件里的每个声明反查定义**。登记为 D7，并在 `05-usage.md` 的近邻符号节里点名（避免读者抄进去）。

### §5.5 三个实现者，全都靠宏注册

`struct iio_sw_device_type` / `iio_sw_trigger_type` 的全树实现只有 3 个：

| 实现 | 位置 | 类型 | 注册方式 |
|---|---|---|---|
| `iio_trig_hrtimer` | `drivers/iio/trigger/iio-trig-hrtimer.c:189-193` | sw_trigger | `module_iio_sw_trigger_driver(...)` `:195` |
| `iio_trig_loop` | `drivers/iio/trigger/iio-trig-loop.c:130-134` | sw_trigger | `module_iio_sw_trigger_driver(...)` `:136` |
| `iio_dummy_device` | `drivers/iio/dummy/iio_simple_dummy.c:712-716` | sw_device | `module_iio_sw_device_driver(...)` `:718` |

**注意第三行：sw-device 框架在整棵树上只有一个使用者，而它是 `drivers/iio/dummy/` 下的示例驱动。** 也就是说 `industrialio-sw-device.c` 这 172 行代码没有一个生产用途的消费者。它存在的意义是"用户态可以 mkdir 出一个假传感器来测应用"（`drivers/iio/Kconfig:53-55` 的 help 文字也是这么写的）。

**这一节是 macro 盲区的教科书案例**：

```bash
grep -rn --include=*.c 'iio_register_sw_trigger_type' .   # → 只有 3 处，全在框架自己的文件和头文件里
```

直接查注册函数**一个真实使用者都查不到**，因为三个实现全部通过 `module_iio_sw_*_driver` 宏注册。正确的口径是查 `struct iio_sw_*_type ... = {` 的赋值点（见上表）。cbm 索引在这里同样失效——`module_driver` 是二层宏展开。本条已按 skill 要求登记进 `99-evidence.md` §3 的索引盲区清单。

### §5.6 arm64 上会碰到吗

**只在你主动 mount configfs 并手工 mkdir 时会。** 三条判据：

- 没有任何 DT 绑定会创建 sw-device 或 sw-trigger（`Documentation/devicetree/bindings/iio/` 下无对应文件）
- 唯一的 sw_device 实现是 `drivers/iio/dummy/`，`CONFIG_IIO_SIMPLE_DUMMY` 默认关
- 两个 sw_trigger 实现（hrtimer / loop）在 arm64 上可用，但都要 `mkdir /config/iio/triggers/hrtimer/trig0` 这样的显式动作

所以 §0.2 分诊表里这三行标的是"仅在手工使能 configfs 时"。**它不是"arm64 不涉及"，而是"不会自己出现"**——两者的区别在排查时很重要：看到 `/sys/bus/iio/devices/trigger0` 却找不到对应硬件时，configfs 是第一个要查的地方。

---

## §6 内核内消费者框架：110 个树外调用点，arm64 上最重要的一个

代码：`drivers/iio/inkern.c`（1004 行，本篇最大的单文件框架）。

**这是 §0.2 分诊表里唯一一个"arm64 上一定会碰到"且"消费者绝大多数在 IIO 之外"的框架。** 它回答的问题是：一个温度控制器驱动（`drivers/thermal/`）怎么读一个 ADC 通道，而不需要经过用户态。

### §6.1 三条获取通道的路径

`iio_channel` 是这个框架的句柄（定义在 `include/linux/iio/consumer.h`）。拿到它有三条互不相干的路径：

| 路径 | 入口 | 依赖什么 | arm64 |
|---|---|---|---|
| **fwnode / DT** | `fwnode_iio_channel_get_by_name()` `drivers/iio/inkern.c:257-288` | 消费者节点里的 `io-channels` + 提供方节点里的 `#io-channel-cells` | **主路径**（63 个 DTS 文件在用） |
| **板级 map 表** | `iio_channel_get()` `:381-396` → `iio_channel_get_sys()` `:328-379` | 提供方驱动在 probe 里调 `iio_map_array_register()` 注册一张字符串映射表 | 少数 MFD/PMIC 走这条 |
| **取全部通道** | `iio_channel_get_all()` `:449-524` | 上面两者之一 | 少数（典型是 hwmon 一次拿完） |

DT 路径还有一个容易漏掉的第三方属性：`io-channel-ranges`（`drivers/iio/inkern.c:274`）。`fwnode_iio_channel_get_by_name()` 在当前节点找不到通道时，会沿 `fwnode_for_each_parent_node` 向上走（`:273-284`），**但只有在父节点带 `io-channel-ranges` 时才继续**，否则立刻 `-ENODEV`（`:274-277`）。这让"子节点继承父节点声明的 ADC 通道"成为可能，代价是多一条静默失效路径——忘写这个属性时症状是 `-ENODEV`，和"通道名拼错"完全一样。

**第二条路径是"没有 DT 时怎么办"的历史遗留**，`struct iio_map`（含 `consumer_dev_name` / `consumer_channel` / `adc_channel_label` 三个字符串）靠字符串匹配把两边缝起来。它的注册者在树里有 10 个以上，全部是 PMIC / MFD 里带的 ADC：

`drivers/iio/adc/axp20x_adc.c:721`、`da9150-gpadc.c:349`、`rn5t618-adc.c:245`、`ltc2497-core.c:207`、`intel_mrfld_adc.c:216`、`sun4i-gpadc-iio.c:594`、`mp2629_adc.c:142`、`axp288_adc.c:288`、`lp8788_adc.c:177`、`drivers/iio/dac/m62332.c:205`。

**这两条路径在同一棵树上同时活着，而且互相看不见**：DT 路径查 `iio_bus_type` 上的设备（`:158` `bus_find_device_by_fwnode`），map 路径查全局链表 `iio_map_list`（`:340`）。一个提供方走了哪条，取决于它自己 probe 里做了什么。

### §6.2 D8：`__fwnode_iio_channel_get` 是唯一一处无锁裸解引用 `->info`

这一节值得单列，因为它是本篇找到的**第二个真缺陷**，而且它的对照组恰好证明了 IIO 在别处是小心的。

先看正确的模式。以 `iio_read_channel_raw()`（`:593-609`）为代表，**每一个公开的读写 API 都是同一个三段式**：

```c
mutex_lock(&iio_dev_opaque->info_exist_lock);      /* :598 */
if (!chan->indio_dev->info) {                      /* :599 —— 提供方已经 unregister 了 */
	ret = -ENODEV;
	goto err_unlock;
}
ret = iio_channel_read(chan, val, NULL, ...);      /* :604 */
```

`inkern.c` 里 `info_exist_lock` 出现 18 次，正好是 9 对 lock/unlock：`:598/:606`、`:617/:625`、`:716/:725`、`:737/:745`、`:763/:784`、`:825/:833`、`:909/:917`、`:929/:937`、`:960/:968`。**这 9 对覆盖了全部会 deref `->info` 的公开入口**（读 raw / 读 average_raw / raw 转 processed / 读属性 / 读 processed_scale / 读 avail / 读 max / 取类型 / 写属性）。

`->info` 被清空的地方在 03 §A13：`iio_device_unregister()` 持同一把锁把 `indio_dev->info = NULL`。所以这个检查的语义是"提供方可能在我持有 channel 句柄期间消失了"。

**现在看例外**（`:164-167`）：

```c
indio_dev = dev_to_iio_dev(idev);
channel->indio_dev = indio_dev;
if (indio_dev->info->fwnode_xlate)          /* :166 —— 没有锁，没有 NULL 检查 */
```

上一行（`:158`）刚做完 `bus_find_device_by_fwnode(&iio_bus_type, iiospec.fwnode)`。**这中间没有任何东西阻止提供方在这一刻 unregister**：设备在 `iio_bus_type` 上还在（`device_del` 尚未跑完）、引用计数已经被 `bus_find_device_by_fwnode` 加上了，但 `->info` 已经被置 NULL。结果是 `:166` 空指针解引用。

**为什么这不是我看错了**（三条对照）：

1. 同一个文件里其他 9 处全部先取锁再判 NULL——**框架自己承认这个竞态存在**
2. 这里连"取锁"都没有，不是"取了锁但忘了判 NULL"
3. `dev_to_iio_dev()` 只是 `container_of`，不做任何有效性检查

**为什么实际上很难触发**：这条路径跑在消费者驱动的 probe 里，而 `-EPROBE_DEFER`（`:161`）说明正常时序是"提供方先于消费者 probe 完成"。要撞上它需要提供方在消费者 probe 的窗口里被 unbind（`echo > /sys/bus/.../unbind`）。**它是"可以手工触发的空指针",不是"会自己发生的崩溃"。** 登记为 D8。

**反例要留在文档里**：我最初怀疑的是另一处——`iio_channel_read()`（`:561-591`）在 `:564` 就把 `chan->indio_dev->info` 装进局部变量，看起来是同一类问题。逐个查它的调用者之后这个假设被推翻了：它是 `static`，全部调用点都在上面那 9 对锁里面。**"看起来像无锁 deref" 和 "是无锁 deref" 的区别只能靠数调用者，不能靠读函数体。**

### §6.3 引用计数：channel 句柄持有提供方设备

三条路径的引用计数模型是统一的，但**加引用的位置在三个不同的地方**：

| 路径 | 加引用 | 位置 | 减引用 |
|---|---|---|---|
| 板级 map | `iio_device_get(c->indio_dev)` | `drivers/iio/inkern.c:346`（**在 `iio_map_list_lock` 里面**） | `iio_channel_release()` `:398-405` |
| fwnode | `bus_find_device_by_fwnode()` 自带 | `:158` | 同上（`iio_device_put` = `put_device`） |
| 失败回滚 | — | `:160` `fwnode_handle_put`、`:178` `iio_device_put` | — |

`iio_channel_release()`（`:398-405`）做两件事：`iio_device_put(chan->indio_dev)` 然后 `kfree(chan)`。**句柄本身是 `kmalloc` 出来的，不是嵌在别的结构里**，所以忘了 release 就是双重泄漏（一个 `struct iio_channel` + 一个设备引用）。

devm 版本是 `devm_iio_channel_get()`（`:412-428`）和 `devm_iio_channel_get_all()`（`:543-558`）。**注意 `iio_channel_get_all()` 的返回值是一个数组而不是单个句柄**，配套的释放函数是 `iio_channel_release_all()`（`:526-535`），它靠 `while (chan->indio_dev)`（`:530`）走到数组末尾——**哨兵是 `indio_dev` 为 NULL 的那一项**。两套 API 的 release 不能混用：拿 `iio_channel_release()` 去释放数组，只会放掉第 0 项的引用并 `kfree` 整个数组，剩下 N-1 个设备引用永久泄漏。

### §6.4 消费者分布：110 处，7 个目录，34 个文件，全在 IIO 之外

按 skill 第 19 条"框架的使用者不一定住在框架自己的目录下"的要求，统计口径必须排掉 IIO 自己。**这里的 `sed` 不是装饰**：GNU grep 递归搜索时会给路径加 `./` 前缀，直接写 `grep -v '^drivers/iio/'` 会一个都过滤不掉，而 `grep -v '^./drivers/iio/'` 里的 `.` 是通配符、语义又不对。先归一化前缀最省事：

```bash
grep -rn --include=*.c -E 'iio_(read|write)_channel_[a-z_]+\(|iio_channel_get(_all)?\(' . \
  | sed 's|^\./||' | grep -v '^drivers/iio/' \
  | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn
```

| 目录 | 调用点 | 文件数 | 典型消费者 |
|---|---|---|---|
| `drivers/power/` | **84** | 21 | 全部在 `power/supply/`：电池充电器读电压/电流/温度 |
| `drivers/input/` | 7 | 4 | 摇杆、电阻触摸屏、加速度键 |
| `sound/soc/` | 5 | 1 | STM32 DFSDM（和 §1.4 hw-consumer 是同一个驱动） |
| `drivers/thermal/` | 5 | 3 | 热敏电阻当温度传感器 |
| `drivers/hwmon/` | 5 | 2 | `iio_hwmon.c`、`ntc_thermistor.c`：把 IIO 通道再导出成 hwmon 接口 |
| `drivers/phy/` | 2 | 1 | Motorola USB PHY 读 ID 引脚电压 |
| `drivers/extcon/` | 2 | 1 | `extcon-adc-jack.c`：耳机插孔检测 |
| **合计** | **110** | **34** | 跨 7 个目录 |

**`drivers/power/` 一家占 76%（84/110），这个偏斜决定了这个框架的实际形态**：它主要不是"通用的传感器读取接口"，而是"电源管理子系统读 ADC 的专用通道"。

这个偏斜在 API 的使用频次上也看得见（同一条 `sed` + `grep -v` 口径，并带 `-w`）：

| 读取 API | 树外调用点 | 语义 |
|---|---|---|
| `iio_read_channel_processed` | **45** | 已换算成标准单位（乘过 scale、加过 offset） |
| `iio_read_channel_raw` | 7 | 原始寄存器值 |
| `iio_read_channel_processed_scale` | 3 | 换算后再乘一个自定义系数 |
| `iio_read_channel_attribute` | **0** | 读任意 `IIO_CHAN_INFO_*`——只有 IIO 内部在用 |
| `iio_read_channel_scale` | **0** | 同上 |

**`processed` 是 `raw` 的六倍多**——消费者要的是"多少毫伏 / 多少毫摄氏度"，不是"哪个码值"。写文档时容易反过来（`raw` 名字更基础、更像主 API），这里的数字是反例。

最后两行同样值得注意：`iio_read_channel_attribute()` 和 `iio_read_channel_scale()` 虽然都是 `EXPORT_SYMBOL_GPL` 的公开 API（`drivers/iio/inkern.c:731`、`:797`），**树外一个调用者都没有**。它们的实际角色是给 `processed` 那一族当内部实现（`:793`、`:799` 都是转手调 `iio_read_channel_attribute`）。这是"导出了但没人用"的一类，不是缺陷，但读者不该以为它们是常规入口。

arm64 DTS 侧的两个数字：

```bash
grep -rln 'io-channels' arch/arm64/boot/dts/ | wc -l         # → 63 个文件
grep -rln '#io-channel-cells' arch/arm64/boot/dts/ | wc -l   # → 49 个文件
```

**提供方（49）少于消费者（63）是正常的**：一个 ADC 节点可以被多个消费者引用。这两个数字是 §0.2 分诊表里"arm64 一定会碰到"这个判定的依据。

### §6.5 一处自陈缺口

`iio_channel_read_max()`（`:856-901`）里有一条 FIXME（`:894`）：

```
/* FIXME: learn about max for other iio values */
```

上下文是这个函数只实现了 `IIO_VAL_INT` 和 `IIO_VAL_INT_PLUS_MICRO` 两种 `iio_available_type`，其余类型走 `default: return -EINVAL`。**也就是说 `iio_read_max_channel_raw()`（`:903-921`）对小数格式的通道会直接失败**，而失败原因和"这个通道不支持 avail"是同一个错误码。这条已在 `03-internals.md` §C2 的自陈债务表登记位置，本节补上后果。

---

## §7 通道变换层：两个"假 IIO 设备"

两个驱动，一个共同形态：**它们注册成 IIO 设备，但自己没有硬件**——每次读取都转手去读另一个 IIO 通道（走 §6 的消费者 API），把结果变换一下再交出去。

| 驱动 | 行数 | 变换什么 | Kconfig |
|---|---|---|---|
| `drivers/iio/multiplexer/iio-mux.c` | 461 | 一个物理通道 → N 个逻辑通道（切模拟开关） | `IIO_MUX`，`drivers/iio/multiplexer/Kconfig:9-11`（`select MULTIPLEXER`） |
| `drivers/iio/afe/iio-rescale.c` | 611 | 数值线性变换（分压、采样电阻、RTD 曲线） | `IIO_RESCALE`，`drivers/iio/afe/Kconfig:9-10` |

**它们同时是 §6 的消费者和 §6 的提供方**，这是本篇唯一一处框架自己叠自己的地方：`iio-mux` 的 `mux_read_raw()`（`:88-116`）里调 `iio_read_channel_raw(mux->parent, ...)`，而它注册出来的通道又能被别人 `iio_read_channel_raw()`。挂载点因此是**两个**：作为消费者挂在 03 §A4 之前（probe 里拿父通道），作为提供方挂在 03 §A3（`iio_device_register`）。

### §7.1 mux：`cached_state` 与 ext_info 重放

`iio_mux_select()`（`:40-81`）是这个驱动的核心，42 行里做了两件不显然的事：

1. **先 select 再判缓存**（`:47` → `:54`）。`mux_control_select_delay()` 是 multiplexer 子系统的加锁 + 切换 + 延时；它必须先跑，因为它同时承担"独占这个 mux"的职责。**判 `cached_state` 只是为了跳过第 2 步，不是为了跳过切换。**
2. **ext_info 重放**（`:57-77`）。IIO 通道的 `ext_info` 属性（比如"这一路要不要开偏置"）写下去时只影响当前选中的那一路。mux 把每个子通道的 ext_info 值缓存在 `child->ext_info_cache[i]` 里，**每次切到这一路就把缓存值重写一遍到父通道上**（`:67-69` `iio_write_channel_ext_info`）。`cache->size < 0` 表示这个属性从没被写过，跳过（`:64-65`）。

第 2 点是"上游硬件只有一份寄存器、下游要看起来像 N 份"的通用解法。**代价是每次切换都可能有 N 次 ext_info 写入**，而任一次失败都要 `mux_control_deselect` + 把 `cached_state` 置 -1（`:71-75`）——**失效状态必须显式作废**，否则下一次会以为父通道还带着上一路的配置。

`cached_state = -1` 在三个地方出现（`:50`、`:73`，加上 probe 时的初始化），全部是错误路径。**这是本篇唯一一处用"魔数 -1 表示缓存无效"的地方**；它能工作是因为 `chan->channel` 恒为非负。

DT 匹配：`mux_match[]` 只有一个 compatible `"io-channel-mux"`（`:444-448`）。

### §7.2 rescale：五个 compatible，一张配置表

`iio-rescale.c` 的结构比 mux 规整：五个 compatible 各对应一个 `rescale_cfg[]` 表项（`:483-504`），表项只有两个字段——输出通道类型 `type` 和一个读 DT 属性的函数 `props`：

| compatible | 输出类型 | props 函数 | 读什么 DT 属性 |
|---|---|---|---|
| `current-sense-amplifier` | `IIO_CURRENT` | `rescale_current_sense_amplifier_props` `:322-359` | `sense-resistor-micro-ohms`（必需 `:331`）、`sense-gain-mult` `:338`、`sense-gain-div` `:339` |
| `current-sense-shunt` | `IIO_CURRENT` | `rescale_current_sense_shunt_props` `:361-380` | `shunt-resistor-micro-ohms` `:368` |
| `voltage-divider` | `IIO_VOLTAGE` | `rescale_voltage_divider_props` `:382-407` | `output-ohms` `:388`、`full-ohms` `:395` |
| `temperature-sense-rtd` | `IIO_TEMP` | `rescale_temp_sense_rtd_props` `:409-448` | `excitation-current-microamp` `:419`、`alpha-ppm-per-celsius` `:427`、`r-naught-ohms` `:434` |
| `temperature-transducer` | `IIO_TEMP` | `rescale_temp_transducer_props` `:450-481` | `sense-offset-millicelsius` `:458`、`sense-resistor-ohms` `:459`、`alpha-ppm-per-celsius` `:460` |

五个 props 函数在 `:322-481`，`rescale_match[]` 在 `:506-518`。**这是本篇唯一一处"一个驱动通过 `of_device_id.data` 分派出五种设备行为"的结构**——比五个独立驱动省，代价是 `rescale_probe()`（`:521-598`）里要处理五种属性组合。

数值变换的两个函数值得单点名，因为它们被别的驱动复用：

```
rescale_process_scale()   drivers/iio/afe/iio-rescale.c:22-110   EXPORT_SYMBOL_NS_GPL(..., IIO_RESCALE)
rescale_process_offset()  drivers/iio/afe/iio-rescale.c:112-144  同上
```

**`EXPORT_SYMBOL_NS_GPL` + 命名空间 `IIO_RESCALE`** 意味着用它的模块必须写 `MODULE_IMPORT_NS(IIO_RESCALE)`，否则模块加载会失败。这是"我导出这个符号，但只给知道自己在干什么的人用"的表达方式。

这里要收一句：**命名空间导出在本篇涉及的框架里只有这一处，但在 `drivers/iio/` 整体上是常见做法**——`grep -rn 'EXPORT_SYMBOL_NS' drivers/iio/ | wc -l` 有 100+ 处，形如 `IIO_ST_SENSORS`、`IIO_AD_SIGMA_DELTA`、`IIO_ADI_AXI`、`LTC2497`，全部是"一个芯片家族的公共代码导给同族驱动"。所以不要把它当成 rescale 的特殊设计，它是 IIO 里"库型驱动"的标准写法。

`rescale_process_scale()` 有 89 行，全部在处理 `IIO_VAL_*` 的各种定点格式两两组合下怎么不丢精度、不溢出。**它是本篇计算密度最高的函数，也是唯一一处需要关心整数溢出的地方**。

### §7.3 arm64 上基本用不到：两道门的实际输出

按 skill 第 19 条，否定性断言要过两道门（grep 目标架构 + grep 对照架构）。口径是**文件数**（`grep -rl ... | wc -l`）：

| compatible | arm64 DTS | arm32 DTS | 结论 |
|---|---|---|---|
| `io-channel-mux` | **1** | 4 | arm64 有唯一一个用户 |
| `current-sense-amplifier` | 0 | 0 | 全树 DTS 无用户 |
| `current-sense-shunt` | 0 | 2 | arm64 无 |
| `voltage-divider` | 0 | 3 | arm64 无 |
| `temperature-sense-rtd` | 0 | 0 | 全树 DTS 无用户 |
| `temperature-transducer` | 0 | 0 | 全树 DTS 无用户 |

arm64 上唯一的用户是：

```
arch/arm64/boot/dts/rockchip/rk3566-anbernic-rgxx3.dtsi:80:  compatible = "io-channel-mux";
```

一台掌机的摇杆——一路 SARADC 通道靠模拟开关切出多个轴。**`iio-rescale` 在 arm64 DTS 上零用户**，这是 §0.2 分诊表里把它标成"arm64 不涉及"的依据。

**一个必须点出来的假命中**：`grep -rn 'voltage-divider' arch/arm64/boot/dts/` 并不是空的，命中在 `arch/arm64/boot/dts/freescale/imx8m*-venice-*.dts` 一类文件里，形如 `gw,voltage-divider-ohms`。**那是 Gateworks 板上另一个驱动的私有属性，不是 `iio-rescale` 的 compatible。** 所以上表的 grep 必须带引号精确匹配 `"voltage-divider"`（连同两侧的双引号），否则会得出"arm64 用了 rescale"的错误结论。这类子串包含正是 skill 第 30 条要防的计数陷阱。

---

## §8 汇总

### §8.1 框架 × 职责 × 挂载点 × arm64

| 章节 | 框架 | 职责 | 挂在 03 时间线的哪一点 | arm64 是否相关 |
|---|---|---|---|---|
| §1.1 | kfifo 缓冲区后端 | 软件 FIFO，`iio_buffer_access_funcs` 的默认实现 | A4（probe 里分配）+ A9/A10（使能与读取） | **是**（§2 的 117 个消费者全部经过它） |
| §1.2 | DMA / dmaengine 后端 | 硬件 DMA 搬运，两层 ops | 同上，但 A9 走 `iio_dma_buffer_enable` | 否（全树唯一消费者是 `drivers/iio/adc/adi-axi-adc.c:116`） |
| §1.3 | callback 缓冲区 | 让内核内消费者收到缓冲区数据 | **绕过 sysfs 直接进 A9** | **是**（4 个消费者，2 个在 `drivers/input/`） |
| §1.4 | hw-consumer | 借核心的缓冲区机制打开提供方硬件通路（"假缓冲区"） | A9 的 enable 序列 | 否（唯一消费者 `drivers/iio/adc/stm32-dfsdm-adc.c`） |
| §2 | triggered-buffer 便利层 | 把 kfifo + pollfunc + modes 三步合并 | **A4**，必须在 A3 之前 | **是**（117 个消费者，本篇之最） |
| §3 | triggered-event 便利层 | 事件版孪生体，只分配 `pollfunc_event` | A4 | 否（唯一消费者 `drivers/iio/adc/hi8435.c`） |
| §4 | 触发器提供方族 | 生产触发信号（sysfs / hrtimer / loop / 中断 / STM32 定时器） | **A6**（`current_trigger` 写入时被 attach） | **是**（sysfs 与 hrtimer 两个与平台无关） |
| §5 | configfs 软件框架 | 用户态 mkdir 造设备/触发器 | 独立于 A1–A14，走 configfs 自己的生命周期 | 仅在手工 mount configfs 时 |
| §6 | 内核内消费者框架 | 别的子系统在内核里读 IIO 通道 | **A3 之后**（提供方注册完才可用）；提供方消失时靠 `info_exist_lock` 兜 | **是**（110 个树外调用点，最重要） |
| §7 | 通道变换层（mux / rescale） | 既是消费者又是提供方，做通道复用与数值变换 | 消费者侧 A4 之前 + 提供方侧 A3 | mux：**是**（arm64 有 1 个 DTS 用户）；rescale：否 |

**三个不在本篇的相关内容**：核心缓冲区机制（`industrialio-buffer.c`）在 03 Part A；触发器核心（`industrialio-trigger.c`）在 03 §A6/§A8；事件通道（`industrialio-event.c`）在 03 Part B。

### §8.2 每个框架的全树使用统计

**统计口径**（三条，全篇一致）：(1) 数的是**调用点行数**，不是函数数；(2) 一律 `sed 's|^\./||'` 归一化路径前缀后再 `grep -v` 排除框架自身；(3) 宏包装的 API 必须把宏名和被包装函数名分别统计——`iio_triggered_buffer_setup` 与 `_ext` 是两个不同的数。

| 框架 | 统计对象 | 调用点 | 文件数 | 重跑命令 |
|---|---|---|---|---|
| §1.1 kfifo（裸） | `iio_kfifo_allocate(` | 1 | 1 | `grep -rn --include=*.c 'iio_kfifo_allocate(' . \| grep -v 'buffer/kfifo_buf.c'` |
| §1.1 kfifo（devm） | `devm_iio_kfifo_buffer_setup` + `_ext` | 16 | 16 | `grep -rnE --include=*.c 'devm_iio_kfifo_buffer_setup(_ext)?\(' . \| grep -v 'drivers/iio/buffer/'` |
| §1.2 DMA | `iio_dmaengine_buffer_*` 全族 | **1** | 1 | `grep -rnE --include=*.c '(devm_)?iio_dmaengine_buffer_setup(_ext)?\(' . \| grep -v 'drivers/iio/buffer/'` |
| §1.3 callback | `iio_channel_get_all_cb(` | 4（命中 5，含 1 条注释） | 4 | `grep -rn --include=*.c 'iio_channel_get_all_cb(' . \| grep -v 'buffer-cb.c'` |
| §1.4 hw-consumer | `iio_hw_consumer_*` 全族 | 6 | **1** | `grep -rnE --include=*.c '(devm_)?iio_hw_consumer_(alloc\|free\|enable\|disable)\(' . \| grep -v 'drivers/iio/buffer/'` |
| §2 triggered-buffer | `iio_triggered_buffer_setup`（宏） | 45 | 44 | `grep -rn --include=*.c -w 'iio_triggered_buffer_setup' . \| grep -v 'industrialio-triggered-buffer'` |
| §2 triggered-buffer | `devm_iio_triggered_buffer_setup`（宏） | **72** | 72 | 同上，换 `devm_` 前缀 |
| §2 triggered-buffer | `iio_triggered_buffer_setup_ext`（函数直接） | 2 | — | 同上，换 `_ext` 后缀 |
| §2 triggered-buffer | `devm_iio_triggered_buffer_setup_ext`（函数直接） | 4 | — | 同上 |
| §3 triggered-event | `iio_triggered_event_setup(` | **1** | 1 | `grep -rn --include=*.c 'iio_triggered_event_setup(' . \| grep -v 'industrialio-triggered-event'` |
| §4 触发器提供方 | `struct iio_trigger_ops` 赋值点 | **53** | — | `grep -rnE --include=*.c 'struct iio_trigger_ops [a-z_0-9]+ = \{' .` |
| §4 触发器提供方 | `devm_iio_trigger_alloc(` | 57 | 54 | `grep -rn --include=*.c 'devm_iio_trigger_alloc(' . \| grep -v 'industrialio-trigger.c'` |
| §4 触发器提供方 | `iio_trigger_alloc(`（排 devm） | 8 | — | 同上再 `\| grep -v devm_` |
| §4 触发器提供方 | `devm_iio_trigger_register(` | 37 | — | 同上换函数名 |
| §4 触发器提供方 | `iio_trigger_register(`（排 devm） | 28 | — | 同上 |
| §5 sw-device | `struct iio_sw_device_type` 赋值点 | **1** | 1 | `grep -rnE --include=*.c 'struct iio_sw_device_type [a-z_0-9]+ = \{' .` |
| §5 sw-trigger | `struct iio_sw_trigger_type` 赋值点 | **2** | 2 | 同上换类型名 |
| §6 内核内消费者 | 读写 API + get 全族（树外） | **110** | 34 | 见 §6.4 的完整管道 |
| §6 板级 map | `iio_map_array_register(` + devm | 10 | 10 | `grep -rnE --include=*.c '(devm_)?iio_map_array_register\(' . \| grep -v 'inkern.c'` |
| §7 mux | `"io-channel-mux"` in arm64 DTS | **1** | 1 | `grep -rl '"io-channel-mux"' arch/arm64/boot/dts/` |
| §7 rescale | 五个 compatible in arm64 DTS | **0** | 0 | 见 §7.3 的逐个 compatible 表 |

**这张表里最该注意的三个数字**：

- **`devm_iio_triggered_buffer_setup` 的 72 是全篇最大的单一数字**，它是"一个 IIO 驱动想支持连续采样"的标准答案。看到它就知道这个驱动走 kfifo + 触发器。
- **§3、§5-device 的 1**：两个框架各只有一个消费者，而 §5-device 的那一个还是 `drivers/iio/dummy/` 里的示例代码。
- **§4 的 53 对比 `trigger/` 目录的 6 个文件**：目录不等于框架边界（§4.0）。

### §8.3 缺陷与可疑点清单

八条，按严重度排。**D6 和 D8 是内存安全级别，其余是接口摩擦或死代码。**

| 编号 | 位置 | 性质 | 后果 |
|---|---|---|---|
| **D6** | `drivers/iio/industrialio-sw-device.c:59-64` | **错误路径漏回滚** | `configfs_register_default_group` 失败时不把类型摘下链表，模块加载失败后链表上留下指向已释放模块内存的表项。孪生文件 `industrialio-sw-trigger.c:61-66` 做了回滚（§5.3） |
| **D8** | `drivers/iio/inkern.c:166-167` | **无锁裸解引用** | `indio_dev->info->fwnode_xlate`，既不取 `info_exist_lock` 也不判 NULL；同文件另 9 处公开入口全部先取锁再判。提供方在消费者 probe 窗口内被 unbind 时空指针（§6.2） |
| **D7** | `include/linux/iio/sw_device.h:54-55`、`sw_trigger.h:54-55` | **幻影声明** | 四个函数只有声明、全树无定义。照头文件写代码得到链接错误而非编译错误（§5.4） |
| **D3** | `drivers/iio/buffer/industrialio-buffer-cb.c:54-57` | 接口强制项 | `iio_channel_get_all_cb()` 强制要求非空回调，不需要回调的消费者只能塞空函数——`sound/soc/stm/stm32_adfsdm.c:300` 把这条抱怨写进了注释（§1.3.3） |
| **D4** | `drivers/iio/buffer/industrialio-buffer-cb.c` 构造失败路径 | 职责重叠 | 构造失败时直接 `kfree` 而不走 `iio_buffer_put`，与 `.release` 回调的职责边界不清；引用计数恰好为 1 所以现在是对的，但改动时容易破（§1.3.4） |
| **D5** | `drivers/iio/buffer/industrialio-hw-consumer.c:152` | kerneldoc 与实现冲突 | 注释说失败返回 NULL，实现返回 ERR_PTR；同文件 `:79` 的另一份注释是对的（§1.4） |
| **D1** | `drivers/iio/buffer/kfifo_buf.c` `iio_kfifo_remove_from` 里的 `kfifo_size` 判断 | 死代码 | 该条件恒不成立：`:212` 初始化 `length = 2`、`iio_set_length_kfifo()` 把更小的请求钳回 2、`__iio_allocate_kfifo()` 对 `length == 0` 返回 `-EINVAL`（§1.1） |
| **D2** | `drivers/iio/buffer/kfifo_buf.c` `iio_set_length_kfifo()` | 静默钳位 | 用户写入的 `length` 小于 2 时被悄悄改成 2，`sysfs` 读回来和写进去的不一致，无任何日志（§1.1） |

**两条不算缺陷但值得记的设计观察**（不进上表，避免把"我不喜欢"混进"它是错的"）：

- **§1.5 的 scan_mask 布局不一致**：callback 缓冲区用 `bitmap_zalloc`（`industrialio-buffer-cb.c:90-91`），hw-consumer 用柔性数组 + `struct_size()`（`industrialio-hw-consumer.c:32`、`:76-77`）。两个孪生框架在同一件事上做了不同选择，都能工作。
- **§3 的 modes 清位不一致**：`iio_triggered_event_cleanup()` 还原 `indio_dev->modes`（`industrialio-triggered-event.c:57`），`iio_triggered_buffer_cleanup()` 不还原。在正常拆除路径上无差别。

### §8.4 读完本篇之后

| 想知道什么 | 去哪篇 |
|---|---|
| 核心的缓冲区/触发器机制本身怎么跑 | `03-internals.md` Part A（A4 / A6 / A8 / A9） |
| `iio_buffer_access_funcs` 每个字段的语义 | `02-datastructures.md` §4 |
| 作为消费者驱动怎么调 §6 的 API（含 devm 配对） | `05-usage.md` 的消费者角色一节 |
| `io-channels` / `#io-channel-cells` / `io-channel-ranges` 怎么写 | `06-devicetree.md` §1.1 起 |
| 缓冲区没数据 / 触发器不触发时怎么查 | `07-debug.md` 的症状分组 |
| 为什么 mux 要"重放 ext_info" | `08-feynman.md` 对应主题 |
| 本篇每个数字的重跑命令与证据等级 | `99-evidence.md` §2 与 §4 |
