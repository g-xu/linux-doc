# 其余内存管理专题：独立挑战记录

日期：2026-11-24。范围为新增 02–14 专题，01 保留已有 Pauli 审阅结果。每篇最多三轮，“提交问题→作者回应更新→同一挑战者复核”组成一轮；未达成一致项最终附在对应正文末尾。作者接受本身不记为关闭。

## 角色与进度

| 专题 | 独立挑战者 | agent ID | 当前状态 |
| --- | --- | --- | --- |
| 02–04 | Boole | `01a20cfd-6e3f-7e60-8db4-b3913f53d087` | 三篇均第1轮一致 |
| 05–07 | Bohr | `01a20d01-4480-7820-87b2-9d9c2f720200` | 三篇均第1轮一致 |
| 08–10 | Hypatia | `01a20d04-7f0a-7241-914c-275934b1712c` | 三篇均第1轮一致 |
| 11–14 | Plato | `01a20d09-0135-7183-8e4c-6c2754f34ca6` | 11/13/14第1轮一致，12第2轮一致 |

挑战者均为独立新身份，没有继承作者的自评结论；只读文章、配图与目标源码，不代替作者修改。skill 为 `kernel-topic-docs`。一致不是用户验收或目标机验证。

## 第1轮：已收到的挑战

| ID | 文章及位置 | 挑战与原始依据 | 状态 |
| --- | --- | --- | --- |
| P2-C1 | 02，第4–5节 | 候选范围到页初始化、PageReserved、引用、managed_pages与Buddy移交缺少状态链；memblock.c、page_alloc.c | resolved |
| P2-C2 | 02，第4/6节 | 早期分配成功如何增加reserved、防重用、失败与节点回退缺少实例；memblock.c | resolved |
| P3-C1 | 03，第3.2/4/5节 | CPU/slab两条freelist、frozen/inuse与partial去向不能推演；slub.c | resolved |
| P3-C2 | 03，第5节 | 缓存名原缓冲与constructor寿命混写；slab_common.c kstrdup_const及释放 | resolved |
| P4-C1 | 04，第3.2/4节 | 后备页归还与VA lazy purge安全复用不是同一时间；vmalloc.c | resolved |
| P5-C1 | 05，第2/4/5节 | mm_users与mm_count计数关系和最后使用者退出未闭合；sched/mm.h、fork.c | resolved |
| P5-C2 | 05，第2/5节 | 包含查询、find_vma后继与空洞未区分；mmap.c、memory.c | resolved |
| P6-C1 | 06，第3.3/4/5节 | break-before-make只有名称，缺具体COW更新链；memory.c、arm64 fault/tlbflush | resolved |
| P7-C1 | 07，第4/5节 | COMPLETED/RETRY等返回锁责任未闭合；arm64 fault.c、mm_types.h | resolved |
| P7-C2 | 07，第3.2节 | 独占标记与保守复用检查仍无法推演；memory.c do_wp_page | resolved |
| P8-C1 | 08，第3.3/5节 | 清脏、共享PTE写保护、写故障和重新记脏缺因果链；page-writeback.c、rmap.c、filemap.c | resolved |
| P8-C2 | 08，第4/6节 | 回写结束不等于成功，暂缓与实际I/O失败、错误交接未分清；buffer.c、pagemap.h、filemap.c | resolved |
| P9-C1 | 09，第3.3/4节 | 交换PTE、缓存、I/O与原页释放时点未闭合；swap_state.c、vmscan.c、memory.c | resolved |
| P9-C2 | 09，第3.2节 | 冷热观察只有概述，缺传统两次观察规则和MGLRU aging因果；vmscan.c、MGLRU文档 | resolved |
| P10-C1 | 10，第3.1/4/5节 | 迁移条目、源/目的锁、缓存转移后等待和失败恢复未闭合；filemap.c、migrate.c | resolved |
| P10-C2 | 10，第3.2/4/6节 | 两类隔离列表、目的回调和部分失败责任缺乏具体过程；compaction.c、migrate.c | resolved |
| P11-C1 | 11，第3/4/5节 | 缓存、PTE引用、逻辑映射数与两类索引缺状态链；filemap/mmap/memory/vmscan | resolved |
| P11-C2 | 11，第6节 | fast可回慢、写权限及dirty/unpin选型未具体说明；gup及pin文档 | resolved |
| P12-C1 | 12，第2/3/4节 | 选择收费组、父层失败回滚、绑定和uncharge未闭合；memcontrol/page_counter | resolved（第2轮） |
| P12-C2 | 12，第4/5/6节 | OOM评分及事件来源不能推演；oom_kill/memcontrol/cgroup文档 | resolved |
| P13-C1 | 13，基线/自动平衡 | 漏NUMA_BALANCING依赖!PREEMPT_RT；init/Kconfig | resolved |
| P13-C2 | 13，第2/3/4节 | DEFAULT/LOCAL、策略优先级、INTERLEAVE缺具体规则；mempolicy | resolved |
| P13-C3 | 13，第3/6节 | mbind flags、权限与失败后部分状态缺口；mempolicy do_mbind | resolved |
| P14-C1 | 14，第2/3节 | 真实key存量与事件增量、范围/单位缺实例；vmstat/vmscan | resolved |
| P14-C2 | 14，第3/6节 | nr_accesses、聚合清零和区域结果入口未明；DAMON core/vaddr/trace | resolved |

