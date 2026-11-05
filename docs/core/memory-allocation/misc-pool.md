# mempool 与 gen_pool — 内存池与通用分配器

> mempool 通过预分配保证在内存压力下仍能分配（面向"不能失败"的场景）；gen_pool 用位图管理预留内存区域（面向设备内存、SRAM 等特殊内存）。两者设计目标完全不同但互补。

---

## mempool — 死锁安全的预分配池

### 定位

**功能**：块 I/O、SCSI 命令处理等关键路径上，内存分配失败意味着死锁（因为回收本身可能需要分配）。mempool 预分配 `min_nr` 个对象，保证从进程上下文分配永远不失败——这是"死锁安全"的核心承诺。

**实现方式**：核心结构体 `mempool_t`（`include/linux/mempool.h:16-26`）包含最小保证元素数（`min_nr`）、当前空闲数（`curr_nr`）、预分配指针数组（`elements`）、用户 alloc/free 回调和阻塞等待队列。分配路径（`mm/mempool.c:374-441`）：先用降低的 GFP 标志尝试用户回调，失败从预分配池弹出，池空用完整 GFP 重试，仍失败则在 `pool->wait` 上以 `io_schedule_timeout(5*HZ)` 睡眠重试——进程上下文永不失败。释放时（`mm/mempool.c:451-502`）优先补充池（`curr_nr < min_nr`），超出才真正释放。内置三对回调：slab（`mempool_alloc_slab`）、kmalloc（`mempool_kmalloc`）、页分配器（`mempool_alloc_pages`）。

**应用场景**：`mempool_alloc` 292 处调用（105 个文件）。主要用户：SCSI 驱动（`drivers/scsi/lpfc/`，用于 ELS/SLI 命令）、device mapper（`drivers/md/`）、NFS 写路径（`fs/nfs/write.c`）、f2fs 数据路径（`fs/f2fs/data.c`）、Ceph（`fs/ceph/addr.c`、`net/ceph/osd_client.c`）、GFS2 日志（`fs/gfs2/log.c`）、SMB Direct/RDMA（`fs/smb/client/smbdirect.c`、`fs/smb/server/transport_rdma.c`）和 BPF 内存分配器（`kernel/bpf/memalloc.c`）。

### 实现方式

核心结构体（`include/linux/mempool.h:16-26`）：

```c
typedef struct mempool_s {
    spinlock_t lock;              // 保护 elements 数组
    int min_nr;                   // 保证的最小元素数
    int curr_nr;                  // 池中当前空闲元素数
    void **elements;              // 预分配元素指针数组
    void *pool_data;              // 传给 alloc/free 回调的不透明数据
    mempool_alloc_t *alloc;       // 用户分配回调
    mempool_free_t *free;         // 用户释放回调
    wait_queue_head_t wait;       // 阻塞分配者的等待队列
} mempool_t;
```

**分配路径**（`mm/mempool.c:374-441`）：
1. 先用降低的 GFP 标志（无直接回收、无 I/O）尝试用户 `alloc` 回调
2. 失败则加锁检查 `curr_nr > 0`，从池中弹出预分配对象
3. 池空则用完整 GFP 标志重试一次
4. 仍失败且允许阻塞：在 `pool->wait` 上以 `io_schedule_timeout(5*HZ)` 睡眠，然后重试
5. **从进程上下文永不失败**（由预分配保证）

**释放路径**（`mm/mempool.c:451-502`）：
1. 如果 `curr_nr < min_nr`：对象归还池中（补充），唤醒等待者
2. 否则：用用户 `free` 回调释放

**内置回调对**（`mm/mempool.c`）：

| 回调对 | 后端 |
|--------|------|
| `mempool_alloc_slab / mempool_free_slab` | kmem_cache |
| `mempool_kmalloc / mempool_kfree` | kmalloc/kfree |
| `mempool_alloc_pages / mempool_free_pages` | 页分配器 |

### 主要 API

头文件：`include/linux/mempool.h`；实现：`mm/mempool.c`

