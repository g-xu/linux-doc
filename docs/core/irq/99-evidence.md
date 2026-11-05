---
title: 取证附录
---

# irq 99：取证、方法与未验证项

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> 本篇的定位：**让读者知道哪些结论可以直接用、哪些要自己再确认一遍**。
> 所有数字都配了可重跑的一条命令；每轮 review 后全部重跑。
>
> ⚠️ 提到别处的错误引用时**用文字描述，不写成 `文件:行号` 的形式**，
> 否则会污染本篇自己的引用集（§8.4）。

---

## §1 基线

### §1.1 树的状态：`interrupt.h` 有一处本地补丁

| 项 | 值 |
|---|---|
| HEAD | `f6e38ae624cf` "Linux 6.1.158" |
| 分支 | `linux-6.1.158` |

```bash
git status --porcelain -- \
  kernel/irq include/linux/irq.h include/linux/irqdomain.h \
  include/linux/irqdesc.h include/linux/irqchip.h \
  drivers/irqchip drivers/of/irq.c include/linux/interrupt.h
# → ` M include/linux/interrupt.h`   （只有这一个）
```

| 路径 | 状态 |
|---|---|
| `kernel/irq/`（全部 25 个文件） | **干净** |
| `include/linux/irq.h` · `irqdomain.h` · `irqdesc.h` · `irqchip.h` | **干净** |
| `drivers/irqchip/`（129 个文件） | **干净** |
| `drivers/of/irq.c` | **干净** |
| ⚠️`include/linux/interrupt.h` | **有 29 行本地补丁** |

**补丁的内容与位置**（`git diff` 原文摘要）：

| 项 | 说明 |
|---|---|
| 性质 | **PREEMPT_RT 相关** |
| 加了什么 | `DECLARE_PER_CPU(struct task_struct *, timersd)` · `DECLARE_PER_CPU(unsigned long, pending_timer_softirq)` · `raise_timer_softirq()` · `raise_hrtimer_softirq()` · `local_pending_timers()`，各带 `CONFIG_PREEMPT_RT` 和非 RT 两个分支 |
| 插入点 | **`raise_softirq()` 声明之后、`DECLARE_PER_CPU(…, ksoftirqd)` 之前** |
| 行数 | +29，无删除 |
| ⇒ 后果 | **该插入点之后的 `interrupt.h` 行号，比上游 6.1.158 大 29** |

**本套的处理方式**：引用的一律是**当前树**的行号（读者面对的就是这棵树），
并在 01 篇篇首、05 篇篇首、00 篇篇首各点明一次。

⚠️ **一个重要的缓解事实**：补丁插在 **softirq 区**，
而本套引用的 `interrupt.h` 内容（`request_irq` 家族、`disable_irq` 家族、
亲和性 API、`struct irqaction`、`IRQF_*`）**全部在插入点之前**。

| 本套引用的 `interrupt.h` 最大行号 | 补丁插入点 |
|---|---|
| 441（`disable_irq_nosync_lockdep`） | 612 |

⇒ **本套 `interrupt.h` 的所有行号引用都在未受影响的区域**，
和上游 6.1.158 一致。可重跑验证：
```bash
grep -n 'CONFIG_PREEMPT_RT' include/linux/interrupt.h | head    # 补丁那个 #ifdef 在 612
```

顺带说清：这个补丁改的是 **softirq**，而 softirq **不在本套覆盖范围内**（§2.7）。
⇒ 它**不影响本套任何一条结论**，只影响行号。

### §1.2 规模（决定"读全"与"读符号表"的分界）

| 文件 | 行数 | 本套的证据等级 |
|---|---|---|
| `kernel/irq/manage.c` | 2934 | 一等（关键路径逐段） |
| `kernel/irq/irqdomain.c` | 2006 | 一等（关键路径逐段） |
| `kernel/irq/chip.c` | 1607 | 一等（关键路径逐段） |
| `kernel/irq/msi.c` | 1042 | 二等（只读 ops 表和入口） |
| `kernel/irq/irqdesc.c` | 996 | 一等 |
| `kernel/irq/timings.c` | 958 | **三等**（只读函数名，arm64 默认不开） |
| `kernel/irq/generic-chip.c` | 668 | 二等 |
| `kernel/irq/proc.c` | 538 | 一等（`show_interrupts` 全文读） |
| `kernel/irq/internals.h` | 513 | 一等（状态位与内部契约） |
| `kernel/irq/matrix.c` | 513 | **三等**（x86 专用，arm64 不开） |
| `kernel/irq/spurious.c` | 478 | 二等 |
| `kernel/irq/ipi.c` | 345 | **三等**（arm64 不走） |
| `kernel/irq/devres.c` | 284 | 一等（全文 284 行） |
| `kernel/irq/debugfs.c` | 261 | 一等（全文 261 行） |
| `kernel/irq/pm.c` | 260 | 二等 |
| `kernel/irq/irq_sim.c` | 250 | 二等（读了全部函数名与 ops） |
| `kernel/irq/handle.c` | 242 | **一等（全文 242 行逐行读）** |
| `kernel/irq/cpuhotplug.c` | 233 | 二等 |
| `kernel/irq/settings.h` | 188 | 一等 |
| `kernel/irq/resend.c` | 184 | 一等 |
| `kernel/irq/autoprobe.c` | 184 | **三等**（ISA 自动探测，arm64 不开） |
| `kernel/irq/affinity.c` | 128 | 二等 |
| `kernel/irq/migration.c` | 119 | 二等 |
| `kernel/irq/dummychip.c` | 64 | 一等（全文 64 行） |
| `kernel/irq/debug.h` | 49 | 一等（全文，含那条 `FIXME`） |
| **合计** | **15044**（22 个 `.c` = 14294 行 + 3 个 `.h` = 750 行） | —— |

```bash
printf ".c=%s 行=%s  .h=%s 行=%s\n" \
  "$(ls kernel/irq/*.c|wc -l)" "$(cat kernel/irq/*.c|wc -l)" \
  "$(ls kernel/irq/*.h|wc -l)" "$(cat kernel/irq/*.h|wc -l)"
```

头文件：

| 文件 | 行数 | 等级 |
|---|---|---|
| `include/linux/irq.h` | 1278 | 一等（结构体、四组 flag、全部 API 声明逐条核） |
| ⚠️ `include/linux/interrupt.h` | 867（**含 29 行本地补丁**） | 一等（消费者 API 部分） |
| `include/linux/irqdomain.h` | 641 | 一等 |
| `include/linux/irqdesc.h` | 251 | 一等 |
| `include/linux/irqchip/arm-gic-v3.h` | 661 | **三等**（寄存器定义，只查了个别常量名） |
| `include/linux/irqchip.h` · `include/linux/of_irq.h` · `include/linux/irqchip/chained_irq.h` | —— | 一等（全部 API 声明） |
| `include/linux/msi.h` | —— | 二等（只读 API 声明） |
| `include/trace/events/irq.h` | —— | 一等（五个事件全读） |
| `include/trace/events/irq_matrix.h` | —— | 三等（只数了 12 个事件） |

驱动侧：

| 文件 | 行数 | 等级 |
|---|---|---|
| `drivers/irqchip/irq-gic-v3.c` | 2584 | 二等（读了 `translate` / `alloc` / `gic_handle_irq` / `gic_init_bases` 的关键片段） |
| `drivers/irqchip/irq-al-fic.c` | 287 | 二等（读了前 60 行 + 全部关键调用点，作为 05 §3 的原型） |
| `drivers/irqchip/irq-gic-v3-its.c` | 5608 | **三等**（只讲契约，一行没读） |
| `drivers/irqchip/irq-gic.c`（v2） | 1764 | 三等 |
| `drivers/irqchip/irq-gic-v3-mbi.c` | 335 | 三等（只确认存在） |
| 其余 **124** 个 | —— | 不读，只出现在统计里 |
| `drivers/of/irq.c` | —— | 二等（读了全部属性消费点和函数入口，**未逐字读 `of_irq_parse_raw()` 的循环**） |
| `drivers/gpio/gpio-pca953x.c` | —— | 三等（只 grep 了 `irq_bus_lock` 那一带，作为 05 §4 的原型） |

