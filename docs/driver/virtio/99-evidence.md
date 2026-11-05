---
title: 取证附录
---

# virtio 99：取证、方法与未验证项

这一篇不讲 virtio 怎么工作，讲**前九篇的每个论断是怎么来的、哪些没验证过**。

⚠️ **本篇比 of / timer 那两套多一层**：§5 有**第五层未验证项
「对端不在本树」** ——这是跨边界 ABI 型子系统特有的。

读法：
- 想知道"某个数字可信吗" → §3，每条都带能一条命令重跑的复现命令
- 想知道"哪些结论是推断出来的" → §5，**五层**分层
- 想知道"哪些地方源码本身容易看错" → §6
- 想知道"这套文档自己犯过什么错" → §7.3

---

## §1 基线

### §1.1 树的状态：干净

```bash
git log -1 --format='%H %s'
# → f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158

git status --porcelain -- drivers/virtio include/linux/virtio'*'.h \
    include/uapi/linux/virtio_'*'.h include/linux/vringh.h
# → 无输出
```

**无输出 ⇒ virtio 在这棵树里是未经改动的 6.1.158 上游。**

⚠️ **这一条必须显式核对，不能假设** ——因为**同一棵树里 timer 是改过的**
（3 处 PREEMPT_RT 改动 + `arch/arm64/Kconfig` 的 `select ARCH_SUPPORTS_RT`）。
⇒ **"这棵树带补丁"和"这个子系统带补丁"是两件事**，必须逐子系统查。

**顺带确认**：`drivers/vhost/`（对端侧的 vringh，04 §4）也没在
上面那条命令的范围里 ⇒ **本套没有核对它的基线**（§2.3 登记为三等证据）。

### §1.2 规模

```bash
wc -l drivers/virtio/*.c drivers/virtio/*.h | tail -1     # → 11854
```

| 分组 | 文件（行数） | 处置 |
|---|---|---|
| **核心框架** | `virtio.c`(577) · `virtio_ring.c`(2876) | **`virtio.c` 全文读；`virtio_ring.c` 读结构与关键路径** |
| **传输层** | `virtio_pci_common.c`(654) · `virtio_pci_modern.c`(556) · `virtio_pci_modern_dev.c`(711) · `virtio_pci_legacy.c`(235) · `virtio_pci_legacy_dev.c`(222) · `virtio_pci_common.h`(141) · `virtio_mmio.c`(860) · `virtio_vdpa.c`(417) | **`virtio_mmio.c` 读关键路径**（arm64 主路）；PCI 只读 ops 位置和日志 |
| **设备驱动** | `virtio_mem.c`(2952) · `virtio_balloon.c`(1132) · `virtio_input.c`(414) | **`virtio_input.c` 读全（05 的骨架参照）**；另两个只读符号表和设计债 |
| **辅助** | `virtio_dma_buf.c`(89) · `virtio_anchor.c`(18) | **`virtio_anchor.c` 18 行读全** |

**头文件 1758 行**：`virtio_config.h`(623) · `virtio_vsock.h`(253) ·
`virtio_net.h`(229) · `virtio.h`(209) · `virtio_pci_modern.h`(118) ·
`virtio_ring.h`(104) · `virtio_byteorder.h`(64) · `virtio_pci_legacy.h`(40) ·
`virtio_console.h`(38) · `virtio_dma_buf.h`(37) · `virtio_caif.h`(24) ·
`virtio_anchor.h`(19)。

⚠️ **两个"最大的文件"都不是核心**（01 §0.3）：
`virtio_mem.c`(2952) 和 `virtio_balloon.c`(1132) 是**设备驱动**。
**真正的框架只有 3453 行。**

**跨边界 ABI**：`include/uapi/linux/virtio_*.h` **27 个**
（`ls include/uapi/linux/virtio_*.h | wc -l`）。

### §1.3 子系统类型判定：虚拟总线 / 跨边界 ABI 型

按 skill 阶段 1 步骤 3b 的两道门：

```bash
# 门 1：有没有自己的 bus_type
grep -rn 'struct bus_type' drivers/virtio/*.c include/linux/virtio.h
# → drivers/virtio/virtio.c:343:static struct bus_type virtio_bus = {

# 门 2：协议 ABI 在不在 uapi 里
ls include/uapi/linux/virtio_*.h | wc -l                      # → 27
grep -rn 'struct virtio_config_ops [a-z_]* = {' --include=*.c .  # → 9 个，跨 5 目录
```

**门 1 有输出、门 2 也有 ⇒ 虚拟总线型。**
**和 skill 里记的实例数字完全一致**（27 个 uapi 头、9 个 `virtio_config_ops`、
`virtio_pci_modern.c` 一个文件两个、散在 5 个目录）。

**门 1 这次是真的**（对比 timer 那两个伪总线，02 §1.0.A）：

| 判据 | virtio | timer 的 `clocksource_subsys` |
|---|---|---|
| 有 driver 绑上去吗 | **有，25 个** | **0 个** |
| 有 `.match` 吗 | **有**（`virtio_dev_match`，`drivers/virtio/virtio.c:85`） | 没有 |
| 走 probe 吗 | **走**（`virtio_dev_probe`，`:237`） | 不走 |

⇒ **所以 timer 那轮补的"第三问（有没有 driver 绑上去）"在这里也起了作用**——
**它把"真总线"和"伪总线"区分开了，而两道门本身做不到。**

### §1.4 官方文档登记：**真的没有**（第四种情况）

**三种变体全试过**（timer 那轮总结的判别流程）：

```bash
ls -d Documentation/virtio Documentation/virtios Documentation/driver-api/virtio*
# → 三个都 No such file or directory
ls Documentation/ | grep -i virt
# → 只有 virt（那是虚拟化目录，不是 virtio）
ls Documentation/driver-api/ | grep -i virt
# → 空
find Documentation -iname '*virtio*' -o -iname '*vring*'
# → 只有 filesystems/virtiofs.rst（virtiofs 文件系统的）、
#    devicetree/bindings/virtio/、i2c/i2c-virtio.yaml、gpio/gpio-virtio.yaml
```

⇒ **处置类别 (d)：真的没有概念/框架文档。**
**和 cdev / mailbox / reset 同类**，**不是 timer 那种"复数 s"、
也不是 of 那种"目录名不同"**。

**所以 `check_doc_coverage.sh` 报"【必查】共 0 篇 → 必查档全部有引用"
这次是真的通过**（§2.4）。

⚠️ **但这不意味着"没有文档"** ——**规范在树外**：
**OASIS VIRTIO 规范**（`Documentation/devicetree/bindings/virtio/mmio.yaml`
的 `description` 段给了链接）。
⇒ **这是本套和 of / timer 最根本的差别**：
of 有 7 篇树内文档、timer 有 7 篇，**virtio 的"设计意图"文档完全在树外**。

**DT 绑定侧**（脚本的【参考】档）：

| 文件 | 行数 | 被引用 |
|---|---|---|
| `Documentation/devicetree/bindings/virtio/mmio.yaml` | 67 | **82 次** |
| `Documentation/devicetree/bindings/virtio/virtio-device.yaml` | 41 | 6 次 |
| `Documentation/devicetree/bindings/virtio/pci-iommu.yaml` | 101 | 4 次 |

⇒ **3 篇全部被引用**（脚本报"共 3 篇，被引用 3 篇"）。

### §1.5 ⚠️ 文档与代码的冲突

⚠️ **本套没有"树内概念文档 vs 代码"的冲突可查** ——因为没有树内概念文档（§1.4）。

**但有两处"绑定文档 vs 代码"的一致性值得记**（都是**一致**的，不是冲突）：

| 点 | 绑定文档 | 代码 | 结论 |
|---|---|---|---|
| `virtio,deviceNN` 的格式 | `pattern: "^virtio,device[0-9a-f]{1,8}$"` + `lower case hexadecimal with leading zeroes suppressed`（`virtio-device.yaml`） | `snprintf(..., "virtio,device%x", ...)`（`drivers/virtio/virtio.c:388`） | **`%x` 正好是小写十六进制不补零 ⇒ 严格对上** |
| 绑定的 `examples` 用 `virtio,device22` | 同上 | `VIRTIO_ID_I2C_ADAPTER` = 34 = 0x22（`include/uapi/linux/virtio_ids.h:63`） | **对上** |

