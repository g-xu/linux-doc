---
title: 内部实现
---

# phy 03：内部机制

## §0 导航

**本篇只承诺一条主轴：Part A 是时间线**（从内核启动到 phy 销毁）。
**Part B 是横切关注点**（锁 / 引用计数 / devres / PM），**Part C 是评价与设计债。**
**章节号带 A/B/C 前缀，引用时自带"在哪条轴上"的信息。**

### §0.1 分层图

```
  ┌───────────────────────────────────────────────────────────┐
  │ consumer 驱动（USB / PCIe / SATA 控制器…）                │
  │   devm_phy_get() → phy_init() → phy_power_on() → …        │
  └────────────────────────┬──────────────────────────────────┘
                           │  40 个导出符号
  ┌────────────────────────▼──────────────────────────────────┐
  │ drivers/phy/phy-core.c（1244 行）—— 框架全部逻辑在这一个文件 │
  │                                                            │
  │  ① DT 解析     phys / #phy-cells / phy-names               │
  │  ② provider 表 phy_provider_list + phy_provider_mutex      │
  │  ③ 引用计数    init_count / power_count（per-phy mutex）    │
  │  ④ 电源        phy->pwr（regulator）+ PM runtime            │
  │  ⑤ devres      三个 release 回调                            │
  │  ⑥ class       phy_class（/sys/class/phy/）                │
  └────────────────────────┬──────────────────────────────────┘
                           │  唯一一层可插拔：struct phy_ops（12 字段）
  ┌────────────────────────▼──────────────────────────────────┐
  │ PHY 驱动（provider）—— drivers/phy/<厂商>/，约 200 个文件   │
  │   真正碰寄存器的地方                                        │
  └───────────────────────────────────────────────────────────┘
```

### §0.2 生命周期时间线

```
 内核启动
   │
   ├─ device_initcall: phy_core_init()          §A1   phy-core.c:1231-1244
   │     └─ class_create(THIS_MODULE, "phy")
   │
   ├─ PHY 驱动 probe（provider）
   │     ├─ devm_phy_create(dev, node, ops)     §A2   :1019
   │     │     └─ phy_create()                        :943
   │     └─ devm_of_phy_provider_register(dev, xlate) §A3   :1151
   │
   ├─ 控制器驱动 probe（consumer）
   │     ├─ devm_phy_get(dev, "usb2-phy")       §A4   :806
   │     │     └─ phy_get() → _of_phy_get() → of_xlate()
   │     ├─ phy_init(phy)                       §A5   :245
   │     ├─ phy_set_mode(phy, PHY_MODE_USB_HOST)§A6   :399
   │     └─ phy_power_on(phy)                   §A6   :322
   │
   ├─ 运行期：phy_configure / phy_validate / phy_reset / phy_calibrate  §A6
   │
   ├─ 控制器驱动 remove
   │     ├─ phy_power_off(phy)                  §A7   :372
   │     ├─ phy_exit(phy)                       §A7   :285
   │     └─ devres 自动 phy_put(dev, phy)       §A7   :28-33 / :670
   │
   └─ PHY 驱动 remove
         ├─ devres 自动 of_phy_provider_unregister()  §A8   :35-40 / :1181
         └─ devres 自动 phy_destroy()                 §A8   :42-47 / :1046
               └─ device_unregister → phy_release()         :1220-1229
```

⚠️ **这个次序是用"谁调用谁"验证的，不是凭直觉排的**：
**`devm_phy_create` 必须早于 `devm_of_phy_provider_register`** ——**因为
`of_phy_simple_xlate()` 靠遍历 `phy_class` 找实例（§A3.2），实例不存在就找不到。**
**树里的驱动普遍是这个顺序**（99 §2.4 的代表驱动都核对过）。

### §0.3 哪些内容不在本篇

| 想找 | 去 |
|---|---|
| API 清单与签名 | `01-api.md` |
| 结构体字段与易错类比 | `02-datastructures.md` |
| **子框架**（phy 只有一个：MIPI D-PHY 辅助） | `04-extensions.md` |
| 代码骨架 | `05-usage.md` |
| DT 属性 | `06-devicetree.md` |
| 调试出口 | `07-debug.md` |

---

# Part A：时间线

## §A1 框架自身的初始化（`phy-core.c:1231-1244`）

```c
static int __init phy_core_init(void)
{
	phy_class = class_create(THIS_MODULE, "phy");
	if (IS_ERR(phy_class)) {
		pr_err("failed to create phy class --> %ld\n",
			PTR_ERR(phy_class));
		return PTR_ERR(phy_class);
	}

	phy_class->dev_release = phy_release;

	return 0;
}
device_initcall(phy_core_init);
```

