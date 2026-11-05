---
title: 调试方法
---

# watchdog 07：调试与观测

## §0 分诊表：出问题先看哪个出口

**watchdog 的可观测性在本项目做过的子系统里属于最好的一档**：

| 出口 | watchdog | 参照 |
|---|---|---|
| **sysfs 属性** | **12 个**（需 `CONFIG_WATCHDOG_SYSFS`） | phy 0 个 · regmap 0 个 |
| **tracepoint** | **4 个事件 / 6 个打点** | phy 0 个 · regmap 17 个 |
| **用户态 ioctl** | **11 个，含 `WDIOC_GETBOOTSTATUS`（复位取证）** | **phy / regmap 都没有用户态 ABI** |
| **debugfs** | **0** | regmap 692 行 |
| **dmesg** | **见 §4** | — |
| **故障注入** | **0**（但 DT / sysfs / ioctl 各有一条"准注入"，§6） | — |

### §0.1 按症状分诊

| 症状 | 第一个该看的出口 | 去 |
|---|---|---|
| **机器半夜自己重启了，想知道是不是看门狗干的** | **`cat /sys/class/watchdog/watchdogN/bootstatus` 或 `ioctl(WDIOC_GETBOOTSTATUS)`** | **§2.3 / §5.1** |
| ⚠️ **驱动 probe 成功但 `/dev/watchdog*` 不存在** | **dmesg + `ls /sys/class/watchdog/`** | **§5.2**（延迟注册那个坑） |
| **`state` 显示 `inactive` 但硬件明明在跑** | **正常** ——**`state` 只看 `WDOG_ACTIVE`** | **§2.2**（本篇头号误读） |
| **超时值不是 DTS 里写的那个** | **`cat timeout` + 查模块参数** | 06 §4 / §5.2 |
| **`open()` 返回 `-EBUSY`** | **`lsof /dev/watchdog*` 或 `fuser`** | §5.3 |
| **`dmesg: watchdog did not stop!`** | **多数是正常的 magic close** | §4.3 |
| **想知道谁在喂狗、喂多快** | **tracepoint `watchdog_ping` / `watchdog_start`** | §3 |
| **pretimeout 没反应** | **`cat pretimeout_governor`** | §2.4 |
| **`nowayout` 改不掉** | **设计如此** ——**一旦置位不能清** | §2.5 |

---

## §1 编译期开关

### §1.1 五个真正影响行为的 Kconfig（与 phy/regmap 不同，这些是"策略"开关）

| 符号 | 类型 | 默认 | 影响什么 |
|---|---|---|---|
| **`WATCHDOG_SYSFS`** | **`bool`** | **N** | **12 个 sysfs 属性的总开关** ⇒ **关掉的话 `/sys/class/watchdog/watchdogN/` 下只有通用设备属性**（§2.1） |
| ⚠️ **`WATCHDOG_NOWAYOUT`** | `bool` | **N** | **关掉 `/dev/watchdog` 后能不能停表**（01 §0.7） |
| ⚠️ **`WATCHDOG_HANDLE_BOOT_ENABLED`** | `bool` | **Y** | **要不要接管"启动前就在跑"的看门狗** |
| ⚠️ **`WATCHDOG_OPEN_TIMEOUT`** | **`int`** | **0（无限）** | **内核最多替用户态兜多久**（03 §B2.4） |
| **`WATCHDOG_HRTIMER_PRETIMEOUT`** | `bool` | N | **软 pretimeout**；⚠️ **开了会让所有看门狗的 sysfs 多出 `pretimeout` 属性**（04 §3.4） |
| `WATCHDOG_PRETIMEOUT_GOV` | `bool` | — | **两个 governor 相关属性的前提**（§2.4） |

⚠️ **`WATCHDOG_SYSFS` 默认是 N** ⇒ **发行版内核通常开了，但自己配的内核容易忘。**
**一条命令确认**：`zcat /proc/config.gz | grep '^CONFIG_WATCHDOG_SYSFS'`。

### §1.2 两个可以在运行期覆盖的

| 项 | 编译期 | 运行期覆盖 |
|---|---|---|
| **`open_timeout`** | **`CONFIG_WATCHDOG_OPEN_TIMEOUT`** | **启动参数 `watchdog.open_timeout=<秒>`**（Kconfig help 原文点名） |
| **`stop_on_reboot`** | — | **模块参数 `watchdog.stop_on_reboot=0/1`** ——**三态，初值 `-1` 表示"不干预"**（`drivers/watchdog/watchdog_core.c:46` / `:294-299`） |

