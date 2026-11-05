---
title: 数据结构
---

# 02 · clk 核心数据结构

> 本篇回答「**这些结构体分别是什么、谁指向谁**」。
> 流程（谁在什么时候改这些字段）在 03；结构体上挂的 API 在 01。

---

## §1.0 先分清四组关系

图画得再全，也补不上一个没被显式否掉的错误类比。所以本篇先花一节把四组最容易搞混的关系钉死。

### 1.0.1 第一组：`clk` / `clk_core` / `clk_hw` 不是同一个东西的三个别名

❌ **错误理解**：「`struct clk` 就是一个时钟」。

✅ **正确理解**：一个物理时钟在内核里同时有**三个**表示，各自服务一个方向：

| | `struct clk` | `struct clk_core` | `struct clk_hw` |
|---|---|---|---|
| 定义在 | `drivers/clk/clk.c:102-111` | `drivers/clk/clk.c:60-97` | `include/linux/clk-provider.h:324-328` |
| 头文件里可见吗 | ❌ 只有前向声明 | ❌ 只有前向声明（`clk-provider.h:38`） | ✅ 完整定义 |
| 谁持有它 | **每个消费者各持一份** | 框架自己（全局唯一） | **提供方驱动** |
| 数量关系 | 一个时钟 N 份（N = 消费者数） | 一个时钟 1 份 | 一个时钟 1 份 |
| 干什么用 | 记住"**这个消费者**的约束"：`min_rate` / `max_rate` / `exclusive_count` | 记住"**这个时钟**的真相"：树位置、引用计数、缓存值 | 提供方通过 `container_of` 找回自己的私有结构 |
| 生命周期 | `clk_get` 创建 / `clk_put` 销毁 | `clk_hw_register` 创建 / `kref` 归零销毁 | **提供方自己分配**（通常嵌在私有结构里），框架不管 |

**为什么"每个消费者一份 `clk`"不会退化成"大家共享一份"**：`clk_hw_create_clk`（`drivers/clk/clk.c:4041`）每次调用都 `kzalloc` 一个新的 `struct clk`，然后挂进 `core->clks` 这条链表（`drivers/clk/clk.c:90` 的 `struct hlist_head clks`）。所以：

```c
a = clk_get(dev, "core");
b = clk_get(dev, "core");
/* a != b —— 两个不同的指针，指向同一个 clk_core */
```

这也解释了 01 §1.5 那条规则：**判断"是不是同一个时钟"必须用 `clk_is_match`，不能用 `==`**。

**为什么消费者永远拿不到 `clk_core`**：`struct clk_core` 的定义整体在 `drivers/clk/clk.c:60-97`，**不在任何头文件里**。头文件里只有 `clk-provider.h:38` 的一行 `struct clk_core;` 前向声明。所以消费者代码就算拿到 `clk->core` 这个指针也**编不过**——它不知道这个结构有哪些字段。这是编译器强制执行的分层，不是约定。

### 1.0.2 第二组：`clk_hw` + `clk_ops` **不是** device + driver

❌ **错误理解**：「`clk_hw` 是 device，`clk_ops` 是 driver，跟 i2c 的 adapter/algorithm 一样，也跟 platform 的 device/driver 一样」。

这个类比在 i2c 上成立（`i2c_adapter` 是 device、`i2c_algorithm` 是操作集），在 clk 上**不成立**。逐条对比：

| 维度 | platform 的 device ↔ driver | clk 的 `clk_hw` ↔ `clk_ops` |
|---|---|---|
| 有没有 `bus_type` | 有（`platform_bus_type`） | ❌ **没有**。全树 `rg 'struct bus_type' drivers/clk/ include/linux/clk*` 无输出（00 §1.3 记了这条验证） |
| 谁匹配谁 | 总线的 `.match` 回调在运行时配对 | ❌ 不匹配。`clk_ops` 的指针由提供方在 `clk_init_data.ops` 里**写死**（`include/linux/clk-provider.h:299`），注册时直接抄进 `core->ops`（`drivers/clk/clk.c:62`） |
| 会不会延迟绑定 | 会（device 先注册，driver 后加载也能绑上） | ❌ 不会。ops 在注册那一刻就定了，之后不变 |
| 是不是 `struct device` | `platform_device` 内嵌 `struct device` | ❌ `clk_hw` 里只有三个指针（`clk-provider.h:324-328`），没有 `struct device` |
| 出现在 `/sys/bus/` 下吗 | 是 | ❌ 不出现。时钟节点只在 `/sys/kernel/debug/clk/` 下 |
| 能不能有多个候选实现 | 能（多个 driver 竞争一个 device） | ❌ 一对一 |

✅ **正确理解**：`clk_hw` 是**提供方在框架里的锚点**——它唯一的作用是让 `container_of` 能从框架给的 `clk_hw *` 找回提供方自己的结构。`clk_ops` 是这个锚点携带的函数表。二者的关系更像「C++ 对象 + 虚函数表」，而不是「设备 + 驱动」。

