---
title: 取证附录
---

# IIO 文档集取证附录

本篇不讲 IIO，讲**这套文档是怎么来的、哪些部分可以直接信、哪些要自己再核一遍**。所有数字都配了能一条命令重跑的口径；正文里每一条「见 `99-evidence.md` §X」都落在这里。

---

## §1 基线

### §1.1 树的状态

```
$ git log -1 --format='%H %s'
f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158
```

工作树**不干净**：`git status --porcelain | wc -l` → 178 个被修改的文件。这是一次树范围的 PREEMPT_RT 相关回移，涉及 `arch/arm64/`、`arch/arm/`、`arch/powerpc/`、`arch/x86/`、`drivers/gpu/drm/i915/`、大量网卡驱动等。

### §1.2 关键判定：IIO 本身没有被改动

```
$ git status --porcelain -- drivers/iio include/linux/iio include/uapi/linux/iio tools/iio
（无输出）
```

**无输出即 IIO 的四个目录一个文件都没被这次回移碰过。** 这条判定决定了整份文档的定性：本文档集描述的是**原版 Linux 6.1.158 的 IIO**，不是打过补丁的变体。读者可以直接拿上游 6.1.158 的源码对照本文档集的每一个行号。

配套的两条推论：

- **锁的语义按上游读。** 本文档集里凡是提到 `mutex`、`spinlock`、`atomic` 的地方，语义都是上游 6.1 的语义，不是 RT 变体的语义。
- **但 IIO 的消费者可能被改了。** 例如 `drivers/gpu/drm/i915/` 在改动列表里。本文档集只覆盖 IIO 自身和它的直接契约，不覆盖消费者驱动被 RT 补丁改动后的行为。

### §1.3 子系统类型判定：框架型

按 skill 阶段 1 步骤 3b 的两道门：

**门 1（有没有自己的 `bus_type`）——有：**

```
$ grep -rn 'struct bus_type' drivers/iio/*.c include/linux/iio/*.h
drivers/iio/industrialio-core.c:42:struct bus_type iio_bus_type = {
include/linux/iio/iio.h:635:extern struct bus_type iio_bus_type;
```

**门 2（协议 ABI 在不在 uapi 里 / 对端在不在本树）——不在：**

```
$ ls include/uapi/linux/iio/*.h | wc -l
3
```

只有 3 个 uapi 头文件，一共 179 行，内容是 4 个枚举 + 2 个 ioctl 号 + 1 个 16 字节结构 + 7 个解码宏（见 `00-overview.md` §2.6）。**没有一份树外规范定义 IIO 的线上格式**——数据的含义完全由驱动和用户态约定，内核只搬字节。所以门 2 空。

按 skill 的判定表，门 1 有、门 2 空 → **硬件总线型**。但这里必须补第三道门，否则会误判：

**门 3（这条总线上有没有 driver 绑定）——没有：**

```
$ awk 'NR>=42 && NR<=44' drivers/iio/industrialio-core.c
struct bus_type iio_bus_type = {
	.name = "iio",
};
$ grep -c 'driver_register\|struct device_driver' drivers/iio/industrialio-core.c
0
```

`iio_bus_type` **只有 `.name`**，没有 `.match`、没有 `.probe`、没有 `.uevent`，全树也没有任何 `struct device_driver` 注册到它上面。这条总线是一个 sysfs 命名空间，不是一条驱动绑定通道。真正的 probe 发生在 SPI / I2C / platform 那一侧。

**最终判定：框架型。** 相应的模板适配：

| 模板项 | 本文档集的选择 | 理由 |
|---|---|---|
| `00-overview.md` §2 | **路径 B（问题背景）** | 没有一份硬件规范或协议规范可依，背景是「一颗芯片三种读法」这个软件问题 |
| `02-datastructures.md` | 画「核心抽象 ↔ 消费者」关系图，并保留一张「本子系统总线 ↔ 父总线」图 | `bus_type` 存在但退化，两张图都需要，而且 §1.0 必须显式否掉「iio 总线会绑 driver」 |
| `03-internals.md` | 主轴是「注册 / 配置 / 回调」，不是「传输」 | 核心不碰硬件，所有硬件访问都在驱动回调里 |
| `04-extensions.md` | 保留（IIO 有 11 个子框架） | 见 `04-extensions.md` 篇首分诊表 |
| `06-devicetree.md` | 保留 DT 篇 + 发现路径分诊表 | IIO 有真实的 DT 绑定（`io-channels` 系列），但不是唯一发现路径 |
| `99-evidence.md` §5.5 | 写「不适用」 | 没有树外对端，见 §5.5 |

### §1.4 架构范围：arm64-only

声明与裁剪记录见 §2.7。

### §1.5 读过的 Documentation

`check_doc_coverage.sh` 认定的「必查」档共 9 篇，逐篇登记在 §2.3。

---

## §2 证据分层

分层的判据一句话：**一等 = 整个文件逐行读过；二等 = 只详读了若干区间，其余只看符号表；三等 = `Documentation/` 下的散文。** 读者据此决定哪些结论可以直接用、哪些要自己再确认——**二等证据里落在「详读区间」之外的论断，可信度等同于推断。**

### §2.1 一等证据（整个文件逐行读过）

| 文件 | 行数 | 读了的什么 |
|---|---|---|
| `drivers/iio/industrialio-sw-device.c` | 179 | configfs 造软件设备的全流程；`:59-64` 的错误路径漏回滚（缺陷 D6） |
| `drivers/iio/industrialio-sw-trigger.c` | 183 | 与上一个文件的孪生对照；`:61-66` 做了回滚 |
| `drivers/iio/industrialio-triggered-event.c` | 64 | 触发器 + 事件的粘合层；`:57` 还原 `modes` |
| `drivers/iio/industrialio-configfs.c` | 48 | 只做 configfs 子系统注册，无逻辑、无打印 |
| `drivers/iio/iio_core.h` | 111 | 24 个核心内部声明 + `IIO_BUSY_BIT_POS`（`:62`） |
| `drivers/iio/iio_core_trigger.h` | 59 | 触发器消费者注册的内部声明 |
| `drivers/iio/buffer/industrialio-triggered-buffer.c` | 139 | 便利层三合一；`:56-57` 的 `-EADDRINUSE` 与 `:49-55` 的理由注释；`:78` 无条件赋 `setup_ops` |
| `drivers/iio/buffer/kfifo_buf.c` | 290 | 默认后端全部；缺陷 D1（`:158` 死代码）、D2（静默钳位） |
| `drivers/iio/buffer/industrialio-buffer-cb.c` | 156 | 回调缓冲区；缺陷 D3（`:54-57` 强制非空回调）、D4（构造失败路径） |
| `drivers/iio/buffer/industrialio-hw-consumer.c` | 213 | 硬件消费者；缺陷 D5（`:152` kerneldoc 与实现冲突，而 `:79` 是对的） |
| `include/uapi/linux/iio/types.h` | 126 | 4 个枚举全部逐项数过（35 / 51 / 8 / 6） |
| `include/uapi/linux/iio/events.h` | 43 | `iio_event_data`（`:22-25`）、ioctl 号（`:27`）、7 个解码宏（`:29`–`:41`） |
| `include/uapi/linux/iio/buffer.h` | 10 | 一个 ioctl 号 |
| `include/linux/iio/consumer.h` | 427 | 消费者 API 全部；`struct iio_channel`（`:24-28`）只有 3 个字段 |
| `include/linux/iio/buffer.h` | 58 | 三个 push 变体（`:19` / `:37` / `:48`）及其对齐假设 |
| `include/linux/iio/buffer_impl.h` | 168 | `iio_buffer_access_funcs`（`:53-73`）12 个函数指针；`iio_buffer`（`:81-139`） |
| `include/linux/iio/trigger.h` | 187 | `iio_trigger`（`:60-101`）；`iio_trigger_get_drvdata`（`:125`） |
| `include/linux/iio/trigger_consumer.h` | 53 | `iio_poll_func` 与两个 top-half 辅助 |
| `include/linux/iio/types.h` | 71 | 8 个值类型码（`:24-32`）+ `iio_chan_info_enum`（`:39`，29 项） |
| `include/linux/iio/iio-opaque.h` | 80 | `iio_dev_opaque`（`:41-77`）——驱动看不到的运行期状态 |
| `include/linux/iio/sw_device.h` | 67 | 缺陷 D7 的两个幻影声明（`:54-55`） |
| `include/linux/iio/sw_trigger.h` | 67 | 缺陷 D7 的另两个幻影声明（`:54-55`） |
| `include/linux/iio/triggered_buffer.h` | 38 | `iio_triggered_buffer_setup` 宏与 `_ext` 的关系 |
| `include/linux/iio/kfifo_buf.h` | 21 | 四个导出符号 |
| `include/linux/iio/hw-consumer.h` | 20 | 五个导出符号 |
| `include/linux/iio/machine.h` | 35 | `iio_map` 结构（板级 map 表） |
| `include/linux/iio/driver.h` | 43 | `iio_map_array_register` 及 devm 版本的声明 |

一等证据合计 **2956 行**：

```bash
wc -l drivers/iio/industrialio-sw-device.c drivers/iio/industrialio-sw-trigger.c \
      drivers/iio/industrialio-triggered-event.c drivers/iio/industrialio-configfs.c \
      drivers/iio/iio_core.h drivers/iio/iio_core_trigger.h \
      drivers/iio/buffer/industrialio-triggered-buffer.c drivers/iio/buffer/kfifo_buf.c \
      drivers/iio/buffer/industrialio-buffer-cb.c drivers/iio/buffer/industrialio-hw-consumer.c \
      include/uapi/linux/iio/*.h \
      include/linux/iio/consumer.h include/linux/iio/buffer.h include/linux/iio/buffer_impl.h \
      include/linux/iio/trigger.h include/linux/iio/trigger_consumer.h include/linux/iio/types.h \
      include/linux/iio/iio-opaque.h include/linux/iio/sw_device.h include/linux/iio/sw_trigger.h \
      include/linux/iio/triggered_buffer.h include/linux/iio/kfifo_buf.h \
      include/linux/iio/hw-consumer.h include/linux/iio/machine.h include/linux/iio/driver.h \
  | tail -1        # → 2956 total
```

### §2.2 二等证据（详读若干区间 + 其余只看符号表）

这六个文件太大，没有逐行读完。**下面每一格「详读区间」之外的论断都是从符号表和调用关系推断的**，可信度低一档。

| 文件 | 行数 | 详读区间 | 只看符号表的部分 |
|---|---|---|---|
| `drivers/iio/industrialio-core.c` | 2153 | `:42-45`（`bus_type`）、`:54-178`（三张名字表）、`:294`、`:350-380`（模块 init/exit）、`:727-780`（格式化与单次读）、`:1085`、`:1191-1200`、`:1651-1724`（`device_type` 与 release）、`:1768-1815`（chrdev 独占）、`:1860-1900`（两套 fops、重复 scan_index 检查）、`:1920-1995`（注册尾段、fops 二选一、devt 条件）、`:2060-2120`（两个 claim 函数） | 通道属性的逐个 `__iio_add_chan_devattr` 展开细节、`iio_read_channel_ext_info` 家族、debugfs 读写的字符串解析 |
| `drivers/iio/industrialio-buffer.c` | 1999 | `:50`、`:118-200`（接口缺失的 `-ENODEV`）、`:265-290`（老 ABI 被新 fd 挡住）、`:333`、`:409-470`（`scan_mask_match` 与 `scan_mask_set`）、`:520-575`、`:655-710`（`compute_scan_bytes`）、`:860-880`（模式选择）、`:905-915`、`:928-1075`（demux 全段 + `enable_buffers`）、`:1270-1280`、`:1350-1360`、`:1530-1600`（ioctl 与 `-EBUSY`）、`:1615-1640`（`scan_index < 0` 跳过、`storagebits` 检查）、`:1850-1890`（三个 push 实现） | 每个 sysfs 属性的 show/store 逐个实现、`iio_buffer_attr` 组的拼装细节、`watermark` 相关路径 |
| `drivers/iio/inkern.c` | 1037 | `:25-102`（map 表与锁）、`:130-200`（DT 索引解析）、`:220-300`（`-ENODEV` 作为「往上找」信号）、`:320-410`（`channel_get`/`put` 与引用计数）、`:460-470`、`:555-600`（两种 `-EINVAL`）、`:631-700`（换算全段）、`:710-801`（`processed` 家族）、`:805-820`、`:885-895`（FIXME）、`:945-955`、`:1010-1035` | `iio_write_channel_*` 家族的实现细节、`iio_read_avail_channel_*` 的分支 |
| `drivers/iio/industrialio-trigger.c` | 750 | `:20-35`（RFC 注释）、`:60-130`（注册与 `trig_readonly`）、`:185-250`（点火与 notify_done）、`:270-300`（attach_poll_func）、`:340-360`（`store_time`）、`:435-455`（切换与 `-EPERM`）、`:475-485`、`:520-570`（`device_type` 与总线赋值）、`:680-715`（alloc/free pollfunc） | `iio_trigger_read_current` / `_write_current` 的 sysfs 字符串处理、`iio_trigger_alloc` 的 vasprintf 细节 |
| `drivers/iio/industrialio-event.c` | 614 | `:30-50`（结构定义）、`:60-90`（`iio_push_event`）、`:125-150`、`:185-225`（`getfd` 与 `kfifo_reset_out`）、`:195-215`（独占）、`:495-510`（ioctl 派发） | 事件属性（`*_thresh_rising_value` 等）的名字合成与 sysfs 实现——这一大块只看了符号表 |
| `include/linux/iio/iio.h` | 792 | `:14-18`（TODO LIST）、`:183-186`、`:238-280`（`iio_chan_spec`）、`:417-433`（`of_xlate` kerneldoc）、`:445-518`（`iio_info`）、`:550-600`（`iio_dev` 及其 kerneldoc）、`:625-640`、`:641-712`（get/put 内联与 `iio_bus_type` 声明） | `IIO_CHAN_SOFT_TIMESTAMP` 等便利宏的展开、`iio_get_time_ns` 家族的 clock_id 分支 |

**只看了符号表、一行实现都没读的文件**（结论全部是从声明和调用点推断的，`05-usage.md` 里凡涉及它们的骨架都在 §5.2 登记）：

