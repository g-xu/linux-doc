---
title: 取证附录
---

# timer 99：取证、方法与未验证项

这一篇不讲 timer 怎么工作，讲**前九篇的每个论断是怎么来的、哪些没验证过**。

读法：
- 想知道"某个数字可信吗" → §3，每条都带能一条命令重跑的复现命令
- 想知道"哪些结论是推断出来的" → §5，四层分层
- 想知道"哪些地方源码本身容易看错" → §6
- 想知道"这套文档自己犯过什么错" → §7.3

---

## §1 基线

### §1.1 ⚠️ 树的状态：**timer 被改过**（本套最重要的一条）

```bash
git log -1 --format='%H %s'
# → f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158

git status --porcelain -- kernel/time include/linux/hrtimer.h include/linux/timer.h \
    include/linux/clocksource.h include/linux/clockchips.h include/linux/jiffies.h \
    include/linux/ktime.h drivers/clocksource
# →  M kernel/time/hrtimer.c
#    M kernel/time/tick-sched.c
#    M kernel/time/timer.c
```

⚠️ **有输出。这和 of 那套（干净）完全不同，直接决定整份文档的定性。**

**逐处改动**（`git diff --stat -- kernel/time/` → 3 files, 4 insertions, 4 deletions）：

| 文件 | 行 | 本树 | 上游 |
|---|---|---|---|
| `kernel/time/hrtimer.c` | 两处 | `raise_hrtimer_softirq()` | `raise_softirq_irqoff(HRTIMER_SOFTIRQ)` |
| `kernel/time/timer.c` | 一处 | `raise_timer_softirq()` | `raise_softirq(TIMER_SOFTIRQ)` |
| `kernel/time/tick-sched.c` | 一处 | `local_pending_timers()` | `local_softirq_pending()` |

**定性结论（这是本套文档的地基，写在 01 §0.1 和 00 开头）**：

1. **三处改动全是同一件事**：把 softirq 唤起换成 PREEMPT_RT 专用包装
2. **非 RT 配置下语义与上游完全一致**——那三个包装在
   `include/linux/interrupt.h:624-638` 的 `#else` 分支里就是原调用的
   `static inline` 直译
3. ⚠️ **但 arm64 上开得起 RT**：`arch/arm64/Kconfig:96` 的
   `select ARCH_SUPPORTS_RT` **也是本地新增的一行**
   （`git diff -- arch/arm64/Kconfig | grep ARCH_SUPPORTS_RT` 显示 `+`），
   而 `CONFIG_PREEMPT_RT` 的门是 `depends on EXPERT && ARCH_SUPPORTS_RT`
   （`kernel/Kconfig.preempt:78`）

⇒ **所以本套文档同时覆盖两条路（非 RT / RT），每处分叉都显式标出。**

**相邻的本地改动**（不在 `kernel/time/` 里但同属一个 patchset）：
`arch/arm64/include/asm/preempt.h` 加了 `CONFIG_PREEMPT_LAZY` 相关逻辑
（`TIF_NEED_RESCHED_LAZY`、`preempt_lazy_count`），
`arch/arm64/include/asm/thread_info.h` 配套改动。
⇒ **这棵树是一个 PREEMPT_RT + PREEMPT_LAZY 的 arm64 安全内核。**
⚠️ 本套**没有**展开 PREEMPT_LAZY（超出 timer 边界，§2.7 登记）。

### §1.2 规模

```bash
wc -l kernel/time/*.c kernel/time/*.h | tail -1        # → 22995
```

| 分组 | 文件（行数） | 处置 |
|---|---|---|
| 四个主体 | `timekeeping.c`(2503) · `hrtimer.c`(2426) · `timer.c`(2367) · `posix-cpu-timers.c`(1702) | 前三个详读，第四个只读契约 |
| 核心其余 | `clocksource.c`(1518) · `tick-sched.c`(1626) · `posix-timers.c`(1459) · `tick-broadcast.c`(1235) · `ntp.c`(1095) · `alarmtimer.c`(964) · `time.c`(909) · `clockevents.c`(778) · `tick-common.c`(564) | 按需详读，见 §2 |
| 中等 | `namespace.c`(467) · `itimer.c`(403) · `timer_list.c`(360) · `posix-clock.c`(320) · `sched_clock.c`(296) · `posix-stubs.c`(254) | 读符号表 + 关键路径 |
| 小文件 | `tick-internal.h`(199) · `vsyscall.c`(170) · `timeconv.c`(141) · `tick-oneshot.c`(128) · `tick-broadcast-hrtimer.c`(111) · `tick-sched.h`(105) · `jiffies.c`(104) · `timecounter.c`(99) · `timekeeping_debug.c`(55) · `posix-timers.h`(45) · `timekeeping_internal.h`(39) · `tick-legacy.c`(37) · `timekeeping.h`(34) · `ntp_internal.h`(22) | 多为读符号表 |
| 测试 | `unittest` 无；`clocksource-wdtest.c`(202) · `test_udelay.c`(159) · `time_test.c`(99) | 只确认存在与开关 |

**头文件 2786 行**：`hrtimer.h`(543) · `jiffies.h`(443) · `tick.h`(324) ·
`timekeeping.h`(312) · `clocksource.h`(309) · `timer.h`(238) · `ktime.h`(234) ·
`clockchips.h`(227) · `timekeeper_internal.h`(156)。

⚠️ **一个和别的子系统不同的地方**：timer **没有一个占比 20%+ 的测试文件**
（对比 of 的 `unittest.c` 占 22.4%）。它的测试分散在
`tools/testing/selftests/timers/` 和 KUnit 里 ⇒ **本套没有读那些**（§2.7）。

### §1.3 ⚠️ 子系统类型判定：框架型（但门 1 会误判）

按 skill 阶段 1 步骤 3b 的两道门：

```bash
# 门 1：有没有自己的 bus_type
grep -rn 'struct bus_type' kernel/time/*.c include/linux/hrtimer.h \
    include/linux/timer.h include/linux/clocksource.h include/linux/clockchips.h
# → kernel/time/clockevents.c:662:static struct bus_type clockevents_subsys = {
#   kernel/time/clocksource.c:1459:static struct bus_type clocksource_subsys = {
```

⚠️ **门 1 有输出——但这是假的。** 按 skill 的判定流程，
"门 1 有输出且门 2 也有 ⇒ 虚拟总线型；门 1 有、门 2 空 ⇒ 硬件总线型"，
**两个结论都错**。

**必须追加第三问：有没有 `device_driver` 绑到这条总线上？**

```bash
grep -rn 'clocksource_subsys\|clockevents_subsys' --include=*.c . \
  | grep -v 'kernel/time/clocksource.c\|kernel/time/clockevents.c'
# → 无输出
```

**三条判据（02 §1.0.E 展开）**：
① 都在 `#ifdef CONFIG_SYSFS` 里；
② 用 `subsys_system_register()` 注册，落点是 `/sys/devices/system/` 不是 `/sys/bus/`；
③ **全树没有任何 driver 绑上去，没有 `.match`、没有 probe**。

⇒ **判定结果：框架型。** 它们是**纯 sysfs 容器**，
存在的唯一目的是挂 5 个属性（§3.6）。

**方法论落点（回写 skill）**：skill 的两道门在"用 `bus_type` 当 sysfs 容器"
这种情况下会误判。**建议给门 1 加一句追问**：
"有输出时先查有没有 `device_driver` 绑上去；只有静态 `device_register()` 而无 driver
⇒ 仍是框架型"。⇒ **这是本轮最有方法论价值的一条发现。**

### §1.4 ⚠️ 官方文档登记：脚本报"0 篇"是假通过（第三种变体）

`check_doc_coverage.sh timer output/timer .` 的输出：

```
=== 【必查】概念/框架文档 ===

【必查】共 0 篇，其中 0 篇 0 引用。
必查档全部有引用。
```

⚠️ **"共 0 篇 → 必查档全部有引用"是假的通过**（skill 规则 28 的 警告）。
**本轮的原因是三种变体里最容易漏的一种**：

