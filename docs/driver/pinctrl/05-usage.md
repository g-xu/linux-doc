---
title: 使用指南
---

# pinctrl 使用举例：五个角色的代码骨架

> **免责声明：本篇所有代码骨架都没有编译过、没有运行过。**
>
> 它们是从 in-tree 驱动提炼出来的**结构示范**，用来说明"哪些字段必填、哪些回调什么时候被调、错用会怎样"。
> - 正文里出现的每一个函数名 / 宏名 / 结构体字段 / 内核打印字符串，都在 6.1.158 树上 `grep` 确认过存在（口径见 `99-evidence.md` §3）；
> - 但**代码块本身**可能缺头文件、缺错误处理、缺 `static`，直接抄进内核不保证能编译。
> - 涉及具体寄存器操作的地方一律写成注释占位，因为那部分只能看你的 SoC 手册。

---

## §0 本篇有哪些角色

pinctrl 是**框架型子系统**（判定过程见 `99-evidence.md` §1），没有 `bus_type`，所以角色不是"控制器 / 设备"两分，而是按"你站在 API 的哪一侧"分：

| # | 角色 | 你要写什么 | 关键 API 方向 | 本篇章节 |
|---|---|---|---|---|
| 1 | **pin controller 驱动** | 填 `pinctrl_desc` + 三套 ops，调注册 API | **提供**服务 | §1 |
| 2 | **普通消费者驱动**（外设） | 多数情况下**一行都不用写** | **消费**服务 | §2 |
| 3 | **GPIO 控制器驱动** | 注册 gpio range + 在 request/direction 里回调 pinctrl | 双向 | §3 |
| 4 | **板级 / machine 代码** | 静态 `pinctrl_map[]` 数组 + `pinctrl_register_mappings()` | **提供**配方 | §4 |
| 5 | **DTS 作者** | `pinctrl-0` / `pinctrl-names` / 子节点属性 | **提供**配方 | 不在本篇，见 `06-devicetree.md` |

**角色 5 单独成篇**是因为 DTS 的语法、属性消费者、配错定位是一整套独立知识（`06-devicetree.md` 全篇）。这里只在 §2.1 提一句"消费者什么都不写就能工作"的机制来源是 DTS。

**arm64 上的现实分布**：99% 的读者是角色 2（而且什么都不用写），其次是角色 1（写 SoC 的 pinctrl 驱动）和角色 3。**角色 4 在 arm64 上基本绝迹**——ARM64 强制 DT/ACPI，没有 board file。§4 保留是因为 `pinctrl_register_mappings()` 仍然是导出 API 且有 in-tree 用户（见 §4.3）。

---

## §1 角色一：pin controller 驱动

### §1.1 最小骨架：填什么、调什么

```c
#include <linux/pinctrl/pinctrl.h>
#include <linux/pinctrl/pinmux.h>
#include <linux/pinctrl/pinconf.h>
#include <linux/pinctrl/pinconf-generic.h>

/* ---- ① 引脚清单：编译期静态表 ---- */
static const struct pinctrl_pin_desc mysoc_pins[] = {
	PINCTRL_PIN(0, "GPIO0"),
	PINCTRL_PIN(1, "GPIO1"),
	/* ... */
	PINCTRL_PIN_ANON(2),		/* 只有编号、没有名字也合法 */
};

/* ---- ② 三套 ops ---- */
static const struct pinctrl_ops mysoc_pctlops = {
	/* 这三个必填，pinctrl_check_ops() 会检查 —— 见 §1.3 */
	.get_groups_count	= pinctrl_generic_get_group_count,
	.get_group_name		= pinctrl_generic_get_group_name,
	.get_group_pins		= pinctrl_generic_get_group_pins,
	/* 走 DT 就填这两个；三套 ops 里只有 pctlops 管 DT */
	.dt_node_to_map		= pinconf_generic_dt_node_to_map_all,
	.dt_free_map		= pinconf_generic_dt_free_map,
	/* 可选：debugfs 的 pins 文件里每根引脚多打一行 */
	.pin_dbg_show		= NULL,
};

static const struct pinmux_ops mysoc_pmxops = {
	.get_functions_count	= pinmux_generic_get_function_count,
	.get_function_name	= pinmux_generic_get_function_name,
	.get_function_groups	= pinmux_generic_get_function_groups,
	.set_mux		= mysoc_set_mux,	/* 只能自己写 */
	/* 引脚要能当 GPIO 就填这两/三个 */
	.gpio_request_enable	= mysoc_gpio_request_enable,
	.gpio_disable_free	= mysoc_gpio_disable_free,
	.gpio_set_direction	= mysoc_gpio_set_direction,
	.strict			= true,		/* 见 §1.5 */
};

static const struct pinconf_ops mysoc_confops = {
	.is_generic		= true,		/* 见 §1.4 */
	.pin_config_get		= mysoc_pinconf_get,
	.pin_config_set		= mysoc_pinconf_set,
	.pin_config_group_get	= mysoc_pinconf_group_get,
	.pin_config_group_set	= mysoc_pinconf_group_set,
};

/* ---- ③ 描述符 ---- */
static const struct pinctrl_desc mysoc_desc = {
	.name		= "mysoc-pinctrl",
	.pins		= mysoc_pins,
	.npins		= ARRAY_SIZE(mysoc_pins),
	.pctlops	= &mysoc_pctlops,
	.pmxops		= &mysoc_pmxops,
	.confops	= &mysoc_confops,
	.owner		= THIS_MODULE,		/* 见 §1.6 */
	.link_consumers	= true,			/* 可选，见 §1.7 */
};

/* ---- ④ probe：三步，顺序不能换 ---- */
static int mysoc_pinctrl_probe(struct platform_device *pdev)
{
	struct mysoc_pinctrl *pc;
	int ret, i;

	pc = devm_kzalloc(&pdev->dev, sizeof(*pc), GFP_KERNEL);
	if (!pc)
		return -ENOMEM;
	pc->dev = &pdev->dev;
	pc->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(pc->base))
		return PTR_ERR(pc->base);

	/* 第 1 步：注册但不启用 —— 拿到 pctldev 句柄 */
	ret = devm_pinctrl_register_and_init(&pdev->dev, &mysoc_desc,
					     pc, &pc->pctldev);
	if (ret)
		return ret;

	/* 第 2 步：这个窗口里往泛型存储里塞 group / function */
	for (i = 0; i < pc->num_groups; i++) {
		ret = pinctrl_generic_add_group(pc->pctldev,
				pc->groups[i].name,
				pc->groups[i].pins,	/* int *，不是 unsigned * */
				pc->groups[i].npins,
				&pc->groups[i]);	/* data：驱动私有指针 */
		if (ret < 0)
			return ret;
	}
	for (i = 0; i < pc->num_funcs; i++) {
		ret = pinmux_generic_add_function(pc->pctldev,
				pc->funcs[i].name,
				pc->funcs[i].groups,
				pc->funcs[i].ngroups,
				&pc->funcs[i]);
		if (ret < 0)
			return ret;
	}

	/* 第 3 步：启用 —— 之后核心才允许消费者进来 */
	return pinctrl_enable(pc->pctldev);
}
```

### §1.2 三步顺序为什么不能换

```
   devm_pinctrl_register_and_init()          core.c:2296-2318
        │  内部调 pinctrl_register_and_init()  core.c:2162-2191
        │  → 分配 pctldev、拷 desc、建 radix tree、注册 pin desc
        │  → 不挂进 pinctrldev_list，不建 debugfs
        ▼
   ┌──── 窗口：pctldev 已存在，但对外不可见 ────┐
   │  pinctrl_generic_add_group()                │
   │  pinmux_generic_add_function()               │
   │  （或者：你自己的 ops 读的静态表已就绪）      │
   └─────────────────────────────────────────────┘
        ▼
   pinctrl_enable()                          core.c:2111-2128
        → 挂进 pinctrldev_list（持 pinctrldev_list_mutex）
        → pinctrl_init_device_debugfs()      core.c:2125
        → 从此刻起消费者的 devm_pinctrl_get() 才能找到你
```

**为什么必须留这个窗口**：完整因果链在 `03-internals.md` §A2 / §A3。一句话——`pinctrl_enable()` 之后随时可能有消费者进来调你的 `get_groups_count()`，如果 group 表还没填完，它读到的是半成品。而 `pinctrl_register()`（老接口）把两步合成一步，**没有这个窗口**，这正是它 kerneldoc 里自陈"known to have problems"的原因（`core.c:2130-2143`，原文见 `03-internals.md` §C1）。

### §1.3 必填回调清单：核心检查得比你想的松