| 事实 | 说明 |
|---|---|
| **initcall 级别是 `device_initcall`** | **不是 `subsys_initcall`** ⇒ **比多数总线子系统晚**；**但 PHY 驱动都是 platform driver（更晚），所以够用** |
| **`phy_class->dev_release = phy_release`** | **class 级别的释放回调**（`:1240`）⇒ **每个 phy 设备最后一个引用消失时走这里** |
| ⚠️ **没有 `module_exit`** | **`phy-core.c` 全文没有 `__exit` 函数** ⇒ **框架一旦编入就不能卸载**（`GENERIC_PHY` 是 `bool` 而非 `tristate`，01 §0.4） |
| **唯一一条 `pr_err`** | `failed to create phy class --> %ld`（`:1235-1236`）——**全框架唯一的 `pr_*` 打印**（07 §4） |

**`phy_release()`（`:1220-1229`）做三件事**：
`regulator_put(phy->pwr)`（`:1226`）· `ida_simple_remove(&phy_ida, phy->id)`（`:1227`）· `kfree(phy)`（`:1228`）。
⚠️ **它不调 `ops->release`** ——**那个回调在 `of_phy_put()` 里**（01 §2 / §A7.3）。

## §A2 provider 侧第一步：`phy_create()`（`phy-core.c:943-1005`）

**九步，顺序有讲究**：

| 步 | 代码 | 位置 | 失败怎么办 |
|---|---|---|---|
| ① | `if (WARN_ON(!dev)) return ERR_PTR(-EINVAL)` | `:950-951` | **`WARN_ON` + `-EINVAL`** ——⚠️ **全框架唯一一处裸 `WARN_ON(`**；**`grep -c 'WARN_ON'` 会返回 4，因为 `dev_WARN_ONCE` 里含同一个子串**（99 §6 的口径条） |
| ② | `kzalloc(sizeof(*phy))` | `:953-955` | `-ENOMEM` |
| ③ | `ida_simple_get(&phy_ida, 0, 0, GFP_KERNEL)` | `:957-962` | **`dev_err "unable to get id\n"` + `goto free_phy`** |
| ④ | `device_initialize(&phy->dev)` + `mutex_init(&phy->mutex)` | `:964-965` | — |
| ⑤ | **三个赋值定下身份**：`dev.class = phy_class` · `dev.parent = dev` · `dev.of_node = node ?: dev->of_node` | `:967-969` | **注意 `node` 为 NULL 时回落到 provider 自己的 `of_node`** |
| ⑥ | `dev_set_name(&phy->dev, "phy-%s.%d", dev_name(dev), id)` | `:973` | **`goto put_dev`** |
| ⑦ | `phy->pwr = regulator_get_optional(&phy->dev, "phy")` | `:978-985` | ⚠️ **只有 `-EPROBE_DEFER` 才算失败**（`:981-982`）；**其他错误一律 `phy->pwr = NULL`**（`:984`） |
| ⑧ | `device_add(&phy->dev)` | `:987-989` | `goto put_dev` |
| ⑨ | ⚠️ **有条件地启用 PM runtime** | `:991-994` | — |

### §A2.1 ⚠️ 第 ⑨ 步与官方文档不一致（本篇第一个重要发现）

```c
/* drivers/phy/phy-core.c:991-994 */
	if (pm_runtime_enabled(dev)) {
		pm_runtime_enable(&phy->dev);
		pm_runtime_no_callbacks(&phy->dev);
	}
```

**官方文档 `phy.rst` 的说法是无条件的**：
> *"while creating the PHY, pm_runtime_enable of the phy device created by this subsystem is called"*

⚠️ **代码里有前提条件 `pm_runtime_enabled(dev)`** ——**`dev` 是 provider 的设备。**
⇒ **provider 自己没启用 PM runtime，phy 实例就也不会启用。**
⇒ **这时所有 `phy_pm_runtime_*()` 都会返回 `-ENOTSUPP`**（而框架把它吃掉，§B4）。
**以代码为准**（99 §1.3）。

**`pm_runtime_no_callbacks(&phy->dev)`（`:993`）的含义**：
**phy 设备自己没有 runtime 回调，它只是一个"传导节点"** ——
**`get`/`put` 的实际效果全部落在父设备（provider）上**（§B4）。

### §A2.2 两条错误路径的区别

| 标签 | 位置 | 做什么 | 什么时候走 |
|---|---|---|---|
| `free_phy:` | `:1002-1004` | **裸 `kfree(phy)`** | **只有第 ③ 步（IDA 失败）走** ——**此时 `device_initialize` 还没做** |
| `put_dev:` | `:998-1000` | **`put_device(&phy->dev)`** ——**源码注释写明 "calls phy_release() which frees resources"** | **第 ⑥⑦⑧ 步失败走** |

⇒ **`device_initialize()` 之后就不能再裸 `kfree`**，**这是标准的设备模型纪律。**