**另外 `handle_boot_enabled` 也是模块参数**（`drivers/watchdog/watchdog_dev.c:59`）。

### §1.3 通用调试开关

| 开关 | 对 watchdog 的价值 |
|---|---|
| **`CONFIG_DYNAMIC_DEBUG`** | **watchdog 核心的 `dev_dbg` 很少**（§4.1）——**价值有限** |
| **`CONFIG_FTRACE` / tracepoint** | **4 个事件是本子系统最有用的动态出口**（§3） |
| **`initcall_debug`** | **看 `subsys_initcall_sync` 与驱动 probe 的先后**（排延迟注册的坑，§5.2） |
| **`panic_on_warn` / kdump** | **配合 `panic` governor 拿 vmcore**（04 §2.2） |

---

## §2 `/sys/class/watchdog/`：12 个属性

### §2.1 全清单（`drivers/watchdog/watchdog_dev.c:442-638`）

| 属性 | 权限 | 实现位置 | 读到什么 |
|---|---|---|---|
| **`state`** | RO | `:558-566` | **`active` 或 `inactive`** ——⚠️ **只看 `WDOG_ACTIVE`**（§2.2） |
| **`identity`** | RO | `:549-554` | **`wdd->info->identity`** ——⚠️ **当 C 字符串打印**（§2.6） |
| **`timeout`** | RO | `:513-518` | **`wdd->timeout`（秒）** |
| **`min_timeout`** | RO | `:522-527` | `wdd->min_timeout` |
| **`max_timeout`** | RO | `:531-536` | `wdd->max_timeout` |
| **`pretimeout`** | RO | `:540-545` | **`wdd->pretimeout`** ——**条件可见**（§2.7） |
| **`timeleft`** | RO | `:495-509` | **调 `ops->get_timeleft`** ——**条件可见** |
| **`bootstatus`** | RO | `:486-491` | **`wdd->bootstatus`** ——**复位原因取证**（§2.3） |
| **`status`** | RO | `:471-482` | **`0x%x` 格式的 `WDIOF_*` 位** ——⚠️ **有副作用**（§2.3） |
| ⚠️ **`nowayout`** | **RW** | `:442-469` | **0/1** ——⚠️ **只能设不能清**（§2.5） |
| **`pretimeout_governor`** | **RW** | `:577-598` | **当前 governor 名** ——**条件可见** |
| **`pretimeout_available_governors`** | RO | `:570-574` | **可选 governor 列表** ——**条件可见** |

**组装在 `wdt_attrs[]`（`:618-632`）+ `wdt_group`（`:634-637`）+ `__ATTRIBUTE_GROUPS(wdt)`（`:638`）**，
**挂在 `watchdog_class.dev_groups`（`:988`）上。**

⚠️ **`CONFIG_WATCHDOG_SYSFS=n` 时整组消失**：
**`#else` 分支把 `wdt_groups` 定义成 `NULL`**（`drivers/watchdog/watchdog_dev.c:639-640`）。

### §2.2 ⚠️⚠️`state` 的含义（本篇头号误读）

```c
/* drivers/watchdog/watchdog_dev.c:558-566 */
static ssize_t state_show(struct device *dev, struct device_attribute *attr,
				char *buf)
{
	struct watchdog_device *wdd = dev_get_drvdata(dev);

	if (watchdog_active(wdd))
		return sysfs_emit(buf, "active\n");

	return sysfs_emit(buf, "inactive\n");
}
```

⚠️⚠️ **它只测 `WDOG_ACTIVE`，完全不看 `WDOG_HW_RUNNING`。**

| 实际状态 | `state` 显示 | 对不对 |
|---|---|---|
| **用户态打开了并启动** | `active` | ✔ |
| ⚠️ **bootloader 开的表，内核在代喂，用户态还没接手** | ⚠️ **`inactive`** | ⚠️ **误导** ——**硬件明明在转** |
| ⚠️ **驱动没有 `stop`，用户态已关闭设备，内核在代喂** | ⚠️ **`inactive`** | ⚠️ **同样误导** |
| **谁都没在用** | `inactive` | ✔ |

⇒ **`state` 的准确含义是"用户态有没有在用"，不是"看门狗有没有在跑"。**
**想知道硬件在不在跑，没有直接的 sysfs 出口**（§2.8）——
**只能靠 `timeleft` 是否在变、或 tracepoint 看有没有周期性的 `watchdog_ping`。**

