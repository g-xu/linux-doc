---
title: 调试方法
---

# drivers/base 调试手册

## §0 导航

### §0.1 这一篇的定位

驱动模型核心自己几乎不产生"错误"。它产生的是**别人的错误的现场**：某个设备没出现在 `/sys/devices/` 下、某个驱动的 `probe()` 没被调用、调用了但返回了负数、返回了 `-EPROBE_DEFER` 之后再也没被重试。这一篇讲的全部是"到哪里去看这些现场"。

一个必须先接受的事实：**驱动核心默认几乎不说话**。它的日志绝大多数是 `dev_dbg()` / `pr_debug()`，在默认配置下一条都不打印。`drivers/base/` 八个核心文件里的调试级日志分布：

| 文件 | `dev_dbg(` 行数 | `pr_debug(` 行数 |
|---|---|---|
| `drivers/base/core.c` | 11 | 17 |
| `drivers/base/dd.c` | 13 | 8 |
| `drivers/base/bus.c` | 0 | 7 |
| `drivers/base/class.c` | 0 | 5 |
| `drivers/base/platform.c` | 0 | 4 |
| `drivers/base/driver.c` | 0 | 0 |
| `drivers/base/devres.c` | 0 | 0 |
| `drivers/base/property.c` | 0 | 0 |

（口径：`grep -c 'dev_dbg('`，数的是**含该串的行数**，不是调用点数——多行调用只算首行。重跑命令见 §11。）

所以本篇的第一顺位内容不是"读日志"，而是 §1 和 §3：**先把嘴打开**。

### §0.2 按症状找章节

| 症状 | 先看 | 再看 |
|---|---|---|
| `/sys/devices/` 下根本没有这个设备 | `06-devicetree.md` §11（DT 没被展开） | §9.1 |
| 设备在，`driver` 符号链接不存在 | §4.1 `devices_deferred` | §7.3、§9.2 |
| 设备在，且不在 `devices_deferred` 里 | §4.1 的"三个不出现的原因" | §7.3 |
| `probe()` 完全没被调用 | §5.2 `drivers_autoprobe`、§3.3 异步探测 | §9.1 |
| `probe()` 返回了错但 dmesg 里没有任何行 | §2.3 的可见性分组 | §7.2 |
| dmesg 里只有 `probe of X failed with error -N` | §7.1 错误码分诊 | §2.4 |
| 一直 defer，超时后系统起来但设备没了 | §3.2 `deferred_probe_timeout` | §7.3 |
| 想知道谁在等谁 | §5.3 device link 的 sysfs 视图 | §3.4 `fw_devlink` |
| `devm_*` 申请的资源怀疑泄漏/提前释放 | §1.3 + §3.5 `devres.log` | §6 tracepoint |
| remove 路径怀疑有 use-after-free | §1.4 `DEBUG_TEST_DRIVER_REMOVE` | — |
| 想手工解绑再绑一次 | §5.2 `bind` / `unbind` | §5.2 的返回码陷阱 |
| 启动慢，想知道时间花在哪个 probe 上 | §3.1 `initcall_debug` | — |

### §0.3 这一篇不覆盖什么

| 内容 | 去处 |
|---|---|
| DT 属性写错的定位 | `06-devicetree.md` §11–§13 |
| 各个 API 的返回值语义 | `01-api.md` §3 |
| probe 流程内部每一步做什么 | `03-internals.md` Part A |
| 电源管理 / runtime PM 的调试（`/sys/.../power/`、`rpm_*` tracepoint） | 不在本篇，属 PM 子系统 |
| regmap 的 debugfs 寄存器视图 | 不在本篇，`drivers/base/regmap/` 是独立子框架 |
| firmware 加载的调试 | 不在本篇；本套的 `firmware` 子系统文档集 |

---

## §1 编译期开关

### §1.1 全部三个

`drivers/base/Kconfig` 里跟调试直接相关的配置项只有三个，全都 `depends on DEBUG_KERNEL`：

| Kconfig | 位置 | 作用 | 打开后的代价 |
|---|---|---|---|
| `CONFIG_DEBUG_DRIVER` | `drivers/base/Kconfig:130-139` | 给 `drivers/base/` 和 `drivers/base/power/` 整目录加 `-DDEBUG` | 日志量大；且**有前置条件**，见 §1.2 |
| `CONFIG_DEBUG_DEVRES` | `drivers/base/Kconfig:141-151` | 编出 `devres.log` 这个运行时开关 | 编译期几乎无代价，默认还是关的 |
| `CONFIG_DEBUG_TEST_DRIVER_REMOVE` | `drivers/base/Kconfig:153-163` | 每次 probe 之后立刻 remove 再 probe | 帮助文本自己写了 "may render your system unusable" |

另有两个测试模块，不是调试开关但同属这一档：`CONFIG_TEST_ASYNC_DRIVER_PROBE`（`drivers/base/test/Kconfig:2-10`，`depends on m`，产出 `test_async_driver_probe.ko`）和 `CONFIG_DRIVER_PE_KUNIT_TEST`（`drivers/base/test/Kconfig:11-14`，property entry 的 KUnit 测试）。

### §1.2 `CONFIG_DEBUG_DRIVER` 要过两道门

⚠️ 打开 `CONFIG_DEBUG_DRIVER` **不一定**能让 `dev_dbg()` 输出。它做的事只有一件：

```
drivers/base/Makefile:32          ccflags-$(CONFIG_DEBUG_DRIVER) := -DDEBUG
drivers/base/power/Makefile:9     ccflags-$(CONFIG_DEBUG_DRIVER) := -DDEBUG
```

也就是定义一个 `DEBUG` 宏。而 `dev_dbg()` 在 `include/linux/dev_printk.h:162-172` 是三选一：

```c
#if defined(CONFIG_DYNAMIC_DEBUG) || \
	(defined(CONFIG_DYNAMIC_DEBUG_CORE) && defined(DYNAMIC_DEBUG_MODULE))
#define dev_dbg(dev, fmt, ...)						\
	dynamic_dev_dbg(dev, dev_fmt(fmt), ##__VA_ARGS__)
#elif defined(DEBUG)
#define dev_dbg(dev, fmt, ...)						\
	dev_printk(KERN_DEBUG, dev, dev_fmt(fmt), ##__VA_ARGS__)
#else
#define dev_dbg(dev, fmt, ...)						\
	dev_no_printk(KERN_DEBUG, dev, dev_fmt(fmt), ##__VA_ARGS__)
#endif
```

第一个分支优先。所以两道门的真值表：

| `CONFIG_DYNAMIC_DEBUG` | `CONFIG_DEBUG_DRIVER` | `dev_dbg()` 的实际行为 |
|---|---|---|
| y | 任意 | 走 dynamic debug，`-DDEBUG` **完全无效**；要靠 `dyndbg=` 或 `/sys/kernel/debug/dynamic_debug/control` 逐条打开 |
| n | y | 编译进去，无条件按 `KERN_DEBUG` 打印 |
| n | n | 编译成 `dev_no_printk()`（`include/linux/dev_printk.h:136-140`，`if (0)` 包着，只保留格式检查） |

`pr_debug()` 的三分支结构与此相同。实践含义：**主流发行版内核都开 `CONFIG_DYNAMIC_DEBUG`，此时 `CONFIG_DEBUG_DRIVER` 是个空开关**，该用的是 §3.6。

还有一个作用域限制常被忽略：`ccflags-y` 只作用于**当前 Makefile 编译的那些 .o**。所以 `-DDEBUG` 覆盖 `drivers/base/*.c`（`drivers/base/Makefile:4-9` 的 `obj-y` 那一批）和 `drivers/base/power/*.c`，**不覆盖** `drivers/base/regmap/`、`drivers/base/firmware_loader/`、`drivers/base/test/`——这三个子目录有自己的 Makefile，里面没有这一行。

另外注意 `drivers/base/Makefile:32` 用的是 `:=` 而不是 `+=`。这个文件里 `ccflags` 只出现这一次，所以没有覆盖别人的问题；但同一个目录里另有一条**按对象**的 flag：`CFLAGS_trace.o := -I$(src)`（`drivers/base/Makefile:35`），两者作用域不同，互不影响。

### §1.3 `CONFIG_DEBUG_DEVRES`：编译期开、运行期再开

这个开关的语义是"把运行时开关编出来"，本身不产生日志。`drivers/base/devres.c:52-65`：

```c
#ifdef CONFIG_DEBUG_DEVRES
static int log_devres = 0;
module_param_named(log, log_devres, int, S_IRUGO | S_IWUSR);

static void devres_dbg(struct device *dev, struct devres_node *node,
		       const char *op)
{
	if (unlikely(log_devres))
		dev_err(dev, "DEVRES %3s %p %s (%zu bytes)\n",
			op, node, node->name, node->size);
}
#else /* CONFIG_DEBUG_DEVRES */
#define devres_dbg(dev, node, op)	do {} while (0)
#endif /* CONFIG_DEBUG_DEVRES */
```

三个细节值得单独记：

