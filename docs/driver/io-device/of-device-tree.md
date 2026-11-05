# OF / Device Tree（设备树）

> **定位**：arm64 上唯一的硬件描述机制——bootloader 传入 DTB（Flattened Device Tree），内核解包为 `device_node` 树，再据此创建 `platform_device`，驱动通过 `compatible` 属性匹配。设备树是 arm64 上"硬件从哪里来"的唯一答案。

---

## 1. 核心数据结构

头文件：`include/linux/of.h`（1641 行）；核心实现：`drivers/of/`

### 1.1 `struct device_node`

```c
struct device_node {                    // of.h:52-72
    const char          *name;          // 节点名（如 "serial"）
    const char          *type;          // device_type 属性值
    phandle             phandle;        // 本节点的 phandle（被引用时用）
    const char          *full_name;     // 完整路径（如 "/soc/serial@10000"）
    struct fwnode_handle fwnode;        // 固件节点抽象（DT/ACPI 统一）

    struct property     *properties;    // 属性链表
    struct property     *deadprops;     // 已删除属性（动态树用）

    struct device_node  *parent;        // 父节点
    struct device_node  *child;         // 第一个子节点
    struct device_node  *sibling;       // 下一个兄弟节点

    struct kobject      kobj;           // sysfs 表示（/sys/firmware/devicetree/）
    unsigned long       _flags;
    void                *data;
};
```

### 1.2 `struct property`

```c
struct property {                       // of.h:32-46
    struct property     *next;          // 下一个属性
    char                *name;          // 属性名（如 "compatible"、"reg"）
    int                 length;         // 值长度（字节）
    void                *value;         // 值指针
    unsigned long       _flags;
    unsigned int        unique_id;
    struct bin_attribute attr;          // sysfs 二进制属性
};
```

### 1.3 `struct of_device_id`

`include/linux/mod_devicetable.h:268-273`：

```c
struct of_device_id {
    char    name[32];
    char    type[32];
    char    compatible[128];            // **最常用**：匹配 compatible 属性
    const void *data;                   // 驱动自定义数据
};
```

### 1.4 全局指针

`include/linux/of.h:135-138`：

| 变量 | 对应节点 | 用途 |
|------|---------|------|
| `of_root` | `/` | 设备树根节点 |
| `of_chosen` | `/chosen` | bootloader 传递的参数（bootargs、stdout-path） |
| `of_aliases` | `/aliases` | 别名（如 `serial0 = &uart0`） |
| `of_stdout` | stdout-path 指向的节点 | 控制台 |

---

## 2. 启动流程：从 DTB 到 platform_device

### 2.1 时间线

```
Bootloader 传 DTB 指针（arm64 在 x0 寄存器）
    │
    ▼
arch/arm64/kernel/setup.c → setup_machine_fdt()
    │  验证 FDT header
    ▼
early_init_dt_scan()                    // fdt.c:1307
    │  扫描 /chosen、/aliases、memory 节点
    ▼
unflatten_device_tree()                 // fdt.c:1327
    │  DTB blob → device_node 树（两趟：计算大小 → 实际分配）
    │  unflatten_dt_nodes() → populate_node() + populate_properties()
    ▼
of_core_init()                          // base.c:166
    │  创建 /sys/firmware/devicetree/ kset
    │  创建 /proc/devicetree 符号链接
    ▼
of_platform_default_populate_init()     // platform.c:517  (arch_initcall_sync)
    │  遍历根节点的子节点
    │  of_platform_bus_create() → of_platform_device_create_pdata()
    │    → of_device_alloc()  分配 platform_device + 映射资源
    │    → of_device_add()    注册到设备模型
    │  递归处理 "simple-bus" / "simple-mfd" / "arm,amba-bus" 子节点
    ▼
bus_probe_device() → platform_match() → of_driver_match_device()
    │  比较 drv->of_match_table 与 dev->of_node->compatible
    ▼
really_probe() → drv->probe()
```

### 2.2 默认总线匹配表

`drivers/of/platform.c:24-32`——这些 compatible 值的子节点会被递归展开为设备：

```c
const struct of_device_id of_default_bus_match_table[] = {
    { .compatible = "simple-bus", },
    { .compatible = "simple-mfd", },
    { .compatible = "isa", },
    { .compatible = "arm,amba-bus", },
    {}
};
```

