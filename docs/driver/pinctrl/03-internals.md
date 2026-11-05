---
title: 内部实现
---

# pinctrl 实现剖析

> 本篇是三条轴，**只有 Part A 是主轴**：
> - **Part A｜时间线**：从子系统初始化到控制器注销，按"谁调用谁"验证过的真实次序逐段剖析
> - **Part B｜横切机制**：贯穿多个时间点的机制（锁、所有权、引用计数、延迟探测、hog、GPIO 桥接、debugfs）
> - **Part C｜评价**：源码自陈的设计债、观测能力缺口、值得注意的取舍
>
> 章节号带 A/B/C 前缀，所以任何一处引用（如"见 §B1"）自带"在哪条轴上"的信息。

---

## §0 导航

### §0.1 分层图

```
┌──────────────────────────────────────────────────────────────────┐
│ 消费者（UART / I2C / MMC / 以太网…… 控制器驱动）                  │
│   大多数一行 pinctrl 代码都不写                                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│ 设备核心桥接层    drivers/base/pinctrl.c（不在 drivers/pinctrl/）│
│   pinctrl_bind_pins()   由 dd.c:616 在 probe 之前调               │
│   pinctrl_init_done()   由 dd.c:684 在 probe 之后调               │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│ pinctrl 核心             drivers/pinctrl/core.c                   │
│   句柄管理 / 状态管理 / mapping 表 / pin 表 / GPIO range / debugfs │
│         ┌──────────────┬──────────────┬───────────────┐          │
│         ▼              ▼              ▼               ▼          │
│   devicetree.c    pinmux.c       pinconf.c    pinconf-generic.c  │
│   DT→map 解析     复用+所有权     电气配置      通用属性解析      │
│                                              pinctrl-utils.c     │
│                                              map 数组构建 helper │
└───────────────────────────┬──────────────────────────────────────┘
                            │  三套 ops 回调（持不持锁分两组，见 §B1）
┌───────────────────────────▼──────────────────────────────────────┐
│ pin controller 驱动     drivers/pinctrl/<vendor>/                 │
│   pinctrl_ops + pinmux_ops + pinconf_ops → readl/writel           │
└──────────────────────────────────────────────────────────────────┘
```

### §0.2 生命周期时间线图

```
 内核启动                                                          关机/rmmod
   │                                                                    │
   ├─ core_initcall: pinctrl_init()                          §A1        │
   │                                                                    │
   ├─────── pin controller 的 platform probe ───────┐                    │
   │  pinctrl_register_and_init()                  §A2                   │
   │    ├─ pinctrl_init_controller()  三套 ops 校验 + pin 表登记          │
   │    └─ list_add_tail(&pinctrldev_list)                               │
   │  pinctrl_enable()                             §A3                   │
   │    └─ pinctrl_claim_hogs()   控制器自己当消费者                    │
   │                                                                    │
   ├─ （可选）板级 pinctrl_register_mappings()      §A4                   │
   │     arm64 上基本不用，只有 renesas 走                                │
   │                                                                    │
   ├─────── 消费者的 platform probe ────────────────┐                     │
   │  dd.c:616 → pinctrl_bind_pins()               §A5                   │
   │    ├─ devm_pinctrl_get()                      §A6                   │
   │    │    └─ create_pinctrl()                                         │
   │    │         ├─ pinctrl_dt_to_map()            §A7                  │
   │    │         └─ add_setting() × N              §A8                  │
   │    ├─ pinctrl_lookup_state("default"/"init"…)  §A9                  │
   │    └─ pinctrl_select_state(init or default)    §A10                 │
   │         └─ pinctrl_commit_state()                                   │
   │              ├─【第一遍】pinmux_enable_setting()  §A11               │
   │              │     └─ pin_request() → ops->set_mux()                │
   │              └─【第二遍】pinconf_apply_setting()  §A12               │
   │                                                                    │
   │  驱动自己的 .probe() 跑                                              │
   │                                                                    │
   │  dd.c:684 → pinctrl_init_done()   init → default   §A13             │
   │                                                                    │
   ├─ 运行期：suspend/resume 切 sleep/idle 状态       §A14                │
   │           debugfs 手工 pinmux-select              §B7                │
   │                                                                    │
   ├─ 消费者 remove：devm 自动 devm_pinctrl_put()      §A15/§A16          │
   │    └─ pinmux_disable_setting() → pin_free()                         │
   │                                                                    │
   └─ 控制器 remove：pinctrl_unregister()             §A17 ───────────────┘
```

### §0.3 哪些内容不在本篇

| 你想找 | 去 |
|---|---|
| API 签名、参数、错误码含义 | `01-api.md` |
| 结构体字段逐个解释、内存所有权表 | `02-datastructures.md` |
| 泛型 group/function 存储、pinconf-generic 的内部 | `04-extensions.md` |
| 我该怎么写驱动 | `05-usage.md` |
| DTS 属性怎么被解析成 map | `06-devicetree.md`（本篇 §A7 只讲流程骨架） |
| debugfs 每个文件输出怎么读 | `07-debug.md`（本篇 §B7 只讲注册时机） |
| 为什么设计成这样（大白话） | `08-feynman.md` |
| 每条论断的出处 | `99-evidence.md` |

---

# Part A｜时间线

> 本部分的次序是用「谁调用谁」逐条验证的，不是凭直觉排的。验证方法见 `99-evidence.md` §2.4。
> 每节标题后的行号区间是函数的完整定义范围。

## §A1 子系统初始化 —— `pinctrl_init()`（`drivers/pinctrl/core.c:2334-2338`）

```c
static int __init pinctrl_init(void)
{
	pr_info("initialized pinctrl subsystem\n");
	pinctrl_init_debugfs();
	return 0;
}
/* init early since many drivers really need to initialized pinmux early */
core_initcall(pinctrl_init);
```

三个观察：

| 观察 | 说明 |
|---|---|
| 用 `core_initcall` 而不是 `subsys_initcall` | 注释写明理由："many drivers really need to initialized pinmux early"。`core_initcall` 比 `subsys_initcall` 早一级 |
| 全部工作就是建 debugfs 根目录 | 三条全局链表是 `LIST_HEAD()` 静态初始化的（`core.c:52/55/58`），三把 mutex 是 `DEFINE_MUTEX()`（`core.c:43/46/49`）——**编译期就好了，不需要运行时初始化** |
| `pr_info("initialized pinctrl subsystem\n")` 是 pinctrl 唯一一条无条件 `pr_info` | 启动日志里看到它就说明子系统在。见 `07-debug.md` §1 |

**后果**：`core_initcall` 早于绝大多数设备注册，所以"pinctrl 子系统没初始化好"这个故障在实践中不存在。真正的启动顺序问题都是"pin controller 驱动还没 probe"（→ §B4 的 `-EPROBE_DEFER`）。

## §A2 控制器注册 —— `pinctrl_register_and_init()`（`core.c:2175-2191`）

调用链（devm 版 `devm_pinctrl_register_and_init()` 在 `core.c:2300-2318`，多一层 devres 包装）：

```
pinctrl_register_and_init(pctldesc, dev, driver_data, &pctldev)   core.c:2175-2191
  ├─> pinctrl_init_controller(pctldesc, dev, driver_data)         core.c:1997-2063
  │     ├─ 入口校验：!pctldesc → -EINVAL；!pctldesc->name → -EINVAL
  │     ├─ kzalloc(struct pinctrl_dev)      失败 → -ENOMEM
  │     ├─ INIT_RADIX_TREE(&pctldev->pin_desc_tree)               core.c:2014
  │     ├─ INIT_RADIX_TREE(&pctldev->pin_group_tree)     #ifdef core.c:2016
  │     ├─ INIT_RADIX_TREE(&pctldev->pin_function_tree)  #ifdef core.c:2019
  │     ├─ INIT_LIST_HEAD(&pctldev->gpio_ranges)                  core.c:2021
  │     ├─ INIT_LIST_HEAD(&pctldev->node)                         core.c:2022
  │     ├─ mutex_init(&pctldev->mutex)
  │     ├─ pctldev->owner = pctldesc->owner   拿的是 desc 里的，不是 THIS_MODULE
  │     ├─ pctldev->desc  = pctldesc          只存指针
  │     ├─ pinctrl_check_ops(pctldev)                    core.c:1977-1986
  │     ├─ pinmux_check_ops(pctldev)                     pinmux.c:33-61
  │     ├─ pinconf_check_ops(pctldev)                    pinconf.c:26-36
  │     └─ pinctrl_register_pins(pctldev, pins, npins)   core.c:254-265
  │           └─ pinctrl_register_one_pin() × npins      core.c:206-249
  └─> 【不】list_add；把 pctldev 通过出参交给调用者，等它调 pinctrl_enable()
```

### §A2.1 三个 ops 校验函数的实际严格度

**这三个函数的宽严程度完全不同**，是"我的驱动为什么注册失败"最常见的落点：

| 校验函数 | 行 | ops 为 NULL 时 | 检查什么 |
|---|---|---|---|
| `pinctrl_check_ops()` | `core.c:1977-1986` | **直接 `-EINVAL`** | `ops` 本身 + `get_groups_count` + `get_group_name` 三者缺一即失败 |
| `pinmux_check_ops()` | `pinmux.c:33-61` | **返回 0**（合法，表示不支持复用） | `get_functions_count` / `get_function_name` / `get_function_groups` / `set_mux` 四者必须齐全；还会**遍历所有 function 检查每个都有名字** |
| `pinconf_check_ops()` | `pinconf.c:26-36` | **返回 0** | `pin_config_set` 和 `pin_config_group_set` **至少有一个** |

**`pinctrl_check_ops()` 与 kerneldoc 矛盾**：`pinctrl_desc.pctlops` 的 kerneldoc（`include/linux/pinctrl/pinctrl.h:133-134`）写 "this is optional"，但代码里它是唯一硬性必填的 ops。登记在 `99-evidence.md` §2.3。

**`pinmux_check_ops()` 里那个 function 名字遍历循环是 O(n) 的注册期开销**，n = function 数量（高通 SoC 上是几百）。它对"某个 function 忘了填名字"这类驱动 bug 有价值，但也意味着**注册期会把整张 function 表走一遍**。

### §A2.2 pin 表登记 —— `pinctrl_register_one_pin()`（`core.c:206-249`）

```
for each pin in pctldesc->pins:
  ├─ pin_desc_get(pctldev, number)  查 radix tree
  │    └─ 已存在 → dev_err("pin %d already registered") + return -EINVAL
  ├─ kzalloc(struct pin_desc)       失败 → -ENOMEM
  ├─ pindesc->pctldev = pctldev
  ├─ 名字处理：
  │    ├─ pin->name 非空 → pindesc->name = pin->name       （借用驱动的字符串）
  │    └─ pin->name 为空 → kasprintf("PIN%u", number)
  │                        pindesc->dynamic_name = true    释放时靠这个标志
  ├─ pindesc->drv_data = pin->drv_data
  └─ radix_tree_insert(&pctldev->pin_desc_tree, number, pindesc)
```

**重号检测是这一步唯一的语义校验**。pin 表里 `number` 重复会让注册失败并打印 `"pin %d already registered"`。但**编号不需要连续、不需要从 0 开始、不需要有序**——radix tree 不在乎。所以稀疏 pin 编号是合法的（有 SoC 这么用）。

**失败时的清理**：`pinctrl_register_pins()` 一旦有一个 pin 失败，`pinctrl_init_controller()` 会 `goto out_err` 走 `pinctrl_free_pindescs()`（`core.c:186-202`）把已插入的全部撤掉。这条回滚路径是完整的。

## §A3 控制器上线 —— `pinctrl_enable()`（`core.c:2111-2128`）