⇒ **这两条是本套唯一能做的"文档 vs 代码"交叉验证**，
**而且结果是一致的** ——⚠️ **所以本套缺少 of / timer 那种
"以代码为准，文档已过期"的处置案例。**

---

## §2 证据等级

### §2.1 一等证据：引用密集、跨篇覆盖（可当结论直接用）

| 文件 | 引用次数 | 覆盖的篇 |
|---|---|---|
| `drivers/virtio/virtio.c` | **96** | **00–08 全部** |
| `include/linux/virtio.h` | **73** | 00,01,02,03,05,07,08 |
| `drivers/virtio/virtio_ring.c` | **65** | 00,02,03,05,07,08 |
| `include/linux/virtio_config.h` | **52** | 00,01,02,03,05,07,08 |
| `include/uapi/linux/virtio_ring.h` | **45** | **00,02,08**（含 02 §5 的字节布局） |
| `drivers/virtio/virtio_mmio.c` | **24** | 00,04,06,07 |
| `include/uapi/linux/virtio_ids.h` | **16** | 04,06,08 |
| `include/uapi/linux/virtio_config.h` | **12** | 00,03,07 |
| `drivers/virtio/virtio_input.c` | **12** | **05 一篇但覆盖全部 9 个对照点**（05 §1.1） |

### §2.2 二等证据：只读了片段（详读范围写明）

| 文件 | 引用次数 | 详读了哪部分 | 没读的部分 |
|---|---|---|---|
| `drivers/virtio/virtio_anchor.c` | 8 | **全文 18 行** | — |
| `drivers/virtio/virtio_dma_buf.c` | 6 | 4 个导出符号的位置 | 89 行的实现体 |
| `include/linux/virtio_ring.h` | 6 | 四个屏障原语（`:25`/`:33`/`:41`/`:49`） | 其余 |
| `include/linux/virtio_byteorder.h` | 5 | **全部 7 个 inline 的位置** | 实现体（都是几行） |
| `drivers/virtio/virtio_pci_modern.c` | 5 | 两份 ops 的位置（`:484`/`:504`）+ HARDEN 的两处 | ⚠️ **556 行里只读了这些** |
| `drivers/virtio/virtio_mem.c` | 5 | **只读了 5 条设计债的位置** | ⚠️ **2952 行几乎没读** |
| `include/uapi/linux/virtio_mmio.h` | 2 | **全部寄存器偏移**（04 §2.2 的表） | 注释 |
| `drivers/virtio/virtio_vdpa.c` | 2 | ops 位置 | 417 行的实现 |
| `drivers/virtio/virtio_balloon.c` | 2 | 设备 ID 与导出符号数 | ⚠️ 1132 行没读 |

### §2.3 三等证据：只 grep 了符号或行号，没读上下文

- ⚠️ **`drivers/vhost/vringh.c`（1511 行）**：**完全没读**，
  只确认了行数和"它是 device 侧的库"这件事（04 §4）。
  **连基线都没核对**（§1.1）
- `drivers/virtio/virtio_pci_common.c`（654）· `virtio_pci_modern_dev.c`(711)
  · `virtio_pci_legacy.c`(235) · `virtio_pci_legacy_dev.c`(222)：
  **只统计了导出符号数和 ops 位置**
- 另外 4 个传输层（`remoteproc_virtio.c` · `virtio_ccw.c` ·
  `mlxbf-tmfifo.c` · `virtio_uml.c`）：**只 grep 了 `virtio_config_ops` 的行号**
- 25 个设备驱动里的 22 个（住在 `drivers/virtio/` 之外的）：
  **只统计了目录分布**（01 §1.2）
- `drivers/xen/grant-dma-ops.c` · `arch/x86/mm/mem_encrypt_amd.c` ·
  `arch/s390/mm/init.c`：**只 grep 了它们用 anchor 这件事**（04 §9.2）
- `arch/arm64/boot/dts/arm/foundation-v8.dtsi`：读了 virtio 节点
  （`:223-227`）和中断控制器的 `#interrupt-cells`（`:128`），**没通读**

### §2.4 ⚠️ `check_doc_coverage.sh` 的处置

**结论：【必查】档的"共 0 篇"这次是真通过；【参考】档的输出也合理。**

| 档 | 输出 | 处置 |
|---|---|---|
| 【必查】 | **共 0 篇** | **真的没有**（§1.4 三种变体全试过）⇒ **类别 (d)** |
| 【参考】 | **共 3 篇，被引用 3 篇** | **全覆盖**，合理 |

⇒ 本套**没有**登记 `.doc-coverage` 豁免文件。

⚠️ **和 timer 那轮的教训呼应**：timer 的"共 0 篇"是**假通过**
（目录叫 `timers`），**virtio 的"共 0 篇"是真的** ——
**两者的脚本输出一模一样，只能靠手工三试来区分**（§8.1）。

### §2.5 索引工具的使用与盲区

cbm 只用于导航，**所有行号最终由 `rg` + `sed` 在真实树上确认**。
**本子系统上三个盲区都命中了**：

- **宏生成的符号**：`virtio_cread()` / `virtio_cwrite()` 家族
  （`include/linux/virtio_config.h:390` / `:419`）是宏，索引里没有节点；
  **`module_virtio_driver()` 也是宏**
- **函数指针间接调用**：**`vdev->config->xxx()` 这一整层索引拉不出来** ——
  而它正是层 1 的全部接口（01 §6）
- **`#ifdef DEBUG` 里的宏**：`BAD_RING` / `START_USE` / `LAST_ADD_TIME_*`
  （`drivers/virtio/virtio_ring.c:18-68`）**默认编译路径上根本不存在**
  ⇒ **索引按默认配置建，看不到它们**（07 §4）

### §2.6 全称断言的验证

| 断言 | 出处 | 验证命令 | 结果 |
|---|---|---|---|
| `drivers/virtio` 没被本树改过 | §1.1 · 01 §0.2 | `git status --porcelain -- drivers/virtio include/linux/virtio*.h include/uapi/linux/virtio_*.h include/linux/vringh.h` | 空 |
| **一个 tracepoint 都没有** | 07 §1.1 | `grep -rn 'trace_' drivers/virtio/*.c \| wc -l` + `ls include/trace/events/ \| grep -i virtio` | **0** + 只有 vsock 那个 |
| 没有故障注入 | 07 §8 | `grep -rn 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR\|CONFIG_FAIL' drivers/virtio/*.c` | 空 |
| 5 个 sysfs 属性**全部只读** | 07 §3.1 | `grep -n 'DEVICE_ATTR' drivers/virtio/virtio.c` | **5 个全是 `DEVICE_ATTR_RO`** |
| `virtio_ring.c` 零全局变量 | 02 §7.1 | `grep -nE '^(static )?(DEFINE_|struct .* = \{)' drivers/virtio/virtio_ring.c` | 只有函数定义，无全局状态 |
| 25 个 `virtio_driver` 跨 22 目录 | 01 §1.2 | **三种口径互相印证**（§3.8） | 25 / 25 / 25 |
| 9 个 `virtio_config_ops` 跨 5 目录 | 01 §1.1 | `grep -rn 'struct virtio_config_ops [a-z_]* = {' --include=*.c .` | 9 行、5 个目录 |
| anchor 的强制路径只有 2 个真使用者 | 04 §9.2 | `grep -rlw 'virtio_require_restricted_mem_acc' --include=*.c .` | AMD SEV + s390 PV（+ 实现本身） |
| ⚠️ **arm64 不在 anchor 的强制路径里** | 04 §9.2 · 07 §2.2 | 同上（**跨出了 `drivers/virtio/` 去查 `arch/`**） | **arm64 确实不在** |

