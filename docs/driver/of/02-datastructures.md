---
title: 数据结构
---

# of 02：核心数据结构与它们的关系

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §1.0 先分清五组关系

> 这个子系统的难度**不在代码**，在于"DT 里的一个东西"和"内核里的一个对象"
> 之间那层不直观的对应。本节先否掉五个错误理解。

### §1.0.A `device_node` / `platform_device` / `struct device` 三者的关系

> **这是本子系统最容易被类比错的一组关系。**

**错误理解**：「`device_node` 就是设备，`platform_device` 是它的一个包装」。

| 断言 | 对不对 | 依据 |
|---|---|---|
| `device_node` 是一个"设备" | **错** —— 它是**硬件的一段描述**，只有 name / phandle / 属性链 / 父子兄弟指针 | `include/linux/of.h:52-72` |
| 每个 `device_node` 都有对应的 `platform_device` | **错** —— **只有被"填充"过的才有**（§1.0.A.2） |
| `platform_device` 里有 `device_node` | **有一个指针** —— 在它内嵌的 `struct device` 里 | `include/linux/device.h:619` 的 `dev->of_node` |
| `device_node` 里有指回 `platform_device` 的指针 | **没有** —— 反查要用 `of_find_device_by_node()`（01 §9.1） |
| 三者是"同一个硬件的三种视角" | **对** | 见下 |

**正确的说法**：

```
   device_node      ── "DTS 里写了什么"          ── 生命周期：整个开机期间（几乎不释放）
   platform_device  ── "内核把它当成一个设备"      ── 生命周期：从 populate 到 depopulate
   struct device    ── "设备模型里的那个节点"      ── 内嵌在 platform_device 里
```

**唯一的连接点是三行赋值**（`of_device_alloc()` 里）：

| 赋值 | 位置 | 含义 |
|---|---|---|
| `dev->dev.of_node = of_node_get(np)` | `drivers/of/platform.c:144` | **device 持有 node 的一份引用** |
| `dev->dev.fwnode = &np->fwnode` | `drivers/of/platform.c:145` | 通用 fwnode 视图（01 §10） |
| `dev->dev.parent = parent ? : &platform_bus` | `drivers/of/platform.c:146` | **父设备不一定对应 DT 的父节点**（§1.0.A.3） |

#### §1.0.A.1 方向是单向的

`struct device` → `device_node` 有指针（`include/linux/device.h:619`），
反过来**没有**。
⇒ 从 DT 节点找设备只能用 `of_find_device_by_node()`（`drivers/of/platform.c:55`），
它**遍历 platform 总线**去找 —— 而且**会 `get_device()`，用完要 `put_device()`**。

#### §1.0.A.2 "哪些节点会变成 `platform_device`"

**不是所有节点** —— 规则是：

| 条件 | 依据 |
|---|---|
| ① 节点 `status` 可用 | `of_device_is_available(np)`（`drivers/of/platform.c:175`） |
| ② 还没被填充过（`OF_POPULATED` 标志） | `of_node_test_and_set_flag(np, OF_POPULATED)`（`drivers/of/platform.c:176`） |
| ③ **它的父节点必须是"被认可的总线"** | 父节点的 compatible 在 `of_default_bus_match_table[]` 里（`drivers/of/platform.c:24-32`） |

**那张白名单只有四项**（`drivers/of/platform.c:24-32`）：

| compatible | 行号 |
|---|---|
| `"simple-bus"` | `drivers/of/platform.c:25` |
| `"simple-mfd"` | `drivers/of/platform.c:26` |
| `"isa"` | `drivers/of/platform.c:27` |
| `"arm,amba-bus"`（`CONFIG_ARM_AMBA`） | `drivers/of/platform.c:29` |

⇒ **这就是"我的设备节点下面的子节点为什么没变成 platform 设备"的答案**：
你的节点的 compatible 不在这张表里 ⇒ 填充在你这一层就停了。
要继续往下填得自己调 `devm_of_platform_populate()`（01 §9.1、06 篇）。

