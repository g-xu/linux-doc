---
title: 使用指南
---

# input 子系统：各角色的代码骨架

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 前置阅读：`01-api.md`（API 清单）、`02-datastructures.md`（结构体字段）、`03-internals.md`（时间线与锁）、`04-extensions.md`（子框架）。

## §0 本篇的前提

### §0.1 ★ 所有骨架都没有编译过

**本篇的每一段代码都是骨架，不是可编译的驱动。** 它们省略了头文件、错误路径的一部分、`MODULE_*` 宏和 `of_device_id` 表；变量名和结构体名是编者写的。**照抄之前必须自己编译一遍。**

已经做过的验证只有一件：**正文里出现的每一个内核函数名、宏名、标志名、常量名、内核打印的字符串，都在本版本树上 grep 确认存在，并逐个查过它「树外模块能不能用」。** 判据两条，缺一条就不算能用：

| 判据 | 怎么查 | 不满足会怎样 |
| --- | --- | --- |
| 有 `EXPORT_SYMBOL` / `EXPORT_SYMBOL_GPL` | `grep -rn '^EXPORT_SYMBOL' drivers/input/` | 树外模块链接时报 `Unknown symbol` |
| 在 `include/` 下的**公开**头文件里有声明 | `grep -rn '\b<符号>\b' include/` | 编译时报隐式声明；`drivers/**/xxx.h` 是私有头，不算 |

★ 两条都满足才写进骨架。不满足的那几个，本篇把它们和它们的替代写法一起点出来（`static inline` 的、宏包装的、私有头的三类，见 §11.11）。

★ **`EXPORT_SYMBOL_GPL` 会限制模块许可证。** input 核心里绝大多数导出是不带 `_GPL` 的普通 `EXPORT_SYMBOL`。带 `_GPL` 的**在核心 16 个文件（`drivers/input/*.c`）里一共 14 个，一个不漏地列在这里**：

| 符号 | 位置 | 本篇哪一节会用到 |
| --- | --- | --- |
| `input_ff_upload` / `input_ff_erase` / `input_ff_flush` / `input_ff_event` | `drivers/input/ff-core.c:165` / `:224` / `:251` / `:291` | §5（都由核心自己接，驱动不直接调） |
| `input_ff_create` / `input_ff_destroy` | `drivers/input/ff-core.c:353` / `:377` | §5 |
| `input_ff_create_memless` | `drivers/input/ff-memless.c:553` | §5 |
| `input_device_enabled` | `drivers/input/input.c:2369` | §1.6 |
| `input_class` | `drivers/input/input.c:1999` | 不用（是变量，见 `01-api.md` §7） |
| `matrix_keypad_parse_properties` | `drivers/input/matrix-keymap.c:67` | §4 |
| `vivaldi_function_row_physmap_show` | `drivers/input/vivaldi-fmap.c:37` | 不用（sysfs `show` 回调） |
| `input_event_from_user` / `input_event_to_user` / `input_ff_effect_from_user` | `drivers/input/input-compat.c:131` / `:132` / `:133` | 不用——**声明在私有头 `drivers/input/input-compat.h`，导出了也拿不到声明**（`04-extensions.md` §4.5） |

rmi4 另有 5 个（`drivers/input/rmi4/rmi_bus.c:40` `rmi_dbg`、`:111` `rmi_register_transport_device`、`:319` `__rmi_register_function_handler`、`:332` `rmi_unregister_function_handler`、`:430` `rmi_of_property_read_u32`），本篇 §8 只用到中间两个。

```bash
# 核心 16 个文件的口径 → 14 行
grep -n '^EXPORT_SYMBOL_GPL' drivers/input/*.c
# 含所有驱动子目录 → 54 行 / 18 个文件（多出来的 40 行全在具体驱动里，
# 大头是 drivers/input/touchscreen/wm97xx-core.c 的 9 行）
grep -rn '^EXPORT_SYMBOL_GPL' drivers/input/ include/linux/input.h | wc -l
```

★ 两个口径差 40 行，**不要拿宽口径的输出去核对上面这张表**——表的范围是核心，驱动自己导出给别的驱动用的符号（wm97xx 那一族、adxl34x、cyttsp）不在其内。

★ 注意 `rmi_unregister_transport_device` 是**不带** `_GPL` 的（`drivers/input/rmi4/rmi_bus.c:125`），和它的注册函数不成对——`04-extensions.md` §5 已记为设计债。

### §0.2 角色清单

本子系统有十个能独立成篇的角色。前八个在内核里，后两个在用户态。**每个角色一节，一个不漏**：

| 节 | 角色 | 你要写的东西 | 必须包含的头文件 |
| --- | --- | --- | --- |
| §1 | 中断驱动的按键/开关设备 | 一个 platform 驱动 + 一个中断处理函数 | `linux/input.h` |
| §2 | 不会中断、只能轮询的设备 | 同上，把中断换成 poller 回调 | `linux/input.h` |
| §3 | 多点触摸屏 | 槽位管理 + DT 属性解析 + 每帧上报 | `linux/input.h` `linux/input/mt.h` `linux/input/touchscreen.h` |
| §4 | 行列矩阵键盘 | keymap 构建 + 扫描 | `linux/input.h` `linux/input/matrix_keypad.h` |
| §5 | 带力反馈的设备 | 一个 `playback` 回调 | `linux/input.h` |
| §6 | input handler（消费事件，不造设备） | `connect`/`disconnect`/`event` 三个回调 | `linux/input.h` |
| §7 | serio 端口驱动 / serio 设备驱动 | 两种，别搞混 | `linux/serio.h` |
| §8 | RMI4 function 驱动 | ★ **只能在树内写**，见 §8.1 | `drivers/input/rmi4/rmi_bus.h`（私有） |
| §9 | 用户态：读 `/dev/input/eventN` | 一个 `read()` 循环 | `linux/input.h`（uapi） |
| §10 | 用户态：用 uinput 造虚拟设备 | 一串 ioctl | `linux/uinput.h`（uapi） |

★ 这张表里**没有**「写一个新的 `input_handler` 让它出字符设备」这一行。理由：evdev/joydev/mousedev 三个已经覆盖了通用需求，`04-extensions.md` §1 统计过全树只有 12 个 handler 注册点、十几年没新增过。要给用户态开新接口，正常做法是加 evdev 的 ioctl，不是加 handler。

### §0.3 按任务找章节

| 我要…… | 去 |
| --- | --- |
| 上报一个 GPIO 按键 | §1 |
| 上报一个 I2C 加速度计（没有 IRQ 引脚） | §2 |
| 上报十指触摸 | §3 |
| 读 DT 里的 `touchscreen-size-x` 之类属性 | §3.3 |
| 让 `evdev` 能看到我新加的事件码 | §1.3（能力位必须在注册前声明完） |
| 弄清 `input_free_device()` 什么时候能调、什么时候是 use-after-free | §11.8 |
| 弄清 `input_sync()` / `input_mt_sync()` / `input_mt_sync_frame()` 三者的关系 | §11.4 |
| 在用户态抓事件调试 | §9 |
| 造一个假键盘做自动化测试 | §10 |
| 查两个名字很像的函数区别 | §11（十二组，一组一小节） |

---

## §1 角色一：中断驱动的按键 / 开关设备

**参照的树内驱动**：`drivers/input/keyboard/gpio_keys.c`（1077 行）。骨架只取它的主干。

### §1.1 五步定死的顺序

| 步 | 调什么 | 必须在这一步做完的事 |
| --- | --- | --- |
| 1 | `devm_input_allocate_device(&pdev->dev)` | 拿到 `input_dev` |
| 2 | 填 `name` / `phys` / `id.*` / `open` / `close` | 用户态看到的身份信息 |
| 3 | `input_set_capability()` 或 `input_set_abs_params()` | ★ **能力位必须在这一步全部声明完** |
| 4 | `input_register_device()` | 分水岭，之后能力位改了没人看 |
| 5 | 中断里 `input_report_key()` + `input_sync()` | 事件上报 |

★ 第 3 步和第 4 步的顺序是**硬约束**，不是习惯。原因在 `03-internals.md` A4/A5：`input_register_device()` 里会拿设备的能力位图去和所有已注册 handler 的 `id_table` 做匹配（`input_attach_handler()`），注册之后再往 `evbit`/`keybit` 里补位，**已经匹配过的 handler 不会重来一遍**。症状是 `evdev` 能看到设备但 `EVIOCGBIT` 报告的能力少一块，或者本该接管的 handler 根本没连上。

### §1.2 骨架

```c
struct mykeys {
	struct input_dev *input;
	struct gpio_desc *gpio;
	unsigned int code;
};

static irqreturn_t mykeys_isr(int irq, void *data)
{
	struct mykeys *mk = data;
	int state = gpiod_get_value_cansleep(mk->gpio);

	input_report_key(mk->input, mk->code, state);
	input_sync(mk->input);

	return IRQ_HANDLED;
}

static int mykeys_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct mykeys *mk;
	struct input_dev *input;
	int irq, error;

	mk = devm_kzalloc(dev, sizeof(*mk), GFP_KERNEL);
	if (!mk)
		return -ENOMEM;

	mk->gpio = devm_gpiod_get(dev, NULL, GPIOD_IN);
	if (IS_ERR(mk->gpio))
		return PTR_ERR(mk->gpio);

	if (device_property_read_u32(dev, "linux,code", &mk->code))
		mk->code = KEY_POWER;

	/* 步 1 */
	input = devm_input_allocate_device(dev);
	if (!input)
		return -ENOMEM;
	mk->input = input;
	input_set_drvdata(input, mk);

	/* 步 2 —— 不要再写 input->dev.parent，见 §1.4 */
	input->name = "my keys";
	input->phys = "mykeys/input0";
	input->id.bustype = BUS_HOST;
	input->id.vendor  = 0x0001;
	input->id.product = 0x0001;
	input->id.version = 0x0100;

	/* 步 3 —— 必须在 register 之前 */
	input_set_capability(input, EV_KEY, mk->code);

	irq = platform_get_irq(pdev, 0);
	if (irq < 0)
		return irq;

	error = devm_request_threaded_irq(dev, irq, NULL, mykeys_isr,
					  IRQF_ONESHOT | IRQF_TRIGGER_RISING |
					  IRQF_TRIGGER_FALLING,
					  dev_name(dev), mk);
	if (error)
		return error;

	/* 步 4 */
	return input_register_device(input);
}
```

### §1.3 `input_set_capability()` 到底改了什么

`drivers/input/input.c:2177-2232`。它做两件事：按 `type` 分派到对应的位图（`keybit` / `relbit` / `absbit` / `mscbit` / `ledbit` / `sndbit` / `swbit` / `ffbit`），最后 `:2231` 无条件 `__set_bit(type, dev->evbit)`。★ 三个特例：

| type | 行 | 特殊之处 |
| --- | --- | --- |
| `EV_ABS` | `:2196-2199` | 顺带 `input_alloc_absinfo(dev)`，但**不填 min/max/fuzz/flat**——轴的量程留在 0..0 |
| `EV_PWR` | `:2221-2223` | `/* do nothing */`——**没有对应位图**，只置 `evbit` |
| 未知 type | `:2225-2228` | `pr_err("%s: unknown type %u (code %u)\n", __func__, type, code)` + `dump_stack()`，然后 `return`（**不置 `evbit`**） |

★ 所以**绝对轴不要用 `input_set_capability(dev, EV_ABS, ABS_X)`**，要用 `input_set_abs_params(dev, ABS_X, min, max, fuzz, flat)`（`:494-511`）。后者一次做全四件事：`__set_bit(EV_ABS, dev->evbit)` `:499`、`__set_bit(axis, dev->absbit)` `:500`、`input_alloc_absinfo()` `:502`、写 `minimum`/`maximum`/`fuzz`/`flat` `:507-510`。量程留成 0..0 的症状是用户态 `EVIOCGABS` 读回全零、libinput 直接忽略这个轴。

★ **不需要自己声明 `EV_SYN`。** `input_register_device()` 在 `drivers/input/input.c:2417-2418` 无条件置位，注释写着 `/* Every input device generates EV_SYN/SYN_REPORT events. */`。

★ 直接 `__set_bit(code, dev->keybit)` 也能达到目的，树里大量老驱动这么写。区别只在于它不会顺带置 `evbit` 里的 `EV_KEY`——漏了那一位，事件在 `input_event()` 的**第一道门**就被挡掉：

```c
/* drivers/input/input.c:428 */
	if (is_event_supported(type, dev->evbit, EV_MAX)) {
```

`is_event_supported()` 是 `:62` 的 `static inline`。门外没有任何日志，**设备注册成功、按键一个都出不来**。新代码一律用 `input_set_capability()`。

### §1.4 ★ 不要覆盖 `input->dev.parent`

`devm_input_allocate_device()` 在 `drivers/input/input.c:2091` 已经写了 `input->dev.parent = dev`，kerneldoc `:2072-2073` 明写：

```
 * NOTE: the owner device is set up as parent of input device and users
 * should not override it.
```

`drivers/input/keyboard/gpio_keys.c:846` 那行 `input->dev.parent = dev;` 是历史残留——赋的是同一个值，无害，但照抄会让人误以为必须写。**用 `input_allocate_device()`（非 devm 版）时才必须自己设 `dev.parent`**，否则 `04-extensions.md` §10 那条 `touchscreen_parse_properties()` 会拿着 NULL 去读属性（`drivers/input/touchscreen.c:68` 无 NULL 检查）。

### §1.5 `open` / `close` 回调的语义

`input->open` / `input->close` 由核心在**第一个 handler 打开**和**最后一个 handler 关闭**时各调一次（`drivers/input/input.c:625-669` / `696-721`）。判据是 `dev->users` 这个计数：打开侧 `:641` 的 `if (dev->users++ || dev->inhibited) goto out;` 只让第一个进来的往下走，关闭侧 `:704` 的 `if (!--dev->users && !dev->inhibited)` 只让最后一个出去的调 `close()`。★ `inhibited` 也在这两个条件里——设备被 inhibit 时 `open()`/`close()` 都不会调。也就是说：

- 没有任何用户态进程打开 `/dev/input/eventN` 时，`open()` 不会被调——**可以把硬件电源和中断留到 `open()` 里再开**
- `open()` 返回非 0 会让 `input_open_device()` 失败并把两个计数都回滚（`:651-660`：`dev->users--`、`handle->open--`，再 `synchronize_rcu()` 确保没有事件还在往这个 handle 上送）
- ★ 这两个回调**不是**给 `probe`/`remove` 用的。硬件资源申请仍然在 `probe` 里

---

## §2 角色二：轮询设备驱动

硬件没有中断线，只能定时读寄存器。历史上这是独立的 `input_polled_dev` 框架，6.1 里已经合并进 `input_dev` 本体——**没有第二种设备结构体，只是多填一个 `dev->poller` 指针**。实现全在 `drivers/input/input-poller.c`（224 行），私有头 `drivers/input/input-poller.h`（18 行）只给核心自己用。

全树使用者 30 个文件（口径见 `04-extensions.md` §10.5）。arm64 上的代表是 `drivers/input/keyboard/gpio_keys_polled.c`。

### §2.1 五步（与 §1 的差别只在第 3、4 步）

| # | 动作 | 说明 |
| --- | --- | --- |
| 1 | `devm_input_allocate_device()` | 同 §1 |
| 2 | 填 `name` / `id` / `input_set_capability()` | 同 §1 |
| 3 | `input_setup_polling(input, poll_fn)` | 分配 `struct input_dev_poller`，挂到 `dev->poller`（`drivers/input/input-poller.c:68-91`） |
| 4 | `input_set_poll_interval(input, ms)` | ★ **必须在第 3 步之后**，见 §2.3 |
| 5 | `input_register_device()` | 内部 `:2450-2451` 调 `input_dev_poller_finalize()` 补默认值 |

★ **不要填 `input->open` / `input->close`**：轮询的启停由核心接管（`drivers/input/input.c:663-664` 开、`:705-706` 关），填了也会被调用，但轮询周期不受你控制。也**不要自己 `kfree(dev->poller)`**：`input_dev_release()` 在 `drivers/input/input.c:1665` free 它。

### §2.2 骨架

```c
/* 骨架，未编译 */
static void mypoll_poll(struct input_dev *input)
{
	struct mypoll *priv = input_get_drvdata(input);
	int val = mypoll_read_hw(priv);		/* 允许睡眠：跑在 workqueue 上 */

	input_report_key(input, KEY_ENTER, val);
	input_sync(input);
}

static int mypoll_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct input_dev *input;
	u32 interval = 100;			/* 自己的默认值 */
	int error;

	input = devm_input_allocate_device(dev);
	if (!input)
		return -ENOMEM;

	input->name = "mypoll";
	input_set_capability(input, EV_KEY, KEY_ENTER);
	input_set_drvdata(input, priv);

	error = input_setup_polling(input, mypoll_poll);
	if (error)
		return error;		/* 已经打了 dev_err，不用再打一遍 */

	device_property_read_u32(dev, "poll-interval", &interval);
	input_set_poll_interval(input, interval);
	input_set_min_poll_interval(input, 10);
	input_set_max_poll_interval(input, 1000);

	return input_register_device(input);
}
```

`input_setup_polling()` 失败时已经在 `drivers/input/input-poller.c:80-81` 打过 `"%s: unable to allocate poller structure\n"`，紧邻的注释 `:75-79` 解释了为什么在 `kzalloc()` 已经会打回溯的情况下还要再打一条：**回溯里看不出是哪个 input 设备**。所以驱动侧直接 `return error` 就够，`drivers/input/keyboard/gpio_keys_polled.c:353-355` 那种再来一条 `dev_err` 属于冗余。

### §2.3 ★ 四个 setter 会静默失败

`input_set_poll_interval()` / `input_set_min_poll_interval()` / `input_set_max_poll_interval()` 三个都是 `void`，内部先过 `input_dev_ensure_poller()`（`:94-103`）：

```c
static bool input_dev_ensure_poller(struct input_dev *dev)
{
	if (!dev->poller) {
		dev_err(dev->dev.parent ?: &dev->dev,
			"poller structure has not been set up\n");
		return false;
	}

	return true;
}
```

返回 false 时 setter **什么也不做，也没法告诉调用者**。错用会怎样：把 `input_set_poll_interval(input, 20)` 写在 `input_setup_polling()` **之前**，日志里多一行 `poller structure has not been set up`，然后 `input_register_device()` 里的 `input_dev_poller_finalize()` 把周期补成 **500 ms**（`:46-52`）——设备能用、事件能出，只是慢了 25 倍。这条日志很容易被淹掉。

只有 `input_get_poll_interval()`（`:126-132`）有返回值，没有 poller 时返回 `-EINVAL`。

### §2.4 默认值与三个周期的关系

`input_dev_poller_finalize()`（`:46-52`）只补两个值：

```c
void input_dev_poller_finalize(struct input_dev_poller *poller)
{
	if (!poller->poll_interval)
		poller->poll_interval = 500;
	if (!poller->poll_interval_max)
		poller->poll_interval_max = poller->poll_interval;
}
```

| 字段 | 你不设时 | 含义 |
| --- | --- | --- |
| `poll_interval` | 500 ms | 当前周期。**为 0 表示不轮询**（`input_dev_poller_start()` `:57` 的门） |
| `poll_interval_max` | 等于 `poll_interval` | 用户态能写的上限 |
| `poll_interval_min` | **0，不补** | 用户态能写的下限 |

★ **只设 `poll_interval` 的后果**：`max` 被补成同一个值，于是用户态**只能把周期改小，永远调不大**。想让用户态有上调空间，必须显式调 `input_set_max_poll_interval()`。这一条在 kerneldoc 里没有，只能从 `:50-51` 这两行读出来。

### §2.5 轮询是怎么跑起来的

```
input_open_device()          drivers/input/input.c:663-664
  └─ input_dev_poller_start()      input-poller.c:54-61
       ├─ if (poll_interval > 0)                :57
       ├─ poller->poll(poller->input)  ← 立即先读一次   :58
       └─ input_dev_poller_queue_work()          :59
            └─ queue_delayed_work(system_freezable_wq, …)  :34
                 └─ input_dev_poller_work()      input-poller.c:37-44
                      ├─ poller->poll(...)       :42
                      └─ 再排一次自己             :43
```

四个要点，都影响驱动怎么写 `poll_fn`：

