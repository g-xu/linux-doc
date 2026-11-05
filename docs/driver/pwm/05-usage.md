---
title: 使用指南
---

# pwm 05：各角色的代码骨架

## §0 开篇三件事

### §0.1 免责声明

⚠️ **本篇所有骨架均未编译、未上机验证。** 它们是"结构正确、
符号存在、调用顺序符合 6.1 核心实现"的示意代码，
省略了错误处理的部分分支、寄存器细节和头文件清单。
直接粘贴不能工作，**要当模板改**。

### §0.2 pwm 有哪些角色

| 角色 | 你在写什么 | 节 | arm64 相关 |
|---|---|---|---|
| **A. provider 驱动作者** | 一个新的 PWM 控制器驱动，注册 `pwm_chip` | §1 | ✅ |
| **B. 消费者驱动作者** | 一个用 PWM 的内核驱动（背光/LED/风扇/调压…） | §2 | ✅ |
| **C. 兼职 provider** | 主职是别的子系统（GPIO/LED/桥片），顺手暴露 PWM | §3 | ✅ |
| **D. 用户态操作者** | shell 脚本，走 `/sys/class/pwm/` | §4 | ✅ |
| **E. 板级静态查表作者** | `pwm_add_table()` + `struct pwm_lookup` | §5 | ❌ **arm32 遗留** |
| **F. 调试者** | tracepoint / debugfs / `CONFIG_PWM_DEBUG` | → [07-debug.md](07-debug.md) | ✅ |

⚠️ **角色 E 在 arm64 上不适用**，但必须知道它存在——
因为 `pwm_get()` 的实现里有一整条静态查表分支
（`core.c:943-1007` 的模糊打分，03 §A3.3），
读代码时会碰到。§5 只交代"为什么你不该用它"。

### §0.3 符号核实口径

本篇出现的每个符号都过了两道门：

1. **存在**：`grep -nE 'define[[:space:]]+FOO'` 或在 `include/linux/pwm.h`
   的原型表里（04 §0 引用的那份）确认本版本有。
2. **外部驱动能用**：`EXPORT_SYMBOL*` **且**在 `include/linux/pwm.h` 里声明；
   或者是 `pwm.h` 里的 `static inline`（inline 不需要导出）。

⇒ 完整核实表在 §6。**有 kerneldoc 不等于能用**，
`pwmchip_sysfs_export()`（`include/linux/pwm.h:596`）就是反例——
它有原型，但只给核心用（04 §1.1）。

---

## §1 角色 A：provider 驱动骨架

### §1.1 最小可用形态

**只需要一个回调：`.apply`。** `pwm_ops_check()`（`core.c:236-247`）
只硬性要求它（`:240-241`）。

树内的参考实现选 `drivers/pwm/pwm-visconti.c`（177 行，
Toshiba Visconti，arm64）——它是 6.1 里最干净的一个：
`.apply`（`:46`）+ `.get_state`（`:106`）+ ops（`:129-133`）+
probe（`:135`）+ `devm_pwmchip_add()`（`:153`），没有多余东西。

```c
#include <linux/pwm.h>
#include <linux/platform_device.h>

struct myctl {
	struct pwm_chip	chip;		/* ⚠️ 必须内嵌，pwm_chip 没有 priv 指针 */
	void __iomem	*base;
	struct clk	*clk;
	struct mutex	lock;		/* ⚠️ 自己带锁：核心不串行化 .apply */
};

static inline struct myctl *to_myctl(struct pwm_chip *chip)
{
	return container_of(chip, struct myctl, chip);
}
```

⚠️ **三条硬性约定，缺一条都会出问题**：

1. **`pwm_chip` 必须是你自己结构的成员**——它没有 `void *priv`
   （02 §1.0.D），拿回私有数据只能 `container_of`。
2. **自己带锁**。`pwm_apply_state()` 完全不加锁就调 `.apply`
   （03 §B1.1）⇒ 并发是你的责任。
3. **`.owner = THIS_MODULE`**。它在 `pwm_ops` 里而不是 `pwm_chip` 里
   （02 §1.0.D），核心靠它 `try_module_get()`（03 §B3）。
   树内 64 个 `.c` 里 **60 个**设了它：

   ```bash
   grep -rc 'owner.*THIS_MODULE' drivers/pwm/*.c | grep -v ':0' | wc -l   # 60
   ls drivers/pwm/*.c | wc -l                                             # 64（含 core.c/sysfs.c）
   ```

### §1.2 `.apply`：唯一必填的回调

```c
static int my_apply(struct pwm_chip *chip, struct pwm_device *pwm,
		    const struct pwm_state *state)
{
	struct myctl *mc = to_myctl(chip);
	u64 period = state->period, duty = state->duty_cycle;
	int ret = 0;

	mutex_lock(&mc->lock);

	if (!state->enabled) {
		/* ⚠️ 请求关闭：不要只写 duty=0，要真的停掉输出 */
		my_hw_disable(mc, pwm->hwpwm);
		goto out;
	}

	if (state->polarity == PWM_POLARITY_INVERSED) {
		/* ⚠️ 硬件不支持反相就必须返回 -EINVAL，不能默默忽略 */
		if (!mc->has_inversion) { ret = -EINVAL; goto out; }
		my_hw_set_polarity(mc, pwm->hwpwm, true);
	}

	/* ⚠️ 契约：向下取整——选不超过请求值的最大可实现值 */
	ret = my_hw_set_timing(mc, pwm->hwpwm, period, duty);
	if (ret)
		goto out;

	my_hw_enable(mc, pwm->hwpwm);
out:
	mutex_unlock(&mc->lock);
	return ret;
}
```

