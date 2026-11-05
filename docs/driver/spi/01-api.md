---
title: API 参考
---

# SPI 对外 API 与代码地图（Linux 6.1.158, arm64）

> 本篇是**查询篇**：「这个功能在哪个文件」和「这个函数叫什么」都在这里回答，
> 不需要跳到别处。
>
> **格式约定**（全篇统一）：
> - 表格固定三列 `API / 说明 / 备注`。「说明」一句话讲它做什么，其余全进「备注」。
> - 函数签名**只写函数名 + 形参名，不带类型**。返回值语义写在「备注」或每节末尾。
> - 位置只精确到**文件**，不到行号。每节开头一行「头文件 / 实现」交代出处。
>   逐行号在 `03-internals.md` 和 `99-evidence.md` 里维护。

---

## §0 代码地图

### §0.1 核心文件

| 文件 | 职责 | 本篇里对应的 API | 实现剖析 |
|-----|------|---------------|---------|
| `drivers/spi/spi.c` | 全部核心逻辑：总线注册、设备枚举、消息泵、传输、片选、DMA 映射、统计 | §1 ~ §6 全部 | `03-internals.md` Part A/B 全篇 |
| `drivers/spi/spi-mem.c` | `spi-mem` 子框架：把「命令+地址+dummy+数据」这种 flash 语义翻译成 SPI 传输 | §7 | `04-extensions.md` §2 |
| `drivers/spi/internals.h` | 核心内部共享声明（`spi_dma_sync_for_*`、`spi_map_msg` 等），**只给 `drivers/spi/` 内部用** | 不对外，本篇不列 | `03-internals.md` §B3 |
| `drivers/spi/spidev.c` | 用户态字符设备 `/dev/spidevB.C` | §8（ioctl） | `04-extensions.md` §4 |
| `drivers/spi/spi-bitbang.c` + `spi-bitbang-txrx.h` | 通用 bit-bang 引擎，给纯 GPIO 控制器复用 | §9 | `04-extensions.md` §6 |
| `drivers/spi/spi-loopback-test.c` | 自检模块：15 个内建测试用例 + 8 个模块参数 | 不是 API，见 `07-debug.md` §4b | — |
| `drivers/spi/spi-mux.c` | 用一颗外部 mux 芯片把一条 SPI 总线扩成多条 | 无对外 API | `04-extensions.md` §5 |
| `drivers/spi/spi-slave-time.c` / `spi-slave-system-control.c` | slave（target）模式的两个示例协议实现 | 无对外 API | `04-extensions.md` §3 |

> 核心 `drivers/spi/spi.c` 4,632 行；`drivers/spi/` 全目录 137 个 `.c` + 12 个 `.h`
> 合计 105,974 行。也就是说 96% 的代码是控制器驱动。逐文件行数见 `99-evidence.md` §1.4。

### §0.2 外围目录

| 目录 / 文件模式 | 里面是什么 | 本文档集怎么处理 |
|--------------|----------|---------------|
| `drivers/spi/spi-*.c`（约 130 个） | 各 SoC / 各芯片组的控制器驱动 | 只讲**契约**（要实现哪些回调），不逐个剖析。代表驱动 3 个，选取理由见 `99-evidence.md` §2.2 |
| `drivers/spi/atmel-quadspi.c`、`spi-cadence-quadspi.c` 等 | QSPI 控制器，走 `mem_ops` 而不是 `transfer_one` | `04-extensions.md` §2 讲清两条路的分界 |
| `drivers/mtd/spi-nor/` | SPI NOR flash 协议驱动，`spi-mem` 的最大消费者 | 不属于 SPI 子系统，只在 `04-extensions.md` §2 提一次接口 |
| `drivers/spi/spi-tle62x0.c` | 唯一一个「不是控制器也不是纯协议驱动」的怪例（带自定义 sysfs） | 只在 `07-debug.md` §3 顺带一提 |
| `arch/arm64/boot/dts/**/*.dts{,i}` | 板级 SPI 拓扑声明 | `06-devicetree.md` 全篇 |

### §0.3 头文件映射

| 头文件 | 给谁看 | 里面有什么 |
|-------|-------|----------|
| `include/linux/spi/spi.h` | **协议驱动 + 控制器驱动都要看**，1,614 行 | `spi_device` / `spi_controller` / `spi_message` / `spi_transfer` / `spi_driver` 全部结构体，以及本篇 §1 ~ §6 的全部函数声明 |
| `include/uapi/linux/spi/spi.h` | 内核 + 用户态共用，43 行 | 模式位（`SPI_CPOL`/`SPI_CPHA`/`SPI_MODE_0..3`/`SPI_TX_QUAD`…）。**只有这一处定义**，`include/linux/spi/spi.h` 直接 include 它 |
| `include/linux/spi/spi-mem.h` | 写 QSPI 控制器 或 flash 协议驱动的人，404 行 | `spi_mem_op` / `spi_controller_mem_ops` / `spi_mem_driver` + §7 的函数 |
| `include/uapi/linux/spi/spidev.h` | 用户态程序，123 行 | `spi_ioc_transfer` + 全部 `SPI_IOC_*` ioctl 号 |
| `include/trace/events/spi.h` | 调试用，239 行 | 9 个 tracepoint 定义 |
| `drivers/spi/internals.h` | **只有 `drivers/spi/` 内部**，43 行 | `spi_map_msg` / `spi_unmap_msg` / `spi_dma_sync_for_{device,cpu}`。控制器驱动**不该** include 它 |

> 另有 `include/linux/spi/` 下约 20 个**具体芯片**的板级数据头（`ads7846.h`、
> `mmc_spi.h`、`pxa2xx_spi.h`…）。它们属于各自的驱动，不是 SPI 核心 API，本篇不列。

### §0.4 Kconfig

| 选项 | 位置 | 作用 |
|-----|------|------|
| `SPI` | `drivers/spi/Kconfig` | 总开关（`menuconfig`）。关掉之后 `spi_register_controller()` 等符号全部不存在 |
| `SPI_MASTER` | 同上 | 主机（host）模式支持。**`def_bool y` 依赖 `SPI`**，实际上没法单独关 |
| `SPI_MEM` | 同上 | `spi-mem` 子框架。被 QSPI 控制器和 `spi-nor` 通过 `select` 自动打开 |
| `SPI_SLAVE` | 同上 | slave（target）模式。开了才有 `spi_slave_abort()` 和 `slave` sysfs 属性 |
| `SPI_SLAVE_TIME` / `SPI_SLAVE_SYSTEM_CONTROL` | 同上 | 两个 slave 协议示例，依赖 `SPI_SLAVE` |
| `SPI_SPIDEV` | 同上 | 用户态字符设备 `/dev/spidevB.C` |
| `SPI_LOOPBACK_TEST` | 同上 | 自检模块。**`depends on m`**——只能编成模块，不能编进内核 |
| `SPI_DEBUG` | 同上 | 打开核心的 `DEBUG` 宏，让 `dev_dbg()` 无条件输出。`depends on DEBUG_KERNEL` |
| `SPI_DYNAMIC` | 同上 | `def_bool ACPI \|\| OF_DYNAMIC \|\| SPI_SLAVE`。**不是给人选的**，它控制 `spi_add_device()` 里那段防重入代码是否编入 |
| `SPI_<厂商>` | 同上 | 约 130 个控制器驱动各自的开关 |

