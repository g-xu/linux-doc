---
title: 取证附录
---

# 99 取证：这套文档是怎么写出来的，哪些地方要加倍小心

> 本篇不讲 dmaengine，讲**这套文档本身的可信度**。
> 读者据此判断哪条结论可以直接用、哪条要自己再确认一遍。
> **所有数字都能用本篇给的命令重跑。** 只要有一个数字对不上，
> 整套文档就要从"取证"降级成"回忆"。

---

## §1 基线

### §1.1 树的状态

```bash
cd /workspace/safety/linux-6.1.y
git log -1 --oneline          # f6e38ae624cf Linux 6.1.158
git rev-parse --short HEAD    # f6e38ae624cf
```

这棵树**不是干净的 upstream v6.1.158** —— 工作区里有大量修改
（`git status` 显示 200+ 个文件被改动，主要是 arm/arm64/powerpc/x86 的
抢占相关补丁和一批网卡驱动）。所以第一件事必须是查目标子系统有没有被碰：

```bash
git status --porcelain -- drivers/dma include/linux/dmaengine.h \
        include/linux/of_dma.h Documentation/driver-api/dmaengine/
# 输出为空
```

**结论：dmaengine 子系统在本树里零改动，等于 upstream v6.1.158。**
这条对整份文档的定性很重要 —— 意味着本文档里所有行号都可以直接
和 upstream v6.1.158 对照，不需要考虑本地补丁的干扰。

### §1.2 子系统类型判定（skill 规则 3b）

```bash
rg 'bus_type\s*=' include/linux/dmaengine.h drivers/dma/dmaengine.c   # 无输出
rg 'struct bus_type'  drivers/dma/dmaengine.c                         # 无输出
rg -n 'struct class .*devclass' drivers/dma/dmaengine.c
#   drivers/dma/dmaengine.c:240:static struct class dma_devclass = {
```

**判定：框架型。** 没有 `bus_type`，只有一个 class（`dmaengine.c:240-244`），
class 不做 driver 匹配。所以：

| 模板项 | 本套怎么适配 |
|---|---|
| 02 的"总线 ↔ 父总线"图 | 改画"控制器 ↔ 消费者"关系图 + 明确否掉 device/driver 类比 |
| 03 的主轴 | 不是"传输"，而是"注册 → 申请 → 三步提交 → 终止" |
| 04 有没有子框架 | **有** —— virt-dma / metadata / router / async_tx，独立成篇 |
| 06 有没有 DT 绑定 | **有**，而且核心只读 3 个属性，值得单独一篇 |

### §1.3 官方文档登记（skill 规则 6，阻塞性前置）

在写任何一篇之前读完的官方文档：

| 文件 | 行数 | 是否纳入 | 纳入到哪 / 为什么不纳入 |
|---|---|---|---|
| `Documentation/driver-api/dmaengine/client.rst` | 379 | ✅ | 01 的 API 分组顺序、05 §1 消费者骨架的步骤划分 |
| `Documentation/driver-api/dmaengine/provider.rst` | 647 | ✅ | 03 Part A 的注册契约、05 §2 提供者骨架、04 §1 virt-dma 的定位 |
| `Documentation/driver-api/dmaengine/dmatest.rst` | 232 | ✅ | 07 §5 全节（参数表、命令形式、结果格式），逐条对上行号 |
| `Documentation/driver-api/dmaengine/index.rst` | 55 | ⚠️ 部分 | 只是目录页，只用来确认没漏文件 |
| `Documentation/driver-api/dmaengine/pxa_dma.rst` | 190 | ❌ 裁掉 | PXA 是 arm32 平台，按 arm64-only 规则整篇不读（记在 §2.7） |
| `Documentation/devicetree/bindings/dma/dma-common.yaml` | 53 | ✅ | 06 §1 属性总表、§6 死字属性 |
| `Documentation/devicetree/bindings/dma/dma-controller.yaml` | 37 | ✅ | 06 §2.1 节点命名要求 |
| `Documentation/devicetree/bindings/dma/dma-router.yaml` | 54 | ✅ | 06 §5、04 §3 |
| `Documentation/devicetree/bindings/dma/arm,pl330.yaml` | 92 | ✅ | 06 §2.2、08 §3（引用时不带行号，原因见 §7.2） |
| `Documentation/devicetree/bindings/dma/mediatek,uart-dma.yaml` | 124 | ✅ | 06 §2.3、08 §3（同上，不带行号） |

复现：

```bash
wc -l Documentation/driver-api/dmaengine/*.rst
wc -l Documentation/devicetree/bindings/dma/dma-{common,controller,router}.yaml
```

**官方文档与代码冲突的地方**（一律以代码为准，并在正文显式指出）：

1. `dmatest.rst:19-22` 的 note 说 dmatest 支持 4 种能力 —— 代码
   （`drivers/dma/dmatest.c:1111`–`:1114`）确认了这一点，**但文档没有
   说明"为什么测不了 slave"**。真正的机制原因是
   `dma_channel_table_init` 把 `DMA_SLAVE` 从全局 mask 里抹掉了
   （`drivers/dma/dmaengine.c:275`）。这条因果链是本文档补上的（07 §5.1）。
2. `provider.rst` 讲 `device_synchronize` 时没有强调它是**可选**回调、
   不实现会让 `dmaengine_terminate_sync` 静默退化。这条是本文档
   从代码（`include/linux/dmaengine.h:1159`）推出来并给了活例子的（08 §8）。

---

## §2 方法

### §2.1 详读范围（一等证据的边界）

**逐行读完、可以直接引用行号的文件：**

| 文件 | 行数 | 备注 |
|---|---|---|
| `drivers/dma/dmaengine.c` | 1655 | 框架心脏，全篇逐行 |
| `drivers/dma/dmaengine.h` | 201 | cookie 助手 + debugfs 桩 |
| `drivers/dma/of-dma.c` | 371 | 全部 DT 逻辑 |
| `drivers/dma/virt-dma.c` | 142 | 全读 |
| `drivers/dma/virt-dma.h` | 227 | 全读（大部分是 static inline） |
| `include/linux/dmaengine.h` | 1637 | 全读（结构体 + 枚举 + 大量 static inline 包装） |
| `include/linux/of_dma.h` | 93 | 全读 |
| `drivers/dma/dmatest.c` | 1360 | **只详读了参数区（`:24`–`:88`）、`module_param_cb` 区（`:151`–`:292`）、通道申请区（`:1029`–`:1114`）、`late_initcall`（`:1347`）** —— 其余是二等证据，见 §2.2 |

合计一等证据约 **4326 行**（不含 dmatest 的部分读）。

复现：

```bash
wc -l drivers/dma/dmaengine.c drivers/dma/dmaengine.h drivers/dma/of-dma.c \
      drivers/dma/virt-dma.c drivers/dma/virt-dma.h \
      include/linux/dmaengine.h include/linux/of_dma.h drivers/dma/dmatest.c
```

### §2.2 只读符号表 / 只读片段的部分（二等证据）

**驱动目录太大，不可能全读。** 规模：

```bash
find drivers/dma -name '*.c' | wc -l                    # 147
cat $(find drivers/dma -name '*.c') | wc -l             # 144616
find drivers/dma -mindepth 1 -maxdepth 1 -type d | wc -l # 18
ls drivers/dma/*.c | wc -l                              # 63（顶层）
cat drivers/dma/*.c | wc -l                             # 76530（顶层）
```

注意"147 个 `.c`"是**递归**计数，对应 144616 行；顶层只有 63 个
`.c` / 76530 行。这两组数字不能混用（本文档初稿把
"147 个 `.c`" 和 "80616 行" 配在了一起，是错的，已在 00 §1.3 改正）。

**选了三个代表驱动**（skill 规则 4.1），选法：

| 驱动 | 为什么选它 | 读了哪些行 |
|---|---|---|
| `drivers/dma/pl330.c` | arm64 上最常见的 DMA 控制器；`arch/arm64/boot/dts/` 下 17 个文件用它 | 只读 `of_dma_pl330_xlate`（`:2142-2160`）和 quirk 扫描循环（`:3032`） |
| `drivers/dma/mediatek/mtk-uart-apdma.c` | 形状反常（每通道一段 reg + 一根中断），且**没有实现 `device_synchronize`**，是 08 §8 的活例子 | 读了 `:496`、`:517`、`:525-526`、`:531-549` |
| `drivers/dma/ti/k3-udma.c` | 全树**唯一**实现 `dbg_summary_show` 的驱动 | 只确认了 `:5394` 这一处 |

