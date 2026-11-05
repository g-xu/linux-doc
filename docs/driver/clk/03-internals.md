---
title: 内部实现
---

# clk 子系统实现剖析

> 基于 Linux 6.1.158（`drivers/clk/clk.c`，5463 行）。arm64-only。
> 本篇是 clk 文档集里唯一逐段读源码的一篇，行号密度最高。

---

## §0 导航

### 0.1 本篇的组织轴

本篇只承诺**一条主轴：时间线**（Part A）。凡是跨越多个时间点的话题，一律移到 Part B（横切），对实现本身的评价放 Part C。章节号自带轴信息：

| 分区 | 轴 | 内容 |
|---|---|---|
| **Part A** | 时间**顺序** | 从提供方驱动 probe 到系统关机，按"先发生什么"排 |
| **Part B** | **横切**关注点 | 锁、通知、缓存一致性、孤儿修复、引用计数、runtime PM |
| **Part C** | 评价 | 设计上的取舍、源码里自陈的设计债 |

### 0.2 分层图（每一层住在哪个文件）

```
┌─────────────────────────────────────────────────────────────┐
│ 消费者驱动          clk_get / clk_prepare_enable / set_rate  │
│ （drivers/ 任何角落）                                        │
└───────────────┬─────────────────────────────────────────────┘
                │  include/linux/clk.h  ← 只有 struct clk 的不完整声明
┌───────────────▼─────────────────────────────────────────────┐
│ 查表层     clkdev.c（名字查表）  clk.c:4813+（DT phandle 查表）│
│            clk-devres.c（devm 包装）  clk-bulk.c（批量包装）  │
└───────────────┬─────────────────────────────────────────────┘
┌───────────────▼─────────────────────────────────────────────┐
│ 框架核心   drivers/clk/clk.c                                 │
│   · clk_core 树 + 两把可重入锁                                │
│   · 三段式频率变更 / prepare-enable 递归 / 通知链              │
│   · 注册 / 注销 / 孤儿修复 / disable_unused / debugfs         │
└───────────────┬─────────────────────────────────────────────┘
                │  include/linux/clk-provider.h ← struct clk_ops
┌───────────────▼─────────────────────────────────────────────┐
│ 提供方     基本类型（clk-gate.c / clk-divider.c / clk-mux.c…） │
│            厂商驱动（drivers/clk/<vendor>/）                  │
└───────────────┬─────────────────────────────────────────────┘
                │  readl / writel
┌───────────────▼─────────────────────────────────────────────┐
│ 硬件       CRU / PLL / 门控寄存器                             │
└─────────────────────────────────────────────────────────────┘
```

### 0.3 生命周期时间线

```
时刻                     发生什么                                本篇章节
─────────────────────────────────────────────────────────────────────────
① CLK_OF_DECLARE 或      提供方驱动被调起                        §A1
   platform probe

② clk_hw_register()      kzalloc clk_core → 拷 ops/flags        §A2
   × N                   → 建 clk_parent_map 缓存

③ __clk_core_init()      重名检查 → ops 合法性 → .init 回调      §A3
                         → 解析父 → 挂树/挂孤儿链
                         → 算 accuracy/rate/phase/duty
                         → CLK_IS_CRITICAL 自动 enable
                         → 反向修复孤儿 → 建 debugfs

④ of_clk_add_hw_provider 把 np → xlate 回调登记进               §A4
                         of_clk_providers 链表
                         → of_clk_set_defaults()（assigned-*）

⑤ 消费者 clk_get()       名字/phandle 查表 → clk_hw             §A5
                         → kzalloc 一个 per-consumer struct clk

⑥ prepare_enable /       递归 + 三段式                           §A6 §A7
   set_rate / set_parent

⑦ late_initcall_sync     clk_disable_unused()                    §A8

⑧ 消费者 clk_put()       kfree 那个 struct clk                   §A9

⑨ clk_hw_unregister()    ops 换成 clk_nodrv_ops → 子节点全部    §A9
                         打成孤儿 → kref_put

⑩ suspend / resume       clk_save_context / clk_restore_context  §B6
```

### 0.4 哪些内容不在本篇

| 你想找 | 去 |
|---|---|
| 某个 API 的签名、参数、返回值 | 01 |
| 结构体字段清单、`clk_ops` 25 个回调逐条、`CLK_*` 标志表 | 02 |
| 八种基本类型各自的算法（分频器怎么算除数） | 04 |
| 写一个提供方 / 消费者的代码骨架 | 05 |
| DTS 属性怎么写、`assigned-clocks` 语义 | 06 |
| debugfs 文件、tracepoint、`clk_ignore_unused` 怎么用 | 07 |
| "prepare 和 enable 到底差在哪"这类概念疑问 | 08 |

---

# Part A：时间线

## §A1 提供方驱动是怎么被调起的

clk 提供方有**两条**入口，差别在时机，而不在做的事情：

| 入口 | 宏/机制 | 时机 | 用在哪 |
|---|---|---|---|
| **DT 早期声明** | `CLK_OF_DECLARE` / `CLK_OF_DECLARE_DRIVER`（`include/linux/clk-provider.h:1365-1371`） | `of_clk_init()`，**比设备模型还早**——arm64 上由 `time_init()` 调用 | 计时器、串口这些必须在设备模型起来之前就有时钟的场景 |
| **正常 platform 驱动** | `platform_driver` 的 `.probe` | 设备模型正常 probe | 绝大多数 SoC 的 CRU 驱动 |

`of_clk_init()`（`drivers/clk/clk.c:5397`）不是简单地遍历一遍就完事：它维护一个 `clk_provider_list`（`:5404`），**对每个 provider 反复重试**，因为 provider A 的初始化可能依赖 provider B 已经注册好的时钟。重试直到某一轮没有任何进展为止。这是 clk 里第一处"依赖顺序未知，所以靠迭代收敛"的设计——第二处是孤儿修复（§B4）。

`CLK_OF_DECLARE_DRIVER` 和 `CLK_OF_DECLARE` 的区别很容易看漏：前者在 `of_clk_init` 里跑完之后**会清掉 `OF_POPULATED` 标记**（`include/linux/clk-provider.h:1380`），让同一个 DT 节点稍后**再**被 platform 驱动 probe 一次。用于"一部分时钟必须早出，其余的等设备模型"的驱动。

## §A2 `clk_hw_register()`：把 `clk_hw` 变成 `clk_core`

`clk_hw_register`（`drivers/clk/clk.c:4331`）→ `__clk_register`（`:4197`）。这一段做的全部是**分配和拷贝**，不碰硬件。

