# 链表与队列类数据结构（合并文档）

> llist / klist / plist / circular_buf / interval_tree / list_lru 六个模块的简明参考。每个模块 20-50 行，覆盖定位、核心 API、使用注意。

---

## 1. llist — 无锁链表

**头文件**：`include/linux/llist.h`（254 行）；**实现**：`lib/llist.c`（88 行）
**使用量**：`struct llist_head` 出现在 45 个文件（89 处）

### 定位

**功能**：提供完全无锁的单向链表，允许任意数量的生产者并发向链表头部添加节点，由单个消费者一次性取出全部节点进行批量处理。

**实现方式**：整个链表只有一个指针 `head->first`，所有修改操作（add/del）都通过对这个指针的 `cmpxchg`（添加、删除第一个）或 `xchg`（删除全部）完成。不需要任何 spinlock、atomic_t 或内存屏障——单条原子指令即保证多生产者安全。遍历操作不在链表上进行，而是先 `llist_del_all` 取出整条链，再在本地遍历已脱离链表的节点。

**应用场景**：中断/NMI handler 向 softirq/工作队列排入任务——生产者（中断上下文）不能睡眠也不能持锁，消费者（softirq）批量取出后逐个处理。典型用法包括 `irq_work`（`kernel/irq_work.c`）、`perf_event` 溢出通知、`block/blk-mq` 的完成队列。

### 核心结构

```c
struct llist_head { struct llist_node *first; };
struct llist_node { struct llist_node *next; };
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `llist_add(new, head)` | 原子添加到头部 | 返回 true 表示之前为空 |
| `llist_add_batch(new_first, new_last, head)` | 批量添加预链接的节点链 | 一次 `cmpxchg` |
| `llist_del_all(head)` | 原子取出全部节点 | 用 `xchg`，返回取出的链表头 |
| `llist_del_first(head)` | 原子取出第一个节点 | 用 `cmpxchg` |
| `llist_empty(head)` | 检查是否为空 | 非 RCU 安全，结果可能立即过时 |
| `llist_for_each(pos, node)` | 遍历已取出的链表 | 从新到旧顺序 |
| `llist_for_each_safe(pos, n, node)` | 遍历时可安全删除 | |
| `llist_reverse_order(head)` | 反转链表 | 取出后翻转为从旧到新 |

### 使用注意

- **只能先取后遍历**：`llist_for_each` 遍历的是已从链表取出的节点，不是链表本身
- **单消费者模型**：`llist_del_all` 是唯一的批量消费接口；多个消费者并发 `del_all` 会各自取出不相交的子集
- **典型用法**：中断下半部工作队列、NMI handler 将工作排入 llist，softirq 中 `del_all` + 遍历处理

---

## 2. klist — kobject 引用计数链表

**头文件**：`include/linux/klist.h`（67 行）；**实现**：`lib/klist.c`（407 行）
**使用量**：`struct klist` 出现在 8 个文件（34 处）

### 定位

**功能**：为内核设备模型提供安全的链表遍历——当遍历者正在访问某个节点时，另一个线程删除该节点不会导致 use-after-free。

**实现方式**：每个 `klist_node` 内嵌 `kref` 引用计数和 spinlock 保护的双向链表。遍历者通过 `klist_next`/`klist_prev` 推进游标时，自动对当前节点 `get`（加引用）、对新节点 `get`、对旧节点 `put`（减引用）。`klist_del` 只做两件事：将节点指针低位标记为 dead（逻辑删除），然后 `put` 引用。当最后一个遍历者 `put` 使引用归零时，回调函数执行真正的内存释放。初始化时传入的 `get`/`put` 回调通常绑定到 kobject 的 `kref_get`/`kobject_put`。

**应用场景**：几乎专用于 `drivers/base/` 中的设备模型——`struct device` 的子设备链表（`klist_children`）、`struct bus_type` 的设备链表、`struct class` 的设备链表。这些场景需要支持"遍历所有子设备时某个设备被 hot-unplug 删除"的并发安全。

### 核心结构

```c
struct klist {
    spinlock_t        k_lock;
    struct list_head  k_list;
    void             (*get)(struct klist_node *);
    void             (*put)(struct klist_node *);
};
struct klist_node {
    void             *n_klist;   // 低位 = dead 标记
    struct list_head  n_node;
    struct kref       n_ref;
};
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `klist_init(k, get, put)` | 初始化 | get/put 是引用计数回调 |
| `klist_add_tail(n, k)` | 尾部添加 | spinlock 保护 |
| `klist_add_head(n, k)` | 头部添加 | |
| `klist_del(n)` | 标记删除 | 异步——refcount 到 0 才真正释放 |
| `klist_remove(n)` | 同步删除 | 阻塞直到 refcount 归零 |
| `klist_next(i)` / `klist_prev(i)` | 迭代器推进 | 自动管理引用计数 |