另有两处点名引用的驱动，都只 grep 确认单点：
`drivers/dma/qcom/bam_dma.c:1349`（枚举当位掩码用，§6 第 ④ 条）、
`drivers/dma/tegra20-apb-dma.c:657`/`:674`/`:939`（全树唯一带
tracepoint 的 DMA 驱动，07 §7）。

**其余 140+ 个驱动只讲契约**，不引用具体行号。凡是正文里出现
"N 个驱动如何如何"的说法，都配了可复现的 grep 命令（见 §2.3）。

### §2.3 可复现的统计命令

**⚠️ 先看 §7.4 的工具版本** —— 本机 `grep` 是 ugrep 包装，
默认会 honor `.gitignore`，统计命令一律显式写 `/usr/bin/grep`（GNU 3.7）。

```bash
cd /workspace/safety/linux-6.1.y

# ---- arm64 DTS 侧 ----
/usr/bin/grep -rl '#dma-cells'    arch/arm64/boot/dts/ | wc -l   # 89
/usr/bin/grep -rl 'arm,pl330'     arch/arm64/boot/dts/ | wc -l   # 17
/usr/bin/grep -rl 'dma-coherent'  arch/arm64/boot/dts/ | wc -l   # 45
/usr/bin/grep -rl 'dma-router'    arch/arm64/boot/dts/ | wc -l   # 0  router 在 arm64 上零使用

# ---- 死字属性：谁在读 ----
/usr/bin/grep -rl '"dma-channels"'     drivers/dma/ | wc -l      # 28
/usr/bin/grep -rl '"dma-requests"'     drivers/dma/ | wc -l      # 13
/usr/bin/grep -rn '"dma-channel-mask"' drivers/dma/              # 5 处（+1 处带厂商前缀）

# ---- 核心一个 DT 属性都不读 ----
/usr/bin/grep -n 'of_property\|of_find_property\|device_property' \
        drivers/dma/dmaengine.c                                  # 空

# ---- tracepoint ----
/usr/bin/grep -rn 'trace_\|TRACE_EVENT' drivers/dma/ \
        --include=*.c --include=*.h                              # 3 行，全在 tegra20-apb-dma.c
ls include/trace/events/ | /usr/bin/grep -i dma                  # 4 个，只有 tegra_apb_dma.h 属于本子系统
wc -l include/trace/events/tegra_apb_dma.h                       # 61

# ---- virt-dma 的普及率 ----
/usr/bin/grep -rl 'virt-dma.h' drivers/dma/ --include=*.c | wc -l  # 43

# ---- dmaengine_get_debugfs_root 零调用者（§6 第 ⑯ 条）----
/usr/bin/grep -rn 'dmaengine_get_debugfs_root' --include=*.c --include=*.h . \
        | /usr/bin/grep -v 'drivers/dma/dmaengine.h'             # 空

# ---- dbg_summary_show 唯一实现者 ----
/usr/bin/grep -rn 'dbg_summary_show' drivers/dma/                # 定义 1 处 + k3-udma.c:5394

# ---- 打印普查（07 §2.1 那张表的来源）----
for p in pr_err dev_err dev_warn WARN_ONCE dev_dbg pr_debug 'BUG()' BUG_ON; do
    printf '%-12s %s\n' "$p" "$(/usr/bin/grep -c "$p" drivers/dma/dmaengine.c)"
done
```

**`dma-channel-mask` 那一条要展开看**，因为它最像"通用属性"却最不通用
（06 §6 有完整表格）：`drivers/dma/bcm2835-dma.c:947` 读的是
**`"brcm,dma-channel-mask"`**（带厂商前缀），不是通用名。
这类"名字很像"的差异只能靠逐个打开看，`wc -l` 会算错。

### §2.4 时间线次序的验证方法（03 Part A 的依据）

**判据只有一个：谁调用谁。** 不用直觉排，也不用"看起来应该先做什么"。

做法：对每一步找出它的唯一调用点，反向串起来。例如"通道注册"这一步：

```bash
/usr/bin/grep -rn '__dma_async_device_channel_register' drivers/dma/
#   dmaengine.c:1039  定义
#   dmaengine.c:1249  在 dma_async_device_register 的逐通道循环里被调
#   dmaengine.c:1090  导出给驱动单独调的包装
```

这个方法抓出过一个真实的次序错误：初稿把 `dmaengine_debug_register`
排在通道注册**之前**，实际它在 `:1276`，在
`list_add_tail_rcu`（`:1270`）**之后**。

### §2.5 全称断言的验证方法（skill 规则 19）

本套文档里出现的全称断言，逐条对应的验证命令：

| 断言 | 验证命令 | 结果 |
|---|---|---|
| `dmaengine_get_debugfs_root` 全树零调用者 | 见 §2.3 | 空 |
| `dbg_summary_show` 全树唯一实现者是 k3-udma | 见 §2.3 | 1 处 |
| 核心（四个文件）零 tracepoint | 见 §2.3，并逐个确认 3 个命中都在 tegra20-apb-dma.c | 成立 |
| arm64 上没有任何 `dma-router` 节点 | 见 §2.3 | 0 |
| `CONFIG_ASYNC_TX_CHANNEL_SWITCH` 不是 Kconfig 符号 | `/usr/bin/grep -rn 'ASYNC_TX_CHANNEL_SWITCH' --include=Kconfig* .` | 空 |
| `mtk-uart-apdma.c` 没有设置 `device_synchronize` | `/usr/bin/grep -n device_synchronize drivers/dma/mediatek/mtk-uart-apdma.c` | 空 |

**框架的使用者不一定住在框架自己的目录下** —— 所以
`dmaengine_get_debugfs_root` 这一条必须在**整棵树**上 grep，
不能只搜 `drivers/dma/`。

### §2.6 索引工具的定位

用了 codebase-memory-mcp（cbm）做导航，**没有用它取证**。
它在本子系统上的两个具体盲区：

1. **`static inline` 包装**：`include/linux/dmaengine.h` 里有大量
   `dmaengine_*` 包装（`dmaengine_submit` `:1212`、
   `dmaengine_terminate_async` `:1129` 等），索引里的调用关系不完整。
   这些全部靠 `rg` + `Read` 确认。
2. **函数指针间接调用**：`device_prep_slave_sg` / `of_dma_xlate` 这类回调
   是本子系统的主力机制，索引给不出"谁实现了它"。
   全部靠 `/usr/bin/grep -rn '\.device_prep_slave_sg' drivers/dma/` 这类
   赋值点普查。

### §2.7 arm64-only 裁掉了什么

**显式记录，避免读者当成遗漏：**

| 裁掉的内容 | 规模 | 理由 |
|---|---|---|
| `drivers/dma/acpi-dma.c` | 481 行 | ACPI 路径。用户明确要求"后续 ACPI 的也不用在文档里面描述" |
| `Documentation/driver-api/dmaengine/pxa_dma.rst` | 190 行 | PXA 是 arm32 平台 |
| `drivers/dma/ipu/`、`drivers/dma/bestcomm/`、`drivers/dma/ppc4xx/` | — | PowerPC / 老 x86 平台专用 |
| `drivers/dma/idxd/`、`drivers/dma/ioat/`、`drivers/dma/ptdma/` | — | Intel / AMD x86 专用 |
| async_tx / RAID offload 的**展开** | — | 保留了机制说明（04 §4）和一条设计债（§6 第 ⑫ 条），但不展开 xor/pq 的算法 |
| router / dmamux 的**逐驱动分析** | 4 个驱动 | arm64 上零使用（§2.3 验证过），只讲机制（04 §3 / 06 §5） |

**ACPI 那一条的连带影响**：`dma_request_chan`
（`dmaengine.c:813-873`）里有一条 `acpi_dma_request_slave_chan_by_name`
的分支，本文档在 03 Part A 里**只提了一句它存在**，没有展开。
如果你在 ACPI 机器上排查问题，03 那一节不适用。

---

## §3 逐篇取证表

规模：00–08 共 **6130 行**，本篇另计。

### §3.1 `01-api.md`（410 行）

| 论断 | 出处 |
|---|---|
| 消费者侧 API 全在一个头文件里 | `include/linux/dmaengine.h`（1637 行）；提供者侧另有 `drivers/dma/virt-dma.h`（227 行） |
| 三步提交的三个入口 | `dmaengine_prep_slave_sg` `:977` / `dmaengine_submit` `:1212` / `dma_async_issue_pending` `:1418` |
| 错误码语义表 | 逐个打开确认：`-ENOSYS` `:952`/`:1105`、`-EINVAL` `:1134`、`-EPROBE_DEFER` `of-dma.c:294`、`-EIO` `dmaengine.c:1153-1214` 九道门、`-EBUSY` cookie 初值 `:582` |

