---
title: 总览
---

# IIO 子系统总览

> 基线：Linux 6.1.158（`f6e38ae624cf`），架构 arm64。
> 本篇是**复习入口**：隔几个月回来只读这一篇就能恢复全貌。所有细节都在别篇，本篇只给骨架和去处。

---

## §1 一句话定位与三条边界

**IIO（Industrial I/O）是内核里管「模拟量转数字量」这类器件的框架**：ADC、DAC、加速度计、陀螺仪、磁力计、气压计、光照/接近传感器、温度传感器、IMU。它给这些器件一套统一的用户态接口（sysfs 属性 + 字符设备），并给内核里的其他驱动一套消费者 API。

官方定位在 `Documentation/driver-api/iio/intro.rst`：IIO 要填的是 **hwmon 和 input 之间那个洞**——hwmon 面向低采样率的系统自监控（风扇转速、片上温度），input 面向人机交互设备（键盘、鼠标、触摸屏），而「高采样率、要时间戳、要连续流」的传感器两边都不合适。

三条边界要一开始就划清，否则读代码会一直找错地方：

| IIO 不是什么 | 为什么 | 依据 |
|---|---|---|
| **不是一条真实总线** | `iio_bus_type` 只有一个 `.name`，没有 `.match`、没有 `.probe`——**这条总线上不发生任何驱动绑定** | `drivers/iio/industrialio-core.c:42-44` |
| **不是传感器驱动的容器** | 传感器驱动住在 SPI / I2C / platform 总线上，它们**创建** IIO 设备，而不是**是** IIO 设备 | `:1685` 一行 `indio_dev->dev.bus = &iio_bus_type` 把新建的 `iio_dev` 挂上去 |
| **不做单位换算** | 内核只给你 `_raw`、`_scale`、`_offset` 三个数，算成物理量是用户态的事 | 见 §2.3.1 与 `08-feynman.md` §3 |

所以本文档集把 IIO 归为**框架型子系统**：它有 `bus_type`，但那个 `bus_type` 退化成一个 sysfs 命名空间。判定过程和两道门的实际输出记在 `99-evidence.md` §1。

---

## §2 背景：这个框架在解决什么问题

### §2.1 一颗传感器芯片同时面对三种互不兼容的读法

拿一颗最常见的三轴加速度计说事。它挂在 I2C 上，寄存器里躺着 X/Y/Z 三个 16 位值。三种使用它的场景，对内核的要求完全不同：

**场景一：偶尔看一眼。** 一个 shell 脚本每分钟读一次倾角，判断设备有没有被搬动。要求：读一次就行，越简单越好，不在乎延迟几毫秒，也不在乎两次读之间的间隔准不准。

**场景二：连续高速采集。** 一个姿态解算程序要 400 Hz 的连续数据流，而且必须知道每个采样点**精确的时刻**（解算算法要做积分，时间戳错了积分就飘）。要求：不能丢样、每个样带时间戳、内核到用户态的拷贝次数越少越好。400 Hz 下每次采样都走一遍 `open`/`read`/`close` 是不可行的。

**场景三：只关心「越界了叫我」。** 一个电源管理逻辑只想知道「加速度超过 2g 了」，平时一点数据都不想收。要求：内核在硬件报警时给用户态一个事件，其余时间完全静默。

这三种要求彼此冲突：

- 场景一想要「同步、简单、无状态」，场景二想要「异步、带缓冲、有状态」。
- 场景二想要「时间戳精确到微秒」，而场景一那条路上**根本没有采样时刻的概念**。
- 场景三想要「零数据、只要通知」，而场景一和场景二都是「拿数据」。

更麻烦的是同一颗芯片可能被三个场景同时使用——脚本在读倾角的时候，姿态解算也在跑。而 I2C 总线上一次只能有一个事务。

IIO 的整个形状就是为了把这三条路都提供出来、并且让它们互相不打架。三条路在 `02-datastructures.md` §5 里画成了三张数据流图；本篇 §6 给它们的调用路径骨架。

### §2.2 为什么不能直接用已有的子系统

内核里有三个「看起来可以」的现成去处，逐个说明为什么不够。

#### hwmon：采样率和 ABI 都不对

hwmon（`drivers/hwmon/`）的接口也是 sysfs 属性，也是读传感器值，形状上和 IIO 的场景一几乎一样。两个硬伤：

**第一，hwmon 的 ABI 规定了单位。** hwmon 的 `temp1_input` 是「毫摄氏度」，`in0_input` 是「毫伏」——单位写死在 ABI 里，驱动必须自己换算好。这对系统监控是对的（`sensors` 命令要能直接显示），但对 IIO 的目标器件是灾难：一颗 24 位 ADC 的最低有效位可能是 0.0000001 V，换算成毫伏就把有效位截掉了。IIO 选择反过来做——`_raw` 给原始整数，`_scale` 给倍数，精度一位不丢（§2.3.1）。

