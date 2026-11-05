---
title: 取证附录
---

# input 子系统 · 取证附录

- 内核版本：Linux 6.1.158（`f6e38ae624cf`），目标架构 **arm64**
- 本篇不讲机制。它回答的是**「前面九篇凭什么这么说」**：哪些代码逐行读过、哪些只读了符号表、每个数字用哪条命令重跑、哪些结论是推断而不是观察
- 读者的正确用法：**看完某个结论觉得可疑时，回这里查它的证据等级**。一等证据可以直接用；二等要自己再确认一遍；`§5` 里列的东西请当成「作者也没验证」

---

## §0 本篇怎么用

| 你的疑问 | 去哪节 |
| --- | --- |
| 这套文档是在哪个树、哪个 commit 上写的？树干净吗？ | §1.1 / §1.2 |
| 为什么整套只讲 arm64？裁掉了什么？ | §1.3 / §2.7 |
| input 到底算哪种子系统？依据是什么？ | §1.4 |
| 我跑同一条命令数字不一样 | §1.5（`grep` 是 ugrep）/ §3.2（口径）/ §3.4（grep 陷阱） |
| 哪些文件是逐行读过的、哪些只扫了符号表 | §2.1 / §2.2 |
| 官方文档读了哪些、哪些没读 | §2.3 |
| 某个数字怎么重跑 | §3.3 |
| 某一篇的关键论断出处 | §4 |
| 哪些结论作者自己也没验证 | §5 |
| 代码里已知的坑（不是文档的坑） | §6 |
| 自审过程中被推翻过的假设 | §7 |
| 这套文档自己的缺陷统计 | §8 |
| 我想从头复核一遍 | §9 |

---

## §1 基线

### §1.1 版本与树状态

```bash
git log -1 --format='%H%n%s%n%ad' --date=short
```

```
f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe
Linux 6.1.158
2025-10-29
```

树**不干净**：

```bash
git status --porcelain | wc -l          # → 178
```

178 个改动文件来自一份 **PREEMPT_RT 回移补丁**（改的是 `arch/arm64/include/asm/preempt.h`、`arch/arm/kernel/entry-armv.S`、`drivers/gpu/drm/i915/`、`drivers/net/ethernet/` 等）。

### §1.2 RT 补丁对 input 的影响：零

这一步是**定性前提**，不是形式检查——如果 input 被补丁改过，整套文档描述的就不是上游 6.1.158。判据是下面这条命令**输出为空**：

```bash
git status --porcelain -- drivers/input include/linux/input.h include/linux/input/ \
    include/uapi/linux/input.h include/uapi/linux/input-event-codes.h \
    include/uapi/linux/uinput.h
# → 空
```

七个路径全部无输出，所以：

| 论断 | 成立范围 |
| --- | --- |
| 本文档集引用的每一行 `drivers/input/` 代码都是**上游 6.1.158 原样** | 全篇 |
| 引用的 `include/linux/input*.h`、`include/uapi/linux/input*.h`、`uinput.h` 同上 | 全篇 |
| 引用到 **input 之外**的行号（`drivers/tty/vt/keyboard.c`、`net/rfkill/input.c`、`sound/usb/mixer_quirks.c`、`drivers/platform/x86/*`、`arch/arm64/boot/dts/*`、各 `Kconfig`） | ★ 有**一个文件**受补丁影响，见下 |

★★ **这一步不能只查 input 目录就收工**，因为 handler 的实现方散在 tty / macintosh / net / sound 里。把全套文档引用过的每个树内路径与改动集求交集：

```bash
python3 - <<'PY'
import re,glob,subprocess
pat=re.compile(r'\b((?:arch|drivers|include|net|sound|kernel|lib|fs|Documentation)/[A-Za-z0-9_./+-]+\.(?:c|h|S|rst|yaml|txt|dts|dtsi))')
refs={m.group(1) for f in glob.glob('output/input/*.md') for line in open(f) for m in pat.finditer(line)}
dirty={l[3:].strip() for l in subprocess.run(['git','status','--porcelain'],
        capture_output=True,text=True).stdout.splitlines()}
print(len(refs), "个被引用的树内文件；其中在改动集里的：")
for p in sorted(refs & dirty): print("  ", p)
PY
```

```
162 个被引用的树内文件；其中在改动集里的：
   arch/arm/kernel/entry-armv.S
   arch/arm64/include/asm/preempt.h
   drivers/tty/sysrq.c
```

前两个只在本篇 §1.1 里作为「补丁改了什么」被提到，**没有引用它们的任何行号**，无影响。第三个有影响，而且是本套文档里**唯一一处行号与上游不一致**的地方：

| 项 | 本树（打了补丁） | 上游 6.1.158 原样 | 偏移 |
| --- | --- | --- | --- |
| `sysrq_handler` 定义 | `drivers/tty/sysrq.c:1029-1035` | `:1027-1033` | +2 |
| `.filter = sysrq_filter` | `:1030` | `:1028` | +2 |
| `input_register_handler(&sysrq_handler)` | `:1043` | `:1041` | +2 |

原因：补丁在 `__handle_sysrq()` 里插了两行 `printk_prefer_direct_enter()` / `printk_prefer_direct_exit()`（`git diff -U0 -- drivers/tty/sysrq.c` 显示 hunk 在 `@@ -584,0 +585 @@` 和 `@@ -625,0 +627 @@`，都在上述引用之前），所以后面所有行整体下移 2。

**处置：不改文档。** 全套行号都是对着**本树**写的，`check_line_refs.py` 也是对着本树校验的；改成上游数字会让脚本和读者手里的树都对不上。带行号的引用共 **6 处**（`00-overview.md:113`、`:720` 各一个 `:1043`；`02-datastructures.md:401` 的 `:1029-1035`、`:410` 的 `:1030`；`03-internals.md:254` 的 `:1029-1035`；`05-usage.md:854` 的 `:1030`），拿到纯净 6.1.158 上核对时**统一减 2**。另有 `03-internals.md:941` 只写了文件名没写行号，不受影响。重跑这份清单：`grep -rn 'sysrq\.c:' output/input/`。`sysrq_filter()` 函数本体和 `sysrq_ids[]` 的内容补丁一个字都没改，所以**语义结论全部不受影响**，只有行号数字要平移。

### §1.3 arm64-only 声明

整套文档只讲 arm64。这不是「写完再删」——它改变了三件事：

| 影响面 | 具体做法 |
| --- | --- |
| **读哪些文件** | `i8042.c`（1675 行）+ `i8042-acpipnpio.h`（1823 行）**没读**；`drivers/input/keyboard/applespi*.c`（x86 only）只读了 tracepoint 与 debugfs 部分；ACPI 发现路径（17 个文件）不覆盖 |
| **统计怎么做** | DTS 举例一律取自 `arch/arm64/boot/dts/`；「arm64 上有几个」这类数字都带 arm64 过滤条件；`select ARCH_MIGHT_HAVE_PC_SERIO` / `SYS_SUPPORTS_APM_EMULATION` 这类 Kconfig 断言都跑了全 `arch/` 再确认没有 arm64 |
| **举什么例子** | 代表驱动全部选 arm64 上真会加载的（`gpio_keys`、`goodix`、`edt-ft5x06`、`cros_ec_keyb`、`ambakmi`），不选 `atkbd`/`psmouse` |

裁掉的东西逐条记在 **§2.7**，不要当成遗漏。

### §1.4 子系统类型判定：**框架型**

skill 要求判定分两道门，只跑第一道会把 virtio 这类误判成硬件总线型。两道门的**实际输出**：

**门 1 —— 有没有自己的 `bus_type`？**

```bash
grep 'struct bus_type' drivers/input/*.c include/linux/input*.h
# → 空
```

空输出 ⇒ 框架型或协议型。**门 2 不必再跑**（门 2 只在门 1 有输出时才有意义）。

但这里必须补一个宽口径，否则读者一递归就会看到五行而以为门 1 判错了：

```bash
grep -rn 'struct bus_type' drivers/input/ include/linux/input.h include/linux/input/
```

```
drivers/input/serio/serio.c:1010:struct bus_type serio_bus = {
drivers/input/gameport/gameport.c:42:static struct bus_type gameport_bus;
drivers/input/gameport/gameport.c:786:static struct bus_type gameport_bus = {
drivers/input/rmi4/rmi_bus.h:188:extern struct bus_type rmi_bus_type;
drivers/input/rmi4/rmi_bus.c:347:struct bus_type rmi_bus_type = {
```

**五行里没有一行叫 `input_bus_type`。** 三条走廊分别属于三个**子框架**（serio、gameport、rmi4），跑在上面的是「端口」和「RMI4 设备」，**不是 `input_dev`**。`input_allocate_device()` 只填 `dev->dev.type` 和 `dev->dev.class`（`drivers/input/input.c:2017-2018`），一个字节都没碰 `dev->dev.bus`。

两个口径都出现在正文里，位置和用途不同，**不要互相核对**：

| 口径 | 出现在 | 输出 | 用途 |
| --- | --- | --- | --- |
| 窄（只扫核心 `.c` + `include/linux/input*.h`） | `02-datastructures.md` §1.0 | 空 | 证明「input 自己没有总线」 |
| 宽（递归子目录 + `include/linux/input/`） | `08-feynman.md`（`/sys/bus/` 那一节） | 5 行 | 把三个子框架的走廊摆出来对照 |

判定结果对模板的影响（这是判定的**用处**，不是形式）：

| 模板项 | 框架型走哪条 | 落在哪 |
| --- | --- | --- |
| 00 §2 背景 | 路径 B（问题背景），不是硬件背景 | `00-overview.md` §2 |
| 02 关系图 | 画「核心抽象 ↔ 消费者」，不画「控制器在父总线 / 设备在子系统总线」 | `02-datastructures.md` §1.0 + §2 |
| 03 主轴 | 「注册 / 匹配 / 事件分发 / 拆除」，不是「传输」 | `03-internals.md` Part A |
| 04 | **不并入 03**——input 有四个真子框架（serio / gameport / rmi4 / ff）外加三个字符设备 handler | `04-extensions.md`（2043 行，全套最长的一篇之一） |
| 06 | DT 绑定**存在**（56 篇），但只占发现路径的一部分，所以用分诊表开篇 | `06-devicetree.md` §0 |
| 99 §5 | **不需要**第五层「对端不在本树」——input 没有跨边界对端 | §5.5 |

★ input 是个「没有自己的总线、但底下挂了三条别人的总线」的框架型子系统。这个形状在四种类型里没有现成的名字，是本次建档最需要一开始就说清的一件事——`02-datastructures.md` §1.0 和 `08-feynman.md` 各用一节专门否掉「`/sys/bus/input/` 应该存在」这个错误预期。

### §1.5 ★★ 工具与环境：本套文档的 `grep` 不是 GNU grep

这一条排在这么前面，是因为它决定**读者能不能重跑出同样的数字**。

| 工具 | 版本 | 备注 |
| --- | --- | --- |
| `grep`（PATH 里的） | **ugrep 7.5.0** | ★★ 见下 |
| `/bin/grep` | GNU grep 3.7 | 需要 GNU 语义时显式写全路径 |
| `rg` | ripgrep 13.0.0 | 只用于导航，正文里的复现命令一律用 `grep` |
| `python3` | 3.10.12 | 三个自检脚本 + 本篇几段一次性脚本 |
| `git` | 2.34.1 | |
| `awk` | GNU Awk 5.1.0 | `06-devicetree.md` 的 YAML 属性计数、区间提取 |
| `perl` | 5.34.0 | `06-devicetree.md` §6 的跨行 `rmi_of_property_read_u32(` 提取（`-0777`） |
| 宿主 | `Linux 6.5.0-14-generic x86_64` | ★ **写文档的机器不是 arm64**；全套结论来自读码，不是在 arm64 上跑出来的（见 §5.1） |

★★ **`grep` 在本会话里是一个 shell 函数**，它把调用转给 ugrep，并且默认带上 `--ignore-files --hidden -I --exclude-dir=.git`（`type grep` 可以看到函数体）。三个后果：

1. `--ignore-files` 让它**遵守 `.gitignore`**。本树**没有编译过**（`find . -name '*.o' -not -path './.git/*' | wc -l` → 0，也没有 `.config`），所以 `.gitignore` 实际没有排除掉任何被统计的文件——这是「两者数字相同」能成立的前提，换一棵编译过的树就不一定了。
2. `-I` 跳过二进制文件。本文档集所有统计都带 `--include=*.c` / `*.h` / `Kconfig`，不受影响。
3. 正则方言可能有细微差别（函数里传了 `-G`，即基本正则）。