按源码顺序：

| 步 | 行 | 做什么 | |
|---|---|---|---|
| 1 | `:4209` | `hw->init = NULL` | **注册后立刻把 init 数据置空**。注释说得很直白：init data 只在注册路径里有效，置空是为了让提供方驱动**没法**在别处再用它，也让核心自己误用时立刻炸 |
| 2 | `:4211-4215` | `kzalloc(sizeof(struct clk_core))` | 提供方看不到这块内存 |
| 3 | `:4217` | `kref_init(&core->ref)` | 引用计数起点，见 §B5 |
| 4 | `:4219` | `core->name = kstrdup_const(init->name)` | 名字是**拷贝**的，提供方可以用栈上的字符串 |
| 5 | `:4224-4228` | `WARN_ON(!init->ops)` → `core->ops = init->ops` | ops 是**指针拷贝**，不是深拷贝。所以 `clk_ops` 表必须是 `static const` 的长寿命对象。唯一例外是 `clk_composite` 在运行时组装的那张（02 §3.3） |
| 6 | `:4230-4239` | 拷 `dev` / `of_node` / `owner` / `hw` / `flags` / `num_parents`，并把 `min_rate=0` `max_rate=ULONG_MAX` | `core->owner` 取自 `dev->driver->owner`——这是 §B5 模块引用计数的来源 |
| 7 | `:4241` | `clk_core_populate_parent_map()` | 见下 |
| 8 | `:4245` | `INIT_HLIST_HEAD(&core->clks)` | per-consumer `struct clk` 的挂点 |
| 9 | `:4251` | `hw->clk = alloc_clk(core, NULL, NULL)` | 注释解释了为什么**不**用 `clk_hw_create_clk`：那会 `try_module_get` 提供方自己，导致模块永远卸不掉 |
| 10 | `:4257` | `clk_core_link_consumer()` | 把这个内部 `clk` 挂进 `core->clks` |
| 11 | `:4259` | `__clk_core_init()` | 真正的初始化，见 §A3 |
| 12 | `:4262-4266` | 失败时反向拆：unlink → `free_clk` → 逐级 goto | |

### A2.1 `clk_core_populate_parent_map()`：父关系为什么是"线索"而不是指针

`drivers/clk/clk.c:4104`。它把 `clk_init_data` 里三选一的父描述（`parent_names` / `parent_hws` / `parent_data`）统一转成一个 `struct clk_parent_map` 数组（`core->parents`）。

关键在 `:4118-4120` 的注释：这个数组的定位是**缓存**——「避免反复做字符串查找」。每个槽位存的是"怎么找到第 i 个父"的线索（hw 指针 / DT fw_name / 全局 name / DT index），加上一个 `core` 字段用来缓存解析结果。

**为什么必须是线索而不是指针**：注册顺序是任意的。一个分频器可能在它的 PLL 父注册之前就注册了。如果这里存指针，就只能要求提供方按拓扑序注册——那对一个有几百个时钟的 CRU 驱动是不现实的。存线索意味着"解析可以推迟到任何时候"，这就是孤儿机制（§B4）的地基。

`num_parents == 0` 时直接返回 0（`:4114-4115`），根节点不分配这个数组。

## §A3 `__clk_core_init()`：逐段读

`drivers/clk/clk.c:3778`。全程持 `prepare_lock`。这是整个子系统里最长也最关键的一个函数，按源码顺序：

### A3.1 先把 `hw->core` 接上（`:3792`）

```c
core->hw->core = core;
```

上面 `:3787-3791` 的注释解释了为什么这行必须在**拿到 `prepare_lock` 之后**：`clk_core_fill_parent_index()` 用 `hw->core == NULL` 当作"这个 clk 还没注册完"的判据。在锁内赋值，保证了**没有节点会在它的父完全注册好之前被认作父**。

### A3.2 两道校验（`:3799-3837`）

| 校验 | 行 | 失败返回 | 说明 |
|---|---|---|---|
| 重名 | `:3800-3805` | `-EEXIST` | `clk_core_lookup(core->name)` 全树扫两条根链。只是 `pr_debug`，不是 `pr_err`——重名在有些平台上是"预期会失败"的探测手段 |
| ops 组合合法性 | `:3807-3837` | `-EINVAL` | 四条规则，02 §4.2 有表。`:3807` 的注释直接指向 `Documentation/driver-api/clk.rst`。这四条**全都 `pr_err`**，所以 dmesg 里能看到具体是哪条不满足 |

注意校验只覆盖**函数指针组合**，不校验硬件语义。填错寄存器偏移、把 mux 的 `.get_parent` 写成永远返回 0，核心一概不知道。

### A3.3 `.init` 回调（`:3853-3857`）

```c
if (core->ops->init) {
        ret = core->ops->init(core->hw);
        ...
}
```

上面 `:3839-3852` 有一整段注释交代 `.init` 的定位，三条硬约束：
1. **八种基本类型一个都没用它**——它是给"必须做初始化魔法，否则 CCF 看到的状态是错的"的怪硬件留的
2. **可以在里面做动态分配**，但必须在 `.terminate` 里释放
3. **不许用来初始化 rate / parent 这类参数状态**——那是核心接下来自己要做的事

在 6.1 里 `.init` 返回 **int**（02 §4.1）；失败会走 `goto out`，整个注册失败。

### A3.4 解析父节点、挂上树（`:3859-3878`）

```c
parent = core->parent = __clk_init_parent(core);
```

`__clk_init_parent`（`:2728`）的逻辑：`num_parents == 0` → NULL；`num_parents == 1` → 直接解析第 0 个槽；否则调 `.get_parent` 拿索引再解析那个槽。解析可能返回 NULL（父还没注册），这不是错误。

然后按三种情况挂链：

```
parent 非空              → hlist_add_head(&core->child_node, &parent->children)   :3872
parent 为空 且 是根      → hlist_add_head(&core->child_node, &clk_root_list)      :3875
parent 为空 但 声明了父  → hlist_add_head(&core->child_node, &clk_orphan_list)    :3878
                           core->orphan = true
```

「根」和「孤儿」的判据是 `num_parents`：**声明了 0 个父叫根，声明了父但一个都解析不到叫孤儿**。两者都挂在全局链表上、都会被 `all_lists[]` 扫到，但语义完全不同——孤儿的 rate 是**不可信的**。

### A3.5 算 accuracy / rate / phase / duty（`:3880-3928`）

顺序固定，每一项都遵循"有回调就调，没回调就继承父，没父就用默认值":

