---
title: 调试方法
---

# I2C 子系统（八）调试与观测

> 顺序建议：先看 §7 的错误码分诊表定位大方向，再用 §1~§5 的手段取证。

---

## 1. 编译期调试开关

三个 Kconfig，各自的作用范围是**按目录给 `-DDEBUG`**，从而把该目录下所有 `dev_dbg()` / `pr_debug()` 变成无条件打印：

| 选项 | 生效范围 | 实现 |
|---|---|---|
| `CONFIG_I2C_DEBUG_CORE` | `drivers/i2c/*.c`（核心、mux、dev、smbus、stub、slave 后端） | `drivers/i2c/Makefile:21`：`ccflags-$(CONFIG_I2C_DEBUG_CORE) := -DDEBUG` |
| `CONFIG_I2C_DEBUG_ALGO` | `drivers/i2c/algos/*.c` | `drivers/i2c/algos/Makefile:10` |
| `CONFIG_I2C_DEBUG_BUS` | `drivers/i2c/busses/*.c` | `drivers/i2c/busses/Makefile:157` 同样的写法 |

打开 `I2C_DEBUG_CORE` 后能看到的关键信息（都是 `dev_dbg`）：

| 消息 | 位置 | 含义 |
|---|---|---|
| `adapter [%s] registered` | `i2c-core-base.c:1577` | 适配器注册完成，此刻起会有传输进来 |
| `client [%s] registered with bus id %s` | `:1022` | client 实例化成功 |
| `probe` / `remove` | `:565` / `:629` | 进入 client 的 probe/remove |
| `Creating %s at 0x%02x` | `:2498` | 自动探测（detect）新建设备 |
| `Address for %s : 0x%x` | `:1216` | 从 board_info 解析出的地址 |
| `Trying i2c bus recovery` | `:298` | 触发总线恢复 |
| `Not using recovery: %s` | `:466` | 恢复配置不完整，说明原因（见下） |
| `Using Host Notify IRQ` | `:504` | client 的 irq 来自 Host Notify 而非硬件线 |
| `I2C level transfers not supported` | `:2243` | 适配器只会 SMBus，拒绝裸 I²C 传输 |
| `Removing %s at 0x%x` | `:1715` / `:1778` | 适配器删除时逐个摘掉 client |
| `attempting to delete unregistered adapter [%s]` | `:1762`（`pr_debug`） | 重复删除或删除未注册适配器 |

**`Not using recovery` 有两个打印点，级别不同** —— 同一句话，一句默认可见、一句默认不可见，这是 §8.3 最容易踩的坑：

| 出错原因 | 打印级别 | 默认可见？ |
|---|---|---|
| `no suitable method provided`（`bri->recover_bus` 为 NULL，`:435-436` 把 `is_error_level` 置 false） | `dev_dbg`（`:466`） | ✗ |
| `no {get|set}_scl() found`（`:452`） | `dev_err`（`:464`） | ✓ |
| `either get_sda() or set_sda() needed`（`:456`） | `dev_err`（`:464`） | ✓ |

即"驱动压根没填恢复方法"这个**最常见**的情况是静默的（`is_error_level = true` 的初值在 `:425`）。两条路径都会把 `adap->bus_recovery_info` 置 NULL 并返回 `-EINVAL`（`:468-470`）。

**不重编内核也能开**（前提是内核带 `CONFIG_DYNAMIC_DEBUG`）：

```bash
# 打开 i2c 核心全部动态调试
$ echo 'file drivers/i2c/i2c-core-base.c +p' | sudo tee /sys/kernel/debug/dynamic_debug/control
# 只打开某一行
$ echo 'file drivers/i2c/i2c-core-base.c line 2243 +p' | sudo tee /sys/kernel/debug/dynamic_debug/control
# 打开某个模块
$ echo 'module i2c_core +p' | sudo tee /sys/kernel/debug/dynamic_debug/control
# 关掉
$ echo 'module i2c_core -p' | sudo tee /sys/kernel/debug/dynamic_debug/control
```

**algo 层的额外开关**：三个算法各有一个 `i2c_debug` 模块参数，但**三者的可控性完全不同**，不能照同一个套路用。差异来自两处独立的写法：`i2c_debug` 的 `module_param()` 权限位，以及打印宏是否被 `#ifdef DEBUG` 包住。

| algo | `i2c_debug` 定义 | sysfs 是否有 `parameters/i2c_debug` | 打印宏是否受 `I2C_DEBUG_ALGO` 约束 |
|---|---|---|---|
| `i2c-algo-bit` | `:40`，默认值 **1**，`module_param(..., S_IRUGO\|S_IWUSR)`（`:41`） | 有，0644，**但整段在 `#ifdef DEBUG` 内（`:39-44`）** —— 没开 `I2C_DEBUG_ALGO` 时这个文件根本不存在 | 是。`bit_dbg()` 在 `#ifdef DEBUG` 下才展开成 `dev_dbg`，否则是空语句（`:22-31`） |
| `i2c-algo-pca` | `:24`，默认 0，`module_param(i2c_debug, int, 0)`（`:561`） | **没有**（权限位是 0）—— 只能 `modprobe i2c-algo-pca i2c_debug=3` 或内建时走 cmdline | **否**。`DEB1/2/3` 是裸 `printk(KERN_DEBUG ...)`（`:17-22`），不看 `DEBUG` 宏 |
| `i2c-algo-pcf` | `:34`，默认 0，`module_param(..., S_IRUGO\|S_IWUSR)`（`:421`） | 有，0644，且**不在 `#ifdef DEBUG` 内** —— 一直存在 | **否**。`DEB2/DEB3/DEBPROTO` 直接判 `i2c_debug`（`:25-27`） |

推论：
- **arm64 上真正会用到的是 `i2c-algo-bit`**（`i2c-gpio` 的底座；pca/pcf 是老 PC 外设芯片）。它的开关**必须重编内核**（`CONFIG_I2C_DEBUG_ALGO=y`），运行时改不了 —— 因为文件不存在，不是因为权限不够。
- pca/pcf 反过来：`I2C_DEBUG_ALGO` 对它们的 `i2c_debug` 输出**没有影响**（裸 `printk`），只影响这两个文件里另外的 `dev_dbg()`。它们的输出也**不能**用 dynamic_debug 控制。

```bash
# i2c-algo-bit：需 CONFIG_I2C_DEBUG_ALGO=y，然后运行时可调
$ echo 3 | sudo tee /sys/module/i2c_algo_bit/parameters/i2c_debug   # 0 关 / 1 常规 / 2 verbose / 3 very verbose
```

`bit_test` 是**另一码事**：它在 `#ifdef DEBUG` 之外（`:35-37`），所以**任何配置下都存在**；但权限是 `S_IRUGO`（0444，只读），只能在加载时给：

```bash
$ sudo modprobe i2c-algo-bit bit_test=1     # 模块形态
# 内建形态走 cmdline：i2c_algo_bit.bit_test=1
```

它会在注册总线时跑 `test_bus()`（`:229`）自检引脚接线；`bit_test >= 2` 时自检失败直接拒绝注册（返回 `-ENODEV`，`:653-656`）。