所以定稿前把**九组关键数字**在两个 grep 上各跑一遍对账：

```bash
G=/bin/grep      # GNU grep 3.7；不写全路径的 grep 是 ugrep
# 逐组把 grep 换成 $G 再跑一次，比对输出
```

| # | 统计 | ugrep 7.5.0 | GNU grep 3.7 | 一致 |
| --- | --- | --- | --- | --- |
| 1 | `input_register_device(` 行数 / 文件数（排除 `input.c`） | 474 / 412 | 474 / 412 | ✅ |
| 2 | `touchscreen_parse_properties(` 调用点（§3.4 的两道排除） | 36 | 36 | ✅ |
| 3 | `^EXPORT_SYMBOL_GPL` 宽口径（含所有子目录） | 54 | 54 | ✅ |
| 4 | `of_device_id` / `acpi_device_id` 文件数 | 138 / 17 | 138 / 17 | ✅ |
| 5 | `\binput_mt_init_slots\(` 文件数 / 行数（`-E` + `\b`） | 64 / 74 | 64 / 74 | ✅ |
| 6 | 核心 7 文件 `-EINVAL`（`-how`） | 33 | 33 | ✅ |
| 7 | `select INPUT` 行数 / 文件数（`-E` + `[[:space:]]`） | 6 / 5 | 6 / 5 | ✅ |
| 8 | `\bserio_register_port\(` 文件数 / 行数 | 30 / 33 | 30 / 33 | ✅ |
| 9 | `\bsparse_keymap_setup\(` 文件数 / 行数 | 24 / 27 | 24 / 27 | ✅ |

★ 九组全部一致，**覆盖了本文档集用到的全部 grep 花样**（`-r` / `-l` / `-n` / `-c` / `-w` / `-o` / `-h` / `-E` + `\b` / `[[:space:]]` / `--include` / 管道二次过滤）。这是一次**抽样**而不是穷尽：如果你在别处遇到差异，先看你的树是不是编译过（第 1 点）。

### §1.6 Documentation 登记：读了什么、没读什么

反查脚本的口径与输出：

```bash
bash ~/.claude/skills/kernel-subsystem-docs/scripts/check_doc_coverage.sh input output/input .
# 【必查】共 33 篇，其中 17 篇 0 引用
# 【参考】DT 绑定：共 56 篇，被引用 12 篇
```

★★ **逐篇的「引用 N 次」是假的，必须先说破**：脚本按文件名 token 去正文里数出现次数，而 `input` 和 `ff` 这两个名字**是普通英文/缩写**——`Documentation/input/input.rst` 报的是四位数、`ff.rst` 报的是三位数，数的其实是全套文档里所有出现 `input` 和 `ff` 字样的地方。同理偏高的还有 `event-codes`、`uinput`、`input-programming`、`multi-touch-protocol`（方向可信、绝对值不可信）。真实的按路径引用只有 §2.3 表里点名的那几处。

★ 这里**故意不抄那几个具体数字**：它们统计的是本文档集自己的文本，所以每改一次文档就会变，抄进来的当天就过期（写这一节的过程中它们就变了一轮）。可信的只有前两行的 **33 / 17** 和 **56 / 12** ——那是文件数，不是词频。

逐篇处置见 **§2.3**。

---

## §2 证据分层

分层的用处是让读者知道**哪些结论可以直接用、哪些要自己再确认一遍**。分界线本身也要给理由，所以每一等都写了「凭什么算这一等」。

### §2.1 一等证据：逐行读过，正文引用了具体行号

**判据**：整个文件（或明确标出的行区间）被打开读过，正文里出现的行号是从文件里读出来的、不是推的。这一等的结论**可以直接用**。

**核心 16 个 `.c`（9446 行）——全部一等：**

| 文件 | 行数 | 读到什么程度 | 主要落在哪篇 |
| --- | --- | --- | --- |
| `drivers/input/input.c` | 2770 | **逐行**。事件分发（`:63-130`）、`input_event()` 全链（`:350-430`）、注册/匹配/拆除（`:2400-2600`）、sysfs 属性组、procfs、autorepeat、inhibit、次设备号分配都读了函数体 | `03-internals.md` Part A/B 全部 |
| `drivers/input/evdev.c` | 1446 | **逐行**。client 环、`SYN_DROPPED`、25 个 ioctl case、时钟选择、GRAB/REVOKE | `04-extensions.md` §1、`05-usage.md` §9 |
| `drivers/input/input-mt.c` | 538 | **逐行**。slot 分配、`input_mt_assign_slots()` 的匈牙利算法、指针模拟 | `03-internals.md` B4、`05-usage.md` §3 |
| `drivers/input/ff-core.c` | 377 | **逐行**。effect 上传/擦除/播放、owner 记账、六个导出符号 | `04-extensions.md` §3、`05-usage.md` §5 |
| `drivers/input/ff-memless.c` | 553 | **逐行**。定时器状态机、效果合成、`ml_play_effects()` | 同上 |
| `drivers/input/mousedev.c` | 1125 | **逐行**（含 4 个 `module_param`） | `04-extensions.md` §1 |
| `drivers/input/joydev.c` | 1098 | **逐行**（含 `joydev_match()`、14 个 ioctl case） | `04-extensions.md` §1 |
| `drivers/input/sparse-keymap.c` | 294 | 逐行 | `01-api.md` §5、`04-extensions.md` §9 |
| `drivers/input/input-poller.c` | 222 | 逐行（含三个 sysfs 属性的 0644/0444 权限位） | `05-usage.md` §2 |
| `drivers/input/input-leds.c` | 220 | 逐行（含那个空 `.event` 回调） | `04-extensions.md` §1 |
| `drivers/input/touchscreen.c` | 207 | 逐行 | `06-devicetree.md` §1 |
| `drivers/input/matrix-keymap.c` | 202 | 逐行 | `06-devicetree.md` §4、`05-usage.md` §4 |
| `drivers/input/input-compat.c` | 133 | 逐行 | `04-extensions.md` §4 |
| `drivers/input/apm-power.c` | 122 | 逐行 | `04-extensions.md`（arm64 上不可用，仍读完） |
| `drivers/input/evbug.c` | 100 | 逐行 | `07-debug.md` §2 |
| `drivers/input/vivaldi-fmap.c` | 39 | 逐行 | `01-api.md` §5 |

**头文件（892 行核心 + 1726 行 uapi）：**

| 文件 | 行数 | 读到什么程度 |
| --- | --- | --- |
| `include/linux/input.h` | 580 | **逐行**，包括 `struct input_dev` 每个字段的注释 |
| `include/linux/input/mt.h` | 129 | 逐行 |
| `include/linux/input/matrix_keypad.h` | 92 | 逐行（含 `MATRIX_MAX_ROWS/COLS` = 32，`:9-10`，和两个 `KEY()` 宏的位宽差异） |
| `include/linux/input/sparse-keymap.h` | 59 | 逐行 |
| `include/linux/input/touchscreen.h` | 32 | 逐行 |
| `include/linux/input/vivaldi-fmap.h` | 27 | 逐行 |
| `include/uapi/linux/input.h` | 518 | **逐行**，含 `struct input_event` 的三分支时间戳布局（`:28-47`）和全部 `EVIOC*` |
| `include/uapi/linux/uinput.h` | 232 | **逐行**，含 23 个 `UI_*` ioctl |
| `include/uapi/linux/input-event-codes.h` | 976 | ★ **不是逐行**：读了全部**码段边界**（`EV_*`、各 `*_MAX`、`BTN_*` 分组、`KEY_BRL_DOT*`），**没有**逐个码点核对语义。所以「某个具体 `KEY_xxx` 该配哪个物理键」这类问题本文档集不回答（官方规范是 `Documentation/input/gamepad.rst` 和 `event-codes.rst`） |
| `include/linux/serio.h` | 164 | 逐行（三个角色的字段与回调） |
| `include/linux/gameport.h` | 216 | 读了 `struct gameport` / `struct gameport_driver` 全部字段，跳过了内联的端口读写宏 |
| `drivers/input/input-compat.h` | 78 | 逐行 |
| `drivers/input/input-core-private.h` | 16 | 逐行 |
| `drivers/input/input-poller.h` | 18 | 逐行 |

**四个子框架的核心文件（一等）：**

| 文件 | 行数 | 读到什么程度 | 落在 |
| --- | --- | --- | --- |
| `drivers/input/serio/serio.c` | 1049 | **逐行**：`serio_bus`、事件工作队列五种事件、两级 `manual_bind`、两个属性组、`serio_open/write/interrupt` | `04-extensions.md` §2 |
| `drivers/input/gameport/gameport.c` | 855 | **逐行**：`gameport_bus`、事件队列、速度测量 | `04-extensions.md` §12 |
| `drivers/input/rmi4/rmi_bus.c` | 478 | **逐行**：`rmi_bus_type`、function handler 注册、`rmi_of_property_read_u32()`（含 `:422-423` 的不可达分支） | `04-extensions.md` §5 |
| `drivers/input/rmi4/rmi_bus.h` | 199 | 逐行（`RMI_DEBUG_*` 只在这个**私有**头文件里，是 §6 一条设计债的依据） | 同上 |
| `drivers/input/misc/uinput.c` | 1117 | **逐行**：16 项环、`UI_*` ioctl、`uinput_inject_events()` | `04-extensions.md` §4 |

**代表驱动（一等，选法见 §2.2）：**

| 文件 | 行数 | 读到什么程度 |
| --- | --- | --- |
| `drivers/input/keyboard/gpio_keys.c` | 1077 | 详读：`disabled_keys` sysfs、DT 解析、`input_register_device()` 前后 |
| `drivers/input/touchscreen/edt-ft5x06.c` | 1515 | 详读 debugfs 部分（`mode`/`raw_data`/`num_x`/`num_y`）+ DT 属性消费 + `:878` 那条错误注释 |
| `drivers/input/serio/ambakmi.c` | 210 | **逐行**（arm64 上唯一有 DTS 在用的 serio 端口驱动；`:61` 返回 `SERIO_TIMEOUT` 那条设计债在这里） |
| `drivers/input/rmi4/rmi_f3a.c` | 241 | **逐行**（`05-usage.md` §8 的 function 驱动骨架照它写的） |
| `drivers/input/keyboard/applespi_trace.h` | 93 | **逐行**（全子系统唯一的 `TRACE_EVENT` 文件，用来给出「arm64 tracepoint 覆盖 = 0」这个结论） |

### §2.2 二等证据：只读了符号表 / 只读了单个函数

**判据**：没有整文件读完，只在正文需要的那个点上打开确认过；或者只用 `grep` 确认了符号存在与调用形态。这一等的结论**方向可信、细节要自己再确认**。

| 对象 | 规模 | 读了什么、没读什么 |
| --- | --- | --- |
| `drivers/input/*/` 九个子目录 | 389 个 `.c` / 约 20.5 万行 | ★ **只讲契约**：统计谁调了哪个核心 API（§3.3 的 21 行使用统计表），逐个打开只发生在「需要点名某一行」时。**没有**读过任何一个驱动的完整概率逻辑 |
| `drivers/input/keyboard/applespi.c` | 1970 | 只读 debugfs 与 tracepoint 相关段落 + `drivers/input/keyboard/Kconfig:77` 的 `depends on X86 \|\| COMPILE_TEST`。**没读**它的 SPI 协议实现 |
| `drivers/input/touchscreen/goodix.c` | 1583 | 只读 DT 属性消费与 `touchscreen_parse_properties()` 调用点 |
| `drivers/input/keyboard/cros_ec_keyb.c` | 780 | 只读注册路径与 `linux,keymap` 消费 |
| `drivers/input/rmi4/rmi_driver.c` | 1283 | 只读 `.attention` 调用链与 function handler 分发；**没读**F11/F12 的手指数据解析 |
| `drivers/input/serio/userio.c` | 285 | ★ **只登记了存在与边界**（misc 设备号、ABI 头、与 `uinput` 的分工），**没有**读它的 285 行 |
| `drivers/tty/vt/keyboard.c` | — | 只读了 `kbd_handler` 定义（`:1643-1651`）、`kbd_match()`（`:1552-1567`）、`kbd_start()`（`:1617-1625`）、五个 notifier 调用点。**没读** keysym 翻译 |
| `drivers/tty/sysrq.c`、`drivers/macintosh/mac_hid.c`、`net/rfkill/input.c`、`sound/usb/mixer_quirks.c`、`drivers/tty/serial/kgdboc.c` | — | 只读各自的 `input_handler` 定义块和它填的回调本体 |
| `drivers/platform/x86/*`（`sparse_keymap_*` 的 20 个消费者） | — | ★ **只数了数**，一个都没打开——x86 路径，`§1.3` 已声明不覆盖 |