**第二，hwmon 没有数据面。** hwmon 里读一个值就是一次 sysfs 读，没有缓冲区、没有时间戳、没有字符设备。场景二在 hwmon 里根本无法表达。

顺带说明一件容易搞混的事：**IIO 和 hwmon 不是互斥的**。`drivers/hwmon/iio_hwmon.c` 就是一个把 IIO 通道包装成 hwmon 设备的桥——一颗芯片可以既是 IIO 设备（给高速采集用），又通过这个桥在 `/sys/class/hwmon/` 下露一份（给 `sensors` 用）。它走的是消费者 API（`01-api.md` §4），是 IIO 消费者的一个典型例子。

#### input：语义层次不对

input（`drivers/input/`）有事件流、有时间戳、有字符设备，场景二和场景三看起来都能满足。问题在语义：

**input 的事件是「已经解释过的」。** `EV_ABS` / `ABS_X` 表示「某个轴的绝对位置」，它假定内核已经知道这个数字的含义。而 IIO 的定位恰恰是「内核不解释」——一颗 ADC 接的是什么传感器，内核不知道也不该知道。把一个通用 ADC 的读数塞进 `ABS_X` 是在编造语义。

**input 的采样率不由设备定。** input 设备的事件是「有变化才报」，没有「以 400 Hz 等间隔采样」这个概念。IIO 把采样时机做成了一个独立的、可替换的东西（触发器，§2.3.2），这在 input 的模型里没有对应物。

同样地，两者也不互斥：一颗加速度计可以有 IIO 驱动（给姿态解算），同时通过别的路径给 input 报翻转事件。`intro.rst` 明确写了 "In some cases there is considerable overlap between these and IIO"。

#### 每个驱动自己开一个字符设备

这是 IIO 之前的实际做法，也是最容易走的路：驱动自己 `alloc_chrdev_region`，自己定义一套 ioctl。三个后果：

**第一，每颗芯片一套 ABI。** 换一颗型号，用户态程序全部重写。IIO 的 500 多个驱动如果各自定义 ioctl，用户态就没有任何通用工具的可能——而现在 `tools/iio/iio_generic_buffer.c` 一个程序能读任何 IIO 设备的缓冲区（`07-debug.md` §5.4）。

**第二,「采样时机」和「读哪个芯片」被绑死。** 姿态解算要求加速度计和陀螺仪**在同一时刻**采样。如果两个驱动各自管自己的定时器，两者的采样时刻就无法对齐。IIO 把触发器做成独立设备，让两个设备挂同一个触发器，这才做得到（§2.3.2）。

**第三，每个驱动重写一遍环形缓冲区和 poll 逻辑。** 这类代码错误率高、每个驱动错的地方还不一样。

### §2.3 IIO 的四个设计决策，以及每个决策的代价

下面四条是从代码形状反推出来的设计决策。**每一条都有代价，而代价往往就是使用者踩的坑**——把决策和代价成对记住，比记住 API 更省事。

#### §2.3.1 决策一：不做单位换算，把三件套抛给用户态

sysfs 上暴露的是 `in_voltage0_raw`（原始整数）、`in_voltage_scale`（倍数）、`in_voltage_offset`（偏置），物理量 = `(raw + offset) × scale`。三个文件名来自同一张后缀表 `iio_chan_info_postfix[]`（`drivers/iio/industrialio-core.c:146-178`，29 项）。

**收益**：任意精度都能表达，不损失有效位；驱动不需要做浮点运算（内核里做浮点是禁忌）。

**代价三条**：

1. **用户态必须自己算，而且顺序不能错**（先加 offset 再乘 scale，依据 `drivers/iio/inkern.c:662` 与 `:678`）。
2. **`_scale` 不是一个简单的小数**。回调返回的是 `(val, val2, 类型码)` 三元组，类型码有 8 种（`include/linux/iio/types.h:24-32`），决定这两个整数怎么拼。解析 `_scale` 字符串比想象的复杂。
3. **`_offset` 在两种类型码下会被静默截断成整数**（`inkern.c:645-651` 带注释写明了）。这是精度损失，而且不报错。

内核里的消费者有现成的换算函数 `iio_read_channel_processed()`（`inkern.c:790`），用户态没有。完整展开见 `08-feynman.md` §3。

#### §2.3.2 决策二：采样时机外置成一个独立设备

「什么时候采一次」不是 IIO 设备的属性，是一个**独立注册的触发器设备**（`struct iio_trigger`，`include/linux/iio/trigger.h:60`），它自己也挂在 `iio_bus_type` 上（`drivers/iio/industrialio-trigger.c:562-563`）。设备通过写 `trigger/current_trigger` 挂上去。

**收益**：多个设备可以挂同一个触发器，从而在同一时刻采样；触发源可以是硬件中断、hrtimer、sysfs 手动触发、甚至另一个 IIO 设备的数据就绪信号。本树提供 6 个通用触发器驱动（`drivers/iio/trigger/`）。

**代价四条**：