`test_bus()` 的诊断输出**与 `I2C_DEBUG_ALGO` 无关，默认就能看到**：它用的是 `pr_info`（`:242`，"Testing SDA only, SCL is not readable"）和一串 `printk(KERN_WARNING)`（`:247` 起），典型的 `SDA stuck high!` / `SDA stuck low!` / `SCL stuck high!` / `SCL stuck low!` —— 这是**排查 i2c-gpio 接线问题最省事的一招**，比开任何调试开关都直接。

---

## 2. tracepoint（首选手段）

**这是最应该先用的工具**：零编译期开销（static_key 门控，`i2c-core-base.c:67`）、能看到每一条消息的完整数据、不需要改内核配置。

### 2.1 九个 tracepoint

| 事件 | 定义 | 触发点 | 输出格式 |
|---|---|---|---|
| `i2c:i2c_write` | `include/trace/events/i2c.h:25` | `__i2c_transfer()` 每条写消息发出前 | `i2c-%d #%u a=%03x f=%04x l=%u [%*phD]`（`:44`）—— 含数据 |
| `i2c:i2c_read` | `:58` | 每条读消息发出前 | `i2c-%d #%u a=%03x f=%04x l=%u`（`:76`）—— 无数据（还没读到） |
| `i2c:i2c_reply` | `:89` | 传输完成后，**成功的**那几条读消息（见下） | `i2c-%d #%u a=%03x f=%04x l=%u [%*phD]`（`:108`）—— 含读回的数据 |
| `i2c:i2c_result` | `:122` | 整个 `__i2c_transfer()` 结束 | `i2c-%d n=%u ret=%d`（`:135`） |
| `smbus:smbus_write` | `include/trace/events/smbus.h:23` | `__i2c_smbus_xfer()`，写方向或 proc-call | 含 command、protocol、数据 |
| `smbus:smbus_read` | `:91` | 同一处调用点，读方向 | 含 command、protocol |
| `smbus:smbus_reply` | `:133` | 事务完成后，读方向且成功 | 含读回的数据 |
| `smbus:smbus_result` | `:199` | SMBus 事务结束（**无条件**） | 含 `res` |
| `i2c_slave:i2c_slave` | `include/trace/events/i2c_slave.h:30` | `i2c_slave_event()` 每次分发 | `i2c-%d a=%03x ret=%d %s [%*phD]`（`:59`），事件名用 `__print_symbolic`（`:23`）符号化 |

字段含义：`adapter_nr` = 总线号，`msg_nr` = 这条消息在本次传输里的序号，`addr` = 7 位地址，`len` = 长度。`f=` 的含义见下面的 。

**`i2c_reply` 的循环上界是 `ret` 而不是 `num`**（`i2c-core-base.c:2287`：`for (i = 0; i < ret; i++)`）。`ret` 是控制器实际成功发出的消息数，所以：

- 传输**整体失败**（`ret < 0`）→ **一条 `i2c_reply` 都没有**，只有 `i2c_result`；
- **部分成功**（`ret < num`）→ 只有前 `ret` 条里的读消息出 reply。

也就是说"发出去了"（`i2c_write`/`i2c_read`，在 `:2262-2269` 传输**之前**打）和"读回来了"（`i2c_reply`，传输**之后**打）是不对称的。**看到 write/read 却没有对应的 reply，本身就是失败的信号**，不用去翻 `ret`。

**四个 `smbus_*` 里只有一个是按成功过滤的**。前三个是 `TRACE_EVENT_CONDITION`，但条件各不相同（内核自己在 `i2c-core-smbus.c:568-570` 和 `:611` 的注释里点明了）：

| 事件 | `TP_CONDITION` | 位置 | 效果 |
|---|---|---|---|
| `smbus_write` | `read_write == I2C_SMBUS_WRITE \|\| protocol == I2C_SMBUS_PROC_CALL \|\| protocol == I2C_SMBUS_BLOCK_PROC_CALL` | `smbus.h:29-31` | 按**方向/协议**分流，与成败无关 |
| `smbus_read` | 上式取反 | `:96-98` | 同上，二者互补，**每次事务必出且仅出一个** |
| `smbus_reply` | `res >= 0 && read_write == I2C_SMBUS_READ` | `:139` | 唯一按成败过滤的 |
| `smbus_result` | 无（普通 `TRACE_EVENT`） | `:199` | 一定出 |

所以失败事务**照样会出 `smbus_write` 或 `smbus_read`**；缺的是 `smbus_reply`。判定成败一律看 `smbus_result` 的 `res`。

**`f=` 在三处是三个不同的东西**，这是本篇最容易读错的字段：

| 出现在 | `f=` 实际是什么 |
|---|---|
| `i2c_write` / `i2c_read` / `i2c_reply` | `msg->flags`，即 `I2C_M_*`（`I2C_M_RD` = 0x0001） |
| `smbus_write` / `smbus_read` | client 的 **未掩码** flags —— 打印点在 `i2c-core-smbus.c:571-574`，而掩码 `flags &= I2C_M_TEN \| I2C_CLIENT_PEC \| I2C_CLIENT_SCCB` 在 `:576`，**在它之后** |
| `smbus_reply` / `smbus_result` | 同一个变量，但已被掩码（打印点在 `:612-615`） |

后果：同一次 SMBus 事务，`smbus_read` 的 `f=` 可能比 `smbus_result` 的 `f=` 多出若干位（例如 `I2C_CLIENT_WAKE`）。**不要拿这两个 `f=` 对比着推理**。

### 2.2 用法

```bash
$ cd /sys/kernel/debug/tracing

# 打开全部 i2c 相关
$ echo 1 | sudo tee events/i2c/enable
$ echo 1 | sudo tee events/smbus/enable
$ echo 1 | sudo tee events/i2c_slave/enable

# 只看某条总线（adapter_nr 是九个事件里唯一都有的字段，可以整组下）
$ echo 'adapter_nr == 3' | sudo tee events/i2c/filter

# 只看某个地址 —— 必须逐个事件下，不能整组下（见下面的 ）
$ for e in i2c_write i2c_read i2c_reply; do
      echo 'addr == 0x50' | sudo tee events/i2c/$e/filter
  done

# 只看失败的传输（i2c 组）
$ echo 'ret < 0' | sudo tee events/i2c/i2c_result/filter
# SMBus 侧字段名不是 ret，是 res
$ echo 'res < 0' | sudo tee events/smbus/smbus_result/filter

$ sudo cat trace_pipe
```

**`events/i2c/filter` 这种"整组过滤"有个静默陷阱**：九个事件的字段并不一致，而组过滤会**逐个事件**去套同一个表达式。

| 事件 | 字段 |
|---|---|
| `i2c_write` / `i2c_read` / `i2c_reply` | `adapter_nr` `msg_nr` `addr` `flags` `len`（`i2c.h:30-34`/`:63-67`/`:94-98`） |
| `i2c_result` | `adapter_nr` `nr_msgs` `ret` —— **没有 `addr`，也没有 `msg_nr`**（`:126-128`） |
| `smbus_write` | `adapter_nr` `addr` `flags` `command` `len` `protocol`（`smbus.h:33-38`） |
| `smbus_read` | 同上但**没有 `len`**（`:100-104`） |
| `smbus_reply` | `adapter_nr` `addr` `flags` `command` `len` `protocol`（`:141-146`） |
| `smbus_result` | `adapter_nr` `addr` `flags` `read_write` `command` **`res`** `protocol`（`:206-212`）—— 字段名是 `res` 不是 `ret` |
| `i2c_slave` | `adapter_nr` `ret` `addr` `len` `event`（`i2c_slave.h:35-39`）—— 没有 `flags`/`msg_nr` |

