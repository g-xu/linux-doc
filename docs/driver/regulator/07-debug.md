---
title: 调试方法
---

# 07 调试：从症状到证据

> arm64-only。所有命令都基于本树 v6.1.158 的源码推导，
> **示例输出是按 `seq_printf` / `TP_printk` 的格式串推出来的，不是抄的真实机器**
> —— 凡是标注"格式来自 `<行号>`"的地方才有源码依据。
> 这一点同时记在 99 §5.1。

## §0 按症状找工具

| 症状 | 第一手段 | 章节 |
|---|---|---|
| 电源"开成功"了但设备没反应 | `dmesg \| grep dummy` | **§2.1** |
| DTS 约束好像没生效 | 读 `/sys/class/regulator/*/min_microvolts` 的**内容** | **§3.2**、06 篇 §8.2 |
| `set_voltage` 返回 0 但电压不对 | `regulator_summary` 看所有消费者的区间 | **§6.2** |
| `set_voltage` 返回 `-EINVAL` / `-EPERM` | 先分清是哪一类 | **§7** |
| 开机慢，怀疑等电源 | `regulator_enable` 三段 tracepoint | **§4.2** |
| 开机 30 秒后一批 `disabling` | `dmesg` + `regulator_summary` 的 `use/open` 列 | **§2.3**、**§6.2** |
| 想在没有驱动的情况下试一路电 | virtual / userspace consumer | **§5** |
| 谁在用这一路 | `regulator_summary` 的缩进 | **§6.2** |
| 想看 DT 解析细节 | `-DDEBUG` 整目录开 | **§1.1**、06 篇 §8.3 |

本篇不覆盖 DTS 怎么写、写错的四种形态 —— 那在 **06 篇 §8/§9**。
本篇是"**已经跑起来了，怎么看它在干什么**"。

---

## §1 编译期开关

### §1.1 只有一个真正的调试开关

| 开关 | 位置 | 作用 |
|---|---|---|
| `CONFIG_REGULATOR_DEBUG` | `drivers/regulator/Kconfig:28-31` | 给整个目录加 `-DDEBUG`（`Makefile:186`） |
| `CONFIG_DEBUG_FS` | 通用 | 决定 §6 那三组 debugfs 文件在不在 |
| `CONFIG_REGULATOR_VIRTUAL_CONSUMER` | `Kconfig:40` | §5.2 的调压工具 |
| `CONFIG_REGULATOR_USERSPACE_CONSUMER` | `Kconfig:50` | §5.1 的开关工具 |

**`CONFIG_REGULATOR_DEBUG` 不是"多打几条日志"**：

```
drivers/regulator/Makefile:186:  ccflags-$(CONFIG_REGULATOR_DEBUG) += -DDEBUG
```

`-DDEBUG` 让 `drivers/regulator/` 下**所有** `dev_dbg` / `rdev_dbg` 无条件编译进去并输出。
这是本子系统唯一的"一键全开"，代价是一颗多路 PMIC 开机刷上百行。

只想看一个文件用 dynamic debug，但**必须走 cmdline**（regulator 的解析发生在 debugfs 挂载之前）：

```
dyndbg="file drivers/regulator/of_regulator.c +p"
```

还有一个通用开关值得一提：`lockdep`。
`core.c` 里有 8 处 `lockdep_assert_held_once`
（`:1875`、`:2415`、`:2853`、`:2878`、`:2898`、`:3017`、`:3098`、`:3967`），
自己写 coupler 或者在 `ops` 回调里调消费者 API 时，
**lockdep 是唯一能在第一时间抓住三层锁用错的东西**（03 篇 §B1）。

### §1.2 没有故障注入

全目录没有 `fault-inject` / `should_fail` 的钩子。
要模拟"电源打不开"只能改驱动的 `ops->enable` 返回错误，
或者用 §5.2 的 virtual consumer 请求一个硬件给不出的电压。

---

## §2 dmesg：日志

### §2.0 日志可见性：本子系统全是"每次都打"

一条重要的普查结论：

```
/usr/bin/grep -rn '_once(\|_ratelimited(' drivers/regulator/*.c
```

**核心（`core.c` / `helpers.c` / `devres.c` / `of_regulator.c` / `irq_helpers.c`）
里没有任何 `dev_*_once` / `dev_*_ratelimited` 日志**。
出来的 8 条命中全是 `lockdep_assert_held_once`（见 §1.1），**那不是日志**。
唯一用了 `dev_*_ratelimited` 的是 `ti-abb-regulator.c`
（`:173`、`:200`、`:313` 等），那是 OMAP 平台的驱动，**arm64 上碰不到**。

一个必须点明的例外：`WARN_ON_ONCE` 有 3 处
（`core.c:205` 引用计数为负、`core.c:344` ww_mutex 返回值意外、`core.c:6076`），
**那三条是"只会出现一次"的** —— 它们是内核 bug 断言，不是诊断日志，
上面那条结论不覆盖它们。

三条推论：

1. **看到一条 warn 就是发生了一次**，没有"第一次之后被吞掉"这回事。
   `dmesg | grep -c` 的计数是可信的。
2. **反过来也成立：会刷屏。** 一颗 12 路 PMIC 上每路都漏了 supply，
   开机就是 12 条 `using dummy regulator`；一个消费者在循环里
   `regulator_get` / `put`，每次都打一条。