| 项 | 值 | 命令 |
|---|---|---|
| `drivers/irqchip/` 个数 / 总行数 | **129** / **48150** | `ls drivers/irqchip/*.c \| wc -l` · `cat drivers/irqchip/*.c \| wc -l` |
| `kernel/irq/Kconfig` 的 `config` 项 | **30** | `grep -c '^config ' kernel/irq/Kconfig` |
| `Documentation/core-api/irq/` | 5 篇、**454** 行 | `wc -l Documentation/core-api/irq/*.rst` |
| DT 绑定文件 | **113**（49 `.yaml` + 63 `.txt` + 1 个其它） | `ls Documentation/devicetree/bindings/interrupt-controller/ \| wc -l` |

### §1.3 子系统类型判定

| 判据 | 命令 | 结果 |
|---|---|---|
| 有 `struct bus_type` 吗 | `grep -c 'struct bus_type' kernel/irq/*.c include/linux/irq*.h` | **全部为 0** |
| 读 DT 属性吗 | `grep -rn 'of_property_read\|of_get_property\|of_parse_phandle' kernel/irq/` | **无输出** |
| 有 tracepoint 吗 | `ls include/trace/events/ \| grep -i irq` | **有**（`irq.h` · `irq_matrix.h` · `preemptirq.h`） |

⇒ **判定为「框架型」子系统**，但有两处特殊性（00 §7.5）：
① 一级控制器**不在设备模型里**（02 §2.2、08 §9）；
② **一条 DT 属性都不读**（06 §1.1）—— 这比 remoteproc 那套的"读 1 条"还极端。

按 skill 的适配规则：02 篇画"核心抽象 ↔ 三类消费者"图（02 §2.1）；
03 主轴是时间线；04 篇按十一个独立框架组织；06 篇正常写（有 DT 绑定）。

### §1.4 官方文档登记（三等证据）

| 文件 | 行数 | 读了吗 | 内容纳入了哪里 |
|---|---|---|---|
| `Documentation/core-api/irq/irq-domain.rst` | 297 | 读了（通读） | 02 §5.5 的四种反向映射、03 §B4 的两阶段、08 §1/§8 |
| `Documentation/core-api/irq/concepts.rst` | 24 | 读了 | 02 §1.0.E 的术语区分 |
| `Documentation/core-api/irq/irq-affinity.rst` | 70 | 读了 | 04 §5.2 的三组亲和性文件 |
| `Documentation/core-api/irq/irqflags-tracing.rst` | 52 | 只扫了标题 | —— |
| `Documentation/core-api/irq/index.rst` | 11 | 读了 | —— |
| `Documentation/devicetree/bindings/interrupt-controller/arm,gic-v3.yaml` | —— | 读了属性清单 | 06 §7 的实例校对 |
| 其余 **112** 个 DT 绑定（48 个 `.yaml` + 63 个 `.txt` + 1 个其它） | —— | **没读** | 见 §2.3 |

**`irq-domain.rst`（297 行）是全 `Documentation/core-api/irq/` 的主体**（454 行里的 65%）——
说明上游也认为**域是这个子系统最需要解释的东西**。本套 08 §1/§2/§8 三节都在讲它。

### §1.5 ⚠️ 文档与代码冲突 / 覆盖不足之处

| 主题 | 官方文档 | 代码现状 |
|---|---|---|
| `IRQCHIP_IMMUTABLE` | **一句没提** | `include/linux/irq.h:570`；6.x 新加，改变了 gpiolib 和 irqchip 的协作方式（00 §7.3） |
| pseudo-NMI（`request_nmi()` 家族） | **一句没提** | `include/linux/interrupt.h:180` 起一整族 + `handle_fasteoi_nmi()`（01 §4.3） |
| 十四个流处理器 | `concepts.rst` 只泛泛说了"flow handler" | 实际有 **14 个**，各自适用不同硬件（01 §4.3） |
| `irq_bus_lock` / `irq_bus_sync_unlock` | 未见系统说明 | 慢总线支持的核心（02 §5.4、05 §4） |

⇒ 本套的 01 §4.3、05 §4、08 §9 这几节**完全是从代码读出来的**，
没有官方文档背书 ⇒ 列入 §5.3「推断而非观察」。

---

## §2 证据等级

### §2.1 一等证据：逐段/逐行读过源码，论断直接引原文

| 文件 | 详读范围 |
|---|---|
| `kernel/irq/handle.c` | **全文 242 行** —— `handle_bad_irq`(33) · `no_action`(46) · `warn_no_thread`(52) · `__irq_wake_thread`(61) · `__handle_irq_event_percpu`(139，逐行) · `handle_irq_event_percpu`(189) · `handle_irq_event`(202，逐行) · `set_handle_irq`(218) · `generic_handle_arch_irq`(232) |
| `kernel/irq/manage.c` | 全部主流程：`__synchronize_hardirq`(38)–`synchronize_irq`(126) · 亲和性家族(146-650) · disable/enable 家族(681-841) · `irq_set_irq_wake`(879) · `can_request_irq`(928) · `__irq_set_trigger`(946) · 三个内部 handler(1023-1044) · `irq_finalize_oneshot`(1074) · 线程家族(1200-1358) · `irq_request_resources`(1397) · NMI 家族(1414-1438) · `__setup_irq`(1497 起的检查段) · `__free_irq`(1866 起) · `free_irq`(2018) · `request_threaded_irq`(2138) |
| `kernel/irq/irqdomain.c` | `__irq_domain_add`(245) · `irq_domain_remove`(269) · associate 家族(588-647) · `irq_create_mapping_affinity`(745) · `irq_create_fwspec_mapping`(807) · `irq_dispose_mapping`(920) · `__irq_resolve_mapping`(949) · `irq_domain_create_hierarchy`(1149) · alloc/free 家族(1329-1554, 1755-1799) · activate 家族(1821-1869) · debugfs(1949-2000) |
| `kernel/irq/chip.c` | startup/shutdown/enable/disable(235-431) · `irq_may_run`(504，全文) · 十三个流处理器的签名 + `handle_fasteoi_irq`(689，逐行) · `__irq_do_set_handler`(973 起的 chained 分支，逐行) · 五处 `-ENOSYS`(1436-1558) |
| `kernel/irq/irqdesc.c` | 两套实现的分界(136/555/639) · `irq_to_desc` 两个版本(354/589) · `alloc_desc`(392) · `irq_kobj_release`(428) · `alloc_descs`(472) · 两个 `early_irq_init`(526/565) · `generic_handle_*` 四个(665-724) · `__irq_alloc_descs`(791) · sysfs(280-334) |
| `kernel/irq/proc.c` | `show_interrupts()`(460-537) **逐行读** + 全部文件创建点(319-436) |
| `kernel/irq/debugfs.c` | **全文 261 行** |
| `kernel/irq/devres.c` | **全文 284 行** |
| `kernel/irq/dummychip.c` | **全文 64 行** |
| `kernel/irq/resend.c` | 全部函数(25-183) |
| `kernel/irq/internals.h` | `istate` 宏(20) · 11 个 `IRQS_*`(58-68) |
| `kernel/irq/settings.h` | 访问器与 `_IRQ_*` 别名 |
| `kernel/irq/debug.h` | **全文 49 行**（含 `___PD` 那条 `FIXME`） |
| `include/linux/irq.h` | `irq_common_data`(147) · `irq_data`(179) · 四组 flag(78-103, 226-248, 559-570) · `irq_chip`(488-545) · 全部 API 声明 |
| `include/linux/irqdesc.h` | `irq_desc`(55-107) 逐字段 |
| `include/linux/irqdomain.h` | `irq_domain_ops`(107) · `irq_domain`(164) · flags(190 起) · 全部 API 声明 |
| `include/linux/interrupt.h` | `irqaction`(118) · 消费者 API(134-506) 逐条 |
| `include/trace/events/irq.h` | 五个事件全读 |
| `include/uapi/linux/irqnr.h` | **有一个 uapi 头文件，但它整个只有一段注释** —— 全文 4 行，原话是「There isn't anything here anymore, but the file must not be empty or patch will delete it.」⇒ irq 子系统**对用户态没有任何 ioctl / uapi 类型**，接口全部走 procfs / debugfs（04 §5 §6） |

