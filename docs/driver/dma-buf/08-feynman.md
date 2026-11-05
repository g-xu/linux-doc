---
title: 费曼学习法
---

# dma-buf 08：从第一性原理重建这个子系统

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

前七篇讲"是什么、怎么用、怎么查"。这一篇只回答一个问题：
**如果让你从零设计，为什么最后一定会得到这个形状。**

⚠️ 本篇的每一步推导都落到源码或官方论证上，不做想象。
最关键的一段论证在内核树里有原文：
`Documentation/driver-api/dma-buf.rst:203-272`（Indefinite DMA Fences）
和 `:274-348`（Recoverable Hardware Page Faults Implications），
以及 `drivers/dma-buf/dma-fence.c:69-112` 的 `DOC: fence cross-driver contract`。

---

## §1 原始问题

**摄像头拍一帧，GPU 处理，显示控制器送屏。三个设备，一块内存，零拷贝。**

这就是全部需求。现在看已有的机制为什么都不够用：

| 已有机制 | 为什么不行 |
|---|---|
| 普通文件 + `read/write` | 那是拷贝，需求是零拷贝 |
| `shmem` / `memfd` | 只解决了"CPU 之间共享"，**没有解决"设备能不能寻址这块内存"** |
| `mmap` 到两个进程 | 同上，而且**设备根本不看虚拟地址** |
| 直接传物理地址 | ⚠️ 有 IOMMU 时，同一块内存对不同设备是**不同的地址**；没有 IOMMU 时不同设备的**可寻址范围也不同** |
| 各驱动自己的 ioctl 互传句柄 | N 个驱动两两配对 = O(N²) 的协议，且跨子系统（v4l2 ↔ drm）无从下手 |

⇒ **需要一个跨驱动、跨子系统、跨进程的"这块内存"的句柄。**

**为什么句柄必须是 fd**（而不是一个整数 ID 或字符串名）：
1. 跨进程传递已有现成机制（`SCM_RIGHTS`、fork 继承）；
2. 生命周期管理已有现成机制（引用计数、进程退出自动关闭）；
3. 权限模型已有现成机制（fd 的可见性就是权限）；
4. 可以被 `poll()`（后面 §4 会用到）。

⇒ **第一个设计决定被逼出来了：dma-buf 就是一个 fd。**
（代码上就是 `dma_buf_export()` + `dma_buf_fd()`，01 §2.1。）

---

## §2 三个不变量

从 §1 出发，最终整个子系统立在三条不变量上。**先给结论，§3–§5 逐条推**：

| # | 不变量 | 一句话 |
|---|---|---|
| **I1** | 共享的是**访问权**，不是地址 | 每个设备自己去 map，拿到自己的地址 |
| **I2** | 访问是**异步**的 | 需要一个"什么时候能用"的对象 ⇒ fence |
| **I3** | ⚠️⚠️ **fence 必须在有界时间内完成** | 这一条是所有奇怪限制的唯一源头 |

⚠️ **I3 是本篇的重点。** 它看起来像一句废话，但它单独推出了：
GFP 限制、锁序、无限期 fence 禁令、sw_sync 只能测试用、
乃至"可恢复缺页与 DMA fence 不兼容"这个影响整个 GPU 栈的结论。

---

## §3 推导 I1：为什么共享的是访问权

假设反过来：dma-buf 直接给出"这块内存的地址"。立刻矛盾：

- 设备 A 走 IOMMU，看到 IOVA `0x1000`；设备 B 不走 IOMMU，看到物理地址 `0x8000_0000`
  ⇒ **同一块内存没有单一"地址"可给。**
- 设备 C 只能寻址 32 位 ⇒ 这块内存对它**可能根本不可用**。

⇒ 句柄不能携带地址，只能携带"去问的能力"。于是必然出现两层：

```
dma_buf                        ← 缓冲区本身（一份）
  └── dma_buf_attachment       ← 「某个设备对它的访问」（每设备一份）
        └── sg_table           ← 「该设备看到的地址列表」（每 attachment 一份）
```

**这就是 `attach` 这一步为什么不可省**（01 §2.2、02 §3）：
它是"设备 X 声明我要访问"的时刻，也是导出方**第一次知道有哪些设备**的时刻。

