---
title: 取证附录
---

# 99 取证与方法：这套文档凭什么这么说

> 本篇不讲 regulator，讲**这套文档自己的可信度**。
> 前九篇的每条论断，在这里能查到它是"读到的"还是"推出来的"。
> 读者用它决定：哪些结论可以直接拿去改代码，哪些必须自己再确认一遍。

---

## §1 基线

### §1.1 代码基线

| 项 | 值 | 复现命令 |
|---|---|---|
| 版本 | Linux **6.1.158** | `git log -1 --oneline` |
| 提交 | `f6e38ae624cf` | 同上 |
| 分支 | `linux-6.1.158` | `git branch --show-current` |
| 目标架构 | **arm64 only** | — |

**这棵树不是纯净的上游树** —— `git status` 显示上百个文件被本地改动
（含 `arch/arm64/`、`drivers/net/`、`drivers/gpu/` 等）。
所以第一件必须查清的事是：**regulator 子系统有没有被改？**

```bash
git status --porcelain -- drivers/regulator \
                          include/linux/regulator \
                          include/trace/events/regulator.h
```

**输出为空。**

⇒ **本文档描述的 regulator 子系统与 v6.1.158 上游完全一致，未被本树的本地补丁触及。**
这条结论的意义：前九篇里所有行号，在任何一棵干净的 v6.1.158 树上都能直接对上。

### §1.2 子系统类型判定

按 skill 阶段 1 的第 3b 条做判定：

```bash
rg 'struct bus_type' drivers/regulator/ include/linux/regulator/
```

无输出。regulator **没有自己的 `bus_type`**，只有一个
`struct class`（`regulator_class`，声明在 `drivers/regulator/internal.h:61`）。

⇒ 判定为 **框架型子系统**。模板按框架型适配的结果：

| 模板项 | 适配 |
|---|---|
| 02 的总线关系图 | 改画"核心抽象 ↔ 消费者/PMIC 驱动"三角关系 |
| 03 的主轴 | 注册 / get / 开关 / 调压，而非"传输" |
| 04 | 有子框架（helpers / coupler / irq_helpers / devres），独立成篇 |
| 06 | 有完整 DT 绑定（120 篇），正常成篇 |

### §1.3 官方文档（三等证据）登记

⚠️ skill 第 6 条要求：读官方文档是**写文档之前的阻塞步骤**。
本套读了 **6 个文件、744 行，全部读全，无抽样**：

| 文件 | 行数 | 是否读全 | 纳入了哪些内容 |
|---|---|---|---|
| `Documentation/power/regulator/overview.rst` | 178 | ✅ | 术语（consumer / regulator / PMIC）、供电树概念 → 00 §2.1、00 §3.1 |
| `Documentation/power/regulator/consumer.rst` | 229 | ✅ | 消费者 API 的官方用法与语义 → 01、05 §1 |
| `Documentation/power/regulator/machine.rst` | 97 | ✅ | 板级约束的设计意图 → 00 §4.4、06 §1 |
| `Documentation/power/regulator/design.rst` | 38 | ✅ | **设计 rationale**，见下 |
| `Documentation/power/regulator/regulator.rst` | 32 | ✅ | PMIC 驱动侧接口概览 → 05 §2 |
| `Documentation/driver-api/regulator.rst` | 170 | ✅ | 术语表 + 三条关键语义声明，见下 |

**`design.rst` 提供的、代码里读不出来的两条设计取向**：

1. 安全性优先（`Documentation/power/regulator/design.rst:5-9`）：
   「配置错误可能造成非常严重的后果，**包括永久性硬件损坏**」。
2. 信息不足时不动手（`Documentation/power/regulator/design.rst:11-16`）：
   「**无法自动判定一个系统的供电配置**」，因此
   「除非明确知道某项改动在这个具体系统上是安全的，**API 不应该改变硬件状态**」。

这两条是 dummy regulator、`have_full_constraints`、
以及"权限默认不给、要 DTS 显式授予"这整套取向的官方依据。
它们被写进了 08 §1 的「误导点 1」。

**`driver-api/regulator.rst` 提供的三条官方语义声明**（都在文档里被引用）：

| 官方原文位置 | 声明 | 用在哪 |
|---|---|---|
| `Documentation/driver-api/regulator.rst:60` | 消费者接口「提供了一套和内核 clock 框架相似的 API」 | 00 §7.3 的 clk 对照 |
| `Documentation/driver-api/regulator.rst:83-88` | 「**调用 `regulator_disable()` 并不保证这一路电真的会被关掉**」 | 08 §3 |
| `Documentation/driver-api/regulator.rst:97-102` | 调压/限流「都接受区间；**需要精确值时把 min 和 max 写成同一个数**」 | 08 §5、05 §1.4 |

**一处官方文档已过时**：`Documentation/driver-api/regulator.rst:66-70`
说驱动注册「应当由平台通过 `struct regulator_init_data` 显式 setup 来触发」。
这在 arm64 DT 启动上**不成立** —— init_data 由
`drivers/regulator/of_regulator.c` 从 DT 现场构造，
平台代码不提供任何 `regulator_init_data`。
按 skill 第 6 条的规定：**冲突处以代码为准**，
文档正文（05 §2、06 §1）只讲 DT 路径，platform data 路径列入 §2.7 裁剪清单。

