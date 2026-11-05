---
title: 数据结构
---

# I2C 子系统（三）主要数据结构与关系图

> 字段表按源码顺序列出，行号为定义位置。省略了纯注释行和条件编译括号。
> 本篇的核心是 §1 的关系图 —— 先看图，再查表。
> **如果你是第一次读，先看 §1.0**：那里把「`i2c_adapter` 和 `i2c_algorithm` 是不是 device/driver 关系」、
> 「i2c 总线和 platform 总线什么关系」这两个最容易搞混的问题讲清楚了，后面的图才看得顺。

---

## 1. 结构体关系全图

### 1.0 先分清三组关系（最容易搞混的地方）

在看下面的大图之前，先把三组关系钉死，否则图会越看越乱。

#### （1）`i2c_adapter` ↔ `i2c_algorithm`：容器 ↔ 操作表，**不是** device ↔ driver

`i2c_algorithm` 就是一张纯函数指针表，性质等同于 `file_operations`、`net_device_ops`：控制器驱动定义一个 `static const struct i2c_algorithm`，把它的地址填进 `adap->algo`，核心调 `adap->algo->master_xfer()` 就完事。它

- **从不注册到设备模型**，没有 `struct device`、没有 `struct device_driver`；
- 没有名字、没有 `of_match_table`、没有 `probe`，**不参与任何匹配**；
- 一份实例可以被同一个驱动的 N 个适配器共用（`i2c-rk3x.c:1261`，所有 rk3x 控制器都指向同一个 `rk3x_i2c_algorithm`）。

所以它俩的关系是"结构体持有一张 ops 表"，跟总线的 device/driver 模型没关系。

#### （2）i2c 总线上真正的 device ↔ driver 对是 `i2c_client` ↔ `i2c_driver`

| | `i2c_client` | `i2c_adapter` | `i2c_algorithm` |
|---|---|---|---|
| 角色 | 从设备（挂在总线上的芯片） | 控制器/总线段 | 控制器的操作表 |
| 内嵌 `struct device`？ | 是 | 是 | **不是** |
| 注册到哪条总线 | `i2c_bus_type` | `i2c_bus_type` | 不注册 |
| `dev.type` | `i2c_client_type`（`:708`） | `i2c_adapter_type`（`:1378`） | — |
| 会绑定 driver 吗 | **会**，绑 `i2c_driver` | **不会**，核心刻意保证 | — |
| sysfs 路径 | `/sys/bus/i2c/devices/3-001b` | `/sys/bus/i2c/devices/i2c-3` | 不可见 |

`i2c_adapter` 自己**也是** i2c 总线上的一个 `struct device`（`adap->dev.bus = &i2c_bus_type`，`i2c-core-base.c:1554`），但它的 `dev.type` 被设成 `&i2c_adapter_type`（`:1555`），而核心的两个回调开头都用 `i2c_verify_client()` 把非 client 挡掉：

```c
struct i2c_client *i2c_verify_client(struct device *dev)      /* :725 */
{
        return (dev->type == &i2c_client_type) ? to_i2c_client(dev) : NULL;
}
```

- `i2c_device_match()`（`:138`）第一行 `client = i2c_verify_client(dev)` → 适配器拿到 NULL；紧接着 `i2c_of_match_device(matches, NULL)`（`i2c-core-of.c:151` `if (!(client && matches)) return NULL`）和 `i2c_match_id(id, NULL)`（`i2c-core-base.c:107` `if (!(id && client)) return NULL`）都早退，`acpi_driver_match_device()` 在 arm64 上也不成立 → 整个函数返回 0，**永不匹配**。
- `i2c_device_probe()`（`:488`）更直白：`if (!client) return 0;`（`:495-496`）。

结论：**适配器节点在 i2c 总线上永远是"只有 device、没有 driver"的空壳。** 它存在的意义只有两个 —— 给 sysfs 一个挂载点（`i2c_adapter_type.groups` 提供 `name` / `new_device` / `delete_device` 三个属性，`i2c_adapter_attrs[]` 在 `:1370-1375`），以及给 client 当 `dev.parent`。真正驱动这块控制器硬件的驱动**不在 i2c 总线上**，见下一条。

#### （3）i2c 总线 ↔ platform 总线：靠 `dev.parent` 一行赋值缝在一起

arm64 上一颗 SoC 片内 I2C 控制器走的是标准 platform 流程：DT 节点 → `platform_device` → 匹配 `platform_driver` → probe → 在 probe 里"顺手"把自己注册成一个 i2c 适配器。以 `i2c-rk3x.c` 为例：

