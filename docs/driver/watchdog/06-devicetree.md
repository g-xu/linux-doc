---
title: 设备树
---

# watchdog 06：设备树

## §0 发现路径分诊表

⚠️ **别先问"watchdog 有哪些 DT 属性"。** **先问"超时值从哪来"** ——
**因为这是本子系统唯一由核心消费的 DT 属性，而它有三个来源、有优先级。**

### §0.1 四条配置来源（只有一条是 DT）

| # | 来源 | 谁消费 | 优先级 | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|---|
| **1** | **模块参数 `timeout`** | **驱动自己**，再传给 `watchdog_init_timeout()` 的第二参数 | **最高** | **常用** | **只讲优先级**（§1.2） |
| **2** | **DT 属性 `timeout-sec`** | **`watchdog_init_timeout()`**（`drivers/watchdog/watchdog_core.c:140-141`） | **第二** | **29 个文件用** | **完整**（§1） |
| **3** | **驱动填的 `wdd->timeout` 默认值** | **驱动 probe 里直接赋值** | **兜底** | **总是** | **§1.2** |
| **4** | **用户态 `WDIOC_SETTIMEOUT`** | **运行期覆盖以上全部** | **运行期最高** | **守护进程常做** | **在 05 §4.4** |

⇒ **DT 只参与"开机时的初值"，运行期的最终决定权在用户态。**

### §0.2 官方通用绑定只定义了两件事

**`Documentation/devicetree/bindings/watchdog/watchdog.yaml` 全文（很短）**：

| 属性 | 内容 |
|---|---|
| **`$nodename`** | **`pattern: "^watchdog(@.*\|-[0-9a-f])?$"`** ——**节点名必须叫 `watchdog` 或 `watchdog@…`** |
| **`timeout-sec`** | **原话：*"Contains the watchdog timeout in seconds."*** |

**`additionalProperties: true`** ⇒ **其余一切（`reg` / `clocks` / `interrupts` / `resets` / 厂商私有）
由各自的驱动绑定定义，通用层不管。**

**另有 65 个厂商绑定文件**（目录共 66 个，减去通用那一个）——
**本篇不逐个讲**（99 §2.5）。

### §0.3 按任务找章节

| 我要… | 去 |
|---|---|
| 设开机默认超时 | §1 |
| 写一个 watchdog 节点 | §2 |
| **指定 `/dev/watchdogN` 里的 N** | **§3（结论是：arm64 上这条路是死字）** |
| **配错了会怎样** | **§4（静默失效与有日志的失效）** |
| 定位 | §5 |
| 看到某条报错 | §6 |
| 哪些期待不成立 | §7 |

---

## §1 `timeout-sec`：核心唯一消费的 DT 属性

### §1.1 消费代码（逐行）

```c
/* drivers/watchdog/watchdog_core.c:118-155 —— watchdog_init_timeout() 全文核心 */
int watchdog_init_timeout(struct watchdog_device *wdd,
				unsigned int timeout_parm, struct device *dev)
{
	const char *dev_str = wdd->parent ? dev_name(wdd->parent) :
			      (const char *)wdd->info->identity;
	unsigned int t = 0;
	int ret = 0;

	watchdog_check_min_max_timeout(wdd);

	/* ① 模块参数优先 */
	if (timeout_parm) {
		if (!watchdog_timeout_invalid(wdd, timeout_parm)) {
			wdd->timeout = timeout_parm;
			return 0;
		}
		pr_err("%s: driver supplied timeout (%u) out of range\n",
			dev_str, timeout_parm);
		ret = -EINVAL;
	}

	/* ② 其次才是 DT */
	if (dev && dev->of_node &&
	    of_property_read_u32(dev->of_node, "timeout-sec", &t) == 0) {
		if (t && !watchdog_timeout_invalid(wdd, t)) {
			wdd->timeout = t;
			return 0;
		}
		pr_err("%s: DT supplied timeout (%u) out of range\n", dev_str, t);
		ret = -EINVAL;
	}

	/* ③ 都不行就保留原值（驱动的默认） */
	if (ret < 0 && wdd->timeout)
		pr_warn("%s: falling back to default timeout (%u)\n", dev_str,
			wdd->timeout);

	return ret;
}
```

| 属性 | 写在哪个节点 | 类型 | 谁消费（定位到行） | 落到哪 |
|---|---|---|---|---|
| `timeout-sec` | **watchdog 设备自己的节点** | **`u32`，单位秒** | `drivers/watchdog/watchdog_core.c:141` 的 `of_property_read_u32(dev->of_node, "timeout-sec", &t)` | **`wdd->timeout`** |