| 检查函数 | 位置 | 真正检查什么 | 报错串 | 不满足的后果 |
|---|---|---|---|---|
| `pinctrl_check_ops()` | `core.c:1976-1986` | 只有 **两个**：`pctlops` 非空 + `get_groups_count` 非空 + `get_group_name` 非空 | 由调用方打：`dev_err "pinctrl ops lacks necessary functions"`（`core.c:2029`） | 注册返回 `-EINVAL` |
| `pinmux_check_ops()` | `pinmux.c:32-61` | `pmxops` 非空 + `get_functions_count` / `get_function_name` / `get_function_groups` / `set_mux` 四个都非空；**再**遍历所有 function 确认每个都有名字 | `dev_err "pinmux ops lacks necessary functions"`（`:44`）/ `dev_err "pinmux ops has no name for function%u"`（`:55`） | 注册返回 `-EINVAL` |
| `pinconf_check_ops()` | `pinconf.c:25-36` | `pin_config_set` 或 `pin_config_group_set` **至少一个**非空（注意：查的是 **set** 侧，`pin_config_get` 不算） | `dev_err "pinconf has to be able to set a pins config"`（`:31-32`） | 注册返回 `-EINVAL` |

三个检查在 `pinctrl_init_controller()` 里依次调用，后两个各有一道前置门：

```c
	ret = pinctrl_check_ops(pctldev);              /* core.c:2027 —— 无条件 */
	if (ret) { dev_err(...); goto out_err; }

	if (pctldesc->pmxops) {                        /* core.c:2034 */
		ret = pinmux_check_ops(pctldev);       /* core.c:2035 */
		...
	}
	if (pctldesc->confops) {                       /* core.c:2041 */
		ret = pinconf_check_ops(pctldev);      /* core.c:2042 */
		...
	}
```

所以 `pmxops` / `confops` 整个为 `NULL` 是**合法**的（只做引脚枚举、不做 mux 也不做 config 的控制器）。反过来说，`pinconf_check_ops()` 第一行就 `pctldev->desc->confops->pin_config_set`，**自己不判 `ops` 是否为 NULL**——它靠的正是 `core.c:2041` 这道门。别在别处直接调它。

**`pctlops` 是硬性必填**，而它的 kerneldoc 只写 `@pctlops: pin control operation vtable`，读起来像可选。这是一处**文档与代码不一致**，登记在 `03-internals.md` §C6 和 `99-evidence.md` §2.3。

#### §1.3.1 `get_group_pins` 不填：一个完全静默的大坑

我一开始以为 `get_group_pins` 也在必填清单里。**它不在**——`pinctrl_check_ops()` 只查 `get_groups_count` 和 `get_group_name` 两个。而全树四个调用点**全部**带 `if` 保护：

| 调用点 | 保护写法 | 不填时的行为 |
|---|---|---|
| `pinctrl_get_group_pins()` | `core.c:458` `if (!pctlops->get_group_pins) return -EINVAL;` | 返回 `-EINVAL`，调用者能看见 |
| debugfs `pinctrl-groups` | `core.c:1709` `if (ops->get_group_pins)` | 该 group 只打名字、不打引脚清单 |
| `pinmux_enable_setting()` | `pinmux.c:416` `if (pctlops->get_group_pins)` | 见下 |
| `pinmux_disable_setting()` | `pinmux.c:496` 同上 | 拆除时同样跳过 |

`pinmux_enable_setting()` 的路径要展开看，因为它是**静默**的：

```
   ret = 0; pins = NULL; num_pins = 0;         pinmux.c:411-412

   if (pctlops->get_group_pins)                pinmux.c:416   ← 不填 = 整块跳过
        ret = ...get_group_pins(...);

   if (ret) {                                  pinmux.c:420
        dev_warn "could not get pins for group %s"    ← ret 仍是 0，这条打不出来
        num_pins = 0;
   }

   for (i = 0; i < num_pins; i++)              pinmux.c:432   ← num_pins == 0，循环体一次不进
        pin_request(...);                      ← 引脚一根都没被占用

   for (i = 0; i < num_pins; i++)              pinmux.c:453
        desc->mux_setting = &setting->data.mux;  ← mux_setting 一个也没记

   ret = ops->set_mux(pctldev, func, group);   pinmux.c:465   ← 但这一句照样执行！
```

后果三条，且**没有任何日志**：

1. **`pin_request()` 完全没跑** → 引脚占用检查失效 → 两个消费者可以同时把同一根 pin mux 成不同功能，谁最后写谁赢；`.strict = true` 也一起失效（它是 `pin_request` 内部的逻辑）；
2. **`desc->mux_setting` 全为 NULL** → debugfs 的 `pinmux-pins` 显示所有引脚 UNCLAIMED，`pinmux_can_be_used_for_gpio()` 认为随便哪根都能当 GPIO；
3. **硬件其实配好了**（`set_mux` 执行了）→ 现象是"功能正常但 debugfs 一片空白"，非常容易被判成 debugfs 的 bug。

结论：**`get_group_pins` 在语法上可选、在语义上必填**。填不了的场合（group 与引脚不是一对多关系）应该改用 `pin`-粒度的 map 类型，不要留空。

全树共 **100** 处填了这个字段：**17** 处用泛型的 `pinctrl_generic_get_group_pins`（`core.c:564-584`，EXPORT 在 `:584`），**83** 处驱动自己实现。重跑：
```bash
grep -rn '\.get_group_pins[[:space:]]*=' drivers/pinctrl/ --include=*.c | wc -l                              # 100
grep -rn '\.get_group_pins[[:space:]]*=[[:space:]]*pinctrl_generic_get_group_pins' drivers/pinctrl/ --include=*.c | wc -l   # 17
```
而全树含 `struct pinctrl_ops` 的 .c 文件有 **107** 个——差值不能直接当成"没填的驱动数"（一个文件可能有多个 ops 实例，也可能一个实例分散多行），要判断某个具体驱动填没填，只能打开它的 `pinctrl_ops` 初始化器看。

### §1.4 `is_generic` 这个字段管什么

```c
static const struct pinconf_ops mysoc_confops = {
	.is_generic = true,	/* ← 只在 CONFIG_GENERIC_PINCONF 下存在（pinconf.h 的 #ifdef） */
	...
};
```

它**只影响 debugfs 的显示**，不影响任何功能路径：`is_generic = true` 时 debugfs 的 `pinconf-pins` 会用 `conf_items[]` 的标准显示名（`04-extensions.md` §3.3、§3.6）；`false` 时只能靠驱动自己的 `pin_config_dbg_show`。

**它不决定"能不能用 `bias-pull-up` 这类 DT 属性"**——那由 `dt_node_to_map` 填的是不是 `pinconf_generic_dt_node_to_map*` 决定。两件事经常被混为一谈。

因为字段在 `#ifdef CONFIG_GENERIC_PINCONF` 里（`include/linux/pinctrl/pinconf.h` 的 `struct pinconf_ops` 开头），**关掉这个 Kconfig 时写 `.is_generic = true` 会编译不过**。所以驱动如果有 `!GENERIC_PINCONF` 的配置组合，得自己包 `#ifdef`——in-tree 驱动的做法通常是让自己的 Kconfig `select GENERIC_PINCONF`（30 个驱动这么做，见 `04-extensions.md` §3.7）。

### §1.5 `.strict = true` 该不该填

| `strict` | `pin_request()` 的行为 | 谁这么选 |
|---|---|---|
| `false`（默认） | 同一根 pin 可以**同时**有 `mux_owner` 和 `gpio_owner`——只检查同类别冲突 | 多数驱动，含 `qcom/pinctrl-msm.c` 和 `freescale/pinctrl-imx.c` |
| `true` | 跨类别也冲突：已被 mux 占用的 pin 不能再当 GPIO，反之亦然 | **29 个驱动** |

完整真值表（5 行 × 2 列）在 `03-internals.md` §A11.2，这里只给选择建议：

- **硬件上 mux 和 GPIO 是互斥的**（一个 mux 选择器，GPIO 是其中一个选项）→ 填 `true`，让核心帮你抓住配置冲突。
- **硬件上 GPIO 输入可以和外设功能并存**（比如既走 UART 又能读电平）→ 只能 `false`，否则正常用法会被误报冲突。

arm64 上填了 `true` 的代表：`stm32/pinctrl-stm32.c:898`、`sunxi/pinctrl-sunxi.c:912`、`mediatek/pinctrl-moore.c:421`、`renesas/pinctrl-rzg2l.c:781`、`renesas/pinctrl-rzv2m.c:737`、`uniphier/pinctrl-uniphier-core.c:695`、`pinctrl-apple-gpio.c:187`、`starfive/pinctrl-starfive-jh7100.c:691`、`visconti/pinctrl-common.c:276`。重跑：
```bash
grep -rn '\.strict[[:space:]]*=[[:space:]]*true' drivers/pinctrl/ | wc -l   # 29
```

### §1.6 `.owner = THIS_MODULE` 不填会怎样

`pin_request()` 里有 `if (!try_module_get(pctldev->owner))`（`pinmux.c` 的 `pin_request`，逐行见 `03-internals.md` §A11.2）。`owner` 为 `NULL` 时 `try_module_get(NULL)` 返回 true（不是崩溃），所以**功能上不会坏**，但你的模块在有引脚被占用时**可以被 rmmod**——之后消费者手里的 `pctldev` 是悬垂指针。

判据：`try_module_get()` 对 `NULL` 的处理在 `include/linux/module.h`，不在 pinctrl 范围内；本篇只断言"pinctrl 侧确实会调它"。这一条属于 `99-evidence.md` §5.3「推断非观察」。

### §1.7 `.link_consumers = true` 是干什么的