前三组首轮将以上项归为实质解释/契约缺口，未认定确定的实质技术错误。初始审阅版本标识及后续回应/复核将在下方保留。

## 第1轮作者回应

以下均为接受并已修改，但在挑战者复核前仍是open：

| ID | 作者回应与实际改动 |
| --- | --- |
| P2-C1 | 比较保留PFN2和候选PFN8的初始化、引用1、Reserved、清零/managed/Buddy移交与延后条件 |
| P2-C2 | 加bottom-up/基址/对齐条件及phys_alloc_range参数；reserved新增合并、21→19和防重复；节点变体失败/回退 |
| P3-C1 | 加CPU0/CPU1的两freelist、inuse/frozen表；补局部取共享链、停用和min_partial保留分支 |
| P3-C2 | 明确kstrdup_const名称复制/安全复用与constructor代码寿命不同 |
| P4-C1 | 加使用、清表/lazy待purge、flush后VA重用三阶段；后备归还另按所有权，不要求额外别名刷新 |
| P5-C1 | 加mm_users整体贡献一个mm_count、共享/额外mmgrab/逐次退出表和取得/复活/锁边界 |
| P5-C2 | 加包含/后继/空洞查询表，闭区间转换、重检与布局保护 |
| P6-C1 | 加COW重检→clear_flush_notify→新关系/安装→旧关系释放；对照访问标志更新 |
| P7-C1 | 加COMPLETED/RETRY/常规返回的锁表、TRIED/重新查询及NOWAIT变体 |
| P7-C2 | 加AnonExclusive/保守验证两分支和父退出/映射数不足推断的例子 |
| P8-C1 | 加folio_mkclean写保护、共享写故障/page_mkwrite/记脏/稳定写条件 |
| P8-C2 | 分清暂缓、实际I/O错误和结束；补mapping/文件错误游标及同步交接，不保证redirty/重试 |
| P9-C1 | 加普通匿名swap项/cache/PTE/I/O/释放与缓存命中/缺失链，区分slot和folio引用 |
| P9-C2 | 加非执行file的两次观察KEEP/ACTIVATE/无访问对照和MGLRU aging因果 |
| P10-C1 | 明确源迁移条目等待/重试、目的锁与缓存就绪、成功指Q/失败指P |
| P10-C2 | 加两隔离列表、compaction_alloc/free、2成功1失败清理、返回含义及NULL回调边界 |
| P11-C1 | 加普通order0稳定引用/逻辑mapcount/i_pages/i_mmap链，保留VMA和延迟PTE释放 |
| P11-C2 | 明确可睡眠/当前mm/冲突锁、GET/PIN用途、获取前FOLL_WRITE及真实dirty/unpin接口 |
| P12-C1 | 加mm owner选组、94/98收费4失败回滚、成功绑定/uncharge和共享文件对照 |
| P12-C2 | 加totalpages1000的两任务评分、资格/组杀/异步顺序与任意OOM的oom_kill区分 |
| P13-C1 | 明示本地!PREEMPT_RT依赖，自动平衡仅非RT条件分支 |
| P13-C2 | 拆DEFAULT/LOCAL，补优先级、VMA偏移交错与任务轮转、COW/已有文件缓存 |
| P13-C3 | 补STRICT/MOVE/MOVE_ALL/CAP_SYS_NICE与策略已安装/部分移动后报错，不保证整体回滚 |
| P14-C1 | 补真实存量/事件key、50/10配对及零分母/口径 |
| P14-C2 | 补10样3阳、聚合输出清零、区域变化与DAMOS区别；新增damon-sampling.svg |

