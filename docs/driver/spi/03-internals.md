---
title: 内部实现
---

# SPI 核心实现剖析（Linux 6.1.158, arm64）

> 本篇是全套里最长的一篇。**只承诺一条主轴：Part A 是时间线。**
> 横切机制（锁、DMA、片选状态机、延时、PM、统计）全部收进 Part B，
> 对设计本身的评价收进 Part C。章节号自带 A/B/C 前缀，
> 所以别处引用 `03 §A9` 时你立刻知道它在哪条轴上。

---

## §0 导航

### §0.1 分层图

```
 ┌─────────────────────────────────────────────────────────────┐
 │ 用户态                                                       │
 │   /dev/spidevB.C  ioctl(SPI_IOC_MESSAGE)                     │
 └──────────────────────────┬──────────────────────────────────┘
                            │  drivers/spi/spidev.c
 ┌──────────────────────────┴──────────────────────────────────┐
 │ 协议驱动层                                                   │
 │   spi-nor / mcp251x / spidev / ...                           │
 │   只会三件事：spi_setup() / 构造 spi_message / spi_sync()      │
 └──────────────────────────┬──────────────────────────────────┘
                            │  spi_async / spi_sync / spi_mem_exec_op
 ┌──────────────────────────┴──────────────────────────────────┐
 │ SPI 核心   drivers/spi/spi.c（4,632 行）                   │
 │                                                              │
 │   枚举与匹配   of_register_spi_devices / spi_match_device     │
 │   参数协商     spi_setup / __spi_validate                     │
 │   提交与排队   __spi_async / __spi_sync / spi_queued_transfer  │
 │   消息泵       __spi_pump_messages（kthread worker）           │
 │   逐段执行     spi_transfer_one_message                       │
 │   横切服务     片选状态机 / DMA 映射 / 拆分 / 延时 / PM / 统计  │
 └──────────────────────────┬──────────────────────────────────┘
              ┌─────────────┼─────────────┐
              │             │             │
   ┌──────────┴───┐  ┌──────┴──────┐  ┌───┴──────────────┐
   │ transfer_one │  │transfer_one_│  │ mem_ops          │
   │ （59 个驱动）│  │message      │  │ （27 个驱动）     │
   │ 核心管循环   │  │（29 个驱动）│  │ 完全绕开传输路径  │
   └──────────┬───┘  └──────┬──────┘  └───┬──────────────┘
              └─────────────┼─────────────┘
 ┌──────────────────────────┴──────────────────────────────────┐
 │ 控制器驱动层  spi-rockchip / spi-pl022 / spi-dw / ...        │
 │   写寄存器、起 DMA、处理中断                                  │
 └──────────────────────────┬──────────────────────────────────┘
                            │
                      SCLK / MOSI / MISO / CS 引脚
```

**读这张图的关键**：中间那三条出口是**互斥的三选一**，控制器驱动挑一条实现
（`spi_controller_check_ops()` `drivers/spi/spi.c:3056` 强制至少有一条）。
选哪条决定了核心替你做多少事：

| 你实现了 | 核心替你做 | 你自己要做 |
|---------|----------|----------|
| `transfer_one` | 片选、循环、延时、统计、DMA 映射、超时等待 | 只管把一段数据摇出去 |
| `transfer_one_message` | DMA 映射、拆分、PM | 片选、循环、延时、统计全自己来 |
| `mem_ops->exec_op` | 几乎什么都不做 | 整个 flash 命令帧自己组 |
| `transfer`（legacy） | 什么都不做 | **已废弃**，注册时会打 `controller is unqueued, this is deprecated`（`drivers/spi/spi.c:3202`） |

### §0.2 生命周期时间线图

```
 内核启动
   │
   ├─ postcore_initcall(spi_init)                    ← §A1
   │     注册 spi_bus_type / spi_master_class / spi_slave_class
   │
   ├─ platform 总线 probe 控制器驱动
   │     └─ spi_alloc_master() + spi_register_controller()   ← §A2
   │           ├─ 分配 bus_num（idr / of_alias）
   │           ├─ device_add(&ctlr->dev)      → /sys/class/spi_master/spiB
   │           ├─ spi_controller_initialize_queue()  → 起 kthread 消息泵
   │           ├─ 分配 per-CPU 统计
   │           ├─ 回扫 board_list（静态板级信息）    ← §A3
   │           └─ of_register_spi_devices()          ← §A4
   │                 for_each_available_child_of_node
   │                   └─ of_spi_parse_dt() → spi_add_device()
   │                        → device_add(&spi->dev) → /sys/bus/spi/devices/spiB.C
   │
   ├─ spi_bus_type 匹配 → spi_probe()               ← §A5
   │     └─ 核心先调 spi_setup()  ← §A6  再调驱动 probe()
   │
   ├─ ══════════ 稳定期：反复传输 ══════════
   │     spi_sync() / spi_async()                    ← §A7
   │       ├─ 快路径：当前线程直接跑
   │       └─ 慢路径：入队 → 消息泵                   ← §A8
   │             └─ __spi_pump_transfer_message()     ← §A9
   │                   └─ spi_transfer_one_message()  ← §A10
   │                         └─ spi_finalize_current_message()  ← §A11
   │                   出错 → handle_err / DMA 回退 PIO  ← §A12
   │
   ├─ suspend / resume                               ← §A15
   │
   ├─ 协议驱动 remove → spi_remove()                 ← §A13
   │
   └─ 控制器驱动 remove → spi_unregister_controller() ← §A14
         ├─ 关 add_lock（挡住新设备）
         ├─ device_for_each_child(__unregister)  → 先拆所有子设备
         ├─ spi_destroy_queue()  → 停泵、销毁 kthread
         ├─ device_del(&ctlr->dev)
         └─ idr_remove(bus_num)
```

### §0.3 哪些内容不在本篇

| 想知道 | 去哪 | 为什么不在这里 |
|-------|------|--------------|
| SPI 协议本身（CPOL/CPHA、波形、片选电平） | `00-overview.md` §2 | 那是硬件背景，不是内核实现 |
| 某个 API 的签名、返回值、上下文约束 | `01-api.md` | 查询动作不同：那边是"我该调哪个"，这边是"它内部怎么走" |
| 某个字段是什么意思、谁写它 | `02-datastructures.md` | 字段表在那边，本篇只讲字段**在流程哪一步被写** |
| `spi-mem` / `spi-bitbang` / `spidev` / `spi-mux` 的内部 | `04-extensions.md` | 它们是挂在本篇时间线上的独立框架，各自成节 |
| DT 属性怎么写、写错了什么症状 | `06-devicetree.md` | 本篇 A4 节只讲"解析这一步做了什么"，不讲"你该怎么写" |
| 怎么开 trace、看哪个 sysfs | `07-debug.md` | 本篇讲机制，那边讲操作 |
| 「为什么这样设计」的大白话版 | `08-feynman.md` | 本篇的 Part C 是技术评价，不是入门解释 |
| 行号是怎么取证的、哪些没验证 | `99-evidence.md` | — |

---

# Part A：时间线

## §A0 主流程总表

**一条规则**：这张表的次序是用「谁调用谁」验出来的，不是凭直觉排的。
拆除路径（§A13/§A14）和注册路径同等重要——只写注册的文档会让你在
`rmmod` panic 时无处可查。

| # | 阶段 | 入口函数 | 位置 | 上下文 | 关键副作用 |
|---|-----|---------|------|-------|----------|
| A1 | 子系统初始化 | `spi_init()` | `drivers/spi/spi.c:4582` | `postcore_initcall` | 注册 1 个 bus + 2 个 class |
| A2 | 控制器注册 | `spi_register_controller()` | `:3099` | 可睡（控制器驱动 probe 内） | 建 kthread 泵、扫 DT 子节点 |
| A3 | 板级延迟实例化 | `spi_match_controller_to_boardinfo()` | `:885` 调用点 | 同上 | 非 DT 平台用，arm64 上基本空转 |
| A4 | DT 子设备枚举 | `of_register_spi_devices()` | `:2386` | 同上 | 每个可用子节点变成一个 `spi_device` |
| A5 | 匹配与 probe | `spi_match_device()` / `spi_probe()` | `:375` / `:410` | 驱动模型工作队列 | 绑定协议驱动 |
| A6 | 参数协商 | `spi_setup()` | `:3702` | 可睡 | 校验 + 裁剪 + 调 `ctlr->setup` |
| A7 | 提交 | `spi_sync()` / `spi_async()` | `:4258` / `:4060` | 可睡 / **可在原子上下文** | 分流快慢路径 |
| A8 | 消息泵 | `__spi_pump_messages()` | `:1764` | kthread（可能 RT 优先级） | 取队首、持 `io_mutex` |
| A9 | 准备硬件与内存 | `__spi_pump_transfer_message()` | `:1641` | 同上 | PM、拆分、`prepare_message`、DMA 映射 |
| A10 | 逐段执行 | `spi_transfer_one_message()` | `:1507` | 同上 | 片选、`transfer_one`、延时、统计 |
| A11 | 完成回收 | `spi_finalize_current_message()` | `:2026` | **可能在中断上下文被间接触发** | `unprepare`、释放 `spi_res`、调 `complete` |
| A12 | 错误与恢复 | `ctlr->handle_err` / DMA 回退 | `:1568` 一带 | 同 A10 | 只有 11 个驱动实现 `handle_err` |
| A13 | 协议驱动卸载 | `spi_remove()` / `spi_unregister_device()` | `:437` / `:2836` 一带 | 可睡 | — |
| A14 | 控制器注销 | `spi_unregister_controller()` | `:3297` | 可睡 | **先拆子设备，再停泵** |
| A15 | 系统 PM | `spi_controller_suspend()` / `_resume()` | `:3358` / `:3374` | 可睡 | 置/清 `SPI_CONTROLLER_SUSPENDED` |

---

## §A1 子系统初始化：`spi_init()`（`spi.c:4582-4622`）

`drivers/spi/spi.c:4582`，`postcore_initcall(spi_init)` 在 `:4632`。

```c
static int __init spi_init(void)
{
        int     status;

        buf = kmalloc(SPI_BUFSIZ, GFP_KERNEL);
        if (!buf) { status = -ENOMEM; goto err0; }

        status = bus_register(&spi_bus_type);          /* 约 :4596 */
        if (status < 0) goto err1;

        status = class_register(&spi_master_class);     /* 约 :4600 */
        if (status < 0) goto err2;

        if (IS_ENABLED(CONFIG_SPI_SLAVE)) {
                status = class_register(&spi_slave_class);
                if (status < 0) goto err3;
        }

        if (IS_ENABLED(CONFIG_OF_DYNAMIC))
                WARN_ON(of_reconfig_notifier_register(&spi_of_notifier));
        if (IS_ENABLED(CONFIG_ACPI))
                WARN_ON(acpi_reconfig_notifier_register(&spi_acpi_notifier));

        return 0;
        ...
}
```

四个要点：