**代表驱动的选法**（这是 §2.1 最后那张表为什么选那五个）：

| 选择条件 | 选中的 | 理由 |
| --- | --- | --- |
| 最简实现 | `drivers/input/serio/ambakmi.c`（210 行） | 一个端口驱动能有多短，它是下限；而且是 arm64 上唯一**本树 DTS 真的在用**的 serio 端口（`arch/arm64/boot/dts/arm/juno-motherboard.dtsi:252`、`:260`；`rtsm_ve-motherboard.dtsi:154`、`:162`） |
| arm64 最常见 | `drivers/input/keyboard/gpio_keys.c`（1077 行） | `CONFIG_KEYBOARD_GPIO=y` 在 `arch/arm64/configs/defconfig:408`，且 DT 绑定被引用 41 次（全部绑定里最多） |
| 全功能 + 调试面最全 | `drivers/input/touchscreen/edt-ft5x06.c`（1515 行） | 全子系统 **2 个 debugfs 提供者之一**，另一个（`applespi.c`）在 arm64 上编不出来——所以它是 arm64 上**唯一**的 debugfs 样本 |
| 子框架代表 | `drivers/input/rmi4/rmi_f3a.c`（241 行） | rmi4 的 function 驱动里最短的一个完整实现，够当骨架 |
| 反例（明知不可用仍读） | `drivers/input/keyboard/applespi_trace.h`（93 行） | ★ **必须读**才能说「arm64 上 tracepoint 覆盖 = 0」这句话——不读就只是猜 |

★ 没有选 `psmouse`/`atkbd`/`synaptics`：它们是 x86 PS/2 路径，`§1.3` 已裁。它们在正文里出现时一律只作为「这个框架长什么样」的例子。

### §2.3 三等证据：`Documentation/` —— 读了哪些、哪些明确不读

**判据**：官方文档给的是**设计意图和已知陷阱**，这些从代码里读不出来，所以必读；但它**会落后于代码**，所以凡冲突以代码为准，并在正文显式指出。

`Documentation/input/` 共 33 篇「必查」档（脚本口径含 `Documentation/driver-api/input.rst`）。逐篇处置：

| 官方文件 | 行数 | 处置 | 落在哪 |
| --- | --- | --- | --- |
| `input/input.rst` | 281 | **读完**。★ 与代码有 2 处冲突（`struct input_event` 早期布局、"必须先加载"），逐条记在 `03-internals.md` C2 | `03-internals.md` A1、C2 |
| `input/input-programming.rst` | 348 | **读完**。★ 与代码有 3 处冲突（`absmin[]` 四数组已不存在、`idtype` 字段不存在、`event()` 约束比文档更严），同上 | `03-internals.md` A3、B1.3、C2 |
| `input/event-codes.rst` | 453 | ★ **只读了需要的段落**：`SYN_REPORT`/`SYN_DROPPED` 语义（`:97`、`:101-102`、`:116`、`:143`）、`EV_ABS` vs `EV_REL` 的区分、`INPUT_PROP_*`。**没有**逐个码点读完 | `04-extensions.md` §1、`08-feynman.md` |
| `input/multi-touch-protocol.rst` | 415 | **读完**。MT-A/MT-B 的区别、`input_mt_sync_frame()` 的契约、`ABS_MT_TOOL_TYPE` | `03-internals.md` B4、`05-usage.md` §3 |
| `input/ff.rst` | 265 | **读完**。effect 生命周期、`FF_*` 能力位、`gain`/`autocenter` | `04-extensions.md` §3、`05-usage.md` §5 |
| `input/uinput.rst` | 245 | **读完**。三种创建方式（老 `UI_SET_*` / `UI_DEV_SETUP` / `UI_ABS_SETUP`） | `04-extensions.md` §4 |
| `driver-api/input.rst` | 42 | **读完**（纯聚合页）。★ 它 `:export:` 的 5 个 `.c` 是「官方认定的核心」，与本套 §0.2 的核心表比对写在 `01-api.md` §0.1 | `01-api.md` §0.1 |
| `input/input_kapi.rst` | 17 | **读完**（纯 toctree） | `01-api.md` §0.1 |
| `input/input_uapi.rst` | 22 | **读完**（纯 toctree） | `01-api.md` §0.1 |
| `input/index.rst` | 19 | **读完**（纯 toctree） | — |
| `input/notifier.rst` | 55 | **读完**。★ 结论是**边界外**：它讲的是 tty/vt 自己的 `keyboard_notifier_list`，不是 input 核心的机制。登记 + 边界声明写在 `04-extensions.md` §1 | `04-extensions.md` §1 |
| `input/gameport-programming.rst` | 233 | **读完**（`cooked`/`raw` 两种读法、`fuzz`、`speed`、`calibrate`） | `04-extensions.md` §12 |
| `input/userio.rst` | 85 | ★ **只读了前言**（`/dev/userio` 是什么、与 `uinput` 的分工）。**没读**它的协议细节 | `04-extensions.md` §2.4 |
| `input/gamepad.rst` | 197 | ★ **明确不覆盖**：它规定的是 `BTN_SOUTH`/`BTN_EAST` 这类**码点语义约定**（哪个码对应手柄上哪个物理键），属于 uapi 命名规范而非子系统机制。登记在 `04-extensions.md` §1 | `04-extensions.md` §1 |
| `input/joydev/joystick-api.rst` | 348 | ★ **明确不覆盖**：`/dev/input/jsN` 的用户态 ABI。本套只覆盖 joydev 作为 handler 的那一面 | `04-extensions.md` §1 |
| `input/joydev/joystick.rst` | 586 | ★ **明确不覆盖**：硬件与历史 | 同上 |
| `input/devices/*.rst`（**18 个文件**，5542 行） | 5542 | ★★ **明确不覆盖，18 篇一篇没读**。理由见下 | — |

★★ **18 篇 `devices/*.rst` 的处置理由要写清楚，否则读者会当成漏读。** 它们是**单个设备型号**的说明（`alps`、`elantech`、`sentelic`、`bcm5974`、`appletouch`、`xpad`、`yealink`、`ntrig`、`atarikbd`、`amijoy`、`cs461x`、`joystick-parport`、`walkera0701`、`iforce-protocol`、`pxrc`、`cma3000_d0x`、`rotary-encoder`、`edt-ft5x06`），描述的是**某一颗芯片的协议**，不是 input 子系统的机制。而且其中 13 篇是 x86 PS/2 或 ISA/并口老硬件，已被 §1.3 裁掉。**唯一一篇本该读的是 `devices/edt-ft5x06.rst`**——`edt-ft5x06.c` 是本套选中的代表驱动之一：

```bash
wc -l Documentation/input/devices/edt-ft5x06.rst    # 引用 0 次
```

★ 这是本套文档集一条**真实的欠债**（不是「覆盖了没标」，是真没读）：`07-debug.md` §5 讲 `edt_ft5x06` 那四个 debugfs 文件时是从**代码**读出来的语义，没有拿官方那篇对账。它可能记录了「`mode` 该怎么用、`raw_data` 的字节布局」这类代码里读不出来的东西。补读动作登记在 §5.3。

**脚本反查的落地结果**：第一次跑 `check_doc_coverage.sh` 时【必查】33 篇里有 **27 篇 0 引用**。逐条处理后降到 **17 篇**，剩下的 17 篇全部落在上面那一整行 `devices/*.rst` 里（处置 = 明确不覆盖）。中间修掉的 10 篇分两类：

★ 注意 `devices/` 下一共 18 个文件，但脚本现在只把 **17** 个报成 0 引用——差的那一个是 `devices/edt-ft5x06.rst`：上一段把它作为**明确的欠债**点名了，于是它有了引用。**「被引用」在这里等于「被登记为没读」，不等于「读了」**。这正是脚本口径的边界：它数的是文档里有没有提到这个路径，判断不了提到它是为了引用内容还是为了承认没读。

| 类 | 篇数 | 处置动作 |
| --- | --- | --- |
| (b) 覆盖了没标出处 | 4 | `driver-api/input.rst`、`input_kapi.rst`、`input_uapi.rst` → 在 `01-api.md` §0.1 新增一张三行登记表；`gameport-programming.rst` → 在 `04-extensions.md` §12 篇首加官方原文指针 |
| (a) 内容真没覆盖 | 5 | `notifier.rst` → `04-extensions.md` §1 新增一段（notifier 链的 5 个事件码 + 5 个调用点 + 2 个消费者 + 边界声明）；`userio.rst` → `04-extensions.md` §2.4 新增 `USERIO` 一行 + 两段（★ 顺带补上了原表里 5 个错的 `——` 依赖格）；`gamepad.rst` / `joydev/joystick-api.rst` / `joydev/joystick.rst` → 各写明「明确不覆盖 + 理由」 |
| ★ (c) 真没读、登记成欠债 | 1 | `devices/edt-ft5x06.rst` → 本节上一段 + §5.3 第 7 条。**这一篇不是修好了，是承认了**——它从「0 引用」变成「有引用」只是因为被点名，覆盖率一点没涨 |

★★ **`userio.rst` 这一条最值回票价**：为了给 `USERIO` 补一行，回去核 `drivers/input/serio/Kconfig` 才发现原表里 `SERIO_ALTERA_PS2`、`SERIO_ARC_PS2` 两格写的是 `——`（欠债记号，规则 19），实际都有 `depends on HAS_IOMEM`（`:215`、`:248`）；`SERIO_APBPS2` 写的 `OF` 其实是 `OF && HAS_IOMEM`（`:258`）；`SERIO_SUN4I_PS2` 漏了 `|| COMPILE_TEST`（`:288`）。**官方文档的反查在这里抓出的是代码事实错误，不是文档引用错误**——这是 `check_doc_coverage.sh` 的意外用途。

### §2.4 四等：设备树绑定

| 口径 | 数字 | 命令 |
| --- | --- | --- |
| `Documentation/devicetree/bindings/input/` 顶层条目 | 78 | `ls Documentation/devicetree/bindings/input/ \| wc -l` |
| 其中 `touchscreen/` 子目录 | 49 | `ls Documentation/devicetree/bindings/input/touchscreen/ \| wc -l` |
| `rmi4/` 子目录 | 4 个文件 | `ls Documentation/devicetree/bindings/input/rmi4/` |
| 脚本口径下的绑定总数 / 被引用数 | 56 / 12 | `check_doc_coverage.sh` |

**读完的绑定文件**：`input.yaml`（7 个属性逐个查了消费者数）、`touchscreen/touchscreen.yaml`（17 个属性，★ 含 `touchscreen-y-plate-ohms` 这个**零消费者的死字**）、`matrix-keymap.yaml`、`gpio-keys.yaml`、`rmi4/*.txt` 四篇 + `rmi_2d_sensor.txt`。

★ 06 篇的硬要求是**每条属性都有定位到行的消费代码**，不是每篇绑定都被引用——所以「56 篇里只引了 12 篇」不是缺陷。剩下 44 篇是**单个触摸屏型号**的绑定，它们的私有属性由各自驱动消费，不经过 input 核心。

### §2.5 索引工具（cbm）的盲区

cbm（codebase-memory MCP）**只用于导航**：找候选符号、看调用关系、定位文件。**一条正文里的行号都不是从它取的**，全部来自 `grep -n` / `sed -n` 的实际输出。

理由是它有两个结构性盲区，恰好是内核的两大主力机制：