⚠️ **五条 `.apply` 的隐式契约**（都不在 kerneldoc 里）：

| 契约 | 依据 | 违反的后果 |
|---|---|---|
| **周期和占空比都向下取整** | `pwm_apply_state_debug()` 的检查判据（`core.c:491` / `:507`，03 §B4.1） | 开 `CONFIG_PWM_DEBUG` 时 `dev_warn`；否则消费者拿到比请求更长的脉宽 |
| **`enabled == false` 要真停输出** | `core.c:512-513` 的检查 | 关不掉；`pwm.rst:54-55` 明说"要 inactive 状态请用 `duty_cycle=0, enabled=true`" |
| **必须幂等** | `core.c:529-538` 的 `dev_err` | 同样的 state 连续 apply 两次结果不同 |
| **不支持的极性要报错** | `core.c:477-479` 的检查 | 波形反了却没人知道 |
| **自己保证并发安全** | 核心不加锁（03 §B1.1） | 两线程同时 apply → 寄存器写交错 |

⚠️ **`.apply` 里能睡**（`pwm_apply_state()` 有 `might_sleep()`，
`core.c:558`），所以 I2C/SPI 的 PWM 扩展芯片可以直接在里面做传输。
⚠️ **但 `.apply` 里不能调 `pwm_get()` / `pwm_put()` / `pwm_request_from_chip()`**
——它们要拿 `pwm_lock`，而你可能正在被持锁的路径调用（03 §B1.1）。

### §1.3 `.get_state`：强烈建议实现

```c
static int my_get_state(struct pwm_chip *chip, struct pwm_device *pwm,
			struct pwm_state *state)
{
	struct myctl *mc = to_myctl(chip);

	/* ⚠️ 必须填满 4 个字段（usage_power 由核心/消费者管，别动） */
	state->period	  = my_hw_read_period(mc, pwm->hwpwm);
	state->duty_cycle = my_hw_read_duty(mc, pwm->hwpwm);
	state->polarity	  = my_hw_read_polarity(mc, pwm->hwpwm);
	state->enabled	  = my_hw_is_running(mc, pwm->hwpwm);

	return 0;
}
```

⚠️⚠️ **三条只有读了 6.1 代码才知道的事**：

1. **它在 request 时被调，不是注册时。** kerneldoc
   （`include/linux/pwm.h:267-269`）说"只在注册时每通道调一次"，
   **说错了**——真实调用点是 `pwm_device_request()`（`core.c:110-111`），
   即**每次 `pwm_get()` 成功都会调一次**（03 §A1.1、§C2.2）。
   ⇒ **必须可重入**，不能假设只跑一次。
2. **它运行时 `pwm->args` 还是全零**，因为 DT 的 `of_xlate` 在
   request 之后才填 args（03 §A3.1）。⇒ **不要在 `.get_state` 里读
   `pwm_get_args()`**。
3. **不实现它的代价被严重低估。** 默认配置下核心完全不抱怨
   （警告只在 `CONFIG_PWM_DEBUG` 下打，`core.c:244-245`），
   但后果是：
   - `pwm_adjust_config()` 会把 duty 清零 ⇒ ⚠️ **背光闪黑，
     PWM 调压器电压掉到 0**（04 §3.3）；
   - `pwm_apply_state_debug()` 直接放弃诊断（`core.c:452-453`）；
   - `pwm_get()` 成功也不产生任何 `pwm_get` trace 事件（03 §B6.1）。

### §1.4 probe / remove

```c
static const struct pwm_ops my_ops = {
	.apply	   = my_apply,
	.get_state = my_get_state,
	.owner	   = THIS_MODULE,
};

static int my_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct myctl *mc;
	int ret;

	mc = devm_kzalloc(dev, sizeof(*mc), GFP_KERNEL);
	if (!mc)
		return -ENOMEM;

	mc->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(mc->base))
		return PTR_ERR(mc->base);

	mc->clk = devm_clk_get_enabled(dev, NULL);
	if (IS_ERR(mc->clk))
		return dev_err_probe(dev, PTR_ERR(mc->clk), "no clock\n");

	mutex_init(&mc->lock);

	mc->chip.dev  = dev;		/* ⚠️ 必填：DT 查找靠它的 of_node */
	mc->chip.ops  = &my_ops;	/* ⚠️ 必填 */
	mc->chip.npwm = 4;		/* ⚠️ 必填 */
	/* ⚠️ 不要设 chip.base——它是输出，pwmchip_add() 会覆盖 */

	return devm_pwmchip_add(dev, &mc->chip);
}
```

⚠️ **四条 probe 陷阱**：

1. **`chip->base` 是输出不是输入。** `pwmchip_add()` 无条件
   `chip->base = ret;`（`core.c:276`）。写
   `mc->chip.base = pdev->id;` 是**死代码**（03 §B2.1）。
2. **用 `devm_pwmchip_add()`（`core.c:347`）而不是 `pwmchip_add()`。**
   后者要你自己在 remove 里配对 `pwmchip_remove()`，
   而 `pwmchip_remove()` **不检查通道是否还被占用、直接
   `kfree(chip->pwms)`**（03 §A2.1）⇒ 忘了配对或顺序错就是 UAF。
   ⚠️ devm 版也不能消除 unbind 时的 UAF（03 §B3），
   但至少消除了顺序错误。
3. **`chip.dev` 必须设**，否则 DT 查找（`of_pwmchip_add()` 靠
   `chip->dev->of_node`，`core.c:181-198`）和 sysfs 目录都建不起来。
