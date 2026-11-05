---
title: 总览
---

# 数据结构 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档链接 |
|------|-----------|---------|---------|
| **list_head** | 侵入式双向循环链表 | 零额外分配、一个结构体可同时挂多条链；内核最广泛使用的容器 | [list-head.md](list-head.md) |
| **hlist** | 哈希桶单向链表 | 头节点仅 8 字节（省一半），pprev 技巧统一删除/移动 | [hlist.md](hlist.md) |
| **llist** | 无锁单向链表 | cmpxchg 多生产者无锁，中断/NMI 上下文安全排入 | [misc-lists.md](misc-lists.md) §1 |
| **klist** | 引用计数链表 | 遍历安全——节点删除不立即释放，防止 hot-unplug 竞态 | [misc-lists.md](misc-lists.md) §2 |
| **plist** | 优先级排序链表 | 按优先级自动排序，支持 round-robin requeue | [misc-lists.md](misc-lists.md) §3 |
| **rbtree** | 红黑树 | O(log n) 有序存取，用户自定义比较函数，侵入式零分配 | [rbtree.md](rbtree.md) |
| **maple_tree** | 范围自适应 B 树 | 每节点存多个区间，不需要外部比较函数，RCU 安全读 | [maple-tree.md](maple-tree.md) |
| **interval_tree** | 区间重叠查询（增强红黑树） | 唯一能高效查"哪些区间与 [start,last] 重叠"的结构 | [misc-lists.md](misc-lists.md) §5 |
| **btree** | 固定 key 宽度 B+ 树 | 非侵入式 + mempool 保证 NOIO 上下文安全 | [misc-trees.md](misc-trees.md) §1 |
| **radix_tree** | 基数树（xarray 兼容层） | 独有 tag bit 功能（dirty/writeback 追踪），RCU 安全查找 | [misc-trees.md](misc-trees.md) §2 |
| **generic_radix_tree** | 通用类型安全基数树 | 类型安全 + 每元素最大 PAGE_SIZE，适合大元素稀疏数组 | [misc-trees.md](misc-trees.md) §3 |
| **hashtable** | 静态大小通用哈希表 | 编译期定长零运行时开销，RCU 变体可用 | [misc-hashtables.md](misc-hashtables.md) §1 |
| **rhashtable** | 可调整大小的并发哈希表 | 动态扩容 + 每桶 bit-spinlock + RCU 无锁读 | [rhashtable.md](rhashtable.md) |
| **assoc_array** | 16 路 trie（关联数组） | 任意长度 opaque key + 两阶段编辑，密钥环专用 | [misc-hashtables.md](misc-hashtables.md) §2 |
| **xarray** | 可扩展稀疏数组 | 按 unsigned long 索引存取指针，内建 spinlock + RCU，统一了 radix tree 和 IDR | [xarray.md](xarray.md) |
| **IDR/IDA** | 整数 ID 分配器 | IDA 仅分配 ID（每 ID 1 bit），推荐替代 IDR | [ida.md](ida.md) |
| **kfifo** | 通用内核 FIFO | 大小必须 2 的幂，1R1W 无锁，单调递增索引 + 掩码 | [kfifo.md](kfifo.md) |
| **circular_buf** | 环形缓冲区计数器宏 | 纯宏不管理内存，SPSC 模型，串口驱动专用 | [misc-lists.md](misc-lists.md) §4 |
| **bitmap** | 位图集合运算 | 编译期常量退化为单寄存器运算，不是容器而是运算工具包 | [bitmap.md](bitmap.md) |
| **sort** | 通用数组排序 | heapsort O(n log n) 最坏情况保证，不稳定排序 | [misc-utils.md](misc-utils.md) §1 |
| **min_heap** | 最小堆（优先队列） | O(log n) 持续维护的优先队列，pop_push 热路径优化 | [misc-utils.md](misc-utils.md) §2 |
| **stackdepot** | 栈 trace 去重存储 | append-only 永不释放，handle 含 5 bit 额外数据 | [misc-utils.md](misc-utils.md) §3 |
| **list_lru** | per-NUMA 节点 LRU 链表 | per-node spinlock + memcg 感知，与 shrinker 子系统对接 | [misc-lists.md](misc-lists.md) §6 |

---

## §2 选型决策

