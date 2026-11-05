## 基础库 & 通用工具汇总
Linux 内核因为不链接标准 C 库（libc），所以它必须自己实现一套完整的**基础库（Infrastructure）**。这些设施通常位于内核源码的 `lib/` 目录下，或者在 `include/linux/` 中以头文件形式提供。

熟练掌握这些“轮子”，是区分“入门级驱动开发者”和“资深内核开发者”的分水岭。

我将它们分为**六大类**来详细列举：

---

### 一、 核心数据结构 (Containers & Data Structures)

这是内核中最常用的“积木”。

| 模块名称 | 头文件 | 关键结构/函数 | 描述与应用场景 |
| :--- | :--- | :--- | :--- |
| **Doubly Linked List**<br>(双向链表) | `&lt;linux/list.h&gt;` | `list_head`<br>`list_add`, `list_del`<br>`list_for_each_entry` | **内核使用率 No.1**。侵入式链表，用于将结构体串联起来。几乎所有子系统都在用。 |
| **Hash List**<br>(哈希链表) | `&lt;linux/list.h&gt;` | `hlist_head`, `hlist_node` | 只有 `next` 指针的双向链表变体。**为了节省内存**，常用于哈希桶（Hash Bucket）的冲突链。 |
| **Red-Black Tree**<br>(红黑树) | `&lt;linux/rbtree.h&gt;` | `rb_root`, `rb_node` | 自平衡二叉查找树。用于需要**快速排序和查找**的场景，如：CFS 进程调度、VMA 内存区域管理。 |
| **XArray**<br>(可扩展数组) | `&lt;linux/xarray.h&gt;` | `struct xarray`<br>`xa_store`, `xa_load` | **现代化的 Radix Tree**。用于将 `unsigned long` 索引映射到指针。自动处理扩容和并发锁。文件句柄 fd table 就用了类似技术。 |
| **Maple Tree**<br>(枫树) | `&lt;linux/maple_tree.h&gt;` | `maple_tree` | **最新引入 (v6.1+)**。专为 VMA (虚拟内存区域) 设计，比红黑树更适合范围查找，正在逐步取代 rbtree 管理 VMA。 |
| **Circular Buffer**<br>(环形缓冲区) | `&lt;linux/kfifo.h&gt;` | `struct kfifo` | 高效的字节流缓冲，**无锁**（单读单写）。常用于串口、USB 数据收发。 |
| **ID Allocator**<br>(ID 分配器) | `&lt;linux/idr.h&gt;`<br>`&lt;linux/ida.h&gt;` | `ida_alloc`, `ida_free` | 专门用来分配整数 ID（如给设备分配 minor number）。`IDA` 比 `IDR` 更轻量，只分配数字不存指针。 |

---

### 二、 同步与并发原语 (Synchronization & Concurrency)

内核是高度并发的，这些工具用于保护数据和协调执行顺序。

| 模块名称 | 头文件 | 关键结构/函数 | 描述与应用场景 |
| :--- | :--- | :--- | :--- |
| **Completion**<br>(完成量) | `&lt;linux/completion.h&gt;` | `struct completion`<br>`wait_for_completion` | **线程间同步**的神器。一个线程告诉另一个线程：“我做完了”。常用于驱动初始化等待硬件响应。 |
| **Wait Queue**<br>(等待队列) | `&lt;linux/wait.h&gt;` | `wait_queue_head_t`<br>`wait_event_interruptible` | **阻塞 IO 的基石**。条件不满足时休眠，满足时被唤醒。 |
| **Refcount**<br>(引用计数) | `&lt;linux/refcount.h&gt;` | `refcount_t`<br>`refcount_inc`, `refcount_dec` | **生命周期管理**。相比 `atomic_t`，它增加了饱和算术运算，防止溢出导致的安全漏洞（Use-After-Free）。 |
| **RCU**<br>(Read-Copy-Update) | `&lt;linux/rcupdate.h&gt;` | `rcu_read_lock`<br>`synchronize_rcu` | **Linux 内核的大杀器**。允许读操作**无锁、无阻塞**地并行执行。适用于“读多写少”的场景（如路由表、网络配置）。 |
| **Seqlock**<br>(顺序锁) | `&lt;linux/seqlock.h&gt;` | `seqlock_t` | 适用于写操作很少且写操作很短，读操作很多且需要获取一致性数据的场景（如系统时间 `jiffies` 的读取）。 |
| **Atomic Bitops**<br>(原子位操作) | `&lt;linux/bitops.h&gt;` | `set_bit`, `test_and_clear_bit` | 在不加锁的情况下原子地修改某个位。常用于状态标志（State Flags）。 |

---

### 三、 算法与数学工具 (Algorithms & Math)

内核不能用 C 标准库的数学函数，必须自己搞定。

