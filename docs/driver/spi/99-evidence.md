---
title: 取证附录
---

# SPI 文档集取证附录

> 这一篇不讲 SPI，讲**前面九篇的可信度**。
> 读者用它来决定：哪些结论可以直接拿去用，哪些要自己再确认一遍。
>
> 本篇的失效方式是**过期**而不是写错。每一个数字都配了可复现的命令，
> 换一个内核版本重跑一遍就知道还成不成立。

---

## §1 基线与判定

### §1.1 代码基线

```bash
git log -1 --format='%H %s'
# f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158

cat localversion-rt
# -rt58

grep -E "^(VERSION|PATCHLEVEL|SUBLEVEL|EXTRAVERSION)" Makefile | head -4
# VERSION = 6 / PATCHLEVEL = 1 / SUBLEVEL = 158 / EXTRAVERSION =
```

**这棵树 = Linux 6.1.158 + PREEMPT_RT 补丁（rt58）**，工作树不干净
（几百个文件被 RT 补丁改过）。所以**第一件必须查清的事是：RT 补丁动了 SPI 吗？**

### §1.2 RT 补丁对 SPI 的影响：**2 行，且不在传输路径上**

```bash
git status --porcelain -- drivers/spi include/linux/spi \
    include/trace/events/spi.h Documentation/spi \
    Documentation/devicetree/bindings/spi
#  M drivers/spi/spi.c            ← 只有这一个文件

git diff --stat -- drivers/spi
#  drivers/spi/spi.c | 4 ++--
#  1 file changed, 2 insertions(+), 2 deletions(-)
```

改动全文（`git diff -- drivers/spi/spi.c`）：

```diff
@@ -127,10 +127,10 @@
-			start = u64_stats_fetch_begin_irq(
+			start = u64_stats_fetch_begin(
 					&pcpu_stats->syncp);
 			inc = u64_stats_read(&pcpu_stats->field);
-		} while (u64_stats_fetch_retry_irq(
+		} while (u64_stats_fetch_retry(
 					&pcpu_stats->syncp, start));
```

| 结论 | 依据 |
|-----|-----|
| 改动位置 = `spi_pcpu_stats_totalize()` 宏内部 | `drivers/spi/spi.c:120-137` |
| 这个宏**只被 sysfs 统计的 `show` 处理函数用** | `SPI_STATISTICS_ATTRS()` `:139` 起 |
| **传输路径、注册路径、片选路径全部是原版上游代码** | 上面的 `git diff` 输出只有这两处 |
| `include/linux/spi/*`、`include/trace/events/spi.h`、`Documentation/spi/`、SPI 的 DT 绑定 **一个字都没改** | `git status --porcelain` 无输出 |

**所以本文档集里除了 `07-debug.md` §4 的统计读取路径，其余全部等价于
纯净的 6.1.158 上游代码。** 这一条是整份文档定性的前提——如果 RT 补丁
改了传输路径，很多结论就只对 RT 树成立了。

### §1.3 子系统类型判定：**硬件总线型**

```bash
grep -n "struct bus_type" drivers/spi/spi.c
# 471:struct bus_type spi_bus_type = {
```

有自己的 `bus_type` → **硬件总线型**，10 篇模板全套适用
（对照：clk / pinctrl / regmap 是框架型，没有 `bus_type`）。

**但有一个反直觉的细节，值得记在判定结果旁边**：`spi_controller`
**本身不在这条总线上**——它只有 `dev.class`（`:2926`/`:2928`），
`dev.bus` 从不赋值。只有 `spi_device` 上总线（`:598`）。
这和 I2C 的做法不同（`i2c-core-base.c:1554` 把 adapter 也放在 i2c 总线上）。
详见 `08-feynman.md` 主题 1。

### §1.4 规模

```bash
wc -l drivers/spi/spi.c include/linux/spi/spi.h drivers/spi/spi-mem.c \
      drivers/spi/spidev.c drivers/spi/spi-loopback-test.c \
      drivers/spi/spi-bitbang.c drivers/spi/spi-mux.c \
      include/linux/spi/spi-mem.h include/trace/events/spi.h
```

| 文件 | 行数 | 读法 |
|-----|-----|-----|
| `drivers/spi/spi.c` | **4632** | **一等：全文逐行读** |
| `include/linux/spi/spi.h` | **1614** | **一等：全文逐行读** |
| `include/trace/events/spi.h` | **239** | **一等：全文逐行读** |
| `drivers/spi/spi-mem.c` | 919 | 一等（`04` 需要） |
| `drivers/spi/spidev.c` | 897 | 一等 |
| `drivers/spi/spi-loopback-test.c` | 1111 | 二等（读了参数和用例表，未逐行读断言逻辑） |
| `drivers/spi/spi-bitbang.c` | 453 | 二等 |
| `drivers/spi/spi-mux.c` | 203 | 一等（很短） |
| `include/linux/spi/spi-mem.h` | 404 | 一等 |
| **`drivers/spi/` 全部 `.c` + `.h`** | **105974**（137 个 `.c`） | **只读符号表和针对性 grep** |

```bash
cat drivers/spi/*.c drivers/spi/*.h | wc -l    # 105974
ls drivers/spi/*.c | wc -l                     # 137
```

**10.6 万行 vs 4632 行**——这个比例决定了全部方法：**核心全读，
137 个控制器驱动只讲契约**。凡是关于控制器驱动的论断，本文档集只用
两种形式：(1) 全树 grep 的计数，(2) 点名到 `文件:行号` 的具体实例。
**没有"控制器驱动一般会……"这种没有依据的概括。**

### §1.5 代表驱动选法

| 角色 | 选了谁 | 为什么 |
|-----|-------|-------|
| 最简实现 | `spi-gpio.c` + `spi-bitbang.c` | 纯软件位摇，没有硬件细节干扰，能看清核心的契约边界 |
| 全功能实现 | `spi-rockchip.c` | arm64、DMA、`num-cs`、`spi-slave` 都有；本树 arm64 DTS 大量在用 |
| arm64 实际在用 | `rk3399.dtsi` / `rk3566-*` / `k3-am642-evm.dts` / `imx8mm-mx8menlo.dts` | `06-devicetree.md` §2、§5 的例子全部取自这里 |
| SPI-NOR 加速路径 | `spi-rockchip-sfc.c`（`&sfc` 节点） | `06` §5.2 的 QSPI 陷阱需要一个真实的 `mem_ops` 控制器 |

**没有选的**：ACPI 相关驱动、x86-only 驱动（`spi-intel.c` 等）、
PCI 附着的控制器。理由见 §2.7。

### §1.6 读过的 `Documentation/`

```bash
wc -l Documentation/spi/*.rst          # 共 1263 行，7 个文件
wc -l Documentation/driver-api/spi.rst # 53
ls Documentation/devicetree/bindings/spi/ | wc -l    # 91
```

| 文件 | 行数 | 读法 | 纳入了哪一篇 |
|-----|-----|-----|------------|
| `Documentation/spi/spi-summary.rst` | 639 | **全文读** | `00`（概念模型、硬件背景）、`02`（结构关系）、`05`（角色划分） |
| `Documentation/spi/spidev.rst` | 191 | **全文读** | `04` §5（字符设备）、`07` §7.1 |
| `Documentation/spi/index.rst` | 22 | 全文读 | 用于确认没有漏掉子文档 |
| `Documentation/driver-api/spi.rst` | 53 | **全文读** | `00` §2 开篇（Controller/Protocol Driver 的官方命名）、`00` §3 出处标注；它的 `:17-20` 已过时，见 §1.7 第 5 条 |
| `Documentation/spi/pxa2xx.rst` | 214 | **未读** | 不涉及 arm64，见 §2.7 |
| `Documentation/spi/butterfly.rst` | 74 | 未读 | 并口 bit-bang 适配器，与 arm64 无关 |
| `Documentation/spi/spi-lm70llp.rst` | 84 | 未读 | 同上 |
| `Documentation/spi/spi-sc18is602.rst` | 39 | 未读 | 单个 I2C-to-SPI 桥驱动 |
| `Documentation/devicetree/bindings/spi/spi-controller.yaml` | — | **全文读** | `06` §2、§4.3（发现两处与代码冲突） |
| `Documentation/devicetree/bindings/spi/spi-peripheral-props.yaml` | — | **全文读** | `06` §1.1、§1.3（发现 3 个死属性） |
| 其余 89 个绑定文件 | — | 只按需 grep | 各驱动私有属性 |

### §1.7 官方文档与代码冲突清单（5 条）

**规则：文档与代码冲突时以代码为准，并在正文里显式指出。** 本次发现 5 条：

| # | 官方文档说 | 代码实际 | 依据 | 写在哪 |
|---|----------|---------|-----|-------|
| 1 | `spi-controller.yaml:63-70` Note 1/2："Should print a warning about polarity inversion" | **这个警告不存在**。`grep -rni "polarity" drivers/spi/*.c \| grep -i "warn\|dev_err\|dev_info"` 为空 | `06` §4.3 | `06` §4.3、`08` 主题 4 |
| 2 | `spi-controller.yaml:54-61` 的极性真值表：`cs-gpios` 用 ACTIVE_HIGH 而无 `spi-cs-high` 时应为低有效 | **代码给的是高有效**。`spi.c:1015` 传的是未翻转的 `activate` | `06` §4.2/§4.3 | `06` §4.3、`08` 主题 4 |
| 3 | `drivers/spi/Kconfig:28-33`：`CONFIG_SPI_DEBUG` 打开 "sysfs, and debugfs support" | **全假**。唯一用处是 `Makefile:6` 的 `-DDEBUG`；`spi.c` 零 debugfs 调用；sysfs 统计无条件编译 | `07` §1.2 | `07` §1.2 |
| 4 | `include/trace/events/spi.h:236` 的 `#endif /* _TRACE_POWER_H */` | 头保护宏其实是 `_TRACE_SPI_H`（`:5-6`），注释是从 `power.h` 抄来没改 | `07` §3.8 | `07` §3.8 |
| **5** | `Documentation/driver-api/spi.rst:17-20`："At this time, only \"master\" side interfaces are supported… (Interfaces to support implementing SPI slaves would necessarily look different.)" | **6.1 里 slave 侧早就实现了**：`spi_slave_class`（`spi.c:2867`）、`spi_controller_is_slave()`、`spi_target_abort()`（`:2784`）、`"spi-slave"` 属性被 8 个驱动读、`drivers/spi/spi-slave-time.c` 与 `spi-slave-system-control.c` 两个 in-tree slave 协议驱动 | `00` §2、`04`（slave 框架） | `00` §2 结尾一句 + 本表 |

第 1、2 条是**同一处绑定文档的同一个表**，而且**冲突的那两行正好就是
绑定自己标了 Note "应该警告"的两行**——也就是说绑定作者知道这两种组合
有歧义，要求实现给警告，而实现既没给警告，行为也和表格不一致。
**这是本文档集发现的最有价值的一条。**

第 5 条不是我读代码读出来的，是 **`check_doc_coverage.sh` 反查出来的**：
它报告 `Documentation/driver-api/spi.rst` 在 10 篇文档里 **0 引用**，我去读那 53 行，
才同时发现了两件事——(a) 这条已过时的 slave 断言；(b) `00` §2 原来写的
"内核里能找到的唯一权威描述是 `spi-summary.rst`"是**错的**，官方还有第二处。
详见 §2.3。

---

## §2 证据等级

### §2.1 一等证据：逐行读过的文件

**判据：从第一行读到最后一行，不是"读了相关函数"。**

| 文件 | 行数 |
|-----|-----|
| `drivers/spi/spi.c` | 4632 |
| `include/linux/spi/spi.h` | 1614 |
| `include/trace/events/spi.h` | 239 |
| `include/linux/spi/spi-mem.h` | 404 |
| `drivers/spi/spi-mem.c` | 919 |
| `drivers/spi/spidev.c` | 897 |
| `drivers/spi/spi-mux.c` | 203 |
| `Documentation/spi/spi-summary.rst` | 639 |
| `Documentation/spi/spidev.rst` | 191 |
| `Documentation/driver-api/spi.rst` | 53（`:46-53` 是 kernel-doc 指令，正文只有前 45 行） |
| `Documentation/devicetree/bindings/spi/spi-controller.yaml` | 全文 |
| `Documentation/devicetree/bindings/spi/spi-peripheral-props.yaml` | 全文 |

**一等证据的含义**：这些文件里的行号引用可以直接信任，
函数存在性、返回值、日志有无都是读出来的，不是推断的。

### §2.2 二等证据：只详读了部分行的文件

**判据：必须写明"详读了哪几行"，超出这个范围的引用要降级。**

| 文件 | 详读范围 | 用在哪 |
|-----|---------|-------|
| `drivers/spi/spi-loopback-test.c` | `:20-75`（全部 9 个模块参数 + `MODULE_PARM_DESC`）、`:375-382`（`module_param_string`）、用例表的表头 | `04` §6、`07` §7.3 |
| `drivers/spi/spi-bitbang.c` | 符号表 + `spi_bitbang_setup()` / `spi_bitbang_transfer_one()` 的签名 | `04`（bitbang 框架）、`05` |
| `drivers/spi/spi-rockchip.c` | `:760`（`spi-slave`）、`:825`（`rx-sample-delay-ns`）、`:867`（`num-cs`） | `06` §1.2/§1.3 |
| `drivers/spi/spi-mxs.c` | `:378`、`:425`（重新发射 tracepoint） | `07` §3.7 |
| `drivers/spi/spi-mem.c` | `:328` 一带（`spi_mem_exec_op()` 的 GPIO CS 降级判断） | `06` §5.2 |
| `drivers/i2c/i2c-core-base.c` | `:1554-1555`（adapter 的 bus/type 赋值） | `08` 主题 1 的对照 |
| `drivers/input/rmi4/rmi_spi.c` | `:338`、`:344`（`spi-rx/tx-delay-us` 的唯一消费者） | `06` §1.3 |
| 其余 130 个控制器驱动 | **只做针对性 grep，没读过函数体** | 只用于计数和点名 |

