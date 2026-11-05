---
title: 调试方法
---

# pinctrl 的调试与观测

> **本篇的定位**：pinctrl 出问题时，你能看到哪些输出、每个输出到底在说什么、以及**哪些输出是不可靠的 oracle**。
>
> **arm64-only**：内核参数与 debugfs 路径按 arm64 常规 defconfig 描述。裁掉了什么见 `99-evidence.md` §2.7。
>
> **本篇最重要的一句话**：pinctrl 核心**没有任何 tracepoint**，而且它最有价值的三条日志全在 `dev_dbg` 级——**默认一个字都不输出**。所以排查 pinctrl 问题的第一个动作不是看 dmesg，是先把 dynamic_debug 打开（§2.3）。
>
> **本篇的示例输出没有一条是编造的**：每段"输出长什么样"都从对应的 `seq_printf` 格式串逐字推出来，格式串的行号就在旁边。真实机器上取到的字段值会不同，格式不会。

---

## §0 分诊：按症状选工具

| 症状 | 第一个动作 | 去哪节 |
|---|---|---|
| `/sys/kernel/debug/pinctrl/` 整个不存在 | 查 `CONFIG_DEBUG_FS` 和 debugfs 挂载 | §1.1 |
| `/sys/kernel/debug/pinctrl/` 存在但里面只有 3 个文件、没有控制器子目录 | 控制器驱动没调 `pinctrl_enable()` | §1.3 |
| 找不到我的控制器子目录（名字不认识） | 目录名是 `dev_name` 或 `dev_name-desc->name` | §3.1 |
| 控制器子目录里没有 `pinmux-*` / `pinconf-*` | 驱动没填 `pmxops` / `confops` | §1.3 |
| dmesg 里一条 pinctrl 相关的话都没有 | 开 dynamic_debug | §2.3 |
| 看到一条报错，想知道它由什么触发 | §4 错误码分诊 + `06-devicetree.md` §7 消息映射 | §4 |
| 想确认某个引脚现在归谁 | `pinmux-pins` | §3.9 |
| 想确认某个 config 有没有配上 | `pinconf-pins`，但它有 5 种「空行」原因 | §3.11 |
| 想在运行时改一个 mux 试试 | `pinmux-select`，但它绕过全部账本 | §3.10 |
| 想追一条 `pinctrl_select_state()` 的完整路径 | ftrace / kprobe（没有 tracepoint 可用） | §2.4 |

### §0.1 本篇不覆盖什么

| 内容 | 去哪 |
|---|---|
| 每条日志字符串背后的 DTS 写法错误 | `06-devicetree.md` §7（错误消息 → 原因映射表） |
| DTS 配错了的完整决策树 | `06-devicetree.md` §5 |
| 各个 seq_show 打印的那些字段在结构体里是什么 | `02-datastructures.md` |
| `pinctrl_enable()` 在注册时间线上的位置 | `03-internals.md` §A3 |
| gpiolib 侧的 `/sys/kernel/debug/gpio` 和 gpio tracepoint | 不在本子系统范围，见 `05-usage.md` §3 |

---

## §1 编译期开关：五道门

### §1.1 `CONFIG_DEBUG_FS`——最外面的那道门

pinctrl 的**全部**观测面都在 debugfs 里。`core.c:1960` 的 `#else /* CONFIG_DEBUG_FS */` 分支把三个初始化函数全部替换成空函数（`:1962`、`:1966`、`:1970`）：

```c
#else /* CONFIG_DEBUG_FS */
static void pinctrl_init_device_debugfs(struct pinctrl_dev *pctldev) { }   /* :1962 */
static void pinctrl_init_debugfs(void) { }                                 /* :1966 */
static void pinctrl_remove_device_debugfs(struct pinctrl_dev *pctldev) { }  /* :1970 */
#endif
```

`pinmux.c` 和 `pinconf.c` 同样把整段 debugfs 代码包在 `#ifdef CONFIG_DEBUG_FS` 里（`pinmux.c:786` 的 `#endif /* CONFIG_DEBUG_FS */`、`pinconf.c:378`）。

**关掉 `CONFIG_DEBUG_FS` 之后 pinctrl 一点观测手段都不剩**——没有 sysfs、没有 procfs、没有 tracepoint（§2.2、§5.1）。arm64 常规 defconfig 是开的。

判据：
```bash
zcat /proc/config.gz 2>/dev/null | grep -E 'CONFIG_DEBUG_FS=|CONFIG_DEBUG_PINCTRL'
mount | grep debugfs        # 没挂就 mount -t debugfs none /sys/kernel/debug
```

### §1.2 `CONFIG_DEBUG_PINCTRL`——只做一件事

`drivers/pinctrl/Kconfig:30-34`：

```
config DEBUG_PINCTRL
	bool "Debug PINCTRL calls"
	depends on DEBUG_KERNEL
	help
	  Say Y here to add some extra checks and diagnostics to PINCTRL calls.
```

**帮助文本在骗你**。全树只有一处引用它，在 `drivers/pinctrl/Makefile:4`：

```makefile
subdir-ccflags-$(CONFIG_DEBUG_PINCTRL)	+= -DDEBUG
```

也就是说它**唯一的作用**是给 `drivers/pinctrl/` 整个子树加 `-DDEBUG`，从而让所有 `dev_dbg()` 无条件编译进去并输出。它**没有**加任何 "extra checks"——`grep -rn 'CONFIG_DEBUG_PINCTRL' drivers/pinctrl/ include/linux/pinctrl/` 只有 Makefile 那一行。

重跑：
```bash
grep -rn 'CONFIG_DEBUG_PINCTRL\|DEBUG_PINCTRL' drivers/pinctrl/ include/linux/pinctrl/
# 只有：drivers/pinctrl/Makefile:4
```

实践上**不要为了看 dev_dbg 去开这个开关**——它要重新编译内核，而 §2.3 的 dynamic_debug 在运行时就能做同样的事，粒度还更细。这个开关的价值只在"内核不带 `CONFIG_DYNAMIC_DEBUG`"的场合。这条已登记在 `99-evidence.md` §2.3（文档/帮助文本与代码不符）。

### §1.3 三个"决定哪些 debugfs 文件存在"的运行期门

**注意这三道门不是 Kconfig，是运行期条件**，但它们造成的现象和"编译没开"完全一样，所以放在本节一起讲：

| 门 | 判据代码 | 不满足时缺什么 |
|---|---|---|
| **`pinctrl_enable()` 被调用过** | `core.c:2125` `pinctrl_init_device_debugfs(pctldev)` | **整个控制器子目录都不存在**——而且同一个函数在 `:2122` 才把控制器加进 `pinctrldev_list`，所以它同时也不出现在 `pinctrl-devices` 里，消费者还会拿到 `-EPROBE_DEFER`（`06-devicetree.md` §5 ①）。这三个现象是**同一个根因** |
| `pctldev->desc->pmxops != NULL` | `core.c:1932-1933` `if (pctldev->desc->pmxops) pinmux_init_device_debugfs(...)` | `pinmux-functions` / `pinmux-pins` / `pinmux-select` 三个文件都不建 |
| `pctldev->desc->confops != NULL` | `core.c:1934-1935` `if (pctldev->desc->confops) pinconf_init_device_debugfs(...)` | `pinconf-pins` / `pinconf-groups` 两个文件都不建 |

所以"我的控制器子目录里只有 `pins` / `pingroups` / `gpio-ranges` 三个文件"是一条**有意义的诊断信息**：说明这个驱动既没有 pinmux 也没有 pinconf 能力。用 `pinctrl-devices` 的两列 `yes/no` 可以一眼确认（§3.2）。

`pinctrl_enable()` 自己也可能失败提前返回：`:2115-2119` 的 `pinctrl_claim_hogs()` 出错时打 `dev_err "could not claim hogs: %i"` 然后 `return error`——**debugfs 目录和链表登记都不会发生**。

### §1.4 与观测无关但会改变行为的四个开关

| Kconfig | 位置 | 影响的观测面 |
|---|---|---|
| `CONFIG_PINMUX` | `Kconfig:14` | 整个 `pinmux.c` 是否编译；不开则 `pmxops` 无意义 |
| `CONFIG_PINCONF` | `Kconfig:22` | 整个 `pinconf.c` 是否编译 |
| `CONFIG_GENERIC_PINCONF` | `Kconfig:25`（`select PINCONF`） | `pinconf-generic.c` 是否编译 → **`pinconf-pins` 里那 23 个泛型参数名是否会显示**（§3.11） |
| `CONFIG_GPIOLIB` | 外部 | `core.c:1658-1674` 那段 `#ifdef CONFIG_GPIOLIB` → `pins` 文件里的 `<gpio号>:<chip标签>` 那一列是否有内容 |

`CONFIG_PINMUX` / `CONFIG_PINCONF` 都写成 `bool "..." if COMPILE_TEST`（`Kconfig:14`、`:22`），意思是**普通配置界面里看不到、改不了**——只能由驱动 `select`。这是有意的：由驱动决定要不要这两个子框架。

---

## §2 日志：pinctrl 的可见性等级

### §2.1 核心六个文件的日志分布

| 文件 | `dev_err`/`pr_err` | `dev_warn`/`pr_warn` | `dev_info` | `dev_dbg` | `WARN_ON`/`WARN` |
|---|---|---|---|---|---|
| `core.c` | 16 | 3 | 2 | 9 | 6 |
| `pinmux.c` | 23 | 5 | 0 | 1 | 1 |
| `pinconf.c` | 10 | 0 | 0 | 2 | 0 |
| `pinconf-generic.c` | 2 | 0 | 0 | 1 | 0 |
| `devicetree.c` | 2 | 0 | 1 | 1 | 0 |
| `pinctrl-utils.c` | 2 | 0 | 0 | 0 | 2 |

重跑：
```bash
for f in core.c pinmux.c pinconf.c pinconf-generic.c devicetree.c pinctrl-utils.c; do
  printf "%-20s err=%-3s warn=%-3s info=%-3s dbg=%-3s\n" $f \
    $(grep -c 'dev_err\|pr_err' drivers/pinctrl/$f) \
    $(grep -c 'dev_warn\|pr_warn' drivers/pinctrl/$f) \
    $(grep -c 'dev_info\|pr_info' drivers/pinctrl/$f) \
    $(grep -c 'dev_dbg\|pr_debug' drivers/pinctrl/$f)
done
```

