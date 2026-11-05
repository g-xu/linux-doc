---
title: 费曼学习法
---

# dma-mapping 费曼解释

这一篇不介绍 API，也不讲流程。它只挑出 `kernel/dma/` 里九个「看了代码还是想不通为什么要这样」的地方，先用生活里的例子把问题讲开，再回到源码指出那个例子在哪一步开始不成立。

每个主题四段：

1. 问题本身（标题里不夹带类比）
2. 先举个例子
3. 回到代码（带文件路径和行号）
4. 这个类比什么时候会误导你

读法建议：不需要按顺序。§6 是这个子系统里最容易被类比错的一组关系，§4 和 §2 是 `05-usage.md` 与 `07-debug.md` 反复往这里指的两处，其余可以按需要挑。

## §0 本篇挑了哪九个问题

| 节 | 问题 | 哪几篇往这里指过 |
| --- | --- | --- |
| §1 | 设备看到的地址和 CPU 看到的地址为什么不是同一个数 | `06-devicetree.md` §3.2；`03-internals.md` §B2 |
| §2 | 一致性内存为什么比普通内存慢 | `05-usage.md` §5.1、§7；`06-devicetree.md` §3.3；`07-debug.md` §11 |
| §3 | 明明内存就在那里，为什么还要先抄一份 | `03-internals.md` §B4；`04-extensions.md` §1 |
| §4 | `_for_cpu` 和 `_for_device` 为什么做的事不对称 | `05-usage.md` §3.3、§10.6；`03-internals.md` §B3；`07-debug.md` §11 |
| §5 | 中断里申请一致性内存为什么要走另一套池子 | `05-usage.md` §5.1；`04-extensions.md` §4 |
| §6 | map 之后段数为什么会变少 | `05-usage.md` §4 |
| §7 | 一个没有 `bus_type`、没有设备节点的东西为什么也算子系统 | `00-overview.md` §2 |
| §8 | CMA 为什么要「先借出去，用的时候再收回来」 | `04-extensions.md` §3 |
| §9 | cache line 比 `ARCH_DMA_MINALIGN` 大为什么要污染内核 | — |

## §1 设备看到的地址和 CPU 看到的地址为什么不是同一个数

### 先举个例子

一栋楼里，物业按「几号楼几单元几室」编门牌，快递公司按自己的分区编号编派送格口。同一户人家在两套编号里是两个不同的号码。物业手上有一张换算表：某一段门牌整体减去一个固定值，就是快递那边的格口号。

关键在于「整体减去一个固定值」这件事是**分段**的：1 单元适用一个偏移，2 单元适用另一个。所以换算不是一次减法，而是先查这个地址落在哪一段，再用那一段自己的偏移去减。

CPU 和设备的关系就是这样。CPU 用物理地址寻址，设备通过它挂的那条总线寻址；两者之间常常差一个由硬件布线决定的常量偏移，而且是分段的。

### 回到代码

那张换算表就是 `struct bus_dma_region`，定义在 `include/linux/dma-direct.h:20-25`，四个字段正好是「哪一段」加「偏移多少」：

```c
struct bus_dma_region {
	phys_addr_t	cpu_start;
	dma_addr_t	dma_start;
	u64		size;
	u64		offset;
};
```

表挂在设备上：`include/linux/device.h:599` 的 `const struct bus_dma_region *dma_range_map;`，字段注释在 `include/linux/device.h:503`。它从 DT 的 `dma-ranges` 属性来（`06-devicetree.md` §3.2 讲了怎么写）。

查表的动作在 `include/linux/dma-direct.h:27-38`，就是遍历这张表找落在哪一段：

```c
	for (m = dev->dma_range_map; m->size; m++)
		if (paddr >= m->cpu_start && paddr - m->cpu_start < m->size)
			return (dma_addr_t)paddr - m->offset;
```

反方向在 `include/linux/dma-direct.h:40-50`，条件换成用 `m->dma_start` 判段、偏移换成加。

两个方向的失败返回值不一样，这点值得记住：正向查不到返回 `DMA_MAPPING_ERROR`（`include/linux/dma-direct.h:37`），而且上面第 36 行的注释明说这样写是为了让后面的 `dma_capable()` 一定失败；反向查不到返回 `(phys_addr_t)-1`（`include/linux/dma-direct.h:49`），没有任何注释，也没有调用者检查这个值。

真正被核心调用的是外面那层包装 `phys_to_dma()`，在 `include/linux/dma-direct.h:72-75`：只有当 `dev->dma_range_map` 非空时才查表（判断在 `include/linux/dma-direct.h:61`），否则物理地址直接当设备地址用。也就是说，绝大多数设备根本没有这张表，走的是「两个编号恰好相同」这条捷径。

`include/linux/dma-direct.h:52-56` 允许体系结构自己接管 `phys_to_dma`。arm64 没有接管：`arch/arm64/include/asm/dma-direct.h` 这个文件不存在，`grep -rn "select ARCH_HAS_PHYS_TO_DMA" arch/` 的输出里只有 powerpc 和 mips。所以 arm64 用的就是上面那段通用 inline。

### 这个类比什么时候会误导你