```
需要存储/管理什么？
│
├─ 按整数索引存取指针（如 fd → struct file）
│   ├─ 新代码 ──────────────────── xarray
│   ├─ 需要 tag bit（dirty/writeback）── radix_tree
│   └─ 只需分配/释放整数 ID ──────── IDA
│
├─ 按自定义 key 有序存取
│   ├─ 需要区间重叠查询 ──────────── interval_tree
│   ├─ 管理不重叠区间（如 VMA） ────── maple_tree
│   └─ 单点 key-value ───────────── rbtree
│
├─ 按 hash 快速查找（无序）
│   ├─ 桶数编译期确定 ────────────── hashtable
│   ├─ 需要动态扩容 + RCU 无锁读 ──── rhashtable
│   └─ 任意长度 opaque key + RCU ──── assoc_array
│
├─ 管理无序对象集合（不需要快速查找）
│   ├─ 需要双向遍历 / 尾部操作 ────── list_head
│   ├─ 哈希桶链（头多链短） ────────── hlist
│   ├─ 需要按优先级排序 ───────────── plist
│   ├─ 中断上下文无锁添加 ──────────── llist
│   └─ 遍历安全（hot-unplug） ──────── klist
│
├─ 严格先进先出数据流
│   ├─ 需要完整 FIFO（分配+读写） ──── kfifo
│   └─ 只需环形计数器宏 ───────────── circular_buf
│
├─ 特殊用途
│   ├─ 集合运算（交并补/查找连续零区域）── bitmap
│   ├─ 数组排序 ───────────────────── sort
│   ├─ 优先队列（取最小值 + 替换堆顶）── min_heap
│   ├─ 栈 trace 去重 ──────────────── stackdepot
│   ├─ 可回收缓存 LRU ─────────────── list_lru
│   └─ 类型安全稀疏数组（大元素） ──── generic_radix_tree
│
└─ 固定 key 宽度的有序映射 + mempool 保证
    └───────────────────────────────── btree
```

---

## §3 核心对比表

### 3.1 链表类

| 模块 | 方向 | 并发安全 | 排序 | 头节点大小 | 典型用途 |
|------|------|---------|------|-----------|---------|
| list_head | 双向循环 | 需外部锁 | 无 | 16 字节 | 通用对象管理、等待队列 |
| hlist | 单向 | 需外部锁 | 无 | 8 字节 | 哈希桶链 |
| llist | 单向 | 多生产者无锁（cmpxchg） | 无 | 8 字节 | 中断→softirq 任务排入 |
| klist | 双向循环 | 引用计数保护遍历 | 无 | 16 字节 + kref | 设备模型子设备链表 |
| plist | 双向循环 | 需外部锁 | 按优先级 | 16 字节 | RT 互斥锁等待队列 |

### 3.2 树类

| 模块 | 类型 | key 类型 | 侵入式 | 并发 | 独有功能 |
|------|------|---------|--------|------|---------|
| rbtree | 红黑树 | 自定义比较函数 | 是 | 需外部锁 | 用户自定义排序 |
| maple_tree | 范围自适应 B 树 | unsigned long 区间 | 是 | RCU 安全读 | 每节点多区间，不需要比较函数 |
| interval_tree | 增强红黑树 | unsigned long 区间 | 是 | 需外部锁 | 区间重叠查询 |
| btree | B+ 树 | 32/64/128-bit | 否 | 需外部锁 | mempool 保证 NOIO 安全 |
| radix_tree | 基数树 | unsigned long 索引 | 否 | RCU 安全查找 | tag bit（dirty/writeback） |
| generic_radix_tree | 通用基数树 | unsigned long 索引 | 否 | 无 | 类型安全 + 大元素 |

### 3.3 哈希表类

| 模块 | 桶数 | 并发模型 | 扩容 | key 类型 |
|------|------|---------|------|---------|
| hashtable | 编译期 2^bits | 需外部锁（RCU 变体可选） | 不可 | 任意（hash 函数） |
| rhashtable | 动态 | 每桶 bit-spinlock + RCU 无锁读 | 异步自动 | 任意（hash 函数） |
| assoc_array | trie 16 路 | 两阶段编辑 + RCU | N/A | 任意长度 opaque |

### 3.4 数组/ID 类

