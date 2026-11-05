---
title: 取证附录
---

# I2C 子系统（附录）取证记录

> 这一篇不讲 i2c，讲**前面八篇的每条论断是怎么来的**，以及**哪些没验证**。
> 目的有两个：一是让你能独立复核而不必信任我；二是把方法固化下来，用于其它模块。

---

## 1. 代码基线核对

### 1.1 版本与树状态

```
$ git log --oneline -1
f6e38ae624cf Linux 6.1.158

$ cat localversion-rt
-rt58                              ← 未纳入 git 跟踪（?? localversion-rt）

$ git status --porcelain | grep -c '^ M'
175                                ← 175 个已跟踪文件被修改（PREEMPT_RT 补丁）

$ git status --porcelain | grep -v '^ M'
?? localversion-rt
?? output/                         ← 本文档集自身
```

**这是一棵打了 PREEMPT_RT 补丁但尚未提交的 6.1.158 树。**

### 1.2 关键结论：i2c 未被 RT 补丁触碰

```
$ git status --porcelain -- drivers/i2c include/linux/i2c.h \
      'include/linux/i2c-*.h' 'include/uapi/linux/i2c*.h' \
      'include/trace/events/i2c*.h' include/trace/events/smbus.h \
      Documentation/i2c
（无输出）
```

**空输出 = i2c 子系统的每一个文件都与上游 6.1.158 完全一致。**

因此本文档集描述的是**纯净的上游代码**，不含任何 RT 补丁的私有改动。`00-overview.md` 开头和 `03-internals.md` §B1.4 的两处声明都以此为依据。

**这条验证很重要，因为它推翻了一个自然的猜测**：`bus_lock` / `mux_lock` 是 `struct rt_mutex`（`include/linux/i2c.h:756-757`），初看很容易以为是 RT 补丁改的。事实是**上游 6.1 mainline 本来就是 `rt_mutex`** —— 为的是优先级继承：一个低优先级线程持有 I²C 总线锁时，高优先级线程等在上面，需要把优先级传递下去，否则实时任务会被无限期阻塞。这与 CONFIG_PREEMPT_RT 无关。

RT 补丁对 i2c 的影响是**间接的**（通过改变别的东西）：

| 间接影响 | 机制 |
|---|---|
| 中断处理变成线程 | RT 下 `request_irq()` 默认走 threaded IRQ，控制器驱动的 ISR 从硬中断上下文变成内核线程 |
| spinlock 变成可睡眠 | 控制器驱动里的 `spin_lock()` 在 RT 下是 rtmutex，改变了原本"持锁不睡眠"的假设 |
| `!preemptible()` 更少为真 | `i2c_in_atomic_xfer_mode()`（`drivers/i2c/i2c-core.h:31`）在 `CONFIG_PREEMPT_COUNT` 下判断 `!preemptible()`；RT 下抢占点更多，同一段代码更可能被判为"非原子"，从而走睡眠路径 |

这三条我**没有在本树上实验验证**，是从 RT 的通用语义推出的 —— 列为未验证项，见 §5。

### 1.3 规模统计的复现方式

```
$ wc -l drivers/i2c/*.c drivers/i2c/*.h            → 7,326（核心）
$ ls drivers/i2c/busses/*.c | wc -l                → 133
$ cat drivers/i2c/busses/*.c | wc -l               → 89,120
$ ls drivers/i2c/muxes/*.c  | wc -l                → 10
$ cat drivers/i2c/muxes/*.c | wc -l                → 2,852
$ cat drivers/i2c/algos/*.c | wc -l                → 1,679
```

逐文件行数（**只留在本篇**：它的用处是"决定哪些文件要全读、哪些只能读符号表"，对读文档的人没有价值，
所以 [`01-api.md`](01-api.md) §0 的表里只留文件名和职责，不写行数）：

| 文件 | 行 | | 文件 | 行 |
|---|---|---|---|---|
| `i2c-core-base.c` | 2,687 | | `i2c-core-of.c` | 220 |
| `i2c-core-acpi.c` | 847 | | `i2c-slave-eeprom.c` | 217 |
| `i2c-dev.c` | 782 | | `i2c-slave-testunit.c` | 192 |
| `i2c-core-smbus.c` | 723 | | `i2c-core-slave.c` | 127 |
| `i2c-mux.c` | 466 | | `i2c-core.h` | 99 |
| `i2c-smbus.c` | 460 | | `i2c-boardinfo.c` | 93 |
| `i2c-stub.c` | 413 | | | |
| `algos/i2c-algo-bit.c` | 695 | | `algos/i2c-algo-pca.c` | 561 |
| `algos/i2c-algo-pcf.c` | 423 | | | |
| `include/linux/i2c.h` | 1,092 | | `include/uapi/linux/i2c.h` | 164 |
| `include/linux/i2c-algo-pca.h` | 87 | | `include/linux/i2c-mux.h` | 66 |
| `include/uapi/linux/i2c-dev.h` | 60 | | `include/linux/i2c-smbus.h` | 52 |
| `include/linux/i2c-algo-bit.h` | 43 | | `include/linux/i2c-algo-pcf.h` | 37 |
| `include/linux/i2c-dev.h` | 15 | | | |

---

## 2. 方法：证据是怎么取的

### 2.1 完整通读的文件（论断的一等证据）

以下文件**逐行读完**，本文档集里对它们的一切描述都是直接观察，不是推断：

| 文件 | 行数 | 用于 |
|---|---|---|
| `include/linux/i2c.h` | 1,092 | `01-api.md` 全篇、`02-datastructures.md` 全篇 |
| `include/uapi/linux/i2c.h` | 164 | `01` §1/§3、`02` §2.11 |
| `include/uapi/linux/i2c-dev.h` | 60 | `01` §10、`02` §2.12 |
| `drivers/i2c/i2c-core.h` | 99 | `03` §A5/§B1 |
| `drivers/i2c/i2c-core-base.c` | 2,687 | `03` 全篇 |
| `drivers/i2c/i2c-core-smbus.c` | 723 | `03` §A6 |
| `drivers/i2c/i2c-core-slave.c` | 127 | `04` §3.1 |
| `drivers/i2c/i2c-core-of.c` | 220 | `04` §6.1、`05` §2 |
| `drivers/i2c/i2c-mux.c` | 466 | `04` §1 |
| `include/linux/i2c-mux.h` | 66 | `04` §1、`05` §5 |
| `drivers/i2c/i2c-dev.c` | 782 | `04` §4、`05` §7 |
| `drivers/i2c/i2c-boardinfo.c` | 93 | `04` §6.3 |
| `drivers/i2c/i2c-slave-eeprom.c` | 217 | `04` §3.3、`05` §6 |
| `drivers/i2c/i2c-slave-testunit.c` | 192 | `04` §3.4 |
| `include/linux/i2c-algo-bit.h` | 43 | `04` §2.1、`05` §4.3 |
| `include/linux/i2c-algo-pca.h` | 87 | `04` §2.2 |
| `include/linux/i2c-algo-pcf.h` | 37 | `04` §2.3 |
| `include/linux/i2c-smbus.h` | 52 | `04` §5 |
| `include/trace/events/i2c.h` | 146 | `03` §B4、`07` §2 |
| `include/trace/events/smbus.h` | 245 | `07` §2（含 4 个 `TP_CONDITION`） |
| `include/trace/events/i2c_slave.h` | 67 | `04` §3、`07` §2 |
| `drivers/i2c/Kconfig` / `algos/Kconfig` / `muxes/Kconfig` / `busses/Kconfig` | — | `01` §0.4、`04` §8、`07` §5.1 |
| `drivers/i2c/Makefile` / `algos/Makefile` / `busses/Makefile` | — | `07` §1 |

### 2.2 部分读 + 符号表的文件（二等证据）

以下文件我**只读了关键函数体，其余靠符号表（函数名 + 起始行）**。对它们的描述里，"某函数在第 N 行"是可靠的，"某函数内部怎么做"只在我明确读过的那几个函数上可靠：

| 文件 | 详读的部分 | 只有符号位置的部分 |
|---|---|---|
| `algos/i2c-algo-bit.c` (695) | `:531-695`（`bit_xfer` / `bit_xfer_atomic` / `bit_func` / `i2c_bit_algo` / `__i2c_bit_add_bus`）；**2026-08-14 补读** `:17-44`（`i2c_debug` / `bit_test` 的 `module_param` 与 `#ifdef DEBUG` 边界）、`:229-301`（`test_bus()` 全函数）、`:430-450`（`-EPROTO` 判定） | `sdalo`…`bit_doAddress`（`:53`-`:228`、`:302`-`:429`）只有名字和行号 |
| `algos/i2c-algo-pca.c` (561) | — | 全部只有符号表；`pca_xfer` 是"状态机"这一判断来自函数长度（`:182-356`）和 PCA9564 手册常识，**不是逐行读出来的** |
| `algos/i2c-algo-pcf.c` (423) | — | 同上；`handle_lab` 处理丢失仲裁这一判断来自函数名 + `lab_mdelay` 字段注释 |
| `i2c-core-acpi.c` (847) | 只读了符号表 | 除 OpRegion handler 安装顺序（与 `i2c-core-base.c:1589-1590` 交叉印证）外，本篇对 ACPI 的描述都是**从函数名和头文件注释推断的** |
| `i2c-smbus.c` (460) | 只读了符号表 | Alert / Host Notify / SPD 三段机制的描述来自符号名 + `include/linux/i2c-smbus.h` 的注释 + `i2c-core-base.c` 里的调用点 |
| `i2c-stub.c` (413) | 只读了符号表 + 模块参数定义（`:20-60`） | `stub_xfer`（`:123-297`）内部逻辑未逐行读 |
| `busses/i2c-gpio.c` | **`:74-301` 全段读完**（整个 `CONFIG_I2C_GPIO_FAULT_INJECTOR` 块：六个 fops、`WIRE_ATTRIBUTE` 宏、注入器 init/exit）；另读 `:313-315`、`:398-420`（引脚申请与两个废弃布尔属性） | 其余（普通 probe/xfer 路径）未读 |

### 2.3 内核文档（三等证据，但对"设计意图"最权威）

| 文档 | 读的范围 | 用于 |
|---|---|---|
| `Documentation/i2c/fault-codes.rst` | `:42-130` 全文逐字 | `01` §9、`07` §7 的错误码表 |
| `Documentation/i2c/i2c-topology.rst` (412) | mux-locked 段 `:60-122`、parent-locked 段 `:126-187` | `04` §1.4/§1.5、`07` §8.5 |
| `Documentation/i2c/gpio-fault-injection.rst` (136) | `:1-136` 全文逐字（2026-08-14 自审时从"只详读 `:12-75`"升级为全文） | `07` §5 |
| `Documentation/i2c/dma-considerations.rst` (71) | `:1-34` | `01` §8、`04` §4.3 |
| `Documentation/i2c/dev-interface.rst` (221) | 结构浏览 | `05` §7 |
| `Documentation/i2c/instantiating-devices.rst` (272) | 结构浏览，"last resort" 一语出自其中 | `05` §3 |
| `Documentation/i2c/writing-clients.rst` (428) | 结构浏览 | `05` §1 |
| `Documentation/i2c/smbus-protocol.rst` (324) | 结构浏览 | `01` §2 |
| `Documentation/i2c/slave-interface.rst` (201) | 结构浏览 | `04` §3.2 |
| `Documentation/i2c/functionality.rst` (156) | 结构浏览 | `01` §3 |
| `Documentation/i2c/i2c-sysfs.rst` (387) / `i2c-protocol.rst` (96) / `ten-bit-addresses.rst` / `summary.rst` / `i2c-stub.rst` / `slave-*-backend.rst` / `old-module-parameters.rst` | 只做了清单，未详读 | 未直接引用 |

**注意三等证据的风险**：内核文档偶尔落后于代码。本文档集里凡是文档与代码可能冲突的地方（例如 `07` §4 里 `i2c-gpio.c:261-265` 那条"将来会挪进 adapter debugfs"的注释，实际上 `adap->debugfs` 已经存在了），我都以代码为准并显式指出了不一致。

### 2.4 工具与其定位

| 工具 | 版本/位置 | 实际起的作用 |
|---|---|---|
| `Read` / `rg` / `sed` / `wc` | — | **主力**。所有落地的论断最终都靠这些直接读到 |
| `codebase-memory-mcp` (cbm) | v0.10.3，`~/.local/bin/codebase-memory-mcp`，project `workspace-safety-kidx-merged` | 建立初始的函数调用关系印象，用于**决定读哪个文件**。它的输出**没有**被直接当作论断依据 |
| overlayfs 影子目录 | `/workspace/safety/kidx/merged`（lowerdir = 真实内核树） | 索引工具会往项目根目录写文件，为了满足"绝不修改内核树"的约束，让它写到 overlay 的 upperdir 去 |
| `git status` / `git log` | — | §1 的基线核对 |

**为什么不直接信索引工具**：它有已知盲区 ——

1. **宏生成的符号查不到**。i2c 里大量存在：`TRACE_EVENT_FN` 生成的 `trace_i2c_write`、`DEVICE_ATTR_RO(name)` 生成的 `dev_attr_name` 和 `name_show`、`ATTRIBUTE_GROUPS(i2c_dev)` 生成的 `i2c_dev_groups`、`module_i2c_driver()` 生成的 `init`/`exit`、`DEFINE_DEBUGFS_ATTRIBUTE` 生成的 fops。这些在图里全部缺失。
2. **函数指针间接调用查不到**。而 i2c 的核心机制**几乎全靠函数指针**：`algo->master_xfer`、`lock_ops->lock_bus`、`slave_cb`、`muxc->select`、`bus_recovery_info->recover_bus`。也就是说，索引工具恰好在这个子系统最关键的地方失效。
3. 置信度分层：`strategy=lsp` 且 `confidence >= 0.9` 可当结论；`heuristic` 只能当线索；低于 0.5 视为噪声。

这两条盲区决定了整个方法：**索引负责导航，`rg` + `Read` 负责取证**。凡是要写进文档的因果链，一律回到源码确认。

### 2.5 交叉验证的实例

举几个"没有直接采信第一手印象"的例子，用于说明取证标准：

| 疑问 | 怎么解决的 |
|---|---|
| `bus_lock` 是 RT 补丁改成 rt_mutex 的吗？ | `git status -- include/linux/i2c.h` 空输出 → 文件未改 → 上游本来如此。见 §1.2 |
| `i2c-gpio` 的故障注入文件在 `adap->debugfs` 下吗？ | 读 `i2c-gpio.c:255-295`，发现是**全局目录**，与我的初始假设相反，改写了 `06` §4 |
| `remove` 回调返回 `int` 还是 `void`？ | 读 `include/linux/i2c.h:288` 确认是 `void`；这是 6.1 期间刚完成的迁移 |
| 探测（detect）为什么对某些地址用 byte read 而不是 quick write？ | 读到 `i2c-core-base.c:2409-2419` 的注释：quick write 会损坏 24RF08 EEPROM 的状态机、还可能不可逆地写保护某些 EEPROM，所以 `0x30-0x37` 和 `0x50-0x5f` 改用 short byte read；另有 x86 上 FSC 硬件监控芯片（地址 0x73）的特例（`:2427-2431`） |
| `-DDEBUG` 是怎么按目录生效的？ | 读 `drivers/i2c/Makefile:21` 和 `algos/Makefile:10` 的 `ccflags-$(CONFIG_I2C_DEBUG_*)` |
| 适配器/client 到底有几个 sysfs 属性？ | 读 `i2c-core-base.c:1370-1375`（适配器 3 个）和 `:691-696`（client 2 个），不靠记忆 |
| `smbus-alert` 属性是自动创建 ARA 的开关吗？ | `rg -n '"smbus-alert"' drivers/i2c/` 只命中 `i2c-stm32f7.c` → 顺着 `i2c_setup_smbus_alert` 往上读，发现真正的触发条件是 `interrupt-names` 含 `"smbus_alert"`（`i2c-smbus.c:181-182`），推翻了初始假设 |
| `i2c_parse_fw_timings()` 是核心自动调的吗？ | `rg -n 'i2c_parse_fw_timings' drivers/ include/` → 调用点全在控制器驱动里，核心侧只有定义（`i2c-core-base.c:1952`）→ 结论：**不调用**，那 8 个属性在不调它的驱动上完全是死字 |

### 2.6 全目录统计的复现命令（`00` §2.9 与 `06` §1 的数字）

**驱动能力普查**（用于 `00` §2.9 说明"控制器实现哪些回调"的分布）：

