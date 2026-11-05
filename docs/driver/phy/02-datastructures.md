---
title: 数据结构
---

# phy 02：数据结构

## §1.0 先分清六组关系

**图画得再全，也补不上一个没被显式否掉的错误类比。**
**本节六组，前两组是 phy 子系统最容易想错的地方。**

### §1.0.A ⚠️⚠️ 组 1：通用 PHY ≠ 网络 PHY（最高频的混淆）

**先否掉这个类比**：
> ❌ *"`drivers/phy/` 和 `drivers/net/phy/` 应该是同一个框架的两部分，
> 后者是前者在网络方向的特化吧？"*

**不是。它们没有任何代码关系，只是名字撞了。**

| 对比项 | **通用 PHY 框架**（本文档） | **网络 PHY / PHYLIB**（不是本文档） |
|---|---|---|
| 目录 | `drivers/phy/` | `drivers/net/phy/` |
| **头文件** | **`include/linux/phy/phy.h`**（带 `phy/`） | **`include/linux/phy.h`**（不带） |
| 核心实例结构 | **`struct phy`**（8 字段） | **`struct phy_device`** |
| 驱动侧 ops | **`struct phy_ops`**（12 字段） | **`struct phy_driver`** |
| **有 `bus_type` 吗** | **没有**（是 `class`，见 §1.0.C） | **有**（`mdio_bus_type`） |
| **有 `driver` 吗** | ⚠️ **没有！**（见 §1.0.C） | **有**（`phy_driver` 会绑定到 `phy_device`） |
| Kconfig | `GENERIC_PHY` | `PHYLIB` |
| sysfs | `/sys/class/phy/` | `/sys/class/mdio_bus/` + net device 下 |
| 官方文档 | `Documentation/driver-api/phy/phy.rst` | `Documentation/networking/phy.rst` |
| 管什么 | **USB / PCIe / SATA / UFS / MIPI / DP 的物理层初始化与上电** | **以太网 PHY 芯片的链路协商与寄存器访问** |

⚠️ **交叉点只有一个，而且很浅**：**`enum phy_mode` 里有一个 `PHY_MODE_ETHERNET`**
（`include/linux/phy/phy.h:41`）——**它表示"这条通用 PHY 当以太网串行链路用"（如 SerDes），
不是"这是一个网络 PHY 芯片"。**

**判断你在读哪一个：看 `#include`。**
**`<linux/phy/phy.h>` 是本文档；`<linux/phy.h>` 不是。**

### §1.0.B 组 2：`phy` 是实例，`phy_provider` 是实现者

**先否掉这个类比**：
> ❌ *"一个 PHY 驱动 probe 出来就是一个 `struct phy` 吧？"*

**不一定。一个驱动（provider）可以造出多个 `struct phy`。**

| | `struct phy_provider` | `struct phy` |
|---|---|---|
| 位置 | `include/linux/phy/phy.h:171-178` | `include/linux/phy/phy.h:152-161` |
| 字段数 | **5** | **8** |
| **是 `struct device` 吗** | ⚠️ **不是** ——**只持有一个 `struct device *dev` 指针**（`:172`） | **是** ——**第一个字段就是 `struct device dev`**（`:153`） |
| **谁创建** | **`of_phy_provider_register()`** | **`phy_create()`** |
| **一对多关系** | **1 个 provider** | **N 个 phy** |
| **怎么从 DT 参数找到具体实例** | **靠 `of_xlate` 回调**（`:176-177`） | — |
| 挂在哪 | **全局链表 `phy_provider_list`**（`drivers/phy/phy-core.c:24`） | **`phy_class`（不是链表）** |

**"为什么不会发生"也要有依据**：
**provider 不会被当成设备去 probe** ——**它没有 `struct device` 内嵌，
`__of_phy_provider_register()` 只是 `list_add_tail(&phy_provider->list, &phy_provider_list)`**
（`drivers/phy/phy-core.c:1130`）**，不调 `device_add`。**

