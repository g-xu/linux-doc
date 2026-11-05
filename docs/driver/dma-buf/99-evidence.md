---
title: 取证附录
---

# dma-buf 99：证据与自评

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

本篇记录本套 10 篇文档的**全部取证过程、口径、裁剪范围、未验证项和发现的缺陷**。
任何对前九篇结论的质疑，先来这里查该结论属于哪一等证据。

---

## §1 基线与范围

### §1.1 树的状态：dma-buf 完全干净

```bash
git status --porcelain -- drivers/dma-buf \
  include/linux/dma-buf.h include/linux/dma-fence.h \
  include/linux/dma-fence-array.h include/linux/dma-fence-chain.h \
  include/linux/dma-fence-unwrap.h include/linux/dma-resv.h \
  include/linux/sync_file.h include/linux/dma-heap.h \
  include/uapi/linux/{dma-buf,sync_file,dma-heap,udmabuf}.h
# ⇒ 0 行输出
```

⚠️ **本子系统的全部代码和头文件都没有被本树（`linux-6.1.158` 分支）的补丁碰过**
⇒ 与 tty 不同（tty 的串口层有 14 个脏文件），**本套文档不需要"补丁性质"一节**，
所有结论都是上游 6.1.158 原生行为。

### §1.2 子系统类型判定：两道门

| 门 | 命令 | 结果 | 结论 |
|---|---|---|---|
| 门 1：有 `struct bus_type` 吗 | `grep -rn 'struct bus_type' drivers/dma-buf/` | **无输出** | **框架型**，不是总线型 |
| 门 2：有 uapi 头吗 | `ls include/uapi/linux/{dma-buf,sync_file,dma-heap,udmabuf}.h` | **4 个都在** | **框架型 + 带用户态 ABI** |

⇒ 文档形态定为"**框架型 + 独立 ioctl 章 + 无 DT 绑定章**"。
06 篇因此改写成"CMA heap 的内存来源"这一条真实链路（见 §2.5）。

### §1.3 基线数字（全部可重跑）

| 项 | 命令 | 结果 |
|---|---|---|
| `.c` 文件数与行数 | `wc -l drivers/dma-buf/*.c drivers/dma-buf/heaps/*.c` | **19 个 / 9020 行** |
| 核心（除 selftest） | 同上减 `st-*.c` + `selftest.c` | **6852 行** |
| 头文件 | `wc -l include/linux/dma-{buf,fence,fence-array,fence-chain,fence-unwrap,resv,heap}.h include/linux/sync_file.h` | **8 个 / 2212 行** |
| uapi | `wc -l include/uapi/linux/{dma-buf,sync_file,dma-heap,udmabuf}.h` | **4 个 / 366 行** |
| 导出符号 | `grep -cE '^EXPORT_SYMBOL' drivers/dma-buf/*.c` | **70 个** |
| 官方文档 | `wc -l Documentation/driver-api/dma-buf.rst Documentation/driver-api/sync_file.rst` | **348 + 86 = 434 行** |
| Kconfig | `wc -l drivers/dma-buf/Kconfig drivers/dma-buf/heaps/Kconfig` | **94 + 14 行**，8 + 2 个选项 |
| tracepoint | `include/trace/events/dma_fence.h` + `drivers/dma-buf/sync_trace.h` | **89 + 33 行**，7 + 1 个事件 |

### §1.4 官方文档登记（两篇都通读）

| 文档 | 行数 | index 挂载点 | 证据等级 |
|---|---|---|---|
| `Documentation/driver-api/dma-buf.rst` | 348 | `Documentation/driver-api/index.rst:25` | ⚠️ **一等：全文逐行读过** |
| `Documentation/driver-api/sync_file.rst` | 86 | `Documentation/driver-api/index.rst:104` | ⚠️ **一等：全文逐行读过** |
| `Documentation/ABI/testing/sysfs-kernel-dmabuf-buffers` | 24 | ABI 树 | 一等 |
| `Documentation/devicetree/bindings/reserved-memory/shared-dma-pool.yaml` | 97 | DT 绑定 | 一等（06 篇用） |

⚠️⚠️ **`dma-buf.rst` 有两节是纯设计论证，不描述任何 API**：
`Indefinite DMA Fences`（`:203-272`）和
`Recoverable Hardware Page Faults Implications`（`:274-348`）
—— **合计 146 行，占全文 42%**。08 篇整篇建立在这两节 + 代码里的
`DOC: fence cross-driver contract`（`drivers/dma-buf/dma-fence.c:69-112`）之上。

### §1.5 ⚠️ 发现的文档缺陷：sysfs 路径写错，且错的那处是被发布的

同一个文件里五处写路径，四对一错：

| 位置 | 写的是 | 判定 |
|---|---|---|
| `drivers/dma-buf/dma-buf-sysfs-stats.c:36` | `/sys/kernel/dma-buf/buffers`（带连字符） | ❌ **错** |
| `drivers/dma-buf/dma-buf-sysfs-stats.c:41` | `/sys/kernel/dmabuf/buffers/<inode_number>/exporter_name` | ✔ |
| `drivers/dma-buf/dma-buf-sysfs-stats.c:42` | `/sys/kernel/dmabuf/buffers/<inode_number>/size` | ✔ |
| `drivers/dma-buf/Kconfig:82` | `/sys/kernel/dmabuf/buffers` | ✔ |
| `Documentation/ABI/testing/sysfs-kernel-dmabuf-buffers:1` | `/sys/kernel/dmabuf/buffers` | ✔ |

