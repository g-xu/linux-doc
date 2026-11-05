---
title: 内部实现
---

# 03 实现剖析：dmaengine 核心机制

> 只讲 **arm64**。ACPI 路径（`drivers/dma/acpi-dma.c`）已裁掉，记在 `99-evidence.md` §2.7。
>
> **本篇只承诺一条主轴，其余分区**：
> - **Part A = 时间线**（A1→A7，按"先发生什么后发生什么"排，次序全部用"谁调用谁"验过）
> - **Part B = 横切机制**（B1→B5，跨越整条时间线、无法归入某一步的东西：锁、引用计数、percpu 表、router）
> - **Part C = 评价**（C1→C3，源码设计债、官方文档与代码的冲突、设计取舍）
>
> 章节号自带前缀，所以别处写「见 `03-internals.md` §A3」时，你一眼就知道那是时间线上的第三步。

---

## §0 导航

### §0.1 分层图：这一篇在剖析哪几层

```
        ┌───────────────────────────────────────────────┐
        │ 消费者驱动（uart / spi / mmc / i2s …）          │  ← 05-usage.md
        │   dma_request_chan / prep / submit / issue     │
        └───────────────────┬───────────────────────────┘
                            │ include/linux/dmaengine.h 的内联包装
                            │ （只是转调回调，不含逻辑 → 01-api.md）
        ┌───────────────────▼───────────────────────────┐
        │ dmaengine 核心  drivers/dma/dmaengine.c      │  ← 本篇 A1/A2/A3/A6/A7 + B1/B2/B4
        │   通道注册表 · 引用计数 · 通道分配 · unmap 池   │
        ├───────────────────────────────────────────────┤
        │ DT 解析层  drivers/dma/of-dma.c              │  ← 本篇 A3 + B5
        │   of_dma_list · xlate · router                 │
        ├───────────────────────────────────────────────┤
        │ 可选公共层  drivers/dma/virt-dma.{c,h}       │  ← 本篇 A4/A5 + B3
        │   五态队列 · tasklet 回调派发 · cookie          │
        ├───────────────────────────────────────────────┤
        │ cookie 记账  drivers/dma/dmaengine.h（私有）  │  ← 本篇 A4
        └───────────────────┬───────────────────────────┘
                            │ struct dma_device 回调表
        ┌───────────────────▼───────────────────────────┐
        │ 控制器驱动（pl330 / bam_dma / mtk-uart-apdma） │  ← 04-extensions.md
        └───────────────────────────────────────────────┘
```

### §0.2 生命周期时间线图（Part A 的骨架）

```
内核启动                                                          模块卸载
   │                                                                  │
   ├─ A1 arch_initcall：核心自举                                       │
   │   dma_channel_table_init(:262) · dma_bus_init(:1642)             │
   │                                                                  │
   ├─── A2 控制器 probe：注册                                          │
   │     dma_async_device_register(:1137-1297)                        │
   │     ├ 能力/回调一致性关卡(:1153-1220)                             │
   │     ├ 逐通道 __dma_async_device_channel_register(:1039-1087)      │
   │     └ of_dma_controller_register(of-dma.c:118)                   │
   │                                                                  │
   │     ┌── A3 消费者 probe：申请通道 ──────────────────┐              │
   │     │   dma_request_chan(:813-873)                 │              │
   │     │   → of_dma_request_slave_channel(of-dma.c:251)│             │
   │     │   → xlate → find_candidate(:656) → dma_chan_get(:446)      │
   │     │                                              │              │
   │     │   ┌── A4 一次传输（可重复 N 次）───────┐       │              │
   │     │   │ prep → submit → issue_pending     │       │              │
   │     │   │ → 硬件 → 中断 → tasklet → 回调    │       │              │
   │     │   └──────────────────────────────────┘       │              │
   │     │   ┌── A5 终止与同步（异常/关闭）─────┐         │              │
   │     │   │ terminate_async → synchronize   │         │              │
   │     │   └────────────────────────────────┘         │              │
   │     │                                              │              │
   │     └── A6 消费者 remove：dma_release_channel(:903) ┘              │
   │                                                                  │
   └────── A7 控制器 remove：dma_async_device_unregister(:1307) ────────┘
```

两个**必须记住的次序事实**（都用"谁调用谁"验过，不是凭直觉）：

1. **A2 里 `of_dma_controller_register` 在 `dma_async_device_register` 之后**。
   pl330 的顺序是 `:3146` 注册设备 → `:3153-3154` 注册 xlate
   （`drivers/dma/pl330.c`）。反过来会让 A3 拿到一个还没进 `dma_device_list`
   的控制器。
2. **A6 一定在 A7 之前，但核心不强制**。核心只在 A7 里打
   `WARN_ONCE(!device->device_release && chan->client_count, ...)`
   （`drivers/dma/dmaengine.c:1109-1111`）—— 也就是说，**它只是告诉你出事了，
   不阻止你出事**。B2 会讲这条 WARN 为什么被 `device_release` 豁免。

### §0.3 哪些内容不在本篇

| 你想找的 | 去哪篇 | 为什么不在这里 |
|---|---|---|
| 每个 API 的签名、错误码 | `01-api.md` | 本篇讲流程，不重列签名 |
| 结构体逐字段含义、结构关系图 | `02-datastructures.md` | 本篇假设你已经分清了 `dma_device`/`dma_chan`/`dma_chan_dev` |
| pl330 / bam_dma 具体怎么实现回调 | `04-extensions.md` | 本篇到"核心调用回调"为止 |
| 消费者/提供方代码骨架 | `05-usage.md` | 本篇不给可抄的代码 |
| `dmas` / `#dma-cells` 怎么写、写错什么症状 | `06-devicetree.md` | 本篇只讲核心怎么**解析**它 |
| debugfs / tracepoint / 错误码分诊 | `07-debug.md` | 本篇只在必要处提一句观测点 |
| 为什么这么设计（大白话） | `08-feynman.md` | 本篇是代码级剖析 |
| 每条论断的取证范围 | `99-evidence.md` | — |

---

# Part A：时间线

## §A1 内核启动期：核心自举（两个 `arch_initcall`）

dmaengine 核心自己**没有 `module_init`**，两个入口都是 `arch_initcall`
—— 比所有 `platform_driver` 的 probe 都早，所以 A2 永远能看到就绪的核心。

### §A1.1 `dma_channel_table_init` —— 建 percpu 通道表

`drivers/dma/dmaengine.c:262-291`，注册点 `:293`（`arch_initcall`）。

```c
	bitmap_fill(dma_cap_mask_all.bits, DMA_TX_TYPE_END);          /* :267 */

	/* 'interrupt', 'private', and 'slave' are channel capabilities,
	 * but are not associated with an operation so they do not need
	 * an entry in the channel_table                                  :269-271 */
	clear_bit(DMA_INTERRUPT, dma_cap_mask_all.bits);              /* :273 */
	clear_bit(DMA_PRIVATE, dma_cap_mask_all.bits);                /* :274 */
	clear_bit(DMA_SLAVE, dma_cap_mask_all.bits);                  /* :275 */
```

然后为剩下的每个能力位 `alloc_percpu` 一份 `struct dma_chan_tbl_ent`
（全局数组 `channel_table[DMA_TX_TYPE_END]` 在 `:260`）。

> **这三行 `clear_bit` 有一个后果，值得单独记住**：
> `DMA_SLAVE` 在 `channel_table` 里**没有槽位**，所以
> `dma_find_channel(DMA_SLAVE)` **永远返回 NULL**
> （`dma_find_channel` 在 `:545-552`，直接索引 `channel_table[tx_type]`）。
> 也就是说 arm64 上最常见的 slave DMA 场景**完全不走这张表**，
> 表只服务 memcpy/xor/pq 这类 async_tx 用途。B4 会把这张表的完整定位讲清楚。

失败时的处理很有意思（`:283-289`）：任何一个 `alloc_percpu` 失败就
`pr_err("initialization failure\n")`，然后**把已分配的全部 `free_percpu` 掉并返回
`-ENOMEM`** —— 但 `arch_initcall` 的返回值**没有人检查**，
所以真失败时核心会带着空表继续跑。

### §A1.2 `dma_bus_init` —— 建 unmap 池、注册 class、建 debugfs

