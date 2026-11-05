---
title: 取证附录
---

# dma-mapping 取证附录

这一篇不讲机制，它回答三个问题：前面九篇的每条论断来自哪里、每个数字怎么重跑、哪些结论**没有**被验证过。它的失效方式是「过期」而不是「写错」——只要有一个数字对不上，整套文档就从「取证」降级成「回忆」。

## §1 基线与判定登记

### §1.1 基线

| 项 | 命令 | 输出 |
| --- | --- | --- |
| 提交 | `git log -1 --format='%H %s'` | `f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158` |
| 本子系统有没有被本树补丁改过 | `git status --porcelain -- kernel/dma include/linux/dma-mapping.h include/linux/dma-map-ops.h include/linux/dma-direct.h include/linux/swiotlb.h \| wc -l` | `0` |

第二行是这套文档定性的关键：**本树在 `kernel/dma/` 和四个主头文件上一行未改**，所以全部内容就是上游 6.1.158 的原样，不含任何本地修改。这也意味着上游 6.1.y 的后续变更会直接让本套文档过期，判据见 §5.4。

⚠️ 上面第二条只覆盖了 `kernel/dma/` 和四个头文件。本套文档还大量引用 `arch/arm64/mm/dma-mapping.c`、`drivers/iommu/dma-iommu.c`、`mm/cma*.c`、`drivers/of/`，这些文件**不在**那条命令的范围里。补跑一条：

```bash
git status --porcelain -- arch/arm64/mm/dma-mapping.c drivers/iommu/dma-iommu.c \
    mm/cma.c mm/cma_debug.c mm/cma_sysfs.c drivers/of/device.c drivers/of/address.c \
    drivers/of/of_reserved_mem.c include/linux/scatterlist.h | wc -l
```

### §1.2 类型判定的两道门

| 门 | 命令 | 实际输出 | 判读 |
| --- | --- | --- | --- |
| 门 1 | `grep -rn "struct bus_type" kernel/dma/` | 唯一一条 `kernel/dma/debug.c:875:void dma_debug_add_bus(struct bus_type *bus)` | 这是**形参**不是定义 → 没有自己的 `bus_type` |
| 门 2 | `ls include/uapi/linux/dma*` | 无 DMA 映射相关 uapi 头文件 | 无跨边界 ABI |

判定：**框架型**。门 1 那条输出正是最容易误判的地方——只看 `grep -c` 会得到 1，看起来像「有 bus_type」。必须打开那一行看它是定义还是形参。

唯一的例外登记在案：`include/linux/map_benchmark.h:9` 定义了一个 ioctl 号，但这个头文件**不在** `include/uapi/linux/` 下，而它的消费者是 `tools/testing/selftests/dma/dma_map_benchmark.c`。这是本子系统唯一的用户态接口，不足以改变类型判定（它是测试工具接口，不是设备驱动 ABI）。

### §1.3 Documentation 覆盖登记

⚠️ **`check_doc_coverage.sh dma-mapping output/dma-mapping .` 报「没找到 `Documentation/dma-mapping/`、`driver-api/dma-mapping*` 或 `bindings/dma-mapping/`」，这是脚本的假阴性，不是「没有官方文档」。** 本子系统的官方文档在 `Documentation/core-api/` 下，文件名以 `dma-api` 开头而不是 `dma-mapping`。手工枚举命令：

```bash
find Documentation -iname "*dma*" -type f | sort
```

登记结果：

| 文件 | 行数 | 读到什么程度 | 纳入了什么 |
| --- | --- | --- | --- |
| `Documentation/core-api/dma-api.rst` | 846 | 全文 | API 语义、返回值约定、方向枚举含义 → 01 §1 / §2 / §5 |
| `Documentation/core-api/dma-api-howto.rst` | 915 | 全文 | 所有权约定、DMA 安全缓冲区规则、常见错误 → 02 §1.0、04 §1、05 §3 |
| `Documentation/core-api/dma-attributes.rst` | 132 | 全文 | `DMA_ATTR_*` 每一位的含义 → 01 §4 |
| `Documentation/core-api/dma-isa-lpc.rst` | 152 | 扫读确认主题 | ⚠️ **未纳入**：全篇讲 ISA / LPC 总线的 x86 DMA 控制器。裁剪理由记在 §2.7 |
| `Documentation/admin-guide/mm/cma_debugfs.rst` | 25 | 全文 | CMA debugfs 文件清单，与 `mm/cma_debug.c` 逐项对照后纳入 07 §3.4 |
| `Documentation/ABI/testing/sysfs-kernel-mm-cma` | 25 | 全文 | ⚠️ **本轮补读**，发现 07 §3.4 原先漏了 CMA 的 sysfs 出口，已补入（详见 §4 第 4 条） |
| `Documentation/devicetree/bindings/reserved-memory/shared-dma-pool.yaml` | — | 全文 | 属性语义 → 06 §4 / §5 / §6 |
| `Documentation/i2c/dma-considerations.rst` | 71 | 全文 | ⚠️ **未纳入**：内容归属 i2c 子系统（`I2C_M_DMA_SAFE` 标志、小传输不值得走 DMA 的阈值经验）。它对「DMA 安全缓冲区」的补充与 05 §3 已有的规则不冲突，但引入会把 05 的角色边界弄乱 |
| `Documentation/driver-api/usb/dma.rst` | 136 | 扫读确认主题 | ⚠️ **未纳入**：讲 USB 子系统自己的 DMA 封装（`usb_alloc_coherent` 一族），归属 USB |
| `Documentation/driver-api/pci/p2pdma.rst` | 131 | 扫读确认主题 | ⚠️ **未纳入**：peer-to-peer DMA，涉及本子系统的 `dma_pci_p2pdma_supported()` 和 `-EREMOTEIO` 返回码（01 §2 已列），但完整机制是 PCI 侧的 |