### §2.3 `bootstatus` 与 `status`：一个无副作用，一个有

| | **`bootstatus`** | ⚠️ **`status`** |
|---|---|---|
| 实现 | **`sysfs_emit(buf, "%u\n", wdd->bootstatus)`**（`:491`） | **调 `watchdog_get_status(wdd)`**（`:471-482`） |
| 格式 | **`%u`（十进制）** | **`0x%x`（十六进制）** |
| **有副作用吗** | **没有** ——**纯读字段** | ⚠️ **有！** ——**`WDIOF_KEEPALIVEPING` 位是 `test_and_clear`**（`drivers/watchdog/watchdog_dev.c:348-349`） |
| 用途 | **"上次是不是看门狗复位的"** | **当前状态位** |

⚠️⚠️ **`cat status` 两次会得到不同结果** ——
**第一次可能带 `0x8000`（`WDIOF_KEEPALIVEPING`），第二次一定没有。**
⇒ **`status` 是一个"读即消费"的属性，脚本里连读两次会看到不一致。**
**完整的三个坑见 05 §5.3。**

**`bootstatus` 的取证用法**：
```bash
### 上次是不是看门狗复位的（WDIOF_CARDRESET = 0x0020 = 32）
cat /sys/class/watchdog/watchdog0/bootstatus
### 32 或 (32 | 其他位) ⇒ 是看门狗复位的
```
⚠️ **`bootstatus` 恒为 0 有两种可能**：**① 真的不是看门狗复位；
② 驱动没填这个字段** ——**两者无法从这个属性区分**（05 §5.2）。

### §2.4 两个 governor 属性

```bash
### 有哪些 governor 可选
cat /sys/class/watchdog/watchdog0/pretimeout_available_governors
### 当前用哪个
cat /sys/class/watchdog/watchdog0/pretimeout_governor
### 切换（换行符会被 sysfs_streq 容忍）
echo panic > /sys/class/watchdog/watchdog0/pretimeout_governor
```

**实现都在 `watchdog_pretimeout.c`**（04 §1.6）：
**`:51` / `:66` / `:78`。写入未知名字返回 `-EINVAL`（`:88`）。**

⚠️ **`pretimeout_available_governors` 为空 ⇒ 一个 governor 模块都没加载**
⇒ **pretimeout 事件会被静默丢弃**（04 §1.4）。

### §2.5 ⚠️`nowayout` 是单向的

```c
/* drivers/watchdog/watchdog_dev.c:458-467 */
	ret = kstrtouint(buf, 0, &value);
	if (ret)
		return ret;
	if (value > 1)
		return -EINVAL;
	/* nowayout cannot be disabled once set */
	if (test_bit(WDOG_NO_WAY_OUT, &wdd->status) && !value)
		return -EPERM;
	watchdog_set_nowayout(wdd, value);
	return len;
```

| 操作 | 结果 |
|---|---|
| **`echo 1 > nowayout`** | **成功，从此停不下来** |
| ⚠️ **`echo 0 > nowayout`（已经是 1）** | ⚠️ **`-EPERM`** ——**源码注释就是这句话：*"nowayout cannot be disabled once set"*** |
| **`echo 0 > nowayout`（本来是 0）** | **成功（无变化）** |
| **`echo 2 > nowayout`** | **`-EINVAL`** |

⇒ **这是一条"有意的单向门"** ——**它保证"一旦决定不允许停表，就没有回头路"。**
**要恢复只能重启。**

### §2.6 ⚠️`identity` 把定长数组当 C 字符串打印

```c
/* drivers/watchdog/watchdog_dev.c:549-554 */
	return sysfs_emit(buf, "%s\n", wdd->info->identity);
```

⚠️ **`identity` 是 `__u8 identity[32]`（`include/uapi/linux/watchdog.h`），不保证 NUL 结尾**
（01 §7.2 / 05 §7.6）
⇒ **驱动填了正好 32 个字符时，`%s` 会读出界。**
**树里的驱动都填得短，所以实际上没出问题** ——**但这是一处真实的脆弱点。**
**同样的模式还出现在 `drivers/watchdog/watchdog_core.c:122` 和 `:364`**（03 §A2 / 06 §6）。

### §2.7 四个属性是条件可见的

