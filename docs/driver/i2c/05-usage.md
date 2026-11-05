---
title: 使用指南
---

# I2C 子系统（六）使用举例

> 本篇给可照抄的骨架。每段代码都标注了它对应的内核参考实现或文档，方便进一步核对。
> 全部示例基于 6.1.158 的 API 形态 —— 特别注意 6.1 正处在 `.probe` → `.probe_new` 的迁移期，新代码一律用 `.probe_new`（见 §1.4）。
>
> **关于这些 C 代码**：它们是按 6.1 的 API 手写的**示意骨架，没有编译过**（函数原型、结构体字段名、宏名逐个对着头文件核过，但 `/* ... */` 处是有意省略的，`myctl_do_msg()` 这类辅助函数也没给实现）。要能跑的完整代码，请直接读每节末尾标出的那个内核参考实现。

---

## 1. 写一个从设备（client）驱动

这是最常见的需求：板子上多了一颗 I²C 芯片，要为它写驱动。

### 1.1 最小骨架

```c
// SPDX-License-Identifier: GPL-2.0
/* 参考实现：Documentation/i2c/writing-clients.rst */

#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mod_devicetable.h>
#include <linux/slab.h>

#define FOO_REG_ID      0x00
#define FOO_REG_CTRL    0x01
#define FOO_REG_DATA    0x02
#define FOO_CHIP_ID     0x5a

struct foo_data {
	struct i2c_client *client;
	struct mutex lock;          /* 保护多寄存器访问的原子性 */
	u8 ctrl_shadow;
};

/* ---- 寄存器访问：优先用 SMBus helper，它们在纯 I²C 控制器上会自动仿真 ---- */

static int foo_read_reg(struct foo_data *foo, u8 reg)
{
	return i2c_smbus_read_byte_data(foo->client, reg);
}

static int foo_write_reg(struct foo_data *foo, u8 reg, u8 val)
{
	return i2c_smbus_write_byte_data(foo->client, reg, val);
}

/* ---- probe ---- */

static int foo_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct foo_data *foo;
	int ret;

	/* 1. 声明我们需要的总线能力，缺了就早退出 */
	if (!i2c_check_functionality(client->adapter,
				     I2C_FUNC_SMBUS_BYTE_DATA))
		return -EOPNOTSUPP;

	/* 2. devm 分配私有数据 */
	foo = devm_kzalloc(dev, sizeof(*foo), GFP_KERNEL);
	if (!foo)
		return -ENOMEM;

	foo->client = client;
	mutex_init(&foo->lock);
	i2c_set_clientdata(client, foo);

	/* 3. 探测芯片是否真的在（ID 寄存器） */
	ret = foo_read_reg(foo, FOO_REG_ID);
	if (ret < 0)
		return dev_err_probe(dev, ret, "failed to read chip ID\n");
	if (ret != FOO_CHIP_ID)
		return dev_err_probe(dev, -ENODEV,
				     "unexpected chip ID 0x%02x\n", ret);

	/* 4. 初始化硬件 */
	foo->ctrl_shadow = 0x01;
	ret = foo_write_reg(foo, FOO_REG_CTRL, foo->ctrl_shadow);
	if (ret < 0)
		return dev_err_probe(dev, ret, "init failed\n");

	/* 5. 注册到上层子系统（hwmon / iio / rtc / input ...） */
	/* ... */

	dev_info(dev, "foo chip at 0x%02x on %s\n",
		 client->addr, client->adapter->name);
	return 0;
}

static void foo_remove(struct i2c_client *client)
{
	struct foo_data *foo = i2c_get_clientdata(client);

	foo_write_reg(foo, FOO_REG_CTRL, 0);   /* 关掉硬件 */
}

/* ---- 匹配表：三张，覆盖三种枚举方式 ---- */

static const struct i2c_device_id foo_id[] = {
	{ "foo-chip", 0 },
	{ }
};
MODULE_DEVICE_TABLE(i2c, foo_id);

static const struct of_device_id foo_of_match[] = {
	{ .compatible = "vendor,foo-chip" },
	{ }
};
MODULE_DEVICE_TABLE(of, foo_of_match);

static struct i2c_driver foo_driver = {
	.driver = {
		.name           = "foo-chip",
		.of_match_table = foo_of_match,   /* arm64 上匹配靠这个 */
		/* foo_pm_ops 见 §1.5；实际代码里它必须定义在本结构之前 */
		.pm             = pm_ptr(&foo_pm_ops),
	},
	.probe_new = foo_probe,        /* 新式：不带 id 参数 */
	.remove    = foo_remove,
	.id_table  = foo_id,
};
module_i2c_driver(foo_driver);      /* 展开成 module_init/exit */

MODULE_AUTHOR("Someone <someone@example.com>");
MODULE_DESCRIPTION("Foo I2C chip driver");
MODULE_LICENSE("GPL");
```

### 1.2 `remove` 的返回值

**6.1 里 `i2c_driver::remove` 返回 `void`**（`include/linux/i2c.h:276`）。核心的 `i2c_device_remove()`（`i2c-core-base.c:622-644`）在 `:628-631` 直接调用、不接收返回值 —— 因为设备移除路径无法处理失败。老代码里 `int remove(...)` 的形式在 6.1 中已经改完（`void` 化就是 6.1 合并窗口做的）。

### 1.3 需要多寄存器原子访问时

单个 `i2c_smbus_*` 调用内部是加锁的（整条总线段），但**两个调用之间总线是开放的**。如果芯片有"先写页选择寄存器再读数据"这种依赖，必须自己加驱动级互斥：

```c
static int foo_read_paged(struct foo_data *foo, u8 page, u8 reg)
{
	int ret;

	mutex_lock(&foo->lock);
	ret = foo_write_reg(foo, FOO_REG_PAGE, page);
	if (ret == 0)
		ret = foo_read_reg(foo, reg);
	mutex_unlock(&foo->lock);

	return ret;
}
```

如果需要在**总线层面**保证不被打断（例如其它 client 也访问同一芯片），用组合传输：

```c
static int foo_read_block(struct foo_data *foo, u8 reg, u8 *buf, size_t len)
{
	struct i2c_msg msgs[2] = {
		{ .addr = foo->client->addr, .flags = 0,
		  .len = 1, .buf = &reg },
		{ .addr = foo->client->addr, .flags = I2C_M_RD,
		  .len = len, .buf = buf },
	};
	int ret;

	ret = i2c_transfer(foo->client->adapter, msgs, 2);
	if (ret < 0)
		return ret;
	if (ret != 2)
		return -EIO;      /* 部分成功一律当失败 */
	return 0;
}
```

这两条消息之间是 repeated-START，**总线不释放**，其它传输插不进来。

