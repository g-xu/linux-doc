---
title: 取证附录
---

# pwm 99：证据与自评

这一篇记录**这套文档是怎么来的、哪些地方没验证、哪些结论被推翻过**。
读前九篇时如果对某条结论存疑，先来这里查它的证据等级。

---

## §1 基线与范围

### §1.1 树的状态：pwm 是干净的

```bash
cd /workspace/safety/linux-6.1.y
git status --porcelain -- drivers/pwm include/linux/pwm.h \
    include/trace/events/pwm.h include/dt-bindings/pwm/pwm.h
# ⇒ 空输出
```

⇒ **本树（linux-6.1.158 分支，含大量 backport）没有碰过 pwm 的任何文件。**
这和 tty 不同（tty 有 14 个脏文件，全在串口驱动层），
所以本套文档描述的就是**上游 6.1.158 的原生 pwm**，
不需要像 tty 那样做"哪一层被改过"的区分。

### §1.2 基线数字（全部可重跑）

```bash
cd /workspace/safety/linux-6.1.y
# 规模
ls drivers/pwm/*.c | wc -l                          # 64
cat drivers/pwm/*.c | wc -l                         # 22949
wc -l drivers/pwm/core.c drivers/pwm/sysfs.c        # 1188 / 546 = 1734
wc -l include/linux/pwm.h include/trace/events/pwm.h \
      include/dt-bindings/pwm/pwm.h                 # 608 / 58 / 15
wc -l drivers/pwm/Kconfig                           # 678
wc -l Documentation/driver-api/pwm.rst              # 179
# 导出符号
grep -cE '^EXPORT_SYMBOL' drivers/pwm/core.c        # 17
grep -cE '^EXPORT_SYMBOL' drivers/pwm/sysfs.c       # 0
# DT 绑定
ls Documentation/devicetree/bindings/pwm/ | wc -l    # 51
ls Documentation/devicetree/bindings/pwm/*.yaml | wc -l   # 25
ls Documentation/devicetree/bindings/pwm/*.txt  | wc -l   # 26
```

### §1.3 子系统类型判定：两道门

```bash
grep -rn 'struct bus_type' drivers/pwm/ include/linux/pwm.h    # 无输出
ls include/uapi/linux/pwm*.h                                    # 不存在
```

⇒ **门 1 无 `bus_type` ⇒ 框架型**（provider/consumer，不是总线型）。
⇒ **门 2 无 uapi 头 ⇒ 没有 ioctl/结构体 ABI**，用户态面**只有 sysfs**。

这个判定决定了本套文档的骨架：没有"总线枚举/匹配"章节，
没有"uapi 结构体版本兼容"章节，用户态部分全部并进 sysfs 一节。

对比同批：
- watchdog / tty：框架型 **+ 有 uapi ABI** ⇒ 需要单独的 ioctl 章
- **pwm：框架型 + 无 uapi** ⇒ 用户态面最小的一个

### §1.4 官方文档登记

**`Documentation/driver-api/pwm.rst`，179 行，唯一一篇，已通读。**

DT 绑定侧另有两篇总纲：
- `Documentation/devicetree/bindings/pwm/pwm.yaml`（34 行，provider 侧）
- `Documentation/devicetree/bindings/pwm/pwm.txt`（60 行，consumer 侧，**未 yaml 化**）

⇒ 因为 consumer 侧总纲还是 `.txt`，**`pwms` 属性从来没有被
`make dtbs_check` 机械校验过**（06 §0.2）。这是 06 那一篇整体基调的来源。

### §1.5 官方文档与代码的六处不一致

全部逐条核对过，是 03 的 C 组的主要内容：

| # | rst 说的 | 代码实际 | 位置 |
|---|---|---|---|
| 1 | 驱动应实现 `->enable()` / `->disable()` / `->config()` | ⚠️ **这三个回调在 6.1 里根本不存在**，`pwm_ops` 只有 6 个成员 | `pwm.rst:153-157` vs `include/linux/pwm.h:272-282` |
| 2 | sysfs 有 4 个通道属性 | 实际 **5 个**，少列了 `capture` | `pwm.rst:110-129` vs `drivers/pwm/sysfs.c:224-231` |
| 3 | Locking 一节说"should be fixed soon" | 6.1 里还是老样子；`pwm_apply_state()` 依然无锁 | `pwm.rst:165-172` |
| 4 | "no way for consumers to get the actually implemented settings" | ✅ **这条是准确的自陈**，代码确实如此 | `pwm.rst:68-72` |
| 5 | `pwmchip_remove()` kerneldoc 说返回错误码 | ⚠️ 原型是 `void` | `drivers/pwm/core.c:318-321` vs `:323` |
| 6 | `.get_state` 的 kerneldoc 描述的调用时机 | 与实际调用点不符（只在 request 和 `PWM_DEBUG` 下调） | `include/linux/pwm.h` 的 `pwm_ops` 注释 |

⇒ **第 1 条最危险**：照 rst 写驱动的人会实现三个永远不被调用的回调。

---

## §2 阅读范围与证据等级

### §2.1 一等证据：逐行读过

