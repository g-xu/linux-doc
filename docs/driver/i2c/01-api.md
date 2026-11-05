---
title: API 参考
---

# I2C 子系统（二）对外 API

> 基线 Linux 6.1.158。约定：
> - **每节开头一行 `头文件：…`** 交代这节的 API 声明在哪个 `.h`、实现在哪个 `.c`。表格里不再重复写位置和行号 —— 要看类型和原型，去这一行指的头文件里搜函数名。
> - **表格统一三列**：`API` 只写**函数名 + 形参名**，不带任何类型（返回类型也省掉）；`说明` 一句话讲它做什么；`备注` 放上下文约束、返回值陷阱、参数类型提示等其余信息。
> - **备注列里的上下文标签**：「可睡眠」= 必须在进程上下文调用；「原子可用」= 有专门的原子路径；「不可睡眠」= 明确要求在原子上下文调用。
> - 返回值若无特别说明，均为 `0` 成功 / 负 errno 失败，错误码语义见本文 §9。
> - **§0 是代码地图**：先知道文件和头文件在哪，再往下查具体 API。
> - 实现的逐段剖析（含行号）在 [`03-internals.md`](03-internals.md)，本篇只讲契约。

---

## 0. 代码地图：文件、头文件、编译开关

**查 API 之前先在这里定位文件。**下面四张表回答三个问题：某个功能的实现在哪个 `.c`、某个声明在哪个 `.h`、这块代码由哪个 Kconfig 控制。

### 0.1 核心（`drivers/i2c/`）

| 文件 | 职责 | 本篇里对应的 API | 实现剖析 |
|---|---|---|---|
| `i2c-core-base.c` | 主干：bus_type、设备/驱动注册、编号分配、`i2c_transfer`、探测、恢复、初始化 | §1、§4、§5、§6、§7 | `03` |
| `i2c-core-smbus.c` | SMBus 协议：PEC、13 个 helper、纯 I²C 仿真 | §2 | `03` §A6 |
| `i2c-core-of.c` | 设备树枚举 + `of_i2c_notifier` 动态增删 | §5 的 `of_i2c_get_board_info()` | `04` §6.1、`06` |
| `i2c-core-slave.c` | slave 框架核心（3 个 API + 1 个事件分发） | §8.4 | `04` §3 |
| `i2c-core.h` | 核心**内部**头（不对外），含 `i2c_in_atomic_xfer_mode()` | 不对外，故不在本篇 | `03` §A5.3/§B1 |
| `i2c-mux.c` | 多路复用器框架，两种锁风格 | §8.5 | `04` §1 |
| `i2c-smbus.c` | SMBus 扩展：Alert(ARA)、Host Notify、SPD | §8.7 | `04` §5 |
| `i2c-dev.c` | `/dev/i2c-N` 字符设备 | §10 | `04` §4 |
| `i2c-slave-eeprom.c` | slave 后端：模拟 24Cxx EEPROM | — | `04` §3.3 |
| `i2c-slave-testunit.c` | slave 后端：测试单元，可主动发起传输 | — | `04` §3.4 |
| `i2c-stub.c` | 纯软件虚拟 SMBus 适配器，无硬件测试用 | — | `04` §7 |
| `i2c-boardinfo.c` | 旧式板级信息静态注册表（arm64 不用） | §5 的 `i2c_register_board_info()` | `04` §6.3 |
| `i2c-core-acpi.c` | **x86 固件路径，本文档集不展开** | — | — |

### 0.2 外围目录

| 目录 | 说明 |
|---|---|
| `drivers/i2c/busses/` | 具体控制器驱动，133 个文件。本文档集不逐个展开，只讲它们必须实现的契约（§4.1）。 |
| `drivers/i2c/muxes/` | mux 硬件驱动：`i2c-mux-pca954x`、`i2c-mux-gpio`、`i2c-mux-reg`、`i2c-mux-pinctrl`、`i2c-arb-gpio-challenge`、`i2c-demux-pinctrl`、`i2c-mux-ltc4306`、`i2c-mux-mlxcpld`、`i2c-mux-pca9541`、`i2c-mux-gpmux`。 |
| `drivers/i2c/algos/` | 可复用算法：`i2c-algo-bit`（GPIO 位操作）、`i2c-algo-pca`（PCA9564/9665）、`i2c-algo-pcf`（PCF8584）。 |

### 0.3 头文件：什么声明在哪

| 头文件 | 面向 | 本篇引用它的小节 |
|---|---|---|
| `include/linux/i2c.h` | **内核内一切 i2c 代码的唯一入口** | §1~§8 几乎全部 |
| `include/uapi/linux/i2c.h` | `struct i2c_msg`、`I2C_M_*`、`I2C_FUNC_*`、`union i2c_smbus_data` —— **内核与用户态共享** | §1.2、§3 |
| `include/uapi/linux/i2c-dev.h` | `/dev/i2c-N` 的 ioctl 号与参数结构 | §10 |
| `include/linux/i2c-dev.h` | 只有一件事：`#define I2C_MAJOR 89` | §10 |
| `include/linux/i2c-mux.h` | mux 驱动 | §8.5 |
| `include/linux/i2c-smbus.h` | Alert / Host Notify / SPD | §8.7 |
| `include/linux/i2c-algo-{bit,pca,pcf}.h` | 三种算法的低层回调结构 | §8.6 |
| `include/dt-bindings/i2c/i2c.h` | DTS 里的 `I2C_TEN_BIT_ADDRESS` / `I2C_OWN_SLAVE_ADDRESS` | `06` §3.1 |
| `include/trace/events/{i2c,smbus,i2c_slave}.h` | tracepoint 定义 | `07` §2 |

