---
title: 取证附录
---

# regmap 99：证据、方法与未验证项

> **这一篇决定了前九篇里哪些结论可以直接用、哪些要自己再确认一遍。**
> **本篇所有数字都能用给出的命令重跑**（§6），**每轮修改后全部重跑过**（§7）。

---

## §1 基线与类型判定

### §1.1 基线（全部可重跑）

| 项 | 值 | 命令 |
|---|---|---|
| **内核版本** | **Linux 6.1.158** | `git log -1 --format='%H %s'` |
| **commit** | `f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe` | 同上 |
| ⚠️ **树是否打过补丁** | **打过**（`git status` 有大量修改文件，含 arm64 / i915 / 网卡等） | `git status --porcelain \| wc -l` |
| **本子系统是否被补丁改动** | **没有，输出为 0** | `git status --porcelain -- drivers/base/regmap include/linux/regmap.h drivers/mfd/syscon.c \| wc -l` |
| **体系结构范围** | **arm64-only**，ACPI 一律裁掉（00 §2.7） | — |

⇒ **结论：本套文档描述的是未经本树补丁修改的原生 6.1.158 regmap 代码。**
**这一条是整份文档定性的前提**，**如果它不成立，后面所有行号都要重新对。**

### §1.2 子系统类型判定（两道门，实际输出）

| 门 | 命令 | 实际输出 |
|---|---|---|
| **门 1：有没有 `bus_type`** | `grep -c 'struct bus_type' drivers/base/regmap/*.c include/linux/regmap.h` | **全部为 0** |
| **门 2：ABI 在不在 uapi** | `ls include/uapi/linux/regmap* \| wc -l` | **0** |

⇒ **门 1 无输出 ⇒ 框架型**（不需要跑门 2，但仍跑了以留档）。
**对照**：i2c / spi / virtio 门 1 都有输出，virtio 门 2 也有（27 个规范头文件）。

**类型判定对模板的实际影响**（记录下来避免下次重新推导）：

| 篇 | 因为是框架型而改成 |
|---|---|
| 00 §2 | **走路径 B（问题背景）**，不是硬件背景，也不是规范背景 |
| 02 | **画"核心抽象 ↔ 消费者 ↔ 三个可插拔层"，不画"本子系统总线 ↔ 父总线"** |
| 03 | **主轴是"初始化 / 读写 / 电源管理"**，不是"传输" |
| 04 | **有子框架**（缓存 / 分页 / regmap-irq / debugfs）⇒ **没有并入 03** |
| 06 | ⚠️ **skill 模板说"regmap 没有 DT 绑定"——这句话是错的**，见 §1.3 |
| 99 §5 | **不需要第五层「对端不在本树」**（那是虚拟总线型才有的） |

### §1.3 ⚠️ 官方文档登记（含一个 skill 模板的错误）

**概念文档：真的没有。四种路径全试过，全为空**：

| 试过的路径 | 结果 |
|---|---|
| `Documentation/regmap/` | **不存在** |
| `Documentation/driver-api/regmap*` | **不存在**（`ls Documentation/driver-api/ \| grep -i 'regmap\|register'` 无输出） |
| `find Documentation -iname '*regmap*'` | **只命中 DT 绑定目录和文件** |
| `Documentation/driver-api/` 里的近似名 | **无**（手工逐个看过，没有 pinctrl 那种"主文档改名"的情况） |

⚠️ **但 DT 绑定存在**：

| 文件 | 行数 | 内容 | 被吸收进 |
|---|---|---|---|
| `Documentation/devicetree/bindings/regmap/regmap.txt` | **29** | **`little-endian` / `big-endian` / `native-endian` 三个布尔属性 + 一段 Note 讲默认值和 `native-endian` 的用途** | **06 §0 / §2 / §3.3 / §7** |
| `Documentation/devicetree/bindings/mfd/syscon.yaml` | — | **`reg-io-width`（`:83-87`，enum `[1,2,4,8]`）· `hwlocks`（`:89`）** | **06 §1 / §3.2 / §3.4** |

⇒ **skill 骨架里"如果子系统没有 DT 绑定（如 regmap）"这句话不成立，06 篇有真实内容。**
**已在 §10 登记为要回写 skill 的一条。**

**文档与代码冲突的地方（三等证据的风险，以代码为准）**：

| 文档说 | 代码是 | 处置 |
|---|---|---|
| **"Regmap defaults to little-endian register access on MMIO based devices"** | **核心兜底是 `REGMAP_ENDIAN_BIG`**（`drivers/base/regmap/regmap.c:725`）；**小端是 MMIO 总线在第三级改的**（`regmap-mmio.c:428`） | **06 §2.2 显式讲了这两层**，不直接复述文档那句话 |
| **`reg-io-width` enum 允许 `8`** | **`regmap-mmio.c:43-63` 无 `case 64` ⇒ 必然 `-EINVAL`** | **06 §3.4 单独一节点明**；**全树 0 处使用，反向印证** |

### §1.4 ⚠️`check_doc_coverage.sh` 报了一个假通过

**脚本输出原话是"【必查】共 0 篇，必查档全部有引用"。**
⚠️ **这不是通过，是"它一篇官方文档都没找到"**（skill 规则 28 的 条）。
**所以本节的结论完全来自手工核对**（上表四条路径），**没有依赖这个脚本兜底。**

---

## §2 代码阅读范围与证据等级

### §2.1 一等证据：逐行读过

**判据：从函数开头读到结尾，引用时把源码原样搬进文档。**

