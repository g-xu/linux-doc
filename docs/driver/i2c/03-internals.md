---
title: 内部实现
---

# I2C 子系统（四）整体架构与实现剖析

> **本篇的组织轴**：先给一张总线生命周期时间线（§0.2），然后
> **Part A** 严格沿着这条时间线走一遍主干，
> **Part B** 收纳"不属于时间线上某一点、而是贯穿全程"的横切机制，
> **Part C** 是回头看的评价。
> 章节号带 `A`/`B`/`C` 前缀，所以引用 `§A5` 就知道它在时间线上，引用 `§B1` 就知道它是横切的。
>
> 每一节都给出关键代码的 `文件:行号`。裸写的 `:N` 一律指 **`drivers/i2c/i2c-core-base.c`**，其它文件都写全名。
> 涉及 PREEMPT_RT 的部分单独放在 §B1.4，并明确区分"上游 6.1 本来就有的"与"RT 补丁引入的"。

---

## 0. 导航

### 0.1 分层与职责边界

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │ 用户态                i2c-tools / libi2c / 自写程序                     │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │ open/read/write/ioctl
 ┌────────────────────────────────┴───────────────────────────────────────┐
 │ 字符设备层    i2c-dev.c —— /dev/i2c-N，匿名 client，compat 层           │
 └────────────────────────────────┬───────────────────────────────────────┘
 ┌────────────────────────────────┴───────────────────────────────────────┐
 │ 芯片驱动层    drivers/rtc、drivers/hwmon、drivers/media…（数千个）       │
 │               通常经 regmap-i2c 间接调用                                │
 └────────────────────────────────┬───────────────────────────────────────┘
 ┌────────────────────────────────┴───────────────────────────────────────┐
 │              ┌──────────────────────────────────────────┐              │
 │  核心层      │ i2c-core-base.c   设备模型 + 传输主干      │              │
 │              │ i2c-core-smbus.c  SMBus 协议 + 仿真        │              │
 │              │ i2c-core-slave.c  从机框架                 │              │
 │              │ i2c-core-of.c     DT 枚举                  │              │
 │              │ i2c-core-acpi.c   （x86 固件路径，不展开） │              │
 │              │ i2c-boardinfo.c   旧式静态枚举             │              │
 │              └──────────────────────────────────────────┘              │
 │  可选层      i2c-mux.c（多路复用）  i2c-smbus.c（Alert/HostNotify/SPD） │
 │  算法层      algos/i2c-algo-{bit,pca,pcf}.c                            │
 └────────────────────────────────┬───────────────────────────────────────┘
 ┌────────────────────────────────┴───────────────────────────────────────┐
 │ 控制器驱动层  drivers/i2c/busses/*.c（133 个），只需实现 i2c_algorithm  │
 └────────────────────────────────┬───────────────────────────────────────┘
                                  │ 寄存器 / GPIO
 ┌────────────────────────────────┴───────────────────────────────────────┐
 │ 硬件                                                                    │
 └────────────────────────────────────────────────────────────────────────┘
```

职责划分的一条重要原则：**核心承担所有"策略"，控制器驱动只承担"机制"**。具体地说，下面这些事控制器驱动**不用**做，核心已经做了：

- 总线锁（`i2c_transfer()` 里加，`master_xfer` 被调用时已持锁）
- 重试（`-EAGAIN` 时按 `adap->retries` 重试）
- 超时（按 `adap->timeout` 判总预算）
- suspend 后拒绝传输
- quirks 合法性检查
- tracepoint
- 地址合法性与占用检查
- SMBus 在纯 I²C 上的仿真

反过来，控制器驱动**必须**做的只有：正确操作硬件、返回成功消息数、以及**按 `fault-codes.rst` 的约定返回错误码**（尤其 `-ENXIO` 表示地址无 ACK、`-EAGAIN` 表示仲裁丢失）。

### 0.2 一条 i2c 总线从无到有再到死

Part A 就是下面这张图的逐节展开。**注意 A3（枚举）和 A4（撮合）的先后**：DT 子设备在任何驱动看到这条总线之前就已经建好 client 了，这个顺序决定了驱动能假设什么。

```
 【A1】 postcore_initcall：i2c_init()
   │      bus_register(&i2c_bus_type)。这一步排在所有控制器驱动之前，
   │      所以后面任何 i2c_add_adapter() 都不会遇到"总线还没注册"。
   ▼
 【A2】 控制器驱动 probe：i2c_add_adapter() → i2c_register_adapter()
   │      分配总线号 → device_register(名字 i2c-N) → debugfs / SMBus Alert /
   │      pm_runtime / i2c_init_recovery()
   │
   ├───► 【A3】 枚举：client 从哪来（就发生在 A2 内部，不是之后）
   │        :1588  of_i2c_register_devices()      DT 子节点 → client（arm64 主线）
   │        :1593  i2c_scan_static_board_info()   只对静态编号区的总线
   │        :1596  bus_for_each_drv(...)          给带 detect 的驱动一次扫描机会
   ▼
 【A4】 撮合：match → probe
   │      client 和 i2c_driver 谁后到就由谁触发，两边顺序无关；
   │      probe 里才解析 irq、attach PM domain、开 devres group。
   ▼
 【A5】 稳定运行期：i2c_transfer() → __i2c_transfer() → algo->master_xfer()
   │        【A6】 走 SMBus API 的话在这里分出第二条轨：原生 / 仿真
   │        【A7】 传了但总线卡死 → i2c_recover_bus()（发 9 个 SCL 脉冲）
   │      ↑ 这一层反复循环，直到拆除
   ▼
 【A8】 拆除：driver remove / shutdown，i2c_del_adapter()
```

横切机制（Part B）不在这条线上的任何一点，而是每一点都要用到：**B1 锁**在 A5 的入口加、在 A2 里初始化、在 A6 里被 `__` 版本绕过；**B2 引用计数**贯穿 A2 到 A8；**B3 电源管理**同时挂在 A4 和 A5 上；**B4 可观测性**是 A5 内部的两个门控点。

### 0.3 本篇的三个部分，以及哪些内容不在本篇

| 部分 | 内容 | 什么时候读 |
|---|---|---|
| Part A（A1–A8） | 一条总线从无到有再到死，严格按时间顺序 | 第一次通读，或想知道"这一步之前什么已经就绪了" |
| Part B（B1–B4） | 锁 / 引用计数 / 电源管理 / 可观测性，贯穿全程 | 排查竞态、UAF、suspend 后传输失败、加 trace |
| Part C | 架构评价：好在哪、别扭在哪 | 想理解某处设计为什么这么难用 |

**不在本篇的内容**（避免同一个问题落在两篇文档里）：

| 你想问的 | 去哪 |
|---|---|
| 某个 API 的签名、参数、返回值 | [`01-api.md`](01-api.md) |
| 结构体字段含义、结构体之间的指针关系、i2c 总线和 platform 总线什么关系 | [`02-datastructures.md`](02-datastructures.md) |
| mux / algos / slave / i2c-dev / smbus 扩展 / 枚举后端 这些可选框架自己的内部实现 | [`04-extensions.md`](04-extensions.md) |
| 怎么写一个芯片驱动、怎么用 i2c-dev | [`05-usage.md`](05-usage.md) |
| DT 属性清单、每个属性被哪一行代码消费 | [`06-devicetree.md`](06-devicetree.md) |
| 九个 tracepoint 的清单、输出格式、过滤器与 trace-cmd 用法 | [`07-debug.md`](07-debug.md) §2 |
| 某个行号是怎么核对的、哪些结论未经验证 | [`99-evidence.md`](99-evidence.md) |

---

# Part A　主干：一条 i2c 总线从无到有再到死

## A1. postcore_initcall：`i2c_init()`

`i2c-core-base.c:2087`，注册点 `postcore_initcall(i2c_init)`（`:2152`）。

`postcore_initcall` 这个级别很关键：它排在 `core_initcall` 之后、`arch_initcall`/`subsys_initcall` 之前。意味着 i2c 总线类型在几乎所有控制器驱动和芯片驱动初始化之前就已存在，任何 `i2c_add_adapter()` 都不会遇到"总线还没注册"。核心还专门为此加了防线：`i2c_register_adapter()` 在 `!is_registered` 时 `WARN` 并返回 `-EAGAIN`（`:1518-1521`）。

执行顺序：

```
i2c_init()                                                i2c-core-base.c:2087
 ├─ 1. retval = of_alias_get_highest_id("i2c")                       :2091
 │      if (retval >= __i2c_first_dynamic_bus_num)
 │          __i2c_first_dynamic_bus_num = retval + 1;
 │      ─── 让动态分配的总线号避开 DT 里 aliases 显式写死的编号
 │
 ├─ 2. bus_register(&i2c_bus_type)                                   :2098
 │      ─── 从此刻起 i2c_bus_type 可用
 │
 ├─ 3. is_registered = true;                                         :2102
 │
 ├─ 4. i2c_debugfs_root = debugfs_create_dir("i2c", NULL)            :2104
 │
 ├─ 5. class_compat_register("i2c-adapter")   （CONFIG_I2C_COMPAT）   :2107
 │
 ├─ 6. i2c_add_driver(&dummy_driver)                                 :2113
 │      ─── 注册那个"什么都不做"的 dummy 驱动，用于占地址
 │
 └─ 7. of_reconfig_notifier_register(&i2c_of_notifier)           :2117-2118
        ─── 支持运行时 DT overlay 增删 i2c 设备（见 06 §6）
        （紧随其后 :2119-2120 是 ACPI 的对应注册，x86 路径，不展开）
```

`i2c_exit()`（`:2134`）逆序拆除，末尾额外调 `tracepoint_synchronize_unregister()`（`:2146`）—— 因为 tracepoint 的探针函数在模块里，必须等所有 RCU 读者退出才能卸载（机制见 §B4）。

---

## A2. 适配器注册与总线编号

### A2.1 编号分配的三条路径

```
i2c_add_adapter(adap)                                             :1648
 ├─ 有 dev->of_node？
 │    id = of_alias_get_id(dev->of_node, "i2c")                    :1654
 │    id >= 0  →  adap->nr = id; 走 __i2c_add_numbered_adapter()
 │                （DT 里写 aliases { i2c0 = &i2c0; } 就固定了编号）
 │
 └─ 否则：
      mutex_lock(&core_lock);
      id = idr_alloc(&i2c_adapter_idr, adap,
                     __i2c_first_dynamic_bus_num, 0, GFP_KERNEL);  :1662
      mutex_unlock(&core_lock);
      adap->nr = id;

i2c_add_numbered_adapter(adap)                                    :1697
 ├─ adap->nr == -1  →  退化成 i2c_add_adapter()
 └─ 否则 __i2c_add_numbered_adapter()                              :1621
          idr_alloc(&i2c_adapter_idr, adap, nr, nr+1, GFP_KERNEL)
          ─── 精确占用一个槽位；-ENOSPC 被翻译成 -EBUSY（:1642）
```

`__i2c_first_dynamic_bus_num` 的作用是划分"静态编号区"和"动态编号区"。它被两处顶高：`i2c_register_board_info()`（`i2c-boardinfo.c:58-59`）和 `i2c_init()` 里的 `of_alias_get_highest_id("i2c")`（`i2c-core-base.c:2091`）。这样固定编号的总线永远不会被动态分配抢占。

### A2.2 `i2c_register_adapter()` 的完整时序（`:1513-1612`）

```
i2c_register_adapter(adap)
 │  ── 前置检查 ──
 ├─ WARN if (!is_registered)                        → -EAGAIN     :1518
 ├─ if (!adap->name[0])                             → -EINVAL     :1524
 ├─ if (!adap->algo)                                → -EINVAL     :1527
 │
 │  ── 补默认值 ──
 ├─ if (!adap->lock_ops) adap->lock_ops = &i2c_adapter_lock_ops;  :1533
 ├─ rt_mutex_init(&adap->bus_lock);                               :1536
 ├─ rt_mutex_init(&adap->mux_lock);                               :1537
 ├─ mutex_init(&adap->userspace_clients_lock);                    :1538
 ├─ INIT_LIST_HEAD(&adap->userspace_clients);
 ├─ if (adap->timeout == 0) adap->timeout = HZ;              :1542-1543
 │
 │  ── 设备注册 ──
 ├─ i2c_setup_host_notify_irq_domain(adap)                        :1546
 ├─ dev_set_name(&adap->dev, "i2c-%d", adap->nr);                 :1553
 ├─ adap->dev.bus  = &i2c_bus_type;
 ├─ adap->dev.type = &i2c_adapter_type;
 ├─ device_register(&adap->dev)                                   :1556
 │
 │  ── 附属设施 ──
 ├─ adap->debugfs = debugfs_create_dir(名字, i2c_debugfs_root)    :1562
 ├─ i2c_setup_smbus_alert(adap)                                   :1564
 │     └─ 若 DT 里 interrupt-names 含 "smbus_alert"，
 │        自动创建 ARA 设备（i2c-core-smbus.c:706）
 ├─ pm_runtime_no_callbacks / pm_suspend_ignore_children /
 │  pm_runtime_enable                                        :1568-1571
 ├─ i2c_init_recovery(adap)                                       :1573
 ├─ class_compat_create_link(...)   （CONFIG_I2C_COMPAT）          :1580
 │
 │  ── 枚举子设备（顺序很重要，详见 §A3）──
 ├─ of_i2c_register_devices(adap)                                 :1588
 │      ─── arm64 上唯一起作用的枚举后端（见 06）
 │      （:1589-1590 是 ACPI 的 OpRegion handler 安装与设备枚举，x86 路径）
 ├─ if (adap->nr < __i2c_first_dynamic_bus_num)
 │      i2c_scan_static_board_info(adap)                          :1593
 │
 └─ ── 让已加载的驱动来认领 ──
    mutex_lock(&core_lock);
    bus_for_each_drv(&i2c_bus_type, NULL, adap, __process_new_adapter);
    mutex_unlock(&core_lock);                                :1596-1598
       └─ __process_new_adapter → i2c_do_add_adapter → i2c_detect(adap, driver)
          （即：给每个带 detect 方法的驱动一次扫描本总线的机会）
```

**这个顺序值得注意的地方在于：枚举（`:1588`）发生在"让已加载驱动来认领"（`:1596-1598`）之前，而两者都在 `i2c_init_recovery()`（`:1573`）之后。**换句话说，DT 里描述的每一个子设备在被任何驱动看到之前就已经建好了 client；恢复机制则更早，第一次传输时就已可用。

`adap->dev.parent` 在这之前就已经由控制器驱动填好了（指向它自己的 `platform_device`），这一行赋值是 i2c 总线和 platform 总线唯一的连接点，见 [`02-datastructures.md`](02-datastructures.md) §1.0(3)。

---

## A3. 枚举：client 从哪来（五条途径）

> **本节的边界**：这里只讲"核心用哪些入口把 client 建出来、它们之间怎么防冲突"。
> DT 属性的完整清单和每个属性的消费点在 [`06-devicetree.md`](06-devicetree.md)；
> ACPI 是 x86 固件路径，arm64 用不到，只列一行；
> sysfs `new_device` 和 i2c-dev 的**用法**在 [`05-usage.md`](05-usage.md)，`i2c-dev` 自身的实现在 [`04-extensions.md`](04-extensions.md) §4；
> 三个枚举后端（DT / ACPI / boardinfo）各自的文件级实现细节在 [`04-extensions.md`](04-extensions.md) §6，本节只讲核心侧的入口与防冲突。

```
 ┌─ ① 设备树   of_i2c_register_devices()      i2c-core-of.c:85   arm64 主线
 │     适配器注册时扫子节点；每个有 reg 的节点 → 一个 client
 │     DT 特殊位：I2C_TEN_BIT_ADDRESS / I2C_OWN_SLAVE_ADDRESS（:41,46）
 │     属性 host-notify（:55）、wakeup-source（:58）会翻成 client flags
 │     运行时 overlay：of_i2c_notify()（:163）响应 ADD/REMOVE
 │     完整属性清单与排错见 06-devicetree.md
 │
 ├─ ② ACPI     i2c_acpi_register_devices()    x86 固件路径，本文档集不展开
 │
 ├─ ③ 板级信息 i2c_scan_static_board_info()   i2c-core-base.c:1405
 │     只对 nr < __i2c_first_dynamic_bus_num 的总线生效
 │     数据源：i2c_register_board_info() 填的 __i2c_board_list
 │
 ├─ ④ sysfs    new_device_store()             i2c-core-base.c:1255
 │     echo "<name> <addr>" > /sys/bus/i2c/devices/i2c-3/new_device
 │     addr 可带偏移前缀：0xa000=10位地址、0x1000=slave（:1289-1297）
 │     创建的设备记入 adap->userspace_clients
 │     配对：delete_device_store()（:1323）
 │
 └─ ⑤ 自动探测 i2c_detect()                   i2c-core-base.c:2510
       驱动提供 detect + address_list + class；核心逐地址试探
```

其中 ①③⑤ 都由 `i2c_register_adapter()` 直接调起（`:1588` / `:1593` / `:1596-1598`），所以它们全部发生在 §A4 的撮合之前；只有 ④ 是运行时随时可以来的。

### A3.1 自动探测的细节（`i2c_detect` / `i2c_detect_address` / `i2c_default_probe`）

```
i2c_detect(adapter, driver)                                        :2510
 ├─ 无 detect 或无 address_list → 直接返回
 ├─ adapter->class & driver->class == 0 → 跳过（class 不匹配）      :2530
 ├─ 建一个栈上的临时 client（temp_client）                          :2534
 └─ 对 address_list 里每个地址调 i2c_detect_address()               :2448
        ├─ i2c_check_7bit_addr_validity_strict(addr)  非法则跳过
        ├─ i2c_check_addr_busy()  已被占用则跳过
        ├─ i2c_default_probe(adap, addr)   ← 先看地址上有没有东西
        └─ driver->detect(temp_client, &info)  ← 再让驱动确认是不是它
                └─ 确认后 i2c_new_client_device(adap, &info)
```

`i2c_default_probe()`（`:2420-2446`）里有一处著名的历史包袱，值得一读：

```c
/* :2432-2437 */
if ((addr & ~0x07) == 0x30 || (addr & ~0x0f) == 0x50)
    → 用 BYTE 读探测，而不是 QUICK 写
    /* :2409-2419 的注释解释了原因：QUICK 写会损坏 24RF08 系列
       EEPROM 的内容，还可能不可逆地写保护某些 EEPROM */
