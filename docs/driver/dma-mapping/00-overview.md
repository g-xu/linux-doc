---
title: 总览
---

# dma-mapping 总览

这一篇的定位是**复习入口**。隔几个月回来，只读这一篇就能恢复对 `kernel/dma/` 的整体认识，然后按 §8 的导读跳到需要的那一篇。它不重复其余九篇的细节，只把骨架和判断依据收在一处。

基线：`f6e38ae624cf` / `Linux 6.1.158`。本树打了补丁，但 `git status --porcelain -- kernel/dma include/linux/dma-mapping.h include/linux/dma-map-ops.h include/linux/dma-direct.h include/linux/swiotlb.h` 输出为空——**这个子系统一行没被改过**，所以全套文档描述的就是上游 6.1.158 的原样。架构范围：arm64 only（裁掉了什么见 `99-evidence.md` §2.7）。

## §1 这个子系统是什么，边界在哪

一句话：**它是「驱动想让设备访问一块内存」这件事的唯一合法入口**，负责把 CPU 视角的内存变成设备能安全访问的内存，并在传输前后维护两者看到的数据一致。

### §1.1 目录边界

| 在本子系统内 | 不在，但紧邻 |
| --- | --- |
| `kernel/dma/` 全部 11 个 `.c` + 2 个 `.h` | `drivers/dma/`（DMAEngine，是**另一个**子系统，做的是「控制一个 DMA 控制器搬数据」） |
| `include/linux/dma-mapping.h`（对驱动的 API 面） | `drivers/iommu/dma-iommu.c`（IOMMU 后端实现，本子系统只定义它要填的 ops） |
| `include/linux/dma-map-ops.h`（对后端的契约面） | `mm/cma.c` / `mm/cma_debug.c`（CMA 的核心算法，本子系统只是它的一个使用者） |
| `include/linux/dma-direct.h`、`include/linux/swiotlb.h` | `arch/arm64/mm/dma-mapping.c`（4 个体系结构钩子的 arm64 实现） |
| `include/linux/map_benchmark.h` | `drivers/dma-buf/`（缓冲区共享，另一个子系统） |

规模：`wc -l kernel/dma/*.c kernel/dma/*.h` → 6182 行，最大的三个是 `debug.c` 1608、`swiotlb.c` 1112、`mapping.c` 831；最小的 `dummy.c` 只有 38 行。头文件侧 `dma-mapping.h` 611 行、`dma-map-ops.h` 449 行。`kernel/dma/Kconfig` 有 37 个 `config` 项（`grep -c "^config " kernel/dma/Kconfig`），但绝大多数是给体系结构 `select` 的能力位，不是给用户选的。

对外导出 38 个符号（`grep -rhoE "EXPORT_SYMBOL(_GPL)?\(" kernel/dma/*.c | wc -l`）。完整清单在 `01-api.md` §0。

### §1.2 类型判定：框架型

按 skill 的两道门跑一遍：

| 门 | 命令 | 输出 | 结论 |
| --- | --- | --- | --- |
| 门 1：有没有自己的 `bus_type` | `grep -rn "struct bus_type" kernel/dma/` | 只有 `kernel/dma/debug.c:875` 一条，且是**形参**（`void dma_debug_add_bus(struct bus_type *bus)`），不是定义 | 无 |
| 门 2（门 1 无输出，本可跳过，仍跑一遍确认） | `ls include/uapi/linux/dma*` | 无 DMA 映射相关的 uapi 头文件 | 无跨边界 ABI |

结论：**框架型**。没有总线、没有 `struct class`、没有字符设备、没有 sysfs 目录。它靠 38 个导出符号和一张函数指针表（`struct dma_map_ops`）被消费。这个判定的后果贯穿全套文档：`02-datastructures.md` 画的是「核心抽象 ↔ 消费者」而不是两条总线；`06-devicetree.md` 的分诊表里 DT 只占一部分；`08-feynman.md` §7 专门解释了「没有总线为什么也算子系统」。

