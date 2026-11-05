---
title: 数据结构
---

# 02 核心数据结构：六个结构体、两把锁、一张关系图

> **本篇的组织轴**：先破歧义（§1），再逐个结构体（§2），再画关系（§3–§5），最后是锁（§6）。
>
> **§1 是本篇最该先读的一节。** reset 框架只有六个结构体，字段总数不到 30 个 ——
> 难点从来不在「字段多」，而在**四组极其容易搞混的关系**。图画得再全，
> 也补不上一个没被显式否掉的错误类比。
>
> 格式约定：`core.c` = `drivers/reset/core.c`；`reset.h` = `include/linux/reset.h`；
> `reset-controller.h` = `include/linux/reset-controller.h`。arm64-only。
>
> **不在本篇的内容**：
>
> | 你想找 | 去哪 |
> |---|---|
> | 某个 API 的签名和错误码 | `01-api.md` |
> | 这些结构在时间线上什么时候被创建/销毁 | `03-internals.md` §A |
> | 四个动作在核心里逐行怎么走 | `03-internals.md` §A4 |
> | DTS 属性怎么变成 `rcdev->of_xlate` 的入参 | `06-devicetree.md` §4 |

---

## §1 先分清四组关系

### 1.1 `struct reset_control` **不是**「一条复位线」

**这是全框架第一号误解。** 名字叫 `reset_control`，很容易读成「一条复位控制线」，
于是推出「一条线只有一个 `reset_control`」。**错。**

正确的读法：**`reset_control` 是「某个消费者对某条复位线的一次引用」**。

| 问题 | 答案 | 依据 |
|---|---|---|
| 一条物理复位线对应几个 `reset_control`？ | **shared 时可以多个消费者共享同一个实例**；exclusive 时**最多一个已 acquire 的实例，外加任意多个未 acquire 的实例** | `core.c:756-771` 的查找逻辑 |
| 谁分配它？ | 核心，`kzalloc`（`core.c:774`） | — |
| 谁释放它？ | `kref` 归零时（`core.c:793-806`） | — |
| 它认识硬件吗？ | ❌ **只存一个 `id`（线号）+ 一个 `rcdev` 指针**，寄存器地址在控制器驱动私有结构里 | `core.c:42-52` |

关键在 `__reset_control_get_internal`（`core.c:749-791`）：**它先在
`rcdev->reset_control_head` 链表里找同 `id` 的已有实例**：

```c
	list_for_each_entry(rstc, &rcdev->reset_control_head, list) {
		if (rstc->id == index) {
			/*
			 * Allow creating a secondary exclusive reset_control
			 * that is initially not acquired for an already
			 * controlled reset line.
			 */
			if (!rstc->shared && !shared && !acquired)      /* :763 */
				break;                                  /* 跳出去新建一个 */

			if (WARN_ON(!rstc->shared || !shared))          /* :766 */
				return ERR_PTR(-EBUSY);                 /* :767 */

			kref_get(&rstc->refcnt);                        /* :769  复用同一个实例 */
			return rstc;
		}
	}
```

⇒ **三种结局，同一段代码**：

| 已有实例 | 你要求的 | 结局 | 行 |
|---|---|---|---|
| shared | shared | **共用同一个 `reset_control`**，`refcnt` 加一 | `:769-770` |
| exclusive | exclusive + `acquired=false`（即 `_released` 变体） | **新建第二个实例**，两个实例竞争 `acquired` 标志 | `:763-764` |
| exclusive | exclusive + `acquired=true` | `-EBUSY` + `WARN_ON` | `:766-767` |
| shared | exclusive（或反之） | `-EBUSY` + `WARN_ON` | `:766-767` |

所以「shared 是引用计数」这句话要拆成两层：
**上层是 `refcnt`（几个消费者拿着句柄），下层是 `deassert_count`（几个消费者要求解除复位）**——
两个计数器完全独立。

### 1.2 `reset_controller_dev` 和 `reset_control` **不是** device 和 driver

一个非常自然但错误的类比：「`reset_controller_dev` 里有 `dev`，
`reset_control_ops` 里有回调 ⇒ 这就是 device/driver 那一对」。

三条否证：

| 检查项 | 结果 | 判据 |
|---|---|---|
| reset 有自己的 `bus_type` 吗 | ❌ **零** | `/usr/bin/grep -rn 'bus_type' drivers/reset/ include/linux/reset*.h` → 零输出 |
| `reset_controller_dev` 是 `struct device` 吗 | ❌ **不是**，它只**内嵌一个 `struct device *` 指针**（`reset-controller.h:75`），而且**这个指针可以是 NULL** | `core.c:66-75` 的 `rcdev_name()` 里有 `if (rcdev->dev)` 兜底 |
| `reset_control` 会被 match / probe 绑定吗 | ❌ 没有 match、没有 probe。全局状态只有**两个链表 + 两把锁**（§6） | `core.c:20-24` |

