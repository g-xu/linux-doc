# rbtree — 红黑树

头文件：`include/linux/rbtree.h`（核心 + 辅助函数）
增强版：`include/linux/rbtree_augmented.h`（augmented rbtree）
实现：`lib/rbtree.c`

---

## §1 定位

### 解决什么问题

内核需要一个数据结构，同时满足三个条件：
1. **按 key 有序**——能中序遍历得到排序序列
2. **O(log n) 的插入、删除、查找**——数据动态增减时不退化
3. **侵入式**——不额外分配内存，节点嵌入用户数据结构

list_head 只满足 3，不满足 1 和 2（O(n) 查找）。哈希表满足 2，不满足 1（无序）。排序数组满足 1，不满足 2（O(n) 插入/删除）。红黑树是**唯一同时满足三个条件的数据结构**。

### 核心设计思想：结构性操作 vs 比较性操作

rbtree 和 list_head 都是侵入式的，但有一个根本区别：

**list_head 的所有操作都是"结构性"的**——`list_add`、`list_del` 不需要知道数据内容，只操作指针。所以内核可以提供通用的链表操作函数。

**rbtree 的操作分为两类**：
- **结构性操作**（内核提供）：rebalance（`rb_insert_color` / `rb_erase`）、遍历（`rb_first` / `rb_next`）。这些只关心树的结构，不关心节点里存什么
- **比较性操作**（用户自己写）：查找插入位置、比较 key 大小。内核不知道你的 key 是 int、字符串还是结构体，无法提供通用实现

这就是 `rbtree.h` 文件开头注释说的："To use rbtrees you'll have to implement your own insert and search cores."

```
用户负责:                    内核负责:
┌─────────────┐           ┌──────────────────┐
│ while 循环   │           │ rb_insert_color() │
│ 比较 key     │──找到位置──│ rb_erase()        │
│ 找插入位置   │──插入后──→│ rb_first/rb_next  │
│ rb_link_node │           │ （rebalance）      │
└─────────────┘           └──────────────────┘
```

后来 `rb_add()` / `rb_find()` 等辅助函数把"用户负责"的部分也模板化了——只需要提供一个 `less` 回调。但底层仍然是这个分工。

### 与同组其他模块的本质区别

| 特性 | list_head | hlist | rbtree | xarray |
|------|-----------|-------|--------|--------|
| 有序性 | 插入序 | 无序 | **key 排序** | index 排序 |
| 查找 | O(n) | O(n) | **O(log n)** | O(log n) |
| 插入 | O(1) | O(1) | **O(log n)** | O(log n) |
| 删除 | O(1) | O(1) | **O(log n)** | O(log n) |
| 中序遍历 | 支持 | 只有后继 | **支持** | 支持 |
| 使用复杂度 | 低 | 低 | **高**（需写比较逻辑） | 中 |

选 rbtree 的场景：需要按 key 有序 + 动态增删 + O(log n) 保证。如果 key 是整数且范围不大，用 xarray 更快（常数因子小）；如果只是哈希桶，用 hlist 更简单。

### 没有它会怎样

内核里最关键的几个子系统会失去核心数据结构：
- **CFS 调度器**：用 rbtree 按虚拟运行时间排序任务，取最小值（下一个要运行的任务）。没有 rbtree 就没有 O(log n) 的调度决策
- **VMA（虚拟内存区域）**：按地址排序的内存区域树。`mmap`/`munmap` 依赖 O(log n) 查找和有序遍历
- **ext4  extent 树**：按文件偏移排序的块映射。没有它就没有 O(log n) 的块查找
- **定时器轮**：按过期时间排序

### 数据结构

```
        [8]
       /   \
     [4]   [12]
     / \    / \
   [2] [6][10][14]

   中序遍历: 2, 4, 6, 8, 10, 12, 14
   rb_first() → 2, rb_next(2) → 4, ...
```

每个节点是 `struct rb_node`（3 个字段 + 1 个打包了颜色和父指针的 `__rb_parent_color`）。`struct rb_root` 只有一个 `rb_node *` 指向根节点。

### 典型使用场景

- **有序集合**：按 key 排序的动态集合，支持查找、插入、删除
- **最小堆**：`rb_root_cached` + `rb_first_cached()` 实现 O(1) 取最小值（CFS 调度器用这个）
- **区间树**：augmented rbtree，每个节点维护子树最大值，支持区间重叠查询（VMA、 DRM）
- **映射表**：key → value 映射，替代哈希表但保持有序

---

## §2 核心 API

全表共 36 个公开 API：基础版 30 个（`rbtree.h`）+ 增强版 6 个（`rbtree_augmented.h`）。

