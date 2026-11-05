---
title: API 参考
---

# of 01：代码地图与对外 API

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> 本篇覆盖的是**设备树核心**：`drivers/of/` + `include/linux/of*.h`。
> ⚠️ 它**不是**"某个硬件的驱动"，而是**所有 arm64 驱动的公共输入**。
>
> 本篇 API 表统一三列 **API / 说明 / 备注**；签名只写**函数名 + 形参名**，不带类型；
> 位置只到**文件**，每节开头统一交代头文件和实现文件。

---

## §0 代码地图

### §0.1 `drivers/of/` 的 18 个文件

| 文件 | 行数 | 职责 | 本套的证据等级 |
|---|---|---|---|
| `base.c` | **2213** | **查找 / 遍历 / 属性 / phandle / compatible 匹配** —— 全子系统的主体 | 一等（关键路径） |
| `property.c` | **1485** | 类型化属性读取（`of_property_read_u32` 家族）+ **fwnode 统一接口** + graph | 一等（关键路径） |
| `fdt.c` | **1398** | **早期解析**：把 bootloader 传来的扁平 DTB 展开成树 | 二等 |
| `overlay.c` | 1266 | 运行期打 overlay | 三等 |
| `address.c` | **1147** | **地址翻译**（`ranges` / `of_iomap` / `of_address_to_resource`） | 一等（关键路径） |
| `dynamic.c` | 937 | **引用计数 + changeset**（节点的增删） | 二等 |
| `platform.c` | **795** | **把 DT 节点变成 `platform_device`** —— arm64 上最重要的一块 | 一等（全文关键路径） |
| `irq.c` | 758 | 中断解析 —— **已在 irq 那套的 06 篇讲过**（§8） | 二等 |
| `kexec.c` | 472 | kexec 时重建 DTB | 三等 |
| `of_reserved_mem.c` | 450 | `reserved-memory` 节点 | 二等 |
| `device.c` | 398 | `of_match_device` / DMA 配置 / modalias | 二等 |
| `resolver.c` | 340 | overlay 的 phandle 重定位 | 三等 |
| `fdt_address.c` | 249 | 早期（树还没展开时）的地址翻译 | 三等 |
| `pdt.c` | 213 | Sparc 的 PROM 树 —— **arm64 不涉及** | 不读 |
| `of_numa.c` | 186 | NUMA 拓扑 | 三等 |
| `of_private.h` | 178 | 内部契约 | 一等 |
| `kobj.c` | 165 | `/sys/firmware/devicetree/` | 二等 |
| `unittest.c` | **3647** | **单元测试** —— 不是产品代码 | 不读（但 §0.5 说明它的价值） |

| 项 | 值 | 命令 |
|---|---|---|
| `drivers/of/*.c` 总行数 | **16297**（含 `unittest.c` 的 3647） | `wc -l drivers/of/*.c drivers/of/*.h \| tail -1` |
| ⇒ 产品代码 | 约 **12650** | 16297 − 3647 |

### §0.2 头文件（对外 API 的全部入口）

| 头文件 | 行数 | 给谁看 |
|---|---|---|
| `include/linux/of.h` | **1640** | **所有驱动作者** —— 节点、属性、查找、遍历、flags |
| `include/linux/of_address.h` | 157 | 地址翻译 |
| `include/linux/of_platform.h` | 130 | 设备填充（写总线驱动 / 复合设备时） |
| `include/linux/of_graph.h` | 127 | 端口图（媒体 / 显示） |
| `include/linux/of_irq.h` | 126 | 中断 —— **详见 irq 那套的 06 篇** |
| `include/linux/of_device.h` | 118 | 匹配 / DMA / modalias |
| `include/linux/of_fdt.h` | 97 | 早期解析（arch 代码用，驱动几乎不用） |
| `include/linux/of_dma.h` | 93 | DMA 通道解析 |
| `include/linux/of_reserved_mem.h` | 84 | reserved-memory |
| `drivers/of/of_private.h` | 178 | **只给 `drivers/of/` 自己用** |

**`include/linux/of.h` 一个文件 1640 行** ——
比整个 cdev 子系统的核心（961 行）还大。
⇒ 它有大量 `#ifdef CONFIG_OF` 的**空实现**（让不支持 DT 的架构也能编译）。

### §0.3 导出符号 130 个

| 文件 | 导出数 |
|---|---|
| `base.c` | **44** |
| `property.c` | **25** |
| `address.c` | 12 |
| `dynamic.c` | 11 |
| `platform.c` | 10 |
| `irq.c` | 9 |
| `device.c` | 8 |
| `overlay.c` | 5 |
| `of_reserved_mem.c` | 4 |
| `fdt.c` · `resolver.c` | 各 1 |
| **合计** | **130**（61 `EXPORT_SYMBOL` + 69 `EXPORT_SYMBOL_GPL`） |

```bash
grep -rho 'EXPORT_SYMBOL[_A-Z]*' drivers/of/*.c | sort | uniq -c
```

**对比本系列**：irq 151 个、remoteproc 33 个、cdev 12 个。
⇒ **of 是第二大的对外面积**，但性质不同：
irq 的 151 个服务**三类**使用者，of 的 130 个几乎**全是"读 DT"**（§0.4）。

### §0.4 130 个 API 的分类

| 类 | 大致数量 | 谁用 | 本篇章节 |
|---|---|---|---|
| **读 DT**（查找 / 遍历 / 属性 / phandle） | **约 70** | **所有驱动** | §2 §3 §4 §5 |
| 匹配与身份 | 约 12 | 驱动 + 总线 | §6 |
| **地址翻译** | 12 | 驱动 | §7 |
| 中断 | 9 | 驱动 | §8 |
| **设备填充** | 10 | 总线驱动 / 复合设备 | §9 |
| fwnode 统一接口 | 1（`of_fwnode_ops`） | **不是给驱动的** | §10 |
| graph | 约 12 | 媒体 / 显示 | §11 |
| reserved-memory | 4 | 需要连续内存的驱动 | §12 |
| 动态修改（changeset / overlay） | 约 14 | **极少数**（FPGA / 可插拔背板） | §13 |

**90% 的驱动只用到 §2 §4 §7 §8 这四组里的十来个函数**。

### §0.5 Kconfig（16 项）