3. 前缀形状：`core.c` 用 `rdev_*` 宏包出来的是
   `<regulator名>: <消息>`（02 篇 §9）；`of_regulator.c` 里那批用的是
   **`pr_*`**，**前缀里没有设备名，只有 `%pOFn` 的节点名**（06 篇 §9 末）。

### §2.1 第一条要查的：dummy

```
dmesg | grep 'using dummy regulator'
```

消息本体（`core.c:2230`）：

```
<消费者设备名>: supply <id> not found, using dummy regulator
```

**这是 arm64 上最重要的一条日志**。它意味着：
`regulator_get` 返回了一个**有效指针**，后续 `regulator_enable` 返回 **0**，
你的驱动 probe 成功、跑得好好的，**而那一路电根本没被碰过**（05 篇 §1.3）。

判据：`have_full_constraints()`（`core.c:112-114`）在 arm64 DT 系统上恒真，
所以走的是 `:2225-2231` 那个分支而不是返回错误。

配套的一条：`dmesg | grep 'Failed to resolve'`
（`core.c:2100-2101`）—— 那是**提供者**侧的 `vin-supply` 解析失败，
返回 `-EPROBE_DEFER`。**反复出现说明上游永远不会来**，
不是"再等等就好"。

一路 `regulator-fixed` 注册成功会打（`fixed.c:295`，dbg 级）：

```
<名字>: <微伏>uV
```

这是 03 篇 §A3 提到的那行 —— 它是 `dev_dbg`，
**默认看不到**，要 §1.1 的开关。

### §2.2 `Restricting voltage` —— 措辞会骗你

```
<regulator名>: Restricting voltage, <min>-<max>uV
```

来源 `core.c:550`，紧跟着的是 `return -EINVAL`（`:552`）。

**它不是"我已经帮你限制到这个范围了"，而是"你要的区间和别人的区间没有交集，
我什么都没做"**（03 篇 §B3、05 篇 §1.5）。
这条日志出现时 `regulator_set_voltage` **一定失败了**。

打印的 `<min>-<max>` 是**求交之后**的区间（`regulator_check_consumers`
在 `:527`–`:553` 逐个消费者收窄），所以它其实告诉了你
"别人把范围压到了哪里" —— 这是判断"是谁挡了我"的第一手线索。
下一步去 §6.2 的 `regulator_summary` 看到底是哪个消费者。

### §2.3 开机 30 秒后那一批

```
<regulator名>: disabling
```

来源是 `regulator_late_cleanup`（`core.c:6215`，日志在 `:6240`，03 篇 §A7）—— 一个
**延迟 30 秒**执行的 `late_initcall` 收尾动作，
把"没有任何消费者认领、又没写 `regulator-always-on` / `regulator-boot-on`"的路关掉。

症状特征：**开机看起来一切正常，30 秒后某个设备突然挂掉**。
判据是时间戳 —— `dmesg` 里这批消息的时间戳会集中在 30s 附近，
和其他 probe 日志明显断开。

确认方法：`regulator_summary` 里那一路的 `use` 和 `open` 都是 0（§6.2）。
判据在源码里就是 `if (rdev->use_count) goto unlock`（`core.c:6229`）——
**它看的是 `use_count`（有没有人 enable 过），不是 `open_count`（有没有人 get 过）**。
所以"我 `regulator_get` 了但没 `enable`"**挡不住**这次清理。

修法是两种之一：让消费者真的 `regulator_enable`，或者在 DTS 里写
`regulator-always-on`（对应 `:6221` 的 `if (c && c->always_on) goto unlock`）。

同一个函数还会打另外两条：

| 消息 | 位置 | 含义 |
|---|---|---|
| `couldn't disable: %pe` | `core.c:6243` | 想关但驱动的 `ops->disable` 报错了 |
| `incomplete constraints, leaving on` | `core.c:6250` | arm64 上**看不到** —— `have_full_constraints()` 恒真（03 篇 §A7.1），走的是 `:6236` 那个分支 |

### §2.4 关键消息一览

| 消息 | 位置 | 级别 | 含义 |
|---|---|---|---|
| `supply %s not found, using dummy regulator` | `core.c:2230` | warn | §2.1 |
| `Failed to resolve %s-supply for %s` | `core.c:2100-2101` | err | 上游没来，`-EPROBE_DEFER` |
| `Restricting voltage, %u-%uuV` | `core.c:550` | 见 §2.2 | 区间无交集，**已失败** |
| `disabling` | `core.c:6240` | info | §2.3 |
| `Failed to set supply %s` | `core.c:5709-5710` | err | 平台数据路径的 consumer 映射失败 |
| `unable to resolve supply early: %pe` | `core.c:5652-5653` / `:5692-5693` | dbg | bypass 模式的鸡生蛋问题（`core.c:5682-5685` 的 `FIXME`，03 篇 §C2） |
| `will resolve supply early: %s` | `core.c:5686-5687` | dbg | 同上 |
| `Looking up %s-supply from device tree` | `core.c:476` | dbg | 06 篇 §8.3 |
| `Looking up %s property in node %pOF failed` | `core.c:486-487` | dbg | 06 篇 §8.3 |

DTS 相关的全套消息（提供者侧解析、耦合、`regulator-fixed`）在 **06 篇 §9**，
本篇不重复。