唯一一个例外值得记住：`include/linux/map_benchmark.h` 定义了一个 ioctl 号（`include/linux/map_benchmark.h:9`），却**没有**放在 `include/uapi/linux/` 下，而它的消费者是 `tools/testing/selftests/dma/dma_map_benchmark.c`。这是本子系统唯一的用户态接口，细节见 `04-extensions.md` §7。

## §2 背景：内核为什么需要这一层

这一节走「问题背景」路径——本子系统不对应任何一种硬件规范，它存在的理由是五个彼此独立的硬件事实，任何一个都足以让「直接把指针给设备」这件事出错。

### §2.0 先看那个朴素的想法

驱动想让设备把一段数据写进内存，最直接的写法是：

```c
	void *buf = kmalloc(4096, GFP_KERNEL);
	writel(virt_to_phys(buf), regs + DMA_ADDR);   /* 这行是错的 */
	writel(4096, regs + DMA_LEN);
	writel(START, regs + DMA_CTRL);
	/* ... 等中断 ... */
	process(buf);                                  /* 这行也是错的 */
```

这段代码在某些板子上能跑通，这正是它危险的原因。它对硬件做了五个假设，每一个都可能不成立：

1. CPU 写进 `buf` 的数据此刻已经在内存里，设备读得到（缓存假设）
2. `virt_to_phys(buf)` 这个数就是设备该往寄存器里写的地址（地址假设）
3. 这个地址落在设备能寻址的范围内（位宽假设）
4. 这 4096 字节在物理上是连续的（连续性假设）
5. 设备发出的地址会原样到达内存控制器（无 IOMMU 假设）

下面五节各拆一个。每一节的形式相同：硬件事实是什么、不管会怎样、本子系统提供的对应机制是什么、arm64 上这条的实际状态。

### §2.1 问题一：CPU 有缓存，设备通常不看缓存

**硬件事实。** CPU 访存要过缓存。`buf[0] = 0x55` 这句话执行完，`0x55` 可能只在 L1 里，内存里还是旧值。反过来，设备写进内存的新值，CPU 可能读不到——它命中了缓存里的旧行。有些互连能让设备的访问也参与缓存一致性协议（cache-coherent 设备），有些不能。

**不管会怎样。** 两个方向各有一种损坏。CPU→设备方向：设备读到旧数据，而且**不报错**，它就是老老实实把旧数据发出去了。设备→CPU 方向：CPU 读到旧数据，同样不报错。两种都是静默的数据损坏，而且和时序相关——同一段代码在负载不同时表现不同。

还有一种更隐蔽的：CPU 缓存里某一行**一半是 DMA 缓冲区、一半是别人的数据**，那半行的脏数据会在某个不确定的时刻自己写回内存，把设备刚写进来的内容盖掉。这一条不是「你的数据错了」，而是「你的 DMA 弄坏了别人的数据」，排查难度高一个量级。

**对应机制。** 两套。

一是「分配一块不用操心缓存的内存」——`dma_alloc_coherent()`。它的做法是把这块内存从线性映射里摘出来，用不带缓存的页表属性另建一套映射。代价是 CPU 访问变慢（`08-feynman.md` §2 讲了慢在哪、慢多少取决于什么）。

二是「继续用普通内存，但在交接点做缓存维护」——`dma_map_*()` / `dma_unmap_*()` / `dma_sync_*()`。这一套快，但要求驱动严格遵守所有权约定：映射期间这块内存归设备，CPU 不许碰。

第三条路是硬件替你解决：设备本身参与缓存一致性协议时，上面两套机制里的缓存维护动作全部退化成空操作。判据是 `dev->dma_coherent`，来自 DT 里有没有 `dma-coherent` 属性（`06-devicetree.md` §3.3）。

**arm64 状态。** 两种设备都常见。SoC 内部的外设经常是 `dma-coherent` 的，挂在外部总线上的常常不是。缓存维护的三个钩子实现在 `arch/arm64/mm/dma-mapping.c`，只有 80 行，全部读完不费时间——而且这三个钩子彼此不对称，`08-feynman.md` §4 专门讲这个不对称。