门牌换算表是**物业在管**，任何一户都能查。内核里这张表是**挂在设备上的**（`dev->dma_range_map`），不同设备可以有不同的表，同一个物理地址在两个设备眼里可以是两个不同的设备地址。所以「一个物理地址对应一个设备地址」这个直觉是错的——必须带上「对哪个设备而言」。

第二处误导更隐蔽：换算表给人的印象是「这是个纯粹的算术变换，不改变能不能送到」。代码里不是。`include/linux/dma-direct.h:99-110` 的 `dma_capable()` 在换算之后还要再过两道：`dev->dma_mask` 和 `dev->bus_dma_limit` 取非零最小值当上界（`include/linux/dma-direct.h:110`）。换算成功不代表设备能寻址到，那是两件独立的事——`07-debug.md` §6.1 那三条 `overflow (mask %llx, bus limit %llx)` 消息全部来自这第二道门。

第三处：`phys_to_dma()` 在换算之外还做了一件与地址无关的事——`__sme_set()`（`include/linux/dma-direct.h:74`），给地址里塞内存加密位。在 arm64 上这是个恒等宏（`include/linux/mem_encrypt.h:30`，非 `CONFIG_ARCH_HAS_MEM_ENCRYPT` 分支），所以你在 arm64 上永远看不到它起作用，但它解释了为什么 `include/linux/dma-direct.h:66-71` 那段注释要专门说明「swiotlb 缓冲要用 `phys_to_dma_unencrypted`」。

## §2 一致性内存为什么比普通内存慢

### 先举个例子

办公桌上放一叠常用文件，是你的「缓存」——伸手就够到。仓库里放着全部档案，是「内存」。平时你只动桌上的副本，攒够了再一次性归档，效率很高。

现在来了个约束：仓库那边有另一个人（设备）随时会来翻同一份档案，而且他看不到你桌上的副本。为了不出错，最省心的做法是**桌上不留副本**——每次要看就跑一趟仓库，每次改完立刻送回去。正确性有了，但每次读写都变成一趟仓库。

一致性 DMA 内存就是「桌上不留副本」的那块内存。

### 回到代码

「不留副本」这件事落实在页表属性上。核心侧的入口是 `dma_pgprot()`，`kernel/dma/mapping.c:415-424`：

```c
	if (dev_is_dma_coherent(dev))
		return prot;
```

设备本身硬件一致（`dev->dma_coherent` 为真）时**原样返回**，什么都不改——这类设备的一致性内存和普通内存一样快，慢的代价根本不存在。只有非一致设备才会走到 `kernel/dma/mapping.c:423` 的 `pgprot_dmacoherent(prot)`。

arm64 的 `pgprot_dmacoherent` 在 `arch/arm64/include/asm/pgtable.h:578-580`，把内存类型改成 `MT_NORMAL_NC`（`arch/arm64/include/asm/memory.h:142`，值为 2）。这个宏上面 `arch/arm64/include/asm/pgtable.h:570-577` 有一段很值得逐字读的注释，它把「为什么不是更严格的 Device 类型」讲清楚了：Normal-NC 仍然允许推测执行、允许非对齐访问、允许写合并；Device-nGnR[nE] 才是禁止推测、保持访问宽度、要求严格对齐的那种。

也就是说 arm64 的「不留副本」不是最严格的那一档。同一个头文件里 `arch/arm64/include/asm/pgtable.h:561-562` 的 `pgprot_noncached` 用 `MT_DEVICE_nGnRnE`、`arch/arm64/include/asm/pgtable.h:565-566` 的 `pgprot_device` 用 `MT_DEVICE_nGnRE`（`arch/arm64/include/asm/memory.h:144`），DMA 一条都不用。

还有一层代价：属性只能按页设置，所以这块内存必须从线性映射里拿出来单独建一套映射。`kernel/dma/direct.c:282-295` 是这条路径，第 292 行调 `dma_common_contiguous_remap()`；实现在 `kernel/dma/remap.c:38` 起，底层是 `kernel/dma/remap.c:22-32` 的 `dma_common_pages_remap()`，一句 `vmap(..., VM_DMA_COHERENT, prot)`。`kernel/dma/remap.c:21` 和 `kernel/dma/remap.c:37` 两处注释都写着 "Cannot be used in non-sleeping contexts"——这条限制直接引出 §5。

重映射之前还有一步 `kernel/dma/direct.c:289` 的 `arch_dma_prep_coherent(page, size)`，注释说得直白：`/* remove any dirty cache lines on the kernel alias */`。桌上原来那份副本得先处理掉，否则它会在某个时刻自己写回仓库，盖掉设备写进去的数据。

`DMA_ATTR_WRITE_COMBINE` 是这条路上唯一的性能旋钮（`kernel/dma/mapping.c:419-422`），在 arm64 上换成 `pgprot_writecombine`（`arch/arm64/include/asm/pgtable.h:563-564`），内存类型也是 `MT_NORMAL_NC` ——和默认那条一样。所以在 arm64 上这个属性在页表层面不产生差别。

### 这个类比什么时候会误导你

「跑一趟仓库」暗示每次访问的代价是固定的、可预测的。Normal-NC 不是：`arch/arm64/include/asm/pgtable.h:572` 明说它允许写合并，连续的小写会被硬件攒起来一起发。所以顺序写一致性内存的实测代价可能远低于「次数 × 单次延迟」，而随机读的代价则接近全额。「慢」是有形状的，不是一个倍数。