打开它之后，`pinctrl_link_add()`（`core.c:1233-1239`）会在消费者取句柄时建立 **device link**：

```c
static void pinctrl_link_add(struct pinctrl_dev *pctldev, struct device *consumer)
{
	if (pctldev->desc->link_consumers)                 /* core.c:1236 */
		device_link_add(consumer, pctldev->dev,
				DL_FLAG_PM_RUNTIME |
				DL_FLAG_AUTOREMOVE_CONSUMER);
}
```

两个标志的含义：`DL_FLAG_PM_RUNTIME` 让 runtime PM 顺序自动正确；`DL_FLAG_AUTOREMOVE_CONSUMER` 让消费者被解绑时 link 自动消失（驱动不用管拆除）。kerneldoc 原文（`include/linux/pinctrl/pinctrl.h:145-148`）："This is sometimes necessary to ascertain the right suspend/resume order for example."

**默认是关的，而且全树只有 2 个驱动打开它**：`pinctrl-stmfx.c:651` 和 `stm32/pinctrl-stm32.c:1602`——注意两个都是**运行时赋值**（`pctl->pctl_desc.link_consumers = true;`），不是静态初始化器里的字段，所以 `grep '.link_consumers'` 要连赋值形式一起找：

```bash
grep -rn 'link_consumers' drivers/pinctrl/     # 2 个驱动 + core.c:1236 + 头文件 2 行
```

含义：**别假设"pinctrl 一定比消费者晚 suspend"**。要保证顺序，消费者得自己在 `.suspend` 里调 `pinctrl_pm_select_sleep_state()`（§2.4）。

### §1.8 `set_mux` 的实现要点

```c
static int mysoc_set_mux(struct pinctrl_dev *pctldev,
			 unsigned func_selector, unsigned group_selector)
{
	struct mysoc_pinctrl *pc = pinctrl_dev_get_drvdata(pctldev);
	struct function_desc *func;
	struct group_desc *grp;
	int i;

	/* 用泛型存储时这样取回自己的私有数据 */
	func = pinmux_generic_get_function(pctldev, func_selector);
	if (!func)
		return -EINVAL;
	grp = pinctrl_generic_get_group(pctldev, group_selector);
	if (!grp)
		return -EINVAL;

	for (i = 0; i < grp->num_pins; i++) {
		/* 写你的 mux 寄存器：pin = grp->pins[i]，值来自 func->data */
	}
	return 0;
}
```

**`set_mux` 被调用时不持 `pctldev->mutex`**（`03-internals.md` §B1.4）。含义：

- 你**可以**在里面调 `pinctrl_find_gpio_range_from_pin()`（带锁版）；
- 你**不能**假设"核心已经帮我串行化了"——如果你的寄存器组会被 `pin_config_set` 等其他回调同时访问，得自己加锁；
- 别在里面调任何会重入 pinctrl 核心状态应用路径的 API。

完整的"哪个回调持不持锁"三组分类表在 `01-api.md` §12 和 `03-internals.md` §B1.4。

### §1.9 常见陷阱

| 陷阱 | 症状 | 正确做法 |
|---|---|---|
| 泛型/自实现三个 group 回调**混填** | group 数量对但名字是 NULL，或 `num_pins = 0` 且**只有一条 `dev_warn`** | 三个一起选，见 `04-extensions.md` §1.4 |
| `pinctrl_generic_add_group()` 传 `unsigned *` | 编译 warning；忽略后行为仍对但类型不安全 | `pins` 字段是 `int *` |
| 在 `pinctrl_enable()` **之后**才 add_group | 早期消费者读到空表 | 严格按 §1.2 三步 |
| 用 `pinctrl_register()`（老接口） | 回调可能在拿到 `pctldev` 之前被调 | 用 `*_register_and_init()` |
| `pin_config_set` 只看 param 不看 argument | DTS 写 `input-disable;` 结果输入被**打开** | `pinconf_to_config_argument()`，见 `04-extensions.md` §3.3 |
| `dt_node_to_map` 里往 map 存栈上拼的字符串 | 后续读到乱码/oops | `add_map_mux` 不拷贝字符串，见 `04-extensions.md` §4.2 |

---

## §2 角色二：普通消费者驱动（外设）

### §2.1 最重要的一节：多数情况下你一行都不用写

```c
static int mydev_probe(struct platform_device *pdev)
{
	/* 这里没有任何 pinctrl 代码 —— 引脚已经配好了 */
	...
}
```

原因是**驱动核心替你做了**。`really_probe()` 在调你的 `.probe` **之前**就调了 `pinctrl_bind_pins()`：

```
   really_probe()                             drivers/base/dd.c
        │
   re_probe:                                  dd.c:612
        dev->driver = drv;                     dd.c:613
        ret = pinctrl_bind_pins(dev);          dd.c:616  在 probe 之前
        if (ret) goto pinctrl_bind_failed;
        │
        dev->bus->dma_configure(dev)           dd.c:620
        driver_sysfs_add(dev)                  dd.c:625
        call_driver_probe(dev, drv)            dd.c:638  ← 你的 .probe 在这里
        │
        pinctrl_init_done(dev)                 dd.c:684  在 probe 之后
        driver_bound(dev)                      dd.c:689
```

`pinctrl_bind_pins()`（`drivers/base/pinctrl.c:21-105`，整个文件只有这一个函数）做的事：

| 行 | 动作 | 失败时 |
|---|---|---|
| `:25-26` | `if (dev->of_node_reused) return 0;` | 复用 DT 节点的设备（如 MFD 子设备）**整段跳过** |
| `:28-30` | `devm_kzalloc` 出 `dev->pins`（`struct dev_pin_info`） | `-ENOMEM` |
| `:32` | `dev->pins->p = devm_pinctrl_get(dev)` | `dev_dbg "no pinctrl handle"` → 走 `cleanup_alloc` |
| `:39-40` | 查 `"default"` 状态 | `dev_dbg "no default pinctrl state"` + `ret = 0` → 走 `cleanup_get` |
| `:47-48` | 查 `"init"` 状态 | `dev_dbg "no init pinctrl state"`（注释原文：`/* Not supplying this state is perfectly legal */`） |
| `:53-57` | 有 `init` 就选 `init`，**没有就直接选 `default`** | — |
| `:59-62` | 选状态失败 | `dev_dbg "failed to activate initial pinctrl state"` |
| `:70-80` | `#ifdef CONFIG_PM` 下顺便查 `"sleep"` 和 `"idle"` | 各打一条 `dev_dbg`，都是合法缺省 |
| `:97-104` | 只把 `-EPROBE_DEFER` 和 `-EINVAL` 往上返回，**其余错误一律吞成 0** | 见 §2.6 |

**这是整个 pinctrl 里最重要的一句话**：普通外设驱动的引脚配置发生在 `.probe` 之前，由驱动核心用 DTS 里的 `pinctrl-0` / `pinctrl-names` 完成（DTS 怎么写见 `06-devicetree.md` §1）。你的驱动看到的世界里，引脚已经是对的了。

### §2.2 `init` → `default` 两段式是干什么的

```
     probe 之前                         probe 之后
  ┌──────────────┐               ┌────────────────────┐
  │ init 状态存在 │──选 init──▶ .probe ──▶ pinctrl_init_done() ──切到 default
  └──────────────┘                                    core.c:1538-1559
  ┌──────────────┐
  │ init 不存在   │──选 default─▶ .probe ──▶ pinctrl_init_done() ──直接返回 0
  └──────────────┘                          （`:1547` 判 init_state 是 ERR 就 return）
```

`pinctrl_init_done()`（`core.c:1538-1559`）的四道早退门，任一命中就什么都不做：

| 行 | 条件 | 语义 |
|---|---|---|
| `:1542-1543` | `!pins` | 这个设备没走 pinctrl |
| `:1545-1546` | `IS_ERR(pins->init_state)` | 没有 init 状态——绝大多数设备走这里 |
| `:1548-1549` | `pins->p->state != pins->init_state` | probe 期间驱动自己切过状态了，核心不覆盖你 |
| `:1551-1552` | `IS_ERR(pins->default_state)` | 没有 default 状态 |

真正切换那一行是 `:1554` `pinctrl_select_state(pins->p, pins->default_state)`，失败打 `dev_err "failed to activate default pinctrl state"`（`:1556`）。

**什么时候需要 `init` 状态**：引脚在 probe 期间必须处于某个"安全/隔离"配置，probe 完成后才切到工作配置。典型是"上电瞬间不能让某个信号有效"。DTS 写法见 `06-devicetree.md`。

### §2.3 需要自己写 pinctrl 代码的三种情况

| 情况 | 用什么 API | 备注 |
|---|---|---|
| ① 运行时在**多组自定义状态**之间切换（不止 default/sleep/idle） | `devm_pinctrl_get()` + `pinctrl_lookup_state()` + `pinctrl_select_state()` | 状态名自己起，DTS 里 `pinctrl-names` 对应 |
| ② PM 回调里手动切 sleep/idle | `pinctrl_pm_select_sleep_state()` / `_idle_state()` / `_default_state()` | 见 §2.4 |
| ③ probe 早于驱动核心（很罕见）或需要在核心之外重新应用 | `pinctrl_select_default_state(dev)` | 依赖 `dev->pins` 已由核心建好 |

