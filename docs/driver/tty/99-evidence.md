---
title: 取证附录
---

# tty 核心层文档集：取证与未验证项登记

> 本篇不讲 tty，只回答一个问题：**前面九篇里每个数字、每个行号是怎么来的，
> 哪些是我读过的，哪些是我推断的。**
> 所有命令都在 `/workspace/safety/linux-6.1.y` 下可直接重跑。

---

## §1 基线与范围

### §1.1 ⚠️ 树的状态：`drivers/tty` 有 14 个脏文件，但核心层是干净的

这是本套文档最重要的一条前置事实，直接决定了范围裁剪。

```sh
git status --porcelain -- drivers/tty
```

输出 14 个修改过的文件，**全部落在串口驱动层**：

| 目录 | 文件 |
|---|---|
| `drivers/tty/serial/8250/` | `8250.h` · `8250_aspeed_vuart.c` · `8250_bcm7271.c` · `8250_core.c` · `8250_exar.c` · `8250_fsl.c` · `8250_ingenic.c` · `8250_mtk.c` · `8250_omap.c` · `8250_port.c` · `Kconfig` |
| `drivers/tty/serial/` | `amba-pl011.c` · `omap-serial.c` |
| `drivers/tty/` | `sysrq.c` |

规模：`git diff --shortstat -- drivers/tty` ⇒ **14 files changed, 269 insertions(+), 63 deletions(-)**。

补丁性质（不是猜的，是数出来的）：

```sh
git diff -- drivers/tty | grep '^+' | grep -oE 'printk_[a-z_]+' | sort | uniq -c
```

⇒ `printk_cpu_sync_get_irqsave` 15 · `printk_cpu_sync_put_irqrestore` 15 ·
`printk_prefer_direct_enter` 1 · `printk_prefer_direct_exit` 1。
**这是 printk/console 序列化的 RT 类 backport**，与 tty 核心层的语义无关。

核心层的干净性单独核过：

```sh
git status --porcelain -- drivers/tty/tty_*.c drivers/tty/n_tty.c drivers/tty/pty.c \
    include/linux/tty*.h include/uapi/linux/tty.h | wc -l
```

⇒ **0**。

⚠️ **范围决定**：本套 10 篇只覆盖 **tty 核心层**，即上面这条命令列出的那些文件。
串口驱动层 `drivers/tty/serial/`（顶层 75 个 `.c`，连子目录 120 个，约 15 万行）
**显式裁掉**，理由有两条，缺一不可：

1. 它是**另一层框架**（`uart_driver` / `uart_port` / `uart_ops` 自成一套），
   混在一起讲会让"谁调谁"彻底乱掉。
2. ⚠️ 它**被本树改过**。在一个已打补丁的层上给出行号，读者按行号去看会对不上，
   而且无法判断某个行为是上游的还是本树的。

同样理由裁掉的还有 `drivers/tty/vt/`（7 个 `.c`，虚拟控制台是独立子系统）、
`drivers/tty/hvc/`（13 个 `.c`）、`fs/devpts/`（文件系统）、
`n_gsm.c`（3889 行，单一 ldisc 的完整协议实现）。

### §1.2 基线（全部可重跑）

| 项 | 值 | 命令 |
|---|---|---|
| 内核版本 | `linux-6.1.158` | `head -5 Makefile` |
| commit | `f6e38ae624cf` | `git log --oneline -1` |
| 分支 | `linux-6.1.158` | `git branch --show-current` |
| 架构口径 | **arm64-only** | 全套约定 |
| `drivers/tty/` 总规模 | 198 个 `.c`/`.h` / 182949 行 | `find drivers/tty -name '*.[ch]' \| wc -l` |
| **核心层规模** | **12004 行 / 12 个 `.c` + `n_tty.c` + `pty.c`** | 见 §6 表 1 |
| 公共头 | 6 个 / 1748 行 | 见 §6 表 1 |

### §1.3 子系统类型判定：两道门

| 门 | 命令 | 结果 | 结论 |
|---|---|---|---|
| 门 1：有没有 `bus_type` | `grep -rn 'struct bus_type' drivers/tty/tty_*.c` | 无输出 | **框架型**，不是总线型 |
| 门 2：有没有 uapi | `ls include/uapi/linux/tty*.h` | `tty.h` `tty_flags.h` | **有用户态 ABI** |

⇒ 类型是"**框架型 + 带用户态 ABI**"，与 watchdog 同类；
但 ⚠️ **ABI 面积大得多**：termios 结构体 + 几十个 ioctl + 31 个行规程编号，
是本项目做过的所有子系统里最大的一个。这条判定决定了 01 篇 §0.6 和 00 篇 §2.4
必须专门开一节讲 ABI 边界。

### §1.4 官方文档登记

`Documentation/driver-api/tty/`，10 个文件 / 886 行：

| 文件 | 行数 | 本套怎么用 |
|---|---|---|
| `moxa-smartio.rst` | 197 | 选读（单一厂商设备） |
| `n_gsm.rst` | 153 | 选读（单一 ldisc 协议） |
| `tty_driver.rst` | 128 | **必读**，01 篇 §3.1 的"六个必需字段"直接来自它 |
| `tty_ldisc.rst` | 85 | **必读**，04 篇的三级兜底与两个引用计数警告来自它 |
| `tty_struct.rst` | 81 | **必读** |
| `index.rst` | 73 | **必读**，01 篇 §4.1 的"驱动侧五步"顺序来自它 |
| `tty_port.rst` | 70 | **必读**，01 篇 §4.3 的绑定优先级来自它 |
| `tty_buffer.rst` | 46 | **必读** |
| `tty_internals.rst` | 31 | 选读 |
| `n_tty.rst` | 22 | 选读 |