### §2.2 二等证据：只读了符号表 / 只读了关键片段

| 对象 | 读到什么程度 | 影响哪些论断 |
|---|---|---|
| `kernel/irq/msi.c` | 读了 `msi_domain_ops`(575) 及其四个成员(510-562)、`msi_create_irq_domain`(679)、`msi_domain_ops_init`(601) 和全部导出符号行。**没读 `__msi_domain_alloc_irqs()` 的主体** | 04 §2 |
| `kernel/irq/generic-chip.c` | 读了十个 `irq_gc_*` 的签名、`irq_generic_chip_ops`(454)、`__irq_alloc_domain_generic_chips`(285)、`-ENOTSUPP`(402) 一带。**没读寄存器读改写的细节** | 04 §1 |
| `kernel/irq/spurious.c` | 读了全部函数签名 + `nobody cared`(202) 一带。**没逐字读 `note_interrupt()` 的阈值算法** | 04 §4、08 §7 |
| `kernel/irq/pm.c` · `cpuhotplug.c` · `migration.c` · `affinity.c` · `irq_sim.c` | 只读了函数签名和调用关系 | 04 §7、§10 · 03 §A10 |
| `drivers/irqchip/irq-gic-v3.c` | 读了 `gic_irq_domain_translate`(1530，**逐行**)、`gic_irq_domain_ops`(1687)、`gic_init_bases`(1930 的域创建与 `set_handle_irq`)、`gic_of_init`(2185)、`IRQCHIP_DECLARE`(2255)、`gic_handle_irq`(834 的签名)。**没读 mask/unmask/set_affinity 的寄存器操作** | 03 §A2/§A3 · 06 §3 |
| `drivers/irqchip/irq-al-fic.c` | 读了前 60 行 + 全部关键调用点(107-174, 287) | 05 §3 |
| `drivers/of/irq.c` | 读了全部属性消费点(70-589) 和函数入口(39-753)。**没有逐字读 `of_irq_parse_raw()` 的循环** | 06 全篇 |
| `include/linux/irqchip/chained_irq.h` | 只确认了两个 inline 函数的位置(16/32)，**没读函数体** | 05 §3.4、08 §2 |

### §2.3 ⚠️ `check_doc_coverage.sh` 的两次运行与处置

**运行 1：`core-api/irq`**（框架文档）—— 5 篇全部登记在 §1.4，
其中 `irqflags-tracing.rst` 只扫了标题 ⇒ 处置：**(a) 内容没覆盖**，
理由是它讲的是 lockdep 的 irqflags 追踪，属于 lockdep 子系统而非 irq 核心。

**运行 2：`interrupt-controller`**（DT 绑定）—— 该目录共 **113** 个文件，
其中 **49** 个 `.yaml`、**63** 个 `.txt`、1 个其它；**脚本只看得到那 49 个 `.yaml`**（§8.1）。
结果：49 篇里被引用 **3** 篇（`arm,gic-v3` · `arm,gic` · `msi-controller`）。
本套实际只**读过** `arm,gic-v3.yaml` 一个（§1.4），另两篇是正文提到名字时被子串匹配上的
—— ⚠️ 这正好又一次说明"引用次数"不可信（§8.1）。
⇒ 处置：**(a) 内容没覆盖**，理由写进 06 §10 的覆盖情况表：
**本套 06 篇的定位是"核心 0 属性 + `drivers/of/irq.c` 全部消费点 + GIC v2/v3 的 cell 语义"，
不是"113 个绑定的逐篇导读"**。

⚠️ 脚本的两个已知行为（沿用 remoteproc 那一轮的结论，§8.1）：
① 「引用次数」是 basename 子串匹配，**只有"是不是 0"可信**；
② **只认 `.yaml`**，`interrupt-controller/` 下有大量 `.txt` 老绑定看不到。

### §2.4 索引工具的使用与盲区

| 用途 | 用了吗 |
|---|---|
| 导航 | 用了 |
| 取证 | **没用** —— 所有行号都是 `sed -n` / `grep -n` 在真实树上取的 |

本子系统撞上索引工具的**三个盲区**：

| 盲区 | 本子系统的体现 |
|---|---|
| **函数指针间接调用** | `irq_chip` 的 33 个回调 + `irq_domain_ops` 的 11 个回调**全部**是间接调用。"谁调用了 `chip->irq_mask`"索引答不出来 |
| **同名函数的条件编译** | `irq_to_desc()` / `early_irq_init()` / `irq_domain_set_info()` / `irq_sw_resend()` / `irq_setup_affinity()` / `irq_validate_effective_affinity()` **各有两个定义**（03 §C3）。索引给出两个符号，必须自己判断走哪个分支 |
| **宏改名** | `#define istate core_internal_state__do_not_mess_with_it`（`kernel/irq/internals.h:20`）—— grep `istate` 找不到字段定义（03 §B2） |

⇒ 这也是 03 篇的时间线次序**逐条用"谁调用谁"验证过**的原因。

### §2.5 逐条 `sed` 核对（自审第 19 条）

本套沿用 remoteproc 那一轮定下的流程：
① 写正文前用一次 Bash 建"行号:符号"台账；② 写正文时只查台账；
③ 每篇写完立刻跑 `check_line_refs.py`；④ 有疑问的逐条 `sed -n` 打开看。

⚠️ 第 4 步的纪律：**`sed` 的行号规格一律按升序列** ——
`sed -n '298p;283p'` 按**文件升序**输出，不按你列的顺序（§8.2）。
本轮**没有再犯**这个错。

### §2.6 全称断言的验证

| 论断 | 命令 | 结果 |
|---|---|---|
| 没有 `struct bus_type` | `grep -c 'struct bus_type' kernel/irq/*.c include/linux/irq*.h` | 全 0 |
| 核心不读任何 DT 属性 | `grep -rn 'of_property_read\|of_get_property\|of_parse_phandle' kernel/irq/` | **无输出** |
| 零 `TP_CONDITION` | `grep -c TP_CONDITION include/trace/events/irq.h` | **0** |
| `-EPROBE_DEFER` 在 `kernel/irq/` 里 0 次 | `grep -rc 'EPROBE_DEFER' kernel/irq/*.c` | 全 0 |
| `CONFIG_DEBUG_SHIRQ_FIXME` 在任何 Kconfig 里都不存在 | 见 03 §C1.3 的两条命令 | **确认为死代码** |
| 十四个流处理器 | `grep -c 'extern void handle_' include/linux/irq.h` | **14**（`include/linux/irq.h:639-658`） |
| 六个函数各有两个条件编译定义 | `grep -c '^[a-z].*<name>(\|^static.*<name>(' kernel/irq/*.c` | `irq_to_desc` / `early_irq_init` / `irq_domain_set_info` / `irq_sw_resend` / `irq_setup_affinity` / `irq_validate_effective_affinity` **各 2** |
| `trace_irq_handler_entry` 的四个触发点 | `grep -rn 'trace_irq_handler_entry' kernel/irq/*.c` | 4 处（07 §3.2） |
| 有一个 uapi 头文件但是空壳 | `ls include/uapi/linux/ \| grep -i irq` · `wc -l include/uapi/linux/irqnr.h` | `irqnr.h`，**4 行**，无实质内容 |

### §2.7 裁掉了什么（arm64-only 的代价）

