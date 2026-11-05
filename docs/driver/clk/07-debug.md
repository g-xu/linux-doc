---
title: 调试方法
---

# 07 调试：clk 的开关、debugfs、tracepoint、日志与错误码分诊

> 本篇只讲 **arm64**。所有命令都在 6.1.158 的源码上核对过；**示例输出的格式头是从源码里
> 逐字抄的**（见 §3.2），不是凭印象编的。
>
> ⚠️ **先说一件对本篇定性的事**：`Documentation/driver-api/clk.rst` 全文 307 行里
> **一次都没有提到 debugfs，也一次都没有提到 tracepoint**
> （重跑：`grep -in 'debugfs\|clk_summary\|trace' Documentation/driver-api/clk.rst` → 无输出）。
> 也就是说 clk 的调试面**完全没有官方文档**，本篇的每一条都只能从源码反推。
> 这也是为什么本篇的行号密度比其他篇高。

---

## §0 按症状找章节

| 你看到的现象 | 先做什么 | 章节 |
|---|---|---|
| **arm64 启动挂死，没有任何日志** | 加 `clk_ignore_unused` 到 cmdline | §2.1 |
| 某个时钟频率不对 | `cat /sys/kernel/debug/clk/<名>/clk_rate` 和 DTS 对 | §3.3 |
| 时钟树的形状不对 / 有孤儿 | `clk_summary` + `clk_orphan_summary` | §3.2 |
| `clk_set_rate` 返回 0 但频率没变 | 看 `clk_flags` 有没有 `CLK_SET_RATE_PARENT` | §3.4 |
| 想看谁在什么时候动了这个时钟 | 打开 `clk` 事件组 | §4 |
| 收到一条 `WARN` 回溯 | 逐字对 §5.2 那张表 | §5.2 |
| 拿到一个错误码不知道哪来的 | §6 按语义分组的分诊表 | §6 |
| 想验证核心行为符合预期 | 跑 KUnit | §7 |
| 想改 debugfs 里的频率试试 | 得先改源码，没有 Kconfig | §1.2 |

---

## §1 编译期开关

### §1.1 Kconfig 全门

规则：一个调试特性能不能用，通常要过**多道门**。clk 这边的门如下。

| 开关 | 位置 | 管什么 | 依赖 |
|---|---|---|---|
| `CONFIG_COMMON_CLK` | `drivers/clk/Kconfig:21`（`menuconfig`） | 整个 CCF；关掉的话本套文档全部不适用 | `depends on !HAVE_LEGACY_CLK`（`:23`）——和 arm32 的老 clk 实现互斥 |
| `CONFIG_DEBUG_FS` | 通用 | §3 全部内容的**第一道门** | 关掉时 `clk_debug_init` 里的 `debugfs_create_*` 全部变成空操作，`/sys/kernel/debug/clk` 目录不存在 |
| `CONFIG_CLK_KUNIT_TEST` | `drivers/clk/Kconfig:476` | 核心行为的 12 个 KUnit suite | `depends on KUNIT`；`default KUNIT_ALL_TESTS` |
| `CONFIG_CLK_GATE_KUNIT_TEST` | `drivers/clk/Kconfig:483` | 基础 gate 类型的 5 个 suite | 同上 |
| `CONFIG_HAVE_CLK` / `CONFIG_HAVE_CLK_PREPARE` | `drivers/clk/Kconfig:3`/`:9` | 供架构和驱动 `depends on` 用的能力声明 | 不是调试开关，但排查"驱动没编进去"时会撞到 |

tracepoint（§4）**不需要单独的 Kconfig**——它只依赖通用的
`CONFIG_TRACEPOINTS` / `CONFIG_FTRACE`，clk 自己没有 `CONFIG_CLK_TRACE` 这种东西。

### §1.2 唯一一个"故意不给 Kconfig"的开关

写 debugfs 改频率、改父、开关时钟这套能力由 `CLOCK_ALLOW_WRITE_DEBUGFS` 控制。
它**不是 Kconfig 选项**，全树只出现在一个文件里：

```bash
grep -rn 'CLOCK_ALLOW_WRITE_DEBUGFS' . --include=*.c --include=*.h --include=Kconfig*
# drivers/clk/clk.c:3352   #undef CLOCK_ALLOW_WRITE_DEBUGFS
# drivers/clk/clk.c:3353   #ifdef CLOCK_ALLOW_WRITE_DEBUGFS
# drivers/clk/clk.c:3518   #ifdef CLOCK_ALLOW_WRITE_DEBUGFS
# drivers/clk/clk.c:3616   #ifdef CLOCK_ALLOW_WRITE_DEBUGFS
# drivers/clk/clk.c:3684   #ifdef CLOCK_ALLOW_WRITE_DEBUGFS
```

第一行 `#undef` 就在 `#ifdef` 上面一行——**它是故意把这个宏关死的**。紧跟着的注释
（`drivers/clk/clk.c:3354-3358`）把理由写得很直白：

```c
/*
 * This can be dangerous, therefore don't provide any real compile time
 * configuration option for this feature.
 * People who want to use this will need to modify the source code directly.
 */
```

所以打开它的**唯一**办法是：删掉 `drivers/clk/clk.c:3352` 那一行 `#undef`，重编内核。
这条要点在实践中很重要——很多人在 menuconfig 里翻半天找不到"允许写 clk debugfs"的开关，
以为是自己没找对，其实内核就是不给。

打开之后的四处变化：

| `#ifdef` 位置 | 打开后多了什么 |
|---|---|
| `drivers/clk/clk.c:3353` | `clk_rate_set()` 实现 + `clk_rate_mode` 从 `0444` 变成 `0644`（`:3371` vs `:3399`）；`clk_prepare_enable_fops`（`:3394`） |
| `:3518` | `clk_parent` 变成可写（`:3621` 用 0644 创建，否则 `:3626` 用 0444） |
| `:3616` | 每个时钟目录下多出 `clk_prepare_enable` 文件（0644） |
| `:3684` | 启动时打印 14 行安全警告横幅（`:3686-3699`） |

那段横幅逐字如下（只摘关键两行，全文在 `drivers/clk/clk.c:3686-3699`）：

```
**  WRITEABLE clk DebugFS SUPPORT HAS BEEN ENABLED IN THIS KERNEL **
** If you see this message and you are not debugging the          **
```

用途：**在别人给你的内核上，只要 dmesg 里搜到这一行，就说明这个内核是可写 clk 的**，
`clk_rate` 之类的值可能被人手动改过，不能当成"驱动设成这样"的证据。

---

## §2 内核命令行参数

### §2.1 `clk_ignore_unused`：arm64 启动挂死的第一诊断手段

| 项 | 内容 |
|---|---|
| 解析 | `__setup("clk_ignore_unused", clk_ignore_unused_setup)`，`drivers/clk/clk.c:1458` |
| 变量 | `static bool clk_ignore_unused __initdata;`，`drivers/clk/clk.c:1452` 是 `__initdata`，启动完就被释放，**运行期无法查询它当时的值** |
| 判据点 | `drivers/clk/clk.c:1465` |
| 生效时确认信号 | `pr_warn("clk: Not disabling unused clocks\n")`，`drivers/clk/clk.c:1466` |
| 官方说明 | `Documentation/admin-guide/kernel-parameters.txt:594` |
| 它抑制的动作 | `clk_disable_unused`，注册为 `late_initcall_sync`（`drivers/clk/clk.c:1499`） |

**为什么它是第一手段**：`clk_disable_unused` 会在启动末期把"硬件上开着、但没有任何
Linux 驱动声明过"的时钟统统关掉。arm64 SoC 上只要有一个驱动漏了 `clk_prepare_enable`，
它依赖的时钟就会在这一刻被关，而此时控制台/内存控制器/中断控制器可能正在用——
**结果是挂死，且挂死发生在 `late_initcall_sync`，此前所有日志都正常**，非常有欺骗性。

标准排查流程（三步，别跳）：

```bash
# 1. 加参数确认方向
#    cmdline 加 clk_ignore_unused，起来了 → 方向确认，
#    dmesg 里应有 "clk: Not disabling unused clocks"

# 2. 抓快照
cat /sys/kernel/debug/clk/clk_summary > /tmp/with-ignore.txt
#    （去掉参数那次起不来，所以拿不到对照份——办法见 §2.2）

# 3. 找嫌疑：enable count 为 0、但 hardware enable 是 Y 的时钟
#    注意不能用 awk 按列号切，理由见 §3.2 的「换行陷阱」
grep -o '[a-z0-9_.-]\+ \+0 \+[0-9]\+ \+[0-9]\+ .\+\? Y ' /tmp/with-ignore.txt
#    实践中更可靠的做法是直接翻这份文件，人眼找 "count 列是 0、Y 在最后" 的行
```

