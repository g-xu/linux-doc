---
title: 取证附录
---

# 99 取证与方法：这套文档是怎么写出来的，哪些地方不要信

> **这一篇的定位：可信度声明。**
> 前面九篇讲 mailbox，这一篇讲**前面九篇本身**：
> 每条论断的出处、哪些数字能一条命令重跑、哪些结论是推断而不是观察、
> 自审过程中被推翻的假设、以及**明确没验证的部分**。
>
> 如果你要拿这套文档去做设计决策，**先读 §5**。

---

## §1 基线与子系统判定

### 1.1 树的基线

```bash
$ git log -1 --format='%H %s'
f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158
```

⇒ 本文档集针对 **Linux 6.1.158**。

### 1.2 关键：这棵树打了补丁，但 mailbox 没被碰

`git status` 显示本树有**上千个文件被修改**（`arch/arm64/`、`drivers/net/`、
`drivers/gpu/drm/i915/` 等），说明这是一棵打了外部补丁集的树。
**所以必须先确认目标子系统有没有被改**：

```bash
$ git status --porcelain -- drivers/mailbox include/linux/mailbox\* | wc -l
0
```

**空输出 ⇒ mailbox 子系统一行都没被改动。**
⇒ 本文档集所有结论**可以按上游 6.1.158 理解**，
不需要考虑任何本地补丁的影响。

这一步是整份文档的定性前提。如果这里非空，
所有"上游是这样"的说法都要打折。

### 1.3 子系统类型判定：框架型

```bash
$ /usr/bin/grep -rn 'bus_type' drivers/mailbox/ \
      include/linux/mailbox_client.h include/linux/mailbox_controller.h | wc -l
0
```

⇒ **无 `bus_type`** ⇒ **框架型子系统**（不是硬件总线型）。

这个判定改变了三处模板适配：

| 篇 | 默认做法（硬件总线型） | mailbox 实际做法 |
|---|---|---|
| 02 | 画"控制器在 platform 总线 / 设备在子系统总线"的双总线图 | 改画"核心抽象 ↔ 消费者"关系图（`02-datastructures.md` §1.1） |
| 03 | 主轴是"匹配 → 传输" | 改为"注册 → 申请 → 提交 → txdone → 注销" |
| 04 | 讲子框架（mux/slave 之类） | mailbox 没有真正的子框架；改讲 `mailbox-test` + `of_xlate` 普查 + 特殊用法 |

### 1.4 官方文档读了什么

| 文件 | 行数 | 读法 | 结论 |
|---|---|---|---|
| `Documentation/driver-api/mailbox.rst` | 129 | 全部详读 | 发现一处与代码冲突，见 §4.1 |
| `Documentation/devicetree/bindings/mailbox/` | 29 个文件 | 按需（通用 `mailbox.txt` + 本文档举例用到的控制器 YAML） | 发现通用属性无 YAML schema，见 §4.4 |

**没有 `Documentation/mailbox/` 这个目录** ——
mailbox 的概念文档只有 `driver-api/` 下那一个 129 行的文件。
这是本子系统官方文档最薄的地方，也是这套文档存在的理由之一。

---

## §2 阅读范围与证据等级

### 2.1 一等证据：逐行详读过的文件

| 文件 | 行数 | 详读范围 |
|---|---|---|
| `drivers/mailbox/mailbox.c` | 626 | **全文** |
| `drivers/mailbox/mailbox.h` | 10 | 全文 |
| `include/linux/mailbox_client.h` | 49 | 全文 |
| `include/linux/mailbox_controller.h` | 140 | 全文 |
| `drivers/mailbox/Kconfig` | 297 | 全文 |
| `drivers/mailbox/mailbox-test.c` | 415 | 全文（07 §4 和 04 §1 全靠它） |
| **合计** | **1537** | |

这 6 个文件里的任何行号引用都是**一等证据**：
我打开看过那一行，并且看过它的上下文。

### 2.2 二等证据：读了特定函数或特定行区间的文件

二等的含义：**只读了下表列出的区间，区间之外没看过。**
如果本文档对这些文件做了"全文性"的断言（比如"这个驱动没有 X"），
那条断言是**三等推断**，不是二等观察。

| 文件 | 详读了哪些 |
|---|---|
| `drivers/mailbox/tegra-hsp.c` | 寄存器定义 `:22-57`；doorbell 查找/中断 `:149-193`；`startup` 含 `:511`；`flush` `:577`；`of_xlate` 注册 `:794` / `:822` |
| `drivers/mailbox/arm_mhu.c` | 全部寄存器定义 `:16-25`；RX/TX 中断与发送 `:44-82` |
| `drivers/mailbox/arm_mhuv2.c` | 头部注释 `:10`；常量 `:39-46`；结构体 `:104-190`；doorbell 系列 `:224-303`；`unregister` 上下文 `:985-1009`；`of_xlate` `:1074` |
| `drivers/mailbox/imx-mailbox.c` | 常量与通道类型 `:22-57`；`con_priv` `:72-93`；发送循环 `:395-402`；`dev_dbg` 点位 `:229`/`:307`/`:381`/`:415`/`:511`/`:530`；三处 `of_xlate` `:730`/`:765`/`:775` |
| `drivers/mailbox/mtk-cmdq-mailbox.c` | `cmdq_mbox_send_data` `:336-360`；`ops` `:491`；`flush` `:494`；通道分配与 txdone flag `:583-605`（含 `:590`/`:593-594`） |
| `drivers/mailbox/bcm2835-mailbox.c` | `send_data` `:80-90`；`startup` 起始 `:92`；`of_xlate` `:165` |
| `drivers/mailbox/bcm-flexrm-mailbox.c` | debugfs 回调 `:1154-1172`；注册段 `:1600-1626` |
| `drivers/mailbox/bcm-pdc-mailbox.c` | debugfs `:499`/`:502`；ratelimited 日志 `:650`/`:654` |
| `drivers/mailbox/omap-mailbox.c` | txdone 改写 `:376`；`of_xlate` `:851` |
| `drivers/mailbox/rockchip-mailbox.c` | 注册失败日志 `:244`；`chans->idx` 缺陷段落（见 §6.4） |
| `drivers/mailbox/hi6220-mailbox.c` | `unregister` 上下文 `:333-338`；`of_xlate` `:313` |
| `drivers/mailbox/mailbox-altera.c` | `unregister` 上下文 `:321-328` |
| `drivers/mailbox/sprd-mailbox.c` | ratelimited 日志 `:114`/`:127`/`:166`；`flush` `:289` |
| `drivers/mailbox/apple-mailbox.c` | `dev_dbg` `:132`/`:160`；`flush` `:308`；`of_xlate` `:361` |
| `drivers/mailbox/sun6i-msgbox.c` | 裸 `mbox_controller_register` `:276` |
| `drivers/firmware/arm_scmi/mailbox.c` | `tx_prepare` 实现 `:35`、注册 `:144`；`mailbox_send_message` `:181-193` |
| `drivers/firmware/arm_scpi.c` | `tx_prepare` 函数 `:421`、注册 `:970` |
| `drivers/firmware/tegra/bpmp-tegra186.c` | 门铃发送 `:86-90` |
| `drivers/rpmsg/qcom_smd.c` | `mbox_client` 内嵌 `:131`；发送 `:374-381`；申请 `:1367-1376`；释放 `:1517-1518`/`:1548` |
| `drivers/soc/mediatek/mtk-cmdq-helper.c` | `knows_txdone` `:84`；`mbox_client_txdone` `:438` |
| `drivers/gpu/drm/mediatek/mtk_drm_crtc.c` | `mbox_client_txdone` `:624`；`knows_txdone` `:1006` |
| `include/linux/mailbox/mtk-cmdq-mailbox.h` | `struct cmdq_pkt` `:73-79` |
| `scripts/dtc/checks.c` | `check_property_phandle_args` `:1400-1458`；宏 `:1474-1477`；`mboxes` 检查项 `:1486`；检查表 `:1952-1953` |
| `scripts/Makefile.lib` | "Disable noisy checks by default" `:339-347` |