对**套不上**的那个事件，内核走的是 `process_system_preds()` 里的 `filter_disable(file)`（`kernel/trace/trace_events_filter.c:1815`）—— 把该事件的过滤器**关掉**，也就是它变成**完全不过滤、全都打**，而不是不打。而且只要组里至少有一个事件成功，`fail` 就是 false（`:1834`），写入**返回成功，shell 不报错**。

所以 `echo 'addr == 0x50' > events/i2c/filter` 的实际效果是：write/read/reply 被正确过滤，`i2c_result` **仍然打出全系统每一次传输的结果行**。看起来像"过滤器漏了"，其实是设计如此。

排查办法：出错信息被 `append_filter_err()`（`:1071`）追加到了**该事件自己的** filter 文件里，`cat` 一下就能看到，格式是"原表达式 + `^` 指位 + `parse_error:` 行"（`:1096-1099`）：

```bash
$ cat events/i2c/i2c_result/filter
addr == 0x50
^
parse_error: Couldn't find or set field in one of a subsystem's events
```

（这句错误文本来自 `FILT_ERR_BAD_SUBSYS_FILTER` 的定义，`:101`。同一条记录也会进 `tracing/error_log`，`:1100`。）

**结论：除了 `adapter_nr`，其它字段一律用逐事件的 filter 文件。**

典型输出（读 EEPROM 的第 0 号寄存器）：

```
  i2cget-1234  [002] .... 1234.567890: i2c_write: i2c-3 #0 a=050 f=0000 l=1 [00]
  i2cget-1234  [002] .... 1234.567912: i2c_read:  i2c-3 #1 a=050 f=0001 l=1
  i2cget-1234  [002] .... 1234.568001: i2c_reply: i2c-3 #1 a=050 f=0001 l=1 [5a]
  i2cget-1234  [002] .... 1234.568005: i2c_result: i2c-3 n=2 ret=2
```

一眼能读出：两条消息（写命令 + 读数据）、地址 0x50、第二条带 `I2C_M_RD`(0x0001)、读回 0x5a、整体返回 2（= 消息数，成功）。

**用 trace-cmd 更方便**：

```bash
$ sudo trace-cmd record -e i2c -e smbus -e i2c_slave sleep 10
$ trace-cmd report
```

### 2.3 tracepoint 看不到什么

- **看不到总线上的实际电平时序**（那要靠逻辑分析仪）；
- **看不到重试**：`__i2c_transfer()` 的重试循环在 tracepoint 之内，重试多次只出一组事件，只有 `i2c_result` 的 ret 反映最终结果（见 `i2c-core-base.c:2273-2283`）；
- **看不到锁等待**：如果传输卡在 `bus_lock` 上，tracepoint 根本还没触发。

---

## 3. sysfs

### 3.1 目录结构

```
/sys/bus/i2c/
├── devices/
│   ├── i2c-0                 → 适配器（device_type = i2c_adapter_type）
│   │   ├── name              适配器名（i2c-core-base.c:671 的 name_show）
│   │   ├── new_device        （W，S_IWUSR）手工实例化，:1312
│   │   ├── delete_device     （W）手工删除，:1367
│   │   ├── i2c-dev/i2c-0/    → 字符设备（若 CONFIG_I2C_CHARDEV）
│   │   ├── 0-0050            → 该总线上的 client
│   │   └── ...
│   ├── 0-0050                → client（device_type = i2c_client_type）
│   │   ├── name              client 名（同一个 name_show，靠 dev->type 分支）
│   │   ├── modalias          → OF modalias（有 of_node 时）或 "i2c:xxx"，:673-689
│   │   ├── driver            → 绑定的驱动（软链接，没绑定就没有这个链接）
│   │   └── ...驱动自己加的属性
│   └── ...
├── drivers/
│   └── <驱动名>/
│       └── bind / unbind     手工绑定/解绑（driver core 提供，不是 i2c 的）
└── drivers_autoprobe

/sys/class/i2c-adapter/        （仅 CONFIG_I2C_COMPAT，兼容老用户态，i2c-core-base.c:1580）
/sys/class/i2c-dev/            （仅 CONFIG_I2C_CHARDEV，i2c-dev.c:742）
└── i2c-N/
    └── name                  该总线的适配器名（i2c-dev.c:98-107 的 name_show，
                              挂法是 i2c_dev_class->dev_groups = i2c_groups，:748）
```

适配器只有 3 个属性（`i2c_adapter_attrs[]`，`i2c-core-base.c:1370-1375`：`name` / `new_device` / `delete_device`）；client 只有 2 个（`i2c_dev_attrs[]`，`:691-696`：`name` / `modalias`）。i2c 核心刻意不在 sysfs 里暴露更多 —— 状态观测靠 tracepoint，配置靠 DT。

**`/sys/bus/i2c/drivers/<驱动名>/` 下只有 `bind` 和 `unbind`**，而且它们来自 driver core（`drivers/base/bus.c:639` 的 `add_bind_files()`），不是 i2c 加的。i2c 侧没有任何 driver 级属性：`i2c_bus_type`（`i2c-core-base.c:699-705`）**没有设 `drv_groups`**。

> ☠ 特别注意：**i2c 没有 `new_id`**。PCI/USB 那种"往 `id_table` 里动态追加 ID"的 sysfs 接口在 i2c 上不存在（`grep -rn new_id drivers/i2c/` 零命中）。要让某个驱动接管一个新地址，办法是 `new_device` 建 client（名字写成驱动 `id_table` 里的名字），或直接改 DT。

**`/sys/class/i2c-dev/i2c-N/name` 是不装 i2c-tools 也能列总线的办法**（`i2cdetect -l` 读的就是这里）：

```bash
$ for d in /sys/class/i2c-dev/*; do echo "$(basename $d): $(cat $d/name)"; done
i2c-0: rk3x-i2c
i2c-1: i2c-0-mux (chan_id 0)
```

这个 class 设备的 parent 是适配器 device（`i2c-dev.c:668`），名字用 `i2c-%d`（`:671`），所以上面 sysfs 树里 `i2c-0/i2c-dev/i2c-0/` 那一层就是它。

`delete_device` 用的是 `DEVICE_ATTR_IGNORE_LOCKDEP`（`:1367`），因为"在 sysfs 写回调里删除自己所属的 kobject"会让 lockdep 误报。

### 3.2 常用查询