### §1.0.C ⚠️ 组 3：phy 是 class 设备，不是总线设备，而且永远不绑 driver

**先否掉这个类比**：
> ❌ *"`struct phy` 内嵌了 `struct device`，那它应该会被匹配到某个 `struct device_driver` 上吧？"*

**不会。phy 设备永远处于"没有驱动"的状态。**

| 问题 | 答案 | 依据 |
|---|---|---|
| **`struct phy` 是 `struct device` 吗** | **是**（`include/linux/phy/phy.h:153`） | `#define to_phy(a) container_of((a), struct phy, dev)`（`:194`） |
| **注册到哪条总线** | ⚠️ **不注册到任何总线**；**只设 `class`** | `phy->dev.class = phy_class`（`drivers/phy/phy-core.c:967`），**全文没有 `dev.bus = ...`** |
| **会绑 driver 吗** | **不会** | **没有 `bus_type` 就没有 `match`/`probe` 流程**；**全树 grep `struct bus_type` 在 `drivers/phy/` 命中 0**（99 §1.2） |
| **父设备是谁** | **provider 的设备** | `phy->dev.parent = dev`（`drivers/phy/phy-core.c:968`） |
| **设备名叫什么** | **`phy-<provider设备名>.<id>`** | `dev_set_name(&phy->dev, "phy-%s.%d", dev_name(dev), id)`（`drivers/phy/phy-core.c:973`） |
| **id 从哪来** | **全局 IDA** | `ida_simple_get(&phy_ida, 0, 0, GFP_KERNEL)`（`:957`） |

⇒ **`phy` 只借用 `struct device` 的三件事**：**引用计数（`get_device`/`put_device`）、
sysfs 里的一个位置、以及 PM runtime 的父子传导。**
**它不参与驱动匹配。**

### §1.0.D 组 4：`init_count` 和 `power_count` 是两个独立的计数

**先否掉这个类比**：
> ❌ *"`phy_init` 和 `phy_power_on` 是同一件事的两步，用一个计数就够了吧？"*

**不是。它们各有一个计数，而且可以不配对。**

| | `init_count` | `power_count` |
|---|---|---|
| 位置 | `include/linux/phy/phy.h:157` | `include/linux/phy/phy.h:158` |
| 谁加 | **`phy_init()`**（`drivers/phy/phy-core.c:268`） | **`phy_power_on()`**（`:349`） |
| 谁减 | `phy_exit()`（`:305`） | `phy_power_off()`（`:388`） |
| **回调只在什么时候调** | **`init_count == 0` 才调 `ops->init`**（`:261`）；**`init_count == 1` 才调 `ops->exit`**（`:298`） | **`power_count == 0` 才调 `ops->power_on`**（`:342`）；**`== 1` 才调 `ops->power_off`**（`:380`） |

⚠️ **框架会检查两者的相对关系，但只是警告**：

```c
/* drivers/phy/phy-core.c:258-259 —— phy_init 里 */
	if (phy->power_count > phy->init_count)
		dev_warn(&phy->dev, "phy_power_on was called before phy_init\n");
```

⇒ **正确顺序是 `phy_init` → `phy_power_on` → … → `phy_power_off` → `phy_exit`。**
**顺序错了只有一条 `dev_warn`，不会失败**（07 §3）。

⚠️⚠️ **两个计数都没有下溢保护**：
**`phy_power_off()` 在 `power_count` 已经是 0 时，`if (power_count == 1)` 不成立 ⇒ 不调 `ops->power_off`
⇒ 但仍然执行 `--phy->power_count`（`drivers/phy/phy-core.c:388`）⇒ 计数变成 -1。**
**同一路径还会多做一次 `phy_pm_runtime_put`（`:390`）和一次 `regulator_disable`（`:392-393`）。**
⇒ **不配对的调用会静默把计数和 PM/regulator 引用计数一起搞坏**（05 §7 / 07 §4）。
**`phy_exit()` 同理**（`:305`）。