**那时钟控制器和 device/driver 模型是什么关系？** 有关系，但在**上一层**：时钟控制器本身是一个 **platform device**（DT 里的 `cru` 节点，见 00 §2.8），它的 `platform_driver` 在 probe 里注册几十上百个 `clk_hw`。所以形状是：

```
platform_bus_type
      │
      └─ platform_device (cru@ff760000)  ←→  platform_driver (rockchip_clk_driver)
                                                     │ probe()
                                                     ↓
                                        clk_hw_register() × N   ←── 这一层跟总线无关
                                                     ↓
                                              clk_core 树（框架私有）
```

**一条 DT 节点 → 一个 platform device → N 个时钟节点**。时钟节点不是 device，也不在任何总线上。

### 1.0.3 第三组：prepare / enable 的分界线**不是**"慢 / 快"

❌ **错误理解**：「prepare 是慢操作，enable 是快操作，所以把耗时的放 prepare」。

✅ **正确理解**：分界线是「**能不能睡眠**」，快慢只是副产品。`clk_ops` 的 kerneldoc 说得很直接（`include/linux/clk-provider.h:221-230`）：*"If enabling a clock requires code that might sleep, this must be done in clk_prepare."*

| | prepare / unprepare | enable / disable |
|---|---|---|
| 锁 | `prepare_lock`，是 **mutex** | `enable_lock`，是 **spinlock**（带 irqsave） |
| 能睡眠吗 | ✅ 能 | ❌ 绝对不能 |
| 能在中断上下文调吗 | ❌ 不能 | ✅ 能 |
| 典型实现 | 等 PLL 锁定、跑 I²C 事务去配外部时钟芯片、`regulator_enable` | 写一个寄存器位 |
| 计数字段 | `core->prepare_count`（`drivers/clk/clk.c:81`） | `core->enable_count`（`drivers/clk/clk.c:80`） |
| 顺序约束 | 必须先 prepare | 未 prepare 就 enable → **`-ESHUTDOWN`**（`drivers/clk/clk.c:1188-1190`） |

**"慢 vs 快"这个说法为什么会误导你**：一个写单个寄存器位的门控**完全可以**只实现 `.prepare` 而不实现 `.enable`——很多驱动就这么写。这时候操作既在 prepare 里、又很快。反过来，一个耗时几百微秒的忙等（`readl_poll_timeout_atomic`）可以合法地待在 `.enable` 里，因为它不睡眠。判据始终只有一个：**这段代码里有没有可能调度出去**。

**一个直接的推论**：`clk_is_enabled_when_prepared()` 返回 true 的节点（没有 `.enable`/`.disable`，`drivers/clk/clk.c:1359-1362`）上，`clk_enable` 是彻底的空操作——真正的开关全在 prepare 里。PM 代码要靠这个判断"想真关掉必须 unprepare"。

### 1.0.4 第四组：时钟树 ≠ DT 的节点层级

❌ **错误理解**：「DT 里 `cru` 是父节点、外设是子节点，所以时钟树就是 DT 树」。

✅ **正确理解**：两棵树完全无关。

| | DT 节点层级 | clk 树 |
|---|---|---|
| 父子由什么决定 | DTS 文件里的花括号嵌套 | `clk_core->parent`（`drivers/clk/clk.c:68`），由**信号走向**决定：谁的输出是谁的输入 |
| 根是什么 | `/`（根节点） | `clk_root_list`（`drivers/clk/clk.c:36`）上的所有节点——通常是晶振和外部时钟输入 |
| 跨节点吗 | 不跨 | **跨**。一个 SoC 的时钟树可以横穿 `cru`、I²C 时钟芯片、GPIO 时钟三个完全不相邻的 DT 节点 |
| 怎么看 | `dtc -I fs /proc/device-tree` | `cat /sys/kernel/debug/clk/clk_summary`（`drivers/clk/clk.c:3707` 一带注册） |

**而且 clk 树上有第三种状态**：除了 root 和 normal，还有 **orphan**（`clk_orphan_list`，`drivers/clk/clk.c:37`；`core->orphan` 标志在 `:78`）——已注册但父节点还没注册。孤儿节点的 `rate` 是 0，`clk_hw_get_parent` 返回 NULL。这在多控制器 SoC 上是**正常的中间状态**（`cru` 先 probe、外部时钟芯片后 probe），父节点一注册核心就自动把孤儿接过去。查看：`cat /sys/kernel/debug/clk/clk_orphan_summary`。

---

## §2 核心抽象与消费者的关系图

clk 是框架型子系统（无 `bus_type`），所以这里画的不是"两条总线怎么缝在一起"，而是"三方（提供方 / 框架 / 消费者）各持有什么"。