`drivers/dma/dmaengine.c:1642-1653`，注册点 `:1655`（`arch_initcall`）。
三件事有严格的依赖顺序：

| 序 | 动作 | 代码 | 失败怎么办 |
|---|---|---|---|
| 1 | `dmaengine_init_unmap_pool()` | `:1644` → 池本体 `:1448-1474` | 直接 `return err`，后两步不做 |
| 2 | `class_register(&dma_devclass)` | `:1649` | 返回 err，第 3 步不做 |
| 3 | `dmaengine_debugfs_init()` | `:1651` | 只在第 2 步成功时执行 |

第 2 步是 dmaengine 和 clk / pinctrl 这类"纯框架"的**分界点**：
dmaengine **有一个 sysfs class**（`static struct class dma_devclass` 在
`:240-244`），所以 `/sys/class/dma/dma0chan0/` 是真实存在的目录。
但注意——**class 不是 bus**：它只提供属性和 release，不做 driver 匹配。
`02-datastructures.md` §5 已把"为什么通道不绑 driver"逐行摊开。

---

## §A2 控制器 probe：`dma_async_device_register`

`drivers/dma/dmaengine.c:1137-1297`，全篇最长的一个函数。它可以切成**六段**：

```
 段1 前置检查     :1143-1151   dev 非空、owner 取自 dev->driver->owner
 段2 能力关卡     :1153-1230   九个能力↔回调一致性检查 + 两个必填回调
 段3 引用计数初始 :1232        kref_init(&device->ref)
 段4 派生能力     :1237-1244   DMA_ASYNC_TX 自动置位 + get_dma_id + ida_init
 段5 逐通道注册   :1247-1251   __dma_async_device_channel_register
 段6 挂全局链表   :1253-1276   补引用 + list_add_tail_rcu + rebalance + debugfs
                  :1280-1296  err_out 回滚
```

### §A2.1 段2：能力关卡（注册失败最常见的原因）

九个检查一字排开在 `:1153-1214`，形状完全一样：

```c
	if (dma_has_cap(DMA_MEMCPY, device->cap_mask) && !device->device_prep_dma_memcpy) {
		dev_err(device->dev, "Device claims capability %s, but op is not defined\n", "DMA_MEMCPY");
		return -EIO;
	}
```

九个分别是 MEMCPY `:1153` · XOR `:1160` · XOR_VAL `:1167` · PQ `:1174` ·
PQ_VAL `:1181` · MEMSET `:1188` · INTERRUPT `:1195` · CYCLIC `:1202` ·
INTERLEAVE `:1209`。紧接着两个**无条件必填**：

| 检查 | 代码 | 不满足时 |
|---|---|---|
| `device_tx_status` 必须有 | `:1217-1220` | `dev_err` + `-EIO` |
| `device_issue_pending` 必须有 | `:1223-1226` | `dev_err` + `-EIO` |
| `device_release` 建议有 | `:1228-1230` | 只 `dev_dbg`，**默认看不见** |

> `:1228-1230` 是本篇第一条设计债，登记在 C1：
> 消息正文写着 `"WARN: Device release is not defined so it is not safe to
> unbind this driver while in use\n"`，**级别却是 `dev_dbg`**。
> 也就是说这条"不安全"的告警在不开 dynamic debug 的产品内核上**一条都不打**。

关卡的**覆盖面缺口**（C1 第 ③ 条）：`dma_transaction_type` 有 16 个成员
（`include/linux/dmaengine.h:51-70`），这里只查了 9 个。
`DMA_MEMSET_SG` / `DMA_SLAVE` / `DMA_REPEAT` / `DMA_LOAD_EOT` /
`DMA_COMPLETION_NO_ORDER` 都**没有对应检查** —— 一个只置 `DMA_SLAVE`
却忘了实现 `device_prep_slave_sg` 的控制器**能注册成功**，
故障延后到 A4 的 prep 阶段变成 NULL 指针解引用。

### §A2.2 段5：逐通道注册 `__dma_async_device_channel_register`

`drivers/dma/dmaengine.c:1039-1087`。它为每个通道做三件事：

```c
	chan->local = alloc_percpu(typeof(*chan->local));       /* :1044 统计计数器 */
	chan->dev   = kzalloc(sizeof(*chan->dev), GFP_KERNEL);  /* :1047 sysfs 载体 */
	chan->chan_id = ida_alloc(&device->chan_ida, GFP_KERNEL);/* :1057 通道号 */
```

然后填 `chan->dev`（`:1065-1070`）并 `device_register`（`:1071`）：

```c
	chan->dev->device.class  = &dma_devclass;   /* :1065 */
	chan->dev->device.parent = device->dev;     /* :1066 ← 唯一的"缝合点" */
	chan->dev->chan          = chan;            /* :1067 回指 */
	chan->dev->dev_id        = device->dev_id;  /* :1068 */
	dev_set_name(&chan->dev->device, "dma%dchan%d",
		     device->dev_id, chan->chan_id);/* :1069-1070 */
```

> **这五行里没有 `chan->dev->device.bus = ...`**。整个 `drivers/dma/` 里
> 也没有任何地方给它赋 bus（`02-datastructures.md` §5 已 grep 过）。
> 这就是"通道有 sysfs 节点、但永远不会被匹配到任何 driver"的**代码级证据**——
> `.class` 有、`.bus` 无。

`:1074` `chan->client_count = 0;` 和 `:1075` `device->chancnt++;`：
**通道号由 `chan_ida` 分配，通道数由 `chancnt` 计数，两者独立**。
所以卸载时也要分别还（A7）。

错误回滚是标准的倒序 goto 阶梯（`:1079-1086`），注意最后一步
`:1085` `chan->local = NULL;` —— **把指针置空了**。记住这个细节，A7 会用到。

### §A2.3 段6：挂链表前的"补引用"

`:1255-1269` 这段是全函数最难读的地方：

```c
	if (dmaengine_ref_count && !dma_has_cap(DMA_PRIVATE, device->cap_mask))
		list_for_each_entry(chan, &device->channels, device_node) {
			/* if clients are already waiting for channels we need
			 * to take references on their behalf                   :1257-1259 */
			if (dma_chan_get(chan) == -ENODEV) { ... goto err_out; }
		}
```

含义：如果**在本控制器注册之前**已经有 async_tx 客户端调过
`dmaengine_get()`（全局计数 `dmaengine_ref_count`，`:61`），
那么新来的公共通道要**追平**这些既有客户端的引用。
这就是 `balance_ref_count`（`:413-421`）那个 `while` 循环的同一件事，
B2 会把五个计数器一起讲。

`:1271-1272`：`DMA_PRIVATE` 控制器在这里就把 `privatecnt++`
并注释 `/* Always private */` —— 它永远不进 percpu 表。
最后 `:1270` `list_add_tail_rcu` 挂进 `dma_device_list`、
`:1273` `dma_channel_rebalance()`、`:1276` `dmaengine_debug_register(device)`。

### §A2.4 段6b：`err_out` 的回滚缺口

```c
 err_out:
	/* if we never registered a channel just release the idr */
	if (!device->chancnt) {                        /* :1282 */
		ida_free(&dma_ida, device->dev_id);    /* :1283 */
		return rc;
	}
	list_for_each_entry(chan, &device->channels, device_node) {
		if (chan->local == NULL) continue;      /* :1288-1289 */
		mutex_lock(&dma_list_mutex);
		chan->dev->chan = NULL;                 /* :1291 */
		mutex_unlock(&dma_list_mutex);
		device_unregister(&chan->dev->device);  /* :1293 */
		free_percpu(chan->local);               /* :1294 */
	}
	return rc;                                      /* :1296 */
```

> 两个不对称，登记在 C1：
> 1. **`dev_id` 只在 `chancnt == 0` 时才还**（`:1282-1285`）。
>    已经注册过至少一个通道再失败时，`dma_ida` 里那个 id **泄漏**，
>    `device->chan_ida` 也没有 `ida_destroy`。
> 2. **这里不置 `chan->local = NULL`**（对比 `:1085` 置了）。
>    A7 的卸载路径靠 `chan->local == NULL` 当哨兵，所以这条路径走完之后
>    再走一次卸载会**二次 `free_percpu`**。实际不会发生（probe 失败后
>    驱动不会再调 unregister），但这是靠调用约定而非代码保证的。