### §1.0.E ⚠️ 组 5：`phy->pwr` 和 PM runtime 是两套电源，不是一套

**先否掉这个类比**：
> ❌ *"`phy_power_on()` 就是把 PHY 的电源打开，一件事一条路径。"*

**`phy_power_on()` 实际上动了三样东西，顺序还很讲究。**

| 层 | 是什么 | 在 `phy_power_on()` 里的位置 |
|---|---|---|
| **① regulator** | **`phy->pwr`（`struct regulator *`，`include/linux/phy/phy.h:160`）** | **最先开**：`regulator_enable(phy->pwr)`（`drivers/phy/phy-core.c:329-333`） |
| **② PM runtime** | **phy 设备的 runtime 状态**（会传导到 provider） | **第二步**：`phy_pm_runtime_get_sync(phy)`（`:335`） |
| **③ 驱动的 `power_on` 回调** | **真正碰 PHY 寄存器** | **最后**：`phy->ops->power_on(phy)`（`:343`） |

**`phy->pwr` 从哪来**：
```c
/* drivers/phy/phy-core.c:978-985 —— 在 phy_create() 里，用 "phy" 这个名字取 */
	phy->pwr = regulator_get_optional(&phy->dev, "phy");
	if (IS_ERR(phy->pwr)) {
		ret = PTR_ERR(phy->pwr);
		if (ret == -EPROBE_DEFER)
			goto put_dev;

		phy->pwr = NULL;	/* 取不到就当没有，不算失败 */
	}
```
⇒ **对应的 DT 属性是 `phy-supply`**（06 §1）。
⇒ **驱动完全不用管这个 regulator，框架替它开关**（05 §2 / 08 §6）。

⚠️ **`phy_power_off()` 的顺序不是 `power_on` 的严格倒序**：
**`ops->power_off`（`:381`）→ `phy_pm_runtime_put`（`:390`）→ `regulator_disable`（`:392-393`）**
**确实是倒序的，但 `put` 用的是不带 `_sync` 的版本**（01 §7.3）。

### §1.0.F 组 6：本子系统有几层可插拔 —— **只有一层**

| 层 | ops 结构 | 实现者目录 | 实现数 |
|---|---|---|---|
| **唯一一层** | **`struct phy_ops`**（12 字段，`include/linux/phy/phy.h:84-127`） | **`drivers/phy/<厂商>/` 25 个子目录 + 5 个顶层驱动** | **约 200 个 `.c` 文件** |

**框架自带的唯一"默认实现"是 `of_phy_simple_xlate()`**（`drivers/phy/phy-core.c:726` 处导出），
**而它属于 `phy_provider.of_xlate` 而不是 `phy_ops`。**

**对照（说明这个"一层"有多薄）**：

| 子系统 | 可插拔层数 | 核心行数 |
|---|---|---|
| **phy** | **1** | **1244 + 164 = 1408** |
| regmap | **3**（总线 / 缓存 / 锁） | 10998 |
| virtio | **2**（传输 ops / 设备驱动 ops） | — |

---

## §2 核心抽象与三方的关系

**phy 没有自己的总线（§1.0.C）**，所以这里画「**consumer ↔ 核心 ↔ provider**」：

