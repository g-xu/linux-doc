---
title: 取证附录
---

# of 99：取证、方法与未验证项

这一篇不讲 of 怎么工作，讲**前九篇的每个论断是怎么来的、哪些没验证过**。
定位和前九篇不同：它决定你该在哪里加倍小心。

读法：
- 想知道"某个数字可信吗" → §3，每条都带能一条命令重跑的复现命令
- 想知道"哪些结论是推断出来的" → §5，四层分层
- 想知道"哪些地方源码本身容易看错" → §6
- 想知道"这套文档自己犯过什么错" → §7.3

---

## §1 基线

### §1.1 树的状态：干净，of 未被改动

```bash
git log -1 --format='%H %s'
# → f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158
```

这棵树带了一批 out-of-tree 补丁（`git status` 在 `arch/`、`drivers/net/`、
`drivers/gpu/` 等处有大量 modified），所以**必须单独确认 of 有没有被碰过**：

```bash
git status --porcelain -- drivers/of include/linux/of.h include/linux/of_*.h scripts/dtc
# → 无输出
```

**无输出 ⇒ 本套文档描述的是未经改动的 6.1.158 上游 of。**
这一条是整份文档定性的前提：如果 of 被打过补丁，所有行号和行为描述都要重新定性。

### §1.2 规模

```bash
wc -l drivers/of/*.c drivers/of/*.h | tail -1        # → 16297
wc -l include/linux/of.h include/linux/of_*.h | tail -1   # → 3059
```

| 分组 | 文件 | 行数 | 处置 |
|---|---|---|---|
| 核心主体 | `base.c` | 2213 | 全文读 |
| 属性/地址 | `property.c` / `address.c` | 1485 / 1147 | 全文读 |
| 扁平树 | `fdt.c` / `fdt_address.c` | 1398 / 249 | `fdt.c` 全文读；`fdt_address.c` 只读符号表 |
| 动态 | `dynamic.c` / `overlay.c` / `resolver.c` | 937 / 1266 / 340 | `dynamic.c` 全文读；后两个读主流程 |
| 设备填充 | `platform.c` / `device.c` | 795 / 398 | 全文读 |
| 中断 | `irq.c` | 758 | 读主流程 |
| 其它 | `of_reserved_mem.c` / `kexec.c` / `kobj.c` / `of_numa.c` / `pdt.c` | 450 / 472 / 165 / 186 / 213 | 见 §2.2、§2.7 |
| 测试 | `unittest.c` | 3647 | 只读符号表 |

**`unittest.c` 一个文件占全目录 22.4%**（`3647/16297`），是本目录最大的单文件——
比 `base.c` 还大 65%。它不参与运行期路径，只在 `CONFIG_OF_UNITTEST` 下编译
（`drivers/of/Makefile:10`），所以只读符号表（233 个 `unittest(` 断言点）。
这个占比本身是一条结论：**of 是内核里测试覆盖异常认真的子系统**（04 §5）。

### §1.3 子系统类型判定：框架型

按 skill 阶段 1 步骤 3b 的两道门：

```bash
# 门 1：有没有自己的 bus_type
grep -rn 'struct bus_type' drivers/of/*.c include/linux/of.h include/linux/of_*.h
# → 无输出
```

门 1 无输出 ⇒ **框架型**，门 2 不必跑（顺带确认了 `include/uapi/linux/of*` 一个都没有，
of 没有跨边界 uapi ABI——DT 的"稳定 ABI"是**绑定文档**层面的，不是 C 结构层面的，见 08 §7）。

**但 of 是框架型里的一个特例，这一点决定了 02 和 06 怎么写**：它自己没有总线，
却是 `platform_bus_type` 上绝大多数设备的**来源**。所以：
- 02 §2 画的不是"总线 ↔ 父总线"，而是「`device_node` 树 ↔ `platform_device` ↔ 消费者驱动」三方关系
- 06 §0 用的是**发现路径分诊表**而不是"有没有 DT 绑定"——of 本身就是那条路径的实现者
- 08 §8 专门讲"两套 bus 抽象名字还一样"（DT 里的 `simple-bus` 节点 vs 驱动模型的 `bus_type`），
  这正是类型判定这一步暴露出来的最容易混的一组概念

### §1.4 官方文档登记：脚本报"0 篇"是假的通过

`check_doc_coverage.sh` 的输出是：

```
=== 【必查】概念/框架文档 ===
没找到 Documentation/of/、driver-api/of* 或 bindings/of/。
```

**这不是"没有官方文档"，是脚本按 `of` 这个名字找不到目录。**
of 的官方文档全在 `Documentation/devicetree/` 下（因为文档是按"设备树"这件事命名的，
不是按 `drivers/of/` 这个目录名）。这正是 skill 规则 28 那条 警告的实例——
「共 0 篇 → 必查档全部有引用」是假通过，必须手工确认真实文件名。

实际存在 7 篇，共 947 行，**全部读过且全部在正文里被引用**：

| 文件 | 行数 | 纳入了什么 | 引用次数 |
|---|---|---|---|
| `usage-model.rst` | 420 | 三阶段模型（早期扫描/unflatten/填充）、`simple-bus` 白名单的官方说法 | 4 |
| `of_unittest.rst` | 226 | unittest 的数据构造方式 | 2 |
| `overlay-notes.rst` | 150 | overlay 的生效顺序与限制 | 2 |
| `kernel-api.rst` | 57 | API 分组的官方口径 | 1 |
| `changesets.rst` | 37 | changeset 两阶段提交语义 | 1 |
| `index.rst` | 30 | 文档集入口 | 1 |
| `dynamic-resolution-notes.rst` | 27 | phandle 重解析时机 | 1 |