```bash
# 这颗设备在哪条总线的哪个地址？→ 目录名就是 "总线号-地址(4 位十六进制)"
$ ls /sys/bus/i2c/devices/
0-0050  0-0068  i2c-0  i2c-1

# 这个地址被哪个驱动占了？
$ readlink /sys/bus/i2c/devices/0-0050/driver
../../../../bus/i2c/drivers/at24

# 没有 driver 链接 = 没有内核驱动，用户态可以安全访问
$ ls /sys/bus/i2c/devices/0-0068/driver 2>/dev/null || echo "no driver"

# 这条总线是谁提供的？
$ cat /sys/bus/i2c/devices/i2c-0/name
rk3x-i2c
$ readlink /sys/bus/i2c/devices/i2c-0/../
# 或看 device 链接找到平台设备

# mux 关系（i2c-mux.c:420-422 / :424-427 建的两条链接）
$ readlink /sys/bus/i2c/devices/i2c-2/mux_device
../../../0-0070
$ ls /sys/bus/i2c/devices/0-0070/channel-*

# 手工解绑一个驱动（不卸载模块）
$ echo 0-0050 | sudo tee /sys/bus/i2c/drivers/at24/unbind
$ echo 0-0050 | sudo tee /sys/bus/i2c/drivers/at24/bind
```

`modalias` 的用途是冷插拔：`modprobe $(cat /sys/bus/i2c/devices/0-0050/modalias)` 能加载正确的驱动（`i2c-core-base.c:692-693` 的注释就是这么写的）。

**`name` 的取值完全由控制器驱动决定，没有统一约定**，所以**不要靠它判断是哪种控制器**。三种常见写法：

| 写法 | 例子 |
|---|---|
| 固定字符串 | `i2c-rk3x.c:1258` → `rk3x-i2c` |
| `dev_name(dev)` | `i2c-tegra.c:1843-1844` |
| `pdev->name` | `i2c-imx.c:1482` |

mux 生成的子总线例外，格式是核心定的：`i2c-<父总线号>-mux (chan_id <N>)`（`i2c-mux.c:329-330`）。要确定控制器型号，看 `readlink -f .../i2c-0/device/driver` 或 DT 的 compatible。

---

## 4. debugfs

```
/sys/kernel/debug/i2c/          全局根目录（i2c-core-base.c:2104）
└── i2c-0/                      每个适配器一个（:1562，用 dev_name(&adap->dev) 命名）
    └── ...                     核心自己不放任何文件；给控制器驱动用
```

**核心只建目录，不建文件。** 目录的生命周期与适配器绑定（删除在 `:1603` 和 `:1805`）。控制器驱动可以把 `adap->debugfs` 当作自己放调试文件的地方。

一个反例说明这个机制还很新：`i2c-gpio` 的故障注入器**没有**用 `adap->debugfs`，而是自己建了一个全局目录 `/sys/kernel/debug/i2c-fault-injector/`，每个适配器一个子目录。原因写在 `drivers/i2c/busses/i2c-gpio.c:261-265` 的注释里 —— "如果将来每个 i2c 适配器都有 debugfs 目录，就把 fault-injector 目录挪进去；在那之前先用全局目录"。`adap->debugfs` 是后来才加的，这条注释还没跟上。

---

## 5. GPIO 故障注入（`i2c-gpio`）

**内核里少见的"主动制造硬件故障"的调试设施**，用于把控制器驱动往异常总线状态上逼。

### 5.1 前置条件（最容易搞错的地方）

**① 要单独打开 `CONFIG_I2C_GPIO_FAULT_INJECTOR`**（`drivers/i2c/busses/Kconfig:654-660`，`depends on I2C_GPIO`）。整段注入器代码都在 `#ifdef CONFIG_I2C_GPIO_FAULT_INJECTOR` 里（`i2c-gpio.c:74-301`），关掉时 `i2c_gpio_fault_injector_init()` 是空函数（`:299`）—— 也就是 debugfs 目录**根本不会出现**，不是"出现了但没用"。

**② 需要两条总线，不是一条。** 这一点 `rst:5-9` 写得很明确：i2c-gpio 提供的注入总线要**物理连到另一条总线**上，那条总线由**被测**的控制器驱动。

```
        ┌──────────────────────┐                 ┌───────────────────────┐
        │  被测控制器（DUT）    │                 │  i2c-gpio（注入器）    │
        │  比如 i2c-rk3x        │  SCL ─────────  │  两根普通 GPIO         │
        │  → /dev/i2c-0        │  SDA ─────────  │  → /dev/i2c-1         │
        └──────────────────────┘                 └───────────────────────┘
                  ▲                                        ▲
         你在这条总线上跑 i2cget            你在这条总线的 debugfs 里写注入文件
                  └────────── 观察 DUT 的反应 ────────────┘
```

**不要在注入总线自己上面 `i2cget`** —— 你要观察的是对端控制器的行为。

**③ `lose_arbitration` / `inject_panic` 只在 `bit_data.getscl != NULL` 时才创建**（`i2c-gpio.c:282` 的条件）—— 它们要 `request_irq()` 监听 SCL 下降沿（`:183`，`IRQF_TRIGGER_FALLING`），SCL 只能输出就做不到。而 `bit_data.getscl` 在 DT 写了 `i2c-gpio,scl-output-only` 时不会被赋值（`:419-420`，属性读取在 `:318`）。

### 5.2 六个文件

```
/sys/kernel/debug/i2c-fault-injector/       （i2c-gpio.c:267，全局目录）
└── <平台设备名>/                            （:272，pdev->name）
    ├── scl                      （0600，读写，:288）
    ├── sda                      （0600，读写，:289）
    ├── incomplete_address_phase （0200 只写，:278）
    ├── incomplete_write_byte    （0200 只写，:280）
    ├── inject_panic             （0200 只写，:283）见 5.1 ③
    └── lose_arbitration         （0200 只写，:285）见 5.1 ③
```

**写进去的那个数，四个文件是三种不同的东西** —— 这是本节最关键的一张表：

| 文件 | 写什么值 | 合法范围 | 行为 |
|---|---|---|---|
| `scl` / `sda` | **电平**：0 = 拉低，1 = 释放 | 0/1 | 立即生效并保持，直到你手动写回 1。读它返回当前电平（`WIRE_ATTRIBUTE` 宏，`:82-101`，读写都持根适配器锁） |
| `incomplete_address_phase` | **7 位从机地址** | `<= 0x7f`，否则 `-EINVAL`（`:136`） | 立刻发 9 个 bit：ADDR(7)+RD(1)+ACK 且 SDA 保持高（`:140` 的 `(addr << 2) \| 3`，`:142` 传 `pattern_size = 9`），**不发 STOP** |
| `incomplete_write_byte` | **7 位从机地址** | `<= 0x7f`（`:153`） | 发 18 个 bit：ADDR+WR+ACK，再一个 0x00 字节 + ACK（`:157-159`，`:161` 传 18），停在 ACK 相位 |
| `lose_arbitration` | **µs 时长** | `<= 100 * 1000`（即 100 ms），否则 `-EINVAL`（`:216`） | **武装**一个 SCL 下降沿中断；等到对端真的开始传输，把 SDA 拉低 `duration` µs 再释放（`:203-205`） |
| `inject_panic` | **µs 延迟** | `<= 100 * 1000`（`:245`） | 同样等 SCL 下降沿，然后延迟 `duration` µs 后 `panic("I2C fault injector induced panic")`（`:235-236`） |

☠ **不是"第 N 个 SCL 周期"** —— 后两个文件写的是**微秒**，触发点固定在**第一个 SCL 下降沿**。内核在 `:220-226` 的注释里解释了为什么选下降沿（确认对端真的开始传了；SDA 下降沿只能测出 'bus busy' 而非 'arbitration lost'），同时也承认**中断延迟会让最前面几个 bit 不被破坏**。

