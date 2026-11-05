---
title: 取证附录
---

# remoteproc 99：取证、方法与未验证项

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> 本篇的定位：**让读者知道哪些结论可以直接用、哪些要自己再确认一遍**。
> 所有数字都配了可重跑的一条命令；每轮 review 后全部重跑。
>
> ⚠️ 本篇里的 `文件:行号` 也写完整路径。提到别处的错误引用时**用文字描述，不写成
> `文件:行号` 的形式**，否则会污染本篇自己的引用集（见 §8.4）。

---

## §1 基线

### §1.1 树的状态

| 项 | 值 | 命令 |
|---|---|---|
| HEAD | `f6e38ae624cf` "Linux 6.1.158" | `git log -1 --format='%H %s'` |
| 分支 | `linux-6.1.158` | `git branch --show-current` |
| 目标路径有无本地改动 | **0 行**（干净） | 见下 |

```bash
git status --porcelain -- \
  drivers/remoteproc \
  include/linux/remoteproc.h \
  include/uapi/linux/remoteproc_cdev.h \
  Documentation/staging/remoteproc.rst \
  Documentation/devicetree/bindings/remoteproc | wc -l      # → 0
```

**这一步是整份文档的定性前提**：整棵树有大量本地修改
（`git status` 顶层输出上百行），但**remoteproc 相关的六条路径一处未改**。
⇒ 本套文档描述的是**上游 6.1.158 的原样代码**，不是打过补丁的版本。

### §1.2 规模（决定"读全"与"读符号表"的分界）

| 文件 | 行数 | 本套的处理 |
|---|---|---|
| `drivers/remoteproc/remoteproc_core.c` | 2770 | 一等证据（逐段读） |
| `drivers/remoteproc/remoteproc_virtio.c` | 603 | 一等证据（**逐行读，建了完整符号台账**） |
| `drivers/remoteproc/remoteproc_coredump.c` | 469 | 一等证据 |
| `drivers/remoteproc/remoteproc_debugfs.c` | 430 | 一等证据 |
| `drivers/remoteproc/remoteproc_elf_loader.c` | 395 | 二等证据（读了错误路径和五个回调，未逐行） |
| `drivers/remoteproc/remoteproc_sysfs.c` | 275 | 一等证据 |
| `drivers/remoteproc/remoteproc_cdev.c` | 126 | 一等证据（逐行读） |
| `drivers/remoteproc/remoteproc_internal.h` | 228 | 一等证据 |
| `drivers/remoteproc/remoteproc_elf_helpers.h` | 122 | **三等证据**（宏定义，索引工具盲区，只读了符号名） |
| `include/linux/remoteproc.h` | 708 | 一等证据 |
| `include/uapi/linux/remoteproc_cdev.h` | 37 | 一等证据（全文读过） |
| **核心七个 `.c` 合计** | **5068** | —— |

```bash
wc -l drivers/remoteproc/remoteproc_{core,virtio,coredump,debugfs,elf_loader,sysfs,cdev}.c \
      drivers/remoteproc/remoteproc_internal.h \
      drivers/remoteproc/remoteproc_elf_helpers.h \
      include/linux/remoteproc.h include/uapi/linux/remoteproc_cdev.h
```

| 项 | 值 | 命令 |
|---|---|---|
| 平台驱动个数 | **34** 个 `.c` | `ls drivers/remoteproc/*.c \| wc -l` |
| 平台驱动总行数 | **25252** | `cat drivers/remoteproc/*.c \| wc -l` |
| Kconfig 行数 / `config` 项 | 357 / **28** | `wc -l drivers/remoteproc/Kconfig` · `grep -c '^config ' …` |

**34 个驱动 25252 行 vs 核心 5068 行** —— 这个比例本身就是一条结论：
硬件差异全部被推到驱动层（00 §7.3）。

### §1.3 子系统类型判定

| 判据 | 命令 | 结果 |
|---|---|---|
| 有 `struct bus_type` 吗 | `grep -c 'struct bus_type' drivers/remoteproc/*.c include/linux/remoteproc.h` | **全部为 0** |
| 有 `struct class` 吗 | —— | 有：`rproc_class`（`drivers/remoteproc/remoteproc_sysfs.c:257`） |

⇒ **判定为「框架型」子系统**。按 skill 的适配规则：
02 篇改画"核心抽象 ↔ 消费者"关系图；03 主轴是"注册/启动/回调"；
04 篇按七个独立框架组织（它确实有子框架，不需要并入 03）；
06 篇正常写（它有 DT 绑定）。

### §1.4 官方文档登记（三等证据）

| 文件 | 行数 | 读了吗 | 内容纳入了哪里 | 备注 |
|---|---|---|---|---|
| `Documentation/staging/remoteproc.rst` | 360 | 读了（通读） | 00 §1、01 的 API 语义、03 的生命周期 | **注意它在 `staging/` 下，不在 `driver-api/`** |
| `Documentation/devicetree/bindings/remoteproc/ti,k3-r5f-rproc.yaml` | —— | 读了属性表 + example + memory-region 描述 | 06 §3.1、§3.4、§5.1、§6 | 唯一被 06 篇引用的绑定 |
| `Documentation/devicetree/bindings/remoteproc/st,stm32-rproc.yaml` | —— | 只读了属性名清单 | 06 §4.2、§5.3 的属性归属 | 未逐条引用 |
| `Documentation/devicetree/bindings/remoteproc/fsl,imx-rproc.yaml` | —— | 只读了属性名清单 | 06 §5.2 | 未逐条引用 |
| `Documentation/devicetree/bindings/remoteproc/qcom,sdm845-adsp-pil.yaml` | —— | 只读了属性名清单 | 06 §6.3 的高通形状对比 | 未逐条引用 |
| 其余 **15** 个 `.yaml` + **6** 个 `.txt` | —— | **没读** | —— | 见 §2.3 |

`Documentation/driver-api/` 下**没有** remoteproc 相关文件
（可重跑：`ls Documentation/driver-api/ | grep -i 'rproc\|remoteproc'` 无输出）。
⇒ 这是本子系统文档位置的一个特点：**唯一的框架文档在 `staging/` 下**，
说明上游认为这套 API 还没稳定。

### §1.5 ⚠️ 文档与代码冲突之处

本轮没有发现 `Documentation/staging/remoteproc.rst` 与代码的**硬冲突**，
但有一处**覆盖不足**：

| 主题 | 官方文档 | 代码现状 |
|---|---|---|
| `attach` / `detach` | **一句都没提** | `rproc_ops` 有 `attach`/`detach`/`get_loaded_rsc_table` 三个回调，`rproc_validate()` 有专门的 `RPROC_DETACHED` 分支 |

⇒ attach/detach 这一整套机制（03 §B3、08 §5）**完全是从代码读出来的**，
没有官方文档背书。这也是它被列进 §5.3「推断而非观察」的原因之一。

