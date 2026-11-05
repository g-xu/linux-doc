---
title: 数据结构
---

# pinctrl 核心数据结构

> 本篇讲**结构体长什么样、谁指向谁、谁持有谁的生命周期**。
> 找 API 去 `01-api.md`；看流程怎么跑去 `03-internals.md`；看怎么写代码去 `05-usage.md`。
> 组织轴：**先破歧义（§1），再画图（§2/§4/§5），最后逐个结构体展开（§3）**。

---

## §0 本篇导航

| 你想知道 | 去 |
|---|---|
| pinctrl 里哪些名字容易被理解错 | §1（**先读这节**） |
| 核心抽象和消费者是怎么连起来的 | §2 |
| 某个结构体每个字段是什么意思 | §3 |
| 一张把所有结构体串起来的总图 | §4 |
| 一次 `pinctrl_select_state()` 数据是怎么流的 | §5 |
| 看到一个名字怎么判断它属于哪一层 | §6 |
| 有哪些全局变量、几把锁、锁什么 | §7 |
| 谁负责 free 谁 | §8 |

**pinctrl 的结构体命名是全内核最容易误读的之一**。原因很具体：`struct pinctrl` 这个"看起来最像子系统主角"的名字，实际上是**消费者侧的一个临时句柄**，而真正的 pin controller 叫 `struct pinctrl_dev`。所以 §1 不是可选的暖场，是必读的排雷。

---

## §1 先分清七组关系

### §1.1 `struct pinctrl` 不是 pin controller

**错误类比**：「`struct pinctrl` 是 pin 控制器，就像 `struct i2c_adapter` 是 I2C 控制器。」

**为什么错**：`struct pinctrl`（`drivers/pinctrl/core.h:77-84`）是**消费者调用 `pinctrl_get(dev)` 拿到的句柄**，一个消费者设备一个，里面装的是「我这个设备有哪些状态可选、当前选中哪个」。它甚至不知道自己的引脚分布在几个控制器上。

真正的 pin controller 是 `struct pinctrl_dev`（`drivers/pinctrl/core.h:42-65`）。

| 判据 | `struct pinctrl` | `struct pinctrl_dev` |
|---|---|---|
| 代表谁 | **消费者**（UART / I2C / MMC 控制器等要用引脚的设备） | **提供者**（SoC 的 pin controller 硬件） |
| 谁创建 | 核心，在消费者第一次 `pinctrl_get()` 时 | 核心，在 pin controller 驱动 `pinctrl_register*()` 时 |
| 数量 | 每个消费者设备 ≤1 个（`dev->pins->p` 缓存） | 一个 SoC 通常 1~4 个 |
| 是不是 `struct device` | **不是**。只有一个 `struct device *dev` 指回消费者 | **也不是**。只有一个 `struct device *dev` 指回 platform 设备 |
| 挂在哪个全局链表 | `pinctrl_list`（`drivers/pinctrl/core.c:55`） | `pinctrldev_list`（`drivers/pinctrl/core.c:52`） |
| 引用计数 | 有，`struct kref users`（`core.h:83`） | 没有 kref。用 `try_module_get(pctldev->owner)` 保护模块不被卸载 |
| 生命周期 | 消费者 probe 到 remove | 控制器 probe 到 remove |

**命名后果**：`pinctrl_get()` / `pinctrl_put()` 操作的是**消费者句柄**；`pinctrl_register()` / `pinctrl_unregister()` 操作的是**控制器**。两组函数名字很近但作用在不同对象上，是本子系统最常见的读错点。

### §1.2 pinctrl 不是一条总线

**错误类比**：「pin controller 注册进 pinctrl 总线，然后跟 pinctrl driver 匹配。」

**为什么错**：pinctrl **没有 `struct bus_type`**。判据可以自己跑：

```bash
grep -rn 'struct bus_type' drivers/pinctrl/ include/linux/pinctrl/
# 空
```

pin controller 驱动本身是**普通的 platform 驱动**（arm64 上几乎全部如此），走 platform 总线的 device↔driver 匹配。`pinctrl_register*()` 只是"把自己登记到一个全局链表里，好让别人按名字/`device_node` 找到我"，不产生任何新的 device↔driver 绑定。

因此：

| 一般子系统有的东西 | pinctrl 有没有 |
|---|---|
| `struct bus_type` | 无 |
| `struct <subsys>_driver` | 无。pin controller 驱动就是 `struct platform_driver` |
| `.match()` / `.probe()` 转发 | 无 |
| `/sys/bus/<subsys>/` | 无。pinctrl 在 sysfs 里**完全不可见**，只有 debugfs |
| `MODULE_DEVICE_TABLE` 匹配表 | 有，但是 platform/OF 的 `of_device_id`，不是 pinctrl 自己的 |

**"为什么不会发生"的代码依据**：`pinctrl_init_controller()`（`drivers/pinctrl/core.c:1994` 起）里没有任何 `device_register()` / `bus_register()` 调用，全部动作是初始化三棵 radix tree、两个链表、一把 mutex，然后在 `pinctrl_register_and_init()` 里 `list_add_tail(&pctldev->node, &pinctrldev_list)`。

**那消费者是怎么"找到"控制器的？** 两条路，都是**按名字查链表**而不是设备模型匹配：

```
① DT 路径（arm64 常态）：
   消费者 DT 里的 pinctrl-0 = <&phandle>
     → devicetree.c 解析出 phandle 指向的 device_node
       → get_pinctrl_dev_from_of_node()   遍历 pinctrldev_list 比对 of_node
② 板级 mapping 表路径（arm64 上基本不用）：
   pinctrl_map.ctrl_dev_name 是一个字符串
     → get_pinctrl_dev_from_devname()     遍历 pinctrldev_list 比对 dev_name()
```

两条路径都会在"找不到"时返回 `-EPROBE_DEFER`——这就是 pinctrl 版的"延迟匹配"，语义上等价于设备模型的 deferred probe，但实现完全独立。

### §1.3 `struct pin_desc` 不是 `struct pinctrl_pin_desc`

这一组的危险在于**名字只差一个前缀，但归属层完全不同**：

| | `struct pinctrl_pin_desc` | `struct pin_desc` |
|---|---|---|
| 定义在 | **公开**头 `include/linux/pinctrl/pinctrl.h:56-60` | **私有**头 `drivers/pinctrl/core.h:159-171` |
| 谁填 | **pin controller 驱动**，写成一个 `static const` 数组塞进 `pinctrl_desc.pins` | **核心**，在 `pinctrl_register_one_pin()` 里 `kzalloc` 出来 |
| 装什么 | 静态声明：`number` / `name` / `drv_data` | 运行时状态：`mux_usecount` / `mux_owner` / `gpio_owner` / `mux_setting` |
| 存在哪 | 驱动的 `.rodata` | 核心的 `pctldev->pin_desc_tree` radix tree，key 是 pin 号 |
| 驱动能不能碰 | 能，是自己写的 | **不能**，头文件都拿不到 |
| 数量 | npins 个 | npins 个（一一对应） |

**`struct pin_desc` 的四个所有权字段全部在 `#ifdef CONFIG_PINMUX` 里**（`core.h:164-169`）。`!CONFIG_PINMUX` 时结构体直接少四个字段——这就是为什么 `pinmux.h` 私有头里要准备一整套返回 0 的 stub（`drivers/pinctrl/pinmux.h:37-83`）。

### §1.4 group 和 function 各有两套类型

**这不是重复定义，是两个不同用途**：