判据是：**`enable count` 为 0（Linux 认为没人用）而 `hardware enable` 为 `Y`
（硬件确实开着）的那些时钟，就是 `clk_disable_unused` 下一刀要砍的对象**。
列的含义见 §3.2。

⚠️ **但这个判据有一个必须知道的假阳性来源**：`hardware enable` 那一列的 `Y`
**有可能是核心编出来的**——没有 `.is_enabled` 也没有 `.enable` 回调的时钟，
核心直接打印 `Y`（`drivers/clk/clk.c:3228-3229`）。这类时钟根本不会被
`clk_disable_unused` 动到，是纯噪声。三值语义见 §3.2 的表。

一条重要的语义澄清（`Documentation/admin-guide/kernel-parameters.txt:594` 起明确写了）：
**这个参数不会把时钟变成 always-on，也不会给它加引用计数**。它只是让那一次批量关闭
不发生。所以它是**诊断手段，不是修复手段**——真正的修复是给漏掉的驱动补上
`clk_prepare_enable`，或者给那个时钟加 `CLK_IS_CRITICAL` / `CLK_IGNORE_UNUSED` 标志。

### §2.2 拿不到"不带参数"那份快照怎么办

既然不带参数就起不来，对比法就缺一半。两个替代办法：

| 办法 | 怎么做 | 局限 |
|---|---|---|
| 提前抓 | 带 `clk_ignore_unused` 起来后，用 §2.1 第 3 步直接算出嫌疑名单 | 名单可能偏大（含真正无用的时钟） |
| 二分法 | 逐个给嫌疑时钟在提供方驱动里加 `CLK_IGNORE_UNUSED`，去掉 cmdline 参数重试 | 要改代码重编，慢 |
| 看 tracepoint | 开 `clk_disable` 事件 + `trace_printk` 到串口，看挂死前最后一条 | 挂死点靠后时串口可能已经跟着挂了 |

---

## §3 debugfs

### §3.1 目录结构与创建时机

```
/sys/kernel/debug/clk/                      ← rootdir，drivers/clk/clk.c:3702
├── clk_summary                 0444        ← :3704   全部时钟，带缩进层次
├── clk_dump                    0444        ← :3706   全部时钟，JSON
├── clk_orphan_summary          0444        ← :3708   只列孤儿
├── clk_orphan_dump             0444        ← :3710   孤儿，JSON
└── <每个时钟一个目录>/          ← 目录名 = core->name，:3600
    ├── clk_rate                0444/0644   ← :3603，模式取决于 §1.2
    ├── clk_min_rate            0444        ← :3605
    ├── clk_max_rate            0444        ← :3606
    ├── clk_accuracy            0444        ← :3607   直接暴露 core->accuracy
    ├── clk_phase               0444        ← :3608   直接暴露 core->phase
    ├── clk_flags               0444        ← :3609   逐行打印标志名，见 §3.4
    ├── clk_prepare_count       0444        ← :3610
    ├── clk_enable_count        0444        ← :3611
    ├── clk_protect_count       0444        ← :3612
    ├── clk_notifier_count      0444        ← :3613
    ├── clk_duty_cycle          0444        ← :3614
    ├── clk_prepare_enable      0644        ← :3617   只在 §1.2 打开时存在
    ├── clk_parent              0444/0644   ← :3621（可写）/ :3626（只读）
    └── clk_possible_parents     0444        ← :3630   只有多父时钟才有
```

**创建时机**：`late_initcall(clk_debug_init)`，`drivers/clk/clk.c:3722`。
在此之前注册的时钟先挂在一个待处理链表上，`clk_debug_init` 统一补建
（源码注释在 `drivers/clk/clk.c:3643`：`will be created lazily by clk_debug_init
as part of a late_initcall`）。

这里有一个**次序**值得记住：

| initcall 级别 | 干什么 | 谁 |
|---|---|---|
| `late_initcall` | 建 debugfs | `clk_debug_init`，`drivers/clk/clk.c:3722` |
| `late_initcall_sync` | 关掉没人用的时钟 | `clk_disable_unused`，`drivers/clk/clk.c:1499` |

`late_initcall` 早于 `late_initcall_sync`，所以 **debugfs 是在批量关闭之前建好的**——
这就是 §2.1 那套排查能工作的前提（你在关闭动作之后读到的 `clk_summary`
反映的是关闭后的状态，而 `hardware enable` 列仍能告诉你硬件的真实状态）。

### §3.2 四个全局文件

**`clk_summary` 的表头是逐字从源码抄的**（`drivers/clk/clk.c:3262-3264`）：

```
                                 enable  prepare  protect                                duty  hardware                            connection
   clock                          count    count    count        rate   accuracy phase  cycle    enable   consumer                         id
---------------------------------------------------------------------------------------------------------------------------------------------
```

列的用法（这是本篇最该背下来的一张表）：

| 列 | 名字 | 怎么用 |
|---|---|---|
| 1 | `clock` | 缩进表示父子层次；**缩进就是时钟树** |
| 2 | `enable count` | Linux 侧的 `clk_enable` 计数 |
| 3 | `prepare count` | Linux 侧的 `clk_prepare` 计数 |
| 4 | `protect count` | 频率保护计数（`clk_rate_exclusive_get` / `CLK_SET_RATE_GATE` 自动加的那份） |
| 5 | `rate` | **是缓存值**，除非有 `CLK_GET_RATE_NOCACHE`（见 `02-datastructures.md`） |
| 6 | `accuracy` | 同样是缓存值；0 的语义是"零误差"不是"未知"（`06-devicetree.md` §2.5） |
| 7 | `phase` | 相位 |
| 8 | `duty cycle` | 占空比 |
| 9 | `hardware enable` | **硬件的真实状态**（走 `.is_enabled` 回调现读），和第 2 列不一致就是排查线索 |
| 10-11 | `consumer` / `connection id` | 谁 get 了它、用的什么 con_id |

**`hardware enable` 那一列是三值的，不是两值的**——这一点在实践中反复坑人。
判据逐字在 `drivers/clk/clk.c:3226-3232`：

```c
	if (c->ops->is_enabled)
		seq_printf(s, " %5c ", clk_core_is_enabled(c) ? 'Y' : 'N');
	else if (!c->ops->enable)
		seq_printf(s, " %5c ", 'Y');
	else
		seq_printf(s, " %5c ", '?');
```

| 你看到的值 | 提供方 ops 长什么样 | 这个值可信吗 |
|---|---|---|
| `Y` / `N` | 有 `.is_enabled` | **可信**，是现读硬件寄存器的结果 |
| `Y` | **没有** `.is_enabled`、**也没有** `.enable` | ⚠️ **是编出来的**。核心的逻辑是"这个时钟根本没有门控，所以永远算开着"。它不代表读过任何寄存器 |
| `?` | 没有 `.is_enabled`、但**有** `.enable` | **未知**。提供方能开关它，却没实现查询回调——这种时候 `clk_summary` 帮不了你，只能去读寄存器 |

有了三值语义，第 2 列 vs 第 9 列的判据表才完整：

| enable count | hardware enable | 含义 | 危险吗 |
|---|---|---|---|
| >0 | `Y`（可信） | 正常 | 否 |
| 0 | `N` | 正常（没人用，也确实关着） | 否 |
| **0** | **`Y`（可信）** | 硬件开着但 Linux 不知道 → **`clk_disable_unused` 的下一刀目标**（§2.1） | **是**，启动挂死的主要来源 |
| >0 | `N` | Linux 以为开着，硬件却是关的 → 提供方 `.enable` 静默失败，或 `.is_enabled` 实现错 | 是，设备行为诡异 |
| 0 | `Y`（编出来的） | 无门控时钟，`clk_disable_unused` 对它不做任何事 | 否，**纯噪声** |
| 任意 | `?` | 提供方缺 `.is_enabled` | 无法判断，先给提供方补上这个回调 |

### §3.2.1 换行陷阱：为什么不能用 `awk` 按列切 `clk_summary`

`clk_summary_show_one`（`drivers/clk/clk.c:3204-3243`）里，**唯一的 `\n` 在打印消费者的
那个循环体内**：

```c
	hlist_for_each_entry(clk_user, &c->clks, clks_node) {
		seq_printf(s, "%*s%-*s  %-25s\n", ...);
		multi_node = 1;
	}
```