**二等证据的风险**：`spi-bitbang.c`、`spi-loopback-test.c` 里凡是涉及
**行为**（而不是签名和参数）的论断，都可能不准。已在 §5.2 单列。

### §2.3 三等证据：`Documentation/`

见 §1.6 的读过清单和 §1.7 的冲突清单。

**三等证据的用法**：只用于**设计意图**（为什么这样分层）和
**已知陷阱**，从不用于确认代码行为。本次 5 条冲突证明了这个纪律的必要性——
**如果按文档写，`06` §4 的极性表会是错的。**

**但三等证据还有一个反向用途：查自己漏了什么。**
`check_doc_coverage.sh` 做的就是这件事——把 `Documentation/spi/`、
`Documentation/driver-api/spi*`、`Documentation/devicetree/bindings/spi/`
列出来，反查每个文件在 10 篇文档里被引用了几次。本轮它报出
`Documentation/driver-api/spi.rst` = **0 引用**，而我在写 `00`~`08` 的
整个过程里**根本不知道这个文件存在**——`Documentation/spi/index.rst`
不收录它。这是"你不知道你不知道"那一类缺陷，靠自审 100 遍也发现不了，
只能靠反向覆盖率。代价：`00` §2 一句"唯一权威描述"的假唯一性断言 +
§1.7 第 5 条一条过时官方说法，两者都是读了这 53 行才浮出来的。

**结论：反向覆盖率检查必须在写 `99` 之前跑，不能留到最后。**
它不是格式检查，是取证范围检查。

### §2.4 可复现的计数（全部重跑于本轮定稿时）

```bash
cd /workspace/safety/linux-6.1.y

# 日志宏普查（07 §2.1）
for m in dev_err dev_err_once dev_warn dev_warn_once dev_info dev_dbg \
         pr_debug WARN_ON WARN_ON_ONCE BUG_ON dev_err_ratelimited; do
  printf "%-22s %s\n" "$m" "$(grep -c "\b$m(" drivers/spi/spi.c)"
done
# dev_err 42 / dev_err_once 1 / dev_warn 6 / dev_warn_once 1 / dev_info 2
# dev_dbg 4 / pr_debug 0 / WARN_ON 6 / WARN_ON_ONCE 1 / BUG_ON 0
# dev_err_ratelimited 0

# 错误码普查（07 §5.1）
for e in EINVAL ENOMEM ENODEV EBUSY EPROBE_DEFER ENOTSUPP ESHUTDOWN \
         ETIMEDOUT EINTR ENOENT EIO EOPNOTSUPP EAGAIN; do
  printf "%-14s %s\n" "-$e" "$(grep -c -- "-$e" drivers/spi/spi.c)"
done
# EINVAL 37 / ENOMEM 16 / ENODEV 8 / EBUSY 6 / EPROBE_DEFER 5
# ENOTSUPP 3 / ESHUTDOWN 3 / ETIMEDOUT 1 / EINTR 1 / ENOENT 1
# EIO 0 / EOPNOTSUPP 0 / EAGAIN 0

# __spi_validate() 零日志（07 §5.2 的核心论断）
sed -n '3845,3995p' drivers/spi/spi.c | grep -c "dev_err\|dev_warn\|dev_dbg"
# 0

# 控制器驱动的 dev_dbg 总量（07 §1.3）
grep -c "dev_dbg" drivers/spi/*.c | grep -v ":0$" | wc -l          # 64 个文件
grep -h "dev_dbg" drivers/spi/*.c | wc -l                          # 328 处

# tracepoint（07 §3.1）
grep -c "TP_CONDITION" include/trace/events/spi.h                  # 0
grep -n "effective_speed_hz" include/trace/events/spi.h            # 空

# CONFIG_SPI_DEBUG 唯一用处（07 §1.2）
grep -rn "CONFIG_SPI_DEBUG" --include=*.c --include=*.h --include=Makefile .
# 只有 drivers/spi/Makefile:6

# 故障注入（07 §8.1）
grep -rn "fault_inject\|should_fail\|DECLARE_FAULT_ATTR" drivers/spi/
# 空

# DT 属性消费者（06 §1.2）
grep -rn '"num-cs"'    drivers/spi/ | wc -l                        # 21
grep -rn '"spi-slave"' drivers/spi/ | wc -l                        # 8
grep -rn '"spi-rx-delay-us"\|"spi-tx-delay-us"' drivers/ --include=*.c
# 只有 drivers/input/rmi4/rmi_spi.c:338 和 :344

# arm64 DTS（06 §5）
grep -rl "spi-max-frequency" arch/arm64/boot/dts/ | wc -l          # 174
grep -rn "spi-cs-high" arch/arm64/boot/dts/
# 只有 arch/arm64/boot/dts/ti/k3-am642-evm.dts:432

# spi-mem 控制器（04 §2）
grep -rn "\.mem_ops[[:space:]]*=\|->mem_ops[[:space:]]*=" drivers/spi/*.c \
  | cut -d: -f1 | sort -u | wc -l                                  # 24

# must_async（03 §B、08 主题 3）
grep -rn "must_async" drivers/ include/linux/spi/spi.h
# 只有 spi.c:4204（读）、spi-mux.c:165（写）、spi.h:496/:728（声明）

# 导出 tracepoint 的再发射者（07 §3.7）
grep -rn "trace_spi_transfer_start" drivers/ | grep -v drivers/spi/spi.c
# 只有 drivers/spi/spi-mxs.c
```

**只要上面有一个数字对不上，整篇从"取证"降级成"回忆"。**

### §2.5 三个"全称断言"的验证方式

全称断言（"全树唯一"/"一个都没有"）是最容易翻车的一类。本文档集里的全称断言
及其验证命令：

| 断言 | 出处 | 验证命令 | 结果 |
|-----|-----|---------|-----|
| `spi-cs-high` 在 arm64 DTS 里**只出现一次** | `06` §5.5 | `grep -rn "spi-cs-high" arch/arm64/boot/dts/` | 1 处，`k3-am642-evm.dts:432` ✓ |
| 极性反转警告**不存在** | `06` §4.3、`08` 主题 4 | `grep -rni "polarity" drivers/spi/*.c \| grep -i "warn\|dev_err\|dev_info"` | 空 ✓ |
| `CONFIG_SPI_DEBUG` **只有一处**使用 | `07` §1.2 | 见 §2.4 | 1 处 ✓ |
| **没有一个** tracepoint 带 `TP_CONDITION` | `07` §3.1 | `grep -c "TP_CONDITION" include/trace/events/spi.h` | 0 ✓ |
| **只有 `spi-mxs.c`** 再发射传输 tracepoint | `07` §3.7 | 见 §2.4 | ✓ |
| `target_abort` **零实现者** | `04` §4、`07` §5.6 | `grep -rn "target_abort" drivers/spi/*.c \| grep -v "spi\.c"` | 空 ✓ |
| `must_async` **只在一处**被设 true | `08` 主题 3 | 见 §2.4 | `spi-mux.c:165` ✓ |
| `spi-cs-{setup,hold,inactive}-delay-ns` 这三个名字**全树不存在** | `06` §1.3 | `grep -rn "spi-cs-setup-delay-ns\|spi-cs-hold-delay-ns\|spi-cs-inactive-delay-ns" .` | 空 ✓ |
| `spi.c` 里**零 debugfs 调用** | `07` §1.2 | `grep -c "debugfs" drivers/spi/spi.c` | 0 ✓ |
| SPI 核心**没有**故障注入 | `07` §8.1 | 见 §2.4 | 空 ✓ |
| 全树**只有 2 个** `spi_mem_driver` | `04` §3 | `grep -rn "struct spi_mem_driver " --include=*.c . \| grep -v spi-mem.c` | 2 个：`drivers/mtd/nand/spi/core.c:1393`（`spinand_drv`）、`drivers/mtd/spi-nor/core.c:3277`（`spi_nor_driver`）✓ |
| **13 个**驱动用 `spi_bitbang` | `04`（bitbang 框架） | `grep -rln "struct spi_bitbang" --include=*.c . \| grep -v drivers/spi/spi-bitbang.c` | 13 个 ✓（`ath79 / au1550 / butterfly / davinci / gpio / lm70llp / oc-tiny / omap-uwire / ppc4xx / s3c24xx / sh-sci / xilinx / xtensa-xtfpga`） |
| **只有 3 个**驱动实现 `set_cs_timing` | `01`、`03` Part B | `grep -rn "\bset_cs_timing\s*=" --include=*.c . \| grep -v drivers/spi/spi.c` | 3 个：`spi-mt65xx.c:1137`、`atmel-quadspi.c:642`、`spi-tegra114.c:1323` ✓ |
| **只有 10 个**驱动实现 `max_transfer_size` | `01`、`03` Part B | 同上换字段名 | `drivers/spi/` 内 10 个 ✓（`omap2-mcspi / rockchip / s3c64xx / amd / fsi / sc18is602 / sun6i / bcm63xx / pxa2xx / sun4i`）树里另有 8 个文件写同名字段，全是 `usb/dwc2`、`scsi/smartpqi`、`dma/amba-pl08x` 等**无关子系统的同名字段**，见下面 |
| **只有 11 个**驱动实现 `handle_err` | `01`、`03` Part B、`07` | 同上换字段名 | `drivers/spi/` 内 11 个 ✓（`geni-qcom / bcm2835aux / rockchip / img-spfi / uniphier / hisi-kunpeng / dw-core / lantiq-ssc / pxa2xx / qcom-qspi / bcm2835`）树外 2 处是 `drivers/misc/cxl/guest.c` 的**布尔字段**，无关 |

**注意 `grep -v drivers/spi/spi.c` 这个动作**：框架的使用者不一定住在
框架自己的目录下，但**框架自己的定义一定在**。不排除自己就会把定义
当成使用者，得出"有 N 个实现者"的错误结论——这个坑本文档集踩过一次，见 §4.2。

**验证这 5 条时又踩到两个新坑，都值得记下来：**

**坑 1：`.field =` 这种"指定初始化器"写法在 SPI 驱动里几乎不用。**
第一遍我跑的是 `grep -rn "\.set_cs_timing\s*="`，三个字段全部得到 **0 个文件**，
差点就写出"零实现者"。原因是 SPI 控制器驱动统一在 probe 里
**逐字段赋值**（`master->handle_err = ...`），不是用结构体初始化器。
判定回调实现者数量必须用 `\bfield\s*=`（带词边界，不带点），
否则会系统性地得到 0。

**坑 2：有 4 个 SPI 控制器驱动不住在 `drivers/spi/`。**

```bash
grep -rln "spi_alloc_master\|devm_spi_alloc_master" --include=*.c . | grep -v "^./drivers/spi/"
```

| 文件 | 行数 | 注册方式 |
|-----|-----|---------|
| `drivers/staging/greybus/spilib.c` | 570 | `spi_alloc_master():494` + `spi_register_master():534`，`transfer_one_message:520` |
| `drivers/media/usb/msi2500/msi2500.c` | 1309 | `spi_alloc_master():1223` + `spi_register_master():1234`，`transfer_one_message:1232` |
| `drivers/media/pci/netup_unidvb/netup_unidvb_spi.c` | 240 | `devm_spi_alloc_master():178` + `spi_register_master():198`，`transfer_one_message:189` |
| `drivers/video/fbdev/mmp/hw/mmp_spi.c` | 167 | `spi_alloc_master():143` + `spi_register_master():157` |

所以 §1.4 里"`drivers/spi/` 有 137 个 `.c`"**不等于**"全树有 137 个 SPI 控制器驱动"，
真实数字要再加这 4 个。**凡是"只有 N 个驱动……"的断言，
grep 范围必须是全树而不是 `drivers/spi/`。**
上面那三条回调断言我按全树重跑过，结论没变（那 4 个都不实现这三个回调），
但结论没变是运气，不是方法。

顺带一个真实收获：**这 4 个树外控制器里有 3 个用 `transfer_one_message`**
（greybus `:520`、msi2500 `:1232`、netup `:189`），也就是绕开了核心的
`transfer_one` 拆分逻辑；第 4 个 `drivers/video/fbdev/mmp/hw/mmp_spi.c:155`
更旧，直接覆盖了 `ctlr->transfer`
（对照 `03` Part A 的两级回调）。它们是"最低层级接管"的现成例子。

### §2.5.1 回调实现数的全量重数（本轮最大一批修正）

**这 5 条断言查完之后我顺手把整张回调表重数了一遍，结果 `01` §1.3 的
数字有 9 处是错的。** 原因是那张表的数字来自初稿时的**全树无边界 grep**，
从来没跟 `02` §2.1 的同一张表对过账——而 `02` 的数字大体是对的。
**这就是方法论第 17 条"跨文档一致性必须单独走一遍"的实例：
两张表写的是同一件事，数字却系统性地不一样，谁都没发现。**

口径：`drivers/spi/` 内**赋值点所在的文件数**，排除核心 `drivers/spi/spi.c`：

```bash
for f in setup cleanup set_cs set_cs_timing transfer transfer_one \
         transfer_one_message prepare_message unprepare_message \
         prepare_transfer_hardware unprepare_transfer_hardware can_dma \
         handle_err max_transfer_size max_message_size slave_abort \
         target_abort fw_translate_cs mem_ops mem_caps; do
  n=$(grep -rlE "(->|\.)$f[[:space:]]*=[^=]?" --include=*.c drivers/spi/ \
      | grep -v "^drivers/spi/spi.c$" | wc -l)
  printf "%-30s %s\n" "$f" "$n"
done
```

