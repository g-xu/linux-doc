---
title: 内部实现
---

# 03 实现剖析：一个 .c 文件的全部内幕

> **组织轴（只承诺这一条）**：
>
> - **Part A —— 时间线**：从控制器注册到句柄释放，按**发生次序**逐段读代码。
> - **Part B —— 横切机制**：锁、引用计数、错误传播、回滚。这些贯穿整条时间线，
>   单独抽出来讲，避免在 A 里反复插话。
> - **Part C —— 评价**：设计债、和别的框架对比、可观测性。
>
> 章节号自带前缀（`§A2` / `§B1` / `§C1`），所以别处引用时一眼能看出「在哪条轴上」。
>
> 格式约定：`core.c` = `drivers/reset/core.c`（**全框架唯一的核心 .c，1289 行**）；
> `reset.h` = `include/linux/reset.h`；`reset-controller.h` = `include/linux/reset-controller.h`。
> arm64-only。

---

## §0 导航

### 0.1 分层图

```
   ┌──────────────────────────────────────────────────────────────┐
   │ 消费者驱动（几千个 .c，遍布 drivers/ 各处）                    │
   │   devm_reset_control_get_exclusive() / _assert() / _deassert()│
   └───────────────────────────┬──────────────────────────────────┘
                               │ 41 个 static inline 包装
                               │   (include/linux/reset.h)
                               v
   ┌──────────────────────────────────────────────────────────────┐
   │ 核心：drivers/reset/core.c  —— 唯一一个文件                   │
   │                                                              │
   │  ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌────────────┐  │
   │  │ 注册/注销   │ │ 取/放句柄  │ │ 四个动作   │ │ acquire/   │  │
   │  │ §A1 §A7    │ │ §A2 §A6   │ │ §A4       │ │ release §A5│  │
   │  └────────────┘ └────────────┘ └───────────┘ └────────────┘  │
   │      两个链表 + 两把锁（§B1） · kref + module_get（§B2）       │
   └───────────────────────────┬──────────────────────────────────┘
                               │ 唯一的下行接口：4 个函数指针
                               │   (struct reset_control_ops)
                               v
   ┌──────────────────────────────────────────────────────────────┐
   │ 控制器驱动：drivers/reset/*.c（39 个，共 8132 行）+ 3 个子目录  │
   │   reset-simple / reset-scmi / reset-ti-syscon / SoC 专用…      │
   └───────────────────────────┬──────────────────────────────────┘
                               v
                       regmap / iomem / SCMI 消息
```

**注意这张图里没有「中间层」**：核心和控制器驱动之间只有四个函数指针，
核心和消费者之间只有一层 `static inline`。**没有 class、没有 bus、没有 device 模型参与**
—— 这是它只用 1289 行就实现完整框架的原因，也是它什么都观测不到的原因（§C3）。

### 0.2 生命周期时间线

```
 时刻                动作                                   本篇章节
 ────────────────────────────────────────────────────────────────────
 t0   控制器驱动 probe
        └─ devm_reset_controller_register(dev, rcdev)         §A1
             ├─ of_xlate 为空则补默认值           core.c:102-105
             ├─ INIT_LIST_HEAD(&rcdev->reset_control_head)  :107
             └─ list_add(&rcdev->list, &reset_controller_list) :110
 ────────────────────────────────────────────────────────────────────
 t1   消费者驱动 probe
        └─ devm_reset_control_get_exclusive(dev, "core")      §A2
             ├─ devres_alloc                              :1040
             ├─ __reset_control_get → DT 或 lookup 二分岔    :949
             │    ├─ of_property_match_string("reset-names")  :830
             │    ├─ of_parse_phandle_with_args("resets")     :838
             │    ├─ 遍历 reset_controller_list 比 of_node    :847
             │    │    └─ 没找到 → -EPROBE_DEFER :855
             │    ├─ rcdev->of_xlate() → 线号                 :864
             │    └─ __reset_control_get_internal → 复用或新建 :871  §A3
             └─ devres_add                                  :1052
 ────────────────────────────────────────────────────────────────────
 t2   消费者操作
        ├─ reset_control_assert / _deassert / _reset / _status §A4
        └─ reset_control_acquire / _release（仅 exclusive）    §A5
 ────────────────────────────────────────────────────────────────────
 t3   消费者 remove（或 probe 失败）
        └─ devres 自动 → devm_reset_control_release → put     §A6
             └─ kref_put → 归零才 list_del + kfree + module_put
 ────────────────────────────────────────────────────────────────────
 t4   控制器驱动 remove
        └─ devres 自动 → reset_controller_unregister          §A7
             └─ 只 list_del，不检查还有没有人在用
```

### 0.3 哪些内容不在本篇

| 你想找 | 去哪 |
|---|---|
| API 签名、错误码语义表 | `01-api.md` |
| 结构体字段逐个解释 | `02-datastructures.md` §2 |
| 四组容易混的关系（shared vs exclusive 等） | `02-datastructures.md` §1 |
| `reset-simple` 这类通用控制器实现 | `04-extensions.md` |
| DTS 属性解析的**语法**层面 | `06-devicetree.md` §4 |
| 具体故障怎么查 | `07-debug.md` |

---

# Part A —— 时间线

## §A1 t0：控制器注册

### A1.1 `reset_controller_register` 全文只有 14 行

```c
/* drivers/reset/core.c:100-114 */
int reset_controller_register(struct reset_controller_dev *rcdev)
{
	if (!rcdev->of_xlate) {
		rcdev->of_reset_n_cells = 1;
		rcdev->of_xlate = of_reset_simple_xlate;
	}

	INIT_LIST_HEAD(&rcdev->reset_control_head);

	mutex_lock(&reset_list_mutex);
	list_add(&rcdev->list, &reset_controller_list);
	mutex_unlock(&reset_list_mutex);

	return 0;
}
```

逐段看：

