---
title: 调试方法
---

# pwm 07：调试与观测

## §0 pwm 一共只有四个观测面

| 面 | 入口 | 编译开关 | 能看到什么 | 会不会改变行为 |
|---|---|---|---|---|
| **debugfs** | `/sys/kernel/debug/pwm` | `CONFIG_DEBUG_FS` | 全树一次性快照：每个 chip 的每个通道 | 只读，但⚠️**持 `pwm_lock`** |
| **sysfs** | `/sys/class/pwm/pwmchipN/` | `CONFIG_PWM_SYSFS`（`default y if SYSFS`） | 单通道的 5 个属性 | ⚠️ **export 会真的 request 通道** |
| **tracepoint** | `events/pwm/pwm_apply`、`events/pwm/pwm_get` | `CONFIG_TRACEPOINTS` | 每次 apply / 每次读硬件 | 只观测 |
| **`CONFIG_PWM_DEBUG`** | dmesg | `CONFIG_PWM_DEBUG`（`depends on DEBUG_KERNEL`） | 7 条驱动契约违反的诊断 | ⚠️⚠️ **会额外 apply 一次，改变时序** |

⚠️ **四个面里有三个会影响被观测对象**。pwm 没有"纯旁观"的调试手段，
除了 tracepoint。这一点在排查间歇性问题时很关键。

### §0.1 先确认这些面存在

```bash
# debugfs
[ -e /sys/kernel/debug/pwm ] && echo "debugfs OK"
# sysfs
ls /sys/class/pwm/                       # 空目录 = 没有任何 provider 注册成功
# tracepoint
ls /sys/kernel/tracing/events/pwm/       # 应有 pwm_apply/ 和 pwm_get/
# PWM_DEBUG（内核编译期决定，运行时不可开）
zcat /proc/config.gz 2>/dev/null | grep PWM_DEBUG
```

⚠️ **`CONFIG_PWM_DEBUG` 是编译期开关**（`drivers/pwm/Kconfig:36-43`），
没有 module param、没有 debugfs 开关。**线上内核基本都是关的**
⇒ 需要它就得重新编译。Kconfig 自己也这么说
（`:42-43`："expected to introduce some runtime overhead …
so only enable while working on a driver"）。

---

## §1 debugfs：`/sys/kernel/debug/pwm`

### §1.1 只有一个文件，一次给出全树

不是每个 chip 一个目录，而是**一个全局文件**
（`core.c:1182-1183`，`subsys_initcall(pwm_debugfs_init)` 在 `:1187`）。
它是 `seq_file`（`DEFINE_SEQ_ATTRIBUTE(pwm_debugfs)` 在 `:1178`），
遍历全局 `pwm_chips` 链表。

典型输出：

```
platform/ff420000.pwm, 1 PWM device
 pwm-0   (backlight           ): requested enabled period: 25000 ns duty: 12500 ns polarity: normal

platform/ff420020.pwm, 1 PWM device
 pwm-0   (                    ): period: 0 ns duty: 0 ns polarity: normal
```

**chip 行**（`core.c:1161-1164`）的四段：

| 段 | 来源 | 说明 |
|---|---|---|
| `platform` | `chip->dev->bus->name`，没有总线时打 `no-bus` | `:1162` |
| `ff420000.pwm` | `dev_name(chip->dev)` | `:1163` |
| `1 PWM device` | `chip->npwm` + 复数 `s` | `:1163-1164` |
| chip 之间的空行 | `s->private`，第一个 chip 是 `""`，之后是 `"\n"`（`:1140` / `:1147`） | 分组用 |

⚠️ **chip 行里没有 `chip->base`**，所以 **debugfs 看不出这个 chip 对应
`/sys/class/pwm/` 里的哪个 `pwmchipN`**。要对上得看 sysfs 那边的
`device` 符号链接（§2.1）。

**通道行**（`pwm_dbg_show()`，`core.c:1107-1135`）：

