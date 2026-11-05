---
title: 调试方法
---

# 07 · gpio 调试与观测

> 本篇教的是「**去看哪个输出**」。所以每一节的落点都不是"代码怎么写"，而是"这个输出长什么样、什么条件下才会出现、看到了该怎么解读"。
>
> 目标架构 arm64。ACPI 相关的调试面（`gpiolib-acpi.c`、`gpiolib_acpi.ignore_*` 内核参数）本篇不涉及，裁剪范围记在 99 §2.7。
>
> **本篇的所有命令都是"应当这样敲"，不是"我敲过并抄回了输出"。** 凡是出现具体输出文本的地方，都是从内核源码里的格式串或 `Documentation/` 里的示例逐字搬来的，并且标了出处行号；没有出处的地方一律只写"会打印什么字段"，不编造样例。

---

## §0 按症状选工具

从"你现在看到的现象"出发，先定位到该看哪一节。

| 现象 | 先看 | 章节 |
|---|---|---|
| `/dev/gpiochipN` 根本不存在 | dmesg 里 `gpiochip_add_data` 的错误 | §2.4、§8.2 |
| 控制器在，但某根线在 debugfs 里查不到 | debugfs 只打印被 request 过或有名字的线 | §4.2 |
| `gpiod_get()` 返回 `-EPROBE_DEFER`，反复重试不好 | 分清是控制器没起来还是 irqchip 没起来 | §8.5 |
| `gpiod_get()` 返回 `-EBUSY` | 线被别人占了；只有 NONEXCLUSIVE 能共享 | §8.6 |
| 读到的电平和万用表量的相反 | 逻辑层 vs raw 层；trace 记的是 raw | §6.3 |
| `gpiod_direction_output()` 返回 `-EIO` | 两种完全不同的原因同码，必须看日志 | §8.3 |
| `gpiod_to_irq()` 返回 `-ENXIO` | 线没有中断映射 / domain 没建 | §8.4 |
| cdev 事件"丢了"但没有任何日志 | 丢事件只有 `pr_debug_ratelimited` | §2.3 |
| dmesg 里出现 `WARN` 但不知道触发条件 | 按语义分组的 WARN 清单 | §7 |
| 想在没有硬件的机器上跑通整条链路 | gpio-sim / gpio-mockup | §9 |
| 想从用户态读写某根线做验证 | `tools/gpio/` 四件套 | §10 |
| 想追一根线的每次读写 | tracepoint（注意只有两个事件） | §6 |
| 编译期想打开额外校验 | `CONFIG_DEBUG_GPIO`，注意它有四道门 | §1.2 |

---

## §1 编译期开关

### §1.1 核心开关一览

全部来自 `drivers/gpio/Kconfig`（下表行号即该 `config` 关键字所在行）。

| 行 | 符号 | 类型 | 默认 | 与调试的关系 |
|---|---|---|---|---|
| :14 | `GPIOLIB` | menuconfig bool | — | 总闸。关掉之后 `consumer.h` 走的是另一套桩函数（见 04 §5） |
| :25 | `GPIOLIB_FASTPATH_LIMIT` | int，`range 32 512`，`default 512` | 512 | 决定 `gpiod_get/set_array` 的栈上位图上限；超限只是性能退化不是错误，但会打印 warn（§2.4） |
| :37 | `OF_GPIO` | `def_bool y`，依赖 `OF && HAS_IOMEM` | y | 关掉它整篇 06 的 DT 路径都不编译 |
| :42 | `GPIO_ACPI` | `def_bool y`，依赖 `ACPI` | — | arm64 单板场景下裁掉，本篇不涉及 |
| :46 | `GPIOLIB_IRQCHIP` | bool，`select IRQ_DOMAIN` | 由驱动 select | 关掉它 §7.2 那组 irqchip WARN 全部不存在 |
| :50 | `DEBUG_GPIO` | bool，依赖 `DEBUG_KERNEL` | n | **本篇的主开关**，见 §1.2 |
| :61 | `GPIO_SYSFS` | bool（`if EXPERT`），依赖 `SYSFS`，`select GPIO_CDEV` | n | 废弃 ABI，见 §5.1 |
| :72 | `GPIO_CDEV` | bool，`default y` | y | `/dev/gpiochipN` |
| :86 | `GPIO_CDEV_V1` | bool，`default y`，依赖 `GPIO_CDEV` | y | v1 ioctl；关掉后 §10.2 表里 v1 那 7 个 ioctl 直接返回失败 |
| :98 | `GPIO_GENERIC` | tristate，依赖 `HAS_IOMEM` | 由驱动 select | `gpio-mmio.c`（bgpio），见 04 §6 |
| :102 | `GPIO_REGMAP` | bool，`select REGMAP` | 由驱动 select | `gpio-regmap.c`，见 04 §7 |

`GPIO_SYSFS` 那行的 `select GPIO_CDEV` 后面跟了一句源码注释 —— `drivers/gpio/Kconfig:64`：

```
	select GPIO_CDEV # We need to encourage the new ABI
```

也就是说**打开废弃的 sysfs ABI 会强制把新的字符设备 ABI 也编进来**。这是有意为之的"劝退"设计，不是依赖关系。

### §1.2 `CONFIG_DEBUG_GPIO` 有四道门，`grep` 只能看到第一道

这是本篇最值得先讲的一件事，因为它示范了"用 grep 找开关"的失败模式。

直觉做法是全树搜 `CONFIG_DEBUG_GPIO`：

```bash
grep -rn 'CONFIG_DEBUG_GPIO' --include='*.c' --include='*.h' --include='Makefile' --include='Kconfig' .
```

本树只有**两个**命中：`drivers/gpio/Kconfig:50`（定义）和 `drivers/gpio/Makefile:4`（使用）。看到这里很容易得出"这个开关是死的、Kconfig 帮助文本在吹牛"的结论。**这个结论是错的。** 链条一共四跳，后三跳都不含 `CONFIG_` 三个字母：

```
第 1 跳  CONFIG_DEBUG_KERNEL          Kconfig:52   depends on
   ↓
第 2 跳  CONFIG_DEBUG_GPIO            Kconfig:50   bool "Debug GPIO calls"
   ↓
第 3 跳  ccflags-$(CONFIG_DEBUG_GPIO) += -DDEBUG
                                      drivers/gpio/Makefile:4
         ← 从这里开始，符号名变成了裸的 DEBUG
   ↓
第 4 跳  #ifdef DEBUG
         #define extra_checks 1       gpiolib.c:50-54
         #else
         #define extra_checks 0
   ↓
落地     extra_checks 的 9 个使用点
```

`drivers/gpio/gpiolib.c:44-54` 逐字：

```c
/* When debugging, extend minimal trust to callers and platform code.
 * Also emit diagnostic messages that may help initial bringup, when
 * board setup or driver bugs are most common.
 *
 * Otherwise, minimize overhead in what may be bitbanging codepaths.
 */
#ifdef	DEBUG
#define	extra_checks	1
#else
#define	extra_checks	0
#endif
```

`extra_checks` 的 9 个使用点（`grep -n extra_checks drivers/gpio/gpiolib.c`）：

| 用法 | 行 | 作用 |
|---|---|---|
| `WARN_ON(extra_checks)` | :2090 | `gpiod_free()` 拿到无效 desc 时才吼；平时静默 |
| `might_sleep_if(extra_checks)` | :3452、:3471、:3502、:3528、:3549、:3567、:3590、:3632 | 8 个 `*_cansleep` API 的入口，检查调用上下文真的可睡 |

对照 `Kconfig:54-59` 的帮助文本：

- "help ensure that GPIOs have been properly initialized before they are used" → `WARN_ON(extra_checks)` @ :2090
- "sleeping calls are not made from non-sleeping contexts" → 8 处 `might_sleep_if(extra_checks)`
- "They can make bitbanged serial protocols slower" → `-DDEBUG` 同时把 §3 里那 20 条 debug 打印全部无条件打开

**帮助文本和代码是一致的。** 需要显式记下这一点，因为 gpio 是"宏改名"这个索引盲区的典型样本 —— 见 99 §4 的交叉验证实例。

第 3 跳还有一个不在帮助文本里的副作用：`-DDEBUG` 是**目录级**的（`ccflags-` 作用于 `drivers/gpio/` 下所有编译单元），所以它同时让 `pr_debug` / `dev_dbg` / `chip_dbg` / `gpiod_dbg` **绕过 dynamic debug 直接变成 `printk`**。这意味着：

打开 `CONFIG_DEBUG_GPIO` 之后，§3 讲的 dynamic debug 操作**全部失去意义**（那些打印已经无条件开着了），而且关不掉。调试期两者选一个，不要同时用。

---

## §2 内核日志

### §2.1 gpio 的日志有两套完全不同的前缀，grep 时要分别处理

`drivers/gpio/gpiolib.h` 定义了两族打印宏，前缀格式**不一样**，底层函数也**不一样**：

| 族 | 定义行 | 展开成 | 前缀格式 | 能否按设备过滤 |
|---|---|---|---|---|
| `gpiod_emerg/crit/err/warn/info/dbg(desc, …)` | :216、:219、:222、:225、:228、:231 | `pr_emerg` … `pr_debug` | `"gpio-%d (%s): "` = **全局编号** + `desc->label ?: "?"` | ✗（`pr_*` 没有 device） |
| `chip_emerg/crit/err/warn/info/dbg(gc, …)` | :237、:239、:241、:243、:245、:247 | `dev_emerg` … `dev_dbg` | `"(%s): "` = `gc->label`，再由 `dev_*` 自动加上 `gpiochipN: ` | ✓ |

`gpiolib.h:222-224` 逐字：

```c
#define gpiod_err(desc, fmt, ...)					       \
	pr_err("gpio-%d (%s): " fmt, desc_to_gpio(desc), desc->label ? : "?",  \
		 ##__VA_ARGS__)
```

`gpiolib.h:241-242`：

```c
#define chip_err(gc, fmt, ...)					\
	dev_err(&gc->gpiodev->dev, "(%s): " fmt, gc->label, ##__VA_ARGS__)
```

三条实操结论：

1. **想抓全部 gpio 日志，一个 grep 抓不住。** `gpiod_*` 那族的行首是 `gpio-<全局号> (<label>):`，`chip_*` 那族的行首是 `gpiochip<N> (<label>):`。两个模式都要。
2. **`gpiod_*` 里的数字是全局 GPIO 编号，不是 offset。** 换算关系见 04 §5；`/sys/kernel/debug/gpio` 的表头会同时给出全局区间（§4.3），是最方便的换算表。
3. **`desc->label ?: "?"`**：日志里出现 `(?)` 说明这根线的 label 是 NULL —— 通常意味着它**还没被 request 成功**，或者刚被 `desc_set_label(desc, NULL)` 清掉（`gpiolib.c:1957`，request 失败回滚路径）。看到 `(?)` 就别再找"是谁占用了"，答案是没人。

### §2.2 可见性等级：哪些日志一定看得见，哪些要额外开门

| 等级 | 出现在 | 默认可见性 | 备注 |
|---|---|---|---|
| `pr_crit` / `dev_crit` | 1 处：`gpiolib.c:906` | 一定可见 | 见 §2.4 第 1 行 |
| `pr_err` / `dev_err`（含 `chip_err`） | `gpiolib.c`、`gpiolib-of.c` 多处 | 一定可见 | 06 §10 已给出 DT 相关的 19 条完整映射表 |
| `pr_warn` / `dev_warn`（含 `chip_warn`、`gpiod_warn`） | 多处 | 一定可见 | §2.4 |
| `pr_info` / `dev_info`（含 `chip_info`） | 少数 | 取决于 `CONFIG_MESSAGE_LOGLEVEL_DEFAULT` / `loglevel=` | quirk 改写会在这一级打印，见 06 §5.1 |
| `pr_debug` / `dev_dbg`（含 `chip_dbg`、`gpiod_dbg`） | 20 处（§3.2 逐文件表） | **默认不可见**，要 dynamic debug 或 `-DDEBUG` | §3 |
| `pr_debug_ratelimited` | **4 处，全在 `gpiolib-cdev.c`** | 默认不可见，**开了还可能被限速压掉** | §2.3 |
| `pr_err_once` | 1 处，在 `gpiolib-acpi.c:359` | — | ACPI，本篇裁掉 |

`_once` / `_ratelimited` 变体的完整普查命令（结果就是上表最后两行）：

```bash
grep -rn '_once(\|_ratelimited(' drivers/gpio/gpiolib*.c drivers/gpio/gpio-mmio.c drivers/gpio/gpio-regmap.c
```

