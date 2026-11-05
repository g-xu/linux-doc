# rhashtable — 可调整大小的并发哈希表

## §1 定位

rhashtable 是内核中**可自动调整大小、支持并发读写**的哈希表。它的核心设计目标是让读侧完全无锁（RCU 保护），写侧只锁单个桶——这意味着读者永远不会被写者阻塞，多个写者只要不在同一个桶上就能完全并行。

**与 `include/linux/hashtable.h`（简单哈希表）的根本区别：**

| 属性 | rhashtable | 简单 hashtable |
|------|-----------|---------------|
| 桶数量 | 动态调整（自动扩容/缩容） | 编译期固定（`DECLARE_HASHTABLE`） |
| 锁粒度 | 每桶 bit-spinlock | 用户自管（通常全局锁） |
| 读侧 | RCU 无锁 | 需要持锁 |
| 扩容 | 异步 workqueue，双表共存过渡 | 不支持 |
| 侵入式 | 是（嵌入 `struct rhash_head`） | 是（嵌入 `struct hlist_node`） |

**核心设计决策：**

1. **桶指针的 BIT(0) 用作锁**——`bucket_table.buckets[]` 的每个指针的最低位用作 bit-spinlock。`rht_lock` 设置 BIT(0)，`rht_unlock` 清除。NULLS 标记不直接存在桶中（桶空时存 NULL），避免了锁位与 NULLS 标记的冲突
2. **双表过渡的增量 resize**——扩容/缩容时创建 `future_tbl`，新插入进入新表，查找先查旧表再追到 `future_tbl`。通过 `work_struct` 异步迁移元素，不阻塞调用者
3. **NULLS 标记检测竞争**——链尾用 `NULLS_MARKER(bucket_addr)` 标记。如果遍历到链尾发现 NULLS 值不匹配当前桶地址，说明在遍历期间发生了 resize，需要重试。这是无锁并发哈希表的经典技巧
4. **75% 扩容 / 30% 缩容**——负载因子超过 75% 时触发异步扩容；低于 30% 且启用了 `automatic_shrinking` 时触发异步缩容。阈值不对称（迟滞）避免反复震荡
5. **两种变体**——`rhashtable`（唯一键，每个桶一条链）和 `rhltable`（允许重复键，匹配项形成二级链表 `rhlist_head`）

**定义位置：**

- 类型定义：`include/linux/rhashtable-types.h`（135 行）
- 内联实现：`include/linux/rhashtable.h`（1275 行）
- 核心实现：`lib/rhashtable.c`（1241 行）

**使用规模：** `struct rhashtable` 全树 205 处引用；`struct rhash_head` 176 处；`rhashtable_remove_fast` 147 处；`rhashtable_destroy` 138 处；`rhashtable_lookup_fast` 126 处；`rhashtable_init` 111 处；`rhashtable_insert_fast` 75 处。

## §2 核心 API

头文件：`include/linux/rhashtable.h` + `include/linux/rhashtable-types.h`；实现：`lib/rhashtable.c`。

### 2.1 数据结构

| 符号 | 说明 | 备注 |
|------|------|------|
| `struct rhashtable` | 哈希表句柄 | 含 `tbl`（当前桶表）、`p`（参数）、`nelems`（元素计数） |
| `struct rhltable` | 允许重复键的哈希表 | 内部包装 `struct rhashtable` |
| `struct rhash_head` | 嵌入用户结构的链节点 | 只含 `next` 指针（`__rcu`） |
| `struct rhlist_head` | 重复键链表的二级节点 | 含 `rhead`（主链）+ `next`（同键链表） |
| `struct rhashtable_params` | 构造参数 | 见 §2.2 |
| `struct rhashtable_iter` | 遍历迭代器 | — |
| `struct rhashtable_walker` | 遍历器内部注册结构 | 嵌入 `rhashtable_iter` |

### 2.2 构造参数

| 字段 | 说明 | 备注 |
|------|------|------|
| `key_len` | 键长度（字节） | — |
| `key_offset` | 键在用户结构中的偏移 | 与 `obj_hashfn` 二选一 |
| `head_offset` | `rhash_head` 在用户结构中的偏移 | 必须设置 |
| `nelem_hint` | 预期元素数提示 | 建议设为目标容量的 75% |
| `max_size` | 最大桶数 | 0 = 无上限 |
| `min_size` | 最小桶数 | — |
| `automatic_shrinking` | 启用自动缩容 | 默认不启用 |
| `hashfn` | 自定义哈希函数 | NULL = 使用 `jhash`/`jhash2` |
| `obj_hashfn` | 自定义对象哈希函数 | 接收完整对象指针 |
| `obj_cmpfn` | 自定义比较函数 | NULL = 使用 `memcmp` 比较 key |