### §1.4 工具版本

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本 |
| GNU grep | 3.7 | 精确取证（`/usr/bin/grep`） |
| ripgrep | 13.0.0 | 全树普查 |
| ugrep | 7.5.0 | shell 里的 `grep` 实为 ugrep，见 §7.2 |
| git | 2.34.1 | 基线 |

---

## §2 读取范围

### §2.1 详读（一等证据来源）

以下文件**逐行读完**，本套文档里所有涉及它们的行号都是直接观察：

| 文件 | 行数 | 说明 |
|---|---|---|
| `drivers/regulator/core.c` | 6310 | 核心，全篇详读 |
| `drivers/regulator/of_regulator.c` | 807 | DT 解析，全篇详读 |
| `drivers/regulator/helpers.c` | 968 | regmap 辅助，全篇详读 |
| `drivers/regulator/devres.c` | 662 | devm_* 包装，全篇详读 |
| `drivers/regulator/irq_helpers.c` | 439 | 错误中断，全篇详读 |
| `drivers/regulator/dummy.c` | 90 | 全篇详读 |
| `drivers/regulator/fixed.c` | 356 | 全篇详读 |
| `drivers/regulator/fixed-helper.c` | 61 | 全篇详读 |
| `drivers/regulator/userspace-consumer.c` | 181 | 全篇详读 |
| `drivers/regulator/virtual.c` | 374 | 全篇详读 |
| `include/linux/regulator/consumer.h` | 723 | 全篇详读 |
| `include/linux/regulator/driver.h` | 783 | 全篇详读 |
| `include/linux/regulator/machine.h` | 292 | 全篇详读 |
| `include/linux/regulator/coupler.h` | 100 | 全篇详读 |
| `include/linux/regulator/of_regulator.h` | 46 | 全篇详读 |
| `drivers/regulator/internal.h` | 125 | 全篇详读 |
| `include/trace/events/regulator.h` | — | 全篇详读（11 个 tracepoint） |

合计约 **12.3k 行**，是"核心 + 框架 + 全部通用消费者/占位驱动"的完整闭包。

### §2.2 只读符号表 / 只讲契约（二等证据）

`ls drivers/regulator/*.c | wc -l` = **184**。
扣掉 §2.1 里的 10 个通用文件，剩下约 **174 个芯片驱动**没有详读。
按 skill 第 4.1 条登记代表驱动的选法：

| 代表驱动 | 选它的理由 | 详读了哪部分 |
|---|---|---|
| `drivers/regulator/88pg86x.c` | **最简实现**：regmap + 线性范围，全文 100 行出头 | 详读了 `:14-18` 的 `linear_ranges` 定义（00 §2.4 的 step=0 惯例） |
| `drivers/regulator/rk808-regulator.c` | **全功能实现**，且本树 arm64 DTS 真实在用（rock-pi-4） | 详读了 `:69-80` 的 `RK817_BOOST_DESC` 宏与 `:532-542` 的 `of_map_mode` |
| `drivers/regulator/fixed.c` | **无寄存器路径**：只有 GPIO / 常开 | 全篇详读（已在 §2.1） |

**其余 174 个驱动只讲契约，不讲实现。**
所有形如「PMIC 驱动应当/通常/可以……」的表述，
依据是 `include/linux/regulator/driver.h` 里的字段与回调语义，
**不是**对 174 个驱动的普查结果。
唯一例外是 §2.4 里明确标了"全树普查"的几条计数。

### §2.3 官方文档 0-引用文件的处置

skill 第 26 条要求收尾时跑 `check_doc_coverage.sh` 反查。
本套跑出 **2 个 0-引用文件**，逐个处置如下：

| 文件 | 0 引用的原因 | 处置 |
|---|---|---|
| `Documentation/power/regulator/design.rst` | 读了、内容被吸收进 08 §1，但当时没标出处 | ✅ **"覆盖了没标"** —— 已回填引用（08 §1 误导点 1、本篇 §1.3） |
| `Documentation/driver-api/regulator.rst` | 同上：三条语义声明分别支撑 08 §3、08 §5、00 §7.3，均未标出处 | ✅ **"覆盖了没标"** —— 已回填三处引用（08 §3、00 §7.3、本篇 §1.3） |

**两者都不是"漏读"**，是标注缺失。
处置方式按 methodology §G 的第 (b) 类：**回填引用，不改结论** ——
因为这两份文档的内容与代码读出的结论**一致**，属于印证而非新信息。

一个有价值的副产品：`driver-api/regulator.rst:83-88`
把 08 §3 那条"disable 可能一点电都没关"从
**"我读代码读出来的行为"** 升级成 **"官方写进契约的语义"**。
证据等级从一等（观察）+ 推断，变成一等 + 三等双向印证。

### §2.4 全树普查（可复现的计数）

以下数字是本套文档里唯一几条"全树"性质的断言，全部可一条命令重跑：

| 断言 | 命令 | 结果 |
|---|---|---|
| regulator 驱动文件数 | `ls drivers/regulator/*.c \| wc -l` | **184** |
| tracepoint 个数 | `/usr/bin/grep -c 'TRACE_EVENT\|DEFINE_EVENT' include/trace/events/regulator.h` | **11** 不是 12 |
| DT 绑定篇数 | `ls Documentation/devicetree/bindings/regulator/ \| wc -l` | **120** |
| `-ENOTSUPP` 全树用量 | `rg -c '\-ENOTSUPP' --stats` | 524（对照 `-EOPNOTSUPP` 95） |
| `fixed-helper.c` 的调用者 | `rg -n 'regulator_register_always_on'` | 仅定义处，**全树零调用** |