| 文件 | 行数 | 为什么没读 |
|---|---|---|
| `drivers/iio/buffer/industrialio-buffer-dma.c` | 682 | DMA 缓冲区后端，本树 arm64 DTS 上零消费者（见 `04-extensions.md` 分诊表） |
| `drivers/iio/buffer/industrialio-buffer-dmaengine.c` | 297 | 同上 |
| `drivers/iio/trigger/stm32-timer-trigger.c` | 918 | STM32 是 arm32 平台，按 §2.7 裁掉 |
| `drivers/iio/trigger/stm32-lptimer-trigger.c` | 118 | 同上 |
| `drivers/iio/trigger/iio-trig-hrtimer.c` | 200 | 只用来举例说明「触发源可以是软件」，没有对它的实现下过论断 |
| `drivers/iio/trigger/iio-trig-loop.c` | 141 | 同上 |
| `drivers/iio/trigger/iio-trig-interrupt.c` | 111 | 同上 |
| `drivers/iio/trigger/iio-trig-sysfs.c` | 225 | 同上；`07-debug.md` 里只用到它的 sysfs 接口形状 |

**代表驱动的选法**（skill 阶段 2 步骤 4.1）。532 个 `.c` 里没有一个被完整读过，只按需打开了片段：

| 驱动 | 打开它是为了确认什么 | 引用位置 |
|---|---|---|
| `drivers/hwmon/iio_hwmon.c` | 消费者 API 的真实用法（`:52` 读换算值、`:78` 取全部通道、`:163` 注册 hwmon） | `00-overview.md` §7 |
| `drivers/iio/adc/ad7266.c` | `iio_triggered_buffer_setup` 的真实调用形状（`:473`）、驱动自定义 `setup_ops`（`:75`） | `04-extensions.md` §2 |
| `drivers/iio/adc/vf610_adc.c` | 同名 `static` 结构的第二个实例（`:765`、`:875`） | `04-extensions.md` §2 |
| `drivers/iio/light/vcnl4035.c` | 同名 `static` 结构的第三个实例（`:363`、`:528`） | `04-extensions.md` §2 |
| `sound/soc/stm/stm32_adfsdm.c` | 缺陷 D3 的证据——驱动把「被迫塞空回调」写进了注释（`:300`） | `04-extensions.md` §1.3.3 |
| `tools/iio/iio_event_monitor.c` | 事件 fd 的真实用法（`:385` open、`:392` ioctl、`:406` close 主 fd） | `07-debug.md` §5、`08-feynman.md` §5 |
| `tools/iio/iio_generic_buffer.c` | 通用缓冲区读取工具存在这一事实本身 | `07-debug.md` §5.4 |

**偏向声明**：`adc` 一个目录占了全部驱动的两成以上（118/532），上表里 4 个代表驱动有 2 个来自 `adc`。这是有意的（跟随分布），但代价是**本文档集对 accel / imu / light 这类「多通道 + 触发器」驱动的实际写法举例偏少**——`04-extensions.md` §2 的骨架来自便利层的代码而不是来自某个具体的 IMU 驱动。

### §2.3 三等证据（`Documentation/`）

`check_doc_coverage.sh` 认定的「必查」档 9 篇，逐篇登记。**引用次数由脚本统计，不是手数的**，取自定稿后最后一次运行。

⚠️ **这一列是下限，不是不变量。** 脚本数的是「这个文件名在本文档集里被提到多少次」，而本篇 §6 / §7 讲方法论时会把这些文件名再提一遍——于是**写取证篇这个动作本身就把计数推高了**。9 篇的数值全部在 §6 写完之后涨过一次（`intro.rst` 2→6、`triggered-buffers.rst` 2→9、`ep93xx_adc.rst` 0→3，其余各涨一到几十次）。正确读法有三条：

- **这一列只能用来回答「有没有 0 引用」**，不能用来比较哪篇官方文档更重要。高的那两篇（`core.rst` 200、`buffers.rst` 123）高在它们是主线，但也高在方法论段落反复拿它们举例。
- **想让计数稳定，唯一的办法是不在 §6/§7/§8 里写出 `.rst` 的基名**。本篇后半确实避开了，但这张表和 §2.3.1 避不开——那里非提不可。
- 所以这一列的有效信息是「每篇都 ≥1」这个事实，数值只是副产品。重跑得到的数只会比这里的大，不会小。

| 文件 | 行数 | 用途 | 引用次数 | 与代码冲突之处 |
|---|---|---|---|---|
| `Documentation/driver-api/iio/intro.rst` | 33 | **IIO 的官方定位**：填 hwmon 与 input 之间的洞。`00-overview.md` §1 / §2.2 的立论基础 | 6 | 无冲突。措辞比代码保守（"considerable overlap"），本文档集据此写成「并列而非包含」 |
| `Documentation/driver-api/iio/core.rst` | 182 | `iio_dev` / `iio_chan_spec` / `iio_info` 的官方讲法，属性名合成规则 | 200 | 无冲突 |
| `Documentation/driver-api/iio/buffers.rst` | 126 | 缓冲区与 `scan_elements` 的官方讲法 | 123 | 无冲突 |
| `Documentation/driver-api/iio/triggers.rst` | 78 | 触发器的官方讲法 | 10 | 无冲突 |
| `Documentation/driver-api/iio/triggered-buffers.rst` | 69 | top half / bottom half 骨架示例（`:17-48`）+ 三条要点（`:52-65`） | 9 | ★ **有冲突**：`:54-55` 说核心有缺省的 `iio_triggered_buffer_setup_ops`，该符号在核心里不存在。详见 §2.3.1 |
| `Documentation/driver-api/iio/hw-consumer.rst` | 50 | 硬件消费者框架的官方讲法 | 59 | 无冲突（但注意实现里的 kerneldoc 有错，即缺陷 D5） |
| `Documentation/iio/iio_configfs.rst` | 102 | 用 configfs 造软件触发器的操作步骤 | 7 | 无冲突 |
| `Documentation/iio/bno055.rst` | 51 | 一个具体传感器（BNO055）的私有属性说明 | 31 | 无冲突。它是「驱动专属 ABI」的样本，`01-api.md` 用它说明 IIO 属性集合是开放的 |
| `Documentation/iio/ep93xx_adc.rst` | 40 | Cirrus EP93xx 的 ADC 说明 | 3（**正文九篇 0 次**） | ★ **按 §2.7 的 arm64-only 范围主动裁掉**。那 3 次都在本取证篇里——这张表、上面那段口径说明、以及 §2.7 的裁剪记录；00-08 九篇一次都没提它。EP93xx 只存在于 `arch/arm/mach-ep93xx/`，`grep -rn ep93xx arch/arm64` 命中 0 条 |

DT 绑定目录：`Documentation/devicetree/bindings/iio/` 共 257 篇，本文档集只引用了 1 篇（`common` 下的通用绑定，14 次）。这是符合 skill 要求的——06 篇的硬要求是**每条属性都要有定位到行的消费代码**，不是每篇绑定都要被引用。

#### §2.3.1 唯一一处 Documentation 与代码的实质冲突

`Documentation/driver-api/iio/triggered-buffers.rst:54-55` 写：若不指定 `setup_ops`，核心会用缺省的 `iio_triggered_buffer_setup_ops`。三条反证：

```bash
# 1. 这个符号在核心里不存在，只有三个驱动各自定义了同名 static
grep -rn 'iio_triggered_buffer_setup_ops' drivers/iio/ include/linux/iio/
#   → drivers/iio/adc/ad7266.c:75 / vf610_adc.c:765 / light/vcnl4035.c:363（都是 static）

# 2. 便利层无条件赋值，传 NULL 就是 NULL
awk 'NR==78' drivers/iio/buffer/industrialio-triggered-buffer.c
#   → indio_dev->setup_ops = setup_ops;

# 3. 真正的兜底在注册时，指向一个全零的 static const
awk 'NR>=1979 && NR<=1981' drivers/iio/industrialio-core.c
awk 'NR==1924' drivers/iio/industrialio-core.c
#   → static const struct iio_buffer_setup_ops noop_ring_setup_ops;   ← 没有初始化式
```

正确结论：`setup_ops` 可以传 NULL，行为是**四个回调全不调**，而不是「有一套缺省实现」。这个兜底还是**承重的**——`industrialio-buffer.c:432`、`:1074`、`:1114`、`:1136`、`:1163`、`:1180` 六处直接解引用 `setup_ops`，没有一处判空。已写进 `04-extensions.md` §2。

### §2.7 架构裁剪记录（arm64-only）

**声明**：本文档集只走 arm64。下面是明确被裁掉的东西——**这些是有意不覆盖，不是遗漏**。

| 被裁掉的 | 为什么 | 影响到哪篇 |
|---|---|---|
| `drivers/iio/trigger/stm32-timer-trigger.c`（918 行）与 `stm32-lptimer-trigger.c`（118 行） | STM32 是 arm32 平台 | `04-extensions.md` 的触发器实现只数到「6 个」，但只对其中 4 个下过论断 |
| `Documentation/iio/ep93xx_adc.rst` | EP93xx 只在 `arch/arm/mach-ep93xx/`，arm64 下 0 命中 | §2.3 里唯一 0 引用的必查档 |
| 全部 ACPI 发现路径 | arm64 上 IIO 设备走 DT，不走 ACPI | `06-devicetree.md` §0 的分诊表里 ACPI 行标为「本篇不覆盖」 |
| x86 专属驱动（`drivers/iio/common/cros_ec_sensors/` 等）的平台细节 | ChromeOS EC 在 arm64 上存在但形态不同，没有在本树 arm64 DTS 上验证 | `04-extensions.md` 只讲它的框架契约，不讲平台胶水 |
| `arch/powerpc/` 与 `arch/x86/` 下所有 iio 相关内容 | 超出范围 | 全篇 |

**裁剪不影响的部分**：IIO 核心九个文件里**没有一行架构条件编译**。

```bash
grep -c 'CONFIG_ARM\|CONFIG_X86\|CONFIG_ARM64\|CONFIG_PPC\|#ifdef CONFIG_OF' drivers/iio/*.c
```

实跑结果：**九个文件全部为 0**（这里不贴原生输出——`grep -c` 的 `文件名:数字` 格式会被行号检查脚本当成越界行号，见 §6.1 的自指陷阱）。

所以「核心的行为与架构无关」这个论断是安全的；裁剪只影响**驱动侧和发现路径侧**的覆盖范围。

---

## §3 逐篇取证表

每篇列三到六条**最容易被误引、代价最高**的论断，加上这一篇在自审里被改过的地方。**「自审修正记录」一列是本节的重点**——它是这套文档做过自审的唯一凭据。

篇幅现状：00 = 465 行，01 = 590，02 = 803，03 = 1073，04 = 999，05 = 1232，06 = 786，07 = 884，08 = 406，本篇独立统计。

### §3.1 `00-overview.md`（复习入口）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `iio_bus_type` 只有 `.name`，这条总线上不发生驱动绑定 | `drivers/iio/industrialio-core.c:42-44`；`grep -c 'driver_register\|struct device_driver'` 该文件为 0 | 无修正。这条是全篇的立论支点，也是子系统类型判定的第三道门（§1.3） |
| IIO 填的是 hwmon 与 input 之间的洞 | `Documentation/driver-api/iio/intro.rst`（33 行） | 原稿把 intro.rst 记成 22 行，实测 33 行，已改 |
| ABI 全部停在 `testing`，`stable` 下零文件 | 52 个 `sysfs-bus-iio*` + 1 个 `configfs-iio`；`ls Documentation/ABI/stable/ \| grep -ci iio` → 0 | ★ 台账里继承的数字是「54（1 通用 + 53 专属）」，实测 52（1 + 51）。见 §6 交叉验证 J |
| `struct iio_dev` 在 `iio.h:571-596`，`iio_info` 在 `:445-518` | `grep -n '^struct iio_dev {'` / `'^struct iio_info {'` | ★ 初稿写 `:550` / `:417`——那是 kerneldoc 注释块的起始行，不是结构定义行。已改，并与 `02-datastructures.md:253` / `:367` 对齐 |
| `iio_event_getfd()` 在 `industrialio-event.c:192` | `grep -n 'static int iio_event_getfd'` | ★ 初稿写 `:206`——那是函数体内 `-EBUSY` 的返回行。已改，并与 `07-debug.md:490` 对齐 |
| `types.h` 里是 4 个枚举，值类型码不在 uapi | `include/uapi/linux/iio/types.h` 只有 4 个 `^enum`；`IIO_VAL_INT` 定义在 `include/linux/iio/types.h:24` | ★ 初稿写「5 个枚举……以及值类型码」，把非 uapi 的东西算进了 uapi。已改，并补了一段说明这是「事实上的 ABI、形式上没导出」 |
| `drivers/iio/Kconfig` 7 个选项、50 个文件（51 个赋值点）实现 `debugfs_reg_access` | `grep -c '^config ' drivers/iio/Kconfig` → 7；`grep -rln '\.debugfs_reg_access' --include=*.c drivers/iio/ \| wc -l` → 50 | ★ 初稿写「8 个选项」，错。文件数一度被写成 53——那是漏了模式里的前导点、把三个消费该字段的核心/库文件也算进了实现者。见 §6 交叉验证 P |
| `drivers/staging/iio/` 还有 7 个驱动目录 | `ls -d drivers/staging/iio/*/ \| grep -v Documentation \| wc -l` → 7 | ★ 正文数字（7）是对的，但**发布的重跑命令少了过滤**，直接跑会得到 8（多算了 `Documentation/`）。命令已改。这是规则 30 最后一条的典型：数字和命令要逐格对账 |

### §3.2 `01-api.md`（代码地图 + 对外 API）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `iio_core.h` 是核心内部头，24 个声明不对外 | `drivers/iio/iio_core.h`（111 行），位置在 `drivers/` 下而非 `include/` 下 | 无修正 |
| `iio_dev_opaque` 的字段驱动不能碰，有 getter 就用 getter | `include/linux/iio/iio-opaque.h`；`iio_device_get_current_mode` / `iio_device_get_clock` / `iio_buffer_enabled` | 无修正 |
| 「符号存在」≠「读者能用」，判据是 `EXPORT_SYMBOL*` + 头文件声明 | 逐个符号 grep | 无修正（这是 skill 规则 20 的直接落地） |