4. **自定义 `of_xlate` 时必须自己设 `of_pwm_n_cells`。**
   `of_pwmchip_add()` 只在你没给 `of_xlate` 时才帮你设默认值
   （`core.c:191`，默认 **2**）⇒ 给了自定义 xlate 又忘了设 cells
   数，解析会错位（03 §A1.3）。

如果坚持用非 devm 版：

```c
static int my_remove(struct platform_device *pdev)
{
	struct myctl *mc = platform_get_drvdata(pdev);

	pwmchip_remove(&mc->chip);	/* ⚠️ 必须丢弃返回值 */
	return 0;
}
```

⚠️ **必须写成 `pwmchip_remove(&mc->chip);` 而不是
`ret = pwmchip_remove(...)`**：`CONFIG_PWM=y` 时它是
`void`（`include/linux/pwm.h:392`），`CONFIG_PWM=n` 时是
`static inline int`（`:476-479`）——**签名不一致**（03 §C2.4）。
只有丢弃返回值的写法在两种配置下都能编译。

### §1.5 可选的三个回调

| 回调 | 何时实现 | 陷阱 |
|---|---|---|
| `.request` | 通道需要上电/申请资源 | ⚠️ 被**持 `pwm_lock` 调用**（03 §B1.1）⇒ 里面不能调核心 API |
| `.free` | 与 `.request` 配对 | 同上；注意 `pwm_put()` 会紧接着 `pwm_set_chip_data(pwm, NULL)`（`core.c:1030`）⇒ **`chip_data` 会被清零** |
| `.capture` | 硬件能测输入波形 | ⚠️ **持全局 `pwm_lock` 整个调用期间**（`core.c:610-612`）⇒ 阻塞全系统 pwm 注册；`timeout` 别设太长。arm64 上树内 0 个实现（04 §5.1） |

`.request` 里放私有数据的正确写法：

```c
static int my_request(struct pwm_chip *chip, struct pwm_device *pwm)
{
	struct my_chan *ch = kzalloc(sizeof(*ch), GFP_KERNEL);

	if (!ch)
		return -ENOMEM;

	return pwm_set_chip_data(pwm, ch);	/* core.c:213 */
}
```

⚠️ **为什么要在 `.request` 里设，而不是 probe 里？**
不是因为有状态检查——`pwm_set_chip_data()` 在 6.1 里
**只在 `!pwm` 时返回 `-EINVAL`，其余恒成功**（`core.c:213-221`，
旧内核那个 `PWMF_REQUESTED` 检查已经没了）。
真正的原因是**`pwm_put()` 会把它清零**（`core.c:1030`）
⇒ 每次 request 都得重新设。

---

## §2 角色 B：消费者驱动骨架

### §2.1 标准形态（照 `pwm_bl.c` 的四步）

```c
#include <linux/pwm.h>

struct myled {
	struct pwm_device *pwm;
	struct pwm_state   state;	/* ⚠️ 建议常驻，避免每次读回 */
};

static int myled_probe(struct platform_device *pdev)
{
	struct myled *ml = /* devm_kzalloc ... */;
	int ret;

	/* 步 1：取句柄。con_id 传 NULL ⇒ 只要 DTS 有 pwms = <...>; */
	ml->pwm = devm_pwm_get(&pdev->dev, NULL);
	if (IS_ERR(ml->pwm))
		return dev_err_probe(&pdev->dev, PTR_ERR(ml->pwm),
				     "failed to get PWM\n");

	/* 步 2：用板级参考值初始化，不改动输出开关 */
	pwm_init_state(ml->pwm, &ml->state);

	/* 步 3：设占空比。按比例设最省事 */
	pwm_set_relative_duty_cycle(&ml->state, 50, 100);	/* 50% */
	ml->state.enabled = true;

	/* 步 4：一次性应用 */
	ret = pwm_apply_state(ml->pwm, &ml->state);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to apply\n");

	return 0;
}
```

对应 `drivers/video/backlight/pwm_bl.c` 的
`:508`（get）→ `:525`（init_state）→ `:536`（apply）→ `:571`（读回初值）。

⚠️ **五条消费者必知**：

1. **`devm_pwm_get()`（`core.c:1070`）没有配对的 `devm_pwm_put()`**——
   `include/linux/pwm.h` 里**不存在**这个符号。释放由 devm 自动做。
   要手动释放就得用 `pwm_get()` + `pwm_put()` 这一对。
2. **`con_id` 传 `NULL` 走 index 路径**，DTS 只写 `pwms = <&pwm 0 ...>;`；
   传非 NULL 则**要求 DTS 有 `pwm-names`**（03 §A3.1）。
   arm64 上 112 个 DTS 有 `pwms =`、**只有 4 个有 `pwm-names`**
   ⇒ **默认传 `NULL`**。
3. ⚠️ **`pwm_put()` 不关输出**（`core.c:1015-1036`，03 §A8）
   ⇒ **remove 里要自己先关**：

   ```c
   static int myled_remove(struct platform_device *pdev)
   {
   	struct myled *ml = platform_get_drvdata(pdev);

   	ml->state.enabled = false;
   	ml->state.duty_cycle = 0;
   	pwm_apply_state(ml->pwm, &ml->state);	/* ⚠️ 必须显式关 */
   	return 0;
   }
   ```

4. ⚠️ **resume 后必须自己重配。** 核心的 PM 只覆盖 sysfs export
   出来的通道（04 §1.4，03 §A10）⇒ 内核消费者的通道
   **在 resume 后状态未定义**，`pwm.rst:82-84` 明确要求消费者自己处理。