| 行 | 动作 | 要注意的 |
|---|---|---|
| `:102-105` | `of_xlate` 为空 ⇒ 同时补 `of_reset_n_cells = 1` 和默认 xlate | **两个字段一起补**。填了自定义 `of_xlate` 就必须自己填 `of_reset_n_cells`（`02-datastructures.md` §2.3 的 bug 形态） |
| `:107` | 初始化本控制器的句柄链表 | 驱动不要自己 `INIT_LIST_HEAD`，重复初始化会丢已有表项（虽然此刻还是空的） |
| `:110` | `list_add`（头插） | **头插不是尾插** ⇒ 后注册的控制器排在前面。因为匹配用的是精确的 `of_node` 比较，次序不影响结果 |
| `:113` | **恒返回 0** | **没有任何入参校验**：`ops` 为 NULL、`of_node` 和 `dev` 都为 NULL 也能注册成功 |

**「恒返回 0」的后果**：控制器驱动里 `if (ret) return ret;` 那一句永远不触发；
真正的错误要等到**第一个消费者取句柄时**才以 `-EPROBE_DEFER` 或
`WARN + -EINVAL` 的形式出现，而且报错发生在**消费者的 probe 上下文里**。
⇒ 「reset 控制器写错了，但 dmesg 里怪的是别的驱动」是本框架的典型症状
（`07-debug.md` §4）。

### A1.2 `devm_reset_controller_register`：正确的 devres 顺序

```c
/* drivers/reset/core.c:143-164 */
	rcdevp = devres_alloc(devm_reset_controller_release, sizeof(*rcdevp),
			      GFP_KERNEL);          /* :149 */
	if (!rcdevp)
		return -ENOMEM;                       /* :152 */

	ret = reset_controller_register(rcdev);       /* :154 */
	if (ret) {
		devres_free(rcdevp);                  /* :156 注册失败就还回去 */
		return ret;
	}

	*rcdevp = rcdev;                              /* :160 先填 */
	devres_add(dev, rcdevp);                      /* :161 后挂 */
```

**`devres_alloc` 在 `register` 之前，`devres_add` 在 `register` 之后** ——
这个顺序保证了「已挂上 devres 的记账项一定指向一个已注册的控制器」。
反过来写（先 add 再 register）会有一个窗口期让 remove 路径注销一个没注册的控制器。

官方文档推荐用 devm 版本（`Documentation/driver-api/reset.rst:210-211`），
理由就在 §A7：手写 unregister 极容易漏在错误分支上。

### A1.3 `reset_controller_add_lookup`：唯一会打日志的地方

```c
/* drivers/reset/core.c:178-190 */
	mutex_lock(&reset_lookup_mutex);
	for (i = 0; i < num_entries; i++) {
		entry = &lookup[i];

		if (!entry->dev_id || !entry->provider) {
			pr_warn("%s(): reset lookup entry badly specified, skipping\n",
				__func__);
			continue;                     /* :185 跳过坏项，不失败 */
		}

		list_add_tail(&entry->list, &reset_lookup_list);   /* :188 */
	}
	mutex_unlock(&reset_lookup_mutex);
```

**`core.c:183` 这条 `pr_warn` 是整个 core.c 里唯一的一条日志语句。**
（普查命令记在 `99-evidence.md` §3.1。）而它只覆盖 lookup 表这条
**arm64 上基本用不到的路径** ⇒ 实际运行的 DT 路径**一条日志都没有**，
只有 `WARN` 系列。这是 §C3 的核心论据。

另外注意 `list_add_tail`（`:188`）和注册控制器用的 `list_add`（`:110`）方向相反：
lookup 表是**先注册的先匹配**，因为匹配是按字符串模糊比较的（`:911-916`），
次序会影响结果。

---

## §A2 t1：消费者取句柄

这是全框架最复杂的一段，四层调用：

```
  devm_reset_control_get_exclusive(dev, id)        ← reset.h 的 inline
    └─ __devm_reset_control_get(dev, id, 0, 0, 0, 1)     core.c:1034
         └─ __reset_control_get(dev, id, index, ...)      core.c:942
              ├─ [有 dev->of_node]  __of_reset_control_get()   core.c:816
              │                       └─ __reset_control_get_internal()  :749
              └─ [没有]             __reset_control_get_from_lookup()  core.c:899
                                      └─ __reset_control_get_internal()  :749
```

### A2.1 `__reset_control_get`：二分岔 + 一道断言

```c
/* drivers/reset/core.c:942-955 */
struct reset_control *
__reset_control_get(struct device *dev, const char *id, int index, bool shared,
		    bool optional, bool acquired)
{
	if (WARN_ON(shared && acquired))
		return ERR_PTR(-EINVAL);          /* :946-947 */

	if (dev->of_node)
		return __of_reset_control_get(dev->of_node, id, index, shared,
					      optional, acquired);   /* :950-951 */

	return __reset_control_get_from_lookup(dev, id, shared, optional,
					       acquired);          /* :953-954 */
}
```

**`dev->of_node` 是唯一的路径选择依据** —— 不看 `CONFIG_OF`、不看总线类型。
⇒ arm64 上只要设备来自 DT（几乎全部），走的永远是上面那条。

`WARN_ON(shared && acquired)` 的意义见 `02-datastructures.md` §1.3：拦语义混乱。
它也是「为什么没有 `reset_control_get_shared_released`」的直接原因（`01-api.md` §1.2）。

### A2.2 `__of_reset_control_get`：DT 路径逐段