| 选项 | 位置 | arm64 上 | 作用 |
|---|---|---|---|
| `OF` | `drivers/of/Kconfig:5` | **y** | 总开关（`menuconfig`） |
| `OF_FLATTREE` | `drivers/of/Kconfig:38` | y | 扁平 DTB 的解析 |
| `OF_EARLY_FLATTREE` | `drivers/of/Kconfig:44` | y | 启动早期就解析 |
| `OF_ADDRESS` | `drivers/of/Kconfig:65` | y | 地址翻译（§7） |
| `OF_IRQ` | `drivers/of/Kconfig:69` | y | 中断解析（§8） |
| `OF_KOBJ` | `drivers/of/Kconfig:52` | y | `/sys/firmware/devicetree/` |
| `OF_DYNAMIC` | `drivers/of/Kconfig:57` | 视配置 | **引用计数真正生效的开关**（§1.2） |
| `OF_RESERVED_MEM` | `drivers/of/Kconfig:73` | y | §12 |
| `OF_RESOLVE` / `OF_OVERLAY` | `drivers/of/Kconfig:76` / `:79` | 通常 **n** | §13 |
| `OF_NUMA` | `drivers/of/Kconfig:90` | 视平台 | —— |
| `OF_DMA_DEFAULT_COHERENT` | `drivers/of/Kconfig:93` | 视平台 | 没写 `dma-coherent` 时的默认 |
| `OF_PROMTREE` | `drivers/of/Kconfig:49` | **n**（Sparc 用） | —— |
| `OF_UNITTEST` | `drivers/of/Kconfig:15` | n | **3647 行的自测**（§0.6） |
| `OF_ALL_DTBS` | `drivers/of/Kconfig:28` | n | 编译全部 DTB |
| `DTC` | `drivers/of/Kconfig:2` | 自动 | 设备树编译器 |

**`OF_DYNAMIC` 是本子系统最值得单独记的一个选项**（§1.2）：
它决定 `of_node_get()` / `of_node_put()` 是**真的引用计数**还是**空操作**。

### §0.6 官方文档质量很高（947 行，必读）

| 文件 | 行数 | 内容 |
|---|---|---|
| `Documentation/devicetree/usage-model.rst` | **420** | **本子系统唯一的"设计意图"文档** —— 从 bootloader 到 platform_device 的完整模型 |
| `Documentation/devicetree/of_unittest.rst` | 226 | 单元测试怎么跑 |
| `Documentation/devicetree/overlay-notes.rst` | 150 | overlay 的语义与限制 |
| `Documentation/devicetree/kernel-api.rst` | 57 | 只是 kernel-doc 的索引 |
| `Documentation/devicetree/changesets.rst` | 37 | changeset 的用法 |
| `Documentation/devicetree/index.rst` | 30 | 索引 |
| `Documentation/devicetree/dynamic-resolution-notes.rst` | 27 | phandle 重定位 |

**`usage-model.rst` 必须先读** —— 它是本套 00 §2 和 03 篇的主要背景来源（99 §1.4）。

---

## §1 引用计数与生命周期

> 头文件：`include/linux/of.h:121-132` · 实现：`drivers/of/dynamic.c`

### §1.1 两个函数

| API | 说明 | 备注 |
|---|---|---|
| `of_node_get(node)` | 引用计数 +1，返回同一个 node | `drivers/of/dynamic.c:33`（导出于 `:39`）；`node` 为 NULL 时安全返回 NULL |
| `of_node_put(node)` | 引用计数 −1 | `drivers/of/dynamic.c:46`（导出于 `:51`） |

### §1.2 ⚠️ 它们在 `!CONFIG_OF_DYNAMIC` 下是**空操作**

```c
#ifdef CONFIG_OF_DYNAMIC
extern struct device_node *of_node_get(struct device_node *node);
extern void of_node_put(struct device_node *node);
#else /* CONFIG_OF_DYNAMIC */
static inline struct device_node *of_node_get(struct device_node *node)
{
	return node;
}
static inline void of_node_put(struct device_node *node) { }
#endif
```

| 分支 | 位置 |
|---|---|
| 有实体的声明 | `include/linux/of.h:121-123` |
| **空实现** | `include/linux/of.h:124-131` |

⇒ **`CONFIG_OF_DYNAMIC` 关掉时，忘记 `of_node_put()` 完全没有后果** ——
因为 DT 是启动时一次性展开的，节点永不释放。

⚠️ **但这不是"可以不 put"的理由**：

| 场景 | 忘了 `of_node_put()` 的后果 |
|---|---|
| `!OF_DYNAMIC` | 什么都不会发生 |
| `OF_DYNAMIC` = y（overlay / 可插拔硬件） | **节点永远不释放** ⇒ 内存泄漏 |
| 打开 `OF_UNITTEST` | **自测会报 refcount 不平衡** |

⇒ **`drivers/of/dynamic.c:471` 那条 `pr_err` 就是专门抓这个的**：
`"ERROR: memory leak, expected refcount 1 instead of %d, of_node_get()/of_node_put() unbalanced …"`。
另有 `drivers/of/dynamic.c:336` 的 `"ERROR: Bad of_node_put() on %pOF"`（put 多了）。

### §1.3 哪些函数会替你 `of_node_get()`

**这是本子系统最容易漏的一类 bug**：很多"查找"函数返回的节点**带一份引用**。

| 会 get 的（**必须 put**） | 不会 get 的（不要 put） |
|---|---|
| `of_find_node_by_name()` / `_by_type()` / `_by_path()` | `of_get_property()`（返回的是**属性值指针**，不是节点） |
| `of_find_compatible_node()` / `_matching_node()` | `of_find_property()`（返回 `struct property *`） |
| `of_get_parent()` / `of_get_next_parent()` | `np->parent` / `np->child` **直接访问字段** |
| `of_get_next_child()` / `of_get_next_available_child()` | —— |
| `of_get_child_by_name()` / `of_get_compatible_child()` | —— |
| `of_parse_phandle()` / `of_parse_phandle_with_args()`（通过 `args.np`） | —— |
| `of_find_node_by_phandle()` | —— |
| `of_graph_get_*()` 家族（§11） | —— |

**判据（沿用官方 kernel-doc 的写法）**：
函数注释里带 "**Return: … Use of_node_put() on it when done**" 的就要 put。
例如 `include/linux/of.h:910` 附近那段注释。

**`for_each_child_of_node()` 这类宏会自动 put 上一个**
（`include/linux/of.h:1373`）—— ⚠️ **但如果你 `break` 出去，必须自己 put**（§3.2）。

### §1.4 6.1.158 里的新工具：`_scoped` 宏

| 工具 | 位置 | 作用 |
|---|---|---|
| `DEFINE_FREE(device_node, …)` | `include/linux/of.h:132` | 配合 `__free(device_node)` 自动 put |
| `for_each_child_of_node_scoped()` | `include/linux/of.h:1377` | **循环变量自动 put，`break`/`return` 都安全** |
| `for_each_available_child_of_node_scoped()` | `include/linux/of.h:1387` | 同上 |

**新代码应该优先用 `_scoped` 版本** —— 它把 §3.2 那个 `break` 陷阱彻底消掉了。

⚠️ 这两个宏是 6.x 后期加进来又回合到 6.1 稳定版的
⇒ **在更老的 6.1.x 上可能不存在**（99 §5.4）。

---

## §2 查找节点