```bash
ls -d Documentation/timer            # → No such file or directory
ls -d Documentation/driver-api/timer*  # → No such file or directory
ls -d Documentation/timers           # → 存在！
```

**只差一个复数 `s`。**

**实际存在 7 篇、1092 行，6 篇被引用**（`index.rst` 是纯 toctree，无内容可引）：

| 文件 | 行数 | 纳入了什么 | 引用次数 |
|---|---|---|---|
| `no_hz.rst` | 317 | 三种 tick 模式、`NO_HZ_FULL` 的四个硬约束 | 4 |
| `highres.rst` | 250 | **五个构建块的历史动机**、clockevent 的设计理由、"dynamic ticks 是逻辑必然" | 8 |
| `timekeeping.rst` | 180 | **clocksource/clockevent 是"conceptual reverse"**、`mult`/`shift` 公式、`sched_clock` 与 clocksource 的区别、delay timer | **10** |
| `hrtimers.rst` | 178 | **"试过合并失败了"的原文**、"绝大多数超时会被取消"这个前提 | 7 |
| `timers-howto.rst` | 115 | 延时/睡眠的决策树（01 §5） | 2 |
| ⚠️ `hpet.rst` | 30 | **x86 专属，裁掉**（§2.7） | 1（只在裁剪表里点名） |
| — `index.rst` | 22 | 纯 toctree | 0 |

复现：

```bash
cd Documentation/timers && wc -l *.rst
grep -ohE 'Documentation/timers/[a-z_-]+\.rst' output/timer/0*.md | sort | uniq -c | sort -rn
```

**处置类别：(c) 目录名只差一个复数** ⇒ 本套**没有**登记 `.doc-coverage` 豁免，
手工建了上面这张表。**三种变体的对照表见 §8.1。**

**DT 绑定侧**：脚本报"共 36 篇，被引用 1 篇（`arm,arch_timer`，3 次）"。
**这是合理的**——skill 明确写了"06 篇的硬要求是每条属性都有定位到行的消费者，
不是每篇绑定都被引用"。06 §12.2 显式声明了为什么只讲那一篇。

### §1.5 ⚠️ 文档与代码的冲突

| 冲突点 | 官方文档说 | 代码实际 | 处置 |
|---|---|---|---|
| tick 广播的措辞 | `highres.rst` 提到 "clock event source" | **代码里叫 "clock event device"** | 文档自己在开头就注明了这个改名（"we switched to the name 'clock event devices' in meantime"）⇒ 本套一律用 device |
| hrtimer 的定位 | `hrtimers.rst` 说 "this subsystem does not offer high-resolution clock sources just yet" | **早就有了** | **该文档写于 2.6.16 时代，从未更新** ⇒ 本套只引用它的**设计论证**，不引用它的现状描述 |
| `HZ` 可能降低 | `hrtimers.rst` 说 "we could decrease the frequency of the timeout subsystem from 250 Hz to 100 HZ" | 实际 `CONFIG_HZ` 仍是 4 选一，没有降 | 当作历史设想，不当作现状 |

⚠️ **`hrtimers.rst` 是一份 2006 年的补丁说明书，不是当前状态的文档。**
⇒ **这条要特别当心**：它的**设计理由部分极其有价值**（本套引了 7 次），
但**现状描述已经过期**。读它的时候要分清这两部分。

---

## §2 证据等级

### §2.1 一等证据：引用密集、跨篇覆盖（可当结论直接用）

判据：被引用的**不同行号数**多，且引用散布在 ≥4 篇里。

| 文件 | 引用次数 | 覆盖的篇 |
|---|---|---|
| `kernel/time/timer.c` | **47** | 00,01,02,03,05,07,08 |
| `kernel/time/clocksource.c` | **40** | 00,01,02,03,04,05,07,08 |
| `drivers/clocksource/arm_arch_timer.c` | **38** | 00,02,03,04,05,06,07,08 |
| `kernel/time/timekeeping.c` | **29** | 00,01,02,03,04,07,08 |
| `kernel/time/tick-sched.c` | **24** | 00,01,03,04,07 |
| `kernel/time/hrtimer.c` | **22** | 00,01,02,03,07,08 |
| `include/linux/hrtimer.h` | **21** | 00,01,02,04,08 |
| `include/linux/timer.h` | **17** | 00,01,02,03,05,08 |
| `kernel/time/jiffies.c` | **17** | 00,02,03,05,07,08 |
| `include/trace/events/timer.h` | **16** | 07 一篇但覆盖全部 13 个事件 |
| `include/linux/clocksource.h` | **14** | 00,01,02,05,08 |
| `kernel/softirq.c` · `include/linux/interrupt.h` | **12** · **12** | 00,01,03,08（RT 路径的取证核心） |
| `include/linux/clockchips.h` | **12** | 00,01,02,05,08 |
| `arch/arm64/kernel/time.c` | **12** | 00,03,04,05,06,08 |

### §2.2 二等证据：只读了片段（详读范围写明）

| 文件 | 引用次数 | 详读了哪部分 | 没读的部分 |
|---|---|---|---|
| `kernel/time/clockevents.c` | 11 | 注册路径、`WARN` 点、sysfs、`printk_deferred` 两处 | 广播相关的状态迁移细节 |
| `kernel/time/tick-common.c` | 10 | `tick_init()`、`tick_check_new_device()`、`tick_setup_device()` 入口 | suspend/resume 的冻结路径 |
| `kernel/time/timer_list.c` | 9 | `print_timer`/`print_active_timers`/`print_cpu`/`timer_list_show`、procfs 创建、sysrq 入口 | `print_tickdevice()` 的输出格式细节 |
| `kernel/time/tick-sched.h` | 8 | `tick_device`(全部) 与 `tick_sched` 的前 12 行 | `tick_sched` 后半部分的统计字段 |
| `kernel/time/posix-timers.c` | 8 | 3 条 `FIXME` 的上下文 + `__initcall` | ⚠️ **1459 行里只读了这些** |
| `include/linux/timekeeper_internal.h` | 7 | `tk_read_base` 全部 8 个字段 | `struct timekeeper` 的其余字段 |
| `include/linux/tick.h` | 8 | `tick_broadcast_enter/exit` 两个 inline | 其余 300 余行 |
| `kernel/time/posix-clock.c` | 4 | 两个导出符号的位置 | `posix_clock_operations` 的语义 |
| `kernel/time/Kconfig` | 逐条列在 §3.7 | 全部 config 名与行号 | 各条的 `help` 文本 |

### §2.3 三等证据：只 grep 了符号或行号，没读上下文

- `kernel/time/ntp.c`（1095 行）：**只读了 1 条 `TODO` 和"它改 `mult`"这个接口面**，
  **NTP 的 PLL/FLL 算法完全没读**（04 §8、§5.2）
- `kernel/time/posix-cpu-timers.c`（1702 行）：**只确认了"它会阻止 NO_HZ_FULL"这件事**
- `kernel/time/tick-broadcast.c`（1235 行）：只读了 `tick_broadcast_control` 的位置和
  `tick_broadcast_enter/exit` 的调用者统计
- `kernel/time/alarmtimer.c`（964 行）：读了 10 个导出符号的位置和 `device_initcall`，
  **没读 RTC 编程的具体逻辑**
- `kernel/time/namespace.c`（467 行）：只读了 `do_timens_ktime_to_host()` 的三个 `case`
- `kernel/time/time.c`（909 行）：只统计了导出符号数，**没读任何函数体**
- `drivers/clocksource/timer-ti-32k.c`（165 行）：读了 `ti_32k_timer_init()` 全部 + 头部注释
- `drivers/clocksource/arm_arch_timer.c`（1791 行）：**读了引用到的约 20 个位置**
  （注册路径、DT 属性消费、rating、name、cpuhp 回调、勘误表边界），**没通读**
- `include/linux/delay.h`：读了全部对外接口的声明行，**没读 `udelay` 的架构实现**

### §2.4 ⚠️ `check_doc_coverage.sh` 的处置