更重要的一处：类比让人以为「一致性」是内存的属性。代码里它是**设备的**属性。`kernel/dma/mapping.c:417` 那个 `dev_is_dma_coherent(dev)` 判断的是设备，值来自 `arch/arm64/mm/dma-mapping.c:75` 的 `dev->dma_coherent = coherent;`，而 `coherent` 又来自 DT 里有没有 `dma-coherent`（见 `06-devicetree.md` §3.3）。同一块物理内存，对硬件一致的设备不需要任何特殊页表属性，对非一致设备才需要。`dma_alloc_coherent()` 这个名字里的 coherent 说的是「调用完之后你不用管一致性了」这个结果，不是「这块内存天生一致」。

第三处：类比里「桌上不留副本」是个二选一。代码里不是——`arch_dma_prep_coherent()` 在 arm64 上做的是 clean+invalidate 而不是架构要求的 clean，`arch/arm64/mm/dma-mapping.c:39-53` 那段长注释解释了原因（有厂商拿 DMA API 当非安全世界到安全世界的搬运工具用，链接在 `arch/arm64/mm/dma-mapping.c:46`），并且第 48-52 行承认这个加强只是让问题「不那么容易发生」，真正的修法在驱动侧。这是一处明确记录在案的设计债。

## §3 明明内存就在那里，为什么还要先抄一份

### 先举个例子

你要寄一件东西，快递公司规定包裹不能超过某个尺寸。你手上这件超了。办法不是让快递改规矩，而是拆成合规的包装重新装一遍——多了一次装箱，但寄得出去了。

设备的「规矩」是它的寻址位宽。一个只能寻址 32 位的设备，拿到一个落在 4 GiB 以上的缓冲区就没辙。内核的办法是在设备能寻址的范围里另找一块，把数据抄过去，让设备去访问那一块。这块「中转包装」就是 swiotlb 的 bounce buffer。

### 回到代码

抄的动作是 `swiotlb_bounce()`，`kernel/dma/swiotlb.c:551` 起。它开头几行就把这件事的本质摊开了——`kernel/dma/swiotlb.c:556` 从槽位里取出 `orig_addr`，也就是「原件在哪」：

```c
	phys_addr_t orig_addr = mem->slots[index].orig_addr;
	size_t alloc_size = mem->slots[index].alloc_size;
```

`kernel/dma/swiotlb.c:562-563` 有个提前返回：`orig_addr == INVALID_PHYS_ADDR` 就什么都不做。

两个方向都要抄，时机不同：map 的时候抄一次（`kernel/dma/swiotlb.c:824`，方向 `DMA_TO_DEVICE`），unmap 的时候抄回来一次（`kernel/dma/swiotlb.c:887`，方向 `DMA_FROM_DEVICE`）。中间如果 CPU 想看，得靠 `dma_sync_*` 触发第 896 行和第 905 行那两次。这就是为什么 `05-usage.md` 反复强调 unmap 之前不许读缓冲区：数据还在中转包装里，没抄回来。

判断要不要走这条路的地方在 `kernel/dma/direct.h:103-105`，也就是 `07-debug.md` §6.1 里那三条 overflow 消息的第一条。它是 `dev_WARN_ONCE`，出现它意味着**没有可用的 bounce 后备**——包装箱都没有，只能失败。

### 这个类比什么时候会误导你

重新装箱是你自己掏钱多买个箱子，箱子随时能买到。swiotlb 的中转区是**开机时一次性划定的固定池子**，用完就没有——池子耗尽时映射直接失败，不会临时扩容。`07-debug.md` §3.2 那几个 debugfs 文件（`io_tlb_nslabs` / `io_tlb_used`）存在的理由就是这个：它是个会耗尽的资源，需要能看水位。

第二处误导在「抄一份」这个说法上，它暗示原件和副本是等长的。`kernel/dma/swiotlb.c:565-589` 那三道越界检查说明不是：槽位有对齐要求，副本区间和原件区间之间有偏移（`kernel/dma/swiotlb.c:566` 的 `swiotlb_align_offset`），而且第三道检查在越界时会做 `size = alloc_size` 截断然后继续（`kernel/dma/swiotlb.c:589`），设备拿到的是**部分**数据。三道检查的差别 `07-debug.md` §6.2 有全表。

第三处：包装尺寸不合规是「大小问题」，很直观。而 swiotlb 在本树里还承担了一件跟大小无关的事——内存加密场景下需要一块未加密的中转区，`include/linux/dma-direct.h:66-71` 的注释和 `kernel/dma/direct.c:264-265` 那条 `force_dma_unencrypted(dev)` 判断是入口。arm64 上这条不生效（见 §1 末尾），但它解释了为什么 swiotlb 的代码里到处是加密相关的分支。

## §4 `_for_cpu` 和 `_for_device` 为什么做的事不对称

### 先举个例子

两个人共用一块白板，但各自手里还有一个笔记本。

「我写完了，轮到你看」——这时我必须把笔记本上的内容抄到白板上。这是**推**的动作，必须做。