⇒ **reset 是典型的框架型子系统**（判定过程记在 `99-evidence.md` §1.3）：
控制器主动 `register` 进一个全局链表，消费者靠 API 去查。
和 clk / pinctrl / mailbox 完全同形；和 i2c / spi（有自己的 `bus_type`）完全不同形。

那 `rcdev->dev` 有什么用？只有**两个**用途：

1. `rcdev_name()`（`core.c:66-75`）取名字，用于 `WARN` 消息（`core.c:479-480` / `:549-550`）。
2. **lookup 路径靠它匹配**：`__reset_controller_by_name` 用
   `dev_name(rcdev->dev)` 和 `lookup->provider` 比字符串（`core.c:889-893`）——
   ⇒ **`dev` 为 NULL 的控制器无法被 lookup 表引用**（`:889-890` 直接 `continue`）。
   DT 平台不受影响（DT 路径比的是 `of_node`，`core.c:848`）。

### 1.3 `shared` **不是** 「exclusive + released」的简写

这两套机制解决的是**两个不同的问题**，语义正交：

| | `shared` | `exclusive` + `acquire`/`release` |
|---|---|---|
| 解决的问题 | 「多个外设挂在同一条复位线上，谁都不能擅自把线拉低」 | 「同一条线要被多个驱动**轮流**独占操作」 |
| `reset_control` 实例 | **一个**，大家共用（`refcnt` 计数） | **多个**，每个消费者一个 |
| 靠什么协调 | `deassert_count` / `triggered_count`（原子计数） | `acquired` 布尔标志 + 遍历同 `rcdev` 的链表 |
| 冲突时 | 不冲突（计数合并） | `-EBUSY`（`core.c:664`） |
| 混用检查 | 有四道（`01-api.md` §3.1） | ❌ 一道都没有 |
| 典型使用者 | 多个共用一路 reset 的外设 | 需要在运行期把复位线交给别的子系统的场合 |

**代码层面的铁证**：`shared` 和 `acquired` 这两个字段在动作路径上
**永远走不同分支**（`core.c:343-352` / `:453-483` / `:541-553`）——
`if (rstc->shared) { …计数… } else { …查 acquired… }`。
⇒ **shared 句柄的 `acquired` 字段从头到尾没人读过**，
这也解释了为什么 `reset_control_get_shared` 传的 `acquired` 恒为 `false`
（`01-api.md` §1.2）—— 那个值根本不重要。

而核心还是拦了「shared + acquired」这个组合（`core.c:946-947` 的
`WARN_ON(shared && acquired)`）—— 拦的是**语义混乱**，不是**功能冲突**。

### 1.4 `_bulk_` **不是** `_array_`

名字都是「一批」，实现完全不同。**判据是「返回几个句柄」**：

```
_bulk_  ：你给 N 个名字 → 拿回 N 个独立的 reset_control *
          （存在你自己的 reset_control_bulk_data[] 里）

_array_ ：你什么都不给 → 拿回 1 个 reset_control *
          （它其实是 struct reset_control_array 的第一个成员）
```

array 的实现手法值得记一下 —— **结构体首成员伪装**：

```c
/* drivers/reset/core.c:60-64 */
struct reset_control_array {
	struct reset_control base;        /* 首成员，用来冒充普通句柄 */
	unsigned int num_rstcs;
	struct reset_control *rstc[];     /* 柔性数组 */
};
```

⇒ 核心把 `&array->base` 交给消费者，消费者当普通句柄用；
四个动作函数各自在开头 `if (reset_control_is_array(rstc))` 分流
（`core.c:337` / `:450` / `:538` / `:610`），靠的是 `base.array == true` 这个标志
（`core.c:309-312` 的 `reset_control_is_array`）。

这就是为什么**数组句柄能直接喂给 `reset_control_assert`**，
也是为什么 **`reset_control_status` 必须显式拒绝它**（`core.c:610`）——
「N 条线的状态」没法压成一个返回值。

---

## §2 六个结构体

### 2.1 `struct reset_control` —— 消费者句柄（核心私有）

**定义在 `drivers/reset/core.c:42-52`，不在任何头文件里** ⇒ 对消费者和控制器驱动
**都是不透明类型**。`reset.h:11` 只有一句前向声明 `struct reset_control;`。