### 2.3 初始化与销毁

| API | 说明 | 备注 |
|-----|------|------|
| `rhashtable_init(ht, params)` | 初始化哈希表 | 返回 0 或负错误码 |
| `rhltable_init(hlt, params)` | 初始化重复键哈希表 | — |
| `rhashtable_destroy(ht)` | 销毁哈希表 | 释放所有桶表；不释放用户对象 |
| `rhashtable_free_and_destroy(ht, free_fn, arg)` | 销毁并释放元素 | 对每个元素调用 `free_fn` |
| `rhltable_free_and_destroy(hlt, free_fn, arg)` | 重复键版本的销毁 | — |
| `rhltable_destroy(hlt)` | 销毁重复键表（不释放元素） | — |

### 2.4 查找

| API | 说明 | 备注 |
|-----|------|------|
| `rhashtable_lookup(ht, key, params)` | 查找 | 需在 RCU 读保护区内调用 |
| `rhashtable_lookup_fast(ht, key, params)` | 查找（自带 RCU 保护） | 内部加/解 `rcu_read_lock` |
| `rhltable_lookup(hlt, key, params)` | 重复键查找 | 返回 `rhlist_head *` 链表头 |

### 2.5 插入

| API | 说明 | 备注 |
|-----|------|------|
| `rhashtable_insert_fast(ht, obj, params)` | 插入 | 已存在返回 `-EEXIST`；可原子上下文调用 |
| `rhashtable_lookup_insert_fast(ht, obj, params)` | 查找并插入 | 需固定键（`key_len` 已设）；已存在返回 `-EEXIST` |
| `rhashtable_lookup_get_insert_fast(ht, obj, params)` | 查找并插入（返回旧值） | 成功返回 NULL；已存在返回旧对象指针 |
| `rhashtable_lookup_insert_key(ht, key, obj, params)` | 用显式键查找并插入 | 需 `obj_hashfn` |
| `rhashtable_lookup_get_insert_key(ht, key, obj, params)` | 同上，返回旧值 | — |
| `rhltable_insert(hlt, list, params)` | 重复键插入 | 允许同键多条目 |
| `rhltable_insert_key(hlt, key, list, params)` | 重复键插入（显式键） | — |

### 2.6 删除与替换

| API | 说明 | 备注 |
|-----|------|------|
| `rhashtable_remove_fast(ht, obj, params)` | 删除 | 返回 0 或 `-ENOENT` |
| `rhltable_remove(hlt, list, params)` | 重复键删除 | — |
| `rhashtable_replace_fast(ht, old, new, params)` | 原地替换 | 新旧对象哈希必须相同；返回 0 / `-ENOENT` / `-EINVAL` |

### 2.7 遍历（Walker API）

| API | 说明 | 备注 |
|-----|------|------|
| `rhashtable_walk_enter(ht, iter)` | 初始化遍历器 | 进程上下文 |
| `rhashtable_walk_exit(iter)` | 释放遍历器资源 | — |
| `rhashtable_walk_start(iter)` | 开始遍历（获取 RCU 锁） | 不检查返回值版 |
| `rhashtable_walk_start_check(iter)` | 开始遍历 | 返回 `-ENOENT` 表示表已空 |
| `rhashtable_walk_next(iter)` | 获取下一个元素 | 返回 `void *` 或 NULL |
| `rhashtable_walk_peek(iter)` | 查看当前元素（不推进） | — |
| `rhashtable_walk_stop(iter)` | 结束遍历（释放 RCU 锁） | — |
| `rhltable_walk_enter(hlt, iter)` | 重复键表的遍历器 | — |

### 2.8 桶链遍历宏