## §A3 provider 侧第二步：注册 provider

### §A3.1 `__of_phy_provider_register()`（`phy-core.c:1088-1134`）

| 步 | 代码 | 位置 |
|---|---|---|
| ① | **`children` 非空时校验它是 `dev->of_node` 的后代** | `:1100-1115`（沿 `of_get_parent()` 上溯） |
| ② | **`children` 为空时替换成 `dev->of_node`** | `:1116-1118` ——**所以字段永远不为 NULL**（02 §5.4） |
| ③ | `kzalloc` | `:1120-1122` |
| ④ | **四个赋值**：`dev` / `children = of_node_get(children)` / `owner` / `of_xlate` | `:1124-1127` |
| ⑤ | **加锁挂链表** | `:1129-1131` |

⚠️ **第 ① 步的失败码是 `-EINVAL`**（`:1112-1113`）——
**源码注释就是这条规则的说明**（`:1095-1099`）：
> *"If specified, the device node containing the children must itself be the provider's device node
> or a child (or further descendant) thereof."*

⚠️ **`of_node_get(children)`（`:1125`）拿了引用，`of_phy_provider_unregister()` 里 `of_node_put`（`:1188`）放掉。**
**注册失败的路径上没有 `of_node_get`，所以不需要配对。**

### §A3.2 `of_phy_simple_xlate()`：遍历整个 class

```c
/* drivers/phy/phy-core.c:707-725 */
struct phy *of_phy_simple_xlate(struct device *dev, struct of_phandle_args
	*args)
{
	struct phy *phy;
	struct class_dev_iter iter;

	class_dev_iter_init(&iter, phy_class, NULL, NULL);
	while ((dev = class_dev_iter_next(&iter))) {
		phy = to_phy(dev);
		if (args->np != phy->dev.of_node)
			continue;

		class_dev_iter_exit(&iter);
		return phy;
	}

	class_dev_iter_exit(&iter);
	return ERR_PTR(-ENODEV);
}
```

⚠️ **三个必须点明的点**：
1. ⚠️ **它的 kerneldoc 说 `@args: of_phandle_args (not used here)`（`drivers/phy/phy-core.c:700`），
   但代码第 `:716` 行用了 `args->np`。**
   ⇒ **准确的说法是"不用 `args->args[]` 里的 cell 值，但用 `args->np`"。**
   **kerneldoc 这句话是误导性的**（99 §1.3）。
2. **它遍历的是整个 `phy_class`，不是"这个 provider 的实例"** ——
   **匹配条件只有 `of_node` 相等** ⇒ **谁的 `of_node` 对上就返回谁。**
3. **正因为按 `of_node` 匹配，"一个 provider 一个实例"时它才够用** ——
   **多实例共用同一个 `of_node` 就会永远返回第一个**（这就是必须自写 `of_xlate` 的理由，01 §3.3）。

**`dev` 形参在循环里被复用当迭代变量**（`:714`）——**函数返回后原来的 `dev` 已被覆盖**，
**不影响正确性（后面不再用），但读代码时容易被绕。**

### §A3.3 devm 版只是包了一层 devres

```c
/* drivers/phy/phy-core.c:1156-1172 —— 三个 devm_* 注册函数全是这个形状 */
	ptr = devres_alloc(devm_phy_provider_release, sizeof(*ptr), GFP_KERNEL);
	if (!ptr)
		return ERR_PTR(-ENOMEM);

	phy_provider = __of_phy_provider_register(dev, children, owner, of_xlate);
	if (!IS_ERR(phy_provider)) {
		/* *ptr = phy_provider; */
		devres_add(dev, ptr);
	} else {
		devres_free(ptr);
	}
```

**"先分配 devres、再做事、成功才 add"这个模式在本文件出现 5 次**（重跑
`grep -n 'devres_alloc' drivers/phy/phy-core.c` ⇒ 5 行）：
`devm_phy_get`(`:810`) · `devm_of_phy_get`(`:866`) · `devm_of_phy_get_by_index`(`:906`) ·
`devm_phy_create`(`:1024`) · `__devm_of_phy_provider_register`(`:1158`)。

⚠️ **`devm_phy_optional_get` 不在这 5 个里** ——**它是 `devm_phy_get` 的三行包装**
（`drivers/phy/phy-core.c:839-847`）：**调 `devm_phy_get()`，然后把 `-ENODEV` 换成 NULL。**
**`phy_optional_get`（`:785-793`）与 `phy_get` 的关系完全相同。**

**三个 devres 释放回调，各管一件事**（`drivers/phy/phy-core.c:28-47`）：