### 2.3 三等证据：只跑了 grep / 只读了符号表

以下结论**来自计数命令的输出，没有逐个打开确认**：

| 结论 | 命令 | 出处 |
|---|---|---|
| 核心零 tracepoint | `grep -rn 'trace_\|TRACE_EVENT\|DEFINE_EVENT' drivers/mailbox/ include/linux/mailbox*` → 0 | `07-debug.md` §3.1 |
| 核心零 sysfs / debugfs / class | `grep -cn 'DEVICE_ATTR\|sysfs_\|ATTRIBUTE_GROUPS\|class_create\|debugfs' drivers/mailbox/mailbox.c` → 0 | `07-debug.md` §5.1 |
| 核心日志共 11 处 | `grep -cn 'dev_err\|dev_warn\|dev_info\|dev_dbg\|pr_err\|pr_warn\|pr_info\|pr_debug' drivers/mailbox/mailbox.c` → 11 | `07-debug.md` §2.1 |
| 消费者调用点 65 处、横跨 15 个目录 | `grep -rn 'mbox_request_channel' --include=*.c drivers/ arch/ sound/ net/ kernel/` 去掉核心 | `00-overview.md` §7.1 |
| arm64 DTS 里 `mboxes` 出现 183 次 | `grep -rn 'mboxes' arch/arm64/boot/dts/ \| wc -l` | `00-overview.md` §2.5 |
| 控制器驱动 32 个 .c、共 17584 行 | `ls drivers/mailbox/*.c \| wc -l` / `cat drivers/mailbox/*.c \| wc -l` | `00-overview.md` §1.4 |
| 核心导出 13 个符号 | `grep -n 'EXPORT_SYMBOL' drivers/mailbox/mailbox.c` | `01-api.md` §0 |

三等证据的风险：**grep 会漏宏生成的符号**。
本文档集在 tracepoint 这一条上额外做了一次
`ls include/trace/events/ | grep -i mailbox` → 0，
所以"零 tracepoint"的置信度接近二等；其余几条没做二次确认。

### 2.4 代表驱动怎么选的

32 个控制器 .c 全读不现实（17584 行）。选法：

| 挑选理由 | 驱动 |
|---|---|
| 最简实现（一次 `writel` 就是一条消息） | `bcm2835-mailbox.c` |
| Arm 参考实现，本树 arm64 DTS 实际在用 | `arm_mhu.c`（Juno）· `arm_mhuv2.c` |
| 形态最丰富（五类通道、三个 `of_xlate`） | `imx-mailbox.c` |
| 门铃 + 共享邮箱两种模式共存，且会改写 `txdone_method` | `tegra-hsp.c` |
| 把 mailbox 用成命令队列的极端案例 | `mtk-cmdq-mailbox.c` |
| 唯一自带统计 debugfs 的两个 | `bcm-flexrm-mailbox.c` · `bcm-pdc-mailbox.c` |
| 核心自带的调试客户端 | `mailbox-test.c`（一等证据，全文详读） |

**其余 24 个驱动只做了契约级检查**（有没有实现某个回调、注册用的哪个函数、
`of_xlate` 用不用默认版本）。

### 2.5 全部可复现命令：一条一条重跑

```bash
cd /workspace/safety/linux-6.1.y

# ① 基线
git log -1 --format='%H %s'
git status --porcelain -- drivers/mailbox include/linux/mailbox\* | wc -l      # 期望 0

# ② 核心规模
wc -l drivers/mailbox/mailbox.c drivers/mailbox/mailbox.h \
      include/linux/mailbox_client.h include/linux/mailbox_controller.h \
      drivers/mailbox/Kconfig                          # 626 / 10 / 49 / 140 / 297

# ③ 控制器规模
ls drivers/mailbox/*.c | wc -l                          # 32
cat drivers/mailbox/*.c | wc -l                         # 17584

# ④ 类型判定
/usr/bin/grep -rn 'bus_type' drivers/mailbox/ include/linux/mailbox_client.h \
    include/linux/mailbox_controller.h | wc -l           # 0

# ⑤ 可观测性（全部为 0 / 11）
/usr/bin/grep -rn 'trace_\|TRACE_EVENT\|DEFINE_EVENT' drivers/mailbox/ \
    include/linux/mailbox_client.h include/linux/mailbox_controller.h | wc -l   # 0
ls include/trace/events/ | /usr/bin/grep -i mailbox | wc -l                     # 0
/usr/bin/grep -cn 'DEVICE_ATTR\|sysfs_\|ATTRIBUTE_GROUPS\|class_create\|debugfs' \
    drivers/mailbox/mailbox.c                                                   # 0
/usr/bin/grep -cn 'dev_err\|dev_warn\|dev_info\|dev_dbg\|pr_err\|pr_warn\|pr_info\|pr_debug' \
    drivers/mailbox/mailbox.c                                                   # 11

# ⑥ 导出符号
/usr/bin/grep -c 'EXPORT_SYMBOL' drivers/mailbox/mailbox.c                      # 13

# ⑦ 注册函数普查
/usr/bin/grep -rl 'mbox_controller_register' --include=*.c drivers/ \
  | /usr/bin/grep -v 'drivers/mailbox/mailbox.c' | wc -l                        # 29（含已裁的 pcc.c）
/usr/bin/grep -rln 'devm_mbox_controller_register' --include=*.c drivers/ \
  | /usr/bin/grep -v 'mailbox/mailbox.c' | wc -l                                # 27
/usr/bin/grep -rn 'mbox_controller_register' --include=*.c drivers/ \
  | /usr/bin/grep -v 'devm_mbox_controller_register' \
  | /usr/bin/grep -v 'drivers/mailbox/mailbox.c'                                # 2 行：sun6i-msgbox / pcc

# ⑧ of_xlate 普查
/usr/bin/grep -rn 'of_xlate[[:space:]]*=' --include=*.c drivers/mailbox/        # 19 行（含核心默认 1 行）

# ⑨ 消费者普查
/usr/bin/grep -rn 'mbox_request_channel' --include=*.c drivers/ arch/ sound/ net/ kernel/ \
  | /usr/bin/grep -v '^drivers/mailbox/mailbox.c' | wc -l                       # 65

# ⑩ arm64 DTS
/usr/bin/grep -rn 'mboxes' arch/arm64/boot/dts/ | wc -l                         # 183

# ⑪ 官方文档
wc -l Documentation/driver-api/mailbox.rst                                      # 129
ls Documentation/devicetree/bindings/mailbox/ | wc -l                           # 29
```

**重跑规则**：只要 ①～⑪ 里有**任何一个数字对不上**，
本文档集就从"取证"降级为"回忆"，必须整套复核。

### 2.6 工具版本

| 工具 | 版本 | 注意 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本纯 stdlib |
| GNU grep | 3.7（`/usr/bin/grep`） | 本文档所有 grep 命令都显式写 `/usr/bin/grep` |
| ugrep | 7.5.0 | shell 里裸 `grep` 是它 —— **不支持 `\s`，且默认遵守 .gitignore**。本文档一律用 POSIX `[[:space:]]` |
| ripgrep | 13.0.0 | 只用于导航 |
| git | 2.34.1 | |