| 用途 | group 侧 | function 侧 | 定义在 |
|---|---|---|---|
| **驱动写字面量表用的** | `struct pingroup`（`include/linux/pinctrl/pinctrl.h:35-39`，配宏 `PINCTRL_PINGROUP`:42） | `struct pinfunction`（`pinctrl.h:215-219`，配宏 `PINCTRL_PINFUNCTION`:222） | 公开头 |
| **泛型存储的表项** | `struct group_desc`（`drivers/pinctrl/core.h:194-199`） | `struct function_desc`（`drivers/pinctrl/pinmux.h:130-135`） | 私有头 |

关键差异：

- **`pingroup`/`pinfunction` 只是"数组元素"**。驱动自己声明、自己实现 `get_groups_count()` 等回调去遍历它。核心从不认识这两个类型
- **`group_desc`/`function_desc` 是泛型存储的节点**。驱动调 `pinctrl_generic_add_group()` / `pinmux_generic_add_function()` 把它塞进 radix tree，然后**回调直接用核心提供的现成实现**（`pinctrl_generic_get_group_count` 等）
- 两者**不能互相转换**。`group_desc` 多一个 `void *data` 给驱动挂私有信息（如寄存器偏移、mux 值）

**泛型存储是可选的，靠两个独立 Kconfig 开关**：`CONFIG_GENERIC_PINCTRL_GROUPS`（`group_desc` 和 `pctldev->pin_group_tree` 都在这个 `#ifdef` 里）、`CONFIG_GENERIC_PINMUX_FUNCTIONS`（`function_desc` 和 `pin_function_tree`）。这两个开关**互不相干**：可以只开一个，此时 `pinctrl_dev` 里只多一棵树。

### §1.5 `pinctrl_map` 不是 `pinctrl_setting`

一句话区别：**`pinctrl_map` 是"配方"，`pinctrl_setting` 是"已下单的菜"**。

```
静态描述（可能来自 DT，也可能来自板级 C 数组）
  struct pinctrl_map { dev_name, name, type, ctrl_dev_name, data }
        │  字符串 → selector 的解析发生在这里
        │  pinmux_map_to_setting() / pinconf_map_to_setting()
        ▼
运行时对象（每个消费者句柄自己一份）
  struct pinctrl_setting { node, type, pctldev, dev_name, union{mux, configs} }
```

| | `struct pinctrl_map` | `struct pinctrl_setting` |
|---|---|---|
| 定义在 | **公开** `include/linux/pinctrl/machine.h:67-76` | **私有** `drivers/pinctrl/core.h:131-140` |
| 里面装 | **字符串**（`"uart0_grp"` / `"uart0"` / 控制器名） | **数字 selector**（`unsigned group` / `unsigned func`） |
| 谁产生 | 板级代码写死，或 `devicetree.c` 从 DT 解析出来 | 核心在 `pinctrl_get()` 走 mapping 表时逐条建 |
| 挂在哪 | 全局 `pinctrl_maps` 链表（经 `struct pinctrl_maps` 包一层） | `pinctrl_state->settings` 链表 |
| 生命周期 | 板级表是 `.rodata` 永久；DT 生成的随消费者句柄释放 | 随消费者句柄释放 |
| 数量 | 一条 map = "某设备某状态里的一个动作" | 一条 map 恰好对应一条 setting |

**字符串→selector 的转换只发生一次**，就在 `pinctrl_get()` 阶段。所以后续每次 `pinctrl_select_state()` 都不再查名字，直接用数字调回调——**这是 pinctrl 把"慢的事"前移到 probe 阶段的关键设计**。相应的代价：DTS 里写错组名/function 名，报错时机是消费者 probe，而不是状态切换。

### §1.6 三套 ops 属于同一个控制器，不是三个设备

`pinctrl_desc` 里有三个 ops 指针（`include/linux/pinctrl/pinctrl.h:154-156`）：

```c
const struct pinctrl_ops  *pctlops;   /* 引脚/组的枚举与 DT 解析 */
const struct pinmux_ops   *pmxops;    /* 复用：谁占用、切到哪个功能 */
const struct pinconf_ops  *confops;   /* 电气配置：上下拉、驱动强度…… */
```

**它们不是三个可插拔的层**，而是**同一个 pin controller 驱动实现的三组回调**，通常在同一个 `.c` 文件里紧挨着定义。核心把它们分开只是为了：

1. `pinmux` 和 `pinconf` 各有独立 Kconfig（`CONFIG_PINMUX` / `CONFIG_PINCONF`），可以整层编译掉
2. 只做电气配置不做复用的控制器（少见但存在）可以只填 `confops`

| ops | 必填吗 | 校验函数 | 缺了会怎样 |
|---|---|---|---|
| `pctlops` | **必填**（kerneldoc 写 "this is optional"，但代码不是这么做的） | `pinctrl_check_ops()` | 注册返回 `-EINVAL` |
| `pmxops` | 可选 | `pinmux_check_ops()`（为 NULL 时直接返回 0） | 复用类 map 无法生效 |
| `confops` | 可选 | `pinconf_check_ops()`（为 NULL 时直接返回 0） | 配置类操作返回 `-ENOTSUPP` |

这是本文档集记录的一处 **kerneldoc 与代码不一致**，登记在 `99-evidence.md` §2.3。

### §1.7 本子系统有几层可插拔

**答案：1 层。**

| 层名 | ops | 实现者住哪 | 实现数 |
|---|---|---|---|
| pin controller 驱动层 | `pinctrl_ops` + `pinmux_ops` + `pinconf_ops`（同一个驱动的三组） | `drivers/pinctrl/`（含 28 个厂商子目录） | 见 `99-evidence.md` §3 的普查命令 |

**没有第二层**。对比一下就清楚：

| 子系统 | 可插拔层数 | 各层是谁 |
|---|---|---|
| i2c | 1 | 控制器驱动实现 `i2c_algorithm` |
| pinctrl | **1** | pin controller 驱动实现三套 ops |
| virtio | 2 | 传输层实现 `virtio_config_ops` + 设备驱动实现 `virtio_driver` |

容易误数成 2 层的原因是「三套 ops」看起来像三层。判据：**可插拔层数 = 有几个独立的实现者可以互相组合**。pinctrl 的三套 ops 必须由同一个驱动提供（都挂在同一个 `pinctrl_desc` 上），所以只算 1 层。

**唯一接近"第二层"的东西是泛型存储 + pinconf-generic**：它们让驱动可以不自己写 `get_groups_count()` / `dt_node_to_map()`，但那是**代码复用**（现成的回调实现），不是**运行时可替换**。见 `01-api.md` §5 / §7。

---

## §2 核心抽象 ↔ 消费者关系图

pinctrl 没有总线，所以画不出"子系统总线 ↔ 父总线"。对应的图是**核心抽象和消费者怎么连上**：