> 具体行号见 `07-debug.md` §1（那一篇需要行号来说明「哪些开关是多道门」）。

---

## §1 控制器驱动 API（分配与注册）

**头文件**：`include/linux/spi/spi.h`；**实现**：`drivers/spi/spi.c`

### §1.1 分配

| API | 说明 | 备注 |
|-----|------|------|
| `spi_alloc_host(dev, size)` | 分配一个主机模式的 `spi_controller`，附带 `size` 字节的驱动私有区 | **6.1 里的新名字**，返回 `NULL` 表示失败。私有区用 `spi_controller_get_devdata()` 取 |
| `spi_alloc_target(dev, size)` | 同上，但是 target（从）模式 | 需要 `CONFIG_SPI_SLAVE`；未开时返回 `NULL` |
| `spi_alloc_master(dev, size)` | `spi_alloc_host()` 的旧名字 | **仍是绝大多数 in-tree 驱动在用的形式**：`drivers/` 下 **81 个文件 / 84 处**，而 `spi_alloc_host` **0 处**。语义完全等价 |
| `spi_alloc_slave(dev, size)` | `spi_alloc_target()` 的旧名字 | `drivers/` 下 **7 个文件 / 8 处**；`spi_alloc_target` **0 处** |
| `devm_spi_alloc_host(dev, size)` | 带 devres 的 `spi_alloc_host()` | 推荐用法。**注意它只自动 `put`，不自动 unregister**——unregister 要另外用 `devm_spi_register_controller()` |
| `devm_spi_alloc_target(dev, size)` | 带 devres 的 target 版 | 同上 |
| `devm_spi_alloc_master(dev, size)` / `devm_spi_alloc_slave(dev, size)` | 上两者的旧名字 | `drivers/` 下分别 **39 / 2 处**（对比新名字：`devm_spi_alloc_host` **3 处**、`devm_spi_alloc_target` **0 处**）|
| `__spi_alloc_controller(dev, size, slave)` | 上面 4 个非 devm 版**都是它的 `static inline` 包装** | **驱动直接调用它的地方一个都没有**（`drivers/` 下 `.c` 里只有 `spi.c` 自己的定义/kerneldoc/`EXPORT_SYMBOL_GPL`；`spi.h:789`/`798`/`804`/`813` 那 4 处是包装函数本身，不是调用者）|
| `__devm_spi_alloc_controller(dev, size, slave)` | devm 版的底层，同样被 4 个 inline 包装（`spi.h:823`/`832`/`838`/`847`）| 同上，驱动零直接调用。它内部再调 `__spi_alloc_controller()`（`spi.c:2968`）|

**返回值语义**：全部返回 `struct spi_controller *`，失败返回 `NULL`（不是 `ERR_PTR`）。

**新旧名字的真相**：`spi_alloc_master` 等不是「废弃函数」。但要分清**两种**新旧对应关系，
它们在头文件里的实现方式完全不同：

**(1) 真正的兼容宏层**——`include/linux/spi/spi.h:1592-1612`，一整块 `/* Compatibility layer */`，
全部是 `#define`，旧名字**展开成**新名字：

| 旧名 | 新名 | 位置 |
|-----|------|-----|
| `spi_master` | `spi_controller` | `:1593` |
| `SPI_MASTER_HALF_DUPLEX` / `_NO_RX` / `_NO_TX` / `_MUST_RX` / `_MUST_TX`（5 个）| `SPI_CONTROLLER_*` | `:1595-1599` |
| `spi_master_get` / `_put` / `_get_devdata` / `_set_devdata` | `spi_controller_*` | `:1601-1605` |
| `spi_master_suspend` / `_resume` | `spi_controller_*` | `:1606-1607` |
| `spi_register_master` / `spi_unregister_master` / `devm_spi_register_master` | `spi_*_controller` | `:1609-1612` |

**这块里只有 5 个标志**。`flags` 字段一共 7 个标志位，其中
`SPI_CONTROLLER_SUSPENDED`（`:556`）从来只有新名字，而
**`SPI_MASTER_GPIO_SS`（`:555`，BIT(5)）从来只有旧名字**——
改名那一轮漏了它，本树里**不存在** `SPI_CONTROLLER_GPIO_SS`。别照着别的标志去猜名字。

**(2) alloc 家族——不是别名，是八个各自独立的 `static inline`**（`spi.h:786-848`）。
旧名和新名之间**没有任何 `#define` 关系**，是两两成对地包同一个底层函数：

| 旧名 | 新名 | 两者共同调用的底层 |
|-----|------|-----------------|
| `spi_alloc_master` `:786` | `spi_alloc_host` `:801` | `__spi_alloc_controller(dev, size, false)` |
| `spi_alloc_slave` `:792` | `spi_alloc_target` `:807` | `__spi_alloc_controller(dev, size, true)` |
| `devm_spi_alloc_master` `:820` | `devm_spi_alloc_host` `:835` | `__devm_spi_alloc_controller(dev, size, false)` |
| `devm_spi_alloc_slave` `:826` | `devm_spi_alloc_target` `:841` | `__devm_spi_alloc_controller(dev, size, true)` |

**行为完全等价，写哪个都对**；差别只在 `grep` 的时候——旧名字才是本树的多数派（见上表计数）。
`slave`/`target` 两版都额外带一层 `if (!IS_ENABLED(CONFIG_SPI_SLAVE)) return NULL;`
（`:795`/`:810`/`:829`/`:844`），**这一层在 `host`/`master` 版里没有**。

**(3) 还有一种最容易漏的形式：`struct spi_device` 里真的存了两份指针。**

```c
struct spi_device {
        struct device           dev;
        struct spi_controller   *controller;
        struct spi_controller   *master;        /* Compatibility layer */
```

`include/linux/spi/spi.h:176-179`。这不是宏、不是 inline，是**一个真实的重复字段**，
由核心在 `drivers/spi/spi.c:596` 一行里同时赋值：

```c
spi->master = spi->controller = ctlr;
```

所以 `spi->master` 和 `spi->controller` 永远指向同一个对象，读哪个都行
（本树 `drivers/` 下 **91 个文件 / 293 处**仍在读 `spi->master`）。
代价是每个 `spi_device` 多占一个指针的空间。
**写新代码只用 `spi->controller`。**

**(4) 第四种形式：`union`——同一块存储挂两个名字。**

这一种只出现在 `slave` → `target` 那条改名线上（`master` → `host` 没用它），
所以很容易被漏掉：

```c
/* include/linux/spi/spi.h:561-566 */
	union {
		/* Flag indicating this is an SPI slave controller */
		bool			slave;
		/* Flag indicating this is an SPI target controller */
		bool			target;
	};

/* include/linux/spi/spi.h:679-682 */
	union {
		int (*slave_abort)(struct spi_controller *ctlr);
		int (*target_abort)(struct spi_controller *ctlr);
	};
```

和形式 (3) 的区别很关键：形式 (3) 是**两个字段、两份存储**，靠核心
一行赋值保持同步；形式 (4) 是**一个字段、一份存储、两个名字**，
物理上不可能不同步。