## 第1轮回应版本

下表为交给挑战者复核的正文SHA-256。前三组配图未变，第四组14新增DAMON图；后续审阅附录/导航变更不冒充已由挑战者复核的新技术正文。

| 文件 | SHA-256 |
| --- | --- |
| 02 | `533b896e74ad5368991f2107b0a87ad8c119dd0b62da14a00041df0bdcc1d15b` |
| 03 | `2bc2156b683580c27a81c657f662c20918dd5cb7fad7f71d2f1a592d9afb071b` |
| 04 | `6c91775f9ae01d40e0809af93bb8d20cc9491bca0c584eb73d1bee20d4fd1b82` |
| 05 | `8bfd537b54a0a389351cffbbae69da4bd68779e4c8f62e1231c0555b21a7996b` |
| 06 | `00193cead052d6dba47a78f80f3fdae49963dfe3299901aee2ff05565130e7b4` |
| 07 | `014611eb0a8495fe3fe37639e74b3f22bc884de86bc159ca5aec60771dca676c` |
| 08 | `20fc2b3e02959d371711f2cd3a94676ac79393d91cf3ee91f35732e6f87f60d2` |
| 09 | `8b72dcd69b7d6af427a414b632ac3ebe0339b5cc187ad06edbdf621655bbdcf7` |
| 10 | `12b0cbe0d1b6d6f42aa4d3beba48a0f1e9ec2a1c417ee15d501c8c18944572b8` |
| 11 | `4615b800e1e1261590831c553d840db2cafe0ebc0b595ed8b492ca1cb8635744` |
| 12 | `4625bcb078dd2a8347da1c4209c27bd60de4e5990699e324fabd844e7f714eae` |
| 13 | `b1e0c40fbcf4d9157b3a5fd790cfdc63104c3c382134ce296061da7af8d80007` |
| 14 | `e2d65dbc5d776e77edbdcc848343f9e27f15a0dc5015bfab6c612db9e16e6c3d` |

## 第1轮复核结论

- Boole：02/03/04的五项全部resolved；核对计数、搜索、两freelist/冻结及VA延迟复用，匹配回应版本，无新增实质问题。
- Bohr：05/06/07的五项全部resolved；核对mm寿命/查询、COW更新协议与缺页返回/复用，匹配回应版本，无新增实质问题。
- Hypatia：08/09/10的六项全部resolved；核对写保护/错误交接、冷热/交换、迁移/隔离清理，匹配回应版本，无新增实质问题。
- Plato：11/13/14全部问题及12的评分事件问题resolved；P12-C1只部分满足，计数尝试与charge最终返回的阶段仍有歧义。

因此12篇在第1轮结束，不继续制造挑战；12-memcg-oom单独进入第2轮。

## 第2轮：仅P12-C1

**挑战者复核观点：** 数值回滚成立，但“计数失败后调用者归还页、之后再重试”易让读者误解为charge内部重试之前准备页已释放。原证据为try_charge_memcg内部回收重试、charge_memcg最终结果及匿名缺页返回错误后的清理。

**作者回应：接受。** 第3.1节改为一次计数尝试失败只回滚计数；charge内部重试期间准备页仍由缺页调用者持有；最终返回错误才归还。父额度恢复后可以将同一准备页绑定成功，不在中途释放。

**回应版本：** `52ecbcea6ad1a9940221d403ceb95e9c5ccedc0082766a661d01668879af83e2`。

**Plato复核：resolved。** 实际回读及哈希匹配，确认单次计数回滚、内部重试保留准备页、成功绑定和最终错误才归还三个阶段均与原始实现相符。第12篇在第2轮结束，没有第3轮；其余已关闭文章未重开。

## 首轮初审输入标识

挑战者提供的截断SHA明确作为版本标识，不伪称以下均为完整哈希：

