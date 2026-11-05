---
title: 总览
---

# pwm 子系统总览（阅读入口）

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

这一篇是**索引和速查**，不含新事实：所有结论都在 01–08 里带行号展开过，
这里只给"是什么 + 去哪读"。第一次接触 pwm 的人按 §7 的顺序读。

---

## §1 一句话

**pwm 子系统把"周期 + 占空比 + 极性 + 使能"这四个量送到 PWM 控制器硬件。**

它是这一类 provider/consumer 框架里**抽象最薄的一个**：
`struct pwm_state` 只有 5 个字段，没有拓扑、没有多消费者投票、没有约束系统。
框架不替你做的事，全落到控制器驱动和消费者驱动头上——这是它绝大多数坑的来源。

---

## §2 规模与形状（一张表看完）

| 项 | 值 |
|---|---|
| 子系统目录 | `drivers/pwm/`，**64 个 `.c` / 22949 行** |
| 核心 | **1734 行** = `core.c` **1188** + `sysfs.c` **546** |
| 头文件 | `include/linux/pwm.h` **608** · `include/linux/pwm_backlight.h` · `include/dt-bindings/pwm/pwm.h` **15** · `include/trace/events/pwm.h` **58** |
| 导出符号 | **17 个，全在 `core.c`，全部 `EXPORT_SYMBOL_GPL`**；`sysfs.c` **0 个** |
| 类型判定 | 无 `struct bus_type` ⇒ **框架型**；无 `include/uapi/linux/pwm*.h` ⇒ **无 uapi ABI** |
| 用户态面 | **只有 sysfs**（`/sys/class/pwm/`），没有 ioctl、没有 char dev |
| Kconfig | `drivers/pwm/Kconfig` **678 行**；核心三项 `PWM` / `PWM_SYSFS` / `PWM_DEBUG` |
| 官方文档 | **`Documentation/driver-api/pwm.rst`，179 行，唯一一篇** |
| DT 绑定 | `Documentation/devicetree/bindings/pwm/` **51 个文件 = 25 `.yaml` + 26 `.txt`** |
| 全局状态 | `pwm_lock` · `pwm_chips` 链表 · `allocated_pwms` 位图（`MAX_PWMS` = **1024**）· `pwm_tree` · `pwm_lookup_lock` · `pwm_lookup_list` |
| arm64 DTS | `pwms =` **112** 个文件 · `#pwm-cells` **71** 个 · `pwm-names` **仅 4** 个 |
| 树的状态 | `drivers/pwm` + `include/linux/pwm.h` **全部干净**，本树补丁未碰过 pwm |

**7 个结构体**（字段数按花括号深度数，本子系统无 `__randomize_layout`）：

| 结构 | 位置 | 字段数 |
|---|---|---|
| `struct pwm_args` | `include/linux/pwm.h:38-41` | **2** |
| `struct pwm_state` | `include/linux/pwm.h:59-65` | **5** |
| `struct pwm_device` | `include/linux/pwm.h:79-90` | **9** |
| `struct pwm_capture` | `include/linux/pwm.h:256-259` | **2** |
| `struct pwm_ops` | `include/linux/pwm.h:272-282` | **6**（含 `owner`） |
| `struct pwm_chip` | `include/linux/pwm.h:295-308` | **8** |
| `struct pwm_lookup` | `include/linux/pwm.h:555-564` | **8** |
| `struct pwm_export` | `drivers/pwm/sysfs.c:17-22` | **4**（核心私有） |

---

## §3 三条纵向路径

### §3.1 消费者路径：拿到通道并输出波形

```
设备树 pwms = <&pwm 0 25000 1>
  → pwm_get() / devm_pwm_get()        core.c:900 / :1055
  → of_pwm_get()                      core.c:737
  → chip->of_xlate()                  默认 of_pwm_xlate_with_flags，core.c:124
  → 值落在 pwm->args（2 字段）        ⚠️ 不落到 state，不落到硬件
  → 消费者 pwm_init_state()           include/linux/pwm.h:179
  → 改 duty_cycle / enabled
  → pwm_apply_state()                 core.c:546
  → chip->ops->apply()                驱动写寄存器（向下取整）
  → pwm->state = *state               core.c:579，缓存"请求值"
```

### §3.2 provider 路径：注册一个控制器

