---
title: 费曼学习法
---

# I2C 费曼学习法：用大白话理解 I²C 子系统

> 技术文档（00-07）讲的是"是什么"和"怎么做"。这篇讲的是"为什么是这样"和"遇到 bug 时该怎么想"。
> 读完这篇不会教你写驱动，但会让你在看到别人写驱动、或者自己调 I²C 出问题的时候，脑子里有一张地图。

---

## §1 adapter 和 algorithm 是什么关系？

### 先举个例子

理解这两个结构，可以把它们想成**餐厅和菜谱**。

假设你住的城市里有三家肯德基。它们都有：
- 各自的门牌号和店面（这就是 `i2c_adapter` 的 `dev_name`、`device_register`）
- 各自的排队系统和营业时间（`bus_lock`、`timeout`、`retries`）
- 各自的水电费账单和员工（`dev.parent`、中断、时钟）

但它们用的都是**同一份标准操作流程**（SOP）：汉堡怎么煎、薯条炸多久、客人点单后怎么叫号。这份 SOP 就是 `i2c_algorithm`。

内核里也是这样的。一个 SoC 上可能有 5 个 I²C 控制器，每个控制器都是一个 `i2c_adapter`。但它们往往共用一份 `i2c_algorithm`——一份写着"怎么发 START、怎么发地址、怎么等 ACK"的函数指针表。

```c
// i2c-rk3x.c 里的一份 SOP
static const struct i2c_algorithm rk3x_i2c_algorithm = {
    .master_xfer = rk3x_i2c_xfer,
    .functionality = rk3x_i2c_func,
};

// 每个 I²C 控制器 probe 出来，都把自己的 adapter 指向这份 SOP
i2c->adap.algo = &rk3x_i2c_algorithm;
```

### 关键区别

| | adapter（餐厅） | algorithm（SOP） |
|---|---|---|
| 数量 | SoC 上有几个控制器，就有几个 | 一种控制器通常只有一份 |
| 有没有状态 | 有，锁、超时、重试次数都是状态 | 没有，`static const`，只读 |
| 需不需要注册 | 需要，`i2c_register_adapter()` | 不需要，内核没有 `i2c_register_algorithm()` |
| 谁来释放 | remove 时释放 | 模块退出时跟着代码段一起消失 |

所以当你看到 `adap->algo->master_xfer(adap, msgs, num)` 的时候，其实是在说："让这家餐厅（adapter）按照它的 SOP（algorithm）做一份菜（传一组消息）。"SOP 本身不知道今天是哪家餐厅在用它。

### 什么时候这个类比会误导你？

真实世界里一份纸质 SOP 只能被一个人看。但内核里的 algorithm 是代码，多个 adapter 可以同时调用同一份 `master_xfer`，因为它没有可变状态。真正需要保护的是 adapter 自己的状态——所以锁在 adapter 里（`bus_lock`），不在 algorithm 里。

### 代码在哪

```
include/linux/i2c.h:545-573     ← i2c_algorithm：只有函数指针
include/linux/i2c.h:723-758     ← i2c_adapter：有 device、有锁、有超时
```

---

## §2 adapter 为什么注册在 i2c_bus_type 上？

### 先举个例子

这里可以用 **USB hub** 来理解。

你买了一个 USB hub，插到电脑上。这个 hub 本身是一个 USB **设备**（电脑要给它供电、要枚举它），但它同时又提供 4 个 USB **接口**给 U 盘、鼠标、键盘用。

I²C 控制器也是这样的双面身份：
- 对 SoC 来说，它是一个 **platform 设备**：需要时钟、需要中断、需要 pinctrl。
- 对 I²C 设备（温度传感器、EEPROM、触摸芯片）来说，它是一个 **总线接口**：设备要挂在它下面，通过它和 CPU 通信。

所以内核里给它造了两条路：
- `platform_bus_type`：用来和 SoC 里的其他控制器并列（UART、SPI、PWM 都在这里）。
- `i2c_bus_type`：用来让 I²C 设备（client）找到它、挂到它下面。

### 它"住"在哪条路上？

