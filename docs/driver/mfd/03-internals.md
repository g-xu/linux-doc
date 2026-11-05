---
title: 内部实现
---

# mfd 03：内部实现

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ **本篇覆盖的核心只有 1092 行**：`drivers/mfd/mfd-core.c`(471) ·
`include/linux/mfd/core.h`(163) · `drivers/mfd/syscon.c`(378) ·
`include/linux/mfd/syscon.h`(80)。

⚠️⚠️ **`drivers/mfd/` 全目录 237 个 `.c` / 112426 行，核心占 0.56%。**
其余 235 个 `.c` 是单芯片驱动，本篇不覆盖（04 篇挑代表讲）。

---

## §1 471 行里有什么

`drivers/mfd/mfd-core.c` 的全部函数，按源码顺序：

| 函数 | 行区间 | 行数 | 性质 |
|---|---|---|---|
| `mfd_cell_enable()` | `:36-46` | 11 | 导出；⚠️ 近乎死 API |
| `mfd_cell_disable()` | `:49-59` | 11 | 导出；同上 |
| `match_device_ids()` | `:68-78` | 11 | ⚠️ ACPI 专用（`#if` 在 `:62`） |
| `mfd_acpi_add_device()` | `:80-117` | 38 | ⚠️ ACPI 专用；空桩在 `:119-123` |
| `mfd_match_of_node_to_dev()` | `:125-168` | 44 | ⚠️ **DT 子节点分配的全部逻辑** |
| **`mfd_add_device()`** | `:170-334` | **165** | ⚠️⚠️ **整个子系统的心脏** |
| `mfd_add_devices()` | `:349-371` | 23 | 导出；就是一个 for 循环 |
| `mfd_remove_devices_fn()` | `:374-404` | 31 | 拆一个子设备 |
| `mfd_remove_devices_late()` | `:406-411` | 6 | 导出 |
| `mfd_remove_devices()` | `:414-419` | 6 | 导出 |
| `devm_mfd_dev_release()` | `:422-425` | 4 | devres 回调 |
| `devm_mfd_add_devices()` | `:444-467` | 24 | 导出 |

⇒ **12 个函数，一个占 35%**。理解 mfd 等于理解 `mfd_add_device()` 那 165 行。

⚠️ **注意这里面没有什么**：
没有 `probe`、没有 `bus_type`、没有 `initcall`、没有 `module_init`。
`mfd-core.c` 只有 `MODULE_LICENSE`/`MODULE_AUTHOR`（`:470-471`）
⇒ **它是一个纯函数库，不是一个驱动，也没有任何启动时行为**（08 §3）。

---

## §2 `mfd_add_device()` 逐段读

**签名**（`drivers/mfd/mfd-core.c:170-173`）：`static`，注意它处理的是**一个** cell。
`mfd_add_devices()` 只是循环调它。

### §2.1 编号与三次分配（`:180-199`）

```c
/* drivers/mfd/mfd-core.c:180-199 —— 节选 */
int ret = -ENOMEM;
...
if (id == PLATFORM_DEVID_AUTO)
        platform_id = id;
else
        platform_id = id + cell->id;

pdev = platform_device_alloc(cell->name, platform_id);
if (!pdev)
        goto fail_alloc;
pdev->mfd_cell = kmemdup(cell, sizeof(*cell), GFP_KERNEL);
if (!pdev->mfd_cell)
        goto fail_device;
res = kcalloc(cell->num_resources, sizeof(*res), GFP_KERNEL);
if (!res)
        goto fail_device;
```

⚠️ **三点值得注意**：

1. **`ret` 的初值就是 `-ENOMEM`**（`:180`）⇒ 上面三个 `goto` 都不赋值，
   直接沿用初值（01 §2.1.1）。**读代码时不要在这附近找错误码。**
2. ⚠️ **`platform_id` 的两条路互斥**：`PLATFORM_DEVID_AUTO`(=-2) 时
   `cell->id` 被完全丢弃 ⇒ **同一份 cell 数组在两种 id 模式下产出的设备名不同**。
3. ⚠️⚠️ **`kcalloc(cell->num_resources, ...)`**：`num_resources = 0` 时
   `kcalloc(0, ...)` 返回的是一个**非 NULL 的最小对象**（slab 的行为）
   ⇒ **不会走 `fail_device`**，后面的 `for` 循环零次执行，一切正常。
   ⇒ **"没有资源的 cell"是完全合法的**，很多 codec 子设备就是这样。

⚠️ `pdev->mfd_cell` 是 `kmemdup` 的**浅拷贝** ⇒ cell 里所有指针
（`name` / `resources` / `platform_data` / `of_compatible` / `parent_supplies`）
**仍然指向父驱动的内存**。⇒ ⚠️⚠️ **父驱动的 cell 数组必须是 `static` 或至少活到子设备被删**
—— 用栈上的局部数组会在 remove 时读到野指针（05 §2）。

### §2.2 从父设备继承四样东西（`:201-205`）

```c
/* drivers/mfd/mfd-core.c:201-205 */
pdev->dev.parent = parent;
pdev->dev.type = &mfd_dev_type;
pdev->dev.dma_mask = parent->dma_mask;
pdev->dev.dma_parms = parent->dma_parms;
pdev->dev.coherent_dma_mask = parent->coherent_dma_mask;
```

| 赋的什么 | 为什么 |
|---|---|
| `parent` | 建立设备树层级；子设备在 sysfs 里挂在父设备下 |
| ⚠️ `type = &mfd_dev_type` | **拆解时认出自己的孩子的唯一手段**（02 §4.1） |
| `dma_mask` / `dma_parms` / `coherent_dma_mask` | ⚠️ **DMA 能力是芯片的属性，不是功能块的属性** |

⚠️⚠️ **`dma_mask` 是指针拷贝，不是值拷贝**（`parent->dma_mask` 类型是 `u64 *`）
⇒ **父子共享同一个掩码变量**。父驱动后来改了自己的 `dma_mask` 指向的值，
所有子设备立刻跟着变。⚠️ 这既是特性也是坑：
**父驱动必须在 `mfd_add_devices()` 之前把 DMA 掩码设好**，
而且**不能在之后把那块内存释放掉**。

