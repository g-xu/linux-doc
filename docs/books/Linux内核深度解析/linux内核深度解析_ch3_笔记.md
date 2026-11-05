# 第 3 章 内存管理 — 深度总结（3.1~3.10）

> 原书：《Linux内核深度解析》余华兵著，基于 ARM64 / Linux 4.x
> 本文档：**基于 Linux 6.1.158 内核源码参考整理**，架构限定为 ARM64

---

## 3.1 概述

### 内存管理子系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Linux 内存管理分层架构                            │
│                                                                     │
│  用户空间                                                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│  │ 应用程序  │ │ 应用程序  │ │ 应用程序  │ │ 应用程序  │               │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘               │
│       │  malloc/free    ptmalloc  jemalloc  tcmalloc                │
│  ═════╪═══════════════════════════════════════════════════════════   │
│       │  sys_brk / sys_mmap / sys_munmap                            │
│  内核空间                                                            │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │ 第 1 层：虚拟内存管理                                      │       │
│  │  VMA 管理 │ 页表管理 │ 缺页异常处理 │ 内存控制器(memcg)    │       │
│  └────────────────────────┬─────────────────────────────────┘       │
│                           │ 调用                                    │
│  ┌────────────────────────▼─────────────────────────────────┐       │
│  │ 第 2 层：上层分配器（构建在伙伴分配器之上）                  │       │
│  │                                                          │       │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────────┐        │       │
│  │  │ 块分配器  │  │ 不连续页分配器 │  │ 每处理器分配器│        │       │
│  │  │(SLUB/SLAB)│  │  (vmalloc)   │  │  (percpu)   │        │       │
│  │  │           │  │              │  │              │        │       │
│  │  │ kmalloc() │  │ vmalloc()    │  │alloc_percpu()│        │       │
│  │  └─────┬─────┘  └──────┬──────┘  └──────┬───────┘        │       │
│  └────────┼───────────────┼────────────────┼────────────────┘       │
│           │               │                │                        │
│  ┌────────▼───────────────▼────────────────▼────────────────┐       │
│  │ 第 3 层：伙伴分配器 (Buddy System)                         │       │
│  │                                                          │       │
│  │  alloc_pages() / __free_pages()                           │       │
│  │  管理所有物理页帧，按 2^order 分配/释放                     │       │
│  │  每个 zone 维护 free_area[MAX_ORDER] 空闲链表               │       │
│  └────────────────────────┬─────────────────────────────────┘       │
│                           │                                         │
│  ┌────────────────────────▼─────────────────────────────────┐       │
│  │ 第 4 层：物理内存组织                                       │       │
│  │  node (pglist_data) → zone (struct zone) → page (struct page)   │
│  └──────────────────────────────────────────────────────────┘       │
│                                                                     │
│  硬件                                                               │
│  ┌──────────┐  ┌──────────┐                                        │
│  │ ARM64    │  │ 物理内存  │                                        │
│  │ MMU/TLB  │  │ DRAM     │                                        │
│  └──────────┘  └──────────┘                                        │
└─────────────────────────────────────────────────────────────────────┘

各分配器的关系：
  SLUB/SLAB：从 buddy 获取整页 → 切分成小对象（解决内部碎片）
  vmalloc：  从 buddy 逐页获取物理页 → 通过页表映射到连续虚拟地址
  percpu：   第一个 chunk 从线性映射区分配，后续 chunk 通过 vmalloc 分配
```

### 用户空间与内核空间的内存分配

| 层面 | 分配接口 | 底层路径 |
|---|---|---|
| 用户空间 | `malloc()`/`free()` | ptmalloc → brk/mmap 系统调用 → 虚拟内存管理 → 缺页时 buddy 分配物理页 |
| 内核空间 | `kmalloc(size, gfp)` | SLUB → buddy → 物理页 |
| 内核空间 | `vmalloc(size)` | vmalloc → buddy（逐页）→ 修改内核页表 |
| 内核空间 | `alloc_pages(gfp, order)` | 直接调用 buddy |

---

## 3.2 虚拟地址空间布局

### 3.2.1 虚拟地址空间划分

ARM64 使用高位符号扩展区分用户态和内核态地址。虚拟地址宽度由 `TCR_EL1` 的 `T0SZ`（用户态）和 `T1SZ`（内核态）字段控制，有效虚拟地址位数 = 64 - TxSZ。

```
┌─────────────────────────────────────────────────────────────────────┐
│                 ARM64 虚拟地址空间划分                                 │
│                                                                     │
│  0xFFFF_FFFF_FFFF_FFFF ┌──────────────────────┐                    │
│                        │                      │                    │
│                        │   内核虚拟地址空间      │ TTBR1_EL1 翻译     │
│                        │   (高位全 1 符号扩展)   │                    │
│                        │                      │                    │
│       -(1 << VA_BITS)  └──────────────────────┘                    │
│                                                                     │
│                        ┌──────────────────────┐                    │
│                        │   非法地址区域         │ 访问触发异常        │
│                        │   (非全0也非全1扩展)   │                    │
│                        └──────────────────────┘                    │
│                                                                     │
│        (1 << VA_BITS)-1┌──────────────────────┐                    │
│                        │                      │                    │
│                        │   用户虚拟地址空间      │ TTBR0_EL1 翻译     │
│                        │   (高位全 0 符号扩展)   │                    │
│                        │                      │                    │
│  0x0000_0000_0000_0000 └──────────────────────┘                    │
│                                                                     │
│  常见 VA_BITS 配置：                                                 │
│  ┌──────────┬──────────┬──────────────┬──────────────┐              │
│  │ VA_BITS  │ 页大小    │ 页表级数      │ 地址空间大小  │              │
│  ├──────────┼──────────┼──────────────┼──────────────┤              │
│  │ 39       │ 4KB      │ 3 级         │ 512GB        │              │
│  │ 48       │ 4KB      │ 4 级         │ 256TB        │              │
│  │ 52       │ 4KB      │ 4 级 + LPA2  │ 4PB          │              │
│  │ 36       │ 16KB     │ 3 级         │ 64GB         │              │
│  │ 47       │ 16KB     │ 4 级         │ 128TB        │              │
│  │ 42       │ 64KB     │ 3 级         │ 4TB          │              │
│  │ 48       │ 64KB     │ 3 级         │ 256TB        │              │
│  └──────────┴──────────┴──────────────┴──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2.2 用户虚拟地址空间布局

```
┌─────────────────────────────────────────────────────────────────────┐
│            用户虚拟地址空间布局（传统布局 vs 新布局）                   │
│                                                                     │
│  传统布局 (mmap 自底向上)          新布局 (mmap 自顶向下，默认)        │
│                                                                     │
│  TASK_SIZE-1 ┌──────────┐        TASK_SIZE-1 ┌──────────┐          │
│              │  栈 ↓    │                    │  栈 ↓    │          │
│              ├──────────┤                    ├──────────┤          │
│              │          │                    │ mmap ↓   │          │
│              │ mmap ↑   │                    │ (自顶向下) │          │
│              │(自底向上) │                    ├──────────┤          │
│              ├──────────┤                    │          │          │
│              │          │                    │ 堆 ↑     │          │
│              │ 堆 ↑     │                    ├──────────┤          │
│              ├──────────┤                    │ BSS      │          │
│              │ BSS      │                    ├──────────┤          │
│              ├──────────┤                    │ 数据段    │          │
│              │ 数据段    │                    ├──────────┤          │
│              ├──────────┤                    │ 代码段    │          │
│  0x0000      │ 代码段    │        0x0000      └──────────┘          │
│              └──────────┘                                           │
│                                                                     │
│  新布局优势：mmap 和堆相向增长，充分利用中间空间                       │
│  由 arch_pick_mmap_layout() 决定布局方式                              │
│  ASLR：load_elf_binary() 中通过 PF_RANDOMIZE 随机化各段地址          │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2.3 内核虚拟地址空间布局（ARM64 Linux 6.1）

基于 `arch/arm64/include/asm/memory.h` 和 `arch/arm64/include/asm/pgtable.h` 源码：

```
┌─────────────────────────────────────────────────────────────────────┐
│       ARM64 内核虚拟地址空间布局 (VA_BITS=48, PAGE_SIZE=4KB)          │
│       从低地址到高地址排列                                             │
│                                                                     │
│  -(1<<48) = 0xFFFF_0000_0000_0000                                   │
│  │                                                                  │
│  ├── MODULES_VADDR = _PAGE_END(VA_BITS_MIN)                        │
│  │   ┌──────────────────────────┐                                   │
│  │   │ 内核模块区域              │ 128MB                             │
│  │   │ (.ko 加载地址)           │                                   │
│  │   └──────────────────────────┘                                   │
│  ├── MODULES_END = KIMAGE_VADDR                                    │
│  │   ┌──────────────────────────┐                                   │
│  │   │ 内核镜像(.text/.data)    │ KIMAGE_VADDR 起始                 │
│  │   └──────────────────────────┘                                   │
│  ├── VMALLOC_START = MODULES_END                                   │
│  │   ┌──────────────────────────┐                                   │
│  │   │ vmalloc / ioremap 区域   │ 巨大的虚拟地址范围                  │
│  │   │ vmalloc() 分配的内存      │                                   │
│  │   │ ioremap() 映射的设备内存  │                                   │
│  │   └──────────────────────────┘                                   │
│  ├── VMALLOC_END (VMEMMAP_START - 256MB)                           │
│  │   ┌──────────────────────────┐                                   │
│  │   │ 守护间隔 (256MB gap)      │                                   │
│  │   └──────────────────────────┘                                   │
│  ├── FIXADDR_TOP (VMEMMAP_START - 32MB)                            │
│  │   ┌──────────────────────────┐                                   │
│  │   │ 固定映射区域 (fixmap)     │ 编译时确定虚拟地址                  │
│  │   │ early ioremap/设备树等    │                                   │
│  │   └──────────────────────────┘                                   │
│  ├── PCI_IO_END (VMEMMAP_START - 8MB)                              │
│  │   ┌──────────────────────────┐                                   │
│  │   │ PCI I/O 端口映射          │ 16MB                              │
│  │   └──────────────────────────┘                                   │
│  ├── VMEMMAP_START                                                  │
│  │   ┌──────────────────────────┐                                   │
│  │   │ vmemmap 区域              │ struct page 数组的虚拟映射         │
│  │   │ 稀疏内存模型使用          │ pfn_to_page() 通过此区域寻址      │
│  │   └──────────────────────────┘                                   │
│  ├── PAGE_OFFSET = -(1 << VA_BITS)                                 │
│  │   ┌──────────────────────────┐                                   │
│  │   │ 线性映射区域              │ 物理内存的直接映射                  │
│  │   │ (Direct Mapping)         │ virt = phys - PHYS_OFFSET         │
│  │   │                          │       + PAGE_OFFSET               │
│  │   │ kmalloc 从这里分配        │ __pa() / __va() 转换              │
│  │   └──────────────────────────┘                                   │
│  ├── PAGE_END                                                       │
│  │                                                                  │
│  0xFFFF_FFFF_FFFF_FFFF                                              │
│                                                                     │
│  注意：内核镜像和模块在低地址端，线性映射在高地址端                     │
│  这与书中 4.x 的描述可能不同，6.1 中布局有所调整                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 进程描述符与内存描述符的关系

```
┌──────────────┐      ┌──────────────┐
│ task_struct   │      │ mm_struct    │
│              │      │              │
│ *mm ──────────────► │ pgd          │ → 页全局目录（写入 TTBR0_EL1）
│ *active_mm   │      │ mmap         │ → VMA 链表头
│              │      │ mm_mt        │ → VMA maple tree（6.1 主要查找结构）
│              │      │ mmap_base    │ → mmap 起始地址
│              │      │ start_brk    │ → 堆起始
│              │      │ brk          │ → 堆当前末尾
│              │      │ start_stack  │ → 栈起始
│              │      │ mm_users     │ → 用户引用计数（线程数）
│              │      │ mm_count     │ → 内核引用计数
└──────────────┘      └──────────────┘

线程共享：同一线程组的所有线程 mm 指向同一个 mm_struct（mm_users > 1）
内核线程：mm = NULL，active_mm 借用上一个用户进程的 mm（避免切换 TTBR1）

6.1 变化：VMA 查找从红黑树 (mm_rb) 迁移到 maple tree (mm_mt)
         maple tree 是 B-tree 变体，缓存友好，减少指针追踪次数
```

---

## 3.3 物理地址空间

### ARM64 的内存类型

ARM64 架构定义了两种内存类型：

| 类型 | 说明 | 用途 |
|---|---|---|
| 正常内存 (Normal Memory) | 包括 RAM 和 ROM，可缓存、可推测访问 | 代码、数据 |
| 设备内存 (Device Memory) | 分配给外围设备寄存器的物理地址区域，不可缓存 | MMIO |

设备内存按三个属性组合细分（G=聚合, R=重排, E=提前执行）：

| 属性 | 含义 |
|---|---|
| Device-nGnRnE | 最严格：不聚合、不重排、不提前执行 |
| Device-nGnRE | 不聚合、不重排、允许提前执行 |
| Device-nGRE | 不聚合、允许重排 |
| Device-GRE | 最宽松：允许聚合和重排 |