**`struct i2c_msg` 定义在 uapi 里，这不是随意的历史选择**：`/dev/i2c-N` 的 `I2C_RDWR` ioctl 直接向用户态暴露这个结构，内核内外必须字节对齐一致。代价是它带着 32/64 位兼容层的包袱。

### 0.4 Kconfig：哪块代码能裁掉

| 选项 | 类型 | 效果 |
|---|---|---|
| `CONFIG_I2C` | tristate | 总开关。关掉后整个 `i2c_bus_type` 不存在。 |
| `CONFIG_I2C_BOARDINFO` | bool（被 select） | 编入 `i2c-boardinfo.c`，供旧式板文件用 |
| `CONFIG_I2C_CHARDEV` | tristate | `/dev/i2c-N`（`i2c-dev.c`）。用户态 i2c-tools 依赖它。 |
| `CONFIG_I2C_MUX` | tristate | mux 框架。**注意它还改变了 `i2c_parent_is_i2c_adapter()` 的行为**（未开启时恒返回 NULL），从而影响锁的嵌套深度计算。 |
| `CONFIG_I2C_SMBUS` | tristate | `i2c-smbus.c`：Alert / Host Notify / SPD。**不是** SMBus 基础协议 —— 那部分在 `i2c-core-smbus.c`，随 `CONFIG_I2C` 无条件编入。 |
| `CONFIG_I2C_SLAVE` | bool | slave 框架。开启后 `i2c_client` 多出 `slave_cb` 字段。 |
| `CONFIG_I2C_SLAVE_EEPROM` / `_TESTUNIT` | tristate | 两个 slave 后端 |
| `CONFIG_I2C_STUB` | tristate | 虚拟适配器 |
| `CONFIG_I2C_COMPAT` | bool | 为老用户态保留 `/sys/class/i2c-adapter` 兼容链接 |
| `CONFIG_I2C_HELPER_AUTO` | bool | 让 Kconfig 自动 select 需要的 helper（algos、smbus 等） |
| `CONFIG_I2C_DEBUG_CORE` / `_ALGO` / `_BUS` | bool | 三档调试打印（按目录给 `-DDEBUG`），见 `07` §1 |
| `CONFIG_I2C_ALGOBIT` / `_ALGOPCF` / `_ALGOPCA` | tristate | 三个算法模块 |

**一条容易搞错的**：`CONFIG_I2C_SMBUS` 控制的是 Alert/Host Notify/SPD 这些**扩展机制**，不是 SMBus 协议本身。`i2c_smbus_read_byte_data()` 这类 helper 随 `CONFIG_I2C` 一起编入，永远可用。

---

## 1. 传输 API（客户端驱动最常用）

> 头文件：`include/linux/i2c.h`（`struct i2c_msg` 和 `I2C_M_*` 在 `include/uapi/linux/i2c.h`）；实现：`drivers/i2c/i2c-core-base.c`。

### 1.1 裸 I²C

| API | 说明 | 备注 |
|---|---|---|
| `i2c_transfer(adap, msgs, num)` | 执行 `num` 条消息组成的一次事务，消息之间用 repeated-START 连接。**自己加总线锁**。 | **可睡眠**（原子上下文会走 trylock，失败返回 `-EAGAIN`）；返回值见下 |
| `__i2c_transfer(adap, msgs, num)` | 同上，但**假定调用者已持有总线锁** | 同上。parent-locked mux 的 `select()` 里只能用这个，用 `i2c_transfer()` 会死锁 |
| `i2c_master_send(client, buf, count)` | 写 `count` 字节到 client | inline；**可睡眠**；返回**字节数** |
| `i2c_master_recv(client, buf, count)` | 从 client 读 `count` 字节 | inline；**可睡眠**；返回**字节数** |
| `i2c_master_send_dmasafe(client, buf, count)` / `i2c_master_recv_dmasafe(client, buf, count)` | 同上，但声明 `buf` 是 DMA 安全的（置 `I2C_M_DMA_SAFE`） | inline；**可睡眠**；返回字节数 |
| `i2c_transfer_buffer_flags(client, buf, count, flags)` | 上面 4 个 inline 的共同底座：构造一条消息并发送 | **可睡眠**；返回字节数 |

**返回值语义（重要且易错）**：`i2c_transfer()` 成功时返回**成功传输的消息条数**，不是字节数、也不是 0。所以正确的判断是 `ret != num`（或 `ret < 0`）。而 `i2c_master_send/recv` 返回**字节数**（内部把 `ret == 1` 转成 `count`）。

`i2c_transfer()` 定义处上方有一段很长的 REVISIT 注释，坦白了这个接口的缺陷：当返回值 `< num` 时，调用者无法知道具体是哪一条消息、传了多少字节就失败了。做健壮的驱动时要意识到这一点。

### 1.2 消息构造：`struct i2c_msg` 与 flags

`struct i2c_msg` 定义在 `include/uapi/linux/i2c.h`：

```c
struct i2c_msg {
    __u16 addr;    /* 7 位地址（不含方向位），10 位模式下是 10 位地址 */
    __u16 flags;   /* 下表 */
    __u16 len;     /* buf 字节数 */
    __u8 *buf;
};
```