**这一条踩过坑**：用裸 `grep` 加 `\s` 时静默返回空结果，
误以为"树里没有这个符号"。所有"零输出"结论都用 `/usr/bin/grep` 重跑过。

### 2.7 明确裁掉的范围

| 裁掉的东西 | 规模 | 理由 | 影响 |
|---|---|---|---|
| **ACPI PCC**（`drivers/mailbox/pcc.c`） | 758 行 | 用户明确要求 arm64 + 不描述 ACPI | 见下 |
| x86 / powerpc / arm32 特有内容 | —— | 用户明确要求 arm64 only | 无（mailbox 核心不含架构代码） |
| PCCT 表、`Documentation` 里的 ACPI 章节 | —— | 同上 | 06 篇完全不提 PCCT |

**裁掉 PCC 造成的三处统计缺口，读者必须知道**：

1. **`txdone_method` 运行期改写点全树共 3 处**，
   arm64 相关的两处是 `drivers/mailbox/tegra-hsp.c:511` 和
   `drivers/mailbox/omap-mailbox.c:376`；**第三处在 `pcc.c:306-307` / `:349-350`**，
   本文档集不展开。
2. **裸 `mbox_controller_register`（非 devm）全树 2 处**：
   `drivers/mailbox/sun6i-msgbox.c:276` 和 `pcc.c:708`。
   ⇒ 如果你只看 arm64，**`sun6i-msgbox.c` 是唯一一个**。
3. **控制器驱动计数 29 里包含 pcc.c** ⇒ arm64 上实际是 **28**。

其余所有"全树 N 处"的断言，如果 PCC 里有第 N+1 处，都在正文里点了名。

---
## §3 逐篇取证表

每一行的格式：**这一篇的核心论断 → 它的出处 → 自审时改过什么。**
「自审修正」一栏是本节最有价值的部分：它告诉你**哪一类论断我曾经写错过**，
你自己读代码时在同样的地方要多看一眼。

### 3.1 `00-overview.md`（819 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| 核心一共 **825 行**（626+10+49+140） | `wc -l`，§2.5 ② | 一等 |
| 硬件形态三分：纯门铃 / 门铃+数据寄存器 / 门铃+FIFO | `tegra-hsp.c:39-42` · `arm_mhu.c:16-25` · `bcm2835-mailbox.c:80-90` · `imx-mailbox.c:35-57` · `tegra-hsp.c:44-51` | 二等 |
| 「② 和 ③ 之间没有任何数据经过 mailbox」 | `arch/arm64/boot/dts/arm/juno-scmi.dtsi:44-64` 的 `shmem` + `mailbox.c` 全文无 `shmem` 字样 | 一等（核心侧）+ 二等（DTS 侧） |
| 四种协议（SCMI/SCPI/BPMP/SMD）传的都是"去看共享内存" | `arm_scmi/mailbox.c:35` · `arm_scpi.c:421` · `bpmp-tegra186.c:86` · `qcom_smd.c:380` | 二等 |
| mailbox 通道是**单向**的 | `juno-scmi.dtsi:44-64` 每个协议节点各自 override `mboxes` | 二等 |
| arm64 DTS 里 `mboxes` 出现 183 次 | §2.5 ⑩ | 三等 |
| `send_data` 必须极快 ⇒ 在 spinlock 内 | `mailbox.c:77` + `mtk-cmdq-mailbox.c:347` 的 `GFP_ATOMIC` | 一等 |
| 「三个没有」：无引用计数、无状态枚举、无计数器 | 通读 `include/linux/mailbox_controller.h` 全 140 行 | 一等 |

**自审修正记录：**

1. 写出了两个**不存在的路径**：`arch/amlogic/…meson-gx.dtsi`（凭空加了前缀）
   和裸文件名 `tegra186.dtsi`。被 `check_line_refs.py` 抓到，改成
   `arch/arm64/boot/dts/amlogic/meson-gx.dtsi` 和
   `arch/arm64/boot/dts/nvidia/tegra186.dtsi`。
   ⇒ **教训：DTS 路径最容易凭记忆写，必须复制粘贴。**
2. 官方文档说六个回调「除 `peek_data` 全部必需」，与代码不符（见 §4.1）。
   00 §4.2 的小标题因此写成「六个回调，只有一个是必需的」。

### 3.2 `01-api.md`（565 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| 核心导出 **13** 个符号 | §2.5 ⑥ | 一等 |
| `mbox_send_message` 成功返回**非负下标**，判据是 `ret < 0` | `mailbox.c:263`/`:287` + kernel-doc `:242-243`/`:252-253` + `arm_scmi/mailbox.c:187-191` | 一等 |
| `-EINVAL` 有五种来源（同码多源） | `mailbox.c:261` · `:293` 一带 · `:474` · `:491` 等 | 一等 |
| `knows_txdone` 不是 `mbox_client_txdone` 的开关 | 全核心只在 `mailbox.c:389` 读一次；准入判据在 `:199` | 一等 |
| devres 版有一处顺序细节（先填 `*ptr` 再 `devres_add`） | `mailbox.c:605-606` | 一等 |
| 申请-释放-再申请有状态机隐患 | `mbox_free_channel` `:445-464` 与 `mbox_request_channel` `:376-390` 对读 | 一等 |

**自审修正记录：** 曾把 `mbox_flush` 的 kernel-doc 位置写成
`include/linux/mailbox_controller.h` 的第 298 行 —— **该文件只有 140 行**，
正确位置是 `:23-25`。被 `check_line_refs.py` 抓到。
（这里故意不把错误引用原样写成 `文件:行号` 形式，否则脚本会再报一次，见 §7.2）
⇒ **教训：越界行号是脚本唯一能 100% 抓住的错误类型，一定要跑。**

### 3.3 `02-datastructures.md`（672 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| 「TX 什么时候算完」由**四个字段**表达，分住三个结构体 | `mbox_controller.txdone_irq`/`txdone_poll`（`mailbox_controller.h`）· `mbox_client.knows_txdone` · `mbox_chan.txdone_method` | 一等 |
| `mbox_client` 由消费者**内嵌**而非 kzalloc | `qcom_smd.c:131` 等全树普查 | 二等 |
| `active_req` 和 `msg_data[]` 是两个不同的东西 | `mailbox.c:40` vs `:79` | 一等 |
| 环形队列**没有读指针**（只有 `msg_free` + `msg_count`） | `mailbox.c:26-51` 与 `:65-72` 的下标算术 | 一等 |
| `poll_hrt` 只在 POLL 分支初始化 | `mailbox.c:507-510` | 一等（但"会不会被误用"是**静态推断**，见 §5.3） |
| `tx_prepare` 全树只有 **3** 个实现者 | `mailbox-test.c:342` · `arm_scpi.c:970`+`:421` · `arm_scmi/mailbox.c:144`+`:35` | 二等 |
| 两把 spinlock，嵌套顺序固定且只有一处 | `mailbox.c:87-89`（`chan->lock` 内取 `poll_hrt_lock`），反向不存在 | 一等 |
| 接收路径完全无锁 | `mailbox.c:155-165` | 一等 |

**自审修正记录：**

1. 三处**自引用章节号写错**：正文写「见本篇 §1.0」但实际章节是 §1.1；
   写「§4.2」但内容在 §8.1。⇒ 重排轮改过章节号之后必须重跑 `check_xrefs.py`。
2. `num_users` 那一段曾说它读 `num_users` 字段，实际 `num_users_show`
   读的是 **`use_count`** —— 这条是 regulator 文档集的教训，本篇写作时直接规避了同类错误。