```
pinctrl_enable(pctldev)
  ├─> pinctrl_claim_hogs(pctldev)                    core.c:2073-2109
  │     ├─ pctldev->p = create_pinctrl(pctldev->dev, pctldev)
  │     │    控制器给自己建一个消费者句柄
  │     ├─ PTR_ERR(p) == -ENODEV → 视为正常（"没有 hog 映射"），继续
  │     ├─ 其他错误 → 返回错误，注册失败
  │     ├─ hog_default = find_state(p, PINCTRL_STATE_DEFAULT)
  │     │    找不到只 dev_dbg，不是错误
  │     ├─ pinctrl_select_state(p, hog_default)   有的话立刻生效
  │     └─ hog_sleep  = find_state(p, PINCTRL_STATE_SLEEP)
  ├─ mutex_lock(&pinctrldev_list_mutex)
  ├─ list_add_tail(&pctldev->node, &pinctrldev_list)   上线的瞬间就在这一行
  └─ mutex_unlock(&pinctrldev_list_mutex)
```

**为什么注册要分 register / enable 两步**——这是本子系统最重要的时序设计，也是 `pinctrl_register()` 被判定为"有设计缺陷"的原因：

```
   问题：hog 映射意味着"控制器一上线就自己认领一批引脚"。
        但 create_pinctrl() 需要遍历 mapping 表、调 dt_node_to_map()、
        调 set_mux()——这些全都要求驱动的 driver_data 已经完全就绪。

   pinctrl_register()（一步式）的顺序：
        分配 pctldev → 校验 ops → 登记 pin 表 → claim hogs → 上线
                                                  ↑
                        此时驱动的 probe 还没跑完，driver_data 里
                          可能有字段还没填（如寄存器基址已 ioremap 但
                          时钟还没使能），回调一执行就炸

   pinctrl_register_and_init() + pinctrl_enable()（两步式）的顺序：
        [驱动 probe 前半段] pinctrl_register_and_init()
                              → 校验 + pin 表，但不上线、不 claim hogs
        [驱动 probe 后半段] 把 driver_data 全部填好、时钟开好
        [驱动 probe 末尾]   pinctrl_enable()
                              → claim hogs + 上线，此时回调可以安全执行
```

`pinctrl_register()` 的 kerneldoc（`core.c:2130-2143`）自陈了这个缺陷。全树仍有 8 个驱动在用一步式，见 `01-api.md` §2.5。

**`-ENODEV` 被当成正常路径**这一点值得单独记：`create_pinctrl()` 在"mapping 表里一条属于本设备的记录都没有"时返回 `-ENODEV`，而绝大多数控制器就是没有 hog 映射的。所以这条 `if` 是常态分支而不是异常分支。

## §A4 板级 mapping 表注册 —— `pinctrl_register_mappings()`（`core.c:1421-1481`）

arm64 上**几乎用不到**（DT 走 §A7 的路径），但机制上它可以在任何时刻发生，所以时间线位置是"任意早于消费者 probe"。

```
pinctrl_register_mappings(maps, num_maps)
  ├─ for each map:  逐条校验
  │    ├─ !map->dev_name                → -EINVAL  "failed to register map %s (%d): no device given"
  │    ├─ !map->name && type != DUMMY   → -EINVAL  "failed to register map %d: no map name given"
  │    ├─ type != DUMMY && !ctrl_dev_name → -EINVAL "failed to register map %s (%d): no pin control device given"
  │    └─ switch (map->type):
  │         ├─ DUMMY_STATE      → break（不校验 data）
  │         ├─ MUX_GROUP        → pinmux_validate_map()    pinmux.c:64-72
  │         ├─ CONFIGS_PIN      → pinconf_validate_map()   pinconf.c:39-54
  │         ├─ CONFIGS_GROUP    → pinconf_validate_map()
  │         └─ default          → -EINVAL  "failed to register map %s (%d): invalid type given"
  ├─ kzalloc(struct pinctrl_maps)
  ├─ maps_node->maps = maps;  maps_node->num_maps = num_maps
  └─ mutex_lock(&pinctrl_maps_mutex) → list_add_tail(&pinctrl_maps) → unlock
```

**校验是"全部通过才注册"**：任何一条不合法，整批都不注册。所以板级表里一个 typo 会让整块表失效。

**`pinctrl_unregister_mappings()`（`core.c:1490-1503`）按 `maps` 指针**在链表里找对应的 `pinctrl_maps` 节点删掉。所以传进去的必须是**当初注册时的同一个指针**。

## §A5 消费者 probe 前 —— `pinctrl_bind_pins()`（`drivers/base/pinctrl.c:22-105`）

**这个函数不在 `drivers/pinctrl/` 下**，找它的人经常在 pinctrl 目录里 grep 不到。调用点是 `drivers/base/dd.c:616`，在 `really_probe()` 里、驱动 `.probe()` **之前**。

```
pinctrl_bind_pins(dev)                                drivers/base/pinctrl.c:22-105
  ├─ if (dev->of_node_reused) return 0;   复用 DT 节点的设备整个跳过
  ├─ dev->pins = devm_kzalloc(sizeof(struct dev_pin_info))
  │     失败 → return -ENOMEM   这一条会真的挡住 probe
  ├─ dev->pins->p = devm_pinctrl_get(dev)              → §A6
  │     IS_ERR → dev_dbg("no pinctrl handle") + goto cleanup_alloc
  ├─ default_state = pinctrl_lookup_state(p, PINCTRL_STATE_DEFAULT)
  │     IS_ERR → dev_dbg("no default pinctrl state")
  │              ret = 0 然后 goto cleanup_get —— 见下面的陷阱
  ├─ init_state = pinctrl_lookup_state(p, PINCTRL_STATE_INIT)
  │     ├─ 有   → pinctrl_select_state(p, init_state)
  │     └─ 没有 → pinctrl_select_state(p, default_state)   （注释："perfectly legal"）
  ├─ ret != 0 → dev_dbg("failed to activate initial pinctrl state") + goto cleanup_get
  ├─ #ifdef CONFIG_PM
  │     sleep_state = lookup("sleep")   找不到只 dev_dbg
  │     idle_state  = lookup("idle")    找不到只 dev_dbg
  │  #endif
  └─ return 0

cleanup_get:   devm_pinctrl_put(dev->pins->p)
cleanup_alloc: devm_kfree(dev, dev->pins);  dev->pins = NULL
               ├─ ret == -EPROBE_DEFER → return ret      只有这两个码往上传
               ├─ ret == -EINVAL       → return ret
               └─ 其他（含 -ENODEV）   → return 0，probe 照常继续
```

### §A5.1 两个静默失效陷阱

这两条是 pinctrl "配了但没生效、还不报错"的根本原因，`06-devicetree.md` 的决策树就是从这里推出来的：

**陷阱 1：错误码被吞。** 除了 `-EPROBE_DEFER` 和 `-EINVAL`，**其他错误全部被改写成 0**。注释写得很直白："We ignore errors like -ENOENT meaning no pinctrl state"。后果：

| DTS 写法 | `pinctrl_bind_pins()` 内部 | 消费者 probe 结果 | 引脚状态 |
|---|---|---|---|
| 完全没写 pinctrl-* | `devm_pinctrl_get()` → `-ENODEV` | **成功**，`dev->pins = NULL` | 保持复位默认值 |
| 写了 `pinctrl-0` 但 `pinctrl-names` 里没有 `"default"` | lookup default → `-ENODEV` | **成功**，`dev->pins = NULL` | **保持复位默认值——静默失效** |
| 组名 / function 名写错 | `add_setting()` → `-EINVAL` | **失败**，probe 被挡 | — |
| phandle 指向的控制器还没 probe | `-EPROBE_DEFER` | 延迟重试 | — |
| 引脚被别的设备占了 | `pin_request()` → `-EINVAL` | **失败** | 部分应用 |

**第二行是最坑的一行**：DTS 里认认真真写了 `pinctrl-0 = <&uart0_pins>;` 但 `pinctrl-names = "active";`，结果是**设备正常 probe、引脚一个都没配、日志里只有一条 `dev_dbg` 级别的 "no default pinctrl state"**（默认不可见）。

**陷阱 2：没有 `default` 状态时整个 `dev->pins` 被释放。** 走 `cleanup_get` 路径会 `devm_kfree(dev, dev->pins); dev->pins = NULL;`。而所有 PM 辅助函数第一行都是 `if (!dev->pins) return 0;`。后果：

```
   DTS 只写了 sleep 没写 default：
     pinctrl-names = "sleep";
     pinctrl-0 = <&uart0_sleep>;

   → lookup("default") 失败 → dev->pins 整个被释放
   → pinctrl_pm_select_sleep_state() 里 !dev->pins → 直接 return 0
   → sleep 状态永远不会被选中，尽管 DTS 里写了
```

**正确写法是 default 必须存在**，其他状态才有意义。这条约束在任何绑定文档里都没写。

## §A6 句柄创建 —— `pinctrl_get()`（`core.c:1123-1142`）/ `create_pinctrl()`（`core.c:1035-1116`）

```
pinctrl_get(dev)                                       core.c:1123-1142
  ├─ !dev → WARN_ON + ERR_PTR(-EINVAL)
  ├─ p = find_pinctrl(dev)                             core.c:1017-1029
  │    └─ 遍历 pinctrl_list 比对 p->dev == dev（持 pinctrl_list_mutex）
  ├─ if (p) { kref_get(&p->users); return p; }   复用，不重新解析
  └─ return create_pinctrl(dev, NULL)

create_pinctrl(dev, pctldev)                           core.c:1035-1116
  ├─ kzalloc(struct pinctrl)                失败 → ERR_PTR(-ENOMEM)
  ├─ p->dev = dev
  ├─ INIT_LIST_HEAD(&p->states)                        core.c:1052
  ├─ INIT_LIST_HEAD(&p->dt_maps)                       core.c:1053
  ├─ pinctrl_dt_to_map(p, pctldev)                     → §A7
  │    出错 → kfree(p) + 返回错误（-EPROBE_DEFER 在这里冒出来）
  ├─ devname = dev_name(dev)
  ├─ mutex_lock(&pinctrl_maps_mutex)
  │  for_each_maps(maps_node, i, map):
  │    ├─ if (map->type == PIN_MAP_TYPE_DUMMY_STATE) → create_state() 后 continue
  │    ├─ if (strcmp(map->dev_name, devname)) continue    按设备名过滤
  │    ├─ add_setting(p, pctldev, map)                 → §A8
  │    └─ ret == -EPROBE_DEFER → 立刻 break，整条路径失败
  ├─ mutex_unlock(&pinctrl_maps_mutex)
  ├─ if (ret) { pinctrl_free(p, false); return ERR_PTR(ret); }
  ├─ if (list_empty(&p->states)) → dev_dbg + ERR_PTR(-ENODEV)
  ├─ kref_init(&p->users)
  └─ mutex_lock(&pinctrl_list_mutex) → list_add_tail(&p->node, &pinctrl_list) → unlock
```

三条要点：

| 要点 | 依据 / 后果 |
|---|---|
| **句柄按设备去重** | `find_pinctrl()` + `kref_get`。同一设备第二次 `pinctrl_get()` 是 O(n) 链表查找 + 加引用，**不重新解析 mapping 表**。所以驱动可以放心多次调用 |
| **"一条 map 都没匹配到"返回 `-ENODEV`** | 这是 §A5 陷阱 1 第一行的来源，也是 §A3 里 hog 认领把 `-ENODEV` 当正常的来源 |
| **整段过滤持 `pinctrl_maps_mutex`** | 而 `add_setting()` 内部会调 `get_pinctrl_dev_from_devname()`（`core.c:101-120`），后者拿 `pinctrldev_list_mutex` → **锁顺序是 `pinctrl_maps_mutex` → `pinctrldev_list_mutex`**。见 §B1 |

**`pctldev` 这个参数的作用**：只有 hog 路径（§A3）会传非 NULL。传了之后 `add_setting()` 会把非本控制器的 map 跳过——因为 hog 只认领自己的引脚。

## §A7 DT → mapping 表 —— `pinctrl_dt_to_map()`（`drivers/pinctrl/devicetree.c:201-291`）