| 回调 | 定稿值 | `01` 初稿写的 | `02` 初稿写的 |
|-----|-------|-------------|-------------|
| `setup` | **62** | "全树 33 个 `set_cs`/setup 类实现"（口径都是错的） | `—`（欠债记号） |
| `cleanup` | **26** | "与 `setup` 配对"（无数字） | `—`（欠债记号） |
| `set_cs` | **30** | 33 ✗ | 33 ✗ |
| `set_cs_timing` | **3** | 3 ✓ | 3 ✓ |
| `transfer` | **2** | 2 ✓ | 5 ✗ |
| `transfer_one` | **55** | 59 ✗ | 59 ✗ |
| `transfer_one_message` | **29** | 29 ✓ | 29 ✓ |
| `prepare_message` / `unprepare_message` | **28 / 11** | 43 / 11 ✗ | 36 / 11 ✗ |
| `prepare_transfer_hardware` / `unprepare_` | **10 / 13** | 11 / 14 ✗ | 10 / 13 ✓ |
| `can_dma` | **24** | "39 个（`drivers/spi/` 内 25 个）" ✗✗ | 25 ✗ |
| `handle_err` | **11** | 13 ✗ | 11 ✓ |
| `max_transfer_size` / `max_message_size` | **10 / 5** | 24 / 7 ✗✗ | 10 / 5 ✓ |
| `slave_abort` / `target_abort` | **9 / 0** | 11 / 0 ✗ | 11 / 0 ✗ |
| `fw_translate_cs` | **1** | 1 ✓ | 1 ✓ |
| `mem_ops` / `mem_caps` | **24 / 3** | 27 / 3 ✗ | 27 / 3 ✗ |

同步改动的下游引用（方法论第 17 条）：
`00` §2.8（`handle_err` 13→11）、`03` §B3.1（`can_dma` 25→24）、
`03` §A10（`transfer_one` 59→55）、`03` §C3 与 `04` §4（`slave_abort` 11→9）、
`05` §0 分诊表（59→55、25→24）。

**三个坑，都是"数得出数字"却数错的典型：**

**坑 3：文件数 ≠ 赋值点数。** `mem_ops` 是 **24 个文件 / 27 处赋值**
（同一个驱动会给多个 `spi_controller` 实例赋值）；`transfer_one` 是
**55 个文件 / 59 处赋值**。初稿的 27 和 59 都是**行数**被当成了驱动数。
说"N 个驱动实现了 X"就必须用 `grep -l` 数文件，`grep -c` 数的是别的东西。

**坑 4：同名的私有结构体字段。** `atmel-quadspi.c:626` 有一行
`.transfer = atmel_qspi_transfer,`，看着像实现了 `ctlr->transfer`，
其实它在 `struct atmel_qspi_ops`（`:624`）里，是**驱动自己的**回调表。
这就是为什么 `transfer` 的正确答案是 2 而不是 3——**光看 grep 的行不够，
得把那一行的上文打开看它在哪个结构体里。**

**坑 5：核心自己也赋值。** `grep -v spi.c` 必须做，否则
`drivers/spi/spi.c:2196` 的 `ctlr->transfer = spi_queued_transfer;`
（`spi_controller_initialize_queue()` 里设的默认值）会被当成一个"实现者"，
`transfer` 就会数成 3。这一行同时也是 `01` §1.3 那句
"核心默认把它设成 `spi_queued_transfer`"的出处。

### §2.5.2 "旧名字都是别名"——一句话概括错了四篇

**起因**：跑 `05` 的符号存在性普查（skill 第 20 条）时，把正文里 127 个
标识符逐个全树 `grep`。52 个空结果全是本篇自造的示例名（`my*` / `MY*` 前缀），
预期之内。**但顺着 `devm_spi_alloc_master` 往下查"它和 `devm_spi_alloc_host`
是什么关系"，翻出了一条贯穿 4 篇的错误概括。**

初稿（`00` §7 命名表、`01` §1.1、`02` §5 命名表、`05`）都写着一句话：
**"`master` 是 `controller` 的别名"**。这句话把**四种**完全不同的实现形式
压成了一种，而四者的 `grep` 行为、调试器表现、"改一个会不会影响另一个"都不同：

| 形式 | 位置 | 机制 | 例子 | 会分叉？ |
|-----|-----|-----|-----|--------|
| **(1) `#define` 兼容层** | `spi.h:1592-1612` | 旧名**展开成**新名，编译后不存在 | `spi_master`→`spi_controller` `:1593`；`spi_master_get()` `:1604`；`spi_register_master()` `:1609` | 不会 |
| **(2) 并列的独立 `static inline` / 独立导出函数** | `spi.h:786-848`；`spi.c:2777`/`2788` | **两个名字互不引用**，各自去调同一个底层函数 | `spi_alloc_master` `:786` 与 `spi_alloc_host` `:801`，都调 `__spi_alloc_controller(dev,size,false)`；`spi_slave_abort` 与 `spi_target_abort` 各自 `EXPORT_SYMBOL_GPL`（`spi.c:2786`/`2797`） | **会** |
| **(3) 真实的重复字段** | `spi.h:176-179` | 结构体里**存了两份指针**，核心一行同时赋值 | `spi_device.master` 与 `.controller`，`spi.c:596` | 会 |
| **(4) `union` 同址双名** | `spi.h:561-566`、`:679-682` | **一份存储、两个名字**，物理上不可能不同步 | `ctlr->slave`/`->target`；`ctlr->slave_abort`/`->target_abort` | 不会 |

复现命令：

```bash
# 形式 1：兼容宏层的确切边界
sed -n '1592,1612p' include/linux/spi/spi.h

# 形式 2：八个 alloc 包装，两两成对，无一是 #define
grep -n "inline struct spi_controller \*\(devm_\)\?spi_alloc_\(master\|slave\|host\|target\)" \
     include/linux/spi/spi.h
# 786 792 801 807 820 826 835 841

# 形式 3
grep -n "spi->master = " drivers/spi/spi.c        # 596
grep -rlw --include=*.c "spi->master" drivers/ | wc -l   # 91 个文件 / 293 处

# 形式 4：union —— 这一种靠"搜名字"是搜不出来的，只有读结构体定义才看得见
grep -n "^	union {" include/linux/spi/spi.h        # 561 679
sed -n '561,566p;679,682p' include/linux/spi/spi.h
```

**形式 (4) 是补录的，补录的过程本身就是教训。**
前三种是顺着 `master`/`host` 这条线查出来的，当时已经声明"三种形式"并
写进了 4 篇。形式 (4) 是后来做 `05` §7「近邻符号区分」时，
为了确认 qoder 那句"`spi_target_abort` 是 `spi_slave_abort` 的新别名"
对不对，`sed` 打开 `spi.c:2770-2800` 顺手看见 `union` 才发现的。

教训：**"我已经把这个机制查清楚了"这个判断，本身要看当初是顺着哪条线查的。**
`master`→`host` 那条线上恰好没有 `union`，于是"形式清单"在那条线上是完备的、
在整个子系统上却不是。**跨改名线的机制普查要显式换一条线再走一遍**，
判据是"另一条改名线上有没有出现前面没见过的形式"。

**由此揪出一个幻觉符号。** `00` §7 那一行的例子写的是
`spi_alloc_master() = spi_alloc_controller(..., false)`——
**`spi_alloc_controller()` 在本树里根本不存在**：

```bash
grep -rn "\bspi_alloc_controller\b" include/ drivers/     # 空
```

真名是 `__spi_alloc_controller()`（两个下划线，`spi.c:2902`）。
这是本轮唯一一条 **ERROR 级幻觉符号**，而它**不在 `05` 里，在 `00` 里**。
**教训：skill 第 20 条的"符号存在性全树 grep"不能只对 `05` 做。**
`00` 的命名表、`02` 的命名表、`07` 的错误码表，凡是"举个例子"的单元格
都在凭记忆写名字，风险和 `05` 的骨架一样高。做法已回写进 skill。

**同时修掉的三个计数错**（`01` §1.1，口径都是 `drivers/` 下 `.c`）：

| 符号 | 初稿 | 定稿 | 说明 |
|-----|-----|-----|-----|
| `spi_alloc_master` | 80 处 | **81 个文件 / 84 处** | 文件数与行数没分开 |
| `spi_alloc_slave` | 8 处 | **7 个文件 / 8 处** | 同上 |
| `devm_spi_alloc_master` | 38 处 | **39 处** | — |
| `__spi_alloc_controller` | "直接调用的只有 4 处" | **驱动零直接调用** | 那 4 处（`spi.h:789`/`798`/`804`/`813`）是**包装函数自己**，不是调用者。`.c` 里只有 `spi.c` 的定义 + kerneldoc + `EXPORT_SYMBOL_GPL` |

最后一行是这一批里最有代表性的：**把"被引用的地方"当成了"调用者"**。
`static inline` 包装函数的函数体里那一次引用，在 `grep` 输出里和真正的
调用点长得一模一样。凡是要说"只有 N 个地方直接调它"，都得把那 N 行打开看
它是**在调用**还是**在被定义成包装**。

**顺带修正 `flags` 标志的命名描述（`02` §2.1、`03` §C3）。** 初稿说
"六个标志里 `SPI_MASTER_GPIO_SS` 是唯一没改名的"。实际是 **7 个标志位**，
分三种情况：

```bash
sed -n '549,556p' include/linux/spi/spi.h    # BIT(0)~BIT(6)，共 7 个
sed -n '1595,1599p' include/linux/spi/spi.h  # 只有 5 个旧名 #define
grep -rn "SPI_CONTROLLER_GPIO_SS" include/ drivers/   # 空
```

| 情况 | 标志 | 说明 |
|-----|-----|-----|
| 新旧双名 | `HALF_DUPLEX` / `NO_RX` / `NO_TX` / `MUST_RX` / `MUST_TX`（`:549-553`）| 旧名 `#define` 在 `:1595-1599` |
| 只有旧名 | `SPI_MASTER_GPIO_SS`（`:555`，BIT(5)）| 改名那轮漏了它，`SPI_CONTROLLER_GPIO_SS` **不存在** |
| 只有新名 | `SPI_CONTROLLER_SUSPENDED`（`:556`，BIT(6)）| 后加的标志，从来没有 `SPI_MASTER_` 版 |

初稿的"六个"是把 BIT(5) 和 BIT(6) 里的一个漏数了，**而且两头各有一个
反例，正好是"别照着邻居猜名字"的最好例子**——所以这三行留在正文里。

### §2.6 索引工具的使用边界

用了 codebase-memory-mcp（cbm，overlayfs 影子根 `/workspace/safety/kidx/merged`）。

| 用途 | 用了吗 |
|-----|-------|
| 找候选符号、看调用关系、快速定位文件 | ✓ 用了 |
| **作为文档里任何行号 / 计数 / 存在性断言的依据** | **一次都没有** |

**两个已知盲区，恰好是 SPI 最核心的机制**：

1. **宏生成的符号**：`SPI_STATISTICS_ATTRS()`（`:139`）一个宏生成
   11×2 个 `show` 函数，`SPI_STATISTICS_TRANSFER_BYTES_HISTO()`（`:193`）
   生成 17×2 个。索引里这些符号**不存在**。`07-debug.md` §4.3/§4.5 的
   30 项统计**全部靠读宏定义 + `sed` 数实例得出**。
2. **函数指针间接调用**：`ctlr->transfer_one()`、`ctlr->set_cs()`、
   `mem_ops->exec_op()` 这些是 SPI 分层的全部关节，索引给不出调用边。
   `03-internals.md` 的时间线**全靠人读**。

**所以本文档集所有行号都是 `rg` / `sed` 出来的。** cbm 只用来决定"去读哪个文件"。

### §2.7 arm64-only 裁掉了什么（显式登记，不是遗漏）

按项目约定，**架构相关内容只走 arm64**。下面这些是**有意不写**的：

| 裁掉的内容 | 在代码里的位置 | 为什么裁 |
|----------|-------------|---------|
| **ACPI 设备枚举** | `spi.c:2646`、`:2670`、`:2742`、`acpi_spi_*` 一整套 | arm64 服务器场景才用；本项目只关心 DT |
| **ACPI 的 GPIO 片选极性分支** | `spi.c:1012`（`has_acpi_companion()`） | **但这一条在 `06` §4.2 的四出口表里保留了**——因为不列出来，读者无法理解为什么 DT 分支要用 `activate` 而不是 `!enable` |
| x86-only 控制器驱动 | `spi-intel.c`、`spi-pxa2xx.c`、`spi-lpss` 系列 | 不在 arm64 上出现 |
| `Documentation/spi/pxa2xx.rst`（214 行） | — | 同上 |
| PCI 附着的 SPI 控制器 | `spi-dw-pci.c` 等 | arm64 嵌入式基本不用 |
| 并口 bit-bang 适配器 | `spi-butterfly.c`、`spi-lm70llp.c` | 历史遗留，x86 并口 |
| 大端字节序相关分支 | 各驱动 | arm64 默认小端 |

**一条"裁了但必须提一句"的判断标准**：如果不提，读者会**误解保留部分的
代码为什么这么写**。ACPI 极性分支就是这种情况——它和 DT 分支是紧挨着的
两行（`:1012` vs `:1015`），而两者用了不同的变量，这个对比本身就是
理解 `06` §4 的钥匙。

---

## §3 逐篇取证表

**每篇列出它最关键的几条论断、出处、以及自审时改了什么。**
「自审修正」一列是这张表的重点——**没有修正记录的篇，说明自审没做到位。**

### §3.1 `00-overview.md`

| 关键论断 | 出处 |
|---------|-----|
| SPI 是**单主多从、全双工、无应答**的同步串行总线 | `Documentation/spi/spi-summary.rst`（三等）+ `spi.c:3895-3906` 半双工分支反证全双工是默认 |
| 四种时钟模式由 `SPI_CPHA`/`SPI_CPOL` 两位组成 | `include/uapi/linux/spi/spi.h:10` 起（`SPI_MODE_0..3` 的宏定义在 **uapi** 头里，不在 `include/linux/spi/spi.h`） |
| 一次传输的最小单位是 `spi_message`，不是 `spi_transfer` | `spi.c:4258` `spi_sync()` 只接受 `spi_message` |
| 核心 4632 行、控制器驱动 10.6 万行 | §1.4 的 `wc -l` |
| 端到端路径 `spi_sync() → __spi_sync() → __spi_transfer_message_noqueue() → spi_transfer_one_message() → transfer_one()` | 逐行读 `:4258`→`:4178`→`:4134`→`:1538`（`transfer_one` 的调用点） |