### 3.4 `03-internals.md`（1313 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| 主轴 = Part A 时间线（T0 注册 → T5 注销）/ Part B 横切 / Part C 评价 | 全篇组织 | —— |
| `tx_tick` 是唯一 `complete()` 的地方，**且超时路径故意不 complete** | `mailbox.c:113-114` `if (r != -ETIME && chan->cl->tx_block)` | 一等 |
| `msg_submit` 的 39 行是核心里最关键的一段 | `mailbox.c:53-91` 逐行 | 一等 |
| 同一通道**严格串行**：`active_req` 非空就不发下一条 | `mailbox.c:62-63` `goto exit` | 一等 |
| 默认 `of_xlate` 只查上界不查下界 | `mailbox.c:471-473` | 一等 |
| **16** 个驱动自己实现 `of_xlate`，13 个用默认（arm64 上 12 个） | §2.5 ⑧ + 逐文件确认 | 二等 |
| 三条 TXDone 路径的**防护强度不一致** | `:176-185` vs `:117-146` vs `:197-205` | 一等 |
| 控制器注销时抢走通道、消费者收不到通知 | `mailbox.c:537-555` | 一等 |
| `mbox_flush` 的入口校验与 `mbox_send_message` 不一致 | `:291-318` vs `:256-288` | 一等 |
| 唯一一条 `REVISIT`（队列长度不可配） | `include/linux/mailbox_controller.h:100-101` | 一等 |
| 全核心**没有** `FIXME` / `TODO` | `/usr/bin/grep -n 'FIXME\|TODO' drivers/mailbox/mailbox.c include/linux/mailbox*.h` → 零 | 一等 |

**自审修正记录：**

1. §A4.2 的标题本轮才改：原写「为什么有 **11** 个驱动要自己写」，
   全树普查是 **16** 个（详见 §4.3）。同篇 §C4 第 9 条的 17/16/8 三个数字是对的
   —— ⇒ **同一个数字在同一篇里出现两次，改一处就得 grep 全篇**。
2. `tx_tick` 的超时分支曾被写成「超时也会 complete」，
   逐字重读 `:113-114` 后推翻（`r != -ETIME` 是个**与**条件）。
3. 时间线次序曾把 `of_xlate` 排在 `try_module_get` 之前，
   按"谁调用谁"重排后确认是 `:363` 遍历 → `:369` `of_xlate` → `:376` `try_module_get`。

### 3.5 `04-extensions.md`（411 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| mailbox **没有真正的子框架**；本篇讲的是五"层"而不是五个子框架 | 篇首声明 + §6 汇总表 | —— |
| `mailbox-test` 注册两个 debugfs 文件，`message` 和 `signal` 分工不同 | `mailbox-test.c` 全文（一等） | 一等 |
| OMAP 的三个额外导出符号**全树零调用者** | `/usr/bin/grep -rn 'omap_mbox_' --include=*.c drivers/ arch/` 去掉 omap-mailbox.c 自身 | 三等（计数命令） |
| 载荷契约层 = `include/linux/mailbox/` 四个头文件 | `ls include/linux/mailbox/` | 一等 |
| MediaTek CMDQ 把 mailbox 用成命令队列 | `mtk-cmdq-mailbox.c:336-360` + `include/linux/mailbox/mtk-cmdq-mailbox.h:73-79` | 二等 |

**自审修正记录：** 「OMAP 三个符号零调用者」这条全称断言，
第一次只在 `drivers/mailbox/` 里 grep（当然零），
按第 19 条规则改成**全树** grep 后结论不变，但**证据强度完全不同**。
⇒ **框架的使用者不一定住在框架自己的目录下。**

### 3.6 `05-usage.md`（653 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| **骨架全部未编译** | 篇首声明 | 见 §5.1 |
| 控制器骨架原型取自 `sun6i-msgbox.c`（POLL 模式最全） | 二等详读 | 二等 |
| 消费者骨架原型取自 `mailbox-test.c:330-355`，但修掉了它的两个反面写法 | 一等详读 | 一等 |
| `tx_block == false` 时超时要自己管 | `mailbox.c:271`（只有 `tx_block` 才进等待分支） | 一等 |
| `send_data` 里绝对不能做的四件事 | `mailbox.c:60`/`:77` 的锁区间 | 一等 |
| 自己写 `of_xlate` 必须同时查上下界 | `mailbox.c:471-473` 只查上界 | 一等 |

**自审修正记录：** 按第 20 条，把骨架里出现的每个符号
（`devm_mbox_controller_register`、`mbox_chan_received_data`、`mbox_chan_txdone`、
`ERR_PTR`、`MBOX_TX_QUEUE_LEN`）逐个全树 grep 确认在 6.1.158 存在。
全部命中，无幻觉符号。

### 3.7 `06-devicetree.md`（984 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| 核心真正会读的属性只有 **`mboxes` + `mbox-names` + `#mbox-cells`** 三个 | `mailbox.c:353`（`of_parse_phandle_with_args`）· `:418` 一带 | 一等 |
| `shmem`：mailbox 核心**一个字都不读** | 核心五文件全文无 `shmem` | 一等 |
| 真正读 `shmem` 的是上层五个文件 | 全树 grep `"shmem"` | 三等 |
| `#mbox-cells = <0>` 官方说不许、现实有三处 | `bcm2835-mailbox.c:165` · `apple-mailbox.c:361` · `mtk-adsp-mailbox.c:122` 三个 `of_xlate` + 各自 DTS | 二等 |
| `mbox-names` 的**前缀匹配**是最大的静默失效陷阱 | `mailbox.c:409-437` 逐行 | 一等 |
| hi6220 的 `mbox-names` 是一条**死字**（无人消费） | `hi6220-mailbox.c` 无 `_byname` 调用 + DTS 里写了该属性 | 二等 |
| `mboxes` 的顺序有语义：SCMI 的 TX=0 / RX=1 | `arm_scmi/mailbox.c` + `juno-scmi.dtsi:44-64` | 二等 |
| dtc 的 `mboxes` 检查**默认开启** | `scripts/dtc/checks.c:1486` + `scripts/Makefile.lib:339-347` 名单里**没有** `mboxes_property` | 一等 |
| `mboxes`/`mbox-names`/`shmem` 三个通用属性**完全没有 YAML schema** | 通用绑定是 `.txt` 不是 `.yaml`，见 §4.4 | 一等 |

**自审修正记录：** 多个 DT binding 文件名**含逗号**
（如 `nvidia,tegra186-hsp.yaml`、`qcom,apcs-kpss-global.yaml`、`ti,omap-mailbox.yaml`），
`check_line_refs.py` 会在逗号处切分基名 ⇒ 本篇对这类文件**一律不带行号引用**。

### 3.8 `07-debug.md`（867 行）

| 核心论断 | 出处 | 证据等级 |
|---|---|---|
| 核心日志 **11 处**，其中 3 处 debug 级 | §2.5 ⑤ + 逐条打开确认等级 | 一等 |
| tracepoint / sysfs / debugfs / 调试 Kconfig **全部为 0** | §2.5 ④⑤ + `ls include/trace/events/` | 一等+三等 |
| 观测缺口清单六项**全部没有接口** | 反查 `mbox_cons`、`chans[]`、`msg_count`、`txdone_method` 有无任何导出路径 | 一等 |
| 三个驱动自建 debugfs | `mailbox-test.c:269` · `bcm-flexrm-mailbox.c:1608` · `bcm-pdc-mailbox.c:499` | 二等 |
| `mailbox.dyndbg=+p` **不可能生效**（`MAILBOX` 是 `bool`） | `drivers/mailbox/Kconfig:2-7` | 一等 |
| `-ENOBUFS`（-105）而不是 `-EAGAIN` | `mailbox.c:36` | 一等 |
| 唯一的调试 config 是 `MAILBOX_TEST` | `drivers/mailbox/Kconfig` 全文 | 一等 |
| `mailbox-test` 自身有 **5** 个缺陷 | `mailbox-test.c` 全文详读 | 一等 |