```

（同一函数里另有一处 `#ifdef CONFIG_X86` 包裹的主板级黑名单，`:2425-2431`，arm64 上编译时就不存在。）

这段代码是"为什么内核里到处是特例"的绝佳教材：探测这件事本质上是在对未知硬件做未定义操作，只能靠积累的黑名单来避雷。

**现代 arm64 代码不应该再用 detect 机制** —— 它是为没有固件描述的 x86 hwmon 芯片准备的。arm64 上设备由 DT 明确描述，地址是已知的，不需要扫。`I2C_CLASS_DEPRECATED`（`include/linux/i2c.h:864`）就是为了逐步淘汰它而设的告警标志。

### A3.2 地址占用检查

```
i2c_check_addr_busy(adapter, addr)                                 :825
 ├─ i2c_check_mux_parents(adapter, addr)      向上查                :797
 │     递归：父总线上已有这个地址？（因为 mux 打开时地址会冲突）
 └─ i2c_check_mux_children(&adapter->dev, addr)  向下查             :812
       递归所有 mux 子总线
```

**为什么要双向查？** 因为 mux 的语义是"父总线的地址空间被子总线共享"。如果父总线上已有 0x50，子总线上再放一个 0x50，那么当 mux 切到该通道时总线上就有两个 0x50 在应答。

