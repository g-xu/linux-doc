---
title: 扩展机制
---

# SPI 子框架

> **本篇的组织轴：一节一个独立框架，彼此互不调用。**
>
> 这六个框架都长在 `03-internals.md` 描述的核心之上，但**它们之间没有调用关系**
> ——`spi-mem` 不认识 `spidev`，`spi-mux` 不认识 `spi-bitbang`。所以本篇可以
> 任意跳读：你只会用到其中一两个。
>
> 每节固定四段：**它解决什么问题 / 挂在核心的哪一点 / 内部实现 / 用它要注意什么**。
> 「挂在核心的哪一点」一律用 `03-internals.md` 的章节号指出来，这样你能把
> 子框架的行为接回主时间线。

---

## §0 分诊：arm64 上你真会碰到哪几个

| 框架 | 文件 | 行数 | Kconfig | arm64 上常见吗 | 什么时候会碰到 |
|-----|------|-----|---------|-------------|-------------|
| **spi-mem** | `drivers/spi/spi-mem.c` | 919 | `SPI_MEM`（bool，`drivers/spi/Kconfig:51`） | **几乎一定** | 板子上有 SPI-NOR flash 或 SPI-NAND，也就是绝大多数 arm64 板 |
| **spidev** | `drivers/spi/spidev.c` | 897 | `SPI_SPIDEV`（`:1136`） | **很常见** | 用户态直接驱动一个 SPI 芯片（自研传感器、FPGA 加载、调试） |
| **spi-mux** | `drivers/spi/spi-mux.c` | 203 | `SPI_MUX`（`:1121`） | 少见 | 片选不够，用外部 mux 芯片扩展 |
| **spi-bitbang** | `drivers/spi/spi-bitbang.c` | 453 | `SPI_BITBANG`（`:202`） | 少见 | 用 GPIO 软模拟 SPI（`spi-gpio`），或写一个纯 PIO 控制器驱动 |
| **SPI slave/target** | `drivers/spi/spi.c` §CONFIG_SPI_SLAVE + 两个示例 handler | — | `SPI_SLAVE`（`:1172`） | ✗ **基本碰不到** | SoC 当从机被别的主机读——需要控制器硬件支持从机模式 |
| **spi-loopback-test** | `drivers/spi/spi-loopback-test.c` | 1111 | `SPI_LOOPBACK_TEST`（`:1144`） | 调试时 | 验证一个新写的控制器驱动 |

**`SPI_MEM` 是 bool 而不是 tristate**——它编进 `spi.ko`（或内核），
不是独立模块。所以 `lsmod` 里看不到它。**如果 SPI-NOR 探测不到，
先确认 `CONFIG_SPI_MEM=y`**，这是最容易漏的一环。

**一句话选择指南**：

```
你要访问 flash？                  → §1 spi-mem（其实是 mtd 层帮你用，你只配 DT）
你要在用户态读写一个 SPI 芯片？   → §2 spidev
你在写一个纯 PIO 的控制器驱动？   → §3 spi-bitbang
片选不够用？                      → §4 spi-mux
SoC 要当从机？                    → §5 slave（先查控制器支不支持）
刚写完控制器驱动想验证？          → §6 spi-loopback-test
```

---

## §1 spi-mem：SPI 存储操作扩展

### §1.1 它解决什么问题

SPI flash 的访问模式和普通 SPI 外设**结构上不同**：

```
普通 SPI 外设：一串字节进，一串字节出。核心不需要理解内容。

SPI flash：    每次访问都是固定的四段结构
               ┌──────┬──────────┬──────────┬────────────┐
               │ cmd  │  addr    │  dummy   │   data     │
               │ 1B   │ 0~4B     │ 0~N 周期 │  0~N B     │
               │ 1线  │ 1/2/4/8线│          │ 1/2/4/8线  │
               └──────┴──────────┴──────────┴────────────┘
                            ↑ 每段的线宽可以不同！
```

这带来两个问题，普通 `spi_message` 都解决不了：

1. **QSPI 控制器有硬件加速器**。它不是「把字节摇出去」，而是「你告诉我
   opcode/地址/线宽，我自己产生完整时序」。用 `spi_transfer` 链表表达这个
   意图会丢失结构信息——控制器只看到四段字节流，不知道哪段是地址。
2. **memory-mapped 直读**。很多 QSPI 控制器能把 flash 映射到 CPU 地址空间，
   `memcpy` 就能读。这完全绕过了 SPI 传输路径。

`spi-mem` 的做法是**引入一个更高层的抽象 `struct spi_mem_op`**
（`include/linux/spi/spi-mem.h:99`），把四段结构显式表达出来，然后：

- 控制器**有** `mem_ops` → 直接把 op 交给硬件加速器
- 控制器**没有** → 核心自己把 op 翻译成 3~4 个 `spi_transfer`，走普通路径

**这是本子系统里唯一的「双实现」抽象**：同一个 API，两条完全不同的执行路径。

### §1.2 挂在核心的哪一点

三个挂载点，都在 `03-internals.md` 的时间线上：

| 挂载点 | 位置 | 对应 03 章节 |
|-------|------|------------|
| `spi_mem_driver` 注册时包装成普通 `spi_driver` | `drivers/spi/spi-mem.c:898` | §A5（匹配与 probe） |
| **绕过消息泵**，直接在调用者线程里执行 | `drivers/spi/spi-mem.c:328-343` | §A7 的第四条路径（03 §0.1 那张表的第 3 行） |
| 回退时走 `spi_sync()` | `drivers/spi/spi-mem.c:408` | §A7.1（同步快路径） |

注意第二条：**`spi-mem` 的 `exec_op` 路径不经过消息泵，也不经过
`__spi_validate()`**。所以 03 §B4 讲的 transfer 拆分、§B3 讲的哑缓冲，
在这条路径上**都不发生**。这是理解「为什么 QSPI 的行为和普通 SPI 不一样」
的关键。

### §1.3 `struct spi_mem_op` 的四段

`include/linux/spi/spi-mem.h:99`。每段都有 `nbytes` + `buswidth`（+ `dtr`）：

| 段 | 字段 | 说明 |
|---|------|------|
| `cmd` | `nbytes`（1 或 2）/ `buswidth` / `opcode` / `dtr` | opcode。8D-8D-8D 模式下是 2 字节 |
| `addr` | `nbytes`（0~4）/ `buswidth` / `val` / `dtr` | 地址，`val` 是 `u64`，按大端拆开 |
| `dummy` | `nbytes` / `buswidth` / `dtr` | 空闲周期，给 flash 内部准备时间。高速读必需 |
| `data` | `nbytes` / `buswidth` / `dir` / `buf.in` 或 `buf.out` / `dtr` / `swap16` | 数据。`dir` 是 `SPI_MEM_DATA_IN` / `_OUT` / `_NO_DATA` |

**`dummy.nbytes` 的单位是「字节」，不是「时钟周期」**。8 个 dummy 周期在
单线模式下是 1 字节，在四线模式下是 4 个周期/字节——所以同样的 `nbytes` 在
不同线宽下代表的周期数不同。这是配 flash 时序最容易搞错的地方。

`spi_mem_check_op()`（`drivers/spi/spi-mem.c:199`）校验 `nbytes` 范围和
`buswidth` 合法性（必须是 1/2/4/8，见 `spi_mem_buswidth_is_valid()` `:191`）。