| 字段 | 类型 | 作用 | 备注 |
|---|---|---|---|
| `rcdev` | `struct reset_controller_dev *` | 属于哪个控制器 | 动作函数靠它找到 `ops` |
| `list` | `struct list_head` | 挂进 `rcdev->reset_control_head` | 被 `reset_list_mutex` 保护（`core.c:870` 的注释明说） |
| `id` | `unsigned int` | **线号**，由 `of_xlate` 从 DTS cell 翻译出来 | 传给 `ops` 的每个回调 |
| `refcnt` | `struct kref` | 有几个消费者拿着这个句柄 | shared 时 > 1；归零走 `__reset_control_release`（`core.c:793`） |
| `acquired` | `bool` | 独占操作权 | **只对 exclusive 有意义**（§1.3） |
| `shared` | `bool` | 语义开关 | 取句柄时定死，之后不可改 |
| `array` | `bool` | 是不是伪装的数组句柄 | 唯一的判据（§1.4） |
| `deassert_count` | `atomic_t` | shared：有几个消费者要求「别复位」 | 0→1 的 deassert 和 1→0 的 assert 才落硬件 |
| `triggered_count` | `atomic_t` | shared：脉冲已发过几次 | kernel-doc（`core.c:38-40`）：**值只会是 0 或 1** |

**注意没有的字段**：没有 `name`（名字只在 DTS 的 `reset-names` 里，取完就丢）、
没有消费者 `device` 指针、没有任何统计计数器。
⇒ 这三个「没有」直接导致 `07-debug.md` §5 的三条观测缺口：
**你无法从核心问出「这条线现在被谁拿着」**。

### 2.2 `struct reset_control_array`

见 §1.4 的代码块。三个字段，`base` 必须是首成员。
分配点在 `of_reset_control_array_get`（`core.c:1187` 的
`kzalloc(struct_size(resets, rstc, num), GFP_KERNEL)` 一带）。

### 2.3 `struct reset_controller_dev` —— 控制器要填的东西

定义在 `include/linux/reset-controller.h:69-80`，kernel-doc 在 `:54-68`。

| 字段 | 必填 | 作用 | 备注 |
|---|---|---|---|
| `ops` | **是** | 四个回调（§2.4） | 核心在动作路径上直接 `rstc->rcdev->ops->xxx` 解引用 |
| `owner` | **是** | `THIS_MODULE` | 取句柄时 `try_module_get(rcdev->owner)`（`core.c:778`）；填错/不填 ⇒ 控制器可以在有人用的时候被卸载 |
| `list` | 否（核心用） | 挂进 `reset_controller_list` | — |
| `reset_control_head` | 否（核心用） | 本控制器已发出的所有句柄 | `reset_controller_register` 里 `INIT_LIST_HEAD`（`core.c:107`）—— **驱动不要自己初始化** |
| `dev` | 二选一 | 控制器设备 | lookup 路径**必须**有它（§1.2）；DT 路径可以没有 |
| `of_node` | 二选一 | 控制器的 DT 节点 | **DT 路径的唯一匹配键**（`core.c:848` `args.np == r->of_node`） |
| `of_reset_n_cells` | 否 | 期望的 cell 个数 | 留 0 时表示期望 **0 个 cell**？—— 不是。见下面的 |
| `of_xlate` | 否 | cell → 线号 转换 | 留 NULL 时核心填 `of_reset_simple_xlate` |
| `nr_resets` | 实际上是 | 本控制器有几条线 | 只被默认 `of_xlate` 用来查上界（`core.c:90`）；**自定义 `of_xlate` 的驱动如果不自己查，这个字段就是死的** |

**`of_reset_n_cells` 的坑**：核心在注册时**只做一次条件补正**：

```c
/* drivers/reset/core.c:102-105 */
	if (!rcdev->of_xlate) {
		rcdev->of_reset_n_cells = 1;              /* 只在这一个条件下补 */
		rcdev->of_xlate = of_reset_simple_xlate;
	}
```

⇒ **「两个字段一起补，或者一个都不补」**：
只要驱动填了自定义 `of_xlate`，`of_reset_n_cells` 就**必须自己填**，
留 0 意味着「本控制器期望 0 个 cell」。而校验是无条件的：

```c
	if (WARN_ON(args.args_count != rcdev->of_reset_n_cells)) {   /* core.c:859 */
		rstc = ERR_PTR(-EINVAL);
```

⇒ **DTS 里 cell 个数和这个字段不一致 = 一条内核栈回溯 + `-EINVAL`**，
而不是安静的失败。这是本框架**唯一**能在 dmesg 里明确看到「DTS 写错了」的地方
（`06-devicetree.md` §7.1）。

**典型 bug 形态**：控制器驱动为了支持 `<bank offset>` 两个 cell 写了自定义
`of_xlate`，却忘了 `of_reset_n_cells = 2` —— 于是每一次消费者取句柄都是
`WARN` + `-EINVAL`，而 DTS 和绑定文档都是对的。
排查方向记在 `07-debug.md` §4。

### 2.4 `struct reset_control_ops` —— 四个回调

