## notify chain
Linux 内核的 **Notifier Chains（通知链）** 是内核中一种核心的**发布-订阅（Publish-Subscribe）** 机制。它允许内核的不同子系统之间进行异步通信，而无需彼此强耦合。

简单来说，当内核中发生了某个事件（例如：USB插入、网络断开、系统重启、低电量），产生事件的子系统会“拉响警报”（调用通知链），所有对该事件感兴趣的模块（订阅者）都会收到通知并执行相应的回调函数。

下面我将从**基本概念、数据结构、四种类型、核心API、使用示例**五个方面详细介绍。

---

### 1. 为什么需要 Notifier Chains？

在复杂的单体内核中，子系统 A（如网络驱动）可能需要告诉子系统 B（如路由表）发生了什么，但 A 不应该直接调用 B 的函数，否则会导致：
1.  **代码耦合高**：A 必须包含 B 的头文件。
2.  **扩展性差**：如果有新模块 C 也想知道 A 的状态，A 必须修改代码。

**Notifier Chains 解决了这个问题**：
*   **发布者**（Event source）：维护一个链表头，不知道谁在监听，只负责广播。
*   **订阅者**（Listener）：向链表注册一个回调函数。
*   **解耦**：双方互不依赖。

---

### 2. 核心数据结构

所有通知链的基础都是 `struct notifier_block`。

```c
#include <linux/notifier.h>

struct notifier_block {
    int (*notifier_call)(struct notifier_block *nb, unsigned long action, void *data);
    struct notifier_block *next;
    int priority;
};
```

*   **`notifier_call`**：回调函数。
    *   `nb`: 指向该结构体本身的指针。
    *   `action`: 事件类型（整数），如 `NETDEV_UP`, `NETDEV_DOWN`。
    *   `data`: 附带的私有数据（通常是指针）。
    *   **返回值**：通常是 `NOTIFY_OK`, `NOTIFY_DONE`, 或 `NOTIFY_STOP`（停止后续通知）。
*   **`priority`**：优先级。数字越大，越先被调用。默认是 0。
*   **`next`**：指向链表中下一个节点的指针（由内核管理，用户无需手动操作）。

---

### 3. 四种通知链类型

根据**锁机制**和**执行上下文**的不同，Linux 提供了四种类型的通知链：

| 类型 | 定义宏/结构 | 锁机制 | 运行上下文 | 特点 |
| :--- | :--- | :--- | :--- | :--- |
| **Atomic** (原子) | `ATOMIC_NOTIFIER_HEAD` | Spinlock | 中断或原子上下文 | **不能睡眠/阻塞**。回调函数必须快速执行。 |
| **Blocking** (阻塞) | `BLOCKING_NOTIFIER_HEAD` | Rwsem (读写信号量) | 进程上下文 | **允许睡眠**。常用于无需低延迟的事件（如设备插拔）。 |
| **Raw** (原始) | `RAW_NOTIFIER_HEAD` | 无锁 (No Lock) | 调用者决定 | 极高性能，但并发保护由调用者全权负责。 |
| **SRCU** | `srcu_notifier_head` | SRCU Lock | 允许睡眠 | 用于读多写少、且要求极低延迟的场景。 |

---

### 4. 核心 API

每种类型的链都有对应的一套 API（注册、注销、调用）。

#### 4.1 注册 (Subscribe)
将回调函数加入链表。

*   `atomic_notifier_chain_register(head, nb)`
*   `blocking_notifier_chain_register(head, nb)`
*   `raw_notifier_chain_register(head, nb)`
*   `srcu_notifier_chain_register(head, nb)`

#### 4.2 注销 (Unsubscribe)
从链表中移除。

*   `atomic_notifier_chain_unregister(head, nb)`
*   ... (同上，前缀不同)

#### 4.3 发送通知 (Publish/Notify)
遍历链表并调用回调函数。

*   `atomic_notifier_call_chain(head, action, data)`
*   `blocking_notifier_call_chain(head, action, data)`
*   ...

---

### 5. 常见的内核内置通知链

Linux 内核中预定义了许多著名的通知链，驱动开发中经常会用到：

1.  **`netdev_chain`** (`net/core/dev.c`):
    *   网络设备状态变化（UP, DOWN, CHANGE, REGISTER）。
    *   API: `register_netdevice_notifier()`

2.  **`inetaddr_chain`** (`net/ipv4/devinet.c`):
    *   IP 地址变化（添加 IP、删除 IP）。
    *   API: `register_inetaddr_notifier()`

3.  **`reboot_notifier_list`** (`kernel/reboot.c`):
    *   系统重启或关机前通知。
    *   API: `register_reboot_notifier()`

4.  **`keyboard_notifier_list`** (`drivers/tty/vt/keyboard.c`):
    *   键盘按键事件（用于捕获特殊的组合键）。

---

### 6. 实战代码示例

下面编写一个独立的 Linux 模块，演示如何**自定义**一条 Blocking 通知链，并完成注册、触发和注销的全过程。

#### 模块代码 (`notifier_test.c`)