---

## §2 证据等级

### §2.1 一等证据：逐段/逐行读过源码，论断直接引原文

| 文件 | 详读范围 |
|---|---|
| `drivers/remoteproc/remoteproc_core.c` | 全部主流程函数：`rproc_da_to_va` (193-230)、carveout 家族 (250-330, 620-900)、vring 家族 (325-435)、`rproc_handle_vdev` (475-530)、`rproc_handle_trace` (545-590)、`rproc_handle_resources` (1018-1060)、subdev 四回调 (1078-1140)、资源表三指针 (1470-1590)、`rproc_fw_boot`/`rproc_start`/`rproc_stop`/`__rproc_detach` (1375-1800)、恢复家族 (1779-1900)、`rproc_boot`/`shutdown`/`detach` (1907-2110)、`rproc_set_firmware` (2167-2210)、`rproc_validate`/`rproc_add` (2212-2345)、`rproc_alloc` 家族 (2375-2630)、panic 家族 (2655-2740)、`rproc_crash_names`/`rproc_crash_to_string` (62-75) |
| `drivers/remoteproc/remoteproc_virtio.c` | **全文 603 行**，建了完整的"行号:符号"台账 |
| `drivers/remoteproc/remoteproc_cdev.c` | **全文 126 行** |
| `drivers/remoteproc/remoteproc_sysfs.c` | **全文 275 行** |
| `drivers/remoteproc/remoteproc_debugfs.c` | **全文 430 行** |
| `drivers/remoteproc/remoteproc_coredump.c` | **全文 469 行** |
| `include/linux/remoteproc.h` | 结构体定义全部 + `rproc_ops` 的 17 个成员逐个核 |
| `include/uapi/linux/remoteproc_cdev.h` | 全文 37 行 |
| `drivers/remoteproc/remoteproc_internal.h` | 包装函数与默认值（80-230） |

### §2.2 二等证据：只读了符号表 / 只读了关键片段

| 对象 | 读到什么程度 | 影响哪些论断 |
|---|---|---|
| `drivers/remoteproc/remoteproc_elf_loader.c` | 读了 `rproc_elf_sanity_check` 的全部错误分支 (37-110)、段加载的边界检查 (185-220)、资源表加载 (280-340)、`find_loaded_rsc_table` (372-395)。**中间的段拷贝循环没逐行读** | 04 §2、07 §5.4 |
| `drivers/remoteproc/stm32_rproc.c` | 读了 `st_rproc_ops` (647-660)、`parse_fw` (218-270)、mailbox 相关 (290-400, 480-570)、`auto-boot`/`wakeup-source` (700-760)。**其余未读** | 05 §2、§3.2 |
| `drivers/remoteproc/imx_rproc.c` | 只读了属性消费点（449-470, 600-620, 680-690, 726, 831）和 `imx_rproc_ops` (551-560) | 06 §5.2 |
| `drivers/remoteproc/ti_k3_r5_remoteproc.c` | 只读了属性消费点（396, 957-995, 1219, 1457-1470, 1512, 1555-1570, 1742） | 06 §5.1 |
| `drivers/remoteproc/meson_mx_ao_arc.c` | 读了函数骨架（58-260），未逐行 | 05 §0.2、§1.3 |
| `drivers/remoteproc/qcom_*.c`（6 个文件） | **只 grep 了 coredump API 和 `auto_boot` 的调用点**，没读实现 | 04 §3.1、06 §5.4 |
| `drivers/rpmsg/*.c` | **只 grep 了设备名字符串**（`rpmsg_ctrl%d`、`rpmsg%d`）和 ioctl 定义 | 05 §5.4 |
| `drivers/mailbox/mailbox.c` | 只读了 `mbox-names` 的读取点（420-430） | 06 §4.1、§8 |
| `drivers/of/of_reserved_mem.c` | 只读了 `by_idx`/`by_name` 两个入口（324-395） | 06 §1.1、§3.5 |
| `samples/rpmsg/rpmsg_client_sample.c` | 读了全部关键行（18-95） | 05 §4.1 |
| 其余 **28 个平台驱动** | **完全没读** | 只在统计里出现（如"34 个驱动"），不承载任何行为论断 |

### §2.3 ⚠️ `check_doc_coverage.sh` 的两次运行结果与处置

**运行 1：`staging`**
```bash
bash ~/.claude/skills/kernel-subsystem-docs/scripts/check_doc_coverage.sh staging output/remoteproc .
```
结果：8 篇「必查」，其中 6 篇 0 引用。

| 0 引用的文件 | 处置 |
|---|---|
| `crc32` · `lzo` · `xz` · `tee` · `speculation` · `static-keys` | **(c) 与本子系统无关** —— 它们只是恰好同住在 `Documentation/staging/` 目录下。remoteproc 的文档在 `staging/` 是个历史遗留（§1.4），把整个目录当成"必查"是脚本按目录取名的副作用，不是覆盖缺口 |
| `remoteproc`（1999 次引用） | 已覆盖 |
| `rpmsg`（208 次引用） | 已覆盖 |

⚠️ 脚本的"引用次数"是 basename 子串匹配，1999 次是因为"remoteproc"这个词在文档里到处出现 ——
**这个数字没有意义，只有"是不是 0"有意义**（§8.1）。

**运行 2：`remoteproc`（DT 绑定）**
```bash
bash ~/.claude/skills/kernel-subsystem-docs/scripts/check_doc_coverage.sh remoteproc output/remoteproc .
```
结果：19 篇 `.yaml`，被引用 **1** 篇（`ti,k3-r5f-rproc`，3 次）；「必查」0 篇。

| 处置 | 说明 |
|---|---|
| **(b) 覆盖了没标** | 4 篇的属性清单被读过并纳入了 06 §5（见 §1.4 那张表），但正文引用的是**驱动侧的消费代码**而不是绑定文档 —— 这是 06 篇的硬要求（"每条属性都要有定位到行的消费代码"）决定的，不是漏标 |
| **(a) 内容没覆盖** | 其余 **15 个 `.yaml` + 6 个 `.txt`** 确实没读。它们对应的平台（omap / davinci / keystone / ingenic / wkup_m3 / pru / meson / rcar / mtk / 多个高通型号）大部分不是 arm64 主流，或已被 §5.2 的"只读符号表"覆盖 |

⇒ **本套 06 篇的定位是"核心 + arm64 主流三家"，不是"25 个绑定的逐篇导读"**。
这一点写进了 06 §10 的覆盖情况表。

### §2.4 索引工具的使用与盲区

| 用途 | 用了吗 |
|---|---|
| 导航（找符号在哪个文件） | 用了 |
| 取证 | **没用** —— 所有行号都是 `sed -n` / `grep -n` 在真实树上取的 |

本子系统撞上索引工具的**两个典型盲区**：