⚠️ **消费的是 `dev->of_node`，而 `dev` 是 `watchdog_init_timeout()` 的第三参数**
⇒ **驱动传谁的 `dev` 就读谁的节点**（通常是 `&pdev->dev`，05 §1.1）。
⚠️ **不是 `wdd->parent->of_node`** ——**两者通常相同，但代码上是独立的。**

### §1.2 三层优先级（kerneldoc 写得很清楚）

**kerneldoc 原文**（`drivers/watchdog/watchdog_core.c:107-113`，逐字）：
> *"Initialize the timeout field of the watchdog_device struct with either the timeout module
> parameter (if it is valid value) or the timeout-sec property (only if it is a valid value and
> the timeout_parm is out of bounds). If none of them are valid then we keep the old value
> (which should normally be the default timeout value). Note that for the module parameter, '0'
> means 'use default' while it is an invalid value for the timeout-sec property. It should simply
> be dropped if you want to use the default value then."*

| 层 | 条件 | 结果 |
|---|---|---|
| **① 模块参数** | **`timeout_parm != 0` 且在范围内** | **直接用，`return 0`** |
| **② `timeout-sec`** | **属性存在、`t != 0`、在范围内** | **直接用，`return 0`** |
| **③ 驱动默认值** | **前两层都不成立** | **保留 `wdd->timeout` 原值** |

⚠️⚠️ **两个 0 的含义不同（kerneldoc 明确点出）**：

| 位置 | `0` 的含义 |
|---|---|
| **模块参数** | **"用默认值"** ——**`if (timeout_parm)` 直接跳过整个第 ① 层** |
| ⚠️ **`timeout-sec`** | ⚠️ **非法值** ——**`if (t && …)` 里 `t == 0` 不成立 ⇒ 走 `pr_err` 报错** |

⇒ **想让 DT 不干预超时，就应该**删掉**`timeout-sec` 属性，而不是写 `timeout-sec = <0>`。**
**kerneldoc 原话就是这个建议**：*"It should simply be dropped if you want to use the default value then."*

### §1.3 ⚠️ 一个容易漏的前提：范围校验用的是驱动填的边界

**`watchdog_timeout_invalid(wdd, t)`（`include/linux/watchdog.h:168`）比的是
`wdd->min_timeout` 和 `wdd->max_timeout`。**
⇒ **驱动没填这两个字段（都是 0）时，`watchdog_check_min_max_timeout()`（`:126`）会先修正它们。**
⚠️ **而 `max_hw_heartbeat_ms` 非 0 时 `max_timeout` 不生效**（01 §3.3 / 05 §7.3）
⇒ **DTS 写一个很大的 `timeout-sec` 在那种驱动上是能通过校验的。**

### §1.4 arm64 实际用量

| 项 | 值 | 重跑命令 |
|---|---|---|
| **`timeout-sec` 文件数** | **29** | `grep -rl 'timeout-sec' arch/arm64/boot/dts/ \| wc -l` |
| **`timeout-sec` 行数** | **34** | `grep -r 'timeout-sec' arch/arm64/boot/dts/ \| wc -l` |
| **`watchdog@` 节点的文件数** | **91** | `grep -rl 'watchdog@' arch/arm64/boot/dts/ \| wc -l` |
| **`watchdog@` 行数** | **125** | `grep -r 'watchdog@' arch/arm64/boot/dts/ \| wc -l` |
| ⚠️ **`wdt@` 节点的文件数** | **3**（行数 17） | **违反通用绑定的 `$nodename` 模式**（§7） |

⚠️ **对比 91 vs 29**：**绝大多数 watchdog 节点**不**写 `timeout-sec`**
⇒ **默认超时靠驱动里的常量。**

**取值分布（可重跑）**：

| 取值 | 次数 |
|---|---|
| **`<60>`** | **25**（压倒性多数） |
| `<30>` | 3 |
| `<10>` | 3 |
| `<40>` / `<20>` / `<12>` | 各 1 |

重跑：`grep -rhoE 'timeout-sec = <[0-9]+>' arch/arm64/boot/dts/ | sort | uniq -c | sort -rn`

---

## §2 怎么写一个 watchdog 节点