内存属性通过 MAIR_EL1 寄存器配置（最多 8 个条目），页表项中的 AttrIndx[2:0] 字段索引 MAIR 中的属性条目。

### I/O 映射

ARM64 没有独立的 I/O 端口地址空间（不像 x86 的 in/out 指令），统一使用内存映射 I/O：

| 函数 | 功能 |
|---|---|
| `ioremap(phys, size)` | 将外设物理地址映射到内核虚拟地址（vmalloc 区域） |
| `iounmap(addr)` | 释放 ioremap 映射 |
| `io_remap_pfn_range()` | 将外设物理地址映射到用户虚拟地址空间 |
| `readl()`/`writel()` | 带内存屏障的 MMIO 读写 |

---

## 3.4 内存映射

### 3.4.1 应用编程接口

| 系统调用 | 功能 |
|---|---|
| `mmap()` | 创建内存映射（文件映射或匿名映射） |
| `mremap()` | 扩大或缩小已有的内存映射 |
| `munmap()` | 删除内存映射 |
| `brk()` | 设置堆的上界 |
| `mprotect()` | 设置虚拟内存区域的访问权限 |
| `madvise()` | 向内核提出内存使用建议（如 MADV_DONTNEED） |
| `mlock()`/`munlock()` | 锁定/解锁物理内存，防止换出 |

### 内存映射分类

```
┌─────────────────────────────────────────────────────────────────────┐
│                     内存映射分类（2×2 矩阵）                          │
│                                                                     │
│              │  共享 (MAP_SHARED)        │  私有 (MAP_PRIVATE)       │
│  ────────────┼──────────────────────────┼─────────────────────────  │
│  文件映射     │ 多进程共享同一物理页       │ 写时复制 (COW)            │
│  (有后端文件) │ 写入同步到文件             │ 修改只对本进程可见        │
│              │ 典型：共享库 .so          │ 典型：私有文件映射读取     │
│  ────────────┼──────────────────────────┼─────────────────────────  │
│  匿名映射     │ 进程间共享内存            │ 进程私有匿名内存          │
│  (无后端文件) │ 典型：shmem/tmpfs        │ 典型：malloc大块分配、栈   │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.4.2 数据结构 — VMA

```c
struct vm_area_struct {
    unsigned long vm_start;     // 区域起始虚拟地址（包含）
    unsigned long vm_end;       // 区域结束虚拟地址（不包含）
    struct vm_area_struct *vm_next, *vm_prev; // VMA 双向链表
    pgprot_t vm_page_prot;      // 页保护标志（转换为 PTE 属性）
    unsigned long vm_flags;     // 标志位
    struct file *vm_file;       // 关联的文件（匿名映射为 NULL）
    const struct vm_operations_struct *vm_ops;
    unsigned long vm_pgoff;     // 文件映射的页偏移
    struct mm_struct *vm_mm;    // 所属的内存描述符
    // ...
};
```

**6.1 中的 VMA 管理变化**：Linux 6.1 将 VMA 的查找/插入/删除操作从红黑树 (`mm->mm_rb`) 迁移到 **maple tree** (`mm->mm_mt`)。maple tree 是一种 B-tree 变体，每个节点可存储多个条目，减少了树的高度和指针追踪次数，对 CPU 缓存更友好。

### vm_flags 常用标志

| 标志 | 含义 |
|---|---|
| VM_READ / VM_WRITE / VM_EXEC | 可读/可写/可执行 |
| VM_SHARED | 共享映射 |
| VM_GROWSDOWN | 向下增长（用户栈） |
| VM_PFNMAP | PFN 直接映射（非 struct page 管理的物理内存） |
| VM_LOCKED | 锁定在物理内存中，不换出 |
| VM_DONTCOPY | fork 时不复制此区域 |
| VM_DONTEXPAND | 不允许 mremap 扩展 |
| VM_HUGETLB | 使用巨型页 |
| VM_ACCOUNT | 记入虚拟内存过量提交计数 |

### 3.4.3 创建内存映射流程

```
mmap() 系统调用
  → sys_mmap → vm_mmap_pgoff → do_mmap
    │
    ├─ 参数检查（页对齐、权限合法性、大小非零）
    ├─ get_unmapped_area() → 在用户地址空间找到合适的空洞
    │   通过 maple tree 遍历查找
    ├─ mmap_region()
    │   ├─ 检查是否可以与相邻 VMA 合并（vma_merge）
    │   ├─ 分配新的 vm_area_struct
    │   ├─ 如果是文件映射 → 调用 file->f_op->mmap()
    │   │   文件系统设置 vm_ops（包含 fault 处理函数）
    │   ├─ 将 VMA 插入 maple tree
    │   └─ 关键：此时不分配物理页！
    │       物理页在首次访问时通过缺页异常按需分配（Demand Paging）
    └─ 返回映射的虚拟地址
```

### 3.4.4 虚拟内存过量提交策略

| 策略 | `/proc/sys/vm/overcommit_memory` | 含义 |
|---|---|---|
| OVERCOMMIT_GUESS | 0 (默认) | 启发式判断，拒绝明显不合理的分配 |
| OVERCOMMIT_ALWAYS | 1 | 总是允许，不检查 |
| OVERCOMMIT_NEVER | 2 | 严格检查：已提交 + 新请求 ≤ swap + RAM × overcommit_ratio |

### 3.4.5 删除内存映射

```
munmap() → do_munmap()
  ├─ 在 maple tree 中找到要删除范围覆盖的 VMA
  ├─ 如果只删除 VMA 的一部分 → 拆分 VMA（split_vma）
  ├─ munlock_vma_pages_all() → 解除物理页锁定
  ├─ detach_vmas_to_be_unmapped() → 从 maple tree 中摘除
  ├─ unmap_region() → 遍历页表解除映射，释放物理页
  └─ remove_vma_list() → 释放 VMA 结构体
```

---

## 3.5 物理内存组织

### 3.5.1 三级结构

```
┌─────────────────────────────────────────────────────────────────────┐
│              物理内存组织：node → zone → page                        │
│                                                                     │
│  内存节点 (node)                                                     │
│  ┌──────────────────────────────────────────────────┐               │
│  │ pglist_data (pg_data_t)                           │               │
│  │                                                  │               │
│  │  ┌─── 内存区域 (zone) ────────────────────────┐    │               │
│  │  │                                           │    │               │
│  │  │  ZONE_DMA      → DMA 设备可访问 (0~1GB)   │    │               │
│  │  │  ZONE_DMA32    → 32位DMA设备可访问 (0~4GB) │    │               │
│  │  │  ZONE_NORMAL   → 正常可用内存 (全部RAM)     │    │               │
│  │  │  ZONE_MOVABLE  → 可迁移页（热插拔/CMA）     │    │               │
│  │  │  ZONE_DEVICE   → 持久内存/设备内存          │    │               │
│  │  │                                           │    │               │
│  │  │  ARM64 没有 ZONE_HIGHMEM（64位地址空间足够   │    │               │
│  │  │  线性映射所有物理内存，无需高端内存区域）      │    │               │
│  │  │                                           │    │               │
│  │  │  每个 zone 包含：                           │    │               │
│  │  │    free_area[MAX_ORDER]  → 伙伴分配器链表   │    │               │
│  │  │    watermark[NR_WMARK]   → 水位线           │    │               │
│  │  │    struct page 数组       → 物理页帧描述符    │    │               │
│  │  └───────────────────────────────────────────┘    │               │
│  └──────────────────────────────────────────────────┘               │
│                                                                     │
│  NUMA 系统：每个 NUMA 节点一个 pglist_data                            │
│  UMA  系统：只有一个 pglist_data                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.5.2 内存区域（zone）

每个 zone 用 `struct zone` 描述，关键成员：

| 成员 | 说明 |
|---|---|
| `zone_start_pfn` | 该区域起始物理页帧号 |
| `managed_pages` | 伙伴分配器管理的物理页数量（扣除预留） |
| `spanned_pages` | 该区域跨越的总页数（含内存空洞） |
| `present_pages` | 实际存在的物理页数量 |
| `free_area[MAX_ORDER]` | 伙伴分配器的空闲链表数组 |
| `watermark[NR_WMARK]` | 水位线：MIN / LOW / HIGH |
| `watermark_boost` | 碎片化防御时的水位线提升量（6.1） |
| `lowmem_reserve[]` | 为高优先级区域预留的页数 |

关系：`spanned_pages ≥ present_pages ≥ managed_pages`

### 3.5.3 物理页（struct page）

`struct page` 是内核中最复杂的数据结构之一，大量使用 union 节省内存（每个实例约 64 字节）。flags 字段高位编码元信息：

```
flags 字段编码（高位到低位）：
  ┌──────────┬──────────┬──────────┬──────────────────┐
  │ SECTION  │  NODE    │  ZONE    │  PAGE FLAGS      │
  │ (稀疏内存)│ (NUMA节点)│ (区域类型)│ (PG_locked等)    │
  └──────────┴──────────┴──────────┴──────────────────┘

辅助函数：
  page_to_pfn(page)  → 页结构体 → 物理页帧号
  pfn_to_page(pfn)   → 物理页帧号 → 页结构体
                       （通过 vmemmap 区域寻址：VMEMMAP_START + pfn * sizeof(struct page)）
  page_zone(page)    → 从 flags 解码区域类型
  page_to_nid(page)  → 从 flags 解码 NUMA 节点
  page_to_phys(page) → page_to_pfn(page) << PAGE_SHIFT
```

---

## Node / Zone / Buddy 全景关系（补充专题）

### 数据结构嵌套关系

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  pglist_data (pg_data_t)  ──── 每个 NUMA 节点一个实例                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ node_id = 0                                                     │    │
│  │ node_start_pfn = 0x80000    (本节点物理页帧起始号)                │    │
│  │ node_present_pages = 262144 (本节点物理页数)                     │    │
│  │ kswapd → 后台回收线程                                           │    │
│  │ kcompactd → 后台压缩线程                                        │    │
│  │                                                                 │    │
│  │ node_zones[MAX_NR_ZONES]:  ──── 本节点的所有 zone 数组           │    │
│  │ ┌───────────────────────────────────────────────────────────┐    │    │
│  │ │ zone[ZONE_DMA]                                           │    │    │
│  │ │ ┌─────────────────────────────────────────────────────┐   │    │    │
│  │ │ │ zone_start_pfn = 0x80000                            │   │    │    │
│  │ │ │ managed_pages = 16384                               │   │    │    │
│  │ │ │ _watermark[WMARK_MIN] = 512                         │   │    │    │
│  │ │ │ _watermark[WMARK_LOW] = 640                         │   │    │    │
│  │ │ │ _watermark[WMARK_HIGH] = 768                        │   │    │    │
│  │ │ │ lowmem_reserve[] = {0, 0, 245760, 245760}           │   │    │    │
│  │ │ │ per_cpu_pageset → (per-CPU 页面缓存，见下文)          │   │    │    │
│  │ │ │ zone_pgdat → 指回 pglist_data                       │   │    │    │
│  │ │ │                                                     │   │    │    │
│  │ │ │ free_area[MAX_ORDER]:  ──── buddy 分配器核心          │   │    │    │
│  │ │ │ ┌─────────────────────────────────────────────┐     │   │    │    │
│  │ │ │ │ [0] nr_free=120                             │     │   │    │    │
│  │ │ │ │     free_list[UNMOVABLE] → page → page →... │     │   │    │    │
│  │ │ │ │     free_list[MOVABLE]   → page → page →... │     │   │    │    │
│  │ │ │ │     free_list[RECLAIMABLE] → ...             │     │   │    │    │
│  │ │ │ │ [1] nr_free=58                              │     │   │    │    │
│  │ │ │ │     free_list[UNMOVABLE] → ...               │     │   │    │    │
│  │ │ │ │     ...                                      │     │   │    │    │
│  │ │ │ │ [10] nr_free=2                               │     │   │    │    │
│  │ │ │ │     free_list[MOVABLE] → page(1024页块)      │     │   │    │    │
│  │ │ │ └─────────────────────────────────────────────┘     │   │    │    │
│  │ │ └─────────────────────────────────────────────────────┘   │    │    │
│  │ │                                                           │    │    │
│  │ │ zone[ZONE_NORMAL]                                         │    │    │
│  │ │ ┌─────────────────────────────────────────────────────┐   │    │    │
│  │ │ │ zone_start_pfn = 0x84000                            │   │    │    │
│  │ │ │ managed_pages = 245760                              │   │    │    │
│  │ │ │ free_area[MAX_ORDER]: ...（同上结构）                 │   │    │    │
│  │ │ └─────────────────────────────────────────────────────┘   │    │    │
│  │ │                                                           │    │    │
│  │ │ zone[ZONE_MOVABLE] (可能为空)                              │    │    │
│  │ └───────────────────────────────────────────────────────────┘    │    │
│  │                                                                 │    │
│  │ node_zonelists[MAX_ZONELISTS]:  ──── 分配时的备用 zone 搜索顺序  │    │
│  │ ┌───────────────────────────────────────────────────────────┐    │    │
│  │ │ zonelist[ZONELIST_FALLBACK]:                               │    │    │
│  │ │   _zonerefs[] = {                                         │    │    │
│  │ │     {zone=&node0.NORMAL, zone_idx=ZONE_NORMAL},           │    │    │
│  │ │     {zone=&node0.DMA,    zone_idx=ZONE_DMA},              │    │    │
│  │ │     {zone=&node1.NORMAL, zone_idx=ZONE_NORMAL},  ← 远端   │    │    │
│  │ │     {zone=&node1.DMA,    zone_idx=ZONE_DMA},              │    │    │
│  │ │     {zone=NULL}  ← 终止符                                  │    │    │
│  │ │   }                                                       │    │    │
│  │ └───────────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  UMA 系统（如 NVIDIA Thor）只有一个 pglist_data 实例                      │
│  NUMA 系统有多个，通过 node_data[] 数组索引                               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 关键关系：谁包含谁