5. ⚠️ **`pwm_get_state()` 不是硬件真值**（`include/linux/pwm.h:102-106`
   就是 `*state = pwm->state;`）⇒ 它给的是**上次 apply 传进去的值**
   （03 §A5.3、§C1.2）。想验证硬件实现了什么，只有
   `CONFIG_PWM_DEBUG`（03 §B4）。

### §2.2 变体一：接管 bootloader 已经在跑的 PWM

场景：背光/调压器在内核起来前就在工作，**不能有任何中断**。

```c
	drvdata->pwm = devm_pwm_get(dev, NULL);
	if (IS_ERR(drvdata->pwm))
		return PTR_ERR(drvdata->pwm);

	/* ⚠️ 不要用 pwm_init_state()：它把 duty 归零 */
	/* ⚠️ 更不要用 pwm_apply_args()：它把 enabled 设成 false */
	ret = pwm_adjust_config(drvdata->pwm);	/* core.c:626 */
	if (ret)
		return ret;
```

照 `drivers/regulator/pwm-regulator.c:374`——**全树唯一一处**
（04 §3.3）。

⚠️⚠️ **它有一个前提条件必须确认**：`pwm_adjust_config()` 在
`pwm->state.period == 0` 时会走"把 duty 清零"的分支
（`core.c:642-648`）⇒ **如果 provider 没实现 `.get_state`，
`pwm->state` 全是 0，它就会把输出关掉**（03 §A6.1）。

⇒ **判据：用 `pwm_adjust_config()` 之前，先确认你板子上的
PWM 控制器驱动实现了 `.get_state`。**

```bash
# 确认某个 provider 有没有 .get_state
grep -n 'get_state' drivers/pwm/pwm-<你的控制器>.c
```

### §2.3 变体二：按比例控制（亮度/电压/转速）

```c
	struct pwm_state st;

	pwm_init_state(ml->pwm, &st);			/* 取板级 period */
	pwm_set_relative_duty_cycle(&st, level, max_level);
	st.enabled = (level > 0);
	pwm_apply_state(ml->pwm, &st);
```

`pwm_set_relative_duty_cycle()`（`include/linux/pwm.h:237`）
在 `!scale || duty_cycle > scale` 时返回 `-EINVAL`；
读回用 `pwm_get_relative_duty_cycle()`（`:209`），
`period == 0` 时返回 0。

⚠️ **两者都用 `DIV_ROUND_CLOSEST*`，所以往返不精确**：
设 33/100 再读回可能得到 33 也可能得到 32。
⇒ **不要用"读回值 == 设进去的值"做状态判断**，
自己缓存 level（02 §4.3）。

### §2.4 变体三：波形发生（时序敏感）

参考 `drivers/media/rc/pwm-ir-tx.c`：
`pwm_init_state`（`:61`）+ `pwm_set_relative_duty_cycle`（`:64`）
+ 循环里的一对 `pwm_apply_state`（`:70` 开 / `:79` 关）。

⚠️⚠️ **这类代码必须知道核心有全等短路**：
`pwm_apply_state()` 在**五个字段全部相同**时直接
`return 0`，不调 `.apply`、也不发 trace（`core.c:566-571`，03 §A5.2）。

⇒ **想让第二次 apply 真的生效，必须至少改一个字段。**
开/关载波靠改 `enabled` 是安全的；
⚠️ **靠"再 apply 一次相同的值来刷新硬件"是无效的，没有任何办法强制刷新。**

⚠️ 另外 `pwm_apply_state()` 有 `might_sleep()`（`core.c:558`）
⇒ **不能在中断/原子上下文调用**，波形精度受调度抖动影响。

### §2.5 消费者绝对不要做的四件事

| 不要 | 原因 |
|---|---|
| ❌ 实现 provider 侧的 PM（suspend/resume 里操作别人的 chip） | `pwm.rst:162-163`：驱动不该实现 PM，消费者自己处理 |
| ❌ 用 `pwm_apply_args()` | ⚠️ 它会**把输出关掉**，且树内 5 处调用**全部带"应当删除"的 FIXME**（04 §3.8） |
| ❌ 用 `pwm_request()` / `pwm_free()` | kerneldoc 明写 deprecated（`core.c:364` / `:432`）；前者靠全局编号，后者只是 `pwm_put()` 的别名 |
| ❌ 硬编码 `period = 0` 表示"关闭" | `pwm_apply_state()` 对 `!state->period` 返回 `-EINVAL`（`core.c:560-562`）⇒ 关闭要用 `enabled = false` |

---

## §3 角色 C：兼职 provider（主职在别的子系统）

场景：你的硬件是个 GPIO 控制器 / LED 图案发生器 / 显示桥片，
同一块寄存器**顺手也能做 PWM**。树内有 4 个先例（04 §4）：
`drivers/gpio/gpio-mvebu.c:877` · `drivers/leds/rgb/leds-qcom-lpg.c:1040` ·
`drivers/gpu/drm/bridge/ti-sn65dsi86.c:1626` ·
`drivers/staging/greybus/pwm.c:302`。