1. **`postcore_initcall` 是很早的档位**（比 `subsys_initcall` 更早）。为什么这么早？
   因为静态 `spi_board_info` 通常在 `arch_initcall` 里注册，而 `spi_register_board_info()`
   需要 `board_lock` 和 `board_list` 已经就绪。旁边那条 REVISIT 注释
   （`drivers/spi/spi.c:4624-4629`）就是在解释这件事：
   > "A board_info is normally registered in arch_initcall(), but even essential
   > drivers wait till later. **REVISIT only boardinfo really needs static linking.**"

2. **`SPI_BUFSIZ` 那块 `buf` 在这里就分配了**，一次分配、全局共用、由一把静态 mutex
   保护。它只服务 `spi_write_then_read()`。这是 SPI 核心里唯一的全局数据缓冲，
   也是为什么这个便捷函数有 4096 字节上限、不适合热路径。

3. **注册了 1 条 bus + 2 个 class**。bus 给 `spi_device` 用，class 给 `spi_controller` 用
   （`02-datastructures.md` §1.0 第 2 组）。`spi_slave_class` 只在 `CONFIG_SPI_SLAVE=y`
   时注册。

4. **两个 reconfig notifier 在这里挂上**。arm64 上有意义的是 `spi_of_notifier`
   （`drivers/spi/spi.c:4452`），它让运行期 overlay 加载能动态增删 SPI 设备，见 §B10。
   ACPI 那条按 arm64-only 约定不展开（裁剪记录见 `99-evidence.md` §2.7）。

## §A2 控制器注册：`spi_register_controller()`（`spi.c:3099-3236`）

`drivers/spi/spi.c:3099`。这是整篇里分支最多的一个函数，按执行顺序拆开：

| 步 | 做什么 | 行 | 失败会怎样 |
|---|-------|---|----------|
| 1 | `dev = ctlr->dev.parent`，为空直接 `-ENODEV` | 3101 / 3106 | 忘了设 parent（`spi_alloc_master` 已经设好）就走不下去 |
| 2 | `spi_controller_check_ops()` 检查三选一 | 3113 | `-EINVAL`。**这是最常见的 register 失败原因** |
| 3 | 分配 `bus_num`，三条互斥分支 | 3117 / 3126 / 3140 | 见下面的详表 |
| 4 | `bus_lock_flag = 0`；`init_completion` ×2 | 3154-3156 | — |
| 5 | `max_dma_len` 为 0 → 置 `INT_MAX` | 3158 | 忘填不会出错，核心给你兜底 |
| 6 | `dev_set_name(&ctlr->dev, "spi%u", bus_num)` | 3164 | 这一步决定了 `/sys/class/spi_master/spiB` 里的 B |
| 7 | `use_gpio_descriptors` 且非 slave → `spi_get_gpio_descs()` | 3167 | 失败 → `free_bus_id` |
| 8 | **用 GPIO 片选就自动加上 `SPI_CS_HIGH`** | 3174 | 驱动不用自己写 |
| 9 | `num_chipselect` 为 0 → `-EINVAL` | 3181 一带 | `__spi_alloc_controller` 已置 1，只有驱动主动写 0 才会踩 |
| 10 | `ctlr->last_cs = -1`（表示当前没选中任何片） | 3187 | — |
| 11 | `device_add(&ctlr->dev)` | 3189 | 失败 → `free_bus_id`。**这一步之后 userspace 就能看到它了** |
| 12 | 建队列：见下面的三分支 | 3201-3209 | 失败要 `device_del` 再 `free_bus_id` |
| 13 | 分配 per-CPU 统计 | 3211 | `-ENOMEM` → `destroy_queue` |
| 14 | 挂进 `spi_controller_list` + 回扫 `board_list` | 3219 / 3221 | 全程持 `board_lock` |
| 15 | `of_register_spi_devices()` + `acpi_register_spi_devices()` | 3225 / 3226 | ⚠️ **返回值被丢掉**，子设备注册失败不影响控制器注册成功 |

**第 3 步的三条分支**（决定 `/dev/spidev**B**.C` 里的 B 是几）：

| 条件 | 行 | 行为 |
|-----|---|------|
| 驱动预设了 `bus_num >= 0` | 3117-3125 | `idr_alloc` 精确占用这个号；被占了返回 `-EBUSY` |
| 有 `of_node`，且 DT 里有 `aliases { spi0 = &spi_x; }` | 3126-3139 | `of_alias_get_id()` 取别名号，同样精确占用 |
| 以上都不满足 | 3140-3152 | 从 `of_alias_get_highest_id("spi") + 1` 开始动态分配 |

第三条分支的起点选择很讲究：**从所有 DT 别名的最大号往后排**，
这样动态分配的号永远不会撞上别名固定的号。所以在 arm64 板子上，
如果 DT 写了 `aliases { spi3 = ...; }`，那么其它没写别名的控制器最小从 4 开始。
**这就是"我明明只有两个控制器，为什么设备叫 `spi5.0`"的答案。**

**第 12 步的三分支**（决定这个控制器走不走消息泵）：

```c
if (ctlr->transfer) {                                    /* :3201 */
        dev_info(dev, "controller is unqueued, this is deprecated\n");
} else if (ctlr->transfer_one || ctlr->transfer_one_message) {
        status = spi_controller_initialize_queue(ctlr);   /* :3204 */
        ...
}
/* 两个都不满足：说明是纯 mem_ops 控制器，不需要队列 */
```

第三种情况（既没有 `transfer*` 也没进 `if`）就是**纯 QSPI 控制器**——只实现
`mem_ops`，不支持普通 SPI 传输。它连消息泵都不建。函数上面的注释明确说了：
"we don't need the queueing logic if the driver is only supporting high-level
memory operations."

### §A2.1 `spi_controller_initialize_queue()`（`spi.c:2192-2219`）：两次"核心替你填字段"

`drivers/spi/spi.c:2192`。这个函数只有 27 行，但做了两件关键的事：

```c
ctlr->transfer = spi_queued_transfer;                          /* :2195 */
if (!ctlr->transfer_one_message)
        ctlr->transfer_one_message = spi_transfer_one_message;  /* :2197 */
```

**核心往 `spi_controller` 里写了两个函数指针。** 这是
`02-datastructures.md` §1.0 第 1 组「SPI 为什么没有 `spi_algorithm`」的直接证据——
如果方法表是共享的 `const` 结构体，这两行根本写不出来。

后果有三个：

1. 注册完之后 `ctlr->transfer` 一定非空。所以 `__spi_async()` 里那句
   `if (!ctlr->transfer) return -ENOTSUPP;`（`drivers/spi/spi.c:4009`）
   实际只会挡住纯 `mem_ops` 控制器。
2. 「这个控制器用的是不是核心自带的排队实现」这个判断，只能写成
   `ctlr->transfer == spi_queued_transfer`——`drivers/spi/spi.c:2233`
   （`spi_flush_queue()`，给 `spi-mem` 用）就是这么判的。
3. 自己实现了 `transfer_one_message` 的 29 个驱动，第 2197 行不会覆盖它们。

然后是 `spi_init_queue()`（`:1967`）：

| 动作 | 行 |
|-----|---|
| `running = false; busy = false; queue_empty = true` | 1969-1971 |
| `kthread_create_worker(0, dev_name(&ctlr->dev))` | 1973 |
| `kthread_init_work(&ctlr->pump_messages, spi_pump_messages)` | 1979 |
| `if (ctlr->rt) spi_set_thread_rt(ctlr)` | 1988-1989 |

**kthread 的名字就是 `dev_name(&ctlr->dev)`，也就是 `spiB`。**
所以 `ps` 里那个 `[spi1]` 内核线程就是这里创建的消息泵——查「传输延迟抖动」时
直接 `chrt -p` 它。

最后 `spi_start_queue()`（`:2079`）把 `running` 置真、`cur_msg` 清空，
然后 `kthread_queue_work()` 踢一下泵（泵会发现队列空、立刻返回）。

## §A3 板级延迟实例化（arm64 上基本空转）

非 DT 平台用 `spi_register_board_info()` 把 `spi_board_info` 静态登记进
`board_list`（`drivers/spi/spi.c:549`）。**但登记时控制器可能还没注册**，
所以做成双向回扫：

| 方向 | 谁扫谁 | 位置 |
|-----|-------|------|
| 控制器后到 | `spi_register_controller()` 遍历 `board_list` | `drivers/spi/spi.c:3220-3222` |
| 板级信息后到 | `spi_register_board_info()` 遍历 `spi_controller_list` | `drivers/spi/spi.c:885` 一带 |

两个方向都持 `board_lock`。arm64 上 `board_list` 通常是空的（DT 走 §A4 那条路），
这一步是空循环。**列在这里是为了让你在读源码时不被它挡住。**

## §A4 DT 子设备枚举

### §A4.1 `of_register_spi_devices()`（`spi.c:2386-2404`）：谁被跳过

`drivers/spi/spi.c:2386`：

```c
if (!ctlr->dev.of_node)
        return;

for_each_available_child_of_node(ctlr->dev.of_node, nc) {
        if (of_node_test_and_set_flag(nc, OF_POPULATED))
                continue;
        spi = of_register_spi_device(ctlr, nc);
        if (IS_ERR(spi)) {
                dev_warn(&ctlr->dev, "Failed to create SPI device for %pOF\n", nc);
                of_node_clear_flag(nc, OF_POPULATED);
        }
}
```

三个跳过条件，**每一个都对应一类"设备没出现"的现场**：

| 跳过原因 | 判据 | 症状 |
|---------|------|------|
| 控制器没有 `of_node` | `!ctlr->dev.of_node` | 整条总线一个子设备都没有。查驱动有没有 `ctlr->dev.of_node = pdev->dev.of_node` |
| 子节点 `status = "disabled"` | `for_each_**available**_child_of_node` | 单个设备缺失，**dmesg 里一句话都没有** |
| `OF_POPULATED` 已置位 | `of_node_test_and_set_flag` | 同上，静默。通常是被 `of_platform_populate` 抢先了 |

第 2、3 条都是**完全静默**的。失败才有 `dev_warn`，被跳过没有任何日志。
这是 `06-devicetree.md` 里那棵决策树的起点。

### §A4.2 `of_register_spi_device()`（`spi.c:2332-2377`）：五步

`drivers/spi/spi.c:2333`：

| 步 | 动作 | 行 | 出错 |
|---|-----|---|------|
| 1 | `spi_alloc_device(ctlr)` | 2337 | `-ENOMEM` |
| 2 | `of_modalias_node(nc, spi->modalias, ...)` | 2347 | `cannot find modalias for %pOF` |
| 3 | `of_spi_parse_dt(ctlr, spi, nc)` | 2354 | 见 §A4.3 |
| 4 | `of_node_get(nc)`；设 `dev.of_node` 和 `dev.fwnode` | 2359-2361 | — |
| 5 | `spi_add_device(spi)` | 2364 | `spi_device register error %pOF` |

**第 2 步是那个著名的坑**：`of_modalias_node()` 取的是 `compatible` 的
**第一个字符串去掉厂商前缀之后的部分**。DT 写 `compatible = "rohm,dh2228fv"`，
`modalias` 就是 `"dh2228fv"`。这意味着 `spi_driver.id_table` 里要写
`"dh2228fv"` 而不是完整的 compatible 字符串。匹配的四级顺序见 §A5。

