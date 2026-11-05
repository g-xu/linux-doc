---
title: 总览
---

# I2C 子系统（一）模块功能介绍

> 代码基线：Linux **6.1.158**（`localversion-rt` = `-rt58`，PREEMPT_RT 补丁树），HEAD `f6e38ae624cf`。
> 本树有 175 个未提交的 RT 相关改动，但**没有一个落在 i2c 子系统**（见 [`99-evidence.md`](99-evidence.md) §1），因此本文档描述的就是纯净的上游 6.1.158 i2c 代码。
> **目标架构：arm64。**凡涉及体系结构的内容一律按 arm64 讲；x86 / powerpc / arm32 的分支最多一句话交代，不展开。ACPI 相关路径（`i2c-core-acpi.c`、OpRegion、DMI/SPD）属于 x86 平台机制，**本文档集不描述**，arm64 的枚举主线是设备树（见 §2.9d）。
> 全套共 9 篇：`00` → `01` → `02` → `03` → `04` → `05` → `06` → `07`，`99` 是取证附录（用于核对每条论断的出处）。按目标分的阅读路径见 §8。

---

## 1. 一句话定位

I2C 子系统是 Linux 内核里**两线串行总线（SCL/SDA）的通用中间层**：它向下把各家控制器（i.MX、Designware、GPIO 模拟、PCA9564……）的差异收拢进一个 `struct i2c_algorithm` 抽象，向上给成千上万个芯片驱动（EEPROM、RTC、touch、PMIC、HDMI DDC、传感器）提供统一的 `i2c_transfer()` / SMBus 调用，并把总线上的每一个芯片包装成 Linux 设备模型里的一个 `struct device`，从而能被 probe、被电源管理、被 sysfs 观察。

它不是驱动某一个硬件的模块，而是**一条总线的框架**。所以它的价值不在"能干什么功能"，而在于"帮你省掉了什么"：地址冲突检测、总线编号分配、锁与并发、SMBus 协议在纯 I²C 控制器上的软件仿真、设备树枚举、多路复用器（mux）的拓扑与锁、总线卡死恢复、tracepoint 观测、用户态字符设备接口。

---

## 2. 硬件与协议基础

**这一节讲的是总线本身，不是内核代码。**但后面大量的设计选择（为什么需要总线恢复、为什么要有 quirks、为什么 SMBus 能被仿真、为什么 mux 存在）都是这些物理约束的直接后果，所以值得先花时间读完。

> 本节的电气参数与时序来自 I²C 总线规范（NXP UM10204）与工程常识，**不是从本树代码读出的**（见 [`99-evidence.md`](99-evidence.md) §5.3）。凡是内核代码里有对应实现的，我都标了 `文件:行号`。

### 2.1 物理层：两根线、开漏、上拉

```
              VDD (1.8V / 3.3V)
               │         │
              ┌┴┐       ┌┴┐
              │ │Rp     │ │Rp        ← 上拉电阻，整条总线只需一对
              └┬┘       └┬┘
   SDA ─────────┼─────────┼──────────────┬──────────────┬────────
   SCL ─────────┼─────────┴──────────────┼──────────────┼────────
                │                        │              │
           ┌────┴────┐              ┌────┴────┐    ┌────┴────┐
           │  SoC    │              │ Slave A │    │ Slave B │
           │ I2C IP  │              │  0x50   │    │  0x68   │
           │(Master) │              │ EEPROM  │    │  RTC    │
           └─────────┘              └─────────┘    └─────────┘

   每个引脚内部都是这样：
             ┌─── 输入缓冲 ──→ 读电平
        SDA ─┤
             └───┤◁── 开漏输出（只能拉低，不能驱动高）
                 ▽ GND
```

**开漏（open-drain）是一切的起点。** 每个器件的引脚只有两种动作：**拉低**（导通到地）或**松手**（高阻）。线上的高电平完全由上拉电阻 Rp 提供。三个直接后果：

1. **天然的"线与"（wired-AND）**：任何一个器件拉低，整条线就是低。多个器件同时说话不会烧毁，只会"低电平赢"。这是仲裁（§2.6）和时钟拉伸（§2.5）能存在的物理基础。
2. **上升沿是 RC 充电，不是驱动**。所以速率上不去的常见原因是 Rp 太大或总线电容 Cb 太大。这也是内核 `struct i2c_timings` 里要有 `scl_rise_ns` / `scl_fall_ns` 字段的原因（`include/linux/i2c.h`，见 [`02-datastructures.md`](02-datastructures.md) §2.4）—— 控制器要靠这些值算分频。
3. **任何一方在错误时机把线按住，总线就死了**，而且**没有任何协议手段能强制复位**（I²C 没有 reset 线）。这就是内核必须有"总线恢复"机制的原因（`i2c_recover_bus()`，见 [`03-internals.md`](03-internals.md) §A7）。

**上拉电阻怎么选**（工程速查，非规范原文）：

| 目标速率 | 典型 Rp（3.3V） | 约束 |
|---|---|---|
| 100 kHz (Standard) | 4.7 kΩ | tr ≤ 1000 ns |
| 400 kHz (Fast) | 2.2 kΩ | tr ≤ 300 ns |
| 1 MHz (Fast+) | 1 kΩ 或需要主动上拉 | tr ≤ 120 ns |

- **下限**由灌电流决定：`Rp(min) ≈ (VDD − VOL_max) / IOL`，IOL 通常 3 mA，VOL_max 0.4 V。
- **上限**由上升时间决定：`Rp(max) ≈ tr / (0.8473 × Cb)`。规范规定 Cb ≤ 400 pF（Standard/Fast），实际每个器件约 10 pF、每 cm PCB 走线约 1 pF。
- **总线越长、挂的芯片越多 → Cb 越大 → Rp 必须越小 → 灌电流越大**。这是 I²C 不能拉长距离、不能挂太多器件的根本原因，也是 mux（[`04-extensions.md`](04-extensions.md) §1）在实际板子上普遍存在的一个动机（除了地址冲突之外）。

**电平域**：VIL ≤ 0.3×VDD，VIH ≥ 0.7×VDD。**同一条总线上不同电压域的器件不能直连**（1.8 V SoC 接 3.3 V 传感器要加电平转换器，通常是双 MOS 管方案）。内核对此完全不可见 —— 电平转换器是纯硬件，在 DT 里也没有节点。**症状是"读回来全 0xff 或全 0x00"，而软件侧看不出任何异常**。

