---
title: 内部实现
---

# of 03：实现剖析

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 导航

### §0.1 本篇只承诺一条主轴，其余显式分区

| 分区 | 主轴 | 章节号 |
|---|---|---|
| **Part A** | **时间线**：从 bootloader 传 DTB 到驱动 probe | §A1 … §A8 |
| **Part B** | **横切机制**：两把锁、phandle 缓存、地址翻译、`_flags` 状态 | §B1 … §B6 |
| **Part C** | **评价**：源码设计债与整体判断 | §C1 … §C6 |

### §0.2 分层图

```
   ┌── bootloader ─────────────────────────────────────────────┐
   │  DTB（扁平二进制）+ 起始地址                                 │
   └──────────────────────────┬───────────────────────────────┘
                              │
   ┌──────────────────────────▼───────────────────────────────┐
   │  早期扫描层  drivers/of/fdt.c（+ scripts/dtc/libfdt）       │
   │  early_init_dt_verify / _scan / unflatten_device_tree      │
   └──────────────────────────┬───────────────────────────────┘
                              │
   ┌──────────────────────────▼───────────────────────────────┐
   │  树访问层  drivers/of/base.c + property.c                  │
   │  查找 · 遍历 · 属性 · phandle（+ 哈希缓存）                  │
   └──────────────────────────┬───────────────────────────────┘
                              │
   ┌──────────────────────────▼───────────────────────────────┐
   │  资源翻译层  drivers/of/address.c + irq.c                  │
   │  of_translate_address（走 ranges）· of_irq_parse_raw        │
   └──────────────────────────┬───────────────────────────────┘
                              │
   ┌──────────────────────────▼───────────────────────────────┐
   │  设备填充层  drivers/of/platform.c + device.c              │
   │  ⇒ platform_device ⇒ platform_bus_type ⇒ 驱动 probe        │
   └───────────────────────────────────────────────────────────┘
```

### §0.3 生命周期时间线

```
   ① bootloader 把 DTB 地址交给内核
       │
   §A1 early_init_dt_verify()          校验 magic / 记下 initial_boot_params
       │
   §A2 early_init_dt_scan()            扫 /chosen · /（root）· /memory
       │   early_init_fdt_scan_reserved_mem()   reserved-memory
       │
   §A3 unflatten_device_tree()         扁平 → device_node 树
       │
   §A4 of_core_init()                  建 sysfs + 填 phandle 缓存
       │
   §A5 of_platform_default_populate()  树 → platform_device
       │
   §A6 驱动 probe：读属性 / 翻译地址 / 取中断
       │
   §A7 运行期：of_find_* / of_parse_phandle（只读）
       │
   §A8 拆除：of_platform_depopulate（极少用）
```

### §0.4 哪些内容不在本篇

| 内容 | 去哪 |
|---|---|
| 130 个 API 的签名与错误码 | 01 篇 |
| 结构体字段、五组关系、两把锁的清单 | 02 篇 |
| overlay / changeset / graph / reserved-memory / kobj 这五个独立框架 | 04 篇 |
| 怎么写驱动 / 怎么写 DTS | 05 篇 |
| DTS 的语法、单元格约定、常见属性 | 06 篇 |
| `/sys/firmware/devicetree/`、`dtc -I dtb`、调试手段 | 07 篇 |
| 「为什么是这样」的大白话解释 | 08 篇 |
| 中断解析的完整机制 | **irq 那套的 06 篇** |

---

# Part A：时间线

## §A1 校验：`early_init_dt_verify()`

| 元素 | 位置 |
|---|---|
| `early_init_dt_verify()` | `drivers/of/fdt.c:1271` |
| `initial_boot_params` | `drivers/of/fdt.c:473`（带 `__ro_after_init`） |

它做两件事：`fdt_check_header()` 校验 magic 和版本，
然后把指针存进 `initial_boot_params`（**全局，之后所有早期扫描都读它**）。

**`__ro_after_init` 说明一件事**：DTB 的地址在 init 阶段之后不再变。

## §A2 早期扫描：还没有 `device_node` 的时候

**关键约束**：此刻**内存分配器还没就绪**（连 `kmalloc` 都不能用），
所以只能**直接在扁平 DTB 上遍历**（用 `scripts/dtc/libfdt` 的 `fdt_*` 函数）。