**核心六个文件里一个 `_once` / `_ratelimited` 变体都没有**：

```bash
grep -rn '_once\|_ratelimited' drivers/pinctrl/core.c drivers/pinctrl/pinmux.c \
    drivers/pinctrl/pinconf.c drivers/pinctrl/pinconf-generic.c \
    drivers/pinctrl/devicetree.c drivers/pinctrl/pinctrl-utils.c
# 空
```

这一点有两个后果：
1. **看到一条就是发生了一次**——不需要担心"第二次被压掉了"。计数可以当事件数用。
2. 反过来，在 `pinctrl_select_state()` 的失败路径上（`core.c:1321` `"Error applying setting, reverse things back"`）如果有个消费者在循环里反复重试，**dmesg 会被刷爆**，没有任何限速。

### §2.2 可见性等级表

| 等级 | 默认能不能看到 | pinctrl 里最需要注意的 |
|---|---|---|
| `dev_err` / `pr_err`（KERN_ERR） | 能（`console_loglevel` 默认 ≥ 4） | 大部分是"驱动没实现某个回调"或"名字查不到"，见 §4 |
| `dev_warn` / `pr_warn`（KERN_WARNING） | 能 | 分两类：`core.c` 的 3 条**全部**是 debugfs 创建失败（`:1910`、`:1922`、`:1947`）——**观测面自己坏了，功能没坏**；`pinmux.c` 的 5 条里 `:426` 和 `:505` 注释明写「errors only affect debug data, so just warn」，另外 3 条（`:456`、`:515`、`:527`）是"取不到 pin desc"和"这个 pin 还被别的 setting 用着所以不释放" |
| `dev_info` / `pr_info`（KERN_INFO） | 能，但 `quiet` 启动参数会把它压到 console 之外（dmesg 里仍在） | 全核心只有 3 条，其中 `devicetree.c:174` 的 `there is not valid maps for state %s` 是**排查 DTS 的头号线索**（`06-devicetree.md` §7）。带 `quiet` 启动的产品固件上必须去 dmesg 里翻 |
| `dev_dbg` / `pr_debug`（KERN_DEBUG） | **不能** | 14 条，含全部最有价值的诊断信息，见下表 |
| `WARN_ON` / `WARN` | 能（会打栈回溯） | 9 处，全部是"内核内部不该发生"，见 §4.5。其中 8 处是裸 `WARN_ON`，dmesg 里只有行号 |

**被藏起来的 14 条 `dev_dbg`**（这张表是完整的）：

| 位置 | 消息（逐字） | 它在告诉你什么 |
|---|---|---|
| `core.c:242` | `registered pin %d (%s) on %s` | 每个引脚注册各一条。500 个引脚的 SoC 会刷 500 行——开 dynamic_debug 时按 `func pinctrl_register_one_pin` 单独控制它 |
| `core.c:733` | `found group selector %u for %s` | group 名字 → selector 解析成功。**失败没有对应的 dbg**，只有调用方的 err |
| `core.c:1136` | `obtain a copy of previously claimed pinctrl` | 同一个 device 第二次 `pinctrl_get()`，返回的是已有 handle 的引用而不是新建 |
| `core.c:1222` | `using pinctrl dummy state (%s)` | 命中了空状态 `pinctrl-N = <>`（`06-devicetree.md` §1.5）。这是"什么都不做"被当成成功的地方 |
| `core.c:1425` | `add %u pinctrl maps` | 一次 `pinctrl_register_mappings()` 加了多少条 map |
| `core.c:2048` | `try to register %d pins ...` | 注册开始。同一函数 `:2051` 的 err `error during pin registration` 是它的失败对照 |
| `core.c:2077` | `no hogs found` | 控制器自己没有 hog，**正常**（`pinctrl_claim_hogs()` 在 `-ENODEV` 上 `return 0`，`:2079`） |
| `core.c:2092` | `failed to lookup the default state` | 控制器自己没有 default hog 状态，**正常**——注意这里只是 dbg，而**选中失败**是 err（`:2097` `failed to select default state`） |
| `core.c:2105` | `failed to lookup the sleep state` | 同上，sleep hog |
| `pinmux.c:124` | `request pin %d (%s) for %s` | **唯一能看到"某个 pin 在什么时刻被谁申请了"的实时日志**——`pinmux-pins` 只给最终快照，这条给过程 |
| `pinconf.c:62` | `cannot get pin configuration, .pin_config_get missing in driver` | **`pinconf-pins` 什么都显示不出来的真正原因**（§3.11） |
| `pinconf.c:88` | `cannot get configuration for pin group, missing group config get function in driver` | 同上，`pinconf-groups` 版 |
| `pinconf-generic.c:226` | `found %s with value %u` | DTS 里每条 config 属性解析成 `(param, arg)` 各一条——**核对"参数取值有没有被 `default_value` 顶替"的唯一手段**（`06-devicetree.md` §5 ⑤） |
| `devicetree.c:215` | `no of_node; not parsing pinctrl DT` | 这个设备根本没有 DT 节点（`of_have_populated_dt()` 为真时才打） |

表外的第 15~20 条：`drivers/base/pinctrl.c` 里还有 6 条 `dev_dbg`（`:34` `no pinctrl handle`、`:42` `no default pinctrl state`、`:51` `no init pinctrl state`、`:60` `failed to activate initial pinctrl state`、`:74` `no sleep pinctrl state`、`:80` `no idle pinctrl state`）。严格说它们不在 `drivers/pinctrl/` 里，但**排查"我的设备为什么完全没生效"时它们是第一手信息**，开 dynamic_debug 时要一起打开（§2.3 ③）。语义见 `06-devicetree.md` §7。

### §2.3 打开 dynamic_debug——排查 pinctrl 的第一个动作

```bash
# 前提：CONFIG_DYNAMIC_DEBUG=y（arm64 常规 defconfig 开）
mount -t debugfs none /sys/kernel/debug 2>/dev/null

# ① 只开 pinctrl 核心（推荐起点，输出量可控）
echo 'file drivers/pinctrl/core.c +p'          > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/pinctrl/pinmux.c +p'        > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/pinctrl/pinconf.c +p'       > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/pinctrl/devicetree.c +p'    > /sys/kernel/debug/dynamic_debug/control

# ② 只想看"谁申请了哪个 pin"这一条
echo 'func pin_request +p' > /sys/kernel/debug/dynamic_debug/control

# ③ 只想看 drivers/base 侧"设备有没有 pinctrl"
echo 'file drivers/base/pinctrl.c +p' > /sys/kernel/debug/dynamic_debug/control

# ④ 确认打开了哪些（第 4 列出现 =p 就是开了）
grep 'drivers/pinctrl' /sys/kernel/debug/dynamic_debug/control | grep '=p'

# ⑤ 关掉
echo 'file drivers/pinctrl/* -p' > /sys/kernel/debug/dynamic_debug/control
```

**pinctrl 的绝大部分动作发生在 `core_initcall` 之后、各驱动 probe 期间**（`core.c:2341` 是 `core_initcall(pinctrl_init)`，注释 `:2340` 写「init early since many drivers really need to initialized pinmux early」）。等系统起来再开 dynamic_debug，**该看的早就过去了**。要抓启动期的，用内核命令行：

```
dyndbg="file drivers/pinctrl/* +p; file drivers/base/pinctrl.c +p" ignore_loglevel
```

`ignore_loglevel` 是必须的——否则 KERN_DEBUG 到不了 console。

### §2.4 pinctrl 核心没有 tracepoint

```bash
# ① 核心目录里没有任何 TRACE_EVENT / trace_ 调用
grep -rn 'TRACE_EVENT\|trace_[a-z_]*(' drivers/pinctrl/*.c
# 只有 pinctrl-pistachio.c 里一堆名字含 "trace" 的 pin group（MIPS 调试口），不是 tracepoint

# ② include/trace/events/ 下没有 pinctrl 相关头文件
ls include/trace/events/ | grep -iE 'pinctrl|pinmux|pinconf'
# 空。只有 gpio.h（属于 gpiolib）和 fsi_master_gpio.h

# ③ 运行期确认
ls /sys/kernel/debug/tracing/events/ | grep -i pin
# 空
```

替代手段，按侵入性递增：

| 手段 | 命令 | 能拿到什么 |
|---|---|---|
| dynamic_debug（§2.3） | 见上 | 已有的 14 条 dbg，零成本 |
| ftrace `function_graph` | `echo pinctrl_select_state > /sys/kernel/debug/tracing/set_graph_function; echo function_graph > .../current_tracer` | 完整调用树 + 每层耗时。看 `pinmux_enable_setting` → `pin_request` → 驱动 `set_mux` 的嵌套关系最好用 |
| ftrace `set_ftrace_filter` | `echo 'pin_request pin_free pinmux_enable_setting' > .../set_ftrace_filter; echo function > .../current_tracer` | 只跟踪几个关心的函数，输出量小 |
| kprobe 取返回值 | `perf probe 'pinctrl_dt_to_map%return $retval'` 然后 `perf record -e probe:*` | 区分 `-ENODEV`（放行）和 `-EPROBE_DEFER`（永久推迟），这是 `06-devicetree.md` §2.2 那张结论表在运行期的验证手段 |
| kprobe 取参数 | `perf probe 'pin_request pin owner:string'` | 看到"谁申请了哪个 pin"，等价于打开 `pinmux.c:124` 的 dbg |

**`gpiolib` 有 tracepoint，pinctrl 没有**（`include/trace/events/gpio.h`）。如果你的问题是"GPIO 借用引脚"，`gpio:gpio_direction` / `gpio:gpio_value` 这两个 tracepoint 能覆盖 gpiolib 那一侧，但**跨不过 `pinctrl_gpio_request()` 这条边界**——pinctrl 侧还是只能用上表的手段。

---

## §3 debugfs：11 个文件

**总数是 11 个，不是更多**——3 个全局 + 8 个每控制器。判据（一条命令列全）：

```bash
grep -rn 'debugfs_create_file' drivers/pinctrl/core.c drivers/pinctrl/pinmux.c drivers/pinctrl/pinconf.c
```