**必读六篇合计 483 行，开写前已全文读过**（技能规则 6 的阻塞前置）。

### §1.5 官方文档与代码的不一致

| 官方文档说 | 代码实际 | 处理 |
|---|---|---|
| `tty_ldisc.rst` 强调驱动要管好 ldisc 引用计数 | 源码注释 `drivers/tty/tty_port.c:275-277` 说明 **port 的引用计数是可选的** | 两处都记：01 篇 §5.5 记 ldisc 的两个计数，08 篇 §2 记 port 计数可选 |
| `tty_driver.rst` 列出六个必需字段 | 代码里 `tty_operations` 有 37 个回调，必需性没有编译期检查 | 01 篇 §2.8 单列"哪些是必需的"，并注明依据是文档而非代码强制 |
| 文档未提 `set_termios` 无法报错 | `drivers/tty/tty_ioctl.c:362-363` 回调是 `void` | 记进 00 篇 §2.4、05 篇 §5.3、08 篇 §8，作为一条 ABI 粗糙处 |

**冲突时一律以代码为准**，并在正文标明"文档这么说 / 代码这么做"。

---

## §2 阅读范围与证据等级

### §2.1 一等证据：逐行读过

| 文件 | 行数 | 用在哪 |
|---|---|---|
| `drivers/tty/tty.h`（**私有头**） | 119 | 07 篇 §1.2 的日志宏全表 |
| `include/linux/tty_ldisc.h` | 270 | 02 篇 §1.0.C/§1.0.E，04 篇 |
| `include/linux/tty_port.h` | 256 | 02 篇 §5.3–§5.4 |
| `include/linux/tty_buffer.h` | 60 | 02 篇 §5.5 |
| `include/linux/tty_flip.h` | 50 | 05 篇 §7.2 的四个插入函数 |
| `include/uapi/linux/tty.h` | 46 | 04 篇 §5.2 的 31 个编号 |
| `drivers/tty/tty_ldsem.c` | 430 | 08 篇 §5 |
| `drivers/tty/tty_mutex.c` | 55 | 02 篇 §6.2 |
| `drivers/tty/n_null.c` | 69 | 05 篇 §3.1 全文引用 |
| `drivers/tty/ttynull.c` | 109 | 05 篇 §1.1 全文引用 |
| `fs/proc/proc_tty.c` | 177 | 07 篇 §4 |

### §2.2 二等证据：读了关键函数与全部结构定义

| 文件 | 行数 | 读到什么程度 |
|---|---|---|
| `drivers/tty/tty_io.c` | 3608 | `tty_open` / `tty_init_dev` / `tty_ioctl` / `tty_release` / `show_cons_active` / 全部 30 个导出的签名与 kerneldoc 逐行读过；剩余部分只做 grep 定位 |
| `drivers/tty/tty_port.c` | 796 | `tty_port_init` / `tty_port_open` / `tty_port_block_til_ready` / `tty_port_close` 逐行；其余 grep |
| `drivers/tty/tty_ldisc.c` | 861 | 注册、`tty_set_ldisc`、`tty_ldisc_ref*`、sysctl 表逐行 |
| `drivers/tty/tty_ioctl.c` | 1004 | `set_termios` / `tty_set_termios` / ioctl 分派逐行 |
| `drivers/tty/tty_buffer.c` | 698 | 插入函数与 `flush_to_ldisc` 逐行 |
| `drivers/tty/tty_jobctrl.c` | 593 | `__proc_set_tty` / `tiocsctty` / `session_clear_tty` 逐行 |
| `drivers/tty/pty.c` | 947 | `pty_write` / `pty_open` / `pty_install` / `pty_show_fdinfo` 逐行 |
| `include/linux/tty.h` | 518 | 结构定义 + `TTY_*` 位号逐行；内联函数只读签名 |
| `include/linux/tty_driver.h` | 594 | `tty_operations` 37 个回调的 kerneldoc 逐条读过 |

### §2.3 三等证据：只做了符号级统计，没有逐行读

| 文件 | 行数 | 处理方式 |
|---|---|---|
| `drivers/tty/n_tty.c` | 2549 | 只读了 `n_tty_ops` 的填法、`#undef N_TTY_TRACE` 块、两处 `XXX`、`n_tty_inherit_ops`。**行编辑与 `c_lflag` 的逐条语义没有逐行核对**，正文里只写到"在 n_tty 里"这一层 |
| `drivers/tty/tty_audit.c` | 248 | 只登记存在与行数（00 篇 §7），没有读实现 |
| `drivers/tty/tty_baudrate.c` | 215 | 只读了 4 个导出的签名 |
| 20 个非默认 ldisc 实现 | — | 只统计与分类（04 篇 §5.1），没有读实现 |

### §2.4 代表实现的选法

05 篇需要"可全文引用的真实 in-tree 实现"。选法与结果：

| 角色 | 选中的实现 | 行数 | 为什么选它 |
|---|---|---|---|
| 最简 tty 驱动 | `drivers/tty/ttynull.c` | 109 | 全树最短的完整 tty 驱动，**能全文引用**，且不依赖任何硬件 |
| 行规程 | `drivers/tty/n_null.c` | 69 | 全树最短的 ldisc，**能全文引用** |
| 双端驱动 | `drivers/tty/pty.c` | 947 | 唯一使用 `driver->other` 的实现，且是 pty 语义的唯一来源 |
| port 用法 | `drivers/tty/tty_port.c` 自带的 `tty_port_open()` | — | 官方文档推荐驱动直接用它，不必自己写 |