⚠️⚠️ 代码侧真实路径由 `kset_create_and_add("dmabuf", …)`
（`drivers/dma-buf/dma-buf-sysfs-stats.c:148`）+ `"buffers"`（`:154`）决定 ⇒ **`dmabuf` 无连字符是对的**。
⚠️⚠️ 而 `:36` 那处在一个 `DOC:` 块里，被
`Documentation/driver-api/dma-buf.rst:114` 的
`.. kernel-doc:: drivers/dma-buf/dma-buf-sysfs-stats.c` 拉进"DMA-BUF statistics"一节
（标题在 `dma-buf.rst:112`）⇒ **kernel.org 上公布的路径是错的那一个**。

**这是本套文档在 6.1.158 上发现的唯一一处纯文档 bug**，已写进 07 §5.2 和 03 §D3.1。

---

## §2 阅读范围与证据等级

### §2.1 一等证据：逐行读过

| 文件 | 行数 |
|---|---|
| `drivers/dma-buf/dma-buf.c` | 1639 |
| `drivers/dma-buf/dma-fence.c` | 965 |
| `drivers/dma-buf/dma-resv.c` | 782 |
| `drivers/dma-buf/dma-heap.c` | 326 |
| `drivers/dma-buf/heaps/system_heap.c` | 440 |
| `drivers/dma-buf/heaps/cma_heap.c` | 407 |
| `drivers/dma-buf/dma-buf-sysfs-stats.c` | 202 |
| `drivers/dma-buf/sync_debug.c` | 190 |
| `include/linux/dma-buf.h` · `dma-fence.h` · `dma-resv.h` | 641 + 662 + 485 |
| `include/uapi/linux/` 四个头 | 366 |
| `drivers/dma-buf/Kconfig` · `heaps/Kconfig` | 94 + 14 |
| `include/trace/events/dma_fence.h` · `drivers/dma-buf/sync_trace.h` | 89 + 33 |
| 两篇 rst | 434 |

**合计约 7800 行一等证据。**

### §2.2 二等证据：读了关键函数，没读全文

| 文件 | 读了什么 | 没读什么 |
|---|---|---|
| `drivers/dma-buf/sw_sync.c`（412） | `sw_sync_debugfs_open/release`、`timeline_fence_ops`、`sw_sync_ioctl_create_fence`、`:197` 的 tracepoint | ioctl 参数校验细节 |
| `drivers/dma-buf/udmabuf.c`（401） | `udmabuf_create`、seal 检查、`:366`/`:375` 的 misc 注册、`:381-382` 的 DMA 掩码 | `vm_ops` 细节 |
| `drivers/dma-buf/sync_file.c`（372） | `sync_file_create`、`:31` 的 `anon_inode_getfile`、merge/info ioctl | poll 实现细节 |
| `drivers/dma-buf/dma-fence-array.c`（279）· `-chain.c`（265）· `-unwrap.c`（172） | 各自的 `ops` 与创建函数 | 迭代器边界 |
| `kernel/dma/contiguous.c` | `rmem_cma_setup`（`:399-439`）· `early_cma`（`:77-96`）· `dma_contiguous_reserve`（`:168-199`） | 其余 |
| `mm/cma.c` | `cma_init_reserved_mem`（`:173-212`）· `cma_get_name`（`:52-55`） | 分配/迁移算法 |

### §2.3 三等证据：只做了符号级统计

- 4 个 selftest（`st-*.c`，2001 行）：**只统计了行数与覆盖范围，未读实现**（07 §8 的结论"没有 selftest 覆盖缓冲区共享层"来自文件名与 `Makefile` 目标，不是逐行确认每个测试用例）。
- 全树使用面统计：`#include <linux/dma-buf.h>` 66 个 `.c` · `dma-fence.h` 19 · `dma-resv.h` 22 · `dma_buf_export()` 核心外 16 处 · `dma_buf_attach()`/`_dynamic_attach()` 17 处 —— **只数了出现次数，没有逐个读上下文**。
- `MODULE_IMPORT_NS(DMA_BUF)` 24 个 `.c` —— 同上。

### §2.4 代表实现的选法

导出方代表选了 **`heaps/system_heap.c` 和 `heaps/cma_heap.c`**（合计 847 行），理由：
① 在树内、② 无外部硬件依赖、③ 两者恰好演示了"页数组"和"连续内存"两种极端、
④ arm64 上真实可用。
导入方代表选了 **`drivers/infiniband/core/umem_dmabuf.c`**（05 §2.3），
理由：它是树内**唯一**一个非 GPU 的动态 attach 使用者，
且明确拒绝不提供 `move_notify` 的调用方，教学价值最高。

⚠️ **没有选任何 GPU 驱动做代表**：DRM 侧的使用被 TTM/GEM 包了两层，
读它需要先读 drm 子系统，超出本套范围（见 §2.6）。