由此又推出三件事：

1. ⚠️ **导出方必须等到 attach 之后才能决定内存放在哪**。
   因为要满足所有 attach 设备的寻址约束的交集
   —— 这正是 `pin`/`move_notify` 存在的理由。
2. ⚠️ **`sg_table` 必须每个 attachment 一份，不能共享**。
   两个设备的 DMA 地址不同（`system_heap.c:58` 的 `dup_sg_table()` 就是干这个）。
3. ⚠️⚠️ **导入方绝不能碰 `struct page`**。
   碰了就等于绕过 I1 直接取地址，在有 IOMMU 或 swiotlb 时结果是错的。
   ⇒ 内核干脆加了个开关**主动破坏这个字段**来抓这类误用
   （`mangle_sg_table()`，`drivers/dma-buf/dma-buf.c:763`，07 §6）。

⚠️ 顺带解释了一个常见困惑：**为什么 `dma_buf_fops` 里没有 `.read`/`.write`**
（`drivers/dma-buf/dma-buf.c:509-517`，7 个成员，03 §A4）。
因为 `read`/`write` 需要"CPU 直接访问"这个语义，而 I1 说"访问要先声明"。
CPU 访问因此也走了自己的一套声明流程：`mmap` + `begin/end_cpu_access`
（DOC 块在 `drivers/dma-buf/dma-buf.c:1218`）
—— **CPU 在这个框架里只是"又一个设备"**，没有特权。

---

## §4 推导 I2：为什么必须有 fence

设备访问是异步的：提交命令后设备自己跑，CPU 不等。于是"缓冲区现在能不能读"
这个问题需要一个可跨驱动传递的答案对象。

**为什么不能用现成的同步原语：**

| 候选 | 为什么不行 |
|---|---|
| `completion` / `wait_queue` | 无法跨驱动传递（没有引用计数、没有 fd 化路径） |
| 信号量 | 无法表达"谁在等谁"，也没法查询状态 |
| 直接轮询设备寄存器 | 需要知道对方是什么设备 —— 违背跨驱动的初衷 |

⇒ 需要一个**带引用计数、可跨驱动、可查询、可注册回调、可 fd 化**的一次性事件对象。
**这就是 `struct dma_fence`。**

由此推出它的几个必然属性：

1. **一次性（不可重置）**。因为"第 N 帧完成"和"第 N+1 帧完成"是两个不同事件。
   ⇒ 代码里三个状态标志**没有一个能清零**（03 §B1）。
   ⚠️ 想复用一个 fence 对象的想法从这里就被否掉了。
2. **`(context, seqno)` 二元组**。同一条时间线上要能比较先后
   ⇒ `dma_fence_context_alloc()` 每条时间线一次（`drivers/dma-buf/dma-fence.c:187`），
   而不是每个 fence 一次（05 §3.1）。
   ⚠️ 这个二元组直接支撑了 dma-resv 的替换优化
   （`drivers/dma-buf/dma-resv.c:294-305`）。
3. **要能被 `poll()`** ⇒ `sync_file` 存在的全部理由
   （fence 的 uapi 面，`drivers/dma-buf/sync_file.c`，04 §4）。
4. **"隐式同步"必须有个地方存 fence** ⇒ `struct dma_resv` 挂在 `dma_buf->resv` 上
   （`DOC: DMA fences overview` 在 `drivers/dma-buf/dma-fence.c:62-65` 讲的就是这层关系）。

⚠️ **显式 vs 隐式同步的分野也在这里被定下来**
（`drivers/dma-buf/dma-fence.c:53-65`）：
- **显式**：fence 变成 fd，用户态自己传（`sync_file`）；
- **隐式**：fence 存在 `dma_buf.resv` 里，谁用这块缓冲区谁自动等。

**两条路必须同时存在**，因为老的 winsys 协议只懂隐式，新的只用显式。

---

## §5 推导 I3：有界时间，以及它推出的一切

### §5.1 为什么"有界"不可协商

`drivers/dma-buf/dma-fence.c:74-81` 是这条契约的原文：