```
        ┌──────────────── 提供方驱动（drivers/clk/rockchip/*.c 等） ────────────────┐
        │                                                                          │
        │  struct rockchip_clk_branch  ←── 厂商自己的描述表（框架不认识）          │
        │            ↓ probe 时展开                                                │
        │  struct clk_gate {                     ← 八个基本类型之一               │
        │      struct clk_hw hw;   ←────────────────────┐  容器的第一个成员        │
        │      void __iomem *reg;  ← 硬件细节            │  （container_of 的锚）  │
        │      u8 bit_idx;                               │                         │
        │      spinlock_t *lock;   ← 调用者提供           │                         │
        │  }                                             │                         │
        │            ↓ clk_hw_register(dev, &g->hw)      │                         │
        └────────────────────────────────────────────────┼─────────────────────────┘
                                                         │
    ════════════════════════ 框架边界 ═══════════════════╪══════════════════════════
                                                         │
        ┌──────────────── 框架内部（drivers/clk/clk.c，全部私有） ─────────────────┐
        │                                                 │                        │
        │   struct clk_core {                             │                        │
        │       const char *name;         ← 全局唯一       │                        │
        │       const struct clk_ops *ops; ─── 抄自 init->ops                      │
        │       struct clk_hw *hw;  ───────────────────────┘  双向指针              │
        │       struct clk_core *parent;      ┐                                    │
        │       struct clk_parent_map *parents;│ 树拓扑                            │
        │       struct hlist_head children;   │                                    │
        │       struct hlist_node child_node; ┘                                    │
        │       unsigned long rate, req_rate; ← 缓存，不是读回来的                  │
        │       unsigned int enable_count;    ┐                                    │
        │       unsigned int prepare_count;   │ 三个引用计数                        │
        │       unsigned int protect_count;   ┘                                    │
        │       struct hlist_head clks;   ───────┐  所有消费者句柄挂在这里          │
        │       struct kref ref;                 │                                 │
        │   }                                    │                                 │
        │                                        │                                 │
        │   clk_root_list ──┬─ clk_core ─── children ─── clk_core ─── ...          │
        │                   └─ clk_core                  │                         │
        │   clk_orphan_list ─── clk_core（父还没来）      │                         │
        └────────────────────────────────────────────────┼─────────────────────────┘
                                                         │
    ════════════════════════ 框架边界 ═══════════════════╪══════════════════════════
                                                         │
        ┌──────────────── 消费者驱动（drivers/spi、drivers/i2c、…） ───────────────┐
        │                                        │                                 │
        │   struct clk {  ←── clk_get 每次调用新建一个，挂进 core->clks             │
        │       struct clk_core *core;  ─────────┘                                 │
        │       struct device *dev;                                                │
        │       const char *dev_id, *con_id;   ← 只为报错信息好看                   │
        │       unsigned long min_rate, max_rate; ← per-consumer 约束            │
        │       unsigned int exclusive_count;     ← per-consumer 独占计数        │
        │       struct hlist_node clks_node;                                       │
        │   }                                                                      │
        │                                                                          │
        │   消费者只能通过 include/linux/clk.h 的函数碰它。                        │
        │   对 core 的字段：编译期不可见。                                         │
        └──────────────────────────────────────────────────────────────────────────┘
```

**这张图里最值得记住的一条**：`min_rate`/`max_rate` **在 `clk` 和 `clk_core` 里各有一份**（`drivers/clk/clk.c:107-108` 和 `:83-84`）。`clk` 里的是"这个消费者的要求"，`clk_core` 里的是"提供方声明的硬件能力"。`clk_set_rate` 时核心把**所有消费者**的区间和硬件区间求交（`clk_core_check_boundaries`），交集为空就 `-EINVAL`。这就是为什么 01 §1.4 说区间约束是 per-consumer 的。

---

## §3 结构体逐个

### 3.1 框架私有（消费者和提供方都看不到定义）

| 结构 | 位置 | 关键字段 | 说明 |
|---|---|---|---|
| `struct clk_core` | `drivers/clk/clk.c:60-97` | 见上图 | **框架的中心**。37 个字段，分五组：身份（name/ops/hw/owner/dev/of_node）、拓扑（parent/parents/num_parents/children/child_node/orphan）、频率变更中间态（new_rate/new_parent/new_child/new_parent_index）、计数与缓存（三个 count、rate/req_rate/min/max/accuracy/phase/duty）、基础设施（clks/notifier_count/kref/rpm_node/rpm_enabled + debugfs 两字段） |
| `struct clk` | `drivers/clk/clk.c:102-111` | `core` / `min_rate` / `max_rate` / `exclusive_count` | **per-consumer 句柄**。只有 8 个字段，其中 3 个是纯记账（`dev`/`dev_id`/`con_id` 只用于报错信息） |
| `struct clk_parent_map` | `drivers/clk/clk.c:52-58` | `hw` / `core` / `fw_name` / `name` / `index` | **延迟解析的关键**。注册时父节点可能还不存在，于是核心把"怎么找到父"的四种线索（hw 指针 / DT 的 fw_name / 全局名字 / DT 索引）都存下来，等到真要用时才解析成 `core` 指针。这是孤儿节点能自动脱孤的机制 |