```c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/notifier.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Demo");
MODULE_DESCRIPTION("Blocking Notifier Chain Demo");

/* 
 * 1. 定义通知链头 (Publisher)
 * BLOCKING_NOTIFIER_HEAD 会定义并初始化一个名为 my_test_chain 的链表头
 */
static BLOCKING_NOTIFIER_HEAD(my_test_chain);

/* 定义自定义事件类型 */
#define EVENT_A  1
#define EVENT_B  2

/* 
 * 2. 定义回调函数 (Subscriber Logic)
 */
static int my_event_handler(struct notifier_block *nb, unsigned long action, void *data)
{
    int *val = (int *)data;
    
    pr_info("NOTIFIER_DEMO: Callback triggered! Action=%lu\n", action);

    switch (action) {
    case EVENT_A:
        pr_info("NOTIFIER_DEMO: Processing Event A. Data value: %d\n", *val);
        break;
    case EVENT_B:
        pr_info("NOTIFIER_DEMO: Processing Event B. Data value: %d\n", *val);
        break;
    default:
        pr_info("NOTIFIER_DEMO: Unknown Event\n");
        break;
    }

    return NOTIFY_OK; 
}

/* 
 * 3. 定义 notifier_block 结构体
 */
static struct notifier_block my_nb = {
    .notifier_call = my_event_handler,
    .priority = 10, /* 优先级 */
};

/*
 * 模拟触发事件的函数 (Publisher Logic)
 */
static void trigger_events(void)
{
    int test_data = 100;
    
    pr_info("NOTIFIER_DEMO: ---------------------------\n");
    pr_info("NOTIFIER_DEMO: Publishing Event A...\n");
    
    /* 调用通知链：通知所有订阅者 */
    blocking_notifier_call_chain(&my_test_chain, EVENT_A, &test_data);
    
    test_data = 200;
    pr_info("NOTIFIER_DEMO: Publishing Event B...\n");
    blocking_notifier_call_chain(&my_test_chain, EVENT_B, &test_data);
    pr_info("NOTIFIER_DEMO: ---------------------------\n");
}

static int __init my_notifier_init(void)
{
    pr_info("NOTIFIER_DEMO: Module loaded.\n");

    /* 4. 注册订阅者 */
    pr_info("NOTIFIER_DEMO: Registering notifier block...\n");
    if (blocking_notifier_chain_register(&my_test_chain, &my_nb)) {
        pr_err("NOTIFIER_DEMO: Failed to register notifier\n");
        return -1;
    }

    /* 5. 触发事件进行测试 */
    trigger_events();

    return 0;
}

static void __exit my_notifier_exit(void)
{
    /* 6. 注销订阅者 */
    pr_info("NOTIFIER_DEMO: Unregistering notifier block...\n");
    blocking_notifier_chain_unregister(&my_test_chain, &my_nb);
    pr_info("NOTIFIER_DEMO: Module unloaded.\n");
}

module_init(my_notifier_init);
module_exit(my_notifier_exit);
```

#### 运行结果预期
当加载模块时，dmesg 会显示：
```text
NOTIFIER_DEMO: Module loaded.
NOTIFIER_DEMO: Registering notifier block...
NOTIFIER_DEMO: ---------------------------
NOTIFIER_DEMO: Publishing Event A...
NOTIFIER_DEMO: Callback triggered! Action=1
NOTIFIER_DEMO: Processing Event A. Data value: 100
NOTIFIER_DEMO: Publishing Event B...
NOTIFIER_DEMO: Callback triggered! Action=2
NOTIFIER_DEMO: Processing Event B. Data value: 200
NOTIFIER_DEMO: ---------------------------
```

---

### 7. 高级特性与注意事项

1.  **返回值控制**：
    *   如果回调函数返回 `NOTIFY_STOP` (或者 `NOTIFY_STOP_MASK` 中的值)，通知链的遍历会**立即终止**，后续优先级较低的订阅者将不会收到通知。
    *   这常用于拦截机制（例如：某个安全模块拦截了 USB 挂载事件）。

2.  **上下文安全**：
    *   如果你在 **中断处理程序** 中调用通知链，**必须** 使用 `Atomic Notifier`。
    *   如果你的回调函数中需要 `msleep` 或分配大块内存（可能导致休眠），发布者必须使用 `Blocking Notifier`。

3.  **不要在回调中注销自己**：
    *   通常情况下，在 `notifier_call` 正在执行时，去调用 `unregister` 是不安全的（除非使用 SRCU 或者特定设计的锁逻辑），这可能导致死锁或链表损坏。

4.  **Priority 的作用**：
    *   如果你希望你的回调比别人的先执行（例如你想拦截某个网络包），可以将 `notifier_block.priority` 设置为一个较大的整数。

### 总结
Linux Notifier Chains 是内核模块间解耦通信的标准方式。
*   **发布者** 定义 `HEAD` 并调用 `call_chain`。
*   **订阅者** 定义 `notifier_block` 并调用 `chain_register`。
*   根据是否允许睡眠选择 `Atomic` 或 `Blocking` 类型。