但**上层不跟着 union 走**：`spi_controller_is_slave()`（`spi.h:755`）与
`spi_controller_is_target()`（`spi.h:760`）是两个函数体逐字相同、互不调用的
独立 inline；`spi_slave_abort()`（`drivers/spi/spi.c:2777`）与
`spi_target_abort()`（`:2788`）是两个各自 `EXPORT_SYMBOL_GPL` 的独立函数
（`:2786` / `:2797`）——**不是别名**。落到形式 (2)。

> **四种形式合起来的启示**：看到 `master` / `slave` 字样先分清它是
> `#define`（形式 1）、平行的 inline（形式 2）、真实的重复字段（形式 3）、
> 还是 `union` 同址（形式 4）——四者的 `grep` 结果、调试器里的表现、
> 以及"改了会不会影响另一个"都不一样。只有形式 1 和形式 4
> 能保证两个名字永远一致；形式 2 和形式 3 靠人维护，将来只删一份时会分叉。

| 形式 | 举例 | 两个名字会不会分叉 |
|-----|-----|----------------|
| (1) `#define` | `spi_master_get` → `spi_controller_get`（`spi.h:1604`） | 不会（一份代码） |
| (2) 平行 inline / 平行函数 | `spi_alloc_master`（`:786`）/ `spi_alloc_host`（`:801`）；`spi_slave_abort` / `spi_target_abort` | **会**（两份独立代码） |
| (3) 重复字段 | `spi_device.master` / `.controller`（`:176-179`） | 会（靠 `spi.c:596` 一行同步） |
| (4) `union` 同址 | `ctlr->slave` / `->target`（`:561-566`）；`->slave_abort` / `->target_abort`（`:679-682`） | 不会（一份存储） |

> 逐对符号的实用区分（"该调哪个"）见 `05-usage.md` §7.8。
> 为什么会有两套名字、什么时候会踩坑，见 `08-feynman.md` §1。

### §1.2 注册与注销

| API | 说明 | 备注 |
|-----|------|------|
| `spi_register_controller(ctlr)` | 把控制器挂上 SPI 总线，并立刻枚举它的 DT 子设备 | 返回 0 或负错误码。**调用之前必须填完 `num_chipselect`、`mode_bits`、`bits_per_word_mask` 和回调**——这个函数返回时子设备的 `probe()` 可能已经跑过了 |
| `devm_spi_register_controller(dev, ctlr)` | 带 devres 的注册 | 推荐。全 `drivers/` 下 25 处（旧名 `devm_spi_register_master` 另有 50 处） |
| `spi_unregister_controller(ctlr)` | 注销：先拆掉全部子设备，再销毁消息泵队列，最后释放 `bus_num` | **无返回值**。它会 `put` 掉一次引用，所以配 `spi_alloc_*` 用时不要再自己 `put` |
| `spi_register_master(ctlr)` / `spi_unregister_master(ctlr)` / `devm_spi_register_master(dev, ctlr)` | 上三者的旧名字 | 分别 22 / — / 50 处 |
| `spi_controller_get(ctlr)` | 引用计数 +1 | 返回 `ctlr` 或 `NULL` |
| `spi_controller_put(ctlr)` | 引用计数 -1 | 无返回值 |
| `spi_controller_get_devdata(ctlr)` | 取出分配时预留的驱动私有区 | 返回 `void *`，不会失败 |
| `spi_controller_set_devdata(ctlr, data)` | 让私有区指向别处 | 极少用；用 `spi_alloc_*(dev, size)` 的 size 参数更好 |

**注册前必须填的字段**（漏掉的后果都写在括号里）：

| 字段 | 漏了会怎样 |
|-----|-----------|
| `num_chipselect` | 所有 `reg` 非 0 的子节点被拒（`spi_add_device()` 会检查上界） |
| `mode_bits` | 芯片要的模式位（比如 `SPI_CPHA`）被 `spi_setup()` 拒掉，`probe` 失败 |
| `bits_per_word_mask` | **为 0 时视为「只支持 8 位」**，不是「支持全部」。这是最常见的新手陷阱 |
| `transfer_one` 或 `transfer_one_message` 或 `mem_ops` | `spi_controller_check_ops()` 直接让注册失败返回 `-EINVAL` |
| `min_speed_hz` / `max_speed_hz` | 校验会退化，芯片声明的高速率不会被下调 |

> `spi_controller_check_ops()` 到底检查了哪几种组合，见 `03-internals.md` §A2。

### §1.3 控制器驱动必须/可以实现的回调

回调全部是 `struct spi_controller` 的成员，定义在 `include/linux/spi/spi.h`。

**三选一（必须至少有一个）**：

| 回调 | 说明 | 备注 |
|-----|------|------|
| `transfer_one(ctlr, spi, xfer)` | 传一个 `spi_transfer` | **最推荐**。`drivers/spi/` 内 **55** 个实现。返回 0 = 已同步传完；返回 **正数** = 传输进行中，核心去等 `xfer_completion`；返回负数 = 失败 |
| `transfer_one_message(ctlr, msg)` | 自己接管整个 message（含片选、延时、统计） | 全树 **32** 个（`drivers/spi/` 内 29 个，另 3 个是住在 `drivers/spi/` 之外的控制器——见 `99` §2.5）。不填则核心用自带的 `spi_transfer_one_message()` |
| `mem_ops` | 一整套 flash 语义操作，见 §7 | `drivers/spi/` 内 **24** 个。填了它可以完全不填 `transfer_one` |
| `transfer(spi, msg)` | **旧式**：驱动自己排队 | 核心默认把它设成 `spi_queued_transfer`。**`drivers/spi/` 内只剩 2 个驱动还自己覆盖它**（`spi-topcliff-pch.c`、`spi-mpc52xx.c`，都不是 arm64），树外还有 1 个 `drivers/video/fbdev/mmp/hw/mmp_spi.c`。新驱动不要用 |

**常用可选回调**：

| 回调 | 说明 | 备注 |
|-----|------|------|
| `setup(spi)` | 某颗芯片的参数变更时被调（速率、位宽、模式） | **62** 个实现，是最常被实现的回调。⚠️ **头文件里明确警告：调用它的时候总线上可能正有别的设备在传输**，所以不能在这里碰共享寄存器 |
| `cleanup(spi)` | 芯片注销时释放 `setup()` 分配的东西 | **26** 个实现。与 `setup` 配对；`setup` 里 `kzalloc` 了私有区就必须实现它 |
| `set_cs(spi, enable)` | 拉/放 native 片选 | **30** 个实现。⚠️ **参数极性是反的**：核心传进来的 `enable` 已经过 `SPI_CS_HIGH` 处理，且调用形式是 `ctlr->set_cs(spi, !enable)`。照抄现有驱动，不要自己推 |
| `prepare_message(ctlr, msg)` / `unprepare_message(ctlr, msg)` | 每个 message 前后各一次 | **28 / 11** 个实现。适合放「按 message 一次性配好的寄存器」 |
| `prepare_transfer_hardware(ctlr)` / `unprepare_transfer_hardware(ctlr)` | 队列由空变忙 / 由忙变空时各一次 | **10 / 13** 个实现。适合放开/关时钟。⚠️ 不是每个 message 都调 |
| `can_dma(ctlr, spi, xfer)` | 这一段要不要走 DMA | **24** 个实现。返回 `true` 核心才会去做 DMA 映射 |
| `handle_err(ctlr, msg)` | message 出错时收拾硬件 | **11** 个实现。典型动作：关芯片、掩中断、`dmaengine_terminate_all()` |
| `max_transfer_size(spi)` / `max_message_size(spi)` | 声明单次上限 | **10 / 5** 个实现。核心据此**自动拆分**超长 transfer |
| `slave_abort(ctlr)` / `target_abort(ctlr)` | 中止一个 slave 模式传输 | `slave_abort` **9** 个实现；**`target_abort` 全树 0 个实现**（新名字刚加，还没人用） |
| `set_cs_timing(spi)` | 把 `cs_setup`/`cs_hold`/`cs_inactive` 编程进硬件 | ⚠️ **只有 3 个驱动实现**（`spi-mt65xx.c`、`atmel-quadspi.c`、`spi-tegra114.c`）。**头文件里它只有一个参数**，但 `Documentation/spi/spi-summary.rst` 里还写着 4 个参数的旧原型——以代码为准 |
| `fw_translate_cs(ctlr, cs)` | 把固件里的片选号翻译成硬件编号 | 全树**只有 1 个**：`drivers/spi/spi-pxa2xx.c`（非 arm64） |
| `mem_caps` | 声明 `spi-mem` 的额外能力 | 3 个 |