| 回调 | 位置 | 调什么 | 谁用 |
|---|---|---|---|
| `devm_phy_release` | `:28-33` | **`phy_put(dev, phy)`** | **consumer 侧的四个 `devm_*_get`** |
| `devm_phy_consume` | `:42-47` | **`phy_destroy(phy)`** | **provider 侧的 `devm_phy_create`** |
| `devm_phy_provider_release` | `:35-40` | **`of_phy_provider_unregister()`** | **`__devm_of_phy_provider_register`** |

⚠️ **名字与用途的对应容易记反**：**`release` 是"消费者放手"，`consume` 是"提供者销毁"。**
**匹配函数只有一个 `devm_phy_match`（`:49-54`），靠指针相等判断。**

## §A4 consumer 侧：取用（`phy_get` → `_of_phy_get` → `of_xlate`）

**完整数据流图在 02 §3**，这里只讲三个"为什么"。

### §A4.1 为什么 `phys` 属性缺失返回 `-ENODEV` 而不是 `-EINVAL`

```c
/* drivers/phy/phy-core.c:575-578 */
	ret = of_parse_phandle_with_args(np, "phys", "#phy-cells",
		index, &args);
	if (ret)
		return ERR_PTR(-ENODEV);	/* 原始错误码被丢掉了 */
```

⇒ **不管 `of_parse_phandle_with_args()` 因为什么失败（属性不存在 / cell 数不对 / 下标越界），
一律变成 `-ENODEV`。**
⇒ **这是"可选 PHY"能工作的关键**：**`phy_optional_get()` 只认 `-ENODEV`**（01 §4.1）。
⚠️ **代价是"DT 写错"和"DT 没写"无法区分**（06 §4 / 07 §5）。

### §A4.2 ⚠️ 一个硬编码的 compatible 特例（全框架唯一）

```c
/* drivers/phy/phy-core.c:581-584 */
	if (of_device_is_compatible(args.np, "usb-nop-xceiv")) {
		phy = ERR_PTR(-ENODEV);
		goto out_put_node;
	}
```

⇒ **`compatible = "usb-nop-xceiv"` 的节点被通用 PHY 框架显式拒绝。**
**原因**：**那种"什么都不做的 USB 收发器"由 `drivers/usb/phy/` 的老框架处理，不是这一套。**
**这是 `phy-core.c` 里唯一一处按 compatible 字符串做判断的地方**
（重跑 `grep -c 'of_device_is_compatible' drivers/phy/phy-core.c` ⇒ **1**）。
⚠️ **它返回 `-ENODEV`** ⇒ **在 `phy_optional_get()` 下会静默变成 NULL**（05 §7）。

### §A4.3 两处 `-EPROBE_DEFER` 的来源

| 位置 | 条件 | 含义 |
|---|---|---|
| `drivers/phy/phy-core.c:588-589` | **`IS_ERR(phy_provider)` 或 `!try_module_get(phy_provider->owner)`** | **provider 还没注册 / provider 模块正在卸载** |
| `drivers/phy/phy-core.c:761-762` | **`!try_module_get(phy->ops->owner)`** | **PHY 驱动模块正在卸载** |

⚠️ **`:588` 把两种情况合并成一个码**：
**"provider 不存在"（正常的探测顺序问题，应该 defer）和
"provider 存在但模块在卸载"（不该 defer，defer 也没用）** ——**无法区分。**

⚠️ **`:602` 的 `module_put(phy_provider->owner)` 在 `of_xlate` 之后立刻放掉**
⇒ **provider 模块的引用只在查找期间持有**；**长期引用靠 `:761` 那次
`try_module_get(phy->ops->owner)`**（注意是 **`ops->owner`** 而不是 **`provider->owner`**）。

## §A5 `phy_init()` / `phy_exit()`：引用计数的第一层

**`phy_init()` 五步**（`drivers/phy/phy-core.c:245-274`）：

| 步 | 代码 | 位置 |
|---|---|---|
| ① | **`if (!phy) return 0`** | `:249-250` |
| ② | **`phy_pm_runtime_get_sync()` + 吃掉 `-ENOTSUPP`** | `:252-255` |
| ③ | **`mutex_lock(&phy->mutex)`** | `:257` |
| ④ | **顺序检查（只警告）**：`if (power_count > init_count) dev_warn(...)` | `:258-259` |
| ⑤ | **`if (init_count == 0 && ops->init)` 才调回调；失败 `dev_err "phy init failed --> %d"`** | `:261-267` |
| ⑥ | `++init_count` → unlock → `phy_pm_runtime_put()` | `:268-272` |

⚠️ **第 ⑤ 步失败时 `goto out`（`:265`）⇒ 计数不增加**
**但 `out:` 标签在 `++init_count` 之后**（`:270`）⇒ **确实跳过了自增，正确。**

⚠️ **`phy_exit()` 的对称性有一处不对称**（`:285-311`）：
**`if (init_count == 1 && ops->exit)`（`:298`）** ——
**`init_count` 为 0 时条件不成立 ⇒ 不调回调，但 `--init_count`（`:305`）照样执行 ⇒ 变成 -1**（02 §1.0.D）。