1. **多了一个必须配对的运行期配置步骤。** 没挂触发器就开缓冲区 → `-EINVAL`，而且**默认没有日志**（`07-debug.md` §4.4 的静默陷阱）。这是 IIO 最常见的「配了半天没数据」原因。
2. **槽位是编译期定长的。** `CONFIG_IIO_CONSUMERS_PER_TRIGGER` 缺省 2，一个触发器最多挂 2 个消费者。超了报错，而且报错信息把 Kconfig 名字直接印出来（`industrialio-trigger.c:288-289`）。
3. **触发丢失是静默的。** 上一轮消费者还没全部 done 时，这一轮触发被整个丢掉，不排队不计数（`:198` 的 `if (!atomic_read(&trig->use_count))`）。采样率上不去而没有任何报错时先查这里。
4. **有两个几乎一样的点火函数，用错了会死锁**：`iio_trigger_poll()`（`:195`，硬中断上下文）和 `iio_trigger_poll_chained()`（`:219`，线程化中断上下文），差别只在一行。

#### §2.3.3 决策三：数据面走字符设备，不走 sysfs

高速数据流从 `/dev/iio:deviceN` 读，不从 sysfs 读。字符设备的 fops 是**二选一**装上去的（`industrialio-core.c:1984` / `:1986`）：有缓冲区装 `iio_buffer_fileops`（`:1862-1872`），否则装 `iio_event_fileops`（`:1874-1881`）。

**收益**：一次 `read()` 能取走多个采样；有 `poll()` 可以配 epoll；数据布局由 `scan_type` 精确定义，用户态可以直接按结构体解。

**代价四条**：

1. **二进制格式必须自己解析。** 每个通道占几位、有效位在哪、什么字节序，都要从 `bufferN/in_*_type` 读出来再算。`tools/iio/iio_generic_buffer.c` 存在的全部理由就是这个。
2. **既没有缓冲区又没有事件接口的设备，`/dev/iio:deviceN` 根本不存在**（`:1988-1991` 的条件）。纯 sysfs 型 ADC 在 `/dev/` 下找不到不是坏了。
3. **主字符设备是独占的**（`:1775-1776` 返回 `-EBUSY`），而且和缓冲区/事件的专用 fd 之间还有一层历史包袱（下一条）。
4. **老 ABI 和新 ABI 在同一个节点上共存。** 一旦某个缓冲区通过 `IIO_BUFFER_GET_FD_IOCTL` 拿过独立 fd，主节点上的老 `read()` / `write()` 一律返回 `-EBUSY`（`industrialio-buffer.c:272`、`:285`）。用老工具读一个被新工具打开过的设备就会撞上。完整对比见 `08-feynman.md` §5。

#### §2.3.4 决策四：总线只做命名空间，不做驱动绑定

`iio_bus_type` 的定义只有三行：

```c
struct bus_type iio_bus_type = {          // industrialio-core.c:42
	.name = "iio",                    // :43
};                                        // :44
```

没有 `.match`、没有 `.probe`、没有 `.uevent`。IIO 设备（`iio_device_type`，`:1651`）和触发器（`iio_trig_type`，`industrialio-trigger.c:527`）都挂在这条总线上，但**从来没有 driver 去绑定它们**——全树没有 `struct device_driver` 注册到 `iio_bus_type` 上。

**收益**：`/sys/bus/iio/devices/` 成了一个统一的枚举入口，设备和触发器在同一处可见；同时不引入第二套 probe 生命周期（真正的 probe 在 SPI/I2C/platform 那一侧发生）。

**代价两条**：

1. **「注册在 iio 总线上」这句话极易被误解成「有 iio 驱动会来绑它」**。这是本子系统头号类比陷阱，`02-datastructures.md` §1.0 专门用一节否掉它。
2. **设备和触发器混在同一个目录下**，靠名字前缀区分（`iio:deviceN` vs `trigger0`）。脚本枚举时必须过滤，不能 `ls` 完就当成设备列表。

### §2.4 这四个决策留下的四笔债

这一节是「读代码时会觉得别扭的地方，原因在这里」。四笔债在 `03-internals.md` Part C 里逐条给了源码注释依据。

**债一：观测面异常地窄。** 核心一个 tracepoint 都没有；`drivers/iio/Kconfig` 7 个选项里没有一个是调试开关；debugfs 只有一个文件 `direct_reg_access`，而且只有 50 个文件（51 个赋值点）实现了它，不到全树 532 个 IIO `.c` 文件的十分之一。整个核心九个文件加起来只有 28 个打印点，其中 `industrialio-event.c` 一条都没有——**整条事件通路完全静音**。`07-debug.md` 整篇是围着这个事实组织的。

**债二：静默失败点多。** 上面已经点了三个（缓冲区无触发器的 `-EINVAL`、触发丢失、mount-matrix 缺失回落到单位矩阵）。共同特征是**返回值正确、日志为空**，只能靠对着代码读判据。`07-debug.md` §8.4 把它们收拢成了症状表。