「你写完了，轮到我看」——这时我必须把笔记本上的旧内容划掉，否则我会照着过期的旧记录办事。这是**丢弃**的动作，和上面那个不是同一件事，也不是它的逆操作。

一次交接只需要做一个方向的动作。如果我刚才只是写、根本没读过白板，那么「轮到我看」这一步我什么都不用做——笔记本上本来就没有需要作废的东西。

这就是 `dma_sync_single_for_device()` 和 `dma_sync_single_for_cpu()` 的关系：一个是 clean（把脏行推下去），一个是 invalidate（把旧行作废）。

### 回到代码

arm64 的两个实现挨在一起，一眼能看出不对称。`arch/arm64/mm/dma-mapping.c:16-22`：

```c
void arch_sync_dma_for_device(phys_addr_t paddr, size_t size,
			      enum dma_data_direction dir)
{
	unsigned long start = (unsigned long)phys_to_virt(paddr);

	dcache_clean_poc(start, start + size);
}
```

形参里有 `dir`，函数体里**一次都没用**——不管什么方向，都无条件 clean。

`arch/arm64/mm/dma-mapping.c:24-33` 就不一样了：

```c
	if (dir == DMA_TO_DEVICE)
		return;

	dcache_inval_poc(start, start + size);
```

`DMA_TO_DEVICE` 直接返回。这就是 `05-usage.md` §3.3 那条陷阱的出处——arm64 上 `dma_sync_single_for_cpu(..., DMA_TO_DEVICE)` 是空操作。

两个底层函数在 `arch/arm64/include/asm/cacheflush.h:74-76` 声明，配套说明在同一文件 `arch/arm64/include/asm/cacheflush.h:52-60`。

不对称还有第二层，在调用它们的核心 inline 里。`kernel/dma/direct.h:55-65` 的 `_for_device` 顺序是「先 swiotlb、后 arch」；`kernel/dma/direct.h:67-82` 的 `_for_cpu` 顺序是「先 arch、后 swiotlb」——**次序是反的**。想一遍就明白为什么必须反：给设备时要先把数据放进中转区（`kernel/dma/direct.h:61`），再把中转区那块内存的缓存推下去（`kernel/dma/direct.h:64`）；给 CPU 时要先作废缓存（`kernel/dma/direct.h:73`）才能读到设备写进中转区的新数据，然后才抄回原件（`kernel/dma/direct.h:78`）。

`_for_cpu` 还多两个调用：`kernel/dma/direct.h:74` 的 `arch_sync_dma_for_cpu_all()` 和 `kernel/dma/direct.h:81` 的 `arch_dma_mark_clean()`（只在 `DMA_FROM_DEVICE` 时）。两个在 arm64 上都是空的——`include/linux/dma-map-ops.h:326-328` 和 `include/linux/dma-map-ops.h:342-344` 的空 inline 分支，因为 `grep -rn "ARCH_HAS_SYNC_DMA_FOR_CPU_ALL\|ARCH_HAS_DMA_MARK_CLEAN" arch/arm64/` 没有输出（这两个 Kconfig 项本身在 `kernel/dma/Kconfig:70` 和 `kernel/dma/Kconfig:51`）。

对外的两个 API 本身是对称的，看不出差别：`kernel/dma/mapping.c:328-339` 和 `kernel/dma/mapping.c:342-353` 结构一模一样，都是「判 direct 走 inline，否则走 ops」。不对称全在下面。

### 这个类比什么时候会误导你

白板类比里，「作废旧记录」是我为自己做的，不做的后果是我读到旧数据——损失是我的。代码里不是这样：`arch_sync_dma_for_device()` 无条件 clean 意味着即使方向是 `DMA_FROM_DEVICE`（设备写、CPU 读），CPU 侧的脏行也会被推下去。这不是多余的谨慎，而是因为**缓存行比传输粒度大**：同一个缓存行里可能一半是这次 DMA 的缓冲区、另一半是别人的数据。不 clean 的话，那半行脏数据会在某个时刻自己写回，把设备刚写进来的内容盖掉——损失是别人的。这条正好通向 §9。

第二处误导：类比让人以为「交接」是两个对等动作，做错方向只是效率问题。不是。`_for_device` 用错方向在 arm64 上没有可观察后果（`dir` 根本没被读），`_for_cpu` 用错方向（写成 `DMA_TO_DEVICE`）会**静默跳过 invalidate**，你读到的是缓存里的旧数据。同一个错误在两个 API 上的后果完全不同，而且严重的那一侧是静默的。`07-debug.md` §4.3 那七条 sync 相关报告都需要先开 `DMA_API_DEBUG` 才看得到。

第三处：类比里两人共用一块白板，是个双方对等的场面。代码里 `dev_is_dma_coherent(dev)` 这道判断（`kernel/dma/direct.h:63` 和 `kernel/dma/direct.h:72`）说明这整套动作对硬件一致的设备**完全不执行**。也就是说这个类比描述的是「非一致设备」这一种情况；在硬件一致的设备上，`dma_sync_*` 只剩下 `kernel/dma/mapping.c:338` 那行 debug 记账。

## §5 中断里申请一致性内存为什么要走另一套池子

### 先举个例子

