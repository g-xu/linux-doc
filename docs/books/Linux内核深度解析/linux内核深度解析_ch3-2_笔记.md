# 第 3 章 内存管理 — 深度总结（3.11~3.22）

> 原书：《Linux内核深度解析》余华兵著，基于 ARM64 / Linux 4.x
> 本文档：**基于 Linux 6.1.158 内核源码审查更新**，架构限定 ARM64
> 6.1 相比书中 4.x 有重大变化的地方标注 **[6.1 变化]**

---

## 3.11 页表

### 3.11.1 统一的页表框架

Linux 内核从 4.11 起支持五级页表框架：

| 级别 | 数据类型 | 4KB 页每级项数 | 覆盖范围 | 说明 |
|---|---|---|---|---|
| PGD (页全局目录) | `pgd_t` | PTRS_PER_PGD | 依赖 VA_BITS | 顶级，TTBR 指向 |
| P4D (第四级目录) | `p4d_t` | — | — | 五级页表才使用，四级时编译器优化掉 |
| PUD (页上层目录) | `pud_t` | 512 | 1GB | `CONFIG_PGTABLE_LEVELS > 3` |
| PMD (页中间目录) | `pmd_t` | 512 | 2MB | `CONFIG_PGTABLE_LEVELS > 2` |
| PTE (页表项) | `pte_t` | 512 | 4KB | 最底层 |

```
虚拟地址翻译过程（4 级页表, 4KB 页, 48-bit VA）:

  虚拟地址 (48 位)
  ┌────────┬────────┬────────┬────────┬────────────┐
  │ PGD    │ PUD    │ PMD    │ PTE    │ Page Offset│
  │ [47:39]│ [38:30]│ [29:21]│ [20:12]│ [11:0]     │
  │ 9 bits │ 9 bits │ 9 bits │ 9 bits │ 12 bits    │
  └───┬────┴───┬────┴───┬────┴───┬────┴─────┬──────┘
      │        │        │        │          │
      ▼        ▼        ▼        ▼          ▼
  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  ┌──────┐
  │ PGD  │→│ PUD  │→│ PMD  │→│ PTE  │→│ 物理页│
  │ 512项│ │ 512项│ │ 512项│ │ 512项│  │ 4KB  │
  └──────┘ └──────┘ └──────┘ └──────┘  └──────┘
 TTBR0/1                每项 8 字节 (64-bit 描述符)
```

五级页表结构（如果不使用某一级，内核通过 `include/asm-generic/pgtable-nopXd.h` 将该级折叠）：

```
五级页表的完整结构:
  虚拟地址 → PGD → P4D → PUD → PMD → PTE → 物理页

  内核有两个页表:
  (1) 用户进程: mm_struct.pgd → TTBR0_EL1, 每个进程独立
  (2) 内核:     init_mm.pgd = swapper_pg_dir → TTBR1_EL1, 所有进程共享
```

### 3.11.2 ARM64 处理器的页表描述符

ARM64 第 3 级转换表 (L3) 的描述符分为 3 种：

```
(1) 无效描述符: bit[0] = 0
    ┌────────────────────────────────────────────────────────────┐
    │                    IGNORED                           │ 0  │
    └────────────────────────────────────────────────────────────┘

(2) 保留描述符: bit[1:0] = 01 (保留，不使用)

(3) 页描述符: bit[1:0] = 11
    ┌────────┬──────────────────────────────┬────────────┬────┐
    │上层属性 │  输出地址 (物理页帧号 OA)      │ 下层属性    │ 11 │
    │[63:52] │  [51:12] 或 [47:12]          │ [11:2]     │    │
    └────────┴──────────────────────────────┴────────────┴────┘
```

L1/L2 级的描述符可以是**表描述符**（指向下一级页表）或**块描述符**（直接映射巨型页）。

关键属性位详解:

| 位 | 名称 | 说明 |
|---|---|---|
| [1:0] | Type | 11=页/表, 01=块 |
| [2] | AttrIndx[0] | 内存属性索引 (配合 MAIR_EL1) |
| [4:3] | AttrIndx[2:1] | 内存属性索引高位 |
| [5] | NS | Non-Secure (在安全状态下使用) |
| [7:6] | AP[2:1] | 访问权限: 00=EL1读写, 01=EL0/1读写, 10=EL1只读, 11=EL0/1只读 |
| [8:9] | SH[1:0] | 共享属性: 00=Non, 10=Outer, 11=Inner Shareable |
| [10] | AF | Access Flag, 首次访问时硬件/软件置 1 |
| [51] | DBM | Dirty Bit Modifier (ARMv8.1+, 硬件管理脏位) |
| [53] | PXN | Privileged eXecute Never (特权态不可执行) |
| [54] | UXN/XN | Unprivileged/eXecute Never (用户态不可执行) |

**[6.1 变化]**: 6.1 中 ARM64 启用 FEAT_HAFDBS (Hardware AF/DBM Setting) 时，MMU 硬件自动设置 AF 和 Dirty 位，无需软件缺页异常处理，大幅减少缺页中断次数。

### 页表操作宏

```c
// 各级页表项的操作 (以 PTE 为例，其他级类似):
pte_val(pte)           // 获取 pte 的原始值 (unsigned long)
__pte(val)             // 将 unsigned long 转为 pte_t
pte_pfn(pte)           // 提取物理页帧号
pfn_pte(pfn, prot)     // 从页帧号和保护位构造 pte
pte_present(pte)       // 是否有效 (bit[0] = 1)
pte_write(pte)         // 是否可写
pte_dirty(pte)         // 是否脏页
pte_young(pte)         // 是否最近被访问 (AF)

// 修改页表项:
set_pte(ptep, pte)     // 设置页表项
pte_clear(mm,addr,ptep)// 清除页表项
pte_mkwrite(pte)       // 设为可写
pte_mkdirty(pte)       // 标记为脏
pte_mkyoung(pte)       // 标记为最近访问
```

---

## 3.12 页表缓存 (TLB)

### 3.12.1 TLB 表项格式

TLB 缓存虚拟地址到物理地址的翻译结果，避免每次访存都遍历页表。

ARM64 TLB 表项包含：虚拟地址标签、ASID、物理地址、属性（权限、缓存策略等）。

### 3.12.2 TLB 管理

ARM64 提供 TLBI (TLB Invalidate) 指令族：

```
TLBI <type><level>{IS|OS}, {Xt}

type 常用值:
  ALL   : 所有表项
  VMALL : 当前 VMID 的所有表项 (虚拟化)
  ASID  : 指定 ASID 的所有表项
  VA    : 指定虚拟地址的表项
  VAA   : 指定虚拟地址且任意 ASID 的表项

level:
  E1    : EL1 翻译方案
  E2    : EL2 翻译方案

IS/OS:
  IS    : Inner Shareable (多核共享, 广播到所有核)
  OS    : Outer Shareable
  无后缀 : 仅本核
```

内核封装的 TLB 管理函数（`arch/arm64/include/asm/tlbflush.h`）：

| 函数 | 功能 | 使用场景 |
|---|---|---|
| `flush_tlb_all()` | 无效化所有 TLB | 内核页表全局修改 |
| `flush_tlb_mm(mm)` | 无效化指定 mm 的 TLB | 进程地址空间销毁 |
| `flush_tlb_page(vma, addr)` | 无效化指定页 | 单页映射修改 |
| `flush_tlb_range(vma, start, end)` | 无效化地址范围 | munmap/mprotect |
| `local_flush_tlb_all()` | 仅无效化本核 TLB | 不需要广播的场景 |

`flush_tlb_all()` 的 ARM64 实现：
```c
static inline void flush_tlb_all(void) {
    dsb(ishst);                    // 确保之前的存储指令完成
    __tlbi(vmalle1is);             // TLBI VMALLE1IS: 无效化所有 EL1 TLB, 内部共享
    dsb(ish);                      // 等待 TLB 无效化完成
    isb();                         // 指令同步屏障
}
```

### 3.12.3 ASID (地址空间标识符) — 深入解析

#### 为什么需要 ASID

```
问题: 没有 ASID 的世界

  进程 A 运行时, TLB 缓存了 A 的虚拟地址翻译:
    VA=0x400000 → PA=0x80001000 (进程 A 的代码段)
    VA=0x7FFE0000 → PA=0x90005000 (进程 A 的栈)

  切换到进程 B 时:
    B 的 VA=0x400000 → PA=0xA0003000 (进程 B 的代码段, 不同物理页!)
    如果 TLB 中还残留 A 的条目 → B 访问 0x400000 命中了 A 的旧条目
    → 访问到错误的物理页! → 必须 flush 整个 TLB

  flush TLB 的代价:
    所有缓存的翻译全部丢失 → 切换后前几百次访存全部 TLB miss
    → 每次 miss 需要遍历 4 级页表 (4 次内存访问)
    → 进程切换后有明显的性能跌落 (TLB warmup)

解决: ASID — 给每条 TLB 条目打上"属于谁"的标签

  进程 A (ASID=5) 运行时, TLB 条目:
    [ASID=5] VA=0x400000 → PA=0x80001000
    [ASID=5] VA=0x7FFE0000 → PA=0x90005000

  切换到进程 B (ASID=7):
    只需更新 TTBR0_EL1 (写入 B 的 pgd + ASID=7)
    不需要 flush TLB!
    B 访问 VA=0x400000 → TLB 查找 [ASID=7, VA=0x400000] → miss (正确!)
    → 遍历 B 的页表 → 填入 [ASID=7] VA=0x400000 → PA=0xA0003000

  再切回进程 A:
    更新 TTBR0_EL1 为 A 的 pgd + ASID=5
    A 访问 VA=0x400000 → TLB 查找 [ASID=5, VA=0x400000] → 命中! (还在!)
    → 零开销!

  效果: 多个进程的 TLB 条目共存, 进程切换几乎零 TLB 代价
```