> 头文件：`include/linux/of.h` · 实现：`drivers/of/base.c`

### §2.1 最常用的六个

| API | 说明 | 备注 |
|---|---|---|
| `of_find_compatible_node(from, type, compat)` | 按 `compatible` 找 | `drivers/of/base.c:1092`；`from` 传 NULL 从头开始，传上次的结果继续找；**会 put 掉 `from`** |
| `of_find_matching_node_and_match(from, matches, match)` | 按 `of_device_id` 表找，并回报匹配到哪一项 | `drivers/of/base.c:1208` |
| `of_find_node_by_path(path)` | 按路径（如 `/soc/uart@1234`） | 实际是 `of_find_node_opts_by_path()`（`drivers/of/base.c:1006`）的包装 |
| `of_find_node_by_name(from, name)` | 按节点名 | `drivers/of/base.c:1033`；⚠️ **名字不唯一**，多个同名节点要循环 |
| `of_find_node_by_type(from, type)` | 按 `device_type` 属性 | `drivers/of/base.c:1061`；**`device_type` 在新绑定里已废弃** |
| `of_find_node_with_property(from, prop_name)` | 按"有某个属性" | `drivers/of/base.c:1127` |

**全部会替你 `of_node_get()`** ⇒ 用完必须 `of_node_put()`（§1.3）。

### §2.2 从一个已知节点出发（驱动最常用）

| API | 说明 | 备注 |
|---|---|---|
| `of_get_child_by_name(node, name)` | 取名字为 `name` 的**直接子节点** | `drivers/of/base.c:905` |
| `of_get_compatible_child(parent, compat)` | 取 compatible 匹配的直接子节点 | `drivers/of/base.c:882` |
| `of_get_parent(node)` | 取父节点 | `drivers/of/base.c:718` |
| `of_get_next_parent(node)` | **取父节点并 put 掉 `node`** —— 给"往上走"的循环用 | `drivers/of/base.c:745` |
| `of_find_all_nodes(prev)` | 遍历全树（含不可用的） | `drivers/of/base.c:262` |

**`of_get_next_parent()` 的语义很特别**：它**吃掉**输入的引用。
⇒ 写"从叶子一路走到根"的循环时用它，不用手工 put。

### §2.3 特殊节点的快捷方式

| API / 变量 | 说明 | 备注 |
|---|---|---|
| `of_root` | 根节点 | `drivers/of/base.c:35`（导出于 `:36`） |
| `of_chosen` | `/chosen` 节点 | `drivers/of/base.c:37`（导出于 `:38`） |
| `of_aliases` | `/aliases` 节点 | `drivers/of/base.c:39` |
| `of_stdout` | `stdout-path` 指向的节点 | `drivers/of/base.c:40` |
| `of_get_cpu_node(cpu, thread)` | 第 N 个 CPU 的节点 | `drivers/of/base.c:412` |
| `of_cpu_node_to_id(np)` | 反向：节点 → Linux CPU 号 | `drivers/of/base.c:438` |
| `of_get_next_cpu_node(prev)` | 遍历 CPU 节点 | `drivers/of/base.c:857` |
| `of_get_cpu_state_node(cpu_node, index)` | CPU 的 idle state 节点 | `drivers/of/base.c:474` |

⚠️`of_root` / `of_chosen` 等是**裸全局变量**，直接用**不要 put**
（它们不是查找函数的返回值）。

### §2.4 `/aliases` 相关

| API | 说明 | 备注 |
|---|---|---|
| `of_alias_get_id(np, stem)` | 从 `/aliases` 反查这个节点的编号（如 `serial0` ⇒ 0） | `drivers/of/base.c:2001`；**tty 的 `ttyS<N>` 编号就靠它** |
| `of_alias_get_highest_id(stem)` | 某个前缀的最大编号 | `drivers/of/base.c:2027` |
| `of_console_check(dn, name, index)` | 这个节点是不是 console | `drivers/of/base.c:2051` |

---

## §3 遍历

> 头文件：`include/linux/of.h` 的一组宏

### §3.1 六个循环宏

| 宏 | 说明 | 位置 |
|---|---|---|
| `for_each_child_of_node(parent, child)` | 遍历直接子节点 | `include/linux/of.h:1373` |
| `for_each_available_child_of_node(parent, child)` | **跳过 `status = "disabled"` 的** | `include/linux/of.h:1383` |
| `for_each_child_of_node_scoped(parent, child)` | **自动 put，`break` 安全** | `include/linux/of.h:1377` |
| `for_each_available_child_of_node_scoped(...)` | 同上 | `include/linux/of.h:1387` |
| `for_each_compatible_node(dn, type, compatible)` | 全树按 compatible | `include/linux/of.h:1363` |
| `for_each_matching_node(dn, matches)` | 全树按匹配表 | `include/linux/of.h:1366` |
| `for_each_matching_node_and_match(dn, matches, match)` | 同上 + 回报匹配项 | `include/linux/of.h:1369` |
| `for_each_node_by_name(dn, name)` | 全树按名字 | `include/linux/of.h:1357` |
| `for_each_of_allnodes(dn)` / `_from(from, dn)` | 全树（含不可用） | `include/linux/of.h:268` / `:266` |
| `for_each_property_of_node(dn, pp)` | 遍历一个节点的所有属性 | `include/linux/of.h:360` |

**`for_each_available_child_of_node()` 才是驱动应该用的** ——
它跳过 `status = "disabled"` 的节点（`of_get_next_available_child()`，`drivers/of/base.c:815`）。

### §3.2 ⚠️`break` 出循环必须手工 `of_node_put()`

```c
	/* ⚠️ 错的 */
	for_each_child_of_node(np, child) {
		if (match(child))
			break;			/* child 的引用泄漏了！ */
	}

	/* 对的（老写法） */
	for_each_child_of_node(np, child) {
		if (match(child)) {
			found = child;		/* 把引用交出去，之后自己 put */
			break;
		}
	}

	/* 最好的（6.1.158 有这个宏） */
	for_each_child_of_node_scoped(np, child) {
		if (match(child))
			break;			/* 自动 put，安全 */
	}
```

**原理**：`for_each_child_of_node()` 展开成
`for (child = of_get_next_child(parent, NULL); child != NULL;
      child = of_get_next_child(parent, child))`
（`include/linux/of.h:1373-1375`）——
`of_get_next_child()`（`drivers/of/base.c:784`）会 put 掉传入的 `prev`。
⇒ **循环正常结束时最后一次调用返回 NULL 并 put 掉了最后一个；
`break` 跳过了这一步。**

### §3.3 底层迭代器

| API | 说明 | 备注 |
|---|---|---|
| `of_get_next_child(node, prev)` | **put `prev`，get 并返回下一个** | `drivers/of/base.c:784` |
| `of_get_next_available_child(node, prev)` | 同上 + 跳过 disabled | `drivers/of/base.c:815` |
| `of_get_next_cpu_node(prev)` | CPU 节点专用 | `drivers/of/base.c:857` |