#### §1.0.A.3 ⚠️`dev->parent` 不一定对应 DT 的父节点

`of_device_alloc()` 的 `parent` 参数是**调用者传的**（`drivers/of/platform.c:146`）。
`of_platform_populate()` 递归时会传上一层的 `struct device`，
所以**大多数情况下**两者一致 ——
⚠️ 但 `of_platform_device_create(np, bus_id, NULL)` 传 NULL 时，
父设备就是 `&platform_bus`（根），**和 DT 的树形无关**。

### §1.0.B phandle 不是指针

**错误理解**：「`<&clkctrl>` 是取那个节点的地址」。

| 断言 | 实际 |
|---|---|
| phandle 是地址 | **错** —— `typedef u32 phandle`，它是一个**标签** |
| DTS 源码里就有这个数 | **错** —— **dtc 编译时**给每个被引用的节点分配一个 `phandle` 属性，`&foo` 被替换成那个值 |
| 解析 phandle 很快 | **需要一次查找**（`of_find_node_by_phandle()`，`drivers/of/base.c:1274`）—— 有哈希缓存优化 |
| 一个 phandle 值全局唯一 | dtc 保证；**overlay 场景要靠 `drivers/of/resolver.c` 重定位** |

**"为什么不会发生"的代码依据**：`struct device_node` 里的 `phandle` 字段
（`include/linux/of.h:54`）是一个 **u32**，不是指针 ——
⇒ 它和 `parent`/`child`/`sibling`（`include/linux/of.h:60-62`，**真正的指针**）
在同一个结构里，对比很明显。

**参数个数由被指向的节点决定**（01 §5.2）：
`clocks = <&clkctrl 5>` 里那个 `5` 有几个 cell，看 `clkctrl` 节点的 `#clock-cells`。
⇒ **这是 DT 里最重要的约定**，也是 `of_parse_phandle_with_args()` 要传
`cells_name` 的原因。

### §1.0.C "引用计数"这个词在这里是误导的

**错误理解**：「`device_node` 有引用计数，所以忘了 `of_node_put()` 会泄漏」。

| 配置 | `of_node_get/put` 是什么 | 忘了 put 的后果 |
|---|---|---|
| `!CONFIG_OF_DYNAMIC` | **空操作**（`include/linux/of.h:126-130`） | **什么都不会发生** |
| `CONFIG_OF_DYNAMIC` = y | 真的 `kref` | **节点永不释放** |

**arm64 上 `OF_DYNAMIC` 是否开着取决于配置** ——
它被 `OF_OVERLAY` 之类 `select`（`drivers/of/Kconfig:57`）。

⇒ **正确的态度**：**永远配平**，因为
① 你不知道用户的配置；
② `OF_UNITTEST` 会抓不平衡（`drivers/of/dynamic.c:471` 那条 `pr_err`）；
③ 代码可读性 —— 配平的代码才能看出"这个引用管到哪里"。

**两条抓 bug 的日志**：

| 日志 | 位置 | 含义 |
|---|---|---|
| `ERROR: memory leak, expected refcount 1 instead of %d, of_node_get()/of_node_put() unbalanced …` | `drivers/of/dynamic.c:471` | **put 少了** |
| `ERROR: Bad of_node_put() on %pOF` | `drivers/of/dynamic.c:336` | **put 多了**（计数归零后又 put） |

### §1.0.D `reg` 不是 CPU 物理地址

**错误理解**：「节点的 `reg = <0x12340000 0x1000>` 就是那个寄存器的物理地址」。

| 断言 | 实际 |
|---|---|
| `reg` 是 CPU 物理地址 | **不一定** —— 它是**相对父总线的地址** |
| 要翻译才能用 | **对** —— 沿父链走每一层的 `ranges` |
| 大多数情况下恰好相等 | **对** —— 因为 `simple-bus` 通常有 `ranges;`（空的 = 一一映射） |