#### ASID 的硬件基础

```
ARM64 ASID 存储位置 (TCR_EL1.A1 = 1 时):

  TTBR1_EL1 (内核空间页表基地址):
  ┌──────────┬──────────────────────────────────────────────┐
  │ ASID     │ swapper_pg_dir 的物理基地址                    │
  │ [63:48]  │ [47:1]                                       │
  └──────────┴──────────────────────────────────────────────┘
  ASID 实际写在 TTBR1 中 (因为 TCR_EL1.A1=1 选择从 TTBR1 取 ASID)

  TTBR0_EL1 (用户空间页表基地址):
  ┌──────────┬──────────────────────────────────────────────┐
  │ (ASID备份)│ 进程 pgd 的物理基地址                          │
  │ [63:48]  │ [47:1]                                       │
  └──────────┴──────────────────────────────────────────────┘
  在 SW_TTBR0_PAN 模式下, TTBR0 高位也存一份 ASID

  ASID 位宽 (TCR_EL1.AS):
    AS=0 → 8-bit ASID → 256 个可用
    AS=1 → 16-bit ASID → 65536 个可用
    通过 ID_AA64MMFR0_EL1.ASIDBits 字段检测硬件支持

  TLB 条目格式 (简化):
  ┌──────┬────────────────┬────────────────┬──────────┐
  │ ASID │ 虚拟地址标签    │ 物理地址        │ 属性      │
  └──────┴────────────────┴────────────────┴──────────┘
  nG (non-Global) 位:
    页表项 nG=1 → TLB 条目关联 ASID (用户页, per-进程)
    页表项 nG=0 → TLB 条目全局有效 (内核页, ASID 无关)
```

#### 内核的 ASID 管理方案

```
核心挑战: ASID 数量有限 (256 或 65536), 进程可以有数万个
  → ASID 不够用时怎么办?

Linux 解决方案: ASID + 全局版本号 (Generation)

  mm_struct.context.id (64-bit):
  ┌──────────────────────────────────────┬────────────────┐
  │ generation (高位, 版本号)             │ ASID (低位)     │
  │ 每次 ASID 耗尽时递增                  │ 实际的 ASID 值  │
  └──────────────────────────────────────┴────────────────┘

  全局变量:
    asid_generation (atomic64_t): 当前全局版本号
    asid_map (位图): 当前版本中哪些 ASID 已分配
    active_asids[NR_CPUS]: 每个 CPU 当前使用的 ASID
    reserved_asids[NR_CPUS]: rollover 时保留的 ASID

  判断 ASID 是否有效:
    进程的 mm->context.id 高位 == asid_generation → 有效
    不相等 → ASID 属于旧版本, 需要重新分配
```

#### 进程切换时的完整流程

```
context_switch()                       (kernel/sched/core.c)
  └── switch_mm(prev_mm, next_mm, next_task)   (arch/arm64/include/asm/mmu_context.h)
      │
      ├── if (prev_mm == next_mm) → 同一地址空间, 不切换
      │     只更新 saved_ttbr0 (线程可能被其他线程改了 ASID)
      │
      └── __switch_mm(next_mm)
          │
          ├── if (next_mm == &init_mm) → 内核线程
          │     cpu_set_reserved_ttbr0()  设置保留的 TTBR0 (无用户映射)
          │     return
          │
          └── check_and_switch_context(next_mm)
              │
              │  ══════════════════════════════════════════════════
              │  这是 ASID 管理的核心函数
              │  (arch/arm64/mm/context.c)
              │  ══════════════════════════════════════════════════
              │
              ├── asid = mm->context.id              读取进程的 ASID+版本号
              │
              ├── ★ 快速路径 (无锁):
              │   if (active_asids[this_cpu] != 0     CPU 有活跃 ASID
              │       && asid 版本号匹配当前 generation  ASID 还是当前版本的
              │       && cmpxchg(active_asids, old, asid) 成功)
              │     → 直接跳到 switch_mm_fastpath
              │     → 写 TTBR0_EL1/TTBR1_EL1
              │     → 完成! (最常见的路径, 无锁无 TLB flush)
              │
              ├── ★ 慢速路径 (需要锁):
              │   spin_lock(&cpu_asid_lock)
              │   │
              │   ├── 再次读 mm->context.id (可能被其他 CPU 更新了)
              │   │
              │   ├── if (版本号不匹配) → 需要新 ASID
              │   │   └── asid = new_context(mm)  ← 见下方详解
              │   │       atomic64_set(&mm->context.id, asid)
              │   │
              │   ├── if (本 CPU 在 tlb_flush_pending 中)
              │   │   └── local_flush_tlb_all()  刷新本 CPU 的 TLB
              │   │       (rollover 后首次调度到此 CPU 时执行)
              │   │
              │   ├── active_asids[this_cpu] = asid  标记本 CPU 活跃 ASID
              │   └── spin_unlock()
              │
              └── switch_mm_fastpath:
                  └── cpu_do_switch_mm(pgd_phys, mm)
                      │
                      │  实际写寄存器:
                      │  asid = ASID(mm)  → mm->context.id & 0xFFFF
                      │  ttbr0 = pgd 物理地址 | CNP 位
                      │  ttbr1 = (原 ttbr1 & ~ASID_MASK) | (asid << 48)
                      │
                      │  write_sysreg(ttbr1, ttbr1_el1)  先写 ASID 到 TTBR1
                      │  isb()                            指令同步
                      │  write_sysreg(ttbr0, ttbr0_el1)  再写页表基地址到 TTBR0
                      │  isb()                            指令同步
                      │
                      └── 从此刻起, MMU 使用新的 ASID + 新的页表
```

#### new_context() — ASID 分配算法

```
new_context(mm) 在 ASID 需要重新分配时调用:

  ┌─────────────────────────────────────────────────────────────────┐
  │ 输入: mm->context.id 中可能有旧版本的 ASID                       │
  │ 输出: 新版本的有效 ASID                                          │
  │                                                                 │
  │ 步骤 1: 尝试复用旧 ASID                                          │
  │   if (mm 之前有 ASID) {                                          │
  │     newasid = 旧ASID值 | 新版本号    (同一个 ASID, 新版本)        │
  │                                                                 │
  │     (a) 检查 reserved_asids: 如果有 CPU 在 rollover 时            │
  │         正在使用这个 ASID → 可以继续用 (更新版本号即可)            │
  │         return newasid ✓                                        │
  │                                                                 │
  │     (b) 检查 pinned: 如果 ASID 被钉住 (如 VFIO)                  │
  │         → 继续使用                                               │
  │         return newasid ✓                                        │
  │                                                                 │
  │     (c) 检查 asid_map: 如果 ASID 值还没被其他进程占用              │
  │         → 在位图中标记, 继续使用                                  │
  │         return newasid ✓                                        │
  │   }                                                             │
  │                                                                 │
  │ 步骤 2: 分配全新 ASID                                            │
  │   asid = find_next_zero_bit(asid_map)                           │
  │   在位图中找一个空闲的 ASID 值                                    │
  │                                                                 │
  │   if (找到了)                                                    │
  │     标记位图, return asid | 当前版本号 ✓                          │
  │                                                                 │
  │ 步骤 3: ASID 耗尽 → Rollover!                                   │
  │   asid_generation += (1 << 16)    递增全局版本号                  │
  │   flush_context()                 ← 见下方详解                   │
  │   asid = find_next_zero_bit(asid_map, 从 1 开始)                │
  │   return asid | 新版本号 ✓                                       │
  │   (rollover 后位图几乎全空, 一定能找到)                            │
  └─────────────────────────────────────────────────────────────────┘
```

#### flush_context() — Rollover 时的处理