**结论：不适用于【必查】档，用手工表替代；【参考】档（DT 绑定）的输出是合理的。**

脚本按子系统目录名 `timer` 找不到文档目录，而真实目录是
`Documentation/timers`（**复数**）⇒ 报"共 0 篇"，这是**假通过**。
手工登记表在 §1.4，三种变体对照见 §8.1。

⇒ 本套**没有**登记 `.doc-coverage` 豁免文件。

### §2.5 索引工具的使用与盲区

cbm 只用于导航（找 `hrtimer_*` / `clocksource_*` 符号的候选位置），
**所有行号最终都由 `rg` + `sed` 在真实树上确认**。
两个盲区都命中了：

- **宏生成的符号**：`TIMER_OF_DECLARE()`（06 §0）、
  `DEFINE_EVENT()` 生成的 tracepoint（07 §3.1）、`DEVICE_ATTR_RW/RO/WO`（§3.6）
  —— **索引里都没有对应节点**
- **函数指针间接调用**：**`event_handler` 这条边索引完全拉不出来**
  （驱动中断处理函数 → 核心填的指针 → `hrtimer_interrupt()`），
  而它恰好是本子系统最容易搞反的一处（01 §8）
  ⇒ **这一条是"索引工具不能用于取证"的最好例证**

### §2.6 全称断言的验证

| 断言 | 出处 | 验证命令 | 结果 |
|---|---|---|---|
| 本树 `kernel/time/` 只有 3 处改动 | 01 §0.1 · §1.1 | `git diff --stat -- kernel/time/` | 3 files, +4/-4 |
| `TP_CONDITION` 一个都没有 | 07 §3 | `grep -n 'TP_CONDITION' include/trace/events/timer.h` | 空 |
| `kernel/time/` 下没有故障注入 | 07 §8 | `grep -rn 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR\|CONFIG_FAIL' kernel/time/*.c` | 空 |
| 没有 driver 绑到那两个伪总线 | 02 §1.0.E · §1.3 | 见 §1.3 的命令（**跨出了 `kernel/time/` 目录**） | 空 |
| `tick_broadcast_control()` 全树只有核心自己调 | 04 §1.2 | `grep -rlw 'tick_broadcast_control' --include=*.c .` | 只有 `kernel/time/tick-broadcast.c` |
| `posix_clock_register()` 只有 1 个消费者 | 04 §7 | `grep -rlw 'posix_clock_register' --include=*.c .` | `drivers/ptp/ptp_clock.c` + 实现本身 |
| `sched_clock_register()` 全系统只能有一个 | 05 §4 | ⚠️ **这一条没有用命令验证** ⇒ 降级到 §5.3 的推断层 |

⚠️ **最后一条要显式说明**：05 §4 写了"全系统只能有一个 sched_clock，
后注册的覆盖先注册的"，**这是从 `sched_clock.c` 只有一份全局
`clock_read_data` 推出来的，没有找到明确拒绝重复注册的代码**。
⇒ **登记进 §5.3。**

### §2.7 裁掉了什么（arm64-only 的代价）

**这些不是遗漏，是显式裁掉的**：

| 裁掉的 | 为什么 | 影响到哪一篇 |
|---|---|---|
| `Documentation/timers/hpet.rst`（30 行） | **HPET 是 x86 专属硬件** | 04 §12.3 点名 |
| **ACPI / GTDT 发现路径** | **arm64 服务器会用**，但本套限定 DT。⚠️ `TIMER_ACPI_DECLARE()` 全树 1 个使用者，就是 `arm_arch_timer.c` ⇒ **同一个驱动同时支持两条路** | 06 §0 分诊表列出但不覆盖，§12.3 登记 |
| `kernel/time/tick-legacy.c`（37 行） | `CONFIG_LEGACY_TIMER_TICK`（`kernel/time/Kconfig:60`）给还没转 clockevent 的架构；arm64 走 `GENERIC_CLOCKEVENTS` | 04 §12.1 · §12.3 |
| **NTP 的 PLL/FLL 算法** | **超出 timer 边界**，本套只讲"它改 `mult`"这个接口面 | 04 §8；§2.3 登记为三等 |
| **`CONFIG_PREEMPT_LAZY`** | **本树确实带了它**（§1.1），但它属于调度/抢占，不属于 timer | 只在 §1.1 交代存在 |
| x86/powerpc/mips 的 C3STOP 实现 | 非目标架构 | 04 §1.1 的表里点名但不展开 |
| `arm,armv7-timer-mem`（MMIO 变体） | arm64 上极少用，节点形状完全不同 | 06 §12.2 |
| `tools/testing/selftests/timers/` | **本套完全没有读用户态自测集** | §1.2 提到 |
| 其余 69 个 DT 定时器绑定 | 讲了对 arm64 读者没有用 | 06 §12.2 |

---

## §3 统计数字总表（每条都能一条命令重跑）

**统计范围一律显式列文件**，不用 `kernel/time/*.c` 通配——
通配会把 3 个测试文件（`clocksource-wdtest.c` / `test_udelay.c` / `time_test.c`）算进去。
下文 `$CORE` 指这 18 个非测试文件：

```bash
CORE="kernel/time/timer.c kernel/time/hrtimer.c kernel/time/timekeeping.c \
kernel/time/clocksource.c kernel/time/clockevents.c kernel/time/tick-common.c \
kernel/time/tick-sched.c kernel/time/tick-broadcast.c kernel/time/time.c \
kernel/time/ntp.c kernel/time/posix-timers.c kernel/time/posix-cpu-timers.c \
kernel/time/posix-clock.c kernel/time/itimer.c kernel/time/alarmtimer.c \
kernel/time/namespace.c kernel/time/sched_clock.c kernel/time/timecounter.c"
```

### §3.1 规模类

| 数字 | 值 | 复现命令 |
|---|---|---|
| `kernel/time/` 总行数 | 22995 | `wc -l kernel/time/*.c kernel/time/*.h \| tail -1` |
| 文件数 | 35 | `ls kernel/time/*.c kernel/time/*.h \| wc -l` |
| 相关头文件总行数 | 2786 | `wc -l include/linux/hrtimer.h include/linux/timer.h include/linux/clocksource.h include/linux/clockchips.h include/linux/jiffies.h include/linux/ktime.h include/linux/timekeeping.h include/linux/tick.h include/linux/timekeeper_internal.h \| tail -1` |
| 官方文档总行数 | 1092 | `cd Documentation/timers && wc -l *.rst \| tail -1` |
| arm64 arch timer 驱动行数 | 1791 | `wc -l drivers/clocksource/arm_arch_timer.c` |
| 最简代表驱动行数 | 165 | `wc -l drivers/clocksource/timer-ti-32k.c` |
| 本树改动 | 3 文件 / +4 / -4 | `git diff --stat -- kernel/time/` |

### §3.2 导出符号类

| 文件 | `EXPORT_SYMBOL*` |
|---|---|
| `timer.c` | 30 |
| `timekeeping.c` | 26 |
| `time.c` | 26 |
| `hrtimer.c` | 18 |
| `alarmtimer.c` | 10 |
| `tick-sched.c` | 8 |
| `clocksource.c` | 6 |
| `clockevents.c` | 4 |
| `timecounter.c` | 3 |
| `jiffies.c` · `posix-clock.c` | 2 · 2 |
| `tick-common.c` · `tick-broadcast.c` · `timeconv.c` | 1 · 1 · 1 |
| `ntp.c` · `posix-timers.c` · `posix-cpu-timers.c` · `itimer.c` · `sched_clock.c` · `namespace.c` · `vsyscall.c` · `timer_list.c` · `tick-oneshot.c` | **全部 0** |
| **合计**（含未列出的 0） | **138** |

复现（逐文件而非合并，便于逐格对账）：