> **这两张表里"N 个实现"的口径**：在 `drivers/spi/` 里数**赋值点所在的文件数**，
> 排除核心 `drivers/spi/spi.c` 自己：
>
> ```bash
> grep -rlE "(->|\.)<字段名>[[:space:]]*=" --include=*.c drivers/spi/ | grep -v "spi\.c$" | wc -l
> ```
>
> 两个坑：**(1)** SPI 驱动几乎都在 probe 里逐字段赋值（`ctlr->handle_err = ...`），
> 不用 `.field =` 初始化器，所以只查 `\.field\s*=` 会得到 0；
> **(2)** 不能拿整棵树数——`max_transfer_size` / `handle_err` 这些名字在
> `usb/dwc2`、`scsi/smartpqi`、`misc/cxl` 里都有同名字段，全树 grep 会虚高一倍多。
> 完整的核对结果和踩坑记录见 `99` §2.5。

**只需设标志、不用写代码的可选能力**：

| 字段 | 说明 | 备注 |
|-----|------|------|
| `auto_runtime_pm` | 让核心替你在传输前后 get/put runtime PM | 42 处（`drivers/spi/` 内 41 处），最常用 |
| `use_gpio_descriptors` | 让核心用 `gpiod` 而不是旧的 GPIO 号处理 `cs-gpios` | 42 处。新驱动应该设 |
| `rt` | 让消息泵 kthread 跑 RT 优先级 | 也可以由 `spi_device.rt` 触发 |
| `must_async` | 禁用 `spi_sync()` 的同步快路径 | 全树**只有 1 个**：`drivers/spi/spi-mux.c`（它必须重入下层总线，不能在原调用栈上跑完） |
| `ptp_sts_supported` | 声明支持精确时间戳 | 全树**只有 1 个**：`drivers/spi/spi-fsl-dspi.c` |
| `flags` 里的 `SPI_CONTROLLER_*` | 半双工 / 只收 / 只发 / 必须有 rx / 必须有 tx / GPIO 片选也要调 `set_cs` | 见 `02-datastructures.md` §2.1 的 flags 子表 |

### §1.4 传输过程中控制器驱动要调的核心函数

| API | 说明 | 备注 |
|-----|------|------|
| `spi_finalize_current_transfer(ctlr)` | 告诉核心「这一段传完了」 | 实现只有一行 `complete(&ctlr->xfer_completion)`。**`transfer_one` 返回正数之后必须调它**，否则核心会一直等到超时 |
| `spi_finalize_current_message(ctlr)` | 告诉核心「整个 message 结束了」 | **只有自己实现了 `transfer_one_message` 的驱动才需要调**。用核心默认实现时核心自己会调 |
| `spi_get_next_queued_message(ctlr)` | 从队列取下一个 message | 旧式排队驱动才用 |
| `spi_take_timestamp_pre(ctlr, xfer, progress, irqs_off)` / `spi_take_timestamp_post(...)` | 打 PTP 时间戳 | 只有 `ptp_sts_supported` 的驱动才需要 |
| `spi_controller_suspend(ctlr)` / `spi_controller_resume(ctlr)` | 停/启消息泵 | 在自己的 PM 回调里调。返回 0 或负错误码 |
| `spi_controller_is_target(ctlr)` / `spi_controller_is_slave(ctlr)` | 问「我是从模式吗」 | `CONFIG_SPI_SLAVE=n` 时恒为假（编译期消掉） |
| `spi_delay_exec(delay, xfer)` | 执行一个 `spi_delay` | 短延时 `ndelay`/`udelay`，长延时 `usleep_range`。分界见 `03-internals.md` §A5 |
| `spi_delay_to_ns(delay, xfer)` | 把 `spi_delay` 换算成纳秒 | `SPI_DELAY_UNIT_SCK` 需要 `xfer` 才能算，`xfer` 为 `NULL` 时返回 `-EINVAL` |

---

## §2 协议驱动 API（注册）

**头文件**：`include/linux/spi/spi.h`；**实现**：`drivers/spi/spi.c`

| API | 说明 | 备注 |
|-----|------|------|
| `spi_register_driver(sdrv)` | 注册一个 SPI 协议驱动 | 宏，展开成 `__spi_register_driver(THIS_MODULE, sdrv)`。返回 0 或负错误码 |
| `module_spi_driver(sdrv)` | 帮你生成 `module_init`/`module_exit` | **绝大多数协议驱动只需要这一行** |
| `spi_unregister_driver(sdrv)` | 注销 | 无返回值，`sdrv` 为 `NULL` 时安全 |
| `__spi_register_driver(owner, sdrv)` | 底层实现 | ⚠️ 它会**自己填 `sdrv->driver.owner = owner`**，所以驱动里再写 `.owner = THIS_MODULE` 是多余的（`Documentation/spi/spi-summary.rst` 的示例里还留着，可以忽略） |

**`struct spi_driver` 要填什么**：

| 成员 | 说明 | 备注 |
|-----|------|------|
| `driver.name` | 驱动名，也是无 DT 时的 modalias 匹配依据 | 必填 |
| `driver.of_match_table` | DT `compatible` 匹配表 | arm64 上**这才是主要匹配途径** |
| `id_table` | `struct spi_device_id` 数组 | 即使有 `of_match_table` 也建议填——sysfs `driver_override` 和模块自动加载会用到 |
| `probe(spi)` | 探测 | 返回 0 成功。**核心在调它之前已经替你调过 `spi_setup()`**，所以进来时参数已生效 |
| `remove(spi)` | 移除 | ⚠️ **返回类型是 `void`**（6.1 已经完成了 `int → void` 的转换）。老代码 `return 0` 编不过 |
| `shutdown(spi)` | 关机时调 | 可选 |

### §2.1 匹配顺序

`spi_bus_type.match` = `spi_match_device()`，四级优先级（第一个命中就停）：