```c
/* drivers/watchdog/watchdog_dev.c:600-617 */
	if (attr == &dev_attr_timeleft.attr && !wdd->ops->get_timeleft)
		mode = 0;
	else if (attr == &dev_attr_pretimeout.attr && !watchdog_have_pretimeout(wdd))
		mode = 0;
	else if ((attr == &dev_attr_pretimeout_governor.attr ||
		  attr == &dev_attr_pretimeout_available_governors.attr) &&
		 (!watchdog_have_pretimeout(wdd) || !IS_ENABLED(CONFIG_WATCHDOG_PRETIMEOUT_GOV)))
		mode = 0;
```

| 属性 | 消失的条件 |
|---|---|
| **`timeleft`** | **驱动没实现 `ops->get_timeleft`** |
| **`pretimeout`** | **`!watchdog_have_pretimeout(wdd)`** ——⚠️ **开了 `HRTIMER_PRETIMEOUT` 就永远可见**（04 §3.4） |
| **`pretimeout_governor`** | **上面那条 **或** `CONFIG_WATCHDOG_PRETIMEOUT_GOV=n`** |
| **`pretimeout_available_governors`** | 同上 |

⇒ **"某个属性不存在"是有信息量的**：
**`timeleft` 不存在 ⇒ 驱动没有 `get_timeleft` 回调**（不是内核版本问题）。

### §2.8 ⚠️ 看不到的四样东西

| 想看 | 有没有 |
|---|---|
| ⚠️ **`WDOG_HW_RUNNING`（硬件在不在跑）** | **没有直接出口**（§2.2） |
| **`init_count` / 是否有人打开** | **没有** ——**只能 `lsof`/`fuser` 看 `/dev/watchdog*`** |
| **`last_keepalive` / `last_hw_keepalive`** | **没有** ——**只能靠 tracepoint 间接看**（§3） |
| **`open_deadline`（内核还能兜多久）** | **没有** ——**只能看 `CONFIG_WATCHDOG_OPEN_TIMEOUT` + 启动时刻推算** |

**通用设备属性仍然有用**：
```bash
### 这个 watchdogN 对应哪个平台设备 / DT 节点
ls -l /sys/class/watchdog/watchdog0/device
cat /sys/class/watchdog/watchdog0/device/of_node/name
### 设备号（区分 misc 与 cdev，02 §1.0.C）
cat /sys/class/watchdog/watchdog0/dev
```

---

## §3 tracepoint：4 个事件 / 6 个打点

### §3.1 全清单

**`include/trace/events/watchdog.h`（66 行）**：

| 事件 | 定义位置 | 字段 | 打点位置 |
|---|---|---|---|
| `watchdog_start` | `include/trace/events/watchdog.h:30`（`watchdog_template` 类，`:11`） | **`id` `err`** | ⚠️ **两处**：`drivers/watchdog/watchdog_dev.c:167`（代替 ping）+ `:267`（真启动） |
| `watchdog_ping` | `:34` | `id` `err` | `drivers/watchdog/watchdog_dev.c:164` |
| `watchdog_stop` | `:38` | `id` `err` | **两处**：`drivers/watchdog/watchdog_dev.c:306` + `watchdog_core.c:169`（reboot notifier） |
| `watchdog_set_timeout` | **独立 `TRACE_EVENT`**（`:42`） | **`id` `timeout` `err`** | `drivers/watchdog/watchdog_dev.c:379` |

**口径**：**`grep -cE '^(TRACE_EVENT|DEFINE_EVENT)\(' include/trace/events/watchdog.h` ⇒ 4**；
**`DECLARE_EVENT_CLASS` 1 个，不算事件。**

### §3.2 ⚠️⚠️`watchdog_start` 不一定是"启动"

```c
/* drivers/watchdog/watchdog_dev.c:160-170 附近 —— __watchdog_ping() 里 */
	if (wdd->ops->ping) {
		err = wdd->ops->ping(wdd);
		trace_watchdog_ping(wdd, err);
	} else {
		err = wdd->ops->start(wdd);
		trace_watchdog_start(wdd, err);		/* 这是一次喂狗！ */
	}
```

⇒ **驱动没实现 `ops->ping` 时，每一次喂狗都打 `watchdog_start`。**
⇒ **trace 里看到一串周期性的 `watchdog_start`，那是心跳，不是反复启动。**

**怎么区分**（`drivers/watchdog/watchdog_dev.c:167` vs `:267`）：
**`:267` 那处只在 `watchdog_start()` 里、一次开表只打一次；
`:167` 那处会周期性出现。**
⇒ **按频率区分**：**周期性的 = 喂狗；孤立的一次 = 真启动。**

### §3.3 怎么用