| 文件 | 行数 | 详读范围 |
|---|---|---|
| `include/linux/regmap.h` | **1977** | **全文**（54 字段的 `regmap_config`、18 字段的 `regmap_bus`、全部宏、`CONFIG_REGMAP=n` 的空桩段） |
| `drivers/base/regmap/regmap.c` | 3529 | **`:27-42`（LOG_DEVICE）· `:60-102`（可访问性判断）· `:653-727`（endian 两个函数 + attach）· `:729-1273`（`__regmap_init` 全流程）· `:1630-1676`（分页）· `:1780-1800`（异步提交）· `:1990-2015`（写路径）· `:2900-2941`（读路径）· `:3243-3275`（update_bits）· `:3330-3361`（异步完成）** |
| `drivers/base/regmap/regcache.c` | 827 | **`:350-432`（`regcache_sync`）· `:446-498`（`sync_region`）· `:504-545`（`drop_region` + `cache_only`）· `:548-588`（`mark_dirty` + `cache_bypass`）** |
| `drivers/base/regmap/regmap-debugfs.c` | 692 | **`:290-345`（写寄存器 + 开关 2）· `:455-545`（两个可写文件的 setter）· `:560-690`（文件建立 + 目录命名 + 根目录）** |
| `drivers/base/regmap/regmap-mmio.c` | 636 | **`:31-63`（两个位宽校验）· `:180-240` `:360-400`（64 位分支）· `:420-470`（初始化校验）** |
| `drivers/base/regmap/regmap-i2c.c` | 400 | **`:14-49`（最简 `regmap_bus` 实现，作 05 §4.1 的范本）· `:310-340`（bus 变体选择 + endian）** |
| `drivers/base/regmap/trace.h` | 284 | **全文**（六个类 + 两个独立事件的字段与 `TP_printk`） |
| `drivers/base/regmap/internal.h` | 310 | **`:179-191`（`regcache_ops`）· `:279-296`（`regmap_get_val_endian` 声明 + `regmap_name`）· `struct regmap` 全体** |
| `drivers/base/regmap/Kconfig` | — | **全文**（16 个符号） |
| `drivers/mfd/syscon.c` | **378** | **`:39-135`（`of_syscon_register` 全流程）· `:221-330`（7 个导出符号）· `:340-355`（platform driver 路径）** |

### §2.2 二等证据：只读了符号表 / 结构定义

**判据：知道它有什么、返回什么，但没有逐行读实现。**

| 文件 | 行数 | 读到什么程度 |
|---|---|---|
| `drivers/base/regmap/regmap-irq.c` | **1319** | **11 个导出符号的签名 + `regmap_irq_chip` 40 字段 + 全部 `dev_warn`/`dev_err` 字符串 + 1 个 `BUG_ON`**；⚠️ **中断分发主循环没读**（00 §2.7 已声明裁掉） |
| `drivers/base/regmap/regcache-rbtree.c` | 554 | **只读了 `regcache_ops` 实例的字段赋值**，算法本体没读 |
| `drivers/base/regmap/regcache-lzo.c` | 368 | 同上 |
| `drivers/base/regmap/regmap-spi-avmm.c` | 713 | **只读了 2 个导出符号和 2 条 `dev_err`**（00 §2.7 裁掉协议细节） |
| `drivers/base/regmap/regmap-spmi.c` | — | **只读了 6 个 `BUG_ON` 的条件**（07 §4.4 用到） |
| **其余 10 个总线适配** | — | **只统计了导出符号数和 `regmap_bus` 实例数**（04 §6） |

### §2.3 三等证据：官方文档

**读了哪些、哪些行，见 §1.3 的表。**
⚠️ **两处文档与代码冲突，均以代码为准并在 06 篇显式指出。**

### §2.4 代表驱动的选法

**消费者散得很开（186 个文件用 syscon 家族，几百个自建 regmap），不可能全读。选法**：

| 挑的 | 为什么 |
|---|---|
| `drivers/base/regmap/regmap-i2c.c` 的 SMBus 部分 | **最简 `regmap_bus`（只两个回调）** ⇒ 说明"最小可用集"（05 §4.1） |
| `drivers/base/regmap/regmap-mmio.c` | **arm64 上最常用，且有完整位宽校验** |
| `drivers/mfd/syscon.c` | **arm64 DT 路径的唯一入口**（99 个 DTS 文件 + 186 个消费者文件） |
| `drivers/mfd/rk808.c` | **arm64 Rockchip 板实际在用的 regmap-irq 消费者**（05 §2.1） |
| `drivers/pinctrl/pinctrl-rockchip.c` / `pinctrl-ocelot.c` | **自建 MMIO regmap 的典型形态**（05 §1） |
| **arm64 DTS**：`rockchip/rk3399.dtsi` · `freescale/fsl-ls1043a.dtsi` · `realtek/rtd16xx.dtsi` | **分别覆盖"普通 syscon / 大端 syscon / 显式 reg-io-width"三种形态**（06 §3.3） |

**其余驱动只讲契约，不讲实现。**

### §2.5 裁掉的范围登记（arm64-only 与 ACPI）

⚠️ **这些不是遗漏，是显式裁掉的**（00 §2.7 有面向读者的版本，本节是取证版）：

| 裁掉的 | 判据 | 代码上是否有独立逻辑 |
|---|---|---|
| **ACPI 固件节点** | **arm64-only 约定** | ⚠️ **没有** ——**`fwnode_property_read_bool()`（`drivers/base/regmap/regmap.c:705-710`）同一行代码同时服务 DT 和 ACPI** ⇒ **裁掉 ACPI 不影响任何代码路径的描述，只影响"属性从哪来"的表述** |
| **x86 / powerpc / arm32 专有路径** | 同上 | **regmap 核心里没有任何 `#ifdef CONFIG_X86` 之类的架构分支**（重跑 `grep -rn 'CONFIG_X86\|CONFIG_PPC\|CONFIG_ARM\b' drivers/base/regmap/` 无输出）⇒ **本子系统几乎与架构无关**，裁剪代价极低 |
| **`CONFIG_64BIT` 的 64 位 MMIO 分支** | **arm64 上 `CONFIG_64BIT=y`，所以这些代码"编进去了"** | ⚠️ **但初始化拦死了 `val_bits=64`**（`regmap-mmio.c:43-63`）⇒ **在本版本上到不了**（§5.3 已登记为推断） |
| **PREEMPT_RT 的 `use_raw_spinlock` 实测** | ⚠️ **本树改过抢占相关头文件**（`git status --porcelain -- arch/arm64/include/asm/preempt.h include/linux/preempt.h` **两个都是 `M`**）⇒ **本树很可能带抢占/RT 类补丁**（这是推断，没有查补丁内容）；**而本文档未在任何 RT 配置下运行** | **代码路径读过（`regmap.c:791-796`），行为未验证**（§5.3）；⚠️ **注意：regmap 自己的文件没被改（§1.1），但它依赖的 spinlock 语义可能被改了** |

---

## §3 符号存在性核查