**最后一条是本套最有 arm64 价值的一个否定性断言** ——
⚠️ **它跨出了子系统目录**（查 `arch/x86` 和 `arch/s390`），符合 skill 规则 30。

### §2.7 裁掉了什么

| 裁掉的 | 为什么 | 影响到哪一篇 |
|---|---|---|
| ⚠️ **对端（device 侧）的一切** | **不在本树**（除 vhost）⇒ **§5.5 有专门一层** | 全套；05 §6 显式声明 |
| ⚠️`drivers/vhost/vringh.c`(1511) | **它实现 device 角色**，超出"只覆盖 driver 侧"的边界 | 04 §4 只交代存在和连接点 |
| **s390 CCW / UML / BlueField 三个传输层** | 非目标架构 | 01 §1.1 点名不展开 |
| **AMD SEV / s390 PV 的机密计算细节** | **§2.6 已确认 arm64 不走这条路** | 04 §9.2 |
| **`pci-iommu.yaml`(101 行)** | arm64 虚拟机更常用 MMIO 形态的 virtio-iommu | 06 §3.3 · §8.2 |
| **22 个住在别处的设备驱动** | 各属自己的子系统；**`virtio_net` 会在网卡驱动那套里再遇到** | 01 §1.2 只统计分布 |
| **legacy（0.9.X）的完整流程** | 只标出哪些寄存器/表是 legacy 专用 | 04 §2.2 · 03 §C5 |
| **PCI 的 MSI-X 向量分配** | **是 PCI 的事，和 virtio 抽象无关** | 05 §9 显式声明 |
| **`virtio_mem` / `virtio_balloon` 的内部算法** | 它们是设备驱动不是框架 | 04 §5 · §6 |
| **ACPI 枚举路径** | 本套 arm64-only 限定在 DT | 06 §8.3 |

---

## §3 统计数字总表（每条都能一条命令重跑）

**统计范围一律显式列文件**。下文 `$CORE` 指 `drivers/virtio/` 下的 14 个 `.c`：

```bash
CORE="drivers/virtio/virtio.c drivers/virtio/virtio_ring.c \
drivers/virtio/virtio_pci_common.c drivers/virtio/virtio_pci_modern.c \
drivers/virtio/virtio_pci_modern_dev.c drivers/virtio/virtio_pci_legacy.c \
drivers/virtio/virtio_pci_legacy_dev.c drivers/virtio/virtio_mmio.c \
drivers/virtio/virtio_vdpa.c drivers/virtio/virtio_balloon.c \
drivers/virtio/virtio_mem.c drivers/virtio/virtio_input.c \
drivers/virtio/virtio_dma_buf.c drivers/virtio/virtio_anchor.c"
```

**注意 `drivers/virtio/` 下共 15 个文件，其中 1 个是 `.h`**
（`virtio_pci_common.h`，141 行）⇒ **`.c` 是 14 个。**

### §3.1 规模类

| 数字 | 值 | 复现命令 |
|---|---|---|
| `drivers/virtio/` 总行数 | **11854** | `wc -l drivers/virtio/*.c drivers/virtio/*.h \| tail -1` |
| 文件数 | **15**（14 `.c` + 1 `.h`） | `ls drivers/virtio/*.c drivers/virtio/*.h \| wc -l` |
| 框架部分 | **3453**（577 + 2876） | `wc -l drivers/virtio/virtio.c drivers/virtio/virtio_ring.c \| tail -1` |
| `include/linux/virtio*.h` | **1758** | `wc -l include/linux/virtio*.h \| tail -1` |
| uapi 规范头文件数 | **27** | `ls include/uapi/linux/virtio_*.h \| wc -l` |
| 对端侧的 vringh | **1511 + 311** | `wc -l drivers/vhost/vringh.c include/linux/vringh.h` |
| DT 绑定 | **3 篇 209 行** | `wc -l Documentation/devicetree/bindings/virtio/*.yaml` |

### §3.2 导出符号类

| 文件 | `EXPORT_SYMBOL*` | 角色 |
|---|---|---|
| `virtio_ring.c` | **32** | **数据面框架** |
| `virtio_pci_modern_dev.c` | 19 | **库**（`CONFIG_VIRTIO_PCI_LIB`） |
| `virtio_pci_legacy_dev.c` | 12 | 库 |
| `virtio.c` | **11** | **总线与注册** |
| `virtio_dma_buf.c` | 4 | 辅助 |
| `virtio_anchor.c` | 2 | 辅助 |
| 其余 8 个 `.c` | **全部 0** | **叶子**（传输层驱动 + 设备驱动） |
| **合计** | **80** | — |

复现（逐文件，便于逐格对账）：

```bash
for f in virtio.c virtio_ring.c virtio_pci_common.c virtio_pci_modern.c \
         virtio_pci_modern_dev.c virtio_pci_legacy.c virtio_pci_legacy_dev.c \
         virtio_mmio.c virtio_vdpa.c virtio_balloon.c virtio_mem.c \
         virtio_input.c virtio_dma_buf.c virtio_anchor.c; do
  printf '%-28s %s\n' "$f" "$(grep -cE '^EXPORT_SYMBOL' drivers/virtio/$f)"
done
echo "总计 → $(grep -hE '^EXPORT_SYMBOL' drivers/virtio/*.c | wc -l)"
```

**这张表本身就是一张分层图**（01 §0.4）：
框架 43 个（`virtio.c` + `virtio_ring.c`）、库 31 个、叶子 0 个。

### §3.3 错误码类

| 错误码 | 次数 | 语义 |
|---|---|---|
| `-ENOMEM` | **46** | **排第一** ——建队列/分配 vring |
| `-EINVAL` | **27** | 参数不合法 |
| `-EBUSY` | **21** | 设备/队列在用 |
| `-ENODEV` | **14** | 设备不存在；**也是协商失败的返回值** |
| `-ENOSPC` | **13** | ⚠️ **队列满 —— 正常背压** |
| `-ENOENT` | **11** | feature 位不存在（`virtio_cread_feature()`） |
| `-EAGAIN` | 5 | — |
| `-EIO` · `-E2BIG` | 4 · 4 | — |
| `-EOPNOTSUPP` · `-ENOSYS` · `-EFAULT` · `-EEXIST` · `-ERANGE` | **全部 0** | 本子系统不返回 |

复现：

```bash
for e in ENOMEM EINVAL EBUSY ENODEV ENOSPC ENOENT EAGAIN EIO E2BIG \
         EOPNOTSUPP ENOSYS EFAULT EEXIST ERANGE; do
  printf '%-14s %s\n' "-$e" "$(grep -how -- "-$e" $CORE | wc -l)"
done
```

**`-ENOMEM` 排第一是本子系统的特征**（01 §9.1）——
和 timer（`-EINVAL`/`-EFAULT`）、of（`-EINVAL`）都不同：
**virtio 的每个队列都要分配一段 DMA 可达的 vring，队列数由设备决定。**

⚠️ **口径陷阱**：`-EOPNOTSUPP` 和 `-ENOTSUPP` 是子串关系
⇒ **必须 `grep -w`**（timer 那轮踩过，那里两者分别是 10 和 0）。

### §3.4 可观测性类

| 数字 | 值 | 复现命令 |
|---|---|---|
| ⚠️ **tracepoint** | ⚠️ **0** | `grep -rn 'trace_' drivers/virtio/*.c \| wc -l` |
| ⚠️ **debugfs 出口** | ⚠️ **0** | `grep -rn debugfs drivers/virtio/*.c` |
| ⚠️ **procfs 出口** | ⚠️ **0** | `grep -rn 'proc_create' drivers/virtio/*.c` |
| sysfs 属性 | **5，全部 RO** | `grep -c 'DEVICE_ATTR' drivers/virtio/virtio.c` |
| 故障注入接入点 | **0** | §2.6 |
| `WARN`/`BUG` 点（`virtio.c`） | **8** | `grep -c 'WARN_ON\|WARN_ONCE\|BUG_ON\|BUG()' drivers/virtio/virtio.c` |
| `WARN`/`BUG` 点（`virtio_ring.c`） | **22** | 同上换文件 |
| `CONFIG_VIRTIO_HARDEN_NOTIFICATION` 使用点 | **7** | `grep -rn 'CONFIG_VIRTIO_HARDEN_NOTIFICATION' drivers/virtio/*.c include/linux/virtio_config.h \| wc -l` |