| 字段 | 行 | 含义 |
|---|---|---|
| `pwm-%-3d` | `:1117` | **chip 内序号 `i`（hwpwm），不是全局号** |
| `(%-20.20s)` | `:1117` | `pwm->label`，⚠️ **20 字符硬截断** |
| `requested` | `:1119-1120` | `PWMF_REQUESTED` 置位，即有人 `pwm_get()` 过 |
| `enabled` | `:1122-1123` | `state.enabled` |
| `period: %llu ns` | `:1125` | |
| `duty: %llu ns` | `:1126` | |
| `polarity: normal\|inverse` | `:1127-1128` | ⚠️ 打的是 `inverse`，不是 `inversed`/`inverted` |
| `usage_power` | `:1130-1131` | ⚠️ **仅当为 true 才打印** |

### §1.2 debugfs 会骗你的三件事

⚠️ **1. 数字是软件缓存，不是硬件。**
`pwm_dbg_show()` 用的是 `pwm_get_state(pwm, &state)`（`core.c:1115`），
而那个 inline 的全部实现就是 `*state = pwm->state;`
（`include/linux/pwm.h:102-106`）⇒ **它复读的是最后一次
`pwm_apply_state()` 成功时缓存下来的值**（`core.c:579`），
**不调 `.get_state`、不碰寄存器**。

⇒ 三种情况下 debugfs 与实际波形不符：

| 情况 | debugfs 显示 | 实际 |
|---|---|---|
| bootloader 已经在输出，内核还没 apply | 全 0 | 有波形 |
| 驱动向下取整了（如请求 25000ns 实得 24000ns） | **25000**（请求值） | 24000 |
| 有人绕过框架直接写寄存器 | 旧值 | 新值 |

⇒ **要硬件真值只有两条路**：开 `CONFIG_PWM_DEBUG`（§4，它会真读）
或者直接 dump 控制器寄存器。

⚠️ **2. label 在 20 字符处被截断**（`:1117` 的 `%-20.20s`）。
label 来自 DT 的三层 fallback（06 §2.3），常常是 consumer 的节点名。
节点名超过 20 字符时两个不同通道会显示成一样的前缀。
对不上就去 sysfs 或 DT 里查全名。

⚠️ **3. 读这个文件会持 `pwm_lock` 整个遍历过程。**
`pwm_seq_start()` 在 `:1139` 加锁，`pwm_seq_stop()` 在 `:1154` 解锁
——**中间跨越所有 chip 的所有通道**。因为 `pwm_lock` 也保护
`pwm_get()` / `pwm_put()` / `pwmchip_add()`（03 §B1），所以：

- `cat /sys/kernel/debug/pwm` 期间，**任何驱动的 probe 里的
  `pwm_get()` 都会阻塞**；
- 反过来，某个驱动的 `.request` 回调卡住时（它是持锁调的，03 §B1.1），
  **`cat` 也会挂住**——这本身就是一个有用的诊断信号：
  `cat /sys/kernel/debug/pwm` 挂死 ⇒ 有 provider 的回调卡在锁里。

### §1.3 `usage_power` 唯一的可见处

`state.usage_power`（`struct pwm_state` 的第 5 个字段）
**在整个内核里只有 debugfs 这一处可观测**（`core.c:1130-1131`），
tracepoint 不带它（§3.4），sysfs 没有对应属性。
而且树内**没有任何消费者设置过它**（04 §5）⇒
**看到 `usage_power` 出现在输出里，说明有人手写了这个字段**，值得追一下。

---

## §2 sysfs：`/sys/class/pwm/`

`drivers/pwm/sysfs.c`（546 行）建立的类，`class_register(&pwm_class)`
在 `:544`，`pwm_class` 定义在 `:493`。

### §2.1 目录结构与编号陷阱