**自审修正**：
1. 初稿把「硬件背景」写成了 200 行的协议科普，但没有一条连回代码。
   重排后每个协议要素都挂了代码锚点（模式位→`uapi/linux/spi/spi.h:10`、位宽→`:3937`、
   片选极性→`:1015`），**协议知识不给行号就是三等证据**。
2. §5 机制速览初稿漏了「统计」这一层，导致 00 无法作为 07 的复习入口。补上。
3. §7 子系统关系图初稿把 `spi_controller` 画在 SPI 总线上。按 §1.3 改正。

### §3.2 `01-api.md`

| 关键论断 | 出处 |
|---------|-----|
| 对外 API 集中在 `include/linux/spi/spi.h` 和 `spi-mem.h` 两个头文件 | 一等（两个头全读） |
| `spi_sync()` 会阻塞且**不可中断、无超时** | `:4244` 的注释 + `:4228` `wait_for_completion()` |
| `spi_async()` 可在原子上下文调用 | `:4060` 起的注释 + `spi_async()` 用 `spin_lock_irqsave` |
| `spi_write_then_read()` 有 `SPI_BUFSIZ` 限制 | `:4371` 起；`SPI_BUFSIZ` 定义在 `:4347`（`max(32, SMP_CACHE_BYTES)`），判断在 `:4388` |
| `devm_spi_alloc_master()` 是 `__devm_spi_alloc_controller()` 的包装 | `spi.h` 的 `static inline` |

**自审修正**：
1. **格式统一**：初稿有的 API 带参数类型有的不带；按项目约定统一为
   「只带形参名」，类型信息搬进「备注」列。
2. **逐 API 行号全部删掉**，换成每节开头一行「头文件：…；实现：…」——
   行号在 API 篇是噪声，集中在 03 和 99 维护。
3. 表格统一成 `API / 说明 / 备注` 三列。初稿有五种不同的表头。
4. §0 代码地图从 00 搬到这里（「找文件」和「找 API」是同一次查询）。

### §3.3 `02-datastructures.md`

| 关键论断 | 出处 |
|---------|-----|
| `spi_controller` **只有 class，没有 bus** | `:2926`（`dev.class = &spi_master_class`）、`:2928` |
| `spi_device` 在 SPI 总线上 | `:598`（`spi->dev.bus = &spi_bus_type`） |
| 两条总线的唯一缝合点是 `dev.parent` | `:597`（`spi->dev.parent = &ctlr->dev`） |
| `spi_controller` ↔ `spi_driver` **不是** device/driver 关系 | `spi_bus_type.match = spi_match_device`（`:474`）只匹配 `spi_device` |
| **唯一的全局锁**是 `board_lock`（`:557`）；其余五把都挂在 `spi_controller` 上——`add_lock`（`spi.h:579`）、`io_mutex`（`:576`）、`bus_lock_mutex`（`:583`）、`bus_lock_spinlock`（`:582`）、`queue_lock`（`:654`） | 逐个 grep 定义行 |
| 统计结构是 per-CPU 的 `struct spi_statistics` | `spi.h:63` 起 + `spi.c:120` 的 totalize 宏 |

**自审修正**：
1. §1.0「先分清 3 组关系」是自审后**新增**的整节。初稿直接开始画图，
   而最容易错的三组关系（controller↔driver、controller↔platform_device、
   `spi_master` 与 `spi_controller` 是不是两个东西）一个都没被显式否掉。
2. `spi_master` 其实是 `spi_controller` 的 `#define` 别名（`spi.h:1593`，同处还有
   `spi_master_get_devdata()` 等 6 个别名 `:1601-1607`），
   初稿当成两个结构体写了。**这是一条 ERROR 级修正。**
3. 补上「本子系统总线 ↔ platform 总线」关系图（必写项，初稿缺）。

### §3.4 `03-internals.md`

| 关键论断 | 出处 |
|---------|-----|
| 注册主流程 `spi_register_controller()` → `of_register_spi_devices()` → `spi_add_device()` → `__spi_add_device()` | `:3099` / `:2386`（调用点 `:3225`） / `:698` / `:637` |
| 匹配走 5 条纯 `strcmp` 路径 | `spi_match_device()` `:375` |
| 拆除路径 `spi_unregister_controller()` → `spi_destroy_queue()` | `:3297` / `:2133`（调用点 `:3313`；注册失败回滚的调用点在 `:3230`） |
| `__spi_pump_messages()` 是唯一的消息出队点 | `:1764` 起（kernel-doc 在 `:1752`）；`spi_pump_messages()` `:1853` 是它的 kthread 包装（`:1858` 传 `in_kthread=true`） |
| `spi_sync()` 的快路径**不经过 pump 线程** | `:4204` 的 `must_async` 分支 + `:4224-4228` |
| 五道锁的层次：`bus_lock_mutex` > `io_mutex` > `queue_lock` > `bus_lock_spinlock` | 逐个调用点读出，非推断 |
| 设计债：`spi_stop_queue()` 自陈 "This is a bit lame" | `:2107-2112` |

**自审修正**：
1. **整篇重排**：初稿把「注册」「传输」「锁」「错误恢复」平铺成 12 节，
   读者不知道自己在哪条轴上。改为 Part A 时间线 / Part B 横切 / Part C 评价，
   章节号带 A/B/C 前缀。
2. **时间线次序错**：初稿把 `spi_setup()` 排在 `spi_add_device()` 之前。
   实际是 `spi_add_device()`（`:698`）→ `__spi_add_device()`（`:637`）内部调用
   `spi_setup()`（`:669`，失败时 `dev_err` 在 `:671`），而 `device_add()` 排在它
   后面（`:677`）。
   **判据是"谁调用谁"，不是直觉。**
3. §0 导航节（分层图 + 生命周期时间线 + 「哪些不在本篇」去处表）自审后新增。
4. 与 07 的 tracepoint 内容重复了两整节，按「03 只讲机制、07 讲怎么用」去重。

### §3.5 `04-extensions.md`

| 关键论断 | 出处 |
|---------|-----|
| 四个独立子框架：`spi-mem`、slave/target、`spidev`、`spi-bitbang` | 各自的文件 + `Kconfig` 条目 |
| 它们**互不调用** | 交叉 grep：`spi-mem.c` 不含 `bitbang`，`spidev.c` 不含 `mem_ops` 等 |
| `spi-mem` 有 **24 个**控制器实现 `mem_ops` | §2.4 的命令 |
| `spi-ti-qspi.c:893` 会在运行时把 `mem_ops` **设回 NULL** | 二等（点名到行，未读整个函数） |
| `target_abort` **零实现者**（核心里只剩 `spi_target_abort()` 的两个 `-ENOTSUPP` 出口 `:2784`/`:2795`） | §2.5 |
| `spidev` 是 SPI 唯一的字符设备出口 | `grep -rn "register_chrdev\|cdev_add" drivers/spi/` 只命中 `spidev.c` |

**自审修正**：
1. 初稿写「`spi_mem_driver` 在树里有若干个」——含糊。全树 grep 后确定
   **只有 2 个**（`spi-nor` 和 `spi-nand` 各一），改成确数。
2. 初稿说 slave 框架「用得很少」，没有依据。改为 `"spi-slave"` 属性
   **8 个驱动**消费（§2.4），并点出 `target_abort` 零实现者这个更硬的证据。
3. 篇末汇总表（框架 × 职责 × 挂载点 × arm64 是否相关）自审后新增。
4. 每个框架补上「挂在 03 时间线的哪一点」——`spi-mem` 挂在
   `transfer_one()` 的**旁路**（`__spi_async()` `:4010` 允许 `transfer == NULL`），
   这一条纠正了初稿「`spi-mem` 走的还是同一条传输路径」的错误说法
   （`:4009-4010`：`if (!ctlr->transfer) return -ENOTSUPP;`，注释在 `:4005-4008`）。
5. **新增 §7.2「六个框架的全树使用统计」**（吸收自 `output/qoder/spi`，
   见 §8.3 第 3 条）。原来只有 §1.8（spi-mem）和 §3.5（bitbang）有"谁在用"，
   另外四个框架没有。**补齐这张表的过程本身抓出了本篇最严重的一个错误**，
   见第 6 条。
6. **ERROR：「`spi-mux` 在 arm64 DTS 里没有 in-tree 实例」是错的。**

   | | 内容 |
   |---|---|
   | 初稿写的 | §4.7 陷阱表："arm64 DTS 里没有 in-tree 实例"，依据栏还写着 `grep -rl "spi-mux" arch/arm64/boot/dts/` **为空**；§7.1 汇总表跟着写"无 in-tree arm64 实例" |
   | 实际 | **4 个文件 / 5 个节点**，全部是 Microchip Sparx5（arm64）：`sparx5_pcb125.dts:45`/`:57`、`sparx5_nand.dtsi:17`、`sparx5_pcb135_board.dtsi:102`、`sparx5_pcb134_board.dtsi:287`。`compatible = "spi-mux"` 与 `drivers/spi/spi-mux.c:186` 精确匹配 |
   | 重跑 | `grep -rn "spi-mux" arch/*/boot/dts/` → 5 行，全在 `arch/arm64/` 下 |

   **最值得记的不是这个错，是它为什么活到了定稿：**
   `06-devicetree.md` §2.4 从初稿起就**一直在引用其中一个真实节点**
   （`sparx5_pcb125.dts:42-70`），当作"一个 GPIO 片选的真实例子"。
   **两篇文档对同一件事给了相反的答案，而两篇各自都是自洽的。**

   → 这正是 §6.2 那条结论的又一个实例，而且比前几个更硬：
   前几个是"同一张表在两篇里数字不一样"，这一个是
   **"一篇说某东西不存在，另一篇正在引用它"**。
   **判据可以写成一条可执行的检查**：凡是写下"没有 / 为空 / 不存在"的
   全称否定，除了重跑 grep，还要 **`grep` 自己这套文档**——
   如果别篇提到过这个东西，两者必有一个错。

   ```bash
   # 否定性断言的第二道门：先查树，再查自己的文档
   grep -rn "spi-mux" arch/*/boot/dts/          # 树里到底有没有
   grep -rn "spi-mux" output/spi/*.md           # 自己有没有在别处引用过它
   ```

   另外 §4.7 那行的**依据栏是伪造的**——它写着某条命令"为空"，
   而那条命令实际有输出。**依据栏里写"某命令为空"时必须当场跑一次**，
   这类文字比结论更容易蒙混过关，因为读者会以为它已经被跑过了。

### §3.6 `05-usage.md`

| 关键论断 | 出处 |
|---------|-----|
| 四个角色：外设驱动 / 控制器驱动 / `spi-mem` 使用者 / 用户态（`spidev`） | 按 `spi.h` 导出面划分 |
| 外设驱动骨架的每个符号在 6.1.158 存在 | 逐符号全树 grep（见下） |
| `module_spi_driver()` 宏存在 | `spi.h:353`（kernel-doc 在 `:346`） |
| `SPI_MODE_0` 等四个宏存在 | 在 `include/uapi/linux/spi/spi.h:10` 起，**不在** `include/linux/spi/spi.h` |
| `spi_message_init_with_transfers()` 存在 | `spi.h` 一带的 `static inline` |

**自审修正**：
1. **符号存在性排查**：初稿骨架里用了 `spi_master_get_devdata()` 和
   `spi_alloc_master()`。两者在本版本都还存在，但**"别名"这个说法只对一半**：
   `spi_master_get_devdata()` 确实是 `#define`（`spi.h:1601`），
   而 `spi_alloc_master()` **不是任何东西的别名**，它是和 `spi_alloc_host()`
   并列的独立 `static inline`（`:786` vs `:801`），两者各自去调
   `__spi_alloc_controller()`。三种形式的完整拆解见 §2.5.2。
   另外**注释里写的 `spi_register_master()` 已经不是推荐名**（它确实是 `#define`，`:1609`），
   改为 `devm_spi_register_controller()`。
2. 篇首「骨架未编译」声明自审后新增——初稿只把免责声明放在 99，
   而 05 是唯一会被直接抄进代码的篇，**免责必须就地。**
3. 回访了全篇 12 处 `见 0X §Y`，其中 4 处因 03 重排而失效（章节号变成
   了 `§B3.1` 这种带前缀的形式）。**示例篇是陈旧交叉引用的重灾区。**
4. **符号存在性全量普查（定稿轮）**：把正文里 127 个内核风格标识符
   逐个跑 `git grep -w`。52 个空结果全部是本篇自造的示例名（`my*` / `MY*`
   前缀），**没有一个真幻觉**——但普查过程中顺出了别的东西，见第 5、6 条。
   值得记的是：这一轮的收获不在"空结果"里，在**"有结果但结果不对"**里。
5. **4 条行号指向了无关代码**（脚本抓不到这一类，只能逐个 `sed` 打开）：

   | 位置 | 初稿写的 | 实际 | 性质 |
   |-----|--------|-----|-----|
   | `05` §1.5 坑表 | 「核心已经调过 `spi_setup()`」→ `spi_probe()` `spi.c:410` | `spi_probe()` **不调** `spi_setup()`，它只解析 irq / `of_clk_set_defaults`。真正的调用点是 `__spi_add_device()` `spi.c:669` | **结论对、出处错** |
   | `05` §2.3 坑 2 | `SPI_NAME_SIZE` = 32，`spi.h:31` | 这个宏**不在 `spi.h` 里**，在 `include/linux/mod_devicetable.h:498`。`spi.h:31` 是 `extern struct bus_type spi_bus_type;` | 跨头文件张冠李戴 |
   | `05` §2.2 `cs_change` | kerneldoc 在 `spi.h:1015` | `:1015` 是 `struct spi_transfer {` 那一行，注释在它**前面**：`:984-1000`（`:988` 起的 (i)/(ii) 两段才是反语义的说明） | 指到了被注释的对象，不是注释 |
   | `05` §3 / §3.5 | `remove` 返回 `void`，`spi.h:318` 一带 | `:316`。`:318` 是 `struct device_driver driver;` | 差 2 行，"一带"掩盖了它 |

   第 1 条是**跨篇一致性失败**：`03` §3.4 的取证表里早就写对了
   （`__spi_add_device()` `:669`，还专门记了"判据是谁调用谁"），
   **`05` 却独立地写错了同一件事**。和 §2.5.1 的回调计数是同一种病。