### §1.4 `spi_mem_exec_op()`：两条路径

`drivers/spi/spi-mem.c:312`。完整流程：

```
spi_mem_exec_op(mem, op)
  ├─ spi_mem_check_op(op)                          :322 一带  参数合法性
  ├─ spi_mem_internal_supports_op(mem, op)         :325 一带  控制器能不能做
  │     └─ 有 mem_ops->supports_op 就问它，否则 spi_mem_default_supports_op()  :164
  │
  ├─【路径 A：硬件加速】if (ctlr->mem_ops && !mem->spi->cs_gpiod)     :328
  │     ├─ spi_mem_access_start(mem)               :262
  │     │     ├─ spi_flush_queue(ctlr)             :270   先把队列排空
  │     │     ├─ pm_runtime_resume_and_get(ctlr->dev.parent)   :275
  │     │     ├─ mutex_lock(&ctlr->bus_lock_mutex) :283
  │     │     └─ mutex_lock(&ctlr->io_mutex)       :284
  │     ├─ ctlr->mem_ops->exec_op(mem, op)         :333   ← 驱动自己产生时序
  │     ├─ spi_mem_access_end(mem)                 :289   反序解锁 + pm_runtime_put :297
  │     └─ if (!ret || ret != -ENOTSUPP) return ret;      :342
  │           只有 -ENOTSUPP 才继续往下走
  │
  └─【路径 B：翻译成 spi_transfer】
        ├─ tmpbufsize = cmd.nbytes + addr.nbytes + dummy.nbytes
        ├─ tmpbuf = kzalloc(tmpbufsize, GFP_KERNEL | GFP_DMA)      :353
        ├─ xfers[0] = cmd                （tx_nbits = cmd.buswidth）
        ├─ xfers[1] = addr（大端拆开）    （tx_nbits = addr.buswidth）
        ├─ xfers[2] = dummy（填 0xff）    dummy_data = 1
        ├─ xfers[3] = data（in 或 out）
        ├─ spi_sync(mem->spi, &msg)                :408
        ├─ kfree(tmpbuf)
        └─ if (msg.actual_length != totalxferlen) return -EIO;     :415
```

**五个必须记住的点**：

1. **`!mem->spi->cs_gpiod` 是路径 A 的前提**（`:328`）。**用 GPIO 片选的
   设备强制走路径 B**——因为硬件加速器自己管片选，它不知道有个 GPIO 要拉。
   这条常被忽略：**在 DT 里给 QSPI 节点加 `cs-gpios` 会静默关掉硬件加速**，
   性能断崖式下降而没有任何日志。
2. **`spi_flush_queue()`（`:270`）的意义**：注释写得很明白——
   > "Flush the message queue before executing our SPI memory operation
   > to prevent preemption of regular SPI transfers."
   >
   `exec_op` 会直接持 `io_mutex` 独占总线，如果队列里还有普通 message
   等着，它们会被推迟很久。所以先排空。
3. **只有 `-ENOTSUPP` 触发回退**（`:342`）。注释说明了理由：
   > "Some controllers only optimize specific paths (typically the read path)
   > and expect the core to use the regular SPI interface in other cases."
   >
   所以驱动的 `exec_op` 遇到不认识的 op 必须返 `-ENOTSUPP`，返其他错误码
   就得不到软件回退。**这是写 QSPI 驱动最容易犯的错。**
4. **`xfers[4]` 是栈上数组**（`:316`），所以路径 B 最多 4 个 transfer——
   四段结构刚好装满。
5. **`tmpbuf` 用 `GFP_DMA` 是必需的**（`:353`），注释解释了：核心可能对这个
   message 做 DMA 映射，所以缓冲必须 DMA-able。栈上的缓冲不行。

### §1.5 direct mapping（memory-mapped 直读）

给能把 flash 映射进 CPU 地址空间的控制器用。四个 API：

| API | 位置 | 说明 |
|-----|------|------|
| `spi_mem_dirmap_create()` | `drivers/spi/spi-mem.c:535` | 建立一个映射描述符。控制器不支持时**不失败**，退化成 no-dirmap |
| `spi_mem_dirmap_destroy()` | `:583` | 销毁 |
| `spi_mem_dirmap_read()` | `:679` | 读 |
| `spi_mem_dirmap_write()` | `:725` | 写 |

**不支持时的降级是静默的**（`spi_mem_no_dirmap_read()` `:481` /
`_write()` `:501`）——它们内部就是拼一个 op 调 `spi_mem_exec_op()`。
所以 `dirmap_create` 成功**不代表真的用上了硬件映射**。
判据是 `desc->nodirmap`：

```c
/* drivers/spi/spi-mem.c:481 一带 */
static ssize_t spi_mem_no_dirmap_read(struct spi_mem_dirmap_desc *desc, ...)
```

`devm_` 版本也有：`devm_spi_mem_dirmap_create()` `:614` /
`devm_spi_mem_dirmap_destroy()` `:655`。

### §1.6 `spi_mem_poll_status()`：等 flash 忙完

`drivers/spi/spi-mem.c:795`。写/擦除之后要轮询状态寄存器等 flash 变空闲。
两条实现：

| 条件 | 行为 |
|-----|------|
| 控制器有 `mem_ops->poll_status` | 交给硬件（有些 QSPI 能自动轮询，不占 CPU） |
| 没有 | `spi_mem_read_status()` `:760` + `read_poll_timeout()` 软件轮询 |

这是 `spi-mem` 里唯一一个「性能差异体现在 CPU 占用而不是吞吐」的 API：
擦一个 64KB 块要几百毫秒，软件轮询期间 CPU 一直在忙。

### §1.7 `spi_mem_adjust_op_size()`：让 op 装得下

`drivers/spi/spi-mem.c:455`。上层（spi-nor）想读 1MB，控制器 FIFO 只有 256 字节
——这个函数把 `op->data.nbytes` 削到能做的大小，**上层负责循环**。

两条实现：

| 条件 | 行为 |
|-----|------|
| 有 `mem_ops->adjust_op_size` | 交给驱动 |
| 没有，但有 `ctlr->max_transfer_size` | 按 `max_transfer_size` 削，**要扣掉 cmd+addr+dummy 的长度** |

第二条的扣减是必要的：路径 B 会把 cmd/addr/dummy 也变成 transfer，
它们同样受 `max_transfer_size` 约束。

### §1.8 谁在用 spi-mem

全树只有**两个** `spi_mem_driver`
（`grep -rn "struct spi_mem_driver " --include=*.c` 排除 spi-mem.c 自己）：

| 驱动 | 位置 | 管什么 |
|-----|------|-------|
| `spi_nor_driver` | `drivers/mtd/spi-nor/core.c:3277` | SPI-NOR flash |
| `spinand_drv` | `drivers/mtd/nand/spi/core.c:1393` | SPI-NAND flash |

实现 `mem_ops` 的控制器：**24 个文件，27 处赋值**
（`grep -rnE "(\.|->)mem_ops[[:space:]]*=" drivers/spi/*.c`）。arm64 上常见的有
`spi-rockchip-sfc.c`、`spi-stm32-qspi.c`、`spi-cadence-quadspi.c`、
`spi-mtk-nor.c`、`spi-nxp-fspi.c`、`spi-hisi-sfc-v3xx.c`。