```c
/* drivers/reset/core.c:816-878，按执行次序摘要 */
	if (!node)
		return ERR_PTR(-EINVAL);                              /* :826-827 */

	if (id) {
		index = of_property_match_string(node, "reset-names", id);  /* :830 */
		if (index == -EILSEQ)
			return ERR_PTR(index);                        /* :832-833 */
		if (index < 0)
			return optional ? NULL : ERR_PTR(-ENOENT);    /* :834-835 */
	}

	ret = of_parse_phandle_with_args(node, "resets", "#reset-cells",
					 index, &args);               /* :838-839 */
	if (ret == -EINVAL)
		return ERR_PTR(ret);                                  /* :840-841 */
	if (ret)
		return optional ? NULL : ERR_PTR(ret);                /* :842-843 */

	mutex_lock(&reset_list_mutex);                                /* :845 */
	list_for_each_entry(r, &reset_controller_list, list) {
		if (args.np == r->of_node) {                          /* :848 */
			rcdev = r;
			break;
		}
	}

	if (!rcdev) {
		rstc = ERR_PTR(-EPROBE_DEFER);                        /* :855 */
		goto out_unlock;
	}

	if (WARN_ON(args.args_count != rcdev->of_reset_n_cells)) {
		rstc = ERR_PTR(-EINVAL);                              /* :859-860 */
		goto out_unlock;
	}

	rstc_id = rcdev->of_xlate(rcdev, &args);                      /* :864 */
	if (rstc_id < 0) {
		rstc = ERR_PTR(rstc_id);                              /* :865-868 */
		goto out_unlock;
	}

	/* reset_list_mutex also protects the rcdev's reset_control list */
	rstc = __reset_control_get_internal(rcdev, rstc_id, shared, acquired); /* :871 */

out_unlock:
	mutex_unlock(&reset_list_mutex);
	of_node_put(args.np);                                         /* :875 */
```

**两条「optional 也救不了」的错误**，是本框架最隐蔽的两个陷阱：

| 错误 | 行 | 为什么不给 optional 让路 |
|---|---|---|
| `-EILSEQ` | `:832-833` | `reset-names` 属性**本身格式坏了**（不是以 NUL 结尾的字符串列表）。这不是「没有这个 reset」，是 DTS 坏了 |
| `-EINVAL`（来自 `of_parse_phandle_with_args`） | `:840-841` | phandle 参数个数和 `#reset-cells` 对不上 ⇒ 同样是 DTS 坏了 |

⇒ 所以「用 `_optional` 变体就不会失败」是**错的**。
`_optional` 只吞掉「这条 reset 不存在」（`-ENOENT`）和「phandle 解析不出来」，
不吞「DTS 写错了」。分诊表在 `07-debug.md` §4.2。

**匹配是精确的指针比较**（`:848` `args.np == r->of_node`），
不是字符串、不是 compatible。⇒ 一个 DT 节点最多对应一个 `rcdev`；
同一个硬件想注册两个 rcdev 必须造两个 of_node。

**`of_node_put(args.np)` 在 `:875`，`out_unlock` 之后** ——
所有错误路径都 `goto out_unlock`，所以引用一定会被放掉。
唯一的例外是 `:826-843` 那几个 early return，那时 `args` 还没被填过。

### A2.3 lookup 路径：为什么它要嵌两把锁

```c
/* drivers/reset/core.c:899-940，摘要 */
	mutex_lock(&reset_lookup_mutex);                       /* :908 外层 */
	list_for_each_entry(lookup, &reset_lookup_list, list) {
		if (strcmp(lookup->dev_id, dev_name(dev)))      /* :911 */
			continue;
		if ((!id && !lookup->con_id) ||
		    (id && lookup->con_id && !strcmp(id, lookup->con_id))) {  /* :914-916 */
			mutex_lock(&reset_list_mutex);         /* :917 内层 */
			rcdev = __reset_controller_by_name(lookup->provider);
			if (!rcdev) {
				mutex_unlock(&reset_list_mutex);
				mutex_unlock(&reset_lookup_mutex);
				/* Reset provider may not be ready yet. */
				return ERR_PTR(-EPROBE_DEFER);  /* :923 */
			}
			rstc = __reset_control_get_internal(rcdev, lookup->index,
							    shared, acquired);
			mutex_unlock(&reset_list_mutex);       /* :929 */
			break;
		}
	}
	mutex_unlock(&reset_lookup_mutex);

	if (!rstc)
		return optional ? NULL : ERR_PTR(-ENOENT);     /* :936-937 */
```

两把锁的嵌套只发生在这里，方向恒定（`reset_lookup_mutex` → `reset_list_mutex`），
所以结构上不可能 AB-BA（§B1.3）。

匹配规则值得记一下（`:914-916`）：**`id` 和 `con_id` 必须「同时有」或「同时无」**。
一个有一个没有就不匹配 —— 所以 `reset_control_get(dev, NULL)` 拿不到
`con_id = "core"` 的那条表项。

和 DT 路径的三个差别：

| | DT 路径 | lookup 路径 |
|---|---|---|
| 匹配控制器 | `of_node` 指针相等（`:848`） | 字符串 `strcmp`（`:889-893`） |
| 线号来自 | `of_xlate(rcdev, &args)`（`:864`） | 表里的 `index` 字段，**不经过 xlate** |
| 参数个数校验 | 有（`:859`） | ❌ 无 |

---

## §A3 `__reset_control_get_internal`：复用还是新建

调用者必须已持 `reset_list_mutex`（`core.c:754` 的 `lockdep_assert_held`）。

```c
/* drivers/reset/core.c:749-791，摘要 */
	list_for_each_entry(rstc, &rcdev->reset_control_head, list) {
		if (rstc->id == index) {
			if (!rstc->shared && !shared && !acquired)
				break;                             /* :763-764 */

			if (WARN_ON(!rstc->shared || !shared))
				return ERR_PTR(-EBUSY);            /* :766-767 */

			kref_get(&rstc->refcnt);                   /* :769 */
			return rstc;
		}
	}

	rstc = kzalloc(sizeof(*rstc), GFP_KERNEL);                 /* :774 */
	if (!rstc)
		return ERR_PTR(-ENOMEM);                           /* :776 */

	if (!try_module_get(rcdev->owner)) {                       /* :778 */
		kfree(rstc);
		return ERR_PTR(-ENODEV);                           /* :780 */
	}

	rstc->rcdev = rcdev;                                       /* :783 */
	list_add(&rstc->list, &rcdev->reset_control_head);         /* :784 */
	rstc->id = index;
	kref_init(&rstc->refcnt);                                  /* :786 */
	rstc->acquired = acquired;
	rstc->shared = shared;                                     /* :788 */
```

