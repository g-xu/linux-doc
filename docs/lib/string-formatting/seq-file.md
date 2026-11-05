# seq_file — 顺序文件输出框架

> 内核虚拟文件系统（/proc、debugfs、sysfs 等）的标准文本输出框架。通过 start/next/stop/show 回调模型将"遍历数据"与"格式化输出"解耦，自动处理缓冲区溢出和 lseek。

---

## 定位

内核需要向用户态输出结构化文本（进程列表、设备状态、调试信息等）。直接实现 `read()` 方法需要手动处理缓冲区管理、偏移量跟踪、多次 read 调用等复杂逻辑。seq_file 框架将这些封装为四个回调函数，开发者只需关注"有哪些数据"和"怎么格式化"。

**核心文件**：
- 头文件：`include/linux/seq_file.h`
- 实现：`fs/seq_file.c`

## 核心数据结构

### struct seq_file（`include/linux/seq_file.h:17-30`）

```c
struct seq_file {
    char *buf;              // 输出缓冲区（kvmalloc 分配）
    size_t size;            // 缓冲区大小（初始 PAGE_SIZE，溢出时翻倍）
    size_t from;            // 已拷贝到用户态的偏移
    size_t count;           // 缓冲区中有效数据的末尾位置
    size_t pad_until;       // seq_pad() 对齐目标
    loff_t index;           // 当前迭代位置（传给 start/next 的 *pos）
    loff_t read_pos;        // 已读到的字节偏移（用于 lseek 校验）
    struct mutex lock;      // 序列化 read/lseek
    const struct seq_operations *op;  // 回调表
    const struct file *file;
    void *private;          // 用户数据（single_open 时传入的 data）
};
```

### struct seq_operations（`include/linux/seq_file.h:32-37`）

```c
struct seq_operations {
    void *(*start)(struct seq_file *m, loff_t *pos);
    void  (*stop)(struct seq_file *m, void *v);
    void *(*next)(struct seq_file *m, void *v, loff_t *pos);
    int   (*show)(struct seq_file *m, void *v);
};
```

**四个回调的调用时机**：

| 回调 | 何时调用 | 返回值语义 |
|------|---------|-----------|
| `start` | 迭代开始或 lseek 后重新定位 | 返回第一个元素指针 / NULL（空序列）/ ERR_PTR（错误） |
| `show` | 每个元素调用一次 | 0 = 成功；正数 = 跳过此元素（SEQ_SKIP）；负数 = 错误 |
| `next` | show 之后，取下一个元素 | 返回下一个元素指针 / NULL（迭代结束） |
| `stop` | 迭代结束时调用（包括错误和正常结束） | void |

**关键约定**：`next` 必须递增 `*pos`，否则 `seq_read_iter` 会打印 `buggy .next function` 警告并强制递增（`fs/seq_file.c:263-267`）。

## 缓冲区溢出与自动扩容

seq_file 的核心机制：**show 输出超出缓冲区时，自动翻倍缓冲区并从头重放整个序列**。

流程（`fs/seq_file.c:226-249`）：

```
1. 分配 PAGE_SIZE 缓冲区（首次 read 时）
2. 调用 start → show → 检查溢出
3. 如果溢出：stop → kvfree → size <<= 1 → 重新分配 → start 重来
4. 不溢出：继续 next → show 填充更多记录（直到再溢出或用户缓冲区满）
5. stop → copy_to_iter 拷贝到用户态
```

缓冲区分配使用 `kvmalloc(size, GFP_KERNEL_ACCOUNT)`（`fs/seq_file.c:33-39`），上限 `MAX_RW_COUNT`。

**溢出判定**：`m->count == m->size`（`seq_has_overflowed()`）。

## single_open — 一次性输出

不需要迭代序列时（只输出一段固定文本），用 `single_open` 代替手工实现四个回调。