| 裁掉的内容 | 为什么 | 影响哪一篇 |
|---|---|---|
| **ACPI（MADT / GSI 路径）** | 用户明确要求不描述 | 06 §10 已标注；01 §4.1 的 `IRQCHIP_ACPI_DECLARE` 只提一句 |
| **softirq / tasklet** | 它们只是**同住 `interrupt.h`**，是另一套机制 | 00 §7.4 显式声明；⚠️ 本树的补丁改的正是这一段（§1.1） |
| `kernel/irq/matrix.c`（513 行） | **x86 专用**（per-CPU 向量分配），arm64 不开 | 04 §9 已标注；那 5 处 `-ENOSPC` 与 arm64 无关（07 §6.1） |
| `kernel/irq/ipi.c`（345 行） | arm64 的 IPI 走 GIC SGI，**不经过 `irq_domain`** | 04 §3 已标注 |
| `kernel/irq/autoprobe.c`（184 行） | ISA 中断自动探测，`GENERIC_IRQ_PROBE` **arm64 不开** | 01 §0.4 已标注 |
| `kernel/irq/timings.c`（958 行） | 只服务 cpuidle governor，arm64 默认不开 | 04 §8 已标注 |
| `DEPRECATED_IRQ_CPU_ONOFFLINE` 相关的两个回调 | arm64 不开 | 02 §5.4 已标注 |
| **GIC v2**（`irq-gic.c` 1764 行） | arm64 主力是 v3 | 只作对照 |
| **ITS 的实现**（5608 行） | 太大；MSI 只讲契约 | 04 §2.5 已标注 |
| 124 个非 GIC 的 irqchip 驱动 | 非 arm64 主流或非 arm64 | 只出现在统计里 |
| 112 个 DT 绑定 | 同上 | §2.3 已记录 |

⚠️ **裁剪不等于遗漏**：以上每一项都在对应篇目里显式标注了"本套不覆盖"。
⇒ **在非 arm64 平台上用这套文档，§2.7 这张表就是你要额外补读的清单。**

---

## §3 统计数字总表（每条都能一条命令重跑）

### §3.1 规模类

| 数字 | 值 | 命令 |
|---|---|---|
| `kernel/irq/*.c` 个数 / 行数 | **22** / **14294** | `ls kernel/irq/*.c \| wc -l` · `cat kernel/irq/*.c \| wc -l` |
| `kernel/irq/*.h` 个数 / 行数 | **3** / **750** | 同上换后缀 |
| `kernel/irq/` 合计 | **15044** | `wc -l kernel/irq/*.c kernel/irq/*.h \| tail -1` |
| `drivers/irqchip/*.c` 个数 / 行数 | **129** / **48150** | `ls drivers/irqchip/*.c \| wc -l` · `cat drivers/irqchip/*.c \| wc -l` |
| Kconfig `config` 项 | **30** | `grep -c '^config ' kernel/irq/Kconfig` |
| `Documentation/core-api/irq/` | 5 篇 **454** 行 | `wc -l Documentation/core-api/irq/*.rst \| tail -1` |
| DT 绑定文件 | **113** | `ls Documentation/devicetree/bindings/interrupt-controller/ \| wc -l` |
| 四个主头文件 | `irq.h` 1278 · `interrupt.h` 867 · `irqdomain.h` 641 · `irqdesc.h` 251 | `wc -l include/linux/irq.h include/linux/interrupt.h include/linux/irqdomain.h include/linux/irqdesc.h` |

**对比 remoteproc 那一套**：核心 5068 行、导出 33 个符号、DT 绑定 25 个。
**irq 在每个维度上都大 3–5 倍**，这是它 API 面积和术语密度都更高的直接原因。

### §3.2 API 类

| 数字 | 值 | 命令 |
|---|---|---|
| `kernel/irq/` 导出符号总数 | **151**（21 `EXPORT_SYMBOL` + 130 `_GPL`） | `grep -rho 'EXPORT_SYMBOL[_A-Z]*' kernel/irq/*.c \| sort \| uniq -c` |
| 逐文件（前五） | `irqdomain.c` **41** · `chip.c` 34 · `manage.c` 26 · `generic-chip.c` 14 · `irqdesc.c` 11 | `for f in kernel/irq/*.c; do n=$(grep -c EXPORT_SYMBOL $f); [ "$n" != 0 ] && echo "$n $f"; done \| sort -rn` |
| `struct irq_chip` 回调数 | **33**（+ `name` + `flags`） | `sed -n '488,545p' include/linux/irq.h \| grep -c '(\*'` |
| 流处理器 | **14** | `grep -c 'extern void handle_' include/linux/irq.h` |
| `struct irq_domain_ops` 成员 | **11**（5 基础 + 5 层级 + 1 debug） | `sed -n '107,131p' include/linux/irqdomain.h \| grep -c '(\*'` |
| `struct irq_data` 字段数 | **8** | `sed -n '179,191p' include/linux/irq.h` |

### §3.3 状态位类

| 组 | 个数 | 位置 |
|---|---|---|
| `IRQ_*`（`irq_desc.status_use_accessors`） | 触发类型 9 项 + 状态位 14 项 | `include/linux/irq.h:78-103` |
| `IRQD_*`（`irq_common_data`） | **22 个位** + 1 个掩码 | `include/linux/irq.h:226-248` |
| `IRQCHIP_*`（`irq_chip.flags`） | **12** | `include/linux/irq.h:559-570` |
| `IRQS_*`（`irq_desc.istate`，仅内部） | **11** | `kernel/irq/internals.h:58-68` |
| `IRQ_DOMAIN_FLAG_*` | 6+ | `include/linux/irqdomain.h:190` 起 |
| `IRQF_*`（`request_irq()` 的参数） | —— | `include/linux/interrupt.h` 前部 |

### §3.4 错误码类（07 §6.1 的原始数据）

| 项 | 值 |
|---|---|
| `kernel/irq/*.c` 的 `-E*` 出现次数 | **208** |
| `-EINVAL` | **121**（58%） |
| `-ENOMEM` / `-EBUSY` / `-ENOSYS` | 33 / 14 / 9 |
| `-ENOSPC` / `-ENOTCONN` / `-ENODEV` / `-EPERM` / `-EEXIST` | 7 / 6 / 4 / 3 / 2 |
| 九个单例 | `-ERANGE` · `-EOPNOTSUPP` · `-ENXIO` · `-ENOTSUPP` · `-ENOENT` · `-EIO` · `-EIDRM` · `-EFAULT` · `-EBADSLT` |
| 其中出现在函数文档注释里 | **5**（`kernel/irq/irqdesc.c:660` · `:701` · `:742` · `kernel/irq/irqdomain.c:608` · `:1259`） |
| 出现在**比较**里（不是返回） | ≥3（`kernel/irq/manage.c:318` · `kernel/irq/irqdomain.c:612` · `:1306`） |
| `-EPROBE_DEFER` | **0** —— 它来自 `drivers/of/irq.c`（01 §10.1） |

```bash
grep -rho '\-E[A-Z_]*' kernel/irq/*.c | sort | uniq -c | sort -rn
grep -rn '^[[:space:]]*\*.*-E[A-Z]' kernel/irq/*.c      # 找出那 5 处注释
```

### §3.5 可观测性类（07 §2 §3 的原始数据）

| 项 | 值 |
|---|---|
| `WARN_ON` | **76** |
| `WARN(` | **19** |
| `WARN_ON_ONCE` | 28 |
| `WARN_ONCE` | 3 |
| **WARN 家族合计** | **126** |
| `pr_err` | **14** |
| `pr_warn` / `pr_warn_once` | 18 / 1 |
| `pr_info` | 8 |
| `pr_debug` | **23**（其中 13 在 `irqdomain.c`） |
| `_ratelimited` | **2**（都在 `kernel/irq/cpuhotplug.c`） |
| tracepoint（`irq.h`） | **5**（2 irq + 3 softirq） |
| tracepoint（`irq_matrix.h`） | **12**（x86 专用） |
| `TP_CONDITION` | **0** |
| `trace_irq_handler_entry` 的触发点 | **4**（07 §3.2） |