| 模块 | 索引类型 | 存储内容 | 并发 | 底层关系 |
|------|---------|---------|------|---------|
| xarray | unsigned long | 指针 | 内建 spinlock + RCU | 统一了 radix_tree 和 IDR |
| IDR | unsigned int | 指针 | 需外部锁 | xarray 薄包装（已弃用） |
| IDA | unsigned int | 纯 ID（每 ID 1 bit） | 内建锁 | xarray 薄包装（推荐） |

### 3.5 FIFO/缓冲区类

| 模块 | 完整性 | 并发 | 大小约束 | 典型用途 |
|------|--------|------|---------|---------|
| kfifo | 完整 FIFO（分配+读写+泛型） | 1R1W 无锁 | 必须 2 的幂 | 中断→进程通信、串行缓冲 |
| circular_buf | 纯宏（不管理内存） | SPSC | 必须 2 的幂 | 串口驱动 UART FIFO |

### 3.6 工具类

| 模块 | 功能 | 时间复杂度 | 使用量 | 典型用途 |
|------|------|-----------|--------|---------|
| bitmap | 位图集合运算 | O(n/64) | 1965 处 | CPU 掩码、IRQ 位图 |
| sort | 数组排序 | O(n log n) | 1272 处 | 驱动初始化排序查找表 |
| min_heap | 优先队列 | O(log n) 插入/弹出 | 44 处 | perf 事件调度 |
| stackdepot | 栈 trace 去重 | O(1) 查找 | 44 处 | KASAN/KMSAN/SLUB 调试 |
| list_lru | per-NUMA LRU | O(1) 添加/删除 | 157 处 | dentry/inode 缓存回收 |

---

## §4 使用规模

以下数据来自对内核源码树的实际 grep 统计：