三条结论（判定表在 `02-datastructures.md` §1.1）：

1. **`shared` 和 `acquired` 只在新建时写入**（`:787-788`），之后除了
   acquire/release 改 `acquired`，没人再动它们。⇒ **句柄的语义是取的时候定死的**。
2. **`try_module_get(rcdev->owner)` 是控制器模块防拔的唯一手段**（`:778`）。
   `owner` 填 NULL 的控制器 —— `try_module_get(NULL)` 返回 true，
   于是**完全没有保护**。这就是 `02-datastructures.md` §2.3 把 `owner` 标成必填的原因。
3. **新建也是头插**（`:784`），和控制器注册一致。

关于 `:763-764` 那个 `break`：它跳出循环去**新建第二个 exclusive 句柄**。
这意味着**同一条线在链表上可以有多个表项**，而 acquire 正是靠遍历这些表项
来判断「有没有别人已经拿着」（`:660-667`）。⇒ **`_released` 变体和 acquire 机制
是同一个设计的两半**，缺一不可。

---

## §A4 t2：五个动作在核心里的统一骨架

四个动作 + `rearm` 的代码骨架高度一致，差别集中在两处。
`assert` 的逐行图在 `02-datastructures.md` §5，这里只给**横向对照**。

### A4.1 五步骨架

| 步 | 内容 | reset | assert | deassert | status | rearm |
|---|---|---|---|---|---|---|
| ① | `!rstc` → 0 | `:331` | `:444` | `:532` | `:607` | `:403` |
| ② | `WARN_ON(IS_ERR)` → `-EINVAL` | `:334` | `:447` | `:535` | `:610` 合并写 | `:406` |
| ③ | 数组分流 | `:337` | `:450` | `:538` | 直接 `-EINVAL` `:610` | `:409` |
| ④ | 回调存在性检查 | `:340` **分岔前** | `:467`/`:475` **分岔内两份** | `:562` **分岔后** | `:613` 三元 | ❌ 无（不碰硬件） |
| ⑤ | shared/exclusive 分岔 | `:343-352` | `:453-483` | `:541-553` | ❌ 无 | `:412-420` |

**第 ④ 步三种位置**是本框架最容易踩的不一致（`01-api.md` §3.1）：
同一件事（控制器没实现这个回调），`reset` 两种语义都报 `-ENOTSUPP`、
`deassert` 两种语义都返回 0、`assert` 一个报错一个静默。

### A4.2 `rearm`：唯一一个不碰硬件的动作

```c
/* drivers/reset/core.c:412-422 */
	if (rstc->shared) {
		if (WARN_ON(atomic_read(&rstc->deassert_count) != 0))
			return -EINVAL;                        /* :413-414 */

		WARN_ON(atomic_dec_return(&rstc->triggered_count) < 0);  /* :416 */
	} else {
		if (!rstc->acquired)
			return -EPERM;                         /* :418-419 */
	}

	return 0;
```

| 观察 | 说明 |
|---|---|
| 对 exclusive 句柄是**纯 no-op** | `:417-420` 只检查 `acquired` 然后返回 0，**什么都没做** |
| shared 时把 `triggered_count` 减一 | ⇒ 允许再发一次脉冲。kernel-doc（`:390-394`）明说必须和 `reset_control_reset` 配平 |
| 减到负数只 `WARN`，不返回错误 | `:416` 的 `WARN_ON` 包在 `atomic_dec_return` 外面 —— **计数已经被改坏了**，函数照样返回 0 |
| 和 assert/deassert 互斥 | kernel-doc `:396-397`：用了 `reset`/`rearm` 就不许再用 `(de)assert` |

「为什么需要 rearm」：shared 语义下 `reset_control_reset` 一辈子只真发一次脉冲
（kernel-doc `core.c:318-320`）。要再发就得先 rearm。
大白话解释在 `08-feynman.md` §4。

### A4.3 数组版的六个静态函数

`core.c:199-307`，全是 `static`，名字容易和导出 API 混（`02-datastructures.md` §7.2）：

| 函数 | 行 | 出错回滚 | 备注 |
|---|---|---|---|
| `reset_control_array_reset` | `:199-210` | ❌ **不回滚** | 中途失败就返回，前面几条已经被脉冲过了 |
| `reset_control_array_rearm` | `:212-243` | **两趟写法** | 见下 |
| `reset_control_array_assert` | `:245-261` | ✅ 反向 deassert | `:258-259` |
| `reset_control_array_deassert` | `:263-279` | ✅ 反向 assert | `:276-277` |
| `reset_control_array_acquire` | `:281-299` | ✅ 反向 release | `:295-296` |
| `reset_control_array_release` | `:301-307` | — | `void`，无失败路径 |

**`array_rearm` 是全框架唯一做到「要么全做要么不动」的函数**：

```c
	/* 第一趟：只检查，不改任何状态 —— core.c:217-233 */
	for (i = 0; i < resets->num_rstcs; i++) { ...检查 shared/acquired... }

	/* 第二趟：确认全部合法之后才改计数 —— core.c:235-240 */
	for (i = 0; i < resets->num_rstcs; i++)
		if (rstc && rstc->shared)
			WARN_ON(atomic_dec_return(&rstc->triggered_count) < 0);
```

⇒ 对比 `array_assert` 的「边做边错再倒回去」：**两种风格并存在同一个文件里**，
是 §C1 的第一条设计债。

顺带一个容易漏的点：`array_rearm` 里有 `if (!rstc) continue;`（`:220-221`）
⇒ **数组元素可以是 NULL**。来源是 `of_reset_control_array_get` 用 `optional=true`
调 `__of_reset_control_get`（`core.c:1190`），单条取不到时返回 NULL 并被存进数组。
其余五个数组函数不检查 NULL，靠的是「四个动作本身接受 NULL」（`01-api.md` §3.2）。