| 盲区 | 本子系统里的实例 | 后果 |
| --- | --- | --- |
| **宏生成的符号** | ★ `INPUT_GENERATE_ABS_ACCESSORS`（`include/linux/input.h`）一个宏生成 **12 个 inline 函数**（`input_abs_get_val/set_val` × `min`/`max`/`fuzz`/`flat`/`res` 等组合），索引里**一个都没有** | 只靠索引会得出「这些函数不存在」的结论。`01-api.md` §4 那张表是读宏本体展开出来的，记在 `01-api.md`（原文位置 §4 的宏说明段） |
| **函数指针间接调用** | input 的**全部**分发都是函数指针：`handler->events` / `handler->event` / `handler->filter`（`drivers/input/input.c:112-130`）、`dev->event`、`serio_driver->interrupt`、`rmi_function_handler->attention`。索引给不出「哪个实现会被调到」 | 所有「谁实现了这个回调」的清单都是 `grep -rn --include=*.c -A N 'struct X [a-z_]* = {' . \| grep -E '\.callback'` 数出来的。★ 这条口径**抓不到运行期赋值**——`sound/usb/mixer_quirks.c` 的 DualSense handler 就是运行期填 `.match` 的，静态 grep 漏掉，是靠交叉核对 `input_register_handler()` 调用点数（13 行 → 12 个注册点 vs 11 个静态实例）才发现的 |

★ 第二行那个 11 vs 12 的差就是「索引和静态 grep 都会漏一个」的证据，登记在 `02-datastructures.md` §3（口径 A / 口径 B 两条命令并列）。

### §2.6 运行期观察：**一次都没有**

★★ 这一条必须显眼：**本文档集没有在任何机器上运行过 input 子系统**。所有 `/sys`、`/proc`、`/dev`、`evtest`、`ftrace`、`debugfs` 相关的内容都是**读代码得出的预期**，不是抓下来的输出。

| 类别 | 出现在 | 状态 |
| --- | --- | --- |
| `/proc/bus/input/devices` 的字段含义 | `07-debug.md` §4 | 从 `input_devices_seq_show()` 逐行读出，**格式串逐字 grep 过** |
| sysfs 属性名与权限位（0644/0444） | `05-usage.md` §2.6、`07-debug.md` §3 | 从 `DEVICE_ATTR*` 宏读出 |
| `evtest` / `v4l2-ctl` / `hexdump` 的示例 | `07-debug.md` §6、§8 | ★ **命令是真的，输出没贴**——规则 22：示例命令输出不许编。凡是本篇没贴输出的地方，就是作者没跑过 |
| ftrace / kprobe 示例 | `07-debug.md` §4.2 | 同上 |

### §2.7 arm64 裁剪记录

裁掉的不是「不重要的」，是「arm64 上编译不出来或跑不到的」。每条都有可复现的判据：

| # | 裁掉什么 | 判据（可重跑） | 影响到哪篇 |
| --- | --- | --- | --- |
| 1 | **i8042 / atkbd / psmouse 整条 PS/2 路径**（`i8042.c` 1675 行 + `i8042-acpipnpio.h` 1823 行 = serio 目录近 1/4 的代码） | `CONFIG_SERIO_I8042` `depends on ARCH_MIGHT_HAVE_PC_SERIO`（`drivers/input/serio/Kconfig:32`）；`grep -rn 'select ARCH_MIGHT_HAVE_PC_SERIO' arch/` → **9 行 / 8 个架构**（alpha、arm/mach-footbridge:60、ia64、loongarch、mips ×2、powerpc、sparc、x86），**没有 arm64** | `04-extensions.md` §2.4、`06-devicetree.md` §0 |
| 2 | **`apm-power.c` 的实际可用性**（文件仍逐行读了） | `CONFIG_APM_EMULATION` 依赖 `SYS_SUPPORTS_APM_EMULATION`；`grep -rn 'SYS_SUPPORTS_APM_EMULATION' --include='Kconfig*' arch/` → **7 行 / 5 个文件**，其中 3 条 `select`（`arch/arm/Kconfig:140`、`arch/mips/Kconfig:202`、`arch/sh/boards/Kconfig:119`）+ 4 条 `config`，**没有 arm64** | `04-extensions.md` |
| 3 | ★★ **全子系统唯一的 `TRACE_EVENT` 文件** | `CONFIG_KEYBOARD_APPLESPI` `depends on X86 \|\| COMPILE_TEST`（`drivers/input/keyboard/Kconfig:77`）⇒ `applespi_trace.h` 在 arm64 上编不出来 ⇒ **arm64 上 input 子系统的 tracepoint 覆盖 = 0** | `07-debug.md` §4（整节的结论） |
| 4 | **2 个 debugfs 提供者里的 1 个** | 同上。`grep -rln 'debugfs_create' drivers/input/` → 2 个文件（`touchscreen/edt-ft5x06.c`、`keyboard/applespi.c`），后者 x86 only ⇒ arm64 上只剩 `edt-ft5x06` | `07-debug.md` §5 |
| 5 | **ACPI 发现路径**（17 个文件） | `grep -rl 'acpi_device_id' drivers/input/ --include=*.c \| wc -l` → 17。★ 口径见 §3.2（另一个口径是 21） | `06-devicetree.md` §0 分诊表 |
| 6 | **6 条 `depends on X86` 的 Kconfig 项** | `grep -rn 'depends on.*\bX86\b' drivers/input/Kconfig drivers/input/*/Kconfig \| wc -l` → 6 | `01-api.md` §0.1 |
| 7 | **`filter` 型 handler 的一半** | `mac_hid`（`drivers/macintosh/mac_hid.c:154-160`）是 PowerMac 专用 ⇒ arm64 上全树带 `filter` 的 handler **只剩 `drivers/tty/sysrq.c` 一个** | `03-internals.md` A6、C3 |
| 8 | **joystick / gameport / tablet 三个目录**（21445 行） | 全是 x86 ISA/并口/USB 老硬件；`gameport` 五个文件里四个是固定 I/O 口或 PCI 声卡 | `04-extensions.md` §12（只讲框架形状） |
| 9 | **x86 平台驱动里 `sparse_keymap_*` 的 20 个消费者** | `grep -rlE --include=*.c '\bsparse_keymap_setup\(' . \| grep -c 'drivers/platform/x86'` → 20（占 24 个文件里的 20 个） | `04-extensions.md` §9 |
| 10 | **`COMPAT_USE_64BIT_TIME` 的唯一覆盖点** | `grep -rn 'define COMPAT_USE_64BIT_TIME' --include=*.h .` → 两行：`include/linux/compat.h:38`（兜底 0）+ `arch/x86/include/asm/compat.h:79`（唯一覆盖）⇒ **arm64 上恒为 0**，`input-compat.c` 走的是「32 位 `struct timeval`」那条分支 | `04-extensions.md` §4 |

★ 第 3 和第 4 条合起来是本子系统在 arm64 上最反直觉的事实：**调试手段的两个「现代」入口（tracepoint、debugfs）一个是零、一个只剩一个驱动**。`07-debug.md` 整篇的重心因此压在 dynamic debug、`evbug`、sysfs 和 `/proc` 上，这是判定的结果，不是偷懒。

---

## §3 规模统计与全部数字的复现命令

### §3.1 规模

★ 下面每一行的命令都是**刚跑过的**，输出就是本表的数字。`find` 与 `ls` 口径不同的地方在 §3.2 单列。

| 范围 | 数字 | 命令 |
| --- | --- | --- |
| 核心 `.c`（`drivers/input/` 顶层） | **16 个 / 9446 行** | `find drivers/input -maxdepth 1 -name '*.c' \| wc -l`；`find drivers/input -maxdepth 1 -name '*.c' -exec cat {} + \| wc -l` |
| 核心私有头（顶层 3 个 `.h`） | **112 行** | `wc -l drivers/input/*.h \| tail -1` |
| 整个 `drivers/input/` | **410 个 `.c` + 44 个 `.h` / 215280 行** | `find drivers/input -name '*.c' \| wc -l`；`-name '*.h'`；`find drivers/input \( -name '*.c' -o -name '*.h' \) -exec cat {} + \| wc -l` |
| 核心公开头（**5 个，逐个列出**） | **892 行** | `wc -l include/linux/input.h include/linux/input/{mt,matrix_keypad,sparse-keymap,touchscreen}.h \| tail -1` |
| uapi | **1771 行**（`input.h` 518 + `input-event-codes.h` 976 + `uinput.h` 232 + `userio.h` 45） | `wc -l include/uapi/linux/{input,input-event-codes,uinput,userio}.h` |
| Kconfig / Makefile | **11 + 11** | `find drivers/input -name 'Kconfig*' \| wc -l`；`-name 'Makefile'` |
| 注册 `input_dev` 的驱动 | **474 处 / 412 个文件**（已排除 `drivers/input/input.c` 自己） | `grep -rn 'input_register_device(' --include=*.c . \| grep -vc 'drivers/input/input\.c'`；`-rl` 版同理 |
| 用 `devm_input_allocate_device()` 的文件 | **210** | `grep -rl 'devm_input_allocate_device(' --include=*.c . \| wc -l` |
| 注册 handler 的地方 | **12 处**（排除核心自己） | `grep -rn 'input_register_handler(' --include=*.c . \| grep -v 'drivers/input/input\.c'` |
| 静态 `struct input_handler` 实例 | **11** | `grep -rn 'struct input_handler [a-z_]* = {' --include=*.c . \| wc -l` |

★★ 最后两行的 **12 vs 11 差一个**，就是 §2.5 说的「静态 grep 会漏运行期赋值」的实证：`sound/usb/mixer_quirks.c:768` 注册的是 `&mei->ih`，`ih` 是嵌在别的结构里、运行期填的，任何「找静态实例」的 grep 都抓不到它。**这个差值本身是本套文档最有价值的一个数字**——它证明「11 个 handler」这句话如果不配口径就是错的。

各子目录的 `.c` 数与行数（`find` 口径，会递归）：

```bash
for d in touchscreen misc keyboard mouse joystick serio rmi4 gameport tablet; do
  printf "%-14s %3d %6d\n" $d \
    $(find drivers/input/$d -name '*.c' | wc -l) \
    $(find drivers/input/$d -name '*.c' -exec cat {} + | wc -l)
done
```

| 目录 | `.c` 数 | 行数 |
| --- | --- | --- |
| `touchscreen/` | 109 | 59409 |
| `misc/` | 84 | 36052 |
| `keyboard/` | 68 | 30797 |
| `mouse/` | 38 | 32818 |
| `joystick/` | **39** ★ | 15823 |
| `serio/` | 29 | 14722 |
| `rmi4/` | 16 | 10511 |
| `gameport/` | 5 | 1696 |
| `tablet/` | 6 | 3894 |

### §3.2 ★★ 四对口径差：同一个问题两个答案，两个都对

规则 30 里最难自查的一条是「子串包含 / 通配范围会把两类算成一类」。本子系统碰到四对，**每一对都必须两个数字都登记**，否则读者重跑出另一个数字就会以为文档错了。

| # | 问题 | 口径 A | 口径 B | 差在哪 | 正文里用哪个 |
| --- | --- | --- | --- | --- | --- |
| 1 | `joystick/` 有几个 `.c`？ | `find` → **39** | `ls drivers/input/*/*.c` → **34** | `find` 递归进 `drivers/input/joystick/iforce/`（5 个文件）。★ 全树验证：`find drivers/input -mindepth 2 -type d` **只输出 `drivers/input/joystick/iforce` 一个**，所以九行表里**只有 joystick 这一行**两个口径不同；恒等式 16（顶层）+ 389（`ls */*.c`）+ 5（iforce）= **410** | §3.1 用 `find`（39），因为要与 410 对上 |
| 2 | 有几个 `EXPORT_SYMBOL_GPL`？ | 核心 16 个 `.c` → **14** | 全 `drivers/input/` → **54 处 / 18 个文件** | 子目录里驱动自己也导出；最大单个贡献者是 `touchscreen/wm97xx-core.c` 的 **9** 个 | `05-usage.md` §0.1 用 14（讲「读者能用的核心 API」）；本篇 §1.5 对账表用 54（要个跨目录的大数字） |
| 3 | 有几个文件走 ACPI？ | `acpi_device_id` → **17** | `ACPI` 子串 → **21** | 4 个差额是 `#ifdef CONFIG_ACPI` / 注释里提 ACPI 但没有 id 表的 | §2.7 第 5 条用 17（判据是「有 ACPI id 表 = 真走 ACPI 发现」） |
| 4 | 核心公开头有多少行？ | 显式列 5 个文件 → **892** | `include/linux/input.h` + `include/linux/input/*.h` → **1823（17 个文件）** | ★ 通配符底下多出 12 个**具体驱动的板级数据结构**头（`adxl34x.h` 357 行、`adp5589.h` 180 行），它们不是子系统 API | §3.1 与 `00-overview.md` §3 都用 892，**并且都显式列文件名** |