| 文件 | 行数 | 说明 |
|---|---|---|
| `drivers/pwm/core.c` | 1188 | **全文逐行** |
| `drivers/pwm/sysfs.c` | 546 | **全文逐行** |
| `include/linux/pwm.h` | 608 | **全文逐行** |
| `include/trace/events/pwm.h` | 58 | **全文** |
| `include/dt-bindings/pwm/pwm.h` | 15 | **全文** |
| `drivers/pwm/Kconfig` | 1-45 | 核心三项，剩下 633 行是逐驱动条目，只做了统计 |
| `Documentation/driver-api/pwm.rst` | 179 | **全文** |
| `Documentation/devicetree/bindings/pwm/pwm.yaml` | 34 | **全文** |
| `Documentation/devicetree/bindings/pwm/pwm.txt` | 60 | **全文** |

⇒ **核心 1734 行 + 头文件 681 行 + 文档 273 行，全部一等证据。**
这是本套文档能给出精确行号的基础。

### §2.2 二等证据：读了关键函数，没读全文

| 文件 | 读了什么 | 用在哪 |
|---|---|---|
| `drivers/video/backlight/pwm_bl.c`（707 行） | probe / update_status / DT 解析 | 04 §3.1、05 §2.1 的样板 |
| `drivers/regulator/pwm-regulator.c`（409 行） | `pwm_adjust_config()` 调用点、两种模式 | 04 §3.3、05 §2.2 |
| `drivers/hwmon/pwm-fan.c`（682 行） | `MAX_PWM` 换算、thermal 接入 | 04 §3.4、06 §5.2 |
| `drivers/clk/clk-pwm.c`（158 行） | **`:78-115` 逐行**（四道校验门） | 06 §5.4 |
| `drivers/pwm/pwm-clk.c`（148 行） | 注册段 | 04 §4.1 |
| `drivers/leds/leds-pwm.c`（217 行） | state 缓存方式 | 04 §3.2 |
| `drivers/pwm/pwm-cros-ec.c` | `:250-315`（自定义 xlate） | 06 §1.4 |
| `drivers/pwm/pwm-clps711x.c` | `:60-115`（自定义 xlate） | 06 §1.4 |
| `drivers/pwm/pwm-pxa.c` | `:190-215`（`of_pwm_single_xlate`） | 06 §1.4 |
| `drivers/gpu/drm/bridge/ti-sn65dsi86.c` | `:1615-1640` | 06 §1.4 |
| `drivers/of/property.c` | `:464-486`（`of_property_match_string` 的返回值） | 06 §2.2 的 `-EINVAL` 歧义 |

### §2.3 三等证据：只做了符号级统计

- **62 个控制器驱动**（`drivers/pwm/pwm-*.c`）：只统计了
  `pwmchip_add` / `devm_pwmchip_add` / `.capture` / `.get_state` / `of_xlate`
  的出现位置，**没有逐个读实现**。
- **35 个核心外消费点**：只做了 `#include <linux/pwm.h>` 的文件级普查
  加上 API 调用点定位。
- **arm64 DTS**：只做了属性和 compatible 的 grep 计数（§6 表 9），
  **没有逐个板子核实电路正确性**。

⚠️ 凡是文档里写"全树只有 N 个 / 一共 N 处"的地方，
都是三等证据的 grep 计数 ⇒ **口径写在 §6 的表里，可以重跑**。

### §2.4 代表实现的选法

选了 8 个消费者展开（04 §3），标准是：

1. **覆盖不同的使用形态**：亮度比例（backlight）· 电压映射（regulator）·
   转速+thermal（fan）· 反向变时钟（clk-pwm）· 时序发波（beeper/vibra/ir-tx）·
   当并行总线时钟（ts-nbus）
2. **arm64 DTS 里真的有人用**（06 §0.1 的 compatible 分布表）
3. **代码量在 700 行以内**，能在文档里讲完

⚠️ **没选**的：i915 背光（x86 专用）· `pwm-pca9685`（I²C expander，
唯一读 `usage_power` 的驱动，只在 04 §5.2 点了一句）。

### §2.5 裁掉的范围登记

| 裁掉的 | 理由 |
|---|---|
| `drivers/pwm/pwm-*.c` 的 62 个驱动实现细节 | 本套是"子系统层"文档，逐驱动等于 62 篇 |
| x86 专用 provider（`pwm-lpss` / `pwm-crc` / i915 背光） | arm64-only |
| ACPI 路径 | 见 §2.7 |
| 静态查表路径（`pwm_add_table()`）的深入 | arm64 上无 board file，只在 01 §5 和 05 §5 登记"要认得" |
| `PWM_POLARITY_INVERSED` 之外的 DT flags | 6.1 里只定义了 1 位 |

### §2.6 与相邻层的边界

| 相邻 | 怎么划 |
|---|---|
| pinctrl | 只说"引脚没复用 PWM 就没波形"这一个交互点（06 §1.1、07 §6），不展开 pinctrl |
| clk | 只说 provider 驱动要取时钟；`clk-pwm` / `pwm-clk` 两个桥单独讲（04 §4.1、06 §6.2） |
| regulator | 只讲 `pwm-regulator` 这个消费者，不讲 regulator 框架 |
| thermal | 只讲 `pwm-fan` 挂进 thermal 的接口点 |
| backlight | 只讲 `pwm_bl.c` 作为消费者样板 |
| driver core | `device_link_add()` 的 PM 顺序影响单列（03 §B5），不展开 device link |