### §2.7 显式裁剪清单（裁掉了什么）

skill 阶段 1 第 3 条要求：裁掉的内容必须显式登记，否则读者会当成遗漏。

| 裁掉的内容 | 理由 | 涉及的代码 |
|---|---|---|
| **x86 / powerpc / arm32 相关路径** | 用户明确指定 arm64-only | 各 `arch/*` 下的 regulator 平台代码 |
| **ACPI 路径** | 用户明确要求"ACPI 的不用在文档里描述" | `drivers/regulator/` 下无独立 ACPI 文件；`regulator_get` 的非-DT 分支未展开 |
| **platform data（`regulator_init_data`）注册路径** | arm64 DT 启动上不走这条；官方文档在这点上已过时（§1.3） | `include/linux/regulator/machine.h:262` 的结构体只在 00 §4.6 列了字段，不讲用法 |
| **174 个芯片驱动的具体实现** | 规模不可控，且对理解框架无增量 | 见 §2.2 |
| **非 DT 的 `regulator_bulk_*` 静态数组用法** | 同 platform data | 01 里列了 API，05 只给 DT 版骨架 |
| **`CONFIG_REGULATOR_VIRTUAL_CONSUMER` 之外的测试设施** | 本树无其他 | — |

**注意区分"裁掉"和"没有"**：上表是**主动裁掉**的。
真正"这个子系统就没有"的东西记在 §6（自陈问题）里，不在这张表。

---

## §3 证据等级

本套文档的每条论断落在三个等级之一。**读者用它决定要不要自己复核**。

### §3.1 一等证据：直接观察

**定义**：我打开了那个文件的那几行，把源码原样搬进了文档。

- 覆盖范围：§2.1 列的 17 个文件的**全部**内容
- 特征：文档里带 `文件路径:行号` 且**贴了代码块或逐字引用**的地方
- 可信度：**可以直接据此改代码**。行号可能因后续 stable 更新漂移，但语义不会错

本套约 **454 条唯一 `file:line` 引用**（`check_line_refs.py` 的统计），
其中绝大多数是一等。

### §3.2 二等证据：读了符号/签名，没读实现

**定义**：我读了函数原型、结构体字段、kernel-doc 注释，但没逐行读它的实现体。

具体是哪些：

| 内容 | 详读到什么程度 |
|---|---|
| 174 个芯片驱动 | 只看了它们**如何填 `regulator_desc` / `regulator_ops`**（即契约面），没读寄存器逻辑 |
| `regmap` 子系统内部 | 只当作"最终会写到 i2c/spi"的黑盒（00 §6.2 的阶段 ④ 明确标了跨子系统边界） |
| `ww_mutex` 的内核实现 | 只读了 regulator 侧的用法，没读 `kernel/locking/ww_mutex.h` |
| DT 绑定的 120 篇 schema | 只详读了 06 篇引用到的那几篇 |

**二等证据在文档里的标记方式**：措辞用「按契约……」「`desc` 里声明的语义是……」，
而不是「这个驱动会……」。凡是出现具体驱动行为断言的，都点了名（§2.2 的三个代表驱动）。

### §3.3 三等证据：官方文档

- 覆盖范围：§1.3 的 6 个文件，744 行，全部读全
- **风险**：内核文档会落后于代码。本套发现 **1 处过时**（§1.3 的 init_data）
- **处置规则**：文档与代码冲突时**以代码为准**，并在正文显式指出

三等证据在本套里只承担两种角色：
(a) 提供**设计意图**（代码里读不出来的"为什么"）；
(b) **印证**已由一等证据得出的结论。
**没有任何一条论断是仅凭官方文档成立的。**

---

## §4 交叉验证实例（初始假设被推翻的记录）

skill 第 25 条要求 ≥3 条。以下 5 条都是**我先写错了、后来被源码推翻**的真实记录。
留在这里是因为它们标出了本子系统最容易误判的位置。

### §4.1 `-EPERM` 的来源：耦合冲突 → `rdev->exclusive`

- **初始假设**：`regulator_get` 返回 `-EPERM` 是耦合（coupling）配置冲突。
- **推翻方式**：把 `-EPERM` 在 `core.c` 里的所有出现点逐个 `sed` 打开。
- **真相**：`drivers/regulator/core.c:2245` 那处 `-EPERM` 的判据是
  `rdev->exclusive` —— 即**已经有人用 `regulator_get_exclusive` 独占了这一路**，
  与耦合无关。耦合冲突走的是另外几处（`:4163`、`:5347`、`:5460`、`:5481`）。
- **修正落点**：07 §7.1 把 `-EPERM` 按**四个语义组**展开
  （`valid_ops_mask` / suspend 不可变 / 独占 / 耦合），而不是罗列行号。
- **教训**：**同一个错误码在一个文件里有多个语义完全不同的来源**。
  只 grep 到行号就下结论，必然出错。

### §4.2 `-EALREADY` 根本不是返回码

- **初始假设**：`-EALREADY` 是 regulator 的一个返回码，需要在错误码表里给它一行。
- **推翻方式**：`rg -n 'EALREADY' drivers/regulator/`。
- **真相**：全子系统**只有一处**，而且是
  `WARN_ON_ONCE(err != -EALREADY)`（`drivers/regulator/core.c:344`）——
  它是**断言的一部分**，不是任何函数的返回值。