---

## 3. 属性读取 API

### 3.1 基础读取

| API | 说明 | 备注 |
|-----|------|------|
| `of_get_property(np, name, &len)` | 返回属性值指针 | 直接返回，不加引用 |
| `of_find_property(np, name, &len)` | 返回 `struct property *` | 带引用 |
| `of_property_read_bool(np, name)` | 检查属性是否存在 | inline |
| `of_property_read_u32(np, name, &val)` | 读单个 u32 | |
| `of_property_read_u32_array(np, name, val, sz)` | 读 u32 数组 | |
| `of_property_read_string(np, name, &str)` | 读字符串 | |
| `of_property_count_elems_of_size(np, name, size)` | 计算元素个数 | |

全树使用量：`of_property_read_` 系列约 1703 处。

### 3.2 Phandle 引用解析

| API | 说明 |
|-----|------|
| `of_parse_phandle(np, name, index)` | 解析 phandle 引用，返回目标节点 |
| `of_parse_phandle_with_args(np, name, cells_name, index, out_args)` | 解析带参数的 phandle |
| `of_parse_phandle_with_args_map(np, name, cells_name, index, out_args)` | 带 map 的版本 |
| `of_count_phandle_with_args(np, name, cells_name)` | 计算 phandle 条目数 |
| `of_find_node_by_phandle(ph)` | 按 phandle 值查找节点 |

### 3.3 地址转换

| API | 说明 |
|-----|------|
| `of_translate_address(np, reg)` | DT 地址 → CPU 物理地址（递归应用 ranges 属性） |
| `of_address_to_resource(np, index, r)` | reg 属性 → `struct resource` |
| `of_iomap(np, index)` | 直接 ioremap |
| `of_dma_get_range(np, map, nmap)` | 获取 DMA 范围映射 |

### 3.4 IRQ 解析

| API | 说明 |
|-----|------|
| `irq_of_parse_and_map(np, index)` | 从 DT 解析 IRQ 并映射到 Linux IRQ 号 |
| `of_irq_get(np, index)` | 获取 IRQ |

---

## 4. 匹配机制

### 4.1 compatible 匹配算法

`__of_device_is_compatible()`（`drivers/of/base.c:506-542`）：

- 逐条比较 `compatible` 列表中的字符串与 `of_device_id` 表
- 返回匹配分数（越靠前分数越高）
- `of_match_device()` 遍历整个表，返回最高分匹配

### 4.2 驱动侧用法

```c
static const struct of_device_id my_driver_of_match[] = {
    { .compatible = "vendor,device-a", .data = &variant_a_info },
    { .compatible = "vendor,device-b", },
    {}
};
MODULE_DEVICE_TABLE(of, my_driver_of_match);

static struct platform_driver my_driver = {
    .driver = {
        .name = "my-device",
        .of_match_table = my_driver_of_match,  // ← 这里
    },
    .probe = my_probe,
};
```

`of_match_ptr()` 宏在 `CONFIG_OF=n` 时把表置 NULL，避免编译警告。

---

## 5. 节点遍历

| 宏/函数 | 说明 |
|---------|------|
| `for_each_child_of_node(parent, child)` | 遍历子节点（自动 get/put 引用） |
| `for_each_available_child_of_node(parent, child)` | 跳过 `status = "disabled"` 的节点 |
| `of_node_name_eq(np, name)` | 比较节点名 |
| `of_n_addr_cells(np)` | 获取 `#address-cells` |
| `of_n_size_cells(np)` | 获取 `#size-cells` |
| `of_find_node_by_name(from, name)` | 按名字查找 |
| `of_find_node_by_type(from, type)` | 按 `device_type` 查找 |
| `of_find_compatible_node(from, type, compat)` | 按 compatible 查找 |
| `of_find_matching_node_and_match(from, matches, &match)` | 按匹配表查找 |

---

## 6. 引用计数

`device_node` 通过内嵌的 `kobject` 管理引用：

| API | 说明 |
|-----|------|
| `of_node_get(np)` | 引用 +1 |
| `of_node_put(np)` | 引用 -1，归零时释放 |

全树使用量：`of_node_get` / `of_node_put` 约 1524 处。

**规则**：返回 `device_node *` 的函数（`of_parse_phandle`、`of_find_*` 等）都会 +1 引用。用完必须 `of_node_put()`。`for_each_child_of_node` 自动处理。