### §2.7 ACPI 的裁剪登记

⚠️ **pwm 核心里真的有一条 ACPI 路径**，这一点和 tty（ACPI 零命中）不同，
必须单独交代：

```bash
grep -n 'acpi' drivers/pwm/core.c
# 818: static struct pwm_device *acpi_pwm_get(const struct fwnode_handle *fwnode)
# 917-921: is_acpi_node(fwnode) 分支
```

- `acpi_pwm_get()`（`drivers/pwm/core.c:818`）在
  `fwnode_pwm_get()` 里被 `is_acpi_node(fwnode)` 分支调用
  （`core.c:917-921`）。
- 它走的是 ACPI 的 `PWM` resource（`acpi_pwm_get` 内部用
  `acpi_dev_get_property` 那一套）。
- **arm64 服务器/嵌入式平台上没有任何 PWM 控制器通过 ACPI 描述**：
  ACPI 的 PWM 绑定只在 Intel LPSS（x86 平板）上使用。

⇒ **裁剪决定**：本套文档在 `pwm_get()` 的三条发现路径里
**只展开 DT 路径和静态查表路径**，ACPI 分支在 03 §A3 的路径图上
标注为"本文档集裁掉，见 99 §2.7"，不展开。

⚠️ **这不是"这条路径不存在"**。如果读者的平台是 x86 或
用了 ACPI-on-arm64 描述 PWM（理论上可行，实际未见），
那么 03 §A3 的路径图对他不完整。

---

## §3 符号存在性核查

### §3.1 核查口径（两级，全套统一）

| 级别 | 判据 | 命令 |
|---|---|---|
| **"存在"** | grep 命中定义 | 函数：`grep -nE '^[a-z_ ]*\bNAME\(' file.c`；宏：⚠️ **`grep -nE 'define[[:space:]]+FOO'`**（`[[:space:]]` 而不是空格——内核里大量宏定义用 TAB 分隔） |
| **"外部驱动可用"** | `EXPORT_SYMBOL*` **且** 在 `include/linux/` 头文件里有声明 | `grep -n 'EXPORT_SYMBOL.*NAME' core.c` + `grep -n 'NAME' include/linux/pwm.h` |

⚠️ **两级必须都查**。反例：`pwmchip_sysfs_export()` 在
`include/linux/pwm.h` 里有原型但**没有 `EXPORT_SYMBOL`**
⇒ 它只能被 pwm 核心自己调用，外部模块链接会失败。

### §3.2 抓到的三个计数口径问题

**1. 用 API 名 grep 消费者会假阳性。**
`grep -rln 'pwm_get' drivers/` 会命中一堆自己定义了
`pwm_get_*` sysfs 属性的驱动（如 hwmon 的 `pwm1` 属性）。
⇒ **正确的消费者普查门是 `#include <linux/pwm.h>`**：

```bash
grep -rln '#include <linux/pwm.h>' drivers/ --include='*.c' | grep -v '^drivers/pwm/'
```

**2. `of_` 前缀的 grep 会命中 of 框架自己。**
统计 `of_pwm_*` 时要限定 `drivers/pwm/`，
否则 `drivers/of/` 里的通用代码会混进来。

**3. 两个 `pwm.h` / 两个 `pwm.yaml`。**
⚠️ **本套文档禁止写裸 `pwm.h:NNN`**：

| 基名 | 两个（或更多）实体 |
|---|---|
| `pwm.h` | `include/linux/pwm.h`（608 行）· `include/trace/events/pwm.h`（58 行）· `include/dt-bindings/pwm/pwm.h`（15 行） |
| `pwm.yaml` | `Documentation/devicetree/bindings/pwm/pwm.yaml`（34 行，provider 总纲） |
| `pwm.txt` | `Documentation/devicetree/bindings/pwm/pwm.txt`（60 行，consumer 总纲） |
| 任意 DTS 基名 | `exynos5433-tm2-common.dtsi` 等在多个 vendor 目录下可能重名 |

⇒ 全套统一用**完整路径**。`check_line_refs.py` 的消歧表
（38 个基名 / 40 个前缀目录）就是为此建的。

### §3.3 计数纪律（本套遵守的四条）

1. **结构体字段数用花括号深度数**，不用 `grep -c ';'`。
   pwm 的 7 个结构都没有 `__randomize_layout`，所以末行都是 `};`
   ——但纪律还是按 tty 那次踩坑后的口径执行。
2. **数字后面永远跟得出命令**（§6 全表）。
3. **"全树只有 N 个"必须给 grep 命令**，因为读者的树版本可能不同。
4. **行号必须 grep 出来，不能凭记忆写**（§5.3 的第 1 条教训）。

---

## §4 索引工具（cbm）的盲区

本套文档主要靠 `grep` / `awk` 直读源码，`cbm` 图索引只用来做初步定位。
在 pwm 上遇到的具体盲区：