```
pinctrl_dt_to_map(p, pctldev)                  devicetree.c:201-291
  ├─ np = p->dev->of_node;  if (!np) return 0;   无 DT 设备直接放过
  ├─ 读 "pinctrl-names" 属性（可选）
  ├─ for (state = 0; ; state++):
  │    ├─ propname = kasprintf("pinctrl-%d", state)
  │    ├─ prop = of_find_property(np, propname)
  │    ├─ if (!prop):
  │    │     ├─ state == 0 → 完全没有 pinctrl-0，直接 return 0（不是错误）
  │    │     └─ state > 0  → break（枚举结束）
  │    ├─ 状态名：从 pinctrl-names 里取第 state 个；取不到就用 "%d" 十进制串
  │    │     所以没写 pinctrl-names 时状态名是 "0"、"1"，不是 "default"！
  │    └─ for each phandle in prop:
  │          └─ dt_to_map_one_config(p, pctldev, statename, np_config)
  └─ return 0

dt_to_map_one_config(p, hog_pctldev, statename, np_config)   devicetree.c:113-184
  ├─ 沿 np_config 向上找 pin controller 节点：
  │    while (np_pctldev):
  │      ├─ np_pctldev = of_get_next_parent(np_pctldev)
  │      ├─ !np_pctldev || !of_node_get(np_pctldev) → dev_err + -ENODEV
  │      ├─ pctldev = get_pinctrl_dev_from_of_node(np_pctldev)   core.c:123-137
  │      └─ if (pctldev) break;
  │           找不到就继续往上一层父节点找（支持嵌套 pinctrl 节点）
  ├─ if (!pctldev) → return -EPROBE_DEFER
  ├─ ops = pctldev->desc->pctlops
  ├─ if (!ops->dt_node_to_map) → dev_err + -ENODEV
  ├─ ops->dt_node_to_map(pctldev, np_config, &map, &num_maps)  ← 驱动回调
  ├─ if (num_maps == 0) → dt_remember_dummy_state()  devicetree.c:187-198
  │       生成一条 PIN_MAP_TYPE_DUMMY_STATE 占位
  ├─ 补齐 map 里驱动没填的字段：
  │    map[i].dev_name = dev_name(p->dev)
  │    map[i].name     = statename
  │    map[i].ctrl_dev_name = dev_name(pctldev->dev)  （驱动没填时）
  └─ dt_remember_or_free_map(p, statename, pctldev, map, num_maps)  devicetree.c:68-101
        ├─ kzalloc(struct pinctrl_dt_map)                devicetree.c:23-28
        ├─ dt_map->pctldev = pctldev;  dt_map->map = map;  dt_map->num_maps = num_maps
        ├─ list_add_tail(&dt_map->node, &p->dt_maps)      记进"欠账本"
        └─ pinctrl_register_map(map, num_maps, false)     false = 不再校验一遍
```

**三条容易写错的**：

1. **状态名的来源有两套**。有 `pinctrl-names` 就用里面的字符串；没有就用 `"0"`、`"1"` 这样的**十进制序号字符串**。所以「只写 `pinctrl-0` 不写 `pinctrl-names`」的设备，状态名是 `"0"` 而不是 `"default"`——然后 §A5 的 lookup("default") 必然失败，走静默失效路径
2. **控制器是沿 DT 父链向上找的**，不是 phandle 直接指向控制器。所以 `pinctrl-0 = <&uart0_pins>` 里的 `uart0_pins` 是 pin controller 节点的**子节点**（或更深的后代）
3. **`num_maps == 0` 不是错误**，核心会替它造一条 dummy state。这是给"这个状态什么都不用做"留的口子

**`pinctrl_register_map(..., false)` 的第三个参数**（`dup_maps`）为 false 表示"不复制、不重新校验"——DT 路径的校验已经在 `dt_node_to_map` 里由驱动负责了。板级路径（§A4）传的是 true。

## §A8 mapping → setting —— `add_setting()`（`core.c:953-1014`）

```
add_setting(p, pctldev, map)                           core.c:953-1014
  ├─ state = find_state(p, map->name)  ?: create_state(p, map->name)
  │     find_state    core.c:924-932   遍历 p->states 比对 name
  │     create_state  core.c:936-949   kzalloc + INIT_LIST_HEAD(&state->settings)
  ├─ if (map->type == PIN_MAP_TYPE_DUMMY_STATE) return 0;   占位状态到此结束
  ├─ kzalloc(struct pinctrl_setting)
  ├─ setting->type = map->type;  setting->dev_name = map->dev_name
  ├─ 找执行控制器：
  │    ├─ pctldev（hog 路径传进来的）非 NULL → 直接用
  │    └─ 否则 get_pinctrl_dev_from_devname(map->ctrl_dev_name)   core.c:101-120
  ├─ if (!setting->pctldev):
  │    ├─ 是 hog 路径 → return -ENODEV（"控制器还没准备好认领自己的 hog"）
  │    └─ 不是        → dev_info("unknown pinctrl device %s in map entry, deferring probe")
  │                     return -EPROBE_DEFER
  ├─ switch (setting->type):
  │    ├─ MUX_GROUP     → pinmux_map_to_setting(map, setting)    pinmux.c:342-398
  │    ├─ CONFIGS_PIN   → pinconf_map_to_setting(map, setting)   pinconf.c:109-142
  │    ├─ CONFIGS_GROUP → pinconf_map_to_setting(map, setting)
  │    └─ default       → ret = -EINVAL
  └─ list_add_tail(&setting->node, &state->settings)
```

### §A8.1 字符串 → selector：`pinmux_map_to_setting()`（`pinmux.c:342-398`）

**本子系统全部名字解析集中在这一个函数里**（配置侧的 `pinconf_map_to_setting()` 只解析 pin/group 名）：

```
pinmux_map_to_setting(map, setting)
  ├─ if (!pmxops) → dev_err("does not support mux function") + -EINVAL
  ├─ func_selector = pinmux_func_name_to_selector(pctldev, map->data.mux.function)
  │     pinmux.c:322-338
  │     ├─ 遍历 0..get_functions_count()-1 调 get_function_name() 比字符串
  │     └─ 没找到 → dev_err("function '%s' not supported") + -EINVAL
  ├─ 取 function 的组列表：ops->get_function_groups(pctldev, selector, &groups, &num_groups)
  ├─ if (!num_groups) → dev_err("function %s can't be selected on any group") + -EINVAL
  ├─ group 名字：
  │    ├─ map 里给了 → 在这个 function 的 groups[] 里找
  │    │     找不到 → dev_err("invalid group \"%s\" for function \"%s\"") + -EINVAL
  │    └─ map 里没给 → 取 groups[0]（"第一个可用的组"）
  ├─ group_selector = pinctrl_get_group_selector(pctldev, group)   core.c:724-747
  ├─ setting->data.mux.group = group_selector
  └─ setting->data.mux.func  = func_selector
```

**"map 没给 group 就取第一个"这条隐式行为**在 `struct pinctrl_map_mux.group` 的 kerneldoc 里写了（`include/linux/pinctrl/machine.h:28-30`："This field may be left NULL, and the first applicable group for the function will be used"）。DT 路径下驱动一般都会填，所以这条主要影响板级表。

**两次线性查找**：function 名一次（O(函数数)），group 名一次（O(该 function 的组数)）。高通 SoC 上 function 数是几百，所以**一个消费者的 probe 会做几百次 `strcmp`**。这就是 §1.5 讲的"把慢的事前移到 probe"的实际成本。

### §A8.2 配置侧：`pinconf_map_to_setting()`（`pinconf.c:109-142`）

```
pinconf_map_to_setting(map, setting)
  ├─ switch (setting->type):
  │    ├─ CONFIGS_PIN:
  │    │    pin = pin_get_from_name(pctldev, map->data.configs.group_or_pin)
  │    │      core.c:145-160，遍历 npins 比 pin_get_name()
  │    │    pin < 0 → dev_err("could not map pin config for \"%s\"") + 返回错误
  │    │    setting->data.configs.group_or_pin = pin
  │    └─ CONFIGS_GROUP:
  │         selector = pinctrl_get_group_selector(pctldev, group_or_pin)
  │         setting->data.configs.group_or_pin = selector
  ├─ configs = kmemdup(map->data.configs.configs, ...)   拷贝一份
  │     失败 → -ENOMEM
  ├─ setting->data.configs.num_configs = map->data.configs.num_configs
  └─ setting->data.configs.configs = configs
```

**`kmemdup` 这一步是 map 和 setting 生命周期解耦的关键**：驱动在 `dt_node_to_map()` 里 `kmalloc` 的 configs 数组，可以在 `dt_free_map()` 里安全释放，因为核心已经拷了一份。见 `02-datastructures.md` §8。

## §A9 状态查找 —— `pinctrl_lookup_state()`（`core.c:1215-1230`）

```c
struct pinctrl_state *pinctrl_lookup_state(struct pinctrl *p, const char *name)
{
	struct pinctrl_state *state;

	state = find_state(p, name);
	if (!state) {
		if (pinctrl_dummy_state) {
			/* create dummy state */
			dev_dbg(p->dev, "using pinctrl dummy state (%s)\n", name);
			state = create_state(p, name);
		} else
			state = ERR_PTR(-ENODEV);
	}

	return state;
}
```

**`pinctrl_dummy_state` 全局开关的唯一作用就在这里**：打开后，任何状态名都"查得到"（返回一个空状态），`pinctrl_select_state()` 于是变成 no-op。这是给"引脚配置由 bootloader 做好、内核不该碰"的平台准备的逃生门。

全树只有一个调用者打开它：`drivers/pinctrl/renesas/core.c`（见 `01-api.md` §3）。**arm64 上没有平台用这条路**，所以 arm64 读者可以认为"状态名写错必然 `-ENODEV`"。

**这个函数不加任何锁**——它只读 `p->states` 链表，而这条链表在 `create_pinctrl()` 之后就不再变化（除了 dummy state 分支会 `create_state`，那是个已知的理论竞态，实践中因为 `pinctrl_dummy_state` 只在单线程早期启动阶段生效而不成问题）。

## §A10 状态切换入口 —— `pinctrl_select_state()`（`core.c:1349-1356`）与 `pinctrl_commit_state()`（`core.c:1248-1342`）

### §A10.1 入口只做一件事：去重

```c
int pinctrl_select_state(struct pinctrl *p, struct pinctrl_state *state)
{
	if (p->state == state)
		return 0;

	return pinctrl_commit_state(p, state);
}
```

**去重的粒度是"指针相同"，不是"内容相同"**。同一个句柄上反复 select 同一个 `pinctrl_state` 是免费的；但如果你拿了两个句柄（比如一个来自 `dev->pins->p`、一个是你自己 `pinctrl_get()` 的），它们各自的 `pinctrl_state` 是两个不同对象，即使 DTS 里指的是同一个节点，也会各切一次。

`pinctrl_commit_state()` 是 `static`，但有三个内部调用者绕过了去重：`pinctrl_force_sleep()`（`core.c:1511-1515`）、`pinctrl_force_default()`（`core.c:1523-1527`）、以及 `pinctrl_commit_state()` 自己的回滚分支。见 §A14。

### §A10.2 五步流程

```
pinctrl_commit_state(p, state)
│
│ ① 拆旧状态里的 mux
│    old_state = READ_ONCE(p->state)
│    for each setting in old_state->settings:
│        if setting->type == PIN_MAP_TYPE_MUX_GROUP:
│            pinmux_disable_setting(setting)     ← 返回 void，失败只 warn（§A15）
│    只拆 mux，pinconf 一条都不动
│
│ ② p->state = NULL
│    这一行有两个作用：
│      (a) 标记"当前处于中间态"
│      (b) 让 ④ 的回滚里再调 pinctrl_select_state() 不会无限递归
│          （源码注释原文："There's no infinite recursive loop here
│            because p->state is NULL"）
│
│ ③ 第一遍：只应用 mux
│    for each setting in state->settings:
│        MUX_GROUP        → pinmux_enable_setting(setting)   （§A11）
│        CONFIGS_PIN/GROUP → ret = 0（跳过）
│        default          → ret = -EINVAL
│        if (ret < 0) goto unapply_new_state
│        if (p != setting->pctldev->p)
│            pinctrl_link_add(setting->pctldev, p->dev)      ← 建设备链接
│            注释："Do not link hogs (circular dependency)"
│
│ ④ 第二遍：只应用 pinconf
│    for each setting in state->settings:
│        CONFIGS_PIN/GROUP → pinconf_apply_setting(setting)  （§A12）
│        MUX_GROUP         → ret = 0（跳过）
│        default           → ret = -EINVAL
│        if (ret < 0) goto unapply_new_state
│
│ ⑤ p->state = state; return 0
```

