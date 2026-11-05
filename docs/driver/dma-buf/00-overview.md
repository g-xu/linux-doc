---
title: 总览
---

# dma-buf 00：总览

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

本篇是 01–08 的**纯浓缩入口**，不引入任何新推导。每一节末尾给出深读去处。

⚠️ **本子系统指 `drivers/dma-buf/`（缓冲区共享框架），不是 dmaengine（`drivers/dma/`）。**
两者名字接近但毫无关系。

---

## §1 一句话

**dma-buf 让多个设备、多个进程零拷贝地共享同一块内存，并用 fence 描述"什么时候能访问"。**

它解决的原始问题：摄像头拍一帧 → GPU 处理 → 显示控制器送屏，三个设备一块内存不许拷贝。
既有机制（文件、`memfd`、`mmap`、传物理地址）全部不够用，因为设备看到的地址各不相同。
⇒ 句柄必须是 **fd**（可跨进程传、有引用计数、可 `poll`）。

**三条不变量撑起整个子系统**：

| # | 不变量 | 后果 |
|---|---|---|
| I1 | 共享的是**访问权**不是地址 | 必须有 `attach` 这一步，每个设备自己 map |
| I2 | 访问是**异步**的 | 需要 `dma_fence` + `dma_resv` |
| I3 | ⚠️ **fence 必须在有界时间内完成** | 推出 GFP 限制、锁序、无限期 fence 禁令 |

→ 三条不变量的完整推导：**08-feynman.md**

---

## §2 规模与文件地图

| 项 | 数字 |
|---|---|
| `drivers/dma-buf/` | 19 个 `.c` / 9020 行（含 selftest 2168 行） |
| **核心** | **6852 行** |
| 头文件 | 8 个 / 2212 行（`include/linux/dma-fence.h` 662 · `dma-buf.h` 641 · `dma-resv.h` 485） |
| uapi | 4 个头 / 366 行 |
| 导出符号 | **70 个**（⚠️ 三种宏混用，见 §7） |
| 官方文档 | `Documentation/driver-api/dma-buf.rst` 348 行 + `sync_file.rst` 86 行 |

**核心文件按职责分四组**：

| 组 | 文件 | 行数 | 干什么 |
|---|---|---|---|
| **缓冲区共享** | `dma-buf.c` | 1639 | `dma_buf` 对象、fops、attach/map、mmap、ioctl |
| **同步** | `dma-fence.c` · `dma-resv.c` | 965 + 782 | fence 状态机 · 缓冲区上的 fence 集合 |
| **fence 容器** | `dma-fence-array.c` · `-chain.c` · `-unwrap.c` | 279 + 265 + 172 | 多 fence 聚合、时间线串联、展开 |
| **用户态出口** | `sync_file.c` · `dma-heap.c` · `heaps/*` · `udmabuf.c` · `sw_sync.c` | 372 + 326 + 847 + 401 + 412 | fence↔fd · 分配器 · 两个 heap · memfd 转 dma-buf · 测试用假 fence |
| 观测 | `dma-buf-sysfs-stats.c` · `sync_debug.c` | 202 + 190 | sysfs（已废弃）· debugfs |

→ 逐文件职责与调用链：**03-internals.md**

---

## §3 三层模型

```
        ┌── 用户态 ────────────────────────────────────────────┐
        │  /dev/dma_heap/{system,<cma 名>}   ALLOC ioctl        │
        │  /dev/udmabuf                     CREATE ioctl       │
        │  dma-buf fd:  mmap · SYNC · SET_NAME · SYNC_FILE 进出 │
        └───────────────────────┬──────────────────────────────┘
                                │  fd（SCM_RIGHTS 可跨进程）
        ┌───────────────────────▼──────────────────────────────┐
        │  struct dma_buf ── resv ──► struct dma_resv           │
        │       │  (ops: 13 个回调)          │  fence 列表       │
        │       └── attachments ──► dma_buf_attachment ──► sg_table
        └───────────────────────┬──────────────────────────────┘
                导出方 exporter │ 导入方 importer
        ┌───────────────────────▼──────────────────────────────┐
        │  heap / GPU 驱动 / v4l2 / udmabuf   ←→   GPU / VPU / RDMA │
        └──────────────────────────────────────────────────────┘
```

**四类角色，四套 API**：