```
   early_init_dt_scan(params)                     drivers/of/fdt.c:1307
     ├─ early_init_dt_verify(params)              drivers/of/fdt.c:1271
     └─ early_init_dt_scan_nodes()                drivers/of/fdt.c:1288
          ├─ early_init_dt_scan_chosen(cmdline)   drivers/of/fdt.c:1151
          │    ⇒ bootargs · initrd · rng-seed
          ├─ early_init_dt_scan_root()            drivers/of/fdt.c:1060
          │    ⇒ 记下根节点的 #address-cells / #size-cells
          └─ early_init_dt_scan_memory()          drivers/of/fdt.c:1096
               ⇒ memblock_add() 每一段物理内存
```

| 元素 | 位置 |
|---|---|
| `early_init_dt_scan()` | `drivers/of/fdt.c:1307` |
| `early_init_dt_scan_nodes()` | `drivers/of/fdt.c:1288` |
| `early_init_dt_scan_chosen()` | `drivers/of/fdt.c:1151` |
| `early_init_dt_scan_root()` | `drivers/of/fdt.c:1060` |
| `early_init_dt_scan_memory()` | `drivers/of/fdt.c:1096` |
| `early_init_dt_scan_chosen_stdout()` | `drivers/of/fdt.c:1009` |
| `early_init_fdt_scan_reserved_mem()` | `drivers/of/fdt.c:630` |
| `early_init_fdt_reserve_self()` | `drivers/of/fdt.c:654` |
| `early_init_dt_reserve_memory()` | `drivers/of/fdt.c:479` |

**`early_init_dt_scan_root()` 的输出被后面所有地址解析依赖**：
它记下根节点的 `#address-cells` / `#size-cells`（arm64 上通常都是 2）。

**`early_init_fdt_reserve_self()`（`drivers/of/fdt.c:654`）很容易被忽略**：
它把 **DTB 自己占的那块内存**也 reserve 掉 ——
否则 memblock 可能把 DTB 所在的内存分配给别人，
而后面 `unflatten_device_tree()` 还要读它。

## §A3 unflatten：扁平 → 树

```
   unflatten_device_tree()                        drivers/of/fdt.c:1327
     └─ __unflatten_device_tree(initial_boot_params, NULL, &of_root,
                                &kernel_tree_alloc, false)   drivers/of/fdt.c:1329
          │
          ├─【第一遍】unflatten_dt_nodes(blob, NULL, dad, NULL)
          │    · 只数需要多少内存（dryrun）
          ├─ 一次性 alloc 那么大一块
          └─【第二遍】unflatten_dt_nodes(blob, mem, dad, mynodes)
               · 在那块内存里依次构造 device_node + property
               · unflatten_dt_alloc() 从这块里切
```

| 元素 | 位置 |
|---|---|
| `unflatten_device_tree()` | `drivers/of/fdt.c:1327` |
| 调 `__unflatten_device_tree()` | `drivers/of/fdt.c:1329` |
| `__unflatten_device_tree()` | `drivers/of/fdt.c:365`（doc 注释在 `:350`） |
| `unflatten_dt_nodes()` | `drivers/of/fdt.c:284` |
| `unflatten_dt_alloc()` | `drivers/of/fdt.c:96` |
| `unflatten_and_copy_device_tree()` | `drivers/of/fdt.c:1349` |
| `of_fdt_unflatten_tree()`（对外，overlay 用） | `drivers/of/fdt.c:461`（导出于 `:467`） |

**"两遍扫描 + 一次大分配"是这个函数的核心设计**：

| 好处 | 说明 |
|---|---|
| 只 alloc 一次 | 启动早期分配器很脆弱，少调用少出错 |
| 整棵树在一块连续内存里 | 局部性好；**而且可以整块释放**（overlay 场景） |
| ⇒ `unflatten_dt_alloc()` 只是"从这块里切一段" | `drivers/of/fdt.c:96` |

**`unflatten_and_copy_device_tree()`（`drivers/of/fdt.c:1349`）的用途**：
DTB 在一块**之后会被回收**的内存里时（如某些 bootloader 的暂存区），
先把 DTB 拷到内核内存再展开。

⚠️ **展开之后 `device_node` 树和扁平 DTB 就没关系了** ——
两者是**两份独立的数据**。
⇒ 这就是 07 篇里"`dtc -I dtb` 反编译的是 DTB，
`/sys/firmware/devicetree/` 反映的是树"这个区别的来源。

## §A4 `of_core_init()`：建 sysfs + 填 phandle 缓存

| 元素 | 位置 |
|---|---|
| `of_core_init()` | `drivers/of/base.c:166`（声明在 `include/linux/of.h:155`，空实现在 `:448`） |
| 填 phandle 缓存的循环 | `drivers/of/base.c:181-182` |