- 打印用的是 `dev_err()`，不是 `dev_dbg()`。所以一旦 `log_devres` 非 0，输出**不受 dynamic debug 管**，直接进 `KERN_ERR` 级。这解释了为什么 `drivers/base/devres.c` 的 `dev_dbg` 计数是 0（见 §0.1 的表）。
- `module_param_named(log, ...)`，`drivers/base/devres.c` 编进 vmlinux，所以运行时路径是 `/sys/module/devres/parameters/log`，命令行形式是 `devres.log=1`。`drivers/base/Kconfig:145-149` 的帮助文本对这一点的表述是 "enables kernel parameter devres.log … can be switched on and off from sysfs node"，与代码一致。
- 权限 `S_IRUGO | S_IWUSR` = 0644，运行时可写，**改了立刻生效**（每次 `devres_log()` 都重读该变量）。

### §1.4 `CONFIG_DEBUG_TEST_DRIVER_REMOVE`

它把 probe 变成 probe → remove → probe。开关落点在 `drivers/base/dd.c:586-587`：

```c
	bool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&
			   !drv->suppress_bind_attrs;
```

两点：

- 用的是 `IS_ENABLED()` 而不是 `#ifdef`，所以这一行在关闭时也参与编译（`test_remove` 恒为 false，编译器消掉后面的分支）。
- 有 `!drv->suppress_bind_attrs` 这个豁免——设置了该标志的驱动不参与这个测试。
- 真正走那条路的地方是 `drivers/base/dd.c:672-673`，且 `test_remove` 在那里被立刻置 false，所以只重试**一次**，不会无限循环。

全树只有这一处引用 `CONFIG_DEBUG_TEST_DRIVER_REMOVE`：

```
grep -rn 'DEBUG_TEST_DRIVER_REMOVE' --include=*.c --include=*.h .
```

→ 一条命中，`drivers/base/dd.c:586`。

---

## §2 日志：先搞清可见性等级

### §2.1 `dev_*` 家族的三层展开

`include/linux/dev_printk.h` 共 278 行，几乎全是宏。链路是三层：

```
dev_err(dev, fmt, ...)                       include/linux/dev_printk.h:153-154
  └─ dev_printk_index_wrap(_dev_err, KERN_ERR, dev, dev_fmt(fmt), ...)   :107
       └─ _dev_err(dev, fmt, ...)            由宏生成，见下
            └─ __dev_printk(KERN_ERR, dev, &vaf)    drivers/base/core.c:4952-4960
                 └─ dev_printk_emit()               drivers/base/core.c:4937
                      └─ dev_vprintk_emit() → vprintk_emit()   drivers/base/core.c:4933
```

中间那七个 `_dev_<level>()` 函数一个都不是手写的。它们由 `drivers/base/core.c:4979-4994` 的 `define_dev_printk_level` 宏展开七次得到（`drivers/base/core.c:4996-5002`）：`_dev_emerg`、`_dev_alert`、`_dev_crit`、`_dev_err`、`_dev_warn`、`_dev_notice`、`_dev_info`。宏体里带 `EXPORT_SYMBOL(func)`，所以七个符号都是导出的——但 `grep EXPORT_SYMBOL` 只能看到宏定义里那一行。这是索引工具和 grep 统计都会漏掉的形态。

`__dev_printk()` 在 `dev` 为 NULL 时不崩，退化成 `printk("%s(NULL device *): %pV", ...)`（`drivers/base/core.c:4958-4959`）。所以 dmesg 里出现 `(NULL device *):` 前缀意味着调用点把一个空指针传进了 `dev_err()`，而不是某个真实设备。

前缀格式是 `dev_driver_string(dev)` + 空格 + `dev_name(dev)` + `": "`（`drivers/base/core.c:4956-4957`）。因此**同一个设备在绑定驱动前后，日志前缀会变**：绑定前是总线名或 `subsystem`，绑定后是驱动名。按前缀 grep dmesg 时要留意这一点。

### §2.2 可见性等级完整表

同一条消息选哪个宏，决定它在生产内核里能不能被看到。`include/linux/dev_printk.h` 提供的全部变体：

| 形式 | 定义位置 | 默认可见性 | 何时用 |
|---|---|---|---|
| `dev_emerg` / `dev_alert` / `dev_crit` / `dev_err` / `dev_warn` / `dev_notice` / `dev_info` | `include/linux/dev_printk.h:147-160` | 始终输出（受 console loglevel 过滤，不受编译开关影响） | 一次性的、真实的错误 |
| `dev_dbg` | `:164-172` | 三选一，见 §1.2 | 流程跟踪 |
| `dev_vdbg` | `:261-268` | `CONFIG_VERBOSE_DEBUG` 打开时等于 `dev_dbg`，否则是 `dev_no_printk` | 高频流程 |
| `dev_<level>_once` 七个 | `:192-204`（`dev_dbg_once` 在 `:206`） | 同基础级别，但**整个内核生命周期只打一次**，靠一个 `static bool __print_once`（`:175-183`） | 会被反复触发的配置类告警 |
| `dev_<level>_ratelimited` 七个 | `:218-230`（`dev_dbg_ratelimited` 在 `:235`/`:247`/`:256` 三分支） | 同基础级别，但被 `_rs` 限速 | 高频错误路径 |
| `dev_WARN` / `dev_WARN_ONCE` | `:271-276` | 走 `WARN`/`WARN_ONCE`，带栈回溯，可能触发 panic_on_warn | 内核自己的不变量被破坏 |

⚠️ 三个容易踩的点：

- `_once` 的 `static bool` 是**按调用点**的，不是按设备的。所以一个宏在同一行被 100 个设备走过，只有第一个设备的消息会出现。看到 "只有第一块卡报了这个错" 时，不要据此认为其他卡没问题。
- `CONFIG_PRINTK=n` 时 `dev_level_once` 换成 `:185-190` 那个分支，直接丢弃。
- `dev_dbg_ratelimited` 有三个分支（`:235` dynamic debug、`:247` `DEBUG`、`:256` 兜底），第一个分支里限速和 dynamic debug 两道门是**串联**的：dynamic debug 没打开时限速器根本不参与。

### §2.3 驱动核心自己的日志清单

按可见性分组，这样才能判断"dmesg 里没有 = 没发生"还是"dmesg 里没有 = 没打开"。

**A. 默认可见（`pr_warn` / `dev_warn` / `pr_info` / `dev_info` / `dev_err`）**

| 消息（逐字） | 位置 | 含义 |
|---|---|---|
| `%s: probe of %s failed with error %d\n` | `drivers/base/dd.c:576-577` | probe 返回了非 0、且不是 defer/ENODEV/ENXIO。参数次序是**驱动名、设备名、错误码** |
| `probe deferral not supported\n` | `drivers/base/platform.c:1407` | 驱动设了 `prevent_deferred_probe` 却返回了 defer；紧接着错误码被改写，见 §7.2 |
| `deferred probe timeout, ignoring dependency\n` | `drivers/base/dd.c:298` | `driver_deferred_probe_check_state()` 判定超时 |
| `ignoring dependency for device, assuming no driver\n` | `drivers/base/dd.c:293` | 同上，但走的是 `!CONFIG_MODULES && initcalls_done` 那条 |
| `deferred probe pending\n` | `drivers/base/dd.c:318` | 超时 work 收尾时逐个列出还挂着的设备。这是**唯一一条默认可见的、逐设备的 deferred 报告** |
| `Too long list of driver names for 'driver_async_probe'!\n` | `drivers/base/dd.c:844` | 命令行参数超 256 字节；⚠️ 只是告警，随后仍然截断使用，见 §3.3 |
| `Trying to probe devices needed for running init ...\n` | `drivers/base/core.c:1861` | fw_devlink 进入 best-effort 阶段 |
| `%pfwf: Fixed dependency cycle(s) with %pfwf\n` | `drivers/base/core.c:2090` | fw_devlink 检测到并打破了一个依赖环 |
| `Failed to create device link (0x%x) with %s\n` | `drivers/base/core.c:2121` | 建 device link 失败，括号里是 flags |
| `Error %d creating of_node link\n` | `drivers/base/core.c:3412` | `of_node` 符号链接没建上 |
| `uevent: failed to send synthetic uevent: %d\n` | `drivers/base/core.c:2692` | 往 `uevent` 属性写入的内容没解析成功 |
| `wait for supplier %pfwP\n` | `drivers/base/core.c:1123-1124` | 经 `dev_err_probe()` 打印，级别取决于错误码，见 §2.4 |
| `supplier %s not ready\n` | `drivers/base/core.c:1150-1152` | 同上 |
| `0 is an invalid IRQ number\n` | `drivers/base/platform.c:236`、`:452` | `WARN()`，带栈；两处逐字相同 |

**B. 只在调试级可见（`dev_dbg` / `pr_debug`），默认一条都看不到**