复现：

```bash
ls Documentation/devicetree/*.rst | while read f; do echo "$(wc -l <$f) $f"; done
grep -ohE 'Documentation/devicetree/[a-z_-]+\.rst' output/of/0*.md | sort | uniq -c
```

**处置类别：(b) 文档目录名与子系统目录名不一致**——不是"没有文档"（cdev 那套是 (d)），
所以本套**没有**登记 `.doc-coverage` 豁免，而是手工建了上面这张表。

### §1.5 ⚠️ 文档与代码的冲突

| 冲突点 | 官方文档说 | 代码实际 | 本套怎么处置 |
|---|---|---|---|
| 引用计数 | `changesets.rst` 与 `dynamic-resolution-notes.rst` 通篇按"节点有引用计数"叙述 | 只有 `CONFIG_OF_DYNAMIC=y` 时才是真的；否则是空函数（`include/linux/of.h:122-131`） | 以代码为准，03 §B6、§C3 和 08 §6 三处显式写出这个分叉 |
| `simple-bus` | `usage-model.rst` 举 `simple-bus` 为例 | 白名单实际是 `of_default_bus_match_table[]`，还含 `simple-mfd`、`isa`、`arm,amba-bus`（06 §7.2） | 以代码为准，列全 4 项 |

---

## §2 证据等级

### §2.0 ⚠️ 这一篇的一个特殊说明：阅读深度是反推出来的

**必须先说清楚**：前九篇是在另一个会话里写完的，那个会话在写完 08 之后
撞上了 1M 上下文硬上限而永久失效，**当时逐文件的"读到哪一行"记录随之丢失**。
所以 §2.1／§2.2 的分级不是从写作过程的记录里抄出来的，而是**从成品的引用分布反推**的：

```bash
grep -ohE '(drivers/of/[a-z_]+\.[ch]|include/linux/of[a-z_]*\.h)' output/of/0*.md \
  | sort | uniq -c | sort -rn
```

**判据**：一个文件被引用的**不同行号数**多、且引用散布在 ≥4 篇里 ⇒ 判为一等；
引用集中在一两个章节、只覆盖单条路径 ⇒ 判为二等。
这个反推口径比原始记录**保守**（它只能证明"引用过这些行"，不能证明"读过而没引用的行"），
所以下面的分级**可能低估**实际阅读量，不会高估。这是本套与 pinctrl／clk／cdev 各套
在方法上唯一的差别，**读者据此把本篇的证据等级本身当二等看待**。

### §2.1 一等证据：引用密集、跨篇覆盖（可当结论直接用）

| 文件 | 引用次数 | 覆盖的篇 |
|---|---|---|
| `drivers/of/base.c` | 152 | 00–08 全部 |
| `drivers/of/platform.c` | 148 | 00,01,02,03,05,06,08 |
| `include/linux/of.h` | 121 | 00–08 全部 |
| `drivers/of/address.c` | 101 | 00,01,03,06,08 |
| `drivers/of/fdt.c` | 65 | 00,03,04,07 |
| `drivers/of/dynamic.c` | 60 | 01,03,04,08 |
| `drivers/of/property.c` | 50 | 01,03,05 |

### §2.2 二等证据：只读了片段（详读范围写明）

| 文件 | 引用次数 | 详读了哪部分 | 没读的部分 |
|---|---|---|---|
| `drivers/of/overlay.c` | 25 | `of_overlay_fdt_apply()` 主路径与 `of_overlay_phandle_mutex` 一带 | 逐个 `overlay_apply_node()` 的细节、错误回滚分支 |
| `drivers/of/of_reserved_mem.c` | 25 | `fdt_init_reserved_mem()`、`of_reserved_mem_device_init_by_*()` | `early_init_dt_alloc_reserved_memory_arch()` 的架构侧 |
| `drivers/of/kobj.c` | 24 | 全文 165 行基本覆盖（sysfs 属性建立与销毁） | — |
| `drivers/of/irq.c` | 15 | `of_irq_parse_one()` / `of_irq_get()` 契约 | `of_irq_init()` 的 irqchip 初始化次序 |
| `drivers/of/device.c` | 11 | `of_dma_configure_id()` 的 dma-ranges 消费 | `of_device_uevent*()` |
| `drivers/of/resolver.c` | 5 | `of_resolve_phandles()` 入口契约 | 符号表重定位细节 |

### §2.3 三等证据：只 grep 了符号或行号，没读上下文

- `drivers/of/unittest.c`（3647 行）：只统计了 233 个 `unittest(` 断言点，没读断言内容
- `drivers/of/kexec.c`（472 行）：只确认了它重建 DTB 这件事和 2 个行号
- `drivers/of/fdt_address.c`（249 行）：只读符号表，确认它是 `address.c` 的早期版本
- `scripts/dtc/`：只用到 libfdt 的边界位置（4 处引用），**没有读 dtc 源码**
- `arch/arm64/boot/dts/ti/k3-am65.dtsi`：06 §9 的三层 `ranges` 实例，**读了引用到的 19 个位置，没通读整个 dtsi**

### §2.4 ⚠️ `check_doc_coverage.sh` 的处置

**结论：不适用，用手工表替代。**
脚本按子系统目录名 `of` 去找文档目录，而 of 的官方文档按"设备树"这件事命名
（`Documentation/devicetree/`），所以脚本报"没找到"。
这是**假的通过**，不是"没有文档"——7 篇 947 行的登记表在 §1.4，
两种输出为什么长得一样、怎么区分见 §8.1。