非 ACPI 的命中恰好 4 条，全部是 `pr_debug_ratelimited`，全部在 cdev。

### §2.3 cdev 丢事件是"双重不可见"的

四条 `pr_debug_ratelimited`（`drivers/gpio/gpiolib-cdev.c`）：

| 行 | 消息 | 含义 |
|---|---|---|
| :756 | `event FIFO is full - event dropped` | v1 `lineevent` 的边沿事件队列满 |
| :1006 | `debouncer failed to read line value` | 软件去抖定时器里读线失败 |
| :2180 | `event FIFO is full - event dropped` | v2 `linereq` 的边沿事件队列满 |
| :2670 | `lineinfo event FIFO is full - event dropped` | `LINEINFO_WATCH` 的变更通知队列满 |

三个 ：

- **丢事件默认完全静默。** 用户态只看到"某些边沿没收到"，dmesg 里一个字都没有。要看见必须先开 dynamic debug（§3.1）。
- **开了还可能看不全。** `_ratelimited` 的语义是每个打印点在一个时间窗内只输出有限条，超出的部分只留一句汇总。所以"日志里出现 3 次"**不等于**"只丢了 3 个事件"。判断丢了多少要靠用户态自己的序号/时间戳连续性，不能靠数日志行数。
- **`pr_debug_ratelimited` 用的是 `pr_` 族，没有设备名。** 系统里有多个 gpiochip 同时丢事件时，这 4 条日志**无法区分是哪个控制器**（对比 §2.1：`chip_dbg` 才有设备前缀）。要定位到控制器只能靠 §6 的 tracepoint 或者一次只压测一个 chip。

:756 和 :2180 的文本完全相同 —— 看到 `event FIFO is full - event dropped` 时**无法从消息本身判断走的是 v1 还是 v2 路径**。要区分只能看用户态用的是哪个 ioctl（§10.2）。

### §2.4 注册期的关键日志（一定可见的那些）

按"看到它意味着什么"排：

| 消息（逐字） | 出处 | 等级 | 意味着 |
|---|---|---|---|
| `REMOVING GPIOCHIP WITH GPIOS STILL REQUESTED` | `gpiolib.c:906-907` `dev_crit` | crit | 有线还被占着就在拆控制器。**注意：只吼，不阻止** —— 见 §2.5 |
| `tried to insert a GPIO chip with zero lines` | `gpiolib.c:674` `chip_err`，随后 `ret = -EINVAL`（:675） | err | `ngpio` 既没被驱动填也没从 `ngpios` 属性读到（06 §2.4） |
| `gpiochip%d: %u GPIOs added but FASTPATH_NGPIO is set to %d` | `gpiolib.c:679-681` | warn | 线数超过 `GPIOLIB_FASTPATH_LIMIT`；只是数组操作要走 kmalloc，功能正常 |
| `missing irqdomain vital data` | `gpiolib.c:1218`，随后 `return -EINVAL` | err | 层级中断三件套（`parent_domain` / `child_to_parent_hwirq` / `fwnode`）没配齐，见 05 §6 |
| `you cannot have chained interrupts on a chip that may sleep` | `gpiolib.c:1557`，随后 `return -EINVAL` | err | 级联 handler 跑在硬中断上下文，和 `can_sleep` 天然矛盾 |
| `%pfw: Ignoring %u default trigger` | `gpiolib.c:1567-1568`，在 `WARN(...)` 里 | **WARN + 栈回溯** | 驱动同时给了 fwnode 和 `default_type`；核心把 type 重置成 `IRQ_TYPE_NONE`（:1569） |
| `to_irq is redefined in %s and you shouldn't rely on it` | `gpiolib.c:1572` `chip_warn` | warn | 驱动自己填了 `gc->to_irq`，紧接着被 `:1574` 覆写 |
| `%s unknown fwnode type proceed anyway` | `gpiolib.c:1088` `chip_err` | err | **这条在成功路径上**。看到它不代表注册失败，见 03 §A3 |
| `could not create pin range` | `gpiolib.c:1880` | — | pinctrl 交叉引用建不起来，见 06 §2.7 |

DT 相关的错误消息（`gpiolib-of.c` 全部 19 条 + `gpiolib.c` 6 条）已经在 **06 §10** 列成完整的「消息 → 原因」映射表，本篇不重复。

### §2.5 `gpiochip_remove()` 的 kerneldoc 和代码不一致

`gpiolib.c:898-907`：

```c
	spin_lock_irqsave(&gpio_lock, flags);
	for (i = 0; i < gdev->ngpio; i++) {
		if (gpiochip_is_requested(gc, i))
			break;
	}
	spin_unlock_irqrestore(&gpio_lock, flags);

	if (i != gdev->ngpio)
		dev_crit(&gdev->dev,
			 "REMOVING GPIOCHIP WITH GPIOS STILL REQUESTED\n");
```

扫一遍找到第一根仍被 request 的线，打一条 crit，**然后继续往下拆**（`:914` `gcdev_unregister(gdev)`）。kerneldoc 的措辞暗示"还有线被占用会阻止移除"，但代码里没有任何 `return`。

实操含义：**看到这条 crit 说明已经晚了。** 它不是"拒绝执行"的提示，而是"即将发生 use-after-free 风险"的讣告。真正的防线是消费者持有的 `module_get` / `get_device` 引用（03 §B4），而不是这个检查。

这一条登记为 99 §6 的文档—代码冲突之一。

---

## §3 dynamic debug

### §3.1 两种打开方式，作用范围不同

前提：`CONFIG_DYNAMIC_DEBUG=y`，且**没有**打开 `CONFIG_DEBUG_GPIO`（否则那些打印已经无条件开着，dynamic debug 对它们不再生效，见 §1.2）。

```bash
mount -t debugfs none /sys/kernel/debug 2>/dev/null

# 按文件开（最常用；gpio 的 debug 打印集中在 4 个文件里）
echo 'file gpiolib.c +p'       > /sys/kernel/debug/dynamic_debug/control
echo 'file gpiolib-of.c +p'    > /sys/kernel/debug/dynamic_debug/control
echo 'file gpiolib-cdev.c +p'  > /sys/kernel/debug/dynamic_debug/control
echo 'file gpiolib-sysfs.c +p' > /sys/kernel/debug/dynamic_debug/control

# 按函数开（想只看 DT 查找那一条链时最省事）
echo 'func of_get_named_gpiod_flags +p' > /sys/kernel/debug/dynamic_debug/control
echo 'func of_find_gpio +p'             > /sys/kernel/debug/dynamic_debug/control

# 启动阶段就要看（注册期的打印在 initcall 里，等系统起来就来不及开了）
#   内核命令行加：
#   dyndbg="file gpiolib.c +p; file gpiolib-of.c +p"
```

最后一条最容易被忽略：**控制器注册和 hog 都发生在 `subsys_initcall` / `core_initcall` 阶段**（`gpiolib.c:4484` `core_initcall(gpiolib_dev_init)`、`:4609` `subsys_initcall(gpiolib_debugfs_init)`）。等 shell 起来再写 `control` 文件，注册期的 `pr_debug` 早就过去了。要看注册期必须走 `dyndbg=` 内核参数。

### §3.2 逐文件能开出多少条 —— 以及为什么"能不能按设备过滤"不一样

`chip_dbg` 展开成 `dev_dbg`，`gpiod_dbg` 展开成 `pr_debug`（§2.1），所以真实的可开条数要把宏也算进去：

| 文件 | `pr_debug` 直写 | `gpiod_dbg` | **pr 族合计** | `dev_dbg` 直写 | `chip_dbg` | **dev 族合计** | 总计 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `gpiolib.c` | 1 | 2 | 3 | 9 | 5 | 14 | 17 |
| `gpiolib-of.c` | 2 | 0 | 2 | 0 | 0 | 0 | 2 |
| `gpiolib-cdev.c` | 0 | 0 | 0 | 4 | 1 | 5 | 5 |
| `gpiolib-sysfs.c` | 4 | 2 | 6 | 0 | 0 | 0 | 6 |
| `gpiolib-devres.c` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `gpio-mmio.c` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `gpio-regmap.c` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **合计** | 7 | 4 | **11** | 13 | 6 | **19** | **30** |

（表中 `pr_debug_ratelimited` 的 4 条计在 `gpiolib-cdev.c` 之外单列，见 §2.3；上表的 cdev 5 条不含它们。）

重跑命令：

```bash
for f in gpiolib.c gpiolib-of.c gpiolib-cdev.c gpiolib-sysfs.c gpiolib-devres.c gpio-mmio.c gpio-regmap.c; do
  printf '%-18s pr_debug=%-3s dev_dbg=%-3s chip_dbg=%-3s gpiod_dbg=%-3s\n' "$f" \
   "$(grep -c 'pr_debug(' drivers/gpio/$f)" "$(grep -c 'dev_dbg(' drivers/gpio/$f)" \
   "$(grep -c 'chip_dbg(' drivers/gpio/$f)" "$(grep -c 'gpiod_dbg(' drivers/gpio/$f)"
done
```

**`gpiolib-devres.c` / `gpio-mmio.c` / `gpio-regmap.c` 一条 debug 打印都没有。** 所以：devres 自动释放的时机、bgpio 的寄存器访问、regmap 提供方的读写，**都无法通过 dynamic debug 观察**。这三条路径只能靠 §6 的 tracepoint（devres 和 bgpio 最终都落到 `gpiod_*_commit`，是有 trace 的）或者 kprobe。

**`pr_debug` 族只能按 file/func/line 过滤，不能按设备过滤。** `gpiolib-sysfs.c` 的 6 条全是 pr 族 —— 这意味着 sysfs 那条路径的调试输出在多控制器系统上是混在一起的。而 `gpiolib-cdev.c` 的 5 条全是 dev 族，可以按 `gpiochipN` 分开看。

### §3.3 一条最值得先开的

06 §10 已经点明：`gpiolib-of.c:250` 的 `pr_debug` 是整个 DT 查找链上唯一记录"用哪个属性名去查、结果如何"的那一行。凡是"DTS 明明写了但拿不到"的问题，先开它：

```bash
echo 'file gpiolib-of.c +p' > /sys/kernel/debug/dynamic_debug/control
```

---

## §4 debugfs：`/sys/kernel/debug/gpio`

### §4.1 挂载点和生成时机

`drivers/gpio/gpiolib.c:4603-4609`：

```c
static int __init gpiolib_debugfs_init(void)
{
	/* /sys/kernel/debug/gpio */
	debugfs_create_file("gpio", 0444, NULL, NULL, &gpiolib_fops);
	return 0;
}
subsys_initcall(gpiolib_debugfs_init);
```

三个细节：

- 文件名就叫 `gpio`，直接挂在 debugfs **根目录**下，不在子目录里。
- 模式 **0444 —— 只读**。gpio 核心的 debugfs 面**没有任何可写文件**。（可写的调试面在 sysfs，见 §5；以及 gpio-sim/gpio-mockup 自己的面，见 §9。）
- `subsys_initcall`，整个 `#ifdef CONFIG_DEBUG_FS` 块从 `:4486` 开始。没有 `CONFIG_DEBUG_FS` 时这个文件不存在，而且**没有任何替代品**。

### §4.2 它不打印全部线 —— 两条过滤规则

`gpiolib_dbg_show()`（`gpiolib.c:4488`）用 `for_each_gpio_desc(gc, desc)`（`:4498`）遍历一个 chip 的所有 desc，但输出与否取决于两个条件：

- 线**被 request 过**（`test_bit(FLAG_REQUESTED, &desc->flags)`，`:4499`）→ 打印完整一行：全局号、`desc->name`、`desc->label`、方向、电平、是否 used-as-irq、是否 active-low（`:4505-4510`）。
- 线**没被 request 但有名字**（`else if (desc->name)`，`:4511`）→ 只打印全局号和名字（`:4512`）。
- 线**既没被 request 也没有名字** → **一行都不打印**。

这是排查"我的线在 debugfs 里查不到"时的第一答案：**查不到通常不是坏了，而是它既没人 request 也没在 DTS 里起名字**。要让它现身，两条路 —— 在 DTS 里写 `gpio-line-names`（06 §2.5），或者从用户态 request 一下（§10）。

第二个后果：**`/sys/kernel/debug/gpio` 不能用来数一个控制器有多少根线**。要数线数看 §4.3 表头里的区间，或者 `/sys/class/gpio/gpiochipN/ngpio`（§5.3），或者 `tools/gpio/lsgpio`（§10.1）。

### §4.3 读它会真的去读硬件