| 属性 | 行 | 有回调 | 无回调有父 | 无回调无父 |
|---|---|---|---|---|
| accuracy | `:3889-3895` | `.recalc_accuracy(hw, parent->accuracy)` | 继承 `parent->accuracy` | **0** |
| rate | `:3921-3928` | `.recalc_rate(hw, parent_rate)` | 继承 `parent->rate` | **0** |
| phase | `:3902-3908` | `.get_phase(hw)` | —— | 0 |
| duty | `:3913` | `.get_duty_cycle` | 继承父的 duty | **1/2** |

最后 `:3928`：

```c
core->rate = core->req_rate = rate;
```

**`req_rate` 的初值等于开机时读回来的实际频率**，不是 0。这一点决定了 `clk_set_rate_range`（01 §1.4）在没人调过 `clk_set_rate` 时拿什么值去重新裁剪。

**精度为 0 的含义**：无回调无父时 accuracy = 0，而 0 在 clk 里表示"完全精确（0 ppb 误差）"。所以一个纯软件定义的 fixed-rate 时钟会声称自己是理想时钟。这不是 bug，是"没人提供信息时的乐观默认"，和 01 §3.5 说的四个"静默成功"是同一类。

### A3.6 `CLK_IS_CRITICAL` 自动开启（`:3931-3940`）

```c
if (core->flags & CLK_IS_CRITICAL) {
        clk_core_prepare(core);   /* 核心替它 prepare */
        clk_core_enable(core);    /* 核心替它 enable */
}
```

注释（`:3931-3934`）点明意图：**新注册的关键时钟必须立刻开起来**，否则等到 §A8 的 `clk_disable_unused` 就被关掉了。副作用是这类节点的 `prepare_count`/`enable_count` 永远 ≥ 1，`clk_core_disable` 里那条 `WARN(enable_count == 1 && CLK_IS_CRITICAL)`（`:1131-1133`）就是拦最后一次 disable 的。

### A3.7 反向修复孤儿（`:3952`）

```c
clk_core_reparent_orphans_nolock();
```

**每注册一个节点，都要回头看一遍孤儿链**——刚注册的这个可能正是某些孤儿等的父。细节见 §B4。

### A3.8 建 debugfs（`:3964`）

`clk_debug_register(core)` 挂进 `clk_debug_list`（`:3648`）。`CONFIG_DEBUG_FS=n` 时这是个空函数（`:3724`），不留任何痕迹。

## §A4 提供方登记到 DT：`of_clk_add_hw_provider`

`drivers/clk/clk.c:4908`。做两件事：

```
1. kzalloc 一个 of_clk_provider，填 { np, data, get_hw = xlate }
   list_add(&cp->link, &of_clk_providers)              :4928，持 of_clk_mutex
2. of_clk_set_defaults(np, true)                       :4934
```

第 2 步经常被忽略：**注册 provider 的同时会立刻处理这个节点自己的 `assigned-clock-*` 属性**（`drivers/clk/clk-conf.c:139`）。`clk_supplier = true` 的含义是"我就是那个供应商，所以现在可以安全地配置我自己的默认值了"。06 §4 有 DTS 侧的写法。

这里的锁是**第三把锁** `of_clk_mutex`（`:4814`），只保护 `of_clk_providers` 链表，和 `prepare_lock` / `enable_lock` 完全独立。它是 clk 里唯一一处"锁保护的对象不是时钟树"的地方。

## §A5 消费者 `clk_get()`：两条查表路径

```
clk_get(dev, con_id)                                          clkdev.c:100
  │
  ├─ dev && dev->of_node ？                                    clkdev.c:105
  │     → of_clk_get_hw(dev->of_node, 0, con_id)               clk.c:5164
  │         → of_parse_clkspec(np, 0, con_id, &clkspec)        clk.c:5071
  │             读 "clock-names" 找 con_id 的下标，
  │             再从 "clocks" 里取出第 N 个 phandle → (np, args)
  │         → of_clk_get_hw_from_clkspec(&clkspec)             clk.c:5123
  │             持 of_clk_mutex，在 of_clk_providers 里按 np 找 :5136
  │             → 调 provider->get_hw(clkspec, data) ← xlate 回调
  │     ┌── 成功，或者拿到 -EPROBE_DEFER ？                     clkdev.c:106
  │     └──→ clk_hw_create_clk(dev, hw, dev_id, con_id)        clk.c:4041
  │           直接返回，**不再退回名字查表**
  │
  └─ 没有 of_node，或上面返回的是别的错误
        → __clk_get_sys(dev, dev_id, con_id)                   clkdev.c:86
            → clk_find_hw(dev_id, con_id)                      clkdev.c:72
                → clk_find(dev_id, con_id)                     clkdev.c:36
                    在 clocks 链表里按 (dev_id, con_id) 打分匹配
            → clk_hw_create_clk()
```

**`clkdev.c:106-108` 那个条件是整条路径上最关键的一行**：

```c
if (!IS_ERR(hw) || PTR_ERR(hw) == -EPROBE_DEFER)
        return clk_hw_create_clk(dev, hw, dev_id, con_id);
```

DT 里写了 `clocks = <&cru CLK_X>` 但 cru 驱动还没 probe 时，DT 路径返回 `-EPROBE_DEFER`。如果这时候还往下退回去查 clkdev 名字表，只会得到一个更没用的 `-ENOENT`——消费者会以为"这个时钟根本不存在"从而**永久失败**，而正确行为是"稍后重试"。这一行就是把 defer 语义**保下来**的地方。01 §3.1 的 `dev_err_probe` 惯用法是配合它用的。

注意这条路径**只查 index 0 加名字匹配**：`of_clk_get_hw(np, 0, con_id)` 的 index 参数写死是 0，真正的选择完全靠 `con_id` 在 `clock-names` 里的位置。所以**没有 `clock-names` 的 DT 节点，`clk_get(dev, "xxx")` 一定失败**——要按下标取只能用 `of_clk_get(np, N)`（`drivers/clk/clk.c:5190`）。06 §1.1 把这条列成了 DTS 侧的硬要求。

`clk_core_get()`（`drivers/clk/clk.c:473`）解析**父节点**时走的是同一套机制，但顺序相反：先试 DT（`of_parse_clkspec` + `of_clk_get_hw_from_clkspec`，`:483-486`），DT 找不到才退回 `clk_find_hw(dev_id, name)`（`:492`）。它的 kerneldoc（`:437-471`）用一段 DT 片段示范了 `parent_data.fw_name` 的意义：**让提供方用自己的局部名字（"xtal"）指代外部时钟，而不必知道对方的全局唯一名**。这是 `parent_names`（全局名）被 `parent_data`（局部名 + phandle）取代的根本原因。