```
/sys/class/pwm/
├── pwmchip0 -> ../../devices/platform/ff420000.pwm/pwm/pwmchip0
│   ├── device -> ../../../ff420000.pwm      ← ⚠️ 唯一能对上物理控制器的线索
│   ├── npwm                                 ← 只读，通道总数
│   ├── export                               ← 只写
│   ├── unexport                             ← 只写
│   └── pwm0/                                ← export 之后才出现
└── pwmchip4
```

⚠️⚠️ **`pwmchipN` 的 N 是全局 `base`，不是 chip 序号**
（`sysfs.c:513-514` 的 `"pwmchip%d", chip->base`）。
`base` 由 `alloc_pwms()` 从全局位图里分配（03 §B2），
所以第二个注册的 4 通道 chip 会是 `pwmchip4` 而不是 `pwmchip1`
⇒ **编号会跳，而且和 probe 顺序绑定，不稳定**。

⚠️ 与之相对，**通道目录 `pwmX` 里的 X 是 `hwpwm`**（`sysfs.c:263`），
从 0 开始。**同一条路径上两套编号口径**（03 §A9）。

⇒ 脚本里**不要硬编码 `pwmchip0`**。稳妥写法：

```bash
# 按物理地址找 chip
for c in /sys/class/pwm/pwmchip*; do
    echo "$c -> $(basename $(readlink -f $c/device))  npwm=$(cat $c/npwm)"
done
```

### §2.2 不 export 也能看的只有 `npwm`

`npwm_show()`（`sysfs.c:359-365`，`DEVICE_ATTR_RO(npwm)` 在 `:366`）
是 chip 级唯一的只读属性。**其余观测都必须先 export**，
而 export 有副作用（§2.4）。

### §2.3 export 之后的五个属性

`pwm_attrs[]`（`sysfs.c:224-231`）：

| 属性 | 权限 | show/store | 观测语义 |
|---|---|---|---|
| `period` | RW | `:36` / `:48` | ⚠️ 读的是**软件缓存**（`pwm_get_state`），同 §1.2 |
| `duty_cycle` | RW | `:71` / `:83` | 同上 |
| `enable` | RW | `:106` / `:118` | 同上 |
| `polarity` | RW | `:154` / `:177` | 字符串 `normal` / `inversed` |
| `capture` | **RO** | `:203` | ⚠️ **读一次触发一次真实采样**（§2.4） |

⚠️ **`polarity` 的字符串和 debugfs 不一样**：sysfs 用 `inversed`，
debugfs 打 `inverse`（`core.c:1128`）。写脚本别混用。

⚠️ **`capture` 这个属性官方文档里没有。**
`Documentation/driver-api/pwm.rst:110-129` 只列了 4 个属性
（03 §C2.5）⇒ 照文档写的工具会漏掉它。

### §2.4 sysfs 观测的两个副作用

⚠️⚠️ **1. `echo 0 > export` 会真的占用通道。**
`export_store()`（`sysfs.c:310-335`）先调
`pwm_request_from_chip(chip, hwpwm, "sysfs")`（`:326`），
成功后才建 `pwmX` 目录（`:330`）。所以：

- 通道已被内核驱动持有 ⇒ export 返回 **`-EBUSY`**；
- export 成功之后，**该通道对内核驱动就是占用的**
  ⇒ 之后 probe 的消费者会拿到 `-EBUSY`。

⚠️ **两个不同的 `-EBUSY` 用同一个 errno**：`sysfs.c:326` 那次
`pwm_request_from_chip()` 失败（通道被内核驱动占用）和
`sysfs.c:248` 的 `test_and_set_bit(PWMF_EXPORTED, …)`（同一个通道
已经 export 过）。从 errno 分不开，只能看 `pwmX` 目录在不在（03 §A9）。

⚠️ 另外 `sysfs.c:323-324` 用 **`-ENODEV`** 表示"通道号 ≥ `npwm`"
——所以 `echo 99 > export` 报的是 `No such device`，
而不是想当然的 `-EINVAL`。先 `cat npwm` 确认上界。

