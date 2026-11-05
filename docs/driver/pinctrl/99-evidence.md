---
title: 取证附录
---

# pinctrl 文档集 —— 取证、方法与未验证项

> **这一篇是给复核者看的。** 前面九篇里的每一条论断，出处在这里；没验证的部分也在这里。
> 判断某条结论能不能直接用，看 §3 的证据等级和 §5 的分层。
> **本篇所有数字都能用给出的命令原地重跑**——重跑对不上，就说明树变了或者我写错了。

---

## §1 基线

### §1.1 树的状态

```bash
git log -1 --format='%H %s'
# f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158
```

这棵树**不是干净的 upstream 6.1.158**——`git status` 显示大量文件被修改（PREEMPT_RT 相关的补丁栈）。所以必须先确认 pinctrl 有没有被动过：

```bash
git status --porcelain -- drivers/pinctrl include/linux/pinctrl \
    Documentation/driver-api/pin-control.rst
# 无输出
```

**无输出 = pinctrl 子系统与 upstream v6.1.158 完全一致**。本文档集所有论断因此可以直接对照 upstream 6.1.158 复核，不受本树补丁影响。

唯一的间接影响：`drivers/base/pinctrl.c` 和 `drivers/base/dd.c`（§2 那条"probe 前自动配引脚"的路径）不在上面的检查范围里。单独查：

```bash
git status --porcelain -- drivers/base/pinctrl.c drivers/base/dd.c
# 无输出（本文档集写作时）
```

### §1.2 子系统类型判定

按 skill 阶段 1 的两道门：

```bash
grep -rn 'struct bus_type' drivers/pinctrl/ include/linux/pinctrl/
# 无输出（0 处）
grep -rn 'bus_type\s*=' include/linux/pinctrl/*.h
# 无输出
```

**判定：框架型（framework）**，不是硬件总线型。落地后果：

| 篇 | 因这个判定而改的地方 |
|---|---|
| 00 §2 | 走"路径 B"：讲引脚复用这件硬件事实，不讲总线协议时序 |
| 02 | §5 画的是「核心抽象 ↔ 消费者」关系图，**不是**「本子系统总线 ↔ platform 总线」图 |
| 03 | 主轴是「控制器注册 → hog → 消费者 get/select」，不是「传输」 |
| 04 | pinctrl 确实有子框架（pinmux / pinconf / 泛型 group-function / DT / GPIO 桥），照常写 |
| 99 §5.5 | 「总线枚举/热插拔相关的未验证项」→ **不适用** |

pinctrl 的消费者不是"注册在某条总线上的设备"，而是**任何总线上的任何设备**——这一点由 `drivers/base/dd.c:616` 在 `really_probe()` 里无条件调用 `pinctrl_bind_pins()` 保证。这是本子系统与 i2c/spi 最大的结构差异，08 §2 专门讲了。

### §1.3 官方文档登记（skill 规则 6，阻塞性前置）

**`scripts/check_doc_coverage.sh` 在 pinctrl 上会报「【必查】共 0 篇」——这是脚本的假阴性，不是真的没文档。** 它按 `Documentation/**/*pinctrl*` 找，而 pinctrl 的主文档叫 **`pin-control.rst`**（带连字符，没有 "pinctrl" 字样）。所以这里手工登记：

| 文件 | 行数 | 读了哪些 | 纳入了什么 | 标注为过时的 |
|---|---|---|---|---|
| `Documentation/driver-api/pin-control.rst` | 1467 | §「GPIO mode pitfalls」`:789-934`、§「Pin control requests from drivers」`:1187-1301`、§「Drivers needing both pin control and GPIOs」`:1302-1345`、§「System pin control hogging」`:1347-1373`、§「Runtime pinmuxing」`:1376-1430`、§「Debugfs files」`:1433-1467`；其余章节扫标题 | 案例 (A)/(B) 两种 GPIO-pinmux 硬件形态（→ 08 §6、04 GPIO 桥节）；`strict` 该在什么硬件上置位（`:834-837`、`:861-864`）；「GPIO mode ≠ `<linux/gpio.h>`」这条设计意图（`:907-916`，→ 06 的 `PIN_CONFIG_OUTPUT` 用法）；get/lookup/select/put 的配对规则（`:1281-1284`，→ 01 备注列）；`pinctrl_select_state()` 不保证非阻塞上下文可用（`:1267-1272`，→ 01、05）；debugfs 11 个文件清单（`:1435-1467`，→ 07 §3 对照） | 4 处，见 §6 的 ⑬⑭⑮⑯：示例代码里 `pinctrl_select_state()` 少一个参数、hog 示例用了不存在的 `.function` 成员、hog 判据描述比代码窄、"core 会调 `pinctrl_get_select_default()`" 与 6.1 代码不符且完全没提 `PINCTRL_STATE_INIT` |
| `Documentation/devicetree/bindings/pinctrl/pinctrl-bindings.txt` | 109 行 | 全文 | 06 §1 的 `pinctrl-N` / `pinctrl-names` 语义。全文只定义了 4 个通用属性名（`pinctrl-0` `:47`、`pinctrl-1` `:65`、`pinctrl-n` `:68`、`pinctrl-names` `:70`），别的属性都是各控制器绑定自己的事 | 它**没有**提到「配置节点可以是孙节点」这条，只有 `pinctrl.yaml:22-23` 说了，见 §6 的 ⑩ |
| `Documentation/devicetree/bindings/pinctrl/pinctrl.yaml` | 45 行 | 全文 | 06 §1.1 属性总表的 schema 侧依据；`:22-23` 那句「配置节点可以是孙节点」是解释「为什么 `devicetree.c` 要往上找两层 parent」的官方依据之一（另一处是 `pinmux-node.yaml:14-18`，→ §6 ⑧⑩） | — |
| `Documentation/devicetree/bindings/pinctrl/pinmux-node.yaml` | 133 行 | 全文 | 06 §6.1.1 的 schema 校验节：5 个属性名的官方定义（`function` `:104`、`pins` `:108`、`groups` `:117`、`pinmux` `:124`、`pinctrl-pin-array` `:130`），`:12-101` 是三个完整示例。`:14-18` 也说了「配置节点可以是孙节点」——比 `pinctrl.yaml:22-23` 说得更细 | — |
| `Documentation/devicetree/bindings/pinctrl/pincfg-node.yaml` | 153 行 | 全文 | 06 §6.1.1：27 个通用配置属性的官方清单（`:21-151`，bias- 6 个、drive- 5 个、input- 5 个、output- 5 个，另加 `power-source`/`low-power-enable`/`low-power-disable`/`sleep-hardware-state`/`slew-rate`/`skew-delay`）。它是 `pinconf-generic.c` 那张 `dt_params[]` 表的 schema 对侧 | — |
| `Documentation/devicetree/bindings/pinctrl/*` 其余 190 篇 | — | **未读**（按需 grep） | 06 举例时只用 arm64 在用的那几篇 | — |

```bash
# 复现「必查文档」清单（脚本漏掉的那个名字）
find Documentation -iname '*pin*control*' -o -iname '*pinctrl*' -o -iname '*pinmux*' | grep -v bindings/
# Documentation/ABI/testing/sysfs-platform-i2c-demux-pinctrl   ← 与本子系统无关（i2c 的）
# Documentation/driver-api/pin-control.rst                      ← 主文档
ls Documentation/devicetree/bindings/pinctrl/ | wc -l   # 194（98 yaml + 96 txt，读了 4 篇）
```

三等证据的定性：`pin-control.rst` 的**设计意图部分可信度高**（案例 (A)/(B)、GPIO mode 的哲学，代码里读不出来），**示例代码部分可信度低**（4 处对不上 6.1 的 API）。凡冲突以代码为准，且在 §6 点名。

---

## §2 方法

### §2.1 详读范围（一等证据的边界）

「详读」= 整个文件逐行读过，函数体不跳。这些文件里引用的行号都是一等证据：

| 文件 | 行数 | 状态 |
|---|---|---|
| `drivers/pinctrl/core.c` | 2341 | 详读 |
| `drivers/pinctrl/pinmux.c` | 956 | 详读 |
| `drivers/pinctrl/pinconf.c` | 379 | 详读 |
| `drivers/pinctrl/pinconf-generic.c` | 419 | 详读 |
| `drivers/pinctrl/devicetree.c` | 435 | 详读 |
| `drivers/pinctrl/core.h` | 249 | 详读 |
| `drivers/pinctrl/pinmux.h` | 168 | 详读 |
| `drivers/pinctrl/pinconf.h` | 131 | 详读 |
| `drivers/pinctrl/pinctrl-utils.c` | 127 | 详读 |
| `drivers/base/pinctrl.c` | 105 | 详读（消费者自动绑定的全部逻辑就这一个文件） |
| `include/linux/pinctrl/*.h`（8 个） | 1106 | 详读 |
| **小计** | **6416 行**（`wc -l` 上表十项 = 5310，加 `include/linux/pinctrl/*.h` 的 1106） | |

```bash
wc -l drivers/pinctrl/core.c drivers/pinctrl/pinmux.c drivers/pinctrl/pinconf.c \
      drivers/pinctrl/pinconf-generic.c drivers/pinctrl/devicetree.c \
      drivers/pinctrl/core.h drivers/pinctrl/pinmux.h drivers/pinctrl/pinconf.h \
      drivers/pinctrl/pinctrl-utils.c drivers/base/pinctrl.c
wc -l include/linux/pinctrl/*.h | tail -1
```

分界线的理由：**pinctrl 的核心足够小（6.4k 行），可以全读，所以本文档集里"框架行为"类论断全部是一等证据**。这和驱动侧的比例极不对称：