```bash
cd /sys/kernel/debug/tracing

### 全开 watchdog 事件
echo 1 > events/watchdog/enable
cat trace_pipe

### 只看喂狗（确认"谁在喂、多快"）
echo 1 > events/watchdog/watchdog_ping/enable
echo 1 > events/watchdog/watchdog_start/enable

### 只看超时变更（排查"谁改了超时"）
echo 1 > events/watchdog/watchdog_set_timeout/enable

### 只看某个看门狗（字段名是 id）
echo 'id == 0' > events/watchdog/watchdog_ping/filter
```

**本篇不给 `trace_pipe` 的输出示例**（未上机运行，99 §5.1）。
**字段可以从 `TRACE_EVENT` 的 `TP_printk` 推**（`include/trace/events/watchdog.h`）。

⚠️ **`err` 字段是每个事件都有的** ⇒ **`err != 0` 的喂狗事件是硬件层面的失败，值得注意。**

### §3.4 tracepoint 能回答的三个问题（sysfs 回答不了）

| 问题 | 怎么答 |
|---|---|
| **现在到底是用户态在喂还是内核在喂** | **看 `watchdog_ping` 的调用栈**（`echo 1 > options/stacktrace`）——**内核代喂的栈里有 `watchdog_ping_work`**（03 §B2.1） |
| **喂狗间隔是多少** | **两次 `watchdog_ping` 的时间戳差** ——**可以验证 `hw_heartbeat_ms / 2` 那个算法**（03 §B2.3） |
| **超时被谁改过** | **`watchdog_set_timeout` 事件 + 栈** |

---

## §4 日志可见性等级

### §4.1 核心的打印分布

| 文件 | 打印数 | 说明 |
|---|---|---|
| `drivers/watchdog/watchdog_core.c` | **9** | **全部是 `pr_*`（不带设备前缀）** |
| `drivers/watchdog/watchdog_dev.c` | **9** | 同上 |
| `drivers/watchdog/pretimeout_noop.c` | **1 条 `pr_alert`** | 04 §2.1 |
| `drivers/watchdog/pretimeout_panic.c` | **0 条打印**（只有 `panic()` 自己的消息） | 04 §2.2 |

**合计 19 条**，重跑：
`grep -n 'pr_crit\|pr_err\|pr_warn\|pr_info\|pr_alert' drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c drivers/watchdog/pretimeout_noop.c | wc -l`

⚠️ **口径**：**`pr_debug` / `dev_dbg` 在这三个文件里是 0**
（重跑 `grep -c 'pr_debug\|dev_dbg' drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c`）
⇒ **watchdog 没有任何"需要 dyndbg 才能看到"的隐藏日志** ——**19 条全是默认可见的。**

⚠️ **一个重要的形式特征**：**watchdog 核心用的是 `pr_*` 而不是 `dev_*`**
⇒ **消息里没有自动的设备前缀**，**改成手工在格式串里写 `watchdog%d:`**。
⇒ **好处是消息统一；坏处是 `dmesg` 里无法用设备名过滤。**

### §4.2 逐条消息 → 含义（全部逐字取自源码）

