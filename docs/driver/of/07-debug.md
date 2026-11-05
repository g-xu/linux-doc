---
title: 调试方法
---

# of 07：调试手段

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径。统计口径是 **`drivers/of/` 的 15 个产品 `.c`**
> （排除 `unittest.c` 和 `pdt.c`，同 01 §14.1）。每条统计都给了可重跑的命令。

---

## §0 按症状找章节

| 症状 | 去哪一节 |
|---|---|
| 我想先把该开的选项开全 | §1 |
| dmesg 里什么都没打 | §2（答案是**打开 `pr_debug`**） |
| 我想用 ftrace 看 DT 解析 | §3（答案是**没有 tracepoint**） |
| 我要看内核实际解析出的树 | §4 |
| 我要看 bootloader 传的 DTB | §5 |
| 属性/地址/phandle 配错了怎么定位 | §6 |
| 拿到一个错误码，想知道是哪一行返回的 | §7 |
| 给我一页速查 | §8 |

---

## §1 编译期开关

### §1.1 ⚠️`drivers/of/Kconfig` 里零个调试选项

```bash
grep -rn 'DEBUG\|debug' drivers/of/Kconfig     # → 无输出
```

**本子系统的 16 个 Kconfig 项里没有一个是调试用的**（01 §0.5）。
⇒ **可观测性完全靠 `pr_debug` + 动态调试**（§2）。

### §1.2 真正影响可观测性的四个（都在别处）

| 选项 | 位置 | 没开的后果 | 怎么确认 |
|---|---|---|---|
| `CONFIG_DYNAMIC_DEBUG` | —— | **113 处 `pr_debug` 全部编译成空** ⇒ **地址翻译完全不可观测** | `ls /sys/kernel/debug/dynamic_debug/control` |
| `CONFIG_OF_KOBJ` | `drivers/of/Kconfig:52` | **`/sys/firmware/devicetree/` 不存在** ⇒ 看不到解析出的树（§4） | `ls /sys/firmware/devicetree/` |
| `CONFIG_PROC_FS` | —— | `/proc/device-tree` 符号链接没有 | `ls -l /proc/device-tree` |
| `CONFIG_OF_UNITTEST` | `drivers/of/Kconfig:15` | **抓不到 `of_node_get/put` 不平衡**（04 §5.1） | ⚠️ **生产内核别开** |

⚠️ **`CONFIG_OF_KOBJ` 是本子系统最重要的调试选项** ——
arm64 defconfig 通常开着，但**精简配置可能关掉**。

### §1.3 调 DT 问题时该额外开的

| 选项 | 为什么 |
|---|---|
| `CONFIG_DYNAMIC_DEBUG` | §2 的全部前提 |
| `CONFIG_DEBUG_KMEMLEAK` | 抓 `of_node_put()` 缺失导致的泄漏（比 `OF_UNITTEST` 轻） |
| `CONFIG_PROVE_LOCKING` | 抓 `devtree_lock`（raw spinlock）的误用（03 §B1） |
| `CONFIG_DEBUG_ATOMIC_SLEEP` | 抓"持 `devtree_lock` 时睡眠"（03 §B1.3） |

---

## §2 日志：`pr_debug` 是主力

### §2.1 全量普查

| 类别 | 次数 | 说明 |
|---|---|---|
| `pr_debug` | **113** | **本子系统的主力** —— 需要动态调试才可见 |
| `pr_err` | **75** | 但分布**极不均衡**（§2.3） |
| `pr_warn` | 20 | —— |
| `pr_info` | 6 | —— |
| `pr_cont` | 6 | 都在 `of_dump_addr()` 那类多行输出里 |
| `WARN_ON` | 17 | —— |
| `WARN(…)` | 2 | `drivers/of/irq.c:568` · `drivers/of/kobj.c:83` |
| `WARN_ONCE(…)` | 2 | 都在 graph（`drivers/of/property.c:583` · `:672`） |
| `BUG_ON` | **2** | `drivers/of/device.c:39` · `drivers/of/kobj.c:153` |
| `pr_*_once` | 1 | `drivers/of/address.c:170` |
| `_ratelimited` | **0** | —— |

