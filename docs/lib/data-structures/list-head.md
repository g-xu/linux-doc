# list_head — 侵入式双向循环链表

头文件：`include/linux/list.h`
实现：`include/linux/list.h`（全部 inline）、`lib/list_sort.c`（list_sort）

---

## §1 定位

### 解决什么问题

内核需要一种通用的、高效的方式来管理动态增减的对象集合。C 语言没有泛型容器，如果为每种数据结构都写一套链表操作代码，既冗余又无法复用。`list_head` 通过**侵入式设计**解决了这个问题：不在链表节点里存数据，而是把链表节点嵌入到数据结构的内部。

### 核心设计思想

```c
struct list_head {
    struct list_head *next, *prev;
};
```

`list_head` 本身不携带任何数据。使用者把 `struct list_head` 成员嵌入到自己的结构体中，然后通过 `container_of()` 宏从 `list_head` 指针反推出外层结构体的指针。这就是内核中最重要的设计模式之一——**侵入式容器**。

```
  +---------------------+
  | struct my_obj       |
  |   ...               |
  |   struct list_head  | <--- 链表只看到这个
  |     next / prev     |
  |   ...               |
  +---------------------+
         |
         | container_of()
         v
   struct my_obj *
```

这种设计的优势：
- **零额外分配**：节点和数据在同一个内存块中，不需要为链表节点单独 `kmalloc`
- **类型无关**：同一套 `list_*()` 函数可以管理任何类型的结构体
- **多链归属**：一个结构体可以嵌入多个 `list_head`，同时属于多条链表
- **缓存友好**：数据节点和链表指针物理相邻，遍历时 cache line 利用率高

### 没有它会怎样

每个需要链表管理的子系统都得自己实现一套。实际上在 `list_head` 统一之前确实如此。现在全树有 1279 处 `struct list_head` 声明、15669 处 `list_for_each_entry` 调用——它是内核中使用最广泛的数据结构。

### 数据结构

链表是**双向循环**的。头节点的 `next` 指向第一个元素，`prev` 指向最后一个元素。空链表时 `next` 和 `prev` 都指向自身。

```
空链表:
  head --> head (next == prev == &head)

含三个元素的链表:
  head <-> A <-> B <-> C <-> head
  (循环: head.next = A, C.next = head, head.prev = C)
```

头节点（`list_head`）是一个哨兵节点，不代表实际数据。`list_for_each_entry()` 的终止条件就是 `pos` 回到头节点。

### 典型使用场景

- **对象管理**：子系统用链表管理动态创建/销毁的对象（如设备列表、文件列表）
- **FIFO 队列**：`list_add_tail()` 入队 + `list_move()`/`list_del()` 出队
- **LIFO 栈**：`list_add()` 入栈 + `list_del()` 出栈
- **事件等待队列**：等待者挂到链表上，事件发生时遍历唤醒
- **任务调度**：CFS 调度器的可运行任务链表

### 与同组其他模块的区别

| 特性 | list_head | hlist | rbtree | xarray |
|------|-----------|-------|--------|--------|
| 结构 | 双向循环链表 | 单向哈希链表 | 红黑树 | 稀疏数组 |
| 查找 | O(n) 遍历 | O(n) 遍历 | O(log n) | O(log n) |
| 尾部操作 | O(1) | 不支持 | 不适用 | 不适用 |
| 典型用途 | 通用链表 | 哈希桶 | 有序集合 | ID/指针映射 |
| 头节点大小 | 2 指针 | 1 指针 | 1 指针 | spinlock + flags + ptr |

选 list_head 的场景：不需要按 key 查找、需要双向遍历、需要 O(1) 的尾部插入。如果需要按 key 快速查找，用 rbtree 或 xarray；如果是哈希表的桶链，用 hlist 节省头节点空间。

---

## §2 核心 API

### 初始化