```c
struct mvchip {
	struct gpio_chip	gc;	/* 主职 */
	struct pwm_chip		chip;	/* 兼职 */
	void __iomem		*base;
};

static int my_probe(struct platform_device *pdev)
{
	/* ... 先完成主职注册 ... */
	ret = devm_gpiochip_add_data(dev, &mc->gc, mc);
	if (ret)
		return ret;

	/* 兼职：只有 DT 里声明了才注册 */
	if (!of_property_read_bool(dev->of_node, "#pwm-cells"))
		return 0;

	mc->chip.dev  = dev;
	mc->chip.ops  = &my_pwm_ops;
	mc->chip.npwm = 2;

	return devm_pwmchip_add(dev, &mc->chip);
}
```

⚠️ **四条只有兼职 provider 才会踩的坑**：

1. ⚠️ **必须处理 `CONFIG_PWM=n`。** 你的 Kconfig 项不在
   `drivers/pwm/Kconfig` 里，所以 `CONFIG_PWM` 可能是 n。
   `include/linux/pwm.h` 有一整套 `CONFIG_PWM=n` 桩
   （`:413-521`），但 ⚠️ **`pwmchip_remove()` 的桩签名与真版不一致**
   （`:476-479` 是 `int`，`:392` 是 `void`，03 §C2.4）
   ⇒ **要么用 devm 版，要么丢弃返回值**。
   正确做法是在 Kconfig 里写 `depends on PWM` 或 `select PWM`。
2. ⚠️ **一个 `of_node` 上两个子系统的 cells 属性会撞名字。**
   GPIO 用 `#gpio-cells`，PWM 用 `#pwm-cells`，
   两者可以共存于同一节点，但**你的 DT 绑定文档必须同时描述两套**。
3. ⚠️ **两个注册的顺序和失败回滚。** 主职注册失败要退出；
   兼职注册失败**是否应该让整个 probe 失败**是个设计决定——
   `gpio-mvebu` 选择让它失败（直接 `return`），
   因为 DT 既然写了 `#pwm-cells` 就说明板子要用。
4. ⚠️ **`pwm_chip` 和主职结构共享同一把锁时要小心锁序。**
   `.apply` 不持 `pwm_lock`（03 §B1.1），但 `.request`/`.free` **持**
   ⇒ 如果 GPIO 路径也拿同一把私有锁，`.request` 里就形成
   `pwm_lock → 私有锁`，而 GPIO 路径是 `私有锁`（无 `pwm_lock`）
   ⇒ 只要 GPIO 路径不反向调 pwm 核心 API 就安全。

---

## §4 角色 D：用户态操作（sysfs）

⚠️ 前提：`CONFIG_PWM_SYSFS=y`（有 SYSFS 时 `default y`，
`drivers/pwm/Kconfig:32-34`）。属性清单见 04 §1.3。

### §4.1 完整的一次操作

```sh
# 1. 找到你要的控制器 —— ⚠️ 不要硬编码 pwmchip0
for c in /sys/class/pwm/pwmchip*; do
	printf '%s -> %s\n' "$c" "$(readlink -f "$c/device")"
done

CHIP=/sys/class/pwm/pwmchip0
cat $CHIP/npwm                  # 本控制器有几条通道

# 2. 导出通道 0（编号是 hwpwm，本地编号）
echo 0 > $CHIP/export           # 成功后出现 $CHIP/pwm0/

# 3. 先设周期，再设占空比 —— ⚠️ 顺序不能反
echo 1000000 > $CHIP/pwm0/period        # 1 ms
echo  500000 > $CHIP/pwm0/duty_cycle    # 50%

# 4. 极性（可选）—— ⚠️ 必须在 enable 之前
echo inversed > $CHIP/pwm0/polarity

# 5. 开
echo 1 > $CHIP/pwm0/enable

# 6. 关 + 收回
echo 0 > $CHIP/pwm0/enable      # ⚠️ 这一步不能省，见下
echo 0 > $CHIP/unexport
```

### §4.2 六个用户态陷阱

1. ⚠️ **`pwmchipN` 的 N 不稳定。** 它是全局 `base`
   （`drivers/pwm/sysfs.c:514`），由 probe 顺序决定，
   换内核版本/改模块化就会变（03 §B2.1）。
   ⇒ **靠 `readlink -f $CHIP/device` 认板子上的哪个控制器**，
   不要硬编码 `pwmchip0`。
2. ⚠️ **`duty_cycle` 必须小于 `period`，而且要后设。**
   新导出的通道 `period` 是 0，先写 `duty_cycle` 一定失败
   （`pwm_apply_state()` 的 `duty_cycle > period → -EINVAL`，
   `core.c:560-562`）。⇒ **永远先 period 后 duty_cycle。**
   反过来"把 period 改小"时也要先把 duty_cycle 调小。
3. ⚠️ **`polarity` 只能在未 enable 时改**（`pwm.rst:122-123`）。
4. ⚠️ **`unexport` 不停输出**（`drivers/pwm/sysfs.c:285-308`，03 §A9.2）
   ⇒ **必须先 `echo 0 > enable`**，否则通道会一直跑，
   而句柄已经没了（只能重新 export 才能关）。
5. ⚠️ **`export` 的 `-EBUSY` 有两个含义**：
   通道被内核驱动占用（`PWMF_REQUESTED`）
   或已经 export 过（`PWMF_EXPORTED`）——**errno 相同**（04 §1.5）。
   ⇒ 排查时先 `cat /sys/kernel/debug/pwm` 看这条通道的 label
   是不是某个内核驱动的名字。
6. ⚠️ **`cat capture` 在 arm64 上恒定失败**，
   errno 是 `ENOSYS`（"Function not implemented"）
   ——树内只有 2 个 arm32 provider 实现了 `.capture`（04 §5.1）。
   **不要把这个报错当成 sysfs 坏了。**

### §4.3 读回的值是请求值，不是硬件值