```
        platform 总线                                 i2c 总线
   ══════════════════════                       ══════════════════════

 DT: i2c@ff110000 {
       compatible = "rockchip,rk3399-i2c";
     }
       │
       │ of_platform_populate()
       ▼
 ┌──────────────────────────┐
 │ platform_device          │
 │  dev.bus = platform_bus  │
 │  dev.of_node = i2c@…     │
 │  name: "ff110000.i2c"    │
 └───────────┬──────────────┘
             │ .of_match_table 匹配（i2c-rk3x.c:1404）
             ▼
 ┌──────────────────────────┐
 │ platform_driver          │  ← 真正碰寄存器/时钟/中断的驱动
 │  rk3x_i2c_driver         │    module_platform_driver()（:1409）
 │  .probe = rk3x_i2c_probe │
 └───────────┬──────────────┘
             │ probe 内：填 i2c->adap（:1259-1265）
             │            i2c_add_adapter(&i2c->adap)（:1369）
             │                       ┌───────────────────────────┐
             └──────────────────────►│ i2c_adapter.dev           │
               dev.parent =          │  dev.bus  = i2c_bus_type  │
                 &pdev->dev（:1265） │  dev.type = adapter_type  │
               ↑ 两条总线唯一连接点  │  name: "i2c-3"            │
                                     │  driver: none  (see (2))  │
                                     └─────────────┬─────────────┘
                                                   │ dev.parent
 DT: i2c@ff110000 的子节点                         │
     pmic@1b {                                     │
       compatible = "rockchip,rk808";              │
       reg = <0x1b>;                               │
     }                                             │
       │                                           ▼
       │ of_i2c_register_devices()  ┌───────────────────────────┐
       │ （注意：不是               │ i2c_client.dev            │
       │   of_platform_populate）   │  dev.bus  = i2c_bus_type  │
       └───────────────────────────►│  dev.type = client_type   │
                                    │  name: "3-001b"           │
                                    └─────────────┬─────────────┘
                                                  │ i2c_device_match + probe
                                                  ▼
                                          struct i2c_driver
                                          （rk808 的驱动）
```

sysfs 里就是一条父子链，两条总线各自软链到它：

```
/sys/devices/platform/ff110000.i2c/                    ← platform_device
/sys/devices/platform/ff110000.i2c/i2c-3/              ← i2c_adapter.dev（子）
/sys/devices/platform/ff110000.i2c/i2c-3/3-001b/       ← i2c_client.dev（孙）

/sys/bus/platform/devices/ff110000.i2c   ─┐
/sys/bus/i2c/devices/i2c-3                ├─ 都是软链，指向上面那条链
/sys/bus/i2c/devices/3-001b              ─┘
```

（图里 `adapter_type` / `client_type` 是 `i2c_adapter_type` / `i2c_client_type` 的简写，为了对齐缩短了。）

三个容易踩的点：

1. **控制器节点的 DT 子节点不会变成 `platform_device`。** `of_platform_populate()` 只对 `of_default_bus_match_table[]`（`drivers/of/platform.c:24-32`，只有 `simple-bus` / `simple-mfd` / `isa` / `arm,amba-bus`）里的 compatible 继续往下递归。I2C 控制器节点不在这张表里，扫到它就停手；它的子节点由 `of_i2c_register_devices()` 变成 `i2c_client`。这就是"为什么 DT 里 i2c 节点不用写 `compatible = \"simple-bus\"`"的答案。
2. **两条总线之间没有任何"总线到总线"的注册关系**，唯一的联系就是 `adap->dev.parent = &pdev->dev` 这一行赋值（`i2c-rk3x.c:1265`）。核心不强制检查它，但不设的话适配器会挂到 `/sys/devices/` 根下，而且电源域/runtime PM 的父子关系、`of_node` 继承都会失灵。
3. **`adap->dev.of_node` 要驱动自己设成控制器节点**（`i2c-rk3x.c:1263`），`of_i2c_register_devices()` 靠它去找子节点。

最后补一句：**适配器的父设备不一定在 platform 总线上。** USB-I2C 桥（`i2c-tiny-usb`）的父设备在 USB 总线上，PCI 控制器（`i2c-designware-pci`）在 PCI 总线上，mux 子适配器的父设备就是父适配器的 `dev`（见 §1.3）。"控制器在 platform 总线上"是 **arm64 SoC 片内控制器**这条主线的情况。

### 1.1 主干：从 bus_type 到一次传输