### §3.3 `02-datastructures.md`（结构与关系）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| IIO 是**框架型**子系统 | 判定过程见本篇 §1.3；结论在 `02-datastructures.md:618` 复述了一次 | 无修正 |
| `iio_dev` 19 个字段，是唯一驱动能直接读写的 IIO 结构体，但只有一部分能写 | `include/linux/iio/iio.h:571-596` | 无修正 |
| `iio_info` 18 个成员 = 2 个 `attribute_group` 指针 + 16 个回调 | `include/linux/iio/iio.h:445-518` | 无修正 |
| `iio_buffer_access_funcs` 是 12 个函数指针 + 2 个非回调字段 | `include/linux/iio/buffer_impl.h:53-73` | 无修正 |
| 分层图里「字符设备 fileops」归 `industrialio-buffer.c` | 原写在 `02`/`03` 的层次图里 | ★ **错**：两套 fops 都定义在 `industrialio-core.c`（`:1862-1872` 和 `:1874-1881`）。修 08 时发现，按规则 17 grep 全集，`03-internals.md:28` 的层次图也被改 |

### §3.4 `03-internals.md`（主流程）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| 注册尾段的 fops 是二选一：有缓冲区用 `iio_buffer_fileops`，否则用 `iio_event_fileops` | `drivers/iio/industrialio-core.c:1983-1986` | 无修正 |
| `devt` 只在「有缓冲区或有事件接口」时才赋值 | `:1988-1991` | 无修正 |
| `setup_ops` 的兜底在注册时，指向全零的 `noop_ring_setup_ops` | `:1924` 声明、`:1979-1981` 赋值 | 本轮新增（写 §2.3.1 时才发现），已同步到 `04-extensions.md` §2 |
| 层次图里 `industrialio-core.c` 负责 fops 装配 | `:1862-1886` | ★ 见 §3.3 最后一行，本篇的层次图被同一个错误波及并已改 |
| Part C 列出源码自陈债 | 见本篇 §4 | ★ IIO 核心的自陈债**异常地少**（只有 2 个 FIXME/TODO 记号），Part C 的分量因此更多来自「行为观察」而不是「注释摘录」。这个差异本身登记在 §4 |

### §3.5 `04-extensions.md`（11 个子框架）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| 全篇唯一一处「框架 A 硬依赖框架 B」：triggered-buffer → kfifo | `drivers/iio/buffer/industrialio-triggered-buffer.c:59` 直接调 `iio_kfifo_allocate()`；`drivers/iio/buffer/Kconfig:53` 写了 `select IIO_KFIFO_BUF` | 无修正 |
| triggered-buffer 便利层有 117 个消费者，是 IIO 驱动的默认写法 | 全树 grep，带重跑命令，见该篇 §8.2 | 无修正 |
| 八条缺陷 D1–D8 | 逐条见 §4.2 | 无修正（D1 的「为什么它无害」推导是自审时补的） |
| `iio_triggered_buffer_setup_ops` 是核心提供的缺省 ops | `Documentation/driver-api/iio/triggered-buffers.rst:54-55` 这么说 | ★ **文档错，代码对**。本轮新增整段反证（§2.3.1）并写进正文 |
| `iio_trigger_ops` 的实现数 | 全树 grep | ★ 初稿写 13，实测 53。见 §6 交叉验证 A |

### §3.6 `05-usage.md`（角色骨架）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| 骨架未编译未运行 | 声明在该篇篇首 `:18` | 无修正 |
| `iio_buffer_access_funcs` 四个后端里唯一都填了的是 `.release` | `include/linux/iio/buffer_impl.h:53-73` + 四个后端逐个核对 | 无修正 |
| 缺陷 D7：`sw_device.h:54-55` / `sw_trigger.h:54-55` 的四个函数全树无定义 | 两个头文件 + 全树 grep 无命中 | 无修正。**这条对 05 的读者最危险**——照头文件写会得到链接错误 |
| 时间戳缓冲区的对齐要求 | `include/linux/iio/buffer.h:37-45` | ★ 自审时把 `aligned_s64` 与 `__aligned(8)` 两种写法的分布数错过一次，见 §6 交叉验证 E |

### §3.7 `06-devicetree.md`（DT）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `-ENODEV` 在 `inkern.c` 的四处是「继续往 `io-channel-ranges` 上级找」的信号，不是错误 | `drivers/iio/inkern.c:265`、`:280`、`:390`、`:466` | 无修正。**这条决定了「换个错误码返回」会破坏查找链** |
| `io-channel-names` 匹配失败时 `inkern.c:236` 故意返回 `-EINVAL` 终止搜索 | `:236` | 无修正 |
| `of_xlate` 的默认实现让「DT 里的数字 = channels[] 下标」成立 | `include/linux/iio/iio.h:427-433` 的 kerneldoc + 默认路径 | ★ kerneldoc 与 `#iio-cells` 的配对关系有歧义，登记在 §4.3 |
| 「Kconfig ≠ 架构门」 | 反复出现的过读 | ★ 自审累计纠正 3 次：`IIO_BUFFER` 之类的 Kconfig 选项不表示架构限制。见 §6 交叉验证 K |

### §3.8 `07-debug.md`（观测面）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| 核心九个文件共 28 个打印点，`industrialio-event.c` 一个都没有 | 逐文件逐类 grep，普查表在该篇 §7.1 | ★ 初稿在 `:26` 写「27 条」，与 §7.1 表格的合计不一致。已改成 28 |
| 三张名字表的项数：35 / 50 / 29 | `sed -n '54,90p' \| grep -c 'IIO_'` → 35；`'92,143p' \| grep -c 'IIO_MOD'` → 50；`'146,178p' \| grep -c 'IIO_CHAN_INFO_'` → 29 | ★★ 初稿写 `iio_chan_type_name_spec[]` = **85**，第二格是 `—`。**跑一遍自己发布的命令**就发现 85 是凭空的，实测 35。见 §6 交叉验证 I |
| `iio_modifier_names[]` 50 项 vs `enum iio_modifier` 51 项，差的是 `IIO_NO_MOD` | `include/uapi/linux/iio/types.h:52` 起逐项数 | ★ 填掉那个 `—` 才发现的差额。规则 19「`—` 是欠债记号」的直接收益 |
| 三个 WARN 站点，其中一个之后代码继续执行 | `industrialio-core.c:1085`、`industrialio-buffer.c:455`、`industrialio-trigger.c:124`（`:126`–`:127` 继续跑） | 无修正 |
| 19 条「一定会打印」的核心消息，其中 5 条挂在 `dev.parent` 而不是 `iio:deviceN` | 该篇 §7.4；5 条是 `:398`、`:437`、`:1959`、`:1966`、`:1972` | 无修正。**这 5 条 `dmesg \| grep iio` 抓不到** |
| 错误码 grep 命中数：`-EINVAL` 71、`-ENODEV` 35、`-EBUSY` 23 | 显式列出 9 个文件的 grep | ★ 口径被纠正过一次：`-EBUSY` 的 23 个命中里只有 16 个是真实产生点，其余是 kerneldoc 行、`if (ret == -EBUSY)` 比较、`int ret = -ENODEV` 初始化。见 §6 交叉验证 H |

### §3.9 `08-feynman.md`（概念）

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `iio_trigger_poll()` 用编译期常量而不是实际消费者数设 `use_count` | `drivers/iio/industrialio-trigger.c:200` | 无修正。**这是本篇最有价值的一条**——它解释了为什么触发器丢帧是静默的 |
| 换算公式是「先加 offset 再乘 scale」 | `drivers/iio/inkern.c:641`（判据）、`:662`（加）、`:665-666`（读 scale）、`:678`（乘） | ★ 本节有**五处**行号是在 `sed` 窗口上做算术得来的，全部偏 1–2 行，逐条用 `awk 'NR>=639 && NR<=690'` 核对后改正。见 §6.2 机械损坏类 (a) |
| `IIO_VAL_INT_PLUS_MICRO`/`_NANO` 的 offset 被静默截断成整数 | 注释 `inkern.c:645-651`，实现 `:647-650` | 无修正 |
| 两套 fd 的 ioctl 方向不同（`_IOR` vs `_IOWR`） | `include/uapi/linux/iio/events.h:27`、`include/uapi/linux/iio/buffer.h:8` | ★ 初稿两处只写了裸文件名加行号（没带 `include/uapi/linux/iio/` 前缀），被 `check_line_refs.py` 报 AMBIGUOUS——`events.h` 和 `buffer.h` 在树里各有两个同名候选（`include/linux/iio/` 下还有一份）。已补全路径 |
| `iio_event_fileops` **没有** `.read` | `industrialio-core.c:1874-1881` | ★ 初稿把这个结构归到 `industrialio-event.c`（只有 614 行），被 `check_line_refs.py` 的越界报警抓到。见 §6.2 机械损坏类 (e) |
| `scan_index < 0` 的两个跳过点 | `industrialio-buffer.c:1622`（不生成 scan-element 文件）、`industrialio-core.c:1892`（不参与重复索引检查） | ★ 初稿这条只有断言没有出处，补齐后才发现第二个跳过点的存在理由（多个 -1 否则会在重复检查里互撞） |
| `struct iio_demux_table` 的字段是 `from`/`to`/`length` | `industrialio-buffer.c:937-939` | ★ 初稿写成 `in_loc`/`out_loc`——那是调用者的局部变量（`:1008-1009`、`:1019-1020` 处做 `roundup` 对齐）。已改 |

---

## §4 自陈问题与本文档集发现的缺陷

分三类：源码自己写下的债记号（§4.1）、本文档集读代码时发现的缺陷（§4.2）、注释与实现冲突（§4.3）。**只有 §4.1 是维护者的自陈，§4.2 和 §4.3 是本文档集的判断，不是上游共识。**

### §4.1 源码里的债记号：异常地少

```bash
grep -rn 'FIXME\|TODO\|REVISIT\|XXX\|HACK\|BUG:' \
     drivers/iio/*.c drivers/iio/*.h include/linux/iio/*.h
```

**全部命中只有 2 条**：

| 位置 | 内容 | 解读 |
|---|---|---|
| `include/linux/iio/iio.h:14-18` | `/* IIO TODO LIST */` 下面一句：`Provide means of adjusting timer accuracy. Currently assumes nano seconds.` | 时间戳精度写死在纳秒。这条 TODO 至今没做，也解释了为什么 `iio_get_time_ns` 家族只有 clock_id 可调、没有精度可调 |
| `drivers/iio/inkern.c:893` | `/* FIXME: learn about max for other iio values */` | 在 `iio_channel_read_max()`（`:856-901`，由 `iio_read_max_channel_raw()` `:903` 调用）的 `IIO_AVAIL_LIST` 分支里：只有 `IIO_VAL_INT` 会真的求最大值（`:885-891`），其余值类型码一律 `-EINVAL`（`:894`）。**注意同一函数的 `IIO_AVAIL_RANGE` 分支没有这个限制**（`:870-879` 直接取数组下标），所以「求最大值支不支持」取决于驱动的 `read_avail` 返回哪种形式 |

另外两条不带记号但语气等价的自陈：

| 位置 | 内容 | 解读 |
|---|---|---|
| `drivers/iio/industrialio-trigger.c:22-31` | 一整段 `/* RFC - Question of approach */`，最后一句 `Any other suggestions?` | 「多设备同时启动」的复杂场景需要触发器的 hold 功能，**明确写了 `(not implemented)`**（`:28`）。这就是本文档集反复提到的「触发器点火没有排队、丢了就丢了」的设计源头 |
| `drivers/iio/industrialio-buffer.c:871-876` | `/* Keep things simple for now and only allow a single buffer to be connected in hardware mode. */` | 硬件缓冲区模式下只允许一个缓冲区，第二个返回 `-EINVAL`（`:876`）。「多缓冲区」这个特性只在软件/触发模式下完整 |

**这个「异常地少」本身是一条观察**：2153 行的 `industrialio-core.c` 里零个 FIXME。可能的解读有两个——代码确实成熟，或者债没有被写下来。本文档集倾向后者的一部分：§4.2 的八条缺陷里有五条（D1、D2、D4、D6、D7）是任何维护者读一遍都会看见的，但源码里没有任何记号。所以**不要把「没有 FIXME」当成「没有问题」**。

### §4.2 本文档集发现的八条缺陷（D1–D8）

完整推导在 `04-extensions.md` §8.3，这里只做索引和严重度排序。**严重度是本文档集的判断。**

| 编号 | 位置 | 类型 | 一句话 | 会怎么咬到你 |
|---|---|---|---|---|
| **D8** | `drivers/iio/inkern.c:166-167` | 无锁裸解引用 | 解引用 `indio_dev->info->fwnode_xlate`，既不取 `info_exist_lock` 也不判 NULL；同文件另 9 处公开入口全部先取锁再判 | 提供方在消费者 probe 窗口内被 unbind → 空指针。**最严重的一条** |
| **D6** | `drivers/iio/industrialio-sw-device.c:59-64` | 错误路径漏回滚 | `configfs_register_default_group` 失败时不把类型摘下链表 | 模块加载失败后链表上留着指向已释放模块内存的表项。孪生文件 `industrialio-sw-trigger.c:61-66` 做了回滚 |
| **D7** | `include/linux/iio/sw_device.h:54-55`、`include/linux/iio/sw_trigger.h:54-55` | 幻影声明 | 四个函数只有声明，全树无定义 | 照头文件写代码得到**链接错误**而不是编译错误——排查成本高 |
| **D2** | `drivers/iio/buffer/kfifo_buf.c` `iio_set_length_kfifo()` | 静默钳位 | 用户写入的 `length` 小于 2 时被悄悄改成 2 | sysfs 读回来和写进去的不一致，无任何日志 |
| **D5** | `drivers/iio/buffer/industrialio-hw-consumer.c:152` | kerneldoc 与实现冲突 | 注释说失败返回 NULL，实现返回 `ERR_PTR`；同文件 `:79` 的另一份注释是对的 | 照注释写 `if (!p)` 判空 → 拿着 `ERR_PTR` 当有效指针用 |
| **D3** | `drivers/iio/buffer/industrialio-buffer-cb.c:54-57` | 接口强制项 | `iio_channel_get_all_cb()` 强制要求非空回调 | 不需要回调的消费者只能塞空函数——`sound/soc/stm/stm32_adfsdm.c:300` 把这条抱怨写进了注释 |
| **D4** | `drivers/iio/buffer/industrialio-buffer-cb.c` 构造失败路径 | 职责重叠 | 构造失败时直接 `kfree` 而不走 `iio_buffer_put` | 引用计数恰好为 1 所以现在是对的，但改动时容易破 |
| **D1** | `drivers/iio/buffer/kfifo_buf.c` `iio_kfifo_remove_from()` | 死代码 | `:158` 的 `kfifo_size(&kf->kf) < 1` 恒为假 | 不影响行为。留在表里是因为它会误导读者以为「这里拦住了空缓冲区取数」——真正拦住的是 `:160-162` |