---

## §3 sysfs：`/sys/class/regulator/regulator.N/`

### §3.1 全部只读

```
/sys/class/regulator/regulator.N/
```

`N` 是一个全局自增序号（`core.c:5631-5632` 的
`dev_set_name(&rdev->dev, "regulator.%lu", atomic_inc_return(&regulator_no))`）——
**和 DTS 顺序、和 PMIC 通道号都没有关系，重启后可能变**。
按名字找，别按编号找：

```
grep -H . /sys/class/regulator/*/name
```

**这个目录下的属性没有一个可写**（全是 `DEVICE_ATTR_RO`，
`core.c:672`–`:986`）。想从用户态**改**电源状态只有 §5 那两个模块。

### §3.2 可见性由 `ops` 决定，内容才反映 DTS

`regulator_attr_is_visible`（`core.c:5167`）决定哪些文件出现：

| 属性 | 出现条件 | 行号 |
|---|---|---|
| `name` / `num_users` / `type` | 恒在 | `core.c:5176-5179` |
| `microvolts` | 四种取值途径任一可用，**判断时会真的读一次硬件** | `core.c:5182-5189` |
| `microamps` | `ops->get_current_limit` | `core.c:5191-5192` |
| `opmode` | `ops->get_mode` | `core.c:5194-5195` |
| `state` | `rdev->ena_pin \|\| ops->is_enabled` | `core.c:5197-5198` |
| `status` | `ops->get_status` | `core.c:5200-5201` |
| `bypass` | `ops->get_bypass` | `core.c:5203-5204` |
| 9 个错误标志属性 | `ops->get_error_flags` | `core.c:5206-5215` |
| `min_microvolts` / `max_microvolts` | `ops->set_voltage \|\| ops->set_voltage_sel` | `core.c:5218-5220` |
| `min_microamps` / `max_microamps` | `ops->set_current_limit` | `core.c:5222-5224` |
| `suspend_{standby,mem,disk}_state` | 恒在 | `core.c:5226-5229` |
| `suspend_*_microvolts` | `ops->set_suspend_voltage` | `core.c:5231-5234` |
| `suspend_*_mode` | `ops->set_suspend_mode` | `core.c:5236-5239` |
| `requested_microamps` | 无条件（不在 `is_visible` 的判断里） | `core.c:855` |

**三条容易踩的**：

1. **`min_microvolts` 文件存在 ≠ DTS 写了约束**，只说明驱动实现了 `set_voltage*`。
   要看约束在不在**必须读内容**：全 0 = 没挂上（06 篇 §8.2 解释了为什么是 0 而不是
   `constraint not defined`）。这是 05 篇 §2.2 和 06 篇 §3.3 都指向的那个检查。
2. **`ls` 这个目录会触发一次硬件读** —— `microvolts` 的可见性判断里
   直接调了 `ops->get_voltage(rdev)`（`core.c:5183`）。
   PMIC 是 I2C 的话，这就是一次 I2C 事务。
3. `suspend_*_state` 恒在（`:5226-5229`），所以 DTS 里没写
   `regulator-state-mem` 也能 `cat` 到，**内容是 0**，不代表配了。

### §3.3 几个属性的语义陷阱

| 属性 | 读出来是什么 | 陷阱 |
|---|---|---|
| `num_users` | `core.c:857-862` 读的是 **`use_count`** | 名字说"用户数"，实际是"**开启计数**"。真正的"拿了引用的人数"是 `open_count`，只在 debugfs（§6.1） |
| `state` | `enabled` / `disabled` / `unknown`（`core.c:724`/`:726`/`:728`） | 反映的是**硬件**状态；一路 always-on 的 `use_count` 可以是 0 而 `state` 是 `enabled` |
| `type` | `voltage` / `current` / `unknown`（`core.c:872`/`:874`/`:876`） | `unknown` 说明 `desc->type` 没填对（03 篇 §A3） |
| `microvolts` | 当场读硬件 | 和 `min/max_microvolts`（读的是**约束**）不是一回事 |
| `opmode` | `fast` / `normal` / `idle` / `standby` | 没有 `of_map_mode` 的驱动这里永远是 `normal`（06 篇 §1.3） |
| `requested_microamps` | 所有消费者 `uA_load` 之和 | 只在用了 `regulator_set_load` 的系统上有意义 |

### §3.4 两个软链：`of_node` 和「谁在用我」

```
ls -l /sys/class/regulator/regulator.N/of_node
ls -l /sys/class/regulator/regulator.N/          # 看还有哪些软链
```

**第二类软链是"谁在用这一路"的直接答案**：
每次 `regulator_get` 成功、且调用者是一个真实设备时，核心就在
rdev 的 sysfs 目录下建一个软链指向那个消费者设备
（`sysfs_create_link_nowarn`，`core.c:1910-1911`）。

软链的**名字**是 `"<消费者设备名>-<supply名>"`
（`core.c:1881-1882` 的 `snprintf(buf, REG_STR_SIZE, "%s-%s", dev->kobj.name, supply_name)`），
例如 `fe320000.mmc-vmmc`。

三条注意：

1. 用的是 `_nowarn` 变体，失败只有一条 dbg
   （`core.c:1913-1914` 的 `could not add device link %s: %pe`）—— **软链缺失不是错误**。