```
flush_context() 在 ASID 耗尽时调用:

  ┌─────────────────────────────────────────────────────────────────┐
  │ 目的: 回收所有 ASID, 开始新一轮分配                               │
  │                                                                 │
  │ (1) 重置 asid_map 位图                                           │
  │     set_reserved_asid_bits():                                   │
  │       如果启用 KPTI → 预留偶数 ASID 给内核 (奇偶分离)             │
  │       如果有 pinned ASID → 从 pinned_asid_map 恢复               │
  │       否则 → 清空位图                                            │
  │                                                                 │
  │ (2) 保存每个 CPU 当前正在使用的 ASID                              │
  │     for_each_possible_cpu(i):                                    │
  │       asid = xchg(active_asids[i], 0)   原子取出并清零            │
  │       if (asid == 0)                    CPU 已经经历过 rollover   │
  │         asid = reserved_asids[i]        使用保留值               │
  │       set_bit(asid, asid_map)           在新位图中标记为已用      │
  │       reserved_asids[i] = asid          保存为保留 ASID          │
  │                                                                 │
  │ (3) 标记所有 CPU 需要 TLB flush                                   │
  │     cpumask_setall(&tlb_flush_pending)                           │
  │     → 每个 CPU 下次切换到用户进程时执行 local_flush_tlb_all()     │
  │     → 不是立即在所有 CPU 上 IPI flush (性能优化!)                  │
  │     → 延迟到各 CPU 自己的下一次上下文切换时才 flush               │
  │                                                                 │
  │ 效果:                                                            │
  │   - 全局版本号 +1                                                │
  │   - 所有进程的 ASID 变为"旧版本" (版本号不匹配)                   │
  │   - 下次调度到某进程时, check_and_switch_context 走慢速路径        │
  │   - 在慢速路径中为该进程分配新 ASID (可能复用旧值)                 │
  │   - 正在运行的进程通过 reserved_asids 保护, 不会被抢走 ASID       │
  └─────────────────────────────────────────────────────────────────┘
```

#### KPTI 对 ASID 的影响

```
KPTI (Kernel Page Table Isolation, 对抗 Meltdown):
  启用 KPTI 时, 内核态和用户态使用不同的页表:
    用户态 TTBR0 → 仅映射用户空间 + 极少量内核入口代码
    内核态 TTBR0 → 映射完整内核空间

  对 ASID 的影响:
    ASID 被分成奇偶对:
      偶数 ASID (bit0=0) → 内核态使用的页表
      奇数 ASID (bit0=1) → 用户态使用的页表
    
    实际效果: 可用 ASID 数量减半!
      8-bit: 256 → 128 对
      16-bit: 65536 → 32768 对

  实现:
    set_kpti_asid_bits() → 在位图中用 0xAA 填充
    → 所有偶数位被预留 → 只分配奇数 ASID
```

#### ASID 管理的全景时序图

```
场景: 系统有 4 个 CPU, 8-bit ASID (256 个)

时间线:
  t0: 系统启动, generation=1, asid_map 全空
      进程 A 调度到 CPU0 → 分配 ASID=2, A.context.id = (gen=1 | asid=2)
      进程 B 调度到 CPU1 → 分配 ASID=3, B.context.id = (gen=1 | asid=3)

  t1: 大量进程创建, ASID 2~255 全部分配完毕

  t2: 新进程 Z 需要 ASID → find_next_zero_bit 找不到空闲
      → Rollover!
      → generation 递增到 2
      → flush_context():
          reserved_asids[CPU0] = (gen=1|asid=2)  保护 CPU0 上运行的 A
          reserved_asids[CPU1] = (gen=1|asid=3)  保护 CPU1 上运行的 B
          asid_map: bit 2,3 置位 (被保留), 其余清空
          tlb_flush_pending: 所有 CPU 标记
      → 给 Z 分配 ASID=4, Z.context.id = (gen=2 | asid=4)

  t3: CPU0 调度进程 C (C 的 ASID 是 gen=1 时分配的)
      → check_and_switch_context():
          C.context.id 的 gen=1 ≠ 当前 gen=2 → 慢速路径
          → new_context(C): 尝试复用 C 的旧 ASID 值
            旧 ASID 值没被其他进程占用 → 复用, 更新版本号
          → local_flush_tlb_all() (tlb_flush_pending 清除)
          → 写 TTBR

  t4: CPU0 再次调度进程 A
      → A.context.id = (gen=1|asid=2)
      → gen=1 ≠ gen=2 → 慢速路径
      → new_context(A):
          check_update_reserved_asid: reserved_asids[CPU0] 是 (gen=1|asid=2)
          匹配! → 更新为 (gen=2|asid=2) → 继续使用 ASID=2
      → A 无缝继续运行, ASID 不变
```

#### 关键数据结构汇总

```c
// 每个进程的 ASID 信息 (arch/arm64/include/asm/mmu.h)
typedef struct {
    atomic64_t id;          // 高位=generation, 低16位=ASID
    refcount_t pinned;      // 引用计数 (VFIO 等需要钉住 ASID)
    void *vdso;
    unsigned long flags;
} mm_context_t;

// 全局 ASID 管理变量 (arch/arm64/mm/context.c)
static u32 asid_bits;                       // ASID 位宽 (8 或 16)
static atomic64_t asid_generation;          // 全局版本号
static unsigned long *asid_map;             // 当前版本的 ASID 分配位图
static DEFINE_PER_CPU(atomic64_t, active_asids);   // 每 CPU 活跃 ASID
static DEFINE_PER_CPU(u64, reserved_asids);        // rollover 时保留
static cpumask_t tlb_flush_pending;         // 哪些 CPU 需要 flush TLB

// ASID 提取宏
#define ASID(mm)  (atomic64_read(&(mm)->context.id) & 0xffff)
#define NUM_USER_ASIDS  (1UL << asid_bits)    // 256 或 65536
#define asid_gen_match(asid)  (同一 generation?)
```

### 3.12.4 VMID (虚拟机标识符)

- VMID 用于 KVM 虚拟化，区分不同虚拟机的 Stage-2 地址空间
- 存储在 VTTBR_EL2 寄存器的高位
- 支持 8-bit 或 16-bit VMID (VTCR_EL2.VS 控制)
- VMID + ASID 二级标签：同一物理核上多个虚拟机的多个进程的 TLB 条目互不干扰

---

## 3.13 巨型页 (Huge Pages)

### 3.13.1 ARM64 对巨型页的支持

ARM64 通过两种方式支持巨型页：

**(1) 块描述符 (Block Descriptor)**

| 页大小 | PMD 级块描述符 | PUD 级块描述符 |
|---|---|---|
| 4KB | 2MB 巨型页 | 1GB 巨型页 |
| 16KB | 32MB 巨型页 | — (不支持) |
| 64KB | 512MB 巨型页 | — (不支持) |

**(2) 连续位 (Contiguous Bit)** — 将多个相邻的页/块标记为连续，共享一条 TLB 条目：

| 页大小 | PTE 连续 | PMD 连续 |
|---|---|---|
| 4KB | 16 × 4KB = 64KB | 16 × 2MB = 32MB |
| 16KB | 128 × 16KB = 2MB | 32 × 32MB = 1GB |
| 64KB | 32 × 64KB = 2MB | 32 × 512MB = 16GB |

### 3.13.2 标准巨型页 (HugeTLB)

**使用方法**：

```bash
# 1. 预留巨型页 (启动时或运行时)
# 启动参数: hugepages=100 hugepagesz=2M
# 运行时:
echo 100 > /proc/sys/vm/nr_hugepages          # 2MB 页
echo 10 > /sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages  # 1GB 页

# 2. 挂载 hugetlbfs
mount -t hugetlbfs -o pagesize=2M none /mnt/huge

# 3. 应用使用
fd = open("/mnt/huge/myfile", O_CREAT | O_RDWR);
addr = mmap(NULL, MAP_LENGTH, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
# 或直接使用 MAP_HUGETLB:
addr = mmap(NULL, size, PROT_READ|PROT_WRITE,
            MAP_SHARED|MAP_ANONYMOUS|MAP_HUGETLB, -1, 0);

# 4. 查看状态
cat /proc/meminfo | grep Huge
# HugePages_Total:   100     预留的总数
# HugePages_Free:     95     空闲数
# HugePages_Rsvd:      3     已预留但未分配的
# HugePages_Surp:      0     超额分配的
# Hugepagesize:     2048 kB  默认巨型页大小
```

**实现原理**：

```
hugetlbfs 文件系统结构:
  struct super_block
    └── struct hugetlbfs_sb_info
          └── hstate → 指向 struct hstate (描述巨型页池)

  struct hstate:
    order            → 巨型页的 order (2MB = order 9)
    nr_huge_pages    → 已分配的巨型页总数
    free_huge_pages  → 空闲巨型页数
    surplus_huge_pages → 超额分配数
    hugepage_freelists[MAX_NUMNODES] → 每个 NUMA 节点的空闲链表

  巨型页池管理:
    alloc_fresh_huge_page() → 从 buddy 分配 2^order 连续页
    free_huge_page()        → 归还到巨型页池
    巨型页不受 buddy 的 MAX_ORDER 限制 (通过 alloc_contig_pages)
```

### 3.13.3 透明巨型页 (THP)

内核自动将连续的小页合并为巨型页，对应用完全透明。