注意这条路径的能力要求和 §1.1 不一样：`i2c_transfer()` 走的是原生 I²C 消息，需要 `I2C_FUNC_I2C`；而 §1.1 的 probe 只检查了 `I2C_FUNC_SMBUS_BYTE_DATA`（纯 SMBus 控制器能满足后者、给不了前者）。驱动里两种方式都用的话，probe 里要把两个 flag 一起检查。

### 1.4 `.probe` vs `.probe_new`

| | `.probe(client, id)` | `.probe_new(client)` |
|---|---|---|
| 6.1 状态 | 仍支持，逐步淘汰 | 推荐 |
| 何时用 | 只在需要 `id->driver_data` 且不想自己查表时（同一驱动支持多型号、靠 id_table 携带参数） | 其余全部场合 |
| 怎么拿型号信息 | 参数直接给 | 自己查：`i2c_match_id()` 或 `device_get_match_data()` |

内核树里两种形态都能找到实例：`i2c-slave-testunit.c:184` 用 `.probe_new`；`i2c-slave-eeprom.c:209` 用 `.probe`，因为它靠 `id->driver_data` 编码 EEPROM 容量（`:147`）。

**`.probe_new` 里怎么拿到 `i2c_device_id`**，6.1 只有这两条路：

| 办法 | 位置 | 说明 |
|---|---|---|
| `i2c_match_id(foo_id, client)` | 声明 `include/linux/i2c.h:362`，实现 `i2c-core-base.c:104-117`（已 EXPORT） | 拿自己的 id_table 去比 `client->name`，返回匹配到的那一项；核心内部匹配走的就是它（`:127`、`:591`） |
| `device_get_match_data(dev)` / `of_device_get_match_data(dev)` | `drivers/base/property.c` | arm64/DT 上更常用：把 per-型号参数放进 `of_device_id::data`，就不需要 id_table 了 |

> ⚠️ 别写 `i2c_client_get_device_id()` —— **6.1 里没有这个函数**（全树无此符号，它是后来的版本才加的）。
> 也别和 `i2c_get_device_id()`（`i2c-core-base.c:2378-2400`）混：那个名字很像，但干的是完全不同的事 —— 它对 `I2C_ADDR_DEVICE_ID`（0x7c）发一次 SMBus 事务，**问硬件**要 manufacturer / part id / die revision，需要 `I2C_FUNC_SMBUS_READ_I2C_BLOCK`，和"驱动匹配到了哪一项"毫无关系。

### 1.5 电源管理

```c
static int foo_suspend(struct device *dev)
{
	struct foo_data *foo = dev_get_drvdata(dev);

	return foo_write_reg(foo, FOO_REG_CTRL, 0);
}

static int foo_resume(struct device *dev)
{
	struct foo_data *foo = dev_get_drvdata(dev);

	return foo_write_reg(foo, FOO_REG_CTRL, foo->ctrl_shadow);
}

static DEFINE_SIMPLE_DEV_PM_OPS(foo_pm_ops, foo_suspend, foo_resume);
```

**注意**：普通 suspend/resume 回调里可以访问 I²C —— client 的 dev.parent 就是 adapter 的 dev，PM 核心先挂子后挂父，所以轮到 client suspend 时总线还活着。

但**不要**在 `_noirq`（甚至 `_late`）阶段访问。控制器驱动一旦调了 `i2c_mark_adapter_suspended()`，之后所有传输在 `__i2c_check_suspended()`（`i2c-core.h:52-60`）里就直接 `-ESHUTDOWN` 并 `dev_WARN` 一次。**具体停在哪个阶段完全由控制器驱动决定**，arm64 上两种都有：

| 控制器 | 标记位置 | 后果 |
|---|---|---|
| `i2c-qcom-geni` | `geni_i2c_suspend_noirq()`（`:1008-1012`） | 到 `_noirq` 才关，`suspend`/`suspend_late` 里还能用 |
| `i2c-designware-platdrv` | `dw_i2c_plat_suspend()`（`:439-446`，普通 suspend 阶段） | 自己 suspend 完就关，client 的 `_late`/`_noirq` 一律失败 |

所以"能不能在 late/noirq 里访问"是不可移植的，别赌。

---

## 2. 把设备写进设备树

**DT 配置有独立一篇：[`06-devicetree.md`](06-devicetree.md)**。这里只给一个能直接跑的最小片段，属性含义、默认值、各属性由谁消费、以及配错了怎么定位，都在那一篇。

对应 §1 的驱动：

```dts
&i2c1 {
	status = "okay";                     /* SoC dtsi 里通常是 disabled，板级必须打开 */
	clock-frequency = <400000>;          /* Fast mode */
	pinctrl-names = "default";
	pinctrl-0 = <&pinctrl_i2c1>;

	foo@2a {
		compatible = "vendor,foo-chip";  /* → info.type = "foo-chip"（去厂商前缀） */
		reg = <0x2a>;                    /* 7 位地址，手册的 0x54 要写 0x2a */
		interrupt-parent = <&gpio3>;
		interrupts = <7 IRQ_TYPE_LEVEL_LOW>;
		vdd-supply = <&reg_3v3>;
	};
};
```

解析入口 `of_i2c_get_board_info()`（`i2c-core-of.c:22-62`），只有 41 行，逐属性对照表见 `06-devicetree.md` §3.1。

三个最常用的扩展写法，速查：

| 需求 | 写法 | 详见 |
|---|---|---|
| 10 位地址设备 | `reg = <(0x3ff \| I2C_TEN_BIT_ADDRESS)>;` | 06 §3.1 |
| 本机作为从设备 | `reg = <(0x64 \| I2C_OWN_SLAVE_ADDRESS)>;` | 06 §5.2、本篇 §6.2 |
| 中断走 SMBus Host Notify | `host-notify;` | 06 §3.3 |
| 可唤醒设备 | `wakeup-source;` | 06 §3.3 |
| 调总线时序 | `clock-frequency` + 7 个 `i2c-*`（含默认值表） | 06 §2.3 |
| 配总线恢复 | `scl-gpios`/`sda-gpios` + pinctrl `"gpio"` state | 06 §2.4 |
| mux / gate 拓扑 | `i2c-mux` / `i2c-gate` 子节点 | 06 §4、本篇 §5 |

时序字段的含义见 [`02-datastructures.md`](02-datastructures.md) §2.5（`struct i2c_timings`），解析代码是 `i2c_parse_fw_timings()`（`i2c-core-base.c:1952-1976`）。

---

## 3. 从用户态手工实例化设备（sysfs）

不改内核、不改 DT，临时在某条总线上创建一个设备：

```bash
# 查看有哪些总线
$ ls /sys/bus/i2c/devices/
i2c-0  i2c-1  1-0050

$ cat /sys/bus/i2c/devices/i2c-1/name
rk3x-i2c

# 在 i2c-1 的 0x2a 地址上实例化一个 "foo-chip"
$ echo foo-chip 0x2a > /sys/bus/i2c/devices/i2c-1/new_device

# 确认
$ ls /sys/bus/i2c/devices/
i2c-0  i2c-1  1-002a  1-0050
$ cat /sys/bus/i2c/devices/1-002a/name
foo-chip

# 删除（写地址，不是名字）
$ echo 0x2a > /sys/bus/i2c/devices/i2c-1/delete_device
```