---

## §A5 acquire / release：唯一的运行期所有权转移

```c
/* drivers/reset/core.c:653-671 */
	mutex_lock(&reset_list_mutex);

	if (rstc->acquired) {                    /* :655 已经拿着 → 幂等成功 */
		mutex_unlock(&reset_list_mutex);
		return 0;
	}

	list_for_each_entry(rc, &rstc->rcdev->reset_control_head, list) {
		if (rstc != rc && rstc->id == rc->id) {    /* :661 同线、不同句柄 */
			if (rc->acquired) {
				mutex_unlock(&reset_list_mutex);
				return -EBUSY;             /* :664 */
			}
		}
	}

	rstc->acquired = true;                   /* :669 */
	mutex_unlock(&reset_list_mutex);
```

| 观察 | 说明 |
|---|---|
| 幂等 | 重复 acquire 返回 0（`:655-658`） |
| 遍历的是**同一个 rcdev** 的链表 | ⇒ 冲突判定的粒度是「(rcdev, id)」这一对 |
| release 不加锁 | `:724` 一句 `rstc->acquired = false;`，详见 `02-datastructures.md` §6.4 |
| release 不检查「你本来 acquire 了吗」 | 重复 release 静默通过 |
| 数组版 | acquire 逐个做并在失败时回滚（`:281-299`）；release 逐个做（`:301-307`） |

**谁在用这套机制**：普查记在 `99-evidence.md` §3.3。
典型场景是同一条复位线在不同时段分别由两个驱动控制。
大白话解释在 `08-feynman.md` §3。

---

## §A6 t3：放句柄的四条路径

| 入口 | 行 | 做什么 |
|---|---|---|
| `reset_control_put` | `:999-1012` | `IS_ERR_OR_NULL` 直接返回（`:1001`）；数组走 `reset_control_array_put`（`:1004-1007`）；否则加锁 + `__reset_control_put_internal` |
| `reset_control_array_put` | `:984-993` | 加锁 → 逐个 `__reset_control_put_internal` → 解锁 → **`kfree(resets)`**（`:992`） |
| `reset_control_bulk_put` | `:1020-1026` | 加锁 → 逐个 put → 解锁 |
| devres 自动 | `:1029-1032` / `:1063-1068` | 设备移除时回调 `reset_control_put` / 逐个 put |

核心两层：

```c
/* drivers/reset/core.c:806-813 */
static void __reset_control_put_internal(struct reset_control *rstc)
{
	lockdep_assert_held(&reset_list_mutex);

	if (IS_ERR_OR_NULL(rstc))
		return;                                    /* :810-811 二次兜底 */

	kref_put(&rstc->refcnt, __reset_control_release);
}

/* drivers/reset/core.c:793-804 */
static void __reset_control_release(struct kref *kref)
{
	lockdep_assert_held(&reset_list_mutex);            /* :798 */

	module_put(rstc->rcdev->owner);                    /* :800 */
	list_del(&rstc->list);                             /* :802 */
	kfree(rstc);                                       /* :803 */
}
```

**释放时不做任何状态检查**：

| 没检查的 | 后果 |
|---|---|
| `deassert_count` 是否为 0 | 消费者 deassert 之后直接 put ⇒ **复位线永远停在「已解除」状态**，没人再管它 |
| `triggered_count` | 同上 |
| `acquired` 是否还是 true | 拿着独占权直接 put ⇒ 句柄消失，独占权随之消失（这个其实是对的） |

⇒ 「put 不会帮你收尾」这件事必须由消费者自己记住：
**assert/deassert 的配平责任完全在消费者侧**（`05-usage.md` §2.3 的骨架里
把 `reset_control_assert` 放在 remove 的第一行）。

---

## §A7 t4：控制器注销 —— 全框架最大的一个洞

```c
/* drivers/reset/core.c:121-126 */
void reset_controller_unregister(struct reset_controller_dev *rcdev)
{
	mutex_lock(&reset_list_mutex);
	list_del(&rcdev->list);
	mutex_unlock(&reset_list_mutex);
}
```

**五行，什么都不检查**：

| 问题 | 答案 |
|---|---|
| 检查 `rcdev->reset_control_head` 空不空吗 | ❌ **不检查** |
| 已经发出去的句柄怎么办 | **它们还指向这个 `rcdev`**，后续任何动作都会解引用一个已经消失的结构 |
| 那为什么实际上不炸 | 靠 `try_module_get(rcdev->owner)`（`:778`）—— **只要还有句柄在，控制器模块就 `rmmod` 不掉**，于是 `unregister` 也就不会被调 |
| 那什么情况会炸 | (a) `owner` 填 NULL 或填错；(b) 控制器是 `bool` 编进内核的驱动、通过 sysfs `unbind` 强制解绑；(c) 控制器 probe 失败回滚时消费者已经拿到句柄（次序上很难，但没有机制阻止） |

⇒ **这是本框架 `RESET_CONTROLLER` 被设计成 `bool` 而不是 `tristate`
（`drivers/reset/Kconfig`）之外，最值得记住的一条生命周期约束。**
对比 clk 框架：`clk_unregister` 会把 `clk_core` 标成孤儿并有一整套 `clk_core->ref` 处理。

具体的排查手法（怎么确认某条线现在还有几个句柄）在 `07-debug.md` §5.2 ——
因为核心不导出任何东西，只能靠 kprobe 或 drgn。

---

# Part B —— 横切机制

## §B1 两把锁的全部临界区

### B1.1 `reset_list_mutex`：十个临界区