⚠️ 另一面：**中断亲和性、电源域、时钟都不继承** ——
子设备想要时钟得自己从 DT 拿（所以必须先匹配到 of_node，§2.4）。

### §2.3 regulator 供电别名（`:207-212`）

```c
/* drivers/mfd/mfd-core.c:207-212 */
ret = regulator_bulk_register_supply_alias(
                &pdev->dev, cell->parent_supplies,
                parent, cell->parent_supplies,
                cell->num_parent_supplies);
if (ret < 0)
        goto fail_res;
```

⚠️ 注意**两个 `cell->parent_supplies` 传了两次**：
函数签名是 `(dev, id[], alias_dev, alias_id[], num)`
⇒ **别名前后同名，只换设备** —— 语义就是"这个名字去父设备上查"（02 §2.8）。

⚠️ **它是第一个可能返回非 `-ENOMEM` 错误的点**。`num_parent_supplies = 0` 时
它是空操作返回 0 ⇒ **对绝大多数 cell 这一段等于不存在**。

### §2.4 DT 子节点匹配 —— 三条出口（`:214-244`）

⚠️⚠️ **这是整个函数最容易读错的一段**，因为 `match:` 标签在 `if` 块**内部**。

```c
/* drivers/mfd/mfd-core.c:214-244 */
if (IS_ENABLED(CONFIG_OF) && parent->of_node && cell->of_compatible) {
        for_each_child_of_node(parent->of_node, np) {
                if (of_device_is_compatible(np, cell->of_compatible)) {
                        /* Skip 'disabled' devices */
                        if (!of_device_is_available(np)) {
                                disabled = true;
                                continue;
                        }
                        ret = mfd_match_of_node_to_dev(pdev, np, cell);
                        if (ret == -EAGAIN)
                                continue;
                        of_node_put(np);
                        if (ret)
                                goto fail_alias;
                        goto match;
                }
        }
        if (disabled) {
                /* Ignore 'disabled' devices error free */
                ret = 0;
                goto fail_alias;
        }
match:
        if (!pdev->dev.of_node)
                pr_warn("%s: Failed to locate of_node [id: %d]\n",
                        cell->name, platform_id);
}
```

**进入这一段需要三个条件同时成立**：`CONFIG_OF=y`、父设备有 DT 节点、cell 声明了
`of_compatible`。缺任何一个 ⇒ **整段跳过，子设备没有 `of_node`，也没有任何警告**。

**三条出口**：

| 出口 | 条件 | 结果 |
|---|---|---|
| **A. 匹配成功** | 找到可用且未被占用的子节点 | `goto match` ⇒ `pdev->dev.of_node` 已设 ⇒ 不打警告，继续往下 |
| **B. ⚠️⚠️ 只找到 disabled 的** | `status = "disabled"` | `ret = 0` 然后 `goto fail_alias` ⇒ **走失败路径，返回成功，子设备不出现** |
| **C. ⚠️ 一个都没找到** | DT 里没有这个 compatible 的子节点 | **落到 `match:`** ⇒ `pdev->dev.of_node` 为空 ⇒ 只 `pr_warn`，**子设备照样注册** |

⚠️⚠️ **出口 B 是本子系统最反直觉的一处**：

```
                  disabled 子节点
                        ↓
              ret = 0;  goto fail_alias
                        ↓
        fail_alias → fail_res → fail_device → fail_alloc
                        ↓
                  return ret;   /* 0 */
```

⇒ **函数走完了整条清理阶梯（含 `platform_device_put`），却返回 0。**
`mfd_add_devices()` 于是继续处理下一个 cell，**不认为出了错**。
⚠️ **症状**：DT 里把一个子节点标成 `status = "disabled"`，
`/sys/bus/platform/devices/` 里那个子设备就**静默消失，dmesg 一个字都没有**。
这是设计意图（"Ignore 'disabled' devices error free"，`:235` 的注释），
**但它意味着"少了一个子设备"这件事在内核侧不可观测**（07 §3）。

⚠️ **出口 C 的症状更隐蔽**：子设备注册成功了，但没有 `of_node`
⇒ 子驱动 probe 时 `device_get_match_data()` / `of_property_read_*()` 全部失败
⇒ **报错出现在子驱动里，而唯一的线索 `pr_warn` 打的是父设备的 cell 名字**。
⚠️ 这条 `pr_warn` 用的是 `pr_warn` 而不是 `dev_warn`
⇒ **日志里没有父设备名**，只有 cell 名和 id（`:242-243`）。

⚠️ 还有一个组合陷阱：**B 和 C 可以同时"成立"**。
DT 里有两个同 compatible 子节点，一个 disabled、一个可用但地址不匹配
（`use_of_reg` 场景，`mfd_match_of_node_to_dev()` 返回 `-EAGAIN`）
⇒ 循环结束时 `disabled` 是 `true` ⇒ **走 B，返回 0，静默消失**
—— 而真正的原因是地址写错了。⚠️ **`disabled` 这个标志一旦置上就再也不清**（`:219`）。

### §2.5 `mfd_match_of_node_to_dev()` —— 占用登记（`:125-168`）

⚠️ 整个函数体包在 `#if IS_ENABLED(CONFIG_OF)`（`:129-166`）里
⇒ `CONFIG_OF=n` 时它直接 `return 0`（`:167`）——但调用点本来也进不去（§2.4）。

**四条返回路径**：

| 返回 | 行 | 含义 |
|---|---|---|
| `-EAGAIN` | `:137` | ⚠️ **这个 np 已经被别的子设备占了** |
| `-EAGAIN` | `:147` | ⚠️ 要按地址匹配，但节点没有 `reg` 属性 |
| `-EAGAIN` | `:153` | ⚠️ 有 `reg`，但地址和 `cell->of_reg` 不等 |
| `-ENOMEM` | `:158` | 表项 `kzalloc` 失败 |
| 0 | `:167` | 匹配成功，已登记 |

**成功时做三件事**：

```c
/* drivers/mfd/mfd-core.c:160-165 */
of_entry->dev = &pdev->dev;
of_entry->np = np;
list_add_tail(&of_entry->list, &mfd_of_node_list);

pdev->dev.of_node = np;
pdev->dev.fwnode = &np->fwnode;
```

