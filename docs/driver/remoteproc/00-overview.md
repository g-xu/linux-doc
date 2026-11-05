---
title: 总览
---

# remoteproc 00：总览（复习入口）

> 版本：Linux 6.1.158 · 架构基准：arm64 · 本篇定位：**复习入口**
>
> 隔几个月回来，只读这一篇就能恢复全貌；要细节再翻后面九篇（§8 有导读表）。
>
> ⚠️ 本篇是**浓缩**，不是新内容。所有论断的取证都在对应的分篇里，
> 本篇只在最关键的地方给行号。

---

## §1 定位：remoteproc 解决什么问题

### §1.1 一句话

**remoteproc 是内核用来"启动、停止、监控一颗不跑 Linux 的处理器"的框架。**

### §1.2 它管什么、不管什么

| | 内容 |
|---|---|
| **管** | 加载固件（ELF）、放开/按下复位、分配那颗核要用的内存、崩溃后自动恢复、把状态暴露给用户态 |
| **不管** | **和那颗核通信**。通信是 virtio + rpmsg 的事，remoteproc 只负责"把管道的规格从固件里读出来、把地址填好" |
| 不管 | 固件的编译、打包、签名（厂商工具链的事） |
| 不管 | 远端那一侧跑什么（RTOS / 裸机 / OpenAMP，都不在内核树里） |

### §1.3 三个关键判据

| 判据 | 答案 | 出处 |
|---|---|---|
| 它有自己的 `struct bus_type` 吗？ | **没有** ⇒ 它是**框架型**子系统，不是总线型 | 02 §1.0 |
| 远端处理器是一个"设备"吗？ | 是 `struct device`，但挂在 `rproc_class` 上，**不绑任何 driver** | 02 §2 |
| 内核和远端怎么通信？ | 通过 remoteproc **创建的 virtio 设备**，再由 rpmsg 认领 | 04 §1 · 08 §1 |

### §1.4 规模

| 项 | 数值 |
|---|---|
| 核心七个 `.c` | `remoteproc_core.c` 2770 行 · `remoteproc_virtio.c` 603 · `remoteproc_coredump.c` 469 · `remoteproc_debugfs.c` 430 · `remoteproc_elf_loader.c` 395 · `remoteproc_sysfs.c` 275 · `remoteproc_cdev.c` 126 |
| 头文件 | `include/linux/remoteproc.h` 708 行 · `drivers/remoteproc/remoteproc_internal.h` 228 · `drivers/remoteproc/remoteproc_elf_helpers.h` 122 |
| 平台驱动 | 34 个 `.c`，共 25252 行 |
| Kconfig | 28 个 `config` 项（1 个总开关 + 1 个 cdev + 26 个平台） |
| 官方文档 | `Documentation/staging/remoteproc.rst` 360 行（注意在 **staging** 下） |
| DT 绑定 | `Documentation/devicetree/bindings/remoteproc/` 25 个文件（19 `.yaml` + 6 `.txt`） |
| tracepoint | **0 个** |

---

## §2 硬件背景：为什么 SoC 上会有"另一颗核"

### §2.1 AMP：非对称多处理

arm64 SoC 上跑 Linux 的那几个 Cortex-A 核是**对称的**（SMP）——
同一份内核调度它们，共享同一份内存视图和 cache 一致性。

但一颗现代 SoC 上还常常有**另一类核**，它们和 A 核**不对称**（AMP）：

```
   ┌──────────────────── SoC ─────────────────────────────┐
   │                                                       │
   │  ┌─────────────────────────┐   ┌──────────────────┐  │
   │  │  Cortex-A 集群（SMP）    │   │  Cortex-M / R    │  │
   │  │  A53 A53 A53 A53         │   │  或 DSP          │  │
   │  │  ↑ 跑 Linux              │   │  ↑ 跑 RTOS/裸机  │  │
   │  │  有 MMU、有 cache 一致性  │   │  常常无 MMU      │  │
   │  └─────────────────────────┘   └──────────────────┘  │
   │            │                            │             │
   │            └──────┬─────────────────────┘             │
   │                   │                                   │
   │             共享 DDR + IPC 硬件（mailbox）             │
   └───────────────────────────────────────────────────────┘
```

### §2.2 为什么要有这颗核：四个真实理由

| 理由 | 说明 | 典型场景 |
|---|---|---|
| **实时性** | Linux 的中断延迟在微秒到毫秒量级且不可预测；Cortex-R / M 能做到确定性的几百纳秒 | 电机控制、逆变器、CAN 总线协议栈 |
| **功耗** | A 核全部下电时，M 核（几十 mW）继续维持传感器采集、唤醒逻辑 | 手机的 always-on 传感器中枢 |
| **专用算力** | DSP 的乘加吞吐远高于通用核 | 音频解码、语音唤醒、图像前处理 |
| **安全隔离** | 独立核 + 独立内存域，Linux 被攻破也影响不到它 | 高通的 modem、安全启动链 |

### §2.3 arm64 上会遇到的几种远端

| 远端类型 | 指令集 | 典型 SoC | 本树驱动 |
|---|---|---|---|
| **Cortex-R5F** | armv7-R | TI AM65x / J721e | `drivers/remoteproc/ti_k3_r5_remoteproc.c` |
| **Cortex-M4/M7** | armv7-M | NXP i.MX8 / 8M | `drivers/remoteproc/imx_rproc.c` |
| **C66x / C71 DSP** | TI 私有 | TI J721e | `drivers/remoteproc/ti_k3_dsp_remoteproc.c` |
| **Hexagon（Q6）** | 高通私有 | 高通 SDM/SM 系列 | `drivers/remoteproc/qcom_q6v5_pas.c` 等 |
| **PRU**（可编程实时单元） | TI 私有 | TI AM65x | `drivers/remoteproc/pru_rproc.c` |
| **SCP**（系统控制处理器） | Cortex-M | 联发科 MT8183 | `drivers/remoteproc/mtk_scp.c` |