```
  DT:  usb@ff100000 {                       usbphy: phy@ff200000 {
         phys = <&usbphy 0>;                  #phy-cells = <1>;
         phy-names = "usb2-phy";              phy-supply = <&vcc_usb>;
       };                                   };
          │                                        │
          │ ①platform 总线 probe                   │ ①platform 总线 probe
          ▼                                        ▼
  ┌──────────────────┐                    ┌────────────────────────┐
  │ 控制器驱动        │                    │ PHY 驱动（provider）    │
  │ (consumer)       │                    │                        │
  └──────────────────┘                    └────────────────────────┘
          │                                        │
          │ ②devm_phy_get(dev,                     │ ②devm_phy_create(dev, node, ops)
          │   "usb2-phy")                          │   ⇒ 造出 struct phy（是个 device）
          │                                        │   ③devm_of_phy_provider_register(dev, xlate)
          │                                        │   ⇒ 挂进 phy_provider_list
          ▼                                        ▼
  ┌─────────────────────────── phy-core.c ──────────────────────────┐
  │                                                                  │
  │  phy_provider_list  ←── 全局链表，phy_provider_mutex 保护         │
  │        │                                                         │
  │        │ of_phy_provider_lookup(args.np)     靠 DT 节点匹配     │
  │        ▼                                                         │
  │  phy_provider->of_xlate(dev, &args)  把 <&usbphy 0> 的 0 翻译   │
  │        │                                                         │
  │        ▼                                                         │
  │  struct phy  ←── phy->dev.class = phy_class（不是 bus）        │
  │                  phy->dev.parent = provider 的 device            │
  │                  设备名 = "phy-<provider名>.<id>"                │
  └──────────────────────────────────────────────────────────────────┘
          │
          │ ④device_link_add(consumer, &phy->dev, DL_FLAG_STATELESS)
          ▼
      电源/关机顺序被固定下来
```

**四条边要记住**：
1. **两侧都是 platform 设备**（或别的总线上的设备）——**phy 框架不负责枚举它们。**
2. **provider 与 phy 实例是父子设备关系**（`dev.parent`）。
3. **consumer 与 phy 实例是 device link 关系**（不是父子）。
4. **consumer 找到 provider 的唯一途径是 DT 节点匹配**（非 DT 时走 lookup 表，01 §8）。

---

## §3 核心操作数据流

**框架型子系统的"核心操作"是"消费者 → ops 回调"。**
**以最常用的 `phy_power_on()` 为例，画到最终执行点**：

```
consumer: phy_power_on(phy)                       drivers/phy/phy-core.c:322
  │
  ├─ if (!phy) goto out; ─────────────────────────► return 0   NULL 是合法的（:326-327）
  │
  ├─ ① regulator_enable(phy->pwr)                 :329-333
  │      └─ 失败 ⇒ 直接返回，后面全不做
  │
  ├─ ② phy_pm_runtime_get_sync(phy)               :335
  │      ├─ 唤醒 phy 设备 ⇒ 连带唤醒 provider（父子传导）
  │      └─ -ENOTSUPP 被改写成 0                  :336-339
  │
  ├─ ③ mutex_lock(&phy->mutex)                    :341   per-phy 的锁
  │
  ├─ ④ if (power_count == 0 && ops->power_on)     :342
  │      └─ ops->power_on(phy) ──────────────────► 驱动真正碰硬件寄存器
  │             失败 ⇒ err_pwr_on:                :346
  │                     unlock + put_sync + regulator_disable  :353-358
  │
  ├─ ⑤ ++power_count                              :349
  └─ ⑥ mutex_unlock                               :350
```

⚠️ **三个必须记住的点**：
1. **`ops->power_on` 只在计数 0→1 时调**（`:342`）——**第二个消费者调 `phy_power_on` 什么硬件动作都没有。**
2. **错误路径把三样东西都回滚了**（`:353-358`），**但 `power_count` 没有被加，所以不需要回滚它。**
3. **锁的粒度是"一个 phy 实例一把 mutex"**（`include/linux/phy/phy.h:156`）——**不是全局锁**（§6）。

**取用路径（`devm_phy_get`）的数据流**：