⚠️ **文档与代码冲突的地方以代码为准。** 本轮没有发现 `dma-api.rst` 与代码的直接冲突，但发现一处措辞落后：`dma-api.rst` 讲 `dma_map_sg()` 的返回约定，而本版本已经推荐用 `dma_map_sgtable()`（返回约定相反）。这一点在 01 §2 和 02 §3.1 里显式对照了三层三种约定。

## §2 方法与范围

### §2.1 一等证据：逐行详读的范围

「一等证据」= 打开文件、从头到尾读完、文中每条论断都能指回具体行。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `kernel/dma/mapping.c` | 831 | 全文 |
| `kernel/dma/direct.c` | 657 | 全文 |
| `kernel/dma/direct.h` | 126 | 全文 |
| `kernel/dma/swiotlb.c` | 1112 | 全文 |
| `kernel/dma/debug.c` | 1608 | 全文 |
| `kernel/dma/debug.h` | 130 | 全文 |
| `kernel/dma/pool.c` | 295 | 全文 |
| `kernel/dma/contiguous.c` | 441 | 全文 |
| `kernel/dma/coherent.c` | 403 | 全文 |
| `kernel/dma/remap.c` | 70 | 全文 |
| `kernel/dma/ops_helpers.c` | 93 | 全文 |
| `kernel/dma/dummy.c` | 38 | 全文 |
| `kernel/dma/map_benchmark.c` | 378 | 全文 |
| `kernel/dma/Kconfig` / `kernel/dma/Makefile` | — | 全文 |
| `include/linux/dma-mapping.h` | 611 | 全文 |
| `include/linux/dma-map-ops.h` | 449 | 全文 |
| `include/linux/dma-direct.h` | 127 | 全文 |
| `include/linux/swiotlb.h` | 190 | 全文 |
| `include/linux/map_benchmark.h` | 31 | 全文 |
| `arch/arm64/mm/dma-mapping.c` | 80 | 全文 |
| `include/trace/events/swiotlb.h` | — | 全文 |

`kernel/dma/` 的 11 个 `.c` 加 2 个 `.h` 全部在一等证据里，合计 6182 行（`wc -l kernel/dma/*.c kernel/dma/*.h | tail -1`）。这是选「全读」而不是「读符号表」的依据：核心不到万行，读完的成本低于反复回查的成本。

### §2.2 二等证据：只读了特定区段

「二等证据」= 没有读全文，只详读了与本套文档论断相关的若干区段。**下表的「详读了哪几行」是这一等级的核心信息**——超出这些区段的任何断言都不该出现在文档里。

| 文件 | 详读区段 | 用在哪 |
| --- | --- | --- |
| `drivers/iommu/dma-iommu.c` | `:1056-1160`（`__finalise_sg()` 与相邻的 unmap）、`:1186-1300`（`iommu_dma_map_sg()`）、`:1558-1580`（`iommu_dma_ops` 定义） | 02 §3.2、08 §6 的 sg 合并 |
| `mm/cma.c` | `:100-130`（区初始化与 `init_cma_reserved_pageblock()` 调用点）、`:440-490`（`cma_alloc()` 的迁移与重试）、`:505-520`（sysfs 计数点） | 08 §8 |
| `mm/page_alloc.c` | `:2310-2330`（`init_cma_reserved_pageblock()`）、`:2598-2606`（`__rmqueue_cma_fallback()`） | 08 §8 |
| `mm/cma_debug.c` | `:155-190`（debugfs 文件创建） | 07 §3.4 |
| `mm/cma_sysfs.c` | `:28-50`（两个 show 回调）、`:70-105`（初始化） | 07 §3.4 |
| `mm/Kconfig` | `:860-890`（CMA 相关）、`:970-990`（`ZONE_DMA` / `ZONE_DMA32`） | 00 §2.3、07 §8.1 |
| `drivers/of/device.c` | `:60-90`、`:114-223`（`of_dma_configure_id()`） | 03 §A2、06 §1 |
| `drivers/of/address.c` | `:969-1044`（`of_dma_get_range()`） | 06 §2 |
| `drivers/of/of_reserved_mem.c` | `:330-360` | 06 §4 |
| `include/linux/scatterlist.h` | `:35-60`（`sg_table` 定义）、`:170-185` 与 `:570-585`（两个 `orig_nents` 遍历宏） | 02 §5、08 §6 |
| `include/linux/device.h` | `:495-610`（`struct device` 的 DMA 字段与其注释） | 02 §2 |
| `arch/arm64/include/asm/pgtable.h` | `:555-585`（五个 `pgprot_*` 宏与那段 Normal-NC 注释） | 08 §2 |
| `arch/arm64/include/asm/cache.h` | `:20-30`、`:70-80` | 08 §9 |
| `arch/arm64/include/asm/memory.h` | `:130-150`（`MT_*` 编号） | 08 §2 |
| `arch/arm64/mm/init.c` | `:266-457`（三笔内存预留） | 03 §A1 |
| `drivers/pci/pci-driver.c` | `:1735-1750`（`dma_debug_add_bus()` 调用点） | 07 §4.4 |
| `include/linux/mem_encrypt.h` | `:20-35` | 08 §1 |
| `include/linux/mmzone.h` | `:1355-1370`（`has_managed_dma()`） | 07 §8.1 |
| `kernel/dma/../../mm/cma.h` | `:40-55`（两个计数函数的声明与空实现） | 07 §3.4 |

⚠️ **`drivers/iommu/dma-iommu.c` 全文 1600 行以上，本套文档只详读了约 250 行。** 任何关于 IOMMU 路径的完整性论断（「IOMMU 侧只做了 X」）都不成立，文档里也刻意没有写这类话。IOMMU 路径的边界声明见 §5.2。

### §2.3 三等证据：Documentation

清单和读到什么程度见 §1.3。三等证据的处置原则：**只用来提取设计意图和已知陷阱，不用来确认任何行为**。凡是从文档里读到的行为断言，都回到代码里逐条确认过；本轮没有出现「文档说 A、代码是 B」的冲突，但也没有把任何文档原话当成事实直接搬进正文。