6. 两处把标志名写成了简写/邻居名：正文注释里的 `MUST_TX` 补全为
   `SPI_CONTROLLER_MUST_TX`；`SPI_MASTER_GPIO_SS` 处补了一段
   "为什么这个名字不是笔误"（见 §2.5.2 末尾的三分表）。
7. 新增 §1.3 的 `master`/`host` 对照块：`devm_spi_alloc_master` 在
   `drivers/` 下 39 处、`devm_spi_alloc_host` 3 处、`spi_alloc_host` **0 处**，
   所以骨架**故意**用旧名字（读者拿真实驱动对照时不会错位），
   并明说新代码该用 `host`/`target`。**"跟着多数派写示例"这个取舍要写出来，
   否则读者会以为文档过时了。**
8. **新增 §7「近邻符号区分」（9 组）**，起因是与 `output/qoder/spi`
   对照（见 §8）：skill 第 20 条本来就要求"近邻符号要成对写进文档并
   点明区别"，但初稿把这些区分**散在各节的坑表里**，读者要先知道自己
   挑错了才找得到。改成常备对照表后，光是为了填表就查出三条新事实：

   | 组 | 查出来的新事实 | 出处 |
   |---|------------|-----|
   | `spi_async_locked` | 它**不是对外 API**：`static`、无 `EXPORT_SYMBOL`、不在 `spi.h`，全树唯一调用者 `spi.c:4226`。但它有一份**完整的 kerneldoc**（`spi.c:4083-4113`），照着抄会编译不过 | `grep -rn spi_async_locked include/ drivers/` |
   | `slave`/`target` 的 `union` | 命名兼容的**第四种形式**，见 §2.5.2 | `spi.h:561-566`、`:679-682` |
   | `spi_w8r16` vs `spi_w8r16be` | 只差 `be` 两个字母，一个返回**线上原序不做转换**（kerneldoc 原话 "at least sometimes big-endian"），一个走 `be16_to_cpu`（`spi.h:1458`）。arm64 小端上挑错 = 字节颠倒，且在 `0x0000`/`0xffff` 这类对称值上不暴露 | `spi.h:1410-1459` |

   **教训：一份"有完整 kerneldoc 但没导出"的内部函数，比没有文档的
   内部函数更危险**——kerneldoc 的存在会让人以为它是公开接口。
   判断某个符号能不能用，判据是 `EXPORT_SYMBOL*` + 头文件里有没有声明，
   **不是"有没有文档注释"**。
9. 顺带修掉篇末「相关章节」表里 `99 §3.5` → **`§3.6`**。
   同一条错误引用在本篇出现了**两次**（篇首导语 + 篇末表格），
   前一轮只改了篇首那处。**改一条引用要 `grep` 整篇，不是改看到的那一处**
   ——而这一类脚本抓不到：`§3.5` 是真实存在的章节（`04` 的取证记录），
   只是**指错了对象**。

### §3.7 `06-devicetree.md`

| 关键论断 | 出处 |
|---------|-----|
| 核心真正读的外设属性：`reg` / `spi-max-frequency` / `spi-cpha` / `spi-cpol` / `spi-cs-high` / `spi-3wire` / `spi-lsb-first` / `spi-rx-bus-width` / `spi-tx-bus-width` | `of_spi_parse_dt()` `:2240`（唯一调用点 `:2354`），逐个 `of_property_read_bool/u32` 读出 |
| `num-cs` 由 **21 个**控制器驱动各自读，核心不读 | §2.4；完整 21 条 `文件:行号` 列在 §1.2 |
| `num-cs` 的**读法都不统一**（`u16`/`u32`、`device_property_*` vs `of_property_*`、检不检返回值） | 逐个打开那 21 处看 |
| 三个死属性：`spi-rx-delay-us`/`spi-tx-delay-us` 只被 `rmi_spi.c` 读；`spi-cs-*-delay-ns` 全树不存在 | §2.4、§2.5 |
| `spi-cs-high` 对 GPIO 片选**无效**（极性由 GPIO 库管） | `:1014-1015` 的注释 + 代码；`:3174` 静默授予能力 |
| arm64 全树只有 1 个 `spi-cs-high`，且是 native CS，所以它确实有效 | §2.5；`k3-am642-evm.dts:424-435` |

**自审修正**：
1. **`num-cs` 少数了 3 个**：初稿列 18 条并写「20+ 个」。重跑
   得到 **21**，补入 `spi-qup.c:1039`、`spi-zynq-qspi.c:703`、
   `spi-zynqmp-gqspi.c:1183`。原有 18 条行号全部复核正确。
   **"20+"这种含糊的量词是欠债的记号**（第 19 条规律），清掉后
   反而额外发现了"读法不统一"这条新论断。
2. §3.1 交叉引用漂移：`:648`/`:650` 与 `:625` 的过滤条件写反了，改正。
3. `%pOF is not called 'slave'` 的行号 `:2310` → **`:2309`**；
   `can't setup %s, status %d` 的 `:672` → **`:671`**。
4. §4 极性章初稿照绑定文档的真值表写，与代码冲突。**改为以代码为准
   并显式登记冲突**（§1.7 第 1、2 条）。
5. 定位章重排为「编译期 / 运行期静态 / 运行期动态」三层，症状清单按
   坏掉的范围分组（总线级 / 单设备级 / 参数级 / 拓扑级）。

### §3.8 `07-debug.md`

| 关键论断 | 出处 |
|---------|-----|
| `CONFIG_SPI_DEBUG` 唯一作用是 `-DDEBUG`，Kconfig 描述**全假** | §1.7 第 3 条、§2.4 |
| `spi.c` 有 42 `dev_err`、4 `dev_dbg`、**0** `dev_err_ratelimited` | §2.4 |
| `__spi_validate()`（`:3845-3995`）**零日志** | §2.4 的 `sed \| grep -c` = 0 |
| 6 个 tracepoint，**没有一个**带 `TP_CONDITION` | `include/trace/events/spi.h` 全读；§2.5 |
| 传输 tracepoint 会 dump 最多 64 字节 tx/rx | `include/trace/events/spi.h:190-196`（`__dynamic_array` 的 64 字节上限）、`:204-210`（`memcpy`）、`:213` 的 `TP_printk`；排除 dummy 缓冲的两个宏在 `:174-177` |
| `effective_speed_hz` **不在** tracepoint 里 | §2.4 |
| 30 项 sysfs 统计（11 项标量 + 17 档直方图 + 2 项），设备和控制器各一套 | 读 `SPI_STATISTICS_ATTRS()` `:139` 与 `SPI_STATISTICS_TRANSFER_BYTES_HISTO()` `:193` 两个宏 + 数实例 |
| `-EINVAL` 37 处、`-EIO` **0** 处 | §2.4 |
| SPI 核心**没有** debugfs、**没有**故障注入 | §2.4、§2.5 |

**自审修正**（本篇修正最多，8 条，其中 3 条是内容错而非行号漂移）：
1. **标志族写错**：初稿把 `:3903`/`:3905` 说成设备的 `SPI_NO_TX`/`SPI_NO_RX`
   模式位。读 `:3896-3906` 后确定是**控制器**的 `SPI_CONTROLLER_NO_TX`/`NO_RX`，
   且三处都在同一个 `SPI_CONTROLLER_HALF_DUPLEX || (spi->mode & SPI_3WIRE)`
   守卫（`:3895-3896`）之下。这次修正催生了**新的 §5.2.0** 整节——
   「半双工有两个独立来源，第二个是你自己的 `spi-3wire`」。
2. **函数归属错**：`-ESHUTDOWN` `:3341` 初稿写成"在 `__spi_pump_messages()` 里"。
   实际是 inline 助手 `__spi_check_suspended()`（`:3339-3342`），
   **全树只有 `:4184` 一个调用者**，因此它不是独立故障点。重写。
3. **编造了模块参数**：`spi-loopback-test` 表里列了 `bus_num`/`chip_select`
   两个**不存在**的参数，还把 `compatible` 说成"只读"（实际权限 `0000`）。
   读 `:20-75` 后按 9 个真参数重写，补上定义行号和 `loopback` vs `loop_req` 的区别。
4. `spi_stop_queue()` 的代码块锚到了函数定义行 `:2099`；`unsigned limit = 500;`
   实际在 `:2102`。全部重锚（`:2102`/`:2113`/`:2115`/`:2119-2120`，注释 `:2107-2112`）。
5. `-EINTR` 初稿写「`:1363` 一带」。errno 在 `:1364`，而且读 `:1360-1365` 才发现
   **它是 slave 独有的**（`:1361` 的守卫）——顺带纠正了 §6.1 超时机制
   「master 独有」的表述。
6. `-EBUSY` 的 `:2087` 和 `:2120` 被混成一行。前者是 `spi_start_queue()`
   （队列已在跑），后者是 `spi_stop_queue()`（5 秒放弃）。拆成两行。
7. `spi_delay` 的注释范围 `:1439-1442` → **`:1438-1441`**；
   `hz = xfer->effective_speed_hz ?: xfer->speed_hz / 2` 的 `:1444` → **`:1442`**。
8. 四处 tracepoint 行号漂移 + 两处 `TP_printk` 表头漂移；`spi_setup` 的
   格式串按 `include/trace/events/spi.h:67` 原文改正。初稿还错认为
   传输 tracepoint 只带 `len`，实际会 dump 数据。

### §3.9 `08-feynman.md`

| 关键论断 | 出处 |
|---------|-----|
| 主题 1：`spi_controller` 是纯 class 设备 | `:2926`/`:2928` vs `:598`；对照 `i2c-core-base.c:1554-1555` |
| 主题 2：`cs_change` 在最后一段的语义是 `keep_cs = true` | `:1585-1599`，反直觉点在 `:1588` |
| 主题 3：`spi_sync()` 快路径绕开 pump 线程 | `:4204`/`:4210`/`:4213`/`:4215` 与慢路径 `:4224-4228`；注释 `:4198-4203` |
| 主题 4：`spi-cs-high` 对 GPIO 无效 | `:976`/`:996-997`（activate vs enable 分家）+ 四出口表 |
| 主题 5：`dummy_tx` 有 `__GFP_ZERO`，`dummy_rx` **没有** | `:1322` vs `:1330` |
| 主题 6：`len` 必须是 `w_size` 的整数倍 | `:3937-3946` |
| 主题 7：`spi_driver` **没有** `detect`/`address_list` | `spi.h` 的 `struct spi_driver` vs `include/linux/i2c.h:305`/`:306` |
| 主题 8：核心几乎不打日志，靠计数器 + tracepoint 补 | §2.4 的日志普查 |

**自审修正**：
1. 两处范围末端漂移：`w_size` 块 `:3936-3946` → **`:3937-3946`**；
   `__spi_validate_bits_per_word()` `:3640-3646` → **`:3640-3645`**。
2. 主题 1 的「类比边界」初稿只写了「类比不够精确」——**这不算指出技术差异**。
   改为给出可操作的规则：DMA/clk/pinctrl/runtime-PM 必须用 `ctlr->dev.parent`
   而不是 `&ctlr->dev`（`:640`、`:1151`、`:1648`）。
3. 主题 3 初稿说「设 `spi->rt` 能让传输走实时线程」。实际当
   ratio ≈ 1.0 时快路径根本不进 pump 线程，`spi->rt` **无效**。改写。
4. 主题数从 5 补到 **8**（模板下限 ≥6）；篇末导航表新增。

---

## §4 交叉验证实例（推翻了初始假设的）

**这一节记录"我本来以为 X，验证后发现是 Y"。** 它的价值是让读者知道
**哪一类直觉在 SPI 上不可靠**。

### §4.1 「总线子系统都把控制器注册在自己的总线上」——错

**初始假设**：`spi_controller` 和 `i2c_adapter` 一样，注册在各自的子系统总线上，
靠 `dev.type` 和设备区分。这个假设很自然，因为 I2C 就是这么做的。

**验证过程**：

```bash
grep -n "dev.bus\|dev.class\|dev.type" drivers/spi/spi.c | head
# 598:	spi->dev.bus = &spi_bus_type;
# 2926:	ctlr->dev.class = &spi_master_class;      ← class，不是 bus
# 2928:	ctlr->dev.class = &spi_slave_class;

grep -n "dev.bus\|dev.type" drivers/i2c/i2c-core-base.c | head
# 1554:	adap->dev.bus = &i2c_bus_type;            ← 真的在 i2c 总线上
# 1555:	adap->dev.type = &i2c_adapter_type;
```

**结论**：**两个最相似的子系统做了相反的选择。** SPI 用 class，所以
`spi_match_device()`（`:375`）不需要判断 `dev.type`；I2C 用 bus + type，
所以它的 match 必须先排除 adapter。

**影响面**：这一条改写了 `02` §1.0、`03` §0 的分层图、`08` 主题 1、
以及 `00` §7 的子系统关系图。**如果没验证，四篇文档会一起错。**

### §4.2 「`spi_bitbang` 有 N 个使用者」——第一次数错了，因为没排除自己

**初始假设**：`grep -rn "spi_bitbang_setup" drivers/ | wc -l` 得到的数字
就是使用者数量。

**验证过程**：

```bash
grep -rn "spi_bitbang_setup" drivers/ | wc -l          # 含 spi-bitbang.c 自己的定义和 EXPORT
grep -rln "spi_bitbang_start\|spi_bitbang_setup" drivers/ | grep -v "spi-bitbang\.c"
# ← 排除框架自己，才是真实使用者
```