**自审修正**：初稿把 `dma_request_slave_channel_compat` 标成"已废弃"。
实际它**没有** `__deprecated` 标记，只是它调用的
`dma_request_slave_channel`（`:1602` 调用点）带。改成了"包装了一个
已废弃函数但自己没标"，并升级成 §6 第 ⑬ 条。

### §3.2 `02-datastructures.md`（622 行）

| 论断 | 出处 |
|---|---|
| §1.0 否掉 device/driver 类比 | 无 `bus_type`（§1.2）；`chan->dev` 只是 sysfs 容器，`dev_set_name` `dmaengine.c:1069-1070`；关联只有两条符号链接 `:866-870` |
| `struct dma_device` 字段 | `include/linux/dmaengine.h` 结构体逐字段 |
| `struct dma_chan` 的 cookie 双水位 | `:331`（`cookie`）/ `:332`（`completed_cookie`） |
| `virt_dma_chan` 五链表 | `drivers/dma/virt-dma.h:22-37`，逐条 `:30`~`:34` |
| 锁清单 | `dma_list_mutex`（`dmaengine.c:59`）+ 每通道 `vc->lock`（`virt-dma.h:24`）+ RCU 遍历（`:1270` 的 `list_add_tail_rcu`） |

**自审修正**：初稿说 `chan->slave` 指向"使用者的 device"，
但没写它**什么时候被设**。补上 `dmaengine.c:864` 这一行，
并指明它只在 `dma_request_chan` 成功路径上设，
匿名申请路径不设 —— 这解释了为什么 sysfs 上看不到 memcpy 通道的使用者。

### §3.3 `03-internals.md`（1033 行）

Part A 时间线的每一步都有唯一调用点，主要行号：

| 步骤 | 行号 |
|---|---|
| `dma_bus_init` / `class_register` | `dmaengine.c:1642-1653`，`:1649` |
| `dma_channel_table_init` 抹掉三个能力位 | `:273-275` |
| `dma_async_device_register` 全流程 | `:1137`；dev 检查 `:1143-1150`；九道能力门 `:1153-1214`；`dev_id` `:1240-1242`；逐通道注册 `:1247-1251`；`list_add_tail_rcu` `:1270`；debugfs `:1276`；错误路径 `:1280-1296` |
| `of_dma_controller_register` | `of-dma.c:118-145` |
| `dma_request_chan` | `dmaengine.c:813-873` |
| `of_dma_request_slave_channel` | `of-dma.c:234`/`:237-238`/`:282-286`/`:289`/`:292`/`:294` |
| 拆除路径 | `dma_release_channel` `:903-912`；`__dma_async_device_channel_unregister` `:1103-1119`；`dma_async_device_unregister` `:1307-1326` |

**自审修正 3 处**：
1. 次序错误：`dmaengine_debug_register` 被排在通道注册之前（见 §2.4）。
2. 初稿写"filter-map 回退调 `__dma_request_channel`"。实际
   `dmaengine.c:844` 调的是
   `find_candidate(d, &mask, d->filter.fn, map->param)`，
   **不经过 `__dma_request_channel`**。已改，并在 04 §5.2 同步。
3. Part C 的设计债一节初稿是空的。补齐后成为 §6 的雏形。

### §3.4 `04-extensions.md`（602 行）

| 论断 | 出处 |
|---|---|
| virt-dma 挂在时间线哪一点 | `vchan_init`（`virt-dma.c:123-138`），在控制器 probe 里、`dma_async_device_register` 之前 |
| 43 个驱动用 virt-dma | §2.3 的 grep |
| metadata 三套 API | `dmaengine.c:1519-1536`/`:1538-1555`/`:1557-1574`，模式检查 `desc_check_and_set_metadata_mode` `:1504-1516` |
| router 机制 | `of_dma_router_register`（`of-dma.c` 内）+ `of_dma_router_xlate:66-67`；4 个驱动，arm64 零使用 |
| async_tx 是独立层 | `include/linux/async_tx.h`；`dma_wait_for_async_tx` `:1581-1599`、`dma_run_dependencies` `:1608-1640` |

**自审修正**：初稿把 router 说成"arm64 上少用"。改成
**"零使用"**，因为 §2.3 的 `grep -rl 'dma-router' arch/arm64/boot/dts/`
返回 0。对称符号必须逐个 grep，"少"和"零"是两个结论。

### §3.5 `05-usage.md`（838 行）

| 论断 | 出处 |
|---|---|
| 消费者骨架步骤 | `client.rst` + `dma_request_chan` `:813` / `dmaengine_slave_config` `:946` / `prep` `:977` / `submit` `:1212` / `issue_pending` `:1418` / `terminate_sync` `:1177` / `dma_release_channel` `:903` |
| 提供者骨架步骤 | `provider.rst` + `vchan_init` `virt-dma.c:123` / 九道能力门要求 / `dma_async_device_register` `:1137` / `of_dma_controller_register` `of-dma.c:118` |
| 真实 probe 参照 | `drivers/dma/mediatek/mtk-uart-apdma.c:531-549` |

**篇首已声明骨架未编译未运行**（skill 规则 20）。
正文出现的每个符号都跑过全树 grep。

**自审修正**：骨架里原本写了 `dmaengine_terminate_sync` 之后
直接 `dma_free_coherent`。这正是 §6 第 ⑤ 条的坑，
已加注释要求先确认控制器实现了 `device_synchronize`。

### §3.6 `06-devicetree.md`（561 行）

| 论断 | 出处 |
|---|---|
| 核心只读 3 个属性（`dmas` / `dma-names` / `#dma-cells`） | `of-dma.c:231`（`dma-names`）、`:237-238`（`dmas` + `#dma-cells`）；且 `dmaengine.c` 里一个 `of_property*` 都没有（§2.3） |
| `dma-channels` / `dma-requests` / `dma-channel-mask` 是驱动私有 | §2.3 的计数：28 / 13 / 5 |
| `bcm2835-dma.c:947` 读的是带厂商前缀的名字 | 逐个打开确认 |
| DTS 举例全部 in-tree arm64 | `arch/arm64/boot/dts/rockchip/rk3399.dtsi`（`dmac_bus` `:1415-1424`、`dmac_peri` `:1426-1435`、`spi0` `:727-739`）；`arch/arm64/boot/dts/mediatek/mt2712e.dtsi`（`apdma` `:384-415`、串口 `:304-306` / `:433-435`） |
| 那一个 cell 的含义由驱动定 | pl330 是请求线号（`pl330.c:2142-2160`）；mtk 是通道号（`of_dma_xlate_by_chan_id`，`of-dma.c:351-370`） |

绑定文档 `arm,pl330.yaml` / `mediatek,uart-dma.yaml` **只写文件名不带行号**，
原因见 §7.2。

### §3.7 `07-debug.md`（697 行）

| 论断 | 出处 |
|---|---|
| debugfs 只有一个文件 | `dmaengine.c:63`–`:141`；`dbg_summary_show` 唯一实现者 `drivers/dma/ti/k3-udma.c:5394` |
| sysfs 三个只读属性 | `dmaengine.c:146`/`:154-244` |
| 打印普查表 | §2.3 的 for 循环 |
| 核心零 tracepoint，全树唯一例外是 tegra20-apb-dma | §2.3；且 `drivers/dma/Kconfig:656` 是 `depends on ARCH_TEGRA \|\| COMPILE_TEST`，四个 compatible（`tegra20-apb-dma.c:1663`/`:1666`/`:1669`/`:1672`）在 arm64 dts 里零引用 |
| dmatest 参数表 | `dmatest.c:24`–`:88` + `dmatest.rst` 逐条对齐 |
| dmatest 测不了 slave | `dmatest.c:1077`（走匿名申请）+ `:1111`–`:1114`（只四种能力）+ `dmaengine.c:275`（`DMA_SLAVE` 被抹） |

**自审修正 2 处**：
1. **"零 tracepoint" 的口径错误**，详见 §4 第 (1) 条。
2. §5.2 的参数名初稿写成 `bufsize`，实际是 `test_buf_size`；
   且 `test_list` / `wait` 的权限是 `0444`，写它们是**VFS 拒绝**，
   不是 dmatest 返回 `-EPERM`。已改。

### §3.8 `08-feynman.md`（682 行）

九个主题，每个都带行号（skill 规则 23：费曼篇不豁免取证）。关键取证：

