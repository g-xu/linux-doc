---
title: 扩展机制
---

# of 04：独立框架与展示层

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 组织轴

### §0.1 一节一个独立框架，节与节之间互不调用

本篇的组织方式和 03 篇**相反**：03 是一条时间线（必须顺读），
本篇是**五个互不依赖的模块**（可以任意跳读）。⚠️ **跳读是设计意图**。

⚠️ 唯一的例外：**§1（overlay）建立在 §2（changeset）之上** ——
本篇把它们放在同一节里讲（§1.2 是 changeset、§1.3 才是 overlay）。

### §0.2 分诊表：哪几个在 arm64 上真会碰到

| § | 框架 | 文件 / 行数 | Kconfig | arm64 相关度 |
|---|---|---|---|---|
| §1 | **changeset + overlay** | `dynamic.c` **937** + `overlay.c` **1266** + `resolver.c` **340** | `OF_DYNAMIC` / `OF_OVERLAY` / `OF_RESOLVE`（通常 **n**） | 只有 FPGA / 可插拔背板 |
| §2 | **reserved-memory** | `of_reserved_mem.c` **450** | `OF_RESERVED_MEM`（y） | **remoteproc / CMA / 安全内存都靠它** |
| §3 | **graph（端口图）** | `property.c` 里约 300 行 | 无独立选项 | 媒体 / 显示 |
| §4 | **sysfs 视图** | `kobj.c` **165** | `OF_KOBJ`（y） | **`/sys/firmware/devicetree/` 是最重要的调试出口** |
| §5 | **unittest** | `unittest.c` **3647** | `OF_UNITTEST`（n） | 只在改 of 核心时用；**但它是抓引用计数泄漏的唯一工具** |
| §6 | **kexec DTB 重建** | `kexec.c` **472** | `KEXEC_FILE` | 只有 kexec 场景 |
| §7 | **NUMA 拓扑** | `of_numa.c` **186** | `OF_NUMA` | 服务器级 arm64 |
| §8 | **Sparc PROM 树** | `pdt.c` **213** | `OF_PROMTREE` | **arm64 完全不涉及** |

**只有 §2 和 §4 是"一定会碰到"的**，先读这两节。

### §0.3 各框架挂在 03 时间线的哪一点

```
   §A2 早期扫描 ──▶ §2  early_init_fdt_scan_reserved_mem() ⇒ reserved_mem[]
       │
   §A3 unflatten
       │
   §A4 of_core_init ──▶ §4  建 /sys/firmware/devicetree/base/
       │
   §A5 设备填充
       │
   §A6 驱动 probe ──▶ §2  of_reserved_mem_device_init_by_idx()
       │           ──▶ §3  of_graph_get_remote_port_parent()
       │
   运行期（极少）──▶ §1  of_overlay_fdt_apply() / of_changeset_apply()
   late_initcall ──▶ §5  unittest
```

| 挂载点 | 位置 |
|---|---|
| §2 早期扫描 | `drivers/of/fdt.c:630`（`early_init_fdt_scan_reserved_mem()`）⇒ `drivers/of/of_reserved_mem.c:254`（`fdt_init_reserved_mem()`） |
| §2 驱动侧绑定 | `drivers/of/of_reserved_mem.c:331` |
| §4 sysfs 建树 | `drivers/of/kobj.c:115`（`__of_attach_node_sysfs()`） |
| §1 overlay 入口 | `drivers/of/overlay.c:967` |
| §1 changeset 入口 | `drivers/of/dynamic.c:790`（apply）· `:884`（revert） |
| §3 graph 主力 | `drivers/of/property.c:795` |

---

## §1 changeset 与 overlay

### §1.1 两层的分工

```
   ┌── overlay（高层，drivers/of/overlay.c）──────────────────────┐
   │  输入：一段 overlay FDT（.dtbo）                              │
   │  · 解析 __symbols__ / __fixups__，重定位 phandle（resolver.c）│
   │  · 遍历 overlay 树，为每个新节点/属性生成一条 changeset entry  │
   │  · 调 of_changeset_apply()                                   │
   └────────────────────────┬────────────────────────────────────┘
                            │
   ┌────────────────────────▼────────────────────────────────────┐
   │  changeset（低层，drivers/of/dynamic.c）                      │
   │  · 一个"事务"：一串 of_changeset_entry                        │
   │  · apply：逐条执行，任一失败则把已执行的逐条 revert            │
   │  · 每条执行时发 OF_RECONFIG_* 通知                            │
   └──────────────────────────────────────────────────────────────┘
```