### 2.2 位与时序

```
   START            数据位（8 个）                  ACK        STOP
   ┌──┐
SDA│  └──┐ ┌───┐ ┌───┐     ┌───┐ ┌───┐ ┌───┐ ┌───┐    ┌────┐  ┌──────
   │     └─┘   └─┘   └─────┘   └─┘   └─┘   └─┘   └────┘    └──┘
   │  ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐
SCL└──┘   └─┘   └─┘   └─┘   └─┘   └─┘   └─┘   └─┘   └─┘   └─────────
      ↑     ↑                                          ↑      ↑
      │     └─ SCL 高时 SDA 必须稳定 ── 从设备在此采样    │      │
      └─ SCL 高时 SDA 由高变低 = START                   │      └─ SCL 高时
                                          从设备拉低 SDA = ACK      SDA 由低
                                          松手不动     = NAK        变高 = STOP
```

**唯一需要记住的规则：SCL 为高时 SDA 不许变化 —— 除了 START 和 STOP。**

- **START**：SCL 高时 SDA 下降。占用总线。
- **STOP**：SCL 高时 SDA 上升。释放总线。
- **数据位**：SCL 低时改变 SDA，SCL 高时保持。MSB 先发。
- **ACK/NAK**：每 8 位后有第 9 个时钟。接收方拉低 SDA = ACK（收到了）；不动 = NAK。
  - 主设备发地址后收到 NAK → 这个地址上没有器件 → 内核报 **`-ENXIO`**。
  - 主设备读数据时，最后一个字节要主动回 NAK 告诉从设备"够了"（`algos/i2c-algo-bit.c:400` 的 `acknak()` 就是干这个的）。
- **repeated-START**：不发 STOP 直接再发一次 START。**总线不释放**，别的主设备插不进来。这是"写寄存器地址 + 读数据"必须原子完成时用的手段。

### 2.3 一次完整事务的波形

**（a）写一个字节到 0x50 的寄存器 0x10**

```
 S │ 1010000 0 │A│ 00010000 │A│ 01011010 │A│ P
   │ addr=0x50 │ │  reg     │ │  data    │ │
   │      W(0) │ │  0x10    │ │  0x5a    │ │
   └── 第一个字节 = addr<<1|0 = 0xA0
       内核用 i2c_8bit_addr_from_msg() 拼（include/linux/i2c.h:940）

 内核侧：i2c_smbus_write_byte_data(client, 0x10, 0x5a)
        → 一条 i2c_msg，len=2，buf={0x10, 0x5a}
```

**（b）从 0x50 的寄存器 0x10 读一个字节（repeated-START 模式）**

```
 S │ 1010000 0 │A│ 00010000 │A│ Sr │ 1010000 1 │A│ 01011010 │N│ P
   │      W    │ │  reg     │ │    │      R(1) │ │  data    │ │
   │           │ │  0x10    │ │    │           │ │  0x5a    │ │
                                ↑                            ↑
                        repeated-START                   主设备回 NAK
                        总线未释放                        表示读完了

 内核侧：i2c_transfer(adap, msgs, 2)，msgs[0] 写 1 字节、msgs[1] 带 I2C_M_RD
        这就是 i2c_transfer 的 num 参数为什么存在（05 §1.3）
```

**（c）同样的读，但用两次独立调用**

```
 S │ addr W │A│ reg │A│ P      ← 第一次调用，发了 STOP
 S │ addr R │A│ data│N│ P      ← 第二次调用
              ↑
        中间总线被释放，别的主设备/别的 client 可以插进来。
        有些芯片会在 STOP 后丢掉"寄存器指针"，于是读回错误数据。
```

**（b）和（c）在软件上都是"读寄存器"，在总线上是两回事。**这是最常见的一类"驱动写完读不对"的原因，`07-debug.md` §8.2 把它列为排查项。

### 2.4 地址空间与保留地址

7 位地址 = 128 个组合，但两头被规范保留，实际可用 **0x08 ~ 0x77，共 112 个**。内核在 `i2c-core-base.c:771-780` 的注释里原样抄了这张表：

| 地址 | 用途 | 内核里的体现 |
|---|---|---|
| `0x00` | General call（广播）/ START byte | 宽松校验唯一挡住的地址（`i2c-core-base.c:757`） |
| `0x01` | CBUS 地址（历史遗留） | — |
| `0x02` | 保留给其它总线格式 | — |
| `0x03` | 保留 | — |
| `0x04-0x07` | Hs-mode 主设备码 | — |
| **`0x08`** | SMBus Host | 内核用它做 Host Notify（`i2c-smbus.c:306` 的 `"smbus_host_notify"`） |
| **`0x0c`** | SMBus Alert Response Address (ARA) | 内核用它做 SMBus Alert（`i2c-core-smbus.c:697` 的 `"smbus_alert"`） |
| `0x78-0x7b` | 10 位地址寻址前缀 | — |
| `0x7c` | （规范后来指定为）Device ID 地址 | `I2C_ADDR_DEVICE_ID`（`i2c-core-base.c:56`），`i2c_get_device_id()` 用（`:2389`） |
| `0x7d-0x7f` | 保留 | — |

**内核的两套校验**（严格程度不同，用在不同场合）：

| 函数 | 位置 | 规则 | 用在哪 |
|---|---|---|---|
| `i2c_check_addr_validity()` | `i2c-core-base.c:751` | 只挡 `0x00` 和 `>0x7f`；注释明写"故意不强制地址映射约束" | 实例化设备时（允许你用保留地址，因为现实中真有芯片这么干） |
| `i2c_check_7bit_addr_validity_strict()` | `:769` | 挡 `<0x08` 和 `>0x77` | **探测（detect）时**、**slave 注册时**（`i2c-core-slave.c:33`）—— 这两种场合不该碰保留地址 |

**方向位**：总线上第一个字节是 `addr << 1 | read`，不是地址本身。内核用 `i2c_8bit_addr_from_msg()`（`include/linux/i2c.h:940`）拼装。所以**手册上写"设备地址 0xA0"时要警惕**：那通常是含方向位的 8 位形式，对应内核里的 7 位地址 0x50。这是新手最常踩的坑。

**10 位地址**：用 `0x78-0x7b` 前缀 + 第二个字节表示低 8 位，共 `11110 A9 A8 R/W` + `A7..A0`。内核用 `I2C_CLIENT_TEN` 标志（DT 里是 `reg = <(0x3ff | I2C_TEN_BIT_ADDRESS)>`，解析在 `i2c-core-of.c:41`）。**现实中极少见**，而且不是所有控制器都支持（要 `I2C_FUNC_10BIT_ADDR`）。