```bash
for f in timer.c hrtimer.c timekeeping.c clocksource.c clockevents.c tick-common.c \
         tick-sched.c tick-broadcast.c tick-oneshot.c time.c ntp.c posix-timers.c \
         posix-cpu-timers.c posix-clock.c itimer.c alarmtimer.c timecounter.c \
         jiffies.c sched_clock.c namespace.c timeconv.c vsyscall.c timer_list.c; do
  printf '%-24s %s\n' "$f" "$(grep -cE '^EXPORT_SYMBOL' kernel/time/$f)"
done
echo "总计 → $(grep -hE '^EXPORT_SYMBOL' kernel/time/*.c | wc -l)"
```

⚠️ **两条口径说明**：
1. 数的是**行首** `EXPORT_SYMBOL`（含 `_GPL`）；本目录没有行中间出现的情况
   ⇒ 行数 = 符号数
2. **总计 138 用的是 `kernel/time/*.c` 通配**（包含测试文件），
   而逐文件表列的是 23 个非测试文件 ⇒ **两个数不完全同源**，
   但测试文件的 `EXPORT_SYMBOL` 数为 0，所以结果一致。**这条口径不一致是刻意留下并说明的。**

**最值得注意的一行**：`posix-timers.c`(1459) + `posix-cpu-timers.c`(1702) +
`itimer.c`(403) + `posix-stubs.c`(254) = **4238 行，导出符号 0 个**
⇒ **整层是系统调用实现，不是内核内部 API**（04 §6.1）。

### §3.3 错误码类

| 错误码 | 次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | **61** | 参数不合法 |
| `-EFAULT` | **56** | `copy_to/from_user()` 失败 —— **几乎全在 POSIX 那一层** |
| `-ENODEV` | **21** | 时钟源/事件设备不存在或不可用 |
| `-EBUSY` | **14** | 设备正在当 tick 源，不能解绑 |
| `-EOPNOTSUPP` | 10 | 该时钟不支持这个操作 |
| `-EPERM` | 8 | 改系统时间要 `CAP_SYS_TIME` |
| `-EAGAIN` | 7 | 稍后重试 |
| `-ENOSYS` · `-EINTR` | 4 · 4 | 功能未编译 · 被信号打断 |
| `-EACCES` | 3 | — |
| `-ENOMEM` · `-EOVERFLOW` | 1 · 1 | — |
| `-ENOENT` · `-ETIMEDOUT` · `-ERESTARTSYS` · `-ENOTSUPP` | **0** | 本子系统不返回这四个 |

复现：

```bash
for e in EINVAL EFAULT ENODEV EBUSY EOPNOTSUPP EPERM EAGAIN ENOSYS EINTR \
         EACCES ENOMEM EOVERFLOW ENOENT ETIMEDOUT ERESTARTSYS ENOTSUPP; do
  printf '%-14s %s\n' "-$e" "$(grep -how -- "-$e" $CORE | wc -l)"
done
```

⚠️ **口径陷阱两条**：
1. `grep -how` 数的是**出现次数**，不是返回点数——有些出现在 `if (ret == -E...)` 里
2. **必须带 `-w`**：不带时 `-ENOTSUPP` 会被 `-EOPNOTSUPP` 的子串命中，
   ⇒ **这两个码在本子系统同时存在（10 和 0），是子串包含陷阱的真实案例**

**`-EFAULT`(56) 排第二**是本子系统的特征：它是"内核基础设施 + 一大层系统调用"
的混合体（01 §14.1）。

### §3.4 可观测性类

| 数字 | 值 | 复现命令 |
|---|---|---|
| tracepoint 事件（timer.h） | **13** | `grep -cE '^(TRACE_EVENT\|DEFINE_EVENT)\(' include/trace/events/timer.h` |
| tracepoint 事件（alarmtimer.h） | **4** | `grep -cE '^(TRACE_EVENT\|DEFINE_EVENT)\(' include/trace/events/alarmtimer.h` |
| **合计** | **17** | 上面两条相加 |
| `TP_CONDITION` | **0** | `grep -c 'TP_CONDITION' include/trace/events/timer.h` |
| `trace_tick_stop` 调用点 | **7** | `grep -c 'trace_tick_stop' kernel/time/tick-sched.c` |
| sysfs 属性 | **5** | `grep -c 'DEVICE_ATTR' kernel/time/clocksource.c kernel/time/clockevents.c`（3+2） |
| procfs 出口 | **1** | `/proc/timer_list`（`kernel/time/timer_list.c:353`，模式 `0400`） |
| SysRq 键 | **`q`** | `drivers/tty/sysrq.c:491` 的 `/* q */` 注释 |
| debugfs 出口 | **0** | `grep -rn debugfs kernel/time/*.c`（只有 `timekeeping_debug.c` 与 debugfs 无关） |
| 故障注入接入点 | **0** | 见 §2.6 |

⚠️ **`DECLARE_EVENT_CLASS` 不算事件**——`timer.h` 里有 15 个
`TRACE_EVENT`/`DEFINE_EVENT`/`DECLARE_EVENT_CLASS` 声明，
其中 **2 个是 `DECLARE_EVENT_CLASS`**（`timer_class`、`hrtimer_class`），
它们只是模板 ⇒ **实际事件 13 个**。**这是一处很容易多算 2 个的地方。**

### §3.5 设计债类

```bash
grep -rnE '(FIXME|TODO|XXX|REVISIT|HACK)' $CORE
```

**6 条**：

| 位置 | 内容 | 性质 |
|---|---|---|
| `kernel/time/posix-timers.c:147` | `FIXME: Replace this by a per signal struct xarray` | 数据结构欠债 |
| `kernel/time/posix-timers.c:347` | `FIXME: if ->sigq is queued we can race with …` | **已知竞态** |
| `kernel/time/posix-timers.c:396` | `FIXME: What we really want, is to stop this …` | 语义不理想 |
| `kernel/time/timer.c:578` | `TODO: This wants some optimizing similar to the code below` | 性能 |
| `kernel/time/ntp.c:882` | `TODO: test various filters` | 测试 |
| `kernel/time/timekeeping.c:1582` | `XXX - Do be sure to remove it once all arches implement it` | 跨架构过渡期 |

**3 条（50%）集中在 `posix-timers.c`（1459 行，占总量 6.3%）**
⇒ 03 §C1 的结论：**核心干净，债在 uapi 那一层**。

### §3.6 sysfs 属性逐个

| 属性 | 模式 | 位置 |
|---|---|---|
| `current_clocksource` | **RW** | `kernel/time/clocksource.c:1378` |
| `unbind_clocksource` | **WO** | `kernel/time/clocksource.c:1413` |
| `available_clocksource` | RO | `kernel/time/clocksource.c:1449` |
| `current_device` | RO | `kernel/time/clockevents.c:684` |
| `unbind_device` | **WO** | `kernel/time/clockevents.c:717` |

**三个可写文件的 setter 语义在 07 §5.1 逐条写明**（skill 规则 22 的要求）。

### §3.7 Kconfig 选项

| 选项 | 位置 |
|---|---|
| `CLOCKSOURCE_WATCHDOG` | `kernel/time/Kconfig:9` |
| `GENERIC_CLOCKEVENTS` | `kernel/time/Kconfig:30`（`def_bool !LEGACY_TIMER_TICK`） |
| `GENERIC_CLOCKEVENTS_MIN_ADJUST` | `kernel/time/Kconfig:44` |
| `HAVE_POSIX_CPU_TIMERS_TASK_WORK` | `kernel/time/Kconfig:53` |
| `POSIX_CPU_TIMERS_TASK_WORK` | `kernel/time/Kconfig:56` |
| `LEGACY_TIMER_TICK` | `kernel/time/Kconfig:60` |
| `TIME_KUNIT_TEST` | `kernel/time/Kconfig:67` |
| `TICK_ONESHOT` | `kernel/time/Kconfig:91` |
| `NO_HZ_COMMON` | `kernel/time/Kconfig:94` |
| `HZ_PERIODIC` | `kernel/time/Kconfig:102` |
| `NO_HZ_IDLE` | `kernel/time/Kconfig:108` |
| `NO_HZ_FULL` | `kernel/time/Kconfig:118` |
| `HIGH_RES_TIMERS` | `kernel/time/Kconfig:191` |
| `CLOCKSOURCE_WATCHDOG_MAX_SKEW_US` | `kernel/time/Kconfig:199` |
| `HZ` | `kernel/Kconfig.hz:51` |
| `PREEMPT_RT` | `kernel/Kconfig.preempt:76-78` |