| 优先级 | 依据 | 备注 |
|-------|------|------|
| 1 | sysfs `driver_override` | 手工写进 `/sys/bus/spi/devices/*/driver_override`，用来强制绑定 |
| 2 | DT `of_match_device()` | arm64 主路径。比对的是 DT 的 `compatible` |
| 3 | ACPI | arm64 上本文档集不覆盖 |
| 4 | `spi_match_id()` 查 `id_table` | 比对 `spi_device.modalias`。**modalias 是 `compatible` 去掉厂商前缀之后的部分**（`"rohm,dh2228fv"` → `"dh2228fv"`） |

> ⚠️ 第 4 级那条「去掉厂商前缀」的规则是 SPI 特有的坑：DT 里写
> `compatible = "rohm,dh2228fv"`，`id_table` 里要写 `"dh2228fv"`。
> 完整推导见 `03-internals.md` §A4。

### §2.2 从 `spi_device` 取信息

| API | 说明 | 备注 |
|-----|------|------|
| `to_spi_device(dev)` | `struct device *` → `struct spi_device *` | `container_of` 包装 |
| `spi_get_drvdata(spi)` / `spi_set_drvdata(spi, data)` | 存/取驱动私有指针 | 就是 `dev_get_drvdata()` 的包装 |
| `spi_get_ctldata(spi)` / `spi_set_ctldata(spi, state)` | 存/取**控制器**给这颗芯片留的私有状态 | 给控制器驱动的 `setup()`/`cleanup()` 用，协议驱动不要碰 |
| `spi_get_device_id(spi)` | 取匹配上的 `spi_device_id` 条目 | 返回 `const struct spi_device_id *`，没匹配上返回 `NULL` |
| `spi_get_device_match_data(spi)` | 一步拿到匹配数据（DT 的 `data` 或 id 表的 `driver_data`） | **推荐**：它把 DT 和 id 表两条路统一了，省掉驱动里的 if/else |
| `spi_dev_get(spi)` / `spi_dev_put(spi)` | 引用计数 | `spi_dev_put(NULL)` 安全 |
| `spi_get_chipselect(spi, idx)` / `spi_set_chipselect(spi, idx, cs)` | 读/写片选号 | ⚠️ **`idx` 参数被完全忽略**——6.1 里它只是为将来的多片选做的占位。传什么都一样 |
| `spi_get_csgpiod(spi, idx)` / `spi_set_csgpiod(spi, idx, gpiod)` | 读/写片选 GPIO 描述符 | 同上，`idx` 被忽略 |

---

## §3 传输 API

**头文件**：`include/linux/spi/spi.h`；**实现**：`drivers/spi/spi.c`

### §3.1 构造 message

| API | 说明 | 备注 |
|-----|------|------|
| `spi_message_init(m)` | 清零 + 初始化 transfer 链表 | 栈上的 `spi_message` 必须先调它 |
| `spi_message_init_no_memset(m)` | 只初始化链表，不清零 | 用于「已经 `kzalloc` 过」的场合，省一次 memset |
| `spi_message_add_tail(t, m)` | 把一个 transfer 挂到 message 尾部 | 顺序就是线上的顺序 |
| `spi_transfer_del(t)` | 从链表摘掉一个 transfer | 少用 |
| `spi_message_init_with_transfers(m, xfers, num_xfers)` | 一次挂一个 transfer 数组 | 最省事的写法 |
| `spi_message_alloc(ntrans, flags)` | 一次 `kzalloc` 出 message + N 个 transfer 并串好 | 返回 `NULL` 表示失败。transfer 数组紧跟在 message 后面 |
| `spi_message_free(m)` | 释放上面分配的 | 只能配 `spi_message_alloc()` 用 |

### §3.2 发起传输

| API | 说明 | 备注 |
|-----|------|------|
| `spi_sync(spi, message)` | **同步**传输，返回时数据已经在线上走完 | 最常用。返回 0 或负错误码。**会睡眠**，不能在原子上下文调。内部有一条「队列空就在当前线程上直接跑完」的快路径 |
| `spi_sync_locked(spi, message)` | 同上，但调用者已经持有 `spi_bus_lock()` | 必须先 `spi_bus_lock()` |
| `spi_async(spi, message)` | **异步**：挂进队列就返回，完成时调 `message->complete()` | ⚠️ **可以在中断上下文调**，但 `complete()` 回调也会在不可睡眠的上下文里跑，回调里不能睡。总线被 `spi_bus_lock()` 占着时返回 `-EBUSY` |
| `spi_async_locked(spi, message)` | 同上，跳过 `bus_lock_flag` 检查 | 给持锁者用 |
| `spi_bus_lock(ctlr)` | 独占整条总线，期间别人的 `spi_async()` 一律 `-EBUSY` | 用于「必须连续几个 message 不被插队」的场景（如 flash 的 erase-program 序列）。返回 0 |
| `spi_bus_unlock(ctlr)` | 放开 | 返回 0 |
| `spi_slave_abort(spi)` | 中止一个进行中的 slave 传输 | 控制器没实现 `slave_abort` 时返回 `-ENOTSUPP` |
| `spi_target_abort(spi)` | 同上，新名字 | 全树 0 个控制器实现它，实际调用必然拿到 `-ENOTSUPP` |

**`spi_sync` / `spi_async` 的返回值语义**：

| 返回值 | 含义 |
|-------|------|
| `0` | ⚠️ **只表示「时钟摇完了、没有内核侧错误」**，不表示对面芯片听懂了。SPI 没有应答位，见 `00-overview.md` §2.2 |
| `-EINVAL` | 参数校验失败（`bits_per_word` 不支持、`len` 为 0、tx/rx 都为 NULL 而控制器又要求必须有…） |
| `-ESHUTDOWN` | 控制器已 suspend。同时会打一条 `dev_warn_once` 「Attempted to sync while suspend」 |
| `-EBUSY` | 只有 `spi_async()` 会返回：总线被 `spi_bus_lock()` 占了 |
| `-ETIMEDOUT` | 传输超时（`spi_transfer_wait()` 按长度和速率算的软超时） |
| `-ENOTSUPP` | 控制器连 `transfer` 都没有（正常注册流程不可能出现），或者 abort 未实现 |
| `-ENOMEM` | DMA 映射 / transfer 拆分时分配失败 |
| 其它负值 | 控制器驱动的 `transfer_one` 原样透出的错误 |

### §3.3 便捷封装

| API | 说明 | 备注 |
|-----|------|------|
| `spi_write(spi, buf, len)` | 只写 | `inline`，内部拼一个单 transfer 的 message 后 `spi_sync()` |
| `spi_read(spi, buf, len)` | 只读 | 同上 |
| `spi_write_then_read(spi, txbuf, n_tx, rxbuf, n_rx)` | 先写 `n_tx` 字节再读 `n_rx` 字节，**期间片选不抬** | 最常用的封装。⚠️ **有长度上限**：走一块 `SPI_BUFSIZ`（默认 4096）的全局静态缓冲，超了返回 `-EINVAL`。而且它内部拿一把全局互斥锁，热路径别用 |
| `spi_w8r8(spi, cmd)` | 写 1 字节命令读 1 字节 | 返回 `ssize_t`：≥0 是读到的值，<0 是错误码 |
| `spi_w8r16(spi, cmd)` | 写 1 读 2，**按主机字节序** | 返回值同上。⚠️ 从线上来的是大端，这个函数**不做转换** |
| `spi_w8r16be(spi, cmd)` | 写 1 读 2，做 be16→CPU 转换 | **这个才是绝大多数芯片想要的** |
| `spi_sync_transfer(spi, xfers, num_xfers)` | 一次同步传一个 transfer 数组 | `inline`，避免手工 `spi_message_init` + 循环 add_tail |
| `spi_is_bpw_supported(spi, bpw)` | 问「这个位宽控制器支持吗」 | 返回 `bool` |
| `spi_max_transfer_size(spi)` / `spi_max_message_size(spi)` | 查单次上限 | 控制器没声明时返回 `SIZE_MAX` |
| `spi_split_transfers_maxsize(ctlr, msg, maxsize, gfp)` | 把超长 transfer 拆成多段 | **核心自己会在泵出 message 前调它**，驱动一般不用手动调 |