| 主题 | 关键行号 |
|---|---|
| §1 不是 device/driver | `dmaengine.c:240-244`、`:1069-1070`、`:866-870`、`:641` |
| §2 `dma-names` 是纯字符串比较 | `of-dma.c:231`/`:234`/`:235`/`:270`；静默失败 `:282-286`/`:294` |
| §3 `#dma-cells` 那个数字 | `pl330.c:2142-2160`（`:2149`/`:2152`/`:2156-2157` 三个静默 `return NULL`）；`of-dma.c:292`/`:321-335`/`:351-370` |
| §4 三步提交 | `:582`、`:1212`、`virt-dma.h:63`/`:82-86`（`:84` 一行挪链表）、`dmaengine.c:1224` |
| §5 cookie 是整数 | `:22`/`:23`/`:331-332`、`drivers/dma/dmaengine.h:34-37`（`:35-36` 回绕）/`:52-57`（`:56` 清零、`:47` 注释）、`include/linux/dmaengine.h:1457-1468`、`:20`、`:25-28` |
| §6 匿名 vs 具名 | `dmaengine.c:273-275`、`dmatest.c:1077`/`:1111-1114`、`dmatest.rst:19-22`、`dmaengine.c:786-804` |
| §7 五条链表 | `virt-dma.h:22-37`/`:70`/`:84`/`:103`/`:149`/`:211-225`（`:220`）/`:120`、`virt-dma.c:19`/`:32`/`:83-110`（`:91`）/`:123-138`（`:137`） |
| §8 刹车不等于停稳 | `include/linux/dmaengine.h:1112-1121`（kerneldoc 原文）、`:1155-1161`（`:1159` 的空落 `if`）、`:1177-1187`（`:1181`/`:1185`）、`:1123-1124`、`:934`/`:835`；活例子 `mtk-uart-apdma.c` 无 `device_synchronize` |
| §9 谁填 slave_config | `:437-450` 逐字段、`:946`/`:949-950`/`:952`、`:977` |

### §3.9 `00-overview.md`（685 行）

按提速规则 5 **最后写**，所以它是纯浓缩，行号全部来自 01–08 的既有台账，
没有新推导。唯一的新增内容是 §2 硬件背景（⚠️ 篇内已声明是三等证据：
来自公开的 DMA/AXI 通用知识，不是从本树源码读出来的），
以及 §2.3/§2.7/§2.9/§2.10 里几条落到源码的锚点
（`include/linux/dmaengine.h:373-384`、`:443-444`、`:452`；
`mtk-uart-apdma.c:496` 读 `mediatek,dma-33bits`；§2.3 的三个 arm64 dts 计数）。

**自审修正 2 处**：规模数字（§4 第 (2) 条）和 tracepoint 口径（§4 第 (1) 条）。

---

## §4 交叉验证实例（初始假设被推翻的地方）

### (1) "`drivers/dma` 零 tracepoint" —— 错

初始假设：这个子系统一个 tracepoint 都没有。写进了 00 §1.3、00 §7.2、
07 的一页速查、07 §0 对比表、07 §7 的标题和 §7.1 的正文
（**还编了一行 `| wc -l   # 0` 的假输出**）。

推翻它的命令：

```bash
/usr/bin/grep -rn 'trace_\|TRACE_EVENT' drivers/dma/ --include=*.c --include=*.h
# 3 行：drivers/dma/tegra20-apb-dma.c:657 / :674 / :939
```

为什么初稿会错：第一次只搜了核心四个文件，然后把结论**扩大**到了整个
`drivers/dma/`。这是典型的"样本范围 ≠ 断言范围"。

最有意思的一点：**`03-internals.md:954-955` 从一开始就是对的** ——
它写的是"核心三文件里没有任何 tracepoint，`include/trace/events/` 下与
DMA 相关的只有 `tegra_apb_dma.h`（单个驱动私有）"。同一套文档里，
限定了范围的那一处正确，图省事扩大口径的两处错误。
**这正好是 skill 规则 19 的实例：全称断言必须在断言自己声称的范围上 grep。**

已改法：所有说法收窄为"核心零 tracepoint"，并补上例外的
arm64-无关性证明（`drivers/dma/Kconfig:656` + 四个 Tegra compatible +
arm64 dts 零引用）。

### (2) 规模数字张冠李戴

初始写法：「`drivers/dma/` 约 80600 行 / 147 个 `.c`」。
重跑 `wc` 发现这是**两次不同统计的结果被配在了一起**：

| 口径 | 文件数 | 行数 |
|---|---|---|
| 递归（含 18 个厂商子目录） | 147 | 144616 |
| 仅顶层 | 63 | 76530 |

80600 既不是 144616 也不是 76530 —— 它大概是某次中途统计的残留。
**教训：文件数和行数必须在同一条命令里得出**，
分两次跑就有配错的风险。已在 00 §1.3 改为同时给两组。

### (3) `dmatest` 全绿 ≠ slave 通道没问题

初始假设：dmatest 是这个子系统的通用自测工具。
读 `dmatest.c:1077` 发现它走的是匿名申请路径，再顺着匿名路径查到
`dma_channel_table_init` 在 `dmaengine.c:275` 把 `DMA_SLAVE`
从全局 mask 里抹掉了 —— 于是**匿名路径在机制上永远看不见 slave 通道**。

这条因果链官方文档没写（`dmatest.rst` 只列了支持的四种能力，
没说为什么 slave 不在其中）。它改变了 07 §5 的定性：
dmatest 不是"通用自测"，而是"memcpy 路径自测"。
而 arm64 上绝大多数 DMA 用途都是 slave。

### (4) `terminate_sync` 的 sync 可能是假的

初始假设：`dmaengine_terminate_sync` 顾名思义会同步等待。
读 `include/linux/dmaengine.h:1155-1161` 发现 `dmaengine_synchronize`
在 `device_synchronize` 为 NULL 时 `if` 直接落空 —— **什么都不做，也不报错**。
再查 `device_synchronize` 是不是必选：九道能力门（`dmaengine.c:1153-1214`）
里没有它，`provider.rst` 也没强调。

最后在本树里找到了活例子：
`drivers/dma/mediatek/mtk-uart-apdma.c` 没有设置这个回调
（`grep -n device_synchronize` 返回空）。
**于是"名字里的 sync 是假的"从推测变成了可指名的事实**（08 §8、§6 第 ⑤ 条）。

### (5) `dmaengine_debug_register` 的位置

见 §2.4。凭直觉排时间线把它放在通道注册之前，
`grep` 调用点发现它在 `:1276`，在 `list_add_tail_rcu`（`:1270`）之后。

### (6) "per-device debugfs 目录永远是空的" —— 错

初始推理：`dmaengine_get_debugfs_root`（访问器）零调用者 ⇒
`dma_dev->dbg_dev_root` 这个目录没人往里放东西 ⇒ 永远是空目录。
写进了 07 §1.2 和 §4.1。

**推翻它的命令**（在做 §9 对照时才想到要跑）：

```bash
/usr/bin/grep -rn 'dbg_dev_root' --include=*.c --include=*.h .
```

三个驱动**绕开访问器、直接摸结构体字段**：hisi_dma、xilinx_dpdma、
ptdma-debugfs（行号见 §6 第 ⑯ 条）。前两个在 arm64 上相关。

为什么初稿会错：**推理链多走了一步。**
"访问器零调用者"是 grep 出来的事实；"目录是空的"是从它**推**出来的，
中间隐含了"驱动只会通过访问器访问"这个没验证的假设。
**教训：凡是形如"A 没人用 ⇒ B 不会发生"的结论，
B 本身必须独立 grep 一次。**（这条已回吐给 skill / methodology。）

---

## §5 明确未验证项

### §5.1 未编译、未运行（最重的一层）

- **05 的所有代码骨架都没编译过。** 没有交叉编译环境，没有 arm64 目标板。
  骨架只保证"每个符号在本版本存在"（逐个 grep 过），
  **不保证能编过，更不保证跑对**。
- **07 的所有命令行都没在真机上跑过。**
  `cat /sys/class/dma/dma0chan0/*`、`cat /sys/kernel/debug/dmaengine/summary`、
  `modprobe dmatest ...` 这些都是从代码反推出来的形式。
  **07 里没有编造过任何一行"输出示例"** —— 凡是没有真实输出的地方，
  写的是"输出的字段来自 `<函数>` 的 `seq_printf`"，而不是贴一段假输出。
  （唯一一次编了输出的地方是 §4 第 (1) 条那个 `# 0`，已删。）