`clk_core` 里 `new_rate` / `new_parent` / `new_child` / `new_parent_index` 这四个字段**只在一次 `clk_set_rate` 的执行期间有意义**（`drivers/clk/clk.c:71`、`:74-76`）。它们是三段式频率变更的暂存区：第一段算出整棵子树的新值先存这里，第三段才真正写寄存器。03 Part A 展开。

### 3.2 提供方接口（`include/linux/clk-provider.h`）

| 结构 | 位置 | 关键字段 | 说明 |
|---|---|---|---|
| `struct clk_hw` | `:324-328` | `core` / `clk` / `init` | 只有三个指针。`init` 在注册**成功后被核心清空**（kerneldoc `:320-322`），所以注册后不要再读它 |
| `struct clk_init_data` | `:297-306` | `name` / `ops` / `parent_names` \| `parent_data` \| `parent_hws` / `num_parents` / `flags` | 注册时的入参包。源码里有一行注释明写「**三个 parent_\* 只能填一个**」（`:300`） |
| `struct clk_parent_data` | `:276-281` | `hw` / `fw_name` / `name` / `index` | 混合来源的父描述：`hw` 用于同一提供方内部的时钟，`fw_name` 用于 DT 里 `clock-names` 的名字，`name` 是全局名兜底，`index` 是 DT `clocks` 的下标 |
| `struct clk_rate_request` | `:57-63` | `rate` / `min_rate` / `max_rate` / `best_parent_rate` / `best_parent_hw` | `determine_rate` 的**双向**参数：调用方填请求和约束，回调改 `rate` 和两个 `best_parent_*`。kerneldoc 要求用 `clk_hw_init_rate_request()` 初始化（`:45`），别自己 memset |
| `struct clk_duty` | `:80-83` | `num` / `den` | 占空比的分数表示 |
| `struct clk_hw_onecell_data` | `:1360-1363` | `num` / `hws[]` | **DT provider 的标配**：柔性数组，配合 `of_clk_hw_onecell_get` 按 `clkspec->args[0]` 直接下标。厂商驱动一般 `devm_kzalloc(struct_size(...))` 一次分配 |
| `struct clk_onecell_data` | `:1355-1358` | `clks` / `clk_num` | 上面那个的 `struct clk *` 老版本，与 `clk_register` 一起属于遗留路径 |

### 3.3 八个基本类型（都把 `clk_hw` 放在**第一个成员**）

统一形状：`struct clk_<类型> { struct clk_hw hw; <硬件细节>; spinlock_t *lock; }`，配一个 `to_clk_<类型>()` 宏做 `container_of`。

| 结构 | 位置 | 硬件细节字段 | `to_` 宏 | ops 符号 |
|---|---|---|---|---|
| `clk_fixed_rate` | `:350-355` | `fixed_rate` / `fixed_accuracy` / `flags` | — | `clk_fixed_rate_ops`（`:359`） |
| `clk_gate` | `:510-516` | `reg` / `bit_idx` / `flags` / `lock` | `to_clk_gate`（`:518`） | `clk_gate_ops`（`:524`） |
| `clk_divider` | `:658-666` | `reg` / `shift` / `width` / `flags` / `table` / `lock` | `to_clk_divider`（`:669`） | `clk_divider_ops`（`:680`）/ **`clk_divider_ro_ops`**（`:681`） |
| `clk_mux` | `:946-954` | `reg` / `table` / `mask` / `shift` / `flags` / `lock` | `to_clk_mux`（`:956`） | `clk_mux_ops`（`:965`）/ **`clk_mux_ro_ops`**（`:966`） |
| `clk_fixed_factor` | `:1072-1076` | `mult` / `div` | `to_clk_fixed_factor`（`:1078`） | `clk_fixed_factor_ops`（`:1080`） |
| `clk_fractional_divider` | `:1132-1146` | `reg` / `mshift`/`mwidth`/`mmask` / `nshift`/`nwidth`/`nmask` / `flags` / **`approximation` 回调** / `lock` | `to_clk_fd`（`:1148`） | 无导出符号（`drivers/clk/clk-fractional-divider.c` 内部） |
| `clk_multiplier` | `:1188-1195` | `reg` / `shift` / `width` / `flags` / `lock` | `to_clk_multiplier`（`:1197`） | `clk_multiplier_ops`（`:1203`） |
| `clk_composite` | `:1216-1227` | `ops`（**值，不是指针**）/ 三对 `*_hw` + `*_ops` | `to_clk_composite`（`:1229`） | 无——它**在运行时拼出自己的 `ops`** |