### §2.5 裁掉的范围登记

| 裁掉的 | 理由 |
|---|---|
| `drivers/gpu/drm/` 的全部使用面 | 属 drm 子系统；本套只到"dma-buf 提供什么"为止 |
| `mm/cma.c` 的迁移/分配算法 | 属内存管理子系统；06 篇只覆盖"DT 如何影响 heap 名字与存在性" |
| `restricted-dma-pool` 的实现 | 属 swiotlb；06 §4 只说明它**不会**变成 heap |
| `ww_mutex` 算法本身 | 属 `kernel/locking/`；02/03 只用它的 `-EDEADLK` 语义 |
| 4 个 selftest 的实现 | 三等证据，见 §2.3 |
| `dma_buf_map`/`iosys_map` 的完整语义 | 6.1 里正在改名迁移中，避免写成过时内容 |

### §2.6 与相邻层的边界

```
用户态（Android gralloc / GStreamer / Mesa）      ← 不覆盖
   │
dma-buf uapi（4 个头 / 9 个 ioctl）               ← ⚠️ 本套覆盖
   │
dma-buf 核心（dma_buf / dma_fence / dma_resv）    ← ⚠️ 本套覆盖（一等）
   │
导出方（heaps / udmabuf / GPU 驱动）              ← heaps + udmabuf 覆盖；GPU 不覆盖
   │
底层内存（CMA / 页分配器 / IOMMU / swiotlb）      ← 只到接口，不覆盖实现
```

### §2.7 ACPI 的裁剪登记

```bash
grep -rn 'acpi\|ACPI' drivers/dma-buf/ include/linux/dma-{buf,fence*,resv,heap}.h \
     include/linux/sync_file.h include/uapi/linux/{dma-buf,sync_file,dma-heap,udmabuf}.h
# ⇒ 0 行
```

⚠️ **本子系统 ACPI 零命中** ⇒ arm64-only 的裁剪对本套文档**没有产生任何删减**。
与 pwm 不同（pwm 核心里真有一条 `acpi_pwm_get()` 路径需要裁掉）。

⚠️ 顺带一条同类结论：

```bash
grep -rn '\bof_[a-z_]*(' drivers/dma-buf/       # ⇒ 0 行
```

**`drivers/dma-buf/` 里一处 `of_*` 调用都没有** —— 这是 06 §0 那句
"dma-buf 自己没有设备树绑定"的直接证据，不是推断。

---

## §3 符号存在性核查

### §3.1 核查口径（两级，全套统一）

| 级别 | 判定标准 | 文档里怎么写 |
|---|---|---|
| **存在** | `grep` 在源码里命中定义 | 可以描述其实现 |
| **外部驱动可用** | ⚠️ **`EXPORT_SYMBOL*` 且在 `include/linux/` 头里有声明** | 才能出现在 01 篇的"可调用接口"表里 |

⚠️ 本子系统有一个额外的第三条：**`EXPORT_SYMBOL_NS_GPL` 的 19 个符号还要求
调用方写 `MODULE_IMPORT_NS(DMA_BUF)`** ⇒ 01 篇给这 19 个单独标了列。
这是 pwm/tty/watchdog 都不存在的形态。

### §3.2 导出符号的三种宏，逐文件核实

```bash
grep -cE '^EXPORT_SYMBOL'        drivers/dma-buf/*.c   # 总数 70
grep -c  '^EXPORT_SYMBOL_NS_GPL' drivers/dma-buf/*.c   # 19
grep -c  '^EXPORT_SYMBOL_GPL'    drivers/dma-buf/*.c   # 10（不含 _NS_GPL）
```

| 文件 | 导出数 |
|---|---|
| `drivers/dma-buf/dma-fence.c` | **20** |
| `drivers/dma-buf/dma-buf.c` | **19**（⚠️ 其中 `_NS_GPL` 占绝大多数） |
| `drivers/dma-buf/dma-resv.c` | **17** |
| `drivers/dma-buf/dma-fence-array.c` | 5 |
| `drivers/dma-buf/dma-fence-chain.c` | 4 |
| `drivers/dma-buf/dma-fence-unwrap.c` | 3 |
| `drivers/dma-buf/sync_file.c` | 2 |
| `drivers/dma-buf/dma-heap.c` | ⚠️ **0** |
| `drivers/dma-buf/dma-buf-sysfs-stats.c` | ⚠️ **0** |

⚠️⚠️ **`dma-heap.c` 导出 0 个是本子系统一个关键事实**：
`dma_heap_add()` 等函数只在 `include/linux/dma-heap.h` 里声明、只能被**内建**代码调用
⇒ **out-of-tree 的 heap 模块在 6.1 里做不出来**（04 §1）。
这条结论完全建立在这个 0 上。

### §3.3 三个计数口径问题（抓到并修正的）

1. ⚠️ **结构体字段数不能用 `grep -c ';'`**。本套统一用**花括号深度**口径。
   `struct dma_buf_ops` 占 253 行（`include/linux/dma-buf.h:36-288`）
   但只有 **13** 个字段 —— 其余全是 kerneldoc 注释。用分号数会得到几十。
