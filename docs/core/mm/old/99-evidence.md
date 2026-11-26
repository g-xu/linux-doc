# 内存管理：源码与参考依据

本附录对应 [总览](00-overview.md)、[物理资源](01-mechanisms.md)、[分配](02-allocation.md)、[地址空间](03-address-space.md)、[回收与 rmap](04-reclaim-rmap.md)、[页粒度与约束](05-placement-and-limits.md)、[API 选型](api.md) 与 [API 详细参考](api-reference.md)。

## 1. 基线与核对边界

| 项目 | 本轮事实 |
| --- | --- |
| 源码 | 本地 `linux-6.1.158`，Makefile 为 6.1.158 |
| Revision | 此源码目录不是 Git 仓库，无法给出提交 revision |
| 架构 | 只讨论 arm64 / AArch64 与它使用的公共 mm 机制 |
| RT | `localversion-rt` 为 `-rt58`；本轮不审计补丁来源，也不推定运行内核启用 RT |
| 配置 | 未发现目标 `.config`，实际页粒度、VA、SLUB、NUMA、memcg、大页和 MGLRU 启用状态未确认 |
| 算例 | 基本页 4 KiB；页表算例另限定 48 位 VA、四级；zone 算例另写明 DMA32 条件 |
| 日期 | 基础主线整理于 2026-11-20；机制增补、API 详细参考及第一版整套审查于 2026-11-21 |
| 验证 | 逐篇内容审查、关键源码核对、Markdown 解析后链接与 SVG 结构检查；未编译、加载、调参、采集目标机数据或执行压力实验 |

源码只读。全文没有把教学地址布局当作真实芯片信息，也没有把源码中存在一个配置分支当作它已启用。

## 2. 实际借鉴了什么？

| 资料 | 借鉴的讲解方法 | 不直接继承的内容 |
| --- | --- | --- |
| 用户此前提供的《精通Linux内核：智能设备开发核心技术》第 7～10 章 | 寻址、物理管理、线性空间与进阶机制的依赖关系 | 本轮未重新读取 EPUB，不把旧结构和布局当作当前实现 |
| bin 的技术小屋：虚拟内存与物理内存文章 | 先解释管理层必要性，再用布局和对象关系对应源码 | 其他架构数值、旧 VMA 链表与红黑树 |
| 蜗窝科技《图解slub》 | 对象供给总关系、局部变化与分配释放前后对照 | Linux 4.15.0-rc3 的字段、阈值和具体链表路径 |
| 蜗窝科技《逆向映射的演进》 | 由朴素方案的搜索和元数据成本解释对象式 rmap | 跨历史版本的字段与“每次 fork 必新建相同对象”的固定模型 |
| Gorman 的内存管理专著、Linux Kernel Development、OSTEP | 整体分工、机制与策略分开、访问序列和条件对比 | 借鉴上一轮阅读的目录、前言与相关章节，不通读或搬用旧版本代码 |
| Arm AArch64 内存管理指南 | 页表存储、硬件解释、翻译结果与同步分层 | 不规定所有芯片的 TLB 内部格式，不引入未经核对的新扩展 |
| Bootlin 内核培训材料 | 从需求连接接口、生命周期与观察 | 不移植其他架构或开发板的布局与实验结果 |
| Chris Down 的 swap / 内存管理文章及 Brendan Gregg 的工作集材料 | 数据复用、回收代价、驻留量与工作集的区别 | 不将经验结论当作所有工作负载的固定调参建议 |

相关正文及关键图已在前两轮阅读，本轮基于此前阅读结果组织文章，并回本地源码校正。正文与 SVG 是新写的教学内容，不复制原站图表作为本地资产。

资料入口：