---

## §4 属性读取（驱动用得最多的一组）

> 头文件：`include/linux/of.h` · 实现：`drivers/of/property.c` + `drivers/of/base.c`

### §4.1 底层三个

| API | 说明 | 备注 |
|---|---|---|
| `of_find_property(np, name, lenp)` | 找属性，返回 `struct property *` | `drivers/of/base.c:223`；**不 get 引用** |
| `of_get_property(np, name, lenp)` | **直接返回属性值的指针** | `drivers/of/base.c:287`；⚠️ **返回的是大端字节序的原始数据**（§4.5） |
| `of_property_count_elems_of_size(np, propname, elem_size)` | 数一数有几个元素 | `drivers/of/property.c:89` |

### §4.2 类型化读取（首选）

| API | 说明 | 备注 |
|---|---|---|
| `of_property_read_u32(np, propname, out_value)` | 读一个 u32 | inline 包装，落到 `of_property_read_variable_u32_array()` |
| `of_property_read_u32_index(np, propname, index, out_value)` | 读第 index 个 u32 | `drivers/of/property.c:159` |
| `of_property_read_u64(np, propname, out_value)` | 读一个 u64 | `drivers/of/property.c:370` |
| `of_property_read_u64_index(np, propname, index, out_value)` | —— | `drivers/of/property.c:192` |
| `of_property_read_u32_array(np, propname, out_values, sz)` | 读固定长度的数组 | inline，落到 variable 版 |
| `of_property_read_variable_u8_array(...)` | u8 数组（sz_min / sz_max） | `drivers/of/property.c:242` |
| `of_property_read_variable_u16_array(...)` | u16 | `drivers/of/property.c:292` |
| `of_property_read_variable_u32_array(...)` | **u32 数组的真正实现** | `drivers/of/property.c:339` |
| `of_property_read_variable_u64_array(...)` | u64 | `drivers/of/property.c:419` |
| `of_property_read_string(np, propname, out_string)` | 读一个字符串 | `drivers/of/property.c:453` |
| `of_property_read_string_index(np, propname, index, output)` | 读第 index 个字符串 | inline，落到 `of_property_read_string_helper()`（`drivers/of/property.c:528`） |
| `of_property_match_string(np, propname, string)` | **在字符串列表里找，返回索引** | `drivers/of/property.c:490` |
| `of_property_count_strings(np, propname)` | 数字符串个数 | inline |
| `of_property_read_bool(np, propname)` | **属性存在就是 true**（不看值） | inline |
| `of_property_present(np, propname)` | 同上的新名字 | inline |

**`of_property_read_bool()` 的语义是"存在性"** ——
DT 里 `some-flag;`（无值）就是 true。
⇒ ⚠️ **写 `some-flag = <0>;` 也是 true！**（06 篇会展开）

### §4.3 迭代属性里的元素

| API | 说明 | 备注 |
|---|---|---|
| `of_prop_next_u32(prop, cur, pu)` | 迭代 u32 列表 | `drivers/of/property.c:551`；配合 `of_property_for_each_u32()` 宏 |
| `of_prop_next_string(prop, cur)` | 迭代字符串列表 | `drivers/of/property.c:569`；配合 `of_property_for_each_string()` |

### §4.4 地址/大小单元格数

| API | 说明 | 备注 |
|---|---|---|
| `of_n_addr_cells(np)` | **沿父链找 `#address-cells`** | `drivers/of/base.c:108` |
| `of_n_size_cells(np)` | 同理 `#size-cells` | `drivers/of/base.c:129` |

**这两个是理解 `reg` 和 `ranges` 的钥匙**（§7 · 06 篇）。

### §4.5 ⚠️ 字节序：DT 里全是大端

| 事实 | 说明 |
|---|---|
| **设备树里所有整数都是大端（big-endian）** | 这是 DTB 格式规定的 |
| ⇒ `of_get_property()` 返回的原始指针 | **必须自己 `be32_to_cpup()`** |
| ⇒ `of_property_read_u32()` 家族 | **已经替你转好了** —— 这是应该用它们的最大理由 |
| `of_device_is_big_endian(np)` | 这是**另一件事** —— 问"这个**设备**的寄存器是不是大端"（看 `big-endian` / `native-endian` 属性），`drivers/of/base.c:696` |

⚠️ **`of_get_property()` + 手工解析是老代码里最常见的 bug 来源** ——
新代码一律用 §4.2 的类型化接口。

---

## §5 phandle：DT 里的"指针"

> 头文件：`include/linux/of.h:369-385`（有实体）· `:709-744`（空实现）
> 实现：`drivers/of/base.c`

### §5.1 两个结构体

| 结构 | 位置 | 内容 |
|---|---|---|
| `struct of_phandle_args` | `include/linux/of.h:75` | `np`（目标节点）+ `args_count` + `args[MAX_PHANDLE_ARGS]` |
| `struct of_phandle_iterator` | `include/linux/of.h:81` | 迭代器状态 |

`MAX_PHANDLE_ARGS` = **16**（`include/linux/of.h:74`）。

### §5.2 四个层次的 API

| API | 说明 | 备注 |
|---|---|---|
| `of_parse_phandle(np, phandle_name, index)` | **只要节点，不要参数** | inline（`include/linux/of.h:912`），落到 `__of_parse_phandle_with_args()`；**返回的节点要 put** |
| `of_parse_phandle_with_args(np, list_name, cells_name, index, out_args)` | **要节点也要参数**（参数个数由目标节点的 `#<cells_name>` 决定） | inline，落到 `drivers/of/base.c:1477` |
| `of_parse_phandle_with_fixed_args(np, list_name, cell_count, index, out_args)` | 参数个数**写死**（目标节点没有 `#…-cells` 时用） | inline |
| `of_parse_phandle_with_args_map(np, list_name, stem_name, index, out_args)` | 走 `<stem>-map` 做一次重映射 | `drivers/of/base.c:1667` |
| `of_count_phandle_with_args(np, list_name, cells_name)` | 数一数有几项 | `drivers/of/base.c:1719` |
| `of_find_node_by_phandle(handle)` | 从 phandle 值直接找节点 | `drivers/of/base.c:1274` |
| `of_phandle_iterator_init(it, np, list_name, cells_name, cell_count)` | 迭代器初始化 | `drivers/of/base.c:1319` |
| `of_phandle_iterator_next(it)` | 迭代下一项 | `drivers/of/base.c:1404` |
| `of_for_each_phandle(it, err, np, ln, cn, cc)` | 上面两个的循环宏 | `include/linux/of.h` |