### 2.5 时钟拉伸（clock stretching）

从设备如果来不及处理，可以在 ACK 之后**一直按住 SCL 不放**，主设备必须等。这是 I²C 唯一的流控手段。

```
SCL  ┐   ┌───┐   ┌─────────────────┐   ┌───┐
     └───┘   └───┘                 └───┘   └──
                  ├───── 从设备按住 ─────┤
                  主设备想拉高，但线上还是低
                  → 主设备必须读回 SCL 才能发现这件事
```

**关键推论：主设备必须能读 SCL 才能支持时钟拉伸。** 内核里的直接体现：`i2c-algo-bit` 在 `getscl == NULL` 时会挂上 `I2C_AQ_NO_CLK_STRETCH` quirk 并打印 `"Not I2C compliant: can't read SCL"` + `"Bus may be unreliable"`（`algos/i2c-algo-bit.c:661-677`）。

**症状**：拉伸太久 → 主设备超时 → **`-ETIMEDOUT`**。常见于 EEPROM 内部写周期（几毫秒）、传感器转换中。

### 2.6 多主机与仲裁

一条总线上可以有多个主设备（例如 SoC 和 BMC 共享一条总线）。仲裁靠"线与"自然完成：

```
   Master A 想发 1，Master B 想发 0
   → 线上是 0（B 赢）
   → A 读回 SDA 发现与自己想发的不一致 → A 立刻退出，成为从设备
```

**内核侧**：丢失仲裁报 **`-EAGAIN`**（`Documentation/i2c/fault-codes.rst`）。`algos/i2c-algo-pcf.c:65` 的 `handle_lab()`（Lost Arbitration Bit）是内核里少见的、直接面对多主机的代码，按 `lab_mdelay` 退避重试。

**多主机在实践中很麻烦**：绝大多数嵌入式板子是单主机。如果你遇到 `-EAGAIN` 但确信是单主机，那更可能是噪声或上升沿太慢被误判（`07-debug.md` §7）。

### 2.7 速率档位

内核把规范的档位定义成常量（`include/linux/i2c.h:45-50`），DT 里的 `clock-frequency` 就是填这些值：

| 常量 | 频率 | 俗称 | 备注 |
|---|---|---|---|
| `I2C_MAX_STANDARD_MODE_FREQ` | 100 kHz | Standard | 最保守，兼容一切 |
| `I2C_MAX_FAST_MODE_FREQ` | 400 kHz | Fast | **嵌入式最常用** |
| `I2C_MAX_FAST_MODE_PLUS_FREQ` | 1 MHz | Fast+ | 需要小 Rp 或主动上拉 |
| `I2C_MAX_TURBO_MODE_FREQ` | 1.4 MHz | Turbo | 罕见 |
| `I2C_MAX_HIGH_SPEED_MODE_FREQ` | 3.4 MHz | Hs | 需要特殊的桥接与主动上拉 |
| `I2C_MAX_ULTRA_FAST_MODE_FREQ` | 5 MHz | UFm | **单向推挽，不兼容普通 I²C** |

**注意"速率"是主设备的目标值，不是保证值**：从设备拉伸时钟、上升沿慢，实际都会低于标称。DT 里写 400 kHz 而示波器上量到 380 kHz 是正常的。

### 2.8 SMBus：I²C 的子集加约定

SMBus 是 Intel 在 I²C 之上加的一层规范。它**不是另一种总线**，电气和位时序完全一样，加的是"上层协议约定"：

| SMBus 加了什么 | 内容 | 内核里的体现 |
|---|---|---|
| **固定的事务形态** | Quick / Byte / Byte Data / Word Data / Block / Process Call 等 9 种 | `I2C_SMBUS_*` 常量 + 13 个 helper（[`01-api.md`](01-api.md) §2） |
| **强制超时** | 从设备最多拉伸 25 ms，主设备 35 ms 后必须放弃 | `adap->timeout`，默认 HZ（`i2c-core-base.c:1542`） |
| **PEC**（可选） | 事务末尾加一个 CRC-8 校验字节 | `I2C_CLIENT_PEC`，校验失败报 `-EBADMSG` |
| **Alert / Host Notify** | 从设备主动通知主设备的两种机制 | `i2c-smbus.c`（[`04-extensions.md`](04-extensions.md) §5） |
| **保留地址语义** | 0x08 = Host，0x0c = ARA | 见 §2.4 |
| **最小频率** | 10 kHz（I²C 没有下限） | — |

**最重要的一条不对称性**：

```
   控制器只会裸 I²C（能发任意消息序列）
        → SMBus 事务可以用 I²C 消息序列【仿真】出来
          i2c_smbus_xfer_emulated()，i2c-core-smbus.c:322
          声明 I2C_FUNC_SMBUS_EMUL 即可

   控制器只会 SMBus（硬件状态机固定了事务形态）
        → 【无法】承载任意 I²C 消息
          发裸 I²C 传输会被拒：
          "I2C level transfers not supported"，i2c-core-base.c:2243
          → -EOPNOTSUPP
```

这条不对称性贯穿整个子系统的设计（[`03-internals.md`](03-internals.md) §A6）。**对 arm64 来说是好消息**：见下节。

### 2.9 arm64 SoC 上的实际硬件形态

**（a）一条 I²C 总线在 arm64 板子上由什么组成**

```
   ┌─────────────────────────── SoC ────────────────────────────┐
   │                                                             │
   │  ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌───────────┐ │
   │  │ I2C IP   │◄──│  clk    │   │ pinctrl  │   │  reset    │ │
   │  │ (寄存器) │   │(分频源) │   │(引脚复用)│   │ controller│ │
   │  └────┬─────┘   └─────────┘   └────┬─────┘   └───────────┘ │
   │       │  中断 ──→ GIC                │                       │
   └───────┼───────────────────────────────┼──────────────────────┘
           │                               │
      ┌────┴────┐                   引脚复用成 I2C 功能
      │ SDA/SCL │                   （而不是 GPIO / SPI / UART）
      └────┬────┘
           │  ← 板上上拉电阻 Rp（DT 里没有节点，纯硬件）
   ────────┴────────────────┬──────────────────┬────────────
                       ┌────┴────┐        ┌────┴────┐
                       │ 传感器  │        │ EEPROM  │
                       │ + 供电  │        │         │
                       │(regulator)       │         │
                       └─────────┘        └─────────┘
```

