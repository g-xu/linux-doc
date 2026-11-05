# vmalloc — 虚拟连续内存分配

> 分配物理不连续但虚拟地址连续的内存。用于大块内存（> 页大小）、模块加载、vmalloc 区域映射。arm64 上 vmalloc 区域位于 `VMALLOC_START` ~ `VMALLOC_END`。

---

## §1 核心设计洞察

### 1.1 虚拟连续 vs 物理连续

```
kmalloc(64KB) → 物理连续 64KB（要求 buddy 有 order-4 空闲块）
vmalloc(64KB) → 16 个物理页（可以分散），映射到连续虚拟地址

虚拟地址空间:
  VMALLOC_START                          VMALLOC_END
  ┌──────────────────────────────────────┐
  │ [vm_area1][guard][vm_area2][guard]... │
  └──────────────────────────────────────┘
       ↓ 页表映射              ↓ 页表映射
  物理页 A              物理页 B
  物理页 C              物理页 D
  物理页 E              物理页 F
  ...                   ...
```

### 1.2 核心结构体

**`struct vmap_area`**（`mm/vmalloc.c:646-659`）：描述一个 vmalloc 区域的虚拟地址范围。

```c
struct vmap_area {
    unsigned long va_start;      // 虚拟起始地址
    unsigned long va_end;        // 虚拟结束地址
    unsigned long flags;         // 标志（VM_VM_AREA 等）
    struct rb_node rb_node;      // rbtree 节点（快速查找）
    struct list_head list;       // 链表（排序遍历）
    struct list_head purge_list; // 延迟释放链表
};
```

**`struct vm_struct`**（`include/linux/vmalloc.h:41-55`）：描述一个完整的 vmalloc 分配。

```c
struct vm_struct {
    struct vm_struct    *next;       // 全局链表
    void                *addr;       // 虚拟地址（页对齐）
    unsigned long       size;        // 大小（含 guard page）
    unsigned long       flags;       // VM_ALLOC 等
    struct page         **pages;     // 物理页数组
    unsigned int        nr_pages;    // 页数
    void                *priv;       // 私有数据
};
```

### 1.3 分配路径

`mm/vmalloc.c`（4,238 行）：

```
vmalloc(size)
    → __vmalloc_node(size, 1, GFP_KERNEL, NUMA_NO_NODE, caller)
        → __vmalloc_node_range(size, 1, VMALLOC_START, VMALLOC_END, ...)
            → 1. get_vm_area_caller() — 在 vmalloc 区域找到虚拟地址空间
            → 2. __vmalloc_area_node() — 分配物理页并映射
                → alloc_page() × nr_pages — 逐页分配
                → vmap_pages_range_noflush() — 建立页表映射
```

### 1.4 虚拟地址空间管理

vmalloc 区域使用 **rbtree + 链表** 双重管理：
- **rbtree**（`free_vmap_area_root`）：快速查找空闲区域
- **链表**（`free_vmap_area_list`）：按地址排序，用于合并

分配新区域时：在空闲 rbtree 中找足够大的洞（hole），分割使用。释放时：合并相邻空闲区域。

### 1.5 Guard Page

每个 vmalloc 分配后面有一个 guard page（不可访问的虚拟页）。如果代码越界写入 vmalloc 区域，访问 guard page 会触发 page fault 而非静默破坏相邻分配。

### 1.6 延迟释放（Lazy vfree）

`vfree` 不立即释放页表和页——而是把 `vmap_area` 加入 `purge_list`，由 `free_work`（workqueue）批量清理。这避免了频繁修改页表（TLB flush 开销大）。

---

## §2 API 速查

头文件：`include/linux/vmalloc.h`；实现：`mm/vmalloc.c`

### 2.1 基本分配

| API | 说明 | 备注 |
|-----|------|------|
| `vmalloc(size)` | 分配虚拟连续内存 | 不初始化，可睡眠 |
| `vzalloc(size)` | 分配并清零 | `vmalloc + __GFP_ZERO` |
| `vmalloc_user(size)` | 分配可映射到用户空间 | `VM_USERMAP` 标志 |
| `vmalloc_node(size, node)` | 指定 NUMA 节点 | |
| `vzalloc_node(size, node)` | 指定节点 + 清零 | |
| `__vmalloc(size, flags)` | 带自定义 GFP 标志 | |
| `__vmalloc_node(size, align, flags, node, caller)` | 完全自定义 | 内部函数 |
| `vfree(addr)` | 释放 vmalloc 内存 | 延迟释放 |
| `vfree_atomic(addr)` | 原子上下文释放 | 不睡眠 |
| `kvfree(addr)` | 释放 kmalloc 或 vmalloc 的内存 | 自动判断 |
| `kvfree_sensitive(addr, len)` | 释放前清零 | |

### 2.2 vmap（映射已有页）

| API | 说明 | 备注 |
|-----|------|------|
| `vmap(pages, count, flags, prot)` | 映射已有物理页到连续虚拟地址 | 不分配页 |
| `vunmap(addr)` | 解除 vmap 映射 | |
| `vmap_pages_range_noflush(addr, end, prot, pages, shift)` | 内部：建立页表 | |

### 2.3 查询