> Fences must complete in a reasonable time. Fences which represent kernels
> and shaders submitted by userspace, which could run forever, must be backed
> up by timeout and gpu hang recovery code.

**为什么？** 因为 fence 是**跨驱动**的。设备 A 的驱动会等设备 B 的 fence，
而 A 完全不知道 B 是什么。⚠️ 于是 A **无法**为 B 的 fence 设置合理超时
—— 原文明确禁止这么做（`:83-87`）：

> Drivers should not try to second guess timeout handling of fences from
> other drivers.

⇒ **每个 fence 的有界性只能由它的生产方自己保证。这是唯一可行的分工。**
一个不守约的生产方会挂住整个系统。

### §5.2 从"允许在哪里等"推出"signal 路径不能做什么"

这是本篇最重要的一段推导。契约里连续四条（`drivers/dma-buf/dma-fence.c:94-106`），
**每一条都是"允许 A 在 X 处等 fence" ⇒ 因此"fence 完成路径不能做 Y"**：

| 允许在哪里 `dma_fence_wait()` | ⇒ 因此 signal 路径不能 | 原文 |
|---|---|---|
| 持 `dma_resv_lock()` 时 | ⚠️ **不能获取任何 `dma_resv` 锁**（连带整条 `dma_resv` 锁序） | `:94-97` |
| `shrinker` 回调里 | ⚠️⚠️ **不能 `GFP_KERNEL` 分配内存** | `:99-101` |
| `mmu_notifier` 回调里 | ⚠️⚠️ **连 `GFP_NOFS`/`GFP_NOIO` 都不行；只剩 `GFP_ATOMIC`，而它可能失败** | `:103-106` |

⚠️⚠️ **这就是"fence 完成路径上不许分配内存"的完整证明**，
不是一条经验法则，而是三条"允许等待的位置"的直接逻辑后果。

**方向不能反过来读**：不是"设计者规定不许分配内存"，而是
"设计者允许在内存回收路径上等 fence" ⇒ **分配内存的能力被剥夺了**。
想恢复这个能力，就得放弃"能在 shrinker 里等 fence"
—— 而那意味着内存回收无法等 GPU 释放内存，整个显存管理就没法做。

⚠️ 契约最后一段（`:108-111`）还划了范围：

> No driver outside of drivers/gpu should ever call dma_fence_wait() in such contexts.

⇒ **只有 GPU 驱动有理由同时需要 mmu_notifier + shrinker + fence**。
其它子系统碰到"必须在这些上下文里等 fence"，说明设计错了。

### §5.3 这条推导被机器化了

上面那些约束全是散文。内核把它变成了可执行的检查：

```
dma_resv_lockdep()          drivers/dma-buf/dma-resv.c:742-780（subsys_initcall 在 :781）
  ↓ 启动时假装走一遍完整锁序，登记进 lockdep
mmap_lock → dma_resv → fs_reclaim → mmu_notifier → 等 fence
```

⚠️⚠️ **登记之后，任何违反的真实路径第一次执行就被抓，不用等真死锁**（03 §C5、07 §7）。
配套的假锁是 `dma_fence_lockdep_map`（`drivers/dma-buf/dma-fence.c:284`），
供 `dma_fence_begin_signalling()`/`_end_signalling()`
（`:302`/`:325`）标注 signal 临界区。

⚠️ **这是本子系统一个值得学的做法**：
把一段跨子系统的设计契约，用一个启动时永不失败的假函数编码成 lockdep 关系。
契约从此不再依赖人读文档。

---

## §6 推导：为什么"无限期 fence"被彻底禁止

### §6.1 需求是真实的

`Documentation/driver-api/dma-buf.rst:206-223` 列了四类反复被提出的无限期 fence：
future fence（HWC1 用过）· proxy fence（`drm_syncobj` 的延迟提交）·
userspace fence / GPU futex · 长跑的 compute 命令缓冲。

**共同点**（`:225-226`）：**依赖关系和触发时机都由用户态控制。**

### §6.2 为什么加个超时兜底也救不回来

原文的核心两句（`Documentation/driver-api/dma-buf.rst:230-234`）：