- [一步一图带你深入理解 Linux 虚拟内存管理](https://www.cnblogs.com/binlovetech/p/16824522.html)
- [一步一图带你深入理解 Linux 物理内存管理](https://www.cnblogs.com/binlovetech/p/16914715.html)
- [图解slub](https://www.wowotech.net/memory_management/426.html)
- [逆向映射的演进](https://www.wowotech.net/memory_management/reverse_mapping.html)

2026-11-21 增补参考：

- [Gorman：前言与阅读组织](https://www.kernel.org/doc/gorman/html/understand/understand003.html)
- [Linux Kernel Development：目录](https://www.pearson.de/media/muster/toc/toc_9780768696783.pdf)
- [OSTEP：Swapping Policies](https://pages.cs.wisc.edu/~remzi/OSTEP/vm-beyondphys-policy.pdf)
- [Arm：AArch64 Memory Management Guide](https://documentation-service.arm.com/static/6ac508d3f59e042617c76666)
- [Bootlin：内核培训材料](https://bootlin.com/doc/training/linux-kernel/linux-kernel-slides.pdf)
- [In defence of swap](https://chrisdown.name/2018/01/02/in-defence-of-swap.html)
- [Working Set Size Estimation](https://www.brendangregg.com/wss.html)

外部材料帮助组织讲解，当前实现仍以目标树核对。除已有 memory-model、allocation 指南外，本轮实际阅读 `mm/workingset.c`、同树 PSI 与 cgroup v2 文档和相关实现。书籍部分的阅读范围是上一轮目录、前言与相关章节，不声称通读整本。

<a id="resources"></a>

## 3. 资源描述与初始化

| 结论 | 文件与符号 | 证据类型 |
| --- | --- | --- |
| 描述符与数据分开，用途字段有不同语义 | `include/linux/mm_types.h`，`struct page` | 声明与注释 |
| Node 持有自身 zone，zonelist 保存候选引用 | `include/linux/mmzone.h`，`pglist_data`、`zone` | 声明与注释 |
| 页可由节点和 zone 编号定位其 zone | `include/linux/mm.h`，`page_zone` | 实现 |
| spanned / present / managed 不是当前空闲量 | `include/linux/mmzone.h`，统计字段注释 | 语义注释 |
| arm64 使用 VMEMMAP 相关描述组织 | `arch/arm64/Kconfig`，`ARCH_SPARSEMEM_ENABLE` | 配置选择 |
| Section 粒度随目标页配置核对 | `arch/arm64/include/asm/sparsemem.h` | 条件定义 |
| PFN 到描述符按 VMEMMAP 定位 | `include/asm-generic/memory_model.h`；`arch/arm64/include/asm/pgtable.h` | 实现与基址定义 |
| 普通直接映射页内容访问 | `include/linux/mm.h`，`page_address`；`arch/arm64/include/asm/memory.h`，`page_to_virt` | 实现 |
| 局部页访问与相应配对 | `include/linux/highmem.h`、`include/linux/highmem-internal.h` 的非 HIGHMEM 实现 | 接口与适用分支，不展开其他架构 |
| 教学 zone 边界 | `arch/arm64/mm/init.c`，`max_zone_phys`、`zone_sizes_init` | 实现与自选输入推演 |
| 早期资源交接 | `arch/arm64/mm/init.c`，`mem_init`；`mm/memblock.c`，`memblock_free_all` | 交接入口 |
| 登记、保留、NOMAP 和早期分配依赖 | `include/linux/memblock.h`，`memory` / `reserved` / flags；`arm64_memblock_init`、`bootmem_init` | 字段、过滤与建立顺序的关键位置 |

RAM 两段合计 2 GiB、跨度 4 GiB，PFN 为 0x40000 与 0x100000，选定 section 编号 8 / 9：这些是自选输入的算例，不是采集结果。

## 4. 分配与接口

| 结论或契约 | 核对入口 |
| --- | --- |
| 按阶寻找、拆分、合并和伙伴条件 | `mm/page_alloc.c`，`__rmqueue_smallest`、`expand`、`__free_one_page`；`mm/internal.h` |
| PCP 适用规格与供给交接 | `mm/page_alloc.c`，`pcp_allowed_order`、`rmqueue`、`rmqueue_pcplist` |
| GFP 工作边界和严格上下文 | `include/linux/gfp_types.h`；同树分配指南 |
| SLUB 规格、CPU 状态与 slab 描述 | `include/linux/slub_def.h`；`mm/slab.h`；`mm/slub.c` 的 `slab_alloc_node` 及补给路径 |
| 字节、数组和清零包装 | `include/linux/slab.h`，`kzalloc`、`kcalloc`、`kvcalloc` |
| Cache 创建和对象接口类型 | `include/linux/slab.h`；`mm/slab_common.c`；`mm/slub.c` |
| 页块释放匹配 order，与 compound 引用模型不同 | `mm/page_alloc.c`，`__free_pages` 的实现和注释 |
| kvmalloc 条件回退与统一清理 | `mm/util.c`，`kvmalloc_node`、`kvfree` |
| 普通 vmap 与所有权转移模式 | `mm/vmalloc.c`，`vmap`、`vunmap`；`include/linux/vmalloc.h` |
| 模块相关接口可见性 | 上述实现中的相应 `EXPORT_SYMBOL`，以及头文件宏 / inline 包装 |
| 水位、保留与慢路径不能按固定流水线概括 | `__zone_watermark_ok`、`get_page_from_freelist`、`__alloc_pages_slowpath`；`include/linux/mmzone.h` |
| DMA 地址、映射与一致性类型 | `include/linux/dma-mapping.h`；同树 `Documentation/core-api/dma-api-howto.rst` |
| MMIO 资源映射及错误指针 | `include/linux/device.h`；`lib/devres.c` 的 `devm_ioremap_resource` |
| 适用用户页固定、部分结果与 unpin | `include/linux/mm.h`、`mm/gup.c`；同树 `Documentation/core-api/pin_user_pages.rst` |

代码是教学函数，包含主要成功、失败与清理路径，但未编译或运行。公开接口族核对不等于保证任意目标配置或任意上下文都可调用；内部机制函数不作为外部 API。

## 5. 映射、缺页与再利用

| 结论 | 核对入口 |
| --- | --- |
| 当前 VMA 组织使用 Maple Tree | `include/linux/mm_types.h`，`mm_struct.mm_mt` |
| VMA 建立与映射对象联系 | `mm/mmap.c`，`do_mmap`、`mmap_region` |
| 共享匿名范围由 shmem 对象承接 | `mm/mmap.c`，`shmem_zero_setup` 调用分支 |
| arm64 异常许可检查与通用交接 | `arch/arm64/mm/fault.c`，`do_page_fault`、`__do_page_fault` |
| 匿名写缺页、零页读取与 PTE 重查 | `mm/memory.c`，`do_anonymous_page` |
| COW 复制、独占复用与重查 | `mm/memory.c`，`do_wp_page`、`wp_page_copy` |
| Fork 的适用映射复制 | `kernel/fork.c`；`mm/memory.c`，`copy_page_range` |
| 文件缺页与缓存数据取得 | `mm/filemap.c`，`filemap_fault`；`mm/memory.c`，`do_fault` |
| 当前 anon_vma 联系及适用复用 | `include/linux/rmap.h`；`mm/rmap.c`，`anon_vma_clone`、`anon_vma_fork` |
| 候选范围与真实页表映射分开 | `mm/rmap.c`，`rmap_walk_anon`、`rmap_walk_file`；`mm/page_vma_mapped.c` |
| 回收在 unmap 后继续检查状态 | `mm/vmscan.c`，`shrink_folio_list`；`mm/rmap.c`，`try_to_unmap` |
| 规整与迁移联系 | `mm/compaction.c`，`try_to_compact_pages`；`mm/migrate.c`，`migrate_pages` |
| 资源组限制与 OOM 边界 | 同树 `Documentation/admin-guide/cgroup-v2.rst`；`mm/memcontrol.c`、`mm/oom_kill.c` |
| 映射类别、属性和 TLB 完成协议 | arm64 的 `memory.h`、`pgtable.h`、`pgtable-prot.h`、`tlbflush.h`；`wp_page_copy` |
| 页缓存索引与脏状态 / 写回 | `include/linux/fs.h` 的 `address_space.i_pages`；`mm/page-writeback.c` 的 `folio_mark_dirty`、写回与 dirty 平衡路径 |
| 传统候选检查与 MGLRU 角色 | `mm/vmscan.c` 的 `folio_check_references` 与相关 LRU 路径；同树 `Documentation/mm/multigen_lru.rst` |
| Swap entry、cache 与恢复入口 | `mm/swap_state.c` 的 `add_to_swap`；`mm/memory.c` 的 `do_swap_page`；swap 后端相关文件 |
| Shadow、refault 距离与类型 / 路径条件 | `mm/workingset.c` 的设计注释、`workingset_eviction`、`workingset_refault`；`mm/filemap.c` 的 `filemap_add_folio` |
| 相关工作集计数的页数与导出 | `workingset_refault` 的 `folio_nr_pages` / 状态更新；`mm/memcontrol.c` 的字段导出 |
| Memory PSI 的含义、单位与作用域 | `Documentation/accounting/psi.rst`；`kernel/sched/psi.c`、mm 的相应 memstall 使用位置 |

四级页表示例的索引、页偏移与 PA 是计算结果。更新与释放只讲已核对的协议位置，没有穷举 arm64 所有页表和 TLB 分支。

## 6. 粒度、位置和约束的依据

| 机制 | 核对依据 |
| --- | --- |
| THP 的适用回退、合并及拆分关系 | 同树 `Documentation/mm/transhuge.rst`；`mm/huge_memory.c`、`mm/khugepaged.c` |
| HugeTLB 池与预留模型 | 同树 `Documentation/admin-guide/mm/hugetlbpage.rst`；`mm/hugetlb.c` |
| CMA 进入管理及连续分配 | `mm/cma.c` 的 `cma_activate_area`、`cma_alloc`；`init_cma_reserved_pageblock`、`alloc_contig_range` |
| NUMA 策略和作用域 | 同树 `Documentation/admin-guide/mm/numa_memory_policy.rst`；`mm/mempolicy.c` |
| 承诺记账与三类 overcommit 策略 | 同树 `Documentation/mm/overcommit-accounting.rst`；`mm/util.c` 的 `__vm_enough_memory` |
| 资源组 charge 与高水位 / 上限 | `mm/memcontrol.c` 的 charge 和相关处理；同树 cgroup v2 指南 |
| Min / low 保护与有效边界 | 同树 cgroup v2 的 `memory.min/low` 说明；`effective_protection`、`mem_cgroup_calculate_protection` 与 `mm/vmscan.c` |
| 固定约束与资源管理变化 | 同树 pin、memory-hotplug 指南；`mm/gup.c`、`mm/memory_hotplug.c` |

同树文档中的某些结构细节可能属于历史描述。正文只使用本轮需要的机制原则与当前声明 / 分支；没有转录旧 pin 计数布局或把其他架构的地址表推广为 arm64 保证。

2026-11-21 的新增算例边界：

- Swap 对照将可讨论供给简化为 8 GiB，初始为 5 GiB 文件缓存、2 GiB 匿名内容、1 GiB 空闲；新增需求 2 GiB。两种选择均腾出额外 1 GiB，省略水位、元数据、固定、范围和扫描细节，不保证内核实际选择相同。
- Low 图中父级有效量 6 GiB、子组各要求并使用 4 GiB；在所选简化层级中，各分得 3 GiB。它不是全部层级设置的计算器，也不表示预先分配 RAM。
- Refault 时间线追踪文件与页索引，不追踪同一个物理页。当前传统路径与 MGLRU 分支有区别，保留历史和统计结果不是每次载入都相同。
- PSI 示例是人为构造格式，图只说明 some / full 的作用域含义；没有采集真实比率，也没有将块宽或 total 直接当成实测压力。
- TLB 图是翻译关系示意，不规定固定硬件记录格式或所有配置下同一套失效指令。

## 7. 当前覆盖与保留边界

总览主文补入原先被压缩的机制解释，分篇保留更细图解与实现入口；API 独立查询。不生成另一份手工拼接全文，以免产生重复维护的正文。

| 认识范围 | 当前实质内容位置 |
| --- | --- |
| 全局能力、对象与相互联系 | 总览主文 |
| 资源、描述符、node / zone / section | 资源篇 |
| 早期分配、保留、NOMAP、交接 | 资源篇的启动部分 |
| Buddy / PCP、对象和虚拟工作区 | 分配篇 |
| 水位、允许工作、慢路径选择 | 分配篇的上下文部分 |
| arm64 映射、页表操作、TLB / Cache / 设备关系 | 地址空间篇 |
| 缺页、文件缓存、COW、写回与脏页平衡 | 地址空间篇 |
| 传统 LRU / MGLRU、工作集与 refault、swap 对照、rmap、回收与 OOM | 回收篇 |
| PSI、统计口径与观察局限 | 回收篇及 API 查询 |
| 大页、CMA、NUMA、memcg、保护、overcommit、固定和热插拔 | 页粒度与约束篇 |
| 主要接口及使用边界 | API 指南及详细参考 |

表中“覆盖”指职责、基本机制及联系，不表示已逐行审计完整算法。当前还包含 17 篇独立实现专题，从 [专题首页](sub/index.md) 进入；下面区分已经展开和可以继续下钻的部分：

| 实现范围 | 当前专题 | 仍可深入的边界 |
| --- | --- | --- |
| 物理页分配 | Buddy、PCP、分配慢路径 | 完整 pageblock 接管启发式、特殊供给、独立规整算法 |
| 对象与虚拟分配 | SLUB、Vmalloc | 所有调试与 hardening、完整节点搜索与特殊映射 |
| 地址与文件数据 | arm64 缺页/COW、页缓存/写回 | 共享匿名与 shmem 专题、KSM、各文件系统及设备特殊路径 |
| 回收与候选选择 | 回收执行、Rmap、传统 LRU、MGLRU | 各 swap 后端、完整迁移及全部能力/配置组合 |
| 大页与资源约束 | THP、HugeTLB、CMA、NUMA、Memcg、Overcommit | NUMA balancing、专门 GUP/长期固定与热插拔实现专题 |

启动、固定和热插拔已有机制说明，但尚未独立展开完整实现。SLUB 并发和大页竞争已经在相应专题解释，不能继续把它们整体标成未写；“还可深入”指未穷举的分支。

2026-11-21 已逐篇审查当前 29 篇 Markdown 及配套 59 张 SVG 的内容、导航和图文关系。修正与核对范围见 [第一版整套审查](#first-edition-review)。图示是独立 SVG；XML 与标签核对不等于视觉布局验证，本次没有启动预览、构建或内核实验。

以下第 8～12 节保留历史写作批次的证据。“本轮”“尚未重写”“未改 skill”等表述只描述对应批次，不作为整套文档的当前状态；当前范围以上表和第 13 节为准。

<a id="api-reference"></a>

## 8. API 详细参考的资料与核对

本轮联网查找重点为同版本的内核官方接口文档。在线页面用于确认文档入口、分类和可借鉴契约，实际参数和示例最终以本地 6.1.158 的声明、实现、导出及相关调用者核对：

| 官方入口 | 本轮借鉴 |
| --- | --- |
| [Memory Management APIs](https://docs.kernel.org/6.1/core-api/mm-api.html) | 参数、返回值、用户访问、映射及分配族的接口组织 |
| [Memory Allocation Guide](https://docs.kernel.org/6.1/core-api/memory-allocation.html) | 常见供给选择、GFP 与配对清理 |
| [GFP masks from FS/IO](https://docs.kernel.org/6.1/core-api/gfp_mask-from-fs-io.html) | 作用域与嵌套恢复，不仅替换表层标志 |
| [Pin user pages](https://docs.kernel.org/6.1/core-api/pin_user_pages.html) | 普通引用、固定、长期使用与 unpin 的区别 |
| [DMA API HOWTO](https://docs.kernel.org/6.1/core-api/dma-api-howto.html) | 地址类型、方向、同步、SG 数量与池对象 |
| [Kref](https://docs.kernel.org/6.1/core-api/kref.html) | 对象存活及所有者引用的配对 |

没有将检索中出现的 6.10、6.19 等页面当作本地签名来源，也没有把目录或缓存摘要阅读表述为完整在线实现审计。同树 allocation、GFP、pin、DMA、kref 文档及实际代码辅助复核。

| 参考页内容 | 实际核对位置与重点 |
| --- | --- |
| 字节、数组和容量 | `include/linux/slab.h`、`include/linux/overflow.h`；非零失败、零大小和容量检查 |
| 扩容 | `mm/slab_common.c` 的 `krealloc/__do_krealloc`；`mm/util.c` 的 `kvrealloc`，失败保留和零大小差异 |
| Cache 与 usercopy | `kmem_cache_create_usercopy`、对象包装、创建和销毁实现；参数类型及复制范围 |
| 连续页和 exact | `mm/page_alloc.c`，原 order/size 与非 compound / 引用模型 |
| 虚拟区域和 vmap | `mm/util.c`、`mm/vmalloc.c`；回退条件、数组与页所有权、转移标志 |
| 用户复制 | `include/linux/uaccess.h` 与 arm64 uaccess；未复制字节、清零和访问上下文 |
| 独立用户映射页 | `mm/memory.c` 的页校验、插入和 `vm_insert_page`；类型、引用、VMA 和错误 |
| vmalloc 后备用户映射 | `vmalloc_user`、`remap_vmalloc_range_partial`；`kernel/fork.c` / VMA 生命周期的 open/close 使用 |
| 固定 | `mm/gup.c`，当前 mm、五参数签名、实际页数与 unpin |
| DMA | `include/linux/dma-mapping.h` 与同树 DMA 指南；地址对、原段数和完成契约 |
| 池 | `mm/mempool.c`、`mm/dmapool.c` 及头文件；reserve、等待、清零、参数顺序与归还 |

共享缓冲区示例假设数据地址与容量不变、外层持有有效拥有者引用、VMA 单独持有引用，所有最后释放都在适用任务上下文。Kref 不提供共享数据同步；实际文件操作、权限、设备完成仍由调用者实现。

单页图只画没有其他使用者的稳态引用，非所有页的完整计数。示例函数未编译、加载或运行；本轮只做静态契约、主要失败路径和文档检查，不据此声称完整驱动可以直接使用。

<a id="buddy"></a>

## 9. Buddy 第一篇深入专题的核对

[Buddy 专题](sub/page-allocation/01-buddy.md) 和 [页分配 API 样章](api-reference.md#pages) 于 2026-11-21 编写，当前状态是内容草稿，等待用户 review。专题解释内部机制，API 样章解释调用者契约，旧 API 页的按阶部分只保留选型与链接。

| 机制或结论 | 目标树核对位置 | 范围与边界 |
| --- | --- | --- |
| 按阶、按类型组织空闲块 | `include/linux/mmzone.h:42`：迁移类型；`:105`：`free_area`；`:822`：zone 数组与锁 | 数组及类型按配置；不固定 MAX_ORDER 数值 |
| 块首个页的元数据 | `include/linux/mm_types.h:89`；`include/linux/page-flags.h:975`；`mm/page_alloc.c:944`、`:1000`、`:1033` | Buddy 类型、链表和 private 阶；不是所有空闲基本页都标记 Buddy |
| 寻找与拆分 | `mm/page_alloc.c:2561`：`__rmqueue_smallest`；`:2347`：`expand` | 固定普通类型、不触发回退和 guard page 的主干 |
| 伙伴计算与验证 | `mm/internal.h:301`、`:340`、`:359` | 阶、zone、空闲表示和引用检查；不是任意 PFN 的公开接口 |
| 逐阶合并与类型边界 | `mm/page_alloc.c:1095`；`include/linux/mmzone.h:89`；`include/linux/pageblock-flags.h:31` | pageblock 类型检查有适用阶条件；不同普通类型不一概禁止合并 |
| 停止合并后的入队位置 | `mm/page_alloc.c:1055`、`:1156` | 合并机会、显式入尾和 shuffle 分支分别解释 |
| 取出候选与最终成功分开 | `mm/page_alloc.c:3715`、`:4324`、`:5578` | 候选检查、prep_new_page 和适用 memcg charge 可影响最终结果 |
| 页分配 API 的真实调用者 | `include/linux/mempool.h:99`；`mm/mempool.c:544`、`:551`；`drivers/iommu/io-pgtable-arm.c:202`、`:240` | 固定阶保存、分配失败回滚和适用映射先撤销；不把专用 IOMMU 地址约束推广到普通驱动 |
| 持锁边界与 PCP 交接 | `mm/page_alloc.c:3124`、`:1546`、`:1617`、`:3715` | zone 锁保护 Buddy 组织；PCP 另有保护，不推广为调用者资源存活保证 |
| proc 计数 | `mm/vmstat.c:1456`、`:1481`；`Documentation/filesystems/proc.rst` | 各阶块数，不展示 PCP 库存；动态输出与遍历上限保留边界 |
| 事件字段和触发点 | `include/trace/events/kmem.h:177`、`:235`、`:243`；`mm/page_alloc.c:2577`、`:1610`、`:5636` | 区分选块、PCP 归还和最终分配；事件不提供所有内部拆分状态 |

另联网阅读了 [6.1 的 proc 说明](https://docs.kernel.org/6.1/filesystems/proc.html) 和 [kmem 事件说明](https://docs.kernel.org/6.1/trace/events-kmem.html)。借鉴其“计数口径对应问题、事件对应交接”的组织方法；其中历史架构、迁移类型和旧事件字段不直接搬用，实际字段回目标源码核对。

四张 Buddy SVG 是新绘制的教学示意。PFN 64～72 为局部计算模型，不是芯片实际 RAM 起点；初始为同 zone、同普通类型的 order-3 块，申请 order 1。拆分后两个余块分别为两页、四页；归还时下一 order-3 伙伴 72～80 已被占用，合并停在 order 3。图表只跟踪这组块对链表的贡献。

普通路径伪代码省略的实际分支已在正文说明：guard page、compaction capture、类型回退、入队策略及 reporting。未采集 proc 输出、未启用追踪、未编译运行示例，不能将静态推演标为目标机实验结果。

本节记录 Buddy 批次当时的范围；PCP、分配慢路径等后续新增覆盖见下一节，类型回退与各机制完整分支仍不以本篇作为全部实现审计。不修改 skill、内核或站点部署配置，不提交或推送。

<a id="more-deep"></a>

## 10. 其余深入专题的核对

本批新增十篇机制草稿和十一张独立 SVG，统一入口为 [深入专题导读](sub/index.md)。日期为 2026-11-21，基线仍是本地 Linux 6.1.158、arm64，配置启用状态未知。本批不重写其他 API 族，也不改已有 skill。

| 专题 | 定向核对的源文件与符号 | 核对重点与未穷举边界 |
| --- | --- | --- |
| PCP | `include/linux/mmzone.h`；`mm/page_alloc.c`：`pcp_allowed_order`、`__rmqueue_pcplist`、`rmqueue_bulk`、`free_unref_page_commit`、`free_pcppages_bulk` | 基本页 count 与块数、批量交接、锁失败分支；不固定 high / batch 或推广所有 RT 语义 |
| 分配慢路径 | `mm/page_alloc.c`：`__alloc_pages_slowpath`、direct reclaim / compact、retry 与 OOM helper | 范围、内部许可和进展不同；早期规整、非睡眠退出和重试有条件 |
| SLUB | `include/linux/slub_def.h`、`mm/slab.h`；`mm/slub.c`：`slab_alloc_node`、`get_freelist`、`deactivate_slab`、`do_slab_free`、`__slab_free` | 两条 freelist、tid / counters、frozen 与列表交接；full 跟踪受调试与标志限制，不穷举 hardening |
| Vmalloc | `include/linux/vmalloc.h`；`mm/vmalloc.c`：`__vmalloc_area_node`、`__vmalloc_node_range`、`__vunmap`、lazy purge；`mm/util.c` | 区间、页数组、实际页数、部分映射和地址重用分开；不把 lazy 处理当合法旧指针存活期 |
| arm64 fault / COW | `arch/arm64/mm/fault.c`；`mm/memory.c`：`handle_pte_fault`、`do_anonymous_page`、`copy_present_pte`、`do_wp_page`、`wp_page_copy` | VMA 与 PTE 状态、锁外准备、锁内重查、旧映射结束顺序；不抹掉 userfaultfd / 大页条件 |
| 页缓存与写回 | `mm/filemap.c`：`filemap_fault`、`filemap_add_folio`；`mm/page-writeback.c`：dirty / writeback helper、节流；`fs/fs-writeback.c` | 引用、成员资格、i_size 与 uptodate；再次脏化不被本轮完成清除；具体文件系统数据快照未假定 |
| 回收 | `include/linux/mmzone.h`；`mm/vmscan.c`：`shrink_folio_list`、`__remove_mapping`；`mm/workingset.c` | 候选、后备、unmap、pin 与引用冻结；MGLRU 和传统路径不当相同算法，demotion 进展不当全机内容消失 |
| Rmap | `include/linux/rmap.h`、`mm/rmap.c`、`mm/page_vma_mapped.c` | 区间候选和真实 PTE 分开、对象 / 索引 / 页表分层保护；不固定每次 fork 的新增关系数 |
| 大页与 CMA | `mm/huge_memory.c`、`mm/khugepaged.c`、`mm/hugetlb.c`、`mm/cma.c`；`mm/page_alloc.c`：`alloc_contig_range` | 映射拆分和对象拆分、预留与实例化、bitmap 占位和物理范围取得；失败恢复资源状态不承诺物理布局完全还原 |
| NUMA / memcg / 承诺 | `mm/mempolicy.c`：`get_vma_policy`、policy helper；`mm/memcontrol.c`：charge、stock、high、protection；`mm/util.c`：`__vm_enough_memory` | 优选位置、层级额度和未来承诺不同；改变策略不自动迁移已有页，stock 不等于物理库存 |

联网确认了同版本官方入口，并以本地相应说明和实现为最终依据：

- [Transparent Hugepage Support](https://docs.kernel.org/6.1/mm/transhuge.html)
- [Multi-Gen LRU](https://docs.kernel.org/6.1/mm/multigen_lru.html)
- [HugeTLB Pages](https://docs.kernel.org/6.1/admin-guide/mm/hugetlbpage.html)
- [NUMA Memory Policy](https://docs.kernel.org/6.1/admin-guide/mm/numa_memory_policy.html)
- [Control Group v2](https://docs.kernel.org/6.1/admin-guide/cgroup-v2.html)

新图是对象关系和条件状态示意，不是实测图。PCP 四页补给是限定的单页算例，不是默认 batch；fault 图假定同 mm、同空 PTE；rmap 图限定普通文件基本页；大页格数不代表实际 PMD 页数；CMA bitmap 单元粒度没有硬编码；共享页图不规定每个映射者重复 charge。

核对类型为“目标源码事实”及“有前提的教学推演”。本批只做内容、源码、链接、锚点和 SVG 静态检查，未编译、运行、追踪、调参或构建站点。文章状态为已写、等待用户内容 review，不将机械检查通过作为理解效果或全部实现覆盖的证明。

<a id="slub-rewrite"></a>

## 11. SLUB 实现剖析重写

此前批量专题主要是机制导读，不能以“有源码入口和图示”认定原理及实现已讲透。本次只重写 [SLUB](sub/object-allocation/01-slub.md)，保留其余文章内容，并在总览和专题入口区分当前深度；不改 API 或 skill。

基线仍为 Linux 6.1.158、arm64。普通槽位模型排除改变复用的 hook / 调试路径；中断 ABA 图限定非 RT、同活动 slab、适用非睡眠快路径，不推定目标 `.config`。

| 结论 / 机制 | 本轮实际核对位置 | 类型与边界 |
| --- | --- | --- |
| 数据大小、槽位步长、freepointer 偏移 | `include/linux/slub_def.h:90`；`mm/slub.c:4190`、`:378`、`:414` | 源码事实；ctor / RCU / poison 和调试可改变布局，cache inuse 为字节而 slab inuse 为计数 |
| 优选阶与实际阶回退 | `mm/slub.c:3965`、`:1923` | 源码事实；给定 order-0 的容量算例不预测实际优选阶 |
| 两条 freelist 与活动计数 | `mm/slab.h:23`；`mm/slub.c:2963`、`:3181`、`:3485`、`:3618` | 根据普通路径推导 N=U+L+G、I=N−G=U+L；不是无锁系统快照 |
| Node partial 接管 | `mm/slub.c:2148`、`:2198`、`:3197` | 联合比较先于移出集合；mode=1 与 mode=0 的计数和链处理不同 |
| 局部原子比较与 tid | `mm/slub.c:2337`、`:3270`、`:3618` | 旧链头回到相同地址但 tid 变化的静态竞争；不声称无限宽度事务标识 |
| Slab 级归还与供给接收 | `mm/slub.c:3485`、`:2963` | head 与 counters 联合验证；空链分支清 frozen，非空链转交局部供给 |
| CPU partial 暂存与 drain | `mm/slub.c:224`、`:458`、`:2632`、`:2536` | 按配置可用，实际采用 slab 数量限制；不把它当对象 freelist |
| 活动 slab 停用 | `mm/slub.c:2680`、`:2416` | 先摘局部状态，数 free_delta，再结合最新共享状态 unfreeze；需要成员资格操作时配 node 锁 |
| 空 slab 保留及最终页回收 | `mm/slub.c:2486`、`:3571`、`:2039`、`:2031`、`:2001` | Partial 保留是策略；按实际 folio 阶归还，RCU 延后底层回收不等于逐对象延迟复用 |
| arm64 与回退原子实现 | `arch/arm64/Kconfig:148`、`:178`；`arch/arm64/include/asm/percpu.h:144`、`cmpxchg.h:202`；`mm/slub.c:506`、`:544` | 区分局部和共享比较，保留对齐、顺序、位锁回退及 RT 条件，不固定芯片指令 |
| RT / 调试路径 | `mm/slub.c:172`、`:1278`、`:3323`、`:3660` | 非 RT 无锁路径不是 RT 硬中断模板；full 跟踪有配置和标志条件 |

布局算例限定 `PAGE_SIZE=4096`、`object_size=512`、对齐 64，无其他空间开销：普通 offset 256、步长 512，给定一页容纳八槽位；仅 ctor 外置链接模型 offset 512、步长 576，容纳七槽位。没有把 4 KiB 或 64 字节写成所有 arm64 的固定参数。

八槽位序列与三组竞争分别验算：A～H 完整归属变化；同 CPU 链头恢复 A 后旧 next B 已失效；slab 接收期间 Y→X 和计数变化；停用期间最新 I=6 扣局部五槽位得到 I=1。图中的不同起始快照相互独立，不拼成所有申请必经的单一路径。

本次新增五张 SVG 并更新原通道图，展示布局、集合数量、局部 tid 竞争、批量接收竞争和停用竞争。还读取了同树 `Documentation/mm/slub.rst` 的调试说明；核心计数与并发结论以目标树实际更新为准，不套用旧版本文章的 deactivation 算法。

只做源码、算例、Markdown 与 SVG 静态核对，未编译或运行文档片段、未启用追踪、未修改系统或站点配置，不提交和推送。SLUB 仍待内容 review；其余专题没有在这一轮一并重写。

<a id="other-rewrite"></a>

## 12. 其余专题的原理与实现重写

此节记录SLUB之后的重写批次，日期为2026-11-21，基线仍为arm64 Linux 6.1.158。PCP、慢路径、vmalloc、fault/COW、cache/writeback、回收和rmap在原路径重写；传统LRU与MGLRU新增独立正文；两个原组合页改为导航，THP/HugeTLB/CMA和NUMA/memcg/overcommit分别落盘。

| 机制 | 本次定向核对 | 关键推演及保留条件 |
| --- | --- | --- |
| PCP | `mm/page_alloc.c` 的索引、`__rmqueue_pcplist`、`rmqueue_bulk`、high/free与drain | 混合阶count、batch按阶换算、库存12→14→16→8的限定策略 |
| 慢路径 | 同文件水位、slowpath、reclaim/compact/retry/OOM | 本次mark、形态扣除与lowmem reserve；进展和真正取得块分开 |
| Vmalloc | `mm/vmalloc.c:1375`、`:1401`，area建立/撤销/lazy处理 | 四种分裂、额外节点、[3,19)切[8,12)及对齐失败 |
| Fault/COW | `arch/arm64/mm/fault.c`、`mm/memory.c` 的分派、anon/copy/wp | 候选身份、重查、mapcount与RSS、clear/flush/install；普通例子排除特殊pin/KSM |
| 缓存写回 | `mm/filemap.c`、`mm/page-writeback.c`、`fs/fs-writeback.c` | i_size/成员重查、DIRTY与WRITEBACK位/mark、TOWRITE、再次脏化 |
| 回收执行 | `mm/vmscan.c`、`mm/swap_state.c`、`mm/memory.c`、`mm/workingset.c` | 输出集合、swap恢复、引用冻结先于dirty；refault不按秒 |
| Rmap | `mm/rmap.c`、`mm/page_vma_mapped.c` | 文件index7与pgoff4的候选；真实条目、祖先关系与锁 |
| 传统LRU | `mm/vmscan.c:1457`、`:2919` | 两轮访问证据、cost/priority/fraction；两组扫描预算算例 |
| MGLRU | `include/linux/mmzone.h`；`mm/vmscan.c:3603`、`:3656`、`:4256`、`:4335`、`:4945` | Seq/slot、aging与type/tier反馈；移动列表不当作物理迁移 |
| THP | `mm/huge_memory.c:656`、`:2613`；`mm/khugepaged.c:978`、`:1064` | PMD竞争、deposit、临时512+512、pmd_populate回滚、拆分引用预检 |
| HugeTLB | `mm/hugetlb.c:1390`、`:1395`、`:1914`、`:6083` | Free/Reserved独立分支、RestoreReserve与实例化保护；节点/子池条件保留 |
| CMA | `mm/cma.c:57`、`:69`、`:76`、`:420`；`mm/page_alloc.c:9338` | bit粒度、mask/offset、占位与迁移、最终验证、EBUSY与重试位置 |
| NUMA | `mm/mempolicy.c:1950`、`:1976`、`:2163` | pgoff/shift换算、nodes{0,2,4}；优选与实际落点不混同 |
| Memcg | `mm/page_counter.c:97`；`mm/memcontrol.c:2226`、`:2630`、`:2766` | 子层成功祖先失败的回滚、32额度stock、high的执行点 |
| Overcommit | `mm/util.c:935`、`:990` | limit两分支、HugeTLB扣除、保留量、严格小于边界及撤销 |

算例均限定无额外并发或明确画出的竞争，省略的元数据/后端条件在正文交代：PCP high/batch不是默认值；水位只选一组内部许可；VA坐标相对适用基址；THP容量数值限定4KiB/2MiB且旧页独占；CMA区域假定已经合法激活；charge表只看指定memory counter；承诺表不是物理页分配保证。

本次新增七张SVG，原图继续作为对应机制的关系图。正文数量不表示全量mm覆盖，尚未穷举所有swap后端、完整NUMA balancing、每种大页/设备迁移和所有调试配置。目标配置仍未知，图表不标为实测。

Buddy本批保留原拆分/合并推演，补入 `mm/page_alloc.c:3004` 的类型回退搜索与重新分类/取块分工，区分先大与MOVABLE条件下另找小块的分支。没有把分类调整当作内容迁移。

核对包括源码、图文数字、Markdown链接/锚点、表格和SVG结构。不预览、构建、编译、调参、追踪或提交，保留原SLUB、API和skill的当前内容；本批仍待用户review。

<a id="first-edition-review"></a>

## 13. 第一版整套内容审查

日期为 2026-11-21。范围是当前 `docs/core/mm/` 的全部 29 篇 Markdown：总览、五篇机制、两篇 API、证据附录、专题首页、17 篇实现正文及两个保留导航页；未将审查扩展到站点其他模块。

| 发现与修正 | 依据和核对边界 |
| --- | --- |
| `pin_user_pages` 示例遗漏调用前持有 mmap 锁的条件，已补配对读锁并同步选型页 | `mm/gup.c:3304`、`:2131`、`:1418`；unlocked 包装在 `:3329`、`:2354` 自行管理锁，本树该 unlocked 路径拒绝 `FOLL_LONGTERM` |
| CMA 释放说明容易被理解为完整所有权/页数校验，已明确调用者保存原区域、首页和 count | `mm/cma.c:522` 的 `cma_pages_valid` 主要检查首页范围；`:551` 的 `cma_release` 不验证完整分配身份，尾界检查不替代调用者责任 |
| MGLRU 数值例子没有显式列出两个 gain 的具体取值，已补 `swappiness=100`、gain=100 和完整乘式 | `mm/vmscan.c:3656` 的控制比较、`:4945` 的类型选择；数值是整数算例而非采集计数 |
| 短专题直接进入字段/公式，缺少从机制到实现的衔接 | 补充各专题负责的问题、前置文章及主线解释；不重写已认可的 Buddy/SLUB 结构 |
| 缩略词与硬件名称解释不完整 | 总览补 PSS、VA/PA、ASID、GUP；地址篇和 fault 专题补寄存器及 EPAN 名称含义 |
| 正文和覆盖表混入过期批次状态 | 查询入口去掉“样章”等临时定位；当前覆盖与历史证据分开，保留既有路径和历史核对记录 |

结构审查区分学习路线与 API 查询，保留总览、机制分篇和实现专题的分工，不新增拼接全文。SVG 已检查标签、方向含义、例子单位及与正文的对应；已有资源约束图接入 Memcg 专题，没有为图数量新增资产。

链接检查用实际 Markdown 解析结果取得 id，再核对页内、跨页与图片引用；同时检查支持内嵌 HTML 的普通 Markdown 模式，不再仅从 `{#id}` 文本推断锚点存在。禁用 HTML 的阅读器不属于该检查的保证范围。

这次还把实际审阅反馈归纳到仓库的 `kernel-core-subsystem-docs` skill：模块职责覆盖、概念依赖、直接专题入口、术语、SVG 图示、先机制后实现、可查询的 API 契约，以及分层 review 方法。文档核对不等于 skill 对所有模块的行为保证，后续新模块仍按具体提纲和样章审阅。

审查属于逐篇文档阅读、关键源码定向核对及静态解析，不是整棵内核逐行审计。所有教学代码仍未编译、加载或运行，未执行设备 DMA、用户映射、固定、回收或压力实验；也未构建、发布或提交网站。