```bash
# 配置 THP 行为
echo "always"  > /sys/kernel/mm/transparent_hugepage/enabled  # 总是启用
echo "madvise" > /sys/kernel/mm/transparent_hugepage/enabled  # 仅 madvise 标记
echo "never"   > /sys/kernel/mm/transparent_hugepage/enabled  # 禁用

# 碎片整理策略
echo "defer+madvise" > /sys/kernel/mm/transparent_hugepage/defrag
# always: 每次分配都尝试整理碎片 (延迟高)
# defer:  由 kcompactd 后台整理
# defer+madvise: 后台整理 + madvise 区域同步整理
# madvise: 仅 madvise 标记的区域整理
# never:  不整理

# 查看统计
cat /proc/vmstat | grep thp
# thp_fault_alloc     → 缺页时成功分配巨型页次数
# thp_fault_fallback  → 缺页时巨型页分配失败回退为小页次数
# thp_collapse_alloc  → khugepaged 合并成功次数
# thp_split_page      → 巨型页拆分次数
```

**THP 缺页异常处理流程**:

```
handle_mm_fault()
  └── __handle_mm_fault()
      ├── 检查 PUD 是否存在 → 不存在则分配
      ├── create_huge_pud() → 如果允许 PUD 级巨型页
      │   └── 检查配置和对齐 → 分配 1GB 巨型页
      ├── 检查 PMD 是否存在 → 不存在则分配
      ├── create_huge_pmd() → 如果允许 PMD 级巨型页 (2MB)
      │   ├── do_huge_pmd_anonymous_page()
      │   │   1. 分配巨型页 (order 9)
      │   │   2. 清零页内容
      │   │   3. 设置 PMD 表项 (块描述符)
      │   │   4. 标记 PG_uptodate
      │   │   5. 加入 LRU 链表
      │   │   6. 加入反向映射
      │   └── 失败 → 回退到普通 4KB 缺页处理
      └── handle_pte_fault() → 普通 4KB 页处理
```

**khugepaged 后台合并**:
```
khugepaged 内核线程定期扫描:
  每 scan_sleep_millisecs (默认 10000ms) 唤醒一次
  扫描符合条件的 VMA:
    (1) 连续 512 个 4KB PTE 都已映射
    (2) 对应的 512 个物理页可合并 (非共享、可迁移)
  合并过程:
    (1) 分配一个巨型页 (order 9)
    (2) 复制 512 个小页的数据到巨型页
    (3) 更新页表: 512 个 PTE → 1 个 PMD 块描述符
    (4) 释放原来的 512 个小页
```

**[6.1 变化]**: 6.1 支持 `prctl(PR_SET_THP_DISABLE)` per-process 禁用 THP，以及更精细的 per-size 策略控制。

---

## 3.14 页错误异常处理

### 3.14.1 ARM64 异常入口

```
用户态/内核态访问虚拟地址 → MMU 翻译失败
  → 硬件保存: FAR_EL1 = 故障虚拟地址
              ESR_EL1 = 异常综合信息 (类型、访问权限等)
  → 跳转到异常向量表 EL1 入口

arch/arm64/mm/fault.c:
  do_mem_abort(addr, esr, regs)
    └── 根据 ESR 的故障类型分发:
        ├── Translation fault → do_page_fault()    (页表项不存在)
        ├── Access flag fault → do_page_fault()    (AF=0, 首次访问)
        ├── Permission fault  → do_page_fault()    (权限不匹配)
        └── Alignment fault   → do_alignment_fault()
```

### 3.14.2 do_page_fault 详细流程

```
do_page_fault(addr, esr, regs)
│
├── vma = find_vma(mm, addr)
│   找不到 VMA:
│   ├── 用户态 → SIGSEGV (段错误)
│   └── 内核态 → 查异常表 fixup, 没有则 oops
│
├── 地址在 VMA 范围外但在栈下方:
│   └── expand_stack() 扩展栈 VMA
│       失败 → SIGSEGV
│
├── 权限检查: VMA 的 vm_flags vs 访问类型
│   ├── 写访问但 VMA 无 VM_WRITE → SIGSEGV (VM_FAULT_BADACCESS)
│   └── 执行访问但 VMA 无 VM_EXEC → SIGSEGV
│
└── handle_mm_fault(vma, addr, flags)
    └── __handle_mm_fault()
        │
        ├── 逐级创建缺失的页表 (pgd_offset → pud_alloc → pmd_alloc)
        │
        └── handle_pte_fault(vma, addr, pte, pmd, flags)
            │
            ├── PTE 不存在 (pte_none):
            │   ├── VMA 有 vm_ops->fault → do_fault() (文件映射)
            │   │   ├── do_read_fault()   → 读操作的文件映射缺页
            │   │   ├── do_cow_fault()    → 写操作的私有文件映射 (COW)
            │   │   └── do_shared_fault() → 写操作的共享文件映射
            │   └── VMA 无 vm_ops → do_anonymous_page() (匿名映射)
            │       ├── 读操作 → 映射到零页 (共享的全零页, 不分配)
            │       └── 写操作 → alloc_zeroed_user_highpage() 分配新零页
            │
            ├── PTE 存在但不在内存 (!pte_present):
            │   └── do_swap_page() → 从 swap 分区换入
            │       1. 从 swap cache 查找
            │       2. 不在 cache → 从 swap 设备读取
            │       3. 设置 PTE 映射
            │
            ├── PTE 存在且写保护 (写访问 + !pte_write):
            │   └── do_wp_page() → 写时复制 (COW)
            │       ├── 页引用计数 = 1 → 直接设为可写 (无需复制)
            │       └── 页引用计数 > 1 → 分配新页 + 复制 + 更新 PTE
            │
            ├── NUMA balancing (pte_protnone):
            │   └── do_numa_page() → 考虑迁移页到本地 NUMA 节点
            │
            └── 正常访问但 AF=0:
                └── pte_mkyoung() → 设置访问标志
                    update_mmu_cache() → 更新 TLB
```

### 3.14.3 内核态页错误

内核态缺页通常是 BUG，但有几种合法场景：

| 场景 | 处理方式 |
|---|---|
| `copy_from_user()` / `copy_to_user()` | 通过异常表 (exception fixup table) 跳转到错误处理代码，返回 -EFAULT |
| vmalloc 区域首次访问 | 从 init_mm 同步页表到当前进程页表（内核页表是全局共享的，但进程切换时不一定同步了 vmalloc 映射） |
| userfaultfd 注册区域 | 调用 handle_userfault() 通知用户态处理程序 |

**[6.1 变化]**: userfaultfd 与 handle_userfault() 路径深度集成，支持 UFFD_FEATURE_MINOR_HUGETLBFS/SHMEM 的 minor fault 拦截。

---

## 3.15 反碎片技术

### 3.15.1 虚拟可移动区域 (ZONE_MOVABLE)

ZONE_MOVABLE 不包含任何不可迁移的页，确保该区域的物理内存可被完整回收或热移除。

```bash
# 配置方式 (内核参数)
kernelcore=1G           # 不可移动内存大小，剩余归 ZONE_MOVABLE
movablecore=2G          # 直接指定 ZONE_MOVABLE 大小
kernelcore=mirror       # 使用镜像内存作为不可移动区域

# 内存热插拔场景:
# 新插入的内存默认归入 ZONE_MOVABLE → 可以安全热移除
```

ZONE_MOVABLE 的分配规则：
- 只接受 `__GFP_MOVABLE` 标志的分配请求
- 不允许分配内核数据结构（slab、页表等）
- 用户匿名页、page cache 可以分配在此区域
- 通过迁移类型 `MIGRATE_MOVABLE` 保证所有页都可迁移

### 3.15.2 内存碎片整理 (Compaction)

```
碎片整理的核心思路:

  碎片化的内存 (无法分配连续 4 页):
  ┌─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┐
  │U│ │U│ │U│ │ │U│ │U│ │ │U│ │ │U│
  └─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┘

  两个扫描器相向而行:
  迁移扫描器 ──►          ◄── 空闲扫描器
  (从头找已分配页)        (从尾找空闲页)

  将可迁移的已分配页搬到空闲位置:
  ┌─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┐
  │U│U│U│U│U│U│U│ │ │ │ │ │ │ │ │ │
  └─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┴─┘
  头部连续 → 无法迁移        尾部连续空闲 → 可分配大块
```

**碎片指数 (fragmentation index)**:

```
对于每个 order:
  碎片指数 = (空闲页数 - 能够分配的最大连续块) / 空闲页数
  
  碎片指数接近 0: 空闲页太少 → 问题是内存不足，不是碎片
  碎片指数接近 1000: 有足够空闲页但不连续 → 碎片问题，需要整理
  
  /proc/buddyinfo: 查看各 order 的空闲块数量
  /sys/kernel/debug/extfrag/extfrag_index: 查看碎片指数
```

**碎片整理的触发和模式**:

| 触发路径 | 说明 |
|---|---|
| 分配慢速路径 `__alloc_pages_slowpath` | 高 order 分配失败时自动触发 |
| `echo 1 > /proc/sys/vm/compact_memory` | 管理员手动触发全局整理 |
| `kcompactd` 后台线程 | 每个 node 一个，水位线驱动 |
| `khugepaged` | THP 合并时需要连续页触发 |