⚠️ **tracepoint / debugfs / procfs 三个全是 0** ——
**这是本子系统和 timer 最大的反差**（timer 有 17 个 tracepoint +
`/proc/timer_list` + 5 个 sysfs 属性含 3 个可写）。
⇒ 03 §C4、07 §1 把它列为最大欠债。

### §3.5 设计债类

```bash
grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' $CORE | grep -vE '"[^"]*XXX'
```

**13 条**：

| 文件 | 条数 | 位置 |
|---|---|---|
| `drivers/virtio/virtio_ring.c` | **8** | `:199` · `:237` · `:263` · `:572` · `:860` · `:921` · `:1068` · `:1770` |
| `drivers/virtio/virtio_mem.c` | **5** | `:544` · `:558` · `:653` · `:1094` · `:1164` |
| **`drivers/virtio/virtio.c`（总线核心）** | **0** | — |
| 其余 12 个 `.c` | **0** | — |

⚠️ **必须排掉字符串里的 `XXX`** ——`drivers/virtio/virtio.c:369` 的
`char compat[] = "virtio,deviceXXXXXXXX"` 是 `snprintf` 占位符
⇒ **不排会数成 14**（§6.4）。

**最有分量的一条**：`drivers/virtio/virtio_ring.c:199` 的
`How to notify other side. FIXME: commonalize hcalls!`
⇒ **"通知对端"本该有统一抽象但没有**，9 个传输层各写一遍（03 §C1）。

### §3.6 sysfs 属性逐个

| 属性 | 模式 | 声明位置 | 格式串位置 | 格式 |
|---|---|---|---|---|
| `device` | **RO** | `drivers/virtio/virtio.c:20` | `:18` | `0x%04x` |
| `vendor` | RO | `drivers/virtio/virtio.c:28` | `:26` | `0x%04x` |
| `status` | **RO** | `drivers/virtio/virtio.c:36` | `:34` | `0x%08x` |
| `modalias` | RO | `drivers/virtio/virtio.c:45` | `:42` | `virtio:d%08Xv%08X` |
| `features` | RO | `drivers/virtio/virtio.c:62` | `:57-59` | 逐位 `%c` |

**数组在 `drivers/virtio/virtio.c:64-71`。**
⚠️ **五个全是 `DEVICE_ATTR_RO`** ⇒ **一个可写的都没有**（07 §3.1）。

### §3.7 Kconfig 选项

| 选项 | 位置 |
|---|---|
| `VIRTIO_ANCHOR` | `drivers/virtio/Kconfig:2` |
| `VIRTIO` | `drivers/virtio/Kconfig:5` |
| `VIRTIO_PCI_LIB` | `drivers/virtio/Kconfig:13` |
| `VIRTIO_PCI_LIB_LEGACY` | `drivers/virtio/Kconfig:21` |
| `VIRTIO_MENU` | `drivers/virtio/Kconfig:31` |
| `VIRTIO_HARDEN_NOTIFICATION` | `drivers/virtio/Kconfig:36` |
| `VIRTIO_PCI` | `drivers/virtio/Kconfig:50` |
| `VIRTIO_PCI_LEGACY` | `drivers/virtio/Kconfig:63` |
| `VIRTIO_VDPA` | `drivers/virtio/Kconfig:83` |
| `VIRTIO_PMEM` | `drivers/virtio/Kconfig:96` |
| `VIRTIO_BALLOON` | `drivers/virtio/Kconfig:107` |
| `VIRTIO_MEM` | `drivers/virtio/Kconfig:118`（`depends on X86_64 \|\| ARM64`，`:120`） |
| `VIRTIO_MMIO` | `drivers/virtio/Kconfig:148` |
| ⚠️`VIRTIO_MMIO_CMDLINE_DEVICES` | `drivers/virtio/Kconfig:158` |
| `VIRTIO_DMA_SHARED_BUFFER` | `drivers/virtio/Kconfig:169` |

### §3.8 两层的实现统计（三种口径互相印证）

| 层 | 口径 | 值 |
|---|---|---|
| 设备驱动 | **严格 pattern（带 `0-9`）** | **25** |
| 设备驱动 | 宽松 pattern | **25** |
| 设备驱动 | `register_virtio_driver`/`module_virtio_driver` 文件数 | **25** |
| 设备驱动的目录数 | — | **22** |
| 传输层 | `struct virtio_config_ops X = {` | **9** |
| 传输层的目录数 | — | **5** |

复现：

```bash
grep -rn 'struct virtio_driver [a-z_0-9]* = {' --include=*.c . | wc -l              # 25
grep -rnE 'struct virtio_driver [a-z_0-9]+ *=' --include=*.c . | wc -l             # 25
grep -rlw 'register_virtio_driver\|module_virtio_driver' --include=*.c . | wc -l   # 25
grep -rln 'struct virtio_driver [a-z_0-9]* = {' --include=*.c . | xargs -n1 dirname | sort -u | wc -l   # 22
grep -rn 'struct virtio_config_ops [a-z_]* = {' --include=*.c . | wc -l            # 9
```

⚠️ **必须带 `0-9`**（§6.3）：用 `[a-z_]*` 会漏 2 个 ⇒ 得到 23。

### §3.9 文档自身规模

| 篇 | 行数 | 章节数 |
|---|---|---|
| 00-overview | 618 | 37 |
| 01-api | 703 | 42 |
| 02-datastructures | 655 | 28 |
| 03-internals | 663 | 32 |
| 04-extensions | 365 | 22 |
| 05-usage | 670 | 26 |
| 06-devicetree | 471 | 32 |
| 07-debug | 474 | 28 |
| 08-feynman | 621 | 37 |
| **前九篇合计** | **5240** | **284** |

---

## §4 工具版本

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本 |
| ripgrep | 13.0.0 | 全树取证主力 |
| git | 2.34.1 | 基线确认（§1.1） |
| GNU sed | 4.8 | 逐行核对 |
| cbm | 见 §2.5 | 仅导航 |

⚠️ **没有跑过的工具**：`dtc`、`make dtbs_check`、`gcc`、任何内核构建、
**任何虚拟机**。

---

## §5 未验证项（**五层**分层）

### §5.1 第一层：未编译、未运行

**本套从头到尾没有编译过一行代码，没有跑过一次内核，
也没有起过一台虚拟机。**

| 内容 | 风险 |
|---|---|
| 05 篇全部骨架 | **未编译**（篇首已声明） |
| 07 篇全部命令的输出 | **未实测** ——路径/属性名从 `DEVICE_ATTR` 和格式串反推 |
| 06 §9 的三层排查 | **没在真机走过** |
| **`DEBUG` 模式的行为** | **从没编译过带 `-DDEBUG` 的 `virtio_ring.o`**（07 §4） |

**唯一逐字确认的**：5 个 sysfs 属性的格式串
（`drivers/virtio/virtio.c:18`/`:26`/`:34`/`:42`/`:57-59`）
和 §2 那批日志原文（07 §2.2、§2.4）。

### §5.2 第二层：只读了片段（见 §2.2、§2.3）

**四处高风险**：

| 内容 | 风险 |
|---|---|
| ⚠️ **`virtio_ring.c` 2876 行只读了结构和关键路径** | **02 §5 的字节布局是从 uapi 头文件的注释和 `vring_init()`/`vring_size()` 推的**，**不是从 `virtio_ring.c` 的实现逐行验证的** |
| ⚠️ **PCI 传输层 2418 行几乎没读** | 01 §1.1、04 §1 关于 PCI 的论断**只到 ops 位置和文件分工** |
| ⚠️ **`virtio_mem.c` 2952 行几乎没读** | 04 §6 只列了设计债位置和 Kconfig 依赖 |
| ⚠️ **`vringh.c` 1511 行完全没读** | 04 §4 关于"它是 device 侧的库"是**从文件名和 `vringh_config_ops` 推的** |