**结论**：**框架自己的定义 + `EXPORT_SYMBOL` + 头文件声明，都会被算成"使用者"。**
必须 `grep -v <框架自己的文件>`。

**推广**：这个坑对所有「有 N 个实现者 / 使用者」的论断都成立。
本文档集所有这类计数都补了 `grep -v`（§2.5 表里可见）。
**这是方法上而不是事实上的修正，但它影响 `04` 全篇的计数可信度。**

### §4.3 「绑定文档说会警告，那就是会警告」——错，而且反了两次

**初始假设**：`spi-controller.yaml` 的极性真值表和 "Should print a warning
about polarity inversion" 是权威描述，可以直接写进 `06` §4。

**验证过程**：

```bash
grep -rni "polarity" drivers/spi/*.c | grep -i "warn\|dev_err\|dev_info"
# 空 —— 警告不存在

sed -n '1005,1025p' drivers/spi/spi.c
# :1012	if (has_acpi_companion(...))                ← ACPI 分支
# :1014		/* Polarity handled by GPIO library */
# :1015		gpiod_set_value_cansleep(..., activate);  ← DT 分支用 activate，未翻转
```

**结论**：**两条都不成立**——警告不存在，而且真值表里 GPIO 那两行的
极性方向和代码相反。

**为什么值得单列**：这不是"文档略微过时"，而是**绑定作者自己标了 Note
说这两种组合有歧义、要求实现给警告**，结果实现既没警告、行为也和表格不一致。
**三等证据在这里如果不复核，`06` §4 整章会是错的，而且是"读者照着配、
硬件不工作、还查不出原因"的那种错。**

### §4.4 「`spi_sync()` 一定要经过消息队列和 pump 线程」——不一定

**初始假设**：SPI 核心有队列有 kthread，所以 `spi_sync()` 就是
「入队 + 等 kthread 处理完」。

**验证过程**：读 `:4183` 起的 `__spi_sync()`，`:4204` 有个分支：

```c
	if (__spi_check_suspended(ctlr))                 /* :4184 */
		return -ESHUTDOWN;
	...
	if (!ctlr->transfer_hook_or_queue || ...)        /* :4204 一带的 must_async 判断 */
```

`:4210`/`:4213`/`:4215` 是**在调用者上下文里直接跑完**的快路径；
`:4224-4228` 才是入队 + `wait_for_completion()`。

```bash
grep -rn "must_async" drivers/ include/linux/spi/spi.h
# 唯一的写点：drivers/spi/spi-mux.c:165
```

**结论**：**默认走快路径，不进 kthread。** 只有 `spi-mux` 这一个驱动
会强制走异步。

**影响面**：改写了 `03` Part A 的时间线（不能画成单一路径）、
`08` 主题 3 整节、以及一条本来要写进 `07` 的错误建议
（初稿准备建议「传输抖动大就设 `spi->rt`」——快路径下这个开关**没有作用**）。

### §4.5 「`num-cs` 是核心解析的通用属性」——错，21 个驱动各解析一遍

**初始假设**：`num-cs` 出现在绑定文档的 controller 通用部分，应该由
`spi.c` 统一解析。

**验证过程**：

```bash
grep -n "num-cs\|num_chipselect" drivers/spi/spi.c
# 核心里没有 "num-cs" 字符串

grep -rn '"num-cs"' drivers/spi/ | wc -l    # 21
```

**结论**：核心**完全不读** `num-cs`，21 个控制器驱动各自读，
而且读法不统一（`u16` / `u32` / `device_property_*` / `of_property_*`、
检不检返回值）。

**附带发现**：`spi-hisi-kunpeng.c:484` 用的是
`device_property_read_u16()`——**16 位**。DTS 里写 `num-cs = <70000>`
在这个驱动上的行为和别的驱动不一样。

**影响面**：`06` §1.1 的总表要把 `num-cs` 从「核心读」挪到「驱动读」，
§1.2 单列一节。**"绑定文档把它放在 controller 通用节"不等于"核心解析它"**——
这是 `06` 全篇的方法基础。

---
## §5 未验证项（四层）

**这一节的价值不低于前面九篇——它决定读者该在哪里加倍小心。**
分层的意义是：**四层的风险性质完全不同，补救办法也不同。**

### §5.1 第一层：未编译、未运行

| 内容 | 风险 | 怎么自己补 |
|-----|-----|----------|
| `05-usage.md` 的**全部**代码骨架 | 从没进过编译器。可能有语法错、头文件缺失、字段名拼错 | 抄进一个真实模块骨架里 `make M=` 编一次 |
| `06-devicetree.md` §5 的 DTS 片段 | **例外**：全部取自 in-tree 真实节点，编过（它们在本树里就是被编译的），但**没在硬件上跑过** | `make dtbs` 已经由上游保证；行为要真板子 |
| `07-debug.md` 的所有命令 | **一条都没在本机执行过**（这棵树没编译、没起过内核） | 见下一小节 5.1.1 |
| `04-extensions.md` §6 的 `spi-loopback-test` 用法 | 参数名和权限是读出来的，**但没插过模块** | `modprobe spi-loopback-test` 需要真硬件或 `spi-gpio` 回环 |

#### §5.1.1 关于 `07-debug.md` 里的"示例输出"

**纪律：示例命令的输出一律不编。** 本文档集的处理方式：

| 情况 | 处理 |
|-----|-----|
| 输出格式能从**格式串**推出（如 tracepoint 的 `TP_printk`） | 按格式串给出格式示意，并标明"格式来自 `include/trace/events/spi.h:213`，不是实测" |
| 输出是内核打印的**固定字符串**（如 `SPI transfer timed out`） | ✓ 直接引用，因为字符串本身是从源码抄的 |
| 输出是**运行时数值**（统计计数、`/sys` 内容、`ls` 结果） | **只写字段名和含义，不写具体数字**；需要举例时明确标"示意" |

所以 `07-debug.md` 里**没有一段伪造的终端回显**。这是它和很多调试文档的区别。

### §5.2 第二层：只读了符号表 / 只读了部分行

**这一层的风险是"契约对、行为未必对"。**

| 内容 | 只读到什么程度 | 具体哪些论断要打折 |
|-----|-------------|----------------|
| **130 个控制器驱动** | 只做针对性 grep | 所有"有 N 个驱动这么做"的计数是可靠的（grep 出来的）；但"这些驱动**怎么**做"的概括**没有**——因为本文档集刻意没写这类概括 |
| `spi-bitbang.c`（453 行） | 符号表 + 两个函数签名 | `04` 里 bitbang 的**分层描述**可靠（来自符号表），**执行细节**未验证 |
| `spi-loopback-test.c`（1111 行） | `:20-75` + `:375-382` | 参数表可靠（逐行读的）；**"它测什么、不测什么"这个判断来自用例表的表头，没有逐行读断言逻辑**——`07` §7.3 那句"它测的是核心 + 控制器驱动这一段，不测外设"属于这一层 |
| `spi-rockchip.c` / `spi-mxs.c` / `spi-ti-qspi.c` / `spi-hisi-kunpeng.c` | 各只读了点名的那几行 | `04` 里"`spi-ti-qspi.c:893` 运行时把 `mem_ops` 设回 `NULL`"——**这一行读了，但没读它所在的函数**，所以"什么条件下会发生"是未验证的 |
| `Documentation/spi/` 未读的 4 个文件（411 行） | 完全没读 | 如果它们里面有和 arm64 相关的陷阱，本文档集会漏 |
| 89 个未通读的 DT 绑定文件 | 只按需 grep | `06` §1.3 的"死属性"清单可能不全——**只覆盖了通用绑定里的属性**，各驱动私有属性没系统排查 |

### §5.3 第三层：推断，不是观察

**这一层最危险，因为读起来和事实一样。所有这类论断在正文里都标了"推断"。**

| 论断 | 为什么是推断 | 反驳它需要什么 |
|-----|-----------|-------------|
| 「`/sys/bus/spi/devices/` 里有设备节点，**只证明 DTS 写了它，不证明芯片焊上了**」（`08` 主题 7、`07` §8 步骤 0） | 从"SPI 无应答、`spi_driver` 没有 `detect`"推出来的**逻辑结论**，不是观察到的现象 | 找一个会在 probe 里读 ID 并失败时 `-ENODEV` 的驱动——那种情况下节点确实会消失。**所以这条的准确说法是"核心不验证，验证与否取决于外设驱动"** |
| 「乘 2 是给控制器的调度/中断延迟留余量」（`07` §6.1） | 源码注释只说 "Increase it twice and add 200 ms tolerance"（`:1379-1382`），**没说为什么** | 查 git log 找引入这个常数的提交 |
| 「`spi_controller` 用 class 而不是 bus，是为了让 `match` 不必判断 `dev.type`」（`08` 主题 1） | **这是我给的解释，不是代码或注释说的**。代码只呈现结果（`:2926`/`:2928` vs `:474`） | 查 SPI 子系统早期提交的 changelog |
| 「`spi-cs-high` 只剩 native CS 一个用途，所以它不是死属性」（`06` §4、`08` 主题 4） | 前半是代码（`:1015` vs `:1022`），**后半"所以它有用"是推断** | 确认 `k3-am642-evm.dts:432` 那块板子真的工作 |
| 「三条通用 RT 语义作用在未修改的 SPI 代码上」（`03` §C4） | **完全是推断**：`spin_lock_irqsave` 在 RT 上变可睡的 rt_spinlock 是通用语义，但**本文档集没有验证 SPI 的具体路径在 RT 上会怎样** | 在 RT 内核上实测；或读 RT 补丁对 `spinlock` 的完整改动 |
| 「`dummy_rx` 没有 `__GFP_ZERO` 所以内容是垃圾」（`08` 主题 5） | 前半是代码（`:1322` vs `:1330`）；后半"是垃圾"是**从 `krealloc` 语义推的**，没有观察 | 实测读一次 dummy_rx 的内容 |

**本层的自审规则**：一条论断如果找不到"哪一行说了这个"，就必须降到这一层，
**哪怕它显然是对的**。上表第 3 条（class vs bus 的动机）就是典型——
结论我很确信，但代码里没有任何一行说出这个动机。

### §5.4 第四层：版本时效

| 时效性 | 说明 |
|-------|-----|
| **基线** | 6.1.158 + rt58。所有行号只对这一个提交（`f6e38ae624cf`）成立 |
| **最脆弱的是行号，最稳的是结构** | `spi.c` 4632 行，一个上游 backport 就能让所有行号漂移。**结构性论断**（controller 用 class、核心不打日志、`num-cs` 由驱动读）跨版本稳定得多 |
| **命名迁移正在进行中** | 本版本 `master/slave` 和 `controller/target` **两套命名共存**：`spi_master` 是 `spi_controller` 的别名（`spi.h:1593`），而 `spi_target_abort()`（`:2784`）用的是新命名，`spi_controller_is_slave()`（`:1361` 用到）还是旧的。**更新的内核里 slave→target 的改名会更彻底，`05`/`04` 的示例要跟着改** |
| **`spi-mem` 在扩张** | 24 个控制器实现 `mem_ops`（§2.4/§2.5.1）。这个数字每个版本都在涨，`04` §2 的计数最容易过期 |
| **整张回调实现数表都是快照** | §2.5.1 的 20 个数字全部是「本版本有几个驱动实现了这个回调」，**每加一个控制器驱动就变**。本轮有 9 个数字是错的（初稿写的和定稿重数的不一样），说明这类数字**不重跑就一定过期**。重跑命令见 §2.5.1 |
| **命名其实有三代，不是两代** | 除了 `master/slave` 和 `controller/target`，6.1.158 里**已经有第三代 `host/target`**：`spi_alloc_host()`（`spi.h:801`）、`spi_alloc_target()`（`:807`）、`devm_spi_alloc_host()`（`:835`）、`devm_spi_alloc_target()`（`:841`），而且 **3 个驱动已经在用** `devm_spi_alloc_host()`（`spi-axi-spi-engine.c:512`、`atmel-quadspi.c:636`、`spi-microchip-core.c:518`）。`01` §1.1 已经把这一代名字列全了（本轮核对过它那张表的 80 / 8 / 38 / 2 / 4 五个计数，全部正确）；**但 `05` 的两个控制器骨架（`:141`、`:595`）和篇末速查表用的还是 `devm_spi_alloc_master()`**——能用，只是落后一代。另外 `spi_alloc_host()` 目前 **0 个调用点**、`devm_spi_alloc_host()` 只有 3 个，所以**「新名字更规范」在 6.1 里还只是趋势，抄现有驱动仍然会抄到旧名字** |
| **绑定文档的冲突可能已被上游修掉** | §1.7 的 5 条冲突是 6.1.158 的状态。重跑 §2.5 那几条命令就能知道还成不成立——**这是本篇最值得重跑的部分** |
| **DTS 计数** | 174 个 arm64 DTS 用 `spi-max-frequency`、1 个用 `spi-cs-high`。新板子的加入会改变后者的"唯一性"，而 `06` §5.5 整节建立在这个唯一性上 |

### §5.5 一条明确的"我不知道"

**`spi_stop_queue()` 的 5 秒（`limit = 500` × `usleep_range(10000, 11000)`）
超时后，控制器处于什么状态？**

代码只说返回 `-EBUSY`（`:2120`），`ctlr->running` **不会**被置 false（`:2121-2122`
的 `else` 分支才置）。`spi_unregister_controller()`（`:3297`）拿到 `-EBUSY` 之后
（`:3313`）会怎么办——`03` 里写了它继续往下拆，但**这条路径上的资源是否泄漏，
本文档集没有查清**。

**这是唯一一处"读了代码仍然给不出结论"的地方，如实登记比含糊过去有用。**

---

## §6 批量自检