★ 第 4 对是本轮自审新抓出来的缺陷：`00-overview.md:301` 原来那格写的是 `` `include/linux/input.h` + `include/linux/input/*.h` | 892 行 ``——**标签和数字不是一回事**。改法不是改数字（892 是对的），是把标签换成「核心 5 个头文件（见下）」并紧跟一段显式列文件的命令。这正是规则 30「统计范围必须显式列文件」要防的形态。

### §3.3 正文里的可复现命令在哪

| 篇 | ```bash 块数 |
| --- | --- |
| `00-overview.md` | 1 |
| `01-api.md` | 4 |
| `02-datastructures.md` | 5 |
| `03-internals.md` | 3 |
| `04-extensions.md` | 13 |
| `05-usage.md` | 2 |
| `06-devicetree.md` | 23 |
| `07-debug.md` | 20 |
| `08-feynman.md` | 3 |
| **00–08 小计** | **74** |

```bash
grep -c '^```bash' output/input/*.md | sed 's/:/ → /'      # 按篇；99 自己的块数随本篇增长
```

★ 上表数的是**行首**的 ````bash`。另有 **1 个缩进的**（在列表项里）位于 `04-extensions.md:2002`，所以 00–08 的实际总数是 **75**。重跑缩进那一类：`grep -rn '^[[:space:]]\+```bash' output/input/`。

★ 命令密度最高的是 06（23）和 07（20），因为这两篇的每条论断（「哪个属性被谁消费」、「这个开关的三道门」）本身就是一次 grep 的结论；命令最少的是 00（1）和 05（2），因为它们分别是浓缩篇和代码骨架篇，数字都从别篇引来——**这个分布是设计结果，不是覆盖不均**。

### §3.4 ★★ 方法论：一个点号让计数少了 3

这条单列出来，因为它是「命令看起来对、数字也看起来合理、但错了」的典型。

数「哪些文件调用了 `touchscreen_parse_properties()`」时，第一版写的是：

```bash
grep -rl 'touchscreen_parse_properties(' --include=*.c .        # → 36
```

看着没问题。但同一个统计换个写法：

```bash
grep -rl 'touchscreen_parse_properties(' --include=*.c . | wc -l    # → 36
grep -rn 'touchscreen_parse_properties' --include=*.c . | wc -l     # → 更多（含声明、注释、`.h`）
```

真正的坑不在这里，而在**另一条统计**里：正则里的 `.` 是通配符。写 `grep -c 'input.c'` 时，它会匹配 `input.c`、`input-c`、`inputXc`……在本树上多匹配了行；反过来，某条本该匹配 `xxx.c:` 的命令因为把 `.` 当通配写窄了，**漏数了 3 个**。定稿前的逐格对账才发现数字与正文里写的不一致。

三条固定改法：

1. **文件名里的点一律转义**：`grep 'input\.c'`，不是 `grep 'input.c'`。本篇 §1.2 那条 `grep -rn 'sysrq\.c:' output/input/` 就是这么写的
2. **符号计数一律带 `-w`**：否则 `input_event` 会把 `input_event_from_user`、`input_event_size` 一起算进来
3. ★ **同一个数字用两条不同形状的命令各数一遍**（`-l | wc -l` vs `-c` 求和 vs `-o | wc -l`），对不上就是有一条写错了。§1.5 那张 ugrep/GNU 对账表和本节是同一个手法的两个方向：一个换工具，一个换写法

---

## §4 逐篇取证表

每篇列 **3–5 条最关键论断 → 出处**，加上**这一篇在自审里被改过什么**。★ 「自审修正」一列是本表的重点：它告诉你这一篇哪里曾经错过，也就是哪里最值得你再看一眼。

### §4.1 `00-overview.md`（809 行）

| 关键论断 | 出处 |
| --- | --- |
| input 是**框架型**，没有 `input_bus_type` | §1.4 两道门的实际输出 |
| 核心只有 16 个 `.c` / 9446 行，而消费者有 412 个文件 | §3.1 |
| 「一次按键」的端到端路径共 8 跳，跨 `input.c` → handler → 用户态 | `drivers/input/input.c:112-130`（`input_pass_values()` 三段分发）+ `evdev.c` |
| arm64 上 tracepoint 覆盖 = 0 | §2.7 第 3 条 |

**自审修正**：(1) 四处交叉引用指向了不存在的章节（`03` 的章节号在重排轮改成了 A/B/C 前缀，00 里的旧引用没跟着改）——靠 `check_xrefs.py` 抓到；(2) `:301` 的 892 行标签写成了通配符（§3.2 第 4 对）；(3) 九目录表的 joystick 行补上了 39 vs 34 的口径说明；(4) §8 导读表里 04 的行数是旧数字。

### §4.2 `01-api.md`（473 行）

| 关键论断 | 出处 |
| --- | --- |
| 核心 16 个 `.c` 里共 **14 处** `EXPORT_SYMBOL_GPL`，这是「读者能用的 API」的判据基线 | §3.2 第 2 对 |
| `INPUT_GENERATE_ABS_ACCESSORS` 一个宏生成 **12 个** inline | 读宏本体展开（§2.5 第 1 行；索引工具查不到） |
| 官方 `driver-api/input.rst` 的 `:export:` 只覆盖 5 个 `.c`，**不能当成 API 清单** | §2.3 |
| `input_inhibit_device()` 返回 `int` 但从不失败 | `drivers/input/input.c` 该函数本体，见 §6 |

**自审修正**：(1) ★★ 曾写出 `input_copy_abs()` 这个**根本不存在的函数**（幻觉符号，全树 grep 为空）——这是规则 20 存在的原因；(2) 位置一律从 `文件:行号` 退回到只写文件（规则 10），退掉了十几处需要维护的行号；(3) 新增官方文档三行登记表 + 「有 kerneldoc ≠ 能用」的警告（§2.3 的 (b) 类修复）。

### §4.3 `02-datastructures.md`（546 行）

| 关键论断 | 出处 |
| --- | --- |
| §1.0 否掉「`/sys/bus/input/` 应该存在」 | §1.4 的两个口径 + `drivers/input/input.c:2017-2018` |
| `input_dev` / `input_handler` / `input_handle` 三者是**多对多加一张连接表**，不是父子 | 三个结构体定义逐字段 + `input_attach_handler()` |
| handler 数：静态 11 vs 注册点 12（口径 A / 口径 B 并列） | §3.1 末两行 |
| `serio` / `gameport` / `rmi4` 三条子总线与 input 核心**不共享任何结构** | §1.4 的五行 `bus_type` 输出 |

**自审修正**：(1) `mt.h` 的行区间 44-52 → **43-51**；(2) `input-mt.c` 的 39-112 → **39-104**；(3) `:572-575` → **:568-571**；(4) `sysrq.c` 的两处引用受 RT 补丁 +2 偏移影响，处置见 §1.2。

### §4.4 `03-internals.md`（958 行）

| 关键论断 | 出处 |
| --- | --- |
| Part A 时间线七段（分配 → 填能力 → 注册 → 匹配 → 事件 → inhibit/resume → 注销），次序用「谁调用谁」验证 | 每节标题带完整函数行区间 |
| `input_pass_values()` 的三段分发：`events` 优于 `event`，`filter` 能截断 | `drivers/input/input.c:112-130` |
| 全树带 `filter` 的 handler 只有 2 个，arm64 上只剩 1 个 | `drivers/tty/sysrq.c:1030`（本树）+ `drivers/macintosh/mac_hid.c:154-160` |
| C2 列出**官方文档与代码的 5 处冲突** | `03-internals.md:898-906`，逐条对着 `input.rst` / `input-programming.rst` |
| C3 的设计债清单 | §6（本篇是汇总，03 C3 是带上下文的展开） |

**自审修正**：(1) 时间线次序初稿凭直觉排，`input_register_device()` 与 `input_attach_handler()` 的先后被调用关系推翻；(2) 章节号加 A/B/C 前缀（重排轮），随后必须重跑 `check_xrefs.py`；(3) ★★ 有一处引用写成 `drivers/input/input.c:638`，而 `:638` 是**空行**，要引的 `handle->open++` 在 `:639`——**两个脚本都通过了**（638 是合法的树内行号），见 §7 第 1 条。

### §4.5 `04-extensions.md`（2043 行，全套最长）

| 关键论断 | 出处 |
| --- | --- |
| 篇首声明：七个独立框架（serio / gameport / rmi4 / ff / evdev / mousedev / joydev），**互不调用** | 各自的 `module_init` 与注册函数 |
| 每个框架都交代了挂在 03 时间线的哪一点 | §12 等各节的「挂点」段 |
| `sparse_keymap_*` 的 24 个消费者里 20 个在 `drivers/platform/x86` | §2.7 第 9 条 |
| `USERIO` 是 serio 里**唯一没有任何 `depends on`** 的选项 | `drivers/input/serio/Kconfig:307`；`userio_misc` 在 `drivers/input/serio/userio.c:273-277` 注册 misc 10:240 |
| 篇末两张汇总表（框架 × 挂点 × arm64；框架 × 全树使用统计） | 21 行，每行带重跑命令 |

**自审修正**：(1) ★★ §2.4 的 serio Kconfig 表里**两个 `——` 是事实错误**，`SERIO_ALTERA_PS2`/`SERIO_ARC_PS2` 都有 `depends on HAS_IOMEM`（`:215`、`:248`）；另有 `SERIO_APBPS2` 漏了 `&& HAS_IOMEM`（`:258`）、`SERIO_SUN4I_PS2` 漏了 `|| COMPILE_TEST`（`:288`）——四格全部改成带行号的准确依赖；(2) 新增 `keyboard_notifier` 一段（§2.3 的 (a) 类修复）；(3) 新增 `USERIO` 一行 + 两段；(4) 新增 joydev / gamepad / gameport-programming 三处官方文档指针；(5) 「一共 5 个」改成 7（规则 24 的「共 N 种」连带修改）。

### §4.6 `05-usage.md`（2133 行）

| 关键论断 | 出处 |
| --- | --- |
| 篇首声明**骨架未编译** | §5.1 |
| 五个角色各一节（设备驱动 / handler / MT 驱动 / ff 驱动 / uinput 用户态） | 各节骨架 |
| `touchscreen_parse_properties()` 全树 **36 个调用点 / 35 个文件**，第三参数 26 个传 `true` / 10 个 `false` | 见 §3.4 的转义陷阱 |
| 独立一节「近邻符号区分」（`input_sync` vs `input_mt_sync_frame`、`input_event` vs `input_report_*`、`allocate` vs `devm_allocate`……） | 每组带「错用会怎样」 |
| sysfs 属性的权限位 0644/0444 从 `DEVICE_ATTR*` 宏读出 | §2.6 |

**自审修正**：(1) ★★ §0.1 那张 7 行 `_GPL` 表配的复现命令输出 **54 行**——数字对（14 是核心口径）但命令是宽口径，属于「右数字、错复现路径」；(2) `/dev/videoN` → `/dev/v4l-touch0`；(3) `poll` 属性权限 0644 的发现（初稿写成只读）；(4) 交叉引用回访：作为示例篇积了最多陈旧 `见 0X §Y`。

### §4.7 `06-devicetree.md`（1104 行）

| 关键论断 | 出处 |
| --- | --- |
| §0 分诊表：五条发现路径（DT / ACPI 17 个文件 / i2c id 表 / platform data / 子总线枚举），DT 只占一部分 | §2.7 第 5 条 |
| `input.yaml` 7 个属性逐个数了消费者（10 / 11 / 13 / 4 / 7 / 2 / 1） | 每个属性一条 `grep -rn '"属性名"'` |
| ★ `touchscreen-y-plate-ohms` **零消费者 = 死字** | `grep -rn 'touchscreen-y-plate-ohms' --include=*.c .` 为空 |
| `touchscreen-average-samples` 落在 `ads7846.c` 的 `debounce_max` 上（名字与用途不符） | `drivers/input/touchscreen/ads7846.c` 该赋值行 |
| DTS 举例全部取自 `arch/arm64/boot/dts/` | §1.3 |

**自审修正**：(1) ★ `:19` 处有一句「18 个文件见下」是**悬空的**——下文没有那 18 个文件，改写成指向 §2.7；(2) `rmi_of_property_read_u32()` 的跨行提取要用 `perl -0777`，`grep` 数不出来。

### §4.8 `07-debug.md`（933 行）

| 关键论断 | 出处 |
| --- | --- |
| arm64 上 tracepoint 覆盖 **= 0**（唯一的 `TRACE_EVENT` 在 x86-only 驱动里） | §2.7 第 3 条 |
| debugfs 提供者 2 个，arm64 上只剩 `edt-ft5x06` | §2.7 第 4 条 |
| 「故障注入：本子系统没有」——结论也写出来 | 全树 `grep 'fault_inject\|should_fail' drivers/input/` 为空 |
| `/proc/bus/input/devices` 逐字段含义 | `input_devices_seq_show()`，格式串逐字 grep |
| 错误码按语义分组的 top-N 直方图（7 条 keycode 映射路径共用一个 `-EINVAL` 且零日志） | 见 §6 |

**自审修正**：(1) 日志可见性等级细化到 `_once`/`_ratelimited` 变体；(2) 示例命令**只留命令不贴输出**（规则 22），并在 §2.6 显式说明「没贴输出 = 没跑过」；(3) `:476` 的裁剪说明改为指向 §2.7；(4) ★ 一条 `:1061` 的引用被误当成「返回点」，实际是**比较**而不是 return。

### §4.9 `08-feynman.md`（672 行）

| 关键论断 | 出处 |
| --- | --- |
| 主题 ≥6，含「为什么没有 `/sys/bus/input/`」这个最容易类比错的一组 | §1.4 的五行输出 |
| 每个主题都有「这个类比什么时候会误导你？」 | 各主题第四段 |
| 类比里的技术断言全部带行号（费曼篇不豁免取证） | 逐条 |

**自审修正**：(1) ★★ 有一处写「唯一的一条」，而它引的命令输出是 **5 行**——描述与证据直接冲突；(2) 标题里夹带的类比全部搬进「先举个例子」；(3) 「5 个」→ 7、「9 个」→ 11、「22」→ 25、「16」→ 17、「33」→ 36 等一批连带修正（规则 30 的最后一条：改一个数字要 grep 全套）。

---

## §5 明确未验证项（四层）

★ 这一节的价值不低于前面九篇：它告诉你**该在哪里加倍小心**。分四层，层次越靠后越难补。

### §5.1 第一层：未编译、未运行

★★ **写这套文档的机器是 `Linux 6.5.0-14-generic x86_64`**（见 §1.5 末行）。所以：

| 项 | 状态 |
| --- | --- |
| 本树是否编译过 | **没有**。`find . -name '*.o' -not -path './.git/*' \| wc -l` → 0；没有 `.config` |
| `05-usage.md` 的代码骨架 | **一行都没编译过**。篇首已声明。它们是「按核心 API 契约拼出来的形状」，不是「跑过的代码」。★ 抄进真实驱动时至少会碰到：头文件包含不全、`devm_` 与非 `devm_` 混用、错误路径少一个 `input_free_device()` |
| `06-devicetree.md` 的 DTS 片段 | **没有 `dtc` 编译过**，也没有在设备上启动过。属性名逐个 grep 过消费者（这是它们唯一的保证） |
| `07-debug.md` 的调试命令 | 命令本身来自代码里的路径与属性名；**输出一律没贴**（规则 22）。凡是本套文档里没有贴输出的命令，就是作者没跑过 |
| arm64 上的任何运行期行为 | **零观察**。所有 arm64 结论都是「读 Kconfig 依赖 + 读代码分支」推出来的 |

### §5.2 第二层：只读了符号表 / 只读了一部分

见 §2.2 的九行表。这里只重申最容易被误读的三条：

| 文件 | 读到什么程度 | 风险 |
| --- | --- | --- |
| `drivers/input/serio/userio.c`（285 行） | ★ **只读了注册段**（`:273-278`）确认 misc 10:240 和设备名。协议解析、`userio_char_write()` 的字节流处理**没读** | `04-extensions.md` §2.4 关于 userio 的两段只保证「它是什么、和 uinput 怎么分工」，不保证协议细节 |
| `drivers/input/keyboard/applespi.c`（1970 行） | 只读了 tracepoint 定义与 debugfs 两块 | 其余 SPI 协议部分完全没读；它 x86-only，见 §2.7 |
| `drivers/platform/x86/` 下 20 个 `sparse_keymap_*` 消费者 | ★ **只数了个数，一个都没打开** | 「20」这个数字可靠（一条 grep），但「它们怎么用」本套没有任何论断 |

### §5.3 第三层：推断而非观察

**判据**：结论来自「读代码分支 + 读 Kconfig 依赖」，没有任何一次运行时确认。这一层最多，逐条列：

| # | 推断 | 依据 | 什么情况下会不成立 |
| --- | --- | --- | --- |
| 1 | arm64 上 tracepoint 覆盖 = 0 | `drivers/input/keyboard/Kconfig:77` 的 `depends on X86 \|\| COMPILE_TEST` | 若读者开了 `COMPILE_TEST` 并手工在 arm64 上加载（`applespi` 找不到硬件、不会产生事件），结论在「有 tracepoint 定义但永不触发」这个意义上依然成立 |
| 2 | 32 位与 64 位用户态读到的位图字符串**段数不同** | `input_bits_to_string()`（`drivers/input/input.c:1070-1090`）的 `in_compat_syscall()` 分支 + 两个调用点 `:1176`、`:1589` | ★ 本环境**没有实测**这两种输出。已在 `03-internals.md`（`CONFIG_COMPAT` 那段）显式标注 |
| 3 | arm64 上 `COMPAT_USE_64BIT_TIME` 恒为 0，`input-compat.c` 走 32 位 `timeval` 分支 | `include/linux/compat.h:38` 兜底 0，唯一覆盖在 `arch/x86/include/asm/compat.h:79` | 上游若给 arm64 加了覆盖（本版本没有） |
| 4 | `matrix_keypad_build_keymap()` 传 rows/cols = 0 时 `get_count_order(0)` 返回 −1 会导致越界 | 读 `get_count_order()` 与调用点的算术 | ★ **没有构造过这个输入**。这是「读出来的 UB」，不是复现过的崩溃。见 §6 |
| 5 | `/proc/bus/input/devices` 与 sysfs 各属性的**具体文本**（字段顺序、分隔符） | 从 `input_devices_seq_show()`（`drivers/input/input.c:1192`）的格式串逐字读出 | 格式串是逐字 grep 的，可靠性接近一等；但**没有 `cat` 过真实输出**，所以整体拼接结果未观察 |
| 6 | 「这个错误码只可能来自这 N 条路径」这类分诊结论 | 全树 grep 该 errno 在相关函数里的返回点 | ★ 被调函数改写返回值的情况已排查（规则：错误码要点名函数），但**间接层数超过两跳的没追** |
| 7 | ★ `edt-ft5x06` 那四个 debugfs 文件的语义 | 从 `drivers/input/touchscreen/edt-ft5x06.c` 代码读出 | ★★ **`Documentation/input/devices/edt-ft5x06.rst` 一个字没读**（见 §2.3）。官方那篇可能写了 `mode` 的合法取值、`raw_data` 的字节布局这类代码里读不出来的约定。**这是本套文档一条明确的欠债，补读动作是打开那一篇再和 `07-debug.md` §5 对账** |

### §5.4 第四层：版本时效

| 项 | 说明 |
| --- | --- |
| 全部行号 | 绑定在 `f6e38ae624cf`（6.1.158）**这棵打了 RT 补丁的树**上。★ 唯一与上游有偏移的是 `drivers/tty/sysrq.c`（+2，见 §1.2） |
| 官方文档的 5 处冲突 | `03-internals.md` C2。上游若更新了 `input.rst` / `input-programming.rst`，这张表就该重核 |
| 数字类论断（474 个驱动、412 个文件、36 个调用点……） | 每个补丁版本都会变。**每个数字都配了命令**，重跑一遍即可；不要相信数字本身 |
| `include/uapi/linux/input-event-codes.h` 的码点 | 976 行，**没有逐个码点核对**（见 §2.1 的诚实说明）。新码点是纯增量，不影响机制结论 |
| 6.1 是 LTS | 机制层面（注册/匹配/分发/inhibit）在 6.1 之后没有大改；`04-extensions.md` 讲的三条子总线同理 |

### §5.5 第五层：不适用

skill 要求**虚拟总线 / 跨边界 ABI 型**子系统再加一层「对端不在本树」（边界停在哪一行、对端行为的依据是什么）。

**input 是框架型，没有跨边界对端**（§1.4 的判定）。事件的两端都在本树：一端是驱动（本树 412 个文件），另一端是 handler（本树 12 个注册点）；再往外是用户态程序，但那属于 uapi 的**已发布 ABI**，不是「另一个软件实体的私有实现」。

★ 唯一沾边的是 `uinput` 和 `userio`：它们让**用户态**变成事件的源头。但用户态程序不是「对端实现」——内核这一侧的行为完全由本树代码决定，读完 `drivers/input/misc/uinput.c`（1117 行）就没有未知了。所以这一层写「不适用」，不是「没查」。

---

## §6 源码设计债

★ 这里记的是**内核代码本身**的问题，不是文档的问题。文档的缺陷在 §8。每条都定位到行，读者可以自己打开看。带 ★ 的是本套文档认为最值得注意的。

### §6.1 静默失败：函数不告诉你它没做成

| # | 位置 | 债 |
| --- | --- | --- |
| 1 | ★ `drivers/input/input.c:594` | `if (handle->open && handle->handler->start)` —— `start` 回调是**可选**的。没实现 `start` 的 handler 在设备 resume/uninhibit 后不会被通知，它自己缓存的状态就此陈旧，而且**没有任何提示** |
| 2 | ★★ `drivers/input/input.c:2470` 与 `:2542` | 两处 `input_attach_handler(dev, handler);` 的**返回值直接丢弃**。这是 input 匹配失败的主路径：一个 handler 因为 `-ENOMEM` 或 `connect()` 失败没接上，`input_register_device()` / `input_register_handler()` 照样返回 0。症状是「设备在 `/proc/bus/input/devices` 里有，但 `/dev/input/eventN` 没出现」，而 dmesg 干净 |
| 3 | `drivers/input/input.c:2565` | `handler->disconnect(handle);` 返回值也没查（`disconnect` 原型是 `void`，所以这是设计上就没有失败通道）。紧跟的 `WARN_ON(!list_empty(&handler->h_list))` 是唯一的兜底 |
| 4 | ★ `drivers/input/input.c:1847-1873`（`input_inhibit_device`） | 声明成 `int`，函数体里**只有一个 `return 0`**——永远不会失败。调用点 `:1527` 还认真接了返回值。`input_uninhibit_device()`（`:1875`）同理。这个 `int` 是给未来留的口子，读者不要以为它会报错 |
| 5 | `drivers/input/serio/serio.c:998` | `ret = serio->drv->interrupt(serio, data, dfl);` —— `interrupt` 回调**不检查是否为 NULL**（`likely(serio->drv)` 只查了 drv）。serio driver 不实现 `interrupt` 就直接空指针 |
| 6 | `drivers/input/serio/ambakmi.c:61` | `return timeleft ? 0 : SERIO_TIMEOUT;` —— 把 `SERIO_TIMEOUT`（一个**数据标志位**，值为 4）当成错误码返回。调用者按 `if (ret)` 判断能工作，但这个值和 `-ETIMEDOUT` 语义完全不同，也不是负数 |

### §6.2 错误码：一个 errno 承载太多含义

| # | 位置 | 债 |
| --- | --- | --- |
| 7 | ★ 键码映射路径（`input.c` 的 `input_get_keycode` / `input_set_keycode` 及其下游） | **7 条不同的失败路径共用一个 `-EINVAL`，且零日志**：keycode 越界、scancode 长度不对、`keycodemax` 为 0、`getkeycode`/`setkeycode` 未实现、位图越界……用户态只看到 `EVIOCSKEYCODE` 返回 `EINVAL`，无法区分。分诊表见 `07-debug.md` §7 |
| 8 | `drivers/input/input-compat.c` | 32/64 位转换里 `-EINVAL` 与 `-EFAULT` 各有多条重复路径，逐条都是「拷贝失败」或「size 不匹配」，同样无日志 |
| 9 | ★ `drivers/input/rmi4/rmi_bus.c:414-429`（`rmi_of_property_read_u32`） | 三重债：(a) 错误只在 `!optional && retval == -EINVAL` 时才上报，**`-ENODATA`（属性存在但空）和 `-EOVERFLOW`（长度不对）被静默吞掉**并写入 `*result = 0`；(b) 函数在所有其它情况返回 0，所以 20 个调用点里那些 `if (retval) return retval;` 基本是**死代码**；(c) ★★ 全树 20 个调用点**全部传 `optional=1`**（`perl -0777` 提取实参后 `uniq -c` → `20 optional=1`），所以 `:422-423` 那两行 `dev_err` + `return retval` 在本树里**永远走不到** |

### §6.3 校验缺失

| # | 位置 | 债 |
| --- | --- | --- |
| 10 | ★★ `drivers/input/matrix-keymap.c`（`matrix_keypad_build_keymap`） | **不校验 rows / cols 是否为 0**。`get_count_order(0)` 返回 **−1**，后续按位移用它就是未定义行为。对照来看，同一个函数在 `:161-162` 却认真写了 `if (WARN_ON(!input_dev->dev.parent)) return -EINVAL;`——**同一个函数里，对 parent 有 `WARN_ON`，对 rows/cols 一个检查都没有**。★ 本套文档没有构造过这个输入（见 §5.3 第 4 条） |
| 11 | `include/linux/input/touchscreen.h` / `touchscreen.c`（`touchscreen_parse_properties`） | 不检查 `input->dev.parent`——而它要靠 parent 拿 `of_node`。和第 10 条的 `WARN_ON` 恰好相反，**两个相邻的核心辅助函数对同一个前提采取了不同态度** |
| 12 | `drivers/input/evdev.c:108` | `BUG_ON(type == EV_SYN);` —— 在一个事件路径上用 `BUG_ON` 而不是 `WARN_ON` + 返回。这条断言只能被核心自己的 bug 触发，但代价是**整机 panic** |

### §6.4 API 可见性错配：导出了但用不了 / 能用但没导出

| # | 位置 | 债 |
| --- | --- | --- |
| 13 | ★ `rmi_dbg` | 用 `EXPORT_SYMBOL_GPL` 导出了，但它的等级常量 `RMI_DEBUG_*` 只住在**私有头** `drivers/input/rmi4/rmi_bus.h` 里。树外模块拿得到符号、拿不到参数 |
| 14 | ★ `drivers/input/rmi4/rmi_bus.c:125` | `EXPORT_SYMBOL(rmi_unregister_transport_device);` —— **没有 `_GPL`**，而它的配对注册函数 `rmi_register_transport_device()` 是 `EXPORT_SYMBOL_GPL`。一对函数两个许可等级 |
| 15 | `rmi_register_function_handler()` | 能用、被 rmi4 内部调用，但**没有任何公开头文件里的声明**（只在 `rmi_bus.h` 里）。所以它不算「读者能用的 API」（判据见 §2.3 末段） |
| 16 | `rmi_function_handler.reset` | 结构体里有这个回调字段，**全树没有一个实现填它**。死字段 |
| 17 | ★ `INPUT_DEVICE_ID_MATCH_*BIT` 系列标志 | 定义在 uapi 里，`input_match_device_id()` 会检查它们，但**没有一个 in-tree handler 用位图匹配**（都用 `evbit`/`keybit` 之外的粗粒度条件）。这些标志在运行期实际是惰性的 |

### §6.5 语义不对称

| # | 位置 | 债 |
| --- | --- | --- |
| 18 | ★ `input_value_sync` vs `input_sync()` | 两者都表示「一帧结束」，但 `SYN_REPORT` 的 **value 一个是 1、一个是 0**：`input_inhibit_device()` 里发的是 `input_handle_event(dev, EV_SYN, SYN_REPORT, 1)`（`drivers/input/input.c:1864`），`input_sync()` 发的是 0。用户态按 value 区分会踩空——`Documentation/input/event-codes.rst:97` 说的是「value 无意义」，但代码里两个值都在用 |
| 19 | 空帧守卫重复实现 | 「没有实际事件就不要发 `SYN_REPORT`」这个逻辑在**核心和 evdev 里各写了一遍**。两份实现的条件不完全一样，改一处不会同步另一处 |
| 20 | `evdev_write()` / `uinput_inject_events()` | 拷贝到一半遇到 `-EFAULT` 时，**已经成功注入的事件数被丢弃**，直接返回 `-EFAULT`。用户态无法知道前几个事件已经生效了 |
| 21 | `serio_open()` / `serio_write()` | 失败返回**裸 `-1`**，不是 `-errno`。调用者 `if (ret)` 能工作，`if (ret == -EIO)` 不能 |
| 22 | ★ uinput 的 16 项环形缓冲 | `drivers/input/misc/uinput.c` 的输出队列只有 16 项，**满了直接丢，而且不发 `SYN_DROPPED`**。evdev 在同样场景下是发 `SYN_DROPPED` 的——**同一个子系统里两套溢出语义** |

### §6.6 注释与开关

| # | 位置 | 债 |
| --- | --- | --- |
| 23 | ★ `drivers/input/touchscreen/edt-ft5x06.c:878` | `#endif /* CONFIG_DEBUGFS */` —— 注释里的 `CONFIG_DEBUGFS` **不是一个存在的符号**。同一文件里真正的开关是 `CONFIG_DEBUG_FS`（`:115` 的 `#if defined(CONFIG_DEBUG_FS)`、`:625` 的 `#ifdef CONFIG_DEBUG_FS`）。注释里写错的符号名会让 grep 找不到配对 |
| 24 | `drivers/input/keyboard/applespi.c` 的 tracepoint | ★ 定义了 tracepoint class 却**没有 `TP_CONDITION`**，而它 memcpy 的内容包含**按键数据**。tracepoint 一开就把击键写进 ring buffer。（arm64 上编不出来，见 §2.7 第 3 条，所以这条是 x86 读者的问题） |