```
   ┌──────────────────── platform 总线（唯一真实的总线） ─────────────────────┐
   │                                                                          │
   │   pin controller 的 platform_device        消费者的 platform_device       │
   │   (来自 DT 节点 pinctrl@1000000)           (来自 DT 节点 serial@78af000)  │
   │            │                                        │                    │
   │      platform_driver.probe                    platform_driver.probe       │
   └────────────┼────────────────────────────────────────┼────────────────────┘
                │                                        │
                │ pinctrl_register_and_init()            │ 不用自己调！
                │ 或 devm_pinctrl_register_and_init()    │ 设备核心已在
                ▼                                        │ really_probe() 之前
   ┌──────────────────────────┐                          │ 调了 pinctrl_bind_pins()
   │ struct pinctrl_dev       │                          ▼
   │ ─────────────────────    │              ┌──────────────────────────┐
   │ desc ────────────────────┼──> pinctrl_desc（驱动的 .rodata）       │
   │ pin_desc_tree  (radix)   │              │ struct pinctrl           │
   │ pin_group_tree (radix)   │              │ ────────────────────     │
   │ pin_function_tree(radix) │              │ dev  ──> 消费者 device   │
   │ gpio_ranges    (list)    │              │ states (list) ──┐        │
   │ dev  ──> platform device │              │ state  ──> 当前 │        │
   │ mutex 唯一的细粒度锁   │              │ users  (kref)   │        │
   │ p     ──> hog 用的句柄   │              └─────────────────┼────────┘
   └───────────┬──────────────┘                                │
               │                            ┌───────────────────┘
               │ ▲                          ▼
               │ │              ┌──────────────────────────┐
               │ │              │ struct pinctrl_state      │   一个状态一个
               │ │              │ name  = "default"         │   ("default"/"init"/
               │ │              │ settings (list) ──┐       │    "sleep"/"idle")
               │ │              └───────────────────┼───────┘
               │ │                                  │
               │ │                                  ▼
               │ │              ┌──────────────────────────────────┐
               │ │              │ struct pinctrl_setting            │  一个动作一个
               │ │              │ type = MUX_GROUP / CONFIGS_*      │
               │ └──────────────┤ pctldev ──> 由哪个控制器执行 │
               │   核心按 setting│ union { mux{group,func}           │
               │   里的 pctldev  │         configs{group_or_pin,...} }│
               │   分发回调      └──────────────────────────────────┘
               │
               ▼  调回调（状态应用路径不持任何锁，见 §7.2）
   ┌──────────────────────────────────────────────────────┐
   │ pin controller 驱动的三套 ops → readl/writel 到寄存器 │
   └──────────────────────────────────────────────────────┘
```

**图里最关键的一条**：`pinctrl_setting.pctldev`（`drivers/pinctrl/core.h:134`）。**一个消费者的一个状态里，不同 setting 可以指向不同的 pin controller**——所以 pinctrl 天然支持"一个设备的引脚横跨两个控制器"。核心分发时按每条 setting 自己的 `pctldev` 取锁调回调，不假设全状态同属一个控制器。

**图里第二关键的一条**：消费者那条竖线上写的"不用自己调"。arm64 上绝大多数驱动**从不出现任何 pinctrl 函数调用**，`default` 状态是设备核心在 `really_probe()` 里替它选好的。见 §5 和 `03-internals.md` Part A。

---

## §3 结构体逐个剖析

### §3.1 `struct pinctrl_desc` — 驱动唯一需要填的结构体

`include/linux/pinctrl/pinctrl.h:150-164`

```c
struct pinctrl_desc {
	const char *name;                              /* 必填，NULL → -EINVAL */
	const struct pinctrl_pin_desc *pins;
	unsigned int npins;
	const struct pinctrl_ops  *pctlops;            /* 实际必填 */
	const struct pinmux_ops   *pmxops;
	const struct pinconf_ops  *confops;
	struct module *owner;
#ifdef CONFIG_GENERIC_PINCONF
	unsigned int num_custom_params;
	const struct pinconf_generic_params *custom_params;
	const struct pin_config_item *custom_conf_items;
#endif
	bool link_consumers;
};
```

逐字段见 `01-api.md` §2.2 的必填表。这里只补三条**结构层面**的观察：

| 观察 | 依据 |
|---|---|
| 整个结构体是 `const` 友好的，但 `pinctrl_register*()` 的形参**不带 `const`** | `pinctrl_register_and_init(struct pinctrl_desc *pctldesc, ...)`。所以驱动通常写成非 const 的 `static struct pinctrl_desc`，或者强转 |
| 核心**只存指针，不复制内容** | `pinctrl_init_controller()` 里 `pctldev->desc = pctldesc;`。所以这个结构体**必须活得比 `pinctrl_dev` 长**——用栈上的局部变量是 use-after-free |
| 三个 `custom_*` 字段整体在 `#ifdef CONFIG_GENERIC_PINCONF` 里 | `pinctrl.h:158-162`。`!CONFIG_GENERIC_PINCONF` 时给这三个字段赋值**编译不过**，不是静默忽略 |

### §3.2 `struct pinctrl_dev` — 核心内部的控制器对象

`drivers/pinctrl/core.h:42-65`

```c
struct pinctrl_dev {
	struct list_head node;                    /* 挂 pinctrldev_list */
	struct pinctrl_desc *desc;                /* 只是指针，不是拷贝 */
	struct radix_tree_root pin_desc_tree;     /* pin 号 → struct pin_desc * */
#ifdef CONFIG_GENERIC_PINCTRL_GROUPS
	struct radix_tree_root pin_group_tree;    /* selector → struct group_desc * */
	unsigned int num_groups;
#endif
#ifdef CONFIG_GENERIC_PINMUX_FUNCTIONS
	struct radix_tree_root pin_function_tree; /* selector → struct function_desc * */
	unsigned int num_functions;
#endif
	struct list_head gpio_ranges;             /* struct pinctrl_gpio_range 链表 */
	struct device *dev;                       /* 指回 platform 设备 */
	struct module *owner;
	void *driver_data;                        /* pinctrl_dev_get_drvdata() 取 */
	struct pinctrl *p;                        /* hog 专用，见下 */
	struct pinctrl_state *hog_default;
	struct pinctrl_state *hog_sleep;
	struct mutex mutex;                       /* 本子系统唯一的细粒度锁 */
#ifdef CONFIG_DEBUG_FS
	struct dentry *device_root;
#endif
};
```

三个必须点出来的字段：

| 字段 | 陷阱 |
|---|---|
| `p` / `hog_default` / `hog_sleep` | **控制器自己当消费者**用的。`pinctrl_enable()` 会对自己调 `pinctrl_get()` 去认领 hog 映射（"我这个控制器上有几个引脚，谁都别动，我上电就配好"）。这三个字段在没有 hog 映射时是 NULL/未初始化 |
| `mutex` | kerneldoc 写 "mutex taken on each pin controller specific action"（`core.h:39`），**但状态应用路径一次都不拿它**。真实覆盖范围和后果见 §7.2 |
| `dev` | 不要和 `struct pinctrl.dev` 混。前者是控制器自己的 platform 设备，后者是消费者设备 |

**三棵 radix tree 用 selector 当 key，但 selector 是驱动定义的**。核心不校验 selector 是否连续、是否从 0 开始——只在 `pinctrl_generic_add_group()` 里用 `pctldev->num_groups` 作为下一个 selector 递增。所以**混用"泛型存储"和"自己实现回调"会导致 selector 空间不一致**，是必须二选一的。

### §3.3 `struct pin_desc` — 每个物理引脚的运行时状态

`drivers/pinctrl/core.h:159-171`

```c
struct pin_desc {
	struct pinctrl_dev *pctldev;
	const char *name;
	bool dynamic_name;                                 /* 名字是 kstrdup 出来的吗 */
	void *drv_data;
	/* These fields only added when supporting pinmux drivers */
#ifdef CONFIG_PINMUX
	unsigned mux_usecount;                             /* 计数而不是 bool */
	const char *mux_owner;
	const struct pinctrl_setting_mux *mux_setting;
	const char *gpio_owner;                            /* 与 mux_owner 并行独立 */
#endif
};
```