2. 同一个设备对同一路 `get` 两次 ⇒ 第二次 `-EEXIST`（`:1919`），
   **不会有第二个软链，也不会有第二个 debugfs 子目录**。
   所以**软链的数量 ≠ `open_count`**（§6.1）。
3. `regulator_get(NULL, ...)` 这种 deviceless 的消费者不会留软链
   （`:1906` 的 `if (dev)`）。

`of_node` 那个软链是另一回事：

**这是"核心认为这一路对应哪个 DT 节点"的权威答案**
（`core.c:5606` 的 `rdev->dev.of_node = of_node_get(config->of_node)`）。
排查"约束挂错了"时它比读 DTS 反推可靠得多（06 篇 §8.2 第 4 步）。

---

## §4 tracepoint：11 个，3 类

### §4.1 全清单

定义在 `include/trace/events/regulator.h`（174 行）。
**3 个 event class，11 个 event，11 个调用点，一一对应**。

| class | 字段 | `TP_printk` |
|---|---|---|
| `regulator_basic` `:15` | `name` | `name=%s`（`:29`） |
| `regulator_range` `:109` | `name` / `min` / `max` | `name=%s (%d-%d)`（`:127-128`） |
| `regulator_value` `:143` | `name` / `val` | `name=%s, val=%u`（`:159-160`） |

| tracepoint | class | 调用点 | 时机 |
|---|---|---|---|
| `regulator_enable` | basic `:33` | `core.c:2757` | 决定要开了，**硬件还没动** |
| `regulator_enable_delay` | basic `:41` | `core.c:2789` | 硬件 enable 已下发，**开始等 `enable_time`** |
| `regulator_enable_complete` | basic `:49` | `core.c:2823` | 等完了 |
| `regulator_disable` | basic `:57` | `core.c:2987` | 决定要关 |
| `regulator_disable_complete` | basic `:65` | `core.c:3006` | 关完 |
| `regulator_bypass_enable` | basic `:73` | `core.c:4810` | 进 bypass 前 |
| `regulator_bypass_enable_complete` | basic `:81` | `core.c:4816` | 进 bypass 后 |
| `regulator_bypass_disable` | basic `:89` | `core.c:4823` | 出 bypass 前 |
| `regulator_bypass_disable_complete` | basic `:97` | `core.c:4829` | 出 bypass 后 |
| `regulator_set_voltage` | range `:131` | `core.c:3641` | 带**请求区间** |
| `regulator_set_voltage_complete` | value `:163` | `core.c:3730` | 带**实际选中的值** |

**三条设计上的观察**：

1. **没有一个 `TP_CONDITION`**，也没有 `TP_fast_assign` 之外的过滤 ——
   开了就是全量。
2. **所有 event 只带 `name` 字符串**，不带 `rdev` 指针、
   不带 `use_count`、不带错误码。⇒ **tracepoint 看不出成功还是失败**：
   `regulator_set_voltage` 打了但 `..._complete` 没打，才说明失败了。
3. `name` 来自 `rdev_get_name(rdev)`（`core.c:101-109`），
   所以 trace 里的名字和 sysfs 的 `name` 属性**是同一个** ——
   可以直接对上。

### §4.2 `enable` 三段：唯一能拆出延时的地方

这是本子系统 tracepoint 设计最有价值的一处（03 篇 §A5、§C3）。
三个点把一次 `regulator_enable` 切成两段：

```
trace_regulator_enable          [core.c:2757]
   │
   │  ← 第 ① 段：取锁、检查约束、走上游、调 ops->enable
   │
trace_regulator_enable_delay    [core.c:2789]
   │
   │  ← 第 ② 段：纯粹在等 desc->enable_time / ena_gpiod 的延时
   │
trace_regulator_enable_complete [core.c:2823]
```

**诊断价值**：开机慢、怀疑卡在电源上时，
第 ② 段长 ⇒ **是 DTS/驱动里的 `enable_time` 或 `startup-delay-us` 填大了**，
改配置就能省；第 ① 段长 ⇒ **是 I2C 事务慢或者上游链条深**，
改配置没用。

```bash
cd /sys/kernel/tracing
echo 1 > events/regulator/regulator_enable/enable
echo 1 > events/regulator/regulator_enable_delay/enable
echo 1 > events/regulator/regulator_enable_complete/enable
echo 1 > tracing_on
# ... 触发 ...
cat trace
```

想看开机期的，用 cmdline：`trace_event=regulator:* trace_buf_size=4M`。

一条限制：**三个点都只有 `name`**，
所以同一路电被并发 enable 时（不同消费者），
trace 里是交错的三对，**没有任何字段能把它们配起来** ——
只能靠 `name` + 时间顺序猜。这是 §4.1 第 2 条的直接后果。

### §4.3 `set_voltage` 两段：请求 vs 实际

```
trace_regulator_set_voltage(name, min_uV, max_uV)      [core.c:3641]  ← 你要的区间
trace_regulator_set_voltage_complete(name, best_val)   [core.c:3730]  ← 实际选的值
```

**这一对是验证"返回 0 但电压不是我要的"的最直接手段**（05 篇 §1.5）：