```
/sys/kernel/debug/pinctrl/
├── pinctrl-devices          0444   core.c:1952    §3.2
├── pinctrl-maps             0444   core.c:1954    §3.3
├── pinctrl-handles          0444   core.c:1956    §3.4
└── <控制器名>/                     core.c:1918    §3.1（目录名规则）
    ├── pins                 0444   core.c:1926    §3.5
    ├── pingroups            0444   core.c:1928    §3.6
    ├── gpio-ranges          0444   core.c:1930    §3.7
    ├── pinmux-functions     0444   pinmux.c:779   §3.8   ← 需要 pmxops
    ├── pinmux-pins          0444   pinmux.c:781   §3.9   ← 需要 pmxops
    ├── pinmux-select        0200   pinmux.c:783   §3.10  ← 需要 pmxops，唯一可写
    ├── pinconf-pins         0444   pinconf.c:373  §3.11  ← 需要 confops
    └── pinconf-groups       0444   pinconf.c:375  §3.12  ← 需要 confops
```

全部 0444（只读）**除了 `pinmux-select` 是 0200（只写）**——`cat pinmux-select` 会得到 `Permission denied`，这不是权限配错，是设计如此（`pinmux.c:783`）。

### §3.0 先建立"每个文件的可靠性"的整体判断

后面每节都会给一个可靠性判据。先把结论摊在一起，避免你花时间去看一个本来就不可信的输出：

| 文件 | 数据从哪来 | 可靠性 |
|---|---|---|
| `pinctrl-devices` | 核心自己的 `pinctrldev_list` | **可信**。列表里有就是 `pinctrl_enable()` 跑过了 |
| `pinctrl-maps` | 核心自己的 `pinctrl_maps` 链表 | **可信**，而且是"DTS 被解析成了什么"的唯一权威视图 |
| `pinctrl-handles` | 核心自己的 `pinctrl_list` | **可信**，含"当前是哪个 state" |
| `pins` | 核心的 `pin_desc`（+ 驱动可选回调） | 引脚号/名字可信；**驱动附加的寄存器值取决于 `pin_dbg_show` 有没有实现** |
| `pingroups` | 全部来自驱动的 `pctlops` 回调 | 可信，但 group 里的引脚数依赖驱动 `get_group_pins` |
| `gpio-ranges` | 核心的 `gpio_ranges` 链表 | 可信 |
| `pinmux-functions` | 全部来自驱动的 `pmxops` 回调 | 可信 |
| `pinmux-pins` | 核心的 `mux_owner` / `gpio_owner` 账本 | **可信，但仅限"走过 `pin_request()` 的占用"**——用 `pinmux-select` 改过的 mux 不在账本里（§3.10） |
| `pinmux-select` | —（只写） | — |
| `pinconf-pins` | 向驱动**反查**硬件 | **三重不可靠**，见 §3.11 |
| `pinconf-groups` | 同上 | 同上 |

### §3.1 控制器子目录的名字不一定是你想的那个

`core.c:1904-1916`：

```c
	if (pctldev->desc->name &&
			strcmp(dev_name(pctldev->dev), pctldev->desc->name)) {
		debugfs_name = devm_kasprintf(pctldev->dev, GFP_KERNEL,
				"%s-%s", dev_name(pctldev->dev),
				pctldev->desc->name);
		...
	} else {
		debugfs_name = dev_name(pctldev->dev);
	}
```

规则只有两条：

| 条件 | 目录名 |
|---|---|
| `desc->name` 为空，**或**它和 `dev_name(dev)` 相等 | `dev_name(dev)`，例如 `1f000000.pinctrl` |
| `desc->name` 非空且**不等于** `dev_name(dev)` | `<dev_name>-<desc->name>`，例如 `1f000000.pinctrl-msmgpio` |

这就是"我 grep 不到我的控制器"的常见原因：DTS 节点名是 `tlmm`，`dev_name` 是 `f100000.pinctrl`，而驱动把 `desc->name` 设成 `msmgpio`，最后目录叫 `f100000.pinctrl-msmgpio`。**不要猜名字，直接列**：

```bash
ls /sys/kernel/debug/pinctrl/
# 对照 pinctrl-devices 的第一列（那一列是 desc->name，不是目录名！）
cat /sys/kernel/debug/pinctrl/pinctrl-devices
```

注意这两处名字**不是同一个东西**：目录名可能带 `dev_name` 前缀，而 `pinctrl-devices` 打的是**纯 `desc->name`**（`core.c:1779`）。两边对不上是正常的。

还有一条"目录建不出来"的路径：`devm_kasprintf` 失败时 `pr_warn "failed to determine debugfs dir name for %s"`（`:1910`）然后 `return`——**控制器功能完全正常，只是没有 debugfs**。`debugfs_create_dir` 失败同理（`:1922`）。这两条 warn 是"观测面坏了但功能没坏"的明确信号。

---

### §3.2 `pinctrl-devices`——先确认控制器在不在

实现：`pinctrl_devices_show()` `core.c:1770-1794`。遍历全局 `pinctrldev_list`。

输出形状（`:1774` 表头 + `:1779-1788` 每行）：

```
name [pinmux] [pinconf]
msmgpio yes yes
```

三列的含义：

| 列 | 来源 | 含义 |
|---|---|---|
| 第 1 列 | `pctldev->desc->name`（`:1779`，格式 `"%s "`） | 驱动自己起的名字，**不是** debugfs 目录名（§3.1） |
| 第 2 列 | `desc->pmxops ? "yes" : "no"`（`:1780-1783`） | 有没有 pinmux 能力 → 决定 `pinmux-*` 三个文件是否存在 |
| 第 3 列 | `desc->confops ? "yes" : "no"`（`:1784-1787`） | 有没有 pinconf 能力 → 决定 `pinconf-*` 两个文件是否存在 |

**它是本子系统最重要的那一个"在不在"判据**。因为 `pinctrl_enable()` 在同一个函数里做了三件事（`core.c:2115-2125`）：跑 hog、把控制器**加进这条链表**、建 debugfs 目录。所以：

```
控制器不在 pinctrl-devices 里
   ⇒ pinctrl_enable() 没跑完
   ⇒ 同时：没有 debugfs 子目录
   ⇒ 同时：get_pinctrl_dev_from_of_node() 找不到它
   ⇒ 同时：所有引用它的消费者永久卡在 -EPROBE_DEFER
```

这四个现象是**一个根因**，不要分头去查。对应 `06-devicetree.md` §5 ①。

反过来，控制器**在**列表里但消费者仍然拿 `-EPROBE_DEFER`，那就不是控制器的问题，去查 DTS 的 phandle 指向（`06-devicetree.md` §5 ②）。

### §3.3 `pinctrl-maps`——"DTS 到底被解析成了什么"的权威视图

实现：`pinctrl_maps_show()` `core.c:1813-1848`。遍历全局 map 链表（`for_each_maps`，`:1822`）。

每条 map 的输出（`:1823-1843`）：

```
Pinctrl maps:
device 78b6000.i2c
state default
type CONFIGS_GROUP (4)
controlling device 1000000.pinctrl
config 00000002
config 00000103
```

逐字段：

| 字段 | 格式串位置 | 说明 |
|---|---|---|
| `device %s` | `:1823` | 是**消费者**的 `dev_name`，不是控制器 |
| `state %s` | `:1823` | 状态名（`default` / `sleep` / `init` / `idle` / 自定义） |
| `type %s (%d)` | `:1823` | 名字来自 `map_type()` `core.c:1797-1811`，见下表 |
| `controlling device %s` | `:1828` | **`DUMMY_STATE` 类型不打这一行**（`:1827` 的 `if`）——少一行是正常的 |
| 后续内容 | `:1831-1841` 按 type 分派 | MUX_GROUP → `pinmux_show_map()`；CONFIGS_* → `pinconf_show_map()` |

`map_type()` 的 5 个名字（`core.c:1799-1805`，越界返回 `"UNKNOWN"` `:1808`）：

| 数值 | 名字 | 什么 DTS 写法会产生它 |
|---|---|---|
| 0 | `INVALID` | 不该出现。出现说明有人漏填 `.type` |
| 1 | `DUMMY_STATE` | 空状态 `pinctrl-0 = <>`（`06-devicetree.md` §1.5） |
| 2 | `MUX_GROUP` | 子节点里有 `function`（+ 可选 `groups`/`pins`） |
| 3 | `CONFIGS_PIN` | 子节点里有 `pins` 且有 config 属性 |
| 4 | `CONFIGS_GROUP` | 子节点里有 `groups` 且有 config 属性 |
| ≥5 | `UNKNOWN` | 同 INVALID，不该出现 |

MUX_GROUP 追加两行（`pinmux_show_map()` `pinmux.c:652-656`）：

```
group gpio30
function gpio
```
其中 `group` 为空时打 `(default)`（`pinmux.c:654` 的三元表达式）。

CONFIGS_* 追加的行（`pinconf_show_map()` `pinconf.c:236-256`）：先打 `pin ` 或 `group `（`:243`/`:246`），再打 `%s\n`（`:253`，就是 `group_or_pin` 字符串），然后由 `pinconf_show_config()` 逐条打 config。

**`config %08lx` 是个坏消息**：`pinconf_show_config()` `pinconf.c:214-234` 的逻辑是——驱动实现了 `pin_config_config_dbg_show` 就让驱动解码，**否则直接打 32 位裸十六进制**（`:231` `seq_printf(s, "%08lx", configs[i])`）。裸十六进制怎么读：

**注意方向：param 在低 8 位，argument 在高 24 位**，和直觉相反。逐字引用 `include/linux/pinctrl/pinconf-generic.h:147`：

```c
#define PIN_CONF_PACKED(p, a) ((a << 8) | ((unsigned long) p & 0xffUL))
```

配套的两个解包 inline（`:156-158`、`:161-163`）：`param = config & 0xff`、`argument = (config >> 8) & 0xffffff`。所以：

```
config 00000501
         │   └┴── 低 8 位  = param    = 0x01 = 1 = PIN_CONFIG_BIAS_DISABLE
         └─────── 高 24 位 = argument = 0x000005 = 5
config 00002d09
         │   └┴── param    = 0x09 = 9 = PIN_CONFIG_DRIVE_STRENGTH
         └─────── argument = 0x00002d = 45   ← 45 mA
```

**param 编号就是 `enum pin_config_param` 的声明次序**（`pinconf-generic.h:114-140`，从 `PIN_CONFIG_BIAS_BUS_HOLD = 0` 开始逐一 +1，到 `PIN_CONFIG_SLEW_RATE = 25`，然后跳到 `PIN_CONFIG_END = 0x7F`）。**驱动私有参数从 `PIN_CONFIG_END + 1` 往上编**，所以 `config` 的低 8 位 ≥ 0x80 就说明这是个私有参数，去驱动的 `custom_params[]` 里查（§3.11 末尾）。