⚠️⚠️ **`fwnode` 是手工赋的**（`:165`），不是 `device_set_node()` 之类的辅助函数。
⇒ 这是 6.1 的写法；意味着**任何依赖 fwnode 的通用设施（如 fwnode graph）
在 mfd 子设备上是可用的**，因为这一行确实设了。

⚠️ **`of_node` 的引用计数在这里没有增加**：
`pdev->dev.of_node = np` 直接存指针，而 `np` 的引用是
`for_each_child_of_node()` 借出的，调用点在 `:226` 就 `of_node_put()` 了
⇒ **子设备持有一个不带引用的 of_node 指针**。
⚠️ 这在实践中安全（父设备的 DT 节点树在整个生命周期内不会消失），
但**不是标准做法** —— 通常应该 `of_node_get()`。这是本子系统的一处技术债（99 §6）。

#### §2.5.1 地址匹配的口径

```c
/* drivers/mfd/mfd-core.c:143-153 */
/* We only care about each node's first defined address */
reg = of_get_address(np, 0, NULL, NULL);
if (!reg)
        return -EAGAIN;
of_node_addr = of_read_number(reg, of_n_addr_cells(np));
if (cell->of_reg != of_node_addr)
        return -EAGAIN;
```

⚠️ 三个约束：
1. **只看 `reg` 里的第一个地址**（注释 `:143` 自己说明了）
   ⇒ 一个节点有多段 `reg` 时，只有第一段参与匹配；
2. `of_n_addr_cells(np)` 取的是**该节点的 `#address-cells`**
   ⇒ 64 位地址要在 DT 里正确声明 `#address-cells = <2>`，否则读出来是高 32 位；
3. ⚠️ **不做地址转换**（没有 `of_translate_address`）
   ⇒ `cell->of_reg` 里写的必须是**DT 里字面写的那个值**，不是转换后的 CPU 物理地址。

⚠️ 全树只有 `drivers/mfd/ocelot-core.c` 用这条路径（02 §2.4）
⇒ ⚠️ **它是本子系统测试覆盖最薄的一段代码**。

### §2.6 平台数据与软件节点（`:246-259`）

```c
/* drivers/mfd/mfd-core.c:246-259 */
mfd_acpi_add_device(cell, pdev);

if (cell->pdata_size) {
        ret = platform_device_add_data(pdev,
                        cell->platform_data, cell->pdata_size);
        if (ret)
                goto fail_of_entry;
}

if (cell->swnode) {
        ret = device_add_software_node(&pdev->dev, cell->swnode);
        if (ret)
                goto fail_of_entry;
}
```

⚠️ **`mfd_acpi_add_device()`（`:246`）的返回值被丢掉了** —— 它是 `void` 版调用形态，
DT 平台上是空桩（`:119-123`）。arm64 上这一行等于不存在（99 §2.7）。

⚠️⚠️ **`if (cell->pdata_size)` 只看长度不看指针**（`:248`）
⇒ 02 §2.3 那个静默失败坑的源头就是这一行。反过来也成立：
**填了 `pdata_size` 但 `platform_data` 是 `NULL`** ⇒ `platform_device_add_data()`
会去 `memdup` 一个空指针。

⚠️ 注意**这里的顺序**：platform data 和 swnode 都在 DT 匹配（§2.4）**之后**才处理
⇒ 三种"给子设备传信息"的手段在时间上是分层的：
先 of_node（有的话），再 platform data，再 software node。
**子驱动 probe 时三者都已就位**，不存在"probe 到一半属性还没挂上"的问题
—— 因为 `platform_device_add()` 在 `:304`，是最后一步。

### §2.7 资源换算 —— 三分支（`:261-298`）

⚠️⚠️ **这是 mfd 最实质的工作**：把 cell 里"相对父设备"的资源，变成绝对资源。

```c
/* drivers/mfd/mfd-core.c:261-289 */
for (r = 0; r < cell->num_resources; r++) {
        res[r].name = cell->resources[r].name;
        res[r].flags = cell->resources[r].flags;

        /* Find out base to use */
        if ((cell->resources[r].flags & IORESOURCE_MEM) && mem_base) {
                res[r].parent = mem_base;
                res[r].start = mem_base->start + cell->resources[r].start;
                res[r].end   = mem_base->start + cell->resources[r].end;
        } else if (cell->resources[r].flags & IORESOURCE_IRQ) {
                ... /* §2.7.2 */
        } else {
                res[r].parent = cell->resources[r].parent;
                res[r].start  = cell->resources[r].start;
                res[r].end    = cell->resources[r].end;
        }
        ...
}
```

| 分支 | 触发条件 | 语义 |
|---|---|---|
| **MEM 偏移** `:266-271` | `IORESOURCE_MEM` **且** `mem_base != NULL` | ⚠️ cell 里的值是**相对 `mem_base->start` 的偏移** |
| **IRQ** `:272-284` | `IORESOURCE_IRQ` | ⚠️ 见 §2.7.2 |
| **原样透传** `:285-289` | 其它一切 | ⚠️ cell 里的值被当**绝对值**用，连 `parent` 都照抄 |

⚠️⚠️ **第一个分支的条件是"与"，这一点非常关键**：
`IORESOURCE_MEM` 但 `mem_base == NULL` ⇒ **落到第三个分支，地址被当绝对值**。
⇒ **同一份 cell 数组，`mem_base` 传或不传，`resources` 的含义完全变了。**
⚠️ 这是本子系统最容易产生"读到错误寄存器"的一处：
父驱动改了一行（`mem_base` 从 `NULL` 变成 `&res`），
cell 里所有 MEM 地址的含义就从绝对变成相对，**编译不报错，运行时读到别处**（07 §4）。

⚠️ 第三个分支抄 `parent` 而第一个分支设 `parent = mem_base`
⇒ ⚠️ **只有走偏移路径的 MEM 资源才会在 `/proc/iomem` 里显示成父窗口的子区间**。

#### §2.7.1 只加 `start`，不裁 `end`

```
mem_base:  [0x1000_0000 .. 0x1000_ffff]      父设备的窗口
cell res:  [     0x0200 ..      0x02ff]      相对偏移
结果:      [0x1000_0200 .. 0x1000_02ff]      ✔
```

⚠️⚠️ **核心不检查换算结果是否落在 `mem_base` 内**：