**`mux_usecount` 为什么是计数而不是布尔**——kerneldoc 自己写了理由（`core.h:148-153`）：一次 `pinctrl_get()` 可能处理多条 mapping 表项，而这些表项可能引用**同一个引脚或同一个组**，每一条都会让计数 +1。所以「引脚被占用」的判据是 `mux_usecount != 0`，「同一个 owner 重复请求」是幂等的（`pin_request()` 里 `if (desc->mux_usecount > 1) return 0;`）。

**`mux_owner` 和 `gpio_owner` 是两个独立字段，这是 pinctrl 所有权模型的核心**：

```
                        ┌── mux_owner  ≠ NULL：被某个消费者当"外设功能引脚"占了
   一个 pin 可以同时有 ─┤
                        └── gpio_owner ≠ NULL：被 gpiolib 当"GPIO"占了

   ops->strict == false（默认）：两者可以同时非空 → 引脚既是 GPIO 又是外设功能
   ops->strict == true          ：核心强制互斥，第二个请求返回 -EINVAL
```

判据在 `drivers/pinctrl/pinmux.c` 的 `pin_request()` 里，两个 `if` 的条件分别是 `(!gpio_range || ops->strict)` 和 `(gpio_range || ops->strict)`——只有 `strict` 为真时两个条件才同时成立，才产生互斥。`strict` 的选择判据见 `01-api.md` §6.2。

### §3.4 消费者侧三件套：`pinctrl` / `pinctrl_state` / `pinctrl_setting`

```c
/* drivers/pinctrl/core.h:77-84 */
struct pinctrl {
	struct list_head node;        /* 挂全局 pinctrl_list */
	struct device *dev;           /* 消费者设备 */
	struct list_head states;      /* struct pinctrl_state 链表 */
	struct pinctrl_state *state;  /* 当前生效的状态，初值 NULL */
	struct list_head dt_maps;     /* 从 DT 生成的 map，释放时要还回去 */
	struct kref users;            /* 唯一带 kref 的 pinctrl 结构体 */
};

/* drivers/pinctrl/core.h:92-96 */
struct pinctrl_state {
	struct list_head node;
	const char *name;             /* "default" / "init" / "sleep" / "idle" / 自定义 */
	struct list_head settings;
};

/* drivers/pinctrl/core.h:131-140 */
struct pinctrl_setting {
	struct list_head node;
	enum pinctrl_map_type type;
	struct pinctrl_dev *pctldev;  /* 每条 setting 自己记归属控制器 */
	const char *dev_name;
	union {
		struct pinctrl_setting_mux mux;          /* core.h:103-106 */
		struct pinctrl_setting_configs configs;  /* core.h:116-120 */
	} data;
};
```

三条必须知道的性质：

| 性质 | 依据 / 后果 |
|---|---|
| `struct pinctrl` 是**按消费者设备去重**的 | `create_pinctrl()` 之前先 `find_pinctrl(dev)` 扫 `pinctrl_list`。同一个设备第二次 `pinctrl_get()` 只是 `kref_get`，不重新解析 mapping 表 |
| `state` 初值 NULL 意味着"还没选过任何状态" | `pinctrl_select_state()` 有一条快路径：`if (p->state == state) return 0;`。所以**重复选同一个状态是零开销的**，驱动可以放心在 runtime PM 回调里无脑调 |
| `union` 让 setting 只能是"复用"或"配置"之一 | 所以 DTS 里同一个组既要设 function 又要设 bias 时，`dt_node_to_map` 必须产生**两条** map（一条 MUX_GROUP、一条 CONFIGS_GROUP）。这是 `pinconf_generic_dt_subnode_to_map()` 的核心工作 |

**`dt_maps` 这个字段是"欠账本"**：DT 解析出来的 `pinctrl_map` 数组是核心 `kmalloc` 的，必须在句柄释放时调回驱动的 `dt_free_map` 还掉。板级 C 数组来的 map 不进这个链表（它是 `.rodata`，不用还）。混淆这两个来源是 `dt_free_map` 被误实现成 `kfree()` 崩溃的根源——见 `01-api.md` §7 和 `05-usage.md`。

### §3.5 `struct pinctrl_map` — 板级/DT 配方

`include/linux/pinctrl/machine.h:67-76`

```c
struct pinctrl_map {
	const char *dev_name;       /* 消费者设备名。等于控制器自己的名字 → hog */
	const char *name;           /* 状态名，传给 pinctrl_lookup_state() 的那个 */
	enum pinctrl_map_type type;
	const char *ctrl_dev_name;  /* 哪个控制器执行。DUMMY_STATE 时不用 */
	union {
		struct pinctrl_map_mux     mux;      /* machine.h:33-36 */
		struct pinctrl_map_configs configs;  /* machine.h:47-51 */
	} data;
};
```

| 字段 | 陷阱 |
|---|---|
| `dev_name` | **`dev_name` 等于控制器自己的 `dev_name()` 时，这条 map 变成 hog**——控制器注册时自己认领，不等消费者。这条规则只写在 kerneldoc 里（`machine.h:53-56`），没有单独的 flag |
| `name` | kerneldoc 写"传给 `pinmux_lookup_state()`"，但**这个函数在本树根本不存在**（全树唯一出现就是这行注释）。真实函数名是 `pinctrl_lookup_state()`。这是第四处 kerneldoc 滞后，登记在 `99-evidence.md` §2.3 |
| `type` | 五种值见 `01-api.md` §3。`PIN_MAP_TYPE_INVALID` 是 0，所以**忘了填 type 的 map 会被注册校验拦下返回 `-EINVAL`**——这是个有意的设计 |
| `data.configs.configs` | 注意它**不是 `const`**（`machine.h:49`），而外层 `pinctrl_map` 在 `pinctrl_maps` 里是 `const struct pinctrl_map *`。这个不一致导致驱动在 `dt_node_to_map` 里可以放心 `kmalloc` 一个 configs 数组塞进去 |

`struct pinctrl_maps`（`drivers/pinctrl/core.h:179-183`）只是**把一个 map 数组包成链表节点**，这样多个来源（板级表 + 每个消费者的 DT 解析结果）可以共存在一条全局链表上：

```
pinctrl_maps (全局 LIST_HEAD, core.c:58)
  ├─ struct pinctrl_maps { maps = 板级 static 数组, num_maps = N }
  ├─ struct pinctrl_maps { maps = 消费者A 的 DT 解析结果, num_maps = 3 }
  └─ struct pinctrl_maps { maps = 消费者B 的 DT 解析结果, num_maps = 5 }
```

**查找是线性扫描全表再按 `dev_name` 过滤**（`for_each_maps()` 宏，定义在 `drivers/pinctrl/core.h`）。表规模在 arm64 上通常几十到几百条，所以 O(n) 是可接受的——但这也解释了为什么解析只在 `pinctrl_get()` 做一次。

### §3.6 `group_desc` / `function_desc` — 泛型存储表项

```c
/* drivers/pinctrl/core.h:194-199，在 #ifdef CONFIG_GENERIC_PINCTRL_GROUPS 里 */
struct group_desc {
	const char *name;
	int *pins;          /* int* 而不是 unsigned*，和 pinctrl_ops 的回调签名不一致 */
	int num_pins;
	void *data;         /* 驱动挂私有信息，核心不碰 */
};

/* drivers/pinctrl/pinmux.h:130-135，在 #ifdef CONFIG_GENERIC_PINMUX_FUNCTIONS 里 */
struct function_desc {
	const char *name;
	const char **group_names;
	int num_group_names;
	void *data;
};
```