`clk_core_get` 的返回值有三态，kerneldoc `:467-471` 写得很清楚，别混：

| 返回 | 含义 | 调用者该怎么办 |
|---|---|---|
| 有效指针 | 找到了 | 接上父 |
| **`NULL`** | 提供方知道这个时钟，但**这个系统上没提供** | 合法状态，节点保持孤儿 |
| `ERR_PTR(-ENOENT)` | 找不到提供方 / 名字不在 DT 也不在 clkdev | 同样保持孤儿，等以后 |

`clk_hw_create_clk`（`:4041`）里除了 kzalloc 一个 `struct clk`，还会 `try_module_get(core->owner)`——**消费者持有时钟句柄期间，提供方模块卸不掉**。这是 §B5 的一半。

## §A6 `prepare` / `enable` 的递归

两条路径形状相同，差别只在锁和"能不能睡"（02 §6.2 有图）。这里补三个只有读源码才看得到的点：

### A6.1 `clk_core_unprepare` 的三道 WARN（`:981-1012`）

```
WARN(prepare_count == 0, "%s already unprepared\n")                   :987-989
WARN(prepare_count == 1 && CLK_IS_CRITICAL, "Unpreparing critical %s") :992
```

和 disable 侧（`:1129`/`:1131`）完全对称。**四条 WARN 全是 `WARN(...)` 而不是 `WARN_ON_ONCE`**——每次都打，因为引用计数失衡是会累积的 bug，只报一次会掩盖问题规模。07 §1.1 讨论了这个可见性等级。

### A6.2 `clk_core_enable` 的 `-ESHUTDOWN`（`:1188-1190`）

```c
if (WARN(core->prepare_count == 0, "..."))
        return -ESHUTDOWN;
```

只 enable 不 prepare 会拿到 `-ESHUTDOWN`——一个非常不直观的错误码。这就是为什么 01 §1.3 说**除非你确知这个时钟不需要 prepare，一律用 `clk_prepare_enable`**。

### A6.3 递归方向：开是先父后子，关是先子后父

| | 顺序 | 代码 |
|---|---|---|
| prepare | 先递归父，再调自己的 `.prepare` | `:1055` 在 `:1061` 之前 |
| enable | 先递归父，再调自己的 `.enable` | `clk_core_enable` 内同构 |
| disable | 先调自己的 `.disable`，**最后**递归父 | `.disable` 在 `:1141`，递归在 `:1145` |
| unprepare | 同 disable | `clk_core_unprepare` 内同构 |

这个次序不是风格问题：**开的时候父必须先就绪**（否则子的寄存器可能写不进去），**关的时候子必须先停**（否则子会在父停掉的瞬间失去时钟，可能锁死总线）。判据就是"谁调用谁"——`:1055` 的 `clk_core_prepare(core->parent)` 在 `:1061` 的 `ops->prepare` 之前，而 `:1145` 的 `clk_core_disable(core->parent)` 在 `:1141` 的 `ops->disable` 之后。

## §A7 三段式频率变更逐段读

入口 `clk_set_rate`（`drivers/clk/clk.c:2503`）→ `clk_core_set_rate_nolock`（`:2433`）。02 §6.1 有整张数据流图，这里只逐段读代码。

### A7.1 第 1 段：算（`:2443-2460`）

```c
rate = clk_core_req_round_rate_nolock(core, req_rate);   :2443
...
if (rate == clk_core_get_rate_nolock(core))              /* 算出来就是当前值 */
        return 0;                                        /* 直接走人 */
...
/* fail on a direct rate set of a protected provider */
if (clk_core_rate_is_protected(core))                    :2450
        return -EBUSY;                                   /* 见下 */

top = clk_calc_new_rates(core, req_rate);                :2454
if (!top)
        return -EINVAL;                                  :2456
```

**`:2449-2451` 这个 `-EBUSY` 是最容易被误诊的一个返回值**。`clk_core_rate_is_protected` 为真的两种来路：有人调过 `clk_rate_exclusive_get`，**或者**这个节点带 `CLK_SET_RATE_GATE` 且已经被 prepare（02 §5 / 01 §1.3）。后一种情况下没有任何"某个消费者"在独占，是提供方自己声明的保护——所以在 `clk_summary` 里看到 `protect_count > 0` 却找不到调用 exclusive_get 的人时，先查这个标志。

`clk_core_req_round_rate_nolock`（`:2407`）只是把请求跑一遍 round/determine，**不写任何字段**。真正把结果写进树的是 `clk_calc_new_rates`（`:2197`）。

`clk_calc_new_rates` 做的事（`:2197-2280`）：

| 步 | 行 | 内容 |
|---|---|---|
| 取当前父和父频率 | `:2216-2218` | `parent = old_parent = core->parent` |
| 取上下界 | `:2220` | `clk_core_get_boundaries()`——把 per-consumer 的 min/max 和硬件的 min/max 求交（02 §2 的"两套 min/max"） |
| 能算就算 | `:2223-2231` | `clk_core_can_round(core)`（`:1623`）为真 → 建 `clk_rate_request` → `clk_core_determine_round_nolock`（`:1501`） |
| 递归往上 | `:2242` / `:2267` | 如果需要改父频率，对父递归 `clk_calc_new_rates`，返回的 `top` 是**这次变更真正的起点** |
| 写 `new_*` 字段 | 函数尾部 | `new_rate` / `new_parent` / `new_parent_index` / `new_child`（`clk_core:71,74-76`） |

**`top` 的含义**：`CLK_SET_RATE_PARENT` 会让请求一路向上传播，最终"真正要改的那个节点"可能在树上离你很远。`top` 就是那个节点。第 2、3 段都是从 `top` 而不是从你调用的那个 clk 开始的。这解释了 01 §1.4 里那个反直觉的现象：**给叶子节点 set_rate，可能导致整个兄弟子树的频率一起变**——因为 `top` 是它们共同的祖先。

`clk_core_can_round`（`:1623`）的判据是"有 `.determine_rate` 或 `.round_rate`"。都没有的话第 1 段没法算，走 `:2445-2447` 那条**静默返回 0**的分支（01 §3.3）。

### A7.2 第 2 段：发预通知（`:2463-2470`）

