---
title: 总览
---

# SPI 子系统总览（Linux 6.1.158, arm64）

> 本篇定位是**复习入口**。隔几个月回来只读这一篇，就能恢复 SPI 子系统的全貌；
> 需要细节时按各节末尾标注的章节号下钻到 01–08。
>
> 分析基线：内核 6.1.158（`git log -1` = `f6e38ae624cf`），目标架构 **arm64 only**。
> 裁掉了什么、为什么裁，见 `99-evidence.md` §2.7。

---

## 目录

| 节 | 内容 | 什么时候读它 |
|----|------|------------|
| §1 | 一句话定位：SPI 核心帮你省掉了什么 | 第一次接触 |
| §2 | 硬件背景：SPI 协议、时序、片选、arm64 上的实际形态 | 看不懂后面为什么这样设计时 |
| §3 | 概念模型：controller / device / driver 三个角色 | 分不清谁是谁时 |
| §4 | 核心数据结构速览 | 复习时 |
| §5 | 核心机制速览（对齐 `03-internals.md` 章节号） | 复习时 |
| §6 | 端到端调用路径：从 `spi_sync()` 到寄存器 | 想知道数据怎么走的 |
| §7 | 与内核其它子系统的关系 | 排查跨子系统问题时 |
| §8 | 本文档集导读（按角色分路径） | 不知道该读哪篇时 |

---

## §1 一句话定位

**SPI 子系统是内核里的"同步串行外设接口"总线框架**：它把"一颗芯片挂在某个
SoC 的 SPI 控制器上"这件事，拆成三个可以独立编写、独立编译、独立复用的部分——
控制器驱动（怎么摇时钟）、协议驱动（怎么解释字节）、以及中间那层核心
（`drivers/spi/spi.c`）。

### 它帮你省掉了什么

如果没有这一层，每个 SPI 外设驱动都要自己做下面这些事。核心做完之后，
外设驱动只剩"填一个 `spi_transfer` 数组然后 `spi_sync()`"：

| 核心替你做的 | 不做会怎样 | 代码位置 |
|------------|-----------|---------|
| **设备枚举** | 每个驱动自己解析 DT、自己算片选号、自己建 `struct device` | `of_register_spi_devices()` `drivers/spi/spi.c:2386` |
| **片选管理** | 每个驱动自己拉 GPIO、自己处理 `SPI_CS_HIGH` 极性反转、自己插 setup/hold 延时 | `spi_set_cs()` `drivers/spi/spi.c:975` |
| **消息排队与串行化** | 两个外设驱动同时发起传输就会在总线上打架 | `__spi_pump_messages()` `drivers/spi/spi.c:1764` |
| **DMA 映射** | 每个控制器驱动自己 `dma_map_sg` + 自己处理 vmalloc 缓冲区分段 | `spi_map_msg()` `drivers/spi/spi.c:1300` |
| **参数校验** | 传了控制器不支持的 `bits_per_word` / 速率 / 模式位，直接得到静默错数据 | `__spi_validate()` `drivers/spi/spi.c:3850`、`spi_setup()` `drivers/spi/spi.c:3702` |
| **超长传输拆分** | 控制器只能一次传 64 KiB，驱动要自己切并且自己重算 CS 保持 | `spi_split_transfers_maxsize()` `drivers/spi/spi.c:3603` |
| **通用 `transfer_one_message()`** | 每个控制器驱动都要自己写一遍"逐 transfer 循环 + CS 翻转 + 延时 + 统计" | `spi_transfer_one_message()` `drivers/spi/spi.c:1507` |
| **可观测性** | 没有 tracepoint、没有 sysfs 统计，性能问题只能靠加 printk | `include/trace/events/spi.h`、`drivers/spi/spi.c:139-215` |
| **运行时电源管理** | 每个控制器驱动自己在传输前后 get/put runtime PM | `auto_runtime_pm` 分支，`drivers/spi/spi.c:1647-1659`、`:1633-1639` |

一句话记法：**核心负责"什么时候、按什么参数、在谁身上"，控制器驱动只负责
"怎么把这一段字节摇出去"。**

### 一个数字量级

`drivers/spi/` 下 137 个 `.c` + 12 个 `.h`，合计 105,974 行；其中核心
`drivers/spi/spi.c` 只有 4,632 行。也就是说 **96% 的代码是控制器驱动，
4% 是核心**——核心的价值就在于让那 96% 不必互相重复。

---

## §2 硬件背景

> 本节讲的是 SPI 这个**总线本身**，不是内核代码。后面几乎每一个设计选择
> （为什么没有总线恢复、为什么片选要软件插延时、为什么 `spi_transfer` 里
> tx/rx 长度必须相等）都是这里某条物理约束的直接后果。
> 凡是内核里有对应定义的，标 `文件:行号`；纯协议约定的注明"无 RFC/无标准"。

### §2.1 物理层：四根线，全推挽，无仲裁

SPI 没有正式标准文档（不像 I²C 有 NXP UM10204、USB 有 USB-IF 规范）。
它是 Motorola 在 1980 年代为 68HC11 定义的一组引脚约定，此后被所有 SoC 厂商
各自"方言化"。内核里能找到的权威描述有两处：
`Documentation/spi/spi-summary.rst`（639 行，文件头日期 `02-Feb-2012`）
和 `Documentation/driver-api/spi.rst`（53 行，物理层和编程模型各一段）。
后者开头那段对四线信号和全双工语义的描述是本节 §2.1 的官方对应原文；
但它同时有一句已经过时的话，见 `99-evidence.md` §1.7 第 5 条。

标准四线：

```
      SoC 侧（master / host）                 芯片侧（slave / target / peripheral）
   ┌───────────────────────┐               ┌──────────────────────────┐
   │                 SCLK  ├──────────────►│ SCLK   时钟，只由主机驱动 │
   │                 MOSI  ├──────────────►│ SDI    主出从入           │
   │                 MISO  │◄──────────────┤ SDO    主入从出           │
   │                 CS0#  ├──────────────►│ CS#    片选，低有效（惯例）│
   └───────────────────────┘               └──────────────────────────┘
```

三个关键电气事实，以及各自导致的软件后果：

| 电气事实 | 软件后果 |
|---------|---------|
| **全部是推挽输出**（不是 I²C 的开漏） | 没有"设备把线拉低卡住总线"这种故障模式 → **SPI 核心里没有任何总线恢复机制**。对比 I²C：`i2c_adapter` 有 `bus_recovery_info`，SPI 的 `struct spi_controller`（`include/linux/spi/spi.h:509-729`）里根本没有对应字段 |
| **时钟只由主机驱动，从设备完全被动** | 从设备无法反压。想让从设备喘口气只有两条路：主机主动插延时（`spi_transfer.word_delay`，`include/linux/spi/spi.h:1041`），或者用可选的 `SPI_READY` 握手线（`include/uapi/linux/spi/spi.h:21`）——后者极少硬件支持 |
| **没有地址、没有应答位、没有仲裁** | 芯片"在不在"从总线上问不出来。**SPI 没有 I²C 那种探测（probe）能力**——DT 里写了什么，内核就当它在。写错片选号不会报错，只会读回全 0 或全 F（见 `06-devicetree.md` §3） |