实现：`new_device_store()`（`i2c-core-base.c:1255-1311`）与 `delete_device_store()`（`:1323-1366`）。
（这两个函数早年叫 `i2c_sysfs_new_device()` / `i2c_sysfs_delete_device()`，6.1 里已改名成 `*_store`，只有 `:1317` 的那句注释还留着老名字。查代码时按新名字搜。）

**四个要点**：

1. **`dummy` 不是被特殊处理的前缀**。核心自己注册了一个名叫 `"dummy"` 的 i2c 驱动（`:1112-1116`，在 `i2c_init()` 里 `:2113` 注册），它的 probe 是空函数。所以 `echo dummy 0x2a > new_device` 走的是**普通的 id_table 名字匹配**，效果是"这个地址被占了、但没人真的驱动它"，正好用于"我只想通过 `/dev/i2c-N` 访问它，同时防止别人抢"。内核代码里对应的 API 是 `i2c_new_dummy_device()`（`:1136`，两种用途写在 `:1128-1131` 的注释里）。
2. 地址可以带高位标志：`0xa3ff` → 10 位地址 0x3ff（`:1289-1292`），`0x1064` → 从机地址 0x64（`:1294-1297`）。这两个偏移量是 `I2C_ADDR_OFFSET_TEN_BIT` / `I2C_ADDR_OFFSET_SLAVE`（`:50-51`），**只在 sysfs 这条路上用**，和 DTS 里那两个宏不是同一个值（见 §6）。
3. `delete_device` 只能删**通过这个接口创建的** client，不能删 DT 枚举出来的。机制不是某个 flag，而是 adapter 上挂了一条 `userspace_clients` 链表：`new_device_store()` 在 `:1304-1306` 把 client 挂进去，`delete_device_store()` 在 `:1348-1358` 只遍历这条链表。**匹配用的是编码后的地址**（`i2c_encode_flags_to_addr(client) == addr`，`:1350`），所以删一个 10 位/从机设备也得写 `0xa3ff` / `0x1064`，写 `0x3ff` / `0x64` 会得到 `Can't find device in list`。
4. 两个属性都是 `S_IWUSR`（只有 root 能写；`new_device` 用 `DEVICE_ATTR_WO`，`:1312`，`delete_device` 显式写了 `S_IWUSR`，`:1367`），且**危险**：给错地址会让驱动去操作不相干的芯片。`Documentation/i2c/instantiating-devices.rst` 明确称之为 "last resort"。

---

## 4. 写一个控制器（adapter）驱动

### 4.1 有专用硬件的情形

```c
// SPDX-License-Identifier: GPL-2.0

#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>

struct myctl {
	struct i2c_adapter adap;
	struct device *dev;
	void __iomem *base;
	struct completion done;
	struct i2c_timings timings;
	int irq;
	bool atomic;                /* 供 §4.2 的原子路径用 */
};

/* ---- 核心真正强制的两件事之一：algo（另一件是 adap.name，见下文） ---- */
static int myctl_xfer(struct i2c_adapter *adap, struct i2c_msg *msgs, int num)
{
	struct myctl *ctl = i2c_get_adapdata(adap);
	int i, ret;

	ret = pm_runtime_resume_and_get(ctl->dev);
	if (ret < 0)
		return ret;

	for (i = 0; i < num; i++) {
		/*
		 * 一条消息的典型流程：
		 *   - 首条发 START，后续发 repeated-START
		 *   - 写地址 + 方向位：i2c_8bit_addr_from_msg(&msgs[i])
		 *   - 等 ACK；NAK → -ENXIO（除非 I2C_M_IGNORE_NAK）
		 *   - 逐字节收发，等中断，wait_for_completion_timeout()
		 *   - 最后一条消息后发 STOP
		 *
		 * 不需要在这里做：重试、超时的整体计时、quirks 检查、
		 *                 tracepoint —— 核心已经做了。
		 */
		ret = myctl_do_msg(ctl, &msgs[i], i == num - 1);
		if (ret)
			goto out;
	}
	ret = num;                 /* 成功时返回处理的消息数 */
out:
	pm_runtime_mark_last_busy(ctl->dev);
	pm_runtime_put_autosuspend(ctl->dev);
	return ret;
}

/* ---- 事实上也必须实现：functionality（不实现就是空指针，见下文） ---- */
static u32 myctl_func(struct i2c_adapter *adap)
{
	return I2C_FUNC_I2C | I2C_FUNC_SMBUS_EMUL | I2C_FUNC_10BIT_ADDR;
	/* I2C_FUNC_SMBUS_EMUL 表示"我能承载任意 I²C 消息，所以核心可以
	 * 用消息序列仿真出 SMBus 事务" —— 不是说我原生支持 SMBus。 */
}

static const struct i2c_algorithm myctl_algo = {
	.master_xfer   = myctl_xfer,
	.functionality = myctl_func,
};

/* 可选：声明硬件缺陷，让核心提前挡掉不可能的传输。
 * 各字段填 0 = 不限制；max_comb_*_msg_len 只有在 flags 里带了
 *   I2C_AQ_COMB 时才会被检查（见下文），所以这里必须一起写上。 */
static const struct i2c_adapter_quirks myctl_quirks = {
	.flags          = I2C_AQ_NO_ZERO_LEN | I2C_AQ_COMB_WRITE_THEN_READ,
	.max_write_len  = 255,
	.max_read_len   = 255,
	.max_comb_1st_msg_len = 1,      /* 只允许"写 1 字节寄存器号再读" */
	.max_comb_2nd_msg_len = 255,
	/* max_num_msgs 不用写：I2C_AQ_COMB 已经隐含 max_num = 2 */
};

static int myctl_probe(struct platform_device *pdev)
{
	struct myctl *ctl;
	int ret;

	ctl = devm_kzalloc(&pdev->dev, sizeof(*ctl), GFP_KERNEL);
	if (!ctl)
		return -ENOMEM;
	ctl->dev = &pdev->dev;

	ctl->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(ctl->base))
		return PTR_ERR(ctl->base);

	ctl->irq = platform_get_irq(pdev, 0);
	if (ctl->irq < 0)
		return ctl->irq;
	ret = devm_request_irq(&pdev->dev, ctl->irq, myctl_isr, 0,
			       dev_name(&pdev->dev), ctl);
	if (ret)
		return ret;

	/* 从设备树读时序参数。第三参数 use_defaults：
	 * true  = DT 里没写的属性按速率档填默认值（新驱动几乎都该用 true，
	 *         这是 i2c-core-base.c:1937 的 doc comment 明说的）
	 * false = 没写的一律留 0，由驱动自己判断
	 * 这个函数核心不会替你调，必须驱动自己调；属性表见 06 §2.3 */
	i2c_parse_fw_timings(&pdev->dev, &ctl->timings, true);
	myctl_setup_clock(ctl);

	/* ---- 填 adapter ---- */
	ctl->adap.owner   = THIS_MODULE;
	ctl->adap.algo    = &myctl_algo;
	ctl->adap.quirks  = &myctl_quirks;
	ctl->adap.dev.parent  = &pdev->dev;
	ctl->adap.dev.of_node = pdev->dev.of_node;
	/* 不要在这里设 adap.nr：i2c_add_adapter()（含 devm 版）根本不读它，
	 *   编号一律由核心定 —— DT 上先试 of_alias_get_id(of_node, "i2c")
	 *   （i2c-core-base.c:1654），拿不到才 idr_alloc 动态分配（:1662）。
	 *   真要固定编号只能改用 i2c_add_numbered_adapter()（:1697，无 devm 版）。 */
	ctl->adap.retries = 3;
	ctl->adap.timeout = msecs_to_jiffies(100);
	strscpy(ctl->adap.name, "My I2C adapter", sizeof(ctl->adap.name));
	i2c_set_adapdata(&ctl->adap, ctl);

	platform_set_drvdata(pdev, ctl);
	pm_runtime_enable(&pdev->dev);

	/* devm 版本：卸载时自动 i2c_del_adapter */
	ret = devm_i2c_add_adapter(&pdev->dev, &ctl->adap);
	if (ret) {
		pm_runtime_disable(&pdev->dev);
		return ret;
	}
	return 0;
}
```