```
consumer: devm_phy_get(dev, "usb2-phy")
  │
  ├─ phy_get(dev, string)                         drivers/phy/phy-core.c:738
  │   ├─ dev->of_node 非空？
  │   │   ├─ 是 ⇒ of_property_match_string(of_node, "phy-names", string)  :746-747
  │   │   │        ⇒ 得到 index
  │   │   │        ⇒ _of_phy_get(of_node, index)                          :750
  │   │   │             ├─ of_parse_phandle_with_args(np, "phys", "#phy-cells", index, &args)  :575
  │   │   │             │     失败 ⇒ -ENODEV（:577-578）
  │   │   │             ├─ compatible == "usb-nop-xceiv" ⇒ -ENODEV     :581-584
  │   │   │             ├─ mutex_lock(&phy_provider_mutex)                :586
  │   │   │             ├─ of_phy_provider_lookup(args.np)                :587
  │   │   │             │     找不到 / try_module_get 失败 ⇒ -EPROBE_DEFER :588-589
  │   │   │             ├─ !of_device_is_available(args.np)
  │   │   │             │     ⇒ dev_warn "Requested PHY is disabled" + -ENODEV  :593-596
  │   │   │             └─ phy_provider->of_xlate(provider->dev, &args)    :599
  │   │   └─ 否 ⇒ phy_find(dev, string) 查 lookup 表                      :756
  │   │              找不到 ⇒ -ENODEV（:128）
  │   ├─ try_module_get(phy->ops->owner) 失败 ⇒ -EPROBE_DEFER              :761-762
  │   ├─ get_device(&phy->dev)                                            :764
  │   └─ device_link_add(dev, &phy->dev, DL_FLAG_STATELESS)                :766
  └─ devres_add(...)  ⇒ 卸载时自动 phy_put
```

⚠️ **`-ENODEV` 有四个来源，而 `phy_optional_get()` 会把它们全部转成 NULL**：
**`phys` 属性缺失 · `usb-nop-xceiv` 特例 · 节点 `status = "disabled"` · lookup 表没命中。**
⇒ **"可选 PHY"能工作，全靠这四条路都收敛到同一个码**（01 §4.1 / 08 §3）。

---

## §4 命名约定速查

| 前缀 / 形态 | 含义 | 例子 |
|---|---|---|
| `phy_*` | **消费者用的 API** | `phy_get` `phy_init` `phy_power_on` |
| `devm_phy_*` | **同上，自动释放版** | `devm_phy_get` `devm_phy_create` |
| `of_phy_*` | **走 DT 的变体** | `of_phy_get` `of_phy_simple_xlate` |
| `devm_of_phy_*` | **DT + 自动释放** | `devm_of_phy_get` `devm_of_phy_get_by_index` |
| `__*` 前缀 | **宏展开后的真实实体，不要直接调** | **`__of_phy_provider_register` / `__devm_of_phy_provider_register`（只有这两个）** |
| `_*` 单下划线前缀 | **文件内部静态函数** | **`_of_phy_get`（`drivers/phy/phy-core.c:568`）** |
| `*_ext` 后缀 | **多一个 `submode` 参数的完整版** | **`phy_set_mode_ext`（不带 `_ext` 的 `phy_set_mode` 是宏）** |
| `*_full` 后缀 | **多一个 `children` 参数的完整版** | **`of_phy_provider_register_full`（展开到同一个 `__` 实体，01 §3.2）** |
| `*_optional_*` | **"没有"不算错，返回 NULL** | **`phy_optional_get` / `devm_phy_optional_get`** |

⚠️ **三个名字陷阱**：

| 陷阱 | 说明 |
|---|---|
| ⚠️ **全局链表 `phys` 装的不是 `struct phy`** | **它装的是 `struct phy_lookup`**（`drivers/phy/phy-core.c:25` 定义，`:80` 插入 `pl->node`）⇒ **名字会让人以为是"所有 phy 的列表"，其实是"非 DT 查找表"** |
| ⚠️ **`-ENOTSUPP` 与 `-EOPNOTSUPP` 语义不同** | **前者来自 PM runtime 且被吞掉；后者是"驱动没实现 ops"**（01 §9.1） |
| ⚠️ **`phy_put` 与 `of_phy_put` 参数个数不同** | **`phy_put(dev, phy)` 两个，`of_phy_put(phy)` 一个**（01 §4.2） |