### §3.4 参数设置

| API | 说明 | 备注 |
|-----|------|------|
| `spi_setup(spi)` | 把 `spi->mode` / `bits_per_word` / `max_speed_hz` 校验后写进硬件 | 返回 0 或负错误码。**核心在调 `probe()` 之前已经替你调过一次**，只有运行时要改参数才需要自己调 |
| `spi_set_cs_timing(spi)` | 把 `cs_setup`/`cs_hold`/`cs_inactive` 编程进硬件 | 由 `spi_setup()` 内部调用。控制器没实现 `set_cs_timing` 时它什么都不做（不报错） |

**`spi_setup()` 会拒掉哪些请求**：

| 情况 | 返回 |
|-----|------|
| 模式位不在 `ctlr->mode_bits` 里 | `-EINVAL` |
| 同时要 `SPI_TX_DUAL` 和 `SPI_TX_QUAD` 这类互斥组合 | `-EINVAL` |
| `bits_per_word` 不在 `bits_per_word_mask` 里 | `-EINVAL` |
| `max_speed_hz` 低于 `ctlr->min_speed_hz` | `-EINVAL` |
| `ctlr->setup()` 自己返回错误 | 原样透出 |

`max_speed_hz` **高于**控制器上限不会报错，会被静默下调到 `ctlr->max_speed_hz`——
这是「速率上不去」的一个常见原因，见 `07-debug.md` §7.3。

---

## §4 设备实例化 API

**头文件**：`include/linux/spi/spi.h`；**实现**：`drivers/spi/spi.c`

arm64 + DT 的正常路径下，这一组**协议驱动和控制器驱动都不需要碰**——
核心在 `spi_register_controller()` 里自己扫 DT 子节点建好了。只在三种场合用：
非 DT 平台、运行时动态加设备、以及一颗芯片需要占多个片选。

| API | 说明 | 备注 |
|-----|------|------|
| `spi_new_device(ctlr, chip)` | 按一个 `spi_board_info` 建一个 `spi_device` 并注册 | 返回 `spi_device *` 或 `NULL` |
| `spi_alloc_device(ctlr)` | 只分配不注册，让调用者继续填字段 | 返回 `NULL` 表示失败。**分配后不注册的话必须 `spi_dev_put()`** |
| `spi_add_device(spi)` | 注册一个已分配好的 `spi_device` | 返回 0 或负错误码。会检查片选号上界和「这个片选是不是已经被占了」 |
| `spi_unregister_device(spi)` | 注销并释放 | 无返回值，`NULL` 安全。DT 场景下它还会清掉 `OF_POPULATED` 标记 |
| `spi_register_board_info(info, n)` | 登记一批静态 `spi_board_info`，等对应总线号的控制器出现时自动实例化 | `__init` 函数，只能在启动早期调。arm64 上基本用不到 |
| `spi_new_ancillary_device(spi, chipselect)` | 给一颗已有芯片再申请一个片选 | 返回 `spi_device *` 或 `ERR_PTR()`。⚠️ **这个返回 `ERR_PTR` 而不是 `NULL`**，和上面几个不一样。用于「一颗 flash 占两个 CS」这种硬件 |
| `of_find_spi_device_by_node(node)` | 从 DT 节点反查 `spi_device` | 主要给 OF_DYNAMIC 通知器用 |

---

## §5 sysfs 与统计

**实现**：`drivers/spi/spi.c`（属性组由 `SPI_STATISTICS_ATTRS` 宏批量生成）

这一节不是给驱动调的函数，而是 sysfs 里能读到什么。完整路径清单见 `07-debug.md` §3。

| 位置 | 内容 |
|-----|------|
| `/sys/bus/spi/devices/spiB.C/modalias` | 匹配用的 modalias 字符串（只读） |
| `/sys/bus/spi/devices/spiB.C/driver_override` | 强制绑定某个驱动（可写） |
| `/sys/bus/spi/devices/spiB.C/statistics/*` | **这颗芯片**的 per-CPU 统计汇总 |
| `/sys/class/spi_master/spiB/statistics/*` | **整条总线**的统计汇总 |
| `/sys/class/spi_master/spiB/slave` | 只在 `CONFIG_SPI_SLAVE=y` 且是 slave 控制器时存在。写驱动名可以绑定，写空可以解绑 |

统计项（两处 sysfs 目录的文件名完全一样）：

| 文件 | 含义 |
|-----|------|
| `messages` / `transfers` | message 数 / transfer 数 |
| `errors` / `timedout` | 出错数 / 超时数 |
| `spi_sync` / `spi_sync_immediate` | 同步调用数 / 其中走了快路径的 |
| `spi_async` | 异步调用数 |
| `bytes` / `bytes_rx` / `bytes_tx` | 总字节 / 收 / 发 |
| `transfer_bytes_histo_*`（17 个文件） | 传输长度分布，从 `0-1` 到 `65536+` |
| `transfers_split_maxsize` | 因为超过 `max_transfer_size` 而被拆分的次数 |

> `spi_sync_immediate / spi_sync` 这个比值是判断「有没有吃到同步快路径」的
> 最直接指标，用法见 `07-debug.md` §3.2。

---

## §6 tracepoint

**定义**：`include/trace/events/spi.h`

9 个事件，都由核心触发，控制器驱动不需要自己埋。

| tracepoint | 参数 | 什么时候触发 |
|-----------|------|------------|
| `spi:spi_controller_idle` | ctlr | 队列变空 |
| `spi:spi_controller_busy` | ctlr | 队列由空变忙 |
| `spi:spi_setup` | spi, status | 每次 `spi_setup()` 结束 |
| `spi:spi_set_cs` | spi, enable | 每次片选状态**真的**改变（状态没变时函数提前返回，不打点） |
| `spi:spi_message_submit` | msg | `spi_async()`/`spi_sync()` 提交 message |
| `spi:spi_message_start` | msg | 消息泵开始处理这个 message |
| `spi:spi_message_done` | msg | message 完成（带 `actual_length`） |
| `spi:spi_transfer_start` | msg, xfer | 每个 transfer 开始（带 `len`、`speed_hz`、缓冲内容） |
| `spi:spi_transfer_stop` | msg, xfer | 每个 transfer 结束 |

⚠️ **9 个事件里没有一个带 `TP_CONDITION`**。这意味着一旦 enable，
每个 transfer 都会打点，高频 SPI（比如触摸屏 100 Hz 轮询）会淹掉 trace buffer。
只能靠 filter 表达式过滤，具体写法和陷阱见 `07-debug.md` §2。