| flag | 值 | 含义 | 需要的 functionality |
|---|---|---|---|
| `I2C_M_RD` | 0x0001 | 这是一条读消息 | — |
| `I2C_M_TEN` | 0x0010 | 使用 10 位地址 | `I2C_FUNC_10BIT_ADDR` |
| `I2C_M_DMA_SAFE` | 0x0200 | `buf` 位于 DMA 安全内存 | — （只是给控制器的提示） |
| `I2C_M_RECV_LEN` | 0x0400 | 首字节是长度：`buf[0]` 由从设备给出，实际读 `1 + buf[0]`（+PEC） | `I2C_FUNC_SMBUS_READ_BLOCK_DATA` |
| `I2C_M_NO_RD_ACK` | 0x0800 | 读时不发 ACK | `I2C_FUNC_PROTOCOL_MANGLING` |
| `I2C_M_IGNORE_NAK` | 0x1000 | 忽略从设备的 NAK，继续 | `I2C_FUNC_PROTOCOL_MANGLING` |
| `I2C_M_REV_DIR_ADDR` | 0x2000 | 反转方向位（读写位取反） | `I2C_FUNC_PROTOCOL_MANGLING` |
| `I2C_M_NOSTART` | 0x4000 | 本条消息前不发 START/地址 | `I2C_FUNC_NOSTART` |
| `I2C_M_STOP` | 0x8000 | 本条消息后强制发 STOP | `I2C_FUNC_PROTOCOL_MANGLING` |

最典型的两条消息模式：

```c
u8 reg = 0x10, val;
struct i2c_msg msgs[2] = {
    { .addr = client->addr, .flags = 0,        .len = 1, .buf = &reg },
    { .addr = client->addr, .flags = I2C_M_RD, .len = 1, .buf = &val },
};
ret = i2c_transfer(client->adapter, msgs, 2);
if (ret != 2)
    return ret < 0 ? ret : -EIO;
```

---

## 2. SMBus API

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-smbus.c`（`i2c_get_device_id()` 在 `i2c-core-base.c`）。全部**可睡眠**。

| API | 说明（总线上的字节序列） | 备注 |
|---|---|---|
| `i2c_smbus_read_byte(client)` | S addr+R [data] P | 返回 0-255 或负 errno |
| `i2c_smbus_write_byte(client, value)` | S addr+W value P | |
| `i2c_smbus_read_byte_data(client, cmd)` | S addr+W cmd Sr addr+R [data] P | 返回 0-255 或负 errno |
| `i2c_smbus_write_byte_data(client, cmd, value)` | S addr+W cmd value P | |
| `i2c_smbus_read_word_data(client, cmd)` | 同上但 2 字节，**小端** | 返回 0-65535 或负 errno |
| `i2c_smbus_write_word_data(client, cmd, value)` | 小端 | |
| `i2c_smbus_read_word_swapped(client, cmd)` | 大端（内部 `swab16`） | inline；返回 0-65535 或负 errno |
| `i2c_smbus_write_word_swapped(client, cmd, value)` | 大端 | inline |
| `i2c_smbus_read_block_data(client, cmd, values)` | 从设备先回长度字节，再回数据 | 返回实际字节数；长度由从设备决定，见下面的「块读的坑」 |
| `i2c_smbus_write_block_data(client, cmd, len, values)` | 先发长度字节再发数据 | `len` 被截到 32 |
| `i2c_smbus_read_i2c_block_data(client, cmd, len, values)` | **无长度字节**的块读（严格说不是 SMBus，是 I²C 惯例） | 返回实际字节数 |
| `i2c_smbus_write_i2c_block_data(client, cmd, len, values)` | 无长度字节的块写 | |
| `i2c_smbus_read_i2c_block_data_or_emulated(client, cmd, len, values)` | 优先用块读；不支持则退化为逐 word / 逐 byte 循环读 | 返回实际字节数 |
| `i2c_smbus_xfer(adap, addr, flags, read_write, command, protocol, data)` | 通用底座，上面所有 helper 最终都落到它。**自己加锁**。 | `data` 是 `union i2c_smbus_data *`；返回值依 protocol |
| `__i2c_smbus_xfer(adap, addr, flags, read_write, command, protocol, data)` | 同上，但调用者需已持锁 | 同上 |
| `i2c_smbus_pec(crc, p, count)` | 在 `crc` 基础上累加 `count` 字节的 CRC-8 PEC | 返回新的 crc |
| `i2c_get_device_id(client, id)` | 读 SMBus 的 "Device ID" 命令（地址 0x7c）：厂商 ID / 器件 ID / 版本 | `id` 填 `struct i2c_device_identity` |

`I2C_SMBUS_BLOCK_MAX` = 32（`include/uapi/linux/i2c.h`）是块传输的协议上限。`union i2c_smbus_data` 里 `block[34]`，`block[0]` 存长度，多出来的 2 字节是给长度字节和 PEC 留的余量。

**块读的坑**：`i2c_smbus_read_block_data()` 依赖从设备在第一个字节里报长度。若该长度不在 1..32 内，核心返回 `-EPROTO`。很多"块读失败"的问题根源在这里。

**PEC 的开启方式**不是函数参数，而是给 client 打标记：`client->flags |= I2C_CLIENT_PEC`。核心会在仿真路径中自动追加/校验 PEC，失败返回 `-EBADMSG`。

---

## 3. 能力查询与缺陷规避

> 头文件：`include/linux/i2c.h`（三个函数都是 inline）；`I2C_FUNC_*` 位定义在 `include/uapi/linux/i2c.h`。

调用任何非基础操作**之前**都应该先查能力，否则你会在运行时拿到 `-EOPNOTSUPP`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_get_functionality(adap)` | 取适配器的能力位图 | 直接转发 `algo->functionality(adap)`，返回 `I2C_FUNC_*` 位或 |
| `i2c_check_functionality(adap, func)` | 检查是否支持 `func` 里的**全部**能力 | `(get & func) == func` 才为真 |
| `i2c_check_quirks(adap, quirks)` | 检查适配器是否带某个缺陷标志 | 返回 bool |