名字对照表另见 `06-devicetree.md` §3.2（`dt_params[]` 的 DTS 属性名 ↔ param）。

一个实用推论：**`pinctrl-maps` 是唯一能看到"config 值在打包之后长什么样"的地方**，因此也是核对 `06-devicetree.md` §5 ⑤ 那两个陷阱（24 位截断、`default_value` 顶替）的最直接手段——比去看驱动寄存器省事。

### §3.4 `pinctrl-handles`——"谁现在处于哪个 state"

实现：`pinctrl_show()` `core.c:1851-1894`。三层嵌套：`pinctrl_list`（每个消费者一个 handle）→ `p->states` → `state->settings`。

```
Requested pin control handlers their pinmux maps:
device: 78b6000.i2c current state: default
  state: default
    type: MUX_GROUP controller 1000000.pinctrl group: i2c3_default (12) function: blsp_i2c3 (7)
    type: CONFIGS_GROUP controller 1000000.pinctrl group i2c3_default (12)config 00000002
  state: sleep
    type: MUX_GROUP controller 1000000.pinctrl group: i2c3_sleep (13) function: gpio (0)
```

逐字段：

| 行 | 格式串 | 说明 |
|---|---|---|
| 表头 | `:1857` `"Requested pin control handlers their pinmux maps:\n"` | 原文语法就是这样（少一个 "and"），别以为是自己看错 |
| `device: %s current state: %s` | `:1862-1864` | **`current state` 是本文件的核心价值**——它是唯一能看到"`pinctrl_select_state()` 最后选中了谁"的地方。`p->state` 为 NULL 时打 `none` |
| `  state: %s` | `:1867` | 这个 handle 下**所有已解析**的 state（不只当前那个） |
| `    type: %s controller %s ` | `:1872-1874` | type 名字同 `map_type()`；controller 是 `pinctrl_dev_get_name()` |
| 行尾 | `:1876-1886` 按 type 分派 | MUX_GROUP → `pinmux_show_setting()`（`pinmux.c:658-670`，打 `group: %s (%u) function: %s (%u)\n`）；CONFIGS_* → `pinconf_show_setting()`（`pinconf.c:259-288`） |

**`pinctrl-maps` 和 `pinctrl-handles` 的区别，一句话**：maps 是"DTS 解析出来的原料"（还没有绑定到具体 selector），handles 是"已经解析成 setting、可以直接执行的成品"（group/function 都已经变成了数字 selector）。**一个 map 存在但没有对应的 setting = 名字解析失败**，去看 §4 里 `pinmux_func_name_to_selector` / `pinctrl_get_group_selector` 那两条 err。

一个反直觉的形状：`pinconf_show_setting()` 打完 `group %s (%d)` **不换行**就接着让 `pinconf_show_config()` 打 `config ...`（`pinconf.c:276-287`），所以你会看到 `group i2c3_default (12)config 00000002` 这种粘在一起的输出。**这不是你的终端出问题**。

`pinconf_show_setting()` 里挂着一条 FIXME（`pinconf.c:283-286`）：

```c
	/*
	 * FIXME: We should really get the pin controller to dump the config
	 * values, so they can be decoded to something meaningful.
	 */
```

——内核自己承认这里只能打裸值。已登记在 `99-evidence.md` §2.3。

### §3.5 `pins`——控制器有哪些引脚

实现：`pinctrl_pins_show()` `core.c:1631-1687`。

```
registered pins: 150
pin 0 (GPIO_0) 0:1000000.pinctrl 
pin 1 (GPIO_1) 0:1000000.pinctrl 
```

| 部分 | 格式串 | 说明 |
|---|---|---|
| 表头 | `:1642` `"registered pins: %d\n"` | 打的是 `desc->npins`，即**声明**的引脚数 |
| `pin %d (%s) ` | `:1656` | 引脚号 + `desc->name` |
| GPIO 那一段 | `:1672` `"%u:%s "` / `:1674` `"0:? "` | 见下 |
| 驱动附加信息 | `:1678-1679` `ops->pin_dbg_show` | 寄存器值之类，**驱动没实现就什么都没有** |

**GPIO 那一列的坑**：整段包在 `#ifdef CONFIG_GPIOLIB`（`:1658-1675`）里。逻辑是先在本控制器的 `gpio_ranges` 里找这个 pin 落在哪个 range（`:1660-1666`），算出全局 gpio 号，再 `gpio_to_chip()`（`:1668`）。

| 你看到的 | 意思 |
|---|---|
| `<n>:<chip标签> ` | 这个 pin 映射到该 gpiochip 的第 n 号线（`:1672` 打的是 `gpio_num - chip->gpiodev->base`，即**芯片内偏移**，不是全局号） |
| `0:? ` | **两种完全不同的情况被打成了同一个字符串**：(a) 这个 pin 没有落在任何 gpio range 里；(b) 落在了 range 里但 `gpio_to_chip()` 返回 NULL（gpiochip 还没注册）。想区分只能看 `gpio-ranges`（§3.7）——range 里有就是 (b) |
| 这一列整个不见 | `CONFIG_GPIOLIB=n` |

`registered pins: %d` 和实际打出来的行数**可以不相等**：`:1653-1654` 对 `pin_desc_get()` 返回 NULL 的引脚 `continue`，注释 `:1652` 写「Pin space may be sparse」。**行数少于 npins 是合法的**，说明引脚号空间稀疏。

### §3.6 `pingroups`——group 里有哪些引脚

实现：`pinctrl_groups_show()` `core.c:1690-1732`。**数据全部来自驱动的 `pctlops` 回调**。

```
registered pin groups:
group: i2c3_default
pin 14 (GPIO_14)
pin 15 (GPIO_15)

group: gpio30
pin 30 (GPIO_30)

```

| 部分 | 格式串 | 说明 |
|---|---|---|
| 表头 | `:1700` `"registered pin groups:\n"` | |
| 正常一组 | `:1716` `"group: %s\n"` + `:1723` `"pin %d (%s)\n"` ×N + `:1725` 空行 | 每组之后有一个空行 |
| 出错一组 | `:1713` `"%s [ERROR GETTING PINS]\n"` | `get_group_pins` 返回非 0。**只影响显示**，不影响功能 |

`ops->get_group_pins` 是**可选**回调（`:1709` 的 `if`）。不实现时 `ret` 保持 0、`num_pins` 保持 0，于是每组只打 `group: <名字>` + 空行，**一个引脚都不列**。这不是错误，别去找"引脚丢了"。

`:1719` 有一处会**中途放弃整个文件**：

```c
				pname = pin_get_name(pctldev, pins[i]);
				if (WARN_ON(!pname)) {
					mutex_unlock(&pctldev->mutex);
					return -EINVAL;
				}
```

驱动的 `get_group_pins` 报了一个本控制器没注册的引脚号时，`cat pingroups` 会**在那一组处直接截断**并返回 `-EINVAL`（shell 上表现为 `cat: pingroups: Invalid argument`），同时 dmesg 里出现一条带栈回溯的 `WARN_ON`。**看到输出突然截断就去 dmesg 找这条 WARN**——它精确定位了是哪个驱动的 group 表写错了。

### §3.7 `gpio-ranges`——pin 号和 GPIO 号的换算表

实现：`pinctrl_gpioranges_show()` `core.c:1736-1766`。

**两种输出形状，取决于 range 是连续区间还是离散列表**（`:1747` 的 `if (range->pins)`）：

```
GPIO ranges handled:
0: 1000000.pinctrl GPIOS [0 - 149] PINS [0 - 149]          ← 连续型（range->pins == NULL）
1: 1000000.pinctrl GPIOS [150 - 152] PINS {7, 12, 30}      ← 列表型（range->pins != NULL）
```

| 形状 | 格式串 | 判据 |
|---|---|---|
| `%u: %s GPIOS [%u - %u] PINS [%u - %u]` | `:1757-1761` | `range->pins == NULL`，用 `pin_base` 线性偏移 |
| `%u: %s GPIOS [%u - %u] PINS {%u, %u, ...}` | `:1749-1754` | `range->pins != NULL`，逐个列 |

四个字段：`range->id`、`range->name`、GPIO 区间 `[base, base+npins-1]`、PIN 区间 `[pin_base, pin_base+npins-1]`。

**这个文件是排查"GPIO 号和 pin 号对不上"的唯一权威**。典型用法：DTS 里写 `gpios = <&tlmm 30 0>`，想知道 30 号 GPIO 对应哪个 pin：

```bash
cat /sys/kernel/debug/pinctrl/*/gpio-ranges
# 找到包含 30 的那条 GPIOS 区间，用 pin_base + (30 - base) 算出 pin 号
# 然后拿这个 pin 号去 pinmux-pins 查它归谁
```

**`gpio-ranges` 为空（只有表头）而 `pins` 里全是 `0:? `**：说明这个控制器没注册任何 GPIO range，`pinctrl_gpio_request()` 这条路整个走不通。判据是驱动有没有调 `pinctrl_add_gpio_range()` / `gpiochip_add_pin_range()`。

### §3.8 `pinmux-functions`——有哪些 function，各能落在哪些 group

实现：`pinmux_functions_show()` `pinmux.c:539-578`。**数据全部来自驱动的 `pmxops` 回调**。

**没有表头**（不像其他文件都有一行 `Format: ...`）——第一行就是数据：

```
function 0: gpio, groups = [ gpio0 gpio1 gpio2 ... ]
function 1: blsp_i2c3, groups = [ i2c3_default ]
function 2: COULD NOT GET GROUPS
```

| 形状 | 格式串 | 说明 |
|---|---|---|
| `function %d: %s, groups = [ %s %s ... ]` | `:567` + `:569` + `:570` | 数字是 **function selector**，就是 `pinmux-select` 和 `pinctrl-handles` 里那个 `(%u)` |
| `function %s: COULD NOT GET GROUPS` | `:562-563` | 驱动的 `get_function_groups` 返回非 0。**注意这一行的第一个 `%s` 是函数名不是编号**，形状和正常行不一样 |

**这个文件是写 DTS 时唯一的"合法值字典"**。DTS 里 `function = "blsp_i2c3"` 和 `groups = "i2c3_default"` 这两个字符串必须**逐字**出现在这里，否则就是 §4.2 那两条 `dev_err`。用法：