2. ⚠️ **`struct dma_fence` 的 union 按 1 个字段计**（`include/linux/dma-fence.h:84-90`
   的 `cb_list`/`timestamp`/`rcu` 三路 union）⇒ 全结构 **8** 个字段。
   按 union 成员展开会得到 10，两个数都对，**口径必须写明**（02 篇写明了）。
3. ⚠️ **`Kconfig:` 这样的裸文件名在本子系统是歧义的** ——
   `drivers/dma-buf/` 下有两个 Kconfig（自己的 94 行 + `heaps/` 的 14 行），
   加上 06 篇还引用了 `kernel/dma/Kconfig` 和 `mm/Kconfig`，共四个
   ⇒ **本套所有 Kconfig 引用一律写目录限定的完整路径**。

### §3.4 计数纪律（本套遵守的四条）

1. 结构体字段数一律花括号深度，并确认末行是 `};`（本子系统 **17 个结构全部是 `};`
   结尾，无 `__randomize_layout`** —— 与 tty 相反，那里踩过这个坑）。
2. 行号一律现场 `grep` 得到，**绝不凭记忆写**。
3. `sed -n 'A,Bp'` 的输出**永不**用相对偏移数行，一律
   `awk 'NR>=A&&NR<=B{printf "%d:%s\n",NR,$0}'` 带出真实行号。
4. 引用"产生某个 errno 的那一行"，不引用函数首行。

---

## §4 索引工具（cbm）的盲区

本套主要用 `rg`/`grep` 直接取证，cbm 索引只用于快速定位。已知盲区在本子系统的具体表现：

| 盲区 | 本子系统的实例 |
|---|---|
| 宏定义的符号索引不到 | `DEFINE_DMA_BUF_EXPORT_INFO`（`include/linux/dma-buf.h`）· `dma_resv_for_each_fence` · `for_each_sgtable_dma_sg` |
| tracepoint 生成的函数索引不到 | 全部 8 个 `trace_dma_fence_*` / `trace_sync_timeline` —— ⚠️ **07 §3 的触发点表全靠 `grep -rn 'trace_dma_fence'` 得到** |
| 间接调用（函数指针）不成边 | `dma_buf_ops` 13 个回调、`dma_fence_ops` 9 个回调、`dma_heap_ops` 1 个 —— ⚠️ **整个子系统的主要控制流都是间接调用，索引图基本看不到** |
| `RESERVEDMEM_OF_DECLARE` 这类段注册 | `kernel/dma/contiguous.c:440` → `rmem_cma_setup` 这条边（06 §1.1 的整条链） |

⚠️⚠️ **结论：本子系统是索引工具最不擅长的形态**（回调 + 宏 + 段注册三样齐全）
⇒ 本套所有调用关系都用 `grep` 交叉验证过，没有直接采信索引结果。

---

## §5 未验证项（四层）

### §5.1 第一层：未编译、未运行

⚠️⚠️ **本套所有代码骨架（05 篇）都没有编译过，没有在硬件上跑过。**
`output/` 是唯一可写目录，源码树只读，没有交叉编译环境。

具体风险点：
- 05 §1 的导出方骨架、§2 的导入方骨架、§3 的 fence 生产方骨架 —— **仅按头文件签名书写**。
- 05 §4 的用户态代码 —— 结构体字段名来自 uapi 头，**没有实际 `ioctl` 过**。
- 07 §2.2 的 `bufinfo` 样例输出 —— ⚠️ **是按 `seq_printf` 格式串手工推演的，不是真机 dump**。
- 07 §4 的 `sync/info` 样例输出 —— 同上。

### §5.2 第二层：读了符号没读实现

见 §2.3。其中最需要警惕的一条：
⚠️ **"没有 selftest 覆盖缓冲区共享层"**（07 §8）是从四个 `st-*.c` 的**文件名和
`Makefile` 目标**推出的，没有逐个读 1600 行测试用例确认里面没有偷偷测 `dma_buf_export`。
**信心：高但非确证。**

### §5.3 第三层：推断而非观察

| 结论 | 属于推断的部分 |
|---|---|
| "arm64 4K 页时 `CMA_MIN_ALIGNMENT_BYTES` 通常是 4 MiB"（06 §1.3） | ⚠️ 由 `pageblock_order` 推出，**没有在真机 `dmesg` 上确认过** |
| "`system_heap` 在 64K 页上退化"（04 §2.1） | 由 `system_heap.c` 的 order 数组推出，未实测 |
| "`trace_dma_fence_emit` 在 arm64 真机上一条不出"（07 §3.1） | ⚠️ **调用点只有 qxl 和 virtio-gpu 两处是确证的**（`grep` 全树）；"arm64 真机不出"是由此推断（qxl/virtio 都是虚拟设备） |
| "`DMABUF_SYSFS_STATS` 会让空 `exp_name` 的导出方失败"（05 §1.2、07 §5.4） | 代码路径是确证的（`:176-179`）；**"真实世界有这样的导出方"是推断** |
| "第 7 条是缺 heap 的最高频原因"（06 §5） | ⚠️ 由 40 vs 9 的静态普查推出，**不是故障统计** |

### §5.4 第四层：版本时效性

⚠️ 以下几处在 6.1 之后已知或很可能发生变化，引用本套文档时注意：