⚠️⚠️ **2. `cat capture` 会真的做一次捕获，并且可能阻塞。**
`capture_show()`（`sysfs.c:203-217`）调 `pwm_capture()`，
后者持 `pwm_lock` 调驱动的 `.capture`（03 §B1.1），
带超时等待。**在 arm64 上它必然返回 `-ENOSYS`**
（唯一两个实现 `.capture` 的驱动是 `pwm-sti` / `pwm-stm32`，
arm64 DTS 零命中，04 §5）⇒ 现象是
`cat: capture: Function not implemented`。

⚠️ 那句 `Function not implemented` 是 `-ENOSYS` 的 strerror。
`-ENOSYS` 在内核里的约定含义是"系统调用不存在"，
`core.c:608` 是**全树唯一一处**这样用它的地方（03 §C2.3）
⇒ 看到这条报错**不是**你的 syscall 有问题，就是"这个控制器没有捕获功能"。

### §2.5 一条能直接抄的巡检脚本

```bash
#!/bin/sh
# 只读巡检：不 export，不写任何东西
echo "== debugfs 全树 =="
cat /sys/kernel/debug/pwm 2>/dev/null || echo "(无 CONFIG_DEBUG_FS)"
echo
echo "== sysfs chip 清单 =="
for c in /sys/class/pwm/pwmchip*; do
    [ -e "$c" ] || { echo "(没有任何 pwmchip)"; break; }
    printf '%s  dev=%s  npwm=%s  已export=' \
        "$(basename $c)" \
        "$(basename $(readlink -f $c/device))" \
        "$(cat $c/npwm)"
    ls -d $c/pwm[0-9]* 2>/dev/null | xargs -r -n1 basename | tr '\n' ' '
    echo
done
```

---

## §3 tracepoint：唯一的纯旁观手段

### §3.1 两个事件共用一个 class

`include/trace/events/pwm.h`（58 行）只有一个
`DECLARE_EVENT_CLASS(pwm)`（`:11-37`）和两个
`DEFINE_EVENT`：`pwm_apply`（`:39-45`）和 `pwm_get`（`:47-53`）。