情况 ① 的骨架：

```c
struct mydev {
	struct pinctrl *pinctrl;
	struct pinctrl_state *state_fast;
	struct pinctrl_state *state_slow;
};

static int mydev_probe(struct platform_device *pdev)
{
	struct mydev *md = ...;

	md->pinctrl = devm_pinctrl_get(&pdev->dev);
	if (IS_ERR(md->pinctrl))
		return PTR_ERR(md->pinctrl);	/* 可能是 -EPROBE_DEFER，必须原样返回 */

	md->state_fast = pinctrl_lookup_state(md->pinctrl, "fast");
	if (IS_ERR(md->state_fast))
		return PTR_ERR(md->state_fast);
	md->state_slow = pinctrl_lookup_state(md->pinctrl, "slow");
	if (IS_ERR(md->state_slow))
		return PTR_ERR(md->state_slow);

	return 0;
}

static void mydev_go_fast(struct mydev *md)
{
	int ret = pinctrl_select_state(md->pinctrl, md->state_fast);
	if (ret)
		dev_warn(...);		/* 一定要判返回值，见 §2.5 */
}
```

**`devm_pinctrl_get()` 和核心的 `pinctrl_bind_pins()` 会各拿一个句柄**——不是同一个 `struct pinctrl`。但 `pinctrl_get()` 内部对同一个 `dev` 有查表复用（`core.c` 的 `find_pinctrl()` 路径，逐行见 `03-internals.md` §A6），所以拿到的可能是同一个对象加引用。**不要**去动 `dev->pins->p`，那是核心的。

### §2.4 PM 回调里的三个 API

```c
static int __maybe_unused mydev_suspend(struct device *dev)
{
	/* ... 先停硬件 ... */
	return pinctrl_pm_select_sleep_state(dev);
}

static int __maybe_unused mydev_resume(struct device *dev)
{
	int ret = pinctrl_pm_select_default_state(dev);
	if (ret)
		return ret;
	/* ... 再启硬件 ... */
	return 0;
}

static int __maybe_unused mydev_runtime_suspend(struct device *dev)
{
	return pinctrl_pm_select_idle_state(dev);
}
```

三个函数的实现都极短（`core.c:1590-1627`，整段在 `#ifdef CONFIG_PM` 里），共同点：**都靠 `pinctrl_select_bound_state()`，都读 `dev->pins`，`dev->pins` 为 NULL 或对应状态不存在时静默返回 0**：

```c
static int pinctrl_select_bound_state(struct device *dev,
				      struct pinctrl_state *state)   /* core.c:1562-1575 */
{
	struct dev_pin_info *pins = dev->pins;
	int ret;

	if (IS_ERR(state))
		return 0;			/* No such state —— 静默成功 */
	ret = pinctrl_select_state(pins->p, state);
	if (ret)
		dev_err(dev, "failed to activate pinctrl state %s\n", state->name);
	return ret;
}
```

**含义：你在 suspend 里调 `pinctrl_pm_select_sleep_state()` 并检查返回值，返回 0 也不代表状态真的切了**——DTS 里没写 `pinctrl-names = ..., "sleep"` 时它返回 0 且什么都没做。判断"到底有没有 sleep 状态"只能看 `dev_dbg "no sleep pinctrl state"`（`drivers/base/pinctrl.c:74`，要开 dynamic debug）或 debugfs 的 `pinctrl-handles`（见 `07-debug.md` §3）。

`pinctrl_pm_select_default_state()` 是 `pinctrl_select_default_state()` 的**一行转发**（`core.c:1596-1599`，函数体只有 `return pinctrl_select_default_state(dev);`），两者行为完全一致。区别只在编译门：三个 `pinctrl_pm_*` 都在 `#ifdef CONFIG_PM` 里（`core.c:1590`），`pinctrl_select_default_state` 不在。

### §2.5 `pinctrl_select_state()` 的返回值一定要检查

`pinctrl_select_state()`（`core.c:1349-1356`）的两条早退：

```
   if (p->state == state)  return 0;      ← 已经是这个状态，直接成功（不重复写寄存器）
   return pinctrl_commit_state(p, state);
```

`pinctrl_commit_state()` 失败时会**尽力回滚**（把已应用的 setting 一个个 disable），路径逐行见 `03-internals.md` §A9。你要关心的是：**它失败后引脚处在一个不确定的中间状态**，驱动不能假装无事发生。

### §2.6 一个静默陷阱：`pinctrl_bind_pins()` 吞掉了大部分错误

```
   pinctrl_bind_pins() 的收尾           drivers/base/pinctrl.c:96-104

   cleanup_get:  devm_pinctrl_put(dev->pins->p);
   cleanup_alloc: devm_kfree(dev, dev->pins); dev->pins = NULL;

   if (ret == -EPROBE_DEFER) return ret;    ← :97-98  往上传，probe 会重试
   if (ret == -EINVAL)       return ret;    ← :100-101 往上传，probe 失败
   /* We ignore errors like -ENOENT meaning no pinctrl state */
   return 0;                                 ← :104 其他全部吞掉
```

后果对照表：

| DTS 状况 | `pinctrl_bind_pins()` 返回 | 你的 `.probe` 会不会跑 | 引脚配了吗 |
|---|---|---|---|
| 完全没写 `pinctrl-0` | 0（`devm_pinctrl_get` 走 `no pinctrl handle`） | **会跑** | **没配** |
| 写了 `pinctrl-0` 但 pin controller 还没 probe | `-EPROBE_DEFER` | 不跑，稍后重试 | 稍后会配 |
| 写了 `pinctrl-0`，引用的子节点里属性拼错 | 视错误码；`pinconf_generic_dt_subnode_to_map()` 对认不出的节点返回 0 | **会跑** | **没配** |
| 引脚被别的设备占用（`pin_request` 冲突） | `-EBUSY` → **被吞成 0** | **会跑** | **没配**（但有 `dev_err "could not request pin ..."`） |
| `pinctrl-names` 里没有 `"default"` | 0 | **会跑** | **没配** |

**这五行里有四行是"probe 成功但引脚没配"**。所以外设驱动"probe 成功却读不到设备 / 通信全 0"时，第一件事是查 pinctrl 而不是查外设寄存器。完整的定位决策树在 `06-devicetree.md` §5 和 `07-debug.md` §2。

**`-EBUSY` 被吞掉这一条尤其要记**：内核里确实有一条 `dev_err "could not request pin %d (%s) from group %s on device %s"`（`pinmux.c:443-447`），但它是 pinctrl 核心打的，你的驱动**看不到任何返回值异常**。

### §2.7 常见陷阱

| 陷阱 | 症状 | 正确做法 |
|---|---|---|
| 在 `.probe` 里手动选 `default` 状态 | 通常无害（`pinctrl_select_state` 会因 `p->state == state` 直接返回 0），但换了句柄就变成重复写寄存器 | 相信核心；只在有自定义状态时才自己动手 |
| 把 `devm_pinctrl_get()` 的错误当成"没有 pinctrl"忽略 | `-EPROBE_DEFER` 被吞 → 引脚永远配不上 | 原样 `return PTR_ERR(...)` |
| 用 `pinctrl_get()`（非 devm）却忘了 `pinctrl_put()` | 句柄泄漏，pin controller 无法卸载 | 优先 `devm_*` |
| 假设 `pinctrl_pm_select_sleep_state()` 返回 0 = 切成功了 | suspend 时引脚仍是工作配置，漏电 | 见 §2.4 |
| 在中断上下文里调 `pinctrl_select_state()` | 会睡（内部取 mutex、可能 `kzalloc`） | 只能在可睡上下文调，见 `01-api.md` §12 |

---

## §3 角色三：GPIO 控制器驱动

### §3.1 你要做的两件事

```
   ① 注册 gpio range —— 建立 "GPIO 号 ↔ pin 号" 的映射
        └─ 首选：DTS 写 gpio-ranges，由 of_gpiochip_add_pin_range() 自动完成
                 （drivers/gpio/gpiolib-of.c:893-978，调用点 :1000）
        └─ 次选：驱动自己调 pinctrl_add_gpio_range() / gpiochip_add_pin_range()

   ② 在 gpio_chip 的回调里把请求转给 pinctrl
        └─ 其实 gpiolib 已经替你调好了（见 §3.3），多数驱动什么都不用写
```

**和角色 2 一样，多数 GPIO 驱动这两件事都不用自己写代码**：range 由 `gpio-ranges` DTS 属性建立，转发由 gpiolib 内部完成。要自己写的只有"pin controller 和 GPIO controller 是同一个驱动"这种合体形态（arm64 上非常常见，如 `qcom/pinctrl-msm.c`）。

### §3.2 合体形态：一个驱动同时是 pin controller 和 GPIO controller