| 事实 | 行 | 对驱动的含义 |
| --- | --- | --- |
| 跑在 `system_freezable_wq` 上 | `:34` | **可以睡眠**，可以做 I2C/SPI 传输；系统挂起时 workqueue 被冻结，不用自己处理 suspend |
| `delay >= HZ` 时走 `round_jiffies_relative()` | `:31-32` | 周期 ≥1 秒的会被对齐到秒边界省电，**实际间隔不精确** |
| 先 `poll()` 再排下一次 | `:42-43` | 间隔是"上一次 poll **结束**到下一次开始"，poll 自己耗时会累加进去，**没有漂移补偿** |
| `input_dev_poller_start()` 里先同步调一次 `poll()` | `:58` | `poll_fn` 会在 `input_open_device()` 的上下文里被直接调用一次，那里持着 `dev->mutex`——**`poll_fn` 里不能再去拿 `dev->mutex`** |

停的一侧是 `input_dev_poller_stop()`（`:63-66`），一句 `cancel_delayed_work_sync()`。除了 close，inhibit 也会停（`drivers/input/input.c:1857-1858`）、uninhibit 会重启（`:1890-1891`）。

### §2.6 用户态旋钮：三个 sysfs 文件

`input_poller_attribute_group`（`:219-222`）挂在 `drivers/input/input.c:1655` 的属性组数组里，出现在 `/sys/class/input/inputN/` 下：

| 文件 | 权限 | 读写行为 |
| --- | --- | --- |
| `poll` | 0644 | 读当前周期；写要过 min/max 两道界（`:159-163`，越界 `-EINVAL`） |
| `max` | 0444 | 只读 `poll_interval_max` |
| `min` | 0444 | 只读 `poll_interval_min` |

★ 这三个文件**只在 `dev->poller` 非空时可见**——`.is_visible = input_poller_attrs_visible`（`:203-210`）在没有 poller 时返回 0，整组隐藏。中断型设备的 `/sys/class/input/inputN/` 下不会有 `poll`。

写 `poll` 的完整语义在 `:146-178`：`kstrtouint` → 界检查 → `mutex_lock(&input->mutex)` → 改 `poll_interval` → **只有 `input_device_enabled(input)` 为真时**才 `cancel_delayed_work_sync()` + 重排（`:169-173`）。也就是说设备没被打开时改周期只改数值，下次 open 生效。写 0 在 `min` 也是 0（默认）时是合法的，效果是**轮询彻底停掉**——`:171` 的 `if (poller->poll_interval > 0)` 不成立，不再排新 work。

### §2.7 `poll-interval` 这个 DT 属性由谁读

★ **核心一个字都不读。** 5 个驱动各自用 `device_property_read_u32(dev, "poll-interval", …)` 读，然后传给 `input_set_poll_interval()`：

```
grep -rlE '"poll-interval"' --include=*.c drivers/input/   →   5
```

| 文件 | 行 | 读不到时 |
| --- | --- | --- |
| `drivers/input/keyboard/gpio_keys_polled.c` | `:165` | 不检查返回值，`pdata->poll_interval` 保持调用前的值 |
| `drivers/input/keyboard/adc-keys.c` | `:176` | `if (!…read_u32(…))` 才设，否则用驱动默认 |
| `drivers/input/keyboard/mpr121_touchkey.c` | `:297` | 同 gpio_keys_polled，不检查 |
| `drivers/input/keyboard/clps711x-keypad.c` | `:123` | 检查返回值并**报错退出** |
| `drivers/input/joystick/adc-joystick.c` | `:211` | 检查返回值，`:217` `dev_err(dev, "Unable to get poll-interval\n")` |

同一个属性名，五个驱动四种缺失处理。写新驱动时**要显式给一个默认值**，别指望核心兜底——核心的兜底是 500 ms（§2.4）。DTS 侧写法见 `06-devicetree.md`。

---

## §3 角色三：多点触摸设备驱动

实现在 `drivers/input/input-mt.c`（538 行），公开头 `include/linux/input/mt.h`（129 行）。全树 64 个文件调 `input_mt_init_slots()`。协议本身（slot 协议 B vs 老的协议 A）在 `03-internals.md` §A8 讲过，本节只讲怎么写。

★ **新驱动一律用协议 B**：`input_mt_init_slots()` + `input_mt_slot()` + `input_mt_report_slot_state()`。协议 A 的 `input_mt_sync()` 全树只剩 3 个文件（`04-extensions.md` 篇末表二），不要照抄那三个。

### §3.1 五步 + 一条硬次序

| # | 动作 | 说明 |
| --- | --- | --- |
| 1 | `devm_input_allocate_device()` | 同 §1 |
| 2 | **先**设 MT 轴的量程 | `input_set_abs_params(input, ABS_MT_POSITION_X, 0, w-1, 0, 0)` 等 |
| 3 | `input_mt_init_slots(input, n, flags)` | 见 §3.2 |
| 4 | 中断/轮询里逐 slot 上报 | 见 §3.4 |
| 5 | `input_register_device()` | — |

★★ **第 2 步必须在第 3 步之前**，原因在 `copy_abs()`（`drivers/input/input-mt.c:15-22`）：

```c
static void copy_abs(struct input_dev *dev, unsigned int dst, unsigned int src)
{
	if (dev->absinfo && test_bit(src, dev->absbit)) {
		dev->absinfo[dst] = dev->absinfo[src];
		dev->absinfo[dst].fuzz = 0;
		__set_bit(dst, dev->absbit);
	}
}
```

`input_mt_init_slots()` 在 `:66-68` 用它把 `ABS_MT_POSITION_X/Y`、`ABS_MT_PRESSURE` 的量程**拷给**单点轴 `ABS_X/ABS_Y/ABS_PRESSURE`。`:17` 的 `test_bit(src, dev->absbit)` 是一道静默门：**MT 轴还没设时这个拷贝整个跳过**，不报错。错用会怎样——`ABS_X` 完全没被声明，单点仿真事件（`:248-249`）在核心的 `EV_ABS` 门（`drivers/input/input.c:290-291`）那里被丢掉，症状是 libinput 认得多点、老的 X 单点程序完全不动。注意 `:19` 顺手把拷过去的 `fuzz` 清零——单点轴的去抖要自己再设。

### §3.2 `input_mt_init_slots()` 的 flags 与副作用

五个标志定义在 `include/linux/input/mt.h:15-19`：

| flag | 行 | 含义 | 谁该用 |
| --- | --- | --- | --- |
| `INPUT_MT_POINTER` | `:15` | 触控板（相对指针语义） | 笔记本触控板 |
| `INPUT_MT_DIRECT` | `:16` | 触摸屏（坐标即屏幕坐标） | 触摸屏 |
| `INPUT_MT_DROP_UNUSED` | `:17` | 本帧没提到的触点自动抬起 | 硬件只报"当前按着的" |
| `INPUT_MT_TRACK` | `:18` | 用内核跟踪算法分配 slot | 硬件不给稳定 ID |
| `INPUT_MT_SEMI_MT` | `:19` | 半多点（只给包围盒） | 老触控板 |

`POINTER` 和 `DIRECT` 二选一，不要同时给。函数体（`drivers/input/input-mt.c:39-104`）替你做的事：

| 行 | 动作 | 条件 |
| --- | --- | --- |
| `:45-46` | `num_slots == 0` 直接 `return 0` | ★ **不报错**，`dev->mt` 仍为 NULL |
| `:47-48` | 已初始化过时：slot 数相同返回 0，不同返回 `-EINVAL` | 可重复调用 |
| `:50-51` | `num_slots > 1024` 返回 `-EINVAL` | 注释 `:49` 写着 "Arbitrary limit" |
| `:59-60` | 设 `ABS_MT_SLOT`（0..n-1）和 `ABS_MT_TRACKING_ID`（0..`TRKID_MAX`） | 无条件 |
| `:63-64` | 置 `EV_KEY` + `BTN_TOUCH` | `POINTER \| DIRECT` |
| `:66-68` | `copy_abs()` 三条轴 | `POINTER \| DIRECT` |
| `:71-78` | `BTN_TOOL_FINGER`/`DOUBLETAP`，slot 数够时再加 `TRIPLETAP`(≥3)/`QUADTAP`(≥4)/`QUINTTAP`(≥5) | `POINTER` |
| `:79` / `:82` / `:84` | `INPUT_PROP_POINTER` / `INPUT_PROP_DIRECT` / `INPUT_PROP_SEMI_MT` | 各自的 flag |
| `:86-89` | 额外分配 `num_slots²` 个 int 的代价矩阵 `mt->red` | `TRACK`。★ 1024 slot 会要 4 MB |
| `:93-94` | 所有 slot 的 `ABS_MT_TRACKING_ID` 置 -1（inactive） | 无条件 |
| `:97` | `mt->frame = 1` | 让所有 slot 一开始就是 "unused" |

★ 失败路径只有一个标签 `err_mem:`（`:101-103`），`kfree(mt)` 后返回 `-ENOMEM`。**`mt->red` 分配失败时走的也是这个标签**，此时 `dev->mt` 还没赋值（`:99` 在后面），所以不会留下半初始化的设备。

`input_mt_destroy_slots()`（`:114-121`）的 kerneldoc `:111-112` 明确说 **"only needed in error path"**——正常路径由 `input_dev_release()` 兜（`drivers/input/input.c:1664` 的 `input_mt_destroy_slots(dev)`）。用了 devm 分配的驱动 probe 失败时也不用调，`devm` 会释放整个 `input_dev`。

### §3.3 骨架

```c
/* 骨架，未编译 */
static irqreturn_t myts_irq(int irq, void *dev_id)
{
	struct myts *ts = dev_id;
	struct myts_point pts[MYTS_MAX_POINTS];
	int i, n;

	n = myts_read_points(ts, pts);	/* 硬件给了 n 个触点 */
	if (n < 0)
		return IRQ_NONE;

	for (i = 0; i < n; i++) {
		input_mt_slot(ts->input, pts[i].id);	/* 硬件给的稳定 ID */
		if (!input_mt_report_slot_state(ts->input, MT_TOOL_FINGER, true))
			continue;
		touchscreen_report_pos(ts->input, &ts->prop,
				       pts[i].x, pts[i].y, true);
		input_report_abs(ts->input, ABS_MT_TOUCH_MAJOR, pts[i].area);
	}

	input_mt_sync_frame(ts->input);		/* 代替 input_sync() 之前的收尾 */
	input_sync(ts->input);
	return IRQ_HANDLED;
}

static int myts_probe(struct i2c_client *client)
{
	struct input_dev *input;
	int error;

	input = devm_input_allocate_device(&client->dev);
	if (!input)
		return -ENOMEM;

	input->name = "myts";
	input_set_abs_params(input, ABS_MT_POSITION_X, 0, ts->w - 1, 0, 0);
	input_set_abs_params(input, ABS_MT_POSITION_Y, 0, ts->h - 1, 0, 0);
	input_set_abs_params(input, ABS_MT_TOUCH_MAJOR, 0, 255, 0, 0);

	/* 读 touchscreen-* DT 属性，可能覆盖上面的量程 */
	touchscreen_parse_properties(input, true, &ts->prop);

	error = input_mt_init_slots(input, MYTS_MAX_POINTS,
				    INPUT_MT_DIRECT | INPUT_MT_DROP_UNUSED);
	if (error)
		return error;

	return input_register_device(input);
}
```

★ 次序上还有一条：`touchscreen_parse_properties()` 要在 `input_set_abs_params()` **之后**、`input_mt_init_slots()` **之前**。它把 DT 里的 `touchscreen-size-x` 等覆盖进 MT 轴（`drivers/input/touchscreen.c:81-92`），而 `copy_abs()` 拷的是覆盖后的值。放到 init_slots 之后，单点轴就还是 DTS 之前的量程。

### §3.4 每帧的三件事

| 顺序 | 调用 | 作用 |
| --- | --- | --- |
| 每个触点前 | `input_mt_slot(dev, i)` | `mt.h:85-88` 的 `static inline`，就是 `input_event(dev, EV_ABS, ABS_MT_SLOT, i)` |
| 每个触点 | `input_mt_report_slot_state(dev, tool, active)` | `drivers/input/input-mt.c:138-164` |
| 帧末 | `input_mt_sync_frame(dev)` | `:333-355`，然后再 `input_sync()` |

`input_mt_report_slot_state()` 的返回值就是 `active` 参数经过 `!mt` 检查后的结果（`:145-146` 返回 false、`:153` 返回 false、`:163` 返回 true），**用它当"要不要继续报这个触点的坐标"的判据**：

```c
	slot = &mt->slots[mt->slot];
	slot->frame = mt->frame;		/* :149 打上"本帧提到过"的戳 */

	if (!active) {
		input_event(dev, EV_ABS, ABS_MT_TRACKING_ID, -1);
		return false;
	}

	id = input_mt_get_value(slot, ABS_MT_TRACKING_ID);
	if (id < 0)
		id = input_mt_new_trkid(mt);	/* :158 只在从无到有时分配新 ID */
```

三个要点：

- ★ `:149` 的 `slot->frame = mt->frame` 是 `INPUT_MT_DROP_UNUSED` 的全部机制——**只有调过 `input_mt_report_slot_state()` 的 slot 才算"本帧用过"**，`__input_mt_drop_unused()` 在 `input_mt_sync_frame()` 里（`:341-347`，持 `dev->event_lock`）把没打戳的抬起。所以抬起触点有两种写法：显式 `input_mt_report_slot_inactive(dev)`（`mt.h:103-106`，等于 `report_slot_state(dev, 0, false)`），或者干脆不提它、靠 `DROP_UNUSED`。**两种都行，但混着用会漏**——硬件报"这个触点抬起了"时如果你 `continue` 掉、又没开 `DROP_UNUSED`，这个 slot 会永远按着。
- `:157-158` 只在 slot 原本 inactive 时分配新 tracking ID。**同一根手指连续两帧都 active 时 ID 不变**，这是用户态区分"移动"和"换手指"的唯一依据。
- `:161` 无条件 `input_event(dev, EV_ABS, ABS_MT_TOOL_TYPE, tool_type)`；kerneldoc `:133-134` 说 "only reported if the corresponding absbit field is set"——这句是对的，但门不在这里，在核心的 `drivers/input/input.c:290-291`。**没设 `ABS_MT_TOOL_TYPE` 量程的驱动传什么 tool_type 都无所谓。**

### §3.5 单点仿真：`input_mt_sync_frame()` 帮你做了什么

`input_mt_sync_frame()`（`:333-355`）内部：`DROP_UNUSED` 抬起 → 决定 `use_count`（`:349-350`：只有 `POINTER` 且非 `SEMI_MT` 时为真）→ `input_mt_report_pointer_emulation(dev, use_count)`（`:352`）→ `mt->frame++`（`:354`）。

仿真函数 `:199-259` 做四件事，都不用你操心：

| 行 | 动作 |
| --- | --- |
| `:212-223` | 扫全部 slot，用 `(id - oldid) & TRKID_SGN`（`:218`，`TRKID_SGN` 定义在 `:13`）挑出**最老的**活跃触点，同时数 `count` |
| `:225` | `BTN_TOUCH` = `count > 0` |
| `:227-242` | `use_count` 时报手指数；`:228-239` 有一个 hover 特例：只报整体悬浮距离（有 `ABS_DISTANCE` 无 `ABS_MT_DISTANCE`）且距离非 0 时把 `count` 强行当 1 |
| `:244-258` | 把最老触点的坐标写到 `ABS_X`/`ABS_Y`（`:248-249`），有 `ABS_MT_PRESSURE` 时也写 `ABS_PRESSURE`；没有活跃触点时把压力清 0（`:256-257`） |

★ 所以**不要自己再报 `ABS_X`/`ABS_Y`/`BTN_TOUCH`/`BTN_TOOL_*`**。自己报一遍会和仿真打架：仿真挑"最老"的触点，你大概率报的是"第一个"，两个值在同一帧里互相覆盖，用户态看到坐标跳。

★ `input_mt_sync_frame()` **不含** `input_sync()`。帧末两句都要写。少了 `input_sync()`，evdev 的这一帧不会被 flush，用户态一个事件都读不到。

---

## §4 角色四：矩阵键盘驱动

实现在 `drivers/input/matrix-keymap.c`（202 行），公开头 `include/linux/input/matrix_keypad.h`（92 行）。它只管**键位映射表**，扫描逻辑是每个驱动自己的。全树 28 个文件调 `matrix_keypad_build_keymap()`、14 个调 `matrix_keypad_parse_properties()`。arm64 上的代表是 `drivers/input/keyboard/matrix_keypad.c`（GPIO 矩阵，纯 DT）。

### §4.1 三步

| # | 动作 | 说明 |
| --- | --- | --- |
| 1 | `matrix_keypad_parse_properties(dev, &rows, &cols)` | 读 `keypad,num-rows` / `keypad,num-columns`（`drivers/input/matrix-keymap.c:57-58`），两个缺一个就 `-EINVAL` |
| 2 | `matrix_keypad_build_keymap(NULL, NULL, rows, cols, NULL, input)` | 从 `linux,keymap` 建表 |
| 3 | 扫描回调里 `input_report_key(input, keymap[MATRIX_SCAN_CODE(r, c, row_shift)], on)` | `row_shift` 自己算：`get_count_order(cols)` |

★ 第 2 步之前**必须先设好 `input->dev.parent`**：`:161` 是 `if (WARN_ON(!input_dev->dev.parent)) return -EINVAL;`，kerneldoc `:147-148` 也明写 "Callers are expected to set up input_dev->dev.parent before calling this function"。用 `devm_input_allocate_device()` 就自动满足（§1.4）。

### §4.2 骨架

```c
/* 骨架，未编译 */
static int mymk_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct mymk *keypad;
	struct input_dev *input;
	unsigned int rows, cols;
	int error;

	error = matrix_keypad_parse_properties(dev, &rows, &cols);
	if (error)
		return error;		/* 已打 dev_err，见 :61 */

	keypad = devm_kzalloc(dev, sizeof(*keypad), GFP_KERNEL);
	if (!keypad)
		return -ENOMEM;

	input = devm_input_allocate_device(dev);
	if (!input)
		return -ENOMEM;

	keypad->input = input;
	keypad->row_shift = get_count_order(cols);
	input->name = "mymk";
	input->id.bustype = BUS_HOST;

	/* keymap 传 NULL：让 helper 用 devm 替你分配 max_keys 个 u16 */
	error = matrix_keypad_build_keymap(NULL, NULL, rows, cols, NULL, input);
	if (error)
		return error;

	__set_bit(EV_REP, input->evbit);		/* 要自动重复才设 */
	input_set_capability(input, EV_MSC, MSC_SCAN);
	input_set_drvdata(input, keypad);

	return input_register_device(input);
}

/* 扫描回调（中断或轮询）里 */
static void mymk_report(struct mymk *keypad, int row, int col, bool pressed)
{
	unsigned short *keycodes = keypad->input->keycode;
	unsigned int code = MATRIX_SCAN_CODE(row, col, keypad->row_shift);

	input_event(keypad->input, EV_MSC, MSC_SCAN, code);
	input_report_key(keypad->input, keycodes[code], pressed);
}
```

`EV_REP` 和 `MSC_SCAN` 都是**约定而非强制**，helper 不会替你设：`matrix_keypad_build_keymap()` 只置 `EV_KEY`（`:179`）。`drivers/input/keyboard/matrix_keypad.c:527-529` 是标准写法——`EV_REP` 受 `no_autorepeat` 控制，`MSC_SCAN` 无条件设。★ 报 `MSC_SCAN` 的意义是让用户态能 `EVIOCSKEYCODE` 改键位（`03-internals.md` §B7），不报也能用，但用户就改不了键。

### §4.3 `build_keymap` 替你做的五件事

| 行 | 动作 |
| --- | --- |
| `:156-157` | `row_shift = get_count_order(cols)`；`max_keys = rows << row_shift` |
| `:164-173` | `keymap == NULL` 时用 `devm_kcalloc(input_dev->dev.parent, …)` 分配——**挂在父设备上，驱动 unbind 时自动释放** |
| `:175-177` | 填 `keycode` / `keycodesize`（`sizeof(unsigned short)` = 2）/ `keycodemax` |
| `:179` | `__set_bit(EV_KEY, input_dev->evbit)` |
| `:181-194` | 有 `keymap_data` 用它、没有就走 `matrix_keypad_parse_keymap()`（`:69-121`）从 DT 属性读 |
| `:196` | `__clear_bit(KEY_RESERVED, input_dev->keybit)` |