也就是说：

> **一个没有任何消费者（没人 `clk_get` 过）的时钟，打印完自己那一行就不换行**，
> 下一个时钟直接接在同一行后面。

后果有两条，都很实际：

| 后果 | 说明 |
|---|---|
| `awk '$2==0 && $9=="Y"'` 这类按字段号切的命令**不可靠** | 多个无消费者时钟挤在同一文本行上，字段位置全乱。而**无消费者的时钟恰恰就是 §2.1 要找的对象**，所以这个陷阱正好打在最需要它的场景上 |
| `clk_orphan_summary` 更明显 | 孤儿几乎都还没被 get，所以这个文件常常整体塌成**一行超长文本** |

能用的替代手段三条：

| 手段 | 怎么做 | 局限 |
|---|---|---|
| 按**字符列**切 | 行首宽度是固定的：名字栏 `35 - level*3`，之后依次 `%-7d %-8d %-8d %-11lu %-10lu`（`drivers/clk/clk.c:3204-3209`） | 缩进随层级变，脚本要跟着算 |
| `grep` 模式匹配 | 如 §2.1 那条，靠 `0` 后跟数字再跟末尾 `Y` 的形状去捞 | 会有误匹配，要人眼复核 |
| ⚠️ 换 `clk_dump` | 它是**每个时钟一个 JSON 对象**，不依赖换行，机器可读 | **但它没有 `hardware enable` 字段**（见下表），所以它救不了 §2.1 那个场景 |

### §3.2.2 另外三个全局文件

| 文件 | 和 `clk_summary` 的区别 | 什么时候用它 |
|---|---|---|
| `clk_dump` | JSON（`clk_dump_show`，`drivers/clk/clk.c:3340-3350`）。字段**不一样**：有 `enable_count`/`prepare_count`/`protect_count`/`rate`/`min_rate`/`max_rate`/`accuracy`/`duty_cycle`，**没有 `hardware enable`，也没有消费者信息**；`phase` 只在 `phase >= 0` 时才出现（`clk_dump_one`，`:3283` 起） | 要写脚本对比两个快照时——**但别指望它查硬件状态** |
| `clk_orphan_summary` | **只列孤儿**，格式和 `clk_summary` 完全一样（含上面的换行陷阱） | 父时钟名字写错的唯一确认手段（`06-devicetree.md` §7.2） |
| `clk_orphan_dump` | 孤儿的 JSON 版 | 同 `clk_dump` |

一个容易忽略的实现细节：这四个 show 函数都会先调 `clk_pm_runtime_get_all()`
（`drivers/clk/clk.c:3266`），失败就直接返回错误。所以**`cat clk_summary` 是有可能
读不出来的**——如果某个时钟控制器的 runtime PM resume 失败，你会看到
`clk: Failed to runtime PM get '<名>' for clk '<名>'`（`drivers/clk/clk.c:165`）。

### §3.3 每时钟目录下的 14 个文件

分成三类看，别一个个记：

| 类别 | 文件 | 特点 |
|---|---|---|
| **直通字段**（`debugfs_create_ulong` / `_u32`，无回调） | `clk_accuracy`、`clk_phase`、`clk_prepare_count`、`clk_enable_count`、`clk_protect_count`、`clk_notifier_count` | 直接映射结构体字段的地址（`drivers/clk/clk.c:3607-3613`），**读的时候不取任何锁**，可能读到撕裂值 |
| **走 show 回调**（要取 `prepare_lock`） | `clk_rate`、`clk_min_rate`、`clk_max_rate`、`clk_flags`、`clk_duty_cycle`、`clk_parent`、`clk_possible_parents` | 值是一致的快照 |
| **可写**（只在 §1.2 打开时） | `clk_rate`、`clk_parent`、`clk_prepare_enable` | 见 §3.5 的 setter 语义 |

上面第一类那条"不取锁"值得单独强调：`clk_enable_count` 和 `clk_prepare_count`
是直接暴露 `core->enable_count` / `core->prepare_count` 的内存地址。这意味着：

> **`cat clk_enable_count` 和 `cat clk_prepare_count` 拿到的两个数字不保证来自同一时刻。**
> 需要一致快照时用 `clk_summary`（它在 `prepare_lock` 下一次性打印所有列）。

`clk_possible_parents` 只在**多父**时钟上存在（`drivers/clk/clk.c:3629-3631` 的条件），
所以"这个目录下没有 `clk_possible_parents`"= 这是个单父或根时钟，不是 bug。

### §3.4 `clk_flags`：`clk_set_rate` 无效时的第一现场

`clk_flags_show`（`drivers/clk/clk.c:3435-3453`）把 `core->flags` **逐行**打印成标志名，
名字表 `clk_flags[]` 在 `drivers/clk/clk.c:3418-3433`，一共 **12 条**（顺序即代码顺序）：

| 标志 | 看到它意味着 | 相关篇目 |
|---|---|---|
| `CLK_SET_RATE_GATE` | 改频率前必须先关掉；`clk_prepare` 会自动给它加频率保护（`clk.c:1072-1080`） | `01-api.md`、`03-internals.md` |
| `CLK_SET_PARENT_GATE` | 换父前必须先 unprepare；判据是 **`prepare_count`** 不是 enable_count（`clk.c:2793-2795`） | `01-api.md` |
| `CLK_SET_RATE_PARENT` | **没有它，一个自己没有 `.set_rate` 的时钟调 `clk_set_rate` 会静默返回 0**（`clk.c:2445-2447`） | §8 第一条 |
| `CLK_IGNORE_UNUSED` | 这个时钟豁免 `clk_disable_unused` | §2.1 |
| `CLK_GET_RATE_NOCACHE` | `rate` 列是现读的，不是缓存 | §3.2 第 5 列 |
| `CLK_SET_RATE_NO_REPARENT` | 调频时不允许换父来凑频率 | |
| `CLK_GET_ACCURACY_NOCACHE` | `accuracy` 列是现读的 | |
| `CLK_RECALC_NEW_RATES` | 调频后要重算子树 | |
| `CLK_SET_RATE_UNGATE` | 改频率时反而要先开着 | |
| `CLK_IS_CRITICAL` | 注册时就 prepare+enable，永不关闭 | §5.2 |
| `CLK_OPS_PARENT_ENABLE` | 操作本时钟期间父必须是开的 | |
| `CLK_DUTY_CYCLE_PARENT` | 占空比操作透传给父 | |

一个实用细节：认不出来的残余位会以 `0x%lx` 形式单独打一行
（`drivers/clk/clk.c:3447-3450`，注释就叫 `/* Unknown flags */`）。所以
**`cat clk_flags` 输出里出现 `0x...` 这种十六进制行，说明提供方用了 6.1 的 `clk_flags[]`
表里没有的标志位**——通常是厂商私加的位，或者你的头文件和 `clk.c` 版本不一致。

### §3.5 `clk_parent` / `clk_possible_parents`：孤儿和"父名写错"的直接证据

**读语义**（这两个文件默认只读，见 §1.2）：

| 文件 | 打印什么 | 排查用法 |
|---|---|---|
| `clk_parent` | `current_parent_show`（`drivers/clk/clk.c:3506-3514`）只在 `core->parent` 非空时打印一行 | **`core->parent` 为空时这个文件是空的**（`cat` 出来什么都没有）。所以 **`cat clk_parent` 无输出 = 这个时钟当前没有父**：要么是根时钟，要么就是孤儿（去 `clk_orphan_summary` 确认，见 `06-devicetree.md` §7.2） |
| `clk_possible_parents` | `possible_parents_show`（`:3493-3504`）把所有候选父按空格分隔打一行 | 只在 `num_parents > 1` 时创建（`:3629-3631`），所以文件不存在不是 bug |

`clk_possible_parents` 里的**标记本身携带诊断信息**。`possible_parent_show`
（`drivers/clk/clk.c:3456-3491`）按四步降级取名，源码注释把顺序写得很清楚：

| 你看到的形式 | 说明这一路候选父的状态 |
|---|---|
| 普通名字 `xin24m` | 最好的情况：候选父**已经注册**，拿到的是它的真名 |
| 普通名字（但对应时钟在 `clk_summary` 里找不到） | 用的是提供方写死的 fallback 名（`core->parents[i].name`），**该时钟尚未注册** |
| `<xxx>(fw)` | 用的是 `fw_name`（DT 里的 `clock-names`），尖括号和 `(fw)` 后缀就是"还没解析成真实时钟"的标记 |
| `(missing)` | **四步全部失败**。这是最强的错误信号：既没注册、没 fallback 名、没 fw_name，`of_clk_get_parent_name` 也没查到 |