**判据两条，必须同时满足才算"读者能用"**（skill 规则 20）：
**① `EXPORT_SYMBOL*`（或是 `static inline` / 宏）；② 公共头 `include/linux/regmap.h` 里有声明。**

### §3.1 核查过的符号（05 篇正文里每一个都跑过）

| 类别 | 结论 |
|---|---|
| **94 个导出符号** | **逐文件核对过分布**（§6 表 3） |
| **初始化宏** | **15 个 `regmap_init*` + 16 个 `devm_regmap_init*`**（含各 1 个通用版），**对应 14 + 15 个 `__` 前缀底层实体**（口径见 §6 表 5） |
| **`regmap_field` 家族** | **全部存在**：`regmap_field_alloc`(`:1300`) · `devm_regmap_field_alloc`(`:1304`) · `regmap_field_bulk_alloc`(`:1308`) · `devm_regmap_field_bulk_alloc`(`:1313`) · `regmap_field_read`(`:1320`) · `regmap_fields_read`(`:1324`) · `regmap_fields_write`(`:1374`) |
| **宏** | **`REG_FIELD`(`:1286`) · `REG_FIELD_ID`(`:1292`) · `REGMAP_IRQ_REG`(`:1437`) · `REGMAP_IRQ_REG_LINE`(`:1440`) · `REGMAP_IRQ_MAIN_REG_OFFSET`(`:1446`) · `regmap_read_poll_timeout`(`:104`) · `regmap_read_poll_timeout_atomic`(`:134`) · `regmap_field_read_poll_timeout`(`:175`)** |
| **`regcache_*` 六个导出** | `sync`(`regcache.c:350`) · `sync_region`(`:446`) · `drop_region`(`:504`) · `cache_only`(`:536`) · `mark_dirty`(`:560`) · `cache_bypass`(`:580`) |
| **regmap-irq 11 个导出** | 04 §1.3/§1.4 逐个列过 |
| **syscon 7 个导出** | 06 §3.1 逐个列过 |
| **内核打印的字符串** | **07 §4.3 的 6 条弃用警告、§2.3 的 5 条 debugfs 告警、06 §6 的 8 条错误消息，全部逐字 grep 过** |

### §3.2 ⚠️ 抓到的两个"存在性"问题

| 问题 | 详情 | 处置 |
|---|---|---|
| **`REGCACHE_MAPLE` 是幻觉** | **它是更高版本才有的枚举值**；**本树 `grep -rn 'REGCACHE_MAPLE' include/ drivers/base/regmap/` 命中 0** | **05 §6.1 改成 `REGCACHE_RBTREE`，并加了一条显式警告**（§9 实例 1） |
| ⚠️ **`regmap_get_val_endian` 导出但不可用** | **有 `EXPORT_SYMBOL_GPL`（`drivers/base/regmap/regmap.c:727`），但声明只在 `drivers/base/regmap/internal.h:279`，公共头里没有** | **06 §5.2 单独点明**；**这是"存在 ≠ 能用"最干净的一个例子** |

### §3.3 `grep` 的 tab 陷阱（做过的规避）

**`#define` 与宏名之间是 TAB 还是空格不确定** ⇒
**统一用 `grep -nE 'define[[:space:]]+<名字>'` 或 `grep -nE '^#define <名字>'`**，
**不用 `grep 'define <名字>'`**（会漏）。

---

## §4 索引工具（cbm）的盲区

**本套文档全程只用 `rg` + `Read` 取证，索引工具只用于找候选。**
**regmap 恰好把两大盲区都占满了**：

| 盲区 | 在 regmap 里的具体表现 |
|---|---|
| **宏生成的符号** | **31 个初始化入口全是 `#define`**（`include/linux/regmap.h:730-1147`）⇒ **`devm_regmap_init_mmio` 在任何 `.c` 里都没有定义** |
| **函数指针间接调用** | **三层可插拔全靠函数指针**：`map->lock()` / `map->reg_read()` / `map->cache_ops->sync()` ⇒ **调用图在这三处全断** |
| **`static inline` 包装** | **`regmap_update_bits` 等四个包装都是 inline**（`:1184-1220`）⇒ **"谁调用了 `regmap_update_bits_base`"数不准**（05 §7.2 用"包装函数名"而不是"调用点数"表述） |
| **`#ifdef CONFIG_REGMAP` 的双份声明** | **同一个函数名在头文件里出现两次**（真声明 + 空桩）⇒ **grep 行号必须区分 `:1160` 段和 `:1700+` 段**（05 §7 的表全部取的是真声明段） |

---

## §5 ⚠️ 未验证项（四层）

**本节按"为什么不能确定"分层，读者据此决定在哪里加倍小心。**

### §5.1 第一层：未编译、未运行（覆盖面最大）

| 项 | 说明 |
|---|---|
| **05 篇全部代码骨架** | **一行都没编译过。**符号存在性核对过（§3），**但字段初始化的完整性、错误路径的资源回收、Kconfig 依赖都没验证** |
| **07 篇全部 `cat` / `echo` 命令** | **没有在真机上跑过。**文件名和 mode 取自 `debugfs_create_*` 的调用点（`regmap-debugfs.c:606-646`），**但没有观察实际输出** |
| **07 篇不给任何示例输出** | **刻意的**：**编造一段 `cat registers` 的输出比不给更糟**（07 §2.2 / §3.6 都写明了这一点） |
| **06 篇的 DTS 片段** | **全部逐字取自 in-tree 文件并给了行号**，**但没有实际编译 DTB、没有上板启动** |

### §5.2 第二层：只读了符号表，没读实现

**完整清单见 §2.2。最要紧的三处**：

| 项 | 后果 |
|---|---|
| **regmap-irq 的中断分发主循环（1319 行里的大部分）** | **04 §1 讲的是"怎么填表"，不是"分发怎么工作"** ⇒ **调试实际中断丢失问题时本文档帮不上忙** |
| **三个缓存后端的算法本体** | **04 §2.2 的"怎么选"是按数据结构常识 + `regcache_ops` 字段推的**，**没有读 rbtree/lzo 的插入查找逻辑** |
| **10 个总线适配的实现** | **04 §6 只有导出符号数和行数，没有行为描述** |

### §5.3 ⚠️ 第三层：推断而非观察（本节最需要读者警惕）