```
                        ┌────────────────────────────┐
                        │  struct bus_type           │  i2c-core-base.c:699
                        │  i2c_bus_type              │
                        │   .name   = "i2c"          │
                        │   .match  = i2c_device_match│
                        │   .probe  = i2c_device_probe│
                        │   .remove = i2c_device_remove│
                        │   .shutdown= i2c_device_shutdown│
                        └───────┬────────┬───────┬───┘
                                │        │       │ 适配器自身的 dev 也注册在
            ┌───────────────────┘        │       │ 这条总线上（:1554），但
            │  注册 driver               │       │ dev.type = i2c_adapter_type
            │                            │       │ （:1555）→ match/probe 一律
            │               注册 device  │       └─► 跳过，**永不绑定 driver**
            │                            │           （为什么见 §1.0）
            │                            └──────────────────────┐
            │                                                   │
┌───────────┴──────────────┐                     ┌──────────────┴─────────────┐
│ struct i2c_driver        │   probe 时绑定       │ struct i2c_client          │
│ include/linux/i2c.h:271  │◄───────────────────► │ include/linux/i2c.h:334    │
│  .probe_new  ────────────┼── 回调              │  addr      ← 7/10 位地址   │
│  .remove                 │                     │  flags     ← I2C_CLIENT_*  │
│  .id_table   ──┐         │                     │  name[20]                  │
│  .detect       │         │                     │  irq                       │
│  .address_list │         │  driver->clients    │  dev (struct device)        │
│  .clients ─────┼─────────┼──── 链表 ──────────►│  detected (list_head)      │
│  .driver ──────┼──┐      │                     │  slave_cb  ← CONFIG_I2C_SLAVE│
└────────────────┼──┼──────┘                     │  adapter ──────┐           │
                 │  │                             └────────────────┼───────────┘
                 │  ▼                                              │
        ┌────────┴──────────────┐                                  │
        │ struct i2c_device_id  │                                  │
        │  name[20]             │   ← MODULE_DEVICE_TABLE(i2c, …)  │
        │  driver_data          │                                  │
        └───────────────────────┘                                  │
                                                                   ▼
              ┌────────────────────────────────────────────────────────────┐
              │ struct i2c_adapter        include/linux/i2c.h:723          │
              │  owner, class                                              │
              │  nr           ← 总线号，来自 i2c_adapter_idr               │
              │  name[48]     ← 人可读名字（必填）                          │
              │  timeout      ← jiffies，默认 HZ                            │
              │  retries      ← 默认 0                                     │
              │  dev (struct device)  ← 命名 "i2c-%d"                      │
              │     dev.bus  = i2c_bus_type                                │
              │     dev.type = i2c_adapter_type → 不绑 driver              │
              │     dev.parent = 控制器的 platform_device（§1.0）          │
              │  bus_lock  : struct rt_mutex   ┐                           │
              │  mux_lock  : struct rt_mutex   ├── 锁，见 03 §B1           │
              │  locked_flags ← I2C_ALF_*      ┘                           │
              │  userspace_clients / _lock  ← sysfs new_device 建的设备     │
              │  addrs_in_instantiation[bitmap]  ← 实例化竞态保护          │
              │  debugfs                                                   │
              │  algo ──────────┐   algo_data ──┐                          │
              │  lock_ops ──────┼───┐           │                          │
              │  quirks ────────┼───┼─┐         │                          │
              │  bus_recovery_info ─┼─┼─┐       │                          │
              │  host_notify_domain │ │ │       │                          │
              │  bus_regulator      │ │ │       │                          │
              └─────────────────────┼─┼─┼───────┼──────────────────────────┘
                                    │ │ │       │
        ┌───────────────────────────┘ │ │       └──► 控制器私有数据
        │                             │ │              （如 struct i2c_algo_bit_data）
        ▼                             │ │
┌───────────────────────────┐         │ │
│ struct i2c_algorithm      │         │ │
│ include/linux/i2c.h:545   │         │ │
│  master_xfer        ──────┼─────────┼─┼──► 控制器驱动实现，真正操作寄存器
│  master_xfer_atomic       │         │ │
│  smbus_xfer               │         │ │
│  smbus_xfer_atomic        │         │ │
│  functionality            │         │ │
│  reg_slave / unreg_slave  │         │ │
└───────────────────────────┘         │ │
                                      │ │
        ┌─────────────────────────────┘ │
        ▼                               │
┌────────────────────────────┐          │
│ struct i2c_lock_operations │          │
│ include/linux/i2c.h:583    │          │
│  lock_bus / trylock_bus /  │          │
│  unlock_bus                │          │
│                            │          │
│ 三个实现：                  │          │
│  i2c_adapter_lock_ops  ────┼── 根适配器（rt_mutex_lock_nested）
│                            │          │      i2c-core-base.c:1434
│  i2c_mux_lock_ops      ────┼── mux-locked 子适配器  i2c-mux.c:271
│  i2c_parent_lock_ops   ────┼── parent-locked 子适配器 i2c-mux.c:277
└────────────────────────────┘          │
                                        │
        ┌───────────────────────────────┘
        ▼
┌────────────────────────────────┐   ┌─────────────────────────────────┐
│ struct i2c_adapter_quirks      │   │ struct i2c_bus_recovery_info    │
│ include/linux/i2c.h:690        │   │ include/linux/i2c.h:644         │
│  flags (I2C_AQ_*)              │   │  recover_bus()                  │
│  max_num_msgs                  │   │  get_scl/set_scl/get_sda/set_sda│
│  max_write_len / max_read_len  │   │  get_bus_free                   │
│  max_comb_1st_msg_len          │   │  prepare_recovery / unprepare_  │
│  max_comb_2nd_msg_len          │   │  pinctrl / pins_default / _gpio │
└────────────────────────────────┘   │  scl_gpiod / sda_gpiod          │
                                     └─────────────────────────────────┘
```