### 使用注意

- `klist_del` 不立即释放——如果此刻有遍历者持有该节点的引用，节点在逻辑上已删除但内存仍存在
- get/put 回调通常绑定到 kobject 的 `kref_get`/`kobject_put`
- 几乎只被 `drivers/base/` 使用（设备/总线/类的子设备链表）

---

## 3. plist — 优先级排序链表

**头文件**：`include/linux/plist.h`（301 行）；**实现**：`lib/plist.c`（263 行）
**使用量**：`struct plist_head` 出现在 7 个文件（33 处）

### 定位

**功能**：按优先级排序的双层链表，支持 O(K) 插入（K=不同优先级数）、O(1) 删除、O(1) 取最高优先级节点，以及同优先级内的 round-robin 重排队。

**实现方式**：每个 `plist_node` 携带两条链表指针。第一层 `prio_list` 将不同优先级的代表节点串成有序链表——插入时沿 `prio_list` 线性扫描找到正确位置（O(K)）。第二层 `node_list` 将所有节点（不论优先级）串成一条完整的 FIFO 链表——同一优先级的节点在 `node_list` 中连续排列。`plist_first` 直接返回 `node_list` 的第一个节点（最高优先级），`plist_requeue` 将节点移到同优先级组的末尾（通过 `prio_list` 定位组的边界，然后在 `node_list` 上移动）。

**应用场景**：实时互斥锁（`kernel/locking/rtmutex.c`）的等待队列——RT 调度需要按优先级排序的等待者、支持优先级继承、支持同优先级 round-robin。futex 等待队列（`kernel/futex/`）同样使用 plist 管理阻塞任务。

### 核心结构