```
probe
  → 填 chip->dev / ->ops / ->npwm     ⚠️ chip->base 是输出，不要填
  → devm_pwmchip_add(dev, chip)       正确用法；pwmchip_add() 需自己注销
  → alloc_pwms() 分全局 base          core.c:276
  → of_pwmchip_add()                  core.c:181，装默认 xlate + 读 #pwm-cells
  → pwmchip_sysfs_export()            sysfs.c:505，建 /sys/class/pwm/pwmchipN
  → debugfs 自动可见                  /sys/kernel/debug/pwm
```

### §3.3 用户态路径

```
cat /sys/class/pwm/pwmchip0/npwm            # 通道数，唯一不需要 export 的属性
echo 0 > /sys/class/pwm/pwmchip0/export     # ⚠️ 真的 request 通道，会和内核驱动冲突
cd /sys/class/pwm/pwmchip0/pwm0
echo 25000 > period ; echo 12500 > duty_cycle ; echo 1 > enable
echo 0 > /sys/class/pwm/pwmchip0/unexport
```

---

## §4 必须先记住的八件事

按"不知道就会掉坑"的程度排序。每条后面是详细篇目。

1. **`pwm_get_state()` 给的是请求值，不是硬件值。** 官方文档自陈
   "no way for consumers to get the actually implemented settings"
   （`Documentation/driver-api/pwm.rst:68-72`）。debugfs 和 sysfs 读到的
   都是这个缓存值。→ 01、07
2. **DT 里的值只进 `pwm->args`，不自动生效。** 必须消费者调
   `pwm_init_state()` / `pwm_apply_args()` / `pwm_adjust_config()` 搬。
   自己 `memset` 一个 `pwm_state` 就会丢掉 DT 的极性。→ 06、08
3. **`pwm_apply_state()` 有五字段全等短路**（`drivers/pwm/core.c:566-571`）：
   命中就 `return 0`，`.apply` 一次都不调，**没有日志也没有 tracepoint**。
   resume 后的第一次 apply 常常被它吃掉。→ 03、07
4. **`pwm_apply_state()` 完全不加锁。** `pwm_lock` 只保护注册/request/put/
   capture/get_state/debugfs。并发互斥由消费者负责；官方文档说这套锁
   "should be fixed soon"（`pwm.rst:165-172`），6.1 里还是这样。→ 03
5. **`pwmchip_remove()` 是 `void`，而且不检查通道占用**，直接
   `kfree(chip->pwms)` ⇒ **unbind 一个仍被消费者持有的控制器是真 UAF**。
   它的 kerneldoc（`core.c:318-321`）说会返回错误，那是陈旧注释。→ 03
6. **缺 `.get_state` + `pwm_adjust_config()` = 硬件掉到最低值。**
   `pwm-regulator` 是唯一用它的消费者，走 `period == 0` 分支会把 duty
   算成 0 ⇒ 电轨掉压 / 背光全黑。→ 05、07
7. **`pwmchipN` 里的 N 是全局 `base` 不是序号**（`sysfs.c:513-514`），
   会跳号、和 probe 顺序绑定，脚本里不要硬编码。→ 02、07
8. **`echo N > export` 会真的占用通道**，之后 probe 的内核驱动拿 `-EBUSY`；
   反过来通道被内核占着时 export 也拿 `-EBUSY`。两个 `-EBUSY` 分不开。→ 07

---

## §5 三组容易混的名字

| 混的东西 | 区别 |
|---|---|
| `pwm->args` / `pwm->state` / 硬件 | DT 说的（2 字段）/ 请求的（5 字段）/ 取整后的（读不到） |
| `hwpwm` / `chip->base` / `pwm_device *` | DTS+debugfs+`pwmX` 用 / 只有 `pwmchipN` 用 / 只有 tracepoint 用；**三者之间没有任何映射表** |
| `pwm_get()` / `pwm_get_state()` / tracepoint `pwm_get` | 申请通道 / 读软件缓存 / **"刚调过 `.get_state`"，和第一个毫无关系** |
| `pwm_set_period()` / `pwm_apply_state()` | 前者只污染缓存不碰硬件（`include/linux/pwm.h:117`），消费者用它基本是 bug |
| `PWM_POLARITY_INVERSED` / `INVERTED` | C 枚举（`include/linux/pwm.h:22`）/ DTS 宏（`include/dt-bindings/pwm/pwm.h:13`）；都等于 1，用错也能编译 |
| `pwm_put()` / `pwm_free()` | 同一件事；`devm_pwm_put()` **不存在** |
| `drivers/pwm/pwm-clk.c` / `drivers/clk/clk-pwm.c` | clk 进 PWM 出 / PWM 进 clk 出；**compatible 字符串和文件名是交叉的**，只能靠目录分辨 |