### 1.2 传输数据流

```
   客户端           核心                              控制器
   ───────          ────                              ──────
                struct i2c_msg[]  ← uapi 结构，跨内核/用户态
                ┌──────────────┐
                │ addr / flags │  I2C_M_RD, I2C_M_TEN, I2C_M_NOSTART …
                │ len / buf    │
                └──────────────┘
                       │  i2c_transfer / __i2c_transfer
                       ▼
                algo->master_xfer(adap, msgs, num)

                union i2c_smbus_data  ← uapi
                ┌────────────────────────┐
                │ byte                   │
                │ word                   │
                │ block[34]              │  block[0]=长度, +1 for PEC
                └────────────────────────┘
                       │  i2c_smbus_xfer / __i2c_smbus_xfer
                       ├──► algo->smbus_xfer(...)                原生
                       └──► i2c_smbus_xfer_emulated(...)         仿真
                                   └── 构造 i2c_msg[] 再走上面那条路
```

### 1.3 mux 拓扑

```
                          ┌──────────────────────┐
                          │ struct i2c_mux_core  │  include/linux/i2c-mux.h:18
                          │  parent ─────────────┼──► 上游 i2c_adapter
                          │  dev                 │
                          │  mux_locked : 1      │  ← I2C_MUX_LOCKED
                          │  arbitrator : 1      │  ← I2C_MUX_ARBITRATOR
                          │  gate       : 1      │  ← I2C_MUX_GATE
                          │  priv ───────────────┼──► mux 驱动私有（i2c_mux_priv()）
                          │  select() / deselect()│
                          │  num_adapters        │
                          │  max_adapters        │
                          │  adapter[] ──────────┼──┐ 柔性数组
                          └──────────────────────┘  │
                                                    │
             ┌──────────────────────────────────────┴────────────┐
             ▼                                                   ▼
   ┌──────────────────────────┐                       ┌──────────────────────┐
   │ struct i2c_mux_priv      │  i2c-mux.c:32         │ struct i2c_mux_priv  │
   │  adap : struct i2c_adapter│ ← **内嵌**，不是指针  │  （通道 1）           │
   │  algo : struct i2c_algorithm│ ← 运行时动态构造     │                      │
   │  muxc ──────► 回指 mux_core│                      │                      │
   │  chan_id = 0             │                       │  chan_id = 1         │
   └──────────────────────────┘                       └──────────────────────┘
             │  &priv->adap 就是暴露给上层的子适配器
             │  priv->adap.algo_data = priv     （所以 xfer 包装能反查 priv）
             ▼
   /dev/i2c-N，名字 "i2c-<parent_nr>-mux (chan_id <N>)"
```

**关键洞察**：mux 通道的 `i2c_adapter` 是**内嵌在 `i2c_mux_priv` 里的**（`i2c-mux.c:33`），并且它的 `algo` 也是一个内嵌的 `struct i2c_algorithm`（`:34`），由 `i2c_mux_add_adapter()` 在运行时按锁风格填不同的包装函数（`:308-326`）。`algo_data` 反指 `priv`，这样 4 个包装函数只要 `container_of` 一下就能拿到 `muxc` 和 `chan_id`。这是内核里"用内嵌结构做多态"的一个漂亮例子。

### 1.4 slave 路径

```
   对端 master 发起传输
          │
          ▼
   控制器中断处理函数（drivers/i2c/busses/*.c）
          │  解析出事件类型
          ▼
   i2c_slave_event(client, event, &val)      i2c-core-slave.c:85
          │  trace_i2c_slave()
          ▼
   client->slave_cb(client, event, &val)     ← 由 i2c_slave_register 设置
          │
          ├──► i2c_slave_eeprom_slave_cb()   i2c-slave-eeprom.c:45
          └──► i2c_slave_testunit_slave_cb() i2c-slave-testunit.c:88

   事件（enum i2c_slave_event，include/linux/i2c.h:386）：
     I2C_SLAVE_READ_REQUESTED   ← 对端要读第一个字节，回调需填 *val
     I2C_SLAVE_READ_PROCESSED   ← 上一个字节已上总线，填下一个
     I2C_SLAVE_WRITE_REQUESTED  ← 对端要开始写
     I2C_SLAVE_WRITE_RECEIVED   ← 收到一个字节，在 *val 里
     I2C_SLAVE_STOP             ← 事务结束
```

### 1.5 用户态路径