拓扑上，一条 SPI 总线是"一主多从，从设备靠独立片选线区分"：

```
                         ┌── CS0# ──► flash@0   (jedec,spi-nor)
   SoC SPI 控制器 ───────┼── CS1# ──► sensor@1
   （SCLK/MOSI/MISO      ├── CS2# ──► display@2
     三根线共享）        └── CS3# ──► （空）
```

也就是说：**共享的是时钟和数据线，独占的是片选线**。片选数量 = 能挂几颗芯片，
这个数字在内核里就是 `spi_controller.num_chipselect`（`include/linux/spi/spi.h:525`）。

### §2.2 SPI 和 I²C 最根本的三个差别

这三条差别贯穿整个子系统的设计，值得单独列出来：

| 维度 | I²C | SPI | 对内核代码的影响 |
|-----|-----|-----|---------------|
| **寻址** | 7/10 位地址在总线上传输，可以探测 | 独立片选线，无地址 | i2c 有 `i2c_new_scanned_device()`；spi **没有任何扫描 API**。`spi_new_device()`（`drivers/spi/spi.c:754`）拿到 board_info 就直接建设备 |
| **双工** | 半双工（一个方向一次） | **全双工**（收发同时发生） | `spi_transfer` 同时有 `tx_buf` 和 `rx_buf`（`include/linux/spi/spi.h:1021-1022`），但**只有一个 `len`**（`:1023`）——因为收发字节数在物理上必然相等 |
| **错误反馈** | 每字节有 ACK/NACK | 无 | i2c 传输返回 `-ENXIO` 表示"没这个设备"；spi 的 `spi_sync()` 返回 0 只表示"时钟摇完了"，**不表示对面听懂了** |

第三条最容易让人栽跟头：`spi_sync()` 返回 0 之后 `rx_buf` 里是全 0xFF，
最常见原因是片选接错或者芯片没上电——而这在 SPI 上**没有任何内核侧的错误码**
可以告诉你。

### §2.3 位与时序：CPOL / CPHA 的四种模式

SPI 唯一真正需要配置的时序参数只有两位：

- **CPOL**（clock polarity）：空闲时 SCLK 是低（0）还是高（1）
- **CPHA**（clock phase）：在时钟的**第一个**边沿采样（0）还是**第二个**边沿采样（1）

内核定义（`include/uapi/linux/spi/spi.h:7-14`）：

```c
#define SPI_CPHA        _BITUL(0)   /* clock phase    */   /* :7  */
#define SPI_CPOL        _BITUL(1)   /* clock polarity */   /* :8  */

#define SPI_MODE_0      (0|0)                             /* :10 */
#define SPI_MODE_1      (0|SPI_CPHA)                      /* :11 */
#define SPI_MODE_2      (SPI_CPOL|0)                      /* :12 */
#define SPI_MODE_3      (SPI_CPOL|SPI_CPHA)               /* :13 */
#define SPI_MODE_X_MASK (SPI_CPOL|SPI_CPHA)               /* :14 */
```

波形（以 MODE 0 为例，`↑` = 采样点，数据在下降沿变化、上升沿采样）：

```
   MODE 0  (CPOL=0, CPHA=0)  —— 最常见
   CS#   ‾‾‾\_________________________________/‾‾‾
   SCLK  ______/‾\__/‾\__/‾\__/‾\__/‾\__/‾\__/‾\__/‾\______
                ↑    ↑    ↑    ↑    ↑    ↑    ↑    ↑
   MOSI  ────< b7 >< b6 >< b5 >< b4 >< b3 >< b2 >< b1 >< b0 >──
             （数据在时钟低电平期间变化，在上升沿被采样）

   MODE 3  (CPOL=1, CPHA=1)  —— 第二常见
   CS#   ‾‾‾\_________________________________/‾‾‾
   SCLK  ‾‾‾‾‾‾\__/‾\__/‾\__/‾\__/‾\__/‾\__/‾\__/‾‾‾‾‾‾
                  ↑    ↑    ↑    ↑    ↑    ↑    ↑
   MOSI  ────< b7 >< b6 >< b5 >< b4 >< b3 >< b2 >< b1 >──
             （空闲时钟为高，数据在上升沿变化、下降沿采样）
```

三条实用规律：

1. **MODE 0 和 MODE 3 覆盖了绝大多数芯片**。两者的采样边沿相对于数据变化沿
   是一样的（都是"数据变完再采"），只是空闲电平相反。
2. **CPHA 比 CPOL 危险**。CPOL 接错通常整个字节全错、一眼能看出来；
   CPHA 接错会得到"移位一位"的数据，看起来像是芯片型号不对。
3. bit-bang 驱动把这四种模式写成了四个独立的内联函数，是最直观的教材：
   `bitbang_txrx_be_cpha0()` / `_be_cpha1()` / `_le_cpha0()` / `_le_cpha1()`，
   分别在 `drivers/spi/spi-bitbang-txrx.h:48-78`、`:80-110`、`:112-143`、`:145-176`。
   想彻底搞明白 CPOL/CPHA，读这 130 行比读任何时序图都快。

### §2.4 三个完整事务的波形

**(a) 纯写（比如给 flash 发 write-enable 命令 0x06）**

```
   CS#   ‾‾‾\_______________________/‾‾‾
   SCLK  ______/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_/‾\______
   MOSI  ────<0><0><0><0><0><1><1><0>────      = 0x06
   MISO  ──────（忽略，但线上仍然有数据被摇出来）──
```

内核侧：一个 `spi_transfer`，`tx_buf` 指向 `{0x06}`，`rx_buf = NULL`，`len = 1`。
`rx_buf == NULL` 时核心会替你准备一块 dummy 接收缓冲——见
`__spi_map_msg()` 里对 `ctlr->dummy_rx` 的处理（`drivers/spi/spi.c:1137`），
以及 `spi_map_msg()` 中 `SPI_CONTROLLER_MUST_RX` 的分支（`drivers/spi/spi.c:1300`）。

**(b) 先写命令再读数据（flash 读 JEDEC ID：0x9F + 3 字节）**