| 项 | 6.1 的状态 | 时效风险 |
|---|---|---|
| `DMABUF_SYSFS_STATS` | 标 `(DEPRECATED)`，Kconfig 说"should sooner or later be removed"（`drivers/dma-buf/Kconfig:88-90`） | ⚠️ **随时可能整个删掉** |
| `DMABUF_MOVE_NOTIFY` | 标 EXPERIMENTAL（`drivers/dma-buf/Kconfig:42`） | 语义可能变 |
| 用户态 fence / 无限期 fence | 被彻底禁止（`Documentation/driver-api/dma-buf.rst:263-272`） | ⚠️ 上游一直在讨论新方案（`drm_syncobj` timeline 等），**结论可能被推翻** |
| `dma_buf_map` → `iosys_map` 改名 | 6.1 正在迁移中 | 本套故意不深入（§2.5） |
| `include/linux/dma-buf.h:193` 的 TODO | 仍在 | 可能被实现 |

---

## §6 可重跑的数字

### 表 1 规模

| 项 | 数字 |
|---|---|
| `.c` 文件 / 行 | 19 / 9020 |
| 核心（除 selftest） | 6852 |
| 头文件 | 8 / 2212 |
| uapi | 4 / 366 |
| 导出符号 | 70 |
| Kconfig 选项 | 8 + 2 |
| tracepoint | 7 + 1 |
| 官方 rst | 434 |

### 表 2 结构体字段数（花括号深度口径，全部 `};` 结尾）

| 结构 | 位置 | 字段数 |
|---|---|---|
| `struct dma_buf_ops` | `include/linux/dma-buf.h:36-288` | 13 |
| `struct dma_buf` | `include/linux/dma-buf.h:302-458` | 17 |
| `struct dma_buf_attach_ops` | `include/linux/dma-buf.h:465-493` | 2 |
| `struct dma_buf_attachment` | `include/linux/dma-buf.h:517-527` | 9 |
| `struct dma_buf_export_info` | `include/linux/dma-buf.h:542-550` | 7 |
| `struct dma_fence` | `include/linux/dma-fence.h:65-96` | 8（含三路 union） |
| `struct dma_fence_cb` | `include/linux/dma-fence.h:116-119` | 2 |
| `struct dma_fence_ops` | `include/linux/dma-fence.h:125-260` | 9 |
| `struct dma_resv` | `include/linux/dma-resv.h:155-179` | 2 |
| `struct dma_resv_iter` | `include/linux/dma-resv.h:193-217` | 8 |
| `struct sync_file` | `include/linux/sync_file.h:35-54` | 7 |
| `struct dma_heap_ops` | `include/linux/dma-heap.h:23-28` | 1 |
| `struct dma_heap_export_info` | `include/linux/dma-heap.h:38-42` | 3 |
| `struct dma_fence_array` | `include/linux/dma-fence-array.h:37-46` | 6 |
| `struct dma_fence_array_cb` | `include/linux/dma-fence-array.h:23-26` | 2 |
| `struct dma_fence_chain` | `include/linux/dma-fence-chain.h:25-50` | 6 |
| `struct dma_fence_unwrap` | `include/linux/dma-fence-unwrap.h:18-31` | 3 |

### 表 3 用户态 ABI：9 个 ioctl，2 个字符设备

| ioctl | 定义 |
|---|---|
| `DMA_BUF_IOCTL_SYNC` | `include/uapi/linux/dma-buf.h:171` |
| `DMA_BUF_SET_NAME` / `_A` / `_B` | `include/uapi/linux/dma-buf.h:176-178`（⚠️ **同号三别名**） |
| `DMA_BUF_IOCTL_EXPORT_SYNC_FILE` | `include/uapi/linux/dma-buf.h:179` |
| `DMA_BUF_IOCTL_IMPORT_SYNC_FILE` | `include/uapi/linux/dma-buf.h:180` |
| `SYNC_IOC_MERGE` | `include/uapi/linux/sync_file.h:86` |
| `SYNC_IOC_FILE_INFO` | `include/uapi/linux/sync_file.h:96` |
| `DMA_HEAP_IOCTL_ALLOC` | `include/uapi/linux/dma-heap.h:50` |
| `UDMABUF_CREATE` / `_LIST` | `include/uapi/linux/udmabuf.h:30-31` |

| 字符设备 | 注册 |
|---|---|
| `/dev/dma_heap/*` | `drivers/dma-buf/dma-heap.c:313` `alloc_chrdev_region` + `:317` `class_create` |
| `/dev/udmabuf` | `drivers/dma-buf/udmabuf.c:366` `MISC_DYNAMIC_MINOR` + `:375` `misc_register` |

### 表 4 可观测面