字段（`TP_STRUCT__entry`，`:17-23`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pwm` | `struct pwm_device *` | ⚠️ **裸指针**，不带 chip 名、不带 hwpwm |
| `period` | `u64` | ns |
| `duty_cycle` | `u64` | ns |
| `polarity` | `enum pwm_polarity` | 打成 `%d`（0/1） |
| `enabled` | `bool` | |

输出格式（`TP_printk`，`:33-35`）：

```
pwm_apply: ffff000001234567: period=25000 duty_cycle=12500 polarity=0 enabled=1
```

⚠️ **只有一个裸指针来标识通道**，没有 `pwmchipN`、没有 label。
多控制器系统里要先建立"指针 → 通道"的对照表：
先 `cat /sys/kernel/debug/pwm` 记下拓扑，再靠**指针的稳定性**分组
（`pwm_device` 生命周期内地址不变，见 03 §B3）。
⚠️ 但 `pwmchip_remove()` 会 `kfree(chip->pwms)`，
**同一个地址可能被下一个 chip 复用** ⇒ 跨 unbind/bind 的 trace 不能靠指针配对。

### §3.2 ⚠️ `pwm_get` 事件和 `pwm_get()` 函数毫无关系

这是 pwm 观测面最大的一个命名坑：

| | 含义 |
|---|---|
| **事件 `pwm_get`** | "**刚从硬件读了一次状态**"，即 `.get_state` 被调用过 |
| **函数 `pwm_get()`**（`core.c:900`） | 消费者申请一个 PWM 通道 |

⇒ **`pwm_get` 事件的数量和 `pwm_get()` 的调用次数不相等**，
开了 `CONFIG_PWM_DEBUG` 之后差得更远（每次 apply 都产生 2 个 `pwm_get`）。
想追"谁申请了通道"要用 `debugfs` 的 `requested` 标志或 kprobe，
**不能用这个事件**。

### §3.3 五个触发点

| 事件 | 触发行 | 上下文 | 有没有 `CONFIG_PWM_DEBUG` 前提 |
|---|---|---|---|
| `pwm_get` | `core.c:112` | `pwm_device_request()` 里读初始状态 | ❌ 无（总是打） |
| `pwm_apply` | `core.c:577` | `pwm_apply_state()` 成功调完 `.apply` | ❌ 无 |
| `pwm_get` | `core.c:461` | `pwm_apply_state_debug()` 第一次回读 | ✅ 需要 |
| `pwm_apply` | `core.c:523` | `pwm_apply_state_debug()` 的**重放** | ✅ 需要 |
| `pwm_get` | `core.c:526` | 重放后的回读 | ✅ 需要 |

三条推论：

1. ⚠️ **`pwm_apply` 只在 `.apply` 成功后才打**（`:573-577`）
   ⇒ **`.apply` 返回错误时没有任何事件**，trace 里看不到失败的尝试。
2. ⚠️⚠️ **短路命中时也没有事件。** `pwm_apply_state()` 的五字段全等短路
   在 `:566-571` **直接 `return 0`**，位置在 `trace_pwm_apply` 之前
   ⇒ **"apply 调用了但 trace 里没有" 的最常见原因就是短路**（03 §A5）。
   这也是排查"我明明 apply 了、波形没变"的第一判据。
3. **`pwm_get` 事件在没开 `PWM_DEBUG` 时只在 request 时出现一次**
   ⇒ 稳态运行的系统里这个事件应该是**静默的**；一直在刷说明有人
   反复 `pwm_get()`/`pwm_put()`。

### §3.4 `usage_power` 不在 trace 里

`TP_STRUCT__entry` 只有 5 个字段（`:17-23`），
**`state->usage_power` 没被记录**——但它**参与短路比较**
（`core.c:570`）。

⇒ ⚠️ **只改 `usage_power` 的一次 apply**：
短路不命中（`:570` 不等）→ `.apply` 被真调用 → `pwm_apply` 事件触发，
但**事件里五个字段和上一条完全一样**。
现象是"trace 里出现了两条一模一样的 `pwm_apply`"。
唯一能确认的地方是 debugfs（§1.3）。

### §3.5 实操

```bash
cd /sys/kernel/tracing

# 只看 apply
echo 1 > events/pwm/pwm_apply/enable
cat trace_pipe

# 两个都看，加上调用栈找出是谁 apply 的（最有用的一招）
echo 1 > events/pwm/enable
echo 1 > options/stacktrace
cat trace_pipe

# 只看某一个通道（先从 debugfs 确定它的指针值不可行——
# 指针只能从 trace 自己的输出里读，所以用 filter 反过来筛）
echo 'period == 25000' > events/pwm/pwm_apply/filter

# 关掉
echo 0 > events/pwm/enable
echo > events/pwm/pwm_apply/filter
```

⚠️ **没有办法按 `pwmchipN` 或 label 过滤**，因为事件里没有这些字段。
只能按 `period` / `duty_cycle` / `enabled` 筛，或者事后按指针分组。

---

## §4 `CONFIG_PWM_DEBUG`：唯一能验证驱动的手段

### §4.1 它是什么

```
config PWM_DEBUG
	bool "PWM lowlevel drivers additional checks and debug messages"
	depends on DEBUG_KERNEL