那两行是：
```c
		if (np->phandle && !phandle_cache[of_phandle_cache_hash(np->phandle)])
			phandle_cache[of_phandle_cache_hash(np->phandle)] = np;
```
⇒ **遍历全树，把每个有 phandle 的节点放进哈希缓存**（§B3）。

它还建 `/sys/firmware/devicetree/base/`（`drivers/of/kobj.c`，04 §5）。

## §A5 设备填充

```
   of_platform_default_populate(NULL, NULL, NULL)     drivers/of/platform.c:504
     └─ of_platform_populate(root, of_default_bus_match_table, lookup, parent)
          │                                          drivers/of/platform.c:465
          ├─ root = root ? : of_find_node_by_path("/")
          ├─ for_each_available_child_of_node(root, child):
          │     of_platform_bus_create(child, matches, lookup, parent, true)
          │                                          drivers/of/platform.c:343
          │       ├─ 已是 OF_POPULATED_BUS ⇒ 跳过     drivers/of/platform.c:368
          │       ├─ auxdata = of_dev_lookup(lookup, bus)   drivers/of/platform.c:374
          │       ├─ compatible 是 "arm,amba-bus" ⇒ of_amba_device_create()
          │       │                                  drivers/of/platform.c:385
          │       ├─ of_platform_device_create_pdata(bus, bus_id, pdata, parent)
          │       │                                  drivers/of/platform.c:167
          │       ├─ !strict || of_match_node(matches, bus) 才继续下钻
          │       ├─ of_node_set_flag(bus, OF_POPULATED_BUS)  drivers/of/platform.c:401
          │       └─ 递归每个子节点
          └─ of_node_set_flag(root, OF_POPULATED_BUS)  drivers/of/platform.c:490
```

| 元素 | 位置 |
|---|---|
| `of_platform_default_populate()` | `drivers/of/platform.c:504`（它把白名单表传进去，`:501`） |
| `of_platform_populate()` | `drivers/of/platform.c:465`（doc 在 `:447`） |
| `of_platform_bus_create()` | `drivers/of/platform.c:343` |
| 白名单表 `of_default_bus_match_table[]` | `drivers/of/platform.c:24-32` |
| `of_platform_device_create_pdata()` | `drivers/of/platform.c:167` |
| `of_device_alloc()` | `drivers/of/platform.c:112` |
| `of_device_make_bus_id()` | `drivers/of/platform.c:74`（doc 在 `:67`） |
| `of_amba_device_create()` | `drivers/of/platform.c:220`（有 `!CONFIG_ARM_AMBA` 的空版本在 `:281`） |
| `of_dev_lookup()` | `drivers/of/platform.c:293`（doc 在 `:291`） |
| `devm_of_platform_populate()` 里的递归调用 | `drivers/of/platform.c:590` |

**`of_device_alloc()` 的五件事**（02 §3.2 已展开）：
数 reg（`drivers/of/platform.c:125-126`）→ 翻译成 resource（`:129-142`）→
`of_node = of_node_get(np)`（`:144`）→ `fwnode`（`:145`）→ `parent`（`:146`）→
起名字（`:148-151`）。

⚠️ **`of_amba_device_create()` 的错误处理值得看一眼**：
它在 `of_address_to_resource()` 失败时打
`"amba: of_address_to_resource() failed (%d) for %pOF"`（`drivers/of/platform.c:260`），
`amba_device_add()` 失败时打 `"amba_device_add() failed (%d) for %pOF"`（`:267`），
两条都会 `of_node_clear_flag(node, OF_POPULATED)`（`:277`）回滚。
⇒ **这是本子系统少见的"有完整错误日志"的路径**（§C4）。

## §A6 驱动 probe 里的三件事

```
   probe(pdev)
     ├─ ① 读属性
     │     of_property_read_u32(pdev->dev.of_node, "clock-frequency", &f)
     │       └─ of_property_read_variable_u32_array()     drivers/of/property.c:339
     │            └─ of_find_property()                   drivers/of/base.c:223
     │                 └─ raw_spin_lock_irqsave(&devtree_lock)  drivers/of/base.c:217
     │
     ├─ ② 取地址（通常不用 of_* —— resource 已经填好了）
     │     devm_platform_ioremap_resource(pdev, 0)
     │       └─ platform_get_resource() ⇒ pdev->resource[0]
     │            （那是 §A5 的 of_device_alloc 翻译的）
     │
     └─ ③ 取中断（惰性解析）
           platform_get_irq(pdev, 0)
             └─ of_irq_get(np, 0)                         drivers/of/irq.c:475
                  ├─ of_irq_parse_one()                   drivers/of/irq.c:413
                  ├─ irq_find_host() 找不到 ⇒ -EPROBE_DEFER
                  └─ irq_create_of_mapping()
```