```
一个系统
  └── N 个 node (pglist_data)                 ← NUMA 节点，UMA 只有 1 个
        ├── node_zones[]: 本节点的 zone 数组    ← zone 是 node 的子结构
        │     └── 每个 zone 内嵌:
        │           ├── free_area[MAX_ORDER]    ← buddy 分配器是 zone 内部的
        │           │     └── free_list[MIGRATE_TYPES]  ← 按迁移类型再分
        │           ├── per_cpu_pageset         ← 每 CPU 的页面热缓存
        │           ├── _watermark[]            ← 水位线控制回收行为
        │           └── lowmem_reserve[]        ← 保护低区域不被高区域耗尽
        │
        ├── node_zonelists[]: 备用 zone 搜索列表 ← 跨节点搜索顺序
        │     包含本节点和远端节点的所有 zone
        │
        └── kswapd: 后台页面回收线程              ← 每个 node 一个
```

### 分配时如何找到正确的 zone

```
┌─────────────────────────────────────────────────────────────────────────┐
│            alloc_pages(GFP_KERNEL, order=0) 的 zone 选择过程            │
│                                                                         │
│  1. 从 GFP 标志推导目标 zone 类型                                        │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │ GFP_KERNEL    → 无特殊 zone 要求 → highest_zoneidx=NORMAL│           │
│  │ GFP_DMA       → __GFP_DMA        → highest_zoneidx=DMA  │           │
│  │ GFP_DMA32     → __GFP_DMA32      → highest_zoneidx=DMA32│           │
│  │ GFP_HIGHUSER  → __GFP_HIGHMEM    → highest_zoneidx=NORMAL│ ARM64无HM│
│  │ GFP_USER      → (无特殊)         → highest_zoneidx=NORMAL│           │
│  └──────────────────────────────────────────────────────────┘           │
│  highest_zoneidx 是允许的最高 zone 类型，意味着：                         │
│    可以从 [0, highest_zoneidx] 范围内的任何 zone 分配                     │
│    但优先从高 zone 分配，保护低 zone 的稀缺内存                           │
│                                                                         │
│  2. 确定从哪个 node 开始搜索                                             │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │ preferred_nid = 当前进程所在的 NUMA 节点                   │           │
│  │ 或由 mempolicy / cpuset 指定                              │           │
│  │                                                          │           │
│  │ 取出该节点的 zonelist:                                    │           │
│  │   nid = preferred_nid                                     │           │
│  │   zonelist = &pgdat->node_zonelists[ZONELIST_FALLBACK]    │           │
│  └──────────────────────────────────────────────────────────┘           │
│                                                                         │
│  3. 遍历 zonelist 中的 zoneref[]                                        │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │ for_each_zone_zonelist(zone, z, zonelist, highest_zoneidx)│           │
│  │   │                                                      │           │
│  │   │  zonelist._zonerefs[] 按优先级排列:                    │           │
│  │   │  本节点 NORMAL → 本节点 DMA → 远端 NORMAL → 远端 DMA  │           │
│  │   │                                                      │           │
│  │   │  跳过 zone_idx > highest_zoneidx 的 zone              │           │
│  │   │  (如 GFP_DMA 只允许 ZONE_DMA，跳过 NORMAL)            │           │
│  │   │                                                      │           │
│  │   ├─ 对每个候选 zone 检查:                                 │           │
│  │   │  (a) cpuset 是否允许此 node?                          │           │
│  │   │  (b) 空闲页数 ≥ watermark + lowmem_reserve?           │           │
│  │   │      zone_watermark_fast(zone, order, mark,           │           │
│  │   │                          highest_zoneidx, alloc_flags)│           │
│  │   │  (c) 脏页比例是否超限?                                 │           │
│  │   │                                                      │           │
│  │   └─ 检查通过 → rmqueue(zone, order, migratetype)         │           │
│  │       从该 zone 的 buddy 分配器取页 → 成功返回              │           │
│  │                                                          │           │
│  │  所有 zone 都不满足 → 进入慢速路径                         │           │
│  └──────────────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────────┘
```

### Zone 内部的 Buddy 分配细节（rmqueue）

```
rmqueue(zone, order, migratetype)
│
├─ order == 0 时走 per-CPU 快速路径:
│   ┌──────────────────────────────────────────────────────┐
│   │ per_cpu_pages (pcp)                                  │
│   │ ┌──────────────────────────────────────────┐         │
│   │ │ count = 32   (当前缓存的页数)             │         │
│   │ │ high = 186   (超过则批量归还给 buddy)      │         │
│   │ │ batch = 31   (每次从 buddy 批量取的页数)   │         │
│   │ │                                          │         │
│   │ │ lists[migratetype]:                      │         │
│   │ │   page → page → page → ...               │         │
│   │ │                                          │         │
│   │ │ 命中 → 直接从 lists 取出一页返回           │         │
│   │ │ 未命中 → rmqueue_bulk() 从 buddy 批量      │         │
│   │ │         取 batch 个页补充 pcp              │         │
│   │ └──────────────────────────────────────────┘         │
│   │                                                      │
│   │ 目的：单页分配是最常见的操作，pcp 避免频繁            │
│   │       加锁 zone->lock 访问 buddy 的 free_area        │
│   └──────────────────────────────────────────────────────┘
│
└─ order > 0 时直接走 buddy:
    ┌──────────────────────────────────────────────────────┐
    │ __rmqueue(zone, order, migratetype)                   │
    │                                                      │
    │ 1. 在 free_area[order].free_list[migratetype] 查找    │
    │    有空闲块 → 取出 → expand() 拆分多余部分            │
    │                                                      │
    │ 2. 本迁移类型没有 → __rmqueue_fallback()              │
    │    按 fallback 优先级尝试其他迁移类型:                 │
    │    ┌──────────────────────────────────────┐           │
    │    │ MOVABLE 的 fallback 顺序:            │           │
    │    │   RECLAIMABLE → UNMOVABLE            │           │
    │    │                                      │           │
    │    │ UNMOVABLE 的 fallback 顺序:           │           │
    │    │   RECLAIMABLE → MOVABLE              │           │
    │    │                                      │           │
    │    │ RECLAIMABLE 的 fallback 顺序:         │           │
    │    │   UNMOVABLE → MOVABLE                │           │
    │    └──────────────────────────────────────┘           │
    │    从最大 order 开始找（偷大块，减少碎片）              │
    │    找到后可能整个 pageblock 改变迁移类型                │
    │                                                      │
    │ 3. 全部没有 → 返回 NULL → 上层进入慢速路径             │
    └──────────────────────────────────────────────────────┘
```

### lowmem_reserve 保护机制

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    为什么需要 lowmem_reserve？                           │
│                                                                         │
│  问题场景：                                                              │
│    GFP_KERNEL 分配（允许 ZONE_NORMAL + ZONE_DMA）                       │
│    如果 ZONE_NORMAL 满了，fallback 到 ZONE_DMA 分配                      │
│    大量 KERNEL 分配把 DMA zone 也耗尽                                    │
│    → 真正需要 DMA 内存的驱动无法分配 → 系统崩溃                           │
│                                                                         │
│  解决方案：lowmem_reserve[]                                              │
│    每个 zone 为更高优先级的 zone 类型预留一定页数                          │
│                                                                         │
│  例：ZONE_DMA.lowmem_reserve[] = {0, 0, 245760, 245760}                │
│       含义：当 ZONE_NORMAL 的分配 fallback 到 DMA 时                     │
│             DMA zone 的空闲页必须 > watermark + 245760 才允许分配         │
│             相当于给 DMA zone 预留了 245760 页的保护缓冲                  │
│                                                                         │
│  水位线检查公式：                                                        │
│    zone_watermark_ok(zone, order, mark, highest_zoneidx, ...) {          │
│      min = mark;                                                        │
│      min += lowmem_reserve[highest_zoneidx];  ← 关键：加上预留量         │
│      if (zone_page_state(zone, NR_FREE_PAGES) < min + (1<<order) - 1)  │
│          return false;   // 空闲页不够，不允许在此 zone 分配              │
│    }                                                                    │
│                                                                         │
│  调节：/proc/sys/vm/lowmem_reserve_ratio                                │
└─────────────────────────────────────────────────────────────────────────┘
```

### 完整分配路径总览

```
用户调用 kmalloc(64, GFP_KERNEL) 或 alloc_pages(GFP_KERNEL, 0)
│
├─ 1. GFP flags 解析
│     GFP_KERNEL → highest_zone = ZONE_NORMAL
│                → 允许回收、允许睡眠、允许 I/O
│     migratetype = GFP_MOVABLE ? MOVABLE : UNMOVABLE
│
├─ 2. 确定起始搜索位置
│     preferred_nid = numa_node_id()  (当前 CPU 所在 NUMA 节点)
│     zonelist = &node_data[nid]->node_zonelists[ZONELIST_FALLBACK]
│
├─ 3. 快速路径：get_page_from_freelist()
│     for each zone in zonelist (filtered by highest_zone):
│     │
│     │  本节点 ZONE_NORMAL:
│     │    zone_watermark_fast() → 检查空闲页 ≥ HIGH + lowmem_reserve
│     │    ├─ 通过 → rmqueue(zone, order, migratetype)
│     │    │          ├─ order=0 → pcp 快速路径
│     │    │          └─ order>0 → buddy free_area 查找
│     │    │          → 成功 → prep_new_page() → 返回 struct page*
│     │    │
│     │    └─ 未通过 → 继续检查下一个 zone
│     │
│     │  本节点 ZONE_DMA:
│     │    zone_watermark_fast() → 检查空闲页 ≥ HIGH + lowmem_reserve[NORMAL]
│     │    → 通常因 lowmem_reserve 保护而拒绝 KERNEL 类分配
│     │
│     │  远端节点 ZONE_NORMAL: (如有)
│     │    → 水位线检查 → 通过则分配
│     │
│     └─ 所有 zone 都不满足 → 进入慢速路径
│
├─ 4. 慢速路径：__alloc_pages_slowpath()
│     ├─ 唤醒 kswapd (异步回收)
│     ├─ 降低水位线到 MIN 重试快速路径
│     ├─ 直接回收 (direct reclaim)
│     ├─ 内存压缩 (compaction)
│     ├─ 回收 + 压缩组合重试
│     └─ OOM Killer → 杀进程释放内存 → 重试
│
└─ 5. 返回 struct page* 或 NULL
```

### 常见问题解答

**Q: 一个 zone 有自己独立的 buddy 分配器吗？**
是的。每个 zone 内部有完整的 `free_area[MAX_ORDER]` 数组，这就是该 zone 的 buddy 分配器。不同 zone 的 buddy 分配器独立管理各自的物理页，互不干扰。`zone->lock` 自旋锁保护该 zone 的 buddy 操作。

**Q: buddy 分配器是全局的还是 per-zone 的？**
per-zone 的。不存在一个"全局 buddy 分配器"。分配时通过 zonelist 选择目标 zone，然后在该 zone 内部的 buddy 分配器中操作。

**Q: GFP_KERNEL 为什么不从 ZONE_DMA 分配？**
可以 fallback 到 DMA，但受 `lowmem_reserve[]` 保护。ZONE_DMA 的 lowmem_reserve 为 NORMAL 预留了大量页，使得 GFP_KERNEL 分配在水位线检查时几乎总是被拒绝，除非 DMA zone 有非常多的空闲页。这保证了 DMA zone 的内存留给真正需要 DMA 的设备驱动。

**Q: per-CPU page cache (pcp) 和 buddy 是什么关系？**
pcp 是 buddy 上面的一层缓存，仅用于 order=0（单页）分配。每个 zone 为每个 CPU 维护一个 pcp 链表。分配时先查 pcp（无需 zone->lock），pcp 空了就从 buddy 批量取 batch 个页补充。释放时页面先放入 pcp，pcp 满了（超过 high）就批量归还给 buddy。这大幅减少了 zone->lock 的争用。

**Q: NUMA 系统中，分配时如何选择节点？**
由 zonelist 的排列顺序决定。默认优先本节点，然后按 NUMA 距离从近到远排列远端节点。进程的内存策略（mempolicy）和 cpuset 可以覆盖默认行为。

---

## 3.6 引导内存分配器（memblock 深入解析）

### 3.6.1 为什么需要 memblock

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         核心矛盾                                        │
│                                                                         │
│  buddy 分配器需要完整的 struct page 数组、zone 结构、水位线等才能工作     │
│  但这些数据结构本身也需要分配内存来存储                                   │
│                                                                         │
│  → 在 buddy 准备好之前，内核需要一个更简单的分配器来 bootstrap             │
│  → 这就是 memblock 的角色：引导阶段的临时内存管理器                       │
│                                                                         │
│  生命周期：                                                              │
│                                                                         │
│  ┌───── memblock 活跃期 ─────┐┌───── buddy 活跃期 ──────────────────┐   │
│  │                           ││                                     │   │
│  处理器  设备树    内核数据   页表   buddy    memblock    正常       │   │
│  上电    解析      结构分配   建立   初始化   交接完成    运行       │   │
│  │       │         │         │      │        │          │          │   │
│  ▼       ▼         ▼         ▼      ▼        ▼          ▼          │   │
│  ════════════════════════════════════════════════════════════════   │   │
│  memblock_add()   memblock_reserve()    mem_init()                 │   │
│  添加物理内存      预留内核/DTB等        memblock_free_all()         │   │
│                   memblock_alloc()       → 释放未预留页给 buddy     │   │
│                   启动期间分配                                      │   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.6.2 memblock 的数据模型

memblock 用**两个区域列表**描述物理内存的状态：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      memblock 的两个列表                                │
│                                                                         │
│  struct memblock                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                                                                 │    │
│  │  memory (memblock_type):  "系统有哪些物理内存"                    │    │
│  │  ┌─────────────────────────────────────────────────────────┐    │    │
│  │  │ regions[0]: base=0x8000_0000, size=0x8000_0000 (2GB)    │    │    │
│  │  │ regions[1]: base=0x2_0000_0000, size=0x1_0000_0000 (4GB)│    │    │
│  │  │ cnt=2                                                   │    │    │
│  │  └─────────────────────────────────────────────────────────┘    │    │
│  │  物理地址    0          2GB         4GB         8GB    12GB    │    │
│  │             ░░░░░░░░░░ ████████████ ░░░░░░░░░░ ████████████    │    │
│  │             无RAM      RAM区域1      无RAM       RAM区域2       │    │
│  │                                                                 │    │
│  │  reserved (memblock_type):  "哪些内存已被占用"                   │    │
│  │  ┌─────────────────────────────────────────────────────────┐    │    │
│  │  │ regions[0]: base=0x8020_0000, size=0x0100_0000 (内核镜像)│    │    │
│  │  │ regions[1]: base=0x8F00_0000, size=0x0010_0000 (DTB)    │    │    │
│  │  │ regions[2]: base=0x8E00_0000, size=0x0080_0000 (initrd) │    │    │
│  │  │ regions[3]: ... (页表、percpu区域、早期分配等)              │    │    │
│  │  │ cnt=N                                                   │    │    │
│  │  └─────────────────────────────────────────────────────────┘    │    │
│  │                                                                 │    │
│  │  空闲内存 = memory 中有、但 reserved 中没有的区域                  │    │
│  │  (memblock 不维护显式的"空闲列表"，空闲是两个列表的差集)           │    │
│  │                                                                 │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  与 buddy 的根本区别：                                                   │
│  buddy: 直接管理空闲页链表 (free_area)，分配/释放都操作链表              │
│  memblock: 管理"已有"和"已用"两个区间数组，空闲 = 已有 - 已用             │
│            分配 = 在 memory 中找到 reserved 不覆盖的区间，然后加入 reserved│
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.6.3 memblock 的操作方式

#### 核心操作

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      memblock 四个核心操作                               │
│                                                                         │
│  (1) memblock_add(base, size)          "告诉内核这段物理地址有 RAM"      │
│      ┌─ 将 [base, base+size) 加入 memory 列表                          │
│      ├─ 如果与已有 region 重叠/相邻 → 合并                              │
│      └─ 来源：设备树的 memory 节点、ACPI SRAT 表                        │
│                                                                         │
│  (2) memblock_reserve(base, size)      "标记这段内存已被占用"            │
│      ┌─ 将 [base, base+size) 加入 reserved 列表                        │
│      ├─ 如果与已有 region 重叠/相邻 → 合并                              │
│      └─ 用于：内核镜像、DTB、initrd、页表、percpu 等                    │
│                                                                         │
│  (3) memblock_alloc(size, align)       "分配一块内存"                    │
│      ┌─ 在 memory 列表中找一块大小≥size、对齐满足 align 的区间           │
│      ├─ 且该区间不与 reserved 列表中任何 region 重叠                     │
│      ├─ 找到后 → 将该区间加入 reserved 列表（标记已用）                  │
│      └─ 返回虚拟地址（通过 __va() 转换）                                │
│                                                                         │
│  (4) memblock_free(base, size)         "释放预留"                       │
│      ┌─ 从 reserved 列表中移除 [base, base+size)                        │
│      └─ 注意：不从 memory 列表移除，RAM 还在                            │
│                                                                         │
│  分配策略：                                                              │
│    bottom_up=false (默认): 从高地址向低地址搜索空闲区间                   │
│    bottom_up=true:         从低地址向高地址搜索                           │
│    current_limit: 分配不超过此物理地址                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 分配示例

```
初始状态：
  memory:   [0x8000_0000, 0x1_0000_0000)    ← 2GB RAM
  reserved: [0x8020_0000, 0x8120_0000)      ← 16MB 内核镜像