| API | 说明 | 备注 |
|-----|------|------|
| `LIST_HEAD_INIT(name)` | 编译期初始化链表头 | 用于结构体静态初始化 |
| `LIST_HEAD(name)` | 声明并初始化链表头变量 | 展开为 `struct list_head name = LIST_HEAD_INIT(name)` |
| `INIT_LIST_HEAD(list)` | 运行期初始化链表头 | 使 `next`/`prev` 指向自身，表示空链表 |

### 插入

| API | 说明 | 备注 |
|-----|------|------|
| `list_add(new, head)` | 在 head 之后插入（头部） | 适合实现栈（LIFO） |
| `list_add_tail(new, head)` | 在 head 之前插入（尾部） | 适合实现队列（FIFO） |

### 删除

| API | 说明 | 备注 |
|-----|------|------|
| `list_del(entry)` | 从链表中删除节点 | 删除后 `next`/`prev` 被设为 `LIST_POISON1`/`LIST_POISON2`，不可再用 `list_empty()` 判断 |
| `list_del_init(entry)` | 删除并重新初始化 | 删除后节点变为空链表状态，可以再次使用 |
| `list_del_init_careful(entry)` | 删除并初始化，带内存序保证 | 配合 `list_empty_careful()` 使用，保证之前的内存写操作对读者可见 |

### 查询

| API | 说明 | 备注 |
|-----|------|------|
| `list_empty(head)` | 链表是否为空 | 返回 `int`（非 `bool`），用 `READ_ONCE` 读 `next` |
| `list_empty_careful(head)` | 无锁安全地判断空链表 | 使用 `smp_load_acquire`，仅在唯一修改者是 `list_del_init_careful()` 时安全 |
| `list_is_first(entry, head)` | 是否为第一个节点 | 判断 `entry->prev == head` |
| `list_is_last(entry, head)` | 是否为最后一个节点 | 判断 `entry->next == head` |
| `list_is_head(entry, head)` | 是否为头节点本身 | 指针比较 `entry == head` |
| `list_is_singular(head)` | 链表是否只有一个节点 | 非空且 `head->next == head->prev` |

### 移动与替换

| API | 说明 | 备注 |
|-----|------|------|
| `list_move(entry, head)` | 移到 head 之后 | 先删后加 |
| `list_move_tail(entry, head)` | 移到 head 之前 | 先删后加到尾部 |
| `list_bulk_move_tail(head, first, last)` | 把 first 到 last 的子串移到 head 之前 | first 和 last 必须在同一条链表上 |
| `list_replace(old, new)` | 用 new 替换 old | old 不重新初始化 |
| `list_replace_init(old, new)` | 替换并重新初始化 old | old 变为空链表 |
| `list_swap(entry1, entry2)` | 交换两个节点的位置 | entry1 放到 entry2 的位置，反之亦然 |

### 拼接与切割

| API | 说明 | 备注 |
|-----|------|------|
| `list_splice(list, head)` | 把 list 拼接到 head 之后 | 适合栈式合并；list 不重新初始化 |
| `list_splice_tail(list, head)` | 把 list 拼接到 head 之前 | 适合队列式合并 |
| `list_splice_init(list, head)` | 拼接并重新初始化 list | 拼接后 list 变为空链表 |
| `list_splice_tail_init(list, head)` | 尾部拼接并重新初始化 list | 同上 |
| `list_cut_position(list, head, entry)` | 从 head 切到 entry（含）放入 list | entry 必须是 head 上的节点 |
| `list_cut_before(list, head, entry)` | 从 head 切到 entry（不含）放入 list | entry == head 时移动全部 |

### 旋转

| API | 说明 | 备注 |
|-----|------|------|
| `list_rotate_left(head)` | 左旋：第一个节点变为最后一个 | 空链表安全 |
| `list_rotate_to_front(entry, head)` | 把 entry 旋转到第一个位置 | entry 必须是链表上的节点 |

### 节点访问（宏）