**自审修正记录：** 日志可见性等级最初写成"始终输出 / 不输出"两档，
按第 22 条细化到 `dev_*_once` / `dev_*_ratelimited` 变体后，
发现 mailbox 核心 **11 处全部是普通变体**（无 `_once`/`_ratelimited`），
但**控制器驱动里有**（`sprd-mailbox.c:114`/`:127`/`:166`、`bcm-pdc-mailbox.c:650`/`:654`）。
⇒ "核心没有"和"这个子系统没有"是两句不同的话，本文档集所有零断言都按这个粒度收窄过。

### 3.9 `08-feynman.md`（675 行）

| 主题 | 类比 | 边界（第四段的落点） |
|---|---|---|
| §1 `mbox_client` vs `mbox_controller` 是 device/driver 吗 | 小区快递柜 | 通道是 DTS 静态绑定、独占、**失败静默** |
| §2 "什么都不传"的消息为什么合法 | 门铃 + 信箱 | 有一类控制器载荷就是数据；格式错了核心零诊断 |
| §3 三种 txdone 说法 | 三种邮筒 | `txdone_method` 是**位掩码**可同时置两位 |
| §4 `send_data` 为什么不能睡 | 收银台 | 约束范围只有 `mailbox.c:60-83` 这一段 |
| §5 返回 5 是什么意思 | 排队取号机 | 这个下标**对调用者毫无用处**；队列硬编码 20 |
| §6 DTS 写错为什么一声不吭 | 填快递单 | cell 值错了可能踢到**另一条真实通道** |
| §7 为什么什么都看不见 | 老式自动门 | Broadcom 平台有统计，其他平台只有 dmesg |
| §8 它其实不是邮箱 | 快递柜的本质 | 队列存**裸指针** ⇒ 栈上构造消息 = 释放后使用 |

选题里的第一个（§1）是模板的强制项：**「这个子系统里最容易被类比错的那组关系」**。

**自审修正记录：** 本篇是错误率最高的一篇（费曼篇不豁免取证，但写作时注意力在类比上）。
逐个 `sed` 核对后改了四处行号：

| 改前 | 改后 | 原因 |
|---|---|---|
| 「从队列取出、赋给 `active_req`」标 `:65-70` | `:72` + `:79` | `:65-70` 是下标算术，不是取出和赋值 |
| `tx_tick` 范围 `:97-115` | `:93-115`（落点 `:113-114`） | 函数起点写晚了 4 行 |
| `msg_submit:63-64` | `:62-63` | 偏移 1 行 |
| bcm2835 的寄存器写 `:87` | `:83`（解引用）+ `:86`（`writel`） | `:87` 是 `dev_dbg`，不是寄存器写 |

⇒ **教训一致：错误全部集中在"我概述了一下"而没有原样贴代码的地方。**

---

## §4 交叉验证实例：五个被推翻或修正的初始假设

这一节是本篇最该读的部分。每一条都是"我原以为 X，查完发现不是"。

### 4.1 官方文档说"六个回调除 `peek_data` 全部必需" —— 代码不支持

`Documentation/driver-api/mailbox.rst:30` 明确要求控制器实现
`struct mbox_chan_ops` 里除 `peek_data` 之外的全部回调。

**逐个查核心到底解引用了哪几个：**

| 回调 | 核心怎么调 | 真的必需吗 |
|---|---|---|
| `send_data` | `mailbox.c:77` **无条件解引用** | **是**，唯一真必需 |
| `startup` | `mailbox.c:381` 一带，`if (chan->mbox->ops->startup)` | 否 |
| `shutdown` | `mbox_free_channel` 内同样带判空 | 否 |
| `last_tx_done` | 只在 POLL 分支（`:129`）被调 | 只有 `txdone_poll` 才必需 |
| `flush` | 全树只有 **4** 个实现者 | 否 |
| `peek_data` | 官方也说可选 | 否 |

**结论**：正确说法是「**只有 `send_data` 无条件必需**，`last_tx_done` 在
`txdone_poll = true` 时必需，其余四个全部可选」。
⇒ **文档与代码冲突，以代码为准**，并在 00 §4.2 和 01 §3.2 显式指出。
反证：`bcm2835-mailbox.c` 就没有 `flush`，工作得很好。

### 4.2 「`mbox_controller_unregister` 同时设两个 txdone flag 会出问题」—— 是活 bug 吗？不是

**初始假设**：`mailbox.c:551` 检查的是控制器的 `txdone_poll` flag 而不是
推导出的 `chan->txdone_method`；如果某个控制器同时设了
`txdone_irq = true` 和 `txdone_poll = true`，注册时按 `:493-498` 走 IRQ 分支
（hrtimer 从未初始化），注销时按 `:551` 走 POLL 分支去 cancel 一个未初始化的 hrtimer。

**验证方法**：把所有同时出现两个 flag 的驱动逐个打开看赋值结构。

| 驱动 | 结构 | 结论 |
|---|---|---|
| `drivers/mailbox/arm_mhuv2.c:985-1009` | `if/else` | 不可能同时为真 |
| `drivers/mailbox/hi6220-mailbox.c:333-338` | `if/else` | 同上 |
| `drivers/mailbox/mailbox-altera.c:321-328` | `if/else` | 同上 |

**结论**：**全树无人同时设两个 flag ⇒ 不是活 bug**。
定性从"bug"降级为**健壮性缺口**（核心没有防住一个它本可以防住的配置错误），
记在 03 §B4 和 08 §3 里，并明确标注"不是活 bug"。

⇒ **教训：「核心没做防护」和「有 bug」是两件事，中间隔着"有没有人真这么写"。**

### 4.3 `of_xlate` 自定义实现的个数：11 → 16

**初始数字 11** 来自一次**不完整的 grep**（只匹配了
`.of_xlate = ` 这种带点号的初始化器形式，漏掉了 `mbox->of_xlate = xxx;`
这种在 probe 里直接赋值的形式）。

**修正方法**：改用 `of_xlate[[:space:]]*=` 同时覆盖两种形式（§2.5 ⑧），
得到 **19 行 / 17 个文件**，去掉核心自己的默认赋值（`mailbox.c:523`）
⇒ **16 个驱动文件、18 个赋值点**（`tegra-hsp.c` 有 2 处、`imx-mailbox.c` 有 3 处）。

⇒ 用默认实现的 = 29 − 16 = **13 个**（去掉 ACPI 的 `pcc.c` 后 arm64 上 **12 个**）。

这个错误在 03 §A4.2 的**标题**里活到了本轮才改掉，而同篇 §C4 的数字是对的。
⇒ **教训：同一个数字在一篇里出现多次，只改一处等于没改。**

### 4.4 「YAML schema 会帮我查 DTS」—— 对 mailbox 完全不成立

**初始假设**：`make dtbs_check` 能校验 `mboxes` 的写法。

**验证**：
```bash
$ ls Documentation/devicetree/bindings/mailbox/mailbox.txt      # 存在，是 .txt
$ ls Documentation/devicetree/bindings/mailbox/*.yaml | wc -l   # 各控制器自己的
```

**结论**：mailbox 的**通用**绑定至今是纯文本 `mailbox.txt`，
⇒ `mboxes` / `mbox-names` / `shmem` 三个通用属性**没有任何 schema**。
唯一能约束的是各控制器自己的 YAML（例如 tegra186-hsp 的
`"#mbox-cells": const: 2`），而它们只管 `#mbox-cells`，管不到消费者侧的 `mboxes`。