| 论断 | 为什么是推断 | 风险 |
|---|---|---|
| **"`regmap-mmio.c:205-213` / `:230-234` 的 64 位分支是本版本到不了的代码"** | **推理链**：`regmap_mmio_get_min_stride()` 无 `case 64` ⇒ `val_bits=64` 在 `:447-449` 被拒 ⇒ `ctx->val_bytes` 永远不为 8。**没有实测，也没有排查"是否存在绕过初始化直接设 `val_bytes` 的路径"** | **中**（06 §3.4 已标注） |
| **"`BUG_ON(!map->cache_ops)` 那 5 处驱动碰不到"** | **按"`cache_type != NONE` 就必有 `cache_ops`"推的**，**没有穷举所有能进入这些函数的路径** | **低**（07 §4.4 表述为"核心内部一致性检查"，不是"绝不可能"） |
| **"`use_raw_spinlock` 在 PREEMPT_RT 上才有实质差别"** | **来自 RT 的一般知识，不是本树验证** ⇒ **本文档没有在 RT 内核上跑过任何 regmap 路径** | **中**（00 §2.7 / 08 §9 已声明） |
| **"I2C 上读一个寄存器要几十到几百微秒"**（00 §2.2） | **常识性数量级，不是本树测量** | **低**（只用来说明"为什么要缓存"，不承载任何代码结论） |
| **"同一设备多个 map 在 tracepoint 里无法区分"** | **由 `regmap_name()`（`internal.h:287-293`）的实现推出**，**没有实际制造这种场景观察过** | **低**（推理链只有一步，且代码就三行） |
| **"regmap-irq 正在改接口"** | **由 6 条 `deprecated` 字符串推的**，**没有查上游 git 历史或邮件列表** | **低**（07 §4.3 的 6 条字符串本身是一等证据） |

### §5.4 第四层：版本时效性

| 项 | 说明 |
|---|---|
| **所有行号只对 `6.1.158` 有效** | **`regmap.c` 3529 行，任何 backport 都会整体位移** |
| **`REGCACHE_MAPLE` 是最明确的一处版本差异** | **更高版本引入了它，6.1 没有**（§3.2 / 05 §6.1） |
| **`regmap-irq` 的 6 条弃用警告说明这块正在变** | **抄更高版本的 `regmap_irq_chip` 代码到 6.1 上大概率编不过**（04 §1.2 组 3） |
| **`drivers/mfd/syscon.c:57` 用了 `__free(kfree)` 清理宏** | **这是 6.1.y 后期 backport 进来的写法**；**文件本身未被本树补丁修改（§1.1 已验证）**，**但说明 6.1.158 的 syscon 与早期 6.1.x 不同** |

⚠️ **本子系统不需要"第五层：对端 / 树外实现"** ——
**regmap 是框架型，没有跨边界 ABI、没有树外对端**（§1.2）。
**唯一"树外"的东西是芯片手册**，**而本文档没有引用任何具体芯片的手册**。

---

## §6 可重跑的数字（每轮改完全部重跑）

⚠️ **口径警告先行**：**每个数字后面的"口径"一列比数字本身更重要。**
**换一个口径同一件事能得出完全不同的数**（本篇 §9 有三个真实例子）。

### 表 1 规模

| 项 | 值 | 命令 | 口径 |
|---|---|---|---|
| 文件数 | **22** | `ls drivers/base/regmap/*.c drivers/base/regmap/*.h \| wc -l` | **含 `.h`；不含 `Kconfig`/`Makefile`** |
| 总行数 | **10998** | `cat drivers/base/regmap/*.c drivers/base/regmap/*.h \| wc -l` | 同上 |
| 公共头行数 | **1977** | `wc -l < include/linux/regmap.h` | — |
| 主体文件 | `regmap.c` 3529（占 32%）· `regmap-irq.c` 1319 · `regcache.c` 827 · `regmap-spi-avmm.c` 713 · `regmap-debugfs.c` 692 · `regmap-mmio.c` 636 · `regcache-rbtree.c` 554 · `regmap-i2c.c` 400 · `regcache-lzo.c` 368 · `internal.h` 310 · `trace.h` 284 | `wc -l drivers/base/regmap/*` | — |

### 表 2 `regmap_config` / `regmap_bus` 字段数

| 项 | 值 | 命令 | 口径 |
|---|---|---|---|
| `regmap_config` 字段 | **54** | `awk '/^struct regmap_config \{/{f=1} f&&/^\};/{exit} f' include/linux/regmap.h \| grep -c ';'` | ⚠️ **按分号计** ⇒ 多行函数指针声明只算一次（本结构里没有跨行声明，逐行看过） |
| `regmap_bus` 字段 | **18** | `awk 'NR>=545 && NR<=562' include/linux/regmap.h \| grep -c ';'` | **`:545-562` 是字段范围**；`:544` 是 `{`、`:563` 是 `};` |

### 表 3 导出符号（94）

| 文件 | 数 |
|---|---|
| `regmap.c` | **47** |
| `regmap-irq.c` | **11** |
| `regcache.c` | **6** |
| `regmap-mmio.c` | 4 · `regmap-spmi.c` 4 · `regmap-ac97.c` 3 |
| `regmap-mdio.c` / `regmap-spi.c` / `regmap-sccb.c` / `regmap-spi-avmm.c` / `regmap-sdw.c` / `regmap-sdw-mbq.c` / `regmap-i2c.c` / `regmap-w1.c` / `regmap-slimbus.c` | **各 2** |
| `regmap-i3c.c` | 1 |
| ⚠️ **`regcache-rbtree.c` / `regcache-lzo.c` / `regcache-flat.c` / `regmap-debugfs.c`** | **全 0** |

重跑：`grep -rc 'EXPORT_SYMBOL' drivers/base/regmap/*.c | grep -v ':0'`，合计 `grep -rh 'EXPORT_SYMBOL' drivers/base/regmap/*.c | wc -l` ⇒ **94**。
⚠️ **口径**：**数的是 `EXPORT_SYMBOL` 出现的行数**（本目录里一行一个，逐行看过）。

### 表 4 ⚠️`regmap_bus` 的三个口径（别混用）