答案是 `i2c_bus_type`。原因是：I²C 设备需要一种机制来找到适配器并绑定驱动。`i2c_bus_type` 上的 match/probe 机制就是干这个的。而 platform 那条路只负责"这个控制器被 SoC 识别出来了"，不负责"I²C 设备怎么找到它"。

这两条路靠 `dev.parent` 连起来：

```
        platform 世界                         i2c 世界
        ==============                       ==========

  SoC ── platform_device (rk3x-i2c) ──┐
                                      │ dev.parent
                                      ▼
                              i2c_adapter (i2c-0)
                                      │
                                      ├── i2c_client (温度传感器 @ 0x48)
                                      └── i2c_client (EEPROM @ 0x50)
```

```c
// i2c-rk3x.c:1265
i2c->adap.dev.parent = &pdev->dev;
```

没有这一行，adapter 就不知道自己在 SoC 里的位置，sysfs 里会少半截路径。

### 什么时候这个类比会误导你？

USB hub 在 USB 总线上仍然是一个"普通设备"，会被驱动绑定。但 I²C adapter 在 `i2c_bus_type` 上**不是普通设备**——它是 `i2c_adapter_type`，内核会专门判断"你是不是 client？不是就别走 probe"。所以 adapter 在 i2c 这条路上更像"街道办事处"，而不是"商户"。

### 代码在哪

```
i2c-core-base.c:1553-1556    ← adapter 注册在 i2c_bus_type 上
i2c-core-base.c:725-730      ← i2c_verify_client() 判断是 client 还是 adapter
```

---

## §3 adapter 为什么不会被 driver 绑定？

### 先举个例子

想象一条高速公路。路上有车，也有马路本身。交警站在路边，只拦车查驾照，不会给马路本身发驾照。

`i2c_bus_type` 上也是两种东西：
- **车**：`i2c_client`，有地址（车牌号），需要 driver（司机）。
- **马路**：`i2c_adapter`，提供服务，但不需要被"绑定"。

内核的 `i2c_device_probe()` 开头就做了这个判断：

```c
static int i2c_device_probe(struct device *dev)
{
    struct i2c_client *client = i2c_verify_client(dev);
    if (!client)
        return 0;    // 不是车？那我不查你，继续
    ...
}
```

`i2c_verify_client()` 检查 `dev->type`。如果是 `i2c_adapter_type`，就返回 NULL，probe 直接返回 0。

这意味着：你写的 `i2c_driver` 永远不可能匹配到一个 adapter。它只能匹配到 client。

### 这有什么用？

它把"总线基础设施"和"总线上的设备"严格分开了。你不用担心自己写的 `i2c_driver` 会不小心绑定到控制器本身。控制器的 driver 是 `platform_driver`（在 platform_bus_type 上），和 i2c 设备 driver 是两套东西。

### 什么时候这个类比会误导你？

马路是被动的，但 adapter 不是完全被动的。它可以主动做很多事情：
- 发起总线恢复（`i2c_recover_bus()`）
- 标记自己 suspended，拒绝后续传输（`__i2c_check_suspended()` 返回 `-ESHUTDOWN`）
- 管理 runtime PM

所以 adapter 更像"有管理员驻守的收费站"，而不是一段没有生命的柏油路。

### 代码在哪

```
i2c-core-base.c:138-160      ← i2c_device_match() 过滤 adapter
i2c-core-base.c:488-496      ← i2c_device_probe() 过滤 adapter
i2c-core-base.c:708-713      ← i2c_client_type 定义
i2c-core-base.c:1378-1382    ← i2c_adapter_type 定义
```

---

## §4 bus_lock 为什么是 rt_mutex？

### 先举个例子

想象医院里的一个检查室，一次只能进一个病人。现在里面正在做一个普通检查（低优先级线程持有 `bus_lock`，做一次长的 I²C 传输）。突然一个急诊病人来了（高优先级 RT 线程也要用总线）。

如果用的是普通 mutex，急诊病人只能在外面等。更糟糕的是，如果医院里还有很多中等优先级的病人（不需要这个检查室的线程）在排队，他们会抢在急诊病人前面占用 CPU，导致正在检查的普通病人一直做不完。急诊病人就被无限期卡住了。这叫**优先级反转**。

`rt_mutex` 的做法是：医院直接把正在检查的普通病人**临时升级为急诊优先级**。这样中等优先级的病人就不能插队，普通病人尽快做完，急诊病人尽快进去。