| 模式 | 阻塞? | 迁移脏页? | 场景 |
|---|---|---|---|
| `COMPACT_PRIO_ASYNC` | 不阻塞 | 不迁移 | 分配路径快速尝试 |
| `COMPACT_PRIO_SYNC_LIGHT` | 轻度阻塞 | 允许大部分 | 默认模式 |
| `COMPACT_PRIO_SYNC_FULL` | 完全阻塞 | 等待所有 I/O | 最后手段 |

**zone 中碎片整理相关字段**:
```c
struct zone {
    // compact_considered: 已考虑整理的次数
    // compact_defer_shift: 推迟整理的位移 (指数退避)
    // compact_order_failed: 上次整理失败的 order
    // 如果整理失败，推迟下次整理的时间指数增长
};
```

---

## 3.16 页回收

### 3.16.1 数据结构 — LRU 链表

每个内存节点的 `lruvec` 维护 5 条 LRU 链表:

```
struct lruvec {
    struct list_head lists[NR_LRU_LISTS];
    // LRU_INACTIVE_ANON   [0] 不活跃匿名页
    // LRU_ACTIVE_ANON     [1] 活跃匿名页
    // LRU_INACTIVE_FILE   [2] 不活跃文件页
    // LRU_ACTIVE_FILE     [3] 活跃文件页
    // LRU_UNEVICTABLE     [4] 不可回收页 (mlock/ramfs/SHM_LOCK)
};

页在 LRU 中的生命周期:
  ┌─────────────────────────────────────────────────────────┐
  │ 新页 ──► inactive 尾部                                   │
  │   │                                                     │
  │   ├── 被访问一次 → 标记 PG_referenced                     │
  │   │                                                     │
  │   ├── 再次被访问(PG_referenced已设) → 提升到 active 头部   │
  │   │                                                     │
  │   └── active 中长时间未访问 → 降级到 inactive 头部         │
  │                                                         │
  │ inactive 尾部 → 回收候选                                  │
  │   ├── 文件页: 直接丢弃 (干净) 或写回后丢弃 (脏)            │
  │   └── 匿名页: 写入 swap 后释放                            │
  └─────────────────────────────────────────────────────────┘
```

### 3.16.1b MGLRU — 多代 LRU **[6.1 重大变化]**

Linux 6.1 引入了 MGLRU，是页面回收算法的重大架构变化：

```
经典 LRU 的问题:
  (1) 扫描 LRU 链表本身就很耗 CPU (遍历数百万页)
  (2) active/inactive 只有二级精度，冷热区分不够
  (3) 大内存系统 (>64GB) LRU 锁争用严重

MGLRU 核心思想:
  用多个"代"(generation) 替代 active/inactive 二分法
  通过扫描页表 (而非 LRU 链表) 判断页面是否被访问

struct lru_gen_struct {
    unsigned long max_seq;                    // 最新代序号
    unsigned long min_seq[ANON_AND_FILE];     // 最老代序号 [匿名, 文件]
    unsigned long timestamps[MAX_NR_GENS];    // 每代诞生时间
    struct list_head folios[MAX_NR_GENS][ANON_AND_FILE][MAX_NR_ZONES];
    //                      4代       2类型      zone数
    long nr_pages[MAX_NR_GENS][ANON_AND_FILE][MAX_NR_ZONES];
    unsigned long avg_refaulted[ANON_AND_FILE][MAX_NR_TIERS]; // 重缺页平均
    unsigned long avg_total[ANON_AND_FILE][MAX_NR_TIERS];     // 总计平均
    bool enabled;
};
```

**MGLRU 的两个核心操作**:

```
(1) 老化 (Aging) — 递增 max_seq，创建新一代
    lru_gen_age_node():
    ├── 遍历进程页表 (不是 LRU 链表！)
    ├── 检查每个 PTE 的 AF (Access Flag) 位
    │   ├── AF=1 (被访问过) → 提升到最新代 → 清除 AF
    │   └── AF=0 (未访问)  → 留在原代 → 随时间老化
    ├── 使用 Bloom filter 避免重复扫描
    └── max_seq++ → 新一代诞生

    比经典 LRU 的优势:
      扫描页表比遍历 LRU 链表快 (页表紧凑, cache 友好)
      直接利用硬件 AF 位, 不需要 PG_referenced 两次检查

(2) 回收 (Eviction) — 从最老代 (min_seq) 开始回收
    evict_folios():
    ├── 选择回收匿名还是文件页 (基于 refault 统计自适应)
    ├── 从 min_seq 代的链表取页
    ├── 按 tier (访问频率层级) 保护热页:
    │   tier 0: 仅被访问 1 次 → 优先回收
    │   tier 1: 被访问 2+ 次 → 保护
    │   保护阈值基于 refault 率自适应调整
    ├── 回收完一代 → min_seq++ → 继续下一代
    └── 直到释放足够页面
```

```bash
# 查看 MGLRU 状态
cat /sys/kernel/mm/lru_gen/enabled
# 0x0007: bit0=启用核心, bit1=启用 Bloom filter, bit2=启用 memcg

cat /sys/kernel/debug/lru_gen
# 显示每个 node/memcg 的代信息:
#   node  memcg  gen  anon  file
#     0     0     3   1024  2048   ← 最新代
#     0     0     2    512  1024
#     0     0     1    256   512
#     0     0     0    128   256   ← 最老代 (回收候选)

# 手动触发老化/回收 (调试)
echo "node_id memcg_id max_seq [can_swap [force_scan]]" > /sys/kernel/debug/lru_gen
```

### 3.16.2 回收触发路径

```
(1) kswapd — 异步回收 (每个 NUMA 节点一个内核线程)
    触发: 空闲页 < LOW 水位线
    行为: 后台回收直到 > HIGH 水位线
    不阻塞分配者

(2) 直接回收 (direct reclaim) — 同步
    触发: 空闲页 < MIN 水位线, kswapd 来不及
    行为: 分配者自己在 __alloc_pages_slowpath() 中执行回收
    阻塞当前进程

(3) memcg 回收
    触发: cgroup 的 memory.high/memory.max 被突破
    行为: 仅回收该 cgroup 的页面
```

回收核心函数调用链:
```
shrink_node()
  ├── shrink_node_memcgs()     → 遍历每个 memcg
  │     └── shrink_lruvec()    → 对一个 lruvec 执行回收
  │           ├── get_scan_count()     → 计算扫描匿名/文件页的比例
  │           ├── shrink_active_list() → 将活跃页降级到不活跃
  │           ├── shrink_inactive_list()→ 从不活跃链表回收
  │           │     └── shrink_page_list()
  │           │           ├── page_check_references() → 检查访问历史
  │           │           ├── 干净文件页 → 直接释放
  │           │           ├── 脏文件页 → pageout() 写回磁盘
  │           │           ├── 匿名页 → add_to_swap() + pageout()
  │           │           └── 映射页 → try_to_unmap() 解除映射
  │           └── [6.1] 如果 MGLRU 启用 → evict_folios() 替代上述流程
  └── shrink_slab()    → 回收 slab 缓存
        └── 遍历所有 shrinker → shrinker->scan_objects()
            (inode_cache, dentry_cache 等)
```

### 3.16.3 swappiness 与匿名/文件页平衡

```
/proc/sys/vm/swappiness (默认 60)

含义: 控制回收匿名页 vs 文件页的倾向
  swappiness = 0   → 尽量不换出匿名页，优先回收文件页
  swappiness = 100 → 匿名页和文件页同等对待
  swappiness = 200 → 积极换出匿名页 [6.1 扩展范围到 200]

计算扫描比例 (get_scan_count):
  匿名页扫描数 = 总扫描数 × swappiness / (swappiness + 文件页权重)
  文件页扫描数 = 总扫描数 × 文件页权重 / (swappiness + 文件页权重)

inactive/active 比例控制:
  inactive 与 active 链表长度比值:
  inactive_ratio = sqrt(10 × GB)  (GB 为内存大小)
  如果 inactive < active × inactive_ratio → 需要从 active 降级
```

### 3.16.4 收缩活动页链表

```
shrink_active_list():
  从 active 链表尾部取一批页
  对每页调用 page_referenced() 检查:
    (1) PTE 的 AF 位是否设置 (通过 rmap 反向映射遍历所有映射)
    (2) PG_referenced 标志
  
  处理结果:
    最近被访问   → 保留在 active，放到头部 (保护)
    未被访问     → 降级到 inactive 头部
    可执行文件页 → 额外保护 (不轻易降级)
```

### 3.16.5 页交换 (Swap)

```
配置交换区:
  (1) 磁盘分区: mkswap /dev/sda2 → swapon /dev/sda2
  (2) 文件:     dd if=/dev/zero of=/root/swap bs=1M count=2048
                mkswap /root/swap → swapon /root/swap
  (3) ZRAM:     modprobe zram num_devices=4
                echo 512M > /sys/block/zram0/disksize
                mkswap /dev/zram0 → swapon /dev/zram0

交换过程 (pageout):
  1. add_to_swap()    → 分配 swap 槽位
  2. try_to_unmap()   → 通过 rmap 解除所有进程对该页的映射
  3. pageout()        → 将页内容写入 swap 设备
  4. PTE 修改为 swap entry (包含 swap 设备编号 + 偏移)
  5. 释放物理页归还 buddy

换入过程 (do_swap_page):
  1. 从 swap cache 查找 → 命中则直接使用
  2. 未命中 → swapin_readahead() 从 swap 设备读取 (可预读)
  3. 设置 PTE 映射
  4. swap_free() 减少 swap 槽位引用计数
```