### §A4.3 `of_spi_parse_dt()`（`spi.c:2240-2330`）：DT 属性 → `spi_device` 字段

`drivers/spi/spi.c:2240`。这是**核心真正会读的 SPI 属性的完整清单**——
不在这个函数里出现的属性，核心一概不看。

| DT 属性 | 行 | 写进哪 |
|--------|---|-------|
| `spi-cpha` | 2247 | `mode \|= SPI_CPHA` |
| `spi-cpol` | 2249 | `mode \|= SPI_CPOL` |
| `spi-3wire` | 2251 | `mode \|= SPI_3WIRE` |
| `spi-lsb-first` | 2253 | `mode \|= SPI_LSB_FIRST` |
| `spi-cs-high` | 2255 | `mode \|= SPI_CS_HIGH` |
| `spi-tx-bus-width` | 2259 | `mode \|=` `SPI_NO_TX`(0) / 无(1) / `SPI_TX_DUAL`(2) / `SPI_TX_QUAD`(4) / `SPI_TX_OCTAL`(8) |
| `spi-rx-bus-width` | 2283 | 同上的 RX 版本 |
| `reg` | 2317 | `chip_select`（**必需**，缺了 `-EINVAL` 并打 `has no valid 'reg' property`） |
| `spi-max-frequency` | 2326 | `max_speed_hz`（**可选**，缺了就是 0，见下面的 ） |

四个必须记住的点：

1. **`spi-max-frequency` 不是必需的**。缺了 `spi->max_speed_hz` 就是 0，
   然后在 `spi_setup()` 里被兜底成 `ctlr->max_speed_hz`。所以
   **忘写这个属性的后果是"以控制器最高速跑"**，不是报错——这是最危险的一类
   静默行为，因为它在一半的板子上能工作。

2. **bus-width 的值不合法只 `dev_warn` 不失败**（`:2277-2280` / `:2301-2304`）：
   `spi-tx-bus-width = <3>` 会打 `spi-tx-bus-width 3 not supported` 然后
   **按单线继续**。

3. **bus-width = 0 是有语义的**：置 `SPI_NO_TX` / `SPI_NO_RX`（`02` §2.2 的两个内部模式位）。
   这是给「物理上只接了一根线」的板子用的。

4. **slave 模式有一条完全不同的短路径**（`:2306-2313`）：
   ```c
   if (spi_controller_is_slave(ctlr)) {
           if (!of_node_name_eq(nc, "slave")) {
                   dev_err(&ctlr->dev, "%pOF is not called 'slave'\n", nc);
                   return -EINVAL;
           }
           return 0;
   }
   ```
   slave 模式下**子节点必须叫 `slave`**，而且 `reg` / `spi-max-frequency`
   根本不解析（从模式位那一段直接 return 了）。

### §A4.4 `spi_add_device()`（`spi.c:698-718`）：唯一性检查

`drivers/spi/spi.c:698`（内层 `__spi_add_device()` 在 `:637`）。核心动作：

| 动作 | 说明 |
|-----|------|
| 持 `ctlr->add_lock` | 防止两个线程同时给同一片选加设备。**`spi-mux` 会嵌套这把锁**，见 §B1 |
| `spi_dev_set_name()` | `"%s.%u"` = 控制器名 + 片选号 → `spi1.0` |
| `device_for_each_child(&ctlr->dev, spi, spi_dev_check)` | 片选撞车检查，撞了返回 **`-EBUSY`** |
| 校验 `chip_select < ctlr->num_chipselect` | 超了 `-EINVAL` |
| `spi_setup(spi)` | ⚠️ **在这里就调了一次**，不是等到 probe |
| `device_add(&spi->dev)` | 至此 `/sys/bus/spi/devices/spi1.0` 出现，驱动匹配开始 |

`-EBUSY` 是「这个片选已经被占了」的专用错误码。两个 DT 子节点写了同样的
`reg` 就会撞上，dmesg 里是 `spi_device register error /spi@.../xxx@0`。

## §A5 匹配与 probe

### §A5.1 `spi_match_device()`（`spi.c:375-396`）：四级顺序

`drivers/spi/spi.c:375`。顺序**不能记错**，因为前面命中就不看后面：

| 级 | 判据 | 说明 |
|---|------|------|
| 1 | `driver_override` | sysfs 里手动指定的强制绑定，最高优先 |
| 2 | `of_driver_match_device()` | 比对 `driver.of_match_table` 和 DT 的 `compatible`。**arm64 上 99% 走这条** |
| 3 | `acpi_driver_match_device()` | arm64-only 约定下不展开 |
| 4 | `spi_match_id()` | 比对 `spi_driver.id_table` 和 `spi_device.modalias` |

**第 2 级和第 4 级用的是不同的字符串**：第 2 级用完整的 `compatible`
（`"rohm,dh2228fv"`），第 4 级用去掉厂商前缀的 `modalias`（`"dh2228fv"`）。
一个驱动可以只填 `of_match_table` 就在 arm64 上工作，但如果它只填了
`id_table`，那 `id_table` 里的字符串必须是**去前缀之后**的那个。这是
「DT 明明写对了 compatible，驱动就是不 probe」的第一嫌疑。

### §A5.2 `spi_probe()`（`spi.c:410-449`）：核心在驱动之前做的事

`drivers/spi/spi.c:410`：

```
spi_probe()
  ├─ 从 DT 取 irq（of_irq_get），-EPROBE_DEFER 要往上传
  ├─ dev_pm_domain_attach()
  ├─ spi_setup(spi)          ← 核心在调你的 probe 之前先协商参数
  └─ sdrv->probe(spi)
        失败 → spi_setup 不回滚，但 pm_domain 会 detach
```

**`spi_setup()` 在你的 `probe()` 之前就被调过了。** 两个后果：

1. 你在 `probe()` 里能直接发传输，不用先调 `spi_setup()`。
2. **如果 DT 里的 `spi-cpol` 之类不被控制器支持，probe 会在你的代码跑之前就失败**，
   dmesg 里是 `spi_setup()` 打的 `setup: unsupported mode bits %x`，
   而不是你的驱动打的任何东西。
3. 你只在需要**改**参数时才自己调 `spi_setup()`（比如运行期换速率）。

## §A6 参数协商：`spi_setup()`（`spi.c:3702-3827`）

`drivers/spi/spi.c:3702`。它是唯一会**修改** `spi_device` 的公共 API，
按「拒绝 → 裁剪 → 下发」三段读：

**第一段：拒绝（返回 `-EINVAL`）**

| 检查 | 说明 |
|-----|------|
| 三线模式不能同时要 `SPI_CS_HIGH` 之外的冲突组合 | 具体组合见函数开头几个 `if` |
| `bits_per_word` 不在 `ctlr->bits_per_word_mask` 里 | 走 `__spi_validate_bits_per_word()` `:3637`。⚠️ mask 为 0 时视为只支持 8 位 |
| `mode` 里有 `ctlr->mode_bits` 之外的位 | 打 `setup: unsupported mode bits %x` |

**第二段：裁剪（静默！）**

```
if (spi->max_speed_hz == 0)
        spi->max_speed_hz = ctlr->max_speed_hz;         /* 兜底 */
if (ctlr->max_speed_hz && spi->max_speed_hz > ctlr->max_speed_hz)
        spi->max_speed_hz = ctlr->max_speed_hz;         /* 静默下调 */
```

**速率被下调不会有任何日志。** 这是「示波器上量到的时钟和 DT 写的不一样」
的第一现场。要看真实速率只有一条路：`spi_transfer.effective_speed_hz`
（由控制器驱动回填，`02` §2.5），或者 `07-debug.md` 里的 trace。

**第三段：下发**

| 动作 | 说明 |
|-----|------|
| `if (ctlr->setup) ctlr->setup(spi)` | ⚠️ kerneldoc 有 IMPORTANT：**调它时总线上可能正有别的设备在传输**（`include/linux/spi/spi.h:588-593`）。所以 `setup` 里不能碰共享寄存器 |
| `spi->rt && !ctlr->rt` → 把泵升成 RT | `drivers/spi/spi.c:3810-3812`。见 §B9 |
| 失败时 `spi_res_release` / `pm_runtime_put` 收尾 | — |

那个 RT 提升是**单向的**：一旦某个设备要求 RT，整个控制器的消息泵就永久变成
RT 优先级，**即使那个设备后来被 remove 了也不会降回来**。函数上面的注释
（`drivers/spi/spi.c:1946-1959`）承认了这一点：
> "the thread will be at realtime priority for all transfers on that controller.
> If this eventually becomes a problem we may see if we can find a way to boost
> the priority only temporarily during relevant transfers."

## §A7 提交：三条路径的分流

### §A7.1 `spi_sync()`（`spi.c:4258-4267`）→ `__spi_sync()`（`spi.c:4178-4235`）

`drivers/spi/spi.c:4258` → `:4178`。

```
spi_sync(spi, msg)
  └─ mutex_lock(&ctlr->bus_lock_mutex)        :4262
     __spi_sync(spi, msg)                     :4178
       ├─ __spi_check_suspended(ctlr)         :4184  → -ESHUTDOWN
       ├─ __spi_validate(spi, msg)            :4190 一带
       ├─ 统计 spi_sync++
       │
       ├─ if (READ_ONCE(ctlr->queue_empty) && !ctlr->must_async)   :4204
       │     快路径
       │     ├─ 统计 spi_sync_immediate++
       │     └─ __spi_transfer_message_noqueue(ctlr, spi, msg)     :4134
       │           在**当前线程**里直接跑完，不进队列、不唤醒 kthread
       │
       └─ else 慢路径
             ├─ spi_async_locked(spi, msg)    :4114
             └─ wait_for_completion(&done)
```

**快路径的三个前提**（少一个就退回慢路径）：

| 前提 | 字段 | 位置 |
|-----|------|------|
| 队列是空的 | `ctlr->queue_empty` | `READ_ONCE`，`drivers/spi/spi.c:4204` |
| 控制器没要求强制异步 | `!ctlr->must_async` | 全树只有 `drivers/spi/spi-mux.c:165` 设它 |
| 调用者能睡 | 隐含（`spi_sync` 本身可睡） | — |

**快路径是 6.1 里 SPI 同步传输的常态**，不是优化捷径。它省掉了
「唤醒 kthread → 上下文切换 → 泵取队列 → 完成后再唤醒调用者」这一整套。
判断某次传输走了哪条路：看 sysfs 的 `spi_sync_immediate` 和 `spi_sync`
两个计数的比值（`07-debug.md`）。

但快路径**仍然要拿 `io_mutex`**（`__spi_transfer_message_noqueue()`
`drivers/spi/spi.c:4139`）——它省的是调度开销，不是互斥。

### §A7.2 `spi_async()`（`spi.c:4060-4080`）：唯一能在原子上下文调的提交口

`drivers/spi/spi.c:4060`：

```c
spin_lock_irqsave(&ctlr->bus_lock_spinlock, flags);      /* :4070 */
if (ctlr->bus_lock_flag)
        ret = -EBUSY;
else
        ret = __spi_async(spi, message);
spin_unlock_irqrestore(&ctlr->bus_lock_spinlock, flags);
```