**`clk_composite` 的特殊之处**：它的第二个成员是 `struct clk_ops ops`（`:1218`）——一个**结构体值**而不是指针。注册时 `clk-composite.c` 逐个检查 mux_ops / rate_ops / gate_ops 里有哪些回调，把它们**拼装**进这个内嵌的 ops，再把 `&composite->ops` 交给核心。这是全 clk 子系统里唯一一处"ops 表在运行时生成"的地方。

**`_ro_ops` 的存在意义**：`clk_divider_ro_ops` 和 `clk_mux_ro_ops` 是"硬件只读"版本——只有 `recalc_rate`/`get_parent` 而没有 `set_*`。用它们比用普通 ops + `CLK_DIVIDER_READ_ONLY` 标志更直接：**核心从 ops 表里就看不到 setter，连尝试都不会尝试**。

### 3.4 消费者接口（`include/linux/clk.h`）

| 结构 | 位置 | 关键字段 | 说明 |
|---|---|---|---|
| `struct clk_notifier` | `:54-58` | `clk` / `notifier_head` / `node` | 框架为每个"被监听的时钟"建一个，挂在 `clk_notifier_list`（`drivers/clk/clk.c:38`）。用的是 **SRCU** 通知链，所以 `COMMON_CLK` 要 `select SRCU` |
| `struct clk_notifier_data` | `:71-75` | `clk` / `old_rate` / `new_rate` | 传给回调的数据 |
| `struct clk_bulk_data` | `:87-90` | `id` / `clk` | 批量操作的数组元素。`id` 由调用者填（或 `_get_all` 时由框架填） |

### 3.5 clkdev 静态表（`include/linux/clkdev.h`）

| 结构 | 位置 | 关键字段 | 说明 |
|---|---|---|---|
| `struct clk_lookup` | `:18-24` | `dev_id` / `con_id` / `clk` / `clk_hw` | 一条 (设备名, 连接名) → 时钟的映射。同时有 `clk` 和 `clk_hw` 两个字段是历史包袱：新代码走 `clk_hw`，旧的 `clkdev_add` 路径走 `clk` |

---

## §4 `struct clk_ops` 的 25 个回调

定义在 `include/linux/clk-provider.h:232-267`。**一个都不是必填**，但组合有硬约束。

### 4.1 按用途分七组

| 组 | 回调（行号） | 返回值 | 什么时候被调 |
|---|---|---|---|
| **prepare 三件套** | `prepare`（`:233`）/ `unprepare`（`:234`）/ `is_prepared`（`:235`） | int / void / int | 持 `prepare_lock`（mutex），**可以睡眠** |
| **enable 三件套** | `enable`（`:237`）/ `disable`（`:238`）/ `is_enabled`（`:239`） | int / void / int | 持 `enable_lock`（spinlock + irqsave），**绝不能睡眠** |
| **清理未用时钟** | `unprepare_unused`（`:236`）/ `disable_unused`（`:240`） | void | 只在 `clk_disable_unused` 那一次（`late_initcall_sync`）被调。给"硬件上其实开着但引用计数是 0"的节点一个特殊关法 |
| **频率读** | `recalc_rate`（`:243-244`） | unsigned long | 父频率变了、或首次注册时，核心用它重算缓存。参数是父频率，回调**不该自己去读父的 rate** |
| **频率算** | `round_rate`（`:245-246`）/ `determine_rate`（`:247-248`） | long / int | 只做计算，不碰寄存器。**二选一** |
| **频率写** | `set_rate`（`:251-252`）/ `set_rate_and_parent`（`:253-255`） | int | 三段式的第三段。后者用于"必须同时切父和改频率"的硬件 |
| **父节点** | `set_parent`（`:249`）/ `get_parent`（`:250`） | int / u8 | `get_parent` 返回**索引**而不是指针 |
| **精度** | `recalc_accuracy`（`:256-257`） | unsigned long | 没实现就继承父精度 |
| **相位/占空比** | `get_phase`（`:258`）/ `set_phase`（`:259`）/ `get_duty_cycle`（`:260-261`）/ `set_duty_cycle`（`:262-263`） | int | 见 01 §3.5 的三处"静默成功" |
| **生命周期** | `init`（`:264`）/ `terminate`（`:265`） | int / void | `init` 在 6.1 里返回 **int**（早期版本是 void）。kerneldoc 明说「基本类型都不用它」（`:203-204`），且**不许在里面碰频率或父节点**（`:208-209`） |
| **上下文** | `save_context`（`:241`）/ `restore_context`（`:242`） | int / void | 只被 `clk_save_context`/`clk_restore_context` 调，见 01 §1.9 |
| **调试** | `debug_init`（`:266`） | void | debugfs 目录建好后调一次，让类型加自己的文件。持 `prepare_lock` |

### 4.2 组合约束（注册时会校验）

`__clk_core_init` 里逐条检查，不合格直接 `-EINVAL`：