| 盲区 | 具体表现 |
|---|---|
| **宏定义不在图里** | `PWM_POLARITY_INVERTED`（`include/dt-bindings/pwm/pwm.h:13`）搜不到 |
| **间接调用不在图里** | `chip->ops->apply()` / `chip->of_xlate()` 的实际目标全部缺失 ⇒ **62 个驱动的 `.apply` 一个都连不上核心** |
| **inline 函数覆盖不全** | `include/linux/pwm.h` 里 20 多个 inline 辅助（`pwm_get_state` / `pwm_init_state` / `pwm_set_period` …）在图里的入边普遍偏低 |
| **tracepoint 宏展开** | `trace_pwm_apply()` / `trace_pwm_get()` 是 `DEFINE_EVENT` 生成的，图里没有 |
| **DTS 不在图里** | 所有 arm64 DTS 统计只能靠 grep |

⇒ **本套所有涉及"回调实际指向谁""某个宏在哪定义""某个符号有几个用户"
的结论，都是 grep 结论，不是图结论。**

---

## §5 未验证项（四层）

### §5.1 第一层：未编译、未运行

⚠️ **本套文档的所有代码骨架（05 的五个角色）都没有编译过，
所有 shell 命令都没有在真机上跑过。**

- 05 §1.1 的 provider 骨架、§2.1 的消费者骨架：按 `pwm_bl.c` 的结构
  改写，**语法未验证**。
- 07 §2.5 的巡检脚本、§4.5 的调试序列、§3.5 的 trace 命令：
  路径和属性名来自源码，**但没有在有 PWM 硬件的板子上执行过**。
- 06 的 DTS 片段：抄自树内真实节点（给了路径行号），
  但组合出来的示例**没有过 `dtc`**。

### §5.2 第二层：只读了符号或完全没读

- 62 个控制器驱动的 `.apply` 实现（§2.3）
- `drivers/pwm/Kconfig` 的 633 行驱动条目
- 26 个 `.txt` 绑定文件的内容（只统计了数量和文件名）
- `pwm-pca9685.c` 的 `usage_power` 读取逻辑（只知道它读了）

### §5.3 第三层：推断而非观察

这一层最需要读者警惕。逐条列出：

| 推断 | 依据 | 为什么没法观察 |
|---|---|---|
| **`core.c:763` 的 `pr_err("PWM chip not found")` 是死代码** | `fwnode_to_pwmchip()` 唯一的失败返回是 `-EPROBE_DEFER`（`core.c:688`），而 `:762` 的条件是 `PTR_ERR(pc) != -EPROBE_DEFER` | 需要构造一个能让 `fwnode_to_pwmchip()` 返回其他错误的场景，代码上做不到 |
| **unbind provider 会 UAF** | `pwmchip_remove()` 不检查 `PWMF_REQUESTED` 就 `kfree(chip->pwms)`（03 §B3） | 需要真机 + KASAN 才能确认崩溃形态 |
| **resume 后第一次 apply 会被短路吃掉** | 五字段短路（`core.c:566-571`）+ `pwm->state` 在 suspend 时不清零 | 需要真机做 suspend/resume |
| **`pwm-regulator` 缺 `.get_state` 会掉压** | `pwm_adjust_config()` 的 `period == 0` 分支（`core.c:642-647`）会把 duty 算成 0 | 需要真机 + 示波器/万用表 |
| **`CONFIG_PWM_DEBUG` 会破坏 `pwm-ir-tx`** | 它在每次 apply 后又 apply 一次（`core.c:516`），红外码依赖精确时序 | 需要真机 + 红外接收 |
| **`#pwm-cells = <1>` 的两种语义会互相打架** | `of_pwm_single_xlate()` 把第一个 cell 当 period（`core.c:171`），`cros_ec_pwm_xlate` 当 channel | 需要把一个板子的 DTS 改错再跑 |
| **debugfs 挂死可以用来诊断 provider 回调卡死** | `pwm_seq_start/stop` 持 `pwm_lock` 跨整个遍历（`core.c:1139` / `:1154`） | 需要构造一个会卡的 `.request` |

⇒ **这 7 条是"代码上必然如此"的推断，不是观测结果。**
如果读者在真机上看到不同现象，以真机为准。

### §5.4 第四层：版本时效性

⚠️ **上游 6.2+ 已经动了本文档描述的几处核心机制**：

| 6.1 的现状 | 上游后续 |
|---|---|
| `chip->base` + `allocated_pwms` 全局位图 | **已移除**，`pwmchipN` 的编号规则变了 |
| `pwmchip_remove()` 返回 `void` 且不检查占用 | 做过调整 |
| `pwm_apply_state()` 无锁 | 后续引入了 per-chip 锁 |
| `pwm_apply_state()` 的名字 | 后续拆成 `pwm_apply_might_sleep()` / `pwm_apply_atomic()` |

⇒ **本套文档只对 6.1.158 成立。** 移植到新内核时，
上面每一条都要重新核对。00 §8 也声明了这一点。

---

## §6 可重跑的数字

### 表 1 规模