- **修正落点**：07 §7 明确写成「**不是返回码**」。
- **教训**：grep 到符号 ≠ 它在语义上存在。**要看它在语法上的位置**。

### §4.3 `-EDEADLK` 和 `-EDEADLOCK` 是两种拼法

- **初始假设**：ww_mutex 路径统一用 `-EDEADLK`，行号是 `:164`、`:231`、`:249`、`:338`、`:420`。
- **推翻方式**：把这 5 行逐个 `sed` 打开核对（第 19 条：不抽样）。
- **真相**：`:164`、`:338`、`:420` 是 `-EDEADLK`，而
  `drivers/regulator/core.c:231` 和 `:249` 写的是 **`-EDEADLOCK`**。
  arm64 上两者数值相同，行为无差别。
- **修正落点**：08 §9 拆成两组并加警告：**只 grep 一种拼法会漏掉另外两处**。
- **教训**：**同义宏的混用是 grep 的系统性盲区**，而它恰好最不容易被注意到
  （因为行为正确，测不出来）。

### §4.4 `check_doc_coverage.sh` 的参数语义

- **初始假设**：脚本第一个参数是"子系统昵称"，而它找不到
  `Documentation/power/regulator/` 这个目录，所以对本子系统无效。
- **推翻方式**：直接试 `bash <脚本> power/regulator output/regulator .`。
- **真相**：参数是**相对 `Documentation/` 的路径**。传 `power/regulator`
  正确找出全部 5 篇概念文档；传 `regulator` 找出的是
  `Documentation/driver-api/regulator.rst` 加 DT 绑定。
  **脚本没有缺陷，是我用错了。**
- **修正落点**：本篇 §7.3；以及"本子系统必须跑两次"这条操作规程。
- **教训**：**工具"没找到"时，先怀疑参数语义，再怀疑工具**。

### §4.5 `num_users` 到底读哪个字段（跨篇冲突）

- **冲突现场**：02 篇写 `num_users` 反映"有几个消费者 get 了这一路"，
  08 §7 写它读的是 `use_count`。两篇互相矛盾。
- **推翻方式**：打开 `drivers/regulator/core.c:857-862`。
- **真相**：`num_users_show` 的返回语句在 `drivers/regulator/core.c:861`，
  读的是 **`rdev->use_count`**。"有几个消费者 get 了"是 `open_count`，
  而它**只在 debugfs 里露出**（`drivers/regulator/core.c:5283-5284`）。
- **修正落点**：02 篇改为与 08 §7 一致；由此还发现了 §6.2 那条可观测性缺口。
- **教训**：**跨篇一致性检查（skill 第 17 条）会顺带挖出新问题**，
  它不只是"统一措辞"的工序。

---
## §5 明确未验证项

### §5.1 未编译、未在硬件上运行

**这是本套文档最重要的一条声明**，`05-usage.md` 和 `07-debug.md` 都指向这里：

1. **05 篇的所有代码骨架没有编译过。**
   它们是按 `include/linux/regulator/*.h` 的真实签名手写的，
   每个符号都跑过全树 grep 确认在 v6.1.158 存在（skill 第 20 条），
   但**没有进过编译器**。直接抄进项目里，预期会遇到头文件缺失、
   变量未使用之类的常规问题；**不预期会遇到"这个函数不存在"**。

2. **07 篇的示例输出不是从真机抄的。**
   `regulator_summary` 的表格、tracepoint 的一行行输出、sysfs 属性的值 ——
   全部是**按源码里的 `seq_printf` 格式串和 `TP_printk` 格式串推导出来的**。
   字段顺序、列宽、分隔符与真机一致（因为它们由格式串决定），
   但**具体数值是我编的**。

   判断方法留给读者：格式对不上 = 我推错了格式串；
   数值对不上 = 正常，那是你的板子的值。

3. **06 篇的 DTS 片段取自本树 in-tree 文件**（`rk3399-rock-pi-4.dtsi` 等），
   可以直接 diff；但**"改错了会怎样"那部分的症状是推导的**，没有真机复现。

### §5.2 只读了符号表的部分

见 §2.2 和 §3.2。一句话：**174 个芯片驱动的实现没读。**
凡是文档里说"PMIC 驱动一般会……"的地方，
可信度是"按契约推断"，不是"普查得出"。

### §5.3 推断而非观察的结论

以下几条是**逻辑推出来的**，没有直接的代码行支撑整条链，逐条列出：

| 推断 | 在哪 | 推理依据 | 没验证的是什么 |
|---|---|---|---|
| arm64 上 `have_full_constraints()` **恒真** | 08 §1、00 §5 | `drivers/regulator/core.c:6291-6292` 在 `of_have_populated_dt()` 时置真 + arm64 必然 DT 启动 | 没有构造"arm64 + 无 DT"的反例来证伪；理论上 EFI stub 之外没有这种配置 |
| 30 秒清理会造成"快机器正常、慢机器挂"的**不可靠复现** | 08 §8 | 定时器是绝对时间（`msecs_to_jiffies(30000)`），probe 完成时刻不是 | 没有实测过任何一块板子上的实际竞态 |
| ww_mutex 慢路径"几乎不走" | 08 §9 | 需要两个 CPU 同时操作有交集的依赖闭包 | **完全没有性能数据**。这是定性判断，不是测量 |
| `regulator_summary` 定时轮询会周期性卡住所有电源操作 | 07 §6.2、08 §9 | 打印路径锁整棵树（`drivers/regulator/core.c:6119` → `:6099`） | 没有实测卡顿时长 |
| `of_match` 写错是**完全静默**的 | 08 §6、06 §8.2 | 无 init_data 时走 `kzalloc`（`drivers/regulator/core.c:5635-5641`），约束全 0 而非"未定义" | 没有真机改错一个字母验证过日志确实一条都不打 |