**所以对使用者来说，`spi-mem` 是透明的**：你在 DT 里写一个
`jedec,spi-nor` 子节点，`spi-nor` 层通过 `spi-mem` 访问它。你不直接调
`spi-mem` 的任何 API。**只有写 QSPI 控制器驱动时才会实现 `mem_ops`。**

### §1.9 `spi_mem_probe()` 的包装

`drivers/spi/spi-mem.c:845`。`spi_mem_driver` 不是 `spi_driver`，
但注册时被包装成一个：

```
spi_mem_driver_register_with_owner()   :898
  └─ memdrv->spidrv.probe    = spi_mem_probe;      ← 固定
     memdrv->spidrv.remove   = spi_mem_remove;     :870
     memdrv->spidrv.shutdown = spi_mem_shutdown;   :879
     __spi_register_driver(owner, &memdrv->spidrv)

spi_mem_probe(spi)     :845
  ├─ devm_kzalloc(struct spi_mem)
  ├─ mem->spi = spi;
  ├─ 有 mem_ops->get_name 就用它，否则用 dev_name(&spi->dev)
  └─ memdrv->probe(mem)      ← 才轮到 spi-nor 自己的 probe
```

所以 `spi-nor` 的 probe 收到的是 `struct spi_mem *` 而不是
`struct spi_device *`。`spi_mem_get_name()`（`:434`）让控制器能自定义
设备名——某些 QSPI 上 flash 的名字不该是 `spiB.C`。

### §1.10 用 spi-mem 要注意什么

| 注意点 | 依据 |
|-------|------|
| **别给 QSPI 节点配 `cs-gpios`**，会静默关掉硬件加速 | `drivers/spi/spi-mem.c:328` |
| 写 `exec_op` 时不支持的 op 必须返 `-ENOTSUPP` | `:342` |
| `exec_op` 里**不能再调 `spi_sync()`**——已经持了 `io_mutex` 和 `bus_lock_mutex`，会自死锁 | `:283-284` |
| `exec_op` 期间普通 SPI 传输全部阻塞 | `:270` + `:283-284` |
| `dirmap_create` 成功不代表真用上了硬件映射 | `:481` / `:501` |
| `CONFIG_SPI_MEM` 是 bool，编进 spi 核心，不是独立模块 | `drivers/spi/Kconfig:51-52` |

---

## §2 spidev：用户态直接访问

### §2.1 它解决什么问题

有些 SPI 芯片**不值得写内核驱动**：一次性的产线校准工具、自研传感器的原型
验证、给 FPGA 灌 bitstream、逆向一个没有文档的芯片。`spidev` 把一个
`spi_device` 暴露成 `/dev/spidevB.C`，用户态用 `ioctl` 直接发 message。

它的定位是**开发和调试工具**，不是生产方案。`drivers/spi/spidev.c:710-713`
的注释把这条说得很直：

> "spidev should never be referenced in DT without a specific compatible
> string, it is a Linux implementation thing rather than a description of
> the hardware."

### §2.2 挂在核心的哪一点

| 挂载点 | 位置 | 对应 03 章节 |
|-------|------|------------|
| 一个普通的 `spi_driver`，靠 modalias / compatible 匹配 | `drivers/spi/spidev.c:694`（`spidev_spi_ids`）/ `:723`（`spidev_dt_ids`） | §A5 |
| `ioctl` 里调 `spi_sync()` | `spidev_message()` `:200` | §A7.1 |
| `SPI_IOC_WR_MODE` 等直接改 `spi->mode` 再调 `spi_setup()` | `spidev_ioctl()` `:345` | §A6 |

### §2.3 设备号与实例上限

| 常量 | 值 | 位置 |
|-----|---|------|
| `SPIDEV_MAJOR` | 153（静态分配的主设备号） | `drivers/spi/spidev.c:43` |
| `N_SPI_MINORS` | **32** | `:44` |
| `bufsiz`（module_param） | 默认 **4096** | `:86-88` |

**同时最多 32 个 spidev 实例**（`DECLARE_BITMAP(minors, N_SPI_MINORS)` `:46`，
`static_assert` 卡在 1~256 之间 `:48`）。第 33 个 probe 会失败——
`find_first_zero_bit()` `:792` 找不到空位。

`bufsiz` 是**模块参数，只读**（`S_IRUGO` `:87`）。要传超过 4096 字节
一次，必须加载时指定 `spidev.bufsiz=65536`。运行期改不了。
超限的地方有三处检查：`spidev_read()` `:153`、`spidev_write()` `:184`、
`spidev_message()` 里的 `rx_total > bufsiz` `:249` / `tx_total > bufsiz` `:259`。

### §2.4 三条访问方式

| 方式 | 实现 | 能做什么 | 不能做什么 |
|-----|------|---------|-----------|
| `read()` | `spidev_read()` `:147` | 半双工只读 | 不能同时收发；不能改片选行为 |
| `write()` | `spidev_write()` `:176` | 半双工只写 | 同上 |
| `SPI_IOC_MESSAGE(N)` | `spidev_message()` `:200` | **全双工、多 transfer、每段独立参数** | — |

**`read`/`write` 是 `SPI_IOC_MESSAGE` 的退化形式**，只在最简单的场景够用。
真正的用法是 `SPI_IOC_MESSAGE`——它把一个 `struct spi_ioc_transfer` 数组
翻译成 `spi_transfer` 链表，`cs_change` / `speed_hz` / `bits_per_word` /
`delay_usecs` / `tx_nbits` / `rx_nbits` **每段都能单独设**。

`drivers/spi/spidev.c:672-675` 有一条 REVISIT，说 `read`/`write` 应该改成 aio
——`03-internals.md` §C1 登记了这条设计债。

### §2.5 ioctl 全表

`spidev_ioctl()` `drivers/spi/spidev.c:345`。参数名和 UAPI 定义都在
`include/uapi/linux/spi/spidev.h`。

| ioctl | 参数 | 说明 |
|-------|-----|------|
| `SPI_IOC_RD_MODE` / `WR_MODE` | `__u8` | 低 8 位模式（`SPI_CPHA`/`CPOL`/`CS_HIGH`/`LSB_FIRST`/`3WIRE`/`LOOP`/`NO_CS`/`READY`） |
| `SPI_IOC_RD_MODE32` / `WR_MODE32` | `__u32` | 完整 32 位模式，含 `SPI_TX_QUAD` 等 |
| `SPI_IOC_RD_LSB_FIRST` / `WR_LSB_FIRST` | `__u8` | 只改 `SPI_LSB_FIRST` 位 |
| `SPI_IOC_RD_BITS_PER_WORD` / `WR_...` | `__u8` | 位宽。0 表示按 8 处理 |
| `SPI_IOC_RD_MAX_SPEED_HZ` / `WR_...` | `__u32` | 速率 |
| `SPI_IOC_MESSAGE(N)` | `struct spi_ioc_transfer[N]` | 提交一个 N 段的 message |

**写类 ioctl 的实现模式是「改了再试，失败就回滚」**：先把新值写进
`spi->mode` / `bits_per_word` / `max_speed_hz`，调 `spi_setup()`，
失败就恢复旧值。所以 `SPI_IOC_WR_*` 的错误返回是可信的——
**不成功就一定没生效**。