```
   /dev/i2c-3 (major=I2C_MAJOR, minor=3)
          │
          ▼
   struct i2c_dev            i2c-dev.c:42
     list  ← 全局 i2c_dev_list（spinlock 保护）
     adap ──────► struct i2c_adapter
     dev
     cdev
          │  open()
          ▼
   file->private_data = 匿名 struct i2c_client   i2c-dev.c:602（分配）/:610（赋值）
     ├─ 这个 client **从不注册到设备模型**（源码注释 :117-132）
     ├─ name = "i2c-dev %d"
     ├─ addr 由 I2C_SLAVE ioctl 设置
     └─ adapter = i2c_get_adapter(minor)
```

---

## 2. 核心结构体字段表

### 2.1 `struct i2c_adapter`（`include/linux/i2c.h:723-758`）

| 行 | 字段 | 含义 / 谁来填 |
|---|---|---|
| 724 | `struct module *owner` | 控制器驱动填 `THIS_MODULE`，供 `i2c_get_adapter()` 的 `try_module_get` |
| 725 | `unsigned int class` | 允许哪些 `detect` 驱动来扫本总线（`I2C_CLASS_*`）。新代码不建议用。 |
| 726 | `const struct i2c_algorithm *algo` | **必填**，否则注册失败 |
| 727 | `void *algo_data` | 算法私有数据（algo-bit 放 `i2c_algo_bit_data`，mux 放 `i2c_mux_priv`） |
| 730 | `const struct i2c_lock_operations *lock_ops` | 不填则核心装 `i2c_adapter_lock_ops`（`i2c-core-base.c:1533`） |
| 731 | `struct rt_mutex bus_lock` | 总线段锁。**是 rt_mutex，上游 6.1 就如此** |
| 732 | `struct rt_mutex mux_lock` | mux 专用锁，只被 mux-locked 风格使用 |
| 734 | `int timeout` | **jiffies**。核心默认 `HZ`（`:1542`）。是"整次传输含重试"的总预算。 |
| 735 | `int retries` | 遇 `-EAGAIN` 的重试次数，默认 0 |
| 736 | `struct device dev` | 适配器自身的设备对象，名字 `i2c-%d`。`dev.bus = &i2c_bus_type`、`dev.type = &i2c_adapter_type`（`:1554-1555`），`dev.parent` 由控制器驱动指向自己的 `platform_device`。**它永远不会绑定 driver**，原因见 §1.0 |
| 737-739 | `unsigned long locked_flags` + `I2C_ALF_IS_SUSPENDED` / `I2C_ALF_SUSPEND_REPORTED` | suspend 状态位。第二个位保证"已挂起还传输"的警告只打一次 |
| 741 | `int nr` | 总线号 |
| 742 | `char name[48]` | **必填**，否则 `i2c_register_adapter()` 返回 `-EINVAL`（`:1524`） |
| 743 | `struct completion dev_released` | `i2c_del_adapter()` 用它等设备真正释放 |
| 745-746 | `struct mutex userspace_clients_lock` + `struct list_head userspace_clients` | 通过 sysfs `new_device` 创建的设备清单，注销时统一清理 |
| 748 | `struct i2c_bus_recovery_info *bus_recovery_info` | 总线恢复能力 |
| 749 | `const struct i2c_adapter_quirks *quirks` | 缺陷描述 |
| 751 | `struct irq_domain *host_notify_domain` | SMBus Host Notify 的虚拟中断域 |
| 752 | `struct regulator *bus_regulator` | 总线供电（部分平台需要在传输前上电） |
| 754 | `struct dentry *debugfs` | `/sys/kernel/debug/i2c/i2c-N/` |
| 757 | `DECLARE_BITMAP(addrs_in_instantiation, 1 << 7)` | 正在被实例化的地址位图，防止两条路径（如 DT 和 sysfs）同时创建同一地址 |

### 2.2 `struct i2c_client`（`include/linux/i2c.h:334-358`）

| 行 | 字段 | 含义 |
|---|---|---|
| 336-342 | `unsigned short flags` | 见下表 |
| 345 | `unsigned short addr` | 7 位地址存低 7 位；10 位模式用 10 位 |
| 348 | `char name[I2C_NAME_SIZE]` | `I2C_NAME_SIZE` = 20（`include/linux/mod_devicetable.h:461`）。用于 `id_table` 匹配；只在没有 OF/ACPI modalias 时才作为 modalias 来源（见 §4） |
| 349 | `struct i2c_adapter *adapter` | 所属总线 |
| 350 | `struct device dev` | 设备对象，名字 `%d-%04x` |
| 351 | `int init_irq` | probe 之前的 irq 快照 |
| 352 | `int irq` | 实际中断号 |
| 353 | `struct list_head detected` | 挂在 `driver->clients` 上 |
| 355 | `i2c_slave_cb_t slave_cb` | slave 回调（`CONFIG_I2C_SLAVE`） |
| 357 | `void *devres_group_id` | probe 时开的 devres group，失败时整组回滚 |