`gpiolib_dbg_show()` 在打印每一根被 request 的线时会调用两个**会碰硬件**的函数：

- `gpiod_get_direction(desc)`（`gpiolib.c:4500`）—— 这不是读缓存，而是**调 `gc->get_direction()` 回读硬件寄存器**，并且会**改写 `desc->flags` 里的 `FLAG_IS_OUT`**（见 02 §4 对 `gpiod_get_direction()` 的说明）。
- `gpio_chip_get_value(gc, desc)`（`gpiolib.c:4502`，被调函数在 `:2623`）—— 回读引脚电平。

两个 ：

- **`cat /sys/kernel/debug/gpio` 是有副作用的操作，不是被动快照。** 对某些控制器（读某个寄存器会清中断状态、或者读操作本身耗时很长的 I²C/SPI 扩展芯片）这会影响被观测的系统。调试时序敏感的问题时，把 `cat` 本身也算进变量。
- 反过来利用它：**如果驱动的 `get_direction()` 有 bug，`cat` 一次就能让 `FLAG_IS_OUT` 和硬件重新对齐**。所以"cat 一下之后现象变了"本身就是一条线索，指向驱动的方向缓存不一致。

`gpio_chip_get_value()` 在 `gc->get` 为 NULL 时返回 `-EIO`（`gpiolib.c:2623`）。但**这个负值不会以数字形式出现在输出里** —— 格式串里那一项是 `value >= 0 ? (value ? "hi" : "lo") : "?  "`（`gpiolib.c:4508`）：

**电平列印出 `?` 就是"读失败"**，可能是 `gc->get` 缺失，也可能是 I²C/SPI 扩展芯片这次通信失败。它**不是**"未知/未初始化"，而是一次真实的读操作返回了负值。看到 `?` 要去 §8.3 查 `-EIO` 的两组语义。

### §4.4 输出格式：字段清单

不抄样例（本机未运行），只逐字引用格式串并逐项解释字段。

**每根线（被 request 的）** —— `gpiolib.c:4505-4510` 逐字：

```c
			seq_printf(s, " gpio-%-3d (%-20.20s|%-20.20s) %s %s %s%s\n",
				   gpio, desc->name ?: "", desc->label,
				   is_out ? "out" : "in ",
				   value >= 0 ? (value ? "hi" : "lo") : "?  ",
				   is_irq ? "IRQ " : "",
				   active_low ? "ACTIVE LOW" : "");
```

字段依次是：

| 位置 | 内容 | 可能取值 | 注意 |
|---|---|---|---|
| 1 | 全局 GPIO 编号 | `gpio-<n>` | `%-3d`，超过 3 位不截断只是不对齐 |
| 2 | `desc->name` | 名字，或**空串** | `desc->name ?: ""` —— 被 request 但没名字的线这一格是空白，不是 `?` |
| 3 | `desc->label` | 消费者名 | 不带 `?:` 兜底，正常路径下 request 成功必然非 NULL |
| 4 | 方向 | `out` / `in ` | 注意 `in` 后面带一个空格补位 |
| 5 | 电平 | `hi` / `lo` / `? ` | `?` = 读失败，见 §4.3 |
| 6 | 中断标记 | `IRQ ` 或空 | 对应 `FLAG_USED_AS_IRQ` |
| 7 | 极性标记 | `ACTIVE LOW` 或空 | 对应 `FLAG_ACTIVE_LOW`。有这个字样时**第 5 格的 hi/lo 是逻辑值**，和引脚实测电平相反 |

两个 `%-20.20s` 是**硬截断**。名字或 label 超过 20 字符会被无声截短 —— `gpio-line-names` 里写长名字（06 §2.5 的 jethub 例子里最长是 `ZigBeeRESET`，11 字符，安全）时留意这一点。debugfs 里看到的名字不一定是完整名字，完整名字要看 `lsgpio`（§10.1，它按 `%s` 打印，不截断）。

**每根线（未 request 但有名字的）** —— `gpiolib.c:4512`：

```c
			seq_printf(s, " gpio-%-3d (%-20.20s)\n", gpio, desc->name);
```

只有两格。**这一行没有方向、没有电平、没有极性** —— 所以"有名字但没人用"的线在 debugfs 里查不到任何状态信息。想看它的状态必须先从用户态 request（§10.1 的 `lsgpio` 不 request 也能读到 flags，走的是 cdev 的 `LINEINFO` ioctl，不走 debugfs）。

**每个 chip 的表头** —— `gpiolib_seq_show()`（`gpiolib.c:4561`）：

| 片段 | 出处行 | 条件 | 说明 |
|---|---|---|---|
| `%s%s: (dangling chip)` | :4568-4569 | `gdev->chip` 为 NULL，随后 `return 0`（:4570） | 控制器已经被 remove 但 gdev 还有用户在引用（对应 §2.5 那条 crit 之后的状态）。**看到这个字样说明系统里有悬挂的 gpio_device，而且这个 chip 的线一根都不会打印** |
| `%s%s: GPIOs %d-%d` | :4573-4575 | 总是 | 前一个 `%s` 是 `s->private`（chip 之间的分隔），第二个是 `dev_name(&gdev->dev)`；区间是 `gdev->base` ~ `gdev->base + gdev->ngpio - 1`。**这是全局号 ↔ offset 的换算表** |
| `, parent: %s/%s` | :4578-4580 | `gc->parent`（:4576）非空 | 父设备的总线名和设备名；父设备没有 bus 时打 `no-bus`（`parent->bus ? parent->bus->name : "no-bus"`） |
| `, %s` | :4582 | `gc->label` 非空 | label |
| `, can sleep` | :4584 | `gc->can_sleep` 为真 | 一眼判断这个控制器能不能在原子上下文里用（决定该调 `gpiod_get_value()` 还是 `gpiod_get_value_cansleep()`，见 01 §3） |
| `:` + 换行 | :4585 | 总是 | 表头以冒号结尾 |

`, parent: platform/…` 这种形式直接印证了 02 §1 讲的"控制器在 platform 总线上、gpio_device 在 gpio 总线上、两者只由 `dev.parent` 缝在一起"。注意读的字段是 **`gc->parent`**（驱动填的那个），而 `gdev->dev.parent` 是在 `gpiolib.c:620` 从它拷过来的 —— 两者同源，所以这一行是那张关系图**唯一的运行期证据**，比读代码直观。

### §4.5 驱动可以整段接管输出

`gpiolib.c:4587-4590` 逐字：

```c
	if (gc->dbg_show)
		gc->dbg_show(s, gc);
	else
		gpiolib_dbg_show(s, gdev);
```

chip 表头打完之后，如果驱动填了 `gc->dbg_show`，核心就**调它而不是调 `gpiolib_dbg_show()`**。

后果：**遇到 `gc->dbg_show` 非空的控制器，§4.2/§4.3/§4.4 讲的规则全部不适用** —— 输出格式、打不打印未 request 的线、要不要回读硬件，全由那个驱动自己决定。排查前先确认：

```bash
grep -rn 'dbg_show[[:space:]]*=' drivers/gpio/ | grep -v gpiolib
```

看到自己的控制器在列表里，就直接去读那个驱动的 `dbg_show` 实现，不要按本节的格式去解析。

---

## §5 sysfs 的三个面

gpio 在 sysfs 上有三个互不相干的面，别混。

| 面 | 路径 | 由谁建 | 可写？ | 状态 |
|---|---|---|---|---|
| A. legacy class | `/sys/class/gpio/` | `gpiolib-sysfs.c`（`CONFIG_GPIO_SYSFS`） | **是** | 废弃，见 §5.1 |
| B. gpio 总线设备 | `/sys/bus/gpio/devices/gpiochipN/` | `gpiolib.c:619` 把 `gdev->dev.bus` 设成 `gpio_bus_type` | 只读 | 见 §5.4 |
| C. 提供方自己的面 | 各驱动自定义，如 `gpio-sim` 的 `/sys/devices/platform/gpio-sim.X/` | 驱动 | 视驱动 | 见 §9.1 |

### §5.1 A 面：`/sys/class/gpio/`（废弃，但调试时仍最方便）

`Documentation/admin-guide/gpio/sysfs.rst:4-9` 的警告逐字：

```
.. warning::

  THIS ABI IS DEPRECATED, THE ABI DOCUMENTATION HAS BEEN MOVED TO
  Documentation/ABI/obsolete/sysfs-gpio AND NEW USERSPACE CONSUMERS
  ARE SUPPOSED TO USE THE CHARACTER DEVICE ABI. THIS OLD SYSFS ABI WILL
  NOT BE DEVELOPED (NO NEW FEATURES), IT WILL JUST BE MAINTAINED.
```

**"废弃"的含义是不加新特性，不是不能用。** 手工排查一根线时它仍然是最少打字的办法（一个 `echo` 就能改电平，不用写 ioctl 程序）。写脚本或写产品代码请走 cdev（§10）。

`gpio_class` 定义在 `drivers/gpio/gpiolib-sysfs.c:527`，`.name = "gpio"`。类级两个属性（`gpio_class_attrs[]` 在 `:520-524`）：

| 文件 | 实现 | 模式 | 语义 |
|---|---|---|---|
| `export` | `export_store()` `:429` | `CLASS_ATTR_WO`（`:482`）**只写** | 写入一个**全局 GPIO 编号**，把该线 request 给 sysfs 并创建 `gpioN/` 目录 |
| `unexport` | `unexport_store()` `:484` | `CLASS_ATTR_WO`（`:518`）**只写** | 写入同一个编号，撤销 |

**`export` / `unexport` 只吃全局编号，不吃 `(chip, offset)`。** 全局编号从哪来？`/sys/kernel/debug/gpio` 的表头（§4.4）或者 `/sys/class/gpio/gpiochipN/base`（§5.3）。这是 legacy ABI 最大的实操摩擦点，也是 cdev 用 `(设备节点, offset)` 取代它的直接原因（08 §5 讲了为什么全局编号是个错误的抽象）。

`export_store()` 的拒绝路径（按代码顺序）：

| 行 | 条件 | 日志 | 返回 |
|---|---|---|---|
| :439 | `kstrtol` 解析失败 | 只有末尾的 `pr_debug`（:479） | kstrtol 的错误码 |
| :443-447 | `gpio_to_desc(gpio)` 返回 NULL | `pr_warn("%s: invalid GPIO %ld\n", …)` | `-EINVAL` |
| :451-454 | `!gpiochip_line_is_valid(gc, offset)` | `pr_warn("%s: GPIO %ld masked\n", …)` | `-EINVAL` |
| :461 | `gpiod_request_user(desc, "sysfs")` 失败 | `pr_debug`（:479） | 传播（`-EBUSY` 最常见） |
| :465-468 | `gpiod_set_transitory(desc, false)` 失败 | `pr_debug`（:479） | 传播，并 `gpiod_free(desc)` 回滚 |
| :471-475 | `gpiod_export(desc, true)` 失败 | 见下表 | 传播，并 `gpiod_free(desc)` 回滚 |

**`masked` 那条日志是 `gpio-reserved-ranges` 的运行期回音。** 06 §2.6 讲了 DTS 里的 `gpio-reserved-ranges` 会把线从 `valid_mask` 里抠掉，而那条路径几乎不打日志；`echo N > export` 时得到 `GPIO %ld masked` + `-EINVAL` 是**验证某根线是否被 reserved 掉的最快办法**。

`gpiod_export()` 自己的拒绝路径（同文件）：

| 行 | 条件 | 日志 | 返回 |
|---|---|---|---|
| :563-564 | `!gpio_class.p`（sysfs 还没就绪） | `pr_debug("%s: called too early!\n", …)` | `-ENOENT` |
| :568-569 | `!desc` | `pr_debug("%s: invalid gpio descriptor\n", …)` | `-EINVAL` |
| :578-580 | `!chip \|\| !gdev->mockdev`（控制器正在拆） | 无 | `-ENODEV`（:579） |
| :584-593 | 没有 `FLAG_REQUESTED`，或已经有 `FLAG_EXPORT` | `gpiod_dbg("%s: unavailable (requested=%d, exported=%d)\n", …)`（:587-590） | `-EPERM`（:591） |
| :596-599 | `kzalloc` 失败 | 无 | `-ENOMEM`（:598） |

上面 5 条日志里有 3 条是 `pr_debug` / `gpiod_dbg` —— **默认全部不可见**。`echo N > export` 返回 `Operation not permitted`（`-EPERM`）而 dmesg 一片空白时，先 `echo 'file gpiolib-sysfs.c +p' > …/control`（§3.1），那一条 `unavailable (requested=…, exported=…)` 会直接告诉你是"没 request"还是"重复 export"。