| 临界区 | 行 | 里面做什么 | |
|---|---|---|---|
| 注册 | `:109-111` | `list_add` 到全局链表 | — |
| 注销 | `:123-125` | `list_del` | — |
| acquire | `:653-671` | 遍历同 rcdev 的句柄链表 + 写 `acquired` | 唯一在锁内写句柄字段的动作路径 |
| DT 取句柄 | `:845-874` | 遍历全局链表 + **调 `of_xlate`** + 建/复用句柄 | 见 B1.4 |
| lookup 取句柄 | `:917-929` | 同上（内层锁） | 见 B1.3 |
| bulk get 回滚 | `:976-979` | 逐个 `__reset_control_put_internal` | — |
| array put | `:988-991` | 同上 | `kfree` 在锁**外**（`:992`） |
| put | `:1009-1011` | 单个 put | — |
| bulk put | `:1022-1025` | 逐个 put | — |
| array get 回滚 | `:1202-1205` | 逐个 put | — |

**它保护两样东西**：全局 `reset_controller_list`，**以及每个 `rcdev` 的
`reset_control_head`**。`core.c:870` 有注释明说。⇒ 四个 `lockdep_assert_held`
（`:754` / `:798` / `:808` / `:886`）就是这个约定的执行者。

### B1.2 `reset_lookup_mutex`：三个临界区

`:178-190`（`add_lookup`）、`:908-934`（lookup 取句柄）、`:1259-1266`
（`reset_control_get_count_from_lookup`）。只保护 `reset_lookup_list`。

### B1.3 嵌套：只有一个方向

`reset_lookup_mutex`（`:908`）→ `reset_list_mutex`（`:917`）。
反向不存在（`reset_lookup_mutex` 全文件只有上面三处）⇒ **结构上不可能 AB-BA**。

### B1.4 对控制器驱动的硬约束

| 约束 | 依据 | 违反的后果 |
|---|---|---|
| `of_xlate` 里不能调任何 `reset_control_*` | 它在 `reset_list_mutex` 里被调（`:845` 加锁 → `:864` 调用） | **自死锁** |
| `of_xlate` 里不能等一个会去取句柄的 work | 同上 | 死锁 |
| `of_xlate` 可以睡 | mutex 本身可睡 | — |
| 四个动作回调**不在**锁内 | `:485` / `:565` / `:354` / `:614` 都在 `mutex_unlock` 之后 | ⇒ 回调里可以睡（regmap/SCMI 都需要） |
| 但动作回调里也不该递归进框架 | 没有锁保护，但语义上会破坏计数 | 计数错乱 |

**「四个动作回调可以睡」这一条很重要**：它意味着
**`reset_control_assert` 不能在原子上下文里调用**（如果控制器是 SCMI 或 I2C 挂的）。
核心自己不阻止 —— 没有 `might_sleep()`（`99-evidence.md` §3.4 记了普查命令）。

---

## §B2 三种「计数」各管什么

| 计数 | 载体 | 加/减点 | 归零/满时 |
|---|---|---|---|
| **句柄引用** | `rstc->refcnt`（kref） | `kref_get`（`:769`）/ `kref_init`（`:786`）/ `kref_put`（`:813`） | 归零 → `list_del` + `kfree` + `module_put`（`:800-803`） |
| **模块引用** | `rcdev->owner` | `try_module_get`（`:778`）/ `module_put`（`:800`） | 防止控制器模块被卸载（§A7） |
| **解除复位** | `rstc->deassert_count`（atomic） | `inc`（`:545`）/ `dec`（`:460`） | 0→1 才落硬件 deassert，1→0 才落硬件 assert |
| **脉冲** | `rstc->triggered_count`（atomic） | `inc`（`:347`）/ `dec`（`:356` 失败回滚、`:416` rearm） | 值只会是 0 或 1（kernel-doc `:38-40`） |

**前两个是「资源生命周期」，后两个是「硬件状态」，绝不能混着理解**：
`refcnt` 归零时**不会**把 `deassert_count` 归零（§A6），也就是说
**句柄没了，硬件状态还留在那儿**。

devres 是第三种「自动化」，但它不是计数：`devm_reset_control_get_*`
只是把 `reset_control_put` 登记到设备的 devres 链表上（`:1040-1052`），
设备移除时一次性回调。注意 `IS_ERR_OR_NULL(rstc)` 时**不登记**（`:1046-1049`）
—— 所以 optional 拿到 NULL 不会在 remove 时产生一次多余的 put。

---

## §B3 错误传播：谁改写了错误码

消费者拿到的错误码经常**不是**最底层产生的那个。全部改写点：

| 原始错误 | 改写点 | 改成 | |
|---|---|---|---|
| `of_property_match_string` 返回负值 | `:834-835` | `optional ? NULL : -ENOENT` | **原始错误码丢失** |
| 同上但等于 `-EILSEQ` | `:832-833` | **原样透传** | optional 也不吞 |
| `of_parse_phandle_with_args` 返回 `-EINVAL` | `:840-841` | 原样透传 | 同上 |
| 其他解析错误 | `:842-843` | `optional ? NULL : 原码` | 保留原码 |
| 控制器还没注册 | `:855` / `:923` | `-EPROBE_DEFER` | **不受 optional 影响** |
| cell 个数不符 | `:859-860` | `-EINVAL` + `WARN` | — |
| `of_xlate` 返回负值 | `:865-868` | 原样透传 | 控制器驱动可以自定错误码 |
| 句柄冲突 | `:766-767` | `-EBUSY` + `WARN_ON` | — |
| 模块正在卸载 | `:778-780` | `-ENODEV` | — |
| lookup 一条都没匹配上 | `:936-937` | `optional ? NULL : -ENOENT` | — |

**`-EPROBE_DEFER` 是唯一一个「optional 也不救」且**语义上正确**的错误**：
控制器还没注册不代表这条 reset 不存在，只代表现在还问不到。
⇒ **消费者写 `_optional` 变体时，仍然必须把返回值往上传**，
不能写成 `rstc = devm_reset_control_get_optional(...); /* 忽略错误 */`。
骨架见 `05-usage.md` §2.2。

**`-ENOTSUPP` vs `-EOPNOTSUPP`**：核心用的是 `-ENOTSUPP`(524)，
而 `CONFIG_RESET_CONTROLLER=n` 时 bulk 系列的桩返回 `-EOPNOTSUPP`(95)
（`include/linux/reset.h:167` vs `:180`）。这个不一致的完整表在 `01-api.md` §6。