**`i2c_add_adapter()` 的时机很关键**：它一返回，DT 枚举出来的所有 client 就会开始 probe，也就是**会立刻有真实的 I²C 传输进来**。所以硬件必须在调用之前完全就绪。

关于上面三个 ，逐个把依据摊开：

| 说法 | 依据 |
|---|---|
| 核心只强制 `adap->name[0]` 和 `adap->algo` | `i2c_register_adapter()`（`i2c-core-base.c:1524-1531`）：没名字 `WARN`，没 algo `pr_err`，都直接 `-EINVAL`；`adap->timeout` 为 0 时核心补成 `HZ`（`:1543-1544`），`lock_ops` 为空时补默认（`:1533-1534`） |
| `functionality` 事实上也必须实现 | 核心不检查它，但 `i2c_check_functionality()` → `i2c_get_functionality()`（`include/linux/i2c.h:909-918`）是 `adap->algo->functionality(adap)` 的**直接解引用**。留空 = 任何 client 一查能力就空指针 |
| `max_comb_*_msg_len` 需要 `I2C_AQ_COMB` 才生效 | `i2c_check_for_quirks()`（`:2171-2223`）：这两个字段的检查在 `if (q->flags & I2C_AQ_COMB)` 且 `num == 2` 的分支里（`:2177-2198`），并且该分支会把 `do_len_check` 置 false —— 也就是**组合传输走 comb 长度，不走 `max_write_len`/`max_read_len`**。`I2C_AQ_COMB` 还会把 `max_num` 强制成 2（`:2178`） |

**arm64 上的总线编号**从哪来：`aliases { i2c1 = &i2c1; }` 这种 alias 节点。有 alias 就是 `i2c-1`，没有就是"当前空闲的最小号"，所以**不要在代码或脚本里写死 `/dev/i2c-3`**（这也是 §7 的例子都先 `i2cdetect -l` 的原因）。

### 4.2 支持原子传输

关机、reboot、panic 路径上要通过 PMIC 关电源，此时中断已关、不能睡眠。核心用 `i2c_in_atomic_xfer_mode()`（`i2c-core.h:31`）识别这种状态并调 `master_xfer_atomic`：

```c
static int myctl_xfer_atomic(struct i2c_adapter *adap,
			     struct i2c_msg *msgs, int num)
{
	struct myctl *ctl = i2c_get_adapdata(adap);
	int ret;

	ctl->atomic = true;         /* 让 ISR 路径改为轮询 */
	ret = myctl_xfer_common(ctl, msgs, num);
	ctl->atomic = false;
	return ret;
}
```

要点：**不能 `wait_for_completion()`，改成 `readl_poll_timeout_atomic()` 之类的忙等**。

**不实现这个回调会怎样**（三步，都在源码里）：

1. `__i2c_lock_bus_helper()`（`i2c-core.h:37-50`）发现处于原子模式且两个 `*_atomic` 回调都为空，打一次
   `WARN`：**`"No atomic I2C transfer handler for '<dev>'"`**（`:41-43`）；
2. 然后它只做 `i2c_trylock_bus()`。**抢不到锁就直接返回 `-EAGAIN`**（`:44`）—— 这是唯一"干净失败"的分支；
3. 抢到锁的话，`__i2c_transfer()` 在 `:2274-2277` 那个三元判断里因为 `master_xfer_atomic == NULL` **退回去调普通的 `master_xfer`**。也就是说核心**警告完照样硬上**，接下来是不可睡眠上下文里执行会睡眠的代码 —— 表现是 "scheduling while atomic"/看门狗，而不是一个返回值。

所以"没实现就返回错误"这个直觉是不对的：核心的态度是"我警告你了，出事你负责"。

algo-bit 也是同一个态度，且**它有自己的第二条警告**：`i2c_bit_algo` 是 `const`，`.master_xfer_atomic` 永远非空，所以走到 `bit_xfer_atomic()` 时它看 `bit_data->can_do_atomic`，为假就 `dev_warn("not flagged for atomic transfers")` 然后照样执行（`i2c-algo-bit.c:610-621`，注释里明说 "will try anyhow. That's what the I2C core would do as well"）。

顺一句反例：原子回调也不是万能的。`i2c-qcom-geni` 在 `.shutdown` 里直接 `i2c_mark_adapter_suspended()`（`i2c-qcom-geni.c:953-959`，注释就是 "Make client i2c transfers start failing"），此后这条总线上**任何**传输都在 `__i2c_check_suspended()` 里变成 `-ESHUTDOWN`，原子不原子都一样。要在关机路径上操作 PMIC，得确认这条总线的驱动没有这么干。

### 4.3 用 algo-bit 做一个位操作控制器

只有两根 GPIO 时（或者调试用），不用自己写协议：