**为什么必须分两遍？** 同一个 state 里 mux 项和 pinconf 项在链表上的顺序取决于 DTS 里子节点的书写顺序，是任意的。如果一遍扫完，就可能先把某个引脚的上下拉配好、再把它 mux 到另一个功能上——很多 SoC 的 mux 切换会重置该引脚的电气配置，于是 pinconf 白配了。分两遍把顺序钉死成 **先 mux 后 conf**，与 DTS 书写顺序无关。

### §A10.3 回滚路径 `unapply_new_state` —— 只能拆一半

```c
unapply_new_state:
	dev_err(p->dev, "Error applying setting, reverse things back\n");

	list_for_each_entry(setting2, &state->settings, node) {
		if (&setting2->node == &setting->node)
			break;
		/*
		 * All we can do here is pinmux_disable_setting.
		 * ... (We can't "unmux a pin"!)
		 */
		if (setting2->type == PIN_MAP_TYPE_MUX_GROUP)
			pinmux_disable_setting(setting2);
	}

	/* There's no infinite recursive loop here because p->state is NULL */
	if (old_state)
		pinctrl_select_state(p, old_state);

	return ret;
```

三条要点：

1. **只回滚 mux，不回滚 pinconf**。源码注释自己承认了原因——pinconf 没有"撤销"语义：核心不知道你把上拉打开之前它是什么值，也没有存过。所以第二遍中途失败，**已经写下去的 pinconf 永久留在硬件上**。
2. **循环的终止条件是"指针撞上出错的那一条"**（`&setting2->node == &setting->node`），因此第二遍失败时，这个循环会把**第一遍成功应用的全部 mux 都扫一遍并 disable**——这一半是干净的。
3. **最后重新 select 旧状态**。此时 `p->state` 已被置 NULL，所以 `pinctrl_select_state()` 的去重不会短路，会真的走一遍 commit。但这一次 commit 自己也可能失败，而它的返回值被丢弃了——**回滚失败是静默的**。

**给读者的结论**：`pinctrl_select_state()` 返回非 0 之后，硬件处于什么状态是**不确定**的。文档能给的最强保证只有"mux 尽力恢复、pinconf 不保证"。这条进 `07-debug.md` §5 的分诊表。

## §A11 应用一条 mux —— `pinmux_enable_setting()`（`pinmux.c:406-484`）与 `pin_request()`（`pinmux.c:111-194`）

### §A11.1 `pinmux_enable_setting()` 四步

```
pinmux_enable_setting(setting)
│
│ ① pctlops->get_group_pins(pctldev, group, &pins, &num_pins)
│    失败时：dev_warn "could not get pins for group %s"
│            并把 num_pins 置 0 —— 不返回错误！
│    后果：一个不实现 get_group_pins 的控制器，
│      整个 mux 的"占用登记"会被静默跳过，只剩 ④ 的 set_mux 生效
│
│ ② for i in 0..num_pins-1:
│        pin_request(pctldev, pins[i], setting->dev_name, NULL)
│                                       ↑ gpio_range = NULL，走 mux 路径
│        失败：dev_err "could not request pin %d (%s) from group %s on device %s"
│              goto err_pin_request
│
│ ③ for i in 0..num_pins-1:
│        desc = pin_desc_get(pctldev, pins[i])
│        desc->mux_setting = &(setting->data.mux)   ← 存的是指针，不是拷贝
│
│ ④ ops->set_mux(pctldev, setting->data.mux.func, setting->data.mux.group)
│    失败：goto err_set_mux
│
│ return 0
│
err_set_mux:
│    for i: desc->mux_setting = NULL          ← 清占用标记
err_pin_request:
│    while (--i >= 0) pin_free(pctldev, pins[i], NULL);
│    return ret
```

**③ 存的是指针**：`desc->mux_setting = &(setting->data.mux)` 指向 `pinctrl_setting` 内部。`pinmux_disable_setting()` 靠**比较这个指针**来判断"这个引脚是不是我占的"（§A15）。这解释了一件反直觉的事：**两个内容完全相同的 setting，指针不同，互相认不出**。

**整条路径不持任何锁**。`pin_request()` 里的 `desc->mux_usecount++`、`desc->mux_owner = owner` 都是裸的读改写。见 §B1。

### §A11.2 `pin_request()` —— 引脚占用的唯一登记点

`pin_request()` 是 `static`，两条入口：mux 路径（`gpio_range == NULL`，来自 §A11.1）和 GPIO 路径（`gpio_range != NULL`，来自 `pinmux_request_gpio()` `pinmux.c:265-279`）。它的第一行就定下了错误码基调：

```c
int status = -EINVAL;
```

**所有失败分支都落在 `-EINVAL`**，包括"引脚已被别人占用"。**不是 `-EBUSY`**。见 `01-api.md` §11.2 第 ⑤ 组。

冲突检测两道门，条件写得很密，展开如下：

| 门 | 源码条件 | 含义 |
|---|---|---|
| 门 1（mux 侧） | `(!gpio_range \|\| ops->strict) && desc->mux_usecount && strcmp(desc->mux_owner, owner)` | **非 strict 控制器上，GPIO 请求不看 mux 占用**——`!gpio_range` 为假就整条短路了 |
| 门 2（GPIO 侧） | `(gpio_range \|\| ops->strict) && desc->gpio_owner` | **非 strict 控制器上，mux 请求不看 GPIO 占用** |

两道门合起来的真值表（`ops->strict` 是控制器的 `pinmux_ops.strict` 字段）：

| 谁在申请 | `strict = false` | `strict = true` |
|---|---|---|
| mux 申请、引脚已被 mux 占（不同 owner） | ✗ 拒绝（门 1） | ✗ 拒绝（门 1） |
| mux 申请、引脚已被 mux 占（**同一 owner**） | ✓ 允许，`mux_usecount++` | ✓ 允许，`mux_usecount++` |
| mux 申请、引脚已被 GPIO 占 | **✓ 允许**（门 2 短路） | ✗ 拒绝（门 2） |
| GPIO 申请、引脚已被 mux 占 | **✓ 允许**（门 1 短路） | ✗ 拒绝（门 1） |
| GPIO 申请、引脚已被 GPIO 占 | ✗ 拒绝（门 2） | ✗ 拒绝（门 2） |

**`strict` 就是这张表的开关，它不是"更严格的检查"而是"打开跨类别检查"**。绝大多数 arm64 SoC 驱动不设 `strict`（默认 false），因此 **mux 和 GPIO 可以同时声称占有同一个引脚而无人报错**。这一条是 pinctrl 调试里最容易踩空的地方，进 `07-debug.md`。

第二部分是引用计数与回调：

```c
	if (gpio_range) {
		desc->gpio_owner = owner;
	} else {
		desc->mux_usecount++;
		if (desc->mux_usecount > 1)
			return 0;          /* 早返回：不调 ops->request，不 try_module_get */

		desc->mux_owner = owner;
	}

	/* Let each pin increase references to this module */
	if (!try_module_get(pctldev->owner)) { ... status = -EINVAL; goto out_free_pin; }

	if (gpio_range && ops->gpio_request_enable)
		status = ops->gpio_request_enable(pctldev, gpio_range, pin);
	else if (ops->request)
		status = ops->request(pctldev, pin);
	else
		status = 0;           /* 没有 request 回调 = 默认成功 */
```

四条要点：

1. **`mux_usecount > 1` 时直接 `return 0`**——不调 `ops->request()`、不 `try_module_get()`。所以"同一个 owner 重复 request 同一个引脚"只有第一次会打到驱动。
2. **GPIO 路径没有引用计数**，只有一个 `gpio_owner` 指针。所以 GPIO 侧是"要么空、要么被一个人占"，没有嵌套。
3. `try_module_get(pctldev->owner)` 是**每个引脚一次**（注释原文 "Let each pin increase references to this module"），不是每个 setting 一次。
4. **没有 `request` 回调等于默认成功**（源码注释："If there is no kind of request function for the pin we just assume we got it by default and proceed."）。这解释了为什么 `pinmux_check_ops()` 不把 `request` 列为必填（§A2.1）。

失败清理 `out_free_pin:` 把 `gpio_owner` 置 NULL 或把 `mux_usecount--`（归零时连 `mux_owner` 一起清），然后 `out:` 统一再打一条 `dev_err "pin-%d (%s) status %d"`。**一次失败会打两条 `dev_err`**（具体原因一条 + 这条汇总），排查时不要以为是两个独立故障。

## §A12 应用一条 pinconf —— `pinconf_apply_setting()`（`pinconf.c:149-198`）

结构比 mux 简单得多，四个错误分支全部是 `-EINVAL`：

| 分支 | 条件 | `dev_err` 消息 | 返回 |
|---|---|---|---|
| 无 confops | `!ops` | `missing confops` | `-EINVAL` |
| pin 级、无回调 | `CONFIGS_PIN` 且 `!ops->pin_config_set` | `missing pin_config_set op` | `-EINVAL` |
| pin 级、回调失败 | `ops->pin_config_set()` 返回非 0 | `pin_config_set op failed for pin %d` | 回调的返回值 |
| group 级、无回调 | `CONFIGS_GROUP` 且 `!ops->pin_config_group_set` | `missing pin_config_group_set op` | `-EINVAL` |
| group 级、回调失败 | `ops->pin_config_group_set()` 返回非 0 | `pin_config_group_set op failed for group %d` | 回调的返回值 |
| 类型不认识 | `default:` | 无消息 | `-EINVAL` |

**`pinconf_apply_setting()` 不做任何"引脚是否被占用"的检查**。pinconf 完全不参与 §A11.2 的所有权体系——它直接调回调写硬件。所以**一个没有出现在任何 mux 组里的引脚，照样可以被 pinconf 配置**。这是设计而非漏洞（很多板子需要把未使用引脚统一配成下拉以省电），但它意味着 `debugfs/pinctrl/*/pins` 里"未占用"的引脚也可能已经被写过配置。

一次调用传的是**整个 configs 数组**（`setting->data.configs.configs`、`num_configs`），不是逐条。驱动的 `pin_config_set()` 要自己遍历。见 `01-api.md` §10.2 关于旧签名的说明、`99-evidence.md` §2.3 第 ② 条（`pin-control.rst` 的签名滞后）。

## §A13 probe 结束后的补台 —— `pinctrl_init_done()`（`core.c:1539-1560`）

调用点是 `drivers/base/dd.c:684`，在驱动的 `probe()` **返回成功之后**。它处理的是 §A5 里 `init` 状态的收尾：

```c
	if (!pins) return 0;                              /* 没走过 pinctrl_bind_pins */
	if (IS_ERR(pins->init_state)) return 0;           /* 没有 init 状态 */
	if (pins->p->state != pins->init_state) return 0; /* 驱动自己已经切走了 */
	if (IS_ERR(pins->default_state)) return 0;        /* 没有 default 状态 */

	ret = pinctrl_select_state(pins->p, pins->default_state);
	if (ret) dev_err(dev, "failed to activate default pinctrl state\n");
```

**`init` 状态的完整语义**：DTS 里同时写了 `pinctrl-names = "init", "default"` 时，**probe 期间引脚处于 `init` 配置，probe 成功返回后自动切到 `default`**。用途是那种"上电瞬间引脚必须是安全态（比如全下拉），驱动初始化好硬件后才能接上真正的功能"的外设。

**第三个 `if` 是逃生门**：驱动在 probe 里自己 `pinctrl_select_state()` 切过状态的话，`pins->p->state` 就不再等于 `init_state`，这里就不动它。所以驱动可以自己决定切换时机。

`pinctrl_select_bound_state()`（`core.c:1564-1575`）是 `pinctrl_select_default_state()` 和三个 PM 函数的公共实现，它**不检查 `dev->pins` 是否为 NULL**（直接 `pins->p`），所有调用者都得自己先判。见 §A14。

## §A14 PM 状态切换（`core.c:1582-1625`）与控制器侧的强制切换（`core.c:1511-1527`）

### §A14.1 消费者侧的四个函数