```bash
find drivers/pinctrl -name '*.c' -o -name '*.h' | xargs wc -l | tail -1   # 460526 total
find drivers/pinctrl -name '*.c' | wc -l                                  # 377
```

**核心占整个 `drivers/pinctrl/` 的 1.4%**。剩下 98.6% 是 377 个控制器驱动，只讲契约。

### §2.2 只读符号表 / 只读片段的部分（二等证据）

| 范围 | 读法 | 用在哪 |
|---|---|---|
| `drivers/base/dd.c` | 只详读 `:600-640`（`really_probe()` 里调 `pinctrl_bind_pins()` 那一段），全文未读 | 08 §2、03 Part A |
| `drivers/pinctrl/renesas/core.c` | 只详读 `:1371-1382`（`pinctrl_provide_dummies()` 的全树唯一调用点） | 08 §4 |
| `drivers/pinctrl/qcom/*`、`drivers/pinctrl/mediatek/*` 等 arm64 主力驱动 | **只 grep 特定符号，没有整体读过** | 05 的"驱动侧契约"、04 的泛型 helper 使用者计数 |
| `drivers/gpio/gpiolib.c` | 只 grep `pinctrl_gpio_*` 的调用点 | 04 的 GPIO 桥节 |
| 377 个控制器驱动 | **只做全树 grep 计数**（`.strict`、`get_group_pins` 等回调的实现率） | 05、07 的"哪些回调是可选的" |

**代表驱动的选法**：本文档集**没有**选定"代表驱动"逐个精读。理由——pinctrl 的框架行为完全由 `drivers/pinctrl/*.c` 那 6.4k 行决定，驱动只提供回调表和寄存器写法，逐个精读一个 SoC 的 pinmux 驱动**对理解框架没有增益**（不像 spi 的 DMA/PIO 两种传输路径会走进核心的不同分支）。**代价**：05 的驱动侧骨架是从核心的调用点反推出来的契约，不是从某个真实驱动抄来的——它没有被任何一个真实驱动逐字比对过。这一点记在 §5.1。

### §2.3 可复现的统计命令

正文里所有带数字的论断，命令都在这里。**每轮 review 后全部重跑。**

```bash
# ── 日志分布（07 §2.1 那张表）──
for f in core.c pinmux.c pinconf.c pinconf-generic.c devicetree.c pinctrl-utils.c; do
  printf '%-20s err=%-3s warn=%-3s info=%-3s dbg=%-3s\n' $f \
    $(grep -c 'dev_err\|pr_err' drivers/pinctrl/$f) \
    $(grep -c 'dev_warn\|pr_warn' drivers/pinctrl/$f) \
    $(grep -c 'dev_info\|pr_info' drivers/pinctrl/$f) \
    $(grep -c 'dev_dbg\|pr_debug' drivers/pinctrl/$f)
done

# ── WARN 的 9 处（07 §4.5）── 注意必须带上 pinctrl-utils.c，只查 core+pinmux 会漏 2 处
grep -rn 'WARN_ON\|WARN(' drivers/pinctrl/core.c drivers/pinctrl/pinmux.c \
    drivers/pinctrl/pinctrl-utils.c
# core.c:337,1126,1407,1719,2245,2328（6）  pinmux.c:225（1）  pinctrl-utils.c:48,68（2）

# ── 错误码普查（07 §4）──
# 范围必须显式列出核心六个文件。写成 drivers/pinctrl/*.c 会把 100 多个
#    顶层控制器驱动一起扫进来（-EINVAL 会变成 326），数字完全不是一回事。
# grep -c 数的是「含该字符串的行数」，注释和 == 比较也算——所以它不等于
#   「返回点数」，07 §4 的表里逐行标出了哪几处不是返回点。
CORE='drivers/pinctrl/core.c drivers/pinctrl/pinmux.c drivers/pinctrl/pinconf.c
      drivers/pinctrl/pinconf-generic.c drivers/pinctrl/devicetree.c
      drivers/pinctrl/pinctrl-utils.c'
for e in EINVAL ENOMEM EPROBE_DEFER ENOENT ENODEV ENOTSUPP ENOSPC; do
  printf '%-16s %s\n' -$e $(grep -c -- "-$e" $CORE | awk -F: '{s+=$2} END{print s}')
done
# -EINVAL 47  -ENOMEM 23  -EPROBE_DEFER 8  -ENOENT 5  -ENODEV 6  -ENOTSUPP 4  -ENOSPC 2

# ── EXPORT_SYMBOL 数（01 的对外 API 面）──
grep -c 'EXPORT_SYMBOL' drivers/pinctrl/core.c drivers/pinctrl/pinmux.c \
    drivers/pinctrl/pinconf.c drivers/pinctrl/pinconf-generic.c drivers/pinctrl/devicetree.c
# core.c → 44   pinmux.c → 6   pinconf.c → 0   pinconf-generic.c → 5   devicetree.c → 3
# 合计 58。pinconf.c 一个都没有——它全是给核心内部和 pinconf-generic 用的

# ── .strict = true 的驱动数（08 §6）──
grep -rl '\.strict\s*=\s*true' drivers/pinctrl/ | wc -l          # 29
grep -rl '\.strict\s*=\s*true' drivers/pinctrl/qcom/ | wc -l     # 0   ← qcom 系全都没设

# ── 显式调用消费者 API 的文件数（必须带 -w，见 §4.2）──
grep -rlnw 'devm_pinctrl_get\|pinctrl_lookup_state\|pinctrl_select_state' \
    --include='*.c' drivers/ | wc -l                              # 42

# ── arm64 DTS 里用了 pinctrl 的文件数（06 的举例基数）──
grep -rl 'pinctrl-0' arch/arm64/boot/dts/ | wc -l                 # 556

# ── Kconfig 规模（01 §0）──
grep -c '^config ' drivers/pinctrl/Kconfig                        # 48

# ── 设计债（03 Part C）── 同样必须限定核心范围，不能用 *.c 通配
grep -n 'FIXME\|TODO\|REVISIT\|XXX' $CORE drivers/pinctrl/core.h \
    drivers/pinctrl/pinmux.h drivers/pinctrl/pinconf.h
# pinconf.c:284 FIXME（→ §6 ⑨）   core.c:2216 TODO（→ §6 ⑰）
# 整个核心 6.4k 行只有这 2 条自陈债——密度极低，这本身是个结论（03 Part C）

# ── debugfs 文件名（07 §3 与官方文档对照）──
grep -n 'debugfs_create_file' drivers/pinctrl/core.c drivers/pinctrl/pinmux.c \
    drivers/pinctrl/pinconf.c                                     # 11 个
```

### §2.4 时间线次序的验证方法（03 Part A 的依据）

03 Part A 那条时间线**不是凭直觉排的**，也不是照抄别的文档。每一步的次序都用同一条判据落实：**谁调用谁**。具体动作：

```bash
# 对时间线上相邻的两步 X、Y，验证「X 在 Y 之前」的唯一可接受证据是：
#   (a) X 的函数体里有一行调用 Y，或
#   (b) 两者被同一个上层函数 Z 顺序调用，且 Z 里 X 那行在 Y 那行之前
grep -n 'Y(' drivers/pinctrl/core.c        # 找 Y 的所有调用点
sed -n '<X 的定义范围>p' drivers/pinctrl/core.c   # 看 X 体内有没有它
```

这条判据实测抓到过一个真错：初稿把 `pinctrl_register()` → `pinctrl_claim_hogs()` → `pinctrl_enable()` → debugfs 排成**四个平级步骤**。实际 `core.c:2111-2128` 显示后三者是**嵌套在 `pinctrl_enable()` 里面**的（`:2115` 调 `pinctrl_claim_hogs()`、`:2122` 挂链表、`:2125` 建 debugfs）。平级和嵌套的区别不是修辞——它决定了"hog 申请失败会不会导致控制器根本不出现在 `pinctrl-devices` 里"这个可观测后果。

排完之后还要**反向检查有没有别处抄走了同一个错误次序**（skill 规则 17）：本次对 00/03/05/07 各 grep 一遍 `claim_hogs`/`pinctrl_enable`，确认没有传播。

### §2.7 arm64-only 裁掉了什么

**这是刻意的范围裁剪，不是遗漏。** 前面九篇都在篇首声明了这一条。具体裁掉的：

| 裁掉的 | 为什么与 arm64 无关 | 如果你在别的架构上看这份文档 |
|---|---|---|
| ACPI 路径 | pinctrl 的 ACPI 支持在 `drivers/pinctrl/intel/`、`drivers/pinctrl/pinctrl-amd.c` 等 x86 驱动里，走 `acpi_*` 而不是 `of_*` | 06 整篇不适用；03 的 map 来源要加一路 |
| `pinctrl_provide_dummies()` 机制 | 全树唯一调用点在 `drivers/pinctrl/renesas/core.c:1379`，被 `if (!of_have_populated_dt())` 挡住（`:1377`）。arm64 必然有 DT，**永不触发** | 08 §4 误导点二在无 DT 的 SH/ARM32 renesas 板子上是活的 |
| 板级 `pinctrl_register_mappings()` 静态 map 表 | arm64 平台一律走 DT 生成 map（`devicetree.c`）。静态表主要是 ARM32 板文件遗产 | 03 Part A 的 map 来源那一节要补一路；`machine.h` 的一堆 `PIN_MAP_*` 宏在 arm64 上基本只有 hog 那两个会被用到 |
| x86 特有的 `pinctrl-intel` 系"pin 与 GPIO 同一个驱动"的形态 | — | 04 的 GPIO 桥节只讲了 `gpiolib` ↔ `pinctrl` 的通用桥 |
| 32 位 ARM 的 `CONFIG_PINCTRL_SINGLE` 常见用法 | arm64 SoC 极少用 `pinctrl-single` | 05 §2 的骨架仍然适用，但 group 粒度的讨论（08 §5 误导点三）在 arm32 上更常见 |