⇒ 本套**没有**登记 `.doc-coverage` 豁免文件。

### §2.5 索引工具的使用与盲区

cbm 只用于导航（找 `of_*` 符号的候选位置），**所有行号最终都由 `rg` + `sed` 在真实树上确认**。
本子系统上索引工具的两个盲区都命中了：

- **宏生成的符号**：`for_each_child_of_node()`、`for_each_available_child_of_node()`
  这一族遍历宏（01 §3）是宏展开出来的，索引里没有对应节点
- **函数指针间接调用**：`of_platform_populate()` → `bus->match` 的白名单回调
  （06 §7.2）是通过 `of_default_bus_match_table[]` 间接走的，索引拉不出这条边

### §2.6 全称断言的验证

本套出现的全称断言，逐条列出验证命令：

| 断言 | 出处 | 验证命令 | 结果 |
|---|---|---|---|
| of 核心里源码自陈的设计债只有一条 | 03 §C1 | `grep -rnE '(FIXME\|TODO\|XXX\|REVISIT\|HACK)' drivers/of/*.c drivers/of/*.h` | 1 条（`base.c:2046`） |
| of 一个 tracepoint 都没有 | 07 §3 | `grep -rn 'trace_' <核心 15 文件>` + `ls include/trace/events/ \| grep -iE '^of\|devicetree'` | 均为空 |
| of 核心不用 debugfs | 07 §4 | `grep -rn debugfs drivers/of/*.c` | 1 处，且只是 `fdt.c:25` 的 `#include` |
| `include/uapi/linux/of*` 不存在 | §1.3 | `ls include/uapi/linux/ \| grep -c '^of'` | 0 |

前三条都跨出了 `drivers/of/` 目录去查（`include/trace/events/`），
符合 skill 规则 30 的"全称断言要跨出子系统目录"。

### §2.7 裁掉了什么（arm64-only 的代价）

**这些不是遗漏，是显式裁掉的**：

| 裁掉的 | 为什么 | 影响到哪一篇 |
|---|---|---|
| **ACPI 路径** | arm64 上 DT 与 ACPI 二选一，本套只走 DT | 06 §0 的分诊表里保留了 ACPI 那一行但标"本篇不覆盖" |
| `drivers/of/pdt.c`（213 行） | `CONFIG_OF_PROMTREE` 只在 SPARC 上开（`drivers/of/Kconfig:49`），arm64 用不到 | 04 §7 点名说明存在但不展开 |
| `drivers/of/of_numa.c`（186 行） | 需要 `CONFIG_OF_NUMA`，arm64 服务器场景才开，本套按单节点讲 | 04 §7 只交代它存在 |
| powerpc 的 `of_platform` 历史差异 | 非目标架构 | 00 §2 的历史背景里一句话交代 |
| `early_init_dt_alloc_reserved_memory_arch()` 的架构实现 | 落在 `arch/` 与 `mm/`，超出 of 边界 | 04 §2 停在核心侧 |

---

## §3 统计数字总表（每条都能一条命令重跑）

统计范围**一律显式列文件**，不用 `drivers/of/*.c` 通配——
通配会把 3647 行的 `unittest.c` 算进去，所有错误码计数会大幅虚高。
下文 `$CORE` 指这 15 个文件：

```bash
CORE="drivers/of/base.c drivers/of/address.c drivers/of/property.c drivers/of/fdt.c \
drivers/of/dynamic.c drivers/of/overlay.c drivers/of/platform.c drivers/of/irq.c \
drivers/of/device.c drivers/of/resolver.c drivers/of/of_reserved_mem.c \
drivers/of/kexec.c drivers/of/kobj.c drivers/of/fdt_address.c drivers/of/of_numa.c"
```

### §3.1 规模类

| 数字 | 值 | 复现命令 |
|---|---|---|
| `drivers/of/` 总行数 | 16297 | `wc -l drivers/of/*.c drivers/of/*.h \| tail -1` |
| `include/linux/of*.h` 总行数 | 3059 | `wc -l include/linux/of.h include/linux/of_*.h \| tail -1` |
| `of.h` 单文件行数 | 1640 | `wc -l include/linux/of.h` |
| `unittest.c` 占比 | 22.4% | `python3 -c "print(3647/16297)"` |
| unittest 断言点 | 233 | `grep -c 'unittest(' drivers/of/unittest.c` |
| 官方文档总行数 | 947 | `cat Documentation/devicetree/*.rst \| wc -l` |

### §3.2 导出符号类

| 文件 | `EXPORT_SYMBOL*` 数 |
|---|---|
| `base.c` | 44 |
| `property.c` | 25 |
| `address.c` | 12 |
| `dynamic.c` | 11 |
| `platform.c` | 10 |
| `irq.c` | 9 |
| `device.c` | 8 |
| `overlay.c` | 5 |
| `of_reserved_mem.c` | 4 |
| `fdt.c` / `resolver.c` | 1 / 1 |
| `kexec.c` / `kobj.c` / `fdt_address.c` / `of_numa.c` | 0 |
| **合计** | **130** |

复现（逐文件而非合并，便于逐格对账）：

```bash
for f in base.c address.c property.c fdt.c dynamic.c overlay.c platform.c irq.c \
         device.c resolver.c of_reserved_mem.c kexec.c kobj.c fdt_address.c of_numa.c; do
  printf '%-22s %s\n' "$f" "$(grep -cE '^EXPORT_SYMBOL' drivers/of/$f)"
done
```