| API | 说明 | 备注 |
|-----|------|------|
| `rht_for_each(pos, tbl, hash)` | 遍历桶链 | 需持桶锁 |
| `rht_for_each_entry(tpos, pos, tbl, hash, member)` | 遍历桶链（带类型） | — |
| `rht_for_each_entry_safe(tpos, pos, next, tbl, hash, member)` | 安全遍历（可删除） | — |
| `rht_for_each_rcu(pos, tbl, hash)` | RCU 遍历桶链 | 需 `rcu_read_lock` |
| `rht_for_each_entry_rcu(tpos, pos, tbl, hash, member)` | RCU 遍历（带类型） | — |
| `rht_for_each_from(pos, head, tbl, hash)` | 从指定位置遍历 | — |
| `rht_for_each_entry_from(tpos, pos, head, tbl, hash, member)` | 从指定位置遍历（带类型） | — |
| `rht_for_each_rcu_from(pos, head, tbl, hash)` | RCU 从指定位置遍历 | — |
| `rht_for_each_entry_rcu_from(tpos, pos, head, tbl, hash, member)` | RCU 从指定位置（带类型） | — |
| `rhl_for_each_rcu(pos, list)` | 遍历重复键链表 | RCU 安全 |
| `rhl_for_each_entry_rcu(tpos, pos, list, member)` | 遍历重复键链表（带类型） | — |

### 2.9 辅助函数与宏

| API | 说明 | 备注 |
|-----|------|------|
| `rht_obj(ht, he)` | 从 `rhash_head` 恢复用户对象 | inline |
| `rht_bucket_index(tbl, hash)` | hash → 桶索引 | inline |
| `rht_is_a_nulls(ptr)` | 检查是否为链尾标记 | 低位为 1 |
| `rht_dereference(p, ht)` | 持锁时的 RCU 解引用 | 宏 |
| `rht_dereference_rcu(p, ht)` | RCU 读侧解引用 | 宏 |
| `rht_entry(tpos, pos, member)` | `container_of` 包装 | 宏 |

### 2.10 锁与扩容判断

| API | 说明 | 备注 |
|-----|------|------|
| `rht_grow_above_75(ht, tbl)` | 负载 > 75% | inline |
| `rht_shrink_below_30(ht, tbl)` | 负载 < 30% | inline |
| `rht_grow_above_100(ht, tbl)` | 负载 > 100% | inline |
| `rht_grow_above_max(ht, tbl)` | 达到最大元素数 | inline |
| `lockdep_rht_mutex_is_held(ht)` | 检查 mutex 持有 | 仅 `CONFIG_PROVE_LOCKING` |
| `lockdep_rht_bucket_is_held(tbl, hash)` | 检查桶锁持有 | 仅 `CONFIG_PROVE_LOCKING` |

**API 总计：** 约 50 个（含 14 个函数 + 16 个 inline + 11 个遍历宏 + 9 个辅助宏/函数）。

## §3 使用模式

### 模式 1：基本键值查找（网络子系统典型用法）

```c
struct my_entry {
    u32 key;
    struct rhash_head node;   /* 嵌入 rhash_head */
    /* ... 其他字段 ... */
};

static const struct rhashtable_params my_params = {
    .key_len    = sizeof(u32),
    .key_offset = offsetof(struct my_entry, key),
    .head_offset = offsetof(struct my_entry, node),
};

struct rhashtable my_ht;

/* 初始化 */
rhashtable_init(&my_ht, &my_params);

/* 插入 */
struct my_entry *e = kmalloc(sizeof(*e), GFP_KERNEL);
e->key = 42;
rhashtable_insert_fast(&my_ht, &e->node, my_params);

/* 查找（RCU 保护） */
rcu_read_lock();
struct my_entry *found = rhashtable_lookup(&my_ht, &key, my_params);
if (found)
    /* 注意：found 可能在 grace period 后被释放 */
    /* 需要在这里取引用计数 */
rcu_read_unlock();

/* 查找（自带 RCU 保护） */
struct my_entry *found = rhashtable_lookup_fast(&my_ht, &key, my_params);

/* 删除 */
rhashtable_remove_fast(&my_ht, &e->node, my_params);
kfree(e);

/* 销毁 */
rhashtable_destroy(&my_ht);
```

### 模式 2：重复键（rhltable）

```c
struct my_item {
    u32 id;
    struct rhlist_head list;  /* 注意：是 rhlist_head，不是 rhash_head */
    /* ... */
};

static const struct rhashtable_params item_params = {
    .key_len     = sizeof(u32),
    .key_offset  = offsetof(struct my_item, id),
    .head_offset = offsetof(struct my_item, list),
};

struct rhltable my_hl;
rhltable_init(&my_hl, &item_params);

/* 插入（允许同键多条目） */
struct my_item *a = kmalloc(sizeof(*a), GFP_KERNEL);
a->id = 42;
rhltable_insert(&my_hl, &a->list, item_params);

struct my_item *b = kmalloc(sizeof(*b), GFP_KERNEL);
b->id = 42;  /* 同键 */
rhltable_insert(&my_hl, &b->list, item_params);

/* 查找：返回匹配链的头 */
rcu_read_lock();
struct rhlist_head *head = rhltable_lookup(&my_hl, &key, item_params);
struct my_item *pos;
struct rhlist_head *cursor;
rhl_for_each_entry_rcu(pos, cursor, head, list) {
    /* 遍历所有 id==42 的条目 */
    process(pos);
}
rcu_read_unlock();
```