### §2.4 索引工具的使用边界

cbm（codebase-memory）只用于**导航和找候选**，没有一条论断以它的输出为依据。理由是这个子系统的两个核心机制正好落在它的两个盲区上：

- **函数指针间接调用**：`struct dma_map_ops` 的 24 个回调全是指针，`dma_map_page()` → `ops->map_page()` 这一跳索引跟不过去
- **宏**：`DMA_ATTR_*`、`pgprot_dmacoherent`、`__sme_set`、`dev_WARN_ONCE` 全是宏

所有取证动作用 `grep -rn` / `awk` / `sed` 直接对源码做。

### §2.5 规模统计的重跑命令

```bash
cd <ktree>
wc -l kernel/dma/*.c kernel/dma/*.h | tail -1           # → 6182 total
wc -l kernel/dma/*.c kernel/dma/*.h | sort -n           # 逐文件，最大 debug.c 1608
wc -l include/linux/dma-mapping.h include/linux/dma-map-ops.h \
      include/linux/dma-direct.h include/linux/swiotlb.h      # → 611/449/127/190
grep -c "^config " kernel/dma/Kconfig                   # → 37
```

### §2.6 能力普查的重跑命令

| 论断 | 命令 | 输出 |
| --- | --- | --- |
| 对外导出 38 个符号 | `grep -rhoE "EXPORT_SYMBOL(_GPL)?\(" kernel/dma/*.c \| wc -l` | 38 |
| `dma_map_ops` 有 24 个回调 + 1 个标志字段 | `awk '/^struct dma_map_ops \{/,/^\};/' include/linux/dma-map-ops.h \| grep -cE "^[[:space:]]+[a-z_].*\(\*"` | 24（`flags` 不是指针，不计入，合计 25 个成员） |
| 全树 24 个 `dma_map_ops` 实例 | `grep -rn "struct dma_map_ops [a-z_0-9]* = {" --include=*.c . \| wc -l` | 24 |
| 全子系统只有 1 个 tracepoint 触发点 | `grep -rn "trace_" kernel/dma/*.c` | ⚠️ **3 条命中，但只有 1 条是 tracepoint**（详见 §4 第 1 条） |
| 没有任何故障注入 | `grep -rn "should_fail\|fault_attr\|DECLARE_FAULT_ATTR\|ALLOW_ERROR_INJECTION" kernel/dma/ \| wc -l` | 0 |
| `dma_debug_add_bus()` 全树只有 2 个调用者 | `grep -rn "dma_debug_add_bus(" --include=*.c . \| grep -v "kernel/dma/debug.c"` | `drivers/pci/pci-driver.c:1744`、`arch/powerpc/platforms/pseries/vio.c:1726` |
| arm64 dts 里 52 个文件用 `dma-ranges` | `grep -rl "dma-ranges" arch/arm64/boot/dts/ \| wc -l` | 52 |
| arm64 dts 里 45 个文件用 `dma-coherent` | `grep -rl "dma-coherent" arch/arm64/boot/dts/ \| wc -l` | 45 |
| arm64 dts 里 54 个文件用 `memory-region` | `grep -rl "memory-region" arch/arm64/boot/dts/ \| wc -l` | 54 |
| arm64 没有接管 `phys_to_dma` | `ls arch/arm64/include/asm/dma-direct.h`（不存在）+ `grep -rn "select ARCH_HAS_PHYS_TO_DMA" arch/`（只有 powerpc / mips） | 两条都符合 |
| arm64 上两个 `_for_cpu` 辅助钩子是空的 | `grep -rn "ARCH_HAS_SYNC_DMA_FOR_CPU_ALL\|ARCH_HAS_DMA_MARK_CLEAN" arch/arm64/` | 空输出 |

⚠️ 三条 dts 统计用的是 `grep -rl ... | wc -l`，口径是**文件数**而不是节点数。同一个 `.dtsi` 里出现十次也只算一个文件，而一个 `.dtsi` 被多块板子 include 时只算一次。要数节点得换 `grep -rc` 再求和，那是另一个数字，本套文档没有用过。

### §2.7 arm64-only 裁掉了什么

⚠️ 下面这些内容在源码里真实存在，是**刻意**没写进文档的，不是遗漏。

| 裁掉的内容 | 在哪 | 理由 |
| --- | --- | --- |
| ISA / LPC 总线的 DMA | `Documentation/core-api/dma-isa-lpc.rst` 全篇 | x86 专有硬件，arm64 上不存在 |
| `dma_dummy_ops` 的唯一安装点 | `drivers/acpi/scan.c` | 走 ACPI 路径。arm64 内核可以开 ACPI，但本套文档的范围是 DT 启动的 arm64，所以只在 02 §3.2 记了「实际用不到」而没有展开 ACPI 侧机制 |
| `ARCH_HAS_PHYS_TO_DMA` 的接管机制 | powerpc / mips 的 `arch/*/include/asm/dma-direct.h` | arm64 不 select，走通用 inline |
| 内存加密（SME / SEV）相关分支 | `include/linux/dma-direct.h:66-75`、`kernel/dma/direct.c:264-265`、`kernel/dma/swiotlb.c` 里的加密分支 | `__sme_set` / `__sme_clr` 在 arm64 上是恒等宏（`include/linux/mem_encrypt.h:30-31`）。只在 08 §1 和 08 §3 各留了一句「arm64 上不生效但解释了代码形状」 |
| 32 位 ARM 的 `dma_declare_coherent_memory` 差异 | `arch/arm/mm/dma-mapping.c` | 非目标架构。06 §4.4 只留了一句差别提示 |
| `ZONE_DMA` 在非 arm64 架构上的不同默认值 | `mm/Kconfig:976-990` | 只登记了 arm64 的两条 `default y if ARM64` |
| Xen 的两套 `dma_map_ops` | `drivers/xen/swiotlb-xen.c:397`、`drivers/xen/grant-dma-ops.c:281` | 只在 02 §3.2 的实例表里列名，未展开 |
| vDPA 的两套 `dma_map_ops` | `drivers/vdpa/vdpa_sim/vdpa_sim.c:251`、`drivers/vdpa/vdpa_user/vduse_dev.c:827` | 同上 |
| `CONFIG_DMA_PERNUMA_CMA` 的 NUMA 行为 | `kernel/dma/contiguous.c:317-328` | 单节点 arm64 板子上这段是死代码。只在 00 §2.4 和 08 §8 提了它存在 |
| USB / PCI-P2PDMA / i2c 各自的 DMA 封装 | 三份 Documentation（见 §1.3） | 归属邻居子系统 |