`I2C_CLIENT_*` 标志：

| 标志 | 值 | 行 | 含义 |
|---|---|---|---|
| `I2C_CLIENT_PEC` | 0x04 | 336 | 对该设备的 SMBus 事务使用 PEC |
| `I2C_CLIENT_TEN` | 0x10 | 337 | 10 位地址 |
| `I2C_CLIENT_SLAVE` | 0x20 | 339 | 这是本机要扮演的从设备地址 |
| `I2C_CLIENT_HOST_NOTIFY` | 0x40 | 340 | 该设备通过 Host Notify 报中断 |
| `I2C_CLIENT_WAKE` | 0x80 | 341 | 该设备是唤醒源 |
| `I2C_CLIENT_SCCB` | 0x9000 | 342 | OmniVision SCCB 协议变体（= `I2C_M_IGNORE_NAK \| I2C_M_STOP` 的复用位） |

### 2.3 `struct i2c_driver`（`include/linux/i2c.h:271-310`）

见 [`01-api.md`](01-api.md) §6.1 的完整表格，不重复。

### 2.4 `struct i2c_algorithm`（`include/linux/i2c.h:545-573`）

见 [`01-api.md`](01-api.md) §4.1。补一点文档注释（`:518-544`）里的要求：`master_xfer` **返回成功处理的消息数**，出错返回负值，且错误码要遵守 `Documentation/i2c/fault-codes.rst`。

### 2.5 `struct i2c_timings`（`include/linux/i2c.h:602-611`）

| 字段 | 含义 |
|---|---|
| `bus_freq_hz` | 目标 SCL 频率 |
| `scl_rise_ns` / `scl_fall_ns` | SCL 上升/下降时间 |
| `scl_int_delay_ns` | 控制器内部延迟 |
| `sda_fall_ns` / `sda_hold_ns` | SDA 下降时间 / 保持时间 |
| `digital_filter_width_ns` | 数字滤波宽度 |
| `analog_filter_cutoff_freq_hz` | 模拟滤波截止频率 |

由 `i2c_parse_fw_timings()`（`i2c-core-base.c:1952`）从设备树填充。**注意这个函数是控制器驱动自己调的，核心不调** —— 驱动不调用，这些字段就一直是 0。逐字段的默认值见 [`06-devicetree.md`](06-devicetree.md) §2.3。控制器驱动拿它算分频系数。

### 2.6 `struct i2c_bus_recovery_info`（`include/linux/i2c.h:644-662`）

| 字段 | 含义 |
|---|---|
| `recover_bus(adap)` | 恢复算法。通常填 `i2c_generic_scl_recovery` |
| `get_scl` / `set_scl` / `get_sda` / `set_sda` | 直接操作引脚。`set_scl` 是通用算法的必需项 |
| `get_bus_free` | 判断总线是否空闲（可选，缺省用 `get_sda`） |
| `prepare_recovery` / `unprepare_recovery` | 恢复前后钩子（如把引脚从 I²C 功能切到 GPIO） |
| `pinctrl` / `pins_default` / `pins_gpio` | pinctrl 方式的引脚切换。`i2c_gpio_init_pinctrl_recovery()`（`i2c-core-base.c:303`）会自动查找名为 `default` 和 `gpio`/`recovery` 的 state |
| `scl_gpiod` / `sda_gpiod` | GPIO descriptor 方式。`i2c_gpio_init_generic_recovery()`（`:348`）自动从 `scl-gpios`/`sda-gpios` 取 |

`i2c_init_recovery()`（`:422`）在适配器注册时被调用，负责把上面这些补齐、校验一致性，不合规就把 `bus_recovery_info` 置回 NULL 并告警。

### 2.7 `struct i2c_board_info`（`include/linux/i2c.h:428-440`）

| 字段 | 含义 |
|---|---|
| `type[I2C_NAME_SIZE]` | 会成为 `client->name`，用于匹配 |
| `flags` | `I2C_CLIENT_*` |
| `addr` | 地址 |
| `dev_name` | 覆盖默认的 `%d-%04x` 设备名 |
| `platform_data` | 传给驱动的板级数据 |
| `of_node` / `fwnode` / `swnode` | 固件节点 |
| `resources` / `num_resources` | 资源数组（主要为了 irq） |
| `irq` | 中断号 |

### 2.8 `struct i2c_device_identity`（`include/linux/i2c.h:200`）

`i2c_get_device_id()` 的输出：`manufacturer_id`、`part_id`、`die_revision`。`I2C_DEVICE_ID_*` 常量在 `:202-216` 列了已知厂商 ID。

### 2.9 mux：`struct i2c_mux_core`（`include/linux/i2c-mux.h:18-33`）与 `struct i2c_mux_priv`（`i2c-mux.c:32-37`）