## §A6 稳态操作：三种保护形态

**11 个消费者操作 API 分成三类，保护强度递减**：

| 类别 | API | 形态 |
|---|---|---|
| **① 计数 + PM + 锁**（最重） | `phy_init` `phy_exit` `phy_power_on` `phy_power_off` | **引用计数 + PM runtime + mutex** |
| **② PM + 锁** | `phy_reset` | **`phy_pm_runtime_get_sync`（`:454`）+ mutex，无计数** |
| **③ 只有锁** | `phy_set_mode_ext` `phy_set_media` `phy_set_speed` `phy_calibrate` `phy_configure` `phy_validate` | **只 `mutex_lock`，不碰 PM runtime** |

⚠️ **第 ③ 类有一个实际后果**：
**PHY 处于 runtime suspend 状态时调 `phy_configure()`，框架不会先唤醒它。**
⇒ **驱动的 `configure` 回调必须自己处理"设备可能没上电"**，**或者由消费者保证时序。**
**`configure` 的 kerneldoc 只要求"`phy_init()` 必须先调过"**（`include/linux/phy/phy.h:98`），
**没提 PM runtime。**

**`phy_power_on()` 逐步剖析见 02 §3 的数据流图**（不重复）。

## §A7 拆除路径

### §A7.1 consumer 侧

| 步 | 谁调 | 位置 |
|---|---|---|
| ① `phy_power_off(phy)` | **消费者自己**（框架不会代劳） | `drivers/phy/phy-core.c:372` |
| ② `phy_exit(phy)` | 消费者自己 | `:285` |
| ③ `phy_put(dev, phy)` | **用了 `devm_*_get` 就由 devres 自动调**（`:28-33`） | `:670` |

⚠️ **第 ①② 步没有任何自动化** ——**`devm_phy_get` 只保证 `phy_put`，不保证 `power_off`/`exit`。**
⇒ **消费者驱动 remove 时忘了 `phy_power_off` ⇒ 计数永远不归零 ⇒ PHY 一直上电**（05 §7 / 07 §4）。

### §A7.2 `phy_put()` 做三件事

```c
/* drivers/phy/phy-core.c:670-674 */
void phy_put(struct device *dev, struct phy *phy)
{
	device_link_remove(dev, &phy->dev);
	of_phy_put(phy);
}
```
**然后 `of_phy_put()`（`:648-660`）**：
① **`if (!phy || IS_ERR(phy)) return`**（`:650-651`）——**对 NULL 和 ERR_PTR 都安全**；
② **加锁调 `ops->release`**（`:653-656`）；
③ **`module_put(phy->ops->owner)`**（`:658`）；
④ **`put_device(&phy->dev)`**（`:659`）。

### §A7.3 ⚠️`ops->release` 的调用时机（容易想错）

| 想法 | 实际 |
|---|---|
| ❌ *"`release` 是设备销毁时的最后清理"* | **不是。它在每次 `of_phy_put()` 时被调**（`drivers/phy/phy-core.c:654-655`） |
| **真正的设备销毁回调** | **`phy_release()`**（`:1220-1229`），**由 `phy_class->dev_release` 挂上**（`:1240`）；**它不调 `ops->release`** |

⇒ **一个被 N 个消费者共享的 phy，`ops->release` 会被调 N 次。**
**驱动在 `release` 里做"只能做一次"的清理是错的。**

### §A7.4 provider 侧

| 步 | devres 回调 | 最终调 |
|---|---|---|
| ① 注销 provider | `devm_phy_provider_release`（`:35-40`） | `of_phy_provider_unregister()`（`:1181-1191`）——**锁内 `list_del` + `of_node_put` + `kfree`** |
| ② 销毁 phy | `devm_phy_consume`（`:42-47`） | `phy_destroy()`（`:1046-1050`）——**`pm_runtime_disable` + `device_unregister`** |
| ③ 设备最终释放 | **`phy_class->dev_release`** | `phy_release()`（`:1220-1229`） |

⚠️ **devres 的释放顺序是"后进先出"** ⇒
**`devm_of_phy_provider_register` 在 `devm_phy_create` 之后调用，所以先被释放** ⇒
**provider 先下线，phy 实例后销毁** ——**顺序正确（避免注销期间还有人能查到实例）。**

⚠️ **`of_phy_provider_unregister()` 对 `IS_ERR` 参数直接 return**（`:1183-1184`），
**但对 NULL 不做检查** ⇒ **传 NULL 会在 `list_del(&phy_provider->list)` 空指针崩溃。**

## §A8 三处 `dev_WARN_ONCE`：devres 找不到时的诊断