---

## §A3 消费者 probe：申请一条通道

主入口 `dma_request_chan(dev, name)`（`drivers/dma/dmaengine.c:813-873`）。
完整调用链（每一跳都在源码里对上了）：

```
dma_request_chan(dev, "tx")                            dmaengine.c:813
  │
  ├─[1] dev->of_node 存在？                            :819
  │      └─ of_dma_request_slave_channel(np, "tx")      of-dma.c:251-308
  │           ├ of_property_match_string("dma-names")   of-dma.c:266  ← 找不到静默 -ENODEV
  │           ├ 轮转起点 atomic_inc_return(&last_index)  of-dma.c:281
  │           ├ of_parse_phandle_with_args("dmas", "#dma-cells")  of_dma_match_channel
  │           │                                         of-dma.c:226-242
  │           ├ of_dma_find_controller(&dma_spec)        of-dma.c:32-44
  │           │    └ 找不到 → pr_debug + 返回 NULL → 上层 -EPROBE_DEFER (of-dma.c:294)
  │           └ ofdma->of_dma_xlate(&dma_spec, ofdma)   ← 控制器自己的翻译函数
  │                ├ of_dma_simple_xlate      of-dma.c:321-335（多数控制器）
  │                ├ of_dma_xlate_by_chan_id  of-dma.c:351-371
  │                ├ pl330_of_xlate           pl330.c:2142
  │                └ of_dma_router_xlate      of-dma.c:55-103（B5 单独讲）
  │
  ├─[2] ACPI 路径 :823-824 ── arm64-only 范围外，已裁剪（99 §2.7）
  │
  ├─[3] -EPROBE_DEFER 直接上抛                          :826-827
  ├─[4] 拿到了就 goto found                             :829-830
  │
  ├─[5] 否则遍历 dma_device_list 查 filter map          :833-848
  │      dma_filter_match(d, name, dev)  :786-811
  │      └ find_candidate(d, &mask, d->filter.fn, map->param)  :656-689
  │
  └─ found:                                             :855
       ├ chan->dbg_client_name = "<设备名>:<dma-names 名>"  :857（CONFIG_DEBUG_FS）
       ├ chan->name            = "dma:<dma-names 名>"       :861
       ├ chan->slave           = dev                        :864
       └ 两条 sysfs 符号链接                              :866-870
```

### §A3.1 `found:` 标签处的两条符号链接

```c
	if (sysfs_create_link(&chan->dev->device.kobj, &dev->kobj, DMA_SLAVE_NAME))
		dev_warn(dev, "Cannot create DMA %s symlink\n", DMA_SLAVE_NAME);   /* :866-868 */
	if (sysfs_create_link(&dev->kobj, &chan->dev->device.kobj, chan->name))
		dev_warn(dev, "Cannot create DMA %s symlink\n", chan->name);       /* :869-870 */
```

方向相反的一对：`/sys/class/dma/dma0chan0/slave` → 消费者设备，
消费者设备目录下的 `dma:tx` → 通道。**这是唯一能从 sysfs 看出
"哪个设备占了哪条通道"的地方**（`07-debug.md` 会讲怎么用）。

`:862-863` 有个静默分支：`chan->name` 的 `kasprintf` 失败就
`return chan` —— **通道正常返回，但两条链接都没建，也不打日志**。

### §A3.2 `find_candidate` → `dma_chan_get`：真正"占用"的那一步

`find_candidate`（`:656-689`）里有一段注释解释了它为什么要临时改能力位：

```c
		/* We first set DMA_PRIVATE to disable balance_ref_count as this
		 * channel will not be published in the general-purpose allocator  :665-666 */
```

流程是：`private_candidate`（`:619-653`）按 mask + filter_fn 挑一个
`client_count == 0` 的通道 → 置 `DMA_PRIVATE` → `dma_chan_get`
→ 成功则 `privatecnt++`；失败则回滚能力位。
**找不到任何候选时返回 `ERR_PTR(-EPROBE_DEFER)`**（`:688`）。

`dma_chan_get`（`:446-486`）是引用计数的核心，五步：

| 步 | 代码 | 说明 |
|---|---|---|
| 1 | `:452-456` | 已有客户端 → 只 `__module_get` + `client_count++`，直接返回 0 |
| 2 | `:458-459` | `try_module_get(owner)` 失败 → `-ENODEV` |
| 3 | `:461-465` | `kref_get_unless_zero(&chan->device->ref)` 失败 → `-ENODEV` |
| 4 | `:467-472` | **首个客户端**才调 `device_alloc_chan_resources` |
| 5 | `:474-477` | `client_count++`；非 PRIVATE 则 `balance_ref_count` |

> 第 4 步是"申请资源"和"申请通道"的分界：一条被两个消费者共享的通道
> （极少见，需要 filter_fn 允许）**只会 alloc 一次硬件资源**。
> 反过来，A6 的释放也只在最后一个客户端离开时 free。

---

## §A4 一次传输：从 prep 到回调

这是运行期反复发生的那一步。核心在这条路上**几乎不做事** —— 它的角色是
"把 cookie 记好、把回调派发对"，真正的搬数据全在控制器驱动里。
下面按 **用了 virt-dma 的控制器**（如 `mtk-uart-apdma`、`bam_dma`）走，
不用 virt-dma 的（如 `pl330`）差异见 B3。

```
消费者                    核心 / virt-dma                        硬件
  │
  ①dmaengine_prep_slave_sg(chan, sgl, n, dir, flags)
  │   include/linux/dmaengine.h:975 → chan->device->device_prep_slave_sg(...)
  │        └──► 控制器分配自己的描述符，内嵌一个 struct virt_dma_desc
  │             然后调 vchan_tx_prep(vc, vd, tx_flags)   virt-dma.h:56-74
  │               ├ dma_async_tx_descriptor_init(&vd->tx, &vc->chan)  :61
  │               ├ vd->tx.tx_submit  = vchan_tx_submit   :63  提交入口在这里定死
  │               ├ vd->tx.desc_free  = vchan_tx_desc_free :64
  │               ├ vd->tx_result 预置为 DMA_TRANS_NOERROR / residue 0  :66-67
  │               └ list_add_tail(&vd->node, &vc->desc_allocated)  :70  【状态1 allocated】
  │
  ②dmaengine_submit(tx)   include/linux/dmaengine.h:1212 → tx->tx_submit(tx)
  │        └──► vchan_tx_submit()   virt-dma.c:19-36
  │               ├ cookie = dma_cookie_assign(tx)   drivers/dma/dmaengine.h:29  cookie 在这一刻才有
  │               └ list_move_tail(&vd->node, &vc->desc_submitted)  :29 【状态2 submitted】
  │
  ③dma_async_issue_pending(chan)  include/linux/dmaengine.h:1418
  │        └──► chan->device->device_issue_pending(chan)
  │               └ 控制器在自己的 issue_pending 里：
  │                   if (vchan_issue_pending(vc))  virt-dma.h:82-86
  │                        desc_submitted 整条 splice 到 desc_issued 【状态3 issued】
  │                        └──► 写硬件寄存器 ─────────────────────────────►│ 开始搬数据
  │
  │                                                             ◄──────────│ 完成中断
  │              控制器中断处理里（持 vc->lock）：
  │                vchan_cookie_complete(vd)   virt-dma.h:94-106
  │                  ├ dma_cookie_complete(&vd->tx)  drivers/dma/dmaengine.h:52  → completed_cookie
  │                  ├ list_add_tail(&vd->node, &vc->desc_completed)  【状态4 completed】
  │                  └ tasklet_schedule(&vc->task)   回调不在硬中断里跑
  │
  │              软中断上下文：vchan_complete()  virt-dma.c:83-110
  │                  ├ spin_lock_irq(&vc->lock)  :90
  │                  ├ desc_completed 整条 splice 到局部 head  :91
  │                  ├ 取出 cyclic 描述符（如果有）:92-98
  │                  ├ spin_unlock_irq  :99
  │                  ├ cyclic 回调  :101
  │                  └ 逐个：get_callback → list_del → invoke → vchan_vdesc_fini  :103-109
  ◄─┴ 你的 callback / callback_result 在这里被调用
```

### §A4.1 三个 cookie 事实