**地址和中断的处理方式不同**（02 §3.2）：

| | 地址 | 中断 |
|---|---|---|
| 什么时候翻译 | **填充时（eager）** | **probe 时（lazy）** |
| 为什么 | 地址翻译只需要 DT 自身信息 | **中断需要中断控制器已经 probe** ⇒ 要能 `-EPROBE_DEFER` |
| 放在哪 | `pdev->resource[]` | 不缓存，每次 `platform_get_irq()` 都解析 |

## §A7 运行期：只读

**DT 在启动之后基本是只读的**（除了 overlay，04 §1）。

| 常见操作 | 入口 | 锁 |
|---|---|---|
| `of_find_property()` | `drivers/of/base.c:223` | `devtree_lock`（raw spinlock，`drivers/of/base.c:217-219`） |
| `of_get_property()` | `drivers/of/base.c:287` | 同上 |
| `of_find_all_nodes()` | `drivers/of/base.c:262` | 同上（`drivers/of/base.c:255-259`） |
| `of_device_is_compatible()` | `drivers/of/base.c:558` | 同上（`drivers/of/base.c:553`） |
| `of_find_node_by_phandle()` | `drivers/of/base.c:1274` | 先查缓存（`drivers/of/base.c:1257-1259`），未命中才遍历 |

## §A8 ⚠️ 拆除：`of_platform_depopulate()`

| 元素 | 位置 |
|---|---|
| `of_platform_depopulate(parent)` | `drivers/of/platform.c:653` |
| `devm_of_platform_depopulate(dev)` | `drivers/of/platform.c:724` |
| `of_platform_device_destroy(dev, data)` | `drivers/of/platform.c:635` |
| 检查 `OF_POPULATED` | `drivers/of/platform.c:616` |
| 检查 `OF_POPULATED_BUS`（递归销毁子设备） | `drivers/of/platform.c:620` |

**`of_platform_device_destroy()` 的两道门**（`drivers/of/platform.c:616` · `:620`）：
① 没有 `of_node` 或没有 `OF_POPULATED` ⇒ 不是我们造的，不动；
② 有 `OF_POPULATED_BUS` ⇒ 先递归销毁它的子设备。

⚠️ **启动时填充的设备几乎从不 depopulate** ——
这套 API 主要给两类场景：
① 复合设备（MFD）的 `devm_of_platform_populate()` 在 remove 时自动拆；
② overlay 撤销（04 §1）。

---

# Part B：横切机制

## §B1 两把锁

### §B1.1 清单（02 §6.2 的机制展开）

| 锁 | 位置 | 类型 | 保护 |
|---|---|---|---|
| `devtree_lock` | `drivers/of/base.c:56` | **`raw_spinlock_t`** | **树结构的读写** |
| `of_mutex` | `drivers/of/base.c:51` | **mutex** | changeset / overlay 的串行化 |

### §B1.2 为什么读树也要拿 spinlock

因为**中断上下文也可能读 DT**：中断控制器的初始化、
`of_irq_parse_raw()` 的某些路径、以及 panic 时的信息打印。

⇒ **`devtree_lock` 必须是 `raw_spinlock_t` 而不是 `spinlock_t`**：
在 `CONFIG_PREEMPT_RT` 下后者会变成可睡眠的 rt-mutex。

典型的加解锁对：

| 函数 | 加锁 | 解锁 |
|---|---|---|
| `of_find_property()` | `drivers/of/base.c:217` | `drivers/of/base.c:219` |
| `of_find_all_nodes()` | `drivers/of/base.c:255` | `drivers/of/base.c:259` |
| `of_device_is_compatible()` | `drivers/of/base.c:553` | 同函数内 |

### §B1.3 ⚠️ 持 `devtree_lock` 时不能分配内存

raw spinlock 临界区里**不能睡** ⇒ 不能 `kmalloc(GFP_KERNEL)`。

⇒ **这就是 changeset 那套设计成"两阶段"的原因**（04 §1）：
在 `of_mutex` 保护下**先在锁外把节点和属性都准备好**，
最后只在一小段 `devtree_lock` 临界区里改指针。

`of_mutex` 声明在 `drivers/of/of_private.h:40`。

## §B2 `_flags`：填充状态机

六个位（02 §5.3）里**只有两个是热路径**：