⚠️ **刻意没有选串口驱动做样板**，尽管它们才是最常见的 tty 驱动。
理由就是 §1.1 那两条：另一层框架 + 被本树改过。
代价是 05 篇缺少"真实硬件驱动"的完整样例，只能给骨架。

### §2.5 裁掉的范围登记

| 裁掉的 | 规模 | 为什么裁 |
|---|---|---|
| `drivers/tty/serial/` | 顶层 75 个 `.c`（连子目录 120），约 15 万行 | 另一层框架；⚠️ **且被本树改过**（§1.1） |
| `drivers/tty/vt/` | 7 个 `.c` | 虚拟控制台是独立子系统 |
| `drivers/tty/hvc/` | 13 个 `.c` | hypervisor console，与通用 tty 语义关系薄 |
| **`drivers/tty/serdev/`** | 2 个 `.c` | 只交代它是 `tty_port_client_operations` 的主要使用者（04 篇 §6.2）与唯一真正走 DT 的路径（06 篇 §3）；**内部实现不覆盖** |
| **`fs/devpts/`** | — | 文件系统，不属 tty 核心。⚠️ 但它负责创建 `/dev/pts/N`，所以本套明确指出"pty 节点不是 `pty.c` 建的"（00 篇 §7、06 篇 §5.1），避免读者找错地方 |
| `drivers/tty/n_gsm.c` + `n_gsm.rst` | 3889 + 153 行 | 单一 ldisc 的完整协议实现 |
| `n_tty` 与 POSIX 的逐条一致性 | — | 需要 POSIX 规范文本；⚠️ 而且源码自己有两处 `XXX` 在问同类问题（§6 表 7） |
| 20 个非默认 ldisc 的实现 | — | 只统计与分类（04 篇 §5.1） |

**arm64-only 的具体落实**：00 篇 §2.6 与 06 篇只统计 `arch/arm64/boot/dts`；
x86 / powerpc / arm32 的差异一律不写，也不在正文里出现这些架构的行号。

### §2.6 与"相邻层"的边界怎么划的

tty 是一个"上下都有别的框架"的中间层，边界划错就会写出错误的归属。本套的划法：

| 现象 | 归谁 | 依据 |
|---|---|---|
| 波特率、引脚、时钟、DMA | **串口驱动层** | 核心层 DT API 零命中（§6 表 9） |
| `/dev/pts/N` 的创建 | **devpts 文件系统** | `TTY_DRIVER_DEVPTS_MEM` 的语义（04 篇 §4） |
| 早期 console 的选择 | **OF 层 + printk** | 06 篇 §4.1 的解析点不在 `drivers/tty/` |
| 键入内容的审计 | `tty_audit.c` 转交 **audit 子系统** | 只登记，未读实现（§2.3） |
| PPP / SLIP / CAN-over-serial | **各自的 ldisc 模块** | 04 篇 §5.1 |

### §2.7 ACPI 的裁剪登记

按全套约定，ACPI 相关内容一律裁掉（arm64-only 声明）。**在 tty 上这条裁剪
不产生任何信息损失**，因为：

```sh
grep -c -i 'acpi' drivers/tty/tty_*.c drivers/tty/n_tty.c drivers/tty/pty.c
```

⇒ **全部为 0**。tty 核心层完全不感知固件枚举方式——它只从驱动手里接过
一个已经能收发字节的对象（00 篇 §2.6）。

⚠️ 与其他子系统的区别值得记一句：在 pinctrl / gpio / regulator 这些子系统里，
裁掉 ACPI 意味着真的少讲了一条枚举路径；**在 tty 核心层里，那条路径根本不存在**。
如果确实需要 ACPI 上的串口枚举，那部分逻辑在被裁掉的串口驱动层里
（`8250_acpi` 之类），不在本套范围内。

---

## §3 符号存在性核查

### §3.1 核查口径（两级，全套统一）

| 说法 | 判据 |
|---|---|
| "**存在**" | grep 能命中定义。⚠️ 宏定义要用 `grep -nE 'define[[:space:]]+FOO'`——`#define` 与名字之间可能是 TAB，写死一个空格会漏 |
| "**外部驱动可用**" | 有 `EXPORT_SYMBOL*` **并且** 在 `include/linux/` 下的头里有声明。两条缺一不可 |

⚠️ 这两级在 tty 上差别很大，因为存在一个**私有头** `drivers/tty/tty.h`
（保护宏 `_TTY_INTERNAL_H`，`:7`）。里面的 `tty_debug()` / `tty_warn()` 等宏
"存在"但**外部驱动不可用**。07 篇 §1.2 和 05 篇 §7.12 都为此专门写了一条。

### §3.2 ⚠️ 抓到的计数口径问题

**问题 1：`} __randomize_layout;` 让结构末尾判断失效。**

`struct tty_operations`、`struct tty_struct` 等以 `} __randomize_layout;` 结尾，
不是 `};`。用"从 `{` 往下找第一个以 `};` 开头的行"来定位结构末尾会**冲过头**，
把后面的 kerneldoc 注释和下一个结构一起数进来。

⇒ 第一次数 `tty_operations` 得到 **76**，实际是 **37**。

正确做法两条，本套用的是第一条：

```sh
# 1) 先确认末行长什么样
sed -n '357,405p' include/linux/tty_driver.h | tail -3
# 2) 数花括号深度，只统计 depth==1 且以 ; 结尾的行，并排掉 #if/#endif
```