```c
/* include/linux/reset-controller.h:18-23 */
struct reset_control_ops {
	int (*reset)(struct reset_controller_dev *rcdev, unsigned long id);
	int (*assert)(struct reset_controller_dev *rcdev, unsigned long id);
	int (*deassert)(struct reset_controller_dev *rcdev, unsigned long id);
	int (*status)(struct reset_controller_dev *rcdev, unsigned long id);
};
```

| 回调 | 语义 | 不实现的后果 | 备注 |
|---|---|---|---|
| `.reset` | 发一个脉冲 | `-ENOTSUPP` | 自清零硬件 |
| `.assert` | 拉进复位 | exclusive `-ENOTSUPP`；shared **静默 0** | `core.c:467-468` vs `:475-476` |
| `.deassert` | 解除复位 | **返回 0（成功）** | `core.c:555-563` 有整段注释解释 |
| `.status` | 查状态 | `-ENOTSUPP` | 返回**正数**表示正在复位 |

三件必须记住的事：

1. **`id` 的类型是 `unsigned long`**，但 `reset_control->id` 是 `unsigned int`
   （`core.c:45`），而 `of_xlate` 返回的是 `int`（`reset-controller.h:78`）。
   ⇒ 三处类型不一致，**负值通过 `int` 返回值传递错误**（`core.c:865-868`），
   到了 `id` 就不可能是负数了。
2. **回调里不能调用任何 `reset_control_*` API**：`of_xlate` 是在
   `reset_list_mutex` 里被调的（`core.c:845` 加锁、`:864` 调用），会死锁。
   四个动作回调虽然在锁外（`core.c:485` 等），但**同样不该递归进框架**。
3. **`reset.rst:161` 一句 `All functions are optional.`** ——
   四个全空的控制器可以注册成功（`01-api.md` §5.2）。

### 2.5 `struct reset_control_lookup` —— 非 DT 平台的查表项

定义在 `include/linux/reset-controller.h:38-44`，配套宏在 `:46-52`：

| 字段 | 作用 | 备注 |
|---|---|---|
| `list` | 挂进全局 `reset_lookup_list` | 被 `reset_lookup_mutex` 保护 |
| `provider` | 控制器的**设备名字符串** | 和 `dev_name(rcdev->dev)` 比（`core.c:889-893`）⇒ §1.2 那条限制 |
| `index` | 线号 | 直接进 `reset_control->id`，**不经过 `of_xlate`** |
| `dev_id` | 消费者的设备名 | 和 `dev_name(dev)` 比（`core.c:911`） |
| `con_id` | 消费者侧的名字 | 允许 NULL；匹配逻辑见 `core.c:914-916` |

`RESET_LOOKUP(_provider, _index, _dev_id, _con_id)` 宏（`reset-controller.h:46-52`）
只是个指定初始化器，用来写静态数组，再由板级代码调
`reset_controller_add_lookup`（`core.c:172-191`）一次性注册。

**arm64 上基本用不到这条路**：整棵树里 `reset_controller_add_lookup`
的调用者屈指可数，且都不在 arm64 平台（普查记在 `99-evidence.md` §3.2）。
本文档集把它当**理解 `__reset_control_get` 那个二分岔的必要背景**来讲
（`core.c:949-954`：有 `dev->of_node` 走 DT，没有走 lookup），而不是当推荐用法。

### 2.6 两个「批量」辅助结构

**(a) `struct reset_control_bulk_data`** —— 消费者自己声明的数组元素：

```c
/* include/linux/reset.h:23-26 */
struct reset_control_bulk_data {
	const char			*id;    /* 你填：reset-names 里的名字 */
	struct reset_control		*rstc;  /* 核心填：拿到的句柄 */
};
```

**一进一出共用一个结构**是它的全部设计：你填 `id[]`，核心回填 `rstc[]`。
所以**同一个数组不能给两次不同的 get 调用复用**（第二次会覆盖第一次的句柄，
造成句柄泄漏）。kernel-doc 在 `include/linux/reset.h:13-22`。

**(b) `struct reset_control_bulk_devres`** —— devm 版本的私有记账：

```c
/* drivers/reset/core.c:1058-1061 */
struct reset_control_bulk_devres {
	int num_rstcs;
	struct reset_control_bulk_data *rstcs;   /* 存的是消费者数组的指针 */
};
```

**它只存指针，不拷贝内容** ⇒ **消费者的 `reset_control_bulk_data[]`
必须活得比设备久**（不能是栈上局部变量！）。
`devm_reset_control_bulk_release`（`core.c:1063-1068`）在设备移除时
顺着这个指针回去逐个 put —— 数组已经消失的话就是 use-after-free。
这是本框架**最容易写错又最难查**的一处，`05-usage.md` §3.2 的骨架里
特意把数组声明成了驱动私有结构的成员。