| 消息（逐字） | 位置 | 用途 |
|---|---|---|
| `Added to deferred list\n` | `drivers/base/dd.c:139` | 进 deferred 队列 |
| `Removed from deferred list\n` | `drivers/base/dd.c:149` | 出 deferred 队列 |
| `Retrying from deferred list\n` | `drivers/base/dd.c:122` | 重试一轮 |
| `Extended deferred probe timeout by %d secs\n` | `drivers/base/dd.c:332-333` | 有新驱动注册，超时被重置 |
| `Driver %s requests probe deferral\n` | `drivers/base/dd.c:567` | 驱动自己返回了 defer |
| `Driver %s force probe deferral\n` | `drivers/base/dd.c:596` | `defer_all_probes` 生效期间 |
| `Device match requests probe deferral\n` | `drivers/base/dd.c:918` | 总线的 `match()` 返回了 defer |
| `Bus failed to match device: %d\n` | `drivers/base/dd.c:927` | `match()` 返回了其他负数 |
| `%s: probe of %s rejects match %d\n` | `drivers/base/dd.c:571-572` | probe 返回 `-ENODEV`/`-ENXIO`，按"不是我的设备"处理 |
| `%s: probe_count = %d\n` | `drivers/base/dd.c:743` | `driver_probe_done()` 被查询 |
| `async probe completed\n` | `drivers/base/dd.c:973` | 一个异步 probe 走完 |
| `Relaxing link with %s\n` | `drivers/base/core.c:1794` | fw_devlink 把一条推断链接降级 |
| `Not linking %pfwf - might never become dev\n` | `drivers/base/core.c:2135` | fw_devlink 放弃给某个 fwnode 建链接 |
| `----- cycle: start -----\n` / `----- cycle: end -----\n` | `drivers/base/core.c:1958`、`:2089` | 环检测的两端 |
| `%pfwf: cycle: child of %pfwf\n` | `drivers/base/core.c:1996` | 环上的一段 |
| `%pfwf: cycle: depends on %pfwf\n` | `drivers/base/core.c:2015` | 环上的一段 |

**C. 特殊：`printk(KERN_DEBUG ...)` 而不是 `pr_debug()`**

只有一条，且源码里有专门的注释解释为什么——`drivers/base/dd.c:723-729`：

```c
	/*
	 * Don't change this to pr_debug() because that requires
	 * CONFIG_DYNAMIC_DEBUG and we want a simple 'initcall_debug' on the
	 * kernel commandline to print this all the time at the debug level.
	 */
	printk(KERN_DEBUG "probe of %s returned %d after %lld usecs\n",
		 dev_name(dev), ret, ktime_us_delta(rettime, calltime));
```

这一条**只受 `initcall_debug` 一个门控制**，不需要 dynamic debug。见 §3.1。

### §2.4 `dev_err_probe()`：一个函数，两种可见性

`drivers/base/core.c:5038-5057`：

```c
	if (err != -EPROBE_DEFER) {
		dev_err(dev, "error %pe: %pV", ERR_PTR(err), &vaf);
	} else {
		device_set_deferred_probe_reason(dev, &vaf);
		dev_dbg(dev, "error %pe: %pV", ERR_PTR(err), &vaf);
	}
```

三个必须记住的结论：

- **同一个调用点，`-EPROBE_DEFER` 走 `dev_dbg`（默认不可见），其余错误走 `dev_err`（可见）**。所以"某个用了 `dev_err_probe()` 的驱动在 dmesg 里一声不响"通常意味着它在 defer，不是它没跑。
- defer 分支额外做一件事：把消息存进设备的 deferred probe reason，那才是它的可见出口——`/sys/kernel/debug/devices_deferred`，见 §4.1。
- 格式固定为 `error %pe: ` 前缀（`%pe` 打印符号化错误名，如 `-EIO` 会显示成 `-EIO`）。所以 dmesg 里的 `error -EPROBE_DEFER:` 这种串一定来自 `dev_err_probe()`，不是驱动自己拼的。

配套函数 `device_set_deferred_probe_reason()`（`drivers/base/core.c:5050` 调用，实现在 `drivers/base/dd.c:227-238`）拼出的字符串是：

```c
	const char *drv = dev_driver_string(dev);
	...
	reason = kasprintf(GFP_KERNEL, "%s: %pV", drv, vaf);
```

即 `<驱动或总线名>: <消息>`。⚠️ 这个函数写满了 kerneldoc（`drivers/base/dd.c:222-226`）却**既没有 `EXPORT_SYMBOL` 也没有在 `include/linux/` 里声明**——它长得像公开 API，实际上外部驱动用不了。要设置 defer 原因，唯一的入口是 `dev_err_probe()`。

---

## §3 内核命令行与运行时参数

`drivers/base/` 里注册的命令行钩子共 8 个，全部由这一条命令列出：

```
grep -rn '__setup(\|early_param(\|module_param' drivers/base/*.c
```

其中与调试直接相关的是下面几个。

### §3.1 `initcall_debug`

不是 `drivers/base/` 自己的参数（定义在 init 核心），但驱动模型有三处专门响应它：

| 位置 | 行为 |
|---|---|
| `drivers/base/dd.c:782-785` | 用 `really_probe_debug()` 代替 `really_probe()`，从而打印每次 probe 的耗时 |
| `drivers/base/core.c:4850-4854` | 关机时 `dev_info(dev, "shutdown_pre\n")` |
| `drivers/base/core.c:4855-4863` | 关机时 `dev_info(dev, "shutdown\n")`，总线和驱动两条路径打印同一个字符串 |

`really_probe_debug()` 的实现（`drivers/base/dd.c:715-731`）就是在 `really_probe()` 两侧取 `ktime_get()`。输出格式见 §2.3 的 C 组。注意它打印的是 `probe of <设备名>`，**不带驱动名**；而失败时那条 `%s: probe of %s failed` 是"驱动名 + 设备名"。两条串很像，参数不一样。

用途：定位启动时间。`grep 'probe of.*after' dmesg | sort -t' ' -k5 -n` 之类。⚠️ 因为它是 `KERN_DEBUG` 级，控制台默认 loglevel 可能过滤掉，需要配合 `loglevel=8` 或直接读 `dmesg`。

### §3.2 `deferred_probe_timeout=<秒>`

解析函数 `drivers/base/dd.c:267-274`，注册在 `:275`。默认值有条件（`drivers/base/dd.c:259-263`）：

```c
#ifdef CONFIG_MODULES
int driver_deferred_probe_timeout = 10;
#else
int driver_deferred_probe_timeout;
#endif
```

即模块化内核默认 10 秒，全静态内核默认 0。这个变量有 `EXPORT_SYMBOL_GPL`（`drivers/base/dd.c:265`），驱动可以读。

超时到达时执行 `deferred_probe_timeout_work_func()`（`drivers/base/dd.c:306-320`）：调 `fw_devlink_drivers_done()` → 把超时值清 0 → 再触发一轮 → 然后**逐个 `dev_info(p->device, "deferred probe pending\n")`**。所以 §2.3 A 组那条日志出现的时刻就是超时时刻。

⚠️ 超时会被**延长**：每次有新驱动注册成功，`deferred_probe_extend_timeout()`（`drivers/base/dd.c:323-335`）会取消并重排定时器。它的日志 `Extended deferred probe timeout by %d secs` 是 `pr_debug` 级。因此"我设了 30 秒，实测过了 2 分钟才超时"是正常行为，不是 bug。函数里有个前置判断：

```c
	if (cancel_delayed_work(&deferred_probe_timeout_work)) {
```

`cancel_delayed_work()` 返回 false（work 还没排上、或已经执行过）时**不重排**，这就是注释 `drivers/base/dd.c:325-328` 说的意思。

解析本身也有一个静默点：`kstrtoint()` 失败时函数**什么都不做直接返回 1**（`drivers/base/dd.c:271-273`），保留默认值。所以 `deferred_probe_timeout=abc` 不会报错，只是没生效。

### §3.3 `driver_async_probe=<驱动名列表>`

解析函数 `drivers/base/dd.c:841-850`，注册在 `:851`。缓冲区固定 256 字节（`drivers/base/dd.c:61-62` 的 `ASYNC_DRV_NAMES_MAX_LEN`）。

⚠️ **两个陷阱**：

- 超长时先 `pr_warn`（`drivers/base/dd.c:843-844`），然后**照样 `strscpy` 截断使用**（`:846`）。列表尾部的驱动名会静默丢失。
- `*` 的语义是**反选**。`drivers/base/dd.c:847` 把 `async_probe_default` 设成"列表里有没有 `*`"，然后 `cmdline_requested_async_probing()`（`drivers/base/dd.c:833-837`）返回：

```c
	async_drv = parse_option_str(async_probe_drv_names, drv_name);

	return (async_probe_default != async_drv);
```

也就是说 `driver_async_probe=*,foo` 表示"除 `foo` 之外全部异步"，`foo` 反而是同步的。`Documentation/admin-guide/kernel-parameters.txt:1124-1129` 对此有明确说明。

命令行只是三道门里的一道。`driver_allows_async_probing()`（`drivers/base/dd.c:853-871`）的完整判定次序：

| 顺序 | 条件 | 结果 |
|---|---|---|
| 1 | `drv->probe_type == PROBE_PREFER_ASYNCHRONOUS` | 异步，命令行不参与 |
| 2 | `drv->probe_type == PROBE_FORCE_SYNCHRONOUS` | 同步，命令行**无法覆盖** |
| 3 | 命令行匹配（含 `*` 反选） | 异步 |
| 4 | 模块加载参数 `async_probe`（`module_requested_async_probing()`，`include/linux/module.h:678-681`） | 异步 |
| 5 | 都不满足 | 同步 |

所以设了 `driver_async_probe=` 却没效果时，第一嫌疑是该驱动写了 `PROBE_FORCE_SYNCHRONOUS`。

第 4 道门的入口不是内核命令行而是**模块加载参数**：`insmod foo.ko async_probe=1`，由 `kernel/module/main.c:2685-2686` 解析进 `mod->async_probe_requested`；另有一个全局默认 `module.async_probe`（`kernel/module/main.c:2447-2448`）。写成 `foo.async_probe=1` 放在内核命令行上对内建驱动无效。