**`group_desc.pins` 是 `int *`，但 `pinctrl_ops.get_group_pins()` 的输出参数是 `const unsigned **pins`**。核心提供的现成实现 `pinctrl_generic_get_group_pins()` 里做了强转。这意味着**驱动如果自己遍历 `group_desc`，类型要跟着 `int` 走**，不要照着回调签名写 `unsigned`。

两个 `add` 函数的行为差异（这是踩过的坑，已在 `01-api.md` §5 记录）：

| 函数 | 重名时 |
|---|---|
| `pinctrl_generic_add_group()` | **不查重**，直接插入新 selector。两个同名组会同时存在，后续按名字查只找到第一个 |
| `pinmux_generic_add_function()` | **幂等**：先查有没有同名 function，有就直接返回已有 selector |

### §3.7 `struct pinctrl_gpio_range` — pinctrl 和 gpiolib 的唯一接缝

`include/linux/pinctrl/pinctrl.h:78-87`

```c
struct pinctrl_gpio_range {
	struct list_head node;
	const char *name;
	unsigned int id;
	unsigned int base;       /* 全局 GPIO 号的起点 */
	unsigned int pin_base;   /* 只在 pins == NULL 时有效 */
	unsigned int npins;
	unsigned const *pins;    /* 非空时是显式 pin 号数组，pin_base 被忽略 */
	struct gpio_chip *gc;    /* 可选 */
};
```

**`pin_base` 和 `pins` 是互斥的两种表达方式**，kerneldoc 写得很含蓄（"base pin number of the GPIO range **if pins == NULL**"）：

```
   方式 A：连续映射（pins == NULL）
     GPIO 号  base .. base+npins-1
                ↓  一一对应，偏移量固定
     pin  号  pin_base .. pin_base+npins-1

   方式 B：任意映射（pins != NULL）
     GPIO 号  base+0   base+1   base+2  ...
                ↓        ↓        ↓
     pin  号  pins[0]  pins[1]  pins[2] ...       pin_base 这时是死字段
```

判据在 `pinctrl_match_gpio_range()` 和 `pinctrl_find_gpio_range_from_pin_nolock()` 里：两处都是先 `if (range->pins)` 走数组查找，`else` 才用 `pin_base` 算偏移。

**arm64 上这个结构体通常不由 pin controller 驱动填**，而是走 DT 的 `gpio-ranges` 属性由 gpiolib 反向注册（`drivers/pinctrl/pinctrl-msm.c` 里有一段回退逻辑处理两种情况）。详见 `06-devicetree.md`。

### §3.8 `struct dev_pin_info` — 挂在每个 `struct device` 上的缓存

`include/linux/pinctrl/devinfo.h:30-38`

```c
struct dev_pin_info {
	struct pinctrl *p;
	struct pinctrl_state *default_state;
	struct pinctrl_state *init_state;
#ifdef CONFIG_PM
	struct pinctrl_state *sleep_state;
	struct pinctrl_state *idle_state;
#endif
};
```

这个结构体挂在 `struct device.pins`（`include/linux/device.h`）上，由 `pinctrl_bind_pins()`（`drivers/base/pinctrl.c`）在设备 probe 前分配填好。

**它的存在意义是"避免每次 PM 切换都重新 lookup"**：`pinctrl_pm_select_sleep_state()` 直接用 `dev->pins->sleep_state`，一次字符串查找都不做。

**两个 sleep/idle 字段在 `#ifdef CONFIG_PM` 里**（`devinfo.h:34-37`），所以 `!CONFIG_PM` 时 `struct dev_pin_info` 更小——这也是为什么 `pinctrl_pm_select_*_state()` 在 `!CONFIG_PM` 时必须是返回 0 的 inline stub，而不能是真函数。

---

## §4 全局关系总图

```
                        ═══ 三条全局链表（core.c:52/55/58） ═══

  pinctrldev_list ──> pinctrl_dev ──> pinctrl_dev ──> ...     (每个控制器一个)
        锁: pinctrldev_list_mutex
                │
                │ desc
                ▼
        pinctrl_desc (驱动 .rodata) ──┬─> pinctrl_pin_desc[]  (静态 pin 表)
                                      ├─> pinctrl_ops         ┐
                                      ├─> pinmux_ops          ├ 三套回调
                                      └─> pinconf_ops         ┘
                │
                │ pin_desc_tree (radix, key = pin 号)
                ▼
        pin_desc ──> mux_owner / gpio_owner / mux_usecount     (每个引脚一个)
                     所有权状态就存在这里，debugfs 的 pinmux-pins 读的就是它

  pinctrl_list ──> pinctrl ──> pinctrl ──> ...                 (每个消费者一个)
        锁: pinctrl_list_mutex
                │
                ├─ states ──> pinctrl_state ──> pinctrl_state ──> ...
                │                    │            ("default" / "sleep" / ...)
                │                    └─ settings ──> pinctrl_setting ──> ...
                │                                          │
                │                                          │ pctldev
                │                                          └────────> 回到某个
                │                                                     pinctrl_dev
                └─ dt_maps ──> pinctrl_dt_map ──> ...  (DT 生成的 map，待归还)

  pinctrl_maps ──> pinctrl_maps ──> pinctrl_maps ──> ...       (每个 map 数组一个)
        锁: pinctrl_maps_mutex                │
                                              └─ maps ──> pinctrl_map[]
                                                          全是字符串

                        ═══ 旁路：GPIO 桥接 ═══

        pinctrl_dev.gpio_ranges ──> pinctrl_gpio_range ──> ...
                                          │ gc（可选）
                                          └───────> gpio_chip (gpiolib 的)
```

**四条从图里能直接读出来的结论**：

1. **`pinctrl_dev` 和 `pinctrl` 之间没有直接指针**（除了 hog 用的 `pctldev->p`）。二者只通过 `pinctrl_setting.pctldev` 间接相连——**每次状态切换才建立联系**
2. **`pin_desc` 是所有权的唯一真相来源**。想知道"这个引脚现在谁在用"，只有一条路：查对应控制器的 `pin_desc_tree`
3. **三条全局链表三把独立的锁**，见 §7
4. **`pinctrl_map` 全是字符串，`pinctrl_setting` 全是数字**——这条边界就是 §1.5 讲的"配方 → 下单"

---

## §5 核心操作数据流图

pinctrl 的"核心操作"是**状态切换**（对应 i2c 的"传输"）。arm64 上最常见的路径是**消费者一行代码都不写**：