这就是**优先级继承**：等待者的高优先级被临时"借给"持锁者。

### 为什么 I²C 特别需要这个？

I²C 传输可能很慢。标准模式 100kHz，一次 block read 可能要好几个毫秒。在实时系统里，一个低优先级的传感器轮询任务占着总线，一个高优先级的电机控制任务要用同一条总线发命令——如果没有优先级继承，电机控制可能被阻塞几十毫秒，这在实时场景里不可接受。

### 一个常见误解

有人看到 `bus_lock` 是 `rt_mutex`，以为是 RT 补丁改的。其实不是。上游 mainline 里 `i2c.h` 本来就是这样设计的：

```c
// include/linux/i2c.h:731-732
struct rt_mutex bus_lock;
struct rt_mutex mux_lock;
```

`git status` 显示 RT 补丁根本没碰过 `include/linux/i2c.h`。优先级继承对所有内核都有意义，不只是实时内核。

### 什么时候这个类比会误导你？

医院升级优先级是"优待"，但内核里这是**正确性要求**。没有优先级继承时，一个中等优先级线程可能永远抢占低优先级持锁线程，导致高优先级线程饿死。rt_mutex 不是让 VIP 插队，而是防止系统因为优先级安排不当而僵死。

### 代码在哪

```
include/linux/i2c.h:731-732   ← bus_lock / mux_lock 是 rt_mutex
i2c-core-base.c:1536-1537    ← 注册 adapter 时初始化这两把锁
```

---

## §5 mux-locked 和 parent-locked 有什么区别？

### 先举个例子

想象一家餐厅的后厨。

最开始只有一个大门。厨师要进去做菜，得先锁门，做完再出来。这就是 **parent-locked**：只要有一个厨师在用厨房，其他所有人都得等。

后来改进了：把厨房分成 4 个独立小隔间，每个隔间有自己的门。一个厨师在隔间 3 做菜时，另一个厨师可以同时进隔间 1 做菜。这就是 **mux-locked**。

I²C mux 就是这个厨房。parent 总线是厨房外面的大厅，每个子通道是一个隔间。

### 两种风格怎么选？

代码里靠 `i2c_mux_alloc()` 的 `flags` 参数决定：

```c
// PCA954x（最常见的 mux 芯片）：没传 I2C_MUX_LOCKED → parent-locked
i2c_mux_alloc(adap, dev, PCA954X_MAX_NCHANS, sizeof(*data), 0, ...);

// LTC4306：传了 I2C_MUX_LOCKED → mux-locked
i2c_mux_alloc(adap, dev, max, sizeof(*data), I2C_MUX_LOCKED, ...);
```

全树 22 个 mux 驱动里，只有 3 个用 mux-locked。为什么？因为 mux-locked 有个致命限制：**你在隔间里不能再去大厅拿东西**——也就是说，`select` 回调里不能发起新的 I²C 传输，否则会死锁。

大多数 mux 芯片（包括 PCA954x）是通过 I²C 命令切换通道的。如果 `select` 回调里再调一次 `i2c_smbus_xfer()`，就会尝试再锁一次 parent 总线——而 parent 总线已经被当前线程锁住了。死锁。

所以 PCA954x 在 `select` 里用的是 `__i2c_smbus_xfer()`（内部已持锁的版本），并且代码里专门写了注释警告这件事（`i2c-mux-pca954x.c:213-214`）。

### 什么时候选 mux-locked？

当你的 mux 不需要在 `select` 回调里做 I²C 传输时。比如某些 GPIO 控制的 mux，或者纯硬件切换的 mux。这种情况下，独立隔间能提高并发：4 个传感器可以同时读，而不是排队。

### 代码在哪

```
i2c-mux.c:143-151    ← mux-locked 的 lock_bus：只锁 mux_lock
i2c-mux.c:178-187    ← parent-locked 的 lock_bus：锁 mux_lock + parent 总线
i2c-mux.c:271-277    ← 两套 lock_ops
```

---

## §6 SMBus 仿真为什么有两条路？

### 先举个例子