⇒ 所以 06 §8.1 的结论是：**唯一的编译期防线是 dtc 内建检查**，
而它只数 cell 个数，不看 cell 的值。

### 4.5 「tegra-hsp 在非 IRQ 通道上调 `mbox_chan_txdone`，是个 bug」—— 被 `:511` 推翻

**初始假设**：`tegra-hsp.c` 的共享邮箱路径调了 `mbox_chan_txdone`，
但驱动的 `mbox_controller` 没设 `txdone_irq` ⇒ 会命中
`mailbox.c:178-182` 那条 `Controller can't run the TX ticker` 并 `return`。

**验证**：打开 `tegra_hsp_mailbox_startup`，发现
`drivers/mailbox/tegra-hsp.c:511` **在 `startup` 里直接把
`chan->txdone_method` 改成了 `TXDONE_BY_IRQ`** —— 而且只对共享邮箱通道改，
门铃通道不改。

**结论**：不是 bug，而是本子系统一个**没有被文档记载的合法用法**：
控制器可以在 `startup` 里按通道类型改写 txdone 模式。
⇒ 这条被提炼成 08 §3 第 2 个"类比失效点"，以及 02 §1.4 的一整节。

**由此得出一条通用的怀疑纪律**：
在 mailbox 里，「控制器没设 flag ⇒ 一定是 ACK 模式」这个推断**必须再查一次
驱动有没有在运行期自己改 `chan->txdone_method`**（全树 3 处，见 §2.7）。

---
## §5 明确未验证项（四层）

**这一节的价值不低于前面九篇** —— 它决定了你该在哪里加倍小心。

### 5.1 第一层：未编译、未运行

| 内容 | 状态 | 风险 |
|---|---|---|
| `05-usage.md` 里**全部**代码骨架 | **从未编译过** | 可能有拼写错误、缺头文件、字段名笔误 |
| `07-debug.md` 里**全部** shell 命令 | **从未在真实硬件上跑过** | 路径可能不同（尤其 debugfs 目录名） |
| `06-devicetree.md` 里自己写的最小节点（§6.3） | 未 `dtc` 编译、未启动 | in-tree 节点是抄的（可靠），自己写的没验证 |
| 任何"打开这个日志你会看到 X"的断言 | **输出内容是从源码的格式字符串推出来的，不是抄的真实 dmesg** | 格式串里的变量顺序可能读错 |

**唯一的例外**：所有 `wc -l` / `grep -c` 类数字都是**真实跑过的命令输出**（§2.5）。

**本文档集没有在任何真实 arm64 硬件上验证过一条命令。**
所有验证都发生在源码层面。如果你在板子上跑出了不同的结果，**信板子**。

### 5.2 第二层：只读了符号表 / 只做了契约级检查

| 范围 | 读到什么程度 |
|---|---|
| 32 个控制器驱动中的 **24 个** | 只查了：实现了哪些 `ops` 回调、用哪个注册函数、`of_xlate` 用不用默认、有没有 debugfs。**没读逻辑** |
| 上层协议框架（SCMI / SCPI / rpmsg / BPMP） | 只读了**它们调 mailbox 的那几行**。协议本身完全没读 |
| 消费者 65 个调用点 | 只做了**计数和分目录归类**，没有逐个打开 |
| `include/linux/mailbox/` 四个头文件 | 只看了结构体定义，没跟踪各自的使用者 |
| DT 绑定 29 个文件 | 只详读了通用 `mailbox.txt` + 本文档举例用到的 4~5 个 YAML |

⇒ 凡是形如「**某个控制器驱动**如何处理 X」的论断，如果那个驱动不在
§2.1/§2.2 的名单里，那是**从它的注册代码反推的**，不是读出来的。

### 5.3 第三层：推断而非观察

以下结论**在本树上找不到实例**，是从代码结构推出来的。它们可能是对的，
但也可能因为某个我没读的驱动而不成立：

| 推断 | 推理依据 | 为什么没法观察 |
|---|---|---|
| `poll_hrt` 只在 POLL 分支初始化 ⇒ 若通道在运行期从 ACK 切到 POLL 会用到未初始化的 hrtimer | `mailbox.c:507-510` 只在 `txdone_poll` 时 `hrtimer_init` | 全树没有任何驱动做"ACK→POLL"的切换（3 处改写都是改成 IRQ 或 ACK） |
| `mbox_controller_unregister` 的两 flag 隐患 | `mailbox.c:551` 看控制器 flag | 全树无人同时设两个 flag（§4.2 已证）⇒ **永远不会触发** |
| `mbox_request_channel_byname` 缺 `cl->dev` 判空 ⇒ 传 `cl->dev == NULL` 会 oops | `mailbox.c:409-437` 直接用 `cl->dev->of_node` | 所有 in-tree 消费者都填了 `dev` |
| 默认 `of_xlate` 不查下界 ⇒ 负数 cell 值可越界访问 | `mailbox.c:471-473` 只有 `>=` 一侧 | 需要人为写畸形 DT（属配置错误，不是攻击面） |
| `mbox-names` 前缀匹配陷阱（写 `"tx"` 会匹配到 `"tx-extra"`） | `mailbox.c:409-437` 的比较方式 | **没有在硬件上复现过**，是读比较逻辑推出来的 |
| 「cell 值写错可能踢到另一条真实通道」 | `of_xlate` 只做范围检查，不做语义检查 | 需要真实硬件 + 故意写错 DTS 才能观察 |
| `kprobe` 抓 `msg_submit` / `tx_tick` 在 inline / LTO 下会失效 | 二者都是 `static` 函数 | **取决于编译器和配置，本文档没有编译验证** ⇒ 07 §3.3 的命令**可能直接报 "no such symbol"** |
| `startup` 失败后 `shutdown` 仍会被调 | `mailbox.c` 错误路径逐行 | 需要构造一个会失败的 `startup` |

### 5.4 第四层：版本时效性

| 时效风险 | 说明 |
|---|---|
| 基线是 **6.1.158**（LTS） | mainline 之后的改动**完全不在范围内**。mailbox 核心在 6.1 之后变化不大，但没有核对 |
| `include/linux/mailbox/` 的头文件数量会随新控制器增加 | 「四个头文件」这个数字是 6.1.158 的 |
| 控制器数量（32）和 DT 绑定数量（29）**每个版本都在涨** | 引用这两个数字时必须带版本号 |
| `MBOX_TX_QUEUE_LEN = 20` 那条 `REVISIT` 从 2013 年活到现在 | 不代表它未来不会变成 Kconfig |
| 官方文档 `Documentation/driver-api/mailbox.rst` 的"全部回调必需"说法 | 如果上游修了这句，§4.1 就成了历史记录 |

---

## §6 已发现的自陈问题（本子系统自己的问题，不是文档的问题）

这些是读代码过程中发现的、**上游代码里真实存在**的缺陷或设计债。
按"会不会咬到你"排序。

### 6.1 会真的咬到人的