```
   CS#   ‾‾‾\_________________________________________/‾‾‾
   SCLK  ___/‾\_/‾\ ... /‾\_/‾\ ... /‾\_/‾\ ... /‾\____
   MOSI  ───<   0x9F   ><  don't care （通常发 0x00 或 0xFF） >───
   MISO  ───<  忽略    ><     0xEF     0x40     0x18       >───
              ↑ 第 1 个 transfer      ↑ 第 2 个 transfer
              片选在两个 transfer 之间【不抬起】
```

内核侧：**一个 `spi_message` + 两个 `spi_transfer`**。关键点是片选在整个
message 期间保持有效——这不是巧合，是 `spi_transfer_one_message()` 的语义：
它在 message 开头调一次 `spi_set_cs(msg->spi, !xfer->cs_off, false)`
（`drivers/spi/spi.c:1517`），然后逐 transfer 循环，只有 `xfer->cs_change`
被置位时才在中间翻片选（`:1585-1599`），最后统一放开（`:1605-1606`）。

这个模式太常见，核心直接提供了封装：`spi_write_then_read()`
（`drivers/spi/spi.c:4371`）。

**(c) 全双工（同时收发，比如 ADC/DAC 或者 SPI slave 场景）**

```
   CS#   ‾‾‾\_______________________/‾‾‾
   SCLK  ______/‾\_/‾\_/‾\_/‾\______
   MOSI  ────< 主机要发的 4 字节  >────
   MISO  ────< 从机同时回的 4 字节>────
              ↑ 同一段时钟，两个方向同时有效
```

内核侧：一个 `spi_transfer`，`tx_buf` 和 `rx_buf` 都非 NULL，**`len` 只有一个**。
这就是 §2.2 那张表第二行的直接体现。

### §2.5 片选空间：native CS 和 GPIO CS

片选号在内核里是 `spi_device.chip_select`，类型 **`u8`**
（`include/linux/spi/spi.h:181`）。它的值来自 DT 子节点的 `reg` 属性。

一个控制器可以用两种方式产生片选，而且**可以混用**：

| 方式 | 谁驱动引脚 | 内核字段 | 典型场景 |
|-----|----------|---------|---------|
| **native CS** | 控制器硬件自己 | `ctlr->set_cs` 回调（`include/linux/spi/spi.h:688`） | SoC 自带的 2~4 条 CS 线 |
| **GPIO CS** | GPIO 子系统 | `spi_device.cs_gpiod`（`include/linux/spi/spi.h:202`）+ `ctlr->cs_gpiods`（`:699`） | 芯片数超过硬件 CS 数，或者硬件 CS 的时序不合用 |

两者的选择逻辑在 `spi_set_cs()`（`drivers/spi/spi.c:975-1031`）里，读这 57 行
能看清全部四种组合：

1. 有 GPIO 描述符 → `gpiod_set_value_cansleep()`（`:1015`），极性由 GPIO 子系统处理；
2. 有 GPIO 且控制器还设了 `SPI_MASTER_GPIO_SS` → **GPIO 和 native CS 都拉**（`:1018-1020`）；
3. 没有 GPIO 但有 `set_cs` → 只走 native（`:1021-1022`）；
4. 两者都没有 → 什么都不做（`SPI_NO_CS` 场景）。

> **一个反直觉的点**：`spi_set_cs()` 的第一件事是"如果状态没变就直接返回"
> （`:983-986`），判据是 `ctlr->last_cs` 和 `ctlr->last_cs_mode_high` 两个缓存字段
> （`include/linux/spi/spi.h:665-666`）。所以控制器驱动的 `set_cs` 回调**不一定
> 每次传输都会被调到**。调试"片选没动"时先看这里。

### §2.6 位宽与字序

- **位宽**：`spi_transfer.bits_per_word`（`include/linux/spi/spi.h:1038`），
  为 0 时回退到 `spi_device.bits_per_word`（`:182`），后者默认 8。
  控制器用位掩码声明自己支持哪些位宽：`bits_per_word_mask`（`:539`），
  配套宏 `SPI_BPW_MASK(bits)`（`:540`）和 `SPI_BPW_RANGE_MASK(min,max)`（`:541`）。
  校验在 `__spi_validate_bits_per_word()`（`drivers/spi/spi.c:3637`）。
- **字序**：`SPI_LSB_FIRST`（`include/uapi/linux/spi/spi.h:17`）。注意它是
  **每个字内部**的位序，不是字节数组的顺序——多字节数值的大小端要靠驱动自己
  处理（`spi_w8r16be()` 就是干这个的，`include/linux/spi/spi.h:1448`）。

### §2.7 多线扩展与 3-wire

SPI 后来长出了两个方向的变体，内核用模式位表达（`include/uapi/linux/spi/spi.h`）：

**加宽**（给 flash 用，把 MOSI/MISO 复用成 2/4/8 根双向数据线）：

| 模式位 | 行号 | 含义 |
|-------|------|------|
| `SPI_TX_DUAL` / `SPI_RX_DUAL` | `:22` / `:24` | 2 线 |
| `SPI_TX_QUAD` / `SPI_RX_QUAD` | `:23` / `:25` | 4 线 |
| `SPI_TX_OCTAL` / `SPI_RX_OCTAL` | `:27` / `:28` | 8 线 |

per-transfer 侧对应 `spi_transfer.tx_nbits` / `rx_nbits`
（`include/linux/spi/spi.h:1033-1034`），取值宏 `SPI_NBITS_SINGLE/DUAL/QUAD`
（`:1035-1037`）。注意 **`spi_transfer` 里没有 `SPI_NBITS_OCTAL`**——8 线只能
通过 `spi_device.mode` 表达，不能 per-transfer 切换。

**减线**（给引脚紧张的板子用）：

| 模式位 | 行号 | 含义 |
|-------|------|------|
| `SPI_3WIRE` | `:18` | SI/SO 共用一根线，半双工 |
| `SPI_3WIRE_HIZ` | `:29` | 3-wire 且方向切换时插入高阻转向 |

3-wire 的实现在 bit-bang 上最清楚：`spi_gpio_set_direction()`
（`drivers/spi/spi-gpio.c:263-299`）——**只有在 `SPI_3WIRE` 下才会把 MOSI 切成
输入**，注释里写明了为什么不能无条件切（会让引脚悬空）。

### §2.8 没有流控、没有应答：三个后果

1. **超时只能靠软件**。核心的 `spi_transfer_wait()`（`drivers/spi/spi.c:1352`）
   按传输长度和速率算一个超时值，超了就报 `-ETIMEDOUT` 并累加 `timedout` 统计
   （`drivers/spi/spi.c:183`）。
2. **字间延时靠软件插**。`spi_transfer.word_delay`（`include/linux/spi/spi.h:1041`），
   单位由 `struct spi_delay`（`:112-115`）表达：微秒 / 纳秒 / **SCK 周期数**。