**changeset 是"事务"，overlay 是"事务的生成器"** ——
⇒ 你可以只用 changeset（手工增删节点），不一定要用 overlay。

### §1.2 changeset：五种动作 + 事务语义

| API | 说明 | 位置 |
|---|---|---|
| `of_changeset_init(ocs)` | 初始化 | `drivers/of/dynamic.c:664` |
| `of_changeset_action(ocs, action, np, prop)` | 加一条动作 | `drivers/of/dynamic.c:913` |
| `of_changeset_apply(ocs)` | **提交（原子）** | `drivers/of/dynamic.c:790`（内部 `__of_changeset_apply()` 在 `:766`） |
| `of_changeset_revert(ocs)` | **回滚** | `drivers/of/dynamic.c:884`（内部 `__of_changeset_revert()` 在 `:859`） |
| `of_changeset_destroy(ocs)` | 销毁 | `drivers/of/dynamic.c:679` |

**五种动作**（`OF_RECONFIG_*`，名字表在 `drivers/of/dynamic.c:69-73`）：

| 动作 | 行号 |
|---|---|
| `OF_RECONFIG_ATTACH_NODE` | `drivers/of/dynamic.c:69` |
| `OF_RECONFIG_DETACH_NODE` | `drivers/of/dynamic.c:70` |
| `OF_RECONFIG_ADD_PROPERTY` | `drivers/of/dynamic.c:71` |
| `OF_RECONFIG_REMOVE_PROPERTY` | `drivers/of/dynamic.c:72` |
| `OF_RECONFIG_UPDATE_PROPERTY` | `drivers/of/dynamic.c:73` |

**`of_changeset_action()`（`drivers/of/dynamic.c:913`）里那句
`ce->np = of_node_get(np)`（`drivers/of/dynamic.c:927`）是事务持有引用的地方** ——
⇒ 所以销毁时要逐条 `of_node_put(ce->np)`
（`__of_changeset_entry_destroy()`，`drivers/of/dynamic.c:466`，那一句在 `:478`）。

**apply / revert 各拆成"执行"和"通知"两步**（这是本框架的事务核心）：

| 步 | apply | revert |
|---|---|---|
| 执行 | `__of_changeset_apply_entries()`（`drivers/of/dynamic.c:709`） | `__of_changeset_revert_entries()`（`drivers/of/dynamic.c:813`） |
| 通知 | `__of_changeset_apply_notify()`（`drivers/of/dynamic.c:738`） | `__of_changeset_revert_notify()`（`drivers/of/dynamic.c:839`） |
| 合起来 | `__of_changeset_apply()`（`drivers/of/dynamic.c:766`） | `__of_changeset_revert()`（`drivers/of/dynamic.c:859`） |

**"执行"和"通知"分开的原因**：
执行要持 `devtree_lock`（不能睡，03 §B1.3），
而通知链的回调**可能睡**（如 `of_platform_populate()` 要分配内存）。
⇒ 所以先在锁内改完树，解锁后再逐条发通知。

单条 entry 的三个辅助：
`__of_changeset_entry_apply()`（`drivers/of/dynamic.c:572`）·
`__of_changeset_entry_revert()`（`drivers/of/dynamic.c:649`）·
`__of_changeset_entry_invert()`（`drivers/of/dynamic.c:507`，**回滚就靠它取反**）。
还有一个调试用的 `__of_changeset_entry_dump()`（`drivers/of/dynamic.c:484`，
`!DEBUG` 时是空实现 `:501`）。

### §1.3 通知链：`OF_RECONFIG_*`

| API | 说明 | 位置 |
|---|---|---|
| `of_reconfig_notifier_register(nb)` | 监听树的变化 | `drivers/of/dynamic.c:59` |
| `of_reconfig_notifier_unregister(nb)` | —— | `drivers/of/dynamic.c:65` |
| `of_reconfig_get_state_change(action, arg)` | **判断这次变化让设备"从可用变不可用"还是反过来** | `drivers/of/dynamic.c:188`（逻辑在 `:119-132`） |