### §3.8 消费者统计（04 §12.2 的原始数据）

| 框架 | 口径 | 值 |
|---|---|---|
| clocksource | `clocksource_register_hz` 调用文件数 | **88** |
| clockevent | `clockevents_config_and_register` 文件数 | **87** |
| sched_clock | `sched_clock_register` 文件数 | **76** |
| `TIMER_OF_DECLARE` | 使用文件数 | **72** |
| timecounter | `timecounter_init` 文件数 | **26**（读侧 `timecounter_read` **23**） |
| `drivers/clocksource` 里的 `platform_driver` | 文件数 | **13** |
| C3STOP 声明者 | `CLOCK_EVT_FEAT_C3STOP` 文件数 | **9**（含 2 个核心文件 ⇒ 驱动/架构侧 **7**） |
| tick 广播 | `tick_broadcast_enter` 文件数 | **6**（含 1 核心 ⇒ 真消费者 **5**） |
| alarmtimer | ⚠️ **`#include <linux/alarmtimer.h>` 的文件数** | **3**（含实现 ⇒ 真消费者 **2**） |
| posix-clock | `posix_clock_register` 文件数 | **2**（含实现 ⇒ 真消费者 **1**） |
| `TIMER_ACPI_DECLARE` | 使用文件数 | **1** |

### §3.9 文档自身规模

| 篇 | 行数 | 章节数 |
|---|---|---|
| 00-overview | 628 | 36 |
| 01-api | 675 | 35 |
| 02-datastructures | 670 | 27 |
| 03-internals | 615 | 31 |
| 04-extensions | 460 | 34 |
| 05-usage | 744 | 29 |
| 06-devicetree | 413 | 27 |
| 07-debug | 551 | 34 |
| 08-feynman | 647 | 37 |
| **前九篇合计** | **5403** | **290** |

复现：`for f in output/timer/0*.md; do printf '%s %s\n' "$(wc -l <$f)" "$f"; done`

---

## §4 工具版本

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本 |
| ripgrep | 13.0.0 | 全树取证主力 |
| git | 2.34.1 | **基线确认（本套尤其关键，§1.1）** |
| GNU sed | 4.8 | 逐行核对 |
| cbm (codebase-memory MCP) | 见 §2.5 | **仅导航** |

⚠️ **没有跑过的工具**：`dtc`、`make dtbs_check`、`gcc`、任何内核构建
⇒ 06 §10.1 和 07 的命令都是从源码反推的（§5.1）。

---

## §5 未验证项（四层分层）

### §5.1 第一层：未编译、未运行

**本套从头到尾没有编译过一行代码，没有跑过一次内核，没有在真机上执行过一条命令。**

| 内容 | 风险 |
|---|---|
| 05 篇全部驱动骨架 | **未编译**（该篇篇首已声明）：省略了部分头文件、`MODULE_*`、完整的错误回滚 |
| 07 篇全部命令的**输出** | **未实测**：路径/文件名是从 `proc_create` / `DEVICE_ATTR` / `TRACE_EVENT` 反推的，**格式和字段没在真机核对** |
| 07 §3.4 的 tracing 命令 | **字段名从 `TP_STRUCT__entry` 读出，没跑过** |
| 06 §10 的 `dtc` / `dtbs_check` | **没跑过** |
| 06 §9 的 DTS 片段 | 取自本树真实节点（`arch/arm64/boot/dts/ti/k3-am65.dtsi:52-58`），**但没有 `dtc` 编译验证** |
| **RT 路径的全部行为** | ⚠️ **本套从未在 `CONFIG_PREEMPT_RT=y` 的内核上验证过任何一条论断**（§5.3 有专门一条） |

**唯一逐字确认过输出格式的两处**：
① `.resolution: %u nsecs`（`kernel/time/timer_list.c:104`）；
② SysRq 的 `show-all-timers(q)` 和 `Show clockevent devices & pending hrtimers (no others)`
（`drivers/tty/sysrq.c` 的 `sysrq_show_timers_op`）。

### §5.2 第二层：只读了片段（见 §2.2、§2.3）

**四处高风险**：

| 内容 | 风险 |
|---|---|
| **NTP（1095 行只读了 1 条 TODO）** | 04 §8 说"它改 `mult`"——**这是从 03 §B4 的接口面推的，没读算法** ⇒ "`maxadj` 限制单次调整幅度"这条也只读了字段注释 |
| **`posix-cpu-timers.c`（1702 行几乎没读）** | 04 §6.3 说"它会阻止 NO_HZ_FULL"——**依据是官方文档，不是代码** |
| **`tick-broadcast.c`（1235 行只读了两处）** | 04 §1.2 那张广播流程图**是按文档和函数名推的**，没有逐段读实现 |
| **`alarmtimer.c` 的 RTC 编程逻辑** | 04 §2.1 说"没有 RTC 就静默退化"——**从 `alarmtimer_get_rtcdev()` 可返回 NULL 推的**，没读所有调用点 |

### §5.3 第三层：推断而非观察

| 论断 | 出处 | 依据 | 为什么算推断 |
|---|---|---|---|
| **RT 上定时器抖动变大** | 05 §1.4 · 07 §6.4 · 08 §8 | 从"`timersd` 是 FIFO 低优先级、可被抢占"推出 | **没有任何实测延迟数据** |
| **`_HARD` 回调里拿 `spinlock_t` 在 RT 上会炸** | 05 §1.4 · §7.5 | 从"RT 上 `spinlock_t` 变可睡"这个通识推出 | **没有构造过这个场景** |
| `sched_clock` 全系统只能有一个 | 05 §4 | 从只有一份全局 `clock_read_data` 推出 | **没找到拒绝重复注册的代码**（§2.6 已降级） |
| `udelay()` 在 arm64 上不受调频影响 | 01 §5.1 · 03 §A5 | 从 `lpj_fine = arch_timer_rate / HZ`（`arch/arm64/kernel/time.c:69`）推出 | 逻辑很直接，但**没实测** |
| `clock_gettime()` 在 arm64 上走 vDSO 不进内核 | 00 §6.2 | 从 `clocksource.vdso_clock_mode` 字段存在 + `vsyscall.c` 的作用推出 | **没有读 vDSO 侧代码，没有 strace 验证** |
| `msleep(1..20)` 实际睡 ~20ms | 01 §5.2 · 05 §7.8 | **官方文档 `timers-howto.rst` 的明确断言** | 属于三等证据（文档），未实测 |
| 广播设备"用还醒着的 CPU 替睡着的 CPU 计时" | 04 §1.3 · 08 §7 | 从 `tick-broadcast-hrtimer.c` 的名字和 111 行规模推出 | **没有逐行读那 111 行** |
| `nr_retries` 涨得快 ⇒ `min_delta_ns` 设小了 | 03 §B6 · 05 §3.1 · 07 §4.2 | 从重试路径的代码结构推出 | 没有实际观察过这个计数器 |

⚠️ **第一、二条是本套最该小心的推断**：整套文档反复强调
"本树能开 RT，所以要选对 `_SOFT`/`_HARD`"，**而这条建议本身没有实测支撑**。
⇒ **它在逻辑上很硬（RT 上 `spinlock_t` 可睡是确定的），但读者若要依赖它做架构决策，
应当自己在 RT 内核上验证一次。**

### §5.4 第四层：版本时效性

- **所有行号只对 6.1.158 + 本树的 3 处改动有效**
- **`timer_delete`/`timer_shutdown` 这组改名正在进行中**（01 §3.3、03 §C5）：
  6.1.158 上旧名仍占绝对多数（`del_timer_sync` 685 文件 vs `timer_delete_sync` 5 文件），
  ⇒ **更高版本上这个比例会持续变化，§3.8 那类统计会过期**