```
实现了 set_rate
    → 必须同时有 (round_rate 或 determine_rate) 且 有 recalc_rate    [clk.c:3808-3814]
实现了 set_parent
    → 必须同时有 get_parent                                         [clk.c:3817-3822]
num_parents > 1
    → 必须有 get_parent                                             [clk.c:3824-3829]
实现了 set_rate_and_parent
    → 必须同时有 set_parent 和 set_rate                             [clk.c:3831-3837]
```

注意 `set_rate_and_parent` 那条：它不是 `set_parent`+`set_rate` 的替代品，而是**附加**——两个单独的回调仍然必须实现，供"只改频率"或"只切父"的场景用。

**`determine_rate` vs `round_rate` 怎么选**：

| | `round_rate` | `determine_rate` |
|---|---|---|
| 出参 | 返回值一个数字 + `*parent_rate` | 整个 `clk_rate_request`（`rate` / `best_parent_rate` / `best_parent_hw`） |
| 能不能改父 | 只能改**当前**父的频率 | ✅ 能指定**换到哪个**父 |
| mux 型节点 | 不够用 | 必需 |
| 新代码 | 不要用 | 用这个 |

写 mux 时直接把 `__clk_mux_determine_rate` 或 `__clk_mux_determine_rate_closest` 填进 `.determine_rate`（01 §2.4），不用自己写。

---

## §5 `CLK_*` 框架标志

定义在 `include/linux/clk-provider.h:19-34`，填进 `clk_init_data.flags` → `clk_core->flags`（`drivers/clk/clk.c:77`）。

头文件顶上有一行给改代码的人看的提醒（`:17`）：**「改这里记得同步更新 `drivers/clk/clk.c` 的 `clk_flags[]`」**——那张表（`drivers/clk/clk.c:3418`）是 debugfs 里 `clk_flags` 文件的名字来源。

| 标志 | 位 | 语义 | 踩坑点 |
|---|---|---|---|
| `CLK_SET_RATE_GATE` | BIT(0) | 改频率前必须先关掉 | 这不只是个检查：节点一旦 `clk_prepare` 成功，核心**自动给它加频率保护**（`drivers/clk/clk.c:1072-1080`），此后任何 `clk_set_rate` 都返回 `-EBUSY` |
| `CLK_SET_PARENT_GATE` | BIT(1) | 切父前必须先关掉 | 判据是 **`prepare_count`** 不是 enable_count（`drivers/clk/clk.c:2793-2795`） |
| `CLK_SET_RATE_PARENT` | BIT(2) | 自己调不到目标频率时，**把请求转发给父** | 最常用也最容易漏。少了它，一条支路上的分频器就只能在父频率的固定除数里选 |
| `CLK_IGNORE_UNUSED` | BIT(3) | `clk_disable_unused` 时跳过它 | **和 `clk_ignore_unused` 内核参数不是一回事**：这个是 per-clk 的编译期声明，那个是全局的启动参数（07 §2.1） |
| —（BIT(4)/BIT(5)） | — | **未使用**（`:23-24` 两行 `/* unused */`） | 曾经的标志被删了，位号故意留空以免破坏 out-of-tree 代码 |
| `CLK_GET_RATE_NOCACHE` | BIT(6) | 每次 `clk_get_rate` 都重新调 `recalc_rate` | 用于会被固件/其他核偷偷改掉的时钟 |
| `CLK_SET_RATE_NO_REPARENT` | BIT(7) | 改频率时不许换父 | 给"换父会产生毛刺"的硬件 |
| `CLK_GET_ACCURACY_NOCACHE` | BIT(8) | 精度也不缓存 | |
| `CLK_RECALC_NEW_RATES` | BIT(9) | 发完通知后再重算一遍频率 | 给"实际频率要等通知里的动作做完才确定"的节点 |
| `CLK_SET_RATE_UNGATE` | BIT(10) | **改频率时时钟必须在跑** | 与 `CLK_SET_RATE_GATE` 正好相反。核心会临时 enable、改完再还原 |
| `CLK_IS_CRITICAL` | BIT(11) | **永远不许关** | 核心在注册时就替它 prepare+enable 一次，`clk_disable` 到最后一次会 `WARN` 并拒绝（`drivers/clk/clk.c:1131-1133`）。这是防止"关掉 DDR 时钟把板子挂死"的机制，DT 侧对应 `clock-critical`（06） |
| `CLK_OPS_PARENT_ENABLE` | BIT(12) | 门控/改频率/切父期间**父必须是开着的** | 给"寄存器只在父时钟供着时才能写"的硬件。核心会自动临时 enable 父 |
| `CLK_DUTY_CYCLE_PARENT` | BIT(13) | 占空比操作可以转发给父 | 与 `CLK_SET_RATE_PARENT` 同为转发类（`drivers/clk/clk.c:3078-3081`） |