调用 memblock_alloc(0x1000, 0x1000):   ← 分配 4KB, 4KB 对齐

  搜索 memory 中未被 reserved 覆盖的区间:
    候选1: [0x8000_0000, 0x8020_0000)  ← 内核镜像之前
    候选2: [0x8120_0000, 0x1_0000_0000) ← 内核镜像之后 (最大)
  
  默认从高地址搜索 → 选择候选2 的末尾
  分配 [0xFFFF_F000, 0x1_0000_0000) 中的 4KB

  结果：
  reserved: [0x8020_0000, 0x8120_0000)      ← 内核镜像
            [0xFFFF_F000, 0x1_0000_0000)    ← 新分配   ← 新增
```

### 3.6.4 ARM64 启动时 memblock 的使用时间线

```
┌─────────────────────────────────────────────────────────────────────────┐
│              ARM64 启动过程中 memblock 的操作时间线                      │
│              (arch/arm64/mm/init.c + drivers/of/fdt.c)                  │
│                                                                         │
│  start_kernel()                                                         │
│    │                                                                    │
│    ├─ setup_arch()                                                      │
│    │   │                                                                │
│    │   ├─ early_init_dt_scan()                                          │
│    │   │   └─ early_init_dt_scan_memory()                               │
│    │   │       对设备树中每个 memory 节点:                                │
│    │   │       → memblock_add(base, size)     ★ 第一步：注册物理内存     │
│    │   │                                                                │
│    │   ├─ arm64_memblock_init()                                         │
│    │   │   ├─ memblock_remove(超出范围的内存)                             │
│    │   │   ├─ memblock_reserve(_stext, _end - _stext)                   │
│    │   │   │   ★ 预留内核代码和数据段                                    │
│    │   │   ├─ memblock_reserve(initrd)  ★ 预留 initrd                   │
│    │   │   ├─ early_init_fdt_scan_reserved_mem()                        │
│    │   │   │   ★ 预留设备树中 reserved-memory 节点                      │
│    │   │   └─ fdt_init_reserved_mem()                                   │
│    │   │                                                                │
│    │   ├─ paging_init()                                                 │
│    │   │   ├─ 建立内核页表                                               │
│    │   │   │   这里会 memblock_alloc() 分配页表页                       │
│    │   │   └─ zone/node 初始化 (但 buddy 还没开始工作)                   │
│    │   │                                                                │
│    │   └─ bootmem_init()                                                │
│    │       ├─ zone_sizes_init() → free_area_init()                      │
│    │       │   初始化 zone 的 free_area[] 结构（但还是空的）              │
│    │       │   初始化 struct page 数组（所有页标记为 Reserved）           │
│    │       └─ 此时 buddy 数据结构就绪，但还没有空闲页                    │
│    │                                                                    │
│    ├─ mm_init()                                                         │
│    │   └─ mem_init()                                                    │
│    │       └─ memblock_free_all()           ★★★ 关键交接点 ★★★          │
│    │           │                                                        │
│    │           │  从此刻起，memblock 的使命结束，buddy 接管               │
│    │           │                                                        │
│    │           ├─ reset_all_zones_managed_pages()                       │
│    │           │   所有 zone 的 managed_pages 清零（准备重新计数）        │
│    │           │                                                        │
│    │           └─ free_low_memory_core_early()                          │
│    │               遍历 memblock.memory 中所有 "空闲" 区间:              │
│    │               for_each_free_mem_range(...)                         │
│    │                 → 即 memory 中有、reserved 中没有的区间              │
│    │                 → __free_memory_core(start, end)                   │
│    │                   → __free_pages_memory(start_pfn, end_pfn)        │
│    │                     → memblock_free_pages(page, pfn, order)        │
│    │                       → __free_pages_core(page, order)             │
│    │                         ├─ ClearPageReserved(page)                 │
│    │                         ├─ set_page_count(page, 0)                 │
│    │                         ├─ zone->managed_pages += nr_pages         │
│    │                         └─ __free_pages_ok(page, order)            │
│    │                             → 将页放入 zone 的 free_area 链表       │
│    │                             → buddy 分配器开始有空闲页可用！         │
│    │                                                                    │
│    └─ 后续初始化正常使用 buddy 分配器 (kmalloc/alloc_pages 等)           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.6.5 memblock → buddy 交接的本质

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  交接本质：逐页"释放"到 buddy                            │
│                                                                         │
│  memblock_free_all() 的核心逻辑:                                        │
│                                                                         │
│  物理内存全景 (memory 列表):                                             │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │░░░░░│████│░░░░░░░│██│░░░│███│░░░░░░░░░░░░░░░░░░░░░░░░░░│   │        │
│  └─────────────────────────────────────────────────────────────┘        │
│        ▲    ▲       ▲  ▲   ▲   ▲                          ▲            │
│        │    │       │  │   │   │                          │            │
│       空闲  内核   空闲 DTB 空闲 initrd    大量空闲内存     │            │
│             镜像                                                        │
│                                                                         │
│  ████ = reserved 中有的区间 (保留，不交给 buddy)                         │
│  ░░░░ = memory 中有但 reserved 中没有的区间 (空闲，交给 buddy)           │
│                                                                         │
│  for_each_free_mem_range() 遍历所有 ░░░░ 区间:                          │
│    对每个空闲区间 [start_pfn, end_pfn):                                  │
│      按最大可能的 order 拆分（贪心策略）:                                 │
│        while (start < end):                                             │
│          order = min(MAX_ORDER-1, __ffs(start))  ← 对齐约束             │
│          while (start + 2^order > end): order--  ← 大小约束             │
│          __free_pages_core(page, order)                                  │
│            → 将 2^order 页作为一个整块放入 buddy 的 free_area[order]     │
│            → zone->managed_pages += 2^order                             │
│          start += 2^order                                               │
│                                                                         │
│  结果：buddy 的 free_area 链表被填满，正常分配开始工作                    │
│  reserved 的区间保持 PageReserved 标记，buddy 永远不会触碰它们           │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.6.6 交接后 memblock 的命运

```
交接完成后：
  ├─ 默认配置：memblock 的数据结构被释放（标记为 __initdata）
  │            启动完成后 __init 段被回收
  │
  ├─ CONFIG_ARCH_KEEP_MEMBLOCK：保留 memblock 数据
  │   用途：kexec（快速重启）需要知道原始内存布局
  │         /sys/kernel/debug/memblock/ 提供调试信息
  │
  └─ memblock_free_late()：启动后期释放的内存
      有些 __init 数据在 buddy 初始化后才能释放
      通过 memblock_free_late() 直接调用 __free_pages_core()
      将页面交给 buddy
```

### 3.6.7 memblock 编程接口汇总

| 函数 | 操作的列表 | 功能 |
|---|---|---|
| `memblock_add(base, size)` | memory | 注册物理内存区域 |
| `memblock_add_node(base, size, nid)` | memory | 注册物理内存并指定 NUMA 节点 |
| `memblock_remove(base, size)` | memory | 从 memory 中移除区域（如超出寻址范围） |
| `memblock_reserve(base, size)` | reserved | 标记区域为已预留 |
| `memblock_free(base, size)` | reserved | 从 reserved 中移除（取消预留） |
| `memblock_alloc(size, align)` | reserved | 在空闲区间中分配，并加入 reserved |
| `memblock_alloc_range_nid(...)` | reserved | 指定地址范围和 NUMA 节点的分配 |
| `memblock_phys_alloc(size, align)` | reserved | 返回物理地址（不转换虚拟地址） |
| `memblock_free_all()` | → buddy | 将所有未预留页释放到 buddy 分配器 |

### 3.6.8 memblock vs buddy 对比

| 维度 | memblock | buddy |
|---|---|---|
| 活跃阶段 | 启动早期（buddy 就绪前） | 启动后期 + 正常运行 |
| 数据结构 | 两个有序区间数组（memory + reserved） | per-zone 的 free_area[MAX_ORDER] 链表 |
| 空闲表示 | 隐式：memory - reserved 的差集 | 显式：空闲页挂在 free_list 链表上 |
| 分配算法 | 线性扫描区间数组 | O(1) 链表取头 + 伙伴拆分/合并 |
| 分配速度 | 慢（启动时不关心性能） | 快（运行时高频调用） |
| 并发安全 | 无锁（启动时单核） | zone->lock 自旋锁 + per-CPU pcp 缓存 |
| 碎片管理 | 无（短暂使用，不关心碎片） | 迁移类型 + compaction + 水位线 |
| 最小单位 | 任意字节（按 align 对齐） | 页（4KB） |
| 释放方式 | 从 reserved 中删除区间 | 伙伴合并归还 free_area |