⚠️ 口径说明：数的是**行首** `EXPORT_SYMBOL`（含 `_GPL` 变体）的行数。
`EXPORT_SYMBOL` 出现在行中间的情况本目录没有，所以行数=符号数。
这个数**不等于"读者能用的 API 数"**：`of.h` 里还有大量 `static inline` 包装
（不需要导出也能用），01 篇的 API 表比 130 多。

### §3.3 错误码类

| 错误码 | 出现次数 | 主要语义（01 §14.2 / 07 §7 展开） |
|---|---|---|
| `-EINVAL` | 106 | **属性不存在**（`property.c:113`）+ 参数不合法 + `np` 为 NULL —— **三者同码** |
| `-ENOMEM` | 27 | 分配 `device_node` / `property` / changeset 条目失败 |
| `-ENOENT` | 24 | ⚠️ **phandle 列表家族专用**：列表属性不存在 / index 越界 / 空条目（`base.c:1308`、`:1331`、`:1443`）。**属性读取家族一次都不返回它** |
| `-ENODEV` | 21 | 节点存在但不可用（`status` 不是 okay），或没有匹配 |
| `-ENODATA` | 19 | **属性存在但没有值**（`property.c:115`） |
| `-EOVERFLOW` | 10 | **属性长度不足或超长**（`property.c:117`、`:119`） |
| `-EBUSY` | 5 | overlay/changeset 正在被使用 |
| `-EPROBE_DEFER` | 5 | 依赖的提供者还没注册（`of_irq_get()` 一族） |
| `-EILSEQ` | 4 | 字符串属性不是 NUL 结尾（`property.c:449`） |
| `-ENOSYS` | 2 | 编译期未开启对应 CONFIG |
| `-EFAULT` | 2 | — |
| `-ENXIO` / `-EEXIST` | 1 / 1 | — |
| `-EPERM` / `-ENOSPC` | 0 | 本子系统不返回 |

复现：

```bash
for e in EINVAL ENOMEM ENODEV ENOENT ENODATA EEXIST EBUSY EOVERFLOW EPERM \
         ENOSYS EPROBE_DEFER ENOSPC EFAULT EILSEQ ENXIO; do
  printf '%-14s %s\n' "-$e" "$(grep -how -- "-$e" $CORE | wc -l)"
done
```

⚠️ **这张表的 `-ENOENT` 一行是本轮最重要的一次修正**——初稿把它写成"属性不存在"，
连带 6 篇里 14 处照抄，详见 §6.1。

⚠️ 口径陷阱两条：
1. `grep -how` 数的是**出现次数**（同一行写两个也算两次），**不是返回点数**——
   有些出现在 `if (ret == -EOVERFLOW)` 这种判断里而非 `return` 里
2. **必须带 `-w`**。不带 `-w` 时 `-ENOENT` 会被 `-ENOENTRY` 之类子串命中；
   本目录虽然没有这类符号，但口径要能复用到别的子系统

### §3.4 可观测性类

| 数字 | 值 | 复现命令 |
|---|---|---|
| `pr_debug` | 113 | `grep -how -- pr_debug $CORE \| wc -l` |
| `pr_err` | 75 | 同上换名 |
| `pr_warn` | 19 | 同上 |
| `pr_info` | 6 | 同上 |
| `pr_notice` | 2 | 同上 |
| tracepoint | **0** | `grep -rn 'trace_' $CORE` |
| debugfs 入口 | **0** | `grep -rn debugfs drivers/of/*.c`（唯一命中是 `fdt.c:25` 的 include） |
| `kobj.c` 里 sysfs/kobject 调用 | 12 | `grep -n 'sysfs_\|kobject_' drivers/of/kobj.c \| wc -l` |

**`pr_debug`(113) : `pr_info`(6) ≈ 19:1** 是 07 §2 和 03 §C4 的数据基础：
of 把几乎全部可观测性押在**默认不输出**的 `pr_debug` 上，
所以"什么都看不到"是它的常态，而不是出了问题。

### §3.5 锁类

| 锁 | 位置 | 保护什么 |
|---|---|---|
| `devtree_lock`（raw spinlock） | `drivers/of/base.c:56` | 树结构的读写（可在原子上下文取） |
| `of_mutex` | `drivers/of/base.c:51` | changeset / overlay 的串行化 |
| `of_fdt_unflatten_mutex` | `drivers/of/fdt.c:438` | 运行期 unflatten |
| `of_rmem_assigned_device_mutex` | `drivers/of/of_reserved_mem.c:314` | reserved-mem 的设备分配 |
| `of_overlay_phandle_mutex` | `drivers/of/overlay.c:110` | overlay 的 phandle 创建 |

复现：`grep -rn 'DEFINE_RAW_SPINLOCK\|DEFINE_MUTEX' drivers/of/*.c`

03 §B1 只讲了前两把（"两把锁"），因为后三把都是局部串行化、
不参与"读树 vs 改树"这条主矛盾。**这不是漏，是分层**——本表补齐全部 5 把。

### §3.6 全局变量

| 变量 | 位置 |
|---|---|
| `of_root` | `drivers/of/base.c:35` |
| `of_chosen` | `drivers/of/base.c:37` |
| `of_aliases` | `drivers/of/base.c:39` |
| `of_stdout` | `drivers/of/base.c:40` |
| `of_stdout_options`（static） | `drivers/of/base.c:41` |

### §3.7 文档自身规模

| 篇 | 行数 | 章节数 |
|---|---|---|
| 00-overview | 652 | 47 |
| 01-api | 807 | 53 |
| 02-datastructures | 544 | 27 |
| 03-internals | 631 | 33 |
| 04-extensions | 447 | 29 |
| 05-usage | 510 | 26 |
| 06-devicetree | 679 | 44 |
| 07-debug | 574 | 37 |
| 08-feynman | 560 | 38 |
| **前九篇合计** | **5404** | **334** |