---

## §5 结构体逐个看

### §5.1 `struct phy`：只有 8 个字段（本子系统最"瘦"的地方）

```c
/* include/linux/phy/phy.h:152-161 */
struct phy {
	struct device		dev;		/* 内嵌 device，class = phy_class */
	int			id;		/* 来自全局 phy_ida */
	const struct phy_ops	*ops;		/* 唯一一层可插拔 */
	struct mutex		mutex;		/* per-phy 锁，保护下面三样 */
	int			init_count;	/* §1.0.D */
	int			power_count;	/* §1.0.D */
	struct phy_attrs	attrs;		/* 纯软状态，见 §5.2 */
	struct regulator	*pwr;		/* §1.0.E，可为 NULL */
};
```

⚠️ **对比 regmap 的私有 `struct regmap`（几十个字段、藏在 `internal.h` 里）**：
**`struct phy` 是公开的、8 个字段、任何驱动都能直接读写。**
⇒ **这是"薄框架"的直接体现**，**也意味着框架无法阻止驱动乱改这些字段。**

### §5.2 `struct phy_attrs`：三个字段，全是软状态

```c
/* include/linux/phy/phy.h:135-139 */
struct phy_attrs {
	u32			bus_width;
	u32			max_link_rate;
	enum phy_mode		mode;
};
```

| 字段 | 谁写 | ⚠️ 有硬件读回吗 |
|---|---|---|
| `bus_width` | **驱动自己 `phy_set_bus_width()`**（`include/linux/phy/phy.h:248`，`static inline` 纯赋值） | **没有** |
| `max_link_rate` | **驱动直接赋值**（框架没有 setter） | **没有** |
| `mode` | **`phy_set_mode_ext()` 在成功后写**（`drivers/phy/phy-core.c:409-410`） | ⚠️ **没有，而且驱动没实现 `set_mode` 时它也会被写**（01 §5.1 的静默陷阱） |

⇒ **这三个字段是"消费者与驱动之间传消息的黑板"**，**不是硬件状态的镜像。**

### §5.3 `struct phy_ops`：12 个字段

**完整表在 01 §2**，这里只记结构上的两点：
1. **11 个函数指针 + 1 个 `struct module *owner`** ——**`owner` 不是回调，但缺了会让 `phy_get()` 返回 `-EPROBE_DEFER`。**
2. **只有 `release` 是 `void` 返回**（`include/linux/phy/phy.h:125`）——**其余 10 个都返回 `int`。**

### §5.4 `struct phy_provider`：5 个字段

```c
/* include/linux/phy/phy.h:171-178 */
struct phy_provider {
	struct device		*dev;		/* 指针，不是内嵌 */
	struct device_node	*children;	/* ⚠️ 永远不为 NULL，见下方 */
	struct module		*owner;		/* 宏里的 THIS_MODULE */
	struct list_head	list;		/* 挂进 phy_provider_list */
	struct phy * (*of_xlate)(struct device *dev,
		struct of_phandle_args *args);	/* 唯一的回调 */
};
```

⚠️ **`children` 字段的用法很特殊**：
**`of_phy_provider_lookup()` 会同时试"节点 == provider->dev->of_node"和
"节点是 provider->children 的子节点"两条**（`drivers/phy/phy-core.c:137-144`）
⇒ **这就是"DT 里 PHY 子节点被多包一层"时 `_full` 版能工作的原因。**

⚠️ **`children` 字段永远不是 NULL**（这一点容易想错）：
**注册宏的普通版传的是 NULL，但 `__of_phy_provider_register()` 会把它替换掉**：