「半行共享」那个问题在 arm64 上由 `ARCH_DMA_MINALIGN`（`arch/arm64/include/asm/cache.h:26`，值 128）兜底，兜不住的时候内核会污染自己并打一条 `WARN_TAINT`（`arch/arm64/mm/dma-mapping.c:69-73`），来龙去脉见 `08-feynman.md` §9。

### §2.2 问题二：设备看到的地址不是 CPU 看到的地址

**硬件事实。** CPU 用物理地址寻址；设备通过它挂的那条总线寻址。两者之间可能差一个由布线决定的常量偏移，而且这个偏移是**分段**的——不同地址区间偏移不同。

**不管会怎样。** 设备访问到错误的物理地址。运气好是访问不存在的地址、总线报错；运气不好是访问了别人的内存，静默损坏。

**对应机制。** `dma_map_*()` 的返回值是 `dma_addr_t`，这是一个**独立的类型**，专门表示「设备该往寄存器里写的那个数」。它和 `phys_addr_t` 在数值上可能相等，但语义上不是一回事，混用两者是本子系统最经典的错误。翻译表是 `struct bus_dma_region`（`include/linux/dma-direct.h:20-25`），挂在 `dev->dma_range_map`（`include/linux/device.h:599`），从 DT 的 `dma-ranges` 属性来。

**arm64 状态。** 绝大多数板子上这层是恒等映射——`grep -rl "dma-ranges" arch/arm64/boot/dts/ | wc -l` 只有 52 个文件用到（统计口径见 `06-devicetree.md` §1）。这意味着**一个混用了 `virt_to_phys()` 和 `dma_map_*()` 的驱动，在大多数板子上测不出问题**，换到带 `dma-ranges` 的板子上立刻坏。这类 bug 的典型症状是「同一份驱动在 A 板好、B 板坏」。

### §2.3 问题三：设备的寻址位宽可能不够

**硬件事实。** 一个设备的地址总线可能只有 32 位、24 位甚至更窄。系统内存却可能远超它能表示的范围。

**不管会怎样。** 设备拿到一个它表示不了的地址，高位被截断，访问落到一个完全无关的位置。同样是静默的。

**对应机制。** 三件事。第一，设备必须声明自己的能力——`dma_set_mask_and_coherent()`，写进 `dev->dma_mask`。第二，分配一致性内存时按这个 mask 挑内存区（`ZONE_DMA` / `ZONE_DMA32` / 普通区）。第三，映射一块已有内存时，如果它落在范围外，用 swiotlb 在设备能寻址的范围内另找一块中转，把数据抄过去——`08-feynman.md` §3 讲这件事。

还有一个容易忽略的第四层：`dev->bus_dma_limit`。设备自己能寻址 64 位，但它挂的那条总线可能只有 32 位。两个限制取更严的那个，判断在 `include/linux/dma-direct.h:110` 的 `min_not_zero(*dev->dma_mask, dev->bus_dma_limit)`。⚠️ **`min_not_zero` 意味着 0 被当作「没设」而不是「限制为 0」**，所以 `bus_dma_limit = 0` 表示无限制。

**arm64 状态。** `ZONE_DMA` 和 `ZONE_DMA32` 在 arm64 上都默认开（`mm/Kconfig:978` 和 `mm/Kconfig:983` 两处 `default y if ARM64`），swiotlb 默认也在。所以三层机制齐全。落到诊断上，`07-debug.md` §6.1 那三条含「overflow (mask %llx, bus limit %llx)」的消息就是这一层报警的出口——三条文本几乎一样，但对应的修法完全不同。

### §2.4 问题四：设备可能要求物理连续，而内存越用越碎

**硬件事实。** 一个不带散列-聚集能力的设备只能接受「一个起始地址 + 一个长度」。要给它 4 MiB，就得有 4 MiB 物理连续的内存。而系统跑久了物理内存会碎片化，页分配器能给出的最大连续块有硬上限（`MAX_ORDER` 决定，通常是 4 MiB 量级），并且随着运行时间推移越来越难拿到。