**`#<cells_name>` 这个机制是 DT 里最重要的约定**：
```dts
	clocks = <&clkctrl 5>, <&clkctrl 6>;   /* clkctrl 的 #clock-cells = <1> */
	dmas   = <&dmac 3 0>;                  /* dmac 的 #dma-cells = <2> */
```
⇒ **参数个数不由调用者决定，由被指向的那个节点声明** ——
这就是 `cells_name` 参数的意义（06 篇会展开）。

### §5.3 ⚠️ phandle 不是指针

| 断言 | 实际 |
|---|---|
| phandle 是一个地址 | **错** —— 它是一个 **u32 标签**（`typedef u32 phandle`） |
| DTS 里的 `<&foo>` 是取地址 | **错** —— dtc 编译时把 `&foo` 换成 `foo` 节点的 `phandle` 属性值 |
| 同一个 phandle 值全树唯一 | **对**（dtc 保证；overlay 场景要靠 `resolver.c` 重定位） |

⇒ **解析 phandle 需要一次全树查找**（`of_find_node_by_phandle()`，
`drivers/of/base.c:1274`）—— 有一个哈希缓存优化它（03 篇会讲）。

### §5.4 通用 ID 映射

| API | 说明 | 备注 |
|---|---|---|
| `of_map_id(np, id, map_name, map_mask_name, target, id_out)` | **`msi-map` / `iommu-map` 用的通用 RID 翻译** | `drivers/of/base.c:2213`（导出于同处） |

这就是 irq 那套 06 §6.2 提到的那个函数 —— **一套机制服务两种 map**。

---

## §6 匹配与身份

> 头文件：`include/linux/of.h` + `include/linux/of_device.h`
> 实现：`drivers/of/base.c` + `drivers/of/device.c`

| API | 说明 | 备注 |
|---|---|---|
| `of_device_is_compatible(device, compat)` | 这个节点的 `compatible` 列表里有 `compat` 吗 | `drivers/of/base.c:558`；**返回的是"匹配分数"**，越大越靠前 |
| `of_device_compatible_match(device, compat_list)` | 对一组 compat 求最高分 | `drivers/of/base.c:581` |
| `of_machine_is_compatible(compat)` | **根节点**的 compatible（判"我在哪块板子上"） | `drivers/of/base.c:602` |
| `of_device_is_available(device)` | `status` 不是 `"disabled"`/`"fail"` | `drivers/of/base.c:651` |
| `of_device_is_big_endian(device)` | 设备寄存器字节序（§4.5） | `drivers/of/base.c:696` |
| `of_match_node(matches, node)` | 在 `of_device_id` 表里找最佳匹配 | `drivers/of/base.c:1169` |
| `of_match_device(matches, dev)` | 同上，从 `struct device` 出发 | `drivers/of/device.c:35` |
| `of_device_get_match_data(dev)` | **取匹配到的那一项的 `.data`** —— 驱动区分型号的标准做法 | `drivers/of/device.c:249` |
| `of_node_name_eq(np, name)` | 比节点名（忽略 `@unit-address`） | `drivers/of/base.c:71` |
| `of_node_name_prefix(np, prefix)` | 节点名前缀 | `drivers/of/base.c:80` |
| `of_modalias_node(node, modalias, len)` | 生成 modalias | `drivers/of/base.c:1235` |
| `of_device_modalias(dev, str, len)` / `of_device_uevent_modalias(dev, env)` | 同上，`struct device` 版 | `drivers/of/device.c:335` / `:398` |
| `of_device_request_module(dev)` | 按 compatible 请求加载模块 | `drivers/of/device.c:315` |
| `of_match_ptr(_ptr)` | 宏：`CONFIG_OF` 关时展开成 **NULL** | `include/linux/of.h:410`（有 OF）· `:848`（无 OF） |

**`of_device_is_compatible()` 返回分数而不是布尔**：
`compatible = "vendor,soc-uart", "ns16550a";` 时，
匹配 `"vendor,soc-uart"` 得分**高于** `"ns16550a"` ——
⇒ 这就是"最具体的 compatible 写在最前面"这条 DT 惯例的代码依据。

⚠️`of_match_ptr()` 的存在意义：让驱动在 `!CONFIG_OF` 时也能编译
（`.of_match_table = of_match_ptr(my_of_match)`）。
**arm64 上 `CONFIG_OF` 恒为 y，所以它是恒等宏** ⇒ 新代码可以直接写表名。

---

## §7 地址翻译

> 头文件：`include/linux/of_address.h` · 实现：`drivers/of/address.c`

### §7.1 驱动用的三个

| API | 说明 | 备注 |
|---|---|---|
| `of_address_to_resource(dev, index, r)` | **把第 index 个 `reg` 翻译成 `struct resource`** | `include/linux/of_address.h:44`；实现 `drivers/of/address.c:887` |
| `of_iomap(device, index)` | **翻译 + `ioremap`，一步到位** | `include/linux/of_address.h:46`；实现 `drivers/of/address.c:908` |
| `of_io_request_and_map(device, index, name)` | 同上 + `request_mem_region()` | `include/linux/of_address.h:47`；实现 `drivers/of/address.c:950` |

⚠️ **platform 驱动通常不直接用这三个** ——
`platform_get_resource()` / `devm_platform_ioremap_resource()` 已经拿到翻译好的
`struct resource` 了（那是 `of_platform_device_create()` 在 §9 里填的）。
⇒ **直接用它们的场景**：你的节点不是 platform 设备（如 irqchip、clk 提供者、
或者你在处理**子节点**的 `reg`）。

### §7.2 底层与特殊用途

| API | 说明 | 备注 |
|---|---|---|
| `of_translate_address(np, addr)` | **沿父链走 `ranges` 做地址翻译** | `drivers/of/address.c:620` |
| `of_translate_dma_address(np, addr)` | 同上但走 `dma-ranges` | `drivers/of/address.c:667` |
| `__of_get_address(dev, index, bar_no, size, flags)` | 取原始的 `reg` 单元格 | `drivers/of/address.c:713`；通过 `of_get_address()` inline 包装（`include/linux/of_address.h:145`） |
| `of_dma_is_coherent(np)` | 有 `dma-coherent` 属性吗 | `drivers/of/address.c:1119` |
| `of_pci_address_to_resource(dev, bar, r)` | PCI BAR | `drivers/of/address.c:235` |
| `of_pci_range_to_resource(range, np, res)` | PCI range | `drivers/of/address.c:292` |
| `of_pci_range_parser_init(parser, node)` | 解析 PCI 的 `ranges` | `drivers/of/address.c:741` |
| `of_pci_dma_range_parser_init(parser, node)` | 解析 `dma-ranges` | `drivers/of/address.c:748` |
| `of_pci_range_parser_one(parser, range)` | 取一项 | `drivers/of/address.c:806` |

**`of_translate_address()` 是本子系统最"魔法"的一个函数**（03 篇会逐段剖析）：
它从当前节点一路往上走到根，每一层用父节点的 `ranges` 做一次窗口变换。
⇒ 这就是"节点的 `reg` 是相对父总线的，不是 CPU 物理地址"这条规则的实现（06 篇）。