☠ **后两个文件的写操作会阻塞**：`i2c_gpio_fi_act_on_scl_irq()` 在 `wait_for_completion_interruptible()`（`:188`）上睡到中断到来。所以**必须放后台**，否则你没机会去另一条总线上发起传输：

```bash
$ cd /sys/kernel/debug/i2c-fault-injector/<平台设备名>

# 仲裁丢失：先武装（后台），再从被测总线发起传输
$ echo 200 | sudo tee lose_arbitration &        # 破坏 200 µs
$ i2cget -y <被测总线号> 0x3f                    # 期望 DUT 返回 -EAGAIN
```

`rst:106-110` 给的正是这个"先 `&` 后 `i2cget`"的写法。地址挑 `0x3f` 是因为**'1' 位多才容易看出被破坏**（`rst:102`），而且**这个地址上不需要真有设备** —— 仲裁丢失应该在地址阶段之前就被检出（`rst:103-104`）。

### 5.3 两条典型路径

**A. 制造总线卡死并验证恢复**（`sda`，可控、可复现，首选）

```bash
# 1. 按住 SDA，模拟从设备卡死
$ echo 0 | sudo tee sda

# 2. 从被测总线发起传输 → 观察 DUT 的反应
$ i2cget -y <被测总线号> 0x50 0x00

# 3. 松手 —— 手动写 sda 的方式恢复不了，必须自己放
$ echo 1 | sudo tee sda
```

`rst:35-41` 明确指出：这种手动按低的方式**恢复注定失败**（SDA 一直被钉住），想测"能自动恢复"的场景要用 `incomplete_*` 那两个。

☠ **别指望 `dmesg` 里出现什么** —— 按低 SDA 这条路径上，核心恢复流程**一句话都不打**。读一遍 `i2c_generic_scl_recovery()`（`:222-287`）就能看出来：

| 你按低的线 | 恢复流程的走向 | 打印 |
|---|---|---|
| **SDA** | 9 个脉冲（`:248` 的 `RECOVERY_CLK_CNT * 2` = 18 个半沿）跑完，每个 SCL 上升沿调 `i2c_generic_bus_free()`（`:274`），它看到 SDA 还是低就返回 `-EBUSY`（`:211`）→ 循环走完，函数**静默返回 `-EBUSY`** | **无** |
| **SCL** | 第一次检查 `get_scl()` 就发现 SCL 是低（`:251`）→ `dev_err "SCL is stuck low, exit recovery"`（`:252-253`），`-EBUSY` | **有**（`dev_err`，一定可见） |

所以想确认"恢复到底跑没跑"，唯一的办法是打开 `Trying i2c bus recovery` 这个 `dev_dbg`（`:298`，见 §1）。

☠☠ **更坑的一条：恢复可能"谎报成功"。** `i2c_generic_bus_free()` 只有在驱动提供了 `get_bus_free` 或 `get_sda` 时才真的能判断总线状态（`:203-206`）；两个都没有时它返回 `-EOPNOTSUPP`，而 `:280-282` 会把 `-EOPNOTSUPP` 直接改写成 **0**，注释写得很直白 —— *"If we can't check bus status, assume recovery worked"*。

于是：**总线还被钉着，`i2c_recover_bus()` 却返回成功**。排查时如果"恢复报成功但传输照旧失败"，第一件事就是去看控制器驱动的 `bus_recovery_info` 里有没有填 `get_sda`。

**B. 验证 panic 路径上的 I²C**（`inject_panic`，对应 §8.4）

```bash
$ echo 0 | sudo tee inject_panic &
$ i2cget -y <被测总线号> 0x3f     # 一旦开始传输就 panic
```

看 panic 之后 shutdown/reboot 代码能不能收拾这个烂摊子（`rst:115-119`）。

### 5.4 两个不要踩的坑

- **`incomplete_*` 要的是一个真实存在的从机地址**（`rst:70-71`：*the address of an existing I2C client device*）—— 因为它靠"从机 ACK 时把 SDA 拉低"来制造卡死。地址上没设备就什么也不会发生。这和 `lose_arbitration` 恰好相反。
- **`incomplete_write_byte` 有毁数据的风险**：它把对端从机置于"接下来的时钟脉冲会往寄存器 0x00 写数据"的状态（`rst:80-84`）。所以恢复流程如果只闷头打 9 个 SCL 脉冲而不检查 SDA、不补 STOP，**会往设备里写进随机数据**。别拿带写保护逻辑的 EEPROM 试。

> **未验证**：本节全部结论来自读代码和读 `Documentation/i2c/gpio-fault-injection.rst`（`:1-136` 全文），**没有在硬件上跑过**。另外从代码看有一处疑似缺陷：`scl` 文件是**无条件**创建的（`i2c-gpio.c:288`），但它的 get 回调走 `getscl(&priv->bit_data)`（`:80` 的宏 / `:88` 的展开），而 `bit_data.getscl` 在 `i2c-gpio,scl-output-only` 下是 NULL（`:419-420`）—— 这种配置下 `cat scl` 应该会空指针解引用。这是**代码推断，未实测**（触发条件是 root + debugfs + 打开注入器 + scl-output-only，影响面很小）。

---

## 6. 用户态工具

| 工具 | 用途 | 注意 |
|---|---|---|
| `i2cdetect -l` | 列出所有总线及其类型 | — |
| `i2cdetect -F <bus>` | 打印该总线的 functionality 位（对应 `I2C_FUNCS` ioctl） | 定位 `-EOPNOTSUPP` 的第一步 |
| `i2cdetect -y <bus>` | 扫描地址。`UU` = 被内核驱动占用（跳过），数字 = 有响应，`--` = 无响应 | **危险**：扫描本身是一次总线操作，某些芯片（尤其写保护/命令式芯片）会被扫描误触发 |
| `i2cget` / `i2cset` | 单次 SMBus 读/写 | `i2cset` 可以毁硬件 |
| `i2cdump` | 批量转储寄存器 | `-r` 限定范围 |
| `i2ctransfer` | 任意消息序列（走 `I2C_RDWR`） | 最灵活，也最危险 |
| `decode-dimms` | 解析 SPD | 依赖 `CONFIG_EEPROM_LEGACY` 或 `CONFIG_EEPROM_EE1004`（见下）。arm64 平台上基本用不到 |

**`decode-dimms` 和 `CONFIG_I2C_SMBUS` 没有关系**。`CONFIG_I2C_SMBUS`（`drivers/i2c/Kconfig:88-96`）只做两件事：**SMBus Alert 协议**和 **SMBus Host Notify 协议**（Kconfig 帮助文本 `:91-93` 原话："two extensions are supported: the SMBus Alert protocol and the SMBus Host Notify protocol"），跟 SPD/EEPROM 读取毫无关系。`decode-dimms` 需要的是把 SPD 内容暴露成 sysfs 文件的驱动：

| 选项 | 位置 | 说明 |
|---|---|---|
| `CONFIG_EEPROM_LEGACY` | `drivers/misc/eeprom/Kconfig:49-58` | 标题里就写着 **DEPRECATED**，帮助文本说"will be removed soon, please use the better at24 driver instead" |
| `CONFIG_EEPROM_EE1004` | `:123-131` | DDR4 的 JEDEC EE1004 SPD EEPROM，现在该用的就是它 |