```sh
echo 1000003 > $CHIP/pwm0/period
cat $CHIP/pwm0/period            # ⇒ 1000003，即使硬件只能做 1000000
```

`period_show()`（`drivers/pwm/sysfs.c:36`）走 `pwm_get_state()`，
后者只是 `*state = pwm->state;`（`include/linux/pwm.h:102-106`）
⇒ **sysfs 显示的是你写进去的值**（03 §C1.2）。
唯一能看到差异的办法是开 `CONFIG_PWM_DEBUG`（03 §B4）看 dmesg 警告。

---

## §5 角色 E：静态查表（arm64 不适用，但要认得）

```c
static struct pwm_lookup my_lookup[] = {
	PWM_LOOKUP("mypwm.0", 0, "backlight.0", NULL, 50000, PWM_POLARITY_NORMAL),
};

/* 板级初始化里 */
pwm_add_table(my_lookup, ARRAY_SIZE(my_lookup));
```

`struct pwm_lookup` 有 8 个字段（`include/linux/pwm.h:555-564`），
注册/注销是 `pwm_add_table()`（`:583`）/ `pwm_remove_table()`（`:584`）。

⚠️ **为什么 arm64 上不该用**：

1. **树内使用者全部是 arm32 板级文件**——
   `arch/arm/mach-pxa/`（16 个）+ `arch/arm/mach-s3c/`（6 个），
   共 22 个（04 §2）。arm64 没有板级 C 文件这种东西。
2. ⚠️ **查表用模糊打分**（`core.c:943-1007`）：
   dev_id 匹配 +2、con_id 匹配 +1，
   ⚠️ **通配条目（`match == 0`）会兜住所有查不到的请求**
   ⇒ 配错了不会报错，而是**拿到一条错误的通道**（03 §C4）。
3. ⚠️ **这条路径里有 `request_module()` 兜底**
   （`core.c:979-984`），注释自陈是"work around driver load
   ordering issues"（03 §C1.4）——DT 平台该用 `-EPROBE_DEFER`。

⇒ **arm64 上一律走 DT**，见 [06-devicetree.md](06-devicetree.md)。

---

## §6 近邻符号区分

这一节是本篇最该慢读的地方。**pwm 的 API 里有九组容易混的符号，
其中三组用错会直接产生现场故障。**

### §6.1 `pwm_set_period()` / `pwm_set_duty_cycle()` vs `pwm_apply_state()`

| 符号 | 位置 | 实际行为 |
|---|---|---|
| `pwm_set_period(pwm, ns)` | `include/linux/pwm.h:117` | ⚠️ **只写 `pwm->state.period`，不碰硬件** |
| `pwm_set_duty_cycle(pwm, ns)` | `include/linux/pwm.h:132` | ⚠️ **只写 `pwm->state.duty_cycle`，不碰硬件** |
| `pwm_apply_state(pwm, &st)` | `core.c:546`（导出在 `:589`） | 真正调 `.apply` |

⚠️⚠️ **错用会怎样**：以为 `pwm_set_period()` 会改硬件 ⇒
**波形完全没变，而且没有任何报错**。更糟的是它**污染了
`pwm->state`**，导致下一次 `pwm_apply_state()` 传入相同值时
命中全等短路（03 §A5.2）⇒ **永久失效**，直到有人改另一个字段。

⇒ **判据：`pwm_set_*` 这一族（`:117` / `:132`）是给核心和
provider 内部用的，消费者永远不要用。**
消费者只操作自己栈上的 `struct pwm_state`，然后 `pwm_apply_state()`。

### §6.2 `pwm_init_state()` vs `pwm_apply_args()` vs `pwm_adjust_config()`

| 符号 | 位置 | 改 `enabled` 吗 | 用在哪 |
|---|---|---|---|
| `pwm_init_state(pwm, &st)` | `include/linux/pwm.h:179` | ❌ **不动**（只填 period/polarity/duty=0/usage_power=false，4 个字段） | ✅ **默认选它** |
| `pwm_apply_args(pwm)` | `include/linux/pwm.h:522` | ⚠️ **设成 false ⇒ 关输出** | ❌ 别用，5 处调用全带待删 FIXME |
| `pwm_adjust_config(pwm)` | `core.c:626` | 保持现状 | ✅ 接管 bootloader 配置时 |

⚠️ **错用会怎样**：在背光 probe 里用 `pwm_apply_args()`
⇒ **屏幕闪一下黑**。用 `pwm_init_state()` 接管 bootloader 配置
⇒ `duty_cycle` 被写成 0，**下一次 apply 时屏幕黑掉**。

⚠️ 三者共同的前提：**`pwm_adjust_config()` 在 provider 没有
`.get_state` 时也会清零 duty**（03 §A6.1）⇒ 选它之前先确认 provider。

### §6.3 `pwm_get_state()` vs `.get_state` 回调

| 名字 | 是什么 | 数据来源 |
|---|---|---|
| `pwm_get_state(pwm, &st)` | `include/linux/pwm.h:102` 的 inline | ⚠️ **`pwm->state`，即上次请求值** |
| `pwm_ops->get_state()` | provider 实现的回调 | 真硬件寄存器 |

⚠️ **名字几乎一样，语义相反。** 消费者调 `pwm_get_state()`
**不会**触发 provider 的 `.get_state`——后者只在
`pwm_device_request()`（`core.c:110-111`）和
`pwm_apply_state_debug()`（`core.c:460` / `:525`）里被调。