一个反直觉的行为：`SPI_IOC_RD_*` 读回的是**驱动记录的值，不是硬件实际值**。
`spi_setup()` 会静默把速率往下夹（`03-internals.md` §A6），
所以**读回 10MHz 不代表线上真的是 10MHz**。真值要看
`/sys/kernel/debug/tracing` 里 `spi_transfer_start` 或者 trace 的
`effective_speed_hz`。

### §2.6 `compatible` 白名单机制

**这是 spidev 最容易踩的坑。** `spidev` 不接受 DT 里直接写 `compatible = "spidev"`：

```c
/* drivers/spi/spidev.c:714 */
static int spidev_of_check(struct device *dev)
{
        if (device_property_match_string(dev, "compatible", "spidev") < 0)
                return 0;

        dev_err(dev, "spidev listed directly in DT is not supported\n");
        return -EINVAL;
}
```

合法的 compatible 是一张**硬编码白名单**（`:723-736`），11 项：

```
cisco,spi-petra          dh,dhcom-board         elgin,jg10309-01
lineartechnology,ltc2488 lwn,bk4                menlo,m53cpld
micron,spi-authenta      rohm,bh2228fv          rohm,dh2228fv
semtech,sx1301           silabs,em3581
```

三个后果：

1. **你自己的板子想用 spidev，必须往这张表里加一项**（或者用非 DT 方式，
   比如 `spi_board_info`——但 arm64 上不现实）。这是内核有意设置的门槛。
2. **实践中最常见的做法是借用 `rohm,dh2228fv`**——它在白名单里且几乎没人
   真用那颗芯片，事实上成了通用占位符。这不优雅但可行。
3. 错误消息 `spidev listed directly in DT is not supported`（`:719`）
   是 `dev_err`，**始终可见**。看到它就是这个问题。
   `07-debug.md` 的错误消息映射表里有登记。

ACPI 侧有对应的 `spidev_acpi_ids`（`:746`）和更强的警告
`do not use this driver in production systems!`（`:742`）——但
**arm64-only 范围内不涉及**（99 §2.7）。

### §2.7 缓冲与锁

```
spidev_open()   :581
  ├─ tx_buffer = kmalloc(bufsiz, GFP_KERNEL)     :602
  └─ rx_buffer = kmalloc(bufsiz, GFP_KERNEL)     :610

spidev_release()  :632
  └─ 引用计数归零时释放两个缓冲
```

**缓冲是按「打开的文件」分配的，不是按设备**。多次 `open()` 同一个
`/dev/spidevB.C` 只分配一次（有引用计数），但**两个进程会共享同一对缓冲**
——靠 `spidev->buf_lock` 串行化。所以两个进程并发 ioctl 会互相阻塞。

`drivers/spi/spidev.c:373` 的注释解释了锁的分工：
> "SPI_IOC_MESSAGE needs the buffer locked 'normally'."

### §2.8 用 spidev 要注意什么

| 注意点 | 依据 |
|-------|------|
| DT 里不能写 `compatible = "spidev"` | `:714-721` |
| 一次最多 `bufsiz`（默认 4096）字节，且只能加载时改 | `:86-88`、`:153`、`:184`、`:249`、`:259` |
| 最多 32 个实例 | `:44`、`:792` |
| `SPI_IOC_RD_MAX_SPEED_HZ` 读回的不是实际速率 | `03-internals.md` §A6 的静默下夹 |
| 用户态能设 `SPI_NO_CS`，从而破坏共享总线的消息边界 | `:60` 的 REVISIT，见 `03-internals.md` §C1 |
| `read`/`write` 只能半双工，全双工必须用 `SPI_IOC_MESSAGE` | `:147` / `:176` vs `:200` |
| 用户态工具用法见 `07-debug.md` | — |

---

## §3 spi-bitbang：软件移位寄存器辅助库

### §3.1 它解决什么问题

**注意定位：`spi-bitbang` 不是一个驱动，是一个「填空题模板」。**

它不注册任何设备。它做的事情是：**你只要提供「摇一个字」的函数，
它把 `transfer_one` / `set_cs` / `setup` / `cleanup` 这一整套核心回调
全部替你填好**。

适用两类驱动：

1. **真的用 GPIO 软模拟 SPI**（`spi-gpio.c`）——没有 SPI 控制器硬件
2. **有硬件但只有一个数据寄存器**（`spi-xilinx.c`、`spi-oc-tiny.c` 之类）
   ——本质上还是「一个字一个字地推」

### §3.2 挂在核心的哪一点

**它挂在 §A2 之前**：`spi_bitbang_start()` 里包含 `spi_register_master()`，
所以它是**注册的前置包装**，不是注册之后的扩展。

```
你的 probe()
  ├─ 填 struct spi_bitbang { master, txrx_word[4], chipselect, ... }
  └─ spi_bitbang_start(bitbang)              drivers/spi/spi-bitbang.c:423
        ├─ spi_bitbang_init(bitbang)         :342     ← 填回调
        └─ spi_register_master(spi_master_get(master))   :435
                └─ 进入 03 §A2 的 15 步
```

### §3.3 `spi_bitbang_init()` 填了什么

`drivers/spi/spi-bitbang.c:342`。这个函数是理解 bitbang 的钥匙——
它明确告诉你**核心的哪些回调被占用了**：

| 被填的字段 | 填成什么 | 行 |
|-----------|---------|---|
| `master->prepare_transfer_hardware` | `spi_bitbang_prepare_hardware` `:271` | 369 |
| `master->unprepare_transfer_hardware` | `spi_bitbang_unprepare_hardware` `:311` | 370 |
| `master->transfer_one` | `spi_bitbang_transfer_one` `:284` | 371 |
| `master->set_cs` | `spi_bitbang_set_cs` `:324`（**仅当 `custom_cs`**） | 377 |
| `master->setup` | `spi_bitbang_setup` `:183`（**仅当 `!bitbang->txrx_bufs && !master->setup`**） | 386 一带 |
| `master->cleanup` | `spi_bitbang_cleanup` `:227`（同上条件） | 387 一带 |
| `master->mode_bits` | `SPI_CPOL | SPI_CPHA | bitbang->flags`（**仅当原本是 0**） | 364 |
| `bitbang->txrx_bufs` | `spi_bitbang_bufs` `:233`（仅当没填） | 381 一带 |
| `bitbang->setup_transfer` | `spi_bitbang_setup_transfer` `:141`（仅当没填） | 384 一带 |

**两条硬性拒绝**：

```c
if (master->transfer || master->transfer_one_message)
        return -EINVAL;              /* drivers/spi/spi-bitbang.c:366-367 */
```
**已经自己实现了 `transfer` 或 `transfer_one_message` 的驱动不能用 bitbang**
——两者会打架。

```c
custom_cs = (!master->use_gpio_descriptors ||
             (master->flags & SPI_MASTER_GPIO_SS));
if (custom_cs && !bitbang->chipselect)
        return -EINVAL;              /* drivers/spi/spi-bitbang.c:354-358 */
```
**`chipselect` 回调只在「核心不会替你管片选」时才是必需的**。
注释写得很清楚：

> "We only need the chipselect callback if we are actually using it.
> If we just use GPIO descriptors, it is surplus. If the
> SPI_MASTER_GPIO_SS flag is set, we always need to call the
> driver-specific chipselect routine."