| 模块 | grep 命令 | 匹配数 |
|------|----------|--------|
| list_head | `grep -rw 'list_head\|LIST_HEAD\|list_add\|list_del\|list_for_each\|INIT_LIST_HEAD' --include='*.c' --include='*.h' . \| wc -l` | 35091 |
| hlist | `grep -rw 'hlist_head\|hlist_node\|HLIST_HEAD\|hlist_add\|hlist_del\|hlist_for_each' --include='*.c' --include='*.h' . \| wc -l` | 2605 |
| rbtree | `grep -rw 'rb_root\|rb_node\|rb_insert\|rb_erase\|rb_first\|rb_next\|RB_ROOT\|rb_link_node' --include='*.c' --include='*.h' . \| wc -l` | 5741 |
| xarray | `grep -rw 'XA_FLAGS\|xa_load\|xa_store\|xa_erase\|xarray\|DEFINE_XARRAY\|xa_alloc\|xa_for_each\|xa_init' --include='*.c' --include='*.h' . \| wc -l` | 1302 |
| kfifo | `grep -rw 'kfifo\|KFIFO\|kfifo_alloc\|kfifo_put\|kfifo_get\|kfifo_in\|kfifo_out\|DECLARE_KFIFO\|DEFINE_KFIFO' --include='*.c' --include='*.h' . \| wc -l` | 614 |
| bitmap | `grep -rw 'bitmap_alloc\|bitmap_free\|bitmap_zero\|bitmap_fill\|bitmap_and\|bitmap_or\|bitmap_intersects\|bitmap_subset\|bitmap_weight\|bitmap_find\|DECLARE_BITMAP\|BITMAP' --include='*.c' --include='*.h' . \| wc -l` | 1965 |
| maple_tree | `grep -rw 'maple_tree\|MA_STATE\|mt_load\|mt_store\|mt_erase\|mt_for_each\|mtree_load\|mtree_store\|MTREE_INIT' --include='*.c' --include='*.h' . \| wc -l` | 315 |
| rhashtable | `grep -rw 'rhashtable\|rhltable\|rhashtable_init\|rhashtable_insert\|rhashtable_remove\|rhashtable_lookup\|rht_for_each' --include='*.c' --include='*.h' . \| wc -l` | 519 |
| hashtable | `grep -rw 'DEFINE_HASHTABLE\|DECLARE_HASHTABLE\|hash_init\|hash_add\|hash_del\|hash_for_each\|hash_for_each_safe' --include='*.c' --include='*.h' . \| wc -l` | 517 |
| llist | `grep -rw 'llist_head\|llist_node\|llist_add\|llist_del_all\|llist_for_each\|LLIST_HEAD' --include='*.c' --include='*.h' . \| wc -l` | 466 |
| klist | `grep -rw 'klist\|klist_node\|klist_init\|klist_add\|klist_del\|klist_iter\|KLIST' --include='*.c' --include='*.h' . \| wc -l` | 205 |
| plist | `grep -rw 'plist_head\|plist_node\|plist_add\|plist_del\|plist_init\|PLIST_HEAD' --include='*.c' --include='*.h' . \| wc -l` | 95 |
| interval_tree | `grep -rw 'interval_tree\|INTERVAL_TREE\|interval_insert\|interval_remove\|interval_iter' --include='*.c' --include='*.h' . \| wc -l` | 61 |
| btree | `grep -rw 'btree_init\|btree_insert\|btree_remove\|btree_lookup\|btree_grim_visitor\|DEFINE_BTREE' --include='*.c' --include='*.h' . \| wc -l` | 38 |
| radix_tree | `grep -rw 'radix_tree_root\|radix_tree_insert\|radix_tree_delete\|radix_tree_lookup\|RADIX_TREE\|radix_tree_iter' --include='*.c' --include='*.h' . \| wc -l` | 522 |
| generic_radix_tree | `grep -rw 'genradix\|GENRADIX\|genradix_init\|genradix_alloc\|genradix_free\|genradix_ptr' --include='*.c' --include='*.h' . \| wc -l` | 38 |
| assoc_array | `grep -rw 'assoc_array\|ASSOC_ARRAY\|assoc_array_insert\|assoc_array_remove\|assoc_array_find' --include='*.c' --include='*.h' . \| wc -l` | 31 |
| IDR/IDA | `grep -rw 'ida_alloc\|ida_free\|ida_simple_get\|ida_simple_remove\|idr_alloc\|idr_find\|idr_remove\|DEFINE_IDA\|DEFINE_IDR' --include='*.c' --include='*.h' . \| wc -l` | 1720 |
| sort | `grep -rw 'sort\|sort_r' --include='*.c' --include='*.h' . \| wc -l` | 1272 |
| min_heap | `grep -rw 'min_heap\|MIN_HEAP\|min_heap_push\|min_heap_pop\|min_heap_peek\|min_heapify' --include='*.c' --include='*.h' . \| wc -l` | 44 |
| stackdepot | `grep -rw 'stack_depot\|STACK_DEPOT\|stack_depot_save\|stack_depot_fetch\|depot_stack_handle' --include='*.c' --include='*.h' . \| wc -l` | 44 |
| list_lru | `grep -rw 'list_lru\|LIST_LRU\|list_lru_add\|list_lru_del\|list_lru_init\|list_lru_walk' --include='*.c' --include='*.h' . \| wc -l` | 157 |
| circular_buf | `grep -rw 'circular_buf\|CIRC_BUF\|CIRC_CNT\|CIRC_SPACE\|CIRC_TO_END' --include='*.c' --include='*.h' . \| wc -l` | 120 |

---

## §5 设计模式速览

### 5.1 侵入式设计

list_head、hlist、rbtree、hashtable、plist、klist、interval_tree 都采用侵入式设计——容器节点嵌入用户结构体，通过 `container_of()` 反查。优势：
- 零额外分配（节点和数据在同一内存块）
- 类型无关（同一套函数管理任何结构体）
- 多链归属（一个结构体可嵌入多个节点，同时属于多个容器）

### 5.2 RCU 并发保护

xarray、rhashtable、radix_tree、maple_tree、hashtable（RCU 变体）都内建 RCU 支持——读侧无锁（`rcu_read_lock` 保护），写侧用内部锁或外部锁。这是内核并发数据结构的标准模式。

### 5.3 原子操作与无锁技术

- **llist**：cmpxchg 实现多生产者无锁
- **kfifo**：单调递增索引 + smp_wmb 实现 1R1W 无锁
- **bitmap**：编译期常量退化为单寄存器位运算
- **xarray**：内建 spinlock + RCU，xa_load 无锁

### 5.4 位运算索引

xarray/radix_tree 使用位索引分块查找（每层消耗 6 位），maple_tree 使用 pivot 划分区间，bitmap 使用 `unsigned long` 数组按位操作——都避免了比较函数开销。

### 5.5 统一底层