### §5.4 版本时效性

- 所有行号绑死 **v6.1.158**。6.1.y 是 LTS，regulator 核心在 stable 里改动很少，
  但**任何 stable 更新都可能让行号漂移**。
- 行号漂移后的复核方法：文档里凡是贴了代码块的地方，
  **用代码块里的字符串去 grep**，比按行号找可靠。
  这也是本套坚持"贴源码而不写大意"（skill 第 19 条）的原因之一。
- 结构体字段和 API 签名的稳定性远高于行号。**语义结论基本不会因 stable 更新失效。**

---

## §6 自陈问题（读代码时发现的、上游还在的问题）

这一节是 01/02/03/04/07/08 六篇共同指向的落点。
**都是"我认为值得记一笔"的观察，不是 bug 报告** —— 有几条明确是历史包袱。

### §6.1 `num_users` 属性名与语义不一致

`/sys/class/regulator/regulator.N/num_users` 读的是 `rdev->use_count`
（`drivers/regulator/core.c:861`），即"当前有几个消费者**要求它开着**"，
而不是"有几个消费者用它"。
后者是 `open_count`。
改名会破坏用户态 ABI，所以这个不一致会一直存在。（08 §7）

### §6.2 `open_count` 没有 sysfs 出口

`rdev->open_count` **只在 debugfs 里露出**
（`drivers/regulator/core.c:5283-5284`）。
后果：`CONFIG_DEBUG_FS=n` 的产品内核上，
**"这一路电有没有人认领"是查不到的** ——
`num_users` 读到 0 既可能是没人 get，也可能是 get 了但没 enable。

这是本子系统一个真实的**可观测性缺口**。（08 §7、07 §3.2）

### §6.3 `-ENOTSUPP` / `-EOPNOTSUPP` 混用

本子系统只有一处用 `-ENOTSUPP`：`drivers/regulator/core.c:5487`。
全树两者的用量是 **524 : 95**（`-ENOTSUPP` 更多），
但 `-ENOTSUPP`（524）**不是一个 POSIX errno**，不该出现在返回给用户态的路径上。
这一处在核心内部，暂无实际危害。（07 §7.3）

### §6.4 `-EDEADLK` / `-EDEADLOCK` 两种拼法混用

同一含义、同一值，在同一个文件里写了两种拼法：
`-EDEADLK`（`drivers/regulator/core.c:164`、`:338`、`:420`）与
`-EDEADLOCK`（`:231`、`:249`）。
行为无差别，但**只 grep 一种会漏掉另一种**。（08 §9、§4.3）

### §6.5 `fixed-helper.c` 是死代码

`drivers/regulator/fixed-helper.c` 全文 61 行，
导出的 `regulator_register_always_on()` 在**全树零调用**
（`rg -n 'regulator_register_always_on'` 只命中定义和头文件声明）。
它是给非-DT 平台准备的便捷入口，随着 DT 普及失去了使用者。（04 §7）

### §6.6 两处不可达分支

| 位置 | 分支 | 为什么不可达 |
|---|---|---|
| `drivers/regulator/core.c:822` 与 `:834` | sysfs 属性里打印 `constraint not defined` | 注册路径保证 `constraints` 非 NULL（无 init_data 时 `kzalloc` 出一份空壳，`:5635-5641`），所以 `constraints == NULL` 到不了 sysfs |
| `drivers/regulator/core.c:1827-1828` | `if (!c) return 0;` | 同一个理由 |

这类分支不是错误 —— 它们是**防御性代码**。
记在这里的意义是：**读者不必花时间琢磨"什么情况下会打这条"**。（07 §2.3）

### §6.7 虚拟消费者的电流路径不对称

`drivers/regulator/virtual.c` 里：
`set_current_limit` 的写入路径存在（`:87`、`:97`），
但读回路径（`:73`）拿的是**驱动自己缓存的值**，不是回读硬件。
所以用 virtual consumer 调限流时，**读出来的数不能当作硬件实际状态**。（07 §4）

### §6.8 `Restricting voltage` 这条日志的措辞是错的

`drivers/regulator/core.c:550` 打印：

```
Restricting voltage, %u-%uuV
```

紧接着 `return -EINVAL`（`drivers/regulator/core.c:552`）。
「Restricting」听起来像"已经帮你收窄好了"，
实际含义是"**消费者区间的交集为空，什么都没做，报错返回**"。
这条日志误导性很强，是"调压失败但看日志像成功"的经典来源。（07 §2.2、08 §5）

### §6.9 30 秒这个数字没有技术依据

`drivers/regulator/core.c:6305-6306` 的 `msecs_to_jiffies(30000)`，
上方 `drivers/regulator/core.c:6294-6303` 的注释自己承认：
"我们随便挑了一个时长……理想情况该由用户态通知我们，
但没有这个机制，所以就等一会儿、希望等够了"。