**不管会怎样。** 分配失败。这一条是五个问题里唯一**不会静默出错**的——它老老实实返回 `NULL`。但它的失败模式很讨厌：开机时能成功、跑几小时后同样的请求失败。一个只在开机后立刻测过的驱动会漏掉这个问题。

**对应机制。** 两条，方向相反。

一是 CMA（连续内存分配器）：开机时圈一块地，平时借给可迁移的普通分配用，需要连续内存时把租客迁走再收回来。`08-feynman.md` §8 讲了这个「借出去再收回来」的设计，`04-extensions.md` §3 讲边界。它把「拿到大块连续内存」的成功率大幅提高，但**不保证成功**——某个页被长期 pin 住就迁不走。

二是绕开这个需求：`dma_alloc_noncontiguous()` 一族（`kernel/dma/mapping.c` 里的四个导出符号 `dma_alloc_noncontiguous` / `dma_free_noncontiguous` / `dma_vmap_noncontiguous` / `dma_vunmap_noncontiguous`）。设备后面有 IOMMU 时，物理上不连续的页可以被映成设备侧连续的一段——需求本身消失了。

**arm64 状态。** CMA 是常态，`cma=` 启动参数（`kernel/dma/contiguous.c:77-96`）在实际板子上很常见。per-device 的 CMA 区域可以通过 DT 的 `memory-region` 指定，但 `shared-dma-pool` 到底变成 CMA 区还是普通一致性池取决于有没有 `reusable`，决策树在 `06-devicetree.md` §4.3。诊断出口是 `/sys/kernel/debug/cma/`（`07-debug.md` §3.4）。

### §2.5 问题五：中间可能插了一个 IOMMU

**硬件事实。** 设备发出的地址不一定直达内存控制器。arm64 上常有 SMMU 挡在中间，它按页表把设备发出的 IOVA 翻译成物理地址；没有对应映射的访问会被拦下来并报故障。

**不管会怎样。** 有 IOMMU 而不建映射，访问直接被拦，设备侧超时或收到 abort。这一条**不静默**，但错误消息来自 IOMMU 驱动，跟你的驱动看起来没关系，容易查错方向。

**对应机制。** 这是本子系统设计上最漂亮的一处：驱动**完全不需要知道有没有 IOMMU**。`dma_map_page()` 这个调用在有 IOMMU 的设备上会去建 IOVA 映射并返回 IOVA，在没有的设备上直接算出物理地址返回。选哪条路的地方在 `include/linux/dma-map-ops.h:89-94` 的 `get_dma_ops()`：`dev->dma_ops` 非空就走它，否则走 direct 路径。设置 `dev->dma_ops` 的时机在 `arch/arm64/mm/dma-mapping.c:76-77`——`if (iommu)` 才调 `iommu_setup_dma_ops()`。

有 IOMMU 还带来一个副作用，恰好也是驱动看得见的：sg 列表被 map 之后**段数会变少**，因为相邻的段可以合并成设备侧连续的一段。这是 `nents` 与 `orig_nents` 这组关系的来源，也是本子系统最容易被想反的一组关系（`08-feynman.md` §6）。

**arm64 状态。** 两种都常见，同一块板子上不同设备可能不同。这意味着**同一个 `dma_map_sg()` 调用在两个设备上返回的段数可以不同**，任何依赖「返回值 == 传入值」的驱动代码都是错的。

### §2.6 五个问题合起来的形状

把五节的机制并排放，会看到一个很规整的结构：**一套 API 面，三条后端路径，四个体系结构钩子**。