一个**没有**被裁掉、但值得说明的：`drivers/base/pinctrl.c` 和 `drivers/base/dd.c` 是架构中立的，08 §2 那条路径在所有架构上都一样。

---

## §3 逐篇取证表

每篇列：**这一篇最吃重的论断** → 出处与证据等级 → **自审推翻/修正过什么**。

证据等级的定义：

| 等级 | 含义 | 你可以怎么用 |
|---|---|---|
| **一等** | 出自 §2.1 的详读文件，行号逐个 `sed` 打开核对过 | 可以直接用 |
| **二等** | 出自 §2.2 的片段读或 grep 计数 | 结论方向可信，边界条件请自己再确认一遍 |
| **三等** | 出自 `Documentation/`（设计意图） | 与代码冲突时以代码为准，见 §6 |
| **推断** | 从代码结构推出来、**没有观察到** | 当成假设，别当成事实。集中在 §5.3 |

### §3.1 `00-overview.md`（514 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| pinctrl 解决的是「每个消费者驱动都得知道 SoC 的 pinmux 寄存器」这个重复问题 | 反面依据：`grep -rn 'pinmux' drivers/i2c/ drivers/spi/` 里没有寄存器级操作 | 二等 |
| 引脚复用是一个物理引脚接到多个 IP 输出的选择器 | `pin-control.rst:810-859` 的案例 (A)/(B) 图 | 三等（硬件事实，代码里读不出来） |
| 同一消费者重复请求同一根 pin 不报错，`mux_usecount++` 后提前 `return 0` | `pinmux.c:146-147` | 一等 |
| 核心的四层对象：`pinctrl_dev` / `pinctrl` / `pinctrl_state` / `pinctrl_setting` | `core.h:42`、`:77`、`:92`、`:131` | 一等 |
| 端到端路径：`really_probe()` → `pinctrl_bind_pins()` → `devm_pinctrl_get()` → `pinctrl_select_state()` → `set_mux()` | `dd.c:616`、`drivers/base/pinctrl.c:32`/`:39-40`、`core.c:1269`、`pinmux.c:249` | 一等（`dd.c` 二等，见 §2.2） |

**自审动作与范围**：全篇行号逐个 `sed` 打开核对；§4/§5 的速览表与 02/03 的对应章节逐格对账（skill 规则 17）。§2 硬件背景刻意**不写**具体 SoC 的 pinmux 寄存器位域——位域每个 SoC 一份，没有可迁移性，写了反而会让读者以为那是通用知识。这是取舍，不是遗漏。

### §3.2 `01-api.md`（745 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 消费者侧对外 API 全在 `include/linux/pinctrl/consumer.h` | 该文件全文 + `EXPORT_SYMBOL` 计数 58 | 一等 |
| `pinctrl_get()` 必须配 `pinctrl_put()`，`devm_` 版不能混配 | `pin-control.rst:1281-1284`（官方明文）+ `core.c:1400-1412` 的 `WARN_ON(devres_release(...))` 兜底 | 三等 + 一等交叉 |
| `pinctrl_select_state()` 不保证能在非阻塞上下文调用 | `pin-control.rst:1267-1272` | 三等（代码侧的依据是 `pctldev->mutex` 是 `struct mutex`，`core.h:61`） |
| `link_consumers` 置 true 后核心为每个消费者建 device link | `pinctrl.h` 的 `struct pinctrl_desc` 字段 + `core.c:1288`/`:1312` 的 `pinctrl_link_add()` | 一等 |
| 配置值打包是 param 在**低 8 位** | `pinconf-generic.h:147` 的 `PIN_CONF_PACKED` 宏本体 | 一等 |

**自审修正**：一处真错——初稿把 `PIN_CONF_PACKED` 写成 `(param << 24) | arg`（凭"高位放类型"的直觉）。读宏本体发现是反的：`((a << 8) | ((unsigned long) p & 0xffUL))`。这个错在 `07-debug.md:418` 也复制了一份，一起改掉。**教训写进 §4.3。**

### §3.3 `02-datastructures.md`（792 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| `struct pinctrl` 不是"控制器"，是**消费者的句柄** | `core.h:77-84`（字段 `dev`/`states`/`state`/`dt_maps`/`users`） | 一等 |
| pinctrl 没有 `bus_type`，所以没有 device/driver 匹配 | `grep -rn 'struct bus_type' drivers/pinctrl/` = 空 | 一等（否定性论断，全树 grep） |
| `pin_desc` 的 mux 相关字段整段包在 `#ifdef CONFIG_PINMUX` 里 | `core.h:165-170` | 一等 |
| `mux_usecount` 是计数器不是布尔 | `core.h:166` + `pinmux.c:146-147`/`:225` | 一等 |
| 泛型 group/function 用 radix tree 存 | `core.h:185-220`、`drivers/pinctrl/pinmux.h:121-168` | 一等 |

**自审动作与范围**：§1.0 按 skill 规则 11 的「≥2 组关系」要求，逐组落地了三组易错类比（`pinctrl` vs `pinctrl_dev`、`pinctrl_state` vs `pinctrl_setting`、`group` vs `function`），每组都给了"为什么不会发生"的行号；结构体字段逐个回到 `core.h` / `include/linux/pinctrl/*.h` 核对。

### §3.4 `03-internals.md`（1343 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| Part A 时间线：`pinctrl_register()` 返回后**驱动必须自己再调** `pinctrl_enable()`，而 `pinctrl_claim_hogs()`、`list_add_tail()`、debugfs 注册**三件事全在 `pinctrl_enable()` 内部**（`core.c:2115` / `:2122` / `:2125`）——不是三个平级步骤 | `core.c:2111-2128` 全文；`pinctrl_register_and_init()` 的 kerneldoc 逐字写了 `Note that pinctrl_enable() still needs to be manually called after this`（`core.c:2169-2170`） | 一等（用「谁调用谁」验证，不是凭直觉排） |
| 所以 hog 申请失败（`dev_err "could not claim hogs: %i"`，`core.c:2117`）会让 `pinctrl_enable()` 直接 `return error`（`:2118`）——**控制器不进 `pinctrldev_list`、debugfs 目录也不会出现**。「debugfs 里找不到这个控制器」的头号原因就是它 | `core.c:2115-2119` | 一等 |
| 拆除路径 `pinctrl_unregister()`：先撤 debugfs，再 `pinctrl_put(pctldev->p)` 放 hog，最后摘链表+释放三张表 | `core.c:2200-2231` | 一等 |
| `pinctrl_commit_state()` 是**两遍**遍历，mux 先 config 后 | `core.c:1269` / `:1292` | 一等 |
| 切换前先把旧 state 的所有 mux setting 全 disable | `core.c:1256-1264` + 该处逐字注释 | 一等 |
| 泛型 group/function helper 在关掉 Kconfig 时退化成空 stub | `core.c:712-714`、`drivers/pinctrl/pinmux.h:121-168` 的 `#else` 分支 | 一等 |
| Part C 设计债：全核心只有 2 条自陈 | `pinconf.c:284` FIXME、`core.c:2216` TODO | 一等 |

**自审修正**：写 99 这一篇时才发现的一处真错——我在本表里先把时间线写成「`pinctrl_register()` → `pinctrl_claim_hogs()` → `pinctrl_enable()` → debugfs」四个平级步骤。读 `core.c:2111-2128` 发现**后三个是嵌套关系**：`claim_hogs` / 入链表 / debugfs 都在 `pinctrl_enable()` 里。差别是实质性的——按平级理解会以为「hog 失败但 debugfs 还在」，实际是 debugfs 根本不会建。已按上表改正。

### §3.5 `04-extensions.md`（833 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 五个子框架互不调用：pinmux / pinconf / 泛型 group-function / DT / GPIO 桥 | 各自的文件边界 + `#ifdef` 分区 | 一等 |
| pinmux 和 pinconf 各有独立 Kconfig（`CONFIG_PINMUX` / `CONFIG_PINCONF`） | `drivers/pinctrl/Kconfig` + `drivers/pinctrl/Makefile` | 一等 |
| 泛型 helper 的使用者计数 | `grep -rl 'pinctrl_generic_add_group\|pinmux_generic_add_function' drivers/pinctrl/` | 二等 |
| GPIO 桥只做**单引脚**复用，不走 group | `core.c:330-345` + `pinmux.c` 的 `pinmux_gpio_*` | 一等 |

**自审动作与范围**：按 skill 规则 19，篇内所有「全树唯一 / 大多数驱动 / 没有一个」这类全称断言都改成了**带命令的计数**并降级为二等证据——框架的使用者不住在框架自己的目录下，凭印象说"大多数"是站不住的。篇末的汇总表（框架 × 职责 × 挂载点 × arm64 是否相关）逐行回到 03 的时间线核对了挂载点。

### §3.6 `05-usage.md`（1042 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 控制器驱动必填的回调（`get_groups_count`/`get_group_name`） | `core.c:1983` + 调用方 `:2029` 的 `pinctrl ops lacks necessary functions` | 一等 |
| pinmux ops 必填三个 | `pinmux.c:45` 的检查 + `:37-44` 的条件 | 一等 |
| pinconf 至少要能 set | `pinconf.c:33` 的 `pinconf has to be able to set a pins config` | 一等 |
| `desc->name` 忘填 → `-EINVAL` 且**完全没有日志** | `core.c:2002`/`:2004` | 一等 |
| `pinctrl_force_sleep()` / `pinctrl_force_default()` 是**控制器给自己的 hog 用的**，消费者要用 `pinctrl_pm_select_*` | `core.c:1510-1515`/`:1522-1527`（读 `pctldev->hog_*`）vs `consumer.h` 的消费者版 | 一等 |
| 骨架代码**未编译** | — | 见 §5.1 |