这个检查的粒度和 i2c-dev 的不同：`i2cdev_check()`（`i2c-dev.c:180`）**只在该地址上已经绑定了驱动时才算占用**（源码注释 `:217-219` 解释：用户态应该被允许访问未绑定驱动的地址）。这就是 `i2cget` 有时能访问"已存在但无驱动"的设备的原因。

### A3.3 实例化竞态保护

`i2c_new_client_device()`（`:959-1046`）的关键序列：

```
 ├─ client = kzalloc(...)                                          :966
 ├─ i2c_check_addr_validity(addr, flags)   → -EINVAL               :983
 ├─ i2c_lock_addr(adap, addr, flags)                                :990
 │     └─ :926 —— 在 addrs_in_instantiation 位图上占位，
 │        已被占则立即返回 -EBUSY（test_and_set_bit，不等待）。
 │        防止 DT 枚举和 sysfs new_device 同时建同一地址。
 ├─ i2c_check_addr_busy(adap, i2c_encode_flags_to_addr(client))     :995
 ├─ 填 dev.parent / dev.bus / dev.type / dev.of_node / dev.fwnode :999-1003
 ├─ device_add_software_node()   （若 info->swnode）               :1007
 ├─ device_register(&client->dev)                                 :1018
 └─ 错误路径逐层回滚                                          :1029-1044
```

`device_register()` 这一行就是把控制权交给设备模型的地方 —— 它会同步触发 §A4 的 match 和 probe。