```
                驱动只看这一层
   ┌────────────────────────────────────────────────┐
   │  dma_alloc_coherent / dma_map_* / dma_sync_*   │  include/linux/dma-mapping.h
   └───────────────────────┬────────────────────────┘
                           │  get_dma_ops(dev)
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
   dev->dma_ops       dev->dma_ops       dev->dma_ops
     == NULL           == iommu           == dummy
        │                  │                  │
   direct 路径        IOMMU 路径          全部失败
   kernel/dma/direct.*  drivers/iommu/    kernel/dma/dummy.c
        │              dma-iommu.c
        │
   ┌────┴─────┬──────────┬──────────┐
   ▼          ▼          ▼          ▼
 普通页    swiotlb      CMA       原子池
         （中转）   （连续）  （不能睡时）
        │
        ▼  非一致设备才走
   四个体系结构钩子  arch/arm64/mm/dma-mapping.c
   arch_sync_dma_for_device / arch_sync_dma_for_cpu
   arch_dma_prep_coherent / arch_setup_dma_ops
```

这张图解释了本子系统所有「看起来重复」的东西。比如为什么有 `dma_alloc_coherent()` 又有 `dma_map_single()`——前者解决问题一，后者解决问题一到问题五但把一致性责任交回给驱动。又比如为什么 swiotlb、CMA、原子池三个东西都在 direct 路径下面而不是并列的后端——它们不是「翻译地址的三种方式」，而是「同一条 direct 路径上按需插入的三种内存来源」。

### §2.7 arm64 上五个问题的实际状态

| 问题 | arm64 上是否真实存在 | 判据 / 怎么确认这块板子 |
| --- | --- | --- |
| 一、缓存不一致 | 两种设备都有 | DT 节点有没有 `dma-coherent`；运行期看 `dev->dma_coherent` |
| 二、地址翻译 | 少数板子有 | `grep -rl "dma-ranges" arch/arm64/boot/dts/ \| wc -l` → 52 个文件 |
| 三、寻址位宽不够 | 常见 | 驱动自己声明的 mask；`ZONE_DMA` / `ZONE_DMA32` 都默认开 |
| 四、要求物理连续 | 常见 | 设备有没有散列-聚集能力，这是硬件属性，代码里看不到 |
| 五、有 IOMMU | 两种都常见 | DT 节点有没有 `iommus` 属性；运行期看 `dev->dma_ops` 是否非空 |

这张表的用法：拿到一个 DMA 相关的 bug，先按这五行确认你这块板子上哪几条是「真实存在」的，再去 `07-debug.md` §0 挑工具。五条里只要有一条不存在，对应的一整类 bug 就可以从怀疑列表里划掉。

### §2.8 这一层不解决什么

划清边界比列出功能更能防止找错地方。以下四件事**不是**本子系统的职责：

- **让设备真正开始搬数据。** 本子系统只负责准备内存并给出一个地址。把地址写进设备寄存器、启动传输、等中断，全是驱动自己的事。如果搬数据这件事是由一个独立的 DMA 控制器完成的，那是 DMAEngine（`drivers/dma/`），另一个子系统。
- **跨进程 / 跨驱动共享缓冲区。** 那是 dma-buf（`drivers/dma-buf/`）。
- **保证映射被解除。** 本子系统没有任何机制在驱动 unbind 时自动清理未解除的映射。`kernel/dma/debug.c` 能**报告**这种泄漏，但 ⚠️ 在 arm64 上这个报告只覆盖 PCI 设备（原因见 `07-debug.md` §4.4），platform / spi / i2c 设备的映射泄漏永远不会被报出来。
- **检查驱动有没有遵守所有权约定。** 默认配置下完全不检查。要检查得开 `CONFIG_DMA_API_DEBUG`，而且开了之后还有四道门要过（`07-debug.md` §2.1），其中「默认只报一条」那道门最容易让人误以为没问题。

## §3 概念模型：三组必须先分清的东西

`02-datastructures.md` §1.0 用六组关系把这件事做全了，这里只留最核心的三组，作为复习锚点。

### §3.1 三种地址

| 名字 | 类型 | 谁用 | 怎么得到 |
| --- | --- | --- | --- |
| CPU 虚拟地址 | `void *` | CPU 解引用 | `kmalloc()` / `dma_alloc_coherent()` 的返回值 |
| CPU 物理地址 | `phys_addr_t` | 内核内部记账 | `virt_to_phys()`、`page_to_phys()` |
| DMA 地址 | `dma_addr_t` | ⚠️ **只能写进设备寄存器** | `dma_map_*()` 的返回值、`dma_alloc_coherent()` 的第三个参数 |