```bash
# 想知道某个 function 名字对不对
grep -w 'blsp_i2c3' /sys/kernel/debug/pinctrl/*/pinmux-functions
# 想知道某个 function 允许哪些 group（DTS 里 groups 只能从这个方括号里挑）
grep 'blsp_i2c3' /sys/kernel/debug/pinctrl/*/pinmux-functions
```

与 `pingroups`（§3.6）的分工：`pingroups` 是"有哪些 group、每个 group 含哪些引脚"，`pinmux-functions` 是"有哪些 function、每个 function 允许落在哪些 group"。**DTS 的 `function` + `groups` 组合合法性只能靠后者判断**——一个 group 存在（在 `pingroups` 里）不代表某个 function 能落在它上面。

### §3.9 `pinmux-pins`——每个引脚现在归谁

实现：`pinmux_pins_show()` `pinmux.c:581-649`。**这是 pinctrl 最常用的一个 debugfs 文件**，也是形状最容易看错的一个。

**表头有两种，正文也有两种，取决于驱动的 `pmxops->strict`**（`:592-597` 表头、`:616-635` 正文）。**先看表头判断自己在哪一边**：

```
Pinmux settings per pin
Format: pin (name): mux_owner|gpio_owner (strict) hog?      ← strict 控制器
```
```
Pinmux settings per pin
Format: pin (name): mux_owner gpio_owner hog?               ← 非 strict 控制器
```

**strict 控制器的正文**（`:616-627`，三个分支互斥）：

| 你看到的 | 格式串 | 含义 |
|---|---|---|
| `pin 14 (GPIO_14): device 78b6000.i2c function blsp_i2c3 group i2c3_default` | `:618` `"pin %d (%s): device %s%s"` | 被一个设备通过 mux 占用 |
| `pin 30 (GPIO_30): GPIO 1000000.pinctrl:30` | `:622` `"pin %d (%s): GPIO %s"` | 被 GPIO 占用。**只有 `mux_owner` 为空时才会走到这个分支**（`:617` 的 `if/else if`） |
| `pin 99 (GPIO_99): UNCLAIMED` | `:625` `"pin %d (%s): UNCLAIMED"` | 两个 owner 都空 |

**非 strict 控制器的正文**（`:628-635`，只有一个分支，**两个 owner 同时打**）：

```
pin 14 (GPIO_14): 78b6000.i2c (GPIO UNCLAIMED) function blsp_i2c3 group i2c3_default
pin 30 (GPIO_30): (MUX UNCLAIMED) 1000000.pinctrl:30
pin 99 (GPIO_99): (MUX UNCLAIMED) (GPIO UNCLAIMED)
```

**占位符 `(MUX UNCLAIMED)` / `(GPIO UNCLAIMED)` 只在非 strict 输出里出现**（`:631`、`:633`），而裸的 `UNCLAIMED` 只在 strict 输出里出现（`:625`）。**这三个字符串不能混着 grep**：

```bash
# 错：在非 strict 控制器上永远匹配不到
grep 'UNCLAIMED$' pinmux-pins
# 对：先看表头，再决定 grep 什么
head -2 /sys/kernel/debug/pinctrl/<ctrl>/pinmux-pins
```

**共同的两个后缀**：

| 后缀 | 格式串 | 判据 |
|---|---|---|
| ` (HOG)` | `:621`/`:634` 的三元 | `mux_owner` 等于 `pinctrl_dev_get_name(pctldev)`（`:608-610`）——即控制器把引脚占给了自己（`06-devicetree.md` §2.3） |
| ` function %s group %s` | `:640-645` | 只在 `desc->mux_setting` 非空时打（`:639`）。**没有这个后缀但有 owner，说明这个引脚是通过 `pinctrl_gpio_request()` 之类的路径占的，不带 mux setting** |

**本节最关键的一条**：`arm64` 上**主流 SoC 都是非 strict**。全树只有 **29 个驱动**设 `.strict = true`：

```bash
grep -rln 'strict\s*=\s*true' drivers/pinctrl/ | wc -l      # 29
grep -rn  'strict\s*=\s*true' drivers/pinctrl/ | sed 's|drivers/pinctrl/||'
```
名单里 arm64 上会碰到的主要是：aspeed（g4/g5/g6）、mediatek moore、renesas（rza1/rza2/rzg2l/rzv2m）、starfive jh7100、sunxi、uniphier、visconti、apple-gpio。**qcom `pinctrl-msm.c` 没有 `strict`，所以是非 strict** —— 这也是本文档集其他篇用 qcom 举例时对应的形态。

strict 与非 strict 的**语义**差别（不只是显示差别）在 `pinmux.c:127-140` 的冲突判断里：

```c
	if ((!gpio_range || ops->strict) &&
	    desc->mux_usecount && strcmp(desc->mux_owner, owner)) {  /* :129-130 */
	...
	if ((gpio_range || ops->strict) && desc->gpio_owner) {        /* :136-137 */
```
两条都打同一条 err `"pin %s already requested by %s; cannot claim for %s\n"`（`:131`、`:138`）。**非 strict 时 mux 占用和 GPIO 占用可以共存**（第一个 `if` 里 `!gpio_range` 为假、`ops->strict` 为假 → 整条跳过）；strict 时任何一方占了另一方就拿不到。

### §3.10 `pinmux-select`——唯一可写的文件，也是最危险的

实现：`pinmux_select()` `pinmux.c:678-761`，注册在 `:783`，**权限 0200（只写）**。

```bash
# 语法：group 在前，function 在后，中间一个空白
echo "i2c3_default blsp_i2c3" > /sys/kernel/debug/pinctrl/<ctrl>/pinmux-select
```

**参数顺序是 group-function，不是 function-group**。判据在解析代码里，逐句对照：

| 步骤 | 代码 | 说明 |
|---|---|---|
| 长度上限 | `:677` `#define PINMUX_SELECT_MAX 128` | 超长直接 `-EINVAL` |
| 取 group 名 | `:704` `gname = strstrip(buf)` → 空则 `-EINVAL` | **第一个 token 是 group** |
| 找分隔符 | `:711` 扫空白，找不到则 `-EINVAL` | 两个 token 之间必须有空白 |
| 取 function 名 | `:718` `fname = skip_spaces(fname + 1)`；`:720` 空则 `-EINVAL` | 第二个 token 是 function |
| 查 function | `:724` `pinmux_func_name_to_selector` → err `invalid function %s in map table`（`:726`） | |
| 查该 function 的 group 列表 | `:733` `get_function_groups` | |
| 校验 group 在列表里 | `:739` `match_string` → err | group 必须是这个 function 允许的（见 §3.8） |
| 查 group selector | `:745` `pinctrl_get_group_selector` → err | |
| **直接执行** | `:751` `ret = pmxops->set_mux(pctldev, fsel, gsel);` → err `set_mux() failed: %d`（`:752`） | 见下 |

**它绕过了 `pin_request()` 和全部账本**。正常路径是 `pinmux_enable_setting()` → 对 group 里每个引脚 `pin_request()` → 检查冲突 → 设置 `mux_owner` / `mux_usecount` / `mux_setting` → 才调 `set_mux()`。而 `pinmux-select` **只做最后一步**。四个后果：

| 后果 | 表现 |
|---|---|
| **不做冲突检查** | 你可以把一组正在被 i2c 用着的引脚直接切成别的 function，`pin_request()` 的 `already requested by` 检查完全不会触发。**i2c 会当场挂掉，而且没有任何日志** |
| **`pinmux-pins` 不更新** | 硬件已经变了，但 `mux_owner` / `mux_setting` 还是旧的 → `pinmux-pins` **继续显示旧的 owner 和旧的 function/group**。这是本子系统最严重的"观测面撒谎"的地方 |
| **`pinctrl-handles` 不更新** | 同理，`current state` 还是原来那个 |
| **不可撤销** | 没有配对的"还原"操作。想回去只能再 `echo` 一次原来的 group/function（前提是你 `echo` 之前记下来了） |

所以它的正确用法只有一个：**在开发板上快速试"这组引脚切到那个 function 之后硬件通不通"**。判定标准是外设行为或示波器，**不是 debugfs 输出**。试完记得改 DTS 走正规路径。

`pinmux_select_open` 用的是 `single_open(file, NULL, ...)`，`pinmux_select_ops` 只有 `.write` 没有 `.read`（`:764-773`）——这就是 `cat` 会失败的原因。

### §3.11 `pinconf-pins`——三重不可靠的 oracle

实现：`pinconf_pins_show()` `pinconf.c:302-330` → `pinconf_dump_pin()` `:291-299` → `pinconf_generic_dump_pins()` `pinconf-generic.c:99-127` +（可选）驱动的 `pin_config_dbg_show`。

```
Pin config settings per pin
Format: pin (name): configs
pin 14 (GPIO_14): input bias pull up (2 ohms), input schmitt enabled, output drive strength (8 mA)
pin 15 (GPIO_15): 
pin 16 (GPIO_16): ERROR READING CONFIG SETTING 7 
```

表头 `:307-308`；每行前缀 `:322` `"pin %d (%s): "`。

**"这一行是空的"有 5 种完全不同的原因，输出上分不出来**：

| # | 原因 | 判据 |
|---|---|---|
| ① | 驱动没设 `confops->is_generic` | `pinconf-generic.c:112` `if (!ops->is_generic) return;` —— **直接返回，一个字都不打**。判据：`grep -n 'is_generic' <驱动.c>` |
| ② | 驱动没实现 `pin_config_get` | `pinconf.c:60-65`：打一条 **dev_dbg**（默认不可见）`cannot get pin configuration, .pin_config_get missing in driver` 然后返回 `-ENOTSUPP` → 被 ③ 吃掉 |
| ③ | 驱动对这个参数返回 `-EINVAL` 或 `-ENOTSUPP` | `pinconf-generic.c:75-76` `if (ret == -EINVAL \|\| ret == -ENOTSUPP) continue;`，注释写「These are legal errors」——**静默跳过**。所以"没有 `bias pull up` 这一项"**不代表**没上拉，只代表**驱动不肯回答** |
| ④ | 该引脚确实所有参数都是默认值 | 真正的"没配置"。**和 ①②③ 在输出上无法区分** |
| ⑤ | `CONFIG_GENERIC_PINCONF=n` | `pinconf-generic.c` 整个没编进去，`pinconf_generic_dump_pins` 是空实现 |