**扫描方式的差异**：`i2cdetect` 有三种模式（`-q` quick write、`-r` read byte、默认自动选），因为**没有一种探测方式对所有芯片都安全**。这与核心 `i2c_default_probe()` 里同样的顾虑一致（`i2c-core-base.c:2432-2437` 对 0x30-0x37 / 0x50-0x5f 改用 short byte read，理由是 quick write 会毁 24RF08，见 [`03-internals.md`](03-internals.md) §A3）。

---

## 7. 错误码分诊表

这是最常用的一页。错误码的权威定义在 `Documentation/i2c/fault-codes.rst:42-130`。

| 错误码 | 内核里的含义 | 最可能的物理/软件原因 | 下一步查什么 |
|---|---|---|---|
| **`-ENXIO`** | 地址阶段没收到 ACK | ① 地址错了；② 芯片没上电（检查 regulator）；③ 芯片复位线没释放；④ 焊接/接线问题；⑤ 芯片还在内部忙（EEPROM 写周期中） | 用 `i2cdetect` 确认地址是否有响应；量 VDD 和复位脚；tracepoint 看 `a=` 字段是否与预期一致 |
| **`-ENODEV`** | 探测时：这个地址上**有东西**，但不是**正确的**东西 | 地址被别的芯片占了；驱动的 ID 检查失败 | 读 ID 寄存器看到的实际值；查芯片型号/版本 |
| **`-EAGAIN`** | 丢失仲裁（master transmit 阶段）；或原子上下文里总线已被占用 | ① 真的多主机；② 总线上有噪声被误判为其它主机；③ 上拉电阻太大导致上升沿太慢；④ 关机路径上有并发传输 | 逻辑分析仪看是否有第二个主机；量上升时间；`I2C_DEBUG_BUS` 看控制器的仲裁状态位 |
| **`-EBUSY`** | **五种毫不相干的情况共用一个码**（见下面的 1） | 见 1 | 先按 1 判断落在哪一类，再决定量电平还是查地址冲突 |
| **`-ETIMEDOUT`** | 操作耗时超过 `adap->timeout` | 时钟拉伸太长；从设备状态机卡住；控制器中断没来 | 增大 `adap->timeout` 试；量 SCL 被按低的时长；检查中断是否被 mask |
| **`-EOPNOTSUPP`** | 适配器不支持这个操作 | ① 控制器只会 SMBus，你发了裸 I²C（`i2c-core-base.c:2243`）；② 用了 `I2C_FUNC_*` 里没声明的事务；③ 用了 `I2C_M_*` 协议变体但控制器没 `I2C_FUNC_PROTOCOL_MANGLING`；④ slave 注册但控制器没 `reg_slave`（`i2c-core-slave.c:40`）；**⑤ quirks 检查失败**（长度/消息数/组合方式超限，见下面的 2） | `i2cdetect -F <bus>` 看能力位；**`dmesg \| grep 'adapter quirk'`**（2，默认就能看到）；`03-internals.md` §A6.1 看 SMBus 仿真的适用条件 |
| **`-EPROTO`** | 从设备违反协议 | 见下面的 3 —— 核心里**只有一处**，且只查上界 | tracepoint 看实际返回长度；`dmesg` 看是哪一句（3 的表给了三句不同的话） |
| **`-EBADMSG`** | PEC 校验失败（**只在读方向报**） | 信号完整性问题；PEC 只有一方开启 | 核心里就一处：`i2c_smbus_check_pec()`（`i2c-core-smbus.c:82-93`），返回前打 **`pr_debug "Bad PEC 0x%02x vs. 0x%02x"`**（`:88-89`，前一个是收到的、后一个是算出来的）—— **默认看不到，要开 `I2C_DEBUG_CORE` 或 dynamic_debug**。拿到这两个值就能判断是"全错"（PEC 一方没开）还是"差几位"（信号质量） |
| **`-EINVAL`** | 参数在**任何 I/O 之前**就被判非法 | 地址越界（`i2c_check_addr_validity()`）；`I2C_M_RECV_LEN` 缓冲太小（`i2c-dev.c:275`）；SMBus block **写**方向长度超限（`i2c-core-smbus.c:403-407` / `:417-421` / `:433-438`）；`I2C_SLAVE` ioctl 地址越界（`i2c-dev.c:402-404`） | `dmesg \| grep 'Invalid block'`（三句都是 `dev_err`，默认可见）。**注意 quirks 失败不在这里**，它是 `-EOPNOTSUPP`（2） |
| **`-ESHUTDOWN`** | 适配器已 suspend | 在 `_noirq` 阶段或 suspend 之后访问 I²C | `__i2c_check_suspended()`（`i2c-core.h:52-60`）是它在整个 `drivers/i2c/` 里的**唯一**来源（`:57`，全树 grep 确认）。它打 **`dev_WARN "Transfer while suspended"`**（`:55-56`）—— ☠ 靠 `I2C_ALF_SUSPEND_REPORTED` 位（`include/linux/i2c.h:739`）做了 `test_and_set_bit`，**每个适配器只打印一次**；而且 resume 时只清 `I2C_ALF_IS_SUSPENDED`、**不清这个 REPORTED 位**（`i2c.h:856`），所以是整个适配器生命周期里只打一次。别以为"没警告就是没发生" |
| **`-EIO`** | 含义模糊的通用失败 | 控制器驱动偷懒了；或部分成功（消息数 < num） | 看 tracepoint 的 `i2c_result` ret 值；读控制器驱动的具体返回点 |
| **`-ENOMEM`** | 内存不足 | — | — |
| **`-EPROBE_DEFER`** | 依赖还没就绪（不是 I²C 错误） | 适配器还没注册；regulator/gpio provider 还没就绪 | `/sys/kernel/debug/devices_deferred` |

**核心对探测错误的特殊处理**（`fault-codes.rst` 的 ENODEV 条目）：探测期间返回 `-ENXIO` 或 `-ENODEV` 是正常的"这里没东西"，核心静默处理；**其它错误码会被核心警告**，因为那表示总线本身有问题而不只是地址空着。

### 1 `-EBUSY` 的五个来源（含义完全不同）

拿到 `-EBUSY` 第一件事是判断它来自哪一类 —— 前两类是**电平问题**，后三类是**软件地址/编号冲突**，排查方向相反。

| # | 来源 | 位置 | 含义 |
|---|---|---|---|
| 1 | `i2c_generic_bus_free()` | `i2c-core-base.c:211` | 9 个恢复脉冲打完，SDA 还是低 —— **总线真的卡着** |
| 2 | `i2c_generic_scl_recovery()` | `:254` | 恢复过程中发现 SCL 被按低（伴随 `dev_err "SCL is stuck low, exit recovery"`，`:252-253`） |
| 3 | `i2c_recover_bus()` | `:296` | ☠ **驱动压根没填 `bus_recovery_info`** —— 与总线状态无关，纯粹"没这功能"。**这是最容易误判成"总线卡死"的一个** |
| 4 | `__i2c_check_addr_busy()` / `i2c_lock_addr()` | `:792` / `:931` | 这个地址上已经有 client 了，或正在实例化中 |
| 5 | `i2c_add_numbered_adapter()` | `:1629` | 请求的总线号已被占（`idr_alloc` 的 `-ENOSPC` 被改写成 `-EBUSY`，伴随 `WARN "couldn't get idr"`） |