- **DTS 片段没有 `dtc` 编译过，更没有实际引导过。**

### §5.2 只读了符号表 / 只读了片段

- `drivers/dma/` 下 147 个 `.c` 里，**只有 3 个被部分详读**（§2.2 那张表），
  其余 144 个只做过针对性 grep。凡是本文档说"驱动通常如何"，
  依据是契约 + 抽样，不是普查。
- `drivers/dma/dmatest.c`（1360 行）只详读了约 400 行（§2.1）。
  dmatest 的**测试算法本身**（数据填充、校验、错误统计）没读，
  07 §5 只讲了怎么用和结果怎么看。
- `include/linux/async_tx.h` 只读了 `:77` 和 `:106-125` 两处
  （为了 §6 第 ⑫ 条）。`crypto/async_tx/` 整个目录没读。
- 中断处理路径**没有系统读过**。本子系统的中断在控制器驱动里，
  核心只提供 `vchan_cookie_complete` / tasklet 这一半
  （`virt-dma.c:83-110`）。所以"中断里能不能调 X"这类问题，
  本文档只在 kerneldoc 明说的地方给了答案（如 `:1123-1124`）。

### §5.3 推断而非观察

- **00 §2 整节（硬件背景）是三等证据**：DRQ/DACK 握手、AXI 突发、
  cache 一致性的机制描述来自通用公开知识，**不是从本树源码读出来的**。
  篇内已用 ⚠️ 标明。落到源码的只有那几个锚点（§3.9）。
- **"arm64 上通道不够用是常见故障"**（08 §1 末）是从机制推的
  （通道独占 + `:641` 的 busy 分支只有 `dev_dbg`），
  没有真实的故障统计支撑。
- **"pl330 是 arm64 上最常见的 DMA 控制器"** 依据是 dts 计数
  （17 个文件），不是出货量。
- **§6 里被标成"设计债"的条目**，只有代码事实是确定的；
  "这是个问题"这个判断是本文档的意见，不是内核社区的共识。
  上游有没有讨论过、有没有被认为是 by design，**没有查 LKML**。

### §5.4 版本时效

本文档只对 **v6.1.158（= 本树的 dmaengine，§1.1 已验证零改动）** 成立。
已知会漂移的地方：

| 内容 | 漂移风险 |
|---|---|
| 所有 `文件:行号` | 最高。跨小版本就可能偏 |
| 九道能力门覆盖哪 9 个能力位 | 中。新增能力位时会变（§6 第 ③ 条） |
| §2.3 的所有计数 | 中。新驱动进来就变 |
| 「唯一实现者 / 零调用者」类断言 | 高。加一个使用者就不成立 |
| 结构体字段偏移与顺序 | 低（但字段本身可能增删） |
| 三步提交、cookie 语义、terminate/synchronize 契约 | 低。这是稳定 ABI 级的设计 |

### §5.5 不适用项（本套明确不覆盖）

- ACPI 路径（§2.7）
- x86 / PowerPC / arm32 专用控制器（§2.7）
- async_tx 的 RAID 算法（§2.7）
- router / dmamux 的逐驱动实现（§2.7）
- **性能**：没有任何吞吐、延迟、CPU 占用的数据。
  本文档一次 benchmark 都没跑过，凡是出现"更快/更省"的说法，
  都是机制层面的定性，不是测量。

---

## §6 已发现的问题清单（16 条）

**读法：** "代码事实"一列是确定的（行号可查）；"这是个问题"是本文档的判断，
上游态度未查（§5.3）。**没有提交过任何补丁**，也没有查 LKML 是否已讨论。

按"读文档的人踩到的概率"排序。

### 高影响（会咬到写驱动的人）

**① `virt-dma.c:101` 对 NULL 做指针运算**

`vchan_complete` 里取出 `vd` 之后：

```c
/* virt-dma.c:100 */ if (dmaengine_desc_test_reuse(&vd->tx))
/* virt-dma.c:101 */         list_add(&vd->node, &vc->desc_allocated);
```

上面 `:96` 那条 `vd = ...` 在链表空时会留下一个不指向有效对象的值，
而 `dmaengine_desc_test_reuse(&vd->tx)` 里的 `&vd->tx` 是
**在 `vd` 上做偏移**。实际执行路径受 `:91` 摘链表的结果保护，
所以现实中不会触发；但**代码形状上依赖调用者保证非空，没有断言**。

**② 同一个"回调缺失"条件，三个不同的错误码**

| 函数 | 行号 | 没回调时返回 |
|---|---|---|
| `dmaengine_terminate_all` | `include/linux/dmaengine.h:1100-1106` | `-ENOSYS`（`:1105`） |
| `dmaengine_terminate_async` | `:1129-1135` | **`-EINVAL`**（`:1134`） |
| `dmaengine_slave_config` | `:946-953` | `-ENOSYS`（`:952`） |

三个函数、完全相同的判据（`chan->device->device_xxx` 为 NULL）、
两种错误码。**调用者没法用一个统一的判断"这个控制器不支持"。**

**③ 注册期只检查 16 个能力位中的 9 个**

`dma_async_device_register` 的九道门（`dmaengine.c:1153-1214`）覆盖
MEMCPY / XOR / XOR_VAL / PQ / PQ_VAL / MEMSET / INTERRUPT / CYCLIC /
INTERLEAVE。**没被检查的 7 个**（`include/linux/dmaengine.h:51-70`）：

| 能力位 | 行号 |
|---|---|
| `DMA_MEMSET_SG` | `:58` |
| `DMA_PRIVATE` | `:60` |
| `DMA_ASYNC_TX` | `:61` |
| `DMA_SLAVE` | `:62` |
| `DMA_COMPLETION_NO_ORDER` | `:65` |
| `DMA_REPEAT` | `:66` |
| `DMA_LOAD_EOT` | `:67` |

**最要紧的是 `DMA_SLAVE` 本身不在被检查之列** ——
一个驱动可以声明 `DMA_SLAVE` 而不实现 `device_prep_slave_sg`，
注册照样成功，失败推迟到消费者调 prep 的时候（那时只能拿到 NULL）。
而 arm64 上绝大多数用途都是 slave。

**⑤ `device_synchronize` 可选 → `terminate_sync` 静默退化**

`dmaengine_synchronize`（`include/linux/dmaengine.h:1155-1161`）在
`device_synchronize` 为 NULL 时 `if`（`:1159`）直接落空，
**不做事也不报错**。于是 `dmaengine_terminate_sync`（`:1177-1187`）
退化成 `terminate_async`。

活例子：`drivers/dma/mediatek/mtk-uart-apdma.c` 没有设置这个回调。
**在那个控制器上"terminate_sync 之后立刻释放 buffer"
是一个真实存在的 use-after-free 窗口。**（详见 08 §8、§4 第 (4) 条）

**④ 把枚举当位掩码用**

```c
/* drivers/dma/qcom/bam_dma.c:1349 */  ... = BIT(DMA_DEV_TO_MEM) | BIT(DMA_MEM_TO_DEV);
```

`bam_dma.c:1349` 这一处**没有 `BIT()`**，直接把
`enum dma_transfer_direction` 的值当掩码用。
正确写法在同树里就有：`drivers/dma/mediatek/mtk-uart-apdma.c:517`。
`directions` 字段的契约是位掩码（`include/linux/dmaengine.h` 的
`dma_device` 结构注释），枚举值 `DMA_MEM_TO_DEV = 1`（`:81`）、
`DMA_DEV_TO_MEM = 2`（`:82`）—— 恰好前两个值当掩码用**看起来能工作**，
所以这个错误很难被发现。

### 文档/契约缺陷（会让读者想错）

**⑥ `enum dma_status` 的 kerneldoc 漏了一个值**

kerneldoc 在 `include/linux/dmaengine.h:31-35`，只描述了 4 个值；
枚举本体 `:37-43` 有 5 个 —— `DMA_OUT_OF_ORDER`（`:42`）没有任何说明。
这个值和 `DMA_COMPLETION_NO_ORDER`（`:65`，也是未被门检查的 7 个之一）
配套使用，两个都没文档。

**⑮ metadata 的"指针用完即失效"契约没有任何断言**

`include/linux/dmaengine.h:258-260` 的注释说，
`get_ptr` 拿到的指针在传输完成后就无效了。
但 `dmaengine.c:1519-1536`（`dmaengine_desc_attach_metadata`）等三个
入口都不检查描述符状态，**用完之后再写那块内存不会被拦住**。

**⑪ "调用者必须持锁"只写在注释里**