3. **传输失败时的硬件状态要驱动自己收拾**。核心给了 `handle_err` 回调
   （`include/linux/spi/spi.h:691`），在 message 出错时调一次
   （`drivers/spi/spi.c:1611-1612`）。`drivers/spi/` 里只有 11 个驱动实现了它——
   典型实现见 `rockchip_spi_handle_err()`（`drivers/spi/spi-rockchip.c:273-292`）：
   关芯片、掩中断、终止 DMA。

### §2.9 速率

SPI 没有规范定义的速率档位（对比 I²C 有 100k/400k/1M/3.4M 这些标准档）。
它是纯粹的"能跑多快看双方"，所以内核里全是上下界而不是枚举：

| 层次 | 字段 | 来源 |
|-----|------|------|
| 控制器能力 | `ctlr->min_speed_hz` / `max_speed_hz`（`include/linux/spi/spi.h:544-545`） | 驱动 probe 时按时钟树算 |
| 芯片能力 | `spi_device.max_speed_hz`（`:180`） | DT 的 `spi-max-frequency` |
| 单次传输 | `spi_transfer.speed_hz`（`:1042`） | 驱动运行时指定 |
| 实际达成 | `spi_transfer.effective_speed_hz`（`:1044`） | 控制器驱动**回填**，除法取整后的真实值 |

`effective_speed_hz` 是个容易被忽略的宝藏：分频器只能整数分频，所以你要 10 MHz
未必真给你 10 MHz。取整逻辑的典型实现见 `rockchip_spi_config()`
（`drivers/spi/spi-rockchip.c:604`），它只能产生**偶数**分频。

### §2.10 与相近总线的对比

| 维度 | SPI | I²C | 1-Wire | (Q)SPI-NOR 专用控制器 |
|-----|-----|-----|--------|---------------------|
| 线数 | 3+N（N = 片选数） | 2 | 1 | 4~8 + CS |
| 双工 | 全双工 | 半双工 | 半双工 | 半双工（命令/数据分相） |
| 寻址 | 片选线 | 总线地址 | ROM ID | 片选线 |
| 可探测 | ✗ | ✓ | ✓ | ✗ |
| 速率 | 无上限规定，实际 1~100 MHz | 100k~3.4M | ~15k | 到 200 MHz |
| 内核抽象 | `spi_controller` + `spi_device` | `i2c_adapter` + `i2c_client` | `w1_master` | 仍是 `spi_controller`，但走 `mem_ops`（见 `04-extensions.md` §2） |
| 总线恢复 | 无（推挽） | 有（`bus_recovery_info`） | 有（复位脉冲） | 无 |

### §2.11 arm64 SoC 上的实际形态

在一颗典型 arm64 SoC 上，SPI 相关的东西长这样：

```
   ┌─────────────────────── SoC ───────────────────────┐
   │                                                    │
   │  CPU ── AXI/AHB ──┬── SPI0 控制器 ── pinmux ──┐    │
   │                   │   （寄存器 + FIFO）        │    │
   │                   ├── SPI1 控制器 ─────────┐  │    │
   │                   ├── QSPI/SFC 控制器 ──┐  │  │    │
   │                   ├── DMA 控制器  ◄─────┼──┼──┤    │
   │                   ├── CRU（时钟）  ◄────┼──┼──┤    │
   │                   └── GIC（中断）  ◄────┼──┼──┤    │
   └──────────────────────────────────────────┼──┼──┼───┘
                                              │  │  │
                        ┌─────────────────────┘  │  └── SCLK/MOSI/MISO/CS
                        │                        │
                     SPI NOR flash          触摸屏 / 传感器 / MCU
                    (jedec,spi-nor)
```

四个"故障源"和它们对应的排查入口：

| 现象 | 常见根因 | 去哪查 |
|-----|---------|-------|
| 设备节点根本没出现在 `/sys/bus/spi/devices/` | DT 子节点缺 `compatible` 或 `reg`，或者控制器 `status = "disabled"` | `06-devicetree.md` §4.2 |
| 出现了但读回全 0xFF | 片选号错、pinmux 没配、芯片没上电 | `07-debug.md` §7.1 |
| 数据移位一位 | CPHA 错 | §2.3 第 2 条 |
| 速率上不去 | `spi-max-frequency` 太小，或分频取整（看 `effective_speed_hz`） | `07-debug.md` §7.3 |

**in-tree arm64 真实节点**（读者可以直接 diff，不是构造的例子）：

`arch/arm64/boot/dts/rockchip/rk3399.dtsi:742-755` 是控制器侧：

```dts
spi1: spi@ff1d0000 {
        compatible = "rockchip,rk3399-spi", "rockchip,rk3066-spi";
        reg = <0x0 0xff1d0000 0x0 0x1000>;
        clocks = <&cru SCLK_SPI1>, <&cru PCLK_SPI1>;
        clock-names = "spiclk", "apb_pclk";
        dmas = <&dmac_peri 12>, <&dmac_peri 13>;
        dma-names = "tx", "rx";
        interrupts = <GIC_SPI 53 IRQ_TYPE_LEVEL_HIGH 0>;
        pinctrl-names = "default";
        pinctrl-0 = <&spi1_clk &spi1_tx &spi1_rx &spi1_cs0>;
        #address-cells = <1>;
        #size-cells = <0>;
        status = "disabled";
};
```

`arch/arm64/boot/dts/rockchip/rk3399-gru.dtsi:576-589` 是板级挂芯片：

```dts
&spi1 {
        status = "okay";
        pinctrl-names = "default", "sleep";
        pinctrl-1 = <&spi1_sleep>;

        flash@0 {
                compatible = "jedec,spi-nor";
                reg = <0>;
                spi-max-frequency = <10000000>;
        };
};
```

规模上：`arch/arm64/boot/dts/` 里 `compatible` 行含 `spi` 的有 543 处，
`spi-max-frequency` 出现 275 次，`jedec,spi-nor` 节点 170 个。也就是说
**arm64 上 SPI 最主要的用户是 NOR flash**，这也解释了为什么 `spi-mem`
子框架（`04-extensions.md` §2）值得单独一节。

代表驱动的选取（详细理由见 `99-evidence.md` §2.2）：

| 驱动 | 行数 | 为什么选它 |
|-----|------|----------|
| `drivers/spi/spi-bitbang.c` + `spi-gpio.c` | 453 + 474 | 最简 PIO 实现，把 CPOL/CPHA 写成了明文 |
| `drivers/spi/spi-rockchip.c` | 1,077 | 全功能：DMA + IRQ + runtime PM + slave 模式，且 arm64 DTS 实际在用 |
| `drivers/spi/spi-mux.c` | 203 | 总线扩展，唯一使用 `must_async` 的驱动 |