**翻译的实现**：`of_translate_address()`（`drivers/of/address.c:620`）——
03 篇会逐段剖析。
驱动看到的 `struct resource` 已经翻译好了
（`of_device_alloc()` 里循环调 `of_address_to_resource()`，
`drivers/of/platform.c:138-141`）。

⚠️ **"翻译"这一步用到两个数**：
`#address-cells` 和 `#size-cells`（`of_n_addr_cells()` / `of_n_size_cells()`，
`drivers/of/base.c:108` / `:129`）——
它们**写在父节点上**，决定 `reg` 里每一项占几个 u32。
⇒ 写错了的表现是**地址解析出一个完全不相关的值**（06 篇会给判据）。

### §1.0.E DT 是"硬件描述"不是"驱动配置"

**错误理解**：「DT 是一个配置文件，我可以往里放驱动需要的任何参数」。

| 断言 | 实际 |
|---|---|
| DT 描述"硬件长什么样" | **对**（这是设计意图，见 `Documentation/devicetree/usage-model.rst`） |
| DT 可以放驱动的策略参数（如超时时间、缓冲区大小） | **不该** —— 那是驱动的实现细节，换驱动就变了 |
| DT 的绑定是**稳定 ABI** | **是** —— 一旦一个属性进了上游绑定，**老 DTB 必须继续能工作** |

**这条约定的实际后果**（06 篇会展开）：

| 后果 | 说明 |
|---|---|
| 加属性要**向后兼容** | 新属性缺失时驱动必须有合理的默认行为 |
| 不能改属性的语义 | 老 DTB 里同名属性的含义必须不变 |
| 厂商私有属性要带前缀 | `vendor,my-quirk` |

⚠️ **这不是本子系统的代码能强制的** —— 它是**社区约定**。
⇒ 代码层面唯一的体现是"缺属性时返回 `-EINVAL` 而不是崩掉"（01 §14.2）。

---

## §2 核心抽象与三方的关系

### §2.1 一张图看清四方分工

```
   ┌── bootloader ───────────────────────────────────────────────┐
   │  把 DTB（扁平二进制）放进内存，把地址传给内核                 │
   └────────────────────────┬───────────────────────────────────┘
                            │
   ┌────────────────────────▼───────────────────────────────────┐
   │  早期解析（drivers/of/fdt.c）                                │
   │  · 扫描 /memory、/chosen、reserved-memory                    │
   │  · unflatten：把扁平 DTB 展开成 device_node 树               │
   └────────────────────────┬───────────────────────────────────┘
                            │
   ┌────────────────────────▼───────────────────────────────────┐
   │  DT 核心（drivers/of/base.c + property.c + address.c）       │
   │  · of_root / of_chosen / of_aliases                          │
   │  · 查找 · 遍历 · 属性读取 · phandle · 地址翻译                │
   └───────┬───────────────────────────────┬───────────────────┘
           │                               │
   ┌───────▼──────────────────┐  ┌─────────▼──────────────────────┐
   │  设备填充                  │  │  驱动直接读                     │
   │  drivers/of/platform.c    │  │  of_property_read_u32(…)        │
   │  ⇒ platform_device         │  │  of_parse_phandle(…)            │
   │  ⇒ platform_bus_type       │  │  of_iomap(…)                    │
   └───────┬──────────────────┘  └────────────────────────────────┘
           │
   ┌───────▼──────────────────────────────────────────────────────┐
   │  设备模型：platform_bus_type 按 of_match_table 匹配驱动         │
   └───────────────────────────────────────────────────────────────┘
```

| 关键位置 | 行号 |
|---|---|
| `of_root` / `of_chosen` / `of_aliases` / `of_stdout` | `drivers/of/base.c:35` / `:37` / `:39` / `:40` |
| unflatten 的入口 | `drivers/of/fdt.c:467` 的 `of_fdt_unflatten_tree()` |
| 设备填充 | `drivers/of/platform.c:465` 的 `of_platform_populate()` |
| `dev->dev.bus = &platform_bus_type` | `drivers/of/platform.c:186` |
| 地址翻译进 `resource` | `drivers/of/platform.c:138-141` |