```c
struct plist_head { struct list_head node_list; };
struct plist_node {
    int              prio;
    struct list_head prio_list;   // 不同优先级之间的链
    struct list_head node_list;   // 同优先级内的链
};
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `plist_head_init(head)` | 初始化 | |
| `plist_node_init(node, prio)` | 初始化节点并设置优先级 | |
| `plist_add(node, head)` | 按优先级插入 | O(K)，K 为不同优先级数 |
| `plist_del(node, head)` | 删除 | O(1) |
| `plist_requeue(node, head)` | 移到同优先级队尾 | round-robin 调度用 |
| `plist_first(head)` | 返回最高优先级节点 | prio 值最小 |
| `plist_for_each(pos, head)` | 遍历 | 按优先级降序 |

### 使用注意

- `prio` 值越小优先级越高（与调度器一致）
- `plist_requeue` 是 RT 调度中时间片耗尽后重新排队的核心操作
- 主要消费者：`kernel/futex/`、`kernel/locking/rtmutex.c`

---

## 4. circular_buf — 环形缓冲区宏

**头文件**：`include/linux/circ_buf.h`（37 行）；**实现**：纯头文件，无 .c
**使用量**：`CIRC_` 宏出现在 75 个文件（259 处）

### 定位

**功能**：提供环形缓冲区的四个基本计数器——当前数据量、可用空间、以及从 head/tail 到物理缓冲区末尾的连续段长度。不管理缓冲区内存本身，只管理 head/tail 索引的算术。

**实现方式**：四个纯宏（`CIRC_CNT`、`CIRC_SPACE`、`CIRC_CNT_TO_END`、`CIRC_SPACE_TO_END`），所有索引运算基于 `& (size - 1)` 位掩码，要求缓冲区大小为 2 的幂。留一个空位区分满和空（`CIRC_SPACE` 范围 0 到 size-1）。宏不访问缓冲区内容，只操作 `head`、`tail`、`size` 三个整数——因此天然适用于 head 和 tail 由不同 CPU 更新的 SPSC 场景（配合 `smp_load`/`smp_store` 使用 `READ_ONCE`/`WRITE_ONCE`）。

**应用场景**：串口驱动（`drivers/tty/serial/` 中约 20 个驱动）的 UART FIFO 管理——硬件 FIFO 是固定大小的环形缓冲区，驱动用 `CIRC_CNT` 判断有多少待发数据、用 `CIRC_SPACE_TO_END` 计算一次 DMA 能搬运多少字节。USB 串口（`drivers/usb/serial/`）和字符设备缓冲区也大量使用。

### 核心结构

```c
struct circ_buf { char *buf; int head; int tail; };
```

### 核心 API（宏）

| API | 说明 | 备注 |
|-----|------|------|
| `CIRC_CNT(head, tail, size)` | 缓冲区中的数据量 | |
| `CIRC_SPACE(head, tail, size)` | 可用空间 | 始终留一个空位区分满/空 |
| `CIRC_CNT_TO_END(head, tail, size)` | 从 tail 到物理末尾的连续数据量 | 用于单次 memcpy |
| `CIRC_SPACE_TO_END(head, tail, size)` | 从 head 到物理末尾的连续空间 | |

### 使用注意

- **SPSC 模型**：设计用于单生产者单消费者，head 和 tail 分别由不同端更新
- 不管理内存分配——调用者自行 `kmalloc` 缓冲区
- 典型消费者：串口驱动（`drivers/tty/serial/`）、USB 串口

---

## 5. interval_tree — 区间树

**头文件**：`include/linux/interval_tree.h`（30 行）；**泛型模板**：`include/linux/interval_tree_generic.h`（187 行）；**实现**：`lib/interval_tree.c`（17 行）
**使用量**：`struct interval_tree_node` 出现在 15 个文件（45 处）

### 定位

**功能**：管理一组不重叠或可重叠的数值区间 `[start, last]`，支持高效查询"哪些区间与给定范围重叠"——这是普通红黑树无法直接回答的问题。

**实现方式**：在 `rb_root_cached`（带缓存最左叶子的红黑树）基础上增加增强字段 `__subtree_last`——每个节点记录以其为根的子树中所有区间的最大 `last` 值。查询时从根开始：如果当前节点的 `start` 大于查询上界，走向左子树；如果当前子树的 `__subtree_last` 小于查询下界，整个子树可以跳过（O(log n) 快速排除的核心）；否则当前节点可能重叠，继续检查并遍历。通过 `INTERVAL_TREE_DEFINE` 宏从泛型模板（`include/linux/interval_tree_generic.h`）实例化，可自定义节点类型和比较方式。

**应用场景**：内存管理子系统（`mm/mmap.c`）用 interval tree 管理进程的 VMA（虚拟内存区域）——查询某个地址范围与哪些 VMA 重叠，用于 `mmap`/`munmap`/`mremap` 的冲突检测。GPU DRM 子系统（`drivers/gpu/drm/`）用 interval tree 管理 VRAM 地址空间分配。

### 核心结构

```c
struct interval_tree_node {
    struct rb_node  rb;
    unsigned long   start;          // 区间起点
    unsigned long   last;           // 区间终点（含入）
    unsigned long   __subtree_last; // 增强字段：子树最大 last
};
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `interval_tree_insert(node, root)` | 插入节点 | 自动维护 subtree_last |
| `interval_tree_remove(node, root)` | 删除节点 | |
| `interval_tree_iter_first(root, start, last)` | 查找第一个与 [start,last] 重叠的节点 | O(log n) 快速排除 |
| `interval_tree_iter_next(node, start, last)` | 查找下一个重叠节点 | |

