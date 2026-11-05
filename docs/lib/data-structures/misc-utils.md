# 工具类数据结构（合并文档）

> sort / min_heap / stackdepot 三个模块的简明参考。每个模块 20-50 行，覆盖定位、核心 API、使用注意。

---

## 1. sort — 通用排序

**头文件**：`include/linux/sort.h`（16 行）；**实现**：`lib/sort.c`（292 行）
**使用量**：`sort()` 在 117 个文件中调用（454 处）；`sort_r()` 在 6 个文件中调用

### 定位

**功能**：提供内核通用的数组排序，接口与用户态 `qsort` 类似（base/num/size/cmp），但内部使用 **heapsort** 而非 quicksort。`sort_r` 是可重入版本，额外接受 `priv` 上下文指针传入比较和交换函数。

**实现方式**：自底向上的 heapsort，最坏情况 O(n log n)、零额外内存。选择 heapsort 而非 quicksort 是因为内核不能容忍 O(n²) 最坏情况——网络协议或用户可控数据的排序可能被攻击者利用触发最坏情况。交换函数根据数据对齐自动选择最优策略：64-bit 对齐用 `swap_words_64`（8 字节块）、32-bit 对齐用 `swap_words_32`、否则逐字节 `swap_bytes`。`parent()` 函数用无分支位运算计算堆父节点偏移，避免整数除法开销。`swap_func` 传 NULL 时使用内部自动选择的交换函数。

**应用场景**：117 个文件使用，454 处调用。典型场景：驱动初始化时排序查找表（如 ACPI 表、PCI 设备表）、网络规则排序（iptables/nftables 规则按优先级排列）、文件系统元数据排序。`sort_r` 使用较少（6 个文件），主要用于需要外部上下文参与比较的场景。

### 核心类型

```c
typedef int  (*cmp_func_t)(const void *a, const void *b);
typedef int  (*cmp_r_func_t)(const void *a, const void *b, const void *priv);
typedef void (*swap_func_t)(void *a, void *b, int size);
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `sort(base, num, size, cmp_func, swap_func)` | 排序数组 | swap_func 可为 NULL（使用默认交换） |
| `sort_r(base, num, size, cmp_r_func, swap_r_func, priv)` | 可重入排序 | priv 传入 cmp 和 swap |

### 使用注意

- **heapsort 不稳定**：相等元素的相对顺序不保证。需要稳定排序不要用这个
- `swap_func` 传 NULL 时使用内部优化的交换函数（按对齐自动选 64-bit/32-bit/逐字节）
- 内部优化亮点：`parent()` 用无分支位运算计算堆父节点偏移，避免整数除法
- 典型消费者：驱动初始化时排序查找表、ACPI 表排序、网络规则排序

---

## 2. min_heap — 最小堆（优先队列）

**头文件**：`include/linux/min_heap.h`（134 行）；**实现**：纯头文件（`static __always_inline`）
**使用量**：仅 3 个文件（65 处）。主要消费者：`kernel/events/core.c`（perf 事件调度）

### 定位

**功能**：提供基于数组的最小堆（优先队列），支持 O(log n) 的插入和弹出、O(log n) 的 pop+push 合并操作、以及 O(n) 的 Floyd 建堆。

**实现方式**：纯头文件实现——所有函数标记为 `static __always_inline`，编译器在每个调用点将比较和交换回调内联展开，消除函数指针间接调用开销。数据结构是经典的数组堆：`data[nr]` 存储元素，父节点在 `i/2`、子节点在 `2i` 和 `2i+1`。`min_heap_callbacks` 提供 `less`（比较）和 `swp`（交换）两个回调，操作 `void *` 数据。`min_heap_pop_push` 是热路径优化——先弹出堆顶再插入新元素只需一次下沉（而非 pop + push 的两次），在频繁替换堆顶的场景中显著减少比较次数。

**应用场景**：6.1 中使用量极低（仅 3 个文件）。主要消费者是 perf 事件调度器（`kernel/events/core.c`）——perf 需要维护一个按调度时间排序的事件堆，频繁用 `pop_push` 替换最早到期的事件。大多数内核优先队列场景使用 `plist`（调度器、rtmutex）或 `rbtree`（定时器），min_heap 的 niche 是需要 O(1) 取最小值 + O(log n) 替换堆顶且对回调内联有极致要求的场景。

### 核心结构

```c
struct min_heap {
    void *data;   // 堆元素数组
    int   nr;     // 当前元素数
    int   size;   // 最大容量
};
struct min_heap_callbacks {
    int   elem_size;
    bool (*less)(const void *lhs, const void *rhs);
    void (*swp)(void *lhs, void *rhs);
};
```

### 核心 API

| API | 复杂度 | 说明 | 备注 |
|-----|--------|------|------|
| `min_heap_push(heap, element, func)` | O(log n) | 插入元素 | 上浮调整 |
| `min_heap_pop(heap, func)` | O(log n) | 弹出最小值 | 用末尾元素替代后下沉 |
| `min_heap_pop_push(heap, element, func)` | O(log n) | 弹出最小 + 插入新元素 | 一次下沉，比 pop+push 高效 |
| `min_heapify(heap, pos, func)` | O(log n) | 指定位置下沉 | |
| `min_heapify_all(heap, func)` | O(n) | Floyd 算法建堆 | 线性时间 |

### 使用注意

- **使用量极低**：6.1 中只有 perf 子系统在用。大多数优先队列场景用 plist 或 rbtree
- `min_heap_pop_push` 是热路径优化——perf 事件调度中频繁替换堆顶
- 容量固定（`size` 字段），不自动增长——调用者管理 `data` 数组的分配
- 没有 `min_heap_peek` 接口——直接读 `heap->data[0]`

---

## 3. stackdepot — 栈 traces 去重存储

**头文件**：`include/linux/stackdepot.h`（75 行）；**实现**：`lib/stackdepot.c`（535 行）
**使用量**：29 个文件（166 处）。主要消费者：`mm/kasan/`、`mm/kmsan/`、`mm/slub.c`、`mm/page_owner.c`、DRM 子系统

### 定位

**功能**：提供栈 trace（调用栈）的全局去重存储——相同的调用栈只存储一份，返回一个 32-bit 不透明 handle 供调用者持有。handle 中嵌入 5 bit 调用者自定义数据，无需额外内存即可附加元信息。

**实现方式**：内部是一个哈希表 + slab 分配器。`stack_depot_save` 对栈帧计算 Jenkins hash，在哈希表中查找是否已存在相同栈。若不存在，从 slab 页面中分配一个 `stack_record`（含柔性数组 `entries[]` 存储栈帧），插入哈希表。handle 编码为 `slabindex + offset + valid + 5 bit extra`——通过 handle 可直接定位到 slab 中的 `stack_record`，无需额外查找。栈 trace 一旦存入永不释放（append-only），用不可回收性换取去重带来的巨大空间节省——当数千个对象共享同一分配路径时，去重后的空间开销约为不去重的 1/100。

**应用场景**：内存调试基础设施的核心组件。KASAN（`mm/kasan/`）为每个堆分配记录分配栈和释放栈，用于 use-after-free 诊断。KMSAN（`mm/kmsan/`）记录未初始化内存的来源栈。SLUB 调试（`mm/slub.c`）记录对象分配栈。page_owner（`mm/page_owner.c`）追踪每个页面的分配者。DRM 子系统（7 个文件）用 stackdepot 追踪 GEM 对象的分配来源。

### 核心结构

```c
typedef u32 depot_stack_handle_t;  // 不透明 handle