★★ 这 24 条里，第 2 条（`input_attach_handler()` 返回值丢弃）是**唯一一条会让读者花几小时定位问题**的：它把「handler 没接上」变成一个完全静默的状态。`07-debug.md` §7 的分诊表把它列在第一条，就是这个原因。

---

## §7 交叉验证实例：被自己推翻过的东西

★★ 这一节是本篇里最该读的一节。它不是「谦虚」，而是**给你一份「同类错误在哪里还可能存在」的清单**。每条都写清楚：错在哪、怎么发现的、为什么脚本抓不到。

### §7.1 三个脚本抓不到的那一类

| # | 缺陷 | 怎么发现 | 为什么脚本无效 |
| --- | --- | --- | --- |
| 1 | ★★ 引用写成 `drivers/input/input.c:638`，而 `:638` 是**空行**——`handle->open++` 在 `:639` | 逐条 `sed -n '638p'` 打开看（规则 19，不抽样） | `check_line_refs.py` 只查「文件存在 + 行号不越界」。638 是合法行号，**指向空行也算合法**。★ 这类错误在整篇里只能靠逐条打开抓，两个脚本一个都不报 |
| 2 | ★★ `03-internals.md` C2 那张表的第 4、5 条一度写成「文档说错了」 | 打开 `Documentation/input/input.rst` 原文逐字读 | 文档没说错，只是**约束给得比真实约束宽**。「引用有效但指错性质」这一类只有对账轮能抓 |
| 3 | ★ 「唯一的一条」（`08-feynman.md`）后面配的命令输出是 **5 行** | 定稿前逐格重跑命令（规则 30 末条） | 数字和文字都在同一句里自相矛盾，但**脚本不读语义** |
| 4 | ★ `06-devicetree.md:19` 有一句「18 个文件见下」，而下文没有那 18 个文件 | 回访交叉引用 | 悬空的是「见下」，不是章节号，`check_xrefs.py` 不管 |