### §5.3 第三层：推断而非观察

| 论断 | 出处 | 依据 | 为什么算推断 |
|---|---|---|---|
| **packed ring 比 split ring 快** | 02 §1.0.D · 08 §9 | 从"段数 3→1、一次缓存访问拿到归属"推出 | **没有任何性能数据** |
| **数据面零陷入是 virtio 的性能来源** | 00 §2.2 · 08 §2 | 从"通知是唯一 `notify` 调用点"推出 | **没测过 vmexit 次数** |
| **`weak_barriers=true` 在同机场景安全** | 03 §B1.2 | 从字段注释和"同一致性域"推出 | **没有跨架构验证** |
| **两个 event_idx 交叉放置有缓存好处** | 08 §4 | **纯推测** ——头文件只说了 backwards compatibility | **文档没说性能，本套自己加的解释** |
| **`virtqueue_get_buf()` 的 `len` 不可信** | 08 §6 · 00 §3.3 | 从"它是对端写进 `used_elem.len` 的"推出 | **没有找到核心校验它的代码**，也**没有找到明确要求驱动校验的文档** |
| **`-ENOSPC` 是正常背压** | 01 §9.2 · 05 §1 | 从"队列满"的语义推出 | 逻辑很硬，但**没有实测高负载行为** |
| **legacy 支持约 1000 行代码** | 03 §C5 | 把 `virtio_pci_legacy.c`(235) + `virtio_pci_legacy_dev.c`(222) 加上 feature 表/字节序的分散代码估的 | **"约"是估算，不是精确统计** |
| **`virtio_input.c` 是"最小的完整设备驱动"** | 04 §7 · 05 §0.2 | 从行数（414）和它有完整的 probe/remove/PM 推出 | **没有逐个对比 25 个驱动的行数** |

⚠️ **第 5 条（`len` 不可信）是本套最该小心的一条推断**：
**它的结论（驱动要自己检查 `len`）在逻辑上很硬**（对端写的值不能信），
⚠️ **但本套既没找到核心的校验代码，也没在规范里核对过**
⇒ **读者若要依赖它，应当自己读一个真实驱动（如 `virtio_net`）看它怎么处理。**

### §5.4 第四层：版本时效性

- **所有行号只对 6.1.158 有效**
- ⚠️ **`VIRTIO_F_RING_RESET`(40) 是较新的位** ⇒ **`virtqueue_resize()`
  和那两个可选回调（`disable_vq_and_reset` / `enable_vq_after_reset`）是新加的**
  ⇒ 更高版本上支持它的传输层会变多（03 §B6）
- **feature 位号在持续增加而上限是 63**（00 §2.5）⇒
  **§3.7 / 00 §2.5 那张位号表会过期**
- ⚠️ **`virtio_ids.h` 的设备类型编号在增加** ⇒
  06 §7.2 的十六进制换算表会过期
- `include/linux/interrupt.h` 之类外部头文件的行号对本树的 patchset 版本敏感
  （本套引用它较少）

### §5.5 ⚠️ 第五层：对端 / 树外实现（**本套特有**）

**这一层是跨边界 ABI 型子系统的强制项**（skill 阶段 1 步骤 3b）。

#### §5.5.1 边界停在哪一行

| 覆盖 | 不覆盖 |
|---|---|
| **Linux 侧 driver 角色的全部代码** | ⚠️ **对端（device 角色）的任何实现** |
| **共享内存的布局**（02 §5）——**双方共同遵守** | ⚠️ **对端何时该读写哪个字段**（协议时序） |
| **驱动侧写入共享内存的顺序与屏障**（03 §B1） | ⚠️ **对端侧的顺序要求** |
| **状态字节里驱动设的 5 个位**（00 §4.3） | ⚠️ **`NEEDS_RESET`(0x40) 何时会被对端设** |
| **feature 位在驱动侧怎么用** | ⚠️ **每个位对设备的语义要求** |

**具体的"边界那一行"是三个函数指针**：

| 边界 | 位置 | 过去之后 |
|---|---|---|
| `vq->notify(vq)` | `drivers/virtio/virtio_ring.c:200` | **传输层写寄存器 ⇒ 对端** |
| `vdev->config->xxx()` | `include/linux/virtio_config.h:95-121` | **传输层 ⇒ 对端** |
| **写 vring 之后** | 02 §5 的布局 | **对端直接读** |

#### §5.5.2 "对端行为"的论断依据是什么

⚠️ **凡是本套写"对端会…"的地方，依据只有三种**：

| 依据种类 | 例子 | 可信度 |
|---|---|---|
| **① 本树代码里的注释** | `Other side has made a mess, don't try any more`（`drivers/virtio/virtio_ring.c:166-167`）· `They are at the end for backwards compatibility`（`include/uapi/linux/virtio_ring.h:194-195`） | **较高** ——内核作者写的 |
| **② uapi 头文件的结构与常量** | 02 §5 的字节布局、六个状态位、所有 `VRING_*` 标志 | **高** ——它就是 ABI |
| ⚠️ **③ 本套的推理** | "设备可以拒绝 feature 组合"（从写-回读握手推）· "对端不守规矩时会怎样" | ⚠️ **低** ——**没有对照规范原文** |

⚠️ **本套没有读 OASIS VIRTIO 规范原文。**
⇒ **规范文本是一个独立的证据档次**（不是三等文档），
**而本套完全没有用到它** ——所有"规范规定…"的表述
**实际依据都是本树代码/注释里的间接体现**。

**三条最依赖这一层的论断**（读者要特别当心）：

| 论断 | 出处 | 实际依据 |
|---|---|---|
| **"规范规定对端在 `DRIVER_OK` 前不得使用队列"** | 03 §A7 · 05 §1.2 | **从 `CONFIG_VIRTIO_HARDEN_NOTIFICATION` 的存在和那句 `virtio vring IRQ raised before DRIVER_OK`（`drivers/virtio/virtio_ring.c:2466`）反推** |
| **"out 段必须排在 in 段之前是规范要求"** | 01 §3.1 · 05 §2.1 | **从 `virtqueue_add_sgs()` 的参数形状（`out_sgs` 在 `in_sgs` 前）反推** |
| **"`num` 必须是 2 的幂是规范要求"** | 08 §3 | **有代码注释支撑**：`We assume num is a power of 2`（`include/uapi/linux/virtio_ring.h:171`）⇒ 这条依据较强 |

#### §5.5.3 一个例外：vhost 场景下对端在本树里

**`drivers/vhost/vringh.c`(1511 行) 实现的就是 device 侧**（04 §4）。
⇒ **理论上可以用它来验证本套关于"对端行为"的推断。**
⚠️ **但本套没有读它**（§2.3、§5.2）
⇒ **这是本套最大的一个"本可以做但没做"的验证机会**，
**建议后续补做**（§9 会再提）。

---

## §6 交叉验证实例：初始假设被推翻的六条

### §6.1 ⚠️`virtio_dev_probe()` 里 `validate` 和 `finalize_features` 的次序

**初稿写的**（01 §4.3 的第一版）：
"`validate()` → `virtio_features_ok()` → `finalize_features()` → `probe()`"。

**推翻它的源码**：

```bash
awk 'NR>=237 && NR<=320 {printf "%d\t%s\n", NR, $0}' drivers/virtio/virtio.c \
  | grep -E 'finalize_features|validate|features_ok|->probe'
```

**真实次序**：
`finalize_features()`（`:282`）→ `validate()`（`:289`）→
**改了就再 `finalize_features()`（`:295`）** → `features_ok()`（`:301`）
→ `probe()`（`:305`）。

⇒ **`finalize_features()` 被调了两次，而 `validate()` 夹在中间。**
**结论（"想否掉 feature 只能在 `validate` 里做"）不变，但机制完全不同**：
不是"validate 在唯一一次 finalize 之前"，而是
**"validate 是唯一改了还会被重新写回设备的钩子"**。