```c
fail_clk = clk_propagate_rate_change(top, PRE_RATE_CHANGE);   :2463
if (fail_clk) {
        pr_debug(...);
        clk_propagate_rate_change(top, ABORT_RATE_CHANGE);    :2467
        ret = -EBUSY;
        goto err;
}
```

三个细节：
1. **从 `top` 开始**，不是从调用的那个 clk
2. `clk_propagate_rate_change`（`:2280`）返回**第一个否决的节点**，非 NULL 就是有人投了 `NOTIFY_BAD`
3. **补发 `ABORT_RATE_CHANGE`**（`:2467`）——已经收到 PRE 的节点必须被告知"取消了"，否则它们会一直等着 POST。这是 clk 通知链里唯一的三态设计

`clk_propagate_rate_change` 的遍历有一处很容易看漏（`:2286`）：

```c
if (core->rate == core->new_rate)
```

**频率没变的节点不发通知**，直接跳过子树。所以"我注册了 notifier 但没收到"的第一嫌疑不是注册失败，而是**这个节点的频率确实没变**（父变了、它的除数跟着变了、结果还是原来那个数）。07 §2 把这条列进了 tracepoint 分诊。

### A7.3 第 3 段：提交（`:2472-2477`）

```c
clk_change_rate(top);           :2473
core->req_rate = req_rate;      :2475  记的是"你要的"，不是"实到的"
clk_pm_runtime_put(core);       :2477
```

`clk_change_rate`（`:2318`）自顶向下递归，每个节点：

| 步 | 行 | 内容 |
|---|---|---|
| 要换父？ | `:2347` | `__clk_set_parent_before(core, core->new_parent)`——见 §A7.4 |
| 有 `set_rate_and_parent`？ | `:2350-2352` | 一次调用同时改频率和父 |
| 否则先换父再改频率 | `:2360` / `:2369` | `__clk_set_parent_after()`，然后 `.set_rate` |
| `CLK_OPS_PARENT_ENABLE` | `:2363-2364` / `:2380-2381` | 改频率**前**临时 `clk_core_prepare_enable(parent)`，改完 `clk_core_disable_unprepare(parent)` |
| 回读真实频率 | `:2373` | `core->rate = clk_recalc(core, best_parent_rate)` |
| 发 POST | `:2384` | `__clk_notify(core, POST_RATE_CHANGE, old_rate, core->rate)` |
| `CLK_RECALC_NEW_RATES` | `:2386` | 再走一遍 `clk_calc_new_rates` |
| 递归子节点 | `:2397` | 所有 `children` |
| 最后递归 `new_child` | `:2402` | 见下 |

**`:2397` 和 `:2402` 为什么要分两次**：`new_child` 是"这次变更中会被**重新认领**的子节点"——它现在还挂在别的父下面，所以 `:2397` 的 `children` 遍历扫不到它。分两次是为了保证它也拿到 POST 通知。`:2014` 的注释「avoid duplicate POST_RATE_CHANGE notifications」处理的是它可能被扫到两次的情况。

**`req_rate` 记的是请求值而不是实到值**（`:2475`），和注册时记实测值（`:3928`）相反。这个不对称是有意的：`clk_set_rate_range` 后续要重新裁剪时，用的应该是"消费者本来想要什么"。

### A7.4 换父的 before/after 对：为什么要拆开

`__clk_set_parent_before`（`:2031`）/ `__clk_set_parent_after`（`:2077`）。`:2038` 起的注释列了 before 要按顺序做的事：

```
1. CLK_OPS_PARENT_ENABLE → 把 old_parent 和 new_parent 都 prepare_enable   :2058-2060
2. 如果自己正在 enable 状态 → 把 new_parent 也 prepare_enable            :2065
   （保证切过去的瞬间新父已经在跑）
3. 改 clk_core 的树链接（clk_reparent，:2005）
```

after 做完全对称的反向（`:2087-2093`）：把 `old_parent` 放掉、把 `CLK_OPS_PARENT_ENABLE` 临时拿的两个引用都还掉。

**为什么必须拆成两半而不是一个函数**：因为中间那一步（真正写寄存器的 `.set_parent`）在**两个不同的调用点**：`clk_core_set_parent_nolock`（`:2104` / `:2119` / `:2124`）和 `clk_change_rate`（`:2347` / `:2360`）。后者可能用 `set_rate_and_parent` 一次搞定。拆开是为了让"切父的前置/后置动作"这一段逻辑只写一遍。

第 3 个用到它们的地方是**孤儿修复**（`:3750-3751`），那里的注释（`:3743`）专门说明了为什么不能图省事直接改指针。见 §B4。

## §A8 `clk_disable_unused()`：arm64 上最常见的启动挂死点

`drivers/clk/clk.c:1460`，`late_initcall_sync(clk_disable_unused)`（`:1498`）。

```
if (clk_ignore_unused) {                      :1465
        pr_warn("clk: Not disabling unused clocks\n");
        return 0;
}
pr_info("clk: Disabling unused clocks\n");    :1471

clk_pm_runtime_get_all();                     :1473  见 §B7
clk_prepare_lock();                           :1479

for (root_list)   clk_disable_unused_subtree()      :1481
for (orphan_list) clk_disable_unused_subtree()      :1484
for (root_list)   clk_unprepare_unused_subtree()    :1487
for (orphan_list) clk_unprepare_unused_subtree()    :1490

clk_prepare_unlock();
clk_pm_runtime_put_all();                     :1495
```

**四遍遍历的顺序不能改**：必须**先把全树 disable 完**，再开始 unprepare。理由是 `clk_core_enable` 要求 `prepare_count > 0`（`:1188`）——如果边 unprepare 边 disable，某个还没被处理到的节点可能在 disable 递归中需要临时操作一个已经被 unprepare 的父。

**这是 arm64 上启动挂死的头号来源**。症状：内核打完 `clk: Disabling unused clocks` 就没有下一行了。原因几乎总是"某个必须一直开的时钟没人 `clk_get` 也没标 `CLK_IS_CRITICAL`"。第一诊断动作是加 `clk_ignore_unused` 内核参数：

| 现象 | 结论 |
|---|---|
| 加了参数就能启动 | 确认是 clk 关掉了不该关的东西。下一步用 debugfs `clk_summary` 找到 enable_count 归零的那个 |
| 加了参数还是挂 | 挂点不在 clk，`late_initcall_sync` 后面还有别的 initcall |

07 §2.1 有完整的排查流程。

`clk_disable_unused_subtree` 里 `.disable_unused` 优先于 `.disable`（02 §4.1）——这是给"硬件上开着但引用计数是 0"的节点留的特殊关法。

## §A9 拆除路径