### §3.4 `fw_devlink=` 与 `fw_devlink.strict=`

两个 `early_param`：`drivers/base/core.c:1738`（解析函数 `:1722-1737`）和 `:1745`（`:1741-1744`）。取值和语义见 `Documentation/admin-guide/kernel-parameters.txt:1576-1597`，四挡：`off` / `permissive` / `on` / `rpm`。默认是 `on`（`drivers/base/core.c:1721`）。

调试用途集中在两点：

- **`fw_devlink=off` 是判定"是不是 fw_devlink 造成的探测顺序问题"的一刀切实验。** 走的是 `drivers/base/core.c:1727-1728` 那个分支，直接把 flags 清 0。
- **`fw_devlink=permissive`** 保留链接但不用于强制探测顺序，适合"想看到依赖图但不想被它阻塞"。

`06-devicetree.md` §7 讲的是这两个参数怎么影响链接的**建立**；本篇只讲怎么用它们做二分。

### §3.5 `devres.log=1`

见 §1.3。需要 `CONFIG_DEBUG_DEVRES=y` 编译期打开，运行时再写 1。生效范围是**全系统所有设备**，没有按设备过滤的手段，输出量在启动后期会非常大。实用做法是启动时保持 0，定位前一刻写 1、定位后立刻写 0。

### §3.6 dynamic debug：真正常用的那一档

因为 §1.2 的结论是"主流内核里 `CONFIG_DEBUG_DRIVER` 无效"，实际打开 §2.3 B 组日志的手段是 dynamic debug。三种粒度：

| 目标 | 命令行 | 运行时 |
|---|---|---|
| 整个文件 | `dyndbg="file drivers/base/dd.c +p"` | `echo 'file drivers/base/dd.c +p' > /sys/kernel/debug/dynamic_debug/control` |
| 单条消息 | `dyndbg="file drivers/base/dd.c line 139 +p"` | 同上，改 `file ... line ... +p` |
| 按内容 | — | `echo 'format "deferred list" +p' > .../control` |

⚠️ 这些都不是 `drivers/base/` 提供的机制，本篇只给出与本子系统日志对应的用法；`dyndbg` 的完整语法属 dynamic debug 自己的文档。带 `+p` 的开关只影响 `pr_debug`/`dev_dbg`，对 §2.3 A 组和 C 组无效。

---

## §4 debugfs：只有两个入口

`drivers/base/` 整个目录（排除 `regmap/`、`power/`、`firmware_loader/` 三个子目录）只创建两个 debugfs 对象。这条命令可以确认：

```
grep -rn 'debugfs_create' drivers/base/*.c
```

→ 两条命中，`drivers/base/dd.c:346` 和 `drivers/base/component.c:113`/`:122`（后者是一个目录加目录下的动态文件）。

### §4.1 `/sys/kernel/debug/devices_deferred`

驱动模型最重要的一个调试出口。创建点 `drivers/base/dd.c:346-347`，权限 0444（只读），在 `deferred_probe_initcall()` 里建，同一个初始化函数的失败清理路径在 `drivers/base/dd.c:375` 用 `debugfs_lookup_and_remove()` 删。

输出由 `deferred_devs_show()` 产生（`drivers/base/dd.c:243-256`）：

```c
	list_for_each_entry(curr, &deferred_probe_pending_list, deferred_probe)
		seq_printf(s, "%s\t%s", dev_name(curr->device),
			   curr->device->p->deferred_probe_reason ?: "\n");
```

格式是 `<设备名>` + TAB + `<原因>`。原因串来自 §2.4 的 `device_set_deferred_probe_reason()`，形如 `<驱动或总线名>: error -EPROBE_DEFER: <消息>`。

⚠️ 四个必须知道的性质：

- **格式串是 `"%s\t%s"`，没有显式换行。** 换行完全依赖原因串自带 `\n`。原因串来自驱动 `dev_err_probe()` 的格式串，惯例是以 `\n` 结尾——但如果某个驱动漏写了，它和下一行会**粘在一起**。没有原因时用的兜底串是 `"\n"`（`drivers/base/dd.c:251`），即输出 `<设备名>` + TAB + 换行。
- **原因只在用了 `dev_err_probe()` 的路径上才有。** 直接 `return -EPROBE_DEFER;` 的驱动只会在这里留一个名字加一个 TAB，看不出为什么。
- **设备可能在 defer 却不出现在这个文件里。** 见下。
- **超时之后这个文件会变空**，因为超时 work 会把还挂着的设备重新触发一轮（`drivers/base/dd.c:312-314`），失败的那些不再回到 pending 列表。要在超时前读，或者靠 §2.3 A 组那条 `deferred probe pending` 日志。

**设备不出现在 `devices_deferred` 里的三个原因**，按可能性排序：

| 原因 | 判据 | 怎么确认 |
|---|---|---|
| `dev->can_match` 为假 | `driver_deferred_probe_add()` 开头就 `if (!dev->can_match) return;`（`drivers/base/dd.c:134-135`） | 说明**从来没有驱动匹配上这个设备**，问题不在 defer 而在匹配。查 `06-devicetree.md` §2 的 compatible |
| 设备根本没注册 | `/sys/devices/` 下找不到 | 查 DT 展开，`06-devicetree.md` §11 |
| 已经超时过一轮 | 见上一条 | 查 dmesg 里的 `deferred probe pending` |

第一条是实践中最常见的误判来源：**"不在 `devices_deferred` 里"不等于"没在等依赖"，很可能是连匹配都没发生。** `can_match` 的置位点之一在 `drivers/base/dd.c:919`。

### §4.2 `/sys/kernel/debug/device_component/<父设备名>`

component 框架的聚合视图。目录在 `core_initcall` 里建（`drivers/base/component.c:111-118`），每个 aggregate device 注册时加一个同名文件（`drivers/base/component.c:120-124`，绑定点在 `:532`），注销时删（`:126-129`，调用点 `:486`）。整块代码在 `#ifdef CONFIG_DEBUG_FS` 里（`drivers/base/component.c:79`），关闭时换成 `:133-137` 的空函数。

输出格式由 `component_devices_show()` 决定（`drivers/base/component.c:83-107`），两张表：

```
aggregate_device name                                 status
-------------------------------------------------------------
<父设备名>                                            bound / not bound

device name                                           status
-------------------------------------------------------------
<组件设备名>                          bound / not bound / not registered
```

⚠️ 第三种状态 `not registered`（`drivers/base/component.c:102`）是这个文件的核心价值：它表示 `component_match_add()` 登记了一个匹配条目，但对应的组件**至今没调用 `component_add()`**。这正是"DRM 驱动一直不出画面"这类问题的典型答案。前两种状态区分的是"组件在但没绑上"。

用法：`cat /sys/kernel/debug/device_component/*` 一次看全部。这也是 §0.1 提到的那两个"假的 DT 属性命中"的来源——`drivers/base/component.c:90` 和 `:95` 里的 `"status"` 是这张表的列头，不是 DT 属性名。

---

## §5 sysfs：观察点与写入点

### §5.1 只读观察点

| 路径 | 产生代码 | 读到什么 | 用途 |
|---|---|---|---|
| `/sys/devices/.../driver` | `drivers/base/dd.c:447-448` 建符号链接 | 指向 `/sys/bus/<bus>/drivers/<drv>` | **判断有没有绑上驱动的唯一权威**。不存在即未绑定 |
| `/sys/bus/<bus>/drivers/<drv>/<设备名>` | `drivers/base/dd.c` 的 `driver_sysfs_add()`，反向链接 | 指回设备 | 从驱动侧列出它绑了哪些设备 |
| `/sys/devices/.../state_synced` | `drivers/base/dd.c:515-526`，仅当 `dev_has_sync_state(dev)` 时创建（`:664-665`） | `0`/`1` | 供应方的 `sync_state()` 有没有跑过 |
| `/sys/devices/.../waiting_for_supplier` | `drivers/base/core.c:1302-1315` | `0`/`1` | 是否有 fwnode 级别的供应方还没成为设备 |
| `/sys/devices/.../removable` | `drivers/base/core.c:2731-2748` | `removable`/`fixed`/`unknown` | 三态，`unknown` 是默认 |
| `/sys/devices/.../of_node` | `drivers/base/core.c:3410` 建符号链接 | 指向 `/sys/firmware/devicetree/base/...` | **反查这个设备来自哪个 DT 节点**，见 `06-devicetree.md` §11.3 |
| `/sys/class/devlink/<name>/status` | `drivers/base/core.c:478-509` | 六种串，见下 | device link 的状态机 |
| `/sys/class/devlink/<name>/auto_remove_on` | `drivers/base/core.c:511-526` | `supplier unbind`/`consumer unbind`/`never` | 这条链接什么时候自动消失 |
| `/sys/class/devlink/<name>/runtime_pm` | `drivers/base/core.c:528-535` | `0`/`1` | 是否带 `DL_FLAG_PM_RUNTIME` |
| `/sys/class/devlink/<name>/sync_state_only` | `drivers/base/core.c:537-545` | `0`/`1` | 是否只用于 `sync_state()` 排序 |

`status` 的六种取值与含义（`drivers/base/core.c:483-505`）：