用的是 **spinlock 而不是 mutex**——这就是 `spi_async()` 能在中断上下文里调的
全部原因。代价是它没法等 `bus_lock_mutex`，所以 `spi_bus_lock()` 期间
它只能返回 `-EBUSY` 而不是排队等。

`__spi_async()`（`:4000`）做四件事：

| 动作 | 行 | 说明 |
|-----|---|------|
| `if (!ctlr->transfer) return -ENOTSUPP` | 4009 | 只会挡住纯 `mem_ops` 控制器（§A2.1） |
| `message->spi = spi` | 4012 | **核心替你填**，提交者不用管 |
| 统计 `spi_async++` | 4014 一带 | |
| `__spi_validate()` + `ctlr->transfer(spi, message)` | 4020 一带 | `transfer` 此时一定是 `spi_queued_transfer`（除了 legacy 驱动） |

### §A7.3 `spi_queued_transfer()`（`spi.c:2187-2190`，4 行的壳）→ `__spi_queued_transfer()`（`spi.c:2155-2178`）

`drivers/spi/spi.c:2187` → `:2155`：

```c
spin_lock_irqsave(&ctlr->queue_lock, flags);
if (!ctlr->running) { ... return -ESHUTDOWN; }
msg->actual_length = 0;
msg->status = -EINPROGRESS;
list_add_tail(&msg->queue, &ctlr->queue);
ctlr->queue_empty = false;                      /* 关掉快路径 */
if (!ctlr->busy && need_pump)
        kthread_queue_work(ctlr->kworker, &ctlr->pump_messages);
spin_unlock_irqrestore(&ctlr->queue_lock, flags);
```

三个要点：

1. `msg->status = -EINPROGRESS` 是「已入队未完成」的标记。
2. **`queue_empty = false` 在入队时立刻置**，所以只要队列里有东西，
   后续的 `spi_sync()` 就一律走慢路径——快慢路径不会交错执行同一个控制器。
3. `need_pump` 参数让 `spi_flush_queue()` 那种「我自己来跑，不要唤醒 kthread」
   的用法成为可能。

## §A8 消息泵：`__spi_pump_messages()`（`spi.c:1764-1847`）

`drivers/spi/spi.c:1764`，由 `spi_pump_messages()`（`:1853`，kthread work 的回调）
调用，也被 `spi_flush_queue()`（`:2231`）直接调用。

```
__spi_pump_messages(ctlr, in_kthread)
  │
  ├─ mutex_lock(&ctlr->io_mutex)                  :1772   硬件互斥从这里开始
  ├─ spin_lock_irqsave(&ctlr->queue_lock)         :1775
  │
  ├─ 已经有 cur_msg？ → 解锁返回（别人在跑）
  ├─ ctlr->busy 且不是 in_kthread？ → 交给 kthread，解锁返回
  ├─ 队列空？
  │     ├─ ctlr->busy → 走 idle 收尾：
  │     │     ├─ ctlr->busy = false
  │     │     ├─ unprepare_transfer_hardware()
  │     │     └─ spi_idle_runtime_pm(ctlr)         :1633
  │     └─ queue_empty = true                     重新打开快路径
  │
  ├─ 取队首 → ctlr->cur_msg
  ├─ list_del_init(&msg->queue)
  ├─ ctlr->busy = true
  ├─ spin_unlock_irqrestore(&ctlr->queue_lock)
  │
  ├─ __spi_pump_transfer_message(ctlr, msg, was_busy)   :1641   → §A9
  │
  ├─ mutex_unlock(&ctlr->io_mutex)                :1837
  └─ cond_resched()                               :1841   主动让出 CPU
```

四个要点：

1. **`io_mutex` 的范围覆盖整个 message**，不是单个 transfer。这就是
   `02` §1.0 第 3 组说的「message 是原子事务」的实现。
2. **`cond_resched()` 在 `io_mutex` 之外**（`:1841`）。所以两个 message 之间
   可以被抢占，一个 message 内部不能被别的 message 插入——但**可以被中断和抢占打断**。
3. **`queue_empty` 只在这里被置回 `true`**（队列真的空了的那一刻）。这是
   快路径能重新生效的唯一时机。
4. **idle 收尾是延迟的**：队列空了才 `unprepare_transfer_hardware()` +
   runtime PM put。所以连续小传输之间硬件不会反复上下电——这是消息泵最重要的
   性能收益，也是 `07-debug.md` 里「为什么 runtime PM 计数不掉」的解释。

## §A9 准备硬件与内存：`__spi_pump_transfer_message()`（`spi.c:1641-1749`）

`drivers/spi/spi.c:1641`。**这是整个传输路径上最容易出错的一段**，
因为它按顺序做了六件互相依赖的事，任何一件失败都要精确回滚。

| 步 | 动作 | 位置 | 失败处理 |
|---|-----|------|---------|
| 1 | `!was_busy && ctlr->auto_runtime_pm` → `pm_runtime_get_sync()` | `:1647-1659` | 失败直接 `msg->status = ret` + `finalize`，**不往下走** |
| 2 | `!was_busy && ctlr->prepare_transfer_hardware` | `:1663` 一带 | 同上，并且要 `pm_runtime_put` |
| 3 | `trace_spi_message_start(msg)` | `:1673` 一带 | — |
| 4 | `ctlr->prepare_message(ctlr, msg)` → 置 `msg->prepared = true` | `:1690` / `:1699` | 失败 → `finalize`；**`prepared` 决定后面要不要配套 `unprepare`** |
| 5 | `spi_map_msg(ctlr, msg)` | `:1300` | 失败 → 走 4 的反向清理 |
| 6 | `ctlr->transfer_one_message(ctlr, msg)` | `:1710` 一带 | 返回非 0 → `msg->status` + `finalize` |

**第 6 步之后那段 `cur_msg_incomplete` / `cur_msg_need_completion` 的舞蹈**
（`drivers/spi/spi.c:1718-1730` 有一段长注释解释动机）：

```
                    ┌ 驱动是同步完成的（transfer_one_message 返回时已经做完）
                    │    → cur_msg_incomplete 已被 finalize 清掉
                    │    → 不需要 wait_for_completion，直接往下
smp_wmb / smp_mb ───┤
                    └ 驱动是异步完成的（ISR 里才 finalize）
                         → 置 cur_msg_need_completion
                         → wait_for_completion(&ctlr->cur_msg_completion)
```

这一对标志加内存屏障的目的是**在同步驱动上省掉一次 completion 的开销**。
它是本篇里唯一需要理解内存序才能读懂的地方；如果只是查 bug，
记住「同步驱动不走 completion」这个结论就够了。

**拆分在哪一步？** `spi_split_transfers_maxsize()`（`:3603`）不在这个函数里，
它由 `__spi_validate()`（`:3850`）在提交时调用——也就是说
**拆分发生在入队之前，不是在泵里**。见 §B4。

## §A10 逐段执行：`spi_transfer_one_message()`（`spi.c:1507-1617`）

`drivers/spi/spi.c:1507`。核心自带的默认实现，55 个只实现 `transfer_one`
的驱动全走这里。

```
spi_transfer_one_message(ctlr, msg)
  │
  ├─ spi_set_cs(msg->spi, !xfer->cs_off, false)     :1517   先拉片选
  ├─ trace_spi_message_start
  │
  ├─ list_for_each_entry(xfer, &msg->transfers, transfer_list) {
  │     ├─ trace_spi_transfer_start(msg, xfer)
  │     ├─ spi_statistics_add_transfer_stats(...)          累加字节 + 17 桶
  │     │
  │     ├─ if (xfer->tx_buf || xfer->rx_buf) {
  │     │     reinit_completion(&ctlr->xfer_completion)     :1534
  │     │  fallback_pio:
  │     │     ret = ctlr->transfer_one(ctlr, msg->spi, xfer)
  │     │
  │     │     ├─ ret > 0  → spi_transfer_wait(ctlr,msg,xfer)  :1352  异步，等
  │     │     ├─ ret == 0 → 已经做完了，往下走
  │     │     └─ ret < 0  → 出错
  │     │           └─ if (cur_msg_mapped &&
  │     │                  (xfer->error & SPI_TRANS_FAIL_NO_START)) {
  │     │                    DMA 没启动 → 解映射、置 fallback、跳回 PIO 重试
  │     │                    goto fallback_pio;             :1542-1548
  │     │                 }
  │     │  } else if (xfer->len) {
  │     │     ret = -EINVAL;    ← 有长度但两个 buf 都是 NULL
  │     │  }
  │     │
  │     ├─ trace_spi_transfer_stop(msg, xfer)
  │     ├─ msg->actual_length += xfer->len                  :1601
  │     │
  │     ├─ spi_transfer_delay_exec(xfer)                    延时（xfer->delay）
  │     └─ if (xfer->cs_change) {
  │            最后一段？ → 记下 keep_cs / 立刻翻片选
  │            否则     → spi_set_cs(false) → delay → spi_set_cs(true)  :1585-1599
  │        }
  │  }
  │
  ├─ if (!keep_cs) spi_set_cs(msg->spi, false, false)       :1605-1606
  ├─ msg->status = ret
  └─ spi_finalize_current_message(ctlr)                     → §A11
```

五个必须记住的点：

1. **片选在循环外拉、在循环外放**（`:1517` 和 `:1605`）。这就是
   「一个 message 默认共享一次片选」的实现位置。
2. **`transfer_one` 的返回值有三种语义**：`>0` = 异步（核心去等）、
   `0` = 已完成、`<0` = 错误。这三种在 `01-api.md` §1.3 有完整表，
   写驱动时返回错值是最常见的 bug 之一。
3. **DMA → PIO 的回退在这里，而不是在 `spi_map_msg` 里**（`:1542-1548`）。
   触发条件是驱动显式置了 `xfer->error & SPI_TRANS_FAIL_NO_START`——
   驱动不置这个位，核心就不敢重试（因为可能已经摇了一半时钟出去）。
4. **`cs_change` 在最后一段的语义是"传完保持片选"**，不是"翻一下"。
   `keep_cs` 那个变量就是干这个的。协议驱动用它来实现「一个 message 结束后
   片选不放，下一个 message 接着用」。
5. **两个 buf 都 NULL 但 `len` 非 0 是 `-EINVAL`**，不是空操作。
   反过来 `len == 0` 是合法的（用来单纯产生一个片选周期或一段延时）。

## §A11 完成回收：`spi_finalize_current_message()`（`spi.c:2026-2076`）

`drivers/spi/spi.c:2026`。**这个函数可能在中断上下文被调**（驱动的 ISR
调 `spi_finalize_current_transfer()` 之后，异步驱动会直接调它），
所以它里面不能睡。