### 模式 3：全表遍历（Walker API）

```c
struct rhashtable_iter iter;
struct my_entry *entry;

rhashtable_walk_enter(&my_ht, &iter);
rhashtable_walk_start(&iter);

while ((entry = rhashtable_walk_next(&iter)) != NULL) {
    if (IS_ERR(entry)) {
        /* -EAGAIN 表示 resize 导致遍历中断，可选择重试 */
        if (PTR_ERR(entry) == -EAGAIN)
            continue;
        break;
    }
    process(entry);
}

rhashtable_walk_stop(&iter);
rhashtable_walk_exit(&iter);
```

关键点：遍历期间如果有 resize 发生，`rhashtable_walk_next` 可能返回 `-EAGAIN`。同一对象可能被看到两次，也可能漏掉——如果需要完全稳定的遍历，必须在外部加锁或复制。

### 模式 4：带自动缩容的长期运行表

```c
static const struct rhashtable_params params = {
    .key_len            = sizeof(u32),
    .key_offset         = offsetof(struct my_entry, key),
    .head_offset        = offsetof(struct my_entry, node),
    .automatic_shrinking = true,   /* 启用自动缩容 */
    .min_size           = 16,      /* 最小桶数 */
    .max_size           = 1024,    /* 最大桶数 */
};
```

当元素数降到桶数 30% 以下时，自动触发异步缩容。扩容阈值是 75%。两个阈值不对称，形成迟滞，避免在边界反复震荡。

## §4 陷阱

### 陷阱 1：rhashtable_lookup 必须在 RCU 读保护区内调用

```c
/* 错误：没有 RCU 保护 */
struct my_entry *e = rhashtable_lookup(&ht, &key, params);
/* e 可能在使用前就被释放了 */

/* 正确方案 A：手动 RCU 保护 */
rcu_read_lock();
struct my_entry *e = rhashtable_lookup(&ht, &key, params);
/* 在 rcu_read_unlock 前取引用计数 */
rcu_read_unlock();

/* 正确方案 B：用 _fast 变体 */
struct my_entry *e = rhashtable_lookup_fast(&ht, &key, params);
/* 内部有 rcu_read_lock/unlock，但返回后对象仍可能被释放 */
/* 仍然需要引用计数保护 */
```

源码依据：`include/linux/rhashtable.h:634`（`rhashtable_lookup` 注释："This must only be called under the RCU read lock"）。

### 陷阱 2：rhashtable_insert_fast 返回 -EEXIST 而非已有对象

```c
/* rhashtable_insert_fast 只返回 0 或错误码 */
int ret = rhashtable_insert_fast(&ht, &obj->node, params);
if (ret == -EEXIST) {
    /* 有冲突，但不知道是谁 */
    /* 需要再调一次 lookup 找到已有对象 */
}

/* 如果需要知道已有对象，用 lookup_get_insert 变体 */
void *old = rhashtable_lookup_get_insert_fast(&ht, &obj->node, params);
if (IS_ERR(old)) {
    /* 真正的错误 */
} else if (old != NULL) {
    /* old 是已有对象，插入未发生 */
} else {
    /* 插入成功 */
}
```

### 陷阱 3：遍历不保证稳定性

`rhashtable_walk_next` 在 resize 期间可能返回 `-EAGAIN`，也可能重复返回同一对象或跳过对象。如果需要完全稳定的遍历：
- 在遍历期间持有写锁（阻止 resize）
- 或者在外部数据结构中收集结果

### 陷阱 4：rhashtable_destroy 不释放用户对象

```c
/* 错误：直接销毁，泄漏内存 */
rhashtable_destroy(&ht);

/* 正确：先释放所有对象 */
rhashtable_free_and_destroy(&ht, my_free_fn, NULL);

void my_free_fn(void *ptr, void *arg)
{
    kfree(ptr);
}
```

### 陷阱 5：params 必须在整个表生命周期内保持一致

`rhashtable_params` 结构体通常在编译期定义为 `static const`。如果在运行时修改 params（比如改变 `key_len`），已存在的条目用旧参数哈希，新条目用新参数哈希——查找会失败。

### 陷阱 6：rhashtable_replace_fast 要求哈希不变