```
   ① 设备核心自动路径（arm64 上 90%+ 的消费者走这条）
   ─────────────────────────────────────────────────────────────

   really_probe()                                 drivers/base/dd.c
     └─> pinctrl_bind_pins(dev)                   drivers/base/pinctrl.c
           ├─> devm_kzalloc(struct dev_pin_info)
           ├─> devm_pinctrl_get(dev) ─────────────┐
           ├─> pinctrl_lookup_state("init")       │  有 init 就先选 init
           ├─> pinctrl_lookup_state("default")    │
           ├─> pinctrl_lookup_state("sleep")      │
           ├─> pinctrl_lookup_state("idle")       │
           └─> pinctrl_select_state(init or default)
                                                  │
     ... 驱动自己的 .probe() 跑完之后 ...          │
     └─> pinctrl_init_done(dev)                   │  把 init 切回 default
                                                  │
   ② 消费者手动路径（需要非标准状态名时）          │
   ─────────────────────────────────────────────  │
   驱动 .probe():                                 │
     p = devm_pinctrl_get(dev)  ──────────────────┤
     s = pinctrl_lookup_state(p, "my_state")      │
     pinctrl_select_state(p, s)                   │
                                                  │
   ═══════════════ 以下是核心内部 ═══════════════  │
                                                  ▼
   create_pinctrl(dev)                            drivers/pinctrl/core.c
     ├─> pinctrl_dt_to_map(p, pctldev)            drivers/pinctrl/devicetree.c
     │     ├─ 读消费者 DT 的 pinctrl-0/1/2... 和 pinctrl-names
     │     ├─ 每个 phandle → get_pinctrl_dev_from_of_node()
     │     │                   找不到就 -EPROBE_DEFER，整条 probe 重来
     │     └─ 调控制器的 pctlops->dt_node_to_map()
     │           └─> 产出 struct pinctrl_map[]，登记进 p->dt_maps + pinctrl_maps
     └─> for_each_maps():  按 dev_name 过滤出属于本设备的 map
           └─> add_setting(p, pctldev, map)
                 ├─ MUX_GROUP  → pinmux_map_to_setting()
                 │                  字符串 → selector（这里做名字查找）
                 └─ CONFIGS_*  → pinconf_map_to_setting()
                                    configs 数组 kmemdup 一份

   pinctrl_select_state(p, state)
     └─> pinctrl_commit_state(p, state)            core.c
           ├─ if (p->state == state) return 0;      重复选同一状态零开销
           ├─ 先 disable 旧状态的所有 setting
           ├─ 【第一遍】只处理 MUX_GROUP：
           │     pinmux_enable_setting(setting)     drivers/pinctrl/pinmux.c
           │       全程不持任何锁——见 §7.3 和 03 §B1
           │       ├─ pctlops->get_group_pins() 取组里的 pin 列表
           │       ├─ 对组里每个 pin 调 pin_request()
           │       │     └─ 检查 mux_owner/gpio_owner 冲突 → 冲突返回 -EINVAL
           │       └─ ops->set_mux(pctldev, func, group)
           │             └─> 驱动: writel() 到 MUX 寄存器  硬件在这里改变
           └─ 【第二遍】只处理 CONFIGS_PIN / CONFIGS_GROUP：
                 pinconf_apply_setting(setting)     drivers/pinctrl/pinconf.c
                   ├─ ops->pin_config_group_set() 或 pin_config_set()
                   └─> 驱动: writel() 到 PULL/DRIVE 寄存器 硬件在这里改变
```

**两遍循环是本子系统最容易被忽略的机制**：`pinctrl_commit_state()` 先把状态里**所有** MUX setting 应用完，**才**开始应用配置 setting。后果：

| 后果 | 说明 |
|---|---|
| **顺序保证** | 引脚一定是"先切到目标功能，再设电气参数"，不会出现"在旧功能上设了上拉再切功能"的中间态 |
| **DTS 内部顺序无效** | 你在 DTS 的一个 pinconf 子节点里把 `bias-pull-up` 写在 `function` 前面还是后面，结果完全一样 |
| **失败分诊有意义** | 报错来自 `pinmux_enable_setting` 还是 `pinconf_apply_setting`，指向完全不同的原因。见 `07-debug.md` §5 |
| **回滚不完整** | 第二遍失败时会 `goto unapply_new_state` 回滚，但第一遍已经写进硬件的 MUX 需要靠 disable 循环撤回——而**`pinconf` 没有 disable 概念**（`pinconf_ops` 里没有 unset 回调）。所以配置类失败后引脚的电气状态是**部分应用**的 |

**注意整条路径上没有一个 tracepoint**。pinctrl 是少数完全没有 ftrace 埋点的子系统，观测只能靠 debugfs + `dev_dbg`。见 `07-debug.md` §2。

---

## §6 命名约定速查

看到一个名字，按下表判断它属于哪一层：

| 名字形态 | 属于 | 例子 | 判据 |
|---|---|---|---|
| `pinctrl_<verb>()` 且第一参数是 `struct pinctrl *` | **消费者 API** | `pinctrl_lookup_state`、`pinctrl_select_state`、`pinctrl_put` | `include/linux/pinctrl/consumer.h` |
| `pinctrl_<verb>()` 且第一参数是 `struct device *` | **消费者 API（便捷版）** | `pinctrl_get`、`devm_pinctrl_get`、`pinctrl_select_default_state` | 同上 |
| `pinctrl_register*` / `pinctrl_unregister` / `pinctrl_enable` | **驱动侧注册 API** | — | `include/linux/pinctrl/pinctrl.h` |
| `pinctrl_gpio_*()` | **gpiolib 桥接**，只给 gpiolib 驱动用 | `pinctrl_gpio_request`、`pinctrl_gpio_direction_input` | `consumer.h`，参数是全局 GPIO 号不是 pin 号 |
| `pinctrl_generic_*()` | **泛型 group 存储**，只在私有 `core.h` 声明 | `pinctrl_generic_add_group` | 只有 `drivers/pinctrl/` 下的驱动能用 |
| `pinmux_generic_*()` | **泛型 function 存储**，只在私有 `pinmux.h` 声明 | `pinmux_generic_add_function` | 同上 |
| `pinconf_generic_*()` | **通用 DT 配置解析**，公开 | `pinconf_generic_dt_node_to_map_group` | `include/linux/pinctrl/pinconf-generic.h` |
| `pinctrl_utils_*()` | **map 数组构建 helper**，只在私有 `pinctrl-utils.h` 声明 | `pinctrl_utils_add_map_mux` | 同上，仅 `drivers/pinctrl/` 内可用 |
| `pinmux_*()`（无 generic） | **核心内部**，复用层 | `pinmux_enable_setting`、`pin_request` | `drivers/pinctrl/pinmux.h`，全部不导出或只私有声明 |
| `pinconf_*()`（无 generic） | **核心内部**，配置层 | `pinconf_apply_setting` | `drivers/pinctrl/pinconf.h`，**一个都不导出** |
| `PIN_MAP_*` | **板级 mapping 表构造宏** | `PIN_MAP_MUX_GROUP_DEFAULT` | `include/linux/pinctrl/machine.h`，12 个 |
| `PIN_CONFIG_*` | **通用配置参数枚举值** | `PIN_CONFIG_BIAS_PULL_UP` | `pinconf-generic.h:114-142`，25 个实参数 |
| `PINCTRL_PIN*` / `PINCTRL_PINGROUP` / `PINCTRL_PINFUNCTION` | **驱动声明静态表用的宏** | — | `include/linux/pinctrl/pinctrl.h` |
| `PINCTRL_STATE_*` | **四个标准状态名字符串** | `PINCTRL_STATE_DEFAULT` | `include/linux/pinctrl/pinctrl-state.h:33-36` |
| `<struct>_desc` | 描述性结构体，但**归属层不统一** | `pinctrl_desc` 是驱动填的；`pin_desc`/`group_desc`/`function_desc` 是核心内部的 | 看头文件是公开还是私有 |

**两条反直觉的**：

1. **`pinctrl_` 前缀同时用在消费者 API 和驱动 API 上**，靠第一个参数类型区分。`struct pinctrl *` = 消费者，`struct pinctrl_dev *` 或 `struct pinctrl_desc *` = 驱动
2. **`pinmux_` 前缀几乎全是内核内部**。唯一的例外是 `pinmux_generic_*` 那 6 个导出符号——而它们**也只在私有头声明**。所以看到 `pinmux_` 开头就可以判断"我作为普通驱动作者用不到"