`I2C_FUNC_*` 位：

| 位 | 值 | 含义 |
|---|---|---|
| `I2C_FUNC_I2C` | 0x00000001 | 支持任意 I²C 消息（即有 `master_xfer`） |
| `I2C_FUNC_10BIT_ADDR` | 0x00000002 | 10 位地址 |
| `I2C_FUNC_PROTOCOL_MANGLING` | 0x00000004 | 支持 `I2C_M_IGNORE_NAK` / `REV_DIR_ADDR` / `NO_RD_ACK` / `STOP` |
| `I2C_FUNC_SMBUS_PEC` | 0x00000008 | PEC |
| `I2C_FUNC_NOSTART` | 0x00000010 | `I2C_M_NOSTART` |
| `I2C_FUNC_SLAVE` | 0x00000020 | 能当从设备（有 `reg_slave`） |
| `I2C_FUNC_SMBUS_QUICK` / `READ_BYTE` / `WRITE_BYTE` / `READ_BYTE_DATA` / `WRITE_BYTE_DATA` / `READ_WORD_DATA` / `WRITE_WORD_DATA` / `PROC_CALL` / `READ_BLOCK_DATA` / `WRITE_BLOCK_DATA` / `READ_I2C_BLOCK` / `WRITE_I2C_BLOCK` | 0x10000… | 逐个 SMBus 事务类型 |
| `I2C_FUNC_SMBUS_HOST_NOTIFY` | 0x10000000 | 支持接收 Host Notify |
| `I2C_FUNC_SMBUS_EMUL` | 组合 | 纯 I²C 控制器能仿真出来的那一组 |
| `I2C_FUNC_SMBUS_EMUL_ALL` | 组合 | 上面再加 PEC / BLOCK_PROC_CALL / I2C_BLOCK |

`struct i2c_adapter_quirks` 描述**控制器做不到的事**，核心在 `i2c_check_for_quirks()` 里替驱动挡掉非法请求，返回 `-EOPNOTSUPP`：

| quirk | 含义 |
|---|---|
| `I2C_AQ_COMB` | 只支持"两条消息组合"这一种多消息形态 |
| `I2C_AQ_COMB_WRITE_FIRST` / `_READ_SECOND` | 组合里第一条必须写 / 第二条必须读 |
| `I2C_AQ_COMB_SAME_ADDR` | 组合的两条消息地址必须相同 |
| `I2C_AQ_COMB_WRITE_THEN_READ` | 上面几条的常用组合宏 |
| `I2C_AQ_NO_CLK_STRETCH` | 不支持时钟拉伸（algo-bit 在无法读 SCL 时会自动打上） |
| `I2C_AQ_NO_ZERO_LEN_READ` / `_WRITE` / `I2C_AQ_NO_ZERO_LEN` | 不支持 0 长度消息 |
| `I2C_AQ_NO_REP_START` | 不支持 repeated START |

`max_read_len` / `max_write_len` / `max_comb_1st_msg_len` / `max_comb_2nd_msg_len` 是长度上限，超了同样 `-EOPNOTSUPP`。

---

## 4. 适配器（控制器驱动）API

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-base.c`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_add_adapter(adap)` | 注册适配器，**自动分配**总线号 | 优先用 DT alias（`aliases { i2c0 = &... }`），否则从 `__i2c_first_dynamic_bus_num` 起动态分配 |
| `i2c_add_numbered_adapter(adap)` | 注册适配器，用调用者填好的 `adap->nr` | `nr == -1` 时等价于 `i2c_add_adapter()`；号被占返回 `-EBUSY` |
| `devm_i2c_add_adapter(dev, adap)` | 上面的 devres 版 | 随 `dev` 自动注销 |
| `i2c_del_adapter(adap)` | 注销适配器 | 无返回值。会先删掉所有子设备，并等待 `dev_released` 完成量 |
| `i2c_get_adapter(nr)` | 按总线号取适配器 | **同时 `try_module_get` + `get_device`**；失败返回 NULL |
| `i2c_put_adapter(adap)` | 与上面配对释放 | 无返回值。源码注释强调 `put_device` 必须最后做，否则有 UAF 风险 |
| `i2c_get_adapter_by_fwnode(fwnode)` | 按 fwnode 查适配器，**带引用计数** | 用完要 `i2c_put_adapter()` |
| `i2c_find_adapter_by_fwnode(fwnode)` | 同上，只查不加引用 | |
| `i2c_adapter_id(adap)` | 取 `adap->nr` | inline |
| `i2c_adapter_depth(adapter)` | 沿 mux 链向上数层数 | 用于 lockdep 子类；超过 `MAX_LOCKDEP_SUBCLASSES` 会 `WARN_ONCE` |
| `i2c_for_each_dev(data, fn)` | 遍历所有 i2c 设备，对每个调 `fn` | 持 `core_lock`；`fn` 原型 `int (*)(struct device *, void *)` |
| `i2c_verify_adapter(dev)` | 判断一个 `struct device` 是不是适配器 | 是则返回 `struct i2c_adapter *`，否则 NULL |
| `i2c_parse_fw_timings(dev, t, use_defaults)` | 从设备树属性读时序参数填 `t` | `t` 是 `struct i2c_timings *`；`use_defaults` 见下 |