这条正好把 `03-internals.md` §B2.2 那张四组合表从另一个角度证实了一遍：
`use_gpio_descriptors` + 没有 `SPI_MASTER_GPIO_SS` ⇒ 驱动的片选回调根本不会被调。

### §3.4 `txrx_word[4]`：四个模式各一个函数

```c
u32 (*txrx_word[4])(struct spi_device *spi, unsigned nsecs,
                    u32 word, u8 bits, unsigned flags);
/* include/linux/spi/spi_bitbang.h:31-33 */
```

**下标是 SPI 模式号（0~3），不是位宽。** 因为 CPOL/CPHA 的四种组合
需要四段不同的边沿代码——用 `if` 判断会在每个 bit 上多一次分支，
所以固化成四个函数。

`spi-bitbang-txrx.h` 里的 `bitbang_txrx_be_cpha0/1` 宏是标准实现，
`spi-gpio.c` 用它生成八个函数（4 模式 × 2 大小端）。

**位宽**由另一层处理——`spi_bitbang_bufs()` `:233` 根据 `bits_per_word`
选 `bitbang_txrx_8` `:54` / `_16` `:83` / `_32` `:112` 三个包装，
每个包装再循环调 `txrx_word[mode]`。

### §3.5 谁在用

全树 **13 个驱动**（`grep -rln "struct spi_bitbang" --include=*.c drivers/`
排除 spi-bitbang.c 自己），`spi_bitbang_start()` 有 **12 个外部调用点**：

```
spi-ath79.c   spi-au1550.c    spi-butterfly.c  spi-davinci.c
spi-gpio.c    spi-lm70llp.c   spi-oc-tiny.c    spi-omap-uwire.c
spi-ppc4xx.c  spi-s3c24xx.c   spi-sh-sci.c     spi-xilinx.c
spi-xtensa-xtfpga.c
```

**arm64 上只有 `spi-gpio.c` 现实可用**——其余全是特定 SoC（ath79/au1550/
davinci/ppc4xx/s3c24xx/sh/xtensa）或者并口调试工装（butterfly/lm70llp）。
`spi-xilinx` 理论上可以（Zynq MPSoC 的 PL 里），但 arm64 DTS 里没有实例。

### §3.6 用 bitbang 要注意什么

| 注意点 | 依据 |
|-------|------|
| 它包含 `spi_register_master()`，不要自己再调一次 | `:435` |
| 已实现 `transfer_one_message` 的驱动不能用 | `:366-367` |
| `txrx_word[]` 的下标是模式号 | `include/linux/spi/spi_bitbang.h:31` |
| 用 gpiod 片选且没设 `SPI_MASTER_GPIO_SS` 时，`chipselect` 回调不需要也不会被调 | `:354-358` |
| **性能**：每个 bit 都是软件循环 + `ndelay`，通常只能跑到几百 kHz | `:54`/`:83`/`:112` |
| 它用的是 `spi_master` 旧名字，不是 `spi_controller` | 整个文件 |

---

## §4 spi-mux：用外部 mux 芯片扩展片选

### §4.1 它解决什么问题

控制器只有 2 个片选，但板上有 8 个 SPI 芯片。外面加一个 mux 芯片，
用几根 GPIO 选择片选路由到哪个设备。

**实现方式非常巧妙：它注册成一个 SPI 控制器，而它自己是一个 SPI 设备。**

```
platform bus
   └─ spi0（真实控制器）              spi_controller
        └─ spi0.0  = spi-mux          既是 spi_device，又持有一个 spi_controller
             └─ spi1（虚拟控制器）     ← 8 个片选
                  ├─ spi1.0
                  ├─ spi1.1
                  └─ ...
```

### §4.2 挂在核心的哪一点

| 挂载点 | 位置 | 对应 03 章节 |
|-------|------|------------|
| 一个普通 `spi_driver`，compatible `"spi-mux"` | `drivers/spi/spi-mux.c:185-188` | §A5 |
| probe 里注册一个新控制器 | `devm_spi_register_controller()` `:166` | **递归进入 §A2** |
| `transfer_one_message` 转发到父设备 | `spi_mux_transfer_one_message()` `:99` | §A9 / §A10 的替代实现 |
| 完成回调链的偷换 | `spi_mux_complete_cb()` `:86` | §A11 |

### §4.3 probe 里的三处关键设置

`drivers/spi/spi-mux.c:126`：

```c
lockdep_set_subclass(&ctlr->io_mutex, 1);      /* :144 */
lockdep_set_subclass(&ctlr->add_lock, 1);      /* :145 */
...
ctlr->mode_bits          = spi->controller->mode_bits;         /* :158 */
ctlr->flags              = spi->controller->flags;             /* :159 */
ctlr->bits_per_word_mask = spi->controller->bits_per_word_mask;/* :160 */
ctlr->transfer_one_message = spi_mux_transfer_one_message;     /* :161 */
ctlr->setup              = spi_mux_setup;                      /* :162 */
ctlr->num_chipselect     = mux_control_states(priv->mux);       /* :163 */
ctlr->bus_num            = -1;                                 /* :164 */
ctlr->dev.of_node        = spi->dev.of_node;                   /* :165 */
ctlr->must_async         = true;                               /* :166 */
```

**三条必须理解**：

1. **能力全部继承父控制器**（`:158-160`）。mux 芯片本身不改变信号，
   所以支持的模式/位宽和父控制器完全一样。**但这也意味着不能超过父控制器**
   ——想在 mux 后面挂一个需要 QUAD 模式的芯片，父控制器必须支持 QUAD。
2. **`num_chipselect` 来自 mux 的状态数**（`:163`）。`mux_control_states()`
   由 `mux` 子系统（`drivers/mux/`）提供，取决于有几根选择线。
3. **`must_async = true`（`:166`）是防死锁的必要条件**，不是优化。
   见 `03-internals.md` §B1.3——这是全树唯一设它的地方。

### §4.4 `spi_mux_select()`：为什么能拷贝子设备的参数

`drivers/spi/spi-mux.c:49`。函数上面有一句关键注释：

> "should not get called when the parent controller is doing a transfer"

```c
ret = mux_control_select(priv->mux, spi->chip_select);      /* :53 */
if (priv->current_cs == spi->chip_select)
        return 0;                                           /* :58-59 */

/* copy the child device's settings except for the cs */
priv->spi->max_speed_hz   = spi->max_speed_hz;              /* :65 */
priv->spi->mode           = spi->mode;                      /* :66 */
priv->spi->bits_per_word  = spi->bits_per_word;             /* :67 */
priv->current_cs          = spi->chip_select;               /* :69 */
```

**它直接改写 `priv->spi`（也就是自己作为 spi_device 的那个结构）的参数**。
这是一个只有在「父总线上此刻没有别的传输」时才安全的操作——而这一点由
`must_async` + `io_mutex` 保证：mux 的 `transfer_one_message` 是在父控制器的
消息泵之外被调的（走 §A9 泵路径），此时父控制器的 `io_mutex` 由 mux 自己的
泵持有，父设备的参数不会被别人读。

`priv->current_cs` 的缓存和核心的 `last_cs`（`03-internals.md` §B2.1）
是同一个思路：**状态没变就不重复设**。`SPI_MUX_NO_CS` 是初始值（`:156`）。