- xarray 统一了 radix_tree 和 IDR——同一个数据结构通过 `XA_FLAGS_ALLOC` 切换模式
- radix_tree 自 5.x 起是 xarray 的薄兼容包装（`#define radix_tree_root xarray`）
- IDA 是 xarray 的薄包装（`struct ida` 内嵌 `struct xarray`）
- hashtable 是 hlist_head 数组上的宏——零运行时开销

---

## §6 锁模型对比

| 模块 | 锁模型 | 读侧 | 写侧 | 说明 |
|------|--------|------|------|------|
| list_head | 需外部锁 | 外部锁 | 外部锁 | 最灵活，锁策略完全由用户决定 |
| hlist | 需外部锁 | 外部锁/RCU | 外部锁 | 常配合 RCU 用于哈希桶 |
| llist | 无锁（cmpxchg） | N/A | cmpxchg | 多生产者无锁，单消费者批量取出 |
| klist | 引用计数 | klist_iter 保护 | kref | 遍历安全，节点删除不立即释放 |
| plist | 需外部锁 | 外部锁 | 外部锁 | 与 rt_mutex 配合 |
| rbtree | 需外部锁 | 外部锁 | 外部锁 | 用户完全控制锁粒度 |
| maple_tree | RCU | rcu_read_lock | 内建 ma_write_lock | 读侧无锁，写侧内部锁 |
| interval_tree | 需外部锁 | 外部锁 | 外部锁 | 基于 rbtree，锁策略同 rbtree |
| btree | 需外部锁 | 外部锁 | 外部锁 | 非侵入式，锁在 btree 外部 |
| radix_tree | RCU | rcu_read_lock | 内建 radix_tree_lock | 读侧无锁，tag 操作需写锁 |
| hashtable | 需外部锁/RCU | 外部锁/RCU | 外部锁 | RCU 变体（`hashtable_rcu`）可选 |
| rhashtable | 每桶 bit-spinlock + RCU | rcu_read_lock | bit-spinlock | 动态扩容，读侧完全无锁 |
| assoc_array | 两阶段编辑 + RCU | rcu_read_lock | 编辑锁 | 密钥环专用 |
| xarray | 内建 spinlock + RCU | rcu_read_lock | xa_lock | 读侧无锁，写侧内部 spinlock |
| IDR/IDA | 内建 spinlock | 内建锁 | 内建锁 | xarray 包装，锁在内部 |
| kfifo | 无锁（1R1W） | smp_load_acquire | smp_store_release | 单调递增索引 + 掩码，1 读者 1 写者无锁 |
| circular_buf | 无锁（SPSC） | volatile 读 | volatile 写 | 纯宏，无内存屏障 |
| bitmap | 无（单线程）或原子 | 原子操作 | 原子操作 | 编译期常量退化为单寄存器 |
| list_lru | per-node spinlock | spin_lock | spin_lock | 每个 NUMA 节点独立锁 |

---

## §7 组合使用模式

### 7.1 list_head + spinlock（工作队列）

`kernel/workqueue.c` 中，每个 workqueue 用 `list_head` 管理 pending work items，配合 `spinlock` 保护链表操作。这是内核最经典的"链表 + 锁"组合。

### 7.2 rbtree + spinlock（定时器/RT 互斥锁）

`kernel/time/timer.c` 用 rbtree 管理定时器，spinlock 保护树操作。`kernel/locking/rtmutex.c` 用 rbtree 管理等待队列中的任务，按优先级排序。

### 7.3 xarray + radix_tree（页缓存）

`mm/page-writeback.c`、`mm/shmem.c` 等内存管理文件中，xarray/radix_tree 管理页缓存，tag bit 追踪 dirty/writeback 状态。新代码用 xarray，遗留代码用 radix_tree。

### 7.4 seq_file + list_head（/proc 输出）

`fs/proc/` 下的文件大量使用 `seq_file` 框架遍历 `list_head` 链表输出信息。`seq_printf` 格式化输出，`list_for_each` 遍历链表。

### 7.5 bitmap + cpumask（调度器）

`kernel/sched/` 中，cpumask 本质是 bitmap 的封装。调度器用 bitmap 运算管理 CPU 集合：`cpumask_and`、`cpumask_weight`、`cpumask_first` 等。

### 7.6 kfifo + tty（串口驱动）

`drivers/tty/` 中，kfifo 用于串口缓冲。中断上下文写入 kfifo，进程上下文读取。1R1W 无锁特性避免了中断与进程之间的竞争。

### 7.7 hashtable + RCU（网络）