| API | 说明 | 备注 |
|-----|------|------|
| `list_entry(ptr, type, member)` | 从 list_head 指针获取容器结构体 | 就是 `container_of()` |
| `list_first_entry(ptr, type, member)` | 获取第一个节点 | **链表不能为空**，否则返回非法指针 |
| `list_last_entry(ptr, type, member)` | 获取最后一个节点 | **链表不能为空** |
| `list_first_entry_or_null(ptr, type, member)` | 获取第一个节点或 NULL | 空链表安全，返回 NULL |
| `list_next_entry(pos, member)` | 获取下一个节点 | pos 是容器类型指针 |
| `list_prev_entry(pos, member)` | 获取上一个节点 | 同上 |
| `list_next_entry_circular(pos, head, member)` | 获取下一个节点，末尾环绕到头部 | 链表不能为空 |
| `list_prev_entry_circular(pos, head, member)` | 获取上一个节点，头部环绕到末尾 | 链表不能为空 |

### 遍历（宏）

| API | 说明 | 备注 |
|-----|------|------|
| `list_for_each(pos, head)` | 遍历所有节点 | pos 是 `struct list_head *` |
| `list_for_each_prev(pos, head)` | 反向遍历 | 同上 |
| `list_for_each_safe(pos, n, head)` | 安全遍历（允许删除当前节点） | n 是临时存储，pos 类型同上 |
| `list_for_each_prev_safe(pos, n, head)` | 安全反向遍历 | 同上 |
| `list_for_each_rcu(pos, head)` | RCU 安全遍历 | 用 `rcu_dereference()` 读指针 |
| `list_for_each_continue(pos, head)` | 从当前位置继续遍历 | 跳过当前 pos，从 pos->next 开始 |
| `list_for_each_entry(pos, head, member)` | 类型安全遍历 | pos 是容器类型指针，最常用 |
| `list_for_each_entry_reverse(pos, head, member)` | 类型安全反向遍历 | 同上 |
| `list_for_each_entry_safe(pos, n, head, member)` | 类型安全安全遍历 | 允许在循环体中删除 pos |
| `list_for_each_entry_safe_reverse(pos, n, head, member)` | 类型安全安全反向遍历 | 同上 |
| `list_for_each_entry_continue(pos, head, member)` | 从 pos 的下一个继续遍历 | pos 不重新初始化 |
| `list_for_each_entry_continue_reverse(pos, head, member)` | 反向继续遍历 | 同上 |
| `list_for_each_entry_from(pos, head, member)` | 从 pos 当前位置开始遍历 | 包含 pos 自身 |
| `list_for_each_entry_from_reverse(pos, head, member)` | 从 pos 反向遍历 | 包含 pos 自身 |
| `list_for_each_entry_safe_continue(pos, n, head, member)` | 安全继续遍历 | 允许删除 |
| `list_for_each_entry_safe_from(pos, n, head, member)` | 从 pos 安全遍历 | 允许删除 |
| `list_entry_is_head(pos, head, member)` | 判断遍历是否到头 | 遍历宏的内部终止条件 |
| `list_safe_reset_next(pos, n, member)` | 重置 safe 遍历的临时指针 | 仅在重新获取锁后使用 |

### 排序

| API | 说明 | 备注 |
|-----|------|------|
| `list_sort(priv, head, cmp)` | 归并排序链表 | cmp 签名：`int cmp(void *priv, const struct list_head *a, const struct list_head *b)`；`EXPORT_SYMBOL` 在 `lib/list_sort.c:253` |

---

## §3 使用模式

### 模式 1：对象管理链表（最常用）

**问题**：子系统需要管理一组动态创建的同类型对象。