```
spi_finalize_current_message(ctlr)
  ├─ mesg = ctlr->cur_msg
  ├─ if (!ctlr->ptp_sts_supported) 补齐所有 transfer 的时间戳
  ├─ spi_unmap_msg(ctlr, mesg)                    解 DMA 映射
  ├─ if (mesg->prepared && ctlr->unprepare_message)
  │       ctlr->unprepare_message(ctlr, mesg)     :1699 的配对
  ├─ mesg->prepared = false
  ├─ spi_res_release(ctlr, mesg)                  :2054  释放拆分记录
  ├─ if (!mesg->status) mesg->status = ...
  ├─ trace_spi_message_done(mesg)
  ├─ 清 ctlr->cur_msg / cur_msg_mapped / fallback
  ├─ complete(&ctlr->cur_msg_completion)（如果泵在等）
  └─ if (mesg->complete) mesg->complete(mesg->context)
```

三个要点：

1. **`spi_res_release()` 在这里**（`:2054`）。拆分产生的临时 transfer 数组
   在这一刻被还原和释放——所以提交者在 `complete` 回调里看到的 `msg->transfers`
   已经是原样了。
2. **`mesg->complete` 是最后一步**，在所有清理之后。所以 `complete` 回调里
   可以立刻再提交一个新 message。
3. **`prepared` 标志保证 `prepare_message` / `unprepare_message` 严格配对**。
   §A9 第 4 步失败时 `prepared` 还是 false，这里就不会调 `unprepare`。

## §A12 错误与恢复

SPI 没有总线恢复（`00-overview.md` §2.1：推挽驱动，没有 I²C 那种
「时钟被从设备拽住」的状态）。所以恢复手段只有三层：

| 层 | 机制 | 位置 | 覆盖面 |
|---|-----|------|-------|
| 1 | DMA 失败回退 PIO | `drivers/spi/spi.c:1542-1548` | 需要驱动置 `SPI_TRANS_FAIL_NO_START` |
| 2 | `ctlr->handle_err()` | `drivers/spi/spi.c:1568` 一带 | **只有 11 个驱动实现** |
| 3 | 超时 | `spi_transfer_wait()` `drivers/spi/spi.c:1352` | 统计里的 `timedout` 计数 |

**第 2 层的典型实现**（`rockchip_spi_handle_err()`
`drivers/spi/spi-rockchip.c:273-292`）：停 DMA 通道、复位 FIFO。
它做的都是「把控制器自己拽回可用状态」，**没有任何一个驱动去动总线上的线**——
因为动不了。

**第 3 层的超时值**：`spi_transfer_wait()` 里按传输长度和速率算出一个上限，
超了就 `dev_err` + `-ETIMEDOUT`，并累加 `timedout` 统计。
「传输卡住」的第一个观察点就是这个计数（`07-debug.md`）。

**没有第 4 层。** 如果控制器硬件卡死且驱动没实现 `handle_err`，
唯一的出路是控制器驱动自己 remove/probe 一遍。这是 SPI 相对 I²C 的一个真实短板。

## §A13 协议驱动卸载

```
spi_remove()            drivers/spi/spi.c:437
  ├─ if (sdrv->remove) sdrv->remove(spi)      返回 void
  └─ dev_pm_domain_detach(&spi->dev, true)

spi_unregister_device()  drivers/spi/spi.c:2836 一带
  ├─ of_node_clear_flag(OF_POPULATED)   ← 允许这个节点将来被重新枚举
  ├─ device_del(&spi->dev)
  ├─ of_node_put(dev->of_node)
  └─ put_device(&spi->dev)
```

**`OF_POPULATED` 必须清掉**，否则这个 DT 节点再也不会被
`of_register_spi_devices()`（§A4.1）看到——overlay 卸载再加载就失效了。

## §A14 控制器注销：`spi_unregister_controller()`（`spi.c:3297-3336`）

`drivers/spi/spi.c:3297`。**顺序是这段代码的全部要点**：

| 步 | 动作 | 行 | 为什么必须在这个位置 |
|---|-----|---|-------------------|
| 1 | `mutex_lock(&ctlr->add_lock)`（仅 `CONFIG_SPI_DYNAMIC`） | 3303-3304 | 挡住并发的 `spi_add_device()`，否则拆完了还有人往里加 |
| 2 | `device_for_each_child(&ctlr->dev, NULL, __unregister)` | 3306 | **先拆所有子设备**。子设备的 remove 里可能还要发传输，所以必须在停泵之前 |
| 3 | `idr_find(&spi_master_idr, id)` 存到 `found` | 3311 一带 | 先查后删，为第 6 步做准备 |
| 4 | `if (ctlr->queued) spi_destroy_queue(ctlr)` | 3313-3316 | 停泵。失败只 `dev_err`，不中止流程 |
| 5 | `list_del(&ctlr->list)` | 3318 | 从 `spi_controller_list` 摘掉 |
| 6 | `device_del(&ctlr->dev)` | 3320 | sysfs 消失 |
| 7 | `if (found == ctlr) idr_remove(...)` | 3324 一带 | 用第 3 步的结果判断，防止误删别人的 bus_num |
| 8 | `mutex_unlock(&ctlr->add_lock)` | 3331-3332 | — |
| 9 | `if (!ctlr->devm_allocated) put_device(&ctlr->dev)` | 3334-3335 | 用 `devm_spi_alloc_*` 的驱动不需要这一步 |

**第 7 步的 `found == ctlr` 判断是防御性代码**：如果这个控制器从来没成功
`idr_alloc` 过（注册中途失败），`id` 位置上可能是别人的控制器，
无条件 `idr_remove` 会把别人删掉。注释里写的是 "First make sure that this
controller was ever added"。

**第 9 步是 devres 迁移的遗留**。注释说得很直白：
> "Release the last reference on the controller if its driver has not yet been
> converted to devm_spi_alloc_master/slave()."

### §A14.1 `spi_destroy_queue()`（`spi.c:2133-2153`）为什么可能失败

`drivers/spi/spi.c:2133` → `spi_stop_queue()` `:2099`：

```c
unsigned limit = 500;                                    /* :2102 */
while ((!list_empty(&ctlr->queue) || ctlr->busy) && limit--) {
        spin_unlock_irqrestore(&ctlr->queue_lock, flags);
        usleep_range(10000, 11000);                       /* :2115 */
        spin_lock_irqsave(&ctlr->queue_lock, flags);
}
if (!list_empty(&ctlr->queue) || ctlr->busy)
        ret = -EBUSY;
```

**这是一个 500 × 10ms = 最多 5 秒的轮询等待。** 注释自己承认了这个设计：
> "This is a bit lame, but is optimized for the common execution path.
> A wait_queue on the ctlr->busy could be used, but then the common execution
> path (pump_messages) would be required to call wake_up or friends on every
> SPI message. Do this instead."

也就是说：**为了让热路径少一次 `wake_up`，把冷路径写成了 5 秒轮询。**
这是一个明确的取舍，不是疏漏。后果：

| 现象 | 解释 |
|-----|------|
| `rmmod` 某个 SPI 控制器驱动挂了 5 秒 | 有 message 没跑完，在这里轮询 |
| dmesg 里 `could not stop message queue`（`:2126`） | 5 秒还没停下来，`-EBUSY` |
| 紧接着 `problem destroying queue`（`:2140`） | `spi_destroy_queue` 拿到 `-EBUSY` 直接返回，**kthread 不会被销毁** |
| 最后 `queue remove failed`（`:3315`） | `spi_unregister_controller` 打的 |

这三条日志是一串的，看到最外层那句要往里看两层。**注意此时 kthread 泄漏了**
（`kthread_destroy_worker()` `:2150` 没被执行到），但注销流程仍然继续往下走。

## §A15 系统 PM

`spi_controller_suspend()` `drivers/spi/spi.c:3358` / `spi_controller_resume()` `:3374`。
这两个函数**要由控制器驱动自己在它的 PM 回调里调**，核心不会自动调。

```
spi_controller_suspend(ctlr)
  ├─ if (ctlr->queued) ret = spi_stop_queue(ctlr)     可能 -EBUSY（§A14.1）
  └─ __spi_mark_suspended(ctlr)                        :3345
        持 bus_lock_mutex，置 flags |= SPI_CONTROLLER_SUSPENDED

spi_controller_resume(ctlr)
  ├─ __spi_mark_resumed(ctlr)                          :3352
  └─ if (ctlr->queued) ret = spi_start_queue(ctlr)
```

三个要点：

1. **`SPI_CONTROLLER_SUSPENDED` 的检查点在 `__spi_sync()`**
   （`drivers/spi/spi.c:4184-4187`，走 `__spi_check_suspended()` `:3339`），
   返回 **`-ESHUTDOWN`**。挂起期间的传输不会排队等 resume，直接失败。
2. **`spi_async()` 路径不检查这个标志**——它走 `__spi_async()`，
   而 suspend 检查只在 `__spi_sync()` 里。挂起期间的异步提交会入队到
   `running = false` 的队列，然后被 `__spi_queued_transfer()` 用 `-ESHUTDOWN` 拒掉。
   **两条路径都返回 `-ESHUTDOWN`，但拒绝的位置不同。**
3. **标志的置/清都持 `bus_lock_mutex`**，所以正在 `spi_sync()` 里的传输不会
   被挂起打断——它会先跑完。

---

# Part B：横切机制

> Part A 是「什么时候发生」，Part B 是「怎么做到的」。
> 这里的每一节都在 Part A 的多个步骤里被反复用到，所以单独拎出来讲一次。

## §B1 锁与并发

### §B1.1 四把锁 + 一个全局锁

| 锁 | 类型 | 保护什么 | 能在原子上下文取吗 |
|---|------|---------|-----------------|
| `bus_lock_mutex` | mutex | `spi_sync()` 的串行化 + `spi_bus_lock()` 的独占语义 + `SPI_CONTROLLER_SUSPENDED` 的读写 | 不能 |
| `io_mutex` | mutex | **实际的硬件访问**，粒度是一整个 message | 不能 |
| `queue_lock` | spinlock（irqsave） | `queue` 链表、`cur_msg`、`busy`、`running`、`queue_empty` | 能 |
| `bus_lock_spinlock` | spinlock（irqsave） | `bus_lock_flag` | 能 |
| `add_lock` | mutex | 同一控制器上并发加设备 | 不能 |
| `board_lock`（全局） | mutex | `spi_master_idr` / `board_list` / `spi_controller_list` | 不能 |

**锁序**：`bus_lock_mutex` → `io_mutex` → `queue_lock`。

推导依据（不是猜的）：

| 证据 | 位置 |
|-----|------|
| `spi_sync()` 先拿 `bus_lock_mutex`，再进 `__spi_sync()` | `drivers/spi/spi.c:4262` |
| `__spi_transfer_message_noqueue()` 在 `bus_lock_mutex` 里拿 `io_mutex` | `drivers/spi/spi.c:4139` |
| `__spi_pump_messages()` 先拿 `io_mutex`（`:1772`），再拿 `queue_lock`（`:1775`） | `drivers/spi/spi.c:1772` / `:1775` |

`add_lock` 和 `board_lock` 只出现在注册/注销路径上，不与传输路径的三把锁嵌套。
唯一的例外是 `spi_add_device()` 在 `add_lock` 里调 `spi_setup()`，而
`spi_setup()` 不取传输路径的任何锁——所以仍然安全。

### §B1.2 为什么 `spi_async()` 用 spinlock、`spi_sync()` 用 mutex

这不是风格问题，是**上下文能力的直接后果**：