```
cell res:  [     0x0200 ..     0x1_ffff]     写超了
结果:      [0x1000_0200 .. 0x1001_ffff]      ⚠️ 越过父窗口末端，无人报错
```

⇒ 越界的后果推迟到子驱动 `devm_ioremap_resource()` 时，
由 `resource` 层的 `request_mem_region()` 去发现冲突
—— **而如果它越到的那段没人占，就连冲突都不会有，直接映射到别的寄存器**。
⚠️ `mem_base->end` 在整个函数里**一次都没被读过**（只用了 `->start`）
⇒ **父窗口的长度对 mfd 完全无意义**。

#### §2.7.2 IRQ 的两条路（`:272-284`）

01 §2.1.2 已给出代码。这里只补三条内部结论：

1. **`domain != NULL` 时 `irq_base` 被完全忽略**，反之亦然
   ⇒ **两个参数互斥，不存在"都传"的语义**；
2. ⚠️ `irq_create_mapping()` 的返回值**没有检查**（`:277-278`）
   ⇒ 映射失败返回 0，`res[r].start` 就是 0
   ⇒ **子驱动 `platform_get_irq()` 拿到 0**（在 arm64 上 0 不是合法 IRQ）
   ⇒ 症状是子驱动报 `-EINVAL`/`-ENXIO`，**而错误的根在父设备的 irqdomain**；
3. ⚠️ `WARN_ON` 之后**代码继续跑**（`:275-278`）⇒ 中断范围被静默压成一个。

#### §2.7.3 ACPI 资源冲突检查（`:291-297`）

```c
if (!cell->ignore_resource_conflicts) {
        if (has_acpi_companion(&pdev->dev)) {
                ret = acpi_check_resource_conflict(&res[r]);
                if (ret)
                        goto fail_res_conflict;
        }
}
```

⚠️⚠️ **外层判 `ignore_resource_conflicts`，内层判 `has_acpi_companion()`**
⇒ **DT 启动的 arm64 内核上，`has_acpi_companion()` 恒假，整块是死代码**
⇒ `ignore_resource_conflicts` 这个字段在 arm64 上无论填什么都一样（02 §2.6、99 §2.7）。

⚠️ 顺带：**这是资源循环里唯一可能失败的地方**
⇒ arm64 上 `for` 循环（`:261-298`）**不可能失败**，`fail_res_conflict` 只会从
`:301`/`:305` 进来。

### §2.8 落地三步（`:300-313`）

```c
/* drivers/mfd/mfd-core.c:300-313 */
ret = platform_device_add_resources(pdev, res, cell->num_resources);
if (ret)
        goto fail_res_conflict;

ret = platform_device_add(pdev);
if (ret)
        goto fail_res_conflict;

if (cell->pm_runtime_no_callbacks)
        pm_runtime_no_callbacks(&pdev->dev);

kfree(res);

return 0;
```

⚠️⚠️ **`platform_device_add()`（`:304`）是不可逆点**：这一行返回后，
**设备已经在总线上，子驱动的 `probe` 可能已经跑完了**（platform bus 的
`device_add` 是同步匹配的）。

⇒ 由此得到两个必须记住的结论：

1. ⚠️ **`pm_runtime_no_callbacks()` 是在设备已注册之后才调的**（`:308-309`）
   ⇒ 存在一个极小的窗口：子驱动 probe 里做 runtime PM 操作时，
   这个标志还没设上。**实践中安全（`wm8994` 的子驱动不在 probe 里碰 runtime PM），
   但顺序确实是反的**（99 §6）；
2. ⚠️⚠️ **`platform_device_add()` 失败后走 `fail_res_conflict`，最终
   `platform_device_put(pdev)`（`:331`）** —— 而 `platform_device_add()` 失败时
   自己已经回滚过。这条路径是标准写法（`alloc` 配 `put`），没有问题，
   **但要注意它不是 `platform_device_unregister()`** —— 那是成功注册后才用的。

⚠️ `kfree(res)`（`:311`）在成功路径上释放临时资源数组
—— 因为 `platform_device_add_resources()` **自己复制了一份**。
⇒ **cell 里的 `resources[]` 数组和这个 `res[]` 临时数组都不需要活过 `mfd_add_devices()`**；
真正活下来的是 platform_device 内部的第三份拷贝。
⚠️ 但 **`res[r].name` 是指针浅拷贝**（`:262`）
⇒ ⚠️ **资源名字符串必须活到子设备被删**（通常是 `static const` 字面量，天然满足）。

### §2.9 六层 goto 阶梯（`:315-333`）

```c
/* drivers/mfd/mfd-core.c:315-333 */
fail_res_conflict:
        if (cell->swnode)
                device_remove_software_node(&pdev->dev);
fail_of_entry:
        list_for_each_entry_safe(of_entry, tmp, &mfd_of_node_list, list)
                if (of_entry->dev == &pdev->dev) {
                        list_del(&of_entry->list);
                        kfree(of_entry);
                }
fail_alias:
        regulator_bulk_unregister_supply_alias(&pdev->dev,
                                               cell->parent_supplies,
                                               cell->num_parent_supplies);
fail_res:
        kfree(res);
fail_device:
        platform_device_put(pdev);
fail_alloc:
        return ret;
}
```

**标签与进入点的完整对应**：

| 标签 | 行 | 从哪来 | 撤销什么 |
|---|---|---|---|
| `fail_res_conflict` | `:315` | `:295`（ACPI，arm64 死）· `:302` · `:306` | swnode |
| `fail_of_entry` | `:318` | `:252` · `:258` | of_node 占用登记 |
| `fail_alias` | `:324` | `:228`（`mfd_match_of_node_to_dev` 返回真错误）· ⚠️ `:237`（**disabled，`ret=0`**） | regulator 别名 |
| `fail_res` | `:328` | `:212`（别名注册失败） | `res[]` 临时数组 |
| `fail_device` | `:330` | `:195`（`kmemdup`）· `:199`（`kcalloc`） | `platform_device_put` |
| `fail_alloc` | `:332` | `:191`（`platform_device_alloc`） | 什么都不撤，直接返回 |

⚠️ **这是一个标准的"倒序落地"阶梯**：每个标签只撤销自己那一层，然后**故意不 return**，
落到下一个标签继续撤。⇒ **看这段代码要从下往上读**，越靠下的越先被撤销。