### §5.2 每根线的四个文件 —— `direction` 和 `value` 的语义层不一样

`gpio_attrs[]`（`gpiolib-sysfs.c:363-369`）四个文件：

| 文件 | show / store | 模式 | 读走哪一层 | 写走哪一层 |
|---|---|---|---|---|
| `direction` | `:60` / `:77`，`DEVICE_ATTR_RW`（`:99`） | 0644 | `gpiod_get_direction(desc)`（`:69`）**回读硬件** | 见下 |
| `value` | `:101` / `:120`，`DEVICE_ATTR_PREALLOC(value, S_IWUSR \| S_IRUGO, …)`（`:143`） | 0644 | `gpiod_get_value_cansleep(desc)`（`:110`）**逻辑层** | `gpiod_set_value_cansleep(desc, value)`（`:135`）**逻辑层** |
| `edge` | `:229` / `:247`，`DEVICE_ATTR_RW`（`:279`） | 0644 | 读的是软件状态 `data->irq_flags`，不碰硬件 | `sysfs_match_string(trigger_names, buf)`（`:254`），见 §5.2.2 |
| `active_low` | `:304` / `:320`，`DEVICE_ATTR_RW`（`:339`） | 0644 | `test_bit(FLAG_ACTIVE_LOW, …)` | `assign_bit(FLAG_ACTIVE_LOW, &desc->flags, value)`（`:292`） |

#### §5.2.1 同一个目录下，`direction` 写的是 raw，`value` 写的是逻辑值

`direction_store()`（`gpiolib-sysfs.c:77-98`）逐字：

```c
	if (sysfs_streq(buf, "high"))
		status = gpiod_direction_output_raw(desc, 1);
	else if (sysfs_streq(buf, "out") || sysfs_streq(buf, "low"))
		status = gpiod_direction_output_raw(desc, 0);
	else if (sysfs_streq(buf, "in"))
		status = gpiod_direction_input(desc);
	else
		status = -EINVAL;
```

对比 `value_store()` 里的 `gpiod_set_value_cansleep(desc, value)`（`:135`）。

**`echo high > direction` 和 `echo out > direction; echo 1 > value` 在 `active_low=1` 的线上结果相反。**

| 操作 | 走的 API | active_low=0 时 PAD | active_low=1 时 PAD |
|---|---|---|---|
| `echo high > direction` | `gpiod_direction_output_raw(desc, 1)`（`:87`） | 高 | **高**（raw，不反转） |
| `echo low  > direction` | `gpiod_direction_output_raw(desc, 0)`（`:89`） | 低 | **低**（raw，不反转） |
| `echo out > direction` | `gpiod_direction_output_raw(desc, 0)`（`:89`） | 低 | **低** |
| `echo 1 > value` | `gpiod_set_value_cansleep(desc, 1)`（`:135`） | 高 | **低**（逻辑，反转） |
| `echo 0 > value` | `gpiod_set_value_cansleep(desc, 0)`（`:135`） | 低 | **高** |

而**读**的时候两个文件又都是逻辑层（`direction` 无极性概念；`value_show` 用 `gpiod_get_value_cansleep`，`:110`）。所以在 `active_low=1` 的线上：

```
echo high > direction    # PAD 变高
cat value                # 读回 0    ← 写"high"读回"0"，不是 bug
```

这是 legacy sysfs 最坑的一条，而**文档和源码注释都"没说错，但也没说透"**。两处措辞都把 `active_low` 的作用域严格限定在 `value` 上：

| 出处 | 逐字 |
|---|---|
| `drivers/gpio/gpiolib-sysfs.c:53-54`（文件头注释 `:39-58`） | `/active_low` → `* configures polarity of /value` |
| `Documentation/admin-guide/gpio/sysfs.rst:109-112` | `"active_low" … Write any nonzero value to invert the value attribute both for reading and writing.` |

**两处都只说 `value`，一个字没提 `direction`** —— 严格讲是准确的（`direction` 确实不受影响）。但同一份文档写 `direction` 时（`sysfs.rst:74-79`）说的是 "values \"low\" and \"high\" may be written to configure the GPIO as an output with that initial value"，**"that initial value" 是哪一层完全没交代**。读者按常理会以为整个目录是一套语义。

排查极性问题时**只信 `value`，不信 `direction`**。这条差异要写进 99 的交叉验证：文档严谨 ≠ 文档够用。

#### §5.2.2 `edge`：唯一会去申请中断的 setter

`edge_store()`（`:247`）不是简单置个标志位，它会真的走 `request_any_context_irq()`：

| 步骤 | 行 | 说明 |
|---|---|---|
| 解析字符串 | :254 | `sysfs_match_string(trigger_names, buf)`，只认 `none` / `falling` / `rising` / `both`；不匹配直接返回负值 |
| 相同则短路 | 上下文 | `flags == data->irq_flags` 时什么都不做 |
| 先拆旧的 | 上下文 | `data->irq_flags` 非 0 时先 `gpio_sysfs_free_irq()` |
| 再申请新的 | `gpio_sysfs_request_irq()` | `data->irq = gpiod_to_irq(desc)`（`:162`），失败返回 **`-EIO`**（`:164`） |
| 拿 kernfs 节点 | :165-168 | `sysfs_get_dirent(dev->kobj.sd, "value")` 失败返回 `-ENODEV`（`:168`） |
| **锁成中断** | :186 | `gpiochip_lock_as_irq()`，注意 `:179` 的 `FIXME` 说这一步本该在 `irq_request_resources` 里做 |
| 真正申请 | :190 | `request_any_context_irq(data->irq, gpio_sysfs_irq, irq_flags, "gpiolib", data)` |

三个 ：

- **`echo rising > edge` 返回 `-EIO` 的意思是"这根线没有中断"**（`gpiod_to_irq()` 失败），不是"IO 错误"。要区分"没 irqchip"还是"irqchip 没起来"看 §8.4。
- **`request_any_context_irq()` 用的 devname 是固定字符串 `"gpiolib"`**（`:190`）。所以 `/proc/interrupts` 里所有由 sysfs `edge` 申请的中断**名字全都叫 `gpiolib`，无法区分是哪根线**。要区分只能看中断号，再和 `gpiod_to_irq()` 的结果对。
- `irq_flags` 会根据 `FLAG_ACTIVE_LOW` **把 rising/falling 对调**（`:170-176`，两处 `test_bit(FLAG_ACTIVE_LOW, …)` 在 `:172` 和 `:175`）。也就是 `edge` 写的是**逻辑边沿**。改 `active_low` 时如果当前只监听单边沿，`gpio_sysfs_set_active_low()`（`:282`，`assign_bit` 在 `:292`）会自动"拆掉重申请"—— 这是唯一一个"写 A 文件会导致 B 文件的中断被重新申请"的地方。

这一条是官方文档**明确写了**的（`Documentation/admin-guide/gpio/sysfs.rst:112-115`：`Existing and subsequent poll(2) support configuration via the edge attribute for "rising" and "falling" edges will follow this setting.`），源码注释也写了（`gpiolib-sysfs.c:56-57`）。和 §5.2.1 那条对比：**同一份文档，`active_low` 对 `edge` 的影响交代得很清楚，对 `direction` 的影响一字未提** —— 后者恰恰是坑所在。

#### §5.2.3 属性的动态可见性

`gpio_is_visible()`（`:341-361`）在建目录时决定哪些文件出现：

| 属性 | 隐藏条件 | 行 |
|---|---|---|
| `direction` | `!data->direction_can_change` | :349-351 |
| `edge` | `gpiod_to_irq(desc) < 0`（这根线没中断） | :354-355 |
| `edge` | `!show_direction && test_bit(FLAG_IS_OUT, …)`（方向不可改且当前是输出） | :356-357 |

而 `direction_can_change` 在 `gpiod_export()` 里这样定（`:604-607`）：

```c
	if (chip->direction_input && chip->direction_output)
		data->direction_can_change = direction_may_change;
	else
		data->direction_can_change = false;
```

**`direction` 文件不存在 = 这个控制器没有同时提供 `direction_input` 和 `direction_output`**（不是"权限不够"）。从 `export` 走进来时 `direction_may_change` 传的是 `true`（`:471` 的 `gpiod_export(desc, true)`），所以从 sysfs 导出的线，`direction` 缺失一定是控制器能力问题，去查 05 §3/§4 那两个提供方骨架里这两个回调填没填。

另一个不在表里的细节：`gpiod_export()` 建目录时如果 `chip->names[offset]` 非空，**目录名用行名而不是 `gpioN`**（`:613-616`）：

```c
	dev = device_create_with_groups(&gpio_class, &gdev->dev,
					MKDEV(0, 0), data, gpio_groups,
					ioname ? ioname : "gpio%u",
					desc_to_gpio(desc));
```

所以 `echo 26 > export` 之后去找 `/sys/class/gpio/gpio26` 可能找不到，实际叫 `/sys/class/gpio/LedRed`（用 06 §2.5 的 jethub 例子）。**用 `ls /sys/class/gpio/` 看，不要拼路径。**

### §5.3 `/sys/class/gpio/gpiochipN/` 三个只读属性

`gpiochip_attrs[]`（`:415-420`，`ATTRIBUTE_GROUPS(gpiochip)` 在 `:421`）：

| 文件 | 实现 | 内容 |
|---|---|---|
| `base` | `base_show()` `:388`，`DEVICE_ATTR_RO` `:395` | `chip->base`，即这个 chip 的全局编号起点 |
| `label` | `label_show()` `:397`，`DEVICE_ATTR_RO` `:404` | `chip->label ?: ""` |
| `ngpio` | `ngpio_show()` `:406`，`DEVICE_ATTR_RO` `:413` | `chip->ngpio` |

**`base` + `ngpio` 是 `export` 需要的全局编号的权威来源**，而且不需要 debugfs（`CONFIG_DEBUG_FS` 关掉时仍可用）。`全局号 = base + offset`。

### §5.4 B 面：`/sys/bus/gpio/devices/`

`gpiolib.c:619` 把每个 `gpio_device` 的 `dev.bus` 设成 `gpio_bus_type`（`:61`），所以每个控制器在 `/sys/bus/gpio/devices/gpiochipN` 下有一个符号链接。

这个面**没有任何 gpio 特有的属性文件**，只有驱动模型的通用项（`uevent`、`subsystem`、`driver`…）。它的调试价值有两点：

1. `ls /sys/bus/gpio/devices/` 是**不依赖 debugfs 也不依赖 `CONFIG_GPIO_SYSFS`** 的控制器清单。
2. `readlink /sys/bus/gpio/devices/gpiochipN` 给出父设备的真实路径，直接印证 §4.4 表头里的 `parent:` 字段。

`/sys/bus/gpio/drivers/` 下永远只有一个 `gpio_stub_drv`（`gpiolib.c:4443-4447`）—— 04 §0 讲过这条总线是"退化总线"，没有真正的 driver 匹配。**看到 `driver` 链接指向 `gpio_stub_drv` 是正常的，不是配错了。**

---

## §6 tracepoint

### §6.1 全部只有两个事件

`include/trace/events/gpio.h` 全文 57 行，`TRACE_SYSTEM gpio`（`:3`），定义了**恰好两个** `TRACE_EVENT`：

| 事件 | 定义行 | `TP_PROTO` | 字段 | `TP_printk` |
|---|---|---|---|---|
| `gpio:gpio_direction` | :10 | `(unsigned gpio, int in, int err)` | `gpio` / `in` / `err`（`:17-19`） | `"%u %3s (%d)"`，`in ? "in" : "out"`（`:28-29`） |
| `gpio:gpio_value` | :32 | `(unsigned gpio, int get, int value)` | `gpio` / `get` / `value`（`:39-41`） | `"%u %3s %d"`，`get ? "get" : "set"`（`:50-51`） |

三条必须先知道的性质：

- **没有 `TP_CONDITION`。** 两个事件都是"到了就打"，不存在"某个条件下静默"的情况。所以 trace 为空 = 那条代码路径真的没走到，不需要怀疑过滤条件。
- **没有 `__string` / `__dynamic_array`。** 三个定长字段，没有名字、没有 label、**没有 chip 标识**。
- 重跑确认全树只有这两个：
  ```bash
  grep -c 'TRACE_EVENT' include/trace/events/gpio.h        # → 2
  grep -rn 'trace_gpio_' --include='*.c' --include='*.h' . # → 9 处，全在 drivers/gpio/gpiolib.c
  ```