**债三：同一个错误码承载多种语义。** 核心九个文件里 `-EINVAL` 有 71 处 grep 命中、`-ENODEV` 35 处、`-EBUSY` 23 处。`-EBUSY` 的 16 个真实产生点分五组语义，其中有一对**语义完全相反**的 API 返回同一个码（`iio_device_claim_direct_mode` vs `iio_device_claim_buffer_mode`，`industrialio-core.c:2076` / `:2116`）。分组展开在 `07-debug.md` §7.3。

**债四：ABI 全部停在 testing。** `Documentation/ABI/testing/` 下有 52 个 `sysfs-bus-iio*` 文件（1 个通用 + 51 个驱动专属）加 1 个 `configfs-iio`，而 `Documentation/ABI/stable/` 下**一个 iio 文件都没有**。同时 `drivers/staging/iio/` 至今还留着 7 个驱动目录没毕业。这意味着：文档里的属性名不承诺永久稳定，而且驱动专属属性的文档覆盖率远低于驱动数量。

四笔债和四个决策不是一一对应的，对照关系是这样：

| 债 | 主要来自哪个决策 | 你会在哪一篇撞上它 |
|---|---|---|
| 观测面窄 | 决策三（数据面走字符设备） | `07-debug.md` 全篇 |
| 静默失败点多 | 决策二（时机外置） | `06-devicetree.md` §5、`07-debug.md` §8.4 |
| 同码多语义 | 决策一 + 决策二共同造成 | `07-debug.md` §7.3 |
| ABI 停在 testing | 决策一（属性名由通道字段拼出来，属性集合是开放的） | `01-api.md` §5、`05-usage.md` §6 |

**观测面窄这件事有结构性原因，不完全是疏忽。** IIO 核心几乎不做决策：读一个值是转手给驱动的 `read_raw`，采样时机是转手给触发器，单位换算是转手给用户态。核心自己只做三件事——合成属性名、搬字节、管模式互斥。前两件不会失败到需要打印，第三件失败时返回值已经说明了一切。所以「核心 2153 行只有 13 个打印点」不是漏写，而是这个分层下核心确实没什么独有的信息可报。

代价落在了另一头：**真正会失败的地方（硬件不响应、配置顺序错、时机对不上）全都在核心看不到的两侧**。驱动侧的失败驱动自己打印（各家风格不一），用户态侧的失败连打印的机会都没有。这就是为什么 `07-debug.md` 的主线不是「打开哪个开关」而是「对着代码读判据」。

重跑这一节的四个数字：

```bash
ls Documentation/ABI/testing/sysfs-bus-iio* | wc -l          # → 52
ls Documentation/ABI/testing/configfs-iio*  | wc -l          # → 1
ls Documentation/ABI/stable/ | grep -ci iio                  # → 0
ls -d drivers/staging/iio/*/ | grep -v Documentation | wc -l # → 7
```

### §2.5 规模与形状

| 口径 | 数字 | 重跑 |
|---|---|---|
| `drivers/iio/` 下 `.c` 文件 | 532 | `find drivers/iio -name '*.c' \| wc -l` |
| `drivers/iio/` 下 `.c` + `.h` 总行 | 283154 | `find drivers/iio -name '*.[ch]' \| xargs wc -l \| tail -1` |
| 顶层核心九个 `.c` 总行 | 7027 | `wc -l drivers/iio/*.c \| tail -1` |
| `include/linux/iio/` 头文件总行 | 2522 | `wc -l include/linux/iio/*.h \| tail -1` |
| 全树 `iio_device_register` / `devm_` 真实调用点 | 388（分布在 387 个文件） | 见下面的命令；口径是「排掉核心自己的定义与 kerneldoc、再排掉错误消息串」，推导过程记在 `99-evidence.md` §6.1 B |
| `drivers/iio/` 下的子目录数 | 30 | `ls -d drivers/iio/*/ \| wc -l`；其中 27 个按器件类别分（`adc`、`accel`…），3 个是子框架（`buffer`、`trigger`、`common`） |

核心九个文件的逐个行数（这决定了「读全」和「读符号表」的分界，登记在 `99-evidence.md` §2.1）：

| 文件 | 行数 | 职责 |
|---|---|---|
| `drivers/iio/industrialio-core.c` | 2153 | 注册、通道属性合成、字符设备、ioctl 分发、debugfs |
| `drivers/iio/industrialio-buffer.c` | 1999 | 缓冲区框架、扫描元素、demux、数据面 fops 实现 |
| `drivers/iio/inkern.c` | 1037 | 内核内消费者 API |
| `drivers/iio/industrialio-trigger.c` | 750 | 触发器框架 |
| `drivers/iio/industrialio-event.c` | 614 | 事件框架 |
| `drivers/iio/industrialio-sw-trigger.c` | 183 | configfs 造软件触发器 |
| `drivers/iio/industrialio-sw-device.c` | 179 | configfs 造软件设备 |
| `drivers/iio/industrialio-triggered-event.c` | 64 | 触发器 + 事件的粘合辅助 |
| `drivers/iio/industrialio-configfs.c` | 48 | configfs 子系统注册（无打印、无逻辑） |