**问题 2：`tty_ldisc_ops` 实例数的两个口径。**

| 口径 | 命令 | 结果 |
|---|---|---|
| 名字只含小写字母和下划线 | `grep -rn 'struct tty_ldisc_ops [a-z_]* = {' --include=*.c .` | 17 |
| **名字含数字**（`can327_ldisc`、`cx81801_ops`、`nci_uart_ldisc` 等） | `grep -rn 'struct tty_ldisc_ops [a-z_0-9]* = {' --include=*.c .` | **20** |

⚠️ 差的 3 个全是名字里带数字的。**本套统一采用 20 这个口径**
（04 篇 §7.2 与 00 篇 §5 引用的都是它），并把命令写在正文里，读者可自行重跑。
这类"标识符里有数字"的漏计在内核代码里非常普遍，值得作为一条通用纪律。

### §3.3 计数纪律（本套遵守的四条）

1. **每个数字都附命令**，让读者能重跑。数字与命令不一致时以命令为准。
2. **字符类一律写 `[a-z_0-9]`**，不要只写 `[a-z_]`（§3.2 问题 2）。
3. **结构体字段数用花括号深度数**，不用 `grep -c ';'`，也不用"找 `};`"（§3.2 问题 1）。
4. **跨篇引用的统计量必须先查前面篇怎么写的**。00 篇写 `tty_ldisc_ops` = 20
   之前，先读了 04 篇 §7.2 的表格确认口径，避免同一套文档里两个数字打架。

---

## §4 索引工具（cbm）的盲区

本套用代码索引做过初步定位，但**所有写进正文的结论都用 `grep`/`sed` 复核过**。
在 tty 上索引的盲区特别明显，原因是这个子系统重度依赖宏和函数指针：

| 盲区 | 具体表现 | 应对 |
|---|---|---|
| **宏定义查不到** | `tty_debug` / `tty_warn` 这批日志宏、`TTY_*` 位号、`N_*` 编号全是宏，索引里没有节点 | 全部改用 `grep -nE 'define[[:space:]]+FOO'` |
| **间接调用查不到调用者** | `tty->ops->write` / `ld->ops->receive_buf2` 这类调用在索引里没有边，导致"谁调驱动的 ops"这个关键问题（02 篇 §1.0.D）根本查不出来 | 手工在 `n_tty.c` 与 `tty_io.c` 里 grep `->ops->` |
| **结构体成员赋值查不到** | 37 个 `tty_operations` 实例、20 个 `tty_ldisc_ops` 实例都是初始化器 | 用 `grep -rn 'struct X [a-z_0-9]* = {'` |
| **条件编译分支** | `CONFIG_CONSOLE_POLL` 下那 3 个回调（`include/linux/tty_driver.h:398-400`） | 读源码时手工看 `#ifdef` |

⇒ 一条通用结论：**框架型子系统里，索引能帮你找到"定义在哪"，
但回答不了"运行时谁调谁"**。tty 的核心难点恰好全在后者。

---

## §5 未验证项（四层，从最不确定到最确定）

### §5.1 第一层：未编译、未运行

- ⚠️ **本套文档全程没有编译过内核，没有跑过任何一条命令的实际输出。**
  正文里所有 `sh` 代码块（07 篇 §8 的速查、05 篇的用户态示例）
  **都是按源码语义写出来的，没有在真机上执行过**。
- 05 篇里的驱动骨架、ldisc 骨架、pty 示例**没有编译过**。
  它们对照的是 in-tree 真实实现（`ttynull.c` 109 行、`n_null.c` 69 行，都全文引用），
  但组合出来的代码没有过编译器。
- 07 篇 §1.3 的 `dynamic_debug` 命令行**没有实测**，写法依据的是
  `Documentation/admin-guide/dynamic-debug-howto.rst`。

### §5.2 第二层：只读了符号或完全没读

见 §2.3。需要特别点出的三处：

1. ⚠️ **`n_tty.c` 的 2549 行没有逐行读**。所以"n_tty 具体怎么实现 `ICANON`
   的行编辑"这类问题，本套只答到"在 `n_tty.c` 里"这一层，不给行号。
2. ⚠️ **`tty_audit.c` 完全没读**，只登记了行数（248）与"它把键入内容送进审计"
   这个从文件名与函数名推出的定位。
3. **20 个非默认 ldisc 的实现没读**，04 篇 §5.1 只做分类统计。

### §5.3 ⚠️ 第三层：推断而非观察

这一层最危险，逐条列出。**读者若要依赖这些结论，请自行验证。**

| 推断 | 依据 | 为什么算推断 |
|---|---|---|
| "打开动态调试后能多看到 23 处 `tty_debug`" | 07 篇 §1.3 的调用点统计（15+3+3+1+1） | **静态调用点数 ≠ 运行时实际打出的条数**，同一处可能打很多次，也可能一次不打 |
| "`set_ftrace_filter` 里写 `tty_*` 能覆盖绝大部分入口" | 核心层 97 个导出符号绝大多数以 `tty_` 开头 | 没实测过 ftrace 的匹配结果；静态函数不在导出表里，覆盖率可能更高或更低 |
| "驱动通常在不支持的波特率上静默回退到最近可用值" | `set_termios` 回调是 `void`，核心不校验 | **"通常"是行业惯例，不是代码保证**。具体驱动完全可以什么都不做 |
| "大多数 tty 驱动在 `/proc/tty/driver/` 下没有条目" | 条件是 `driver->ops->proc_show` 非空（`fs/proc/proc_tty.c:137-139`） | 没有统计全树有多少驱动实现了 `proc_show` |
| "`system_unbound_wq` 意味着处理顺序与 CPU 亲和性无关" | unbound 队列的语义 | 属于 workqueue 子系统的行为，本套没有读 workqueue 代码验证 |
| "串口驱动从 DT 读波特率/引脚/时钟" | 06 篇的三层分工表 | ⚠️ **串口层被本树改过且未纳入阅读范围**，这条是按框架分工推的 |
| "五层引用计数" 的层数 | 03 篇 §B3 的归纳 | "五层"是本套的归纳口径，源码里没有这个说法 |