⚠️⚠️ **三处细节值得单独指出**：

1. ⚠️ **注册和注销两侧都是无条件调用的**：`:207-210` 的注册没有任何 `if` 包着，
   `:325-327` 的注销也一样 —— `num_parent_supplies == 0` 时两边都是空操作。
   ⇒ **`parent_supplies` 这条路对绝大多数 cell 是"两次零成本函数调用"**，
   而不是"被 `if` 跳过"；
2. ⚠️ **`fail_of_entry` 那个循环没有 `break`**（`:319-323`）
   ⇒ 会扫完整条全局链表。⚠️ 链表长度 = 全内核所有已匹配的 mfd 子设备数
   ⇒ **一次失败回滚的代价是 O(全局子设备数)**。设备数少，无实际影响；
3. ⚠️⚠️ **`fail_res_conflict` 不撤销 platform data**
   —— `platform_device_add_data()`（`:249`）分配的那份拷贝
   由 `platform_device_put()`（`:331`）的 release 回调统一释放
   ⇒ **不漏，但这个依赖是隐式的**，读代码时容易误判成泄漏。

---

## §3 `mfd_add_devices()` —— 循环加不完整回滚

```c
/* drivers/mfd/mfd-core.c:349-371 */
int mfd_add_devices(struct device *parent, int id, const struct mfd_cell *cells,
                    int n_devs, struct resource *mem_base,
                    int irq_base, struct irq_domain *domain)
{
        int i;
        int ret;

        for (i = 0; i < n_devs; i++) {
                ret = mfd_add_device(parent, id, cells + i, mem_base,
                                     irq_base, domain);
                if (ret)
                        goto fail;
        }

        return 0;

fail:
        if (i)
                mfd_remove_devices(parent);

        return ret;
}
```

**全部逻辑就是这 23 行。** 三个要点：

1. ⚠️ **`n_devs <= 0` 时循环零次，直接返回 0** —— 不报错。
   ⇒ `ARRAY_SIZE()` 写错成 0 的症状是"什么都没发生，返回成功"；
2. ⚠️⚠️ **`if (i)` 这个判断是多余但无害的**：`i == 0` 意味着第一个 cell 就失败，
   此时没有任何子设备需要删。**但它也掩盖了一件事** ——
   `mfd_remove_devices(parent)` 删的是**父设备下所有 mfd 子设备**，
   不只是本次调用建的。⇒ ⚠️ **同一个父设备上调两次 `mfd_add_devices()`，
   第二次失败会把第一次成功建立的子设备也一起删掉**（07 §6）；
3. ⚠️⚠️ **回滚用的是 `mfd_remove_devices()`（`NORMAL`）而不是 `_late()`（`HIGH`）**
   ⇒ `level = MFD_DEP_LEVEL_HIGH` 的 cell 不会被回滚（01 §2.1）。

⚠️ **注意 `mem_base`/`irq_base`/`domain` 三个参数对所有 cell 是同一份**
⇒ **一次调用只能对应一个寄存器窗口和一个中断域**。
父芯片有两个不相干的窗口时，必须**分两次调用**
—— 而这就直接踩上要点 2 那个坑（05 §3 给出正确写法）。

---

## §4 拆解路径

### §4.1 `mfd_remove_devices_fn()` —— 逐个子设备（`:374-404`）

```c
/* drivers/mfd/mfd-core.c:374-404 —— 节选 */
static int mfd_remove_devices_fn(struct device *dev, void *data)
{
        struct platform_device *pdev;
        const struct mfd_cell *cell;
        struct mfd_of_node_entry *of_entry, *tmp;
        int *level = data;

        if (dev->type != &mfd_dev_type)
                return 0;

        pdev = to_platform_device(dev);
        cell = mfd_get_cell(pdev);

        if (level && cell->level > *level)
                return 0;

        if (cell->swnode)
                device_remove_software_node(&pdev->dev);

        list_for_each_entry_safe(of_entry, tmp, &mfd_of_node_list, list)
                if (of_entry->dev == &pdev->dev) {
                        list_del(&of_entry->list);
                        kfree(of_entry);
                }

        regulator_bulk_unregister_supply_alias(dev, cell->parent_supplies,
                                               cell->num_parent_supplies);

        platform_device_unregister(pdev);
        return 0;
}
```

**两道过滤，五个动作。**

| 步骤 | 行 | 说明 |
|---|---|---|
| 过滤 1：类型 | `:381` | ⚠️ `dev->type != &mfd_dev_type` ⇒ **不是我建的，跳过**（02 §4.1） |
| 过滤 2：level | `:387-388` | `cell->level > *level` ⇒ 这一轮不删它 |
| 撤 swnode | `:390-391` | 与 `:255-256` 对称 |
| 摘 of_entry | `:393-396` | 与 `:156-162` 对称 |
| 注销别名 | `:399-400` | 与 `:207-210` 对称 |
| 注销设备 | `:402` | ⚠️ `platform_device_unregister()`（**不是 `_put`**） |

⚠️⚠️ **`if (level && ...)` 里的 NULL 检查是死代码**：
两个调用者（`:410` / `:418`）都传 `&level`，**`data` 永远非空**
⇒ 这个 `level &&` 是防御性写法，实际从不为假（99 §6）。

⚠️ **拆解顺序是注册顺序的严格逆序**（`device_for_each_child_reverse`，`:410`/`:418`）
—— 01 §2.3 已解释为什么这是必须的。

⚠️⚠️ **拆解里没有对应 `platform_data` 的撤销动作**
—— 那份拷贝由 `platform_device_unregister()` → `platform_device_release()` 释放。
同理 `pdev->mfd_cell` 那份 `kmemdup`（`:193`）也由 platform 层的
release 回调 `kfree` 掉。⇒ ⚠️ **`cell` 指针在 `:402` 之后就是野指针**
—— 所以 `:399-400` 用 `cell->parent_supplies` 必须在 `:402` 之前，
**这个顺序不是风格问题，是正确性要求**。

### §4.2 两个入口只差一个常量（`:406-419`）