复现：`for f in output/of/0*.md; do printf '%s %s\n' "$(wc -l <$f)" "$f"; done`

---

## §4 工具版本

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本 |
| ripgrep | 13.0.0 | 全树取证主力 |
| git | 2.34.1 | 基线确认 |
| GNU sed | 4.8 | 逐行核对 |
| cbm (codebase-memory MCP) | 见 §2.5 | **仅导航**，不用于取证 |

⚠️ `dtc` 未跑（本套没有编译任何东西，见 §5.1），所以 06 §8.2 里的
`dtc -I dtb` / `make dtbs_check` 命令是**从源码和文档抄的，没有实测输出**。

---

## §5 未验证项（四层分层）

### §5.1 第一层：未编译、未运行

**本套文档从头到尾没有编译过一行代码，没有跑过一次内核。**

- 05 篇的所有驱动骨架**未编译**（该篇篇首已声明）。它们是"形状正确"的示意，
  不是可直接 `make` 的代码：省略了头文件、错误路径的 `goto` 标签、`MODULE_*` 宏
- 06 §9 的 DTS 片段取自本树真实节点，**但没有 `dtc` 编译验证过**
- 07 篇所有 `ls` / `hexdump` / `dmesg` 命令的**输出没有实测**——
  路径和文件名是从 `drivers/of/kobj.c` 的 sysfs 建立逻辑推出来的
- 06 §10 / 07 §6 的定位流程**没有在真机上走过一遍**

### §5.2 第二层：只读了片段（见 §2.2、§2.3）

高风险的三处：

| 内容 | 风险 |
|---|---|
| 04 §1 的 overlay 生效顺序 | 只读了 `of_overlay_fdt_apply()` 主路径，**错误回滚分支没读** ⇒ "失败时是否完全回滚"这个论断是按注释写的 |
| 01 §8 / 04 的中断部分 | `irq.c` 只读了 `of_irq_parse_one()` 契约，**`of_irq_init()` 的 irqchip 初始化次序没读** ⇒ 涉及"中断控制器什么时候可用"的话都偏保守 |
| 04 §2 的 reserved-memory | 架构侧分配（`arch/` + `mm/`）没跟进去 ⇒ 只讲到"核心把区间记下来"为止 |

### §5.3 第三层：推断而非观察

| 论断 | 出处 | 推断的依据 | 为什么没法直接观察 |
|---|---|---|---|
| phandle 哈希缓存能显著减少遍历 | 03 §B3 | 读了缓存的建立与命中逻辑 | **没有性能数据**——没跑过内核，不知道实际命中率 |
| `-EINVAL` 占 46% 主要来自参数检查 | 01 §14.1 | 抽看了 `property.c` 里的检查模式 | 没有逐条分类 106 处 |
| sysfs 视图与 `.dtb` 文件不是同一份数据 | 04 §4.4 | 读了 `kobj.c` 的属性建立逻辑（从内存里的 `device_node` 生成） | 没在真机上 diff 过两者 |
| overlay 在 arm64 生产环境基本不用 | 04 §1 | `CONFIG_OF_OVERLAY` 默认不开、依赖 `OF_DYNAMIC` | 属于生态判断，本树无法证实 |
| `pr_debug` 是"默认不输出"⇒ of 平时很沉默 | 03 §C4 · 07 §2 | `pr_debug` 需要 `CONFIG_DYNAMIC_DEBUG` + 运行期开关 | 没实际开过看输出量 |

### §5.4 第四层：版本时效性

- 所有行号**只对 6.1.158 有效**。of 是活跃子系统，6.6/6.12 上：
  `of_property_read_*` 家族增加了 `of_property_present()` 等新入口，
  `fwnode` 相关代码有较大重构 ⇒ **跨版本使用本套请先重跑 §3 的命令**
- `include/linux/of.h:132` 有一行 `DEFINE_FREE(device_node, ...)`
  （`__free(device_node)` 的自动释放支持）。这是较新的 cleanup 机制被回移到 6.1.y 的痕迹，
  **本套正文没有讲它**——如果你在 6.1.158 之后的 stable 上工作，这类回移会继续增加
- 绑定文档（`Documentation/devicetree/bindings/`）的数量和内容随版本变化很快，
  06 §8 只讲机制不列清单，就是为了不引入时效性负债

---

## §6 交叉验证实例：初始假设被源码推翻的五条

### §6.1 ⚠️`-ENOENT` 是"属性不存在"吗？——**不是**（本轮最大的一条）

**初始假设**（前九篇初稿一致采用）："`of_property_read_u32()` 读不到属性时返回 `-ENOENT`，
所以可选属性的标准写法是 `if (ret == -ENOENT) use_default();`"。

**推翻它的源码**：

```bash
sed -n '107,125p' drivers/of/property.c
```

`of_find_property_value_of_size()` 的四条检查里**没有 `-ENOENT`**：
属性不存在走 `drivers/of/property.c:113` 的 `ERR_PTR(-EINVAL)`。
字符串家族 `of_property_read_string()` 同样在 `drivers/of/property.c:445` 返回 `-EINVAL`。

`-ENOENT` 的真实归属是 **phandle 列表家族**（`drivers/of/base.c:1308`、`:1331`、`:1443`），
源码在 `drivers/of/base.c:1469-1470` 的注释里把它和 `-EINVAL` 并列写清了。