| 位置 | 消息（逐字） | 什么时候出现 |
|---|---|---|
| `drivers/phy/phy-core.c:693` | `couldn't find PHY resource\n` | **`devm_phy_put()` 找不到 devres** |
| `drivers/phy/phy-core.c:1066` | `couldn't find PHY resource\n` | **`devm_phy_destroy()` 找不到 devres** |
| `drivers/phy/phy-core.c:1209` | `couldn't find PHY provider device resource\n` | **`devm_of_phy_provider_unregister()` 找不到 devres** |

⚠️ **前两条消息字面完全相同**（逐字核对过）
⇒ **看到这条 warn 无法区分是 `devm_phy_put` 还是 `devm_phy_destroy` 触发的**，
**只能靠栈**（07 §4.2）。

**典型原因**：**用了非 devm 版创建 / 取用，却调了 devm 版释放**（05 §7）。

---

# Part B：横切关注点

## §B1 两种锁，各管一件事

| 锁 | 位置 | 保护什么 | 临界区长度 |
|---|---|---|---|
| **`phy_provider_mutex`（全局）** | `drivers/phy/phy-core.c:23` | **`phy_provider_list` + `phys`（lookup 表）两个链表** | **短**（链表增删查） |
| **`phy->mutex`（per-phy）** | `include/linux/phy/phy.h:156` | **`init_count` / `power_count` / `attrs.mode` + `ops` 回调的串行化** | ⚠️ **长** ——**驱动的回调整个在锁内跑** |

⚠️ **全框架 0 处 spinlock、0 处 `_irqsave`**
（重跑 `grep -c 'spin_lock\|irqsave' drivers/phy/phy-core.c` ⇒ **0**）
⇒ **phy 的所有 API 都可能睡眠，一律不能在中断上下文里调。**

⚠️ **一个隐式锁契约**：**`of_phy_provider_lookup()`（`:131-145`）自己不加锁，
要求调用者已持 `phy_provider_mutex`** ——**`_of_phy_get()` 在 `:586` 先加锁。**
**源码里没有 `lockdep_assert_held()`，也没有注释说明**（Part C §C4）。

## §B2 引用计数一共四层，互不相干

| 层 | 计数在哪 | 谁加谁减 | 作用 |
|---|---|---|---|
| **① 模块引用**（provider 模块） | `module` 结构 | **`try_module_get(phy_provider->owner)`（`:588`）→ `module_put`（`:602`）** | **只在查找期间持有** |
| **② 模块引用**（PHY 驱动模块） | `module` 结构 | **`try_module_get(phy->ops->owner)`（`:761` / `:633`）→ `module_put`（`:658`）** | **消费者持有引用期间一直持有** |
| **③ 设备引用** | `phy->dev.kobj` | **`get_device(&phy->dev)`（`:764` / `:636`）→ `put_device`（`:659`）** | **决定 `phy_release()` 何时被调** |
| **④ 功能计数** | **`init_count` / `power_count`** | **`phy_init`/`phy_exit` · `phy_power_on`/`phy_power_off`** | **决定 `ops` 回调何时真的被调** |

⚠️ **第 ①② 层用的 `owner` 不是同一个字段**：
**`phy_provider->owner`（`include/linux/phy/phy.h:174`，来自注册宏的 `THIS_MODULE`）
与 `phy->ops->owner`（`:126`，PHY 驱动在 `phy_ops` 里填的）。**
⇒ **同一个驱动里这两个通常是同一个模块，但框架把它们当两回事。**

⚠️ **第 ④ 层是唯一没有下溢保护的一层**（02 §1.0.D / Part C §C3）。

## §B3 devres：三个回调，三种拆除语义

**完整表在 §A3.3。这里只强调一条易错点**：

| 你用的 | 释放时会调 | ⚠️ 不会调 |
|---|---|---|
| `devm_phy_get` | **`phy_put(dev, phy)`** | ⚠️ **不会调 `phy_power_off` / `phy_exit`** |
| `devm_phy_create` | **`phy_destroy(phy)`** | — |
| `devm_of_phy_provider_register` | **`of_phy_provider_unregister()`** | — |

⇒ **devm 只帮你管"引用"和"对象"，不帮你管"状态"。**
**上电了就必须自己关**（§A7.1）。

## §B4 PM runtime：两条规律

### §B4.1 规律一：phy 只是传导节点

**`pm_runtime_no_callbacks(&phy->dev)`（`drivers/phy/phy-core.c:993`）**
⇒ **phy 设备自身没有 runtime suspend/resume 回调。**
⇒ **`phy_pm_runtime_get_sync(phy)` 的实际效果是"唤醒 provider 设备"**
（通过 `dev.parent` 的父子传导，`:968`）。

**官方文档明写这一点**（`phy.rst` "PM Runtime"）：
> *"pm_runtime_get_sync of the phy_device created by this subsystem will invoke
> pm_runtime_get_sync of PHY provider device because of parent-child relationship."*