这是上游明确标记的设计债，不是我的推测。（08 §8、03 §C2）

### §6.10 `regulator_desc` 没有"这是 LDO 还是 buck"的字段

`desc->type` 只有 `REGULATOR_VOLTAGE` / `REGULATOR_CURRENT` 两个值。
**拓扑类型（LDO / buck / boost / switch）在数据结构里完全不存在。**
后果：核心无法据此做任何决策（比如"buck 效率高、优先用"），
也无法在 `regulator_summary` 里显示。
这不是遗漏 —— 核心确实不需要知道。但它会让第一次读的人反复找那个字段。（00 §2.2、00 §2.9）

---

## §7 工具与局限

### §7.1 索引工具（cbm）只用于导航

按 skill 前置约束：**cbm 不用于取证**。
本子系统上它的两个盲区都撞到了：

| 盲区 | 具体例子 |
|---|---|
| **宏生成的符号** | `rk808-regulator.c` 的 `RK817_BOOST_DESC` 宏（`:69-80`）批量生成 desc，索引里查不到生成后的字段赋值 |
| **函数指针间接调用** | `rdev->desc->ops->enable()` 这一整类调用 —— 而这恰好是本子系统的**主要控制流**。核心到驱动的每一次下钻都是间接调用 |

⇒ 03 篇的调用链**全部由 `rg` + `Read` 手工建立**，没有一条来自索引查询。

### §7.2 shell 里的 `grep` 不是 GNU grep

本机 `grep` 是 **ugrep 7.5.0**。两个坑：

1. **不支持 `\s`** —— 必须写 POSIX 的 `[[:space:]]`。
2. **默认遵守 `.gitignore`** —— 在内核树里可能静默漏文件。

⇒ 本套所有精确计数都显式调 **`/usr/bin/grep`**（GNU 3.7）或 `rg`。
如果你按本篇的命令重跑而数字对不上，先确认你的 `grep` 是哪一个。

### §7.3 三个自检脚本的局限

**（a）`check_xrefs.py`** —— 查跨篇 `0X §Y` 引用是否命中。
它的正则**认不出下面这两种写法**：

| 写不出来的形式 | 后果 |
|---|---|
| 「07 **篇** §6.2」（数字和 `§` 之间有汉字） | **不被检查**，必须手工回访 |
| 同篇内部引用「（§A3.5）」 | 不被检查 |

⇒ 本套里 `NN 篇 §X` 这种写法用得很多（因为读起来更顺），
**这部分交叉引用的正确性靠手工核对，不是脚本保证的**。这是本套最大的一处工具缺口。

**（b）`check_line_refs.py`** —— 查 `路径:行号` 是否越界。
两个已知限制：

1. **文件名里带逗号会被切开**。例如 arm 的 pl330 绑定文件，
   基名含 `,`，脚本会切成后半段去找，永远 UNRESOLVED。
   ⇒ **处置：这类文件名一律不带行号引用**（06 篇就是这么写的）。
2. `--ignore` **只对"行号越界"生效**，对 UNRESOLVED 无效 ——
   所以第 1 条不能靠登记假阳性绕过。