---

## §3 概念模型

> 官方原文见 `Documentation/driver-api/spi.rst`（"Controller Driver" /
> "Protocol Driver" 这组术语出自那里）和 `Documentation/spi/spi-summary.rst`。
> 本节在它们之上补了两件官方文档没画的事：**两条总线的连接点**，
> 以及 **`spi_controller` 其实不在 SPI 总线上**（见 `02-datastructures.md` §1.0）。

SPI 子系统只有三个角色，但它们的关系容易搞混，所以先把图画清楚：

```
     ┌──────────────── platform 总线 ──────────────────┐
     │                                                  │
     │   platform_device "ff1d0000.spi"                 │
     │        ↑ 由 of_platform_populate() 创建           │
     │        │                                          │
     │   platform_driver "rockchip-spi"  ← probe() ──────┼──┐
     └──────────────────────────────────────────────────┘  │
                                                            │
                       spi_alloc_master() + spi_register_controller()
                                                            │
     ┌──────────────── spi 总线（spi_bus_type）────────────┐ │
     │                                                     │ │
     │   struct spi_controller  ────────────────────────── ┼─┘
     │      · dev.parent = platform_device 的 dev  ← 缝合点 │
     │      · 挂在 spi_master_class 上，不是 spi_bus_type    │
     │      · 提供 transfer_one / set_cs / can_dma 回调      │
     │                                                      │
     │        │ of_register_spi_devices() 扫 DT 子节点        │
     │        ▼                                             │
     │   struct spi_device "spi1.0"   ← 注册在 spi_bus_type │
     │      · dev.parent = &ctlr->dev                       │
     │      · modalias = "jedec,spi-nor"（去掉厂商前缀）      │
     │        │                                             │
     │        │ spi_bus_type.match → spi_match_device()      │
     │        ▼                                             │
     │   struct spi_driver "spi-nor"  ← 真正的 device↔driver │
     │                                                      │
     └──────────────────────────────────────────────────────┘
```

三条必须记住的事实：

1. **只有 `spi_device` ↔ `spi_driver` 是设备模型的 device↔driver 匹配。**
   匹配函数 `spi_match_device()` 在 `drivers/spi/spi.c:375`，
   总线定义 `spi_bus_type` 在 `drivers/spi/spi.c:471-479`。
2. **`spi_controller` 不注册在 `spi_bus_type` 上，它注册在一个 class 上**
   （`spi_master_class`，或者 slave 模式下的 `spi_slave_class`；
   两个 class 的注册见 `spi_init()`，`drivers/spi/spi.c:4596-4604`）。
   所以 `spi_controller` **永远不会被任何 driver 绑定**。
3. **控制器有双重身份**：对 SoC 它是 platform 设备（被 platform_driver 探测），
   对芯片它是总线接口（提供 `transfer_one`）。两个身份靠
   `dev.parent`（`include/linux/spi/spi.h:510` 的 `struct device dev`
   在 `__spi_alloc_controller()` 里被赋 parent，`drivers/spi/spi.c:2902`）缝在一起。

> 这三条里第 2 条最容易搞错，`02-datastructures.md` §1.0 会用对比表和
> 否定性证据把它彻底钉住，`08-feynman.md` §2 会用生活类比再讲一遍。

角色一览：

| 角色 | 结构体 | 谁写它 | 一句话职责 |
|-----|-------|-------|----------|
| 控制器 | `spi_controller` | SoC 厂商 / 内核维护者 | 把一段字节按给定模式和速率摇到线上 |
| 设备 | `spi_device` | **没人写**，核心从 DT 生成 | 描述"某颗芯片挂在哪条总线的哪个片选上，用什么参数" |
| 协议驱动 | `spi_driver` | 外设厂商 / 驱动作者 | 把字节解释成寄存器读写、图像数据、传感器采样…… |

---

## §4 核心数据结构速览

> 完整字段表、flags 子表、ASCII 关系图见 `02-datastructures.md`。这里只留复习用的骨架。

| 结构体 | 定义位置 | 一句话 | 详见 |
|-------|---------|-------|------|
| `spi_controller` | `include/linux/spi/spi.h:509-729` | 一条 SPI 总线的全部能力和状态（约 60 个字段 + 20 个回调） | `02` §2.1 |
| `spi_device` | `include/linux/spi/spi.h:176-220` | 一颗芯片：片选号 + 模式 + 速率 + 位宽 | `02` §2.2 |
| `spi_driver` | `include/linux/spi/spi.h:313-320` | `probe`/`remove`/`shutdown` + id 表 | `02` §2.3 |
| `spi_message` | `include/linux/spi/spi.h:1090-1127` | 一次**原子**事务：一串 transfer + 完成回调 | `02` §2.4 |
| `spi_transfer` | `include/linux/spi/spi.h:1015-1057` | 一段连续时钟：tx/rx 缓冲 + 长度 + 参数覆盖 | `02` §2.5 |
| `spi_delay` | `include/linux/spi/spi.h:112-118` | 一个带单位的延时（μs / ns / SCK 周期） | `02` §2.6 |
| `spi_statistics` | `include/linux/spi/spi.h:63-83` | per-CPU 统计，直接映射成 sysfs 文件 | `02` §5.2 |
| `spi_res` | `include/linux/spi/spi.h:879` | 挂在 message 上的"用完自动释放"资源 | `03` §B4 |
| `spi_replaced_transfers` | `include/linux/spi/spi.h:1282` | transfer 拆分时保存被替换的原件 | `03` §B5 |
| `spi_board_info` | `include/linux/spi/spi.h:1509` | 非 DT 平台的静态设备声明（arm64 上基本不用） | `02` §7 |

### 命名规则（看到名字就知道是什么）

| 前缀 | 含义 | 例子 |
|-----|------|------|
| `spi_` | 面向协议驱动的公共 API | `spi_sync()`、`spi_setup()` |
| `__spi_` | 内部实现，通常调用者已持锁 | `__spi_sync()`、`__spi_async()`、`__spi_validate()` |
| `spi_controller_` | 面向控制器驱动 | `spi_controller_suspend()`、`spi_controller_get_devdata()` |
| `spi_mem_` | `spi-mem` 子框架（flash 专用） | `spi_mem_exec_op()` |
| `spi_master_*` / `spi_slave_*` | **旧名字**，语义完全等于 `spi_controller_*`；但实现形式有**四**种，见 `01-api.md` §1.1 | `spi_master_get()` 是 `#define`（`spi.h:1604`）；`spi_alloc_master()` 是独立的 `static inline`（`:786`），**不存在** `spi_alloc_controller()` 这个名字，底层叫 `__spi_alloc_controller()` |
| `SPI_` | 用户可见的模式位 / 能力位 | `SPI_CPOL`、`SPI_BPW_MASK` |
| `SPI_CONTROLLER_` | 控制器约束标志 | `SPI_CONTROLLER_MUST_TX` |