`single_open`（`fs/seq_file.c:572-591`）自动填充：
- `start` → `single_start`：`*pos == 0` 时返回 `SEQ_START_TOKEN`，否则 NULL
- `next` → `single_next`：递增 pos 后返回 NULL（只迭代一次）
- `stop` → `single_stop`：空操作
- `show` → 用户提供的函数

```c
// 典型用法
static int my_show(struct seq_file *m, void *v)
{
    seq_printf(m, "value: %d\n", my_global_value);
    return 0;
}

static int my_open(struct inode *inode, struct file *file)
{
    return single_open(file, my_show, NULL);
}

static const struct file_operations my_fops = {
    .open    = my_open,
    .read    = seq_read,
    .llseek  = seq_lseek,
    .release = single_release,   // 注意：必须用 single_release 而非 seq_release
};
```

**注意**：`single_open` 内部分配一个 `struct seq_operations`（`kmalloc`），`single_release` 负责释放它。如果用 `seq_release` 会内存泄漏。

## 便捷宏

三个宏自动生成 `file_operations`（或 `proc_ops`），减少样板代码：

### DEFINE_SHOW_ATTRIBUTE（`include/linux/seq_file.h:196-208`）

配合 `single_open` 使用。只需定义 `xxx_show` 函数：

```c
static int status_show(struct seq_file *m, void *v)
{
    seq_printf(m, "hello\n");
    return 0;
}
DEFINE_SHOW_ATTRIBUTE(status);
// 展开为：status_open() + status_fops
```

### DEFINE_SEQ_ATTRIBUTE（`include/linux/seq_file.h:177-194`）

配合完整 `seq_operations` 使用。需要定义 `xxx_sops`：

```c
static const struct seq_operations items_sops = {
    .start = items_start,
    .next  = items_next,
    .stop  = items_stop,
    .show  = items_show,
};
DEFINE_SEQ_ATTRIBUTE(items);
// 展开为：items_open() + items_fops（open 中调用 seq_open + 传递 i_private）
```

### DEFINE_PROC_SHOW_ATTRIBUTE（`include/linux/seq_file.h:210-221`）

与 `DEFINE_SHOW_ATTRIBUTE` 类似，但生成 `proc_ops` 而非 `file_operations`，用于 `proc_create()` 注册：

```c
static int info_show(struct seq_file *m, void *v) { ... }
DEFINE_PROC_SHOW_ATTRIBUTE(info);
// 展开为：info_open() + info_proc_ops
```

## 输出函数

头文件：`include/linux/seq_file.h`；实现：`fs/seq_file.c`。

| API | 说明 | 备注 |
|-----|------|------|
| `seq_printf(m, fmt, ...)` | printf 风格格式化 | 溢出时标记状态，不立即报错 |
| `seq_vprintf(m, fmt, args)` | va_list 版本 | |
| `seq_puts(m, s)` | 输出字符串 | 比 seq_printf 快，无格式化开销 |
| `seq_putc(m, c)` | 输出单字符 | |
| `seq_write(m, data, len)` | 输出原始数据 | 成功返回 0，溢出返回 -1 |
| `seq_put_decimal_ull(m, delim, num)` | 输出无符号十进制 | 比 seq_printf("%llu") 快 |
| `seq_put_decimal_ll(m, delim, num)` | 输出有符号十进制 | |
| `seq_put_hex_ll(m, delim, v, width)` | 输出十六进制 | |
| `seq_put_decimal_ull_width(m, delim, num, width)` | 带最小宽度 | |
| `seq_escape(m, s, esc)` | 转义输出 | 将 esc 中字符替换为八进制转义 |
| `seq_escape_mem(m, src, len, flags, esc)` | 内存区域转义 | |
| `seq_path(m, path, esc)` | 输出路径名 | 内部调用 d_path + mangle_path |
| `seq_file_path(m, file, esc)` | 输出文件的 path | |
| `seq_dentry(m, dentry, esc)` | 输出 dentry 路径 | |
| `seq_hex_dump(m, prefix, type, rowsize, groupsize, buf, len, ascii)` | 十六进制转储 | 等价于 print_hex_dump 的 seq 版本 |
| `seq_pad(m, c)` | 填充空格到 seq_setwidth 设定的宽度 | 然后输出字符 c（若非零） |
| `seq_show_option(m, name, value)` | 输出挂载选项格式 | 自动转义 `,= \t\n\\` |