`of_dma_find_controller`（`drivers/dma/of-dma.c`）要求调用者持
`of_dma_lock`。这个要求**既没写进 kerneldoc，也没有
`lockdep_assert_held`** —— 新加调用者时没有任何机制会提醒。

**⑬ 包装了 `__deprecated` 函数，自己没标**

`dma_request_slave_channel_compat`（`include/linux/dmaengine.h:1595-1609`）
在 `:1602` 调用 `dma_request_slave_channel`，后者带 `__deprecated`。
包装自己**不带** —— 所以用它不会有编译告警。

### 死代码与孤儿

**⑫ `CONFIG_ASYNC_TX_CHANNEL_SWITCH` 不是 Kconfig 符号**

```bash
/usr/bin/grep -rn 'ASYNC_TX_CHANNEL_SWITCH' --include=Kconfig* .   # 空
```

`include/linux/async_tx.h:77` 用 `#ifdef` 保护它，
于是 `:106-125` 那段是**永远不会编译进去的死代码**。

**⑯ `dmaengine_get_debugfs_root` 零调用者（但目录本身有人用）**

访问器定义在 `drivers/dma/dmaengine.h:188-191`，全树 grep（§2.3）除定义外无命中。

**但"访问器零调用"≠"目录是空的"**：三个驱动直接摸
`dma_dev.dbg_dev_root` 字段（`include/linux/dmaengine.h:943`），绕开了访问器 ——
`drivers/dma/hisi_dma.c:906`/`:921`/`:939`、
`drivers/dma/xilinx/xilinx_dpdma.c:458`、
`drivers/dma/ptdma/ptdma-debugfs.c:93`/`:96`/`:102`。
前两个在 arm64 上相关。所以这条的问题是**核心提供的访问器没被采纳**
（驱动宁可直接摸字段），不是"这个机制没人用"。详见 07 §1.2 的表。

**⑭ 四个驱动给 `of_dma_xlate_by_chan_id` 传了错类型**

`of_dma_xlate_by_chan_id`（`drivers/dma/of-dma.c:351-370`）的第二参数
签名是 `void *ofdma_data`，但实现里当成 `struct dma_device *` 用。
有四个驱动在 `of_dma_controller_register` 时传的是自己的私有 struct。
因为私有 struct 的**第一个成员**恰好是 `struct dma_device`，
所以指针值相同、能工作 —— 这是靠布局巧合成立的，
`void *` 签名让编译器完全查不出来。

### 核心自身的小疏漏

**⑦ 带 "WARN:" 字样的消息用 `dev_dbg` 打**

```c
/* dmaengine.c:1228-1230 */ dev_dbg(device->dev, "WARN: ...");
```

默认内核配置下 `dev_dbg` **什么都不输出**。
一条自称 WARN 的诊断，实际可见性等级是最低的那一档
（07 §2 的可见性表里单独列了这条）。

**⑧ 错误路径漏回收两样东西**

`dma_async_device_register` 的 `err_out`（`dmaengine.c:1280-1296`）：
当 `chancnt != 0` 时提前返回，**既没有归还 `dev_id`
（`:1240-1242` 的 `get_dma_id`），也没有 `ida_destroy(&device->chan_ida)`
（`:1244` 的 `ida_init`）**。probe 反复失败会泄漏 id。

**⑨ `chan->local` 释放后不置 NULL，而哨兵测的是 NULL**

| 路径 | 行号 | 做法 |
|---|---|---|
| 注册失败 | `dmaengine.c:1085` | 释放后**置 NULL** |
| 正常注销 | `:1118` | 释放后**不置 NULL** |
| 哨兵检查 | `:1106` | 测的正是 `chan->local == NULL` |

两条路径对同一个字段的处理不一致，而判据只认其中一种。

**⑩ router 的错误信息被压成一个值**

`of_dma_router_xlate`（`drivers/dma/of-dma.c:66-67`）把
route 分配失败的各种原因统统折成 `NULL`。
`NULL` 回到 `of_dma_request_slave_channel` 就变成了预置的
`-EPROBE_DEFER`（`of-dma.c:294`）。
**于是"路由资源真的用尽了"和"驱动还没上来"在日志上完全一样。**

### 观测缺口（不算 bug，但影响可调试性）

- **dmaengine 核心零 tracepoint。** `dmaengine.c` / `dmaengine.h` /
  `of-dma.c` / `virt-dma.c` / `include/linux/dmaengine.h` 一个都没有。
  全树唯一带 tracepoint 的 DMA 驱动是 `drivers/dma/tegra20-apb-dma.c`
  （3 个调用点，事件定义在 `include/trace/events/tegra_apb_dma.h`，61 行），
  **arm64 上碰不到**（`drivers/dma/Kconfig:656` 只对 `ARCH_TEGRA` 开）。
  对比同类框架：clk 有 `include/trace/events/clk.h`。
  树里已有现成范本可照抄（07 §7.3）。
- `dbg_summary_show` 全树**只有一个实现者**
  （`drivers/dma/ti/k3-udma.c:5394`）。
  所以 `/sys/kernel/debug/dmaengine/summary` 在 arm64 上
  绝大多数平台只有通道列表，没有每通道细节。
- 通道申请失败的全路径都是 `dev_dbg`
  （`dmaengine.c:626`/`:641`/`:646`/`:675`/`:679`/`:710`）。
  默认配置下"DMA 申请不到"这件事**在 dmesg 里一个字都没有**。

---

## §7 批量自检脚本的结果