| trace 现象 | 结论 |
|---|---|
| 两条都有，`val` 落在 `(min-max)` 内 | 正常 |
| 两条都有，`val` **不在**你的区间内 | 你的区间被别的消费者的区间收窄了（§6.2 找是谁） |
| 只有 `set_voltage` 没有 `_complete` | 失败了。去 §7 分错误码 |
| 一条都没有 | 根本没走到 `_regulator_do_set_voltage` —— 可能是"落在约束内只记账"的路径（03 篇 §A6），或者 `CHANGE_VOLTAGE` 没授权 |

最后一行是最容易误判的：**`regulator_set_voltage` 返回 0 且完全没有 trace 输出**
是一个合法的正常结果。

---

## §5 用户态调试工具：两个可选模块

**`/sys/class/regulator/` 全是只读的（§3.1），所以想从用户态
"开一下这路电试试"或者"调个电压看看"，只有这两个模块。**
它们都是**消费者**，不是提供者（04 篇 §8、§9）。

**两个都不要开在生产内核里** —— 它们是把电源控制权交给用户态。

### §5.1 userspace-consumer：只能开关

`CONFIG_REGULATOR_USERSPACE_CONSUMER`（`Kconfig:50`），
实现 `drivers/regulator/userspace-consumer.c`（181 行）。

两个 sysfs 文件（挂在这个 platform 设备自己的目录下，不是 regulator 目录）：

| 文件 | 权限 | 语义 |
|---|---|---|
| `name` | 0444 | 这一组的名字（`userspace-consumer.c:32`、`:90`） |
| `state` | **0644** | 读 = 当前状态；**写 = 开/关**（`:40` / `:51`、`:91`） |

```bash
# 找到它
ls /sys/devices/platform/*/name
# 开
echo 1 > /sys/devices/platform/<你的节点>/state
# 关
echo 0 > /sys/devices/platform/<你的节点>/state
```

`state_store`（`:51`）解析布尔值后调
`regulator_bulk_enable`（`:74`）/ `regulator_bulk_disable`（`:77`）——
所以它一次操作的是**一组**，不是一路。

**写 `0` 不等于"电关了"**：走的是 `regulator_bulk_disable`，
语义是"这一组不再需要它"。别的消费者还在用 ⇒ 电还在（03 篇 §A5.3）。

### §5.2 virtual consumer：能调压，但有一个反直觉的用法

`CONFIG_REGULATOR_VIRTUAL_CONSUMER`（`Kconfig:40`），
实现 `drivers/regulator/virtual.c`（374 行），
`compatible = "regulator-virtual-consumer"`（`:287`）。

源码自己承认是开发工具：probe 里有 `static bool warned`（`:297`）
+ 首次 probe 打警告（`:300`）。

五个 **0664** 属性（`virtual.c:266-270`）：

| 文件 | 对应 API |
|---|---|
| `min_microvolts` / `max_microvolts` | `regulator_set_voltage` |
| `min_microamps` / `max_microamps` | `regulator_set_current_limit` |
| `mode` | `regulator_set_mode` |

**写一个不够，必须两个都写**，判据在
`update_voltage_constraints`（`virtual.c:29`）：

```c
if (data->min_uV && data->max_uV
    && data->min_uV <= data->max_uV) {              [:34-35]
	regulator_set_voltage(data->regulator,
			      data->min_uV, data->max_uV);  [:38-39]
}
```

⇒ **`min_uV` 或 `max_uV` 只要有一个是 0，`set_voltage` 根本不被调用**。
而且**不报错、不打日志** —— 你 `echo` 完什么都没发生。

正确顺序：

```bash
cd /sys/devices/platform/<virtual-consumer 节点>
echo 1100000 > min_microvolts
echo 1300000 > max_microvolts      # 这一步才真正触发 set_voltage
cat /sys/class/regulator/regulator.N/microvolts   # 回读实际值
```

**更反直觉的一条：它会顺手替你 enable / disable。**
同一个函数里还有两段：

| 条件 | 动作 | 行号 |
|---|---|---|
| `min_uV && max_uV && !enabled` | 自动 `regulator_enable` | `virtual.c:47-55` |
| `!(min_uV && max_uV) && enabled` | 自动 `regulator_disable` | `virtual.c:57-65` |

⇒ **`echo 0 > min_microvolts` 会把这一路电关掉**（如果是它开的）。
这是"清零 = 关电"，不是"清零 = 取消我的电压请求"。

电流那一路的判据**不对称**（`update_current_limit_constraints`，`:68`）：
设置只看 `max_uA`（`:73`），自动 enable 也只看 `max_uA`（`:87`），
但自动 disable 要求 `min_uA && max_uA` 都为 0（`:97`）。
⇒ **只写了 `max_microamps` 能把电打开，却要清掉两个才能关掉**。

调试价值：想验证"这个 PMIC 到底能不能输出 1.05V"，
不用写驱动 —— 在 DTS 里加一个 virtual consumer 节点指向那一路，
写 min/max 然后回读 `microvolts`（§3.3）。
这也是 §1.2 说的"没有故障注入"的替代手段：**请求一个硬件给不出的电压，
看核心怎么报错**。

---

## §6 debugfs：`/sys/kernel/debug/regulator/`

需要 `CONFIG_DEBUG_FS`。根目录建在 `core.c:6194`。

### §6.1 每一路 + 每一个消费者