| 盲区 | 本子系统的体现 |
|---|---|
| **宏生成的符号** | `drivers/remoteproc/remoteproc_elf_helpers.h` 里 32/64 位无关的访问器全是宏（04 §2.3），索引里查不到 |
| **函数指针间接调用** | `rproc_ops` 的 17 个回调**全部是间接调用**。"谁调用了 `ops->start`"这类问题索引给不出答案，只能 grep `->start` |

⇒ 这也是 03 篇 Part A 的时间线次序**逐条用"谁调用谁"验证过**的原因。

### §2.5 逐条 `sed` 核对（自审第 19 条）

本套的行号核对方式：
1. 写正文前先用**一次 Bash 调用**把要用的 `行号:符号` 全 dump 出来建台账；
2. 写正文时只查台账，不回源码；
3. 每篇写完立刻跑 `check_line_refs.py`；
4. 有疑问的逐条 `sed -n` 打开看。

⚠️ 第 4 步踩过一个坑：**`sed -n '298p;301p;283p'` 按文件升序输出，不按你列的顺序**。
详见 §8.2 和 §7.3 的自我修正第 4 条。

### §2.6 全称断言的验证

本套所有"全树唯一 / 一个都没有 / 只有 X"的论断都跑过全树 grep。清单：

| 论断 | 命令 | 结果 |
|---|---|---|
| 没有 `struct bus_type` | `grep -c 'struct bus_type' drivers/remoteproc/*.c include/linux/remoteproc.h` | 全 0 |
| 零 tracepoint | `ls include/trace/events/ \| grep -ci 'rproc\|remoteproc'` + `grep -rn 'CREATE_TRACE_POINTS\|TRACE_EVENT\|DECLARE_EVENT_CLASS' drivers/remoteproc/ include/linux/remoteproc.h` | 0 / 无输出 |
| 零 `_once` / 零 `_ratelimited` | 07 §2.1 的循环命令 | 全 0 |
| 核心七个 `.c` 里唯一的 `BUG_ON` | `grep -n 'BUG_ON' drivers/remoteproc/remoteproc_{core,virtio,elf_loader,coredump,sysfs,debugfs,cdev}.c` | 只有 `drivers/remoteproc/remoteproc_virtio.c:280` |
| 核心零 `dev_err_probe` | `grep -c dev_err_probe drivers/remoteproc/remoteproc_core.c` | 0 |
| `memory-region-names` 在 remoteproc 里 0 使用者 | `grep -rn 'memory-region-names' drivers/remoteproc/` | 无输出 |
| `elf_class` 在 ELF 加载器里 0 次出现 | `grep -c elf_class drivers/remoteproc/remoteproc_elf_loader.c` | **0** |
| `remoteproc_internal.h` 不对目录外开放 | 见下 | 目录内 **28** 个 `.c`（+1 个 `.h`），目录外 **0** |

```bash
grep -l '#include "remoteproc_internal.h"' drivers/remoteproc/*.c | wc -l   # → 28
grep -l '#include "remoteproc_internal.h"' drivers/remoteproc/*.h | wc -l   # → 1
grep -rl 'remoteproc_internal.h' --include=*.c --include=*.h . \
  | grep -v '^./drivers/remoteproc/' | wc -l                                # → 0
```

### §2.7 裁掉了什么（arm64-only 的代价）

| 裁掉的内容 | 为什么 | 影响哪一篇 |
|---|---|---|
| **ACPI** | 用户明确要求不描述。而且 remoteproc **根本没有任何 ACPI 代码** —— `grep -ril 'acpi' drivers/remoteproc/` 命中 **0 个文件**，`include/linux/remoteproc.h` 里也是 0 次 | 06 §10 已标注 |
| **x86 / powerpc / arm32 的体系结构差异** | arm64-only 基准 | —— |
| **STM32MP1 是 armv7** | 它是回调填得最全的参考实现，**保留作对照**，但明确标注了"不在 arm64 基准内" | 05 §0.2、06 §5.3 |
| **Ingenic（MIPS）/ OMAP（armv7）/ davinci（armv7）/ keystone（armv7）/ wkup_m3（armv7）** | 非 arm64 | 只在统计和"谁写 `auto_boot`"这类清单里出现 |
| `RPROC_SUSPENDED` 状态 | **只有 `drivers/remoteproc/omap_remoteproc.c` 会写它**（armv7），arm64 上永不出现 | 02 §10、00 §3.3 已标注 |
| 15 个 `.yaml` + 6 个 `.txt` DT 绑定 | 对应平台非 arm64 主流 | §2.3 已记录 |

⚠️ **裁剪不等于遗漏**：以上每一项都在对应篇目里显式标注了"本套不覆盖"。
如果你在非 arm64 平台上用这套文档，**§2.7 这张表就是你要额外补读的清单**。

---

## §3 统计数字总表（每条都能一条命令重跑）

### §3.1 规模类

| 数字 | 值 | 命令 |
|---|---|---|
| 核心七个 `.c` 总行数 | 5068 | `wc -l drivers/remoteproc/remoteproc_{core,virtio,coredump,debugfs,elf_loader,sysfs,cdev}.c` |
| 平台驱动个数 | 34 | `ls drivers/remoteproc/*.c \| wc -l` |
| 平台驱动总行数 | 25252 | `cat drivers/remoteproc/*.c \| wc -l` |
| Kconfig `config` 项 | 28 | `grep -c '^config ' drivers/remoteproc/Kconfig` |
| DT 绑定文件总数 | 25（19 `.yaml` + 6 `.txt`） | `ls Documentation/devicetree/bindings/remoteproc/ \| wc -l` |
| `Documentation/staging/remoteproc.rst` | 360 行 | `wc -l Documentation/staging/remoteproc.rst` |

### §3.2 API 类

| 数字 | 值 | 命令 |
|---|---|---|
| 核心导出符号（`remoteproc_core.c`） | 23 | `grep -c EXPORT_SYMBOL drivers/remoteproc/remoteproc_core.c` |
| `remoteproc_virtio.c` 导出 | 1 | 同上换文件 |
| `remoteproc_coredump.c` 导出 | 4 | 同上 |
| `remoteproc_elf_loader.c` 导出 | 5 | 同上 |
| `remoteproc_{sysfs,debugfs,cdev}.c` 导出 | **全为 0** | 同上 |
| **核心七个 `.c` 导出合计** | **33** | —— |
| 全 `drivers/remoteproc/` 的 `EXPORT_SYMBOL` / `_GPL` | 35 / 34 | `grep -rho 'EXPORT_SYMBOL[_A-Z]*' drivers/remoteproc/*.c \| sort \| uniq -c` |
| `struct rproc_ops` 成员数 | **17** | `sed -n '385,406p' include/linux/remoteproc.h \| grep -c '(\*'` |