`:196` 这一句是必要的收尾：未映射的格子留在 keymap 里是 0，也就是 `KEY_RESERVED`，逐项 `__set_bit(code, keybit)`（`:39`）会把它一起点亮。清掉之后用户态看到的能力位就不含那个假键。

★ `keycodesize` 被正确填成 2（`:176`）——对比 `04-extensions.md` §11 里 sparse-keymap **不填** `keycodesize` 的设计债，这里是对的一侧，`EVIOCGKEYCODE` / `EVIOCSKEYCODE` 直接可用。

### §4.4 ★ 两个 `KEY()` 宏，位宽不一样

同一个 32 位打包格式有两份打包宏和一份解包宏：

| 宏 | 位置 | row / col 掩码 |
| --- | --- | --- |
| `KEY(row, col, val)` | `include/linux/input/matrix_keypad.h:12-14` | `& (MATRIX_MAX_ROWS - 1)` = **5 位** |
| `MATRIX_KEY(row, col, code)` | `include/dt-bindings/input/input.h:15-16` | `& 0xFF` = **8 位** |
| `KEY_ROW(k)` / `KEY_COL(k)` | `include/linux/input/matrix_keypad.h:16-17` | `(k >> 24) & 0xff` / `(k >> 16) & 0xff` = **8 位** |

解包侧和 DT 侧一致（8 位），只有**内核内部的 `KEY()` 宏窄到 5 位**。错用会怎样：平台代码写 `KEY(32, 0, KEY_A)` 时 `32 & 31 == 0`，**静默变成 row 0**，把已有的 (0,0) 键覆盖掉，没有任何日志。`MATRIX_MAX_ROWS` / `MATRIX_MAX_COLS` 都是 32（`:9-10`），所以 5 位掩码在合法范围内是等价的——但它把"越界"从可诊断的错误变成了静默回绕。

DT 侧（`MATRIX_KEY`）不回绕，写超范围的 row 会一路走到 `matrix_keypad_map_key()` 的 `:31-36`，那里有明确的报错：

```c
	if (row >= rows || col >= cols) {
		dev_err(input_dev->dev.parent,
			"%s: invalid keymap entry 0x%x (row: %d, col: %d, rows: %d, cols: %d)\n",
			__func__, key, row, col, rows, cols);
		return false;
	}
```

★ 这条错误消息会一路把 `build_keymap` 变成 `-EINVAL`（`:187` 或经 `:111`），probe 失败。**DTS 写错行列号是"编译过、probe 挂、日志明确"，平台代码写错是"静默按错键"**——这是 DT 路径比 platform data 路径安全的一个具体例子。

### §4.5 三个属性名和它们的消费者

| 属性 | 谁读 | 行 | 缺失后果 |
| --- | --- | --- | --- |
| `keypad,num-rows` | `matrix_keypad_parse_properties()` | `drivers/input/matrix-keymap.c:57` | `dev_err "number of keypad rows/columns not specified"` + `-EINVAL`（`:60-63`） |
| `keypad,num-columns` | 同上 | `:58` | 同上 |
| `linux,keymap` | `matrix_keypad_parse_keymap()` | `:84`（属性名默认值在 `:81-82`） | `dev_err "missing or malformed property %s: %d"` + `-EINVAL`（`:85-89`） |

`linux,keymap` 还有两道校验：`:91-95` 项数超过 `max_keys` 时报 `"%s size overflow (%d vs max %u)"`；每一项过 `:31-36` 的行列检查。DTS 写法见 `06-devicetree.md`。

★ 属性名可以换：`matrix_keypad_build_keymap()` 的第二个参数 `keymap_name` 非 NULL 时用它代替 `linux,keymap`（`:81-82`）。全树 28 个调用者里绝大多数传 NULL——传非 NULL 的要在自己的绑定文档里写清属性名。

### §4.6 导出符号的一处不对称

```
grep -n 'EXPORT_SYMBOL' drivers/input/matrix-keymap.c
```

| 符号 | 行 | 形式 |
| --- | --- | --- |
| `matrix_keypad_parse_properties` | `:67` | `EXPORT_SYMBOL_GPL` |
| `matrix_keypad_build_keymap` | `:200` | `EXPORT_SYMBOL`（**不带 `_GPL`**） |

同一个文件里成对使用的两个函数，一个 GPL-only 一个不是。**非 GPL 模块能建 keymap 但读不到行列数**，只能自己 `device_property_read_u32()`。已登记为设计债（`99-evidence.md`）。

---

## §5 角色五：力反馈 / 振动驱动

两条路，选哪条取决于**硬件有没有效果内存**：

| 路 | 入口 | 你要实现 | 全树文件数 |
| --- | --- | --- | --- |
| 完整 FF | `input_ff_create(dev, max_effects)` | `ff->upload` / `ff->playback`（必需），`erase` / `set_gain` / `set_autocenter`（可选） | 10 |
| 无内存 FF | `input_ff_create_memless(dev, data, play_effect)` | 一个 `play_effect()`，只管"现在该多大力" | 39 |

★ **振动马达一律走 memless。** 包络、周期、叠加、定时全由 `drivers/input/ff-memless.c`（553 行）替你算，你的回调只在力度变化时被调一次。arm64 上的代表是 `drivers/input/misc/gpio-vibra.c` 和 `drivers/input/misc/regulator-haptic.c`。

统计口径（同 `04-extensions.md` §10.5）：

```
grep -rlE '\binput_ff_create_memless\(' --include=*.c .   →   39
grep -rlE '\binput_ff_create\(' --include=*.c .           →   10
```

### §5.1 memless 骨架

```c
/* 骨架，未编译 */
static int myvib_play(struct input_dev *dev, void *data,
		      struct ff_effect *effect)
{
	struct myvib *vib = input_get_drvdata(dev);
	u16 level = effect->u.rumble.strong_magnitude;

	if (!level)
		level = effect->u.rumble.weak_magnitude;

	vib->level = level;
	schedule_work(&vib->play_work);	/* ★ 不能在这里睡眠，见 §5.3 */

	return 0;
}

static int myvib_probe(struct platform_device *pdev)
{
	struct myvib *vib;
	int error;

	vib = devm_kzalloc(&pdev->dev, sizeof(*vib), GFP_KERNEL);
	if (!vib)
		return -ENOMEM;

	vib->input = devm_input_allocate_device(&pdev->dev);
	if (!vib->input)
		return -ENOMEM;

	vib->input->name = "myvib";
	vib->input->close = myvib_close;	/* 关闭时停振 */
	input_set_drvdata(vib->input, vib);

	/* ★ 必须在 create 之前设 ffbit */
	input_set_capability(vib->input, EV_FF, FF_RUMBLE);

	INIT_WORK(&vib->play_work, myvib_play_work);

	error = input_ff_create_memless(vib->input, NULL, myvib_play);
	if (error)
		return error;

	return input_register_device(vib->input);
}
```

★★ **第二个参数传 NULL，驱动数据走 `input_set_drvdata()`。** 原因在 `ml_ff_destroy()`（`drivers/input/ff-memless.c:485-499`）：

```c
static void ml_ff_destroy(struct ff_device *ff)
{
	struct ml_device *ml = ff->private;
	...
	del_timer_sync(&ml->timer);

	kfree(ml->private);
}
```

`ml->private` 就是你传进去的 `data`（`:520`）。核心**会 `kfree()` 它**。所以传 `devm_kzalloc()` 的指针 = 双重释放：devm 在驱动 unbind 时放一次，`ml_ff_destroy()` 再放一次。树里 39 个使用者中的绝大多数（`gpio-vibra.c:142`、`xpad.c:1546`、`regulator-haptic.c:188`、`twl6040-vibra.c:336` …）传的都是 NULL；传非 NULL 的（`drivers/hid/hid-dr.c:113`、`drivers/hid/hid-betopff.c:109`、`drivers/input/joystick/gamecon.c:302`）都是裸 `kzalloc()` 出来的——`hid-dr.c:107` 就是 `kzalloc()`，而且 `:114-117` 的失败路径**自己 `kfree(drff)`**，因为此时 `ml_ff_destroy` 还没被挂上。

`input_ff_create_memless()`（`:507-552`）替你做的：

| 行 | 动作 |
| --- | --- |
| `:515-517` | 分配 `struct ml_device`（内含 16 个 effect 状态槽 + 一个 timer） |
| `:522` | `ml->gain = 0xffff`（满增益起步） |
| `:523` | `timer_setup(&ml->timer, ml_effect_timer, 0)` |
| `:525` | `set_bit(FF_GAIN, dev->ffbit)`——★ **无条件替你声明 FF_GAIN**，用户态可以调总增益 |
| `:527` | 转调 `input_ff_create(dev, FF_MEMLESS_EFFECTS)`，`FF_MEMLESS_EFFECTS` = 16（`:26`） |
| `:535-538` | 挂上四个 op：`ml_ff_upload` / `ml_ff_playback` / `ml_ff_set_gain` / `ml_ff_destroy` |
| `:541-546` | 硬件只支持 `FF_RUMBLE` 时，**对用户态额外声明** `FF_PERIODIC` + `FF_SINE`/`FF_TRIANGLE`/`FF_SQUARE`（用 rumble 仿真周期效果） |

所以驱动只声明 `FF_RUMBLE`，用户态 `EVIOCGBIT(EV_FF)` 会看到 5 个位。

### §5.2 `input_ff_create()` 的三条硬约束

`drivers/input/ff-core.c:304-352`。

| # | 约束 | 依据 |
| --- | --- | --- |
| 1 | **`dev->ffbit` 必须先设好** | kerneldoc `:300` "should be already set up before calling this function"；`:344-345` 在 create 里就把 `dev->ffbit` 拷进 `ff->ffbit`，之后再设的位不会被拷 |
| 2 | **ops 必须在 `input_register_device()` 之前挂好** | kerneldoc `:301-302`；注册之后设备立刻可被打开，用户态马上能上传效果 |
| 3 | `max_effects` ∈ [1, 96] | `:310-313` 为 0 时 `dev_err "cannot allocate device without any effects"` + `-EINVAL`；`:315-318` 超过 `FF_MAX_EFFECTS` 时 `dev_err "cannot allocate more than FF_MAX_EFFECTS effects"`。`FF_MAX_EFFECTS` = `FF_GAIN` = `0x60` = 96（`include/uapi/linux/input.h:513` / `:504`） |

★ **不要自己填 `dev->event` 和 `dev->flush`**：`:339-340` 无条件覆盖成 `input_ff_flush` / `input_ff_event`。填了会被静默丢掉——这是"我明明设了回调却从来不被调"这类问题的一个具体来源。

★ **两份 `ffbit`，含义不同**（`include/linux/input.h:529-530` 的 kerneldoc 讲得很清楚）：

| 位图 | 含义 | 谁写 |
| --- | --- | --- |
| `dev->ffbit` | 用户态看到的能力，**含仿真出来的** | 驱动 + `ff-core`（`:348-349`）+ `ff-memless`（`:541-546`） |
| `ff->ffbit` | 硬件**真**支持的 | `input_ff_create()` 在 `:344-345` 从 `dev->ffbit` 快照一份 |

`:348-349` 是这个区分的用处：硬件支持 `FF_PERIODIC` 时，核心往 `dev->ffbit` 里加 `FF_RUMBLE`，然后 `compat_effect()`（`:50-83`）把用户态传来的 rumble 翻成一个 50 ms 的正弦（`:66-69`），幅度按 `strong/3 + weak/6`（`:63-64`）。**驱动收到的永远是 `FF_PERIODIC`，不是 `FF_RUMBLE`。**

### §5.3 五个回调的上下文——一半不能睡

kerneldoc `include/linux/input.h:542-544` 一句话概括：

> Note that playback(), set_gain() and set_autocenter() are called with dev->event_lock spinlock held and interrupts off and thus may not sleep.

| 回调 | 上下文 | 能睡？ | 必需？ |
| --- | --- | --- | --- |
| `upload(dev, effect, old)` | ioctl，持 `ff->mutex` | ✅ | **是** |
| `erase(dev, effect_id)` | ioctl，持 `ff->mutex` | ✅ | 否 |
| `playback(dev, effect_id, value)` | 事件路径，持 `dev->event_lock`，关中断 | ❌ | **是** |
| `set_gain(dev, gain)` | 同上 | ❌ | 仅当设了 `FF_GAIN` |
| `set_autocenter(dev, magnitude)` | 同上 | ❌ | 仅当设了 `FF_AUTOCENTER` |

★ 这就是为什么 memless 驱动几乎都在 `play_effect()` 里 `schedule_work()`——I2C/SPI/regulator 操作全要睡眠。`drivers/input/misc/gpio-vibra.c:88` 就是这个写法（`schedule_work(&vibrator->play_work)`），`drivers/input/misc/gpio-vibra.c:77-91` 整个回调里没有一处可能睡眠的调用。

★★ **设了能力位却不实现 op = NULL 解引用。** `input_ff_event()`（`drivers/input/ff-core.c:260-290`）：

```c
	case FF_GAIN:
		if (!test_bit(FF_GAIN, dev->ffbit) || value > 0xffffU)
			break;

		ff->set_gain(dev, value);
		break;
```

`:273` 唯一的门是 `test_bit(FF_GAIN, dev->ffbit)`，**没有 `if (ff->set_gain)`**。`FF_AUTOCENTER` 一侧（`:276-281`）同样。所以在 `dev->ffbit` 里点了 `FF_GAIN` 而没挂 `set_gain`，用户态一写增益就是持 `event_lock`、关中断状态下的 NULL 调用——不可恢复。走 memless 的不会碰到这个坑（`:525` 设位、`:537` 挂 op，成对），自己写完整 FF 的要当心。

`default` 分支（`:283-286`）是播放：`check_effect_access(ff, code, NULL)`（`:23-34`）通过后才 `ff->playback()`。传 NULL 的 `file` 参数让 `:30` 的所有者检查被跳过——**内核内部注入的播放请求不受"谁上传的谁才能播"限制**。

### §5.4 清理：一句都不用写

`input_ff_destroy()`（`drivers/input/ff-core.c:363-376`）的 kerneldoc `:359-361` 写着 "only needed in error path"。正常路径由 `input_dev_release()` 调（`drivers/input/input.c:1663`）。它按序放四样：`ff->destroy(ff)`（`:369-370`）→ `kfree(ff->private)`（`:371`）→ `kfree(ff->effects)`（`:372`）→ `kfree(ff)`（`:373`），并且**先**清 `EV_FF` 位（`:367`，在 `if (ff)` 之外，所以 `ff` 为 NULL 也会清）。

★ 你唯一需要写的清理是 `input->close`：把马达停掉。核心不会替你停——`input_ff_flush()`（`:235-250`）只擦效果，`ml_ff_destroy()` 的注释 `:489-495` 专门解释了为什么它还要再 `del_timer_sync()` 一次（flush 停了效果但没停 timer）。设备被关闭而马达还在转是这一类驱动最常见的可见 bug。

---

## §6 角色六：写一个 handler（监听 / 过滤）

handler 是"接管 input 设备事件流"的一侧。全树 13 个文件调 `input_register_handler()`（含定义文件 `drivers/input/input.c` 本身）：

```
grep -rlE '\binput_register_handler\(' --include=*.c .   →   13
```

★ 本节**只讲监听型和过滤型**（不创建字符设备的那种，如 `04-extensions.md` §13 的 evbug / apm-power 和 §9 的 input-leds）。要新写一个出字符设备的 handler，需要自己管次设备号段、`file_operations`、client 链表和唤醒队列——那是 evdev 那一级的工作量，超出"角色骨架"的范围，`03-internals.md` §B4 和 `04-extensions.md` §1~§4 是读那条路的入口。

### §6.1 三种事件回调，只会用其中一条路

`struct input_handler` 定义在 `include/linux/input.h:310-331`。事件相关的三个成员和它们的实际使用情况：

| 成员 | 行 | 语义 | 全树实现者 |
| --- | --- | --- | --- |
| `filter` | `:317` | 返回 `true` **吃掉**这个事件，后面的 handler 看不到 | 2 个：`drivers/tty/sysrq.c:1030`、`drivers/macintosh/mac_hid.c:155` |
| `events` | `:315-316` | 一次拿到整帧 `struct input_value` 数组 | 1 个：`drivers/input/evdev.c:1422` |
| `event` | `:314` | 一次一个事件 | 其余全部 |

派发逻辑在 `input_to_handler()`（`drivers/input/input.c:105-133`）：

```c
	if (handler->filter) {
		for (v = vals; v != vals + count; v++) {
			if (handler->filter(handle, v->type, v->code, v->value))
				continue;
			if (end != v)
				*end = *v;
			end++;
		}
		count = end - vals;
	}

	if (!count)
		return 0;

	if (handler->events)
		handler->events(handle, vals, count);
	else if (handler->event)
		for (v = vals; v != vals + count; v++)
			handler->event(handle, v->type, v->code, v->value);
```

三个要点：

- ★ `events` 和 `event` 是 `else if`（`:126-130`）——**两个都填只有 `events` 会被调**，`event` 静默失效。
- ★ `filter` 不是"另一种 event"，它和 `event`/`events` 可以并存：先过滤，剩下的再走 `events`/`event`（`:112-121` 在 `:126` 之前）。
- ★ 过滤是**原地压缩这个 `vals` 数组**（`:116-118`），改的是所有后续 handler 共用的那份数据。`count` 归零时 `input_pass_values()` 的 `:160-161` 直接 `break`，后面的 handler 一个事件都收不到。

### §6.2 骨架：一个只监听的 handler

```c
/* 骨架，未编译 */
struct mywatch_handle {
	struct input_handle handle;
	/* 自己的每设备状态 */
};

static void mywatch_event(struct input_handle *handle, unsigned int type,
			  unsigned int code, int value)
{
	/* 持 dev->event_lock、关中断——不能睡眠、不能 GFP_KERNEL */
	if (type == EV_KEY && value == 1)
		pr_info("mywatch: %s key %u down\n", handle->dev->name, code);
}

static int mywatch_connect(struct input_handler *handler,
			   struct input_dev *dev,
			   const struct input_device_id *id)
{
	struct mywatch_handle *mh;
	int error;

	mh = kzalloc(sizeof(*mh), GFP_KERNEL);
	if (!mh)
		return -ENOMEM;

	mh->handle.dev = input_get_device(dev);
	mh->handle.handler = handler;
	mh->handle.name = "mywatch";
	mh->handle.private = mh;

	error = input_register_handle(&mh->handle);
	if (error)
		goto err_free;

	error = input_open_device(&mh->handle);
	if (error)
		goto err_unregister;

	return 0;

err_unregister:
	input_unregister_handle(&mh->handle);
err_free:
	input_put_device(dev);
	kfree(mh);
	return error;
}

static void mywatch_disconnect(struct input_handle *handle)
{
	struct mywatch_handle *mh = handle->private;

	input_close_device(handle);
	input_unregister_handle(handle);
	input_put_device(handle->dev);
	kfree(mh);
}

static const struct input_device_id mywatch_ids[] = {
	{ .driver_info = 1 },		/* 匹配所有设备 */
	{ },
};

static struct input_handler mywatch_handler = {
	.event		= mywatch_event,
	.connect	= mywatch_connect,
	.disconnect	= mywatch_disconnect,
	.name		= "mywatch",
	.id_table	= mywatch_ids,
};

static int __init mywatch_init(void)
{
	return input_register_handler(&mywatch_handler);
}

static void __exit mywatch_exit(void)
{
	input_unregister_handler(&mywatch_handler);
}

module_init(mywatch_init);
module_exit(mywatch_exit);
```

★ 树里**没有** `module_input_handler()` 这类便利宏（`grep -rn 'module_input_handler' .` 零命中）——`module_init`/`module_exit` 要自己写，`input_register_handler()` 的返回值要自己检查（它会 `mutex_lock_interruptible()`，`drivers/input/input.c:2533`，可以返回 `-EINTR`）。

四步的次序（`connect` 里）和 `04-extensions.md` §13.1 拆的 evbug 五步一致：**`input_register_handle()` → `input_open_device()`**，失败按逆序回滚。`drivers/input/evbug.c:28-62` 是全树最短的完整实现，`:89-100` 是最短的模块样板，可以对着看。