```
spi_async()                          spi_sync()
  spin_lock_irqsave(bus_lock_spinlock)  mutex_lock(bus_lock_mutex)
  ↓ 能在中断里调                        ↓ 只能在可睡上下文调
  ↓ 但拿不到 bus_lock_mutex             ↓ 能等
  ↓ 所以 bus_lock 期间只能 -EBUSY       ↓ 所以 bus_lock 期间排队等
  drivers/spi/spi.c:4070                drivers/spi/spi.c:4262
```

由此得出一条实用结论：**`spi_bus_lock()` 期间，同步提交会阻塞、异步提交会失败。**
如果你的驱动同时用两种提交方式，`spi_bus_lock()` 会让它们表现不一致。

### §B1.3 `spi-mux` 的同类型锁嵌套

`drivers/spi/spi-mux.c` 让一条 SPI 总线挂在另一条 SPI 总线下面。于是
上层的 `io_mutex` 里会再取下层的 `io_mutex`——同一个锁类，lockdep 会报
`possible recursive locking`。解法是显式降子类：

```c
lockdep_set_subclass(&ctlr->io_mutex, 1);    /* drivers/spi/spi-mux.c:144 */
lockdep_set_subclass(&ctlr->add_lock, 1);    /* drivers/spi/spi-mux.c:145 */
```

同一个文件里还有 `ctlr->must_async = true`（`drivers/spi/spi-mux.c:165`），
**这两件事是同一个问题的两个必要条件**：

| 如果缺了 | 会怎样 |
|---------|-------|
| 缺 `lockdep_set_subclass` | lockdep 误报（功能正常，但日志被污染） |
| 缺 `must_async` | **真死锁**。同步快路径会在上层 `io_mutex` 里再取下层 `io_mutex`——如果两者是同一个控制器（多级 mux），就是自死锁 |

这是全树唯一设 `must_async` 的地方，也是理解快路径边界最好的反例。

### §B1.4 中断上下文能碰哪些函数

| 函数 | 能在 ISR 里调吗 | 依据 |
|-----|--------------|------|
| `spi_async()` | ✓ | 只用 spinlock |
| `spi_sync()` | ✗ | `mutex_lock` + `wait_for_completion` |
| `spi_finalize_current_transfer()` | ✓ | 只 `complete()`，这是给 `transfer_one` 的 ISR 用的 |
| `spi_finalize_current_message()` | ✓ | 给自己实现 `transfer_one_message` 的异步驱动用 |
| `spi_get_next_queued_message()` | ✓ | `spin_lock_irqsave(&ctlr->queue_lock)`，`drivers/spi/spi.c:2004` 一带 |
| `spi_delay_exec()` | ✗ | 里面有 `might_sleep()`，`drivers/spi/spi.c:1461` |
| `msg->complete` 回调 | 由核心在什么上下文调就是什么上下文 | 所以**回调里一律不能睡** |

## §B2 片选状态机：`spi_set_cs()`

`drivers/spi/spi.c:975`。看起来是「拉一根线」，实际有四个正交维度。

### §B2.1 早退：`set_cs` 不是每次传输都被调

```c
if (!force && ((enable && spi->controller->last_cs == spi_get_chipselect(spi, 0)) ||
               (!enable && spi->controller->last_cs != spi_get_chipselect(spi, 0))) &&
    (spi->controller->last_cs_mode_high == (spi->mode & SPI_CS_HIGH)))
        return;                                   /* drivers/spi/spi.c:983-986 一带 */
```

**状态没变就直接返回。** 两个后果：

1. 驱动的 `set_cs` 回调**不会每次传输都被调**。「我在 `set_cs` 里加了打印，
   为什么只打了一次」——答案在这三行。
2. `last_cs` / `last_cs_mode_high` 是**跨 message 保留的**。所以
   `cs_change` 让片选保持在激活状态时，下一个 message 的开头这里会早退，
   片选就真的连续保持住了。

`force = true` 只在少数需要强制重发的地方用（`spi_transfer_one_message()` 里
传的是 `false`，`drivers/spi/spi.c:1517`）。

### §B2.2 四种极性/驱动组合

`drivers/spi/spi.c:1015-1031` 一带的核心逻辑：

| 情况 | `cs_gpiod` | `SPI_MASTER_GPIO_SS` | 行为 | 行 |
|-----|-----------|---------------------|------|---|
| 纯 native 片选 | NULL | — | 只调 `ctlr->set_cs(spi, !enable)` | 1029 一带 |
| 纯 GPIO 片选 | 非 NULL | 未设 | 只 `gpiod_set_value_cansleep()` | 1021-1022 |
| GPIO + native 同时 | 非 NULL | **已设** | **两个都拉** | 1018-1020 |
| 无片选 | `SPI_NO_CS` in mode | — | 什么都不做 | — |

极性有两层反转，这是最容易搞错的地方：

1. **`SPI_CS_HIGH`** 决定逻辑电平（高有效还是低有效）。
2. **gpiod 的 `GPIO_ACTIVE_LOW`**（DT 里 `cs-gpios = <&gpio 5 GPIO_ACTIVE_LOW>`）
   由 gpiolib 自己再翻一次。

所以 `gpiod_set_value_cansleep(spi->cs_gpiod, activate)` 传的是**逻辑值**，
不是电平值——DT 里的 `GPIO_ACTIVE_LOW` 和 `spi-cs-high` **会互相抵消**。
这条正好是 `06-devicetree.md` 里那张极性表的来源，也是「片选电平反了」
最常见的原因。

另一条：`spi_register_controller()` 在 `use_gpio_descriptors` 时
**自动给 `mode_bits` 加上 `SPI_CS_HIGH`**（`drivers/spi/spi.c:3174`），
理由是「GPIO 片选总能实现任意极性」。所以用 gpiod 的控制器不需要自己声明
支持 `SPI_CS_HIGH`。

### §B2.3 三段片选时序

`cs_setup` / `cs_hold` / `cs_inactive`（`include/linux/spi/spi.h:205-207`）
由 `spi_set_cs()` 在拉线前后 `spi_delay_exec()` 执行。

但 `set_cs_timing` 回调（把时序编程进硬件）**全树只有 3 个驱动实现**：
`spi-mt65xx.c`、`atmel-quadspi.c`、`spi-tegra114.c`。其余控制器上这三个字段
只能靠软件延时兜——精度是微秒级，不是纳秒级。

## §B3 DMA 映射、哑缓冲与回退

### §B3.1 `spi_map_msg()`：先补哑缓冲，再映射

`drivers/spi/spi.c:1300`。分两段：

**第一段：哑缓冲**（只在 `MUST_RX`/`MUST_TX` 且非 3-wire 时做）

```
扫一遍所有 transfer，算出最大的「缺 tx」和「缺 rx」长度
  ↓
krealloc(ctlr->dummy_tx, max_tx, GFP_KERNEL | GFP_DMA | __GFP_ZERO)   :1322 一带
krealloc(ctlr->dummy_rx, max_rx, GFP_KERNEL | GFP_DMA)                :1330 一带
  ↓
再扫一遍，把 NULL 的 tx_buf/rx_buf 指向哑缓冲                          :1342-1344
```

三个要点：

1. **`dummy_tx` 带 `__GFP_ZERO`，`dummy_rx` 不带**（`:1323` vs `:1331`）。
   合理：发出去的必须是确定值（0），收进来的马上会被覆盖。
2. **`krealloc` 是按需增长的，`ctlr->dummy_*` 在控制器生命周期内复用**——
   不是每个 message 分配一次。
3. **`SPI_3WIRE` 被排除在外**（`:1306`）。三线模式下收发本来就分时复用同一根线，
   补哑缓冲没有意义。

哑缓冲**会改写调用者的 `xfer->tx_buf`/`rx_buf`**。这是核心少数几个
直接写提交者数据结构的地方——所以提交者不能假设 `complete` 回调里
`xfer->rx_buf` 还是自己填的那个指针。

**第二段：`__spi_map_msg()`**（`:1137`）

| 条件 | 行为 |
|-----|------|
| `msg->is_dma_mapped` | 什么都不做（提交者说已经映射好了） |
| `!ctlr->can_dma` 或 `can_dma()` 返 false | 什么都不做，走 PIO |
| 需要映射 | `spi_map_buf()` → `dma_map_sgtable()`（`:1102`），置 `ctlr->cur_msg_mapped = true`（`:1196`） |
| 映射失败 | 回滚已映射的部分，`cur_msg_mapped = false`（`:1223`） |

**`can_dma()` 是逐 transfer 问的**，不是整个 message 问一次。所以同一个
message 里可以一部分走 DMA、一部分走 PIO——24 个实现了 `can_dma` 的驱动
基本都是按「长度是否超过阈值」来答的。

### §B3.2 DMA → PIO 回退的三个条件

回退发生在 `spi_transfer_one_message()`（`drivers/spi/spi.c:1542-1548`），
**三个条件必须同时满足**：

| 条件 | 判据 |
|-----|------|
| 这次确实做了 DMA 映射 | `ctlr->cur_msg_mapped` |
| 驱动明确说了「时钟一下都没摇」 | `xfer->error & SPI_TRANS_FAIL_NO_START` |
| 还没回退过 | 隐含（`ctlr->fallback` 在 `:1545` 才被置） |

第 2 个条件是**驱动的责任**。驱动只返回 `-EIO` 而不置 `SPI_TRANS_FAIL_NO_START`，
核心就不会重试——因为可能已经摇了一半时钟出去，重发会导致从设备收到重复数据。
**这是一个正确但反直觉的保守设计。**

`ctlr->fallback` 在 `:1545` 置真，在 `:1835`（泵的收尾）和 `:4148`
（noqueue 路径的收尾）清零。所以它的作用域是「一个 message」。

## §B4 transfer 拆分与 `spi_res`

### §B4.1 拆分在哪一步

**拆分发生在提交时，不是在泵里。** 调用链：

```
__spi_validate()            drivers/spi/spi.c:3850
  └─ spi_split_transfers_maxsize()   :3603
        └─ __spi_split_transfer_maxsize()   :3524
              └─ spi_replace_transfers()    :3427
                    ├─ spi_res_alloc()      :3441
                    └─ spi_res_add()        :3519
```

后果：**入队的 message 已经是拆好的**。所以泵和控制器驱动看到的
transfer 链表长度可能比提交者写的长。

触发条件：`ctlr->max_transfer_size` 返回的值小于某个 transfer 的 `len`。
只有 10 个驱动实现了 `max_transfer_size`——其余控制器不会触发拆分。

### §B4.2 `spi_res`：绑在 message 上的 devres

`spi_res` 的设计定位见 `02-datastructures.md` §2.8。在拆分场景里它解决的问题是：

```
提交者的 transfers:   [ A(8KB) ]
                          ↓ 拆分
实际执行的 transfers: [ A1(4KB) ][ A2(4KB) ]
                          ↓ 但 A1/A2 的内存从哪来？谁来还原？
spi_res:              记录「原来是 A，现在换成了 A1+A2」
                          ↓ spi_finalize_current_message() :2054
提交者看到的:         [ A(8KB) ]   ← 原样还原
```