| 消息 | 出处 | 级别 | 含义 | 是错误吗 |
|---|---|---|---|---|
| ⚠️`watchdog%d: watchdog did not stop!` | `drivers/watchdog/watchdog_dev.c:946` | **`pr_crit`** | **关闭设备时没停下来** | ⚠️ **多数不是** ——**正常的 magic close 流程**（§4.3） |
| `watchdog%d: nowayout prevents watchdog being stopped!` | `drivers/watchdog/watchdog_dev.c:298-299` | **`pr_info`** | **`nowayout` 拦住了停表** | **不是** ——**设计如此** |
| `watchdog%d: stop_on_reboot not supported` | `drivers/watchdog/watchdog_core.c:303` | `pr_warn` | **要求重启时停表但驱动没 `stop`** | **是配置问题** |
| `watchdog%d: Cannot register reboot notifier (%d)` | `drivers/watchdog/watchdog_core.c:309-310` | **`pr_err`** | **注册会失败**（致命，03 §A3.4） | **是** |
| `watchdog%d: Cannot register restart handler (%d)` | `watchdog_core.c:323-324` | `pr_warn` | **注册继续**（非致命） | 降级 |
| `watchdog%d: Cannot register pm handler (%d)` | `watchdog_core.c:332-333` | `pr_warn` | 同上 | 降级 |
| `%s: failed to register watchdog device (err = %d)` | `watchdog_core.c:365-366` | `pr_err` | **注册失败** | ⚠️ **延迟注册路径走不到它**（§5.2） |
| `%s: DT supplied timeout (%u) out of range` | `watchdog_core.c:146` | `pr_err` | **`timeout-sec` 越界或为 0** | **是**（06 §6） |
| `%s: driver supplied timeout (%u) out of range` | `watchdog_core.c:134-135` | `pr_err` | **模块参数越界** | **是** |
| `%s: falling back to default timeout (%u)` | `watchdog_core.c:151-152` | `pr_warn` | **上两条之后的兜底** | 提示 |
| `%s: cannot register miscdev on minor=%d (err=%d).` | `drivers/watchdog/watchdog_dev.c:1042` | `pr_err` | **`/dev/watchdog` 的 misc 号被占** | ⚠️ **后面会自动重试用别的 id**（03 §A3.2） |
| `watchdog%d: pretimeout event` | `drivers/watchdog/pretimeout_noop.c:20` | **`pr_alert`** | **`noop` governor 报了 pretimeout** | **是严重信号** ——**再不喂就复位了** |
| `watchdog pretimeout event`（panic 消息） | `drivers/watchdog/pretimeout_panic.c:20` | **`panic()`** | **`panic` governor 触发** | **是** ——⚠️ **消息里没有 `id`**（04 §2.2） |
| ⚠️`%s: a legacy watchdog module is probably present.` | `drivers/watchdog/watchdog_dev.c:1045` | `pr_err` | **紧跟在 `cannot register miscdev` 之后的诊断提示** | **是** ——**它直接告诉你原因：有个老式看门狗驱动占了 misc 号**（§5.2） |
| `watchdog%d unable to add device %d:%d` | `drivers/watchdog/watchdog_dev.c:1060` | `pr_err` | **`cdev_device_add()` 失败** | **是** |
| ⚠️`watchdog%d running and kernel based pre-userspace handler disabled` | `drivers/watchdog/watchdog_dev.c:1085` | `pr_info` | **看门狗启动时已在跑，但 `WATCHDOG_HANDLE_BOOT_ENABLED=n` ⇒ 内核不代喂** | ⚠️ **不是错误，但是一个高危提示** ——**用户态起得慢就会重启**（01 §0.7） |
| `Invalid min and max timeout values, resetting to 0!` | `drivers/watchdog/watchdog_core.c:95` | `pr_info` | **驱动填的 `min_timeout > max_timeout`** | **是驱动的 bug** |
| `Failed to create watchdog kworker` | `drivers/watchdog/watchdog_dev.c:1213` | `pr_err` | **框架初始化失败** | **是**（几乎不会发生） |
| `couldn't register class` | `drivers/watchdog/watchdog_dev.c:1220` | `pr_err` | 同上 | 是 |
| `watchdog: unable to allocate char dev region` | `drivers/watchdog/watchdog_dev.c:1226` | `pr_err` | 同上 | 是 |

### §4.3 ⚠️⚠️`pr_crit "watchdog did not stop!"` 不一定是故障

**完整判定表在 03 §A7。这里只给"看到它怎么办"**：

| 同时看到 | 结论 |
|---|---|
| **只有这一条** | **正常的 magic close** ——**守护进程没写 `'V'` 就退出了，机器稍后会重启**（这正是想要的） |
| **加上 `nowayout prevents watchdog being stopped!`** | **`nowayout` 生效中** ——**同样是设计行为** |
| **完全没有守护进程退出的迹象** | **才值得怀疑**（例如某个进程意外打开又关闭了 `/dev/watchdog`） |

⇒ **它用 `pr_crit`（"critical"）来描述一个正常路径，是本子系统日志设计上的一处债**（03 §C4 第 3 条）。

### §4.4 三个"没有日志"的地方（最需要警惕）

| 情形 | 为什么没日志 |
|---|---|
| ⚠️ **延迟注册失败** | **返回值被丢弃，而"注册失败"的 `pr_err` 在外层函数里**（03 §A2 / §5.2） |
| ⚠️ **DT alias 指定的 id 被占用，静默回退** | **`drivers/watchdog/watchdog_core.c:267` 只是一个 `if`**（06 §3.3） |
| ⚠️ **pretimeout 事件但 `wdd->gov` 是 NULL** | **`watchdog_notify_pretimeout()` 静默 return**（04 §1.4） |

---

## §5 三个高频排查场景

### §5.1 场景一：机器自己重启了，是不是看门狗？