注意第 5 行：sysfs / debugfs / cdev **一个符号都不导出** ——
它们只被 `remoteproc_internal.h` 暴露给同目录的 `.c`（§2.6）。
这是"这三块不对驱动开放"的机器可证形式。

### §3.3 枚举类

| 枚举 | 定义位置 | 值 |
|---|---|---|
| `enum fw_resource_type` | `include/linux/remoteproc.h:116` 起 | `RSC_CARVEOUT`=0 · `RSC_DEVMEM`=1 · `RSC_TRACE`=2 · `RSC_VDEV`=3 · `RSC_LAST` · `RSC_VENDOR_START` · `RSC_VENDOR_END` |
| `enum rproc_state` | `include/linux/remoteproc.h:428` 起 | `OFFLINE`=0 · `SUSPENDED`=1 · `RUNNING`=2 · `CRASHED`=3 · `DELETED` · `ATTACHED` · `DETACHED` · `LAST` |
| `enum rproc_crash_type` | —— | `MMUFAULT` · `WATCHDOG` · `FATAL_ERROR`（名字表在 `drivers/remoteproc/remoteproc_core.c:62-66`） |
| `enum rproc_dump_mechanism` | `include/linux/remoteproc.h:464-468` | `DISABLED`=0 · `ENABLED` · `INLINE` ⇒ **kzalloc 的默认值就是"关"** |

两个枚举的注释都明确要求"和查找表保持同步"
（`enum rproc_state` 的注释在 `include/linux/remoteproc.h:422-426`，
`enum fw_resource_type` 的在 `include/linux/remoteproc.h:112`）。
⇒ 04 §4.2 把这标成"必须手工维护的同步点"。

### §3.4 错误码类（07 §6.1 的原始数据）

| 项 | 值 | 命令 |
|---|---|---|
| 核心七个 `.c` 的 `-E*` 出现次数 | 96 | 07 §6.1 的循环命令 |
| 其中在函数文档注释里 | 2（`drivers/remoteproc/remoteproc_core.c:2554`、`drivers/remoteproc/remoteproc_elf_loader.c:35`） | `grep -n '^[[:space:]]*\*.*-E[A-Z]' …` |
| 实际返回/赋值点 | **94** | 96 − 2 |
| `-EINVAL` / `-ENOMEM` / `-EFAULT` / `-EBUSY` | 57 / 26 / 5 / 3 | 同上 |
| `-EOVERFLOW` / `-ENOSYS` / `-ENODEV` | 各 1 | 同上 |

### §3.5 日志与可观测性类（07 §2.1、§3.1 的原始数据）

| 项 | 值 |
|---|---|
| `dev_err`/`pr_err` 合计 | 106 |
| `dev_warn` | 8 |
| `dev_info` | 11 |
| `dev_dbg` | 17 |
| `_once` | **0** |
| `_ratelimited` | **0** |
| tracepoint | **0** |
| `WARN_ON` | 2（`drivers/remoteproc/remoteproc_core.c:158`、`:1508`） |
| `BUG_ON` | 1（`drivers/remoteproc/remoteproc_virtio.c:280`） |
| `dev_err_probe` | 0 |

### §3.6 源码设计债类（03 §C1 的原始数据）

| 标记 | 个数 | 位置 |
|---|---|---|
| `TODO` | **3** | `drivers/remoteproc/remoteproc_core.c:364`、`:365`、`:2561` |
| `FIXME` / `XXX` / `REVISIT` / `HACK` | **0** | —— |
| 自嘲式注释 `hacky` | 1 | `drivers/remoteproc/remoteproc_core.c:471` |

```bash
grep -n 'TODO\|FIXME\|XXX\|REVISIT\|HACK\|hacky' \
  drivers/remoteproc/remoteproc_{core,virtio,elf_loader,coredump,sysfs,debugfs,cdev}.c
```

### §3.7 arm64 相关性

| 项 | 值 | 命令 |
|---|---|---|
| `drivers/remoteproc/` 里的 ACPI 代码 | **0 个文件** | `grep -ril 'acpi' drivers/remoteproc/ \| wc -l` |
| arm64 DTS 里能反查到节点的驱动 | 2 个（`ti,am654-r5f` → `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi`；`qcom,sdm845-adsp-pas` → `arch/arm64/boot/dts/qcom/sdm845.dtsi`） | 06 §6 的反查命令 |
| 写 `RPROC_SUSPENDED` 的文件 | 只有 `drivers/remoteproc/omap_remoteproc.c`（armv7） | `grep -rn 'RPROC_SUSPENDED' drivers/remoteproc/` |

---

## §4 工具版本

| 工具 | 版本 | ⚠️ 注意 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本纯 stdlib |
| GNU grep | 3.7（`/usr/bin/grep`） | 本套所有 grep 统计都用**绝对路径**调它 |
| 默认 `grep` | **ugrep 7.5.0** | ⚠️ **不认 `\s`**，而且**默认遵守 `.gitignore`** ⇒ 会漏文件 |
| ripgrep | 13.0.0 | 只用于粗筛 |
| git | 2.34.1 | —— |

⚠️ **ugrep 那两条差异是本套踩过最多次的环境坑**：
- 用 `\s` 写正则会静默匹配不到 ⇒ 统一用 POSIX `[[:space:]]`；
- ugrep 遵守 `.gitignore` ⇒ 统计"全树有几处"时**必须用 `/usr/bin/grep`**，
  否则数字偏小而且不报错。

另一个环境限制：**递归 `grep -rn` 扫整个 `arch/arm64/boot/dts/` 会超过 2 分钟的 Bash 超时**。
本套的做法是先按 compatible 字符串反查文件名（`grep -rl`），再对单个文件 `sed`。

---

## §5 未验证项（四层分层）

### §5.1 第一层：未编译、未运行

| 内容 | 说明 |
|---|---|
| **05 篇的全部代码骨架** | 篇首已声明。它们是从真实驱动提炼的结构，**没有 `make` 过一次** |
| 05 §5.3 那个 ioctl 小程序 | 宏定义没有逐字抄 `include/uapi/linux/remoteproc_cdev.h`，正文已提示以头文件为准 |
| 07 §3.2/§3.3 的 ftrace / kprobe 命令 | **没有在真机上跑过**。kprobe 那一段还明确提示"先 `grep /proc/kallsyms` 确认符号存在" |
| 07 §7.2 的监控脚本 | 未运行 |
| 06 §7.1 的 `make dtbs_check` | **没有跑过**。"哪些错误能被 schema 抓到"这个判断来自绑定 yaml 里 `additionalProperties: false` 的存在，不是实测 |

### §5.2 第二层：只读了符号表 / 只读了片段

见 §2.2 那张表。受影响最大的三处：