- **`try_to_del_timer_sync()` 还没有新名**（`include/linux/timer.h:184`）
  ⇒ 未来版本可能补上，届时 03 §C5 的"留了尾巴"这个评价需要更新
- ⚠️ **本树的 RT patchset 不是上游**：上游 6.1 的 arm64 **没有**
  `select ARCH_SUPPORTS_RT`。⇒ **把本套文档用到别的 6.1.y 树上时，
  §1.1 的基线核对必须重跑**——否则 §2.9 / §5.7 那一整套 RT 叙述可能不适用
- `include/linux/interrupt.h:612-639` 那段的行号对 patchset 版本敏感

---

## §6 交叉验证实例：初始假设被推翻的七条

### §6.1 ⚠️`kernel/time/` 是干净的吗？——**不是**（本套最重要的一条）

**初始预判**（写在台账里）："这棵树可能改过 timer——首页 `git status` 里有
`arch/arm64/include/asm/preempt.h`，必须确认 `kernel/time` 有没有被碰过"。

**预判方向对了，而且确实被证实**：3 个文件被改。
**但预判没料到的是"改动的性质"**：一开始看到 4 行改动会以为是小修补，
逐行 diff 之后才发现它们**全部指向同一个机制**（PREEMPT_RT 的 `timersd`），
而且**真正的开关在 `arch/arm64/Kconfig:96`**——一行 `select ARCH_SUPPORTS_RT`。

⇒ **教训：4 行改动可以改变整套文档的定性。**
如果只做"`git status` 有没有输出"这一步就收工，会漏掉"这棵树能开 RT"这个结论，
而那是 05 §1.4 那条最重要的驱动建议的全部依据。

### §6.2 ⚠️ timer 有 `bus_type` 吗？——**有两个，但都是假的**

**初始预判**（台账里写着）："预判：**没有** ⇒ 框架型"。

**预判错了**：`kernel/time/clockevents.c:662` 和
`kernel/time/clocksource.c:1459` 各有一个 `struct bus_type`。

**但结论仍然是框架型**——靠追加第三问（有没有 driver 绑上去）才确定。
⇒ **详见 §1.3。这条同时是本轮最有方法论价值的发现**（skill 的两道门在这里会误判）。

### §6.3 ⚠️ alarmtimer 有几个消费者？——**9 → 2**

**初始统计**：`grep -rlwE 'alarm_init|alarm_start' --include=*.c .` → **9 个文件**。

**推翻它的检查**：逐个打开看，发现 5 个是 nouveau 的：

```bash
grep -nw 'alarm_init' drivers/gpu/drm/nouveau/nvkm/subdev/timer/nv04.c
# → 143:	.alarm_init = nv04_timer_alarm_init,
```

**那是 nouveau 自己 `struct nvkm_timer_func` 里的一个函数指针字段名**，
和内核 alarmtimer 毫无关系（调用点在 `drivers/gpu/drm/nouveau/nvkm/subdev/timer/base.c:81` 和 `:132`）。

**正确口径是按头文件**：`grep -rln 'linux/alarmtimer.h' --include=*.c .` → **3**
（含实现本身 ⇒ 真消费者 **2**：`fs/timerfd.c`、`net/netfilter/xt_IDLETIMER.c`）。

⇒ **虚高 4.5 倍。** 记进 04 §2.2 和 §12.2 的警告栏。

### §6.4 ⚠️`tick_broadcast_control()` 有几个使用者？——**导出了但没人直接调**

**初始假设**：它是 `EXPORT_SYMBOL` 的，所以数它的调用者就能知道广播机制有多少用户。

**实际**：`grep -rlw 'tick_broadcast_control' --include=*.c .` 只有
`kernel/time/tick-broadcast.c` 自己。

**真消费者走的是 inline 包装**：`tick_broadcast_enter()` /
`tick_broadcast_exit()`（`include/linux/tick.h:99` / `:103`），**6 个文件**在用。

⇒ **教训：数使用者要按"读者实际会调的那个名字"数，不是按导出符号数。**
skill 规则 20 的"被引用的地方 ≠ 调用者"在这里换了个形式出现。

### §6.5 ⚠️ time namespace 影响几个时基？——**2 → 3**

**初稿写的**："只影响 `CLOCK_MONOTONIC` 和 `CLOCK_BOOTTIME`"。

**推翻它的源码**：`kernel/time/namespace.c:23-58` 的 `switch` 有**三个 `case`**——
还有 `CLOCK_BOOTTIME_ALARM`。

⇒ **典型的"数字类错误"**（skill 规则 30）：不打开 `switch` 逐个数就会少算。
已修正 04 §9。

### §6.6 ⚠️ timer 有官方文档吗？——**有 7 篇，脚本因为一个复数 s 找不到**

**初始输入**：`check_doc_coverage.sh` 报"共 0 篇 → 必查档全部有引用"。

**推翻它的一条命令**：`ls -d Documentation/timers`（**复数**）。

⇒ **这是该陷阱的第三种变体**（§8.1 有三种对照）。
**而且这 7 篇是本套最有价值的三等证据**：
`hrtimers.rst` 提供了"试过合并失败了"的原文，
`highres.rst` 提供了 clockevent 的设计动机和"dynamic ticks 是逻辑必然"，
`timekeeping.rst` 提供了 "conceptual reverse" 这个关键措辞。
⇒ **00 §2 那一整节背景几乎全靠它们**——如果按脚本的输出收工，这一节就写不出来。

### §6.7 `/proc/timer_list` 列不列低精度定时器？——**内核自己给了答案**

**初始假设**：既然文件名叫 `timer_list`，应该列 `struct timer_list`。

**推翻它的两处证据**：
① 代码：`print_timer()` 的参数类型是 `struct hrtimer *`，
`print_cpu()` 取的是 `per_cpu(hrtimer_bases, cpu)`（`kernel/time/timer_list.c` 的
`print_timer` / `print_active_timers` / `print_cpu`）——**全程只有 hrtimer**；
② **内核自己的措辞**：SysRq 的 action 消息是
`Show clockevent devices & pending hrtimers (no others)`。

**`(no others)` 这三个字是官方确认。**
⇒ **文件名反直觉**：它叫 `timer_list` 但**不含 `timer_list`**。
记进 04 §10 和 07 §4.1。

---

## §7 逐篇取证表

### §7.1 逐篇关键论断 → 出处