### §7.2 「数字对、复现路径错」——本套最隐蔽的一类

★★ 这四条的共同点：**数字是对的**，所以逐格核对数字不会发现问题；错的是「读者按这条命令跑会得到另一个数字」。

| # | 缺陷 | 真相 |
| --- | --- | --- |
| 5 | `05-usage.md` §0.1 一张 **7 行**的 `_GPL` 表，配的复现命令输出 **54 行** | 14 / 54 是两个口径（§3.2 第 2 对）。表用的是核心口径，命令写的是全目录口径 |
| 6 | ★ `00-overview.md:301` 那格标签写 `include/linux/input.h` + `include/linux/input/*.h`，数字写 **892** | 892 是**显式 5 个文件**的和；那个通配符是 **1823**（17 个文件）。改的是标签不是数字，并加了一段显式列文件的命令（§3.2 第 4 对） |
| 7 | 一条统计里 `grep 'xxx.c'` 的 `.` 没转义，**漏数了 3** | §3.4 单列了这条方法论 |
| 8 | 九目录表里 joystick 写 **39**，而读者用 `ls drivers/input/*/*.c` 会数出 **34** | 两个口径都对；补了恒等式 16 + 389 + 5 = 410（§3.2 第 1 对） |

### §7.3 事实错误：`——` 不只是含糊，它可能是错的

| # | 缺陷 | 真相 |
| --- | --- | --- |
| 9 | ★★ `04-extensions.md` §2.4 的 serio Kconfig 表里，`SERIO_ALTERA_PS2` 与 `SERIO_ARC_PS2` 的依赖格写成 `——` | 两个**都有** `depends on HAS_IOMEM`（`drivers/input/serio/Kconfig:215`、`:248`）。规则 19 说 `——` 是欠债记号；这两格证明它还可能是**事实错误**——写 `——` 的那一刻你并不知道自己写的是「没有」还是「我没查」 |
| 10 | 同表 `SERIO_APBPS2` 写 `OF` / `SERIO_SUN4I_PS2` 写 `ARCH_SUNXI` | 实际是 `OF && HAS_IOMEM`（`:258`）和 `ARCH_SUNXI \|\| COMPILE_TEST`（`:288`）。**少写一个条件比写错一个条件更难发现**，因为句子读起来是通的 |
| 11 | ★ 幻觉符号 `input_copy_abs()` 出现在 `01-api.md` | 全树 grep 为空。**这个函数不存在**。规则 20 的存在理由 |
| 12 | ★ 「`input_print_bitmap` 在 `:1489` 的前向声明是冗余的，定义在 `:1581`」 | **这条债是我自己写错的，已从 §6 删掉**：`:1497` 就在用它，前向声明是**必需的**。★ 教训是「看起来多余的代码」在下结论前必须 grep 全部使用点（`grep -n 'input_print_bitmap' drivers/input/input.c` → `:1489` 声明 / `:1497` 用 / `:1581` 定义 / `:1616` 用 / `:1685` 用） |

### §7.4 行号偏移与范围错

| # | 缺陷 | 真相 |
| --- | --- | --- |
| 13 | `include/linux/input/mt.h` 区间写 44-52 | 实际 **43-51** |
| 14 | `drivers/input/input-mt.c` 区间写 39-112 | 实际 **39-104** |
| 15 | 一处写 `:572-575` | 实际 **:568-571** |
| 16 | ★ `07-debug.md` 一处把 `:1061` 当成「返回点」 | 那一行是**比较**（`if (... == ...)`），不是 `return`。「grep 到 errno 就当成返回点」是错的口径 |
| 17 | ★★ `drivers/tty/sysrq.c` 的 6 处行号与上游 6.1.158 差 **+2** | RT 补丁插了两行。**只有把「引用了哪些树内路径」和 `git status` 求交集才会发现**——skill 的基线步骤只查了 `drivers/input/`，而 input 的 handler 住在 tty/macintosh/net/sound 里。处置与命令见 §1.2 |

### §7.5 数字连带修改

改一个数字必须 grep 全套（规则 30 末条）。本套实际发生过的连带修正：

| 旧 → 新 | 说明 |
| --- | --- |
| 33 → **36** | `touchscreen_parse_properties()` 调用点重数 |
| 9 → **11** | 静态 `input_handler` 实例 |
| 22 → **25** | （子框架消费者计数） |
| 16 → **17** | ACPI `acpi_device_id` 文件数（原先误用了 21 那个口径） |
| 5 → **7** | 独立框架数（规则 24：「共 N 种」的连带修改） |
| 4 → **5** | `driver-api/input.rst` 的 `:export:` 覆盖的 `.c` 数 |
| 33 → **55** | `Documentation/input/notifier.rst` 行数（初稿凭印象写 33，`wc -l` 是 55） |
| 27 → **17** | `check_doc_coverage.sh` 的 0 引用官方文档数（补内容后的真实下降，不是修错） |