```c
/* drivers/mfd/mfd-core.c:406-419 */
void mfd_remove_devices_late(struct device *parent)
{
        int level = MFD_DEP_LEVEL_HIGH;

        device_for_each_child_reverse(parent, &level, mfd_remove_devices_fn);
}
EXPORT_SYMBOL(mfd_remove_devices_late);

void mfd_remove_devices(struct device *parent)
{
        int level = MFD_DEP_LEVEL_NORMAL;

        device_for_each_child_reverse(parent, &level, mfd_remove_devices_fn);
}
EXPORT_SYMBOL(mfd_remove_devices);
```

⚠️ `int level` 是**栈上的局部变量**，地址传给回调 ⇒ **不可重入的隐患不存在**
（每个调用者有自己的一份）。

⚠️⚠️ **两次调用是幂等的**：第二次 `mfd_remove_devices()` 时子设备已经不在了，
`device_for_each_child_reverse` 遍历不到 ⇒ **父驱动的 remove 里重复调不会出问题**。
⇒ **但 `_late()` 漏调就是真漏**：`level = HIGH` 的子设备永远留在系统里，
父设备的 `struct device` 引用计数不归零 ⇒ **父设备也删不掉**（07 §6）。

### §4.3 devm 路径（`:422-467`）

```c
/* drivers/mfd/mfd-core.c:422-425 · :452-466 —— 节选 */
static void devm_mfd_dev_release(struct device *dev, void *res)
{
        mfd_remove_devices(dev);
}
...
        ptr = devres_alloc(devm_mfd_dev_release, sizeof(*ptr), GFP_KERNEL);
        if (!ptr)
                return -ENOMEM;

        ret = mfd_add_devices(dev, id, cells, n_devs, mem_base, irq_base,
                              domain);
        if (!ret)
                devres_add(dev, ptr);
        else
                devres_free(ptr);

        return ret;
```

⚠️ **`devres_alloc` 的载荷 `sizeof(*ptr)` 里什么都不存** —— 它只是一个"钩子"，
释放时靠 `dev` 参数（devres 框架传的就是宿主设备）找回父设备。

⚠️⚠️ **`devres_alloc` 在 `mfd_add_devices` 之前，`devres_add` 在之后**
⇒ 顺序是对的：失败时 `devres_free`（`:461`）而不是留下一个会误触发的钩子。

⚠️ **但释放回调只调 `mfd_remove_devices()`** ⇒ 01 §2.2 那个真实缺陷：
**devm 版本删不掉 `level = HIGH` 的 cell**，而 kerneldoc（`:431-432`）
声称"All child-devices of the MFD will automatically be removed"。

---

## §5 并发：那条无锁链表

⚠️⚠️ **`mfd-core.c` 全文没有 `mutex`、没有 `spinlock`、没有 RCU、没有原子变量。**
唯一的共享可变状态是 `mfd_of_node_list`（`:24`）。

**四个访问点**：

| 访问 | 行 | 上下文 |
|---|---|---|
| 遍历查重 | `:135` | `mfd_add_device()` ← 父驱动 probe |
| 分配并插入 | `:156-162` | 同上 |
| 失败回滚删除 | `:319-323` | 同上 |
| 正常移除删除 | `:393-396` | `mfd_remove_devices*()` ← 父驱动 remove |

**它依赖的假设**：父驱动的 probe/remove 之间彼此串行。

⚠️⚠️ **这个假设在 6.1 上不成立**：

| 情形 | 是否并行 |
|---|---|
| 同一条总线上的两个设备 probe | 串行（总线的 probe 是顺序的） |
| ⚠️ **不同总线上的两个 mfd 父设备**（一个 I2C、一个 SPI） | **可以并行** |
| ⚠️ **`PROBE_PREFER_ASYNCHRONOUS` 的驱动** | **可以并行** |
| ⚠️ **一边 probe、另一边 remove**（`echo > unbind`） | **可以并行** |

⇒ **理论上的后果**：两个 CPU 同时 `list_add_tail()`
⇒ 链表被撕坏（一条表项丢失，或 `list_del` 时踩野指针）。

⚠️ **为什么至今没人报 bug**（可推的三条理由）：

1. mfd 父设备本来就少（一块板上通常 1–3 颗多功能芯片），且多在 I2C 上；
2. 用 `of_compatible` 的 cell 才会碰这条链表 —— 不用 DT 匹配的 cell 完全不进来；
3. probe 窗口极短，`list_add_tail` 只有几条指令。

⚠️⚠️ **但这是运气，不是设计**。同一个目录下的 `syscon.c` 就为它的全局链表加了
mutex，还把"必须持锁"写成了 `WARN_ON` 断言（`drivers/mfd/syscon.c:55`，02 §6.1）
⇒ **同目录、同年代、同类数据结构，两种态度** —— 08 §6 把这个对比当作
"框架代码的锁纪律靠什么保证"的教材。

⚠️ 另一处并发相关的观察：**`mfd_of_node_list` 是全局的而不是 per-parent 的**
⇒ 查重范围是全内核。这一点在正确性上是保守的（宁可多判"被占用"），
但让链表长度随系统上所有 mfd 子设备总数增长（§2.9 要点 2）。

---

## §6 syscon 的内部路径

### §6.1 七个接口，一个漏斗（`:151-173`）

⚠️ **01 §4 那七个导出函数全部收到同一个内部函数**：

```c
/* drivers/mfd/syscon.c:151-173 —— 节选 */
static struct regmap *device_node_get_regmap(struct device_node *np,
                                             bool check_clk)
{
        struct syscon *entry, *syscon = NULL;

        mutex_lock(&syscon_list_lock);

        list_for_each_entry(entry, &syscon_list, list)
                if (entry->np == np) {
                        syscon = entry;
                        break;
                }

        if (!syscon)
                syscon = of_syscon_register(np, check_clk);

        mutex_unlock(&syscon_list_lock);

        if (IS_ERR(syscon))
                return ERR_CAST(syscon);

        return syscon->regmap;
}
```

⚠️⚠️ **`if (!syscon) syscon = of_syscon_register(...)`（`:164-165`）是 syscon 最重要的一行**：

**syscon 的 regmap 是"第一次被查的时候才建出来"的（懒注册）。**

⇒ 由此得到四条实际后果：