### §6.1 三个脚本的实际输出

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/spi
python3 $S/check_xrefs.py     $D --ignore $D/.xref-ignore
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh spi $D .
```

**`check_xrefs.py`（跨篇 `0X §Y` 引用）**：

```
文档 10 篇；跨篇引用命中 226 条，失效 0 条（忽略项 8 条）
干净：所有跨篇引用都指向真实存在的章节。
```

那 8 条忽略项**全部出自"举失效引用的例子"的表格**——4 条出自本节下面
那张表（本套自己重排造成的），4 条出自 §8.2（`qoder` 那套里失效的章节号）。
表里必须原样保留失效前的写法，否则示范不出"失效引用长什么样"。
登记在 `.xref-ignore` 里并写了理由，不是改文档去迁就脚本。

**写完任何一张"失效引用长什么样"的表都要再跑一次脚本**：
把失效引用原样抄进文档，脚本就会把它们当成新的失效引用报出来。
这件事在本文档集里**发生了两次**（§6.1 的表、§8.2 的表），
第二次仍然踩了——说明它不是"记住就行"，而是要写成固定动作：
**99 篇每次新增引用类表格 → 立刻重跑 `check_xrefs.py` → 新增项进 ignore**。

定稿前的三轮里，这个脚本抓到 **4 条失效引用**，全部是**重排造成的**，
不是写错：

| 失效引用 | 原因 | 修法 |
|---------|-----|-----|
| `01-api.md:30` → `99 §8` | 99 的章节编号在定稿时收敛成 §1–§7，规模统计落在 §1.4 | 改成 `99 §1.4` |
| `03-internals.md:115` → `06 §A4` | **误报**：句子里的 `§A4` 指的是**本篇**的 A4 节，但它紧跟在 `06-devicetree.md` 后面，脚本按"最近的文档名"归属 | 把 `§A4` 改写成"本篇 A4 节"，去掉 `§` 让脚本不再匹配 |
| `03-internals.md:1432` → `99 §5` | 引用写在前，`99 §5` 还没写 | 写完 §5 即命中 |
| `99-evidence.md:726` → `07 §5.1.1` | **和第 2 条同一种误报**：这一行讲的是 `07-debug.md` 的命令，句末"见下面的 §5.1.1"指的却是**本篇**的 §5.1.1 | 改写成"见下一小节 5.1.1"，去掉 `§` |

**第 2、4 条值得记住**：脚本的归属规则是"同一行里最近的文档名"，
所以**在提到别篇的同一行里写本篇的章节号会误报**。
处理办法不是加 ignore，而是把话说清楚——**误报往往说明原文确实有歧义**：
"见下面的 §5.1.1"这句话，人读到这里也得先愣一下才知道是哪一篇的 §5.1.1。
这一类误报在本轮出现了 **4 次**（`03` 一次、`99` 自己三次），
说明它不是偶然，而是"表格单元格里同时出现别篇文件名和本篇章节号"
这个写法本身的问题。后两次的经过值得完整记下来：

| 次 | 犯在哪 | 触发写法 | 处置 |
|---|-------|--------|-----|
| 3 | 8.6 节「回写 skill」的落点表 | 左栏是 `05-usage-template` 这种**带 `.md` 后缀**的模板文件名，右栏是本篇的 8.3 小节号 | **去掉后缀**（写成 `05-usage-template`），脚本不再把它当篇号 |
| 4 | **就在上面这段解释里** | 为了讲清第 3 次，正文里原样写出了那个带后缀的文件名 + 本篇小节号 | 把小节号改成"8.3 小节"，去掉 `§` |

第 4 次最能说明问题：**连"解释这个陷阱"的那段文字本身都会触发这个陷阱**。
所以处置有优先级：**先消除触发条件，再考虑登记忽略项**——
去掉 `.md` 后缀、把 `§8.3` 改写成"8.3 小节"、文件名和章节号拆到两列，
误报归零而读者读到的信息一个字没少。
`.xref-ignore` 留给"内容本身必须保留那个老编号"的场合（本篇的 8 条就是）。

**`check_line_refs.py`（`路径:行号` 是否越界）**：

```
扫描 502 条唯一 file:line 引用（消歧表 69 个基名 / 34 个前缀目录，忽略项 0 条）

== AMBIGUOUS（1 个简写文件名，树里有多个候选）==
   已按文档里最常用的全路径校验，但正文最好写全路径：
  spi.h → 按 include/linux/spi/spi.h 校验；另有 include/uapi/linux/spi/spi.h, include/trace/events/spi.h

干净：没有解析不到的文件，也没有越界行号。
```

**这条 AMBIGUOUS 不是噪声，它抓到了一个真实缺陷。** SPI 树里有
**三个** `spi.h`：

| 路径 | 行数 | 里面有什么 |
|-----|-----|----------|
| `include/linux/spi/spi.h` | 1614 | 结构体、API、`spi_master` 别名 |
| `include/trace/events/spi.h` | 239 | 6 个 tracepoint |
| `include/uapi/linux/spi/spi.h` | **42** | `SPI_MODE_0..3`、`SPI_CPHA`/`SPI_CPOL` 等**模式位** |

**脚本会把所有裸 `spi.h:N` 都按 1614 行的那个校验**，所以一个本该指向
uapi 头（只有 42 行）的 `spi.h:174` 会**静默通过**。逐个打开裸引用后
发现两处需要改：

1. `99-evidence.md` 里 `spi.h:174-177` 实际是 `include/trace/events/spi.h`
   的两个宏 → 补全路径。
2. `SPI_MODE_0..3` 一度被写成在 `include/linux/spi/spi.h:180-186`。
   **真实位置是 `include/uapi/linux/spi/spi.h:10` 起**——
   `include/linux/spi/spi.h` 里根本没有 `SPI_MODE_0` 这个符号
   （`grep -rn "define SPI_MODE_0" include/linux/` 为空）。

**教训**：**同名头文件是"行号在范围内但指向无关代码"这一类缺陷的
最大来源，而这一类恰好是三个脚本都抓不到的。** 唯一的办法是
**看到 AMBIGUOUS 就把那几条裸引用全部打开**。本篇之后所有涉及
uapi / trace 的引用都写全路径。

**`check_doc_coverage.sh`（官方文档反向覆盖率）**：

```
=== 【必查】概念/框架文档 ===
  butterfly                                引用 4 次
  pxa2xx                                   引用 7 次
  spi-lm70llp                              引用 3 次
  spi-sc18is602                            引用 1 次
  spi-summary                              引用 8 次
  spidev                                   引用 155 次
  driver-api/spi                           引用 9 次

=== 【参考】DT 绑定：共 58 篇，被引用 12 篇 ===
  rockchip-sfc（3 次）      spi-cadence（4 次）        spi-controller（9 次）
  spi-fsl-lpspi（1 次）     spi-gpio（15 次）          spi-mux（49 次）
  spi-nxp-fspi（1 次）      spi-peripheral-props（8 次）spi-pl022（1 次）
  spi-rockchip（24 次）     spi-sunplus-sp7021（1 次）  spi-xilinx（3 次）
  提醒：06 篇的硬要求是每条属性都有定位到行的消费者，不是每篇绑定都被引用。