| API | 说明 | 备注 |
|-----|------|------|
| `mempool_create(min_nr, alloc, free, pool_data)` | 创建堆分配的池 | |
| `mempool_create_node(min_nr, alloc, free, pool_data, gfp, nid)` | 指定 NUMA 节点 | |
| `mempool_init(pool, min_nr, alloc, free, pool_data)` | 初始化嵌入式池 | 不堆分配 mempool_t 本身 |
| `mempool_init_node(pool, min_nr, alloc, free, pool_data, gfp, nid)` | 嵌入式 + NUMA | |
| `mempool_destroy(pool)` | 销毁堆分配的池 | |
| `mempool_exit(pool)` | 销毁嵌入式池 | |
| `mempool_resize(pool, new_min_nr)` | 调整池大小 | |
| `mempool_alloc(pool, gfp)` | 分配 | 进程上下文永不失败 |
| `mempool_free(element, pool)` | 释放 | 补充池或调用 free 回调 |

**便捷内联函数**：

| 函数 | 后端 |
|------|------|
| `mempool_create_slab_pool(min_nr, kc)` | kmem_cache |
| `mempool_create_kmalloc_pool(min_nr, size)` | kmalloc |
| `mempool_create_page_pool(min_nr, order)` | 页分配器 |

### 使用规模

`mempool_alloc` 292 处（105 个文件）、`mempool_free` 425 处（101 个文件）、`mempool_destroy` 110 处。主要用户：`drivers/scsi/lpfc/`、`drivers/md/`（device mapper）、`drivers/scsi/csiostor/`、`fs/smb/`。

---

## gen_pool — 位图驱动的通用内存分配器

### 定位

**功能**：管理 slab/页分配器不覆盖的特殊内存区域：设备 SRAM、TCM（紧耦合内存）、DMA 描述符池、GPU 内存等。用位图追踪分配状态，支持多种分配算法（首次适配、最佳适配、对齐适配等）。

**实现方式**：核心结构体 `struct gen_pool`（`include/linux/genalloc.h:58-67`）管理 chunk 链表，每个 chunk（`gen_pool_chunk`，`:72-80`）含位图柔性数组 `bits[]` 追踪分配状态。关键设计是无锁分配：使用 `cmpxchg` 循环操作位图（`set_bits_ll / clear_bits_ll`，`lib/genalloc.c:43-71`），arm64 有 `CONFIG_ARCH_HAVE_NMI_SAFE_CMPXCHG` 因此 NMI 安全。结构变更（添加/删除 chunk）仍用 spinlock，遍历 chunk 用 `rcu_read_lock()`。支持多种算法：`gen_pool_first_fit`（默认）、`gen_pool_best_fit`（最小足够区域）、`gen_pool_first_fit_align`（字节对齐）等。

**应用场景**：SRAM 驱动、DMA 描述符池（Freescale QMan/BMan `drivers/soc/fsl/qbman/`、TI CPPI `drivers/net/ethernet/ti/k3-cppi-desc-pool.c`）、ACPI APEI/GHES 错误处理（`drivers/acpi/apei/ghes.c`）、网络描述符池（Davinci `drivers/net/ethernet/ti/davinci_cpdma.c`、Mellanox ACL `drivers/net/ethernet/mellanox/mlxsw/spectrum_acl_erp.c`）、FPGA 固件服务（`drivers/firmware/stratix10-svc.c`）、EDAC 错误检测（`drivers/edac/altera_edac.c`、`drivers/edac/igen6_edac.c`）。`gen_pool_alloc` 47 处、`gen_pool_free` 85 处调用。

### 实现方式

核心结构体（`include/linux/genalloc.h:58-67`）：

```c
struct gen_pool {
    spinlock_t lock;              // 保护 chunk 链表修改
    struct list_head chunks;      // chunk 链表
    int min_alloc_order;          // 每个位图位代表的字节数 = 2^order
    genpool_algo_t algo;          // 分配算法回调
    void *data;                   // 算法附加数据
    const char *name;             // 可选池名
};
```

chunk 结构（`include/linux/genalloc.h:72-80`）：