### 2.1 初始化与状态查询

头文件：`include/linux/rbtree.h`。

| API | 说明 | 备注 |
|-----|------|------|
| `RB_ROOT` | 静态初始化空树 | `{ NULL }` |
| `RB_ROOT_CACHED` | 静态初始化空 cached 树 | `{ { NULL }, NULL }` |
| `RB_EMPTY_ROOT(root)` | 树是否为空 | `root->rb_node == NULL` |
| `RB_EMPTY_NODE(node)` | 节点是否未挂载 | `node->__rb_parent_color == (unsigned long)node` |
| `RB_CLEAR_NODE(node)` | 标记节点为未挂载 | `node->__rb_parent_color = (unsigned long)node` |
| `rb_parent(r)` | 获取父节点 | 从 `__rb_parent_color` 提取（清除低 2 位） |
| `rb_entry(ptr, type, member)` | 从 rb_node 获取容器 | 就是 `container_of()` |
| `rb_entry_safe(ptr, type, member)` | 安全版 | ptr 为 NULL 时返回 NULL |

### 2.2 插入（手动模式）

手动模式需要用户写查找插入位置的 while 循环。

| API | 说明 | 备注 |
|-----|------|------|
| `rb_link_node(node, parent, link)` | 把节点挂到 parent 的 left/right | `link` 是 `struct rb_node **`，指向 parent 的 `rb_left` 或 `rb_right` 字段 |
| `rb_link_node_rcu(node, parent, link)` | RCU 安全版 | 用 `rcu_assign_pointer` 写 link |
| `rb_insert_color(node, root)` | 插入后 rebalance | O(log n)，最多 2 次旋转 |

**手动插入流程**：
```
1. 从 root 开始，按 key 比较找到叶子位置，记录 parent 和 link
2. rb_link_node(&node->rb, parent, link)
3. rb_insert_color(&node->rb, root)
```

### 2.3 插入（辅助函数模式）

辅助函数把"找位置 → link → rebalance"三步封装成一个调用，用户只需提供比较回调。

| API | 说明 | 备注 |
|-----|------|------|
| `rb_add(node, tree, less)` | 插入节点 | `less` 签名：`bool (*)(struct rb_node *, const struct rb_node *)` |
| `rb_add_cached(node, tree, less)` | 插入到 cached tree | 返回新节点是否为最左（用于 O(1) 最小值维护） |
| `rb_find_add(node, tree, cmp)` | 查找或插入 | 找到相等 key 返回该节点，否则插入并返回 NULL |

### 2.4 查找

| API | 说明 | 备注 |
|-----|------|------|
| `rb_find(key, tree, cmp)` | 查找匹配的节点 | `cmp` 签名：`int (*)(const void *key, const struct rb_node *)`；返回任意一个匹配 |
| `rb_find_first(key, tree, cmp)` | 查找第一个（最左）匹配 | 处理重复 key——返回中序遍历中第一个匹配的 |
| `rb_next_match(key, node, cmp)` | 查找下一个匹配 | 配合 `rb_find_first` 遍历所有重复 key |
| `rb_for_each(node, key, tree, cmp)` | 遍历所有匹配的节点 | 宏，内部用 `rb_find_first` + `rb_next_match` |

### 2.5 删除

| API | 说明 | 备注 |
|-----|------|------|
| `rb_erase(node, root)` | 删除节点 | O(log n)，最多 3 次旋转 |

### 2.6 遍历

| API | 说明 | 备注 |
|-----|------|------|
| `rb_first(root)` | 最小节点 | O(log n)；从 root 一路向左 |
| `rb_last(root)` | 最大节点 | O(log n)；从 root 一路向右 |
| `rb_next(node)` | 中序后继 | amortized O(1) |
| `rb_prev(node)` | 中序前驱 | amortized O(1) |
| `rb_first_postorder(root)` | 后序遍历第一个 | 最深层最左节点 |
| `rb_next_postorder(node)` | 后序遍历下一个 | 用于释放整棵树 |
| `rbtree_postorder_for_each_entry_safe(pos, n, root, field)` | 后序安全遍历 | 允许释放当前节点的内存 |

**典型中序遍历**：
```c
struct rb_node *n;
for (n = rb_first(&tree); n; n = rb_next(n)) {
    struct my_data *data = rb_entry(n, struct my_data, rb);
    /* 处理 data */
}
```

### 2.7 替换