**两条独立的路**：
① **填充路**（DT → `platform_device` → 驱动 probe）；
② **直读路**（驱动在 probe 里自己 `of_property_read_*()`）。
⇒ 一个典型驱动**两条都走**：靠 ① 被 probe 起来，靠 ② 读自己的私有属性。

### §2.2 ⚠️ of **没有** `bus_type`，但它**创造** platform 设备

| 判据 | 结果 |
|---|---|
| `drivers/of/` 里有 `struct bus_type` 吗 | **没有** |
| 那 `platform_device` 挂在哪条总线上 | `platform_bus_type`（`drivers/of/platform.c:186` 那行赋值） |
| ⇒ of 的定位 | **它是 platform 总线的"设备来源"，不是一条总线** |

**对比 remoteproc**：那套也没有 `bus_type`，但它**创造 virtio 设备**
（那套的 04 §1）。⇒ **两者是同一类角色：某条总线的设备来源。**

⚠️ **区别**：remoteproc 造的设备来自**固件里的资源表**，
of 造的设备来自**bootloader 传的 DTB** ——
⇒ 两者都是"外部数据驱动的设备创建"，这是 08 篇会用的一个类比。

---

## §3 核心操作数据流

### §3.1 启动路径：DTB → `platform_device`

```
   ① bootloader 把 DTB 地址放进寄存器
        │
   ② arch/arm64/kernel/setup.c → setup_machine_fdt()
        │  early_init_dt_scan()  ← drivers/of/fdt.c
        │    · 扫 /chosen 拿 bootargs
        │    · 扫 /memory 建 memblock
        │    · 扫 reserved-memory
        ▼
   ③ unflatten_device_tree()   ← drivers/of/fdt.c
        │  · 遍历扁平 DTB，为每个节点 kzalloc 一个 device_node
        │  · 建 parent/child/sibling 链
        │  · of_root / of_chosen / of_aliases 就位
        ▼
   ④ of_core_init()            ← drivers/of/base.c
        │  · 建 /sys/firmware/devicetree/
        ▼
   ⑤ of_platform_default_populate(NULL, NULL, NULL)
        │  （由 arch 的 arch_initcall 或 driver core 调）
        │  · 从 of_root 开始递归
        │  · 对每个可用节点 of_platform_device_create_pdata()
        │  · 父节点 compatible 在白名单里才继续下钻
        ▼
   ⑥ platform_bus_type 按 of_match_table 匹配 ⇒ 驱动 probe
```

| 关键位置 | 行号 |
|---|---|
| unflatten 入口 | `drivers/of/fdt.c:467` |
| `of_platform_default_populate()` | `drivers/of/platform.c:504` |
| `of_platform_populate()` | `drivers/of/platform.c:465` |
| 白名单表 | `drivers/of/platform.c:24-32` |
| `of_platform_bus_create()`（递归的那一层） | `drivers/of/platform.c:343` |
| 检查 `OF_POPULATED_BUS` | `drivers/of/platform.c:368` |
| `of_dev_lookup()`（auxdata 覆盖） | `drivers/of/platform.c:293`（调用于 `:374`） |
| 设 `OF_POPULATED_BUS` | `drivers/of/platform.c:401` · `:490` |
| `of_platform_device_create_pdata()` | `drivers/of/platform.c:167` |
| `of_device_alloc()` | `drivers/of/platform.c:112` |

### §3.2 `of_device_alloc()` 做的五件事