**三个返回值**（`drivers/of/dynamic.c:108-110` 的注释）：
`OF_RECONFIG_CHANGE_REMOVE`（enabled → disabled）·
`OF_RECONFIG_CHANGE_ADD`（disabled → enabled）·
`OF_RECONFIG_NO_CHANGE`（`drivers/of/dynamic.c:132`）。

⇒ **`drivers/of/platform.c` 就注册了这个通知**，
用来在 overlay 加节点时自动 `of_platform_populate()`、
在删节点时自动 `of_platform_depopulate()`。

### §1.4 overlay 的十步

| 步 | 动作 | 位置 |
|---|---|---|
| ① | `of_overlay_fdt_apply()` 入口 | `drivers/of/overlay.c:967` |
| ② | unflatten overlay FDT ⇒ 一棵独立的树 | `drivers/of/fdt.c:461` 的 `of_fdt_unflatten_tree()` |
| ③ | `of_resolve_phandles()` —— **重定位 phandle** | `drivers/of/resolver.c:340` |
| ④ | `init_overlay_changeset()` | `drivers/of/overlay.c:728` |
| ⑤ | `build_changeset()` —— **遍历 overlay 树生成 entry** | `drivers/of/overlay.c:632` |
| ⑥ | `build_changeset_next_level()`（递归） | `drivers/of/overlay.c:477` |
| ⑦ | `add_changeset_node()` / `add_changeset_property()` | `drivers/of/overlay.c:405` / `:300` |
| ⑧ | `build_changeset_symbols_node()` —— 合并 `__symbols__` | `drivers/of/overlay.c:509` |
| ⑨ | `changeset_dup_entry_check()` —— 查重 | `drivers/of/overlay.c:607`（辅助在 `:528` · `:561`） |
| ⑩ | `of_overlay_apply()` ⇒ `of_changeset_apply()` | `drivers/of/overlay.c:901` |
| 通知 | `overlay_notify()` | `drivers/of/overlay.c:155` |
| 全局链表 | `ovcs_list` | `drivers/of/overlay.c:122` |
| `struct overlay_changeset` | `drivers/of/overlay.c:69`（doc 在 `:57`） |

### §1.5 撤销的限制

| API | 说明 | 位置 |
|---|---|---|
| `of_overlay_remove(ovcs_id)` | 撤一个 | `drivers/of/overlay.c:1174` |
| `of_overlay_remove_all()` | 全撤 | `drivers/of/overlay.c:1252` |
| `overlay_removal_is_ok()` | **能不能撤的判断** | `drivers/of/overlay.c:1126` |
| `node_overlaps_later_cs()` | **后面的 overlay 是否动了同一个节点** | `drivers/of/overlay.c:1087` |
| `free_overlay_changeset()` | 释放 | `drivers/of/overlay.c:835` |

**`overlay_removal_is_ok()` 的规则**：
**只能按后进先出的顺序撤** —— 如果后面的 overlay 动了同一个节点，
当前这个就撤不掉（`drivers/of/overlay.c:1087` 的检查）。

⚠️`Documentation/devicetree/overlay-notes.rst`（150 行）明确列了 overlay 的限制。
⇒ 本套对 overlay 只讲到这个层次（三等证据，99 §2.3）。

---

## §2 reserved-memory

### §2.1 两个阶段

```
   ①【早期，没有 device_node】
      early_init_fdt_scan_reserved_mem()          drivers/of/fdt.c:630
        · 直接在扁平 DTB 上扫 /reserved-memory
        · 对每个子节点：
            有 reg ⇒ memblock_reserve 那段
            只有 size ⇒ 记下来待分配
        · 填进全局 reserved_mem[] 数组
      early_init_fdt_reserve_self()               drivers/of/fdt.c:654
        · 把 DTB 自己占的内存也 reserve

   ②【树建好之后】
      fdt_init_reserved_mem()                     drivers/of/of_reserved_mem.c:254
        · __reserved_mem_alloc_size()  给"只有 size"的分配实际地址
        · __rmem_check_for_overlap()   检查区间重叠
        · __reserved_mem_init_node()   调各 RESERVEDMEM_OF_DECLARE 的初始化器

   ③【驱动 probe 时】
      of_reserved_mem_device_init_by_idx(dev, np, idx)
                                                  drivers/of/of_reserved_mem.c:331
        · 顺着 np 的 memory-region 第 idx 项找到那块
        · 把它绑到 dev 的 DMA（dma_declare_coherent / CMA）
```