```text
02 b1257e376fc3d9af   03 404a31484aa823e4   04 d933e24110e2cc4e
05 6a525a6a45d2e1e6   06 3271db11d78e1b21   07 1ce2d33263369ea9
08 9ad98eb981233a95   09 5c8da180c8620ef3   10 0ce97434b131757e
11 601e455135fd       12 385228c7d098       13 17521f31f784       14 df861b943641
```

## 各篇终止结果

以下结论仅指挑战范围内无未关闭问题，不代表用户验收或全部配置/目标机验证。每篇的机制正文版本按前表记录；复核后只补审阅附录，未据此改动机制内容。

<a id="p02"></a>
### 02 物理组织与初始化

Boole第1轮确认P2-C1/C2关闭：页状态移交、早期保留更新与回退可以推演。无待裁决项。

<a id="p03"></a>
### 03 内核对象

Boole第1轮确认P3-C1/C2关闭：两freelist/frozen及缓存名称和回调寿命得到区分。无待裁决项。

<a id="p04"></a>
### 04 内核虚拟空间

Boole第1轮确认P4-C1关闭：后备归还与lazy VA安全复用时间区分。无待裁决项。

<a id="p05"></a>
### 05 地址空间

Bohr第1轮确认P5-C1/C2关闭：mm计数寿命和包含/后继查询可以推演。无待裁决项。

<a id="p06"></a>
### 06 arm64页表

Bohr第1轮确认P6-C1关闭：COW更新链与访问标志更新协议区别成立。无待裁决项。

<a id="p07"></a>
### 07 缺页与COW

Bohr第1轮确认P7-C1/C2关闭：返回锁责任、重试和复用条件得到解释。无待裁决项。

<a id="p08"></a>
### 08 缓存与回写

Hypatia第1轮确认P8-C1/C2关闭：重新记脏、稳定写与异步错误交接成立。无待裁决项。

<a id="p09"></a>
### 09 回收与交换

Hypatia第1轮确认P9-C1/C2关闭：冷热两次观察与交换缓存/后备的寿命链成立。无待裁决项。

<a id="p10"></a>
### 10 迁移与规整

Hypatia第1轮确认P10-C1/C2关闭：迁移等待和两类隔离资源的部分失败清理成立。无待裁决项。

<a id="p11"></a>
### 11 生命周期与rmap

Plato第1轮确认P11-C1/C2关闭：引用/映射/索引链与pin使用责任成立。无待裁决项。

<a id="p12"></a>
### 12 memcg与OOM

Plato第1轮确认P12-C2关闭，第2轮确认P12-C1的计数尝试与最终charge返回阶段关闭。无待裁决项。

<a id="p13"></a>
### 13 NUMA

Plato第1轮确认P13-C1/C2/C3关闭：RT排斥条件、有效策略、交错与失败后状态成立。无待裁决项。

<a id="p14"></a>
### 14 观测与扩展入口

Plato第1轮确认P14-C1/C2关闭：真实key口径与DAMON抽样结果、聚合和动作统计区别成立。扩展仍只定位，不声称全量深挖。无待裁决项。

**本批共13篇新增专题、25个独立问题编号。12篇止于第1轮，memcg止于第2轮，全部resolved，没有人为凑第三轮或伪造待裁决问题。**

## 交付检查

各篇`review-result`锚点之前的机制正文重新计算哈希，与相应挑战者确认的第1轮/第2轮版本全部一致，审阅附录未修改机制正文。01只更新已写专题导航，其历史版本标识已明确注明；总览仅接通入口，不视为另一次全部技术复核。

本批Markdown链接、跨篇锚点、代码块与SVG XML已检查；新增9张SVG经离线浏览器渲染及文字/连线边界检查。没有运行内核、加载模块、改动配置、写入业务文件或执行目标机监测/压力实验。

最终检查覆盖18份Markdown、286个文档链接和32份SVG；13篇新增专题均检查了代码块/图片加载及桌面、480px窄屏的页面溢出。这个离线阅读器检查不等于正式站点构建或目标机机制验证。

## 验证边界

只读源码和文字/图示检查，不构建内核、加载模块、写配置、运行目标机监测或压力实验。作者负责渲染和链接检查，挑战者视觉验证能力不同，不能将其声明合并成“全部已运行验证”。