**修正范围**：01 §4.3 整段重写 + 新增 §4.3.1，03 §A4.1 专门解释。
⇒ **教训：调用次序不能凭"逻辑上应该先协商后确认"推，必须 `awk` 打出行号看。**

### §6.2 ⚠️`virtio_features_ok()` 的两条检查是无条件的吗？

**初稿写的**："`VIRTIO_F_VERSION_1` 必须被协商上，否则失败"。

**推翻它的源码**（`drivers/virtio/virtio.c:171-202`）：
那两条检查在 `if (virtio_check_mem_acc_cb(dev))`（`:177`）**里面**
⇒ **只在"平台要求受限内存访问"时才生效**。

**而且紧接着有一条相反的判断**（`:191`）：
`if (!virtio_has_feature(dev, VIRTIO_F_VERSION_1)) return 0;`
⇒ **没有 `VERSION_1` 反而直接放行**（那就是 legacy 设备）。

⚠️ **所以 `VIRTIO_F_VERSION_1` 在同一个函数里被检查两次，含义相反**：
第 ② 步是"受限内存场景下必须有"（硬性），
第 ③ 步是"没有就说明是 legacy，走宽松路径"。

⇒ **这是本子系统一处真正容易看错的地方**，01 §4.3.1 专门画了表。
**而且它连着 04 §9.2 那个 arm64 结论**：
arm64 不在 anchor 的强制路径里 ⇒ **arm64 上第 ② 步几乎不触发。**

### §6.3 ⚠️`virtio_driver` 有几个实现？——23 → 25

**初始统计**：`grep -rn 'struct virtio_driver [a-z_]* = {'` → **23**。

**推翻它的检查**：换成 `[a-z_0-9]*` 得到 **25**，差的两个是：

```bash
comm -13 <(grep -rln 'struct virtio_driver [a-z_]* = {' --include=*.c . | sort) \
         <(grep -rln 'struct virtio_driver [a-z_0-9]* = {' --include=*.c . | sort)
# → drivers/i2c/busses/i2c-virtio.c
#   net/9p/trans_virtio.c
```

**原因**：**字符类 `[a-z_]` 不含数字**，
而这两个文件的驱动变量名里有数字（`i2c` 的 `2`、`9p` 的 `9`）。

**三种口径复核后一致是 25**（§3.8）⇒ 可信。
⇒ **教训：写字符类时别忘 `0-9`** ——内核符号名里数字很常见
（`i2c` / `9p` / `mmc` / `sha256` …）。**这条比 timer 那轮的
"同名字段"陷阱更隐蔽，因为它不是虚高而是虚低。**

### §6.4 ⚠️ 设计债有几条？——14 → 13

**初始统计**：`grep -nE '(FIXME|TODO|XXX|REVISIT|HACK)'` → **14** 条。

**推翻它的检查**：逐个打开，发现 `drivers/virtio/virtio.c:369` 是

```c
	char compat[] = "virtio,deviceXXXXXXXX";
```

⇒ **那是 `snprintf` 的占位字符串，不是注释**（03 §A2.1）。

**正确口径**：`grep -vE '"[^"]*XXX'` 排掉字符串
⇒ **13 条**（`virtio_ring.c` 8 + `virtio_mem.c` 5 + 核心 **0**）。

⇒ **教训：`grep XXX` 会命中占位符字符串。**
**这条正好解释了另一个结论**：核心 `virtio.c` 的自陈债是 **0**，
**如果不排这一条，会误以为核心有 1 条债。**

### §6.5 ⚠️ arm64 的 dts 里有 virtio-mmio 节点吗？——**有**

**初稿写的**（06 §4 第一版）："本树 `arch/arm64/boot/dts/` 里没有
virtio-mmio 节点，所以下面这个节点是按绑定文档手写的"。

**推翻它的一条命令**：

```bash
grep -rl 'virtio,mmio' arch/arm64/boot/dts/
# → arch/arm64/boot/dts/arm/foundation-v8.dtsi
#   arch/arm64/boot/dts/arm/rtsm_ve-motherboard.dtsi
#   arch/arm64/boot/dts/arm/rtsm_ve-motherboard-rs2.dtsi
```

**三个文件都有**，而且内容正是最小形态：

```dts
	virtio@130000 {
		compatible = "virtio,mmio";
		reg = <0x130000 0x200>;
		interrupts = <42>;
	};
```
—— `arch/arm64/boot/dts/arm/foundation-v8.dtsi:223-227`

⇒ **06 §4 从"手写示例"改成了"本树真实节点，可以 diff"**。
**顺带发现 `reg` 长度 `0x200` 和我原先猜的一致，中断是裸数字 42
（因为该平台 `#interrupt-cells = <1>`，`arch/arm64/boot/dts/arm/foundation-v8.dtsi:128`）。**

⇒ **教训：skill 规则"举例一律用 in-tree 真实节点"不是可选的** ——
**在断言"树里没有"之前必须 grep 一次**（这也是规则 19 的全称断言要求）。

### §6.6 ⚠️`status` sysfs 的格式是 `0xf` 还是 `0x0000000f`？

**初稿写的**（06 §9.2 第一版）："正常应是 `0xf`"。

**推翻它的源码**（`drivers/virtio/virtio.c:34`）：

```c
	return sprintf(buf, "0x%08x\n", dev->config->get_status(dev));
```

⇒ **`%08x` ⇒ 输出是 `0x0000000f`**，不是 `0xf`。

**同样核对了另外四个属性的格式串**（§3.6）：
`device`/`vendor` 是 `0x%04x`（`:18`/`:26`）、
`modalias` 是 `virtio:d%08Xv%08X`（`:42`）、
`features` 是逐位 `%c`（`:57-59`）。

⇒ **教训：凡是文档里写"你会看到 X"的地方，必须回去读格式串。**
这条影响的是"读者照抄命令后能不能对上输出"，不影响结论，
**但它正是 07 篇整篇的可信度基础。**

---

## §7 逐篇取证表

### §7.1 逐篇关键论断 → 出处

| 篇 | 关键论断 | 出处 |
|---|---|---|
| 00 | virtio 有真总线但另一端是软件 | `drivers/virtio/virtio.c:343` + 25 个 driver（§3.8） |
| 00 | feature 位号史（27→32→34→40） | `include/uapi/linux/virtio_config.h:63` · `:67` · `:83` · `:105` |
| 00 | `VIRTIO_F_IOMMU_PLATFORM` 是 `#define` 别名 ⇒ 不会分叉 | `include/uapi/linux/virtio_config.h:78-79` |
| 01 | 导出符号 80 个，分层清晰 | §3.2 的逐文件计数 |
| 01 | 17 个 `virtio_config_ops` 回调 | `include/linux/virtio_config.h:95-121` 逐行数 |
| 01 | probe 的 12 步（含两次 `finalize_features`） | `drivers/virtio/virtio.c:247`–`:320` 逐行（§6.1） |
| 01 | 写-回读握手 | `drivers/virtio/virtio.c:194-198` |
| 02 | **vring 的字节级布局** | `include/uapi/linux/virtio_ring.h:170-193` 的注释 + `vring_init()`(`:199`) + `vring_size()`(`:209`) |
| 02 | 两个 event_idx 插在对方段末尾 | `include/uapi/linux/virtio_ring.h:194-197` |
| 02 | 三个对齐常量用 `typedef` 施加（只有它能降对齐） | `include/uapi/linux/virtio_ring.h:92-94` · `:139-155` |
| 02 | packed 用 `__le*`，split 用 `__virtio*` | `include/uapi/linux/virtio_ring.h:239-248` vs `:107-112` |
| 02 | `virtqueue` 无锁，责任在驱动 | `drivers/virtio/virtio_ring.c:26` 的注释 |
| 03 | `register_virtio_device()` 先复位（且注释说"顺便测这条路径"） | `drivers/virtio/virtio.c:418` 一带 |
| 03 | `remove()` 必须自己复位 | `drivers/virtio/virtio.c:335` 的 `WARN_ON_ONCE` |
| 03 | 三层不信任对端 | `drivers/virtio/virtio_ring.c:94` · `:100` · `:103-104` · `:57-62` |
| 03 | 设计债 13 条，核心 0 条 | §3.5（§6.4 修正后） |
| 04 | MMIO 寄存器布局 | `include/uapi/linux/virtio_mmio.h:43`–`:134` |
| 04 | anchor 的真使用者只有 AMD SEV + s390 PV | §2.6（跨出子系统目录查 `arch/`） |
| 05 | 骨架的 9 个对照点 | `drivers/virtio/virtio_input.c` 的 9 个行号（05 §1.1） |
| 06 | `virtio,deviceNN` 是十六进制 | `drivers/virtio/virtio.c:388` 的 `%x` + 绑定的正则 |
| 06 | 只能有一个 DT 子节点 | `drivers/virtio/virtio.c:381` 的 `WARN_ON(count > 1)` |
| 06 | arm64 真实节点 | `arch/arm64/boot/dts/arm/foundation-v8.dtsi:223-227`（§6.5） |
| 07 | 零 tracepoint / 零 debugfs / 零 procfs | §3.4 的三条命令 |
| 07 | 5 个 sysfs 属性全 RO 及其格式串 | §3.6（§6.6 修正后） |
| 07 | `DEBUG` 下 `BAD_RING` 变 `BUG()` | `drivers/virtio/virtio_ring.c:20-25` vs `:57-62` |
| 08 | "num 必须是 2 的幂"有代码注释支撑 | `include/uapi/linux/virtio_ring.h:171` |