### §4.5 完成回调的偷换

`spi_mux_transfer_one_message()` `drivers/spi/spi-mux.c:99` 的核心把戏：

```
子设备提交的 message m:
    m->complete = 子设备的回调
    m->context  = 子设备的 context
    m->spi      = 子设备
         ↓ 保存到 priv，然后偷换                    :112-118
    m->complete = spi_mux_complete_cb
    m->context  = priv
    m->spi      = priv->spi          ← 父总线上的那个 spi_device
         ↓ spi_async(priv->spi, m)                  :123
    父控制器执行这个 message
         ↓ 父控制器调 m->complete = spi_mux_complete_cb   :86
    spi_mux_complete_cb(priv):
        m->complete = priv->child_msg_complete       ← 换回来
        m->context  = priv->child_msg_context
        m->spi      = priv->child_msg_dev
        spi_finalize_current_message(ctlr)           ← 通知 mux 自己的核心
        mux_control_deselect(priv->mux)
```

**同一个 `spi_message` 对象被两条总线共用**，靠三个字段的存取实现。
两个后果：

1. **mux 后面的设备最多只能有一个传输在飞**（`priv->child_msg_*` 是单份的）。
   这不是限制——mux 本来就只能选一个片选。
2. **`spi_async()` 是这里唯一可行的调用**（`:123`）：`transfer_one_message`
   返回后核心期待异步完成，用 `spi_sync()` 会在父控制器的 `io_mutex` 上
   和 mux 自己持的锁打架。

### §4.6 `spi_mux_setup()`：为什么不做真正的 setup

`drivers/spi/spi-mux.c:74`。注释写明了：

> "can be called multiple times, won't do a valid setup now but we will
> change the settings when we do a transfer (necessary because we can't
> predict from which device it will be anyway)"

**mux 无法在 setup 时知道参数该是谁的**——8 个子设备参数可能各不相同。
所以 setup 只是把父设备 setup 一遍占个位，真正的参数在每次
`spi_mux_select()` 时才写。

**后果**：`spi_setup()` 在 mux 上的错误检查能力被削弱了。子设备要求一个
父控制器不支持的模式，`spi_setup()` 时可能不报错（因为检查的是继承来的
`mode_bits`，粒度够但时机晚），真正的失败会推迟到第一次传输。

### §4.7 用 spi-mux 要注意什么

| 注意点 | 依据 |
|-------|------|
| 需要 `CONFIG_MULTIPLEXER` 和一个 mux 驱动（`drivers/mux/`） | `devm_mux_control_get()` `:147` |
| 子设备能力不能超过父控制器 | `:158-160` |
| 多级 mux 会遇到 lockdep 子类只降了一级的问题 | `:144-145` 只设 subclass 1 |
| mux 上的设备**不能走同步快路径**，延迟一定高于直连设备 | `must_async` `:166` |
| DT 里 mux 节点要同时是 `spi_device` 和 `spi_controller`（既有 `reg` 又有子节点） | `ctlr->dev.of_node = spi->dev.of_node` `:165` |
| **有 in-tree arm64 实例**：Microchip Sparx5，4 个 DTS 文件 / 5 个节点 | `grep -rn "spi-mux" arch/arm64/boot/dts/microchip/`；真实节点见 `06-devicetree.md` §2.4（`sparx5_pcb125.dts:42-70`） |

---

## §5 SPI slave / target：SoC 当从机

### §5.1 它解决什么问题

**先说结论：arm64 上你基本碰不到这个，除非做很特殊的板间通信。**

正常情况 SoC 是主机（产生时钟、控制片选）。slave 模式是反过来：**外部主机
产生时钟，SoC 被动响应**。用途是板间通信——比如一块管理板通过 SPI 读取
一块业务板的状态。

需要三个条件同时成立：
1. **控制器硬件支持从机模式**（大多数不支持）
2. `CONFIG_SPI_SLAVE=y`（`drivers/spi/Kconfig:1172`）
3. 有一个 slave protocol handler（内核里只有两个示例）

### §5.2 挂在核心的哪一点

| 挂载点 | 位置 | 对应 03 章节 |
|-------|------|------------|
| `spi_alloc_slave()` 让 `dev.class = &spi_slave_class` | `drivers/spi/spi.c:2925-2926` | §A2 的第 2 步 |
| 注册时走同一个 `spi_register_controller()` | — | §A2（完全复用） |
| `sysfs` 的 `slave` 属性动态绑 handler | `slave_show` `:2799` / `slave_store` `:2811` | 不在时间线上（运行期） |
| `spi_slave_abort()` 中止进行中的传输 | `:2777` | §A12 的第四层（主机模式没有这一层） |
| DT 里 `slave` 子节点的短路解析 | `of_spi_parse_dt()` `:2306-2313` | §A4.3 |

### §5.3 三处关键差异

**差异 1：一个控制器只挂一个从设备。**

`slave_store()`（`drivers/spi/spi.c:2811`）的逻辑：

```
写入 modalias 字符串
  ├─ device_find_any_child(&ctlr->dev)   有旧的就 device_unregister 掉
  ├─ 如果不是 "(null)"：
  │     spi_alloc_device(ctlr)
  │     strscpy(spi->modalias, name)
  │     spi_add_device(spi)
  └─ 返回 count
```

**每次写入都先删掉现有的从设备**。这在结构上强制了「一个从机控制器
只有一个从设备」。写 `(null)` 就是纯删除。

`slave_show()`（`:2799`）读回当前从设备的 modalias，没有就是 `(null)`。

**差异 2：DT 里的子节点必须叫 `slave`。**

`of_spi_parse_dt()`（`drivers/spi/spi.c:2306-2313`）：如果控制器是从机模式，
它**只认名字是 `slave` 的子节点**，且不解析 `reg`（从机没有片选编号）。
名字不对就直接跳过——静默失效。

**差异 3：`slave_abort` 是唯一的取消机制。**

主机模式下传输总会结束（时钟由自己产生）。从机模式下**外部主机可能永远
不来时钟**，所以必须能中止。

```c
int spi_slave_abort(struct spi_device *spi)                /* :2777 */
{
        ...
        if (spi_controller_is_slave(ctlr) && ctlr->slave_abort)
                return ctlr->slave_abort(ctlr);            /* :2781-2782 */
        return -ENOTSUPP;
}
```

**`slave_abort` 有 9 个实现，`target_abort` 有 0 个**
（`03-internals.md` §C3）。`spi_target_abort()`（`:2789` 起，在 `:2795`
返 `-ENOTSUPP`）在 6.1 里是**永远失败的 API**——它是为将来的改名准备的。

### §5.4 两个示例 handler

| handler | 文件 | 做什么 |
|---------|------|-------|
| `spi-slave-time` | `drivers/spi/spi-slave-time.c`（128 行） | 每次收到 message，回送启动至今的时间（两个 `__be32`：秒 + 微秒） |
| `spi-slave-system-control` | `drivers/spi/spi-slave-system-control.c`（154 行） | 收到两字节命令就执行系统操作 |

`spi-slave-system-control` 的命令表（`:33-36`），命令是 ASCII 助记：