1. ⚠️ **建 regmap 的上下文是消费者的 probe，不是 syscon 自己的启动流程**
   ⇒ `of_iomap()` 失败、时钟拿不到、hwspinlock 没就绪 ——
   **这些错误全部出现在调用者的 probe 日志里**，而不是 syscon 的（07 §5）；
2. ⚠️ **谁第一个查，决定了 regmap 的配置**：
   走 `syscon_node_to_regmap()`（`check_clk = true`）会挂上时钟，
   走 `device_node_to_regmap()`（`check_clk = false`）不挂
   ⇒ ⚠️⚠️ **同一个 DT 节点，第一个查的人用哪个 API，后面所有人都跟着它** ——
   因为第二个人只会命中链表里那条已经建好的记录，`check_clk` 根本不再被看（01 §4.2）；
3. ⚠️ **两个 CPU 同时第一次查同一个节点**：由 `syscon_list_lock` 保护，
   只会建一次 —— **这就是那把 mutex 存在的理由**（§5 的对比）；
4. ⚠️ 整个查找是 **O(n) 线性扫链表**。一块板上 syscon 节点通常个位数，无所谓。

### §6.2 `of_syscon_register()` —— 建一个 regmap（`:45-149`）

**十步，按源码顺序**：

| 步 | 行 | 干什么 | ⚠️ |
|---|---|---|---|
| 1 | `:55` | `WARN_ON(!mutex_is_locked(&syscon_list_lock))` | ⚠️ **把"调用者必须持锁"写成可执行断言**（02 §6.1） |
| 2 | `:57` | `kzalloc` 表项，带 `__free(kfree)` | ⚠️ 6.1.158 的回合补丁（99 §1.1） |
| 3 | `:61` | `of_address_to_resource(np, 0, &res)` | 拿 `reg` 的第一段 |
| 4 | `:64` | `base = of_iomap(np, 0)` | ⚠️ **成功路径上这个指针不被保存**（02 §6.3） |
| 5 | `:69-74` | 解析 `big-endian`/`little-endian`/`native-endian` | 三者互斥，`else if` 链 |
| 6 | `:81-83` | 解析 `reg-io-width`，**读不到就默认 4** | 见 §6.2.1 |
| 7 | `:85-101` | 解析 `hwlocks`，取 hwspinlock | ⚠️ 缺了是**可选**（`:93` 的注释），取到了但无效才报错（`:96`） |
| 8 | `:103-110` | 拼 regmap 名字 + 算三个参数 | 见 §6.2.1 |
| 9 | `:112` | `regmap = regmap_init_mmio(NULL, base, &syscon_config)` | ⚠️⚠️ **第一个参数是 `NULL`**，见 §6.2.2 |
| 10 | `:120-132` | `check_clk` 为真时取时钟并 `regmap_mmio_attach_clk()` | ⚠️ 时钟**不存在**是允许的（`:124-126` 只对非 `-ENOENT` 报错） |
| ✔ | `:137` `:139` | `list_add_tail` 然后 `return_ptr(syscon)` | ⚠️ `return_ptr()` 解除 `__free` 的自动释放 |

#### §6.2.1 三个参数怎么算出来的（`:103-110`）

```c
/* drivers/mfd/syscon.c:103-110 */
syscon_config.name = kasprintf(GFP_KERNEL, "%pOFn@%pa", np, &res.start);
...
syscon_config.reg_stride = reg_io_width;
syscon_config.val_bits = reg_io_width * 8;
syscon_config.max_register = resource_size(&res) - reg_io_width;
```

⚠️⚠️ **`reg-io-width` 一个属性同时决定三件事**：步进、数据位宽、最后一个合法寄存器。

| `reg-io-width` | `reg_stride` | `val_bits` | `max_register`（窗口 0x100 时） |
|---|---|---|---|
| 缺省（=4） | 4 | 32 | `0x100 - 4 = 0xfc` |
| `<1>` | 1 | 8 | `0x100 - 1 = 0xff` |
| `<2>` | 2 | 16 | `0x100 - 2 = 0xfe` |

⚠️ **注意 `reg_bits` 始终是 32**（来自模板 `:39-43`，02 §6.2）——
只有**值**的宽度跟着 `reg-io-width` 变，**寄存器地址**永远按 32 位编号。

⚠️⚠️ **`max_register` 的单位是"字节偏移"而不是"寄存器序号"** ——
因为 `reg_stride` 非 1 时 regmap 用的是字节地址。
⇒ **DT 里 `reg` 的长度写短了，超出的寄存器 `regmap_read()` 直接返回 `-EIO`**，
而不是读到垃圾（07 §5）。

⚠️ `kasprintf` 拼出的名字形如 `syscon@1c00000`（`%pOFn` 是节点名，`%pa` 是物理地址）
⇒ **这个名字就是 `/sys/kernel/debug/regmap/` 下的目录名** ——
**syscon 唯一的可观测出口**（07 §5）。
⚠️ 名字在 `:113` 被 `kfree` 掉 —— regmap 内部已经复制了一份，这里不漏。

#### §6.2.2 `regmap_init_mmio(NULL, ...)` 的后果

⚠️⚠️ **第一个参数是 `struct device *`，syscon 传的是 `NULL`。**

| 影响 | 说明 |
|---|---|
| ⚠️ 没有宿主设备 | **不能用 `devm_`**，所以只能手工管理生命周期 ⇒ 与"永不移除"自洽（02 §6.3） |
| ⚠️ debugfs 名字 | 没有 `dev_name()` 可用 ⇒ 必须自己 `kasprintf` 一个（`:103`） |
| ⚠️ 无 runtime PM | regmap 不会替你做父设备的 runtime PM get/put |
| ⚠️ 日志无设备前缀 | regmap 内部的错误用 `pr_*` 而非 `dev_*` |

⇒ **这是"syscon 的 regmap 不属于任何设备"这个设计的技术表达。**
它描述的是 SoC 上一块**没有驱动认领**的杂项寄存器区
—— 没有设备，也就没有生命周期。

#### §6.2.3 三个错误标签（`:141-147`）

```
err_attach:   :141   if (!IS_ERR(clk)) clk_put(clk);
err_clk:      :144   regmap_exit(regmap);
err_regmap:   :146   iounmap(base);
              :147
```