### §7.2 三个自检脚本的最终结果

| 脚本 | 结果 |
|---|---|
| `check_line_refs.py` | 见 §7.5 |
| `check_xrefs.py` | 见 §7.5 |
| `check_doc_coverage.sh` | **【必查】"共 0 篇"是真通过**（§1.4 三种变体全试过 ⇒ 类别 (d)）；**【参考】3 篇全部被引用** |

两个 `.ignore` 文件**都不存在** ⇒ **零豁免**：

```bash
ls output/virtio/.xref-ignore output/virtio/.lineref-ignore
# → 两条 No such file or directory
```

### §7.3 本套的自我修正记录（九条）

**本套的前九篇和 99 是同一个会话写的**，所以记录完整。

| # | 修正 | 严重度 | 怎么发现的 |
|---|---|---|---|
| 1 | **probe 次序写错**：以为 `validate` → `features_ok` → `finalize_features`，实际 `finalize_features`(`:282`) → `validate`(`:289`) → **再 `finalize_features`**(`:295`) → `features_ok`(`:301`) | **ERROR** | **写 03 §A 前用 `awk` 打行号看实际次序** |
| 2 | **`virtio_features_ok()` 的两条检查不是无条件的** ——它们在 `if (virtio_check_mem_acc_cb(dev))` 里面；而且 `VERSION_1` 在同一函数里被检查两次含义相反 | **ERROR** | **打开 `:171-202` 全文读** |
| 3 | `virtio_config_ops` 回调数 **18 → 17** | 数字错 | **逐行数 `:96`–`:120`** |
| 4 | `virtio_driver` 实现数 **23 → 25**（字符类漏了 `0-9`） | **计数虚低** | **三种口径复核**（§6.3） |
| 5 | 设计债 **14 → 13**（`XXX` 占位串误判） | 计数虚高 | **逐个打开命中行**（§6.4） |
| 6 | **"arm64 dts 里没有 virtio-mmio" → 有 3 个文件** ⇒ 06 §4 从手写示例改为本树真实节点 | **全称断言错** | **`grep -rl 'virtio,mmio' arch/arm64/boot/dts/`**（§6.5） |
| 7 | `status` sysfs 格式 `0xf` → **`0x0000000f`** | 输出格式错 | **回去读 `sprintf` 格式串**（§6.6） |
| 8 | `snprintf` 的行号 `:389` → **`:388`** | 行号偏 1 | **`awk` 打行号核对** |
| 9 | `virtio_device_of_init()` 从"形如 `virtio,device1`"改为**十六进制** + 补齐 `WARN_ON(count > 1)` 等 6 步逻辑 | 不完整 | 同上 |

**第 1、2 条最值得记**：两条都是
**"逻辑上应该这样"和"代码实际这样"的差距**。
① 直觉上"先协商确认再写回设备"更合理，实际是"先写回再让 validate 改再写回"；
② 直觉上"必须有 VERSION_1"是硬要求，实际只在受限内存场景下才是。
⇒ **教训：调用次序和条件分支必须打行号逐行看，不能凭设计直觉推。**

**第 4、5 条是一对相反的计数错误**：
**一个虚低（字符类漏数字）、一个虚高（`XXX` 命中字符串）**
⇒ **计数错误可以往两个方向偏，不能只防一头。**

### §7.4 ⚠️ 本轮自己新产生的问题

按 skill 第 15 条（自审的对象必须包含自审这一轮新写的文字）：

- 写 §3.1 时**差点把文件数写成 14** ——`drivers/virtio/` 有 **15 个文件**
  （14 个 `.c` + 1 个 `.h`），而错误码统计范围是 **14 个 `.c`**。
  ⇒ **已在 §3 开头显式说明这个差别**，**不说明就是两个对不上的数字**。
- 写 §5.3 时**发现"`len` 不可信"这条论断没有代码依据** ——
  它在逻辑上很硬，但**既没找到核心校验它的代码，也没读规范**
  ⇒ **当场降级到推断层并写明"读者应自己读一个真实驱动确认"**。
  **这正是 skill 规则 17 说的"填格子会让空缺掉出来"** ——
  在 §5.3 那张表里必须填"依据"一列，才发现这一格填不出来。
- ⚠️ 写 §5.5.2 时**发现整套文档里"规范规定…"的表述都没有对照规范原文**
  ⇒ **列了三条最依赖这一层的论断，并标出各自的实际依据**。
  **其中两条是"从代码形状反推规范"，只有一条有代码注释直接支撑。**
- 写 §1.5 时**发现本套没有"文档 vs 代码冲突"可写**（因为没有树内概念文档）
  ⇒ **改成写两条"绑定文档 vs 代码一致"的交叉验证**，
  并显式说明"本套缺少 of/timer 那种以代码为准的处置案例"。

### §7.5 定稿后重跑的脚本数字

| 指标 | 值 |
|---|---|
| `check_line_refs.py` 扫到的唯一引用 | 见下方重跑命令 |
| `check_xrefs.py` 命中的跨篇引用 | 同上 |
| 忽略项 | 0 / 0 |