硬件类型自己的标志（`CLK_GATE_*` / `CLK_DIVIDER_*` / `CLK_MUX_*` / `CLK_FRAC_DIVIDER_*` / `CLK_MULTIPLIER_*` / `CLK_FIXED_RATE_PARENT_ACCURACY`）是**另一套**，放在各类型结构的 `flags` 字段里，框架不认识它们（头文件注释 `:13-15` 明确了这个分工）。00 §2.3 有分频器那 7 条的映射表。

---

## §6 核心操作数据流图 + 命名约定

### 6.1 `clk_set_rate` 的数据流（消费者 → 硬件）

```
消费者                核心（clk.c）                       提供方 ops              硬件
  │
  │ clk_set_rate(clk, 100M)
  ├──────────────────→ clk_prepare_lock()        ← mutex，可睡眠
  │                         │
  │                    ┌── 第 1 段：只算不做 ────────────────────┐
  │                    │ clk_core_req_round_rate_nolock          │
  │                    │        └──────────────→ .determine_rate │
  │                    │                          （或 round_rate）
  │                    │        ← rate / best_parent_hw / best_parent_rate
  │                    │ 算出来等于当前值？→ 直接 return 0 │
  │                    │ clk_calc_new_rates(core, rate)           │
  │                    │   把整棵受影响子树的结果写进               │
  │                    │   new_rate / new_parent / new_child      │
  │                    │   （clk_core:71,74-76，只在本次调用期间有效）
  │                    └─────────────────────────────────────────┘
  │                    ┌── 第 2 段：发预通知 ───────────────────┐
  │                    │ clk_propagate_rate_change(PRE_RATE_CHANGE)
  │                    │   → 每个注册者的 notifier_call          │
  │                    │   有人返回 NOTIFY_BAD？                 │
  │                    │     → 补发 ABORT_RATE_CHANGE，-EBUSY │
  │                    └────────────────────────────────────────┘
  │                    ┌── 第 3 段：提交 + 后通知 ──────────────┐
  │                    │ clk_change_rate(top)  自顶向下递归       │
  │                    │        └──────────────→ .set_rate ─────────────→ 写寄存器
  │                    │                      （或 .set_rate_and_parent）
  │                    │        └──────────────→ .recalc_rate ← 回读算出真实值
  │                    │   更新 core->rate 缓存                   │
  │                    │   发 POST_RATE_CHANGE                    │
  │                    └────────────────────────────────────────┘
  │←─────────────────── clk_prepare_unlock()
  │  返回 0 或负错误码 ← 0 不代表频率真的变了（01 §3.3）
```

三段之间的关键性质：**第 1 段一个寄存器都不碰**。这让"算不出来"和"写失败"能分开处理——绝大多数失败在第 1、2 段就返回了，硬件状态没被动过。

### 6.2 `clk_prepare_enable` 的数据流

```
clk_prepare(clk)
  → clk_prepare_lock()  [mutex]
      → clk_core_prepare(core)
          → 递归 clk_core_prepare(core->parent)   先父后子
          → prepare_count == 0 ? 调 .prepare : 只 ++
          → CLK_SET_RATE_GATE ? 自动加频率保护      [clk.c:1072-1080]
  → clk_prepare_unlock()

clk_enable(clk)
  → clk_enable_lock()   [spinlock_irqsave]
      → clk_core_enable(core)
          → prepare_count == 0 ? return -ESHUTDOWN  [clk.c:1188-1190]
          → 递归 clk_core_enable(core->parent)     先父后子
          → enable_count == 0 ? 调 .enable : 只 ++
  → clk_enable_unlock()
```

关闭方向完全对称，但**顺序反过来**：先子后父（`clk_core_disable` 在自己的 `.disable` 之后才递归父，`drivers/clk/clk.c:1145`）。

### 6.3 命名约定速查

看到一个符号，靠名字就能判断它属于哪一层：

| 前缀 / 形态 | 属于 | 例子 |
|---|---|---|
| `clk_*(struct clk *…)` | **消费者 API**，在 `include/linux/clk.h` | `clk_get` / `clk_set_rate` |
| `clk_hw_*(struct clk_hw *…)` | **提供方 API**，在 `include/linux/clk-provider.h` | `clk_hw_register` / `clk_hw_get_rate` |
| `clk_hw_register_<类型>` | 基本类型的注册宏 | `clk_hw_register_gate` |
| `__clk_hw_register_<类型>` | 上面那些宏真正展开到的函数 | `__clk_hw_register_gate` |
| `devm_*` | 自动清理版 | `devm_clk_get_enabled` |
| `of_clk_*` | 和 DT 打交道 | `of_clk_add_hw_provider` |
| `clk_core_*` | **框架内部**，只在 `drivers/clk/clk.c` 里 | `clk_core_prepare` |
| `*_nolock` | 内部，**要求调用者已持锁** | `clk_core_set_rate_nolock` |
| `__clk_*` | 半内部：导出了但只给少数厂商/PM 代码 | `__clk_get_hw` / `__clk_lookup` |
| `to_clk_<类型>(hw)` | `container_of` 宏 | `to_clk_gate` |
| `<类型>_ro_ops` | 只读版本的 ops（无 setter） | `clk_divider_ro_ops` |
| `divider_*` / `clk_mux_*_val` | 可复用的**计算**辅助函数（不碰锁不碰树） | `divider_get_val` |