---

## A4. 撮合：`i2c_bus_type` 的 match 与 probe

```c
struct bus_type i2c_bus_type = {          /* i2c-core-base.c:699-705 */
    .name    = "i2c",
    .match   = i2c_device_match,
    .probe   = i2c_device_probe,
    .remove  = i2c_device_remove,
    .shutdown= i2c_device_shutdown,
};
```

后两个回调属于拆除路径，放在 §A8.1。

同时定义了两个 `device_type`：`i2c_client_type`（`:708`）和 `i2c_adapter_type`（`:1378`）。它们让 `i2c_verify_client()`（`:725`）/ `i2c_verify_adapter()`（`:1393`）能在遍历 `i2c` 总线上的设备时区分"这是适配器还是从设备"—— 因为两者都挂在同一条总线上。**适配器因此永不绑定 driver**，为什么见 [`02-datastructures.md`](02-datastructures.md) §1.0。

### A4.1 匹配：`i2c_device_match()`（`:138`）

**匹配顺序（顺序本身很重要）**：

```
i2c_device_match(dev, drv)                                          :138
 ├─ client = i2c_verify_client(dev);  非 client 直接不匹配
 │
 ├─ 1) OF：  i2c_of_match_device(driver->of_match_table, client)     :145
 │            └─ 先 of_match_device()（真有 of_node 时）
 │               再 i2c_of_match_device_sysfs()（sysfs 建的设备没有 of_node，
 │                  就拿 client->name 去和 compatible 字符串比，
 │                  逗号后的部分也比一次）      i2c-core-of.c:116-143
 │
 ├─ 2) ACPI：acpi_driver_match_device(dev, drv)                      :149
 │        （arm64/DT 上恒为不匹配，直接落到 3）
 │
 └─ 3) id_table： i2c_match_id(driver->id_table, client)             :155
                  └─ 逐条 strcmp(client->name, id->name)      :104
```

三条路径任一命中即匹配；**arm64 上实际起作用的是 1 和 3**。这解释了一个常见现象：通过 sysfs `new_device` 手工创建的设备，只写了 `<name> <addr>`，没有 of_node，却依然能匹配到只有 `of_match_table` 的驱动 —— 靠的就是 `i2c_of_match_device_sysfs()` 这条"用名字冒充 compatible"的后门。

`i2c_device_uevent()`（`:161`）生成 modalias，顺序同样是 of → acpi → `i2c:<name>`，用于 udev 自动加载模块。DT 设备走第一条，得到的是 `of:N*T*C<compatible>` 形式；**`i2c:<name>` 这条兜底用的 `name` 只是第一个 compatible 的逗号后半段**（`of_modalias_node()`，见 [`06-devicetree.md`](06-devicetree.md) §3.2）。

### A4.2 probe：`i2c_device_probe()`（`:488-620`）

这是核心里最需要逐行读的函数之一，因为它决定了驱动 probe 时能拿到什么。

```
i2c_device_probe(dev)                                              :488
 │
 ├─ ① 中断号解析（若 client->irq 还没有）                        :503-517
 │     优先级：
 │       a. client->flags & I2C_CLIENT_HOST_NOTIFY
 │            → i2c_smbus_host_notify_to_irq(client)              :472
 │              （从 adapter->host_notify_domain 里映射一个虚拟 irq）
 │       b. dev->of_node                                  ← arm64 走这条
 │            → of_irq_get_byname(node, "irq")，失败再 of_irq_get(node, 0)
 │              即：优先按名字 "irq" 取，没有 interrupt-names 就取第 0 个
 │       c. ACPI_COMPANION(dev)                            （x86，不展开）
 │     -EPROBE_DEFER 会被原样返回（中断控制器还没就绪，稍后重试）
 │
 ├─ ② 匹配确认                                                   :536-541
 │     if (!driver->id_table &&
 │         !acpi_driver_match_device(dev, dev->driver) &&
 │         !i2c_of_match_device(...))
 │             return -ENODEV;
 │     ─── 防止一个既无 id_table 又无固件匹配的驱动被强绑上来
 │
 ├─ ③ 唤醒中断处理                                                :543-563
 │     I2C_CLIENT_WAKE（DT 的 wakeup-source 属性）→ device_init_wakeup +
 │     dev_pm_set_dedicated_wake_irq / dev_pm_set_wake_irq
 │     专用唤醒中断从 of_irq_get_byname(node, "wakeup") 取（:546）
 │
 ├─ ④ of_clk_set_defaults(dev->of_node, false)                    :567
 │
 ├─ ⑤ dev_pm_domain_attach(&client->dev, true)                    :572
 │
 ├─ ⑥ client->devres_group_id = devres_open_group(...)             :576
 │     ─── 之后驱动里所有 devm_* 都进这个组，probe 失败时整组释放
 │
 ├─ ⑦ 调 probe                                                    :588-593
 │     if (driver->probe_new)   status = driver->probe_new(client);
 │     else if (driver->probe)  status = driver->probe(client,
 │                                        i2c_match_id(driver->id_table, client));
 │     else                     status = -EINVAL;
 │
 └─ ⑧ 失败路径：err_release_driver_resources → err_detach_pm_domain
        → err_clear_wakeup_irq → err_wakeup_irq → 逐层回滚      :608-619
```

注意 ⑦：**新代码只应实现 `probe_new`**。旧的 `probe(client, id)` 之所以还留着，是因为有的驱动需要 `id->driver_data`（例如 `i2c-slave-eeprom.c:143` 就是用 `id->driver_data` 编码 EEPROM 容量）。新式做法是在 `probe_new` 里用 `i2c_client_get_device_id()` 或 `i2c_get_match_data()`（`:119`）自取。

⑤⑥ 两步的后续影响分别在 §B3（电源管理）和 §B2（生命周期）。

---

## A5. 一次传输

### A5.1 `i2c_transfer()`（`:2309-2338`）—— 加锁外壳

```c
int i2c_transfer(struct i2c_adapter *adap, struct i2c_msg *msgs, int num)
{
    int ret;

    /* :2313-2328 一段长注释：REVISIT，目前的返回值无法表达
     * "第几条消息失败、传了几个字节"，调用者只能知道总数不对。*/

    ret = __i2c_lock_bus_helper(adap);          /* :2329 */
    if (ret)
        return ret;

    ret = __i2c_transfer(adap, msgs, num);      /* :2333 */
    i2c_unlock_bus(adap, I2C_LOCK_SEGMENT);     /* :2334 */

    return ret;
}
```

`__i2c_lock_bus_helper()`（`i2c-core.h:37`）是理解原子传输的钥匙：

```c
static inline int __i2c_lock_bus_helper(struct i2c_adapter *adap)
{
    int ret = 0;

    if (i2c_in_atomic_xfer_mode()) {
        WARN(!adap->algo->master_xfer_atomic &&
             !adap->algo->smbus_xfer_atomic,
             "No atomic I2C transfer handler for '%s'\n",
             dev_name(&adap->dev));
        ret = i2c_trylock_bus(adap, I2C_LOCK_SEGMENT) ? 0 : -EAGAIN;
    } else {
        i2c_lock_bus(adap, I2C_LOCK_SEGMENT);
    }
    return ret;
}
```