**自审修正**：按 skill 规则 20 把正文里每个符号名跑了一遍全树 grep（`grep -rlw`）。两组"名字很像"的高危对，**其中一组我一开始判错了**：

| 存在 | 不存在（写了不会编译） | 怎么确认 |
|---|---|---|
| `pinctrl_gpio_request()`（`core.c`、`core.h`） | `pinctrl_request_gpio()` | `grep -rlw pinctrl_request_gpio .` 全树为空 |
| `pinctrl_force_default()` / `pinctrl_force_sleep()`（`core.c:1510`/`:1522`） | `pinctrl_force_idle()` | 同上，全树为空。**`pinctrl_pm_select_idle_state()` 是存在的**（消费者侧，被 `i2c-nomadik.c`、`i2c-tegra.c`、`pci-tegra.c` 等在用）——我一开始把它也当成了不存在的那个，是判错了 |

教训：**"不存在"这个论断本身也要 grep**，不能因为"配套的另一个不存在"就顺手把它也划掉。

### §3.7 `06-devicetree.md`（933 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 核心真正会读的属性只有 `pinctrl-N` 和 `pinctrl-names` | `devicetree.c` 全文 + `pinctrl-bindings.txt:47-70` | 一等 + 三等交叉 |
| `#pinctrl-cells` 由 `pinctrl_find_cells_size()` 读，找不到时退化成 1 | `devicetree.c:300-345` | 一等 |
| 配置节点可以是孙节点，所以解析要往上找两层 parent | `devicetree.c:304` 的 `np->parent->parent` + `pinctrl.yaml:22-23` | 一等 + 三等交叉 |
| 泛型 pinconf 属性（`bias-pull-up` 等）由 `pinconf-generic.c` 的两张静态表驱动 | 解析用 `dt_params[]`（`:160`，被 `pinconf_generic_parse_dt_config()` 在 `:261` 使用）；debugfs 显示用 `conf_items[]`（`:29`）。**两张表是分开的**，一个 param 可以能解析但不能显示 | 一等 |
| DTS 举例全部取自 `arch/arm64/boot/dts/` | 556 个文件用了 `pinctrl-0` | 二等 |

**自审动作与范围**：按 skill 规则 14，篇内每条属性都跑过 `rg -n '"<属性名>"' drivers/pinctrl/` 找消费代码，**找不到消费者的属性在正文里显式标成死字**而不是删掉——绑定文档不会告诉你这件事，标出来才有用。DTS 举例一律取 `arch/arm64/boot/dts/` 下的 in-tree 节点，读者可以直接 diff。

### §3.8 `07-debug.md`（1105 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| debugfs 11 个文件，3 个在顶层、8 个在每控制器子目录 | `core.c:1926`/`:1928`/`:1930`/`:1952`/`:1954`/`:1956`、`pinmux.c:779`/`:781`/`:783`、`pinconf.c:373`/`:375` | 一等（与 `pin-control.rst:1435-1467` 逐个对上） |
| pinctrl **没有** tracepoint / sysfs / 故障注入 / `module_param` | 全树 grep 四个都为空 | 一等（否定性论断） |
| mux 冲突和 GPIO 冲突打印**完全相同**的字符串 | `pinmux.c:129-131` 与 `:136-138` | 一等 |
| `pinmux-select` 是唯一可写的调试文件（0200） | `pinmux.c:783` | 一等 |
| WARN 共 9 处，8 处是裸 `WARN_ON`（dmesg 里只有行号） | 见 §2.3 的命令 | 一等 |
| 错误码计数 | 见 §2.3 的命令 | 一等 |
| 示例命令的输出**是构造的**，不是真机抓的 | — | 见 §5.1 |

**自审修正**（这一篇改动最多）：
1. **两条编造的错误消息**——我写了 `cannot mux pin(s) without a function`（`pinmux.c:353`）和 `could not find group selector for %s`（`:375`）。全树 grep：**都不存在**。唯一相似的是三个不相关驱动里的 `cannot mux pin %u to function %u`。读 `pinmux.c:320-397` 重写了整个 §4.1，六条消息全部逐字。
2. `core.c:746` 被写成"无日志"——它其实是四个查表函数里**唯一有日志**的（`does not have pin group %s`）。
3. §4.7 把 `if (ret == -EINVAL)` 这样的**比较点**当成了返回点（`pinconf-generic.c:219`/`:327`）。重写成"真返回点"和"把 `-EINVAL` 读作属性缺失"两组。
4. WARN 计数在三处不一致（§2.1 表 / §2.2 / §4.5 标题），统一为 9。
5. **错误码表里 `-ENOMEM`/`-EPROBE_DEFER`/`-ENODEV` 三个数字与它自己给的复现命令不符**（18/7/5 vs 实际 23/8/6）。改数字，并补上"哪几处不是返回点"这一列——这是 §4.4 那条教训的直接产物。

### §3.9 `08-feynman.md`（736 行）

| 主题 | 关键取证 | 等级 |
|---|---|---|
| §1 `pinctrl_get()` 拿到的是编译产物 | `core.c:1065-1067`/`:1080`/`:1094-1098`、`core.h:42`/`:77`/`:92`/`:131` | 一等 |
| §2 引脚在 `.probe()` 之前就配好了 | `dd.c:612-618`、`drivers/base/pinctrl.c:25-104` 全文 | 一等（`dd.c` 二等） |
| §3 mux/config 六层分离 + mux 必须先 | `core.c:1269`/`:1292`、`:1275-1278`/`:1295-1297` | 一等 |
| §4 state 是 `#define` 字符串不是 enum | `pinctrl-state.h:33-36`、`find_state()` `core.c:922-932` | 一等 |
| §4 dummy state 旁路在 arm64 上**永不触发** | `core.c:1213-1229`、唯一调用者 `drivers/pinctrl/renesas/core.c:1379` 被 `if (!of_have_populated_dt())`（`:1377`）挡住 | 一等 + 二等 |
| §5 map 里不写 group 会静默取 `groups[0]` | `pinmux.c:376-387`，`else` 分支 `:386` 无任何日志 | 一等 |
| §6 `strict` 的全部作用是把两个 `||` 短路条件变成恒真 | `pinmux.c:127-128`/`:135` + `include/linux/pinctrl/pinmux.h:56-58`/`:82` | 一等 |
| §6 官方文档说明了 `strict` 该在什么硬件上置位 | `pin-control.rst:834-837`、`:861-864` | 三等 |
| §7 hog 的判据是 `dev_name == ctrl_dev_name` 字符串相等 | `core.c:1076-1077`、`machine.h:56-58`、宏 `:102-106` | 一等 |
| §8 回滚只撤 mux 账本、不撤硬件、完全不撤 config | `core.c:1320-1339`，逐字 `We can't "unmux a pin"!`（`:1329-1330`） | 一等 |

**自审修正**：
1. 四处行号偏 1–3 行（`core.c:1268`→`:1269`、`:1274-1276`→`:1275-1278`、`:1296-1298`→`:1295-1297`、`:1332-1337`→`:1326-1332`），逐个 `sed` 核对后改。
2. 一条弱断言换成强断言：原写"arm64 上这个开关基本没人用（grep `arch/arm64/` 为空）"。全树 grep 找到唯一调用者及其 `!of_have_populated_dt()` 守卫——**这才是它在 arm64 上永不触发的真正原因**。
3. `pinmux-pins` 的 `(HOG)` 标记：判据行写成 `:604-614`（把 `bool is_hog = false;` 的声明也算进去了），改成 `:612-614`；打印点写成 `:621`，实际 strict 分支在 `:620`。
4. `pinctrl_force_sleep()` 被误称为 "`pinctrl_pm_select_sleep_state` 的控制器版"——两者不是同一族函数，改成点名真实函数名并说明区别。

---

## §4 交叉验证实例：这些是初始假设被推翻的地方

**这一节的每条都是"我先想错了，代码告诉我不是"**。它们比正确的部分更能说明该在哪里小心。

### §4.1 「`grep` 数消费者」——词边界能把结论翻个方向

初始做法：

```bash
grep -rl 'pinctrl_get' --include='*.c' drivers/ | wc -l
```

这个数很大，我据此打算写"绝大多数驱动都显式调用 pinctrl API"。**问题**：`pinctrl_get` 是 `devm_pinctrl_get`、`pinctrl_get_select_default`、`pinctrl_get_group_selector` 的子串。加词边界后：

```bash
grep -rlnw 'devm_pinctrl_get\|pinctrl_lookup_state\|pinctrl_select_state' \
    --include='*.c' drivers/ | wc -l          # 42
```

**42 个文件**——在整个 `drivers/` 里是极小的比例。结论**反过来了**：绝大多数消费者一行 pinctrl 代码都不写，全靠 `pinctrl_bind_pins()` 自动完成。这条反转直接变成了 08 §2 的主题。

教训：**统计符号使用量必须带 `-w`**。不带的话，凡是有 `devm_` 前缀或更长同族函数的 API，数字一律偏大。

### §4.2 「hog 只认 default state」——官方文档说窄了

初始假设（来自 `pin-control.rst:1355-1356`，官方明文）：hog 的条件是 *client device name == pin controller device name* **且** *state name == PINCTRL_STATE_DEFAULT*。

代码不是这样。`create_pinctrl()` 的 hog 筛选只比对名字（`core.c:1076-1077`），**完全不看 state 名**；而 `pinctrl_claim_hogs()` 除了 `default` 还会查 `sleep`（`core.c:2101-2103`）并存进 `pctldev->hog_sleep`（`core.h:60`）。

