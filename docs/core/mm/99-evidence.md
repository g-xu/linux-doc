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
| 日期 | 基础主线整理于 2026-11-20；机制增补和 API 详细参考核对于 2026-11-21 |
| 验证 | 内容、关键源码、链接与 SVG 结构静态核对；未编译、加载、调参、采集目标机数据或执行压力实验 |

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

## 3. 资源描述与初始化 {#resources}

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

表中“覆盖”指职责、基本机制、联系和定向实现入口，不表示已逐行审计每项完整算法。完整启动调用时序、SLUB 并发、大页并发、所有回收排序、swap 后端、NUMA 与 memcg 内部细节仍可继续下钻。

认可的样章与 skill 本轮保持不变。只修改文档及其图示，不变更内核源码、站点部署配置，不提交 Git。

图示是独立静态 SVG。检查文件引用、XML 结构及图文算例，不启动站点预览、构建或排版渲染测试。内容是否符合阅读预期仍由用户 review 判断。

## 8. API 详细参考的资料与核对 {#api-reference}

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

## 9. Buddy 第一篇深入专题的核对 {#buddy}

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

## 10. 其余深入专题的核对 {#more-deep}

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
