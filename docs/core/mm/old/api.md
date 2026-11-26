# 内存管理主要 API 与使用方式

本页服务于接口查询，不要求按学习章节顺序阅读。原理入口为 [分配](02-allocation.md)、[地址空间](03-address-space.md) 与 [资源再利用](04-reclaim-rmap.md)。

需要直接查参数、错误判据、调用条件及完整清理模式时，进入 [内核内存 API 详细参考](api-reference.md)。本页保留选型与最短示例；mmap 生命周期、扩容、池资源等详细契约集中维护在参考页。

以内核 Linux 6.1.158、arm64 为基线。前六节的分配教学函数默认普通可睡眠任务上下文，资源独立持有、没有并发或异步使用；它们不是完整模块，未编译、加载或运行。设备和固定相关入口另按各节说明遵循其使用者与完成契约。

## 1. 先选择需求，再选择接口

| 需求 | 候选 API | 关键约束与清理 | 详细用法 |
| --- | --- | --- | --- |
| 小字节块，需清零 | `kzalloc` | 初始化真实对象；`kfree` | [字节块](api-reference.md#bytes) |
| 数组 | `kmalloc_array` / `kcalloc` | 检查元素乘法，通常 `kfree` | [数组与容量](api-reference.md#sizes) |
| 专用固定类型对象 | `kmem_cache_*` | cache 与对象两层生命周期 | [对象缓存](api-reference.md#cache) |
| 明确按阶物理块 | `alloc_pages` | 保存 order、使用协议匹配；`__free_pages` | [页块](api-reference.md#pages) |
| 可变 CPU 工作区，接受两类结果 | `kvmalloc` / `kvzalloc` / `kvcalloc` | 适用可睡眠上下文，不依赖物理连续；`kvfree` | [工作区](api-reference.md#virtual) |
| 明确动态虚拟区域的新存储 | `vmalloc` / `vzalloc` | 管理存储与映射；`vfree` | [工作区](api-reference.md#virtual) |
| 已有页，需要连续内核映射 | `vmap` | 默认映射与页所有权分开；`vunmap` | [已有页](api-reference.md#vmap) |
| 用户态建立、解除、保护或提示地址范围 | `mmap` / `munmap` / `mprotect` / `madvise` | 用户态库接口、权限及映射类型 | [用户态](#userspace) |
| 设备使用 RAM 或访问寄存器 | DMA API / `devm_ioremap_resource` | DMA 地址、完成与同步；MMIO 资源和错误指针 | [DMA](api-reference.md#dma) / [MMIO](api-reference.md#mmio) |
| 固定适用用户页 | `pin_user_pages*` / `unpin_user_pages*` | 部分成功、固定及脏状态契约 | [固定](api-reference.md#pin) |
| 大页、节点策略、文件同步或资源组查询 | 对应用户接口及控制文件 | 各类失败与权限，不统一当成普通分配 | [扩展入口](#policy-queries) |
| 柔性数组扩容 | `struct_size` / `krealloc` | 临时结果、失败保留、新增部分初始化 | [扩容骨架](api-reference.md#resize) |
| 复制用户请求 | `copy_from_user` / `copy_to_user` | 剩余字节、语义验证和输出初始化 | [用户访问](api-reference.md#uaccess) |
| 内核缓冲区用户映射 | `vm_insert_page` / `remap_vmalloc_range` | 页引用、VMA 和后备对象分别存活 | [单页](api-reference.md#insert-page) / [共享缓冲区](api-reference.md#user-map) |
| 保底元素和 DMA 小块池 | `mempool_*` / `dma_pool_*` | 同一池归还，不能只靠 min_nr 推断硬上限 | [Mempool](api-reference.md#mempool) / [DMA Pool](api-reference.md#dma-pool) |

接口是否适用不只取决于大小。还要确定实际使用者、连续性、上下文、失败策略和释放责任。DMA、MMIO、用户映射回调和固定用户页不在普通缓冲区示例的契约内。

<a id="bytes"></a>

## 2. 字节块与数组

头文件为 `<linux/slab.h>`。以下为去掉属性后的代表性调用签名，部分是 inline 包装：

```c
void *kmalloc(size_t size, gfp_t flags);
void *kzalloc(size_t size, gfp_t flags);
void *kmalloc_array(size_t n, size_t size, gfp_t flags);
void *kcalloc(size_t n, size_t size, gfp_t flags);
void kfree(const void *ptr);
```

- `size` 单位是字节，数组接口将个数与单元素大小分别传入。
- `kmalloc` 不清零，`kzalloc` 清零；`kcalloc` 检查乘法并清零。
- 正常非零请求失败返回 NULL。零大小有特殊返回语义，不当作可读写对象；示例主动拒绝零个数。
- 不应硬编码所有配置统一的最大缓存尺寸。较大的 kmalloc 可以走页分配路径，仍需满足其物理连续结果契约。

```c
#include <linux/errno.h>
#include <linux/slab.h>
#include <linux/types.h>

struct sample_entry {
    u32 flags;
    u32 value;
};

static int array_example(size_t count)
{
    struct sample_entry *entries;

    if (!count)
        return -EINVAL;
    entries = kcalloc(count, sizeof(*entries), GFP_KERNEL);
    if (!entries)
        return -ENOMEM;

    entries[0].value = 1;
    kfree(entries);
    return 0;
}
```

清零只初始化字节。真实对象中的链表、锁、引用计数等仍应使用其初始化接口。发布给其他访问者后，销毁必须等待或结束相应使用，不能因为 `kfree` 有内部同步就忽略对象并发。

避免先做可能溢出的 `count * size` 再传普通字节接口。柔性数组等复杂布局可比较 `struct_size` 类 helper，而不是手写未经检查的容量计算。

<a id="object-cache"></a>

## 3. 专用对象缓存

头文件 `<linux/slab.h>`。本树签名为：

```c
struct kmem_cache *kmem_cache_create(
    const char *name, unsigned int size, unsigned int align,
    slab_flags_t flags, void (*ctor)(void *));
void *kmem_cache_alloc(struct kmem_cache *s, gfp_t flags);
void *kmem_cache_zalloc(struct kmem_cache *s, gfp_t flags);
void kmem_cache_free(struct kmem_cache *s, void *obj);
void kmem_cache_destroy(struct kmem_cache *s);
```

Cache 创建标志是 `slab_flags_t`，对象分配标志是 `gfp_t`，两者不是同一参数族。

创建失败通常返回 NULL，特殊 `SLAB_PANIC` 策略另有行为；本例不用它。对象分配失败也返回 NULL。`ctor` 不是每次取得对象都执行的普通初始化替代品，若需要每次初始化，应在分配后明确完成。

```c
#include <linux/errno.h>
#include <linux/list.h>
#include <linux/slab.h>
#include <linux/types.h>

struct demo_item {
    u32 tag;
    struct list_head link;
};

static int cache_example(void)
{
    struct kmem_cache *cache;
    struct demo_item *item;

    cache = kmem_cache_create("mm_notes_item", sizeof(*item),
                             __alignof__(struct demo_item), 0, NULL);
    if (!cache)
        return -ENOMEM;

    item = kmem_cache_zalloc(cache, GFP_KERNEL);
    if (!item) {
        kmem_cache_destroy(cache);
        return -ENOMEM;
    }
    INIT_LIST_HEAD(&item->link);
    item->tag = 1;

    kmem_cache_free(cache, item);
    kmem_cache_destroy(cache);
    return 0;
}
```

示例只演示配对。真实组件应在初始化建立长期 cache，在需要时分配对象，停止全部使用并归还所有对象后再销毁；不为每个对象反复创建 cache。

频繁分配同一结构不自动意味着必须专用 cache，应比较规格、配置与管理需求。涉及向用户态复制的对象，还要按范围核对 usercopy 契约；RCU 类型安全标志也不等于延迟对象复用。

<a id="pages"></a>

## 4. 明确的按阶物理块

明确需要 `2^order` 个连续基本页时选择 `alloc_pages`；普通独占块保存原阶，用 `__free_pages` 配对。小对象、可接受物理不连续的 CPU 工作区和设备 DMA 存储仍按各自需求选型。

本节只保留查询导航，函数参数与完整示例集中维护：

- [页分配 API 详细参考](api-reference.md#pages)：参数、返回、上下文、释放模型、真实调用者与错误处理。
- [Exact 分配](api-reference.md#exact)：非二次幂页数容量及专用释放接口。
- [页内容访问](api-reference.md#access)：`page_address` 与 Local 访问配对。
- [Buddy 深入专题](sub/page-allocation/01-buddy.md)：空闲块组织、拆分、合并及实现约束。

<a id="workspace"></a>

## 5. CPU 工作区与动态虚拟区域

头文件 `<linux/slab.h>` 与 `<linux/vmalloc.h>`：

```c
void *kvmalloc(size_t size, gfp_t flags);
void *kvzalloc(size_t size, gfp_t flags);
void *kvcalloc(size_t n, size_t size, gfp_t flags);
void kvfree(const void *ptr);
void *vmalloc(unsigned long size);
void *vzalloc(unsigned long size);
void vfree(const void *addr);
```

`kvzalloc` 适合需要清零、接受 kmalloc 或 vmalloc 结果的 CPU 工作区。`kvcalloc` 为数组增加乘法检查。`vzalloc` 明确选择动态虚拟区域。

正常非零请求失败返回 NULL。结果不保证整体物理连续，不能直接作为 DMA 地址，也不能将任意动态地址交给直接映射转换 helper。

```c
#include <linux/errno.h>
#include <linux/slab.h>

static int workspace_example(size_t bytes)
{
    unsigned char *data;

    if (!bytes)
        return -EINVAL;
    data = kvzalloc(bytes, GFP_KERNEL);
    if (!data)
        return -ENOMEM;

    data[0] = 1;
    kvfree(data);
    return 0;
}
```

使用 `kvfree` 接受两类结果。不要根据一次测试碰巧来自 kmalloc，就改用 `kfree` 或依赖物理连续。

本树 `kvmalloc_node` 不支持 `GFP_NOWAIT` / `GFP_ATOMIC` 通用非阻塞调用，也不支持随意添加 `__GFP_NORETRY`。回退需要大小和阻塞条件；它不是每种失败的成功保证。

异步使用结束之后才能清理。上述示例没有工作线程、设备访问或跨线程发布，不提供这些生命周期的通用模板。

<a id="existing-pages"></a>

## 6. 已有页组成连续映射

头文件 `<linux/vmalloc.h>`，保护类型还依赖目标架构定义：

```c
void *vmap(struct page **pages, unsigned int count,
           unsigned long flags, pgprot_t prot);
void vunmap(const void *addr);
```

`count` 单位是页，`flags` 是虚拟区域标志，不是 GFP。`vmap` 返回虚拟基址或 NULL。

本例使用普通 `VM_MAP`，不转移页数组和页引用所有权：先 `vunmap` 撤销映射，再由调用者释放页。两者在本例中都放在允许睡眠的任务上下文。

```c
#include <asm/pgtable.h>
#include <linux/errno.h>
#include <linux/gfp.h>
#include <linux/mm.h>
#include <linux/vmalloc.h>

static int vmap_example(void)
{
    struct page *pages[4];
    unsigned int i;
    unsigned char *addr;
    int ret = -ENOMEM;

    for (i = 0; i < 4; i++) {
        pages[i] = alloc_page(GFP_KERNEL | __GFP_ZERO);
        if (!pages[i])
            goto out_pages;
    }
    addr = vmap(pages, 4, VM_MAP, PAGE_KERNEL);
    if (!addr)
        goto out_pages;

    addr[0] = 1;
    vunmap(addr);
    ret = 0;
out_pages:
    while (i)
        __free_page(pages[--i]);
    return ret;
}
```

部分分配失败只释放已成功取得的页；映射失败释放全部四页；成功先撤映射再归还存储。

`VM_MAP_PUT_PAGES` 是不同契约：数组自身和相应页引用转移给映射管理，后续用 `vfree` 配合。数组须符合分配来源要求，不能把本例栈数组改标志后套用。

<a id="userspace"></a>

## 7. 用户态地址范围接口

这是用户态库接口，不是可加载内核模块应直接调用的 API。头文件 `<sys/mman.h>`：

```c
void *mmap(void *addr, size_t length, int prot, int flags,
           int fd, off_t offset);
int munmap(void *addr, size_t length);
int mprotect(void *addr, size_t length, int prot);
int madvise(void *addr, size_t length, int advice);
```

| 接口 | 用途与失败处理 |
| --- | --- |
| `mmap` | 建立映射范围；失败为 `MAP_FAILED`，不是 NULL |
| `munmap` | 解除相应范围；成功 0，失败 -1 并设置 errno |
| `mprotect` | 改变许可；成功 0，失败 -1，地址需满足页对齐等条件 |
| `madvise` | 对范围提供特定 advice；不是通用“释放物理内存”，语义按选项区分 |

`MAP_PRIVATE` 与 `MAP_SHARED` 表达不同共享及写入语义。文件 `offset` 是字节单位，需满足页对齐；内核 VMA 的 `vm_pgoff` 则以基本页计数。

以下用户态教学函数不预填充，只写一字节，随后保护为只读再解除映射：

```c
#include <sys/mman.h>
#include <unistd.h>

static int user_mapping_example(void)
{
    long page_size = sysconf(_SC_PAGESIZE);
    unsigned char *p;
    size_t len;

    if (page_size <= 0)
        return -1;
    len = (size_t)page_size;
    p = mmap(NULL, len, PROT_READ | PROT_WRITE,
             MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED)
        return -1;

    p[0] = 1;
    if (mprotect(p, len, PROT_READ) == -1) {
        munmap(p, len);
        return -1;
    }
    return munmap(p, len);
}
```

实际程序还应记录错误并设计恢复。`mmap` 成功不代表全部数据页驻留；`munmap` 成功也不保证共享或缓存数据立即回到空闲页供给。

<a id="device-memory"></a>

## 8. DMA 内存与 MMIO 资源

这是设备使用入口，不是普通字节分配器的别名。调用者需要有效设备及资源、正确设备地址能力、传输方向和完成协议。

### 8.1 Coherent DMA：保留两个地址，按同一参数释放

头文件 `<linux/dma-mapping.h>`：

```c
void *dma_alloc_coherent(struct device *dev, size_t size,
                        dma_addr_t *dma_handle, gfp_t gfp);
void dma_free_coherent(struct device *dev, size_t size,
                       void *cpu_addr, dma_addr_t dma_handle);
```

失败返回 NULL。成功同时得到 CPU 地址和设备使用的 DMA 地址；保存 dev、size 和两种地址，结束设备使用后配对释放。不能改成普通 `kfree`，也不能把 CPU 指针当作 DMA 地址。

先按设备能力检查并设置适用 DMA mask，不为全部设备硬编码统一位宽。Coherent 不免除描述符发布顺序、内存屏障或设备完成确认；设备仍使用时不能释放。

### 8.2 Streaming DMA：映射已有适用缓冲区

代表性调用形式，部分由宏或包装提供：

```c
dma_addr = dma_map_single(dev, cpu_addr, size, direction);
if (dma_mapping_error(dev, dma_addr)) {
    /* 不提交传输，按调用者策略返回或退出。 */
} else {
    /* 提交并确认设备完成；实际动作由驱动实现。 */
    dma_unmap_single(dev, dma_addr, size, direction);
}
```

以上是调用形式，不是完整驱动函数；错误分支必须停止后续传输。`DMA_TO_DEVICE` 表达设备读数据，`DMA_FROM_DEVICE` 表达设备写数据，方向不能随意替换。

有效映射后的设备使用权、CPU 使用权和结束条件要明确。复用映射时，按相应边界使用 `dma_sync_single_for_cpu` / `dma_sync_single_for_device`；同步不代替设备完成，撤销映射也不自动释放原存储。

普通 vmalloc 区域不能直接套 `dma_map_single`。分散页应按页或 scatter-gather 等相应契约处理，不靠直接映射转换绕过 DMA API。

### 8.3 MMIO：映射的是已有设备资源

声明见 `<linux/device.h>`，I/O accessor 见 `<linux/io.h>`：

```c
void __iomem *devm_ioremap_resource(struct device *dev,
                                  const struct resource *res);
```

资源须属于目标设备的适用内存资源。失败为 `ERR_PTR`，用 `IS_ERR` / `PTR_ERR` 处理，不只判断 NULL。

该接口检查和申请资源、建立映射，并由 devres 管理对应释放；它不向 Buddy 分配一份新的寄存器数据。访问使用相应 `readl` / `writel` 等 accessor，并遵守设备宽度、顺序和副作用规则。

Devm 自动撤销资源不替代停设备、停中断及结束并发访问。相关机制见 [内存属性与设备使用](03-address-space.md#tlb-cache)，依据为同树 DMA 指南和 `lib/devres.c`。

<a id="pinning"></a>

## 9. 用户页固定

头文件 `<linux/mm.h>`，本树 6.1.158 的代表性签名为：

```c
long pin_user_pages(unsigned long start, unsigned long nr_pages,
                    unsigned int gup_flags, struct page **pages,
                    struct vm_area_struct **vmas);
void unpin_user_pages(struct page **pages, unsigned long npages);
```

这里 `start` 是用户地址，`nr_pages` 是页数，输出数组需足够容纳结果；页数和首尾偏移应按覆盖范围计算。返回负值表示错误，非负结果还要判断实际取得数量，可能只部分成功。

本树这个五参数入口要求调用者已经持有当前 mm 的 `mmap_lock`，不能直接在未加锁的普通路径调用。自行管理锁的 unlocked 变体有不同参数和限制；配对加锁及部分成功清理见 [固定 API 详细参考](api-reference.md#pin)。返回的 VMA 指针不能在解锁后继续当作稳定对象使用。

调用前选择实际使用模型与读写权限，不能把任意用户指针直接转成物理地址。需要写内容时还要处理相应 COW 和脏状态契约。

Pin 包装设置内部固定标志，调用者不要向普通 GUP 随意塞 `FOLL_PIN` 来替代它。长期固定的 `FOLL_LONGTERM` 还受对应区域和实现限制。

结果必须按实际取得数量配对 unpin，不能只用普通 `put_page` 替代。内容被修改时需评估 `unpin_user_pages_dirty_lock` 等适用接口；普通 `get_user_pages` 引用模型则应按自身协议释放。

固定不自动创建 DMA 映射。设备使用前后仍需地址、同步和完成协议；解除用户 VMA 也不意味着外部使用已经结束。本节提供选择与清理边界，不给所有异步设备拼装通用固定模板。

原理见 [固定为何限制迁移](05-placement-and-limits.md#pinning)，源依据为 `mm/gup.c` 与同树 pin 指南。其他版本的签名可能不同，勿复制最新版本参数替换当前树。

<a id="policy-queries"></a>

## 10. 大页、策略、文件同步与资源组查询

以下是各能力的主要使用入口，不要求先阅读全部实现：

| 需求 | 入口 | 必须注意 |
| --- | --- | --- |
| 提示适用 THP 使用 | `madvise` 的 `MADV_HUGEPAGE` 等相应选项 | 提示不保证实际得到大页；检查支持与返回 |
| 显式 HugeTLB 映射 | `mmap` 的 `MAP_HUGETLB` 或 hugetlbfs 等方式 | 资源池、尺寸、对齐和权限；不假定自动小页回退 |
| 设置任务默认节点策略 | `set_mempolicy` | 任务作用域、nodemask 和继承，已有页不自动移动 |
| 设置地址范围策略 | `mbind` | 适用映射、策略和迁移标志、权限与失败处理 |
| 要求文件保存修改 | `fsync` / `fdatasync`；适用映射用 `msync` | 持久化需求、文件系统支持与错误；不将 write 返回代替同步 |
| 观察驻留与承诺 | `/proc/meminfo`、`smaps` 等 | `Committed_AS` 与 RSS、空闲量口径不同 |
| 管理 cgroup v2 用量与保护 | `memory.min/low`、`memory.high/max`、`memory.swap.max` 等文件 | 保护、限制与作用域不同，配置量不等于有效量 |
| 观察资源停顿与重访 | `/proc/pressure/memory`、相应 `memory.pressure`、`memory.stat/events` | PSI 是时间影响，计数应比较增量；见 [查询说明](#pressure-observation) |

这些能力的原理和使用模型见 [页粒度、位置与资源约束](05-placement-and-limits.md)。用户态调用应按所用库接口处理返回值与 errno；控制文件也需处理权限及不支持情况。

本轮不执行调参、配置大页池、改变节点绑定或固定页面。CMA 内部连续分配、memblock 初始化、回收入口和裸页表 helper 也不作为普通调用者的通用替代入口。

<a id="pressure-observation"></a>

## 11. 资源压力与保护的查询

先确认目标版本、启用状态、权限与实际资源组路径。这些是用户态观察入口，不是普通内核模块应直接调用的分配 API：

| 入口 | 查询什么 | 使用时的边界 |
| --- | --- | --- |
| `/proc/pressure/memory`、`io` | 系统范围的资源停顿 | some / full、平均趋势和累计时间，不是内存占用率 |
| 相应 cgroup 的 `memory.pressure` | 资源组的内存停顿 | 不与系统或父级百分比直接相加 |
| `memory.stat` 的相关 workingset 字段 | 适用重访和活跃调整计数 | 核对页数、分支和增量，不直接当精确工作集 |
| `memory.events`、适用 `memory.events.local` | high、low、OOM 等事件 | 区分层级和局部，计数不等于当前瞬时压力 |
| `memory.min/low` | 配置的保护边界 | 不是预分配量，需结合祖先和当前用量 |
| `memory.high/max`、`memory.swap.max` | 适用压力或用量限制 | 不因改变分配接口而失效 |

只读字段通常不能单独还原所有决策。修改资源控制值更会改变实际回收和任务行为，不在本轮自动执行。

含义、条件对照和采样局限见 [工作集](04-reclaim-rmap.md#working-set)、[PSI](04-reclaim-rmap.md#psi) 与 [保护关系](05-placement-and-limits.md#memory-protection)。

## 12. 使用前的最后检查

- 当前上下文是否允许所选 GFP 和接口可能进行的工作？
- 真实使用者是否允许非物理连续或动态映射？
- 失败返回值、零大小和溢出是否正确处理？
- 谁持有存储、谁持有映射、是否转移所有权？
- 结束全部访问后，是否按原协议清理？

这里选择的内核接口族在目标树有公开声明、包装和相应导出实现；不将 `__rmqueue_smallest`、`do_anonymous_page`、`rmap_walk_anon` 等内部机制入口当作外部使用 API。模块实际可用性仍受目标配置和构建上下文影响。

需要 DMA 缓冲区时沿 DMA API；需要 MMIO 时沿资源与 I/O 映射；需要用户页面映射或固定时核对专门接口、引用与权限。本页不以普通页或工作区示例替代这些契约。