### §B4.2 ⚠️ 规律二：`-ENOTSUPP` 被系统性地吃掉

**三处完全相同的处理**（`drivers/phy/phy-core.c:252-255` / `:292-295` / `:335-339`）：

```c
	ret = phy_pm_runtime_get_sync(phy);
	if (ret < 0 && ret != -ENOTSUPP)
		return ret;
	ret = 0; /* Override possible ret == -ENOTSUPP */
```

**加上 `phy_reset()` 里的一处变体**（`:454-456`，**少了 `ret = 0` 那一行，
因为它后面会被 `ops->reset` 的返回值覆盖**）。

⇒ **含义**：**"这个 phy 没启用 PM runtime"是完全正常的情况**（§A2.1），
**不该让 `phy_init()` 失败。**
⇒ **所以 `-ENOTSUPP` 几乎不会传到消费者手上**（01 §9.1）。

## §B5 device link：三处建、一处拆

| 动作 | 位置 | 标志 |
|---|---|---|
| 建 | `drivers/phy/phy-core.c:766`（`phy_get`）· `:879`（`devm_of_phy_get`）· `:926`（`devm_of_phy_get_by_index`） | **`DL_FLAG_STATELESS`** |
| 拆 | `drivers/phy/phy-core.c:672`（`phy_put`） | — |

⚠️ **`of_phy_get()` 不建 device link**（`:621-639` 全文没有 `device_link_add`）
⇒ **用裸 `of_phy_get()` 拿到的 phy 没有顺序保证。**

**建失败只打 `dev_dbg`**（`:768-769` / `:881-882` / `:928-929`，三条消息字面相同：
`failed to create device link to %s\n`）⇒ **不算错误，流程继续。**

⚠️ **`DL_FLAG_STATELESS` 的含义**：**只影响 suspend/resume/shutdown 的顺序，
不影响 probe 顺序、不做自动 unbind 联动。**
**本文档没有跟进 `device_link_add` 的内部语义**（99 §5.3 已登记为推断）。

## §B6 可观测性：本框架最薄的一环

| 出口 | 有没有 | 说明 |
|---|---|---|
| **tracepoint** | **0** | **没有 `trace.h`，没有任何 `trace_*` 调用** |
| **debugfs** | **0** | — |
| **自定义 sysfs 属性** | **0** | **`DEVICE_ATTR` / `ATTRIBUTE_GROUPS` 都没有** |
| **`/sys/class/phy/`** | **有** | **唯一的静态观测面**：**能看到有哪些 phy、名字是什么、父设备是谁**（07 §2） |
| **dmesg** | **16 处打印** | **1 `pr_err` + 5 `dev_err` + 2 `dev_warn` + 1 `dev_WARN` + 3 `dev_WARN_ONCE` + 3 `dev_dbg` + 1 `dev_vdbg`**（07 §4） |

⇒ **诊断 phy 问题基本只能靠 dmesg + `/sys/class/phy/` + 读驱动源码。**
**和 regmap（17 tracepoint + 692 行 debugfs）差距极大**（07 §0）。

---

# Part C：评价与设计债

## §C1 源码自陈的设计债：**0 条**

**重跑 `grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' drivers/phy/phy-core.c drivers/phy/phy-core-mipi-dphy.c` 无输出。**

⇒ **框架作者没有留下任何"这里以后要改"的标记。**
**这不代表没有问题** ——**下面 §C2–§C4 的四类问题都是本文档自己发现的，源码里没有承认。**

## §C2 ⚠️ 官方文档与代码的四处不一致（本篇最有价值的一节）

**`Documentation/driver-api/phy/phy.rst` 是一份真正的概念文档（197 行），
但它落后于代码。四处，全部以代码为准**（99 §1.3）：

| # | 文档说 | 代码是 | 后果 |
|---|---|---|---|
| **1** | **`void phy_put(struct phy *phy);`（一个参数）** | **`void phy_put(struct device *dev, struct phy *phy);`（两个参数）** ——`include/linux/phy/phy.h:261` / `drivers/phy/phy-core.c:670` | **照文档写编译不过**（至少是显式失败） |
| ⚠️⚠️ **2** | **"All phy consumer calls on the NULL phy become NOPs"** | ⚠️ **`phy_configure` 和 `phy_validate` 对 NULL 返回 `-EINVAL`** ——`drivers/phy/phy-core.c:508-509` / `:543-544` | ⚠️ **静默的运行期失败** ——**可选 PHY 的板子上 `phy_configure()` 直接失败**（01 §4.3） |
| **3** | **`devm_of_phy_provider_register_full` 展开到 `__devm_of_phy_provider_register_full`** | **展开到 `__devm_of_phy_provider_register`**（`include/linux/phy/phy.h:205-206`）；**`_full` 后缀的底层函数全树不存在** | **只是文档笔误，宏本身能用** |
| **4** | **"while creating the PHY, pm_runtime_enable … is called"（无条件）** | **`if (pm_runtime_enabled(dev))` 有条件**（`drivers/phy/phy-core.c:991`） | **误以为 phy 一定有 PM runtime**（§A2.1） |