★ `input_get_device()` / `input_put_device()` 这一对不是所有 handler 都写——evbug 就没写（`drivers/input/evbug.c:28-62` 里没有）。省掉是安全的：`input_unregister_device()` 会先 `input_disconnect_device()` 再释放（`03-internals.md` §A12.3），handle 一定先被拆掉。写上是更保守的做法。

### §6.3 `id_table` 与匹配：三件反直觉的事

`id_table` 必须以一个**全零项结尾**（`{ }`）。匹配逻辑在 `input_match_device_id()`（`drivers/input/input.c:1000-1033`），`03-internals.md` §A5.1 已经逐行拆过，这里只列对写 handler 有直接影响的三条：

| 事实 | 依据 | 后果 |
| --- | --- | --- |
| ★ 所有 `INPUT_DEVICE_ID_MATCH_*BIT` 标志在运行时**不起作用** | `:1019-1028` 的十个 `bitmap_subset()` 完全不看 `id->flags` | 想按能力位筛选，**只填位图即可，不用填 flags**；flags 只影响 `scripts/mod/file2alias.c:776` 生成的 modalias |
| ★ 位图匹配是**子集**语义，不是"有交集" | `bitmap_subset(id->keybit, dev->keybit, …)` | `id->keybit` 里点的每一位设备都得有；**空位图匹配一切**（evbug 的 `{ .driver_info = 1 }`） |
| ★ 不填 `.match` 等于通过 | `:1043` 的 `(!handler->match \|\| handler->match(handler, dev))` | 需要"位图表达不了"的筛选条件时才实现 `.match`，全树只有 3 个（见下表） |

★ `.driver_info = 1` 不是魔法：`input_match_device()` 的循环条件是 `for (id = handler->id_table; id->flags \|\| id->driver_info; id++)`（`drivers/input/input.c:1041`），**只要这两个字段有一个非零就不算结束哨兵**。`drivers/input/evbug.c:74-77` 的注释直接写着 `/* Matches all devices */` + `/* Terminating zero entry */`。

三个 `.match` 实现（全树，即上面 13 个注册者的全集里）：

| 实现（定义） | 赋值点 | 补的是什么筛选 |
| --- | --- | --- |
| `kbd_match`（`drivers/tty/vt/keyboard.c:1552-1567`） | `:1645` | 有 `EV_SND` 直接收（`:1554-1555`）；否则要求按键位里有 `BTN_MISC` **以下**的码（`:1558-1560`，即真正的键，不是鼠标/手柄按钮）或盲文键（`:1561-1563`） |
| `joydev_match`（`drivers/input/joydev.c:898-909`） | `:1078` | 排掉黑名单设备（`:901`）和"绝对坐标鼠标"（`:905`）——两者位图上都像摇杆 |
| `snd_dualsense_ih_match`（`sound/usb/mixer_quirks.c:571-616`） | `:761` | 比 sysfs 路径前缀（`:609` 的 `memcmp`），确认这个 input 设备真挂在自己那台 USB 声卡下面；注释 `:601-602` 说明动机是"同一台主机插了两个同款手柄" |

```
grep -rn '\.match\b' drivers/tty/vt/keyboard.c drivers/input/joydev.c sound/usb/mixer_quirks.c
```

### §6.4 `disconnect` 是必需的，而且必须调 `input_unregister_handle()`

`input_unregister_handler()`（`drivers/input/input.c:2558-2573`）：

```c
	list_for_each_entry_safe(handle, next, &handler->h_list, h_node)
		handler->disconnect(handle);
	WARN_ON(!list_empty(&handler->h_list));
```

- `:2565` **无条件**调 `handler->disconnect(handle)`——没有 `if (handler->disconnect)`。不填 = 模块卸载时 NULL 调用。
- `:2566` 的 `WARN_ON` 检查 `h_list` 已空。`handle` 是在 `input_register_handle()` 的 `:2650` 挂上 `handler->h_list` 的，只有 `input_unregister_handle()`（`:2669-2683`）的 `:2673` 会摘。**`disconnect` 里漏调 `input_unregister_handle()` 就会 WARN，并留下悬空链表。**

`input_register_handle()`（`:2619-2656`）替你做三件事：

| 行 | 动作 |
| --- | --- |
| `:2637-2640` | ★ **有 `filter` 的插链表头，没有的插尾**——这就是"过滤器先跑"的全部实现 |
| `:2650` | 挂到 `handler->h_list` |
| `:2652-2653` | 有 `.start` 就调（全树 3 个实现：`drivers/tty/vt/keyboard.c:1648`、`net/rfkill/input.c:312`、`sound/usb/mixer_quirks.c:764`） |

kerneldoc `include/linux/input.h:307-308` 补一条保证：**"input core serializes calls to connect() and disconnect() methods"**，所以这两个回调里不用自己加锁保护 handler 级的状态。

### §6.5 上下文：三个回调不能睡，两个可以

| 回调 | 上下文 | 能睡？ |
| --- | --- | --- |
| `event` / `events` / `filter` | 持 `dev->event_lock`，关中断（kerneldoc `include/linux/input.h:273-278`；调用点 `drivers/input/input.c:151-165` 在 `rcu_read_lock()` 内） | ❌ |
| `connect` | 注册 handler 时对每个已有设备调（`:2541-2542`，持 `input_mutex`），或注册设备时对每个已有 handler 调 | ✅ |
| `disconnect` | `input_unregister_handler()` 的 `:2564-2565`，持 `input_mutex`（`:2562`） | ✅ |
| `start` | `input_register_handle()` 尾部（`:2652-2653`）**或**别人放弃 grab 时（`:593-595`，遍历 `dev->h_list` 对每个已 open 的 handle 调；kerneldoc `include/linux/input.h:285-287`） | ✅ |

★ `connect` 里可以 `kzalloc(GFP_KERNEL)`（骨架里就是），但 `event` 里绝对不行。这是新写 handler 最容易踩的一条：想在事件回调里 `kmalloc` 一个通知结构、或者调 `i2c_transfer()` 上报给别的芯片，都会在 `event_lock` 里睡死。要转手就交给工作队列——`net/rfkill/input.c` 整个模块就是这个模式的范例：事件回调只置位，实际动作在 `rfkill_op_handler()`（`:94`）里跑，由 `:137` 的 `DECLARE_DELAYED_WORK` + `:148` 的 `schedule_delayed_work()` 驱动。

★ `start` 会被调**两次以上**：注册时一次，之后每次有进程 grab 完又释放，你的 `start` 都会再被调一次（`:593-595`）。所以它必须是幂等的——把"初始化一次性状态"放进 `connect`，`start` 里只做"重新同步当前状态到我这一侧"这种可重复的事。`kbd_start()`（`drivers/tty/vt/keyboard.c:1617-1625`）就是这样：关掉 tasklet，把当前 `ledstate` 重新推给这个 handle（`:1621-1622`），再打开。

---

## §7 角色七：serio 两侧——提供端口 vs 消费端口

serio 是一条**自带 `bus_type`** 的字节流总线（`drivers/input/serio/serio.c:1010-1021`），它本身完全不认识 `input_dev`。写代码的人会落在两个完全不同的位置上，**填的结构、注册的函数、能不能睡眠都不一样**：

| 你在做什么 | 填哪个结构 | 注册函数 | 造 `input_dev` 吗 |
| --- | --- | --- | --- |
| 我有一块 PS/2 控制器 / 一个 UART 口，想把字节流交出去 | `struct serio`（`include/linux/serio.h:20-64`） | `serio_register_port()`（宏，`:94-95`） | ❌ 不造 |
| 我知道某种设备的字节协议，想把字节变成事件 | `struct serio_driver`（`:67-82`） | `serio_register_driver()`（宏，`:104-105`） | ✅ 走 §1 那套 |

★ `04-extensions.md` §2.1 的表里把后者叫「端口驱动」（因为它绑在端口上），本篇为了和"提供端口的那一侧"区分，下文统一称**协议驱动**。两个说法指的是同一个 `struct serio_driver`。

### §7.1 提供端口：六个字段 + 五个回调

代表实现 `drivers/input/serio/ambakmi.c`（ARM PrimeCell PL050，**本树 arm64 DTS 真在用**，见 `04-extensions.md` §2.4）：

```c
/* 骨架，未编译；对照 drivers/input/serio/ambakmi.c:106-171 */
struct myport {
	struct serio	*io;
	void __iomem	*base;
	unsigned int	irq;
};

static irqreturn_t myport_isr(int irq, void *dev_id)
{
	struct myport *p = dev_id;

	while (readb(p->base + STAT) & RX_FULL)
		serio_interrupt(p->io, readb(p->base + DATA), 0);

	return IRQ_HANDLED;
}

static int myport_write(struct serio *io, unsigned char val)
{
	struct myport *p = io->port_data;
	int timeleft = 10000;

	while (!(readb(p->base + STAT) & TX_EMPTY) && --timeleft)
		udelay(10);
	if (!timeleft)
		return -ETIMEDOUT;		/* 非 0 即失败，见下面的 ★ */

	writeb(val, p->base + DATA);
	return 0;
}

static int myport_open(struct serio *io)		/* 可以睡眠 */
{
	struct myport *p = io->port_data;

	return request_irq(p->irq, myport_isr, 0, "myport", p);
}

static void myport_close(struct serio *io)
{
	struct myport *p = io->port_data;

	free_irq(p->irq, p);
}

static int myport_probe(struct platform_device *pdev)
{
	struct myport *p;
	struct serio *io;

	p = devm_kzalloc(&pdev->dev, sizeof(*p), GFP_KERNEL);
	io = kzalloc(sizeof(*io), GFP_KERNEL);	/* ★ 不能用 devm_，见下 */
	if (!p || !io) {
		kfree(io);
		return -ENOMEM;
	}

	io->id.type	= SERIO_8042;		/* 决定谁能绑上来 */
	io->write	= myport_write;
	io->open	= myport_open;
	io->close	= myport_close;
	io->port_data	= p;
	io->dev.parent	= &pdev->dev;
	strscpy(io->name, dev_name(&pdev->dev), sizeof(io->name));
	strscpy(io->phys, dev_name(&pdev->dev), sizeof(io->phys));

	p->io = io;
	platform_set_drvdata(pdev, p);

	serio_register_port(io);		/* void，无返回值 */
	return 0;
}

static int myport_remove(struct platform_device *pdev)
{
	struct myport *p = platform_get_drvdata(pdev);

	serio_unregister_port(p->io);	/* 同步；返回后 p->io 已不可再用 */
	return 0;
}
```

五个端口回调，以及"填不填"的后果：

| 回调 | 声明 | 不填会怎样 |
| --- | --- | --- |
| `write` | `include/linux/serio.h:34` | `serio_write()`（`:122-128`）直接返回 `-1`——协议驱动会以为写失败。只收不发的口可以不填 |
| `open` | `:35` | 不填 = `serio_open()` 的 `:971` 跳过，视为成功。**申请中断的正确位置就是这里**，不是 `probe` |
| `close` | `:36` | 不填 = `serio_close()` 的 `:982` 跳过 |
| `start` | `:37` | 在 `device_add()` **之前**被调（`serio_add_port()` 的 `:536-539`） |
| `stop` | `:38` | 在 `serio_destroy_port()` 的 `:559-560` 被调 |

★ **`serio_register_port()` 是异步的。** `__serio_register_port()`（`:692-696`）只做两件事：`serio_init_port()` 初始化 + 把 `SERIO_REGISTER_PORT` 事件排进工作队列。**函数返回时端口设备还没 `device_add()`，也没有任何驱动绑上来**，所以它是 `void`——没有失败可报。真正的 `serio_add_port()` 在工作线程里跑（`04-extensions.md` §2.2）。反过来，`serio_unregister_port()`（`:702-709`）是**同步**的，注释 `:700` 写着 `Synchronously unregisters serio port`。

★ **`struct serio` 必须 `kzalloc`，不能 `devm_`、不能放在你自己的结构里。** 端口的生命周期由引用计数管：`serio->dev.release = serio_release_port`（`:509`），而 `serio_release_port()`（`:483-489`）里就是 `kfree(serio)`。`ambakmi.c` 的 remove（`:162-171`）只 `kfree(kmi)` 不 `kfree(io)`，就是这个原因。用 `devm_` 或内嵌会变成双重释放。

★ **`serio->start()` 早于 `device_add()`**，意味着端口可能在**还没有任何协议驱动**的时候就开始收字节。核心为此留了一条兜底路：`serio_interrupt()` 的 `:999-1001` 在 `serio->drv` 为空且 `dfl == 0` 时调 `serio_rescan()` 重新找驱动，并返回 `IRQ_HANDLED`。所以"插上键盘按一下才被识别"这种行为是设计好的，不是 bug。

★ **`serio_pause_rx()` / `serio_continue_rx()` 是 `spin_lock_irq()` / `spin_unlock_irq()`**（`include/linux/serio.h:154-162`），**不是** `irqsave`/`irqrestore` 版本。在已经关了中断的上下文里调它，`continue_rx` 会把中断打开——这一对只能从可睡眠或明确开中断的上下文用。

★ `.write` 的返回值**只有"0 / 非 0"两档语义**，非 0 的具体值没人解读。`ambakmi.c:61` 超时返回的是 `SERIO_TIMEOUT`（`include/uapi/linux/serio.h:21`，`_BITUL(0)` = 1）——那其实是 `serio_interrupt()` 第三个参数 `dfl` 的**标志位**，借到返回值上属于命名串味。骨架里写 `-ETIMEDOUT` 更清楚，效果相同（都非 0）。

### §7.2 消费端口：协议驱动（`struct serio_driver`）

这一侧才是造 `input_dev` 的角色——它把字节流翻译成事件，走的还是 §1 那五步，只是**入口不是 `platform_driver.probe` 而是 `serio_driver.connect`**。代表实现 `drivers/input/keyboard/xtkbd.c`（152 行，全树最短的完整协议驱动之一）。

```c
/* 骨架，未编译；对照 drivers/input/keyboard/xtkbd.c:44-152 */
struct myproto {
	struct input_dev	*dev;
	struct serio		*serio;
	unsigned char		keycode[128];
	char			phys[32];
};

static irqreturn_t myproto_interrupt(struct serio *serio, unsigned char data,
				     unsigned int flags)
{
	struct myproto *mp = serio_get_drvdata(serio);

	/* ★ 持 serio->lock、关中断——不能睡眠 */
	input_report_key(mp->dev, mp->keycode[data & 0x7f], !(data & 0x80));
	input_sync(mp->dev);

	return IRQ_HANDLED;
}

static int myproto_connect(struct serio *serio, struct serio_driver *drv)
{
	struct myproto *mp;
	struct input_dev *input;
	int err = -ENOMEM;

	mp = kzalloc(sizeof(*mp), GFP_KERNEL);
	input = input_allocate_device();	/* ★ 不能用 devm_ 版，见下 */
	if (!mp || !input)
		goto fail1;

	mp->serio = serio;
	mp->dev = input;
	snprintf(mp->phys, sizeof(mp->phys), "%s/input0", serio->phys);

	input->name		= "My serio device";
	input->phys		= mp->phys;
	input->id.bustype	= BUS_XTKBD;
	input->dev.parent	= &serio->dev;	/* ★ 这里必须自己设 */

	input_set_capability(input, EV_KEY, KEY_A);	/* 步 3，注册前声明完 */

	serio_set_drvdata(serio, mp);		/* ★ 必须在 serio_open() 之前 */

	err = serio_open(serio, drv);
	if (err)
		goto fail2;

	err = input_register_device(input);
	if (err)
		goto fail3;

	return 0;

fail3:
	serio_close(serio);
fail2:
	serio_set_drvdata(serio, NULL);
fail1:
	input_free_device(input);
	kfree(mp);
	return err;
}

static void myproto_disconnect(struct serio *serio)
{
	struct myproto *mp = serio_get_drvdata(serio);

	serio_close(serio);
	serio_set_drvdata(serio, NULL);
	input_unregister_device(mp->dev);
	kfree(mp);
}

static const struct serio_device_id myproto_serio_ids[] = {
	{
		.type	= SERIO_XT,
		.proto	= SERIO_ANY,
		.id	= SERIO_ANY,
		.extra	= SERIO_ANY,
	},
	{ 0 }
};
MODULE_DEVICE_TABLE(serio, myproto_serio_ids);

static struct serio_driver myproto_drv = {
	.driver		= { .name = "myproto", },
	.description	= "My serio protocol",
	.id_table	= myproto_serio_ids,
	.interrupt	= myproto_interrupt,
	.connect	= myproto_connect,
	.disconnect	= myproto_disconnect,
};

module_serio_driver(myproto_drv);	/* ★ 这个宏是真的，见下 */
```

七个回调的全树实现率（分母 = 39 个 `struct serio_driver` 静态实例，39 个文件）：

| 回调 | 声明 | 实现数 | 必填？ |
| --- | --- | --- | --- |
| `interrupt` | `include/linux/serio.h:74` | 39 | ★ **事实必填**，见下 |
| `connect` | `:75` | 39 | 必填：`serio_connect_driver()` 的 `:44` 无条件调 `drv->connect()` |
| `disconnect` | `:78` | 39 | 必填：`serio_disconnect_driver()` 的 `:65-66` 检查的是 `serio->drv`，不是函数指针 |
| `reconnect` | `:76` | 4 | 可选（`serio_raw.c:436`、`ps2mult.c:301`、`atkbd.c:1427`、`psmouse-base.c:1786`） |
| `cleanup` | `:79` | 2 | 可选，`serio_cleanup()` 的 `:788` 有 NULL 检查（`atkbd.c:1429`、`psmouse-base.c:1789`） |
| `fast_reconnect` | `:77` | 1 | 可选（只有 `drivers/input/mouse/psmouse-base.c:1787`） |
| `write_wakeup` | `:73` | 1 | 可选，`serio_drv_write_wakeup()` 的 `:132` 有 NULL 检查（只有 `drivers/input/joystick/iforce/iforce-serio.c:247`） |

重跑（分母和 `.interrupt` 那一格）：

```bash
grep -rlE --include=*.c 'struct serio_driver [a-z_0-9]* = \{' . | wc -l
F=$(grep -rlE --include=*.c 'struct serio_driver [a-z_0-9]* = \{' .)
grep -rlE --include=*.c '^[[:space:]]*\.interrupt[[:space:]]*=' $F | wc -l
```

两条都输出 `39`。

★ **`.interrupt` 不填就是内核崩。** `serio_interrupt()` 只检查 `serio->drv` 非空，**不检查函数指针**：

```c
/* drivers/input/serio/serio.c:997-998 */
        if (likely(serio->drv)) {
                ret = serio->drv->interrupt(serio, data, dfl);
```

而且这行是在 `spin_lock_irqsave(&serio->lock, flags)`（`:995`）里面、通常还在硬中断上下文里跑的。39 个实例全都填了这一项——**不是巧合，是这里没有兜底**。

★ **`serio_open()` 返回的是 `-1`，不是 errno。** `drivers/input/serio/serio.c:967-976`：端口的 `open()` 失败时 `:973` `return -1`。这个值会一路原样冒出去——`connect()` 返回它，`serio_driver_probe()`（`:770-776`）原样返回，驱动核心把 `-1` 当成 `-EPERM` 记进 `dmesg`。看到 `probe failed with error -1` 这种日志时不要去找哪里返回了 `-EPERM`，源头就是这一行。（`serio_write()` 在没有 `.write` 时也返回 `-1`，`include/linux/serio.h:127`。两处都记进 `99-evidence.md` 的设计债。）

★ **`serio_set_drvdata()` 必须在 `serio_open()` 之前。** `serio_open()` 的第一件事是 `serio_set_drv(serio, drv)`（`:969`），赋值之后**端口的下一个字节就会立刻进你的 `.interrupt`**——而 `.interrupt` 第一件事是 `serio_get_drvdata()`。次序颠倒 = 拿到 NULL 再解引用。骨架里 `serio_set_drvdata()` 在 `serio_open()` 上一行不是排版偏好。

★ **全树 39 个协议驱动没有一个用 `devm_input_allocate_device()`**——40 处分配全是非 devm 版（`grep -hoE 'devm_input_allocate_device|input_allocate_device' $F | sort | uniq -c` → `40 input_allocate_device`）。这不是"不能用"：宿主设备会是 `&serio->dev`，而绑在它上面的驱动就是你自己，unbind 即 `disconnect()`，正是 kerneldoc `drivers/input/input.c:2063-2065` 说的时机。但 `serio_open()` / `serio_close()` 这一对无论如何要手写，input 侧也做成显式配对，回滚顺序才一眼看得清（`drivers/input/keyboard/xtkbd.c:112-116` 三级 label）。照抄树内写法是安全选择。