器件类别目录里驱动最多的六个：`adc` 118、`accel` 62、`light` 55、`dac` 44、`imu` 43、`pressure` 28。**`adc` 一个目录就占了全部驱动的两成以上**——挑代表驱动时的偏向依据记在 `99-evidence.md` §2.2。

```bash
for d in drivers/iio/*/; do echo "$(find $d -name '*.c' | wc -l) $(basename $d)"; done | sort -rn | head -6
grep -rn 'iio_device_register(' --include=*.c . \
  | grep -v '^drivers/iio/industrialio' | grep -v '"' | wc -l      # → 388（调用点）
grep -rl 'iio_device_register(' --include=*.c . \
  | grep -v '^drivers/iio/industrialio' | wc -l                    # → 387（文件）
```

### §2.6 uapi 边界

用户态能依赖的东西只有三个头文件，一共 179 行（`types.h` 126 + `events.h` 43 + `buffer.h` 10）：

| 头文件 | 内容 |
|---|---|
| `include/uapi/linux/iio/types.h` | 4 个枚举，全部：`iio_chan_type`（`:14`，35 项）、`iio_modifier`（`:52`，51 项）、`iio_event_type`（`:106`，8 项）、`iio_event_direction`（`:117`，6 项） |
| `include/uapi/linux/iio/events.h` | `struct iio_event_data`（`:22-25`）、`IIO_GET_EVENT_FD_IOCTL`（`:27`）、7 个 `IIO_EVENT_CODE_EXTRACT_*` 解码宏（`:29`–`:41`） |
| `include/uapi/linux/iio/buffer.h` | `IIO_BUFFER_GET_FD_IOCTL`（`:8`） |

值类型码（`IIO_VAL_INT` 等 8 个）**不在 uapi 里**，它们定义在 `include/linux/iio/types.h:24-32`。但用户态解析 `_scale` 字符串时事实上依赖它们的格式约定——这是一处「ABI 事实上存在、形式上没有导出」的地方。

**注意 `iio_modifier` 有 51 项而内核里的名字表只有 50 项**——差的那一项是 `IIO_NO_MOD`（表示「无修饰符」，本来不该有名字）。这类「枚举项数 ≠ 名字表项数」的地方是数数容易错的位置，`07-debug.md` §2.3 把三张表的项数逐个核过。

### §2.7 本文档集的边界

- **架构只走 arm64**。x86 / powerpc / arm32 专属的驱动和路径不读、不统计、不举例。裁掉了什么逐条记在 `99-evidence.md` §2.7。
- **驱动只挑代表**。532 个 `.c` 里详读的是核心九个文件加少数代表驱动，其余只讲契约。分界登记在 `99-evidence.md` §2.1 / §2.2。
- **示例代码未编译未运行**。`05-usage.md` 篇首有同样的声明。
- **本树打了 PREEMPT_RT 相关的树范围回移，但 IIO 自身未被改动**。验证命令和输出记在 `99-evidence.md` §1。

---

## §3 概念模型

### §3.1 六个概念

| 概念 | 是什么 | 在代码里 |
|---|---|---|
| **IIO 设备**（`iio_dev`） | 一颗芯片（或芯片的一个功能块）在 IIO 里的化身。sysfs 下叫 `iio:deviceN` | `include/linux/iio/iio.h:571-596` |
| **通道**（`iio_chan_spec`） | 设备上一个可读/可写的物理量：X 轴加速度、通道 3 的电压、片上温度 | `include/linux/iio/iio.h:238-270` |
| **属性** | 通道的一个可读/可写项，sysfs 上是一个文件。文件名 = 方向 + 类型 + 修饰 + 索引 + 后缀 | 三张名字表：`industrialio-core.c:54-90`、`:92-143`、`:146-178` |
| **触发器**（`iio_trigger`） | 「什么时候采一次」的载体，独立注册的设备 | `include/linux/iio/trigger.h:60-101` |
| **缓冲区**（`iio_buffer`） | 连续采集的数据在内核侧的落脚点，一个设备可以有多个 | `include/linux/iio/buffer_impl.h:81-139` |
| **事件** | 硬件报警（越阈值、检测到运动等）转成的一条 16 字节记录 | 结构 `include/uapi/linux/iio/events.h:22-25`，产生点 `industrialio-event.c:66` |

「属性名怎么拼出来」是理解 sysfs 的关键，展开在 `03-internals.md` Part A。名字的四段各来自一张表或一个字段：

```
in_accel_x_raw
│  │     │ └── 后缀：iio_chan_info_postfix[]（29 项，core:146-178）
│  │     └──── 修饰：iio_modifier_names[]（50 项，core:92-143）
│  └────────── 类型：iio_chan_type_name_spec[]（35 项，core:54-90）
└───────────── 方向：output 字段为真时是 out_，否则 in_
```