★ 判据是 `grep -rn '<旧数字>' output/input/` 返回的每一处都确认过语境。**这条最容易漏的地方是篇末汇总表**——正文改了、表没改。

### §7.6 换工具、换写法的等价性验证

| 手法 | 做法 | 结果 |
| --- | --- | --- |
| ★★ 换工具 | 发现 PATH 里的 `grep` 是 **ugrep 7.5.0**（shell 函数），把九组关键统计在 ugrep 和 `/bin/grep`（GNU 3.7）上各跑一遍 | **九组全部一致**（表在 §1.5）。同时找出了让它成立的前提：本树未编译，所以 ugrep 的 `--ignore-files` 没排除任何被统计的文件 |
| 换写法 | 同一个数字用 `-l \| wc -l`、`-c` 求和、`-o \| wc -l` 三种形状各数一遍 | 抓出了 §7.2 第 7 条那个未转义的 `.` |
| 换方向 | 「静态实例数」与「注册点数」两个方向对同一件事计数 | 11 vs 12，抓出了 `sound/usb/mixer_quirks.c:768` 这个运行期赋值（§3.1 末段） |
| 官方文档反查 | `check_doc_coverage.sh` | ★ 意外收获：为补 `userio.rst` 一行而回查 Kconfig，抓出 §7.3 第 9、10 条**四个错误的依赖格**——反查官方文档抓到的是**代码事实错误**，不是文档引用错误 |

---

## §8 本套文档自己的缺陷统计

★ 统计范围**只包含本篇 §4 / §7 里逐条登记在案的 34 处**，不是「全部缺陷」——没被发现的缺陷当然不在表里。这张表的用途是回答一个具体问题：**哪种手段值得下一次继续花时间**。

### §8.1 按缺陷类型

| 类型 | 数量 | 代表 |
| --- | --- | --- |
| 数字连带修改（改了一处没改引用它的地方） | **8** | 33→36、9→11、5→7（§7.5） |
| 失效或悬空交叉引用 | **6** | 00 的四处旧章节号、`06-devicetree.md:19` 的「18 个文件见下」 |
| 行号 / 范围 / 性质错 | **5** | `mt.h` 43-51、`:638` 是空行、`:1061` 是比较不是 return |
| 口径与复现命令错配（**数字对、命令错**） | **4** | 892 vs 1823、7 行表配 54 行命令（§7.2） |
| 事实错误（Kconfig 依赖格） | **4** | 两个 `——` 其实都有 `HAS_IOMEM`（§7.3） |
| 组织与可读性（标题夹带类比、章节号重排） | **3** | 08 的标题、03 加 A/B/C 前缀 |
| 幻觉符号（函数不存在） | **1** | `input_copy_abs()` |
| 自己造出来的假设计债 | **1** | `input_print_bitmap` 的前向声明（§7.3 第 12 条） |
| 文档-代码冲突的**性质**误判 | **1** | C2 第 4、5 条：文档不是错，是约束给宽了 |
| 上游行号偏移登记 | **1** | `sysrq.c` +2 |
| **合计** | **34** | |

★★ 排在第一的不是「写错事实」，而是**「改了一处没改另一处」**（8 处，占近四分之一）。这说明本套文档的主要风险来自**冗余**：同一个数字在正文、汇总表、别篇的导读里各写一遍。规则 30 末条（改一个数字就 `grep -rn '<旧数字>' output/input/`）是针对这一类的唯一有效手段。

### §8.2 按发现手段

| 手段 | 抓到 | 说明 |
| --- | --- | --- |
| ★★ **定稿前逐格重跑命令**（规则 30 末条） | **12** | 全套最高产的一条。它抓的是「数字对、命令错」和「改了没连带改」这两类，而这两类占了 34 处里的 16 |
| ★ **逐条 `sed` 打开核对，不抽样**（规则 19） | **5** | 唯一能抓「行号合法但指向空行/指向别的语法结构」的手段 |
| `check_xrefs.py` | **6** | 跨篇章节号，纯机械，零成本 |
| ★ **对账轮 / 把散落判断收成一张必须填满的表**（规则 17） | **4** | 空格子蒙混不过去；C2 那条「文档不是错、是宽」就是填格子时掉出来的 |
| `check_doc_coverage.sh` 反查 | **4** | ★ 意外用途：为补 `userio.rst` 一行而回查 Kconfig，抓出的是**代码事实错误** |
| 全树 grep 符号存在性（规则 20） | **2** | 幻觉符号 + 自造的假债 |
| ★ **`git status` 改动集 × 引用路径求交**（本套新增手段） | **1** | 唯一发现 `sysrq.c` +2 偏移的办法。skill 的基线步骤只查子系统目录，对 input 这种「handler 住在别人家」的子系统不够 |
| `check_line_refs.py` | **0** ★ | 见下 |
| **合计** | **34** | |

★★ **`check_line_refs.py` 在本套一处内容缺陷都没抓到**，这不代表它没用：

- 它在每一轮都确认了**几百条引用没有越界**（最后一轮 896 条），把「行号写飞了」这一整类风险清零。**它的产出是「持续为 0」，不是「抓到几个」**
- 它抓到过 **1 处 AMBIGUOUS**：本篇 §2.2 有一处引用只写了 `Kconfig` 这个基名、没写目录。AMBIGUOUS 不是噪声——内核树里叫这个名字的文件有几千个，脚本是**猜**着校验的（它会挑「文档里最常用的那条全路径」，猜错了就等于没校验）。处置是把目录补全（那处指的是 `drivers/input/keyboard/` 下那个），不是登记忽略——**触发条件消掉之后，脚本现在报 0 条 AMBIGUOUS**。★★ 顺带一个自指陷阱：**这段文字本身一旦照抄那个基名加行号，就会再次触发同一条 AMBIGUOUS**（第一次写这段时就真的触发了一次），所以这里只描述、不复现格式（规则 28 的同一个机理）
- 它的另一个职责（抓「拿别的子系统文档集当模板起草」带来的串味文件名）在本套**没有触发**，因为本套是从零起草的，不是改别人的

★ 两个 ignore 文件（`.xref-ignore` / `.lineref-ignore`）**本套一条都没登记**（脚本输出里的「忽略项 0 条」）。规则 27/28 说「优先消除触发条件」，本套的做法是：把文件名写全路径、把「见下面的 §X」改成「见下一小节」、把文件名与章节号拆到两列——**触发条件消掉了，就不需要 ignore**。

---

## §9 如何从头复核这套文档

★ 按顺序做，越靠前的越便宜。前四步全是机械的，十分钟内能跑完；第 5 步开始才需要读代码。

**第 1 步：确认你和我在同一棵树上。**

```bash
git log -1 --format='%H %s'          # 应为 f6e38ae624cf... Linux 6.1.158
git status --porcelain | wc -l       # 本树 178；你的树若是 0，见第 3 步
```

★ 如果你的树是**干净的上游 6.1.158**，全套行号仍然有效，**只有 `drivers/tty/sysrq.c` 的 6 处要减 2**（§1.2）。

**第 2 步：确认 input 本身没被改。** 这条输出空就说明所有 `drivers/input/` 行号可直接用：

```bash
git status --porcelain -- drivers/input include/linux/input.h include/linux/input/ \
    include/uapi/linux/input.h include/uapi/linux/input-event-codes.h \
    include/uapi/linux/uinput.h
```

**第 3 步：★★ 把「文档引用过的路径」和「树的改动集」求交集。** 这是本套新增的一步，也是唯一能发现 `sysrq.c` 那种偏移的办法——**因为 input 的 handler 住在 tty / macintosh / net / sound 里，只查 `drivers/input/` 会漏**。脚本在 §1.2，直接复制运行。你的树若干净，它会输出 `0 个……在改动集里`。

**第 4 步：跑三个自检脚本。** 三条都应该「干净」：

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/input
python3 $S/check_xrefs.py     $D --ignore $D/.xref-ignore
python3 $S/check_line_refs.py $D . --ignore $D/.lineref-ignore
bash    $S/check_doc_coverage.sh input $D .
```

预期：`check_xrefs.py` 跨篇引用失效 **0**（命中 302 条）；`check_line_refs.py` 扫描 **896 条**唯一引用后报「干净」，**UNRESOLVED / 越界 / AMBIGUOUS 全为 0**；`check_doc_coverage.sh` 报【必查】里 **17 篇 0 引用**，那 17 篇全是 `Documentation/input/devices/*.rst`（处置 = 明确不覆盖，§2.3）。

★ 三个脚本抓不到的两类：**行号合法但指向空行或指向别的语法结构**（§7.1 第 1 条）、**章节号存在但不讲那件事**（§7.1 第 2 条）。这两类只能靠第 5、6 步。

**第 5 步：重跑所有数字。** §3.1 / §3.2 每一行都带命令，逐格对账。★ 两个提醒：(a) 你的 `grep` 可能是 GNU grep 而本套用的是 ugrep 7.5.0，九组关键统计已验证一致（§1.5），但**换一棵编译过的树就不一定**；(b) 碰到数字不一样先查 §3.2 的四对口径，多半是口径不同而不是数字错。

**第 6 步：抽查引用是否指向它该指的东西。**

```bash
# 从全套里随机抽 20 条带行号的引用打开看
grep -rhoE '`?(drivers|include|arch|net|sound|Documentation)/[A-Za-z0-9_./+-]+:[0-9]+(-[0-9]+)?' output/input/ \
  | tr -d '`' | sort -u | shuf -n 20 \
  | while IFS=: read f r; do echo "--- $f:$r"; sed -n "${r%-*},${r#*-}p" "$f"; done
```

★ 判据不是「行号在范围内」（脚本已经查过了），而是**「这一行是不是在讲文档说它讲的那件事」**。本套 34 处缺陷里有 5 处是这一类。

**第 7 步：挑一条论断做端到端复核。** 推荐这三条，它们分别代表三种最容易出错的论断形态：

| 论断 | 形态 | 复核方式 |
| --- | --- | --- |
| 「全树注册 handler 的地方共 **12** 处，但静态 `struct input_handler` 实例只有 **11** 个」 | 数字 + 口径差 | 跑 §3.1 末两行的命令，找出那个差额（`sound/usb/mixer_quirks.c:768`） |
| 「arm64 上 input 子系统的 tracepoint 覆盖 = **0**」 | 否定性全称断言 | `grep -rln 'TRACE_EVENT\|DECLARE_EVENT_CLASS' drivers/input/` 只应命中 `keyboard/applespi_trace.h`，再查它的 Kconfig 依赖（§2.7 第 3 条） |
| 「`touchscreen-y-plate-ohms` 是死字」 | 「找不到消费者」 | `grep -rn 'touchscreen-y-plate-ohms' --include=*.c .` 应为空，而它在 `Documentation/devicetree/bindings/input/touchscreen/touchscreen.yaml` 里有定义 |

**第 8 步：看该在哪加倍小心。** §5 的四层未验证项 + §6 的 24 条源码设计债。**§5.1 的第一行是「这套文档没在任何 arm64 机器上跑过」**——凡是涉及运行期输出的结论，请当成「读代码推出来的预期」。

---

## 篇末：这套文档的边界

| 问题 | 答案 |
| --- | --- |
| 覆盖了什么 | input 核心 16 个 `.c` / 9446 行**逐行读过**；5 个公开头文件 892 行逐行读过；四个子框架（serio / gameport / rmi4 / ff）+ 三个字符设备 handler 的框架形状；DT 属性的逐条消费者定位 |
| 没覆盖什么 | 410 个 `.c` 里的 394 个（只讲契约，选了 5 个代表驱动）；`input-event-codes.h` 的逐个码点；18 篇 `devices/*.rst`；x86 专属路径（i8042 / ACPI / applespi 协议部分） |
| 哪些结论可以直接用 | §2.1 一等证据支撑的、且**带具体行号**的机制结论 |
| 哪些要自己再确认 | §2.2 二等证据（只读符号表）、§5.3 的 7 条推断、§6 里带 ★ 的设计债 |
| 哪些一定要自己跑 | 全部数字（都配了命令）、`05-usage.md` 的代码骨架（未编译）、`06-devicetree.md` 的 DTS 片段（未 `dtc` 编译） |
| 最该记住的一件事 | ★★ input **没有自己的 `bus_type`**，它是「核心 + 一张多对多连接表 + 三条别人的子总线」。`/sys/bus/input/` 不存在不是内核的疏漏（§1.4） |