三者在很多板子上数值相等，这是所有混用错误的温床。判断依据只有一条：**这个数将来要给谁看**。给设备看就必须是 `dma_addr_t`，而且必须来自 `dma_*` 系列函数的输出，不能自己算。

### §3.2 三条拿到缓冲区的路

| 路 | 入口 | 谁负责一致性 | 典型用途 |
| --- | --- | --- | --- |
| 一致性分配 | `dma_alloc_coherent()` | 子系统（一次性解决） | 描述符环、寄存器影子、长期存在的小块 |
| 流式映射 | `dma_map_single()` / `dma_map_sg()` | ⚠️ **驱动**（每次传输都要管） | 一次性的数据包、来自别处的缓冲区 |
| 页分配 | `dma_alloc_pages()` / `dma_alloc_noncontiguous()` | 驱动，但拿到的是页而不是虚拟地址 | 需要自己控制映射方式的场合 |

选错不会立刻报错。最常见的错法是「用流式映射的方式对待一致性内存」——多调了几次 `dma_sync_*`，只是浪费；反过来「用一致性内存的方式对待流式映射」——漏调 `dma_sync_*`，是静默的数据损坏。

### §3.3 所有权：映射期间这块内存不是你的

流式映射的全部约定可以压成一句话：**`dma_map_*()` 返回之后到 `dma_unmap_*()` 返回之前，这块内存归设备，CPU 不许碰。** 中间要碰就必须用 `dma_sync_*()` 显式借回来、还回去。

这条约定没有任何强制手段。默认配置下违反它既不会 `WARN` 也不会崩，只会在某些时序下数据不对。这是本子系统与内核其他子系统最大的气质差异——大部分子系统的误用会撞上 `WARN_ON` 或 `BUG_ON`，这里的误用是安静的。

关系图（完整版在 `02-datastructures.md` §8.1）：

```
   驱动
    │  dma_set_mask_and_coherent()          → dev->dma_mask / coherent_dma_mask
    │  dma_alloc_coherent()  ──┐
    │  dma_map_single()      ──┤
    │  dma_sync_single_*()   ──┼──→ kernel/dma/mapping.c（薄壳：判 direct / 记账）
    │  dma_unmap_single()    ──┤          │
    │  dma_free_coherent()   ──┘          │
    ▼                                     ▼
  设备寄存器                    dma_map_ops 或 direct 内联
   （写 dma_addr_t）                       │
                                          ▼
                              内存来源 + 体系结构缓存维护
```

## §4 数据结构速览

只列骨架，字段级别看 `02-datastructures.md`。

| 结构 | 在哪 | 一句话 | 详见 |
| --- | --- | --- | --- |
| `struct device` 的 DMA 字段 | `include/linux/device.h` | `dma_mask` / `coherent_dma_mask` / `dma_ops` / `dma_coherent` / `dma_range_map` / `cma_area` / `bus_dma_limit` 等，本子系统的全部 per-device 状态都寄居在这里 | 02 §2 |
| `struct dma_map_ops` | `include/linux/dma-map-ops.h` | 25 个成员，本子系统**唯一**的可插拔接口 | 02 §3 |
| `struct bus_dma_region` | `include/linux/dma-direct.h:20-25` | 地址翻译表的一行 | 02 §4 |
| `struct scatterlist` / `struct sg_table` | `include/linux/scatterlist.h` | 分散缓冲区；⚠️ `nents` 与 `orig_nents` 是本子系统最容易想反的一组 | 02 §5 |
| `struct io_tlb_mem` / `io_tlb_slot` | `include/linux/swiotlb.h` | swiotlb 中转区的账本 | 02 §6.2 |
| `struct dma_coherent_mem` | `kernel/dma/coherent.c` | per-device 一致性池 | 02 §6.1 |
| `struct dma_debug_entry` | `kernel/dma/debug.c` | debug 层的账本条目，默认预留 65536 个 | 02 §7 |
| `struct device_dma_parameters` | `include/linux/device.h` | 段大小上限、段边界，影响 IOMMU 合并结果 | 02 §2.1 |