### 3.16.6 回收 slab 缓存

```
shrinker 机制:
  每个可回收的 slab 缓存注册一个 shrinker:
  struct shrinker {
      unsigned long (*count_objects)(...);  // 返回可释放的对象数
      unsigned long (*scan_objects)(...);   // 实际释放对象
  };

  主要 shrinker:
    super_block->s_shrink      → inode/dentry 缓存
    workingset_shadow_shrinker → 工作集影子条目
    
  回收时:
    shrink_slab() 遍历所有 shrinker
    按比例分配扫描预算给每个 shrinker
    优先回收容易释放的 (count 大的)
```

---

## 3.17 内存耗尽杀手 (OOM Killer)

### 3.17.1 使用方法

```bash
# OOM 相关内核参数
/proc/sys/vm/oom_kill_allocating_task  # 是否直接杀分配者 (0=否, 选最差进程)
/proc/sys/vm/oom_dump_tasks           # OOM 时是否 dump 进程列表
/proc/sys/vm/panic_on_oom             # OOM 时是否 panic (0=否, 1=是, 2=仅强制)

# 进程级调节
/proc/<pid>/oom_score_adj   # 调整 OOM 分数 (-1000 ~ 1000)
                            # -1000: 永不被杀 (如 init, 关键服务)
                            # 0: 默认
                            # 1000: 优先被杀
/proc/<pid>/oom_score       # 只读，内核计算的最终分数

# cgroup 级
memory.oom.group = 1        # OOM 时杀整个 cgroup (不只一个进程)
```

### 3.17.2 技术原理

```
OOM 触发路径:
  __alloc_pages_slowpath()
    → 所有回收手段失败 (kswapd + direct reclaim + compaction)
    → out_of_memory()

oom_badness() 评分算法:
  基础分 = 进程 RSS + swap使用 + 页表页数
  
  调整:
    oom_score_adj = -1000 → 返回 LONG_MIN (永不被杀)
    其他情况: 基础分 × (1000 + oom_score_adj) / 1000
    
  特殊保护:
    init 进程 (pid=1): 默认 oom_score_adj = -1000
    内核线程: 不参与 OOM 评分
    正在 vfork 的进程: 跳过 (杀了可能导致父子都挂)

oom_kill_process():
  1. 选中 victim (分数最高的进程)
  2. 如果 victim 有子进程且子进程不共享 mm → 优先杀子进程
  3. 发送 SIGKILL 给 victim
  4. 标记 TIF_MEMDIE → 给 victim 最高优先级回收内存
  5. 等待 victim 退出释放内存

全局 OOM vs memcg OOM:
  全局: out_of_memory() → 在所有进程中选择 victim
  memcg: mem_cgroup_out_of_memory() → 仅在 cgroup 内选择
         如果 memory.oom.group=1 → 杀整个 cgroup 的所有进程
```

---

## 3.18 内存资源控制器 (memcg)

### 3.18.1 使用方法

详见 `cgroup_v2.md` 中 memory 控制器章节。v1 接口汇总：

| v1 接口 | 功能 |
|---|---|
| `memory.limit_in_bytes` | 硬限制 (v2: memory.max) |
| `memory.soft_limit_in_bytes` | 软限制 (v2: memory.high) |
| `memory.usage_in_bytes` | 当前使用量 (v2: memory.current) |
| `memory.memsw.limit_in_bytes` | 内存+swap 限制 |
| `memory.memsw.usage_in_bytes` | 内存+swap 使用量 |
| `memory.max_usage_in_bytes` | 历史最大使用量 |
| `memory.failcnt` | 达到限制的次数 |
| `memory.swappiness` | per-cgroup 的 swappiness |
| `memory.oom_control` | OOM 控制 |
| `memory.stat` | 详细统计信息 |

### 3.18.2 技术原理

```
核心数据结构:
  struct mem_cgroup
  ┌──────────────────────────────────────────────────┐
  │ css (cgroup_subsys_state)  → 嵌入 cgroup 框架     │
  │                                                  │
  │ memory 计数器:                                    │
  │   struct page_counter memory  → 内存使用计数       │
  │   struct page_counter swap    → swap 使用计数      │
  │   struct page_counter memsw   → 内存+swap 计数     │
  │                                                  │
  │ 每节点信息:                                       │
  │   struct mem_cgroup_per_node *nodeinfo[]           │
  │     └── struct lruvec lruvec  → 每 memcg 独立 LRU  │
  │                                                  │
  │ 软限制管理:                                       │
  │   struct mem_cgroup_tree_per_node                  │
  │     └── rb_tree → 按使用量排序，超软限制的排前面      │
  │                                                  │
  │ OOM:                                              │
  │   oom_lock, oom_group                             │
  └──────────────────────────────────────────────────┘

计费流程 (charge):
  缺页 / kmalloc / page cache 分配
    → mem_cgroup_charge() 或 try_charge()
      → 当前 memcg 的 page_counter.usage += nr_pages
      → 检查是否超过 memory.max:
          未超过 → 返回成功
          超过 → 尝试回收该 memcg 的页面
                  回收成功 → 重试
                  回收失败 → mem_cgroup_oom() → memcg 内 OOM

uncharge 流程:
  页面释放时
    → mem_cgroup_uncharge()
      → page_counter.usage -= nr_pages
```

**[6.1 变化]**: 每个 memcg 有独立的 `lru_gen_struct` (MGLRU)，回收精度从 per-node 提升到 per-memcg。

---

## 3.19 处理器缓存

### 3.19.1 缓存结构 (ARM64)

```
典型 ARM64 缓存层次 (如 Cortex-A78 / Neoverse):

  CPU Core 0         CPU Core 1
  ┌─────────┐        ┌─────────┐
  │ L1 I$   │ 32-64KB│ L1 I$   │  指令缓存, 2-4 周期
  │ L1 D$   │ 32-64KB│ L1 D$   │  数据缓存, 2-4 周期
  │ L2 $    │ 256KB- │ L2 $    │  统一缓存, 8-12 周期
  └────┬────┘  1MB   └────┬────┘
       └─────────┬────────┘
            ┌────┴────┐
            │  L3 $   │ 2-16MB, Cluster 共享, 20-40 周期
            └────┬────┘
                 │
            ┌────┴────┐
            │  内存    │ DRAM, 100-300+ 周期
            └─────────┘

  缓存行大小: 64 字节 (ARM64 通常)
  关联度: L1 通常 4-way, L2 8-way, L3 16-way
  
  SRAM vs DRAM:
    SRAM: 缓存使用, 快但贵, 不需要刷新
    DRAM: 主内存, 慢但密度高, 需要定期刷新
```

**VIPT 缓存的别名问题**:

```
ARM64 L1 D-cache 通常使用 VIPT (Virtually Indexed, Physically Tagged):
  虚拟地址的低位 → 索引缓存行位置
  物理地址 → 标签匹配

别名问题: 两个不同虚拟地址映射到同一物理页
  如果虚拟地址的 [11:0] 不同 → 索引到不同缓存行
  → 同一物理数据在缓存中出现两份 → 一致性问题!

ARM64 的解决:
  当页大小 ≥ 缓存 way 大小 (4KB ≥ 4-way × 16B 行 × 64 组)
  → 索引位都在页偏移内 → 不同虚拟地址索引相同 → 无别名
  ARM64 4KB 页 + 64B 缓存行: 索引位 [11:6] 都在页内偏移中
  → VIPT 等效于 PIPT → 无别名问题

ARM64 处理器的缓存类型:
  (1) PIPT (Physically Indexed, Physically Tagged): 无别名，常用于 L2/L3
  (2) VPIPT (VMID-aware PIPT): 虚拟化环境
  (3) VIPT: L1 常用, ARM64 下 4KB 页不存在别名问题
```

### 3.19.2 缓存策略

| 策略 | 读 | 写 | 适用场景 |
|---|---|---|---|
| Write-back (回写) | 命中读缓存 | 命中只写缓存, 标脏 | 高性能 (默认) |
| Write-through (写穿) | 命中读缓存 | 同时写缓存和内存 | 一致性要求高 |
| Read-allocate | 未命中分配缓存行 | — | 默认读行为 |
| Write-allocate | — | 未命中分配缓存行 | 配合 write-back |

### 3.19.3 缓存维护 (ARM64)

```
ARM64 缓存维护指令:
  DC CIVAC, Xt   → Clean + Invalidate 到 PoC (物理一致性点)
  DC CVAC, Xt    → Clean 到 PoC (写回但不失效)
  DC IVAC, Xt    → Invalidate (仅失效, 不写回)
  DC CVAU, Xt    → Clean 到 PoU (指令可见点, 用于自修改代码)
  IC IALLU       → Invalidate 所有指令缓存

  dsb ish        → 确保缓存维护指令前的存储完成
  dsb ishst      → 确保存储完成 (仅 store)
  isb            → 指令同步屏障 (刷流水线)

内核缓存管理函数:
  flush_dcache_page(page)     → 刷新整页数据缓存
  invalidate_icache_range()   → 无效化指令缓存范围
  __flush_dcache_area()       → 刷新指定区域
  clean_dcache_area_poc()     → 清洁到一致性点
```