> Only the kernel knows about all DMA fence dependencies, userspace is not aware
> of dependencies injected due to memory management or scheduler decisions.
>
> Only userspace knows about all dependencies in indefinite fences and when
> exactly they will complete, the kernel has no visibility.

⇒ **没有任何一个实体掌握全部依赖关系**（`:260` 原文：
"there is no single entity with knowledge of all dependencies"）。

再加上一条不可去掉的需求（`:236-240`）：**内核必须能为了内存管理挂住用户态提交**
⇒ 无限期 fence **必须**允许依赖 DMA fence。
如果 DMA fence 也允许依赖无限期 fence，就成环：

```
       ┌────────────────────────┐
       │   Kernel DMA Fences    │
       └───┬────────────────▲───┘
   memory  │                │  Future fence / fence proxy …
 management│                │
       ┌───▼────────────────┴───┐
       │ userspace controlled   │
       │        fences          │
       └────────────────────────┘
```

（原图是 `dma-buf.rst:246-254` 的那段 DOT 描述。）

⚠️⚠️ **成环的后果不是"偶尔慢"，而是**（`:256-259`）：
内核会**意外**造出用户态看不见的死锁，
**从用户态视角看毫无死锁的负载会随机挂住，直到超时踢进来**。
⇒ 原文结论（`:261`）："preventing such deadlocks from within the kernel is not possible."

### §6.3 结论只有一个方向

`Documentation/driver-api/dma-buf.rst:263-272`：

> The only solution to avoid dependencies loops is by not allowing indefinite
> fences in the kernel. This means:
> * No future fences, proxy fences or userspace fences imported as DMA fences,
>   **with or without a timeout**.

⚠️⚠️ **"with or without a timeout"这半句是全篇最容易被忽略的一句**：
**加超时不能让无限期 fence 变合法。** 超时只把"永久挂死"换成"随机挂几秒然后失败"
—— 依赖环仍然存在，只是症状变成了间歇性故障。

⚠️ 这直接解释了 04 §5.4 的判断：**`sw_sync` 之所以只能是测试设施**，
因为它把 fence 的完成权交给了用户态（`sw_sync.c:155` 的
`enable_signaling` 永远返回 `true`，推进完全靠用户态写 debugfs）
—— **它按定义就是一个无限期 fence 工厂**。`CONFIG_SW_SYNC` 的 help 自己承认
"can result in deadlocking kernel drivers from userspace"（`drivers/dma-buf/Kconfig:20` 那一项）。
**它不是"实现得不好"，它是"故意造一个违反契约的东西来测试契约"。**

---

## §7 推导：可恢复缺页为什么和 DMA fence 天然冲突

这一节是 I3 最反直觉的后果。原文在
`Documentation/driver-api/dma-buf.rst:274-348`。

**"可恢复硬件缺页"是什么**：GPU 访问一个还没建立映射的地址，硬件挂起该线程，
内核处理缺页、建立映射、恢复执行 —— 就像 CPU 的缺页一样。
听起来纯属好事：不用预先 pin 内存、支持超额提交、编程模型简单。

**为什么和 fence 冲突**（`Documentation/driver-api/dma-buf.rst:280-285`）：

> the kernel is not allowed to allocate memory to
> handle these page faults, since the memory allocation might gate completion of
> a DMA fence.

⚠️⚠️ **一步就到矛盾**：
处理 GPU 缺页 = 分配内存；而那次 GPU 执行正被一个 DMA fence 代表。
⇒ **"完成这个 fence"依赖"分配内存"，而 §5.2 已经证明这是被禁止的方向。**

⇒ 结论（`:287-293`）：**可恢复缺页在 GPU 上只能用于纯计算负载**，
因为一旦这块缓冲区参与 winsys（compositor 用隐式同步等它），
就必须给它挂 DMA fence，矛盾立刻回来。

具体死锁形态（`Documentation/driver-api/dma-buf.rst:295-304`）是两条同时成立：

1. 一个走缺页的 compute 任务在等内存 ⇒ 内存回收在等某个 DMA fence；
2. 那个 DMA fence 在等 GPU ⇒ GPU 被那个缺页任务占着。