**写语义**（只在 §1.2 打开时）：`current_parent_write`（`drivers/clk/clk.c:3519-3544`）
接受的是**候选父的下标**（`kstrtou8` 解析），不是名字：

| 你写进去的值 | 发生什么 |
|---|---|
| 一个合法下标 | 走 `clk_core_set_parent_nolock`，等价于驱动调 `clk_set_parent`；该有的 `CLK_SET_PARENT_GATE` 之类检查**一个都不会少** |
| 越界下标 | `clk_core_get_parent_by_index` 返回 NULL → **`-ENOENT`**（`:3535`） |
| 非数字 | `kstrtou8_from_user` 失败，把它的错误码原样返回（通常 `-EINVAL`） |
| 名字（如 `echo xin24m >`） | **不认**，会被当成非数字 → `-EINVAL`。下标从 `clk_possible_parents` 里数，从 0 开始 |

同理 `clk_rate`（`clk_rate_set`，`drivers/clk/clk.c:3359-3369`）走的是
`clk_core_set_rate_nolock`，`clk_prepare_enable`（`clk_prepare_enable_set`，`:3373-3384`）
写非 0 就 `clk_prepare_enable`、写 0 就 `clk_disable_unprepare`。

这三个 setter 有一个**共同的语义要点**，实践中很关键：

> 它们**不是**绕过核心去改寄存器，而是**代替一个驱动去调标准 API**。所以
> 一切 flag 检查、通知链、频率保护、父使能都照常发生；`clk_rate` 写下去以后
> `clk_enable_count` 不会变，而**驱动侧的引用计数被你搅乱了**——
> 比如你 `echo 1 > clk_prepare_enable` 加的那一份计数，
> 谁也不会帮你还。调试完记得 `echo 0` 还回去。

`clk_prepare_enable` 的**读**语义也有个坑：
`clk_prepare_enable_get`（`drivers/clk/clk.c:3386-3392`）返回的是
`core->enable_count && core->prepare_count`，是**布尔与**。所以读到 `0`
只说明"两个计数里至少有一个是 0"，不告诉你是哪一个——要区分就去看
`clk_enable_count` / `clk_prepare_count`（注意 §3.3 说的不取锁问题）。

### §3.6 debugfs 一页速查

| 想知道 | 读哪个 | 注意 |
|---|---|---|
| 整棵时钟树的形状 | `clk_summary` | 缩进就是层次；有 §3.2.1 的换行陷阱 |
| 硬件到底开着没有 | `clk_summary` 第 9 列 | 三值，`Y` 可能是编的（§3.2） |
| 某个时钟为什么调不动频率 | `<名>/clk_flags` | 先看有没有 `CLK_SET_RATE_PARENT`（§3.4） |
| 父挂错了没有 | `<名>/clk_parent` + `clk_orphan_summary` | `clk_parent` **空文件 = 没有父**（§3.5） |
| 候选父列表对不对 | `<名>/clk_possible_parents` | `(missing)` / `<x>(fw)` 是错误标记（§3.5） |
| 频率范围被谁夹住了 | `<名>/clk_min_rate`、`clk_max_rate` | 是所有消费者约束的合并结果（`04-extensions.md`） |
| 写脚本比对两次快照 | `clk_dump` | 它**没有** `hardware enable` 字段（§3.2.2） |

---

## §4 tracepoint

### §4.1 全清单：6 个事件类、20 个事件

事件组名是 `clk`（`include/trace/events/clk.h:6` 的 `TRACE_SYSTEM clk`）。
6 个 `DECLARE_EVENT_CLASS` 决定了字段，20 个 `DEFINE_EVENT` 是实际能开的事件：

| 事件类 | 声明处 | 字段（`TP_STRUCT__entry`） | 打印格式 | 属于它的事件 |
|---|---|---|---|---|
| `clk` | `include/trace/events/clk.h:15` | `name`（仅此一个） | `"%s"` | `clk_enable`:32、`clk_enable_complete`:39、`clk_disable`:46、`clk_disable_complete`:53、`clk_prepare`:60、`clk_prepare_complete`:67、`clk_unprepare`:74、`clk_unprepare_complete`:81 |
| `clk_rate` | `:88` | `name` + `rate`（`unsigned long`） | `"%s %lu"` | `clk_set_rate`:107、`clk_set_rate_complete`:114、`clk_set_min_rate`:121、`clk_set_max_rate`:128 |
| `clk_rate_range` | `:135` | `name` + `min` + `max` | — | `clk_set_rate_range`:158 |
| `clk_parent` | `:165` | `name` + `pname`；父为空时 `pname` 写死成字符串 `"none"`（`:172`/`:177`） | `"%s %s"` | `clk_set_parent`:184、`clk_set_parent_complete`:191 |
| `clk_phase` | `:198` | `name` + `phase`（`int`） | `"%s %d"` | `clk_set_phase`:217、`clk_set_phase_complete`:224 |
| `clk_duty_cycle` | `:231` | `name` + `num` + `den` | — | `clk_set_duty_cycle`:253、`clk_set_duty_cycle_complete`:260 |

**`TP_CONDITION` 在这个头文件里出现 0 次**（重跑：
`grep -c TP_CONDITION include/trace/events/clk.h`）。含义很实际：

> **clk 的每个事件都是无条件的**——开了就把所有时钟的所有该动作全部打出来。
> 没有"只跟踪某个时钟"的内建过滤。要缩范围只能用 ftrace 的通用
> 事件过滤器（`events/clk/clk_set_rate/filter`），按 `name` 字段过滤。

### §4.2 三个必须知道的缺口

| 缺口 | 后果 |
|---|---|
| 没有 `clk_get` / `clk_put` 事件 | **无法用 tracepoint 回答"谁 get 了这个时钟"**。这个问题只能看 `clk_summary` 最后两列（consumer / connection id），或 §5 的 `clk: Failed to ...` 日志 |
| 没有注册/注销事件 | `clk_hw_register` / `clk_unregister` 全程不打点，孤儿的形成过程追不到 |
| 没有 `.determine_rate` / `.round_rate` 事件 | 你能看到 `clk_set_rate`（请求值）和 `clk_set_rate_complete`（落定值），**但看不到中间的协商过程**。两者不一致时得靠提供方自己加 `trace_printk` |
| **没有任何失败事件** | 见下面 §4.2.1——这是本节最容易被想反的一条 |

#### §4.2.1 "entry 有、`_complete` 没有 ⇒ 中途失败"——这个推断在 6.1 里是**错的**

很自然会这么想：既然每个动作都有 `X` 和 `X_complete` 一对打点，
那么"只看到 `clk_set_rate` 没看到 `clk_set_rate_complete`"就说明中间失败了。
**逐字读三处提交代码，这个信号不存在**：

| 动作 | entry | 中间那一句 | `_complete` |
|---|---|---|---|
| set_rate | `drivers/clk/clk.c:2366` | `:2367-2368` `if (!skip_set_rate && core->ops->set_rate) core->ops->set_rate(...)` | `:2371`，**无条件** |
| set_parent（提交阶段） | `:2348` | `:2349-2356` 调 `set_rate_and_parent` 或 `set_parent`，**返回值都不接** | `:2359`，**无条件** |
| set_parent（独立路径） | `:2106` | `:2110` `ret = core->ops->set_parent(...)` | `:2112`，**无条件**；`ret` 的处理在 `:2114` 之后 |
| set_phase | `:2898` | `:2900-2903` | `:2906`，**无条件** |

四处的两个打点之间**都没有 `return` / `goto` / 提前退出**。所以：

> **entry 触发了，`_complete` 就一定会触发。配对缺失这件事在 6.1 的 clk 上不可能发生。**

真正有诊断价值的是**反过来**那一半——**连 entry 都没有**，说明失败发生在打点之前：

| 连 entry 都没有 | 说明失败在哪 | 去哪查 |
|---|---|---|
| 调了 `clk_set_rate`，无 `clk_set_rate` 事件 | 协商阶段就退了：`-EBUSY`（`:2450`）、`clk_calc_new_rates` 失败、或**"没有变化"直接 `return 0`**（`:2445-2447`） | `08-feynman.md` §7 |
| 有 `clk_set_rate` 事件但频率没变 | 提交阶段 `.set_rate` 返回了错误——**而这个错误被彻底丢掉了**，见下 |
| 什么事件都没有，且 rate 没变 | `clk_pm_runtime_get(core)` 失败，`:2339` 直接 `return`（提交函数是 `void`） | `03-internals.md` Part B 的电源管理一节 |