---

## 3.7 伙伴分配器

### 3.7.1 基本原理

```
┌─────────────────────────────────────────────────────────────────────┐
│                   伙伴分配器原理                                      │
│                                                                     │
│  每个 zone 的 free_area[] 数组：                                      │
│                                                                     │
│  order  块大小(页数)   每个迁移类型一条空闲链表                         │
│  ──────────────────────────────────────────                         │
│  0      1 页 (4KB)    [UNMOVABLE] [MOVABLE] [RECLAIMABLE] ...      │
│  1      2 页 (8KB)    [UNMOVABLE] [MOVABLE] [RECLAIMABLE] ...      │
│  2      4 页 (16KB)   [UNMOVABLE] [MOVABLE] [RECLAIMABLE] ...      │
│  ...                                                                │
│  10     1024页 (4MB)  [UNMOVABLE] [MOVABLE] [RECLAIMABLE] ...      │
│                                                                     │
│  MAX_ORDER = 11, 最大可分配块 = 2^10 = 1024 页 = 4MB                │
│                                                                     │
│  分配 2^k 页：                                                       │
│  ┌─────────────────────────────────────────────────┐               │
│  │ 1. 查 order=k 对应迁移类型的空闲链表              │               │
│  │ 2. 有空闲块 → 取出返回                            │               │
│  │ 3. 没有 → 查 order=k+1，取出后拆成两个伙伴        │               │
│  │    一个返回给请求者，另一个放入 order=k 空闲链表     │               │
│  │ 4. 逐级向上找，直到 MAX_ORDER-1                   │               │
│  │ 5. 本迁移类型没有 → 从其他迁移类型的链表"偷取"      │               │
│  └─────────────────────────────────────────────────┘               │
│                                                                     │
│  释放 2^k 页：                                                       │
│  ┌─────────────────────────────────────────────────┐               │
│  │ 1. 计算伙伴地址：buddy_pfn = pfn ^ (1 << order)  │               │
│  │ 2. 检查伙伴是否空闲 + 同区域 + 同迁移类型           │               │
│  │ 3. 是 → 合并成 order+1 的块，递归检查更高阶伙伴    │               │
│  │ 4. 否 → 放入 order=k 空闲链表                     │               │
│  └─────────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.7.2 区域水位线

```
┌─────────────────────────────────────────────────────────────────────┐
│                  内存水位线机制                                       │
│                                                                     │
│  空闲页数                                                            │
│  ▲                                                                  │
│  │  ████████████████████  充足，正常分配                              │
│  │                                                                  │
│  │  ─── HIGH + boost ─── boost：碎片化防御时临时提升水位线(6.1)       │
│  │  ─── HIGH ─────────── kswapd 停止异步回收                         │
│  │                                                                  │
│  │  ─── LOW ──────────── kswapd 开始异步回收                         │
│  │                                                                  │
│  │  ─── MIN ──────────── 直接回收（阻塞当前分配者）                   │
│  │                        只有 GFP_ATOMIC 等紧急分配可使用 MIN 以下   │
│  │                                                                  │
│  │  ░░░ 紧急预留 ░░░░░░░ PF_MEMALLOC / GFP_ATOMIC 可用              │
│  └──────────────────────────────────────────────►                   │
│                                                                     │
│  水位线计算：由 min_free_kbytes 内核参数控制                           │
│  LOW ≈ MIN × 1.25      HIGH ≈ MIN × 1.5                            │
│  默认 min_free_kbytes 根据物理内存大小自动计算                         │
│                                                                     │
│  watermark_boost（6.1）：                                            │
│    当检测到外部碎片化严重时，临时提升 HIGH 水位线                       │
│    让 kswapd 提前开始回收，腾出可合并的连续空闲页                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.7.3 页面迁移类型

| 迁移类型 | 说明 | 典型用户 |
|---|---|---|
| MIGRATE_UNMOVABLE | 不可迁移 | 内核 slab 对象、页表 |
| MIGRATE_MOVABLE | 可迁移（通过页表重映射） | 用户匿名页、page cache |
| MIGRATE_RECLAIMABLE | 可回收 | 文件页缓存、inode 缓存 |
| MIGRATE_HIGHATOMIC | 高优先级原子分配预留 | GFP_ATOMIC 紧急分配 |
| MIGRATE_CMA | CMA 连续内存分配器专用 | DMA 大缓冲区（空闲时可给 MOVABLE 用） |
| MIGRATE_ISOLATE | 隔离（内存热插拔时临时使用） | offlining |

目的：**缓解外部碎片化**。将不同寿命的页面物理隔离，避免长寿命的 UNMOVABLE 页插在短寿命页之间，导致释放后无法合并。

### 3.7.4 分配页面核心流程

```
alloc_pages(gfp_mask, order)
  → __alloc_pages(gfp_mask, order, preferred_nid, nodemask)
    │
    ├─ 快速路径 (get_page_from_freelist)
    │   ├─ 遍历备用区域列表 (zonelist)
    │   ├─ 对每个 zone 检查：
    │   │   - 水位线是否满足（zone_watermark_fast）
    │   │   - 是否在 cpuset 允许的节点上
    │   │   - 脏页比例是否超限
    │   ├─ 从对应迁移类型的空闲链表取页（rmqueue）
    │   └─ 成功 → 初始化页（prep_new_page）→ 返回
    │
    └─ 慢速路径 (__alloc_pages_slowpath) — 快速路径失败才走
        ├─ 唤醒 kswapd 异步回收
        ├─ 降低水位线重试快速路径（忽略 watermark_boost）
        ├─ 直接回收 (direct reclaim) → 同步回收页面
        ├─ 内存压缩 (compaction) → 整理碎片，合并空闲块
        ├─ 回收+压缩重试
        ├─ __GFP_NOFAIL → 无限循环重试
        └─ 全部失败 → OOM Killer 选择进程杀死 → 释放内存后重试
```

### 3.7.5 备用区域列表

```
每个 NUMA 节点的 pglist_data 包含 zonelist[]：
  zonelist[ZONELIST_FALLBACK]: 包含所有节点的所有 zone（备用列表）
  zonelist[ZONELIST_NOFALLBACK]: 仅包含本节点的 zone

区域优先顺序（默认）：
  本节点 NORMAL → 本节点 DMA32 → 本节点 DMA
  → 远端节点 NORMAL → 远端节点 DMA32 → 远端节点 DMA

目的：优先从本地节点分配（NUMA 局部性），
     高优先级区域（NORMAL）不轻易消耗低优先级区域（DMA）的内存
     lowmem_reserve[] 数组保护低区域不被高区域的分配耗尽
```

---

## 3.8 块分配器（slab/slub/slob）

### 分配器在内存管理中的位置

```
kmalloc(64, GFP_KERNEL)
  │
  ├─ SLUB 分配器
  │   在 kmalloc-96 缓存的 per-CPU freelist 中取一个对象
  │   │
  │   └─ 如果 freelist 为空 → 需要新 slab
  │       │
  │       └─ 调用 alloc_pages(gfp, order) → 伙伴分配器
  │           分配物理页 → 切分成固定大小对象 → 初始化 freelist
  │
  └─ 返回 64 字节对象的虚拟地址（在线性映射区域内）
```

### 3.8.1 编程接口

| 函数 | 功能 |
|---|---|
| `kmalloc(size, gfp)` | 从通用缓存分配（有 kmalloc-8/16/32/.../8192 等预建缓存） |
| `kfree(objp)` | 释放 |
| `kmem_cache_create(name, size, align, flags, ctor)` | 创建专用缓存 |
| `kmem_cache_alloc(cache, gfp)` | 从专用缓存分配 |
| `kmem_cache_free(cache, objp)` | 归还到专用缓存 |
| `kmem_cache_destroy(cache)` | 销毁专用缓存 |

### 3.8.2 SLAB 分配器

```
┌─────────────────────────────────────────────────────────────────────┐
│                   SLAB 分配器                                        │
│                                                                     │
│  struct kmem_cache                                                  │
│  ┌──────────────────────────────────────────────────┐               │
│  │ cpu_cache (per-CPU)  → array_cache 热缓存         │               │
│  │                                                  │               │
│  │ node[N] (per-NUMA-node):                          │               │
│  │   slabs_partial → 部分使用的 slab 链表             │               │
│  │   slabs_full    → 完全使用的 slab 链表             │               │
│  │   slabs_free    → 完全空闲的 slab 链表             │               │
│  └──────────────────────────────────────────────────┘               │
│                                                                     │
│  每个 slab = 1 或多个连续物理页：                                     │
│  ┌──────┬──────┬──────┬──────┬──────┬──────┐                       │
│  │ 着色  │ obj0 │ obj1 │ obj2 │ obj3 │ 剩余 │                       │
│  └──────┴──────┴──────┴──────┴──────┴──────┘                       │
│  slab 着色：不同 slab 的对象在 L1 缓存行中错开                        │
│  减少不同 slab 的同偏移对象争用相同缓存行                              │
│                                                                     │
│  注：SLAB 在 Linux 6.5 中被移除，6.1 仍支持但非默认                   │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.8.3 SLUB 分配器（Linux 6.1 默认，深度解析）

#### 核心数据结构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   SLUB 核心数据结构关系全景图                             │
│                   (基于 Linux 6.1 include/linux/slub_def.h)              │
│                                                                         │
│                                                                         │
│  struct kmem_cache                     "一种对象缓存的全局描述"          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                                                                 │    │
│  │  object_size = 64          ← 用户请求的对象长度                   │    │
│  │  size = 80                 ← 实际对象长度 (含 freelist ptr/对齐)  │    │
│  │  offset = 64               ← freelist 指针在对象内的偏移         │    │
│  │  inuse = 64                ← 对象中用户可用部分的末尾偏移         │    │
│  │  align = 8                 ← 对齐要求                           │    │
│  │  red_left_pad = 0          ← 左红区填充 (DEBUG 时非零)           │    │
│  │  oo = {order=0, objects=51}← 最优 slab 配置 (1页=4KB,51个对象)   │    │
│  │  min = {order=0, objects=51}← 最小 slab 配置                    │    │
│  │  min_partial = 5           ← node partial 链表最小保留数          │    │
│  │  cpu_partial = 30          ← per-CPU partial 保留的对象数         │    │
│  │  name = "kmalloc-64"       ← 缓存名称                          │    │
│  │  ctor = NULL               ← 构造函数 (可选)                     │    │
│  │  list                      ← slab_caches 全局链表节点             │    │
│  │                                                                 │    │
│  │  cpu_slab ──────────────────────► (per-CPU 结构，见下方)          │    │
│  │                                                                 │    │
│  │  node[MAX_NUMNODES] ───────────► (per-Node 结构，见下方)          │    │
│  │                                                                 │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│       │                                    │                            │
│       │ __percpu *cpu_slab                  │ *node[nid]                 │
│       ▼                                    ▼                            │
│  ┌─── CPU 0 ──────────────┐    ┌─── NUMA Node 0 ──────────┐           │
│  │ kmem_cache_cpu          │    │ kmem_cache_node            │           │
│  │ (每个 CPU 一份副本)     │    │ (每个 NUMA 节点一份)       │           │
│  │                         │    │                            │           │
│  │ freelist ──► 空闲对象    │    │ list_lock (自旋锁)         │           │
│  │ tid = 全局唯一事务 ID   │    │ nr_partial = 3             │           │
│  │ slab ──► 当前活跃 slab  │    │ partial ──► slab 链表      │           │
│  │ partial ──► CPU partial │    │                            │           │
│  │ lock (local_lock)       │    │                            │           │
│  └─────────────────────────┘    └────────────────────────────┘          │
└─────────────────────────────────────────────────────────────────────────┘
```