```bash
$ cd drivers/i2c/busses
$ ls *.c | wc -l                                → 133   （驱动文件总数）
$ rg -l '\.master_xfer\s*=' *.c | wc -l         → 81
$ rg -l '\.smbus_xfer\s*=' *.c | wc -l          → 30
# 两者都实现的：
$ comm -12 <(rg -l '\.master_xfer\s*=' *.c | sort) \
           <(rg -l '\.smbus_xfer\s*=' *.c | sort) | wc -l    → 4
# 只会 SMBus（不实现 master_xfer）的：
$ comm -13 <(rg -l '\.master_xfer\s*=' *.c | sort) \
           <(rg -l '\.smbus_xfer\s*=' *.c | sort) | wc -l    → 26
$ rg -l '\.master_xfer_atomic\s*=' *.c | wc -l  → 11
$ rg -l '\.reg_slave\s*=' *.c | wc -l           → 13
```

**关于这些数字要说清三件事**：

1. **26 个 smbus-only 驱动里只有 2 个可能出现在 arm64 上**（`i2c-mlxbf.c`、`i2c-xgene-slimpro.c`），其余全是 x86 南桥/PCI 设备。这是"arm64 上几乎所有控制器都实现 `master_xfer`"这句话的依据。
2. **`master_xfer_atomic`（11 个）和 `reg_slave`（13 个）经人工过目，全部是 arm/arm64 平台驱动**。这不奇怪：原子传输的动机是关机时通过 PMIC 断电，从机模式的动机是嵌入式设备被当作从机 —— 都是嵌入式场景。
3. **统计方式的局限**：`rg -l` 按**文件**计数，一个文件里有多套 `i2c_algorithm` 会被算作 1；反之 `.master_xfer =` 若被宏包裹（如某些驱动用宏批量生成 algorithm 结构）就会漏计。所以这些数字是**下界**，量级可信，个位数不必当真。

### 2.7 arm64-only 的裁剪决定与被裁掉的内容

本文档集的一条前置约束是：**涉及体系结构时只以 arm64 为基础**。据此明确裁掉的内容（记在这里，以免读者以为是遗漏）：

| 被裁掉的 | 在树里的位置 | 为什么与 arm64 无关 |
|---|---|---|
| ACPI 枚举整条链路 | `i2c-core-acpi.c`（847 行）、`i2c-core-base.c:1589-1590` | arm64 服务器虽然有 ACPI，但本文档集面向嵌入式/DT 场景；`00`/`05`/`06`/`07` 里的 ACPI 分支已全部移除，只在 `04` §6 保留"存在另一条枚举路径"这一句话级别的提及 |
| ACPI OpRegion（AML 直接访问 I²C） | `i2c-core-acpi.c` | 同上；这也正好是 §5.2 里我从未逐行读过的部分 |
| SPD/DIMM 自动注册 | `i2c-smbus.c:366-454` | 依赖 DMI 与 x86 内存控制器拓扑 |
| FSC 硬件监控芯片（0x73）探测特例 | `i2c-core-base.c:2425-2431`，用 `#ifdef CONFIG_X86` 包裹 | 编译时就不在 arm64 上；`03` §A3 只留一行说明"另有一个 x86 专属特例" |
| x86 南桥控制器（i801、piix4 等） | `busses/` 里那 26 个 smbus-only 驱动的大部分 | 见 §2.6 |

**这不是"内核没有这些东西"，而是"本文档集选择不讲"。**要看 ACPI 侧，起点是 `i2c-core-acpi.c` 的 `i2c_acpi_register_devices()`。

---

## 3. 按文档分的证据索引

### `00-overview.md`

| 论断 | 出处 |
|---|---|
| 频率档位常量表 | `include/linux/i2c.h:45-50` |
| 两套地址校验函数 | `i2c-core-base.c:751`（宽松）/ `:769`（严格） |
| `i2c_8bit_addr_from_msg()` | `include/linux/i2c.h:940` |
| SMBus 仿真入口 | `i2c-core-smbus.c:322` |
| `i2c_adapter` / `i2c_client` / `i2c_driver` 定义 | `include/linux/i2c.h:723-758` / `:334-358` / `:271-310` |
| client 命名 `%d-%04x` | `i2c-core-base.c:892` |
| 规模数字 | §1.3 |
| `CONFIG_I2C_MUX` 改变 `i2c_parent_is_i2c_adapter()` | `include/linux/i2c.h:771` |
| `slave_cb` 字段受 `CONFIG_I2C_SLAVE` 保护 | `include/linux/i2c.h:355` |
| class_compat 链接 | `i2c-core-base.c:1580` |
| `postcore_initcall` | `i2c-core-base.c:2152` |
| DT / ACPI 枚举调用点 | `i2c-core-base.c:1588` / `:1590`；OpRegion `:1589` |
| client irq 三来源 | `i2c-core-base.c:503-517` |
| pm_runtime 支持 | `i2c-core-base.c:1568-1571` |
| devres group | `i2c-core-base.c:576` |
| debugfs 根 / 适配器目录 | `i2c-core-base.c:2104` / `:1562` |
| static_key 门控 | `i2c-core-base.c:67` |
| 默认 timeout = HZ | `i2c-core-base.c:1542-1543` |
| algo-bit 把 retries 设成 3 | `algos/i2c-algo-bit.c:660` |
| 重试循环 | `i2c-core-base.c:2273-2283` |
| SMBus→I²C 退化 | `i2c-core-smbus.c:599-608` |
| `I2C_MAJOR 89` | `include/linux/i2c-dev.h:14` |