---

## §B4 顺序与回滚矩阵

这是「bulk 和 array 到底差在哪」的最终答案：

| 操作 | 遍历顺序 | 失败回滚 | 依据 |
|---|---|---|---|
| `reset_control_bulk_reset` | 正序 | ❌ 无 | `core.c:376-380` |
| `reset_control_bulk_assert` | **正序** | ✅ 反向 deassert | `:504-514` |
| `reset_control_bulk_deassert` | **逆序** | ✅ 正向 assert | `:584-594` |
| `reset_control_bulk_acquire` | 正序 | ✅ 反向 release | `:691-701` |
| `reset_control_bulk_release` | 正序 | — | `:741-745` |
| `reset_control_array_*`（除 rearm） | **一律正序** | 见 §A4.3 | `:199-307` |

**`bulk_deassert` 逆序是有意设计的**，kernel-doc 标题就写着
「deasserts the reset lines in reverse order」（`core.c:570`）。
配合正序的 `bulk_assert`，得到**对称的上下电次序**：

```
   上电（deassert）: rstcs[N-1] → … → rstcs[1] → rstcs[0]
   下电（assert）  : rstcs[0]   → … → rstcs[N-1]
```

⇒ 消费者应该把 `reset_control_bulk_data[]` 按**「最先该被 assert 的放前面」**
的次序声明。这一点绑定文档不会告诉你，`05-usage.md` §3.2 的骨架里写了注释。

**而 array 没有任何顺序保证**，官方文档明说
（`Documentation/driver-api/reset.rst:152-153`）：数组的次序就是 DTS 里
`resets` 属性的次序，核心一律正序遍历，**上下电不对称**。
⇒ **对顺序敏感的硬件必须用 bulk 或者逐条取，不能用 array。**

---

# Part C —— 评价

## §C1 设计债

### C1.1 先说一个反常的事实：源码里没有一条自陈债

```bash
$ /usr/bin/grep -rn 'FIXME\|TODO\|REVISIT\|XXX\|HACK' drivers/reset/ \
      --include=*.c --include=*.h | wc -l
0
```

**整个 `drivers/reset/`（44 个文件、8922 行）里没有一个 FIXME / TODO /
REVISIT / XXX / HACK。** 这在内核里相当罕见（对比：多数框架的核心文件里都有几条）。

⇒ 所以本节的债**全部是从行为不一致里读出来的，不是作者自己标的**。
这一点要老实告诉读者：**下面每一条都是本文档集的判断，不是内核作者的表态**
（记进 `99-evidence.md` §5.3「推断而非观察」）。

### C1.2 六条行为不一致

| # | 债 | 证据 | 后果 |
|---|---|---|---|
| 1 | **同一件事三种返回码**：控制器没实现回调时，`reset` 报 `-ENOTSUPP`、`deassert` 返回 0、`assert` 视 shared 与否而定 | `core.c:340-341` / `:562-563` / `:467-468` + `:475-476` | 消费者无法用统一写法判断「这个能力有没有」 |
| 2 | **两种回滚风格并存**：`array_rearm` 是「先全检查再全做」，`array_assert`/`array_deassert`/`array_acquire` 是「边做边错再倒回去」，`array_reset`/`bulk_reset` **完全不回滚** | `:212-243` vs `:245-299` vs `:199-210` / `:376-380` | 部分失败后的硬件状态取决于你调了哪个函数 |
| 3 | **只有 `reset` 有失败回滚计数**：`ops->reset` 失败会把 `triggered_count` 减回去（`:355-356`），而 `assert`/`deassert` 失败后计数已经改了不还原 | `:355-356` vs `:460`/`:545` | shared 语义下一次失败的 assert 会让计数和硬件永久错位 |
| 4 | **acquire 持锁、release 裸写** | `:653` vs `:724` | `rstc->acquired` 上的无同步写（`02-datastructures.md` §6.4） |
| 5 | **同一个错误三个入口两种响法**：未 acquire 时 `assert`/`deassert` 打 `WARN(1, "reset %s (ID: %u) is not acquired\n")`（`:479-481` / `:549-551`），`reset` 和 `rearm` 只静默返回 `-EPERM`（`:350-351` / `:418-419`） | 同左 | 用 `reset_control_reset` 的消费者拿到 `-EPERM` 时没有任何线索 |
| 6 | **`unregister` 不检查在用句柄** | `:121-126` | §A7 的那个洞；靠 `try_module_get` 间接兜着 |

### C1.3 三条能力缺口

| 缺口 | 依据 | 影响 |
|---|---|---|
| `status` 不支持数组句柄 | `core.c:610` 直接 `-EINVAL` | 用了 array 就查不了状态；只能改成逐条取 |
| array 没有顺序保证 | `Documentation/driver-api/reset.rst:152-153` + `:199-307` 一律正序 | 上下电不对称，顺序敏感的硬件不能用（§B4） |
| `rearm` 对 exclusive 是 no-op 且**不提示** | `:417-420` | 误以为「rearm 了就能再发脉冲」的 exclusive 消费者拿到 0，什么都没发生 |

### C1.4 一条文档/代码不一致

`drivers/reset/Kconfig:11-12` 的帮助文字说本框架抽象的是
「reset handling of devices **via GPIOs** or SoC-internal reset controller modules」，
但 **6.1 树里没有任何基于 GPIO 的通用 reset 控制器驱动**
（`drivers/reset/` 下没有 `reset-gpio.c`，普查记在 `99-evidence.md` §7.3）。
⇒ 帮助文字描述的是设计意图或更晚的版本，不是 6.1 的实现。
用 GPIO 做复位的驱动在 6.1 里走的是 `gpiod_set_value` 直连，不经过本框架。

---

## §C2 和 clk 框架的对比：为什么「像」但差得很远