### §7.1 `check_xrefs.py`（跨篇 `0X §Y` 引用）

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
python3 $S/check_xrefs.py output/dmaengine --ignore output/dmaengine/.xref-ignore
```

结果记在 §7.5。**约定**：本篇 §9 引用对照套（`output/qoder/dma/`）的章节时，
一律写成「对照套 07 的 §2.3」这种形式（数字和 `§` 之间插一个"的"）。
原因：脚本的裸引用模式是 `(\d{2})\s*§\s*(章节号)`，
"07 §2.3" 会被当成对本套 07 的引用去校验。插一个"的"就不匹配了，
**不需要往 ignore 文件里加条目**。同理，**绝不能写出对照套的
`NN-名字.md` 文件名** —— 那会命中 `XREF_MD` 模式。

### §7.2 `check_line_refs.py`（`路径:行号` 是否越界 / 能否解析）

```bash
python3 $S/check_line_refs.py output/dmaengine .
```

**三个必须登记的已知项：**

**(a) 一个残留 AMBIGUOUS：`dmaengine.h`**

本树里有两个同名文件，本套文档**两个都写过全路径**：
`include/linux/dmaengine.h`（1637 行）和 `drivers/dma/dmaengine.h`（201 行）。
脚本因此报 AMBIGUOUS 并按 `include/linux/` 那个校验。

**人工普查结论：正文里裸写 `dmaengine.h:` 的全部指
`include/linux/dmaengine.h`**；指 `drivers/dma/dmaengine.h` 的地方
**一律写全路径**（cookie 助手 `:16-20`/`:29-40`/`:52-57`/`:68-83`、
debugfs 桩 `:188-191`）。读者看到裸 `dmaengine.h:` 可以直接当
`include/linux/` 读。

**(b) 两个文件名只能不带行号引用**

`arm,pl330.yaml` 和 `mediatek,uart-dma.yaml`。
原因是脚本的基名解析**按逗号切词**，会把它们切成
`pl330.yaml` / `uart-dma.yaml`，然后永远解析不到 → 报 UNRESOLVED，
**而 `--ignore` 只对越界行号生效**（脚本 `:172` 在
`if hi > total or start < 1:` 分支内），压不掉 UNRESOLVED。
所以 06 / 08 引用这两个绑定文档时只写文件名。
（clk 套的 `arm,pl172.txt` 是同一个原因。）

**(c) 一度出现过第二个 AMBIGUOUS：`Kconfig`（已消除）**

本套引用了四个不同目录下的 Kconfig：`drivers/dma/Kconfig`、
`drivers/dma/ti/Kconfig`、`drivers/dma/dw/Kconfig`、`crypto/async_tx/Kconfig`。
中途脚本报过 `Kconfig` 的 AMBIGUOUS，原因是本篇 §6 里有**一处**
只写了 `Kconfig` 加行号、没带目录。补上 `drivers/dma/` 前缀之后，
这条 AMBIGUOUS 就消失了。
⚠️ 本节不能举出那个裸写的原样，**举一次就会把 AMBIGUOUS 重新引出来**
（第一次写本节时真的复发了一遍）。

**由此得到一个脚本行为的确证**：`AMBIGUOUS` **不是**"文档写过 ≥2 个同名全路径"
就报，而是**只在某条引用是裸基名、需要脚本替你猜路径时**才报
（`check_line_refs.py` 里 `others` 由 `resolve()` **逐条引用**返回，
写了全路径的引用不产生它）。
顺带一个同类陷阱：**脚本自己的路径不能带行号写进文档** ——
它不在内核树里，必然 UNRESOLVED（这条也当场踩了一次）。所以 **AMBIGUOUS 是可以靠"每处都写全路径"清零的**；
`dmaengine.h` 那条留着是因为正文有意保留了大量裸写（见 (a)）。

普查命令（现在 21 处全部带目录前缀）：

```bash
/usr/bin/grep -rho '[A-Za-z0-9_/,.-]*Kconfig:[0-9]*' output/dmaengine/ | sort -u
```

其余基名无风险：`virt-dma.h` / `virt-dma.c` / `of-dma.c` 全树各只有一个。

### §7.3 `check_doc_coverage.sh`（官方文档反向覆盖率）

**本套必须跑两次，参数不同，结果互补** —— 这是踩出来的：

| 命令 | 覆盖什么 | 为什么 |
|---|---|---|
| `... dmaengine output/dmaengine .` | 【必查】4 篇概念文档 | 概念文档在 `Documentation/driver-api/`**`dmaengine`**`/` |
| `... dma output/dmaengine .` | 【参考】38 篇 DT 绑定 | 绑定在 `Documentation/devicetree/bindings/`**`dma`**`/` |

**两个目录名不一样**，而脚本只接受一个子系统名，所以**单跑任何一次都不完整**：
传 `dmaengine` 得到「【必查】共 4 篇」但**一篇 DT 绑定都不报**；
传 `dma` 得到 38 篇绑定但「【必查】共 0 篇」——
后者看起来像"这个子系统没有官方概念文档"，是假象。
危险点在于**失败形式是"0 篇"而不是报错**，和"确实没有官方文档"无法区分。
两次的原样输出都在 §7.5。

**数字要打折看**（跨子系统通用陷阱）：脚本按去后缀基名做**子串**匹配，
所以 `client` / `provider` 这类普通英文词会被严重高估
（`dmatest` 报 108 次、`client` 报 63 次，真实的实质引用远少于此）；
而本篇 §1.3 / §2.7 讨论篇名本身也会被算成引用（自指污染）。

一个具体例子：**`pxa_dma` 被报成"引用 5 次"，但它其实是被整篇裁掉的**
（§2.7）—— 那 5 次全部来自 §1.3、§2.7 和本节里"为什么不读它"的说明
（本节这段话本身就是第 5 次，自指污染看得见）。
**真正的覆盖判据是 §1.3 那张手工登记表，不是脚本输出的次数。**

### §7.4 工具与复现环境

| 工具 | 版本 | 注意 |
|---|---|---|
| Python | 3.10.12 | 三个脚本纯 stdlib |
| GNU grep | 3.7 | 在 `/usr/bin/grep`，统计一律用它 |
| ugrep | 7.5.0（`+sse2; -P:pcre2jit`） | 本 shell 的 `grep` 是它的包装（带 `-G`）：**不支持 `\s`**，且**默认 honor `.gitignore`** |
| ripgrep | 13.0.0 | 导航用 |
| git | 2.34.1 | — |

**ugrep 那一条踩过**：`grep '\sdma'` 在本机静默匹配不到东西
（`\s` 不被 `-G` 的 BRE 认识），会得出"零命中"的假结论。
本篇所有统计命令都显式写了 `/usr/bin/grep`，正文里的 POSIX 写法用
`[[:space:]]`。

### §7.5 三脚本实跑结果（10 篇齐全后）

四次实跑，原样贴（`$S` 同 §7.1）：

```
$ python3 $S/check_xrefs.py output/dmaengine
文档 10 篇；跨篇引用命中 155 条，失效 0 条（忽略项 0 条）
干净：所有跨篇引用都指向真实存在的章节。

$ python3 $S/check_line_refs.py output/dmaengine .
扫描 371 条唯一 file:line 引用（消歧表 31 个基名 / 28 个前缀目录，忽略项 0 条）

== AMBIGUOUS（1 个简写文件名，树里有多个候选）==
   已按文档里最常用的全路径校验，但正文最好写全路径：
  dmaengine.h → 按 include/linux/dmaengine.h 校验；另有 drivers/dma/dmaengine.h

干净：没有解析不到的文件，也没有越界行号。

$ bash $S/check_doc_coverage.sh dmaengine output/dmaengine .
=== 【必查】概念/框架文档 ===
  client                                   引用 63 次
  dmatest                                  引用 108 次
  provider                                 引用 11 次
  pxa_dma                                  引用 5 次

【必查】共 4 篇，其中 0 篇 0 引用。
必查档全部有引用。

$ bash $S/check_doc_coverage.sh dma output/dmaengine .
=== 【必查】概念/框架文档 ===

=== 【参考】DT 绑定：共 38 篇，被引用 6 篇 ===
  arm,pl330（18 次）
  dma-common（14 次）
  dma-controller（12 次）
  dma-router（20 次）
  mediatek,uart-dma（8 次）
  ti/k3-udma（6 次）
  提醒：06 篇的硬要求是每条属性都有定位到行的消费者，不是每篇绑定都被引用。