```bash
for k in pr_err pr_warn pr_info pr_debug WARN_ON_ONCE WARN_ONCE pr_warn_once WARN_ON 'WARN(' _ratelimited; do
  printf "%-18s %s\n" "$k" "$(grep -roh "$k" kernel/irq/*.c | wc -l)"
done
grep -c 'DEFINE_EVENT\|TRACE_EVENT' include/trace/events/irq_matrix.h
grep -c TP_CONDITION include/trace/events/irq.h
```

**WARN 家族 126 vs `pr_err` 14** —— 这是本子系统最有信息量的一个比例（07 §2.1）：
**irq 核心认为"用错 API 就是内核 bug，应该打栈回溯"**。

### §3.6 源码设计债类（03 §C1 的原始数据）

| 标记 | 个数 | 位置 |
|---|---|---|
| `FIXME`（真注释） | **2** | `kernel/irq/ipi.c:246` · `kernel/irq/debug.h:8` |
| 名字里带 `FIXME` 的 **config 符号** | **1** | `kernel/irq/manage.c:2206` ⇒ **19 行死代码** |
| `TODO` / `XXX` / `REVISIT` / `HACK` | **0** | —— |

```bash
grep -rn 'TODO\|FIXME\|XXX\|REVISIT\|HACK' kernel/irq/*.c kernel/irq/*.h
grep -rn 'DEBUG_SHIRQ' --include=Kconfig --include='Kconfig.*' .    # → 只有 lib/Kconfig.debug:1000 的 DEBUG_SHIRQ
grep -rn 'DEBUG_SHIRQ_FIXME' --include=*.c --include=*.h .          # → 只有 kernel/irq/manage.c:2206
```

**15044 行只有 2 条真 `FIXME`** —— 代码成熟度极高（03 §C5）。

### §3.7 arm64 相关性

| 项 | 值 | 命令 |
|---|---|---|
| `kernel/irq/` 不读任何 DT 属性 | **无输出** | `grep -rn 'of_property_read\|of_get_property\|of_parse_phandle' kernel/irq/` |
| `interrupts-extended` 在 arm64 DTS 里的文件数 | **90** | `grep -rl 'interrupts-extended' arch/arm64/boot/dts/ \| wc -l` |
| arm64 上 GIC v3 的实例 | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5114` | 06 §7.1 |
| uapi 头文件 | `include/uapi/linux/irqnr.h`，**4 行、只有注释** | `wc -l include/uapi/linux/irqnr.h` |

---

## §4 工具版本

| 工具 | 版本 | ⚠️ 注意 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本纯 stdlib |
| GNU grep | 3.7（`/usr/bin/grep`） | 本套所有统计都用**绝对路径**调它 |
| 默认 `grep` | **ugrep 7.5.0** | ⚠️ **不认 `\s`**，而且**默认遵守 `.gitignore`** ⇒ 会漏文件 |
| ripgrep | 13.0.0 | 只用于粗筛 |
| git | 2.34.1 | —— |

⚠️ **ugrep 那两条差异是本套踩过最多次的环境坑**（沿用 remoteproc 那一轮的结论）：
- 用 `\s` 写正则会**静默匹配不到** ⇒ 统一用 POSIX `[[:space:]]`；
- ugrep 遵守 `.gitignore` ⇒ 统计"全树有几处"时**必须用 `/usr/bin/grep`**。

另一个环境限制：**递归 `grep -rn` 扫整个 `arch/arm64/boot/dts/` 会超过 2 分钟的 Bash 超时**。
本套的做法是先按 compatible 反查文件名（`grep -rl`），再对单个文件 `sed`。

---

## §5 未验证项（四层分层）

### §5.1 第一层：未编译、未运行

| 内容 | 说明 |
|---|---|
| **05 篇的全部五套代码骨架** | 篇首已声明。它们是从真实驱动提炼的结构，**没有 `make` 过一次** |
| 07 §3 的全部 ftrace / tracepoint 命令 | **没有在真机上跑过**。语法来自内核文档，路径与文件名来自源码 |
| 07 §5 的 `echo trigger` | **没有实测**。它需要 `CONFIG_GENERIC_IRQ_INJECTION`，本套只从代码推出行为 |
| 07 §7.1 的三个统计脚本 · §7.2 的字段解释 | 未运行 |
| 06 §8.1 的 `make dtbs_check` / `dtc -I dtb` | **没有跑过** |
| 05 §6 / 07 §8 的全部 shell 命令 | 未运行 |

### §5.2 第二层：只读了符号表 / 只读了片段

见 §2.2。受影响最大的四处：

| 论断 | 依赖的阅读深度 | 风险 |
|---|---|---|
| 04 §2「MSI 的 `.activate` 才是真正生效那一步」 | 读了 `msi_domain_activate`(510) 的签名和 `msi_domain_ops`(575)，**没读函数体** | 中 —— 结论也由 `Documentation/core-api/irq/irq-domain.rst` 和 ITS 的硬件模型（00 §2.8）双重支持 |
| 04 §4「乱中断检测的 100000 次阈值」 | **没逐字读 `note_interrupt()` 的算法** | 中 —— 阈值数字来自对该函数的通读印象，**未逐行核对** |
| 06 §5「`interrupt-map` 的 cell 结构」 | **没有逐字读 `of_irq_parse_raw()` 的循环** | 高 —— 结论主要来自 DT 规范和属性消费点的行号，**不是逐行推导** |
| 05 §4「gpiolib 的 `girq->threaded` 语义」 | 只 grep 了 `drivers/gpio/gpio-pca953x.c` 的几行 | 中 —— gpiolib 的实现不在本套范围 |

### §5.3 第三层：推断而非观察

| 论断 | 推断链 | 为什么没实测 |
|---|---|---|
| 03 §C1.3 的 **19 行死代码** | `CONFIG_DEBUG_SHIRQ_FIXME` 全树无 Kconfig 定义 ⇒ `#ifdef` 永假 | **推断链极短且两条命令可验**（§3.6）。**这是本套置信度最高的一条"设计债"** |
| 08 §5 / 03 §B3「lazy disable 会让某些硬件卡住」 | 代码事实（`disable_irq()` 只置位、`kernel/irq/chip.c:704-707` 才 mask）确定；**"某些硬件因此卡住"是推断** | 需要一块具体的会卡的硬件 |
| 06 §8.4「四个静默失效」 | 代码路径确定（每条都给了行号）；**"表现为中断永远不来且无日志"是推断** | 需要故意写错 DTS 再跑 |
| 05 §3.4「不调 `chained_irq_exit()` ⇒ 中断只来一次」 | GIC 是 fasteoi 型 + 不 EOI 就不再送（00 §2.7 的硬件事实） | 硬件手册级别的确定性，但**没有实测** |
| 01 §4.3「选错流处理器 ⇒ 中断风暴 / 丢中断」 | 流处理器的 mask/unmask 时序 + 电平/边沿的硬件行为 | 同上 |
| 01 §4.3 的**十四个流处理器各自的适用硬件** | **只读了函数体的结构，没有对每一个都找到真实使用者** | 六个"特殊场景"的（`untracked` / `edge_eoi` / 两个 `nmi` / `fasteoi_ack` / `fasteoi_mask`）**尤其要小心** —— 它们的"什么时候用"是从函数名和注释推的 |
| 02 §6.2「lazy disable 的 `IRQD_IRQ_DISABLED` / `MASKED` 分离」 | 两个位的定义 + `irq_disable()`(385) 的实现 | 代码确定，后果是推断 |
| 00 §2.5「Linux 基本不用 GIC 的优先级」 | **没有读 GIC 驱动设优先级的那段代码** | 这条来自对 GIC 架构和 Linux 中断模型的一般理解，**不是本树的取证** |
| 00 §2.4「Linux 默认不用 GIC 的 1-of-N 模式」 | 同上 | 同上 |