顺带记一个正确的 devres 顺序（`core.c:1088-1090`）：**先填好 `ptr` 的两个字段，
最后才 `devres_add`**。反过来写就有窗口期让释放路径读到半初始化的记账项。

---

## §3 结构关系图

```
                        全局（core.c 文件作用域）
   ┌──────────────────────────────────────────────────────────────────┐
   │  reset_list_mutex ──保护──> reset_controller_list  (:20-21)      │
   │                    └─也保护──> 每个 rcdev 的 reset_control_head   │
   │                                （注释在 core.c:870）             │
   │  reset_lookup_mutex ──保护──> reset_lookup_list    (:23-24)      │
   └──────────────────────────────────────────────────────────────────┘
              │                                        │
              │ list                                   │ list
              v                                        v
   ┌────────────────────────────┐         ┌──────────────────────────┐
   │ struct reset_controller_dev│         │ struct reset_control_    │
   │  (reset-controller.h:69-80)│         │        lookup            │
   │                            │         │ (reset-controller.h:38)  │
   │  ops ─────────────┐        │         │  provider ── 字符串比 ──┐ │
   │  owner (THIS_MODULE)       │         │  index                 │ │
   │  dev / of_node  <──────────┼─────────┼────────────────────────┘ │
   │  of_reset_n_cells          │  匹配键  │  dev_id / con_id         │
   │  of_xlate ────────┐        │         └──────────────────────────┘
   │  nr_resets        │        │
   │  reset_control_head        │
   │        │          │        │
   └────────┼──────────┼────────┘
            │          │              ┌────────────────────────────┐
            │          └─────────────>│ struct reset_control_ops   │
            │                         │ (reset-controller.h:18-23) │
            │ list (每个句柄一项)      │  .reset  .assert           │
            v                         │  .deassert  .status        │
   ┌────────────────────────────┐     │  四个全可选               │
   │ struct reset_control       │     └──────────────┬─────────────┘
   │  (core.c:42-52  核心私有)  │                    │
   │  rcdev ────────────────────┼────> 回到上面的 rcdev│
   │  id (线号)                  │                    v
   │  refcnt (kref)             │            控制器驱动私有结构
   │  shared / acquired / array │            （regmap / iomem / 位偏移）
   │  deassert_count  (atomic)  │                    │
   │  triggered_count (atomic)  │                    v
   └──────────┬─────────────────┘                  寄存器
              │
              │ 当 array == true 时，它其实是下面这个结构的首成员
              v
   ┌─────────────────────────────────────────┐
   │ struct reset_control_array (core.c:60)  │
   │  base  ← 就是上面那个 reset_control      │
   │  num_rstcs                              │
   │  rstc[] ──> reset_control * ──> ... ──┐  │
   └───────────────────────────────────────┼──┘
                                           └─> 每个都是一个真正的句柄
                                               （各自挂在 rcdev 链表上）
```

**图上最该注意的一条**：`reset_control_array` 的 `base`
**除了 `array = true` 之外全是零**（`core.c:1185` 的 `kzalloc` +
`:1196-1197` 只赋了 `num_rstcs` 和 `base.array`）——
**`base.rcdev` 是 NULL**，`base.refcnt` 从没 `kref_init` 过。

⇒ 这解释了三件事：

| 现象 | 原因 |
|---|---|
| 四个动作函数都在**第一行**就查 `array` | 查完才敢碰 `rstc->rcdev`（`core.c:337` / `:450` / `:538` / `:610`） |
| `reset_control_status` 干脆拒绝数组 | 它没有「先分流再合并」的写法可选（`core.c:610`） |
| `reset_control_put` 要特殊分支 | 数组句柄不能走 `kref_put`，得走 `reset_control_array_put`（`core.c:984-993`） |

---

## §4 核心抽象 ↔ 消费者：框架型子系统的连接方式

reset 没有自己的 `bus_type`（§1.2），所以这里画的不是「两条总线」，
而是**「谁在 platform 总线上、谁只是 API 调用」**：

```
   platform 总线                            reset 框架（无总线，只有链表）
   ─────────────                            ──────────────────────────────

   ┌─────────────────────┐
   │ 复位控制器的 DT 节点  │  of_platform_populate
   │ (compatible=...)    │ ──────────────────┐
   └─────────────────────┘                   │
                                             v
                                  ┌─────────────────────────┐
                                  │ platform_device         │
                                  │  + platform_driver      │
                                  │      .probe()           │
                                  └───────────┬─────────────┘
                                              │ 唯一的入口动作：
                                              │   devm_reset_controller_register()
                                              v
                                  ┌──────────────────────────────┐
                                  │  reset_controller_list       │
                                  │   ← 全局链表，就是「总线」的  │
                                  │     全部替代品               │
                                  └──────────────┬───────────────┘
                                                 │ 被查找
   ┌─────────────────────┐                       │
   │ 消费者的 DT 节点      │                      │
   │  resets = <&rst 5>; │                       │
   │  reset-names = "a"; │                       │
   └──────────┬──────────┘                       │
              │ of_platform_populate             │
              v                                  │
   ┌─────────────────────┐   devm_reset_control_get_exclusive()
   │ 消费者 platform_dev  │ ───────────────────────┘
   │  （也可能是 i2c/spi/ │        这一步只是函数调用，
   │    pci 设备，无所谓） │          不产生任何 device / driver 绑定
   └─────────────────────┘
```