这六类里，**只有 Cortex-R5F 和 Cortex-M 是"标准 ARM 核"**，
其余三类的指令集、地址空间、启动方式都是厂商私有的 ——
这就是为什么 34 个驱动看起来差别那么大。

### §2.4 硬件要素一：复位与时钟

远端核的启动几乎总是这个形状：

```
   ┌── 上电前 ────────────────────────────────────────┐
   │  reset 信号 = 有效（核被按住）                     │
   │  clock      = 关闭                                │
   │  远端的指令 RAM = 未初始化（全是垃圾）              │
   └──────────────────────────────────────────────────┘
                      │
                      │  ① 使能时钟
                      │  ② 把固件写进指令 RAM / DDR
                      │  ③ 写"启动地址"寄存器（如果硬件支持）
                      │  ④ 撤销 reset
                      ▼
   ┌── 运行中 ────────────────────────────────────────┐
   │  远端从启动地址开始取指、执行                       │
   └──────────────────────────────────────────────────┘
```

**①②③④ 的顺序不能乱**。特别是 ② 必须在 ④ 之前：
复位一撤销，核立刻开始取指，那时候固件必须已经在位。
这就是 `rproc_ops` 里 `load` 在 `start` 之前的原因（03 §A6）。

有的硬件没有"启动地址寄存器"，核复位后固定从某个地址取指
（比如 Cortex-M 从地址 0 读向量表）。
这种情况下**固件的链接地址必须和硬件写死的那个地址一致** ——
这也是 06 §5.1 里 `ti,loczrama` 那类属性存在的理由。

### §2.5 硬件要素二：地址空间不一样

**这是本子系统最核心的硬件差异**。

同一块物理内存，A 核和远端看到的地址往往**不是同一个数**：

```
   物理地址（A 核视角）        远端地址（远端视角）
   ─────────────────────      ──────────────────
   0x4100_0000  ─────────────▶  0x0000_0000   ← 远端的 TCM，映射到它的地址 0
   0xA010_0000  ─────────────▶  0x8000_0000   ← DDR 里的固件段
   0xA000_0000  ─────────────▶  0x9000_0000   ← DDR 里的 vring 区
```

原因有三层：

| 层 | 说明 |
|---|---|
| **核内固定映射** | Cortex-R5F 的 TCM 就是挂在它自己的低地址上，不可移动 |
| **总线上的地址翻译** | SoC 内部互联（NoC）可能对远端发出的地址做偏移 |
| **IOMMU / SMMU** | 有的平台给远端配了 IOMMU，页表由 Linux 建 |

⇒ 内核必须维护一张"物理地址 ↔ 远端地址"的对照表。
**这张表在代码里叫 `carveout`**（08 §6）。

三个术语必须分清（贯穿全套文档）：

| 术语 | 全称 | 谁的视角 |
|---|---|---|
| `pa` | physical address | A 核 / 总线 |
| `va` | virtual address | **内核的**虚拟地址（`ioremap` / `dma_alloc` 得到的） |
| `da` | **device address** | **远端的**视角 |

⇒ `rproc_da_to_va()`（`drivers/remoteproc/remoteproc_core.c:193`）
这个名字读作"把远端地址翻译成内核虚拟地址"。

### §2.6 硬件要素三：cache 一致性通常没有

A 核之间有硬件 cache 一致性（CCI / CMN 之类的互联）。
但**远端核通常不在一致性域里**：

| 情况 | 后果 |
|---|---|
| A 核写了共享内存，数据还在 A 核的 L1/L2 里 | 远端读到的是旧数据 |
| 远端写了共享内存，数据在它自己的 cache 里 | A 核读到旧数据 |

内核的对策是**把共享内存标记为 non-cached（或 write-combining）**。
这就是 DTS 里 `no-map` 属性的作用（06 §3.4）——
让这块内存不进入内核的普通线性映射，
之后通过 `dma_alloc_coherent()` 拿到的是一致性内存。

⚠️ 代价是**访问慢**。所以固件段（只在加载时写一次）和
vring / buffer（频繁读写）在 DTS 里通常分成两块，用不同的属性
（06 §3.1 那张表）。

### §2.7 硬件要素四：IPC —— 门铃与信箱

内核和远端要互相通知"我放了东西在共享内存里"。
硬件提供的机制通常很简单：

```
   ┌── A 核 ──────┐                    ┌── 远端 ──────┐
   │              │  ① 写共享内存       │              │
   │  vring 写数据 ├───────────────────▶│              │
   │              │                     │              │
   │  写门铃寄存器 ├──② 产生中断────────▶│  中断处理     │
   │              │                     │  读 vring     │
   │              │                     │              │
   │  中断处理     │◀──④ 产生中断───────┤  写门铃寄存器 │
   │  读 vring     │◀─── ③ 写共享内存 ───┤              │
   └──────────────┘                    └──────────────┘
```

| 名词 | 是什么 |
|---|---|
| **门铃**（doorbell） | 一个寄存器，写它就在对方那边产生一个中断。**不带数据** |
| **信箱**（mailbox） | 门铃 + 几个可以带少量数据的寄存器 |
| **共享内存** | 真正的数据通道（vring 就在这里） |

Linux 把这类硬件统一抽象成 **mailbox 子系统**（`drivers/mailbox/`）。
remoteproc 驱动通过 `mbox_request_channel()` 拿通道、
`mbox_send_message()` 敲门铃（05 §2.2）。