## §3 逐篇取证表

每行是一篇文档的主要论断来源，以及自审过程中实际改掉的东西。「自审修正」一列为空表示那一篇本轮没有发现内容错误——不是没审。

### §3.1 `01-api.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| §0 代码地图 | 13 个源文件 + 5 个头文件 + 37 个 Kconfig 项 + 38 个导出符号 | `kernel/dma/` 全目录、§2.5/§2.6 的命令 |
| §1 对外 API | 每个 API 的签名与能否睡眠 | `include/linux/dma-mapping.h` 全文 + `kernel/dma/mapping.c` 逐个实现 |
| §2 错误码语义 | `dma_map_*` 失败返回 `DMA_MAPPING_ERROR` 而不是负值；`dma_map_sg_attrs` 返回 0 表示失败、`dma_map_sgtable` 返回 0 表示成功 | `include/linux/dma-mapping.h:78`、`kernel/dma/mapping.c:182`、`:266-277` |
| §3 inline 与导出符号 | 哪些 API 是 `static inline`（因此不可 trace、不在 `/proc/kallsyms` 里） | 逐个在头文件里确认定义形式 |
| §4 `DMA_ATTR_*` | 7 个标志位的含义与实际消费点 | `include/linux/dma-mapping.h:26-63` + `Documentation/core-api/dma-attributes.rst` |
| §5 方向枚举 | 4 个值，`DMA_NONE` 只用于校验 | `include/linux/dma-direction.h`、`valid_dma_direction()` |
| §6 不该用的接口 | `dma_map_sg()` 相对 `dma_map_sgtable()` 的劣势；`cache_sync` 回调是遗留 | `include/linux/dma-map-ops.h` 的注释 + `Documentation/core-api/dma-api.rst` |

自审修正：无内容错误。行号按规则 10 从表格里撤掉，改为每节开头一行「头文件 / 实现」。

### §3.2 `02-datastructures.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| §1.0（6 组关系） | 三种地址不可混用；三条获取路；5 个叫 pool 的东西互不相同；3 个名字带 dma 的子系统互不相同 | `include/linux/dma-direct.h`、`kernel/dma/coherent.c`、`kernel/dma/pool.c`、`include/linux/swiotlb.h`、`kernel/dma/swiotlb.c` |
| §2 `struct device` 的 DMA 字段 | 全部 per-device 状态寄居在 `struct device` 里 | `include/linux/device.h:495-610`，逐字段带注释行号 |
| §3.1 `struct dma_map_ops` | 25 个成员 = 1 个标志 + 24 个回调；`opt_mapping_size` 是唯一不带 `struct device *` 的回调 | `include/linux/dma-map-ops.h:22-84` |
| §3.2 实例清单 | 全树 24 个实例，arm64 上相关的 6 个 | §2.6 的 grep 命令 + 逐个打开确认安装点 |
| §4 `struct bus_dma_region` | 翻译表一行的四个字段 | `include/linux/dma-direct.h:20-25` |
| §5 sg / sg_table | `nents` 与 `orig_nents` 的定义与责任划分 | `include/linux/scatterlist.h:41-42` 的字段注释 |
| §6 池类结构 | `dma_coherent_mem`、`io_tlb_mem`、`io_tlb_slot`、原子池的 `gen_pool` | 四处定义逐个打开 |
| §7 `dma_debug_entry` | 默认预留 65536 条 | `kernel/dma/debug.c` 的 `PREALLOC_DMA_DEBUG_ENTRIES` |
| §8 关系图 | 调用者 → 薄壳 → ops/direct → 内存来源 + 体系结构钩子 | 由 §6.1/§6.2 两条调用路径反推 |

自审修正：`dma_map_ops` 成员数与本篇 §2.6 的 grep 一度不一致，核对后确认本篇的 25 正确（详见 §4 第 2 条）。

### §3.3 `03-internals.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| Part A（§A1-§A10 时间线） | 从内存预留 → `of_dma_configure_id()` → `arch_setup_dma_ops()` → 分配/映射 → 释放的完整次序 | `arch/arm64/mm/init.c:266-457`、`drivers/of/device.c:114-223`、`arch/arm64/mm/dma-mapping.c:57-80`，次序用「谁调用谁」逐跳验证 |
| §A5 分配决策树 | `dma_direct_alloc()` 的六个分支 | `kernel/dma/direct.c` 全文 |
| §B1 arm64 没有自己的 ops | `dev->dma_ops` 只在 `if (iommu)` 时被设 | `arch/arm64/mm/dma-mapping.c:76-77` |
| §B2 地址翻译三层 | 体系结构接管层（arm64 无）/ `dma_range_map` 层 / 加密层 | `include/linux/dma-direct.h:58-87`；arm64 不接管的两条判据见 §2.6 |
| §B3 缓存维护三个钩子 | 两个钩子在 arm64 上有实现，两个是空 inline | `arch/arm64/mm/dma-mapping.c:16-33`、`include/linux/dma-map-ops.h:326-328`、`:342-344` |
| §B4 bounce 决策 | 三个入口两套语义（强制 bounce / 能力不足 bounce / 受限池） | `kernel/dma/direct.h:91-105` |
| §B5 debug 层插入点 | 每个对外 API 后面跟一个 `debug_dma_*` | `kernel/dma/mapping.c` 逐个调用点 |
| Part C 评价 | 设计债与源码自陈问题 | 见 §6 |