### 使用注意

- 区间是闭区间 `[start, last]`
- 泛型模板允许自定义节点类型（`INTERVAL_TREE_DEFINE`），但 in-tree 只有 `unsigned long` 版本
- 主要消费者：`mm/mmap.c`（VMA 区间管理）、`drivers/gpu/drm/`

---

## 6. list_lru — 每 NUMA 节点 LRU 链表

**头文件**：`include/linux/list_lru.h`（221 行）；**实现**：`mm/list_lru.c`（605 行）
**使用量**：`struct list_lru` 出现在 21 个文件（87 处）

### 定位

**功能**：为内核可回收缓存（dentry cache、inode cache、文件系统私有 LRU）提供 per-NUMA-node、可选 memcg 感知的 LRU 链表基础设施，并与 shrinker 子系统对接实现内存压力下的自动回收。

**实现方式**：`struct list_lru` 内部维护一个 per-NUMA-node 的 `list_lru_node` 数组，每个节点有自己的 `spinlock_t` 和独立的 `list_head` 链表——避免跨节点争用。启用 `CONFIG_MEMCG_KMEM` 时，每个 node 内还嵌套 per-memcg 的子链表（通过 `xarray` 索引 memcg id）。`list_lru_walk_one` 是 shrinker 的核心扫描路径：遍历 LRU 链表中的项目，对每个调用 `isolate` 回调，回调返回 `LRU_REMOVED`（释放）、`LRU_ROTATE`（移到尾部继续等待）、`LRU_SKIP`（跳过）等枚举决定项目命运。`nr_items` 计数器允许瞬态负值（并发 add/del 的中间状态）。

**应用场景**：虚拟文件系统层（`fs/super.c`、`fs/inode.c`）的 dentry 和 inode 缓存 LRU——当系统内存紧张时，shrinker 通过 `list_lru_walk_one` 扫描这些 LRU，释放不再使用的 dentry/inode 以回收内存。文件系统（如 ext4、XFS、btrfs）也注册自己的 per-fs list_lru 管理私有缓存对象。

### 核心结构

```c
struct list_lru {
    struct list_lru_node *node;        // per-NUMA-node 数组
    // memcg 相关字段（CONFIG_MEMCG_KMEM）...
};
struct list_lru_node {
    spinlock_t          lock;
    struct list_lru_one lru;
    long                nr_items;
} ____cacheline_aligned_in_smp;
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `list_lru_init(lru)` | 初始化 | |
| `list_lru_destroy(lru)` | 销毁 | |
| `list_lru_add(lru, item)` | 添加到对应节点/cgroup 的 LRU 尾部 | |
| `list_lru_del(lru, item)` | 从 LRU 中删除 | |
| `list_lru_count_one(lru, nid, memcg)` | 统计特定节点/cgroup 的项目数 | |
| `list_lru_walk_one(lru, nid, memcg, isolate, cb_arg, nr_to_walk)` | 扫描并调用 isolate 回调 | shrinker 核心路径 |
| `list_lru_isolate(list, item)` | 在回调中从 LRU 取出 | |
| `list_lru_isolate_move(list, item, head)` | 取出并移到释放列表 | |

### 使用注意

- `walk_one` 的 `isolate` 回调返回 `LRU_REMOVED`/`LRU_ROTATE`/`LRU_SKIP` 等枚举决定每个项目的命运
- `nr_items` 可为负数（表示收缩过程中的瞬态不一致）
- 主要消费者：`fs/super.c`（super_block 的 dentry/inode LRU）、`fs/inode.c`