| 篇 | 关键论断 | 出处 |
|---|---|---|
| 00 | 本树有 3 处 PREEMPT_RT 改动，非 RT 下语义同上游 | `git diff` + `include/linux/interrupt.h:624-638` |
| 00 | 四层抽象是四次分别解决问题累积出来的 | `Documentation/timers/highres.rst` 的五个构建块 + `hrtimers.rst` |
| 00 | `clock_gettime()` 在 arm64 上走 vDSO | ⚠️ `clocksource.vdso_clock_mode` + `vsyscall.c`（**推断**，§5.3） |
| 01 | 导出符号 138 个 | §3.2 的逐文件计数 |
| 01 | `timer_delete_sync()` 不能持"任何中断上下文会拿的锁" | `kernel/time/timer.c:1584-1624` 的 kerneldoc（含 CPU0/CPU1 对照图） |
| 01 | `timer_shutdown_sync()` 把 `function` 置 NULL | `kernel/time/timer.c:1315`、`:1334-1337`、返回值注释 `:1182`/`:1207` |
| 01 | `del_timer*` 是平行 inline，会分叉 | `include/linux/timer.h:184-214` |
| 01 | 旧名 685 文件 vs 新名 5 文件 | §3.8 口径 + `grep -rlw` |
| 02 | 8 个 clock_base = 4 时基 × {HARD,SOFT} | `include/linux/hrtimer.h:170-179` |
| 02 | 那两个 `bus_type` 是纯 sysfs 容器 | §1.3 的三条判据 |
| 02 | `ktime_get()` 的完整调用链 | `kernel/time/timekeeping.c:854` → `:384` → `:284`/`:240` → `:191` → `:374` |
| 03 | `timekeeping_init()` 在 `time_init()` 之前 | `init/main.c:1063` vs `:1064` |
| 03 | 兜底时钟源 rating=1 | `kernel/time/jiffies.c:34`（注释 `lowest valid rating`） |
| 03 | arm64 arch timer rating=400 | `drivers/clocksource/arm_arch_timer.c:235` |
| 03 | `lpj_fine = arch_timer_rate / HZ` | `arch/arm64/kernel/time.c:69` |
| 03 | 高精度是懒切换 | `kernel/time/hrtimer.c:1954-1955` → `:756` |
| 03 | RT 的 `timersd` 复用 `__do_softirq()` | `kernel/softirq.c:1022` 一带 |
| 03 | hang 检测三级保护 | `kernel/time/hrtimer.c:1886`、`:1906`、`:1915-1921`、`:701` |
| 03 | 设计债 6 条，3 条在 `posix-timers.c` | §3.5 |
| 04 | 广播真消费者 6 个，走 inline 包装 | §6.4 |
| 04 | alarmtimer 真消费者 2 个 | §6.3 |
| 04 | `/proc/timer_list` 不含 `timer_list` | §6.7（内核自己的 `(no others)`） |
| 04 | time namespace 影响 3 个时基 | `kernel/time/namespace.c:23-58` 的三个 `case`（§6.5） |
| 05 | 可选属性判 `-EINVAL`（引用 of 那套） | 跨文档集引用，§8.2 |
| 05 | `_HARD` 回调里拿 `spinlock_t` 在 RT 上会炸 | ⚠️ **推断**（§5.3） |
| 06 | `always-on` 直接控制 C3STOP | `drivers/clocksource/arm_arch_timer.c:1401` |
| 06 | 10 个属性每条都有消费行号 | 06 §1 的表 |
| 06 | 五条发现路径 | `TIMER_OF_DECLARE` 72 / `platform_driver` 13 / `TIMER_ACPI_DECLARE` 1 / `core_initcall` / cpuhp |
| 07 | 17 个 tracepoint，0 个 `TP_CONDITION` | §3.4（注意 `DECLARE_EVENT_CLASS` 不算） |
| 07 | 看门狗日志逐字 | `kernel/time/clocksource.c` 的 14 处 `pr_*` |
| 07 | SysRq 是 `q` 键 | `drivers/tty/sysrq.c:491` |
| 07 | 没有故障注入 | §2.6 |
| 08 | "试过合并失败了" | `Documentation/timers/hrtimers.rst` 原文 |
| 08 | "dynamic ticks 是逻辑必然" | `Documentation/timers/highres.rst` 原文 |

### §7.2 三个自检脚本的最终结果

| 脚本 | 结果 |
|---|---|
| `check_line_refs.py` | 见 §7.5 |
| `check_xrefs.py` | 见 §7.5 |
| `check_doc_coverage.sh` | **【必查】档不适用**（复数 `s` 导致找不到，§1.4／§2.4／§8.1）⇒ **手工建表替代**；**【参考】档（DT 绑定 36 篇引 1 篇）的输出是合理的**，理由见 06 §12.2 |

两个 `.ignore` 文件**都不存在** ⇒ **零豁免**：

```bash
ls output/timer/.xref-ignore output/timer/.lineref-ignore
# → 两条 No such file or directory
```

### §7.3 本套的自我修正记录（八条）

**和 of 那套不同**：本套的前九篇和 99 是**同一个会话**写的，
所以修正记录是完整的（of 那套因为会话撞 1M 上限而丢失了初稿阶段的记录）。

| # | 修正 | 严重度 | 怎么发现的 |
|---|---|---|---|
| 1 | ⚠️ **`include/linux/timer.h:47` 被我读错**：以为是 `timer_delete_sync()` 的死锁警告，实际是 `TIMER_IRQSAFE` 的说明（"从 IRQ 里等是**安全的**"，语义正好相反） | **ERROR** | **符号存在性核对时顺手打开了那段注释** |
| 2 | `ktime_get()` 的调用链函数名写错：写成 `timekeeping_get_delta()` 直接被调，实际是 `timekeeping_get_ns()`（`:384`）→ `get_delta`（`:388`）→ `delta_to_ns`（`:389`） | **ERROR** | **按规则 19 回源码核对"写了概述没逐字引用"的段落** |
| 3 | `run_local_timers()` 漏了两件事：它还调 `hrtimer_run_queues()`（`:2047`），而且 NO_HZ 下要**再查一次 deferrable base** | 遗漏 | 同上，打开 `:2036-2075` 逐行读 |
| 4 | "`flags` 的三个可选值"下面列了 4 行（含默认 0） | 数字不一致 | **填表时格子数对不上** |
| 5 | time namespace 从"2 个时基"改为"3 个" | 数字错 | 打开 `switch` 逐个数（§6.5） |
| 6 | alarmtimer 消费者从 9 改为 2 | **计数虚高 4.5 倍** | 逐个打开命中文件（§6.3） |
| 7 | `arm,arch_timer.yaml:NNN` 触发 `check_line_refs.py` 的 UNRESOLVED | 工具触发条件 | 脚本报告（§8.3） |
| 8 | 06 篇引用 of 那套的章节号被 `check_xrefs.py` 误判 | 工具假阳性 | 脚本报告（§8.2） |

**第 1 条最值得记**：它是"**读注释读反了**"这一类错误。
`TIMER_IRQSAFE` 的 kerneldoc 说"irqsafe 定时器**可以**安全地从 IRQ 处理函数里等待完成"，
我当成了"从 IRQ 里等会死锁"的警告。
⇒ **同一段文字既可以读成"允许"也可以读成"禁止"，取决于你带着什么预期去读。**
真正的死锁说明在另一个地方（`kernel/time/timer.c:1584-1624`），而且**内容更强**
（"即使那把锁和这个定时器毫无关系"）。

### §7.4 ⚠️ 本轮自己新产生的问题

按 skill 第 15 条（自审的对象必须包含自审这一轮新写的文字）：

- 写 §3.2 时留下了一处**刻意的口径不一致**：逐文件表列 23 个文件，
  但"总计 138"用的是 `kernel/time/*.c` 通配（含测试文件）。
  ⇒ **已在表下显式说明**，并说明为什么结果仍然一致（测试文件的导出符号数为 0）。
  **不说明就是一处对不上账的数字。**
- 写 §3.4 时差点把 tracepoint 数成 **15**——因为
  `grep -cE '^(TRACE_EVENT|DEFINE_EVENT|DECLARE_EVENT_CLASS)\('` 会把
  2 个 `DECLARE_EVENT_CLASS` 模板算进去。
  ⇒ **已改成只数 `TRACE_EVENT|DEFINE_EVENT`，并在表下写明为什么。**
- ⚠️§2.6 的最后一条（`sched_clock` 全系统只能有一个）**在写全称断言表的时候
  才发现自己没验证过**，当场降级到 §5.3 的推断层。
  ⇒ **这正是 skill 规则 17 说的"填格子会让空缺掉出来"** ——
  流水的正文里这句话可以一直混过去。

### §7.5 定稿后重跑的脚本数字

| 指标 | 值 |
|---|---|
| `check_line_refs.py` 扫到的唯一 `文件:行号` 引用 | 见下方重跑命令的输出 |
| `check_xrefs.py` 命中的跨篇引用 | 同上 |
| 忽略项 | 0 / 0 |