```bash
### ① 最直接的证据
for w in /sys/class/watchdog/watchdog*; do
	echo "$w: bootstatus=$(cat $w/bootstatus) identity=$(cat $w/identity)"
done
### WDIOF_CARDRESET = 0x0020 = 32 ⇒ bootstatus 含 32 就是看门狗复位的

### ② 用 ioctl 拿同一个值（与 sysfs 等价，实现都是读 wdd->bootstatus）
### 见 05 §5.2 的代码

### ③ 如果 bootstatus 是 0，看驱动填不填这个字段
grep -n 'bootstatus' drivers/watchdog/<驱动>.c
```

⚠️ **`bootstatus == 0` 的两种可能无法区分**：
**① 真的不是看门狗复位；② 驱动没实现复位原因的读取。**
⇒ **必须查驱动源码才能定论**（§5.1 第 ③ 步）。

### §5.2 ⚠️ 场景二：probe 成功但 `/dev/watchdog*` 不存在

**这是 watchdog 最阴的一个坑**（03 §A2 / §C3 第 1 类）。

```bash
### ① 设备类里有没有
ls /sys/class/watchdog/
### 空的 ⇒ 一个都没注册成功

### ② dmesg 里找 __watchdog_register_device() 内部的抱怨
dmesg | grep -iE 'watchdog.*(cannot register|out of range|not supported)'

### ③ 确认驱动确实调了注册
grep -n 'watchdog_register_device\|devm_watchdog_register_device' drivers/watchdog/<驱动>.c

### ④ 确认框架的 initcall 与驱动 probe 的先后（判断走没走延迟注册）
### 启动参数加 initcall_debug，然后：
dmesg | grep -E 'watchdog_init|<驱动名>'
```

⚠️ **判据**：**驱动 probe 早于 `watchdog_init()`（`subsys_initcall_sync`）⇒ 走了延迟注册
⇒ `watchdog_register_device()` 一定返回 0，真正的失败只能靠 dmesg 里那几条内部消息看出来。**

**`/dev/watchdog` 缺失但 `/dev/watchdog1` 存在的情形**：
**看 `dmesg | grep 'cannot register miscdev'`** ——**那说明 misc 号被占，框架退让了**（03 §A3.2）。

### §5.3 场景三：`open()` 返回 `-EBUSY`

**看门狗是独占设备**（01 §8.3 / 03 §A5）。

```bash
### 谁打开着
lsof /dev/watchdog /dev/watchdog0 2>/dev/null
fuser -v /dev/watchdog0 2>&1

### 注意 /dev/watchdog 与 /dev/watchdog0 是同一个设备（id == 0 时）
### 两个都打开，第二个必然 -EBUSY
ls -l /dev/watchdog /dev/watchdog0
### 主设备号不同（10 vs 动态），但指向同一个 watchdog_core_data
```

⚠️ **`-EBUSY` 还有第二个来源**：
**`open()` 里 `try_module_get(wdd->ops->owner)` 失败也返回 `-EBUSY`**
（`drivers/watchdog/watchdog_dev.c:867-870`）⇒ **驱动模块正在卸载。**
⇒ **两个来源用同一个码** ——**`lsof` 查不到打开者时，怀疑第二种。**

---

## §6 故障注入：没有专用设施，但有三条"准注入"

**结论先说**：**watchdog 不提供任何故障注入设施。**

| 检查项 | 结果 | 重跑 |
|---|---|---|
| `fault_inject` / `should_fail` | **0** | `grep -rniE 'fault_inject\|should_fail' drivers/watchdog/watchdog_*.c` |
| debugfs | **0** | `grep -rl 'debugfs' drivers/watchdog/watchdog_*.c` |

**但 watchdog 天生就是"可以安全地让它真的触发"的子系统**，**三条准注入**：

| # | 手段 | 做法 | 效果 |
|---|---|---|---|
| **1** | **停止喂狗** | **打开 `/dev/watchdog` 然后不写、不关（或 kill -9 守护进程且驱动支持 magic close）** | **`timeout` 秒后真的复位** ——**最真实的端到端测试** |
| **2** | **改超时到最小值** | **`ioctl(WDIOC_SETTIMEOUT)` 设成 `min_timeout`** | **缩短验证周期** |
| **3** | **切 governor 到 `panic`** | **`echo panic > pretimeout_governor`** | **pretimeout 时拿 vmcore 而不是硬复位**（04 §2.2） |

⚠️ **第 1 条要在能重启的机器上做** ——**它会真的重启。**
**配合 `nowayout=0` + magic close 可以随时安全退出**（05 §4.2）。