`i2c_parse_fw_timings()` 读 8 个属性：`clock-frequency`、`i2c-scl-rising-time-ns`、`i2c-scl-falling-time-ns`、`i2c-scl-internal-delay-ns`、`i2c-sda-falling-time-ns`、`i2c-sda-hold-time-ns`、`i2c-digital-filter-width-ns`、`i2c-analog-filter-cutoff-frequency`。`use_defaults = true` 时按速率档给默认值（≤100 kHz 时 rise/fall = 1000/300 ns，≤400 kHz 时 300/120 ns，更高速 120/120 ns）。

**注意这个函数由控制器驱动自己调用，核心不调**（`i2c-core-base.c` 里只有定义，调用点全在 `drivers/i2c/busses/`）。也就是说：上面这 8 个 DT 属性在不调它的驱动上完全无效。逐属性的默认值和消费者见 [`06-devicetree.md`](06-devicetree.md) §2.3。

### 4.1 适配器必须实现的契约：`struct i2c_algorithm`

> 头文件：`include/linux/i2c.h`（结构体上方有一段完整的文档注释，值得一读）。

| 回调 | 说明 | 备注 |
|---|---|---|
| `master_xfer(adap, msgs, num)` | 把 `num` 条消息真正发到线上 | **I²C 控制器必需**；返回成功消息数或负 errno |
| `master_xfer_atomic(adap, msgs, num)` | 同上的原子版 | 可选；关机/panic 路径用，**不能睡眠** |
| `smbus_xfer(adap, addr, flags, read_write, command, size, data)` | 硬件原生 SMBus 事务 | 可选；返回 `-EOPNOTSUPP` 时核心会退回 I²C 仿真 |
| `smbus_xfer_atomic(adap, addr, flags, read_write, command, size, data)` | 同上的原子版 | 可选 |
| `functionality(adap)` | 报告本控制器支持哪些能力 | 强烈建议实现；返回 `I2C_FUNC_*` 位图 |
| `reg_slave(client)` / `unreg_slave(client)` | 把本控制器切成从机 / 切回来 | slave 模式必需；见 `04` §3 |

至少要有 `master_xfer` 或 `smbus_xfer` 之一；`i2c_register_adapter()` 在 `algo == NULL` 时直接返回 `-EINVAL`。

---

## 5. 设备实例化 API

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-base.c`（`of_i2c_get_board_info()` 在 `i2c-core-of.c`，`i2c_register_board_info()` 在 `i2c-boardinfo.c`）。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_new_client_device(adap, info)` | 在 `adap` 上按 `info` 建一个 client 设备 | **最主要的实例化入口**。会做地址合法性、地址占用检查 |
| `i2c_new_scanned_device(adap, info, addr_list, probe)` | 在候选地址列表里逐个探测，命中即实例化 | `addr_list` 是 `unsigned short` 数组，以 `I2C_CLIENT_END` 结尾 |
| `i2c_probe_func_quick_read(adap, addr)` | 现成的探测函数：向 `addr` 发一个 Quick Read | 可直接传给上一行的 `probe` 参数 |
| `i2c_new_dummy_device(adap, address)` | 建一个"只占地址、不带驱动"的假设备 | 用于一颗芯片占多个地址的情形 |
| `devm_i2c_new_dummy_device(dev, adap, address)` | 上面的 devres 版 | 随 `dev` 自动注销 |
| `i2c_new_ancillary_device(client, name, default_addr)` | 按名字查 DT 里的第二地址，建附属 dummy | 查 `reg-names` 里 `name` 的下标，取对应 `reg`；查不到用 `default_addr` |
| `i2c_unregister_device(client)` | 注销一个 client | 无返回值 |
| `i2c_verify_client(dev)` | 判断一个 `struct device` 是不是 client | 是则返回 `struct i2c_client *`，否则 NULL |
| `i2c_register_board_info(busnum, info, n)` | 旧式静态板级注册 | `__init`，arm64 上不用 |
| `of_i2c_get_board_info(dev, node, info)` | 把一个 DT 节点翻译成 board_info | 字段映射见 [`06-devicetree.md`](06-devicetree.md) §3.1 |

上表所有 `i2c_new_*` / `devm_i2c_new_*` 都返回 `struct i2c_client *`，**失败时是 `ERR_PTR()` 而不是 NULL**，必须用 `IS_ERR()` 判断。
入参 `info` 是 `struct i2c_board_info *`，常用 `I2C_BOARD_INFO(dev_type, dev_addr)` 宏初始化。

### 5.1 查找已存在的设备