### §6.2 九个调用点，全在 `gpiolib.c`

| 行 | 事件 | 所在函数 | 语义 |
|---|---|---|---|
| :2337 | `gpio_direction` | `gpiod_direction_input()`（:2297） | `in=1`，带 `ret` |
| :2381 | `gpio_value` | `gpiod_direction_output_raw_commit()`（:2343） | `get=0`，切输出时顺带写的那个初值 |
| :2382 | `gpio_direction` | 同上 | `in=0`，带 `ret` |
| :2656 | `gpio_value` | `gpiod_get_raw_value_commit()`（:2648） | `get=1` |
| :2776 | `gpio_value` | `gpiod_get_array_value_complex()`（:2679） | `get=1`，**数组里每根线一条** |
| :2911 | `gpio_direction` | `gpio_set_open_drain_value_commit()`（:2898） | 见 §6.4 |
| :2936 | `gpio_direction` | `gpio_set_open_source_value_commit()`（:2923） | 见 §6.4，记的是 `!value` |
| :2948 | `gpio_value` | `gpiod_set_raw_value_commit()`（:2943） | `get=0` |
| :3052 | `gpio_value` | `gpiod_set_array_value_complex()`（:2976） | `get=0`，数组里每根线一条 |

### §6.3 两个必须记住的坐标系错位

**第一个：`gpio` 字段是全局编号。** 九个调用点全都写成 `trace_gpio_*(desc_to_gpio(desc), …)`。而 cdev（`/dev/gpiochipN` + offset）、debugfs 的每行（虽然打全局号但表头给区间）、`gpioinfo` 全都用 `(chip, offset)`。

**看 trace 之前先把全局号换算表抄下来**：`cat /sys/kernel/debug/gpio | grep GPIOs`（§4.4 表头），或者 `for d in /sys/class/gpio/gpiochip*; do echo "$d $(cat $d/base) $(cat $d/ngpio)"; done`（§5.3）。

**第二个：记录的是 raw 值，不是逻辑值。** 九个调用点全部落在函数名带 `_commit` 或 `_complex` 的函数里 —— 也就是 **`active_low` 反转之后、真正写寄存器之前**的那一层（03 §A5 讲的四层值模型里的第 3 层）。

所以：`gpiod_set_value(desc, 1)` 在一根 `GPIO_ACTIVE_LOW` 的线上，trace 里会看到 **`gpio_value: <n> set 0`**。这不是 bug，这是设计。**trace 是验证"逻辑层反转有没有正确发生"的最佳工具** —— 上层调用参数（你自己传的）和 trace 里的值应当在 active-low 线上相反、在 active-high 线上相同。对不上就说明 `FLAG_ACTIVE_LOW` 没被正确设置（回去查 06 §5.1 的 quirk）。

### §6.4 open-drain / open-source 的"写值"在 trace 里表现为"改方向"

`gpio_set_open_drain_value_commit()`（`:2898`）和 `gpio_set_open_source_value_commit()`（`:2923`）发的是 **`gpio_direction`**，不是 `gpio_value`：

| 场景 | 实际动作 | trace 里出现的 |
|---|---|---|
| open-drain 线写 0 | 切成输出并驱动低 | `gpio_direction: <n> out (<err>)`（`:2911`，`value` 作为 `in` 参数） |
| open-drain 线写 1 | 切成**输入**（靠外部上拉浮到高） | `gpio_direction: <n> in (<err>)` |
| open-source 线写 1 | 切成输出并驱动高 | `gpio_direction`，`:2936` 传的是 **`!value`** |
| open-source 线写 0 | 切成输入 | 同上 |

**在 open-drain 线上 `gpio:gpio_value` 一条都不会有。** 只 enable `gpio_value` 会看到"完全没有写操作"的假象。排查 open-drain 线（I²C 的 SDA/SCL bit-bang、复位脚）时**必须同时开两个事件**。

`:2936` 传 `!value` 是 open-source 的语义所致（这一行如果按 `gpio_direction` 的字面意思读会得到反的结论）。看 trace 时对 open-source 线把 `in`/`out` 反过来理解。

### §6.5 开启命令

```bash
cd /sys/kernel/tracing 2>/dev/null || cd /sys/kernel/debug/tracing

# 两个都开（理由见 §6.4）
echo 1 > events/gpio/gpio_value/enable
echo 1 > events/gpio/gpio_direction/enable

# 只关心某几根线：用全局编号过滤（换算见 §6.3）
echo 'gpio == 26 || gpio == 27' > events/gpio/gpio_value/filter
echo 'gpio == 26 || gpio == 27' > events/gpio/gpio_direction/filter

# 只想看失败的方向切换
echo 'err != 0' > events/gpio/gpio_direction/filter

cat trace_pipe
```

过滤字段名就是 §6.1 表里的那三个（`gpio` / `in` / `err`，`gpio` / `get` / `value`），因为没有 `__string`，**不能按 label 或 consumer 名过滤**。想按消费者定位只能先用 §4 的 debugfs 查出全局号，再回来写过滤器。

---

## §7 `WARN` / `BUG` 清单

WARN 会带栈回溯，在 dmesg 里最显眼，但触发条件常常不明显。按语义分组（普查命令见本节末）。

### §7.1 `can_sleep` 误用 —— 8 处，同一个原因

| 行 | 所在函数 | 条件 |
|---|---|---|
| :2696 | `gpiod_get_array_value_complex()` | `if (!can_sleep) WARN_ON(array_info->chip->can_sleep)` |
| :2742 | 同上（逐 chip 收集分支） | `if (!can_sleep) WARN_ON(gc->can_sleep)` |
| :2806 | `gpiod_get_raw_value()` | `WARN_ON(desc->gdev->chip->can_sleep)` |
| :2827 | `gpiod_get_value()` | 同 |
| :2993 | `gpiod_set_array_value_complex()` | `if (!can_sleep) WARN_ON(array_info->chip->can_sleep)` |
| :3036 | 同上（逐 chip 分支） | `if (!can_sleep) WARN_ON(gc->can_sleep)` |
| :3100 | `gpiod_set_raw_value()` | `WARN_ON(desc->gdev->chip->can_sleep)` |
| :3141 | `gpiod_set_value()` | 同 |

全部一个意思：**你在一个 `can_sleep` 控制器上调了不带 `_cansleep` 后缀的 API**。典型场景是 I²C/SPI GPIO 扩展芯片（`gc->can_sleep = true`），消费者却按片上 GPIO 的习惯写了 `gpiod_set_value()`。

关键：**这 8 处只 WARN，不阻止执行**。函数照样往下走，在原子上下文里就会真的睡眠 → 后面才爆出 `scheduling while atomic` 或者直接死机。**看到这 8 条 WARN 就必须改代码**，别当成噪声。哪个控制器 `can_sleep` 一眼可查：§4.4 表头的 `, can sleep` 字样。

### §7.2 irqchip 配置矛盾

| 行 | 条件 | 后果 |
|---|---|---|
| :1110 | `WARN_ON(*type == IRQ_TYPE_NONE)`，在 `gpiochip_hierarchy_irq_domain_translate()` | 层级 domain 翻译出的触发类型是 NONE |
| :1135 | `WARN_ON(nr_irqs != 1)`，在 `gpiochip_hierarchy_irq_domain_alloc()` | 一次分配多于 1 个 irq |
| :1506 | `if (WARN_ON(gc->irq.irq_enable)) return;` | 驱动自己填了 `gc->irq.irq_enable`。**直接 `return`，irqchip 的 hook 安装被跳过** |
| :1567 | `WARN(fwnode && type != IRQ_TYPE_NONE, "%pfw: Ignoring %u default trigger\n", …)` | 同时给了 fwnode 和 `default_type`；核心把 type 重置成 `IRQ_TYPE_NONE`（`:1569`） |

`:1506` 是这一组里唯一**会改变控制流**的（其余只是吼一声）。看到它说明这个 irqchip 的 mask/unmask 包装没装上，中断行为会不对 —— 去 05 §5/§6 对照该填哪些字段。

`:1567` 用的是 `WARN(...)` 的返回值做条件，所以**它既打栈回溯又执行修正**。日志里 `%pfw` 会打出 fwnode 的完整路径（DT 节点名），可以直接对到 DTS 里那一行。

### §7.3 `FLAG_USED_AS_IRQ` 一致性 —— 注意 `!WARN_ON(…)` 的写法

`gpiochip_disable_irq()` 全文（`gpiolib.c:3354-3361`），注意 `!WARN_ON(…)` 这个写法：

```c
void gpiochip_disable_irq(struct gpio_chip *gc, unsigned int offset)
{
	struct gpio_desc *desc = gpiochip_get_desc(gc, offset);

	if (!IS_ERR(desc) &&
	    !WARN_ON(!test_bit(FLAG_USED_AS_IRQ, &desc->flags)))
		clear_bit(FLAG_IRQ_IS_ENABLED, &desc->flags);
}
```

| 行 | 所在函数 | 语义 |
|---|---|---|
| :3359 | `gpiochip_disable_irq()` | 这根线没有 `FLAG_USED_AS_IRQ` 就 WARN，**并且跳过 `clear_bit`** |
| :3369 | `gpiochip_enable_irq()` | 同，跳过后面整块 |
| :3374 | `gpiochip_enable_irq()` 内层 | `WARN_ON(test_bit(FLAG_IS_OUT, …) && !test_bit(FLAG_OPEN_DRAIN, …))` —— 输出方向的线被当中断用，且不是 open-drain |

前两条写成 `!WARN_ON(cond)`，意思是 **"cond 为真时 WARN 且不执行后续"**。所以这两个 WARN **会改变控制流**，`FLAG_IRQ_IS_ENABLED` 不会被更新 → 中断使能状态和硬件脱节。

`:3374-3375` 的条件对应它上面那段注释（`:3370-3373`）：

```c
		/*
		 * We must not be output when using IRQ UNLESS we are
		 * open drain.
		 */
```

open-drain 线可以既是输出又是中断源（I²C 中断线的标准形态），其他情况不行。

### §7.4 其余

| 文件:行 | 内容 | 说明 |
|---|---|---|
| `gpiolib.c:2090` | `WARN_ON(extra_checks)` | 只在 `CONFIG_DEBUG_GPIO=y` 时吼。`gpiod_free()` 拿到 NULL / 无 gdev 的 desc |
| `gpiolib.c:4479` | `WARN_ON(of_reconfig_notifier_register(&gpio_of_notifier))` | DT overlay 通知链注册失败；一次性，启动期 |
| `gpiolib-of.c:778` | `WARN_ON(1)` | `of_gpio_simple_xlate()` 里 `of_gpio_n_cells < 2`，见 06 §2.3 |
| `gpiolib-of.c:782` | `if (WARN_ON(gpiospec->args_count < gc->of_gpio_n_cells))` | DTS 给的 cell 数不够，见 06 §2.3 |
| `gpiolib-cdev.c:627` | `WARN(1, "duplicate line inserted")` | 同一个 `linereq` 里出现重复 offset |
| `gpiolib-devres.c:327`、`:358`、`:373` | `WARN_ON(devres_release(…))` | `devm_gpiod_put*()` 传了一个不是 devm 分配的 desc，见 04 §4 |

普查命令：

```bash
grep -n 'WARN_ON\|WARN(\|WARN_ONCE\|BUG_ON\|BUG(' \
  drivers/gpio/gpiolib.c drivers/gpio/gpiolib-of.c drivers/gpio/gpiolib-cdev.c \
  drivers/gpio/gpiolib-devres.c drivers/gpio/gpiolib-sysfs.c drivers/gpio/gpiolib-legacy.c \
  drivers/gpio/gpio-mmio.c
```

结果里**一个 `BUG_ON` / `BUG()` 都没有，也没有 `WARN_ONCE`**。gpio 核心的所有断言都是可重复触发的 WARN —— 好处是不会一条日志之后就静默，坏处是循环里踩到会刷屏（尤其 §7.1 那 8 条在 bit-bang 路径上）。

---

## §8 错误码分诊

用法：拿到一个负返回值，先在本节找到那个码，再按语义组缩小到具体函数。**同一个码在同一个文件里出现十几次是常态**，所以下面全部按"这个码在这里表示什么"分组，而不是按行号罗列。

### §8.0 先看一眼总量