**这一层是本篇最该被读者盯住的**：
上表里**代码事实都可核对**（都给了行号），但**"会导致什么后果"多数是推断**。
⇒ 要在产品上依赖其中任何一条，请自己复现一次。

⚠️ **两条特别标注为"非本树取证"**：00 §2.4 和 §2.5 关于 GIC 优先级和
1-of-N 模式的说法，来自架构层面的一般理解，**没有在 `drivers/irqchip/irq-gic-v3.c` 里
逐行确认**。要依赖它们请读 GICv3 架构手册 + 那个文件的 `gic_dist_init()`。

### §5.4 第四层：版本时效性

| 项 | 说明 |
|---|---|
| 版本锚点 | 全部结论锚在 **6.1.158**（`f6e38ae624cf`） |
| ⚠️ **本树非纯上游** | `include/linux/interrupt.h` 有 29 行 PREEMPT_RT 补丁（§1.1）。⇒ **跨树引用本套的 `interrupt.h` 行号前先 diff** |
| 上游变化最快的部分 | MSI 那一套（6.x 期间 `msi_desc` 从数组改成 xarray、锁语义变严）；`IRQCHIP_IMMUTABLE` 与 gpiolib 的协作（§1.5） |
| 已知会变的具体点 | 流处理器个数（本版本 **14**）；`irq_chip` 回调数（本版本 **33**）；`IRQD_*` 位数（本版本 **22**） |

快速判断本套是否过期的三条命令：
```bash
grep -c 'extern void handle_' include/linux/irq.h                  # 不再是 14 ⇒ 重读 01 §4.3
sed -n '488,545p' include/linux/irq.h | grep -c '(\*'              # 不再是 33 ⇒ 重读 02 §5.4
grep -c 'struct bus_type' kernel/irq/*.c include/linux/irq*.h      # 不再是 0 ⇒ 整套定性都要改
```

---

## §6 交叉验证实例：初始假设被源码推翻的五条

> 这一节的价值在于**把结论的方向性钉准**。每条都是"我先以为是 A，验证后发现是 B"。

### §6.1 流处理器到底有几个？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 我从 `include/linux/irq.h` 里 grep `handle_.*_irq` 的**部分**结果出发，写成了「**七个**流处理器」 |
| **触发复核的动作** | 写 07 §3.2 时要找 `trace_irq_handler_entry` 的所有触发点，顺手 dump 了 `kernel/irq/chip.c` 的全部 `handle_*` 函数定义 |
| **结果** | **`kernel/irq/chip.c` 里有 13 个**，加 `kernel/irq/handle.c` 的 `handle_bad_irq` = **14 个**；`include/linux/irq.h:639-658` 全部声明了 |
| **漏掉的六个** | `handle_untracked_irq` · `handle_edge_eoi_irq` · `handle_fasteoi_nmi` · `handle_percpu_devid_fasteoi_nmi` · `handle_fasteoi_ack_irq` · `handle_fasteoi_mask_irq` |
| **修正范围** | 01 §4.3 整节重写（拆成"常用六个 / 兜底两个 / 特殊六个"三张表 + 导出情况）；02 §5 的机制表；03 §0.2 的分层图 |
| **可重跑** | `grep -c 'extern void handle_' include/linux/irq.h` → **14** |

**这是本套最大的一处修正**，而且它带出了一个新结论：
`handle_fasteoi_nmi` 的存在**证明 arm64 的 pseudo-NMI 是走正常 irq 框架的**（00 §2.5）——
这一点官方文档一句没提（§1.5）。

⚠️ 教训：**从头文件 grep 出来的"部分"结果不能当全集** ——
必须 `grep -c` 拿总数对一遍。

### §6.2 这个子系统靠 `pr_err` 说话还是靠 `WARN` 说话？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 第一遍普查我把 `pr_warn` 和 `WARN` **合并成一列**统计，结果看起来是"warn 类比 err 类多一点点" |
| **触发复核** | 写 07 §2.1 时觉得"warn/WARN=33"这个列名太含糊，拆开重跑 |
| **结果** | WARN 家族 **126**（76 + 19 + 28 + 3）vs `pr_err` **14** —— 差了 9 倍 |
| **修正后的结论** | 07 §2.1 的整节定性从"日志分布"改成 **「这个子系统靠 `WARN` 说话」**，并加了一条给读者的建议：**看到 irq 的 `WARNING:` 栈回溯不要当噪声，栈顶就是误用者** |
| **可重跑** | §3.5 那个 `for` 循环 |

⚠️ 教训：**统计时把语义不同的东西合并成一列，会得出方向相反的结论。**

### §6.3 `CONFIG_DEBUG_SHIRQ_FIXME` 是个正常的调试开关吗？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 在 `kernel/irq/manage.c` 里看到 `#ifdef CONFIG_DEBUG_SHIRQ_FIXME`，以为是"某个我没见过的调试选项" |
| **验证** | 在全树所有 `Kconfig*` 里搜 `DEBUG_SHIRQ` |
| **结果** | 只有 `lib/Kconfig.debug:1000` 的 **`DEBUG_SHIRQ`**（没有 `_FIXME` 后缀）；`DEBUG_SHIRQ_FIXME` 在**全树只出现一次**，就是那个 `#ifdef` |
| **修正后的结论** | 那 19 行是**永久死代码**（03 §C1.3）。⇒ 07 §5.4 因此加了一条警告：**"申请共享中断后立刻假触发一次 handler"这个能力在 6.1 上不存在** |
| **可重跑** | §3.6 的两条命令 |

这一条从"我不认识的选项"变成了"一条可验证的设计债" ——
**判据是"这个 config 符号在任何 Kconfig 里定义过吗"，不是"这段代码看起来对不对"。**

### §6.4 irq 子系统有 uapi 接口吗？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 写 §2.1 时我写了「本子系统没有 uapi 头文件」 |
| **验证** | `ls include/uapi/linux/ \| grep -i irq` |
| **结果** | 有一个：`include/uapi/linux/irqnr.h` |
| **进一步** | `cat` 它 —— **全文 4 行，只有一段注释**：「There isn't anything here anymore, but the file must not be empty or patch will delete it.」 |
| **修正后的结论** | 结论方向不变（**irq 对用户态没有任何 ioctl / uapi 类型**），但表述从"没有文件"改成"有一个空壳文件"，并把那句注释原文引了出来 |

教训：**"没有 X"这类否定性论断也要跑一次命令**，不能凭印象。

### §6.5 `kernel/irq/` 有多少个文件、Kconfig 有多少项？

| 项 | 初始写的 | 实际 | 怎么发现的 |
|---|---|---|---|
| `kernel/irq/*.c` 个数 | **25** | **22**（25 是 `.c` + `.h`） | 写 99 §3.1 时把 `.c` 和 `.h` 分开重跑 |
| Kconfig `config` 项 | **29** | **30** | 第一次用 `grep -n '^config \|^menu'` 数行，把 `menu` 那一行算进去又漏减了一项 |

⇒ 修正范围：01 §0.1 / §0.4、07 篇首、00 §1.4、05 §0.3。
⚠️ 教训：**统计"个数"一律用 `grep -c` / `wc -l`，不要人工数 `grep -n` 的输出行。**

---

## §7 逐篇取证表

> 格式：每篇列「关键论断 → 出处」和「本轮自审的修正」。
> ⚠️ 提到"某处原先写错了"时**用文字描述那个错误值**，不写成 `文件:行号` 形式（§8.4）。

### §7.1 逐篇关键论断的出处