| 口径 | 值 | 命令 |
|---|---|---|
| **全树实例数** | **61** | `grep -rhoE 'struct regmap_bus [a-z_0-9]+ = \{' --include=*.c . \| wc -l` |
| **`drivers/base/regmap/` 内实例数** | **22** | `grep -hoE 'struct regmap_bus [a-z_0-9]+ = \{' drivers/base/regmap/*.c \| wc -l` |
| **同目录含实例的文件数** | **13** | `grep -lE 'struct regmap_bus [a-z_0-9]+ = \{' drivers/base/regmap/*.c \| wc -l` |
| **目录外自定义** | **39**（= 61 − 22） | 上两条相减 |

⚠️ **"12 种通用适配 vs 22 个实例"不矛盾**：
**`regmap-i2c.c` 一个文件 6 个变体、`regmap-w1.c` 3 个（按地址×值位宽）、`regmap-mdio.c` / `regmap-spmi.c` 各 2 个。**

### 表 5 ⚠️ 初始化入口的两个口径

| 口径 | 值 | 命令 |
|---|---|---|
| **用户可见的宏** | **15 个 `regmap_init*` + 16 个 `devm_regmap_init*`** | `grep -cE '^#define regmap_init' include/linux/regmap.h` / `grep -cE '^#define devm_regmap_init' include/linux/regmap.h` |
| **底层实体（`__` 前缀原型）** | **14 + 15** | `grep -cE '^struct regmap \*__regmap_init' include/linux/regmap.h` / `grep -cE '^struct regmap \*__devm_regmap_init' include/linux/regmap.h` |

⚠️ **两个口径都含各自的通用版**（`regmap_init` / `__regmap_init`）。
**宏比实体多 1 的原因**：**`regmap_init_mmio` 和 `regmap_init_mmio_clk` 两个宏共用
`__regmap_init_mmio_clk` 一个实体。**
**本套文档正文统一用"14 + 15"（底层实体口径）**，**01 §3.1 / 03 §A1 / 05 §1.4 / 00 §5.1 四处一致。**

**不对称点（可重跑验证）**：
`grep -oE '__regmap_init[a-z0-9_]*' include/linux/regmap.h | sort -u` 的 14 项里 **没有 `_i3c`**，
`grep -oE '__devm_regmap_init[a-z0-9_]*' include/linux/regmap.h | sort -u` 的 15 项里 **有 `_i3c`**。

### 表 6 tracepoint

| 项 | 值 | 命令 |
|---|---|---|
| **真实事件数** | **17** | `grep -cE '^(TRACE_EVENT\|DEFINE_EVENT)\(' drivers/base/regmap/trace.h` |
| **事件类模板数** | **5** | `grep -cE '^DECLARE_EVENT_CLASS\(' drivers/base/regmap/trace.h` |
| ⚠️ **三者合计** | **22** | **把 `DECLARE_EVENT_CLASS` 也算进去就得到 22 —— 这是错的口径**（04 §5 / 07 §3.1） |
| `TP_CONDITION` | **0** | `grep -c 'TP_CONDITION' drivers/base/regmap/trace.h` |

### 表 7 错误码普查

| 码 | 值 |
|---|---|
| `-EINVAL` | **102** |
| `-ENOMEM` | **49** |
| `-ENOTSUPP` | **16** |
| `-EFAULT` | **12** |
| `-ENODEV` | 8 |
| `-EIO` | **7** |
| `-EBUSY` / `-EOPNOTSUPP` | 各 2 |
| `-ETIMEDOUT` | 1 |
| `-EPERM` / `-EAGAIN` / `-ERANGE` / `-ENOSPC` | **全 0** |

重跑：`for e in EINVAL ENOMEM ENOTSUPP EFAULT ENODEV EIO EBUSY EOPNOTSUPP ETIMEDOUT; do echo -n "-$e "; grep -rho -- "-$e" drivers/base/regmap/*.c | wc -l; done`

⚠️ **口径三条**：
1. **`grep -rho` 数的是"出现次数"，不是"返回点数"** ——一行里两个 `-EINVAL` 会算两次。
2. ⚠️ **统计范围显式限定 `drivers/base/regmap/*.c`（20 个 `.c`）**，**不含 `include/linux/regmap.h`**
   ⇒ **头文件里 45 处空桩的 `-EINVAL` 不在这 102 之内**（07 §5.2 类别 ③ 单独讲）。
3. **`-ENOTSUPP` 和 `-EOPNOTSUPP` 是两个不同的串**，**子串包含不会互相计入**（前者不含后者的前缀）。

### 表 8 告警与打印

| 项 | 值 | 口径 |
|---|---|---|
| `WARN_ONCE` | **47**（**45 在 `include/linux/regmap.h`、2 在 `regmap.c`**） | `grep -c 'WARN_ONCE' include/linux/regmap.h` + `grep -rc 'WARN_ONCE' drivers/base/regmap/*.c` |
| `WARN_ON` | **13** | **正则按 `WARN_ONCE\|WARN_ON_ONCE\|WARN_ON` 顺序交替，长的在前** ⇒ **不会把 `WARN_ONCE` 误算成 `WARN_ON`** |
| `BUG_ON` | **12**（`regcache.c` 5 · `regmap-spmi.c` 6 · `regmap-irq.c` 1） | `grep -rn 'BUG_ON' drivers/base/regmap/*.c` |
| **打印语句所在行数** | `regmap-irq.c` **30** · `regmap.c` **13** · `regcache.c` **11** · `regmap-debugfs.c` **5** · `regmap-spi-avmm.c` **2** · **`regmap-mmio.c` 0** | **`grep -rc` 数"含该串的行数"**（07 §4.1 已标注） |
| `dev_dbg`/`pr_debug` | **10**（`regcache.c` 7 · `regmap.c` 1 · `regmap-debugfs.c` 1 · `regcache-lzo.c` 1） | `grep -rc 'pr_debug\|dev_dbg' drivers/base/regmap/*.c` |
| **`dev_info`（`LOG_DEVICE` 门控）** | **3**（`regmap.c:2010` `:2213` `:2905`） | `grep -n 'dev_info' drivers/base/regmap/regmap.c` |

### 表 9 arm64 DTS 与消费者