**实际危害**：按初稿写的 `if (ret == -ENOENT)` 分支**永远不会进**。
放在"老 DTB 缺新属性时用默认值"的场景里（08 §7 讲的向后兼容），
后果是老 DTB 上直接走进"必需属性缺失"的报错路径 ⇒ **向后兼容当场失效**。
这是本套唯一一条"照抄就出 bug"的错误。

**修正范围**：6 篇 14 处（00 两处、01 三处、02 一处、03 两处、05 五处、06 一处、07 四处、08 一处，
其中 01 新增了一个专讲这件事的小节）。**修完还发现两个附带的漏项**：
`-ENODATA`(19) 和 `-EILSEQ`(4) 原本不在 §3.3 的错误码表里，一并补上。

**为什么会错**：`ENOENT` 字面就是 "No such file or entry"，
和"属性不存在"在直觉上完全吻合——**这是一处源码本身反直觉的地方**，
所以 01 §14.2.1 和 08 §7 都显式写了"这里最容易写成 `-ENOENT`"。

### §6.2 of 有官方文档吗？——有 7 篇，脚本找不到

**初始假设**：`check_doc_coverage.sh` 报"没找到 `Documentation/of/`、`driver-api/of*`"，
按字面读就是"of 没有独立官方文档"（cdev 那套就是这个结论）。

**推翻它的命令**：

```bash
ls Documentation/devicetree/*.rst
```

7 篇、947 行，其中 `usage-model.rst`(420 行) 是**整份文档最重要的三等证据来源**——
03 篇的"早期扫描 / unflatten / 填充"三阶段划分直接来自它。

**机理**：脚本按**子系统目录名**（`of`）去猜文档目录名，
而 of 的文档是按**这件事的名字**（`devicetree`）组织的。
这正是 skill 规则 28 那条 警告的实例。**处置见 §1.4 和 §8.1。**

### §6.3 `of_node_get()` / `of_node_put()` 是真的引用计数吗？——一半是

**初始假设**：名字带 `get`/`put`，又有 `EXPORT_SYMBOL`，那就是标准的引用计数。

**推翻它的源码**：

```bash
sed -n '120,132p' include/linux/of.h
```

`include/linux/of.h:122-131`：只有 `CONFIG_OF_DYNAMIC=y` 时才是真的
（`drivers/of/dynamic.c:33` 的 `kobject_get()`）；否则是**空的 `static inline`**，
而且注释就写着 `Dummy ref counting routines - to be implemented later`。

**arm64 defconfig 上 `CONFIG_OF_DYNAMIC` 默认不开**
（`drivers/of/Kconfig:58` 只在 `OF_UNITTEST` 下才提供选项）
⇒ **绝大多数 arm64 内核上这两个函数是空操作**。

但**代码仍然必须配对调用**——因为开了 `OF_DYNAMIC` 的内核上它是真的，
而且 `drivers/of/dynamic.c:336` 有专门的 `pr_err("ERROR: Bad of_node_put()...")` 会抓不配对。
这条被写进 03 §B6、03 §C3 和 08 §6 三处。

### §6.4 of 这么老的子系统应该有一堆 FIXME/TODO 吧？——全目录只有 1 条

**初始假设**：of 的代码从 2005 年 PowerPC OF 时代演化而来，历史包袱重，
`grep FIXME` 应该能挖出一批设计债（这是 03 Part C 的常规素材）。

**推翻它的命令**：

```bash
grep -rnE '(FIXME|TODO|XXX|REVISIT|HACK)' drivers/of/*.c drivers/of/*.h
```

**只有 1 条**，在 `drivers/of/base.c:2046`，而且内容只是解释一个
`(char *)` 强制转换为什么存在（为了压掉编译警告），**不是设计债**。

**所以 03 §C1 的写法改成了"源码里的自陈问题：只有一条，而且不算债"**，
并把设计债的论证改为从**行为**上推（错误码语义混淆、`of_node_get/put` 的半真半假、
`of_find_*` 返回 NULL 而不是错误码），而不是靠注释里的自陈。
这条反过来是一条正面评价：**of 核心的代码卫生比预期好得多**。

### §6.5 `-EINVAL` 只表示"调用者传错了"吗？——它同时表示"属性不存在"

初稿在 01 §14.2 里把 `-EINVAL` 解释成"`np` 或 `propname` 为 NULL ⇒ 你的代码有 bug"。
实际上 `__of_find_property()`（`drivers/of/base.c:191-208`）对 NULL 节点
在 `drivers/of/base.c:196` 就 `return NULL`，和"属性没找到"走的是**同一个出口**
⇒ 上层的 `if (!prop) return ERR_PTR(-EINVAL)` 把两种情况压成了一个码。

**这是 of 真实存在的一处错误码设计缺陷**：驱动拿到 `-EINVAL` 分不出
"DTS 少写了属性"（应该用默认值）和"我拿到的 `np` 是空的"（自己的 bug）。
写进了 03 §C2 的评价里，是 §6.1 那条修正的直接副产品。

---

## §7 逐篇取证表

### §7.1 逐篇关键论断 → 出处