| 输出串 | 枚举 | 含义 |
|---|---|---|
| `not tracked` | `DL_STATE_NONE` | 无状态链接，或已被丢弃 |
| `dormant` | `DL_STATE_DORMANT` | 供应方还没绑驱动 |
| `available` | `DL_STATE_AVAILABLE` | 供应方已绑，消费方可以探测 |
| `consumer probing` | `DL_STATE_CONSUMER_PROBE` | 消费方正在 probe，供应方此时不能解绑 |
| `active` | `DL_STATE_ACTIVE` | 两端都绑好了 |
| `supplier unbinding` | `DL_STATE_SUPPLIER_UNBIND` | 供应方正在解绑，消费方会被先解 |
| `unknown` | default 分支 | 状态字段被写坏了 |

⚠️ 这四个属性都在 `devlink_attrs[]` 里（`drivers/base/core.c:547-554`），**全部只读，没有一个可写**。想改依赖关系只能靠 §3.4 的启动参数或改 DT。

### §5.2 可写入口与它们的 setter 语义

一共五个可写点。每一个的语义都要单独读，因为它们对"写进去的字节"的解释方式**互不相同**。

| 路径 | 权限 | 实现 | 接受什么 |
|---|---|---|---|
| `/sys/bus/<bus>/drivers/<drv>/bind` | 0200 | `drivers/base/bus.c:207-226` | 设备名 |
| `/sys/bus/<bus>/drivers/<drv>/unbind` | 0200 | `drivers/base/bus.c:184-200` | 设备名 |
| `/sys/bus/<bus>/drivers_probe` | 0200 | `drivers/base/bus.c:243-256` | 设备名 |
| `/sys/bus/<bus>/drivers_autoprobe` | 0644 | `drivers/base/bus.c:228-241` | 见下，⚠️ 极其宽松 |
| `/sys/devices/.../coredump` | 0200 | `drivers/base/dd.c:423-432` | 任何内容 |
| `/sys/devices/.../uevent` | 0644 | `drivers/base/core.c:2684-2698` | 合成 uevent 串 |
| `/sys/devices/.../online` | 0644 | `drivers/base/core.c:2711-2729` | bool |

逐个说清语义：

**`drivers_autoprobe`** —— `drivers/base/bus.c:236-239`：

```c
	if (buf[0] == '0')
		bus->p->drivers_autoprobe = 0;
	else
		bus->p->drivers_autoprobe = 1;
```

⚠️ 只看**第一个字节**，而且只认字符 `'0'`。含义：`echo 0 >` 关闭；`echo off >`、`echo no >`、`echo " 0" >` 全部是**打开**。写任何非法内容都不会报错，`return count` 无条件成功（`drivers/base/bus.c:240`）。这是全篇最容易被静默误用的一个开关。

关掉它之后，新注册的设备不会自动尝试绑定，必须靠 `drivers_probe` 或 `bind` 手动触发。这是"想在 probe 前先做点什么"的标准手法。

**`bind`** —— 关键限制写在源码注释里（`drivers/base/bus.c:202-205`）：

```
 * Manually attach a device to a driver.
 * Note: the driver must want to bind to the device,
 * it is not possible to override the driver's id table.
```

即**不能绕过 match**。流程是 `bus_find_device_by_name()` → `driver_match_device()` → `device_driver_attach()`。返回值语义：

| 返回 | 触发条件 |
|---|---|
| `-ENODEV` | 设备名找不到，**或者** `driver_match_device()` 不通过 |
| `device_driver_attach()` 的错误码 | 匹配通过但绑定失败 |
| `count`（写入成功） | 绑上了 |

⚠️ 头两种情况都返回 `-ENODEV`，shell 上都显示 `No such device`——**分不清"设备不存在"和"驱动不认这个设备"**。要区分只能先去 `/sys/bus/<bus>/devices/` 下确认名字在不在。

**`unbind`** —— `drivers/base/bus.c:191-195`。同样两种情况共用 `-ENODEV`：设备名找不到，或 `dev->driver != drv`（绑在别的驱动上）。

`bind`/`unbind` 两个属性都用 `DRIVER_ATTR_IGNORE_LOCKDEP` 声明（`drivers/base/bus.c:200`、`:226`，宏定义在 `:35-37`）。这不是"没有锁"，是**告诉 lockdep 别管这条路径**——通过 sysfs 触发 probe 会形成 sysfs 锁与 device 锁的交叉持有顺序，lockdep 会误报。含义是：这两个文件确实有已知的锁序特异性，脚本里反复 bind/unbind 时不要指望 lockdep 帮你兜底。

**`drivers_probe`** —— ⚠️ 错误码被改写。`drivers/base/bus.c:243-256`：

```c
	struct device *dev;
	int err = -EINVAL;

	dev = bus_find_device_by_name(bus, NULL, buf);
	if (!dev)
		return -ENODEV;
	if (bus_rescan_devices_helper(dev, NULL) == 0)
		err = count;
	put_device(dev);
	return err;
```

`bus_rescan_devices_helper()` 返回什么都不重要——只要非 0 就统一报 `-EINVAL`。而这个 helper 自己（`drivers/base/bus.c:686-699`）还有一层过滤：

```c
	int ret = 0;

	if (!dev->driver) {
		...
		ret = device_attach(dev);
		...
	}
	return ret < 0 ? ret : 0;
```

两个后果：设备**已经绑了驱动**时 helper 直接返回 0，`drivers_probe` 报"成功"但什么都没做；`device_attach()` 返回正数（表示已匹配）也被归成 0。真正传出来的只有负数，且到了 `drivers_probe` 那一层还会被压成 `-EINVAL`。所以 `echo dev > drivers_probe` 报 `Invalid argument` 时，**真实原因几乎一定不是"参数无效"**，要去 dmesg 找 §2.3 A 组那条 `probe of %s failed with error %d`。

**`coredump`** —— `drivers/base/dd.c:423-431`，写入任何内容都触发 `dev->driver->coredump(dev)`，返回 `count`（永远成功）。这个属性只在两个条件同时满足时才存在（`drivers/base/dd.c:452-455`）：

```c
	if (!IS_ENABLED(CONFIG_DEV_COREDUMP) || !dev->driver->coredump)
		return 0;

	ret = device_create_file(dev, &dev_attr_coredump);
```

即需要 `CONFIG_DEV_COREDUMP=y` **且**驱动实现了 `.coredump`。所以"这个设备没有 coredump 文件"先查驱动有没有实现，别先怀疑配置。移除路径在 `drivers/base/dd.c:474-475`，条件是 `drv->coredump` 非空——与创建条件不完全对称（少了 `IS_ENABLED` 那一半），但 `device_remove_file()` 对不存在的文件是安全的。

**`uevent`** —— `drivers/base/core.c:2689` 走 `kobject_synth_uevent()`，失败时**先打一条可见的 `dev_err`**（`:2692`）再返回错误码。这是七个可写点里唯一会主动报错到 dmesg 的。同名属性在驱动层也有一个（`drivers/base/bus.c:578-586`），在总线层还有一个（`drivers/base/bus.c:760`，用 `__ATTR()` 手写在 `:774`，源码注释 `:769-773` 解释了为什么不能用 `BUS_ATTR_WO` 宏——会和 `DEVICE_ATTR_WO(uevent)` 的 `store` 函数名撞车）。三个 `uevent` 属性是**三个不同层级**的对象，别混。

**`online`** —— `drivers/base/core.c:2717` 用 `strtobool()`，接受 `0/1/y/n/Y/N/yes/no/on/off/true/false`，比 `drivers_autoprobe` 严格得多。失败直接返回负数。随后要拿 `lock_device_hotplug_sysfs()`（`:2721`），拿不到就返回——所以并发热插拔期间写这个文件会失败，重试是正常做法。

### §5.3 device link 在 sysfs 里的三处身影

一条 device link 会在 sysfs 里留下**四个符号链接加一个类设备**，这是排查依赖问题时最直接的视图。建立代码在 `drivers/base/core.c:629-648`：

```
/sys/class/devlink/<link 设备名>/
    ├── supplier   → 供应方设备        建立于 :634
    ├── consumer   → 消费方设备        建立于 :638
    ├── status, auto_remove_on, runtime_pm, sync_state_only   （§5.1）
/sys/devices/<供应方>/consumer:<消费方总线名>:<消费方设备名>  → link 设备   建立于 :642-643
/sys/devices/<消费方>/supplier:<供应方总线名>:<供应方设备名>  → link 设备   建立于 :647-648
```

命名格式是 `snprintf(buf, len, "consumer:%s:%s", dev_bus_name(con), dev_name(con))` 和对称的 `supplier:...`。

实用查法：

| 想知道 | 命令 |
|---|---|
| 这个设备在等谁 | `ls -d /sys/devices/.../supplier:*` |
| 谁在等这个设备 | `ls -d /sys/devices/.../consumer:*` |
| 每条链接的状态 | `for f in /sys/devices/.../supplier:*; do echo "$f -> $(cat $f/status)"; done` |
| 全系统一次看完 | `grep . /sys/class/devlink/*/status` |

⚠️ `devlink` 这个类由 `postcore_initcall(devlink_class_init)` 注册（`drivers/base/core.c:703-717`）。类名是 `devlink`，**和网络子系统的 devlink（`/sys/class/net/` 那套、`include/trace/events/devlink.h`）完全无关**，只是撞名。搜资料时极易搞混。