| 元素 | 位置 |
|---|---|
| `MAX_RESERVED_REGIONS` = **64** | `drivers/of/of_reserved_mem.c:29` |
| `reserved_mem[]` 全局数组 | `drivers/of/of_reserved_mem.c:30` |
| `__reserved_mem_alloc_size()` | `drivers/of/of_reserved_mem.c:85` |
| `__reserved_mem_init_node()` | `drivers/of/of_reserved_mem.c:178` |
| `__rmem_check_for_overlap()` | `drivers/of/of_reserved_mem.c:224` |
| `fdt_init_reserved_mem()` | `drivers/of/of_reserved_mem.c:254` |
| `of_reserved_mem_device_init_by_idx()` | `drivers/of/of_reserved_mem.c:331`（导出于 `:377`） |
| `of_reserved_mem_device_init_by_name()` | 导出于 `drivers/of/of_reserved_mem.c:396` |
| `of_reserved_mem_device_release()` | 导出于 `drivers/of/of_reserved_mem.c:424` |
| `of_reserved_mem_lookup()` | `drivers/of/of_reserved_mem.c:435`（导出于 `:450`） |
| `RESERVEDMEM_OF_DECLARE` 宏 | `include/linux/of_reserved_mem.h:32` |

**`MAX_RESERVED_REGIONS = 64` 是一个硬上界**（`drivers/of/of_reserved_mem.c:29`）——
⚠️ 一块板子上 reserved-memory 子节点超过 64 个就会有节点被丢掉。

### §2.2 三种"谁来用这块内存"

| compatible | 谁初始化 | 效果 |
|---|---|---|
| `"shared-dma-pool"` | **CMA 或 coherent pool** | `dma_alloc_coherent()` 从这块出 |
| **无 compatible** | **没人** —— 只是"别用这块内存" | 只有 `memblock_reserve` 的效果 |
| 厂商自定义 | `RESERVEDMEM_OF_DECLARE()` 登记的初始化器 | 自定义 |

**这就是 remoteproc 那套 06 §3.4 里"纯固件段可以不写 compatible"的依据**。

`__reserved_mem_init_node()`（`drivers/of/of_reserved_mem.c:178`）遍历
`__reservedmem_of_table` 找匹配的初始化器 —— 找不到就什么都不做（**不报错**）。

### §2.3 ⚠️ 两个静默陷阱

| 陷阱 | 说明 |
|---|---|
| **超过 64 个区间** | `MAX_RESERVED_REGIONS`（`drivers/of/of_reserved_mem.c:29`）—— 会打日志但驱动侧只表现为"找不到那块" |
| **区间重叠** | `__rmem_check_for_overlap()`（`drivers/of/of_reserved_mem.c:224`）会**打警告但不阻止** ⇒ 两个设备用同一块内存，症状极难查 |

**`no-map` 属性的作用**（remoteproc 那套 06 §3.4 讲过）：
让这块内存**不进入内核线性映射** ⇒ 避免 cache 属性冲突。

---

## §3 graph：端口与端点

### §3.1 DTS 结构

```dts
	camera@10 {
		port {                          /* 只有一个 port 时可以省掉 ports */
			cam_out: endpoint {
				remote-endpoint = <&csi_in>;
			};
		};
	};

	csi@20000000 {
		ports {
			#address-cells = <1>;
			#size-cells = <0>;
			port@0 {
				reg = <0>;
				csi_in: endpoint {
					remote-endpoint = <&cam_out>;
				};
			};
			port@1 {
				reg = <1>;
				csi_out: endpoint { … };
			};
		};
	};
```

**三层结构**：`ports` → `port@N` → `endpoint@M`，
两端用 `remote-endpoint` **互相指向**（**必须双向写**）。

### §3.2 API 与它们走的路

