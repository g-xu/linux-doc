---
title: 费曼学习法
---

# pwm 08：费曼式理解

这一篇不引入新事实，只把前七篇的东西重讲一遍——用"如果我要从零设计一个
PWM 框架会怎么做，以及 Linux 实际做成了什么样"的方式。
所有结论都在前面各篇有行号出处，这里只做解释。

---

## §1 用一句话说清 pwm 是什么

**pwm 子系统是一个"把两个纳秒数写进硬件"的框架。**

就这样。两个数：`period`（周期）和 `duty_cycle`（高电平时长），
加一个极性开关和一个使能开关。`struct pwm_state` 一共只有
5 个字段（`include/linux/pwm.h:59-65`）。

对比一下同级的框架：

| 子系统 | 核心抽象的状态量 |
|---|---|
| clk | 频率 + 父时钟 + 使能 + 精度 + 相位 + duty，带树形拓扑和自动重算 |
| regulator | 电压范围 + 电流限 + 模式 + 使能，带多消费者投票和约束 |
| **pwm** | **周期 + 占空比 + 极性 + 使能 + usage_power。没有拓扑、没有投票、没有约束。** |

⇒ **pwm 是这一类框架里抽象最薄的一个。** 这既是它容易上手的原因，
也是它绝大多数坑的根源：**框架没有替你做的事，全落到驱动和消费者头上**。

---

## §2 第一性原理：为什么会长成这个样子

### §2.1 硬件事实一：PWM 控制器只能做离散的选择

真实的 PWM 控制器是"一个计数器 + 一个比较器"：

```
时钟源 fclk ──> 分频器 (1/2/4/…/N) ──> 计数器 (0..PERIOD) ──┐
                                                            ├─> 比较器 ─> 引脚
                                        DUTY 寄存器 ────────┘
```

于是**能产生的周期不是连续的**，而是
`period = (div × (PERIOD + 1)) / fclk` 这个集合里的一些点。
消费者请求 25000 ns，硬件可能只能给 24800 或 25200。

**框架必须规定一个取舍方向，否则每个驱动各选各的。**
Linux 选的是：**向下取整（round down）** ——
周期和占空比都不能超过请求值。

⇒ 这就是 `CONFIG_PWM_DEBUG` 里那两条
`.apply is supposed to round down period / duty_cycle`
（`drivers/pwm/core.c:490-492` / `:506-509`，07 §4.4）的由来。

⚠️ 但这条契约**只写在那两条 warn 的字符串里**——
`Documentation/driver-api/pwm.rst` 没写，`struct pwm_ops` 的
kerneldoc 也没写（03 §B4）。**一条只存在于诊断消息里的 API 契约。**

### §2.2 硬件事实二：读回来的和写进去的不一样

因为取整，**"我请求的" ≠ "硬件实际的"**。
一个诚实的框架应该给消费者两个查询接口：请求值和实际值。

Linux 只有一个：`pwm_get_state()`，**返回的是请求值**
（`include/linux/pwm.h:102-106`，实现就是 `*state = pwm->state;`）。
官方文档自己承认了这一点：
"There is currently no way for consumers to get the actually
implemented settings"（`pwm.rst:68-72`）。

⇒ 从这一个决定，长出了后面一串现象：

- debugfs 显示的数字是请求值（07 §1.2）
- sysfs 读 `period`/`duty_cycle` 也是请求值（07 §2.3）
- `.get_state` 变成一个**几乎不被调用**的回调：只在 request 时
  调一次（`core.c:112` 附近），以及 `CONFIG_PWM_DEBUG` 下调（07 §3.3）
- 于是很多驱动干脆不实现 `.get_state`，
- 而 `pwm_adjust_config()` 依赖它（`core.c:626`），不实现就会走
  `period == 0` 分支，把 duty 算成 0 ⇒ **调压轨掉到最低压、背光全黑**
  （05 §2.2）

**一条链子：为了省一个查询接口，代价是一个可以让硬件掉压的 bug。**