两条**不算缺陷但值得记的设计观察**（不混进上表，避免把「我不喜欢」写成「它是错的」）：

- `scan_mask` 布局在两个孪生框架里不一致：callback 缓冲区用 `bitmap_zalloc`（`industrialio-buffer-cb.c:90-91`），hw-consumer 用柔性数组 + `struct_size()`（`industrialio-hw-consumer.c:32`、`:76-77`）。都能工作。
- `modes` 清位不一致：`iio_triggered_event_cleanup()` 还原 `indio_dev->modes`（`industrialio-triggered-event.c:57`），`iio_triggered_buffer_cleanup()` 不还原。正常拆除路径上无差别。

### §4.3 注释与实现冲突汇总（skill 规则 6）

| 位置 | 注释说 | 代码做 | 处置 |
|---|---|---|---|
| `drivers/iio/buffer/industrialio-hw-consumer.c:152` | 失败返回 NULL | 返回 `ERR_PTR` | 登记为缺陷 D5；同文件 `:79` 的注释是对的，所以这不是「整个文件的注释风格」问题 |
| `Documentation/driver-api/iio/triggered-buffers.rst:54-55` | 核心有缺省的 `iio_triggered_buffer_setup_ops` | 该符号在核心不存在；兜底是全零的 `noop_ring_setup_ops`（`industrialio-core.c:1924`、`:1979-1981`） | 全段反证写进 §2.3.1 与 `04-extensions.md` §2 |
| `include/linux/iio/iio.h:427-433`（`@of_xlate` 的 kerneldoc） | 描述与 `#iio-cells` 的配对关系 | 默认路径下「DT 数字 = `channels[]` 下标」只在驱动**没有**提供 `of_xlate` 时成立，kerneldoc 没有把这个条件写清 | `06-devicetree.md` 显式补了这个条件；`08-feynman.md` §6 把它列为误导点 |
| `iio_trigger_ops` 的 kerneldoc | 对回调的必填要求比代码严格 | 代码里多数回调可为 NULL | `05-usage.md` 按代码写，并注明 kerneldoc 更严 |

---

## §5 未验证项（四层）

### §5.1 未编译未运行

- **`05-usage.md` 全篇的骨架代码没有编译过、没有运行过。** 声明在该篇 `:18`。骨架里的符号名逐个 grep 确认过存在（skill 规则 20），但**符号存在 ≠ 这段代码能编过**——参数个数、类型匹配、头文件包含顺序都没有验证。
- **`06-devicetree.md` 的 DTS 片段没有编译过**（没跑 `dtc`），也没有在真实硬件上加载过。节点名和属性名取自本树真实 DTS，但组合是本文档集拼的。
- **`07-debug.md` 里所有 shell 命令都跑过了**（在本树上、对源码做静态统计的那些）。但**需要真实 IIO 硬件才能跑的命令没跑过**——`cat /sys/bus/iio/devices/...`、`iio_generic_buffer`、`iio_event_monitor` 的输出**全部是从代码推出来的形状，不是抄的真实输出**。这一条最容易被误当成实测。

### §5.2 只读符号表（行为是推断的）

按 §2.2 的登记，下面这些地方的结论是从声明、调用点和命名推断的：

| 范围 | 推断了什么 | 风险 |
|---|---|---|
| `industrialio-event.c` 的事件属性合成（约 300 行没读） | 事件属性名（`*_thresh_rising_value` 等）的拼法与通道属性同源 | 中。如果它有独立的拼名逻辑，`01-api.md` 的属性名规则会漏一类 |
| `industrialio-buffer.c` 的每个 sysfs 属性 show/store | 它们只是薄封装，真正的逻辑在本篇 §2.2 登记的详读区间里 | 低 |
| DMA 缓冲区后端（`buffer-dma.c` 682 行 + `buffer-dmaengine.c` 297 行） | 它们实现了 `iio_buffer_access_funcs` 的同一套契约 | 中。`04-extensions.md` 对这两个框架只讲契约、不讲实现，是有意的 |
| 6 个通用触发器实现 | 它们都通过 `iio_trigger_register` 走同一条注册路径 | 低。注册路径本身是一等证据 |
| `iio_write_channel_*` / `iio_read_avail_channel_*` 家族 | 与对应的 read 家族对称 | 中。`01-api.md` 里这几个 API 的「备注」列比 read 家族薄 |

### §5.3 推断而非观察（没有运行时验证）

这一层是最需要读者警惕的。下面每条都是**从代码结构推出来的因果链，没有一条被运行时观察证实过**：

| 结论 | 推理依据 | 怎么自己验证 |
|---|---|---|
| 缓冲区没挂触发器就 enable 会得到 `-EINVAL` 且无任何日志 | `industrialio-buffer.c:860-880` 的模式选择走不到 `INDIO_BUFFER_TRIGGERED`，且该路径上没有打印点 | 在真机上不挂触发器 `echo 1 > buffer/enable`，看返回值和 `dmesg` |
| 上一轮消费者未全部 done 时，本轮触发被整个丢掉、不排队、不计数 | `industrialio-trigger.c:198` 的 `if (!atomic_read(&trig->use_count))` 前置判断 + 无计数器 | 用高频 hrtimer 触发器压一个慢 bottom half，看实际采样率与设定值的差 |
| 5 条核心消息挂在 `dev.parent` 上，`dmesg \| grep iio` 抓不到 | 5 处 `dev_err(indio_dev->dev.parent, ...)` 的第一参数 | 制造一次失败，比较 `dmesg \| grep iio` 与全量 `dmesg` |
| 主字符设备的独占会挡住第二个打开者 | `industrialio-core.c:1775-1776` 的 `test_and_set_bit` | 两个进程同时 `open("/dev/iio:device0")` |
| 新拿的事件 fd 会丢掉积压事件 | `industrialio-event.c:218` 的 `kfifo_reset_out()` | 制造事件后不读，重开 event fd，看是否收到 |
| 老 ABI 的 `read()` 在某缓冲区拿过独立 fd 后返回 `-EBUSY` | `industrialio-buffer.c:272`、`:285` | 先用新 ioctl 取 fd，再用老工具读主节点 |
| `available_scan_masks` 会让硬件采比你启用的更多的通道 | `industrialio-buffer.c:409` 的 `iio_scan_mask_match` 语义 + `:469` / `:910` 两处调用 | 找一个定义了 `available_scan_masks` 的驱动，只开一个通道，看 `buffer/length` 对应的 `scan_bytes` |
| demux 的恒等变换也照走一次 memcpy | `industrialio-buffer.c:1039-1046` 无「无需变换则跳过」的分支 | 加 tracepoint 或 ftrace `iio_update_demux` 下游的 memcpy 计数 |

**「静默失败」这一整类结论全部落在这一层。** 它们的推理都是同一个形状：*代码路径上没有打印点 + 返回值就是全部信息*。这个推理对「不会打印」是可靠的（打印点是可穷举的），对「用户会看到什么现象」是推断的。

### §5.4 版本时效性

| 事项 | 本版本（6.1.158）的状态 | 变化风险 |
|---|---|---|
| ABI 全在 `Documentation/ABI/testing/`，`stable/` 下零文件 | 52 + 1 个文件 | 中。属性可能被增删；但 IIO 的核心属性名已事实稳定多年 |
| `drivers/staging/iio/` 还有 7 个驱动目录，TODO 文件日期 2020-02-25 | 未毕业 | 低。停滞了五年多 |
| `CONFIG_IIO_CONSUMERS_PER_TRIGGER` 缺省 2 | `drivers/iio/Kconfig:41` 起 | 低 |
| 两代缓冲区 ABI 共存（主节点老 `read()` + 每缓冲区独立 fd） | 共存，老路径被新 fd 挡住 | ★ **高**。这是一次进行中的 ABI 迁移，后续版本可能移除老路径。`08-feynman.md` §5 的整节都建立在「两者共存」上 |
| `iio_dev` 的公开字段集合 | 19 个 | 中。上游一直在往 `iio_dev_opaque` 里搬字段，公开字段会继续减少 |
| 三张名字表的项数 35 / 50 / 29 | 见 §3.8 | ★ **高**。新传感器类型和修饰符每个版本都在加。**这三个数字是本文档集里最容易过期的数字。** |
| 全树 `iio_device_register` 真实调用点 388 个、532 个 `.c` 文件 | 见 §8 | 高。驱动数量单调增长 |

### §5.5 对端 / 树外实现

**不适用。** IIO 没有树外对端：

- 没有一份树外规范定义 IIO 的线上格式（§1.3 门 2）。缓冲区里的字节布局由 `scan_type` 在本树里完整描述。
- 用户态工具的对端在本树里（`tools/iio/`），已作为二等证据读过。
- 硬件寄存器布局属于芯片数据手册，但**本文档集不对任何具体芯片的寄存器下论断**——所有硬件访问都停在驱动的 `read_raw` 回调边界上。这是覆盖边界，登记在这里而不是当成「已核对」。

唯一接近「树外」的地方是**用户态程序怎么解析 `_scale` 字符串**。值类型码的格式约定事实上是 ABI，但它定义在 `include/linux/iio/types.h:24-32`（**不在 uapi**），所以用户态只能靠 `Documentation/ABI/testing/sysfs-bus-iio` 的散文描述和惯例。这一点写在 `00-overview.md` §2.6，本文档集**没有**核对任何用户态库（libiio 等）的实际解析行为。

---

## §6 交叉验证与自审收获

skill 规则 15 说"每一轮 review 自己会产生新的错误"，规则 30 说"每一个数字都是一条能被读者一条命令推翻的论断"。这一节是这两条的账本：**十七条初始判断被推翻的实例**，按"当时怎么想的 → 什么动作推翻了它 → 落到了哪一篇"三段记录。

留着它们的理由有三个。一是**反例把结论的方向钉准了**——只写正确结论，读者无法判断哪些地方容易看错。二是**推翻方式本身是可复用的**：十七条里有十条是"跑一遍自己写的命令"抓出来的，四条是"填一张必须填满的表"抓出来的，两条是"改一个数字后 grep 全集"抓出来的，只有一条来自重读正文。三是**它标出了源码本身有歧义的地方**（E、N、P 三条尤其），这些地方值得在正文里显式写"为什么容易看错"。

### §6.1 十七条实例

#### A · `iio_trigger_ops` 的实现数：13 → 53

**当时怎么想的**：写 `04-extensions.md` §3（触发器）时数了一遍 `iio_trigger_ops` 的实现，得到 13 个，就写成了"全树 13 个实现"。**当时给自己的解释是"通用触发器 3 个 + 少量传感器驱动自带的 10 个"**——这个解释听起来很合理，所以数字没被质疑。

**什么动作推翻了它**：把模式的字符类补上数字再跑一遍：

```bash
grep -rn 'struct iio_trigger_ops [a-z_0-9]* = {' --include=*.c drivers/ | wc -l   # → 53
```

**13 这个数不是"某个目录下的数量"，而是全树里名字不含数字的那些实现的数量**——这一点可以直接验证：

```bash
grep -rhoE 'struct iio_trigger_ops [a-z_0-9]+ = \{' --include=*.c drivers/ \
  | sed 's/struct iio_trigger_ops //;s/ = {//' | grep -cv '[0-9]'      # → 13
```

13 个名字里不含数字的实现是 `adis_`、`hid_sensor_`、`iio_hrtimer_`、`iio_loop_`、`inv_mpu_`、`mxs_lradc_adc_`、`st_accel_`、`st_gyro_`、`st_magn_`、`st_press_`、`sx_common_`、`timer_`、`xadc_` 这十三个前缀。而 `drivers/iio/trigger/` 目录下实际只有 4 个赋值点——**所以"13 个"和"这个目录"从来就没有对应关系，是巧合让错误的数字配上了一个说得通的解释。**

漏掉的那 40 个全是"芯片型号 + 后缀"的命名，也就是传感器驱动的标准命名法：`adxl372_trigger_ops`（`drivers/iio/accel/adxl372.c:1137`）、`stk8312_trigger_ops`（`drivers/iio/accel/stk8312.c:238`）、`kxcjk1013_trigger_ops`（`drivers/iio/accel/kxcjk-1013.c:1279`）、`mcp3911_trigger_ops`（`drivers/iio/adc/mcp3911.c:420`）、`ad7768_trigger_ops`（`drivers/iio/adc/ad7768-1.c:545`）。**漏的不是零散几个，而是一整类。**

**落地**：`04-extensions.md` §3 与 §7 汇总表；`00-overview.md` §5 的机制速览。这条落地做窄了，同一个数字在 `02-datastructures.md` §1.1 又发布了一遍、错了一遍——见 Q。

#### B · 注册点计数 395 → 390 → 388：一条命令里叠了两个错

**当时怎么想的**：`00-overview.md` §2.5 要给"全树有多少处调 `iio_device_register` / `devm_` 版本"。命令写成了"递归 grep 整棵树，再把核心自己的文件滤掉"，滤掉的写法是 `grep -v '^./drivers/iio/industrialio'`——那个 `./` 是照着"`grep -r .` 的输出带 `./` 前缀"这个印象加的。得到 395。

**什么动作推翻了它**：本轮逐格对账，把两种写法各跑一遍：

```bash
grep -rn 'iio_device_register(\|devm_iio_device_register(' --include=*.c . \
  | grep -v '^./drivers/iio/industrialio' | wc -l    # → 395（过滤器空转）
grep -rn 'iio_device_register(\|devm_iio_device_register(' --include=*.c . \
  | grep -v '^drivers/iio/industrialio'  | wc -l    # → 390
```

**GNU grep 递归搜 `.` 时不加 `./` 前缀**（`grep -rln … . | grep -c '^\./'` → 0），所以带 `./` 的模式一行都匹配不上，过滤器整条空转。多出来的 5 行全部来自核心自己：