最后要记住这条**比 tracepoint 缺口更严重的事实**：提交阶段的函数
`clk_change_rate` 的签名是 `static void`（`drivers/clk/clk.c:2318`），
`.set_rate` 的返回值在 `:2367-2368` **根本没有被接**，
调用它的 `clk_core_set_rate_nolock` 在 `:2473` 也不检查。
也就是说——

> **`clk_set_rate()` 返回 0 只证明"协商阶段通过了"，
> 不证明提供方真的把寄存器写成功了。**
> 这是 §6 那张错误码表**照不到**的一整片区域，
> 也是为什么 `05-usage.md` §2 坚持"`clk_set_rate` 之后必须 `clk_get_rate` 回读"。

### §4.3 `_rcuidle` 变体：enable/disable 路径的特殊写法

同一个事件在 clk 里有**两种调用形式**，这是本节最容易被忽略的一点。
`drivers/clk/clk.c` 里的打点位置：

| 事件 | 调用点 | 形式 |
|---|---|---|
| unprepare | `drivers/clk/clk.c:1004`/`:1009` | 普通 `trace_clk_unprepare()` |
| prepare | `:1059`/`:1064` | 普通 |
| disable | `:1139`/`:1144` | **`trace_clk_disable_rcuidle()` / `trace_clk_disable_complete_rcuidle()`** |
| enable | `:1198`/`:1203` | **`trace_clk_enable_rcuidle()` / `trace_clk_enable_complete_rcuidle()`** |
| disable（批量关闭路径） | `:1402`/`:1407`、`:1438`/`:1443` | 普通形式，**不是** `_rcuidle` |
| set_parent | `:2106`/`:2112` | 普通 |
| set_rate 提交 | `:2348`/`:2359`、`:2366`/`:2371` | 普通 |
| set_rate_range | `:2586` | 普通 |
| set_min_rate / set_max_rate | `:2682` / `:2700` | 普通 |
| set_phase | `:2898`/`:2906` | 普通 |
| set_duty_cycle | `:3059`/`:3068` | 普通 |

为什么 enable/disable 要用 `_rcuidle`：这两个路径**在 `enable_lock` 自旋锁下、
且可能发生在 CPU 进入 idle 之后**（cpuidle 驱动关时钟就是典型场景）。此时
RCU 已经不再观测这个 CPU，普通 tracepoint 用的 `rcu_read_lock_sched_notrace`
是无效的，必须走 `_rcuidle` 版本。

对使用者的实际影响：**事件名不变**（`/sys/kernel/tracing/events/clk/clk_enable`
就是这一个），`_rcuidle` 只影响内核侧怎么进这个 tracepoint。所以你不需要为它
做任何特殊操作——但**看到 idle 路径上的 clk_disable 事件不要觉得奇怪**，那是正常的。

一个对读者有用的推论：`clk_disable_unused`（§2.1）走的是**非** `_rcuidle` 形式
（`:1402`/`:1438`），所以启动末期那一批关闭动作在 trace 里和运行期的关闭
**长得一模一样**，无法靠事件名区分。要区分只能看时间戳落在 `late_initcall_sync` 附近。

### §4.4 用法

```bash
cd /sys/kernel/tracing

# 全开 clk 事件组（20 个事件一起）
echo 1 > events/clk/enable

# 只开调频相关
echo 1 > events/clk/clk_set_rate/enable
echo 1 > events/clk/clk_set_rate_complete/enable

# 因为没有 TP_CONDITION，缩范围只能靠通用过滤器
echo 'name == "i2c1"' > events/clk/clk_set_rate/filter

cat trace
```

配对读法：clk 的事件全是 **`X` / `X_complete` 成对**出现的（8 个基本动作里
6 对，加上 set_rate/set_parent/set_phase/set_duty_cycle 各一对）。
**只看到 `X` 没有 `X_complete`，说明中间失败了或者卡住了**——
这是 clk tracepoint 最有价值的用法，比看单个事件强得多。

---

## §5 日志

### §5.1 可见性等级：clk 的日志分成三个截然不同的档

先给一个反直觉的事实（重跑：
`/usr/bin/grep -c '_once\|_ratelimited' drivers/clk/clk.c` → **0**）：

> **`drivers/clk/clk.c` 里一个 `pr_*_once` / `pr_*_ratelimited` 都没有。**
> 所以 clk 的 `pr_*` 日志**每次都打、不限流**。一条 `pr_warn` 在循环里被触发
> 就会刷满 dmesg——这在排查时既是好事（不丢事件）也是坏事（会把前面的日志顶掉，
> 遇到刷屏先 `dmesg -w` 转存到文件）。

`drivers/clk/clk.c` 一共 **40** 条 `pr_*`（重跑：
`/usr/bin/grep -c 'pr_err\|pr_warn\|pr_info\|pr_debug' drivers/clk/clk.c`），按可见性分三档：

| 档 | 数量与手段 | 默认看得到吗 | 怎么打开 |
|---|---|---|---|
| `pr_err` / `pr_warn` | 无限流，直接进 dmesg | **能**（除非 `loglevel` 调得很低） | 不用做什么 |
| `pr_info` | 只有 2 条：`clk: Disabling unused clocks`（`drivers/clk/clk.c:1470`）和 §2.1 那条的对偶 | 能 | — |
| `pr_debug` | 6 条，全部在**静默失败路径**上 | **不能** | 见下 |

那 6 条 `pr_debug` 是本篇最值钱的信息，因为它们和 `06-devicetree.md` §7
里那些"不报错的失败"**一一对应**：

| `pr_debug` 内容（逐字） | 位置 | 它对应哪个静默失败 |
|---|---|---|
| `%s: clk notifier callback for clock %s aborted with error %d\n` | `drivers/clk/clk.c:2159` | 通知链否决了调频（对外只返回 `-EBUSY`，`clk.c:2463-2470`） |
| `%s: %s not gated but wants to reparent\n` | `:2250` | `CLK_SET_PARENT_GATE` 时钟没关就想换父 |
| `%s: clk %s can not be parent of clk %s\n` | `:2259`、`:2804` | 换父失败最常见的原因，**两处同一条串**：一处在调频找父的路上，一处在 `clk_set_parent` 里 |
| `%s: failed to set %s rate\n` | `:2465` | 提供方 `.set_rate` 返回错误 |
| `%s: clk %s already initialized\n` | `:3801` | 重复注册（对外 `-EEXIST`，`clk.c:3799-3804`） |
| `Added clock from %pOF\n` / `Added clk_hw provider from %pOF\n` | `:4888` / `:4930` | **提供方注册成功的唯一凭据**——排查 `-EPROBE_DEFER` 死循环时就靠它（`06-devicetree.md` §7.4） |

打开办法（`pr_debug` 走 dynamic debug）：

```bash
# 需要 CONFIG_DYNAMIC_DEBUG；否则只能改 clk.c 顶部加 #define DEBUG 重编
echo 'file drivers/clk/clk.c +p' > /sys/kernel/debug/dynamic_debug/control

# 或者只开某一条（按行号）
echo 'file drivers/clk/clk.c line 4930 +p' > /sys/kernel/debug/dynamic_debug/control

# 启动阶段的（如 :4888/:4930）要用 cmdline，debugfs 那时还没起
#   dyndbg="file drivers/clk/clk.c +p"
```

最后一条注释很重要：`Added clk_hw provider from ...` 发生在**驱动 probe 期间**，
很多是 `core_initcall` / `arch_initcall` 级别，**远早于你能登录去写 dynamic_debug**。
要看这些必须走 cmdline 的 `dyndbg=`。

### §5.2 WARN 逐字对照表

`drivers/clk/clk.c` 的 WARN 分两类（重跑：`WARN(` 9 条、`WARN_ON(` 8 条、
`WARN_ON_ONCE` 12 条）。**带消息串的那 9 条**是排查时真正有用的，逐字如下：