⚠️`of_dma_is_coherent()` 的默认值取决于
`CONFIG_OF_DMA_DEFAULT_COHERENT`（`drivers/of/Kconfig:93`）——
也就是"**没写 `dma-coherent` 时算不算 coherent**"因平台而异。

---

## §8 中断

> 头文件：`include/linux/of_irq.h` · 实现：`drivers/of/irq.c`
>
> **本节只列 API 表** —— 完整的机制、DTS 写法和排查方法**见 irq 那套的 06 篇**。

| API | 说明 | 备注 |
|---|---|---|
| `of_irq_get(dev, index)` | **首选** —— 解析 + 建映射，**能返回 `-EPROBE_DEFER`** | `drivers/of/irq.c:475`（导出同处） |
| `of_irq_get_byname(dev, name)` | 按 `interrupt-names` | `drivers/of/irq.c:499` |
| `irq_of_parse_and_map(node, index)` | ⚠️ **老接口，失败返回 0** | `drivers/of/irq.c:52`（导出行） |
| `of_irq_count(dev)` | 数一数 | `include/linux/of_irq.h:44` |
| `of_irq_parse_one(device, index, out_irq)` | 只解析不映射 | `drivers/of/irq.c:413` |
| `of_irq_parse_raw(addr, out_irq)` | **`interrupt-map` 的遍历主体** | `drivers/of/irq.c:344` |
| `of_irq_find_parent(child)` | 沿 `interrupt-parent` 往上找 | `drivers/of/irq.c:84` |
| `of_irq_to_resource(dev, index, r)` / `_table(...)` | 填进 `struct resource` | `drivers/of/irq.c:448` / `:537` |
| `of_msi_configure(dev, np)` | 装 MSI 域 | `drivers/of/irq.c:758` |

---

## §9 把 DT 节点变成 `platform_device`

> 头文件：`include/linux/of_platform.h` · 实现：`drivers/of/platform.c`

### §9.1 这是 arm64 上最重要的一块

**arm64 上绝大多数驱动是 platform 驱动，而它们的 `platform_device`
全部是 `drivers/of/platform.c` 从 DT 造出来的。**

| API | 说明 | 备注 |
|---|---|---|
| `of_platform_default_populate(root, lookup, parent)` | **启动时把整棵树填成 platform 设备** | `include/linux/of_platform.h:80`；实现 `drivers/of/platform.c:504` |
| `of_platform_populate(root, matches, lookup, parent)` | 同上但自己给"哪些节点要继续下钻"的匹配表 | `include/linux/of_platform.h:76`；实现 `drivers/of/platform.c:495` |
| `devm_of_platform_populate(dev)` | **复合设备用：把我的子节点填成 platform 设备**（devres 版） | `include/linux/of_platform.h:85`；实现 `drivers/of/platform.c:692` |
| `devm_of_platform_depopulate(dev)` | 反操作 | `drivers/of/platform.c:724` |
| `of_platform_depopulate(parent)` | 非 devres 版 | `drivers/of/platform.c:653` |
| `of_platform_device_create(np, bus_id, parent)` | 单独造一个 | `drivers/of/platform.c:217` |
| `of_platform_device_destroy(dev, data)` | 销毁一个 | `drivers/of/platform.c:635` |
| `of_device_alloc(np, bus_id, parent)` | 只分配不注册（自己要改字段时用） | `drivers/of/platform.c:155` |
| `of_find_device_by_node(np)` | **从 DT 节点反查 `platform_device`** | `drivers/of/platform.c:55`；**会 `get_device()`，要 `put_device()`** |
| `of_platform_bus_probe(root, matches, parent)` | **老接口** | `drivers/of/platform.c:444` |
| `of_device_register(pdev)` / `of_device_unregister(pdev)` | 老式包装 | `drivers/of/device.c:231` / `:237` |

### §9.2 `of_platform_default_populate()` 只下钻"总线型"节点

关键问题：**DT 是一棵树，但 `platform_device` 是平的** ——
哪些子节点也要变成设备？

答案：**只有父节点的 compatible 在一张白名单里，才继续往下填**。
⇒ 那张白名单是 `of_default_bus_match_table[]`（`drivers/of/platform.c` 里的静态表）。

⚠️ **这是 06 篇会详细讲的一个坑**：
你的设备节点下面的子节点**默认不会**变成 platform 设备 ——
除非你自己 `devm_of_platform_populate()`。

### §9.3 `struct of_dev_auxdata`：给老平台打补丁

`include/linux/of_platform.h:22` 附近的注释说明了它的用途：
让 `of_platform_populate()` 的调用者**覆盖**某个节点生成的设备名或 platform_data。

⚠️ **新平台不该用它** —— 它是给"从板文件迁移到 DT"的过渡期设计的。

---

## §10 fwnode 统一接口

> 实现：`drivers/of/property.c:1485` 的 `of_fwnode_ops`

| 事实 | 说明 |
|---|---|
| `of_fwnode_ops` 是**唯一**导出的符号 | `drivers/of/property.c:1485` |
| 它是什么 | 一张 `fwnode_operations` 表，把 `device_node` 包装成通用的 `fwnode_handle` |
| 谁用它 | **不是驱动** —— 是 `device_property_read_u32()` 那一套通用接口 |
| `of_fwnode_handle(node)` 宏 | `include/linux/of.h:172`（有 OF）· `:541`（无 OF ⇒ NULL） |

**它解决的问题**：同一个驱动要能同时支持 DT 和 ACPI。
⇒ 驱动用 `device_property_read_u32(dev, "foo", &v)`，
底层根据 `dev->fwnode` 是 of 还是 acpi 分派。

⚠️ **本套 arm64-only 不覆盖 ACPI**（99 §2.7），
所以本节只说明 `of_fwnode_ops` 的定位，不展开 fwnode 抽象层。

**对驱动作者的实际建议**：
只支持 DT 就用 `of_property_read_u32()`（§4）；
要同时支持 ACPI 就用 `device_property_read_u32()`。

---

## §11 graph：端口与端点

> 头文件：`include/linux/of_graph.h`（127 行）· 实现：`drivers/of/property.c`

用于**媒体和显示**：一条数据通路上多个设备要互相指向对方
（摄像头 → CSI → ISP，或 显示控制器 → 桥 → 面板）。