★ **`module_serio_driver()` 存在**（`include/linux/serio.h:118-120`，`module_driver()` 的包装），和 §6 里那个不存在的 `module_input_handler()` 正好相反。这一对是"名字很像但一个真一个假"的典型，`grep -nE 'define module_[a-z_]*driver' include/linux/serio.h` 一条命令就能分清。

★ **`id_table` 的终止条件是 `type || proto` 两个字段**（`serio_match_port()` 的 `:72`），不是"整项为零"。所以 `{ 0 }` 这种只写一个零的哨兵是够的（`drivers/input/keyboard/xtkbd.c:136`），但**如果你只填 `.id` / `.extra` 而把 `.type` 和 `.proto` 都留 0，这一项会被当成表尾**，后面的项一个都不会被看。四个字段各支持 `SERIO_ANY` 通配（`:73-76`，`SERIO_ANY` = `0xff`，`include/linux/mod_devicetable.h:240`）——非零，所以 `.type = SERIO_ANY` 语法上是合法的"匹配任何端口类型"，只是全树 39 个驱动里**没有一个**这么写（`grep -rn '\.type[[:space:]]*=[[:space:]]*SERIO_ANY' --include=*.c .` 零命中）：端口类型是唯一能区分 PS/2、RS-232、XT 的字段，通配它等于抢别人的设备。★ 另外 `serio_bus_match()` 的 `:885-886`：**端口或驱动任一方设了 `manual_bind`，自动匹配直接返回 0**，只能靠 sysfs 手动绑（`04-extensions.md` §2.3）。

---

## §8 角色八：RMI4 function 驱动

RMI4 是 Synaptics 触摸板/触摸屏的寄存器协议，它把一块芯片的能力切成编号的「function」（F01 设备控制、F11/F12 二维传感器、F30/F3A GPIO 按键、F34 固件升级、F54/F55 诊断）。每个 function 是 `rmi_bus` 上的一个设备，写一个 function 驱动就是写一个 `struct rmi_function_handler`。整个子框架的结构见 `04-extensions.md` §5。

### §8.1 先说清：这个角色目前只能在树内写

★ **`rmi_register_function_handler()` 不满足 §0.1 的第二条判据。** 它确实导出了：

```
drivers/input/rmi4/rmi_bus.c:319   EXPORT_SYMBOL_GPL(__rmi_register_function_handler);
drivers/input/rmi4/rmi_bus.c:332   EXPORT_SYMBOL_GPL(rmi_unregister_function_handler);
```

但声明只在**私有头** `drivers/input/rmi4/rmi_bus.h:92-95`（宏 `rmi_register_function_handler`）和 `:97`（注销）里，`include/` 下一个字都没有——`grep -n 'function_handler\|rmi_register_function' include/linux/rmi.h` 零命中（`include/linux/rmi.h` 共 377 行，只有传输层和平台数据）。`struct rmi_function_handler` 本身也定义在 `rmi_bus.h:75-87`。树外模块 `#include <linux/rmi.h>` 拿不到类型，编译就过不去。

★ **而且新增 function 是四处联动的编译期改动，不是"写个模块加载上去"：**

| 要改的地方 | 位置 | 内容 |
| --- | --- | --- |
| extern 声明 | `drivers/input/rmi4/rmi_driver.h:132-140` | 九个 `extern struct rmi_function_handler rmi_fXX_handler;` |
| 注册数组 | `drivers/input/rmi4/rmi_bus.c:352-378` | `static struct rmi_function_handler *fn_handlers[]`，f01 无条件，其余八个各带 `#ifdef CONFIG_RMI4_FXX` |
| Kconfig | `drivers/input/rmi4/Kconfig` | 新增 `RMI4_FXX` 选项 |
| Makefile | `drivers/input/rmi4/Makefile` | 把 `.o` 挂进 `rmi_core-$(CONFIG_RMI4_FXX)` |

`fn_handlers[]` 由 `rmi_register_function_handlers()`（`rmi_bus.c:393-412`）在核心模块 init 时统一注册（`:443` 调用，`:470` 反向注销），失败时回滚已注册的部分（`:410` 的 `__rmi_unregister_function_handlers(i - 1)`）。也就是说 `rmi_register_function_handler()` 这个导出符号在全树**只有 `rmi_bus.c` 自己调**——导出是历史遗留，不是给树外用的接口。

所以本节的定位是：**照着树内的 handler 改一个新 function**，而不是"外挂一个模块"。

### §8.2 结构体与匹配规则

`struct rmi_function_handler`（`drivers/input/rmi4/rmi_bus.h:75-87`）：

| 成员 | 类型/原型 | 说明 |
| --- | --- | --- |
| `driver` | `struct device_driver` | 内嵌，只需填 `.name`；`.bus` / `.owner` / `.mod_name` / `.probe` / `.remove` 由 `__rmi_register_function_handler()` 覆写（`rmi_bus.c:304-308`） |
| `func` | `u8` | function 号，**匹配规则的全部内容** |
| `probe` | `int (*)(struct rmi_function *)` | 可选（`rmi_bus.c:212` 有 NULL 检查） |
| `remove` | `void (*)(struct rmi_function *)` | 可选（`:233`） |
| `config` | `int (*)(struct rmi_function *)` | 可选（`rmi_driver.c:88`）；首次初始化和每次 reset 之后各调一次（`rmi_bus.h:63-65`） |
| `reset` | `int (*)(struct rmi_function *)` | 可选（`rmi_driver.c:69`） |
| `attention` | `irqreturn_t (*)(int irq, void *ctx)` | **中断处理函数原型**，见 §8.4 |
| `suspend` / `resume` | `int (*)(struct rmi_function *)` | 可选（`rmi_driver.c:265` / `:299`） |

`rmi_bus.h:73` 的 kerneldoc 一句话定了返回值约定：`All callbacks are expected to return 0 on success, error code on failure.`（`attention` 例外，它返回 `irqreturn_t`。）

★ **匹配只比一个字节。** `rmi_function_match()`（`rmi_bus.c:147-153`）整个函数体就是 `fn->fd.function_number == handler->func`；`rmi_bus_match()`（`:336-345`）先分清设备是物理设备还是 function 设备，再走到它。**没有 id_table，没有通配，没有 `.match` 钩子**——和 §6 的 input handler、§7 的 serio 驱动完全不同。所以同一个 function 号在树里只能有一个 handler。

九个 handler 的回调普查（`grep -rnE '^(const )?struct rmi_function_handler [a-z_0-9]+ = \{' --include=*.c . | wc -l` → 9，全在 `drivers/input/rmi4/`）：

| handler | func | probe | remove | config | reset | attention | suspend | resume |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rmi_f01_handler` | 0x01 | ✔ | ✔ | ✔ | | ✔ | ✔ | ✔ |
| `rmi_f03_handler` | 0x03 | ✔ | ✔ | ✔ | | ✔ | | |
| `rmi_f11_handler` | 0x11 | ✔ | | ✔ | | ✔ | | ✔ |
| `rmi_f12_handler` | 0x12 | ✔ | | ✔ | | ✔ | | |
| `rmi_f30_handler` | 0x30 | ✔ | | ✔ | | ✔ | | |
| `rmi_f34_handler` | 0x34 | ✔ | | | | ✔ | | |
| `rmi_f3a_handler` | 0x3a | ✔ | | ✔ | | ✔ | | |
| `rmi_f54_handler` | 0x54 | ✔ | ✔ | ✔ | | | | |
| `rmi_f55_handler` | 0x55 | ✔ | | | | | | |
| **合计** | | 9 | 3 | 7 | **0** | 7 | 1 | 2 |

★ **`.reset` 全树零实现。** 核心确实会调它（`rmi_driver.c:69-70`，失败时打 `Reset failed with code %d.\n`），但九个 handler 一个都没填——想在硬件 reset 后做事的都写在 `.config` 里，因为 `.config` 在 reset 之后也会被调一次（`rmi_bus.h:63-65` 的 kerneldoc）。重跑：`grep -rn '\.reset[[:space:]]*=' drivers/input/rmi4/` 只有一条 `rmi_smbus.c:284`，那是 `rmi_transport_ops` 的成员，不是本结构体的。

### §8.3 骨架（照 `rmi_f3a.c`）

以 GPIO 按键类 function 为原型，对应真实文件 `drivers/input/rmi4/rmi_f3a.c`（241 行）。

```c
/* drivers/input/rmi4/rmi_fxx.c —— 未编译骨架 */
#include <linux/kernel.h>
#include <linux/rmi.h>
#include <linux/input.h>
#include <linux/slab.h>
#include "rmi_driver.h"

struct fxx_data {
	struct input_dev *input;	/* 借来的，不是自己分配的 */
	struct rmi_function *fn;
	u8 key_map[16];
};

static irqreturn_t rmi_fxx_attention(int irq, void *ctx)
{
	struct rmi_function *fn = ctx;
	struct fxx_data *fxx = dev_get_drvdata(&fn->dev);
	u8 buf;
	int error;

	/* 可以睡眠：这是 threaded IRQ 的线程体，见 §8.4 */
	error = rmi_read(fn->rmi_dev, fn->fd.data_base_addr, &buf);
	if (error) {
		dev_err(&fn->dev, "%s: Failed to read Fxx data: %d\n",
			__func__, error);
		return IRQ_RETVAL(error);
	}

	input_report_key(fxx->input, fxx->key_map[0], buf & 1);
	input_sync(fxx->input);
	return IRQ_HANDLED;
}

static int rmi_fxx_config(struct rmi_function *fn)
{
	struct fxx_data *fxx = dev_get_drvdata(&fn->dev);
	struct rmi_driver *drv = fn->rmi_dev->driver;

	if (!fxx)
		return 0;

	/* 不调这一行，attention 永远不会被触发 */
	drv->set_irq_bits(fn->rmi_dev, fn->irq_mask);
	return 0;
}

static int rmi_fxx_probe(struct rmi_function *fn)
{
	struct rmi_device *rmi_dev = fn->rmi_dev;
	struct rmi_driver_data *drv_data = dev_get_drvdata(&rmi_dev->dev);
	struct fxx_data *fxx;

	if (!drv_data->input) {
		dev_info(&fn->dev, "Fxx: no input device found, ignoring\n");
		return -ENXIO;
	}

	fxx = devm_kzalloc(&fn->dev, sizeof(*fxx), GFP_KERNEL);
	if (!fxx)
		return -ENOMEM;

	fxx->input = drv_data->input;
	fxx->fn = fn;
	fxx->key_map[0] = BTN_LEFT;
	input_set_capability(fxx->input, EV_KEY, BTN_LEFT);

	dev_set_drvdata(&fn->dev, fxx);
	return 0;
}

/* 不是 module_xxx_driver()：这个符号要被 fn_handlers[] 引用 */
struct rmi_function_handler rmi_fxx_handler = {
	.driver = {
		.name = "rmi4_fxx",
	},
	.func = 0xXX,
	.probe = rmi_fxx_probe,
	.config = rmi_fxx_config,
	.attention = rmi_fxx_attention,
};
```

对照 `rmi_f3a.c`：`:207-231` 是 probe（`:214-216` 的 `-ENXIO`、`:219` 的 `devm_kzalloc`、`:229` 的 `dev_set_drvdata`），`:94-110+` 是 config，`:56-90` 是 attention，`:233-241` 是 handler 定义。

★ **不用 `.remove`，因为一切都是 devm 的。** 三个填了 `.remove` 的 handler（f01/f03/f54）做的都是 devm 管不了的事。私有数据用 `devm_kzalloc(&fn->dev, ...)`，IRQ 用核心替你申请的 `devm_request_threaded_irq()`（§8.4），unbind 时自动回收。

★ **`.config` 里的 `set_irq_bits` 不是可选项。** 七个带 `.attention` 的 handler 里，五个在 `.config` 里调 `drv->set_irq_bits()`（`rmi_f3a.c:110`、`rmi_f03.c:159`、`rmi_f11.c:1284`/`:1289`、`rmi_f12.c:306`、`rmi_f30.c:194`）。F11/F12 还按 abs/rel 分别开不同掩码——`f11->abs_mask` 和 `f11->rel_mask` 是两个独立的位集。不设位，硬件的中断状态字节里对应位永远是 0，`rmi_process_interrupt_requests()` 就不会去派发你的虚拟中断。

### §8.4 `.attention` 是唯一一个「像中断又能睡眠」的回调

这是本篇十个角色里最特殊的一处，值得把整条链摊开：

```
真实硬件中断（DT 里 touchpad 节点的 interrupts）
  └─ rmi_driver.c:227-228  devm_request_threaded_irq(&rmi_dev->dev, pdata->irq,
                                NULL, rmi_irq_fn, irq_flags | IRQF_ONESHOT, ...)
       └─ rmi_irq_fn() 线程体 → rmi_driver.c:201 rmi_process_interrupt_requests()
            └─ rmi_driver.c:158  handle_nested_irq(irq_find_mapping(data->irqdomain, i))
                 └─ 你的 .attention(irq, fn)
```

每个 function 的虚拟中断是核心在 probe 时替你建的（`rmi_create_function_irq()`，`rmi_bus.c:174-201`）：

- `:183-184` `irq_create_mapping()` 从驱动层的 `irqdomain` 里取号，一个 function 可以有 `fn->num_of_irqs` 个；
- `:187-188` 挂到一个只有名字的假 irqchip 上（`static struct irq_chip rmi_irq_chip = { .name = "rmi4" }`，`:170-172`）；
- `:189` `irq_set_nested_thread(fn->irq[i], 1)` —— 声明"这个中断是在别人的中断线程里被派发的"；
- `:191-193` `devm_request_threaded_irq(&fn->dev, fn->irq[i], NULL, handler->attention, IRQF_ONESHOT, dev_name(&fn->dev), fn)`。

★ **`.attention` 被传给 `thread_fn` 参数，硬中断参数是 `NULL`——所以它跑在内核线程里，可以睡眠。** 这正是它敢直接调 `rmi_read()` / `rmi_read_block()`（走 I2C/SPI 传输，必然睡眠）的原因。和 §1 的 `IRQF_ONESHOT` 门铃、§7 的 `serio_driver.interrupt`（在 `spin_lock_irqsave` 里，硬中断上下文）对比，这是三种完全不同的上下文。

★ **`ctx` 参数是 `struct rmi_function *`，不是你的私有数据。** `:193` 传的是 `fn`，所以回调第一件事必须是 `dev_get_drvdata(&fn->dev)`（`rmi_f3a.c:58-59` 一带就是这么写的）。

★ **两个条件都成立才会申请中断**：`rmi_function_probe()` 的 `:218` 是 `if (fn->num_of_irqs && handler->attention)`。`num_of_irqs` 来自硬件的 function descriptor，不是你能填的——如果芯片报告这个 function 没有中断源，你填了 `.attention` 也不会被调，而且**不会有任何警告**。反过来 f54/f55 不填 `.attention`，核心就跳过整段。申请失败时的日志是 `Error %d registering IRQ\n`（`:195`）。

### §8.5 function 驱动不造 input 设备

★ **整块 RMI4 芯片只有一个 `input_dev`，属于驱动层，不属于任何 function。**

```
drivers/input/rmi4/rmi_driver.c:1188   data->input = rmi_dev->xport->input;
drivers/input/rmi4/rmi_driver.c:1190   data->input = devm_input_allocate_device(dev);
drivers/input/rmi4/rmi_driver.c:1217   input_register_device(data->input)
```

`:1188` 是传输层已经建好一个（如 HID over RMI 的场景）就借用，否则 `:1190` 自己分配。**这是全树 39 个 serio 驱动都不用、而这里用了 `devm_input_allocate_device()` 的地方**（对比 §7.2 的普查）。

后果有三条，都是能静默咬人的：

★ **能力位必须在 `input_register_device()` 之前声明完。** 所有 function 的 probe 都跑在 `:1217` 之前，`input_set_capability()`（`rmi_f3a.c:153`）、`input->keycode` / `keycodesize` / `keycodemax`（`:157-159`）、`__set_bit(INPUT_PROP_BUTTONPAD, ...)`（`:162`）都在 probe 阶段写。注册之后再改能力位，evdev 已经按旧位集生成了 `ioctl` 返回的位图，`01-api.md` §1.2 那条"注册前必须做的事"在这里是硬约束。

★ **拿不到 `drv_data->input` 就必须放弃，而且要放弃得体面。** `rmi_f3a.c:214-216` 的写法是 `dev_info("F3A: no input device found, ignoring\n")` + `return -ENXIO`——info 级别不是 err，因为这是合法配置（传输层没给 input 设备的场景），不该在正常启动日志里报错。

★ **`input_sync()` 的语义变成全芯片共享。** F11 报的绝对坐标和 F3A 报的按键落在同一个设备的同一个事件流里，一次 `input_sync()` 结束的是整块芯片这一轮的事件包。谁调 `input_sync()` 要看具体 function 的实现，`rmi_f3a.c` 的 attention 只 `input_report_key()`（`:85-87` 一带的 per-GPIO 循环）。

### §8.6 设备树侧只有一件事

★ **子节点名字是 `"rmi4-f%02x"`，两位小写十六进制。** `rmi_function_of_probe()`（`rmi_bus.c:156-168`）用这个格式串去找 function 自己的 DT 子节点，把它挂到 `fn->dev.of_node`。`:161-162` 就是 `snprintf` 那两行。

三个限制：

- 整个函数体在 `CONFIG_OF` 之外是空的；
- **格式是 `%02x`**：F3A 的节点名是 `rmi4-f3a`，F01 是 `rmi4-f01`（不是 `rmi4-f1`）；
- 它在 `handler->probe` **之前**调（`rmi_bus.c:210`），所以 probe 里可以直接读自己的 DT 属性。

具体属性由各 function 自己解析，见 `06-devicetree.md`。平台数据侧的入口是 `rmi_get_platform_data()`（`drivers/input/rmi4/rmi_bus.h:104-108`），`rmi_f3a_config()` 的 `:98-99` 就是这么取 `pdata->gpio_data` 的。

### §8.7 本节速查

| 想做的事 | 怎么做 | 依据 |
| --- | --- | --- |
| 认领一个 function 号 | 填 `.func`，别的什么都不用填 | `rmi_bus.c:147-153` |
| 读/写芯片寄存器 | `rmi_read()` / `rmi_read_block()` / `rmi_write()`，从 `fn->fd.*_base_addr` 起算 | `rmi_bus.h:134-185` |
| 让 `.attention` 真的被调 | `.config` 里 `drv->set_irq_bits(fn->rmi_dev, fn->irq_mask)` | `rmi_f3a.c:110` |
| 报事件 | 用 `drv_data->input`，不要自己 `input_allocate_device()` | `rmi_driver.c:1188-1190` |
| 私有数据 | `devm_kzalloc(&fn->dev, ...)` + `dev_set_drvdata(&fn->dev, ...)` | `rmi_f3a.c:219`/`:229` |
| 拿平台数据 | `rmi_get_platform_data(fn->rmi_dev)` | `rmi_bus.h:104-108` |
| 硬件 reset 后重新配置 | 写在 `.config` 里，不要指望 `.reset` | `rmi_bus.h:63-65`，`.reset` 全树零实现 |

---

## §9 角色九：用户态读 evdev

前八节都在内核里。这一节和下一节的代码跑在用户态，编译时不需要内核头文件之外的任何东西——但要读的头文件是 `include/uapi/linux/input.h`（518 行）和 `include/uapi/linux/input-event-codes.h`。内核侧的实现全在 `drivers/input/evdev.c`（1446 行），本节所有行号都指向它。

设备节点是 `/dev/input/eventN`，主设备号 13（`INPUT_MAJOR`），次设备号分配规则见 `03-internals.md` §B4。

### §9.1 事件结构与两个必须知道的宏

```c
/* include/uapi/linux/input.h:28-47 */
struct input_event {
	/* 32/64 位 time_t 分叉，不要直接写 .time.tv_sec */
	__u16 type;
	__u16 code;
	__s32 value;
};
```

★ **时间字段必须通过 `input_event_sec` / `input_event_usec` 两个宏访问。** 结构体前半段有三套条件编译分支（`:29-43`）：传统 `struct timeval time`、`__kernel_ulong_t __sec/__usec`、以及 sparc64 的补 pad 变体。两个宏（`:31-32` 和 `:41-42`）在每个分支里都被定义成对应的成员名。**直接写 `ev.time.tv_sec` 的代码在定义了 `__USE_TIME_BITS64` 的 32 位 libc 上编译不过**，这是 2038 年问题在 input ABI 上的落点。

`EV_VERSION` 是 `0x010001`（`:53`），用 `EVIOCGVERSION` 读回来的就是它。

### §9.2 骨架

```c
/* 未编译骨架：读一个 event 设备并打印 */
#include <linux/input.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <time.h>