```
pinctrl_select_default_state(dev)         core.c:1582-1587   任何时候可调
pinctrl_pm_select_default_state(dev)      core.c:1597-1599   #ifdef CONFIG_PM
pinctrl_pm_select_sleep_state(dev)        core.c:1607-1612   #ifdef CONFIG_PM
pinctrl_pm_select_idle_state(dev)         core.c:1620-1625   #ifdef CONFIG_PM
        └─ 全部 → pinctrl_select_bound_state(dev, dev->pins-><某个>_state)
                     └─ if (IS_ERR(state)) return 0;   ← 状态不存在 = 成功
                        pinctrl_select_state(pins->p, state)
```

**`pinctrl_pm_select_default_state()` 就是 `pinctrl_select_default_state()` 的一行转发**（`core.c:1597-1599` 函数体只有 `return pinctrl_select_default_state(dev);`）。两个名字是历史包袱，不是两套机制。区别只有一个：带 `pm_` 的三个在 `!CONFIG_PM` 时是 `include/linux/pinctrl/consumer.h` 里的 `static inline` 空壳。

**"状态不存在返回 0"是这四个函数的共同语义**。DTS 里没写 `sleep` 状态时，`pinctrl_pm_select_sleep_state()` 返回 0——**看起来成功了，其实什么都没做**。这与 §A5.1 陷阱 2（`dev->pins` 整个为 NULL 时全部 no-op）是两个不同的静默失效，都要在 `07-debug.md` 的分诊表里。

### §A14.2 控制器侧的两个函数 —— hog 状态的强制切换

```c
int pinctrl_force_sleep(struct pinctrl_dev *pctldev)
{
	if (!IS_ERR(pctldev->p) && !IS_ERR(pctldev->hog_sleep))
		return pinctrl_commit_state(pctldev->p, pctldev->hog_sleep);
	return 0;
}
/* pinctrl_force_default() 同构，用 pctldev->hog_default */
```

三条要点：

1. **对象是控制器自己 hog 的引脚**（§A3），不是消费者的。参数是 `pinctrl_dev *` 而不是 `device *`。
2. **直接调 `pinctrl_commit_state()` 而不是 `pinctrl_select_state()`**——绕过了"指针相同就返回 0"的去重。原因：resume 之后寄存器内容已经丢了，即使 `p->state` 还记着"当前是 default"，也必须真的重写一遍硬件。
3. **这两个函数导出了（`EXPORT_SYMBOL_GPL`，`core.c:1516/1528`）但只在私有头 `core.h:239/240` 声明**。使用者是 6 个控制器驱动：`pinctrl-single.c:1712/1726`、`pinctrl-rockchip.c:3352/3365/3386`、`qcom/pinctrl-msm.c:1450/1457`、`freescale/pinctrl-imx.c:892/899`、`nomadik/pinctrl-nomadik.c:1824/1835`、`tegra/pinctrl-tegra.c:776`。树外驱动想用就得自己抄声明——这是"符号存在≠读者能用"的典型例子，见 `05-usage.md` §6。

## §A15 拆一条 mux —— `pinmux_disable_setting()`（`pinmux.c:487-534`）与 `pin_free()`（`pinmux.c:209-253`）

### §A15.1 `pinmux_disable_setting()` 返回 void

```
pinmux_disable_setting(setting)
│
│ ① pctlops->get_group_pins(...)   失败 → dev_warn + num_pins = 0（同 §A11.1）
│
│ ② for each pin:
│        desc = pin_desc_get(...)
│        if (desc == NULL) { dev_warn "could not get pin desc..."; continue; }
│        if (desc->mux_setting == &(setting->data.mux))
│                pin_free(pctldev, pins[i], NULL);
│        else
│                dev_warn "not freeing pin %d (%s) as part of deactivating
│                          group %s - it is already used for some other setting"
```

**返回 void 意味着状态切换的"拆"这一半永远不会报错**。`pinctrl_commit_state()` 的第 ① 步（§A10.2）拿不到任何失败信号。所有异常只体现为 `dev_warn`，需要 `dmesg` 才能看见。

**`desc->mux_setting == &(setting->data.mux)` 这个指针比较是"是不是我占的"的唯一判据**。不相等就打那条 `not freeing pin ... it is already used for some other setting` 的 warn 并跳过。这条 warn 在实践中最常见的成因是 §A11.2 的非 strict 跨类别放行：GPIO 侧把 `mux_setting` 抢了/清了，mux 侧拆的时候就认不出来。

**注意 `ops->set_mux` 没有对偶**——disable 只做"归还占用登记"，**不会把硬件的 mux 寄存器改回去**。引脚的功能选择一直停在最后一次 `set_mux()` 写下的值，直到下一次 `set_mux()`。这是 `pinctrl_commit_state()` 回滚注释里那句 "We can't 'unmux a pin'!" 的硬件层根源。

### §A15.2 `pin_free()` 与 `pin_request()` 的不对称

`pin_free()` 是 `static`，**返回的是 owner 字符串（`const char *`）而不是错误码**——调用者用它来知道"刚释放的是谁的"（只有 `pinmux_free_gpio()` `pinmux.c:289-294` 真的用了返回值）。

```c
	desc = pin_desc_get(pctldev, pin);
	if (desc == NULL) {
		dev_err(pctldev->dev, "pin is not registered so it cannot be freed\n");
		return NULL;
	}

	if (!gpio_range) {
		/*
		 * A pin should not be freed more times than allocated.
		 */
		if (WARN_ON(!desc->mux_usecount))
			return NULL;
		desc->mux_usecount--;
		if (desc->mux_usecount)
			return NULL;          /* 还有别人占着，不调回调 */
	}
```

然后才是回调与清理：

```c
	if (gpio_range && ops->gpio_disable_free)
		ops->gpio_disable_free(pctldev, gpio_range, pin);
	else if (ops->free)
		ops->free(pctldev, pin);

	if (gpio_range) {
		owner = desc->gpio_owner;
		desc->gpio_owner = NULL;
	} else {
		owner = desc->mux_owner;
		desc->mux_owner = NULL;
		desc->mux_setting = NULL;
	}

	module_put(pctldev->owner);
```

与 `pin_request()` 的对称性检查表：

| 项 | `pin_request()` | `pin_free()` | 对称？ |
|---|---|---|---|
| mux 引用计数 | `++`，`>1` 时早返回 | `--`，非 0 时早返回 | ✓ |
| 计数下溢 | — | `WARN_ON(!desc->mux_usecount)` | ✓（有防护） |
| GPIO 引用计数 | 无（只有 owner 指针） | 无 | ✓ |
| 模块引用 | `try_module_get()` 每引脚一次 | `module_put()` 每引脚一次 | ✓ |
| 驱动回调 | `gpio_request_enable` / `request` / 无=成功 | `gpio_disable_free` / `free` / 无=跳过 | ✓ |
| `mux_setting` | 由 `pinmux_enable_setting()` 设（不在 `pin_request` 里） | 由 `pin_free()` 清 | ✗ **设与清不在同一层** |
| 失败可见性 | 返回错误码，两条 `dev_err` | 返回 owner 字符串，异常只有 `dev_err`/`WARN_ON` | ✗ |

表里两个 ✗ 就是 §A11.1/§A15.1 那些 warn 的机制根源：**`mux_setting` 在 `pinmux.c:406-484` 里被设置、在 `pinmux.c:209-253` 里被清除，中间隔了一层**，任何绕过 `pinmux_enable_setting()` 的路径（GPIO 桥接就是）都会让这个字段与实际占用脱节。

## §A16 释放句柄 —— `pinctrl_put()`（`core.c:1203-1205`）到 `pinctrl_free()`（`core.c:1164-1185`）

```
pinctrl_put(p)                    core.c:1203-1205
└─ kref_put(&p->users, pinctrl_release)
   └─ pinctrl_release(kref)       core.c:1192-1196   ← 计数归零才走到这
      └─ pinctrl_free(p, true)    core.c:1164-1185
         │
         │ mutex_lock(&pinctrl_list_mutex)          这里有锁
         │ for each state in p->states:
         │     for each setting in state->settings:
         │         pinctrl_free_setting(state == p->state, setting)
         │                          ↑ 只有"当前生效的状态"才 disable
         │         list_del + kfree(setting)
         │     list_del + kfree(state)
         │ pinctrl_dt_free_maps(p)                  devicetree.c:51-63
         │ if (inlist) list_del(&p->node)           ← 从全局 pinctrl_list 摘掉
         │ kfree(p)
         │ mutex_unlock(&pinctrl_list_mutex)
```

**`pinctrl_free_setting()`（`core.c:1147-1161`）的第一个参数 `disable_setting` 就是 `state == p->state`**：释放句柄时，只有那个正在生效的状态需要先 `pinmux_disable_setting()` 把引脚还回去；其他状态的 setting 从来没被应用过，只需要释放内存（`pinmux_free_setting()` / `pinconf_free_setting()`）。

**`pinctrl_put()` 只是减引用**。`pinctrl_get()` 对同一个 `(dev)` 返回的是同一个 `struct pinctrl`（`find_pinctrl()` `core.c:1017-1029` 按 `dev` 查全局链表），引用计数由 `create_pinctrl()`/`pinctrl_get()` 维护。所以两个模块各 `pinctrl_get()` 同一个设备，谁 put 都不会把对方的句柄拆掉。但 **devm 版和非 devm 版不能交叉释放**，见 `01-api.md` §10.2。

`pinmux_free_setting()`（`pinmux.c:401-403`）**函数体是空的**，只有一句注释 `/* This function is currently unused */`。它存在的意义是与 `pinconf_free_setting()`（要 `kfree` 那份 `kmemdup` 出来的 configs）保持结构对称。见 §C1。

## §A17 注销控制器 —— `pinctrl_unregister()`（`core.c:2201-2231`）

```
pinctrl_unregister(pctldev)
│ if (pctldev == NULL) return;
│
│ mutex_lock(&pctldev->mutex);
│   pinctrl_remove_device_debugfs(pctldev);       core.c:1939-1941
│ mutex_unlock(&pctldev->mutex);
│
│ if (!IS_ERR_OR_NULL(pctldev->p))
│     pinctrl_put(pctldev->p);                    ← 放掉自己 hog 的句柄（§A3）
│
│ mutex_lock(&pinctldev_list_mutex);              锁顺序：全局 → 单控制器
│ mutex_lock(&pctldev->mutex);
│   /* TODO: check that no pinmuxes are still active? */
│   list_del(&pctldev->node);
│   pinmux_generic_free_functions(pctldev);       pinmux.c:946-954
│   pinctrl_generic_free_groups(pctldev);         core.c:701-709
│   pinctrl_free_pindescs(pctldev, pctldev->desc->pins, pctldev->desc->npins);
│   /* 清空 gpio_ranges 链表 */
│ mutex_unlock(&pctldev->mutex);
│ mutex_destroy(&pctldev->mutex);
│ kfree(pctldev);
│ mutex_unlock(&pinctldev_list_mutex);
```

**`core.c:2216` 那行 `/* TODO: check that no pinmuxes are still active? */` 是这个子系统最重的一笔设计债**：注销控制器时**不检查还有没有消费者持着它的引脚**。消费者手里的 `pinctrl_setting.pctldev` 指针会立刻变成悬垂指针。实践中不炸是因为：

- 绝大多数 arm64 pin controller 驱动是 `builtin`（`bool` 型 Kconfig，不是 `tristate`），没有卸载路径；
- 有 `devm_pinctrl_register*()` 的驱动依赖 `pinctrl_link_add()`（`core.c:1235-1240`，在 §A10.2 第 ③ 步调用）建立的 device link，让消费者先被拆掉。

但这条链有个已知缺口：`pinctrl_commit_state()` 里那句注释 `/* Do not link hogs (circular dependency) */` —— **hog 不建 link**，所以控制器 hog 自己引脚这条路上没有保护。

**锁顺序在这里是 `pinctrldev_list_mutex` → `pctldev->mutex`**，与 §A6 的 `pinctrl_maps_mutex` → `pinctrldev_list_mutex` 是两条不同的嵌套。全子系统一共只有三条嵌套，能排成一个全序（§B1），所以不会互锁。