写/调 arm64 上的 I²C 时，实际要对的**五样东西**（每一样都可能是故障源）：

| 组成 | DT 里长什么样 | 出问题的典型症状 |
|---|---|---|
| 控制器 IP | 控制器节点的 `compatible` + `reg` + `interrupts` | probe 失败、根本没有 `/dev/i2c-N` |
| 时钟 | `clocks` / `clock-names` | 实际速率不对（分频算错） |
| 引脚复用 | `pinctrl-0` / `pinctrl-names` | 完全没有波形（引脚还是 GPIO 功能） |
| 复位 | `resets`（部分 SoC 需要） | 控制器寄存器读回全 0 |
| 从设备供电 | 子节点的 `xxx-supply` | `-ENXIO`（芯片没电当然不 ACK） |
| **上拉电阻** | **DT 里没有** | 上升沿太慢 → 高速下随机出错 |

**上拉电阻在 DT 里不可见**这一点值得单独强调：它是软件完全观测不到的故障源，只能靠示波器或查原理图。

**（b）为什么 arm64 上几乎总是"裸 I²C + SMBus 仿真"**

对 `drivers/i2c/busses/` 的 133 个驱动做了一次统计（命令见 [`99-evidence.md`](99-evidence.md) §3）：

| 分类 | 数量 | 说明 |
|---|---|---|
| 实现 `.master_xfer` | 81 | 能发任意 I²C 消息 |
| 实现 `.smbus_xfer` | 30 | 有原生 SMBus 硬件加速 |
| 两者都实现 | 4 | 优先原生、失败退回仿真 |
| **只有 `.smbus_xfer`（不能发裸 I²C）** | **26** | 这类会拒绝 `i2c_transfer()` |

那 26 个"只会 SMBus"的驱动里，**绝大多数是 x86 南桥/PCH**（`i2c-i801`、`i2c-piix4`、`i2c-ali*`、`i2c-amd*`、`i2c-sis*`、`i2c-nforce2`、`i2c-viapro`、`i2c-isch`、`i2c-ismt`、`scx200_acb`…）。arm64 上只有两个：

- `i2c-mlxbf.c` —— Mellanox BlueField（`depends on MELLANOX_PLATFORM && ARM64`，`busses/Kconfig:764-768`）
- `i2c-xgene-slimpro.c` —— APM X-Gene，通过 mailbox 委托给协处理器（`depends on ARCH_XGENE && MAILBOX`，`:1406-1408`）

**结论：在普通 arm64 SoC 上你几乎一定拿到的是 `I2C_FUNC_I2C | I2C_FUNC_SMBUS_EMUL`。**这意味着：

- 你写的 client 驱动可以自由使用 `i2c_transfer()` 的多消息组合，不用担心被拒；
- 你调用 `i2c_smbus_*()` helper 时，走的是**软件仿真**路径（`i2c-core-smbus.c:322`）而不是硬件加速 —— 所以 SMBus 的 PEC、超时这些"规范保证"实际上是内核软件在做，性能也就是普通 I²C 的性能；
- `03-internals.md` §A6 讲的"双轨选择"逻辑，在 arm64 上基本只会走仿真那一轨。

**（c）arm64 相关的两个可选能力**

| 能力 | 实现驱动数 | arm/arm64 上的实例 |
|---|---|---|
| `.master_xfer_atomic`（关机/panic 路径可用，见 [`05-usage.md`](05-usage.md) §4.2） | 11 | `i2c-imx`、`i2c-rk3x`、`i2c-tegra`、`i2c-tegra-bpmp`、`i2c-rcar`、`i2c-sh_mobile`、`i2c-omap`、`i2c-owl`、`i2c-meson`、`i2c-mv64xxx`、`i2c-ocores` |
| `.reg_slave`（从机模式，见 [`04-extensions.md`](04-extensions.md) §3） | 13 | `i2c-aspeed`、`i2c-npcm7xx`、`i2c-imx`、`i2c-rcar`、`i2c-cadence`、`i2c-designware-slave`、`i2c-stm32f7`、`i2c-bcm-iproc`、`i2c-at91-slave`、`i2c-axxia`、`i2c-emev2`、`i2c-pxa`、`i2c-mlxbf` |

**全是 arm/arm64 平台** —— 这两个能力恰好是嵌入式独有的需求（关机时用 PMIC 断电、BMC 场景下扮演从设备），x86 桌面/服务器的 PCH SMBus 控制器都不做。

**（d）arm64 上的枚举路径**

**只有设备树一条主线。**控制器由 platform bus 从 DT 匹配出来，从设备由 `of_i2c_register_devices()`（`i2c-core-of.c:85`）在适配器注册时扫描子节点建出来。

本文档集**不描述 ACPI 路径**（`i2c-core-acpi.c` 的 847 行、OpRegion、DMI/SPD 自动注册），因为那些是 x86 平台机制。arm64 服务器（SBSA/SBBR）确实也用 ACPI，但那超出本文档集的范围。

---

## 3. 三层模型：adapter / client / driver

这是理解 i2c 子系统的主干概念，务必分清：

```
  ┌──────────────────────────────────────────────────────────────┐
  │  芯片驱动层  struct i2c_driver                                │
  │  例：drivers/rtc/rtc-ds1307.c、drivers/eeprom/at24.c          │
  │  提供 probe_new/remove/id_table，注册到 i2c_bus_type          │
  └──────────────┬───────────────────────────────────────────────┘
                 │  driver-model 匹配（OF → ACPI → id_table；arm64 走 OF）
  ┌──────────────┴───────────────────────────────────────────────┐
  │  设备实例层  struct i2c_client                                │
  │  "挂在第 3 条总线 0x50 地址上的那颗 EEPROM"                    │
  │  五种实例化途径；arm64 主用 DT，另有 sysfs/探测/板级信息        │
  └──────────────┬───────────────────────────────────────────────┘
                 │  client->adapter
  ┌──────────────┴───────────────────────────────────────────────┐
  │  总线控制器层  struct i2c_adapter + struct i2c_algorithm      │
  │  例：drivers/i2c/busses/i2c-imx.c（133 个文件，8.9 万行）      │
  │  提供 master_xfer / smbus_xfer / functionality                │
  └──────────────────────────────────────────────────────────────┘
```