唯一能把 ② 从其他情况里分出来的手段：**打开 dynamic_debug 看有没有那条 `pinconf.c:62`**（§2.3）。① 和 ⑤ 靠读驱动源码 / 查 config。③ 和 ④ **在运行期无法区分**。

**能打出来的 23 个参数名**（`conf_items[]` `pinconf-generic.c:29-53`，逐个 `PCONFDUMP`）。这 23 个和 `06-devicetree.md` §3.2 里 `dt_params[]` 去重后的 23 个是**同一个集合**——也就是说 `enum pin_config_param` 的 25 个成员里，有 **2 个既不能从 DTS 写、也不会被 dump**：

| 漏掉的参数 | 全树消费者 | 说明 |
|---|---|---|
| `PIN_CONFIG_MODE_PWM` | 只有 `pinctrl-cy8c95x0.c:717`、`:804` | 该驱动自己走私有路径 |
| `PIN_CONFIG_PERSIST_STATE` | `gpiolib.c:2228`、`:2592`（`gpiod_set_transitory()`）、`pinctrl-cy8c95x0.c:736`、`sunplus/sppctl.c:492`、`gpio-aspeed.c:973`、`gpio-aspeed-sgpio.c:492` | **只能从 gpiolib 侧的 `gpiod_set_transitory()` 设**，DTS 写不了、debugfs 看不到 |

**每一项的输出形状**（`pinconf_generic_dump_one()` `pinconf-generic.c:57-96`）：

| 形状 | 代码 | 例子 |
|---|---|---|
| `<display>` | `:87` `seq_puts(s, items[i].display)` | `input schmitt enabled`（`has_arg = false` 的 10 项） |
| `<display> (<arg> <unit>)` | `:89-93`，仅当 `items[i].has_arg` 且 `format` 非空 | `output drive strength (8 mA)`、`input bias pull up (2 ohms)`、`input debounce (100 usec)` |
| `<display> (<arg>)` | `:94-95`，`has_arg` 但 `format` 为 NULL | 只有两项：`slew rate (1)` 和 `skew delay (5)`（`pinconf-generic.c:51-52`）——**数字后面没有单位，靠驱动自己定义语义** |
| 多项之间 `, ` | `:83-85` `print_sep` | |
| `ERROR READING CONFIG SETTING %d ` | `:79-81` | 驱动返回了 `-EINVAL`/`-ENOTSUPP` **以外**的错误。`%d` 是 **`conf_items[]` 里的下标**，不是参数号——要查是哪个参数得去数 `pinconf-generic.c:29-53` 的第 `%d+1` 个 `PCONFDUMP` |

**两个不同参数共用同一个 display 字符串**：`PIN_CONFIG_DRIVE_STRENGTH`（`pinconf-generic.c:39`）和 `PIN_CONFIG_DRIVE_STRENGTH_UA`（`:40`）都打 `output drive strength`，**只有括号里的单位不同**（`mA` vs `uA`）。所以 `grep 'output drive strength'` 会同时命中两个语义不同的参数，**必须连单位一起看**。对应的 DTS 属性是 `drive-strength` 和 `drive-strength-microamp`（`06-devicetree.md` §3.2）。

驱动私有参数也会打，但**有两个条件同时满足**才行（`pinconf-generic.c:123-124`）：`desc->num_custom_params` 非 0 **且** `desc->custom_conf_items` 非空。只填一个 = 私有参数不显示。

驱动的 `pin_config_dbg_show` 是**额外**追加的（`pinconf.c:297-298`），走的不是泛型路径——所以有些控制器上你会看到泛型项之后还跟着一段驱动自己格式的寄存器信息。

### §3.12 `pinconf-groups`——按 group 看配置

实现：`pinconf_groups_show()` `pinconf.c:345-364` → `pinconf_dump_group()` `:333-342`。

```
Pin config settings per pin group
Format: group (name): configs
0 (gpio0): input bias pull up (2 ohms)
1 (i2c3_default): output drive strength (2 mA)
```

表头 `:350-351`；每行前缀 `:357` `"%u (%s): "`（**selector 编号 + group 名**）。

后半段和 §3.11 完全同源——同一个 `pinconf_generic_dump_pins()`，只是传 `gname` 而不是 `pin`（`pinconf.c:339`），底层走 `pin_config_group_get()` 而不是 `pin_config_get_for_pin()`（`pinconf-generic.c:70-74`）。**§3.11 那 5 种"空行原因"全部同样适用**，对应的 dbg 是 `pinconf.c:88`（group 版）。

一个 §3.11 没有的坑：`pinconf_groups_show()` **没有加 `pctldev->mutex`**（对比 `pinconf_pins_show()` `:310` 有 `mutex_lock`）。读的时候如果驱动正在改 group 表，取到的可能是撕裂的数据。这是一处真实的不一致，已登记 `99-evidence.md` §2.3。

---

## §4 错误码分诊：同一个码，七种语义

pinctrl 核心六个文件的错误码分布（可重跑）。注意 `grep -c` 数的是**含该字符串的行数**，注释和 `==` 比较也算在内，**不等于「返回点数」**——所以下表逐行标出了哪几处不是返回点：

```bash
for c in EINVAL ENOMEM EPROBE_DEFER ENOENT ENODEV ENOTSUPP ENOSPC; do
  printf "%-14s %s\n" -$c $(grep -c -- "-$c" drivers/pinctrl/core.c drivers/pinctrl/pinmux.c \
    drivers/pinctrl/pinconf.c drivers/pinctrl/pinconf-generic.c \
    drivers/pinctrl/devicetree.c drivers/pinctrl/pinctrl-utils.c | awk -F: '{s+=$2} END{print s}')
done
```

| 错误码 | 出现次数 | 一句话语义 |
|---|---|---|
| `-EINVAL` | 47 | **七种完全不同的语义挤在一个码上**，见 §4.1–§4.7 |
| `-ENOMEM` | 23 | 分配失败，无歧义，不展开。分布：`core.c` 11 / `pinmux.c` 4 / `devicetree.c` 3 / `pinctrl-utils.c` 3 / `pinconf-generic.c` 2 |
| `-EPROBE_DEFER` | 8 | 「控制器还没就绪，稍后重试」。8 处里**只有 4 处是真的返回点**（`core.c:400`/`:444`/`:989`、`devicetree.c:135`），其余 4 处是注释和比较（`core.c:374` kerneldoc、`:1090`/`:1092` 注释、`:1094` 的 `if (ret == -EPROBE_DEFER)`） |
| `-ENOENT` | 5 | 两类：DT 解析找不到东西（`devicetree.c:307` 找不到 `#pinctrl-cells`、`:336` 找不到属性本身、`:340` cells 数为负）；radix tree 里没这个 selector（`core.c:682`、`pinmux.c:926`，都在 remove 路径） |
| `-ENODEV` | 6 | **「没有 pinctrl 配置」而不是「出错了」**——真返回点 5 处（`devicetree.c:132`/`:166`/`:235`、`core.c:982`/`:1226`），第 6 处 `core.c:2076` 是**比较**（`if (PTR_ERR(pctldev->p) == -ENODEV)`）。**多处调用方把它当成「正常，放行」**（如 `pinctrl_claim_hogs()` `core.c:2076-2079` 直接 `return 0`） |
| `-ENOTSUPP` | 4 | 驱动不支持这个操作——真返回点 3 处（`pinconf.c:64`/`:90`/`:207`），第 4 处 `pinconf-generic.c:76` 是比较。在 debugfs dump 里被静默跳过（§3.11 ③） |
| `-ENOSPC` | 2 | `pinctrl-utils.c:49`/`:69`，map 数组扩容越界 |

**`-EINVAL` 是 pinctrl 里最没有信息量的返回值**。下面按语义分组，每组给"什么时候发生 / 有没有日志 / 怎么确认"。**列的顺序大体是排查的优先顺序**：§4.1（DTS 名字写错）和 §4.8（引脚已被占用）两组占了实践中绝大多数，§4.2/§4.3 只在写新驱动或改板级代码时碰到，§4.4–§4.7 多半是"看着像错误其实不是"。

### §4.1 名字 → selector 解析失败（最常见的一组）

DTS 里写的字符串在驱动的表里找不到。这是**写 DTS 时的头号错误**。

**底层的四个查表函数全部静默返回 `-EINVAL`**，消息都在调用方打：

| 位置 | 函数 | 找不到什么 | 自己打日志吗 |
|---|---|---|---|
| `pinmux.c:337` | `pinmux_func_name_to_selector()` | function 名 | 无 |
| `core.c:622` | `pinctrl_generic_group_name_to_selector()` | group 名（泛型 group 实现） | 无 |
| `core.c:159` | `pin_get_from_name()` | pin 名 | 无 |
| `core.c:746` | `pinctrl_get_group_selector()` | group 名 | **有**，是四个里唯一一个：`does not have pin group %s`（`:743-744`） |

调用方打出来的消息才是你在 dmesg 里能看到的东西。`pinmux_map_to_setting()` 一个函数里五个返回点，**每个都有独立消息，逐字如下**：

| 位置 | 条件 | 消息（逐字） |
|---|---|---|
| `pinmux.c:352` | `!pmxops`——控制器没实现 pinmux | `does not support mux function`（`:351`） |
| `pinmux.c:358` | function 名查不到（上表第一行） | `invalid function %s in map table`（`:357-358`） |
| `pinmux.c:367` | `get_function_groups()` 返回错误 | `can't query groups for function %s`（`:366-367`） |
| `pinmux.c:374` | 该 function 的允许 group 列表为空（`num_groups == 0`） | `function %s can't be selected on any group`（`:371-373`） |
| `pinmux.c:383` | **DTS 里写的 group 不在这个 function 的允许列表里** | `invalid group "%s" for function "%s"`（`:380-382`） |
| `pinmux.c:393` | group 名在全局 group 表里也查不到 | `invalid group %s in map table`（`:391-392`） |

**`pinmux.c:383` 和 `:393` 的区别是排查的关键**：
- `invalid group "X" for function "Y"` = 两个名字都存在，但**配错了对**（`match_string()` 在 `get_function_groups()` 返回的列表里没找到）。去 §3.8 看这个 function 到底允许哪些 group
- `invalid group X in map table` = group 名压根不存在。去 §3.6 看 group 全集

`pinconf_map_to_setting()` 两条（`pinconf.c:113` / `:126` 走查表失败，**返回的是查表函数的 `-EINVAL` 原样透传**）：