### §2.1 SoC dtsi 里的定义（真实节点）

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:1630-1635 —— 逐字引用 */
watchdog@ff848000 {
	compatible = "rockchip,rk3399-wdt", "snps,dw-wdt";
	reg = <0x0 0xff848000 0x0 0x100>;
	clocks = <&cru PCLK_WDT>;
	interrupts = <GIC_SPI 120 IRQ_TYPE_LEVEL_HIGH 0>;
};
```

**注意这里没有 `timeout-sec`** ——**超时由 `snps,dw-wdt` 驱动的默认值定。**

### §2.2 板级 dts 里加超时（真实节点）

```dts
/* arch/arm64/boot/dts/renesas/rzg2lc-smarc-som.dtsi:270-283 —— 逐字引用 */
&wdt0 {
	status = "okay";
	timeout-sec = <60>;
};

&wdt1 {
	status = "okay";
	timeout-sec = <60>;
};

&wdt2 {
	status = "okay";
	timeout-sec = <60>;
};
```

**这是最典型的写法**：**SoC dtsi 定义节点并 `status = "disabled"`，板级 dts 打开并设超时。**

### §2.3 常见属性的三方分工

| 属性 | 谁消费 | 说明 |
|---|---|---|
| `compatible` | **驱动模型** | **决定加载哪个驱动** |
| `reg` | **驱动**（`devm_platform_ioremap_resource`） | **watchdog 核心不看** |
| `interrupts` | **驱动** | **多数用于 pretimeout 中断**（04 §1） |
| `clocks` / `resets` | **驱动** | **89 / 61 个含 `watchdog@` 的 arm64 文件里出现** |
| `status` | **驱动模型** | **86 个文件里出现** |
| **`timeout-sec`** | **watchdog 核心**（唯一一个） | **§1** |

⚠️ **`clocks` / `resets` / `interrupts` 全部由驱动自己处理，核心一个都不看** ——
**和 phy 的 `phy-supply`（框架代管 regulator）形成对比**（watchdog 核心不代管任何资源）。

---

## §3 ⚠️`aliases`：能钉住 `/dev/watchdogN` 的 N —— 但树里没人用

### §3.1 机制

```c
/* drivers/watchdog/watchdog_core.c:260-268 */
	if (wdd->parent) {
		ret = of_alias_get_id(wdd->parent->of_node, "watchdog");
		if (ret >= 0)
			id = ida_simple_get(&watchdog_ida, ret,
					    ret + 1, GFP_KERNEL);
	}

	if (id < 0)
		id = ida_simple_get(&watchdog_ida, 0, MAX_DOGS, GFP_KERNEL);
```

**理论上的写法**：
```dts
aliases {
	watchdog0 = &wdt_soc;
	watchdog1 = &wdt_pmic;
};
```
⇒ **`of_alias_get_id(np, "watchdog")` 会返回 0 / 1，核心据此固定 `wdd->id`。**
⇒ **`/dev/watchdog0` 与 `/dev/watchdog1` 的对应关系就确定了。**

### §3.2 ⚠️ 但这条路在本树上是死字

| 检查 | 结果 |
|---|---|
| **arm64 DTS 里的 `watchdogN = <&…>` alias** | **0 处** |
| **整个 `arch/` 下（含 arm32）** | **0 处** |

重跑：`grep -rn 'watchdog[0-9]* = <&' arch/ --include=*.dts --include=*.dtsi` ⇒ **无输出**。

⇒ **本树里没有任何一份 DTS 用过这个机制** ⇒ **所有 `wdd->id` 都是自动分配的。**
**这不代表机制无用**（树外板级 DTS 可以用），**但读者不该指望在树里找到范例。**

### §3.3 ⚠️ 两个失效方式，都是静默的

| # | 情形 | 后果 | 有日志吗 |
|---|---|---|---|
| **1** | **alias 指定的 id 已被别的看门狗占用** | **`ida_simple_get(…, ret, ret+1, …)` 失败 ⇒ `id < 0` ⇒ 回退到自动分配**（`:267-268`） | **无** |
| **2** | **`wdd->parent` 为 NULL** | **整个 alias 分支被跳过**（`:260` 的 `if (wdd->parent)`） | **无** |

⇒ **"我在 DTS 里指定了 `watchdog0`，实际却成了 `watchdog2`"是可能的，而且查不到原因**
（03 §C3 第 2 类）。
**验证方法只有一个：`ls -l /sys/class/watchdog/watchdogN/device`**（§5.2）。

---

## §4 ⚠️ 静默失效与有日志的失效

**watchdog 的 DT 问题比 phy 少得多（只有一个属性），但要分清哪些有日志。**

```
timeout-sec 写了但看起来没生效
│
├─ ① 驱动有 timeout 模块参数，而且被设过（非 0）？
│     └─ 是 ⇒ 模块参数优先，DT 被跳过（drivers/watchdog/watchdog_core.c:129-133）
│        无日志（成功路径直接 return 0）
│        验证：cat /sys/module/<驱动名>/parameters/timeout
│
├─ ② 值越界（< min_timeout 或 > max_timeout）？
│     └─ 是 ⇒ 有日志！pr_err "%s: DT supplied timeout (%u) out of range"
│        （:146）+ 可能还有 pr_warn "falling back to default timeout"（:151）
│        这是唯一能从 dmesg 直接看出来的一层
│
├─ ③ 写成了 timeout-sec = <0>？
│     └─ ⇒ 有日志：同样走 :146 的 pr_err（t == 0 不满足 if (t && …)）
│        注意：模块参数的 0 是"用默认"，DT 的 0 是非法值（§1.2）
│
├─ ④ 驱动根本没调 watchdog_init_timeout()？
│     └─ ⇒ DT 属性完全没人读，无日志
│        验证：grep -n 'watchdog_init_timeout' drivers/watchdog/<驱动>.c
│        这是最常见的一条
│
├─ ⑤ 属性写在了错误的节点上？
│     └─ ⇒ 核心读的是 watchdog_init_timeout() 第三参数那个 dev 的 of_node（§1.1）
│        无日志
│
└─ ⑥ 用户态守护进程用 WDIOC_SETTIMEOUT 改过了？
      └─ ⇒ 运行期覆盖，DT 的初值确实生效过但已被替换
         验证：cat /sys/class/watchdog/watchdogN/timeout（07 §2）