几种真实硬件：

| 硬件 | 平台 | 形态 |
|---|---|---|
| IPCC | STM32MP1 | 多通道，每通道一个中断 |
| MU（Messaging Unit） | i.MX | 4 个 32 位数据寄存器 + 状态位 |
| TI Mailbox | TI K3 | FIFO 式，能排队多条消息 |
| SMP2P / QMP | 高通 | **不是 mailbox**，是共享内存里的位图 + 中断 |

⚠️ 高通的形态最特殊：它不用 `mboxes` 属性，
而是 `qcom,smem-states`（06 §6.3）。
⇒ **不要把 TI/NXP 的 DTS 写法照搬到高通平台**。

### §2.8 硬件要素五：固件从哪来、长什么样

| 环节 | 说明 |
|---|---|
| 格式 | **ELF**（32 位或 64 位），内核只认这一种（04 §2） |
| 存放 | `/lib/firmware/<name>`，通过 `request_firmware()` 家族读 |
| 加载 | 按 ELF 的 program header 逐段 `memcpy` 到 `da` 对应的位置 |
| 特殊 section | `.resource_table` —— 固件对内核的"需求清单"（§3.2） |

内核**不检查固件的指令集**。
你可以把一个 x86 的 ELF 喂给 Cortex-M 的 remoteproc 驱动，
`rproc_elf_sanity_check()` 只看 magic、class、endianness、段是否可加载
（`drivers/remoteproc/remoteproc_elf_loader.c:37`）。
⇒ 固件不对的表现是"加载成功但远端立刻跑飞"，不是加载报错。

### §2.9 硬件要素六：远端崩溃了怎么知道

| 机制 | 硬件形态 | 本树用法 |
|---|---|---|
| **看门狗中断** | 远端的 WDT 超时后给 A 核一个中断 | `RPROC_WATCHDOG` 崩溃类型 |
| **MMU / MPU fault** | 远端访问非法地址，IOMMU 报错给 A 核 | `RPROC_MMUFAULT` |
| **远端主动报错** | 通过 mailbox 发一条"我挂了" | `RPROC_FATAL_ERROR` |
| **心跳超时** | 内核定时检查远端有没有回应 | 驱动自己实现 |

三种类型的名字在 `drivers/remoteproc/remoteproc_core.c:62-66`
（`mmufault` / `watchdog` / `fatal error`），
驱动通过 `rproc_report_crash()`（`drivers/remoteproc/remoteproc_core.c:2677`）上报。
⇒ **注入测试**：`echo 2 > /sys/kernel/debug/remoteproc/remoteproc0/crash`（07 §5）。

### §2.10 硬件背景总结：六件事决定了框架长什么样

| 硬件事实 | 框架里对应的设计 |
|---|---|
| 复位/时钟要按顺序操作 | `rproc_ops` 的 `prepare`/`start`/`stop`/`unprepare` 四段（03 §A） |
| 地址空间不一样 | `carveout` 链表 + `rproc_da_to_va()`（08 §6） |
| 没有 cache 一致性 | DTS 的 `no-map` + `dma_alloc_coherent`（06 §3.4） |
| IPC 只有门铃 | `rproc_ops` 的 `kick` + mailbox 子系统（05 §2.2） |
| 固件是 ELF | 五个 ELF 默认回调（04 §2） |
| 远端会崩 | `recovery` 机制 + `rproc_report_crash()`（03 §A9） |

**把这张表记住，整个框架的形状就推得出来了。**

---

## §3 概念模型

### §3.1 五个必须分清的对象

```
   ┌─────────────────────────────────────────────────────────────┐
   │  ① struct rproc          一颗远端处理器                      │
   │     ├─ ops               ② struct rproc_ops  平台钩子（17 个）│
   │     ├─ carveouts ────────③ 内存块链表（da ↔ va 对照表）      │
   │     ├─ rvdevs    ────────④ virtio 设备链表                   │
   │     ├─ traces            固件日志缓冲链表                     │
   │     ├─ subdevs           子设备回调链表                       │
   │     └─ table_ptr ────────⑤ 资源表（固件的需求清单）           │
   └─────────────────────────────────────────────────────────────┘
```

| # | 对象 | 一句话 | 详见 |
|---|---|---|---|
| ① | `struct rproc` | 远端处理器的内核表示。有 `struct device`，但**不绑 driver** | 02 §2 |
| ② | `struct rproc_ops` | 平台驱动填的 17 个回调，**只有 3 个必填** | 05 §1.1 |
| ③ | `struct rproc_mem_entry` | 一块内存 + 它的 `da`/`va`/`dma`/`len`。就是 carveout | 02 §6 · 08 §6 |
| ④ | `struct rproc_vdev` | 一个 virtio 设备的中间层，挂在一个**平台设备**上 | 04 §1 · 08 §3 |
| ⑤ | `struct resource_table` | **固件 ELF 里的一个 section**，不是配置文件 | 02 §4 · 08 §2 |

### §3.2 资源表：整个框架的枢纽

固件里那张表长这样：

```
   ┌── resource_table 头 ────────────────────┐
   │  ver = 1                                 │
   │  num = 3          ← 一共 3 条资源         │
   │  offset[0..2]     ← 每条的偏移            │
   ├── 资源 0：RSC_CARVEOUT ─────────────────┤
   │  da = 0x80000000, len = 0xf00000          │
   │  name = "vdev0buffer"                    │
   ├── 资源 1：RSC_TRACE ────────────────────┤
   │  da = 0x80f00000, len = 0x8000            │
   ├── 资源 2：RSC_VDEV ─────────────────────┤
   │  id = 7 (VIRTIO_ID_RPMSG)                │
   │  num_of_vrings = 2                        │
   │  vring[0]: da = ?, notifyid = ?  ← 内核填│
   │  vring[1]: da = ?, notifyid = ?  ← 内核填│
   │  （紧跟在后面就是 virtio 配置空间）        │
   └──────────────────────────────────────────┘
```