### A9.1 `clk_put()`：只拆消费者那一层

`clk_put`（`clkdev.c:115`）→ `__clk_put`（`drivers/clk/clk.c:4608`）。做的事：从 `core->clks` 摘掉这个 `struct clk`、`module_put(core->owner)`、`kfree`。**`clk_core` 一点没动**——它的生命周期归提供方。

`clk_put` 里还会做一件容易忘的事：如果这个消费者句柄之前调过 `clk_rate_exclusive_get` 或设过 min/max range，`__clk_put` 会替它还掉。这就是为什么 01 §1.4 说"忘了配对调用 `clk_rate_exclusive_put` 不会永久漏"——只要最终 `clk_put` 了。

### A9.2 `clk_hw_unregister()`：ops 掉包 + 子节点集体打成孤儿

`clk_hw_unregister`（`:4484`）就一行 `clk_unregister(hw->clk)`。真正的逻辑在 `clk_unregister`（`:4422`）：

| 步 | 行 | 做什么 | |
|---|---|---|---|
| 1 | `:4430` | `clk_debug_unregister()` | 先摘 debugfs，避免有人正在读 |
| 2 | `:4435-4441` | 检查 `ops == &clk_nodrv_ops` | 二次注销的哨兵：已经注销过了就 `pr_err` 返回 |
| 3 | `:4444-4447` | **`clk->core->ops = &clk_nodrv_ops`**（持 `enable_lock`） | 见下 |
| 4 | `:4449-4450` | `ops->terminate(hw)` | 对应 `.init` 里的动态分配 |
| 5 | `:4452-4460` | 所有子节点 `clk_core_set_parent_nolock(child, NULL)` | **子节点全部变成孤儿**，不是跟着一起注销 |
| 6 | `:4462` | `clk_core_evict_parent_cache()`（`:4405`） | 把全树 `clk_parent_map` 里指向本节点的缓存全清掉 |
| 7 | `:4464` | `hlist_del_init(&core->child_node)` | 从父的 children 或根/孤儿链摘掉 |
| 8 | `:4466-4472` | `prepare_count` / `protect_count` 非零就 `pr_warn` | 只警告，**不阻止** |
| 9 | `:4475` | `kref_put(&core->ref, __clk_release)`（`:4182`） | 最后一个引用没了才真释放 |

**第 3 步的 ops 掉包是这一整段的核心设计**。`clk_nodrv_ops`（`:4381`）是一张"什么都拒绝"的 ops 表。掉包的意思是：**已经拿到句柄的消费者不会崩，只会开始拿到错误码**。这比"释放内存让消费者踩野指针"温和得多，也比"拒绝注销"实用得多。持 `enable_lock` 改是因为原子上下文里的 `clk_enable` 也会读这个指针。

**第 5 步是最反直觉的**：注销一个中间节点，它的子节点**不会**被注销，而是变成孤儿。所以一个厂商 CRU 驱动 `rmmod` 之后，依赖它的时钟节点全都还在，只是 rate 变得不可信。第 6 步的 evict 保证了这些孤儿之后如果同名节点重新注册，能重新解析上去（§B4）。

第 8 步只警告不阻止，是 clk 里"注销永远成功"这条原则的体现。代价是**提供方模块可以在消费者还 prepare 着的时候被卸掉**——防线是 §B5 的 `module_get`，而不是这里。

---

# Part B：横切关注点

## §B1 两把锁的重入机制

02 §7.2 已经给了机制和代码。这里补 Part A 里散落各处的**实际后果**：

| 现象 | 根源 |
|---|---|
| `__clk_core_init` 里能直接调 `clk_core_prepare`（`:3937`）而不用先放锁 | 重入 |
| 通知回调在持 `prepare_lock` 的上下文里跑 | 重入让它不会死锁——但**语义上**仍然禁止在回调里改频率（§B2） |
| `clk_change_rate` 递归几十层不会锁爆栈 | 重入只加计数 |
| 提供方的 `.set_rate` 里可以调 `clk_hw_get_rate` 等查询 API | 重入 |

**重入的代价**：`lockdep` 看不见真正的嵌套关系。所以 clk 里几乎每个内部函数开头都手写一句 `lockdep_assert_held(&prepare_lock)`（如 `:983`、`:1124`）——那是补回 lockdep 丢掉的那部分检查。

**第三把锁 `of_clk_mutex`**（`:4814`）和上面两把没有嵌套约束，因为它保护的是 DT provider 链表而不是时钟树。但 `clk_core_get`（`:473`）里会在**已持 `prepare_lock`** 的情况下去拿 `of_clk_mutex`——所以顺序是 `prepare_lock` → `of_clk_mutex`，反过来会死锁。

## §B2 通知链

`clk_notifier_register`（`:4665`）的实现有一个容易看漏的层次：

```
clk_notifier_list                      全局链表（clk.c:38）
   └─ struct clk_notifier              一个 clk 一个（不是一个 clk_core 一个！）
        ├─ clk        ← per-consumer 的那个 struct clk
        └─ notifier_head              SRCU 通知链
```

**`cn->clk` 存的是 `struct clk *` 而不是 `clk_core *`**（`:4677-4678` 的比较、`:4685` 的赋值）。所以**两个消费者对同一个时钟注册 notifier，会产生两个独立的 `clk_notifier`**——它们各自有自己的 SRCU 链。而 `__clk_notify`（`:1761`）广播时是按 `clk_core` 匹配的，所以两条链都会被通知到。这个设计的实际效果是"注册和注销天然按消费者隔离"。

`notifier_count`（`clk_core` 的字段，`:4693` 处 `++`）是给 `clk_core_set_parent_nolock` 用的：**有 notifier 挂着的节点不允许换父**（因为换父会让通知里报的 old_rate/new_rate 失去意义）。

用的是 **SRCU** 而不是普通 notifier chain，所以 `COMMON_CLK` 在 Kconfig 里 `select SRCU`（01 §0.6）。选 SRCU 的原因：通知回调可能睡眠（它在 `prepare_lock` 保护下跑，而那是 mutex）。

通知的三种消息构成一个**三态协议**，缺一不可：

| 消息 | 何时发 | 回调能否否决 |
|---|---|---|
| `PRE_RATE_CHANGE` | 第 2 段，改之前 | ✅ 返回 `NOTIFY_BAD` → 整个 set_rate 得到 `-EBUSY` |
| `ABORT_RATE_CHANGE` | 有人否决后补发（`:2467`） | ❌ 返回值被忽略 |
| `POST_RATE_CHANGE` | 第 3 段，改完（`:2384`） | ❌ `:1889` 的注释明说「ignore NOTIFY_STOP and NOTIFY_BAD」 |