拆除三步 `pinmux_generic_free_functions` / `pinctrl_generic_free_groups` / `pinctrl_free_pindescs` 里，**前两个在关掉对应 Kconfig 时是空的 `static inline` stub**：`pinctrl_generic_free_groups` 的 stub 在 `core.c:712-714`（`#endif /* CONFIG_GENERIC_PINCTRL_GROUPS */` 在 `core.c:715`），`pinmux_generic_free_functions` 的整段实现在 `pinmux.c:789-956` 的 `CONFIG_GENERIC_PINMUX_FUNCTIONS` 里、stub 在 `drivers/pinctrl/pinmux.h:121-168` 的 `#else` 分支。见 `04-extensions.md` §1。

---

# Part B —— 横切机制

Part A 是一条时间线；Part B 的每一节都是**贯穿整条时间线的一个机制**。读 Part B 时不要按顺序，按需要查。

| 节 | 机制 | 在 Part A 的哪几点出现 |
|---|---|---|
| §B1 | 锁与并发 | 几乎每一节，但**状态应用路径一次锁都不拿** |
| §B2 | 引脚所有权模型 | §A11.2 / §A15 |
| §B3 | 三套引用计数 | §A11.2（`mux_usecount`、`module`）/ §A16（`kref`） |
| §B4 | `-EPROBE_DEFER` | §A5 / §A6 / §A8 |
| §B5 | hog 机制 | §A3 / §A14.2 |
| §B6 | GPIO 桥接 | 不在 Part A 时间线上——**它是一条平行入口** |
| §B7 | debugfs 注册时机 | §A3（`pinctrl_enable()` 里）/ §A1 |

## §B1 锁与并发

### §B1.1 四把锁

| 锁 | 定义 | 保护什么 | 粒度 |
|---|---|---|---|
| `pinctrldev_list_mutex` | `core.c:49` | 全局 `pinctrldev_list`（所有已注册控制器） | 全局 |
| `pinctrl_list_mutex` | `core.c:43` | 全局 `pinctrl_list`（所有已创建的消费者句柄） | 全局 |
| `pinctrl_maps_mutex` | `core.c:46`（非 static，`for_each_maps()` 宏在 `core.h` 里要用） | 全局 `pinctrl_maps`（板级 mapping 表） | 全局 |
| `pctldev->mutex` | `pinctrl_init_controller()` 里 `mutex_init` | 见 §B1.3 —— **不是"这个控制器的全部状态"** | 每控制器 |

**全子系统没有一个 spinlock**（`grep -c spin_lock drivers/pinctrl/{core,pinmux,pinconf,devicetree,pinconf-generic,pinctrl-utils}.c` = 0）。这是"pinctrl 只能在可睡眠上下文使用"的机制根源，见 `01-api.md` §12。

### §B1.2 三条嵌套，能排成全序

全树只有三处锁嵌套：

| 外层 | 内层 | 出现在 |
|---|---|---|
| `pinctrl_list_mutex` | `pinctrl_maps_mutex` | `pinctrl_free()`（`core.c:1168`）→ `pinctrl_dt_free_maps()`（`devicetree.c:51-63`）→ `pinctrl_unregister_mappings()`（`core.c:1493`） |
| `pinctrl_maps_mutex` | `pinctrldev_list_mutex` | `create_pinctrl()`（`core.c:1063`）→ `add_setting()`（`core.c:953-1014`）→ `get_pinctrl_dev_from_devname()`（`core.c:107`） |
| `pinctrldev_list_mutex` | `pctldev->mutex` | `pinctrl_ready_for_gpio_range()`（`core.c:340`/`345`）、`pinctrl_unregister()`（`core.c:2214`/`2215`） |

三条首尾相接、无环，得到唯一的全序：

```
pinctrl_list_mutex  >  pinctrl_maps_mutex  >  pinctrldev_list_mutex  >  pctldev->mutex
```

这个全序是**观察出来的，不是源码里声明的**——没有任何注释或 lockdep 标注把它写下来。所以它是"当前版本恰好如此"，改动 pinctrl 核心时没有机制会提醒你违反了它。

顺带一个陷阱：`pinctrl_free()` 持着 `pinctrl_list_mutex` 一路调到驱动的 `ops->dt_free_map()` 回调（`devicetree.c:41-43`）。**驱动的这个回调里不能做任何会回到 pinctrl 核心的事**。

### §B1.3 `pctldev->mutex` 的真实保护范围

`core.h:39` 的 kerneldoc 写的是 `@mutex: mutex taken on each pin controller specific action`。**这句话与代码不符**。把全部 `mutex_lock(&pctldev->mutex)` 落点列出来（`grep -rn 'mutex_lock(&pctldev->mutex)' drivers/pinctrl/*.c`，共 19 处）：

| 落点 | 所在函数 | 类别 |
|---|---|---|
| `core.c:303` | `pinctrl_match_gpio_range()` | GPIO range 查找 |
| `core.c:345` | `pinctrl_ready_for_gpio_range()` | GPIO range 查找 |
| `core.c:414` | `pinctrl_add_gpio_range()` | GPIO range 增删 |
| `core.c:504` | `pinctrl_find_gpio_range_from_pin()` | GPIO range 查找 |
| `core.c:520` | `pinctrl_remove_gpio_range()` | GPIO range 增删 |
| `core.c:764` | `pinctrl_gpio_can_use_line()` | GPIO 桥接 |
| `core.c:799` | `pinctrl_gpio_request()` | GPIO 桥接 |
| `core.c:831` | `pinctrl_gpio_free()` | GPIO 桥接 |
| `core.c:854` | `pinctrl_gpio_direction()` | GPIO 桥接 |
| `core.c:913` | `pinctrl_gpio_set_config()` | GPIO 桥接 |
| `pinconf.c:83` | `pin_config_group_get()` | 读配置 |
| `core.c:1644` | `pinctrl_pins_show()` | debugfs |
| `core.c:1696` | `pinctrl_groups_show()` | debugfs |
| `core.c:1743` | `pinctrl_gpioranges_show()` | debugfs |
| `pinmux.c:549` | `pinmux_functions_show()` | debugfs |
| `pinmux.c:599` | `pinmux_pins_show()` | debugfs |
| `pinconf.c:310` | `pinconf_pins_show()` | debugfs |
| `core.c:2207` / `core.c:2215` | `pinctrl_unregister()` | 拆除 |

归纳成一句话：**`pctldev->mutex` 只覆盖 GPIO range 与 GPIO 桥接、debugfs、以及注销**。

**状态应用路径完全不在这张表里**：

```
pinctrl_select_state()  →  pinctrl_commit_state()  →  pinmux_enable_setting()
                                                   →  pin_request()
                                                   →  ops->set_mux()
                                                   →  pinconf_apply_setting()
                                                   →  ops->pin_config_set()
        ↑ 这一整条链，从头到尾不持任何锁
```

### §B1.4 四条后果

1. **控制器注册是串行的**（`pinctrl_enable()` 持 `pinctrldev_list_mutex`，`core.c:2121`），但**状态切换之间没有任何互斥**。
2. **两个设备同时 `pinctrl_select_state()`、且它们的 mux 组共享引脚时，`desc->mux_usecount++` 是裸的读改写**。arm64 上实践中不炸的原因是 probe 由 `deferred_probe` 串行驱动、运行期状态切换（PM）通常也在同一条路径上——**这是路径依赖，不是设计保证**。
3. **GPIO 路径持锁、mux 路径不持锁 → 冲突检测本身不可靠**。`pin_request()` 的两道门（§A11.2）读的是 `desc->mux_usecount` / `desc->gpio_owner`，一边有锁一边没有，检测结果本身可能是撕裂的。
4. **驱动回调按"调用时有没有持锁"分成两组**——这决定回调里能不能反过来调 pinctrl API：

| 组 | 回调 | 调用时是否持 `pctldev->mutex` |
|---|---|---|
| 不持锁 | `set_mux` / `request` / `free` / `pin_config_set` / `pin_config_group_set` / `dt_node_to_map` / `get_group_pins` / `get_function_groups` | ✗ 不持 |
| 持锁 | `gpio_request_enable` / `gpio_disable_free` / `gpio_set_direction` / `pin_config_group_get` / 各 `*_dbg_show` | ✓ 持 |
| 特殊 | `dt_free_map` | 不持 `pctldev->mutex`，但**经 `pinctrl_free()` 这条路时持 `pinctrl_list_mutex`** |

第二组的回调里**不能**再调 `pinctrl_gpio_*()` 或 `pinctrl_add_gpio_range()`——会自死锁（`mutex` 不可重入）。

### §B1.5 PREEMPT_RT 上的实际形态（本基线打了 RT 补丁栈）

本基线是打了 PREEMPT_RT 补丁的树，但 `drivers/pinctrl` 与 `include/linux/pinctrl` **一行未改**（`99-evidence.md` §1.1）。这不等于"RT 上没有区别"，区别在核心层与驱动层之间**分岔**：

| 层 | 用什么锁 | RT 上会怎样 | 后果 |
|---|---|---|---|
| **核心层** | 只有 mutex：三把全局锁 + `pctldev->mutex`（§B1.1）。`grep 'spin_lock\|raw_spin'` 六个核心文件为**空** | mutex 在 RT 上是可抢占的睡眠锁（本来就是） | **没有额外风险，也没有额外收益**。核心层的行为在 RT 和非 RT 上一致：全部 API 只能在可睡眠上下文调 |
| **驱动层** | 相当多的控制器驱动用 `raw_spinlock` 保护寄存器：`grep -rl 'raw_spinlock' drivers/pinctrl/` = **18 个文件**；另有 17 个文件只用普通 `spinlock_t`（不能直接 `grep -rl spinlock_t | wc -l`，那个数是 34，因为 `raw_spinlock_t` 里含有 `spinlock_t` 这个子串，两类会被算进同一个数） | `raw_spinlock` 在 RT 上**不会**被转换成 rt_mutex，仍然关抢占 | 回调里那段寄存器读改写在 RT 上依然是**不可抢占的临界区**，直接计入 RT 延迟预算 |

**arm64 上的实证**（`drivers/pinctrl/qcom/pinctrl-msm.c`）：`msm_pinmux_set_mux()` 用 `raw_spin_lock_irqsave(&pctrl->lock, flags)` 包住 `msm_readl_ctl()` → 改位 → `msm_writel_ctl()` 这一段；锁本身声明在 `:70` 的 `raw_spinlock_t lock;`。

由此得到两条对**写控制器驱动**的人有效的结论（对写消费者驱动的人无效——消费者只看到 mutex）：

1. **`set_mux` / `pin_config_set` 的实现体内不能睡眠**，即使核心调用它时并没有持任何 spinlock。原因是驱动自己会在里面取 `raw_spinlock`。典型踩法：用 `regmap` 访问 syscon 里的 mux 寄存器，而底层 regmap 是 I2C/SPI（会睡眠）——MMIO regmap 没这个问题。
2. **临界区长度就是 RT 延迟**。`msm_pinmux_set_mux()` 的临界区里只有两次 MMIO 读写，很短；但同一个驱动的 `msm_config_group_set()` 之类如果在锁内循环处理多个引脚，延迟会随引脚数增长。RT 上要量这个的话，观测点是驱动的回调而不是 pinctrl 核心。

反过来说：**`pinctrl_select_state()` 这条路在 RT 上不会给你带来惊喜**——它从头到尾只有 mutex，能睡就是能睡。RT 上真正的风险点全在控制器驱动的回调实现里。

## §B2 引脚所有权模型

每个 `pin_desc`（`core.h:159-171`）上有**两套并行、互不覆盖**的占用标记：

```
struct pin_desc {
        ...
        const char *mux_owner;                  ← mux 路径的占用者（设备名字符串）
        const struct pinctrl_setting_mux *mux_setting;   ← 指向占用它的那条 setting
        unsigned int mux_usecount;              ← mux 路径的引用计数
        const char *gpio_owner;                 ← GPIO 路径的占用者
        ...
};
```