```

⇒ **六层里有四层完全静默**（①④⑤⑥），**两层有 `pr_err`（②③）。**
**第 ④ 层"驱动没调"是最常见的** ——**它不是 DT 的问题，是驱动的问题。**

---

## §5 怎么定位（三层）

### §5.1 编译期 / 静态层

| 想确认 | 命令 |
|---|---|
| **驱动到底读不读 `timeout-sec`** | `grep -n 'watchdog_init_timeout' drivers/watchdog/<驱动>.c` ——**没有就是不读**（§4 第 ④ 层） |
| **驱动的默认超时是多少** | `grep -n 'timeout' drivers/watchdog/<驱动>.c \| grep -i default` |
| **驱动的 `min_timeout` / `max_timeout`** | `grep -n 'min_timeout\|max_timeout\|max_hw_heartbeat_ms' drivers/watchdog/<驱动>.c` |
| **有没有 timeout 模块参数** | `modinfo <驱动>.ko \| grep timeout` 或 `grep -n 'module_param' drivers/watchdog/<驱动>.c` |

### §5.2 运行期静态层：`/sys/class/watchdog/`

**watchdog 的 sysfs 面很全（12 个属性，07 §2），DT 问题基本都能在这里确认**：

```bash
### 有哪些看门狗
ls /sys/class/watchdog/

### 这个 watchdogN 对应哪个 DT 节点（排 §3.3 的 id 错位）
ls -l /sys/class/watchdog/watchdog0/device
cat /sys/class/watchdog/watchdog0/device/of_node/name 2>/dev/null

### 当前超时（DT 的初值有没有生效，看这个）
cat /sys/class/watchdog/watchdog0/timeout

### 范围（驱动填的）
cat /sys/class/watchdog/watchdog0/min_timeout
cat /sys/class/watchdog/watchdog0/max_timeout

### 驱动自报的名字（= wdd->info->identity）
cat /sys/class/watchdog/watchdog0/identity