⚠️ 有五个东西名字里都带 pool，互不相同（per-device 一致性池 / 全局一致性池 / 原子池 / swiotlb 池 / 受限 DMA 池）。对照表在 `02-datastructures.md` §1.0.4——这是查资料时最容易串台的一处。

## §5 机制速览

| 机制 | 一句话 | 入口 | 详见 |
| --- | --- | --- | --- |
| 后端选择 | `dev->dma_ops` 非空走 ops，否则走 direct 内联 | `get_dma_ops()`，`include/linux/dma-map-ops.h:89-94` | 03 §B1 |
| 地址翻译 | 三层，只有中间一层归本子系统 | `phys_to_dma()`，`include/linux/dma-direct.h:72-75` | 03 §B2 |
| 缓存维护 | 四个体系结构钩子，彼此不对称 | `arch/arm64/mm/dma-mapping.c` | 03 §B3、08 §4 |
| bounce 决策 | 三个入口、两套语义 | `kernel/dma/direct.h:91-105` | 03 §B4、08 §3 |
| 一致性内存的分配决策树 | 一致 / 非一致 × 能睡 / 不能睡 × 有无 CMA，六个分支 | `dma_direct_alloc()`，`kernel/dma/direct.c` | 03 §A5 |
| 原子池 | 不能睡时的一致性内存来源，三个池对应三段地址范围 | `atomic_pool_init()`，`kernel/dma/pool.c:187-221` | 04 §4、08 §5 |
| CMA | 借出去再收回来 | `dma_alloc_contiguous()`，`kernel/dma/contiguous.c:303-333` | 04 §3、08 §8 |
| debug 记账 | 每次 map 记一条，unmap 时核销 | `kernel/dma/debug.c` | 04 §6、07 §2 |
| 设备无 DMA 能力 | 一套全部失败的 ops，38 行 | `kernel/dma/dummy.c` | 04 §8 |

## §6 端到端调用路径

两条最值得记住的路径，函数级细节在 `03-internals.md` Part A。

### §6.1 `dma_alloc_coherent()`（一致性分配）

```
dma_alloc_coherent()                       include/linux/dma-mapping.h（inline）
 └─ dma_alloc_attrs()                      kernel/dma/mapping.c:519 附近，EXPORT
     ├─ dma_alloc_from_dev_coherent()      per-device 池优先，kernel/dma/coherent.c
     ├─ [dev->dma_ops == NULL] dma_direct_alloc()      kernel/dma/direct.c
     │   ├─ 一致设备 + 能睡 → __dma_direct_alloc_pages() → 直接返回线性地址
     │   ├─ 非一致 + 不能睡 → dma_direct_alloc_from_pool()   kernel/dma/direct.c:250-252
     │   └─ 非一致 + 能睡   → arch_dma_prep_coherent()       kernel/dma/direct.c:289
     │                       + dma_common_contiguous_remap() kernel/dma/direct.c:292
     └─ [否则] ops->alloc()                drivers/iommu/dma-iommu.c
```

关键分叉全在 `dma_direct_alloc()` 里，`03-internals.md` §A5 把六个分支画成决策树。

### §6.2 `dma_map_single()`（流式映射）

```
dma_map_single()                           include/linux/dma-mapping.h（inline，不可 trace）
 └─ dma_map_page_attrs()                   kernel/dma/mapping.c:143，EXPORT at :165
     ├─ BUG_ON(!valid_dma_direction(dir))
     ├─ [dev->dma_ops == NULL] dma_direct_map_page()   kernel/dma/direct.h:84
     │   ├─ is_swiotlb_force_bounce() → swiotlb_map()  kernel/dma/direct.h:91-94
     │   ├─ !dma_capable() → swiotlb 或失败            kernel/dma/direct.h:97-105
     │   └─ 非一致设备 → arch_sync_dma_for_device()     kernel/dma/direct.h:110
     ├─ [否则] ops->map_page()
     └─ debug_dma_map_page()               只在 CONFIG_DMA_API_DEBUG 下有内容
```