另外 `spi_transfer_start` / `spi_transfer_stop` 通过
`EXPORT_TRACEPOINT_SYMBOL()` 导出，允许模块直接挂 probe。

---

## §7 `spi-mem` API（flash 语义层）

**头文件**：`include/linux/spi/spi-mem.h`；**实现**：`drivers/spi/spi-mem.c`

这一层的存在理由：QSPI 控制器的硬件接口不是「摇一串字节」，而是「发一个命令 +
一个地址 + N 个 dummy 周期 + 一段数据」。用 `spi_transfer` 表达这件事既别扭又
无法利用硬件的自动化。所以 `spi-mem` 引入了 `struct spi_mem_op`。

### §7.1 给协议驱动（如 `spi-nor`）用

| API | 说明 | 备注 |
|-----|------|------|
| `spi_mem_exec_op(mem, op)` | 执行一个 `spi_mem_op` | 返回 0 或负错误码。控制器有 `mem_ops->exec_op` 就走硬件加速，否则**自动退化成一串 `spi_transfer` + `spi_sync()`** |
| `spi_mem_supports_op(mem, op)` | 问「这个操作这个控制器能做吗」 | 返回 `bool`。**发之前应该先问**，不然拿到 `-ENOTSUPP` |
| `spi_mem_adjust_op_size(mem, op)` | 把 `op->data.nbytes` 下调到控制器能一次做完的大小 | 返回 0 或负错误码。调用者要循环处理剩余部分 |
| `spi_mem_dirmap_create(mem, info)` | 建一个「直接映射」描述符，之后可以像内存一样读写 | 返回 `spi_mem_dirmap_desc *` 或 `ERR_PTR()`。控制器不支持时**仍然成功**，只是退化成走 `exec_op` |
| `devm_spi_mem_dirmap_create(dev, mem, info)` | devres 版 | 推荐 |
| `spi_mem_dirmap_destroy(desc)` / `devm_spi_mem_dirmap_destroy(dev, desc)` | 销毁 | — |
| `spi_mem_dirmap_read(desc, offs, len, buf)` / `_write(...)` | 通过直接映射读/写 | 返回实际字节数（`ssize_t`）或负错误码。⚠️ **可能少于请求的 `len`**，调用者要循环 |
| `spi_mem_poll_status(mem, op, mask, match, initial_delay_us, polling_delay_us, timeout_ms)` | 轮询状态寄存器直到 `(status & mask) == match` | 返回 0 或 `-ETIMEDOUT`。控制器有 `mem_ops->poll_status` 就用硬件轮询（省 CPU），否则软件轮询 |
| `spi_mem_get_name(mem)` | 取控制器给这个 mem 设备起的名字 | 返回 `const char *`；控制器没实现 `get_name` 时返回 `dev_name()` |
| `spi_mem_driver_register(drv)` | 注册一个 `spi_mem_driver` | 宏，展开成 `_with_owner(drv, THIS_MODULE)` |
| `spi_mem_driver_unregister(drv)` | 注销 | — |
| `module_spi_mem_driver(drv)` | 生成 init/exit | — |
| `spi_mem_get_drvdata(mem)` / `spi_mem_set_drvdata(mem, data)` | 私有指针 | — |

### §7.2 构造 `spi_mem_op`

不要手填字段，用宏（全部在 `include/linux/spi/spi-mem.h`）：

| 宏 | 说明 |
|---|------|
| `SPI_MEM_OP(cmd, addr, dummy, data)` | 顶层组装宏 |
| `SPI_MEM_OP_CMD(opcode, buswidth)` | 命令阶段 |
| `SPI_MEM_OP_ADDR(nbytes, val, buswidth)` / `SPI_MEM_OP_NO_ADDR` | 地址阶段 |
| `SPI_MEM_OP_DUMMY(nbytes, buswidth)` / `SPI_MEM_OP_NO_DUMMY` | dummy 周期 |
| `SPI_MEM_OP_DATA_IN(nbytes, buf, buswidth)` / `_DATA_OUT(...)` / `_NO_DATA` | 数据阶段 |

例子（读 JEDEC ID）：

```c
struct spi_mem_op op = SPI_MEM_OP(SPI_MEM_OP_CMD(0x9f, 1),
                                  SPI_MEM_OP_NO_ADDR,
                                  SPI_MEM_OP_NO_DUMMY,
                                  SPI_MEM_OP_DATA_IN(3, id, 1));
ret = spi_mem_exec_op(mem, &op);
```

### §7.3 给 QSPI 控制器驱动实现的回调

全部是 `struct spi_controller_mem_ops` 的成员（`include/linux/spi/spi-mem.h`）：

| 回调 | 说明 | 备注 |
|-----|------|------|
| `exec_op(mem, op)` | 执行一个操作 | **必填**（这一整套的核心） |
| `supports_op(mem, op)` | 能力查询 | 不填则用 `spi_mem_default_supports_op()` |
| `adjust_op_size(mem, op)` | 按硬件 FIFO/窗口下调数据长度 | 不填则核心按 `max_transfer_size` 算 |
| `get_name(mem)` | 自定义名字 | 可选 |
| `dirmap_create(desc)` / `dirmap_destroy(desc)` | 建立/销毁直接映射窗口 | 可选。不实现则 `spi_mem_dirmap_*` 退化成 `exec_op` |
| `dirmap_read(desc, offs, len, buf)` / `dirmap_write(...)` | 直接映射读写 | 与 `dirmap_create` 配对 |
| `poll_status(mem, op, mask, match, ...)` | 硬件辅助轮询 | 可选，省 CPU |

`spi_mem_default_supports_op(mem, op)` 可以在自己的 `supports_op` 里先调一次做通用检查，
再叠加自己的限制——这是标准写法。

---

## §8 用户态 API（spidev ioctl）

**头文件**：`include/uapi/linux/spi/spidev.h`；**实现**：`drivers/spi/spidev.c`

设备节点 `/dev/spidevB.C`（B = 总线号，C = 片选号），主设备号 153，最多 32 个次设备号。

| ioctl | 方向 | 参数类型 | 说明 |
|------|------|---------|------|
| `SPI_IOC_RD_MODE` / `SPI_IOC_WR_MODE` | 读 / 写 | `__u8` | 模式低 8 位（CPOL/CPHA/CS_HIGH/LSB_FIRST/3WIRE/LOOP/NO_CS/READY） |
| `SPI_IOC_RD_MODE32` / `SPI_IOC_WR_MODE32` | 读 / 写 | `__u32` | 完整 32 位模式，多线（DUAL/QUAD/OCTAL）必须用这个 |
| `SPI_IOC_RD_LSB_FIRST` / `SPI_IOC_WR_LSB_FIRST` | 读 / 写 | `__u8` | 字内位序，等价于模式里的 `SPI_LSB_FIRST` |
| `SPI_IOC_RD_BITS_PER_WORD` / `SPI_IOC_WR_BITS_PER_WORD` | 读 / 写 | `__u8` | 位宽，0 表示默认 8 |
| `SPI_IOC_RD_MAX_SPEED_HZ` / `SPI_IOC_WR_MAX_SPEED_HZ` | 读 / 写 | `__u32` | 最高速率 |
| `SPI_IOC_MESSAGE(N)` | 写 | `struct spi_ioc_transfer[N]` | **核心接口**：一次提交 N 个 transfer，语义等价于内核态的一个 `spi_message` |