int main(int argc, char **argv)
{
	int fd, clk = CLOCK_MONOTONIC, ver;
	char name[256] = "?";
	unsigned long keybit[(KEY_MAX / (8 * sizeof(long))) + 1];
	struct input_event ev[64];
	ssize_t n;

	fd = open(argv[1], O_RDONLY);	/* 只读也能收全部事件 */
	if (fd < 0)
		return perror("open"), 1;

	if (ioctl(fd, EVIOCGVERSION, &ver) < 0)
		return perror("EVIOCGVERSION"), 1;	/* 不是 evdev 节点 */
	ioctl(fd, EVIOCGNAME(sizeof(name)), name);
	memset(keybit, 0, sizeof(keybit));
	ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keybit)), keybit);

	/* 时间戳换成单调钟。不设的话是 CLOCK_REALTIME，会被 NTP 拽着走 */
	if (ioctl(fd, EVIOCSCLOCKID, &clk) < 0)
		perror("EVIOCSCLOCKID");	/* 不致命，继续 */

	printf("%s ver=%d.%d.%d\n", name,
	       ver >> 16, (ver >> 8) & 0xff, ver & 0xff);

	for (;;) {
		n = read(fd, ev, sizeof(ev));
		if (n < 0) {
			if (errno == EINTR)
				continue;	/* 阻塞读会被信号打断 */
			if (errno == ENODEV)
				break;		/* 设备拔了，或被 REVOKE */
			return perror("read"), 1;
		}
		for (size_t i = 0; i < n / sizeof(ev[0]); i++) {
			if (ev[i].type == EV_SYN && ev[i].code == SYN_DROPPED) {
				/* 必须重新同步状态，见 §9.5 */
				continue;
			}
			printf("%ld.%06ld type=%u code=%u value=%d\n",
			       (long)ev[i].input_event_sec,
			       (long)ev[i].input_event_usec,
			       ev[i].type, ev[i].code, ev[i].value);
		}
	}
	close(fd);
	return 0;
}
```

### §9.3 `read()` 的五条硬规则

全部来自 `evdev_read()`（`drivers/input/evdev.c:558-607`），每一条都有对应行号：

| 情形 | 行为 | 行号 |
| --- | --- | --- |
| `count` 非零但小于一个 `input_event` | `-EINVAL`，一个字节都不读 | `:567-568` |
| `count == 0` | 只做错误检查，不做 IO，正常返回 0 | `:579-583` |
| 设备已消失或本 fd 被 revoke | `-ENODEV`（**优先于其他所有判断**，每轮循环都先查） | `:571-572` |
| `O_NONBLOCK` 且队列空 | `-EAGAIN` | `:574-576` |
| 阻塞模式且队列空 | `wait_event_interruptible()`，被信号打断返回 `-EINTR` 等 | `:597-603` |

★ **返回值一定是 `sizeof(struct input_event)` 的整数倍，但不保证是完整的一「帧」。** 循环条件是 `read + input_event_size() <= count`（`:585`），装不下就停。所以按 `SYN_REPORT` 划分帧的逻辑必须由你自己跨多次 `read()` 维护。

★ **反过来，你永远读不到「半个帧」的最新数据。** `evdev_fetch_next_event()`（`:540-556`）的判据是 `client->packet_head != client->tail`，而 `packet_head` 只在 `SYN_REPORT` 到达时才前移（`__pass_event()` 的 `:238-239`）。也就是说：**内核已经收到但还没收到 `SYN_REPORT` 的事件对 `read()` 不可见**。这是 input 子系统给用户态的原子性保证——一帧要么全看见，要么一个都看不见。

★ **`poll()` 的 `EPOLLOUT` 恒为真，不代表能写。** `evdev_poll()` 的 `:618-619`：只要设备还在且没被 revoke 就无条件置 `EPOLLOUT | EPOLLWRNORM`；设备没了则是 `EPOLLHUP | EPOLLERR`（`:620-621`）。`EPOLLIN` 才是真的按 `packet_head != tail` 判的（`:623-624`）。

### §9.4 ioctl 全表

`include/uapi/linux/input.h:132-243`，按用途分组。可变长度的那几个（`EVIOCG*(len)`）传的是缓冲区字节数，返回实际拷贝的字节数。

| 组 | ioctl | 说明 |
| --- | --- | --- |
| 身份 | `EVIOCGVERSION` (`:132`) | 读协议版本，也是「这是不是 evdev 节点」的探测手段 |
| | `EVIOCGID` (`:133`) | `struct input_id`：bustype/vendor/product/version |
| | `EVIOCGNAME(len)` (`:142`) / `EVIOCGPHYS(len)` (`:143`) / `EVIOCGUNIQ(len)` (`:144`) | 名字 / 物理位置 / 唯一标识 |
| 能力 | `EVIOCGBIT(ev, len)` (`:178`) | **`ev` 传 0 时读的是 `evbit` 本身**（`drivers/input/evdev.c:787`），传 `EV_KEY` 等则读对应分类位图；只支持八种分类，别的一律 `-EINVAL`（`:796`） |
| | `EVIOCGPROP(len)` (`:145`) | `INPUT_PROP_*` 属性位（`POINTER` / `DIRECT` / `BUTTONPAD` / `SEMI_MT` …） |
| | `EVIOCGABS(abs)` (`:179`) / `EVIOCSABS(abs)` (`:180`) | 单根绝对轴的 `struct input_absinfo`，可读可写 |
| 当前状态 | `EVIOCGKEY(len)` (`:173`) / `EVIOCGLED` (`:174`) / `EVIOCGSND` (`:175`) / `EVIOCGSW` (`:176`) | 四类「有状态」事件的当前值快照 |
| | `EVIOCGMTSLOTS(len)` (`:171`) | 多点触摸槽位快照，第一个 `__s32` 传入要查的 `ABS_MT_*` 码 |
| 键码映射 | `EVIOCGKEYCODE` / `EVIOCSKEYCODE` (`:137`/`:139`) | 旧接口，`unsigned int[2]`，扫描码只能是整数 |
| | `EVIOCGKEYCODE_V2` / `EVIOCSKEYCODE_V2` (`:138`/`:140`) | `struct input_keymap_entry`，扫描码可以是字节串 |
| 自动重复 | `EVIOCGREP` / `EVIOCSREP` (`:134`/`:135`) | `unsigned int[2]`，delay 和 period，单位毫秒 |
| 力反馈 | `EVIOCSFF` (`:182`) / `EVIOCRMFF` (`:183`) / `EVIOCGEFFECTS` (`:184`) | 见 §5 |
| 独占与撤销 | `EVIOCGRAB` (`:186`) | 见 §9.6 |
| | `EVIOCREVOKE` (`:187`) | 见 §9.6 |
| 过滤 | `EVIOCGMASK` (`:218`) / `EVIOCSMASK` (`:241`) | `struct input_mask`，按类型/按码过滤 |
| 时钟 | `EVIOCSCLOCKID` (`:243`) | 见 §9.5 |

★ **`EVIOCSMASK` 过滤不掉 `EV_SYN`。** `__evdev_is_filtered()` 的 `:83-84` 第一句就是 `if (type == EV_SYN || type >= EV_CNT) return false;`。所以帧边界和 `SYN_DROPPED` 永远送得到你——这是设计,不是漏网。同理未知的 code（`code >= cnt`）也一律放行（`:91-94`），新增事件码不会被老掩码悄悄吃掉。

### §9.5 时钟与 `SYN_DROPPED`

★ **`EVIOCSCLOCKID` 只接受三个值。** `evdev_set_clk_type()` 的 `:179-192`：`CLOCK_REALTIME`（默认）、`CLOCK_MONOTONIC`、`CLOCK_BOOTTIME`，其余一律 `-EINVAL`。**注意默认是 `CLOCK_REALTIME`**——NTP 校时或用户改系统时间会让事件时间戳跳变甚至倒退。要算按键间隔就必须先切到 `CLOCK_MONOTONIC`。

★ **切换时钟会丢掉队列里已有的事件。** `:194` 起的注释（`:198`）写明 `Flush pending events and queue SYN_DROPPED event`——已经排队的事件带的是旧时钟的时间戳，混在一起没有意义，所以内核直接丢，并补一个 `SYN_DROPPED` 告诉你。**所以 `EVIOCSCLOCKID` 应该在 open 之后立刻做，不要在事件流中间切。**

★ **`SYN_DROPPED` 意味着「你的状态机现在是错的」，必须重新同步。** 两个产生点：

- **队列满**（`__pass_event()` 的 `:220-235`）：写入后发现追上了 tail，就把 tail 退两格，在那里放一个 `SYN_DROPPED`，`packet_head` 也跟着退。注释 `:222-223` 说得很直白——`This effectively "drops" all unconsumed events, leaving EV_SYN/SYN_DROPPED plus the newest event in the queue`。**未消费的事件全没了，只剩这个标记和最新一个事件。**
- **`EVIOCSCLOCKID` / `EVIOCSMASK` 主动刷队列**（`evdev_queue_syn_dropped()`，`:165-172`；无空间时 `:158-161` 同样退 tail 保标记）。

正确的处理方式是：丢弃当前正在拼装的帧，用 `EVIOCGKEY` / `EVIOCGABS` / `EVIOCGMTSLOTS` / `EVIOCGLED` / `EVIOCGSW` 重新读一遍设备的**当前状态**，然后从下一个 `SYN_REPORT` 之后继续。

★ **队列大小是算出来的，不是固定的。** `evdev_compute_buffer_size()`（`:461-468`）= `roundup_pow_of_two(max(hint_events_per_packet * 8, 64))`，两个常数是 `EVDEV_BUF_PACKETS` = 8（`:13`）和 `EVDEV_MIN_BUFFER_SIZE` = 64U（`:12`）。`hint_events_per_packet` 由驱动通过 `input_set_events_per_packet()` 给，或核心自己估（见 `01-api.md` §1.2）。**多点触摸设备的队列会比键盘大得多**——所以「多久不读会溢出」这个问题没有统一答案,得看具体设备。

### §9.6 独占（GRAB）与撤销（REVOKE）

★ **`EVIOCGRAB` 的失败码有两个，含义不同。**

| 调用 | 条件 | 返回 | 行号 |
| --- | --- | --- | --- |
| `ioctl(fd, EVIOCGRAB, 1)` | 已经有别的 fd 抢占着 | `-EBUSY` | `:345-346` |
| `ioctl(fd, EVIOCGRAB, 1)` | 设备被内核其他 handle 打开 | `input_grab_device()` 的返回值 | `:348-350` |
| `ioctl(fd, EVIOCGRAB, 0)` | 抢占者不是自己 | `-EINVAL` | `:362-363` |

抢占成功后，事件只送给这个 fd，其他 evdev 客户端和内核 handler（keyboard/sysrq 等）都收不到——这是 X/Wayland 抓独占输入、以及 `evtest --grab` 的机制。释放时 `synchronize_rcu()`（`:366`）保证在返回前没有任何在途事件还指向你。抢占的核心侧语义见 `03-internals.md` §A9。

★ **`EVIOCREVOKE` 是单向门，没有反悔操作。** `evdev_revoke()`（`:943-951`）做四件事：置 `client->revoked = true`、替你 ungrab、`input_flush_device()` 冲掉本 fd 的力反馈效果、唤醒 poll 并报 `EPOLLHUP | EPOLLERR`。之后这个 fd 上的 `read()`、`write()`、**以及所有 ioctl（含 `EVIOCREVOKE` 自己）** 全部返回 `-ENODEV`：三处检查分别在 `:571`、`:517` 和 `:1267-1269`——最后这处在 `evdev_do_ioctl()` 分派**之前**，所以 revoke 之后连再调一次 `EVIOCREVOKE` 都会失败（幂等性靠"反正也没用"实现，不是靠返回 0）。它的用途是「把 fd 传给不受信任的进程，然后随时能吊销」——`fd` 本身还在，只是彻底哑了。

### §9.7 `write()`：从 evdev 反向注入

★ **`evdev_write()` 不只能写力反馈。** `:530-531` 调的是 `input_inject_event(&evdev->handle, event.type, event.code, event.value)`——**任何**事件类型都能往回送。真正的限制在核心侧：`input_inject_event()` 会走完整的 `input_event()` 校验，设备没声明的能力位会被静默丢弃（见 `03-internals.md` §A8）。常见用途是 `EV_LED`（打开 CapsLock 灯）、`EV_SND`、`EV_FF` 的播放/停止（`EV_FF` + effect id + value 1/0）。

★ **`write()` 的返回值是已消费的字节数，可能小于 `count`。** 循环条件 `retval + input_event_size() <= count`（`:522`），尾巴上不足一个事件的字节被忽略。`count` 非零但小于一个事件是 `-EINVAL`（`:510-511`）。中途 `-EFAULT` 时返回的是 `-EFAULT` 而不是已写字节数（`:524-527` 直接覆盖 `retval` 再 `goto out`）——**部分成功的信息在这条路径上丢了**。

★ **整个 `write()` 在 `evdev->mutex` 下执行，且每个事件之间 `cond_resched()`（`:532`）。** 所以它可以睡眠，一次写很多事件不会独占 CPU，但会和同设备的 ioctl 串行化。

---

## §10 角色十：用户态造设备（uinput）

uinput 是本篇唯一一个「从用户态往里造 input 设备」的角色。它是个 misc 设备 `/dev/uinput`（次设备号 `UINPUT_MINOR`，`drivers/input/misc/uinput.c:1107`），打开它、声明能力、`UI_DEV_CREATE`，内核就多出一个 `/dev/input/eventN`——对系统里其他所有 evdev 客户端来说，它和真硬件毫无区别。实现是 `drivers/input/misc/uinput.c`（1117 行），uapi 是 `include/uapi/linux/uinput.h`（232 行）。子框架侧的介绍见 `04-extensions.md` §6，本节只讲怎么用。

### §10.1 三态状态机：顺序错了就是 `-EINVAL`

★ **所有「设备创建后不能改」的限制都由一个三值枚举兜着：**

```c
/* drivers/input/misc/uinput.c:37 */
enum uinput_state { UIST_NEW_DEVICE, UIST_SETUP_COMPLETE, UIST_CREATED };
```

| 状态 | 怎么进来 | 这个状态下能做什么 |
| --- | --- | --- |
| `UIST_NEW_DEVICE` | `open()`（`:389`）；`UI_DEV_DESTROY` 之后（`:291`） | `UI_SET_*BIT` 声明能力、`UI_ABS_SETUP`、`UI_SET_PHYS`、`UI_DEV_SETUP` |
| `UIST_SETUP_COMPLETE` | `UI_DEV_SETUP` 成功（`:484`），或 legacy 的 `write()` 写完 `struct uinput_user_dev`（`:578`） | 上面那些还能继续做，另外 `UI_DEV_CREATE` 现在会成功 |
| `UIST_CREATED` | `UI_DEV_CREATE` 成功（`:368`） | `write()` 变成注入事件、`read()` 变成收内核下行请求、`UI_GET_SYSNAME` 可用 |

三条由状态直接决定的错误码：

- **`UI_DEV_CREATE` 在 `UIST_NEW_DEVICE` 下返回 `-EINVAL`**，并且往 dmesg 打 `write device info first`（`:313-316`，`KERN_DEBUG` 级别，默认看不见——`07-debug.md` 会讲怎么让它露出来）。**必须先 `UI_DEV_SETUP`。**
- **`UI_DEV_SETUP` / `UI_ABS_SETUP` / `UI_SET_PHYS` 在 `UIST_CREATED` 下返回 `-EINVAL`**（`:466-467`、`:498-499`、`:939-941`）。想改就得先 `UI_DEV_DESTROY`。
- **`UI_GET_SYSNAME` 在非 `UIST_CREATED` 下返回 `-ENOENT`**（`:1035-1037`）——注意是 `ENOENT` 不是 `EINVAL`，因为这时候确实还没有 sysfs 名字可给。

★ **`UI_SET_*BIT` 这一组只有一道状态门。** 它们全都展开成 `uinput_set_bit()` 宏（`:818-827`），宏体里两个判断：`UIST_CREATED` 下返回 `-EINVAL`（`:821-822`），码值超过该类上限也返回 `-EINVAL`（`:823-824`），其余情况 `set_bit()`。**它不区分 `UIST_NEW_DEVICE` 和 `UIST_SETUP_COMPLETE`——能力位在 `UI_DEV_SETUP` 之前或之后声明都行，只要在 `UI_DEV_CREATE` 之前。**

### §10.2 骨架

```c
/* 未编译骨架：造一个三键鼠标 + 相对轴 */
#include <linux/uinput.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <sys/ioctl.h>

static void emit(int fd, int type, int code, int val)
{
	struct input_event ev = { .type = type, .code = code, .value = val };
	/* 时间戳留 0：内核 input_event() 会自己盖时间 */
	write(fd, &ev, sizeof(ev));
}