### §5.4 `/sys/class/devcoredump/`

`CONFIG_DEV_COREDUMP=y` 时由 `drivers/base/devcoredump.c` 提供。类定义在 `drivers/base/devcoredump.c:244-250`，`.name = "devcoredump"`。

| 路径 | 实现 | 说明 |
|---|---|---|
| `/sys/class/devcoredump/devcd<N>/data` | `drivers/base/devcoredump.c:120-151` 的 bin attribute | 读走 dump；**写任何内容立刻销毁这个 dump** |
| `/sys/class/devcoredump/disabled` | `drivers/base/devcoredump.c:189-236`，`CLASS_ATTR_RW` | 全局开关，写非 0 会同步销毁所有现存 dump（`:230-232`） |

设备名格式 `devcd%d`（`drivers/base/devcoredump.c:378`）。⚠️ 有一个五分钟的自动过期：`DEVCD_TIMEOUT` 定义为 `HZ * 60 * 5`（`drivers/base/devcoredump.c:21-22`），注释直说 "if data isn't read by userspace after 5 minutes then delete it"。所以出问题后**必须在五分钟内把 dump 捞走**，否则内核自己删掉。`disabled` 的语义是单向的：源码注释 `drivers/base/devcoredump.c:224-225` 说明写 1 之后不能再写 0 恢复，"This is intentional"。

---

## §6 tracepoint：只有一个

### §6.1 完整清单

`drivers/base/` 自己定义的 tracepoint 只有一个。定义文件是子系统本地的 `drivers/base/trace.h`（不在 `include/trace/events/` 下），`TRACE_SYSTEM` 名为 `dev`（`drivers/base/trace.h:9-10`）。事件类 `devres` 在 `:19-39`，唯一的事件 `devres_log` 在 `:41-44`。

| tracefs 路径 | 唤起点 | 数据来源 |
|---|---|---|
| `/sys/kernel/tracing/events/dev/devres_log/` | `drivers/base/devres.c:70` | `devres_log()`，被每一次 devres 节点的申请/释放调用 |

编译门：`obj-$(CONFIG_TRACING) += trace.o`（`drivers/base/Makefile:36`），并且需要 `CFLAGS_trace.o := -I$(src)`（`:35`）才能让 `define_trace.h` 找到本地头文件——这一行是 `TRACE_INCLUDE_PATH .`（`drivers/base/trace.h:50-51`）的配套，缺了会编译失败。

### §6.2 字段清单

`drivers/base/trace.h:22-29` 的 `TP_STRUCT__entry`：

| 字段 | 类型 | 来源 | 备注 |
|---|---|---|---|
| `devname` | `__string` | `dev_name(dev)` | 用 `__assign_str()` 拷贝进 ring buffer，安全 |
| `dev` | `struct device *` | 原始指针 | ⚠️ 只是数值，事件消费时对象可能已释放 |
| `op` | `const char *` | 调用方传的操作串 | ⚠️ **只存指针不拷贝字符串**（`:32` 是 `__entry->op = op`，不是 `__assign_str`）；能工作是因为实参都是编译期常量串 |
| `node` | `void *` | devres 节点地址 | 用于把 ADD 和 REL 配对 |
| `name` | `const char *` | `node->name` | ⚠️ 同 `op`，只存指针 |
| `size` | `size_t` | `node->size` | 净载荷字节数，不含 `struct devres` 头 |

`TP_printk` 格式（`drivers/base/trace.h:37-38`）：`"%s %3s %p %s (%zu bytes)"` → `<设备名> <op> <节点地址> <资源名> (<N> bytes)`。这与 §1.3 那条 `dev_err` 的格式几乎一致（`drivers/base/devres.c:60-61` 是 `"DEVRES %3s %p %s (%zu bytes)\n"`），差别只在前缀。两个出口共用同一个 `devres_log()` 入口（`drivers/base/devres.c:67-72`），所以**看到 tracepoint 有输出而 dmesg 没有，只说明 `devres.log` 是 0**，不说明有两套逻辑。

### §6.3 没有 `TP_CONDITION`

`drivers/base/trace.h` 里没有 `TP_CONDITION`，也没有 `DEFINE_EVENT_CONDITION`。所以这个 tracepoint 一旦在 tracefs 里 enable，**每一次 devres 操作都会产生一条记录**，无过滤。启动阶段的量级是每设备几十到几百条。要限制范围只能用 tracefs 自己的 filter，例如：

```
echo 'size > 4096' > /sys/kernel/tracing/events/dev/devres_log/filter
```

### §6.4 关于"成对性"

`op` 串是运行时传进来的，`drivers/base/devres.c` 里共 5 个调用点、4 种取值（`grep -n 'devres_log(' drivers/base/devres.c`，减去 `:67`/`:70` 那两行定义与转发）：

| `op` | 调用点 | 含义 |
|---|---|---|
| `ADD` | `drivers/base/devres.c:131` | 新节点挂上设备 |
| `REPLACE` | `drivers/base/devres.c:139` | 同名节点被替换 |
| `REM` | `drivers/base/devres.c:359`、`:644` | 从链表摘下（未必立即释放）；后者是 group 头 |
| `REL` | `drivers/base/devres.c:501` | 真正调 release 回调 |

⚠️ `REM` 和 `REL` 是两件事：`REM` 只摘链表，用于 `devres_remove()` 这类"取回所有权"的路径；`REL` 才是调用 `release()`。把 `REM` 当成"释放了"会数错。

配对的判据不是"有没有 `_complete` 事件"——这个子系统只有一个事件，ADD 和 REL 是同一个事件的不同 `op` 值，靠 `node` 字段（节点地址）配对。⚠️ 两个必须注意的点：

- **地址会被复用。** `node` 是 `kmalloc` 回来的地址，一个 ADD/REL 对闭合之后同一地址可能被下一次申请拿到。按 `node` 分组时必须按时间顺序配对，不能全局 group by。
- **REL 缺失不一定是泄漏。** 设备解绑时 `devres_release_all()`（`drivers/base/dd.c:530` 调用）会批量释放，每一个都会产生 REL。真正的"缺 REL"只发生在设备从未解绑的情况下——而这在正常运行的系统上是常态。**判断泄漏要看的是同一个设备的多次 probe/remove 循环之间 ADD 与 REL 的数量差**，不是单次启动的绝对差值。

配套地，devres 的提交阶段确实有一个返回值黑洞，但影响可控：`devres_add()` 声明为 `void devres_add(struct device *dev, void *res)`（`include/linux/device.h:182`），无法报错。真正会失败的是 `devres_alloc()`（宏，`include/linux/device.h:172-173`，实体是 `__devres_alloc_node()`，`:170-171`），它返回指针，NULL 必须由调用方检查。批量释放函数 `devres_release_all()` 返回 `int`（`drivers/base/base.h:157`），但它只声明在 `drivers/base/base.h` 里——**是核心内部函数，外部驱动看不到也调不到**；调用点是 `drivers/base/dd.c:530` 的 `device_unbind_cleanup()`，返回值在那里被丢弃。

---

## §7 错误码分诊

### §7.1 核心八个文件里的错误码分布

统计范围显式列出，就是这八个文件（这也是 §0.1 那张表的同一批）：

```
drivers/base/core.c  drivers/base/bus.c    drivers/base/driver.c  drivers/base/class.c
drivers/base/dd.c    drivers/base/platform.c  drivers/base/devres.c  drivers/base/property.c
```

| 错误码 | 出现次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | 55 | 参数校验，语义最杂，不具备诊断价值 |
| `-ENOMEM` | 32 | 分配失败 |
| `-EPROBE_DEFER` | 30 | 见 §7.3；其中相当一部分是注释和比较，不是返回点 |
| `-ENODEV` | 22 | "不是我的设备" 或 "找不到" |
| `-ENXIO` | 21 | 资源（尤其是 IRQ）不存在 |
| `-EAGAIN` | 9 | fw_devlink best-effort 的内部信号，见 §7.2 |
| `-EBUSY` | 6 | 见下 |
| `-ETIMEDOUT` | 2 | 只在 `driver_deferred_probe_check_state()` 里 |
| `-EEXIST` / `-ENOSYS` / `-EOPNOTSUPP` / `-EDEADLK` | 0 | 这四个在这批文件里不出现 |

⚠️ 口径必须说清，否则数字无法复核：这里数的是**出现次数**（`grep -ho -- "-EINVAL\b" <文件列表> | wc -l`），不是"返回点数"，也不是"行数"。注释里的 `-EPROBE_DEFER`、`switch` 的 `case` 标签、`if (ret == -EPROBE_DEFER)` 这类比较全都计入。所以这张表只能用来判断"哪些错误码在这个子系统里根本不出现"（`-EEXIST` 那一行才是它最有用的信息），不能用来估计"多少条失败路径"。

`-EBUSY` 的 6 处逐个点名，因为它是少数几个语义清晰的：

| 位置 | 语义 |
|---|---|
| `drivers/base/driver.c:243` | 同名驱动已在这条总线上注册（前一行 `:241-242` 有可见的 `pr_err`） |
| `drivers/base/dd.c:608` | probe 期间发现设备已经被绑定 |
| `drivers/base/dd.c:745` | `driver_probe_done()`：还有 probe 在跑 |
| `drivers/base/dd.c:771` | `__driver_probe_device()`：设备已被标记 dead 或已有驱动 |
| `drivers/base/dd.c:800` | kerneldoc 文字，不是返回点 |
| `drivers/base/core.c:4186` | `device_supports_offline(dev) && !dev->offline`，离线检查 |