### §5.4 第四层：版本时效性

| 项 | 说明 |
|---|---|
| 全部行号 | 只对 `f6e38ae624cf` 有效。⚠️ 上游 6.2 起 tty 有大规模重构（`tty_operations` 的 `write` 签名改成 `size_t`、`tty_port` 增加字段），**行号与部分签名都会变** |
| `CONFIG_LEGACY_PTYS` | 上游正在推动移除；本树 6.1 上仍默认 y |
| `drivers/tty/serial/` 的补丁 | 是本树独有的 RT backport，**上游 6.1.158 没有这些改动**（§1.1） |

---

## §6 可重跑的数字

### 表 1 规模

| 文件 | 行数 |
|---|---|
| `drivers/tty/tty_io.c` | 3608 |
| `drivers/tty/n_tty.c` | 2549 |
| `drivers/tty/tty_ioctl.c` | 1004 |
| `drivers/tty/pty.c` | 947 |
| `drivers/tty/tty_ldisc.c` | 861 |
| `drivers/tty/tty_port.c` | 796 |
| `drivers/tty/tty_buffer.c` | 698 |
| `drivers/tty/tty_jobctrl.c` | 593 |
| `drivers/tty/tty_ldsem.c` | 430 |
| `drivers/tty/tty_audit.c` | 248 |
| `drivers/tty/tty_baudrate.c` | 215 |
| `drivers/tty/tty_mutex.c` | 55 |
| **核心 `.c` 合计** | **12004** |
| `include/linux/tty_driver.h` | 594 |
| `include/linux/tty.h` | 518 |
| `include/linux/tty_ldisc.h` | 270 |
| `include/linux/tty_port.h` | 256 |
| `include/linux/tty_buffer.h` | 60 |
| `include/linux/tty_flip.h` | 50 |
| **公共头合计** | **1748** |
| `drivers/tty/tty.h`（私有） | 119 |
| `include/uapi/linux/tty.h` | 46 |
| `drivers/tty/Kconfig` | 385（30 个 `config`/`menuconfig` 条目） |
| `fs/proc/proc_tty.c` | 177 |

### 表 2 结构体字段数

口径见 §3.2 问题 1。

| 结构 | 位置 | 字段数 |
|---|---|---|
| `struct tty_operations` | `include/linux/tty_driver.h:357-403` | **37**（3 个在 `CONFIG_CONSOLE_POLL` 下，`:398-400` ⇒ 无条件 34） |
| `struct tty_driver` | `include/linux/tty_driver.h:442-473` | 21 |
| `struct tty_struct` | `include/linux/tty.h:193-251` | 46 |
| `struct tty_ldisc_ops` | `include/linux/tty_ldisc.h:211-248` | 18 |
| `struct tty_ldisc` | `include/linux/tty_ldisc.h:250-253` | **2** |
| `struct tty_port` | `include/linux/tty_port.h:100-123` | 22 |
| `struct tty_port_operations` | `include/linux/tty_port.h:33-39` | 5 |
| `struct tty_port_client_operations` | `include/linux/tty_port.h:41-46` | 3 |
| `struct tty_buffer` | `include/linux/tty_buffer.h:10-23` | 9 |
| `struct tty_bufhead` | `include/linux/tty_buffer.h:38-48` | 5 |

### 表 3 导出符号：97 个

口径 `grep -cE '^EXPORT_SYMBOL'`：

| 文件 | 数量 |
|---|---|
| `tty_io.c` | 30 |
| `tty_port.c` | 26 |
| `tty_ioctl.c` | 13 |
| `tty_buffer.c` | 11 |
| `tty_ldisc.c` | 7 |
| `tty_baudrate.c` | 4 |
| `tty_jobctrl.c` | 3 |
| `tty_mutex.c` | 2 |
| `n_tty.c` | 1 |
| `tty_ldsem.c` / `pty.c` | **各 0** |

⚠️ 最后一行值得注意：`tty_ldsem.c`（430 行）一个符号都不导出——
它是纯内部设施，这本身就是"别拿去别处用"的证据（08 篇 §5）。

### 表 4 用户态 ABI

| 项 | 值 | 命令 |
|---|---|---|
| uapi 头 | 4 个：`include/uapi/linux/tty.h` · `tty_flags.h` · `asm-generic/termbits.h` · `termbits-common.h` | `ls` |
| 行规程编号 | **31**（`N_TTY`=0 … `N_CAN327`=30），在 `include/uapi/linux/tty.h:10-41` | `grep -cE '^#define N_[A-Z0-9_]+' include/uapi/linux/tty.h` |
| `NR_LDISCS` | 31 | `grep -n 'NR_LDISCS' include/uapi/linux/tty.h` |
| `-ENOIOCTLCMD` | 515，定义在 `include/linux/errno.h:17`，约定说明在 `include/linux/tty_driver.h:134` | grep |
| 废弃标志日志 | `drivers/tty/tty_io.c:2604`，`pr_warn_ratelimited` | grep |