想象一个万能遥控器，上面有"空调""电视""风扇"各种按钮（SMBus 操作：byte read、word write、block read）。但你家某些老设备其实只认识红外线脉冲序列（原始 I²C 传输）。

按下"空调温度+1"按钮时，万能遥控器内部会把这个按钮翻译成一组红外线脉冲发出去。对你来说是按了一个 SMBus 风格的按钮，对设备来说收到的是原始 I²C 信号。结果一样。

I²C 核心的 SMBus 仿真就是这个万能遥控器：

1. 你调用 `i2c_smbus_write_block_data(client, cmd, len, buf)`
2. 核心先问控制器："你会不会原生 SMBus？"（调 `adap->algo->smbus_xfer`）
3. 如果控制器说"不会"（返回 `-EOPNOTSUPP`），但控制器有 `master_xfer`（会原始 I²C）
4. 核心就把这个 SMBus 操作翻译成一组 `i2c_msg`，用 `master_xfer` 发出去

这个翻译函数就是 `i2c_smbus_xfer_emulated()`。

### 反过来也一样

如果控制器只实现了 SMBus（没有 `master_xfer`），你调 `i2c_transfer()` 做原始 I²C 传输，核心会直接返回 `-EOPNOTSUPP`。因为遥控器没有"原始红外线"按钮，它只能按预定义的功能键。

### `-EOPNOTSUPP` 在这里不是错误

这是很多新手会迷惑的地方。在 SMBus 双轨里，`-EOPNOTSUPP` 是**正常的控制流信号**，意思是"这条路不通，换一条"。它不是说"出了什么错"。

### 什么时候这个类比会误导你？

万能遥控器翻译后功能一样，但 I²C 的 SMBus 仿真有一个性能差异：**PEC（CRC-8 校验）**。如果原生 SMBus 控制器支持硬件 PEC，校验很快；如果是仿真路径，PEC 是软件算的（`i2c_smbus_msg_pec()`），会慢一点。但功能上是一样的。

### 代码在哪

```
i2c-core-smbus.c:578-598   ← 先试原生 SMBus，-EOPNOTSUPP 退回仿真
i2c-core-smbus.c:599-608   ← 退回仿真的条件
i2c-core-smbus.c:322-520   ← i2c_smbus_xfer_emulated()：协议翻译
i2c-core-base.c:2242-2244  ← 无 master_xfer 时返回 -EOPNOTSUPP
```

---

## §7 i2c-dev 里的匿名 client 是什么？

### 先举个例子

你去银行办一笔简单业务，比如交水电费。银行不会让你先开一个正式账户、给你一张银行卡、把你录入客户系统。它只给你一张临时排队号，叫到号就办，办完号码作废。

`i2c-dev` 的匿名 client 就是这张临时排队号。

当你 `open("/dev/i2c-0")` 时，内核干这些事：

1. 创建一个 `struct i2c_client`（`i2c-dev.c:602`）
2. **但不调用 `device_register()`**——它不在设备模型里
3. 没有 driver 会匹配它，sysfs 里也看不到
4. 你通过 `ioctl(fd, I2C_SLAVE, 0x50)` 告诉它目标地址
5. 你通过 `ioctl(fd, I2C_RDWR, ...)` 做传输
6. `close(fd)` 时直接 `kfree(client)`，没有 `device_unregister()`

### 为什么这样设计？

因为**用户空间自己就是驱动**。内核不需要为每个 userspace 程序分配一个 `i2c_driver`——userspace 程序通过 ioctl 直接告诉内核"我要对 0x50 地址读/写什么"，内核代理执行。

如果你非要把 userspace 程序也建模成内核 client driver，那每个 `open()` 都要走一次 probe、都要在 sysfs 里创建一个设备，太重了。

### 什么时候这个类比会误导你？

临时排队号办完事就作废，不会影响银行关门。但匿名 client 会**持有 adapter 的引用**（`i2c_get_adapter()` 在 open 时，`i2c_put_adapter()` 在 close 时）。只要有一个 fd 开着 `/dev/i2c-0`，对应的 adapter 模块就不能被卸载。所以它不是完全无状态的。

### 代码在哪

```
i2c-dev.c:585-613    ← i2cdev_open()：创建匿名 client
i2c-dev.c:598-601    ← 注释明确说 "NEVER REGISTERED"
i2c-dev.c:602        ← kzalloc 分配 client
i2c-dev.c:615        ← close 时直接释放
```