白天办事可以走正常流程：填表、排队、等审批，慢但拿得到。半夜有急事，正常窗口关着，只能去应急柜台——柜台里备了一小格常用件，拿完就没有，也不会临时补货。

中断上下文就是「半夜」。它不能睡，而正常的一致性内存申请路径中至少有两处会睡。

### 回到代码

第一处会睡的是重映射。`kernel/dma/remap.c:21` 和 `kernel/dma/remap.c:37` 两条注释都写着 "Cannot be used in non-sleeping contexts"，底层是 `kernel/dma/remap.c:29` 那个 `vmap()`。§2 已经说明非一致设备的一致性内存必须走这条路。

第二处是 CMA。`kernel/dma/contiguous.c:309-311` 开头就挡掉了：

```c
	/* CMA can be used only in the context which permits sleeping */
	if (!gfpflags_allow_blocking(gfp))
		return NULL;
```

判断条件收拢在一个函数里，`kernel/dma/direct.c:162-169`，上面的注释直接说明了它的用途：

```c
static bool dma_direct_use_pool(struct device *dev, gfp_t gfp)
{
	return !gfpflags_allow_blocking(gfp) && !is_swiotlb_for_alloc(dev);
}
```

三个调用点：`kernel/dma/direct.c:250`（非一致设备走重映射前）、`kernel/dma/direct.c:264`（内存加密场景）、`kernel/dma/direct.c:375`。

「应急柜台」就是 `kernel/dma/pool.c` 里那几个原子池，在 `atomic_pool_init()`（`kernel/dma/pool.c:187-221`）里开机预留。它们是三个而不是一个，因为设备的寻址能力不同：`kernel/dma/pool.c:202-203` 无条件建 `atomic_pool_kernel`；`kernel/dma/pool.c:207-208` 建 `atomic_pool_dma`，条件是 `kernel/dma/pool.c:206` 的 `has_managed_dma()`（运行期判断，声明在 `include/linux/mmzone.h:1364`）；`kernel/dma/pool.c:213-214` 建 `atomic_pool_dma32`，条件是 `kernel/dma/pool.c:212` 的 `IS_ENABLED(CONFIG_ZONE_DMA32)`。

「一小格」有多大：`kernel/dma/pool.c:195-199` 按内存量算，每 1 GiB 给 128 KiB，下限 128 KiB，上限 `MAX_ORDER_NR_PAGES`。也可以用 `coherent_pool=` 启动参数直接指定（`kernel/dma/pool.c:29-34`）。

### 这个类比什么时候会误导你

「应急柜台备的是常用件」暗示池子是同一种东西的备货。实际是三个池子对应三段地址范围，取哪一个取决于设备的 `dma_mask`——拿错了池子设备根本寻址不到。所以这不是「同一种资源的两个通道」，而是「三种资源的应急版本」。

第二处，也是这个类比最容易骗人的地方：它暗示「不能睡」是调用者的处境。代码里判据是**你传的 `gfp`**，不是你实际在什么上下文。`gfpflags_allow_blocking(gfp)` 看的是 `__GFP_DIRECT_RECLAIM` 位。在进程上下文里传 `GFP_ATOMIC` 一样会走原子池，把这一小格资源用掉。反过来，在真正不能睡的上下文里传了 `GFP_KERNEL`，代码不会阻止你，你会睡在 `vmap()` 里。

第三处：`kernel/dma/direct.c:168` 那个 `&& !is_swiotlb_for_alloc(dev)` 说明「不能睡」并不总是导向原子池。用 swiotlb 做分配后端的设备走另一条路，因为那块内存本来就是开机划好的、不需要任何会睡的操作。类比里没有对应物。

## §6 map 之后段数为什么会变少

### 先举个例子

寄一批零散货物，你自己列了张清单，二十件，每件一行。到了物流那边，他们看了看，把其中几件装进同一个箱子——因为它们本来就挨着放，装一个箱子完全够。最后贴出去的运单只有六张。

现在关键问题来了：**取货的时候你按几件报？** 按你那张二十行的清单报，还是按六张运单报？

答案是：**清单是你的，运单是他们的，两个都得留着，用途不同。** 想知道货物本身有多少件，看清单；想知道有几张运单要处理，看运单。销单的时候要拿你的原始清单去销，不是拿运单张数去销。

### 回到代码

两个数字并排放在 `struct sg_table` 里，`include/linux/scatterlist.h:41-42`：

```c
	unsigned int nents;		/* number of mapped entries */
	unsigned int orig_nents;	/* original size of list */
```

`dma_map_sgtable()` 把这层关系处理得最干净，`kernel/dma/mapping.c:266-277`：传进去的是 `sgt->orig_nents`（`kernel/dma/mapping.c:271`），返回值写回 `sgt->nents`（`kernel/dma/mapping.c:274`），函数本身只返回 0 或负错误码。

老一套 API 不帮你管这件事，而且内核文档在这里留了一句很直白的警告，`kernel/dma/mapping.c:224-225`：

```
 * dma_unmap_sg_attrs() should be used to unmap the buffer with the
 * original sg and original nents (not the value returned by this funciton).
```

（源码里 `funciton` 就是这么拼的，全文件唯一一处。）