> `spi_master` ↔ `spi_controller` 的命名史是本子系统第一大迷惑源。
> `#define` 兼容层在 `include/linux/spi/spi.h:1592-1612`，但它**只覆盖一部分**——
> alloc 家族、`spi_device.master` 字段、`slave`/`target` 的 `union` 都不在里面，
> 四种形式的完整拆解见 `01-api.md` §1.1，
> 专门的解释在 `08-feynman.md` §1。

### 锁摘要

| 锁 | 字段 | 保护什么 | 谁拿 |
|---|------|---------|------|
| `io_mutex` | `include/linux/spi/spi.h:576` | **实际的硬件访问**，一次只有一个 message 在跑 | `__spi_pump_messages()` `drivers/spi/spi.c:1772`、`__spi_transfer_message_noqueue()` `:4139` |
| `add_lock` | `:579` | 防止同一个片选被注册两次 | `spi_add_device()` `drivers/spi/spi.c:698` |
| `bus_lock_mutex` | `:583` | `spi_sync()` 的串行化 + `spi_bus_lock()` 独占 | `spi_sync()` `drivers/spi/spi.c:4262`、`spi_bus_lock()` `:4307` |
| `bus_lock_spinlock` | `:582` | 保护 `bus_lock_flag`，可在原子上下文取 | `spi_async()` `drivers/spi/spi.c:4070` |
| `queue_lock` | `:654` | 保护 `ctlr->queue` 链表和 `cur_msg` | `__spi_pump_messages()` `drivers/spi/spi.c:1775` |
| `board_lock`（全局） | `drivers/spi/spi.c:557` | `board_list` 和 `spi_controller_list` | `spi_register_controller()` |

锁序：`bus_lock_mutex` → `io_mutex` → `queue_lock`。详见 `03-internals.md` §B1。

---

## §5 核心机制速览

> 每节标题后的 `(03 §Ax/§Bx)` 指向 `03-internals.md` 的对应小节。
> 排列顺序和 03 完全一致：**Part A 是时间线，Part B 是横切机制**。

### 时间线上的机制（Part A）

1. **子系统初始化 (03 §A1)** — `spi_init()`（`drivers/spi/spi.c:4582`）在
   `postcore_initcall` 级别（`:4632`）注册 `spi_bus_type` + 1~2 个 class +
   OF/ACPI 重配置通知器。比 `subsys_initcall` 更早，因为控制器驱动可能在
   `subsys_initcall` 就要注册。
2. **控制器注册 (03 §A2)** — `spi_register_controller()`（`drivers/spi/spi.c:3099`）：
   分配 `bus_num`（IDR）→ 校验回调组合（`spi_controller_check_ops()` `:3056`）→
   拿 GPIO 片选（`spi_get_gpio_descs()` `:2985`）→ 建消息泵队列
   （`spi_controller_initialize_queue()` `:2192`）→ `device_add` → 扫 DT 子节点。
3. **设备枚举 (03 §A3)** — `of_register_spi_devices()`（`drivers/spi/spi.c:2386`）
   遍历子节点 → `of_register_spi_device()`（`:2333`）→ `of_spi_parse_dt()`（`:2240`）
   把 DT 属性翻译成 `spi_device.mode` 等字段 → `spi_add_device()`（`:698`）。
4. **驱动绑定 (03 §A4)** — `spi_match_device()`（`drivers/spi/spi.c:375`）四级匹配：
   `driver_override` → DT `of_match_device` → ACPI → `spi_device_id` 表。
   然后 `spi_probe()`（`:410`）先 `spi_setup()` 再调驱动的 `probe`。
5. **一次传输 (03 §A5)** — 见 §6 的完整链路。
6. **拆除 (03 §A6)** — `spi_unregister_controller()`（`drivers/spi/spi.c:3297`）
   先对每个子设备调 `__unregister()`（`:3279`）→ `spi_destroy_queue()`（`:2133`）→
   从 IDR 摘掉 bus_num。**注意它和 `devm_spi_register_controller()`
   （`:3257`）的自动拆除路径是两条不同的代码**。

### 横切机制（Part B）

7. **四把锁与锁序 (03 §B1)** — 见 §4 锁摘要。
8. **消息泵与 `spi_sync` 快路径 (03 §B2)** — 默认所有 message 都进
   `ctlr->queue` 由 kthread 泵出（`spi_pump_messages()` `drivers/spi/spi.c:1853`）；
   但 `__spi_sync()` 发现队列空且 `!ctlr->must_async` 时**直接在调用者线程上跑完**
   （`:4204-4216`），省掉一次上下文切换。这条快路径是 6.1 上性能的主要来源。
9. **DMA 映射 (03 §B3)** — `can_dma` 返回真的 transfer 才映射
   （`spi_map_msg()` `drivers/spi/spi.c:1300`）；vmalloc 缓冲区走 sg 表分页。
   `CONFIG_HAS_DMA=n` 时整组函数退化成空实现（`:1258-1279`）。
10. **`spi_res` 资源生命周期 (03 §B4)** — `spi_res_alloc()`（`drivers/spi/spi.c:911`）
    挂上，`spi_res_release()`（`:959`）在 message 完成时统一释放。
11. **超长 transfer 拆分 (03 §B5)** — `spi_split_transfers_maxsize()`
    （`drivers/spi/spi.c:3603`）在 `__spi_pump_transfer_message()` 里被无条件调用
    （`:1683-1685`），拆出来的片段用 `spi_res` 管理。
12. **runtime PM (03 §B6)** — `auto_runtime_pm` 为真时，核心在泵出第一个 message
    前 `pm_runtime_get_sync()`（`:1648`），空闲时 `spi_idle_runtime_pm()`（`:1633`）。
13. **tracepoint (03 §B7)** — 9 个事件，定义在 `include/trace/events/spi.h`。
    **没有一个带 `TP_CONDITION`**，所以过滤只能靠 filter 表达式。详见 `07-debug.md` §2。
14. **统计 (03 §B8)** — per-CPU `spi_statistics`，17 个长度分布桶
    （`drivers/spi/spi.c:197-213`），同时挂在控制器和设备两处 sysfs。