| 角色 | 关键调用 | 章节 |
|---|---|---|
| 导出方 | `dma_buf_export()` + `dma_buf_fd()` + 实现 `dma_buf_ops` | 01 §2.1、05 §1 |
| 导入方 | `dma_buf_get()` → `attach()` → `map_attachment()` → 用 → 严格反序拆 | 01 §2.2、05 §2 |
| fence 生产方 | `dma_fence_context_alloc()` 一次 + `dma_fence_init()` 每个 + `dma_fence_signal()` | 01 §3、05 §3 |
| 用户态 | heap ALLOC → mmap → SYNC → 传 fd | 05 §4 |

---

## §4 五个核心对象

| 对象 | 定义 | 字段数 | 一句话 |
|---|---|---|---|
| `struct dma_buf` | `include/linux/dma-buf.h:302-458` | 17 | 缓冲区本身，一块内存一份 |
| `struct dma_buf_ops` | `include/linux/dma-buf.h:36-288` | 13 | 导出方要实现的回调；⚠️ 三个是真必需（`map_dma_buf`/`unmap_dma_buf`/`release`） |
| `struct dma_buf_attachment` | `include/linux/dma-buf.h:517-527` | 9 | 「某个设备对这块缓冲区的访问」，每设备一份 |
| `struct dma_fence` | `include/linux/dma-fence.h:65-96` | 8 | 一次性完成事件；⚠️ 含 `cb_list`/`timestamp`/`rcu` 三路 union（`:84-90`） |
| `struct dma_resv` | `include/linux/dma-resv.h:155-179` | **2** | 只有一把 `ww_mutex` + 一串 fence |

**`enum dma_resv_usage` 是 6.1 的核心语义，且有序**：
`KERNEL` > `WRITE` > `READ` > `BOOKKEEP` —— 等 `READ` 的人自动等 `WRITE` 和 `KERNEL`。

→ 全部 17 个结构的字段清单与 union 生命周期：**02-datastructures.md**

---

## §5 最小可用代码

**导出方**（三步，`exp_name` 不能为空）：

```c
static const struct dma_buf_ops my_ops = {
        .map_dma_buf   = my_map,      /* 必需 */
        .unmap_dma_buf = my_unmap,    /* 必需 */
        .release       = my_release,  /* 必需 */
};
DEFINE_DMA_BUF_EXPORT_INFO(exp_info);
exp_info.ops = &my_ops;  exp_info.size = size;  exp_info.priv = obj;
struct dma_buf *dmabuf = dma_buf_export(&exp_info);
int fd = dma_buf_fd(dmabuf, O_CLOEXEC);
```

**导入方**（拆的顺序绝不能乱）：

```c
dmabuf = dma_buf_get(fd);
att    = dma_buf_attach(dmabuf, dev);
sgt    = dma_buf_map_attachment(att, DMA_BIDIRECTIONAL);
/* ⚠️ 只读 sg_dma_address()/sg_dma_len()，绝不碰 sg_page() */
dma_buf_unmap_attachment(att, sgt, DMA_BIDIRECTIONAL);
dma_buf_detach(dmabuf, att);
dma_buf_put(dmabuf);
```

**用户态**（arm64 上 `SYNC` 不是可选项）：

```c
int h = open("/dev/dma_heap/system", O_RDWR);
struct dma_heap_allocation_data d = { .len = SZ_8M, .fd_flags = O_RDWR|O_CLOEXEC };
ioctl(h, DMA_HEAP_IOCTL_ALLOC, &d);          /* ⚠️ d.fd 传入时必须是 0 */
void *p = mmap(NULL, SZ_8M, PROT_READ|PROT_WRITE, MAP_SHARED, d.fd, 0);
struct dma_buf_sync s = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW };
ioctl(d.fd, DMA_BUF_IOCTL_SYNC, &s);  /* 读写 */  s.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_RW;
ioctl(d.fd, DMA_BUF_IOCTL_SYNC, &s);
```

→ 完整骨架、动态 attach、fence 生产方模板、18 条陷阱表：**05-usage.md**

---

## §6 十条必须记住的约束

按"踩上去有多贵"排序：