原文列了四个缓解方向（`:306-336`），但**每一个都是在削减功能**：
禁止在 compute 上下文里用 DMA fence · 硬件层面隔离 3D 与 compute 引擎 ·
用 preemption 强行踢掉缺页任务 · 用户态 fence 全程替代。
**没有一个是"两者兼得"。** 唯一的例外（`:338-341`）是硬件上完全独立、
不共享任何资源的引擎 —— 那本来就不构成依赖。

⚠️ 原文最后自己把两节串起来（`Documentation/driver-api/dma-buf.rst:343-348`）：
可恢复缺页问题**就是无限期 fence 问题的一个特例**。
⇒ **§6 和 §7 是同一条定理的两个推论**：
只要有一方掌握全部依赖信息、另一方不掌握，环就无法从内核内部打破。

---

## §8 反推：那些"看着奇怪"的设计其实都是被逼的

现在从三条不变量倒推，前面几篇里所有"为什么要这样"的疑问都能回答：

| 看着奇怪的设计 | 由哪条不变量逼出 | 逻辑 |
|---|---|---|
| `dma_buf_ops` 有 13 个回调却大多"可选" | I1 | 因为设备形态差异全在导出方，核心不能预设；"可选"只是编译期可选（05 §1.3） |
| `map_dma_buf` 必须在 attach 之后调 | I1 | attach 是核心第一次知道设备约束的时刻 |
| `struct dma_resv` 只有 2 个字段（`include/linux/dma-resv.h:155-179`） | I2 | 它只需要"一把锁 + 一串 fence"，多一个字段就是多一处需要同步的状态 |
| `dma_resv` 用 `ww_mutex` 而不是普通 mutex | I2 + I3 | 一次提交要锁多块缓冲区 ⇒ 必须能死锁回退（`-EDEADLK`）；⚠️ **不能"排序后依次上锁"，因为缓冲区集合由用户态给定且可任意组合** |
| `dma_resv_reserve_fences()` 必须先调，漏了 `BUG_ON`（`drivers/dma-buf/dma-resv.c:307`） | I3 | 加 fence 的时刻**不许分配内存**（§5.2）⇒ 内存只能提前预留 ⇒ ⚠️ **`BUG_ON` 不是粗暴，它标记的是一处"此处不可能有失败路径"的设计约束** |
| `enum dma_resv_usage` 是**有序**的（`include/linux/dma-resv.h`，02 §4） | I2 | 因为"等 read 的人也必须等 write"这种包含关系必须能一次表达；平级枚举做不到 |
| `dma_fence` 的三路 union（`include/linux/dma-fence.h:84-90`） | I3 | fence 生命周期严格三段（未完成→已完成→待释放），三个字段永不共存 ⇒ ⚠️ 省内存不是主因，**"状态互斥"才是** |
| `DMABUF_MOVE_NOTIFY` 默认关（`drivers/dma-buf/Kconfig:42`） | I1 + I3 | 不 pin 才能真正解决"用户态可 pin 无限内存"的安全问题，但迁移路径要在 fence 约束下工作 ⇒ ⚠️ **还标着 EXPERIMENTAL** |
| CPU 访问要显式 `begin/end_cpu_access` | I1 | CPU 只是"又一个设备"，没有特权（§3） |
| 导入方拿到的 sg 表**只能读 DMA 地址** | I1 | 见 §3 第 3 条；核心宁愿加个开关主动破坏 `page` 来抓这类误用 |

⚠️ **一条通用的读法**：这个子系统里凡是看起来"限制过严"的地方，
先问"是不是 I3 推出来的"。绝大多数时候是。

---

## §9 不变量 → 代码里的强制点

设计契约要活下来，必须有机器执行的强制点。全清单：