| 项 | 值 | 命令 |
|---|---|---|
| `reg-io-width` | **32 个文件** | `grep -rl 'reg-io-width' arch/arm64/boot/dts/ \| wc -l` |
| **`reg-io-width` 取值分布** | **`<4>` 120 次 · `<1>` 14 次 · `<2>` 3 次 · `<8>` 0 次** | `grep -rhoE 'reg-io-width = <[0-9]+>' arch/arm64/boot/dts/ \| sort \| uniq -c \| sort -rn` |
| `big-endian` | **7 个文件** | `grep -rl 'big-endian' arch/arm64/boot/dts/ \| wc -l` |
| `little-endian` | **5 个文件** | 同上换字符串 |
| ⚠️`native-endian` | **0** | 同上 |
| `"syscon"` | **99 个文件** | `grep -rl '"syscon"' arch/arm64/boot/dts/ \| wc -l` |
| **`syscon_regmap_lookup_by_phandle` 消费者** | **186 个文件** | `grep -rl 'syscon_regmap_lookup_by_phandle' drivers/ \| wc -l` |
| **`drivers/mfd/` 里含 `regmap_irq_chip` 的文件** | **56** | `grep -rln 'struct regmap_irq_chip' drivers/mfd/*.c \| wc -l` |
| **全树 `regmap_irq_chip` 实例数** | **106** | `grep -rhoE 'struct regmap_irq_chip [a-z_0-9]+ = \{' --include=*.c . \| wc -l` |
| **`drivers/mfd/` 里 `regmap_bus` 实例（文件数 = 实例数）** | **8 / 8** | `grep -lE 'struct regmap_bus [a-z_0-9]+ = \{' drivers/mfd/*.c \| wc -l` |

⚠️ **`reg-io-width` 的两个数不一样，都对**：
**32 是"文件数"（`grep -rl`），137 是"命中行数"（`grep -rn \| wc -l`），120+14+3=137 是"带取值的赋值次数"。**
**06 §3.3 用的是文件数，§3.4 用的是取值分布** ——**两处口径都写明了。**

### 表 10 其他

| 项 | 值 | 命令 |
|---|---|---|
| **Kconfig 符号数** | **16** | `grep -c '^config ' drivers/base/regmap/Kconfig` |
| **debugfs 建文件的调用点数** | **8**（其中 1 个是"每个命名 range 一个"的循环，实际文件数随 range 数变化） | `grep -cE 'debugfs_create_(file\|bool)' drivers/base/regmap/regmap-debugfs.c` |
| **故障注入设施** | **0** | `grep -rniE 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR' drivers/base/regmap/` 无输出 |
| **模块参数** | **0** | `grep -rn 'module_param' drivers/base/regmap/` 无输出 |
| **字符设备 / sysfs** | **0** | `grep -rn 'class_create\|cdev_add\|device_attr\|sysfs_create' drivers/base/regmap/` 无输出 |
| **源码自陈设计债** | **1 条** | `grep -nE '(FIXME\|TODO\|REVISIT\|HACK\|\bXXX\b)' drivers/base/regmap/*.c \| grep -vE '"[^"]*XXX'` |
| **`REGMAP_ALLOW_WRITE_DEBUGFS` 提及处** | **3，无一处 `#define`** | `grep -rn 'REGMAP_ALLOW_WRITE_DEBUGFS' --include=*.c --include=*.h --include=Kconfig .` |
| **`REGCACHE_MAPLE`** | **0** | `grep -rn 'REGCACHE_MAPLE' include/ drivers/base/regmap/ \| wc -l` |

---

## §7 三个批量自检脚本的结果

**定稿时的状态（每次改完文档都重跑）**：

| 脚本 | 结果 | 说明 |
|---|---|---|
| `check_line_refs.py` | **干净** | **扫描约 300 条唯一 `文件:行号` 引用，无解析不到的文件、无越界行号，无 AMBIGUOUS、无 UNRESOLVED、忽略项 0 条** |
| `check_xrefs.py` | **干净** | **跨篇 `0X §Y` 引用全部命中；忽略项 0 条** |
| ⚠️`check_doc_coverage.sh` | ⚠️ **报"必查档全部有引用"，但这是假通过** | **它一篇官方概念文档都没找到（因为本子系统真的没有）**；**结论完全靠手工核对**（§1.4） |

**忽略文件**：**本文档集不需要 `.xref-ignore` / `.lineref-ignore`** ——
**两个脚本都是零告警通过，没有假阳性需要登记。**

⚠️ **本节刻意只描述结果、不照抄脚本失败行的格式**（skill 规则 28 的 条）：
**照抄会让下一轮脚本把这段引述再解析一次，制造出自指的假失败。**
**同理，§6 里所有 `grep -c` 的结果都写成"项 → 数字"的表格，
不写成 `文件名:数字` 的原生格式**（那会被行号检查器当成越界行号）。

---

## §8 逐篇取证表

**每篇列"核心论断 → 出处"和"自审改过什么"。**

### 00-overview.md

| 论断 | 出处 |
|---|---|
| 框架型、非总线型 | §1.2 两道门的实际输出 |
| 22 文件 / 10998 行 / 1977 行 / 94 导出 | §6 表 1、表 3 |
| 三层可插拔 | 01 §1（一等证据：`internal.h` + `regmap.c:774-811`） |
| 复杂度在配置里（54 字段） | §6 表 2 |
| 端到端路径 A / B / C 的每一步行号 | §2.1 的详读范围 |

**自审修正**：**①「⑧ 建分页树 `:1150`」改成 `:1151-1152`**（`:1150` 是空行）；
**② §4 表里 `struct reg_field` / `regmap_range_cfg` / `regmap_irq_chip` 三个位置原先指向宏或引用点，
改成结构定义本行（`:1278` / `:462` / `:1563`）**；
**③ `regcache_ops` 的关键字段列表补上了 `exit`，并注明 `debugfs_init` 在 `#ifdef` 里。**

### 01-api.md

| 论断 | 出处 |
|---|---|
| 三层可插拔 + 各层实现数 | 一等证据（头文件全文 + `internal.h`） |
| 7 种内置锁策略及各自行号 | `regmap.c:448-535` 逐个读过 |
| 14 + 15 个初始化实体 | §6 表 5（两个口径都列了） |
| 错误码语义（含两个"一码多因"） | §6 表 7 + `regmap.c:90-102` / `:1994-1995` |

**自审修正**：**`regmap_bus` 字段数最初写 19，重数为 18**（§9 实例 2）——**01 篇原本就是 18，是 05 篇写错了，以 01 为准。**