所以 6.1 里 **hog 是支持 sleep state 的**，官方文档那句话已经过时。写进 §6 的 ⑮。

教训：**官方文档描述"条件"的地方要特别小心**——功能扩展时文档常常忘了跟着放宽。

### §4.3 「位域打包按直觉是高位放类型」——错

初始写法（凭经验）：`PIN_CONF_PACKED` 是 `(param << 24) | (arg & 0xffffff)`。理由是"类型放高位"看起来更自然。

读宏本体（`pinconf-generic.h:147`）：

```c
#define PIN_CONF_PACKED(p, a) ((a << 8) | ((unsigned long) p & 0xffUL))
```

**完全相反**：param 在**低 8 位**，argument 在高 24 位。两个解包宏（`:156-158`、`:161-163`）确认。

这个错的可怕之处：它让 `pinconf-pins` 的十六进制输出全部读反。`00000501` 在错的读法下是"param 0、arg 0x501"，正确读法是"param 1（`PIN_CONFIG_BIAS_DISABLE`）、arg 5"。

教训：**位域打包宏必须读宏本体，一次直觉都不能信**。

### §4.4 「计数类论断改了正文，汇总表会留在旧数字上」

`WARN_ON` 的处数在 07 里出现了三次（§2.1 的表格列、§2.2 的正文、§4.5 的标题）。我改了 §4.5 的表格内容之后，另外两处仍然是旧数字。

更严重的是错误码表：**它自己给了复现命令，但表里的三个数字跟命令的输出不符**。这说明数字是初稿时凭印象填的，命令是后来补的，两者从未对账。

教训（已回写 skill 候选）：**凡是文档里同时给了"数字"和"复现命令"的地方，定稿前必须真的跑一遍命令并逐格对账**。给了命令却没跑，比不给命令更糟——读者会因为有命令而更相信那个数字。

### §4.5 「同名不同文件」——`pinmux.h` 有两个

`drivers/pinctrl/pinmux.h`（168 行，内部头）和 `include/linux/pinctrl/pinmux.h`（85 行，驱动接口）。03/04 引用的 `:121-168` 属于前者，08 引用的 `:34-82` 属于后者。

`check_line_refs.py` 按"文档里最常用的全路径"消歧——08 写完之后 `include/` 那个变成了多数，于是 03/04 的 `:121-168` 突然报"越界（实际只有 85 行）"。

这是**脚本救回来的一次真错**（如果 08 没写，这个歧义会一直藏着）。修法是把两处都改成全路径，不是加进 `.lineref-ignore`。

教训：**同名头文件必须写全路径**，尤其是 `drivers/<subsys>/x.h` 和 `include/linux/<subsys>/x.h` 这种成对出现的。

---

## §5 明确未验证项

这一节的价值不低于前面九篇：它决定了读者该在哪些地方加倍小心。按"离事实有多远"分四层，越往下越不可靠。

### §5.1 第一层：未编译、未运行

**没有任何一行代码被编译过，没有任何一条命令在运行的内核上执行过。** 全部结论来自静态阅读 6.1.158 的源码。

| 涉及位置 | 具体是什么 | 读者该怎么办 |
|---|---|---|
| `05-usage.md` 全篇 | 五个角色的代码骨架（控制器驱动、消费者驱动、GPIO 桥、板级静态 map、debugfs 排查）。篇首已声明"未编译" | 当伪代码读。抄之前先对着 `01-api.md` 核签名，编译期会立刻暴露参数个数/类型问题 |
| `07-debug.md` §3 的 debugfs 输出样例 | 这些**不是**从运行系统上 `cat` 出来的，是按 `seq_printf()` 的格式串反推构造的。每处都给了格式串的行号（如 `pinmux.c:620`、`:634`） | 格式（列数、分隔符、字段顺序）可信；**具体数值全是编的**。要真值就自己 `cat` |
| `07-debug.md` §2 的日志样例 | 同上，按 `dev_err/dev_dbg` 的格式串反推。字符串本身全树 grep 过（规则 20），是真存在的 | 消息文本可信，`%d`/`%s` 填进去的内容不可信 |
| `06-devicetree.md` 的 DTS 片段 | 引自 in-tree `arch/arm64/boot/dts/`，读者可 `diff`。但"配错了会怎样"的症状是从代码路径推出来的 | 正例可信（是真节点）；反例的症状描述是推断，见 §5.3 |
| `07-debug.md` 的 tracepoint 一节 | pinctrl 核心**没有** tracepoint（六个核心文件里 `grep -n 'trace_'` 为 0；全 `drivers/pinctrl/` 只有 2 个文件命中，且全是 `mips_trace_clk` / `arm_trace_pins` 这种「JTAG/ETM trace 引脚组」的名字，与 ftrace 无关），该节讲的是"没有 tracepoint 时用什么替代" | 这是个否定性结论，已按规则 19 全树 grep |

### §5.2 第二层：只读了符号表 / 只读了片段

登记见 §2.1（一等，逐行读）与 §2.2（二等，只读片段）。这里只强调三处最容易被误读成"我读过"的地方：

| 位置 | 实际读了多少 | 由此带来的风险 |
|---|---|---|
| `drivers/pinctrl/` 下 371 个控制器驱动 | **一个都没有逐行读**。全部只做 `grep` 统计（`.strict` 的 29 处、`gpio_request_enable` 的实现者、错误码分布） | 凡是"控制器驱动通常会…"这类句子都是从统计推的群体特征，不是从某个驱动读出来的个体事实。文档里没有"代表驱动详解"这一节，就是因为没选代表驱动（§2.2 已声明代价） |
| `drivers/base/dd.c` | 只读 `:600-640`（`pinctrl_bind_pins()` 的调用点）+ `:684`（`pinctrl_init_done()` 的调用点） | probe 路径上 pinctrl 前后还发生了什么（deferred probe 队列、`device_links`）没读。03 Part A 时间线的"上游"是断的 |
| `drivers/pinctrl/renesas/core.c` | 只读 `:1371-1382` | 只用来证明"有驱动自己实现了 `gpio_request_enable`"这一条存在性论断，不构成对 renesas 实现的任何评价 |
| `Documentation/devicetree/bindings/pinctrl/` 下 194 篇绑定 | 只读了 4 篇通用的（`pinctrl-bindings.txt`、`pinctrl.yaml`、`pinmux-node.yaml`、`pincfg-node.yaml`），其余 190 篇**未读**（`find Documentation/devicetree/bindings/pinctrl -type f \| wc -l` = 194） | 06 讲的是"通用属性"和"核心真正会读的属性"。具体某个 SoC 的属性名（qcom 的 `function`/`pins`/`drive-strength` 之类）不在覆盖范围内 |

### §5.3 第三层：推断而非观察

以下结论**逻辑链完整、但没有任何运行时证据**。它们是本文档集里最该被质疑的部分。

| 论断 | 出现在 | 推断依据 | 缺什么证据 |
|---|---|---|---|
| state 切换失败后硬件处于"既非新 state 也非旧 state"的中间态 | `08-feynman.md` §8、`07-debug.md` §4 | `pinmux_disable_setting()`（`pinmux.c:486-534`）自己只用 `pctlops` 的两个**查询**回调（`:496` `get_group_pins`、`:503`/`:525` `get_group_name`），然后调 `pin_free()`（`:521`）划账本。精确说法：**`set_mux` 在整棵拆除路径上没有对偶**（`pmxops->set_mux` 全核心只在 `pinmux.c:464` 和 `:750` 被调，都是 enable 侧），核心从不把"上一个 mux 值"写回寄存器；拆除路径上唯一能碰硬件的驱动钩子是 `pmxops->free`（`pinmux.c:238-239`，29 个驱动实现了它），而它的语义按 `include/linux/pinctrl/pinmux.h:27-29` 是「`request()` 的逆操作」，不是「恢复原 mux」。加上 `core.c:1324` 的 `We can't "unmux a pin"!` 注释 | 没有寄存器 dump。"账本干净、硬件是脏的"这个措辞是我对代码的解读，不是观测。那 29 个实现了 `.free` 的驱动里，有没有哪个顺手把 mux 恢复了，**我没有逐个读**（§5.2 第一行） |
| "先全放开再全申请"之间存在所有引脚无主的窗口 | `08-feynman.md` §8 | `core.c:1260-1264` 先对旧 state 全部 `pinmux_disable_setting()`，`:1289` 才开始对新 state `pinmux_enable_setting()`；两段之间取的锁不是同一把（`p` 的锁 vs `pctldev->mutex`） | 没有构造并发场景实测。**理论上存在 ≠ 真能触发**。已在 08 §8 标为推断 |
| `(HOG)` 标记会随控制器改 `dev_name()` 而消失，而配置没变 | `08-feynman.md` §7 | 判据是每次 `cat` 时做的字符串比较（`pinmux.c:612-614`），不是持久化的标志位 | 没有实际改名重测 |
| `pinctrl_find_cells_size()` 的 `np->parent->parent` 可能是 NULL | §6 的 ⑧ | `devicetree.c:303-304` 无 NULL 检查 | **没有构造出能真的触发崩溃的 DTS**。它可能被上游调用链的某个前置条件挡住了（该函数是 `static`，唯一调用点 `:338`）。所以只登记为"缺检查"，不声称"能崩" |
| "绝大多数消费者驱动不显式调用 pinctrl API，靠驱动核心自动应用 default" | `08-feynman.md` §2、`00-overview.md` | 全树只有 42 个文件显式调用（§2.3 的带 `-w` 命令）+ `pinctrl_bind_pins()` 对每个 device 无条件尝试（`drivers/base/pinctrl.c:21`） | 分母是"所有会用到引脚的驱动"，我没有办法精确统计这个分母。方向可信，比例是估的 |
| `pinconf_groups_show()` 无锁是个隐患 | §6 的 ⑥ | 与 `pinconf_pins_show()`（`pinconf.c:310`/`:328` 成对取放）对比得出的不对称 | 没有分析出具体的并发破坏路径，也没实测。只登记不对称这个**事实**，"是隐患"是我的判断 |