合并动作发生在 IOMMU 路径。`drivers/iommu/dma-iommu.c:1060` 起的 `__finalise_sg()` 是干这件事的：`drivers/iommu/dma-iommu.c:1063` 的 `cur` 是「当前正在攒的那个箱子」，`drivers/iommu/dma-iommu.c:1065` 的 `max_len = dma_get_max_seg_size(dev)` 是箱子的容量上限。能不能塞进当前箱子的判断在 `drivers/iommu/dma-iommu.c:1102`：

```c
		    (max_len - cur_len >= s_length)) {
```

塞得进就累加长度（`drivers/iommu/dma-iommu.c:1104`），塞不进就换下一个箱子（`drivers/iommu/dma-iommu.c:1108-1109`）。没有分到运单的那些行，`sg_dma_len()` 被清成 0（`drivers/iommu/dma-iommu.c:1076`），但 `s->length` 原样保留（`drivers/iommu/dma-iommu.c:1092`）——清单那一行还在。

没有 IOMMU 时不会合并：`dma_direct_map_sg()` 一段一段映射，返回值等于传入的段数。所以「段数变少」是 IOMMU 路径的特征，不是 DMA API 的普遍行为。

`dma_unmap_sg_attrs()` 的形参名就叫 `nents`（`kernel/dma/mapping.c:280`），而你要传的是 `orig_nents`——形参名和你该传的值不一致，这是这组关系最容易踩的地方。

### 这个类比什么时候会误导你

运单类比里，「六张运单」和「二十行清单」是两份独立的纸。代码里不是——两个数字描述的是**同一个数组**，只是有效前缀长度不同。`nents` 是 6 意味着 `sgl[0..5]` 有有效的 DMA 地址，`sgl[6..19]` 的 `sg_dma_len()` 是 0；但这二十个元素从头到尾都是同一块 `struct scatterlist` 数组，`s->length` 全都有效。所以「有两份数据」是错的，是「一份数据、两个长度」。

第二处，也是把这组关系彻底想反的那个点：类比让人以为合并是物流方的优化，「客户不关心」。恰恰相反——遍历时用哪个数字**决定你读的是哪一套地址**。要拿 DMA 地址喂给设备，必须用 `nents` 配 `for_each_sg_dma_page` 那一套；要访问页面本身（比如 CPU 侧拷贝），必须用 `orig_nents`，`include/linux/scatterlist.h:178` 和 `include/linux/scatterlist.h:578` 那两个宏就是这么写的，两处都用 `(sgt)->orig_nents`。搞混了不会报错，会读到 `sg_dma_len()` 为 0 的段然后什么都不做。

第三处：类比里箱子容量是物流公司的规定，固定不变。代码里 `max_len` 来自 `dma_get_max_seg_size(dev)`，是**每个设备自己声明的**。同一个 sg 列表在两个设备上 map，`nents` 可以不同。所以 `nents` 不是这块数据的属性，是「这块数据 × 这个设备」这一次映射的结果。unmap 时必须用 `orig_nents` 的根本原因就在这里：`nents` 是本次映射的产物，而 unmap 要还的是原始那张清单。

## §7 一个没有 `bus_type`、没有设备节点的东西为什么也算子系统

### 先举个例子

小区里有物业、有快递、有水电。物业有办公室、有牌子、有编制；「用电」没有。但没人会说「用电不是小区的一个系统」——它是一套贯穿所有住户的**约定加设施**：插座标准统一、计费方式统一、出问题找谁统一。它不需要一间办公室来证明自己存在。

`kernel/dma/` 就是这种形态。它不注册任何东西，没有自己的设备类型，也不会在 sysfs 里长出一个目录。它提供的是一套所有驱动都遵守的约定，加上实现这套约定的几个后端。

### 回到代码

「没有办公室」这句话可以直接验证：`grep -rn "struct bus_type" kernel/dma/` 只有一条输出，`kernel/dma/debug.c:875` 的 `void dma_debug_add_bus(struct bus_type *bus)`——那是个**形参**，不是定义。整个子系统一个 `bus_type` 都没有定义，也没有 `struct class`、没有字符设备、没有 uapi 头文件（`00-overview.md` §2 登记了这次判定）。

那它靠什么被找到？靠 38 个导出符号（`grep -rhoE "EXPORT_SYMBOL(_GPL)?\(" kernel/dma/*.c | wc -l` → 38）和一个函数指针表。挑后端的地方在 `include/linux/dma-map-ops.h:89-94`：

```c
static inline const struct dma_map_ops *get_dma_ops(struct device *dev)
{
	if (dev->dma_ops)
		return dev->dma_ops;
	return get_arch_dma_ops(dev->bus);
}
```

`dev->dma_ops` 为空时走 direct 路径——也就是「不需要任何后端」这个最常见的情况。设置它的入口在 `include/linux/dma-map-ops.h:96-100` 的 `set_dma_ops()`，arm64 上真正调用它的是 IOMMU 侧：`arch/arm64/mm/dma-mapping.c:76-77` 里 `if (iommu)` 才调 `iommu_setup_dma_ops()`。

那句「贯穿所有住户的约定」在 `arch/arm64/mm/dma-mapping.c:75` 落成一行赋值：`dev->dma_coherent = coherent;`。每个设备被创建时都会经过 `arch_setup_dma_ops()`，从此带上自己的一致性属性——不需要注册到任何总线上。