struct stack_record {
    struct stack_record *next;     // 哈希链
    u32                  hash;     // Jenkins hash
    u32                  size;     // 帧数
    union handle_parts   handle;   // 自引用 handle
    unsigned long        entries[]; // 栈帧（柔性数组）
};
// handle 编码：slabindex + offset + valid + 5 bit extra
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `stack_depot_save(entries, nr_entries, gfp)` | 保存栈 trace | 返回 handle；自动去重 |
| `__stack_depot_save(entries, nr_entries, extra_bits, gfp, can_alloc)` | 完整版 | `can_alloc=false` 用于原子/NMI 上下文 |
| `stack_depot_fetch(handle, &entries)` | 取出栈帧 | 返回帧数；entries 指向内部存储（无拷贝） |
| `stack_depot_print(handle)` | 打印到内核日志 | |
| `stack_depot_snprint(handle, buf, size, spaces)` | 格式化到缓冲区 | |
| `stack_depot_get_extra_bits(handle)` | 提取 handle 中的 5 bit 额外数据 | 无需 fetch 完整栈 |
| `stack_depot_init()` | 初始化哈希表 | 延迟初始化，可重复调用 |
| `stack_depot_early_init()` | 早期初始化 | 在 `mm_init()` 中用 `alloc_large_system_hash` |

### 使用注意

- **不可释放**：存储的栈 trace 永远不会被回收。如果栈 trace 种类无限增长，内存会持续消耗
- **extra bits**：handle 中有 5 bit 供调用者存储元数据（如 KASAN 用来标记分配/释放栈）
- `__stack_depot_save` 的 `can_alloc` 参数：中断上下文或 NMI 中必须设为 false
- `stack_depot_fetch` 返回的指针指向 depot 内部内存——调用者不能释放或修改
- 初始化有两种路径：`early_init`（boot 阶段）和 `init`（slab 可用后）