| API | 从 → 到 | 位置 |
|---|---|---|
| `of_graph_is_present(node)` | 有没有 `ports`/`port` | `drivers/of/property.c:55` |
| `of_graph_parse_endpoint(node, endpoint)` | endpoint 节点 → (port 号, endpoint 号) | `drivers/of/property.c:600` |
| `of_graph_get_port_by_id(parent, id)` | 设备 → 第 id 个 port | `drivers/of/property.c:632` |
| `of_graph_get_next_endpoint(parent, prev)` | 遍历设备的所有 endpoint | `drivers/of/property.c:699` |
| `of_graph_get_endpoint_by_regs(parent, port_reg, reg)` | 按两级编号取 | `drivers/of/property.c:726` |
| `of_graph_get_remote_endpoint(node)` | endpoint → **对面的 endpoint** | `drivers/of/property.c:740` |
| `of_graph_get_port_parent(node)` | endpoint → **它自己的设备节点** | `drivers/of/property.c:772` |
| `of_graph_get_remote_port_parent(node)` | endpoint → **对面的设备节点**（一步） | `drivers/of/property.c:795` |
| `of_graph_get_remote_port(node)` | endpoint → 对面的 port | `drivers/of/property.c:814` |
| `of_graph_get_endpoint_count(np)` | 数一数 | `drivers/of/property.c:826` |
| `of_graph_get_remote_node(node, port, endpoint)` | 三个编号一步到对面设备 | `drivers/of/property.c:864` |

**`of_graph_get_remote_port_parent()`（`drivers/of/property.c:795`）是最常用的一个** ——
"我这个 endpoint 对面接的是哪个设备"。

⚠️ **全部返回的节点都要 `of_node_put()`**（01 §1.3）。

### §3.3 ⚠️ 为什么需要它

普通的 phandle（`clocks = <&clk 5>`）是**单向**的：消费者指向提供者。
但媒体通路上的两个设备是**对等的** —— 双方都要知道对面是谁。
⇒ 用 `remote-endpoint` 双向写就解决了，代价是**DTS 要写两遍**。

本套对 graph 只给 API 表和 DTS 结构（三等证据，99 §2.3）——
它的框架层（`v4l2-async` / `drm_bridge`）不在本套范围。

---

## §4 sysfs 视图：`/sys/firmware/devicetree/`

### §4.1 它是最重要的调试出口

```sh
# 整棵树以目录形式呈现
ls /sys/firmware/devicetree/base/
# → #address-cells  #size-cells  chosen/  compatible  memory@80000000/  soc/  ...

# 每个属性是一个文件（内容是原始字节，大端）
hexdump -C /sys/firmware/devicetree/base/soc/uart@12340000/reg
cat /sys/firmware/devicetree/base/compatible | tr '\0' '\n'
```

| 元素 | 位置 |
|---|---|
| `__of_attach_node_sysfs()` | `drivers/of/kobj.c:115` |
| 根节点的名字是 `"base"` | `drivers/of/kobj.c:128` |
| 其它节点用 `kbasename(np->full_name)` | `drivers/of/kobj.c:131` |
| `__of_detach_node_sysfs()` | `drivers/of/kobj.c:149` |
| `__of_add_property_sysfs()` | `drivers/of/kobj.c:63` |
| 属性文件名 | `drivers/of/kobj.c:77` |
| `__of_sysfs_remove_bin_file()` | `drivers/of/kobj.c:87` |
| `safe_name()` | `drivers/of/kobj.c:40` |
| `of_node_ktype` | `drivers/of/kobj.c:27` |
| `of_node_release()` | `drivers/of/kobj.c:21` |

### §4.2 `safe_name()`：处理重名

`safe_name()`（`drivers/of/kobj.c:40`）在名字冲突时给后来的加 `#1`、`#2` 后缀。

⚠️ **什么时候会冲突**：
① 两个属性名在 sysfs 里非法字符被替换后撞了；
② **两个子节点的 `full_name` 相同**（DTS 写错了，两个节点同名同 unit-address）。

⇒ 所以 `ls /sys/firmware/devicetree/base/soc/` 里出现 `foo` 和 `foo#1`
**说明你的 DTS 里有两个同名节点**（07 篇会作为一条判据）。

### §4.3 `of_node_release()`：引用计数的真正落点

`drivers/of/kobj.c:21` 是 `of_node_ktype` 的 release 回调 ——
⇒ **`of_node_put()` 的计数归零时走到这里**。

它调 `drivers/of/dynamic.c:336` 那条检查
（`"ERROR: Bad of_node_put() on %pOF"`）—— 02 §1.0.C 的那条日志。

### §4.4 ⚠️ 它和 DTB 是两份数据