---

## §8 i2c_lock_addr 和 i2c_check_addr_busy 有什么不同？

### 先举个例子

想象公司有一个会议室系统，你要预约会议室。有两道检查：

**第一道：并发保护（`i2c_lock_addr`）**

防止两个人**同时**点击"预订 3 号会议室"导致重复预订。系统用一个原子位图记录"正在预订中"。谁先点到谁成功，后点的直接提示"操作冲突"（`-EBUSY`）。

注意：这只是**预订过程中的瞬间保护**。一旦预订完成（成功或失败），位图就清掉了。

**第二道：占用检查（`i2c_check_addr_busy`）**

检查 3 号会议室是不是**已经被别人长期占用了**。它会遍历所有已预订的会议室记录，看有没有冲突。如果公司有大楼（mux）结构，它还会检查整栋楼的父子会议室关系。

### 在代码里

当你创建一个 `i2c_client` 时，内核先调用 `i2c_lock_addr()` 占位，再调用 `i2c_check_addr_busy()` 检查长期占用：

```c
// i2c_new_client_device() 里
status = i2c_lock_addr(adap, client->addr, client->flags);   // :990
if (status)
    goto out_err_silent;

status = i2c_check_addr_busy(adap, i2c_encode_flags_to_addr(client));  // :995
if (status)
    goto out_err;
```

为什么要先 lock 再 check？因为 check 本身不是原子的。如果两个线程同时 check，可能都发现"没人占用"，然后都成功创建，导致地址冲突。lock 先占位，保证 check 期间不会有第三个线程也来创建同一个地址。

### 什么时候这个类比会误导你？

会议室预订成功后，这个会议室在整个会议期间都被占用。但 `i2c_lock_addr` 不是这样——它只在 `i2c_new_client_device()` 和 `i2c_unregister_device()` 执行期间有效，创建完就释放了。

日常的 I²C 传输（`i2c_transfer()`）**不检查地址**。两个 client driver 可以同时往各自地址发数据，只要它们不争同一把 `bus_lock`。地址冲突只在"注册设备"时有意义，在"使用设备"时不存在。

### 代码在哪

```
i2c-core-base.c:926-934     ← i2c_lock_addr()：test_and_set_bit，-EBUSY
i2c-core-base.c:825-842     ← i2c_check_addr_busy()：遍历 + mux 递归
i2c-core-base.c:990-995     ← 注册时先 lock 再 check
```

---

## 一些调 bug 时会用到的小结

| 现象 | 可能对应的概念 |
|---|---|
| `i2c_recover_bus()` 返回 `-EBUSY` | adapter 没有填 `bus_recovery_info`，见 §6 和 §7 |
| `i2c_transfer()` 返回 `-EOPNOTSUPP` | adapter 没有 `master_xfer`，见 §6 |
| 创建 client 报 `-EBUSY` | 地址被 `i2c_lock_addr` 占位或 `i2c_check_addr_busy` 命中，见 §8 |
| mux 下死锁 | 可能是 mux-locked 的 select 回调里又发起 I²C 传输，见 §5 |
| RT 系统里 I²C 响应抖动 | 检查 `bus_lock` 是不是 rt_mutex，以及有没有优先级反转，见 §4 |
| `/dev/i2c-0` 开着时 adapter 无法卸载 | 匿名 client 持有 adapter 引用，见 §7 |

## 读完这篇后该看哪篇？

| 你感兴趣的 | 去看 |
|---|---|
| adapter / algorithm 的完整字段 | [`02-datastructures.md`](02-datastructures.md) §1 |
| adapter 注册的完整流程 | [`03-internals.md`](03-internals.md) §A2 |
| mux 的锁风格选择与死锁陷阱 | [`04-extensions.md`](04-extensions.md) §1.4-1.5 |
| SMBus 仿真的完整协议翻译表 | [`03-internals.md`](03-internals.md) §A6 |
| i2c-dev 的 ioctl 完整列表 | [`01-api.md`](01-api.md) §10 |
| 地址冲突与探测的细节 | [`03-internals.md`](03-internals.md) §A3 |