| API | 说明 | 备注 |
|-----|------|------|
| `is_vmalloc_addr(addr)` | 地址是否在 vmalloc 区域 | arm64: `VMALLOC_START ≤ addr < VMALLOC_END` |
| `is_vm_area_huge(addr)` | 是否是大分配 | |
| `vmalloc_info()` | 统计信息 | `/proc/vmallocinfo` |

### 2.4 特殊用途

| API | 说明 | 备注 |
|-----|------|------|
| `vmalloc_sync_all()` | 同步所有页表 | 模块加载用 |
| `map_kernel_range_noflush()` | 映射内核虚拟范围 | |
| `get_vm_area(size, flags)` | 只分配虚拟地址空间 | 不分配物理页 |
| `remove_vm_area(addr)` | 移除 vm_struct | |
| `find_vm_area(addr)` | 查找 vm_struct | |

---

## §3 使用模式

### 3.1 大块内存分配

```c
// 需要 1MB 连续虚拟地址，物理页可以分散
void *buf = vmalloc(1024 * 1024);
if (!buf)
    return -ENOMEM;
// 使用 buf...
vfree(buf);
```

### 3.2 模块加载

```c
// 模块代码段需要可执行虚拟内存
struct vm_struct *area = get_vm_area(module_size, VM_ALLOC);
// ... 加载模块内容到 area->addr ...
```

### 3.3 vmap（映射已有页）

```c
// 已有分散的物理页，需要连续虚拟地址访问
struct page *pages[16];
// ... 分配 pages ...
void *vaddr = vmap(pages, 16, VM_MAP, PAGE_KERNEL);
if (!vaddr)
    return -ENOMEM;
// 通过 vaddr 连续访问所有页
vunmap(vaddr);
```

### 3.4 kvfree（不确定来源）

```c
void *ptr;
if (size > PAGE_SIZE)
    ptr = vmalloc(size);
else
    ptr = kmalloc(size, GFP_KERNEL);
// ... 使用 ...
kvfree(ptr);  // 自动判断 kfree 还是 vfree
```

---

## §4 常见陷阱

### 4.1 vmalloc 不能用于原子上下文

**错误**：中断 handler 或 spinlock 内调用 `vmalloc`。vmalloc 分配物理页并建立页表映射，可能睡眠。

**正确**：原子上下文用 `kmalloc(size, GFP_ATOMIC)`。如果必须大块内存，提前分配。

### 4.2 vmalloc 内存的页表开销

**事实**：每次 vmalloc 都需要修改页表 + TLB flush。频繁的小 vmalloc 分配/释放会显著增加 TLB 压力。

**正确**：小块内存（< 页大小）用 kmalloc。vmalloc 适合大块（多页）分配。

### 4.3 vmalloc 和 DMA

**错误**：把 vmalloc 地址传给 DMA 硬件。

**事实**：vmalloc 地址是虚拟连续的，但物理不连续。DMA 需要物理连续地址（或 IOMMU 映射）。

**正确**：DMA 缓冲区用 `dma_alloc_coherent` 或 CMA。

### 4.4 is_vmalloc_addr 的判断

`is_vmalloc_addr` 检查地址是否在 vmalloc 区域。在 arm64 上检查 `VMALLOC_START ≤ addr < VMALLOC_END`。如果代码需要判断指针来源（kmalloc vs vmalloc）以选择正确的释放函数，用 `kvfree` 代替手动判断。

### 4.5 vfree 的延迟释放

`vfree` 不立即释放页表——延迟到 workqueue 执行。如果立即需要虚拟地址空间（如模块卸载后立即加载新模块），可能需要 `vmalloc_sync_all()` 强制同步。

### 4.6 vmalloc 区域大小有限

arm64 上 vmalloc 区域通常 128GB 或 32GB（取决于配置）。大量 vmalloc 可能耗尽虚拟地址空间。`/proc/vmallocinfo` 可查看使用情况。

---

## §5 代码示例

### 5.1 文件系统元数据缓冲区

```c
// 需要大块连续缓冲区读取大量目录项
static int read_directory_entries(struct super_block *sb)
{
    void *buf;
    size_t buf_size = 256 * PAGE_SIZE;  // 1MB

    buf = vzalloc(buf_size);
    if (!buf)
        return -ENOMEM;

    // 读取目录项到缓冲区
    int ret = read_metadata(sb, buf, buf_size);
    if (ret < 0) {
        vfree(buf);
        return ret;
    }

    // 处理数据...
    process_entries(buf, ret);
    vfree(buf);
    return 0;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| vmalloc.c 实现 | `mm/vmalloc.c`（4,238 行） | 一等 |
| vmalloc.h API 声明 | `include/linux/vmalloc.h`（287 行） | 一等 |
| vmap_area 结构体 | `mm/vmalloc.c:646-659` | 一等 |
| vm_struct 结构体 | `include/linux/vmalloc.h:41-55` | 一等 |
| vmalloc → __vmalloc_node 路径 | `mm/vmalloc.c` | 一等 |
| Guard page 机制 | `mm/vmalloc.c` | 一等 |
| Lazy vfree | `mm/vmalloc.c` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `vmalloc` | 2,036 |
| `vzalloc` | 418 |
| `vfree` | 1,459 |
| `vmap` | 370 |
| `vunmap` | 149 |
| `is_vmalloc_addr` | 104 |
| `__vmalloc` | 63 |