| 项 | 值 | 命令 |
|---|---|---|
| `drivers/pwm/` `.c` 数 | 64 | `ls drivers/pwm/*.c \| wc -l` |
| `drivers/pwm/` 总行 | 22949 | `cat drivers/pwm/*.c \| wc -l` |
| `core.c` | 1188 | `wc -l drivers/pwm/core.c` |
| `sysfs.c` | 546 | `wc -l drivers/pwm/sysfs.c` |
| 核心合计 | 1734 | 上二者之和 |
| `include/linux/pwm.h` | 608 | `wc -l include/linux/pwm.h` |
| `include/trace/events/pwm.h` | 58 | `wc -l include/trace/events/pwm.h` |
| `include/dt-bindings/pwm/pwm.h` | 15 | `wc -l include/dt-bindings/pwm/pwm.h` |
| `drivers/pwm/Kconfig` | 678 | `wc -l drivers/pwm/Kconfig` |
| `pwm.rst` | 179 | `wc -l Documentation/driver-api/pwm.rst` |

### 表 2 结构体字段数（花括号深度口径）

| 结构 | 位置 | 字段数 |
|---|---|---|
| `struct pwm_args` | `include/linux/pwm.h:38-41` | 2 |
| `struct pwm_state` | `include/linux/pwm.h:59-65` | 5 |
| `struct pwm_device` | `include/linux/pwm.h:79-90` | 9 |
| `struct pwm_capture` | `include/linux/pwm.h:256-259` | 2 |
| `struct pwm_ops` | `include/linux/pwm.h:272-282` | 6（含 `owner`） |
| `struct pwm_chip` | `include/linux/pwm.h:295-308` | 8 |
| `struct pwm_lookup` | `include/linux/pwm.h:555-564` | 8 |
| `struct pwm_export` | `drivers/pwm/sysfs.c:17-22` | 4 |

⚠️ pwm 的结构都以 `};` 结尾（无 `__randomize_layout`），
所以 tty 那次的踩坑（`} __randomize_layout;` 导致数多了一倍）
在这里不会重现——但口径仍按花括号深度执行。

### 表 3 导出符号：17 个

```bash
grep -nE '^EXPORT_SYMBOL' drivers/pwm/core.c    # 17 行，全部 _GPL
grep -cE '^EXPORT_SYMBOL' drivers/pwm/sysfs.c   # 0
```

⚠️ `sysfs.c` 零导出，但它的两个函数
（`pwmchip_sysfs_export()` `drivers/pwm/sysfs.c:505` /
`pwmchip_sysfs_unexport()` `:521`）在
`include/linux/pwm.h` 里有 `CONFIG_PWM_SYSFS` 条件原型，
**被核心直接调用** ⇒ 属于"有声明但不可被外部模块使用"。

### 表 4 用户态 ABI

| 项 | 值 |
|---|---|
| uapi 头 | **无** |
| ioctl | **0 个** |
| char device | **无** |
| sysfs class | `/sys/class/pwm/`（`drivers/pwm/sysfs.c:493`，`class_register` 在 `:544`） |
| chip 级属性 | **3 个**：`npwm`（RO）· `export`（WO）· `unexport`（WO） |
| 通道级属性 | **5 个**：`period` `duty_cycle` `enable` `polarity`（RW）+ `capture`（RO） |
| ⚠️ rst 少列的 | `capture`（`pwm.rst:110-129` 只列 4 个） |

### 表 5 可观测面

| 面 | 数量 | 位置 |
|---|---|---|
| tracepoint | **2**（`pwm_apply` / `pwm_get`） | `include/trace/events/pwm.h:39` / `:47`；同一个 `DECLARE_EVENT_CLASS` `:11` |
| tracepoint 触发点 | **5** | `drivers/pwm/core.c:112` `:461` `:523` `:526` `:577` |
| debugfs 文件 | **1** | `drivers/pwm/core.c:1182`（`subsys_initcall` 在 `:1187`） |
| Kconfig 调试开关 | **1** | `PWM_DEBUG`，`drivers/pwm/Kconfig:36-43` |
| `dev_dbg` | **0** ⇒ dynamic_debug 无效 | — |

### 表 6 错误码普查（`core.c` + `sysfs.c`）

| 错误码 | 次数 | 备注 |
|---|---|---|
| `-EINVAL` | **15** | 最主要的错误码；也是 DT 各种失败的统一出口 ⇒ **歧义的根源** |
| `-ENODEV` | 7 | 含 `sysfs.c:323-324` 的"通道号超界" |
| `-EPROBE_DEFER` | 4 | `fwnode_to_pwmchip()` 的唯一失败返回在 `core.c:688` |
| `-ENOMEM` | 2 | |
| `-EBUSY` | 2 | ⚠️ 两处语义不同却同码（`sysfs.c:326` 间接 / `:248`） |
| `-EPROTO` | 1 | |
| **`-ENOSYS`** | **1** | ⚠️ `core.c:608`，**全树唯一这样用**，会漏到用户态成 `Function not implemented` |
| `-ENOSPC` | 1 | `alloc_pwms()` 位图满 |
| `-ENOENT` | 1 | |

### 表 7 打印与设计债

| 项 | 值 |
|---|---|
| 核心日志总数 | **15**（`core.c` 14 + `sysfs.c` 1） |
| 其中锁在 `CONFIG_PWM_DEBUG` 里 | **8**（`core.c:244` `:479` `:485` `:490` `:499` `:506` `:512` 的 warn + `:519` `:533` 的 err，共 9 处中 `:244` 也属 DEBUG） |
| 死代码日志 | **1**（`core.c:763`） |
| 不带设备名的 | **1**（`core.c:1023` 的 `pr_warn`） |
| `dev_dbg` | **0** |
| `FIXME/TODO/XXX` | **0 处**（`core.c` / `sysfs.c` / `include/linux/pwm.h`） |
| ⚠️ 但文档里有自陈设计债 | `pwm.rst:165-172`（Locking，"should be fixed soon"）· `:68-72`（拿不到硬件值） |