```
drivers/iio/industrialio-triggered-event.c:25   ← kerneldoc 里提到这个函数名
drivers/iio/industrialio-event.c:63             ← kerneldoc 里提到这个函数名
drivers/iio/industrialio-core.c:1926            ← __iio_device_register 的定义
drivers/iio/industrialio-core.c:2046            ← __devm_iio_device_register 的定义
drivers/iio/industrialio-core.c:2051            ← 前者调后者
```

**一条命中都不是"驱动注册了一个 IIO 设备"**——两条是 kerneldoc 里提到函数名，三条是核心自己的定义与内部调用。

**改对过滤器还不够，390 里还剩两条假命中。** 387 个文件里有 3 个文件各有两处命中，逐个打开看：

```
drivers/iio/imu/kmx61.c:1385 / :1391                 ← 真的注册了两个 iio_dev（accel + magnetometer）
drivers/iio/adc/palmas_gpadc.c:613                   ← dev_err 的消息串里含函数名
drivers/input/touchscreen/tsc2007_iio.c:130          ← 同上
```

后两条是 `"iio_device_register() failed: %d\n"` 这样的**错误消息字符串**。滤掉含双引号的行之后：

```bash
grep -rn 'iio_device_register(' --include=*.c . \
  | grep -v '^drivers/iio/industrialio' | grep -v '"' | wc -l    # → 388
```

**最终口径：388 个真实注册调用点、387 个文件**，唯一一个"一个文件两次注册"的是 `drivers/iio/imu/kmx61.c`——一颗六轴芯片注册成两个独立的 IIO 设备。

**这条一条命令里叠了两个错**：过滤器空转（395→390）属于自造的操作损坏，消息串混进计数（390→388）属于工具语义损坏（§6.2.1 类 c）。两个错方向相同、都让数字虚高，所以互相掩护——只看总数完全看不出有问题。

**同一个坑换工具就反过来**：`find . -name 'industrialio-core.c'` 输出的**确实**是 `./drivers/iio/industrialio-core.c`，带前缀。所以这不是"记错了"，而是**两个工具的行为不同、混用时必须现场验一次**——而验的方法就是把过滤器去掉再跑一次，看数字有没有变。**过滤器"看起来生效了"是最危险的状态：数字变小了会让人放心，数字没变则完全无声。**

**落地**：`00-overview.md` §2.5 的表与命令改为 388；本篇 §5.4 与 §8 同步改为 388 / 387。

#### C · "`iio_channel_read()` 裸解引用 `indio_dev->info`"——被源码推翻

**当时怎么想的**：读 `drivers/iio/inkern.c` 的 in-kernel 消费者路径时，看到一堆 `chan->indio_dev->info->read_raw(...)` 形式的调用，第一反应是"设备正在被 unregister 时这里会解引用到已置 NULL 的 `info`"，准备把它写成一条缺陷。

**什么动作推翻了它**：把 `inkern.c` 里每一个这样的调用点的**上一行**打开看。它们全部包在 `info_exist_lock` 里：

```bash
grep -n 'mutex_lock(&iio_dev_opaque->info_exist_lock)' drivers/iio/inkern.c | wc -l   # → 9
```

九处，正好覆盖 `read_raw` / `write_raw` / `read_avail` / `read_label` / `ext_info` 的每一条对外入口。`info == NULL` 的检查就在锁内紧跟着（`drivers/iio/inkern.c:599-602` 一类），拿不到 `info` 时返回 `-ENODEV` 并跳到 `err_unlock`。这是一个**设计过的**竞态防护，不是漏洞。

**落地**：`03-internals.md` Part B 的锁一节把 `info_exist_lock` 单列成一条"专门为 in-kernel 消费者存在的锁"；`04-extensions.md` 的 in-kernel 消费者一节不再声称有这条缺陷。**这是唯一一条"初稿写的是缺陷、终稿写的是设计"的翻转。**

#### D · 核心消费的 DT 属性数：5 → 6（换行把一个属性藏了）

**当时怎么想的**：普查 `drivers/iio/*.c` 里出现的 DT 属性名字符串，命令是"抓 `of_property` / `fwnode_property` / `device_property` 的命中行，再从行里抠出带引号的小写标识符"。得到 5 个，就写成了"核心只读 5 个属性"。

**什么动作推翻了它**：写 §1.1 总表时要给每个属性填"写在哪个节点"一列，填到 `io-channels` 时意识到**提供方那一侧必须有个 `#io-channel-cells`**，否则 phandle 根本解析不了——可它不在 5 个里面。回去看代码，属性名是 `fwnode_property_get_reference_args()` 的第二个实参，换行落到了下一行上（`drivers/iio/inkern.c:152-153`、`:298-299`），所以和函数名不在同一行。加 `-A1`：

```bash
grep -rnE -A1 'of_property|fwnode_property|device_property' --include=*.c \
     drivers/iio/*.c | grep -oE '"[a-z#][a-z0-9#,-]*"' | sort -u    # → 6 个
```

**这条的推翻方式值得单记**：不是重读代码发现的，是**填表时空格子填不上**发现的（skill 规则 17 的 §A22）。流水的正文可以顺过去，"写在哪个节点"这一列填不上就必须回去查。

**落地**：`06-devicetree.md` §0.3 把"`-A1` 不能省"写成了显式警告，并指出这类"属性名和读它的函数不在同一行"是本篇所有普查命令的共同风险。

#### E · 时间戳对齐的两种写法：把 91 当成一类

**当时怎么想的**：写 `05-usage.md` 的扫描缓冲区陷阱时想给出"两种对齐写法各占多少"，随手数了 `__aligned(8)`：

```bash
grep -rn '__aligned(8)' --include=*.c --include=*.h drivers/iio | wc -l    # → 91
```

准备写成"`__aligned(8)` 91 处、`aligned_s64` 9 处"。

**什么动作推翻了它**：skill 规则 30 的"子串包含会把两类算成一类"。91 里混着**两件不同的事**：

```bash
grep -rn 's64 .*__aligned(8)'  --include=*.c --include=*.h drivers/iio | wc -l   # → 55
grep -rn '__aligned(8)' --include=*.c --include=*.h drivers/iio | grep -v 's64' | wc -l   # → 33
```

33 处是给**非 s64 成员**加的（`u8 data[24] __aligned(8)`、`__be16 data[20] __aligned(8)`），目的是让结构体整体对齐从而让追加在末尾的时间戳落在 8 字节边界上——和"给时间戳字段本身加对齐"是两个不同的手法。

**但 55 + 33 = 88，不等于 91——这个二分本身也不干净。** 按字段的**类型**而不是按 `s64` 这个词重新切一刀，才刚好切满：

```bash
# A：给 64 位整型字段本身加标注
grep -rnE '(s64|u64|int64_t|__s64|__u64) [a-z_0-9]+ __aligned\(8\)' \
  --include=*.c --include=*.h drivers/iio | wc -l                # → 67
# B：其余（给数组/小成员加，靠结构体整体对齐兜住）
grep -rn '__aligned(8)' --include=*.c --include=*.h drivers/iio \
  | grep -vE '(s64|u64|int64_t|__s64|__u64) [a-z_0-9]+ __aligned\(8\)' | wc -l   # → 24
```

**67 + 24 = 91，一行不多一行不少。** 之所以按 `s64` 切会漏，是因为时间戳字段的类型在树里根本不统一——`u64` 有 8 处（全是 `hid-sensor-*` 那一家）、`int64_t` 有 2 处（`inv_icm42600` 的两个），它们都被 `grep -v 's64'` 扫到"填充"那一类去了。

**字段名同样不统一，而且树里有个拼写错误。** 那 67 处的字段名分布是 `ts` 35 个、`timestamp` 31 个、`timetamp` 1 个：

```
drivers/iio/accel/stk8ba50.c:97      s64 timetamp __aligned(8);
```

（同一个拼写错误还出现在另一处注释里：`drivers/iio/adc/ad7923.c:63`。）**所以"按字段名 grep"这个口径注定会漏掉至少这一个。** 本文档集对外发布的 31 就是按字段名 `timestamp` 数的，它回答的是"有多少驱动把这个字段叫 `timestamp` 并加了标注"，不是"有多少驱动加了标注"——两个问题的答案分别是 31 和 67，**发布时必须说清问的是哪一个**：

```bash
grep -rn 'timestamp __aligned(8)' --include=*.c --include=*.h drivers/iio/ | wc -l   # → 31
grep -rnw 'aligned_s64' drivers/iio/ | wc -l                                          # → 9
```

**顺带一条版本事实**：`aligned_s64` 这个宏在 6.1.158 里**存在**（`include/linux/types.h:114` → `include/uapi/linux/types.h:52`），9 个驱动已经在用。它不是"更新内核才有的写法"。

**落地**：`05-usage.md` §7 陷阱 2 的两行表（31 / 9），以及紧跟的 `IIO_DMA_MINALIGN` 一段（100 处使用 / 95 个文件）。

#### F · "写错 `mount-matrix` 会静默用单位矩阵"——两头都说错了

**当时怎么想的**：`mount-matrix` 是典型的"配错了不报错"属性，初稿把它写成"失效完全静默"，并把这一条列进 `06-devicetree.md` 的静默失效清单。

**什么动作推翻了它**：把 `iio_read_mount_matrix()` 从头到尾读一遍（`drivers/iio/industrialio-core.c:627-646`）。它有**四条出口**，两个方向都和初稿相反：

- **写错项数（比如写了 8 个）→ 返回 `-EINVAL`**（`:636-638`），驱动 probe 直接失败。这是最容易发现的情形，不是最难的。
- **完全不写 → 回落到单位矩阵，但打了一条 `dev_info`**（`:644-645` → `:593`，字符串 `mounting matrix not found: using identity...`）。所以"完全静默"是错的。

也就是说：**写错个数比不写更容易发现**，而不写这条路径也不是全无声响，只是那条打印长得像一条正常信息。回落还有 kerneldoc 背书（`:622-623` 写着会填单位矩阵），它是预期行为而非漏掉的错误处理。

**落地**：`06-devicetree.md` §3.4 整节重写，标题从"失效完全静默"改成"不写会回落，而且有一条打印"，正文加了四出口决策树 + 四行出口表；§6.4 的错误消息表把这条标成"唯一一条 `dev_info` 等级，而且不是错误"。

#### G · `.debugfs_reg_access` 在 adc 目录：15 → 16

**当时怎么想的**：按目录统计 `.debugfs_reg_access` 的分布时，用 `grep -rln`（文件数）填了表，adc 一行填 15。

**什么动作推翻了它**：表头写的是"赋值点数"而不是"文件数"，合计对不上（各行相加 50，但赋值点总数 51）。差的一个是：

```bash
grep -c '\.debugfs_reg_access' drivers/iio/adc/ina2xx-adc.c     # → 2
```

`drivers/iio/adc/ina2xx-adc.c` 一个文件里有两个 `iio_info` 实例（两种芯片各一套），各自填了这个回调。

**落地**：`07-debug.md` §3.3 的分布表按赋值点计数（adc 16），并在表下显式写"文件数少一个是因为 `ina2xx-adc.c` 一个文件里有两个赋值点"。**"文件数"和"赋值点数"必须在表头就说清是哪个**——这条后来又以另一种形式复发了，见 P。

#### H · `-EBUSY`：23 个 grep 命中 ≠ 23 个产生点

**当时怎么想的**：`07-debug.md` §7 的错误码普查表是用 `grep -o -- "-EBUSY\b" | wc -l` 数出来的，得到 23。接着要按语义分组展开，下意识地想凑够 23 处。

**什么动作推翻了它**：分组时逐个打开行号，发现命中里混着三类**不产生错误码**的行：

- kerneldoc 注释里提到错误码（`drivers/iio/industrialio-core.c:1766`、`:2068`、`:2106`）
- 把错误码当条件比较（`:1251` 的 `if ((ret == -EBUSY) && …)`）
- `int ret = -ENODEV` 这类初始化（`:1835`）

去掉之后实际产生点是 **16 个**，分成五组语义。九个核心文件的口径可以重跑：

```bash
C="drivers/iio/industrialio-core.c drivers/iio/industrialio-buffer.c \
drivers/iio/industrialio-trigger.c drivers/iio/industrialio-event.c \
drivers/iio/inkern.c drivers/iio/industrialio-sw-device.c \
drivers/iio/industrialio-sw-trigger.c drivers/iio/industrialio-configfs.c \
drivers/iio/industrialio-triggered-event.c"
grep -o -- "-EBUSY\b" $C | wc -l      # → 23（命中）
```

**落地**：`07-debug.md` §7.3 在分组表之前先写"「grep 命中」不等于「产生这个错误码的地方」"，并把三类非产生点各点了名。同一段还澄清了两个方向相反的 claim 函数——`iio_device_claim_direct_mode()` 是"缓冲区开着就 `-EBUSY`"，`iio_device_claim_buffer_mode()` 是"缓冲区没开才 `-EBUSY`"。

#### I · `iio_chan_type_name_spec[]` 的项数：凭空的 85 → 实测 35

**当时怎么想的**：写三张名字表的规模时，`iio_chan_type_name_spec[]` 一格填了 **85**，第二格（重跑命令）留了个 `—`。

**什么动作推翻了它**：skill 规则 19 说表格里的 `—` 是欠债记号必须清掉。回去补命令，一跑就露了：

```bash
awk '/^static const char \* const iio_chan_type_name_spec\[\]/,/^};/' \
    drivers/iio/industrialio-core.c | grep -c '\[IIO_'          # → 35
```

**35，不是 85。** 85 这个数字没有任何来源——它不是别的表的数字、也不是别的版本的数字，是纯粹编出来的。这是本文档集里最严重的一条数字缺陷，而且**三个自检脚本一个都抓不到它**（脚本只查引用和行号，不查数字）。

**这条直接催生了 skill 规则 30 的最后半句**：凡是同时给了数字和复现命令的地方，定稿前必须真跑一遍逐格对账。本轮按这条对账，另外抓出 6 个错数（见 §6.3 的统计）。

**落地**：`07-debug.md` §7 与 `00-overview.md` §2.6 的 35 / 50 / 29 三个数字，每个都带自己的重跑命令。

#### J · ABI 文档数：54 → 52

**当时怎么想的**：台账里继承下来的数字是"54 个（1 个通用 + 53 个专属）"。

**什么动作推翻了它**：本轮逐格对账时跑了一遍：