| 篇 | 关键论断 | 出处（等级） |
|---|---|---|
| **00** | 没有 `bus_type` ⇒ 框架型 | 一等（§2.6 的 grep） |
| 00 | §2 的 GIC v3 硬件模型（三部件、六段 INTID、EOI、ITS） | 二等 —— **架构层面的一般理解 + `drivers/irqchip/irq-gic-v3.c` 的关键片段**；⚠️ §2.4/§2.5 两条明确标为非本树取证（§5.3） |
| 00 | §2.3 的 hwirq 换算 | 一等（`drivers/irqchip/irq-gic-v3.c:1547` / `:1550` 逐行） |
| 00 | §6 的六条端到端路径 | 一等（全部行号取自一等证据范围） |
| **01** | 151 个导出符号 | 一等（§3.2） |
| 01 | 十四个流处理器 | 一等（`include/linux/irq.h:639-658` + `kernel/irq/chip.c` 函数图）；⚠️ **各自"适用硬件"是推断**（§5.3） |
| 01 | 错误码语义与计数 | 一等（§3.4，本轮重跑） |
| 01 | API 位置只到文件 | 规则 10 |
| **02** | 五组关系（§1.0.A–E） | 一等 —— 每组都给了"为什么不会发生"的行号 |
| 02 | 级联被 `set_norequest` 的证据链 | 一等（`kernel/irq/chip.c:1036-1039` + `kernel/irq/manage.c:928` + `kernel/irq/settings.h:100-102`） |
| 02 | 四组 flag 的完整位表 | 一等（逐字读头文件） |
| 02 | 五个全局状态 + 五把锁 | 一等 |
| **03** | Part A 时间线次序 | 逐条用"谁调用谁"验证（§2.4） |
| 03 | `handle.c:208` 释放锁那一段 | 一等（`kernel/irq/handle.c` 全文逐行读） |
| 03 | Part C 的四条设计债 | 一等（§3.6 + §6.3） |
| **04** | 十一个独立框架 | 混合：§4 §5 §6 §11 一等，§1 §2 §10 二等，§3 §7 §8 §9 三等 |
| 04 | §6.4 的 debugfs 注入机制 | 一等（`kernel/irq/debugfs.c` 全文） |
| **05** | 五套骨架 | 二等 —— 结构提炼自点名的真实驱动（§0.2），⚠️ **未编译**（§5.1） |
| 05 | 每个符号存在性 | 一等 —— **逐个 grep 确认**（含 `__exception_irq_entry` 在 `arch/arm64/include/asm/exception.h:17`） |
| **06** | 核心读 **0** 条 DT 属性 | 一等（§2.6，一条命令可验） |
| 06 | 属性总表的消费点 | 一等（`drivers/of/irq.c` 逐条 grep 到行） |
| 06 | GIC 的六种 cell 0 + 两个守卫 | 一等（`gic_irq_domain_translate()` 逐行） |
| 06 | §5 的 `interrupt-map` 结构 | 二等 —— ⚠️ **未逐字读 `of_irq_parse_raw()`**（§5.2） |
| **07** | 零 `TP_CONDITION`、5 + 12 个 tracepoint | 一等 |
| 07 | WARN 家族 126 vs `pr_err` 14 | 一等（§3.5，本轮重跑并拆列，§6.2） |
| 07 | `/proc/interrupts` 的每一列 | 一等（`show_interrupts()` 逐行） |
| **08** | 九个主题 | 每个"回到代码"段都带行号 |
| 08 | 每个类比都给了误导边界 | 篇内每节第四段 |
| **99** | 本篇全部数字 | §3 每行一条命令 |