用户态另有两处：`i2c-dev.c:407`（`I2C_SLAVE` ioctl 发现该地址已被内核驱动绑定，判定逻辑在 `:187`：`dev->driver ? -EBUSY : 0`）—— 这也是 `i2cdetect` 打 `UU` 的同一个判据。

### 2 quirks 拒绝：是 `-EOPNOTSUPP`，而且**默认就能看到原因**

`i2c_check_for_quirks()` 的所有拒绝都走同一个 `i2c_quirk_error()`（`i2c-core-base.c:2163-2168`），它：

- 返回 **`-EOPNOTSUPP`**，不是 `-EINVAL`；
- 打 **`dev_err_ratelimited("adapter quirk: %s (addr 0x%04x, size %u, %s)")`** —— `dev_err` 级别，**不需要开任何调试开关**（只是会被 ratelimit）。

所以"打开 `I2C_DEBUG_CORE` 才能看到 quirk 拒绝原因"是错的 —— 直接 `dmesg | grep 'adapter quirk'` 就有，而且第一个 `%s` 已经把原因说清楚了。一共 **6 句**不同的原因、10 个调用点：

| 原因字符串 | 调用点 | 触发条件 |
|---|---|---|
| `1st comb msg must be write` | `:2183` | 有 `I2C_AQ_COMB_WRITE_FIRST`，但第一条不是写 |
| `2nd comb msg must be read` | `:2186` | 有 `I2C_AQ_COMB_READ_SECOND`，但第二条不是读 |
| `comb msg only to same addr` | `:2189` | 有 `I2C_AQ_COMB_SAME_ADDR`，但两条地址不同 |
| `msg too long` | `:2192` / `:2195` / `:2209` / `:2215` | 超 `max_comb_1st_msg_len` / `max_comb_2nd_msg_len` / `max_write_len` / `max_read_len` |
| `too many messages` | `:2202` | 超 `max_num_msgs` |
| `no zero length` | `:2212` / `:2218` | 有 `I2C_AQ_NO_ZERO_LEN_READ/WRITE`，但 `msg->len == 0` |

打印里的 `size %u` 是 `msg->len`、最后的 `%s` 是 `read`/`write`，配合上表基本能直接定位到是哪一条 quirk。

顺带一个常见误解：**核心只会拒绝超长传输，不会帮你拆分**（`max_write_len` 超了就是 `-EOPNOTSUPP`，不是自动分片）。要分片得调用方自己做。

### 3 `-EPROTO`：核心里只有一处，而且只查上界

全树 grep（`drivers/i2c/*.c` + `drivers/i2c/algos/*.c`）只有**两个** `-EPROTO`，语义还不一样：

| 位置 | 检查 | 打印 |
|---|---|---|
| `i2c-core-smbus.c:502-507` | `msg[1].buf[0] > I2C_SMBUS_BLOCK_MAX`（**只有上界，没有 `== 0` 的下界检查**） | `dev_err "Invalid block size returned: %d"`（`:503-505`），默认可见 |
| `algos/i2c-algo-bit.c:438-446` | `inval <= 0 \|\| inval > I2C_SMBUS_BLOCK_MAX`，即完整的 **1..32** | `dev_err "readbytes: invalid block length (%d)"`（`:442-444`） |

所以"SMBus block 长度超出 1..32 → `-EPROTO`"这句话**只对走 `i2c-algo-bit` 的总线成立**；走原生 SMBus 控制器时，长度 0 不会在核心层被 `-EPROTO` 拦下。

另外 `drivers/i2c/busses/` 下有 24 个文件也会返回 `-EPROTO`，含义由各控制器自定。**所以看到 `-EPROTO` 不能直接套上面两行，先看 `dmesg` 里到底是哪一句话。**

注意区分方向：上面两处都是**读**方向（从设备返回的长度不对）。**写**方向的长度违规是 `-EINVAL`，见分诊表的 `-EINVAL` 行。

---

## 8. 典型问题的排查路径

### 8.1 "驱动没 probe"

```
1. client 存在吗？
     ls /sys/bus/i2c/devices/ | grep <总线号>-
     ├─ 不存在 → DT 枚举问题，转 06 §7 的完整排查表。
     │    最快的三条：
     │      cat .../status 是否 okay
     │      dmesg | grep of_i2c   （"modalias failure" / "invalid reg"）
     │      dtc -I fs -O dts /sys/firmware/devicetree/base 看内核实际拿到什么
     └─ 存在 → 继续
2. driver 注册了吗？
     ls /sys/bus/i2c/drivers/ | grep <驱动名>
     └─ 没有 → 模块没加载。检查 modalias/MODULE_DEVICE_TABLE
3. 匹配上了吗？
     readlink /sys/bus/i2c/devices/<N>-<addr>/driver
     └─ 空 → 匹配失败。arm64 上实际起作用的是 OF 匹配，失败后退回
              id_table（i2c-core-base.c 的 i2c_device_match，见 03 §A4）
              最常见原因：compatible 字符串与 of_match_table 不一致；
              注意 client->name 只取第一个 compatible 的逗号后半段（06 §3.2）
4. probe 被调用但失败了？
     dmesg | grep <驱动名>
     └─ 用 dev_err_probe() 的驱动会打出具体原因
5. 延迟 probe？
     cat /sys/kernel/debug/devices_deferred
```

### 8.2 "读到的数据不对"

```
1. 开 tracepoint 看总线上到底发了什么：
     echo 1 > events/i2c/enable ; cat trace_pipe
   ├─ 地址不对 → 驱动或 DT 的 reg 错
   ├─ 消息数/长度不对 → 检查 quirks 是否把传输拆开了
   │    （i2c-core-base.c 的 i2c_check_for_quirks；例如 max_write_len
   │      会让长写被拒绝而不是自动拆分 —— 核心不拆，只拒绝）
   ├─ 写命令 + 读数据之间出现了 STOP → 用的是两次独立调用而不是
   │    一次 i2c_transfer(msgs, 2)；某些芯片要求 repeated-START
   └─ 总线上一切正常但数据错 → 信号完整性 / 芯片状态机 / 手册理解
2. 是不是 SMBus 仿真的字节序问题？
     i2c_smbus_read_word_data() 是小端（LSB 先），
     _swapped() 是大端。搞反了就是高低字节颠倒。
     （i2c-core-smbus.c 的两个 helper）
3. 是不是有第二个访问者？
     ├─ 用户态：lsof /dev/i2c-N；有没有人在跑 i2cdump
     ├─ 另一个内核驱动：readlink .../driver 看地址是否被占
     └─ 另一个主机：DT 里写了 multi-master 吗（06 §2.5）；
        arm64 上常见的隐藏主机是 SCP/BMC 之类的协处理器
```

### 8.3 "总线卡死 / 一直超时"

