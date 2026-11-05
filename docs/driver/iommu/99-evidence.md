---
title: 取证附录
---

# IOMMU 子系统 — 取证附录

> **定位**：证据、方法、已发现的自陈问题、明确未验证项
>
> **目标架构**：arm64
>
> **基线**：Linux 6.1.158（`f6e38ae624cf`）

---

## §1 取证方法

### §1.1 证据等级

| 等级 | 定义 | 本篇应用 |
|------|------|---------|
| 一等 | 直接阅读源码，行号精确 | 结构体定义、API 签名、函数流程 |
| 二等 | 基于 grep 推断，未逐行读 | 部分内部函数流程、消费者统计 |
| 三等 | 基于 Documentation/ 或注释 | DT 绑定、设计意图 |

### §1.2 工具

- **codebase-memory-mcp (cbm)**：代码导航、找候选
- **ripgrep (rg)**：精确验证、消费者统计
- **sed**：行号提取、函数体读取

### §1.3 盲区

- **宏生成符号**：`SYSCALL_DEFINE*` 不产生节点
- **函数指针间接调用**：ops 结构体分派不可见
- **预处理器条件**：`#ifdef` 分支不可见

---

## §2 规模统计

### §2.1 核心文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `drivers/iommu/iommu.c` | 3276 | 核心框架 |
| `drivers/iommu/dma-iommu.c` | 1710 | DMA 集成 |
| `drivers/iommu/iova.c` | 953 | IOVA 分配器 |
| `drivers/iommu/io-pgtable.c` | 78 | 页表格式分发 |
| `drivers/iommu/iommu-sva-lib.c` | 71 | SVA 辅助 |
| `drivers/iommu/iommu-traces.c` | 28 | tracepoint |
| `drivers/iommu/iommu-debugfs.c` | 51 | debugfs 框架 |
| **合计** | **6167** | — |

### §2.2 arm64 驱动

| 驱动 | 行数 | 平台 |
|------|------|------|
| `arm-smmu-v3.c` | 3971 | 现代服务器/嵌入式 |
| `arm-smmu.c` | 2327 | 旧平台 |
| `apple-dart.c` | 1064 | Apple Silicon |
| `mtk_iommu.c` | 1587 | MediaTek |
| `ipmmu-vmsa.c` | 1166 | Renesas R-Car |
| **合计** | **10115** | — |

### §2.3 导出符号

| 文件 | EXPORT_SYMBOL 数 |
|------|-----------------|
| `iommu.c` | 55 |
| `dma-iommu.c` | 3 |
| `iova.c` | 12 |
| `io-pgtable.c` | 2 |
| `iommu-sva-lib.c` | 2 |
| **合计** | **74** |

### §2.4 DT 绑定

- YAML 文件数：11
- 路径：`Documentation/devicetree/bindings/iommu/`

---

## §3 逐篇取证表

### §3.1 `00-overview.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| IOMMU 是框架型子系统 | 无 `bus_type` | 一等 |
| 5 种域类型 | `include/linux/iommu.h:71-89` | 一等 |
| arm64 有 5 个 IOMMU 驱动 | `wc -l` 统计 | 一等 |

### §3.2 `01-api.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| 74 个导出符号 | `grep -c EXPORT_SYMBOL` | 一等 |
| 7 个 tracepoint | `drivers/iommu/iommu-traces.c` | 一等 |
| 3 个内核参数 | `drivers/iommu/iommu.c:37-39` | 一等 |

### §3.3 `02-datastructures.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| 核心结构体字段 | `include/linux/iommu.h` | 一等 |
| 两层 ops 设计 | `include/linux/iommu.h:229-324` | 一等 |
| 锁层次 | `drivers/iommu/iommu.c` 代码阅读 | 二等 |

### §3.4 `03-internals.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| probe 流程 | `drivers/iommu/iommu.c:283-343` | 一等 |
| attach 流程 | `drivers/iommu/iommu.c:2119-2128` | 一等 |
| map/unmap 流程 | `drivers/iommu/iommu.c:2352-2440` | 一等 |
| flush queue 机制 | `drivers/iommu/dma-iommu.c:157-280` | 一等 |

### §3.5 `04-extensions.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| SVA 限制单设备 group | `drivers/iommu/iommu.c:2808-2813` | 一等 |
| 故障处理单处理器 | `drivers/iommu/iommu.c:1190-1193` | 一等 |
| IOVA 缓存大小 | `drivers/iommu/iova.c:18,627` | 一等 |

### §3.6 `05-usage.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| arm-smmu-v3 probe 流程 | `drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:3810-3950` | 一等 |
| DMA-API 用法 | `drivers/net/ethernet/intel/e1000/e1000_main.c` | 一等 |
| VFIO 用法 | `drivers/vfio/vfio_iommu_type1.c` | 一等 |