「设施」部分在 `kernel/dma/Makefile` 里一目了然，12 行，每个 Kconfig 对着一个后端：`kernel/dma/Makefile:8` 是 `debug.o` ← `DMA_API_DEBUG`，`kernel/dma/Makefile:11` 是 `remap.o` ← `CONFIG_MMU`，`kernel/dma/Makefile:12` 是 `map_benchmark.o` ← `DMA_MAP_BENCHMARK`。

### 这个类比什么时候会误导你

「用电」是个单一约定，插座就那一种。DMA API 不是：同一个 `dma_map_page()` 调用，在无 IOMMU 的设备上走 `dma_direct_map_page()`（`kernel/dma/direct.h:84`），在有 IOMMU 的设备上走 `iommu_dma_map_page`，在需要中转的设备上走 `swiotlb_map()`（`kernel/dma/direct.h:94`）。表面一个 API，底下三条完全不同的物理路径，而且是**运行期按设备选的**。「统一插座」这个印象会让你在调试时找错地方——`07-debug.md` §0 那张分诊表第一列就是先确定你这个设备走哪条路。

第二处：类比里「用电」不需要办公室，是因为它简单。DMA API 不需要 `bus_type` 是另一个原因——它的服务对象不是「设备」而是「设备与驱动之间的一次数据传输」，这件事没有生命周期可以挂在 `struct device` 上。这个差别有一个很实际的后果：**子系统没有地方记住「这个设备还有没有未解除的映射」**。`kernel/dma/debug.c` 是唯一试图记这件事的地方，而它要记账就必须借别人的总线通知——`dma_debug_add_bus()`（`kernel/dma/debug.c:875-891`）全树只有两个调用者，其中在 arm64 上有效的只有 `drivers/pci/pci-driver.c:1744` 一个。`07-debug.md` §4.4 把这个覆盖缺口写全了。

## §8 CMA 为什么要「先借出去，用的时候再收回来」

### 先举个例子

小区规划了一块空地，将来要盖楼。空地空着几年很浪费，于是物业把它临时改成停车场，但立了牌子：**随时可能收回，届时车必须挪走**。真要动工时，物业挨个通知车主挪车，挪完开工。

如果一开始就把空地围起来谁也不许进，那几年里这块地的利用率是零。CMA 选的是「先借出去」这条路。

### 回到代码

「圈地」在开机时做：`dma_contiguous_reserve()`，`kernel/dma/contiguous.c:166`，大小可以用 `cma=` 参数指定（`kernel/dma/contiguous.c:77-96` 的 `early_cma`，第 84 行用 `memparse()` 解析）。

「改成停车场」这一步是关键，在 `mm/cma.c:120` 调用的 `init_cma_reserved_pageblock()`，实现在 `mm/page_alloc.c:2314-2330`。它做的事和「保留」正好相反——把这些页**还给了页分配器**：

```c
	set_pageblock_migratetype(page, MIGRATE_CMA);
	set_page_refcounted(page);
	__free_pages(page, pageblock_order);
```

`MIGRATE_CMA` 就是那块牌子。普通分配请求能拿到它，入口是 `mm/page_alloc.c:2600-2604` 的 `__rmqueue_cma_fallback()`。牌子的含义是「只借给可迁移的分配」，所以借出去的东西一定挪得走。

「挪车开工」在 `mm/cma.c:469-470`：

```c
		ret = alloc_contig_range(pfn, pfn + count, MIGRATE_CMA,
				     GFP_KERNEL | (no_warn ? __GFP_NOWARN : 0));
```

这一步会真的去迁移当前占着这块地的页。挪不动就重试——`mm/cma.c:478-482`，`-EBUSY` 时清掉位图重试下一块，并打一条 `pr_debug` 的 `memory range at %p is busy, retrying`。注意 `mm/cma.c:460-464` 那段注释：位图先置位再放锁，理由是「已经标记为我们独占，迁移失败再回来取消标记」。

DMA 侧的入口是 `dma_alloc_contiguous()`（`kernel/dma/contiguous.c:303-333`），归还是 `dma_free_contiguous()`（`kernel/dma/contiguous.c:346`）。选哪块 CMA 区域有优先级：`kernel/dma/contiguous.c:312-313` 先看设备自己的 `dev->cma_area`，`kernel/dma/contiguous.c:314-315` 小于一页直接不用 CMA，`kernel/dma/contiguous.c:317-328` 是 per-NUMA 区域，最后 `kernel/dma/contiguous.c:329-332` 落到默认区域。

### 这个类比什么时候会误导你

停车场类比里，收回空地是**一定成功**的，只是慢——物业有权强制挪车。CMA 不是：`alloc_contig_range()` 会失败，`mm/cma.c:478` 那个 `if (ret != -EBUSY) break;` 说明有些失败连重试都不给。某个页被长期 pin 住（比如被 `get_user_pages()` 拿着）就是挪不动。所以「CMA 保证能拿到大块连续内存」这个说法是错的，它只是**大幅提高**了概率。`07-debug.md` §3.4 那几个 CMA debugfs 文件存在的理由就是这个。