⇒ **pwm 的设计债登记在文档里而不是代码注释里**，
所以只 grep `FIXME` 会得出"这个子系统没有已知问题"的错误结论。

### 表 8 全树实例统计

```bash
grep -rn 'pwmchip_add'      drivers/ | wc -l          # 44
grep -rn 'devm_pwmchip_add' drivers/ | wc -l          # 32
grep -rln '#include <linux/pwm.h>' drivers/ --include='*.c' \
    | grep -v '^drivers/pwm/' | wc -l                 # 35（消费点文件数）
grep -rn '\.capture' drivers/pwm/pwm-*.c              # 2（sti / stm32）
grep -rn 'usage_power' drivers/ --include='*.c'        # provider 读 1（pca9685），consumer 写 0
grep -rn 'of_xlate' drivers/ --include='*.c' | grep -v drivers/pwm/core.c   # 4 个非默认
```

### 表 9 arm64 与设备树

```bash
cd /workspace/safety/linux-6.1.y/arch/arm64/boot/dts
grep -rl 'pwms *='      . | wc -l    # 112
grep -rl '#pwm-cells'   . | wc -l    # 71
grep -rl 'pwm-names'    . | wc -l    # 4  ⚠️ 只有 4 个
grep -rho '#pwm-cells = <[0-9]>' . | sort | uniq -c
#   132  #pwm-cells = <3>
#   119  #pwm-cells = <2>
#     8  #pwm-cells = <1>      ⚠️ 全部是 google,cros-ec-pwm
grep -rl 'PWM_POLARITY_INVERTED' . | wc -l   # 5，全是背光
```

consumer compatible 分布（同一命令的分类统计）：
`pwm-backlight` 36 · `pwm-regulator` 23 · `pwm-clock` 22 · `pwm-fan` 9 ·
`pwm-leds` 4 · `pwm-beeper` 3 · `pwm-vibrator` 2 · `pwm-ir-tx` 2 ·
`maxim,max77843-haptic` 1。

---

## §7 三个自检脚本的结果

```bash
cd /workspace/safety/linux-6.1.y
S=~/.claude/skills/kernel-subsystem-docs/scripts
python3 $S/check_xrefs.py     output/pwm
python3 $S/check_line_refs.py output/pwm .
bash    $S/check_doc_coverage.sh pwm output/pwm .
```

| 脚本 | 查什么 | 结果 |
|---|---|---|
| `check_xrefs.py` | 跨篇引用的篇目和章节号是否真的存在 | ✅ 全绿（10 篇，228 条跨篇引用全部命中） |
| `check_line_refs.py` | 每条 `file:line` 的文件是否存在、行号是否越界 | ✅ 全绿（**309 条唯一引用，0 失效**） |
| `check_doc_coverage.sh` | 10 篇是否齐、必需章节是否缺 | ✅ 全绿 |

⚠️ **`check_line_refs.py` 有一个它查不出来的类别**：
**行号在文件范围内但指向了错的地方**。
本套在 05 §7 就踩过这个坑（7 个函数定义行号全写错，全部在范围内 ⇒ 脚本全绿）。
⇒ 见 §9 第 1 条和 §10.2 第 5 条。

⚠️ 写作过程中脚本报错两次，都是**引用格式**问题而不是内容错：

1. `06-devicetree.md` 里写了裸 DTS 基名
   `exynos5433-tm2-common.dtsi:928`，消歧表解析不到
   ⇒ 改成完整路径 `arch/arm64/boot/dts/exynos/exynos5433-tm2-common.dtsi:928`。
2. 脚本以 `FileNotFoundError: 'output/pwm'` 失败，原因是上一次 Bash 调用
   `cd` 进了 `output/pwm` 目录，相对路径失效
   ⇒ 所有脚本调用统一加 `cd /workspace/safety/linux-6.1.y &&` 前缀。

---

## §8 逐篇取证表

| 篇 | 行数 | 一等证据来源 | 主要三等证据（grep 统计） |
|---|---|---|---|
| 00 | 185 | 无新证据，纯浓缩 01–08 | — |
| 01 | 760 | `core.c` 全文 · `include/linux/pwm.h` 全文 · `pwm.rst` 全文 | 17 个导出符号、错误码普查 |
| 02 | 683 | `include/linux/pwm.h` 全文 · `sysfs.c:17-22` | 结构体字段数（花括号深度） |
| 03 | 1135 | `core.c` 全文逐行（注册/注销/发现/apply/capture/put/sysfs/PM 全路径） | `pwm_lock` 覆盖范围、`MAX_PWMS` |
| 04 | 602 | `sysfs.c` 全文 · 8 个消费者的关键函数（§2.2） | 消费者分布表、`.capture` 2 个、`usage_power` 1 读 0 写 |
| 05 | 795 | 同 01 + `pwm_bl.c` / `pwm-regulator.c` / `pwm-fan.c` | 符号存在性核实表（§3.1 两级口径） |
| 06 | 642 | `core.c:124-198` + `:737-799` 逐行 · `pwm.yaml` + `pwm.txt` 全文 · `of/property.c:464-486` · 4 个自定义 xlate 驱动 | 表 9 的全部 arm64 DTS 统计 |
| 07 | 519 | `core.c:440-539` + `:546-589` + `:1106-1188` 逐行 · `include/trace/events/pwm.h` 全文 · `Kconfig:1-45` · `sysfs.c` 全文 | 15 条日志普查 |
| 08 | 353 | 无新证据，重讲 01–07 | — |
| 99 | 本篇 | — | — |