| 位 | 谁设 | 谁读 | 作用 |
|---|---|---|---|
| `OF_POPULATED` | `of_node_test_and_set_flag()`（`drivers/of/platform.c:176`） | `of_platform_device_destroy()`（`drivers/of/platform.c:616`） | **幂等保护 + 拆除时的归属判断** |
| `OF_POPULATED_BUS` | `drivers/of/platform.c:401` · `:490` | `drivers/of/platform.c:368` · `:620` | **"这个节点的孩子已经填过了"** |

**`of_node_test_and_set_flag()` 的原子性很重要**：
它保证同一个节点不会被并发填充两次（`drivers/of/platform.c:176`）。
⇒ 失败路径要 `of_node_clear_flag()` 回滚（`drivers/of/platform.c:198`、`:277`）。

## §B3 phandle 哈希缓存

### §B3.1 为什么需要

`of_find_node_by_phandle()` 的朴素实现是**遍历全树**。
而一个典型 arm64 DTB 有**几百到上千个节点**，
每个驱动 probe 时可能解析十几个 phandle（clocks / resets / gpios / pinctrl / dmas / phys）
⇒ **启动时的 phandle 解析次数是"节点数 × 每节点引用数"量级**。

### §B3.2 实现

| 元素 | 位置 |
|---|---|
| `OF_PHANDLE_CACHE_SZ` | `drivers/of/base.c:139`（= `BIT(OF_PHANDLE_CACHE_BITS)`） |
| `phandle_cache[]` | `drivers/of/base.c:141` |
| `of_phandle_cache_hash()` | `drivers/of/base.c:143` |
| `__of_phandle_cache_inv_entry()`（失效一项） | `drivers/of/base.c:151`（清空在 `:161-163`） |
| 启动时填充 | `drivers/of/base.c:181-182`（在 `of_core_init()` 里） |
| 查找时命中判断 | `drivers/of/base.c:1257-1259` |
| 未命中时遍历后回填 | `drivers/of/base.c:1265` |

**它是一个"单槽直接映射缓存"**（不是链式哈希）：
`phandle_cache[hash]` 只存**一个**节点。
⇒ 哈希冲突时后来的会**覆盖**前面的（`drivers/of/base.c:1265`）——
所以它是 best-effort，不保证命中。

**命中判断有两个条件**（`drivers/of/base.c:1257-1258`）：
槽非空 **且** 槽里那个节点的 phandle 正好等于要找的 ——
⇒ 这就是"直接映射 + 校验 tag"的经典做法。

### §B3.3 ⚠️ 失效

`__of_phandle_cache_inv_entry()`（`drivers/of/base.c:151`）在
**节点被摘除时**（changeset / overlay）被调，把对应的槽清空。
⇒ **`!CONFIG_OF_DYNAMIC` 时这个函数几乎不会被调** —— 树不变，缓存永久有效。

## §B4 地址翻译：`__of_translate_address()`

### §B4.1 算法

```
   __of_translate_address(dev, get_parent, in_addr, rprop, host)
     ├─ of_node_get(dev)                        先加引用
     ├─ parent = get_parent(dev)；parent 为空 ⇒ bail
     ├─ bus = of_match_bus(parent)               按父节点类型选"总线"
     ├─ bus->count_cells(dev, &na, &ns)          取 #address-cells / #size-cells
     ├─ OF_CHECK_COUNTS 失败 ⇒ bail
     ├─ memcpy(addr, in_addr, na * 4)            拷一份本地副本
     │
     └─ for (;;) {                               一层层往上走
            of_node_put(dev); dev = parent; parent = get_parent(dev);
            parent == NULL ⇒ 到根了，result = of_read_number(addr, na)；break
            （indirectIO 特例 ⇒ 从 reg 直接取，break）
            pbus = of_match_bus(parent)；pbus->count_cells(dev, &pna, &pns)
            OF_CHECK_COUNTS 失败 ⇒ pr_err + break
            of_translate_one(dev, bus, pbus, addr, na, ns, pna, rprop)
                 失败 ⇒ break
            na = pna; ns = pns; bus = pbus;      换成父层的单元格数
        }
     └─ bail: of_node_put(parent); of_node_put(dev)
```