### §3.2 关系图

```
                    SPI / I2C / platform 总线
                    （真正的 probe 在这里发生）
                              │
                      驱动 probe 时调用
                    devm_iio_device_alloc()
                              ↓
        ┌──────────────── struct iio_dev ────────────────┐
        │                                                │
        │  channels[]  ← iio_chan_spec 数组（驱动静态定义）│
        │  info        ← iio_info（read_raw/write_raw…）  │
        │  buffer      ← 可选，开了 IIO_BUFFER 才有        │
        │  trig        ← 可选，运行期由用户态挂上          │
        └────────────────────────────────────────────────┘
                              │
                  iio_device_register()
              ┌───────────────┼────────────────┐
              ↓               ↓                ↓
      /sys/bus/iio/     /dev/iio:deviceN   内核内消费者
      devices/iio:deviceN  （二选一 fops）   iio_channel_get()
      ├── in_*_raw        ├── 缓冲区 fops    │
      ├── in_*_scale      └── 事件 fops      ↓
      ├── scan_elements/                  hwmon 桥 / thermal /
      ├── bufferN/                        power-supply / input …
      ├── events/
      └── trigger/current_trigger ──┐
                                    │ 写名字挂上
   /sys/bus/iio/devices/triggerN ←──┘
   （同一条总线，靠名字前缀区分）
```

要点三条，都在别篇展开：

1. **`iio_bus_type` 上没有 driver**（§2.3.4），所以图里没有「driver 绑定」这一步。
2. **设备和触发器在 `/sys/bus/iio/devices/` 下混放**，靠 `iio:deviceN` / `triggerN` 前缀区分。
3. **`/dev/iio:deviceN` 的 fops 是二选一的**，缓冲区路径和事件路径不共用一套 fops（`08-feynman.md` §5）。

---

## §4 数据结构速览

只列骨架和「读这个结构时最容易看错的一点」。完整字段、锁、生命周期在 `02-datastructures.md`。

| 结构 | 位置 | 骨架 | 最容易看错的一点 |
|---|---|---|---|
| `struct iio_dev` | `include/linux/iio/iio.h:571-596` | 通道数组 + `info` 回调表 + 可选 buffer/trig + 一个内嵌 `struct device` | 公开字段只是冰山一角，真正的运行期状态在 `iio_dev_opaque` 里（`include/linux/iio/iio-opaque.h:41-77`），驱动看不到也不该看 |
| `struct iio_chan_spec` | `include/linux/iio/iio.h:238-270` | 类型 + 索引 + `scan_type` + 三个 `info_mask` 位图 | `scan_index` 为负表示「这个通道不参与缓冲区扫描」，两处代码会跳过它（`industrialio-buffer.c:1622`、`industrialio-core.c:1892`） |
| `struct iio_info` | `include/linux/iio/iio.h:445-518` | `read_raw` / `write_raw` / `read_avail` / `debugfs_reg_access` / `of_xlate` … | `read_raw` 和 `read_raw_multi` 是两个不同的回调，核心优先调后者（`industrialio-core.c:765-771`） |
| `struct iio_trigger` | `include/linux/iio/trigger.h:60-101` | `ops` + 一个定长 subirq 数组 + 一个位图 pool + `use_count` | 数组长度是 `CONFIG_IIO_CONSUMERS_PER_TRIGGER`（缺省 2），不是动态的 |
| `struct iio_buffer` | `include/linux/iio/buffer_impl.h:81-139` | `access` 回调表 + 扫描掩码 + demux 表 | 「启用的通道」和「硬件实际采的通道」可以不一样（`available_scan_masks` 机制，`industrialio-buffer.c:409`） |
| `struct iio_channel` | `include/linux/iio/consumer.h:24-28` | 只有三个字段：`indio_dev`、`channel`、`data` | 它**不持模块引用**——只有触发器路径会 `__module_get`（`industrialio-trigger.c:283`） |
| `struct iio_event_data` | `include/uapi/linux/iio/events.h:22-25` | 一个 `__u64` id + 一个 `__s64` 时间戳 | id 是位打包的，必须用 7 个 `IIO_EVENT_CODE_EXTRACT_*` 宏解（`:29`–`:41`），别自己算移位 |

---

## §5 机制速览