```c
/* drivers/phy/phy-core.c:1116-1118 */
	} else {
		children = dev->of_node;	/* 没传就用 provider 自己的节点 */
	}
```
然后 `phy_provider->children = of_node_get(children)`（`:1125`）。
⇒ **普通版的效果是"遍历 provider 节点的直接子节点"，不是"跳过遍历"** ——
**这恰好就是"PHY 子节点直接挂在 provider 下"这种最常见 DT 布局需要的行为。**

⚠️ **`_full` 版还多一道校验**（`drivers/phy/phy-core.c:1100-1113`，源码注释原话：
*"If specified, the device node containing the children must itself be the provider's device node
or a child (or further descendant) thereof."*）：
**沿 `of_get_parent()` 一路上溯，走不到 `dev->of_node` 就返回 `-EINVAL`（`:1112-1113`）。**
⇒ **传一个不相干的节点会被拒，不会静默生效。**

### §5.5 `struct phy_lookup`：4 个字段（非 DT 路径专用）

```c
/* include/linux/phy/phy.h:187-192 */
struct phy_lookup {
	struct list_head node;
	const char *dev_id;	/* 消费者的 dev_name() */
	const char *con_id;	/* 连接名 */
	struct phy *phy;
};
```

⚠️ **`dev_id` / `con_id` 是裸 `const char *`，框架不复制字符串**
（`drivers/phy/phy-core.c:75-77` 只是赋值指针）
⇒ **调用者必须保证这两个字符串的生命周期长于 lookup 项。**

⚠️ **比较用的是 `strcmp` 而不是指针比较**（`drivers/phy/phy-core.c:122`）
⇒ **传字面量或动态串都可以，但不能是栈上的临时缓冲。**

### §5.6 `union phy_configure_opts`：3 个成员

**完整表在 01 §6.1。** **结构上唯一要说的**：
⚠️ **它没有任何 tag / type 字段**（`include/linux/phy/phy.h:64-68`）
⇒ **"这个 union 现在是哪一种"完全靠调用者与驱动的约定**，**框架不检查也无法检查。**

---

## §6 全局变量与锁

**`drivers/phy/phy-core.c` 只有 5 个文件级静态变量**（`:22-26`）：

| 变量 | 类型 | 位置 | 保护它的锁 | 用途 |
|---|---|---|---|---|
| `phy_class` | `struct class *` | `:22` | **无需**（只在 init/exit 赋值） | **`/sys/class/phy/`** |
| `phy_provider_mutex` | `DEFINE_MUTEX` | `:23` | — | **同时保护下面两个链表** |
| `phy_provider_list` | `LIST_HEAD` | `:24` | **`phy_provider_mutex`** | **所有已注册的 provider** |
| ⚠️`phys` | `LIST_HEAD` | `:25` | **同一个 `phy_provider_mutex`** | ⚠️ **非 DT lookup 表**（不是 phy 列表，§4） |
| `phy_ida` | `DEFINE_IDA` | `:26` | **IDA 自带锁** | **分配 `phy->id`** |

**加上 per-phy 的 `phy->mutex`（`include/linux/phy/phy.h:156`），全框架一共两种锁。**

### §6.1 ⚠️ 一把 mutex 保护两个不相关的链表

| 链表 | 装什么 | 谁访问 |
|---|---|---|
| `phy_provider_list` | **`struct phy_provider`** | **`__of_phy_provider_register`（`:1130` 插入）· `of_phy_provider_lookup`（`:136` 遍历）** |
| `phys` | **`struct phy_lookup`** | **`phy_create_lookup`（`:80`）· `phy_remove_lookup`（`:104`）· `phy_find`（`:121`）** |

⇒ **两条互不相干的路径（DT 注册 vs 非 DT 查表）争同一把锁。**
**代价很小（都是短临界区），但这是一个可以合理质疑的设计选择**（03 Part C）。

⚠️ **`of_phy_provider_lookup()` 自己不加锁**（`drivers/phy/phy-core.c:131-145` 全文没有 `mutex_lock`）
⇒ **调用者必须已持锁** ——**`_of_phy_get()` 在 `:586` 先加锁再调它**。
**这是一个"隐式锁契约"，源码里没有注释说明**（03 Part C 的评价项）。