| 资源类型 | 内核做什么 | 处理函数 |
|---|---|---|
| `RSC_CARVEOUT` | 分配一块内存，登记进 carveout 链表 | `rproc_handle_carveout()`（`drivers/remoteproc/remoteproc_core.c:822`） |
| `RSC_DEVMEM` | 通过 IOMMU 映射一段已有的物理区间 | `rproc_handle_devmem()`（`drivers/remoteproc/remoteproc_core.c:619`） |
| `RSC_TRACE` | 建一个 debugfs 文件读固件日志 | `rproc_handle_trace()`（`drivers/remoteproc/remoteproc_core.c:545`） |
| `RSC_VDEV` | 建一个平台设备，将来变成 virtio 设备 | `rproc_handle_vdev()`（`drivers/remoteproc/remoteproc_core.c:475`） |
| `RSC_VENDOR_START` 以上 | 转交给驱动的 `ops->handle_rsc` | ⚠️ 有 `RSC_IGNORED` 陷阱（03 §C2） |

**"内核填"这三个字是理解全套文档的钥匙**：
资源表既是固件的**请求单**，也是内核的**回复单**。
`vring[i].da` 和 `vring[i].notifyid` 都是内核算好后写回去的
（`drivers/remoteproc/remoteproc_core.c:381`、
`drivers/remoteproc/remoteproc_virtio.c:162`）。

### §3.3 状态机（七个状态，arm64 上只会看到六个）

```
                 rproc_add()
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
     ┌─────────┐           ┌──────────┐
     │ OFFLINE │           │ DETACHED │  ← bootloader 已经启动了远端
     └────┬────┘           └────┬─────┘
          │ rproc_boot()        │ 自动 attach
          ▼                     ▼
     ┌─────────┐           ┌──────────┐
     │ RUNNING │           │ ATTACHED │
     └────┬────┘           └────┬─────┘
          │                     │ rproc_detach()
          │ 崩溃                 └──────▶ DETACHED
          ▼
     ┌─────────┐  recovery  ┌─────────┐
     │ CRASHED ├───────────▶│ RUNNING │
     └─────────┘            └─────────┘

     rproc_del() ──▶ DELETED
     SUSPENDED：arm64 上永远不会出现（只有 omap 写它）
```

| 状态 | 字符串 | 说明 |
|---|---|---|
| `RPROC_OFFLINE` | `offline` | 已注册，未启动 |
| `RPROC_SUSPENDED` | `suspended` | **arm64 上不会出现**（02 §10） |
| `RPROC_RUNNING` | `running` | 只表示 `ops->start` 成功返回（07 §7.3） |
| `RPROC_CRASHED` | `crashed` | 崩溃已上报，恢复可能正在进行 |
| `RPROC_DELETED` | `deleted` | `rproc_del()` 之后 |
| `RPROC_ATTACHED` | `attached` | 接管了一个别人启动的远端 |
| `RPROC_DETACHED` | `detached` | 远端在跑，但内核不管它 |
| （越界） | `invalid` | `state_show()` 的钳制值（04 §4.2） |

字符串表在 `drivers/remoteproc/remoteproc_sysfs.c:166-175`，
读法 `cat /sys/class/remoteproc/remoteprocN/state`。

---

## §4 数据结构速览

### §4.1 `struct rproc` 的关键字段（完整版见 02 §2）

| 分组 | 字段 | 要点 |
|---|---|---|
| 身份 | `name` · `index` · `dev` | `dev` 挂在 `rproc_class` 上，**不绑 driver** |
| 平台钩子 | `ops` · `priv` | `priv` 就是 `rproc_alloc()` 尾部那块空间（05 §1.3） |
| 固件 | `firmware` · `bootaddr` · `elf_class` · `elf_machine` | `elf_class` 初值 `ELFCLASSNONE`，只有 `rproc_coredump_set_elf_info()` 会改（04 §3.1） |
| 资源表 | `table_ptr` · `cached_table` · `clean_table` · `table_sz` | 三个指针，最多同时有两个非空（08 §4） |
| 链表 | `carveouts` · `mappings` · `traces` · `rvdevs` · `subdevs` | 五条链表 |
| 引用计数 | `power`（atomic）· `dev` 的 kref | **两套，完全独立**（03 §B2） |
| 锁 | `lock`（mutex） | 只有公开 API 拿它（03 §B1） |
| 恢复 | `crash_handler`（work）· `crash_cnt` · `recovery_disabled` | `crash_cnt` **没有 sysfs 文件**（07 §5.2） |
| coredump | `dump_segments` · `dump_conf` | 默认值合起来 = 什么都不做（04 §3.1） |
| virtio | `notifyids`（idr）· `max_notifyid` | notifyid 由 idr 分配后回写资源表 |
| 自动启动 | `auto_boot` | 默认 `true`（`drivers/remoteproc/remoteproc_core.c:2457`） |

### §4.2 `struct rproc_ops`：17 个回调，3 个必填