| 机制 | 一句话 | 关键入口 | 详见 |
|---|---|---|---|
| **注册** | 驱动 alloc → 填 channels/info → register，核心据此合成全部 sysfs 属性 | `iio_device_register()` | `03-internals.md` Part A |
| **属性名合成** | 四段拼名（方向/类型/修饰/后缀），三张表加上通道字段 | `industrialio-core.c:54-178` | `03-internals.md` Part A |
| **单次读** | sysfs 读 → 核心调 `read_raw` → 按值类型码格式化成字符串 | `iio_read_channel_info()` `:755` | `03-internals.md` Part A |
| **模式声明** | 单次读要求设备不在缓冲区模式，用一对语义相反的 claim 函数把关 | `iio_device_claim_direct_mode()` `:2070` / `..._buffer_mode()` `:2108` | `08-feynman.md` §7 |
| **触发器挂接** | 用户态写名字 → 核心找触发器 → 分配 subirq → `request_threaded_irq` | `iio_trigger_attach_poll_func()` `industrialio-trigger.c:274` | `03-internals.md` Part A |
| **点火** | 触发源调 poll → 广播到所有 subirq → 各消费者的 bottom half 抓数 | `iio_trigger_poll()` `:195` / `_chained()` `:219` | `08-feynman.md` §2 |
| **demux** | 硬件采了 N 个通道、用户只要 M 个时，核心按表 memcpy 拆出子集 | `iio_update_demux()` `industrialio-buffer.c:1039` | `08-feynman.md` §8 |
| **数据入队** | 驱动把一次采样交给核心，核心分发到每个启用的缓冲区 | `iio_push_to_buffers()` 及两个带时间戳的变体，`include/linux/iio/buffer.h:19`/`:37`/`:48` | `04-extensions.md` |
| **事件** | 硬件中断 → 驱动打包 id+时间戳 → kfifo → 用户态从事件 fd 读 | `iio_push_event()` `industrialio-event.c:66` | `04-extensions.md` |
| **消费者查找** | 内核内驱动按 DT phandle 或 map 表拿通道引用 | `iio_channel_get()` `inkern.c:381` | `01-api.md` §4 |
| **换算** | 内核内消费者可以直接拿到换算好的值（用户态没有这个便利） | `iio_read_channel_processed()` `inkern.c:790` | `08-feynman.md` §3 |
| **软件设备/触发器** | 通过 configfs 在运行期造出没有硬件的 IIO 设备和触发器 | `industrialio-sw-device.c` / `-sw-trigger.c` | `04-extensions.md` |

---

## §6 端到端调用路径

三条路径的骨架，每条只列关键跳，完整逐段剖析在 `03-internals.md`。

### §6.1 路径一：sysfs 单次读

```
用户态: cat /sys/bus/iio/devices/iio:device0/in_voltage0_raw
  └─ VFS → sysfs → 属性 show 回调
       └─ iio_read_channel_info()                  industrialio-core.c:755
            ├─ info->read_raw_multi()  优先        :765-766
            │  或 info->read_raw()                 :770-771
            │     └─ 驱动: iio_device_claim_direct_mode()  core:2070
            │          （在缓冲区模式则 -EBUSY，:2076）
            │          └─ 真正的 I2C/SPI 事务
            └─ iio_format_value()                  :727
                 （按值类型码把 (val,val2) 拼成字符串）
```

这条路上**没有采样时刻的概念**——你读到的值是「读的那一刻硬件寄存器里的东西」，两次读之间的间隔不受任何控制。

### §6.2 路径二：触发式缓冲区

配置阶段（用户态，顺序不能颠倒）：

```
1. echo 1 > scan_elements/in_voltage0_en      选通道
2. echo trigger0 > trigger/current_trigger    挂触发器 ← 忘了这步 → 第 4 步 -EINVAL 且无日志
3. echo 128 > buffer/length                   设深度
4. echo 1 > buffer/enable                     开
```

第 4 步在内核里的展开：

```
buffer/enable 写 1
  └─ iio_enable_buffers()                      industrialio-buffer.c:1059
       ├─ 算出 active_scan_mask                :1066
       ├─ 算 scan_bytes                        :1068（iio_compute_scan_bytes :705）
       ├─ 设 currentmode                       :1069
       └─ iio_update_demux()                   :1071 → :1039
```

运行阶段（每次采样）：

```
触发源（hrtimer / 硬件中断 / sysfs 手动）
  └─ iio_trigger_poll()                        industrialio-trigger.c:195
       ├─ use_count = CONFIG_IIO_CONSUMERS_PER_TRIGGER   :200
       └─ generic_handle_irq() 逐个 subirq      :203
            └─ 驱动的 top half: iio_pollfunc_store_time()  :348（打时间戳）
                 └─ 驱动的 bottom half（线程上下文）：
                      读寄存器 → iio_push_to_buffers_with_timestamp()
                                   include/linux/iio/buffer.h:37
                         └─ demux → 各缓冲区的 store_to
                 └─ iio_trigger_notify_done()  :236
                      └─ 全部 done 后 ops->reenable()  :240
```

用户态侧：`poll()` 等 `/dev/iio:device0`（或 `IIO_BUFFER_GET_FD_IOCTL` 拿到的专用 fd）可读，然后 `read()` 一次取走多个采样，按 `bufferN/in_*_type` 描述的布局解析。

### §6.3 路径三：事件