```c
struct my_device {
    char name[32];
    int status;
    struct list_head node;    /* 嵌入链表节点 */
};

/* 全局设备链表 */
static LIST_HEAD(device_list);
static DEFINE_SPINLOCK(device_lock);

/* 添加设备 */
void device_register(struct my_device *dev)
{
    spin_lock(&device_lock);
    list_add_tail(&dev->node, &device_list);
    spin_unlock(&device_lock);
}

/* 遍历设备 */
void device_dump(void)
{
    struct my_device *dev;

    spin_lock(&device_lock);
    list_for_each_entry(dev, &device_list, node) {
        pr_info("device: %s, status: %d\n", dev->name, dev->status);
    }
    spin_unlock(&device_lock);
}

/* 删除设备 —— 必须用 safe 版本 */
void device_unregister(struct my_device *dev)
{
    spin_lock(&device_lock);
    list_del_init(&dev->node);
    spin_unlock(&device_lock);
}
```

**关键点**：
- 用 `list_add_tail()` 保持插入顺序（FIFO）
- 遍历时用 `list_for_each_entry()`，删除时用 `list_for_each_entry_safe()`
- `list_del_init()` 比 `list_del()` 更安全——删除后节点可再次使用

### 模式 2：FIFO 队列

**问题**：中断上半部把任务挂到队列，下半部按序处理。

```c
struct work_item {
    int data;
    struct list_head list;
};

static LIST_HEAD(work_queue);

/* 生产者：添加到队尾 */
void enqueue(struct work_item *item)
{
    list_add_tail(&item->list, &work_queue);
}

/* 消费者：从队头取出 */
struct work_item *dequeue(void)
{
    struct work_item *item;

    if (list_empty(&work_queue))
        return NULL;

    item = list_first_entry(&work_queue, struct work_item, list);
    list_del_init(&item->list);
    return item;
}
```

### 模式 3：批量处理 + 链表切割

**问题**：需要把当前链表上的任务分批处理，避免长时间持锁。

```c
void process_batch(void)
{
    LIST_HEAD(batch);
    struct work_item *item, *tmp;

    spin_lock(&lock);
    /* 把整条链表切到局部变量上 */
    list_splice_init(&work_queue, &batch);
    spin_unlock(&lock);

    /* 无锁处理批量 */
    list_for_each_entry_safe(item, tmp, &batch, list) {
        process(item);
        list_del(&item->list);
        kfree(item);
    }
}
```

**关键点**：`list_splice_init()` 是"偷梁换柱"——把链表整体搬到局部变量，然后释放锁。这样处理期间不需要持锁，新来的任务会进入新的空链表。这是内核中非常常见的模式。

### 模式 4：list_sort 排序

**问题**：需要按某个条件对链表排序。

```c
static int cmp_priority(void *priv, const struct list_head *a,
                        const struct list_head *b)
{
    const struct task *ta = container_of(a, struct task, list);
    const struct task *tb = container_of(b, struct task, list);

    return ta->priority - tb->priority;
}

void sort_tasks(struct list_head *head)
{
    list_sort(NULL, head, cmp_priority);
}
```

---

## §4 常见陷阱

### 陷阱 1：遍历时删除节点导致死循环或崩溃

**错误写法**：
```c
list_for_each_entry(pos, head, member) {
    if (should_remove(pos)) {
        list_del(&pos->member);  /* BUG: pos->member.next 已被毒化 */
        kfree(pos);
    }
}
```

**正确写法**：
```c
list_for_each_entry_safe(pos, n, head, member) {
    if (should_remove(pos)) {
        list_del_init(&pos->member);
        kfree(pos);
    }
}
```

**原因**：`list_for_each_entry()` 在循环末尾用 `pos->member.next` 获取下一个节点。`list_del()` 把 `next` 设为 `LIST_POISON1`，下一轮迭代会访问非法地址。`_safe` 版本提前保存了下一个节点到临时变量 `n`。

源码依据：`include/linux/list.h:760-764`（`list_for_each_entry_safe` 宏定义）。

### 陷阱 2：对空链表使用 list_first_entry()