| API | 说明 | 备注 |
|-----|------|------|
| `rb_replace_node(victim, new, root)` | 用 new 替换 victim | **不 rebalance**；new 必须和 victim 有相同的 key 位置 |
| `rb_replace_node_rcu(victim, new, root)` | RCU 安全版 | 用 `rcu_assign_pointer` 更新父指针 |

### 2.8 缓存版（rb_root_cached）

`struct rb_root_cached` 在 `rb_root` 基础上加了一个 `rb_leftmost` 指针，把取最小值从 O(log n) 降到 O(1)。CFS 调度器用这个实现"找下一个要运行的任务"。

| API | 说明 | 备注 |
|-----|------|------|
| `rb_first_cached(root)` | O(1) 取最小节点 | 直接返回 `root->rb_leftmost`；是宏不是函数 |
| `rb_insert_color_cached(node, root, leftmost)` | cached 版 rebalance | `leftmost` 表示新节点是否是最左 |
| `rb_erase_cached(node, root)` | cached 版删除 | 自动维护 `rb_leftmost`；返回被删节点是否是最左 |
| `rb_replace_node_cached(victim, new, root)` | cached 版替换 | 同时更新 `rb_leftmost` |

### 2.9 增强版（augmented rbtree）

头文件：`include/linux/rbtree_augmented.h`。

增强版在每个节点上维护额外信息（如子树最大值），用于区间查询。

| API | 说明 | 备注 |
|-----|------|------|
| `rb_insert_augmented(node, root, callbacks)` | 增强版插入 | 回调更新 augment 数据 |
| `rb_erase_augmented(node, root, callbacks)` | 增强版删除 | 回调更新 augment 数据 |
| `rb_insert_augmented_cached(node, root, newleft, callbacks)` | 增强版 cached 插入 | 同时维护 leftmost 和 augment |
| `rb_erase_augmented_cached(node, root, callbacks)` | 增强版 cached 删除 | — |
| `RB_DECLARE_CALLBACKS(static, name, type, member, field, compute)` | 自动生成三个回调 | 适用于"子树 field 的聚合"模式 |
| `RB_DECLARE_CALLBACKS_MAX(static, name, type, member, field)` | 生成"子树最大值"回调 | `RB_DECLARE_CALLBACKS` 的特化版 |

**三个回调**（`struct rb_augment_callbacks`）：

| 回调 | 时机 | 用途 |
|------|------|------|
| `propagate(node, stop)` | 从插入/删除点向 root 传播 | 更新路径上每个节点的 augment 值 |
| `copy(old, new)` | 节点被替换时 | 把 old 的 augment 值复制到 new |
| `rotate(old, new)` | 旋转时 | 先复制 old→new，再重新计算 old |

---

## §3 使用模式

### 模式 1：辅助函数插入（推荐）

```c
#include <linux/rbtree.h>

struct task_info {
    int pid;
    struct rb_node rb;
};

static struct rb_root task_tree = RB_ROOT;

/* 比较回调：用于 rb_add */
static bool task_less(struct rb_node *a, const struct rb_node *b)
{
    return rb_entry(a, struct task_info, rb)->pid <
           rb_entry(b, struct task_info, rb)->pid;
}

/* 比较回调：用于 rb_find */
static int task_cmp(const void *key, const struct rb_node *b)
{
    int pid = *(const int *)key;
    int bpid = rb_entry(b, struct task_info, rb)->pid;
    return pid < bpid ? -1 : (pid > bpid ? 1 : 0);
}

/* 插入 */
void task_add(struct task_info *info)
{
    rb_add(&info->rb, &task_tree, task_less);
}

/* 查找 */
struct task_info *task_find(int pid)
{
    struct rb_node *n = rb_find(&pid, &task_tree, task_cmp);
    return n ? rb_entry(n, struct task_info, rb) : NULL;
}

/* 删除 */
void task_remove(struct task_info *info)
{
    rb_erase(&info->rb, &task_tree);
}
```

### 模式 2：cached tree 做最小堆

CFS 调度器的核心模式——O(1) 取最小值。

```c
static struct rb_root_cached min_heap = RB_ROOT_CACHED;

/* O(1) 取最小值 */
struct task_info *task_get_min(void)
{
    struct rb_node *n = rb_first_cached(&min_heap);
    return n ? rb_entry(n, struct task_info, rb) : NULL;
}

/* 弹出最小值 */
struct task_info *task_pop_min(void)
{
    struct rb_node *n = rb_first_cached(&min_heap);
    if (!n)
        return NULL;
    struct task_info *t = rb_entry(n, struct task_info, rb);
    rb_erase_cached(&t->rb, &min_heap);
    return t;
}

/* 插入——需要告知是否是最左 */
void task_insert(struct task_info *info)
{
    bool leftmost = rb_add_cached(&info->rb, &min_heap, task_less);
    /* leftmost 为 true 表示 info 成为新的最小值 */
}
```