**§4「核心数据结构速览」与 §5「核心机制速览」是 `02` / `03` 的浓缩，不含新论断。**
它们引用的每一个行号都在本节的 [`02-datastructures.md`](#02-datastructuresmd) 和
[`03-internals.md`](#03-internalsmd) 两个小节里已经取证过；复核时以那两篇为准。
§4/§5 相对 `02`/`03` **只做了两件加工**：(1) 把顺序敏感的内容（初始化七步、注册时序、probe
八步、匹配三路）压成一行流程；(2) 把 `03` 散在各节的"易误解点"集中成 §5.4 的三行表。
两者都可能因压缩而丢失前提条件 —— 真要照它改代码前请回到 `03` 读完整节。

原本在 `00` §4/§5 的**代码地图与 Kconfig 表已移入 [`01-api.md`](01-api.md) §0**，
证据同 §1.3 的逐文件行数统计与 §2.1 的 Kconfig 行。

**§2「硬件与协议」的出处需要单独说明 —— 它基本不出自这棵树。**

| §2 的内容 | 出处 |
|---|---|
| 两线结构、开漏 + 上拉、线与逻辑、START/STOP/ACK/NAK、地址帧格式、时钟拉伸、仲裁、Sr（repeated START）、总线电容 400pF 的限制、上拉电阻的取值权衡 | **NXP UM10204《I²C-bus specification and user manual》+ 通用硬件工程实践**。这些是协议与电气事实，**内核代码里没有、也不该有**。本树上唯一的旁证是频率档位常量（`include/linux/i2c.h:45-50`）和时序默认值的分档方式（`i2c-core-base.c:1960-1965`），二者与规范的 Standard/Fast/Fast+/High-speed 分档一致 |
| 保留地址（0x00-0x07、0x78-0x7f）为何不可用 | 规范定义；内核侧的对应是 `i2c_check_addr_validity()`（`i2c-core-base.c:769` 严格版拒绝这些地址）与宽松版 `:751`（探测时允许） |
| SMBus 相对 I²C 多出的约束（超时、PEC、地址响应要求） | `Documentation/i2c/smbus-protocol.rst` + SMBus 3.x 规范 |
| §2.9 的 arm64 驱动能力统计 | **在本树上实测**，命令与数字见 §2.6 |

**结论**：§2 是"读代码前必须先懂的硬件背景"，性质上是**引用外部规范**，不是本树的观察。凡是与规范原文有出入的地方，以 UM10204 为准，不以本文档为准。

### `01-api.md`

§0「代码地图」（原 `00` §4/§5）：文件清单与职责逐个打开确认；Kconfig 行为逐行读
`drivers/i2c/Kconfig`、`algos/Kconfig`、`muxes/Kconfig`、`busses/Kconfig`。
表里"本篇里对应的 API / 实现剖析"两列是**编者做的映射**，不是代码里的信息 —— 属于组织方式，不属于论断。
行数不写进 §0，理由见 §1.3 上方那段说明。

所有 API 签名、参数、返回值：`include/linux/i2c.h` 与 `include/uapi/linux/i2c.h` 逐行读出。
**呈现方式是改写过的，`01` 里已经没有行号**（见 §7 方法第 12、13 条）：

- 签名只写"函数名 + 形参名"，返回类型和参数类型都不写。形参名一律取自头文件原型里的原名，
  头文件里没给名字的（只写了类型）按实现里的名字补，例如 `i2c_probe_func_quick_read(adap, addr)`、
  `i2c_bit_add_bus(adap)`。**所以表里的签名不能直接当原型抄进代码。**
- 位置精确到**文件**而不是行：每节开头的「头文件：…」行说明声明在哪个 `.h`、实现在哪个 `.c`，
  逐 API 的行号全部删掉。要跳到具体某一行，用 `03-internals.md`（主流程逐段剖析，带行号）
  或直接在那个文件里搜函数名。本篇 §4 保留了关键行号，可作查证入口。
- 三列固定为「API / 说明 / 备注」。**「备注」列是编者归纳**：其中的上下文标签
  （可睡眠 / 不可睡眠 / 原子可用）来自源码的 `Context:` 注释和实现里的锁/分配行为，
  不是内核里显式声明的属性；`inline` / `宏` 标注来自头文件里的实际形式。

错误码语义表：`Documentation/i2c/fault-codes.rst:42-130` 逐字。
ioctl 表：`include/uapi/linux/i2c-dev.h` 全文。
DMA-safe 规则：`Documentation/i2c/dma-considerations.rst:1-34`。

### `02-datastructures.md`

六张关系图（§1.0 新增两张）：由 `include/linux/i2c.h`（结构定义）+ `i2c-mux.c:32-37`（`i2c_mux_priv` 内嵌适配器）+ `i2c-dev.c:585-613`（匿名 client）三处的实际字段关系画出。
字段表：`include/linux/i2c.h` 各结构定义处。
全局变量与锁：`i2c-core-base.c`（`i2c_bus_type`、`core_lock`、IDR）+ `i2c-boardinfo.c:19-25`（`__i2c_board_lock` / `__i2c_board_list` / `__i2c_first_dynamic_bus_num`）。

**§1.0「先分清三组关系」的逐条证据**（这一节是 review 时补的，起因是"`i2c_adapter` 和 `i2c_algorithm` 是不是 device/driver 关系"这个提问）：

| 论断 | 证据 |
|---|---|
| `i2c_algorithm` 不是 driver，只是 ops 表 | `include/linux/i2c.h:545-573` 全是函数指针，结构里没有 `struct device_driver`、没有 `name`、没有 `of_match_table`；全树没有任何"注册 algorithm"的接口 |
| 一份 `i2c_algorithm` 可被多个适配器共享 | `i2c-rk3x.c:1261` `i2c->adap.algo = &rk3x_i2c_algorithm`，`rk3x_i2c_algorithm` 是文件级 `static const`，每个 probe 出来的控制器都指它 |
| 适配器的 dev 确实注册在 `i2c_bus_type` 上 | `i2c-core-base.c:1553-1556`：`dev_set_name(&adap->dev, "i2c-%d", …)` / `adap->dev.bus = &i2c_bus_type` / `adap->dev.type = &i2c_adapter_type` / `device_register(&adap->dev)` |
| 适配器永不绑定 driver | `i2c_verify_client()`（`:725-730`）只在 `dev->type == &i2c_client_type` 时返回非 NULL；`i2c_device_match()`（`:140`）和 `i2c_device_probe()`（`:490`）都以它开头，后者紧跟 `if (!client) return 0;`（`:495-496`）。`i2c_of_match_device()`（`i2c-core-of.c`）与 `i2c_match_id()`（`:104`）内部各有一条 `client == NULL` 的早退 |
| `dev.type` 两个取值的定义位置 | `i2c_client_type` 在 `:708-713`，`i2c_adapter_type` 在 `:1378-1382` |
| arm64 控制器是 platform_device/platform_driver | `i2c-rk3x.c:1404` `.of_match_table = rk3x_i2c_match`、`:1409` `module_platform_driver(rk3x_i2c_driver)` |
| 两条总线的唯一连接点是 `dev.parent` | `i2c-rk3x.c:1265` `i2c->adap.dev.parent = &pdev->dev`；核心侧不设默认值，`i2c_register_adapter()` 也不检查 |
| 控制器的 DT 子节点不会变成 platform_device | `drivers/of/platform.c:24-32` 的 `of_default_bus_match_table[]` 只有 `simple-bus` / `simple-mfd` / `isa` / `arm,amba-bus`（后者还要 `CONFIG_ARM_AMBA`），I2C 控制器 compatible 不在其中，`of_platform_populate()` 递归到此为止 |
| `adap->dev.of_node` 由驱动自己设 | `i2c-rk3x.c:1263` |
| sysfs 路径形态 | 由上面的 `dev_set_name` + `dev.parent` 推出的**必然结果**，但**本机没有 arm64 目标板，未实机 `ls` 过**（见 §5.3） |

### review 中修正的错误（原样记录）

| 位置 | 原文 | 实际 |
|---|---|---|
| `02` §4 命名表 | client 用 `info->dev_name` 时"直接用该名"，位置 `:880` | `dev_set_name(&client->dev, "i2c-%s", info->dev_name)`，**带 `i2c-` 前缀**，在 `:883`；`:880` 是同一函数里取 ACPI companion 的那行 |
| `02` §4 命名表 | modalias 就是 `i2c:<client->name>` | `i2c_device_uevent()`（`:161-175`）是三级回退：先 `of_device_uevent_modalias()`，再 `acpi_device_uevent_modalias()`，两者都 `-ENODEV` 才走 `:174` 的 `i2c:` 形式。**DT 实例化的 client 拿到的是 OF modalias** |
| `02` §1.5 | `filp->private_data = 匿名 client`，`i2c-dev.c:602` | 源码里是 `file->private_data`，`:602` 是 `kzalloc`，赋值在 `:610` |

### `03-internals.md`

`i2c-core-base.c` 全文 + `i2c-core-smbus.c` 全文 + `i2c-core.h` 全文。
`i2c_default_probe` 的两个硬件特例：`:2409-2431`。
两遍摘 client 的理由与 FIXME：`:1809-1813`。
RT 归因（§B1.4）：见本文 §1.2。

**2026-08-14 全量重排**：原来 §1–§13 的平铺结构改成
`§0 导航 / Part A 主干（A1–A8）/ Part B 横切机制（B1–B4）/ Part C 架构评价`，
起因是用户读完后的第一个问题是"这篇文档的整体组织逻辑是什么"。原因分析见 §7 第 16 条。
新旧章节号对照（外部引用已全部同步更新，见下）：

| 原 | 现 | 原 | 现 |
|---|---|---|---|
| §1 分层与职责边界 | §0.1 | §7 并发与锁 | §B1 |
| §2 初始化 `i2c_init()` | §A1 | §7.3 原子传输路径图 | §B1.3 |
| §3 头部 + §3.1 + §3.2 | §A4 | §7.4 PREEMPT_RT | §B1.4 |
| §3.3 remove / shutdown | §A8.1 | §8 总线恢复 | §A7 |
| §4.1 + §4.2 适配器注册 | §A2 | §9 设备发现 | §A3 |
| §4.3 `i2c_del_adapter()` | §A8.2 | §10 生命周期与引用计数 | §B2 |
| §5 传输主路径 | §A5 | §11 电源管理 | §B3 |
| §6 SMBus 双轨 | §A6 | §12 tracepoint | §B4 |
| §13 架构评价 | Part C |  |  |

新增内容只有 §0.2（一张总线生命周期时间线图，Part A 的地图）和 §0.3（三部分分工表 +
"哪些内容不在本篇"的去处表）；其余都是原文搬家，**没有新增未经核对的行号**。

同时做的三件事：

- **去重**：原 §12 的九个 tracepoint 清单表、`TP_printk` 格式串、`TRACE_EVENT_CONDITION`
  说明全部撤掉（`07` §2.1 已有更全的版本，多了输出格式和过滤器用法），`03` §B4 只留
  static key 机制本身（`:67` / `:72` / `:78` / `i2c.h:25` / `:2262` / `:2146`）。
- **划边界**：原 §9 节首加了一行分工声明（DT 属性清单归 `06`、`i2c-dev` 实现归 `04`、
  sysfs `new_device` 的用法归 `05`），避免同一个问题落在两篇里。
- **修同源错误**：`00` §5 的小节顺序照抄了 `03` 的错误次序（匹配与 probe 排在适配器注册
  之前），一并改成 §5.1 初始化 → §5.2 适配器注册 → §5.3 枚举 → §5.4 匹配与 probe →
  §5.5 传输 → §5.6 SMBus → §5.7 恢复 → §5.8 锁 → §5.9 观测与生命周期 → §5.10 评价，
  并给每个小节标题加上 `03` 的对应节号。

外部引用同步更新的 15 处：`00`（§A7 / §A6 / Part C / §B1）、`01`（§A6 / §A5.3+§B1 / §A3）、
`02`（§1.1 图内 `03 §7` → `03 §B1`）、`04`（Part C）、`07`（§A3 / §A4 / §A7）、
`99`（§A5+§B1 / §A6 / §A3）。核对方式：在 `output/i2c/` 下 grep 所有形如
"03 后面直接跟数字章节号"的写法（三种拼法：markdown 链接后、反引号 `03` 后、裸写 `03` 后），
结果只剩本节这段变更记录本身。

### `04-extensions.md`

mux：`i2c-mux.c` 全文 + `include/linux/i2c-mux.h` 全文 + `i2c-topology.rst:60-187`。
mux 驱动清单：`ls drivers/i2c/muxes/` + `muxes/Kconfig`，**外加全树 `grep -rn "i2c_mux_alloc("`**（第一版只看了 `muxes/` 目录，因此漏掉一半使用者并写错两条断言，见下面的自审记录）。
algos：`include/linux/i2c-algo-{bit,pca,pcf}.h` 全文 + `algos/i2c-algo-bit.c:531-695` 详读 + 另两个只有符号表（见 §2.2）。
slave：`i2c-core-slave.c` 全文 + 两个后端全文。
i2c-dev：`i2c-dev.c` 全文。
SMBus 扩展：`include/linux/i2c-smbus.h` 全文 + `i2c-smbus.c` 符号表 + `i2c-core-base.c:1440-1458`（Host Notify irq domain **拆除**）与 `:1468-1484`（**建立**）与 `:1496-1510`（事件转发）+ `i2c-core-smbus.c:693-702`（ARA 显式创建）与 `:706-722`（自动创建）。
枚举：`i2c-core-of.c` 全文 + `i2c-core-acpi.c` 符号表 + `i2c-boardinfo.c` 全文。
i2c-stub：`i2c-stub.c` 符号表 + 模块参数定义。

**2026-08-14 自审（review 04 时用户说"这块我不熟，你自己挑战一下"，于是改成逐条回验而不是答疑）**

做法：把 `04` 里所有形如 `文件:行号` 的引用（约 150 处）**逐个 `sed` 打开核对**，不抽样。
结论：绝大多数正确，而**错误全部集中在"当初图省事写了概述、没有逐字引用源码"的地方**——
这条规律本身比单个错误更有价值，已记进 §7 第 17 条。

补读的源码（原来只有符号表或根本没读）：`drivers/i2c/muxes/` 全部 10 个文件的
`i2c_mux_alloc()` 调用、`algos/i2c-algo-bit.c:53-80` 与 `:600-621`、
`algos/i2c-algo-pca.c:363-366`、`algos/i2c-algo-pcf.c:390-393`、
`i2c-core-slave.c:20-45` 与 `:105-127`、`i2c-dev.c:118-130`/`:185-235`/`:355-375`/`:649-680`、
`i2c-core-smbus.c:704-723`、`i2c-core-base.c:1435-1490` 与 `:2234-2245` 与 `i2c-core.h:28-50`、
`i2c-stub.c:65-90` 与 `:298-312`，以及全树 `i2c_mux_alloc()` 调用点涉及的 13 个驱动
（`sbs-manager.c`、`mpu3050-i2c.c` + `mpu3050-core.c:859-884`、`inv_mpu_i2c.c`、`max9286.c`、
`si2168.c`、`rtl2832.c`、`lgdt3306a.c`、`rtl2830.c`、`af9013.c`、`m88ds3103.c`、
`cx231xx-i2c.c`、`sii902x.c`、`of/unittest.c`）。

修正的错误（**含我在本次自审前半段自己新写进去、后半段又被自己推翻的两条**，标 ）：

| 位置 | 原文 | 实际 |
|---|---|---|
| §1.1 | "一条物理 I²C 总线只有 **127 个**地址"（无出处） | 7 位地址空间是 128 个编码；内核允许普通 client 用的是 **0x08–0x77 共 112 个**，判定在 `i2c-core-base.c:769-781`，注释里逐条列了保留地址的用途 |
| §1.3 | `priv = kzalloc(struct_size(...) + sizeof_priv)`，`:248-249` | 把两件事搞混了：`i2c_mux_add_adapter()` 里是 `kzalloc(sizeof(*priv))`（`:297`，一个通道一份）；`:248-249` 的 `devm_kzalloc(size_add(mux_size, sizeof_priv))` 分配的是 **`muxc`**，不是 `priv`。根因是树里有**两个都叫 "mux priv" 的东西**，已补 §1.2 专门破歧义 |
| §1.3 | 老式 DT 写法的陷阱只说了"会被丢弃" | 补全后果链：分组节点自带 `reg` → `:368-372` 判为老式条目并 `mux_node = NULL` → 回退到器件节点本身（`:375-376`）→ 在错误层级找 `reg == chan_id`，撞上就认错节点、撞不上则 `of_node` 为 NULL 使该通道下所有从设备静默不枚举，**两种结果都不报错** |
| §1.3 | 一个手写的"包装函数"代码块，标着 `i2c_mux_master_xfer():58-76` 却贴的是 `__i2c_transfer()`，还加了句"或 `i2c_transfer`，看风格" | 是**两个真实存在的孪生函数**，不是风格问题：`i2c_mux_master_xfer()`（`:58-75`）调 `i2c_transfer()`，`__i2c_mux_master_xfer()`（`:39-56`）逐字相同只差一行调 `__i2c_transfer()`；smbus 那一对同理（`:99-120` / `:77-97`）。已按源码原样重写 |
| §1.4 | mux-locked"适合 mux 切换本身走 I²C（如 PCA954x）" | **正好反了**。pca954x 切换确实走 I²C，却是 parent-locked（flags `0`，`:428`），因为它能直接调 `__i2c_smbus_xfer()`（`:220`，源码注释 `:213-214` 明写 *"Don't use i2c_transfer()/i2c_smbus_xfer()"*）。真正的判据是**切换路径上能不能拿到 `__` 不加锁变体**：拿不到（ltc4306 走 regmap）才退让成 mux-locked |
| §1.5 | `i2c-topology.rst` 的 PL1 说"破坏的是父 mux 的假设" | 原文说的是**子 mux（即那个 parent-locked mux 自己）**的假设：*"a possible assumption from the child mux that the root adapter is unused between its select op and the actual transfer"* |
| §1.6（原） | 锁风格一栏 7 个驱动写"—"（未知），pca954x 写"可配（DT 里 `i2c-mux-idle-disconnect` 等）" | 该 DT 属性管的是 deselect 行为，**与锁风格无关**。已逐个打开驱动读 `i2c_mux_alloc()` 的 flags 实参填表 |
| §1.4/§1.7 | "ltc4306 是**全树唯一**的 mux-locked"、"**没有一个驱动用 `I2C_MUX_GATE`**" | 两条都错，因为我只 grep 了 `drivers/i2c/muxes/`。全树 `i2c_mux_alloc()` 有 **22 处调用**，13 处在 iio / media / drm / power-supply 下；`I2C_MUX_LOCKED` 共 8 处，`I2C_MUX_GATE` 有 3 处（`mpu3050-i2c.c:66`、`inv_mpu_i2c.c:140`、`sii902x.c:1049-1050`）。已新增 §1.8 整节 |
| §2.1 | 最底层原语"都是 static inline" | `sclhi()`（`algos/i2c-algo-bit.c:75`）是 `static int` —— 它要循环等 SCL 变高（时钟拉伸）并可能超时，所以有返回值。只有 sdalo/sdahi/scllo 是 inline |
| §2.4 | algo-pca / algo-pcf 的原子传输"依赖 `wait_for_completion` 实现" | 两者**都没有** `.master_xfer_atomic`：`pca_algo`（`:363-366`）和 `pcf_algo`（`:390-393`）只有 `.master_xfer` + `.functionality`。只有 algo-bit 有（`bit_xfer_atomic`，`:612-621`）。后果已补：`__i2c_lock_bus_helper()`（`i2c-core.h:37-50`）会 `WARN` 并改 trylock，抢不到就 `-EAGAIN` |
| §3.1 | `i2c_check_7bit_addr_validity_strict(client->addr)` 在 `:33` 无条件执行 | 包在 `if (!(client->flags & I2C_CLIENT_TEN))` 里（`:31-38`），10 位从机跳过 |
| §3.1 | `i2c_detect_slave_mode()` 只说"扫子节点的 reg" | 补两点实现细节：判据是 `IS_BUILTIN(CONFIG_OF)`（arm64 上与 `IS_ENABLED` 等价）；`u32 reg` 未初始化（`:113`）而 `of_property_read_u32()` 的返回值被丢弃（`:116`），子节点无 `reg` 时 `:117` 读的是未初始化栈值 |
| §4.1 | `get_free_i2c_dev(adap)` 标 `:68` | `:68` 是**函数定义**（`:68-89`），`i2cdev_attach_adapter()` 里的调用点在 `:659` |
| §4.2 | 引用 `:117-132` 的注释时漏掉一句 | 漏了 *"However, SMBus-only adapters don't support that interface"*（`:124-125`）。这句直接解释了 §7 的 `i2c-stub`（只填 `.smbus_xfer`，`:304-307`）上 `I2C_RDWR` 为什么会失败——已在两处互相补上交叉引用 |
| §4.3 | "按 size 决定要从用户态拷多少字节 `:358-365`" | `:358-365` 只算 `datasize`；真正的 `copy_from_user()` 在 `:367-373`，而且**只对写方向 + PROC_CALL / BLOCK_PROC_CALL / I2C_BLOCK_DATA 才拷** |
| §4.3 | `i2cdev_check_addr()` 只说"与核心的判定不同" | 漏了它**遍历整棵 mux 树**：向上 `i2cdev_check_mux_parents()`（`:191-201`）、向下 `i2cdev_check_mux_children()`（`:204-215`）、入口 `:220-233`。已补图与理由（同一条物理总线共享地址空间；不横向走兄弟通道，因为同时只有一条接通） |
| §5.1 | `i2c_setup_smbus_alert()` 只说"检查 DT 的 `interrupt-names`" | 关键细节是查的是 **`adapter->dev.parent`**（控制器设备）而不是 adapter 自己（`i2c-core-smbus.c:708`/`:715`），且无 parent 时直接跳过（`:711-713`）；调用点 `i2c-core-base.c:1564`。与 `06` §2.5 已有的记述对齐 |
| §5.2 | "irq domain 在 `i2c-core-base.c:1440-1484` 建立" | 把**建**和**拆**两个函数并成了一段：建是 `i2c_setup_host_notify_irq_domain()`（`:1468-1484`，调用点 `:1546`），拆是 `i2c_host_notify_irq_teardown()`（`:1440-1458`）。两者在源码里紧挨着，最容易这么错 |
| §5.3 | "需要 `CONFIG_DMI`……arm64 上不适用"（只有断言，无证据） | 补硬证据：`i2c_register_spd()`（`:366-455`，函数体在 `#if IS_ENABLED(CONFIG_DMI)` 内，`:365`）**全树唯一调用点是 `drivers/i2c/busses/i2c-i801.c:1300`**（Intel 南桥 SMBus） |
| §7 | `struct stub_chip` 标 `:86` | 结构定义在 `:69-84`；`:86` 是 `static struct stub_chip *stub_chips;` |

### `05-usage.md`

代码骨架**是我按上述 API 现写的，不是从内核里抄的**，所以它们**没有被编译验证**（见 §5）。每段都标了对应的真实参考实现，用于比对：

| 示例 | 对照的真实实现 |
|---|---|
| client 驱动骨架 | `Documentation/i2c/writing-clients.rst` |
| `.probe` vs `.probe_new` | `i2c-slave-testunit.c:184` / `i2c-slave-eeprom.c:209,147` |
| adapter 驱动骨架 | `drivers/i2c/busses/` 里任一现代驱动 |
| bit-banging adapter | `drivers/i2c/busses/i2c-gpio.c` |
| mux 驱动 | `drivers/i2c/muxes/i2c-mux-reg.c` |
| slave 后端 | `i2c-slave-eeprom.c:45-90`（尤其 READ_REQUESTED/READ_PROCESSED 的处理） |
| 用户态 C 程序 | `Documentation/i2c/dev-interface.rst` |
| DT 属性 | `i2c-core-of.c:22-62`（解析侧）+ `i2c-core-base.c:1952`（`i2c_parse_fw_timings`，时序解析；辅助函数 `i2c_parse_timing` 在 `:1924`） |
| `I2C_M_RECV_LEN` 缓冲要求 | `i2c-dev.c:275-283`（四条检查）+ `:285`（内核改写 `len`） |
| `I2C_TIMEOUT` 单位是 10ms | `i2c-dev.c:485` |

**2026-08-14 自审（同 `04`：用户只说"写得还不错，你自己挑战一下"，没提具体问题，于是照 §7 第 17 条当红队逐条回验）**

做法与 `04` 一致：把全篇 43 处 `文件:行号` 逐个 `sed` 打开核对，不抽样；**另外对每一个出现在正文里的内核函数名/宏名/标志名先跑一次全树 `grep`，确认它在 6.1 里真的存在**。后一条是这次新加的动作，直接抓出三个**本版本根本不存在的符号**（见下表标 ☠ 的行）—— 这类错误不可能靠"读得更仔细"发现，因为它们看起来完全合理。

补读的源码：`i2c-dev.c:142-167`/`:240-300`/`:466-500`/`:585-610`、`i2c-core-base.c:104-117`/`:622-644`/`:735-760`/`:876-900`/`:1100-1178`/`:1255-1380`/`:1500-1545`/`:1615-1704`/`:1937-1977`/`:2163-2223`/`:2370-2402`、`i2c-core-of.c:22-63`、`i2c-core.h:25-60`、`i2c-mux.c:340-400`、`muxes/i2c-mux-reg.c:61-76`/`:201-226`、`algos/i2c-algo-bit.c:596-632`、`i2c-slave-eeprom.c:140-171`、`i2c-stub.c:20-45`/`:295-315`、`include/dt-bindings/i2c/i2c.h` 全文、`include/uapi/linux/i2c-dev.h` 全文、`include/linux/pm.h:312-449`、`kernel/params.c:217-244`，以及 arm64 上两个真实控制器的电源管理实现：`busses/i2c-qcom-geni.c:946-965`/`:1000-1030` 与 `busses/i2c-designware-platdrv.c:428-470`。

| 位置 | 原文 | 实际 |
|---|---|---|
| ☠ §1.4 | 用 `i2c_client_get_device_id(client)` 在 `.probe_new` 里取型号 | **6.1 里没有这个函数**（全树 grep 为空）。6.1 只有两条路：`i2c_match_id(foo_id, client)`（`include/linux/i2c.h:362`，实现 `i2c-core-base.c:104-117`）和 `device_get_match_data()`。另有一个名字极像但完全无关的 `i2c_get_device_id()`（`:2378-2400`），它发的是 SMBus 0x7c 事务去问**芯片本身**要 ID，不是查 id_table |
| ☠ §3 | 用户态创建的 client 带 `I2C_CLIENT_USER` 标志 | **没有这个标志**。核心是把它挂进 `adapter->userspace_clients` 链表（`:1304-1306` 挂入，`delete_device_store()` `:1348-1358` 遍历），删除时用 `i2c_encode_flags_to_addr()` 比对地址（`:1350`） |
| ☠ §4.2 | 未实现 `master_xfer_atomic` 时核心报 "I2C level parent adapter cannot handle atomic transfers" | **这个字符串全树不存在**（它是我拼的）。真实行为是 `__i2c_lock_bus_helper()`（`i2c-core.h:37-50`）先 `WARN` 出 `"No atomic I2C transfer handler for '<dev>'"`（`:41-43`），再 trylock（抢不到 `-EAGAIN`，`:44`），然后 `__i2c_transfer()` **照旧调会睡眠的 `master_xfer`**（`:2274-2277`） |
| §4.2 | "没实现 atomic 回调就会失败" | 方向反了，见上一行：核心的态度是"我警告过你了"，不是拒绝执行。algo-bit 的 `"not flagged for atomic transfers"`（`algos/i2c-algo-bit.c:610-621`）是另一码事 |
| §3 | `dummy` 是 `new_device` 认识的**特殊前缀** | 不是。核心自己注册了一个名叫 `"dummy"` 的普通 driver（`:1112-1116`，在 `i2c_init()` `:2113` 注册），走的是**普通 id_table 匹配**。正经 API 是 `i2c_new_dummy_device()`（`:1136`，注释 `:1128-1131`） |
| §3 / §6 | 写 `0x1000` 表示从机地址，这个编码来自 dt-bindings | **两套编码，别混**。sysfs 走 `I2C_ADDR_OFFSET_SLAVE`=0x1000 / `I2C_ADDR_OFFSET_TEN_BIT`=0xa000（`:50-51`，解码 `:1289-1297`）；DT 走 `I2C_OWN_SLAVE_ADDRESS`=`1<<30` / `I2C_TEN_BIT_ADDRESS`=`1<<31`（`include/dt-bindings/i2c/i2c.h:15`，解码 `i2c-core-of.c:41-49`）。两条路只在**结果**（`I2C_CLIENT_SLAVE` 标志）上汇合 |
| §1.5 | 电源管理小节暗示可以在 late/noirq 阶段访问 I²C | 这是**不可移植的**，同一个 arm64 SoC 家族里就有分歧：geni 用 `geni_i2c_suspend_noirq()`（`:1008-1012`），designware 用普通 `dw_i2c_plat_suspend()`（`:439-446`）。已改成实测对照表 + "别赌" |
| §1.2 | `remove` 返回值改动在 `include/linux/i2c.h:288` | `:276`；并补上核心侧证据：`i2c_device_remove()`（`i2c-core-base.c:622-644`）在 `:628-631` 直接调用，**不接收返回值** |
| §1.3 | 多寄存器原子访问示例直接用 `i2c_transfer()` | 它要求 `I2C_FUNC_I2C`，而 §1.1 的 probe 只查了 `I2C_FUNC_SMBUS_BYTE_DATA` —— 示例之间自相矛盾，已补一段说明 |
| §1.4 | 对照表两个格子写"—" | 按 §7 第 17 条清债：两行（"何时用"/"怎么拿型号信息"）都填实，并补出上面那张 `i2c_match_id` vs `device_get_match_data` 的表 |
| §4.1 | `adap.nr = 3;` 出现在骨架里 | 误导：核心**不读**驱动填的 `nr`，`i2c_add_adapter()` 走 `of_alias_get_id()`（`:1654`）或 `idr_alloc()`（`:1662`）；只有 `i2c_add_numbered_adapter()`（`:1697`）才用它。已改成注释 + arm64 上"不要写死 `/dev/i2c-3`"的说明 |
| §4.1 | `functionality` 被列为"核心强制实现" | 核心强制的只有 `algo`（`i2c_register_adapter()` `:1524-1531`）。`functionality` 是**事实上必须**：`i2c_check_functionality()` → `i2c_get_functionality()`（`i2c.h:909-918`）直接解引用它，留空 = 空指针 |
| §4.1 | quirks 示例只写了 `max_comb_*_msg_len` | 补 `.flags = I2C_AQ_COMB_WRITE_THEN_READ` 的联动：`i2c_check_for_quirks()`（`:2171-2223`）只在带 `I2C_AQ_COMB` 时才检查这两个长度（`:2177-2198`），并**隐含 `max_num_msgs = 2`**（`:2178`）；0 表示无限制 |
| §4.3 | `bit.timeout` 未标单位、`can_do_atomic` 未交代 | 单位是 jiffies；bit-banging 骨架里 `can_do_atomic` **必须保持 false**；udelay 值与频率的换算补成"半个时钟周期 → 5 µs ≈ 100 kHz" |
| §5 | 锁风格判据"适合切换本身走 I²C 的芯片用 mux-locked" | 与 `04` 自审推翻的是同一条错误断言，`05` 这份**没有跟着改**（跨篇陈旧）。已换成"select 能不能用 `__` 前缀免锁变体"的判据，并加上 pca954x 的反例（`muxes/i2c-mux-pca954x.c:220`），结论句改为"**mux-locked 是一种让步，不是一种优化**" |
| §5 | mux 骨架的 `last` 初值为 0、DT 节点名与 `reg` 不一致 | 0 是**合法通道号**，不能当"未知"，已改 `U32_MAX` 并在两处赋值；DT 节点改成 `i2c-mux@12340000` 并点明 unit-address 必须等于 `reg`；另补老式 DT 写法的回退路径（`i2c-mux.c:361-388`）与 `sizeof_priv` 的两种写法（`muxes/i2c-mux-reg.c:201-207`） |
| §6 | slave 后端的 DT 匹配需要 `of_match_table` | 两个 in-tree 后端都没有 `of_match_table`，靠 `i2c_device_match()`（`:155`）回退到 `i2c_match_id()` + `of_modalias_node()` 剥掉厂商前缀来匹配 id_table。另修：`3-1064` 这个 sysfs 名字来自 `i2c_dev_set_name()`（`:876-894`）+ `i2c_encode_flags_to_addr()`（`:735-747`）；eeprom 的 sysfs 属性名与容量取自 `i2c-slave-eeprom.c:165` / `:147` |
| §7.2 | 把 `I2C_RETRIES` / `I2C_TIMEOUT` 与 `I2C_SLAVE` / `I2C_PEC` 并列成"per-fd 设置" | 前两个写的是 **`client->adapter->{retries,timeout}`**（`i2c-dev.c:472-486`，且 `timeout` 单位是 10ms），**影响这条总线上所有使用者、关掉 fd 也不恢复**；只有 `I2C_SLAVE`/`I2C_PEC` 是 per-fd（`i2cdev_open()` `:585-610` 每个 fd 一个"** NEVER REGISTERED **"的匿名 client） |
| §7.3 | 三种方式对比表里 8192 一律写成"长度上限"，`I2C_SMBUS` 一列有个"—" | 两种 8192 语义不同：`read()`/`write()` 是**静默截断**（`:142-143` / `:166-167`），`I2C_RDWR` 是超限 `-EINVAL`（`:250-253`）。另修 `I2C_RDWR_IOCTL_MAX_MSGS` 的位置（`include/uapi/linux/i2c-dev.h:55`，不是 `i2c-dev.h:32`；超限判定 `i2c-dev.c:450`）、`I2C_SMBUS_BLOCK_MAX` 的位置（`uapi/linux/i2c.h:140`），"—"改成"不适用" |
| §7.4 | `buf[0]` 要"预置为 1"；`len` 是读回长度 | `buf[0]` 是**额外字节数**，≥1 即可、可以更大（注释 `:264-274` 明说可留校验和字节）；检查通过后内核立刻 `msgs[i].len = msgs[i].buf[0]`（`:285`），所以返回后 `len` **不是**读到的字节数。另补：能力位 `I2C_FUNC_SMBUS_READ_BLOCK_DATA` 与这个标志绑定（`uapi/linux/i2c.h:79`/`:106`），但**核心不替你检查**，不认识它的控制器只会读 1 个字节还报成功。（这里我第一版又写错了一个全称断言——"全树只有 3 处用到 `I2C_M_RECV_LEN`"，那是只 grep 了 `i2c-core-*.c` 和 `algos/`；实际 `busses/` 下有 19 个控制器驱动处理它。又一次印证 §7 第 17 条第三点）|
| §8 | `cat .../parameters/functionality` 输出 `0x0eff0000` | 两处错。(1) `ulong` 模块参数用 `param_get_ulong` → `"%lu"`（`kernel/params.c:242`），读出来是**十进制**；(2) 默认值 `STUB_FUNC_DEFAULT`（`i2c-stub.c:27-30`）= `0x0c7f0000` = 209649664。另补：改动**立即生效**（`stub_func()` `:299-302` 每次现读），且写入值会被 `STUB_FUNC_ALL` 掩掉，**只能减能力不能加** |
| §7.4 | **我在本次自审里自己新写进去的一句**："可以 `cat /sys/class/i2c-adapter/i2c-N/functionality` 查能力" | 写完顺手 grep 了一下就推翻了：适配器在 sysfs 里只有 `name` / `new_device` / `delete_device` 三个属性（`i2c_adapter_attrs[]`，`i2c-core-base.c:1370-1375`），没有 `functionality` 节点。查能力只能用 `I2C_FUNCS` ioctl 或 `i2cdetect -F N`。（`/sys/class/i2c-adapter/` 本身确实存在，但那是 `CONFIG_I2C_COMPAT` 的符号链接目录，`:1580`，链接指向的还是同样那三个属性）|
| §2 | "`of_i2c_get_board_info()` 只有 40 行"、时序字段见 `02` §2.4 | 41 行（`i2c-core-of.c:22-62`）；`struct i2c_timings` 在 `02` **§2.5**（§2.4 是 `i2c_algorithm`）—— 这是 `02` 加节后没同步的陈旧交叉引用，与 `03` 重排时那 15 处同类 |

篇首另加了一句原来没有的话：**这些 C 代码是按 6.1 API 手写的示意骨架，没有编译过**（原来这个免责声明只写在本篇 §5.1，读者读 `05` 时看不到）。

### `06-devicetree.md`

这一篇的写法与其它篇不同：**每条 DT 属性都必须找到"谁读它"的那一行代码**，读不到就不写、或明确标注"只有某个驱动读"。证据分三类：

**（1）核心侧解析代码（一等证据，全文读完）**

| 论断 | 出处 |
|---|---|
| DT 枚举总入口 `of_i2c_register_devices()` | `i2c-core-base.c:1588`（对照 ACPI 在 `:1589-1590`，arm64 上不走） |
| `i2c-bus` 子节点优先 | `i2c-core-of.c:96-98`（仅 3 行；找不到就退回适配器节点自身） |
| 只遍历 `status` 可用的子节点 | `i2c-core-of.c:100`（`for_each_available_child_of_node`） |
| `OF_POPULATED` 去重 | `i2c-core-of.c:101-102`（置位）/ `:109`（失败时清除） |
| DT → `i2c_board_info` 的逐字段映射 | `i2c-core-of.c:30`（modalias）/`35-39`（reg）/`41-44`（10-bit）/`46-49`（own slave addr）/`51`（of_node）/`52-53`（fwnode）/`55-56`（name）/`58-59`（addr） |
| `client->name` 只取第一个 compatible 且截到第一个逗号 | `drivers/of/base.c:1231-1232`（`of_modalias_node()`） |
| 地址高位标志的定义 | `include/dt-bindings/i2c/i2c.h`（`I2C_TEN_BIT_ADDRESS` / `I2C_OWN_SLAVE_ADDRESS`） |
| client irq 三来源与优先级 | `i2c-core-base.c:499-534` |
| `wakeup` 中断名 | `i2c-core-base.c:542-560`，取名点 `:546`（`of_irq_get_byname(node, "wakeup")`） |
| 时序属性与各自默认值 | `i2c_parse_fw_timings()` `i2c-core-base.c:1952`；逐属性 `:1957`（clock-frequency）/`:1960-1962`（scl-rising-time-ns 分档）/`:1964-1965`（scl-falling-time-ns 分档）/`:1967`（scl-internal-delay-ns）/`:1969`（sda-falling-time-ns 默认取 scl_fall_ns）/`:1971`（digital-filter-width-ns）/`:1972`（analog-filter-cutoff-frequency）/`:1974`（结束）；辅助函数 `i2c_parse_timing` `:1924`，doc comment `:1937` |
| **它是驱动调用的、核心不调用** | `rg -n 'i2c_parse_fw_timings' drivers/ include/` —— 调用点全在 `drivers/i2c/busses/`，`i2c-core-base.c` 里只有定义 |
| 总线恢复的 DT 装配 | `i2c-core-base.c:327-336`（`i2c_gpio_init_pinctrl_recovery`）/`:339-345`（`i2c_gpio_init_generic_recovery`）/`:340`（scl-gpios）/`:374`、`:382`、`:386`（各分支）/`:392-398`（完整性检查）/`:375`、`:400`（dev_dbg vs dev_err 的分野）/`:422`、`:436`、`:463-466`；两个子函数入口 `:303`、`:348` |
| ARA 是由控制器节点的 `interrupt-names = ..., "smbus_alert"` 触发的 | `i2c-core-smbus.c:706-722`；`i2c-smbus.c:181-182`（真正取名字的那两行） |
| mux/gate/arb 拓扑节点查找的 4 步 | `i2c-mux.c:355-392`；"old-style DT entry" 的静默丢弃在 `:367-373`（注释 `:368`，判定 `:369`，丢弃 `:370-371`） |
| `OF_DYNAMIC` / overlay 通知链 | `i2c-core-of.c:172-210`；注册点 `i2c-core-base.c:2117-2118`；`FWNODE_FLAG_NOT_DEVICE` 的原因注释 `i2c-core-of.c:181-185` |
| `i2c-gpio` 默认就按 open-drain 申请引脚 | `busses/i2c-gpio.c:313-315`（`GPIOD_OUT_HIGH_OPEN_DRAIN`）/`:398`、`:406`（两个废弃布尔属性的实际语义） |
| sysfs 名字 `%d-%04x` | `i2c-core-base.c:892` |

**（2）"谁消费什么"那张表的取证方式（本篇的核心方法）**

对每一个属性名做一次全目录搜索，看**有没有代码读它**、**读它的是核心还是某个驱动**：

```bash
$ rg -n '"multi-master"' drivers/i2c/
drivers/i2c/busses/i2c-tegra.c:1682
drivers/i2c/busses/i2c-aspeed.c:940
drivers/i2c/busses/i2c-rcar.c:1123      → 只有 3 个驱动读，核心不读

$ rg -n '"single-master"' drivers/i2c/
drivers/i2c/busses/i2c-xiic.c:821       → 只有 1 个驱动读

$ rg -n '"i2c-digital-filter"' drivers/i2c/
drivers/i2c/busses/i2c-at91-master.c:905
drivers/i2c/busses/i2c-stm32f7.c:679    → 布尔开关由驱动读，
                                          而 *-width-ns 数值由核心读（见上表）
$ rg -n '"smbus-alert"' drivers/i2c/
drivers/i2c/busses/i2c-stm32f7.c        → 只有 1 个驱动读；
                                          真正创建 ARA 的是 interrupt-names
```

**结论的性质**：这些计数是**在本树上实测的**，不是从绑定文档抄的。绑定文档 `Documentation/devicetree/bindings/i2c/i2c.txt` 自己只说这些属性 "may not be supported by all drivers" —— 本篇把这句含糊的话换成了可复核的行号。任何人想复核，只要重跑上面的 `rg` 即可。

**（3）绑定文档与真实 DTS（三等证据，但对"该怎么写"最有用）**

| 来源 | 用于 |
|---|---|
| `Documentation/devicetree/bindings/i2c/i2c.txt` | §1 的属性清单与那句 "may not be supported by all drivers" |
| `.../i2c-mux.yaml` / `i2c-gate.yaml` | §4.1/§4.4 的节点名与结构 |
| `.../i2c-mux-pca954x.yaml` | §4.2 的 pca9548 例子、`i2c-mux-idle-disconnect` |
| `.../i2c-mux-gpio.yaml` | §4.5 的 `i2c-parent` |
| `.../i2c-gpio.yaml` | §5.1 的属性表 |
| `arch/arm64/boot/dts/freescale/imx8mp.dtsi:845-853` | §2.1 的控制器节点范例（`#address-cells`/`#size-cells` = 1/1） |
| `arch/arm64/boot/dts/freescale/imx8mp-evk.dts:234-247` | §2.1 的板级 client 范例 |
| `arch/arm64/boot/dts/rockchip/rk3399.dtsi:585-598` | §2.1/§2.2 的第二个范例（父节点 2/2 → `reg` 写 4 个数） |
| `arch/arm64/boot/dts/exynos/exynos5433-tm2-common.dtsi` | §5.1 的 `i2c-gpio` 真实节点 |

**四个 DTS 文件都是逐节点原样抄下来的**（不是我编的示例），目的是让读者能 diff 一份确定能编译的东西。

**这一篇里两个非显然的发现**（都不在任何绑定文档里）：

1. **`smbus-alert` 属性并不创建 ARA。**真正触发自动创建 0x0c ARA 设备的是控制器节点 `interrupt-names` 里有 `"smbus_alert"`（`i2c-smbus.c:181-182`）。`smbus-alert` 布尔属性只有 `i2c-stm32f7.c` 读。
2. **在 `i2c-mux` 分组节点上写 `reg` 会静默作废整个拓扑。**`i2c-mux.c:369-371` 把带 `reg` 的节点判为 "old-style DT entry" 直接丢弃（注释见 `:368`），**不打印任何错误**。

**2026-08-14 可读性重排（事实未变，只改组织方式）**

用户对这一篇的反馈是"内容基本可以，但有些地方读起来费劲"。所以这一轮**不是纠错，是重排**：不删任何已取证的论断，只换承载形式。改动 15 处，逐一列出（左边是原来的形式，右边是改成的形式）：

| 位置 | 原来 | 改成 |
|---|---|---|
| 篇首 | 直接进 §1 | 新增「按任务找章节」6 行导航表（启用总线 / 挂芯片 / 加 mux / slave / 没生效 / 查属性有没有人读） |
| §1 | 一张 3 行表里塞了"典型属性 + 判断特征 + 实测规模" | 表只留判断特征；实测规模（`multi-master` 3 处等）拆成表后的独立段落 |
| §1.1（新增） | 核心会读的属性散落在 §2–§5 各节 | 新增 12 行总表：属性 / **写在哪个节点** / 效果 / 消费代码 / 详见；并在表后单独点出"时序那一行有前提" |
| §2.3 | 默认值分档写在散文里 | 前提 `rg` 命令提到最前面成代码块；分档默认值抽成 3 行表 |
| §2.4 | 5 个要点混着行号、日志文案、条件 | 拆成四块：A/B 两条路径对照表 → DTS 片段 → 两步 ASCII 流程 → 5 行「dmesg 里看到 / 含义」表，收尾一句"没看到报错 ≠ 恢复能用" |
| §2.5 | 散文解释 `smbus-alert` 不管用 | 「你可能以为要写 / 实际」对照表 + ARA 创建路径 3 步 |
| §3.2 | 第 2 点是一个带括号从句的长句 | 拆成 3 条子项（怎么比 / 正常设备用不到 / 是给 sysfs 设备用的） |
| §3.3 | 链式"否则…否则…"的 ASCII 图 | 3 行优先级表 + `pm_runtime_get_sync()` 副作用单独成段 |
| §4.0（新增） | 5 种 mux 写法并列，读者选不出来 | 5 行选型表 + 拓扑节点名对照表 |
| §4.1 | 4 步线性列表描述 if/else 逻辑 | 改成完整 ASCII 决策树，old-style `reg` 那条岔路标 并写出后果（子总线为空、不报错） |
| §4.3 | 指向"§4.1 第 2 步" | 指向"§4.1 决策树里标 的那一步"，并以"**它不带 `reg`。**"收尾 |
| §5.1 | 开漏的三种情况写在一段里 | 拆成 3 个编号层次（默认已是开漏 / 写标志只是写明事实 / 两个废弃布尔语义相反） |
| §6 | `FWNODE_FLAG_NOT_DEVICE` 的原因写成一句长句 | 拆成 3 句（标志含义 / fw_devlink 不建 link / i2c 需要 link，所以先清后注册） |
| §7.1 | ASCII 三层图 + 三段散文交替 | 三层验证表（顺序 / 查什么 / **能排除的原因**）+ 明确"② 最该先做"，再按 ①②③ 分小标题给命令 |
| §7.2 | 表格最后一行一个单元格塞了三种"什么都不打印"的原因 | 三种原因提出表外，写成 3 条编号（`status`、`i2c-bus` 分组、挂错父节点） |
| §7.3 | 12 行症状表平铺 | 按坏掉的范围分 A 总线级 / B 单设备级 / C 参数级 / D mux 级四张小表 |
| §8 | 速查块漏了 `status` | 补 `status` 一行，并注明"完整表+行号见 §1.1"，与新增的 §1.1 对齐 |

顺手修掉的行号/引用问题（重排时逐条回验发现的）：

| 位置 | 原文 | 实际 |
|---|---|---|
| §3.1 | `of_i2c_get_board_info()`"只有 40 行" | 41 行（`i2c-core-of.c:22-62`） |
| §1.1 | `sda-gpios` 在 `i2c-core-base.c:393` | `:394` |
| §4.1 | `priv->adap.dev.of_node = child` 在 `:389` | `:390` |
| 本文件 §5.1 | dtschema 缺失一事"在 `06` §4.1 已注明" | 实际写在 §7.1，已改 |
| 本文件 §3 | old-style `reg` 丢弃"在 `i2c-mux.c:370`" | 精确到 `:367-373`（注释 `:368` / 判定 `:369` / 丢弃 `:370-371`） |

重排后重跑了 `file:line` 越界检查：**39 处引用全部在文件行数范围内**。

### `07-debug.md`

**一等证据（原样读过的文件/段落）**

`-DDEBUG` 机制：`drivers/i2c/Makefile:21`、`algos/Makefile:10`、`busses/Makefile:157`。
调试消息表：`rg 'dev_dbg|pr_debug' drivers/i2c/i2c-core-base.c` 的结果逐条核对行号。
tracepoint 表：`include/trace/events/{i2c,smbus,i2c_slave}.h` **逐行读**，含 TP_printk 格式串、`TP_CONDITION` 和字段清单。
sysfs 属性：`i2c-core-base.c:665-697`（name/modalias）、`:1312`（new_device）、`:1367`（delete_device，含 `DEVICE_ATTR_IGNORE_LOCKDEP`）、`:1370-1375`、`:699-705`（`i2c_bus_type`）；`i2c-dev.c:98-107`、`:748`、`:668-671`。
debugfs：`i2c-core-base.c:2104/:1562/:1603/:1805` + `i2c-gpio.c:74-301` 全段读完。
错误码分诊表：`fault-codes.rst:42-130` + **每个错误码都定位到返回它的函数**（见下）。

**2026-08-14 自审（用户："07 你自己挑战一下吧"）**

和 `04`/`05` 一样走红队自审，不抽样。这一篇的特殊性在于：**它整篇都在教读者"去看某个输出"** ——
日志、tracepoint、sysfs 文件、错误码。所以错误的形态也集中在"那个输出到底存不存在、看不看得见、
写进去的值是什么意思"。共修正 21 处，其中 3 处是我在这一轮**自己新写**的段落里的错误。

关键修正（按严重程度）：

| # | 位置 | 原文 | 实际 |
|---|---|---|---|
| 1 | §8.4 | 让读者 `dmesg \| grep "cannot handle atomic transfers"` | ☠ **这句文案全树 0 命中，是我拼的**。真实文案是 `i2c-core.h:42-43` 的 `WARN(..., "No atomic I2C transfer handler for '%s'\n", ...)`；而且它是 `WARN()`（带栈回溯）、要求 `master_xfer_atomic` 和 `smbus_xfer_atomic` **两个都缺**、打印后执行还继续（`:44`）。同时消掉了它与 `05` 的一处矛盾 |
| 2 | §3.1 | 列了 `/sys/bus/i2c/drivers/<drv>/new_id` | ☠ **`new_id` 在 `drivers/i2c/` 下 0 命中，是我编的**（那是 USB/PCI 的机制）。`i2c_bus_type`（`:699-705`）不设 `drv_groups`，driver 目录下只有 `bind`/`unbind`，来自 `drivers/base/bus.c:639` `add_bind_files()` |
| 3 | §7 | quirks 检查失败返回 `-EINVAL`，并建议"打开 `I2C_DEBUG_CORE` 看拒绝原因" | **两点都错**：`i2c_quirk_error()` 返回 `-EOPNOTSUPP`，且用 `dev_err_ratelimited`（`:2163-2168`）**默认就可见**，不需要开任何开关。原因文案共 **6 条不同字符串 / 10 个调用点**（`:2183/:2186/:2189/:2192/:2195/:2202/:2209/:2212/:2215/:2218`） |
| 4 | §5 | 故障注入 `lose_arbitration`/`inject_panic` 写入的数字是"第 N 个 SCL 周期" | **是微秒时长（上限 100 ms，`i2c-gpio.c:216`/`:245`）**，且在**第一个 SCL 下降沿**才触发（`:183` `IRQF_TRIGGER_FALLING`，注释 `:220-226`）；写操作**会阻塞**在 `wait_for_completion_interruptible()`（`:188`），必须放后台跑 |
| 5 | §5 | 直接教用法，没写前提 | 漏了 `CONFIG_I2C_GPIO_FAULT_INJECTOR`（`busses/Kconfig:654-660`）这个 Kconfig 门（整块代码在 `#ifdef` 内，`i2c-gpio.c:74-301`，关掉时是空 stub `:299`），也漏了 rst 要求的**两条总线**拓扑（`Documentation/i2c/gpio-fault-injection.rst:5-9`） |
| 6 | §5 | 六个文件的可见条件写在 `:277` | 门在 **`:282`**（`if (priv->bit_data.getscl)`）；`:276` 是 `init_completion`。`scl-output-only` 会让 `getscl` 为 NULL（`:419-420`/`:318`），此时后四个文件不出现 |
| 7 | §5.3 | "把 SDA 拉低会看到 `SCL is stuck low, exit recovery`" | ☠ **拉低 SDA 是完全静默的**：9 个脉冲照打完（`:248`，`RECOVERY_CLK_CNT` `:220`），`i2c_generic_bus_free()` 在 `:274` 返回 `-EBUSY`（`:211`），一条日志都没有。只有拉低 **SCL** 才打 `dev_err "SCL is stuck low, exit recovery"`（`:252-253`） |
| 8 | §5.3/§8.3 | 恢复失败可以靠 `dmesg \| grep -i recover` 定位 | **最常见的失败根本不打印**：`Not using recovery: %s` 里"没提供任何方法"这一支把 `is_error_level` 置 false（`:435-436`）走 `dev_dbg`（`:466`），默认不可见；只有"给了 gpio 但缺 `get_scl`/`set_sda`"两支（`:452`/`:456`）走 `dev_err`（`:464`）。三支都会把 `bus_recovery_info` 置 NULL 并返回 `-EINVAL`（`:468-470`） |
| 9 | §5.3 | — | **新发现的"谎报成功"路径**：既没有 `get_bus_free` 也没有 `get_sda` 时（`:203-206`）拿到 `-EOPNOTSUPP`，`i2c_recover_bus()` 在 `:280-282` 把它**改写成 0** —— 恢复"成功"了，但什么也没确认 |
| 10 | §2.1 | `i2c_reply` 覆盖所有读消息 | 循环上界是 `ret`（`:2287`），不是传输前那个 `num`（`:2262-2269`）—— **传输失败就一条 reply 也没有** |
| 11 | §2.1 | 四个 smbus tracepoint 的触发条件语焉不详 | 补 4 行 `TP_CONDITION` 表：`smbus_write` `smbus.h:29-31`、`smbus_read` `:96-98`（是前者的取反）、`smbus_reply` `:139`（`res >= 0 && read_write == I2C_SMBUS_READ`）、`smbus_result` `:199`（无条件）；并引了内核自己的注释 `i2c-core-smbus.c:568-570`、`:611` |
| 12 | §2.1 | — | **`f=` 这个字段有三种含义**：i2c 组是 `msg->flags`；`smbus_write/read` 是**未掩码**的 client flags（`:571-574`，在 `:576` 掩码之前）；`smbus_reply/result` 是**已掩码**的（`:612-615`）。例：`I2C_CLIENT_WAKE`（0x80，`include/linux/i2c.h:341`）会在后两者里消失 |
| 13 | §2.2 | 过滤器示例按 `addr` 直接写到事件组上 | **组级过滤有静默陷阱**：`process_system_preds()`（`kernel/trace/trace_events_filter.c:1787`）对表达式套不上的事件调 `filter_disable(file)`（`:1815`）→ **那些事件变成无过滤全量输出**，而 `fail = false`（`:1834`）让写入**返回成功**。真报错格式来自 `append_filter_err()`（`:1071`、`:1096-1099`），文案 `Couldn't find or set field in one of a subsystem's events`（`:101`） |
| 14 | §2.2 | — | 补 7 行字段表：`i2c_result` 只有 `adapter_nr`/`nr_msgs`/`ret`（`i2c.h:126-128`）；`smbus_read` 没有 `len`；`smbus_result` 的字段叫 `res`（`:211`）；`i2c_slave` 没有 `flags`/`msg_nr` —— 按不存在的字段写过滤器会失败 |
| 15 | §1 | 三个 algo 的 `i2c_debug` 一句话带过 | 三者的**开关门完全不同**，拆成 3 行表：algo-bit 的 `module_param` 是 `S_IRUGO\|S_IWUSR`（`:41`）但**整块在 `#ifdef DEBUG` 内**（`:39-44`）→ 不开 `I2C_DEBUG_ALGO` 连 sysfs 文件都没有；algo-pca 的是 `module_param(i2c_debug, int, 0)`（`:561`）→ **权限 0，根本没有 sysfs 文件**，且它用裸 `printk(KERN_DEBUG)`（`:17-22`）**不受 dynamic_debug 管**；algo-pcf 是 0644（`:421`）且不在 `#ifdef` 内。**`module_param` 权限位和 `#ifdef DEBUG` 是两道独立的门** |
| 16 | §1 | `bit_test` 是运行时可调的 | `S_IRUGO`（0444，`:35-37`，在 `#ifdef DEBUG` **之外**）→ 只能 modprobe 参数或 cmdline；`test_bus()` 在 `:229`，失败返回 `-ENODEV`（`:653-656`） |
| 17 | §1 | — | `test_bus()` 的输出**默认就可见**：`pr_info`（`:242`）+ `printk(KERN_WARNING)`（`:247` 起），四条结论 `SDA stuck high!`（`:257`）/`SDA stuck low!`（`:271`）/`SCL stuck high!`（`:285`）/`SCL stuck low!`（`:299`） |
| 18 | §7 | 每行只给错误码和"物理原因" | 每行补上**返回它的函数**。新增/改写 6 行：`-EBADMSG` 的 `pr_debug "Bad PEC 0x%02x vs. 0x%02x"`（`i2c-core-smbus.c:88-89`）默认不可见；`-ESHUTDOWN` 的 `dev_WARN "Transfer while suspended"`（`i2c-core.h:55-56`）**每个适配器一生只打一次** —— `I2C_ALF_SUSPEND_REPORTED`（`i2c.h:739`）在 resume 时不清（`:856`）；`-EBUSY` 有 5 个来源（1）；`-EPROTO` 核心只 2 处（3） |
| 19 | §7 3 | "`-EPROTO` 表示读回字节数不对" | 核心里只有两处，**语义还不一样**：`i2c-core-smbus.c:502-507` 只查上界，`algos/i2c-algo-bit.c:438-446` 查完整的 1..32 区间；另有 24 个 `busses/` 下的文件自己返回它 |
| 20 | §6 | `decode-dimms` 需要 `CONFIG_I2C_SMBUS` | **错**。`CONFIG_I2C_SMBUS`（`drivers/i2c/Kconfig:88-96`）只管 SMBus Alert + Host Notify。真正的依赖是 `CONFIG_EEPROM_LEGACY`（`drivers/misc/eeprom/Kconfig:49-58`，已标 DEPRECATED）或 `CONFIG_EEPROM_EE1004`（`:123-131`） |
| 21 | §3.2 | 适配器名字是固定的 | 三种写法并存：固定字符串（`i2c-rk3x.c:1258`）、`dev_name()`（`i2c-tegra.c:1843-1844`）、`pdev->name`（`i2c-imx.c:1482`）、mux 用 `snprintf` 格式串（`i2c-mux.c:329-330`）。**所以文档里不能写死某个名字** |

顺手修掉的行号问题：mux 链接 `:421/:425` → `:420-422 / :424-427`；`i2c-rk3x.c:1259` → `:1258`。
§8.4 那张 mux 表的两行也修了（`:35`→`:39`、`:74`→`:77`），并顺带纠正了它的含义 ——
**mux 没有"专门的原子版本函数"**，`:314-315`/`:323-324` 是把非原子那个函数指针**原样复制**进
atomic 槽，条件只是"父适配器支持原子"；被复制进去的还可能是 mux-locked 那个**会拿锁**的
版本（`:58`/`:99`）。

**三处我在这一轮自己写错、又在同一轮抓出来的**（记下来是因为它们比原文的错更值得警惕）：

| 我写的 | 实际 | 教训 |
|---|---|---|
| "`test_bus()` 的输出是 `bit_dbg` 和 `pr_info` 混排" | 全是 `pr_info` + `printk(KERN_WARNING)`，**没有一条 `bit_dbg`** | 写"某处的日志级别"时也要 `sed` 打开看，不能因为函数在 algo-bit 里就假定它用 `bit_dbg` |
| 编了一段 `cat .../filter` 的报错输出 | 真实格式由 `append_filter_err()` 决定 | ☠ **示例命令的输出和内核字符串是同一类事实**，编造一段 `cat` 输出与编造一句 `dev_err` 文案等价 |
| "把 SDA 拉低会打 `SCL is stuck low`" | 那条路径完全静默 | 相邻代码路径的日志容易串台；"哪条路径打哪条日志"要逐条对 |
| （跨篇）`i.MX I2C adapter` 作为 `i2cdetect -l` 的示例输出 | ☠ 全树 0 命中，是我编的适配器名。已换成实测存在的 `rk3x-i2c`（`i2c-rk3x.c:1258`），**`05-usage.md:284`/`:810` 的同一处伪造也一并修了** | 同一个伪造事实会跨篇复制，抓到一处要全篇搜 |

自审后重跑 `file:line` 越界检查：**`07` 的全部引用在文件行数范围内**。

### `99-evidence.md`（本篇自审）

**2026-08-16 自审（用户："99 你看看需要 review 吗？"）**

本篇的特殊性：**它唯一的论断对象是我自己** —— "哪些文件读完了"、"哪些只读了符号表"、
"哪些没验证"。所以它的失效方式和前八篇完全不同：**前八篇的风险是写错，本篇的风险是过期。**
每次 review 都会让"我读了什么"往前走一步，而本篇不会自己跟着走。

先跑了一遍**可机械复现的部分**，全部通过，没有一处需要改：

| 检查 | 结果 |
|---|---|
| §1.1 的 `git log` / `localversion-rt` / `175` / 两个 `??` | 全部一致 |
| §1.2 的 i2c 范围 `git status --porcelain` | 仍为空输出 —— 结论成立 |
| §1.3 的 6 条统计命令（7,326 / 133 / 89,120 / 10 / 2,852 / 1,679） | 逐条重跑，全中 |
| §1.3 的 25 个逐文件行数 | `wc -l` 逐个对，全中 |
| §2.3 的 12 个内核文档行数 | 全中 |
| §2.4 的 cbm 版本与 overlay 路径 | `codebase-memory-mcp 0.10.3` 实测一致，`/workspace/safety/kidx/merged` 存在 |
| §2.6 的 8 个能力普查数字（81/30/4/26/11/13） | 逐条重跑，全中 |
| §2.7 的 4 组裁剪位置行号 | 逐个 `sed` 打开，全中 |
| §4 的 4 条自陈问题行号 | 逐个打开，行号全中（但有 1 条概述失真，见下） |

**修正 6 处，全部是"过期"或"概述失真"，没有一处是编造** —— 这本身就是本篇与前八篇的分野：

| # | 位置 | 问题 | 改成 |
|---|---|---|---|
| 1 | §1.2 | 指向 `03-internals.md` **§7.4** | `03` 重排成 Part A/B/C 之后那一节是 **§B1.4**。☠ **这正是 §7 第 19 条最后一句警告的情形，而受害者是本篇自己** —— 当时回访了 `06` 的引用，漏了 `03` 的 |
| 2 | §2.1 | 指向 `02` **§2.13 / §2.14** | `02` 加了 §1.0 之后 uapi 结构与内部结构变成 **§2.11 / §2.12** |
| 3 | §2.1 | 一等证据表里**没有那三个 tracepoint 头文件** | `07` §2 的所有表（含 4 个 `TP_CONDITION`、7 行字段清单）都是逐行读出来的，共 458 行，属于一等证据却没登记。已补 3 行；顺带把 `busses/Kconfig` 和 `busses/Makefile` 补进 Kconfig/Makefile 那两行 |
| 4 | §2.2 | `algos/i2c-algo-bit.c` 记作"详读 `:531-695`" | `07` 自审时补读了 `:17-44`、`:229-301`、`:430-450`，**证据等级已经升级**，二等证据表却还按旧范围写 |
| 5 | §2.2 | `busses/i2c-gpio.c` 记作"详读 `:255-295`" | 实际 **`:74-301` 全段读完**，另加 `:313-315`、`:398-420`。这一条尤其要紧：`07` §5 整节的可信度取决于这个范围 |
| 6 | §4 | `i2c_del_adapter()` 的 FIXME 概述成"设备移除顺序 / 引用计数的处理" | FIXME 原话说的是**把 `struct device` 的生命周期与 `i2c_adapter` 解耦（像 spi/netdev 那样）**，没有"移除顺序"这回事。行号也精确到 `:1806-1814`（原写 `:1809-1813`，只圈到注释、没圈到它所修饰的那两行代码） |

另外把 §7 第 9 条的统计**重写了** —— 原文写的是"至少五处假设被推翻、约十处行号被纠正"，
那是初稿阶段的数字，四轮自审（`04`/`05`/`06`/`07`）之后早已不成立。现在改成一张逐篇的
修正数表，并从中提炼出两条结论（错误率与"有没有人能提问"相关；每轮 review 自己会产生新错误）。

跨篇引用检查（**新增的机械检查，之前没做过**）：把本篇里所有 `0X §Y` 形式的引用
提取出来，与八篇文档的实际标题逐个比对 —— **90 条命中，3 条失效**，即上面的第 1、2 条。
修完后对**全部 9 篇**跑了一遍：**170 条引用全部命中**。这个检查以后每次重排后都要跑，
见 §7 第 21 条。

**两个已知的"假阳性"**，下次跑检查时不必再追（记在这里就是为了省下这次排查）：

| 报出的位置 | 为什么不是错 |
|---|---|
| 本篇 §3 的 `05` 修正表里 `i2c-dev.h:32` 越界（该文件只有 15 行） | 那是**故意引用的错值** —— 正文写的是"`I2C_RDWR_IOCTL_MAX_MSGS` 在 `include/uapi/linux/i2c-dev.h:55`，**不是** `i2c-dev.h:32`"。修正表必须同时出现错值和对值，越界检查天然会报它。**注意本行自己也引用了它两次**，所以检查器现在会报 3 次（原始 1 + 本行 2）—— 这是"记录假阳性"这个动作的必然副作用，不是新问题 |
| 本篇 §3 的 `03` 重排记录里 `03 §7` | 那是**老章节号的变更记录**（"`02` §1.1 图内 `03 §7` → `03 §B1`"）。历史映射表里的旧号是资料，不是失效引用 |

**一般规律**：纠错型文档里，"错值"和"旧值"是内容的一部分，所以**机械检查器在取证篇上
必然有假阳性**。处理办法不是放宽检查，而是**把假阳性逐条记下来并说明理由** ——
下次跑出同样两条就能直接跳过，跑出第三条就说明真出了新问题。

---

## 4. 已发现的代码内自陈问题（原样记录，不做评判）

这些是内核自己在注释里承认的，值得记下来是因为它们往往是"为什么不这么设计"的答案：

| 位置 | 内容 |
|---|---|
| `i2c-core-base.c:1806-1814` | `i2c_del_adapter()` 结尾"等所有引用消失"那段（`init_completion(&adap->dev_released)` + `device_unregister()`）的 FIXME 原话是：**这是老代码，理想的替代方案是把 `struct device` 的生命周期与 `i2c_adapter` 解耦，像 spi 或 netdev 那样做**；任何方案"都应该在 `DEBUG_KOBJECT_RELEASE` 打开的情况下彻底测试"。（2026-08-16 自审改：原文把它概述成"设备移除顺序 / 引用计数的处理"，FIXME 里并没有"移除顺序"这回事 —— 它说的是**生命周期耦合**这个具体设计缺陷。又一次印证 §7 第 17 条第一点：概述会漏掉决定性的那半句） |
| `algos/i2c-algo-bit.c:664-667` | 曾尝试在注册时把 SCL/SDA 强制到初始状态，导致回归（Bugzilla #200045），只好放弃 |
| `algos/i2c-algo-bit.c:606-611` | `bit_xfer_atomic` 无法真正区分原子/非原子，因为 `i2c_bit_algo` 是 `const` 静态结构，只能在运行时 `dev_warn` |
| `i2c-slave-eeprom.c:8-11` | 因为多数从机 IP 只能识别一个地址，不支持需要多地址的 EEPROM 型号 |
| `i2c-slave-eeprom.c:13-17` | FIXME：对端只发 16 位地址的高 8 位时该怎么办（ST-M24C64 回 0xff，本实现把这 8 位当有效地址） |
| `i2c-slave-testunit.c:15` | FIXME：`system_long_wq` 是最佳选择吗 |
| `i2c-gpio.c:261-265` | "如果将来每个适配器都有 debugfs 目录，就把 fault-injector 挪进去" —— 而 `adap->debugfs` 已经存在了，注释未更新 |
| `i2c-boardinfo.c:16-18` | 三个全局符号 `EXPORT_SYMBOL_GPL` 只是为了让核心的多个 .o 共享，注释明写 "ONLY FOR the i2c core" |
| `i2c-core-of.c:181-185` | 添加 DT 动态设备前必须清 `FWNODE_FLAG_NOT_DEVICE`，否则 `fw_devlink` 不会给它加 consumer 依赖 |
| `i2c-dev.c:217-219` | 用户态占用判定刻意与核心不同：只有**绑定了驱动**的地址才算被占（允许访问已实例化但无驱动的地址） |
| `i2c-topology.rst:184-187` | parent-locked mux 的 select 里要保证下游子系统产生"不加锁的传输"，文档自己说这"可能很难，甚至在追求干净方案时是不可能的" |

**一条不是自陈、而是 2026-08-14 自审时读出来的**（所以单列，不混进上表）：
`i2c-core-slave.c:113-117` 的 `i2c_detect_slave_mode()` 里，`u32 reg;` 未初始化，
`of_property_read_u32(child, "reg", &reg)` 的返回值被丢弃，下一行直接 `reg & I2C_OWN_SLAVE_ADDRESS`。
子节点若没有 `reg` 属性，读的就是未初始化的栈值。实践中 I²C 子节点几乎必然有 `reg`，
所以极难触发；**这是我的判读，不是内核自己承认的问题，也没有对应的上游补丁被我找到**
（未做 upstream 检索，见 §5.4）。

---

## 5. 明确未验证的部分

**这一节是本附录最重要的部分。** 以下内容我没有验证，读者不应把它们当作已核实的事实：

### 5.1 未编译、未运行

- **`05-usage.md` 里的所有代码示例都没有编译过**。它们是按 6.1 的 API 手写的骨架，可能有拼写错误、缺失的头文件、或与实际编译约束（如 `__init` 段、`static` 修饰）不符的地方。示例是按主题分节写的，不是按编译顺序 —— 例如 `foo_pm_ops` 在 §1.1 被引用、在 §1.5 才定义（该处已加注说明）。**这条免责声明 2026-08-14 已同时写进 `05` 篇首**（原来只写在这里，读者直接翻 `05` 时看不到）。经 2026-08-14 自审，示例里出现的每个内核符号都已用全树 grep 确认在 6.1 存在（此前有三个不存在的符号，见 §3），但**"存在"不等于"这样组合能编译过"** —— 类型是否匹配、头文件是否齐全，仍然未验证。
- **所有 shell 命令都没有在本机执行过**（本机没有可用的 I²C 硬件，也不能修改内核树来编译验证）。`i2cdetect` 等命令的输出示例是按工具的已知格式手写的，不是真实抓取的。
- **tracepoint 的输出示例是按 TP_printk 格式串手工渲染的**，不是真实 `trace_pipe` 的抓取。字段顺序和空格可能与实际有细微差异。
- **`06-devicetree.md` 里的所有 DTS 片段都没有经过 dtc 编译，也没有跑过 schema 校验。**本机**没有安装 dtschema**，所以 `make dt_binding_check` 和 `make dtbs_check` 这两条命令我**写在文档里但从未执行**。相应地，`i2c-mux.yaml` 引用的 `i2c-controller.yaml` 在本树里**不存在**（它由外部 dtschema 包提供），这一点在 `06` §7.1 已注明。
- **overlay（`OF_DYNAMIC`）的加载路径完全没有实测。**`06` §6 里那段通过 configfs 加载 dtbo 的操作步骤是按已知用法写的，文档里已显式标注"未验证"。我读到的只有内核侧的通知链代码（`i2c-core-of.c:172-210`），**没有验证过一次真实的运行时插拔**。
- **`07` §5 的 `i2c-gpio` 故障注入器从头到尾没有在硬件上跑过。**整节（六个 debugfs 文件的语义、写入值的单位、阻塞行为、两个使用场景）**全部是从 `busses/i2c-gpio.c:74-301` 和 `Documentation/i2c/gpio-fault-injection.rst` 读出来的**，不是操作出来的。特别是"写 `lose_arbitration` 会阻塞、要放后台"这条：代码依据是 `:188` 的 `wait_for_completion_interruptible()`，rst `:106-110` 也这么写，但**我没有真的按过一次回车**。同理 `07` §1 的 `test_bus`、§3 的 sysfs 读写、§5.3 的两个恢复场景，都没有实机执行。
- **`06` §2.3 与 §7 里的 dynamic_debug 命令没有在本机执行过**（本机内核不是这棵树编出来的）。命令语法本身取自 `Documentation/admin-guide/dynamic-debug-howto.rst` 的通行写法，但"打开后确实会打出那几条时序信息"这一点是从 `i2c_parse_timing()`（`i2c-core-base.c:1924`）的 `dev_dbg` 推出的，不是看到的。

### 5.2 只读了符号表、未逐行读的代码

见 §2.2。具体地：

- **`algos/i2c-algo-pca.c` 的 `pca_xfer()`（`:182-356`）内部逻辑未逐行读。**我描述它是"围绕硬件状态码的大 switch 状态机"，这个判断基于函数长度、`pca_start`/`pca_address`/`pca_tx_byte`/`pca_rx_ack` 这组辅助函数的命名，以及 PCA9564 的工作方式。**未逐行确认。**
- **`algos/i2c-algo-pcf.c` 的 `pcf_xfer()`（`:294-382`）同上。**`handle_lab()` 处理丢失仲裁的说法来自函数名和 `lab_mdelay` 字段的注释。
- **`i2c-core-acpi.c` 的绝大部分（847 行里我只读了符号表）。**`04` §6.2 对 ACPI 枚举流程的描述是从函数名和调用顺序推断的。特别地，**OpRegion 的具体行为（`acpi_gsb_i2c_read_bytes` / `_write_bytes` 怎么把 AML 请求翻译成 I²C 传输）我没有读。**
- **`i2c-smbus.c` 的三段机制（Alert / Host Notify / SPD）我没有逐行读**，只读了符号表 + 头文件注释 + 核心侧的调用点。SPD 自动注册那 90 行（`:366-455`）尤其未详读 —— 只确认了它的 `CONFIG_DMI` 包裹（`:365`）和全树唯一调用点（`i2c-i801.c:1300`）。
- **`i2c-stub.c` 的 `stub_xfer()`（`:123-297`）未逐行读**，包括 bank switching 的具体实现。
- **`drivers/i2c/busses/` 的 133 个文件、89,120 行完全没有读。**本文档集只讲控制器驱动必须实现的**契约**，不讲任何具体控制器的实现。`05` §4 的 adapter 骨架是按契约写的，不是从某个真实驱动改的。
- **`drivers/i2c/muxes/` 的 10 个文件只读了各自的 `i2c_mux_alloc()` 调用点及其上下文**（2026-08-14 自审时补的；此前只读了 Kconfig，导致 `04` 的锁风格表有 7 个格子是空白）。现在 `04` §1.7 那张表的 flags 一列每格都有行号，但**这 10 个驱动的 select/deselect 实现只有 pca954x / pca9541 / ltc4306 三个读过**，其余是按 flags 值填的。同理 `04` §1.8 那 13 个树外驱动，只读了 `i2c_mux_alloc()` 调用点 + select 函数的调用链，**没有读它们的其余部分**。
- **`05` §5 的 mux 骨架标注参考 `i2c-mux-reg.c`，但我没有逐行读那个文件**（2026-08-14 自审时补读了 `:61-76` 的 select 实现和 `:201-226` 的 probe/`i2c_mux_alloc()` 段，其余仍未读）。
- **`05` §1.5 的电源管理只对照了两个 arm64 控制器**（`i2c-qcom-geni.c` 的 `_noirq` 写法与 `i2c-designware-platdrv.c` 的普通 `suspend` 写法），**没有统计 `busses/` 下其余驱动的挂起阶段分布**。文档里因此只下了"不可移植、别赌"的结论，没给出比例。

### 5.3 推断而非观察的结论

| 结论 | 依据的性质 |
|---|---|
| RT 补丁对 i2c 的三条间接影响（§1.2 的表） | 从 PREEMPT_RT 的通用语义推出，**未在本树上验证** |
| "slave 回调通常在硬中断上下文" | 来自 `Documentation/i2c/slave-interface.rst` 的约束和两个后端用 `spin_lock_irqsave` 的事实；**具体是硬中断还是线程化中断取决于控制器驱动**，我没有统计 |
| `07` §7 错误码表的"最可能的物理原因"一列 | 这是**工程经验判断**，不是从代码或文档读出的。代码只定义错误码的含义，不定义它的物理根因 |
| `07` §8 的五条排查路径 | 同上，是按代码结构组织的推荐流程，**没有在真实故障上验证过** |
| `07` §5 里"在 `i2c-gpio,scl-output-only` 的总线上 `cat scl` 会踩 NULL 解引用" | **纯代码推断，未构造验证**。依据是三点：`scl` 这个 debugfs 文件在 `i2c-gpio.c:288` 是**无条件**创建的、模式 0600（可读），而 `inject_panic`/`lose_arbitration` 在 `:282` 有 `if (priv->bit_data.getscl)` 保护；`WIRE_ATTRIBUTE(scl)` 生成的 getter 直接用 `getscl(bd)` 宏（`:80`），展开成 `(bd)->getscl((bd)->data)`（`:88` 附近）；而 `scl-output-only` 会让 `bit_data.getscl` 为 NULL（`:419-420`/`:318`）。三条都逐行确认过，**但"读它真的会 oops"这个结论没有实机或构造测试支持**，也没有查上游是否已修 |
| `07` §5.3 两个恢复场景的"你会看到什么" | 日志文案和代码路径都逐行确认（`i2c-core-base.c:203-211`/`:248-253`/`:274`/`:280-282`），但**串起来的因果是读代码推的**，没有实机拉低 SDA/SCL 观察过 |
| `06` §7 的 11 行症状对照表 | 同上；每一行的"内核侧成因"都能回到具体行号（见 §3 的 `06` 小节），但"症状 → 成因"的映射是经验归纳 |
| `06` §1 里"某属性只有 N 个驱动读"的计数 | 由 `rg` 实测（§2.6 的方法），但**按文件计数、且宏生成的赋值会漏计**，是下界 |
| `04` §2.4 的算法对比表里"CPU 开销"、"典型使用者" | 定性判断；"典型使用者"一列只对 algo-bit 做了 `rg` 确认（`i2c-gpio.c`、DRM DDC），另两个未确认 |
| `05` §7.1 的 i2c-tools 命令语法 | 来自工具的通行用法，**本机未安装 i2c-tools，未验证** |
| §2.6 里"11 个原子传输驱动、13 个从机驱动全是 arm/arm64" | 由驱动文件名与所在平台人工判断，**没有逐个打开确认 Kconfig 依赖** |
| `02-datastructures.md` §4 的命名规则速查表 | 从观察到的命名模式归纳，不是内核明文规定 |
| `02` §1.0（3）里的 sysfs 路径示例（`ff110000.i2c/i2c-3/3-001b`） | 由 `dev_set_name` + `dev.parent` 推出的必然结果，**没有在 arm64 目标板上实机 `ls` 验证过**；`ff110000` 这个地址取自 RK3399 的 DT 惯例，不是从本树某个 dts 抄的 |
| `02` §1.0（3）里"不设 `dev.parent` 会导致 runtime PM / of_node 继承失灵" | 从设备模型的通用语义推出，**未构造反例验证** |

### 5.4 版本相关的时效性

- 本文档集只对 **6.1.158** 成立。`.probe` → `.probe_new` 的迁移在 6.1 之后完成（`.probe_new` 最终被重命名回 `.probe`），所以 `05` §1 的模板在更新的内核上需要改。
- `i2c_driver::remove` 的返回值在 6.1 之前是 `int`。
- `class` 字段的自动探测机制在后续版本里被逐步移除。
- **本文档集全程没有做上游检索**：没有查 `git log --all`、没有比对 mainline、没有查
  lore/patchwork。所以凡是本文档指出的"看起来是问题"的地方（如 §4 末尾那条未初始化变量），
  都不能推断它在新版本里仍然存在或已被修掉。

---

## 6. 如何复核本文档集

如果你要核对某条论断：

```bash
cd /workspace/safety/linux-6.1.y

# 1. 确认基线一致
git log --oneline -1                 # 应为 f6e38ae624cf
git status --porcelain -- drivers/i2c   # 应为空

# 2. 按文档里的 file:line 直接看
sed -n '2237,2300p' drivers/i2c/i2c-core-base.c

# 3. 如果行号对不上（例如你的树版本不同），按符号名找
rg -n '^\w.*\b__i2c_transfer\b' drivers/i2c/i2c-core-base.c

# 4. 验证一个"结构体字段"类论断
rg -n -A3 'struct i2c_adapter \{' include/linux/i2c.h
```

**行号是脆弱的、符号名是稳定的。**本文档集用 `file:line` 是为了让复核成本最低，但如果你的树不是 6.1.158，请用符号名找。

### 6.1 两个批量检查（每次改完文档都跑）

这两个不是核对内容，而是核对**文档自身的自洽性**。手工回访会漏，脚本不会（§7 第 21 条）。

**（1）`file:line` 越界检查** —— 抓"行号超出该文件实际行数"的引用。

```bash
# 思路：正则抽出所有 `路径:行号` 形式的引用，把裸文件名在若干候选目录下解析成真实路径，
#       再与 wc -l 比对。全 9 篇共约 730 处引用。
```

它抓不到的：**行号在范围内但指错了地方**。这类只能逐个 `sed` 打开看 —— 也正是四轮自审
在做的事（`04` 约 150 处、`05` 43 处、`07` 70 处逐个打开，不抽样）。

**（2）跨篇引用检查** —— 抓 `0X §Y` 指向的小节已经不存在。

```bash
# 思路：从每篇抽出所有 ^#{2,4} 后面的编号（含 A/B/C 前缀）建成集合，
#       再抽出所有 `0X` §Y 形式的引用求差集。
```

当前状态：**170 条引用全部命中**，另有 2 条已知假阳性（见 §3 本篇自审小节末尾的表）。
**每次重排章节号之后必须跑这一条** —— `03` 改 Part A/B/C、`02` 加 §1.0 时都留下了失效引用，
而且都是在"自认为已经手工回访过"之后才被脚本查出来的。

---

## 7. 本方法的可复用部分

（这一节是为把流程固化成 skill 准备的素材。）

对一个陌生的内核子系统写这套文档，实际有效的步骤是：

1. **先定基线**：`git log -1` + `git status` 判断树是否干净、是否打了补丁；**如果打了补丁，必须先查清目标子系统有没有被改**（本例中这一步直接决定了整份文档的定性）。
2. **量规模**：`wc -l` 定出核心/外围的边界，据此决定"读全"与"读符号表"的分界线。核心通常在 5k~10k 行，可以全读；驱动目录通常几万到十几万行，只能讲契约。
3. **先读头文件**：`include/linux/<subsys>.h` 是最高信息密度的入口。数据结构和 API 全在这里，读完就有了骨架。
4. **再读核心 .c**：按依赖顺序（base → 协议 → 扩展）。
5. **读 `Documentation/<subsys>/`**：它给的是**设计意图和已知陷阱**，这些从代码里读不出来。`fault-codes.rst`、`*-topology.rst` 这类文档尤其值钱。
6. **一开始就定死体系结构范围**。本次是 arm64-only。这个决定不是写完再删 —— 它改变**读哪些文件**（`i2c-core-acpi.c` 直接不读）、**统计怎么做**（§2.6 要按 arm64 过滤）、**举例用什么**（DTS 必须取自 `arch/arm64/boot/dts/`）。裁掉了什么要显式记下来（§2.7），否则读者会当成遗漏。
7. **设备树单独成篇，并且每条属性都要找到消费者**。这是本次最有价值的方法收获：先建"谁消费什么"的三方分工表（通用 DT/驱动模型 / 子系统核心 / 具体控制器驱动），再对每个属性跑一次 `rg -n '"属性名"' drivers/<subsys>/`。**找不到消费者的属性就是死字**，而绑定文档不会告诉你这件事（它只会说 "may not be supported by all drivers"）。本次靠这一步发现了两个非显然结论（见 §3 的 `06` 小节末尾）。举例一律用 in-tree 的真实节点，读者能 diff。
8. **索引工具只用来导航，不用来取证**。宏和函数指针是内核的两大主力机制，恰好是索引工具的两大盲区。
9. **每写一条因果链就回去确认一次**。初稿阶段就有五处假设被源码推翻（RT 归因、fault-injector 位置、remove 返回值、`smbus-alert` 是否创建 ARA、`i2c_parse_fw_timings` 是否由核心调用）。
    但真正的数量级是**逐篇 review 之后才暴露的** —— 全部记在 §3 各篇的修正表里：

    | 篇 | review 形式 | 修正数 |
    |---|---|---|
    | `00`/`01` | 用户提结构性意见 → 重排 | 各若干（含 `00` §5 抄错的时间线次序） |
    | `02` | 用户提了一个类比问题 → 补 §1.0 | 3 |
    | `03` | 用户问"组织逻辑是什么" → 全量重排 | 15 处陈旧交叉引用 + 时间线次序 |
    | `04` | 用户不熟 → **自审** | 19 |
    | `05` | 用户不熟 → **自审** | ~20（含 3 个**根本不存在的符号**） |
    | `06` | 用户说"读起来费劲" → 重排 | 15 处重排 + 5 处行号 |
    | `07` | 用户不熟 → **自审** | 21（含 2 处凭空编造） |
    | `99` | **自审本篇** | 6（见本篇末） |

    可复用的结论有两条。**一是初稿的错误率与"这一篇有没有人能提问"强相关**：三篇用户不熟的
    （`04`/`05`/`07`）都要靠自审，而自审抓出的错比用户 review 抓出的**更严重**
    —— 用户能发现的是"读不懂"，自审才能发现"根本不存在"。
    **二是每一轮 review 自己会产生新的错误**：`04` 的自审新写了两条错的全称断言，
    `05` 新写了一个不存在的 sysfs 节点，`07` 新写了三处（含一段编造的命令输出）。
    所以**自审的对象必须包含自审这一轮新写的文字**，不能只审原文。
10. **老实记下没验证的部分**。这一节（§5）的价值不低于前面八篇 —— 它决定了读者该在哪里加倍小心。
11. **按"查询动作"而不是"内容类别"分篇**。初稿把代码地图和 Kconfig 放在 `00`，把 API 放在 `01`，
    结果每次"这个函数在哪个文件"都要跨两篇。改成**代码地图并入 `01` §0** 之后，"找文件"和
    "找 API"是同一次查询。同理，`00` 的定位从"总览"改成**复习入口** —— 把 `02`/`03` 的骨架
    （结构体压缩链、机制速览、端到端路径图）浓缩进去，隔几个月回来只读 `00` 就能恢复全貌，
    要细节再按行号下钻。判断标准很简单：**读者的一次提问，应该只落在一篇文档里。**
12. **API 篇的表格固定三列：`API / 说明 / 备注`**。初稿每节列都不一样（有的是"声明/语义/上下文"，
    有的是"定义/字节序列/返回值"，有的只有"API/位置"），读者每换一节就要重新适应一次表头。
    统一成三列之后规则只有一条：**「说明」一句话讲它做什么，其余全部进「备注」**
    —— 上下文标签（粗体前置的"可睡眠/不可睡眠/原子可用"）、返回值陷阱、参数类型提示、
    `inline`/宏、已废弃、arm64 不涉及。列数固定还有个附带好处：新增一个 API 时不用想放哪。
13. **签名只写"函数名 + 形参名"，位置只精确到文件**。两件事都是为了让表能一眼扫完：
    - 初稿照抄头文件原型，长的一行占满屏宽把表撑变形，眼睛全花在 `struct i2c_adapter *` 上。
      改成 `i2c_transfer(adap, msgs, num)` 后一屏能看完一整节。**类型不是删掉而是搬家**：
      返回值语义、回调原型、结构体名移进「备注」（"无返回值"、"失败返回 `ERR_PTR()`，
      用 `IS_ERR()` 判断"、"`fn` 原型 `int (*)(struct device *, void *)`"）。
    - 逐 API 的 `文件:行号` 列同样删掉，换成每节开头一行「头文件：…；实现：…」。
      理由：**行号在 API 篇是噪声**（读者要的是"在哪个文件"，进了文件搜函数名比记行号快），
      而且行号是全文档集最易腐的信息，集中在 `03`（实现剖析）和 `99`（取证）两篇里维护成本低得多。
      同理 §0 代码地图里的**行数**也删掉了 —— 行数只对"决定哪些文件要全读"的编者有用，对读者没用。
    三个例外**不要动**：C 代码块（骨架示例必须能编译）、ioctl 表的"参数"列（那本来就是实参类型
    而不是函数签名，要在表前点明这一点）、以及本身是变量而非函数的符号（如 `i2c_bit_algo`，
    要在备注里点明它是变量）。
14. **数据结构篇开头必须先破歧义，再画图**。`02` 初稿一上来就是从 `bus_type` 出发的大图，
    结果读者的第一个问题是"`i2c_adapter` 和 `i2c_algorithm` 是不是总线模型里的 device 和 driver？"
    —— 这说明图画得再全，也补不上一个**没被显式否掉的错误类比**。补出来的 `02` §1.0
    只做一件事：把三组最容易混的关系钉死（容器 vs ops 表、谁才是真正的 device/driver 对、
    本模块总线 vs 它挂靠的父总线）。做法是三步：
    - **先否掉错误类比再讲正确的**（"不是 device↔driver，它等同于 `file_operations`"），
      比只正面陈述有效；
    - **用一张对比表把"是不是 struct device / 注册到哪条总线 / 会不会绑 driver"逐行摊开**，
      让读者自己看出差别；
    - **把"为什么不会发生"也找到代码依据**。"适配器永不绑定 driver"不是设计意图的推测，
      而是 `i2c_verify_client()` 的 `dev->type` 判断加上 `i2c_device_probe()` 里
      `if (!client) return 0;` 两处代码保证的 —— 否定性论断同样要给行号。
15. **每个模块都要交代"它和它的父总线是什么关系"**。子系统总线（i2c/spi/mmc…）几乎都是
    "控制器在 platform 总线上、设备在子系统总线上、两者只由 `dev.parent` 缝在一起"这个形状，
    而这件事在子系统自己的代码里**没有任何一处集中表达**（唯一的痕迹就是控制器驱动 probe 里
    那一行 `adap->dev.parent = &pdev->dev`），所以必须由文档补上。三个必查点：
    - 控制器 DT 节点由谁变成设备（`of_platform_populate` 还是子系统自己的扫描函数）；
    - 子节点会不会被 platform 层继续下钻（查 `of_default_bus_match_table[]`，
      不在表里就不会 —— 这是"为什么 i2c 节点不用写 `simple-bus`"的答案）；
    - 两条总线的连接点具体是哪一行赋值。
16. **组织轴要显式声明，而且正文必须真的按那个轴排**。`03` 初稿开头写着"按从内核启动到
    一次传输完成的时间顺序展开"，但正文实际上把四条不同的轴编在了一起：时间线（初始化→
    注册→传输）、横切机制（锁、引用计数、电源管理、可观测性）、分支与出错路径（SMBus 仿真、
    总线恢复）、静态地图与尾声（分层图、架构评价）。更糟的是**时间线本身还印错了顺序** ——
    §3（match/probe）排在 §4（适配器注册）和 §9（枚举）前面，可实际上枚举是在
    `i2c_register_adapter()` 内部第 `:1588` 行触发的，probe 在时间上最靠后。
    读者因此看不出组织逻辑，也就问了出来。三条可复用的做法：
    - **一篇文档只承诺一条主轴，其余内容显式分区**。改后 Part A 是时间线，Part B 收所有
      横切机制，Part C 是回头评价。分区用 `A`/`B`/`C` 前缀写进章节号里，这样**一个引用
      `§A5` 或 `§B1` 自带"它在哪条轴上"的信息**，比纯数字编号 `§5`/`§7` 多带一份元信息。
    - **主轴要配一张地图放在最前面**（§0.2），并在图里标出每个节点对应的章节号。
      长文档（本篇 800+ 行）没有导航节，读者是靠目录猜结构的。
    - **时间线的顺序要用代码验证，不能凭直觉排**。判据是"谁调用谁"：
      `i2c_register_adapter()` 里第 `:1588` 行调 `of_i2c_register_devices()`，
      第 `:1596` 行调 `bus_for_each_drv()`，所以注册 → 枚举 → 撮合的次序是代码定死的。
      排完之后要顺手检查**别处有没有抄走同一个错误次序**（本例 `00` §5 就抄了）。
17. **"框架篇"（`04`）没有读者能提的问题，就自己当红队逐条回验**。用户对 `04` 的内容不熟，
    提不出实质问题，于是这一篇的 review 换成了**自审**：把全篇约 150 处 `文件:行号`
    逐个 `sed` 打开核对，不抽样。结果很有规律，四条可复用：
    - **错误全部集中在"写了概述、没有逐字引用源码"的段落**。凡是当初贴了真实代码片段的
      地方一处没错；凡是当初图省事写成"大意是 X"的地方，错误率极高（本次 19 处修正里
      有 14 处属于这类）。**所以取证时的判断标准不是"我理解得对不对"，而是"我有没有
      把源码原样搬过来"** —— 理解可以对，概述仍然会漏掉决定性的那半句
      （如 `:124-125` 那句 "SMBus-only adapters don't support that interface"）。
    - **表格里的"—"和"可配"是欠债的记号，必须清掉**。`04` 初稿的 mux 驱动表有 7 个格子
      写"—"（当时没读驱动），还有一格凭 DT 属性名猜成"可配"。逐个打开驱动读
      `i2c_mux_alloc()` 的实参之后，不但填满了表，还反推出了整节的核心结论
      （为什么某个驱动只能选 mux-locked）。**未知不该以占位符的形式留在正文里。**
    - **"全树唯一 / 没有一个 / 只有 X 这么用"这类全称断言，必须在整棵树上 grep**。
      本次我在自审过程中自己新写了两条这样的断言，又在同一次自审里推翻了它们 ——
      因为只 grep 了 `drivers/i2c/muxes/`。**框架的使用者不一定住在框架自己的目录下**：
      i2c-mux 的 22 个调用点里有 13 个在 `drivers/iio`、`drivers/media`、`drivers/gpu/drm`、
      `drivers/power/supply` 下，而且那 13 个的用法（1 通道的旁路"门"）比 `muxes/` 里的
      正统 mux 芯片更常见。写全称断言时的检查动作是固定的：
      `grep -rn "<框架入口函数>(" --include=*.c | grep -v <框架自己的目录>`。
    - **反例要老实留在文档里**。同一批驱动里 `inv_mpu6050` 的 select 是空函数，
      不符合"mux-locked 是被迫的"这个结论。写上它比藏起来有用 ——
      它把结论的方向性钉准了（A⇒B 成立，B⇒A 不成立）。
18. **"示例篇"（`05`）的自审动作和别的篇不一样：先验符号存在性，再验行号**。
    `05` 全是手写代码骨架和命令行，行号只有 43 处（比 `04` 的 150 处少得多），但它是
    **唯一会被读者直接抄进自己代码里**的一篇，错误的代价最高。这次自审在"逐个 `sed` 打开
    行号"之外多加了一个动作：**把正文里出现的每个内核函数名 / 宏名 / 标志名 / 内核打印的
    字符串，都跑一次全树 grep 确认它在本版本存在**。结果抓出三个**根本不存在的符号**
    （`i2c_client_get_device_id()`、`I2C_CLIENT_USER`、一句我自己拼出来的 WARN 文案），
    而它们**通读一百遍也发现不了** —— 因为命名规律太合理，读起来比真实 API 还顺。
    四条可复用：
    - **检查动作固定**：`grep -rn "<符号名>" --include=*.c --include=*.h`（含 `include/`）。
      空结果 = 这个符号是幻觉，不管你多确信。**内核打印的字符串同样要 grep 原文**，
      不能凭记忆复述，否则读者拿它去 `dmesg | grep` 什么也搜不到。
    - **"名字很像"是高危信号，要成对写进文档**。本例 `i2c_get_device_id()` 真实存在但
      语义完全不同（发 SMBus 0x7c 问芯片要 ID，不是查 id_table），
      `i2c_client_get_device_id()` 不存在。凡是发现这种近邻，**把两个都写上并点明区别**，
      比只写对的那个更能防错。
    - **示例篇最容易积攒跨篇陈旧引用**，因为它到处 "详见 0X §Y"。别的篇改了章节号或推翻了
      结论，`05` 不会自己跟着改：本次 `05` §5 的锁风格表还留着 `04` 自审时已推翻的旧判据，
      §2 还指着 `02` 加节前的旧节号。**所以示例篇的自审必须包含一遍交叉引用回访**：
      对每个 "见 0X §Y" 打开目标确认小节号和结论都还在。
    - **同一个数字在不同路径上未必是同一个意思**。`05` 里的 8192 有两种语义
      （`read()` 静默截断 vs `I2C_RDWR` 超限 `-EINVAL`），`0x1000` 有两套来源
      （sysfs 的 `I2C_ADDR_OFFSET_SLAVE` vs DT 的 `1<<30`）。**写"上限/编码"类事实时，
      要问一句"哪条路径上的？超了会怎样？"** —— 两条路径的常量恰好都汇合到同一个内部标志，
      所以只看结果是分不出来的。
19. **"内容对但读不懂"是一类独立的缺陷，要单独走一遍重排，而且不能顺手改结论**。
    用户对 `06` 的反馈是"内容基本可以，就是有些地方读起来费劲"。这类反馈**不该按纠错处理**
    —— 一边重排一边改结论，读者就无法判断这一版和上一版哪里变了。做法是：
    **重排轮只换承载形式，事实一条不动；顺手发现的行号错另立一张表记账**（本次 5 条）。
    五种反复出现的"费劲"形态，和对应的固定改法：
    - **一个表格单元格里塞了两三件事** → 拆列，或把其中一类移到表外成段落。
      判据很机械：**一个单元格如果需要用"；"或括号连接两个不同来源的事实，就该拆。**
      （`06` §1 的"典型属性 + 判断特征 + 实测规模"、§7.2 的"三种什么都不打印的原因"）
    - **散文在描述 if/else** → 画决策树，并在危险的那条岔路上标 、写清后果。
      （`06` §4.1 的通道节点查找：原来是 4 步线性列表，读者看不出哪一步会静默失败）
    - **链式"否则…否则…"的 ASCII 图** → 改成优先级表（一行一个条件 + 结果 + 行号），
      副作用另起一段。ASCII 图适合画**结构**，不适合画**优先级**。
    - **一句话里同时挂着行号、日志文案和触发条件** → 按"是什么 / 谁不做什么 / 所以怎样"
      拆成 2–3 句编号。（`06` §6 的 `FWNODE_FLAG_NOT_DEVICE`、§5.1 的开漏三层）
    - **同一类东西有 N 种写法却没有选型入口** → 先给一张选型表再展开细节。
      （`06` §4.0：5 种 mux 写法；读者的第一个问题永远是"我该用哪种"，不是"它们怎么工作"）
    另外三条结构性动作：
    - **篇首加「按任务找章节」表**。长篇（`06` 800+ 行）的读者极少从头读，
      入口应该是**动作**（"我要挂一颗芯片"），不是章节号。
    - **散落各节的同类事实要抽一张总表放前面**（`06` §1.1：核心真正会读的 12 个属性）。
      抽表时最容易补齐的一列恰恰是最容易出错的那一列 —— 本例是**"写在哪个节点"**。
    - **症状清单超过 8 行就按"坏掉的范围"分组**（`06` §7.3 → 总线级/单设备级/参数级/mux 级）。
      分组顺序本身就是排查顺序，比 12 行平铺多传递了一层信息。
    最后：**重排会动章节号，所以重排轮结束必须回访跨篇引用和本文件里的 "见 0X §Y"**。
    本次就有一条 `99` §5.1 指向 `06` §4.1 的引用因为 §4.1 被改写而失效。
20. **"调试篇"（`07`）整篇都在教读者"去看某个输出"，所以自审的对象是输出本身，不是论断**。
    别的篇写的是"代码怎么工作"，`07` 写的是"你会看到什么" —— 日志、tracepoint、sysfs 文件、
    错误码。因此错误形态高度集中，六条固定检查动作：
    - **每条日志都要查级别，而不只是查文案存在**。文案对、级别错，读者照做仍然什么都搜不到。
      本次最典型的是总线恢复：**最常见的失败走 `dev_dbg`（默认不可见）**，只有两个次要分支走
      `dev_err`，所以"`dmesg | grep -i recover` 看不到东西"恰恰是最常见的情况 ——
      原文把这条排查路径写成了必然可见。检查动作：把 `dev_err` / `dev_warn` / `dev_dbg` /
      `pr_debug` / `WARN` / `dev_err_ratelimited` 当成**五个不同的可见性等级**逐条标注。
      顺带两个特例值得单独记：`WARN()` 会带栈回溯（读者会以为是 panic），
      而带 "打印一次" 标志的（本例 `I2C_ALF_SUSPEND_REPORTED`）**一个对象一生只打一次**，
      复现时看不到第二遍 —— 这两点都会让读者判断失误。
    - **可写的调试文件，写进去的值是什么意思必须从 setter 里读出来，不能猜**。
      本次把故障注入器的参数猜成了"第 N 个 SCL 周期"，实际是**微秒时长**，
      而且**触发时机是第一个 SCL 下降沿**、**写操作会阻塞**。三件事都在 setter 函数里明写着。
      同一张表里六个文件恰好有三种语义（电平 / 7 位地址 / 微秒时长）——
      **同名的"debugfs 数值文件"之间没有任何统一约定**，必须逐个读。
    - **一个调试开关往往有多道互相独立的门，要全部列出**。本次三个 algo 的 `i2c_debug`
      看着一模一样，实际是三种情况：`module_param()` 的**权限位**决定有没有 sysfs 文件
      （权限 0 就没有，压根不能运行时改），`#ifdef DEBUG` 决定这段代码**在不在**，
      用 `dev_dbg` 还是裸 `printk` 决定它**受不受 dynamic_debug 管**。
      三者两两独立，缺任何一道说明读者都会白忙。同理只读权限（`S_IRUGO`）的参数
      **只能 modprobe/cmdline 给**，不能运行时写。
    - **错误码表的每一行都要点名"哪个函数返回它"**，否则那一行不可复核。做完这件事会顺带
      发现两类问题：**同一个错误码有多个来源**（本次 `-EBUSY` 有 5 处、语义各不相同），
      以及**归错主**（本次 quirks 失败被写成 `-EINVAL`，其实是 `-EOPNOTSUPP`）。
      还要留意**错误码被中途改写**的路径 —— 本次发现 `i2c_recover_bus()` 会把
      `-EOPNOTSUPP` 改写成 0，即"恢复成功"是假的。这种路径只有顺着 return 值读才会露出来。
    - **tracepoint 要连 `TP_CONDITION` 和字段清单一起读**。三件事全靠它：
      哪些情况**根本不触发**（本次四个 smbus 点的条件两两不同，其中一个是另一个的取反）、
      **循环上界是失败前还是失败后的计数**（本次 reply 事件用的是返回值而非消息总数，
      所以传输失败时一条 reply 都没有）、以及**同名字段在不同事件里未必同义**
      （本次 `f=` 有三种含义：msg flags / 未掩码的 client flags / 已掩码的）。
      字段清单还决定读者的过滤器写不写得出来 —— 按不存在的字段写 filter 会失败。
      另外**事件组级过滤有静默陷阱**：套不上的事件会被静默改成"无过滤全量输出"，
      而写入还返回成功。这类"错了但不报错"的机制必须显式写进文档。
    - **示例命令的输出是和内核字符串同等级的事实，绝对不能编**。本次我编了一段
      `cat .../filter` 的报错输出，还编了一个适配器名 `i.MX I2C adapter`（全树 0 命中）
      —— 后者甚至跨篇复制到了 `05`。判据和第 18 条一致：**凡是"读者会拿去 grep 或比对的
      字符串"，都要有出处**。适配器名这类东西还有个额外坑：它**本来就不固定**
      （固定串 / `dev_name()` / `pdev->name` / `snprintf` 格式串四种写法并存），
      所以正确做法不是换一个真名字，而是**先说明它为什么不固定**，再给一个有行号的实例。

21. **取证篇（`99`）也要自审，但它的失效方式是"过期"而不是"写错"**。别的篇论断的对象是内核，
    取证篇论断的对象是**作者自己**（"哪些文件读完了""哪些只读了符号表""哪些没验证"）。
    这类论断几乎不会一开始就是假的 —— 它是**被后续每一轮 review 慢慢变假的**：
    补读了代码，二等证据没升级成一等；重排动了章节号，指过去的引用没跟着改。
    本次自审 6 处问题**全部**属于这两类，一处编造都没有。四条固定动作：
    - **把本篇里所有可机械复现的东西全部重跑一遍**。取证篇的信誉全靠这个：
      基线命令、规模统计、能力普查、工具版本、文档行数、自陈问题的行号。
      本次重跑了 9 组、约 60 个数字，全中 —— 这个"全中"才是读者敢信前八篇的理由。
      反过来说，**只要有一个数字对不上，整篇的性质就从"取证"降级成"回忆"**。
    - **每轮补读之后要回去升级证据等级**。这是最容易漏的一步，因为补读发生在写别的篇的时候，
      注意力不在本篇。判据很机械：**本轮新引用的行号，有没有落在本篇登记的"详读范围"之外？**
      落在外面就说明范围变了。本次两个文件（algo-bit、i2c-gpio）都是这样查出来的，
      其中 i2c-gpio 从"读了 40 行"变成"读了 228 行"，`07` §5 整节的可信度全押在这个数字上。
    - **跨篇引用要用脚本查，不能靠回访**。做法：正则抽出所有 `0X §Y`，
      与各篇实际标题（`^#{2,4} 编号`）求差集。本次 90 条命中、**3 条失效**，
      而这 3 条恰好是我上一轮"手工回访"时自认为查过的 —— 手工回访会漏，脚本不会。
      **重排轮结束必须跑这个脚本**，它比人可靠。
    - **一等/二等证据的分界线本身要写清"为什么"**。不是为了严谨好看：
      读者据此决定**哪些结论可以直接用、哪些要自己再确认一遍**。
      所以"只读了符号表"这句话必须落到具体函数和行号区间，
      写成"部分阅读"就等于没写。

**固化下来的文件骨架（9 篇）**：

```
<模块名>/
├── 00-overview.md       功能定位 + 硬件与协议背景 + 结构与机制速览 + 阅读路径
│                        定位是"复习入口"：02/03 的骨架都压在这里
├── 01-api.md            §0 代码地图（文件/头文件/Kconfig）+ 对外 API + 错误码语义
│                        "找文件"和"找 API"放同一篇，不要拆到 00
│                        表格固定三列 API/说明/备注；签名不带类型；位置只到文件不到行
├── 02-datastructures.md 核心结构体 + 结构关系 ASCII 图 + 全局变量与锁
│                        开篇先立 §1.0「先分清 N 组关系」：否掉错误类比、
│                          摊开对比表、给出"为什么不会发生"的行号
│                        必须有一张"本模块总线 ↔ platform 总线"的图
├── 03-internals.md      注册/匹配/传输/锁/恢复等主流程逐段剖析
│                        只承诺一条主轴：Part A 时间线 / Part B 横切 / Part C 评价，
│                          章节号带 A/B/C 前缀，引用自带"在哪条轴上"的信息
│                        开篇 §0 必须有导航：分层图 + 生命周期时间线图 +
│                          "哪些内容不在本篇"的去处表
│                        时间线次序用"谁调用谁"验证，别凭直觉排
├── 04-extensions.md     子框架（本例：mux / algos / slave / smbus / 枚举 / 字符设备）
│                        组织轴是"一节一个独立框架、互不调用"，要在篇首声明，
│                          并给一张"哪几个在目标架构上真会碰到"的分诊表
│                        每个框架都要交代它挂在 03 时间线的哪一点上
│                        全称断言（"全树唯一"）必须全树 grep，不能只 grep 框架目录
├── 05-usage.md          各角色的代码骨架（client / adapter / mux / slave / 用户态）
│                        篇首必须声明"骨架未编译"，别把免责声明只放在 99
│                        正文出现的每个符号都要全树 grep 确认本版本存在（含内核打印的字符串）
│                        自审时回访所有 "见 0X §Y"：示例篇最容易积陈旧交叉引用
├── 06-devicetree.md     DTS 怎么写 + 每个属性由谁消费 + 配错了怎么定位
│                        篇首「按任务找章节」表；§1.1 一张「核心真正会读的属性」总表
│                          （必须有"写在哪个节点"一列），后面每节是它的一行展开
│                        每条属性都要有定位到行的消费代码；只有某个驱动读的要点名
│                        有静默失效陷阱的地方画决策树并标 （例：mux 分组节点误加 reg）
│                        定位章按「编译期 / 运行期静态 / 运行期动态」三层，
│                          症状清单按坏掉的范围分组（总线级/单设备级/参数级/拓扑级）
├── 07-debug.md          编译期开关 / tracepoint / sysfs / debugfs / 故障注入 / 错误码分诊
│                        每条日志都要标可见性等级（dev_err / dev_dbg / pr_debug /
│                          WARN / ratelimited / 一生只打一次），"默认看不见"要写在最显眼处
│                        每个调试开关列全所有门：module_param 权限位、#ifdef、
│                          用 dev_dbg 还是裸 printk（决定受不受 dynamic_debug 管）
│                        可写调试文件的"写进去的值是什么意思"必须从 setter 读，不能猜
│                        错误码表每行点名返回它的函数；留意同码多源与"被改写"的路径
│                        tracepoint 连 TP_CONDITION + 字段清单一起读（决定过滤器写法）
│                        示例命令的输出不许编，与内核字符串同等级
└── 99-evidence.md       本篇：证据、方法、已发现的自陈问题、明确未验证项
                         一等/二等证据要分开，二等必须写明"详读了哪几行"
                         所有数字都要能一条命令重跑；每轮 review 后全部重跑
                         补读过代码就回来升级证据等级（最易漏）
                         跨篇引用用脚本查，不靠手工回访
```