| 回调 | 必填？ | 干什么 |
|---|---|---|
| `start` | **是** | 放开复位 |
| `stop` | **是**（否则不能停、不能恢复） | 按下复位 |
| `da_to_va` | 视情况 | 核心的 carveout 链表查不到时兜底 |
| `kick` | 有 `RSC_VDEV` 时必须 | 敲门铃 |
| `prepare` / `unprepare` | 否 | 启动前/后的时钟电源准备。⚠️ **attach 路径不跑**（08 §5） |
| `attach` / `detach` | 否 | 接管 / 交还一个已启动的远端 |
| `load` / `parse_fw` / `sanity_check` / `get_boot_addr` / `find_loaded_rsc_table` | 否 | 不填则五个一起由 ELF 默认实现补上，**判据只看 `load`**（04 §2.2） |
| `get_loaded_rsc_table` | attach 场景需要 | 告诉核心远端内存里那份资源表在哪 |
| `handle_rsc` | 否 | 处理厂商资源。⚠️ `RSC_IGNORED` 陷阱（03 §C2） |
| `panic` | 否 | 返回"给我多少毫秒"（08 §7） |
| `coredump` | 否 | 覆盖默认的 coredump 行为 |

定义在 `include/linux/remoteproc.h:385-406`。

### §4.3 `struct rproc_mem_entry`（carveout）

| 字段 | 视角 | 说明 |
|---|---|---|
| `da` | 远端 | 远端看到的地址 |
| `va` | 内核 | 内核虚拟地址 |
| `dma` | 总线 | DMA 地址 |
| `len` | —— | 长度 |
| `name[32]` | —— | **32 字节上限，超长静默截断**（05 §3.1） |
| `alloc` / `release` | —— | 延迟分配/释放回调 |
| `of_resm_idx` | —— | ≥0 表示来自 DT 的 reserved-memory；`rproc_mem_entry_init()` 建的是 **0**（05 §3.3） |
| `is_iomem` | —— | 这块是不是 `ioremap` 来的 |

### §4.4 全局变量（只有四个）

| 变量 | 类型 | 保护 |
|---|---|---|
| `rproc_list` | 链表 | `rproc_list_mutex` + **RCU**（panic 路径要用，03 §B1.1） |
| `rproc_list_mutex` | mutex | —— |
| `rproc_dev_index` | ida | 自身原子 |
| `rproc_dbg` | dentry | debugfs 根目录 |

加上 `rproc_class`（`drivers/remoteproc/remoteproc_sysfs.c:257`）
和 `rproc_recovery_wq`。全套文档里所有"全局状态"就这些。

---

## §5 机制速览

### §5.1 十个机制，一句话各一条

| # | 机制 | 一句话 | 详见 |
|---|---|---|---|
| 1 | **注册** | `rproc_alloc()` → 填 ops → `rproc_add()`；devm 版会自动收尾 | 03 §A1-A5 · 05 §1 |
| 2 | **固件加载** | `request_firmware` → sanity → 解析资源表 → 逐段 memcpy → 放复位 | 03 §A6 |
| 3 | **资源表处理** | 两遍扫描：第一遍建 carveout，第二遍处理其余（含回写 vring 地址） | 03 §A7 |
| 4 | **地址转换** | 先问驱动，再线性扫 carveout 链表 | 03 §B4 · 08 §6 |
| 5 | **virtio 三级跳** | rproc → 平台设备 → virtio 设备，中间那层是为了 DMA 属性 | 04 §1 · 08 §3 |
| 6 | **门铃** | 出：`ops->kick`；入：mailbox 回调里调 `rproc_vq_interrupt()` | 05 §2.2 |
| 7 | **崩溃恢复** | `rproc_report_crash()` → 工作队列 → stop → 重新 load → start | 03 §A9 |
| 8 | **attach/detach** | 接管别人启动的远端；判据是 `cached_table` 是否为空 | 03 §B3 · 08 §5 |
| 9 | **coredump** | 三种模式，但**默认三个条件都不满足 ⇒ 什么都不做** | 04 §3 |
| 10 | **panic 通知** | RCU 遍历 + `mdelay` 忙等，单向无确认 | 04 §8 · 08 §7 |

### §5.2 两把锁 + 一个 RCU

| 保护对象 | 机制 | 要点 |
|---|---|---|
| 单个 `rproc` 的状态 | `rproc->lock`（mutex） | 只有公开 API 拿它；用 `mutex_lock_interruptible()` |
| `rproc_list` 写侧 | `rproc_list_mutex` | —— |
| `rproc_list` 读侧 | **RCU** | panic 上下文不能睡（08 §7） |
| ⚠️ `carveouts` 链表 | **没有锁** | `rproc_da_to_va()` 裸遍历（08 §6） |

### §5.3 四条"默认值决定行为"的链

这四条是本子系统最容易踩的坑，都是"你什么都不做时的默认行为不是你想的那样"：

| 链 | 默认值 | 后果 |
|---|---|---|
| `auto_boot = true`（`drivers/remoteproc/remoteproc_core.c:2457`） | 开 | `rproc_add()` 之后立刻尝试加载固件；rootfs 没挂好就报一句错然后待机 |
| `dump_conf = RPROC_COREDUMP_DISABLED`（枚举第 0 项） | 关 | coredump 不出 |
| `elf_class = ELFCLASSNONE`（`drivers/remoteproc/remoteproc_core.c:2458`） | 未设 | 即使开了 coredump 也会在 `Elf class is not set` 处返回 |
| `dump_segments` 空链表 | 空 | 同上，而且**这一条不打日志** |

⇒ 三条合起来：**默认情况下崩溃恢复时一个 dump 都不会产生**（04 §3.1）。

### §5.4 三个静默失效（没有任何日志）