- **`i2c_adapter`** = 一条总线的主控端。它是一个 `struct device`（`i2c-%d`），持有算法指针 `algo`、总线锁 `bus_lock`、超时 `timeout`、重试次数 `retries`、能力缺陷描述 `quirks`。定义在 `include/linux/i2c.h:723-758`。
- **`i2c_client`** = 总线上的一颗从设备。它也是一个 `struct device`（命名 `%d-%04x`，即"总线号-地址"，见 `i2c-core-base.c:892`），核心字段只有 `addr` / `adapter` / `flags` / `name`。定义在 `include/linux/i2c.h:334-358`。
- **`i2c_driver`** = 能驱动某类从设备的代码。定义在 `include/linux/i2c.h:271-310`。

注意 client 和 driver 是**分离**的：client 的产生（枚举）和 driver 的加载是两个独立事件，谁先谁后都行，`i2c_bus_type` 负责在两者相遇时撮合。这正是 Linux 设备模型的标准做法，i2c 是它一个非常干净的实例 —— 想理解 `bus_type` / `device` / `driver` 三元组，读 i2c core 比读 platform bus 更有收获。

**第四个角色：slave 框架。** Linux 也能让本机的 I²C 控制器**扮演从设备**（例如模拟一颗 EEPROM 供对端 BMC 读取）。这套机制走的是完全不同的路径：`i2c_slave_register()` + 事件回调，见 `04` §3。

---

## 4. 核心数据结构速览（细节见 `02`）

> 代码地图（哪个功能在哪个 `.c`、哪个声明在哪个 `.h`、Kconfig 能裁掉什么）已移到 [`01-api.md`](01-api.md) §0 ——
> 这样"找文件"和"找 API"在同一篇里完成。

### 4.1 一条压缩链

整个子系统的对象关系可以压缩成一条链，**记住这条链就记住了 90% 的数据结构**：

```
 i2c_driver ──match──► i2c_client ──adapter──► i2c_adapter ──algo──► i2c_algorithm
     │                     │                       │                      │
 .probe_new            addr / flags            nr / name[48]         master_xfer
 .remove               irq / slave_cb          timeout / retries     smbus_xfer
 .id_table             dev  "3-0050"           dev  "i2c-3"          *_xfer_atomic
 .of_match_table       ↑                       bus_lock : rt_mutex   functionality
 .detect               │                       mux_lock : rt_mutex   reg_slave
 .address_list         │                       │
 .class                │                       ├─ lock_ops ─────► 3 个实现（04 §1）
                       │                       ├─ quirks ───────► 能力缺陷描述
                       │                       ├─ bus_recovery_info ─► 卡死恢复
                       │                       ├─ algo_data ────► 控制器/算法私有
                       │                       └─ addrs_in_instantiation（防竞态位图）
                       │
              一次传输的载荷：struct i2c_msg[]  {addr, flags, len, buf}   ← uapi
                             union i2c_smbus_data {byte, word, block[34]} ← uapi
```

**四个层次的对应关系**：`i2c_driver` 是代码，`i2c_client` 是"总线上的一颗芯片"，
`i2c_adapter` 是"一条总线"，`i2c_algorithm` 是"这条总线怎么发字节"。
`i2c_bus_type`（`i2c-core-base.c:699`）负责在 driver 和 client 相遇时撮合。

**这条链上只有第一段是设备模型的 device↔driver 关系**（`i2c_client` ↔ `i2c_driver`）。
后两段都不是：`──adapter──►` 只是一个指针，`──algo──►` 是"结构体持有一张 ops 表"，
`i2c_algorithm` 根本没有 `struct device`、不注册、不参与匹配。`i2c_adapter` 自己虽然也是
i2c 总线上的一个 `struct device`，但核心用 `dev.type` 保证它**永不绑定 driver**；
真正驱动这块控制器的 `platform_driver` 在 **platform 总线**上。
这三组关系连同 i2c 总线与 platform 总线的缝合方式，见 [`02-datastructures.md`](02-datastructures.md) §1.0。

### 4.2 必须认识的结构体

| 结构体 | 定义 | 谁填 | 一句话 |
|---|---|---|---|
| `i2c_adapter` | `include/linux/i2c.h:723-758` | 控制器驱动 | 一条总线。`name` 和 `algo` **必填**，否则注册返回 `-EINVAL` |
| `i2c_client` | `:334-358` | **核心**（枚举时建） | 一颗从设备。驱动只读不建 |
| `i2c_driver` | `:271-310` | 芯片驱动 | 新代码只实现 `probe_new`，不实现旧的 `probe` |
| `i2c_algorithm` | `:545-573` | 控制器驱动 | `master_xfer` 返回**成功处理的消息数**，不是 0 |
| `i2c_timings` | `:602-611` | 控制器驱动**自己**调 `i2c_parse_fw_timings()` 从 DT 填 | 核心不替你调；不调就一直是 0（见 `06` §2.3） |
| `i2c_bus_recovery_info` | `:644-662` | 控制器驱动 | 校验不过会被核心**静默置 NULL**（见 §5.7） |
| `i2c_adapter_quirks` | `:690` | 控制器驱动 | 把"我做不到 XX"写成数据，核心统一挡请求 |
| `i2c_msg` | `include/uapi/linux/i2c.h:73-87` | 调用者 | 在 **uapi** 里，所以带 32/64 位 compat 包袱 |

`i2c_client->flags` 的六个位（`:336-342`）：`I2C_CLIENT_PEC`(0x04) /
`TEN`(0x10，10 位地址) / `SLAVE`(0x20) / `HOST_NOTIFY`(0x40) / `WAKE`(0x80) /
`SCCB`(0x9000)。DT 里通过 `reg` 的高位标志和 `wakeup-source` 等属性翻译过来。

### 4.3 命名规则：看到一个名字就知道它是什么

| 你看到的 | 含义 | 出处 |
|---|---|---|
| `/sys/bus/i2c/devices/i2c-3` | 第 3 条总线（适配器） | `i2c-core-base.c:1553` |
| `/sys/bus/i2c/devices/3-0050` | 第 3 条总线上 0x50 地址的从设备 | `:892`（`"%d-%04x"`） |
| `/dev/i2c-3` | 第 3 条总线的字符设备（major 89） | `i2c-dev.c:667` |
| `i2c-1-mux (chan_id 0)` | 挂在总线 1 上的 mux 的第 0 通道 | `i2c-mux.c:329-330` |
| `i2c:at24c02` | modalias，udev 靠它自动加载模块 | `i2c-core-base.c:161` |
| DTS 里的 `eeprom@50` | 单位地址，**核心从不解析它**，只读 `reg` | 见 `06` §3.1 |