```c
#include <linux/i2c-algo-bit.h>

struct bb {
	struct i2c_adapter adap;
	struct i2c_algo_bit_data bit;
	struct gpio_desc *sda, *scl;
};

static void bb_setsda(void *data, int state)
{
	struct bb *bb = data;
	/* 开漏语义：state=1 时释放（设为输入或输出高阻），state=0 时拉低 */
	gpiod_set_value_cansleep(bb->sda, state);
}
static void bb_setscl(void *data, int state)
{
	struct bb *bb = data;
	gpiod_set_value_cansleep(bb->scl, state);
}
static int bb_getsda(void *data)
{
	struct bb *bb = data;
	return gpiod_get_value_cansleep(bb->sda);
}
static int bb_getscl(void *data)     /* 尽量实现，否则失去时钟拉伸支持 */
{
	struct bb *bb = data;
	return gpiod_get_value_cansleep(bb->scl);
}

static int bb_probe(struct platform_device *pdev)
{
	struct bb *bb = /* devm_kzalloc + 取两个 gpiod */;

	bb->bit.data    = bb;
	bb->bit.setsda  = bb_setsda;
	bb->bit.setscl  = bb_setscl;
	bb->bit.getsda  = bb_getsda;
	bb->bit.getscl  = bb_getscl;
	bb->bit.udelay  = 5;                     /* µs：5 → ~100 kHz */
	bb->bit.timeout = HZ / 10;               /* 等 SCL 变高的超时（jiffies） */
	/* bb->bit.can_do_atomic 这里必须保持 false：上面四个回调用的是
	 * *_cansleep() 版本。置 true 只是"我保证回调不睡眠"的声明，
	 * 声明错了不会被拦，只会在原子路径上真的睡下去。 */

	bb->adap.owner      = THIS_MODULE;
	bb->adap.algo_data  = &bb->bit;          /* algo 由 i2c_bit_add_bus 填 */
	bb->adap.dev.parent = &pdev->dev;
	bb->adap.dev.of_node = pdev->dev.of_node;
	strscpy(bb->adap.name, "bitbanged i2c", sizeof(bb->adap.name));

	return i2c_bit_add_bus(&bb->adap);       /* 内部会 i2c_add_adapter */
}
```

`udelay` 是**半个时钟周期**的微秒数，取值规则在 `include/linux/i2c-algo-bit.h:31-34` 的注释里：fast-mode 最小 2 µs，standard-mode / SMBus 最小 5 µs，SMBus 最大 50 µs。所以 5 µs ≈ 100 kHz。

真实参考实现：`drivers/i2c/busses/i2c-gpio.c`。

---

## 5. 写一个 mux 驱动

以"用一个 MMIO 寄存器切换 4 个通道"为例（参考 `drivers/i2c/muxes/i2c-mux-reg.c`）：

```c
#include <linux/i2c-mux.h>

struct mymux {
	void __iomem *reg;
	u32 last;                         /* U32_MAX = "当前不确定" */
};

static int mymux_select(struct i2c_mux_core *muxc, u32 chan)
{
	struct mymux *mux = i2c_mux_priv(muxc);

	if (mux->last == chan)
		return 0;                 /* 已经在这个通道，省一次写 */
	writel(chan, mux->reg);
	mux->last = chan;
	return 0;
}

static int mymux_deselect(struct i2c_mux_core *muxc, u32 chan)
{
	struct mymux *mux = i2c_mux_priv(muxc);

	writel(0xff, mux->reg);           /* 全断开 */
	mux->last = U32_MAX;
	return 0;
}

static int mymux_probe(struct platform_device *pdev)
{
	struct i2c_adapter *parent;
	struct i2c_mux_core *muxc;
	struct mymux *mux;
	int i, ret;

	parent = i2c_get_adapter(/* 从 DT 的 i2c-parent 解析 */);
	if (!parent)
		return -EPROBE_DEFER;

	/* flags = 0 → parent-locked，也就是默认值。判据见下表：这里的
	 * select 只写 MMIO，不需要发 I²C，所以没有任何理由退让成 mux-locked。
	 * sizeof_priv 传非 0 → 核心把 priv 连在 muxc 后面一起分配，
	 * 用 i2c_mux_priv() 取（另一种写法是传 0 再手动赋 muxc->priv，
	 * i2c-mux-reg.c:201-207 走的是后者，两者都对，见 04 §1.2）。 */
	muxc = i2c_mux_alloc(parent, &pdev->dev, 4 /* max_adapters */,
			     sizeof(*mux), 0,
			     mymux_select, mymux_deselect);
	if (!muxc) {
		i2c_put_adapter(parent);
		return -ENOMEM;
	}
	mux = i2c_mux_priv(muxc);
	mux->reg = devm_platform_ioremap_resource(pdev, 0);
	mux->last = U32_MAX;
	platform_set_drvdata(pdev, muxc);

	for (i = 0; i < 4; i++) {
		ret = i2c_mux_add_adapter(muxc, 0 /* force_nr: 0 = 自动 */,
					  i /* chan_id */, 0 /* class */);
		if (ret) {
			i2c_mux_del_adapters(muxc);
			i2c_put_adapter(parent);
			return ret;
		}
	}
	return 0;
}

static int mymux_remove(struct platform_device *pdev)
{
	struct i2c_mux_core *muxc = platform_get_drvdata(pdev);

	i2c_mux_del_adapters(muxc);
	i2c_put_adapter(muxc->parent);
	return 0;
}
```

对应的 DT：

```dts
/* 这是个平台设备（MMIO），所以节点写在 soc 下面，不在 i2c 总线里 */
i2c-mux@12340000 {
	compatible = "vendor,my-i2c-mux";
	reg = <0x12340000 0x4>;          /* unit-address 必须等于 reg */
	i2c-parent = <&i2c1>;
	#address-cells = <1>;
	#size-cells = <0>;

	i2c@0 {
		reg = <0>;
		#address-cells = <1>;
		#size-cells = <0>;
		temp@48 { compatible = "national,lm75"; reg = <0x48>; };
	};
	i2c@1 {
		reg = <1>;
		#address-cells = <1>;
		#size-cells = <0>;
		temp@48 { compatible = "national,lm75"; reg = <0x48>; };
	};
	/* ... 通道 2、3 */
};
```

上面这份 DT 把四个通道写成了 mux 节点的**直接子节点**，没有 `i2c-mux` 分组节点 —— 这是"老式"写法，仍然有效：`i2c_mux_add_adapter()` 找不到名为 `i2c-mux` 的子节点时会退回到 `dev_node` 本身，再在它的直接子节点里按 `reg` 匹配 `chan_id`（`i2c-mux.c:361-388`）。两种写法的取舍见 [`06-devicetree.md`](06-devicetree.md) §4.2 / §4.3。

**锁风格选择的判据**（详见 [`04-extensions.md`](04-extensions.md) §1.4）。注意判据不是"select 走不走 I²C"，而是"**select 能不能用 `__` 前缀的免锁传输**"：

| select 的实现方式 | 选什么 | 原因 |
|---|---|---|
| MMIO / GPIO / pinctrl（本例） | parent-locked（`flags = 0`，默认） | 压根不碰 I²C，父总线锁着也无所谓 |
| 自己写 mux 芯片寄存器，能调 `__i2c_transfer()` / `__i2c_smbus_xfer()` | **仍然是 parent-locked** | pca954x 就是这样（`i2c-mux-pca954x.c:220`）：走 I²C 但用免锁变体，于是能拿到更强的独占性 |
| 切换要经过 regmap，或经过某个子系统自己的"已加锁"封装 | 被迫 `I2C_MUX_LOCKED` | 那些封装内部会再上一次父总线锁，parent-locked 下必然自锁死 |