### 3.19.4 SMP 缓存一致性

ARM64 使用硬件缓存一致性协议 (基于 MOESI 变体):

```
MESI 协议 4 种状态:
  Modified (M)  : 仅本核缓存有效, 已修改, 与内存不一致
  Exclusive (E) : 仅本核缓存有效, 未修改, 与内存一致
  Shared (S)    : 多核缓存有效, 未修改, 与内存一致
  Invalid (I)   : 无效

状态转换示例:
  处理器 0 读地址 a: Miss → 从内存加载 → 状态 E (独占)
  处理器 1 读地址 a: 处理器 0 收到 snoop → 状态变 S, 处理器 1 也变 S
  处理器 0 写地址 a: 发送无效化消息 → 处理器 1 变 I, 处理器 0 变 M
  处理器 1 再读 a:  处理器 0 收到 snoop → 写回内存 → 两者变 S

false sharing (伪共享) 问题:
  ┌──────────────────── 一条缓存行 (64字节) ────────────────────┐
  │  变量 A (CPU0 频繁写)  │  变量 B (CPU1 频繁写)  │ padding │
  └────────────────────────────────────────────────────────────┘
  A 和 B 无关，但在同一缓存行 → CPU0 写 A 使 CPU1 的缓存行失效
  → CPU1 写 B 使 CPU0 的缓存行失效 → 反复 bounce → 性能暴跌

解决: __cacheline_aligned_in_smp
  struct data {
      int cpu0_var ____cacheline_aligned_in_smp;  // 对齐到缓存行
      int cpu1_var ____cacheline_aligned_in_smp;  // 各占一行
  };
```

**存储缓冲区 (Store Buffer)** 与内存屏障:

```
问题: 写操作先进入 store buffer, 其他核看不到
  CPU0: a = 1; b = 1;
  CPU1: while(b != 1); assert(a == 1); // 可能失败!
  因为 CPU1 可能先看到 b=1 但 a=1 还在 CPU0 的 store buffer 中

解决: 内存屏障
  CPU0: a = 1; smp_wmb(); b = 1;       // 写屏障: a 必须在 b 之前可见
  CPU1: while(b != 1); smp_rmb(); assert(a == 1); // 读屏障

ARM64 屏障指令:
  DMB (Data Memory Barrier)  → 数据内存屏障
  DSB (Data Synchronization Barrier) → 数据同步屏障 (比 DMB 强)
  ISB (Instruction Synchronization Barrier) → 指令同步屏障

  dmb ishld  → 内部共享域的 Load-Load/Load-Store 屏障
  dmb ishst  → 内部共享域的 Store-Store 屏障
  dmb ish    → 内部共享域的全屏障
```

### 3.19.5 利用缓存提高效率的编程技巧

| 技巧 | 说明 |
|---|---|
| 数据局部性 | 相关数据放在一起，减少缓存行加载次数 |
| `__cacheline_aligned` | 结构体对齐到缓存行，避免 false sharing |
| percpu 变量 | 每 CPU 独立副本，消除缓存行争用 |
| `PRFM` 预取 | `prefetch(addr)` / ARM64 PRFM 指令，提前加载数据到缓存 |
| 结构体字段排序 | 热字段放在同一缓存行，冷字段放其他行 |
| `likely()` / `unlikely()` | 分支预测提示，提高指令缓存命中率 |

---

## 3.20 连续内存分配器 (CMA)

### 3.20.1 使用方法

```bash
# 方式 1: 内核参数全局 CMA
cma=64M                        # 全局 CMA 大小
cma=64M@0x80000000              # 指定起始地址

# 方式 2: 设备树 reserved-memory
reserved-memory {
    #address-cells = <2>;
    #size-cells = <2>;
    ranges;

    /* 全局默认 CMA 区域 */
    linux,cma {
        compatible = "shared-dma-pool";
        reusable;
        size = <0x0 0x4000000>;         /* 64MB */
        alignment = <0x0 0x2000>;
        linux,cma-default;              /* 标记为默认 CMA */
    };

    /* 显示设备专用 CMA */
    display_reserved: framebuffer@78000000 {
        compatible = "shared-dma-pool";
        reg = <0x0 0x78000000 0x0 0x800000>;   /* 8MB */
    };

    /* 多媒体设备专用 CMA */
    multimedia_reserved: multimedia@77000000 {
        compatible = "acme,multimedia-memory";
        reg = <0x0 0x77000000 0x0 0x4000000>;  /* 64MB */
    };
};

/* 设备节点引用 CMA 区域 */
fb0: video@12300000 {
    memory-region = <&display_reserved>;
};
```

### 3.20.2 技术原理

```
CMA 在 DMA 映射框架中的位置:
  驱动调用 dma_alloc_coherent() / dma_alloc_attrs()
    → DMA 映射层
      → cma_alloc()  从 CMA 区域分配连续物理页
    → 或直接 alloc_pages() (如果不需要连续)

CMA 区域的创建流程:
  setup_arch()
    → arm64_memblock_init()
      → early_init_fdt_scan_reserved_mem()      ← 解析设备树
        → fdt_init_reserved_mem()
          → __reserved_mem_init_node()
            → rmem_cma_setup()                   ← 创建 CMA 区域
              → cma_init_reserved_mem()
    → dma_contiguous_reserve()                   ← 全局默认 CMA
      → cma_declare_contiguous()
        → memblock_reserve() 或 memblock_alloc() ← 预留物理内存

CMA 区域的双重身份:
  ┌──────────────────────────────────────────────────────────┐
  │ CMA 区域 (MIGRATE_CMA 迁移类型, 属于 ZONE_DMA/NORMAL)   │
  │                                                          │
  │ 空闲时:                                                  │
  │   被 buddy 分配给 MOVABLE 页使用 (用户匿名页/page cache)   │
  │   对系统来说这些内存不浪费                                 │
  │                                                          │
  │ 设备需要时:                                               │
  │   cma_alloc(cma, count, align, gfp)                      │
  │   (1) alloc_contig_range() 请求连续物理页                  │
  │   (2) 内部调用 __alloc_contig_migrate_range()             │
  │       → 迁移区域内的已分配页 (改页表映射到其他物理页)        │
  │   (3) 腾出连续空间 → 返回给设备                           │
  │                                                          │
  │ 设备释放后:                                               │
  │   cma_release()                                           │
  │   → 页面归还 buddy → 重新可被 MOVABLE 分配使用             │
  └──────────────────────────────────────────────────────────┘

CMA 与 buddy 的关系:
  CMA 区域在 buddy 中标记为 MIGRATE_CMA 迁移类型
  MIGRATE_CMA 的 fallback 允许 MIGRATE_MOVABLE 从中分配
  但不允许 MIGRATE_UNMOVABLE 分配 (保证所有页可迁移)
```

---

## 3.21 userfaultfd

用户态缺页处理机制，允许用户空间程序接管特定 VMA 的缺页异常。

### 3.21.1 使用方法

```c
// 完整使用流程:

// 1. 创建 userfaultfd 文件描述符
int uffd = userfaultfd(O_CLOEXEC | O_NONBLOCK);

// 2. API 握手
struct uffdio_api uffdio_api = {
    .api = UFFD_API,
    .features = UFFD_FEATURE_MISSING_SHMEM  // 可选特性
};
ioctl(uffd, UFFDIO_API, &uffdio_api);

// 3. 创建匿名内存区域
char *addr = mmap(NULL, len, PROT_READ | PROT_WRITE,
                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

// 4. 注册区域到 userfaultfd
struct uffdio_register uffdio_register = {
    .range = { .start = (unsigned long)addr, .len = len },
    .mode = UFFDIO_REGISTER_MODE_MISSING   // 拦截缺页
};
ioctl(uffd, UFFDIO_REGISTER, &uffdio_register);

// 5. 处理线程: poll 等待缺页事件
struct pollfd pollfd = { .fd = uffd, .events = POLLIN };
poll(&pollfd, 1, -1);

struct uffd_msg msg;
read(uffd, &msg, sizeof(msg));
// msg.arg.pagefault.address = 缺页地址

// 6. 提供页面内容
struct uffdio_copy uffdio_copy = {
    .dst = msg.arg.pagefault.address & ~(page_size - 1),
    .src = (unsigned long)page_buffer,  // 用户准备好的数据
    .len = page_size,
    .mode = 0,
};
ioctl(uffd, UFFDIO_COPY, &uffdio_copy);
// 触发缺页的进程恢复执行
```

### 3.21.2 使用场景

| 场景 | 说明 |
|---|---|
| QEMU postcopy 实时迁移 | 虚拟机内存边运行边从源主机按需拉取 |
| 用户态内存管理器 | 垃圾回收器自定义页面管理 |
| 延迟初始化 | 大内存区域按需初始化，避免一次性分配 |
| 检查点恢复 (CRIU) | 进程迁移和恢复 |