```c
	dev = platform_device_alloc("", PLATFORM_DEVID_NONE);

	/* ① 数一数有几个 reg */
	while (of_address_to_resource(np, num_reg, &temp_res) == 0)
		num_reg++;

	/* ② 把每个 reg 翻译成 struct resource */
	if (num_reg) {
		res = kcalloc(num_reg, sizeof(*res), GFP_KERNEL);
		dev->num_resources = num_reg;
		dev->resource = res;
		for (i = 0; i < num_reg; i++, res++) {
			rc = of_address_to_resource(np, i, res);
			WARN_ON(rc);
		}
	}

	/* ③④⑤ 建立三方联系 + 起名字 */
	dev->dev.of_node = of_node_get(np);
	dev->dev.fwnode = &np->fwnode;
	dev->dev.parent = parent ? : &platform_bus;
	if (bus_id) dev_set_name(&dev->dev, "%s", bus_id);
	else        of_device_make_bus_id(&dev->dev);
```

| 步骤 | 行号 |
|---|---|
| `platform_device_alloc("", PLATFORM_DEVID_NONE)` | `drivers/of/platform.c:120` |
| ① 数 reg | `drivers/of/platform.c:125-126` |
| ② 翻译成 resource | `drivers/of/platform.c:129-142`（`of_address_to_resource` 在 `:139`，`WARN_ON` 在 `:140`） |
| ③ `of_node = of_node_get(np)` | `drivers/of/platform.c:144` |
| ④ `fwnode` | `drivers/of/platform.c:145` |
| ⑤ `parent` | `drivers/of/platform.c:146` |
| 起名字 | `drivers/of/platform.c:148-151` |
| `of_device_make_bus_id()` | `drivers/of/platform.c:99-101` 那一带（拼 `full_name` 和父节点名） |

**注意 ② 里没有中断** —— `interrupts` 不在这里翻译！
⇒ **中断资源是 `of_device_add()` 之后由 `platform_get_irq()` 惰性解析的**
（irq 那套 06 §1.3、01 §3.1）。
这是一个真实的设计差异：**地址是 eager 的、中断是 lazy 的**
（因为中断控制器可能还没 probe ⇒ 要能返回 `-EPROBE_DEFER`）。

### §3.3 `of_platform_device_create_pdata()` 的六步

| 步骤 | 动作 | 行号 |
|---|---|---|
| ① | `!of_device_is_available(np)` 或**已被填充** ⇒ 返回 NULL | `drivers/of/platform.c:175-177` |
| ② | `of_device_alloc()` | `drivers/of/platform.c:179` |
| ③ | 设 `coherent_dma_mask = DMA_BIT_MASK(32)` | `drivers/of/platform.c:183-185` |
| ④ | **`dev->dev.bus = &platform_bus_type`** | `drivers/of/platform.c:186` |
| ⑤ | `of_msi_configure()` | `drivers/of/platform.c:188` |
| ⑥ | `of_device_add()`；失败则 `platform_device_put()` + 清 `OF_POPULATED` | `drivers/of/platform.c:190-193` · `:197-198` |

⚠️ **③ 那个 32 位默认 DMA 掩码** 是一个真实的坑：
很多 arm64 设备需要更宽的掩码，驱动必须自己
`dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64))`。

**① 里的 `of_node_test_and_set_flag(np, OF_POPULATED)` 是幂等保护**：
同一个节点被填充两次时第二次直接返回 NULL（不是错误）。
⇒ 所以 `devm_of_platform_populate()` 可以安全地对已经填充过的子树调用。

---

## §4 命名约定速查