⇒ ⚠️ **错用会怎样**：以为 `pwm_get_state()` 读的是硬件，
用它去验证配置是否生效 ⇒ **永远"验证通过"**，因为它读的就是你写的值。

### §6.4 `pwm_put()` vs `pwm_free()` vs `devm_pwm_put()`

| 符号 | 存在吗 | 说明 |
|---|---|---|
| `pwm_put(pwm)` | ✅ `core.c:1015`，导出在 `:1037` | ✅ 用这个 |
| `pwm_free(pwm)` | ✅ `core.c:434`，导出在 `:438` | ⚠️ **deprecated**（`core.c:432`），是 `pwm_put()` 的一行别名（`core.c:434-437`） |
| `devm_pwm_put(...)` | ❌ **不存在** | `include/linux/pwm.h` 里没有这个符号；`devm_pwm_get()` 的释放由 devm 自动做 |

⚠️ **三者都不关输出**（03 §A8）。

⚠️ **重复 `pwm_put()` 的行为**：打
`pr_warn("PWM device already freed\n")`（`core.c:1023`）——
⚠️ **这条消息不含设备名**，多条 PWM 的系统里无法定位是哪一条。
好消息是它**跳过 `module_put`**，不会造成模块引用计数下溢。

### §6.5 `pwm_get()` vs `pwm_request()` vs `pwm_request_from_chip()`

| 符号 | 位置 | 谁该用 |
|---|---|---|
| `pwm_get(dev, con_id)` | `core.c:900`（导出在 `:1009`） | 消费者（无 devm 时） |
| `devm_pwm_get(dev, con_id)` | `core.c:1055`（导出在 `:1070`） | ✅ **消费者首选** |
| `devm_fwnode_pwm_get(dev, fwnode, con_id)` | `core.c:1084`（导出在 `:1104`） | 消费者，句柄在子节点上时（如 `leds-pwm-multicolor`） |
| `pwm_request(pwm_id, label)` | `core.c:369` | ⚠️ **deprecated**（`core.c:364`），靠全局编号 |
| `pwm_request_from_chip(chip, index, label)` | `core.c:406` | ⚠️ **provider 内部用**（`of_xlate` 里、sysfs `export_store()` 里），消费者不用 |

⚠️ **错用会怎样**：消费者调 `pwm_request_from_chip()`
⇒ 绕过了 device link（03 §B5）⇒ **PM 顺序没有保证**，
suspend 时可能 provider 先睡。

### §6.6 `PWM_POLARITY_INVERSED` vs `PWM_POLARITY_INVERTED`

| 拼写 | 是什么 | 位置 |
|---|---|---|
| `PWM_POLARITY_INVERSED` | **C 枚举值** | `include/linux/pwm.h:22` |
| `PWM_POLARITY_INVERTED` | **DT 宏**（值 `1 << 0`） | `include/dt-bindings/pwm/pwm.h:13` |

⚠️ **`INVERSED`（C）vs `INVERTED`（DT）——差一个字母，用途完全不同。**
好消息：写错是**编译错误**，不会静默。
⚠️ 坏消息：在 DTS 里写 `PWM_POLARITY_INVERSED` 也是编译错误，
但错误信息指向 dtc 而非源头，容易看半天。

⇒ 记法：**C 代码里 `INVERSED`，DTS 里 `INVERTED`。**

### §6.7 `pwm_config()` / `pwm_enable()` / `pwm_disable()` vs `pwm_apply_state()`

| 符号 | 位置 | 性质 |
|---|---|---|
| `pwm_config(pwm, duty_ns, period_ns)` | `include/linux/pwm.h:325` | inline 包装，内部转 `pwm_apply_state()` |
| `pwm_enable(pwm)` | `include/linux/pwm.h:351` | 同上 |
| `pwm_disable(pwm)` | `include/linux/pwm.h:370` | 同上，返回 `void` |

⚠️ **这三个不是"驱动回调"。** `pwm.rst:167-172` 的 Locking 一节和
`pwm.rst:153-157` 都在拿它们当驱动方法说事——
**那是十年前的代码，`struct pwm_ops` 在 6.1 里根本没有
`.enable`/`.disable`/`.config`**（只有 6 个成员，
`include/linux/pwm.h:272-282`，03 §C2.6）。

⇒ **它们可以用**（`pwm-regulator.c:121` / `:128` 就在用），
但**不是原子的**：`pwm_config()` 只改 duty+period，
要改极性还得再来一次 ⇒ **新代码统一用 `pwm_apply_state()`**。

### §6.8 `pwm_get_duty_cycle()` vs `pwm_get_relative_duty_cycle()`

| 符号 | 位置 | 参数 | 返回 |
|---|---|---|---|
| `pwm_get_duty_cycle(pwm)` | `include/linux/pwm.h:138` | **`pwm_device *`** | 绝对纳秒（`u64`） |
| `pwm_get_relative_duty_cycle(&state, scale)` | `include/linux/pwm.h:209` | ⚠️ **`pwm_state *`** | 相对刻度（`unsigned int`），`period == 0` 时返回 0 |

⚠️ **第一个参数类型不同**（device vs state）⇒ 混用是编译错误。
⚠️ 但**语义混淆是真陷阱**：`pwm_get_duty_cycle()` 返回纳秒，
拿它去和"百分比"比较会得到荒谬结果。

### §6.9 驱动文件名：`clk-pwm.c` vs `pwm-clk.c`

| 文件 | 方向 | 04 §  |
|---|---|---|
| `drivers/clk/clk-pwm.c` | **PWM → clk**（消费一条 PWM，产出时钟） | §3.5 |
| `drivers/pwm/pwm-clk.c` | **clk → PWM**（消费一个时钟，产出 `pwm_chip`） | §4.1 |