一条最省事的判据：**参数是 `struct clk *` 就是消费者的，是 `struct clk_hw *` 就是提供方的**。

---

## §7 全局变量与锁

### 7.1 全局变量（全部在 `drivers/clk/clk.c`，全部 `static`）

| 变量 | 行 | 类型 | 作用 |
|---|---|---|---|
| `enable_lock` | `:27` | spinlock | 保护 `enable_count` 和 `.enable`/`.disable` 调用 |
| `prepare_lock` | `:28` | mutex | 保护树拓扑、`prepare_count`、频率、通知链 |
| `prepare_owner` | `:30` | `task_struct *` | 当前持 `prepare_lock` 的任务 |
| `enable_owner` | `:31` | `task_struct *` | 当前持 `enable_lock` 的任务 |
| `prepare_refcnt` | `:33` | int | 同一任务的重入次数 |
| `enable_refcnt` | `:34` | int | 同上 |
| `clk_root_list` | `:36` | hlist | 所有根节点（无父） |
| `clk_orphan_list` | `:37` | hlist | 所有孤儿节点（父未注册） |
| `clk_notifier_list` | `:38` | list | 所有 `clk_notifier` |
| `clk_rpm_list` | `:41` | hlist | 启用了 runtime PM 的时钟 |
| `clk_rpm_list_lock` | `:42` | mutex | 保护上一条 |
| `all_lists[]` | `:44-48` | 数组 | `{ &clk_root_list, &clk_orphan_list, NULL }`——遍历全树时用它一次扫两条链 |
| `rootdir` | `:3194` | `dentry *` | `/sys/kernel/debug/clk` |
| `clk_debug_lock` | `:3196` | mutex | 保护 debugfs 链表 |
| `clk_debug_list` | `:3197` | hlist | 已建 debugfs 目录的节点 |
| `clk_flags[]` | `:3418` | 数组 | `CLK_*` 标志的名字表，供 debugfs 的 `clk_flags` 文件用 |

**没有全局的"所有时钟"链表**。要遍历全树只能从 `all_lists[]` 的两个根出发递归 `children`。这就是为什么按名字查找（`__clk_lookup` / `clk_core_lookup`）是 O(n) 的树遍历，而不是哈希查表。

### 7.2 两把锁的规则

| | `prepare_lock` | `enable_lock` |
|---|---|---|
| 类型 | mutex | spinlock（`clk_enable_lock` 里 `spin_lock_irqsave`） |
| 加锁函数 | `clk_prepare_lock`（`drivers/clk/clk.c:214`）/ `clk_prepare_unlock`（`:229`） | `clk_enable_lock`（`:240`）/ `clk_enable_unlock`（`:268`） |
| 保护什么 | 树拓扑、`prepare_count`、`protect_count`、rate/accuracy/phase/duty 缓存、通知链、debugfs 建目录 | 只有 `enable_count` 和 `.enable`/`.disable` |
| 嵌套顺序 | **外层** | **内层**（持 prepare_lock 时可以再取 enable_lock，反之不行） |
| 可重入 | ✅ 见下 | ✅ 见下 |

**两把锁都是"手工实现的可重入锁"**（`drivers/clk/clk.c:214-227` 和 `:240-266`）。做法：

```
if (!mutex_trylock(&prepare_lock)) {
        if (prepare_owner == current) {   ← 是我自己已经持有的？
                prepare_refcnt++;          ← 只加计数，不阻塞
                return;
        }
        mutex_lock(&prepare_lock);          ← 别人持有，正常等
}
prepare_owner = current;
prepare_refcnt = 1;
```

**为什么需要可重入**：clk 的所有操作都是沿树递归的（prepare 父、enable 父、改父频率），而递归的每一层都会走一遍加锁的公共入口。设计意图记在 `Documentation/driver-api/clk.rst:298-303`。

**由此产生的一个真实约束**：通知回调（`PRE_RATE_CHANGE` 等）是在**持 `prepare_lock` 的上下文**里跑的。锁可重入意味着你在回调里调 `clk_set_rate` 不会死锁——但那是**递归改频率**，会把正在进行的三段式搅乱。所以 01 §1.6 的规则是"不要在回调里改频率"，理由不是死锁而是语义。

**和 runtime PM 的交互有一处专门的注释**（`drivers/clk/clk.c:130-140`）：`clk_disable_unused` 必须先把所有启用了 runtime PM 的时钟提供者 `pm_runtime_get` 一遍，否则会和"某个设备正在 runtime resume、其回调正要拿 `prepare_lock`"形成死锁。这就是 `clk_rpm_list` 这条链表存在的唯一原因。