> 头文件：`include/linux/i2c.h`；实现：`i2c-core-base.c` / `i2c-core-of.c`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_find_device_by_fwnode(fwnode)` | 按 fwnode 找 client | 返回 `struct i2c_client *`，找不到为 NULL |
| `of_find_i2c_device_by_node(node)` | 按 DT 节点找 client | 内部转成 fwnode 走上一行 |
| `of_find_i2c_adapter_by_node(node)` | 按 DT 节点找适配器 | 返回 `struct i2c_adapter *`，**带引用计数**，用完要 `i2c_put_adapter()` |
| `of_get_i2c_adapter_by_node(node)` | 同上，**带引用计数** | 同上，功能等价，只是另一套接口 |
| `i2c_of_match_device(matches, client)` | 拿 `client` 去匹配一张 `of_device_id` 表 | 返回命中的 `struct of_device_id *`。先试标准 `of_match_device()`，失败再拿 `client->name` 去比 compatible 串（含去掉 `厂商,` 前缀后再比一次）—— 这是为了让 sysfs 手工实例化的、没有 of_node 的设备也能匹配上 |

`include/linux/i2c.h` 末尾另有一组 `i2c_acpi_*` 接口（按 handle 查适配器、查总线速率、新建 ACPI 设备）。**那是 x86 固件路径，arm64 上不用，本文档集不展开** —— `CONFIG_ACPI=n` 时它们全是返回错误的 inline 空壳。

---

## 6. 驱动注册 API

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-base.c`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_register_driver(owner, driver)` | 真正的注册函数 | `owner` 是 `struct module *` |
| `i2c_add_driver(driver)` | 上面的常用封装 | 宏，展开为 `i2c_register_driver(THIS_MODULE, driver)` |
| `i2c_del_driver(driver)` | 注销驱动 | 无返回值 |
| `module_i2c_driver(__i2c_driver)` | 一行生成 `module_init`/`module_exit` | 宏，**绝大多数芯片驱动只需要这个** |
| `builtin_i2c_driver(__i2c_driver)` | 内建（非模块）版 | 宏 |
| `i2c_client_has_driver(client)` | 判断 client 是否已绑定驱动 | inline，返回 bool |
| `i2c_clients_command(adap, cmd, arg)` | 向该总线上所有 client 广播 `driver->command()` | 遗留接口，无返回值 |

### 6.1 `struct i2c_driver` 回调约定

> 头文件：`include/linux/i2c.h`。

| 成员 | 说明 | 备注 |
|---|---|---|
| `probe_new(client)` | 绑定设备 | **新式 probe，新驱动只写这个** |
| `probe(client, id)` | 旧式绑定，多一个"命中了哪条 id_table 表项"的参数 | 注释已标 deprecated；`id` 是 `const struct i2c_device_id *` |
| `remove(client)` | 解绑设备 | 返回 `void`（6.1 已改完，老代码里是 `int`） |
| `shutdown(client)` | 关机路径上被调 | 此时可能已不可睡眠，见 §11 |
| `alert(client, protocol, data)` | SMBus Alert / Host Notify 到达时被调 | `protocol` 是 `enum i2c_alert_protocol` |
| `command(client, cmd, arg)` | 配合上面的 `i2c_clients_command()` | 遗留 |
| `id_table` | `struct i2c_device_id[]`，驱动能认哪些名字 | 匹配用；配 `MODULE_DEVICE_TABLE(i2c, ...)` 才能让模块被自动加载 |
| `detect` + `address_list` | 自动探测（老式 hwmon 风格） | 见 `03` §A3 |
| `class` | 限定只在哪一类总线上做自动探测 | 只在带 `detect` 的驱动里有意义 |
| `clients` | 核心维护的、属于本驱动的 client 链表 | 驱动不要自己动 |
| `flags` | 6.1 里只定义了一个位 | 只对 x86 固件路径有意义；**arm64 驱动填 0** |

`i2c_get_clientdata()` / `i2c_set_clientdata()` 是 `dev_get_drvdata` / `dev_set_drvdata` 的薄封装。

---

## 7. 锁与挂起标记 API

> 头文件：`include/linux/i2c.h`（`i2c_root_adapter()` 在 `include/linux/i2c-mux.h`）；实现：`i2c-core-base.c` / `i2c-mux.c`。

**普通驱动不应该碰这些**。它们是给 mux 驱动、以及需要在一次事务内做多步操作的特殊驱动用的。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_lock_bus(adap, flags)` | 加总线锁 | inline，转发到 `adap->lock_ops->lock_bus`；无返回值 |
| `i2c_trylock_bus(adap, flags)` | 尝试加锁，不阻塞 | inline；抢到返回非 0 |
| `i2c_unlock_bus(adap, flags)` | 解锁 | inline；无返回值 |
| `I2C_LOCK_ROOT_ADAPTER` | 上面三个函数 `flags` 的取值之一：锁整条物理总线（含 mux 之上） | `BIT(0)` |
| `I2C_LOCK_SEGMENT` | 另一个取值：只锁本总线段 | `BIT(1)` |
| `i2c_root_adapter(dev)` | 找到 mux 链顶端的物理适配器 | 无 mux 时就是它自己 |
| `i2c_mark_adapter_suspended(adap)` | 标记适配器已挂起 | inline；置 `I2C_ALF_IS_SUSPENDED`，之后所有传输返回 `-ESHUTDOWN` |
| `i2c_mark_adapter_resumed(adap)` | 清除挂起标记 | inline |

`i2c_mark_adapter_{suspended,resumed}()` 内部都取 `I2C_LOCK_ROOT_ADAPTER`，所以**不能在已持有总线锁时调用**。控制器驱动应在 `suspend()` 的最后、`resume()` 的最前调用它们。

---

## 8. 其它

### 8.1 DMA 安全缓冲

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-base.c`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_get_dma_safe_msg_buf(msg, threshold)` | 拿一块能用于 DMA 的缓冲：`msg->buf` 本身带 `I2C_M_DMA_SAFE` 就直接返回它，否则分配影子缓冲（写消息 `kmemdup`，读消息 `kzalloc`） | 返回 `u8 *`；`msg->len < threshold` 或 `threshold == 0` 时返回 NULL，表示"这次别用 DMA"。**只能在进程上下文调用**（源码注释明写） |
| `i2c_put_dma_safe_msg_buf(buf, msg, xferred)` | 释放影子缓冲 | 读消息且 `xferred` 为真时先把数据拷回 `msg->buf`；无返回值 |