**溢出处理**：所有输出函数在缓冲区满时只标记溢出状态（`m->count = m->size`），不返回错误。调用者可用 `seq_has_overflowed(m)` 检查，但通常不需要——框架会自动扩容重试。

## 链表迭代辅助

框架提供了 `list_head` 和 `hlist_head` 的迭代辅助函数，简化 start/next 实现：

### list_head 辅助

| API | 说明 |
|-----|------|
| `seq_list_start(head, pos)` | 从 pos 位置开始遍历 |
| `seq_list_start_head(head, pos)` | pos=0 时返回 head 本身（用于输出表头行） |
| `seq_list_next(v, head, ppos)` | 返回下一个节点，到尾部返回 NULL |
| `seq_list_start_rcu` / `seq_list_next_rcu` | RCU 保护版本 |

### hlist_head 辅助

| API | 说明 |
|-----|------|
| `seq_hlist_start(head, pos)` | 从 pos 开始 |
| `seq_hlist_start_head(head, pos)` | pos=0 返回 `SEQ_START_TOKEN` |
| `seq_hlist_next(v, head, ppos)` | 下一个节点 |
| `seq_hlist_start_rcu` / `seq_hlist_next_rcu` | RCU 保护版本 |
| `seq_hlist_start_percpu(head, cpu, pos)` | 跨 per-CPU hlist 遍历 |
| `seq_hlist_next_percpu(v, head, cpu, pos)` | 下一个 per-CPU 节点 |

## seq_file 与 seq_buf 的区别

两者名字相似但定位不同：

| | seq_file | seq_buf |
|---|---------|---------|
| 头文件 | `include/linux/seq_file.h` | `include/linux/seq_buf.h` |
| 缓冲区 | 框架自动分配/扩容（kvmalloc） | 调用者提供固定缓冲区 |
| 回调模型 | 有（start/next/stop/show） | 无——直接写入 |
| 溢出处理 | 自动翻倍重试 | 标记溢出，调用者处理 |
| 用户态接口 | 完整（read/lseek/release） | 无——纯内核态缓冲区 |
| 典型用途 | /proc、debugfs 文件输出 | tracing 子系统内部格式化 |

详见 `misc-string.md` 中 seq_buf 一节。

## 使用规模

| 指标 | 数量 |
|------|------|
| `seq_printf` 调用 | 9976 处（1154 个文件） |
| `seq_puts` 调用 | 3290 处 |
| `single_open` 调用 | 261 处 |
| `DEFINE_SHOW_ATTRIBUTE` 使用 | 587 处 |
| `DEFINE_SEQ_ATTRIBUTE` 使用 | 27 处 |
| `seq_open` 直接调用 | 104 处 |

seq_file 是内核中使用最广泛的文本输出框架，几乎所有 `/proc` 和 debugfs 文件都通过它输出。

## §4 常见陷阱

### 陷阱 1：single_open 配 seq_release（内存泄漏）

**错误写法**：
```c
static const struct file_operations my_fops = {
    .open    = my_open,      // 内部调用 single_open()
    .read    = seq_read,
    .llseek  = seq_lseek,
    .release = seq_release,  // 错误！single_open 分配的 op 不会被释放
};
```

**正确写法**：
```c
static const struct file_operations my_fops = {
    .open    = my_open,      // 内部调用 single_open()
    .read    = seq_read,
    .llseek  = seq_lseek,
    .release = single_release,  // 正确：释放 single_open 分配的 seq_operations
};
```

**原因**：`single_open` 内部通过 `kmalloc` 分配一个 `struct seq_operations`（`fs/seq_file.c:575`），将其指针存入 `seq_file->op`。`single_release` 先取出这个指针，调用 `seq_release` 释放 `seq_file` 本身和缓冲区，然后 `kfree(op)` 释放这个动态分配的 `seq_operations`。如果用 `seq_release` 替代，只释放了 `seq_file` 和缓冲区，那个 `kmalloc` 的 `seq_operations` 就泄漏了。