| API | 说明 | 位置 |
|---|---|---|
| `of_graph_is_present(node)` | 有 `ports`/`port` 子节点吗 | `drivers/of/property.c:55` |
| `of_graph_parse_endpoint(node, endpoint)` | 解析出 port/endpoint 编号 | `drivers/of/property.c:600` |
| `of_graph_get_port_by_id(parent, id)` | 按 `reg` 取 port | `drivers/of/property.c:632` |
| `of_graph_get_next_endpoint(parent, prev)` | 遍历 endpoint | `drivers/of/property.c:699` |
| `of_graph_get_endpoint_by_regs(parent, port_reg, reg)` | 按两级编号取 | `drivers/of/property.c:726` |
| `of_graph_get_remote_endpoint(node)` | **顺着 `remote-endpoint` 走到对面** | `drivers/of/property.c:740` |
| `of_graph_get_port_parent(node)` | endpoint → 它所属的设备节点 | `drivers/of/property.c:772` |
| `of_graph_get_remote_port_parent(node)` | **一步到对面的设备节点** | `drivers/of/property.c:795` |
| `of_graph_get_remote_port(node)` | 对面的 port 节点 | `drivers/of/property.c:814` |
| `of_graph_get_endpoint_count(np)` | 数一数 | `drivers/of/property.c:826` |
| `of_graph_get_remote_node(node, port, endpoint)` | 三个编号一步到位 | `drivers/of/property.c:864` |

**全部返回的节点都要 `of_node_put()`**（§1.3）。

⚠️ 本套对 graph 只给 API 表（三等证据，99 §2.3）——
它的 DTS 结构（`ports` / `port@N` / `endpoint@M` / `remote-endpoint`）在 06 篇简述。

---

## §12 reserved-memory

> 头文件：`include/linux/of_reserved_mem.h`（84 行）· 实现：`drivers/of/of_reserved_mem.c`

| API | 说明 | 备注 |
|---|---|---|
| `of_reserved_mem_device_init_by_idx(dev, np, idx)` | **把 `np` 的第 idx 个 `memory-region` 绑到 `dev` 的 DMA 上** | `include/linux/of_reserved_mem.h:35`；实现导出于 `drivers/of/of_reserved_mem.c:377` |
| `of_reserved_mem_device_init(dev)` | inline：`(dev, dev->of_node, 0)` | `include/linux/of_reserved_mem.h:79-82` |
| `of_reserved_mem_device_init_by_name(dev, np, name)` | **按 `memory-region-names` 取** | `include/linux/of_reserved_mem.h:37`；导出于 `drivers/of/of_reserved_mem.c:396` |
| `of_reserved_mem_device_release(dev)` | 反操作 | 导出于 `drivers/of/of_reserved_mem.c:424` |
| `of_reserved_mem_lookup(np)` | **拿 `struct reserved_mem`（base / size / name）** | 导出于 `drivers/of/of_reserved_mem.c:450` |
| `RESERVEDMEM_OF_DECLARE(name, compat, init)` | **登记一种 reserved-memory 的初始化器** | `include/linux/of_reserved_mem.h:32` |

**这一组在 remoteproc 那套的 05 §3 和 06 §3 用得很多** ——
`of_reserved_mem_device_init_by_idx()` 就是那里 `vdev0buffer` 那条路。

⚠️`of_reserved_mem_device_init_by_name()` 走的 `memory-region-names`
在 remoteproc 里**零使用者**（remoteproc 那套的设备树篇里点明过）—— 这里是它的提供方。

---

## §13 动态修改：changeset 与 overlay

> 实现：`drivers/of/dynamic.c` + `drivers/of/overlay.c` + `drivers/of/resolver.c`
>
> ⚠️ **arm64 上通常不开**（`OF_OVERLAY` 默认 n，`drivers/of/Kconfig:79`）。
> 本节只列 API，机制见 04 篇（三等证据，99 §2.3）。

### §13.1 changeset（低层，事务式）

| API | 说明 | 位置 |
|---|---|---|
| `of_changeset_init(ocs)` | 初始化一个事务 | `drivers/of/dynamic.c:664`（导出于 `:669`） |
| `of_changeset_destroy(ocs)` | 销毁 | `drivers/of/dynamic.c:679`（导出于 `:697`） |
| `of_changeset_action(ocs, action, np, prop)` | 往事务里加一条动作 | `drivers/of/dynamic.c:913`（导出于 `:937`） |
| `of_changeset_apply(ocs)` | **提交** | `drivers/of/dynamic.c:790`（导出于 `:800`） |
| `of_changeset_revert(ocs)` | **回滚** | `drivers/of/dynamic.c:884`（导出于 `:894`） |
| `of_detach_node(np)` | 摘掉一个节点 | `drivers/of/dynamic.c:290`（导出于 `:310`）；配对的 `of_attach_node()` 在 `drivers/of/dynamic.c:237`（**不导出**） |
| `of_add_property(np, prop)` / `of_remove_property(np, prop)` | 加/删属性 | `drivers/of/base.c:1770` / `:1825` |

### §13.2 通知链

| API | 说明 | 位置 |
|---|---|---|
| `of_reconfig_notifier_register(nb)` / `_unregister(nb)` | 监听树的变化 | `drivers/of/dynamic.c:59` / `:65` |
| `of_reconfig_get_state_change(action, arg)` | 判断这次变化是否影响 `status` | `drivers/of/dynamic.c:188` |
| `of_overlay_notifier_register(nb)` / `_unregister(nb)` | overlay 专用通知 | `drivers/of/overlay.c:143` / `:153` |

### §13.3 overlay（高层）

| API | 说明 | 位置 |
|---|---|---|
| `of_overlay_fdt_apply(overlay_fdt, size, ovcs_id, target_base)` | **打一个 overlay** | `drivers/of/overlay.c:1057` |
| `of_overlay_remove(ovcs_id)` | 撤一个 | `drivers/of/overlay.c:1243` |
| `of_overlay_remove_all()` | 全撤 | `drivers/of/overlay.c:1266` |
| `of_resolve_phandles(tree)` | overlay 里的 phandle 重定位 | `drivers/of/resolver.c:340` |
| `of_fdt_unflatten_tree(blob, dad, mynodes)` | 把一段 FDT 展成树 | `drivers/of/fdt.c:467` |

---

## §14 错误码语义

### §14.1 全量普查

`drivers/of/` 的**产品代码**里 `-E*` 共出现 **228** 次。
⚠️ 这个数字的口径是**这 15 个文件**：`base.c` · `property.c` · `address.c` ·
`platform.c` · `device.c` · `irq.c` · `fdt.c` · `dynamic.c` · `overlay.c` ·
`of_reserved_mem.c` · `resolver.c` · `kobj.c` · `fdt_address.c` · `of_numa.c` · `kexec.c`
—— **排除了 `unittest.c`（3647 行的测试）和 `pdt.c`（Sparc 专用）**。