⚠️ **`dma_map_single()` 本身是 `static inline`，ftrace 抓不到。** 要下断点或加 tracepoint 得盯 `dma_map_page_attrs`（`kernel/dma/mapping.c:143`）。这一条在 `07-debug.md` §5 展开。

## §7 与相邻子系统的关系

| 子系统 | 关系 | 边界在哪一行 |
| --- | --- | --- |
| driver core（`drivers/base/`） | 提供 `struct device`，本子系统的所有 per-device 状态寄居其中；设备创建时调用 `of_dma_configure_id()` 完成 DMA 属性初始化 | `drivers/of/device.c:114-223` |
| `drivers/iommu/` | 本子系统定义 `dma_map_ops` 契约，IOMMU 侧填它 | `arch/arm64/mm/dma-mapping.c:77` 的 `iommu_setup_dma_ops()` |
| `mm/`（CMA、ZONE_DMA、页分配器） | 本子系统是使用者，不是实现者 | `kernel/dma/contiguous.c:313` 的 `cma_alloc_aligned()` |
| `arch/arm64/mm/` | 提供四个缓存维护与初始化钩子 | `arch/arm64/mm/dma-mapping.c`，全文 80 行 |
| DMAEngine（`drivers/dma/`） | ⚠️ **完全无关的另一个子系统**，只是名字像。它的客户端会用本子系统准备内存，仅此而已 | 02 §1.0.6 |
| dma-buf（`drivers/dma-buf/`） | 缓冲区共享框架，它的导出者内部会调本子系统的 API | 02 §1.0.6 |
| `drivers/of/` | 解析 `dma-ranges` / `dma-coherent` / `memory-region`，把结果写进 `struct device` | `drivers/of/address.c:969-1044`、`drivers/of/of_reserved_mem.c:342` |

⚠️ 树里有**三个**名字带 dma 的子系统（DMA 映射 API、DMAEngine、dma-buf），互不相同。搜资料时最先要确认的就是看的是哪一个，对照表在 `02-datastructures.md` §1.0.6。

## §8 导读：按你想干的事挑一篇

| 你想做的事 | 去哪篇 | 直接跳到 |
| --- | --- | --- |
| 找某个 API 的签名、返回值、能不能睡 | `01-api.md` | §1 |
| 找某个文件 / 头文件 / Kconfig 在哪 | `01-api.md` | §0 |
| 查 `DMA_ATTR_*` 某个标志的含义 | `01-api.md` | §4 |
| 分清三种地址 / 三条获取路 / 五个池 | `02-datastructures.md` | §1.0 |
| 查某个结构体的字段 | `02-datastructures.md` | §2 起 |
| 想知道一次 map 到底走过哪些函数 | `03-internals.md` | Part A |
| 想知道 arm64 上缓存维护具体做了什么 | `03-internals.md` | §B3 |
| 想知道这套设计的取舍和源码自陈的债 | `03-internals.md` | Part C |
| 判断 swiotlb / CMA / 原子池哪个和我有关 | `04-extensions.md` | §0 |
| 照着写驱动代码 | `05-usage.md` | 按角色挑一节 |
| 区分名字很像的两个 API | `05-usage.md` | §10 |
| 写 DTS：`dma-ranges` / `dma-coherent` / 保留区 | `06-devicetree.md` | §1 总表 |
| DTS 写了没生效 | `06-devicetree.md` | §8、§9 |
| 出了问题不知道从哪查 | `07-debug.md` | §0 分诊表 |
| 看到一条不认识的内核消息 | `07-debug.md` | §4、§6 |
| 想开 `DMA_API_DEBUG` 但没看到报告 | `07-debug.md` | §2.1 |
| 「为什么要这样设计」类的疑问 | `08-feynman.md` | §0 挑一节 |
| 想知道某个数字是怎么数出来的、哪些结论没验证 | `99-evidence.md` | §5 |