| 消息（逐字） | 何时 |
|---|---|
| `could not map pin config for "%s"`（`pinconf.c:117-118`） | `PIN_MAP_TYPE_CONFIGS_PIN`，pin 名查不到 |
| `could not map group config for "%s"`（`pinconf.c:127-128`） | `PIN_MAP_TYPE_CONFIGS_GROUP`，group 名查不到 |

另有 `pinconf.c:135` 是同函数的 `default:` 兜底（`setting->type` 既不是 PIN 也不是 GROUP），**无日志**，归 §4.4。

**确认动作固定三步**：

```bash
# ① 把 DTS 里写的 function 名拿去比对（必须逐字相等）
grep -w '<你的function名>' /sys/kernel/debug/pinctrl/*/pinmux-functions
# ② 把 group 名拿去比对
grep -w '<你的group名>'    /sys/kernel/debug/pinctrl/*/pingroups
# ③ 确认这个 group 在这个 function 的允许列表里（§3.8）
grep '<你的function名>'    /sys/kernel/debug/pinctrl/*/pinmux-functions
```

三步全过还失败，说明**不是名字问题**，去 §4.2。

### §4.2 驱动契约校验失败（控制器根本注册不上）

发生在 `pinctrl_init_controller()` 里，**在任何引脚注册之前**。症状是控制器驱动 probe 直接失败，`pinctrl-devices` 里什么都没有。

| 位置 | 条件 | 日志 |
|---|---|---|
| `core.c:2002` | `pctldesc == NULL` | **无任何日志**——直接 `ERR_PTR(-EINVAL)` |
| `core.c:2004` | `pctldesc->name == NULL` | **同样无日志**。驱动忘填 `desc.name` 就是这条 |
| `core.c:1983` | `pctlops` 缺 `get_groups_count` 或 `get_group_name` | 调用方打 `core.c:2029` `pinctrl ops lacks necessary functions` |
| `pinmux.c:45` | `pmxops` 缺 `get_functions_count` / `get_function_name` / `get_function_groups` / `set_mux` 之一 | 就地打 `pinmux ops lacks necessary functions`（`:44`） |
| `pinmux.c:55` | 某个 function 的 `get_function_name()` 返回 NULL | 打 `pinmux ops has no name for function%u`（`:53`）——**带 selector 编号，直接定位驱动表里第几项** |
| `pinconf.c:33` | `confops` 里 `pin_config_set` 和 `pin_config_group_set` **两个都没有** | 打 `pinconf has to be able to set a pins config`（`:31`） |

前两条（`core.c:2002`/`:2004`）是**全子系统最难查的两个返回点**：`pinctrl_register()` 返回 `-EINVAL`，dmesg 里一个字都没有。写新控制器驱动时先自查 `desc.name` 有没有填。

注意 `pinmux_check_ops` / `pinconf_check_ops` 只在对应的 ops 非空时才跑（`core.c:2034`、`:2041`）——**不填 `pmxops` 是合法的**，填了但填不全才报错。

### §4.3 map 表校验失败（`pinctrl_register_mappings()`）

只影响**手工注册 map 的板级代码**，DT 路径不走这里（DT 生成的 map 不经过 `pinctrl_register_mappings`）。全部**有 `pr_err`**，而且消息里带 map 名和下标 `%d`：

| 位置 | 消息 |
|---|---|
| `core.c:1432` | `failed to register map %s (%d): no device given` |
| `core.c:1438` | `failed to register map %d: no map name given` |
| `core.c:1445` | `failed to register map %s (%d): no pin control device given`（`DUMMY_STATE` 类型豁免，`:1443`） |
| `core.c:1465` | `failed to register map %s (%d): invalid type given` |
| `pinmux.c:68` | `failed to register map %s (%d): no function given` |
| `pinconf.c:43` | `failed to register map %s (%d): no group/pin given` |
| `pinconf.c:50` | `failed to register map %s (%d): no configs given` |

这组是本子系统**日志质量最好的一组**——消息直接给出是第几条 map、缺什么字段。

### §4.4 `map->type` 落到 `default:` 分支（内部一致性）

`switch (type)` / `switch (setting->type)` 的 `default:` 兜底，一共 5 处：`pinconf.c:135`（`pinconf_map_to_setting`）、`core.c:1003`（`add_setting`）、`core.c:1280`、`core.c:1303`（`pinctrl_commit_state` 的应用与回滚两处）、`core.c:1465`（同 §4.3）。

正常情况下**永远不会走到**——`map_type()` 打出 `INVALID` 或 `UNKNOWN`（§3.3）就是这一类问题的可观测表现。看到就说明有人的 map 表 `.type` 没填或填了越界值。

### §4.5 `WARN_ON` 的 9 处——"内核内部不该发生"

全部 9 处，逐个核对过：

| 位置 | 条件 | 什么情况会真的触发 |
|---|---|---|
| `pinmux.c:225` | `WARN_ON(!desc->mux_usecount)`，在 `pin_free()` 里 | **`pin_free()` 被调用的次数多于 `pin_request()`**——引用计数漏平衡。这是真实驱动 bug 的信号，**排查 mux 问题时优先看它** |
| `core.c:1719` | `WARN_ON(!pname)`，在 `pingroups_show()` 里 | 驱动的 `get_group_pins` 报了未注册的引脚号 → `cat pingroups` 中途截断并返回 `-EINVAL`（§3.6） |
| `core.c:337` | 唯一带消息的一处：`WARN(!chip, "no gpio_chip for gpio%i?", gpio)`，在 `pinctrl_ready_for_gpio_range()` 里 | GPIO 号找不到对应 gpio_chip。只在 `CONFIG_GPIOLIB=y` 时编进来（`core.c:330` 的 `#ifdef`） |
| `core.c:1126` | `WARN_ON(!dev)`，在 `pinctrl_get()` 里 | 调用方传了 NULL device |
| `core.c:1407` | `WARN_ON(devres_release(...))`，在 `devm_pinctrl_put()` 里 | 释放了一个不是 devm 分配的 `struct pinctrl` |
| `core.c:2328` | `WARN_ON(devres_release(...))`，在 devm 版 unregister 里 | 同上，控制器维度 |
| `core.c:2245` | `WARN_ON(!r \|\| !*r)`，在 `devm_pinctrl_dev_match()` 里 | devres 内部一致性，正常不触发 |
| `pinctrl-utils.c:48`/`:68` | `WARN_ON(*num_maps == *reserved_maps)` | map 数组已满还继续加。配合 `-ENOSPC`。驱动侧 `pinctrl_utils_reserve_map()` 算错容量 |

`WARN_ON` **带栈回溯**，是本子系统唯一能自动给出"谁调的"的机制（因为没有 tracepoint，§2.4）。**看到 WARN 就把整段栈存下来**，比任何 debugfs 都有用。

注意 9 处里有 **8 处是裸 `WARN_ON` 不带消息**，dmesg 里只有 `WARN: CPU: ... at drivers/pinctrl/xxx.c:NNN`。**行号就是全部信息**，对着上表查。

### §4.6 `pinmux-select` 的写入解析（`pinmux.c:704`/`:711`/`:720`）

只影响手工 `echo`，**无日志**，只靠 `echo` 的返回值（shell 上表现为 `write error: Invalid argument`）。三个条件见 §3.10 的解析表。

### §4.7 DT 解析路径（`pinconf-generic.c` + `devicetree.c`）

这一组里 **`-EINVAL` 有一半根本不是错误**，要分两类看。

**(a) 真的返回 `-EINVAL`（只有 3 处）：**

| 位置 | 条件 | 日志 |
|---|---|---|
| `devicetree.c:264` | `pinctrl_dt_to_map()`：`pinctrl-N` 里的 phandle 指不到任何节点 | 有：`prop %s index %i invalid phandle`（`:261-262`），**带属性名和第几个 phandle** |
| `devicetree.c:395` | `pinctrl_copy_args()`：`index >= nr_elem` | 无 |
| `pinconf-generic.c:251` | `pinconf_generic_parse_dt_config(np == NULL)` | 无 |

**(b) 把 `-EINVAL` 当作"没有这个属性，正常"来读（3 处）——这是本节最该记住的事：**

| 位置 | 代码语义 |
|---|---|
| `pinconf-generic.c:219` | `of_property_read_u32()` 返回 `-EINVAL` ⇒ 属性不存在 ⇒ `continue` 跳过。逐字注释：`/* property not found */`。**返回其他错误则用 `par->default_value` 顶上**（`:222-224`）——这就是 `06-devicetree.md` 里"写了属性但值读不出来会静默变默认值"那条陷阱的出处 |
| `pinconf-generic.c:327` | `of_property_read_string(np, "function", ...)` 返回 `-EINVAL` ⇒ 没写 `function` ⇒ **不报错**（逐字注释 `/* EINVAL=missing, which is fine since it's optional */`，`:326`）。只有别的错误码才打 `%pOF: could not parse property function`（`:328-329`），且随后 `function = NULL` 继续往下走 |
| `pinconf-generic.c:76` | debugfs dump 里驱动返回 `-EINVAL`/`-ENOTSUPP` ⇒ 静默 `continue`（见 §3.11 ③） |

结论：**在 pinctrl 里看到 `-EINVAL`，先判断是谁在读它**。同一个码在 `of_property_read_u32()` 的返回位上意思是"属性不存在，一切正常"，在 `pinmux_map_to_setting()` 的返回位上意思是"DTS 写错了，整个 state 应用失败"。这也是为什么本篇 §4 要按语义分组而不是按行号罗列。

---

### §4.8 `pin_request()` 引脚冲突（症状最明显的一组）

`pin_request()`（`pinmux.c:108`）把 `status` 初始化成 `-EINVAL`（`:114`），所有失败路径 `goto out` 原样返回它。四个失败点，**全部有 dev_err**：

| 位置 | 条件 | 消息（逐字） |
|---|---|---|
| `pinmux.c:117` | 引脚号没注册过 | `pin %d is not registered so it cannot be requested`（`:118-120`） |
| `pinmux.c:127` | mux 已被别人占（条件 `(!gpio_range \|\| ops->strict) && desc->mux_usecount && strcmp(...)`，`:127-128`） | `pin %s already requested by %s; cannot claim for %s`（`:129-131`） |
| `pinmux.c:135` | GPIO 已被别人占（条件 `(gpio_range \|\| ops->strict) && desc->gpio_owner`，`:135`） | **同一条消息，一字不差**（`:136-138`） |
| `pinmux.c:157` | `try_module_get(pctldev->owner)` 失败 | `could not increase module refcount for pin %d`（`:154-156`） |