| 命令值 | 助记 | 动作 |
|-------|------|-----|
| `0x7c50` | `rb` | `kernel_restart(NULL)` `:62` |
| `0x713f` | `OF` | 关机 `:65` 一带 |
| `0x3876` | `HL` | halt `:70` 一带 |
| `0x1b1b` | `ZZ` | suspend `:75` 一带 |

**这是一个远程电源控制通道**：外部主机通过 SPI 就能重启/关闭这块板。
`spi-slave-time.c:15-24` 的注释给了配套的验证命令：

```
# spidev_test -D /dev/spidev2.0 -p dummy-8B
RX | 00 00 04 6D 00 09 5B BB ...
     ^^^^^    ^^^^^^^^
     seconds  microseconds
```

两个 handler 的实现模式相同，都值得作为**写 slave handler 的模板**读：
`submit → complete 回调 → 再 submit` 的自循环（`spi_slave_time_complete()`
`drivers/spi/spi-slave-time.c:42` → `spi_slave_time_submit()`），
message 一直挂在总线上等外部主机来读。

### §5.5 用 slave 要注意什么

| 注意点 | 依据 |
|-------|------|
| 先确认控制器驱动支持从机（找 `spi_alloc_slave` 或 `slave = true`） | `drivers/spi/spi.c:2925` |
| DT 子节点名字必须是 `slave` | `:2306-2313` |
| 一个控制器只能挂一个从设备 | `slave_store()` `:2811` |
| `spi_target_abort()` 在 6.1 里必然返 `-ENOTSUPP` | `:2795` |
| 从机模式下 message 必须一直挂着等主机来读，所以要用 `spi_async()` + 自循环 | `spi-slave-time.c:42` |
| `spi-slave-system-control` 提供的是无认证的远程重启通道 | `:33-36` |

---

## §6 spi-loopback-test：控制器驱动自测框架

### §6.1 它解决什么问题

刚写完一个控制器驱动，怎么知道它对？**这个模块用「同一根线上收发」的
方式，把核心传输路径的各种边界情况跑一遍**，不需要接任何外设。

两种接线方式：

| 方式 | 怎么做 | `loopback` 参数 |
|-----|-------|---------------|
| **物理回环** | MOSI 和 MISO 短接 | `loopback=1`（校验收到的和发出的一致） |
| **控制器回环** | 控制器支持 `SPI_LOOP` 模式 | 同上 |
| **只发不校验** | 什么都不接 | `loopback=0`（只验证不崩、不超时） |

### §6.2 挂在核心的哪一点

一个普通 `spi_driver`（§A5），所有测试都通过 `spi_sync()` 提交（§A7.1）。
它**不碰核心内部**——所以它测的是**核心 + 驱动的组合行为**，
和真实使用完全一致。

compatible 可以用模块参数覆盖（`drivers/spi/spi-loopback-test.c:379`）：

```c
module_param_string(compatible, spi_loopback_test_of_match[0].compatible, ...);
```

这条很实用：**不改 DT 就能把测试挂到任意 SPI 节点上**。

### §6.3 15 个测试用例

`drivers/spi/spi-loopback-test.c` 里 `spi_tests[]` 的 `.description`
（`grep -n "\.description"`，第 77~317 行共 15 条）：

| # | 用例 | 测什么 |
|---|-----|-------|
| 1 | `tx/rx-transfer - start of page` | 基本全双工 |
| 2 | `tx/rx-transfer - crossing PAGE_SIZE` | **缓冲跨页**——DMA 映射的经典坑 |
| 3 | `tx-transfer - only` | 只发（rx_buf 为 NULL → 触发哑缓冲，§B3.1） |
| 4 | `rx-transfer - only` | 只收（tx_buf 为 NULL → 同上） |
| 5-7 | `two tx-transfers - alter both/first/second` | 多 transfer + 逐段改参数 |
| 8-10 | `two transfers tx then rx - alter both/tx/rx` | 半双工两段 |
| 11-13 | `two tx+rx transfers - alter both/first/second` | 全双工两段 + 逐段改参数 |
| 14 | `two tx+rx transfers - delay after transfer` | **`delay` 字段生效吗**（§B5） |
| 15 | `three tx+rx transfers with overlapping cache lines` | **缓冲共享 cache line**——DMA 一致性的经典坑 |

**这张表本身就是一份「控制器驱动必须处理的边界清单」**。用例 2、3、4、15
分别对应 `03-internals.md` §B3 讲的跨页映射、哑缓冲、cache 一致性
——**这四条是驱动最常出错的地方**，所以被单独列成用例。

### §6.4 八个模块参数

`drivers/spi/spi-loopback-test.c:27-71`：

| 参数 | 行 | 权限 | 作用 |
|-----|---|-----|------|
| `simulate_only` | 27 | 0（只读） | 只构造 message 不真发。**验证核心校验逻辑而不碰硬件** |
| `dump_messages` | 32 | 0 | 打印每个 message 的内容。1=概要，2=含数据 |
| `loopback` | 39 | 0 | 是否校验收到的数据 |
| `loop_req` | 46 | 0 | 要求控制器支持 `SPI_LOOP` 并启用它 |
| `no_cs` | 52 | 0 | 设 `SPI_NO_CS`（外部接线场景） |
| `run_only_test` | 58 | 0 | **只跑第 N 个用例**（调试单个失败用例） |
| `use_vmalloc` | 64 | **0644** | 用 `vmalloc` 而不是 `kmalloc` 分配缓冲 |
| `check_ranges` | 70 | **0644** | 校验有没有写越界 |

**只有 `use_vmalloc` 和 `check_ranges` 是运行期可写的（0644）**，
其余必须加载时指定。这两个恰好是最需要反复切换的：

- `use_vmalloc=1` 的意义：**`vmalloc` 的内存物理上不连续**。
  DMA 驱动如果假设了物理连续会立刻崩——这是一个高价值的破坏性测试。
- `check_ranges=1`：检测驱动有没有写出缓冲边界。

### §6.5 怎么用

```bash
# 编译进内核或模块，然后在 DT 里挂一个节点（或用 compatible 参数覆盖）
modprobe spi-loopback-test loopback=1 dump_messages=1

# 只跑第 15 个用例（cache line 重叠），用 vmalloc 加大压力
modprobe spi-loopback-test loopback=1 run_only_test=15 use_vmalloc=1

# 不接线，只验证不崩
modprobe spi-loopback-test loopback=0
```

输出在 `dmesg` 里，每个用例一行 `Running test %s`
（`drivers/spi/spi-loopback-test.c:890`）。`07-debug.md` 有完整的判读说明。

### §6.6 用 loopback-test 要注意什么

| 注意点 | 依据 |
|-------|------|
| 它测的是**核心 + 驱动组合**，不能定位问题在哪一层 | 全部走 `spi_sync()` |
| `loopback=0` 时只能发现崩溃和超时，发现不了数据错 | `:39-40` |
| 六个参数只能加载时设，改要重新 `modprobe` | `:27`~`:59` 的权限都是 0 |
| 用例 2/3/4/15 是驱动最常出错的四类，跑通它们价值最大 | `:91`/`:105`/`:117`/`:317` |
| 它不测 `spi-mem` 路径 | `spi-mem` 绕过消息泵，见 §1.2 |

---

## §7 汇总

### §7.1 六个框架 × 四个维度