### §5.4 第四层：版本时效

| 项 | 状态 |
|---|---|
| 基线 | `f6e38ae624cf` Linux 6.1.158（§1.1）。`drivers/pinctrl/`、`include/linux/pinctrl/`、`drivers/base/pinctrl.c`、`Documentation/driver-api/pin-control.rst` 相对 upstream v6.1.158 **无本地改动** |
| 未做的比对 | **没有和 6.2+ / 主线做任何 API 差异比对**。本文档集的所有签名、行号、结构体成员只对 6.1.158 成立 |
| 行号的脆弱性 | 全篇 826 处唯一 `文件:行号` 引用。stable 分支的小版本升级（6.1.158 → 6.1.16x）就可能整体位移 | 
| 保底手段 | 正是因为行号会漂，凡是关键论断都同时给了**可 grep 的字符串**（函数名、错误消息、注释原文）。行号失效时用字符串重新定位，见 §2.3 的命令 |
| 官方文档的时效 | `pin-control.rst` 自身就落后于 6.1 代码，实证 4 处，见 §6 的 ⑬⑭⑮⑯。**读文档不能替代读代码** |

### §5.5 不适用项

skill 模板里有一项"子系统总线 ↔ 父总线"的验证（控制器在父总线、设备在子系统总线、`dev.parent` 缝合点）。

**pinctrl 不适用**：`grep -rn 'struct bus_type' drivers/pinctrl/ include/linux/pinctrl/` 为 0 处（§1.2）。pinctrl 是框架型子系统，没有自己的 `bus_type`，不注册 device、不绑 driver、没有 `match`/`probe`。02 篇里对应位置画的是"核心抽象 ↔ 消费者"的关系图。

由此连带不适用的还有：uevent / sysfs 设备属性（pinctrl 只有 debugfs，没有 sysfs 接口——`grep -rn 'DEVICE_ATTR\|ATTRIBUTE_GROUPS' drivers/pinctrl/core.c` 为空）、驱动匹配表（无 `of_device_id` 在核心层）、设备热插拔路径。

---

## §6 已发现的问题清单

统一编号 ①–⑲，前面几节的前向引用（§1.3、§2.3、§4.2）都指向这里的编号。顺序是：**先说会咬人的（有运行期后果）→ 再说文档骗人的 → 最后说还没人处理的**。

「问题」在这里是中性词：有的是真缺陷，有的只是"不对称/容易误读"，每条都标了性质。

### §6.1 会咬人的：代码层面（①–⑨）

| # | 位置 | 是什么 | 性质 | 后果 |
|---|---|---|---|---|
| ① | `core.c:1320-1339` + `pinmux.c:486-534` | 应用 state 失败时的回滚**只销毁账本、不复原硬件**。`set_mux` 没有对偶（详见 §5.3 第一行） | 设计使然，注释已写明（`core.c:1324` `We can't "unmux a pin"!`） | 失败后硬件处于**既非新 state 也非旧 state** 的中间态，而 `pinmux-pins` 会显示"这些引脚没人占着"——**账本干净、硬件是脏的**。排查时不能信 debugfs |
| ② | `core.c:1256-1264` → `:1289` | 切 state 时先把旧 state 的所有 group **全部** `pinmux_disable_setting()`，再逐个申请新 state。注释自己说了 `Any pingroups which are still owned by the new state will be re-acquired`（`:1256-1258`） | 不对称/推断 | 两段之间存在一个"所有引脚无主"的窗口。此刻若有别的消费者来抢同一个 group，它会成功。**只是推断，未实测**（§5.3 第二行） |
| ③ | `pinmux.c:129-131` vs `:136-138` | 两条 `dev_err` 用了**逐字相同**的前缀 `pin %s already requested by %s; cannot claim for %s`，一条是 mux 冲突、一条是 GPIO 冲突 | 可观测性缺陷 | 只看 dmesg **分不清是两个驱动抢 mux，还是 GPIO 和 mux 打架**——而这两种情况的修法完全不同（改 DTS 里的 group 归属 vs 改驱动的 GPIO 申请时机）。07 §2 给了区分办法 |
| ④ | `pinmux.c:386` | `pinmux_func_name_to_selector()` 找不到匹配 group 时，**静默回退到 `groups[0]`** | 静默失效 | DTS 里 group 名写错，不报错，而是**接到了函数的第一个 group 上**。启动完全正常，只是引脚接错了。06 的决策树把它标为最难查的一类 |
| ⑤ | `core.c:980`、`:1287`、`:1311` | 三处针对 hog 的循环依赖特判。`:980` 的注释是 `/* Do not defer probing of hogs (circular loop) */`，于是 hog 的 map 解析失败时返回 **`-ENODEV` 而不是 `-EPROBE_DEFER`**；`:1287`/`:1311` 的 `/* Do not link hogs (circular dependency) */` 让 hog 不建 device_link，判据是 `p != setting->pctldev->p` | 设计使然，但错误码有歧义 | `-ENODEV` 在这里的含义是"没有 hog"和"hog 解析失败"两种，**调用方 `pinctrl_claim_hogs()` `:2076-2080` 把它一律当成前者并 `return 0`**。真正写错了的 hog map 会被静默忽略 |
| ⑥ | `pinconf.c:302-343` vs `:345-365` | `pinconf_pins_show()` 成对取放 `pctldev->mutex`（`:310` / `:328`）；`pinconf_groups_show()` **一次都不取** | 不对称（我没找到具体的破坏路径） | `cat pinconf-groups` 与并发的 state 切换之间没有互斥。只登记这个**不对称的事实**，"是隐患"是我的判断（§5.3 最后一行） |
| ⑦ | `pinmux.c:612-614` | `pinmux-pins` 里的 `(HOG)` 标记不是持久标志位，是**每次 `cat` 时做的字符串比较**：`!strcmp(desc->mux_owner, pinctrl_dev_get_name(pctldev))` | 容易误读 | 控制器改了自己的 `dev_name()`，标记就消失，而配置一个字节都没变。**它反映的是"名字撞上了"，不是"这是一条 hog map"** |
| ⑧ | `devicetree.c:297-311` | `pinctrl_find_cells_size()` 里 `:303` 的第一次尝试是**安全的**（`of_property_read_u32()` 最终落到 `__of_find_property()`，`drivers/of/base.c:194-195` 有 `if (!np) return NULL;`）；但 `:303-304` 的第二次尝试 **`np->parent->parent`** 直接解引用了 `np->parent`，**没有检查它是不是 NULL** | 缺检查（未构造出触发路径） | 之所以要往上找两层，依据是配置节点**可以是孙节点**。畸形 DT（配置节点挂得太浅）理论上能走到这里。`static` 函数、唯一调用点 `:338`（`pinctrl_get_list_and_count()`），再往上是 `dt_to_map_one_config()`（`:109`）——**我没有构造出能真的触发的 DTS**，所以只登记"缺检查"，不声称"能崩"（§5.3 第四行） |
| ⑨ | `pinconf.c:282-285` | 代码自陈的 FIXME，逐字：`/* FIXME: We should really get the pin controller to dump the config values, so they can be decoded to something meaningful. */` | 源码自陈 | `pinconf-pins` 打出来的是**原始整数**，不是"pull-up / drive-strength=8mA"这样的人话。07 §3 已提醒读者必须自己对着驱动的 `PIN_CONFIG_*` 解码 |

### §6.2 文档骗人的：官方文档 vs 代码 / 文档之间（⑩–⑯）

前提：**这一节的每一条都以代码为准**。skill 规则 6 要求先读官方文档，读的结果就是下面这些——它们是"只读代码"绝对得不到的收获（反过来说，也是"只读文档"会踩的坑）。