### 02-datastructures.md

| 论断 | 出处 |
|---|---|
| 六组易错关系 | 一等证据（`internal.h` 全体 + 头文件） |
| 三层并列（不是叠起来） | `regmap.c` 读写路径 + `internal.h` 字段分组 |
| 格式化层不可插拔 | `regmap.c:919` 起的硬编码 switch |
| 两把真锁 + 一对函数指针 | `internal.h:62-64` + `regmap.c:789-811` |

**自审修正**：**无内容修正**（本篇早于本轮写成，行号检查通过）。

### 03-internals.md

| 论断 | 出处 |
|---|---|
| `__regmap_init` 九步 | `regmap.c:729-1273` 逐段读过 |
| 位宽组合硬编码是最大设计约束 | `regmap.c:919` 起 + `regmap-mmio.c:31-63` |
| 源码自陈设计债只有 1 条 | §6 表 10 |
| `parse_inplace` 为 NULL 的性能悬崖 | `regmap.c` 读路径 |

**自审修正**：**无内容修正。**

### 04-extensions.md

| 论断 | 出处 |
|---|---|
| 四个子框架 + 挂载点 | 一等/二等混合（§2.1 / §2.2 已分开登记） |
| 17 个 tracepoint（不是 22） | §6 表 6 |
| 三个缓存后端 0 导出符号 | §6 表 3 |
| 12 种适配 / 22 实例 / 61 全树 | §6 表 4 |

**自审修正**：**无内容修正**（本篇的口径警告表被 §6 直接继承）。

### 05-usage.md

| 论断 | 出处 |
|---|---|
| 六个角色 | 由"谁建 regmap / 谁用 regmap"切分，代表驱动见 §2.4 |
| 每个骨架里的符号都存在 | §3.1 |
| 六组近邻符号的区别 | 一等证据（头文件的 inline 包装 + `regcache.c` 的 `WARN_ON`） |
| `dev_get_regmap` 返回 NULL 而非 ERR_PTR | `regmap.c:1615` 附近 |

⚠️ **自审修正（本篇最多，四条）**：
**① `REGCACHE_MAPLE` 是幻觉，改成 `REGCACHE_RBTREE` 并加显式版本警告**（§9 实例 1）；
**② `regmap_bus` 字段数 19 → 18，且分组表补上了漏掉的 `reg_update_bits`**（§9 实例 2）；
**③ 六处 `regcache.c` 行号原先指向 `EXPORT_SYMBOL` 行或函数体内某行，改成函数定义本行**
（`sync` `:432→:350` · `sync_region` `:491→:446` · `drop_region` `:522→:504` ·
`mark_dirty` `:561→:560` · `cache_bypass` `:581→:580` · `no_sync_defaults` `:565→:564`）；
**④ 两处 `WARN_ON` 行号 `:538-539→:539-540`、`:582→:583`。**

### 06-devicetree.md

| 论断 | 出处 |
|---|---|
| 五条发现路径 | `regmap.c:705-710` + `regmap-mmio.c:465` + `regmap-i2c.c:327` + `syscon.c:69-74` |
| 核心只读 5 个属性 | 逐属性 grep 过消费代码（每条都有行号） |
| 四级瀑布优先级 | `regmap.c:690-725` 逐行 |
| 分支 c 让 DT 彻底失效 | `regmap.c:883-917` 四路分支逐个读过 |
| `reg-io-width = <8>` 必然失败 | `regmap-mmio.c:43-63` + 全树 0 处使用 |
| arm64 DTS 统计 | §6 表 9 |

**自审修正**：**§5.2 原先说"regmap 不提供字节序 getter"，
改成更精确的表述**：**公共头 5 个 getter 都不查字节序；`regmap_get_val_endian` 虽导出但声明只在
`internal.h:279`，且参数是 `(dev, bus, config)` 不是 `(map)`**（§9 实例 7）。

### 07-debug.md

| 论断 | 出处 |
|---|---|
| 两个源码级开关（`#undef` 硬关） | `regmap.c:33` + `regmap-debugfs.c:293` + 全树 grep |
| debugfs 8 个建文件调用点及 mode | `regmap-debugfs.c:606-646` 逐行 |
| 两个可写文件的 setter 语义 | `regmap-debugfs.c:459-538` 逐行 |
| debugfs 绕过了 `WARN_ON` 互斥检查 | `regmap-debugfs.c:486` / `:532` vs `regcache.c:539-540` / `:583` |
| 17 个 tracepoint 的字段与配对性 | `trace.h` 全文 + 4 个打点位置 |
| `name` 字段是设备名 | `internal.h:287-293` |
| 日志可见性六个等级 | §6 表 8 |
| 0 故障注入 | §6 表 10 |

⚠️ **自审修正（四条）**：
**① `regmap-irq.c` 打印行数 31 → 30**（§9 实例 9）；
**② 打印合计 62 → 61，`dev_err`/`dev_warn` 58**；
**③ Kconfig 符号数 17 → 16**（§9 实例 10）；
**④ `trace_regmap_cache_only` `:540→:542`、`trace_regcache_drop_region` `:522→:514`。**

### 08-feynman.md

| 论断 | 出处 |
|---|---|
| 九个主题的每个技术断言 | **全部带行号，取自 §2.1 的详读范围** |
| `*change` 在一条分支上说谎 | `regmap.c:3253-3258` 逐行 |
| 分页的三个例外 | `regmap.c:1643-1657` 逐行 |
| `fast_io` 三件事一起变 | `regmap.c:789-811` + `:819-822` |

**自审修正**：**`use_raw_spinlock` 的位置从"头文件里的 config 字段"补成 `include/linux/regmap.h:435`。**

### 99-evidence.md（本篇）

**自审修正**：**§6 的每一格都逐条重跑对账过**（skill 规则 30 的 条），
**抓到三个数字错：Kconfig 16（非 17）· `drivers/mfd` 的 `regmap_irq_chip` 文件数 56（非 ≥8，之前的 grep 被 `head -8` 截断了）· debugfs 那 8 个是"调用点数"不是"每 map 文件数"。**

---

## §9 交叉验证实例（初始假设被推翻的十条）