**三条从图里读出来的结论**：

1. **消费者是什么总线上的设备，reset 框架完全不关心** ——
   `__reset_control_get` 只要求一个 `struct device *`，且只用它的
   `of_node`（DT 路径）或 `dev_name`（lookup 路径）。
   ⇒ i2c 设备、spi 设备、PCI 设备都能取 reset 句柄。
2. **控制器和消费者之间没有父子关系**，
   连接完全靠 DTS 里那个 phandle（`core.c:838-839` 的
   `of_parse_phandle_with_args`）。这和 i2c「控制器是父设备」完全不同。
3. **注册次序不确定是常态**：消费者 probe 时控制器可能还没注册，
   核心的答案就是 `-EPROBE_DEFER`（`core.c:855`）——
   **这是本框架和 driver model 之间唯一的耦合点**。
   官方说法在 `Documentation/driver-api/reset.rst:166-167`：控制器
   「在 probe 函数里」注册，也就是说框架从设计上就接受了这个次序不确定性。

一个常见的架构疑问：**复位控制器常常和时钟控制器是同一个硬件模块**
（一个 CRU / CCU 寄存器块既管时钟又管复位）。这时的做法是**一个
platform_driver 里注册两次**：`devm_clk_hw_register` 系列 + 
`devm_reset_controller_register`。两个框架各挂自己的链表，互不知道对方存在。
实例见 `04-extensions.md` §6。

---

## §5 核心操作数据流：一次 `reset_control_assert`

```
消费者                     核心 (core.c)                控制器驱动         硬件
  │
  │ reset_control_assert(rstc)
  ├──────────────────────────>│ :442
  │                           │
  │                     ① if (!rstc) return 0;        ← :444-445 NULL 合法
  │                     ② if (WARN_ON(IS_ERR(rstc)))  ← :447-448
  │                          return -EINVAL;
  │                     ③ if (reset_control_is_array) ← :450
  │                          → 逐个递归，见 :245-261
  │                     ④ if (rstc->shared) {         ← :453
  │                            if (WARN_ON(atomic_read(
  │                              &rstc->triggered_count)))
  │                              return -EINVAL;      ← :454-455
  │                            if (WARN_ON(atomic_read(
  │                              &rstc->deassert_count) == 0))
  │                              return -EINVAL;      ← :457-458
  │                            if (atomic_dec_return(
  │                              &rstc->deassert_count) != 0)
  │                              return 0;            ← :460-461 不落硬件！
  │                            if (!rstc->rcdev->ops->assert)
  │                              return 0;            ← :467-468 静默成功
  │                        } else {
  │                            if (!rstc->rcdev->ops->assert)
  │                              return -ENOTSUPP;    ← :475-476 同一情况，不同码
  │                            if (WARN(!rstc->acquired, ...))
  │                              return -EPERM;       ← :479-481
  │                        }
  │                     ⑤ return rstc->rcdev->ops->assert(
  │                            rstc->rcdev, rstc->id);  ← :485
  │                           ├──────────────────────────>│
  │                           │                          │ 例：reset-simple
  │                           │                          │ 自旋锁 + readl/writel
  │                           │                          ├──────────────>│
  │                           │<─────────────────────────┤   置位复位寄存器
  │<──────────────────────────┤ 0 或错误码
```

**这张图上最反直觉的一步是 ④ 里的 `:460-461`**：
shared 句柄上调 `assert`，**在 `deassert_count` 没减到 0 之前根本不碰硬件**，
而且**返回 0（成功）**。官方文档明说了这件事
（`reset.rst:99-104`：「no guarantee that calling reset_control_assert
on a shared reset control will actually cause the reset line to be asserted」）。

⇒ 对调试的含义：**「我调了 assert 但设备没复位」在 shared 语义下不是 bug**。
`07-debug.md` §3.1 把这个场景单列成一条分诊项，因为核心
**不打印任何日志**（全 core.c 只有一条 `pr_warn`，在 `core.c:183`，
和这条路径无关）。

另外三个动作的骨架一样，只是 ④ 的内容不同：