| 篇 | 关键论断 | 出处 |
|---|---|---|
| 00 | of 的三阶段：早期扫描 / unflatten / 填充 | `Documentation/devicetree/usage-model.rst` + `drivers/of/fdt.c` + `drivers/of/platform.c` |
| 00 | `devtree_lock` 是 raw spinlock，所以中断上下文能读 DT | `drivers/of/base.c:56` |
| 01 | 导出符号 130 个，`base.c` 独占 44 | §3.2 的逐文件计数 |
| 01 | 属性读取家族的四个错误码 | `drivers/of/property.c:107-125`、`:440-452`（§6.1 修正后） |
| 01 | `-EPROBE_DEFER` 的三个来源 | `drivers/of/irq.c:475`、`drivers/of/device.c:224`、`drivers/of/property.c` |
| 02 | `device_node` / `platform_device` / `struct device` 三者关系 | `include/linux/of.h` + `drivers/of/platform.c` |
| 02 | 五把锁里只有两把参与"读树 vs 改树" | §3.5 的全量锁表 |
| 03 | unflatten 是两遍扫描 + 一次大分配 | `drivers/of/fdt.c` |
| 03 | 地址翻译逐层进行 | `drivers/of/address.c` 的 `__of_translate_address()` |
| 03 | 源码自陈设计债只有一条且不算债 | `drivers/of/base.c:2046`（§6.4） |
| 04 | `unittest.c` 占全目录 22.4% | §1.2 |
| 04 | `memory-region-names` 有 API 但 remoteproc 里零使用者 | `drivers/of/of_reserved_mem.c:396` + 跨子系统 grep |
| 05 | 可选属性判 `-EINVAL` 而不是 `-ENOENT` | §6.1 |
| 06 | 填充白名单实际有 4 项 | `of_default_bus_match_table[]`（`drivers/of/platform.c`） |
| 06 | 三层嵌套 `ranges` 的真实实例 | `arch/arm64/boot/dts/ti/k3-am65.dtsi`（19 处引用） |
| 07 | of 一个 tracepoint 都没有 | §2.6 的两条命令 |
| 07 | `pr_debug`:`pr_info` ≈ 19:1 | §3.4 |
| 08 | phandle 不是指针，是一个 u32 标签 | `drivers/of/base.c` 的 phandle 查找逻辑 |
| 08 | DT 的"稳定 ABI"是绑定文档层面的，不是 C 结构层面 | §1.3 门 2（无 uapi 头文件） |

### §7.2 三个自检脚本的最终结果

| 脚本 | 结果 |
|---|---|
| `check_line_refs.py` | 见 §7.5（定稿后重跑的数字） |
| `check_xrefs.py` | 见 §7.5 |
| `check_doc_coverage.sh` | **不适用**——它按 `of` 找不到文档目录，实际文档在 `Documentation/devicetree/`（§1.4、§8.1）。**手工建表替代**，7 篇全部读过且全部被引用 |

两个 `.ignore` 文件**都不存在** ⇒ **零豁免**：

```bash
ls output/of/.xref-ignore output/of/.lineref-ignore
# → 两条 No such file or directory
```

### §7.3 本套的自我修正记录

⚠️ **先说清一件事**：前九篇是在另一个会话里写的，那个会话撞上 1M 上下文硬上限后
永久失效（§2.0），**它当时做过的自我修正没有留下记录**。
下面五条是**收尾这一轮**（写 99 + 跑脚本）做的修正，前九篇写作过程中的修正**已丢失**。
⇒ 读者应当假设"前九篇里还有未被记录的修正过程"，而不是"只修过这五处"。

| # | 修正 | 严重度 | 范围 |
|---|---|---|---|
| 1 | `-ENOENT` → `-EINVAL`（属性读取家族） | **ERROR（照抄出 bug）** | 6 篇 14 处 + 新增 01 §14.2.1 |
| 2 | 错误码表漏了 `-ENODATA`(19) 和 `-EILSEQ`(4) | 遗漏 | 99 §3.3 + 01 §14.2 |
| 3 | `-EINVAL` 的语义从"调用者 bug"改为"属性不存在 + 调用者 bug 同码" | 内容错 | 01 §14.2 + 03 §C2 |
| 4 | 两处跨文档集章节号引用被脚本误判 | 工具假阳性 | 01、06 各一处，改为按主题指路（§8.2） |
| 5 | `03 §B1` 只讲两把锁，99 §3.5 补齐全部 5 把并说明这是分层不是漏 | 完整性 | 99 §3.5 |

### §7.4 ⚠️ 本轮自己新产生的问题

按 skill 第 15 条（自审的对象必须包含自审这一轮新写的文字），
本轮**自己引入并当场发现**的错误：

- 写 §3.3 的错误码表时，**把初稿那条 `-ENOENT` 语义原样抄了进来**——
  也就是说本篇差点成为这条错误的第 15 处。是在为 §6 找交叉验证素材、
  回源码读 `of_find_property_value_of_size()` 时才发现的
- 写 01 §14.2 时**行号写错了两处**：`property.c` 的 `-EINVAL` 写成 `:115`（实际 `:113`）、
  `-ENODATA` 写成 `:117`（实际 `:115`）。原因是凭 grep 结果里相邻的行号推的，
  没有先建引用台账（skill 第 7.5 条正是为了防这个）
- ⇒ **两条都印证同一件事**：`-ENOENT` 这类"直觉正确"的论断和"相邻行号"
  是最容易带着错继续传播的两种材料

### §7.5 定稿后重跑的脚本数字

| 指标 | 值 |
|---|---|
| `check_line_refs.py` 扫到的唯一 `文件:行号` 引用 | 见下方重跑命令的输出 |
| `check_xrefs.py` 命中的跨篇引用 | 同上 |
| 忽略项 | 0 / 0 |