**（c）`check_doc_coverage.sh`** —— 官方文档反向覆盖率。
**参数是相对 `Documentation/` 的路径**，不是子系统昵称（§4.4）。
本子系统的概念文档在 `Documentation/power/regulator/`，
而 API 文档在 `Documentation/driver-api/`，
⇒ **必须跑两次**：

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
bash $S/check_doc_coverage.sh regulator       output/regulator .
bash $S/check_doc_coverage.sh power/regulator output/regulator .
```

**它的"引用次数"不可信**：那是**基名子串匹配**。
本子系统的实测输出是最好的反例：

| 文件 | 报告的引用次数 | 真实情况 |
|---|---|---|
| `regulator.rst` | **1890 次** | 假阳性 —— "regulator" 这个词在文档里到处都是 |
| `consumer.rst` | **128 次** | 假阳性 —— 同理 |
| `design.rst` | **0 次** | ✅ 真实（已处置，§2.3） |

⇒ **只有"0 引用"这个信号可信**，非零计数一律当作噪声。

### §7.4 三个脚本都抓不到的那一类

**行号在范围内、但指向无关代码。**
唯一办法是逐个 `sed` 打开核对（skill 第 19 条）。
本套 454 条引用是**逐条打开核对过的**，
§4.3 那条 `-EDEADLOCK` 就是这么抓出来的 —— 脚本报的是"干净"。

---

## §8 逐篇取证表

每篇一行"主要论断 → 出处"，加一行"自审改了什么"。
**"自审修正"那一列是本表的重点** —— 它标出每篇最容易出错的位置。

### §8.1 `00-overview.md`（1003 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 供电树靠 `rdev->supply` 串起来，关一路会递归关上游 | `drivers/regulator/core.c:3061-3062` | 一等 |
| 硬件接口三类：寄存器 / GPIO / 完全不可控 | `include/linux/regulator/driver.h:396-431` + `drivers/regulator/fixed.c` | 一等 |
| 没有"LDO 还是 buck"的字段 | `include/linux/regulator/driver.h:361` 起全字段清单 | 一等（否定性论断，逐字段核对过） |
| `ramp_delay` 的单位是 **uV/us**，不是 us | `include/linux/regulator/driver.h:292` 的 kernel-doc | 一等 |
| 缺 `enable_time` 会造成机器速度相关的偶发问题 | `include/linux/regulator/driver.h:427` + 推理 | **推断**（§5.3） |
| 「能不能」有三层门，且两种失败都返回 0 | `core.c:117-127` / `:3027` / `:3030` | 一等 |
| regulator 与 clk 的三处关键差异 | 代码对照 + `Documentation/driver-api/regulator.rst:60` | 一等 + 三等 |

**自审修正**：§2.2 最初写了"`desc->type` 区分 LDO 和 DCDC"——
逐字段核对 `driver.h` 后发现 `type` 只有 `VOLTAGE`/`CURRENT` 两个值，
改为显式的否定性论断并进 §6.10。

### §8.2 `01-api.md`（501 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 消费者 API 全在 `include/linux/regulator/consumer.h` | 文件全篇 | 一等 |
| `_optional` 变体在找不到时返回 `-ENODEV` 而非给 dummy | `drivers/regulator/core.c:2225-2231` 的分支条件 | 一等 |
| `devm_*` 变体的释放时机 | `drivers/regulator/devres.c` 全篇 | 一等 |
| 错误码语义表 | 逐个错误码 `sed` 打开核对 | 一等（见 §4.1） |

**自审修正**：表格统一成 `API / 说明 / 备注` 三列，签名去掉类型；
逐 API 行号删除，改成每节开头声明头文件（skill 第 9、10 条）。

### §8.3 `02-datastructures.md`（660 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| `struct regulator` 是**凭证**，`regulator_dev` 是**电源** | `drivers/regulator/internal.h:45-59` vs `include/linux/regulator/driver.h:611` | 一等 |
| 三个计数各自的含义 | `internal.h:52` + rdev 的 `use_count`/`open_count` | 一等 |
| `num_users` 读的是 `use_count` | `drivers/regulator/core.c:861` | 一等 |
| regulator 没有 `bus_type`，只有 `class` | `drivers/regulator/internal.h:61` | 一等 |

**自审修正**：原先说 `num_users` 是"get 了几次"，与 08 §7 冲突；
打开 `core.c:857-862` 后改为 `use_count`（§4.5）。
另修正 debugfs 指针，统一指向 07 §6。

### §8.4 `03-internals.md`（1278 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 注册主流程（含拆除路径） | `drivers/regulator/core.c` 注册/注销两段 | 一等 |
| `enable` 四道门的次序 | `core.c` 逐段，按"谁调用谁"验证过 | 一等 |
| 「只记账不写寄存器」的调压路径 | `regulator_check_consumers` + 权限判断 | 一等 |
| ww_mutex 三个入口与回滚重试 | `core.c:157` / `:218` / `:393` / `:402-422` | 一等 |
| 核心五个文件里只有一条 `FIXME` | `rg 'FIXME|REVISIT|TODO' drivers/regulator/{core,of_regulator,helpers,devres,irq_helpers}.c` | 一等（**已窄化**：驱动层另有 4 条，列在同节表格里） |

**自审修正**：Part A/B/C 三轴前缀是重排后加的（skill 第 13、21 条）；
§C2 的设计债断言从"整个子系统只有一条 FIXME"窄化为"核心五个文件"，
并补上驱动层的 4 条。

### §8.5 `04-extensions.md`（576 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 四个子框架互不调用 | `helpers.c` / `coupler` / `irq_helpers.c` / `devres.c` 交叉 grep | 一等 |
| helpers 全部依赖 `rdev->regmap` | `drivers/regulator/helpers.c` 全篇 | 一等 |
| coupler 只在有 `regulator-coupled-with` 时才起作用 | `of_regulator.c` 的解析 + `core.c` 的耦合路径 | 一等 |
| `regulator_register_always_on` 全树零调用 | `rg -n 'regulator_register_always_on'` | 一等（全称断言，全树 grep） |
| 三种 fixed-regulator compatible | `drivers/regulator/fixed.c` 的 `of_match_table` | 一等（**已窄化为"三种"**） |

**自审修正**：§6.2 原写"两种 compatible"，打开 `of_match_table` 数了是三种。

### §8.6 `05-usage.md`（603 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 消费者骨架（get → enable → 用 → disable → put） | `consumer.h` 签名 + `Documentation/power/regulator/consumer.rst` | 一等 + 三等 |
| PMIC 驱动骨架（desc + ops + 注册） | `driver.h` + `rk808-regulator.c` | 一等 |
| 回调里不能调消费者 API、不能长睡眠 | `rdev->mutex` 在 ops 期间持有（`core.c` 锁路径） | 一等 |
| 每个符号都在 v6.1.158 存在 | 逐个全树 grep（skill 第 20 条） | 一等 |

**自审修正**：篇首补了「骨架未编译」声明（原先只在 99 里）；
回访了所有 "见 0X §Y" 交叉引用。

### §8.7 `06-devicetree.md`（955 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| `REGULATOR_CHANGE_STATUS` 在**没写** `regulator-always-on` 时才给 | `drivers/regulator/of_regulator.c:130-131`（取反判断） | 一等 |
| `CHANGE_VOLTAGE` 要求 min ≠ max | `of_regulator.c` 的对应分支 | 一等 |
| 每条属性的消费代码定位到行 | 逐条 `rg -n '"属性名"' drivers/regulator/` | 一等 |
| 12 条文档化属性 grep 为零却真被消费 | **`snprintf` 拼接属性名**，见下 | 一等（机制已定位） |

**本篇最有方法论价值的一条发现**：
`of_regulator.c` 里有一批属性名是**用 `snprintf` 现场拼出来的**
（形如 `"regulator-%s-microvolt"` 配合状态名），
所以 `rg '"regulator-suspend-mem-microvolt"'` **命中零次**，
但这条属性是真的被消费的。

⇒ **"grep 不到就是死字"这条判据有一个系统性例外：拼接的属性名。**
本篇 §6.1 单列了这 12 条。**这是本套里"工具会骗你"的最好例子。**

**自审修正**：原先把这 12 条全部标成"死字"，
定位到 `snprintf` 拼接后全部改判；DT 绑定引用中含逗号的文件名去掉了行号（§7.3）。

### §8.8 `07-debug.md`（767 行）

| 论断 | 出处 | 等级 |
|---|---|---|
| 日志可见性分级（`_once` / `_ratelimited` / 普通） | 逐条 `dev_*` 调用核对 | 一等（**已窄化**：三处 `WARN_ON_ONCE` 单列，`core.c:205`、`:344`、`:6076`） |
| `-EPERM` 的四个语义组 | §4.1 | 一等 |
| `-EALREADY` **不是返回码** | `drivers/regulator/core.c:344` | 一等（§4.2） |
| 11 个 tracepoint 的字段清单 | `include/trace/events/regulator.h` 全篇 | 一等 |
| `regulator_summary` 会锁整棵树，不能轮询 | `drivers/regulator/core.c:6119` → `:6099` | 一等 + 推断（卡顿时长未测，§5.3） |
| 示例输出的格式 | 按 `seq_printf` / `TP_printk` 格式串推导 | **推导，数值是编的**（§5.1） |

**自审修正**：§2.0 的日志可见性从"始终输出"窄化到变体级；
§7.1 的 `-EPERM` 从行号罗列改成四个语义组；
`-EALREADY` 从"返回码"改判为"断言的一部分"。

### §8.9 `08-feynman.md`（736 行）

| 主题 | 关键行号 | 等级 |
|---|---|---|
| §1 dummy | `core.c:2225-2231`、`dummy.c:30` | 一等 + 三等（`design.rst`） |
| §2 凭证 vs 电源 | `internal.h:45-59` | 一等 |
| §3 disable 可能不断电 | `core.c:3017-3065` 逐行 | 一等 + 三等（`driver-api/regulator.rst:83-88`） |
| §4 `always-on` 没收开关权 | `of_regulator.c:130-131` | 一等 |
| §5 两个电压参数 | `core.c:527-552` | 一等 + 三等（`Documentation/driver-api/regulator.rst:97-102`） |
| §6 三个名字 | `rk3399-rock-pi-4.dtsi:265-268` + `core.c:101-109` | 一等 |
| §7 `num_users` | `core.c:857-862` | 一等 |
| §8 30 秒清理 | `core.c:6215-6310` | 一等 |
| §9 ww_mutex | `core.c:157`/`:218`/`:393` | 一等 |

**自审修正**：§9 原把两种死锁错误码拼法混为一种，逐行核对后拆开（§4.3）。

---

## §9 对照与遗留

### §9.1 无对照套

本子系统**没有 qoder 对照套** —— `output/qoder/` 下没有 regulator 目录。
所以本套没有做交叉套对比，所有自审都是单套内部的。

缺少对照套的实际影响：**"我漏了什么"这类问题只能靠模板清单兜底**，
没有第二个视角。i2c / spi 两套的经验是：对照套主要暴露**选题遗漏**
（不是事实错误）。所以本套的风险集中在"哪些机制我根本没想到要写"，
而不是"我写错了"。

### §9.2 脚本自检结果（收尾时重跑）

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/regulator
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh regulator       $D .
bash    $S/check_doc_coverage.sh power/regulator $D .
```