### §2.3 软件事实：PWM 的消费者不需要共享

一个 GPIO 引脚上的 PWM 通道，同一时刻只服务一个用途：
背光就是背光，风扇就是风扇。**不存在两个驱动同时要用同一路 PWM
并需要仲裁的场景**（对比 regulator：一条电轨供好几个器件，
必须投票取最大电压）。

⇒ 所以 pwm 用了**最简单的独占模型**：一个 `PWMF_REQUESTED` 位，
先到先得，第二个来的拿 `-EBUSY`。没有引用计数、没有投票、
没有 `regulator_get_exclusive()` 那种双模式。

⇒ 但也因此，**sysfs export 和内核驱动是直接冲突的**（07 §2.4）：
两者抢同一个位。这不是 bug，是这个模型的必然结果。

---

## §3 三个必须一起理解的概念

初学者最容易混的三组东西。放在一起看就清楚了。

### §3.1 `args` / `state` / 硬件——三个不同的地方

```
   设备树 pwms = <&pwm 0 25000 1>
        │
        │ of_pwm_xlate_with_flags()  (core.c:124-152)
        ▼
   pwm->args   ── period=25000, polarity=INVERSED
   （"板子上这一路应该怎么用"的说明书，只有 2 个字段）
        │
        │ ⚠️ 必须消费者自己搬：pwm_init_state() / pwm_apply_args()
        ▼
   pwm->state  ── 5 个字段，"我请求的"
        │
        │ pwm_apply_state() → chip->ops->apply()
        ▼
   硬件寄存器  ── 取整后的实际值，⚠️ 没有接口读回
```

三句话记住：

- **`args` 是设备树说的**，只有 `period` 和 `polarity`（2 个字段），
  **从不自动生效**——`struct pwm_args` 的 kerneldoc 自己就这么写
  （`include/linux/pwm.h:34-36`，06 §4）。
- **`state` 是消费者请求的**，5 个字段，`pwm_get_state()` 读它。
- **硬件是取整后的**，只有开 `CONFIG_PWM_DEBUG` 才能间接看到（07 §4）。

⇒ 「DTS 里写了 `PWM_POLARITY_INVERTED`，波形却不是反的」
这个经典症状，就是**消费者没把 `args` 搬进 `state`**
——通常是它自己 `memset` 了一个 `pwm_state` 然后填字段（06 §4）。

### §3.2 三个"编号"

| 编号 | 范围 | 谁分配 | 出现在哪 |
|---|---|---|---|
| **`hwpwm`** | `0 .. npwm-1` | 硬件决定 | DTS 的第一个 cell、debugfs 的 `pwm-N`、sysfs 的 `pwmX` 目录 |
| **`chip->base`** | 全局，`0 .. 1023` | 核心从 `allocated_pwms` 位图里分（03 §B2） | **只出现在 sysfs 的 `pwmchipN`** |
| **`pwm_device *` 指针** | — | `kzalloc` | **只出现在 tracepoint**（07 §3.1） |

⚠️ 三套编号**互相之间没有任何一个地方给出映射**：

- debugfs 打了 chip 的 `dev_name` 但不打 `base`（07 §1.1）
- sysfs 打了 `base` 但要靠 `device` 符号链接才能对上 `dev_name`
- tracepoint 只有指针，两边都对不上

⇒ 所以多控制器板子上排查问题，第一步永远是**手工建这张对照表**：

```bash
cat /sys/kernel/debug/pwm                      # dev_name ↔ hwpwm ↔ label
for c in /sys/class/pwm/pwmchip*; do           # base ↔ dev_name
    echo "$(basename $c) = $(basename $(readlink -f $c/device))"
done
```

### §3.3 三个叫 `pwm_get` 的东西

| 名字 | 是什么 |
|---|---|
| `pwm_get()`（函数，`core.c:900`） | 消费者申请通道 |
| `pwm_get_state()`（inline，`include/linux/pwm.h:102`） | 读软件缓存 |
| `pwm_get`（tracepoint，`include/trace/events/pwm.h:47`） | **"刚调过 `.get_state`"**，即读了硬件 |