`Documentation/i2c/dma-considerations.rst` 建议的阈值：多数驱动用 8 字节，≥16 字节基本一定值得 DMA。它同时说明：用户态经 i2c-dev 传下来的缓冲、以及核心 SMBus 仿真里的块缓冲，**核心保证是 DMA 安全的**。

### 8.2 总线恢复

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-base.c`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_recover_bus(adap)` | 触发一次总线恢复 | 适配器没填 `bus_recovery_info` 时返回 `-EBUSY` |
| `i2c_generic_scl_recovery(adap)` | 通用恢复算法：发 9 个 SCL 脉冲把卡住的从设备冲出来，再补一个 STOP | 控制器驱动把它填进 `bus_recovery_info->recover_bus` 即可复用；SCL 一直被拉低时返回 `-EBUSY` |

### 8.3 Host Notify

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-base.c`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_handle_smbus_host_notify(adap, addr)` | 控制器收到 Host Notify 后，通知核心去唤醒 `addr` 对应设备的 `alert()` | **不可睡眠** —— 源码注释明写 "Context: can't sleep"，内部用 `generic_handle_irq_safe()`，设计上就是给中断处理函数调用的 |

### 8.4 从机（slave）API

> 头文件：`include/linux/i2c.h`；实现：`drivers/i2c/i2c-core-slave.c`。完整说明见 `04` §3。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_slave_register(client, slave_cb)` | 把 `client` 注册成从机，事件回调走 `slave_cb` | `slave_cb` 类型 `i2c_slave_cb_t`；适配器没实现 `reg_slave` 时返回 `-EOPNOTSUPP` |
| `i2c_slave_unregister(client)` | 注销从机身份 | |
| `i2c_detect_slave_mode(dev)` | 看设备树里有没有带 `I2C_OWN_SLAVE_ADDRESS` 标志的子节点 | 返回 bool；控制器驱动用它决定要不要初始化从机硬件 |
| `i2c_slave_event(client, event, val)` | 控制器驱动向上报一个从机事件 | `event` 是 `enum i2c_slave_event`（READ/WRITE_REQUESTED、READ/WRITE_PROCESSED…）；通常在中断上下文调 |

### 8.5 mux API

> 头文件：`include/linux/i2c-mux.h`；实现：`drivers/i2c/i2c-mux.c`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_mux_alloc(parent, dev, max_adapters, sizeof_priv, flags, select, deselect)` | 分配一个 mux 核心对象，附带 `sizeof_priv` 字节私有数据 | 返回 `struct i2c_mux_core *`；`select`/`deselect` 原型 `int (*)(struct i2c_mux_core *, u32 chan_id)`；`flags` 取 `I2C_MUX_LOCKED` / `I2C_MUX_ARBITRATOR` / `I2C_MUX_GATE` |
| `i2c_mux_add_adapter(muxc, force_nr, chan_id, class)` | 建一条子总线（一个虚拟适配器） | `force_nr` 非 0 则固定总线号；`chan_id` 会原样传给 `select()` |
| `i2c_mux_del_adapters(muxc)` | 一次删掉该 mux 下全部子总线 | 无返回值 |
| `i2c_mux_priv(muxc)` | 取 `sizeof_priv` 那块私有数据 | inline |

### 8.6 算法（algos）API

> 头文件：`include/linux/i2c-algo-{bit,pca,pcf}.h`；实现：`drivers/i2c/algos/`。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_bit_add_bus(adap)` | 给适配器装上 `i2c_bit_algo` 并注册，总线号自动分配 | 调用前 `adap->algo_data` 必须已指向填好的 `struct i2c_algo_bit_data`；函数内部还会顺手把 `adap->retries` 设成 3，`getscl == NULL` 时自动补上 `I2C_AQ_NO_CLK_STRETCH` quirk |
| `i2c_bit_add_numbered_bus(adap)` | 同上，但用 `adap->nr` 固定编号 | |
| `i2c_bit_algo` | 位操作算法的 `i2c_algorithm` 实例 | 这是**变量**不是函数，想自己控制注册流程时可直接引用 |
| `i2c_pca_add_bus(adap)` / `i2c_pca_add_numbered_bus(adap)` | PCA9564/9665 版的上面两个 | `algo_data` 是 `struct i2c_algo_pca_data` |
| `i2c_pcf_add_bus(adap)` | PCF8584 版 | `algo_data` 是 `struct i2c_algo_pcf_data` |

### 8.7 SMBus 扩展

> 头文件：`include/linux/i2c-smbus.h`；实现：`drivers/i2c/i2c-smbus.c`（`i2c_new_smbus_alert_device()` 在 `i2c-core-smbus.c`）。

| API | 说明 | 备注 |
|---|---|---|
| `i2c_new_smbus_alert_device(adapter, setup)` | 在该总线上建一个 ARA（Alert Response Address）client，负责响应 SMBus Alert | `setup` 是 `struct i2c_smbus_alert_setup *`，只有一个 `irq` 字段；返回 `struct i2c_client *` |
| `i2c_handle_smbus_alert(ara)` | 控制器驱动在 ALERT 引脚中断里调，触发对 ARA 的轮询 | |
| `i2c_new_slave_host_notify_device(adapter)` | 以从机身份占用 Host Notify 地址 0x08，接收从设备主动上报 | 需要 `CONFIG_I2C_SLAVE`；返回 `struct i2c_client *` |
| `i2c_free_slave_host_notify_device(client)` | 释放上面那个设备 | 无返回值 |
| `i2c_register_spd(adap)` | 扫描内存条 SPD EEPROM 并自动实例化 | 整个实现包在 `#if IS_ENABLED(CONFIG_DMI)` 里，面向 x86 服务器，**arm64 嵌入式平台不涉及**；无返回值 |