| 模块名称 | 头文件 | 关键函数 | 描述与应用场景 |
| :--- | :--- | :--- | :--- |
| **Bitmap**<br>(位图) | `&lt;linux/bitmap.h&gt;` | `bitmap_alloc`<br>`bitmap_zero` | 处理**任意长度**的位数组。如果标志位超过了 64 个（一个 long 放不下），就得用这个。 |
| **Sort / Search**<br>(排序与查找) | `&lt;linux/sort.h&gt;`<br>`&lt;linux/bsearch.h&gt;` | `sort()`, `bsearch()` | 内核实现的快速排序（Heap Sort）和二分查找。 |
| **Division**<br>(除法) | `&lt;asm/div64.h&gt;` | `do_div(n, base)` | **特别注意**：在 32 位 CPU 上，64 位除法不能直接用 `/` 符号（会报 `__udivdi3` 链接错误），必须用 `do_div`。 |
| **Math Helpers** | `&lt;linux/math.h&gt;`<br>`&lt;linux/math64.h&gt;` | `abs`, `gcd` (最大公约数)<br>`lcm`, `int_sqrt` | 基础数学辅助。 |
| **CRC / Checksum** | `&lt;linux/crc32.h&gt;` 等 | `crc32`, `crc16` | 各种循环冗余校验，用于网络包、文件系统完整性检查。 |

---

### 四、 字符串与内存操作 (String & Memory Utils)

替代 libc 中的 `string.h` 和 `stdio.h`。

| 模块名称                  | 头文件                 | 关键函数                                      | 描述与应用场景                                                               |
| :-------------------- | :------------------ | :---------------------------------------- | :-------------------------------------------------------------------- |
| **String Lib**        | `&lt;linux/string.h&gt;`  | `memset`, `memcpy`<br>`strlen`, `strlcpy` | 标准字符串操作。注意用 `strscpy` 代替不安全的 `strcpy`。                                |
| **Kasprintf**         | `&lt;linux/sprintf.h&gt;` | `kasprintf`, `snprintf`                   | 格式化字符串。`kasprintf` 会自动分配内存来存放格式化后的结果，非常好用。                            |
| **Err Ptr**<br>(错误指针) | `&lt;linux/err.h&gt;`     | `ERR_PTR`, `PTR_ERR`<br>`IS_ERR`          | **内核黑魔法**。利用指针的高地址段（内核空间最后 4KB）来编码错误码。让函数既能返回指针，又能返回错误码（如 `-ENOMEM`）。 |
| **Mem Pool**<br>(内存池) | `&lt;linux/mempool.h&gt;` | `mempool_alloc`                           | 保证在内存极度紧缺时也能分配成功。主要用于块设备层（写盘操作不能因为没内存而失败，否则会死锁）。                      |

---

### 五、 时间与延时 (Time & Delays)

| 模块名称 | 头文件 | 关键函数 | 描述与应用场景 |
| :--- | :--- | :--- | :--- |
| **Ktime** | `&lt;linux/ktime.h&gt;` | `ktime_get`, `ktime_sub` | 纳秒级高精度时间类型。 |
| **Delay**<br>(忙等待) | `&lt;linux/delay.h&gt;` | `mdelay`, `udelay`, `ndelay` | **死等**（Busy Loop）。CPU 在空转。通常用于原子上下文或极短的硬件时序要求。 |
| **Sleep**<br>(睡眠) | `&lt;linux/delay.h&gt;` | `msleep`, `usleep_range` | **让出 CPU**。`usleep_range` 是微秒级延时的推荐做法（给定时器一个范围，利用合并中断来省电）。 |
| **Rate Limit**<br>(限速) | `&lt;linux/ratelimit.h&gt;` | `printk_ratelimited` | 防止日志打印太快刷屏导致系统卡死。 |

---

### 六、 其他重要设施 (Miscellaneous)

| 模块名称 | 头文件 | 关键结构 | 描述与应用场景 |
| :--- | :--- | :--- | :--- |
| **Notifier Chains**<br>(通知链) | `&lt;linux/notifier.h&gt;` | `atomic_notifier_head` | **发布-订阅模式**。例如：USB 子系统发现设备插入，通过通知链告诉所有感兴趣的模块。 |
| **LList**<br>(无锁链表) | `&lt;linux/llist.h&gt;` | `llist_head` | 单向无锁链表（Lock-less）。主要用于中断处理程序（NMI）向进程上下文传递数据。 |
| **Per-CPU Vars**<br>(每 CPU 变量) | `&lt;linux/percpu.h&gt;` | `DEFINE_PER_CPU` | 为每个 CPU 核心分配独立的变量副本。**消除锁竞争**的最佳手段之一。 |

### 总结

作为开发者，当你需要实现一个功能时，**第一反应应该是“内核里有没有现成的？”**

*   需要队列？用 `kfifo` 或 `sk_buff`。
*   需要查找？用 `xarray` 或 `rbtree`。
*   需要标志位？用 `bitmap`。
*   需要等硬件？用 `completion`。
*   需要函数返回指针又想带回错误码？用 `ERR_PTR`。

不要自己去写链表或哈希表，内核提供的版本经过了千锤百炼，不仅 Bug 少，而且性能通常是针对硬件缓存优化过的。