**这一节的价值是"说明方法确实在起作用"。**
**每条都是先写下了一个错的东西，再被一条命令推翻。**

| # | 初始假设 | 推翻它的证据 | 落到哪 |
|---|---|---|---|
| **1** | **"缓存类型可以用 `REGCACHE_MAPLE`"**（凭更高版本的印象写进 05 §6.1） | `grep -rn 'REGCACHE_MAPLE' include/ drivers/base/regmap/` **命中 0**；枚举只有 4 个值（`include/linux/regmap.h:41-46`） | **05 §6.1 改值 + 加版本警告** |
| **2** | **"`regmap_bus` 有 19 个字段"** | `awk 'NR>=545 && NR<=562' \| grep -c ';'` = **18**；而且分组表加起来只有 17，**漏了 `reg_update_bits`** | **05 §4.1/§4.2 改数 + 补分组** |
| **3** | **"regmap 没有 DT 绑定"**（skill 模板的原话） | `Documentation/devicetree/bindings/regmap/regmap.txt` **确实存在，29 行**；加上 `syscon.yaml` 的两个属性 | **06 篇整篇建立在这上面**；§10 回写 skill |
| **4** | **"debugfs 目录名就是 `config.name`"** | `regmap-debugfs.c:586-587` 是 `kasprintf("%s-%s", devname, name)` | **07 §2.6 单独一节** |
| **5** | **"tracepoint 的 `name` 字段也是 `config.name`，能和 debugfs 对上"** | `internal.h:289-290` 优先返回 `dev_name(map->dev)` | **07 §3.2 定为"本篇最大观测盲区"** |
| **6** | **"DTS 里的 `big-endian` 总会被核心读到"** | `regmap.c:883-917` 是四路分支，**只有第四路调 `regmap_get_val_endian()`**；两路直接 `goto skip_format_initialization` | **06 §2.3 + §4 决策树的 ④⑤ 两层** |
| **7** | **"`regmap_get_val_endian` 导出了，所以可以用它查字节序"** | 声明只在 `internal.h:279`，**公共头里没有**；参数是 `(dev, bus, config)` 不是 `(map)` | **06 §5.2 改写**；**成为"存在 ≠ 能用"的范例** |
| **8** | **"引用 `EXPORT_SYMBOL` 那一行就等于引用了这个函数"** | 六处 `regcache.c` 行号全指到了 export 行或函数体中间；`grep -n '^void regcache_\|^int regcache_'` 给出真实定义行 | **05 篇六处行号修正**（§8 已列） |
| **9** | **"`regmap-irq.c` 有 31 条打印"**（肉眼数 grep 输出列表得来） | `grep -rc` 给出 **30** | **07 §4.1 改数，连带 §4.2 的合计从 59 改成 58** |
| **10** | **"Kconfig 有 17 个符号"**（肉眼数 Kconfig 得来） | `grep -c '^config '` 给出 **16** | **07 §1.1 + 99 §6 表 10 改数** |

**两条方法论结论**：
1. **凭印象写的版本相关符号（实例 1）和凭肉眼数的计数（实例 9、10）是两类高发错误**
   ⇒ **前者必须 grep，后者必须让 `grep -c` 出数。**
2. ⚠️ **`| head -N` 是统计的头号陷阱**（§8 本篇自审那条）：
   **用来"看几个样本"的 `head` 很容易被当成"总数"。**
   **凡是要写进文档的数字，命令末尾必须是 `| wc -l` 或 `grep -c`，不能有 `head`。**

**一条反例（说明结论的方向）**：**04 篇写的"22 个声明里 5 个是 `DECLARE_EVENT_CLASS` ⇒ 17 个真实事件"
在本轮复核中完全正确** ——**说明"先把口径写清楚"确实能防住这类错，不是运气。**

---

## §10 对照与回写

### §10.1 rule-29 独立对照：本子系统没有对照套

**`output/qoder/` 下没有 regmap 目录**（对照套只覆盖了 spi）
⇒ **本套文档没有做过独立双套 diff。**

**这意味着**：**§9 那十条全部来自自审，没有"另一套独立产出"提供的外部视角。**
⇒ **skill 规则 29 说的"两套写了同一句错话 ⇒ 源码本身有歧义"这条信号在本子系统上拿不到。**
**读者如果另有一套 regmap 文档，最值得对照的三处是**：
**① 三层还是两层可插拔（本套认为三层，把锁也算进去）；
② `regmap_bus` 的实例口径（本套给了三个口径，§6 表 4）；
③ DT 字节序属性在哪几条路上生效（本套找到 4 个消费点，06 §2.1）。**

### §10.2 要回写 skill 的三条

| # | 内容 | 为什么 |
|---|---|---|
| **1** | ⚠️ **删掉骨架里"（如 regmap）"这个"没有 DT 绑定"的例子** | **regmap 有 29 行 DT 绑定 + syscon 那条重度使用的路径**（§1.3 / §9 实例 3）⇒ **这个例子会误导下一个用 skill 的人直接跳过 06 篇** |
| **2** | **在规则 30 里加一条：「统计命令末尾不许有 `head`」** | **本轮唯一一个被 `head -8` 截断的数字差了 7 倍**（56 vs 8，§9 结论 2） |
| **3** | **在规则 20 里补一句：「引用函数要指定义行，不是 `EXPORT_SYMBOL` 行」** | **本轮六处行号错全是同一个机理**（§9 实例 8）；**三个脚本都抓不到，因为行号在范围内** |

### §10.3 明确不吸收的权衡（避免下次又发现一遍）

| 没做 | 理由 |
|---|---|
| **不读 regmap-irq 的分发主循环** | **它本质是 `irq_chip` 实现**，属 irq 子系统；**`output/irq/` 那套讲了那一侧** |
| **不读三个缓存后端的算法** | **通用数据结构问题**，收益低于成本；**04 §2.2 只讲"怎么选"** |
| **不做 ACPI 对照** | **arm64-only 约定**；**代码是同一行 `fwnode_property_read_bool`，没有独立逻辑** |
| **07 篇不给任何示例输出** | **未上机运行**（§5.1）；**编造输出的危害大于"读者得自己跑一次"** |
| **不引用具体芯片手册** | **手册是树外资料，无法让读者复核**；**所有"这个寄存器是 volatile"之类的例子都用假名（`FOO_STATUS`）** |