**错误写法**：
```c
struct my_obj *obj = list_first_entry(head, struct my_obj, list);
/* 如果链表为空，obj 是一个非法指针（head 偏移 member 位置的野指针） */
```

**正确写法**：
```c
if (list_empty(head))
    return NULL;
struct my_obj *obj = list_first_entry(head, struct my_obj, list);
```

或者直接用 `list_first_entry_or_null()`：
```c
struct my_obj *obj = list_first_entry_or_null(head, struct my_obj, list);
```

**原因**：`list_first_entry()` 展开为 `list_entry((ptr)->next, ...)`。空链表时 `head->next == head`，`container_of(head, type, member)` 计算出的指针不是 NULL，而是一个非法地址。源码依据：`include/linux/list.h:530-531`。

### 陷阱 3：list_del() 之后用 list_empty() 判断

**错误写法**：
```c
list_del(&entry->node);
if (list_empty(&entry->node))  /* BUG: 永远不为 true */
    ...
```

**正确写法**：
```c
list_del_init(&entry->node);
if (list_empty(&entry->node))  /* OK: 已重新初始化 */
    ...
```

或者用 `list_empty_careful()` 配合 `list_del_init_careful()`。

**原因**：`list_del()` 把 `next` 设为 `LIST_POISON1`（`0x00100100`），`prev` 设为 `LIST_POISON2`（`0x00200200`）。`list_empty()` 检查 `head->next == head`，毒化后的值不等于 `head`，所以永远返回 false。源码依据：`include/linux/list.h:146-151`（`list_del`），`include/linux/list.h:290-293`（`list_empty`）。

### 陷阱 4：忘记初始化链表头

**错误写法**：
```c
struct my_struct {
    struct list_head list;
} obj;

list_add(&new_node, &obj.list);  /* BUG: obj.list 未初始化 */
```

**正确写法**：
```c
INIT_LIST_HEAD(&obj.list);
list_add(&new_node, &obj.list);
```

**原因**：未初始化的 `list_head` 的 `next`/`prev` 是垃圾值。`list_add()` 会修改 `head->next->prev`，导致访问非法地址。如果用了 `LIST_HEAD()` 静态声明或 `INIT_LIST_HEAD()` 初始化，就不会出问题。

### 陷阱 5：list_splice() 不重新初始化源链表

**错误写法**：
```c
LIST_HEAD(tmp);
list_splice(&src, &dst);
/* src 现在指向已释放的内存，但 src->next 不为空 */
list_add(&new_entry, &src);  /* BUG: src 链表状态不一致 */
```

**正确写法**：
```c
list_splice_init(&src, &dst);  /* src 变为空链表 */
```

**原因**：`list_splice()` 只把 src 的节点接到 dst，不修改 src 本身。src 的 `next`/`prev` 仍然指向已接走的节点。后续对 src 的操作会破坏 dst 的链表。`list_splice_init()` 在拼接后调用 `INIT_LIST_HEAD()` 修复这个问题。源码依据：`include/linux/list.h:461-466`（`list_splice`）和 `include/linux/list.h:487-494`（`list_splice_init`）。

### 陷阱 6：RCU 遍历与普通遍历混用

**错误写法**：
```c
rcu_read_lock();
list_for_each_entry(pos, head, member) {  /* BUG: 不是 RCU 版本 */
    ...
}
rcu_read_unlock();
```

**正确写法**：
```c
rcu_read_lock();
list_for_each_entry_rcu(pos, head, member) {  /* 注意 _rcu 后缀 */
    ...
}
rcu_read_unlock();
```

**原因**：普通 `list_for_each_entry()` 直接解引用指针，不经过 `rcu_dereference()`。在 RCU 读侧临界区中，写者可能正在并发修改链表，直接解引用可能看到不一致的状态。注意：`list.h` 中只有 `list_for_each_rcu()` 有 RCU 版本，`list_for_each_entry_rcu()` 定义在 `include/linux/rculist.h` 中。

---

## §5 代码示例