| 前缀 / 名字 | 含义 | ⚠️ 易混点 |
|---|---|---|
| `device_node` | **DT 里的一个节点** | ⚠️ **不是 `struct device`** |
| `property` | 节点上的一个属性 | —— |
| `phandle` | **u32 标签**（不是指针） | §1.0.B |
| `of_find_*` | **全树**查找，会 get 引用 | ⚠️ 用完要 put |
| `of_get_*` | 从已知节点出发，**多数会 get 引用** | ⚠️ `of_get_property()` **例外**（返回属性值，不 get） |
| `of_property_read_*` | **类型化读取**（已处理字节序） | 首选 |
| `of_parse_phandle*` | 解析引用 | 返回的 `args.np` 要 put |
| `of_node_get/put` | 引用计数 | ⚠️`!OF_DYNAMIC` 下是空操作（§1.0.C） |
| `of_n_addr_cells` / `of_n_size_cells` | **沿父链**找 `#address-cells`/`#size-cells` | ⚠️ 不是读本节点的 |
| `of_translate_address` | 走 `ranges` 翻译 | §1.0.D |
| `of_platform_*` | 设备填充 | —— |
| `of_device_*` | 匹配 / DMA / modalias（**从 `struct device` 出发**） | ⚠️ 和 `of_device_is_*`（从 `device_node` 出发）名字很像 |
| `of_device_is_*` | 从 `device_node` 出发的判定 | 同上 |
| `of_fwnode_*` | fwnode 适配层 | 不是给驱动的（01 §10） |
| `of_graph_*` | 端口图 | 媒体/显示专用 |
| `of_changeset_*` / `of_overlay_*` | 动态修改 | arm64 上通常不开 |
| `OF_*`（`OF_DYNAMIC` / `OF_POPULATED` / …） | `device_node._flags` 的位 | §5.3 |
| `%pOF` | **printk 格式符：打印节点全路径** | 调试时非常有用（07 篇） |

**`%pOF` 值得单独记**：`pr_err("... %pOF\n", np)` 会打出
`/soc/uart@12340000` 这样的完整路径。
⇒ `drivers/of/dynamic.c:336` 和 `drivers/of/platform.c:229` 都在用它。

---

## §5 结构体逐个看

### §5.1 `struct device_node`（`include/linux/of.h:52-72`）

| 字段 | 行号 | 要点 |
|---|---|---|
| `name` | `include/linux/of.h:53` | 节点名，**不含 `@unit-address`** |
| `phandle` | `include/linux/of.h:54` | **u32 标签**（§1.0.B） |
| `full_name` | `include/linux/of.h:55` | **含 `@unit-address`**（如 `uart@12340000`） |
| `fwnode` | `include/linux/of.h:56` | **内嵌的通用视图** —— `of_fwnode_handle()` 就是取它的地址（`include/linux/of.h:172`） |
| `properties` | `include/linux/of.h:58` | 属性单链表 |
| `deadprops` | `include/linux/of.h:59` | **被删除的属性**（overlay 撤销时要还原） |
| `parent` / `child` / `sibling` | `include/linux/of.h:60-62` | **树形结构就是这三个指针** |
| `kobj` | `include/linux/of.h:64` | `CONFIG_OF_KOBJ` —— `/sys/firmware/devicetree/` 和引用计数 |
| `_flags` | `include/linux/of.h:66` | `OF_*` 位（§5.3） |
| `data` | `include/linux/of.h:67` | 给使用者挂私有数据（很少用） |
| `unique_id` / `irq_trans` | `include/linux/of.h:69-70` | **`CONFIG_SPARC` 专用**，arm64 不存在 |

**`name` 和 `full_name` 的区别**：
DTS 里写 `uart@12340000 { ... }` ⇒ `name = "uart"`、`full_name = "uart@12340000"`。
⇒ `of_node_name_eq()`（`drivers/of/base.c:71`）比的是 `name`（忽略 unit-address）。

⚠️ **引用计数藏在 `kobj` 里** ——
所以 `!CONFIG_OF_KOBJ` 时 `of_node_get/put` 也没有真正的计数载体（§1.0.C）。

### §5.2 `struct property`（`include/linux/of.h:32-46`）

| 字段 | 行号 | 要点 |
|---|---|---|
| `name` | `include/linux/of.h:33` | 属性名 |
| `length` | `include/linux/of.h:34` | **字节数**（不是元素个数！） |
| `value` | `include/linux/of.h:35` | **原始字节，大端** |
| `next` | `include/linux/of.h:36` | 单链表 |
| `_flags` | `include/linux/of.h:38` | `OF_DYNAMIC` / SPARC |
| `unique_id` | `include/linux/of.h:41` | SPARC |
| `attr` | `include/linux/of.h:44` | `CONFIG_OF_KOBJ` —— sysfs 里那个属性文件 |