| 事实 | 代码 | 后果 |
|---|---|---|
| cookie 在 **submit** 时分配，不是 prep 时 | `drivers/dma/dmaengine.h:29-45` | prep 返回的描述符**还没有 cookie**，`tx->cookie` 此时是 0 |
| cookie 从 `DMA_MIN_COOKIE`(=1) 起单调递增，回绕后重置 | `include/linux/dmaengine.h:23`；`dmaengine.h:37-41` | 不能拿 cookie 当句柄用（`02-datastructures.md` §1.0 组4） |
| `dma_cookie_complete` 里有 `BUG_ON` | `drivers/dma/dmaengine.h:54` | `BUG_ON(tx->cookie < DMA_MIN_COOKIE)` —— **对没 submit 就 complete 的描述符直接 BUG** |

### §A4.2 回调派发：两种回调、一个 dummy

回调不是直接从描述符里取的，而是先复制到一个临时结构
`struct dmaengine_desc_callback`（`drivers/dma/dmaengine.h:98-102`），
由 `dmaengine_desc_get_callback`（`:113-120`）填、
`dmaengine_desc_callback_invoke`（`:131-147`）调。派发规则只有两行：

```c
	if (cb->callback_result) {                 /* :140 ← 优先 */
		if (!result) result = &dummy_result;/* :141-142 */
		cb->callback_result(cb->callback_param, result);
	} else if (cb->callback) {                 /* :144 ← 退化 */
		cb->callback(cb->callback_param);
	}
```

> **`callback_result` 存在时 `callback` 被完全忽略**，两者不会都调。
> 这是 `02-datastructures.md` §1.0 组5 那条"回调二选一"的执行点。
> 而"先复制到临时结构再调"的意义在于：**invoke 发生在锁外**
> （`virt-dma.c:99` 已 unlock），所以你的回调里可以再调
> `dmaengine_submit` 而不会自锁。

### §A4.3 `vchan_complete` 里那个 NULL 指针算术

```c
	vd = vc->cyclic;                                    /* :92  可能是 NULL */
	if (vd) { vc->cyclic = NULL; dmaengine_desc_get_callback(&vd->tx, &cb); }
	else    { memset(&cb, 0, sizeof(cb)); }             /* :96-98 */
	spin_unlock_irq(&vc->lock);
	dmaengine_desc_callback_invoke(&cb, &vd->tx_result);/* :101 vd 可能是 NULL */
```

**不是崩溃，但是未定义行为**：`&vd->tx_result` 在 `vd == NULL` 时只做
偏移量计算、不解引用；而 `cb` 被 memset 成全 0，所以 `:140` 和 `:144`
两个分支都不成立，那个非法指针**从头到尾没被读过**。
也就是说这段代码**靠被调方的实现细节兜住**，一旦
`dmaengine_desc_callback_invoke` 将来加一行 `result->residue`
之类的前置读取就会立刻炸。登记在 C1 第 ① 条。

### §A4.4 五个状态的完整迁移

`02-datastructures.md` §3.4 画了队列结构，这里补**谁负责搬**：

| 从 | 到 | 搬运者 | 代码 |
|---|---|---|---|
| （新建） | `desc_allocated` | `vchan_tx_prep` | `virt-dma.h:70` |
| `desc_allocated` | `desc_submitted` | `vchan_tx_submit` | `virt-dma.c:29` |
| `desc_submitted` | `desc_issued` | `vchan_issue_pending` | `virt-dma.h:84` |
| `desc_issued` | `desc_completed` | `vchan_cookie_complete` | `virt-dma.h:103` |
| `desc_completed` | 释放/复用 | `vchan_complete` → `vchan_vdesc_fini` | `virt-dma.c:108`；`virt-dma.h:112-125` |
| 任意 | `desc_terminated` | `vchan_terminate_vdesc` | `virt-dma.h:145-153` |
| `desc_terminated` | 释放 | `vchan_synchronize` | `virt-dma.h:211-225` |

`vchan_vdesc_fini`（`virt-dma.h:112-125`）是**复用机制的落点**：
带 `DMA_CTRL_REUSE` 的描述符不释放，而是 `list_add` **回到 `desc_allocated`
的头部**（`:120`），下次可以直接 submit。不带的走 `vc->desc_free(vd)`。

查询残留量时注意：`vchan_find_desc`（`virt-dma.c:66-76`）**只遍历
`desc_issued`**。所以对已经进了 `desc_completed` 但 tasklet 还没跑的描述符，
`device_tx_status` 查不到它 —— 但此时 `dma_cookie_status` 已经能返回
`DMA_COMPLETE`（`completed_cookie` 在 `:103` 之前的 `dma_cookie_complete`
里就更新了），所以两者是**互补而不是冲突**。

---

## §A5 终止与同步

这是 dmaengine 里最容易写错的一段，而核心提供的保证**比你以为的少**。

```
消费者要停下来
  │
  ├─ 方式1（推荐）dmaengine_terminate_sync(chan)   include/linux/dmaengine.h:1177-1188
  │      ├ dmaengine_terminate_async(chan)   :1129-1135
  │      │    └ chan->device->device_terminate_all(chan)
  │      │         └ 控制器里典型实现：
  │      │             ├ 关硬件 / 停当前搬运
  │      │             ├ vchan_get_all_descriptors(vc, &head)  virt-dma.h:177-185
  │      │             │    五条链表【全部】splice 走，含 desc_completed
  │      │             ├ vchan_terminate_vdesc(当前那个)  virt-dma.h:145-153
  │      │             └ vchan_dma_desc_free_list(vc, &head)  virt-dma.c:112-121
  │      └ dmaengine_synchronize(chan)   :1155-1161
  │           └ chan->device->device_synchronize(chan)
  │                └ 控制器里典型实现：vchan_synchronize(vc)  virt-dma.h:211-225
  │                     ├ tasklet_kill(&vc->task)   :216  真正的"等回调跑完"
  │                     └ 把 desc_terminated 清空
  │
  └─ 方式2（已废弃）dmaengine_terminate_all(chan)  :1100-1106  → 返回 -ENOSYS
```

### §A5.1 为什么 terminate 必须配 synchronize

`dmaengine_terminate_async` 的 kerneldoc（`include/linux/dmaengine.h:1108-1128`）
自己写清了：它**只保证不再启动新传输，不保证回调已经跑完**。
而 `dmaengine_synchronize` 的 kerneldoc（`:1137-1154`）加了一句更硬的：

> 在 `terminate_async` 之后、`synchronize` 返回之前，
> **对通道调用除 `synchronize` 以外的任何操作都是未定义行为**。

`dmaengine_synchronize` 本体只有六行（`:1155-1161`），第一行是
`might_sleep();`（`:1157`）—— 所以它**不能在原子上下文里调**。

### §A5.2 三个坑（按危险程度排）

| # | 坑 | 代码依据 |
|---|---|---|
| | `device_synchronize` **是可选回调**。控制器没实现时，`dmaengine_synchronize()` 是**彻底的静默空操作** —— 你以为回调排干了，其实没有 | `:1155-1161` 里 `if (chan->device->device_synchronize)` 是唯一的门 |
| | `vchan_get_all_descriptors` 把 **`desc_completed` 也 splice 走了**（`virt-dma.h:183`），所以"已完成但回调还没派发"的描述符会被直接释放 —— **它们的回调永远不会被调用** | `virt-dma.h:177-185` |
| | 两个 terminate 的错误码不一致：`dmaengine_terminate_all` 无回调时返回 `-ENOSYS`（`:1105`），`dmaengine_terminate_async` 返回 `-EINVAL`（`:1134`） | 登记在 C1 第 ② 条 |

第二条的实践含义：**terminate 之后不要指望"最后一次回调会告诉我搬了多少"**。
要残留量就在 terminate 之前调 `dmaengine_tx_status`，或者用
`vchan_terminate_vdesc` 保留的那份（这是控制器驱动的事，不是消费者的）。

---

## §A6 消费者 remove：释放通道

`dma_release_channel(chan)`（`drivers/dma/dmaengine.c:903-912`）只有六行，
但每一行都在做记账：