⚠️ **这两个数字故意不写死在表里**：本篇自己就在被这两个脚本扫描，
把数字写成"扫描 NNN 条"的原文格式，下一轮会连着这句话一起再解析一遍。
重跑命令：

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
python3 $S/check_xrefs.py     output/of
python3 $S/check_line_refs.py output/of .
```

**验收标准**：两条都必须报"干净 / 0 失效"。

---

## §8 工具坑：三个自检脚本的已知行为

### §8.1 `check_doc_coverage.sh`：本轮**不适用**，但原因和 cdev 不同

| 处置类别 | 含义 | 哪套是这种 |
|---|---|---|
| (a) 正常 | 按子系统名能找到文档目录 | pinctrl（但要注意主文档名带连字符）、clk、gpio |
| (b) **目录名不一致** | 有官方文档，但目录按"这件事"命名，不按代码目录名 | **of（本套）** |
| (d) 真的没有 | 该子系统没有属于自己的官方文档 | cdev、mailbox、reset |

**(b) 和 (d) 的输出一模一样**（都是"没找到…"），
**必须手工确认真实文件名才能区分**。of 的判别方法：
子系统的"人类名字"（devicetree）和"目录名"（of）不一样时，用人类名字再找一遍。

⇒ **回写 skill 的落点**：规则 28 的 警告目前只举了 pinctrl 那种
"文件名带连字符"的情形，应补上 of 这种"整个目录名都不同"的情形。

### §8.2 跨文档集的章节号引用会被判失效，而且**不该登记忽略**

本套有两处（01 篇和 06 篇各一处）在讲 `memory-region-names` 时，
引用了 **remoteproc 那套文档**里的一个小节。
`check_xrefs.py` 按"同一行里最近的文档名"归属，所以它拿去**本套**的对应篇里找，
必然报失效。

**处置：消除触发条件，不登记忽略**（skill 规则 27）。
改法是把章节号删掉、改成按主题指路（"remoteproc 那套的设备树篇里点明过"）。
⚠️ **不要**改成"对方 06 篇 §3.5"这种写法——那仍然是一个可被解析的编号模式。

### §8.3 99 篇的三个自污染陷阱（本篇全部踩过或绕过）

| 陷阱 | 机理 | 本篇怎么绕 |
|---|---|---|
| 引述脚本的失败行 | 下一轮会把引述再解析一次 ⇒ 你描述失败就制造失败 | §7.5 只描述"必须报干净"，不抄任何失败行的原文格式 |
| 抄 `grep -c` 的原生输出 | `文件名:数字` 会被行号检查器当成越界行号 | 全篇统计一律写成 `文件 → 数字` 或表格两列 |
| 举例说明"失效引用长什么样" | 举的例子本身就是一条失效引用 | §8.2 用文字描述，不写出可解析的编号模式 |

### §8.4 `check_line_refs.py` 的两个行为

- **会解析代码块和 ASCII 图里的 `路径:数字`**。本套 06 §9 的 DTS 片段里
  有 `arch/arm64/boot/dts/ti/k3-am65.dtsi:NNN` 形式的引用，都是真实行号，所以没触发问题
- **AMBIGUOUS**（同名头文件）本轮**没有出现**：of 的头文件名
  （`of.h`、`of_platform.h` 等）在内核树里都是唯一的。
  ⇒ 这是 of 的运气，不是通例——i2c 的 `i2c.h` 有 12 个同名文件

### §8.5 环境坑速查

| 坑 | 现象 | 处置 |
|---|---|---|
| 单次写入过大 | 一次写 8k+ 输出 token 会撞网关超时，整轮作废 | 每篇拆 2–3 次写入 |
| 上下文撞 1M 硬上限 | 会话**永久**报 400，任何输入都在十几秒内失败，resume 也修不回来 | 跑到 ~700k 就主动新开会话，靠 `output/PROGRESS.md` 台账续跑（本套 99 就是这么接上的） |
| 源码树只读 | 只有 `output/` 可写 | 所有产出进 `output/of/` |

---

## §9 rule-29 对照：本套没有对照集

```bash
ls output/qoder/of
# → No such file or directory
```

**`output/qoder/` 下没有 of**，所以本套**没有做**独立产出的双套对照
（pinctrl / gpio / clk / dma 四套做了）。

⇒ **这意味着什么**：rule-29 的核心价值是"两套独立产出写了同一句错话 ⇒
源码在这里本身有歧义"。本套缺这个信号，**只能靠自审**。
§6.1 那条 `-ENOENT` 的错误恰好是 rule-29 最擅长抓的类型
（一个直觉性误解，独立产出很可能同样写错），本轮是靠回源码读函数体抓到的，
**不是靠对照**。⇒ 没有对照集的子系统，"回源码逐字读"这一步不能省。

---

## §10 这套文档怎么用

| 你的处境 | 先读 |
|---|---|
| 刚接触设备树，完全不懂 | 08 篇（费曼解释）→ 00 §2（背景）→ 06 篇（DTS 怎么写） |
| 要写一个消费 DT 的驱动 | 05 §1 → 01 §4（属性读取）→ **01 §14.2.1（错误码那个坑）** |
| 要写一个被别人 phandle 引用的提供者 | 05 §3 → 06 §5 |
| DTS 改了但设备没出来 | 07 §6 → 06 §7（填充白名单）→ 06 §10 |
| 寄存器地址读出来不对 | 06 §2 / §3 → 03 §B4 → 08 §3 §4 |
| 要改 of 核心本身 | 02 → 03（全篇）→ 04 → **本篇 §5**（先知道哪些地方没验证过） |
| 隔几个月回来复习 | 只读 00 篇 |

**本篇最该记住的一条**：of 的属性读取家族**不返回 `-ENOENT`**，
"属性不存在"是 `-EINVAL`（01 §14.2.1）。这是本套唯一一条照抄就会出 bug 的地方，
也是本轮自审改动最大的一处（§6.1）。