**源码依据**：`fs/seq_file.c:572-590` — `single_open` 中 `kmalloc(sizeof(*op), GFP_KERNEL_ACCOUNT)`；`fs/seq_file.c:611-617` — `single_release` 中 `kfree(op)`。

### 陷阱 2：.next 回调忘记递增 *pos

**错误写法**：
```c
static void *my_next(struct seq_file *m, void *v, loff_t *pos)
{
    struct my_item *item = v;
    // 忘记递增 *pos
    return list_next_entry(item, list);  // *pos 没有变！
}
```

**正确写法**：
```c
static void *my_next(struct seq_file *m, void *v, loff_t *pos)
{
    struct my_item *item = v;
    (*pos)++;  // 必须递增 pos
    return list_next_entry(item, list);
}
```

**原因**：`seq_read_iter` 在 Fill 阶段检查 `next` 回调是否递增了 `pos`。如果 `pos` 没有变化，框架打印 `buggy .next function` 警告并强制递增（`fs/seq_file.c:263-267`）。如果不递增 `pos`，`seq_lseek` 无法正确定位到指定偏移——因为 `traverse` 函数依赖 `pos` 的累加来计算偏移量。使用 `seq_list_next` 等内置辅助函数可以避免这个问题，因为它们自动递增 `*ppos`。

**源码依据**：`fs/seq_file.c:262-267` — `if (pos == m->index) { pr_info_ratelimited("buggy .next function %ps did not update position index\n", m->op->next); m->index++; }`

### 陷阱 3：在 show 回调中检查 seq_has_overflowed 并返回错误

**错误写法**：
```c
static int my_show(struct seq_file *m, void *v)
{
    seq_printf(m, "data: %d\n", item->value);
    if (seq_has_overflowed(m))
        return -ENOMEM;  // 错误！不应该返回错误
    return 0;
}
```

**正确写法**：
```c
static int my_show(struct seq_file *m, void *v)
{
    // 直接输出，不需要检查溢出
    // 框架会自动扩容并重试整个序列
    seq_printf(m, "data: %d\n", item->value);
    return 0;
}
```

**原因**：seq_file 的溢出处理机制是：当 `show` 输出超出缓冲区时，框架自动翻倍缓冲区大小，然后从头重放整个序列（stop → kvfree → 重新分配 → start 重来）。`show` 返回负数表示"硬错误"，会终止整个迭代。返回 0 表示成功，即使溢出也只是触发框架的自动扩容。检查溢出并返回错误会错误地中止迭代，用户只能看到部分输出。

**源码依据**：`fs/seq_file.c:229-248` — `seq_read_iter` 中溢出检测后执行 `m->size <<= 1` 扩容并 `goto` 重新从头开始；`fs/seq_file.c:390-397` — `seq_vprintf` 中溢出只调用 `seq_set_overflow(m)` 不返回错误。

### 陷阱 4：从 seq_file 回调中访问可能已释放的数据

**错误写法**：
```c
static void *my_start(struct seq_file *m, loff_t *pos)
{
    // 直接返回链表节点指针，没有持锁
    struct my_item *item = list_first_entry(&global_list, ...);
    return item;
}

static void *my_next(struct seq_file *m, void *v, loff_t *pos)
{
    struct my_item *item = v;
    (*pos)++;
    return list_next_entry(item, list);  // 节点可能已被其他线程删除
}
```