---

## 9. 错误码约定

这是 i2c 子系统一个被低估的部分：`Documentation/i2c/fault-codes.rst` 明确规定了每个错误码的含义，**控制器驱动必须遵守**，否则上层无法正确重试。

| 错误码 | 含义（据 `fault-codes.rst`） | 谁产生 |
|---|---|---|
| `-EAGAIN` | 主发送时**仲裁失败**（另一个 master 在发不同数据）；或原子上下文里总线已被别人占用 | 控制器驱动；核心的 trylock。**核心只对这个码自动重试** |
| `-EBADMSG` | SMBus **PEC 校验失败**（只在读事务上报） | 核心的 `i2c_smbus_check_pec()` |
| `-EBUSY` | 总线忙超时，通常需要恢复或复位 | `i2c_generic_scl_recovery()` 发现 SCL 一直被拉低；固定编号被占 |
| `-EINVAL` | 参数非法，**I/O 还没开始** | 核心：地址校验、`num < 1`、`algo == NULL`、既无 `probe_new` 也无 `probe` |
| `-EIO` | 泛化的 I/O 错误。文档要求"能用更具体的就别用它" | 核心 SMBus 仿真里实际消息数 ≠ 期望 |
| `-ENODEV` | 地址上有东西，但**不是我要的设备** | 客户端驱动的 probe / detect |
| `-ENOMEM` | 内存不足 | 各处 |
| `-ENXIO` | **地址阶段没收到 ACK** —— 那个地址上大概没东西 | 控制器驱动 |
| `-EOPNOTSUPP` | 适配器不支持该操作 | 核心：quirk 挡下、无 `master_xfer`、无 `reg_slave` |
| `-EPROTO` | 从设备不符合协议。典型：SMBus 块响应长度超过 32 | 核心 SMBus 仿真 |
| `-ESHUTDOWN` | 适配器已 suspend | 核心的 `__i2c_check_suspended()` |
| `-ETIMEDOUT` | 操作超时被中止 | 控制器驱动 |

**给控制器驱动作者的实操建议**：地址无 ACK 一定要回 `-ENXIO`（很多探测逻辑依赖它区分"没设备"和"设备坏了"）；仲裁丢失一定要回 `-EAGAIN`（核心会自动重试，回别的码就变成硬失败）。

---

## 10. 用户态 API：`/dev/i2c-N`

> 头文件：`include/uapi/linux/i2c-dev.h`（ioctl 号与参数结构）、`include/linux/i2c-dev.h`（只有 `I2C_MAJOR 89`）；实现：`drivers/i2c/i2c-dev.c`。完整用法见 `05` §6。

这张表的「参数」列是 ioctl 的**实参类型**，不是函数签名，所以保留类型。

| ioctl | 值 | 参数 | 说明 |
|---|---|---|---|
| `I2C_SLAVE` | 0x0703 | `unsigned long addr` | 设置后续 read/write/SMBus 操作的目标地址。若该地址已被内核驱动占用则 `-EBUSY`。 |
| `I2C_SLAVE_FORCE` | 0x0706 | 同上 | 强制设置，跳过占用检查 |
| `I2C_TENBIT` | 0x0704 | `0`/`1` | 10 位地址模式（**注意：uapi 头文件明确说 i2c-dev 不支持 10 位地址**） |
| `I2C_FUNCS` | 0x0705 | `unsigned long *` | 取 `I2C_FUNC_*` 位图 |
| `I2C_RDWR` | 0x0707 | `struct i2c_rdwr_ioctl_data *` | 提交任意消息数组，最多 `I2C_RDWR_IOCTL_MAX_MSGS` = 42 条 |
| `I2C_PEC` | 0x0708 | `0`/`1` | 开关 PEC |
| `I2C_SMBUS` | 0x0720 | `struct i2c_smbus_ioctl_data *` | 提交单个 SMBus 事务 |
| `I2C_RETRIES` | 0x0701 | `unsigned long` | 设置重试次数 |
| `I2C_TIMEOUT` | 0x0702 | `unsigned long` | 设置超时，**单位是 10ms**（内部 `msecs_to_jiffies(arg * 10)`） |

限制：单条消息 `len` 上限 8192；`read()`/`write()` 也各自被截到 8192。`I2C_RDWR` 之外的操作都需要先 `I2C_SLAVE`。

---

## 11. 上下文速查表

| 想在这里调用 | 能用什么 |
|---|---|
| 进程上下文（probe、workqueue、ioctl） | 全部 |
| 中断处理函数 | `i2c_handle_smbus_host_notify()`；**不能**用 `i2c_transfer()` 等（它们会睡） |
| 关机 / panic / reboot 路径（`system_state > SYSTEM_RUNNING` 且不可抢占） | `i2c_transfer()` / `i2c_smbus_xfer()` 会自动切到原子路径：`i2c_in_atomic_xfer_mode()` 为真时改用 `master_xfer_atomic` / `smbus_xfer_atomic`，锁改成 trylock。**前提是控制器实现了 `*_atomic` 回调**，否则核心会 `WARN` 并硬着头皮走睡眠路径。 |
| slave 事件回调（`slave_cb`） | 通常在中断上下文。**不能**在里面发起同步 I²C 传输 —— testunit 后端就是用 workqueue 绕开的。 |
| mux 的 `select`/`deselect` | 取决于锁风格：parent-locked mux 必须用**不加锁**的 `__i2c_transfer()`；mux-locked mux 用普通 `i2c_transfer()`。搞反就死锁。见 `04` §1。 |