| 论断 | 依赖的阅读深度 | 风险 |
|---|---|---|
| 04 §3.1「本树只有高通和 ST 开箱可用 coredump」 | **只 grep 了 `rproc_coredump_set_elf_info` 的调用点**，没读那 6 个文件的上下文 | 如果某个驱动通过别的路径设了 `elf_class`（虽然该字段只有那一个 setter），结论会变。**已用 `grep -c elf_class` 在 ELF 加载器里验证为 0，这是最强的一道支撑** |
| 06 §5.1 的 TI 属性默认值 | 来自**绑定文档的描述文字**，不是驱动代码里的默认值赋值 | 驱动可能有自己的兜底逻辑；正文已把出处写成"绑定文档里写着" |
| 05 §2.1 stm32 的 12 个回调 | 读了 `st_rproc_ops` 结构体全文（`drivers/remoteproc/stm32_rproc.c:647-660`） | 低 —— 这是逐字引用 |

### §5.3 第三层：推断而非观察

| 论断 | 推断链 | 为什么没实测 |
|---|---|---|
| 03 §C2 的 `RSC_IGNORED` 误判 | `RSC_IGNORED` 是正数 1 → `rproc_handle_resources()` 把最后一条资源的返回值直接 return → 两个调用点用 `if (ret)` 判断 | **需要一个带厂商资源且它恰好是最后一条的固件**。正文已明确写「静态代码阅读得出的结论，未在硬件/QEMU 上实测」 |
| 03 §C3 / 07 §5.3 的 `pm_relax` 泄漏 | `RPROC_CRASHED` 分支用 `return` 而 `RPROC_OFFLINE` 分支用 `goto out`，`pm_relax` 在 `out:` 后面 | 需要构造"崩溃处理中再次上报崩溃"。代码结构上是确定的，**后果（系统进不了 suspend）是推断** |
| 03 §B4.1 的 `int offset` 溢出 | `da` 是 `u64`，`carveout->da` 是 `u32`，差值存进 `int` | 需要一块 da 跨度 > 2 GB 的布局 |
| 03 §C5 / 07 §6.3 的 32 位截断 | `rsc->pa = (u32)pa` 等三处显式窄化 | 需要 32 位内核 + 高位物理地址 |
| 06 §3.3 分支 C 的静默失效 | `of_reserved_mem_device_init_by_idx()` 的返回值在 `drivers/remoteproc/remoteproc_virtio.c:429` 完全没检查 | 代码事实是确定的（这一句就在那儿）；**"rpmsg 因此不通"是推断** |
| 06 §3.2 的索引顺序语义 | TI 驱动的 `i + 1` 跳过索引 0 | 代码事实确定；"颠倒后跑飞"是推断 |
| 04 §7.2 的 iomem memcpy 问题 | `rproc_trace_read()` 传 NULL 给 `is_iomem`，之后走 `simple_read_from_buffer()` 的普通 memcpy | 只有当 trace 缓冲真是 `ioremap` 来的才会出问题 |
| 08 §5 attach 场景 `.prepare` 不跑 | `rproc_attach()` 的调用链里没有 `rproc_prepare_subdevices` 之前的 `ops->prepare` | 读代码得出；**没有在支持 attach 的硬件上验证过** |

**这一层是本篇最该被读者盯住的**：以上八条**代码事实都可核对**（都给了行号），
但**"会导致什么后果"都是推断**。要在产品上依赖其中任何一条，请自己复现一次。

### §5.4 第四层：版本时效性

| 项 | 说明 |
|---|---|
| 版本锚点 | 全部结论锚在 **6.1.158**（`f6e38ae624cf`） |
| 上游变化最快的部分 | attach/detach 机制（6.x 期间才引入，官方文档尚未跟上，§1.5）；coredump 的 `dump_conf` 机制 |
| `Documentation/staging/` 这个位置 | 说明 API 未稳定 ⇒ **跨大版本引用本套文档前先 diff `include/linux/remoteproc.h`** |
| 已知会变的具体点 | `rproc_ops` 的成员数（本版本 17 个）；`enum rproc_state` 的成员（本版本 8 个，含 `RPROC_LAST`） |

快速判断本套是否过期的一条命令：
```bash
sed -n '385,406p' include/linux/remoteproc.h | grep -c '(\*'    # 不再是 17 就要重读 04 §2、05 §1
```

---

## §6 交叉验证实例：初始假设被源码推翻的五条

> 这一节的价值在于**把结论的方向性钉准**。每条都是"我先以为是 A，验证后发现是 B"。

### §6.1 ELF 加载器会不会顺手记下固件的 `elf_class`？

| 阶段 | 内容 |
|---|---|
| **初始假设** | coredump 需要 `elf_class`，而 ELF 加载器刚好解析过 ELF 头 ⇒ 它应该顺手填上 |
| **验证命令** | `grep -c elf_class drivers/remoteproc/remoteproc_elf_loader.c` |
| **结果** | **0** |
| **进一步** | `grep -rn 'elf_class' drivers/remoteproc/` ⇒ 只有 `remoteproc_core.c` 的初始化（`:2458`）、`remoteproc_coredump.c` 的 setter（`:118`）和读取点（`:242`） |
| **修正后的结论** | `elf_class` **只能由驱动显式调 `rproc_coredump_set_elf_info()` 设置**。不设 ⇒ `rproc_coredump()` 在 `drivers/remoteproc/remoteproc_coredump.c:251-254` 就返回 ⇒ **默认 coredump 什么都不做** |
| **落到哪里** | 04 §3.1、07 §4.4、00 §5.3 |

这一条改变了 04 §3 整节的写法：从"介绍 coredump 三种模式"变成
"先说清默认什么都不做，再讲三种模式"。

### §6.2 debugfs 的 `crash` 文件是不是一个越界漏洞？

| 阶段 | 内容 |
|---|---|
| **初始假设** | `rproc_crash_write()` 用 `kstrtouint_from_user()` 读一个整数后**不做任何校验**就交给 `rproc_report_crash()`（`drivers/remoteproc/remoteproc_debugfs.c:259`、`:263`）⇒ 疑似数组越界 |
| **验证** | 追到 `rproc_crash_to_string()` |
| **结果** | 那里有 `if (type < ARRAY_SIZE(rproc_crash_names))`（`drivers/remoteproc/remoteproc_core.c:71`），越界返回 `"unknown"` |
| **修正后的结论** | **不是漏洞**。而且这个宽容设计是**有用的**：`echo 99 > crash` 能安全地触发一次完整的恢复流程 |
| **落到哪里** | 04 §5.1、07 §5.1 |

这一条把一个"疑似 bug"变成了"推荐的调试手法"。**反例留在文档里，方向就钉准了。**

### §6.3 `rproc_trace_read()` 传 NULL 给 `is_iomem` 会不会空指针解引用？