---

## §6 观测面速查

| 面 | 入口 | 一句话 |
|---|---|---|
| debugfs | `/sys/kernel/debug/pwm` | 全树一次性快照；⚠️ 数字是软件缓存，label 20 字符截断，读时持 `pwm_lock` |
| sysfs | `/sys/class/pwm/` | 单通道 5 属性；⚠️ export 有副作用，`capture` 在 arm64 上必然 `-ENOSYS` |
| tracepoint | `events/pwm/pwm_apply`、`events/pwm/pwm_get` | 唯一纯旁观手段；⚠️ 只有裸指针，`.apply` 失败和短路都不发事件 |
| `CONFIG_PWM_DEBUG` | dmesg | 唯一能验证驱动契约的手段；⚠️ 编译期开关，且会额外 apply 一次改变时序 |

**核心一共只有 15 条日志**，其中 8 条锁在 `CONFIG_PWM_DEBUG` 里、
1 条是死代码（`core.c:763`）、1 条不带设备名（`core.c:1023`）。
**一条 `dev_dbg` 都没有 ⇒ `dynamic_debug` 对 pwm 核心完全无效。**
⇒ 排查 pwm 问题的基本心态：**日志不会帮你**。详见 07。

---

## §7 十篇怎么读

**第一次接触（约 2 小时）**：本篇 → 01 §1–§2 → 02 §1.0 → 05 §2 → 07 §1–§2

**要写控制器驱动**：01 §3–§4 → 05 §1 → 03 的 A 组 → 07 §4（`CONFIG_PWM_DEBUG` 是必读）

**要写消费者驱动**：01 §2 → 05 §2 → 06 → 04 §3（找一个最像自己的样板抄）

**在排一个具体故障**：直接跳 07 §6 的症状表，再按表里的指引回溯

| 篇 | 行数 | 内容 |
|---|---|---|
| [01-api.md](01-api.md) | 760 | 五族 API 全景、17 个导出符号、`pwm_ops` 6 个成员、错误码语义、近邻符号区分 |
| [02-datastructures.md](02-datastructures.md) | 683 | 7 个结构体逐字段、六组关系（一对多 / 三份配置 / 三个编号 / 三层嵌套 / 生命周期 / 两个占用位）、全局状态与两把锁 |
| [03-internals.md](03-internals.md) | 1135 | 注册注销九步、三条发现路径、apply 判定链、锁覆盖、编号分配、`CONFIG_PWM_DEBUG` 机制、设计债与陈旧文档清单 |
| [04-extensions.md](04-extensions.md) | 602 | sysfs 层 546 行分层、消费者分布表、8 个代表消费者逐个展开、反方向的 provider、两个几乎没人用的能力 |
| [05-usage.md](05-usage.md) | 795 | 五种角色的可编译骨架、消费者四不要、用户态六陷阱、10 组近邻符号、符号存在性核实表 |
| [06-devicetree.md](06-devicetree.md) | 642 | provider/consumer 两侧属性、三种 cells 语义、第三个 cell 的极性、四大消费者样板、绑定地图、12 行排错表 |
| [07-debug.md](07-debug.md) | 519 | 四个观测面、debugfs 会骗你的三件事、tracepoint 五个触发点、`CONFIG_PWM_DEBUG` 八条诊断消息、15 条日志普查、症状决策表 |
| [08-feynman.md](08-feynman.md) | 353 | 从硬件第一性原理重讲一遍、三个必须一起理解的概念、一次 apply 的完整故事、"沉默"失败模式总表 |
| [99-evidence.md](99-evidence.md) | — | 每条结论的来源、复算命令、裁剪范围（ACPI）、与官方文档的差异、技能写回 |

---

## §8 边界声明

- **arm64 only**：x86 / powerpc / arm32 特有的部分不写。
- **ACPI 裁掉**：`acpi_pwm_get()` 确实存在（`drivers/pwm/core.c:818`），
  但 arm64 上不走这条路，裁剪理由记在 99。
- **只覆盖 pwm 核心层**：`core.c` + `sysfs.c` + 4 个头文件；
  62 个具体控制器驱动只在举例时引用，不逐个展开。
- **6.1 快照**：上游 6.2+ 已经移除 `chip->base` 和全局位图、调整了
  `pwmchip_remove()`。本文档描述的是 6.1.158，上面所有现象在这棵树上成立。