### §7.2 被改写的错误码：四条路径

这是本篇最需要背下来的一节。**驱动返回的错误码到达用户态之前会被改四次**，每一次都丢失信息。

**改写 1：probe 的错误码被取反成正数。** `drivers/base/dd.c:650-654`：

```c
		/*
		 * Return probe errors as positive values so that the callers
		 * can distinguish them from other errors.
		 */
		ret = -ret;
		goto probe_failed;
```

于是 `really_probe()` 对"驱动 probe 失败"返回的是 `+EIO` 这种**正数**。`driver_probe_device()` 的 kerneldoc 明确写了这件事（`drivers/base/dd.c:800-802`）："a positive (inverted) error code for failures from the ->probe method"。配套地 `drivers/base/dd.c:816` 必须同时判两种形式：

```c
	if (ret == -EPROBE_DEFER || ret == EPROBE_DEFER) {
```

⚠️ 实践含义：读 `drivers/base/dd.c` 的代码时，`ret > 0` **不代表成功**。这是这个文件里最容易读错的约定。

**改写 2：`-EAGAIN` → `-EPROBE_DEFER`。** `drivers/base/dd.c:647-648`：

```c
		if (link_ret == -EAGAIN)
			ret = -EPROBE_DEFER;
```

`-EAGAIN` 来自 `device_links_check_suppliers()` 的 best-effort 分支（`drivers/base/core.c:1126` 和 `:1145`），表示"依赖没就绪但允许先试"。试了失败之后就当成 defer 处理。所以**best-effort 阶段的失败在外部完全看不出来源**，只表现为一次普通的 defer。

**改写 3：`-EPROBE_DEFER` → `-ENXIO`。** `drivers/base/platform.c:1406-1409`：

```c
	if (drv->prevent_deferred_probe && ret == -EPROBE_DEFER) {
		dev_warn(_dev, "probe deferral not supported\n");
		ret = -ENXIO;
	}
```

这一条有可见的 `dev_warn` 陪着，所以还算好查——但 `-ENXIO` 随后会被 `call_driver_probe()` 归入"rejects match"那一档（`drivers/base/dd.c:569-573`，`pr_debug` 级），于是**最终 dmesg 里只剩那条 `probe deferral not supported`，没有任何"失败"字样**。

**改写 4：`drivers_probe` 的一切非零 → `-EINVAL`。** 见 §5.2。

另有一处不算改写但同样吞掉信息：`__device_attach_driver()`（`drivers/base/dd.c:939-946`）的注释直说：

```
	 * Ignore errors returned by ->probe so that the next driver can try
	 * its luck.
```

即自动匹配路径上，一个驱动 probe 失败之后核心会继续试下一个驱动，失败信息只留在 §2.3 A 组那条 `probe of %s failed with error %d` 里。

### §7.3 `-EPROBE_DEFER` 的四个来源

看到设备在 defer，第一件事是分清是谁在 defer。四个来源，对应四条不同的日志：

| 来源 | 判定代码 | 对应日志（级别） |
|---|---|---|
| 驱动自己 `return -EPROBE_DEFER` | `drivers/base/dd.c:565-568` | `Driver %s requests probe deferral`（dbg） |
| 总线 `match()` 返回 defer | `drivers/base/dd.c:917-925` | `Device match requests probe deferral`（dbg） |
| device link 的供应方没就绪 | `drivers/base/core.c:1139-1154` | `supplier %s not ready`（经 `dev_err_probe`，defer 时是 dbg） |
| fwnode link 的供应方还不是设备 | `drivers/base/core.c:1120-1124` | `wait for supplier %pfwP`（同上） |
| 全局阻塞（`device_block_probing()`） | `drivers/base/dd.c:590-597` | `Driver %s force probe deferral`（dbg） |

⚠️ 后三种在默认配置下**一条都看不到**（都是 dbg 级），而它们恰好是最难猜的三种。所以排查 defer 的标准第一步是：

```
echo 'file drivers/base/dd.c +p' > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/base/core.c +p' > /sys/kernel/debug/dynamic_debug/control
```

再触发一次 `echo <设备名> > /sys/bus/<bus>/drivers_probe`。

### §7.4 `driver_deferred_probe_check_state()` 的三态

供子系统显式调用的一个辅助函数（`drivers/base/dd.c:290-304`，有 `EXPORT_SYMBOL_GPL`）。它的 kerneldoc（`drivers/base/dd.c:277-289`）把三种返回值和条件写全了，代码与文档一致：

| 返回 | 条件 | 含义 |
|---|---|---|
| `-ENODEV` | `!IS_ENABLED(CONFIG_MODULES) && initcalls_done` | 全静态内核、initcall 跑完了，这个依赖永远不会出现 |
| `-ETIMEDOUT` | `!driver_deferred_probe_timeout && initcalls_done` | 超时已过（或从一开始就是 0） |
| `-EPROBE_DEFER` | 其余 | 继续等 |

两个 `-ENODEV`/`-ETIMEDOUT` 分支各带一条**默认可见**的 `dev_warn`（`drivers/base/dd.c:293`、`:298`），这是驱动模型里少见的"放弃"时会主动喊一声的地方。

配套：`deferred_probe_timeout=0` 的官方说法是 "A timeout of 0 will timeout at the end of initcalls"（`Documentation/admin-guide/kernel-parameters.txt:982`）。代码依据是两处配合——`drivers/base/dd.c:365` 只在 `driver_deferred_probe_timeout > 0` 时才排定时器，而 `:297` 的判断是 `!driver_deferred_probe_timeout && initcalls_done`，于是 0 值等价于"initcall 一结束就算超时"。

---

## §8 故障注入

**结论：`drivers/base/` 没有故障注入设施。** 没有 `fault-inject.h` 的引用，没有 `DECLARE_FAULT_ATTR`，没有 `should_fail()` 调用点。

⚠️ 验证这条结论时踩到了一个子串包含的假阳性，值得记下来当反例：

```
grep -rn 'fault-inject\|should_fail\|DECLARE_FAULT_ATTR\|fault_attr' drivers/base/
```

这条命令返回 6 条命中，全部落在 `drivers/base/cacheinfo.c` 和 `drivers/base/topology.c` 里的 `default_attrs` / `cache_default_attrs` 上——因为 `default_attrs` 这个标识符**逐字包含 `fault_attr`**（`de` + `fault_attr` + `s`）。加 `-w` 或改成 `\bfault_attr\b` 才是对的。

没有故障注入的替代物有三个，按侵入性排序：

| 手段 | 能模拟什么 | 位置 |
|---|---|---|
| `CONFIG_DEBUG_TEST_DRIVER_REMOVE` | remove 路径的所有资源释放错误、UAF | §1.4 |
| `echo 0 > drivers_autoprobe` 之后手工 `bind` | 探测时机异常、乱序绑定 | §5.2 |
| `deferred_probe_timeout=1` | 依赖永远不来的超时路径 | §3.2 |

第一个是唯一真正意义上的"注入"——它强制走一遍平时几乎不走的 remove 路径。⚠️ 它不能覆盖的场景是"probe 成功后很久才 remove"，因为它的 remove 紧跟 probe，此时驱动的很多延迟初始化还没发生。

对 devres 的引用计数/生命周期问题，替代手段是 §1.3 加 §6：把 `devres.log=1` 或 tracepoint 打开，做一轮 bind/unbind，对比 ADD 与 REL 的数量。

---

## §9 三条实战路线

### §9.1 设备在 `/sys/devices/` 下，但没有 `driver` 符号链接

```
① ls /sys/devices/.../driver              不存在 → 确认未绑定
② cat /sys/kernel/debug/devices_deferred | grep <设备名>
   ├─ 有这一行 ────────────────────→ 走 §9.2
   └─ 没有这一行 ─────────────────┐
③ cat /sys/bus/<bus>/drivers_autoprobe
   ├─ 0  → 有人关掉了自动绑定，echo 1 > 或手工 drivers_probe
   └─ 1  → 继续
④ ls /sys/bus/<bus>/drivers/ | grep <期望的驱动名>
   ├─ 不在 → 驱动没注册（模块没加载 / Kconfig 没开）
   └─ 在   → 继续
⑤ echo <设备名> > /sys/bus/<bus>/drivers/<drv>/bind
   ├─ 成功 → 之前是自动匹配没触发，查 ③ 或注册顺序
   └─ -ENODEV → match 不通过：设备名对不上，或 compatible/ID 表不匹配
                → 06-devicetree.md §2 与 §10
```

第 ② 步"没有这一行"是最关键的分叉点，原因见 §4.1 的三个原因表——首选嫌疑是 `dev->can_match` 为假，即**从来没有驱动匹配上**。

### §9.2 一直 defer