**提交者永远看不到拆分**。这也意味着调试时看到的 trace 事件数量
可能比你写的 transfer 多——`07-debug.md` 里有对应的说明。

释放点只有一个：`spi_res_release()` `drivers/spi/spi.c:959`，
由 `spi_finalize_current_message()` 在 `:2054` 调用。

## §B5 延时执行

三层结构，每层都有一个必须知道的取舍。

### §B5.1 `spi_delay_to_ns()`：单位换算

`drivers/spi/spi.c:1418`。三个单位：

| 单位 | 换算 | 特殊行为 |
|-----|------|---------|
| `SPI_DELAY_UNIT_USECS` | `× NSEC_PER_USEC` | — |
| `SPI_DELAY_UNIT_NSECS` | 原样 | — |
| `SPI_DELAY_UNIT_SCK` | `× (1e9 / hz)` | **需要 `xfer`**，传 NULL 返 `-EINVAL` |

`SCK` 单位取速率的方式很讲究（`drivers/spi/spi.c:1442`）：

```c
hz = xfer->effective_speed_hz ?: xfer->speed_hz / 2;
```

注释解释了那个 `/2`：
> "If there is unknown effective speed, approximate it by **underestimating**
> with half of the requested hz."

**驱动没回填 `effective_speed_hz` 时，核心按请求速率的一半估算——也就是
故意把延时算长一倍。** 宁可慢，不可短。这是一个保守但正确的默认。
所以：**驱动回填 `effective_speed_hz` 不只是为了可观测性，还直接影响
`SPI_DELAY_UNIT_SCK` 延时的精度。**

### §B5.2 `_spi_transfer_delay_ns()`：三档实现

`drivers/spi/spi.c:1402`：

| 延时长度 | 用什么 | 会睡吗 |
|---------|-------|-------|
| `ns == 0` | 直接返回 | — |
| `ns <= 1000`（≤1µs） | `ndelay()` | 忙等 |
| 换算后 `us <= 10` | `udelay()` | 忙等 |
| `us > 10` | `usleep_range(us, us + us/10)` | **会睡**，上界留 10% 余量 |

**10 微秒是忙等和睡眠的分界线。** 这解释了两件事：

1. `spi_delay_exec()` 里有 `might_sleep()`（`:1461`）——即使大多数情况走忙等，
   API 契约上它是可睡的，所以不能在原子上下文调。
2. 短延时（`word_delay` 常见值 1~2µs）是忙等的，**不会引入调度延迟**，
   但会真的占着 CPU。密集 `word_delay` 是一个隐藏的 CPU 消耗源。

### §B5.3 `cs_change_delay` 的隐式默认值

`_spi_transfer_cs_change_delay()` `drivers/spi/spi.c:1476`：

```c
u32 default_delay_ns = 10 * NSEC_PER_USEC;        /* :1479 */

if (!delay) {
        if (unit == SPI_DELAY_UNIT_USECS)
                _spi_transfer_delay_ns(default_delay_ns);
        return;
}
```

**`cs_change_delay` 不填时，如果单位恰好是 `SPI_DELAY_UNIT_USECS`（也就是 0），
会插入 10µs 默认延时。** 而 `SPI_DELAY_UNIT_USECS` 的值就是 0
（`include/linux/spi/spi.h:113`）——所以对**任何没显式设过 `cs_change_delay`
的 transfer**（结构体被 memset 成 0），这个默认延时都会生效。

这是本篇里最隐蔽的一条行为：**`cs_change` 翻片选时总有至少 10µs 的开销，
即使你什么都没配。** 需要更快就必须显式设一个非零的小值 + 纳秒单位。

单位不支持时的降级路径也在这里（`:1490-1496`）：`dev_err_once` 一句
`Use of unsupported delay unit %i, using default of %luus`，然后**仍然插 10µs**。
注意是 `_once`——同一个设备只打一次，`07-debug.md` 的日志可见性表里有登记。

## §B6 runtime PM

核心只在 `auto_runtime_pm = true`（42 个驱动设它）时接管 runtime PM。
**操作的对象是 `ctlr->dev.parent`，也就是那个 platform device，不是控制器自己**：

| 时机 | 动作 | 行 |
|-----|-----|---|
| 泵开始处理第一个 message（`!was_busy`） | `pm_runtime_get_sync(ctlr->dev.parent)` | 1648 |
| 队列空了（idle 收尾） | `pm_runtime_mark_last_busy` + `pm_runtime_put_autosuspend` | 1636-1637 |
| `spi_setup()` 里 | 同上一对 | 3673-3674 |
| `spi_setup()` 失败路径 | 同上一对 | 3802-3803 |

三个要点：

1. **`get` 是 `_sync` 的，`put` 是 `_autosuspend` 的**。所以第一次传输会同步等
   硬件上电（可能几毫秒），而下电是延迟的——延迟多久由驱动的
   `pm_runtime_set_autosuspend_delay()` 决定，核心不管。
2. **粒度是「队列从非空变空」，不是每个 message**。连续小传输之间硬件保持
   上电状态，这是消息泵最大的性能收益（§A8 要点 4）。
3. `__spi_alloc_controller()` 里有 `pm_suspend_ignore_children(&ctlr->dev, true)`
   （`drivers/spi/spi.c:2930` 一带）——**子设备的 runtime PM 状态不影响控制器**。
   否则每个 SPI 芯片的 runtime PM 都会把控制器钉在 active。

## §B7 统计

per-CPU + `u64_stats_sync`，累加宏 `SPI_STATISTICS_ADD_TO_FIELD`
（`include/linux/spi/spi.h:85`）/ `SPI_STATISTICS_INCREMENT_FIELD`（`:96`）。

累加点（**每次都同时打设备侧和控制器侧两份**）：

| 累加什么 | 在哪 |
|---------|-----|
| `messages` / `bytes` / `bytes_tx` / `bytes_rx` / 17 桶 | `spi_statistics_add_transfer_stats()`，`spi_transfer_one_message()` 里调 |
| `spi_sync` / `spi_sync_immediate` | `__spi_sync()` `drivers/spi/spi.c:4196` / `:4207` 一带 |
| `spi_async` | `__spi_async()` `:4014` 一带 |
| `errors` | 各失败路径 |
| `timedout` | `spi_transfer_wait()` `:1352` |
| `transfers_split_maxsize` | `spi_split_transfers_maxsize()` `:3603` |

读取侧 `spi_pcpu_stats_totalize()`（`drivers/spi/spi.c:120-137`）遍历所有 CPU
做重试循环求和。sysfs 的 `show` 由 `SPI_STATISTICS_ATTRS`（`:139`）批量生成。

**这是本子系统里唯一被 PREEMPT_RT 补丁改过的地方**：`:130` 和 `:133`
的两个 `_irq` 后缀被去掉。传输路径一行没改。取证见 `99-evidence.md` §1.2。

最有用的一对比值：`spi_sync_immediate / spi_sync`。接近 1 说明几乎全走快路径；
明显小于 1 说明有并发竞争或者 `must_async`。查「SPI 延迟突然变大」先看这个。

## §B8 tracepoint 机制

`include/trace/events/spi.h`（239 行）定义了 9 个事件：

| 事件 | 定义行 | 触发点 |
|-----|-------|-------|
| `spi_controller_idle` / `spi_controller_busy` | 11 / 29 | 泵的 idle/busy 切换 |
| `spi_setup` | 37 | `spi_setup()` 出口 |
| `spi_set_cs` | 45 | `spi_set_cs()` |
| `spi_message_submit` | 78 | `__spi_async()` |
| `spi_message_start` | 102 | `__spi_pump_transfer_message()` / `spi_transfer_one_message()` |
| `spi_message_done` | 125 | `spi_finalize_current_message()` |
| `spi_transfer_start` | 220 | 每个 transfer 之前 |
| `spi_transfer_stop` | 228 | 每个 transfer 之后 |

三个机制要点：

1. **一个 `TP_CONDITION` 都没有。** 意味着这些事件开了就全量记录，
   没有内建过滤。高频 SPI（比如 SPI-NOR 跑文件系统）开 `spi_transfer_*`
   会产生巨量事件——过滤只能靠 `trace-cmd -f` 或 `set_event_pid`。
2. **`spi_transfer_start` / `spi_transfer_stop` 被 `EXPORT_TRACEPOINT_SYMBOL`**
   （`drivers/spi/spi.c:2075` 一带）。所以模块化的控制器驱动可以自己触发它们
   ——这是为了让自己实现 `transfer_one_message` 的驱动也能产生一致的 trace。
   其余 7 个事件没有导出，只有核心能触发。
3. **`spi_transfer_start` 记录的是 `xfer->len` 和 `xfer->tx_buf` 的内容摘要**，
   所以它能看到实际发出去的字节——这是不接示波器时最接近线上真相的观察点。

本篇自查发现的一处源码缺陷：`include/trace/events/spi.h:236` 的闭合注释
写的是 `#endif /* _TRACE_POWER_H */`，而文件头的 guard 是 `_TRACE_SPI_H`
——**复制粘贴遗留，无功能影响**，登记在 `99-evidence.md` §4。

## §B9 RT 优先级与消息泵调度

`spi_set_thread_rt()` `drivers/spi/spi.c:1960`，实现只有一句
`sched_set_fifo(ctlr->kworker->task)`（`:1964`）。

**两个触发点**：

| 触发点 | 位置 | 时机 |
|-------|------|------|
| `ctlr->rt`（控制器驱动预设） | `drivers/spi/spi.c:1988-1989`，在 `spi_init_queue()` 里 | 注册时 |
| `spi->rt`（设备侧要求） | `drivers/spi/spi.c:3810-3812`，在 `spi_setup()` 里 | **运行期，任何一次 setup** |

```c
if (spi->rt && !spi->controller->rt) {          /* :3810 */
        spi->controller->rt = true;
        spi_set_thread_rt(spi->controller);
}
```

**三个必须知道的后果**：

1. **单向、不可逆**。一旦某个设备要求 RT，整个控制器的泵永久是 RT 优先级，
   即使那个设备后来被 remove。函数上面的注释（`:1946-1959`）承认了这一点：
   > "If this eventually becomes a problem we may see if we can find a way to
   > boost the priority only temporarily during relevant transfers."
2. **影响面是整条总线**，不是单个设备。同一控制器上的其它设备也会被
   RT 泵服务——这既是收益（延迟低）也是风险（一个慢设备的长传输会以 FIFO 优先级
   霸占 CPU）。
3. **快路径不受影响**。快路径在调用者线程里跑，用的是调用者的优先级，
   跟泵的优先级无关。所以「设了 `spi->rt` 但延迟没改善」的常见原因是：
   **传输本来就走快路径，泵根本没参与。**

`dev_info` 一句 `will run message pump with realtime priority`（`:1961-1962`）
是唯一的确认信号，`07-debug.md` 里有登记。

## §B10 OF_DYNAMIC：运行期增删设备

`spi_of_notifier`（`drivers/spi/spi.c:4452` 起的 `of_spi_notify()`）挂在
`of_reconfig_notifier` 上，在 §A1 注册。