### §3.7 `06-devicetree.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| SMMUv3 DT 属性 | `Documentation/devicetree/bindings/iommu/arm,smmu-v3.yaml` | 三等 |
| of_xlate 实现 | `drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2778-2780` | 一等 |

### §3.8 `07-debug.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| 7 个 tracepoint | `drivers/iommu/iommu-traces.c` | 一等 |
| 3 个内核参数 | `drivers/iommu/iommu.c:37-39` | 一等 |
| 无故障注入框架 | 全树 grep 无结果 | 一等 |

### §3.9 `08-feynman.md`

| 论断 | 出处 | 证据等级 |
|------|------|---------|
| 6 个类比主题 | 原创 | — |
| 类比边界 | 技术差异 | 一等 |

---

## §4 未验证项

### §4.1 第一层：未编译未运行

- 所有代码示例未编译
- 未在实际硬件上验证行为

### §4.2 第二层：只读符号表

- 部分内部函数（如 `__iommu_attach_group`）基于上下文推断，未逐行读
- 部分驱动（apple-dart、mtk_iommu）只读符号表，未深入流程

### §4.3 第三层：推断非观察

- 锁层次基于代码阅读推断，未形式化验证
- flush queue 定时器精度（10ms）是硬编码，未验证是否可调

### §4.4 第四层：版本时效

- 基于 Linux 6.1.158，未来版本可能变化
- SVA 限制（单设备 group）可能在未来版本解除

### §4.5 第五层：对端不在本树

- ACPI IORT 路径未覆盖（arm64 也用，但本篇以 DT 为主线）
- 用户态工具（如 `iommu` 命令行）未覆盖

---

## §5 交叉验证实例

### §5.1 推翻初始假设：`iommu_domain.type` 是枚举

**初始假设**：`type` 是枚举值，用 `==` 比较

**验证**：`include/linux/iommu.h:71-89` 显示是组合标志

**结论**：必须用 `&` 比较，`IOMMU_DOMAIN_DMA_FQ` 包含 `IOMMU_DOMAIN_DMA` 的所有位

### §5.2 推翻初始假设：IOVA 缓存覆盖所有大小

**初始假设**：IOVA 缓存覆盖所有分配

**验证**：`drivers/iommu/iova.c:18` 显示 `IOVA_RANGE_CACHE_MAX_SIZE=6`，只缓存 ≤ 64 页

**结论**：大分配直接走红黑树

### §5.3 推翻初始假设：所有 IOMMU 驱动都实现 debugfs

**初始假设**：debugfs 有具体内容

**验证**：`drivers/iommu/iommu-debugfs.c` 只创建目录，arm-smmu-v3 未实现

**结论**：debugfs 是框架，具体内容靠驱动

---

## §6 自审修正记录

### §6.1 第一轮自审

- 修正：`iommu_domain.type` 是组合标志，不是枚举
- 修正：IOVA 缓存只覆盖小分配
- 修正：debugfs 无具体内容

### §6.2 第二轮自审

- 修正：SVA 限制单设备 group
- 修正：故障处理单处理器
- 修正：flush queue 10ms 定时器

---

## §7 脚本结果

### §7.1 `check_xrefs.py`

```
文档 10 篇；跨篇引用命中 14 条，失效 0 条
干净：所有跨篇引用都指向真实存在的章节。
```

### §7.2 `check_line_refs.py`

```
扫描 177 条唯一 file:line 引用
干净：没有解析不到的文件，也没有越界行号。
```

### §7.3 `check_doc_coverage.sh`

```
【必查】共 0 篇
```

**说明**：`Documentation/driver-api/iommu*` 不存在，IOMMU 无官方 API 文档。

---

## §8 取证方法可复现性

### §8.1 基线命令

```bash
git log -1 --oneline
# f6e38ae624cf Linux 6.1.158
```

### §8.2 规模统计命令

```bash
wc -l drivers/iommu/iommu.c drivers/iommu/dma-iommu.c drivers/iommu/iova.c
grep -c EXPORT_SYMBOL drivers/iommu/iommu.c
ls Documentation/devicetree/bindings/iommu/*.yaml | wc -l
```

### §8.3 消费者统计命令

```bash
grep -rn 'iommu_sva_bind_device' --include=*.c | wc -l
grep -rn 'dma_map_page' --include=*.c | wc -l
```

---

## §9 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| 回顾全貌 | `00-overview.md` |
| API 速查 | `01-api.md` |
| 数据结构 | `02-datastructures.md` |