也就是说 **mux-locked 是一种让步，不是一种优化**。选它之前先确认"我真的拿不到免锁的传输路径"。

---

## 6. 写一个 slave 后端

模拟一颗"读会返回递增计数器、写会重置计数器"的芯片：

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/i2c.h>
#include <linux/module.h>

struct counter_data {
	u8 count;
};

static int counter_slave_cb(struct i2c_client *client,
			    enum i2c_slave_event event, u8 *val)
{
	struct counter_data *c = i2c_get_clientdata(client);

	switch (event) {
	case I2C_SLAVE_WRITE_REQUESTED:
		/* 对端要写我们，还没有数据 —— 复位写状态 */
		break;

	case I2C_SLAVE_WRITE_RECEIVED:
		/* 收到一个字节，*val 是数据 */
		c->count = *val;
		break;

	case I2C_SLAVE_READ_PROCESSED:
		/* 上一个字节已经上了总线，现在准备下一个。
		 * 只在这里推进状态，不在 READ_REQUESTED 里推进。 */
		c->count++;
		fallthrough;

	case I2C_SLAVE_READ_REQUESTED:
		*val = c->count;
		/* 不要在这里 ++：这个字节可能不会被对端取走 */
		break;

	case I2C_SLAVE_STOP:
		break;

	default:
		break;
	}
	return 0;
}

static int counter_probe(struct i2c_client *client)
{
	struct counter_data *c;

	c = devm_kzalloc(&client->dev, sizeof(*c), GFP_KERNEL);
	if (!c)
		return -ENOMEM;
	i2c_set_clientdata(client, c);

	return i2c_slave_register(client, counter_slave_cb);
}

static void counter_remove(struct i2c_client *client)
{
	i2c_slave_unregister(client);
}

static const struct i2c_device_id counter_id[] = {
	{ "slave-counter", 0 },
	{ }
};
MODULE_DEVICE_TABLE(i2c, counter_id);

static struct i2c_driver counter_driver = {
	.driver = { .name = "i2c-slave-counter" },
	.probe_new = counter_probe,
	.remove    = counter_remove,
	.id_table  = counter_id,
};
module_i2c_driver(counter_driver);
MODULE_LICENSE("GPL");
```

**回调上下文**：通常是硬中断上下文。所以：

- 不能睡眠、不能分配 `GFP_KERNEL` 内存、不能调 `i2c_transfer()`；
- 要做需要睡眠的事，派 workqueue —— 参见 `i2c-slave-testunit.c` 的做法（[`04-extensions.md`](04-extensions.md) §3.4）；
- 与主机侧访问（sysfs、ioctl）共享数据时用 `spin_lock_irqsave` —— 参见 `i2c-slave-eeprom.c:101`。

**启用方式**：

```dts
&i2c3 {
	counter@64 {
		compatible = "slave-counter";
		reg = <(0x64 | I2C_OWN_SLAVE_ADDRESS)>;
	};
};
```

上面这个驱动**没有 `of_match_table`，却能被 DT 匹配上**，不是笔误：`i2c_device_match()` 先试 OF、再试 ACPI，最后退回 `i2c_match_id(driver->id_table, client)`（`i2c-core-base.c:155`）；而 `client->name` 是 `of_modalias_node()` 从 compatible 里取逗号后半段得来的（没有逗号就取整串），所以 `"slave-counter"` 正好命中 id_table。`i2c-slave-eeprom.c` / `i2c-slave-testunit.c` 用的都是这套 —— 它们也都没有 `of_match_table`。代价是这种 compatible 不带厂商前缀，过不了 `dtbs_check`，只适合本地/调试用途。

或者用现成的 EEPROM 后端 + sysfs：

```bash
# 在 i2c-3 的 0x64 上模拟一颗 24c02（256 字节）
$ echo slave-24c02 0x1064 > /sys/bus/i2c/devices/i2c-3/new_device
#                    ^^^^ 0x1000 = I2C_ADDR_OFFSET_SLAVE

# 从主机侧读写这块"EEPROM"
$ hexdump -C /sys/bus/i2c/devices/3-1064/slave-eeprom
$ echo -n "hello" | dd of=/sys/bus/i2c/devices/3-1064/slave-eeprom bs=1 seek=0
```

三个细节，都容易记混：

- **`0x1000` 不是 DTS 里那个宏**。sysfs 这条路用的是 `I2C_ADDR_OFFSET_SLAVE = 0x1000`（`i2c-core-base.c:51`，解析在 `new_device_store()` `:1294-1297`）；DTS 那条路用的是 `I2C_OWN_SLAVE_ADDRESS = (1 << 30)`（`include/dt-bindings/i2c/i2c.h:15`，解析在 `of_i2c_get_board_info()` `i2c-core-of.c:41-49`）。两个常量值不同、解析代码也不同，**只有终点一致**：都是 `info.flags |= I2C_CLIENT_SLAVE`。
- **`3-1064` 这个目录名和你怎么创建它无关**。`i2c_dev_set_name()`（`:876-894`）对所有 client 都用 `"%d-%04x"`，其中地址是 `i2c_encode_flags_to_addr()`（`:735-747`）编码过的 —— 从机加 `0x1000`、10 位加 `0xa000`。所以 DT 枚举出来的那个从机设备，目录名同样是 `3-1064`。
- sysfs 里那个二进制文件名是驱动自己起的：`eeprom->bin.attr.name = "slave-eeprom"`（`i2c-slave-eeprom.c:165`）。容量由 id_table 编码（`slave-24c02` → 256 字节，`:147` 解出 `I2C_SLAVE_BYTELEN`）。

---

## 7. 用户态访问 `/dev/i2c-N`

### 7.1 命令行（i2c-tools）

```bash
# 列出所有总线
$ i2cdetect -l
i2c-0	i2c       	rk3x-i2c                  	I2C adapter
i2c-1	i2c       	i2c-0-mux (chan_id 0)     	I2C adapter

# 扫描 i2c-0 上有哪些地址有响应（-y 跳过确认）
$ i2cdetect -y 0
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:                         -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
...
50: 50 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
...
# UU 表示该地址已被内核驱动占用（i2cdetect 会跳过，避免打扰）

# 读一个字节（SMBus Read Byte Data）
$ i2cget -y 0 0x50 0x00
0x5a

# 写一个字节
$ i2cset -y 0 0x50 0x01 0xff

# 连续读 16 字节
$ i2cdump -y -r 0-15 0 0x50