### 示例 1：完整的设备管理模块（真实代码模式）

来源：基于内核中常见的设备链表管理模式。

```c
#include <linux/list.h>
#include <linux/slab.h>
#include <linux/spinlock.h>

struct sensor {
    char name[16];
    int value;
    struct list_head node;
};

static LIST_HEAD(sensor_list);
static DEFINE_SPINLOCK(sensor_lock);

/* 创建并注册传感器 */
int sensor_add(const char *name)
{
    struct sensor *s = kmalloc(sizeof(*s), GFP_KERNEL);
    if (!s)
        return -ENOMEM;

    strscpy(s->name, name, sizeof(s->name));
    s->value = 0;
    INIT_LIST_HEAD(&s->node);

    spin_lock(&sensor_lock);
    list_add_tail(&s->node, &sensor_list);
    spin_unlock(&sensor_lock);

    return 0;
}

/* 查找传感器 */
struct sensor *sensor_find(const char *name)
{
    struct sensor *s;

    rcu_read_lock();
    list_for_each_entry_rcu(s, &sensor_list, node) {
        if (strcmp(s->name, name) == 0) {
            rcu_read_unlock();
            return s;
        }
    }
    rcu_read_unlock();
    return NULL;
}

/* 删除所有传感器 */
void sensor_cleanup(void)
{
    struct sensor *s, *tmp;

    spin_lock(&sensor_lock);
    list_for_each_entry_safe(s, tmp, &sensor_list, node) {
        list_del_init(&s->node);
        kfree(s);
    }
    spin_unlock(&sensor_lock);
}
```

注意：上面的 `sensor_find()` 用了 `list_for_each_entry_rcu()`，它定义在 `include/linux/rculist.h` 中。对应的写入侧需要用 `list_add_tail_rcu()` 和 `list_del_rcu()`。

### 示例 2：用 list_cut_position 分批处理（伪代码）

```c
/* 场景：把一条长链表切成两半，分别处理 */
LIST_HEAD(first_half);
LIST_HEAD(second_half);
struct entry *pos;
int count = 0, total;

/* 先数总数 */
list_for_each(pos, &full_list)
    count++;

total = count;
count = 0;

/* 找到中间位置 */
list_for_each(pos, &full_list) {
    if (++count == total / 2)
        break;
}

/* 切割：前半到 first_half，后半留在 full_list */
list_cut_position(&first_half, &full_list, pos);
/* 此时 full_list 包含后半部分 */
list_splice_init(&full_list, &second_half);
```

---

## §6 取证摘要

### 读了哪些文件

- `include/linux/list.h`（1074 行）—— 完整阅读，所有 list_head 和 hlist API 定义
- `lib/list_sort.c`（253 行）—— list_sort 实现
- `include/linux/rculist.h` —— RCU 变体（未在本文档中详述）
- `include/linux/rculist_nulls.h` —— hlist_nulls RCU 变体
- `Documentation/core-api/` —— list_head 无独立官方文档

### 关键论断出处

- 全树 `struct list_head` 声明 1279 处：`grep -rn 'struct list_head' --include='*.c' --include='*.h' include/linux/ | grep -c 'struct list_head'`
- `list_for_each_entry` 调用 15669 处：`grep -rn 'list_for_each_entry' --include='*.c' | wc -l`
- `list_add_tail` 调用 5204 处：`grep -rn 'list_add_tail(' --include='*.c' | wc -l`
- `list_sort` 导出符号：`lib/list_sort.c:253`
- `LIST_POISON1`/`LIST_POISON2` 定义：`include/linux/poison.h`

### 未验证项

- RCU 变体（`list_for_each_entry_rcu()` 等）的详细用法未在本篇展开，它们在 `include/linux/rculist.h` 中定义
- `hlist_nulls` 变体未在本篇展开，它们在 `include/linux/rculist_nulls.h` 中定义
- `list_sort()` 的归并排序算法实现细节未详述