### §6.1 便捷宏全表：展开成什么、谁在用

pinctrl 的静态表几乎全靠宏声明，但这些宏分成**两类形态完全不同**的东西，混用会直接编不过：

| 宏 | 头文件 | 展开形态 | 展开后 | 本树使用者 |
|---|---|---|---|---|
| `PINCTRL_PIN(a, b)` | `pinctrl.h:63` | **花括号初始化列表** | `{ .number = a, .name = b }` | 几乎所有控制器驱动 |
| `PINCTRL_PIN_ANON(a)` | `pinctrl.h:64` | 同上 | `{ .number = a }` | **0 个**（`grep -rlw PINCTRL_PIN_ANON drivers/` 为空） |
| `PINCTRL_PINGROUP(_name, _pins, _npins)` | `pinctrl.h:42-47` | **复合字面量** `(struct pingroup){...}` | 填 `struct pingroup`（`pinctrl.h:35-39`：`name`/`pins`/`npins`） | 4 个头文件：`pinctrl-amd.h`、`nomadik/pinctrl-nomadik.h`、`bcm/pinctrl-bcm63xx.h`、`intel/pinctrl-intel.h` |
| `PINCTRL_PINFUNCTION(_name, _groups, _ngroups)` | `pinctrl.h:222-227` | 复合字面量 `(struct pinfunction){...}` | 填 `struct pinfunction`（`pinctrl.h:215-219`） | **1 处**：`intel/pinctrl-intel.h:169`，而且是嵌在自己的 `.func =` 成员里 |
| `PIN_MAP_DUMMY_STATE(dev, state)` | `machine.h:80-85` | 花括号初始化列表 | `.type = PIN_MAP_TYPE_DUMMY_STATE` 的 map 条目 | **0 个。全树只有它自己的定义那一行**（`grep -rnw PIN_MAP_DUMMY_STATE --include=*.c --include=*.h .`） |
| `PIN_MAP_MUX_GROUP(dev, state, pinctrl, grp, func)` | `machine.h:87-97` | 花括号初始化列表 | `type = MUX_GROUP` + `data.mux = {grp, func}` | 下面 4 个宏的基础；板级代码直接用 |
| `PIN_MAP_MUX_GROUP_DEFAULT` / `_HOG` / `_HOG_DEFAULT` | `machine.h:99-106` | 同上（转发） | 见 `05-usage.md` §4.4 的逐宏展开表 | hog 版把 `dev` 传两次，`08-feynman.md` §7 讲了为什么 |
| `PIN_MAP_CONFIGS_PIN*` / `PIN_MAP_CONFIGS_GROUP*` | `machine.h:108-151` | 同上，共 8 个 | `type = CONFIGS_PIN` / `CONFIGS_GROUP` + `data.configs` | 板级 config 表 |
| `for_each_maps(node, i, map)` | 私有 `drivers/pinctrl/core.h:245-249` | **双重 for 循环**（外层遍历 map 段链表，内层遍历段内数组） | — | 只有 2 处，都在核心：`core.c:1065`（`pinctrl_dt_to_map` 之后建 state）、`core.c:1822`（debugfs 的 `pinctrl-maps`） |

**三条从这张表读出来的结论**：

1. **`PIN_MAP_DUMMY_STATE` 是一个死宏**，但 `PIN_MAP_TYPE_DUMMY_STATE` 这个**枚举值**活得很好——`devicetree.c:195` 会在解析到空的 `pinctrl-0 = <0>` 时亲手把 `map->type` 设成它，核心里 `core.c:964`/`:1441`/`:1449`/`:1827` 四处都要特判。**"宏没人用"不等于"这个机制没人用"**：机制走的是 DT 路径，宏是给板级 C 代码用的，而板级 C 代码在 arm64 上基本绝迹（见 `05-usage.md` §4.1）。
2. **两种展开形态不能互换**。`PINCTRL_PIN` 展开成裸花括号，所以必须写在数组初始化器里（`static const struct pinctrl_pin_desc pins[] = { PINCTRL_PIN(0, "GPIO0"), ... }`）；`PINCTRL_PINGROUP` 展开成**带类型的复合字面量**，它本身就是一个 `struct pingroup` 值，可以直接赋值、传参、也可以放进数组。把 `PINCTRL_PIN` 当值用、或者在 `PINCTRL_PINGROUP` 外面再套一层花括号，都是编译错误。
3. **arm64 的控制器驱动一个都不用 `PINCTRL_PINGROUP` / `PINCTRL_PINFUNCTION`**：那 4 个使用者里 amd/intel 是 x86，bcm63xx 是 MIPS，nomadik 是 arm32。arm64 驱动（qcom、rockchip、mediatek…）都自己定义 group/function 结构体。所以这两个宏在本文档集的 arm64 视角下**只需要认得，不需要会用**。

---

## §7 全局变量与锁

### §7.1 全局变量（全部在 `drivers/pinctrl/core.c`）

| 变量 | 行 | 作用 | 备注 |
|---|---|---|---|
| `pinctrl_dummy_state` | `core.c:40` | 是否允许"找不到就用空状态"模式 | `static bool`，由 `pinctrl_provide_dummies()` 置真。全树只有 1 个调用者：`drivers/pinctrl/renesas/core.c` |
| `pinctrldev_list` | `core.c:52` | 所有已注册的 pin controller | `static LIST_HEAD`。消费者按名字/`of_node` 在这里线性查找 |
| `pinctrl_list` | `core.c:55` | 所有活着的消费者句柄 | `static LIST_HEAD`。`find_pinctrl()` 用它做去重 |
| `pinctrl_maps` | `core.c:58` | 所有 mapping 表段 | **非 static**，因为 `for_each_maps()` 宏和 debugfs 代码要用 |
| `debugfs_root` | `core.c:1897` | `/sys/kernel/debug/pinctrl/` 的根 | `static struct dentry *`，在 `#ifdef CONFIG_DEBUG_FS` 里 |

### §7.2 五把锁

| 锁 | 类型 | 定义 | 保护什么 | 谁拿 |
|---|---|---|---|---|
| `pinctrl_list_mutex` | `mutex` | `core.c:43`（static） | `pinctrl_list` 链表本身 | `create_pinctrl()` / `find_pinctrl()` / `pinctrl_free()` |
| `pinctrl_maps_mutex` | `mutex` | `core.c:46`（**非 static**） | `pinctrl_maps` 链表本身 | `pinctrl_register_mappings()` / `pinctrl_unregister_mappings()` / `for_each_maps()` 的调用者 / debugfs |
| `pinctrldev_list_mutex` | `mutex` | `core.c:49`（static） | `pinctrldev_list` 链表本身 | 注册/注销控制器、按名字查控制器、debugfs |
| `pctldev->mutex` | `mutex` | `core.h:61`（每控制器一把） | **实际保护范围比 kerneldoc 说的窄**：GPIO range 链表、GPIO 桥接路径上的 `pin_desc` 所有权字段、`pin_config_group_get()`、debugfs 遍历、注销清理 | 见下 |
| `p->users` | `kref`（不是锁） | `core.h:83` | `struct pinctrl` 的生命周期 | `pinctrl_get()` / `pinctrl_put()` |