```bash
F="base property address platform device irq fdt dynamic overlay of_reserved_mem resolver kobj fdt_address of_numa kexec"
for k in pr_err pr_warn pr_info pr_debug WARN_ON WARN_ONCE 'WARN(' BUG_ON _ratelimited; do
  t=0; for n in $F; do c=$(grep -oh "$k" drivers/of/$n.c | wc -l); t=$((t+c)); done
  printf "%-14s %s\n" "$k" "$t"
done
```

**`pr_debug` (113) > `pr_err` (75)** —— 这个比例是本子系统的定性：
**它默认几乎不说话**。

### §2.2 `pr_debug` 的分布（决定你该开哪个文件）

| 文件 | `pr_debug` 数 | 什么时候开 |
|---|---|---|
| `fdt.c` | **24** | **早期解析问题**（内存/命令行/reserved-mem 没生效） |
| `address.c` | **18** | **地址翻译失败**（最常用！§6.2） |
| `irq.c` | **18** | 中断解析失败（irq 那套 06 §8） |
| `platform.c` | 11 | 设备没被创建 |
| `overlay.c` | 11 | overlay 打不上 |
| `fdt_address.c` | 10 | 早期地址翻译 |
| `dynamic.c` | 10 | changeset |
| `property.c` | 4 | 属性读取（比想象的少！） |
| `base.c` | **2** | **查找/遍历几乎完全不可观测** |
| `of_reserved_mem.c` · `kexec.c` | 各 2 | —— |
| `of_numa.c` | 1 | —— |

⚠️ **`base.c` 只有 2 处 `pr_debug`** ——
而它是查找、遍历、属性定位、phandle 解析的主体（01 §0.1）。
⇒ **"找不到节点/属性"这类问题几乎没有日志线索**，只能靠 §4 的 sysfs 视图。

### §2.3 `pr_err` 的分布（极不均衡）

| 文件 | `pr_err` 数 | 评价 |
|---|---|---|
| `overlay.c` | **22** | **说得最多** —— overlay 每一步失败都有明确消息 |
| `dynamic.c` | 9 | changeset 也说得清楚 |
| `base.c` | 7 | 其中 **5 条**在 phandle 解析里（§6.3） |
| `fdt.c` · `of_reserved_mem.c` | 各 6 | —— |
| `resolver.c` | 5 | —— |
| `of_numa.c` · `kexec.c` | 各 4 | —— |
| `platform.c` · `fdt_address.c` | 各 3 | —— |
| `address.c` · `property.c` · `irq.c` | **各 2** | **热路径基本沉默** |

**`address.c` 18 个 `pr_debug` 对 2 个 `pr_err`** ——
这就是 03 §C4 说的"可观测性极不均衡"的量化形式。

**合理解释**：属性不存在、地址翻译失败在**正常情况下也会大量发生**
（可选属性、不需要翻译的节点）⇒ 打 `pr_err` 会把 dmesg 刷满。

### §2.4 五条最该认识的消息

| 消息 | 位置 | 含义 |
|---|---|---|
| `Bad cell count for %pOF`（**pr_err**） | `drivers/of/address.c:581` | **`#address-cells`/`#size-cells` 不合法**（06 §2.3） |
| `%pOF: could not find phandle %d` | `drivers/of/base.c:1348` | **phandle 指向的节点不存在** |
| `%pOF: phandle %s needs %d, found %td` | `drivers/of/base.c:1384` | **参数个数和 `#xxx-cells` 不一致**（06 §5.3） |
| `ERROR: memory leak, expected refcount 1 instead of %d, of_node_get()/of_node_put() unbalanced …` | `drivers/of/dynamic.c:471` | **忘了 `of_node_put()`**（01 §1.2） |
| `ERROR: Bad of_node_put() on %pOF` | `drivers/of/dynamic.c:336` | **put 多了** |

另外三条 phandle 相关的（都在 `drivers/of/base.c`）：
`%pOF: could not get %s for %pOF`（`:1363`）·
`%pOF: %s = %d found %td`（`:1380`）。