自审修正：一条指向 `06-devicetree.md` 的引用原先落在一个并不存在的二级小节上，改为指向该篇的一级章节（详见 §4 第 3 条）。

### §3.4 `04-extensions.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| §0 分诊表 | 9 个扩展里哪几个在 arm64 上真会碰到 | 逐个查 Kconfig 的 `default` 与 arm64 的 `select` |
| §1 swiotlb | 中转区的分配、对齐、三次越界检查 | `kernel/dma/swiotlb.c` 全文 |
| §2 受限 DMA 池 | `restricted-dma-pool` 的 DT 入口与 per-device 隔离 | `kernel/dma/swiotlb.c` 的 `rmem_swiotlb_*` |
| §3 CMA | 借出去再收回来；`cma=` 参数；per-NUMA 变体 | `kernel/dma/contiguous.c` 全文 + `mm/cma.c` 二等区段 |
| §4 原子池 | 三个池对应三段地址范围，其中两个是条件创建 | `kernel/dma/pool.c:187-221` |
| §5 一致性池 | per-device 与全局两套，`dma_declare_coherent_memory()` 全树调用者 | `kernel/dma/coherent.c` 全文 |
| §6 DMA-API debug | 记账表、哈希桶、泄漏检查 | `kernel/dma/debug.c` 全文 |
| §7 映射性能基准 | 唯一的用户态接口，ioctl 号在非 uapi 头文件里 | `kernel/dma/map_benchmark.c` 全文 + `include/linux/map_benchmark.h` |
| §8 `dma_dummy_ops` | 38 行，全部回调返回失败；唯一安装点走 ACPI | `kernel/dma/dummy.c` 全文；安装点见 §2.7 |
| §11 全树使用统计 | 每个扩展的消费者数与重跑命令 | 逐条列在该节，命令口径与 §2.6 一致 |

自审修正：无内容错误。

### §3.5 `05-usage.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| 篇首 | ⚠️ **所有骨架代码未编译未运行** | 见 §5.1 |
| §1-§2 角色枚举 | 本子系统的消费者角色：需要描述符环的驱动 / 做一次性传输的驱动 / 需要大块连续内存的驱动 / 实现 `dma_map_ops` 的后端 | 由 38 个导出符号的消费面反推 |
| §3 DMA 安全缓冲区 | 哪些内存不能直接 map（栈、`vmalloc` 区、模块静态数据） | `Documentation/core-api/dma-api-howto.rst` + `kernel/dma/debug.c` 的对应检查 |
| §5.1 不能睡的场合 | `GFP_ATOMIC` 走原子池，判据是你传的 gfp 而不是实际上下文 | `kernel/dma/direct.c:162-169` |
| §10 近邻符号区分 | 逐组「名字很像但语义不同」的 API 对照 | 每组回到头文件逐字确认 |

自审修正：正文出现的每个符号名跑过一遍全树 grep，无幻觉符号。

### §3.6 `06-devicetree.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| §0 发现路径分诊表 | DT 只是本子系统属性来源的一条路径 | 与 §2.7 的裁剪表对齐 |
| §1 核心真正会读的属性总表 | `dma-ranges` / `dma-coherent` / `iommus` / `memory-region` / `dma-noncoherent` 及各自写在哪个节点 | 逐条 grep 消费代码 |
| §2 `dma-ranges` | 解析入口与失败行为 | `drivers/of/address.c:969-1044` |
| §3.3 `dma-coherent` | 写进 `dev->dma_coherent` 的那一行 | `arch/arm64/mm/dma-mapping.c:75` |
| §4.3 决策树 | `shared-dma-pool` 有无 `reusable` 决定是 CMA 区还是普通一致性池 | `drivers/of/of_reserved_mem.c:330-360` + `shared-dma-pool.yaml` |
| §8-§9 定位章 | 编译期 / 运行期静态 / 运行期动态三层 | 与 07 的分诊表不重叠 |

自审修正：无内容错误。三条 dts 统计的口径（文件数而非节点数）已在该篇 §1 显式声明。

### §3.7 `07-debug.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| §2.1 四道门 | 开了 `CONFIG_DMA_API_DEBUG` 之后还有四个条件才会看到报告 | `kernel/dma/debug.c` 逐个开关 |
| §3.4 CMA 的诊断出口 | debugfs 四个文件 + sysfs 两个累计计数器 | `mm/cma_debug.c:155-190`、`mm/cma_sysfs.c:28-105` |
| §4.4 泄漏检查只覆盖 PCI | `dma_debug_add_bus()` 全树 2 个调用者，arm64 上只有 PCI 那个有效 | 见 §2.6 |
| §5 tracepoint | 全子系统只有 1 个 tracepoint 触发点 | `kernel/dma/swiotlb.c:920` + `include/trace/events/swiotlb.h`；计数陷阱见 §4 第 1 条 |
| §6.1 三条 overflow 消息 | 文本几乎一样但修法不同 | 三处 `dev_WARN_ONCE` 逐字 grep |
| §7 故障注入 | ⚠️ **本子系统没有任何故障注入** | `grep` 结果 0 条，命令见 §2.6 |
| §8.1 Kconfig 开关 | 每个调试开关的默认值与依赖 | `kernel/dma/Kconfig` + `mm/Kconfig` |

自审修正：§3.4 原先只写了 debugfs，遗漏了 CMA 的 sysfs 出口，补读 `Documentation/ABI/testing/sysfs-kernel-mm-cma` 后补上（详见 §4 第 4 条）。

### §3.8 `08-feynman.md`