| # | 问题 | 位置 | 症状 |
|---|---|---|---|
| 1 | **通道申请失败可以完全没有日志** | `mailbox.c:371-374` 那个出口 | dmesg 干干净净，消费者 probe 失败但你不知道为什么（07 §2.4、§7.1） |
| 2 | **`-EINVAL` 有两处零日志产出点** | `mailbox.c:474` 和 `:491` | 同上；而且 `-EINVAL` 一共有 6 个产出点，撞上哪个无法区分（01 §4.3、07 §6.6） |
| 3 | **`Try increasing MBOX_TX_QUEUE_LEN` 这条建议做不到** | 建议在 `mailbox.c:265`；常量在 `include/linux/mailbox_controller.h:103`，`REVISIT` 在 `:100-101` | 你按提示去找 Kconfig，找不到（08 §5、07 §6.5） |
| 4 | **txdone 模式配错的症状是超时，不是错误** | `mailbox.c:178-182` / `:199` 一带的两条 dev_err + `:282` 的 `-ETIME` | 上层框架报超时，你去查上层，其实错在 DTS/驱动配置（07 §6.4） |
| 5 | **`mbox-names` 前缀匹配** | `mailbox.c:409-437` | 写 `"tx"` 可能拿到名字以 tx 开头的**另一条**通道，全程无日志（06 §4.2） |

### 6.2 文档与代码矛盾（两处）

| # | 问题 | 位置 |
|---|---|---|
| 6 | 官方文档说「除 `peek_data` 全部必需」，实际只有 `send_data` 无条件必需 | `Documentation/driver-api/mailbox.rst:30` vs `mailbox.c:77`（详见 §4.1） |
| 7 | `mbox_send_message` 的 kernel-doc 说它「could be called from atomic context」 | kernel-doc 在 `mailbox.c:249-250`；但 `tx_block` 分支在 `:280` 明确 `wait_for_completion_timeout` ⇒ **会睡**。正确说法：只有 `tx_block = false` 时才能在原子上下文调 |

### 6.3 观测性缺失（整类问题）

| # | 问题 | 依据 |
|---|---|---|
| 8 | **零调试 Kconfig** —— 297 行 `drivers/mailbox/Kconfig` 里没有任何 `*_DEBUG` | §2.5 ⑤ |
| 9 | **`mbox_cons` 全局链表从不被 dump** ⇒ 「系统里注册了哪些控制器」无法回答 | `mailbox.c:23` + 核心零 sysfs/debugfs |
| 10 | **零 tracepoint** ⇒ 无法测延迟、无法统计吞吐 | 07 §3.1；对比 clk 24 / regulator 11 |
| 11 | **`mailbox.dyndbg=` 不可用**（`MAILBOX` 是 `bool` 不是 `tristate`） | `drivers/mailbox/Kconfig:2-7` |

### 6.4 具体驱动里的缺陷

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| 12 | **rockchip 的 `of_xlate` 使得所有消费者都拿到通道 0** | `drivers/mailbox/rockchip-mailbox.c` 的 `chans->idx` 用法 | 静态推断（没在 RK 硬件上验证）；如果成立，多通道 DTS 会静默共享一条通道 |
| 13 | **hi6220 DTS 里的 `mbox-names` 是死字** | `drivers/mailbox/hi6220-mailbox.c` 无 `_byname` 调用者 | 属性写了没人读（06 §4.4） |

### 6.5 `mailbox-test` 自身的五个缺陷

它是唯一的端到端观测面，所以它的缺陷会直接妨碍调试（07 §4.5、04 §1.4）：

| # | 缺陷 |
|---|---|
| 14 | debugfs 目录名是 `dev_name()` 而不是 `mailbox-test` ⇒ 你不知道去哪个目录找（07 §4.1 给了 `ls -d` 的办法） |
| 15 | `message` 和 `signal` 的分工在任何文档里都没写，只能读代码 |
| 16 | 没有独立的 DT 绑定文档 ⇒ 节点怎么写只能抄唯一的 in-tree 示例（04 §1.5） |
| 17 | RX 缓冲只有一格，来第二条消息会覆盖第一条 |
| 18 | 它自己的写法有两处不宜照抄（05 §3.1 已修掉） |

### 6.6 生命周期与防护的不对称（设计债，非 bug）

| # | 问题 | 位置 |
|---|---|---|
| 19 | **申请全局串行（取 `con_mutex`），释放不串行**（只取 `chan->lock`） | `mailbox.c:351-404` vs `:445-464`（03 §B2.1） |
| 20 | **控制器注销时直接抢走通道，消费者收不到任何通知** | `mailbox.c:537-555`（03 §B2.2） |
| 21 | **`mbox_flush` 的入口校验与 `mbox_send_message` 不一致** | 03 §B2.3 |
| 22 | **三条 TXDone 路径的校验强度不一致**（IRQ 路径查、POLL 路径不查、ACK 路径查得不一样） | 03 §B3.1 |
| 23 | **默认 `of_xlate` 是全树 17 个实现里最不设防的一个**（不查 `args_count`、不查下界） | `mailbox.c:471-473`（03 §A4.1） |
| 24 | **`startup` 失败后 `shutdown` 仍会被调** | 03 §B5.1（推断，见 §5.3） |
| 25 | **`-EBUSY` 与 `-ENODEV` 的归因混乱** | 03 §B5.2 |

注意 #19～#25 全部是**核心没有防住一个它本可以防住的情况**，
而不是"现在就会出错"。定性依据见 §4.2 那条纪律。

---
## §7 批量自检脚本的结果

### 7.1 三个脚本的最终输出

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/mailbox
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh driver-api/mailbox $D .
bash    $S/check_doc_coverage.sh mailbox            $D .
```

**① 跨篇引用（`check_xrefs.py`）**

```
文档 10 篇；跨篇引用命中 341 条，失效 0 条（忽略项 0 条）
干净：所有跨篇引用都指向真实存在的章节。
```

341 条 —— 这是本文档集内部交叉引用的规模。**0 条失效**。

**② 行号越界（`check_line_refs.py`）**

```
扫描 464 条唯一 file:line 引用（消歧表 71 个基名 / 51 个前缀目录，忽略项 0 条）
== AMBIGUOUS（1 个简写文件名，树里有多个候选）==
  mailbox.c → 按 drivers/mailbox/mailbox.c 校验；另有 drivers/firmware/arm_scmi/mailbox.c