| 消息串（逐字） | 位置 | 触发条件 | 拿到它先查什么 |
|---|---|---|---|
| `%s already unprotected\n` | `drivers/clk/clk.c:854` | `protect_count == 0` 还在 unprotect | `clk_rate_exclusive_put` 多调了一次 |
| `%s already unprepared\n` | `:988` | `prepare_count == 0` 还在 unprepare | prepare/unprepare 不配对，最常见是错误路径上重复 `clk_disable_unprepare` |
| `Unpreparing critical %s\n` | `:992` | `prepare_count == 1` 且带 `CLK_IS_CRITICAL` | 有人在关一个被声明为关键的时钟；查是谁 |
| `Unpreparing enabled %s\n` | `:1002` | `enable_count > 0` 时 unprepare | 次序错了：必须先 `clk_disable` 再 `clk_unprepare`，即 `clk_disable_unprepare` 而不是反着来 |
| `%s already disabled\n` | `:1129` | `enable_count == 0` 还在 disable | enable/disable 不配对 |
| `Disabling critical %s\n` | `:1132` | `enable_count == 1` 且带 `CLK_IS_CRITICAL` | 同 `:992` |
| `Enabling unprepared %s\n` | `:1188` | prepare 之前就 enable | **这条会让 `clk_enable` 返回 `-ESHUTDOWN`**（`:1190`），是 clk.c 里 `-ESHUTDOWN` 的唯一来源（§6） |
| `%s: invalid NULL in %s's .parent_names\n` | `:4131-4133` | `parent_names[i]` 是 NULL | 提供方驱动的父名数组里有洞 |
| `Must specify parents if num_parents > 0\n` | `:4149` | 声明了父个数却没给父信息 | `clk_init_data` 里 `num_parents` 和 `parent_names`/`parent_data`/`parent_hws` 三者只填了一半 |

另外两类不带串的：

| 类别 | 位置举例 | 怎么读 |
|---|---|---|
| `WARN_ON(...)` 参数检查 | `:665`、`:912`、`:1555`、`:1590`、`:1614`、`:1985`、`:4225`、`:4622` | 只有回溯没有消息串，**按行号去源码看是哪个条件**。`:4225` 的 `WARN_ON(!init->ops)` 是提供方注册时最常撞的一条 |
| `WARN_ON_ONCE(...)` 锁归属断言 | prepare 锁：`:223`/`:224`/`:231`/`:232`；enable 锁：`:261`/`:262`/`:271`/`:272` | 这 8 条是**锁递归/跨线程解锁**的探测器。它们是 `_ONCE`——**同一个 bug 只报一次**，所以在 dmesg 里看到一条就要当真，不要因为"只出现一次"而忽略 |

这里正好补上 §5.1 的对照：**clk 的"只报一次"机制只用在 `WARN_ON_ONCE` 断言上，
`pr_*` 一条都没有用**。所以你在 dmesg 里看到某条 clk 警告只出现了一次，
判断依据是它属于上面哪一类，而不是"内核限流了"。

---

## §6 错误码分诊

### §6.1 先看总量：哪些码根本不存在

一手数据（重跑：`for c in ...; do /usr/bin/grep -c -- "-$c" drivers/clk/clk.c; done`）：

| 错误码 | `drivers/clk/clk.c` 里出现次数 | 是否值得单独查 |
|---|---|---|
| `-EINVAL` | **30** | 太多了，必须按语义分组，见 §6.2 |
| `-ENOMEM` | 14 | 不用查，含义唯一 |
| `-ENOENT` | 12 | 见 §6.3 |
| `-EBUSY` | 6 | 全部集中在一类语义上，见 §6.4 |
| `-EPROBE_DEFER` | 3 | 见 §6.5 |
| `-EPERM` | 1 | 唯一点，`:2791` |
| `-EEXIST` | 1 | 唯一点，`:3803` |
| `-ESHUTDOWN` | 1 | 唯一点，`:1190` |
| `-EOPNOTSUPP` / `-ENOSYS` / `-ERANGE` / `-EIO` / `-ENODEV` | **全部 0** | — |

最后一行是本节最有用的一条：

> **在 `drivers/clk/clk.c` 里拿到 `-EOPNOTSUPP` / `-ENOSYS` / `-ERANGE` / `-EIO` / `-ENODEV`
> 是不可能的。** 如果你的日志里 clk 相关调用返回了这些码，来源一定是
> **提供方驱动**（`drivers/clk/<厂商>/`）或者更下层（regmap / i2c / SCMI 传输层），
> 不是 clk 核心。这一条能立刻把排查范围砍掉一半。

三个"唯一点"的码顺手记住，因为一撞上就等于点名了一行代码：

| 码 | 唯一出处 | 含义 |
|---|---|---|
| `-EPERM` | `drivers/clk/clk.c:2791`（`clk_core_set_parent_nolock`） | 提供方**没有实现 `.set_parent`**。注意不是 `-ENOSYS`、不是 `-EOPNOTSUPP`（`01-api.md` §错误码） |
| `-EEXIST` | `:3803`（`__clk_register`） | 同名时钟已注册；配套 `pr_debug` 在 `:3801` |
| `-ESHUTDOWN` | `:1190`（`clk_core_enable`） | prepare 之前就 enable；配套 `WARN` 在 `:1188`（§5.2） |

`drivers/clk/` 下另外四个核心文件（`clk-conf.c` + `clk-bulk.c` + `clk-devres.c` + `clkdev.c`）
的分布完全不同：`-EPROBE_DEFER` **5**、`-ENOMEM` 6、`-ENOENT` 4、`-EINVAL` 1、`-ENODEV` 1。
这些文件是**取用侧**，所以 `-EPROBE_DEFER` 占比高是正常的。

### §6.2 `-EINVAL`（30 处）按语义分四组

30 处堆在一起没有诊断价值，按**它在回答什么问题**分组才有：

| 组 | 语义 | 出处（函数 @ 行） | 拿到它先查什么 |
|---|---|---|---|
| **A. 提供方注册时的必选回调缺失** | "你的 `clk_ops` 组合不合法" | `__clk_register` @ `drivers/clk/clk.c:3813`、`:3820`、`:3827`、`:3835`、`:4226` | 四条**都配了 `pr_err` 点名**（§6.6），直接照 dmesg 改驱动 |
| **B. 父关系不成立** | "这个时钟不是那个时钟的父" | `clk_fetch_parent_index` @ `:1937`、`:1968`；`clk_hw_get_parent_index` @ `:1986`；`clk_mux_determine_rate_flags` @ `:741` | 去 `clk_possible_parents` 看候选表里到底有没有它（§3.5） |
| **C. 参数/范围非法** | "你传进来的值本身不对" | `clk_set_rate_range_nolock` @ `:2592`（min>max，**配 `pr_err`**）、`:2605`；`clk_core_set_phase_nolock` @ `:2888`；`clk_core_update_duty_cycle_nolock` @ `:3020`；`clk_set_duty_cycle` @ `:3108`；`clk_notifier_register` @ `:4671`；`clk_notifier_unregister` @ `:4719`；`clk_core_determine_round_nolock` @ `:1543` | 大多是 NULL 参数或 min/max 反了 |
| **D. DT / clkspec 解析失败** | "DTS 里的引用解析不出来" | `of_clk_get_hw_from_clkspec` @ `:5129`；`of_clk_hw_onecell_get` @ `:4851`；`of_clk_src_onecell_get` @ `:4836`；`of_clk_detect_critical` @ `:5380` | `:4851`/`:4836` **配了 `pr_err` 打印越界下标**（§6.6），是 `#clock-cells` 写错的直接证据 |

剩下的散落几处（`:871` 的 `clk_core_rate_nuke_protect`、`:2456` 的
`clk_core_set_rate_nolock`、`:4093` 的 `clk_cpy_name`、`:4148` 的父信息缺失）
在 §5.2 或 §6.4 里已经从别的角度覆盖了。

### §6.3 `-ENOENT`：一个"被改写"的错误码

这一节讲一个**改写路径**，是本篇最容易被忽略、又最影响判断的一条。
`clk_get` 的实现只有 12 行（`drivers/clk/clkdev.c:100-112`）：

```c
struct clk *clk_get(struct device *dev, const char *con_id)
{
	const char *dev_id = dev ? dev_name(dev) : NULL;
	struct clk_hw *hw;

	if (dev && dev->of_node) {
		hw = of_clk_get_hw(dev->of_node, 0, con_id);
		if (!IS_ERR(hw) || PTR_ERR(hw) == -EPROBE_DEFER)
			return clk_hw_create_clk(dev, hw, dev_id, con_id);
	}

	return __clk_get_sys(dev, dev_id, con_id);
}
```

读第 7 行那个条件：**只有成功、或者 `-EPROBE_DEFER`，才会用 DT 的结果**。
其他任何错误都被**丢掉**，然后落到 `__clk_get_sys`（按名字查 clkdev 全局表，
即 `06-devicetree.md` §0 的路径 ③），而它找不到时返回的是
`-ENOENT`（`drivers/clk/clkdev.c:75`）。后果：