⚠️ **第一个和第三个毫无关系**（07 §3.2）。
想追"谁申请了通道"用 tracepoint 是白费功夫——
`pwm_get` 事件在稳态系统里是静默的，一刷就说明有人在反复
`pwm_get()`/`pwm_put()`，但事件本身不是那个函数打的。

---

## §4 一次 `pwm_apply_state()` 的完整故事

用一个具体场景走一遍：背光从 50% 调到 60%。

```
pwm_backlight 驱动：
  pwm_init_state(pwm, &state);        ← 从 args 拿 period + polarity
  state.duty_cycle = 15000;           ← 60% of 25000
  state.enabled = true;
  pwm_apply_state(pwm, &state);
        │
        ▼
core.c:558   might_sleep()             ⚠️ 不能在原子上下文调
core.c:560   参数检查：period 非 0、duty ≤ period → 否则 -EINVAL
core.c:566   ┌── 五字段全等？ ──── 是 ──→ return 0   ⚠️⚠️ 驱动完全没被调用
             └── 否
core.c:573   chip->ops->apply(chip, pwm, state)
             │   驱动内部：算分频、算重载值、向下取整、写寄存器
             └── 返回错误 → 直接 return，⚠️ 不发 tracepoint、不更新 state
core.c:577   trace_pwm_apply(pwm, state)
core.c:579   pwm->state = *state       ← 缓存"请求值"，不是硬件值
core.c:585   pwm_apply_state_debug()   ← 只在 CONFIG_PWM_DEBUG 下有内容
```

四个反直觉点，每一个都在实战里咬过人：

**⚠️ 1. 没有锁。** `pwm_apply_state()` 不取 `pwm_lock`
（03 §B1）。并发对同一通道 apply 的互斥**完全由消费者负责**。
官方文档的 Locking 一节自陈"should be fixed soon"（`pwm.rst:165-172`）
——这句话在 6.1 里还在。

**⚠️ 2. 短路会吃掉整次调用。** 五个字段全等就 `return 0`，
`.apply` 一次都不调（`core.c:566-571`）。这在两个场合出事：

- 驱动想靠"重新 apply 同一状态"来从硬件异常里恢复 ⇒ 做不到
- resume 之后硬件已经复位，但 `pwm->state` 还是 suspend 前的值
  ⇒ apply 同样的值 = 短路 = 硬件保持复位状态
  （这也是 `sysfs.c:412` 那个 `pwm_class_resume_npwm` 存在的原因）

**⚠️ 3. `might_sleep()` 意味着不能在中断里用。**
`core.c:558`。所以 PWM 不能做"在 timer 回调里改占空比"这种事，
需要延后到工作队列。

**⚠️ 4. 开 `CONFIG_PWM_DEBUG` 会把这个流程变成 apply→read→apply→read**
（07 §4.3 第 5 步真的又写了一次硬件）
⇒ **时序敏感的场景（红外发码、步进马达）开了它就坏**。

---

## §5 为什么这个框架的坑集中在"沉默"

把前七篇里所有的失败模式排一遍，会看到一个共同点：**大部分不报错**。