```c
	mutex_lock(&dma_list_mutex);                                     /* :905 */
	WARN_ONCE(chan->client_count != 1,
		  "chan reference count %d != 1\n", chan->client_count); /* :906-907 */
	dma_chan_put(chan);                                              /* :908 */
	/* drop PRIVATE cap enabled by __dma_request_channel() */         /* :909 */
	if (--chan->device->privatecnt == 0)
		dma_cap_clear(DMA_PRIVATE, chan->device->cap_mask);      /* :910-911 */
	mutex_unlock(&dma_list_mutex);
```

`:906-907` 那条 `WARN_ONCE` 的判据是 `!= 1`，**不是 `> 1`** ——
所以"释放一条根本没申请到的通道"（`client_count == 0`）也会告警。

`dma_chan_put`（`:494-518`）是 A3 第 5 步的镜像：

```c
	if (!chan->client_count) return;              /* :497-498 早退 */
	chan->client_count--;                         /* :500 */
	if (!chan->client_count && chan->device->device_free_chan_resources) {
		dmaengine_synchronize(chan);          /* :505 核心的兜底 */
		chan->device->device_free_chan_resources(chan);  /* :506 */
	}
	if (chan->router && chan->router->route_free) { ... }  /* :510-514 释放 router 路由 */
	dma_device_put(chan->device);                 /* :516 */
	module_put(dma_chan_to_owner(chan));          /* :517 */
```

> `:505` 这行是核心留给"忘了 synchronize 的消费者"的最后一道保险，
> 但它有**两个洞**：
> (a) 它包在 `:503` 的 `if (!chan->client_count && chan->device->device_free_chan_resources)`
> 里 —— **控制器没实现 `device_free_chan_resources` 时这一步根本不执行**；
> (b) 即使执行了，它也发生在消费者已经 `dma_unmap_sg` / `kfree` buffer **之后**，
> 救不了已经消失的内存。
> 所以正确写法永远是消费者自己在 remove 里显式
> `dmaengine_terminate_sync()` 再 `dma_release_channel()`。

`:510-514` 是 router 场景的收尾：`route_free` 被调用后
`chan->router` 和 `chan->route_data` 双双置 NULL，所以**同一条物理通道
下次可以被路由给另一个外设**。B5 讲这条路的来路。

---

## §A7 控制器 remove：注销

`dma_async_device_unregister(device)`（`drivers/dma/dmaengine.c:1307-1326`）：

| 序 | 动作 | 代码 |
|---|---|---|
| 1 | `dmaengine_debug_unregister(device)` | `:1311` |
| 2 | 逐通道 `__dma_async_device_channel_unregister` | `:1313-1314` |
| 3 | `dma_cap_set(DMA_PRIVATE, ...)` —— 注释说明是"确保正在拆的设备不会再进 channel_table" | `:1317-1321` |
| 4 | `dma_channel_rebalance()` | `:1322` |
| 5 | `ida_free(&dma_ida, device->dev_id)` | `:1323` |
| 6 | `dma_device_put(device)` | `:1324` |

第 2 步的 `__dma_async_device_channel_unregister`（`:1103-1119`）是本篇
**第二条设计债的所在地**：

```c
	if (chan->local == NULL) return;                        /* :1106-1107 哨兵 */
	WARN_ONCE(!device->device_release && chan->client_count,
		  "%s called while %d clients hold a reference\n",
		  __func__, chan->client_count);                /* :1109-1111 */
	mutex_lock(&dma_list_mutex);
	device->chancnt--;                                      /* :1113 */
	chan->dev->chan = NULL;                                 /* :1114 */
	mutex_unlock(&dma_list_mutex);
	ida_free(&device->chan_ida, chan->chan_id);             /* :1116 */
	device_unregister(&chan->dev->device);                  /* :1117 */
	free_percpu(chan->local);                               /* :1118 没置 NULL */
```

> 两点：
> 1. **`:1118` 之后没有 `chan->local = NULL;`**，而 `:1106` 的哨兵恰好是判
>    `chan->local == NULL`。也就是说这个哨兵**防不住重复调用** ——
>    对比 A2 错误路径 `:1085` 是明确置了 NULL 的。同一个字段、同一个语义、
>    两处写法不一致。
> 2. `:1109-1111` 的 `WARN_ONCE` 被 `!device->device_release` 门住：
>    **实现了 `device_release` 的控制器，带着活跃客户端卸载时一声不响**。
>    这是有意的（`device_release` 就是给"延迟到最后一个引用消失才真正释放"
>    准备的，见 `:1129-1136` 的 kerneldoc），但它意味着
>    "卸载时没有 WARN" ≠ "没有客户端还在用"。

注意第 2 步**不 kfree `chan->dev`** —— 那块内存由
`chan_dev_release`（`:232-237`）在 `device_unregister` 触发的最后一次
put 里释放。这是"通道注册在 class 上"带来的唯一好处：
生命周期交给了驱动模型。

### §A7.1 devm 版本：`dmaenginem_async_device_register`

`:1343-1362`。它把 `dma_async_device_unregister` 挂成 devres
（release 函数 `dmam_device_release` 在 `:1329-1335`），
于是控制器驱动**可以完全不写 `.remove`**。
pl330 用的是**非 devm 版**（`drivers/dma/pl330.c:3146` 注册、
`:3219` 在 remove 里手动 `of_dma_controller_free`），
所以两种写法在本树里都有活样本。

---

# Part B：横切机制

Part A 的每一步都会碰到下面这五件事，但它们不属于任何单独一步，
所以单独摊开。读法建议：**B1 和 B2 必读**（写控制器驱动必踩），
B3 决定你要不要看 virt-dma，B4/B5 是 arm64 上少见但会突然出现的路径。

## §B1 锁与并发：全子系统只有三把锁

| 锁 | 类型 | 定义 | 保护什么 | 谁在持着调回调 |
|---|---|---|---|---|
| `dma_list_mutex` | mutex | `drivers/dma/dmaengine.c:58` | `dma_device_list` + 所有 `client_count` / `privatecnt` / `table_count` / `dmaengine_ref_count` | 持着调 `device_alloc_chan_resources` 和 `device_free_chan_resources` |
| `of_dma_lock` | mutex | `drivers/dma/of-dma.c:21` | `of_dma_list`（xlate 注册表） | **持着调 xlate**（router 时是两级 xlate）—— 见下 |
| `vc->lock` | spinlock | `drivers/dma/virt-dma.h:27` | 单个虚拟通道的五条描述符链表 + `vc->cyclic` | 持着调 `vchan_*` 全族 |

### §B1.1 `dma_list_mutex` 是**唯一的全局锁**，而它锁得很粗

所有 `Must be called under dma_list_mutex.` 的 kerneldoc：
`balance_ref_count`（`:411`）、`dma_chan_get`（`:444`）、
`dma_chan_put`（`:492`）、`min_chan`（`:318`）、`dma_channel_rebalance`（`:359`）。
`dma_device_put` 干脆用 `lockdep_assert_held(&dma_list_mutex)`（`:436`）把这条
约定变成运行期断言。

> **后果一**：`device_alloc_chan_resources` 和 `device_free_chan_resources`
> 是在**全局 mutex 下**被调用的（A3 第 4 步、A6）。
> 所以控制器驱动在这两个回调里：
> - **可以睡眠**（mutex 不禁抢占）；
> - **绝不能反过来调 `dma_request_chan` / `dma_release_channel`** —— 立刻自死锁；
> - 长耗时操作（等 PLL、走 I2C）会**卡住整机所有其它 DMA 通道的申请**。
>
> **后果二**：`dma_request_chan` 自己**不碰** `dma_list_mutex`。两条路径各自补锁：
> DT 路径先取 `of_dma_lock`（`of-dma.c:288`），再由 xlate 内部的
> `__dma_request_channel`（`:755-783`）或 `dma_get_slave_channel`（`:697-724`）
> 补上 `dma_list_mutex`；filter-map 兜底路径则是 `dma_request_chan` 自己
> 就地取 `dma_list_mutex`（`:833`），查表 + `find_candidate`（`:844`）
> 都在锁内，`:848` 才放。
> 所以本子系统的锁序是固定的 **`of_dma_lock` → `dma_list_mutex`**，
> 反向嵌套（持 `dma_list_mutex` 再进 `of_dma_*`）全树不存在。

### §B1.2 `of_dma_lock` 是**持着调回调**的

`of_dma_request_slave_channel` 里这一段要逐行看，因为 xlate 在锁内还是锁外
决定了控制器驱动能写什么：