| 阶段 | 内容 |
|---|---|
| **初始假设** | `drivers/remoteproc/remoteproc_debugfs.c:135` 传 NULL，而 `rproc_da_to_va()` 里有 `*is_iomem = …` ⇒ 疑似空指针 |
| **验证** | `sed -n '193,232p' drivers/remoteproc/remoteproc_core.c` 读全函数体 |
| **结果** | 有守卫：`if (is_iomem)` 在 `drivers/remoteproc/remoteproc_core.c:220`，赋值在 `:221` |
| **修正后的结论** | 传 NULL 是**合法**的。但由此发现了一个**真实的**粗糙点：既然不问 iomem，后面 `simple_read_from_buffer()` 就是普通 memcpy —— 对比 `rproc_copy_segment()` 认真区分了 `memcpy_fromio()` |
| **落到哪里** | 04 §7.2、05 §1.2 的骨架注释（"必须判空，核心允许传 NULL"） |

这一条的价值：**初始假设错了，但顺着它查出了另一个真问题**。

### §6.4 `-EBUSY` 是不是"全核心唯一一处"？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 04 §4.4 写成了「这是全核心唯一一处 `-EBUSY`」，转述 01 §10.1 |
| **验证** | `grep -o '\-EBUSY' …` 逐文件计数 |
| **结果** | 共 **3** 处：`drivers/remoteproc/remoteproc_sysfs.c:112`（`return` 形式）、`drivers/remoteproc/remoteproc_debugfs.c:92`（`err =`）、`drivers/remoteproc/remoteproc_core.c:2186`（`ret =`） |
| **修正** | 01 §10.1 本身没错（它按"return 形式 / 赋值形式"分开列），是 **04 的转述漏了赋值形式**。已在 04 §4.4 就地更正并指向 07 §6.1 |
| **落到哪里** | 04 §4.4、07 §6.1 |

这一条说明**转述别篇的统计时必须回去看那篇的原始分组**，不能只记结论数字。

### §6.5 `sed -n` 的多段输出顺序

| 阶段 | 内容 |
|---|---|
| **初始假设** | `sed -n '298p;301p;363p;283p'` 会按我列的顺序输出四行 |
| **结果** | **sed 按文件升序输出**（283 → 298 → 301 → 363） |
| **后果** | 04 篇 batch 1 有 **22 处行号被映射错**（详见 §7.3 第 4 条） |
| **纪律** | **`sed` 的行号规格一律按升序列**；跨文件取证一律用 `echo ===` 分段 |
| **落到哪里** | §8.2、§2.5 |

---

## §7 逐篇取证表

> 格式：每篇列「关键论断 → 出处」和「本轮自审的修正」。
> ⚠️ 提到"某处原先写错了"时**用文字描述那个错误值**，不写成 `文件:行号` 形式（§8.4）。

### §7.1 逐篇关键论断的出处

| 篇 | 关键论断 | 出处（一等证据） |
|---|---|---|
| **00** | 没有 `bus_type` ⇒ 框架型 | `grep -c 'struct bus_type'` 全 0 |
| 00 | 硬件背景六条决定框架形状 | §2.10 的映射表，每条都指向具体篇目 |
| 00 | 五条端到端路径 | 全部行号取自 `remoteproc_core.c` / `remoteproc_virtio.c` 的一等证据范围 |
| **01** | 33 个导出符号 | `grep -c EXPORT_SYMBOL`（§3.2） |
| 01 | 错误码语义与计数 | 07 §6.1 的普查（本轮重跑过） |
| 01 | API 位置只到文件 | 规则 10 |
| **02** | `struct rproc` 字段分组 | `include/linux/remoteproc.h` 逐字读 |
| 02 | 资源表三指针互斥 | `drivers/remoteproc/remoteproc_core.c:1470-1590` 逐段读 |
| 02 | `RPROC_SUSPENDED` arm64 不出现 | `grep -rn RPROC_SUSPENDED drivers/remoteproc/` 只命中 omap |
| 02 | 四个全局变量 | `remoteproc_core.c` 文件头 |
| **03** | Part A 时间线次序 | 逐条用"谁调用谁"验证（§2.4） |
| 03 | 两把锁 + RCU | `:2576` 的 `synchronize_rcu()` + panic 路径的 `rcu_read_lock()` |
| 03 | Part C 的 6 条设计债 | 3 个 TODO（§3.6）+ 4 条静态推断（§5.3） |
| **04** | 七个独立框架 | 七个 `.c` 各自的一等证据 |
| 04 | ELF 默认回调"全有或全无" | `rproc_alloc_ops()`（`drivers/remoteproc/remoteproc_core.c:2397`）逐行读 |
| 04 | 默认 coredump 什么都不做 | §6.1 的交叉验证 |
| 04 | 唯一的 `BUG_ON` | §2.6 的全树 grep |
| **05** | 三个必填回调 | `rproc_validate()` + `rproc_add_virtio_dev()` 的 kick 检查 |
| 05 | 两个 devm 包装的粒度不同 | `drivers/remoteproc/remoteproc_core.c:2332`（`devm_add_action_or_reset`）vs `:2603`（`devres_alloc`） |
| 05 | rpmsg 骨架 | 对照 `samples/rpmsg/rpmsg_client_sample.c` 逐行 |
| **06** | 核心只读 1 条 DT 属性 | `grep -n 'of_property_read' drivers/remoteproc/remoteproc_core.c` 只有一处 |
| 06 | 三方分工 | 每条属性都给了消费者的行号 |
| 06 | arm64 完整实例 | 三段全部原样摘自本树 DTS（给了行号） |
| **07** | 零 tracepoint | §2.6 的两条命令 |
| 07 | 零 `_once` / 零 `_ratelimited` | §2.6 的循环命令 |
| 07 | 错误码 94 个实际点 | §3.4 |
| **08** | 八个主题 | 每个主题的"回到代码"段都带行号 |
| 08 | 每个类比都给了误导边界 | 篇内每节第四段 |
| **99** | 本篇全部数字 | §3 每行一条命令 |