两条 `of_map_id()` 的（`msi-map` / `iommu-map`，01 §5.4）：
`%pOF: Error: Bad %s length: %d`（`drivers/of/base.c:2149`）·
`%pOF: Invalid %s translation - %s-mask (0x%x) ignores id-base (0x%x)`（`drivers/of/base.c:2173`）。

### §2.5 两处 `BUG_ON`

| 位置 | 条件 | 含义 |
|---|---|---|
| `drivers/of/device.c:39` | `ofdev->dev.of_node == NULL` | **`of_match_device()` 被传了一个没有 `of_node` 的设备** ⇒ 调用者的 bug |
| `drivers/of/kobj.c:153` | `!of_node_is_initialized(np)` | `__of_detach_node_sysfs()` 收到未初始化的节点 |

⚠️ **前者是真实的踩坑点**：驱动如果对非 DT 设备调 `of_match_device()`
⇒ **直接 panic**。
⇒ **这就是 05 §1.4 那条"probe 开头判 `!np`"的必要性**。

### §2.6 打开动态调试

```sh
C=/sys/kernel/debug/dynamic_debug/control

# 地址翻译（最常用，06 §10.3）
echo 'file address.c +p' > $C

# 早期解析（⚠️ 但早期日志可能已经过去了 —— 见下）
echo 'file fdt.c +p' > $C

# 设备为什么没被创建
echo 'file platform.c +p' > $C

# 只开一个函数
echo 'func of_translate_one +p' > $C

# 关掉
echo 'file address.c -p' > $C
```

⚠️ **`fdt.c` 的日志有一个时序问题**：
早期扫描发生在**动态调试可用之前** ⇒ 运行时打开也看不到那些消息。
⇒ **必须用命令行参数**：
```
dyndbg="file fdt.c +p"       # 在内核命令行里加
```
或者 `CONFIG_DYNAMIC_DEBUG_CORE` + 模块参数（本套未实测，99 §5.1）。

**`address.c` 的日志没有这个问题** —— 地址翻译主要发生在设备填充和
驱动 probe 时（`late` 阶段），运行期 unbind/bind 就能复现（06 §10.3）。

---

## §3 tracepoint：一个都没有

```bash
ls include/trace/events/ | grep -iE '^of\.h|devicetree|fdt'     # → 无输出
F="base property address platform device irq fdt dynamic overlay …"
for n in $F; do grep -l 'TRACE_EVENT\|trace_' drivers/of/$n.c; done   # → 无输出
```

**`drivers/of/` 零 tracepoint**。
⚠️`include/trace/events/sof.h` 是 "Sound Open Firmware"，**和本子系统无关**。

### §3.1 用 kprobe 代替

```sh
cd /sys/kernel/tracing

# ① 看某个属性被读了没有、返回什么
echo 'p:fp of_find_property np=%x0:u64 name=+0(%x1):string' > kprobe_events
echo 'r:fpr of_find_property $retval' >> kprobe_events
echo 1 > events/kprobes/fp/enable
echo 1 > events/kprobes/fpr/enable
cat trace_pipe

# ② 看地址翻译的结果
echo 'r:tar of_translate_address $retval' > kprobe_events
echo 1 > events/kprobes/tar/enable
#   返回 0xffffffffffffffff (OF_BAD_ADDR) ⇒ 翻译失败

# ③ 看 phandle 解析
echo 'r:pp __of_parse_phandle_with_args $retval' > kprobe_events
```

**② 是最有价值的一条**：`OF_BAD_ADDR` 就是翻译失败的哨兵值
（01 §14.4、03 §C2）。

⚠️`of_find_property()` 是导出符号（`drivers/of/base.c:223`，导出同处）
⇒ kprobe 一定能挂上。先 `grep of_find_property /proc/kallsyms` 确认。

### §3.2 function_graph 看整条解析链

```sh
cd /sys/kernel/tracing
echo 'of_*' > set_ftrace_filter
echo function_graph > current_tracer
echo 1 > tracing_on
echo <设备> > /sys/bus/platform/drivers/<驱动>/bind
echo 0 > tracing_on
cat trace | head -60
```

能看到 `of_platform_device_create → of_device_alloc →
of_address_to_resource → of_translate_address → of_translate_one` 这条链（03 §A5）。

---

## §4 `/sys/firmware/devicetree/`：最重要的出口