**最后一轮的实测结果**（本套 10 篇、7802 行）：

| 脚本 | 结果 |
|---|---|
| `check_xrefs.py` | 跨篇引用命中 **79 条，失效 0 条**，忽略项 0 条 |
| `check_line_refs.py` | 扫描 **478 条**唯一 `file:line` 引用（消歧表 28 个基名 / 17 个前缀目录），**无解析失败、无行号越界**，忽略项 0 条 |
| `check_doc_coverage.sh regulator` | 必查 1 篇，**0 篇 0 引用** |
| `check_doc_coverage.sh power/regulator` | 必查 5 篇，**0 篇 0 引用** |

两个 `.ignore` 文件都是空的（不存在）—— 本套没有登记任何假阳性，
所有脚本告警都是靠改文档消掉的，不是靠豁免。

⚠️ **每轮改动后必须重跑并更新上表**，
否则本篇从"取证"降级成"回忆"（skill 第 25 条）。

### §9.3 遗留未决

| 项 | 状态 |
|---|---|
| 卡顿时长、竞态窗口等定量数据 | 需要真机，本套无 |
| 174 个芯片驱动的实现差异 | 主动裁掉（§2.2、§2.7） |
| `NN 篇 §X` 形式的交叉引用 | 脚本查不到（§7.3a），靠手工核对 |