---

## 7. 动态设备树

`drivers/of/dynamic.c`（938 行）支持运行时修改设备树：

| API | 说明 |
|-----|------|
| `of_add_property(np, prop)` | 添加属性 |
| `of_remove_property(np, prop)` | 删除属性 |
| `of_attach_node(np)` | 把节点挂入活树 |
| `of_detach_node(np)` | 从活树摘除节点 |

### 7.1 Changeset

批量原子修改：

```c
struct of_changeset ocs;
of_changeset_init(&ocs);
of_changeset_add_property(&ocs, np, prop);
of_changeset_attach_node(&ocs, new_np);
of_changeset_apply(&ocs);    // 原子应用
// ...
of_changeset_revert(&ocs);   // 回滚
```

### 7.2 Overlay（设备树覆盖）

`drivers/of/overlay.c`（1267 行）：

| API | 说明 |
|-----|------|
| `of_overlay_fdt_apply(overlay_fd, fd_size, &ov_id)` | 应用 DT overlay |
| `of_overlay_remove(&ov_id)` | 移除 overlay |
| `of_overlay_remove_all()` | 移除所有 |

内部流程：解包 overlay FDT → `of_resolve_phandles()` 解析 phandle → `build_changeset()` → `of_changeset_apply()`

---

## 8. arm64 特殊性

### 8.1 唯一启动方式

arm64 **只能**从 DT 启动（无 ATAGS、无 x86 式 ACPI 枚举）：
- Bootloader 把 DTB 物理地址放在 `x0`
- `setup_machine_fdt()` 验证并存储
- `unflatten_device_tree()` 建立 `device_node` 树

### 8.2 DTS 文件统计

| 类别 | 数量 |
|------|------|
| `.dts` 文件（板级描述） | 762 |
| `.dtsi` 文件（SoC/包含文件） | 613 |
| **合计** | **1375** |
| 供应商目录 | 34 个 |

位置：`arch/arm64/boot/dts/`

### 8.3 无 Machine Descriptor

arm64 不使用 `DT_MACHINE_START` / `MACHINE_START`（全在 `arch/arm/`）。设备发现完全由 DT compatible 匹配 + 驱动 probe 完成。

---

## 9. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `of_property_read_` | 4,890 | 属性读取 |
| `of_node_get` / `of_node_put` | 3,993 | 引用计数 |
| `of_match_table` 赋值 | 4,629 | 驱动匹配表 |
| `of_device_id` 定义 | 5,542 | 匹配表项 |

---

## 10. 核心文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| `include/linux/of.h` | 1641 | 所有核心结构体和 API 声明 |
| `drivers/of/base.c` | 2214 | 属性查找、匹配、phandle 解析 |
| `drivers/of/fdt.c` | 1399 | FDT 解包、早期扫描 |
| `drivers/of/platform.c` | 796 | DT → platform_device 创建 |
| `drivers/of/address.c` | 1148 | 地址转换、资源映射、DMA 范围 |
| `drivers/of/irq.c` | — | IRQ 解析和映射 |
| `drivers/of/dynamic.c` | 938 | 动态树修改、changeset |
| `drivers/of/overlay.c` | 1267 | Overlay 应用/移除 |
| `drivers/of/resolver.c` | 341 | Overlay phandle 解析 |

---

## §4 常见陷阱

### 陷阱 1：for_each_child_of_node 中 break 导致引用泄漏

**错误写法**：
```c
struct device_node *child;
for_each_child_of_node(parent, child) {
    if (some_condition(child)) {
        do_something(child);
        break;  /* BUG! child 的引用计数没有释放 */
    }
}
```

**正确写法**：
```c
struct device_node *child;
for_each_child_of_node(parent, child) {
    if (some_condition(child)) {
        do_something(child);
        of_node_put(child);  /* 手动释放 */
        break;
    }
}
/* 或者用 goto 跳出，在 goto 前 put */
```

**原因**：`for_each_child_of_node` 宏展开后调用 `of_get_next_child()`，该函数对每个迭代的子节点调用 `of_node_get()` 增加引用计数，并在下一次迭代时对前一个节点调用 `of_node_put()`（`drivers/of/base.c:756-757`）。正常循环结束时，最后一个节点的引用会在循环条件检查时被释放。但如果在循环体内 `break`，当前节点的引用计数没有被递减，导致引用泄漏——节点永远不会被释放。