| 元素 | 位置 |
|---|---|
| `__of_translate_address()` | `drivers/of/address.c:513` |
| `of_node_get(dev)`（先加引用） | `drivers/of/address.c:527` |
| `of_match_bus(parent)` | `drivers/of/address.c:534`（实现在 `:402`） |
| `bus->count_cells()` | `drivers/of/address.c:537` |
| `OF_CHECK_COUNTS` 检查 | `drivers/of/address.c:538-541` |
| 拷本地副本 | `drivers/of/address.c:542` |
| 主循环 | `drivers/of/address.c:549` |
| 换到父层 | `drivers/of/address.c:553-555` |
| 到根 ⇒ `of_read_number()` | `drivers/of/address.c:558-562` |
| indirectIO 特例 | `drivers/of/address.c:564-575` |
| 父层单元格数 | `drivers/of/address.c:578-583` |
| `of_translate_one()` 调用 | `drivers/of/address.c:589` |
| 换单元格数 | `drivers/of/address.c:593-595` |
| `bail:` + 两次 put | `drivers/of/address.c:599-601` |
| `of_translate_one()` 实现 | `drivers/of/address.c:433` |
| `of_busses[]` 表 | `drivers/of/address.c:354` |
| `of_match_bus()` | `drivers/of/address.c:402` |
| `of_empty_ranges_quirk()` | `drivers/of/address.c:413` |
| `of_bus_default_map()` / `_translate()` | `drivers/of/address.c:70` / `:86` |
| `of_translate_address()`（对外） | `drivers/of/address.c:606` |
| `of_translate_dma_address()` | `drivers/of/address.c:652` |

**三个设计要点**：

| 要点 | 说明 |
|---|---|
| **`of_busses[]` 是一张"总线类型"表**（`drivers/of/address.c:354`） | 不同总线（PCI / ISA / default）的 `ranges` 格式不同 ⇒ 每种一套 `count_cells` / `map` / `translate` 回调 |
| **`addr[]` 是本地副本**（`drivers/of/address.c:542`） | 每层翻译**原地修改**它，所以不能直接改 DT 里的数据 |
| **单元格数每层都要重新取** | `na`/`ns` 换成 `pna`/`pns`（`drivers/of/address.c:593-595`）—— 父子两层的 `#address-cells` **可以不同** |

⚠️ **`of_empty_ranges_quirk()`（`drivers/of/address.c:413`）**：
某些老 DTB 的 `ranges` 是空的却本该是一一映射 ——
这个 quirk 让它们仍然能工作（`CONFIG_PPC32` 相关，arm64 上通常返回 false）。

### §B4.2 `pr_debug` 是这条路唯一的可观测性

`__of_translate_address()` 里有**六处 `pr_debug`**（
`drivers/of/address.c:524` · `:539` · `:544-545` · `:559` · `:571-572` · `:585-586`）
+ 两次 `of_dump_addr()`（`:546` · `:597`）。

**`of_translate_one()`（`drivers/of/address.c:433`）里的五条 `pr_debug` 更有诊断价值**：

| 消息 | 位置 | 含义 |
|---|---|---|
| `no ranges; cannot translate` | `drivers/of/address.c:464` | **父节点没有 `ranges` 属性** ⇒ 翻译失败 |
| `empty ranges; 1:1 translation` | `drivers/of/address.c:471` | 父节点有 `ranges;`（空）⇒ 一一映射 |
| `walking ranges...` | `drivers/of/address.c:475` | 开始逐项匹配 |
| `not found !` | `drivers/of/address.c:486` | **地址不在任何一个 range 窗口里** |
| `with offset: %llx` | `drivers/of/address.c:493` | 匹配成功，给出偏移 |

⇒ **这五条把"地址翻译为什么失败"分成了三种**（06 §8 会给对照表）：
① 父节点缺 `ranges`；② `ranges` 里没有覆盖这个地址的窗口；③ 单元格数不对（`pr_err`）。

⇒ **调地址解析问题的唯一办法就是打开这些 `pr_debug`**（07 篇）：
```sh
echo 'file address.c +p' > /sys/kernel/debug/dynamic_debug/control
```

只有一处 `pr_err`：`"Bad cell count for %pOF"`（`drivers/of/address.c:581`）——
⇒ **父节点的 `#address-cells` 不合法**（06 篇会给判据）。

## §B5 属性读取的三层

```
   of_property_read_u32(np, "foo", &v)          ← inline，include/linux/of.h:1325
     └─ of_property_read_variable_u32_array(np, "foo", &v, 1, 0)
          │                                     drivers/of/property.c:339
          └─ of_find_property_value_of_size(np, propname, min, max, &len)
               └─ of_find_property(np, name, lenp)   drivers/of/base.c:223
                    └─ raw_spin_lock_irqsave(&devtree_lock)  drivers/of/base.c:217
               ⇒ 长度检查 ⇒ -ENODATA / -EOVERFLOW / -EINVAL
          └─ 逐个 be32_to_cpup()
```