### §4.1 结构

```sh
# 整棵树以目录呈现
ls /sys/firmware/devicetree/base/
# → #address-cells  #size-cells  aliases/  bus@100000/  chosen/  compatible  model  ...

# 常见的符号链接
ls -l /proc/device-tree        # → /sys/firmware/devicetree/base
```

| 元素 | 位置 |
|---|---|
| 建树 | `drivers/of/kobj.c:115` 的 `__of_attach_node_sysfs()` |
| 根节点叫 `"base"` | `drivers/of/kobj.c:128` |
| 其它节点用 `kbasename(full_name)` | `drivers/of/kobj.c:131` |
| 每个属性一个文件 | `drivers/of/kobj.c:63` 的 `__of_add_property_sysfs()` |
| 重名时加 `#N` 后缀 | `drivers/of/kobj.c:40` 的 `safe_name()` |

### §4.2 读属性的三种姿势

```sh
D=/sys/firmware/devicetree/base/bus@100000/serial@2810000

# ① 字符串（NUL 分隔的列表）
cat $D/compatible | tr '\0' '\n'
# → ti,am654-uart
#   ti,am64-uart

# ② 整数（大端！用 hexdump 而不是 cat）
hexdump -C $D/reg
# → 00000000  00 00 00 00 02 81 00 00  00 00 00 00 00 00 01 00
#              ▲───── 地址(2 cells) ──▲  ▲───── 大小(2 cells) ─▲
#   ⇒ 地址 = 0x0000000002810000，大小 = 0x100

# ③ 布尔（存在即真 —— 文件长度为 0）
ls -l $D/dma-coherent 2>/dev/null && echo "有" || echo "没有"

# ④ status
cat $D/status | tr -d '\0'; echo
```

**② 那个 `hexdump` 是本篇最实用的一条命令** ——
它让你**直接看到内核眼里的 `reg` 是什么**（06 §2.3 的判据）。

⚠️ **不要用 `cat` 看整数属性** —— 那是二进制。

### §4.3 五条诊断用法

| 想确认 | 命令 |
|---|---|
| 节点在树里吗 | `ls /sys/firmware/devicetree/base/<路径>/` |
| 属性名写对了吗 | `ls /sys/firmware/devicetree/base/<路径>/` ⇒ **直接看到所有属性名** |
| `status` 生效了吗 | `cat .../status \| tr -d '\0'` ⇒ 必须是 `okay` 或 `ok`（06 §4.1） |
| `#address-cells` 是多少 | `hexdump -C <父目录>/'#address-cells'` |
| 有重名节点吗 | `ls` 里出现 `foo` 和 `foo#1` ⇒ **DTS 里两个同名同 unit-address 的节点**（04 §4.2） |

**第二条最重要**：`ls` 一次就能看出"属性名是不是拼错了" ——
比在源码里 grep 快得多（06 §10.4 第 3 条）。

### §4.4 ⚠️ 它和 DTB 是两份数据

`/sys/firmware/devicetree/` 反映的是**内存里的 `device_node` 树**（03 §A3 末段）。

| 情形 | sysfs 视图 | DTB 文件 |
|---|---|---|
| 正常启动 | 一致 | 一致 |
| 打了 overlay | **反映最新状态** | **不变** |
| bootloader 改过 DTB（如填 MAC 地址） | **反映改后的** | 取决于你看的是哪份 |

⇒ **要看"内核到底看到了什么"，一律看 sysfs，不要看 `.dtb` 文件**。

---

## §5 看 bootloader 传的 DTB

### §5.1 三种方式

```bash
# ① 反编译内核树里的 DTB（编译产物）
dtc -I dtb -O dts -o /tmp/x.dts arch/arm64/boot/dts/ti/k3-am654-base-board.dtb

# ② 从运行中的系统"反向导出"（用 sysfs 视图，不是原 DTB）
dtc -I fs -O dts /sys/firmware/devicetree/base > /tmp/live.dts

# ③ 从 /proc/device-tree（同 ②）
dtc -I fs -O dts /proc/device-tree
```

**② 是最有用的**：它把整棵树导成一份 `.dts`，
可以和 ① 的输出 `diff` ⇒ **一眼看出 bootloader 或 overlay 改了什么**。