```
of_spi_notify(nb, action, arg)
  ├─ OF_RECONFIG_CHANGE_ADD
  │     ├─ 找父节点对应的控制器：
  │     │     class_find_device_by_of_node(&spi_master_class, rd->dn->parent)   :4442
  │     │     失败再试 spi_slave_class                                          :4444
  │     ├─ 父节点不是 SPI 控制器 → NOTIFY_OK（不关我的事）
  │     ├─ of_node_test_and_set_flag(OF_POPULATED) 已置 → 跳过
  │     └─ of_register_spi_device(ctlr, rd->dn)     ← 复用 §A4.2 那条路
  │
  └─ OF_RECONFIG_CHANGE_REMOVE
        ├─ 节点没有 OF_POPULATED → 跳过
        ├─ of_find_spi_device_by_node() 找到 spi_device
        └─ spi_unregister_device(spi)                ← 复用 §A13
```

三个要点：

1. **`class_find_device_by_of_node()` 用的是 class，不是 bus**（`:4442`）——
   这是 `02-datastructures.md` §1.0 第 2 组说的「class 提供反查能力」的
   唯一实际用例。控制器走 class 不只是为了 sysfs 好看。
2. **两条路径都完全复用静态枚举的代码**（`of_register_spi_device` /
   `spi_unregister_device`）。所以 overlay 加载出来的设备和 DT 里静态写的
   设备**没有任何区别**。
3. **`CONFIG_SPI_DYNAMIC` 的定义是 `def_bool ACPI || OF_DYNAMIC || SPI_SLAVE`**
   （`drivers/spi/Kconfig`），不是用户可选项。它控制的是
   `spi_add_device()` / `spi_unregister_controller()` 里那几处 `add_lock`——
   只有可能动态增删时才需要那把锁。

---

# Part C：对实现的评价

> 这一节是技术评价，不是入门解释（后者在 `08-feynman.md`）。
> 每一条都有源码依据，不做无据推测。

## §C1 源码里的设计债

全树 `REVISIT/FIXME/TODO/XXX` 在 SPI 核心 + 头文件 + spidev 里**只有 4 条**
（`grep -n "REVISIT\|FIXME\|TODO\|XXX" drivers/spi/spi.c drivers/spi/spi-mem.c
include/linux/spi/spi.h include/linux/spi/spi-mem.h drivers/spi/spidev.c`）：

| # | 位置 | 内容 | 评价 |
|---|------|------|------|
| 1 | `include/linux/spi/spi.h:1097-1106` | 想加一个「只影响最后一个 transfer」的标志，用来表达「读 16 位长度 L，紧接着读 L 字节」这种依赖前一段结果的调度 | **真实缺口**。目前这类协议只能拆成两个 `spi_sync()`，中间靠 `cs_change` 保持片选。多队列流水线控制器无法优化它 |
| 2 | `drivers/spi/spi.c:4624-4629` | 只有 `boardinfo` 真的需要静态链接，其余本可模块化 | **历史包袱**。arm64 走 DT，`board_list` 基本是空的，这条债的实际成本接近 0 |
| 3 | `drivers/spi/spidev.c:60` | 改 `SPI_MODE_MASK` 里那些危险标志（`3WIRE`/`NO_CS`/`READY`）该不该要特权 | **真实安全考虑**。目前任何能打开 `/dev/spidevB.C` 的进程都能设 `SPI_NO_CS`，从而破坏共享总线的消息边界 |
| 4 | `drivers/spi/spidev.c:672-675` | `read`/`write` 应改成 aio | 低优先级。ioctl 路径已经能表达全部语义 |

值得注意的是**没有一条 FIXME**——SPI 核心是一段成熟稳定的代码，
四条 REVISIT 都是「可以做得更好」而不是「这里有问题」。

## §C2 明确的取舍（不是缺陷）

| 取舍 | 位置 | 代价 | 收益 |
|-----|------|------|------|
| `spi_stop_queue()` 用 500×10ms 轮询而不是 wait_queue | `drivers/spi/spi.c:2099-2117` + 注释 `:2106-2111` | `rmmod` 最坏挂 5 秒，超时后 kthread 泄漏 | 热路径每个 message 省一次 `wake_up` |
| `cur_msg_incomplete` / `need_completion` + 内存屏障 | `drivers/spi/spi.c:1718-1730` | 代码难读，需要理解内存序 | 同步驱动省掉一次 completion |
| `SPI_DELAY_UNIT_SCK` 无 `effective_speed_hz` 时按半速估 | `drivers/spi/spi.c:1442` | 延时偏长一倍 | 绝不会偏短（宁慢不错） |
| DMA 失败必须驱动显式置 `SPI_TRANS_FAIL_NO_START` 才回退 | `drivers/spi/spi.c:1542-1548` | 不置就得不到回退 | 绝不重发已经摇出去的时钟 |
| 方法与数据合并在 `spi_controller` 里 | `include/linux/spi/spi.h:509-729` | 每控制器多几十字节函数指针，方法表不能 `const`/共享 | 核心可以逐个回调降级、可以动态替换（§A2.1） |
| `spi_write_then_read()` 用全局 `buf` + 静态 mutex | `spi_init()` 里分配 | 4096 字节上限 + 全局串行化 | 调用方零分配、零 DMA 考虑 |

这六条都在源码注释里有明确说明，是**设计者知情的选择**，
不应该在 review 里当成 bug 报。

## §C3 命名遗留

| 遗留 | 现状 | 影响 |
|-----|------|------|
| `master` / `slave` → `controller` / `target` | 两套名字并存。`spi_device.master` 和 `.controller` 是同一个指针（`drivers/spi/spi.c:596`，注释 `/* Compatibility layer */`） | 读代码时要认两套。**新代码只用 `controller`** |
| `SPI_MASTER_GPIO_SS` | **7 个** `flags` 标志里唯一一个**只有旧名字**的（`include/linux/spi/spi.h:555`，BIT(5)） | 容易误以为它是「旧别名」。它不是——旧别名（5 个）在 `:1595-1599`，而 `SPI_CONTROLLER_GPIO_SS` **根本不存在**。另一头是 `SPI_CONTROLLER_SUSPENDED`（`:556`）只有新名字 |
| `slave_abort` / `target_abort` | union，两个名字同一个字段。`slave_abort` 有 9 个实现，**`target_abort` 有 0 个** | `target_abort` 是为将来准备的名字，6.1 里是死字段 |
| `spi_get_chipselect(spi, idx)` 等四个 accessor 的 `idx` 参数 | 完全被忽略（`include/linux/spi/spi.h:266`/`271`/`276`/`281`） | **容易误以为已支持一设备多片选**。6.1 里 `cs_gpiod` 是单数、`chip_select` 是单个 `u8` |
| `spidev_release()` 在 `drivers/spi/spi.c:47` 和 `drivers/spi/spidev.c` 里各有一个同名函数 | 两者毫无关系 | grep 时容易串 |

这几条**不是纯粹的美观问题**：后两条会让读者对功能边界产生错误判断，
是真实的可维护性成本。

## §C4 PREEMPT_RT 对本子系统的影响面

本树是 `-rt58` 补丁树（`99-evidence.md` §1.1）。SPI 的受影响面**极小**：

| 项 | 结论 |
|---|------|
| 被改的文件 | 只有 `drivers/spi/spi.c` |
| 被改的行数 | **4 行** |
| 被改的位置 | `spi_pcpu_stats_totalize()` `drivers/spi/spi.c:120-137` 里的 `:130` 和 `:133` |
| 改了什么 | `u64_stats_fetch_begin_irq` → `u64_stats_fetch_begin`，`u64_stats_fetch_retry_irq` → `u64_stats_fetch_retry` |
| 传输路径受影响吗 | **不受**。那个函数只被 sysfs 的 `show` 处理器调用（`SPI_STATISTICS_ATTRS` `:139`） |
| `drivers/spi/` 其余 136 个 .c | 一行没改 |
| `include/linux/spi/*.h` | 一行没改 |

**所以本篇描述的所有传输路径行为都是纯上游 6.1 语义**，不需要区分 RT 和非 RT。

但这不等于 RT 对 SPI 没影响——只是影响不在代码里，而在运行时：

| RT 下的行为差异 | 机制 |
|--------------|-----|
| `_spi_transfer_delay_ns()` 里 `us > 10` 走 `usleep_range()`，在 RT 上调度延迟特性不同 | §B5.2 |
| 消息泵 kthread 在 RT 内核上的抢占行为不同 | §B9 |
| `spin_lock_irqsave(&ctlr->queue_lock)` 在 RT 上是可睡的 rt_spinlock | 通用 RT 语义 |

这三条都是**通用 RT 语义作用在未修改的 SPI 代码上**的结果，不是 SPI 自己的改动。
本篇不展开（超出 SPI 子系统边界），登记在 `99-evidence.md` §5 的「推断非观察」层。

## §C5 与 i2c 核心的设计对比

两者都是「硬件总线型」，放在一起看能看清各自的选择：

| 维度 | i2c | spi | 谁更好 |
|-----|-----|-----|-------|
| 方法与数据 | 分离（`i2c_algorithm`） | 合并（回调在 `spi_controller` 里） | **各有道理**。i2c 能共享 `const` 方法表；spi 能逐个降级 + 核心动态替换（§A2.1） |
| 探测能力 | 有（`i2c_new_scanned_device`，靠 ACK） | **无**（协议里没有 ACK） | i2c。这是硬件差异决定的，不是软件选择 |
| 总线恢复 | 有（`bus_recovery_info`，靠开漏可以强拉时钟） | **无**（推挽驱动，没得可拉） | i2c。同样是硬件差异 |
| 同步传输快路径 | 无等价物 | 有（`queue_empty` + noqueue，§A7.1） | **spi**。省掉整个 kthread 往返 |
| 消息原子性 | `i2c_lock_bus` | `io_mutex`（粒度是一个 message） | 相当 |
| 传输参数粒度 | 整条总线一个速率 | **每个 transfer 都能改速率/位宽/线宽** | spi。SPI 的点对点特性让这变得可能 |
| 错误反馈 | 每字节 ACK | **完全没有** | i2c。SPI 上「传输成功」只表示时钟摇完了 |

一条贯穿性的观察：**SPI 核心的复杂度几乎全部来自"参数可以逐段变"**
（速率/位宽/线宽/延时/片选），而 i2c 核心的复杂度主要来自"总线状态可能坏"
（仲裁、恢复、探测）。这两种复杂度的方向完全不同，
也解释了为什么 SPI 有 `__spi_validate()` 这么重的校验层而 i2c 没有。

---

## 相关章节

| 想知道 | 去哪 |
|-------|------|
| 这些函数的签名和返回值语义 | `01-api.md` |
| 字段表和结构关系图 | `02-datastructures.md` |
| `spi-mem` / `spidev` / `spi-bitbang` / `spi-mux` 内部 | `04-extensions.md` |
| 照着写一个控制器驱动 / 协议驱动 | `05-usage.md` |
| DT 属性怎么写、症状怎么查 | `06-devicetree.md` |
| 怎么开 trace、看哪个计数 | `07-debug.md` |
| 大白话版的「为什么这样设计」 | `08-feynman.md` |
| 行号取证和未验证项 | `99-evidence.md` |