三层锁与全局对象（完整表见 `02` §3）：`core_lock`（保护 `i2c_adapter_idr`，并序列化
detect 与 adapter 删除）、`adap->bus_lock`（一次事务的原子性）、`adap->mux_lock`（mux
的 select→xfer→deselect 三步）。

---

## 5. 核心机制速览（细节见 `03`）

这一节把 `03` 的 Part A + Part B 压成"每个机制一句话 + 一个行号"，
**排列顺序和 `03` 完全一致**：§5.1–§5.7 沿总线生命周期的时间线走，§5.8–§5.9 是贯穿全程的横切机制。
每个小节标题后面括号里的 `§Ax`/`§Bx` 就是 `03` 里对应的那一节。
**复习时读这节，要改代码时去 `03`。**

> 行号约定：本节裸写的 `:N` 一律指 **`drivers/i2c/i2c-core-base.c`**；其它文件都写全名。

### 5.1 初始化：为什么从来不会"总线还没注册"　　（`03` §A1）

`i2c_init()`（`i2c-core-base.c:2087`）注册在 **`postcore_initcall`**（`:2152`）——
排在 `core_initcall` 之后、`subsys_initcall` 之前，因此早于所有控制器驱动和芯片驱动。
核心还留了防线：`!is_registered` 时 `i2c_register_adapter()` 直接 `WARN` + `-EAGAIN`（`:1518`）。

初始化七步：抬高动态总线号起点（`of_alias_get_highest_id("i2c")`）→ `bus_register` →
`is_registered = true` → 建 debugfs 根 → class_compat → 注册 `dummy_driver` →
注册 DT overlay 通知链（`:2117-2118`）。

### 5.2 适配器注册：这个顺序决定了驱动能假设什么　　（`03` §A2）

`i2c_register_adapter()`（`:1513-1612`）的关键先后：

```
 检查 name/algo → 补 lock_ops、初始化两把 rt_mutex、timeout 默认 HZ（:1542）
   → device_register（名字 i2c-%d）→ debugfs → SMBus Alert → pm_runtime
   → i2c_init_recovery()（:1573）           恢复机制在第一次传输前就位
   → of_i2c_register_devices()（:1588）      DT 子设备全部建好
   → 遍历已加载驱动、给每个带 detect 的一次扫描机会（:1596-1598）
```

**含义**：DT 里描述的每个子设备，在任何驱动看到这条总线之前就已经有 client 了。

总线号有三条来路：DT `aliases { i2c0 = &i2c0; }` 固定编号（`of_alias_get_id`，`:1654`）→
`i2c_add_numbered_adapter()` 精确占位（`:1621`，冲突返回 `-EBUSY`）→
`idr_alloc` 从 `__i2c_first_dynamic_bus_num` 起动态分配（`:1662`）。

### 5.3 设备从哪来：五条实例化途径　　（`03` §A3）

| 途径 | 入口 | arm64 上 |
|---|---|---|
| ① 设备树 | `of_i2c_register_devices()`，`i2c-core-of.c:85` | **唯一主线**，见 `06` |
| ② ACPI | `i2c_acpi_register_devices()` | x86 固件路径，本文档集不展开 |
| ③ 板级信息 | `i2c_scan_static_board_info()`，`:1405` | 旧式板文件遗留，只对静态编号总线生效 |
| ④ sysfs | `new_device_store()`，`:1255` | 调试/临时挂设备很常用，见 `07` |
| ⑤ 自动探测 | `i2c_detect()`，`:2510` | **不要再用**，`I2C_CLASS_DEPRECATED` 就是它的墓碑 |

地址占用检查是**双向**的（`i2c_check_addr_busy()`，`:825`）：既往上查父总线，又往下递归所有
mux 子总线 —— 因为 mux 的语义是"父总线的地址空间被子总线共享"。
另有 `addrs_in_instantiation` 位图防止 DT 枚举和 sysfs 同时创建同一地址（`:926`）。

### 5.4 匹配与 probe：驱动 probe 时能拿到什么　　（`03` §A4）

**匹配顺序**（`i2c_device_match()`，`:138`）：OF → ACPI（arm64 恒不中）→ `id_table`。
一个反直觉的结果：sysfs `new_device` 建的设备没有 `of_node`，却能匹配只有
`of_match_table` 的驱动 —— 靠 `i2c_of_match_device_sysfs()` 拿 `client->name` 去和
compatible 字符串比（`i2c-core-of.c:116-143`）。

**probe 顺序**（`i2c_device_probe()`，`:488-620`）：
① 解析 irq（Host Notify → DT `interrupt-names` 里的 `"irq"` / 第 0 个 → ACPI）→
② 二次确认匹配，防止无 `id_table` 又无固件匹配的驱动被强绑 →
③ `wakeup-source` → wake irq →
④ `of_clk_set_defaults` → ⑤ PM domain attach →
⑥ **开 devres group**（`:576`，probe 失败时驱动的所有 `devm_*` 整组回滚）→
⑦ 调 `probe_new`（或旧 `probe`）。

### 5.5 传输主路径：三个最容易误解的点　　（`03` §A5）

外壳 `i2c_transfer()`（`:2309`）只做一件事：加锁 → 调 `__i2c_transfer()` → 解锁。
主干 `__i2c_transfer()`（`:2237-2295`）依次做 suspend 检查、quirks 检查、tracepoint、
重试循环、tracepoint 收尾。**端到端调用链见 §6。**

| 误解 | 事实 | 行号 |
|---|---|---|
| `adap->timeout` 是单次传输超时 | 是**重试循环的总时间预算**；单次超时得控制器驱动自己做（`wait_for_completion_timeout`） | `:2273-2283` |
| 出错就会重试 | **只有 `-EAGAIN`（仲裁丢失）才重试**。把丢仲裁误报成 `-EIO` 会让多主机重试彻底失效 | 同上 |
| 带 `__` 的版本可以随便调 | `__i2c_transfer()` / `__i2c_smbus_xfer()` **假定调用者已持锁**；mux 的 select 回调和 SMBus 仿真必须用它们，否则死锁 | — |

### 5.6 SMBus 双轨：原生优先，失败退回仿真　　（`03` §A6）