```
/sys/kernel/debug/regulator/
├── <regulator名>/                       ← rdev_init_debugfs(), core.c:5264
│   ├── use_count                        [:5281]
│   ├── open_count                       [:5283]  sysfs 没有的那个
│   ├── bypass_count                     [:5285]
│   └── <消费者设备名>-<supply名>/        ← core.c:1920
│       ├── uA_load                      [:1928]
│       ├── min_uV                       [:1930]  这个消费者请求的区间
│       ├── max_uV                       [:1932]
│       └── constraint_flags             [:1934]
├── regulator_summary                    [:6202]
└── supply_map                           [:6199]
```

**`open_count` 只在这里能看到。** sysfs 的 `num_users` 属性
读的是 `use_count`（`core.c:857-862`，§3.3）—— 两个数意思完全不同：

| 计数 | 含义 | 谁加的 |
|---|---|---|
| `open_count` | **有多少个消费者 `get` 了这一路** | `regulator_get` |
| `use_count` | **有多少次 `enable` 还没配对 `disable`** | `regulator_enable` |
| `bypass_count` | 有多少个消费者要求 bypass | `regulator_allow_bypass` |

判"这一路会不会被 30 秒清理关掉"看的是 **`use_count`**（§2.3）；
判"rmmod 会不会出事"看的是 **`open_count`**（03 篇 §A8）。

子目录名是 `"<消费者设备名>-<supply名>"`（`core.c:1881-1882` 的
`snprintf(buf, REG_STR_SIZE, "%s-%s", dev->kobj.name, supply_name)`）——
例如 `fe320000.mmc-vmmc`。**这就是"谁在用这一路"的直接答案**，
不用去猜。

`constraint_flags` 是唯一一个自定义 fops 的文件
（`constraint_flags_read_file`，`core.c:1818`），输出七行
（格式来自 `:1834-1848`）：

```
always_on: 1
boot_on: 1
apply_uV: 0
ramp_disable: 0
soft_start: 0
pull_down: 0
over_current_protection: 0
```

**这是核对"DTS 里那些布尔属性到底进没进核心"的最直接手段** ——
比读 sysfs 的 `min/max_microvolts` 更明确，因为布尔属性在 sysfs 里
**根本没有对应文件**（06 篇 §1.1）。

一个陷阱：`if (!c) return 0`（`:1827-1828`）—— 约束为 NULL 时**返回空**。
但 06 篇 §8.2 已经证明对任何注册成功的 rdev，`constraints` 恒非 NULL，
⇒ **这个分支和 sysfs 那句 `constraint not defined` 一样是不可达的**。

### §6.2 `regulator_summary`：一屏看全树

```bash
cat /sys/kernel/debug/regulator/regulator_summary
```

表头（`core.c:6173-6174` 的两行 `seq_puts`）：

```
 regulator                      use open bypass  opmode voltage current     min     max
---------------------------------------------------------------------------------------
```

**它是一棵树，用缩进表达供电拓扑**：
`regulator_summary_show_subtree`（`core.c:5984`）每递归一层
缩进 3 格（`:5997-5999` 的 `level * 3 + 1`），
子节点通过 `class_for_each_device` + `regulator_summary_show_children`
（`:6054-6055`）找出来。

每一路自己的行（格式来自 `:5997`–`:6018`）：

| 列 | 来源 |
|---|---|
| `use` | `rdev->use_count` |
| `open` | `rdev->open_count` |
| `bypass` | `rdev->bypass_count` |
| `opmode` | `_regulator_get_mode_unlocked`（`:5996`）→ `regulator_opmode_to_str` |
| `voltage` | `regulator_get_voltage_rdev`（`:6003`）—— **当场读硬件** |
| `current` | `_regulator_get_current_limit_unlocked`（`:6005`） |
| `min` / `max` | **约束**（`c->min_uV` / `c->max_uV`，`:6011-6012`） |

每一路下面**还会列出它的每个消费者**（`:6023`–`:6048`），
消费者行的 `min`/`max` 是**这个消费者请求的区间**
（`consumer->voltage[PM_SUSPEND_ON]`，`:6040-6041`）——
**这就是 §2.2 那条 `Restricting voltage` 的答案所在：
把所有消费者行的区间摆在一起，一眼能看出是谁把范围压窄了。**

三条读法上的要点：

1. 消费者名字取 `supply_name`，没有就用设备名，都没有就是
   `"deviceless"`（`:6030-6031`）。
2. `uA_load` 后面那个 **`*`** 号（`:6038-6039`）意思是
   "**声明了负载但没有 enable**" —— 一个明确的 bug 信号。
3. **regulator 自己作为消费者的那些条目被跳过了**
   （`:6024-6025` 的 `consumer->dev->class == &regulator_class`），
   因为上下游关系已经用缩进表达了，不重复列。

**一个重要的副作用**：读这个文件会锁住**整棵树**
（`regulator_summary_lock`，`:6119`，ww_mutex 全量加锁，`:6099`）
并且**逐路读硬件电压**。
⇒ 一颗 12 路 I2C PMIC 上，`cat` 一次是几十次 I2C 事务 +
全局锁持有一段时间。**不要放在监控脚本里高频轮询。**

### §6.3 `supply_map`

```bash
cat /sys/kernel/debug/regulator/supply_map
```

格式 `"<regulator名> -> <设备名>.<supply名>"`（`core.c:5953-5955`）。