见 §1.3 的图。三个标志位（`include/linux/i2c-mux.h:42-44`）：

| 标志 | 值 | 语义 |
|---|---|---|
| `I2C_MUX_LOCKED` | `BIT(0)` | 用 mux-locked 锁风格（否则 parent-locked） |
| `I2C_MUX_ARBITRATOR` | `BIT(1)` | 这是总线仲裁器（DT 子节点名 `i2c-arb`） |
| `I2C_MUX_GATE` | `BIT(2)` | 这是总线门（DT 子节点名 `i2c-gate`） |

### 2.10 三种算法的低层结构

**`struct i2c_algo_bit_data`**（`include/linux/i2c-algo-bit.h:21-37`）—— GPIO 位操作：

| 字段 | 含义 |
|---|---|
| `data` | 低层私有数据 |
| `setsda` / `setscl` | 拉低/释放引脚（**必需**） |
| `getsda` / `getscl` | 读引脚。`getscl == NULL` 时核心自动打上 `I2C_AQ_NO_CLK_STRETCH` 并告警"Not I2C compliant"（`algos/i2c-algo-bit.c:662,674-677`） |
| `pre_xfer` / `post_xfer` | 传输前后钩子 |
| `udelay` | 半个时钟周期，微秒。注释给出下限：fast 模式 ≥2 µs，standard/SMBus ≥5 µs，SMBus 上限 50 µs |
| `timeout` | jiffies |
| `can_do_atomic` | 声明回调不睡眠，可用于原子传输。**未声明但被调用时只是 `dev_warn`，仍会执行**（`:617-618`） |

**`struct i2c_algo_pca_data`**（`include/linux/i2c-algo-pca.h:70-82`）—— PCA9564/PCA9665：

| 字段 | 含义 |
|---|---|
| `data` | 私有 |
| `write_byte(data, reg, val)` / `read_byte(data, reg)` | 访问 PCA 寄存器（`I2C_PCA_STA/DAT/ADR/CON`） |
| `wait_for_completion(data)` | 等中断 |
| `reset_chip(data)` | 复位 |
| `i2c_clock` | PCA9564 必须用预定义档位（330k/288k/217k/146k/88k/59k/44k/36k）；PCA9665 可任意 |
| `chip` | `I2C_PCA_CHIP_9564` / `_9665` |
| `bus_settings` | `struct pca_i2c_bus_settings`（`:63`）：`mode`/`tlow`/`thi`/`clock_freq` |

**`struct i2c_algo_pcf_data`**（`include/linux/i2c-algo-pcf.h:17-33`）—— PCF8584：

| 字段 | 含义 |
|---|---|
| `setpcf` / `getpcf` | 寄存器访问 |
| `getown` | 本机地址 |
| `getclock` | 时钟设置 |
| `waitforpin` | 等 PIN 位 |
| `xfer_begin` / `xfer_end` | 传输前后钩子 |
| `lab_mdelay` | 多主机丢失仲裁后的退避毫秒数（单主机总线填 0） |

### 2.11 uapi 结构

**`struct i2c_msg`**（`include/uapi/linux/i2c.h:73-87`）—— 见 [`01-api.md`](01-api.md) §1.2。

**`union i2c_smbus_data`**（`include/uapi/linux/i2c.h:141-146`）：

```c
union i2c_smbus_data {
    __u8 byte;
    __u16 word;
    __u8 block[I2C_SMBUS_BLOCK_MAX + 2];  /* 34：block[0]=长度，尾部给 PEC 留位 */
};
```

**SMBus 事务类型**（`:154-162`）：

| 常量 | 值 |
|---|---|
| `I2C_SMBUS_QUICK` | 0 |
| `I2C_SMBUS_BYTE` | 1 |
| `I2C_SMBUS_BYTE_DATA` | 2 |
| `I2C_SMBUS_WORD_DATA` | 3 |
| `I2C_SMBUS_PROC_CALL` | 4 |
| `I2C_SMBUS_BLOCK_DATA` | 5 |
| `I2C_SMBUS_I2C_BLOCK_BROKEN` | 6 |
| `I2C_SMBUS_BLOCK_PROC_CALL` | 7 |
| `I2C_SMBUS_I2C_BLOCK_DATA` | 8 |

方向：`I2C_SMBUS_READ` = 1，`I2C_SMBUS_WRITE` = 0（`:149-150`）—— **注意这和 `I2C_M_RD`（读 = 1）方向一致，但和总线上的读写位含义不同，别混。**

**`struct i2c_smbus_ioctl_data`**（`include/uapi/linux/i2c-dev.h:42-47`）与 **`struct i2c_rdwr_ioctl_data`**（`:50-53`）—— 用户态 ioctl 参数，见 [`05-usage.md`](05-usage.md) §6。

### 2.12 内部结构（不对外）