九个主题各自的核心行号：§1 `include/linux/dma-direct.h:20-50`；§2 `kernel/dma/mapping.c:415-424` + `arch/arm64/include/asm/pgtable.h:555-585`；§3 `kernel/dma/swiotlb.c:551-590`；§4 `arch/arm64/mm/dma-mapping.c:16-33` + `kernel/dma/direct.h:55-82`；§5 `kernel/dma/direct.c:162-169` + `kernel/dma/pool.c:187-221`；§6 `include/linux/scatterlist.h:41-42` + `drivers/iommu/dma-iommu.c:1060-1110`；§7 §1.2 的两道门；§8 `mm/page_alloc.c:2314-2330` + `mm/cma.c:440-490`；§9 `arch/arm64/mm/dma-mapping.c:64-75`。

自审修正：§0 的主题→章节映射表里有两条指错对象——一条指向 `05-usage.md` 里一个不存在的二级小节，一条把 CMA 指到了 `04-extensions.md` 讲原子池的那一节。前者由脚本发现，后者靠对账轮发现（详见 §4 第 3 条）。

### §3.9 `00-overview.md`

| 章节 | 主要论断 | 出处 |
| --- | --- | --- |
| 篇首 | 基线与「子系统一行未改」 | §1.1 |
| §1.2 类型判定 | 框架型 | §1.2 |
| §2.0-§2.5 五个硬件事实 | 缓存 / 地址翻译 / 位宽 / 连续性 / IOMMU | 各自指向 §3.1-§3.8 已定稿的论断，本篇不新增取证 |
| §2.7 五个问题的 arm64 状态 | 逐条给出「怎么确认这块板子」的命令 | §2.6 |
| §6.1/§6.2 两条调用路径 | 函数级次序 | 与 03 Part A 同源，写完后逐跳回查一致性 |

⚠️ 00 是最后写的，它的定位是对已定稿内容的浓缩，**没有任何一条论断是在 00 里首次出现的**。这是刻意的：复习入口不该承担取证责任。唯一的例外是 §2.0 那段故意写错的示例代码，它不引用源码，只用来立起五个假设。

## §4 交叉验证实例

下面五条都是「先有一个看起来对的结论，然后被另一条证据推翻或修正」的实例。它们比正文里任何一条论断都更有用——它们说明这套文档在哪些方向上容易出错。

### §4.1 grep 命中数 3，真实 tracepoint 数 1

初始动作：`grep -rn "trace_" kernel/dma/*.c`，得到 3 条命中。如果就此收工，07 §5 会写成「本子系统有 3 个 tracepoint」。

打开三条看：

| 命中位置 | 实际是什么 |
| --- | --- |
| `kernel/dma/debug.c:171` | `stack_trace_print()` —— `lib/stacktrace.c` 的栈回溯打印函数 |
| `kernel/dma/debug.c:657` | `stack_trace_save()` —— 同一族的栈抓取函数 |
| `kernel/dma/swiotlb.c:920` | `trace_swiotlb_bounced()` —— 唯一真正的 tracepoint |

前两条是**子串包含**造成的误计：`stack_trace_` 里含 `trace_`。修正后的口径：

```bash
grep -rnE "\btrace_[a-z_]+\(" kernel/dma/*.c | grep -v "stack_trace"
```

结论：全子系统只有一个 tracepoint 触发点，定义在 `include/trace/events/swiotlb.h`。这也印证了 07 §5 那句「本子系统基本没有 tracepoint 覆盖，要看流程只能靠 kprobe」——它不是偷懒的说法，是事实。

### §4.2 结构体成员数 24 与 25 的冲突

`02-datastructures.md` §3.1 写的是「`struct dma_map_ops` 有 25 个成员」。写 99 §2.6 时用下面这条命令复核：

```bash
awk '/^struct dma_map_ops \{/,/^\};/' include/linux/dma-map-ops.h | grep -cE "^[[:space:]]+[a-z_].*\(\*"
```

得到 24。两个数字差 1，看起来 02 写错了。

把整个结构体 dump 出来逐个数，发现差的那一个是 `flags`（`include/linux/dma-map-ops.h:23`），它是个 `unsigned int`，不是函数指针。上面那条命令的正则要求行里有 `(*`，所以只数了指针。

结论：**两个数字都对，只是口径不同**——24 个回调 + 1 个标志字段 = 25 个成员。02 §3.1 无需修改。这条之所以值得记下来，是因为它是本套文档里唯一一处「同一个结构体在两篇里出现两个不同数字」的地方；不写进 99，下一个读者会当成矛盾去改，改错方向。

### §4.3 引用有效、但指向的章节不讲那件事

三个批量脚本能查「章节号是否存在」和「行号是否越界」，查不到「章节存在但内容不对」。本轮抓到两条，抓法不同：

| 缺陷 | 怎么发现的 |
| --- | --- |
| `08-feynman.md` §0 表里有一行指向 `05-usage.md` 的一个不存在的二级小节 | 脚本发现（章节号不存在）。修法：grep 05 找到原子池实际所在的小节，改指过去 |
| 同一张表里，讲 CMA 的那一行指向了 `04-extensions.md` 里讲**原子池**的那一节 | ⚠️ **脚本查不出来**——那一节真实存在。靠人工对账轮发现：把表里每一行的目标章节打开，看它讲的是不是那件事 |
| `03-internals.md` 有一条指向 `06-devicetree.md` 一个不存在的二级小节 | 脚本发现 |
| `00-overview.md` §2.4 曾指向 `06-devicetree.md` 讲「没有 `reusable` 的普通一致性池」的那一节，而正文要说的恰好是 CMA（有 `reusable`）的情形 | 人工对账轮发现。修法：改指该篇的决策树小节，那里才同时覆盖两种情形 |

四条里两条只有人工对账能抓。这印证了 skill 第 17 条那句话：**脚本全绿不等于引用正确**。

### §4.4 补读一份 25 行的 ABI 文档，推翻了「CMA 的诊断出口只有 debugfs」