```
 __i2c_smbus_xfer()   i2c-core-smbus.c:553
   ├─ 有 algo->smbus_xfer？ → 原生调用（同样带重试循环）
   │     └─ 返回 -EOPNOTSUPP 且有 master_xfer → 落到仿真（:599-608）
   └─ 无 → i2c_smbus_xfer_emulated()（:322-520）
            把一次 SMBus 事务翻成 1~2 条 i2c_msg，再走 __i2c_transfer()
```

这是子系统里最实用的兼容性设计：一个只做了 Byte/Word 的 SMBus 控制器，只要还有
`master_xfer`，就自动支持 Block 和 Process Call。**arm64 上基本只走仿真那一轨**（见 §2.9b）。
PEC 是软件算的 CRC-8，且**覆盖地址字节**（`:60`）。

### 5.7 总线恢复：为什么"配了却没生效"　　（`03` §A7）

原理：从设备在传输中途被主设备复位打断，一直拉低 SDA 等时钟 →
**发 9 个 SCL 脉冲**走完它的移位寄存器，再补一个 STOP
（`i2c_generic_scl_recovery()`，`i2c-core-base.c:222-291`）。

引脚来源两种：pinctrl 的 `default`/`gpio` state（`:303`），或 DT 的
`scl-gpios`/`sda-gpios`（`:348`）。**关键陷阱**：`i2c_init_recovery()`（`:422`）校验不过
（例如声明了 `recover_bus` 却没有 `set_scl`）会打印错误并把 `bus_recovery_info`
**置回 NULL，静默降级为不支持恢复**。排查时第一件事就是开 `CONFIG_I2C_DEBUG_CORE` 看那条错误。

### 5.8 锁与原子传输　　（`03` §B1）

三个 `lock_ops` 实现，区别只在"要不要连父总线一起锁"：

| 实现 | 用于 | 行为 |
|---|---|---|
| `i2c_adapter_lock_ops`（`:1434`） | 根（物理）适配器 | `rt_mutex_lock_nested(&adap->bus_lock, depth)`，**忽略 flags** |
| `i2c_mux_lock_ops`（`i2c-mux.c:271`） | mux-locked 子适配器 | 默认只锁 `mux_lock`，父总线上别人的传输可以插进来 |
| `i2c_parent_lock_ops`（`i2c-mux.c:277`） | parent-locked 子适配器 | 总是连父总线一起锁，整条物理总线被独占 |

`i2c_adapter_depth()`（`:1231`）数 mux 嵌套层数当 lockdep 子类 —— 不这样标注，
子适配器与父适配器的 `bus_lock` 同类嵌套会被 lockdep 报死锁。超过 8 层丢失 lockdep 保护（`:1238`）。

**原子传输**的门槛比想象的高（`i2c_in_atomic_xfer_mode()`，`i2c-core.h:31`）：
必须 `system_state > SYSTEM_RUNNING`（关机/重启/panic）**且**当前不可抢占。
它是给"必须再写一次 PMIC 才能真正断电"兜底的，日常驱动不该指望它可用。
此时加锁只 `trylock`，拿不到直接 `-EAGAIN`。

`bus_lock`/`mux_lock` 是 `rt_mutex` —— **上游 6.1 主线就如此**（为了优先级继承），
不是本树 PREEMPT_RT 补丁改的（i2c 目录一个文件都没被改，见 `99` §1）。

### 5.9 观测与生命周期　　（`03` §B2 / §B4）

- **9 个 tracepoint**：`i2c_write`/`i2c_read`/`i2c_reply`/`i2c_result` +
  4 个 `smbus_*` + 1 个 `i2c_slave`。i2c 那 4 个用 **static key 门控**（`:67`），
  没人开 trace 时热路径上是一条 nop。这是抓波形之外最有效的调试手段，用法见 `07` §2。
- **引用计数**：`i2c_get_adapter()`（`:2602`）同时 `try_module_get(owner)` + `get_device()`
  —— 前者防止用户态还开着 `/dev/i2c-3` 时 `rmmod` 掉控制器驱动，后者防止对象被释放。
  `i2c_put_adapter()` 里 `put_device` 必须最后做，否则 UAF。
- **注销的两遍扫描**（`:1790-1791`）：先清非 dummy 的 client，再清 dummy —— 因为真实驱动的
  `remove` 可能还要访问它自己创建的 dummy（第二地址）。

### 5.10 这个模块好在哪、别扭在哪（完整评价见 `03` Part C）

**好**：`i2c_algorithm` 的抽象边界选得准（133 个控制器驱动平均只 670 行）；quirks 把能力缺陷
写成数据而非代码；SMBus 双向退化；static key 门控的零成本 tracepoint。

**别扭**：`i2c_transfer()` 的返回值表达不了"第几条消息失败、传了几字节"（源码自己的 REVISIT
注释，`:2313-2328`）；`i2c_del_adapter()` 用完成量兜引用计数（FIXME，`:1809-1813`）；
detect/class 是纯历史包袱；`struct i2c_msg` 在 uapi 里锁死了演进空间；
mux 的两种锁风格正确性靠人脑保证，编译器和 lockdep 都查不出来。

---

## 6. 一次传输的端到端路径（预览）

§5.5（重试）/ §5.6（SMBus 选路）/ §5.8（加锁）讲的三件事在真实调用里是套在一起的。
下面用最常见的"读一个寄存器"把它们串成一条完整链路 —— **这张图是本篇最值得反复看的一张**：

```
  客户端驱动
    i2c_smbus_read_byte_data(client, 0x10)          i2c-core-smbus.c:137
        │
        ├─ i2c_smbus_xfer(adap, addr, flags,        i2c-core-smbus.c:535
        │                 I2C_SMBUS_READ, 0x10,
        │                 I2C_SMBUS_BYTE_DATA, &data)
        │      │
        │      ├─ __i2c_lock_bus_helper(adap)       i2c-core.h:37
        │      │     └─ 原子上下文 → trylock，否则 i2c_lock_bus(SEGMENT)
        │      │           └─ adap->lock_ops->lock_bus
        │      │                 ├─ 根适配器：rt_mutex_lock_nested(&adap->bus_lock, depth)
        │      │                 └─ mux 子适配器：见 04 §1
        │      │
        │      ├─ __i2c_smbus_xfer()                i2c-core-smbus.c:553
        │      │     ├─ __i2c_check_suspended()  → -ESHUTDOWN
        │      │     ├─ trace_smbus_read()
        │      │     ├─ 有 algo->smbus_xfer？
        │      │     │     ├─ 有 → 原生调用（重试循环）
        │      │     │     │        └─ 返回 -EOPNOTSUPP 且有 master_xfer → 退回仿真
        │      │     │     └─ 无 → 直接仿真
        │      │     │              i2c_smbus_xfer_emulated()   :322
        │      │     │                 └─ 构造 2 条 i2c_msg（写命令 / 读数据）
        │      │     │                      └─ __i2c_transfer()          i2c-core-base.c:2237
        │      │     │                           ├─ quirks 检查
        │      │     │                           ├─ trace_i2c_write/read
        │      │     │                           ├─ 重试循环（≤ adap->retries，超时 adap->timeout）
        │      │     │                           │     └─ algo->master_xfer(adap, msgs, 2)  ← 控制器驱动
        │      │     │                           └─ trace_i2c_reply/result
        │      │     └─ trace_smbus_reply / trace_smbus_result
        │      │
        │      └─ i2c_unlock_bus(adap, I2C_LOCK_SEGMENT)
        └─ 返回 data.byte
```