`rhashtable_replace_fast` 用旧对象的位置放入新对象。新旧对象的哈希值必须相同（通常意味着键相同）。如果不同，返回 `-EINVAL`。

源码依据：`include/linux/rhashtable.h:1152-1154`——`if (hash != rht_head_hashfn(...)) return -EINVAL`。

### 陷阱 7：桶锁是 bit-spinlock，禁用 softirq

`rht_lock` 内部调用 `local_bh_disable()` + `bit_spin_lock()`。这意味着持桶锁期间 softirq 被禁用。不要在桶锁内做耗时操作。

源码依据：`include/linux/rhashtable.h:326-332`——`rht_lock` 中 `local_bh_disable()` + `bit_spin_lock(0, ...)`。

## §5 代码示例

### 示例 1：网络连接的快速查找表（基于真实内核模式，伪代码）

```c
#include <linux/rhashtable.h>

struct connection {
    __be32 src_addr;
    __be32 dst_addr;
    __be16 src_port;
    __be16 dst_port;
    struct rhash_head node;
    refcount_t refcnt;
    /* ... */
};

static const struct rhashtable_params conn_params = {
    .key_len     = sizeof(__be32) * 2 + sizeof(__be16) * 2,
    .key_offset  = offsetof(struct connection, src_addr),
    .head_offset = offsetof(struct connection, node),
    .automatic_shrinking = true,
};

struct rhashtable conn_table;

int conn_table_init(void)
{
    return rhashtable_init(&conn_table, &conn_params);
}

void conn_table_destroy(void)
{
    rhashtable_destroy(&conn_table);
}

/* 查找连接（调用者负责引用计数） */
struct connection *conn_lookup(__be32 src, __be32 dst,
                               __be16 sport, __be16 dport)
{
    struct {
        __be32 src;
        __be32 dst;
        __be16 sport;
        __be16 dport;
    } key = { src, dst, sport, dport };

    return rhashtable_lookup_fast(&conn_table, &key, conn_params);
}

/* 添加连接 */
int conn_add(struct connection *conn)
{
    return rhashtable_insert_fast(&conn_table, &conn->node, conn_params);
}

/* 删除连接 */
void conn_del(struct connection *conn)
{
    rhashtable_remove_fast(&conn_table, &conn->node, conn_params);
}
```

## §6 取证

### 已读文件

| 文件 | 行数 | 读法 |
|------|------|------|
| `include/linux/rhashtable-types.h` | 135 | 全读 |
| `include/linux/rhashtable.h` | 1275 | 全读 |
| `lib/rhashtable.c` | 1241 | EXPORT_SYMBOL 表 + 关键函数签名 |

### 关键论断出处

| 论断 | 出处 | 验证命令 |
|------|------|----------|
| 75% 扩容阈值 | `include/linux/rhashtable.h:186` | `atomic_read(&ht->nelems) > (tbl->size / 4 * 3)` |
| 30% 缩容阈值 | `include/linux/rhashtable.h:199` | `atomic_read(&ht->nelems) < (tbl->size * 3 / 10)` |
| BIT(0) 锁机制 | `include/linux/rhashtable.h:326-332` | `bit_spin_lock(0, (unsigned long *)bkt)` |
| RHT_ELASTICITY = 16 | `include/linux/rhashtable.h:62` | `#define RHT_ELASTICITY 16u` |
| struct rhashtable 205 处 | grep 统计 | `grep -rw 'struct rhashtable\b' --include='*.c' --include='*.h' . \| wc -l` → 205 |
| rhashtable_lookup_fast 126 处 | grep 统计 | `grep -rw 'rhashtable_lookup_fast\b' --include='*.c' --include='*.h' . \| wc -l` → 126 |
| rhashtable_remove_fast 147 处 | grep 统计 | `grep -rw 'rhashtable_remove_fast\b' --include='*.c' --include='*.h' . \| wc -l` → 147 |
| rhashtable_insert_fast 75 处 | grep 统计 | `grep -rw 'rhashtable_insert_fast\b' --include='*.c' --include='*.h' . \| wc -l` → 75 |

### 未验证项

- `lib/rhashtable.c` 的增量 resize 实现（`rhashtable_insert_slow`、桶迁移逻辑）只看了符号表，未逐行核实
- `rhltable`（重复键变体）的二级链表操作细节未深入分析
- 嵌套桶表（`tbl->nest`）的使用场景和实现细节未验证
- walker 在 resize 期间的具体行为（`-EAGAIN` 的触发条件）未从源码逐行确认