干净：没有解析不到的文件，也没有越界行号。
```

**464 条唯一的 `文件:行号` 引用，零越界、零解析失败**。
唯一的 AMBIGUOUS 是设计上就无法消除的，见 §7.2。

**两个 `.xref-ignore` / `.lineref-ignore` 都不需要建** ——
本文档集的所有脚本告警都是通过**改文档**解决的，没有一条靠忽略列表压掉。

**③ 官方文档覆盖率（`check_doc_coverage.sh`）**

这个脚本的参数是**相对 `Documentation/` 的路径**，不是子系统昵称，
而 mailbox 的官方文档分散在两个位置，所以要跑**两次**：

| 参数 | 结果 |
|---|---|
| `driver-api/mailbox` | 「没找到独立的官方文档目录」—— **正确**：mailbox 只有单个 `.rst` 文件，没有 `Documentation/mailbox/` 目录（已在 §1.4 登记） |
| `mailbox` | `driver-api/mailbox` **引用 14 次**；DT 绑定 **17 篇中被引用 8 篇**；**必查档 0 篇零引用** |

**两个数字要对上**：脚本说 DT 绑定 17 篇，§2.5 ⑪ 说 29 个文件 ——
差在脚本只统计 `.yaml`（17 个），而目录里还有 **12 个 `.txt`**（含通用的
`mailbox.txt`）。⇒ 29 = 17 + 12，两个数字都对，**计数口径不同**。

「17 篇里只引用了 8 篇」不是缺陷：06 篇的硬要求是
**每条属性都有定位到行的消费者**，不是每篇绑定都被引用。
未被引用的 9 篇 YAML 都是本文档集没有选作代表的控制器。

### 7.2 假阳性与脚本使用陷阱登记

这一小节写给**下一个用这套脚本的人**。五条，全部踩过。

| # | 现象 | 根因 | 正确做法 |
|---|---|---|---|
| 1 | `mailbox.c` 被报 AMBIGUOUS | 树里有两个同名文件：`drivers/mailbox/mailbox.c`（626 行）和 `drivers/firmware/arm_scmi/mailbox.c`（约 200 行） | **无法消除，也不该消除**。本文档集在篇首约定「`mailbox.c` 指 `drivers/mailbox/mailbox.c`」，引用 SCMI 那一个时**一律写全路径**。脚本按最常用的全路径校验，行为正确 |
| 2 | 顶层 `Makefile` 加行号引用会解析到一个无关的 64 行 Makefile | 顶层 `Makefile` 是全树**最重复的基名** | **引用顶层 `Makefile` 时永远不带行号**。本文档集遵守了这一条 |
| 3 | 含逗号的 DT 绑定文件名（`nvidia,tegra186-hsp.yaml` 等）带行号会被误判 | 脚本在逗号处切分基名 | 这类文件**一律不带行号引用**（06 篇全部遵守） |
| 4 | **自污染 A**：在自审记录里把"曾经写错的那个引用"**原样**写成 `文件:行号` 形式 | 脚本不区分"正文引用"和"举例说明的错误引用" | 改成散文形式（「该文件的第 298 行」），或干脆不写行号。本篇 §3.2 就是这么处理的 |
| 5 | **自污染 B**：引用检查脚本自己的路径时带上行号 | 脚本路径在 `~/.claude/` 下，不在被扫描的源码树里 ⇒ 解析失败 | 引用脚本时只写文件名，不带行号 |

还有一条 `check_xrefs.py` 的**新发现假阳性**（本轮才踩到）：

> 表格单元格里写成
> 一个指向 06 篇的文件名（`06-` 开头、`.md` 结尾），
> 紧跟一个括号，里面写「本篇 §A4 只讲核心那一侧」
> 时，脚本会把那个「本篇 §A4」归属到 06 篇上，
> 于是报「06 篇里没有 §A4」—— 而 §A4 其实是 03 篇自己的章节。

**根因**：匹配规则是"`NN-xxx.md` 之后 60 个字符内出现 `§X`"，
它**分不清这个 `§X` 说的是哪一篇**。

**修法**（不是加忽略项，而是改语序）：**把"本篇 §X"写在文件名之前**。
`03-internals.md` 里那一处已按此改成：

> 「本篇 §A4 只讲核心那一侧的解析；DTS 写法和属性消费者见 06 那一篇」
>
> （真实文档里当然写完整文件名；这里为了不再次触发脚本才简写。）

⇒ **通用纪律：一行里不要同时出现"别篇的文件名"和"本篇的章节号"。**

### 7.3 三个脚本都抓不到的那一类

**「行号在范围内，但指向的是无关代码」** —— 脚本无能为力，只能逐个 `sed` 打开核对。
本子系统实测抓到 **5 处**（08 篇 4 处 + 02 篇 1 处，见 §3.9 和 §3.3），
**全部集中在"我概述了一下"而没有原样贴代码的段落**。

⇒ **判据不是"我理解得对不对"，而是"我有没有把源码原样搬过来"。**

---

## §8 数字总表：一页重跑全部统计

下表每一行都对应 §2.5 里的一条命令。**任何一个数字对不上，整套文档降级为"回忆"。**

| 类别 | 数字 | 命令编号 |
|---|---|---|
| 基线 commit | `f6e38ae624cf` / Linux 6.1.158 | ① |
| 子系统被本地补丁改动的文件数 | **0** | ① |
| `drivers/mailbox/mailbox.c` | 626 行 | ② |
| `drivers/mailbox/mailbox.h` | 10 行 | ② |
| `include/linux/mailbox_client.h` | 49 行 | ② |
| `include/linux/mailbox_controller.h` | 140 行 | ② |
| **核心合计** | **825 行** | ② |
| `drivers/mailbox/Kconfig` | 297 行 | ② |
| 控制器 `.c` 文件数 | 32（arm64 上 31，扣掉 `pcc.c`） | ③ |
| 控制器 `.c` 总行数 | 17584 | ③ |
| 一等证据合计（详读） | 1537 行 | §2.1 |
| `bus_type` 出现次数 | **0** ⇒ 框架型 | ④ |
| tracepoint | **0** | ⑤ |
| `include/trace/events/*mailbox*` | **0** | ⑤ |
| 核心 sysfs / debugfs / class | **0** | ⑤ |
| 核心日志点 | 11（其中 3 处 debug 级） | ⑤ |
| 调试 Kconfig | **0** | §2.5 ⑤ 一带 |
| `EXPORT_SYMBOL_GPL` | 13 | ⑥ |
| 调用注册函数的驱动 | 29（arm64 上 28） | ⑦ |
| 用 `devm_` 版的 | 27 | ⑦ |
| 用裸版的 | 2（`sun6i-msgbox.c` + 已裁的 `pcc.c`） | ⑦ |
| `of_xlate` 赋值点 | 19 行 / 17 文件（含核心默认 1） | ⑧ |
| 自定义 `of_xlate` 的驱动 | **16 个文件 / 18 个赋值点** | ⑧ |
| 用默认 `of_xlate` 的驱动 | 13（arm64 上 12） | ⑧ 推算 |
| 消费者调用点 | 65 处 / 15 个目录 | ⑨ |
| arm64 DTS 里 `mboxes` | 183 次 | ⑩ |
| `Documentation/driver-api/mailbox.rst` | 129 行 | ⑪ |
| DT 绑定文件 | 29（17 `.yaml` + 12 `.txt`） | ⑪ |
| `flush` 回调实现者 | 4 | 全树 grep |
| `tx_prepare` 实现者 | 3 | 全树 grep |
| 运行期改写 `txdone_method` 的驱动 | 3（arm64 上 2） | §2.7 |
| 自建 debugfs 的控制器 | 3 | 07 §5.2 |
| **文档集自身**：跨篇引用 | 341 条，0 失效 | §7.1 ① |
| **文档集自身**：`文件:行号` 引用 | 464 条唯一，0 越界 | §7.1 ② |
| **文档集自身**：总行数 | **7790 行**（00 819 · 01 565 · 02 672 · 03 1316 · 04 411 · 05 653 · 06 985 · 07 868 · 08 675 · 99 826） | `wc -l output/mailbox/*.md` |

---

## §9 对照套

**无。** pinctrl / gpio / clk / dma / regulator 有过外部工具生成的对照文档集，
**mailbox 没有** ⇒ 本套文档的所有结论**没有经过第二个独立来源的比对**。

⇒ 这一点提高了 §5.3（推断而非观察）那一类结论的风险：
没有第二双眼睛看过同一段代码。

---

## §10 这一篇怎么用

| 你的处境 | 该看哪一节 |
|---|---|
| 要拿文档里的结论去改代码 / 做设计 | **§5**（四层未验证项）+ **§6**（子系统自陈问题） |
| 想知道某条断言可信到什么程度 | §2.1 / §2.2 / §2.3 三个证据等级，再查 §3 对应那一篇 |
| 数字看起来不对 | §2.5 一条一条重跑，再对 §8 的总表 |
| 想知道我在哪些地方想错过 | §3 每篇的「自审修正记录」+ §4 五个交叉验证实例 |
| 要照抄 05 的骨架 | 先读 §5.1 第一行：**没编译过** |
| 要在硬件上跑 07 的命令 | 先读 §5.1 最后一段：**没在硬件上跑过** |
| 想复用这套方法写别的子系统 | §7.2（脚本陷阱五条 + 那条新发现的假阳性）+ §2.4（代表驱动怎么选） |