**合计 5674 行 + 本篇。**

---

## §9 交叉验证：初始假设被推翻的六条

这一节是本套文档最该被信任的部分——它记录了**我先想错、后来被代码纠正**的地方。

### 1. 05 §7 的七个函数定义行号全写错

**初始**：凭记忆写了 `pwm_request` / `pwm_request_from_chip` /
`pwm_apply_state` / `pwm_capture` / `pwm_get` / `devm_pwm_get` /
`devm_fwnode_pwm_get` 七个函数的定义行号。

**推翻**：逐条 grep 后全部不对。正确值：
`drivers/pwm/core.c:369` `:406` `:546` `:599` `:900` `:1055` `:1084`。

⚠️ **`check_line_refs.py` 全绿地放过了这七条**，因为错的行号都在
`core.c` 的 1188 行范围内。
⇒ **这类错误只能靠"每个行号都 grep 出来"预防，脚本查不出。**

顺带做了一次全文一致性统一：**定义行 vs 导出行分开写**，
例如 `pwm_apply_state` 写成"`core.c:546`（导出在 `:589`）"。

### 2. `pwm-cros-ec.c` 的 `of_pwm_n_cells = 1` 不是死代码

**初始假设**：`of_pwmchip_add()`（`core.c:181-198`）只在
`chip->of_xlate == NULL` 时（`:186`）才填 `of_xlate` 和 `of_pwm_n_cells`
的默认值 ⇒ 那么驱动自己设 `of_pwm_n_cells` 就没人读了？
如果成立，**所有 cros-ec Chromebook 的 `pwm_get()` 都会失败**——
这是个大结论，所以先去核实。

**推翻**：读 `drivers/pwm/pwm-cros-ec.c:305-306` 发现它
**同时设了 `chip->of_xlate = cros_ec_pwm_xlate`**，
所以 `:186` 的分支不进，两个赋值都是驱动自己的，都生效。

⇒ 改写成一个更窄但真实的陷阱：**只有"设了 `of_xlate` 却忘了
`of_pwm_n_cells`"的驱动会坏**——那时 `of_pwm_n_cells` 是 0，
`of_pwm_xlate_with_flags()` 的 `:129-130` 直接 `-EINVAL`，
而且**一条日志都不打**（06 §1.3）。

### 3. `drivers/clk/clk-pwm.c` 确实做了交叉校验

**初始**：06 §5.4 写"`clk-pwm.c` 不校验 `clock-frequency` 与 PWM 周期是否自洽"。

**推翻**：spot-check grep 发现 `drivers/clk/clk-pwm.c:99-104`
**正是在做这件事**，不匹配就
`dev_err("clock-frequency does not match PWM period")` + `-EINVAL`。

⇒ 改成一个 4 行的门表（`:86-89` period 非 0 · `:91-92` `clock-frequency`
可选、可反推 · `:94-97` `fixed_rate` 非 0 · `:99-104` 交叉校验），
并补上"为什么树内 DTS 把 32768 Hz 配 30518 ns"的算术解释。

⚠️ **教训：否定性断言（"X 不校验 Y"）要和肯定性断言一样严格地验证。**

### 4. `core.c:763` 是死代码

**初始**：把 `pr_err("PWM chip not found")` 当成一条正常的诊断消息写进 06 §7。

**修正**：追 `fwnode_to_pwmchip()` 发现它**唯一的失败返回**是
`-EPROBE_DEFER`（`drivers/pwm/core.c:688`），
而 `:762` 的守卫条件是 `if (PTR_ERR(pc) != -EPROBE_DEFER)`
⇒ **条件永假，这个字符串永远不会出现在 dmesg 里**。

⇒ 06 §2.4 和 07 §5 都改成明确标注"死代码"。
这也是 07 §5 那张日志表里"15 条中 1 条永不触发"的来源。

### 5. `#pwm-cells = <1>` 的第一个 cell 是周期，不是通道号

**初始直觉**：cells 数减少就是"省掉后面的可选参数"，
所以 `<1>` 应该是"只给通道号"。

**推翻**：`of_pwm_single_xlate()`（`drivers/pwm/core.c:155-178`）
在 `:167` **把通道硬编码成 0**，在 `:171` 把
**`args[0]` 赋给 `args.period`** ⇒ 单 cell 是**周期**。

⇒ 而 `cros_ec_pwm_xlate`（`drivers/pwm/pwm-cros-ec.c:221`）的单 cell
**是通道号**。
⇒ **`#pwm-cells = <1>` 在树里有两种互不兼容的含义**，
只能看 provider 装了哪个 xlate（06 §1.4）。这是 06 最有价值的一条。