```bash
ls Documentation/ABI/testing/sysfs-bus-iio* | wc -l    # → 52
ls Documentation/ABI/stable/ | grep -ci iio            # → 0
```

52，其中 1 个是通用的 `sysfs-bus-iio`、51 个是芯片专属的。另外 `configfs-iio` 是单独一个文件，不在这 52 里——**初稿把它算进去过一次，这就是 54 里多出来的另一个 1**。

`stable/` 下 0 个这条本身也是一条论断（IIO 的 ABI 全部停在 `testing/`），同样要跑命令确认，不能靠"我记得 IIO 的 ABI 没稳定"。

**落地**：`00-overview.md` §2.4 的四条重跑命令；本篇 §5.4 的版本时效表。

#### K · "Kconfig 选项 = 架构门"——同一个过读纠正了三次

**当时怎么想的**：arm64-only 的裁剪要求里有一条"按 arm64 过滤统计"。执行时反复把 `CONFIG_IIO_BUFFER`、`CONFIG_IIO_TRIGGER`、`CONFIG_IIO_CONSUMERS_PER_TRIGGER` 之类的 Kconfig 选项当成"可能在 arm64 上不成立的东西"，写出过"arm64 上是否启用取决于配置"这种含糊话。

**什么动作推翻了它**：直接查核心 Kconfig 里有没有架构依赖：

```bash
grep -cE 'depends on.*(ARM|X86|POWERPC|ARCH_)' drivers/iio/Kconfig     # → 0
grep -rhE 'depends on.*(ARM|X86|POWERPC|ARCH_)' drivers/iio/*/Kconfig | wc -l   # → 26
```

**核心的 7 个选项一条架构依赖都没有**；26 条架构依赖全部在具体驱动的子 Kconfig 里（`drivers/iio/adc/Kconfig` 占 23 条，都是 SoC 专属 ADC）。所以"IIO 核心机制在 arm64 上是否存在"这个问题**没有架构维度**——它只有"发行版配置里开没开"这个维度，而这与架构无关。

这条纠正了三次（`00-overview.md` §2.5、`04-extensions.md` 篇首分诊表、`07-debug.md` §2 各一次）说明它不是记性问题，而是**"arm64-only"这个约束容易被过度应用**：裁剪的对象是 `arch/` 下的代码路径和 ACPI 发现路径，不是 Kconfig 选项。

**落地**：`04-extensions.md` 篇首的"哪几个在 arm64 上真会碰到"分诊表明确写成"和架构无关，取决于配置"。

本篇 §2.7 的裁剪记录里也写了这一条。

#### L · `_once` 变体普查：子串把宏名和函数名混成一类

**当时怎么想的**：skill 规则 22 要求日志可见性等级细化到 `_once` / `_ratelimited` 变体。普查时搜 `_once`，在 IIO 里搜到了命中，准备写"核心里有 `_once` 类打印"。

**什么动作推翻了它**：打开命中看它到底是什么。命中的是 `am335x_tsc_se_set_once()`——一个 **MFD 层的函数名**，和打印毫无关系：

```
drivers/mfd/ti_am335x_tscadc.c:72   void am335x_tsc_se_set_once(...)
drivers/iio/adc/ti_am335x_adc.c:477 am335x_tsc_se_set_once(adc_dev->mfd_tscadc, step_en);
```

改成搜宏调用形式，核心九个文件的结果是 **0**：

```bash
grep -rn '_once(' --include=*.c drivers/iio/*.c drivers/iio/buffer/*.c | wc -l   # → 0
```

**IIO 核心一个 `_once` / `_ratelimited` 打印都没有。** 这是一条有内容的否定结论：核心的每条打印都是每次都打，没有"第一次才打"的陷阱——读者在 dmesg 里看到几条就是发生了几次。

**落地**：`07-debug.md` §6 的日志可见性表把"无 `_once` / `_ratelimited` 变体"写成显式一行，而不是留白。

#### M · 两套 fops 定义在哪个文件：串味到了 03

**当时怎么想的**：`iio_buffer_fileops` 和 `iio_event_fileops` 这两个名字里一个带 buffer 一个带 event，自然地把它们分别归给了 `drivers/iio/industrialio-buffer.c` 和 `drivers/iio/industrialio-event.c`。

**什么动作推翻了它**：写注册尾段时要给"fops 二选一"这个动作定位到行，一 grep 就发现两套 fops **都定义在 `industrialio-core.c`**：

```
$ grep -rn 'struct file_operations iio_.*fileops' --include=*.c drivers/iio/
drivers/iio/industrialio-event.c:184:static const struct file_operations iio_event_chrdev_fileops = {
drivers/iio/industrialio-buffer.c:1516:static const struct file_operations iio_buffer_chrdev_fileops = {
drivers/iio/industrialio-core.c:1862:static const struct file_operations iio_buffer_fileops = {
drivers/iio/industrialio-core.c:1874:static const struct file_operations iio_event_fileops = {
```

**IIO 一共有四套 fops，而不是两套**，这正是串味的根源：

- **主字符设备**的两套（`iio_buffer_fileops` / `iio_event_fileops`）都在 `industrialio-core.c`，二选一发生在 `:1983-1986`——有缓冲区就用前者，否则用后者；`devt` 只在 `:1988-1991` 的条件成立时才设。
- **per-fd 的两套**（`iio_buffer_chrdev_fileops` / `iio_event_chrdev_fileops`）确实住在 buffer / event 两个文件里，由 `IIO_BUFFER_GET_FD_IOCTL` / `IIO_GET_EVENT_FD_IOCTL` 用 `anon_inode_getfd()` 挂上（`drivers/iio/industrialio-buffer.c:1557`、`drivers/iio/industrialio-event.c:212`）。

所以"buffer 的 fops 在 buffer.c 里"这句话**一半是对的**——对 per-fd 那套成立，对主设备那套不成立。半对的论断比全错的更难发现。

**这条的危害是它已经串进了别篇**：`03-internals.md` §0 的分层图里，"接口层"三行原本把 fops 装配写在了 buffer 一行上。按 skill 规则 17 grep 全集才改干净。

**落地**：`03-internals.md` §0 分层图第 1 层第三行改为"`industrialio-core.c` fileops 装配 + 通道属性合成 + ioctl 分发"；`07-debug.md` §5 的字符设备节按核心定位。

#### N · kerneldoc 注释块的起始行冒充结构定义行

**当时怎么想的**：写 `00-overview.md` §4 的数据结构速览时，从台账里抄了 `struct iio_dev` 在 `include/linux/iio/iio.h:550`、`struct iio_info` 在 `:417`。

**什么动作推翻了它**：`00-overview.md` 是最后写的，写完按规则 17 和 `02-datastructures.md` 对账，发现两篇的行号不一样。回源码：

```bash
grep -n '^struct iio_dev {\|^struct iio_info {' include/linux/iio/iio.h
# 445:struct iio_info {
# 571:struct iio_dev {
```

`:417` 和 `:550` 是**这两个结构体上方那一大段 kerneldoc 注释块的起始行**。IIO 的头文件里每个大结构体都带几十行 kerneldoc，两个行号差了 20~30 行，肉眼扫过去完全像是对的。

**这条是 `check_line_refs.py` 抓不到的典型**：`:550` 在文件范围内，指向的也确实是 `iio_dev` 的注释——引用有效但指错对象（skill 规则 17 的最后一句）。只有跨篇对账能抓。

**落地**：`00-overview.md` §3 / §4 改为 `:571-596` 和 `:445-518`，与 `02-datastructures.md:253` / `:367` 对齐。同批还改了 `iio_dev_opaque` 的归属文件（初稿写 `drivers/iio/iio_core.h`，实际是 `include/linux/iio/iio-opaque.h:41-77`）和 `iio_event_getfd()` 的行（初稿写 `:206`，那是函数内部的一个 `-EBUSY` 返回，函数本身在 `drivers/iio/industrialio-event.c:192`）。

#### O · `setup_ops` 能不能传 NULL：官方文档说不行，代码说行

**当时怎么想的**：`Documentation/driver-api/iio/triggered-buffers.rst` 是一篇 69 行的官方文档，第 52-65 行列了三点注意事项，其中写了 `setup_ops` 该怎么填。初稿跟着它写。

**什么动作推翻了它**：`check_doc_coverage.sh` 报这篇官方文档在本文档集里 0 引用，回去补引用时逐行核对，撞上了冲突。`Documentation/driver-api/iio/triggered-buffers.rst:54-55` 的说法与代码不一致——代码里有一条**兜底**路径：

```bash
grep -n 'noop_ring_setup_ops' drivers/iio/industrialio-core.c
# 1924:static const struct iio_buffer_setup_ops noop_ring_setup_ops;
# 1981:		indio_dev->setup_ops = &noop_ring_setup_ops;
```

`:1924` 是一个**没有初始化式**的 `static const` 定义——四个回调全是 NULL。`:1979-1981` 在 `(modes & INDIO_ALL_BUFFER_MODES)` 且 `setup_ops == NULL` 时把它填进去。而 `drivers/iio/industrialio-buffer.c` 里对这四个回调的解引用**全部带 NULL 判断**（`:432`、`:1074`、`:1114`、`:1136`、`:1163`、`:1180` 六处）。

结论：**`setup_ops` 可以传 NULL，行为是"四个回调一个都不调"**，不会崩。以代码为准（skill 阶段 2 步骤 6 的三等证据风险条）。

**落地**：`04-extensions.md` §2 开篇加了一整段，写明这处文档与代码的冲突、`drivers/iio/buffer/industrialio-triggered-buffer.c:78` 的无条件赋值、以及六个受保护的解引用点。本篇 §4.3 也登记了这条。

#### P · `debugfs_reg_access` 的文件数：53 → 50（模式漏了前导点）

**当时怎么想的**：`00-overview.md` §2.4 要写"多少个文件实现了 `direct_reg_access`"，用了：

```bash
grep -rln 'debugfs_reg_access' --include=*.c drivers/iio | wc -l    # → 53
```

**什么动作推翻了它**：本轮逐格对账时把它和 `07-debug.md` 已发布的"51 个赋值点 / 50 个文件"对上，两个数字打架。差的三个文件是：

```
drivers/iio/industrialio-core.c              ← 调用它（debugfs 的 read/write 实现）
drivers/iio/common/st_sensors/st_sensors_core.c  ← 库层转发
drivers/iio/imu/adis.c                       ← 库层转发
```

它们**消费**这个字段，不**实现**它。带前导点的模式才是"实现者"的口径：

```bash
grep -rln '\.debugfs_reg_access' --include=*.c drivers/iio/ | wc -l   # → 50
grep -rn  '\.debugfs_reg_access' --include=*.c drivers/iio/ | wc -l   # → 51
```

**这条和 G 是同一个机理的两面**：G 是"文件数当成赋值点数"，P 是"消费者算进实现者"。两次都是**同一个字段**的统计，两次都是口径没说清。所以本篇 §8 的每一行都强制写明"数的是文件还是行"。

**落地**：`00-overview.md` §2.4 改为"50 个文件（51 个赋值点）"；`07-debug.md` 的四处（§1 结论、§2 概览表、§3.3 分布表、§8 速查）本来就是 50/51，无需改动——**这次是新写的那篇错、老的那篇对**，如果按"新的覆盖老的"处理就反而改坏了。

#### Q · A 条的机理跑到了第二张表上：02 §1.1 五行数字同时缩水

**当时怎么想的**：A 条（`iio_trigger_ops` 13 → 53）的落地写的是"改 `04-extensions.md` §3 与 §4.0"。改完就当这件事结了——毕竟那才是讲触发器的篇。

**什么动作推翻了它**：写本篇 §8 的能力普查行时要把每个 ops 结构的实现数重跑一遍，顺手 grep 了全套文档里还有哪儿在发布这些数字，撞见 `02-datastructures.md` §1.1「本子系统有几层可插拔」那张表——它的重跑命令用的是：

```
struct $s [a-zA-Z_]* = {
```

`[a-zA-Z_]` 加了大写字母，看起来比 A 条里的 `[a-z_]` 更宽，**但一样不含数字**。于是这张表的五行数字全是缩水后的值。改用带 `0-9` 的字符类重跑：

| ops 结构 | 表里发布的 | 实测赋值点 | 实测文件 |
|---|---|---|---|
| `iio_info` | 64 | 421 | 387 |
| `iio_buffer_access_funcs` | 4 | 4 | 4 |
| `iio_trigger_ops` | 13 | 53 | 52 |
| `iio_buffer_setup_ops` | 19 | 63 | 63 |
| `iio_sw_trigger_ops` / `iio_sw_device_ops` | 2 / 1 | 2 / 1 | 2 / 1 |

`iio_info` 那一行错得最狠：64 → 421，差了六倍多。而 `iio_buffer_access_funcs` 和软件对象层三行**恰好没错**，因为那几个实现的变量名里本来就没有数字（`kfifo_access_funcs`、`iio_cb_access` 之类）。**三行对、两行错**，正是 M 条说的那种"半对"状态。

**这条揭示的是 A 条的落地做窄了**。A 条只改了"讲触发器的那一篇"，而同一个数字被另一篇当成横向对照表的一格发布着。skill 规则 30 的"改了一个计数必须回头改所有引用它的汇总表"给了判据——`grep -rn '<旧数字>' output/iio/` 为空。当时没跑这条 grep，因为旧数字是 `13`，直觉觉得"13 太常见，grep 出来全是噪声"。**这个直觉本身就是漏洞**：数字越常见越要 grep，因为越可能撞上另一处发布点。

**落地**：`02-datastructures.md` §1.1 的表改成「赋值点 / 文件」两列并全部换成实测值，重跑命令的字符类补上 `0-9`，表下加一条 ⚠️ 说明少写这三个字符会怎样。本篇 §8 的能力普查表用同一口径。

### §6.2 缺陷统计：什么手段抓出了什么

十七条实例加上 §3 各篇的"自审修正记录"列，本文档集自审期间一共改掉 **45 处**。按"被什么手段抓出来"分类：