```c
	mutex_lock(&of_dma_lock);                              /* :288 */
	ofdma = of_dma_find_controller(&dma_spec);             /* :289 */

	if (ofdma) {
		chan = ofdma->of_dma_xlate(&dma_spec, ofdma);  /* :292 锁内 */
	} else {
		ret_no_channel = -EPROBE_DEFER;                /* :294 */
		chan = NULL;
	}

	mutex_unlock(&of_dma_lock);                            /* :298 */
```

三条后果：

| # | 后果 | 依据 |
|---|---|---|
| | **xlate 里绝不能再调 `of_dma_controller_register` / `of_dma_controller_free`** —— 两者都取同一把非递归 mutex（`of-dma.c:157`、`:166`），立刻自死锁 | `:288` vs `:157`/`:166` |
| | xlate 里**可以睡眠**（mutex 不禁抢占），但整机所有 DT 通道申请被这把锁串行化 —— 一个慢 xlate 会拖住其它设备的 `dma_request_chan` | `:288`/`:298` |
| | 因为 xlate 在锁内，`of_dma_controller_free`（`of-dma.c:153-167`）**不可能与正在执行的 xlate 并发** —— 这里没有 `ofdma` 的 use-after-free 窗口 | `:157`/`:166` 同锁 |

`of_dma_find_controller`（`of-dma.c:32-44`）自己**一行锁都不加**，
锁完全由调用者负责 —— 这也解释了 router 路径为什么能在
`of_dma_router_xlate` 里再调一次它（`of-dma.c:69`）而不死锁：
那时 `of_dma_lock` 已经在 `:288` 被外层持住了（B5）。
但这条约定**没有任何机制保护**：函数既没有 kerneldoc 声明"必须持
`of_dma_lock`"，也没有 `lockdep_assert_held` —— 对比 `dmaengine.c` 里
六处显式 kerneldoc（`:152`/`:318`/`:359`/`:411`/`:444`/`:492`）
加一处 `lockdep_assert_held`（`:436`），差距很明显。登记在 C1 第 ⑪ 条。

### §B1.3 `vc->lock`：唯一一处 `spin_lock_irq` 与 `spin_lock_irqsave` 的分工

| 用法 | 出现位置 | 为什么 |
|---|---|---|
| `spin_lock_irqsave` | `virt-dma.h:70`、`:119`、`:193`、`:218`；`virt-dma.c:26`、`:55` | 调用者上下文不确定（可能已关中断） |
| `spin_lock_irq` | **只有** `virt-dma.c:90`（`vchan_complete` 内） | 它是 tasklet，**保证**在开中断的软中断上下文里跑，不需要存旧状态 |

`spin_lock_init(&vc->lock)` 在 `vchan_init`（`virt-dma.c:127`）。

另一条约定藏在 kerneldoc 里：`vchan_issue_pending`（`virt-dma.h:80`）、
`vchan_cookie_complete`（`virt-dma.h:92`）、`vchan_get_all_descriptors`
（`virt-dma.h:172`）都写着 `vc.lock must be held by caller` ——
**这三个函数自己不加锁**，锁必须由控制器驱动加。
而 `vchan_tx_prep` / `vchan_vdesc_fini` / `vchan_synchronize`
是**自己加锁**的。两组混用是 virt-dma 驱动最常见的 bug 来源。

## §B2 引用计数：五个计数器，各管一件事

这是 dmaengine 最反直觉的部分 —— **同一条通道上同时有五个数在动**。

| 计数器 | 位置 | 语义 | 谁加 | 谁减 |
|---|---|---|---|---|
| `device->ref`（kref） | `include/linux/dmaengine.h` 的 `dma_device` | `struct dma_device` 本体的生命周期 | `kref_init` `:1232`；`dma_chan_get` 里 `kref_get_unless_zero` `:461` | `dma_device_put` `:434-438`（`:482`/`:516`/`:1324`） |
| `chan->client_count` | `dma_chan` | 这条通道有几个客户端 | `dma_chan_get` `:454`/`:474` | `dma_chan_put` `:500` |
| `device->privatecnt` | `dma_device` | 有几条通道被私有占用 | `find_candidate` 成功时；`:1272`（PRIVATE 控制器恒 +1） | `dma_release_channel` `:910` |
| `chan->table_count` | `dma_chan` | 这条通道被 percpu 表引用了几次 | `min_chan` `:347` | `dma_channel_rebalance` `:377` 直接清 0 |
| `dmaengine_ref_count` | 全局 `:61` | 有几个 async_tx 客户端调过 `dmaengine_get` | `dmaengine_get` `:939` | `dmaengine_put` `:977`（`:978` 有 `BUG_ON(< 0)`） |

### §B2.1 模块引用和 kref 是**两套**，而且必须成对

`dma_chan_get`（`:446-486`）里每次 `client_count++` 都伴随一次
`__module_get`/`try_module_get`（`:453`/`:458`），
`dma_chan_put` 里 `:517` 对应一次 `module_put`。
所以 **`client_count` 和控制器模块的引用计数是 1:1 绑死的** ——
这就是"通道在用时控制器模块无法卸载"的实现方式。

`balance_ref_count`（`:413-421`）的那个 `while` 正是为了维持这个 1:1：

```c
	while (chan->client_count < dmaengine_ref_count) {
		__module_get(owner);         /* :418 */
		chan->client_count++;        /* :419 */
	}
```

**只对非 PRIVATE 通道生效**（`dma_chan_get:476-477` 的判断），
因为 PRIVATE 通道不参与 async_tx 的公共池。

### §B2.2 `device_release` 是"延迟释放"的钩子

`dma_device_release`（`:423-432`）在最后一个 kref 消失时跑：

```c
	list_del_rcu(&device->global_node);   /* :427 */
	dma_channel_rebalance();             /* :428 */
	if (device->device_release)
		device->device_release(device);/* :430-431 */
```

这解释了 A7 里那条被门住的 `WARN_ONCE`：实现了 `device_release`
的控制器可以在还有客户端时安全 unregister —— 结构体的实际释放会
**推迟到最后一个 `dma_chan_put` 之后**。没实现的就必须自己保证时序，
核心只能 WARN 一句（`:1109-1111`），
而 `dma_async_device_register` 里对这件事的提醒是 `dev_dbg`（`:1228-1230`）——
C1 会评这个组合。

## §B3 用不用 virt-dma：两条并存的实现路线

virt-dma 是**可选**公共层：全树 51 个文件 include 了 `virt-dma.h`，
而 **`drivers/dma/pl330.c` 不在其中**（arm64 上最常见的控制器之一）。

| | 用 virt-dma（如 `mtk-uart-apdma`、`bam_dma`） | 不用（如 `pl330`） |
|---|---|---|
| 描述符队列 | 五条链表由 virt-dma 定义 | 驱动自己定义（pl330 有自己的 `work_list` 等） |
| cookie 记账 | `vchan_tx_submit` / `vchan_cookie_complete` 全包 | 驱动自己调 `dma_cookie_assign` / `dma_cookie_complete`（`drivers/dma/dmaengine.h:29`/`:52`） |
| 回调派发 | virt-dma 的 tasklet（`virt-dma.c:83-110`） | 驱动自己起 tasklet 并调 `dmaengine_desc_get_callback_invoke`（`drivers/dma/dmaengine.h:159`） |
| `device_synchronize` | 直接挂 `vchan_synchronize` | pl330 **两个都没有**：既无 `device_synchronize` 也无 `device_resume` |
| 代码量 | `virt-dma.c` 142 行 + `virt-dma.h` 227 行帮你省掉大半 | pl330 3277 行 |

> 结论：**virt-dma 不是核心的一部分，是一个"要不要用"的选择**。
> 它在 `drivers/dma/Kconfig:47` 有独立的 `DMA_VIRTUAL_CHANNELS`
> （裸 `tristate`，由驱动 `select`），编译产物是独立的 `virt-dma.o`
> （`drivers/dma/Makefile:8`）。
> 读别人的控制器驱动时，**第一件事就是看它 include 没 include `virt-dma.h`**
> —— 这决定了后面 2000 行怎么读。

## §B4 percpu 通道表与 rebalance：一套 arm64 slave 场景用不到的机制