#### 数据结构关系详细展开

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  struct kmem_cache ("kmalloc-64")                                       │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  cpu_slab (per-CPU)             node[] (per-NUMA)               │    │
│  │  ┌───────┐ ┌───────┐           ┌───────────────────┐           │    │
│  │  │ CPU 0 │ │ CPU 1 │  ...      │ node[0]           │           │    │
│  │  └───┬───┘ └───┬───┘           │ kmem_cache_node   │           │    │
│  │      │         │                │                   │           │    │
│  └──────┼─────────┼────────────────┼───────────────────┼───────────┘    │
│         │         │                │                   │                │
│         ▼         ▼                ▼                   │                │
│                                                        │                │
│  ╔═══════════════════════════════╗                      │                │
│  ║  kmem_cache_cpu (CPU 0)       ║                      │                │
│  ║                               ║                      │                │
│  ║  freelist ─────────────┐      ║                      │                │
│  ║  tid = 12345           │      ║                      │                │
│  ║  slab ──┐              │      ║                      │                │
│  ║  partial──┐            │      ║                      │                │
│  ╚══════════╪════════════╪══════╝                      │                │
│             │            │                              │                │
│    ┌────────▼────────┐   │                              │                │
│    │ struct slab      │   │   "当前活跃 slab"            │                │
│    │ (frozen=1)       │   │                              │                │
│    │ objects=51       │   │                              │                │
│    │ inuse=3          │   │   slab 对应的物理页 (1 page)  │                │
│    │ slab_cache ──────────────► kmem_cache              │                │
│    │ freelist ────┐   │   │   ┌─────────────────────┐   │                │
│    └──────────────┼───┘   │   │ obj0 │ obj1 │ obj2  │   │                │
│                   │       │   │(已用) │(已用)│(已用) │   │                │
│                   │       │   ├───────┼──────┼───────┤   │                │
│                   │       └──►│ obj3  │ obj4 │ obj5  │   │                │
│                   │           │(空闲) │(空闲)│(空闲) │   │                │
│                   │           ├───────┼──────┼───────┤   │                │
│                   └──────────►│FP→obj4│FP→obj5│FP→NULL│  │                │
│                               │ ...   │ ...  │       │   │                │
│                               └───────┴──────┴───────┘   │                │
│                                                          │                │
│    freelist 是嵌入在对象内部的单链表:                      │                │
│    cpu_slab->freelist → obj3 → obj4 → obj5 → NULL        │                │
│    FP (Free Pointer) 在对象偏移 offset 处                  │                │
│                                                          │                │
│    ┌────────▼────────┐                                    │                │
│    │ struct slab      │   "CPU partial 链表"               │                │
│    │ (frozen=1)       │   通过 slab->next 串联             │                │
│    │ objects=51       │                                    │                │
│    │ inuse=20         │   这些 slab 部分空闲，等待          │                │
│    │ next ──┐         │   当前 slab 用完后切换过来          │                │
│    └────────┼─────────┘                                    │                │
│    ┌────────▼────────┐                                    │                │
│    │ struct slab      │                                    │                │
│    │ (frozen=1)       │                                    │                │
│    │ inuse=35         │                                    │                │
│    │ next → NULL      │                                    │                │
│    └──────────────────┘                                    │                │
│                                                          │                │
│                           ╔════════════════════════════════╗              │
│                           ║ kmem_cache_node (Node 0)       ║              │
│                           ║                                ║              │
│                           ║ list_lock (spinlock)           ║              │
│                           ║ nr_partial = 3                 ║              │
│                           ║ partial ──┐                    ║              │
│                           ╚═══════════╪════════════════════╝              │
│                                       │                                   │
│                              ┌────────▼────────┐                          │
│                              │ struct slab      │  "Node partial 链表"    │
│                              │ (frozen=0)       │  通过 slab_list 串联    │
│                              │ inuse=10         │  这些 slab 不属于任何CPU│
│                              │ slab_list ──┐    │                          │
│                              └─────────────┼────┘                          │
│                              ┌─────────────▼────┐                          │
│                              │ struct slab      │                          │
│                              │ (frozen=0)       │                          │
│                              │ inuse=30         │                          │
│                              │ slab_list ──┐    │                          │
│                              └─────────────┼────┘                          │
│                              ┌─────────────▼────┐                          │
│                              │ struct slab      │                          │
│                              │ (frozen=0)       │                          │
│                              │ inuse=45         │                          │
│                              │ slab_list → NULL │                          │
│                              └──────────────────┘                          │
│                                                                            │
│  注意：完全使用的 slab (inuse == objects) 不在任何链表中！                   │
│  SLUB 不追踪 full slab，节省链表操作开销                                   │
│  (DEBUG 模式下 kmem_cache_node.full 会追踪，用于检测)                      │
└─────────────────────────────────────────────────────────────────────────┘
```

#### struct slab 内部布局（对象与 freelist 指针的关系）

```
┌─────────────────────────────────────────────────────────────────────────┐
│              一个 slab 的物理页内存布局 (非 DEBUG 模式)                   │
│              假设: object_size=64, size=80, offset=64, objects=51       │
│                                                                         │
│  物理页起始地址 (slab_address)                                           │
│  │                                                                      │
│  ▼                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │ 对象0 (80字节)                                                │       │
│  │ ┌──────────────────────────────┬────────────────────────┐    │       │
│  │ │    用户数据区 (64字节)        │  FP 指针区 (8字节)      │    │       │
│  │ │    [实际存储的数据]           │  → 指向下一个空闲对象    │    │       │
│  │ │                              │  (已分配时此处无意义)    │    │       │
│  │ └──────────────────────────────┴────────────────────────┘    │       │
│  │ │◄── object_size=64 ──────────►│◄── size-object_size ──►│   │       │
│  │ │◄── inuse=64 ────────────────►│                         │   │       │
│  │ │◄────────────── size=80 ─────────────────────────────►│    │       │
│  │                                                              │       │
│  ├──────────────────────────────────────────────────────────────┤       │
│  │ 对象1 (80字节) ...                                            │       │
│  ├──────────────────────────────────────────────────────────────┤       │
│  │ ...                                                          │       │
│  ├──────────────────────────────────────────────────────────────┤       │
│  │ 对象50 (最后一个)                                             │       │
│  ├──────────────────────────────────────────────────────────────┤       │
│  │ 剩余空间 (4096 - 51*80 = 16字节，浪费)                        │       │
│  └──────────────────────────────────────────────────────────────┘       │
│                                                                         │
│  空闲对象链表 (freelist):                                                │
│                                                                         │
│  cpu_slab->freelist                                                     │
│       │          offset=64         offset=64         offset=64          │
│       ▼                                                                 │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐              │
│  │  obj 3  │    │  obj 7  │    │  obj 12 │    │  obj 20 │              │
│  │         │    │         │    │         │    │         │              │
│  │ [FP]────────►│ [FP]────────►│ [FP]────────►│ [FP]→NULL│             │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘              │
│                                                                         │
│  分配一个对象: 取 freelist 头 (obj3)，freelist 前进到 obj7               │
│  释放一个对象: 将对象插入 freelist 头部                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

#### struct slab 与 struct page 的关系

```
┌─────────────────────────────────────────────────────────────────────────┐
│  每个物理页帧有一个 struct page（通过 vmemmap 寻址）                      │
│  SLUB 将 struct page 的 union 字段"重解释"为 struct slab：               │
│                                                                         │
│  struct page (通用)              struct slab (SLUB 解释)                 │
│  ┌──────────────────────┐       ┌──────────────────────┐               │
│  │ flags                │  ═══  │ __page_flags         │               │
│  │ (PG_slab 标志位)      │       │                      │               │
│  ├──────────────────────┤       ├──────────────────────┤               │
│  │ union {              │       │ union {              │               │
│  │   lru               │  ═══  │   slab_list          │ node partial  │
│  │                      │       │   {next, slabs}      │ cpu partial   │
│  │ }                    │       │ }                    │               │
│  ├──────────────────────┤       ├──────────────────────┤               │
│  │ mapping              │  ═══  │ slab_cache           │ → kmem_cache  │
│  ├──────────────────────┤       ├──────────────────────┤               │
│  │ index                │  ═══  │ freelist             │ → 空闲对象头  │
│  ├──────────────────────┤       ├──────────────────────┤               │
│  │ private              │  ═══  │ counters             │               │
│  │                      │       │  inuse:16  已分配数   │               │
│  │                      │       │  objects:15 总对象数  │               │
│  │                      │       │  frozen:1  是否冻结   │               │
│  ├──────────────────────┤       ├──────────────────────┤               │
│  │ _refcount            │  ═══  │ __page_refcount      │               │
│  └──────────────────────┘       └──────────────────────┘               │
│                                                                         │
│  转换: slab = (struct slab *)page;  同一内存地址，不同解释                │
│  判断: PageSlab(page) → flags 中 PG_slab 位为 1                        │
│  优势: 无需为 slab 元数据额外分配内存                                    │
│                                                                         │
│  frozen 标志:                                                            │
│    frozen=1: slab 被某 CPU "冻结"占用（在 cpu_slab 或 cpu partial 中）   │
│    frozen=0: slab 在 node partial 链表中，任何 CPU 可获取                │
└─────────────────────────────────────────────────────────────────────────┘
```

#### SLUB 三级缓存分配路径

```
kmalloc(64, GFP_KERNEL)
│
▼
╔═════════════════════════════════════════════════════════════╗
║ 第 1 级: per-CPU freelist (最快, 无锁 cmpxchg_double)       ║
║ cpu_slab->freelist 非空? → 取头对象返回 ✓                   ║
╚═══════════════════════════╪═════════════════════════════════╝
                            │ 空
╔═══════════════════════════▼═════════════════════════════════╗
║ 第 2 级: per-CPU partial (快, local_lock)                    ║
║ cpu_slab->partial 非空? → 切换 slab → 回第1级 ✓            ║
╚═══════════════════════════╪═════════════════════════════════╝
                            │ 空
╔═══════════════════════════▼═════════════════════════════════╗
║ 第 3 级: per-Node partial (慢, spinlock)                     ║
║ node->partial 非空? → 取 slab(s) → frozen=1 → 回第1级 ✓   ║
╚═══════════════════════════╪═════════════════════════════════╝
                            │ 空
╔═══════════════════════════▼═════════════════════════════════╗
║ 第 4 级: buddy 分配新页 (最慢)                               ║
║ alloc_pages() → 初始化 slab → 构建 freelist → 回第1级 ✓    ║
╚═════════════════════════════════════════════════════════════╝
```

#### kmem_cache 的全局组织

```
┌─────────────────────────────────────────────────────────────────────────┐
│  全局链表 slab_caches:                                                   │
│  slab_caches ──► kmalloc-8 ──► kmalloc-16 ──► ... ──► kmalloc-8192     │
│                  ──► task_struct ──► inode_cache ──► dentry_cache ──►...│
│                                                                         │
│  通用缓存 kmalloc_caches[type][index]:                                   │
│    kmalloc(size) → 向上取整到 2 的幂 → 选择对应 kmem_cache               │
│    size ≤ 8 → kmalloc-8,  ≤ 16 → kmalloc-16, ... ≤ 8192 → kmalloc-8K │
│    size > KMALLOC_MAX_CACHE_SIZE → 直接 alloc_pages()，不走 SLUB       │
│                                                                         │
│  专用缓存: kmem_cache_create("task_struct", sizeof(...), ...)           │
│    驱动/子系统为特定对象创建，如 task_struct/inode/dentry/vm_area_struct  │
│                                                                         │
│  查看: /proc/slabinfo 或 /sys/kernel/slab/                              │
└─────────────────────────────────────────────────────────────────────────┘
```

#### SLUB 相比 SLAB 的简化

1. 去掉 slabs_full/slabs_free 链表（full slab 不追踪，减少链表操作）
2. 去掉独立的 slab 管理结构（元数据复用 struct page/slab 字段，零额外开销）
3. freelist 嵌入对象内部（空闲对象的 offset 处存下一个空闲对象指针）
4. 快速路径完全无锁（per-CPU freelist + cmpxchg_double 原子操作）
5. 去掉 slab 着色（现代 CPU 的 L1 缓存关联性足够高，着色收益微小）

### 3.8.4 SLOB 分配器

- SLOB = Simple List Of Blocks，极简实现
- 适用于嵌入式小内存系统（CONFIG_SLOB）
- 3 条空闲链表按对象大小分类（<256B / <1024B / <PAGE_SIZE）
- 每个 slab 固定一页，空闲对象使用 first-fit 算法
- 无 NUMA 支持，无 per-CPU 缓存
- 注：SLOB 在 Linux 6.4 中被移除，6.1 仍支持

### 三种块分配器对比

| 特性 | SLAB | SLUB (默认) | SLOB |
|---|---|---|---|
| 复杂度 | 高（三链表+着色+array_cache） | 中 | 低 |
| 内存开销 | 大 | 小 | 最小 |
| 性能 | 好 | 最好（无锁快速路径） | 一般 |
| NUMA | 支持 | 支持（更优） | 不支持 |
| 调试 | 有 | 有（red zone/poisoning） | 基本无 |
| 适用 | 6.1 可选 | **6.1 默认** | 嵌入式 |
| 未来 | 6.5 移除 | 持续优化 | 6.4 移除 |

---

## 3.9 不连续页分配器（vmalloc）

### 3.9.1 编程接口

| 函数 | 功能 |
|---|---|
| `vmalloc(size)` | 分配虚拟连续但物理可不连续的内存 |
| `vzalloc(size)` | 同上，但零初始化 |
| `vfree(addr)` | 释放 vmalloc 分配的内存 |
| `vmap(pages, count, flags, prot)` | 将已有物理页数组映射到连续虚拟地址 |
| `vunmap(addr)` | 释放 vmap 映射 |
| `kvmalloc(size, gfp)` | 优先 kmalloc，如果 order 太高则 fallback 到 vmalloc |
| `kvfree(addr)` | 自动判断使用 kfree 或 vfree |

### 3.9.2 数据结构

```
struct vmap_area                struct vm_struct
┌────────────────┐              ┌────────────────┐
│ va_start       │              │ addr           │ → 分配的虚拟起始地址
│ va_end         │              │ size           │ → 大小（含 guard page）
│ rb_node        │ 红黑树管理    │ flags          │ → VM_ALLOC/VM_IOREMAP 等
│ list           │              │ struct page **pages│ → 物理页数组
│ *vm ───────────────────────► │ nr_pages       │ → 物理页数量
└────────────────┘              │ phys_addr      │ → ioremap 的物理地址
                                └────────────────┘

vmalloc 的每次分配会在虚拟地址末尾额外留一个 guard page（不映射），
用于检测越界访问。因此 vm_struct.size = 实际大小 + PAGE_SIZE。
```