| 动作 | ④ 处的 shared 特有逻辑 | 落硬件的条件 |
|---|---|---|
| `reset` | `atomic_inc_return(&triggered_count) != 1` 就返回 0（`core.c:347-348`） | 第一个调用者 |
| `assert` | `atomic_dec_return(&deassert_count) != 0` 就返回 0（`:460-461`） | 最后一个调用者 |
| `deassert` | `atomic_inc_return(&deassert_count) != 1` 就返回 0（`:545-546`） | 第一个调用者 |
| `status` | 无 shared 特殊逻辑，直接透传（`:613-614`） | 总是落 |

**只有 `reset` 有失败回滚**：`ops->reset` 返回错误时把 `triggered_count`
减回去（`core.c:355-356`）。`assert` / `deassert` **没有对应的回滚** ——
底层回调失败后计数已经改了，语义上就和硬件不一致了。
这条不对称记进 `03-internals.md` §C1 的设计债清单。

还有两处「检查位置」的差别值得单独记，因为它决定了错误码：

| 检查 | 位置 | 影响 |
|---|---|---|
| `!ops->reset` | 在 shared 分岔**之前**（`core.c:340-341`） | ⇒ shared 和 exclusive **一样**返回 `-ENOTSUPP` |
| `!ops->assert` | 在分岔**里面**，两边各一份（`:467-468` / `:475-476`） | ⇒ 同一情况两种返回码（`01-api.md` §3.1） |
| `!ops->deassert` | 在分岔**之后**的公共路径（`:562-563`） | ⇒ 两种语义都返回 0 |

**注意 `status` 是四个动作里唯一不看 `shared`、也不看 `acquired` 的** ——
只读操作不需要协调。这也是它能被任何持有句柄的人随时调用的原因。

---

## §6 全局状态与两把锁

### 6.1 全部全局变量：四个，都在 `core.c:20-24`

```c
static DEFINE_MUTEX(reset_list_mutex);       /* :20 */
static LIST_HEAD(reset_controller_list);     /* :21 */

static DEFINE_MUTEX(reset_lookup_mutex);     /* :23 */
static LIST_HEAD(reset_lookup_list);         /* :24 */
```

**全部 `static`** ⇒ 整个框架的全局状态**外部完全不可见**：
没有导出符号、没有 debugfs、没有 sysfs。
⇒ 「系统里现在有哪些复位控制器」这个问题**在运行期无法回答**
（`07-debug.md` §5.1 把这条列为第一缺口，并给出 kprobe/drgn 的绕法）。

### 6.2 两把锁分别保护什么

| 锁 | 保护 | 加锁点 |
|---|---|---|
| `reset_list_mutex` | **两样东西**：全局 `reset_controller_list`，**以及每个 `rcdev` 的 `reset_control_head`** | 注册 `core.c:109-111`、注销 `:123-125`、acquire `:653-671`、DT 取句柄 `:845-874`、lookup 取句柄内层 `:917-929`、bulk get 回滚 `:976-979`、array put `:988-991`、put `:1009-1011`、bulk put `:1022-1025`、array get 回滚 `:1202-1205` |
| `reset_lookup_mutex` | 全局 `reset_lookup_list`（非 DT 平台的查表项） | `:178-190`、`:908-934`、`:1259-1266` |

「一把锁管两件事」不是疏忽，`core.c:870` 有一行注释明说：

```c
	/* reset_list_mutex also protects the rcdev's reset_control list */
```

⇒ 这就是为什么 `__reset_control_get_internal` / `__reset_control_put_internal` /
`__reset_control_release` / `__reset_controller_by_name` 四个内部函数
**开头都是 `lockdep_assert_held(&reset_list_mutex)`**
（`core.c:754` / `:808` / `:798` / `:886`）—— 调用者负责持锁，
这也是它们名字带 `__` 前缀的含义（§7）。

### 6.3 嵌套次序：只有一个方向

```
  reset_lookup_mutex        (core.c:908  进入 __reset_control_get_from_lookup)
        └── reset_list_mutex (core.c:917  在里面再取)
```

**反方向从不发生** —— 持 `reset_list_mutex` 的代码路径里
没有一处去取 `reset_lookup_mutex`（全 `core.c` 里
`reset_lookup_mutex` 只有 §6.2 表里那三处）。⇒ **结构上不可能 AB-BA 死锁**。

但对**控制器驱动**有一条硬约束：`rcdev->of_xlate` 是
**在持 `reset_list_mutex` 的情况下被调用的**（`:845` 加锁 → `:864` 调用）。
⇒ `of_xlate` 里调用任何 `reset_control_*` API = 自死锁。
（同理不能在里面等一个会去取句柄的 workqueue。）

### 6.4 acquire 加锁、release 不加锁

这是本框架锁设计上最值得注意的一处不对称：