```c
static int mysoc_gpio_init(struct mysoc_pinctrl *pc)
{
	struct gpio_chip *chip = &pc->chip;

	chip->label		= dev_name(pc->dev);
	chip->parent		= pc->dev;
	chip->owner		= THIS_MODULE;
	chip->base		= -1;		/* 动态分配 GPIO 号 */
	chip->ngpio		= pc->npins;
	chip->request		= gpiochip_generic_request;   /* → pinctrl_gpio_request */
	chip->free		= gpiochip_generic_free;      /* → pinctrl_gpio_free */
	chip->set_config	= gpiochip_generic_config;    /* → pinctrl_gpio_set_config */
	chip->direction_input	= mysoc_gpio_direction_input;
	chip->direction_output	= mysoc_gpio_direction_output;
	chip->get		= mysoc_gpio_get;
	chip->set		= mysoc_gpio_set;
	/* 不需要自己填 of_node / gpio_ranges，gpiolib 从 parent 的 DT 节点读 */

	return devm_gpiochip_add_data(pc->dev, chip, pc);
}
```

三个 `gpiochip_generic_*` 就是 gpiolib 提供的转发器：

| gpio_chip 回调 | gpiolib 转发器 | 位置 | 有没有前置门 | 转给谁 |
|---|---|---|---|---|
| `.request` | `gpiochip_generic_request()` | `gpiolib.c:1743-1751`，`:1750` 调 | **有**：`:1747-1748` `if (list_empty(&gc->gpiodev->pin_ranges)) return 0;`（在 `#ifdef CONFIG_PINCTRL` 内） | `pinctrl_gpio_request` `core.c:785-809` |
| `.free` | `gpiochip_generic_free()` | `gpiolib.c:1759-1767`，`:1766` 调 | **有**：`:1762-1763` 同样的 `list_empty` 早退（`return;`） | `pinctrl_gpio_free` `core.c:820-839` |
| `.set_config` | `gpiochip_generic_config()` | `gpiolib.c:1776-1780`，`:1779` 调 | **没有** —— 无条件转发 | `pinctrl_gpio_set_config` `core.c:902-919` |

前两个的 `list_empty(&gc->gpiodev->pin_ranges)` 早退意味着：**这个 gpiochip 一条 range 都没注册时，`.request` 连 pinctrl 都不会进**，直接返回 0。也就是说 §3.4 讲的"静默成功"在 gpiolib 这一层就已经发生了一次，pinctrl 里的那次是第二道。两道门叠起来的结果是同一个：**GPIO 申请成功、mux 没切、零日志**。

第三个没有这道门，所以 `gpiod_set_config()` 在没有 range 的 chip 上会一路走到 `pinctrl_gpio_set_config()`，再由 pinctrl 侧的 `pinctrl_get_device_gpio_range()` 失败后静默返回。

**方向切换没有对应的 `gpiochip_generic_*`**。要让 pinctrl 知道方向变化，得在自己的 `direction_input` / `direction_output` 里显式调 `pinctrl_gpio_direction_input()` / `_output()`（`core.c:873-876` / `:887-890`），它们最终打到 `pmxops->gpio_set_direction`。合体驱动通常直接写自己的寄存器、不绕这一圈。

### §3.3 五个 `pinctrl_gpio_*` API 的调用者分布

| API | 全树使用文件数 | 主要调用者 | 备注 |
|---|---|---|---|
| `pinctrl_gpio_direction_output` | **22** | 各 GPIO/pinctrl 驱动 | — |
| `pinctrl_gpio_direction_input` | **21** | 同上 | — |
| `pinctrl_gpio_request` | **13** | `gpiolib.c:1750` + 12 个驱动 | — |
| `pinctrl_gpio_free` | **13** | `gpiolib.c:1766` + 12 个驱动 | — |
| `pinctrl_gpio_set_config` | **2** | `gpiolib.c:1779` + 1 | 几乎只走 gpiolib |
| `pinctrl_gpio_can_use_line` | **1** | 全树唯一：`drivers/gpio/gpiolib-cdev.c:2401` | 只服务字符设备的 lineinfo 查询 |

重跑口径（**必须带 `-w`**，理由见 §5.3）：
```bash
for f in pinctrl_gpio_direction_output pinctrl_gpio_direction_input \
         pinctrl_gpio_request pinctrl_gpio_free pinctrl_gpio_set_config \
         pinctrl_gpio_can_use_line; do
    printf '%-36s %s\n' "$f" "$(grep -rlnw "$f" drivers/ --include=*.c | grep -v 'pinctrl/core.c' | wc -l)"
done
```

### §3.4 三个 API 的"静默成功"语义

`pinctrl_gpio_request()`（`core.c:785-809`）的开头：

```c
	ret = pinctrl_get_device_gpio_range(gpio, &pctldev, &range);   /* core.c:792 */
	if (ret) {
		if (pinctrl_ready_for_gpio_range(gpio))
			ret = 0;                                        /* core.c:795-796 */
		return ret;
	}
```

翻译成人话：**"这个 GPIO 找不到对应的 pin"不一定是错误**——如果全树所有 pin controller 都已经 probe 完（`pinctrl_ready_for_gpio_range()` 返回 true，`core.c:331-361`），那就认定"这个 GPIO 本来就不受 pinctrl 管"，返回 0。否则返回 `-EPROBE_DEFER`（`pinctrl_get_device_gpio_range()` 的 `core.c:400`）。

完整决策树（三条 API 共用同一形状）：

```
   pinctrl_gpio_{request,free,direction_*,set_config}(gpio, ...)
        │
        ▼
   pinctrl_get_device_gpio_range(gpio, &pctldev, &range)      core.c:377-401
        │  遍历 pinctrldev_list，逐个 pinctrl_match_gpio_range()
        │
        ├── 找到 ──▶ 转给对应 pctldev 的 pmxops / confops
        │
        └── 没找到（-EPROBE_DEFER）
                │
                ├── request：pinctrl_ready_for_gpio_range() 为 true
                │            ──▶ 返回 0（静默成功，什么也没做）
                │            为 false ──▶ 返回 -EPROBE_DEFER
                │
                ├── free / direction / set_config
                │            ──▶ 直接 return，无日志（`core.c:828-830` 是
                │                `if (ret) { return; }`，free 返回类型是 void）
                │
                └── 三条路都不打印任何东西
```

**这是 GPIO 驱动最容易掉的坑**：`gpio-ranges` 写错（或干脆没写）时，`gpiochip_generic_request()` 返回 0，GPIO 申请成功，`gpiod_get()` 也成功，但**引脚的 mux 根本没被切到 GPIO 功能**——外设功能仍然占着这根线。现象是"GPIO 读写没反应"而不是"申请失败"。

定位办法：`cat /sys/kernel/debug/pinctrl/<name>/gpio-ranges` 看 range 有没有建起来（见 `07-debug.md` §3）；`pinmux-pins` 里对应引脚应该显示被 GPIO 占用。

### §3.5 range 注册的四条路

| 路 | 入口 | 谁调 | arm64 是否推荐 |
|---|---|---|---|
| ① DTS `gpio-ranges` | `of_gpiochip_add_pin_range()` `gpiolib-of.c:893-978` | gpiolib 自动（`:1000`） | **首选** |
| ② 驱动调 `gpiochip_add_pin_range()` | `gpiolib.c:1855-1893` | GPIO 驱动 | 无 DT 时用 |
| ③ 驱动调 `gpiochip_add_pingroup_range()` | `gpiolib.c:1797-1836` | GPIO 驱动 | kerneldoc 明确写 "Calling this function directly from a DeviceTree-supported pinctrl driver is DEPRECATED" |
| ④ pin controller 侧调 `pinctrl_add_gpio_range()` | `core.c:411-417` | pinctrl 驱动 | 合体驱动会用；单独的 pin controller 用它是老写法 |

第 ① 条的 DTS 语法（三元组 `<&pinctrl phandle> <gpio-offset> <pin-base> <count>`，`of_parse_phandle_with_fixed_args(np, "gpio-ranges", 3, ...)` `gpiolib-of.c:914`）详见 `06-devicetree.md` §4。

统计：`pinctrl_add_gpio_range` 有 **14** 个文件在用，`pinctrl_remove_gpio_range` 3 个，`pinctrl_find_and_add_gpio_range` **1** 个，`pinctrl_add_gpio_ranges`（复数版）**0** 个——它是导出的死代码，见 §5.2。

### §3.6 常见陷阱

| 陷阱 | 症状 | 正确做法 |
|---|---|---|
| 只写 `gpio-controller` 不写 `gpio-ranges` | GPIO 申请成功但 mux 没切，读写无反应 | 见 §3.4 |
| `gpio-ranges` 的 `pin-base` 填错 | 操作到了**别的引脚** | 对着 `debugfs/pinctrl/<n>/pins` 核对 |
| 自己实现 `direction_input` 却忘了通知 pinctrl | pinctrl 侧记录的方向与硬件不符，debugfs 误导 | 显式调 `pinctrl_gpio_direction_input()`，或接受 debugfs 不准并在注释里写明 |
| `.strict = true` 但硬件允许 GPIO 与功能并存 | 正常用法被误报 `-EBUSY` | 见 §1.5 |
| 用 `gpiochip_add_pingroup_range()` 新写驱动 | 能跑，但 kerneldoc 标了 DEPRECATED | 用 `gpio-ranges` |

---

## §4 角色四：板级 / machine mapping 提供者