int main(void)
{
	struct uinput_setup usetup;
	int fd;

	fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
	if (fd < 0)
		return perror("open /dev/uinput"), 1;

	/* 1. 声明能力（必须在 UI_DEV_CREATE 之前） */
	ioctl(fd, UI_SET_EVBIT, EV_KEY);
	ioctl(fd, UI_SET_KEYBIT, BTN_LEFT);
	ioctl(fd, UI_SET_KEYBIT, BTN_RIGHT);
	ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE);
	ioctl(fd, UI_SET_EVBIT, EV_REL);
	ioctl(fd, UI_SET_RELBIT, REL_X);
	ioctl(fd, UI_SET_RELBIT, REL_Y);

	/* 2. 身份。name 不能是空串，否则 -EINVAL */
	memset(&usetup, 0, sizeof(usetup));
	usetup.id.bustype = BUS_VIRTUAL;
	usetup.id.vendor  = 0x1234;
	usetup.id.product = 0x5678;
	strcpy(usetup.name, "my virtual mouse");
	if (ioctl(fd, UI_DEV_SETUP, &usetup) < 0)
		return perror("UI_DEV_SETUP"), 1;

	/* 3. 真正建设备 */
	if (ioctl(fd, UI_DEV_CREATE) < 0)
		return perror("UI_DEV_CREATE"), 1;

	/* 4. 现在才有 sysfs 名字 */
	{
		char sys[64] = "?";
		ioctl(fd, UI_GET_SYSNAME(sizeof(sys)), sys);
		printf("created: %s\n", sys);	/* 如 input42 */
	}

	sleep(1);	/* 等 udev 建好 /dev/input/eventN 并让消费者打开 */

	emit(fd, EV_REL, REL_X, 5);
	emit(fd, EV_REL, REL_Y, 5);
	emit(fd, EV_SYN, SYN_REPORT, 0);	/* 不发这一句，谁都收不到 */

	sleep(1);
	ioctl(fd, UI_DEV_DESTROY);
	close(fd);
	return 0;
}
```

★ **`sleep(1)` 不是凑数。** `UI_DEV_CREATE` 返回时 `input_register_device()`（`:364`）已经完成，`/dev/input/eventN` 已经存在——但 udev 规则还没跑完、桌面环境还没 open 它。这段时间里发出去的事件**没有任何 client 在监听，直接丢弃**（`input_pass_values()` 只发给已 open 的 handle，见 `03-internals.md` §A8.5）。真实程序应该监听 udev 事件而不是 `sleep`。

★ **`close(fd)` 就等于 `UI_DEV_DESTROY`。** `uinput_release()`（`:720`）会销毁设备，所以显式调 `UI_DEV_DESTROY` 只是为了让时机可控（比如想 destroy 之后重新 setup 一个不同的设备）。

★ **事件时间戳可以留 0。** `uinput_inject_events()`（`:586-611`）调的是 `input_event(udev->dev, ev.type, ev.code, ev.value)`（`:605`），**只取三个字段，时间字段被完全忽略**。真正的时间戳是在投递给 evdev 客户端时懒生成的：`evdev_events()` 调 `input_get_timestamp(handle->dev)`（`drivers/input/evdev.c:296`），而 `input_get_timestamp()`（`drivers/input/input.c:2157-2166`）发现 `dev->timestamp[INPUT_CLK_MONO]` 还是 0 就现取一个 `ktime_get()`。想给精确时间的驱动要主动调 `input_set_timestamp()`（`drivers/input/input.c:2142-2149`，同时算好三个时钟的值）——uinput 没有把这个能力开放给用户态。这和 evdev 的 `write()` 一样（§9.7）。

### §10.3 ioctl 全表

`include/uapi/linux/uinput.h`，`UINPUT_IOCTL_BASE` 是 `'U'`（`:63`）。

| 组 | ioctl | 说明 |
| --- | --- | --- |
| 生命周期 | `UI_DEV_CREATE` (`:64`) / `UI_DEV_DESTROY` (`:65`) | 无参数 `_IO` |
| 身份 | `UI_DEV_SETUP` (`:100`) | `struct uinput_setup`：`id` / `name` / `ff_effects_max` |
| 绝对轴 | `UI_ABS_SETUP` (`:135`) | `struct uinput_abs_setup`：一次一根轴，`code` + 完整 `input_absinfo`（含 `resolution`） |
| 能力位 | `UI_SET_EVBIT` (`:137`) / `KEYBIT` (`:138`) / `RELBIT` (`:139`) / `ABSBIT` (`:140`) / `MSCBIT` (`:141`) / `LEDBIT` (`:142`) / `SNDBIT` (`:143`) / `FFBIT` (`:144`) / `SWBIT` (`:146`) / `PROPBIT` (`:147`) | 参数是 `int`，一次一个码 |
| 物理位置 | `UI_SET_PHYS` (`:145`) | 参数是字符串指针，内核 `strndup_user(p, 1024)`（`drivers/input/misc/uinput.c:944`） |
| 力反馈应答 | `UI_BEGIN_FF_UPLOAD` (`:149`) / `UI_END_FF_UPLOAD` (`:150`) / `UI_BEGIN_FF_ERASE` (`:151`) / `UI_END_FF_ERASE` (`:152`) | 见 §10.5 |
| 查询 | `UI_GET_SYSNAME(len)` (`:161`) | 返回 sysfs 名（如 `input42`），只在 `UIST_CREATED` 下可用 |
| | `UI_GET_VERSION` (`:170`) | 返回 `UINPUT_VERSION` = 5（`:46`，`drivers/input/misc/uinput.c:879-880`） |

★ **`UI_ABS_SETUP` 比 `UI_SET_ABSBIT` 强，而且两者的关系不是"新旧接口"。** `UI_SET_ABSBIT` 只置位，范围要靠 legacy 的 `write(struct uinput_user_dev)` 一次性给全部轴；`UI_ABS_SETUP`（`uinput_abs_setup()`，`:488-520`）一次给一根轴的完整 `absinfo` 并顺手置位（`:517-518`）。**用了 `UI_ABS_SETUP` 就不需要再 `UI_SET_ABSBIT`。**

`UI_ABS_SETUP` 的四道校验，返回码各不相同：

| 条件 | 返回 | 行号 |
| --- | --- | --- |
| 传入结构体比内核的大 | `-E2BIG` | `:495-496` |
| 已经 `UI_DEV_CREATE` 过 | `-EINVAL` | `:498-499` |
| `code > ABS_MAX` | `-ERANGE` | `:504-505` |
| `absinfo` 本身不合法 | `-EINVAL`（`uinput_validate_absinfo()`） | `:509-511` |

★ **`uinput_validate_absinfo()`（`:397-434`）有一条容易撞上的硬上限：`ABS_MT_SLOT` 的 `maximum` 不能超过 99。** `:426-430` 直接拒掉，dmesg 打 `unreasonably large number of slots requested: %d`。注释 `:419-425` 给了理由——把 `struct input_mt` 限制在两页以内。另外 `max < min` 会被拒（`:405-410`，但 `min == max == 0` 是允许的），`flat > max - min` 也会被拒（`:412-417`）。

### §10.4 `UI_DEV_CREATE` 时内核替你做的事

`uinput_create_device()`（`drivers/input/misc/uinput.c:308-375`）里有几件不做声明但会影响行为的动作：

| 条件 | 内核动作 | 行号 |
| --- | --- | --- |
| 声明了 `EV_ABS` | `input_alloc_absinfo()`，失败则 `-EINVAL` | `:318-323` |
| 声明了 `ABS_MT_SLOT` | 按 `max + 1` 自动调 `input_mt_init_slots(dev, nslot, 0)` | `:325-329` |
| 声明了 `ABS_MT_POSITION_X` 但没有 `ABS_MT_SLOT` | `input_set_events_per_packet(dev, 60)` | `:330-332` |
| 声明了 `EV_FF` 但 `ff_effects_max == 0` | **`-EINVAL`**，dmesg 打 `ff_effects_max should be non-zero when FF_BIT is set` | `:335-340` |
| `ff_effects_max != 0` | `input_ff_create()` + 挂五个回调 + 覆盖 `dev->flush` | `:342-358` |
| 总是 | `dev->event = uinput_dev_event`，然后 `input_register_device()` | `:360-364` |

★ **多点触摸设备不需要自己调槽位初始化，但 A 类协议要自己算 `events_per_packet`。** `:325-332` 的分支是互斥的：有 `ABS_MT_SLOT`（B 类协议）走自动 `input_mt_init_slots()`；只有 `ABS_MT_POSITION_X`（A 类协议）则只给一个 60 的估值。两类协议的区别见 §3。

★ **失败路径会把设备整个拆掉。** `fail1`（`:373`）是 `uinput_destroy_device()`，`fail2`（`:372`）先 `input_ff_destroy()` 再落到 `fail1`。所以 `UI_DEV_CREATE` 失败之后状态回到 `UIST_NEW_DEVICE`（`:291`），能力位也没了——**要重试必须从声明能力开始重来一遍**。

### §10.5 `read()` 侧：接收内核下行的请求

这是 uinput 最容易被忽略的一半。`/dev/uinput` 的 `read()` 不是读你自己发的事件，而是读**内核要你这个「虚拟设备」去做的事**。

`dev->event = uinput_dev_event`（`:360`）是入口：任何 client 往这个虚拟设备 `write()` 一个输出事件（`EV_LED` / `EV_SND` / `EV_FF`），核心就会调它，把事件塞进 `udev->buff[]` 并唤醒你（`:79-89`）。

| 项 | 值/行为 | 行号 |
| --- | --- | --- |
| 环形缓冲大小 | `UINPUT_BUFFER_SIZE` = **16** 个事件 | `:34` |
| 时间戳时钟 | 固定 `ktime_get_ts64()`（单调钟），**`EVIOCSCLOCKID` 管不到这一侧** | `:77` |
| `count` 小于一个事件 | `-EINVAL` | `:677-678` |
| 未 `UI_DEV_CREATE` | `-ENODEV` | `:685-686` |
| `O_NONBLOCK` 且队列空 | `-EAGAIN` | `:687-689` |
| 阻塞模式 | `wait_event_interruptible()`，设备被 destroy 也会醒 | `:698-701` |
| `poll()` | `EPOLLOUT` 恒置（注释 `:710` 写明 `uinput is always writable`），`EPOLLIN` 按 `head != tail` | `:707-717` |

★ **这个 16 个事件的环形缓冲没有溢出保护。** `uinput_dev_event()` 的 `:87` 是裸的 `udev->head = (udev->head + 1) % UINPUT_BUFFER_SIZE`——**没有 `head == tail` 的检查，也不会补 `SYN_DROPPED`**。你不读，旧事件就被静默覆盖，而且 `head` 追过 `tail` 之后 `tail` 还在原地，队列会被算成"几乎空"。和 evdev 那套 `SYN_DROPPED` 机制（§9.5）对比，这是同一棵树上两种截然不同的处理方式，已登记在 `99-evidence.md` 的设计债清单里。**所以只要声明了 `EV_LED` / `EV_FF` 这类输出能力，就必须有人在 `poll()` 这个 fd。**

★ **力反馈请求是同步的，你有 30 秒。** `uinput_request_submit()`（`:172-195`）在 `:185` 上 `wait_for_completion_timeout(&request->done, 30 * HZ)`，超时返回 `-ETIMEDOUT` 给内核侧的调用者（也就是发 `EVIOCSFF` 的那个进程）。流程是：

1. 你 `read()` 到一个 `EV_UINPUT` 事件，`code` 是 `UI_FF_UPLOAD`（`include/uapi/linux/uinput.h:220`）或 `UI_FF_ERASE`（`:221`），`value` 是请求 id；
2. `ioctl(UI_BEGIN_FF_UPLOAD)` 带上这个 id，取回 `struct ff_effect`；
3. 你处理（真机上就是往硬件写，虚拟设备上随你）；
4. 填好 `retval` 后 `ioctl(UI_END_FF_UPLOAD)` 应答。

同时在途的请求上限是 `UINPUT_NUM_REQUESTS` = 16（`drivers/input/misc/uinput.c:35`）。设备销毁时 `uinput_flush_requests()`（`:201-217`）把所有未完成请求的 `retval` 置成 `-ENODEV`（`:211`），免得内核侧永远等下去。

★ **不打算实现力反馈就不要声明 `EV_FF`。** 声明了却不应答，每个 `EVIOCSFF` 都会让调用进程卡 30 秒。

### §10.6 legacy 路径：`write()` 在 `UIST_CREATED` 之前是另一个意思

★ **`uinput_write()` 是一个双身份函数**（`:613-651`）：`:626-628` 一个三元表达式分岔——`UIST_CREATED` 状态下走 `uinput_inject_events()`（注入事件），否则走 `uinput_setup_device_legacy()`（`:523-585`，解析 `struct uinput_user_dev`）。

所以**在 `UI_DEV_CREATE` 之前误 `write()` 事件，会得到一个和"事件"毫无关系的 `-EINVAL`**：legacy 路径第一句就是 `if (count != sizeof(struct uinput_user_dev)) return -EINVAL;`（`:531-532`），长度必须**精确等于**那个结构体的大小。这个错误码不会告诉你"你还没 CREATE"，排查时容易往错的方向找。新代码一律用 `UI_DEV_SETUP` + `UI_ABS_SETUP`，`struct uinput_user_dev`（`include/uapi/linux/uinput.h:223-231`）只为兼容老程序保留。

★ **`count == 0` 时 `write()` 直接返回 0**（`:619-620`），不加锁不判状态——和 `read()`/evdev 的 `count == 0` 语义（做错误检查）不一样。

★ **注入路径遇到 `-EFAULT` 会丢掉已成功的计数。** `uinput_inject_events()` 的注释 `:596-601` 明确说这是故意的：`even if some events were fetched successfully we are still going to return EFAULT instead of partial count to let userspace know that it got it's buffers all wrong`。和 evdev 的同一处行为（§9.7）一致，但那边没有注释说明意图。

## §11 近邻符号区分

input 子系统里名字差一个词、语义差一整层的符号特别多。本节把它们成组摆开，每组给出「差别在哪一行代码上」和「错用会怎样」。

判据仍是 §0 那两条：`EXPORT_SYMBOL*` 且**公开** `include/` 头文件里有声明。本节里凡是标了「用不上」的符号，理由见 §11.11。

### §11.1 `input_allocate_device()` vs `devm_input_allocate_device()`

| | `input_allocate_device()` | `devm_input_allocate_device(dev)` |
|---|---|---|
| 声明 | `include/linux/input.h:359`（带 `__must_check`） | `include/linux/input.h:360` |
| 实现 | `drivers/input/input.c:2010` | `drivers/input/input.c:2075-2098` |
| `dev.parent` | 你自己填 | 核心替你填成 `dev`（`:2091`） |
| `devres_managed` | `false` | `true`（`:2092`） |
| 失败/卸载时怎么收 | 你自己配 `input_free_device()` 或 `input_unregister_device()` | 不用管，绑定失败或解绑时自动 |

`devm_` 版内部就是 `input_allocate_device()`（`:2085`）加一层 devres 登记（`:2080` 分配、`:2095` 加入）。真正影响写法的是两条：

★ **`devm_` 版把 `dev.parent` 定死了，别去覆盖它。** kerneldoc `drivers/input/input.c:2072-2073` 逐字写着 `NOTE: the owner device is set up as parent of input device and users should not override it`。

★ **`devres_managed` 会改变 `input_register_device()` 的行为。** `:2408-2415` 在 `devres_managed` 为真时额外分配一个 `devm_input_device_unregister` 的 devres 节点（`:2503-2507` 才真正 `devres_add`）；`input_unregister_device()`（`:2501-2527`）也因此分成两个分支，注释写明 `We do not do input_put_device() here because it will be done when 2nd devres fires up`。

**错用会怎样：** 用了 `devm_input_allocate_device()` 又在 `remove()` 里手动 `input_unregister_device()`，不会立刻崩——`devres_destroy` 会把那个节点撤掉——但你多写了一段没用的代码，并且如果顺序写反（先 `input_free_device()` 后注销）就会 double free。反过来，用了非 devm 版却忘了在错误路径上 `input_free_device()`，是一次 `struct input_dev` 泄漏，加上一个已经进了 kobject 体系的 `dev.parent` 引用。

### §11.2 三个 `register`：device / handle / handler

| API | 注册的对象 | 实现 | 谁调用 |
|---|---|---|---|
| `input_register_device()` | 一个输入设备（`struct input_dev`） | `drivers/input/input.c:2394` | 设备驱动（§1–§5） |
| `input_register_handler()` | 一类消费者（`struct input_handler`） | `drivers/input/input.c:2528` | handler 模块（§6） |
| `input_register_handle()` | 一条「某 handler ↔ 某 device」的连接（`struct input_handle`） | `drivers/input/input.c:2619` | handler 的 `connect()` 回调里（§6） |

三者的时序关系在 `03-internals.md` §A5/§A6/§A13 里有完整时间线：handler 先注册，设备注册时核心遍历 handler 做匹配，匹配上了才调 `connect()`，`connect()` 里才轮到 `input_register_handle()`。

★ **`handle` 和 `handler` 差一个字母，但一个是「连接」一个是「消费者类型」。** 判断方法看参数类型：`input_register_handle()` 收 `struct input_handle *`，`input_register_handler()` 收 `struct input_handler *`。

**错用会怎样：** 在 `connect()` 里写成 `input_register_handler()` 是编译期类型错误（两个结构体不兼容），这组不太可能错到运行期。真正的坑是**漏掉** `input_register_handle()`——事件永远不会流向你的 handler，因为分发是沿着 `dev->h_list` 走的（`03-internals.md` §A8.5），而那条链表只由 `input_register_handle()` 挂上去。

### §11.3 `input_event()` / `input_report_*()` / `input_inject_event()` / `input_handle_event()`

| 符号 | 第一个参数 | 位置 | 你能不能用 |
|---|---|---|---|
| `input_event()` | `struct input_dev *` | 声明 `include/linux/input.h:420`，实现 `drivers/input/input.c:423` | 能 |
| `input_report_key()` 等 7 个 | `struct input_dev *` | `include/linux/input.h:425-455`，全是 `static inline` | 能 |
| `input_inject_event()` | `struct input_handle *` | 声明 `include/linux/input.h:421`，实现 `drivers/input/input.c:448` | 能，但你得先有个 handle |
| `input_handle_event()` | `struct input_dev *` | `drivers/input/input.c:390` | **不能**（见 §11.11） |

`input_report_*()` 全是 `input_event()` 的一行包装，差别只在替你填了 `type` 和 `code`：

| inline | 展开成 | 行号 |
|---|---|---|
| `input_report_key(dev, code, value)` | `input_event(dev, EV_KEY, code, !!value)` | `include/linux/input.h:425` |
| `input_report_rel(dev, code, value)` | `input_event(dev, EV_REL, code, value)` | `:430` |
| `input_report_abs(dev, code, value)` | `input_event(dev, EV_ABS, code, value)` | `:435` |
| `input_report_ff_status(dev, code, value)` | `input_event(dev, EV_FF_STATUS, code, value)` | `:440` |
| `input_report_switch(dev, code, value)` | `input_event(dev, EV_SW, code, !!value)` | `:445` |
| `input_sync(dev)` | `input_event(dev, EV_SYN, SYN_REPORT, 0)` | `:450` |
| `input_mt_sync(dev)` | `input_event(dev, EV_SYN, SYN_MT_REPORT, 0)` | `:455` |

★ **只有 `input_report_key()` 和 `input_report_switch()` 会把 value 折成 0/1**（`!!value`）。`input_report_abs()` / `input_report_rel()` 原样传，因为坐标和位移本来就要保留数值。

★ **`input_inject_event()` 的第一个参数是 `handle` 不是 `dev`。** 它存在的意义是「以某个 handler 的身份注入」——注入的事件会带上 `handle` 的身份，从而能被 grab 检查（`03-internals.md` §A9）正确处理。设备驱动手里没有 handle，只能用 `input_event()`。

**错用会怎样：** 把 `input_report_key()` 用在 `EV_ABS` 轴上（比如 `input_report_key(dev, ABS_X, 300)`），代码编译通过、运行不崩，但事件被当成 `EV_KEY` 类型：一是 `ABS_X`（值 0x00）和 `KEY_RESERVED`（值 0）撞码，二是 `!!value` 把 300 折成 1。表现是「事件出去了但坐标恒为 1」。这类错误 `input_event()` 侧的 `is_event_supported()`（`03-internals.md` §A8.1）不一定挡得住——如果设备恰好也声明了 `EV_KEY`。

### §11.4 `input_sync()` vs `input_mt_sync()` vs `input_mt_sync_frame()`

三个名字都带 sync，但一个结束整帧、一个分隔触点、一个**根本不发事件**。

| API | 发什么事件 | 用在哪 | 位置 |
|---|---|---|---|
| `input_sync()` | `EV_SYN` / `SYN_REPORT` / 0 | 每帧末尾，所有角色都要调 | `include/linux/input.h:450` |
| `input_mt_sync()` | `EV_SYN` / `SYN_MT_REPORT` / 0 | 仅 A 类多点协议，每个触点后 | `include/linux/input.h:455` |
| `input_mt_sync_frame()` | **不发任何事件** | 仅 B 类槽位协议，`input_sync()` 之前 | `drivers/input/input-mt.c:333-355` |

`input_mt_sync_frame()` 做的是三件与「发事件」无关的事：`INPUT_MT_DROP_UNUSED` 标志置位时把本帧没被 report 的槽位置为非活跃（`:341-347`，在 `spin_lock_irqsave` 里）、更新 `use_count`（`:349-350`）、跑指针模拟（`:352`），最后 `mt->frame++`（`:354`）。那个 `frame` 计数器就是「本帧有没有碰过这个槽位」的判据——`struct input_mt` 的 kerneldoc（`include/linux/input/mt.h:39`）写着 `increases every time input_mt_sync_frame() is called`，而 `struct input_mt_slot` 的 `frame`（`:22-27`）记的是 `last frame at which input_mt_report_slot_state() was called`。

★ **A 类和 B 类不能混。** A 类用 `input_mt_sync()` 分隔触点、不用槽位；B 类用 `input_mt_slot()` 选槽位、不发 `SYN_MT_REPORT`。§3 的骨架走的是 B 类。

**错用会怎样：**
- B 类里调了 `input_mt_sync()`：多发一个 `SYN_MT_REPORT`，用户态库（libinput / mtdev）会以为你在说 A 类协议，两套解析混在一起，表现是触点数量乱跳。
- B 类里漏了 `input_mt_sync_frame()`：`INPUT_MT_DROP_UNUSED` 失效，抬起的手指永远留在「活跃」状态（因为没人替你发 `TRACKING_ID = -1`），表现是幽灵触点；`use_count` 和指针模拟（`BTN_TOUCH` / 单点坐标）也一起停摆。
- 只调 `input_mt_sync_frame()` 不调 `input_sync()`：一个事件都不会到用户态——evdev 的 `packet_head` 只在 `SYN_REPORT` 上前进（§9.3）。

### §11.5 `input_set_capability()` vs `input_set_abs_params()` vs 裸 `__set_bit()`