```c
int reset_control_acquire(struct reset_control *rstc)
{
	...
	mutex_lock(&reset_list_mutex);                     /* :653 */
	if (rstc->acquired) { ...unlock; return 0; }        /* :655-658 */
	list_for_each_entry(rc, &rstc->rcdev->reset_control_head, list) {
		if (rstc != rc && rstc->id == rc->id) {     /* :661 */
			if (rc->acquired) { ...unlock;      /* :662 */
				return -EBUSY; }            /* :664 */
		}
	}
	rstc->acquired = true;                              /* :669 */
	mutex_unlock(&reset_list_mutex);                    /* :671 */
```

```c
void reset_control_release(struct reset_control *rstc)
{
	if (!rstc || WARN_ON(IS_ERR(rstc)))                 /* :718 */
		return;
	if (reset_control_is_array(rstc))
		reset_control_array_release(rstc_to_array(rstc));
	else
		rstc->acquired = false;                     /* :724 裸写，无锁 */
}
```

| 观察 | 说明 |
|---|---|
| acquire 要遍历同 `rcdev` 的链表 | 必须持锁（链表本身受 `reset_list_mutex` 保护） |
| release 只改自己的一个 `bool` | 作者据此省了锁 |
| 这构成 `rstc->acquired` 上的一次**无同步写** | 与 acquire 里 `:655` / `:662` 的读并发 |
| 实际风险 | 有限：每个句柄只有其持有者会写。**但 KCSAN 视角下这是一处 data race**，且「release 之后另一个 CPU 立刻 acquire」有理论上看不到新值的窗口 |
| `release` 检查「你本来 acquire 了吗」吗 | ❌ **不检查**（对比 assert/deassert 的 `WARN`）—— 重复 release 静默通过 |

⇒ 这条记进 `03-internals.md` §C1（设计债）和 `99-evidence.md` §5.3
（「推断非观察」：没有实机复现过这个竞态）。

---

## §7 命名约定速查

### 7.1 对外 API 的命名维度

完整的六维分解和 18 行参数映射表在 `01-api.md` §1 ——
**不要在本篇重复**。这里只留一句判据：

> 看到一个 `reset_control_get_*` 的长名字，从右往左拆：
> `_optional` → 拿不到返回 NULL；`_shared` → shared=true；
> `_released` → acquired=false；`_by_index` → 用序号不用名字；
> 前缀 `devm_` → 自动释放；前缀 `of_` → 直接给 `device_node` 而不是 `device`。

### 7.2 核心内部的命名约定

| 记号 | 含义 | 例子 |
|---|---|---|
| `__` 前缀 | **调用者必须已持 `reset_list_mutex`**，或者是被 inline 包装封装的真实现 | `__reset_control_get_internal`、`__reset_control_put_internal` |
| `_internal` 后缀 | 同上，且**不做参数合法性检查** | `core.c:749` / `:806` |
| `rstc` | 变量名恒指 `struct reset_control *` | 全文件统一 |
| `rcdev` | 变量名恒指 `struct reset_controller_dev *` | 全文件统一 |
| `rstc_to_array()` | `container_of` 包装，`base` → 外层 `reset_control_array` | `core.c:195-198` |
| `reset_control_array_*()` | **静态内部函数**，不是导出 API（容易和 `of_reset_control_array_get` 混） | `core.c:199-307` 六个 |

**一个必须记住的命名陷阱**：

| 名字 | 是什么 |
|---|---|
| `reset_control_array_assert` | **内部** static 函数（`core.c:245`），给数组句柄用 |
| `of_reset_control_array_get` | **导出** API（`core.c:1173`），获取数组句柄 |
| `reset_control_bulk_assert` | **导出** API（`core.c:499`），对 N 个独立句柄操作 |

⇒ 三个名字都含「array/bulk + 动作」，但一个是内部、一个是获取、一个是操作。

---

## §8 接下来看哪一篇

| 你现在的问题 | 去哪 |
|---|---|
| 这些结构在什么时刻被创建、谁先谁后 | `03-internals.md` §A（时间线） |
| `reset_list_mutex` 的每个临界区逐行 | `03-internals.md` §B1 |
| acquire/release 不对称、无回滚这些设计债 | `03-internals.md` §C1 |
| `reset-simple` 这类通用控制器实现怎么复用 | `04-extensions.md` |
| 我要写消费者/控制器代码 | `05-usage.md` |
| `resets` / `reset-names` / `#reset-cells` 怎么写 | `06-devicetree.md` |
| 「调了 assert 但设备没复位」怎么查 | `07-debug.md` §3.1 |
| shared 和 exclusive 到底该选哪个（大白话） | `08-feynman.md` §2 |
| 上面每个数字/行号是怎么来的 | `99-evidence.md` |