即：**普通上下文阻塞加锁；原子上下文只 trylock，拿不到就 `-EAGAIN`**。同时如果控制器没实现原子回调，会打一次 WARN 但仍继续（尽力而为，因为此时可能正在关机，报错也没人看）。锁本身的层次和 mux 的两种风格见 §B1。

### A5.2 `__i2c_transfer()`（`:2237-2295`）—— 真正的主干

```
__i2c_transfer(adap, msgs, num)
 ├─ if (!adap->algo->master_xfer) return -EOPNOTSUPP;              :2242
 ├─ if (WARN_ON(!msgs || num < 1)) return -EINVAL;                 :2247
 ├─ ret = __i2c_check_suspended(adap); if (ret) return ret;        :2250
 │      └─ i2c-core.h:52：已 suspend 则 -ESHUTDOWN，
 │         并用 I2C_ALF_SUSPEND_REPORTED 保证只警告一次（见 §B3）
 ├─ if (adap->quirks && i2c_check_for_quirks(adap, msgs, num))
 │       return -EOPNOTSUPP;                                       :2254
 │
 ├─ ── tracepoint（static_key 门控，未开启时零开销，见 §B4）── :2262-2269
 │      if (static_branch_unlikely(&i2c_trace_msg_key))
 │          for (i = 0; i < num; i++)
 │              if (msgs[i].flags & I2C_M_RD) trace_i2c_read(...);
 │              else                          trace_i2c_write(...);
 │
 ├─ ── 重试 + 超时循环 ──                                   :2273-2283
 │      orig_jiffies = jiffies;
 │      for (ret = 0, try = 0; try <= adap->retries; try++) {
 │          if (i2c_in_atomic_xfer_mode() && adap->algo->master_xfer_atomic)
 │              ret = adap->algo->master_xfer_atomic(adap, msgs, num);
 │          else
 │              ret = adap->algo->master_xfer(adap, msgs, num);
 │
 │          if (ret != -EAGAIN)                  break;   ← 只有仲裁丢失才重试
 │          if (time_after(jiffies, orig_jiffies + adap->timeout))
 │                                               break;   ← 总预算耗尽
 │      }
 │
 └─ ── tracepoint 收尾 ──                                   :2285-2291
        trace_i2c_reply(...)  每条读消息一次（带回读到的数据）
        trace_i2c_result(adap, num, ret)
```

三个容易误解的点：

1. **`adap->timeout` 不是"单次传输超时"**，而是"重试循环的总时间预算"。单次传输的超时必须由控制器驱动自己实现（通常是 `wait_for_completion_timeout`）。
2. **只有 `-EAGAIN` 触发重试**。所以控制器驱动把仲裁丢失误报成 `-EIO`，会让多主机总线上的重试机制完全失效。
3. **原子模式的判定在循环内部**，每次重试都重新判断。这是有意的：`system_state` 可能在传输过程中变化。

### A5.3 `i2c_in_atomic_xfer_mode()`（`i2c-core.h:31`）

```c
static inline bool i2c_in_atomic_xfer_mode(void)
{
    return system_state > SYSTEM_RUNNING &&
           (IS_ENABLED(CONFIG_PREEMPT_COUNT) ? !preemptible() : irqs_disabled());
}
```

**两个条件必须同时满足**：

- `system_state > SYSTEM_RUNNING` —— 即已进入 `SYSTEM_HALT` / `SYSTEM_POWER_OFF` / `SYSTEM_RESTART` / `SYSTEM_SUSPEND`。这限定了原子传输只在关机、重启、panic 这类"最后时刻"启用。
- 当前确实不可抢占 / 中断已关。

设计意图很明确：原子传输是**给关机路径上"必须写一下 PMIC 才能真正断电"这类场景兜底的**，不是给普通驱动图省事用的。日常驱动不该期望它可用。完整的两条路径对照图在 §B1.3。

---

## A6. 第二条轨：SMBus 的原生 / 仿真

### A6.1 `__i2c_smbus_xfer()`（`i2c-core-smbus.c:553-619`）—— 选路逻辑

```
__i2c_smbus_xfer(adap, addr, flags, read_write, command, protocol, data)
 ├─ ret = __i2c_check_suspended(adap);                              :564
 ├─ trace_smbus_write() / trace_smbus_read()                   :571-574
 ├─ flags &= I2C_M_TEN | I2C_CLIENT_PEC | I2C_CLIENT_SCCB;          :576
 │     ─── 只保留这三个位往下传，其余过滤掉
 │
 ├─ ── 选择原生函数 ──                                        :578-584
 │     if (i2c_in_atomic_xfer_mode()) {
 │         if (adap->algo->smbus_xfer_atomic)  xfer_func = ...atomic;
 │         else if (adap->algo->master_xfer_atomic) xfer_func = NULL; ← 强制仿真
 │     } else {
 │         xfer_func = adap->algo->smbus_xfer;
 │     }
 │
 ├─ ── 原生路径（带重试）──                                   :589-597
 │     if (xfer_func) {
 │         orig_jiffies = jiffies;
 │         for (res = 0, try = 0; try <= adap->retries; try++) {
 │             res = xfer_func(adap, addr, flags, read_write,
 │                             command, protocol, data);
 │             if (res != -EAGAIN) break;
 │             if (time_after(jiffies, orig_jiffies + adap->timeout)) break;
 │         }
 │         if (res != -EOPNOTSUPP || !adap->algo->master_xfer)
 │             goto trace;               ← 成功、或失败且无法仿真，结束
 │         /* :599-608 的注释：原生不支持这个特定事务，退回仿真 */
 │     }
 │
 ├─ ── 仿真路径 ──
 │     res = i2c_smbus_xfer_emulated(adap, addr, flags, read_write,
 │                                   command, protocol, data);       :609
 │
 └─ trace:  trace_smbus_reply() / trace_smbus_result()          :610-615
```

这段"原生优先、`-EOPNOTSUPP` 退回仿真"的逻辑（`:599-608`）是子系统里最实用的兼容性设计：一个只实现了 Byte/Word 的 SMBus 控制器，只要它也有 `master_xfer`，就能自动支持 Block 和 Process Call。

### A6.2 `i2c_smbus_xfer_emulated()`（`:322-520`）—— 协议翻译

核心是一个把 SMBus 事务映射成 1~2 条 `i2c_msg` 的 switch（`:355-454`）：

| protocol | 行 | 消息构造 |
|---|---|---|
| `I2C_SMBUS_QUICK` | 356 | 1 条，`len = 0`，方向位携带信息 |
| `I2C_SMBUS_BYTE` | 363 | 写：1 条 1 字节（command）；读：1 条 1 字节读 |
| `I2C_SMBUS_BYTE_DATA` | 370 | 写：1 条 2 字节；读：2 条（写 cmd + 读 1 字节） |
| `I2C_SMBUS_WORD_DATA` | 378 | 同上但 2 字节数据 |
| `I2C_SMBUS_PROC_CALL` | 387 | 2 条：写 cmd+word，读 word |
| `I2C_SMBUS_BLOCK_DATA` | 395 | 读方向置 `I2C_M_RECV_LEN`（`:397`），长度由从设备给 |
| `I2C_SMBUS_BLOCK_PROC_CALL` | 414 | 2 条，读那条也用 `I2C_M_RECV_LEN` |
| `I2C_SMBUS_I2C_BLOCK_DATA` | 433 | 2 条，长度由调用者给定（无长度字节） |
| default | 451 | `-EOPNOTSUPP` |