# 复合传输（先写 0x10，再读 4 字节）
$ i2ctransfer -y 0 w1@0x50 0x10 r4
```

**危险性**：`i2cset`/`i2ctransfer` 可以往任意地址写任意数据。在有 SPD（内存条）或 PMIC 的总线上误写会导致硬件损坏或系统立刻断电。`Documentation/i2c/dev-interface.rst` 和 `i2cdetect` 的手册都有明确警告。

### 7.2 C 代码

```c
// SPDX-License-Identifier: GPL-2.0
/* 参考：Documentation/i2c/dev-interface.rst */

#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>

#define BUS   "/dev/i2c-0"
#define ADDR  0x50

int main(void)
{
	int fd, ret;
	unsigned long funcs;

	fd = open(BUS, O_RDWR);
	if (fd < 0) { perror("open"); return 1; }

	/* ---- 1. 查询能力（可选但推荐） ---- */
	if (ioctl(fd, I2C_FUNCS, &funcs) < 0) { perror("I2C_FUNCS"); goto out; }
	printf("funcs = 0x%08lx  (I2C=%d SMBUS_BYTE_DATA=%d)\n", funcs,
	       !!(funcs & I2C_FUNC_I2C),
	       !!(funcs & I2C_FUNC_SMBUS_BYTE_DATA));

	/* ---- 2. 绑定从设备地址 ---- */
	if (ioctl(fd, I2C_SLAVE, ADDR) < 0) {
		perror("I2C_SLAVE");        /* -EBUSY = 已有内核驱动占用 */
		goto out;
	}
	/* 若确实想抢占（危险），用 I2C_SLAVE_FORCE */

	/* ---- 3a. 方式一：read()/write() 裸字节流 ---- */
	{
		unsigned char reg = 0x00, buf[4];

		if (write(fd, &reg, 1) != 1)      { perror("write"); goto out; }
		if (read(fd, buf, 4)  != 4)       { perror("read");  goto out; }
		printf("regs: %02x %02x %02x %02x\n",
		       buf[0], buf[1], buf[2], buf[3]);
		/* 注意：write 和 read 之间会有 STOP，不是 repeated-START */
	}

	/* ---- 3b. 方式二：I2C_RDWR 组合传输（有 repeated-START） ---- */
	{
		unsigned char reg = 0x00, buf[4] = { 0 };
		struct i2c_msg msgs[2] = {
			{ .addr = ADDR, .flags = 0,        .len = 1, .buf = &reg },
			{ .addr = ADDR, .flags = I2C_M_RD, .len = 4, .buf = buf  },
		};
		struct i2c_rdwr_ioctl_data data = { .msgs = msgs, .nmsgs = 2 };

		ret = ioctl(fd, I2C_RDWR, &data);
		if (ret < 0) { perror("I2C_RDWR"); goto out; }
		printf("rdwr got %d msgs: %02x %02x %02x %02x\n",
		       ret, buf[0], buf[1], buf[2], buf[3]);
	}

	/* ---- 3c. 方式三：I2C_SMBUS ioctl ---- */
	{
		union i2c_smbus_data d;
		struct i2c_smbus_ioctl_data args = {
			.read_write = I2C_SMBUS_READ,
			.command    = 0x00,
			.size       = I2C_SMBUS_BYTE_DATA,
			.data       = &d,
		};

		if (ioctl(fd, I2C_SMBUS, &args) < 0) { perror("I2C_SMBUS"); goto out; }
		printf("smbus byte = 0x%02x\n", d.byte);
	}

	/* ---- 4. 其它可调参数 ---- */
	/* 前两个改的是 adapter 上的字段（i2c-dev.c:472-486），不是 per-fd：
	 *   会影响这条总线上所有使用者，而且你关闭 fd 后也不会恢复。 */
	ioctl(fd, I2C_RETRIES, 3);      /* → adapter->retries */
	ioctl(fd, I2C_TIMEOUT, 20);     /* → adapter->timeout，单位 10ms，这里 200ms */
	ioctl(fd, I2C_PEC, 1);          /* 这个才是 per-client 的：打开 SMBus PEC */

out:
	close(fd);
	return 0;
}
```

（在实际项目里通常直接用 i2c-tools 提供的 `libi2c` / `i2c/smbus.h` 封装，就不用手写 `struct i2c_smbus_ioctl_data`。）

### 7.3 三种访问方式的选择

| | `read()`/`write()` | `I2C_RDWR` | `I2C_SMBUS` |
|---|---|---|---|
| 需要先 `I2C_SLAVE` | 是 | 否（消息自带地址） | 是 |
| repeated-START | 不可能 | 支持 | 由核心决定 |
| 多设备交替访问 | 需反复改地址 | 一个 fd 搞定 | 需反复改地址 |
| 单次消息数上限 | 1 | 42（`I2C_RDWR_IOCTL_MAX_MSGS`，`include/uapi/linux/i2c-dev.h:55`；超了 `-EINVAL`，`i2c-dev.c:450`） | 不适用：核心自己组装 1~2 条消息 |
| 单条消息长度上限 | 8192，但**是静默截断不是报错**（`i2c-dev.c:142-143` 读 / `:166-167` 写：`if (count > 8192) count = 8192;`） | 每条 8192，超了整个 ioctl `-EINVAL`（`:250-253`） | 32（`I2C_SMBUS_BLOCK_MAX`，`uapi/linux/i2c.h:140`） |
| PEC | 不支持 | 不支持 | 支持 |
| 需要的能力 | `I2C_FUNC_I2C` | `I2C_FUNC_I2C` | 对应的 `I2C_FUNC_SMBUS_*` |

那个"静默截断"值得记一下：`read(fd, buf, 10000)` 不会失败，它只读 8192 字节然后返回 8192。按返回值判断就没事，按"我要的长度"判断就会错。

### 7.4 一个易踩的坑：`I2C_M_RECV_LEN`

用 `I2C_RDWR` 做 SMBus Block Read（长度由从设备返回）时，缓冲区必须足够大，否则 `-EINVAL`：

```c
/* i2c-dev.c:275-283 的检查，四个条件全部要满足：
 *   1) 必须同时带 I2C_M_RD（只读方向有意义）
 *   2) msgs[i].len >= 1
 *   3) msgs[i].buf[0] >= 1
 *   4) msgs[i].len >= msgs[i].buf[0] + I2C_SMBUS_BLOCK_MAX
 * buf[0] 的含义是"额外字节数"：至少 1（放长度字节），
 * 也可以更大（比如再留一个字节放校验和），不是固定写 1。
 */