### DT 侧：属性真的进了 DTB 吗
cat /proc/device-tree/<节点路径>/timeout-sec | xxd
```

⚠️ **`timeout-sec` 是 `u32` 大端存储在 DTB 里** ⇒ **`xxd` 看到的是 4 字节大端值**
（例：`timeout-sec = <60>` ⇒ `00 00 00 3c`）。

⚠️ **`cat timeout` 读到的是**当前**值，不一定是 DT 的初值** ——
**用户态可能已经用 `WDIOC_SETTIMEOUT` 改过**（§4 第 ⑥ 层）。

### §5.3 运行期动态层

| 手段 | 做法 |
|---|---|
| **dmesg** | `dmesg \| grep -iE 'watchdog.*(out of range\|falling back)'` ——**§4 的 ②③ 层** |
| **tracepoint** | **`watchdog_set_timeout` 事件能看到每次超时变更**（07 §3） |
| **`ioctl(WDIOC_GETTIMEOUT)`** | **与 sysfs 的 `timeout` 应当一致** |

---

## §6 错误消息 → 原因映射表

**watchdog 核心与 DT 直接相关的消息只有三条，全在 `watchdog_init_timeout()` 里。**

| 消息（逐字） | 出处 | 原因 |
|---|---|---|
| `%s: DT supplied timeout (%u) out of range` | `drivers/watchdog/watchdog_core.c:146` | **`timeout-sec` 越界或为 0** |
| `%s: driver supplied timeout (%u) out of range` | `drivers/watchdog/watchdog_core.c:134-135` | **模块参数越界** ——与 DT 无关 |
| `%s: falling back to default timeout (%u)` | `drivers/watchdog/watchdog_core.c:151-152` | **上面两条之后的兜底提示**；⚠️ **只在 `wdd->timeout` 非 0 时才打**（`:150`） |
| `%s: failed to register watchdog device (err = %d)` | `drivers/watchdog/watchdog_core.c:365-366` | **注册失败的总日志**；⚠️ **延迟注册路径走不到它**（03 §A2） |

⚠️ **`%s` 是 `dev_str`，它的取值有个陷阱**（`drivers/watchdog/watchdog_core.c:121-122`）：
```c
	const char *dev_str = wdd->parent ? dev_name(wdd->parent) :
			      (const char *)wdd->info->identity;
```
⇒ **没有 `parent` 时用 `identity`** ——**而它是 uapi 里的 32 字节定长数组，不保证 NUL 结尾**
（01 §7.2 / 05 §7.6）⇒ **日志可能出现越界的字符。**

⚠️ **没有任何一条消息告诉你"`timeout-sec` 写在了错的节点上"或"驱动没调 `watchdog_init_timeout()`"**
⇒ **§4 的 ①④⑤⑥ 四层全部静默。**

---

## §7 死字与不成立的期待

| 期待 | 实际 | 依据 |
|---|---|---|
| **"用 `aliases` 就能固定 `/dev/watchdogN` 的编号"** | ⚠️ **机制在，但本树 0 处使用，且失效时静默** | **§3.2 / §3.3** |
| **"写了 `timeout-sec` 就一定生效"** | ⚠️ **模块参数会压过它，而且无日志** | **§1.2 / §4 第 ① 层** |
| **"`timeout-sec = <0>` 表示用默认值"** | ⚠️ **错，那是非法值，会报 `pr_err`** ——**模块参数的 0 才是"用默认"** | **kerneldoc 明文 + `:142` 的 `if (t && …)`** |
| **"通用绑定定义了更多属性"** | **只有 `$nodename` 和 `timeout-sec` 两项** | `Documentation/devicetree/bindings/watchdog/watchdog.yaml` 全文 |
| **"节点必须叫 `watchdog@…`"** | ⚠️ **通用绑定的 `$nodename` 模式是这么写的，但 arm64 里有 3 个文件用 `wdt@`** | **`grep -rl 'wdt@' arch/arm64/boot/dts/ \| wc -l` ⇒ 3**；**schema 校验只对声明引用该 schema 的节点生效** |
| **"`clocks` / `resets` 由核心管"** | ⚠️ **不** ——**核心一个资源都不代管，全部由驱动处理** | §2.3 |
| **"pretimeout 也能在 DT 里配"** | ⚠️ **通用绑定里没有 pretimeout 属性** | **`watchdog.yaml` 全文只有两项**；**pretimeout 只能通过 `WDIOC_SETPRETIMEOUT` 或 sysfs 设** |
| **"`nowayout` 能在 DT 里配"** | ⚠️ **不能** ——**它是编译期 Kconfig + 模块参数 + sysfs 三条路** | 01 §0.7 / 07 §2 |

**反例（说明结论的方向）**：
**`timeout-sec` 在 arm64 上确实有 29 个文件、34 处在用**，
**`arch/arm64/boot/dts/renesas/rzg2lc-smarc-som.dtsi:270-283` 是真生效的三个节点**（§2.2）
⇒ **不能反过来说"watchdog 的 DT 属性都是死字"。**

## §8 阅读下一篇

| 想知道 | 去 |
|---|---|
| **12 个 sysfs 属性 + 4 个 tracepoint + 用户态工具** | `07-debug.md` |
| 为什么内核会替用户态喂狗 | `08-feynman.md` |
| 驱动骨架（含 `watchdog_init_timeout` 的用法） | `05-usage.md` §1 |
| 注册流程与 id 分配 | `03-internals.md` §A3 |