**三层各自的职责**：

| 层 | 职责 | 错误码 |
|---|---|---|
| inline 包装 | 填默认的 min/max | —— |
| `_variable_*_array()` | **长度校验 + 字节序转换** | `-ENODATA` / `-EOVERFLOW` |
| `of_find_property()` | 加锁 + 遍历属性链 | 返回 NULL ⇒ 上层一律转成 `-EINVAL`（`property.c:113`） |

⇒ **01 §14.2 那四个错误码的语义就是在这三层里分出来的**。

## §B6 ⚠️`of_node_get/put` 在非 overlay 场景下是空操作

02 §1.0.C 已展开。这里补一条**代码层面的后果**：

**`drivers/of/` 自己的代码里到处都是配平的 get/put**
（如 `__of_translate_address()` 的 `drivers/of/address.c:527` / `:599-601`）——
⇒ 即使在 `!OF_DYNAMIC` 下这些都是空操作，**上游仍然坚持写全**。
⇒ **这是"代码即文档"的一个例子**：get/put 的配对标出了"这个引用管到哪里"。

---

# Part C：评价

## §C1 源码里的自陈问题：只有一条

```bash
F="base property address platform device irq fdt dynamic overlay of_reserved_mem resolver kobj fdt_address of_numa kexec"
for n in $F; do grep -Hn 'TODO\|FIXME\|XXX\|REVISIT\|HACK' drivers/of/$n.c; done
```

| 标记 | 个数 | 位置 |
|---|---|---|
| `XXX` | **1** | `drivers/of/base.c:2046` |
| `TODO` / `FIXME` / `REVISIT` / `HACK` | **0** | —— |
| `of_private.h` / `include/linux/of.h` | **0** | —— |

**约 12650 行产品代码只有一条 `XXX`**，而且那条只是一个类型转换的抱怨
（`of_console_check()` 里 "cast `options' to char pointer to suppress complication"）。

⇒ **这是本系列里代码"最干净"的一套**（对比：irq 2 条 `FIXME`、
cdev 有 `/* will die */` 和一条 20 年的 `NOTE:`）。

⚠️ **但"没有 FIXME"不等于"没有设计债"** —— §C2–§C5 是本套发现的四条。

## §C2 一大类 API 用 NULL / 0 表示失败

这是本子系统**最影响使用者**的一条设计债（01 §14.4 末段）：

| API | 失败时返回 | 问题 |
|---|---|---|
| `of_find_*()` 全家 | **NULL** | 分不清"没这个节点"和"参数错" |
| `of_get_property()` | **NULL** | 分不清"没这个属性"和"属性为空" |
| `of_parse_phandle()` | **NULL** | 分不清"属性不存在" / "index 越界" / "phandle 指向的节点找不到" |
| `of_iomap()` | **NULL** | 分不清"翻译失败"和"`ioremap` 失败" |
| `of_translate_address()` | **`OF_BAD_ADDR`** | 至少有一个专门的哨兵值 |

**对比同一子系统里做得对的那些**：
`of_property_read_u32()` 家族区分了 `-EINVAL` / `-ENODATA` / `-EOVERFLOW` / `-EILSEQ`
四种（01 §14.2）—— ⇒ **同一个子系统里两种风格并存**。

⚠️ **但它也没做干净**：`-EINVAL` 同时表示"属性不存在"和"调用者传了 NULL"
（`property.c:113` 只有一条 `if (!prop)`，而 `of_find_property()` 对 NULL 节点也返回 NULL），
⇒ 驱动分不出"DTS 少写了属性"和"我拿到的 `np` 是空的"（01 §14.2.1）。

**历史原因**：`of_find_*` 这套 API 来自 PowerPC 的 OF 时代（2005 年前后），
比 `of_property_read_*`（2011 年前后加的）早得多。

⇒ **实际后果**：驱动拿到 NULL 只能打一句"failed to find xxx"，
**无法给出可操作的错误信息**。
这也是 irq 那套 03 §C4 记录的 `irq_of_parse_and_map()` 返回 0 那条债的同源问题。

## §C3 `of_node_get/put` 的"半真半假"

02 §1.0.C 已展开机制。这里给**评价**：