**`length` 是字节数**这一点是 `-EOVERFLOW` 的来源（01 §14.2）：
`of_property_read_u32_array(np, "foo", buf, 4)` 要求 `length >= 16`。

### §5.3 `_flags` 的六个位（`include/linux/of.h:145-150`）

| 位 | 值 | 行号 | 含义 |
|---|---|---|---|
| `OF_DYNAMIC` | 1 | `include/linux/of.h:145` | 这个节点（和属性）是 `kmalloc` 出来的（overlay / changeset 建的） |
| `OF_DETACHED` | 2 | `include/linux/of.h:146` | 已从树上摘掉 |
| `OF_POPULATED` | 3 | `include/linux/of.h:147` | **已经为它创建过设备** |
| `OF_POPULATED_BUS` | 4 | `include/linux/of.h:148` | **已经为它的子节点创建过设备** |
| `OF_OVERLAY` | 5 | `include/linux/of.h:149` | 属于某个 overlay |
| `OF_OVERLAY_FREE_CSET` | 6 | `include/linux/of.h:150` | 正在被释放的 overlay cset 里 |

| 访问器 | 位置 |
|---|---|
| `of_node_check_flag(n, flag)` | `include/linux/of.h:190`（有 OF）· `:798`（无 OF） |
| `of_node_set_flag(n, flag)` | `include/linux/of.h:201` · `:809` |
| `of_node_clear_flag(n, flag)` | `include/linux/of.h:206` · `:813` |
| `of_node_test_and_set_flag()` | 填充时的幂等保护（`drivers/of/platform.c:176`） |
| `OF_IS_DYNAMIC(x)` / `OF_MARK_DYNAMIC(x)` 宏 | `include/linux/of.h:255-256` |

**`OF_POPULATED` 和 `OF_POPULATED_BUS` 是两个不同的位**：
前者说"这个节点自己变成设备了"，后者说"这个节点的孩子们被填充过了"。
⇒ `of_platform_depopulate()` 用它们判断该拆哪些
（`drivers/of/platform.c:616` · `:620`）。

### §5.4 `struct of_phandle_args` / `of_phandle_iterator`

| 结构 | 位置 | 内容 |
|---|---|---|
| `of_phandle_args` | `include/linux/of.h:75` | `np`（**带一份引用，要 put**）+ `args_count` + `args[16]` |
| `of_phandle_iterator` | `include/linux/of.h:81` | 迭代状态 |
| `MAX_PHANDLE_ARGS` | `include/linux/of.h:74` | **16** |

### §5.5 `struct of_device_id`

定义在 `include/linux/mod_devicetable.h`，不在 `of.h` 里。
四个字段：`name` / `type` / `compatible` / **`data`**（`of_device_get_match_data()` 取的就是它，01 §6）。

---

## §6 全局变量与锁

### §6.1 全部四个全局节点指针 + 一个链表

| 变量 | 位置 | 内容 |
|---|---|---|
| `of_root` | `drivers/of/base.c:35` | 根节点 |
| `of_chosen` | `drivers/of/base.c:37` | `/chosen` |
| `of_aliases` | `drivers/of/base.c:39` | `/aliases` |
| `of_stdout` | `drivers/of/base.c:40` | `stdout-path` 指向的节点 |
| `aliases_lookup` | `drivers/of/of_private.h:41` | `/aliases` 解析出来的 `struct alias_prop` 链表 |
| `struct alias_prop` | `drivers/of/of_private.h:24` | 一条别名（stem + id + 目标节点） |

`of_root` / `of_chosen` **都导出了**（`drivers/of/base.c:36` / `:38`）
⇒ 模块可以直接用，**不需要也不应该 put**。

### §6.2 两把锁