这套机制服务的是 **async_tx**（RAID / memcpy offload），不是 slave DMA。

### §B4.1 三段式

```
dmaengine_get()           :932-965   ← async_tx 客户端"进场"，ref_count++
   └─ 抓走所有非 PRIVATE 通道的引用 :942-956
   └─ 首次进场时 dma_channel_rebalance() :962-963

dma_channel_rebalance()   :361-390
   ├ 清空整张表           :368-371   per_cpu_ptr(...)->chan = NULL
   ├ 清空所有 table_count :373-378
   ├ if (!dmaengine_ref_count) return;  :380-382  ← 没客户端就不填表
   └ 逐 cap × 逐 online cpu：chan = min_chan(cap, cpu)  :385-389

min_chan(cap, cpu)        :320-350
   ├ 跳过不具备该 cap 的、以及所有 DMA_PRIVATE 的控制器 :328-330
   ├ 跳过 client_count == 0 的通道                  :332-333
   ├ 全局最小 table_count → min；NUMA 本地最小 → localmin :334-340
   ├ 优先 localmin                                     :344
   └ 选中者 table_count++                              :346-347
```

`dma_chan_is_local`（`:302-307`）的判据是
`dev_to_node(chan->device->dev)` 等于 `NUMA_NO_NODE` **或**
CPU 属于该 node。arm64 单 node 平台上 `NUMA_NO_NODE` 恒成立，
所以 `localmin` 总是等于 `min` —— 这段 NUMA 逻辑在多数 arm64 板子上是空转。

### §B4.2 为什么 slave 场景完全用不到它

三重原因，缺一不可：

1. **表里没有 `DMA_SLAVE` 槽位** —— A1.1 的三行 `clear_bit`（`:273-275`）。
2. **slave 控制器通常置 `DMA_PRIVATE`**，而 `min_chan:329` 和
   `dma_channel_rebalance:374` 都跳过 PRIVATE。
3. **`dmaengine_ref_count` 为 0 时表压根不填**（`:380-382`），
   而 slave 消费者走 `dma_request_chan`，**从不调 `dmaengine_get`**。

> 所以：在一台只跑 slave DMA 的 arm64 机器上，
> `channel_table` 全是 NULL，`dma_find_channel()` / `dma_issue_pending_all()`
> （`:554-568`）这一族 API **一个都不会被调到**。
> 读代码时可以整块跳过 —— 但要知道它为什么在那里，
> 否则会以为 `dma_request_chan` 和它有关系。

## §B5 router 路径：一层 DMA 请求线的交换机

有些 SoC 的外设数量超过 DMA 控制器能直连的请求线数
（`Documentation/devicetree/bindings/dma/dma-router.yaml` 的原话：
"Some SoCs (like TI DRA7x) have more peripherals integrated with DMA requests
than what the DMA controller can handle directly"），于是中间插一个
**crossbar / dmamux**。

DT 上表现为消费者的 `dmas` 指向 **router 节点**而不是控制器节点，
router 节点自己再用 `dma-masters` 指向真控制器。

```
消费者 DT: dmas = <&sdma_xbar 33 0 1>;
                     │
                     ▼
   sdma_xbar: dma-router@...  ← of_dma_router_register(of-dma.c:185-213)
              │                  把 ofdma->of_dma_xlate 写成 of_dma_router_xlate (:202)
              │                  所以对上层完全透明：还是走同一个 of_dma_xlate 指针
              ▼
   of_dma_router_xlate(of-dma.c:55-103)
     ① memcpy 出一份 dma_spec_target                        :64
     ② ofdma->of_dma_route_allocate(&dma_spec_target, ofdma) :65
        └ router 驱动在这里【改写 dma_spec_target】：把 np 换成真控制器、
          把参数换成分配到的那条物理请求线；返回 route_data
     ③ of_dma_find_controller(&dma_spec_target)             :69
        └ 找不到 → route_free + ERR_PTR(-EPROBE_DEFER)      :70-75
     ④ ofdma_target->of_dma_xlate(...)  ← 真控制器的 xlate   :77
     ⑤ 成功：chan->router / chan->route_data 记账            :84-85
        + 可选 device_router_config 回调                     :87-88
        失败：route_free 释放路由                            :78-80
     ⑥ of_node_put(dma_spec_target.np)                       :101
```

三个要点：

1. **`chan->router` / `chan->route_data`（`include/linux/dmaengine.h:348-349`）
   是路由的唯一凭据**，A6 的 `dma_chan_put:510-514` 靠它释放。
2. `struct dma_router` 只有两个字段（`include/linux/dmaengine.h:304-307`）：
   `dev` 和 `route_free`。**没有 `route_allocate`** ——
   分配函数是注册时单独传的（`of_dma_router_register` 的第二个参数），
   存在 `struct of_dma` 里。
3. `:66-67`：`of_dma_route_allocate` 返回错误时，
   **错误码被压成 `return NULL`**。上层 `of_dma_request_slave_channel`
   把 NULL 当成"这一项没成"继续轮转，最终返回 `-EPROBE_DEFER`（`of-dma.c:294`）。
   也就是说**router 分配失败在消费者看来永远是 defer，永远不是真错误**。
   登记在 C1 第 ⑩ 条。

arm64 上的 router 实例（本树）：`drivers/dma/stm32-dmamux.c`、
`drivers/dma/ti/dma-crossbar.c`、`drivers/dma/lpc18xx-dmamux.c`。
`06-devicetree.md` 会给 DTS 写法。

---

# Part C：评价

## §C1 源码设计债

**先给一个反常识的事实**：把
`TODO` / `FIXME` / `REVISIT` / `XXX` / `HACK` 在
`drivers/dma/dmaengine.c`、`drivers/dma/dmaengine.h`、`drivers/dma/of-dma.c`、
`drivers/dma/virt-dma.c`、`drivers/dma/virt-dma.h`、`include/linux/dmaengine.h`
六个核心文件里全扫一遍 —— **零命中**。

所以本节**不是"抄注释"**，而是逐条读出来的。为了让读者能分辨可信度，
分成两类：

### §C1.1 作者自己承认的（一等，有注释/文档为证）

| # | 内容 | 出处 |
|---|---|---|
| 甲 | `device_release` 缺失时"unbind 不安全" | `include/linux/dmaengine.h:843-846` 的 kerneldoc + `dmaengine.c:1228-1230` |
| 乙 | cookie 辅助函数"要求调用者自己加锁防并发" | `drivers/dma/dmaengine.h:27`、`:50` 的两条 `Note:` |
| 丙 | `DMA_ASYNC_TX` 不该由驱动设置 | `include/linux/dmaengine.h:48-49` 的 `Note:` |
| 丁 | metadata 区在某些时点后失效 | `include/linux/dmaengine.h:259` 的 `Note:` |
| 戊 | 两种 metadata 模式不兼容，客户端必须二选一 | `include/linux/dmaengine.h:279` 的 `Note:` |
| 己 | 官方文档自陈的两个空白：`- TODO: What is it about?` 和 `TODO: Can it be modified by the driver itself, or` | `Documentation/driver-api/dmaengine/provider.rst:223`、`:415` |

### §C1.2 代码读出来的（二等，本篇的判断）