**arm64 DT 系统上这个文件通常是空的** ——
它遍历的 `regulator_map_list` 只由 `set_consumer_device_supply`
（`core.c:1749` 那个函数）填充，而后者只在**平台数据**路径
（`init_data->consumer_supplies`，`core.c:5703-5707`）被调用。
DT 路径用的是 phandle，不进这张表（06 篇 §2）。

⇒ 看到它是空的**不代表出问题了**。

---

## §7 错误码分诊

**同一个码有几十个来源**，所以"返回 `-EINVAL`"这句话本身没有信息量。
普查（`grep -o '\-E[A-Z]*' drivers/regulator/core.c | sort | uniq -c`）：

| 码 | `core.c` 里的出现次数 |
|---|---|
| `-EINVAL` | 56 |
| `-EPROBE_DEFER` | 18 |
| `-EPERM` | 11 |
| `-ENOMEM` | 11 |
| `-EOPNOTSUPP` | 9 |
| `-ENODEV` | 7 |
| `-EDEADLK` / `-EDEADLOCK` | 6 / 2 |
| `-ETIMEDOUT` / `-EEXIST` / `-EBUSY` | 2 / 2 / 2 |
| `-ERANGE` / `-ENOTSUPP` / `-ENOTRECOVERABLE` / `-EIO` / `-EALREADY` | 各 1 |

### §7.1 `-EPERM`：只有一个意思

11 处，**按语义分四组**：

| 语义 | 位置 | 判据 |
|---|---|---|
| **`valid_ops_mask` 没授权** | `core.c:501`（电压）、`:566`（电流）、`:600`（模式）、`:2926`（enable） | `regulator_ops_is_valid` 返回假（03 篇 §B2） |
| **suspend 子节点不可变** | `:4217`（开关）、`:4264`（电压） | `rstate->changeable` 为假 / `rstate->min_uV == rstate->max_uV`，日志 `The suspend voltage can't be changed!`（`:4263`）。DTS 侧见 06 篇 §5.2 |
| **这一路被独占了** | `:2245` | 别人先用 `regulator_get_exclusive` 拿走了（`rdev->exclusive`，`:2244`） |
| **耦合** | `:4163`（`Not all coupled regulators registered`）、`:5347` / `:5481`（`Voltage balancing for multiple regulator couples is unimplemented`）、`:5460`（`of_check_coupling_data` 失败） | 03 篇 §B4、06 篇 §7.2 |

**前两组占了 6 处，全部源自"DTS 没给这条权限"**（06 篇 §1.2、§5.2）——
所以**看到 `-EPERM` 先去查 DTS，不要查驱动**。
`ops` 表里有函数但 `valid_ops_mask` 没置位 ⇒ 一律 `-EPERM`。

### §7.2 `-EINVAL`：按函数分组

56 处分布在 40 多个函数里。按**消费者会碰到的**分组：

| 你调的 API | 出错的函数 | 位置 | 最可能的原因 |
|---|---|---|---|
| `regulator_set_voltage` | `regulator_check_voltage` | `:512` | 请求区间和**约束**区间无交集 |
| 同上 | `regulator_check_consumers` | `:552` | 和**别的消费者**的区间无交集 → §2.2 |
| 同上 | `_regulator_do_set_voltage` | `:3684`、`:3688` | 驱动的 `ops` 组合不完整（既没 `set_voltage` 也没 `set_voltage_sel`） |
| 同上 | `regulator_set_voltage_unlocked` | `:3794` | 硬件档位里没有落在区间内的值 |
| 同上 | `_regulator_call_set_voltage` / `_sel` | `:3510` / `:3534` | 驱动回调自己拒绝了 |
| `regulator_set_current_limit` | `regulator_check_current_limit` | `:566`、`:577` | 约束里 min/max 电流没配或区间不符 |
| `regulator_set_mode` | `regulator_mode_constrain` | `:595`、`:600`、`:613` | 请求的模式不在 `valid_modes_mask` 里（06 篇 §1.3） |
| `regulator_get` | `_regulator_get` | `:2195`、`:2200` | 参数为 NULL / id 为 NULL |
| `regulator_enable` | `_regulator_do_enable` | `:2782` | 驱动既没 `ops->enable` 也没 `ena_pin` |
| `regulator_disable` | `_regulator_disable` | `:3035` | **不配对的 disable**（`use_count` 已经是 0） |
| `regulator_list_voltage` | `_regulator_list_voltage` | `:3251`、`:3263` | 选择子越界，或驱动没实现 `list_voltage` |
| `regulator_set_load` | `drms_uA_update` | `:1036` | 有 `CHANGE_DRMS` 授权但驱动既没 `set_load` 也没 `set_mode` |
| `regulator_sync_voltage` | 同名函数 | `:4423`、`:4429` | 之前没有成功的 `set_voltage`，没有可同步的值 |
| `regulator_register`（提供者） | 同名函数 | `:5522`、`:5526`、`:5533`、`:5539`、`:5552`、`:5557` | `desc` 字段缺失 / `config` 不合法（03 篇 §A1） |
| — | `set_machine_constraints` | `:1505` 及 `:1579`/`:1593`/`:1607`/`:1621` | 约束自相矛盾（03 篇 §A3） |

判断技巧：**`-EINVAL` 来自约束检查还是来自驱动回调**，
用 §4.3 的 tracepoint 分：
`trace_regulator_set_voltage` 打了 ⇒ 已经过了约束检查（`:3641` 在 `:512` 之后），
错在驱动侧；一条 trace 都没有 ⇒ 卡在约束检查。