| 锁 | 位置 | 类型 | 保护什么 |
|---|---|---|---|
| `devtree_lock` | `drivers/of/base.c:56` | **`raw_spinlock_t`** | **树结构的读写**（`properties` 链、`parent/child/sibling`） |
| `of_mutex` | `drivers/of/base.c:51` | **mutex** | **changeset / overlay 的串行化** |

**为什么读树也要拿 spinlock**：
`drivers/of/base.c:217-219`（`of_find_property()` 里）用的是
`raw_spin_lock_irqsave(&devtree_lock, flags)` ——
⇒ 因为**中断上下文也可能读 DT**（如中断控制器的初始化路径）。

典型用法（`of_find_all_nodes()`，`drivers/of/base.c:262`）：
加锁在 `drivers/of/base.c:255`，解锁在 `:259`。

⚠️ **`devtree_lock` 是 raw spinlock 意味着**：
持它时**不能睡**、不能分配内存（`GFP_KERNEL`）。
⇒ 所以 changeset 那套要用 `of_mutex` 在外层串行化，
在锁外准备好节点和属性，只在最后一小段持 `devtree_lock` 改指针。

`of_mutex` 声明在 `drivers/of/of_private.h:40`（给 `dynamic.c` / `overlay.c` 用）。

---

## §7 结构关系总图

```
   全局
   ┌──────────────────────────────────────────────────────────────┐
   │  of_root ──▶ device_node（"/"）                               │
   │  of_chosen ──▶ device_node（"/chosen"）                       │
   │  of_aliases ──▶ device_node（"/aliases"）                     │
   │  aliases_lookup ──▶ alias_prop ──▶ …                          │
   │  devtree_lock（raw_spinlock）· of_mutex（mutex）               │
   └──────────────────────────────────────────────────────────────┘

   一个节点
   ┌─── struct device_node ──────────────────────────────────────┐
   │  name = "uart"        full_name = "uart@12340000"            │
   │  phandle = 0x1f       u32 标签，不是指针                  │
   │  fwnode               内嵌的通用视图                      │
   │  _flags               OF_POPULATED / OF_POPULATED_BUS / …    │
   │  kobj                 引用计数 + /sys/firmware/devicetree/ │
   │                                                              │
   │  parent ──▶ device_node（父）                                 │
   │  child ──▶ device_node ──sibling──▶ device_node ──▶ …         │
   │                                                              │
   │  properties ──▶ property ──next──▶ property ──▶ …             │
   │                  · name = "reg"                              │
   │                  · length = 8      字节数                 │
   │                  · value ──▶ [大端字节] 要转换             │
   │  deadprops ──▶ （被删的属性，overlay 撤销用）                  │
   └──────────────────────────────────────────────────────────────┘
              ▲
              │ dev->of_node（单向！带一份引用）
   ┌──────────┴──────────────────────────────────────────────────┐
   │  struct platform_device                                      │
   │    dev（struct device）                                       │
   │      · of_node ──▶ 上面那个节点        唯一的连接          │
   │      · fwnode  ──▶ &np->fwnode                               │
   │      · bus     ──▶ platform_bus_type   挂在这条总线上      │
   │      · parent  ──▶ 上一层的 device（不一定对应 DT 父节点）      │
   │    num_resources / resource[] ──▶ reg 翻译后的地址         │
   │    ⚠️ 中断不在这里 —— platform_get_irq() 惰性解析              │
   └──────────────────────────────────────────────────────────────┘
```

**图里最该盯住的四条边**：
1. `dev->of_node` 是**单向**的（§1.0.A.1）——反查要遍历总线。
2. `property.value` 是**大端原始字节**（§5.2）——必须用类型化接口读。
3. `phandle` 是 **u32 标签**，不是指针（§1.0.B）。
4. `resource[]` 是 `reg` **翻译后**的；**中断不在里面**（§3.2）。

---

**下一篇**：03 篇按时间线剖析 DTB 解析、unflatten、地址翻译、设备填充，
以及两把锁和 phandle 缓存。