```
（`drivers/pwm/Kconfig:36-38`）

打开后 `pwm_apply_state_debug()`（`core.c:440-539`，整 100 行）
从空函数变成真检查。**它是内核里唯一表达"`.apply` 必须向下取整"
这条契约的地方**（03 §B4）——文档里没有，kerneldoc 里也没有。

### §4.2 两个前置条件

```
:448-449   if (!IS_ENABLED(CONFIG_PWM_DEBUG)) return;
:451-453   if (!chip->ops->get_state) return;    ← ⚠️ 没有 .get_state 就完全放弃诊断
```

⚠️⚠️ **驱动不实现 `.get_state`，`CONFIG_PWM_DEBUG` 等于没开。**
唯一的提示是注册时的一条
`dev_warn(chip->dev, "Please implement the .get_state() callback\n")`
（`core.c:244-245`）——**而这条 warn 本身也只在 `CONFIG_PWM_DEBUG`
下才打**（`:243`）⇒ 关着开关就永远看不到提醒。

⇒ **调试新驱动的第一步是先把 `.get_state` 写出来**，
否则整套诊断链条都是空转（05 §1.3）。

### §4.3 九步流程

`pwm_apply_state_debug()` 在 `pwm_apply_state()` 里的调用点是
`:585`，即 **`.apply` 成功、`pwm->state` 已更新之后**
（`:581-584` 的注释说明了为什么必须在之后：有些 `.get_state`
依赖 `pwm->state`）。

```
1. :460     .get_state → s1           读出驱动认为的当前硬件状态
2. :461     trace_pwm_get(s1)
3. :468-475 极性反演归一化 → s2       ⚠️ 驱动"尽力反相"时把 duty 折回来
4. :477-513 六条检查，逐条 dev_warn
5. :516     .apply(&s1)              ⚠️⚠️ 把刚读出来的状态原样再 apply 一遍
6. :517-521 失败 → dev_err + 记 last，收工
7. :523     trace_pwm_apply(s1)
8. :525-526 .get_state → last，trace_pwm_get(last)
9. :529-538 s1 与 last 逐字段比 → 不等就 dev_err（幂等性）
```

⚠️⚠️ **第 5 步是真的又写了一次硬件。** 所以开着这个开关时：

- 每次 `pwm_apply_state()` 变成 **apply → read → apply → read**；
- 时序完全变了 ⇒ ⚠️ **对时序敏感的场景（`pwm-ir-tx` 发红外码、
  马达驱动）开了它就会失灵**；
- trace 里每次 apply 出现 **2 条 `pwm_apply` + 2 条 `pwm_get`**。

### §4.4 七条诊断消息全表

按出现顺序。dmesg 里 grep 这些原文：

| # | 行 | 级别 | 原文（可直接 grep） | 含义 | 该改哪 |
|---|---|---|---|---|---|
| 1 | `:479` | warn | `.apply ignored .polarity` | 请求了反相，硬件回读还是正相 | `.apply` 里要么支持反相，要么返回 `-EINVAL`（05 §1.2） |
| 2 | `:485-487` | warn | `.apply didn't pick the best available period (requested: … applied: … possible: …)` | 有更接近请求值的周期没被选 | 分频/重载值的选择算法 |
| 3 | `:490-492` | warn | `.apply is supposed to round down period (requested: … applied: …)` | ⚠️ **向上取整了**，违反核心契约 | 改成向下取整 |
| 4 | `:499-503` | warn | `.apply didn't pick the best available duty cycle (requested: …)` | duty 有更好的可选值 | duty 寄存器计算 |
| 5 | `:506-509` | warn | `.apply is supposed to round down duty_cycle (requested: … applied: …)` | ⚠️ **duty 向上取整了** | 同上 |
| 6 | `:512-513` | warn | `requested disabled, but yielded enabled with duty > 0` | 请求关闭却没真关 | `.apply` 的 `!state->enabled` 分支要真停输出 |
| 7 | `:519` | **err** | `failed to reapply current setting` | 把自己刚报告的状态再 apply 一次都失败了 | `.get_state` 报告了一个 `.apply` 拒绝的状态 ⇒ 两个回调不自洽 |
| 8 | `:533-537` | **err** | `.apply is not idempotent (ena=… pol=… …) -> (ena=… …)` | 同一状态 apply 两次结果不同 | ⚠️ **最严重的一条**，通常是寄存器读改写没做好，或有硬件影子寄存器 |