```
1. 静态电平：SCL 和 SDA 空闲时都应该是高。
     用 i2c-gpio 的 debugfs 读 scl/sda，或用万用表。
     ├─ SDA 低 → 某个从设备卡在发送数据中间（最常见）
     └─ SCL 低 → 从设备在时钟拉伸，或控制器自己出问题
2. 恢复机制配了吗？—— ☠ 不要只靠 dmesg 判断，多数情况是静默的
     见下面的"恢复取证三步"。
3. 恢复原理（03 §A7）：核心发最多 9 个 SCL 脉冲
   （i2c-core-base.c:248，RECOVERY_CLK_CNT=9，:220），
   让卡住的从设备把它剩下的位吐完，同时让 SDA 跟随 SCL
   半个周期以便顺带造出 STOP（:229-234 的注释）。
   要么用 GPIO 直接摇引脚（i2c_generic_scl_recovery），
   要么用控制器自带的恢复功能（recover_bus 回调）。
4. 复现：用 i2c-gpio 的 sda 文件按住 SDA（§5）。
```

**恢复取证三步**（`dmesg | grep -i recover` 通常什么都不出，原因见 §1 的 ）：

| 步 | 查什么 | 怎么查 | 读法 |
|---|---|---|---|
| ① | 驱动到底配没配恢复 | `grep -n bus_recovery_info drivers/i2c/busses/<你的控制器>.c` | 没有 → 后面两步不用做了，`i2c_recover_bus()` 直接 `-EBUSY`（`:296`） |
| ② | 配了但被核心拒了吗 | 开 `I2C_DEBUG_CORE`（或 dynamic_debug 打开 `i2c-core-base.c` 全文），看 `Not using recovery: %s` | **`no suitable method provided` 是 `dev_dbg`，不开就看不到** —— 这是最常见的一句，见 §1 的三行表 |
| ③ | 跑了吗、成了吗 | 同样需要开关，看 `Trying i2c bus recovery`（`:298`，`dev_dbg`） | 出现了说明进了恢复流程；后续是否成功**看返回值，不要看日志** —— 见 §5.3 A 的两张表：按低 SDA 这条路核心一句话都不打 |

☠ **恢复可能"谎报成功"**：驱动没提供 `get_bus_free`/`get_sda` 时，`i2c_generic_bus_free()` 返回 `-EOPNOTSUPP`，被 `:280-282` 改写成 0（注释原话 *"If we can't check bus status, assume recovery worked"*）。**"恢复报成功但传输照旧失败" = 优先怀疑驱动没填 `get_sda`**。

### 8.4 "关机/reboot 时挂住"

```
关机路径要通过 PMIC 断电，此时中断已关。
1. dmesg 里有 "No atomic I2C transfer handler for '<适配器名>'"？
     → 控制器驱动既没 master_xfer_atomic 也没 smbus_xfer_atomic
       （05 §4.2）。这是 WARN()，一定看得到，带调用栈。
2. 卡在 rt_mutex 上？
     → 关机路径上另有传输在跑；i2c_in_atomic_xfer_mode() 走的是
        trylock，失败返回 -EAGAIN（i2c-core.h:37 的 __i2c_lock_bus_helper）
3. 走的是 mux 子总线？
     → 子总线只在父适配器支持原子时才支持（i2c-mux.c:314-315 / :323-324），
        而且 mux 的 select 回调也必须在原子上下文可用 —— 见下方
4. 验证手段：i2c-gpio 的 inject_panic（§5.3 B），传输一开始就
   触发 panic，看 panic 路径上的 I²C 是否还能工作。
```

☠ **第 1 条要 grep 的字符串必须一字不差**。这句话的原文在 `i2c-core.h:42-43`：

```c
WARN(!adap->algo->master_xfer_atomic && !adap->algo->smbus_xfer_atomic,
     "No atomic I2C transfer handler for '%s'\n", dev_name(&adap->dev));
```

`dmesg | grep -i 'atomic'` 是安全的搜法。要注意的三点：

- 判定是**两个回调都缺**才警告 —— 只有 `smbus_xfer_atomic` 也算过关（`i2c-core.h:42`）；
- 它是 `WARN()` 不是 `dev_warn()`，所以带**调用栈**，能直接看出是谁在关机路径上发的传输；
- 警告之后代码**继续往下走**（`:44` 的 `i2c_trylock_bus`），并不阻止这次传输 —— 挂住是后面的事。

**mux 其实没有"专门的原子版本函数"** —— 它把非原子那个函数指针**原样复制**到 atomic 槽位。四个函数和两处赋值：

| 位置 | 内容 |
|---|---|
| `i2c-mux.c:39` | `__i2c_mux_master_xfer()` —— parent-locked 用 |
| `:58` | `i2c_mux_master_xfer()` —— mux-locked 用（**会拿锁**） |
| `:77` | `__i2c_mux_smbus_xfer()` —— parent-locked 用 |
| `:99` | `i2c_mux_smbus_xfer()` —— mux-locked 用（**会拿锁**） |
| `:308-313` | 按 `muxc->mux_locked` 在上面两者中选一个填进 `algo.master_xfer` |
| **`:314-315`** | `if (parent->algo->master_xfer_atomic) algo.master_xfer_atomic = algo.master_xfer;` —— **同一个指针**，条件只是"父适配器支持原子" |
| `:317-322` / **`:323-324`** | smbus 侧同理 |

含义：**子总线的原子能力完全继承自父适配器**，mux 自己不做任何原子适配。而被复制进 atomic 槽的可能正是 mux-locked 那个**会拿锁**的版本（`:58`/`:99`）—— 所以 §8.5 里"mux 的 select 在原子上下文必须可用"这条要求是硬性的，核心不会替你兜。

### 8.5 "mux 下的设备偶发读错"

这类问题的根因往往是拓扑与锁风格的组合不安全。按 [`04-extensions.md`](04-extensions.md) §1.5 的清单逐条对照：

```
1. 这个 mux 是 mux-locked 还是 parent-locked？
     看驱动 i2c_mux_alloc() 的 flags 参数有没有 I2C_MUX_LOCKED
2. mux-locked？检查四条限制：
     ├─ 它下面有没有 parent-locked 的 mux（不允许）
     ├─ 有没有另一个非兄弟的 mux-locked mux，且子总线上有相同地址
     ├─ 是不是"用一次就自动关"的门
     └─ select 里有没有非 I²C 的状态改动（必须自己锁根适配器，
        用 i2c_root_adapter()，i2c-mux.c:213）
3. parent-locked？检查两条：
     ├─ 它是不是另一个 mux 的子节点
     └─ select 里调 gpio/pinctrl/regmap/iio 时，那些子系统内部
        引发的 I²C 传输是不是不加锁的（这一点文档自己都说
        "maybe even impossible"，i2c-topology.rst:184-187）
4. 观测：tracepoint 的 adapter_nr 能区分是走了哪条子总线；
   如果看到"select 后到实际传输之间插入了别的 adapter_nr 的传输"，
   就是交错问题。
```

---

## 9. 一页速查

```
最快定位手段：
  能力问题        → i2cdetect -F <bus>
  地址/枚举问题   → ls /sys/bus/i2c/devices/ + readlink .../driver
  总线上发了什么  → echo 1 > /sys/kernel/debug/tracing/events/i2c/enable
  匹配/probe 问题 → echo 'module i2c_core +p' > .../dynamic_debug/control
  卡死            → dmesg | grep -i recover  +  量 SCL/SDA 静态电平
  想主动制造故障  → /sys/kernel/debug/i2c-fault-injector/（需 i2c-gpio）
  无硬件测驱动    → modprobe i2c-stub chip_addr=0xNN
```