### §5.2 `dtc -I dtb` 的价值：宏都展开了

```dts
	/* 你写的 */
	interrupts = <GIC_SPI 25 IRQ_TYPE_LEVEL_HIGH>;
	clocks = <&clkctrl 5>;

	/* dtc -I dtb -O dts 输出的 */
	interrupts = <0x00 0x19 0x04>;
	clocks = <0x1f 0x05>;              /* 0x1f 是 clkctrl 的 phandle 值 */
```

**两个用途**：
① 确认宏展开成了你以为的数（06 §10.1）；
② **确认 phandle 指向了正确的节点** ——
拿那个值去 `grep 'phandle = <0x1f>'` 找目标节点（06 §10.4 第 4 条）。

### §5.3 ⚠️ `dtc` 从哪来

| 来源 | 说明 |
|---|---|
| 内核树自带 | `scripts/dtc/dtc`（编译内核时会生成） |
| 发行版包 | `device-tree-compiler` |

版本差异通常不影响 `-I dtb -O dts`（反编译）。

---

## §6 三类配置问题的定位

### §6.1 属性读不到

```sh
# 第一步：属性到底在不在树里、名字对不对
ls /sys/firmware/devicetree/base/bus@100000/serial@2810000/
# ⇒ 一眼看出拼没拼错

# 第二步：值对不对
hexdump -C .../vendor,fifo-threshold

# 第三步（属性在但驱动没用）：驱动侧的问题，不是 DT 的
```

**90% 的"属性读不到"是拼错或写错层级**（06 §10.4）——
`ls` 一次就能确认。

### §6.2 地址翻译失败（本篇最重要的流程）

```sh
# ① 打开 pr_debug
echo 'file address.c +p' > /sys/kernel/debug/dynamic_debug/control

# ② 重新触发一次 probe
echo 2810000.serial > /sys/bus/platform/drivers/8250_omap/unbind
echo 2810000.serial > /sys/bus/platform/drivers/8250_omap/bind

# ③ 看日志
dmesg | tail -40
```

**三种失败的判据**（03 §B4.2、06 §11）：

| 日志 | 位置 | 原因 |
|---|---|---|
| `no ranges; cannot translate` | `drivers/of/address.c:464` | **父节点缺 `ranges`** |
| `not found !` | `drivers/of/address.c:486` | **地址不在任何窗口里** |
| `Bad cell count for %pOF`（pr_err，不用开 debug） | `drivers/of/address.c:581` | **cells 数不合法** |
| `empty ranges; 1:1 translation` | `drivers/of/address.c:471` | 正常 |

成功时会打 `with offset: %llx`（`drivers/of/address.c:493`）+ 两次 `of_dump_addr()`
（`drivers/of/address.c:546` · `:597`）。

**配套确认**：`grep -i <设备名> /proc/iomem` —— 在里面说明翻译成功了（06 §10.3）。

### §6.3 phandle 解析失败

**好消息：这一类有明确的 `pr_err`**（不用开动态调试）：

| 日志 | 位置 | 原因 |
|---|---|---|
| `%pOF: could not find phandle %d` | `drivers/of/base.c:1348` | **phandle 值指向的节点不存在**（DTS 里 `&foo` 的 foo 拼错？） |
| `%pOF: phandle %s needs %d, found %td` | `drivers/of/base.c:1384` | **参数个数不对**（06 §5.3） |
| `%pOF: could not get %s for %pOF` | `drivers/of/base.c:1363` | 提供者节点缺 `#xxx-cells` |
| `%pOF: %s = %d found %td` | `drivers/of/base.c:1380` | 列表长度不对 |

**这四条是本子系统"说得最清楚"的一组** ——
和 §2.3 里 `base.c` 只有 7 条 `pr_err` 对应，其中 **5 条**都在这里。

### §6.4 设备没被创建