### §7.2 三个自检脚本的最终结果

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/remoteproc
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh staging    $D .
bash    $S/check_doc_coverage.sh remoteproc $D .
```

| 脚本 | 结果 |
|---|---|
| `check_line_refs.py` | **干净** —— 无解析不到的文件、无越界行号 |
| `check_xrefs.py` | **0 条失效** |
| `check_doc_coverage.sh staging` | 相关的 2 篇（`remoteproc` / `rpmsg`）都有引用；6 篇 0 引用是同目录无关文件（§2.3） |
| `check_doc_coverage.sh remoteproc` | 「必查」0 篇；19 篇 DT 绑定引用 1 篇（处置见 §2.3） |
| `.xref-ignore` / `.lineref-ignore` | **两个都不存在 —— 零豁免** |

**零豁免**这件事值得强调：本套没有靠 ignore 文件绕过任何一条脚本告警，
所有告警都是改文档解决的（§8 记录了改法）。

### §7.3 本套的自我修正记录（11 条）

| # | 原先写的 | 实际是 | 怎么发现的 |
|---|---|---|---|
| 1 | `rproc_get_by_phandle()` 找不到时返回 `ERR_PTR(-EPROBE_DEFER)` | 返回 **NULL** | 逐行读函数体 |
| 2 | `rproc_get_by_child()` 是"从子设备往上找 rproc"的通用机制 | 它只是 `dev->parent->driver_data` 的一层薄包装，**依赖驱动把 rproc 存进了 drvdata** | 读实现 |
| 3 | `remoteproc_internal.h` 里的东西"驱动可以用" | 它**只被 `drivers/remoteproc/` 目录内的文件包含，目录外 0 个** ⇒ 不对外开放 | §2.6 的三条 grep |
| 4 | **04 篇 batch 1 有 22 处行号偏移** | 见下方明细 | `sed -n` 按文件升序输出（§6.5） |
| 5 | `rproc_set_firmware()` 失败时返回 `-EBUSY`（措辞含糊，未指明分支） | 明确为：远端不在 `RPROC_OFFLINE` 时以 `ret = -EBUSY` 形式返回（`drivers/remoteproc/remoteproc_core.c:2186`），并配一行日志 | 逐行读 |
| 6 | 把 `rproc_alloc_ops()` 的**两层默认**混为一谈 | 第一层是 `rproc_alloc_ops()` 里"`ops->load` 为空则五个一起填"；第二层是 `remoteproc_internal.h` 的包装函数在回调仍为空时返回 0 / NULL。**这是本套最大的一处修正**，它改变了 04 §2.2 的整节结构 | 对读 `remoteproc_core.c:2397` 与 `remoteproc_internal.h:180`/`:200` |
| 7 | 02 §1.0.B 里两个字段名写错 | 已按 `include/linux/remoteproc.h` 逐字更正 | 逐字对读头文件 |
| 8 | 04 §4.4「全核心唯一一处 `-EBUSY`」 | 共 **3** 处（§6.4） | 逐文件计数 |
| 9 | 05 §3.3 的四处行号 | `of_resm_idx` 赋值、`rsc_offset` 初始化、`kzalloc` 三行、以及 `rproc_ops` 注释里 `RSC_HANDLED/RSC_IGNORED` 那段说明，**四处都往后偏了 1–2 行**，已按 `sed` 实测更正 | 写完后回头 `sed` 核对 |
| 10 | 08 §3 把"ouch, hacky!"那句注释定位到 `rproc_handle_vdev()` 的**函数体**里 | 它在**函数文档注释**里（`drivers/remoteproc/remoteproc_core.c:471`） | §3.6 的 TODO/hacky 普查 |
| 11 | 07 §2.3 把 `dev_dbg(dev, "enter %s\n", __func__)` 归给 `rproc_trigger_recovery()` | 它在 **`rproc_crash_handler_work()`**（函数定义在 `drivers/remoteproc/remoteproc_core.c:1862`，那句 `dev_dbg` 在 `:1867`） | 打开 1855-1900 区段核对 |

**第 4 条（22 处）的明细** —— 全部是"我列 `sed` 规格的顺序"和"sed 实际输出顺序"错配导致：

| 类别 | 涉及的对象 |
|---|---|
| virtio 三对象的创建点 | `rproc_vdev` 的 `devm_kzalloc`、`virtio_device` 的 `register_virtio_device` |
| `rproc_handle_vdev()` 的三处检查 + `struct_size` | 四处 |
| vring 相关 | `idr_alloc`、notifyid 回写、`max_notifyid` 更新、`rproc_mem_entry_init` 调用、`if (rproc->table_ptr)`（并补上了长注释的行号范围） |
| virtio 配置访问 | `rproc_virtio_config_ops` 定义、`cfg = &rsc->vring[...]`、`vq->priv` 赋值、`idr_find` 三行 |
| 两处引用计数 | `get_device(&rproc->dev)` / `put_device`、`get_device(dev)` / release 里的 `put_device` |
| vdev buffer 三分支 | 两个分支的行号范围 + `dev_warn` + `dma_declare_coherent_memory` |
| `BUG_ON` | 一处 |
| 附带的范围修正 | 「唯一一处 `BUG_ON`」的断言被**重新限定**为「核心七个 `.c` 里」，并补出两处 `WARN_ON` |

**这 22 处全部在同一次 Python heredoc 里用带编号的 `assert` 逐条改掉**，
每条都先断言原文存在再替换 —— 这样漏改会立刻报错，不会静默。

### §7.4 ⚠️ 本轮 review 自己新产生的问题

按方法论第 15 条「每一轮 review 自己会产生新的错误」：

| 新问题 | 处置 |
|---|---|
| 06 篇引用带逗号的 yaml 文件名 + 行号 ⇒ `check_line_refs.py` 报 UNRESOLVED | 改成**不带行号**引用，并在正文就地说明理由（§8.5） |
| 07 篇在跨子系统对比里把「regulator」和它的篇号-小节号并排写成了 `NN §X.Y` 形式 ⇒ `check_xrefs.py` 误判为**本套** 07 篇的那一节 | 改写成「regulator 那套的 NN 篇 §X.Y」形式 defuse（§8.6）。⚠️ 本行也必须避开那个字面形式，否则连本篇一起污染 |
| 本篇 §7.3 若照抄那 22 处的错误行号 ⇒ 会污染本篇的引用集 | 改成**文字描述**（§8.4） |

---

## §8 工具坑：三个自检脚本的已知行为

> 这一节是给下一次用同一套 skill 的人看的。每条都是本套实际踩到的。

### §8.1 `check_doc_coverage.sh`：只有"是不是 0"可信

| 行为 | 说明 |
|---|---|
| **「引用次数」不可信** | 它是 **basename 子串匹配**。本套跑出 `remoteproc 引用 1999 次` —— 那是因为"remoteproc"这个词在文档里到处出现，不是"引用了那个文档 1999 次" |
| **只认 `.yaml` DT 绑定** | 本子系统有 6 个老式 `.txt` 绑定，脚本**完全看不到**（19 vs 25，§3.1） |
| **参数是相对 `Documentation/` 的路径，不是子系统昵称** | 本子系统的框架文档在 `Documentation/staging/` 下，所以第一个参数要写 `staging` 而不是 `remoteproc` |
| ⚠️ **按目录取名的副作用** | 传 `staging` 会把整个 `Documentation/staging/` 当成"必查"，于是 `crc32` / `lzo` / `xz` / `tee` / `speculation` / `static-keys` 六个无关文件全部报 0 引用。**这不是覆盖缺口**，处置见 §2.3 |
| **单文件文档要手工登记** | `Documentation/staging/remoteproc.rst` 能被探到；但如果框架文档是一个孤立的 `.rst`，脚本可能漏 ⇒ 手工登记在 §1.4 |

### §8.2 `sed -n` 的多段规格按文件升序输出

```bash
# 你写的顺序
sed -n '298p;301p;363p;283p' file.c
# 实际输出顺序：283 → 298 → 301 → 363
```

⇒ **纪律：`sed` 的行号规格一律按升序列**。
本套因为违反这一条，在 04 篇产生了 22 处行号错配（§7.3 第 4 条）——
**这是本套单次代价最大的一个坑**。

更稳的做法：一次只取一段，用 `echo ===` 分隔多次调用的输出：
```bash
echo "=== 283 ==="; sed -n '283p' file.c
echo "=== 298 ==="; sed -n '298p' file.c
```

### §8.3 `check_line_refs.py` 会解析代码块和 ASCII 图里的 `path:NNN`

| 行为 | 说明 |
|---|---|
| 它抓**任何** `path:NNN` token | 包括 ` ``` ` 围栏代码块里的、ASCII 图里的、表格里的 |
| 正确的规避 | ASCII 图里**不写行号**，行号放到图下方的表里，用完整路径 |
| ⚠️ **错误的规避** | 加 `--ignore` 条目 —— 那只是把问题藏起来 |
| `--ignore` 的真实适用范围 | **只对"行号越界"生效**，对"文件名解析不到"无效 |