**中间两条打印完全相同的字符串**——从 dmesg 上分不出是 mux 冲突还是 GPIO 冲突。区分办法只有一个：

```bash
grep -w 'pin 30' /sys/kernel/debug/pinctrl/<控制器>/pinmux-pins
#   ... UNCLAIMED / ... (HOG) / ... function X group Y  ⇒ mux 侧（§3.9）
#   ... GPIO ...                                        ⇒ GPIO 侧
```

`ops->strict` 是这两个条件的开关（29 个驱动设了它，见 §3.9）：**非 strict 上 mux 和 GPIO 可以同时占同一个引脚，strict 上不行**。所以"同一份 DTS 在 A 板正常、在 B 板报 already requested"的第一嫌疑就是两个控制器驱动的 `.strict` 不一样。

同一函数里 `desc->mux_usecount > 1` 时直接 `return 0`（`:146-147`）——**同一个 owner 重复 request 是允许的且不报错**，这是 hog 和消费者共用引脚不冲突的原因。

### §4.9 泛型 group / function 表查不到 selector

只在控制器驱动用了 `pinctrl_generic_*` / `pinmux_generic_*` 那套 radix tree helper 时才走到（`04-extensions.md` 有分诊）。**这组日志质量最好——消息里带 `__func__` 和 selector 编号**：

| 位置 | 消息（逐字） |
|---|---|
| `core.c:576` | `%s could not find pingroup%i`（`:574-575`，`pinctrl_generic_get_group_pins()`） |
| `pinmux.c:840` | `%s could not find function%i`（`:838-839`，`pinmux_generic_get_function_groups()`） |
| `core.c:642` / `pinmux.c:886` | `add_group` / `add_function` 时 `name == NULL`，**无日志** |

另有 `core.c:214` `pin %d already registered`（`:211-212`）——**同一个引脚号在 `desc->pins[]` 里出现了两次**，控制器驱动的引脚表写重了。这条在 probe 期出现，控制器会直接注册失败。

还有 `core.c:459` `pinctrl_get_group_pins()` 在 `!pctlops->get_group_pins` 时返回 `-EINVAL`，**无日志**——就是 §3.6 那个"可选回调没实现"的另一个出口。

### §4.10 剩下没分组的

上面九组覆盖了 47 处中的约 40 处。剩下的是内部一致性检查（`core.c:1127` 的 `WARN_ON(!dev)` 配套返回、devres 相关），正常运行不会碰到，**碰到了就属于内核 bug 而不是配置问题**，不在本篇展开。

---

## §5 pinctrl 没有的那些东西

写这一节的原因：读者按其他子系统的经验去找这些东西，**找不到会怀疑自己**。以下全部是"确实没有"，每条带判据命令。

### §5.1 没有 sysfs 属性

```bash
grep -rn 'DEVICE_ATTR\|ATTRIBUTE_GROUPS\|sysfs_create' drivers/pinctrl/core.c \
    drivers/pinctrl/pinmux.c drivers/pinctrl/pinconf.c drivers/pinctrl/pinconf-generic.c \
    drivers/pinctrl/devicetree.c drivers/pinctrl/pinctrl-utils.c
# 空
```

对比 gpiolib：gpiolib 有 `/sys/class/gpio/`（已废弃）和 `/dev/gpiochipN`。**pinctrl 一个都没有**——它对用户态完全不可见，只有 debugfs。

### §5.2 没有字符设备、没有 ioctl、没有用户态工具

```bash
grep -rn 'register_chrdev\|cdev_add\|\.unlocked_ioctl\|misc_register' drivers/pinctrl/*.c
# 核心六个文件：空
```

所以**不存在 `pinctrl-utils` 这类用户态命令**。想在用户态操作 pinctrl，只有两条路：(a) 读 debugfs（只读）；(b) `echo` 到 `pinmux-select`（§3.10，绕过账本）。真正的配置手段是**改 DTS 重启**。

### §5.3 没有 tracepoint

见 §2.4。

### §5.4 没有故障注入

```bash
grep -rn 'should_fail\|fault_inject\|DECLARE_FAULT_ATTR\|ALLOW_ERROR_INJECTION' drivers/pinctrl/
# 空
```

想测"pinctrl 失败时消费者驱动的错误处理对不对"，只能靠**故意写错 DTS**（`06-devicetree.md` §5 那五级各对应一种错法）或 kprobe 改返回值。

### §5.5 没有内核命令行参数、没有 module_param

```bash
grep -rn 'module_param\|__setup(\|early_param\|core_param' drivers/pinctrl/core.c \
    drivers/pinctrl/pinmux.c drivers/pinctrl/pinconf.c drivers/pinctrl/pinconf-generic.c \
    drivers/pinctrl/devicetree.c
# 空
```

**对比 clk 子系统的 `clk_ignore_unused`——pinctrl 没有任何等价物**。也就是说：**没有任何办法在启动时让 pinctrl "别动引脚"**。唯一的运行期旋钮是 `dyndbg`（§2.3），而它只改日志不改行为。

### §5.6 只有一个 initcall，而且非常早

```c
static int __init pinctrl_init(void)
{
	pr_info("initialized pinctrl subsystem\n");     /* core.c:2335 */
	pinctrl_init_debugfs();
	return 0;
}

/* init early since many drivers really need to initialized pinmux early */
core_initcall(pinctrl_init);                            /* core.c:2340-2341 */
```

`initialized pinctrl subsystem`（`core.c:2335`，KERN_INFO）是 pinctrl 在 dmesg 里的**第一条也是唯一一条无条件输出**。看不到它 = `CONFIG_PINCTRL=n`。

`core_initcall` 的含义：**pinctrl 核心比几乎所有设备驱动都早就绪**，所以"控制器没就绪"的原因**永远不是核心没起来**，而是控制器驱动自己还没 probe（→ `-EPROBE_DEFER`，§3.2）。

---

## §6 一页速查

```bash
# ───── 0. 前提 ─────
mount -t debugfs none /sys/kernel/debug 2>/dev/null
dmesg | grep -i 'initialized pinctrl subsystem'      # 没有 → CONFIG_PINCTRL=n

# ───── 1. 控制器在不在（最先做的一件事）─────
cat /sys/kernel/debug/pinctrl/pinctrl-devices        # name / pinmux? / pinconf?
ls  /sys/kernel/debug/pinctrl/                       # 目录名 ≠ 上面的 name，见 §3.1
#   不在 ⇒ pinctrl_enable() 没跑完 ⇒ 消费者必然 -EPROBE_DEFER

# ───── 2. 我的设备拿到 pinctrl 了吗 ─────
grep -A3 '<你的设备>' /sys/kernel/debug/pinctrl/pinctrl-handles
#   看 "current state:" 那一行；none 或整段缺失都是问题
grep '<你的设备>' /sys/kernel/debug/pinctrl/pinctrl-maps
#   maps 里有、handles 里没有 ⇒ 名字解析失败，去 §4.1

# ───── 3. DTS 里的名字对不对（写 DTS 时最常错）─────
C=/sys/kernel/debug/pinctrl/<控制器目录>
grep -w '<function名>' $C/pinmux-functions            # function 存在？允许哪些 group？
grep -w '<group名>'    $C/pingroups                   # group 存在？含哪些引脚？

# ───── 4. 引脚现在归谁 ─────
head -2 $C/pinmux-pins                                # 先看表头判断 strict / 非 strict
grep -w 'pin 30' $C/pinmux-pins

# ───── 5. config 上了吗（空行有 5 种原因，见 §3.11）─────
grep -w 'pin 30' $C/pinconf-pins
cat $C/pinconf-groups

# ───── 6. GPIO 号 ↔ pin 号换算 ─────
cat $C/gpio-ranges

# ───── 7. 什么都看不出来时：打开隐藏的 14 条 dbg ─────
for f in core pinmux pinconf devicetree pinconf-generic; do
  echo "file drivers/pinctrl/$f.c +p" > /sys/kernel/debug/dynamic_debug/control; done
echo 'file drivers/base/pinctrl.c +p' > /sys/kernel/debug/dynamic_debug/control
#   要抓启动期：内核命令行加
#   dyndbg="file drivers/pinctrl/* +p; file drivers/base/pinctrl.c +p" ignore_loglevel

# ───── 8. 追调用路径（没有 tracepoint，只能 ftrace）─────
cd /sys/kernel/debug/tracing
echo 'pinctrl_select_state' > set_graph_function; echo function_graph > current_tracer

# ───── 9. 开发板上试 mux（绕过全部账本，见 §3.10）─────
echo "<group名> <function名>" > $C/pinmux-select        # 顺序是 group 在前！
```

**三条最容易踩的**：

| # | 陷阱 | 一句话 |
|---|---|---|
| 1 | `pinmux-pins` 有两种格式 | 先 `head -2` 看表头。非 strict 上 grep `UNCLAIMED$` 永远为空（§3.9） |
| 2 | `pinconf-pins` 的空行不代表没配置 | 5 种原因，其中"驱动不肯回答"和"真的没配"运行期无法区分（§3.11） |
| 3 | `pinmux-select` 之后 debugfs 会撒谎 | 硬件变了，账本没变，`pinmux-pins` 显示旧值（§3.10） |

---

## 下一步读什么

| 你想干什么 | 去哪 |
|---|---|
| 手上有一条错误消息，想知道 DTS 该怎么改 | `06-devicetree.md` §7（消息 → 原因映射表） |
| DTS 配了但完全没生效，要一步步排 | `06-devicetree.md` §5（五级决策树） |
| 想知道 debugfs 里那些字段在结构体里是什么 | `02-datastructures.md` §2、§3 |
| 想知道 `pin_request()` / `mux_owner` 账本怎么记的 | `03-internals.md` §A11.2 |
| 想知道 `pinctrl_enable()` 在注册流程的哪一步 | `03-internals.md` §A3 |
| 想知道 `pinctrl_select_state()` 失败时怎么回滚 | `03-internals.md` §B 部分 |
| 要自己写控制器驱动，想知道哪些回调是必填的 | `05-usage.md` §2 + 本篇 §4.2 |
| 想弄清 strict / 非 strict 到底差在哪 | `08-feynman.md` |
| 想知道本篇的数字怎么来的、哪些没验证 | `99-evidence.md` §2、§5 |