【必查】共 7 篇，其中 0 篇 0 引用。
必查档全部有引用。
```

三点要说明：

1. **`driver-api/spi` 的 9 次引用全部是本轮补的。** 它上一轮是 **0 引用**，
   而且我当时**不知道这个文件存在**——`Documentation/spi/index.rst` 不收录它。
   补读的收获见 §1.7 第 5 条和 §2.3。这是三个脚本里唯一能发现
   "我漏读了官方文档"的那个。
2. **4 个"未读"的 rst 也有引用次数**（`butterfly` 4 / `pxa2xx` 7 /
   `spi-lm70llp` 3 / `spi-sc18is602` 1）。**引用 ≠ 读过**——这几次
   引用都是在 §1.6 和 §2.7 里**声明"我没读它、为什么不读"**。
   脚本只查"提到没提到"，不查"读没读"，所以 0 引用是硬错误，
   非 0 引用不等于覆盖到位。
3. **58 篇绑定里只有 12 篇被引用**，脚本自己也提醒这不是缺陷：
   `06` 的硬要求是每条**属性**都能定位到消费代码行，不是每篇**绑定**都被引用。
   剩下 46 篇是单个控制器的私有属性，arm64 上不一定碰到。

### §6.2 三个脚本都抓不到的那一类

| 缺陷类型 | 谁能抓 |
|---------|-------|
| 跨篇引用指向不存在的章节 | `check_xrefs.py` ✓ |
| 行号超出文件长度 | `check_line_refs.py` ✓ |
| 官方文档里有、我没写的主题 | `check_doc_coverage.sh` ✓ |
| **行号在范围内，但指向无关代码** | **没有脚本能抓。只能逐个 `sed` 打开看。** |
| **函数归属错**（行号对，说成另一个函数里的） | 同上 |
| **标志族错**（`SPI_NO_TX` vs `SPI_CONTROLLER_NO_TX`） | 同上 |
| **编造出来的符号**（模块参数、字段名） | 全树 grep，见第 20 条方法 |
| **否定性断言写反了**（"树里没有 X"，其实有） | **没有脚本能抓。** 要重跑 grep **并且** `grep` 自己这套文档，见 §3.5 第 6 条 |
| **跨篇引用有效但指错对象**（`99 §3.5` 存在，但该指 `§3.6`） | **`check_xrefs.py` 抓不到**——它只验章节号存在，不验语义 |

本轮从初稿到定稿共修正 **86 条**（口径：一处被改动的事实 = 1 条；
同一论断在多篇里同步改算 1 条）：

| 类型 | 条数 | 谁发现的 |
|-----|-----|---------|
| 行号 / 范围漂移 | **29** | 0——**全部在文件长度内，脚本一条都抓不到**，靠逐个 `sed` 打开 |
| 内容错（函数归属、标志族、编造符号、计数错） | **33** | 1（`00` §2"唯一权威描述"这条假唯一性断言由 `check_doc_coverage.sh` 反查出来） |
| 跨篇引用失效 | 4 | **4（全部）** |
| 跨篇引用**有效但指错对象** | **1** | 0——`99 §3.5` 是真实章节，只是该指 `§3.6`（见 §3.6 第 9 条） |
| 同名头文件 / 裸文件名混淆 | **3** | **2**（`check_line_refs.py` 的 AMBIGUOUS 提示 + 1 条 UNRESOLVED；余下 1 条是顺着 AMBIGUOUS 人工找出的） |
| 组织 / 格式重排（"内容对但读不懂"那一轮） | 14 | 0 |
| 与另一套独立文档集对照后的吸收改动（§8） | **2** | 0（`05` §7、`04` §7.2 两个新增节） |
| **合计** | **86** | **7（8%）** |

**"内容错"最后 4 条全部出在与 `qoder` 那套对照的过程里**
（§8），而且**没有一条是对照时"发现对方写得对"发现的**——都是
为了核对对方某句话对不对，去打开源码，顺手看见了自己的错：

| 触发点 | 顺出来的自己的错 | 记在 |
|-------|-------------|-----|
| 核对"`spi_target_abort` 是别名吗" | 命名兼容的**第四种形式**（`union`）漏了，"三种形式"这个结论写进了 4 篇 | §2.5.2 |
| 为 `05` §7 填"近邻符号"表 | `spi_async_locked` 有完整 kerneldoc 但没导出，差点写成可用 API | §3.6 第 8 条 |
| 为 `04` §7.2 填"全树使用统计" | 「`spi-mux` 无 arm64 实例」写反了，而 `06` 一直在引用那个实例 | §3.5 第 6 条 |
| 篇末速查表复核 | `99 §3.5` → `§3.6`（同一条错引用在 `05` 里出现两次，前一轮只改了一处） | §3.6 第 9 条 |

**这四条的共同点：都是"为了给一张表填格子"逼出来的。**
逐篇 review 时这些地方都读过、也都"读得通"。
**把散落的判断收拢成一张必须填满的表，是比再读一遍更有效的查错手段**——
因为空格子无法蒙混过关，而流水的正文可以。

**"内容错"从 14 涨到 29，涨的全部来自最后两轮全量普查**，
而这两轮都不是逐篇 review——是**横着切一刀，只查一件事**：

| 普查 | 查什么 | 逮到 | 记在 |
|-----|-------|-----|-----|
| 回调实现数重数 | 15 个回调，每个数一遍实现者 | 14 个错数字，其中 9 个在 `01`、5 个在 `02`，**两篇互不相符** | §2.5.1 |
| 符号存在性 + 命名形式 | `05` 的 127 个标识符；顺出 `master`/`host` 的三种形式 | 1 个幻觉符号（在 `00`）、4 个计数错、1 个"六个标志"错、4 条行号指错 | §2.5.2、§3.6 |

**这是本轮方法论上最重要的一条结论：逐篇 review 结构上抓不到
"同一张表在两篇里数字不一样"。** 因为每篇 review 时那些数字**看着都合理**，
只有把同一个论断在所有篇里横着并排，差异才会跳出来。
**跨篇数值对账必须是独立的一遍，不能当成逐篇 review 的副产品。**
已回写进 skill（新增一条工作流步骤）。

（另有 §1.7 第 5 条不计在内——那是**新发现的源码/文档冲突**，不是修正。）

**29 条行号漂移里有 15 条出在本篇（`99`）自己的草稿里。**
写 §3 逐篇取证表时我凭记忆写行号，一次写下 14 条错的；
`07` 的 `-ETIMEDOUT` 那条也是同一批。原因是本篇的 §3 是
"回忆着总结"出来的，而 `00`~`08` 的多数条目在写的时候当场 `grep` 过。

**但"定稿篇的行号是干净的"这个说法，被最后一轮 `05` 普查推翻了。**
本篇曾经就是这么写的，现在必须改口：`05` 里另有 **4 条行号落在文件长度内
却指向无关代码**（明细见 §3.6 第 5 条），全部躲过了两个脚本。

失效方式很一致——**都是"当场 grep 过、但 grep 的是符号，没回头看它落在哪"**：

| 子型 | 例子 | 为什么当场 grep 也漏 |
|-----|-----|-------------------|
| 跨头文件张冠李戴 | `SPI_NAME_SIZE` 记成 `spi.h:31`，实际在 `mod_devicetable.h:498` | `grep SPI_NAME_SIZE include/linux/spi/spi.h` **有输出**（`:200`/`:1517` 在用它），于是以为定义也在这儿 |
| 指到被注释的对象而非注释 | `cs_change` 的 kerneldoc 写成 `spi.h:1015` | `:1015` 是 `struct spi_transfer {`，注释在它**上面** `:984-1000`。grep 关键字命中的是结构体名 |
| 结论对、出处错 | 「核心已调过 `spi_setup()`」挂在 `spi_probe()` `:410` 名下 | `spi_setup` 确实被核心调，但调用点在 `__spi_add_device()` `:669`。**只验证了结论，没验证归属** |
| 差 2 行被"一带"掩盖 | `remove` 原型 `:318`（实为 `:316`） | 写"一带"就等于给自己发了免检 |

**所以"一带 / 附近 / 起"这类模糊限定词要当成欠债记号**（和第 19 条里的
"—""可配""20+"同类）。它们唯一的作用是让错行号读起来像是对的。

这正是方法论第 15 条那句话的实测数据：
**"每一轮 review 自己会产生新的错误，自审的对象必须包含自审这一轮新写的文字。"**
取证篇是全套里最容易自己烂掉的一篇，因为它通篇都在复述别处的行号。

**结论：脚本只覆盖了 86 条里的 7 条（8%）。** 它们的价值是**把机械的
那部分彻底清零**，好让人工复核集中在"行号在不在范围内不重要、
指的东西对不对才重要"这类问题上——**不是替代逐条 `sed`。**

---

## §7 怎么重跑这份取证

换了内核版本、或者隔了几个月回来，按这个顺序跑：

| 步骤 | 命令 | 判据 |
|-----|-----|-----|
| 1. 基线 | §1.1 的三条 | 版本变了就必须重跑 2–5 |
| 2. 补丁影响 | §1.2 的 `git status --porcelain` + `git diff --stat` | **如果输出多了文件，整份文档的定性前提就变了** |
| 3. 规模 | §1.4 的 `wc -l` / `ls \| wc -l` | 行数变了 → 全部行号可疑 |
| 4. 计数 | §2.4 整段（可以直接贴进 shell） | 任何一个数字对不上 → 对应章节要重读 |
| 5. 全称断言 | §2.5 表里的 10 条命令 | **最容易失效的一类**：新驱动 / 新 DTS 一加进来就不成立 |
| 6. 文档冲突 | §1.7 的 4 条 | 上游可能已经修了——修了就要把正文里的"冲突"说明改掉 |
| 7. 脚本 | §6.1 的三条 | 必须全绿；AMBIGUOUS 出现就逐条打开裸引用 |
| 8. 逐条 `sed` | §3 每篇取证表里的行号 | **脚本抓不到的那一类只能靠这一步**，见 §6.2 |

**第 2 步单独标出来的原因**：这份文档最强的一条结论是
「除了统计读取路径，SPI 全部等价于纯净上游代码」（§1.2）。
**它是一条会随时间失效的结论，而失效之后前九篇的适用范围会立刻收窄。**

---

## §8 与另一套独立生成的文档集对照（`output/qoder/spi`）

本子系统有一个特殊条件：**同一个 skill、同一棵树、由另一个模型独立生成过
一套完整的 10 篇**（存在 `output/qoder/spi/`）。这给了一次难得的机会——
把"自审"换成"对照"。本节记录这次对照的方法和结论。

### §8.1 对照的前提：先写完再看

**本套 10 篇全部定稿、三个脚本全绿之后，才第一次打开
`output/qoder/spi/`。** 这一条是刻意的：**先读就没有独立对照了**，
两套文档会收敛成一套，共同的盲点也就永远发现不了。

代价是重复劳动（同样的行号查了两遍）；收益是每一处**分歧**都是一个信号：
要么其中一方错了，要么这个地方本身就容易出错。

### §8.2 先跑脚本，再读内容

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
python3 $S/check_xrefs.py     output/qoder/spi
python3 $S/check_line_refs.py output/qoder/spi .
```

| 检查 | 本套 | `qoder` 套 |
|-----|-----|-----------|
| 跨篇引用失效 | **0** | **6 条**（`04`→`02 §1.7` ×3、`04`→`02 §1.8`、`04`→`03 §A1.2`、`06`→`07 §8.5`） |
| 解析不到的文件名 | **0** | **6 个** |
| 其中最典型的一个 | — | `06-devicetree.md:555` 引用 **`i2c-core-of.c`**——**SPI 文档里残留的 i2c 文件名** |

**`i2c-core-of.c` 这一条值得单独记**：它说明"用上一个子系统的文档集做
模板"这个做法会**把上一个子系统的文件名一起带过来**，而这类残留
**只有脚本查得出来**（人读的时候 `i2c-core-of.c` 和 `spi-core-of.c` 长得太像）。
本套没有这个问题的原因不是更小心，而是 `check_line_refs.py` 从第一轮就在跑。

### §8.3 `qoder` 做得更好的地方 → 吸收了什么

| # | `qoder` 的做法 | 本套原状 | 处置 |
|---|-------------|--------|-----|
| 1 | `05` 有一节**独立的「近邻符号区分」常备表** | 同样的内容**散在各节的坑表里** | **吸收**：新增 `05` §7，9 组，见 §3.6 第 8 条 |
| 2 | 章节标题里带函数/结构体的**行号范围**（`spi_register_controller()（:3099-3236）`） | 标题只有函数名 | **吸收**：见 §8.5 |
| 3 | `04` 每个框架用**统一的固定小节骨架**（它是什么／挂在哪／数据结构／原理／**全树统计**／陷阱） | 本套 `04` 的骨架已经统一（每节都是 `.1 它解决什么问题` → `.2 挂在核心的哪一点` → …→ 末节陷阱表），**唯一缺的槽位是"全树统计"**：只有 §1.8（spi-mem）和 §3.5（bitbang）有，另外四个框架没有 | **吸收，但按本套的体例**：不逐节加，而是新增一张合并表 `04` §7.2「六个框架的全树使用统计」，六个框架每格都配可重跑命令。**填这张表时抓出了本篇最严重的错误**（`spi-mux` 的 arm64 实例，见 §3.5 第 6 条） |
| 4 | `05` 每个角色小节**末尾各有一张该角色的 API 速查** | 只有篇末 §6 一张总表 | **不吸收**：本套 §6 是 5 行的一张总表，拆成 5 处反而更难对照。这是取舍不是缺陷，记在这里以免下次又"发现"一遍 |

第 1 条最值得记的地方是：**skill 第 20 条本来就写着"近邻符号要成对写进
文档并点明区别"，本套也确实逐条做了——但做成了"分散在各处"。**
同一条要求，两套文档的执行结果差别不在"有没有做"，而在**"读者能不能一次找到"**。
→ skill 里这条要加上**载体要求**（独立成节 + 常备表），不能只写"要点明区别"。

### §8.4 `qoder` 写错、本套写对的地方

对照时逐条回源码验证了 `qoder` 的具体论断。**大部分正确**
（`spi_slave_abort:2777`、`spi_target_abort:2788`、`spi_sync_locked:1308`、
`SPI_DELAY_UNIT_*:113-115`、`SPI_NBITS_*:1035-1037`、
`spi_controller_mem_caps:295-298`、`spi_controller_mem_ops:269-288`、
以及"`spi_async` 可在中断上下文调"），下面三条不对：

| # | `qoder` 写的 | 实际 | 性质 |
|---|-----------|-----|-----|
| 1 | `spi_alloc_host` 是 `spi_alloc_master` 的**新别名** | 不是别名，是两个独立的 `static inline`（`spi.h:786` / `:801`），互不引用 | 和本套初稿**同一个错**（见 §2.5.2）——**两套文档独立地犯了同一个错，说明这里是子系统本身的陷阱，不是谁的疏忽** |
| 2 | `spi_target_abort` 是 `spi_slave_abort` 的**新别名** | 两个各自 `EXPORT_SYMBOL_GPL` 的独立函数（`spi.c:2786` / `:2797`）；底下的**回调**才是 `union` 同址 | 同上；**查证这一条时才发现了命名兼容的第四种形式**（§2.5.2 形式 4） |
| 3 | 兼容宏表漏了 `spi_master_set_devdata` | `spi.h:1602-1603`（跨两行的 `#define`，单行 `grep` 容易漏） | **跨行 `#define` 是清单类表格的固定漏点**：`grep -n "define spi_master"` 拿到的是第一行，续行里的目标名字看不见 |

**第 1、2 条是本次对照最有价值的产出**：两套文档在完全独立的情况下
写出了同一句错话（"旧名是新名的别名"）。**独立复现的错误 ≠ 随机疏忽，
它标记的是源码本身有歧义的地方。**
→ skill 里要加一条：**凡是"X 是 Y 的别名"这种论断，必须打开定义看是
`#define` / inline / 字段 / `union` 哪一种**（四种形式见 §2.5.2）。

### §8.5 吸收 #2：给 `03` 的函数小节标题补行号范围

`qoder` 的 `03` 标题形如 `### A2.2 spi_register_controller()（:3099-3236）`。
好处很实在：**从目录就能跳到源码**，不用先进正文找行号。

本套的改法有两点不同：

1. **写全路径的基名**（`spi.c:3099-3236` 而不是裸 `:3099-3236`）——
   本项目全篇禁止裸 `:N`（§6.2 第 4 类）。
2. **范围要用命令取，不许目测**：

```bash
# 取某个函数的起止行（起 = 定义行，止 = 下一个顶层定义前的 })
awk '/^int spi_register_controller\(/{s=NR} s&&/^}/{print s"-"NR; exit}' \
    drivers/spi/spi.c
```

**这一条是有代价的**：标题里的行号是**全文档集里最容易过期的东西**
（函数一改动就漂）。所以只给 `03` Part A 的主流程函数加，
**不给 `01` 的 API 表加**——`01` 的既定规则就是"位置只到文件不到行"
（skill 第 10 条），两者不冲突：`01` 是"扫一眼找 API"，`03` 是"对着源码读"。

### §8.6 回写 skill：这一轮往 skill 里加了什么

对照的最后一步是**把本轮所有可复用的经验写回 skill**，否则下一个子系统会把同样的错
再犯一遍。改动落在 `~/.claude/skills/kernel-subsystem-docs/`：

| 落点 | 加了什么 | 来源 |
|-----|--------|-----|
| `SKILL.md` 骨架 `03` 条目 | Part A 标题带函数行号区间（`01` 反之不带） | §8.5 |
| `SKILL.md` 骨架 `04` 条目 | 否定性断言两道门；篇末加「全树使用统计」表 | §3.5 第 6 条、§8.3 第 3 条 |
| `SKILL.md` 骨架 `05` 条目 | 独立一节「近邻符号区分」（**载体要求**）；"符号存在"≠"读者能用" | §8.3 第 1 条、§3.6 第 8 条 |
| `SKILL.md` 第 17 条 | 从"跨文档一致性 grep 一遍"升级为**表格化对账轮**；补"引用有效但指错" | §6.2 |
| `SKILL.md` 第 19 条 | 欠债记号从 `—`/`可配` 扩到**含糊词**（一带/附近/起/20+） | §3.5 第 5 条 |
| `SKILL.md` 第 20 条 | 符号普查覆盖全部十篇；grep 的 tab 陷阱；导出+声明判据；"被引用" ≠ "调用者" | §3.6 第 8 条 |
| `SKILL.md` 新增第 24 条 | 改名线要显式换一条再走一遍；"X 是 Y 的别名"必须归到四种形式 | §2.5.2、§8.4 |
| `SKILL.md` 新增第 25 条 | 模板串味（外来文件名）只有 `check_line_refs.py` 的 UNRESOLVED 能抓 | §8.2 |
| `SKILL.md` 第 27 条 | 引用失效引用当例子 → **立刻重跑脚本**（本套犯了两遍）；`check_doc_coverage.sh` 前置 | §6.1 |
| `SKILL.md` 第 28 条 | AMBIGUOUS / UNRESOLVED 两类输出不许当噪声跳过 | §6.1 |
| `SKILL.md` 新增第 29 条 | 与另一套文档集对照的固定协议（先写完再读） | §8.1、§8.3 |
| `methodology.md` §A17 | 否定性断言两道门 + 依据栏必须真跑 + 全树统计表 | §3.5 |
| `methodology.md` §A18 | 四形式对照表 + 近邻符号六个选组来源 + kerneldoc≠可用 | §2.5.2、§3.6 |
| `methodology.md` 新增 §A22–§A25 | 填表比重读更能查错／改名线／独立对照协议／模板串味 | §6.2、§8 |
| `methodology.md` §B | 新增固定动作：举例引用坏引用 → 立刻重跑并登记（含归属判据） | §6.1 |
| `methodology.md` 新增 §H | 取证小配方：`awk` 取函数区间（含两个坑）、tab 陷阱、可用性判据 | §8.5 |
| `05-usage-template` | 「近邻符号区分」整节模板 + 六类选组来源表 + 两条判据 | §8.3 第 1 条 |
| `04-extensions-template` | 「全树使用统计」表模板（含 0 使用者也要给命令） | §8.3 第 3 条 |
| `03-internals-template` | 标题行号区间的写法 + `awk` 配方 + "只适用于 03" | §8.5 |
| `99-evidence-template` | 新增 §6.2（缺陷统计 + "靠什么手段发现的"）、§6.3（对照另一套文档集四张表） | §6.2、§8 |

一条**没有**写回 skill 的：§8.3 第 4 条（每个角色小节末尾各放一张 API 速查）。
它是取舍不是缺陷，理由留在 §8.3 表里，skill 不动——**否则下一轮会把它当成"漏了的要求"补上去**。