15. **PREEMPT_RT (03 §B9)** — 本树是 `-rt58`。**`drivers/spi/spi.c` 确实被 RT 补丁
    改了，但只改了 4 行，且全在统计路径上**——`spi_pcpu_stats_totalize()`
    （`drivers/spi/spi.c:120-137`）里两处 `u64_stats_fetch_*_irq` 去掉了 `_irq`
    后缀（`:130`、`:133`）。传输路径是纯净上游代码。这一点很重要，因为它决定了
    "本文档描述的传输行为对非 RT 内核同样成立"。完整取证见 `99-evidence.md` §1.2。
16. **消息泵的 RT 优先级 (03 §B10)** — 控制器可以要求它的 kthread 跑 RT 优先级：
    `spi_set_thread_rt()`（`drivers/spi/spi.c:1960`），由 `ctlr->rt`
    （`include/linux/spi/spi.h:662`）或者 `spi_device.rt`（`:183`）触发。

---

## §6 端到端调用路径

以最常见的"协议驱动调 `spi_sync()` 读 4 个字节"为例，走一遍从 API 到寄存器：

```
 协议驱动
   │  spi_sync(spi, &msg)                             drivers/spi/spi.c:4258
   │    └─ mutex_lock(&ctlr->bus_lock_mutex)           :4262   ◄── 【锁 1】
   ▼
 __spi_sync()                                         drivers/spi/spi.c:4178
   │  ├─ __spi_check_suspended()  → -ESHUTDOWN         :4184-4187
   │  ├─ __spi_validate()                              :4189 → :3850
   │  │     · 检查 message 非空、len 合法
   │  │     · 检查 bits_per_word 在 bits_per_word_mask 内   :3637
   │  │     · 补齐 speed_hz / bits_per_word 默认值
   │  │     · 检查 tx/rx 与控制器 NO_TX/NO_RX/MUST_TX 约束
   │  ├─ 统计 spi_sync++                               :4195-4196
   │  │
   │  ├─【快路径】queue_empty && !must_async ?          :4204
   │  │     ├─ trace_spi_message_submit                :4208   ◄── 【trace 1】
   │  │     ├─ 统计 spi_sync_immediate++               :4210-4211
   │  │     └─ __spi_transfer_message_noqueue()        :4213 → :4134
   │  │            └─ mutex_lock(&ctlr->io_mutex)      :4139   ◄── 【锁 2】
   │  │
   │  └─【慢路径】进异步队列后等 completion             :4224-4230
   │         └─ spi_async_locked() → __spi_async()     :4114 → :4000
   │                └─ ctlr->transfer() = spi_queued_transfer()   :2187
   │                       └─ __spi_queued_transfer()  :2155
   │                              · spin_lock(&queue_lock)  ◄── 【锁 3】
   │                              · list_add_tail(&ctlr->queue)
   │                              · kthread_queue_work(pump_messages)
   │                                     ↓（切到 kthread 上下文）
   │                                spi_pump_messages()  :1853
   │                                     └─ __spi_pump_messages(ctlr, true)  :1764
   │                                            └─ mutex_lock(&io_mutex)  :1772 ◄─【锁 2】
   ▼
 __spi_pump_transfer_message()                         drivers/spi/spi.c:1641
   │  ├─ pm_runtime_get_sync(ctlr->dev.parent)         :1648   （auto_runtime_pm）
   │  ├─ trace_spi_controller_busy                    :1662   ◄── 【trace 2】
   │  ├─ ctlr->prepare_transfer_hardware()            :1665   （可选回调：开时钟）
   │  ├─ trace_spi_message_start                      :1681   ◄── 【trace 3】
   │  ├─ spi_split_transfers_maxsize()                 :1683 → :3603
   │  ├─ ctlr->prepare_message()                      :1693   （可选回调）
   │  ├─ spi_map_msg()                                :1704 → :1300
   │  │     └─ can_dma() 为真 → dma_map_sg()          :1137
   │  └─ ctlr->transfer_one_message()                 :1736
   ▼
 spi_transfer_one_message()（核心的通用实现）           drivers/spi/spi.c:1507
   │  ├─ spi_set_cs(spi, true, false)                 :1517 → :975
   │  │     ├─ trace_spi_set_cs                       :988    ◄── 【trace 4】
   │  │     ├─ cs_hold / cs_setup 延时                :994 / :1027
   │  │     └─ gpiod_set_value_cansleep() 或 ctlr->set_cs()   :1015 / :1022
   │  │
   │  └─ for each spi_transfer:                       :1522
   │        ├─ trace_spi_transfer_start               :1523   ◄── 【trace 5】
   │        ├─ 统计：字节数 + 17 桶长度分布            :1525-1526
   │        ├─ spi_dma_sync_for_device()              :1537
   │        ├─ ctlr->transfer_one(ctlr, spi, xfer)    :1538   ◄══ 控制器驱动入口
   │        │     · 返回 0  = 已经同步传完
   │        │     · 返回 >0 = 传输进行中，核心去等 xfer_completion
   │        │     · 返回 <0 = 失败（可能触发 DMA→PIO 回退，:1542-1548）
   │        ├─ ret > 0 → spi_transfer_wait()          :1560 → :1352（超时保护）
   │        ├─ trace_spi_transfer_stop                :1578   ◄── 【trace 6】
   │        ├─ spi_transfer_delay_exec()              :1583   （transfer 后延时）
   │        └─ cs_change 处理：中途翻片选              :1585-1599
   │
   ├─ spi_set_cs(spi, false, false)                   :1606   （放开片选）
   ├─ ctlr->handle_err()（仅出错时）                   :1611-1612
   └─ spi_finalize_current_message()                  :1614 → :2026
          ├─ ctlr->unprepare_message()
          ├─ spi_res_release()                        → :959
          ├─ trace_spi_message_done                           ◄── 【trace 7】
          └─ msg->complete(msg->context)  → 唤醒 spi_sync 的等待者

 控制器驱动侧（以 spi-rockchip 为例）
   rockchip_spi_transfer_one()                        drivers/spi/spi-rockchip.c:666
     ├─ rockchip_spi_config()                         :524   （写 CTRLR0/CTRLR1/BAUDR）
     ├─ can_dma → rockchip_spi_prepare_dma()          :445   （返回 1 = 进行中）
     └─ 否则   → rockchip_spi_prepare_irq()           :375   （返回 1 = 进行中）
            中断到来 → rockchip_spi_isr()             :347
                   └─ spi_finalize_current_transfer() → drivers/spi/spi.c:1627
                          └─ complete(&ctlr->xfer_completion)  ← 唤醒 :1560 的等待
```

三个观察点，复习时优先记住：

1. **锁在三层加**：`bus_lock_mutex`（API 层，串行化 `spi_sync`）→
   `io_mutex`（硬件访问层）→ `queue_lock`（链表层，可在原子上下文取）。