（表里 8 行是因为 `:519` 和 `:533` 都是 `dev_err`，
"7 条 warn/err"的常见说法漏掉了 `:519` 这条。）

⚠️ **第 2、4 条依赖 `pwm->last`**——那是**上一次诊断跑完时记下的
硬件真值**（`:525`）。所以**第一次 apply 时 `last` 还是零值，
第 2/4 条不会触发**。要看到它们至少得 apply 两次。

### §4.5 怎么用

```bash
# 需要重编内核：CONFIG_DEBUG_KERNEL=y + CONFIG_PWM_DEBUG=y
dmesg -w | grep -E '\.apply|get_state'    # 一边操作一边看

# 典型的"新驱动第一次跑"序列
echo 0 > /sys/class/pwm/pwmchip0/export
cd /sys/class/pwm/pwmchip0/pwm0
echo 25000 > period ; echo 12500 > duty_cycle ; echo 1 > enable
echo 12500 > duty_cycle     # ⚠️ 故意重复一次：验证幂等性和短路
echo 0 > enable
```

⚠️ 第二次写同一个 `duty_cycle` 时，**如果核心的短路生效
（五字段全等，`core.c:566-571`）就什么都不会发生**——
`.apply` 不被调用，诊断也不跑。要验证幂等性得**改一个字段再改回来**。

---

## §5 核心自己的日志（全普查）

`grep -nE 'dev_warn|dev_err|pr_err|pr_warn' drivers/pwm/core.c drivers/pwm/sysfs.c`
一共 **15 条**，其中 8 条属于 `CONFIG_PWM_DEBUG`（§4.4）。剩下 7 条：

| 行 | 级别 | 原文 | 什么时候出现 |
|---|---|---|---|
| `core.c:244-245` | warn | `Please implement the .get_state() callback` | 注册时，⚠️ **仅 `CONFIG_PWM_DEBUG` 下** |
| `core.c:702-703` | warn | `No consumer device specified to create a link to` | `pwm_get(NULL, …)`，即消费者没传 `dev` ⇒ ⚠️ **PM 顺序无保证**（03 §B5） |
| `core.c:709-710` | err | `failed to create device link to %s` | `device_link_add()` 失败 |
| `core.c:756` | err | `of_pwm_get(): can't parse "pwms" property` | **DT 面唯一有日志的失败**（06 §7） |
| `core.c:763` | err | `of_pwm_get(): PWM chip not found` | ⚠️ **死代码，永不出现**（06 §2.4） |
| `core.c:1023` | warn | `PWM device already freed` | **重复 `pwm_put()`**，⚠️ 用 `pr_warn` 所以**没有设备前缀**，看不出是哪个通道 |
| `sysfs.c:516-517` | warn | `device_create failed for pwm_chip sysfs export` | sysfs 目录建不起来；⚠️ **注册仍然成功**（`:509-511` 的注释：chip "is still usable by the kernel it's just not exported"） |

⚠️ **`core.c:1023` 的 `pr_warn` 是全核心唯一一条没带设备名的日志。**
排查"PWM device already freed"时无法从日志判断是哪个通道，
只能靠调用栈：`echo 1 > /sys/kernel/tracing/options/stacktrace` 配合
`func_stack_trace`，或直接读消费者驱动的 remove 路径找重复 `pwm_put()`。

⚠️ **核心里一条 `dev_dbg` 都没有** ⇒ `dynamic_debug`
（`echo 'file core.c +p' > /sys/kernel/debug/dynamic_debug/control`）
对 pwm 核心**完全无效**。这条路走不通，别浪费时间。

---

## §6 症状 → 手段决策表