### 表 5 可观测面

| 项 | 值 | 命令（对 12 个核心 `.c`） |
|---|---|---|
| tracepoint | **0** | `grep -cE 'TRACE_EVENT\|DEFINE_EVENT'` ⇒ 全 0；`ls include/trace/events/ \| grep -i tty` ⇒ 无 |
| `trace_` 字样 | **1 处**，在 `drivers/tty/n_tty.c:83-88` 的 `#undef N_TTY_TRACE` 块里（硬关闭） | grep |
| debugfs | **0** | `grep -c debugfs` ⇒ 全 0 |
| sysfs 自定义属性 | **1 个**：`active`（`drivers/tty/tty_io.c:3563`） | `grep -n 'DEVICE_ATTR'` |
| sysctl | **1 个**：`dev.tty.ldisc_autoload`（`drivers/tty/tty_ldisc.c:827-838`） | grep |
| 故障注入 | **0**（无 `fault-inject.h`、无 `should_fail`、无 `CONFIG_FAIL_*`） | grep |
| procfs 入口 | `/proc/tty/ldiscs` · `/proc/tty/drivers` · `/proc/tty/driver/` · `/proc/tty/ldisc/` | `fs/proc/proc_tty.c:163-177` |

### 表 6 错误码普查（12 个核心 `.c`）

| 错误码 | 次数 | 错误码 | 次数 |
|---|---|---|---|
| `EFAULT` | 30 | `EPERM` | 10 |
| `EIO` | 20 | `ENOIOCTLCMD` | 10 |
| `ENOTTY` | 17 | `EAGAIN` | 8 |
| `EINVAL` | 15 | `ENODEV` `EINTR` `EBUSY` | 各 4 |
| `ERESTARTSYS` | 12 | `ESRCH` `EOVERFLOW` `EOPNOTSUPP` `EBADF` | 各 1 |
| `ENOMEM` | 11 | — | — |

⚠️ `ENXIO` **在整个核心层只出现 1 次**：`drivers/tty/tty_io.c:1885`，
`tty_open_current_tty()` 里"本进程没有控制终端"。这条是 §9 里被推翻的假设之一。

### 表 7 打印与设计债

| 项 | 值 |
|---|---|
| `WARN*` 处数 | 12：`tty_ldisc.c` 5 · `tty_buffer.c` 2 · `tty_port.c` 2 · `tty_io.c` 2 · `pty.c` 1 · `n_tty.c` **0** |
| `tty_debug` 调用点 | 23：`tty_io.c` 15 · `pty.c` 3 · `tty_ioctl.c` 3 · `tty_ldisc.c` 1 · `tty_jobctrl.c` 1 |
| **设计债** | **10 处**（本项目做过的子系统里最多） |

设计债逐条（`grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)'`）：

| 位置 | 内容 |
|---|---|
| `drivers/tty/tty_io.c:1026` | `/* FIXME! Have Al check this! */` |
| `drivers/tty/tty_io.c:2267` | `FIXME: does not honour flow control ??` |
| `drivers/tty/tty_io.c:2813` | `compat_ulong_t iomap_base FIXME` |
| `drivers/tty/tty_io.c:2995` | 经 `AF_UNIX` 传递 fd 的场景，`FIXME` |
| `drivers/tty/tty_ioctl.c:239` | `What should we do for i/ospeed` |
| `drivers/tty/tty_ioctl.c:353` | **"we need to decide on some locking/ordering semantics"** |
| `drivers/tty/tty_ioctl.c:540` | `tmp_termios == tty->termios` 时的行为存疑 |
| `drivers/tty/tty_jobctrl.c:341` | **"Review locking here. The tty_lock never covered any race"** |
| `drivers/tty/n_tty.c:1348` | `XXX are EOL_CHAR and EOL2_CHAR echoed?!?` |
| `drivers/tty/n_tty.c:1358` | `XXX does PARMRK doubling happen for ...` |

⚠️ 其中 `tty_ioctl.c:353` 与 `tty_jobctrl.c:341` 是**源码自陈锁语义没定清**，
03 篇 §C2 专门展开。这两条不是"待优化"，是"当前行为可能就是不对的"。

### 表 8 全树实例统计

| 项 | 数量 | 命令 |
|---|---|---|
| `struct tty_operations` 实例（全树 `.c`） | **37** | `grep -rn 'struct tty_operations [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| ——分布 | `drivers/tty` 13 · `drivers/tty/hvc` 3 · `drivers/usb/serial` 2 · `drivers/tty/serial` 2 · 其余各 1（`usb/host`、`usb/gadget/function`、`usb/class`、`tty/vt`、`tty/ipwireless`、`staging/greybus`、`staging/gdm724x`、`s390/char`） | 同上加 `xargs dirname \| sort \| uniq -c` |
| `struct tty_port_operations` 实例 | **28** | `grep -rn 'struct tty_port_operations [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| `struct tty_ldisc_ops` 实例 | **20** | 口径见 §3.2 问题 2 |
| `pty.c` 里的 `tty_operations` | 4 | `grep -c ... drivers/tty/pty.c` |
| `driver->other` 赋值点 | 4，全在 `pty.c` | `grep -rn '\->other = ' --include=*.c .` |

⚠️ **37 这个数字与 `tty_operations` 的字段数 37 是巧合**，两者毫无关系。
写正文时刻意把两处分开表述，避免读者误以为有对应关系。

### 表 9 arm64 与设备树