**arm64 上你几乎不会用到这一节**——ARM64 强制 DT，没有 board file。保留它的三个理由：① `pinctrl_register_mappings()` 是导出 API，随时可能被调；② **DT 路径自己就走这条 API**（见 §4.3），理解它才能看懂 `devicetree.c`；③ "hog" 概念只在这里出现，而 debugfs 里能看到它。

### §4.1 静态 map 数组长什么样

```c
#include <linux/pinctrl/machine.h>

static unsigned long uart_pin_configs[] = {
	PIN_CONF_PACKED(PIN_CONFIG_BIAS_PULL_UP, 1),	/* 这个宏不存在，见下面的注 */
};

static struct pinctrl_map __initdata mysoc_pinmap[] = {
	/* 设备 "serial0" 的 default 状态：把 uart0grp 复用成 uart0 功能 */
	PIN_MAP_MUX_GROUP_DEFAULT("serial0", "mysoc-pinctrl", "uart0grp", "uart0"),

	/* 同一设备的 sleep 状态：换成 gpio 功能 */
	PIN_MAP_MUX_GROUP("serial0", PINCTRL_STATE_SLEEP,
			  "mysoc-pinctrl", "uart0grp", "gpio"),

	/* 给一组引脚下配置 */
	PIN_MAP_CONFIGS_GROUP_DEFAULT("serial0", "mysoc-pinctrl",
				      "uart0grp", uart_pin_configs),

	/* 只占位、不做任何事的状态（让 pinctrl_lookup_state 能成功） */
	PIN_MAP_DUMMY_STATE("serial0", "idle"),
};

static void __init mysoc_board_init(void)
{
	pinctrl_register_mappings(mysoc_pinmap, ARRAY_SIZE(mysoc_pinmap));
}
```

**注**：上面那个 `PIN_CONF_PACKED` 是我编的，树上**没有**这个宏。真正的打包方式是 `pinconf_to_config_packed(param, argument)`（`static inline`，`include/linux/pinctrl/pinconf-generic.h:166-170`），或者用驱动自定义的编码。我把它留在这里并标出来，是因为"顺手写一个看起来该存在的宏"正是 05 这一篇最容易犯的错——判据是全树 grep，不是"看起来合理"：

```bash
grep -rnE 'define[[:space:]]+PIN_CONF_PACKED' include/ drivers/     # 空
```

### §4.2 五族宏一览

全部在 `include/linux/pinctrl/machine.h`，共 **13** 个（`grep -nE 'define[[:space:]]+PIN_MAP' include/linux/pinctrl/machine.h | wc -l`）：

| 宏 | 行 | 展开成什么 | 用途 |
|---|---|---|---|
| `PIN_MAP_DUMMY_STATE(dev, state)` | `:80-85` | `type = PIN_MAP_TYPE_DUMMY_STATE`，无 `ctrl_dev_name` | 让某个状态名"存在但不做事" |
| `PIN_MAP_MUX_GROUP(dev, state, pinctrl, grp, func)` | `:87-97` | `type = MUX_GROUP` + `data.mux = {grp, func}` | 基础宏，下面四个都由它展开 |
| `PIN_MAP_MUX_GROUP_DEFAULT(dev, pinctrl, grp, func)` | `:99-100` | `PIN_MAP_MUX_GROUP(dev, PINCTRL_STATE_DEFAULT, ...)` | 省掉状态名 |
| `PIN_MAP_MUX_GROUP_HOG(dev, state, grp, func)` | `:102-103` | `PIN_MAP_MUX_GROUP(dev, state, **dev**, grp, func)` | 见 §4.4 |
| `PIN_MAP_MUX_GROUP_HOG_DEFAULT(dev, grp, func)` | `:105-106` | 上面两个的组合 | — |
| `PIN_MAP_CONFIGS_PIN*` 四个 | `:108-128` | `type = CONFIGS_PIN` + `data.configs` | 单引脚配置 |
| `PIN_MAP_CONFIGS_GROUP*` 四个 | `:130-150` | `type = CONFIGS_GROUP` + `data.configs` | 引脚组配置 |

`struct pinctrl_map`（`machine.h:67-76`）只有 5 个字段，最后一个是 `union { mux; configs; }`——所以一条 map 要么描述 mux、要么描述 config，**不能同时**。这也是 `pinconf_generic_dt_subnode_to_map()` 里 `reserve = (function ? 1 : 0) + (num_configs ? 1 : 0)` 这行算术的来源（`pinconf-generic.c:347-352`，逐行见 `04-extensions.md` §3.4）。

### §4.3 DT 路径自己也走这条 API

```
   pinctrl_dt_to_map()                      devicetree.c
        │  解析 pinctrl-0 / pinctrl-names，调控制器的 dt_node_to_map
        ▼
   dt_remember_or_free_map()                devicetree.c:60-98
        │
        └── pinctrl_register_mappings(map, num_maps)     devicetree.c:96  同一个 API

   拆除侧：
   pinctrl_dt_free_maps() → dt_free_map() → pinctrl_unregister_mappings(dt_map->map)
                                                          devicetree.c:55
```

**所以 machine mapping 不是"另一套机制"，而是同一套机制的手工入口**。DT 只是自动生成 map 数组的一台机器。这解释了为什么 debugfs 的 `pinctrl-maps` 文件在纯 DT 系统上也有内容（见 `07-debug.md` §3）。

### §4.4 hog 是什么

**hog = pin controller 自己认领的 map**。判据在 `struct pinctrl_map` 的 kerneldoc（`machine.h:52-55`）：

> "If this name is set to the same name as the pin controllers own `dev_name()`, the map entry will be hogged by the driver itself upon registration"

也就是 `dev_name == ctrl_dev_name` —— `PIN_MAP_MUX_GROUP_HOG()` 那个宏做的就是把第三个参数填成 `dev` 本身（`machine.h:102-103`）。

hog 在核心里有独立处理，因为它天然会形成环：

| 位置 | 处理 | 原因（源码注释原文） |
|---|---|---|
| `core.c:980` | 不给 hog 做 `-EPROBE_DEFER` | `/* Do not defer probing of hogs (circular loop) */` |
| `core.c:1287` / `:1311` | 不给 hog 建 device link | `/* Do not link hogs (circular dependency) */` |
| `core.c:2073-2109` | `pinctrl_claim_hogs()`：注册末期一次性认领 | `dev_dbg "no hogs found"` / `dev_err "error claiming hogs: %li"` |
| `core.c:2115-2118` | 由 `pinctrl_enable()` 调用 | `dev_err "could not claim hogs: %i"` |

`pinctrl_claim_hogs()` 顺便把两个状态缓存进 `pctldev`：`hog_default`（`:2089-2091`）和 `hog_sleep`（`:2101-2104`）。它们的唯一消费者就是 `pinctrl_force_default()`（`core.c:1522-1527`，EXPORT `:1528`）和 `pinctrl_force_sleep()`（`core.c:1510-1515`，EXPORT `:1516`）——这两个函数的可用性问题见 §6.2。

### §4.5 `pinctrl_register_mappings()` 的全树使用者

全树只有 **4** 个文件用（含 pinctrl 自己），逐个点名：

| 文件 | 调用行 | 性质 | arm64 相关 |
|---|---|---|---|
| `drivers/pinctrl/devicetree.c` | `:96` 注册 / `:55` 注销 | DT 路径的内部实现，见 §4.3 | **是**（所有 arm64 系统都走） |
| `drivers/pinctrl/cirrus/pinctrl-madera-core.c` | `:1063` 注册 / `:1091` 注销 | 音频 codec 从 platform data 里拿 `gpio_configs` 再注册 | 可能（codec 挂在 arm64 板上，但走的是 pdata 而非 DT） |
| `drivers/gpu/drm/i915/display/intel_dsi_vbt.c` | — | 从 VBT（x86 显卡 BIOS 表）建 map | **否**（x86-only） |
| `drivers/platform/x86/x86-android-tablets.c` | — | x86 平板的板级修补 | **否**（x86-only） |

重跑：
```bash
grep -rlnw 'pinctrl_register_mappings' --include=*.c . | grep -v 'drivers/pinctrl/core.c'
```

**结论：arm64 上"手写 machine map"这件事在 6.1 主线里一个例子都没有**。唯一贴近的是 madera 那种"MFD 子设备从 pdata 拿配置"的形态。这是一条**否定性断言**，过了两道门：grep 树（上面那条命令，4 个文件全部点名核查过）+ grep 本文档集（没有别处声称有 arm64 board file 用法）。

---

## §5 近邻符号区分

这一节是独立成节的，因为"名字很像"是 pinctrl 里最高频的错误源。**每组都给"错用会怎样"**，不只是"区别是什么"。

### §5.1 `pinctrl_select_default_state` vs `pinctrl_pm_select_default_state`

| | `pinctrl_select_default_state(dev)` | `pinctrl_pm_select_default_state(dev)` |
|---|---|---|
| 位置 | `core.c:1581-1587`，EXPORT `:1588` | `core.c:1596-1599`，EXPORT `:1600` |
| 编译门 | 无 | `#ifdef CONFIG_PM`（`core.c:1590`） |
| 函数体 | `if (!dev->pins) return 0; return pinctrl_select_bound_state(dev, dev->pins->default_state);` | **一行**：`return pinctrl_select_default_state(dev);` |
| 头文件 | `consumer.h:43`（无条件） | `consumer.h:46`（在 `#ifdef CONFIG_PM` 里，`:45` 开门） |