⚠️ **数字故意不写死**：本篇自己就在被这两个脚本扫描。

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
python3 $S/check_xrefs.py     output/virtio
python3 $S/check_line_refs.py output/virtio .
```

**验收标准**：两条都必须报"干净 / 0 失效"。

---

## §8 工具坑

### §8.1 `check_doc_coverage.sh` 的四种情况（本套补齐了最后一种）

**三套文档集下来，"共 0 篇"这个输出对应四种完全不同的现实**：

| 情况 | 现实 | 哪套 | 怎么区分 |
|---|---|---|---|
| (a) 文件名带连字符 | 有文档，目录对了名字不匹配 | pinctrl（`pin-control.rst`） | `ls Documentation/driver-api/` |
| (b) 目录名完全不同 | 有文档，按"这件事"命名 | of（`Documentation/devicetree/`） | 想这个子系统的"人类名字" |
| (c) **只差一个复数 s** | 有文档 | timer（`Documentation/timers/`） | `ls -d Documentation/${SUB}s` |
| (d) **真的没有** | **规范/文档在树外** | **virtio**（本套）· cdev · mailbox · reset | **前三种全试过之后** |

**完整判别流程**（回写 skill 规则 28 的建议）：

```bash
SUB=virtio
ls -d Documentation/$SUB Documentation/${SUB}s Documentation/driver-api/$SUB* 2>/dev/null
ls Documentation/ | grep -i "$SUB"
ls Documentation/driver-api/ | grep -i "$SUB"
find Documentation -iname "*$SUB*"          # 最后这条最全
# 都空 ⇒ 类别 (d)，并在 99 里写明"规范/文档在哪"（本套：OASIS 规范）
```

⚠️ **(d) 不等于"不用登记"** ——**必须写明"那么真正的文档在哪"**
（本套 §1.4 写的是 OASIS VIRTIO 规范）⇒ **否则读者会以为这个子系统没有设计文档。**

### §8.2 跨文档集引用：⚠️ 本套仍然踩了，而且踩在"解释它"的那段文字里

**of / timer 两套都踩过"引用对方章节号被 `check_xrefs.py` 判失效"**，
处置一律是**消除触发条件**：不写对方的章节号，改成按主题指路。

⚠️ **本套开写时就知道这条，结果还是踩了三次**：

| 踩点 | 情况 |
|---|---|
| 06 篇的属性表里 | 提到 of 那套的一个小节时**写了编号** |
| ⚠️ **本节自己（两处）** | **在"自查发现 06 踩了"这句话里把那个编号又写了两遍** |

⇒ **这是同一个机理的第 4、5 次发作**（of 两次、timer 一次、本套三次），
**而且和 skill 里记的 spi 那次一样：第 N 遍是在"解释前 N-1 遍"的文字里犯的。**

**根因**：**描述一个"格式错误"时，最自然的写法就是把那个格式抄一遍。**
⇒ **所以纪律必须是"连举例都不许写出可解析的编号模式"**，
**只能用文字描述"某套文档的某一篇的某个小节"。**

**本节的处置**：三处全部改成按主题指路，不留任何编号。

### §8.3 `check_line_refs.py` 的两个已知行为（本套都绕过了）

| 行为 | 本套的情况 |
|---|---|
| **按逗号切基名** ⇒ 引用 `bindings/**/*.yaml`（文件名带逗号）必然 UNRESOLVED | **本套引用 3 篇绑定时一律不写行号**，改用 YAML 键名（`required:` 段、`examples` 段）⇒ **零触发** |
| **AMBIGUOUS（同名头文件）** | **本套出现过风险**：`virtio_config.h` / `virtio_ring.h` / `virtio_net.h` / `virtio_console.h` **在 `include/linux/` 和 `include/uapi/linux/` 各有一个** ⇒ **所以本套一律写全路径** ——⚠️ **连"反面例子"都不能写出裸文件名加冒号加数字的形式，否则脚本会当真**（§8.4） |

⚠️ **第二条是本套特有的高风险**（02 §1.0.C）：
**四个头文件名在两个目录里重名** ⇒
**只要有一处写成裸文件名，脚本就会猜，而且可能猜错目录。**
⇒ **这也是 §7.5 能全绿的原因之一。**

### §8.4 99 篇的三个自污染陷阱（本篇全部绕过）

| 陷阱 | 本篇怎么绕 |
|---|---|
| 引述脚本的失败行 | §7.5 只描述"必须报干净" |
| 抄 `grep -c` 的原生输出 | 全篇统计写成表格两列或 `→ 数字` |
| 举例说明"失效引用长什么样" | §8.2 用文字描述 |

### §8.5 本套遇到的三个计数陷阱

| 陷阱 | 实例 | 正确口径 |
|---|---|---|
| **字符类漏数字（虚低）** | `[a-z_]` 漏掉 `i2c-virtio.c` / `trans_virtio.c` ⇒ 23 而非 25 | **一律用 `[a-z_0-9]`** |
| **`XXX` 命中占位字符串（虚高）** | `"virtio,deviceXXXXXXXX"` ⇒ 14 而非 13 | **`grep -vE '"[^"]*XXX'`** |
| **子串包含** | `-ENOTSUPP` vs `-EOPNOTSUPP` | **必须 `grep -w`** |

⇒ **三条印证 skill 规则 30：计数错误三个脚本一个都抓不到。**

### §8.6 环境坑速查

| 坑 | 处置 |
|---|---|
| 单次写入过大撞网关超时 | **每篇拆 2–3 次写入** |
| 上下文撞 1M 硬上限 ⇒ 会话永久 400 | 靠 `output/PROGRESS.md` 台账续跑 |
| ⚠️ **脚本第二个参数是内核树根** | **一律在内核树根下跑**：`python3 $S/check_line_refs.py output/virtio .`；**在 `output/virtio/` 里跑会产生一堆假 UNRESOLVED** |
| **`cd` 到子目录后再跑脚本** | **同一条 Bash 命令里先 `cd` 过子目录，后面的相对路径就废了** ——本轮踩过 3 次 |
| 源码树只读 | 所有产出进 `output/virtio/` |

---

## §9 rule-29 对照：本套没有对照集

```bash
ls output/qoder/virtio
# → No such file or directory
```

**`output/qoder/` 下只有 clk / dma / gpio / irq / pinctrl / spi 六套**
⇒ 本套**没有做**双套独立对照。

**§7.3 那九条修正里，有四条正是 rule-29 最擅长抓的类型**：

| 修正 | 为什么独立产出也可能犯 |
|---|---|
| #1（probe 次序） | **"先协商后确认"的设计直觉太强** ——两套独立产出很可能同样写错 |
| #2（两条检查不是无条件的） | **`if` 的缩进层次容易看漏** |
| #6（arm64 dts 里没有 virtio-mmio） | **"虚拟设备不会写在 dts 里"这个直觉也很强** |
| #7（`status` 格式） | **谁都会想当然写 `0xf`** |

⇒ **没有对照集的子系统，"打行号逐行读 + 填格子"这两步不能省。**
**本套那九条全部靠这两步抓到，没有一条来自重读正文。**

**另外，本套有一个"本可以做但没做"的验证机会**（§5.5.3）：
**`drivers/vhost/vringh.c`(1511 行) 是本树里的 device 侧实现**，
⇒ **读它就能验证本套关于"对端行为"的大部分推断**（§5.5.2 的第 ③ 类依据）。
**建议后续补做** ——**它相当于一个"半个 rule-29 对照集"**：
不是另一套文档，而是**协议另一侧的实现**。

---

## §10 这套文档怎么用

| 你的处境 | 先读 |
|---|---|
| 完全不懂 virtio | 08 篇 → 00 §2 → 02 §1.0 |
| 要写 virtio 设备驱动 | 05 §1 → 01 §2–§5 → 02 §5 |
| 要看共享内存怎么排 | **02 §5** |
| 设备起不来 / 协商失败 | 07 §3.2 → 07 §2.2 |
| 写 DT | 06 §4 + 06 §7 |
| 要写传输层 | 05 §5 → 01 §6 |
| 要改 virtio 核心 | 02 → 03 → 04 → **本篇 §5（五层）** |
| 隔几个月回来复习 | **只读 00 篇** |

### §10.1 本篇最该记住的五条

1. ⚠️ **`drivers/virtio` 在这棵树里是干净的，但同一棵树的 timer 是改过的**
   ⇒ **基线必须逐子系统查**（§1.1）
2. ⚠️ **§5 有第五层：对端不在本树。** 所有"对端会…"的论断依据只有
   代码注释 / uapi 结构 / 本套推理三种，**本套没有读 OASIS 规范原文**（§5.5.2）
3. ⚠️ **RT 之外最该小心的一条推断是"`get_buf()` 的 `len` 不可信"**（§5.3）——
   逻辑很硬但**没有代码依据**，要依赖它请自己读一个真实驱动
4. **三个计数口径必须按 §8.5 的方式数**：
   字符类带 `0-9`、排掉 `XXX` 字符串、`grep -w`
5. **`check_doc_coverage.sh` 的"共 0 篇"有四种现实**（§8.1）——
   **本套是第四种（真的没有），必须写明"文档在树外哪里"**

### §10.2 ⚠️ 后续最该补的一件事

**读 `drivers/vhost/vringh.c`(1511 行)** ——
**它是本树里的 device 侧实现**，能把 §5.5.2 里那些
"从代码形状反推规范"的论断变成"有对照实现支撑"的论断。
⇒ **这是本套最大的一个可改进点**（§5.5.3、§9）。