【必查】共 0 篇，其中 0 篇 0 引用。
必查档全部有引用。
```

三个脚本**全绿**：0 条失效跨篇引用、0 条 UNRESOLVED、0 条越界行号、
0 篇零引用必查文档。**唯一剩下的 AMBIGUOUS（`dmaengine.h`）是有意保留的**，
处置见 §7.2 (a)；`Kconfig` 那条已经消除，过程见 (c)。
`.xref-ignore` / `.lineref-ignore` 两个文件本套**都不需要**（忽略项 0 条）——
所有假阳性都靠改写引用形式规避了（§7.1 的"的"字约定、
§7.2 (b) 的不带行号约定）。

⚠️ **三个脚本都抓不到的一类错误**：行号在范围内但指向无关代码。
这只能逐个 `sed` 打开核对（skill 规则 19），本套是逐篇做的（§3 的自审修正栏）。
而 §4 第 (1) 条那种错误连 `sed` 都抓不到 —— 它是**断言范围**错了，
不是行号错了。抓它的唯一办法是把全称断言当成命令重跑一遍（§2.5）。

---

## §8 本文档集的读法建议

| 你的处境 | 怎么读 |
|---|---|
| 隔几个月回来复习 | **只读 00**。它是按"复习入口"设计的，8 节读完能恢复全貌 |
| 第一次接触，想建直觉 | **08 → 00 → 03**。先破错误类比，再看全貌，最后看代码 |
| 要写一个消费者（UART/SPI 驱动加 DMA） | **05 §1 → 06 → 07 §5**。写完对着 §6 的 ①②⑤ 三条自查 |
| 要写一个控制器驱动 | **05 §2 → 04 §1（virt-dma）→ 03 Part A**。务必看 §6 第 ③ 条（声明能力位和实现回调是两件事） |
| 板子上 DMA 不工作 | **06 §7 的决策树 → 07 §3 分诊**。先怀疑 clk/reset/irq/IOMMU，再怀疑 dmaengine（00 §7.2 末） |
| 想知道某个论断可不可信 | **本篇 §3 找那一篇的取证表，§5 看它落在哪一层未验证** |

**如果只带走三件事**（和 00 §8.3 同步）：

1. 控制器和外设**不是** device/driver 关系，**平级**，靠 API + 两条符号链接关联。
2. `dmas = <&dmac N>` 里那个 `N` 的含义**由控制器驱动定**，写错**全程静默**。
3. `dmaengine_terminate_sync` 的 sync **可能是假的** ——
   先确认你的控制器实现了 `device_synchronize`。

---

## §9 与另一套文档集的对照（rule-29）

`output/qoder/dma/` 是同一子系统的另一套文档集（10 篇，**4353 行**，
本套 00–08 是 6130 行）。本节按 skill 规则 29 的五段式记录对照结果。
引用对照套时只写篇号，不写它的文件名 —— 避免污染 `check_xrefs.py` 的统计。

### §9.1 吸收过来的（2 条，均已落地）

**(1) `residue` 恒为 0 这类"没有错误码的故障"。**
对照套 07 的 §7.3 有一节专讲 residue 排查，而我这套的 07 **通篇没出现过
`residue` 这个词**——它只在 02（`dma_tx_state` 字段）和 04 里作为结构成员出现过。
这是一个真实的缺口：cyclic/音频/串口 DMA 上"residue 不动"是常见症状，
而它**不产生任何错误码**，正好落在 07 的职责范围里。

已落地为 **07 §6.1**，并补了 §8.2 速查表一行。取证时比对照套多挖了一层 ——
根因不是"驱动没写"，而是核心的 `dma_cookie_status`
（`drivers/dma/dmaengine.h:68-83`）**无条件把 `state->residue` 清零**
（`:79`），驱动如果不接着调 `dma_set_residue`（`:85-88`），
读出来就永远是 0。这条根因对照套没写。

**(2) cyclic / 音频的费曼题目。**
对照套 08 的 §3 是「Cyclic DMA：为什么音频要用它？」。
我这套 08 原本九个题目**没有一个讲 cyclic**——00 §2.5 在硬件层面提过
"环形/循环模式"，但没有"为什么这种形状能解决实时性问题"这一层。
对音频/串口这两个 arm64 上最主要的 DMA 消费方来说，这是个该有的题目。

已落地为 **08 §10**（十个题目，导航节顺延为 §11）。同样多挖了一层：
把 cyclic 的正确性和 residue 粒度绑在一起
（`include/linux/dmaengine.h:454`/`:458`/`:466`），
并指出 cyclic 的 overrun 是**静默丢数据**而非错误码。
于是 §9.1 这两条吸收项在正文里是互相咬合的，不是两个孤立补丁。

### §9.2 谢绝的（4 条，附理由）

| 对照套的内容 | 谢绝理由 |
|---|---|
| 04 的 §4 **dmapool** | **不是本子系统。** `dmapool` 在 `mm/dmapool.c`，属于 DMA **映射** API（`dma_alloc_coherent` 那一族）。我这套 00 §1.2 专门用一节把"DMA API"和"DMA engine API"划开，把 dmapool 写进"扩展框架"篇会**当场推翻那一节**。要讲也只能在 00 §1.2 那张对照表里作为"不是我"的例子出现 |
| 04 的 §2 **dma-fence** | 同上：`drivers/dma-buf/dma-fence.c` 是 DRM/GPU 的**同步原语**框架，和 `drivers/dma/` 只共享 "dma" 三个字母。对照套自己也标注它「独立但相关」——既然独立，就不该占"本子系统扩展框架"的一节。我这套 04 的组织轴是"一节一个挂在 03 时间线上的可选层"，dma-fence 挂不上任何一点 |
| 06 的 §6 Allwinner DRQ 端口号表 | 我这套 06 的举例约定是"必须取自本树 `arch/arm64/boot/dts/`，读者能 diff"。已经用了 rockchip rk3399（`#dma-cells = <1>`，pl330）和 mediatek mt2712e（uart-apdma）两个真实节点。再加一个 Allwinner 的端口号表只增加篇幅，不增加新机制 —— 那张表的信息量等价于"这个数字由控制器驱动解释"（08 §3 已讲透） |
| 08 的「一句话总结」四段式 | 对照套每个费曼题目的第四段是"一句话总结"。skill 规则 14.1 要求第四段必须是**「这个类比什么时候会误导你？」**。这不是格式偏好：类比边界是费曼法唯一不可替代的部分，换成"一句话总结"等于把最难写的一段删掉，换成最容易写的一段。本套 08 十个题目的第四段全部指出了一个真实的技术差异（如 §1 的独占性、§10 的静默 overrun） |

### §9.3 对照套里发现的问题（5 条）

**(1) tracepoint 的过度断言 —— 和我犯过的是同一个错。**
对照套 07 的 §2.1 写「dmaengine 子系统本身没有定义任何 tracepoint」。
这句话漏掉了 `drivers/dma/tegra20-apb-dma.c`（本树里唯一一个自带
tracepoint 的 DMA 驱动）。我这套初稿里也写过同样的话，
在 §4 第 (1) 条里被自己的复现命令推翻，现在全套统一narrow成
「**核心**零 tracepoint（唯一例外 tegra20-apb-dma，arm64 上碰不到）」。
两套文档独立犯同一个错，说明这是**方法上的共同盲区**：
"框架自己没定义"和"这棵树里没有"是两个不同的断言，前者不蕴含后者。

**(2) per-device debugfs 目录"目前为空" —— 同样是我犯过的错。**
对照套 07 的 §4.3 写那个目录「目前为空，供驱动添加自定义调试文件」。
实际有三个驱动往里放东西（`hisi_dma.c`、`xilinx/xilinx_dpdma.c`、
`ptdma/ptdma-debugfs.c`，其中前两个 arm64 相关），
证据和修正过程见本篇 §4 第 (6) 条与 §6 第 ⑯ 条。
两套都错在同一条推理上：从"核心的访问器没人调"推出"目录是空的"。
这条教训已经回吐给 methodology（§9.4）。

**(3) 07 的 §6.3 写了 ACPI 相关的错误码。**
按本项目的 arm64-only 约定这部分应裁掉（本篇 §2.7 记了裁剪清单）。
不算错，但和本项目的范围约定冲突。

**(4) 对照套的 99 §3「逐篇取证」只覆盖了 1 篇。**
它的 §3 下面只有一个 §3.1（对应 07），其余九篇没有取证表。
skill 规则把"逐篇取证表（00–08 每篇论断→出处 + 自审修正）"列为强制项 ——
覆盖 1/10 相当于没做。本套 §3 有 §3.1–§3.9 九个小节。

**(5) 对照套的 99 缺两块结构。**
其一，它的「未验证项」是一个平铺的 4 条列表，没有按
skill 要求的**四层**分（未编译未运行 / 只读符号表 / 推断非观察 / 版本时效）——
四层的价值在于告诉读者"这条不可信到什么程度"，平铺列表做不到。
其二，它**完全没有脚本自检结果**（本套 §7），
所以跨篇引用有没有失效、行号有没有越界都是未知的。

需要说清楚：以上 5 条里，(1)(2) 是**我也犯过**的错，
不是"我对它错"；(3) 是范围约定差异；只有 (4)(5) 是它单方面的结构性缺失。

### §9.4 回吐给 skill / methodology 的（2 条）

**(1) 新方法论规则：凡是形如"A 没人用 ⇒ B 不会发生"的结论，
B 本身必须独立 grep 一次。**
来源是本篇 §4 第 (6) 条。这类错误的隐蔽性在于**前提是真的**
（`dmaengine_get_debugfs_root` 确实零调用者，命令跑过），
所以自审时看到"有取证"就放过了 —— 但**取证的是 A，结论说的是 B**。
两套文档独立踩中同一个坑（§9.3 第 (2) 条），足以升级为通用规则。
判据很简单：结论句里的主语如果不是命令里 grep 的那个符号，就得再跑一次。

**(2) `check_doc_coverage.sh` 的第一个参数是 `Documentation/` 下的
*目录名*，不是子系统的俗称。**
本套踩过：`... dma output/dmaengine .` 报「【必查】共 0 篇」，
看上去像"这个子系统没有官方文档"；实际要传 `dmaengine`（文档在
`Documentation/driver-api/dmaengine/`）才会报出 4 篇。
详细复现见本篇 §7.3。危险点在于**失败形式是"0 篇"而不是报错**，
和"确实没有官方文档"无法区分。

### §9.5 结论

| 维度 | 本套（6130 行 + 99） | 对照套（4353 行） |
|---|---|---|
| 机制深度 | 逐段贴源码 + 行号，注册期 12 道门、五条链表、cookie 生命周期都展开到代码 | 以概念和 API 讲解为主，行号较少 |
| 取证结构 | 一/二/三等证据分离、逐篇取证表 9 篇、未验证四层、交叉验证 6 条、三脚本全绿 | 取证表覆盖 1 篇，未验证平铺 4 条，无脚本 |
| 自陈问题 | 16 条（①–⑯），每条带复现命令 | 未单列 |
| 覆盖范围 | 严格限于 `drivers/dma/` + dmaengine API，arm64-only | 更宽，但把 dmapool（DMA 映射 API）和 dma-fence（DRM 同步）也算进来了 —— 见 §9.2 |
| 费曼篇 | 10 题，第四段固定为"类比边界" | 题目数相近，第四段是"一句话总结" |

一句话：**对照套在"周边还有什么"上更宽，本套在"这一层到底怎么运作、
每句话凭什么这么说"上更深。** 两处真正的缺口（residue 排查、cyclic 题目）
已经吸收进正文，两条共同盲区（tracepoint 范围、由访问器无人用推目录为空）
已经修正并回吐成方法论规则。