```
硬件报警（超阈值）→ 芯片拉中断线
  └─ 驱动的中断处理函数
       └─ iio_push_event(indio_dev, code, timestamp)   industrialio-event.c:66
            └─ 打包成 struct iio_event_data 进 kfifo
                 └─ 唤醒等在事件 fd 上的 poll

用户态:
  fd = open("/dev/iio:device0")
  ioctl(fd, IIO_GET_EVENT_FD_IOCTL, &event_fd)   include/uapi/linux/iio/events.h:27
       └─ iio_event_getfd()  industrialio-event.c:192
            （kfifo_reset_out() :218 —— 新 fd 会丢掉之前积压的事件）
  close(fd)          ← tools/iio/iio_event_monitor.c:406 就是这么干的
  read(event_fd, &ev, 16)
  用 IIO_EVENT_CODE_EXTRACT_* 宏解 ev.id
```

这条路整个是**静音的**——`industrialio-event.c` 一个打印点都没有，配错了不会有任何 dmesg 线索。判据只能靠读代码，收在 `07-debug.md` §8.4。

---

## §7 与其他子系统的关系

| 子系统 | 关系 | 具体连接点 |
|---|---|---|
| **SPI / I2C / platform** | **父总线**。传感器驱动住在那里，probe 在那里发生，`indio_dev->dev.parent` 指回去 | `iio_device_register()` 之前由驱动设置 |
| **hwmon** | **下游消费者**，有官方桥。IIO 通道包装成 hwmon 设备 | `drivers/hwmon/iio_hwmon.c:78` 拿全部通道，`:52` 读换算值，`:163` 注册成 hwmon 设备 |
| **thermal** | **下游消费者**。ADC 通道当温度传感器用 | `drivers/thermal/` 与 `drivers/thermal/qcom/` 共 3 个文件调消费者 API |
| **power-supply** | **最大的下游消费者**，21 个文件。电池电压/电流/温度大量来自 ADC | `drivers/power/supply/` |
| **input** | **下游消费者**（4 个文件：触摸屏 2、键盘 1、摇杆 1）。和 IIO 是并列而非包含关系（§2.2） | `drivers/input/touchscreen/`、`keyboard/`、`joystick/` |
| **ASoC** | 下游消费者，2 个文件（stm、samsung） | `sound/soc/` |
| **extcon / phy** | 下游消费者，各 1 个文件 | `drivers/extcon/`、`drivers/phy/motorola/` |
| **configfs** | IIO 用它做「运行期造软件设备/触发器」 | `industrialio-configfs.c`（48 行，只做注册） |
| **irq / hrtimer** | 触发源。触发器的实现挂在这两个之上 | `drivers/iio/trigger/` 6 个实现 |

`drivers/iio/` **之外**调消费者 API 的文件共 34 个，跨 11 个目录：

```bash
grep -rl -e iio_channel_get -e iio_read_channel_raw -e iio_read_channel_processed \
         -e iio_channel_cb_setup --include=*.c . \
  | sed 's#^\./##' | grep -v '^drivers/iio/' | wc -l          # → 34
```

`drivers/power/supply/` 一个目录占了 21/34。这决定了「IIO 消费者 API 的实际主战场是电源管理」，而不是文档里最常举例的 hwmon。

---

## §8 导读

按你现在想干的事挑一篇，别按编号顺序读。

| 你现在想干的事 | 去哪篇 | 从哪一节切入 |
|---|---|---|
| 写一个传感器驱动 | `05-usage.md` | §2「provider 驱动骨架」 |
| 在自己的驱动里读别人的 ADC | `05-usage.md` | §4「消费者骨架」，再看 `01-api.md` §4 |
| 查某个函数叫什么、参数是什么 | `01-api.md` | §0 是代码地图，找文件也在这篇 |
| 弄明白 `iio_dev` 里哪个字段是干什么的 | `02-datastructures.md` | §1.0 先破歧义再看图 |
| 搞清楚注册时到底发生了什么 | `03-internals.md` | §0 的时间线图 |
| 加一个新的缓冲区实现 / 触发器 / 软件设备 | `04-extensions.md` | 篇首分诊表 |
| 写 DTS 或 DTS 写错了不工作 | `06-devicetree.md` | §0 发现路径分诊表 |
| 配了半天没数据 / 出了错不知道从哪查 | `07-debug.md` | §8 一页速查 |
| 概念上就是想不通（比如「触发器为什么是独立设备」） | `08-feynman.md` | §0 的选题表 |
| 想知道哪些结论能直接信、哪些要自己再核 | `99-evidence.md` | §5 未验证项四层分层 |

三句话的复习提要，忘了别的先记住这三条：

1. **IIO 不解释数据。** 它给你 raw / scale / offset，物理量是你自己的事；这一条决定了 sysfs 长什么样。
2. **采样时机是一个独立的设备。** 触发器不是 IIO 设备的一个属性，是另一个注册在同一条总线上的东西；这一条决定了缓冲区路径为什么要两步配置。
3. **那条总线是假的。** `iio_bus_type` 只有名字，没有 `.match` 没有 `.probe`；这一条决定了「谁 probe 了谁」这个问题的答案永远在 SPI/I2C/platform 那一侧。