⚠️ 排查时看错文件会得出完全相反的结论。
记法：**文件名的前缀 = 它注册进哪个子系统。**

### §6.10 trace 事件 `pwm_get` vs 函数 `pwm_get()`

| 名字 | 含义 |
|---|---|
| 函数 `pwm_get()`（`core.c:900`） | 消费者获取一条 PWM |
| **trace 事件 `pwm_get`**（`include/trace/events/pwm.h:47`） | ⚠️ **"从硬件读到了 state"**，触发点在 `pwm_device_request()` 的 `core.c:112` 和 `pwm_apply_state_debug()` 的 `:461` / `:526` |

⚠️ **错用会怎样**：想统计"有多少驱动拿了 PWM"，
去数 `pwm_get` 事件 ⇒ **provider 没实现 `.get_state` 时计数是 0**；
开了 `CONFIG_PWM_DEBUG` 时**每次 apply 反而多出 2 个**（03 §B6.1）。

---

## §7 符号存在性与可用性核实表

判据：**存在** = 本版本 grep 到定义；**外部可用** = `EXPORT_SYMBOL*`
且在 `include/linux/pwm.h` 声明，或本身是 `pwm.h` 的 `static inline`。

| 符号 | 定义位置 | 导出 | 外部驱动可用 |
|---|---|---|---|
| `pwmchip_add` | `core.c:258` | ✅ `:312` | ✅ |
| `pwmchip_remove` | `core.c:323` | ✅ `:338` | ✅（⚠️ `void`，见 §1.4） |
| `devm_pwmchip_add` | `core.c:347` | ✅ `:357` | ✅ |
| `pwm_request_from_chip` | `core.c:406` | ✅ `:426` | ✅（provider 内部用） |
| `pwm_apply_state` | `core.c:546` | ✅ `:589` | ✅ |
| `pwm_adjust_config` | `core.c:626` | ✅ `:672` | ✅ |
| `pwm_capture` | `core.c:599` | ✅ `:616` | ✅（arm64 上恒 `-ENOSYS`） |
| `pwm_get` | `core.c:900` | ✅ `:1009` | ✅ |
| `pwm_put` | `core.c:1015` | ✅ `:1037` | ✅ |
| `devm_pwm_get` | `core.c:1055` | ✅ `:1070` | ✅ |
| `devm_fwnode_pwm_get` | `core.c:1084` | ✅ `:1104` | ✅ |
| `pwm_set_chip_data` | `core.c:213` | ✅ `:222` | ✅（provider 用） |
| `pwm_get_chip_data` | `core.c:230` | ✅ `:234` | ✅（provider 用） |
| `of_pwm_xlate_with_flags` | `core.c:124` | ✅ `:153` | ✅（默认 xlate） |
| `of_pwm_single_xlate` | `core.c:155` | ✅ `:179` | ✅（单通道 xlate） |
| `pwm_request` | `core.c:369` | ✅ `:394` | ⚠️ deprecated |
| `pwm_free` | `core.c:434` | ✅ `:438` | ⚠️ deprecated |
| `pwm_init_state` | `include/linux/pwm.h:179` | — inline | ✅ |
| `pwm_get_state` | `include/linux/pwm.h:102` | — inline | ✅ |
| `pwm_get_args` | `include/linux/pwm.h:156` | — inline | ✅ |
| `pwm_is_enabled` | `include/linux/pwm.h:108` | — inline | ✅ |
| `pwm_get_period` / `pwm_get_duty_cycle` / `pwm_get_polarity` | `include/linux/pwm.h:123` / `:138` / `:147` | — inline | ✅ |
| `pwm_set_period` / `pwm_set_duty_cycle` | `include/linux/pwm.h:117` / `:132` | — inline | ⚠️ **不要用**（§6.1） |
| `pwm_get_relative_duty_cycle` | `include/linux/pwm.h:209` | — inline | ✅ |
| `pwm_set_relative_duty_cycle` | `include/linux/pwm.h:237` | — inline | ✅ |
| `pwm_config` / `pwm_enable` / `pwm_disable` | `include/linux/pwm.h:325` / `:351` / `:370` | — inline | ⚠️ 老包装（§6.7） |
| `pwm_apply_args` | `include/linux/pwm.h:522` | — inline | ⚠️ **待删**（§6.2） |
| `pwm_add_table` / `pwm_remove_table` | `core.c:856` / `:873`（原型 `include/linux/pwm.h:583` / `:584`） | ✅ | ⚠️ arm32 板级用 |
| `pwmchip_sysfs_export` / `_unexport` | `include/linux/pwm.h:596` / `:597` | ❌ **无导出** | ❌ **仅核心用** |
| `devm_pwm_put` | — | — | ❌ **不存在** |
| `pwm_ops_check` | `core.c:236` | ❌ static | ❌ 核心内部 |

⚠️ **表里两个"❌ 不可用"是重点**：
`pwmchip_sysfs_export()` 有原型有实现但没导出（04 §1.1）；
`devm_pwm_put()` **根本不存在**——有人会凭 devm 的命名惯例去用它。

---

## §8 读下一篇

- DTS 怎么写 → [06-devicetree.md](06-devicetree.md)
- 现场排查 → [07-debug.md](07-debug.md)
- 为什么这些约定长这样 → [03-internals.md](03-internals.md) 的 Part B/C
- 结构体字段含义 → [02-datastructures.md](02-datastructures.md)