| 框架 | 核心职责 | 挂在 03 时间线的哪一点 | arm64 相关性 |
|-----|---------|---------------------|-----------|
| **spi-mem** | 把「四段结构」抽象出来，让 QSPI 硬件加速器可用；不支持时翻译成 `spi_transfer` | §A5（注册包装）+ **绕过 §A7~§A10 的第四条路径** | 有 flash 就用得上 |
| **spidev** | 把一个 `spi_device` 暴露成 `/dev/spidevB.C` | §A5 → §A6（ioctl 改参数）→ §A7.1（`spi_sync`） | 调试必备 |
| **spi-bitbang** | 替驱动填好整套核心回调，只需提供「摇一个字」 | **§A2 之前**（`spi_bitbang_start` 含 `spi_register_master`） | 仅 `spi-gpio` |
| **spi-mux** | 注册一个虚拟控制器，用外部 mux 扩展片选 | §A5 → **递归进入 §A2**；`transfer_one_message` 替代 §A9/§A10 | **有 arm64 实例**（Sparx5，4 个 DTS） |
| **slave/target** | 让 SoC 当从机被外部主机读 | §A2 第 2 步走 `spi_slave_class`；新增 §A12 的第四层（abort） | ✗ 需硬件支持 |
| **spi-loopback-test** | 用 15 个用例验证控制器驱动 | 全部走 §A7.1，不碰核心内部 | 写驱动时 |

### §7.2 六个框架的全树使用统计

上面 §1.8 和 §3.5 各自有一节"谁在用"，另外四个框架原来没有。
**这张表把六个补齐，每格都配可重跑的命令**——这一节是为了回答
"我该不该关心这个框架"，光看"它是什么"回答不了。

| 框架 | 实现方 / 使用方的全树计数 | 重跑命令 |
|-----|----------------------|--------|
| **spi-mem** | 消费者 **2 个** `spi_mem_driver`（`drivers/mtd/spi-nor/core.c:3277`、`drivers/mtd/nand/spi/core.c:1393`）；实现方 **24 个文件 / 27 处** `mem_ops` 赋值 | `grep -rn "struct spi_mem_driver " --include=*.c drivers/`<br>`grep -rlE "mem_ops[[:space:]]*=" --include=*.c drivers/spi/ \| grep -v spi-mem.c \| wc -l` |
| **spidev** | `compatible` 白名单 **11 条**（`drivers/spi/spidev.c:723` 起）；不在白名单里的 DT 节点绑不上，见 §2.6 | `awk '/spidev_dt_ids\[\] = \{/,/^};/' drivers/spi/spidev.c \| grep -c compatible` |
| **spi-bitbang** | **13 个**驱动引用 `struct spi_bitbang`，`spi_bitbang_start()` 有 **12 个**外部调用点；arm64 上只有 `spi-gpio.c` 现实可用（详见 §3.5） | `grep -rln "struct spi_bitbang" --include=*.c drivers/` |
| **spi-mux** | **arm64 DTS 4 个文件 / 5 个节点**，全部是 Microchip Sparx5：`sparx5_pcb125.dts:45`/`:57`、`sparx5_nand.dtsi:17`、`sparx5_pcb135_board.dtsi:102`、`sparx5_pcb134_board.dtsi:287`。**非 arm64 架构 0 个** | `grep -rn "spi-mux" arch/*/boot/dts/` |
| **slave / target** | 读 `"spi-slave"` 属性的控制器驱动 **8 个**（`spi-omap2-mcspi` / `spi-fsl-lpspi` / `spi-rockchip` / `spi-pxa2xx` / `spi-imx` / `spi-sh-msiof` / `spi-fsl-dspi` / `spi-sunplus-sp7021`）；in-tree slave **协议**驱动 **2 个**（`spi-slave-time.c:124`、`spi-slave-system-control.c:150`，都用 `module_spi_driver`）；另有 **1 个** slave-only **控制器** `spi-slave-mt27xx.c:391`（用 `spi_alloc_slave`）——**名字带 `spi-slave-` 前缀的三个文件里，两个是协议驱动、一个是控制器** | `grep -rln --include=*.c '"spi-slave"' drivers/`<br>`grep -n "module_spi_driver\|spi_alloc_slave" drivers/spi/spi-slave-*.c` |
| **spi-loopback-test** | **0 个 in-tree 使用者**——它是测试模块本身，靠 `CONFIG_SPI_LOOPBACK_TEST` 单独编（`drivers/spi/Makefile:14`），没有别的代码引用它 | `grep -rn "spi-loopback-test\|spi_loopback" drivers/spi/Makefile` |

**这张表补出来的时候纠正了一个错误结论**：原来 §4.7 和 §7.1 都写着
"spi-mux 在 arm64 DTS 里没有 in-tree 实例"。实际有 4 个文件 5 个节点。
更糟的是 **`06-devicetree.md` §2.4 一直引用着其中一个真实节点**
（`sparx5_pcb125.dts:42-70`）——两篇文档对同一件事给了相反的答案。
详见 `99-evidence.md` §3.5 自审修正第 6 条。

### §7.3 三条贯穿性观察

1. **只有 `spi-mem` 真正扩展了核心的语义**，其余五个都是在核心之上做包装：
   spidev 是接口转换，bitbang 是回调填充，mux 是递归嵌套，slave 是同一套
   代码的另一种配置，loopback-test 是纯使用者。这也解释了为什么
   `spi-mem` 需要 `drivers/spi/internals.h` 这个私有头文件
   （`spi_flush_queue()` / `spi_map_buf()` 只给 spi.c 和 spi-mem.c 用，
   `internals.h:8-9` 的注释明说了）。

2. **三个框架都在解决「参数该跟谁」的问题**，解法各不相同：

   | 框架 | 问题 | 解法 |
   |-----|-----|-----|
   | spi-mux | 8 个子设备参数不同，父设备只有一份 | 每次传输前拷贝（`:65-67`），靠 `must_async` 保证安全 |
   | spidev | 用户态想改参数 | 改了调 `spi_setup()`，失败回滚 |
   | spi-mem | op 的四段线宽各不相同 | 不改 `spi_device`，逐 transfer 设 `tx_nbits`/`rx_nbits` |

3. **两个框架显式承认「无法在正确的时机做正确的事」**，并把这一点写进了注释：
   `spi_mux_setup()`（`:78-81`「can't predict from which device it will be」）
   和 `spi_mem_exec_op()` 的 `-ENOTSUPP` 回退（`:339-341`「Some controllers
   only optimize specific paths」）。**这两处注释比代码更有信息量**——
   它们记录了设计者遇到的真实约束。

---

## 相关章节

| 想知道 | 去哪 |
|-------|------|
| 这些框架用到的核心 API | `01-api.md` |
| `spi_mem_op` / `spi_bitbang` 的完整字段表 | `02-datastructures.md` §2、§7 |
| §A/§B/§C 各章节的正文 | `03-internals.md` |
| 照着写一个 spi-mem 驱动 / spidev 用户程序 | `05-usage.md` |
| spidev 的 DT compatible 怎么配 | `06-devicetree.md` |
| loopback-test 的输出怎么判读、spidev_test 怎么用 | `07-debug.md` |
| 「为什么 spi-mux 要注册成控制器」的大白话版 | `08-feynman.md` |
| 本篇的全称断言取证命令 | `99-evidence.md` §3 |