unsigned char buf[1 + I2C_SMBUS_BLOCK_MAX];
buf[0] = 1;
struct i2c_msg msg = {
	.addr = ADDR, .flags = I2C_M_RD | I2C_M_RECV_LEN,
	.len = sizeof(buf), .buf = buf,
};
```

两个补充：

- **内核会改写 `msg.len`**：检查通过后立刻 `msgs[i].len = msgs[i].buf[0]`（`i2c-dev.c:285`），之后控制器驱动在收到长度字节时再把它加上去。所以 ioctl 返回后 `msg.len` 不是"读到了多少字节"，真正的长度看 `buf[0]`。别拿这个 `len` 当返回长度用。
- **控制器必须声明 `I2C_FUNC_SMBUS_READ_BLOCK_DATA`**，这是能力位和消息标志的绑定关系，写在两个定义旁边（`include/uapi/linux/i2c.h:79`：`use only if I2C_FUNC_SMBUS_READ_BLOCK_DATA`；`:106`：`required for I2C_M_RECV_LEN`）。但**核心不替你检查**：`I2C_RDWR` 路径上没有任何地方拿 `I2C_M_RECV_LEN` 去比对 `functionality`，核心侧只有 `i2c-dev.c:275-285` 那段缓冲区检查和 `i2c-core-smbus.c:397`/`:428`（SMBus 仿真路径自己置位）。**真正认这个标志的是控制器驱动自己**：`drivers/i2c/busses/` 下有 19 个文件处理它（arm64 常见的 `i2c-imx.c`、`i2c-tegra.c`、`i2c-rcar.c`、`i2c-qup.c`、`i2c-designware-master.c`、`i2c-cadence.c`、`i2c-aspeed.c` 都在其中），bit-banging 则在 `algos/i2c-algo-bit.c:438`。不在这个名单里的控制器会把它当普通读，而此时 `len` 已经被改成 `buf[0]`（通常是 1），于是你只会读到 1 个字节、还以为传输成功了。所以要自己先查能力：内核里用 `i2c_check_functionality()`，用户态用 `I2C_FUNCS` ioctl 或 `i2cdetect -F N`。（顺便：适配器在 sysfs 里**只有** `name`、`new_device`、`delete_device` 三个属性，见 `i2c_adapter_attrs[]`，`i2c-core-base.c:1370-1375`，没有 `functionality` 节点可以 `cat`。）

---

## 8. 无硬件测试：i2c-stub

开发芯片驱动但硬件还没到时：

```bash
# 创建两个虚拟芯片
$ sudo modprobe i2c-stub chip_addr=0x50,0x2a

$ i2cdetect -l | grep stub
i2c-9	smbus     	SMBus stub driver         	SMBus adapter

# 用 i2c-tools 读写（就是内存读写）
$ i2cset -y 9 0x2a 0x00 0x5a
$ i2cget -y 9 0x2a 0x00
0x5a

# 现在加载你的驱动，让它 probe 到 0x2a
$ echo foo-chip 0x2a > /sys/bus/i2c/devices/i2c-9/new_device
$ dmesg | tail
[  ...] foo-chip 9-002a: foo chip at 0x2a on SMBus stub driver
```

**预置寄存器值**：先用 `i2cset` 把 ID 寄存器写成期望值，再实例化驱动，这样驱动的 ID 检查就能通过。

**运行时改能力**（`functionality` 参数是 `S_IRUGO | S_IWUSR`，`i2c-stub.c:40-42`）：

```bash
# 读出来是十进制，不是 0x 形式（ulong 参数用 param_get_ulong → "%lu"，
# kernel/params.c:242），209649664 就是默认值 STUB_FUNC_DEFAULT
$ cat /sys/module/i2c_stub/parameters/functionality
209649664
# 写入可以用 0x 前缀（kstrtoul base 0 自动识别进制）
$ echo 0x0c7f0000 | sudo tee /sys/module/i2c_stub/parameters/functionality
# 用来测试"控制器不支持某种 SMBus 事务"时驱动的行为
```

默认值 `STUB_FUNC_DEFAULT`（`i2c-stub.c:27-30`）= `QUICK | BYTE | BYTE_DATA | WORD_DATA | I2C_BLOCK` = `0x0c7f0000`。注意它**故意不含** `I2C_FUNC_SMBUS_BLOCK_DATA`——注释（`:22-26`）明确说这要显式打开，要测 block 事务得自己把那两位加进去（上限是 `STUB_FUNC_ALL`，`:32-33`）。

两点行为细节：

- **立即生效**，不用重新 `modprobe`：`stub_func()`（`:299-302`）每次被调用都现读这个变量，`return STUB_FUNC_ALL & functionality;`。已经 probe 完的 client 不会再查一遍，所以顺序是"先改能力，再 `new_device`"。
- 写进去的值先被 `STUB_FUNC_ALL` 掩一遍，所以只能**减**能力、不能**加**出 stub 没实现的能力。比如 `I2C_FUNC_I2C` 写了也读不出来，这是对的：stub 的 algo 里只有 `smbus_xfer`，没有 `master_xfer`（`:304-307`），能力位和实现是一致的。

**局限**（见 `04-extensions.md` §7）：只模拟"寄存器就是内存"，不能模拟 NAK、时序、时钟拉伸、中断。测这些要用真硬件 + `i2c-gpio` 的故障注入（[`07-debug.md`](07-debug.md) §5）。

---

## 9. 常用调用速查

| 我想做 | 用什么 | 上下文要求 |
|---|---|---|
| 读一个 8 位寄存器 | `i2c_smbus_read_byte_data(client, reg)` | 可睡眠 |
| 写一个 8 位寄存器 | `i2c_smbus_write_byte_data(client, reg, val)` | 可睡眠 |
| 读一个 16 位寄存器 | `i2c_smbus_read_word_data()`（小端）/ `_swapped()`（大端） | 可睡眠 |
| 连续读 N 字节（N 已知） | `i2c_smbus_read_i2c_block_data(client, reg, len, buf)` | 可睡眠 |
| 连续读 N 字节（N 由从设备给） | `i2c_smbus_read_block_data()` | 可睡眠 |
| 任意消息序列 | `i2c_transfer(adap, msgs, num)` | 可睡眠 |
| 只写几个字节，无寄存器概念 | `i2c_master_send(client, buf, count)` | 可睡眠 |
| 关机路径上访问 PMIC | `i2c_transfer()`（核心自动走 atomic 路径） | 需控制器实现 `master_xfer_atomic` |
| 在 mux 的 select 回调里传输 | `__i2c_transfer()`（parent-locked）或 `i2c_transfer()`（mux-locked） | 见 §5 |
| 检查能力 | `i2c_check_functionality(adap, I2C_FUNC_xxx)` | 任意 |
| 拿到某编号的适配器 | `i2c_get_adapter(nr)` … `i2c_put_adapter()` | 可睡眠 |
| 建一个只为占位的 client | `i2c_new_dummy_device()` / `devm_i2c_new_dummy_device()` | 可睡眠 |
| 保证缓冲 DMA 安全 | `i2c_get_dma_safe_msg_buf()` … `i2c_put_dma_safe_msg_buf()` | 可睡眠 |

完整的 API 语义（返回值约定、错误码含义）见 [`01-api.md`](01-api.md)。