**正确写法**：
```c
// 方案 A：在 start/stop 中持锁
static void *my_start(struct seq_file *m, loff_t *pos)
{
    spin_lock(&my_lock);
    return seq_list_start(&global_list, *pos);
}
static void my_stop(struct seq_file *m, void *v)
{
    spin_unlock(&my_lock);
}

// 方案 B：使用 RCU 保护版本
static void *my_start(struct seq_file *m, loff_t *pos)
{
    rcu_read_lock();
    return seq_list_start_rcu(&global_list, *pos);
}
static void my_stop(struct seq_file *m, void *v)
{
    rcu_read_unlock();
}
static void *my_next(struct seq_file *m, void *v, loff_t *pos)
{
    return seq_list_next_rcu(v, &global_list, pos);
}
```

**原因**：seq_file 的 `start` 到 `stop` 之间，框架不持有任何锁。如果底层数据在迭代期间被修改（其他线程删除/添加节点），`next` 回调可能解引用已释放的指针。`seq_file->lock`（mutex）只序列化 `read` 和 `lseek` 调用，不保护数据本身。开发者必须在 `start`/`stop` 中自行管理数据保护。

**源码依据**：`fs/seq_file.c:182-292` — `seq_read_iter` 中 `mutex_lock(&m->lock)` 只保护 read 操作的序列化，不保护数据；`include/linux/seq_file.h:25` — `struct mutex lock` 注释 "序列化 read/lseek"。

### 陷阱 5：在 seq_file 回调中使用 seq_printf 的返回值判断成功

**错误写法**：
```c
static int my_show(struct seq_file *m, void *v)
{
    int ret = seq_printf(m, "value: %d\n", item->value);
    if (ret < 0) {
        // seq_printf 不返回错误码！
        pr_err("seq_printf failed\n");
        return ret;
    }
    return 0;
}
```

**正确写法**：
```c
static int my_show(struct seq_file *m, void *v)
{
    // seq_printf 返回 void，不返回错误码
    seq_printf(m, "value: %d\n", item->value);
    // 不需要检查返回值
    // 如果需要检查溢出（通常不需要）：
    // if (seq_has_overflowed(m)) { ... }
    return 0;
}
```

**原因**：`seq_printf` 的返回类型是 `void`，不是 `int`。它在溢出时只设置 `m->count = m->size`（通过 `seq_set_overflow`），不返回错误码。试图检查其"返回值"在编译时就会报错。`seq_write` 是唯一返回错误码的输出函数（成功返回 0，溢出返回 -1），但通常也不需要检查——框架会自动处理溢出。

**源码依据**：`include/linux/seq_file.h:119` — `void seq_printf(struct seq_file *m, const char *fmt, ...)`；`fs/seq_file.c:401-408` — 实现为调用 `seq_vprintf`，无返回值。

## §6 取证摘要

**读取的文件**：
- `include/linux/seq_file.h` — seq_file 结构体、seq_operations 结构体、输出函数声明、DEFINE_*_ATTRIBUTE 宏
- `fs/seq_file.c` — seq_open、seq_read_iter、traverse、single_open、single_release、seq_printf/seq_vprintf、seq_list_*、seq_hlist_*

**关键论断出处**：
- "single_open 分配 seq_operations" — 来源：`fs/seq_file.c:575`（`kmalloc(sizeof(*op), GFP_KERNEL_ACCOUNT)`）
- "single_release 释放 seq_operations" — 来源：`fs/seq_file.c:611-617`（`kfree(op)`）
- "buggy .next function 警告" — 来源：`fs/seq_file.c:263-267`
- "溢出后自动翻倍缓冲区" — 来源：`fs/seq_file.c:242-248`（`m->size <<= 1`）
- "seq_printf 返回 void" — 来源：`include/linux/seq_file.h:119`
- "seq_buf_alloc 使用 kvmalloc" — 来源：`fs/seq_file.c:33-39`
- "缓冲区上限 MAX_RW_COUNT" — 来源：`fs/seq_file.c:35`

**未验证项**：
- `seq_open_private` 与 `__seq_open_private` 的使用差异（两者都存在，但具体使用场景未深入对比）
- `seq_file` 在 `pread` 场景下的行为（`fs/seq_file.c:83-85` 清除了 `FMODE_PWRITE`，但 `pread` 通过 `seq_read_iter` 支持）