写 07 §3.4 时的路径是「grep `mm/cma*.c` 里的 `debugfs_create_*`」，得到四个文件，写完就收工了。收尾跑 Documentation 覆盖检查时补读了 `Documentation/ABI/testing/sysfs-kernel-mm-cma`（25 行），发现 CMA 还有 sysfs 出口：

- `/sys/kernel/mm/cma/<name>/alloc_pages_success`（`mm/cma_sysfs.c:32-40`）
- `/sys/kernel/mm/cma/<name>/alloc_pages_fail`（`mm/cma_sysfs.c:42-49`）

开关是另一个 Kconfig（`CMA_SYSFS`，`mm/Kconfig:876-881`），所以只 grep `mm/cma_debug.c` 永远碰不到它。

⚠️ 而且这两个计数器提供的信息 debugfs **结构上给不了**：debugfs 的 `used` / `maxchunk` 是瞬时水位，sysfs 的两个是累计历史值。「三小时前有一次 CMA 分配失败」这件事只能从 sysfs 看到。这是本轮补读带来的最实质的一处改进。

⚠️ 单位陷阱一并记下：计数点在 `mm/cma.c:512`（成功）和 `mm/cma.c:516`（失败），加的是**页数**不是调用次数。4K 页的 arm64 上，一次失败的 4 MiB 请求让 `alloc_pages_fail` 涨 1024。

### §4.5 「有 `bus_type`」的 grep 命中是形参

见 §1.2。`grep -rn "struct bus_type" kernel/dma/` 有 1 条命中，只看计数会把这个框架型子系统误判成有自己的总线。判读靠打开那一行看它是定义还是形参（`kernel/dma/debug.c:875` 是 `dma_debug_add_bus()` 的形参）。

这一条和 §4.1 是同一个机理的两种形态：**`grep -c` 数的是「含该字符串的行数」，不是「该语义的实例数」**。本套文档里凡是给了数字的地方，都在 §2.5 / §2.6 附了口径说明。

## §5 明确未验证项

### §5.1 未编译、未运行

- **`05-usage.md` 的全部骨架代码未经编译。** 它们是从本树真实驱动的写法归纳出来的形状，不是从某个具体驱动摘下来的可编译片段。头文件包含、错误路径的 `goto` 标签、变量声明都可能不完整。
- **本套文档没有在任何硬件或模拟器上运行过。** 所有关于运行期行为的描述（哪条消息会打出来、哪个 debugfs 文件里会有什么、tracepoint 触发时机）都是从代码推出来的，不是观察到的。
- **`07-debug.md` 里所有示例命令的输出格式没有实测。** 该篇刻意没有编造任何输出样例；凡是写了输出形状的地方，形状来自源码里的格式字符串（逐字 grep 确认），不是来自某次真实运行。
- **`06-devicetree.md` 的 DTS 片段没有编译过。** 引用的真实节点来自 `arch/arm64/boot/dts/` 下的 in-tree 文件（读者可以 diff），但文中为说明问题而拼接的片段没过 `dtc`。

### §5.2 只读了符号表或局部区段

- **`drivers/iommu/dma-iommu.c` 只详读了约 250 行（区段见 §2.2），全文 1600 行以上。** 所以：IOMMU 路径的 sg 合并逻辑（`__finalise_sg()`）是一等把握的，但 IOVA 分配策略、`iommu_dma_ops` 其余回调的内部实现、IOMMU 侧的缓存维护差异全部**未验证**。文档里刻意没有写任何「IOMMU 侧只做了 X」这类完整性论断。
- **`mm/cma.c` 只读了三段。** `cma_alloc()` 的迁移与重试路径是一等把握的，但 CMA 区的初始化时序、`cma_declare_contiguous_nid()` 的对齐处理、多区管理只读了调用点。08 §8 讲的是「借出去再收回来」这个机制的形状，不是 CMA 的完整实现。
- **`kernel/dma/debug.c` 全文读完了，但它的哈希表容量、桶数、锁粒度对性能的影响只是读代码得出的结论，没有 profile 数据。**
- **24 个 `dma_map_ops` 实例里只打开了 6 个的定义体**（arm64 相关的那 6 个），另外 18 个只确认了位置。02 §3.2 那张表里非 arm64 那几行只有「在哪」，没有「做什么」。
- **`drivers/of/` 三个文件只读了区段。** DT 属性从文本到 `struct device` 字段这条链是一等把握的；`of_dma_configure_id()` 之外的 DT 处理没有覆盖。

### §5.3 推断而非观察

以下结论在代码上成立，但**「实际会不会发生」没有验证过**：

| 结论 | 推断的依据 | 为什么算推断 |
| --- | --- | --- |
| 混用 `virt_to_phys()` 和 `dma_addr_t` 的驱动「在大多数 arm64 板子上测不出问题」 | 52 个 dts 文件用 `dma-ranges`，占比很低 | 没有统计「有多少块板子的多少个设备实际处在非恒等映射区间下」 |
| arm64 上 `DMA_ATTR_WRITE_COMBINE` 「不产生页表差异」 | `pgprot_writecombine` 与 `pgprot_dmacoherent` 都归到 `MT_NORMAL_NC`（`arch/arm64/include/asm/pgtable.h:563-564` 与 `:578-580`） | 只比对了页表属性位。没有验证在真实 CPU 上两者的写合并行为是否真的一致 |
| 一致性内存「比普通内存慢」 | 页表属性是 Normal-NC，不进 cache | 没有任何测量数据。慢多少、在什么访问模式下最明显，08 §2 只给了定性方向 |
| CMA 回收「可能失败」 | `alloc_contig_range()` 返回 `-EBUSY` 的重试与最终失败路径（`mm/cma.c:478-482`） | 没有构造出实际失败的场景 |
| 原子池「三个池里两个是条件创建」在典型 arm64 板子上的实际情况 | `has_managed_dma()` 是运行期判断（`kernel/dma/pool.c:206`） | 依赖具体板子的内存布局，没有在板子上看过 |
| `dev->dma_coherent` 为假的设备在 arm64 上「常见」 | `dma-coherent` 只在 45 个 dts 文件里出现 | 同第一行，占比不等于设备数占比 |
| 「本子系统的误用是安静的」 | 默认配置下没有 `WARN_ON`/`BUG_ON` 覆盖所有权约定 | 是「代码里没有检查」的直接推论，但没有真的写一个违规驱动跑一遍看是否真的静默 |