官方文档开篇就说本框架「similar to the kernel clock framework」
（`Documentation/driver-api/reset.rst:57`）。相似之处确实是**结构性的**：
provider 注册进全局链表、consumer 用 `devm_*_get` 拿句柄、DT phandle + `#*-cells`
+ `*-names` 三件套、`of_xlate` 翻译 cell、`-EPROBE_DEFER` 处理次序。

但差别更值得记，因为它们决定了「你能从 reset 框架期待什么」：

| 维度 | reset | clk |
|---|---|---|
| 核心规模 | **1 个 .c，1289 行** | 十几个文件，核心单文件就上万行 |
| 句柄层数 | **一层**（`reset_control` → `rcdev`） | 两层（`struct clk` → `clk_core` → `clk_hw`） |
| 有没有拓扑 | ❌ **没有**：复位线之间无父子关系 | 有完整的父子树 + reparent |
| 状态查询 | 只有 `status` 回调，多数控制器不实现 | 完整的 rate/parent/enable 状态机 |
| 原子上下文 | 全 mutex，**回调可睡** | prepare（可睡）+ enable（自旋锁）两层 |
| debugfs | **零** | 完整的 `clk_summary` 树 |
| tracepoint | **零** | 有 |
| provider 注销 | 只 `list_del`（§A7） | 有孤儿处理和引用检查 |
| 「谁在用」可查吗 | **不可查** | 可从 debugfs 查 |

**一句话总结这个对比**：reset 借用了 clk 的**接口形状**，
但没有借用它的**基础设施**。原因也很清楚 —— 复位线没有拓扑、没有速率、
没有连续状态，绝大多数场景就是「上电时 deassert 一次」。
**框架的复杂度匹配了问题的复杂度**，这是设计上的优点，不是缺点。
代价全部落在可观测性上（§C3）。

---

## §C3 可观测性：一个可以量化的零

| 观测手段 | reset 框架有吗 | 依据（命令记在 `99-evidence.md` §3.1） |
|---|---|---|
| sysfs 属性 | ❌ **零** | `grep -c 'DEVICE_ATTR\|sysfs' core.c` = 0 |
| debugfs 节点 | ❌ **零** | `grep -c debugfs core.c` = 0 |
| tracepoint | ❌ **零** | `grep -c 'trace_' core.c` = 0 |
| `pr_debug` / `dev_dbg` | ❌ **零** ⇒ **`dyndbg` 对本框架完全无效** | `core.c` 里只有 `:183` 一条 `pr_warn` |
| 正常路径日志 | ❌ **零** | 同上，且那一条在 arm64 用不到的 lookup 路径上 |
| `WARN` | **21 处**（19 个 `WARN_ON` + 2 个带消息的 `WARN(1, …)`） | `grep -c WARN core.c` = 21 |
| 全局状态可读吗 | ❌ 两个链表两把锁**全 `static`**（`core.c:20-24`） | — |
| 句柄里有消费者信息吗 | ❌ `reset_control` 里没有 `name`、没有消费者 `device` 指针 | `core.c:42-52` |

**合起来意味着**：本框架的运行期可见性 **= 21 个 WARN 的栈回溯**。
除此之外你什么都看不到 —— 既不知道系统里有几个控制器，
也不知道某条线现在被谁拿着、`deassert_count` 是几。

⇒ **这直接决定了 `07-debug.md` 的写法**：那一篇不可能是「看某个 debugfs 文件」，
只能是「(a) 从 21 个 WARN 反推、(b) 用 kprobe/kretprobe 在核心函数上打点、
(c) 用 drgn 读那两个 static 链表、(d) 在控制器驱动侧加 dev_dbg」。

**一条实用推论**：`WARN` 的两条带消息的（`:479-481` / `:549-551`）
是唯一带上下文的输出，格式是 `reset <控制器名> (ID: <线号>) is not acquired`。
⇒ **只有「未 acquire」这一类错误能从 dmesg 直接定位到具体哪条线**。
其余 19 个 `WARN_ON` 只有栈回溯，得靠符号名反推。

---

## §C4 公平地说：这个设计好在哪

前面三节全在讲缺口，但要避免给出「这框架写得糙」的错觉。三条优点：

1. **规模匹配问题**：1289 行提供了一个被上千个驱动使用的完整抽象。
   没有 class、没有 bus、没有额外的 device 对象 —— 消费者侧的代价是
   **一次函数调用**，没有任何隐藏的对象生命周期要管。
2. **shared 语义是真的解决了一个硬问题**：多个外设共用一路复位线，
   在没有框架协调的年代只能靠「谁也不敢碰」或者各自维护计数。
   `deassert_count` / `triggered_count` 这两个原子计数把它压成了 20 行代码。
3. **`-EPROBE_DEFER` 用得干净**：`:855` 和 `:923` 两处，
   把「设备注册次序不确定」这个 driver model 的固有问题完全交回给 driver model，
   框架自己不做任何排序、不维护任何等待队列。

真正的改进方向也很清楚，而且不需要动架构：
**加一个 debugfs 目录**（控制器列表 + 每条线的两个计数 + 谁持有），
以及**给 `reset_control` 加一个消费者 `device` 指针**。
上游后续版本确实往这个方向走了一些，但 6.1 里没有
（版本时效性记在 `99-evidence.md` §5.4）。

---

## §C5 接下来看哪一篇

| 你现在的问题 | 去哪 |
|---|---|
| `reset-simple` / `reset-scmi` / `reset-ti-syscon` 怎么实现的 | `04-extensions.md` |
| 我要写消费者或控制器代码 | `05-usage.md` |
| DTS 三件套怎么写、写错了什么症状 | `06-devicetree.md` |
| 21 个 WARN 分别对应什么故障 | `07-debug.md` §3 |
| 没有 debugfs 的情况下怎么查「谁拿着这条线」 | `07-debug.md` §5 |
| shared / exclusive / acquire 的大白话解释 | `08-feynman.md` |
| 本篇每个行号、每个计数是怎么来的 | `99-evidence.md` |