| 抓出它的手段 | 处数 | 只有这个手段能抓的那一类 |
|---|---|---|
| **跑一遍自己发布的命令**（规则 30 末句） | 16 | 凭空数字（I 的 85）、口径不一致（G、P）、过窄/过宽的模式（E） |
| **填一张必须填满的表**（规则 17 的 §A22） | 9 | "我没去确认边界"的地方（D 的第六个属性、`—` 格子背后的空白） |
| `check_line_refs.py` 的越界与 AMBIGUOUS | 7 | 归错文件（类 e）、裸文件名歧义（`events.h` 有两个候选） |
| **跨篇对账**（规则 17） | 6 | 引用有效但指错对象（N 的 kerneldoc 起始行） |
| **把整个函数读完**而不是读片段 | 4 | 方向相反的结论（F 的四出口、C 的锁） |
| `check_doc_coverage.sh` 的 0 引用报告 | 2 | 官方文档与代码冲突（O） |
| `check_xrefs.py` | 1 | 重排后失效的章节号 |
| **重读正文** | 0 | 没有一处 |

**最后一行是这张表的重点：重读正文一处都没抓到。** 45 处全部来自"回到源码跑一条命令"或"被一张表逼着填格子"。这与 skill 规则 17 的判断一致——**流水的正文可以顺过去，空格子不能**。

### §6.2.1 六类机械性损坏

上表的手段是"怎么抓"，这一小节是"怎么产生的"。六类都与理解无关、纯粹是操作过程中的损坏，因此**它们会在任何一个子系统上重现**：

**(a) 在 `sed` 窗口上做算术。** 用 `sed -n '639,690p'` 打开一段代码，然后数着屏幕上的行数去推某一行的绝对行号。窗口起始行是 639 意味着屏幕第 1 行是 639，很容易当成 640，于是整段偏 1；跨过空行或注释块再数一次就偏 2。`08-feynman.md` 的换算公式一节有五处这样偏出来的行号。**改法**：需要绝对行号时用 `grep -n` 或 `awk 'NR>=A && NR<=B'`（`awk` 会保留 `NR`，`sed` 不会），永远不要在窗口上做加法。

**(b) heredoc 吞掉反斜杠。** 用 `cat >> doc.md << EOF` 追加正文时，未加引号的定界符会让 shell 解释 `\`、`$`、反引号。后果有两种：正则里的 `\.` 变成 `.`（模式变宽，数字变大——P 就是这个机理的近亲）、以及 ASCII 图里的 `└─` 前一行被替换掉。**改法**：定界符一律加引号（`<< 'EOF'`），需要变量时改用 python 写文件。本文档集有一处 ASCII 图因此重复了一行，是在通读时肉眼发现的——**这类损坏三个脚本都抓不到**。

**(b') 追加命令的目标路径写成了相对路径。** 与 (b) 同源的一条：`cat >> 99-evidence.md` 依赖 shell 当前目录，而中途为了跑源码 grep 切过 `cd`，于是**整整一节被追加到了源码树根目录下的一个新文件里**。本轮真的发生了一次，那个误建的文件已删除、`git status` 已确认树恢复干净。**改法**：所有写文档的命令一律用绝对路径，不依赖 cwd。这条对"源码树只读"的约束尤其要紧——写错方向就是往只读树里写东西。

**(c) 把非产生点算成产生点。** `grep -c` 数的是"含这个串的行数"，而含 `-EBUSY` 的行里有注释、有 `if (ret == -EBUSY)` 比较、有 `int ret = -ENODEV` 初始化。同理 `#endif` 会被算进"多少个分支"，kerneldoc 里的函数名会被算进"多少个调用者"。**改法**：凡是要发布"多少个 X"，先随机打开三个命中确认它们真的是 X；口径写成"文件数"或"含该串的行数"这种能重跑且不需要解释的形式（见 H、G、P）。

**(d) 子串把两类混成一类。** `debugfs_reg_access` 命中里混着实现者和消费者（P）；`__aligned(8)` 命中里混着时间戳字段和数组填充（E）；`_once` 命中里混着打印宏和一个 MFD 函数名（L）。**改法**：模式带上足以区分类别的语法上下文——赋值点带前导点 `\.field`，宏调用带括号 `_once(`，字段声明带类型 `s64 .*__aligned`。规则 30 的 `grep -w` 是这一条的最小版本。

**(e) 模板与邻居文件串味。** 把符号归给"名字看起来对得上"的那个文件：`iio_event_fileops` 归给 `industrialio-event.c`（它只有 614 行，行号一越界就被脚本抓住了）、`iio_dev_opaque` 归给 `drivers/iio/iio_core.h`（因为那也是个"核心内部头文件"）。M 那条更隐蔽——**四套 fops 里有两套确实住在那两个文件里**，所以"buffer 的 fops 在 buffer.c"半对，行号还落在范围内，脚本沉默。**改法**：每个符号的归属都用 `grep -rn '符号名' --include=*.c --include=*.h <目录>` 现场确认，不要从名字推断；跨篇对账是唯一能兜住"半对"的网。