从这张图上能直接读出的四件事：

1. **锁只在最外层加**（`i2c_transfer()` / `i2c_smbus_xfer()`），带 `__` 的版本假定已持锁。
2. **仿真路径要嵌套一层**：`i2c_smbus_xfer_emulated()` 构造 `i2c_msg[]` 后调 `__i2c_transfer()`，
   所以一次 `i2c_smbus_read_byte_data()` 其实走了两层协议翻译。
3. **重试和超时都在核心**（`:2273-2283`），控制器驱动不用自己做。默认 `timeout = HZ`（1 秒，
   `:1542-1543`），`retries` 默认 0（驱动可覆盖，例如 algo-bit 设成 3，`algos/i2c-algo-bit.c:660`）。
4. **tracepoint 埋在两层**：SMBus 层一对、I²C 消息层一对。所以开 `smbus:*` 只看到语义事务，
   开 `i2c:*` 才看到总线上真正的字节（见 `07` §2）。

---

## 7. 与内核其它子系统的关系

| 子系统 | 关系 |
|---|---|
| **driver model** | `i2c_bus_type` 在 `postcore_initcall` 阶段注册（`i2c-core-base.c:2152`），早于绝大多数驱动初始化。 |
| **设备树 / OF** | **arm64 上唯一的枚举路径。**`of_i2c_register_devices()` 在适配器注册时扫描子节点（`i2c-core-base.c:1588`）；`of_i2c_notifier` 支持运行时 overlay 增删设备。 |
| **pinctrl / clk / reset / regulator** | 控制器 probe 时的四类依赖，任一未就绪则 `-EPROBE_DEFER`。见 §2.9a 的故障对照表。 |
| **中断** | client 的 irq 有三个来源，按优先级：Host Notify 虚拟 irq → DT `interrupts`（名字 "irq"）→ ACPI（`i2c-core-base.c:503-517`）。arm64 上走中间那条。适配器还可提供 Host Notify 的 irq domain。 |
| **电源管理** | `i2c_device_probe()` 调 `dev_pm_domain_attach()`；适配器有 `pm_runtime` 支持（`:1568-1571`）；`i2c_mark_adapter_suspended()` 让核心在 suspend 后拒绝传输。 |
| **regmap** | `regmap_init_i2c()`（在 `drivers/base/regmap/regmap-i2c.c`）是绝大多数现代芯片驱动的实际入口，底层就是本子系统的 SMBus/I²C 调用。 |
| **ftrace** | 4 个 i2c tracepoint + 4 个 smbus + 1 个 i2c_slave，用 static_key 门控以做到零开销（`i2c-core-base.c:67`）。 |
| **devres** | `devm_i2c_add_adapter()`、`devm_i2c_new_dummy_device()`；`i2c_device_probe()` 还为每个 client 开了一个 devres group（`:576`），让 probe 失败时能整组回滚。 |
| **debugfs** | 全局 `/sys/kernel/debug/i2c/`（`:2104`），每个适配器一个子目录（`:1562`）。 |
| **PREEMPT_RT** | `bus_lock` / `mux_lock` 是 `struct rt_mutex`（**在上游 6.1 里就是**，不是 RT 补丁加的），加上 `i2c_in_atomic_xfer_mode()` 这套原子传输路径，共同解决"关机/panic 路径上要用 I²C 关电源"的问题。详见 `03` §B1。 |

---

## 8. 本文档集导读

| 文档 | 回答什么问题 |
|---|---|
| `00-overview.md`（本篇） | 这个模块是干什么的？硬件长什么样？**核心结构体和机制的速览（复习入口）**。 |
| [`01-api.md`](01-api.md) | **代码在哪**（§0 文件/头文件/Kconfig 地图）+ 我要调用什么？签名、语义、上下文约束、返回值。 |
| [`02-datastructures.md`](02-datastructures.md) | 有哪些结构体？字段什么含义？它们怎么互相指？ |
| [`03-internals.md`](03-internals.md) | 核心是怎么实现的？**Part A** 按时间线走一遍（初始化 → 注册 → 枚举 → 匹配 → 传输 → 恢复 → 拆除），**Part B** 是贯穿全程的横切机制（锁 / 引用计数 / PM / 可观测性），**Part C** 是架构评价。 |
| [`04-extensions.md`](04-extensions.md) | mux / algos / slave / i2c-dev / SMBus 扩展 / 枚举后端。 |
| [`05-usage.md`](05-usage.md) | 给我可以照抄的代码。 |
| [`06-devicetree.md`](06-devicetree.md) | DTS 怎么写？每个属性由谁消费？配错了怎么定位？ |
| [`07-debug.md`](07-debug.md) | 出问题了怎么查。 |
| [`99-evidence.md`](99-evidence.md) | 上面每条论断的 `文件:行号` 出处，以及哪些是我没验证的。 |

**阅读顺序建议**：

- **只想用**（板上加了一颗芯片）：本篇 §2 → `06` → `05` §1 → `01`。出问题转 `07`。
- **要写控制器驱动**：本篇 §2.9 → `01` §0 → `02` → `03` → `05` §4 → `06` §2。
- **要看懂核心**：本篇全篇 → `01` §0 → `02` → `03` → `04`。
- **要复核我的结论**：`99`。
- **隔了几个月回来复习**：只读本篇 §4 + §5 + §6（三节合起来是 `02`/`03` 的骨架），
  哪一条想不起来细节了，再按行号跳到对应文档。