```sh
# ① 节点在树里吗、status 对吗（§4.3）
ls /sys/firmware/devicetree/base/<路径>/
cat /sys/firmware/devicetree/base/<路径>/status | tr -d '\0'; echo

# ② platform_device 建出来了吗
ls /sys/bus/platform/devices/ | grep <地址>

# ③ 父节点在填充白名单里吗（06 §7.1）
cat /sys/firmware/devicetree/base/<父路径>/compatible | tr '\0' '\n'
#   ⇒ 必须含 simple-bus / simple-mfd / isa / arm,amba-bus 之一

# ④ 打开 platform.c 的 pr_debug
echo 'file platform.c +p' > /sys/kernel/debug/dynamic_debug/control
```

**③ 是最容易被忽略的一步**（06 §7.1 条件 ③）。

---

## §7 错误码分诊

### §7.1 全量普查（01 §14.1 的原始数据）

| 错误码 | 次数 | 主要含义 |
|---|---|---|
| `-EINVAL` | **106**（46%） | **属性不存在** / 参数不合法 / 格式不对（⚠️ 三者同码） |
| `-ENOMEM` | 27 | 分配失败 |
| `-ENOENT` | **24** | ⚠️ **phandle 列表家族专用**，不是属性读取的码（01 §14.2.1） |
| `-ENODEV` | 21 | 找不到设备 |
| `-ENODATA` | **19** | **属性存在但没值** |
| `-EOVERFLOW` | 10 | **元素个数/长度不够** |
| `-EPROBE_DEFER` | 5 | **依赖还没就绪** |
| `-EBUSY` | 5 | —— |
| `-EILSEQ` | 4 | 字符串属性不是合法 NUL 结尾 |
| 其余六个 | 各 1–2 | `-ENOSYS` · `-EFAULT` · `-ENXIO` · `-EEXIST` · `-EALREADY` |

（可重跑命令见 01 §14.1）

### §7.2 四个"属性专用"错误码的区分（01 §14.2 的调试视角）

| 错误码 | 怎么确认 |
|---|---|
| ⚠️`-EINVAL` | 先 `ls /sys/firmware/devicetree/base/<路径>/` ⇒ **属性名不在里面**（最常见）；排除后再怀疑 `np` 为 NULL ⇒ **加一句 `if (!np) return -ENODEV;`**（05 §1.4） |
| `-EILSEQ` | 字符串属性不是 NUL 结尾 ⇒ `hexdump -C` 看末字节是不是 `00` |
| `-ENODATA` | 属性在，但 `ls -l` 显示**长度 0** ⇒ DTS 写成了 `prop;` |
| `-EOVERFLOW` | `hexdump -C <属性>` 数一下有几个 u32 ⇒ **和代码要读的个数对不上** |

**这四步全部只靠 `ls` 和 `hexdump`** —— 不需要改内核、不需要开动态调试。

### §7.3 ⚠️ 一大类 API 不返回错误码

03 §C2 记录的设计债，调试视角：

| API | 失败时 | 怎么拿到真正的原因 |
|---|---|---|
| `of_find_*()` | NULL | **只能靠 §4.3 的 `ls`** |
| `of_get_property()` | NULL | 同上 |
| `of_parse_phandle()` | NULL | **换成 `of_parse_phandle_with_args()`** ⇒ 拿到错误码 + §6.3 的 `pr_err` |
| `of_iomap()` | NULL | **换成 `of_address_to_resource()` + `ioremap()`** ⇒ 分开看哪一步失败 |
| `of_translate_address()` | `OF_BAD_ADDR` | 至少有哨兵值；配合 §6.2 |

⇒ **调试时的通用招数：把返回 NULL 的 API 换成返回错误码的等价 API**。

### §7.4 高频错误 → 第一嫌疑

| 拿到 | 先查 |
|---|---|
| `-EINVAL` | `ls /sys/firmware/devicetree/base/<路径>/` 看属性名在不在 |
| `-ENOENT` | **是 phandle 列表**（`clocks` / `widgets` 之类）⇒ 数一下条目数、看 index 有没有越界 |
| `-EOVERFLOW` | `hexdump` 数元素个数 |
| `-ENODATA` | 属性写成了无值形式 |
| `-EPROBE_DEFER` | **正常** —— 原样返回；一直不好 ⇒ 提供者驱动没编进内核 |
| `-EINVAL`（属性读取） | `np` 为 NULL |
| `-EINVAL`（`of_parse_phandle_with_args`） | 参数个数和 `#xxx-cells` 不一致（§6.3） |
| NULL（`of_find_*` / `of_parse_phandle`） | 换成返回错误码的 API（§7.3） |
| `OF_BAD_ADDR` | §6.2 |
| **panic 在 `of_match_device()`** | `BUG_ON(of_node == NULL)`（`drivers/of/device.c:39`）⇒ 对非 DT 设备调用了它 |