**`pctldev->mutex` 的 kerneldoc 写 "mutex taken on each pin controller specific action"（`core.h:39`），但状态应用路径一次都不拿它。** 全树 `mutex_lock(&pctldev->mutex)` 的落点（可自己跑 `grep -n 'mutex_lock(&pctldev->mutex)' drivers/pinctrl/*.c`）：

| 拿锁的地方 | 文件:行 |
|---|---|
| GPIO range 查找/增删 | `core.c:303`、`345`、`414`、`504`、`520` |
| **GPIO 桥接六函数** | `core.c:764`（`can_use_line`）、`799`（`gpio_request`）、`831`（`gpio_free`）、`854`（`gpio_direction`）、`913`（`gpio_set_config`） |
| `pin_config_group_get()` | `pinconf.c:83` |
| debugfs 各 show | `core.c:1644`、`1696`、`1743`；`pinmux.c:549`、`599`；`pinconf.c:310` |
| `pinctrl_unregister()` | `core.c:2207`、`2215` |

**没有出现在这张表里的路径**：`pinctrl_select_state()` → `pinctrl_commit_state()` → `pinmux_enable_setting()` → `pin_request()` → `ops->set_mux()`，以及 `pinconf_apply_setting()`。**整条状态切换路径无锁**。

四条后果：

- **注册控制器是全局串行的**——多核并行 probe 时会在 `pinctrldev_list_mutex` 上排队
- **状态切换之间没有互斥**。两个消费者同时对**同一个控制器**切换状态时，`pin_desc.mux_owner` / `mux_usecount` 的读改写没有保护。实践中不炸的原因是 arm64 上状态切换绝大多数发生在 probe 阶段（`pinctrl_bind_pins()`），而设备核心的 probe 已经被 `deferred_probe` 和各自的设备锁串行化了——但**这是路径依赖，不是设计保证**
- **GPIO 路径和 mux 路径的锁不对称**：`pinctrl_gpio_request()` 持锁调 `pin_request()`，`pinmux_enable_setting()` 不持锁调 `pin_request()`。所以"GPIO 抢引脚"和"外设抢引脚"并发时，冲突检测本身就是不可靠的
- **ops 回调因此分成两组**（速查表在 `01-api.md` §12）：GPIO 类回调和 `pin_config_group_get` 是**持锁调**的，`set_mux`/`request`/`pin_config_set`/`dt_node_to_map` 是**不持锁调**的。驱动在前一组里调带锁版 helper 会死锁

详细讨论见 `03-internals.md` §B1 和 §C1。

### §7.3 全子系统一律用 mutex，没有 spinlock 变体

判据：

```bash
grep -rn 'spin_lock\|DEFINE_SPINLOCK\|raw_spin' drivers/pinctrl/core.c \
    drivers/pinctrl/pinmux.c drivers/pinctrl/pinconf.c drivers/pinctrl/devicetree.c
# 空
```

这条性质的三个后果：

1. **pinctrl 全部 API 只能在可睡眠上下文调**（速查表见 `01-api.md` §12）
2. **不能从中断处理函数里切换引脚状态**。需要这么做的场合只有一个正规解法：`workqueue`
3. **PREEMPT_RT 上核心层没有额外风险**——核心一把 spinlock 都没有（上面那条命令为空），全是 mutex，在 RT 上是可抢占的睡眠锁，pinctrl 核心不需要任何 RT 特殊处理。**但驱动层不是这样**：详见 `03-internals.md` §B1.5

---

## §8 内存所有权与生命周期

| 对象 | 谁分配 | 谁释放 | 陷阱 |
|---|---|---|---|
| `pinctrl_desc` | **驱动**（通常是 `static`） | 驱动（`static` 则不用） | **核心只存指针**。用栈变量 = use-after-free |
| `pinctrl_pin_desc[]` | 驱动（通常 `static const`） | 同上 | 核心在注册时逐个读，之后仍会通过 `desc->pins` 访问 |
| `pinctrl_dev` | 核心（`pinctrl_init_controller()` 里 `kzalloc`） | `pinctrl_unregister()`；devm 版自动 | 非 devm 版忘了 unregister 会泄漏且留下悬空链表节点 |
| `pin_desc`（每引脚一个） | 核心（`pinctrl_register_one_pin()`） | `pinctrl_free_pindescs()`，随控制器注销 | 驱动无法访问 |
| `pin_desc.name` | 核心 `kstrdup`（驱动没给名字时）或直接指向驱动的字符串 | 靠 `dynamic_name` 标志区分要不要 `kfree` | 这就是那个 bool 字段的唯一用途 |
| `pinctrl`（消费者句柄） | 核心（`create_pinctrl()`） | `kref` 归零时 `pinctrl_free()` | `pinctrl_put()` 配 `pinctrl_get()`，`devm_pinctrl_put()` 配 `devm_pinctrl_get()`，**不能交叉** |
| `pinctrl_state` / `pinctrl_setting` | 核心（解析 mapping 表时） | 随 `pinctrl` 一起 | 消费者拿到的 `struct pinctrl_state *` 在句柄释放后立刻失效 |
| `pinctrl_setting.data.configs.configs` | 核心 `kmemdup`（`pinconf_map_to_setting()`） | `pinconf_free_setting()` | 因为是拷贝，所以驱动在 `dt_node_to_map` 里 `kmalloc` 的 configs 数组可以在 `dt_free_map` 里安全释放 |
| DT 生成的 `pinctrl_map[]` | **驱动**在 `dt_node_to_map()` 里分配 | **驱动**在 `dt_free_map()` 里释放，核心负责在正确时机调 | 两个回调必须配对实现。只实现前者 = 每次 probe 泄漏 |
| 板级 `pinctrl_map[]` | 板级代码（通常 `static const`） | 不释放 | **不进 `p->dt_maps`**，所以核心不会对它调 `dt_free_map` |
| `struct dev_pin_info` | 核心（`pinctrl_bind_pins()` 里 `devm_kzalloc`） | devm 自动 | 挂在 `dev->pins`，驱动可以读但不该写 |
| `pinctrl_gpio_range` | **gpiolib 或 pin controller 驱动** | 同上 | 核心只把它挂进链表，不拥有它。提前释放会留悬空节点 |
| `group_desc` / `function_desc` | 核心（`*_add_group` / `*_add_function` 里 `kzalloc`） | `*_remove_*` 或控制器注销时的 radix tree 清理 | `.data` 里挂的驱动私有数据**核心不释放**，驱动要自己管 |

**一句话总则**：**能用 devm 就用 devm**。pinctrl 的非 devm 路径存在明确设计债——`pinctrl_unregister()` 里有一条 `/* TODO: check that no pinmuxes are still active? */`（`drivers/pinctrl/core.c:2216`），意思是**注销时不检查引脚是否还被占用**。详见 `03-internals.md` Part C。

---

## §9 下一步读什么

| 你想知道 | 去 |
|---|---|
| 这些结构体是在哪一步被创建/填充的 | `03-internals.md` Part A（时间线） |
| 锁的具体加锁顺序和死锁风险 | `03-internals.md` Part B |
| 泛型存储和 pinconf-generic 怎么用 | `01-api.md` §5 / §7、`04-extensions.md` |
| 我该怎么填这些结构体 | `05-usage.md` |
| DTS 里的属性最后变成哪个结构体的哪个字段 | `06-devicetree.md` |
| 怎么从 debugfs 读出 `pin_desc` 的当前状态 | `07-debug.md` §3 |
| 为什么 `struct pinctrl` 要叫这个名字 | `08-feynman.md` |