| 面 | 位置 | 依赖 |
|---|---|---|
| `/proc/<pid>/fdinfo/<fd>` | `drivers/dma-buf/dma-buf.c:495-507` | ⚠️ **无** |
| `ls -l /proc/<pid>/fd/` 显示 `/dmabuf:<name>` | `drivers/dma-buf/dma-buf.c:45-58` + `:108` | ⚠️ **无** |
| `/sys/kernel/debug/dma_buf/bufinfo` | `drivers/dma-buf/dma-buf.c:1577-1598` | `DEBUG_FS` |
| `/sys/kernel/debug/sync/{info,sw_sync}` | `drivers/dma-buf/sync_debug.c:174-189` | `SW_SYNC` |
| `/sys/kernel/dmabuf/buffers/<ino>/` | `drivers/dma-buf/dma-buf-sysfs-stats.c:146-163` | `DMABUF_SYSFS_STATS`（DEPRECATED） |
| 8 个 tracepoint | `include/trace/events/dma_fence.h` + `drivers/dma-buf/sync_trace.h` | `FTRACE` |
| `dma_resv_lockdep()` | `drivers/dma-buf/dma-resv.c:742-780` | `PROVE_LOCKING` |

### 表 5 错误码普查（5 个核心 `.c`）

`-EINVAL` **47** · `-ENOMEM` 18 · `-EFAULT` 11 · `-ERESTARTSYS` 7 · `-ENOENT` 6 ·
`-ENOTTY` 3 · `-EBUSY` 3 · `-EINTR` 2 · `-EDEADLK` 2 · `-EBADF` 2 ·
`-EOVERFLOW` 1 · `-ENODEV` 1 · `-EAGAIN` 1

⚠️ `-EDEADLK` 来自 `ww_mutex`，**是 dma-resv 的核心语义而不是异常**（02 §4、08 §8）。

### 表 6 设计债

| 项 | 数字 |
|---|---|
| 核心 `.c` + 三个主头文件里的 `FIXME/TODO/XXX` | ⚠️ **只有 1 处** |
| 那 1 处 | `include/linux/dma-buf.h:193`：`TODO: Add try_map_dma_buf version, to return immed with -EBUSY` |
| rst 里的纯设计论证 | ⚠️⚠️ **146 行**（`dma-buf.rst:203-272` + `:274-348`） |

⚠️⚠️ **和 pwm 完全同型的一条结论：真正的设计债写在文档里，不在代码里。**
但程度更极端 —— pwm 是文档里一句"should be fixed soon"，
dma-buf 是**两节共 146 行、结论是"这个问题在内核内部无解"**。

### 表 7 全树使用面

| 统计 | 数字 |
|---|---|
| `#include <linux/dma-buf.h>` 的 `.c` | 66 |
| `#include <linux/dma-resv.h>` | 22 |
| `#include <linux/dma-fence.h>` | 19 |
| `MODULE_IMPORT_NS(DMA_BUF)` 的 `.c` | **24** |
| 核心外 `dma_buf_export()` | 16 |
| 核心外 `dma_buf_attach()` / `_dynamic_attach()` | 17 |
| `trace_dma_fence_emit` 的调用点 | ⚠️ **2**（`drivers/gpu/drm/qxl/qxl_release.c:427` · `drivers/gpu/drm/virtio/virtgpu_fence.c:114`） |

### 表 8 arm64 与设备树

| 统计 | 数字 |
|---|---|
| `arch/arm64/boot/dts/` 含 `shared-dma-pool` 的文件 | **40** |
| 其中含 `linux,cma-default` | ⚠️ **9** |
| 含 `memory-region` | 54（与 dma-buf 无关，06 §3） |
| `drivers/dma-buf/` 里的 `of_*` 调用 | ⚠️ **0** |
| `drivers/dma-buf/` 里的 ACPI 命中 | ⚠️ **0** |

---

## §7 三个自检脚本的结果

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/dma-buf
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh dma-buf $D .
```

| 脚本 | 结果 |
|---|---|
| `check_xrefs.py` | **10 篇；跨篇引用命中 190 条，失效 0 条** |
| `check_line_refs.py` | **395 条唯一 `file:line` 引用；无解析不到的文件，无越界行号，无歧义简写** |
| `check_doc_coverage.sh` | **必查档 `driver-api/dma-buf` 引用 18 次；无 0 引用篇目** |

### §7.1 全套体量与格式指标

| 篇 | 行数 | `**` 对/行 |
|---|---|---|
| 00-overview | 269 | 0.279 |
| 01-api | 654 | 0.239 |
| 02-datastructures | 583 | 0.259 |
| 03-internals | 753 | 0.214 |
| 04-extensions | 637 | 0.226 |
| 05-usage | 682 | 0.141 |
| 06-devicetree | 377 | 0.223 |
| 07-debug | 533 | 0.184 |
| 08-feynman | 403 | 0.256 |
| 99-evidence | 596 | 0.255 |
| **合计** | **5487** | **均值 0.223** |

| 指标 | 目标 | 实际 |
|---|---|---|
| 实心五角星重要度标记 | **0** | ✔ **0**（全套 10 篇一个都没有） |
| `**` 对/行 | ≤ 0.25 | ✔ 均值 **0.223**；⚠️ **00 / 02 / 08 / 99 四篇略超（0.279 / 0.259 / 0.256 / 0.255）** |

⚠️ 00 和 99 偏高有结构性原因：两篇都以表格为主体，正文密度天然高，几乎每行都是结论。
**这里如实登记而不强行削减** —— 削 00 的粗体等于把结论变成平文，可读性反而下降。
（用户 2026-08-24 的原话是「实心五角星清除掉就可以了，加粗关系不大」⇒ **本套不跑削粗体的机械化处理**。）

### §7.2 修脚本报错时改了什么

三处，全部记录：

1. **`check_line_refs.py` 在 06 写完后报 `shared-dma-pool.yaml` 5 处 UNRESOLVED**。
   原因：该路径在全套里只以缩写形式（不带 `:行号`）出现过 ⇒ 消歧表学不到它。
   修法：在 `06-devicetree.md` §1.2 把全路径 + 行号写全一次
   （`Documentation/devicetree/bindings/reserved-memory/shared-dma-pool.yaml:13`）。
   ⚠️⚠️ **这是本套踩到的同类问题第三次** ⇒ 已进 §10.2 回写清单。
2. **`check_xrefs.py` 在 99 写完后报 13 条失效**。
   ⚠️ 根因是 `04-extensions.md` 的 7 个大节用了 **`# §N`（一级标题）** 而不是
   `## §N` ⇒ 脚本的标题扫描（只认 `##`/`###`）看不到它们，
   于是所有指向 `04 §1`…`04 §7` 的引用全被判失效。
   修法：把 04 的 7 个一级标题降为 `##`，并把它的 `##  §N.M` 子节降为 `###`
   （层级也因此与其余九篇一致）。