| # | 问题 | 代码 | 严重度 |
|---|---|---|---|
| ① | `vchan_complete` 在 `vd == NULL` 时对 NULL 做指针算术（`&vd->tx_result`），靠被调方不解引用兜住 | `virt-dma.c:92`/`:101` + `drivers/dma/dmaengine.h:140`/`:144` | UB，不崩 |
| ② | `dmaengine_terminate_all` 无回调返 `-ENOSYS`，`dmaengine_terminate_async` 返 `-EINVAL` | `include/linux/dmaengine.h:1105` vs `:1134` | 语义不一致 |
| ③ | 注册关卡只覆盖 16 个能力位中的 9 个；`DMA_SLAVE` 等 5 个无检查 | `dmaengine.c:1153-1214` vs `include/linux/dmaengine.h:51-70` | 故障延后到运行期 |
| ④ | `bam_dma` 把 `src_addr_widths` 赋成裸枚举值 `DMA_SLAVE_BUSWIDTH_4_BYTES`(=4)，而该字段语义是位掩码；歪打正着（`4 == BIT(2)`） | `drivers/dma/qcom/bam_dma.c:1349`，对比正确写法 `drivers/dma/mediatek/mtk-uart-apdma.c:517` | 驱动侧 |
| ⑤ | `device_synchronize` 是可选回调 → `dmaengine_synchronize()` 可能是**静默空操作** | `include/linux/dmaengine.h:1155-1161` | 本子系统最危险的可选回调 |
| ⑥ | `enum dma_status` 的 kerneldoc 漏了 `DMA_OUT_OF_ORDER` | `include/linux/dmaengine.h:31-36` vs `:42` | 文档残缺 |
| ⑦ | "不安全"告警用 `dev_dbg` 发出，正文却写着 `"WARN: ..."` | `dmaengine.c:1228-1230` | 产品内核上等于没有 |
| ⑧ | `dma_async_device_register` 的 `err_out` 在 `chancnt != 0` 时不还 `dev_id`、不 `ida_destroy(&device->chan_ida)` | `dmaengine.c:1280-1296` | 泄漏（仅 probe 失败路径） |
| ⑨ | `__dma_async_device_channel_unregister` 释放 `chan->local` 后不置 NULL，而 `:1106` 的哨兵正是判它为 NULL；同一字段在 `:1085` 是置了的 | `dmaengine.c:1118` vs `:1085`/`:1106` | 写法不一致 |
| ⑩ | router 的 `of_dma_route_allocate` 失败时错误码被压成 `NULL` → 消费者只会看到 `-EPROBE_DEFER` | `of-dma.c:66-67` + `:294` | 误导排查方向 |
| ⑪ | `of_dma_find_controller` 要求调用者持 `of_dma_lock`（两个调用点 `:289`/`:69` 都依赖这一点），但既无 kerneldoc 声明也无 `lockdep_assert_held`；同树 `dmaengine.c` 对 `dma_list_mutex` 做了 6 处 kerneldoc + 1 处断言 | `of-dma.c:32-44` vs `dmaengine.c:436` | 约定无保护 |

一条**观测能力的缺口**（不算 bug 但影响排查）：
`drivers/dma/` 的核心三文件里**没有任何 tracepoint** ——
`include/trace/events/` 下与 DMA 相关的只有 `tegra_apb_dma.h`（单个驱动私有）
和 `dma_fence.h`（GPU 同步，与本子系统无关）。
所以 dmaengine 的可观测面只有 **debugfs + `dev_dbg`/`dev_vdbg` + sysfs 三样**，
`07-debug.md` 会展开。

## §C2 官方文档与代码的冲突

| # | 文档说 | 代码是 | 以谁为准 |
|---|---|---|---|
| 1 | `Documentation/driver-api/dmaengine/provider.rst` 大量使用 `dma_async_device_register` 的旧式流程叙述 | 本版本已有 devm 变体 `dmaenginem_async_device_register`（`dmaengine.c:1343-1362`）和逐通道动态注册 API（`:1089`/`:1121`） | 代码；文档未覆盖动态注册 |
| 2 | `struct dma_slave_config` 的 `direction` 字段 | kerneldoc 自己标了 **DEPRECATED**（`include/linux/dmaengine.h:389-392`），但仍有大量驱动在读 | 以 kerneldoc 为准：新代码不要用 |
| 3 | `dma-common.yaml` 定义了 `dma-channels` 和 `dma-requests` | **核心一行都不读** —— 只有约 20 / 19 个驱动各自解析 | 代码；见 `06-devicetree.md` §1.1 |
| 4 | `provider.rst` 用 `- TODO: What is it about?`（`:223`）描述某个能力字段 | 代码里该字段有明确语义 | 代码 |

一个**不算冲突但值得点名**的：`Documentation/driver-api/dmaengine/client.rst`
（379 行）把 "terminate + synchronize" 讲得很清楚，
但**没有提 `device_synchronize` 可选**这件事（C1 第 ⑤ 条）。
文档描述的是"应该"，代码允许的是"可以不"。

## §C3 设计取舍评价

### §C3.1 三个做得好的地方

| 取舍 | 好在哪 | 代价 |
|---|---|---|
| **cookie 而不是描述符指针作为查询句柄** | 提交后描述符可以立刻被核心/驱动回收复用，消费者手上那个整数永远安全 | 不能用 cookie 反查描述符；回绕要靠 `dma_async_is_complete` 的三值比较（`include/linux/dmaengine.h:1457-1468`） |
| **prep / submit / issue_pending 三段分离** | 可以攒一批描述符再一次性下发（省寄存器写、省中断），也让"提交"和"启动"能在不同上下文 | 三步都可能失败，且 prep 失败只返回 NULL 不带错误码（`01-api.md` §11 已列） |
| **virt-dma 做成可选层而不是核心** | 简单控制器（`mtk-uart-apdma` 全文 656 行）能白拿队列+tasklet；复杂控制器（pl330）不被强塞 | 两套实现风格并存，读驱动前必须先判断走哪条（B3） |

### §C3.2 三个代价明显的地方

1. **`dma_list_mutex` 一把全局锁管一切**（B1.1）。
   在通道申请/释放很频繁的系统上是明确的串行点，而且它持着调用
   `device_alloc_chan_resources` —— 一个慢控制器能拖住整机。
   对比 clk 的做法（两把锁 + 可重入 owner 追踪），dmaengine 这里明显更粗糙。
   合理的辩解是：**通道申请是 probe 期一次性动作**，热路径（A4）完全不碰这把锁。

2. **能力位与回调指针的一致性只靠注册时的 9 个 `if`**（C1 第 ③ 条）。
   更彻底的做法是像 clk 那样按类型给出必选矩阵，或者干脆把 16 个位
   全部生成检查。现在这个状态是"查了一半"，比全不查更容易让人误信。

3. **静默失败偏多**。本篇一路数下来至少四处：
   prep 返回 NULL 不带错误码、`dmaengine_synchronize` 无回调时空操作（⑤）、
   router 分配失败压成 defer（⑩）、`chan->name` 分配失败导致 sysfs 链接
   不建也不报（`:862-863`）。
   这形成了一条可迁移的经验：**在 dmaengine 上，"调用返回成功"
   的信息量低于你的直觉** —— 关键动作后要么回读状态，要么用 debugfs 确认。

### §C3.3 和另外两个子系统的横向对比

| 维度 | dmaengine | clk | pinctrl |
|---|---|---|---|
| 有无 `bus_type` | 无（但有 `class`） | 无 | 无 |
| 有无 sysfs 节点 | **有**：`/sys/class/dma/dmaNchanM/` | 无 | 有（devices 下） |
| 有无 tracepoint | **无** | 有（`include/trace/events/clk.h`） | 无 |
| 锁结构 | 一把全局 mutex + 每通道 spinlock | 两把（mutex + spinlock）+ 可重入 owner | 一把 mutex |
| 提供方注册失败的检查密度 | 中（9/16 能力位） | 高（按类型的必选矩阵） | 中 |
| 消费者拿到的句柄 | `struct dma_chan *`（**共享对象**，不是 per-get 副本） | `struct clk *`（**per-get 副本**） | `struct pinctrl *` |

最后一行是三者最大的结构差异：**clk 给每个消费者发一份独立 handle，
dmaengine 直接把共享的 `dma_chan` 指针给你**。
所以在 dmaengine 上"我改我自己的配置不影响别人"这件事**不成立** ——
`dmaengine_slave_config` 写的就是那条通道上唯一的 `dma_slave_config`
（`02-datastructures.md` §1.0 组2 已把这点摊开）。

---

## §C4 读到这里之后去哪

| 想继续了解 | 去哪篇 |
|---|---|
| 每个 API 的签名和错误码语义 | `01-api.md` |
| 结构体字段、五组易混关系、队列图 | `02-datastructures.md` |
| pl330 / bam_dma / mtk-uart-apdma 怎么实现回调，dmatest 怎么用 | `04-extensions.md` |
| 消费者/提供方/router 的代码骨架 | `05-usage.md` |
| `dmas` / `dma-names` / router 的 DTS 写法与排错 | `06-devicetree.md` |
| debugfs `summary`、sysfs 三个属性、错误码分诊 | `07-debug.md` |
| 为什么 cookie 不是句柄、为什么要 synchronize（大白话） | `08-feynman.md` |
| 本篇每条论断的取证范围 | `99-evidence.md` |