`net/sunrpc/auth_gss/auth_gss.c` 等网络代码中，hashtable 配合 RCU 实现无锁读的热路径。桶链用 hlist，读侧 rcu_read_lock，写侧 spinlock。

### 7.8 rbtree + interval_tree（VMA 管理）

`mm/mmap.c` 中，VMA（虚拟内存区域）同时挂在 rbtree（按地址排序）和 interval_tree（支持重叠查询）。interval_tree 基于 rbtree 增强，维护子树最大 end 值。

### 7.9 list_lru + shrinker（缓存回收）

`mm/list_lru.c` 实现 per-NUMA 节点 LRU 链表，与 shrinker 子系统对接。内存压力时 shrinker 回调遍历 list_lru 回收 dentry/inode 缓存。

---

## §8 阅读顺序建议

### 8.1 入门路径（理解内核数据结构基础）

1. **list_head** → 内核最广泛使用的容器，理解侵入式设计
2. **hlist** → 理解哈希桶链的特殊优化
3. **rbtree** → 理解有序容器
4. **xarray** → 理解现代索引容器

### 8.2 需要队列 → list_head → kfifo

先理解 list_head 的侵入式设计，再学 kfifo 的无锁 FIFO。如果只需要简单环形缓冲，看 circular_buf。

### 8.3 需要有序映射 → rbtree → maple_tree → interval_tree

先学 rbtree 理解红黑树基础，再看 maple_tree（VMA 新实现），最后看 interval_tree（区间重叠查询）。

### 8.4 需要快速查找 → hashtable → rhashtable

先学 hashtable（简单静态哈希），再看 rhashtable（动态扩容 + RCU 无锁读）。assoc_array 只有密钥环在用，一般不需要。

### 8.5 需要按整数索引 → xarray → IDA

xarray 是现代内核的标准选择。如果只需分配整数 ID（不存指针），用 IDA。radix_tree 和 IDR 是遗留接口，新代码不应用。

### 8.6 需要特殊用途

- 集合运算 → bitmap
- 优先队列 → min_heap
- 栈 trace 去重 → stackdepot
- 缓存回收 → list_lru
- 中断上下文无锁添加 → llist
- 遍历安全 → klist
- 优先级排序 → plist

---

## §9 容易混淆的关系

| 容易混淆的对 | 区别要点 |
|-------------|----------|
| list_head vs hlist | list_head 双向循环（16 字节头）；hlist 单向（8 字节头），专用于哈希桶 |
| xarray vs radix_tree | radix_tree 是 xarray 的兼容层；独有 tag bit 功能；新代码用 xarray |
| IDR vs IDA | IDR 映射 ID→指针（已弃用）；IDA 仅分配 ID（每 ID 1 bit，推荐） |
| hashtable vs rhashtable | hashtable 编译期定长；rhashtable 动态扩容 + 每桶锁 + RCU |
| kfifo vs circular_buf | kfifo 是完整 FIFO（分配+读写+类型泛型）；circular_buf 只是计数器宏 |
| rbtree vs maple_tree | rbtree 每节点一个 key-value；maple_tree 每节点多个区间，不需要外部比较函数 |
| btree vs maple_tree | btree 非侵入式 + mempool；maple_tree 侵入式 + RCU |
| sort vs min_heap | sort 是一次性排序（heapsort）；min_heap 是持续维护的优先队列 |

---

## §10 文档索引

| 文档 | 覆盖模块 |
|------|----------|
| [list-head.md](list-head.md) | list_head |
| [hlist.md](hlist.md) | hlist |
| [rbtree.md](rbtree.md) | rbtree |
| [xarray.md](xarray.md) | xarray |
| [kfifo.md](kfifo.md) | kfifo |
| [bitmap.md](bitmap.md) | bitmap |
| [maple-tree.md](maple-tree.md) | maple_tree |
| [rhashtable.md](rhashtable.md) | rhashtable |
| [ida.md](ida.md) | IDR / IDA |
| [misc-lists.md](misc-lists.md) | llist / klist / plist / circular_buf / interval_tree / list_lru |
| [misc-trees.md](misc-trees.md) | btree / radix_tree / generic_radix_tree |
| [misc-hashtables.md](misc-hashtables.md) | hashtable / assoc_array |
| [misc-utils.md](misc-utils.md) | sort / min_heap / stackdepot |