3. 两条真错的引用（下面刻意不写成可被脚本识别的引用形式，否则本篇自己就会报失效）：
   - `02-datastructures.md:573` 原先指向 03 篇一个纯数字编号的第三节 ——
     ⚠️ **03 篇用的是字母前缀编号（§A1…§D4），根本没有纯数字节**
     ⇒ 改指 **03 §C4**（RCU 与 `dma_fence_get_rcu` 在那里）。
   - 本篇 `99-evidence.md:85` 原先指向一篇编号 09 的文档 —— ⚠️ **本套只有十篇，没有 09**
     （这是把 `99` 手打成 `09` 的笔误）⇒ 改指 **03 §D3.1**。

⚠️ **第 2 条的教训**：脚本"失效"报告里如果同一目标篇集中出现大量失效，
**先怀疑那一篇的标题层级，不要逐条改引用**。

---

## §8 逐篇取证表

| 篇 | 主要证据来源 | 最薄弱的一处 |
|---|---|---|
| **00** | 纯浓缩 01–08，零新推导 | 无独立证据；⚠️ **任何 00 篇里的数字都应回 99 §6 核对** |
| **01** | `grep -nE '^EXPORT_SYMBOL' drivers/dma-buf/*.c` + 四个 uapi 头逐行 | ⚠️ 返回值语义部分来自 kerneldoc，**kerneldoc 本身可能陈旧** |
| **02** | 17 个结构逐个花括号深度计数 + `include/linux/dma-resv.h` 的 usage 枚举 | union 口径见 §3.3 第 2 条 |
| **03** | 四个核心 `.c` 逐行 | ⚠️ 锁序结论依赖 `dma_resv_lockdep()` 的代码，**未在开了 `PROVE_LOCKING` 的内核上实测** |
| **04** | `drivers/dma-buf/Makefile` 18 行 + 两个 Kconfig + 五个可选层的实现 | selftest 层是三等证据（§2.3） |
| **05** | 由 01/02/03 的接口形状 + 两个 heap + `umem_dmabuf.c` 反推 | ⚠️⚠️ **全部代码未编译**（§5.1） |
| **06** | `shared-dma-pool.yaml` 97 行 + `kernel/dma/contiguous.c` + `mm/cma.c` + arm64 DTS 普查 | ⚠️ 4 MiB 对齐下限是推断（§5.3） |
| **07** | 每个观测面的实现逐行 + `grep` 确认 tracepoint 触发点 | ⚠️ 两处样例输出是手工推演（§5.1） |
| **08** | `dma-fence.c:69-112` 契约 + `dma-buf.rst:203-348` 两节论证 | ⚠️ 推导本身是复述 + 重组，**不是本套原创**；原创部分只有"强制强度与规则重要性不成正比"（§9 第 4 条） |
| **99** | 本篇自身 | —— |

---

## §9 交叉验证：初始假设被推翻的五条

### 1. "dma-buf 是 dmaengine 的一部分" —— 立刻被否

⚠️ 开工第一件事就确认了 `drivers/dma-buf/` 与 `drivers/dma/`（dmaengine）
**没有任何代码关系**：前者是缓冲区共享框架，后者是 DMA 控制器驱动框架。
两者唯一的共同点是名字里都有 dma。**PROGRESS 台账里为此专门加了一行提醒。**

### 2. "`dma_fence_emit` 是核心触发的" —— 错

初始假设：7 个 tracepoint 都由 `dma-fence.c` 触发。
实测 `grep -n 'trace_dma_fence' drivers/dma-buf/dma-fence.c` 只有 **6 处**
（`:384 :514 :519 :536 :592 :963`），缺 `emit`。
再 `grep -rn 'trace_dma_fence_emit'` 全树 ⇒ **只有 2 个调用点，都在 DRM 驱动**
（`drivers/gpu/drm/qxl/qxl_release.c:427` · `drivers/gpu/drm/virtio/virtgpu_fence.c:114`）。
⇒ 07 §3.1 的结论从"核心不触发"升级为"arm64 真机上一条不出"。