| 错误码 | 次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | **106**（46%） | ⚠️ **"属性不存在"** + 参数不合法 + 节点为 NULL —— **三种情况同一个码**（§14.2） |
| `-ENOMEM` | 27 | 分配节点、属性、字符串失败 |
| `-ENOENT` | **24** | ⚠️ **不是"属性不存在"** —— 它是 **phandle 列表家族专用**（§14.2.1） |
| `-ENODEV` | 21 | 找不到设备 / 不可用 |
| `-ENODATA` | **19** | **"属性存在但没有值"** |
| `-EOVERFLOW` | 10 | **"属性的值比你给的缓冲区长"** |
| `-EPROBE_DEFER` | **5** | **"被依赖的东西还没就绪"** |
| `-EBUSY` | 5 | —— |
| `-EILSEQ` | 4 | 字符串属性不是合法的 NUL 结尾序列 |
| `-ENOSYS` / `-EFAULT` | 各 2 | —— |
| `-ENXIO` / `-EEXIST` / `-EALREADY` | 各 1 | —— |

```bash
# 精确口径（15 个产品文件，排除 unittest.c 和 pdt.c）
F="base property address platform device irq fdt dynamic overlay of_reserved_mem resolver kobj fdt_address of_numa kexec"
for n in $F; do echo drivers/of/$n.c; done | xargs grep -ho '\-E[A-Z_]*' | sort | uniq -c | sort -rn
```

按文件（前六）：`property.c` **50** · `overlay.c` **34** · `base.c` **32** ·
`address.c` 16 · `fdt.c` 15 · `of_reserved_mem.c` 12。

`property.c` 最多（50）是因为**每个类型化读取函数都有 3–4 条参数检查**。

### §14.2 四个"属性读取专用"的错误码

这四个的区分是本子系统**最有实用价值**的一组语义。
全部由 `of_find_property_value_of_size()`（`drivers/of/property.c:107-125`）
和 `of_property_read_string()`（`drivers/of/property.c:440-452`）分出来：

| 错误码 | 含义 | 你该怎么办 |
|---|---|---|
| ⚠️`-EINVAL` | **属性根本不存在**（`property.c:113` 的 `if (!prop)`）—— ⚠️ **`np`/`propname` 为 NULL 也是这个码**，两者分不开 | **可选属性 ⇒ 用默认值；必需属性 ⇒ 报错** |
| `-ENODATA` | **属性存在但 `value` 为空**（`property.c:115`） | DTS 写了 `foo;`（无值）却当整数读 |
| `-EOVERFLOW` | **属性的字节数不够（或不是元素大小的整数倍）**（`property.c:117` 和 `:119`，分别对应"短于 min"和"长于 max"） | **DTS 里的元素个数和你要读的不一致** |
| `-EILSEQ` | **字符串属性不是 NUL 结尾**（`property.c:449`，只有字符串家族会返回） | DTS 里用 `[...]` 字节数组写了字符串 |

⇒ **`-EINVAL` 和 `-ENODATA` 的区别是"没有"和"空"** ——
判断"这个可选属性有没有配"要判 `-EINVAL`，判 `!= 0` 会把 `-EOVERFLOW` 也算进去。

### §14.2.1 ⚠️`-ENOENT` **不属于**这一组（最容易搞错的一条）

**属性读取家族一次都不返回 `-ENOENT`**。它属于 **phandle 列表家族**：

| 谁返回 | 什么情况 | 位置 |
|---|---|---|
| `of_phandle_iterator_init()` | **列表属性本身不存在**（如没写 `clocks`） | `drivers/of/base.c:1308` |
| `of_phandle_iterator_next()` | **迭代到末尾**（index 超出实际条目数） | `drivers/of/base.c:1331` |
| `of_parse_phandle_with_args()` | **该 index 上是空条目**（`phandle == 0`） | `drivers/of/base.c:1443` |

源码自己在 `drivers/of/base.c:1469-1470` 的注释里把两种结果并列写清了：
`-ENOENT` = index 指向空 phandle，`-EINVAL` = 数据解析错。

⚠️ **实际后果**：`if (ret == -ENOENT) use_default();`
用在 `of_property_read_u32()` 上**永远不会成立**（该判 `-EINVAL`），
用在 `of_parse_phandle_with_args()` 上才是对的。

**`of_property_read_bool()` 不返回错误码** —— 它返回 bool（§4.2），
所以"存在性"检查用它最省事。

### §14.3 `-EPROBE_DEFER` 的五处

**它是 DT 世界里最重要的一个错误码**：
"我依赖的那个提供者（clk / gpio / phy / 中断控制器）还没 probe，请稍后重试。"

| 谁返回它 | 说明 |
|---|---|
| `of_irq_get()`（`drivers/of/irq.c:475`） | 中断控制器还没就绪（irq 那套 06 §1.3） |
| `of_dma_configure_id()`（`drivers/of/device.c:224`） | IOMMU 还没就绪 |
| `drivers/of/property.c` 里的 fwnode link 相关 | 依赖关系推导 |

⇒ **驱动 probe 里拿到 `-EPROBE_DEFER` 应该原样 `return`**，
不要打错误日志（设备模型会自动重试）。

### §14.4 高频错误的第一嫌疑

| 拿到 | 第一嫌疑 |
|---|---|
| `of_property_read_u32()` → `-EINVAL` | **DTS 里没写这个属性**（或写在了别的节点上）；其次才是 `np` 为 NULL（`dev->of_node` 为空 —— 不是 DT 探测出来的设备） |
| `of_parse_phandle_with_args()` → `-ENOENT` | **列表属性没写，或 index 超出条目数，或该条目是空 phandle**（§14.2.1） |
| `→ -ENODATA` | DTS 写成了 `prop;`（bool 形式）却当整数读 |
| `→ -EOVERFLOW` | **元素个数不对** —— 比如你 `read_u32_array(…, 4)` 但 DTS 只写了 3 个 |
| `of_address_to_resource()` → `-EINVAL` | 节点没有 `reg`，或父节点的 `#address-cells`/`#size-cells` 不匹配 |
| `of_iomap()` → **NULL** | 翻译失败或 `ioremap` 失败（⚠️ **不返回错误码**） |
| 任何 → `-EPROBE_DEFER` | **正常** —— 原样返回 |
| `of_parse_phandle()` → **NULL** | 属性不存在、index 越界、或 phandle 指向的节点找不到（⚠️ **不返回错误码**） |
| `of_find_*()` → **NULL** | 没找到（⚠️ **不返回错误码**） |
| `of_platform_populate()` → 非 0 | 某个子节点创建失败 —— 看 dmesg |

⚠️ **注意有一大类 API 用 NULL 表示失败而不是错误码**
（`of_find_*` / `of_parse_phandle` / `of_get_property` / `of_iomap`）——
⇒ 这类失败**分不清"没配"和"配错了"**，和 irq 那套的
`irq_of_parse_and_map()` 返回 0 是同一类设计债（03 Part C 会讨论）。

---

**下一篇**：02 篇讲 `device_node` / `property` / `of_phandle_args` 的关系，
以及 **`device_node` ↔ `platform_device` ↔ `struct device` 三者的分工**。