| 走哪个 API | DT 解析失败时驱动看到什么 |
|---|---|
| `clk_get` / `devm_clk_get` / `devm_clk_get_optional*` / `clk_bulk_get*` | **一律 `-ENOENT`**，原始原因（`clock-names` 没这个名字 → `-EINVAL`，见 `06-devicetree.md` §3.3）**丢失** |
| `of_clk_get_by_name` / `of_clk_get` / `of_clk_get_hw` | 原样返回，`-EINVAL` 看得见 |

对排查的两条直接影响：

1. **拿到 `-ENOENT` 不能推出"DTS 里没写这个时钟"**。它同样可能是名字拼错、
   `#clock-cells` 不匹配、提供方注册失败。要区分只能开 §5.1 那些 `pr_debug`，
   或者临时把驱动改成调 `of_clk_get_by_name` 看真码。
2. **`devm_clk_get_optional` 的"可选"语义比你想的宽**。
   `clk_get_optional`（`include/linux/clk.h:1131-1139`）只把 `-ENOENT` 转成 NULL：

   ```c
   	if (clk == ERR_PTR(-ENOENT))
   		return NULL;
   ```

   而经过上面的改写，**几乎所有 DT 配置错误都已经变成了 `-ENOENT`**。
   所以 `devm_clk_get_optional` 会把"DTS 写错了"和"这个时钟本来就可选"
   一起吞掉、返回 NULL、**一条日志都没有**——这正是 `06-devicetree.md` §7.3
   那个"时钟从来没被使能"的静默失败的机制根源。

`clk_bulk_get_optional` 的写法可以对照看（`drivers/clk/clk-bulk.c:96-101`）：

```c
			if (ret == -ENOENT && optional)
				continue;

			if (ret != -EPROBE_DEFER)
				dev_err(dev, "Failed to get clk '%s': %d\n",
					clks[i].id, ret);
```

它比单个版本好：**非 optional 或非 `-ENOENT` 的失败会打一条 `dev_err`**
（`06-devicetree.md` 那些静默失败在 bulk 路径上是有日志的），
而 `-EPROBE_DEFER` 被特意排除在打印之外——这是内核里"defer 不算错"的标准写法。

### §6.4 `-EBUSY`（6 处）：全部是"时机不对"，不是"设备忙"

6 处一个不落，语义高度集中：

| 位置 | 函数 | 判据 |
|---|---|---|
| `drivers/clk/clk.c:2451` | `clk_core_set_rate_nolock` | `protect_count != 0`——有消费者用 `clk_rate_exclusive_get` 锁住了频率 |
| `:2468` | `clk_core_set_rate_nolock` | **PRE 通知链里有人否决**（`01-api.md` 已定：不是 `-EINVAL`）；配 `pr_debug` 在 `:2159` |
| `:2795` | `clk_core_set_parent_nolock` | `CLK_SET_PARENT_GATE` 且 **`prepare_count != 0`**（判据是 prepare 不是 enable） |
| `:2798` | `clk_core_set_parent_nolock` | `protect_count != 0` |
| `:2896` | `clk_core_set_phase_nolock` | `protect_count != 0` |
| `:3057` | `clk_core_set_duty_cycle_nolock` | `protect_count != 0` |

> 一句话结论：**clk 的 `-EBUSY` 只有两个来源——频率保护计数（4/6 处）和
> 通知链否决（1 处）、`CLK_SET_PARENT_GATE`（1 处）。** 它从来不表示"硬件忙"。
> 所以拿到 `-EBUSY` 的第一动作是 `cat <名>/clk_protect_count`，
> 不是去看硬件寄存器。

### §6.5 `-EPROBE_DEFER`（3 处）

| 位置 | 函数 | 什么时候产生 |
|---|---|---|
| `drivers/clk/clk.c:523` | `clk_core_fill_parent_index` | 父时钟还没注册 → 这个时钟先当孤儿 |
| `:5126` | `of_clk_get_hw_from_clkspec` | **初值就是它**：`struct clk_hw *hw = ERR_PTR(-EPROBE_DEFER);`，遍历提供方链表没找到就保持这个值 |
| `:5339` | `parent_ready` | 用于判断某个消费者的所有父是否就绪 |

`:5126` 那个"初值即 defer"的写法解释了一个常见困惑：
**提供方节点根本不存在时，你拿到的也是 `-EPROBE_DEFER` 而不是 `-ENOENT`**——
因为遍历的是已注册提供方链表，"没找到"和"还没注册"在这里无法区分。
所以 `06-devicetree.md` §7.4 那个"永远 defer"的排查，
判据只能是 §5.1 的 `Added clk_hw provider from ...`。

### §6.6 点名了函数/时钟的错误（能直接照着改）

这一节列的是**日志本身带足信息、不需要再排查**的那几条。全部 `pr_err`：

| 消息串（逐字） | 位置 | 你要做什么 |
|---|---|---|
| `%s: %s must implement .round_rate or .determine_rate in addition to .recalc_rate\n` | `drivers/clk/clk.c:3811` | 给这个时钟补 `.determine_rate` |
| `%s: %s must implement .get_parent & .set_parent\n` | `:3818` | 补这两个回调 |
| `%s: %s must implement .get_parent as it has multi parents\n` | `:3825` | 多父时钟必须能查当前父 |
| `%s: %s must implement .set_parent & .set_rate\n` | `:3833` | 同时声明了两种能力就都要实现 |
| `%s: invalid clock index %u\n` | `:4835`（`of_clk_src_onecell_get`） | 出现它说明提供方用的是**已废弃**的 `clk_onecell_data`；下标越界 |
| `%s: invalid index %u\n` | `:4850`（`of_clk_hw_onecell_get`） | **`#clock-cells` / DTS 下标写错的最直接证据**（`06-devicetree.md` §7.4） |
| `clk: Failed to runtime PM get '%s' for clk '%s'\n` | `:165` | 时钟控制器的 runtime PM resume 失败；它会让 `cat clk_summary` 直接读失败（§3.2） |
| `%s: clk %s dev %s con %s: invalid range [%lu, %lu]\n` | `:2589` | min > max，四个标识全打出来了，直接定位到是哪个消费者 |
| `%s: unregistered clock: %s\n` | `:4436` | 对一个没注册的时钟做操作 |

另外 `drivers/clk/clk-conf.c` 那 6 条 `pr_err`（`assigned-clock*` 相关）
已经在 `06-devicetree.md` §9 逐字列过，这里不重复。

---

## §7 KUnit：唯一能在本机验证核心行为的手段

### §7.1 两个测试文件、17 个 suite

| Kconfig | 文件 | suite 数 |
|---|---|---|
| `CONFIG_CLK_KUNIT_TEST`（`drivers/clk/Kconfig:476`） | `drivers/clk/clk_test.c` | **12** |
| `CONFIG_CLK_GATE_KUNIT_TEST`（`drivers/clk/Kconfig:483`） | `drivers/clk/clk-gate_test.c` | **5** |

两者都是 `tristate ... if !KUNIT_ALL_TESTS` + `depends on KUNIT` +
`default KUNIT_ALL_TESTS`（`drivers/clk/Kconfig:477-479`/`:484-486`），
所以 **只要打开 `CONFIG_KUNIT_ALL_TESTS` 就自动带上，不用单独找**。

`drivers/clk/clk_test.c` 的 12 个 suite（名字逐字，`kunit.filter_glob` 要用它）：

| suite 名 | 定义处 | 测什么 |
|---|---|---|
| `clk-test` | `drivers/clk/clk_test.c:300` | 单个时钟的基本 get/set rate |
| `clk-uncached-test` | `:421` | `CLK_GET_RATE_NOCACHE` 的行为（对应 §3.2 第 5 列） |
| `clk-multiple-parents-mux-test` | `:585` | 多父 mux |
| `clk-orphan-transparent-multiple-parent-mux-test` | `:929` | 孤儿 + 多父 |
| `clk-single-parent-mux-test` | `:1206` | 单父 |
| `clk-orphan-transparent-single-parent-test` | `:1292` | 孤儿 + 单父 |
| `clk-orphan-two-level-root-last-test` | `:1422` | 两级孤儿、根最后注册 |
| `clk-range-test` | `:1757` | 频率范围 |
| `clk-range-maximize-test` | `:1945` | 范围上界 |
| `clk-range-minimize-test` | `:2125` | 范围下界 |
| `clk-leaf-mux-set-rate-parent` | `:2236` | `CLK_SET_RATE_PARENT` 透传 |
| `clk-mux-notifier` | `:2391` | 通知链 |