---

## §8 一页速查

```
┌─ 三步定位（按顺序做）───────────────────────────────────┐
│ ① ls /sys/firmware/devicetree/base/<路径>/                    │
│      ⇒ 节点在不在、属性名对不对（90% 的问题在这一步解决）  │
│ ② cat .../status | tr -d '\0'                                 │
│      ⇒ 必须是 okay 或 ok（拼错就等于 disabled）           │
│ ③ ls /sys/bus/platform/devices/ | grep <地址>                 │
│      ⇒ 没有 ⇒ 检查父节点 compatible 是否在填充白名单里         │
└──────────────────────────────────────────────────────────────┘

┌─ 读属性 ─────────────────────────────────────────────────────┐
│ 字符串：cat  <属性> | tr '\0' '\n'                            │
│ 整数：  hexdump -C <属性>        不要用 cat（大端二进制）   │
│ 布尔：  ls -l <属性>             长度 0 = 存在即真            │
└──────────────────────────────────────────────────────────────┘

┌─ 地址翻译失败 ──────────────────────────────────────────┐
│ echo 'file address.c +p' > /sys/kernel/debug/dynamic_debug/control │
│ unbind / bind 你的设备 ; dmesg | tail -40                     │
│   "no ranges; cannot translate" ⇒ 父节点缺 ranges             │
│   "not found !"                 ⇒ 地址不在任何窗口里           │
│   "Bad cell count for …"(pr_err)⇒ cells 数不合法              │
│ 配套：grep -i <设备> /proc/iomem                              │
└──────────────────────────────────────────────────────────────┘

┌─ phandle 失败（有 pr_err，不用开 debug）───────────────┐
│ "could not find phandle %d"      ⇒ 目标节点不存在              │
│ "phandle %s needs %d, found %td" ⇒ 参数个数和 #xxx-cells 不符  │
└──────────────────────────────────────────────────────────────┘

┌─ 导出整棵树对比 ─────────────────────────────────────────────┐
│ dtc -I fs -O dts /sys/firmware/devicetree/base > /tmp/live.dts │
│ dtc -I dtb -O dts -o /tmp/src.dts <你的>.dtb                   │
│ diff /tmp/src.dts /tmp/live.dts   看 bootloader 改了什么   │
└──────────────────────────────────────────────────────────────┘

┌─ 没有 tracepoint，用 kprobe ─────────────────────────────────┐
│ echo 'r:tar of_translate_address $retval' > kprobe_events      │
│   返回 0xffffffffffffffff (OF_BAD_ADDR) ⇒ 翻译失败             │
└──────────────────────────────────────────────────────────────┘
```

### §8.1 错误码 → 第一嫌疑（压缩版）

| 拿到 | 先查 |
|---|---|
| `-EINVAL` | `ls` sysfs 目录看属性名 |
| `-ENOENT` | phandle 列表的条目数 / index |
| `-EOVERFLOW` | `hexdump` 数元素个数 |
| `-ENODATA` | 属性写成无值形式了 |
| `-EPROBE_DEFER` | 正常，原样返回 |
| NULL | 换成返回错误码的 API |
| panic 在 `of_match_device()` | 对非 DT 设备调用了它 |

### §8.2 这个子系统调试的三条纪律

| 纪律 | 为什么 |
|---|---|
| **先看 `/sys/firmware/devicetree/`，别先读 DTS 源码** | sysfs 反映**内核实际看到的**（§4.4） |
| **整数属性用 `hexdump` 不用 `cat`** | 大端二进制（§4.2） |
| **地址问题一律先开 `file address.c +p`** | 那 18 处 `pr_debug` 是唯一线索（§2.2） |

---

**下一篇**：08 篇用大白话和类比解释这个子系统里最难理解的几组关系。