| 维度 | mux 侧 | GPIO 侧 |
|---|---|---|
| 占用者字段 | `mux_owner` | `gpio_owner` |
| 引用计数 | `mux_usecount`（可嵌套） | **无**（要么空、要么被一个人占） |
| 谁设 | `pin_request()`（`pinmux.c:111-194`） | `pin_request()` 同一函数，`gpio_range != NULL` 分支 |
| 谁清 | `pin_free()`（`pinmux.c:209-253`） | `pin_free()` 同一函数 |
| 额外指针 | `mux_setting`（由 `pinmux_enable_setting()` 设、`pin_free()` 清——不同层，见 §A15.2） | 无 |
| 冲突检测 | 门 1（§A11.2） | 门 2（§A11.2） |

**`pinmux_ops.strict` 是"要不要跨类别检查"的开关**（真值表见 §A11.2）。默认 `false`，意味着**mux 和 GPIO 可以同时声称占有同一个引脚**。

pinconf 完全不参与这套所有权体系（§A12）。所以 `debugfs/pinctrl/*/pins` 显示"UNCLAIMED"的引脚，仍然可能已经被写过电气配置。

**`strict` 的全树使用情况**：29 个驱动设了 `.strict = true`。

```bash
grep -rn '\.strict[[:space:]]*=[[:space:]]*true' drivers/pinctrl/ --include=*.c | wc -l   # 29
```

其中与 arm64 相关的有 `stm32/pinctrl-stm32.c:898`、`sunxi/pinctrl-sunxi.c:912`、`mediatek/pinctrl-moore.c:421`、`renesas/pinctrl-rzg2l.c:781`、`renesas/pinctrl-rzv2m.c:737`、`visconti/pinctrl-common.c:276`、`uniphier/pinctrl-uniphier-core.c:695`、`pinctrl-apple-gpio.c:187`、`starfive/pinctrl-starfive-jh7100.c:691`、`aspeed/pinctrl-aspeed-g6.c:2735`、`nomadik/pinctrl-nomadik.c:1682` 等。**`qcom/pinctrl-msm.c` 和 `freescale/pinctrl-imx.c` 都没设**——这两家覆盖了 arm64 上最大的一批 SoC，所以"默认不做跨类别检查"在实践中是主流。

## §B3 三套引用计数

| 计数 | 类型 | 作用域 | 谁 ++ | 谁 -- | 归零时 |
|---|---|---|---|---|---|
| `pinctrl->users` | `struct kref` | 一个消费者句柄 | `create_pinctrl()`（`kref_init`，`core.c:1109`）/ `pinctrl_get()`（`kref_get`） | `pinctrl_put()`（`core.c:1203-1205`） | `pinctrl_release()` → `pinctrl_free(p, true)` |
| `pin_desc->mux_usecount` | `unsigned int` | 一个引脚的 mux 占用 | `pin_request()` 非 GPIO 分支 | `pin_free()` 非 GPIO 分支 | 才调 `ops->free()`、才清 `mux_owner` |
| 模块引用 | `try_module_get(pctldev->owner)` | 控制器驱动模块 | `pin_request()`，**每个引脚一次** | `pin_free()`，每个引脚一次 | — |

**三者是独立的**。`pinctrl_put()` 归零会走到 `pinctrl_free()`，后者对"当前生效状态"的 setting 调 `pinmux_disable_setting()`（§A16），从而间接把 `mux_usecount` 和模块引用一起放掉。但**对非当前状态的 setting，`mux_usecount` 本来就没加过**——它们从未被应用。

**没有引用计数的地方**：`pinctrl_dev` 自己（注销时直接 `kfree`，见 §A17 的 TODO）、`gpio_owner`（§B2）。

## §B4 `-EPROBE_DEFER` 机制

pinctrl 是 arm64 上 `-EPROBE_DEFER` 的一大产地。全部产生点：

| 位置 | 函数 | 触发条件 | 会不会往上传到驱动 |
|---|---|---|---|
| `core.c:988-990` | `add_setting()` | mapping 里写的控制器还没注册（`get_pinctrl_dev_from_devname()` 返回 NULL），**且不是 hog** | ✓（`create_pinctrl` 直接 `return ERR_PTR(ret)`） |
| `devicetree.c:141-146` | `dt_to_map_one_config()` | DT 里 `pinctrl-N` 指向的控制器节点还没有对应的 `pinctrl_dev` | ✓ |

`add_setting()` 里那个"不是 hog"的判断是 `if (!strcmp(map->ctrl_dev_name, map->dev_name)) return -ENODEV;`，注释写的是 `/* Do not defer probing of hogs (circular loop) */`。**hog 缺控制器时返回 `-ENODEV` 而不是 defer**——因为 hog 的 consumer 就是控制器自己，defer 会等自己，死循环。

**这两条 defer 能不能真的到达驱动，取决于入口**：

| 入口 | `-EPROBE_DEFER` 的命运 |
|---|---|
| `pinctrl_bind_pins()`（`drivers/base/pinctrl.c:22-105`，设备核心自动调） | ✓ 原样返回给 `really_probe()`，设备进 deferred list |
| 驱动自己调 `devm_pinctrl_get()` | ✓ 拿到 `ERR_PTR(-EPROBE_DEFER)`，**驱动必须自己 `return PTR_ERR(...)` 才有效** |
| `pinctrl_claim_hogs()`（`core.c:2073-2109`，控制器 enable 时） | 不可能是 defer（上面的 `-ENODEV` 分支挡掉了） |

驱动自己调时最常见的写法错误是 `if (IS_ERR(p)) { dev_warn(...); p = NULL; }` —— 把 defer 咽掉了，设备会带着错误的引脚配置继续 probe。见 `05-usage.md` §2 与 `07-debug.md` §4。

## §B5 hog 机制

"hog"（占用）指**控制器把自己的引脚配置在自己身上**。判据是 mapping 里 `dev_name == ctrl_dev_name`。

```
   板级 mapping / DTS 里控制器节点自己带 pinctrl-0
                     │
   pinctrl_enable(pctldev)          core.c:2111-2128
     └─ pinctrl_claim_hogs(pctldev) core.c:2073-2109
        ├─ pctldev->p = create_pinctrl(pctldev->dev, pctldev)
        │     ↑ 注意第二个参数非 NULL：create_pinctrl 会跳过
        │       "为本设备但由别的控制器服务"的 map（core.c:1077-1079）
        ├─ if (PTR_ERR(pctldev->p) == -ENODEV) → 没有 hog，正常返回 0
        ├─ pctldev->hog_default = pinctrl_lookup_state(pctldev->p, "default")
        ├─ pctldev->hog_sleep   = pinctrl_lookup_state(pctldev->p, "sleep")
        └─ pinctrl_select_state(pctldev->p, pctldev->hog_default)
```

四条要点：

1. **`-ENODEV` 是常态分支**，绝大多数控制器没有 hog。见 §A3。
2. **hog 的 `default` 和 `sleep` 两个状态被单独缓存在 `pctldev->hog_default` / `hog_sleep` 里**，供 §A14.2 的 `pinctrl_force_default()` / `pinctrl_force_sleep()` 用。**其他状态名不会被缓存**——控制器 hog 只支持这两个状态。
3. **hog 不建 device link**：`pinctrl_commit_state()` 里 `if (p != setting->pctldev->p) pinctrl_link_add(...)`，注释 `/* Do not link hogs (circular dependency) */`。所以 §A17 那条"靠 device link 保护注销顺序"的兜底在 hog 上是缺失的。
4. **hog 走的是和普通消费者完全相同的 `create_pinctrl` / `commit_state` 代码**，只是 consumer device 恰好等于 controller device。它不是一套独立机制。

## §B6 GPIO 桥接 —— 一条平行入口

**这是 pinctrl 唯一一条不经过 `pinctrl_map` / `pinctrl_state` / `pinctrl_setting` 的引脚控制入口**，所以它不在 Part A 的时间线上。调用方是 gpiolib，不是设备驱动。

```
gpiolib (drivers/gpio/gpiolib.c)
   │
   │  pinctrl_gpio_can_use_line(gpio)    core.c:750-774
   │  pinctrl_gpio_request(gpio)         core.c:786-809
   │  pinctrl_gpio_free(gpio)            core.c:821-839
   │  pinctrl_gpio_direction_input(gpio) core.c:874-876  ┐→ pinctrl_gpio_direction()
   │  pinctrl_gpio_direction_output(gpio) core.c:888-890 ┘   core.c:843-863
   │  pinctrl_gpio_set_config(gpio, cfg) core.c:903-919
   ↓
每个函数的固定三步：
   ① pinctrl_get_device_gpio_range(gpio, &pctldev, &range)   core.c:380-401
        └─ 遍历 pinctrldev_list，对每个控制器遍历 gpio_ranges
   ② mutex_lock(&pctldev->mutex)                    这条路径有锁（对比 §B1.3）
   ③ pin = gpio_to_pin(range, gpio)                 core.c:282-288
      调 pinmux_* / pinconf_* 的对应内部函数
   ④ mutex_unlock(&pctldev->mutex)
```

三条要点：

1. **编号空间转换在 `gpio_to_pin()`（`core.c:282-288`）里**。`pinctrl_gpio_range` 有两种表达 pin 集合的方式（连续 `pin_base` / 显式 `pins[]` 数组），二者互斥，图见 `02-datastructures.md` §3.7。
2. **找不到 range 时的行为是"静默成功"**：`pinctrl_gpio_request()` 拿不到 range 就调 `pinctrl_ready_for_gpio_range(gpio)`（`core.c:332-361`），如果**这块 gpio_chip 的编号区间与任何控制器的任何 range 有重叠**，就 `ret = 0` 返回。语义是"这个 GPIO 芯片确实有 pinctrl 后端，只是这一根引脚不由 pinctrl 管"。后果：**`gpio-ranges` 写漏一根引脚，`gpio_request()` 照样成功，引脚的 mux 却没被切走**。这是 `06-devicetree.md` 决策树里的一个 项。
3. `!CONFIG_GPIOLIB` 时 `pinctrl_ready_for_gpio_range()` 是 `static bool ... { return true; }`（`core.c:363`），即**无条件静默成功**。

## §B7 debugfs 注册时机

```
pinctrl_init()                 core.c:2334-2338    core_initcall
  └─ pinctrl_init_debugfs()    core.c:1944-1958
     └─ /sys/kernel/debug/pinctrl/
        ├─ pinctrl-devices     （0444）所有控制器 + 各自 ops 齐不齐
        ├─ pinctrl-maps        （0444）全局 mapping 表
        └─ pinctrl-handles     （0444）所有消费者句柄及其状态

pinctrl_enable(pctldev)        core.c:2111-2128    不是 register_and_init
  └─ pinctrl_init_device_debugfs(pctldev)   core.c:1900-1936（调用点 core.c:2125）
     └─ /sys/kernel/debug/pinctrl/<name>/
        ├─ pins                （0444）
        ├─ pingroups           （0444）
        ├─ gpio-ranges         （0444）
        ├─ pinmux-functions    （0444）仅当 desc->pmxops 非空
        ├─ pinmux-pins         （0444）仅当 desc->pmxops 非空
        ├─ pinmux-select       （0200）仅当 desc->pmxops 非空——唯一可写文件
        ├─ pinconf-pins        （0444）仅当 desc->confops 非空
        └─ pinconf-groups      （0444）仅当 desc->confops 非空
```

**目录名不一定是 `dev_name()`**：`pinctrl_init_device_debugfs()` 前 15 行做了一次判断——如果 `pctldev->desc->name` 存在且不等于 `dev_name(pctldev->dev)`，目录名就变成 `"<dev_name>-<desc->name>"`（`devm_kasprintf`）。所以路径要先 `ls` 再猜。

**注册时机的实际影响**：debugfs 目录在 `pinctrl_enable()` 里创建，而 `pinctrl_register_and_init()` 到 `pinctrl_enable()` 之间可能间隔很久（驱动要在这段里初始化硬件、注册 gpio range）。**如果驱动 probe 在这个窗口里失败，控制器已经在 `pinctrldev_list` 里（出现在 `pinctrl-devices`）但没有自己的目录**。见 §A3。

`pinmux-select`（`0200`，只写）是**全子系统唯一一个能从用户态改硬件的接口**，实现是 `pinmux_select()`（`pinmux.c:680-761`）：