### 模式 3：区间树（augmented rbtree）

```c
struct interval_node {
    unsigned long start;
    unsigned long last;      /* inclusive end */
    unsigned long __subtree_last;
    struct rb_node rb;
};

/* 计算子树最大值 */
static inline unsigned long compute_subtree_last(struct interval_node *n)
{
    unsigned long max = n->last;
    if (n->rb.rb_left) {
        unsigned long l = rb_entry(n->rb.rb_left, struct interval_node, rb)->__subtree_last;
        if (l > max) max = l;
    }
    if (n->rb.rb_right) {
        unsigned long r = rb_entry(n->rb.rb_right, struct interval_node, rb)->__subtree_last;
        if (r > max) max = r;
    }
    return max;
}

/* 自动生成 propagate/copy/rotate 三个回调 */
RB_DECLARE_CALLBACKS(static, interval_callbacks, struct interval_node,
                     rb, unsigned long, __subtree_last, compute_subtree_last)

/* 查找第一个与 [start, last] 重叠的区间 */
struct interval_node *interval_search(struct rb_root *root,
                                      unsigned long start, unsigned long last)
{
    struct rb_node *n = root->rb_node;
    while (n) {
        struct interval_node *node = rb_entry(n, struct interval_node, rb);
        if (node->rb.rb_left) {
            struct interval_node *left = rb_entry(node->rb.rb_left, struct interval_node, rb);
            if (left->__subtree_last >= start) {
                n = node->rb.rb_left;
                continue;
            }
        }
        if (node->start <= last && node->last >= start)
            return node;
        if (node->rb.rb_right) {
            struct interval_node *right = rb_entry(node->rb.rb_right, struct interval_node, rb);
            if (right->__subtree_last >= start) {
                n = node->rb.rb_right;
                continue;
            }
        }
        return NULL;
    }
    return NULL;
}
```

### 模式 4：后序遍历（释放整棵树）

```c
struct rb_node *n;
struct my_data *pos, *tmp;

rbtree_postorder_for_each_entry_safe(pos, tmp, &tree, rb) {
    kfree(pos);
}
/* 遍历结束后 tree 的所有节点内存已释放，但 tree 本身需要重新 RB_ROOT */
tree = RB_ROOT;
```

### 模式 5：手动插入（经典模式）

不使用辅助函数时的完整流程。

```c
int my_insert(struct rb_root *tree, struct my_data *data)
{
    struct rb_node **link = &tree->rb_node, *parent = NULL;

    /* 第一步：找插入位置 */
    while (*link) {
        struct my_data *cur = rb_entry(*link, struct my_data, rb);
        parent = *link;
        if (data->key < cur->key)
            link = &(*link)->rb_left;
        else if (data->key > cur->key)
            link = &(*link)->rb_right;
        else
            return -EEXIST;  /* 重复 key */
    }

    /* 第二步：挂载 */
    rb_link_node(&data->rb, parent, link);
    /* 第三步：rebalance */
    rb_insert_color(&data->rb, tree);
    return 0;
}
```

---

## §4 常见陷阱

### 陷阱 1：rb_first() 是 O(log n) 不是 O(1)

`rb_first` 从 root 一路向左走，是 O(log n)。如果需要频繁取最小值（如调度器每个 tick 都取），用 `rb_root_cached` + `rb_first_cached()`（O(1)）。

### 陷阱 2：rb_replace_node() 不 rebalance

`rb_replace_node` 只是把 `new` 放到 `victim` 的位置，复制颜色和指针。它**不检查**树是否仍然平衡。`new` 必须和 `victim` 在树中有完全相同的排序位置（相同 key），否则树的有序性被破坏。

### 陷阱 3：手动插入时 parent 和 link 必须同步更新

在查找插入位置的 while 循环中，每次移动 `link` 时必须同时更新 `parent = *link`。漏掉 `parent` 更新会导致 `rb_link_node` 把节点挂到错误的 parent 上，rebalance 时崩溃。

### 陷阱 4：rb_erase 后节点的指针被修改

`rb_erase` 会修改被删除节点的 `rb_left` / `rb_right` / `__rb_parent_color`（rebalance 过程中）。如果需要遍历被删除的节点（例如释放它），必须在 `rb_erase` 之前保存需要的信息。

### 陷阱 5：重复 key 的处理