本套从 remoteproc 04 篇起就在**篇首声明**了这条纪律：
「本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里」。
⇒ 结果是 04–08 五篇**一次告警都没有**。

### §8.4 99 篇的三个自污染陷阱

写取证篇时，下面三种写法会让本篇自己的引用集变脏：

| 陷阱 | 表现 | 正确做法 |
|---|---|---|
| ① 在「自审修正记录」里**照抄那个错误的 `文件:行号`** | 那个错误行号会被脚本当成一条真实引用去校验 | **用文字描述**："某处的行号往后偏了 2 行"，不写成 `文件:行号` |
| ② 引用自检脚本自己的路径 + 行号 | `~/.claude/...` 不在源码树里，脚本解析不到 | 只写脚本名和它的相对位置，不带行号 |
| ③ 为了演示 `check_xrefs.py` 的假阳性，**写出那个字面串** | 演示本身就会触发同一个告警 | 描述它的**形状**（"`NN §X.Y` 形式"），不写具体数字 |

⚠️ 本套在写 §7.4 时**当场踩了第 ③ 条**（写出了另一个子系统的篇号-小节号并排形式，
被 `check_xrefs.py` 当成本套 07 篇的一节），已就地改掉。

### §8.5 `check_line_refs.py` 按逗号切基名

⚠️ DT 绑定文件名里常有逗号（`ti,k3-r5f-rproc.yaml`）。
脚本在建消歧表时**会在逗号处切开基名**，于是
「`Documentation/devicetree/bindings/remoteproc/` 下那个逗号文件名 + 冒号 + 行号」这样的引用，
会被报成"逗号后半截.yaml 解析不到"。
⚠️ 注意本行**故意不写出那个字面形式** —— 写出来就会把本篇自己也污染一次
（这是 §8.4 陷阱 ③ 的又一种形态，本套在这里第二次踩到）。

⇒ **纪律：文件名含逗号的一律不写行号**，改为描述性引用
（"该文件的 `memory-region` 描述段"）。06 §3.4 就地记录了这条理由。

同类的两个特例（沿用前几套的结论）：顶层 **`Makefile`** 和 **`Kconfig`** 也一律不带行号。

### §8.6 `check_xrefs.py` 的两种假阳性与 defuse 手法

| 假阳性 | 原因 | defuse |
|---|---|---|
| 一行里 `NN-xxx.md` 文件名后 60 字符内出现「本篇 §X」 | 正则把 §X 归给了那个文件名 | 调整语序，让文件名和「本篇」隔开 |
| 跨子系统引用（提到别套文档的篇号 + 小节号） | `XREF_BARE` 只看 `NN §X` 的形状，不区分是哪一套 | 写成「**NN 篇 §X**」——脚本**不匹配**这个形式 |

本套一共用到 defuse 一次（§7.4 那条），其余告警都是真问题、都改了正文。

### §8.7 环境坑速查（沿用前几套，本轮重新确认）

| 坑 | 规避 |
|---|---|
| 默认 `grep` 是 **ugrep 7.5.0** | 统计一律用 `/usr/bin/grep`（GNU 3.7） |
| ugrep 不认 `\s` | 用 POSIX `[[:space:]]` |
| ugrep 默认遵守 `.gitignore` | 同第 1 条 |
| 递归扫 `arch/arm64/boot/dts/` 超 2 分钟 | 先 `grep -rl` 按 compatible 反查文件，再单文件 `sed` |
| Bash 的 cwd 跨调用保留、shell 变量不保留 | `cat >>` 一律用**绝对路径** |
| Python heredoc 里的替换串含 ASCII `"` | 一律用三引号字符串 |
| `assert` 失败时不要凭记忆猜原文 | 去 grep 那个特征串，拿回真实措辞 |

---

## §9 rule-29 对照：本套没有对照集

| 项 | 情况 |
|---|---|
| `output/qoder/remoteproc/` | **不存在** |
| `output/qoder/` 下有什么 | 只有 `spi` 一套 |
| ⇒ 处置 | **无对照套**。本套的质量控制完全依赖三个自检脚本 + §7.3 的 11 条自我修正 |

上一次做过 rule-29 对照的是 spi 那一套（结论已吸收进 skill）。
本套沿用了那次吸收的全部规则，其中在 remoteproc 上体现最明显的三条：

| 规则 | 在本套的体现 |
|---|---|
| 02 篇开篇先立「先分清 N 组关系」 | 02 §1.0 否掉了"rproc 和 rproc_ops 是 device/driver 关系"这个错误类比 |
| 04 篇要有"哪几个在目标架构上真会碰到"的分诊表 | 04 §0.1 的 8 行分诊表 |
| 08 篇必含"最容易被类比错的那组关系" | 08 §1 的 remoteproc / virtio / rpmsg 三层 |

---

## §10 这套文档怎么用

| 你的处境 | 建议 |
|---|---|
| 想快速上手 | 00 → 08 → 05。**§5.1 提醒你 05 的代码没编译过** |
| 在排查线上问题 | 07 §8 速查 → 06 §8 错误映射。这两处的每一句报错都能在源码里 grep 到 |
| 要基于本套做设计决策 | **先读 §5.3** —— 那八条是"代码事实确定、后果是推断"的 |
| 要把本套移到别的内核版本 | 先跑 §5.4 那条命令，再 diff `include/linux/remoteproc.h` |
| 要在非 arm64 上用 | **§2.7 就是你要补读的清单** |
| 想复核某个数字 | §3 每一行都配了命令 |