之后：

```
 ├─ PEC 处理                                                   :456-467
 │     若 flags & I2C_CLIENT_PEC 且 protocol 适用：
 │       写：i2c_smbus_add_pec(&msg[num-1])   ← 追加 CRC 字节
 │       读：msg[num-1].len++                ← 多读一个字节收 CRC
 ├─ status = __i2c_transfer(adap, msg, num);                        :469
 │     ─── 注意用的是 __ 版本：调用者（i2c_smbus_xfer）已持锁（§B1）
 ├─ if (status != num) → -EIO（若 status >= 0）                     :473
 ├─ 读方向且启用 PEC：i2c_smbus_check_pec()  → -EBADMSG             :479
 ├─ 回读数据 switch                                            :485-511
 │     BLOCK_DATA 时校验 msg[1].buf[0] ≤ 32（仅上界，0 也通过），否则 -EPROTO :506
 └─ cleanup: 释放可能分配的 DMA 安全缓冲                       :513-517
       （i2c_smbus_try_get_dmabuf()，:302）
```

### A6.3 PEC 的实现（`:28-93`）

```c
#define POLY (0x1070U << 3)     /* CRC-8, x^8+x^2+x+1 */
static u8 crc8(u16 data)        /* :29  逐位移位 */
u8 i2c_smbus_pec(u8 crc, u8 *p, size_t count)     /* :49  累加一段缓冲 */
static u8 i2c_smbus_msg_pec(u8 pec, struct i2c_msg *msg)  /* :60
        先把 addr<<1|rd 算进去，再算 buf */
static void i2c_smbus_add_pec(struct i2c_msg *msg)        /* :71 */
static int i2c_smbus_check_pec(u8 cpec, struct i2c_msg *msg)  /* :82 → -EBADMSG */
```

关键点：PEC **覆盖包含地址字节在内的所有字节**，所以 `i2c_smbus_msg_pec()` 第一步就是把 `i2c_8bit_addr_from_msg(msg)` 喂进 CRC。

---

## A7. 出错：总线恢复（bus recovery）

I²C 总线卡死的典型场景：主设备在传输中途被复位（如看门狗重启），从设备正处于"已收到地址、正在输出数据"的状态，一直把 SDA 拉低等时钟。此时总线上没有 START 可发，一切传输都失败。

标准解法：**发 9 个 SCL 脉冲**把从设备的移位寄存器走完，让它释放 SDA，然后补一个 STOP。

### A7.1 `i2c_generic_scl_recovery()`（`:222-291`）

```
i2c_generic_scl_recovery(adap)
 ├─ bri->prepare_recovery(adap)   （若有，通常切 pinctrl 到 GPIO）
 ├─ if (bri->get_scl && !bri->get_scl(adap))   ← SCL 已被别人拉低
 │      ...
 ├─ RECOVERY_CLK_CNT = 9   （:220）
 │  RECOVERY_NDELAY = 5000 ns（:219，即 100 kHz 的半周期）
 │
 ├─ for (i = 0; i < RECOVERY_CLK_CNT * 2; i++) {
 │      /* 交替产生下降沿和上升沿 */
 │      bri->set_scl(adap, scl);
 │      ndelay(RECOVERY_NDELAY);
 │      /* 在合适的相位插入 STOP：SCL 高时把 SDA 从低拉高 */
 │      ...
 │      if (bus_free)  break;      ← get_bus_free / get_sda 判定
 │  }
 ├─ if (SCL 仍被拉低) ret = -EBUSY;                                 :254
 └─ bri->unprepare_recovery(adap)
```

### A7.2 `i2c_init_recovery()`（`:422`）的两种引脚来源

适配器注册时被调用（`:1573`，见 §A2.2），负责把 `bus_recovery_info` 补齐：

- **pinctrl 方式**：`i2c_gpio_init_pinctrl_recovery()`（`:303`）。查找 pinctrl 的 `default` 与 `gpio`（或 `recovery`）两个 state，恢复时切到 GPIO state 直接操作引脚。
- **GPIO descriptor 方式**：`i2c_gpio_init_generic_recovery()`（`:348`）。从 DT 的 `scl-gpios` / `sda-gpios` 拿 `gpio_desc`，装上 `get_scl_gpio_value` / `set_scl_gpio_value` / `get_sda_gpio_value` / `set_sda_gpio_value`（`:178-196`）以及 `i2c_generic_bus_free`（`:198`）。

校验不通过（例如声明了 `recover_bus = i2c_generic_scl_recovery` 却没有 `set_scl`）时，`i2c_init_recovery()` 会打印错误并把 `adap->bus_recovery_info` 置回 NULL —— 即**静默降级为"不支持恢复"**，之后 `i2c_recover_bus()` 返回 `-EBUSY`。这是排查"恢复没生效"时第一个要看的地方（开 `CONFIG_I2C_DEBUG_CORE` 能看到那条错误）。

---

## A8. 拆除

### A8.1 client 侧：remove 与 shutdown

`i2c_device_remove()`（`:622`）：调 `driver->remove()` → 关闭 devres group → 解除 pm domain → 清理 wake irq。**顺序恰好是 §A4.2 里 ③⑤⑥ 的逆序。**

`i2c_device_shutdown()`（`:646`）：只调 `driver->shutdown()`。这条路径经常发生在 `system_state > SYSTEM_RUNNING` 的时刻，所以关机时要写 I²C 寄存器的驱动必须依赖原子传输路径（见 §B1.3）。

### A8.2 适配器侧：`i2c_del_adapter()`（`:1752-1826`）

```
i2c_del_adapter(adap)
 ├─ ① 校验：idr_find(&i2c_adapter_idr, adap->nr) == adap ?          :1759
 │      不是（说明从没注册成功）→ pr_debug 后直接返回
 ├─ ② 通知所有驱动移除本总线上的设备
 │      bus_for_each_drv(..., i2c_do_del_adapter)                   :1706
 ├─ ③ 清理 sysfs 手工创建的 client                              :1774-1783
 │      持 userspace_clients_lock，list_for_each_entry_safe +
 │      i2c_unregister_device
 ├─ ④ 两遍扫描子设备                                           :1790-1791
 │      device_for_each_child(..., __unregister_client)   ← 跳过 "dummy"
 │      device_for_each_child(..., __unregister_dummy)    ← 再收 dummy
 │      ─── 为什么两遍？因为真实驱动的 remove 可能要访问它自己创建的
 │          dummy（如 i2c_new_ancillary_device 建的第二地址），
 │          必须保证 dummy 后死。
 ├─ ⑤ pm_runtime_disable                                           :1801
 ├─ ⑥ i2c_host_notify_irq_teardown                                 :1803
 ├─ ⑦ debugfs_remove_recursive                                     :1805
 ├─ ⑧ init_completion(&adap->dev_released);
 │     device_unregister(&adap->dev);
 │     wait_for_completion(&adap->dev_released);              :1814-1816
 │     ─── 源码在 :1809-1813 有一段 FIXME：这里的等待其实是在
 │         等最后一个引用释放，理论上不该由总线核心来兜底
 ├─ ⑨ mutex_lock(&core_lock); idr_remove(...); mutex_unlock();      :1820
 └─ ⑩ memset(&adap->dev, 0, sizeof(adap->dev));                    :1825
       ─── 允许调用者重新注册同一个 adapter 结构体
```