**源码依据**：`drivers/of/base.c:756-757` — `__of_get_next_child` 中 `of_node_get(next); of_node_put(prev);`。

### 陷阱 2：of_property_read_* 返回值的三种错误码

**错误写法**：
```c
u32 val;
int ret = of_property_read_u32(np, "my-prop", &val);
if (ret) {
    /* 把所有错误都当作"属性不存在"处理 */
    val = DEFAULT_VALUE;
}
```

**正确写法**：
```c
u32 val;
int ret = of_property_read_u32(np, "my-prop", &val);
switch (ret) {
case 0:
    /* 成功 */
    break;
case -EINVAL:
    /* 属性不存在 */
    val = DEFAULT_VALUE;
    break;
case -ENODATA:
    /* 属性存在但没有值 */
    val = DEFAULT_VALUE;
    break;
case -EOVERFLOW:
    /* 属性数据长度不够 */
    pr_err("my-prop has wrong size\n");
    return -EINVAL;
}
```

**原因**：`of_property_read_u32` 系列函数通过 `of_find_property_value_of_size()` 返回三种不同的错误码（`drivers/of/property.c:107-125`）：`-EINVAL`（属性不存在）、`-ENODATA`（属性存在但 value 为 NULL）、`-EOVERFLOW`（属性长度不匹配）。这三种错误的含义完全不同——`-EOVERFLOW` 通常意味着 DT 格式错误，应该报错而不是静默使用默认值。

**源码依据**：`drivers/of/property.c:107-125` — `of_find_property_value_of_size` 返回三种错误码。

### 陷阱 3：属性值是 __be32 大端格式，直接当 u32 用

**错误写法**：
```c
const __be32 *result;
int len;
result = of_get_property(np, "my-reg-value", &len);
if (result) {
    u32 val = *result;  /* BUG! 直接解引用，在 little-endian CPU 上值错误 */
    write_reg(val);
}
```

**正确写法**：
```c
u32 val;
/* 方法 1：用 of_property_read_u32，内部自动做 endian 转换 */
of_property_read_u32(np, "my-reg-value", &val);
write_reg(val);

/* 方法 2：手动转换 */
const __be32 *result;
int len;
result = of_get_property(np, "my-reg-value", &len);
if (result) {
    u32 val = be32_to_cpu(*result);  /* 正确的 endian 转换 */
    write_reg(val);
}
```

**原因**：DT 属性值以大端（big-endian）格式存储。`of_get_property()` 返回的是 `void *`，指向的属性数据是 `__be32` 格式。在 arm64（little-endian）上直接解引用 `*result` 得到的是字节序错误的值。`of_property_read_u32()` 内部调用 `be32_to_cpup()` 做转换（`drivers/of/property.c:156`），而 `of_get_property()` 不做任何转换。

**源码依据**：`drivers/of/property.c:156` — `*out_value = be32_to_cpup(((__be32 *)val) + index);`。

### 陷阱 4：of_node_put(NULL) 安全，但 of_find_* 返回的节点必须 put

**错误写法**：
```c
/* 情况 1：忘记 put */
struct device_node *np = of_find_compatible_node(NULL, NULL, "vendor,device");
if (np) {
    do_something(np);
    /* 忘记 of_node_put(np)！引用泄漏 */
}

/* 情况 2：对 of_get_property 返回值 put */
const void *prop = of_get_property(np, "compatible", NULL);
of_node_put(prop);  /* BUG! prop 不是 device_node，是属性值指针 */
```

**正确写法**：
```c
/* 情况 1：of_find_* 返回的节点必须 put */
struct device_node *np = of_find_compatible_node(NULL, NULL, "vendor,device");
if (np) {
    do_something(np);
    of_node_put(np);  /* 必须释放 */
}

/* 情况 2：of_get_property 不需要 put */
const void *prop = of_get_property(np, "compatible", NULL);
/* prop 指向属性值，不需要 of_node_put */
```