| 现象 | 原因 | 详见 |
|---|---|---|
| rpmsg 不通，dmesg 干净 | vdev buffer 落在了父节点 `memory-region` 索引 0，返回值都没检查 | 06 §3.3 分支 C |
| `rproc_add()` 返回 `-EINVAL`，dmesg 干净 | `ops->start`（或 `ops->attach`）没填，`rproc_validate()` 全程静默 | 07 §6.2 组 C |
| carveout 明明登记了却查不到 | 名字超过 31 字符被截断 | 05 §3.1 |

**遇到"没有任何日志"的问题，先查这三条。**

---

## §6 端到端调用路径

### §6.1 路径一：注册 + 自动启动（最常见）

```
 平台驱动 probe
   │
   ├─ rproc_of_parse_firmware()          读 DT 的 firmware-name（可选）
   ├─ devm_rproc_alloc()                 分配 rproc + 私有数据
   │    └─ rproc_alloc()
   │         ├─ rproc_alloc_firmware()   记下固件名
   │         ├─ rproc_alloc_ops()        ops->load 为空 ⇒ 补五个 ELF 默认回调
   │         ├─ auto_boot = true         默认开
   │         ├─ elf_class = ELFCLASSNONE
   │         └─ device_initialize()      建 struct device，挂 rproc_class
   │
   ├─（驱动自己取时钟/复位/mailbox/内存）
   │
   └─ devm_rproc_add()
        └─ rproc_add()
             ├─ rproc_validate()          检查 ops 齐不齐（静默 -EINVAL）
             ├─ device_add()               → sysfs 五个属性出现
             ├─ rproc_create_debug_dir()   → debugfs 六个文件出现
             ├─ 加进 rproc_list（持 rproc_list_mutex）
             └─ auto_boot ⇒ request_firmware_nowait() ──▶ 异步走路径二
```

| 关键函数 | 位置 |
|---|---|
| `rproc_alloc()` | `drivers/remoteproc/remoteproc_core.c:2443` |
| `devm_rproc_alloc()` | `drivers/remoteproc/remoteproc_core.c:2603` |
| `rproc_alloc_ops()`（五个 ELF 默认回调） | `drivers/remoteproc/remoteproc_core.c:2397` |
| `auto_boot = true` | `drivers/remoteproc/remoteproc_core.c:2457` |
| `rproc_validate()` | `drivers/remoteproc/remoteproc_core.c:2212` |
| `rproc_add()` | `drivers/remoteproc/remoteproc_core.c:2269` |
| `devm_rproc_add()` | `drivers/remoteproc/remoteproc_core.c:2332` |
| `request_firmware_nowait` 失败日志 | `drivers/remoteproc/remoteproc_core.c:1700` |

### §6.2 路径二：启动（`rproc_boot()`）

```
 rproc_boot()                             ← sysfs "echo start" 或 auto_boot
   ├─ atomic_inc_return(&rproc->power)     已经在跑就直接返回
   ├─ try_module_get()                     失败 → -ENODEV
   ├─ mutex_lock_interruptible(&rproc->lock)
   └─ rproc_fw_boot()
        ├─ request_firmware()
        ├─ ops->sanity_check()             默认 rproc_elf_sanity_check()
        ├─ rproc_enable_iommu()            没 IOMMU 就跳过
        ├─ ops->parse_fw()                 默认 rproc_elf_load_rsc_table()
        │                                   ⇒ cached_table = kmemdup(固件里那份)
        ├─ rproc_handle_resources(第 1 遍)  只处理 RSC_CARVEOUT
        ├─ rproc_handle_resources(第 2 遍)  处理 DEVMEM / TRACE / VDEV / 厂商
        │    └─ rproc_handle_vdev()        建平台设备 → rproc_virtio_probe()
        │         └─ rproc_alloc_vring()   idr_alloc 拿 notifyid，回写资源表
        ├─ ops->load()                     默认 rproc_elf_load_segments()
        ├─ ops->get_boot_addr()            默认 rproc_elf_get_boot_addr()
        └─ rproc_start()
             ├─ table_ptr 切到远端内存那份 + memcpy(cached_table 的内容)
             ├─ rproc_prepare_subdevices() 正序
             ├─ ops->start()               放开复位，远端开始跑
             ├─ rproc_start_subdevices()   正序
             │    └─ rproc_vdev_do_start()
             │         └─ rproc_add_virtio_dev()
             │              ├─ 检查 ops->kick 是否存在（缺则失败）
             │              ├─ 处理 vdev buffer（三个分支，06 §3.3）
             │              └─ register_virtio_device()  → rpmsg 认领
             └─ state = RPROC_RUNNING
```

| 关键函数 | 位置 |
|---|---|
| `rproc_boot()` | `drivers/remoteproc/remoteproc_core.c:1907` |
| `rproc_fw_boot()` | `drivers/remoteproc/remoteproc_core.c:1375` |
| `rproc_handle_resources()` | `drivers/remoteproc/remoteproc_core.c:1018` |
| `rproc_handle_vdev()` | `drivers/remoteproc/remoteproc_core.c:475` |
| `rproc_alloc_vring()`（notifyid 回写） | `drivers/remoteproc/remoteproc_core.c:381` |
| 五个 ELF 默认回调 | `drivers/remoteproc/remoteproc_elf_loader.c:37`（sanity）· `:128`（boot_addr）· `:160`（load_segments）· `:326`（load_rsc_table）· `:372`（find_loaded_rsc_table） |
| `rproc_virtio_probe()` | `drivers/remoteproc/remoteproc_virtio.c:494` |
| `rproc_vdev_do_start()` | `drivers/remoteproc/remoteproc_virtio.c:476` |
| `rproc_add_virtio_dev()` | `drivers/remoteproc/remoteproc_virtio.c:370` |
| kick 检查 | `drivers/remoteproc/remoteproc_virtio.c:378-382` |
| `register_virtio_device()` | `drivers/remoteproc/remoteproc_virtio.c:446` |