第二处，也是这个类比最反直觉的一点：「先借出去」不是妥协，它有一个类比完全给不出的副作用——因为借出去的必须是可迁移的分配，CMA 区域**天然排斥不可迁移的内容**。这不是「利用率换成功率」的权衡，而是牌子本身就把风险最高的租客挡在门外了。`mm/page_alloc.c:2313` 那行注释和 `MIGRATE_CMA` 这个名字说的是同一件事。

第三处：类比里空地只有一块。代码里可以有很多块，而且**归属不同**——全局默认区域、per-device 区域（`dev->cma_area`）、per-NUMA 区域，`kernel/dma/contiguous.c:312-332` 那四段判断就是在这几种之间挑。一个设备申请失败不代表系统没有 CMA 内存，可能只是它自己那块用完了。

## §9 cache line 比 `ARCH_DMA_MINALIGN` 大为什么要污染内核

### 先举个例子

搬家公司规定最小搬运单位是一整个纸箱。你要托运的是箱子里的一件衣服，但他们只能整箱搬。如果这个箱子里还装了别人的东西，那么「只搬你的衣服」这件事在物理上做不到——你的操作必然影响到同箱的其他物品。

缓存维护就是这样：CPU 只能按缓存行为单位 clean 或 invalidate。DMA 缓冲区如果和别的数据共享同一个缓存行，那么为 DMA 做的缓存操作会连带作用在别人的数据上。

内核的应对是给分配器设一个下限：DMA 用的内存至少按这么大对齐，这样就不会和别人共享缓存行。这个下限就是 `ARCH_DMA_MINALIGN`。

### 回到代码

arm64 把它定成 128 字节，`arch/arm64/include/asm/cache.h:26`：

```c
#define ARCH_DMA_MINALIGN	(128)
```

这是个**编译期常量**。而真实缓存行宽度是运行期才知道的，读的是 `CTR_EL0.CWG`，封装在 `arch/arm64/include/asm/cache.h:75` 的 `cache_line_size_of_cpu()`。

于是就有了一个编译期承诺对不上运行期硬件的可能，检查在 `arch/arm64/mm/dma-mapping.c:64-73`：

```c
	int cls = cache_line_size_of_cpu();

	WARN_TAINT(!coherent && cls > ARCH_DMA_MINALIGN,
		   TAINT_CPU_OUT_OF_SPEC,
		   "%s %s: ARCH_DMA_MINALIGN smaller than CTR_EL0.CWG (%d < %d)",
		   dev_driver_string(dev), dev_name(dev),
		   ARCH_DMA_MINALIGN, cls);
```

三件事值得注意。一是条件里带 `!coherent`——硬件一致的设备不做任何缓存维护，共享缓存行也不出问题，所以不报。二是它用 `WARN_TAINT` 而不是 `WARN`，污染标志是 `TAINT_CPU_OUT_OF_SPEC`：内核在说「这不是我的 bug，是这颗 CPU 超出了我编译时的假设」。三是它在 `arch_setup_dma_ops()` 里，也就是**每个设备**建立 DMA 能力时都会过一次，所以消息里带了设备名。

`07-debug.md` §6.3 把它收在核心层 `WARN` 那张表里。

### 这个类比什么时候会误导你

纸箱类比里，只要每个人各用一箱，问题就彻底解决了。内核里没有这么干净：`ARCH_DMA_MINALIGN` 只约束**分配器给出的地址**，它管不到你自己算出来的偏移。你用 `kmalloc()` 拿到一块对齐的内存，然后从中间某个偏移开始映射一小段，共享缓存行又回来了——这也是 `05-usage.md` §3 一直强调「一个缓冲区在 map 期间整体归设备」的原因。

第二处：类比暗示这个警告是给你看的、你能修。基本不能。`TAINT_CPU_OUT_OF_SPEC` 的意思是问题出在编译期常量和这颗芯片的组合上，修法是改内核编译配置或者换芯片，不是改驱动。这条警告的实际用途是**解释别处的怪现象**——看到它之后，任何非一致设备上的偶发数据损坏都要先怀疑它，而不是继续查驱动逻辑。

第三处，也是最容易漏的：警告只在 `cls > ARCH_DMA_MINALIGN` 时打。反过来（真实缓存行比 128 小）不打也不需要打，但这意味着 arm64 上绝大多数机器这条永远静默。「没看到这条警告」不等于「对齐没问题」，只等于「这颗 CPU 的 CWG 不大于 128」。

## 阅读下一篇

| 想继续了解 | 去哪篇 |
| --- | --- |
| 这些概念对应哪些函数、怎么调 | `01-api.md` |
| `struct sg_table` / `struct io_tlb_mem` 的字段全貌 | `02-datastructures.md` |
| 映射一次到底走过哪些函数 | `03-internals.md` |
| swiotlb / CMA / 原子池 / debug 四个后端各自的边界 | `04-extensions.md` |
| 照着写驱动代码 | `05-usage.md` |
| `dma-ranges` / `dma-coherent` / `memory-region` 怎么写 | `06-devicetree.md` |
| 出了问题去看哪个输出 | `07-debug.md` |
| 本篇每条断言的出处、哪些没验证 | `99-evidence.md` |