| 症状 | 第一步 | 第二步 |
|---|---|---|
| **没有任何 `pwmchipN`** | `ls /sys/class/pwm/` 为空 ⇒ provider 没注册 | 查 provider 驱动 probe 是否失败；`ls /sys/kernel/debug/pwm` 也应为空 |
| **消费者设备不出现，dmesg 干净** | `cat /sys/kernel/debug/devices_deferred` | `-EPROBE_DEFER` 来自 `core.c:688`（06 §7） |
| **apply 返回 0 但波形没变** | 开 `events/pwm/pwm_apply`，看有没有事件 | 没事件 ⇒ **五字段短路**（`core.c:566-571`，§3.3）；有事件 ⇒ 查 pinctrl / 驱动 |
| **波形完全没有** | `cat /sys/kernel/debug/pinctrl/*/pinmux-pins \| grep <pin>` | 引脚没复用到 PWM（06 §1.1） |
| **周期/占空比和请求的不一样** | debugfs 显示的是**请求值**，靠不住 | 开 `CONFIG_PWM_DEBUG`（§4）看 round-down 警告，或 dump 寄存器 |
| **背光闪一下变全黑 / 调压轨掉压** | `grep -n get_state drivers/pwm/pwm-<x>.c` | 缺 `.get_state` + `pwm_adjust_config()` ⇒ duty 清零（`core.c:642-647`，05 §2.2） |
| **`export` 返回 `-EBUSY`** | `ls /sys/class/pwm/pwmchipN/` 看 `pwmX` 在不在 | 在 ⇒ 已 export 过；不在 ⇒ 被内核驱动占用（§2.4） |
| **`cat capture` 报 `Function not implemented`** | 正常现象 | arm64 上无任何 `.capture` 实现（§2.4、04 §5） |
| **`cat /sys/kernel/debug/pwm` 挂死** | 有 provider 回调卡在 `pwm_lock` 里 | `echo w > /proc/sysrq-trigger` 看阻塞任务栈（§1.2） |
| **unbind provider 之后崩溃** | ⚠️ 这是真 UAF，不是配置问题 | `pwmchip_remove()` 不检查占用（03 §B3）；开 `KASAN` 确认 |
| **`PWM device already freed`** | 消费者重复 `pwm_put()` | `core.c:1023`；日志无设备名，靠栈（§5） |
| **trace 里两条一模一样的 `pwm_apply`** | 只有 `usage_power` 变了 | 事件不记录该字段（§3.4），去 debugfs 看 |

---

## §7 观测面测不到的东西

诚实列一遍**没有任何手段能看到**的：

| 想看的 | 为什么看不到 |
|---|---|
| **硬件寄存器里的真实周期/占空比** | 核心从不主动回读；`.get_state` 只在 request 时和 `PWM_DEBUG` 下被调 |
| **谁持有这个通道**（消费者是哪个驱动） | debugfs 只有 label（可能只是 DT 节点名），没有 consumer 的 `struct device` |
| **`pwm_get()` / `pwm_put()` 的调用次数** | `pwm_get` 事件是"读硬件"的意思（§3.2）；只能 kprobe |
| **`chip->base` ↔ debugfs 里的 chip 行** | chip 行不打 base（§1.1） |
| **`.apply` 失败的尝试** | `trace_pwm_apply` 在成功之后（§3.3） |
| **短路命中了多少次** | 无计数器、无事件、无日志 |
| **`usage_power` 的历史** | 只有 debugfs 快照 |
| **多通道是否"同时"生效** | 框架没有多通道原子 apply（04 §3.6）；trace 只能看到逐通道的先后 |

⇒ 这份空洞清单本身就是 pwm 的一个设计评价：**它把可观测性
几乎全押在了 `CONFIG_PWM_DEBUG` 上，而那是个编译期开关。**

---

## §8 接着读

- 短路、锁、`CONFIG_PWM_DEBUG` 的完整机制 → [03-internals.md](03-internals.md) 的 §A5、§B1、§B4、§B6
- sysfs 层的全部符号地图 → [04-extensions.md](04-extensions.md) 的 §1
- 上面每条症状对应的正确写法 → [05-usage.md](05-usage.md)
- DT 写错导致的那一类 → [06-devicetree.md](06-devicetree.md) 的 §7