| # | 文档位置 | 文档怎么说 | 代码实际怎样 | 处置 |
|---|---|---|---|---|
| ⑩ | `devicetree.c:293-296`（代码注释）→ `pinctrl-bindings.txt`（109 行，全文）vs `pinctrl.yaml:22-23` | **代码注释指向的那份文档里没有这句话**。注释逐字：`/* For pinctrl binding, typically #pinctrl-cells is for the pin controller device, so either parent or grandparent. See pinctrl-bindings.txt. */`——但 `pinctrl-bindings.txt` 全文 109 行**一个字都没提**祖父节点，通篇只讲子节点。真正说了"配置节点可以是孙节点"的是另外两份：`pinctrl.yaml:22-23` 和 `pinmux-node.yaml:14-18` | `devicetree.c:303-304` 确实往上找两层 parent，孙节点形态是真支持的（见 ⑧） | 06 §1 以 `pinctrl.yaml` 为准并注明只有它说了这条。这是"照着注释里给的文档指引去读，反而读不到"的一个实例——注释里的文档引用也会过时 |
| ⑪ | `drivers/pinctrl/Kconfig:28-32`（Kconfig help 也是文档） | `DEBUG_PINCTRL` 的 help：`Say Y here to add some extra checks and diagnostics to PINCTRL calls.` | 全树只有**一处**引用这个 config：`drivers/pinctrl/Makefile:4 subdir-ccflags-$(CONFIG_DEBUG_PINCTRL) += -DDEBUG`。它**只是打开 `-DDEBUG`（让 `dev_dbg` 编进去）**，没有任何"extra checks" | 07 §1 明确写"它只做一件事：让 `pinctrl` 目录下的 `dev_dbg` 生效"，并给出这条 Makefile |
| ⑫ | `pin-control.rst` 全篇（1467 行） | 讲 PM 时只教手写 `pinctrl_lookup_state(p, PINCTRL_STATE_SLEEP)` + `pinctrl_select_state()`（`:924-932`） | 全篇 `grep 'pm_select\|STATE_IDLE\|STATE_INIT'` **零命中**——完全没提 `pinctrl_pm_select_default/sleep/idle_state()` 这一族现成 helper（`include/linux/pinctrl/consumer.h:46-48`），也没提 `pinctrl-state.h:34-35` 定义的 `"init"` 和 `"idle"` 两个 state 名 | 01 把 `pm_select` 三兄弟列全；05 的消费者骨架直接用 helper；08 §7 点明 hog 用的 `pinctrl_force_*()` 是另一族 |
| ⑬ | `pin-control.rst:1241`、`:1414`、`:1421` | 三处示例写成 **`pinctrl_select_state(foo->s)`**，只有一个参数 | 原型是两个参数：`pinctrl_select_state(struct pinctrl *p, struct pinctrl_state *s)`（`include/linux/pinctrl/consumer.h:39`）。同一篇文档的 `:930`、`:932`、`:1174` 写的又是对的两参数版 | 05 的骨架全部用两参数版，且篇首声明"未编译" |
| ⑭ | `pin-control.rst:1358-1364` | hog 的 map 示例里写 `.function = "power_func",` | **`struct pinctrl_map`（`include/linux/pinctrl/machine.h:67-76`）没有 `.function` 这个成员**——只有 `dev_name`/`name`/`type`/`ctrl_dev_name`/`data`，function 藏在 `data.mux.function` 里。这段示例编不过 | 05/08 一律用 `PIN_MAP_MUX_GROUP_HOG_DEFAULT()` 宏（`machine.h:102-106`），让宏去填 union |
| ⑮ | `pin-control.rst:1355-1356` | 把 hog 的触发条件说成 `the state name is PINCTRL_STATE_DEFAULT` | 代码里判据只是**控制器名 == 消费者名**（`core.c:1069-1078` 的 `strcmp`），与 state 名无关。`pinctrl_claim_hogs()` 除了 default（`:2089-2099`）还会查 **sleep**（`:2101-2106`），并由 `pinctrl_force_sleep()`（`core.c:1510-1515`）应用 | 08 §7 按代码写，并显式说明"文档这句话说窄了：6.1 的 hog 是支持 sleep state 的"（此条即 §4.2） |
| ⑯ | `pin-control.rst:1190-1191` | `the device core will automatically attempt to issue pinctrl_get_select_default() on these devices` | 6.1 的 device core 调的是 `pinctrl_bind_pins()`（`drivers/base/pinctrl.c:21`，由 `drivers/base/dd.c:616` 调用），它 `devm_pinctrl_get()`（`:32`）之后**先查 `"init"`、init 存在就优先应用 init 而不是 default**（`:47-56`），probe 结束后再由 `pinctrl_init_done()`（`core.c:1538-1560`，`dd.c:684` 调用）补上 default。`pinctrl_get_select_default()` 确实存在（`consumer.h:174`），但**不在这条路径上** | 03 Part A 的时间线按代码写成两跳（bind_pins → probe → init_done），文档那句一笔带过的说法在 03 §A 里被显式否掉 |

### §6.3 还没人处理的：源码 TODO 与本文档集的工具短板（⑰–⑲）

| # | 位置 | 是什么 |
|---|---|---|
| ⑰ | `core.c:2216` | 源码自陈的 TODO，在 `pinctrl_unregister()` 里，逐字：`/* TODO: check that no pinmuxes are still active? */`。意思是**卸载控制器时不检查还有没有活着的 mux**。加上 ① 的"拆除不复原硬件"，控制器模块卸载后引脚会停在最后一次 `set_mux()` 的状态上。整个核心 6.4k 行只有 ⑨ 和 ⑰ 两条自陈债，密度极低（§2.3 最后一条命令） |
| ⑱ | `scripts/check_doc_coverage.sh` | 在 pinctrl 上是**假阴性**：脚本按 `*pinctrl*` 找官方文档，而主文档叫 `pin-control.rst`（中间有连字符），于是"漏读官方文档"这个最该被抓住的错误它抓不到。本次是靠手工 `ls Documentation/driver-api/ \| grep -i pin` 找到的。**已在 §1.3 登记，规则 6 的检查不能只依赖这个脚本** |
| ⑲ | `scripts/check_line_refs.py` | 简写文件名按"文档里最常用的全路径"消歧，这个多数会随文档增长而**翻转**（§4.5 的 `pinmux.h`）。当前仍有一条 AMBIGUOUS：`core.c` 被解析成 `drivers/pinctrl/core.c`，另一候选是 `drivers/pinctrl/renesas/core.c`。本文档集里 renesas 只被引用一次（`:1371-1382`，且写了全路径），所以这条消歧是对的——但它**不是脚本保证的，是我核对过的** |

---

## §7 批量自检脚本的结果

三个脚本（skill `scripts/` 下，纯 stdlib）在**本文档集定稿时**的实际输出。命令：

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/pinctrl

python3 $S/check_xrefs.py     $D --ignore $D/.xref-ignore
python3 $S/check_line_refs.py $D . --ignore $D/.lineref-ignore
bash    $S/check_doc_coverage.sh pinctrl $D .
```

### §7.1 `check_xrefs.py`（跨篇 `0X §Y` 引用）

```
文档 10 篇；跨篇引用命中 334 条，失效 0 条（忽略项 2 条）
干净：所有跨篇引用都指向真实存在的章节。
```

两条忽略项都登记在 `.xref-ignore` 里并写了理由（源是 03 篇，目标分别是 06 和 07，章节号是 `§A7` 与 `§B7`）——都出自 03 §0 的「哪些内容不在本篇」去处表，那张表里那两个章节号指的是 **03 自己**的章节，脚本按"同一行里最近的文档名"错判给了同格里的目标篇。这种写法对读者有用，**不为迁就脚本而改**。

这个 334 里**含本篇自己的引用**（§3 逐篇取证表、§9 对照表都在引用别篇），所以修本篇的正文会让这个数字变动——定稿时重跑到收敛为止。

顺带一个自指陷阱：**本节如果照抄脚本的失效行原文，脚本下一轮会把这段引述本身再报一次**。所以上面两条只描述、不照抄格式。

全部轮次真抓到的 4 条（已修）：

| 位置 | 问题 | 修法 |
|---|---|---|
| 00 篇 `:154`、01 篇 `:257` | 指向本篇一个不存在的第 8 节，而当时本篇只有 §1–§7 | 改指 §2.3（那两处要的都是"全树普查的复现命令"） |
| 03 篇 `:105` | 指向本篇 §2 下一个当时不存在的小节 | **把那一节补写了出来**（即现在的 §2.4，时间线次序的验证方法）。03 要的那个内容确实该有一节，是我漏写了，不是引用写错 |
| 本篇 §9 初稿（4 条） | **引用的是"对方文档集"的章节号，脚本拿去本套里找**，于是报「06 里没有 §10 / §9 / §11」「07 里没有 §7」 | 改成「对方 NN 篇 §X」的写法（见 §9 开头的引用约定）。**没有登记进 `.xref-ignore`**——这不是假阳性，是我的写法有歧义：读者也会以为那是本套的章节 |

### §7.2 `check_line_refs.py`（`路径:行号` 是否越界）

```
扫描 844 条唯一 file:line 引用（消歧表 72 个基名 / 37 个前缀目录，忽略项 0 条）
== AMBIGUOUS（1 个简写文件名，树里有多个候选）==
  core.c → 按 drivers/pinctrl/core.c 校验；另有 drivers/pinctrl/renesas/core.c
干净：没有解析不到的文件，也没有越界行号
```

- **忽略项 0 条**：本文档集**没有**任何登记的行号假阳性
- **AMBIGUOUS 1 条**：见 §6 的 ⑲。renesas 在本文档集里只被引用一次且写了全路径，所以消歧结果是对的——但这是我核对的结论，不是脚本保证的

这一轮真抓到的 2 类（已修）：

| 问题 | 详情 |
|---|---|
| `pinmux.h` 的同名歧义 | 见 §4.5。08 写完后消歧的多数翻转，把 03/04 里潜伏的 `:121-168` 顶成"越界（实际只有 85 行）"。修法是 7 处全部改写全路径 |
| 计数被误当成行号 | §2.3 的 `EXPORT_SYMBOL` 计数原本用 `grep -c` 的原生输出格式写"某文件 0 个"（文件名紧跟冒号和数字），被脚本当成了行号引用，报"越界"。改成 `文件名 → 数字` 的写法 |

### §7.3 `check_doc_coverage.sh`（官方文档反向覆盖率）

```
=== 【必查】概念/框架文档 ===
=== 【参考】DT 绑定：共 98 篇，被引用 3 篇 ===
  pincfg-node（4 次）  pinctrl（3067 次）  pinmux-node（6 次）