| 做法 | 置 `dev->evbit` | 置具体的 code 位 | 分配 `absinfo` | 填 min/max/fuzz/flat |
|---|---|---|---|---|
| `input_set_capability(dev, type, code)` | 是（`drivers/input/input.c:2231`） | 是 | `EV_ABS` 时会（`:2196-2199`） | **不填**，留 0 |
| `input_set_abs_params(dev, axis, min, max, fuzz, flat)` | 是（`:499-500` 同时置 `EV_ABS` 和轴位） | 是 | 是 | 是 |
| `__set_bit(code, dev->keybit)` | **不置** | 是 | 不 | 不 |

★ **`input_set_capability()` 有上界检查，而且失败是静默的。** `:2179-2185` 拿 `input_max_code[type]` 做上界比较，超了就 `pr_err` + `dump_stack()` 然后**直接 return，不返回错误码**。所以传错 code 的后果是设备注册成功但那个能力没声明上——只有 dmesg 里那段栈回溯能提示你。

★ **`input_set_capability()` 对 `EV_PWR` 什么都不做**（`:2221-2223`），`default` 分支同样是 `pr_err` + `dump_stack` + 静默返回（`:2225-2228`）。

★ **裸 `__set_bit()` 必须自己补 `evbit`。** 很多驱动写 `__set_bit(KEY_POWER, dev->keybit)` 之后接一句 `__set_bit(EV_KEY, dev->evbit)`——漏了后面这句，`is_event_supported()` 在 `evbit` 那一关就把事件丢了。`input_set_capability()` 存在的全部意义就是替你把这两位一起置上。

★ **声明了 `EV_ABS` 却没有 absinfo 会导致注册直接失败。** `input_register_device()` 的 `:2402-2406` 检查这一条，打印 `Absolute device without dev->absinfo, refusing to register` 并返回 `-EINVAL`。用 `input_set_abs_params()` 或 `input_set_capability(dev, EV_ABS, …)` 都能满足它（后者靠 `input_alloc_absinfo`），但后者留下的是全 0 的 min/max。

**错用会怎样：** 只用 `input_set_capability(dev, EV_ABS, ABS_X)` 声明坐标轴，注册能过，但用户态 `EVIOCGABS` 读回来的 `maximum` 是 0。libinput 一类的库会认为这个轴的量程是 0，触摸位置全落在原点。绝对轴一律用 `input_set_abs_params()`。

### §11.6 `input_ff_create()` vs `input_ff_create_memless()`

| | `input_ff_create(dev, max_effects)` | `input_ff_create_memless(dev, data, play_effect)` |
|---|---|---|
| 声明 | `include/linux/input.h:568` | `include/linux/input.h:577` |
| 实现 | `drivers/input/ff-core.c:304` | `drivers/input/ff-memless.c:507-545` |
| 效果数 | 你指定 | 固定 `FF_MEMLESS_EFFECTS` = 16（`drivers/input/ff-memless.c:26`） |
| 五个回调 | 你自己填 | 核心替你填 `private` / `upload` / `playback` / `set_gain` / `destroy`（`:534-538`） |
| 效果类型模拟 | 无 | 用 `FF_RUMBLE` 模拟 `FF_PERIODIC` / `SINE` / `TRIANGLE` / `SQUARE`（`:541-545`） |

★ **`input_ff_create_memless()` 内部就调了 `input_ff_create()`**（`ff-memless.c:527`，参数是 `FF_MEMLESS_EFFECTS`）。所以两个都调 = 创建两次 ff 设备。第二次会覆盖 `dev->ff` 指针，第一次分配的内存和 effects 数组泄漏。

★ **`input_ff_create(dev, 0)` 是错误，不是「不要力反馈」。** `:310-313` 判 `!max_effects` → `dev_err("cannot allocate device without any effects\n")` + `-EINVAL`；上界是 `FF_MAX_EFFECTS`（`:315-318`）。不要力反馈就别调这个函数。

**错用会怎样：** 硬件只有一个震动马达却用了 `input_ff_create()`，你得自己实现波形合成、gain 缩放、效果调度——`ff-memless.c` 那 500 行就是替你干这件事的。反过来，硬件自带效果存储（能记住多个 effect slot）却用了 memless 版，你被锁在 16 个效果和 `FF_RUMBLE` 语义里，硬件能力用不上。§5 的骨架用的是 memless 版。

### §11.7 `input_mt_report_slot_state()` vs `input_mt_report_slot_inactive()`

后者就是前者的一个固定参数版本：`include/linux/input/mt.h:103-105` 里 `input_mt_report_slot_inactive(dev)` = `input_mt_report_slot_state(dev, 0, false)`。

★ **`input_mt_report_slot_state()` 的返回值是 bool，含义是「这个槽位现在活跃吗」，不是「成功了吗」。** 三个返回点：没有槽位机制（`dev->mt == NULL`）返回 false（`drivers/input/input-mt.c:145-146`）、`active` 为假时发 `TRACKING_ID = -1` 后返回 false（`:151-154`）、正常路径发完 `TRACKING_ID` 和 `TOOL_TYPE` 返回 true（`:160-163`）。所以 `if (!input_mt_report_slot_state(...)) return -Exxx;` 这种写法是错的——抬手指的正常情况也会走进去。

★ **它顺手记账。** `:149` 的 `slot->frame = mt->frame` 是 `input_mt_sync_frame()` 判断「本帧碰过没有」的唯一依据（§11.4）。这也是为什么「不活跃的槽位」也要显式 report 一次，或者依赖 `INPUT_MT_DROP_UNUSED` 替你收尾。

**错用会怎样：** 拿返回值当错误码判断，抬手指时会误退出上报循环，后面的槽位全部不更新；如果那次退出还跳过了 `input_mt_sync_frame()` + `input_sync()`，整帧数据都不会到用户态。

### §11.8 三种「释放」：`input_free_device()` / `input_unregister_device()` / `input_put_device()`

| API | 位置 | 什么时候用 |
|---|---|---|
| `input_free_device(dev)` | 声明 `include/linux/input.h:361`，实现 `drivers/input/input.c:2115-2126` | **只在还没注册、或注册失败之后** |
| `input_unregister_device(dev)` | 声明 `include/linux/input.h:385`，实现 `drivers/input/input.c:2501-2527` | 注册成功之后 |
| `input_put_device(dev)` | `include/linux/input.h:368`（`static inline`，包 `put_device()`） | 只在你显式 `input_get_device()` 过之后配对使用 |

★ **kerneldoc 把适用时机写死了。** `drivers/input/input.c:2105-2108` 逐字说 `input_free_device()` `should only be used if input_register_device() was not called yet or if it failed`。它内部就是 `WARN_ON(devres_destroy(...))`（`:2118-2122`）加一次 `input_put_device()`（`:2123`）——引用计数机制是同一套，区别只在「注册后设备已经进了 `input_dev_list` 和 sysfs，必须先走注销流程」。

★ **`input_unregister_device()` 内部会替你把引用放掉。** 它分 devres / 非 devres 两支，注释写明 `We do not do input_put_device() here because it will be done when 2nd devres fires up`——也就是说注销之后**不要**再补一次 `input_free_device()` 或 `input_put_device()`。

**错用会怎样：** 注册成功后调 `input_free_device()`，`devres_destroy()` 的 `WARN_ON` 会在 dmesg 里抛一次栈（devres 版），设备仍然挂在 `input_dev_list` 上、sysfs 节点还在，而 `struct input_dev` 的引用被放掉一次——后续任何一次 `open()` 都在访问已释放对象。反过来，`input_allocate_device()` 成功但 `input_register_device()` 之前的错误路径上直接 `return -Exxx`，是纯泄漏。

### §11.9 `input_setup_polling()` 和四个 interval 函数的先后顺序

| API | 位置 | 备注 |
|---|---|---|
| `input_setup_polling(dev, poll_fn)` | `include/linux/input.h:389`，实现 `drivers/input/input-poller.c:68-92` | 必须先调；`kzalloc` 失败返回 `-ENOMEM` |
| `input_set_poll_interval(dev, ms)` | `:390` / `input-poller.c:105-110` | 单位毫秒 |
| `input_set_min_poll_interval(dev, ms)` | `:391` / `:112-117` | 只约束 sysfs 写入 |
| `input_set_max_poll_interval(dev, ms)` | `:392` / `:119-124` | 只约束 sysfs 写入 |
| `input_get_poll_interval(dev)` | `:394` / `:126-133` | 没 setup 过返回 `-EINVAL`（`:128-129`） |

★ **四个 setter 在没 `input_setup_polling()` 的情况下静默不干活。** 它们都先过 `input_dev_ensure_poller()`（`input-poller.c:94-103`），`dev->poller` 为空时打印 `poller structure has not been set up` 然后**什么都不做，没有返回值可查**（三个 setter 都是 `void`）。

★ **默认值是在注册时才补的，不是 setup 时。** `input_dev_poller_finalize()`（`:46-52`）在 `poll_interval` 为 0 时填 500 毫秒（`:48-49`），在 `poll_interval_max` 为 0 时填成等于 `poll_interval`（`:50-51`）。这个函数由核心在注册路径上调用，不是你调的（它属于 §11.11 那类私有符号）。

★ **`poll_interval` 为 0 等于关闭轮询。** `input_dev_poller_start()`（`:54-61`）只在 `poll_interval > 0` 时才排第一次 work。用户态可以通过 sysfs 的 `poll` 属性写 0 来停掉轮询，写入值会被 min/max 夹住（`:159-162`）。

**错用会怎样：** 先调 `input_set_poll_interval()` 再调 `input_setup_polling()`，你设的间隔被后者的 `kzalloc` 清零（`:73`）后由 finalize 补成 500 毫秒——一个「间隔莫名变成 500ms」的 bug，只有 dmesg 里那行 `poller structure has not been set up` 能提示你顺序反了。只设 min/max 不设 interval 也一样：max 会被 finalize 覆盖成 500（因为此时 `poll_interval` 已经是 500）。

### §11.10 `touchscreen_report_pos()` vs `touchscreen_set_mt_pos()` vs 裸 `input_report_abs()`

`include/linux/input/touchscreen.h` 整个头文件只有 32 行、三个符号：

| API | 做什么 | 位置 |
|---|---|---|
| `touchscreen_parse_properties(input, multitouch, prop)` | 从 DT 读 `touchscreen-*` 属性，填 `prop`，顺带设 abs 参数 | 声明 `:20-21`，实现 `drivers/input/touchscreen.c:65-146` |
| `touchscreen_set_mt_pos(pos, prop, x, y)` | 套用翻转/交换后**只写进 `pos->x` / `pos->y`**，一个事件都不发 | 声明 `:23-25`，实现 `touchscreen.c:173-181` |
| `touchscreen_report_pos(input, prop, x, y, multitouch)` | 套用翻转/交换后**直接发事件** | 声明 `:27-30`，实现 `touchscreen.c:195-204` |

两者共用同一个静态辅助 `touchscreen_apply_prop_to_x_y()`（`touchscreen.c:148-160`）：按 `prop->invert_x` / `invert_y` 做 `max - v`（`:152-156`）、按 `prop->swap_x_y` 交换（`:158-159`）。区别只在最后两行——`set_mt_pos` 是 `pos->x = x; pos->y = y;`（`:178-179`），`report_pos` 是两次 `input_report_abs()`，轴由 `multitouch` 三元表达式选（`:201-202`）。

★ **`touchscreen_set_mt_pos()` 是给 `input_mt_assign_slots()` 用的。** 你要先把这一帧所有触点的坐标填进 `struct input_mt_pos` 数组，交给槽位分配算法算出 slot 编号，然后才逐个 report——所以这一步不能发事件。§3 的骨架里如果硬件自带 contact id 就不需要它。

★ **裸 `input_report_abs()` 不做翻转和交换。** DT 里写了 `touchscreen-inverted-x` 却直接用 `input_report_abs()`，属性等于没生效——`06-devicetree.md` 里这条属性的消费者就是 `touchscreen_apply_prop_to_x_y()`，不经过它就没人读那三个 bool。

**错用会怎样：** 用 `touchscreen_set_mt_pos()` 代替 `touchscreen_report_pos()`，编译和运行都正常，但**一个事件都不会出去**——`pos` 是你自己栈上的结构体，写完就丢。这是本节里最安静的一种错用。

### §11.11 三类「存在但你用不了」的符号

`01-api.md` §7 有完整的六行清单，这里只讲**怎么自己判**，不重复它的内容。三类形态：

| 形态 | 特征 | 后果 |
|---|---|---|
| 只在私有头文件里声明 | 有 `EXPORT_SYMBOL*`，但声明在 `drivers/input/*.h`（如 `input-core-private.h` / `input-poller.h` / `input-compat.h`）而不是 `include/` | 树外模块拿不到原型；树内模块要显式 `#include "../xxx.h"`，等于承认你在改核心 |
| `static inline` | 没有 `EXPORT_SYMBOL`（也不需要），但只在头文件里 | 头文件是公开的就能用，是私有的就不能——判据在头文件的位置，不在有没有 export |
| 宏生成 | 符号名在整棵树里 grep 不到定义（如 `INPUT_GENERATE_ABS_ACCESSORS` 生成的 12 个 accessor） | 索引工具查不到，人也容易以为不存在 |

★ **`EXPORT_SYMBOL_GPL` 加满格 kerneldoc 也不代表你能用。** §8.1 的 `rmi_register_function_handler()` 就是标本：导出了、有 kerneldoc、就是没有 `include/` 声明。

★ **反过来，`static inline` 不需要导出。** `include/linux/input.h:425-455` 那七个 `input_report_*`、`include/linux/input/mt.h:85` 的 `input_mt_slot()`、`:90` 的 `input_is_mt_value()`、`:95` 的 `input_is_mt_axis()`、`:103-105` 的 `input_mt_report_slot_inactive()`，都是 `static inline`，`grep EXPORT_SYMBOL` 一个都查不到，但它们是本子系统最常用的 API。（同一个头文件里的 `input_mt_report_slot_state()` `:100-101` 和 `input_mt_sync_frame()` `:112` 反而是真函数，实现在 `drivers/input/input-mt.c` 里并带 `EXPORT_SYMBOL`——**同一个头文件里两种形态混排，这就是不能靠头文件位置反推形态的原因**。）**用「有没有 EXPORT_SYMBOL」单独判断会把它们全部误杀。**

**错用会怎样：** 树外模块引用私有头里的符号，编译期就是 `implicit declaration of function`；硬加 `extern` 声明骗过编译器，链接期变成 `Unknown symbol in module`（私有头的符号往往连 export 都没有）。这类错误发现得早，代价小；真正的代价是你在设计阶段就选错了实现路径。

### §11.12 `module_serio_driver()` 是真的，`module_input_handler()` 不存在

| 宏 | 存在吗 | 依据 |
|---|---|---|
| `module_serio_driver(drv)` | 存在 | `include/linux/serio.h:118` |
| `module_platform_driver(drv)` | 存在 | `include/linux/platform_device.h` |
| `module_input_handler(h)` | **不存在** | `grep -rn 'module_input_handler' --include=*.c --include=*.h .` → 空 |

★ **handler 模块必须自己写 `module_init` / `module_exit`。** 骨架见 §6：`input_register_handler()` 放 init、`input_unregister_handler()` 放 exit，没有一行宏能替你做。设备驱动侧则相反——arm64 上绝大多数 input 驱动是 platform 驱动或 i2c/spi 驱动，用对应总线的 `module_*_driver()` 宏。

**错用会怎样：** 照着 serio 那节的写法给 handler 也写一个 `module_input_handler(...)`，编译期报 `implicit declaration`（宏不存在时编译器会当成函数调用）。这条列在这里不是因为容易写错，而是因为**「别的框架有，这个没有」本身是需要被记录的事实**——找不到宏时会怀疑是自己没找对。

---

## §12 篇末速查：每个角色必调的 API

一行一个角色，列的是**不调就一定不工作**的最小集合，次序就是代码里的次序。可选项（`input_set_capability()` 之外的能力声明、`devm_` 变体、DT 解析）不在表内，见各节正文。

| 角色 | 必调 API（按顺序） | 一定不能忘的那一个 |
| --- | --- | --- |
| §1 中断按键 | `devm_input_allocate_device` → `input_set_capability` → `input_register_device` → 中断里 `input_report_key` + `input_sync` | `input_sync()`：不发帧就结束不了，用户态一个事件都读不到（§9.3） |
| §2 轮询设备 | `devm_input_allocate_device` → `input_setup_polling` → `input_set_poll_interval` → 能力声明 → `input_register_device` → 回调里上报 + `input_sync` | `input_setup_polling()` 必须在四个 interval setter **之前**（§11.9） |
| §3 多点触摸 | `devm_input_allocate_device` → `input_set_abs_params`(×N) → `touchscreen_parse_properties` → `input_mt_init_slots` → `input_register_device` → 每帧 `input_mt_slot` + `input_mt_report_slot_state` + `touchscreen_report_pos` + `input_mt_sync_frame` + `input_sync` | `input_mt_sync_frame()`：漏了就有幽灵触点（§11.4） |
| §4 矩阵键盘 | `devm_input_allocate_device` → `matrix_keypad_build_keymap` → `input_register_device` → 扫描里 `input_report_key` + `input_sync` | `dev->keycode` / `keycodesize` / `keycodemax` 由 `matrix_keypad_build_keymap()` 填，别自己填一半（§4.2） |
| §5 力反馈 | `devm_input_allocate_device` → `input_set_capability(EV_FF, FF_RUMBLE)` → `input_ff_create_memless` → `input_register_device` | `input_ff_create_memless()` 要在 `input_register_device()` **之前**；不要再调 `input_ff_create()`（§11.6） |
| §6 handler | `input_register_handler`（init）→ `connect()` 里 `input_register_handle` → `disconnect()` 里 `input_unregister_handle` → `input_unregister_handler`（exit） | `connect()` 里的 `input_register_handle()`：不挂上 `dev->h_list` 就收不到事件（§11.2） |
| §7 serio 端口 | `kzalloc(struct serio)` → 填 `write`/`open`/`close`/`id`/`name`/`parent` → `serio_register_port` → 中断里 `serio_interrupt` | `serio->write` 不能为空，`serio_write()` 是无条件解引用（§7.1） |
| §7 serio 设备驱动 | 填 `struct serio_driver` 的 `connect`/`interrupt` → `serio_register_driver`（或 `module_serio_driver`）；`connect()` 里 `serio_set_drvdata` + `serio_open` | `serio_open()` 返回的是裸 `-1` 不是错误码（§7.2） |
| §8 RMI4 function | 填 `struct rmi_function_handler`（`func`/`probe`/`config`/`attention`）→ 进 `fn_handlers[]` → `.config` 里 `drv->set_irq_bits` | `set_irq_bits()` 不是可选项，不调中断永远不来（§8.3） |
| §9 用户态读 evdev | `open(O_RDONLY)` → 可选 `ioctl(EVIOCSCLOCKID)` → `poll`/`read` 循环 → 按 `SYN_REPORT` 切帧 | 用 `input_event_sec` / `input_event_usec` 宏取时间，不要碰 `time` 字段（§9.1） |
| §10 uinput | `open(/dev/uinput, O_RDWR)` → `UI_SET_EVBIT`/`UI_SET_KEYBIT`/`UI_ABS_SETUP` → `UI_DEV_SETUP` → `UI_DEV_CREATE` → `write(struct input_event)` | `UI_DEV_CREATE` 之后要等一下（`sleep(1)`）再注入，否则用户态监听方还没连上（§10.2） |

★ **表里所有角色都要 `input_sync()`（或用户态侧的按 `SYN_REPORT` 切帧）**，唯一例外是 §6 handler 和 §7 serio 端口——它们不产生 input 事件。

★ **能力声明必须全部在 `input_register_device()` 之前完成。** 注册之后 `evdev` 已经按当时的能力位算好了缓冲区大小（§9.5），再置位不会重算，也不会重发 sysfs 里的 `capabilities/*`。

---

## 本篇引用的其他文档

| 你想知道 | 去 |
| --- | --- |
| API 的完整清单和错误码语义 | `01-api.md` |
| 结构体每个字段的含义 | `02-datastructures.md` |
| 事件从 `input_event()` 到 `read()` 的完整路径、锁、grab | `03-internals.md` |
| evdev / uinput / serio / rmi4 / 力反馈各自的框架细节 | `04-extensions.md` |
| DT 属性谁消费、配错了怎么定位 | `06-devicetree.md` |
| tracepoint、debugfs、sysfs、错误码分诊 | `07-debug.md` |
| 为什么要分 handler 和 handle、为什么槽位协议长这样 | `08-feynman.md` |
| 本篇每条论断的出处 | `99-evidence.md` |