| 结构 | 位置 | 用途 |
|---|---|---|
| `struct i2c_devinfo` | `drivers/i2c/i2c-core.h:9-13` | 旧式板级信息条目，挂在 `__i2c_board_list` 上 |
| `struct i2c_dev` | `drivers/i2c/i2c-dev.c:42-47` | 一个 `/dev/i2c-N` 节点 |
| `struct i2c_smbus_alert` | `drivers/i2c/i2c-smbus.c:20-23` | ARA 设备私有 |
| `struct alert_data` | `drivers/i2c/i2c-smbus.c:25-29` | 遍历时传递的 alert 信息 |
| `struct eeprom_data` | `drivers/i2c/i2c-slave-eeprom.c:29-38` | slave EEPROM 后端的内存映像 |
| `struct testunit_data` | `drivers/i2c/i2c-slave-testunit.c:38-44` | testunit 后端寄存器 |

---

## 3. 全局变量与锁：一张表看清并发边界

| 对象 | 位置 | 保护什么 / 被什么保护 |
|---|---|---|
| `static DEFINE_MUTEX(core_lock)` | `i2c-core-base.c:62` | 保护 `i2c_adapter_idr`；并且**序列化 detect 与 adapter 删除**（源码注释 `:58-61`）。`i2c_for_each_dev()`、`i2c_get_adapter()`、适配器注册末尾遍历驱动时都持它。 |
| `static DEFINE_IDR(i2c_adapter_idr)` | `i2c-core-base.c:63` | 总线号 → 适配器映射 |
| `DECLARE_RWSEM(__i2c_board_lock)` | `i2c-boardinfo.c:19` | 保护 `__i2c_board_list` |
| `LIST_HEAD(__i2c_board_list)` | `i2c-boardinfo.c:22` | 旧式板级设备表 |
| `int __i2c_first_dynamic_bus_num` | `i2c-boardinfo.c:25` | 动态分配总线号的起点。`i2c_register_board_info()` 和 `i2c_init()` 里的 `of_alias_get_highest_id("i2c")` 会把它顶高。 |
| `DEFINE_STATIC_KEY_FALSE(i2c_trace_msg_key)` | `i2c-core-base.c:67` | tracepoint 零开销门控 |
| `static bool is_registered` | `i2c-core-base.c:68` | `i2c_bus_type` 是否已注册。太早调 `i2c_add_adapter()` 会 WARN（`:1518`） |
| `static struct dentry *i2c_debugfs_root` | `i2c-core-base.c:70` | `/sys/kernel/debug/i2c` |
| `adap->bus_lock` | `include/linux/i2c.h:731` | 一次事务的互斥。`rt_mutex_lock_nested(..., i2c_adapter_depth(adap))` |
| `adap->mux_lock` | `:732` | mux-locked 风格下保护 select-xfer-deselect 三步 |
| `adap->userspace_clients_lock` | `:745` | sysfs 创建的 client 列表 |
| `adap->addrs_in_instantiation` | `:757` | 位图 + `i2c_lock_addr()`/`i2c_unlock_addr()`（`i2c-core-base.c:926`/`:936`） |
| `DEFINE_SPINLOCK(i2c_dev_list_lock)` | `i2c-dev.c:51` | `/dev` 节点链表 |
| `eeprom->buffer_lock` | `i2c-slave-eeprom.c:31` | slave EEPROM 的内存映像（回调在中断上下文，sysfs 在进程上下文，故用 spinlock + `_irqsave`） |

---

## 4. 命名规则速查

| 对象 | 名字 | 出处 |
|---|---|---|
| 适配器设备 | `i2c-<nr>` | `i2c-core-base.c:1553` |
| client 设备（默认） | `<busnr>-<addr:04x>`，如 `3-0050` | `i2c-core-base.c:892` |
| client 设备（`info->dev_name` 指定） | `i2c-<dev_name>`，**带 `i2c-` 前缀**，不是直接用该名 | `:883` |
| mux 子适配器 | `i2c-<parent_nr>-mux (chan_id <N>)` | `i2c-mux.c:329-330` |
| modalias | 三级回退：先 OF（`of:NnameTtypeCcompatible…`），再 ACPI，都没有才是 `i2c:<client->name>` | `i2c_device_uevent`，`i2c-core-base.c:166-174`。**DT 实例化的 client 拿到的是 OF modalias**，`i2c:` 形式只出现在没有 `of_node` 的 client 上 |
| 字符设备 | `/dev/i2c-<adap->nr>` | `i2c-dev.c:667`（`MKDEV(I2C_MAJOR, adap->nr)`） |
| sysfs mux 链接 | 父上 `channel-<N>`，子上 `mux_device` | `i2c-mux.c:421,425-428` |
| debugfs | `/sys/kernel/debug/i2c/i2c-<nr>/` | `i2c-core-base.c:1562` |