| 项 | 值 | 命令 |
|---|---|---|
| 核心层 DT API 命中 | **0** | `grep -cE 'of_[a-z_]+\(\|device_node' <12 个核心 .c>` |
| arm64 DTS 里写 `stdout-path` 的文件数 | **396** | `grep -rl 'stdout-path' arch/arm64/boot/dts \| wc -l` |
| `drivers/tty/serial/` 顶层 `.c` | 75（连子目录 120） | `ls drivers/tty/serial/*.c \| wc -l` |
| 常见 arm64 串口驱动 | `amba-pl011.c` · `8250/8250_dw.c` · `qcom_geni_serial.c` · `samsung_tty.c` | `ls` |
| ACPI 命中（核心层） | **0** | `grep -c -i acpi <12 个核心 .c>` |

---

## §7 三个批量自检脚本的结果

```sh
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/tty
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh tty $D .
```

| 脚本 | 结果 |
|---|---|
| `check_xrefs.py` | 10 篇，跨篇引用 **100 条全部命中** |
| `check_line_refs.py` | 扫描 **500 余条**唯一 `file:line` 引用，**无解析不到的文件，无越界行号** |
| `check_doc_coverage.sh` | 9 篇必查官方文档**全部有引用**，最少的 `moxa-smartio` 2 次，最多的 `tty_port` 394 次 |

⚠️ `check_xrefs.py` 第一次跑出 **3 条失效**：04 篇两处引 `99 §2.5`、
06 篇一处引 `99 §2.7`，而本篇当时只写到 §2.4。
⇒ **这是"99 最后写"这个顺序的固有风险**：前面九篇会预先引用 99 的小节号。
修法是按被引用的编号补齐本篇结构（现在的 §2.5 / §2.7 就是这么来的），
**不是**去改前面九篇。

⚠️ `check_line_refs.py` 也抓到过一次真实越界：`proc_tty_init()` 的区间被写成了
163 到 178 行，而该文件只有 177 行。修正为 `fs/proc/proc_tty.c:163-177`，
并顺带纠正了两处 `proc_create_seq` 的偏移一行（详见 §9）。**这个脚本抓到的是"读者按行号点过去看到别的东西"这类错误，
是本套最有价值的自检。**

---

## §8 逐篇取证表

| 篇 | 行数 | 主要证据来源 | 本篇的薄弱处 |
|---|---|---|---|
| `00-overview.md` | 499 | **纯浓缩**，所有结论来自 01–08；新增的只有 §2.5 的选择判据与 §2.6 的 arm64 统计 | §2.6 关于串口驱动读 DT 的分工是推断（§5.3） |
| `01-api.md` | 721 | 97 个导出符号逐个核过；`tty_operations` 37 个回调的 kerneldoc 逐条读过；官方文档六篇 | §2.8 的"哪些必需"依据是文档，非编译期强制 |
| `02-datastructures.md` | 534 | 全部结构定义逐行读；锁全景表来自 `grep` + 逐处确认 | §6.2 的 14 把锁里有几把只见定义未见完整用法 |
| `03-internals.md` | 645 | `tty_open` / `tty_init_dev` / `tty_release` / `tty_set_ldisc` 逐行；kerneldoc 里的锁序逐字引用 | §B3 的"五层引用计数"是本套归纳口径，源码无此说法 |
| `04-extensions.md` | 455 | ldisc 注册路径逐行；20 个实例的全树统计 | 20 个实现只分类未读（§2.3） |
| `05-usage.md` | 1240 | `ttynull.c`（109）与 `n_null.c`（69）**全文引用**；12 组近邻符号逐组核过 | ⚠️ **所有代码骨架未编译**（§5.1） |
| `06-devicetree.md` | 618 | 核心层 DT 零命中的取证；serdev 的 DT 扫描点逐行 | serdev 内部实现未读，见本篇的裁剪登记 |
| `07-debug.md` | 573 | 日志宏、sysctl、sysfs 属性、`proc_tty.c` 逐行；错误码普查 | ⚠️ 所有命令**未实测**（§5.1） |
| `08-feynman.md` | 473 | 八个概念全部落到具体行号；`tty_ldsem.c:5-9` 的设计意图逐字引用 | 类比本身不是证据，第四段专门讲它在哪失效 |
| `99-evidence.md`（本篇） | 本文件 | 全部命令可重跑 | §5.3 那七条推断是本套最需要读者自行验证的部分 |

**10 篇合计 6300 余行。**

---

## §9 交叉验证实例：初始假设被推翻的七条

这一节是本篇最值得读的部分。**每一条都是"先写下了一个看起来合理的说法，
然后跑命令发现是错的"**——列出来是为了说明哪类判断不能凭直觉。

### 1. `ENXIO` 的来源

- **初始假设**：`open()` 返回 `ENXIO` 是驱动的 `install`/`activate` 拒绝了打开。
- **实际**：错误码普查里 `ENXIO` 一次都没出现在那些路径上。
  单独 grep 发现整个核心层只有 **1 处**：`drivers/tty/tty_io.c:1885`，
  在 `tty_open_current_tty()` 里，含义是"**本进程没有控制终端**"。
- **怎么发现的**：写 07 篇 §7.2 的错误码表时，发现表里的 `ENXIO` 在 §6.1 的
  普查表里找不到对应行——两张表自相矛盾。
- ⇒ 修正后这条反而成了最有价值的一条：`setsid()` 之后 `open("/dev/tty")`
  必然失败，是守护进程的经典陷阱（08 篇 §6）。

### 2. 未知 ioctl 的 errno