### §7.2 三个自检脚本的最终结果

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/irq
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh core-api/irq            $D .
bash    $S/check_doc_coverage.sh interrupt-controller    $D .
```

| 脚本 | 结果 |
|---|---|
| `check_line_refs.py` | **干净** —— 无解析不到的文件、无越界行号 |
| `check_xrefs.py` | **0 条失效** |
| `check_doc_coverage.sh`（两次） | 处置见 §2.3 |
| `.xref-ignore` / `.lineref-ignore` | **两个都不存在 —— 零豁免** |

**零豁免**：本套没有靠 ignore 文件绕过任何一条脚本告警，
所有告警都是改文档解决的（§8 记录了改法）。

### §7.3 本套的自我修正记录（九条）

| # | 原先写的 | 实际是 | 怎么发现的 |
|---|---|---|---|
| 1 | **「七个流处理器」** | **十四个** | §6.1 —— **本套最大的一处修正**，01 §4.3 整节重写 |
| 2 | 日志普查把 `pr_warn` 和 `WARN` 合成一列，看起来"差不多" | WARN 家族 **126** vs `pr_err` **14** | §6.2 —— 07 §2.1 的整节定性改了 |
| 3 | `kernel/irq/*.c` 是 **25** 个 | **22** 个（25 是 `.c` + `.h`） | §6.5 |
| 4 | Kconfig 有 **29** 个 `config` 项 | **30** | §6.5 —— 人工数 `grep -n` 输出行导致 |
| 5 | 「本子系统没有 uapi 头文件」 | 有一个 4 行、只含注释的 `irqnr.h` | §6.4 |
| 6 | 06 篇引用带逗号的 DT 绑定文件名**并写了行号** | 必须**不写行号** | `check_line_refs.py` 报 UNRESOLVED（§8.5） |
| 7 | 07 篇在跨子系统对比里把另一套的篇号-小节号并排写成 `NN §X.Y` 形式 | 必须改写成「那一套的 NN 篇 §X.Y」形式 | `check_xrefs.py` 误判为**本套** 07 篇的一节（§8.6） |
| 8 | 04 §12 的结尾把三个小节号**连写**，其中两个恰好组成「两位数 + 空格 + §一位数」的形状 | 必须在两者之间插入文字（本套改成了「§10 加 §11」） | `check_xrefs.py` 的 `XREF_BARE` 把那两位数当成篇号（§8.6 新形态）⇒ ⚠️ **本行也刻意不写出那个字面形状** |
| 9 | 03 §C5 引用了 01 篇一个**不存在的小节号**（错误码普查那一节） | 应为 01 §10.1 | `check_xrefs.py` |

⚠️ **第 1、2 条是"方法性"错误而不是"手误"**：
第 1 条是**用部分 grep 结果当全集**，第 2 条是**把语义不同的东西合并统计**。
⇒ 两条都写进了 §6 的教训里。

### §7.4 ⚠️ 本轮 review 自己新产生的问题

按方法论第 15 条「每一轮 review 自己会产生新的错误」：

| 新问题 | 处置 |
|---|---|
| 修正流处理器个数后，02 篇和 03 篇里"七个"的转述变成陈旧引用 | 全套 grep「七个」逐处核对并改掉（02 的机制表、03 的分层图） |
| 修正 `.c` 个数后，07 篇篇首的统计口径与 01/00 不一致 | 三篇一起改成"22 个 `.c` = 14294 行；连 `.h` 共 15044 行" |
| 本篇 §7.3 若照抄第 6–8 条的**具体字面形式** ⇒ 会污染本篇自己的引用集 | 改成**描述形状**（"带逗号的 DT 绑定文件名"、"`NN §X.Y` 形式"、"三个小节号连写"），不写出字面串（§8.4 陷阱 ③） |

---

## §8 工具坑：三个自检脚本的已知行为

> 这一节是给下一次用同一套 skill 的人看的。前六条沿用 remoteproc 那一轮的结论，
> **§8.6 的第二种形态和 §8.4 的第三次复现是本轮新增的**。

### §8.1 `check_doc_coverage.sh`：只有"是不是 0"可信

| 行为 | 说明 |
|---|---|
| **「引用次数」不可信** | basename 子串匹配。本套跑 `core-api/irq` 时 `irq-domain` 的次数被"irq"这个词到处出现严重放大 |
| **只认 `.yaml`** | ⚠️ `Documentation/devicetree/bindings/interrupt-controller/` 共 113 个文件，其中 **63 个是 `.txt` 老绑定，脚本完全看不到**（比 remoteproc 那一轮的 6 个严重得多） |
| **参数是相对 `Documentation/` 的路径** | 本子系统要传 `core-api/irq` 和 `interrupt-controller`，**不是** `irq` |
| ⚠️ **按目录取名的副作用** | 113 个 DT 绑定全被当成"参考"清单 ⇒ 大量 0 引用。**这不是覆盖缺口**，处置见 §2.3 |

### §8.2 `sed -n` 的多段规格按文件升序输出

```bash
sed -n '298p;283p' file.c        # 实际输出顺序：283 → 298
```

⇒ **纪律：`sed` 的行号规格一律按升序列。**
**本轮没有再犯**（remoteproc 那一轮因此产生了 22 处行号错配）。
更稳的做法是一次只取一段，用 `echo ===` 分隔多次调用的输出。

### §8.3 `check_line_refs.py` 会解析代码块和 ASCII 图里的 `path:NNN`

| 行为 | 说明 |
|---|---|
| 它抓**任何** `path:NNN` token | 包括围栏代码块里的、ASCII 图里的、表格里的 |
| 正确的规避 | ASCII 图里**不写行号**，行号放到图下方的表里，用完整路径 |
| ⚠️ **错误的规避** | 加 `--ignore` 条目 |
| `--ignore` 的真实适用范围 | **只对"行号越界"生效**，对"文件名解析不到"无效 |

本套从 01 篇篇首就声明了这条纪律，**十篇一次告警都没有**（除了 §8.5 那一次）。

### §8.4 99 篇的三个自污染陷阱

| 陷阱 | 表现 | 正确做法 |
|---|---|---|
| ① 在"自审修正记录"里**照抄那个错误的 `文件:行号`** | 会被当成真实引用去校验 | **用文字描述** |
| ② 引用自检脚本自己的 `~/.claude/...` 路径 + 行号 | 不在源码树里，解析不到 | 只写脚本名 |
| ③ 为了演示 `check_xrefs.py` 的假阳性而**写出那个字面串** | 演示本身触发同一个告警 | **描述它的形状**，不写具体数字 |

⚠️ **陷阱 ③ 本轮又踩了一次**（在 §7.3 记录"04 §12 那处相邻小节号"时写出了字面形状），
已就地改成描述性表述。
⇒ **这是它在两套文档里连续第三次出现** —— 说明它是这套流程里最顽固的一个坑。
**下次写 99 §7 之前先默念一遍陷阱 ③。**

### §8.5 `check_line_refs.py` 按逗号切基名

⚠️ DT 绑定文件名常带逗号。脚本建消歧表时**会在逗号处切开基名**，
于是「`Documentation/devicetree/bindings/interrupt-controller/` 下那个逗号文件名 + 冒号 + 行号」
这样的引用会被报成"逗号后半截.yaml 解析不到"。

⇒ **纪律：文件名含逗号的一律不写行号**，改为描述性引用。
本套在 06 篇篇首就地声明了这条（该篇引用了 GIC 的 yaml 绑定）。

同类的两个特例（沿用前几套结论）：顶层 **`Makefile`** 和 **`Kconfig`** 也一律不带行号。

### §8.6 `check_xrefs.py` 的假阳性：本轮发现第二种形态

| 形态 | 原因 | defuse |
|---|---|---|
| 一行里 `NN-xxx.md` 文件名后 60 字符内出现「本篇 §X」 | 正则把 §X 归给了那个文件名 | 调整语序 |
| **跨子系统引用**（提到别套文档的篇号 + 小节号） | `XREF_BARE` 只看形状，不区分是哪一套 | 写成「**NN 篇 §X**」——脚本不匹配这个形式 |
| **本轮新增：两个相邻小节号连写** | 前一个小节号的**两位数字**恰好被 `XREF_BARE` 当成篇号，后一个的 `§X` 被当成小节号 | **在两者之间插入文字**（本套改成了「§10 加 §11」） |

**第三种形态是本轮的新发现**，值得单独强调：
它不涉及跨篇引用、不涉及文件名，**纯粹是两个本篇小节号挨在一起**就会误报。
⇒ **纪律：列举多个小节号时不要纯用空格分隔，至少加一个「和」/「加」/顿号。**

### §8.7 环境坑速查（沿用前几套，本轮重新确认）

| 坑 | 规避 |
|---|---|
| 默认 `grep` 是 **ugrep 7.5.0** | 统计一律用 `/usr/bin/grep`（GNU 3.7） |
| ugrep 不认 `\s` | 用 POSIX `[[:space:]]` |
| ugrep 默认遵守 `.gitignore` | 同第 1 条 |
| 递归扫 `arch/arm64/boot/dts/` 超 2 分钟 | 先 `grep -rl` 按 compatible 反查，再单文件 `sed` |
| Bash 的 cwd 跨调用保留、shell 变量不保留 | `cat >>` 一律用**绝对路径** |
| Python heredoc 里的替换串含 ASCII `"` | 一律用三引号字符串 |
| `assert` 失败时不要凭记忆猜原文 | 去 grep 那个特征串 |
| **统计"个数"不要人工数 `grep -n` 的输出行** | 用 `grep -c` / `wc -l`（§6.5 的教训） |
| **从头文件 grep 出的部分结果不能当全集** | 用 `grep -c` 拿总数对一遍（§6.1 的教训） |

---

## §9 rule-29 对照：本套没有对照集

| 项 | 情况 |
|---|---|
| `output/qoder/irq/` | **不存在** |
| `output/qoder/` 下有什么 | 只有 `spi` 一套 |
| ⇒ 处置 | **无对照套**。质量控制完全依赖三个自检脚本 + §7.3 的九条自我修正 |

上一次做过 rule-29 对照的是 spi 那一套（结论已吸收进 skill）。
本套沿用了那次吸收的全部规则，其中在 irq 上体现最明显的四条：

| 规则 | 在本套的体现 |
|---|---|
| 02 篇开篇先立「先分清 N 组关系」 | 02 §1.0 立了**五组**（本套所有子系统里最多），其中 §1.0.B「级联 vs 层级」是全套的枢纽 |
| 04 篇要有"哪几个在目标架构上真会碰到"的分诊表 | 04 §0.2 的十一行分诊表，明确点出只有四节"一定会碰到" |
| 06 篇每条属性都要有定位到行的消费者 | 06 §1.2 的十二行属性总表，每条都指向 `drivers/of/irq.c` 的具体行 |
| 08 篇必含"最容易被类比错的那组关系" | 08 §2「级联和层级差在哪」—— 用"分机再接交换机 vs 同一号码两级配置"两个并列类比 |

相比 remoteproc 那一套，本套多做了两件事：

| 多做的 | 为什么 |
|---|---|
| 00 §2 的硬件背景写到 **GIC v3 三部件 + 六段 INTID + EOI 模式 + ITS 转换流程** | irq 的抽象**完全是硬件形状决定的**（00 §2.10 的映射表） |
| 每一处"非本树取证"的论断都单独标注（§5.3 末两条） | irq 涉及大量架构手册级别的知识，必须区分"读了代码"和"知道架构" |

---

## §10 这套文档怎么用

| 你的处境 | 建议 |
|---|---|
| 想快速上手 | 00 §2（硬件）→ 08 → 05。**§5.1 提醒你 05 的代码没编译过** |
| 在排查线上问题 | 07 §8 速查 → 07 §6 错误码 → 06 §9 错误映射。这三处的每一句报错都能在源码里 grep 到 |
| 要基于本套做设计决策 | **先读 §5.3** —— 那九条是"代码事实确定、后果是推断"的；末两条更是"非本树取证" |
| 要引用 `interrupt.h` 的行号 | **先读 §1.1** —— 本树有 29 行本地补丁；好消息是本套引用的都在未受影响区 |
| 要把本套移到别的内核版本 | 先跑 §5.4 那三条命令 |
| 要在非 arm64 上用 | **§2.7 就是你要补读的清单**（尤其 `matrix.c` / `ipi.c` / `autoprobe.c` 那三块） |
| 想复核某个数字 | §3 每一行都配了命令 |
| 要写 irqchip 驱动 | 05 §2/§3/§4/§5 挑一个 + 00 §2 全部 + 08 §9；⚠️ **01 §4.3 那十四个流处理器的"适用硬件"是推断**（§5.3），选之前对照真实驱动 |