## §B3 缓存一致性：四个被缓存的属性

`clk_core` 里 `rate` / `accuracy` / `phase` / `duty` 都是**缓存**，不是每次都问硬件。维护点：

| 属性 | 谁刷新 | 强制不缓存的标志 |
|---|---|---|
| rate | `__clk_recalc_rates`（`:1870`），在 set_rate 第 3 段（`:2373`）、换父后（`:2743`/`:2829`）、孤儿修复（`:3759`）被调 | `CLK_GET_RATE_NOCACHE` |
| accuracy | `__clk_recalc_accuracies`（`:1793`），写值在 `:1804-1807` | `CLK_GET_ACCURACY_NOCACHE` |
| phase | `clk_core_get_phase`（`:2959`）写 `core->phase`（`:2970`） | 无 |
| duty | `clk_core_update_duty_cycle_nolock`，`memcpy` 进 `core->duty`（`:3066`），或从父继承（`:3038`/`:3081`） | 无 |

**phase 和 duty 没有 NOCACHE 逃生门**。如果固件或另一个核在背后改了相位，Linux 侧读到的永远是上一次刷新的值，而且**没有 API 能强制刷新**。这是一处真实的能力缺口，07 §3 把 debugfs 的 `clk_phase` 标注成"可能过时"就是因为它直接读这个缓存字段（`:3608`）。

`__clk_recalc_rates` 的第二个参数 `update_req`（`:1870`）决定要不要连 `req_rate` 一起更新，第三个参数是"顺便发哪个通知"（传 0 表示不发）。`:1886` 是 `req_rate` 被跟着刷的地方。

## §B4 孤儿修复：迭代收敛的第二处

`clk_core_reparent_orphans_nolock`（`:3730`）。**每注册一个节点就跑一遍**（§A3.7），另一处调用点是 `clk_core_reparent_orphans`（`:4783`，带锁版本）。

```c
hlist_for_each_entry_safe(orphan, tmp2, &clk_orphan_list, child_node) {
        struct clk_core *parent = __clk_init_parent(orphan);
        if (parent) {
                __clk_set_parent_before(orphan, parent);      :3750
                __clk_set_parent_after(orphan, parent, NULL); :3751
                __clk_recalc_accuracies(orphan);              :3752
                __clk_recalc_rates(orphan, true, 0);          :3753
                orphan->req_rate = orphan->rate;              :3765
        }
}
```

**为什么用 before/after 对而不是直接改指针**：`:3743-3747` 的注释给了答案——要**迁移孤儿身上已有的 prepare/enable 计数**。关键场景是 `CLK_IS_CRITICAL` 的孤儿：它在 §A3.6 里已经被核心 prepare+enable 了一次，但那时候没有父。现在找到父了，必须把这份"已开启"的状态沿新父向上传播，否则父的 `enable_count` 会少算一次，`clk_disable_unused` 就会把父关掉——而子还在跑。

`:3765` 的 `orphan->req_rate = orphan->rate` 有一段五行注释（`:3755-3764`）专门解释：孤儿注册时如果没有 `.recalc_rate`，`req_rate` 会是 **0**。而 `clk_set_rate_range` 和 `clk_put` 都会拿 `req_rate` 去触发一次 `clk_set_rate`——`req_rate` 是 0 的话就会把频率**降到 0**。这一行是补这个洞的。这是"缓存字段的初值也是语义"的典型例子。

修复是**单向的**：孤儿找到父就脱离孤儿链，但**已经上树的节点不会因为出现了"更合适的父"而重新挂**。所以 DT 里父子写反了不会被自动纠正。

注意 `__clk_recalc_rates(orphan, true, 0)`（`:3753`）**不发通知**（第三参 0）。理由：孤儿脱离孤儿链之前 rate 本来就是不可信的，没有"变化"可以通知。

## §B5 三层引用计数

clk 里有**三套**互不相干的计数，混起来看就全乱了：

| 计数 | 字段 | 谁加谁减 | 保护什么 |
|---|---|---|---|
| **对象生命周期** | `clk_core->ref`（kref） | `kref_init`（`:4217`）/ `kref_put`（`:4475`） | `clk_core` 这块内存 |
| **模块** | `core->owner` 的 module refcnt | `clk_hw_create_clk` 里 `try_module_get` / `__clk_put`（`:4608`）里 `module_put` | 提供方模块不被 rmmod |
| **硬件状态** | `prepare_count` / `enable_count` / `protect_count` | 消费者的 prepare/enable/exclusive_get 配对调用 | 时钟不被误关 |

**`__clk_release`（`:4182`）只在 kref 归零时跑**，而 kref 的持有者只有两个：注册时的那 1（`:4217`）和 `clk_unregister` 的那次 put（`:4475`）。**消费者的 `clk_get` 不加 kref**——它加的是 module refcnt。所以：

| 场景 | 结果 |
|---|---|
| 提供方是内置的（`built-in`），消费者持有句柄 | module refcnt 无意义，`clk_core` 由注册那 1 撑着，永远不释放 |
| 提供方是模块，消费者持有句柄 | `rmmod` 失败（module refcnt > 0），`clk_core` 安全 |
| 提供方是模块，无人持有句柄但有人 prepare 着 | `rmmod` **能成功**（module refcnt 是 0），走 §A9.2 的 ops 掉包路径，只 `pr_warn` |

最后一行就是 §A9.2 说的"防线是 module_get 而不是 `pr_warn`"的意思——而这条防线只在**有句柄**时才生效。

## §B6 suspend / resume：`save_context` / `restore_context`

`clk_save_context`（`:1283`）/ `clk_restore_context`（`:1311`）。

两者都遍历 `clk_root_list` **和** `clk_orphan_list`（`:1288`/`:1294` 和 `:1314`/`:1317`），递归调子树的 `.save_context` / `.restore_context`。

**它们不在 clk 核心的任何自动路径上**：全树 grep 显示调用者是各平台的 PM 代码，clk 自己一次都不调。所以一个提供方实现了 `.save_context` 但平台 PM 代码没调 `clk_save_context`，这两个回调就是死代码。01 §1.9 已经标了这一点。

保存方向有返回值检查（`ret < 0` 就中止整个遍历），恢复方向**没有**（返回 void）——恢复失败无处可报。

## §B7 runtime PM 交互：为什么有第四条链表

02 §7.2 提到 `clk_rpm_list` 存在的唯一原因是防死锁。这里给完整因果：