⚠️ **想"注入一个假的 pretimeout"没有干净手段**：
**`watchdog_notify_pretimeout()` 是导出符号，但没有 sysfs/debugfs 入口**
⇒ **只能写一个测试模块调它，或用 kprobe**（本文档未实测，99 §5.1）。

---

## §7 用户态工具

| 工具 | 用途 | 注意 |
|---|---|---|
| **`watchdog` 守护进程** | **官方 Kconfig help 给了地址**：`https://ibiblio.org/pub/Linux/system/daemons/watchdog/` | **不在内核树里** |
| **`systemd` 的 `RuntimeWatchdogSec=`** | **现代发行版最常见的喂狗者** | **它会占用 `/dev/watchdog`** ⇒ **手工测试前要先停它**（§5.3） |
| **`samples/watchdog/watchdog-simple.c`** | **in-tree 的 25 行样例，可编译** | **05 §4.1 全文引用** |
| **`wdctl`（util-linux）** | **一条命令看全部能力和状态** | **它用的就是 `WDIOC_GETSUPPORT` + `WDIOC_GETSTATUS`**（本文档未实测该工具的具体输出） |
| **`cat` / `echo` on sysfs** | §2 | ⚠️ **`cat status` 有副作用**（§2.3） |
| **裸 ftrace** | §3 | — |
| **`lsof` / `fuser`** | §5.3 | **唯一能看出"谁打开着"的办法** |

⚠️ **一条最容易忘的操作纪律**：
**在跑着 systemd watchdog 的机器上手工 `cat /dev/watchdog` 会拿到 `-EBUSY`** ——
**要先 `systemctl` 关掉相关设置或改 `/etc/systemd/system.conf`。**

---

## §8 一页速查

```bash
### ① 有哪些看门狗、对应哪个设备
ls /sys/class/watchdog/
for w in /sys/class/watchdog/watchdog*; do
	echo "== $w"
	echo "  identity   : $(cat $w/identity)"
	echo "  state      : $(cat $w/state)        # 只表示用户态在不在用！"
	echo "  timeout    : $(cat $w/timeout)"
	echo "  bootstatus : $(cat $w/bootstatus)   # 32(0x20)=看门狗复位过"
	echo "  nowayout   : $(cat $w/nowayout)     # 1 之后不能改回 0"
	readlink -f $w/device
done

### ② 上次是不是看门狗复位的（最常用）
cat /sys/class/watchdog/watchdog0/bootstatus

### ③ 还剩多久（属性存在说明驱动实现了 get_timeleft）
cat /sys/class/watchdog/watchdog0/timeleft

### ④ pretimeout governor
cat /sys/class/watchdog/watchdog0/pretimeout_available_governors
cat /sys/class/watchdog/watchdog0/pretimeout_governor
echo panic > /sys/class/watchdog/watchdog0/pretimeout_governor

### ⑤ tracepoint（看谁在喂、多快）
cd /sys/kernel/debug/tracing
echo 1 > events/watchdog/enable
echo 1 > options/stacktrace        # 区分用户态喂 vs 内核代喂
cat trace_pipe

### ⑥ 谁打开着（排 -EBUSY）
lsof /dev/watchdog*

### ⑦ dmesg 关键字
dmesg | grep -iE 'watchdog.*(did not stop|nowayout prevents|out of range|cannot register|pretimeout event)'

### ⑧ 确认编译开关
zcat /proc/config.gz | grep -E '^CONFIG_WATCHDOG(_CORE|_SYSFS|_NOWAYOUT|_HANDLE_BOOT_ENABLED|_OPEN_TIMEOUT|_HRTIMER_PRETIMEOUT|_PRETIMEOUT_GOV)'

### ⑨ DT 侧（06 篇）
cat /proc/device-tree/<节点>/timeout-sec | xxd
```

⚠️ **三条最容易误读的**：
1. **`state = inactive` ≠ 看门狗没在跑**（§2.2）
2. **`cat status` 有副作用，连读两次结果不同**（§2.3）
3. **`pr_crit "watchdog did not stop!"` 多数是正常的 magic close**（§4.3）

**本篇不给任何命令的输出示例** ——**本套文档未上机运行**（99 §5.1）。

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| 为什么内核会替用户态喂狗、为什么有两个"在跑" | `08-feynman.md` |
| 用户态骨架与七组近邻符号 | `05-usage.md` |
| 心跳工作队列的完整算法 | `03-internals.md` §B2 |
| DT 失效决策树 | `06-devicetree.md` §4 |