| 事实 | 评价 |
|---|---|
| `!CONFIG_OF_DYNAMIC` 时是空操作（`include/linux/of.h:126-130`） | **导致大量驱动的 put 缺失长期不被发现** |
| 上游自己的代码坚持配平（如 `drivers/of/address.c:527` / `:599-601`） | 好 |
| 唯一的检测手段是 `OF_UNITTEST`（`drivers/of/dynamic.c:471` 那条 `pr_err`） | **但那需要专门打开一个 3647 行的测试模块** |

⚠️ **这是一个"配置依赖的正确性"** —— 同一份驱动代码：
在 `!OF_DYNAMIC` 的板子上跑十年没问题，
换到开了 overlay 的板子上就泄漏。
⇒ 严重程度：中（泄漏的是几十字节的 `device_node`，且只在动态场景）。

## §C4 可观测性极不均衡

| 路径 | 出错时说话吗 |
|---|---|
| 地址翻译 | **只有 `pr_debug`**（§B4.2）+ 一条 `pr_err`（`drivers/of/address.c:581`） |
| 属性读取 | **一句话都不说** —— 只返回错误码 |
| 设备填充 | **部分说** —— amba 路径有两条 `pr_err`（`drivers/of/platform.c:260` · `:267`），普通 platform 路径**几乎不说** |
| 早期扫描 | 说（`drivers/of/fdt.c` 里有 `pr_err`/`pr_warn`） |
| overlay | **说得最多**（`drivers/of/overlay.c` 有大量 `pr_err`） |

**这个不均衡有一个合理解释**：
地址翻译和属性读取是**每个驱动 probe 都要走几十次**的热路径 ——
在这里打 `pr_err` 会把 dmesg 刷满（因为"属性不存在"经常是**正常的**，
可选属性就是这样）。

⇒ **但代价是"配错 DTS"这类问题几乎没有直接线索** ——
必须打开 `pr_debug`（07 篇）。
这和 cdev 那套的"`cdev_map` 没有出口"是同一类：
**"正常情况太多"导致设计者选择了沉默**。

## §C5 两套"总线抽象"并存

| 抽象 | 位置 | 用途 |
|---|---|---|
| `of_busses[]`（地址翻译用的"总线"） | `drivers/of/address.c:354` | 决定 `ranges` 怎么解析（PCI / ISA / default） |
| `of_default_bus_match_table[]`（设备填充用的"总线"） | `drivers/of/platform.c:24-32` | 决定**要不要往下填充子设备** |

⚠️ **两张表毫无关系，但名字都叫"bus"**：
一个节点可以在 `of_busses[]` 里是 "default bus"，
同时**不在** `of_default_bus_match_table[]` 里（⇒ 子节点不填充）。

⇒ **这是本子系统一个真实的命名混淆**（02 §4 那张表里已标注）。
08 篇会用类比把它们分开。

## §C6 整体评价

| 维度 | 评价 |
|---|---|
| **代码整洁度** | **约 12650 行只有 1 条 `XXX`**（§C1） |
| **文档质量** | `Documentation/devicetree/usage-model.rst`（420 行）是本系列里**最好的一份设计意图文档**（01 §0.6） |
| **抽象的清晰度** | ☆ `device_node` / `property` / phandle 三层非常干净；⚠️ 唯一的污点是两套"bus"抽象同名（§C5） |
| **API 一致性** | ☆☆☆ ⚠️ **两种错误约定并存**：`of_find_*` 返回 NULL、`of_property_read_*` 返回错误码（§C2） |
| **可观测性** | ☆☆☆ ⚠️ 热路径只有 `pr_debug`（§C4）—— **调 DTS 问题必须重编内核或开动态调试** |
| **引用计数的严格性** | ☆☆ ⚠️ 配置依赖（§C3） |
| **测试覆盖** | **3647 行的 `unittest.c`**（占全目录 22%）+ 一份 226 行的测试文档 |
| **对 arm64 的适配** | **arm64 完全依赖它**（所有 platform 设备都是它造的），而且 Sparc 专用代码被隔离在 `pdt.c` 里 |
| **稳定性承诺** | **DT 绑定是稳定 ABI**（02 §1.0.E）—— 这是内核里少见的对外兼容性承诺 |

**一句话总结**：这是内核里**代码最干净、文档最好、但对使用者最"沉默"**的子系统。
它的难度不在读代码，而在**理解那些不成文的约定**（单元格数、phandle 参数个数、
填充白名单、稳定 ABI）—— 而这些恰好**大部分写在 `usage-model.rst` 和绑定文档里，
不在代码里**。

---

**下一篇**：04 篇讲五个互不调用的独立框架
（overlay/changeset · reserved-memory · graph · sysfs 视图 · unittest）。