`:130-140` 的 kerneldoc 描述的死锁：

```
CPU A: 某设备正在 runtime resume
         → 它的 resume 回调调 clk_prepare_enable()
         → 等 prepare_lock

CPU B: clk_disable_unused()
         → 持 prepare_lock
         → clk_disable_unused_subtree() 需要 pm_runtime_get 提供方设备
         → 等那个设备 resume 完成
                                          ← 死锁
```

解法：`clk_disable_unused` 在**拿 `prepare_lock` 之前**先把所有 `rpm_enabled` 的时钟提供方设备全部 `pm_runtime_get` 一遍（`:1473` 的 `clk_pm_runtime_get_all()`），做完再 `clk_pm_runtime_put_all()`（`:1495`）。`clk_rpm_list`（`:41`）就是"哪些提供方启用了 runtime PM"的登记表，`:208` 是节点入链的地方。

`clk_pm_runtime_get_all`（`:145`）的 Context 注释说它**成功时带着 `clk_rpm_list_lock` 返回**——一个"加锁函数"和"业务函数"合体的写法，读的时候容易忽略。`:152` 的注释说明了为什么要一直持着：防止遍历期间有新的提供方注册或注销。

同样的 get_all/put_all 模式在 debugfs 的两处遍历里也用了（`:3277`、`:3345`）——`clk_summary` 和 `clk_dump` 会真的去读硬件寄存器，所以也需要提供方设备是 resumed 的。

---

# Part C：设计评价

## §C1 三处关键取舍

### C1.1 为什么 `clk_core` 不放头文件

代价：提供方想加一个字段就得改核心；调试时没法从外部结构体访问。
收益：**提供方物理上不可能依赖核心的内部布局**。对照 `clk_hw`（3 个指针，`clk-provider.h:324-328`）——十年间核心结构改了无数次，`clk_hw` 没动过。

这个取舍的成本在 debugfs 上显形：所有内部状态只能通过 `clk.c` 自己实现的 debugfs 文件（`:3557` 起）暴露，不能靠通用工具 dump。

### C1.2 为什么把 prepare 和 enable 分开而不是加一个"can sleep"参数

代价：所有消费者都要记住配对调两次，API 数量翻倍（`clk_prepare_enable` 这类合成 API 就是为了缓解）。
收益：**框架可以对两半用不同的锁**。如果合成一个 API，那把锁就必须是能睡的 mutex，`clk_enable` 就没法在原子上下文用了——而中断处理里开时钟是真实需求。

观察点：`clk_is_enabled_when_prepared`（`:1359-1362`）的存在说明这个分离**在很多硬件上是多余的**（没有 `.enable`/`.disable` 回调的节点，prepare 就等于全开）。API 保留分离是为了统一，不是因为每个节点都需要。

### C1.3 为什么孤儿是合法状态而不是注册失败

代价：`clk_get_rate` 可能返回一个**毫无根据的 0**，而**没有任何 API 能问出"这是孤儿"**——全树 grep 显示 `include/linux/clk.h` 和 `clk-provider.h` 里一个 orphan 相关的声明都没有，唯一的出口是 debugfs 的 `clk_orphan_summary`（`:3708`）和 `clk_orphan_dump`（`:3710`）。
收益：**提供方不需要按拓扑序注册**。对一个有 500 个时钟、父子关系跨越三个不同驱动的 SoC，这是唯一可行的做法。

这个取舍的成本正是 01 §3.5 那四个"看起来成功其实没根据"的读 API 的来源。

## §C2 源码里自陈的设计债

全树 grep `FIXME|TODO|REVISIT|XXX|HACK` 覆盖 `drivers/clk/clk.c`、`clkdev.c`、`clk-devres.c`、`include/linux/clk.h`、`include/linux/clk-provider.h`，共 **4 条**：

| # | 位置 | 内容 | 影响 |
|---|---|---|---|
| 1 | `drivers/clk/clk.c:2622-2629` | `FIXME`：`clk_set_rate_range` 改完边界后会 `clamp` 并重新 set_rate，但这一步**可能失败**——不只是"时钟坏了/被保护"这类正常原因，还可能因为 `round_rate` 落到了边界的错误一侧，或者 `determine_rate` 回调没考虑这个角落情况 | 直接影响 01 §1.4：**`clk_set_rate_range` 成功不代表频率落进了区间**。`:2632-2636` 有回滚，但回滚的是 min/max 字段，不是硬件频率 |
| 2 | `drivers/clk/clk.c:2720` | `TODO: Create a per-user clk and change callers to call clk_put` | 这就是 01 §1.5 那条「`clk_get_parent` 的返回值不要 `clk_put`」的根源：它返回的是**提供方内部那个 `hw->clk`**（`:2721`），不是给你的新句柄。put 它会破坏提供方自己的引用 |
| 3 | `drivers/clk/clk.c:4729` | `XXX the notifier code should handle this better` | 说的是 `clk_notifier_unregister` 里那段"最后一个 notifier 走了就手工 cleanup + kfree `clk_notifier`"的代码（`:4730-4734`）。属于通用 notifier 基础设施缺一个"链空了"的钩子，clk 自己补的 |
| 4 | `include/linux/clk-provider.h:1350-1352` | `FIXME clock api without lock protection`，紧跟着声明 `clk_hw_round_rate` | 意思是这个提供方 API **不自己加锁**，调用者必须已经持有 `prepare_lock`。名字上完全看不出来——01 §2.3 因此把它标成了"只能在 ops 回调内部调用" |

四条债的分布很说明问题：**两条（#1、#2）直接导致了消费者 API 的行为反直觉**，而且都在 01 里必须专门写 提醒。这不是文档写得啰嗦，是实现本身有裂缝。

## §C3 一句话评价

clk 的核心设计是**用"缓存 + 迭代收敛"换取"注册顺序自由"**：`clk_parent_map` 是缓存，孤儿链是收敛，`of_clk_init` 的重试是收敛。这个选择让 SoC 厂商能把几百个时钟拆到多个驱动里任意顺序注册，代价是**框架的每个读接口都可能返回一个"还没收敛完"的值**——而这些值在 API 层面和"真值"长得一模一样。

理解 clk 的关键因此不是记住 25 个回调，而是随时知道：**我手上这个数字，是硬件回读的，是继承来的，还是默认值？** 01 §3.5 的四个"静默成功"、§A3.5 的"精度 0 = 完美时钟"、§B4 的"`req_rate` 初值是 0"，全都是同一个问题的不同侧面。