| 错误码 | `gpiolib.c` 里的出现次数 | 语义组数 | 小节 |
|---|---:|---:|---|
| `-EINVAL` | 22 | **9** | §8.1 |
| `-EIO` | 8 | 2 | §8.2 |
| `-ENOTSUPP` (524) | 7 | 3 | §8.3 |
| `-EPROBE_DEFER` (517) | 6 | 2 | §8.5 |
| `-ENXIO` | 4 | 1 | §8.4 |
| `-EBUSY` | 3 | 3 | §8.6 |
| `-ENODEV` | 2 | 2 | §8.7 |
| `-ENOSPC` | **1** | 1 | §8.8 |
| `-ENODATA` | 1 | 1 | §8.9 |
| `-EEXIST` | 1 | 1 | §8.9 |

重跑：

```bash
for e in EINVAL EIO ENOTSUPP EPROBE_DEFER ENXIO EBUSY ENODEV ENOSPC ENODATA EEXIST; do
    printf '%-16s %s\n' "$e" "$(grep -c -- "-$e" drivers/gpio/gpiolib.c)"
done
```

上表的"出现次数"是文本计数，**包含 kerneldoc 注释里承诺的返回值**（`-ENOTSUPP` 的 7 处里有 2 处是注释，见 §8.3）。语义组数才是排查时真正要看的。

### §8.1 `-EINVAL`（22 处 / 9 组）—— 最模糊的码，必须靠上下文

| 组 | 行 | 判定条件 | 有日志吗 |
|---|---|---|---|
| ① hwnum 越界 | :148 | `gpiochip_get_desc()` 里 `hwnum >= gdev->ngpio` | 无 |
| ② `ngpio == 0` | :675 | 注册时控制器声明 0 根线 | 有：`chip_err(gc, "tried to insert a GPIO chip with zero lines\n")`（:674） |
| ③ irqdomain 数据不合法 | :1113 / :1218 / :1289 / :1557 / :1592 / :1696 | 层级 domain 翻译、alloc、stub、`gpiochip_irqchip_add_*` 参数校验 | 无 |
| ④ 线被 mask 掉 | :1952 | `gpiod_request_commit()` 里 `!gpiochip_line_is_valid()` | 无（见下） |
| ⑤ desc 没有 gdev | :1992 | `validate_desc()` | 有：`pr_warn("%s: invalid GPIO (no device)\n", func)` |
| ⑥ **`!desc_array`（8 处，同一语义）** | :2860 / :2886 / :3165 / :3190 / :3504 / :3530 / :3592 / :3634 | 所有 `*_array_value*()` 家族的入口 NULL 检查 | 无 |
| ⑦ `gpiod_to_irq()` desc 无效 | :3248 | | 无 |
| ⑧ 板级查找表 hwnum 越界 | :3792 | `gpiod_find()` 里 `p->chip_hwnum >= gc->ngpio` | 无 |
| ⑨ 多个 pull 同时开 | :4040 | `gpiod_configure_flags()` | 有：`gpiod_err(desc, "multiple pull-up, pull-down or pull-disable enabled, invalid configuration\n")` |

三条实操结论：

- **`-EINVAL` 有 8/22 是同一件事：数组 API 收到了 NULL 数组**（组⑥）。写 `gpiod_get_array_value()` 之类的调用时忘了检查 `gpiod_get_array()` 的返回，就落在这里。**这 8 处一条日志都没有**，只能靠调用点自己打。
- **组④是最难查的一个**：`gpiod_request()` 返回 `-EINVAL` 且 dmesg 全空，几乎一定是 DTS 里的 `gpio-reserved-ranges` 把这根线抠掉了。验证办法在 §5.1（`echo N > export` 会给出 `GPIO %ld masked` 的 `pr_warn`，那是唯一会说话的入口）。
- 只有 3 组（②⑤⑨）自带日志，其中②在启动期、⑨在 `gpiod_get()` 期。**看到 dmesg 里有话说，就直接按那句话查；一句话都没有，优先怀疑组④和组⑥。**

### §8.2 `-EIO`（8 处 / 2 组）

**组 A：控制器回调缺失（6 处，全部带日志）**

| 行 | 日志 | 触发的调用 |
|---|---|---|
| :2314 | `gpiod_warn(desc, "%s: missing get() but have direction_input()\n", __func__)` | `gpiod_direction_input()` |
| :2330 | `gpiod_warn(desc, "%s: missing direction_input() operation and line is output\n", __func__)` | 同上 |
| :2358 | `gpiod_warn(desc, "%s: missing set() and direction_output() operations\n", __func__)` | `gpiod_direction_output_raw_commit()` |
| :2370 | `gpiod_warn(desc, "%s: missing direction_output() operation\n", __func__)` | 同上 |
| :2623 | 无（三元表达式 `gc->get ? … : -EIO`） | `gpio_chip_get_value()` |
| :2676 | 无 | `gpio_chip_get_multiple()`，`gc->get_multiple` 为 NULL 且需要它时 |

**前 4 条日志会点名函数（`__func__`）**，这是 gpio 核心里最友好的一组错误。看到就直接去补那个回调（05 §3/§4 的提供方骨架列了哪些必填）。

但 `:2623` **没有日志**，而它是 `gpiod_get_raw_value()` 路径上的。所以"读电平返回 `-EIO` 但 dmesg 无输出" = `gc->get` 是 NULL（纯输出型控制器）。这也是 §4.3 里 debugfs 电平列打 `?` 的最常见原因。

**组 B：线已被当中断占用（2 处，都带日志）**

| 行 | 日志 |
|---|---|
| :2432 | `gpiod_err(desc, "%s: tried to set a GPIO tied to an IRQ as output\n", __func__)` |
| :3311 | `chip_err(gc, "%s: tried to flag a GPIO set as output for IRQ\n", __func__)` |

注意这两条的**前缀族不同**（§2.1）：`:2432` 是 `gpiod_err` → `pr_err` → 日志里是 `gpio-<全局号> (<label>):`；`:3311` 是 `chip_err` → `dev_err` → 日志里是 `gpiochipN: (<chip label>):`。同一个冲突从两个方向报出来，日志长得不一样。

另外 `edge_store` 也会返回 `-EIO`（`gpiolib-sysfs.c:164`），但那是"没有中断"的意思，和这组无关 —— 见 §5.2.2。**同一个 `-EIO` 在核心里是"回调缺失/线冲突"，在 sysfs 里是"没有中断"。**

### §8.3 `-ENOTSUPP` (524) vs `-EOPNOTSUPP` (95) —— 这个区分是有意的

`include/linux/errno.h:8-13` 的注释逐字：

```
/*
 * These should never be seen by user programs.  To return one of ERESTART*
 * codes, signal_pending() MUST be set.  Note that ptrace can observe these
 * at syscall exit tracing, but they will never be left for the debugged ones.
 */
```

`ENOTSUPP` 定义在 `:27`（值 524），和 `EPROBE_DEFER`（`:19`，517）同属这段"用户态永远不该看见"的内核内部码。

`-ENOTSUPP` 在 `gpiolib.c` 的 7 处：

| 组 | 行 | 语义 |
|---|---|---|
| ① 方向不可知 | :232 | `gpiod_get_direction()`：控制器没有 `get_direction` 回调 |
| ② `set_config` 不支持 | :2199 / :2224 | `gpio_do_set_config()` / 上层包装：控制器没有 `set_config`，或该项配置不认 |
| ③ 硬件时间戳 | :2495 / :2524 | `gpiod_enable/disable_hw_timestamp_ns()`：没有对应回调 |
| （注释） | :2541 / :2561 | kerneldoc 里承诺的返回值，不是实际 `return` |

而 cdev **面向用户态的校验用 POSIX 的 `-EOPNOTSUPP`**：`gpiolib-cdev.c:1281`（`gpio_v2_line_flags_validate()` 里请求 HTE 时钟但 `!CONFIG_HTE`）。`gpio-regmap.c:121` / `:152` 同样用 `-EOPNOTSUPP`。

**`-ENOTSUPP` 泄漏到用户态是 bug 的信号。** 用户态看到 `errno=524`（`strerror` 会打 `Unknown error 524`）说明某条路径漏了转换。反之 `errno=95 Operation not supported` 是正常的、设计好的拒绝。

**cdev 把 `-ENOTSUPP` 当作"退化"信号，不当错误。** `debounce_setup()`（`gpiolib-cdev.c:1059`）：

```c
	ret = gpiod_set_debounce(line->desc, debounce_period_us);
	if (ret) {
		/* debounce not supported, use software debouncer */
		if (ret != -ENOTSUPP)
			return ret;
```

（`gpiod_set_debounce` 在 `:1064`，`if (ret != -ENOTSUPP) return ret;` 在 `:1070-1071`。）

这条的调试含义：**设了 `debounce-period-us` 之后去抖生效了，不代表硬件在去抖**。要区分硬件去抖还是软件去抖，看控制器有没有 `set_config`（`grep -n 'set_config' drivers/gpio/gpio-<你的驱动>.c`）—— 没有就是软件在做，会多占一个 timer 且精度差。

### §8.4 `-ENXIO`（4 处 / 1 组）—— 「这根线没有中断」

| 行 | 所在函数 |
|---|---|
| :1316 | `gpiochip_irq_map()` |
| :1424 | `gpiochip_to_irq()` |
| :3257 / :3271 | `gpiod_to_irq()` |

全部一个意思：**这个控制器/这根线没有 irq domain，或者 offset 不在 irqchip 的有效范围内**。

和 §8.5 的 `-EPROBE_DEFER` 是同一路径上的兄弟：`gpiochip_to_irq()` 在 `:1420` 返回 `-EPROBE_DEFER`（domain 还没建好），在 `:1424` 返回 `-ENXIO`（**永远不会有**）。**看到 `-ENXIO` 就别等了，去查 irqchip 有没有注册；看到 `-EPROBE_DEFER` 就是时序问题，会自愈。** 两者的区分是 gpio irqchip 排查里最省时间的一条。

### §8.5 `-EPROBE_DEFER`（6 处 / 2 组）—— 会自愈，别急着改代码

| 组 | 行 | 语义 |
|---|---|---|
| ① 控制器还没注册 | :846、:3769、:3784 | `gpiod_find()` 板级查找 / `gpiochip_find_*` 找不到目标 chip |
| ② irq domain 还没建 | :1420、:3268 | `gpiochip_to_irq()` / `gpiod_to_irq()` |
| （初值） | :2016 | `gpiod_request()` 里 `ret` 的初始化值 |

**`-EPROBE_DEFER` 在启动期出现是正常的**，驱动核心会把 probe 排到后面重试。判断"是否真的有问题"的唯一标准是**启动完成后还在不在**：

```bash
# 有没有驱动最终没 probe 成功
cat /sys/kernel/debug/devices_deferred
```

这个文件里出现你的消费者设备 = 依赖的 gpio 控制器**从来没注册成功**（去 §2.4 的注册期日志表查），不是时序问题。

### §8.6 `-EBUSY`（3 处 / 3 组）

| 行 | 所在函数 | 语义 |
|---|---|---|
| :292 | `gpiodev_add_to_list()` | 两个控制器的**全局编号区间重叠** |
| :1941 | `gpiod_request_commit()` | 这根线已经被别人 request 了 |
| :4127 | `gpiod_get_index()` | 不是返回值，是**特判**：`!(ret == -EBUSY && flags & GPIOD_FLAGS_BIT_NONEXCLUSIVE)` |

`:292` 极隐蔽：多个控制器都硬编码了 `gc->base` 且区间撞了。日常在 DT 平台上不会遇到（`base = -1` 走 §8.8 的自动分配），但用板级代码或某些老驱动时会。**注册期返回 `-EBUSY` 而不是 `-EEXIST`，容易被当成"线被占"去查消费者，方向就错了。**

`:4127` 是唯一一条"`-EBUSY` 被当成成功"的路径：带 `GPIOD_FLAGS_BIT_NONEXCLUSIVE` 时多个消费者可以共享同一根线（04 §4 讲了这个 flag 的用途）。**调试共享 reset 脚时，`-EBUSY` 消失不代表冲突解决了，可能只是有人加了 NONEXCLUSIVE。**

### §8.7 `-ENODEV`（2 处）

| 行 | 语义 |
|---|---|
| :3395 | `gpiochip_reqres_irq()`：request 这根线失败（被 irqchip 侧调用） |
| :3852 | `fwnode_get_named_gpiod()` 的 `ret` 初值 |

另有 `gpiolib-sysfs.c` 三处：`gpiod_export()` 的 `:579`（控制器正在拆）、`gpio_sysfs_request_irq()` 的 `:168`（拿不到 `value` 的 kernfs 节点）、`gpiod_export_link()` 的 `:666`。

### §8.8 `-ENOSPC`（全文件唯一 1 处）