| # | 约束 | 违反的后果 |
|---|---|---|
| 1 | ⚠️⚠️ **加 fence 前必须 `dma_resv_reserve_fences()`** | `BUG_ON`，`drivers/dma-buf/dma-resv.c:307` 内核挂 |
| 2 | ⚠️⚠️ **fence signal 路径上不许分配内存** | lockdep splat；真实场景下 OOM 时死锁 |
| 3 | ⚠️⚠️ **导入方绝不碰 `sg_page()`** | 有 IOMMU/swiotlb 时数据静默错，`DMABUF_DEBUG` 才炸 |
| 4 | ⚠️ **fence 必须有界完成，兜底在生产方** | 一个不守约的驱动挂住全系统 |
| 5 | ⚠️ **无限期 fence 一律禁止（加超时也不行）** | 内核造出用户态看不见的随机死锁 |
| 6 | ⚠️ **arm64 上 CPU 访问必须 `SYNC START/END` 成对** | cache 不一致 ⇒ 花屏/脏数据 |
| 7 | ⚠️ **拆解顺序 unmap → detach → put，不可乱** | 引用计数错乱、UAF |
| 8 | ⚠️ **`exp_name` 不能为空** | 开 `DMABUF_SYSFS_STATS` 后 `dma_buf_export()` 直接 `-EINVAL` |
| 9 | ⚠️ **每条时间线一个 `context`，不是每个 fence 一个** | fence 先后无法比较，resv 的替换优化失效 |
| 10 | ⚠️ **`dma_fence_wait()` 的三种返回值语义各自独立** | 把"超时"当"失败"或反之 |

→ 逐条的代码依据与推导：**05 §5**（清单）+ **08 §5/§8**（为什么）

---

## §7 编译与配置速查

**8 个 Kconfig 选项**（`drivers/dma-buf/Kconfig` 94 行 + `heaps/Kconfig` 14 行）：

| 选项 | 位置 | 生产内核建议 | 备注 |
|---|---|---|---|
| `DMA_SHARED_BUFFER` | 由使用者 select | y | 核心，无独立菜单项 |
| `SYNC_FILE` | `drivers/dma-buf/Kconfig:4` | 按需 | 用户态要 fence fd 就得开 |
| `SW_SYNC` | `:20` | ⚠️ **n** | help 自陈可被用户态挂死内核 |
| `UDMABUF` | `:33` | 按需 | memfd → dma-buf，虚拟化场景 |
| `DMABUF_MOVE_NOTIFY` | `:42` | n | ⚠️ EXPERIMENTAL；但 n 意味着用户态可 pin 内存 |
| `DMABUF_DEBUG` | `:54` | n（CI 必须 y） | `default y if DMA_API_DEBUG` |
| `DMABUF_SELFTESTS` | `:63` | n | tristate，可做模块 |
| `DMABUF_SYSFS_STATS` | `:77` | ⚠️ **n** | Kconfig 自标 `(DEPRECATED)` |
| `DMABUF_HEAPS` | `:68` | y | `/dev/dma_heap/` |
| `DMABUF_HEAPS_SYSTEM` | `drivers/dma-buf/heaps/Kconfig:1` | y | ⚠️ **bool，不能 `=m`** |
| `DMABUF_HEAPS_CMA` | `drivers/dma-buf/heaps/Kconfig:8` | 按板子 | ⚠️ 同上；还需 `DMA_CMA` + DT |

⚠️⚠️ **`EXPORT_SYMBOL_NS_GPL` 的 19 个符号在 `DMA_BUF` 命名空间里**
⇒ 外部模块调 `dma_buf_*` 必须写 `MODULE_IMPORT_NS(DMA_BUF)`，否则链接失败。
`dma_fence_*` / `dma_resv_*` / `sync_file_*` **不需要**。全树已有 24 个 `.c` 这么写。

→ 各选项到底改变什么行为、heap 为什么只能内建：**04-extensions.md**

---

## §8 设备树：只有一条间接链路

⚠️ **dma-buf 自己没有任何 DT 绑定**（无 provider、无 consumer、无 schema）。
唯一相关的是 **CMA heap 的内存来源**：

```
DT /reserved-memory 节点（compatible="shared-dma-pool"; reusable; linux,cma-default;）
   → kernel/dma/contiguous.c:440 → :429-430 设为默认 CMA 区
   → drivers/dma-buf/heaps/cma_heap.c:397 取默认区 → :380 用区名当 heap 名
   → /dev/dma_heap/<设备树节点名>
```