⚠️ **这两个数字故意不写死**：本篇自己就在被这两个脚本扫描，
把数字写成"扫描 NNN 条"的原文格式，下一轮会连着这句话一起再解析。

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
python3 $S/check_xrefs.py     output/timer
python3 $S/check_line_refs.py output/timer .
```

**验收标准**：两条都必须报"干净 / 0 失效"。

---

## §8 工具坑

### §8.1 `check_doc_coverage.sh` 的假通过：现在有三种变体

**这是本套对 skill 最直接的一条回写建议**。三套文档集各踩了一种：

| 变体 | 现象 | 哪套踩了 | 怎么发现 |
|---|---|---|---|
| (a) **文件名带连字符** | 目录找到了，主文档名不匹配 `*<subsys>*` | pinctrl（`pin-control.rst`） | 手工 `ls Documentation/driver-api/` |
| (b) **目录名完全不同** | 文档按"这件事"命名，不按代码目录名 | of（`Documentation/devicetree/`） | 想一想这个子系统的"人类名字"是什么 |
| (c) **只差一个复数 `s`** | **目录名几乎一样** | **timer（`Documentation/timers/`）** | **最容易漏——因为看起来就该匹配上** |
| (d) 真的没有 | 该子系统确实没有官方文档 | cdev、mailbox、reset | 三种都试过之后 |

**判别流程建议**（回写 skill 规则 28）：

```bash
SUB=timer
ls -d Documentation/$SUB Documentation/${SUB}s Documentation/driver-api/$SUB* 2>/dev/null
ls Documentation/ | grep -i "$SUB"        # 模糊匹配一遍
# 都没有时，再想"这个子系统的人类名字"（of→devicetree, cdev→char device）
```

⚠️ **(c) 和 (d) 的输出一模一样，但结论天差地别**——
timer 这套如果按 (d) 收工，00 §2 那整节背景就写不出来（§6.6）。

### §8.2 跨文档集的章节号引用会被判失效，而且**不该登记忽略**

本套 06 篇有一处在讲 `of_property_read_u32()` 的错误码时，
引用了 **of 那套文档**的一个小节。`check_xrefs.py` 按"同一行里最近的文档名"归属，
会拿去**本套**的对应篇里找，必然报失效。

**处置：消除触发条件**（skill 规则 27）——把章节号删掉，改成按主题指路
（"of 那套文档的 API 篇专门有一小节讲这个错误码陷阱"）。
⚠️ **不要**改成"对方 NN 篇 §X"——那仍然是一个可被解析的编号模式。

**of 那套也踩了同一个坑**（两处引用 remoteproc 那套的小节号），处置相同。
⇒ **这说明只要文档集之间开始互相引用，这个假阳性就会反复出现**，
建议在 skill 里把"跨文档集引用一律不写对方章节号"定成硬规则。

### §8.3 `check_line_refs.py` 按逗号切基名 ⇒ 文件名带逗号必然 UNRESOLVED

**本套踩到的具体形态**：DT 绑定文件叫
`Documentation/devicetree/bindings/timer/arm,arch_timer.yaml`。
写成 `<路径>:105-113` 时，脚本按逗号切基名后只剩 `arch_timer.yaml`，
消歧表里找不到 ⇒ **报 UNRESOLVED**。

⚠️ **`.lineref-ignore` 救不了这个** —— 读了脚本才确认：
忽略文件只作用于**越界项**（`oor`），UNRESOLVED 是无条件上报的。

**处置：消除触发条件** —— 把这 5 处的 `:行号` 全部换成 **YAML 键名**
（`required:` 段、`always-on:` 段的 `description` …）。
**键名本身就能定位，而且比行号更抗版本变化。**

⇒ **回写 skill 建议**：凡是引用 `Documentation/devicetree/bindings/` 下的
yaml（文件名普遍带逗号），**一律用键名而不是行号**。

### §8.4 99 篇的三个自污染陷阱（本篇全部绕过）

| 陷阱 | 机理 | 本篇怎么绕 |
|---|---|---|
| 引述脚本的失败行 | 下一轮会把引述再解析一次 | §7.5 只描述"必须报干净"，不抄失败行原文格式 |
| 抄 `grep -c` 的原生输出 | `文件名:数字` 会被行号检查器当成越界行号 | 全篇统计一律写成表格两列或 `文件 → 数字` |
| 举例说明"失效引用长什么样" | 举的例子本身就是失效引用 | §8.2 用文字描述，不写可解析的编号模式 |

### §8.5 本套遇到的两个"同名/子串"计数陷阱

| 陷阱 | 实例 | 正确口径 |
|---|---|---|
| **同名函数指针字段** | nouveau 的 `alarm_init` 是 `struct nvkm_timer_func` 的字段（§6.3） | **按 `#include <头文件>` 数** |
| **子串包含** | `-ENOTSUPP` 会被 `-EOPNOTSUPP` 命中（本子系统两者分别是 0 和 10） | **必须 `grep -w`** |

⇒ **这两条印证 skill 规则 30：计数错误三个脚本一个都抓不到。**

### §8.6 环境坑速查

| 坑 | 现象 | 处置 |
|---|---|---|
| 单次写入过大 | 一次写 8k+ 输出 token 会撞网关超时，整轮作废 | **每篇拆 2–3 次写入**（本套 10 篇都这么做的） |
| 上下文撞 1M 硬上限 | 会话**永久** 400，任何输入都在十几秒内失败，resume 修不回来 | 跑到 ~700k 主动新开会话，靠 `output/PROGRESS.md` 台账续跑 |
| ⚠️ 脚本的第二个参数是**内核树根** | 在 `output/timer/` 里跑 `check_line_refs.py . ..` 会把 `output/` 当内核根 ⇒ **一堆假 UNRESOLVED** | **一律在内核树根下跑**：`python3 $S/check_line_refs.py output/timer .` |
| 源码树只读 | 只有 `output/` 可写 | 所有产出进 `output/timer/` |

---

## §9 rule-29 对照：本套没有对照集

```bash
ls output/qoder/timer
# → No such file or directory
```

**`output/qoder/` 下只有 clk / dma / gpio / irq / pinctrl / spi 六套，没有 timer**
⇒ 本套**没有做**双套独立对照。

⇒ **这意味着什么**：rule-29 的核心价值是"两套独立产出写了同一句错话
⇒ 源码在这里本身有歧义"。本套缺这个信号，只能靠自审。

**§7.3 那八条修正里，有三条正是 rule-29 最擅长抓的类型**：
① 第 1 条（`TIMER_IRQSAFE` 注释读反）——**独立产出很可能同样读反**；
② 第 5 条（time namespace 时基数）——不打开 `switch` 就会少算；
③ 第 6 条（alarmtimer 计数虚高）——同名符号是通用陷阱。

⇒ **没有对照集的子系统，"回源码逐字读 + 填格子"这两步不能省。**
本套那三条全部是靠这两步抓到的，**没有一条来自重读正文**。

---

## §10 这套文档怎么用

| 你的处境 | 先读 |
|---|---|
| 完全不懂时间子系统 | 08 篇 → 00 §2 → 02 §1.0 |
| 要在驱动里定时/延时/读时间 | 05 §1 → 01 §2–§6 |
| 要写平台定时器驱动 | 05 §2 §3 → 01 §7 §8 → 06 篇 |
| 定时器不到点 / 时间跑偏 | 07 §6 → 07 §2.2 §3 |
| 深度 idle 后系统不醒 | 06 §5.2 → 04 §1 |
| 要开 PREEMPT_RT | 05 §1.4 → 03 §B2 → 08 §8 → **本篇 §5.3** |
| 要改 timer 核心本身 | 02 → 03 → 04 → **本篇 §5** |
| 隔几个月回来复习 | **只读 00 篇** |

### §10.1 本篇最该记住的四条

1. ⚠️ **这棵树的 timer 被改过（3 处），而且 arm64 开得起 PREEMPT_RT。**
   ⇒ **把本套用到别的 6.1.y 树上之前，必须重跑 §1.1 的基线核对**（§5.4）
2. ⚠️ **RT 相关的行为论断全部是推断，没有实测**（§5.3）——
   包括"`_HARD` 回调里拿 `spinlock_t` 会炸"这条最重要的建议。
   **逻辑很硬，但要依赖它做架构决策请自己在 RT 内核上验一次。**
3. **两个"名字骗人"的地方**：`/proc/timer_list` 不含 `timer_list`（§6.7）；
   `kernel/time/` 里的两个 `bus_type` 不是总线（§1.3）
4. **三个计数口径必须按本篇写的方式数**，否则会虚高：
   alarmtimer 按头文件（§6.3）、tick 广播按 inline 包装（§6.4）、
   tracepoint 不数 `DECLARE_EVENT_CLASS`（§3.4）