### 3. "两处 `count` 是同一个口径" —— 错

`fdinfo` 打的是 `file_count(dmabuf->file) - 1`（`drivers/dma-buf/dma-buf.c:501`，
注释明说要减掉 procfs 自己那份临时引用），
而 `bufinfo` 打的是 `file_count()` **不减**。
⇒ ⚠️ **同一块缓冲区在两个接口里显示的 `count` 差 1**。
这条只有把两处 `seq_printf` 并排看才会发现，写进了 07 §2.2。

### 4. "重要的规则一定有强制检查" —— 错，而且反过来

初始假设：既然"禁止无限期 fence"是最重要的规则，代码里一定有检查。
实测：**没有任何编译期或运行期检查**，全靠 review。
而唯一无条件生效的强制点是 `BUG_ON`（`drivers/dma-buf/dma-resv.c:307`），
管的是"忘了 `dma_resv_reserve_fences()`"这个相对次要的问题。
⇒ ⚠️⚠️ **强制强度与规则重要性完全不成正比** —— 这是 08 §9 的结论，
也是本套唯一一条不来自任何现有文档的原创判断。

### 5. "`bufinfo` 里没有 fence 就等于 resv 上没有 fence" —— 错

`dma_resv_describe()` 用 `DMA_RESV_USAGE_READ` 起迭代
（`drivers/dma-buf/dma-resv.c:733`），而 usage 是有序枚举
⇒ **`BOOKKEEP` 级别的 fence 不在遍历范围内，永远不会被打印**。
⇒ 07 §2.2 加了这条警告。**这是"调试工具本身有盲区"的一个实例。**

---

## §10 对照与回写

### §10.1 rule-29 独立对照：本子系统没有对照套

```bash
ls -d output/qoder/dma-buf     # ⇒ No such file or directory
ls output/qoder/               # ⇒ clk  dma  gpio  irq  pinctrl  spi
```

⚠️ **`output/qoder/` 下只有 6 个子系统（clk / dma / gpio / irq / pinctrl / spi），
没有 dma-buf** ⇒ **rule-29 的独立对照无法进行**。

⚠️ 注意 `output/qoder/dma` 是 **dmaengine** 的对照套，**不是本子系统的**
（见 §9 第 1 条）⇒ **绝不能拿它来对照**。

⇒ 与 regulator/mailbox/reset/cpufreq/remoteproc/irq/cdev/of/timer/virtio/
regmap/phy/watchdog/tty/pwm 相同处理：**登记"无对照套"，不做对照，不虚构对照结论。**

### §10.2 要回写 skill 的五条

1. ⚠️⚠️ **缩写路径必须在同一套文档里至少出现一次"全路径 + 行号"形式**，
   否则 `check_line_refs.py` 的消歧表学不到它。本套第三次踩（前两次是
   `uapi/*.h` 和 ABI 文件，本次是 DT 绑定 yaml）⇒ **应写进 skill 的硬规则**：
   头文件、ABI 文件、DT 绑定第一次出现时一律写全路径带行号。
2. ⚠️⚠️ **章节标题层级必须统一用 `## §N` / `### §N.M`**。
   本套 04 篇用了 `# §N`，导致 `check_xrefs.py` 报 11 条假失效。
   ⇒ skill 应明确"大节 `##`、子节 `###`，**不用 `#`**（`#` 只留给文档标题）"。
3. ⚠️ **多个同名 Kconfig / Makefile 的子系统，引用一律写目录限定路径**。
   本套涉及四个 Kconfig（`drivers/dma-buf/` · `drivers/dma-buf/heaps/` ·
   `kernel/dma/` · `mm/`）⇒ 只写文件名加行号（不带目录）是歧义的。
4. ⚠️ **"框架型 + 有 uapi + 无 DT 绑定"是一种独立形态**，06 篇不能空写也不能硬套
   provider/consumer 模板。本套的处理方式（改写成"唯一一条间接链路"+ 现状普查
   + 排查清单）建议进 skill 作为该形态的 06 篇模板。
5. ⚠️ **设计债在 rst 而不在代码里，是框架型子系统的常态**（pwm 一句、
   dma-buf 两节 146 行）⇒ 99 §6 的"设计债"表应固定包含"代码里的 TODO 数"和
   "官方文档里的设计论证行数"两栏，而不是只数 TODO。

### §10.3 明确不吸收的一条

⚠️ **不把"08 篇必须原创推导"写进 skill**。
本套 08 篇的主体是复述 + 重组内核自己的两处论证
（`dma-fence.c:69-112` 和 `dma-buf.rst:203-348`），原创部分只有 §9 一节。
**对于上游已经把设计论证写清楚的子系统，忠实复述并标明出处比强行原创更有价值**
—— 强行原创会产生看似深刻实则无据的内容，这正是要避免的。

---

## §11 一句话自评

**结构可靠、行号可核、口径写明；最大的短板是全套代码一行都没编译过（§5.1），
最有价值的一条是"最重要的规则恰恰没有任何机器强制"（§9 第 4 条）。**