```
写入格式：  "<group-name> <function-name>"     组名在前，功能名在后
上限：      PINMUX_SELECT_MAX = 128 字节（pinmux.c:677），超了返回 -ENOMEM
解析：      strstrip → 找第一个 isspace 断开 → skip_spaces
校验：      pinmux_func_name_to_selector()  → "invalid function %s in map table"
            pmxops->get_function_groups()   → "no groups for function %d (%s)"
            match_string(groups, ...)       → "invalid group %s"
            pinctrl_get_group_selector()    → "failed to get group selector for %s"
执行：      pmxops->set_mux(pctldev, fsel, gsel)  → "set_mux() failed: %d"
```

**它直接调 `set_mux()`，完全绕过 `pin_request()`**：不做所有权检查、不更新 `mux_owner`/`mux_usecount`/`mux_setting`、**也不持 `pctldev->mutex`**。后果：

- 用它改过 mux 之后，`debugfs/pinctrl/*/pinmux-pins` 显示的 owner 信息是**过期的**；
- 下一次任何设备 `pinctrl_select_state()` 会把你改的东西盖掉，而且不会警告；
- 它能把一个正在被别的设备使用的引脚抢走，没有任何检查。

所以这是**调试工具，不是配置接口**。详见 `07-debug.md` §3。

---

# Part C —— 评价

Part C 不讲"怎么工作"，只讲**这份代码自己承认或暴露出来的问题**。每一条都给出代码依据，读者据此判断哪些地方要额外小心。

## §C1 源码自陈的设计债

全树扫一遍（`grep -rn 'TODO\|FIXME\|REVISIT\|XXX\|currently unused' drivers/pinctrl/{core,pinmux,pinconf,devicetree,pinconf-generic,pinctrl-utils}.c drivers/pinctrl/{core,pinmux,pinconf}.h`），核心文件里只有三条显式标记，但每一条都很重：

| # | 位置 | 原文 | 后果 |
|---|---|---|---|
| 1 | `core.c:2216` | `/* TODO: check that no pinmuxes are still active? */` | 注销控制器时不检查是否还有消费者持着引脚 → 悬垂 `pinctrl_setting.pctldev`。见 §A17 |
| 2 | `pinconf.c:284-286` | `FIXME: We should really get the pin controller to dump the config` | debugfs 的 `pinconf-pins` 输出依赖驱动的 `pin_config_dbg_show`，驱动没实现就什么都看不到 |
| 3 | `pinmux.c:402` | `/* This function is currently unused */`（`pinmux_free_setting()` 函数体为空） | 与 `pinconf_free_setting()` 结构对称的空壳。见 §A16 |

另外四条**没有标记但同样是设计债**：

| # | 位置 | 问题 |
|---|---|---|
| 4 | `core.c:2130-2143` | `pinctrl_register()` 的 kerneldoc **自陈设计缺陷**：它把 register + enable 合成一步，因此控制器在 hog 生效之前就已经上线，驱动无法在两者之间插入硬件初始化。官方建议改用 `pinctrl_register_and_init()` + `pinctrl_enable()`。但全树仍有 8 个驱动调非 devm 的 `pinctrl_register()`（见 `01-api.md` §2.5） |
| 5 | `core.c:1248-1342` | 状态应用的回滚只能 disable mux，**pinconf 无法撤销**（源码注释 `We can't "unmux a pin"!`）。见 §A10.3 |
| 6 | 全部状态应用路径 | **一次锁都不拿**，而 `core.h:39` 的 kerneldoc 声称 `@mutex: mutex taken on each pin controller specific action`。见 §B1.3 |
| 7 | `pinmux.c:406-484` | `get_group_pins()` 失败只 `dev_warn` 并把 `num_pins = 0`，**不返回错误** → 占用登记被静默跳过。见 §A11.1 |

## §C2 观测能力缺口 —— 零 tracepoint

```bash
ls include/trace/events/ | grep -i pinctrl        # 空
grep -rn 'trace_[a-z_]*(' drivers/pinctrl/core.c drivers/pinctrl/pinmux.c \
     drivers/pinctrl/pinconf.c drivers/pinctrl/devicetree.c   # 空
```

**pinctrl 全子系统没有一个 tracepoint**。（`drivers/pinctrl/` 下 grep `trace_` 的 14 处命中全部来自 `pinctrl-pistachio.c` 里名为 `mips_trace_*` 的引脚组名，与 tracing 无关。）

对比同为框架型的邻居：

| 子系统 | tracepoint 文件 |
|---|---|
| clk | `include/trace/events/clk.h` |
| gpio | 也没有独立 tracepoint 文件 |
| regulator | `include/trace/events/regulator.h` |
| pinctrl | **没有** |

**后果与替代手段**：想知道"引脚什么时候被谁切成了什么"，本版本只有三条路：

1. `dmesg` 里的 `dev_dbg`（要开 `CONFIG_DYNAMIC_DEBUG` 并按文件打开，见 `07-debug.md` §1）；
2. `debugfs/pinctrl/*/pinmux-pins` 的**当前快照**（不是历史）；
3. `ftrace` 的 `function` tracer 直接跟 `pinmux_enable_setting` / `pin_request`（这两个都不是 `static inline`，可以被 ftrace 挂上）。

这一条决定了 `07-debug.md` 的整体形状：**pinctrl 的调试是"读快照 + 开 dyndbg"，没有事件流**。

## §C3 错误码单一化

```bash
grep -h 'EINVAL' drivers/pinctrl/{core,pinmux,pinconf,devicetree,pinconf-generic,pinctrl-utils}.c | wc -l
# 48
```

48 处 `-EINVAL`（`core.c` 19 / `pinmux.c` 13 / `pinconf.c` 9 / `pinconf-generic.c` 5 / `devicetree.c` 2 / `pinctrl-utils.c` 0），覆盖了**七组互不相关的语义**——完整分组见 `01-api.md` §11.2。这里只强调后果：

**`-EINVAL` 在 pinctrl 里不携带任何信息**。看到它必须去 `dmesg` 找配套的 `dev_err`，否则无法区分"DTS 写错了状态名"、"引脚被别的设备占了"、"驱动没实现某个回调"这三类完全不同的故障。

最容易误判的一条：**引脚冲突返回 `-EINVAL` 而不是 `-EBUSY`**（`pinmux.c:114` `int status = -EINVAL;`，所有失败分支都落在它上面）。全树 `drivers/pinctrl/{core,pinmux,pinconf}.c` 里**没有一处返回 `-EBUSY`**。

对照：`-ENOTSUPP` 在 `pinconf.c`（3 处）和 `pinconf-generic.c`（1 处）里出现，语义是"这个控制器不支持这个配置参数"。官方要求驱动的 `pin_config_get()` 对不支持的参数返回 `-ENOTSUPP` 而不是 `-EINVAL`——但 debugfs 的显示代码把两者**同等跳过**，所以驱动写错了也看不出来。见 `01-api.md` §11.3。

## §C4 两遍应用带来的回滚不完整

§A10 的"先 mux 后 conf"两遍设计解决了顺序依赖问题，代价是回滚语义的破碎：

```
        第一遍（mux）失败              第二遍（pinconf）失败
             │                              │
   已应用的 mux：全部 disable ✓     已应用的 mux：全部 disable ✓
   已应用的 pinconf：无（还没到）     已应用的 pinconf：永久留在硬件上
   旧状态：重新 select（返回值被丢弃）  旧状态：重新 select（返回值被丢弃）
```

三条对读者的实际影响：

1. **`pinctrl_select_state()` 返回非 0 之后硬件状态不确定**。能给的最强保证是"mux 尽力恢复、pinconf 不保证"。
2. **回滚本身失败是静默的**（`if (old_state) pinctrl_select_state(p, old_state);` 的返回值没有被检查）。
3. **`pinmux_disable_setting()` 返回 void**（§A15.1），所以"拆"这一半永远不报错，异常只有 `dev_warn`。

这不是可以简单修的 bug：pinconf 要能撤销，核心就得先把每个引脚的原始配置读出来存着（`pin_config_get` 是可选回调，很多驱动不实现），代价远大于收益。**它是一个明确的取舍，不是遗漏**——因此文档要教读者"select 失败就当整个设备不可用"，而不是教他们怎么恢复。

## §C5 泛型存储与自实现回调必须二选一

pinctrl 提供了两套并行的 group/function 存储实现：

| | 泛型存储 | 自己实现 |
|---|---|---|
| 数据结构 | `group_desc` / `function_desc` + radix tree（`core.h:185-220`、`drivers/pinctrl/pinmux.h:121-168`） | 驱动自己的数组 |
| Kconfig | `CONFIG_GENERIC_PINCTRL_GROUPS` / `CONFIG_GENERIC_PINMUX_FUNCTIONS` | — |
| 回调实现 | `pinctrl_generic_get_group_*()` / `pinmux_generic_get_function_*()` 直接填进 ops | 驱动自己写 |
| 注册 | `pinctrl_generic_add_group()` / `pinmux_generic_add_function()` 运行期逐个加 | 编译期静态表 |

**两套不能混用**：ops 里的 `get_groups_count` / `get_group_name` / `get_group_pins` 是**一组三个**，要么全部指向 `pinctrl_generic_*`（此时数据必须在 radix tree 里），要么全部自己实现（此时 radix tree 是空的）。混用的表现是"函数名查得到、pin 列表是空的"——因为两边读的是不同的数据源。

两个 Kconfig **互不相干**（可以只开一个），但绝大多数驱动是 `select` 两个一起开（`drivers/pinctrl/Kconfig` 里有 10 处成对出现）。

关掉 Kconfig 时，`pinctrl_generic_free_groups()` 变成空 `static inline`（`core.c:712-714`），`pinmux_generic_free_functions()` 的 stub 在 `pinmux.h` 的 `#else` 分支——**所以 §A17 的拆除步骤在这两种配置下做的事情完全不同**。详见 `04-extensions.md` §1。

## §C6 文档与代码不一致（五处）

这五处全部登记在 `99-evidence.md` §2.3，此处只列，不重复论证：

| # | 出处 | 说的是 | 代码实际 |
|---|---|---|---|
| ① | `include/linux/pinctrl/pinctrl.h` 的 `pinctrl_desc.pctlops` kerneldoc | 暗示可选 | `pinctrl_check_ops()`（`core.c:1977-1986`）**强制必填** |
| ② | `Documentation/driver-api/pin-control.rst` | `foo_pin_config_set(pctldev, offset, unsigned long config)` | 实际签名是 `unsigned long *configs, unsigned num_configs` |
| ③ | `pin-control.rst` 教驱动调 `pinctrl_add_gpio_range()` | 推荐做法 | 已被 `gpio-ranges` DT 属性取代，驱动侧只留回退路径（如 `qcom/pinctrl-msm.c:1388-1406`） |
| ④ | `include/linux/pinctrl/machine.h:60` 注释提到 `pinmux_lookup_state()` | 该函数名 | **全树不存在这个符号**，真名是 `pinctrl_lookup_state()` |
| ⑤ | `drivers/pinctrl/core.h:39` `@mutex: mutex taken on each pin controller specific action` | 每个控制器动作都持锁 | 状态应用路径**一次锁都不拿**（§B1.3） |

一条通用教训：**这五处里有四处（①②④⑤）是"文档说的比代码宽松/严格"，只有 ③ 是纯粹过时**。所以读 pinctrl 的官方文档时，**把它当"设计意图"读，不要当"接口契约"读**——契约在 `pinctrl_check_ops` / `pinmux_check_ops` / `pinconf_check_ops` 这三个函数里（§A2.1）。

---

## 下一步读什么

| 你现在想知道 | 去 |
|---|---|
| 泛型 group/function 存储、pinconf-generic、pinctrl-utils、GPIO 桥接的内部 | `04-extensions.md` |
| 我要写一个 pin controller 驱动 / 我要在消费者驱动里用 pinctrl | `05-usage.md` |
| DTS 属性怎么写、每条属性谁消费 | `06-devicetree.md` |
| debugfs 每个文件怎么读、dyndbg 怎么开、错误码怎么分诊 | `07-debug.md` |
| 为什么 `pinctrl` 不是 pin controller、为什么要分两遍应用 | `08-feynman.md` |
| 本篇每条论断的出处 | `99-evidence.md` |