⑧ 这个"用完成量等引用计数"的做法为什么是设计债，见 §B2 和 §C。

---

# Part B　横切机制：不属于时间线上的某一点

## B1. 并发与锁

### B1.1 三层锁

| 锁 | 保护范围 | 类型 |
|---|---|---|
| `core_lock` | `i2c_adapter_idr`；序列化 detect 与 adapter 删除 | `struct mutex` |
| `adap->bus_lock` | 一次事务的原子性（多条消息之间不被打断） | `struct rt_mutex` |
| `adap->mux_lock` | mux-locked 风格下 select→xfer→deselect 三步的原子性 | `struct rt_mutex` |
| `adap->userspace_clients_lock` | sysfs 建的 client 链表 | `struct mutex` |
| `addrs_in_instantiation` 位图 | 同一地址不被两条枚举路径同时实例化（§A3.3） | 位操作 + 自旋等待 |

前四把都在 `i2c_register_adapter()` 里初始化（`:1533-1538`，见 §A2.2）。

### B1.2 `lock_ops` 间接层与嵌套深度

所有加锁都不直接操作 `bus_lock`，而是走 `adap->lock_ops`（`include/linux/i2c.h:796/810/822`）。这是为 mux 留的扩展点，三个实现：

```
                    lock_bus(adap, flags) 的三种行为
 ┌──────────────────────────────────────────────────────────────────┐
 │ i2c_adapter_lock_ops        i2c-core-base.c:1434                 │
 │   根（物理）适配器：                                              │
 │     rt_mutex_lock_nested(&adap->bus_lock, i2c_adapter_depth(adap))│
 │   忽略 flags —— 对根适配器而言 ROOT 和 SEGMENT 是同一件事      │
 ├──────────────────────────────────────────────────────────────────┤
 │ i2c_mux_lock_ops            i2c-mux.c:271（mux-locked）           │
 │   rt_mutex_lock_nested(&parent->mux_lock, depth);          :148   │
 │   if (flags & I2C_LOCK_ROOT_ADAPTER)                             │
 │       i2c_lock_bus(parent, flags);                      :149-151  │
 │   默认只锁 mux_lock，不锁父总线 → 别人的传输可以插进来          │
 ├──────────────────────────────────────────────────────────────────┤
 │ i2c_parent_lock_ops         i2c-mux.c:277（parent-locked）        │
 │   rt_mutex_lock_nested(&parent->mux_lock, depth);          :179   │
 │   i2c_lock_bus(parent, flags);       ← 总是锁父总线              │
 │   整条物理总线在 select→xfer→deselect 期间被独占               │
 └──────────────────────────────────────────────────────────────────┘
```

`i2c_adapter_depth()`（`i2c-core-base.c:1231`）沿 `i2c_parent_is_i2c_adapter()` 向上数层数，结果作为 `rt_mutex_lock_nested()` 的 lockdep 子类。这是必需的：mux 子适配器的 `bus_lock` 和父适配器的 `bus_lock` 是同一个锁类，嵌套获取会被 lockdep 报成死锁，只有标注不同子类才能通过。层数超过 `MAX_LOCKDEP_SUBCLASSES` 时 `WARN_ONCE`（`:1238`），意味着 mux 嵌套超过 8 层就失去 lockdep 保护。

顺带一个易错点：`i2c_parent_is_i2c_adapter()`（`include/linux/i2c.h:771`）在 `CONFIG_I2C_MUX` **未开启**时恒返回 NULL，此时深度恒为 0。所以关掉 mux 支持会让所有嵌套深度计算退化 —— 这是正确的，因为此时不存在嵌套。

### B1.3 原子传输路径完整图

```
      普通上下文                          关机 / panic 上下文
      ──────────                          ──────────────────
   i2c_transfer()                        i2c_transfer()
        │                                     │
   __i2c_lock_bus_helper()               __i2c_lock_bus_helper()
        │                                     │
   i2c_in_atomic_xfer_mode() == false    i2c_in_atomic_xfer_mode() == true
        │                                     │
   i2c_lock_bus(SEGMENT)                 WARN 若无 *_atomic 回调
     → rt_mutex_lock_nested()            i2c_trylock_bus(SEGMENT)
        │  可能睡眠                          │  失败 → -EAGAIN
        ▼                                     ▼
   __i2c_transfer()                      __i2c_transfer()
        │                                     │
   algo->master_xfer()                   algo->master_xfer_atomic()
        │  可以睡眠、可以用中断              │  绝对不能睡眠
        │  wait_for_completion             │  必须轮询寄存器
        ▼                                     ▼
      硬件                                  硬件
```

对控制器驱动作者的含义：`master_xfer_atomic` 通常是把 `master_xfer` 里的 `wait_for_completion_timeout()` 换成 `readl_poll_timeout_atomic()` 的一份变体。`algo-bit` 的做法更简单：因为位操作本身是纯轮询的，`bit_xfer_atomic()` 直接调 `bit_xfer()`，只在 `can_do_atomic` 未声明时打个 warning（`algos/i2c-algo-bit.c:612-621`）。

判定条件本身在 §A5.3；走到这条路径的时机在 §A8.1（shutdown）。

### B1.4 PREEMPT_RT 相关：哪些是 RT 引入的？

本树打了 PREEMPT_RT（`-rt58`），但**i2c 子系统的源码文件一个都没被 RT 补丁修改**（`git status --porcelain -- drivers/i2c include/linux/i2c.h include/uapi/linux/i2c*.h` 输出为空，见 [`99-evidence.md`](99-evidence.md) §1）。所以：

- **`bus_lock` / `mux_lock` 是 `struct rt_mutex`，这是上游 6.1 主线的写法，不是 RT 补丁改的。** 上游选 rt_mutex 的原因是需要优先级继承：一个低优先级任务持有总线锁时，高优先级任务等锁会被无限期阻塞，rt_mutex 通过 PI 避免这个问题。这在 mainline 也是需要的（例如 RT 内核之外的实时音频路径也可能走 I²C）。
- 在 PREEMPT_RT 内核上，i2c 的行为变化来自**别处**：`spinlock_t` 变成可睡眠锁、中断线程化、`preempt_disable` 语义变化等。对 i2c 的具体影响：
  - `i2c_in_atomic_xfer_mode()` 里的 `!preemptible()` 在 RT 下更少为真（因为大部分临界区变成可抢占），所以原子路径触发得更少 —— 这通常是好事。
  - 控制器驱动里用 `spin_lock_irqsave` 保护的区域在 RT 下变成可睡眠的，如果该区域内调用了真正的原子 API 会出问题 —— 但这是控制器驱动的问题，不是核心的。
  - slave 回调（`i2c_slave_event()`）在 RT 下通常运行在线程化中断上下文，可以睡眠；但**不能依赖这一点写代码**，因为同一份驱动要能在非 RT 内核上跑。
  - `i2c-slave-eeprom.c:31` 用 `spinlock_t buffer_lock` + `spin_lock_irqsave`，在 RT 下这是可睡眠锁；但因为临界区只有一个 `memcpy`，没有实际问题。