`gpiolib.c:202`，在 `gpiochip_find_base()` 里。

**含义唯一且明确：全局 GPIO 编号空间用完了。** 编号空间上限由 `ARCH_NR_GPIOS` 决定。这是一个"整个 gpiolib 里只有一个地方会返回"的码 —— 看到 `-ENOSPC` 从 gpio 注册路径出来，不需要任何进一步分诊。

重跑确认唯一性：

```bash
grep -n -- '-ENOSPC' drivers/gpio/*.c        # → 只有 gpiolib.c:202
```

这也是 08 §5 要讲的"全局编号是个错误的抽象"的最直接证据：一个纯粹由 legacy 编号方案引入的失败模式，cdev 路径上根本不存在。

### §8.9 一次性的两个

| 码 | 行 | 语义 | |
|---|---|---|---|
| `-ENODATA` | :659 | `gpiochip_add_data_with_key()` 里读 `ngpios` 属性没找到 | **这是正常分支，不是错误** —— 大多数控制器在代码里写死 `ngpio`，不用 DT 属性。这个码不会传出去 |
| `-EEXIST` | :1178 | **不是返回值，是"被吞掉"的码** | 见下 |

`-EEXIST` 在 `gpiolib.c` 里唯一的一处（`:1172-1179`）是**把它当成功**：

```c
	ret = irq_domain_alloc_irqs_parent(d, irq, 1, &gpio_parent_fwspec);
	/*
	 * If the parent irqdomain is msi, the interrupts have already
	 * been allocated, so the EEXIST is good.
	 */
	if (irq_domain_is_msi(d->parent) && (ret == -EEXIST))
		ret = 0;
```

所以父 domain 是 MSI 时，`-EEXIST` 被静默转成 0。**排查层级 irqdomain 时不要指望看到 `-EEXIST`。** 这条路径失败会走 `:1181-1183` 的 `chip_err(gc, "failed to allocate parent hwirq %d for hwirq %lu\n", …)`，那是真正会说话的地方。另外 `:1170-1171` 有一条 `chip_dbg(gc, "alloc_irqs_parent for %d parent hwirq %d\n", …)`，属于 §3.2 那 30 条里可以打开的（层级 domain 排查必开）。

### §8.10 分诊速查

| 拿到的码 | 第一步查 |
|---|---|
| `-EINVAL` + dmesg 无输出 | 数组 API 传了 NULL（§8.1 组⑥），或 `gpio-reserved-ranges`（§8.1 组④，用 §5.1 的 export 验） |
| `-EINVAL` + dmesg 有话 | 直接按那句话（②⑤⑨） |
| `-EIO` 从核心出来 | 回调缺失（§8.2 组 A），四条带 `__func__` 的日志直接指路 |
| `-EIO` 从 sysfs `edge` 出来 | 这根线没中断（§5.2.2） |
| `-ENXIO` | irqchip 没注册，**不会自愈**（§8.4） |
| `-EPROBE_DEFER` | 看 `/sys/kernel/debug/devices_deferred`，启动后还在才是问题（§8.5） |
| `-EBUSY` 在 request 期 | 线被占，用 §4.2 的 debugfs 看是谁 |
| `-EBUSY` 在注册期 | 编号区间重叠（§8.6 `:292`） |
| `-ENOSPC` | 编号空间耗尽，全树唯一路径（§8.8） |
| `errno=524` 到了用户态 | 内核 bug，`-ENOTSUPP` 漏转换（§8.3） |
| `errno=95` | 正常拒绝，功能真的不支持（§8.3） |

---

## §9 虚拟 / 测试提供方

不用改硬件就能造出 gpio 控制器来验证消费者代码、验证 DTS、验证脚本。三个可用（`drivers/gpio/Kconfig` 的 `menu` 尾部，`endmenu` 在 `:1713`）：

| 驱动 | Kconfig 行 | 行数 | 配置方式 | 能造中断？ | 定位 |
|---|---|---|---|---|---|
| `gpio-sim` | `:1705` | 1613 | **configfs** | 是（`select IRQ_SIM`） | 首选，现代替代品 |
| `gpio-mockup` | `:1685` | 614 | module_param + debugfs | 是（`select IRQ_SIM`） | 老的，selftests 还在用 |
| `gpio-aggregator` | `:1673` | 550 | sysfs（platform driver） | 转发真实中断 | 把**真实**的几根线聚成一个虚拟 chip |

`drivers/gpio/gpio-virtuser.c` **在 6.1 里不存在**（它是后来才加的）。看到网上教程提到它，不适用于本版本。

### §9.1 `gpio-sim`（首选）

configfs 层级逐字取自 `Documentation/admin-guide/gpio/gpio-sim.rst:20-70`：

```
/sys/kernel/config/gpio-sim/
└── <gpio-device>/          # 一个虚拟设备
    ├── dev_name            # 只读：分配到的 platform 设备名（:27-31）
    ├── live                # 写 1 激活 / 写 0 停用（:33-35）
    └── <gpio-bankX>/       # 一个 chip
        ├── chip_name       # 只读：分配到的 gpiochipN（:39-43）
        ├── num_lines       # 线数（:45-47）
        └── <lineY>/
            ├── name        # 行名，对应 gc->names[]（:51-54）
            └── hog/
                ├── name      # 内核内 consumer 名（:57-60）
                └── direction # input / output-high / output-low（:61-65）
```

（脚本里的根路径是 `CONFIGFS_DIR="/sys/kernel/config/gpio-sim"`，见 `tools/testing/selftests/gpio/gpio-sim.sh:6`。）

**`live` 是分水岭：写 1 之前所有属性可改，写 1 之后 `chip_name` / `dev_name` 才有值。** selftests 里 `live` 的读写在 `gpio-sim.sh:93` / `:99`，`num_lines` 在 `:75`，`label` 在 `:67`，`chip_name` 在 `:113`，`dev_name` 在 `:120`。

**`hog` 是 gpio-sim 独有的能力：让内核内部先把某根线 request 掉**（`gpio-sim.sh:33-34`）。这是**唯一一种不写驱动就能复现 `-EBUSY`**、验证消费者的错误处理路径的办法。

激活之后每根线在 sysfs 上有两个 0644 属性（`gpio-sim.c:346-349`）：

| 属性 | show / store | 取值 |
|---|---|---|
| `value` | `gpio_sim_sysfs_val_show()` `:222` | 读到的当前电平 |
| `pull` | `_show` `:252` / `_store` `:267` | 字符串 **`pull-down`** / **`pull-up`**（`gpio_sim_sysfs_pull_strings[]` 在 `:247-250`） |

**`pull` 是"外部世界"的模拟**，写它相当于焊了个上拉/下拉电阻。`gpio_sim_apply_pull()` 在 `:60`，映射表 `pull_map` 在 `:43`。**这是验证"输入线读到的值对不对"的注入点** —— 写 `pull` 然后从消费者侧读，就能端到端验证 §5.2.1 的 active_low 反转。

`GPIO_SIM_NUM_ATTRS 3 /* value, pull and sentinel */`（`:35`）—— 每根线**只有** value 和 pull 两个属性，没有 direction、没有 edge。想造边沿只能改 `pull`。

### §9.2 `gpio-mockup`（selftests 在用，但接口更粗）

| 项 | 位置 | 说明 |
|---|---|---|
| 区间参数 | `module_param_array(gpio_mockup_ranges, int, &gpio_mockup_num_ranges, 0400)`（`:64`） | 成对给 `<base>,<ngpio>`；`-1` 让核心自动分配 base |
| 行名开关 | `module_param_named(gpio_mockup_named_lines, …, bool, 0400)`（`:67-68`） | 打开后每根线有名字 |
| 上限 | `GPIO_MOCKUP_MAX_GC 10`（`:28`）、`GPIO_MOCKUP_MAX_RANGES`（`:33`）、`GPIO_MOCKUP_MAX_PROP 5`（`:35`） | 最多 10 个 chip |
| debugfs 根 | `debugfs_create_dir("gpio-mockup", NULL)`（`:578`） | `/sys/kernel/debug/gpio-mockup/` |
| 每 chip 目录 | `chip->dbg_dir = debugfs_create_dir(devname, gpio_mockup_dbg_dir)`（`:356`） | 目录名是 gpiochipN |
| **每行文件** | `debugfs_create_file(name, 0600, chip->dbg_dir, priv, …)`（`:371`） | **0600，可写** |

**两个模块参数都是 `0400`（只读）**，只能在 `modprobe` 时给：

```bash
modprobe gpio-mockup gpio_mockup_ranges=-1,4
# 上面这行的形式取自 tools/testing/selftests/gpio/gpio-mockup.sh:79 / :294
```

**和 gpio-sim 的关键差别：mockup 的注入点是 debugfs 0600 文件，不是 sysfs `pull`。** 每根线在 `/sys/kernel/debug/gpio-mockup/gpiochipN/<line>` 下，读写都是电平。selftests 里定位这个目录的写法（`gpio-mockup.sh:51-54`）：

```bash
DEBUGFS=$(grep -w debugfs /proc/mounts | cut -f2 -d' ')
GPIO_DEBUGFS=$DEBUGFS/$module
# 注释里给的例子：# e.g. /sys/kernel/debug/gpio-mockup/gpiochip1   (:296)
```

**注意别和 §4 的 `/sys/kernel/debug/gpio` 搞混**：那是全局只读清单（0444），这是 mockup 自己的可写注入面（0600）。同在 debugfs 下，来源和权限完全不同。

### §9.3 `gpio-aggregator`（聚合真实线）

用途和上面两个不一样：**它不造假线，它把已有的真实线重新打包成一个新 chip**，从而可以把某几根线单独交给一个受限的用户/容器。

| 操作 | 路径 | 文档行 |
|---|---|---|
| 驱动目录 | `/sys/bus/platform/drivers/gpio-aggregator/` | `gpio-aggregator.rst:28` |
| 新建 | `new_device` | `:30` |
| 删除 | `delete_device` | `:59` |
| 解绑/重绑 | `driver_override` + `bind` | `:81` / `:101` / `:102` |

文档里的真实示例（`gpio-aggregator.rst:57`，Renesas R-Car，和 06 §2.5 的 `r8a77960` 例子同源）：

```
$ echo 'e6052000.gpio 19 e6050000.gpio 20-21' > new_device
```

删除（`:69`）：

```
$ echo gpio-aggregator.0 > delete_device
```

**调试价值：它是唯一能"给一根线换个全局编号 / 换个 chip"的手段**，用来验证消费者代码有没有硬编码编号。聚合后原来的线仍被 aggregator request 着，所以在 §4.2 的 debugfs 清单里会看到 consumer 变成 aggregator。

### §9.4 selftests

`tools/testing/selftests/gpio/` 全部内容：

| 文件 | 用途 |
|---|---|
| `gpio-mockup.sh` | 主脚本，跑 mockup 的功能矩阵 |
| `gpio-mockup-sysfs.sh` | 验 §5 的 legacy sysfs 面 |
| `gpio-sim.sh` | 跑 gpio-sim 的 configfs 矩阵 |
| `gpio-mockup-cdev.c` | 用 cdev ioctl 操作 mockup 线的小工具 |
| `gpio-chip-info.c` | 打印 `GPIO_GET_CHIPINFO_IOCTL` 的结果 |
| `gpio-line-name.c` | 验行名 |
| `Makefile` / `config` | `config` 里列了跑测试需要哪些 Kconfig |

跑法：

```bash
make -C tools/testing/selftests/gpio            # 编 3 个 .c
bash tools/testing/selftests/gpio/gpio-sim.sh   # 需 root + CONFIG_GPIO_SIM=y
```

**`config` 文件是"要打开哪些 Kconfig"的权威清单**，比自己推断可靠。改内核配置前先 `cat` 它。

---

## §10 用户态工具

### §10.1 in-tree 四件套：`tools/gpio/`

编译：

```bash
make -C tools/gpio        # 产物：lsgpio、gpio-hammer、gpio-event-mon、gpio-watch
```

（目录下还有 `Build`、`Makefile`、`gpio-utils.c`、`gpio-utils.h`。）

| 工具 | 用途 | usage 出处 |
|---|---|---|
| `lsgpio` | 列出所有 chip 和每根线的名字/consumer/flags | `tools/gpio/lsgpio.c:169-176` |
| `gpio-hammer` | 反复翻转指定的几根输出线 | `tools/gpio/gpio-hammer.c:113-125` |
| `gpio-event-mon` | 监听边沿事件，最有用的一个 | `tools/gpio/gpio-event-mon.c:142-161` |
| `gpio-watch` | 监听**行信息变更**事件（谁 request/release 了） | **没有 usage 函数** |