【必查】共 0 篇，其中 0 篇 0 引用。
必查档全部有引用。
```

**这个"全部有引用"是假的**——「必查共 0 篇」意味着脚本**一篇官方文档都没找到**。原因见 §6 的 ⑱：它按 `*pinctrl*` 匹配，而主文档叫 `pin-control.rst`（中间有连字符）。**"0 篇 0 引用"和"0 篇"是完全不同的两件事，这个脚本的输出把二者混成了同一句"通过"。**

- 本次靠手工 `ls Documentation/driver-api/ | grep -i pin` 找到并读完了 `pin-control.rst`，产出 §6 的 ⑫⑬⑭⑮⑯ 五条发现
- "DT 绑定 98 篇"只统计了 `*.yaml`；该目录实际有 194 个文件（98 yaml + 96 txt，`find ... -type f | wc -l`）。这不影响结论——06 的硬要求是"每条属性都有定位到行的消费者"，不是"每篇绑定都被引用"
- 被引用的 3 篇里，`pinctrl` 那 3067 次是**假的**：脚本按去掉后缀的基名 `pinctrl` 做子串匹配，于是全文每一个 "pinctrl" 字样都算它一次。真正说明问题的是另两个数——`pinmux-node` 6 次、`pincfg-node` 4 次，都来自 §9 那轮新读的两篇 schema（06 §6.1.1）

**结论：规则 6（先读官方文档）不能依赖这个脚本自动兜底。** 每个子系统都要手工确认一遍 `Documentation/driver-api/` 和 `Documentation/<subsys>/` 下的真实文件名——名字里有连字符、下划线、缩写的都会漏。

---

## §8 本文档集的读法建议

按可靠性排序，读者可以按这个顺序决定"要不要自己再验一遍"：

| 可以直接用 | 建议自己再确认 | 别当结论用 |
|---|---|---|
| 贴了逐字源码的段落（全文最可靠的部分，见 §3 的证据等级） | 所有 `文件:行号`（小版本升级就会漂，用 §5.4 的字符串保底） | §5.3 那六条推断（尤其"无主窗口"和"能崩") |
| §2.3 里带命令的每个数字（定稿前逐格重跑过） | 05 的代码骨架（未编译，§5.1） | 07 里 debugfs/日志的**具体数值**（格式是真的，值是编的） |
| §6 的 ⑬⑭⑯（文档与代码不符，两边都给了行号） | 关于"控制器驱动通常怎么做"的群体特征（只做过 grep 统计，没读过任何一个驱动，§5.2） | 任何关于 6.2+ 或主线的推测（**本文档集没做过任何跨版本比对**） |

**发现错误时的最小复现路径**：先跑 §2.3 的对应命令；数字对不上就说明本文档集相对你的树已经过期（很可能是行号漂了），此时用 §5.4 说的"字符串重新定位"；如果字符串也找不到，那就是真的版本差异，以你的代码为准。

---

## §9 与另一套文档集的对照（skill 规则 29）

`output/qoder/pinctrl/`（10 篇 7710 行）是另一个工具就同一个子系统、同一棵树独立生成的文档集。按 skill 规则 29 的次序：**本套 10 篇先自己写完、自审通过、三个脚本全绿，然后才打开对方**。这个次序是硬要求——先看别人的会污染自己的取证链，之后再也分不清"这条是我验的还是我抄的"。

对照的判据只有一条：**对方是否指出了本套漏掉的事实，或者提供了更好的承载形式**。措辞差异、章节顺序差异、篇幅差异一律不算。

**本节的引用写法约定**：指对方文档时一律写成「对方 NN 篇 §X」，指本套文档时写成 `` `NN-xxx.md` §X ``。两种写法必须区分开——否则 `check_xrefs.py` 会拿对方的章节号去本套里找，报一堆假失效（本节初稿就踩了这个，4 条）。

### §9.1 吸收的 3 条

| # | 对方位置 | 吸收到哪 | 落地方式 |
|---|---|---|---|
| ① | 对方 06 篇 §10「DT Schema 验证（编译期）」 | **新增 `06-devicetree.md` §6.1.1** | 事实全部**重新自验**，不是搬运：`pinctrl.yaml` 45 行（只有 3 个属性 + `:45` `additionalProperties: true`）、`pinmux-node.yaml` 133 行（5 个属性名 + 3 个示例）、`pincfg-node.yaml` 153 行（27 个属性）都是自己读的；`make dt_binding_check` / `make dtbs_check` 两条命令自己确认过；额外加了一张对方没有的「schema 会拦 / 不会拦」表，结论是**schema 是语法层的第一道门，不是语义层的门**（group/function 名对不对它一概不管） |
| ② | 对方 02 篇 §5 提到 PREEMPT_RT | **改写 `02-datastructures.md` §7.3 那条 + 新增 `03-internals.md` §B1.5** | 本套原来写的是「PREEMPT_RT 上没有额外风险」——**这句话对核心成立、对驱动不成立**，属于过宽。改成两层：核心只有 mutex（六个核心文件 `grep raw_spin` 为空），驱动侧 18 个文件用 `raw_spinlock`（RT 上**不转换**、仍关抢占）。arm64 实证自己找的：`pinctrl-msm.c:70` 的 `raw_spinlock_t lock` + `msm_pinmux_set_mux()` 里 `raw_spin_lock_irqsave` 包住两次 MMIO |
| ③ | 对方 02 篇 §8「便捷宏汇总」 | **新增 `02-datastructures.md` §6.1** | 对方给的是宏 → 行号 → 展开的三列表；本套在自验时多查了"谁在用"，结果查出三条对方没有的结论：**`PIN_MAP_DUMMY_STATE` 全树 0 使用者**（但枚举值 `PIN_MAP_TYPE_DUMMY_STATE` 有 4 处特判）、`PINCTRL_PIN_ANON` 也是 0、`PINCTRL_PINGROUP`/`PINCTRL_PINFUNCTION` 的 5 个使用者**全在 x86/MIPS/arm32**（arm64 一个都没有）。另外补了"花括号初始化列表 vs 复合字面量"这个会直接编不过的形态区别 |

### §9.2 明确不吸收的 6 条（附理由）

不吸收 ≠ 对方写错了。多数是本套已经在别处覆盖，而且覆盖得更细。

| # | 对方位置 | 为什么不吸收 |
|---|---|---|
| ④ | 对方 05 篇 §5「近似符号辨析」 | 本套 `05-usage.md` §5「近邻符号区分」已经是独立一节，而且每组都给了「**错用会怎样**」（对方只给"区别是什么"）。skill 规则 20 要求的就是后者 |
| ⑤ | 对方 07 篇 §5「用户态工具」 | 本套 `07-debug.md` §5 是「pinctrl 没有的那些东西」，把"没有字符设备、没有专用工具、没有 tracepoint、没有 sysfs"作为**结论**写出来（并给了每条的 grep 依据），比"没有工具，所以介绍一下 shell 命令"更准。对方那一节 §5.2 小节的 shell 命令组合，本套 §6「一页速查」全部覆盖且多了 10 个观测点、debugfs 目录名 ≠ 控制器名的坑、以及三条最容易踩的陷阱 |
| ⑥ | 对方 07 篇 §7「排查路径」 | 本套的排查入口在 `07-debug.md` §0「分诊：按症状选工具」（在篇首而不是篇末，读者进门就用得上）+ §6 一页速查，另有 `06-devicetree.md` §5 决策树 / §7 错误消息映射 / §8 症状清单三节。分布在正确的篇里，不需要再加一节 |
| ⑦ | 对方 06 篇 §9「设备链接（device_link）关系」 | 本套 `05-usage.md` §1.7（在**控制器驱动**那一章，标题就是「`.link_consumers = true` 是干什么的」）已经讲了：`pinctrl_link_add()`（`core.c:1233-1240`）只在 `pctldev->desc->link_consumers` 为真时才建 link，两个标志的含义 + kerneldoc 原文（`pinctrl.h:145-148`）都在。而且它属于**驱动怎么写**（谁去置 `link_consumers`），不属于 DT 篇 |
| ⑧ | 对方 06 篇 §11「多 state 运行时切换的完整实例」 | 本套 `05-usage.md` §2 有完整消费者骨架（含 sleep state 的 map + PM 回调），§3 有分诊表；`06-devicetree.md` §1 讲 `pinctrl-names` 的写法。关键在于本套还多了一条对方没有的**返回值陷阱**：DTS 里没写 `"sleep"` 时 `pinctrl_pm_select_sleep_state()` **返回 0 且什么都不做**（`drivers/base/pinctrl.c:74`），"返回 0 = 切成功了"是误判（05 §2.4 ） |
| ⑨ | 对方 06 篇 §8「Hog 机制详解」 | 本套把 hog 拆到了三处并且更深：`03-internals.md` Part A 讲 `pinctrl_claim_hogs()` 在时间线的哪一点、`08-feynman.md` §7 讲"为什么 hog 要把 `dev` 传两次"、`99-evidence.md` §6 的 ⑤ 讲三处循环依赖特判导致 `-ENODEV` 有歧义且被 `:2076-2080` 吞掉、⑮ 讲官方文档把 hog 判据说窄了。这些对方都没有 |

### §9.3 这轮对照本身产生的两条方法论

1. **"对方有一节我没有"最常见的原因不是漏了，而是拆到了别的篇。** 判断"是不是真漏"的唯一办法是拿对方的**事实清单**（不是章节名）去 grep 自己的全套文档。上面 ⑥⑦⑧⑨ 四条都是这样排除的——按章节名比对会误判成 4 个缺口。
2. **对方最有价值的贡献不是"补了一节"，而是"逼我发现自己有一句话说宽了"。** ② 号那条 PREEMPT_RT 就是：对方只是提了一句 RT，本套原来的结论"没有额外风险"经不起追问——核心成立，驱动不成立。**先自己写完再对照的真正收益在这里**：如果先看了对方的，我大概会直接抄一句"驱动用 raw_spinlock"，而不会去查 18/34 这两个数字和 `msm_pinmux_set_mux()` 的实际临界区。

---

*本篇是 pinctrl 文档集（10 篇）的取证附录。前九篇的导航见 `00-overview.md` §8。*