**另有一处 kerneldoc 误导（不算文档，算注释）**：
**`of_phy_simple_xlate()` 的 `@args: of_phandle_args (not used here)`（`drivers/phy/phy-core.c:700`），
但 `:716` 用了 `args->np`**（§A3.2）。

**文档还有一个格式瑕疵**：**`phy.rst` 里有一行裸编号标题 `4. Getting a reference to the PHY`**，
**不是 rst 标题语法** ⇒ **说明这份文档维护不勤，进一步支持"以代码为准"。**

## §C3 ⚠️ 三类静默失效（框架不报错，也没日志）

| # | 现象 | 机理 | 位置 |
|---|---|---|---|
| **1** | **`phy_set_mode()` 返回成功，但硬件没换模式** | **驱动没实现 `set_mode` ⇒ `ret` 保持 0 ⇒ 但 `attrs.mode` 照样被写** | `drivers/phy/phy-core.c:407-410` |
| **2** | **`init_count` / `power_count` 变成负数** | **不配对调用时，`if (count == 1)` 不成立但 `--count` 照样执行** | `:305`（exit）· `:388`（power_off） |
| **3** | **不配对的 `phy_power_off` 多做一次 `regulator_disable` 和 `phy_pm_runtime_put`** | **这两个动作在计数判断之外** | `:390` · `:392-393` |

⚠️ **第 2、3 类叠加的后果**：**regulator 的引用计数被打穿 ⇒
regulator 子系统会打自己的 `WARN`（"unbalanced disables"），但那条 warn 指向 regulator 而不是 phy。**
⇒ **诊断线索会指错子系统**（07 §5）。

**框架唯一给的提示是一条顺序警告**（`:258-259` 的 `phy_power_on was called before phy_init`）——
**它只检查"相对大小"，检查不出下溢。**

## §C4 三个可以合理质疑的设计选择

| # | 设计 | 质疑点 | 位置 |
|---|---|---|---|
| **1** | **一把 `phy_provider_mutex` 保护两个不相干的链表** | **DT 注册路径和非 DT 查表路径互相阻塞**（代价小，但概念上混淆） | `drivers/phy/phy-core.c:23-25` |
| **2** | **`of_phy_provider_lookup()` 依赖调用者持锁，没有 `lockdep_assert_held()`** | **隐式契约，未来加新调用者容易漏** | `:131-145` |
| **3** | ⚠️ **`_of_phy_get()` 把所有 DT 解析错误都变成 `-ENODEV`** | **"DT 写错"和"DT 没写"无法区分** ⇒ **配合 `phy_optional_get()` 会把真实的 DT 错误静默吞掉** | `:575-578` |

**第 3 条是"可选 PHY"这个特性的直接代价**（08 §3）——
**不是 bug，但它把一类配置错误变成了"什么都没发生"。**

**一处硬编码也值得记**：**`usb-nop-xceiv` 的 compatible 特例**（§A4.2）——
**全框架唯一一处按字符串判断的地方，属于历史兼容包袱。**

## §C5 整体评价

| 维度 | 评价 |
|---|---|
| **代码量** | **极小**（1408 行核心，1.4% 的目录占比）——**一天能读完** |
| **抽象层数** | **一层**（`phy_ops`），**框架不提供任何默认实现** |
| **可读性** | **好** ——**每个导出函数都短，逻辑直白，0 条设计债标记** |
| **文档** | **有真正的概念文档**（和 regmap 的 0 篇形成反差），⚠️ **但有四处过时**（§C2） |
| ⚠️ **可观测性** | ⚠️ **差** ——**0 tracepoint / 0 debugfs / 0 自定义 sysfs 属性**，**只有 16 处打印和一个 class 目录** |
| ⚠️ **错误处理的严格度** | ⚠️ **偏松** ——**大量"ops 没实现就返回成功"、计数无下溢保护、DT 错误码被归一** |

**本子系统最特殊的一点**：
**它把"薄"做到了极致 ——框架只做引用计数、互斥和 DT 解析，
其余全部交给驱动。**
⇒ **代价是"框架无法阻止驱动做错事"**：**`struct phy` 的 8 个字段全部公开可写、
`attrs` 三个字段没有硬件读回、`ops` 缺失时静默成功。**
⇒ **读 phy 的正确姿势是"读完 `phy-core.c` 之后必须去读你那颗芯片的驱动"**，
**因为真正决定行为的是驱动，不是框架。**