`lsgpio` 的 usage 逐字（`tools/gpio/lsgpio.c:169-176`）：

```
Usage: lsgpio [options]...
List GPIO chips, lines and states
  -n <name>  List GPIOs on a named device
  -?         This helptext
```

**`lsgpio` 无参数时会遍历 `/dev/gpiochip*`**（内部 `asprintf(&chrdev_name, "/dev/%s", device_name)`，`:109`）。输出格式来自这几个 `fprintf`：

| 行 | 格式 |
|---|---|
| :127 | `"GPIO chip: %s, \"%s\", %u GPIO lines\n"` —— 设备名、label、线数 |
| :143 | `"\tline %2d:"` |
| :145 / :147 | 有名字打 `" \"%s\""`，没名字打 `" unnamed"` |
| :149 / :151 | 有 consumer 打 `" \"%s\""`，没有打 `" unused"` |
| :153 / :155 | flags 用 `" ["` 和 `"]"` 包起来 |

**`lsgpio` 用 offset（`line %2d`），不是全局编号。** 和 §6 的 trace、§5 的 sysfs export 是两个坐标系，换算见 §6.3。

`gpio-event-mon` 的选项（`tools/gpio/gpio-event-mon.c:142-161`）：`-n`（chip 名）`-o`（offset）`-d`（消费者名）`-s`/`-r`/`-f`（bias / rising / falling）`-w`（wall clock）`-t`（HTE）`-b`（去抖 μs）`-c`（事件个数上限）`-?`。文档里的真实示例行（`:159`）：

```
gpio-event-mon -n gpiochip0 -o 4 -r -f -b 10000
```

`gpio-hammer` 的示例行（`tools/gpio/gpio-hammer.c:123`）：

```
gpio-hammer -n gpiochip0 -o 4
```

**`gpio-watch` 没有 usage，参数只能读代码**：`main()` 在 `:23`，`if (argc < 3) goto err_usage;`（`:32`），`fd = open(argv[1], O_RDWR | O_CLOEXEC)`（`:35`）。所以形式是：

```bash
gpio-watch /dev/gpiochip0 4 5 6      # 第一个参数是设备节点，后面是 offset 列表
```

**`gpio-watch` 是排查"谁抢了我的线"的正解**：它监听 lineinfo-changed 事件，任何进程 request/release/reconfigure 这几根线都会打出来。比反复 `cat /sys/kernel/debug/gpio` 轮询靠得住（那种轮询会漏掉短暂持有）。

### §10.2 out-of-tree：libgpiod

内核树里没有，但 `Documentation/admin-guide/gpio/sysfs.rst:11-13` 指的方向就是它 —— 该文件把用户引向 `tools/gpio/*` 和 `include/uapi/linux/gpio.h`。发行版包名通常是 `libgpiod` / `gpiod`，命令行工具是 `gpiodetect` / `gpioinfo` / `gpioget` / `gpioset` / `gpiomon` / `gpiofind`。

**它不属于本树，版本行为不保证**，本文档不给它的示例输出。要精确对应 6.1 的 ABI，用 §10.1 的 in-tree 四件套。

### §10.3 ioctl 全表（`include/uapi/linux/gpio.h:505-529`）

自己写工具时按这张表。魔数全部是 `0xB4`。

**v2（推荐，`CONFIG_GPIO_CDEV`）**

| 名字 | 定义 | 行 |
|---|---|---|
| `GPIO_GET_CHIPINFO_IOCTL` | `_IOR(0xB4, 0x01, struct gpiochip_info)` | :505 |
| `GPIO_GET_LINEINFO_UNWATCH_IOCTL` | `_IOWR(0xB4, 0x0C, __u32)` | :506 |
| `GPIO_V2_GET_LINEINFO_IOCTL` | `_IOWR(0xB4, 0x05, struct gpio_v2_line_info)` | :511 |
| `GPIO_V2_GET_LINEINFO_WATCH_IOCTL` | `_IOWR(0xB4, 0x06, struct gpio_v2_line_info)` | :512 |
| `GPIO_V2_GET_LINE_IOCTL` | `_IOWR(0xB4, 0x07, struct gpio_v2_line_request)` | :513 |
| `GPIO_V2_LINE_SET_CONFIG_IOCTL` | `_IOWR(0xB4, 0x0D, struct gpio_v2_line_config)` | :514 |
| `GPIO_V2_LINE_GET_VALUES_IOCTL` | `_IOWR(0xB4, 0x0E, struct gpio_v2_line_values)` | :515 |
| `GPIO_V2_LINE_SET_VALUES_IOCTL` | `_IOWR(0xB4, 0x0F, struct gpio_v2_line_values)` | :516 |

**v1（`CONFIG_GPIO_CDEV_V1`，已废弃）**

| 名字 | 编号 | 行 |
|---|---|---|
| `GPIO_GET_LINEINFO_IOCTL` | `0x02` | :523 |
| `GPIO_GET_LINEHANDLE_IOCTL` | `0x03` | :524 |
| `GPIO_GET_LINEEVENT_IOCTL` | `0x04` | :525 |
| `GPIOHANDLE_GET_LINE_VALUES_IOCTL` | `0x08` | :526 |
| `GPIOHANDLE_SET_LINE_VALUES_IOCTL` | `0x09` | :527 |
| `GPIOHANDLE_SET_CONFIG_IOCTL` | `0x0A` | :528 |
| `GPIO_GET_LINEINFO_WATCH_IOCTL` | `0x0B` | :529 |

三个 ：

- **`GPIO_GET_CHIPINFO_IOCTL`（`0x01`）和 `GPIO_GET_LINEINFO_UNWATCH_IOCTL`（`0x0C`）不带 `V2` 前缀，但它们是 v1/v2 共用的。** 只用 v2 的程序仍然需要这两个。
- **编号是交错的**：v1 占 `0x02-0x04`、`0x08-0x0B`，v2 占 `0x05-0x07`、`0x0D-0x0F`，共用的占 `0x01`、`0x0C`。**不能按"编号大小"判断新旧**。
- **`ENOTTY` 的含义是"这个 ioctl 被编掉了"**：v1 那 7 个在 `CONFIG_GPIO_CDEV_V1=n` 的内核上会返回 `ENOTTY`。而 `CONFIG_GPIO_SYSFS` 会 `select GPIO_CDEV`（§1.1 的 `Kconfig:64`）但**不会** select `GPIO_CDEV_V1`，所以"有 sysfs 面就一定有 v1"是错的。

---

## §11 一页速查

### §11.1 五分钟定位表

| 现象 | 一条命令 | 看什么 |
|---|---|---|
| 有哪些控制器 | `ls /sys/bus/gpio/devices/` | 不依赖 debugfs 和 GPIO_SYSFS（§5.4） |
| 全局编号换算 | `grep GPIOs /sys/kernel/debug/gpio` | 表头的 `GPIOs %d-%d`（§4.4） |
| 或者 | `for d in /sys/class/gpio/gpiochip*; do echo "$d $(cat $d/base) $(cat $d/ngpio)"; done` | 不依赖 debugfs（§5.3） |
| 某根线被谁占了 | `cat /sys/kernel/debug/gpio` | 只列 requested 或有名字的（§4.2） |
| 短暂占用抓不到 | `gpio-watch /dev/gpiochip0 <offsets…>` | 事件式，不漏（§10.1） |
| 线名和 consumer | `lsgpio` | 用 offset 坐标（§10.1） |
| 电平列印 `?` | 那根线的控制器没有 `get` | `-EIO` at `gpiolib.c:2623`（§4.3 / §8.2） |
| 查不到某根线 | 既没人 request 也没起名字 | `gpiolib.c:4499` / `:4511`（§4.2） |
| 读写操作有没有真发生 | 开 §6.5 两个 tracepoint | **两个都要开**（§6.4） |
| 极性对不对 | 对比调用参数和 trace 里的值 | trace 是 raw 层（§6.3） |
| 边沿有没有来 | `gpio-event-mon -n gpiochipN -o M -r -f` | §10.1 |
| 或手工 | `echo N > /sys/class/gpio/export; echo both > gpioN/edge` | 中断名固定叫 `gpiolib`（§5.2.2） |
| 某属性没被读 | 见 06 §定位章 | 07 不重复 |
| 一句日志都没有 | `echo 'file gpiolib*.c +p' > .../dynamic_debug/control` | 共 30 条可开（§3.2） |
| 启动期日志赶不上 | 内核命令行加 `dyndbg="file gpiolib*.c +p"` | 注册在 `core_initcall`（§3.1） |
| probe 一直不成 | `cat /sys/kernel/debug/devices_deferred` | §8.5 |
| 想造一个假控制器 | `modprobe gpio-sim` + configfs | §9.1 |
| 想复现 `-EBUSY` | gpio-sim 的 `hog` | §9.1 |

### §11.2 三个坐标系（最常踩的坑）

| 面 | 用什么标识一根线 |
|---|---|
| cdev / `lsgpio` / `gpio-event-mon` / gpio-sim / mockup | **`(设备节点, offset)`** |
| tracepoint / `/sys/class/gpio/export` / debugfs 每行的 `gpio-%d` | **全局编号** |
| DTS | **`(phandle, args…)`**，args 的含义由 `of_gpio_n_cells` 定（06 §2.3） |

换算：`全局编号 = base + offset`。`base` 来自 §5.3 或 §4.4。

### §11.3 四个值层（读 trace / debugfs / sysfs 时对号）

| 层 | 在哪能看到 |
|---|---|
| PAD 实际电平 | 示波器；gpio-sim 的 `pull` 可注入（§9.1） |
| 寄存器值 | 控制器驱动的 `dbg_show`（§4.5） |
| **raw** | tracepoint（§6.3）、`direction` 写 high/low（§5.2.1） |
| **logical** | `value` 读写（§5.2.1）、`gpiod_get/set_value()`、debugfs 电平列 |

**同一个 sysfs 目录里 `direction` 是 raw、`value` 是 logical** —— 这是全篇最容易出错的一条（§5.2.1）。

### §11.4 编译期开关最小集

| 目标 | 需要 |
|---|---|
| 基本可用 | `CONFIG_GPIOLIB=y` |
| debugfs 清单 | `+ CONFIG_DEBUG_FS=y`（§4.1） |
| legacy sysfs 面 | `+ CONFIG_GPIO_SYSFS=y`（会连带 `GPIO_CDEV`，`Kconfig:64`） |
| cdev v1 兼容 | `+ CONFIG_GPIO_CDEV_V1=y`（**不会**被自动 select，§10.3） |
| 睡眠上下文检查 | `+ CONFIG_DEBUG_KERNEL=y` 且 `CONFIG_DEBUG_GPIO=y`（四道门，§1.2） |
| dynamic debug | `+ CONFIG_DYNAMIC_DEBUG=y` 且 **`CONFIG_DEBUG_GPIO=n`**（互斥，§1.2） |
| 造虚拟控制器 | `+ CONFIG_GPIO_SIM=y`（连带 `IRQ_SIM` + `CONFIGFS_FS`，§9） |

**最后一条互斥必须记住**：`CONFIG_DEBUG_GPIO=y` 会给整个 `drivers/gpio/` 加 `-DDEBUG`（`Makefile:4`），使 30 条 `*_dbg` 变成无条件输出，**dynamic debug 的按需开关随之失效**（§1.2）。两者选一个。

---

## §12 导航

| 想知道 | 去 |
|---|---|
| 这些 API 本身的语义 | 01 |
| `struct gpio_desc` 的 flags 全表 | 02 §3 |
| 四层值模型、`_commit` 那一层为什么存在 | 03 §A5 |
| `gpiochip_add_data()` 注册期做了什么（对应 §2.4 的日志） | 03 §A2 |
| irqchip 是怎么挂上去的（对应 §7.2 / §8.4） | 03 §B4、04 §5 |
| `NONEXCLUSIVE` 是干什么的（对应 §8.6） | 04 §4 |
| cdev 的 v1/v2 结构体和事件语义（对应 §10.3） | 04 §6 |
| 提供方该填哪些回调（对应 §8.2 组 A 的四条日志） | 05 §3、§4 |
| 消费者代码骨架 | 05 §2 |
| `gpio-reserved-ranges` 为什么静默（对应 §8.1 组④） | 06 §2.6 |
| 属性没被消费怎么定位 | 06 §定位章 |
| 为什么全局编号是个错误的抽象（对应 §8.8） | 08 §5 |
| 本篇每条论断的出处 | 99 §逐篇取证表 |