### 3.9.3 vmalloc 分配流程

```
vmalloc(size)
  → __vmalloc_node_range(size, align, VMALLOC_START, VMALLOC_END, ...)
    │
    ├─ 1. 在 [VMALLOC_START, VMALLOC_END] 中分配虚拟地址区间
    │     alloc_vmap_area() → 红黑树查找空闲区间 → 插入
    │     包含 guard page 的额外空间
    │
    ├─ 2. 分配 struct page* 数组（数组本身用 kvmalloc）
    │
    ├─ 3. 逐页从伙伴分配器获取物理页
    │     for (i = 0; i < nr_pages; i++)
    │         pages[i] = alloc_page(gfp)  // 每页独立分配，可以不连续
    │
    └─ 4. 建立页表映射
          vmap_pages_range() → 填充内核页表
          ARM64: 修改 swapper_pg_dir 的 PGD → PUD → PMD → PTE
          每个 PTE 指向独立分配的物理页
          → 不同于线性映射的固定偏移，vmalloc 区域的映射是动态建立的
```

### vmalloc vs kmalloc 选择

| 维度 | kmalloc | vmalloc |
|---|---|---|
| 物理连续性 | 连续 | 不要求 |
| 虚拟连续性 | 连续 | 连续 |
| 最大大小 | 4MB (MAX_ORDER) | 仅受虚拟地址空间限制 |
| 性能 | 快（无需修改页表） | 慢（需建立页表映射+TLB miss） |
| DMA 可用 | 是 | 否（物理不连续） |
| 所在区域 | 线性映射区 | vmalloc 区 |

---

## 3.10 每处理器内存分配器（percpu）— 深入解析

### 3.10.1 为什么需要 percpu

```
问题: 多核系统中的全局共享变量

  假设有一个全局统计计数器:
    static atomic_t packet_count;    // 所有 CPU 共享

  CPU0: atomic_inc(&packet_count)   → 获取缓存行独占 → CPU1 缓存失效
  CPU1: atomic_inc(&packet_count)   → 获取缓存行独占 → CPU0 缓存失效
  CPU2: atomic_inc(&packet_count)   → 获取缓存行独占 → CPU0,1 缓存失效

  → 每次操作都导致 cache line bouncing (缓存行在核间反复迁移)
  → 原子操作本身也有总线锁定开销
  → 核数越多越慢 (反向扩展!)

解决: percpu — 每个 CPU 一份独立副本

  DEFINE_PER_CPU(unsigned long, packet_count);

  CPU0: this_cpu_inc(packet_count)  → 只操作 CPU0 自己的副本, 无竞争
  CPU1: this_cpu_inc(packet_count)  → 只操作 CPU1 自己的副本, 无竞争
  CPU2: this_cpu_inc(packet_count)  → 只操作 CPU2 自己的副本, 无竞争

  → 无锁, 无原子操作, 无 cache line bouncing
  → 需要全局总数时: sum = 0; for_each_cpu(cpu) sum += per_cpu(packet_count, cpu);
```

### 3.10.2 编程接口

#### 静态 percpu 变量

```c
// 定义 (编译时, 放入 .data..percpu 段)
DEFINE_PER_CPU(int, my_counter);                    // 普通
DEFINE_PER_CPU_SHARED_ALIGNED(int, my_aligned);     // 缓存行对齐 + SMP 共享
DEFINE_PER_CPU_READ_MOSTLY(int, my_readonly);       // 读多写少优化
DEFINE_PER_CPU_FIRST(int, my_first);                // 放在 percpu 段最前面

// 声明 (头文件中)
DECLARE_PER_CPU(int, my_counter);
```

#### 动态 percpu 变量

```c
// 分配 (可睡眠)
int __percpu *ptr = alloc_percpu(int);
// 等价于: __alloc_percpu(sizeof(int), __alignof__(int))

// 带 GFP 标志的分配
ptr = alloc_percpu_gfp(int, GFP_KERNEL);

// 释放
free_percpu(ptr);
```

#### 访问 percpu 变量的三种方式

```c
// ===== 方式 1: get_cpu_var / put_cpu_var (最安全) =====
// 禁用抢占 → 获取本 CPU 副本引用 → 使用完恢复抢占
get_cpu_var(my_counter)++;       // 禁用抢占 + 获取本 CPU 的 my_counter
put_cpu_var(my_counter);         // 恢复抢占

// 优点: 保证在同一个 CPU 上完成整个操作
// 缺点: 抢占禁用期间不能睡眠

// ===== 方式 2: this_cpu_xxx (推荐, 单指令) =====
this_cpu_read(my_counter)        // 读取本 CPU 的值
this_cpu_write(my_counter, 42)   // 写入本 CPU 的值
this_cpu_inc(my_counter)         // 递增本 CPU 的值
this_cpu_dec(my_counter)         // 递减
this_cpu_add(my_counter, 10)     // 加
this_cpu_sub(my_counter, 5)      // 减
this_cpu_and(my_counter, mask)   // 与
this_cpu_or(my_counter, bits)    // 或
val = this_cpu_xchg(my_counter, new)  // 交换
old = this_cpu_cmpxchg(my_counter, old, new)  // 比较交换

// ARM64 实现: 利用 TPIDR_EL1/EL2 寄存器存储 per-CPU 偏移
// 单条指令完成: addr = base + percpu_offset + 变量偏移
// 即使被抢占迁移到其他 CPU, 指令是原子的, 不会出现半完成状态

// ===== 方式 3: per_cpu_ptr / per_cpu (访问指定 CPU) =====
int *p = per_cpu_ptr(&my_counter, cpu);  // 获取指定 CPU 的变量地址
int val = per_cpu(my_counter, cpu);      // 读取指定 CPU 的变量值
// 适用于: 遍历所有 CPU 做汇总
```

#### 完整 API 表

| 接口 | 功能 | 是否禁用抢占 | 适用场景 |
|---|---|---|---|
| `DEFINE_PER_CPU(type, name)` | 静态定义 | — | 编译时已知的全局变量 |
| `alloc_percpu(type)` | 动态分配 | — | 模块加载、运行时创建 |
| `free_percpu(ptr)` | 释放 | — | |
| `get_cpu_var(name)` | 获取本 CPU 变量 + 禁止抢占 | 是 | 需要多步操作的场景 |
| `put_cpu_var(name)` | 恢复抢占 | 是(恢复) | 配合 get_cpu_var |
| `this_cpu_read/write/add/...` | 单指令操作本 CPU | 否 | 单步读写(推荐) |
| `__this_cpu_read/write/add/...` | 同上但不检查抢占 | 否 | 已知安全的上下文 |
| `per_cpu(var, cpu)` | 读指定 CPU | 否 | 遍历汇总 |
| `per_cpu_ptr(ptr, cpu)` | 获取指定 CPU 地址 | 否 | 遍历或传递指针 |

### 3.10.3 静态 percpu 变量的实现

```
编译期:
  DEFINE_PER_CPU(int, my_counter) 展开为:
    __attribute__((section(".data..percpu"))) int my_counter;
  
  所有静态 percpu 变量都放在 .data..percpu 段中
  链接器脚本: __per_cpu_start 和 __per_cpu_end 标记段的起止

  内核镜像中只有一份模板:
  ┌──── vmlinux ─────────────────────────────────┐
  │ .text  .data  .bss  .data..percpu  .init ... │
  │                      ▲                       │
  │                      └── 所有 percpu 变量的模板│
  └──────────────────────────────────────────────┘

启动时 setup_per_cpu_areas():
  (1) 计算每个 CPU 的 unit 大小:
      unit_size = ALIGN(静态 percpu 段大小 + 动态预留, PAGE_SIZE)
  
  (2) 为每个 CPU 分配 unit_size 的内存:
      ┌─────────────────────────────────────────────────────────────┐
      │  first chunk (通过 memblock 从线性映射区分配)                 │
      │                                                             │
      │  CPU 0 unit          CPU 1 unit         CPU 2 unit   ...    │
      │  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐    │
      │  │ 静态区域      │   │ 静态区域      │   │ 静态区域      │    │
      │  │ (从模板复制)  │   │ (从模板复制)  │   │ (从模板复制)  │    │
      │  ├──────────────┤   ├──────────────┤   ├──────────────┤    │
      │  │ 模块预留区域  │   │ 模块预留区域  │   │ 模块预留区域  │    │
      │  │ PERCPU_MODULE│   │ PERCPU_MODULE│   │ PERCPU_MODULE│    │
      │  │ _RESERVE(8KB)│   │ _RESERVE     │   │ _RESERVE     │    │
      │  ├──────────────┤   ├──────────────┤   ├──────────────┤    │
      │  │ 动态预留区域  │   │ 动态预留区域  │   │ 动态预留区域  │    │
      │  │ PERCPU_DYN   │   │ PERCPU_DYN   │   │ PERCPU_DYN   │    │
      │  │ _RESERVE(28KB)│  │ _RESERVE     │   │ _RESERVE     │    │
      │  └──────────────┘   └──────────────┘   └──────────────┘    │
      └─────────────────────────────────────────────────────────────┘

  (3) 计算 __per_cpu_offset[cpu]:
      __per_cpu_offset[cpu] = CPU_unit基地址 - __per_cpu_start
      
      实现 (mm/percpu.c):
        delta = pcpu_base_addr - __per_cpu_start
        __per_cpu_offset[cpu] = delta + pcpu_unit_offsets[cpu]

  (4) 访问时地址计算:
      per_cpu_ptr(ptr, cpu) = (void *)ptr + __per_cpu_offset[cpu]
      
      ptr 是编译时的 .data..percpu 段内地址
      加上 offset 后得到该 CPU 副本的实际内存地址
```

### 3.10.4 动态 percpu 变量的实现

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    percpu chunk 分配器架构                                │
│                                                                         │
│  全局管理:                                                               │
│    pcpu_chunk_lists[pcpu_nr_slots]                                      │
│    按 chunk 空闲字节数分 slot:                                            │
│      slot 0: 完全空闲的 chunk                                            │
│      slot 1: 空闲 1~31 字节                                              │
│      slot 2: 空闲 32~63 字节                                             │
│      ...                                                                │
│      slot N: 空闲最多的 chunk                                            │
│    分配时从空闲最多的 slot 开始找 (best-fit)                               │
│                                                                         │
│  struct pcpu_chunk                                                      │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │ list           → 挂在 pcpu_chunk_lists[slot] 中           │           │
│  │ free_bytes     → 本 chunk 总空闲字节数                     │           │
│  │ base_addr      → chunk 虚拟基地址                          │           │
│  │ nr_pages       → 本 chunk 管理的总页数                     │           │
│  │ nr_populated   → 已分配物理页的页数 (按需分配)              │           │
│  │                                                          │           │
│  │ alloc_map[]    → 分配位图 (每 bit = PCPU_MIN_ALLOC_SIZE)  │           │
│  │ bound_map[]    → 边界位图 (标记分配块的起止)                │           │
│  │ md_blocks[]    → 元数据块 (加速大范围搜索)                  │           │
│  │                                                          │           │
│  │ populated[]    → 哪些页已分配物理内存 (按需延迟分配)        │           │
│  └──────────────────────────────────────────────────────────┘           │
│                                                                         │
│  一个 chunk 的内存布局:                                                   │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │ CPU 0 unit          │ CPU 1 unit         │ CPU 2 unit │ ... │       │
│  │ ┌──────────────────┐│ ┌──────────────────┐│            │     │       │
│  │ │ alloc A (40B)    ││ │ alloc A (40B)    ││            │     │       │
│  │ │ alloc B (16B)    ││ │ alloc B (16B)    ││  (镜像)    │     │       │
│  │ │ (空闲 8B)        ││ │ (空闲 8B)        ││            │     │       │
│  │ │ alloc C (128B)   ││ │ alloc C (128B)   ││            │     │       │
│  │ │ (空闲 ...)       ││ │ (空闲 ...)       ││            │     │       │
│  │ └──────────────────┘│ └──────────────────┘│            │     │       │
│  └──────────────────────────────────────────────────────────────┘       │
│                                                                         │
│  关键设计: 所有 CPU unit 内的分配布局完全相同!                              │
│  → alloc_percpu 只需在"模板"中分配一次                                   │
│  → 所有 CPU 的偏移一致 → per_cpu_ptr(ptr, cpu) = ptr + offset[cpu]      │
└─────────────────────────────────────────────────────────────────────────┘
```

#### alloc_percpu 分配流程

```
alloc_percpu(type)
  → __alloc_percpu(sizeof(type), __alignof__(type))
    → pcpu_alloc(size, align, false, GFP_KERNEL)
      │
      ├─ (1) 参数校验:
      │     size 对齐到 PCPU_MIN_ALLOC_SIZE (通常 4 字节)
      │     size 不能超过 PCPU_MIN_UNIT_SIZE (通常 32KB)
      │
      ├─ (2) 遍历 pcpu_chunk_lists 找合适的 chunk:
      │     从空闲空间最多的 slot 开始
      │     for each chunk in slot:
      │       在 chunk 的 alloc_map 位图中搜索连续空闲区域
      │       找到 → 跳到步骤 4
      │
      ├─ (3) 没有合适的 chunk → 创建新 chunk:
      │     pcpu_create_chunk()
      │       → vmalloc 分配虚拟地址空间 (所有 CPU unit 的总大小)
      │       → 初始化 alloc_map / bound_map / md_blocks
      │       → 物理页暂不分配! (延迟到步骤 5)
      │
      ├─ (4) 在 chunk 中标记分配:
      │     更新 alloc_map (标记 bit 为已用)
      │     更新 bound_map (标记分配块边界)
      │     chunk->free_bytes -= size
      │     调整 chunk 在 pcpu_chunk_lists 中的 slot 位置
      │
      ├─ (5) 按需分配物理页 (populate):
      │     检查分配区域对应的物理页是否已分配
      │     if (!test_bit(page_idx, chunk->populated)):
      │       为每个 CPU unit 的对应页分配物理页
      │       pcpu_populate_chunk():
      │         first chunk: 页已经在 (线性映射, memblock 分配)
      │         后续 chunk:  alloc_pages() 分配 + 映射到 vmalloc 区域
      │       set_bit(page_idx, chunk->populated)
      │       chunk->nr_populated++
      │
      └─ (6) 返回 percpu 指针:
            ptr = chunk->base_addr + off  (off 是 chunk 内偏移)
            → 这是 CPU0 的地址
            → 访问 CPU N: ptr + __per_cpu_offset[N]