| 失败 | 有日志吗 | 出处 |
|---|---|---|
| DTS 的 `#pwm-cells` 数量不对 | ⚠️ 无 | 06 §7 |
| 通道号 ≥ `npwm` | ⚠️ 无 | 06 §1.4 |
| 消费者传了 con_id 但 DTS 没有 `pwm-names` | ⚠️ 无（`-EINVAL`） | 06 §2.2 |
| 驱动设了 `of_xlate` 忘了 `of_pwm_n_cells` | ⚠️ 无（每次 `-EINVAL`） | 06 §1.3 |
| `pwms` 属性本身语法错 | ✅ 有（`core.c:756`） | 06 §7 |
| PWM chip 找不到 | ⚠️ 有代码但**是死代码**（`core.c:763`） | 06 §2.4 |
| pinctrl 没配 ⇒ 引脚不输出 | ⚠️ 无，apply 返回 0 | 06 §1.1 |
| 消费者没搬 `args` ⇒ 极性丢了 | ⚠️ 无 | 06 §4 |
| 短路吃掉了 apply | ⚠️ 无（连 tracepoint 都没有） | 07 §3.3 |
| 驱动向上取整了 | 仅 `CONFIG_PWM_DEBUG` | 07 §4.4 |
| 驱动没实现 `.get_state` | 仅 `CONFIG_PWM_DEBUG`，⚠️ 而且提示本身也被这个开关关着 | 07 §4.2 |
| `.apply` 不幂等 | 仅 `CONFIG_PWM_DEBUG` | 07 §4.4 |
| unbind provider 时通道仍被持有 | ⚠️ 无检查，直接 `kfree` ⇒ **UAF** | 03 §B3 |

核心一共只有 15 条日志（07 §5），其中 8 条锁在 `CONFIG_PWM_DEBUG` 里，
1 条是死代码，1 条（`core.c:1023`）连设备名都不带。
**核心里一条 `dev_dbg` 都没有** ⇒ `dynamic_debug` 完全无效（07 §5）。

⇒ **理解 pwm 的调试策略，本质上是接受"日志不会帮你"**。
可用的手段按有效性排序：

1. `cat /sys/kernel/debug/pwm` —— 先确认拓扑和 label（有没有人 request）
2. `echo 1 > events/pwm/pwm_apply/enable` + stacktrace ——
   看 apply 有没有真的发生、是谁发起的
3. `cat /sys/kernel/debug/pinctrl/*/pinmux-pins` —— 排掉引脚问题
4. 重编内核开 `CONFIG_PWM_DEBUG` —— 最后一招，但也是唯一能验证驱动的一招

---

## §6 三个"看起来像 bug，其实是设计"的地方

### §6.1 `pwmchip_remove()` 返回 `void` 但 kerneldoc 说会返回错误

kerneldoc（`core.c:318-321`）写"may return busy"和
"Returns: 0 on success or a negative error code"，
函数原型（`:323`）却是 `void`，而且**根本不检查 `PWMF_REQUESTED`**。

这不是笔误，是一次 API 变更留下的**陈旧注释**：
早期版本真的返回 `-EBUSY`，后来因为 `platform_driver.remove`
的返回值没人看，改成了 `void`，注释忘了改。

⚠️ 结果是真的：**unbind 一个还在被消费者用的 PWM 控制器，
`chip->pwms` 会被 `kfree`，消费者手上的 `pwm_device *` 立刻悬空**
⇒ 下一次 `pwm_apply_state()` 就是 UAF（03 §B3）。

⚠️ 而且 `CONFIG_PWM=n` 时的桩函数（`include/linux/pwm.h:476-479`）
返回 `int` / `-EINVAL`，和 `CONFIG_PWM=y` 的 `void` 原型
（`:392`）**签名不一致** ⇒ 同一份消费者代码在两种配置下编译结果不同。

### §6.2 `-ENOSYS` 泄漏到用户态

`pwm_capture()` 在驱动没实现 `.capture` 时返回 `-ENOSYS`
（`core.c:608`，全树唯一一处这样用它）。
内核约定 `-ENOSYS` 表示"系统调用不存在"，
所以 `cat /sys/class/pwm/pwmchip0/pwm0/capture` 报的是
`Function not implemented`，看着像 syscall 出了问题（07 §2.4）。

正常应该是 `-EOPNOTSUPP`。但**改了就是 ABI 变更**，
所以只能留着。

⇒ arm64 上这个属性**必然**返回这个错误：全树只有
`pwm-sti` / `pwm-stm32` 实现 `.capture`，两者在 arm64 DTS 里零命中（04 §5）。