⚠️⚠️ **arm64 树里 40 个文件写了 `shared-dma-pool`，只有 9 个写了 `linux,cma-default`**
⇒ 其余板子哪怕开了 `DMABUF_HEAPS_CMA=y`，`/dev/dma_heap/` 里也只有 `system`，
**而且开机毫无提示**（`drivers/dma-buf/heaps/cma_heap.c:398` 静默返回 0）。

→ 属性校验、`cma=` 命令行如何顶掉 DT、十步排查清单：**06-devicetree.md**

---

## §9 出问题先看哪里

| 症状 | 第一件事 | 章节 |
|---|---|---|
| 缓冲区不释放 | `cat /proc/<pid>/fdinfo/<fd>` 看 `count` ⚠️ **零配置可用** | 07 §1 |
| 想知道全系统有哪些缓冲区 | `cat /sys/kernel/debug/dma_buf/bufinfo` | 07 §2 |
| 进程卡在 D 状态 | `dma_fence_wait_start` 有没有配对的 `wait_end` | 07 §3 |
| 数据损坏（arm64） | 用户态漏了 `DMA_BUF_IOCTL_SYNC`？ | 05 §4.2 |
| 只在某些机器崩 | `CONFIG_DMABUF_DEBUG` 是否开着 | 07 §6 |
| lockdep splat 带 `fs_reclaim` | signal/wait 路径上分配了内存 | 07 §7 |
| `/dev/dma_heap/<cma>` 缺失 | 有没有 `linux,cma-default` | 06 §5 |

⚠️ **核心几乎不打日志** —— `dma-buf.c` 全文件的用户可见日志只有一条 `pr_debug`。
可观测性完全依赖上表这些结构化设施，不是 `dmesg`。

⚠️ 跨工具关联的唯一稳定标识是 **inode 号**（fdinfo 的 `ino` / bufinfo 的 `ino` /
sysfs 的目录名，三处同源）。

→ 全部 9 类工具、格式逐列解释、两套推荐配置：**07-debug.md**

---

## §10 与本套其它子系统的形态差异

| 维度 | pwm / clk / gpio / phy / reset | **dma-buf** |
|---|---|---|
| 类型 | 框架型 + provider/consumer | 框架型 |
| 设备树 | 核心机制 | ⚠️ **无绑定**（只有 CMA 这条间接链路） |
| 用户态 ABI | sysfs 或无 | ⚠️ **4 个 uapi 头 / 9 个 ioctl / 2 个字符设备** |
| 导出符号 | 单一宏 | ⚠️ **三种混用，19 个带命名空间** |
| 设计债在哪 | 代码里的 `TODO` | ⚠️ **代码里只有 1 处 TODO，真正的债写在 rst 的两节论证里** |
| 最难的部分 | 硬件抽象 | ⚠️ **跨驱动同步契约**（08 篇整篇在讲这个） |

⚠️ **ACPI**：本子系统 ACPI 零命中，arm64-only 的裁剪对本套文档无影响（99 §2.7）。

---

## 十篇导航

| 篇 | 内容 | 什么时候读 |
|---|---|---|
| **00**（本篇） | 总览 | 第一次接触，或复习 |
| **01-api.md** | 70 个导出符号 + 9 个 ioctl 的签名与返回值 | 查接口 |
| **02-datastructures.md** | 17 个结构、`dma_resv_usage` 语义 | 看代码时对照 |
| **03-internals.md** | 核心实现：fops、attach、sg 表、fence 状态机、锁 | 改核心代码 |
| **04-extensions.md** | 两个 heap、udmabuf、sw_sync、fence 容器、Kconfig | 加新导出方 |
| **05-usage.md** | 四类角色的可编译骨架 + 18 条陷阱 | ⚠️ **写代码前必读** |
| **06-devicetree.md** | `/reserved-memory` → CMA heap 这一条链 | 板子上缺 heap |
| **07-debug.md** | 9 类调试设施 + 症状对照表 | 出问题时 |
| **08-feynman.md** | 三条不变量的第一性推导 | ⚠️ **想彻底理解"为什么这么设计"** |
| **99-evidence.md** | 全部证据、口径、发现的文档缺陷 | 质疑本套结论时 |

⚠️ **推荐路径**：00 → 05（先能写对）→ 08（再理解为什么）→ 按需查 01/02/03。
**不建议**从 03 开始 —— 不先读 08，很多实现选择看起来像是随意的。