这张表本身就是一条信息：**12 个 suite 里 3 个专测孤儿、3 个专测频率范围**。
说明社区认定这两块是 clk 核心最容易出错的地方——正好和
`06-devicetree.md` §7.2（孤儿）以及 `04-extensions.md`（范围合并）对上。

`drivers/clk/clk-gate_test.c` 的 5 个 suite：
`clk-gate-register-test`（`:126`）、`clk-gate-test`（`:233`）、
`clk-gate-invert-test`（`:306`）、`clk-gate-hiword-test`（`:378`）、
`clk-gate-is_enabled-test`（`:453`，正好测 §3.2 那个 `hardware enable` 列的来源）。

### §7.2 怎么跑

```bash
# 本机 UML/QEMU 跑（在内核源码树顶层）
./tools/testing/kunit/kunit.py run --kunitconfig=drivers/clk

# 只跑孤儿相关的三个 suite
./tools/testing/kunit/kunit.py run 'clk-orphan*'

# 已经编进目标板内核时，结果在
cat /sys/kernel/debug/kunit/clk-test/results
```

注意：**本套文档没有编译、也没有运行过这些测试**（见 `99-evidence.md` §5），
上面的命令是标准 KUnit 用法，suite 名逐字来自源码。**输出格式没有在此编造。**

---

### §7.3 故障注入：clk **没有**，所以 §7 标题里的"唯一"是字面意思

重跑：`/usr/bin/grep -rn 'fault_inject|should_fail|DECLARE_FAULT_ATTR' drivers/clk/` → **无输出**（用 `-E`）。

也就是说 clk 子系统里：

- **没有** `fault-inject` 钩子（对比 `failslab` / `fail_page_alloc` / `fail_make_request` 那一套）
- **没有** debugfs 下的失败概率/次数旋钮
- **没有**任何"让 `clk_prepare` 第 N 次失败"这类开关

后果很直接：**clk 的错误路径无法从外部触发**。§6 那张错误码表里的每一条，
你要么在真机上凑出触发条件，要么改代码。三条现实可行的替代手段：

| 想测什么 | 怎么做 | 代价 |
|---|---|---|
| 核心自身的逻辑（孤儿重挂、范围合并、父切换） | **KUnit**（§7.1/§7.2）——它用假的提供方 ops 直接在核心上跑，不需要硬件 | 最低 |
| 提供方回调返回错误时核心怎么办 | 在自己的提供方驱动里临时 `return -EIO;` 重编 | 要能重编内核/模块 |
| `clk_disable_unused` 关掉某个时钟会发生什么 | 反过来用 `clk_ignore_unused`（§2.1）**对照两次启动** | 只能观察，不能定点 |

注意最后一行的不对称：`clk_ignore_unused` 只能让批量关闭**整体不发生**，
**没有任何开关能让它只对指定的某一个时钟发生**。所以"是哪个时钟被关掉导致挂死"
这个问题只能靠 §2.1 的两次启动对照 + `clk_summary` 差分去缩小范围。

还有一处**看起来像故障注入、其实不是**的东西：`CLOCK_ALLOW_WRITE_DEBUGFS`
（§1.2）。它开出来的 `clk_rate` / `clk_parent` 写接口是**直接改硬件**，
不是模拟失败——写进去之后核心的缓存和硬件都真的变了，而且
`clk_summary` 里的值从此不可信（§1.2 的横幅就是为此存在的）。
拿它当故障注入用，会得到一个**状态被污染、无法复现**的系统。

## §8 一页速查

本子系统**没有字符设备、没有 sysfs 类、也没有配套用户态工具**
（重跑：`/usr/bin/grep -rn 'misc_register\|class_create\|cdev_add' drivers/clk/*.c` → 无输出）。
所以 clk 的全部对外可观测面就是：**debugfs + tracepoint + dmesg**，三样，没有第四样。

| 症状 | 第一动作 | 第二动作 | 章节 |
|---|---|---|---|
| 启动挂死无日志 | cmdline 加 `clk_ignore_unused` | 找 `enable count`=0 而 `hardware enable`=`Y`（可信）的时钟 | §2.1 |
| `clk_set_rate` 返回 0 但没变 | `cat <名>/clk_flags` 找 `CLK_SET_RATE_PARENT` | 没有它就是静默返回 0（`clk.c:2445-2447`） | §3.4 |
| `clk_set_rate` 返回 `-EBUSY` | `cat <名>/clk_protect_count` | 非 0 就是有人 `clk_rate_exclusive_get` | §6.4 |
| `clk_set_parent` 返回 `-EPERM` | 不用查，提供方没实现 `.set_parent` | — | §6.1 |
| `clk_set_parent` 返回 `-EBUSY` | 看 `clk_prepare_count`（不是 enable！） | `CLK_SET_PARENT_GATE` 要求先 unprepare | §6.4 |
| `clk_enable` 返回 `-ESHUTDOWN` | 不用查，忘了先 `clk_prepare` | dmesg 里有 `Enabling unprepared <名>` | §5.2 |
| `clk_get` 返回 `-ENOENT` | **不要**直接断定 DTS 没写 | 开 `dyndbg` 看真实原因（错误码被改写过） | §6.3 |
| 一直 `-EPROBE_DEFER` | `dyndbg` 找 `Added clk_hw provider from` | 没有这行就是提供方没注册 | §5.1 / §6.5 |
| 时钟树是平的 / 有孤儿 | `cat clk_orphan_summary` | `cat <名>/clk_possible_parents` 找 `(missing)` | §3.5 |
| 时钟从没被使能、无任何日志 | `clk_get_rate(clk) == 0`？ | 查是不是 `devm_clk_get_optional` 吞了错误 | §6.3 |
| 频率被夹住了 | `cat <名>/clk_min_rate`、`clk_max_rate` | 是所有消费者约束的合并结果 | §3.6 |
| 想看操作时序 | `echo 1 > events/clk/enable` | **不要**去找“缺 `_complete`”的对——那个信号不存在；改看有没有 entry | §4.2.1 / §4.4 |
| dmesg 里有 `WRITEABLE clk DebugFS` 横幅 | 这个内核的 `clk_rate` 值不可信 | 有人可能手改过 | §1.2 |
| `cat clk_summary` 直接失败 | 看有没有 `Failed to runtime PM get` | 时钟控制器 runtime PM resume 失败 | §6.6 |

---

## §9 官方文档没有覆盖的部分

这一节是给"想按官方文档核对本篇"的读者用的——**核对不了**：

| 本篇内容 | `Documentation/driver-api/clk.rst`（307 行）里有吗 |
|---|---|
| debugfs（§3，14 个文件 + 4 个全局文件） | **一个字都没有** |
| tracepoint（§4，20 个事件） | **一个字都没有** |
| `clk_ignore_unused`（§2.1） | 只在 `Documentation/admin-guide/kernel-parameters.txt:594` 有一句话 |
| `CLOCK_ALLOW_WRITE_DEBUGFS`（§1.2） | 无（只有源码里那段注释，`drivers/clk/clk.c:3354-3358`） |
| KUnit（§7） | 无 |
| 错误码语义（§6） | 无 |

`clk.rst` 实际覆盖的是**提供方怎么实现 `clk_ops`** 那一层（必选回调矩阵、
prepare/enable 分离的设计理由、`container_of` 模式），这些已经落到
`01-api.md`、`03-internals.md`、`04-extensions.md` 里了。

> 所以对 clk 而言：**"官方文档"能帮你写驱动，帮不了你查故障。**
> 本篇的每一条都必须靠源码行号自证，这也是它行号密度最高的原因。
> 未验证项（未编译、未在真机上运行过这些命令）见 `99-evidence.md` §5。

---

## §10 读到这里之后去哪

| 想继续了解 | 去哪篇 |
|---|---|
| 每个 API 的完整错误码语义 | `01-api.md` §错误码 |
| `clk_core` / `clk` 两层结构、`protect_count` 是什么 | `02-datastructures.md` |
| `clk_disable_unused`、调频协商的完整流程 | `03-internals.md` |
| 基础时钟类型（gate/divider/mux）各自的 ops | `04-extensions.md` |
| 消费者/提供方的代码骨架 | `05-usage.md` |
| DTS 属性、静默失效决策树、`clk-conf.c` 的错误消息表 | `06-devicetree.md` |
| 为什么要分 prepare / enable 这些"想不通"的问题 | `08-feynman.md` |
| 本套文档的取证范围和未验证项 | `99-evidence.md` |