`rb_find` 在遇到重复 key 时返回其中一个匹配节点（不保证是哪个）。如果需要找到所有重复 key，用 `rb_find_first` + `rb_next_match` 或 `rb_for_each` 宏。`rb_find_add` 在找到重复 key 时返回已存在的节点、**不插入**新节点。

### 陷阱 6：augmented 的 propagate 必须到 stop 为止

`propagate(node, stop)` 必须从 `node` 一路更新到 `stop`（通常是 root 或某个已正确的祖先）。提前停止会导致祖先节点的 augment 值过期。`RB_DECLARE_CALLBACKS` 宏生成的 `propagate` 在 augment 值不变时提前退出（优化），但逻辑上仍然遍历到 stop。

---

## §5 代码示例

### 示例 1：完整的有序集合（辅助函数模式）

```c
#include <linux/rbtree.h>
#include <linux/slab.h>

struct task_info {
    int pid;
    char name[16];
    struct rb_node rb;
};

static struct rb_root task_tree = RB_ROOT;

static bool task_less(struct rb_node *a, const struct rb_node *b)
{
    return rb_entry(a, struct task_info, rb)->pid <
           rb_entry(b, struct task_info, rb)->pid;
}

static int task_cmp(const void *key, const struct rb_node *b)
{
    int pid = *(const int *)key;
    int bpid = rb_entry(b, struct task_info, rb)->pid;
    return pid < bpid ? -1 : (pid > bpid ? 1 : 0);
}

struct task_info *task_find(int pid)
{
    struct rb_node *n = rb_find(&pid, &task_tree, task_cmp);
    return n ? rb_entry(n, struct task_info, rb) : NULL;
}

int task_add(struct task_info *info)
{
    rb_add(&info->rb, &task_tree, task_less);
    return 0;
}

void task_remove(struct task_info *info)
{
    rb_erase(&info->rb, &task_tree);
}

void task_dump(void)
{
    struct rb_node *n;
    for (n = rb_first(&task_tree); n; n = rb_next(n)) {
        struct task_info *t = rb_entry(n, struct task_info, rb);
        printk("pid=%d name=%s\n", t->pid, t->name);
    }
}
```

### 示例 2：cached tree 做最小堆

```c
static struct rb_root_cached min_heap = RB_ROOT_CACHED;

struct task_info *task_pop_min(void)
{
    struct rb_node *n = rb_first_cached(&min_heap);
    if (!n)
        return NULL;
    struct task_info *t = rb_entry(n, struct task_info, rb);
    rb_erase_cached(&t->rb, &min_heap);
    return t;
}
```

---

## §6 取证摘要

### API 计数

| 来源 | 宏 | extern 函数 | inline 函数 | 合计 |
|------|-----|-------------|-------------|------|
| `include/linux/rbtree.h` | 9 | 10 | 11 | 30 |
| `include/linux/rbtree_augmented.h` | 2 | 0 | 4 | 6 |
| **合计** | **11** | **10** | **15** | **36** |

### 关键论断出处

- `rb_insert_color` 最多 2 次旋转：`lib/rbtree.c` 中 `__rb_insert` 实现
- `rb_erase` 最多 3 次旋转：`lib/rbtree.c` 中 `____rb_erase_color` 实现
- `rb_first_cached` 是 O(1)：`include/linux/rbtree.h:106`，直接返回 `root->rb_leftmost`
- `RB_DECLARE_CALLBACKS` 自动生成三个回调：`include/linux/rbtree_augmented.h:74-100`
- 侵入式设计：`include/linux/rbtree.h:9-14` 文件头注释明确说明

### 与原版（rbtree.md）的差异

| 改进项 | 原版 | 新版 |
|--------|------|------|
| §1 核心洞察 | "最常用的有序容器"（泛泛） | "结构性操作 vs 比较性操作"的分工（设计本质） |
| §1 独特价值 | 对比表（与其他模块重复） | "唯一同时满足三个条件的数据结构" + 具体子系统依赖 |
| API 覆盖率 | ~25 个 | 36 个（含 `rb_next_match`、`RB_DECLARE_CALLBACKS_MAX` 等） |
| `rb_link_node_rcu` | 未列出 | 已收录 |
| `rb_entry_safe` | 未列出 | 已收录 |
| 辅助函数 | 6 个 | 6 个（相同，但加了 `rb_add_cached` 返回值说明） |

### 未验证项

- `rb_add` 全树使用量仍然较低（9 处），多数子系统仍用手动模式
- augmented rbtree 的具体消费者（interval_tree_generic.h）未逐一核实
- `RB_DECLARE_CALLBACKS_MAX` 的全树使用数未精确 grep