**行为完全一致**，区别只是 `!CONFIG_PM` 时后者变成 `static inline ... { return 0; }`（`consumer.h:49-61`，三个 `pinctrl_pm_*` 各一个 stub）。

注意别把这组 stub 和另一组搞混：`consumer.h:126-144` 那四个 stub 是 **`!CONFIG_PINCTRL`** 的（`#else /* !CONFIG_PINCTRL */` 在 `:63`），里面 `pinctrl_select_default_state` 也变成返回 0。也就是说**关掉 `CONFIG_PINCTRL` 时两个函数都静默成功**，`!CONFIG_PM` 时只有带 `_pm_` 的那个静默。

**错用会怎样**：在非 PM 路径（比如 reset 恢复、错误重试）里调 `pinctrl_pm_select_default_state()` —— 关掉 `CONFIG_PM` 的配置下走的是 stub，**静默返回 0**，引脚不恢复。反过来在 PM 回调里调无 `_pm_` 版本没有任何问题。**建议：不确定就用不带 `_pm_` 的那个。**

### §5.2 `pinctrl_add_gpio_range` vs `pinctrl_add_gpio_ranges`（差一个 s）

| | `pinctrl_add_gpio_range` | `pinctrl_add_gpio_ranges` |
|---|---|---|
| 位置 | `core.c:411-417`，EXPORT `:418` | `core.c:420-428`，EXPORT `:429` |
| 参数 | 一个 `struct pinctrl_gpio_range *` | 一个数组 + `unsigned nranges` |
| 实现 | 取 `pctldev->mutex`，`list_add_tail(&range->node, &pctldev->gpio_ranges)` | `for (i...)` 循环调单数版（`:426-427`） |
| 全树使用文件数 | **14** | **0** |

**错用会怎样**：把单个 range 传给复数版并忘了传 `nranges`（或传 0）→ **编译过、一根 range 都没注册**，然后落进 §3.4 的静默成功。复数版是 0 调用者的导出死代码，看到它别以为"这才是推荐用法"。

### §5.3 `pinconf_generic_dt_node_to_map` 和它的三个包装

| 符号 | 传给核心的 `type` | 直接使用文件数 |
|---|---|---|
| `pinconf_generic_dt_node_to_map(pctldev, np, map, num_maps, type)` | 由**调用方**指定 | **2** |
| `pinconf_generic_dt_node_to_map_group()` | `PIN_MAP_TYPE_CONFIGS_GROUP` | 16 |
| `pinconf_generic_dt_node_to_map_pin()` | `PIN_MAP_TYPE_CONFIGS_PIN` | 27 |
| `pinconf_generic_dt_node_to_map_all()` | `PIN_MAP_TYPE_INVALID`（= 让核心自动判） | 21 |

三个包装都是 `static inline`（`pinconf-generic.h:199` / `:207` / `:215`），所以填进 `pctlops.dt_node_to_map` 的其实是它们各自的地址。

**`PIN_MAP_TYPE_INVALID` 不是错误值**，它在这里当"自动检测"哨兵用：`pinconf_generic_dt_subnode_to_map()` 里 `if (type == PIN_MAP_TYPE_INVALID) type = ...`（`pinconf-generic.c:313` 用 `pins` → `CONFIGS_PIN`；`:319` 用 `groups` → `CONFIGS_GROUP`）。

**错用会怎样**：控制器的 group 是"多引脚组"，但填了 `_pin` 版本 → DTS 里写 `groups = "uart0grp"` 会走进"既没有 `pins` 也没有 `groups`"分支吗？不会——`_pin` 版本传的是 `CONFIGS_PIN`，`:313/:319` 那两个 `if` 因为 `type != INVALID` 不生效，于是核心按 **pin** 名去查 `"uart0grp"`，查不到 → `dev_err "does not have pin group %s"`（`core.c:743-747` 是 group 侧的对应报错）。**建议：不确定就用 `_all`。**

**统计这一族时必须带 `grep -w`**：不加 `-w`，`grep -rl pinconf_generic_dt_node_to_map` 会把三个包装的命中一起吃进来，结果从 **2** 变成 **66**（去重后的并集）。这个 2-vs-66 的差距是本文档集里最大的一处统计口径陷阱，详细命令见 `04-extensions.md` §3.7。

### §5.4 `pinctrl_utils_add_map_configs` vs `pinctrl_utils_add_config`

| | `pinctrl_utils_add_map_configs` | `pinctrl_utils_add_config` |
|---|---|---|
| 位置 | `pinctrl-utils.c:60-83`，EXPORT `:84` | `pinctrl-utils.c:86-107`，EXPORT `:108` |
| 往哪里加 | 往 **map 数组**加一条新 map | 往 **一个 configs 数组**追加一个 config 值 |
| 内存 | `kmemdup(configs, ...)` `:71` | `krealloc(*configs, ...)` `:94` |
| 前置条件 | 必须先 `pinctrl_utils_reserve_map()` 留位，否则 `WARN_ON` + `-ENOSPC`（`:68-69`） | 无（自己 realloc） |
| 全树使用文件数 | 10 | **2** |

**错用会怎样**：想给已有 map 追加一个 config，却调了 `add_map_configs` → 多出一条 map 且**吃掉一个 reserve 配额**，若配额已满就 `WARN_ON` 打栈 + 返回 `-ENOSPC`，DTS 解析整体失败。反向错用（想加 map 却调 `add_config`）→ map 数量不变，配置被塞进了别人的 configs 数组。

### §5.5 `pinctrl_find_gpio_range_from_pin` vs `..._nolock`

| | 带锁版 | `_nolock` 版 |
|---|---|---|
| 位置 | `core.c:498-510`，EXPORT `:510` | EXPORT `:491` |
| 锁 | 内部取 `pctldev->mutex` | **要求调用者已持 `pctldev->mutex`** |

**错用会怎样**：在已持锁的回调里（比如某些 `pinconf` 路径）调带锁版 → **自死锁**，整个 CPU 卡住，没有任何日志。反向（未持锁调 `_nolock`）→ 遍历链表时可能读到正在被摘除的 range，随机崩溃或读到错 pin 号。**判据：看你所在的回调持不持 `pctldev->mutex`，三组分类表在 `03-internals.md` §B1.4。**

### §5.6 `struct group_desc` vs `struct pingroup`

| | `struct group_desc` | `struct pingroup` |
|---|---|---|
| 位置 | **私有** `drivers/pinctrl/core.h:185-220` 一带 | **公开** `include/linux/pinctrl/pinctrl.h`（`PINCTRL_PINGROUP` 宏在 `:42`） |
| `pins` 字段类型 | `int *` | `const unsigned int *` |
| 用途 | 泛型 group 存储（radix tree）的节点，带 `data` 私有指针 | 驱动的静态 group 描述表 |

两者的 `pins` 类型不同，而回调 `get_group_pins` 要求 `const unsigned **`——所以泛型实现里有一次隐式转换。这是 `04-extensions.md` §1.2 记的那处类型不一致。

**错用会怎样**：把 `PINCTRL_PINGROUP()` 造出来的 `struct pingroup` 直接传给 `pinctrl_generic_add_group()`（它要 `int *pins`）→ 编译 warning，运行时因为两者内存布局不同而读到垃圾引脚号。**正确做法是拆开传 `name` / `pins` / `npins` 三个参数。**

### §5.7 `pinctrl_register` vs `pinctrl_register_and_init` vs `devm_*`

| 符号 | 位置 | 特点 | 建议 |
|---|---|---|---|
| `pinctrl_register` | EXPORT `core.c:2160` | 注册+启用一步完成，**没有 §1.2 的窗口** | kerneldoc 自陈有问题，不要用 |
| `pinctrl_register_and_init` | EXPORT `core.c:2192` | 两步，留窗口；要自己 `pinctrl_unregister` | 可用 |
| `devm_pinctrl_register` | EXPORT `core.c:2283` | `pinctrl_register` 的 devm 版 | 同样没有窗口 |
| `devm_pinctrl_register_and_init` | EXPORT `core.c:2319` | **推荐**：两步 + 自动清理 | **首选** |
| `devm_pinctrl_unregister` | EXPORT `core.c:2331` | 提前注销 devm 注册的控制器 | 罕用 |
| `pinctrl_unregister` | EXPORT `core.c:2232` | 非 devm 版的注销 | 与 `_and_init` 配对 |

**错用会怎样**：用 `devm_pinctrl_register()`（无 `_and_init`）+ 之后再 `pinctrl_generic_add_group()` → 控制器**已经挂进 `pinctrldev_list` 且 debugfs 已建**，早到的消费者读到空 group 表，表现为随机的 `does not have pin group` 报错（跟启动顺序有关，重现困难）。

### §5.8 `pinctrl_gpio_request` 的两个"看起来该配对"的函数

| 符号 | 是不是 `pinctrl_gpio_request` 的反向操作 |
|---|---|
| `pinctrl_gpio_free(gpio)` | **是**（`core.c:820-839`） |
| `pinctrl_gpio_can_use_line(gpio)` | **不是**——它是**只读查询**，不占用任何东西 |