### §6.2 `phy->mutex` 保护什么

| 被保护的 | 依据 |
|---|---|
| `init_count` / `power_count` | **每个增减都在临界区内**（`drivers/phy/phy-core.c:257-271` / `:341-350`） |
| **`ops` 回调的串行化** | **`ops->init`/`power_on`/`set_mode`/`configure`/`validate`/`reset`/`calibrate` 全在锁内调** |
| `attrs.mode` | `:409-410` 在锁内 |

⚠️ **两个不在锁内的地方**：
1. **`regulator_enable(phy->pwr)` 在加锁之前**（`:329-333` vs `:341`）
2. **`phy_pm_runtime_get_sync()` 也在加锁之前**（`:335`）——**刻意的：PM runtime 会睡，且自己有锁。**

⚠️ **`ops` 回调在 mutex 内被调用 ⇒ 驱动的回调可以睡眠，但不能在原子上下文里调 phy API。**
**全框架没有 spinlock，也没有任何 `_irqsave` 变体**
（重跑 `grep -c 'spin_lock\|irqsave' drivers/phy/phy-core.c` ⇒ 0）
⇒ **phy API 一律不能在中断上下文里调**（05 §7 / 07 §4）。

---

## §7 结构关系总图

```
                    ┌──────────────────────┐
                    │  phy_provider_list   │ ← 全局，phy_provider_mutex
                    └──────────┬───────────┘
                               │ list
                    ┌──────────▼────────────────────────────┐
                    │ struct phy_provider        （5 字段）  │
                    │  dev ────────────► provider 的 device │
                    │  children ───────► DT 子节点容器（可 NULL）│
                    │  owner ──────────► THIS_MODULE         │
                    │  of_xlate() ─────► 参数 → 实例         │
                    └──────────┬────────────────────────────┘
                               │ of_xlate 返回
                    ┌──────────▼────────────────────────────┐
                    │ struct phy                 （8 字段）  │
                    │  dev ─┬─ class  = phy_class            │
                    │       ├─ parent = provider 的 device   │
                    │       └─ name   = "phy-<父名>.<id>"    │
                    │  id ────────────► phy_ida              │
                    │  ops ───────────► struct phy_ops（12）  │──► 驱动实现
                    │  mutex ─────────► per-phy 锁            │
                    │  init_count  ┐                          │
                    │  power_count ┘ 两个独立计数（§1.0.D）    │
                    │  attrs ─────────► struct phy_attrs（3） │ 纯软状态
                    │  pwr ───────────► struct regulator *    │──► regulator 子系统
                    └──────────▲────────────────────────────┘
                               │ device_link_add(DL_FLAG_STATELESS)
                    ┌──────────┴───────────┐
                    │  consumer 的 device  │
                    └──────────────────────┘

  另一条独立的路（非 DT）：
                    ┌──────────────────────┐
                    │  phys（名字误导）   │ ← 全局，同一把 phy_provider_mutex
                    └──────────┬───────────┘
                               │ node
                    ┌──────────▼────────────────────────────┐
                    │ struct phy_lookup          （4 字段）  │
                    │  dev_id / con_id ─► 裸字符串指针，不复制 │
                    │  phy ────────────► struct phy          │
                    └────────────────────────────────────────┘
```

## §8 阅读下一篇

| 想知道 | 去 |
|---|---|
| API 完整清单 | `01-api.md` |
| 注册 / 取用 / 上电的逐段剖析 | `03-internals.md` |
| 代码骨架 | `05-usage.md` |
| DTS 属性（`phys` / `#phy-cells` / `phy-supply`） | `06-devicetree.md` |
| 出问题看什么 | `07-debug.md` |
| 为什么两个计数、为什么 NULL 合法 | `08-feynman.md` |