**这六类里 (a)(b)(b') 是编者自己造成的，(c)(d) 是工具语义造成的，(e) 是命名相似性造成的。** 三类的防御手段不同，混在一起谈会得出"再仔细一点"这种没用的结论。

### §6.3 有没有第二套独立产出可比对

**没有。** skill 规则 29 给的是"先写完自己的、再读对方"的比对协议，前提是同一子系统存在另一套独立写成的文档。本轮 IIO **不具备这个条件**：`output/` 下没有第二套 IIO 文档，本文档集也没有参照任何第三方的 IIO 讲解材料。

因此规则 29 的两类落地（吸收 / 明确不吸收）本轮都是空的。**这不是"比对通过"，是"没有比对"**——它意味着本文档集缺少一道最有效的检查：另一个人对同一段代码的独立理解。§6.2 那张表里"重读正文 0 处"说的正是这件事的另一面——**编者自己抓不到的那类缺陷，只有独立的第二双眼睛能抓**。

替代手段有一条，效力低一档但不是零：**官方 `Documentation/` 是一份独立产出**。它是另一批人对同一段代码的独立描述，因此本文档集把"官方文档与代码冲突"当成一等重要的发现来处理——O 那条（`setup_ops` 能不能传 NULL）就是这么来的，登记在 §2.3.1 和 §4.3。

---

## §7 如何复核本文档集

### §7.1 三个批量脚本

三个脚本都是纯 stdlib、无外部依赖，在仓库根目录下跑：

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/iio

python3 $S/check_xrefs.py     $D          # 跨篇 0X §Y 引用是否命中
python3 $S/check_line_refs.py $D .        # 路径:行号 是否越界 / 能否解析
bash    $S/check_doc_coverage.sh iio $D . # 官方文档反向覆盖率
```

**定稿时的三条结果**（描述而非照抄——脚本下一轮会把这段引述再解析一次，照抄失败行的原生格式就等于制造失败）：

| 脚本 | 结果 | 需要注意的地方 |
|---|---|---|
| `check_xrefs.py` | 10 篇文档、跨篇引用命中 216 条，失效 0 条，忽略项 0 条 | 无 `.xref-ignore` 文件——**本文档集一条假阳性都没有登记**，全部靠消除触发条件解决，见 §7.2 |
| `check_line_refs.py` | 629 条唯一「路径:行号」引用全部解析成功，无越界、无 AMBIGUOUS、忽略项 0 条 | 消歧表覆盖 98 个基名 / 45 个前缀目录。AMBIGUOUS 归零是靠**把裸文件名补成全路径**做到的，不是靠忽略 |
| `check_doc_coverage.sh` | 「必查」档共 9 篇，0 篇零引用 | 「共 9 篇」这个数字本身要看一眼：如果它报「共 0 篇」，那是**假通过**，意味着脚本一篇官方文档都没匹配到 |

**三个脚本都抓不到的两类缺陷**，复核时必须靠人：

1. **行号在范围内但指向无关代码。** 本轮的 N（kerneldoc 注释块的起始行冒充结构定义行）就是这一类，只有跨篇对账抓住了它。
2. **章节号存在但不讲那件事。** 引用 `0X §Y` 时 §Y 确实存在、内容却是别的。这一类只能逐条打开看。

### §7.2 为什么两个忽略文件都不存在

skill §B 允许把假阳性登记进 `.xref-ignore` / `.lineref-ignore`。**本文档集两个文件都没有创建**，因为每一次假阳性都用"消除触发条件"解决掉了。三种触发形态和处置：

| 触发形态 | 本轮的实例 | 处置 |
|---|---|---|
| 同一行里既有别篇文件名、又有本篇章节号 | §3 的取证表里，一个单元格先写「声明在 `02-datastructures.md` 第 618 行」，紧接着写「判定过程见本篇某小节」并给出该小节的编号——脚本按「同一行里最近的文档名」归属，于是拿本篇的小节号去 02 里找，报失效 | **把两件事拆进相邻的两个单元格**，本篇的小节号不与别篇文件名同行 |
| 段落里先提别篇、紧接着提本篇章节 | §6.1 的 K 条落地行，先写 `04-extensions.md` 的分诊表，随后写「本篇裁剪记录那一节」并给出编号 | **拆成两个段落**，让本篇的章节号独占一行、上一行不含别篇文件名 |
| 举例引用一条失效引用 | §3.9 要说明"初稿写了裸文件名加行号"这个错误，初稿直接把裸形式写进了表格，被行号检查器当成真引用去解析 | **改成散文描述**缺的是哪个前缀，不写出裸形式 |

第三种是 skill 里被点名犯过四遍的那一种（规则 27 的 ★★ 条）。它的机理是**自指**：文档里出现的每一条形似引用的文本，都会被下一轮脚本当成真引用。所以凡是要"举例说明一条坏引用长什么样"，唯一安全的写法是用散文描述而不是给出样本。

同一条自指规则还有第三种形态：`grep -c` 的原生输出格式是 `文件名:数字`，行号检查器会把它当成"该文件的第 N 行"。本文档集所有统计结果一律写成 `文件名 → 数字`（见 §8）。

### §7.3 复核一条论断的最短路径

如果只想核一条具体论断，不必读完本篇：

1. **论断带行号** → 直接 `awk 'NR>=A && NR<=B' <文件>` 打开看。不要用 `sed -n` 然后数屏幕行（§6.2.1 类 a）。
2. **论断带数字** → 文档里紧跟着的那条命令就是口径，直接重跑。数字对不上时先看口径：数的是文件数还是命中行数（类 c、类 d）。
3. **论断是否定性的**（"树里没有 X"、"全树唯一"） → 按 §6.1 的 A 条办：grep 必须跨出 `drivers/iio/`，因为消费者不住在框架自己的目录下。
4. **论断关于某个函数的行为** → 把整个函数读完，不要读片段。本轮四条方向相反的错误（C、F 及 §3 里另两条）全部源于只读了片段。
5. **论断在 §2.2 的"只看符号表"一列里** → 它的可信度等同于推断，按 §5.2 的风险表处理。

---

## §8 规模统计

本节把全文档集发布过的每一个数字集中登记一遍。**每一行都有两样东西：一条能原样粘贴重跑的命令，和一句说明"数的是什么"**——因为本轮十七条交叉验证里有六条（B、E、G、H、P、Q）的根因都是口径没写清，不是命令写错。

三条通用约定：

- **口径列里的"文件"指含该模式的文件数（`grep -rl`），"行"指含该模式的行数（`grep -rn`），"赋值点"指 `struct X 名字 = {` 这一形式的出现次数。** 三者互不相等，混用就是 §6.2.1 类 c。
- **统计范围里凡是写"核心九个文件"的，都指下面这个显式列表**，不用 `drivers/iio/*.c` 通配——通配会把 `industrialio-triggered-buffer.c` 之类算进来（skill 规则 30 第一条）。
- **所有命令的工作目录是内核树根**（`/workspace/safety/linux-6.1.y`），且**这些数字全部在 6.1.158 / `f6e38ae624cf` 上重跑过一遍**，不是从初稿抄来的。

核心九个文件的列表，后面多处引用：

```bash
C="drivers/iio/industrialio-core.c drivers/iio/industrialio-buffer.c \
drivers/iio/industrialio-trigger.c drivers/iio/industrialio-event.c \
drivers/iio/inkern.c drivers/iio/industrialio-sw-device.c \
drivers/iio/industrialio-sw-trigger.c drivers/iio/industrialio-configfs.c \
drivers/iio/industrialio-triggered-event.c"
```

### §8.1 树规模

| 量 | 值 | 口径 | 重跑 |
|---|---|---|---|
| `drivers/iio/` 下 `.c` 文件 | 532 | 文件 | `find drivers/iio -name '*.c' \| wc -l` |
| `drivers/iio/` 下 `.c` + `.h` 总行 | 283154 | 行 | `find drivers/iio -name '*.[ch]' \| xargs wc -l \| tail -1` |
| 核心九个 `.c` 总行 | 7027 | 行 | `wc -l drivers/iio/*.c \| tail -1`（顶层只有这九个 `.c`，所以通配在这一行是安全的） |
| `include/linux/iio/` 头文件总行 | 2522 | 行 | `wc -l include/linux/iio/*.h \| tail -1` |
| `include/uapi/linux/iio/` 总行 | 179 | 行 | `wc -l include/uapi/linux/iio/*.h \| tail -1`（`types.h` 126 + `events.h` 43 + `buffer.h` 10） |
| `drivers/iio/` 下的子目录 | 30 | 目录 | `ls -d drivers/iio/*/ \| wc -l`；27 个按器件类别分，3 个是子框架（`buffer` `trigger` `common`） |
| staging 里的 IIO 驱动目录 | 7 | 目录 | `ls -d drivers/staging/iio/*/ \| grep -v Documentation \| wc -l` |
| `tools/iio/` 下 `.c` 总行 | 2402 | 行 | `wc -l tools/iio/*.c \| tail -1` |

核心九个文件的逐个行数在 §2.1 与 `00-overview.md` §2.5 里，本节不重复——**同一张表出现两遍就是下一次数字打架的来源**（Q 条正是这么发生的）。

### §8.2 证据覆盖规模

| 量 | 值 | 口径 | 重跑 |
|---|---|---|---|
| 一等证据总行 | 2956 | 行 | §2.1 末尾那条 `wc -l` |
| 一等证据文件数 | 27 | 文件 | 数 §2.1 表的行数 |
| 二等证据文件数 | 21 | 文件 | 数 §2.2 表的行数。其中 6 个是核心文件与 `iio.h`（详读区间登记在表里），15 个是驱动/工具/消费者侧的取样 |
| 一等证据占 `drivers/iio` + `include/linux/iio` 的比例 | 约 1.0% | 计算值 | `2956 / (283154 + 2522)`，**分母含全部 532 个驱动**，所以这个比例低是设计使然：驱动只讲契约（§2.2 的分界理由） |
| 一等证据占核心九个 `.c` + IIO 头文件的比例 | 约 31% | 计算值 | `2956 / (7027 + 2522)`。核心九个 `.c` 里 `industrialio-core.c` / `-buffer.c` / `-trigger.c` / `inkern.c` 四个大文件属二等（详读区间见 §2.2） |

**这两个比例必须一起读。** 只给前者会让人以为文档集只看了百分之一；只给后者会隐去"绝大多数驱动没读"这个事实。

### §8.3 能力普查：谁实现了什么

这一节的每一行都是"全树有多少个驱动做了 X"。**字符类里必须有 `0-9`**——传感器驱动的命名习惯是"芯片型号 + 后缀"，少写这三个字符会漏掉一整类（§6.1 A 与 Q）。

| 量 | 赋值点 | 文件 | 重跑 |
|---|---|---|---|
| `struct iio_info` 实现 | 421 | 387 | `grep -rnE --include=*.c 'struct iio_info [a-z_0-9]+ = \{' drivers/ \| wc -l`；文件数换 `-rl` |
| `struct iio_trigger_ops` 实现 | 53 | 52 | 同上，把结构名换掉。`drivers/iio/trigger/` 只占 4 个，分布见 `04-extensions.md` §4.0 |
| `struct iio_buffer_setup_ops` 实现 | 63 | 63 | 同上 |
| `struct iio_buffer_access_funcs` 实现 | 4 | 4 | 同上；四个全在 `drivers/iio/buffer/` |
| `iio_sw_trigger_ops` / `iio_sw_device_ops` 实现 | 2 / 1 | 2 / 1 | 同上 |
| `.debugfs_reg_access` 实现 | 51 | 50 | `grep -rn '\.debugfs_reg_access' --include=*.c drivers/iio/ \| wc -l`；**必须带前导点**，否则会把三个消费者算进来（§6.1 P） |
| 其中 `drivers/iio/adc/` 下的 | 16 | 15 | 同上，路径换成 `drivers/iio/adc/`。`ina2xx-adc.c` 一个文件两个赋值点（两种芯片变体） |
| `iio_device_register` / `devm_` 真实调用点 | 388 | 387 | `grep -rn 'iio_device_register(' --include=*.c . \| grep -v '^drivers/iio/industrialio' \| grep -v '"' \| wc -l`。两道过滤器的理由在 §6.1 B |
| 核心外的 IIO 消费者 | — | 34 | `grep -rl 'iio_channel_get\|devm_iio_channel_get\|iio_read_channel_' --include=*.c . \| grep -v '^drivers/iio/' \| wc -l`；散在 11 个目录（把上一条的 `wc -l` 换成 `xargs -n1 dirname \| sort -u \| wc -l`） |

一条口径警告：**上表第一行的 421 个赋值点对 388 个注册点**，差出来的 33 个不是错误——同一个驱动常为多个芯片变体各写一套 `iio_info`（`drivers/staging/iio/addac/adt7316.c:2114` 与 `:2119` 就是相邻的两套），但只注册一次。**"有几套 ops"和"注册了几个设备"是两个不同的问题**，谁也不能替谁回答。

### §8.4 对齐与 DMA

扫描缓冲区的对齐是驱动作者唯一会因为"少写一个标注"而拿到错数据的地方，所以这几个数字单列一节。**统计范围一律是 `drivers/iio/`**；带 `-w` 的行是为了避免子串命中（`aligned_s64` 会被 `IIO_ALIGNED_S64` 之类含住、`IIO_DMA_MINALIGN` 会被更长的宏名含住），按字段名或按类型数的两行不需要 `-w`，因为模式本身已经带了语法上下文。

| 写法 | 值 | 口径 | 重跑 |
|---|---|---|---|
| 字段名叫 `timestamp` 且带 `__aligned(8)` | 31 | 行；类型不限（其中 `s64` 21、`u64` 8、`int64_t` 2） | `grep -rn 'timestamp __aligned(8)' --include=*.c --include=*.h drivers/iio/ \| wc -l` |
| `aligned_s64`（宏，`include/linux/types.h:114`） | 9 | 行 | `grep -rnw 'aligned_s64' drivers/iio/ \| wc -l` |
| `IIO_DMA_MINALIGN`（`include/linux/iio/iio.h:723`） | 100 | 行 | `grep -rnw 'IIO_DMA_MINALIGN' --include=*.c --include=*.h drivers/iio/ \| wc -l` |
| 同上 | 95 | 文件 | 把 `-rnw` 换成 `-rlw` |
| `____cacheline_aligned` | 0 | 行 | `grep -rnw '____cacheline_aligned' drivers/iio/ \| wc -l`。**6.1.158 里这种写法在 IIO 已经绝迹**，看到老代码这么写要知道它不是现行做法 |

**上表第一行的 31 是"字段名叫 `timestamp`"的口径，不是"加了对齐标注"的口径。** 按类型数才是后者：

| 口径 | 值 | 重跑 |
|---|---|---|
| 给 64 位整型字段本身加 `__aligned(8)` | 67 | `grep -rnE '(s64\|u64\|int64_t\|__s64\|__u64) [a-z_0-9]+ __aligned\(8\)' --include=*.c --include=*.h drivers/iio \| wc -l` |
| 其余的 `__aligned(8)`（加在数组/小成员上，靠结构体整体对齐兜住） | 24 | 把上一条改成 `grep -rn '__aligned(8)' … \| grep -vE '<同一个模式>' \| wc -l` |

**67 + 24 = 91，正好是宽模式 `__aligned(8)` 的全部命中**，所以这个二分是穷尽的。67 里的字段名有三种：`ts` 35 个、`timestamp` 31 个，还有一个拼错的 `timetamp`（`drivers/iio/accel/stk8ba50.c:97`）——**这就是为什么"按字段名 grep"这个口径注定漏数**。推导过程在 §6.1 E。

### §8.5 观测面规模

这一节的数字全部服务于一个判断：**IIO 的观测面很窄**。`07-debug.md` 的结论建立在它们之上。

| 量 | 值 | 口径 | 重跑 |
|---|---|---|---|
| 核心九个文件的打印点 | 28 | 行 | `grep -c 'dev_err(' $C` 等逐宏计数后相加，分解表在 `07-debug.md` §7.1。构成：`dev_err` 10、`pr_err` 8、`dev_dbg` 6、WARN 族 3、`dev_info` 1 |
| 其中 `industrialio-core.c` 一个文件 | 13 | 行 | 同上，范围只留这一个文件。核心 2153 行里只有 13 个打印点 |
| 其中默认不输出的 | 6 | 行 | `grep -c 'dev_dbg(' drivers/iio/industrialio-buffer.c` → 6。**六个全在这一个文件里**，别处的核心文件一个 `dev_dbg` 都没有 |
| `_once` / `_ratelimited` 变体 | 0 | 行 | `grep -cE '_(once\|ratelimited)\(' $C` 逐文件相加为 0。唯一的 `_once` 字样命中是一个 MFD 函数名，不是打印宏（§6.1 L） |
| `TRACE_EVENT` 定义 | 5 | 行 | `grep -rn 'TRACE_EVENT(' --include=*.h drivers/iio \| wc -l`。**核心 0 个**，五个全在 `drivers/iio/imu/bno055/bno055_ser_trace.h` |
| debugfs 文件种类 | 1 | 种 | 只有 `direct_reg_access`，由 50 个驱动的回调支撑（§8.3） |
| `drivers/iio/Kconfig` 顶层选项 | 8 | 行 | `grep -cE '^(config\|menuconfig) ' drivers/iio/Kconfig`。**一个都不是调试开关**，全表在 `07-debug.md` §1.1 |
| 其中带 `default` 的 | 1 | 行 | `grep -c '^\sdefault' drivers/iio/Kconfig`；只有 `IIO_CONSUMERS_PER_TRIGGER` 的 `default "2"`。其余七个的缺省是 n，靠别人 `select` 拉起 |
| sysfs ABI 文档 | 52 | 文件 | `ls Documentation/ABI/testing/sysfs-bus-iio* \| wc -l`；另有 1 个 configfs 的 ABI 文档 |
| stable 档的 IIO ABI | 0 | 文件 | `ls Documentation/ABI/stable/ \| grep -c iio`。**整个 IIO 的 sysfs 接口全部登记在 testing 档**，没有一条进过 stable |
| 字符设备的 `file_operations` | 5 | 赋值点 | `grep -rn 'struct file_operations' --include=*.c drivers/iio/` 在核心里有 5 处：4 套服务字符设备（`industrialio-core.c:1862`、`:1874`、`industrialio-buffer.c:1516`、`industrialio-event.c:184`），1 套服务 debugfs（`industrialio-core.c:449`）。四套的配对关系见 §6.1 M |
| uapi ioctl 号 | 2 | 个 | `IIO_BUFFER_GET_FD_IOCTL`（`include/uapi/linux/iio/buffer.h:8`）与 `IIO_GET_EVENT_FD_IOCTL`（`include/uapi/linux/iio/events.h:27`） |

### §8.6 名字表与枚举的项数

这四个数字支撑 `03-internals.md` 的属性名合成一节。

它们同时是本篇 §6.1 第 I 条（凭空的 85）的落地处。**四个都是"数枚举项"，口径是在给定行区间里数 `[IIO_` 开头的初始化项**：

| 表 / 枚举 | 项数 | 位置 | 重跑 |
|---|---|---|---|
| `iio_chan_type_name_spec[]` | 35 | `drivers/iio/industrialio-core.c:54-90` | `awk 'NR>=54 && NR<=90' drivers/iio/industrialio-core.c \| grep -c '\[IIO_'` |
| `iio_modifier_names[]` | 50 | `:92-143` | 同上，区间换成 92,143 |
| `iio_chan_info_postfix[]` | 29 | `:146-178` | 同上，区间换成 146,178 |
| `enum iio_chan_type` | 35 | `include/uapi/linux/iio/types.h` | `awk '/^enum iio_chan_type/,/^};/' include/uapi/linux/iio/types.h \| grep -cE '^\s+IIO_'` |
| `enum iio_modifier` | 51 | 同上 | 同上，枚举名换掉 |
| `enum iio_chan_info_enum` | 29 | `include/linux/iio/types.h:39` 起 | `awk '/^enum iio_chan_info_enum/,/^};/' include/linux/iio/types.h \| grep -c 'IIO_CHAN_INFO_'` |

**两组数字必须对着看，这是本节存在的理由：**

- `iio_chan_type_name_spec[]` 35 项 = `enum iio_chan_type` 35 项，**一一对应，没有空洞**。
- `iio_modifier_names[]` 50 项 < `enum iio_modifier` 51 项，**差的那一个是 `IIO_NO_MOD`**——它是"没有修饰符"的哨兵值，名字表里当然不该给它一个字符串。所以这个 50 对 51 不是缺口，是设计。
- `iio_chan_info_postfix[]` 29 项 = `enum iio_chan_info_enum` 29 项，同样一一对应。

**"表项数 = 枚举项数"这件事必须逐个数过再说。** 只要有一处不等，属性名合成就会拼出空字符串或越界读——而这种 bug 在运行期表现为"sysfs 里少了一个属性"，没有任何打印。

### §8.7 一次性全量重跑

把本节全部数字重跑一遍，用于换基线后的快速核对。**工作目录必须是内核树根**：

```bash
cd /workspace/safety/linux-6.1.y
C="drivers/iio/industrialio-core.c drivers/iio/industrialio-buffer.c \
drivers/iio/industrialio-trigger.c drivers/iio/industrialio-event.c \
drivers/iio/inkern.c drivers/iio/industrialio-sw-device.c \
drivers/iio/industrialio-sw-trigger.c drivers/iio/industrialio-configfs.c \
drivers/iio/industrialio-triggered-event.c"

p() { printf '%-38s %s\n' "$1" "$2"; }
p '.c 文件'          "$(find drivers/iio -name '*.c' | wc -l)"
p '.c+.h 行'         "$(find drivers/iio -name '*.[ch]' | xargs cat | wc -l)"
p '核心九个 .c 行'   "$(cat $C | wc -l)"
p '子目录'           "$(ls -d drivers/iio/*/ | wc -l)"
p 'iio_info 赋值点'  "$(grep -rnE --include=*.c 'struct iio_info [a-z_0-9]+ = \{' drivers/ | wc -l)"
p 'trigger_ops 赋值点' "$(grep -rnE --include=*.c 'struct iio_trigger_ops [a-z_0-9]+ = \{' drivers/ | wc -l)"
p 'setup_ops 赋值点' "$(grep -rnE --include=*.c 'struct iio_buffer_setup_ops [a-z_0-9]+ = \{' drivers/ | wc -l)"
p 'debugfs 赋值点'   "$(grep -rn '\.debugfs_reg_access' --include=*.c drivers/iio/ | wc -l)"
p '注册调用点'       "$(grep -rn 'iio_device_register(' --include=*.c . | grep -v '^drivers/iio/industrialio' | grep -v '"' | wc -l)"
p '64 位时间戳字段'  "$(grep -rnE '(s64|u64|int64_t|__s64|__u64) [a-z_0-9]+ __aligned\(8\)' --include=*.c --include=*.h drivers/iio | wc -l)"
p 'IIO_DMA_MINALIGN' "$(grep -rnw 'IIO_DMA_MINALIGN' --include=*.c --include=*.h drivers/iio/ | wc -l)"
p 'TRACE_EVENT'      "$(grep -rn 'TRACE_EVENT(' --include=*.h drivers/iio | wc -l)"
p 'Kconfig 顶层选项' "$(grep -cE '^(config|menuconfig) ' drivers/iio/Kconfig)"
p 'sysfs ABI 文档'   "$(ls Documentation/ABI/testing/sysfs-bus-iio* | wc -l)"
```

**期望输出**（6.1.158 / `f6e38ae624cf`）：532 / 283154 / 7027 / 30 / 421 / 53 / 63 / 51 / 388 / 67 / 100 / 5 / 8 / 52。

**任何一行对不上，先看是不是换了基线**（`git log -1 --oneline` 与 §1.1 比对），再看是不是工作目录不对——本节所有相对路径都从树根算起。**对不上又不是这两个原因，就说明这一节从"取证"降级成了"回忆"，整份文档集的数字都要重跑**（skill 规则 27 第一条）。