- **初始假设**：核心不认识的 ioctl 一律返回 `ENOTTY`。
- **实际**：逐行读 `drivers/tty/tty_io.c:2768-2789` 发现 `:2781` 先把
  `retval` 预置成 **`-EINVAL`**，而 `-ENOTTY` 的赋值只在
  `if (ld->ops->ioctl)` 成立的分支里（`:2787`）。
  ⇒ **当前行规程没有 `ioctl` 回调时，未知 ioctl 得到的是 `EINVAL`**。
- ⇒ 这是 ABI 层面一个真实的粗糙处：同一个请求换个行规程 errno 就变。
  写进 07 篇 §6.2 与 00 篇 §2.4。

### 3. `proc_tty.c` 的行号越界

- **初始假设**：`proc_tty_init()` 的区间是 `:163-178`。
- **实际**：`wc -l fs/proc/proc_tty.c` ⇒ **177**。越界一行，
  且两处 `proc_create_seq` 的行号也偏了一行。
- **怎么发现的**：`check_line_refs.py`。
- ⇒ 纪律：**行号区间的上界必须与 `wc -l` 对一次**，尤其是"到文件末尾"的区间。

### 4. `tty_operations` 的字段数

- **初始假设**：76 个字段。
- **实际**：**37 个**。原因是结构以 `} __randomize_layout;` 结尾，
  "找第一个以 `};` 开头的行"冲过了结构末尾。
- ⇒ 口径纪律见 §3.2 问题 1。这个坑对 `tty_struct`、`tty_driver` 同样成立。

### 5. `tty_ldisc_ops` 实例数

- **初始假设**：17 个（用 `[a-z_]*` 匹配变量名）。
- **实际**：**20 个**。漏掉的 3 个变量名里带数字（`can327_ldisc` 等）。
- **怎么发现的**：与 04 篇 §7.2 已经写好的数字对不上。
- ⇒ 纪律：**标识符的字符类一律写 `[a-z_0-9]`**。

### 6. `tty_port` 的引用计数是不是强制的

- **初始假设**：驱动必须用 `tty_port_put()` 管好 port 引用，
  只有 `tty_port_init()`/`tty_port_destroy()` 而没有 `put` 就是泄漏。
- **实际**：源码注释 `drivers/tty/tty_port.c:275-277` 明确留了第二条路：
  "**要么间接通过引用计数，要么在不用引用计数时直接销毁**"。
- ⇒ 引用计数是**可选机制**，不是强制协议。写进 08 篇 §2 的"类比失效"一段。
  ⚠️ 这条如果不改，会让读者把一大批正常驱动误判为泄漏。

### 7. 核心层到底有没有碰设备树

- **初始假设**：`grep -cE 'of_[a-z_]+\(|device_node'` 在 `tty_jobctrl.c` 上
  命中 2 次，说明核心层有 DT 代码。
- **实际**：两处都是 **`session_of_pgrp()`** 这个函数名的假阳性
  （`:441` 定义、`:522` 调用）。
- ⇒ 核心层 DT 参与度确认为 **0**。
  纪律：**`of_` 这个前缀的 grep 必须逐条看命中行**，
  内核里 `..._of_...` 形式的函数名太多。

⚠️ 七条里有 **4 条（1、3、4、5）是靠"两张表互相矛盾"或脚本发现的**，
不是靠重读源码发现的。⇒ **同一事实在多处重复出现，是最有效的自检机制**；
只写一遍的数字没有交叉验证的机会。

---

## §10 对照与回写

### §10.1 rule-29 独立对照：本子系统没有对照套

```sh
ls output/qoder/
```

⇒ `clk  dma  gpio  irq  pinctrl  spi`。**没有 `tty`。**

⇒ 本套**无法做 rule-29 的独立对照**。这意味着本套 10 篇缺少一次
"别人怎么讲同一个子系统"的外部校验，§5.3 那七条推断也没有第二方复核。
按约定登记于此，不做替代性对照。

### §10.2 要回写 skill 的四条

| 条目 | 内容 |
|---|---|
| 1 | ⚠️ **结构体末尾可能是 `} __randomize_layout;`**。数字段数前先看末行长什么样（§3.2 问题 1）。此坑不限 tty，`include/linux/` 下大量核心结构都带这个标注 |
| 2 | ⚠️ **标识符的 grep 字符类一律 `[a-z_0-9]`**（§3.2 问题 2）。少写 `0-9` 在本套已造成一次统计错误 |
| 3 | ⚠️ **`99` 最后写会撞已被引用的小节号**（§7）。对策：`01`–`08` 里凡是引 `99 §x.y`，写完后先跑一次 `check_xrefs.py`，按报错补齐 99 的结构；不要回改前面九篇 |
| 4 | **`of_` / `_of_` 前缀的 grep 必须逐条看命中行**（§9 第 7 条）。用 `grep -c` 直接下结论会得到假阳性 |

### §10.3 明确不吸收的两条

| 不吸收的做法 | 为什么 |
|---|---|
| "把串口驱动层一起写进来，读者才有真实硬件样例" | 代价太大且有害：它是另一层框架（**会让"谁调谁"彻底混乱**），而且**被本树改过**，给出的行号读者对不上。缺样例这个代价是明知而接受的（§2.4） |
| "给每个结论加重要度标记，方便读者挑重点" | 标记不携带信息，只会指数膨胀并挤掉正文。重要度靠**章节顺序与标题层级**表达；只保留 `⚠️` 标真陷阱、`**粗体**` 标句内关键短语 |