**这条路径上最容易失败的三处**（按发生概率排）：

| 处 | 症状 | 去哪 |
|---|---|---|
| `rproc_validate()` | `-EINVAL`，无日志 | 07 §6.2 组 C |
| `rproc_add_virtio_dev()` 的 kick 检查 | `.kick method not defined for %s` | 04 §1.5 |
| vdev buffer 三分支 | 无日志，rpmsg 不通 | 06 §3.3 |

### §6.3 路径三：数据通路（rpmsg 发一条消息）

```
 你的 rpmsg_driver 调 rpmsg_send()
   │
   ├─ virtio_rpmsg_bus 把消息塞进 TX vring（共享内存）
   ├─ virtqueue_kick()
   │    └─ rproc_virtio_notify()          ← remoteproc 提供的 notify 实现
   │         └─ rproc->ops->kick(rproc, notifyid)
   │              └─ 驱动：mbox_send_message()  敲门铃
   │
   ▼  远端收到中断，读 vring，处理，回一条消息，敲回门铃
   │
   ├─ 驱动的 mailbox rx 回调
   │    └─ rproc_vq_interrupt(rproc, vqid)
   │         ├─ idr_find(&rproc->notifyids, vqid)
   │         ├─ 找不到 / vq 未建 ⇒ IRQ_NONE
   │         └─ vring_interrupt()
   │              └─ virtio_rpmsg_bus 的 rx 回调
   │                   └─ 你的 rpmsg_driver 的 .callback
```

| 关键函数 | 位置 |
|---|---|
| `rproc_virtio_notify()` | `drivers/remoteproc/remoteproc_virtio.c:65` |
| `rproc_vq_interrupt()` | `drivers/remoteproc/remoteproc_virtio.c:89` |
| `idr_find` + 双重判空 | `drivers/remoteproc/remoteproc_virtio.c:95-97` |
| `rpmsg_send()` 声明 | `include/linux/rpmsg.h:180` |

**注意 remoteproc 在这条路上只出现两次**：`ops->kick` 和 `rproc_vq_interrupt()`。
中间的 vring 编解码全是 virtio 和 rpmsg 的事（08 §1）。

### §6.4 路径四：崩溃恢复

```
 驱动检测到崩溃（看门狗中断 / IOMMU fault / 远端报错）
   └─ rproc_report_crash(rproc, type)
        ├─ pm_stay_awake(dev->parent)     防止恢复期间系统睡下去
        ├─ dev_err "crash detected in %s: type %s"
        └─ queue_work(rproc_recovery_wq, &rproc->crash_handler)
             │
             ▼（工作队列上下文）
        rproc_crash_handler_work()
             ├─ mutex_lock(&rproc->lock)
             ├─ state 已是 CRASHED ⇒ return（跳过 pm_relax！03 §C3）
             ├─ state == OFFLINE ⇒ goto out
             ├─ state = RPROC_CRASHED；dev_err "handling crash #%u in %s"
             ├─ mutex_unlock                先解锁再恢复
             ├─ !recovery_disabled ⇒ rproc_trigger_recovery()
             │    ├─ 复查 state == CRASHED（因为刚才解过锁）
             │    ├─ ops->coredump()        默认 rproc_coredump()（多数情况什么都不做）
             │    ├─ rproc_stop()
             │    └─ rproc_boot_recovery() 或 rproc_attach_recovery()
             └─ out: pm_relax(dev->parent)
```

| 关键函数 | 位置 |
|---|---|
| `rproc_report_crash()` | `drivers/remoteproc/remoteproc_core.c:2677` |
| `pm_stay_awake()` | `drivers/remoteproc/remoteproc_core.c:2685` |
| `rproc_crash_handler_work()` | `drivers/remoteproc/remoteproc_core.c:1862` |
| ⚠️ 跳过 `pm_relax` 的那条 `return` | `drivers/remoteproc/remoteproc_core.c:1871-1874` |
| `pm_relax()` | `drivers/remoteproc/remoteproc_core.c:1893` |
| `rproc_trigger_recovery()` | `drivers/remoteproc/remoteproc_core.c:1830` |
| 解锁后的复查 | `drivers/remoteproc/remoteproc_core.c:1840` |
| `rproc_boot_recovery()` / `rproc_attach_recovery()` | `drivers/remoteproc/remoteproc_core.c:1790` / `:1779` |

### §6.5 路径五：拆除

```
 driver detach（或显式调用）
   ├─ rproc_del()
   │    ├─ state == RUNNING ⇒ rproc_shutdown()
   │    ├─ state == ATTACHED ⇒ rproc_detach()   返回值被丢弃（03 §C4）
   │    ├─ state = RPROC_DELETED
   │    ├─ rproc_delete_debug_dir()
   │    ├─ 从 rproc_list 摘掉 + synchronize_rcu()
   │    └─ device_del()
   └─ rproc_free()
        └─ put_device(&rproc->dev) → rproc_type.release → 真正 kfree
```

| 关键函数 | 位置 |
|---|---|
| `rproc_del()` | `drivers/remoteproc/remoteproc_core.c:2556` |
| 丢弃 `rproc_detach()` 返回值 | `drivers/remoteproc/remoteproc_core.c:2562` |
| `synchronize_rcu()` | `drivers/remoteproc/remoteproc_core.c:2576` |
| `rproc_free()` | `drivers/remoteproc/remoteproc_core.c:2519` |

---

## §7 子系统关系

### §7.1 remoteproc 依赖谁