### 3.21.3 技术原理

```
内核路径:
  do_page_fault()
    → handle_mm_fault()
      → handle_pte_fault()
        → 检测到 VMA 注册了 userfaultfd 且页不存在
        → handle_userfault(vmf, VM_UFFD_MISSING)
          ├── 构造 uffd_msg (包含缺页地址、类型等信息)
          ├── 写入 userfaultfd 的事件队列
          ├── 唤醒 poll 等待的处理线程
          └── 当前进程进入 TASK_KILLABLE 睡眠
              等待处理线程通过 UFFDIO_COPY/UFFDIO_ZEROPAGE 提供页面

fork 时的处理:
  copy_mm() → dup_userfaultfd()
    如果 userfaultfd 注册了 UFFD_FEATURE_EVENT_FORK:
    → 子进程的 userfaultfd 上下文复制给处理线程
    → 处理线程可以监控子进程的缺页
```

**[6.1 变化]**: 支持 `UFFD_FEATURE_MINOR_HUGETLBFS` 和 `UFFD_FEATURE_MINOR_SHMEM`，可拦截已有页但需更新的 minor fault (不仅是缺页)，用于增量更新场景。

---

## 3.22 内存错误检测工具 KASAN

### 3.22.1 使用方法

```bash
# 内核配置 (三种模式)
CONFIG_KASAN=y

# 模式 1: Generic KASAN (最完整, 性能开销 ~2x)
CONFIG_KASAN_GENERIC=y
# 编译器在每次内存访问前插入检查代码

# 模式 2: Software Tag-Based KASAN (ARM64 专用, 开销较低)
CONFIG_KASAN_SW_TAGS=y
# 利用 ARM64 TBI (Top Byte Ignore) 特性
# 在指针的高 8 位存储标签

# 模式 3: Hardware Tag-Based KASAN (ARMv8.5+ MTE, 开销最低 ~3-5%)
CONFIG_KASAN_HW_TAGS=y
# 利用 MTE (Memory Tagging Extension) 硬件特性
# 每 16 字节一个标签, 硬件自动校验

# 启动参数
kasan.mode=on            # 启用
kasan.stacktrace=on      # 记录分配/释放调用栈
kasan.fault=report       # 检测到错误时: report(只报告) / panic(崩溃)
```

### 3.22.2 检测能力

| 错误类型 | Generic | SW_TAGS | HW_TAGS |
|---|---|---|---|
| 堆越界 (out-of-bounds) | 是 | 是 | 是 |
| 释放后使用 (use-after-free) | 是 | 是 | 是 |
| 双重释放 (double-free) | 是 | 是 | 是 |
| 栈越界 | 是 | 否 | 否 |
| 全局变量越界 | 是 | 否 | 否 |
| 性能开销 | ~2x | ~1.5x | ~3-5% |

### 3.22.3 技术原理 — Generic KASAN

```
Shadow Memory 1:8 映射:
  每 8 字节内存 → 1 字节 shadow
  shadow 值含义:
    0    : 8 字节全部可访问
    1~7  : 前 N 字节可访问, 其余不可 (slab 对象末尾)
    0xFx : 各种不可访问类型:
           0xFA = freed (已释放)
           0xFB = redzone (slab 红区)
           0xFC = stack redzone (栈红区)
           0xFE = shadow gap
           0xFF = 永不可访问

  ARM64 shadow 内存映射在 KASAN_SHADOW_START 区域:
  shadow_addr = KASAN_SHADOW_START + (addr >> 3)

编译器插桩 (每次内存访问前):
  __asan_load4(addr):
    shadow = *(KASAN_SHADOW_START + (addr >> 3))
    if (shadow != 0) {
        if (shadow < (addr & 7) + 4)
            kasan_report(addr, 4, false);  // 报告越界!
    }
```

### 3.22.4 技术原理 — HW_TAGS (MTE)

```
ARM MTE (Memory Tagging Extension, ARMv8.5+):
  硬件在每 16 字节物理内存上附加 4-bit 标签 (Tag Storage)
  指针的高 4 位存储期望标签 (利用 TBI)
  
  分配时: kmalloc → 随机标签 → 写入内存标签 + 指针标签
  访问时: CPU 硬件自动比较指针标签 vs 内存标签
    匹配 → 正常访问
    不匹配 → 触发 Tag Check Fault 异常 → KASAN 报告

  优势:
    检测是硬件做的 → 几乎零软件开销 (3~5%)
    相比 Generic KASAN 的 2x 开销是巨大进步
    适合在生产环境开启

  限制:
    标签只有 4 bit → 16 种值 → 1/16 概率误匹配 (false negative)
    每 16 字节粒度 → 小于 16 字节的越界检测不到
    需要 ARMv8.5+ 硬件支持 (如 Cortex-A715+)
```

**[6.1 变化]**: 6.1 完善了 HW_TAGS 支持，启动参数 `kasan.mode=on/off` 可动态控制，配合 MTE 的 sync/async 模式选择精度与性能的平衡。

---

## 全章知识脉络（3.11~3.22）

```
第 3 章 内存管理（续）
│
├── 地址翻译硬件
│   ├── 3.11 页表
│   │   ├── 五级框架 PGD→P4D→PUD→PMD→PTE (ARM64 通常 4 级)
│   │   ├── ARM64 描述符格式: 块/页/表描述符 + 属性位
│   │   ├── [6.1] FEAT_HAFDBS 硬件自动管理 AF/Dirty
│   │   └── 页表操作宏: pte_pfn/set_pte/pte_mkwrite...
│   └── 3.12 TLB
│       ├── TLBI 指令族 (ALL/VA/ASID × IS/OS)
│       ├── ASID: 8/16-bit, 避免进程切换刷 TLB, rollover 机制
│       └── VMID: KVM 虚拟化 Stage-2 标识
│
├── 大页
│   └── 3.13 巨型页
│       ├── 块描述符 (2MB/1GB) + 连续位 (64KB/32MB)
│       ├── 标准巨型页: hugetlbfs, 预留池, alloc_contig_pages
│       └── 透明巨型页: THP, 缺页分配 + khugepaged 后台合并
│
├── 缺页处理
│   └── 3.14 页错误异常
│       ├── ARM64: FAR_EL1+ESR_EL1 → do_mem_abort → do_page_fault
│       ├── handle_pte_fault 分发:
│       │   anonymous/file/swap/cow/numa 五种缺页
│       ├── 内核态: fixup table / vmalloc 同步
│       └── [6.1] userfaultfd 集成
│
├── 碎片与回收
│   ├── 3.15 反碎片
│   │   ├── ZONE_MOVABLE: 仅可迁移页, 保障热移除
│   │   └── Compaction: 迁移扫描器+空闲扫描器相向, kcompactd
│   ├── 3.16 页回收 (最复杂的子系统之一)
│   │   ├── 经典 LRU: active/inactive × anon/file 四链表
│   │   ├── [6.1] MGLRU: 多代LRU, 扫描页表, refault 自适应
│   │   ├── 触发: kswapd/direct reclaim/memcg
│   │   ├── 核心: shrink_node → shrink_lruvec → shrink_page_list
│   │   ├── swap: add_to_swap → try_to_unmap → pageout
│   │   └── slab 回收: shrinker 机制 (inode/dentry 等)
│   └── 3.17 OOM Killer
│       ├── oom_badness 评分 (RSS+swap+oom_score_adj)
│       └── 全局 OOM vs memcg OOM
│
├── 资源控制
│   └── 3.18 memcg
│       ├── 计费: try_charge → page_counter
│       ├── 结构: mem_cgroup → per_node → lruvec
│       └── [6.1] 独立 lru_gen_struct per memcg
│
├── 硬件特性
│   └── 3.19 处理器缓存
│       ├── ARM64 L1(VIPT)/L2(PIPT)/L3 层次
│       ├── MESI 一致性协议 + false sharing 问题
│       ├── Store buffer + 内存屏障 (DMB/DSB/ISB)
│       ├── 缓存维护指令: DC CIVAC/CVAC/IVAC
│       └── 编程技巧: prefetch/cacheline_aligned/percpu
│
├── 特殊分配器
│   └── 3.20 CMA
│       ├── 配置: 内核参数/设备树 reserved-memory
│       ├── 双重身份: 空闲给 MOVABLE, 需要时迁移收回
│       └── DMA 映射框架集成: dma_alloc_coherent → cma_alloc
│
└── 调试与高级特性
    ├── 3.21 userfaultfd
    │   ├── API: userfaultfd → ioctl REGISTER → poll → UFFDIO_COPY
    │   ├── 场景: QEMU postcopy/CRIU/用户态内存管理
    │   └── [6.1] MINOR fault 拦截支持
    └── 3.22 KASAN
        ├── Generic: shadow memory 1:8, 编译器插桩, ~2x 开销
        ├── SW_TAGS: ARM64 TBI, ~1.5x 开销
        └── [6.1] HW_TAGS: ARM64 MTE, ~3-5% 开销, 可用于生产
```