`pinctrl_gpio_can_use_line()`（`core.c:749-774`）的第一行逻辑就说明了这点：`if (pinctrl_get_device_gpio_range(...)) return true;`（`:761-762`）——找不到 range 就返回"能用"。它全树唯一调用者是 `drivers/gpio/gpiolib-cdev.c:2401`，服务 GPIO 字符设备的 lineinfo 查询。

**错用会怎样**：拿它当"试着申请一下"用 → 返回 true 但引脚**没有被占用**，后续两个消费者都以为自己拿到了。

---

## §6 "符号存在" ≠ "你能用"

判据固定两条：**`EXPORT_SYMBOL*`** **且** **公开头文件（`include/linux/pinctrl/*.h`）里有声明**。有 kerneldoc 不算，`static` 却写满注释的函数比没注释的更危险。

### §6.1 只在私有头文件里声明的三族

| 族 | 声明在 | EXPORT | 你能用吗 |
|---|---|---|---|
| `pinctrl_generic_*`（7 个） | `drivers/pinctrl/core.h` | 有 | **只有 pin controller 驱动能用**（要 `#include "core.h"`，即必须住在 `drivers/pinctrl/` 下或用相对路径） |
| `pinmux_generic_*`（6 个） | `drivers/pinctrl/pinmux.h` | 有 | 同上 |
| `pinctrl_utils_*`（5 个） | `drivers/pinctrl/pinctrl-utils.h` | 有 | 同上 |

三族的完整 API 表、行号、使用统计在 `04-extensions.md` §1.3 / §2.2 / §4.2。

**它们导出了但不在公开头里**，含义是：内核**允许**树外模块用（符号可解析），但**不提供**头文件——你得自己抄声明，而下个版本改了原型不会有人通知你。**结论：只在 `drivers/pinctrl/` 里写代码时才用。**

### §6.2 最容易误判的一对：`pinctrl_force_default` / `pinctrl_force_sleep`

| | 事实 |
|---|---|
| EXPORT | **有**：`EXPORT_SYMBOL_GPL` 在 `core.c:1516` / `:1528` |
| 公开头声明 | **没有**——只在 `drivers/pinctrl/core.h:239` / `:240` |
| 有 kerneldoc 吗 | 有 |
| 判据结论 | **不算公开 API** |

但它有 **6 个驱动**在用，而且全都住在 `drivers/pinctrl/` 下（所以能 `#include "../core.h"`）：

| 驱动 | 调用行 |
|---|---|
| `pinctrl-single.c` | `:1712`（sleep）/ `:1726`（default） |
| `pinctrl-rockchip.c` | `:3352` / `:3365` / `:3386` |
| `qcom/pinctrl-msm.c` | `:1450` / `:1457` |
| `freescale/pinctrl-imx.c` | `:892` / `:899` |
| `nomadik/pinctrl-nomadik.c` | `:1824` / `:1835` |
| `tegra/pinctrl-tegra.c` | `:776` |

**用途是"pin controller 自己给 hog 的引脚做 PM 切换"**——它们读的是 `pctldev->hog_default` / `hog_sleep`（§4.4），不是某个消费者设备的状态。**错用会怎样**：普通外设驱动照着 rockchip 的写法调 `pinctrl_force_sleep(pctldev)`——首先你拿不到 `pctldev`（消费者只有 `struct pinctrl *`），其次即使拿到了，切的也是控制器自己的 hog 状态而不是你的引脚。消费者要的是 §2.4 那三个 `pinctrl_pm_select_*`。

### §6.3 `pinconf.c` 一个符号都不导出

```bash
grep -c 'EXPORT_SYMBOL' drivers/pinctrl/pinconf.c        # 0
```

`pinconf_check_ops` / `pinconf_validate_map` / `pinconf_set_config` 等全部只在 `drivers/pinctrl/pinconf.h` 里声明，供核心内部使用。**含义：pin config 这一层没有任何面向驱动的公开 helper**——驱动能用的只有 `pinconf-generic.h` 里的那套（`04-extensions.md` §3）。

### §6.4 `pinctrl_select_bound_state` —— 有名字、无出口

| | 事实 |
|---|---|
| 定义 | `core.c:1562-1575` |
| `static` 吗 | **是** |
| EXPORT | **没有** |
| 头文件 | 没有任何声明 |

它是 §2.4 三个 PM API 和 `pinctrl_select_default_state()` 的共同实现。**列在这里是因为它在 debugfs / 日志里会露脸**（`dev_err "failed to activate pinctrl state %s"` `:1572-1573` 出自它），读者搜到这条日志会想找这个函数——找到了也没法调用。

### §6.5 `#pinctrl-cells` 那两个函数

| 符号 | 定义 | 声明 | EXPORT | 结论 |
|---|---|---|---|---|
| `pinctrl_count_index_with_args` | `devicetree.c:357-369` | 私有 `drivers/pinctrl/devicetree.h:15-16` | **无** | 只能同目录用 |
| `pinctrl_parse_index_with_args` | `devicetree.c:416-434` | 私有 `devicetree.h:18-20` | **无** | 同上 |

实际使用者只有 **2** 个文件：`pinctrl-single.c`（`:1011` / `:1030` / `:1126` / `:1158`）和 `ti/pinctrl-ti-iodelay.c`（`:498` / `:528`）。`!CONFIG_OF` 时两个 stub 都 `return -ENODEV`（`devicetree.h:34-46` 一带）。详见 `04-extensions.md` §6。

### §6.6 四个 `static inline` 的消费者便利函数

这四个**不在** `EXPORT_SYMBOL` 列表里，但**能用**——因为它们是 `include/linux/pinctrl/consumer.h` 里的 `static inline`，在调用方就地展开：

| 符号 | 位置 | 函数体 | `__must_check` |
|---|---|---|---|
| `pinctrl_get_select(dev, name)` | `consumer.h:148-172` | `pinctrl_get` → `lookup_state` → `select_state`，任一失败就 `pinctrl_put` 后返回错误 | 是 |
| `pinctrl_get_select_default(dev)` | `consumer.h:174-178` | `return pinctrl_get_select(dev, PINCTRL_STATE_DEFAULT);` | 是 |
| `devm_pinctrl_get_select(dev, name)` | `consumer.h:180-204` | 同 `pinctrl_get_select` 但用 `devm_*` 版 | 是 |
| `devm_pinctrl_get_select_default(dev)` | `consumer.h:206-210` | `return devm_pinctrl_get_select(dev, PINCTRL_STATE_DEFAULT);` | 是 |

`devm_pinctrl_get_select()` 的完整错误处理（这是它比手写三步好的唯一理由）：

```c
	p = devm_pinctrl_get(dev);
	if (IS_ERR(p))
		return p;
	s = pinctrl_lookup_state(p, name);
	if (IS_ERR(s)) {
		devm_pinctrl_put(p);          /* 立刻回收，不等 devm 兜底 */
		return ERR_CAST(s);
	}
	ret = pinctrl_select_state(p, s);
	if (ret < 0) {
		devm_pinctrl_put(p);
		return ERR_PTR(ret);
	}
	return p;
```

四个都带 `__must_check`，**忽略返回值会编译 warning**。

**它们和 `pinctrl_select_default_state()` 的区别**（这一组最容易混）：

| | `devm_pinctrl_get_select_default(dev)` | `pinctrl_select_default_state(dev)` |
|---|---|---|
| 谁的句柄 | **自己新拿一个** | **用核心建好的 `dev->pins->p`** |
| `dev->pins` 为 NULL 时 | 照常工作（自己 get） | 直接 `return 0`，什么都不做 |
| 返回值 | `struct pinctrl *`（可能是 `ERR_PTR`） | `int` |
| 典型场合 | probe 里主动应用 default | reset / 错误恢复时重新应用 |

**错用会怎样**：在 `dev->pins == NULL` 的设备（比如 DTS 没写 `pinctrl-0`，或 `of_node_reused`）上调 `pinctrl_select_default_state()` → **静默返回 0**，你以为配好了。要真正应用就得用 `devm_pinctrl_get_select_default()`，它会返回 `-ENODEV` 之类的真错误。

---

## 下一步读什么

| 你想知道 | 去哪 |
|---|---|
| DTS 到底怎么写、每个属性谁消费、写错了怎么定位 | `06-devicetree.md`（本篇的角色 5） |
| debugfs 里哪个文件能验证"引脚真配上了" | `07-debug.md` §3 |
| 上面这些 API 的完整签名、错误码语义、上下文（能不能睡） | `01-api.md` |
| `pinctrl_select_state()` 内部到底做了什么、锁在哪 | `03-internals.md` Part A §A9 / Part B §B1 |
| `pinctrl_generic_*` / `pinctrl_utils_*` 三族的完整 API 表 | `04-extensions.md` §1 / §2 / §4 |
| `struct pinctrl` 和 `struct pinctrl_dev` 到底谁是谁 | `02-datastructures.md` §1.1 |
| 用大白话讲"为什么消费者一行代码都不用写" | `08-feynman.md` |
| 本篇每条断言的取证口径、未验证项 | `99-evidence.md` §3 / §5 |