---

## B2. 生命周期与引用计数

```
   ┌──────────────────── 适配器 ────────────────────┐
   i2c_add_adapter()
      → idr_alloc（IDR 持一个"逻辑引用"）
      → device_register（kobject refcount = 1）
   ...
   i2c_get_adapter(nr)                          :2602
      → mutex_lock(core_lock)
      → idr_find
      → try_module_get(adapter->owner)     ← 防止控制器模块被卸载
      → get_device(&adapter->dev)          ← 防止对象被释放
      → mutex_unlock
   i2c_put_adapter(adap)                        :2622
      → module_put(adap->owner)
      → put_device(&adap->dev)             ← 源码注释：必须最后，否则 UAF
   ...
   i2c_del_adapter()
      → 逐层清理（见 §A8.2）
      → wait_for_completion(&adap->dev_released)   ← 等真正释放
      → idr_remove
```

```
   ┌──────────────────── client ────────────────────┐
   i2c_new_client_device()
      → kzalloc + device_register
   ...
   probe 时：devres_open_group()
      → 驱动的所有 devm_* 进这个组
   remove 时：devres_release_group()
   ...
   i2c_unregister_device()                      :1053
      → device_unregister
      → i2c_client_dev_release()               :660  → kfree(client)
```

**module_get 的必要性**：假设用户态打开了 `/dev/i2c-3`，此时 `rmmod i2c-imx` 不应该成功 —— 否则 `master_xfer` 的函数指针会指向已卸载的代码。`i2cdev_open()`（`i2c-dev.c:591`）正是用 `i2c_get_adapter(minor)` 来建立这个保护。

---

## B3. 电源管理

| 环节 | 实现 |
|---|---|
| client 的 PM domain | `i2c_device_probe()` 里 `dev_pm_domain_attach(&client->dev, true)`（`:572`，§A4.2 ⑤） |
| 适配器的 runtime PM | `pm_runtime_no_callbacks()` + `pm_suspend_ignore_children(true)` + `pm_runtime_enable()`（`:1568-1571`）—— 适配器自身不参与 runtime PM，只是不阻碍子设备 |
| suspend 后的传输拦截 | 控制器驱动在 suspend 末尾调 `i2c_mark_adapter_suspended()`，核心在每次传输入口 `__i2c_check_suspended()` 检查（`i2c-core.h:52`），返回 `-ESHUTDOWN` |
| 只警告一次 | `I2C_ALF_SUSPEND_REPORTED` 位（`include/linux/i2c.h:739`）—— 否则一个循环重试的驱动会刷屏 |
| 唤醒中断 | `i2c_device_probe()` 的 `dev_pm_set_dedicated_wake_irq()` / `dev_pm_set_wake_irq()`（`:543-563`，§A4.2 ③）；DT 侧的开关是 `wakeup-source` 属性 + `interrupt-names` 里的 `"wakeup"`（见 `06` §3.3） |
| 总线供电 | `adap->bus_regulator`（`include/linux/i2c.h:752`），由控制器驱动在 suspend/resume 里操作 |

---

## B4. 可观测性：tracepoint 的 static key 机制

> 九个 tracepoint 的完整清单、`TP_printk` 输出格式、过滤器与 trace-cmd 用法在 [`07-debug.md`](07-debug.md) §2。
> 本节只回答一个问题：**为什么这些 tracepoint 摆在传输热路径上却是零开销的。**

```c
DEFINE_STATIC_KEY_FALSE(i2c_trace_msg_key);        /* i2c-core-base.c:67 */

int i2c_transfer_trace_reg(void)                  /* :72 */
{   static_branch_inc(&i2c_trace_msg_key);
    return 0;   }

void i2c_transfer_trace_unreg(void)               /* :78 */
{   static_branch_dec(&i2c_trace_msg_key);   }
```

这两个函数被注册为 tracepoint 的 reg/unreg 回调（`TRACE_EVENT_FN` 的第 7、8 个参数，见 `include/trace/events/i2c.h:25`）。效果：

- 没人开 trace 时，`__i2c_transfer()` 里 `:2262` 的 `static_branch_unlikely()` 被**编译成一条 nop**，热路径上完全零开销；
- 一旦有人 `echo 1 > events/i2c/enable`，`i2c_transfer_trace_reg()` 被调用，静态键翻转，那条 nop 被 **运行时 patch 成跳转**到 trace 代码。

代价是卸载时要处理"探针函数在模块里"的问题，所以 `i2c_exit()` 末尾有 `tracepoint_synchronize_unregister()`（`:2146`，见 §A1）。

对照：SMBus 那三个 tracepoint（`smbus_write` / `smbus_read` / `smbus_reply`）走的是 `TRACE_EVENT_CONDITION`（按 `read_write` 方向过滤），第四个 `smbus_result` 是普通 `TRACE_EVENT`。都不是 static key —— 细节见 `07` §2.1。

---

# Part C　架构评价：这个模块好在哪、别扭在哪

**好的部分**

- **`i2c_algorithm` 的抽象边界选得准。** 控制器驱动只需回答"把这几条消息发出去"，不管重试、锁、超时、能力检查。133 个 busses 驱动能保持在平均 670 行，靠的就是这个。
- **quirks 机制** 把"我这个控制器做不到 XX"表达成数据而非代码，核心统一挡掉非法请求，避免了每个驱动各自 `if (num > 2) return -EINVAL;`。
- **SMBus 双向退化** 让能力矩阵的两个极端（只会 SMBus 的 PCH、只会 I²C 的 SoC）都能服务尽可能多的芯片驱动。
- **static key 门控的 tracepoint** 是零成本可观测性的范本。

**别扭的部分（源码自己也承认）**

- **`i2c_transfer()` 的返回值表达力不足**。`i2c-core-base.c:2313-2328` 的 REVISIT 注释写得很直白：调用者拿到 `ret < num` 时无法知道失败位置和已传字节数。真正需要精确错误的场合（如带 CRC 的固件升级协议）只能自己拆成单消息传输。
- **`i2c_del_adapter()` 里的 `wait_for_completion(&adap->dev_released)`** 带 FIXME（`:1809-1813`）：这是在用完成量兜引用计数，本质上是设计债。
- **detect / class 机制**是纯历史包袱，`I2C_CLASS_DEPRECATED` 就是它的墓碑。
- **`struct i2c_msg` 在 uapi 里**，带来了 compat 层（`i2c-dev.c:498-583`）和无法演进结构体的限制。
- **mux 的两种锁风格**在正确性上是真的难用对：`Documentation/i2c/i2c-topology.rst` 用了整整 412 行来列举各种拓扑下的陷阱，而这些陷阱无法被编译器或 lockdep 检查出来。详见 [`04-extensions.md`](04-extensions.md) §1.5。