### §6.3 `pwm_set_period()` / `pwm_set_duty_cycle()` 不碰硬件

`include/linux/pwm.h:117` 和 `:132` 这两个 inline 直接改
`pwm->state.period` / `.duty_cycle`，**不调 `.apply`**。

名字看着像"设置周期"，实际是"污染缓存"。
⚠️ 更糟的是：**改完之后再 `pwm_apply_state()` 传一个真实值，
短路可能命中**（因为缓存已经被改成了目标值）
⇒ 硬件永远不会被更新。

这两个函数的正当用途只有一个：驱动在 `.request` 或
`.get_state` 里同步"我发现硬件当前就是这个值"。
**消费者代码里出现它们基本都是 bug**（05 §6.1）。

---

## §7 如果重新设计会怎么改

不是抱怨，是用来检验理解的：能说出该改什么，说明真的懂了这个框架的约束。

| 现状 | 会怎么改 | 为什么现在改不了 |
|---|---|---|
| `pwm_get_state()` 只给请求值 | 拆成 `pwm_get_requested_state()` + `pwm_get_hw_state()` | 后者需要所有驱动实现 `.get_state`，60+ 个驱动的工作量 |
| 五字段短路无条件生效 | 给一个 `PWM_APPLY_FORCE` 标志 | `pwm_state` 加字段会破坏短路比较的语义 |
| `pwmchip_remove()` 不检查占用 | 加 `PWMF_REQUESTED` 检查 + 返回 `int` | 40+ 个 `.remove` 回调要改 |
| 三套编号无映射 | debugfs chip 行加打 `base` | 会破坏解析 debugfs 的现有脚本 |
| round-down 契约只在 warn 字符串里 | 写进 `struct pwm_ops` 的 kerneldoc | 纯文档工作，**这条随时可以做** |
| `-ENOSYS` | 换成 `-EOPNOTSUPP` | ABI |
| 无锁 | 每通道一把 mutex | 官方文档说"should be fixed soon"（`pwm.rst:165-172`），说了很多年 |

⚠️ 注意上游 6.2+ 已经动了一部分：`chip->base` 和全局位图在后续版本被移除，
`pwmchip_remove()` 也做了调整。**但本文档描述的是 6.1，
上面的现象在这棵树上全部成立。**

---

## §8 一页速记

```
状态量        period + duty_cycle + polarity + enabled + usage_power (5 个)
三个地方      args（DT 说的，2 字段）→ state（请求的，5 字段）→ 硬件（取整后，读不到）
搬运责任      args → state 必须消费者自己做（pwm_init_state / pwm_apply_args）
契约          .apply 必须向下取整；只写在 CONFIG_PWM_DEBUG 的 warn 里
短路          五字段全等 → return 0，.apply 不被调，无日志无 trace
锁            pwm_lock 保护注册/request/put/capture/get_state/debugfs
              ⚠️ pwm_apply_state() 完全无锁
编号          hwpwm（DTS/debugfs/pwmX）· base（只在 pwmchipN）· 指针（只在 trace）
观测          debugfs 全树快照（软件值，持锁）
              sysfs 单通道（export 有副作用）
              2 个 tracepoint（只有裸指针，pwm_get ≠ pwm_get()）
              CONFIG_PWM_DEBUG（编译期，会额外 apply 一次）
最大的雷       ① 缺 .get_state + pwm_adjust_config() ⇒ 电轨/背光掉到最低
              ② unbind provider ⇒ UAF
              ③ 短路吃掉 resume 后的第一次 apply
```

---

## §9 接着读

- 每个 API 的完整签名和约束 → [01-api.md](01-api.md)
- 5 个结构体的字段逐个解释 → [02-datastructures.md](02-datastructures.md)
- 短路 / 锁 / 编号分配的源码级追踪 → [03-internals.md](03-internals.md)
- 写驱动和写消费者的具体做法 → [05-usage.md](05-usage.md)
- 上面提到的每条症状怎么查 → [07-debug.md](07-debug.md)