`/sys/firmware/devicetree/` 反映的是**内存里的 `device_node` 树**，
不是 bootloader 传来的 DTB（03 §A3 末段）。

⇒ **打了 overlay 之后两者会不一致** —— sysfs 反映最新状态，DTB 不变。
这一点在 07 §4 会作为"两种查看方式的区别"讲。

---

## §5 unittest：3647 行，占全目录 22%

### §5.1 它的价值：抓引用计数泄漏

`CONFIG_OF_UNITTEST`（`drivers/of/Kconfig:15`）在 `late_initcall` 时跑一整套自测。

**对普通开发者的唯一价值**：它是**抓 `of_node_get/put` 不平衡的唯一工具**
（02 §1.0.C · 03 §C3）——
`drivers/of/dynamic.c:471` 那条 `pr_err`（"memory leak, expected refcount 1 instead of %d"）
就是它触发的。

226 行的官方文档：`Documentation/devicetree/of_unittest.rst`。

### §5.2 ⚠️ 它需要一棵测试树

unittest 会**打一个内置的 overlay**（`drivers/of/unittest-data/`）到运行中的树上 ——
⇒ 所以它依赖 `OF_OVERLAY` 和 `OF_DYNAMIC`（`drivers/of/Kconfig:15` 附近的 `select`）。

⚠️ **生产内核绝对不要开它** —— 它会往你的设备树里塞测试节点。

本套**没有读 `unittest.c`**（三等证据，99 §2.3）——
它是测试代码，不承载任何产品行为论断。

---

## §6 kexec：重建 DTB

`drivers/of/kexec.c`（472 行）在 kexec 时**构造一份新的 DTB** 交给下一个内核：
保留原有内容 + 更新 `/chosen` 里的 initrd 位置、命令行、随机种子、IMA 日志等。

**它是"内核自己写 DTB"的唯一场景** ——
其它时候内核只读 DTB。

本套只提到它的存在（三等证据，99 §2.3）。

---

## §7 NUMA 与 Sparc

| § | 框架 | arm64 |
|---|---|---|
| NUMA（`drivers/of/of_numa.c`，186 行） | 解析 `numa-node-id` 建 NUMA 拓扑 | 服务器级 arm64 会用 |
| Sparc PROM 树（`drivers/of/pdt.c`，213 行） | 从 OpenFirmware PROM 直接读树（不用 DTB） | **完全不涉及** |

**`pdt.c` 的存在解释了 `struct device_node` 里那两个 `CONFIG_SPARC` 字段**
（`include/linux/of.h:69-70`，02 §5.1）。

---

## §8 汇总表

| § | 框架 | 职责 | 挂在 03 时间线的哪一点 | arm64 相关 | 最该记住的一条 |
|---|---|---|---|---|---|
| §1 | changeset + overlay | 运行期改树 | 任意时刻 | | **changeset 是事务，overlay 是事务生成器**；撤销只能后进先出 |
| §2 | reserved-memory | 从 DT 划出内存块 | **早期扫描 + 驱动 probe 两个阶段** | | 上界 **64** 个区间；**区间重叠只警告不阻止** |
| §3 | graph | 双向的端口连接 | 驱动 probe | | `remote-endpoint` **必须两端都写** |
| §4 | sysfs 视图 | `/sys/firmware/devicetree/` | `of_core_init()` | | **最重要的调试出口**；出现 `foo#1` 说明 DTS 有重名节点 |
| §5 | unittest | 自测 | `late_initcall` | | **抓 `of_node_get/put` 不平衡的唯一工具**；生产内核别开 |
| §6 | kexec | 重建 DTB | kexec 时 | | 内核唯一"写 DTB"的场景 |
| §7 | NUMA / Sparc | —— | —— | / 无 | `pdt.c` 解释了 `device_node` 里那两个 SPARC 字段 |

**七节里只有 §2 参与"设备能不能正常工作"**，
§4 §5 是可观测性/正确性工具，§1 §6 是特殊场景，§3 是特定领域，§7 是非 arm64。

⇒ **调"DTS 配了但不生效"看 03 篇的时间线 + 本篇 §4**；
调"内存分配失败"看 §2。

---

**下一篇**：05 篇给出四个角色的代码骨架
（消费者驱动 / 复合设备 / 提供者 / DTS 作者）。