| 不变量 | 强制点 | 位置 | 强度 |
|---|---|---|---|
| I1 导入方不碰 `page` | `mangle_sg_table()` 打乱指针 | `drivers/dma-buf/dma-buf.c:763` | ⚠️ 需 `DMABUF_DEBUG`，**默认关** |
| I1 先 attach 再 map | `map_dma_buf` 只在 attachment 上调用 | 01 §2.3 的接口形状 | 编译期（拿不到 attachment 就没法调） |
| I2 fence 一次性 | 三个状态位无清零路径 | `include/linux/dma-fence.h:98-103` | ⚠️ **无强制，靠约定** |
| I3 signal 路径不分配内存 | `dma_fence_begin_signalling()` 假锁 | `drivers/dma-buf/dma-fence.c:284`/`:302` | ⚠️ 需 `PROVE_LOCKING`，**且要驱动自己加** |
| I3 锁序 | `dma_resv_lockdep()` 启动排练 | `drivers/dma-buf/dma-resv.c:742-780` | ⚠️ 需 `PROVE_LOCKING`；**开了就是全自动** |
| I3 加 fence 不分配 | `BUG_ON` 在 `dma_resv_add_fence` | `drivers/dma-buf/dma-resv.c:307` | ⚠️⚠️ **无条件，最强的一处** |
| 禁止无限期 fence | ⚠️⚠️ **无任何代码强制** | —— | **只有 rst 里的散文 + review** |

⚠️⚠️ **最后一行是本篇最值得记住的一句**：
**"禁止无限期 fence"这条最重要的规则，在 6.1 里没有任何编译期或运行期检查。**
它靠的是 maintainer 在 review 时挡下来。
`sw_sync` 之所以要在 Kconfig help 里自陈危险（`drivers/dma-buf/Kconfig:20`），
正是因为**代码层面拦不住它**。

⚠️ 与之对照：`BUG_ON` 那一处（`drivers/dma-buf/dma-resv.c:307`）是唯一无条件生效的强制点
⇒ **强制强度和规则重要性完全不成正比**。这是这个子系统真实的工程状态。

---

## §10 三个自测问题

**Q1：能不能给 `dma_fence_wait()` 加一个"最多等 10 秒然后返回错误"的全局兜底，
让不守约的驱动挂不死系统？**

不能，而且这是被明确否掉的方向。理由分两层：
(a) `drivers/dma-buf/dma-fence.c:83-87` 禁止一个驱动去猜别人 fence 的超时；
(b) `Documentation/driver-api/dma-buf.rst:266-268` 的"with or without a timeout"说明
超时不消除依赖环，只把确定性挂死换成随机故障。
⚠️ 真正的兜底必须在**生产方**（GPU hang recovery），而不是等待方。

**Q2：一个非 GPU 驱动（比如某个 NPU 或 ISP）想在自己的 shrinker 里等 fence，可以吗？**

契约允许（`:99-101`），但 `:108-111` 明确说 `drivers/gpu` 之外不该这么做。
⚠️ 更实际的回答是：能这么做的前提是**你的整条 fence signal 路径也遵守 GFP_ATOMIC 约束**，
而这通常意味着要重写中断处理和内存管理。**先问"能不能不在这里等"。**

**Q3：为什么不干脆让 dma-buf 直接暴露物理地址给导入方，省掉 attach/map 这一层？**

§3 已经证明这在有 IOMMU 或寻址范围差异时是错的。
⚠️ 补一条实践证据：内核认为这类误用**普遍到值得专门加一个 Kconfig 去主动破坏
`page` 指针来抓**（`drivers/dma-buf/dma-buf.c:763`，07 §6）。
一个框架会为某类误用专门造检测设施，说明这类误用真实且高频。

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 一页看完整个子系统 | 00-overview.md |
| 接口签名和返回值 | 01-api.md |
| 那些结构体的字段清单 | 02-datastructures.md |
| `dma_resv_lockdep` / `mangle_sg_table` 的实现 | 03-internals.md |
| Kconfig 各选项的取舍 | 04-extensions.md |
| 写代码时怎么不违反这些不变量 | 05-usage.md |
| 已经违反了，怎么查出来 | 07-debug.md |
| 本篇引用的原始论证、以及发现的文档缺陷 | 99-evidence.md |

⚠️ **本篇不覆盖**：具体 GPU 驱动怎么实现 hang recovery（属各 DRM 驱动）、
`ww_mutex` 的算法本身（属 `kernel/locking/`）、
用户态 fence 在更新内核里的进展（6.1 之外）。