⚠️ 上表第一行和最后一行是本套文档里**最容易被当成实测结论引用**的两句。它们不是。

### §5.4 版本时效

- **基线是 `f6e38ae624cf` / Linux 6.1.158，2026 年 8 月 24 日的树状态。** 6.1 是长期维护分支，`kernel/dma/` 在其上仍会收到修复。
- **本树未改动本子系统**（§1.1 的空输出），所以本套文档等价于「上游 6.1.158 的 `kernel/dma/`」。反过来说，**任何一次 6.1.y 的稳定版更新都可能让某个行号偏移**。行号失效的判据是跑一次 `check_line_refs.py`；语义失效的判据要看 `git log --oneline v6.1.158..HEAD -- kernel/dma/`。
- **`swiotlb.c` 是本子系统在上游变动最频繁的文件**（6.1 之后有一系列动态扩容相关的改动），任何跨版本引用 08 §3 的结论都要先确认这一点。
- ⚠️ **主线（6.19 以后）与本基线在这个子系统上已有结构性差异**，本套文档不适用于主线阅读。最典型的一处：`ARCH_DMA_MINALIGN` 在 arm64 上后来做成了运行期可变量，08 §9 讲的那个编译期常量兜底机制在主线上形状已经不同。
- **本套文档引用的 `mm/` 与 `drivers/iommu/` 区段随大版本变动更快**，它们不在 §1.1 那条「未改动」证明的覆盖范围内，重跑 §1.1 的第二条命令确认。

## §6 源码自陈的问题

以下不是文档作者的评价，是源码自己在注释里写的，或者是代码形状直接暴露出来的。

| 问题 | 位置 | 源码怎么说 |
| --- | --- | --- |
| `arch_dma_prep_coherent()` 在 arm64 上做了架构上不必要的 invalidate，是为了兜住写错的驱动 | `arch/arm64/mm/dma-mapping.c:35-55` | 注释（`:39-53`）里给了 lore 链接（`:46`），并明确承认（`:48-52`）这个加固只让问题「less likely」，驱动该修的还得修 |
| kerneldoc 里的拼写错误 `funciton` | `kernel/dma/mapping.c:225` | 在 `dma_map_sg_attrs()` 的警告段里。该文件里只出现这一次 |
| 注释里的拼写错误 `scather-gather` | `kernel/dma/debug.c:142` | 正确写法是 scatter-gather |
| 性能基准模块自陈「第二次 probe 会失败」 | `kernel/dma/map_benchmark.c:314-317` | 注释直接写明这是已知限制，没有修 |
| `translate_dma_to_phys()` 失败时返回 `(phys_addr_t)-1`，但没有任何调用者检查 | `include/linux/dma-direct.h:40-50`，失败返回在 `:49` | 对照着看更明显：同一个文件里 `translate_phys_to_dma()`（`:27-38`）失败返回 `DMA_MAPPING_ERROR`，并且在 `:36` 有一行注释解释「这样做是为了让 `dma_capable()` 失败」。反方向那个函数既没有注释也没有检查者 |
| `cache_sync` 回调是遗留接口 | `include/linux/dma-map-ops.h` 的 `dma_map_ops` 定义里 | 树里已无 arm64 相关实现，但字段仍在 |

⚠️ 第五行那条不是注释里写的，是**两个反方向函数的处理不对称**这个代码形状暴露的。它没有在任何地方被承认为问题，本套文档也不断言它是 bug——只记录不对称本身，判断留给读者。

## §7 自检脚本结果

三个脚本的调用形式（脚本目录记作 `$S`，文档目录记作 `$D`）：

```bash
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh dma-mapping $D .
```

| 脚本 | 结果 |
| --- | --- |
| 行号检查 | 干净。扫描到的唯一 `路径:行号` 引用数在六百条量级，消歧表覆盖六十个基名 / 四十余个前缀目录，无解析不到的文件，无越界行号，忽略项 0 条 |
| 跨篇引用检查 | 十篇文档全部纳入；命中的跨篇引用数在两百五十条量级。本轮共修掉四条无效引用，其中两条由本脚本发现（目标章节号不存在），另两条由人工对账轮发现（章节存在但不讲那件事，脚本无法发现）。逐条记录见 §4.3 |
| 官方文档覆盖率 | ⚠️ **脚本报「必查档 0 篇」，这是假阴性**，原因和处置见 §1.3。手工枚举后确认 4 份必查文档中 3 份已被引用，第 4 份因主题不适用而显式裁剪（§2.7） |

⚠️ 三个脚本都抓不到的两类缺陷，本轮各抓到了实例：

- **行号在范围内但指向无关代码**：只能逐条 `sed` 打开核对。本轮把每条引用都打开过一遍
- **章节存在但不讲那件事**：只有人工对账轮能抓，本轮抓到 2 条（§4.3 的第二、第四行）

⚠️ 关于计数命令的输出格式：本篇里凡是引用 `grep -c` 一类的统计结果，一律写成 `文件名 → 数字` 的形式，不用工具的原生输出格式——原生格式会被行号检查器当成一条 `路径:行号` 引用去解析，从而制造出虚假的越界报告。同理，本篇描述失效引用时只描述现象、不照抄失效引用本身的写法。这两条不是排版偏好，是**自指陷阱**的规避动作：这一篇本身也是被脚本扫描的对象。