**原因**：`of_node_put()` 对 NULL 参数安全（`drivers/of/dynamic.c:46-50` — 内部检查 `if (node)`）。但关键是区分哪些函数返回带引用的节点：`of_find_*`、`of_parse_phandle`、`of_get_parent`、`of_get_next_child` 等返回的节点引用计数已递增，必须 `of_node_put()`。而 `of_get_property()` 返回的是属性值指针（`void *`），不是 `device_node *`，不需要也不能 `of_node_put()`。

**源码依据**：`drivers/of/dynamic.c:46-50` — `of_node_put` NULL 安全；`drivers/of/base.c:1106-1126` — `of_find_node_with_property` 返回带引用的节点。

### 陷阱 5：of_property_read_u32_array 成功时返回元素个数而非 0

**错误写法**：
```c
u32 values[4];
int ret = of_property_read_u32_array(np, "my-array", values, 4);
if (ret) {
    /* 以为 ret 非零就是错误！ */
    pr_err("failed to read my-array: %d\n", ret);
    return ret;
}
```

**正确写法**：
```c
u32 values[4];
int ret = of_property_read_u32_array(np, "my-array", values, 4);
if (ret < 0) {
    /* 负值才是错误 */
    pr_err("failed to read my-array: %d\n", ret);
    return ret;
}
/* ret >= 0 表示成功读取的元素个数 */
```

**原因**：`of_property_read_variable_u32_array()` 成功时返回实际读取的元素个数（正数），而不是 0（`drivers/of/property.c:337`）。`of_property_read_u32_array` 是它的包装，当 `sz_min == sz_max` 时返回 0，但 variable 版本始终返回元素个数。如果驱动用 `if (ret)` 检查错误，会把成功的正数返回值误判为错误。正确做法是检查 `if (ret < 0)`。

**源码依据**：`drivers/of/property.c:337` — `return sz;`（成功时返回元素个数）。

### 陷阱 6：of_parse_phandle 返回的节点引用未释放

**错误写法**：
```c
struct device_node *phandle_np;
phandle_np = of_parse_phandle(np, "my-phandle-ref", 0);
if (phandle_np) {
    use_node(phandle_np);
    /* 忘记 of_node_put！ */
}
```

**正确写法**：
```c
struct device_node *phandle_np;
phandle_np = of_parse_phandle(np, "my-phandle-ref", 0);
if (phandle_np) {
    use_node(phandle_np);
    of_node_put(phandle_np);  /* 必须释放 */
}
```

**原因**：`of_parse_phandle()` 内部调用 `of_find_node_by_phandle()`，后者对找到的节点调用 `of_node_get()` 增加引用计数。返回的节点指针带有引用，用完必须 `of_node_put()`。如果不释放，每次调用都泄漏一个节点引用，长期运行后节点永远不会被释放（在动态 DT 环境中尤其严重）。

**源码依据**：`drivers/of/base.c:1106-1126` — `of_find_node_with_property` 中 `of_node_get(np)` 后返回；phandle 解析路径同理。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/of.h` — struct device_node / struct property 定义、of_node_get/put 声明、of_property_read_* 声明、for_each_child_of_node 宏
- `drivers/of/base.c` — of_find_property、of_get_parent、of_get_next_child、of_match_node、of_find_matching_node_and_match 实现
- `drivers/of/property.c` — of_find_property_value_of_size、of_property_read_u32_index、of_property_read_variable_u32_array 实现
- `drivers/of/dynamic.c` — of_node_get / of_node_put 实现

**关键论断出处**：
- "of_node_put 对 NULL 安全" — 来源：`drivers/of/dynamic.c:46-50`
- "of_find_property_value_of_size 返回三种错误码" — 来源：`drivers/of/property.c:107-125`
- "属性值以大端存储，需要 be32_to_cpup 转换" — 来源：`drivers/of/property.c:156`
- "for_each_child_of_node 内部做 get/put" — 来源：`drivers/of/base.c:756-757`
- "variable array 读取成功返回元素个数" — 来源：`drivers/of/property.c:337`
- "of_find_* 返回带引用的节点" — 来源：`drivers/of/base.c:1117`（`of_node_get(np)`）

**未验证项**：
- `of_property_read_string()` 的具体返回值语义（是否也返回正数）未在当前阅读中确认
- `of_overlay_remove()` 对引用计数的影响未追踪
- `CONFIG_OF_DYNAMIC=n` 时 `of_node_get/put` 退化为空操作的行为对驱动代码的影响未评估