```
① 打开 dbg 日志（§7.3 那两条 dynamic_debug 命令）
② cat /sys/kernel/debug/devices_deferred        看原因串
   ├─ 有原因 → 直接读；串的开头是驱动名或总线名（§2.4）
   └─ 只有名字加一个 TAB → 该驱动没用 dev_err_probe()，转 ③
③ ls -d /sys/devices/.../supplier:*              列出它在等的所有供应方
   for f in /sys/devices/.../supplier:*; do echo "$f -> $(cat $f/status)"; done
   ├─ 有 dormant → 那个供应方自己没绑驱动，对它重跑 §9.1
   └─ 全 active/available → 依赖不是 device link 造成的，转 ④
④ cat /sys/devices/.../waiting_for_supplier
   ├─ 1 → fwnode 级依赖，供应方设备还没被创建。fw_devlink=off 二分
   └─ 0 → 驱动内部在等别的东西（clk/regulator/iommu），去那个子系统查
⑤ 二分实验：fw_devlink=off 重启
   ├─ 好了 → 是 fw_devlink 推断出了一条不该有的依赖
   └─ 没好 → 真实依赖缺失
```

⚠️ 第 ⑤ 步只是二分手段，`fw_devlink=off` 不是长期方案——关掉它会同时失去 `sync_state()` 的排序保障。

### §9.3 probe 被调用了，失败了，dmesg 里什么都没有

按 §7.2 的四条改写路径逐个排除：

| 检查 | 命令 / 依据 |
|---|---|
| 是不是返回了 `-ENODEV`/`-ENXIO`（被降级成 dbg） | 打开 `drivers/base/dd.c` 的 dbg，找 `rejects match` |
| 是不是走了 `prevent_deferred_probe` 改写 | dmesg 找 `probe deferral not supported` |
| 是不是 defer（`dev_err_probe` 走 dbg 分支） | 查 `devices_deferred` |
| 是不是被 `__device_attach_driver()` 忽略了 | 打开 dbg，`probe of %s failed with error` 那条其实是 `pr_warn`，默认可见——如果连它都没有，说明 probe 从未被调用 |

最后一行是判据：**`%s: probe of %s failed with error %d` 是 `pr_warn` 级（`drivers/base/dd.c:576-577`），默认可见。dmesg 里连它都没有，就说明 `probe()` 根本没被调用**，回到 §9.1。这是这一节唯一需要记住的事实。

---

## §10 一页速查

**先打开**

```
# 命令行
initcall_debug loglevel=8 deferred_probe_timeout=30
# 运行时
echo 'file drivers/base/dd.c +p'   > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/base/core.c +p' > /sys/kernel/debug/dynamic_debug/control
```

**四个文件**

```
/sys/kernel/debug/devices_deferred            谁在 defer + 为什么      §4.1
/sys/kernel/debug/device_component/*          component 谁没到齐       §4.2
/sys/devices/<路径>/driver                    绑上了没                 §5.1
/sys/devices/<路径>/of_node                   来自哪个 DT 节点         §5.1
```

**三条链**

```
ls -d /sys/devices/<路径>/supplier:*          我在等谁                 §5.3
ls -d /sys/devices/<路径>/consumer:*          谁在等我                 §5.3
grep . /sys/class/devlink/*/status            全系统依赖状态           §5.1
```

**四个写入点**

```
echo <设备名> > /sys/bus/<bus>/drivers/<drv>/bind     手工绑         §5.2
echo <设备名> > /sys/bus/<bus>/drivers/<drv>/unbind   手工解绑       §5.2
echo <设备名> > /sys/bus/<bus>/drivers_probe          重新匹配一遍   §5.2
echo 0        > /sys/bus/<bus>/drivers_autoprobe      关掉自动绑定   §5.2
```

**四条一定看得见的日志**

```
probe of X failed with error N        probe 返回了非 defer/ENODEV/ENXIO   §2.3
probe deferral not supported         prevent_deferred_probe 改写现场     §7.2
deferred probe timeout, ...          放弃等待                            §3.2
deferred probe pending               超时时刻的存量清单                  §3.2
```

**两个容易搞错的语义**

```
drivers_autoprobe 只看第一个字节且只认 '0'      §5.2
really_probe() 的正数返回值 = 失败              §7.2
```

---

## §11 汇总

### §11.1 覆盖边界

| 覆盖了 | 没覆盖 |
|---|---|
| `drivers/base/*.c` 的调试设施 | `drivers/base/regmap/`、`drivers/base/power/`、`drivers/base/firmware_loader/` 各自的调试面 |
| `dev_printk` 家族的可见性规则 | printk / dynamic debug 自身的实现 |
| `devices_deferred`、`device_component` | ACPI 相关的发现路径调试（arm64-only，已裁） |
| device link 的 sysfs 视图 | 网络子系统那个同名的 `devlink` |
| `dev:devres_log` 这一个 tracepoint | `rpm_*`（PM）、`devfreq`、`dma_fence` —— 它们的头文件在 `include/trace/events/` 下，属别的子系统 |

### §11.2 三句话

1. 驱动核心默认几乎不说话，**排查的第一步永远是先把 dbg 打开**，不是先读 dmesg。
2. `/sys/kernel/debug/devices_deferred` 是最重要的单一入口，但"不在里面"通常意味着连匹配都没发生，而不是"没在等"。
3. probe 的错误码在到达任何可观察出口之前会被改写四次（取反成正数、`-EAGAIN` 变 defer、defer 变 `-ENXIO`、一切变 `-EINVAL`），**看到的错误码基本不能当作驱动实际返回的那个**。

### §11.3 可重跑的数字

| 论断 | 数字 | 命令 |
|---|---|---|
| `drivers/base/Kconfig` 里的调试开关数 | 3 | `grep -c '^config DEBUG_' drivers/base/Kconfig` |
| `drivers/base/*.c` 的 debugfs 创建点 | 2 处文件 / 3 行 | `grep -rn 'debugfs_create' drivers/base/*.c` |
| `drivers/base/` 自有 tracepoint 数 | 1 | `grep -c 'DEFINE_EVENT\|^TRACE_EVENT' drivers/base/trace.h` |
| `__setup`/`early_param`/`module_param` 钩子数 | 8 | `grep -rn '^__setup(\|^early_param(\|^module_param' drivers/base/*.c`（清单见 §11.4） |
| `CONFIG_DEBUG_TEST_DRIVER_REMOVE` 全树引用点 | 1 | `grep -rn 'DEBUG_TEST_DRIVER_REMOVE' --include=*.c --include=*.h .` |
| 八个核心文件的 `-EPROBE_DEFER` 出现次数 | 30 | `grep -ho -- '-EPROBE_DEFER' <§7.1 的八文件列表> \| wc -l` |
| 其中 `-EEXIST` 出现次数 | 0 | 同上，把串换成 `-EEXIST` |
| `-EBUSY` 出现次数 | 6 | 同上；逐行清单见 §7.1 |
| `devres_log()` 的调用点 / `op` 取值数 | 5 / 4 | `grep -n 'devres_log(' drivers/base/devres.c`（减去 `:67`、`:70` 两行） |
| `dev_printk.h` 总行数 | 278 | `wc -l include/linux/dev_printk.h` |
| `_dev_<level>` 由宏展开的份数 | 7 | `grep -c '^define_dev_printk_level' drivers/base/core.c` |
| `devlink_attrs[]` 的属性数 | 4，全只读 | `sed -n '547,553p' drivers/base/core.c` |
| devcoredump 的自动过期 | 5 分钟 | `grep -n 'DEVCD_TIMEOUT' drivers/base/devcoredump.c` |

### §11.4 `drivers/base/` 的全部八个命令行/模块参数钩子

| 钩子 | 注册点 | 本篇是否覆盖 |
|---|---|---|
| `deferred_probe_timeout=` | `drivers/base/dd.c:275` | §3.2 |
| `driver_async_probe=` | `drivers/base/dd.c:851` | §3.3 |
| `fw_devlink=` | `drivers/base/core.c:1738` | §3.4 |
| `fw_devlink.strict=` | `drivers/base/core.c:1745` | §3.4 |
| `devres.log=` | `drivers/base/devres.c:54` | §3.5 |
| `sysfs.deprecated=` | `drivers/base/core.c:48` | 否，见 §11.1 |
| `devtmpfs.mount=` | `drivers/base/devtmpfs.c:60` | 否，devtmpfs 不属驱动模型核心 |
| `numa=` | `drivers/base/arch_numa.c:36` | 否，NUMA 解析寄居在此目录，与驱动模型无关 |

⚠️ 这一节数出 8 的过程踩到第二个子串包含陷阱，和 §8 那个是同一类：不加行首锚点写成 `grep -c 'early_param('` 会多数出一条，因为 `drivers/base/arch_undefined` 这类命名里……更准确地说，`drivers/base/arch_numa.c:27` 那行是

```c
static __init int numa_parse_early_param(char *opt)
```

函数名 `numa_parse_early_param` 逐字包含 `early_param(`（连同后面的左括号），于是被当成一次注册数了进去。**注册宏一律出现在行首**，所以正确写法是 `^early_param(`。

`initcall_debug`（§3.1）不在这张表里——它的实现在 `init/main.c`，不是 `drivers/base/` 的钩子，只是被 `drivers/base/dd.c:782-785` 和 `drivers/base/core.c:4850-4863` 读取。

### §11.5 阅读下一篇

| 想知道 | 去 |
|---|---|
| 这些机制为什么长这样、用大白话讲一遍 | `08-feynman.md` |
| 全套文档的入口和复习路径 | `00-overview.md` |
| 本篇每条论断的取证等级 | `99-evidence.md` |