**`struct spi_ioc_transfer` 的字段**（和内核的 `spi_transfer` 一一对应）：

| 字段 | 说明 |
|-----|------|
| `tx_buf` / `rx_buf` | ⚠️ 类型是 `__u64`，用户态要 `(__u64)(uintptr_t)ptr` 强转。为 0 表示该方向不用 |
| `len` | 字节数，收发共用 |
| `speed_hz` / `bits_per_word` | 0 表示沿用设备默认 |
| `delay_usecs` | 本段之后的延时 |
| `cs_change` | 本段之后翻一下片选 |
| `tx_nbits` / `rx_nbits` | 多线位宽 |
| `word_delay_usecs` | 字间延时 |
| `pad` | 必须为 0 |

**三个必须知道的限制**：

| 限制 | 细节 |
|-----|------|
| **单次 ioctl 的数据量上限** | `bufsiz` 模块参数，**默认硬编码 4096 字节**。`Documentation/spi/spidev.rst` 说它「默认一页」，这在 16K/64K 页的 arm64 上是**错的**——它就是 4096。加载时可改：`modprobe spidev bufsiz=65536` |
| **能改的模式位有限** | 只有 `SPI_MODE_MASK` 覆盖的那些位能通过 ioctl 改。控制器 `mode_bits` 不支持的位会让 `SPI_IOC_WR_MODE*` 返回 `-EINVAL` |
| **DT 里怎么写才会出现节点** | ⚠️ **不能直接写 `compatible = "spidev"`**——`spidev_of_check()` 会打印警告并拒绝。必须写具体芯片的 compatible，且它得在 `spidev_dt_ids[]` 里。完整名单和绕法见 `04-extensions.md` §4.3 |

**典型返回值**：

| 返回 | 含义 |
|-----|------|
| `SPI_IOC_MESSAGE` 返回 > 0 | 实际传输的总字节数 |
| `-EMSGSIZE` | 数据量超过 `bufsiz`，或 transfer 个数超限 |
| `-EFAULT` | 用户指针不可访问 |
| `-EINVAL` | 参数非法（`pad` 非 0、模式位不支持…） |
| `-ENOMEM` | 内核缓冲分配失败 |
| `-ESHUTDOWN` | 底层控制器已 suspend |

---

## §9 `spi-bitbang` 复用层

**头文件**：`include/linux/spi/spi_bitbang.h`；**实现**：`drivers/spi/spi-bitbang.c`
+ `drivers/spi/spi-bitbang-txrx.h`

给「没有 SPI 硬件、只有 GPIO」的场合用。它自己就是一个控制器驱动的半成品：
你只需要提供「怎么摇一个字」的函数，它负责片选、循环、延时。

| API | 说明 | 备注 |
|-----|------|------|
| `spi_bitbang_init(bitbang)` | 初始化，把 `spi_bitbang` 的回调装进 `spi_controller` | 返回 0 或负错误码 |
| `spi_bitbang_start(bitbang)` | init + `spi_register_controller()` | 一步到位 |
| `spi_bitbang_stop(bitbang)` | 注销 | 无返回值 |
| `spi_bitbang_setup(spi)` | 通用 `setup` 实现 | 可以直接赋给 `ctlr->setup` |
| `spi_bitbang_cleanup(spi)` | 配对的 cleanup | — |
| `spi_bitbang_setup_transfer(spi, t)` | 通用速率/位宽计算 | 算出每半个时钟要 delay 多少纳秒 |

你要提供的：

| 成员 | 说明 |
|-----|------|
| `chipselect(spi, is_on)` | 拉片选 |
| `txrx_word[4]` | 四个函数指针，对应 CPOL/CPHA 的四种组合。⚠️ **必须四个都填**，哪个模式没填、用了就是 NULL 解引用 |
| `txrx_bufs(spi, t)` | 可选，自己接管整段传输而不是逐字 |
| `set_line_direction(spi, output)` | 只有 `SPI_3WIRE` 才需要 |

`drivers/spi/spi-bitbang-txrx.h` 提供了现成的四个实现，
`drivers/spi/spi-gpio.c` 是最完整的使用范例（474 行，值得整篇读）。

---

## §10 上下文速查表

| API | 可否睡眠 | 能在中断上下文调吗 | 需要先持什么锁 |
|-----|---------|-----------------|-------------|
| `spi_sync()` / `spi_sync_transfer()` | **会睡** | ✗ | 无（它自己拿 `bus_lock_mutex`） |
| `spi_sync_locked()` | 会睡 | ✗ | 必须先 `spi_bus_lock()` |
| `spi_write()` / `spi_read()` / `spi_write_then_read()` / `spi_w8r*()` | 会睡 | ✗ | 无 |
| `spi_async()` | **不睡** | **✓** | 无 |
| `spi_async_locked()` | 不睡 | ✓ | 必须先 `spi_bus_lock()` |
| `message->complete()` 回调 | ⚠️ **不允许睡** | 它自己就可能在原子上下文跑 | — |
| `spi_bus_lock()` / `spi_bus_unlock()` | 会睡 | ✗ | 无 |
| `spi_setup()` | 会睡 | ✗ | 无 |
| `spi_finalize_current_transfer()` | 不睡 | **✓**（就是给 ISR 用的） | 无 |
| `spi_finalize_current_message()` | 不睡 | ✓ | 无 |
| `spi_register_controller()` / `spi_unregister_controller()` | 会睡 | ✗ | 无 |
| `spi_register_driver()` / `spi_unregister_driver()` | 会睡 | ✗ | 无 |
| `spi_new_device()` / `spi_add_device()` / `spi_unregister_device()` | 会睡 | ✗ | 无 |
| `spi_delay_exec()` | **看延时长度**：短的忙等不睡，长的 `usleep_range` 会睡 | 短延时可以 | 无 |
| `spi_mem_exec_op()` | 会睡（内部走 `spi_sync()`） | ✗ | 无 |
| `spi_mem_dirmap_read()` / `_write()` | 会睡 | ✗ | 无 |
| `spi_controller_suspend()` / `_resume()` | 会睡 | ✗ | 无 |
| `spi_get_drvdata()` / `spi_get_ctldata()` / `to_spi_device()` 等 accessor | 不睡 | ✓ | 无 |

**两条最容易踩的**：

1. **`spi_sync()` 不能在中断上下文调**。触摸屏之类需要在中断里读寄存器的场合，
   标准做法是 `request_threaded_irq()` 在线程化 handler 里调，
   或者用 `spi_async()` + 完成回调。
2. **`spi_async()` 的完成回调不能睡**。它可能在提交者的线程上跑，
   也可能在消息泵 kthread 上跑，还可能在控制器的 ISR 里跑——最坏情况决定了约束。

---

## 相关章节

| 想知道 | 去哪 |
|-------|------|
| 这些结构体的每个字段是什么意思 | `02-datastructures.md` |
| 这些函数内部怎么实现的、锁怎么加的 | `03-internals.md` |
| `spi-mem` / slave / mux / spidev 的完整用法 | `04-extensions.md` |
| 拿这些 API 写一个驱动的完整骨架 | `05-usage.md` |
| DT 里怎么写才能让这些 API 生效 | `06-devicetree.md` |
| 返回了错误码怎么定位 | `07-debug.md` §6 |