### §7.3 其余几个码

| 码 | 语义 | 典型位置 |
|---|---|---|
| `-EPROBE_DEFER` | 上游 regulator 还没注册 | `core.c:2095`、`:2102`（`regulator_resolve_supply`）；`:2227` 的 dummy 前置检查 |
| `-ENODEV` | `_get_optional` / `_get_exclusive` 找不到 | 05 篇 §1.3 那张表 |
| `-EOPNOTSUPP` | 驱动没实现这个能力 | 9 处，`regulator_get_regmap` `:3352`、`regulator_get_hardware_vsel_register` `:3378`、`regulator_list_hardware_vsel` `:3405` 等 |
| `-ENOTSUPP` | **和上面那个不是同一个码**（524 vs 95） | 只有 1 处：`generic_coupler_attach` `:5487` |
| `-EDEADLK` / `-EDEADLOCK` | ww_mutex 检测到锁循环，**调用者要重试** | `:393`–`:424` 的 `regulator_lock_dependent`（03 篇 §B1） |
| `-EALREADY` | **不是返回码** —— 只出现在 `WARN_ON_ONCE(err != -EALREADY)`（`core.c:344`）里，是 ww_mutex 「这把锁我已经持有」的合法返回 | `core.c:344` |

**`-ENOTSUPP`（524）vs `-EOPNOTSUPP`（95）混用**是一处真实的不一致：
前者是内部码、`errno` 里没有、不该返回给用户态。
本子系统只有 1 处用了它 —— 详见 99 §6。

---

## §8 一页速查

```bash
# ① 有没有拿到 dummy（最高优先级）
dmesg | grep 'using dummy regulator'

# ② 这一路叫什么、编号是多少
grep -H . /sys/class/regulator/*/name

# ③ DTS 约束进没进来（全 0 = 没进来）
R=/sys/class/regulator/regulator.7
cat $R/min_microvolts $R/max_microvolts
cat /sys/kernel/debug/regulator/*/`basename $R`  2>/dev/null

# ④ 核心认为这一路对应哪个 DT 节点
ls -l $R/of_node

# ⑤ 布尔约束（always_on / boot_on / apply_uV …）
cat /sys/kernel/debug/regulator/<名字>/*/constraint_flags

# ⑥ 全树拓扑 + 每个消费者的请求区间（会读硬件、会全局加锁）
cat /sys/kernel/debug/regulator/regulator_summary

# ⑦ 三个计数
cat /sys/kernel/debug/regulator/<名字>/{use_count,open_count,bypass_count}

# ⑧ enable 延时拆两段
cd /sys/kernel/tracing
echo 1 > events/regulator/enable  # 一次开本子系统 11 个 event 全部
echo 1 > tracing_on; cat trace

# ⑨ 调压请求 vs 实际
echo 1 > events/regulator/regulator_set_voltage/enable
echo 1 > events/regulator/regulator_set_voltage_complete/enable

# ⑩ DT 解析细节（必须 cmdline）
#   dyndbg="file drivers/regulator/of_regulator.c +p"
#   或 CONFIG_REGULATOR_DEBUG=y 整目录开
```

| 我想知道 | 看哪里 | 章节 |
|---|---|---|
| 这一路真的通电了吗 | `dmesg` 有没有 dummy；`$R/state` | §2.1、§3.3 |
| 现在电压是多少 | `$R/microvolts`（读硬件） | §3.3 |
| 约束是多少 | `$R/min_microvolts`（读约束） | §3.2 |
| 谁在用它 | `regulator_summary` 缩进 / debugfs 子目录名 / sysfs 软链 | §6.2、§6.1 |
| 谁把我的电压压窄了 | `regulator_summary` 的消费者行 | §6.2 |
| 有多少人 get / enable 了 | debugfs `open_count` / `use_count` | §6.1 |
| 为什么 30 秒后被关了 | `use_count` == 0 | §2.3 |
| enable 慢在哪 | 三段 tracepoint | §4.2 |
| DTS 布尔属性进来了吗 | `constraint_flags` | §6.1 |
| 手动开关一路电 | userspace-consumer 的 `state` | §5.1 |
| 手动调一路电压 | virtual consumer（min+max 都要写） | §5.2 |

---

## §9 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 本篇提到的每个 API 的签名和错误码语义 | `01-api.md` §7 |
| `use_count` / `open_count` / `bypass_count` 在结构里的位置 | `02-datastructures.md` §3 |
| 为什么 `disable` 不关电、30 秒清理是什么 | `03-internals.md` §A5 / §A7 |
| `valid_ops_mask` 授权机制（`-EPERM` 的总根源） | `03-internals.md` §B2 |
| virtual / userspace consumer 的完整实现 | `04-extensions.md` §8 / §9 |
| 消费者和提供者的检查清单 | `05-usage.md` §1.7 / §2.3 |
| DTS 写错了怎么定位、错误消息全表 | `06-devicetree.md` §8 / §9 |
| 「`num_users` 为什么不是用户数」这类直觉纠偏 | `08-feynman.md` |
| 本篇每条论断的出处，以及"示例输出未在真机验证"的声明 | `99-evidence.md` §5.1 |