```
                    ┌──────────────┐
                    │  remoteproc  │
                    └──────┬───────┘
       ┌───────────┬───────┼────────┬───────────┬──────────┐
       ▼           ▼       ▼        ▼           ▼          ▼
   firmware_    virtio   IOMMU   reserved-   devcoredump  driver
   class                （可选）   memory     （可选）      core
   （必需）      （必需）                                  （platform）
```

| 依赖 | 强度 | Kconfig | 说明 |
|---|---|---|---|
| `FW_LOADER` | 必需（`select`） | 自动 | 加载 ELF |
| `VIRTIO` | 必需（`select`） | 自动 | 建 virtio 设备 |
| `CRC32` | 必需（`select`） | 自动 | —— |
| `WANT_DEV_COREDUMP` | **只是"想要"** | 真开关是 `DEV_COREDUMP` | 07 §1.1 |
| `MAILBOX` | 各驱动自己 `select` | 平台相关 | 门铃 |
| IOMMU | 可选 | 平台相关 | 没有就跳过（`-ENOSYS` 那句注释，03 §B5） |
| reserved-memory | 实际必需 | `OF_RESERVED_MEM` | carveout 的来源 |

### §7.2 谁依赖 remoteproc

| 使用者 | 关系 | 边界 |
|---|---|---|
| **rpmsg** | 通过 virtio 间接依赖 | remoteproc 建 virtio 设备，`virtio_rpmsg_bus` 认领它 |
| **rproc_srm**（部分平台） | 通过 subdev 机制 | —— |
| **各平台驱动**（34 个） | 直接调 `rproc_alloc()` 家族 | `remoteproc_internal.h` **只被 `drivers/remoteproc/` 里的 28 个 `.c` 包含，目录外 0 个** |

### §7.3 和 mailbox / clk / reset / power-domain 的关系

remoteproc **核心不碰这四个**。全部由平台驱动自己在 `prepare`/`start` 里处理：

| 子系统 | 谁调 | 例子 |
|---|---|---|
| mailbox | 驱动 | `drivers/remoteproc/stm32_rproc.c:382`、`drivers/remoteproc/imx_rproc.c:680` |
| clk | 驱动 | `drivers/remoteproc/meson_mx_ao_arc.c:211` |
| reset | 驱动 | `drivers/remoteproc/meson_mx_ao_arc.c:204` |
| power-domain | DT + 驱动模型自动 | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:307` |

⇒ **这解释了为什么 34 个驱动加起来 25252 行、而核心只有 2770 行**：
硬件差异全部被推到驱动层了。

### §7.4 对比：remoteproc 是框架型，不是总线型

| | i2c / spi（总线型） | remoteproc（框架型） |
|---|---|---|
| `struct bus_type` | 有 | **没有** |
| 设备怎么产生 | DT 子节点 → 总线枚举 | 平台驱动 `rproc_add()` 一个个建 |
| driver 怎么匹配 | `of_match_table` / `id_table` | **不匹配** —— rproc 的 `dev` 不绑 driver |
| 消费者怎么用 | `i2c_transfer()` 等 API | 消费者是 **rpmsg 驱动**，走另一条总线 |

判据可重跑：
```bash
grep -c 'struct bus_type' drivers/remoteproc/*.c include/linux/remoteproc.h   # 全为 0
```

---

## §8 导读：我该看哪一篇

### §8.1 按问题找篇目

| 我想知道 | 去哪篇 |
|---|---|
| 有哪些文件、哪些对外 API、错误码什么意思 | **01** |
| 结构体长什么样、字段之间怎么串、锁和全局变量 | **02** |
| 注册/启动/停止/恢复的完整时间线，横切机制，源码设计债 | **03** |
| virtio / ELF / coredump / sysfs / debugfs / cdev / trace / panic 这七块 | **04** |
| 我要动手写代码（平台驱动 / rpmsg 客户端 / 用户态） | **05** |
| DTS 怎么写、每条属性谁消费、配错了怎么定位 | **06** |
| 编译开关、动态调试、故障注入、错误码分诊、一页速查 | **07** |
| 「我理解不了 X」「为什么是这样」 | **08** |
| 这些结论怎么来的、哪些没验证 | **99** |

### §8.2 按角色推荐阅读顺序

| 角色 | 顺序 |
|---|---|
| **第一次接触** | 00 → 08 → 02 → 03 |
| **要写平台驱动** | 00 §2 → 05 §1-§3 → 03 Part A → 06 |
| **要写 rpmsg 客户端** | 08 §1 → 05 §4 → 04 §1 |
| **在排查问题** | 07 §8 速查 → 07 §6 错误码 → 06 §8 错误映射 |
| **要评审代码** | 03 Part C（设计债）→ 04 §3.1 → 06 §3.3 |
| **要复核结论** | 99 |

### §8.3 只记五条的话

1. **remoteproc / virtio / rpmsg 是叠起来的三层**，remoteproc 没有自己的 bus_type（§1.3 · 08 §1）。
2. **资源表是固件 ELF 里的一个 section，而且内核会往里回写**（§3.2 · 08 §2）。
3. **`rproc_ops` 只有 `start` / `stop` 必填**；不填 `load` 会自动补五个 ELF 回调，**全有或全无**（§4.2 · 04 §2.2）。
4. **四条默认值链**决定了"你什么都不做时"的行为 —— 尤其是 `auto_boot = true` 和 coredump 默认什么都不做（§5.3）。
5. **三个静默失效**：vdev buffer 落错内存、`rproc_validate()` 静默 `-EINVAL`、carveout 名字截断（§5.4）。

---

**取证与未验证项**：见 99 篇。