⚠️ 依次落下的阶梯，与 §2.9 同一个套路。
⚠️⚠️ **`err_regmap` 是最外层，负责 `iounmap`** ⇒ **失败路径不漏 io 映射，
但成功路径永远不 `iounmap`**（02 §6.3）—— 两者是同一个决定的两面。

### §6.3 那个几乎跑不到的 platform_driver（`:324-378`）

`syscon.c` 里还有一个完整的 platform 驱动：

```c
/* drivers/mfd/syscon.c:361-372 */
static const struct platform_device_id syscon_ids[] = {
        { "syscon", },
        { }
};

static struct platform_driver syscon_driver = {
        .driver = {
                .name = "syscon",
        },
        .probe          = syscon_probe,
        .id_table       = syscon_ids,
};
```

⚠️⚠️ **它没有 `of_match_table`** ⇒ **DT 里的 `compatible = "syscon"` 节点不会绑到它**
（DT 实例化出来的 platform_device 名字形如 `1c00000.syscon`，与 id_table 里的
`"syscon"` 不相等）。
⇒ **它只能匹配"由代码显式创建、名字恰好是 `syscon` 的 platform_device"**
—— 板级文件或某个 mfd cell 那种老式用法。

⚠️ 更关键的一点：**`syscon_probe()`（`:324-359`）建好 regmap 之后
只 `platform_set_drvdata()`（`:354`），从不 `list_add_tail` 到 `syscon_list`**
⇒ ⚠️⚠️ **这条路径产出的 regmap，`syscon_regmap_lookup_by_*()` 一个都查不到。**

⚠️ 它与 DT 路径的三处配置差异：

| 项 | `syscon_probe()`（`:324-359`） | `of_syscon_register()`（`:45-149`） |
|---|---|---|
| 内存 | ⚠️ `devm_ioremap`(`:341`) / `devm_regmap_init_mmio`(`:348`) ⇒ **能释放** | `of_iomap` / `regmap_init_mmio` ⇒ 永不释放 |
| `max_register` | ⚠️ **硬编码 `resource_size(res) - 4`**(`:345`) | 按 `reg-io-width` 算（`:110`） |
| endian / hwlock / clk | ⚠️ **全部不支持** | 都支持 |
| 名字 | 来自 `pdata->label`（`:346-347`） | `kasprintf`（`:103`） |
| 进 `syscon_list` | ⚠️⚠️ **不进** | 进（`:137`） |

⚠️ 而 `struct syscon_platform_data`（`include/linux/platform_data/syscon.h:5`）
**全树没有任何代码去填它** —— `grep -rn syscon_platform_data` 只有定义处和
`drivers/mfd/syscon.c:327` 的读取处两个命中。
⇒ ⚠️⚠️ **这条 platform_driver 路径在本树上是事实上的死代码**，
arm64 DT 平台只会走 §6.1 的懒注册路径（99 §6）。

⚠️ `postcore_initcall(syscon_init)`（`:378`）把驱动注册得非常早
（`postcore` 早于 `arch`/`subsys`/`device`）—— **但既然没有设备会匹配上，
这个早期注册在 arm64 上没有实际效果**。
真正保证"syscon 随时可用"的机制是懒注册，**不是 initcall 顺序**。

⚠️ 顺带：`syscon_probe()` 里有 `-ENOENT`（`:339`，`platform_get_resource` 失败）
⇒ **这是 mfd 目录核心层唯一的 `-ENOENT`**，而它在死代码里。

---

## §7 内部实现的反直觉点汇总

| # | 结论 | 依据 |
|---|---|---|
| 1 | ⚠️⚠️ DT 子节点标 `disabled` ⇒ 走失败路径**返回 0**，子设备静默消失 | §2.4 出口 B |
| 2 | ⚠️ 匹配不到 of_node ⇒ **只 `pr_warn`，子设备照样注册** | §2.4 出口 C |
| 3 | ⚠️⚠️ `mem_base` 传 `NULL` ⇒ MEM 地址由**相对**变**绝对** | §2.7 |
| 4 | ⚠️ 换算结果**不检查是否落在 `mem_base` 内**；`mem_base->end` 从不被读 | §2.7.1 |
| 5 | ⚠️ `irq_create_mapping()` 返回值**不检查** | §2.7.2 |
| 6 | ⚠️ ACPI 冲突检查在 arm64 上是死代码 ⇒ 资源循环**不可能失败** | §2.7.3 |
| 7 | ⚠️ `pm_runtime_no_callbacks()` 在设备**已注册之后**才调 | §2.8 |
| 8 | ⚠️⚠️ 同一父设备第二次 `mfd_add_devices()` 失败，会删掉**第一次建的** | §3 |
| 9 | ⚠️⚠️ 回滚只删 `level <= NORMAL` ⇒ `HIGH` 的 cell 漏回滚 | §3 |
| 10 | ⚠️ `mfd_remove_devices_fn()` 里 `cell` 在 `:402` 之后即为野指针 | §4.1 |
| 11 | ⚠️⚠️ 全局链表 `mfd_of_node_list` **无锁**，而同目录 syscon 有锁+断言 | §5 |
| 12 | ⚠️⚠️ syscon 的 regmap 是**懒注册**的；第一个查的人的 `check_clk` 定终身 | §6.1 |
| 13 | ⚠️ `reg-io-width` 一个属性同时改 `reg_stride`/`val_bits`/`max_register` | §6.2.1 |
| 14 | ⚠️ `regmap_init_mmio(NULL, ...)` ⇒ regmap 不属于任何设备 | §6.2.2 |
| 15 | ⚠️⚠️ `syscon_probe()` 建的 regmap **不进 `syscon_list`**，且这条路径是死代码 | §6.3 |

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 接口签名、返回值、`CONFIG=n` 桩 | 01-api.md |
| `struct mfd_cell` 各字段语义 | 02-datastructures.md |
| 真实芯片驱动怎么用这套机制 | 04-extensions.md |
| 我要写父驱动，正确姿势是什么 | 05-usage.md |
| `simple-mfd` / `reg-io-width` / `hwlocks` 怎么写 | 06-devicetree.md |
| 子设备没出现 / regmap 读不到，怎么查 | 07-debug.md |
| 为什么这套设计是对的 | 08-feynman.md |
| 上面每条结论的证据 | 99-evidence.md |