```c
struct gen_pool_chunk {
    struct list_head next_chunk;  // 链接到下一个 chunk
    atomic_long_t avail;          // 本 chunk 可用字节数
    phys_addr_t phys_addr;        // 物理起始地址
    void *owner;                  // 私有数据
    unsigned long start_addr;     // 虚拟起始
    unsigned long end_addr;       // 虚拟结束（含）
    unsigned long bits[];         // 柔性数组：位图追踪分配
};
```

**关键设计——无锁分配**：
- 使用 `cmpxchg` 循环操作位图（`set_bits_ll / clear_bits_ll`，`lib/genalloc.c:43-71`）
- arm64 有 `CONFIG_ARCH_HAVE_NMI_SAFE_CMPXCHG`，因此 **NMI 安全**
- 结构变更（添加/删除 chunk）仍用 spinlock
- 分配遍历 chunk 时用 `rcu_read_lock()`

**分配算法**：

| 算法 | 说明 |
|------|------|
| `gen_pool_first_fit` | 首个可用区域（默认） |
| `gen_pool_first_fit_align` | 首次适配 + 字节对齐 |
| `gen_pool_first_fit_order_align` | 首次适配 + 2 的幂对齐 |
| `gen_pool_fixed_alloc` | 固定偏移预留 |
| `gen_pool_best_fit` | 最小足够区域（扫描所有候选） |

### 主要 API

头文件：`include/linux/genalloc.h`；实现：`lib/genalloc.c`

| API | 说明 | 备注 |
|-----|------|------|
| `gen_pool_create(min_order, nid)` | 创建池 | 默认 first_fit 算法 |
| `gen_pool_add(pool, addr, size, nid)` | 添加 chunk | 虚拟地址，phys=-1 |
| `gen_pool_add_virt(pool, virt, phys, size, nid)` | 添加带物理地址的 chunk | |
| `gen_pool_destroy(pool)` | 销毁池 | BUG 如果有未释放分配 |
| `gen_pool_alloc(pool, size)` | 分配 | 用池默认算法 |
| `gen_pool_free(pool, addr, size)` | 释放 | |
| `gen_pool_avail(pool)` | 总可用字节 | |
| `gen_pool_size(pool)` | 总管理字节 | |
| `gen_pool_virt_to_phys(pool, virt)` | 虚拟→物理转换 | |
| `gen_pool_for_each_chunk(pool, func, data)` | 遍历 chunk | |
| `gen_pool_has_addr(pool, start, size)` | 检查地址范围 | |
| `gen_pool_set_algo(pool, algo, data)` | 更改算法 | |
| `gen_pool_get(dev, name)` | 从设备获取池 | 按名称查找 |
| `devm_gen_pool_create(dev, min_order, nid, name)` | 设备管理创建 | devres 自动释放 |
| `of_gen_pool_get(np, propname, index)` | 从 DT 获取池 | phandle 引用 |

**DMA 变体**：

| API | 说明 |
|-----|------|
| `gen_pool_dma_alloc(pool, size)` | DMA 分配 |
| `gen_pool_dma_zalloc(pool, size)` | DMA 分配 + 清零 |
| `gen_pool_dma_alloc_align(pool, size, align)` | DMA 分配 + 对齐 |

### 使用规模

`gen_pool_alloc` 47 处（33 个文件）、`gen_pool_free` 85 处（52 个文件）、`gen_pool_create` 42 处。典型用户：SRAM 驱动、DMA 描述符池、GPU 驱动、ACPI APEI/GHES 错误处理、网络描述符池（Freescale QMan/BMan）、HabanaLabs AI 加速器。

---

## 对比

| 特性 | mempool | gen_pool |
|------|---------|----------|
| 目标 | 保证分配不失败 | 管理特殊内存区域 |
| 锁 | 全局 spinlock | 无锁 cmpxchg（NMI 安全） |
| 后端 | 任意（slab/kmalloc/pages/自定义） | 位图管理连续内存 |
| 预分配 | 是（min_nr 个对象） | 否（chunk 按需添加） |
| 保证 | 进程上下文永不失败 | 位图满时失败 |
| 典型场景 | 块 I/O、SCSI 命令 | SRAM、TCM、DMA 池 |

> **注**：`objpool`（per-CPU 无锁对象池）在 Linux 6.4 才引入，6.1.y 中不存在。