```

#### free_percpu 释放流程

```
free_percpu(ptr)
  → pcpu_free_area(chunk, off)
    ├─ 清除 alloc_map 对应位
    ├─ chunk->free_bytes += size
    ├─ 调整 chunk 在 slot 列表中的位置
    │
    ├─ 如果 chunk 变为完全空闲:
    │   且空闲 chunk 数量超过阈值
    │   → 调度 pcpu_balance_workfn() 工作队列
    │     → pcpu_depopulate_chunk(): 释放物理页 (但保留虚拟地址)
    │     → 或 pcpu_destroy_chunk(): 完全销毁 chunk
    │
    └─ 注意: 不立即释放物理页 (保留用于后续分配, 避免频繁 populate/depopulate)
```

### 3.10.5 first chunk vs 后续 chunk

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    两种 chunk 类型的区别                                  │
│                                                                         │
│  ┌─── first chunk ───────────────────────────────────────────────┐      │
│  │                                                               │      │
│  │  创建时机: 启动早期 setup_per_cpu_areas()                       │      │
│  │  分配方式: pcpu_embed_first_chunk()                            │      │
│  │            → memblock_alloc() 分配连续物理内存                  │      │
│  │  映射区域: 线性映射区 (PAGE_OFFSET 开始)                        │      │
│  │  物理页:   全部预分配, immutable=true, 不可 depopulate          │      │
│  │  包含:     静态 percpu 变量 + 模块预留 + 动态预留               │      │
│  │  特点:     物理连续 → 缓存友好                                  │      │
│  │            不可回收 → 占用固定内存                               │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  ┌─── 后续 chunk (动态创建) ──────────────────────────────────────┐      │
│  │                                                               │      │
│  │  创建时机: 运行时 first chunk 空间不足时                         │      │
│  │  分配方式: pcpu_create_chunk()                                 │      │
│  │            → vmalloc 分配虚拟地址空间                           │      │
│  │            → alloc_pages() 按需分配物理页                       │      │
│  │  映射区域: vmalloc 区域                                        │      │
│  │  物理页:   按需 populate/depopulate (延迟分配)                  │      │
│  │  包含:     仅动态 alloc_percpu 分配的变量                       │      │
│  │  特点:     物理不一定连续 (每个 CPU unit 的每页独立分配)          │      │
│  │            可回收 → chunk 空闲时释放物理页                       │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  first chunk 还可以有 reserved chunk (pcpu_reserved_chunk):             │
│    专门给 模块的静态 percpu 变量 预留的空间                               │
│    模块的 DEFINE_PER_CPU 分配到这里, 不占用通用 chunk 空间                │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.10.6 地址计算的完整图解

```
已知: DEFINE_PER_CPU(int, counter) 编译后位于 .data..percpu 段偏移 0x100 处

编译时:
  &counter = __per_cpu_start + 0x100    (段内偏移, 链接时确定)

启动后 setup_per_cpu_areas:
  假设 4 个 CPU, unit_size = 64KB

  pcpu_base_addr = first chunk 的虚拟基地址 (如 0xFFFF800040000000)
  delta = pcpu_base_addr - __per_cpu_start

  __per_cpu_offset[0] = delta + 0 * 64KB
  __per_cpu_offset[1] = delta + 1 * 64KB
  __per_cpu_offset[2] = delta + 2 * 64KB
  __per_cpu_offset[3] = delta + 3 * 64KB

运行时访问:
  per_cpu_ptr(&counter, 2)
    = &counter + __per_cpu_offset[2]
    = (__per_cpu_start + 0x100) + (delta + 2 * 64KB)
    = pcpu_base_addr + 2 * 64KB + 0x100
    = 0xFFFF800040020100    ← CPU 2 的 counter 地址

  this_cpu_read(counter)  在 CPU 1 上执行:
    ARM64 实现:
      TPIDR_EL1 存储当前 CPU 的 percpu 偏移
      ldr x0, [x_base, TPIDR_EL1]  → 一条指令完成
    等价于:
      *(&counter + __per_cpu_offset[1])
      = *(pcpu_base_addr + 1 * 64KB + 0x100)

内存布局:
  0xFFFF800040000000 ┌──────────────────┐
                     │ CPU 0 unit (64KB) │
                     │ 偏移 0x100: counter│ ← CPU 0 的 counter
  0xFFFF800040010000 ├──────────────────┤
                     │ CPU 1 unit (64KB) │
                     │ 偏移 0x100: counter│ ← CPU 1 的 counter
  0xFFFF800040020000 ├──────────────────┤
                     │ CPU 2 unit (64KB) │
                     │ 偏移 0x100: counter│ ← CPU 2 的 counter
  0xFFFF800040030000 ├──────────────────┤
                     │ CPU 3 unit (64KB) │
                     │ 偏移 0x100: counter│ ← CPU 3 的 counter
  0xFFFF800040040000 └──────────────────┘
```

### 3.10.7 this_cpu_xxx 的 ARM64 实现

```c
// this_cpu_read 的核心实现 (简化):
// ARM64 使用 TPIDR_EL1 或 TPIDR_EL2 存储当前 CPU 的 per_cpu_offset

// include/asm-generic/percpu.h:
#define this_cpu_read_1(pcp)    __this_cpu_read(pcp)
#define __this_cpu_read(pcp)    (*raw_cpu_ptr(&(pcp)))

// raw_cpu_ptr 的展开:
// SHIFT_PERCPU_PTR(&(pcp), __my_cpu_offset)
// = (typeof(pcp) *)((unsigned long)&(pcp) + __my_cpu_offset)

// ARM64 的 __my_cpu_offset:
// arch/arm64/include/asm/percpu.h:
static inline unsigned long __my_cpu_offset(void)
{
    unsigned long off;
    asm ("mrs %0, tpidr_el1" : "=r" (off));  // 从系统寄存器读取
    return off;
}

// 最终一条 this_cpu_read 编译为大约 2-3 条 ARM64 指令:
//   mrs  x1, tpidr_el1          // 读取当前 CPU 的 percpu 偏移
//   adrp x0, counter            // 加载 counter 的基地址
//   add  x0, x0, :lo12:counter  // 加上低 12 位偏移
//   ldr  w0, [x0, x1]           // 从 base + cpu_offset 处加载值
```

### 3.10.8 percpu 的优势、注意事项与典型用途

#### 优势

| 优势 | 原理 |
|---|---|
| 无锁访问 | 每个 CPU 独立副本，天然无竞争，不需要 spinlock/atomic |
| 无 cache line bouncing | 不同 CPU 的副本在不同物理地址，不会互相失效缓存行 |
| 单指令操作 | `this_cpu_*` 利用 TPIDR_EL1 硬件寄存器，编译为 2~3 条指令 |
| 汇总简单 | `for_each_possible_cpu(cpu) sum += per_cpu(var, cpu)` |

#### 注意事项

| 问题 | 说明 | 解决方案 |
|---|---|---|
| 抢占迁移 | 进程被抢占后可能迁移到其他 CPU，访问到错误副本 | `get_cpu_var()` 禁用抢占，或用 `this_cpu_*` 单指令操作 |
| 中断并发 | 中断处理函数也可能访问同一 percpu 变量 | 使用 `this_cpu_*` 原子操作，或 `local_irq_save()` |
| 内存放大 | 每个变量占 NR_CPUS 份内存 | 不要把大结构体做 percpu，12 核系统 1KB 变量 = 12KB |
| 汇总开销 | 读全局值需遍历所有 CPU | 高频读全局值的场景不适合 percpu |
| 调试困难 | crash dump 中需要知道是哪个 CPU 的副本 | `cat /proc/stat` 等通过 per_cpu 汇总展示 |

#### 内核中的典型用途

```
调度器:
  DEFINE_PER_CPU_SHARED_ALIGNED(struct rq, runqueues);
  → 每个 CPU 独立的运行队列, 调度器操作无需全局锁

SLUB 分配器:
  struct kmem_cache_cpu __percpu *cpu_slab;
  → 每个 CPU 的空闲对象链表, 分配路径完全无锁

网络子系统:
  DEFINE_PER_CPU(struct softnet_data, softnet_data);
  → 每个 CPU 的网络包处理队列

中断计数:
  DEFINE_PER_CPU_SECTION(irq_cpustat_t, irq_stat, ...);
  → 中断统计计数器

RCU:
  DEFINE_PER_CPU_SHARED_ALIGNED(struct rcu_data, rcu_data);
  → 每个 CPU 的 RCU 状态, 用于 quiescent state 检测

页面分配器:
  struct per_cpu_pages __percpu *per_cpu_pageset;
  → zone 的 per-CPU 页面缓存 (pcp), 单页分配快速路径
```

### 3.10.9 调试与查看

```bash
# 查看 percpu 分配器统计
cat /proc/percpu_stats      # 如果 CONFIG_PERCPU_STATS=y

# 查看每个 chunk 的使用情况
cat /sys/kernel/debug/percpu/chunks   # debugfs

# 查看 percpu 内存总占用
grep -i percpu /proc/meminfo
# Percpu:   12345 kB      (所有 CPU 的 percpu 区域总占用)

# 在 crash dump 中查看 percpu 变量:
# crash> per_cpu counter   (会显示每个 CPU 的值)
```

---

## 全章知识脉络（3.1~3.10）

```
第 3 章 内存管理
│
├── 3.1 分层架构
│   虚拟内存管理 → 上层分配器(SLUB/vmalloc/percpu) → 伙伴分配器 → 物理页
│
├── 虚拟地址空间
│   ├── 3.2 虚拟地址空间布局
│   │   ├── ARM64: TTBR0(用户) / TTBR1(内核)，VA_BITS 可配置
│   │   ├── 用户空间：代码→数据→堆↑ ... mmap↓→栈 (ASLR 随机化)
│   │   ├── 内核空间：模块→内核镜像→vmalloc→fixmap→PCI→vmemmap→线性映射
│   │   └── mm_struct：VMA 管理迁移到 maple tree（6.1）
│   └── 3.3 物理地址空间
│       ├── Normal Memory vs Device Memory
│       └── MAIR_EL1 属性配置 + ioremap
│
├── 内存映射
│   └── 3.4 mmap/munmap
│       ├── 文件/匿名 × 共享/私有（2×2 矩阵）
│       ├── VMA 数据结构与 vm_flags
│       ├── Demand Paging：mmap 不分配物理页，缺页时才分配
│       └── 过量提交策略（GUESS/ALWAYS/NEVER）
│
├── 物理内存组织
│   └── 3.5 node → zone → page 三级结构
│       ├── ARM64 无 ZONE_HIGHMEM
│       ├── zone 水位线 + lowmem_reserve
│       └── struct page flags 编码（SECTION|NODE|ZONE|FLAGS）
│
└── 物理内存分配器（层次关系）
    ├── 3.6 memblock（引导期间，替代 bootmem）
    ├── 3.7 伙伴分配器（基座）
    │   ├── 2^order 分配/释放 + 伙伴合并
    │   ├── 水位线 MIN/LOW/HIGH + watermark_boost(6.1)
    │   ├── 迁移类型防碎片
    │   └── 快速路径 → 慢速路径(回收+压缩) → OOM
    ├── 3.8 块分配器（建在 buddy 之上）
    │   ├── SLUB（6.1 默认）：per-CPU freelist + 无锁 cmpxchg
    │   ├── SLAB（6.5 移除）：三链表 + 着色 + array_cache
    │   └── SLOB（6.4 移除）：极简 first-fit
    ├── 3.9 vmalloc（建在 buddy 之上）
    │   ├── 逐页分配物理页 + 内核页表映射
    │   └── 每次分配末尾加 guard page 检测越界
    └── 3.10 percpu（first chunk 线性映射，后续 chunk vmalloc）
        ├── per_cpu_ptr = ptr + __per_cpu_offset[cpu]
        └── 无锁访问，避免 cache line bouncing
```