2. **重试只在一个地方做**：`spi_transfer_one_message()` 里的 DMA→PIO 回退
   （`drivers/spi/spi.c:1536` 的 `fallback_pio:` 标签 + `:1542-1548`）。
   核心**不会**因为传输失败而重发整个 message——这一点和 I²C 不同。
3. **tracepoint 埋在 7 个位置**（上图标注），全部在核心里，
   控制器驱动不需要也不应该自己埋。

---

## §7 与内核其它子系统的关系

| 子系统 | 关系 | 关键位置 |
|-------|------|---------|
| **驱动模型** | `spi_bus_type` 是一条真正的总线；`spi_controller` 走 class 而非 bus | `drivers/spi/spi.c:471-479`、`:4592-4604` |
| **platform 总线** | 控制器几乎总是 platform 设备，靠 `dev.parent` 与 SPI 总线缝合 | `__spi_alloc_controller()` `drivers/spi/spi.c:2902` |
| **设备树 (OF)** | 子节点 → `spi_device`；属性翻译在 `of_spi_parse_dt()` | `drivers/spi/spi.c:2240-2331`；详见 `06-devicetree.md` |
| **OF_DYNAMIC / overlay** | `spi_of_notifier` 响应运行时节点增删 | `of_spi_notify()` `drivers/spi/spi.c:4452`；`CONFIG_SPI_DYNAMIC` `drivers/spi/Kconfig:1194` |
| **ACPI** | 有完整的枚举路径，**arm64 上本文档集不覆盖** | `drivers/spi/spi.c:2459-2753`；裁剪声明见 `99-evidence.md` §2.7 |
| **GPIO** | 片选可以由 GPIO 提供；bit-bang 驱动整个都建在 GPIO 上 | `spi_get_gpio_descs()` `drivers/spi/spi.c:2985`；`drivers/spi/spi-gpio.c` |
| **pinctrl** | 控制器驱动自己在 suspend/resume 里切 pin 状态，核心不管 | `rockchip_spi_suspend()` `drivers/spi/spi-rockchip.c:974` |
| **clk** | 控制器驱动自己取时钟并据此算 `max_speed_hz`，核心不碰时钟 | `drivers/spi/spi-rockchip.c:874-875` |
| **dmaengine** | 核心提供映射与 sg 表，实际 dmaengine 调用在驱动里 | `spi_map_buf_attrs()` `drivers/spi/spi.c:1034`；`rockchip_spi_prepare_dma()` `drivers/spi/spi-rockchip.c:445` |
| **中断** | 核心完全不申请中断；控制器驱动自己 request_irq | `drivers/spi/spi-rockchip.c:817` |
| **PM（system）** | 核心提供 `spi_controller_suspend/resume()`，驱动在自己的 PM 回调里调 | `drivers/spi/spi.c:3358` / `:3374` |
| **PM（runtime）** | `auto_runtime_pm` 让核心替驱动做 get/put | `drivers/spi/spi.c:1647-1659`、`:1633-1639` |
| **devres** | `devm_spi_alloc_*()` / `devm_spi_register_controller()` | `drivers/spi/spi.c:2957`、`:3257` |
| **ftrace** | 9 个 tracepoint | `include/trace/events/spi.h` |
| **MTD / spi-nor** | 通过 `spi-mem` 子框架消费 SPI；arm64 上最大的用户 | `drivers/spi/spi-mem.c`；`04-extensions.md` §2 |
| **字符设备** | `spidev` 把 SPI 暴露成 `/dev/spidevB.C`，主设备号 153 | `drivers/spi/spidev.c:43`；`04-extensions.md` §4 |
| **PTP / 时间戳** | 核心提供 `spi_take_timestamp_pre/post()`，**全树只有 1 个驱动用** | `drivers/spi/spi.c:1881` / `:1918`；`drivers/spi/spi-fsl-dspi.c:1441` |
| **PREEMPT_RT** | 只影响统计路径的 4 行，传输路径未变；消息泵可跑 RT 优先级 | `drivers/spi/spi.c:120-137`、`:1960` |

---

## §8 本文档集导读

| 篇 | 一句话定位 | 大概什么时候用 |
|---|----------|-------------|
| `00-overview.md` | 复习入口 + 硬件背景 | 现在 / 隔几个月回来 |
| `01-api.md` | 代码地图 + 对外 API 全表 + 错误码 | 「这个函数叫什么」「这个功能在哪个文件」 |
| `02-datastructures.md` | 结构体字段 + 关系图 + 锁 | 「这个字段谁改的」「adapter 和 algorithm 那种关系在 SPI 里对应什么」 |
| `03-internals.md` | 注册/枚举/传输/拆除主流程逐段剖析 | 要改核心，或者要看懂某个函数为什么在这里 |
| `04-extensions.md` | `spi-mem` / slave / mux / spidev / loopback 五个子框架 | 要用 QSPI flash、要做 slave、要挂 mux |
| `05-usage.md` | 四种角色的代码骨架 | 要动手写驱动 |
| `06-devicetree.md` | DTS 每条属性由谁消费 + 配错了怎么定位 | 板子起不来 |
| `07-debug.md` | 编译开关 / tracepoint / sysfs / 故障注入 / 错误码分诊 | 现象有了但不知道从哪查 |
| `08-feynman.md` | 大白话讲清 7 个最难的概念 | 「我理解不了为什么要这样」 |
| `99-evidence.md` | 证据分级 + 自审记录 + 未验证项 | 想知道哪些结论可以直接用 |

### 按角色的阅读路径

**A. 我只是要用一颗 SPI 芯片（写协议驱动）**
`00` §1、§3 → `01` §1~§3（API 表）→ `05` §2（协议驱动骨架）→ `06` §1（DTS 怎么写）
→ 卡住了看 `07` §7.1/§7.2。

**B. 我要写一个 SoC 的 SPI 控制器驱动**
`00` §2（硬件背景，尤其 §2.3/§2.5）→ `02` §2.1（`spi_controller` 全字段）→
`03` Part A §A2 + §A5 → `01` §4（回调约定）→ `05` §3（控制器骨架）→
参考 `drivers/spi/spi-rockchip.c`。

**C. 我要看懂核心 / 要改核心**
`03` 全篇（先读 §0 导航）→ `02` §1.0 破歧义 → `04`（子框架怎么挂进主流程）→
`99` §5（哪些结论是推断而非观察）。

**D. 我要复核这套文档的结论**
`99` 全篇。特别是 §2 证据分级、§3 逐篇取证表、§6.1 三个批量自检脚本的实际输出。

**E. 我隔了几个月回来复习**
只读本篇。§4 + §5 是 02/03 的浓缩版，§6 是一条完整链路，
需要细节时按标注的 `03 §Ax` 下钻。