### 6. tracepoint `pwm_get` 与函数 `pwm_get()` 无关

**初始**：把 `events/pwm/pwm_get` 当成"追踪谁申请了通道"的手段写进 07 草稿。

**推翻**：`trace_pwm_get()` 的 5 个触发点
（`drivers/pwm/core.c:112` `:461` `:526`）全部紧跟在
`.get_state` 调用之后 ⇒ 它的语义是"**刚从硬件读了一次状态**"。

⇒ 07 §3.2 单列了这个命名坑，08 §3.3 把它和 `pwm_get_state()`
放在一起做三方对比。

---

## §10 对照与回写

### §10.1 rule-29 独立对照：本子系统没有对照套

```bash
ls /workspace/safety/linux-6.1.y/output/qoder/
```

⇒ `output/qoder/` 下**没有 `pwm/` 目录**。
本批次里 regmap / phy / watchdog / tty 也都没有对照套，
只有第一批的 pinctrl / gpio / clk / dma 有。

⇒ **rule-29 的"独立对照"这一步在 pwm 上无法执行**，登记为
「无对照套」。替代措施：§9 的六条自我推翻记录，
以及 §5.3 那张"推断而非观察"的清单——这两节承担了本来由对照套
提供的"找出我哪里想错了"的功能。

### §10.2 要回写 skill 的八条

前四条是 pwm 特有的新发现，后四条是对已有条目的加强。

**1. 否定性断言要和肯定性断言一样严格地验证。**
"驱动 X 不校验 Y""这个路径没有日志""这个字段没人用"——
这类句子读者无法自己反驳，写错的代价比写错一个行号大。
⇒ 每条否定性断言都要留一条能重跑的 grep（§9 第 3 条的教训）。

**2. 永远不要从未编号的 `sed -n 'A,Bp'` 输出里数相对行号。**
源码里的空行在显示时不带编号，数一遍就会整体偏移
（06 第一稿偏了 1–4 行，约 20 处）。
⇒ **一律用 `awk 'NR>=A&&NR<=B{printf "%d:%s\n",NR,$0}'`**，
让每行自带真实行号。本套从 06 第二稿起全部改用这个形式。

**3. `check_line_refs.py` 查不出"在范围内但指错地方"的行号。**
⇒ **函数定义行号必须逐个 grep，不能凭记忆写。**
脚本全绿 ≠ 行号正确（§9 第 1 条）。

**4. 一个基名对应多个文件时，全套禁止写裸基名。**
pwm 有三个 `pwm.h`、一个 `pwm.yaml` 加一个 `pwm.txt`，
DTS 基名也可能跨 vendor 目录重名。
⇒ 消歧表能兜住一部分，但**写作时就用完整路径**成本更低。

**5. 消费者普查的门是 `#include <header>`，不是 API 名。**
`grep -rln 'pwm_get' drivers/` 会命中一堆自己定义 `pwm*` sysfs
属性名的 hwmon/backlight 驱动。
⇒ 先用 include 过滤出候选文件，再在这些文件里找 API 调用点（§3.2）。

**6. 宏定义用 `grep -nE 'define[[:space:]]+FOO'`。**
内核里大量宏用 TAB 分隔，写成 `'define +FOO'` 会漏。

**7. 结构体字段数用花括号深度，并确认末行形态。**
pwm 全部是 `};`，但 tty 的 `} __randomize_layout;` 那次
让计数多了一倍。口径不变。

**8. 99 最后写会和前面已引用的章节号冲突。**
`04-extensions.md:40` / `:209` 和 `03-internals.md:195`
在 99 存在之前就引用了 `99 §2.7`。
⇒ **99 的章节编号在写 01 之前就要定死**（至少 §2.7 ACPI、
§10.1 rule-29、§10.2 回写这三个位置），否则要么回改前面，
要么 99 的结构被前面绑住。本套是提前定死的，所以只在
99 写完前有 3 条 `check_xrefs` 失效，写完即消。

### §10.3 明确不吸收的两条

**1. 不给"重要度评级"。**
实心五角星那类分级标记不携带信息、会指数膨胀、渲染出来是一片 `***`。
重要度靠**章节顺序和标题层级**表达。本套 10 篇的五角星计数恒为 0
（用 `grep -c` 核过）。

**2. 不为了凑齐"每篇都有 N 个陷阱"而造陷阱。**
`⚠️` 只标真陷阱。pwm 的真陷阱本来就多（沉默失败是它的结构性问题，
见 08 §5），不需要凑。反过来，04 §5 那种"这个能力几乎没人用"
的诚实结论比硬凑一个陷阱有用。

---

## §11 一句话自评

**这套文档最有价值的部分不是 API 清单，而是三张表**：
06 §7 的 12 行 DT 排错表（标出了 9 个 dmesg 里一个字都没有的失败）、
07 §6 的症状决策表、08 §5 的"沉默失败模式"总表。

**最不可靠的部分**是 05 里的代码骨架（未编译）
和 07 里的 shell 序列（未在真机跑过），
以及 §5.3 那七条"代码上必然如此、但没观察过"的推断。
