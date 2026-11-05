---
title: 使用指南
---

# base 的使用骨架

## §0 使用前必读

### §0.1 本篇代码没有编译过

⚠️ 本篇所有代码片段都是骨架，**没有编译、没有运行**。它们的用途是"照着这个形状写"，不是"复制粘贴就能跑"。具体地：

- 头文件包含只列关键的几个，实际编译还需要 `linux/module.h`、`linux/of.h` 等
- 错误路径为了让形状清楚而做了简化，真实驱动的回滚顺序要按自己申请资源的顺序倒着来
- 所有出现的符号名、宏名、标志名都已在 6.1.158 树上逐个 grep 确认存在（核对表见 §11），但"符号存在"不等于"这样组合起来能编译"

未验证项的完整分层登记在 `99-evidence.md`。

### §0.2 本子系统有哪些角色

`drivers/base/` 是元框架：它的"使用者"几乎就是整棵驱动树。按**读者要写的代码类型**枚举，一共九个角色：

| 角色 | 要写什么 | 用到的核心 API 族 | 本篇 |
| --- | --- | --- | --- |
| A 平台设备驱动作者 | `platform_driver` + `probe()`/`remove()` | `platform_*`、`devm_*` | §1 |
| B 平台设备提供方 | 板级/胶水代码里造出 `platform_device` | `platform_device_*` | §2 |
| C 托管资源使用者 | 自定义 release、分组回滚 | `devres_*`、`devm_add_action*` | §3 |
| D 新总线实现者 | 一个 `bus_type` + 一对 register 包装 | `bus_register`、`driver_register`、`device_register` | §4 |
| E class 实现者 | 一个 `class` + 设备创建 | `class_register`、`device_create` | §5 |
| F sysfs 属性作者 | `DEVICE_ATTR_*` + 属性组 | `DEVICE_ATTR_RO`、`ATTRIBUTE_GROUPS`、`dev_groups` | §6 |
| G 依赖关系声明者 | 显式建供需链接、实现 `sync_state()` | `device_link_add`、`.sync_state` | §7 |
| H 延迟探测处理者 | `probe()` 里的返回值纪律 | `-EPROBE_DEFER`、`dev_err_probe` | §8 |
| I 子框架消费者 | component / auxiliary 两侧 | 见 `04-extensions.md` | §9 |

角色 A 是数量级最大的一个：全树 `module_platform_driver` 的使用文件数是 2573（重跑：`grep -rlw 'module_platform_driver' --include=*.c drivers/ | wc -l`）。角色 D 相反，全树只有 146 个 `struct bus_type` 实例（`grep -rn 'struct bus_type [a-z_]* = {' --include=*.c . | wc -l`）。

### §0.3 按任务找章节

| 我要做的事 | 去哪一节 |
| --- | --- |
| 写一个读 DT 节点、映射寄存器、申请中断的驱动 | §1 |
| 没有 DT 节点，要用代码造设备 | §2 |
| 想让某个资源在 `probe()` 失败时自动回滚 | §3.1 |
| 想让一整批资源作为一个单位回滚 | §3.3 |
| 要发明一条新总线（如一种新的固件通道） | §4 |
| 只想在 `/sys/class/` 下露一组设备，不需要 match/probe | §5 |
| 想给设备加一个 sysfs 文件 | §6 |
| 我的设备必须在另一个设备之后才能工作 | §7 |
| `probe()` 里依赖的资源还没就绪 | §8 |
| 一个 DRM 显示管线要等多个子设备齐了才能起 | §9 |
| 两个名字很像的函数不知道该用哪个 | §10 |

---

## §1 角色 A：平台设备驱动作者

### §1.1 最小骨架

参考实现：`drivers/soc/qcom/icc-bwmon.c`（DT 匹配、arm64）。骨架：

```c
#include <linux/platform_device.h>
#include <linux/mod_devicetable.h>

struct foo_priv {
	void __iomem	*base;
	int		irq;
};

static int foo_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct foo_priv *priv;
	int ret;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(priv->base))
		return PTR_ERR(priv->base);

	priv->irq = platform_get_irq(pdev, 0);
	if (priv->irq < 0)
		return priv->irq;		/* 已经打过日志，见 §8.2 */

	ret = devm_request_threaded_irq(dev, priv->irq, foo_isr, foo_thread,
					IRQF_ONESHOT, dev_name(dev), priv);
	if (ret)
		return dev_err_probe(dev, ret, "failed to request IRQ\n");

	platform_set_drvdata(pdev, priv);
	return 0;
}

static int foo_remove(struct platform_device *pdev)
{
	struct foo_priv *priv = platform_get_drvdata(pdev);

	/* 只需要停硬件；devm_* 申请的东西核心会自动回滚 */
	foo_hw_stop(priv);
	return 0;
}

static const struct of_device_id foo_of_match[] = {
	{ .compatible = "vendor,foo" },
	{ }
};
MODULE_DEVICE_TABLE(of, foo_of_match);

static struct platform_driver foo_driver = {
	.probe	= foo_probe,
	.remove	= foo_remove,
	.driver	= {
		.name		= "foo",
		.of_match_table	= foo_of_match,
	},
};
module_platform_driver(foo_driver);
```

对照真实代码：`drivers/soc/qcom/icc-bwmon.c:585` 是 `devm_kzalloc()`，`:595` 是 `platform_get_irq()`，`:616` 是 `devm_request_threaded_irq()`，`:622` 是 `platform_set_drvdata()`，`:690-697` 是 `platform_driver` 实例，`:698` 是 `module_platform_driver()`。

### §1.2 三处容易写错的地方

| 位置 | 陷阱 | 正确写法 |
| --- | --- | --- |
| `.driver.name` | 忘了填 → `driver_register()` 里 `driver_find()` 用它做去重，空名字会撞车 | 必填，且全总线唯一 |
| `.driver.of_match_table` | 只写 `MODULE_DEVICE_TABLE` 不填这一项 → 模块能自动加载但匹配不上 | 两处都要写 |
| `remove()` 里手工 `iounmap()` | 与 `devm_platform_ioremap_resource()` 双重释放 | 用了 `devm_` 前缀就不要手工释放 |

⚠️ `module_platform_driver()` 是宏（`include/linux/platform_device.h:274`），它展开出 `module_init`/`module_exit` 两个入口。如果驱动还要做别的初始化，就不能用这个宏，得手写 `module_init()` 并在里面调 `platform_driver_register()`。

⚠️ `platform_driver_register()` 本身也是宏（`include/linux/platform_device.h:244`），真正导出的函数叫 `__platform_driver_register()`（`drivers/base/platform.c:861`）。宏的作用是把 `THIS_MODULE` 塞进去。所以在 `System.map` 里搜 `platform_driver_register` 会搜不到。

### §1.3 私有数据放哪里

三个层次，别混：

| 写法 | 存在哪个字段 | 什么时候用 |
| --- | --- | --- |
| `platform_set_drvdata(pdev, p)` | `pdev->dev.driver_data` | 平台驱动，最常用 |
| `dev_set_drvdata(dev, p)` | `dev->driver_data`（同一个字段） | 手里只有 `struct device *` |
| `dev_get_platdata(dev)` | `pdev->dev.platform_data` | 读**设备提供方**塞进来的板级配置（§2.3），不是自己写的 |

`platform_set_drvdata()` / `platform_get_drvdata()` 是 `static inline`（`include/linux/platform_device.h:263` / `:258`），底下就是 `dev_set_drvdata()` / `dev_get_drvdata()`（`include/linux/device.h:766` / `:761`）。⚠️ `driver_data` 和 `platform_data` 是两个不同字段，前者归驱动、后者归设备提供方，互相覆盖会静默出错。

---

## §2 角色 B：平台设备提供方

有 DT 的时候设备是 `of_platform_populate()` 造的，不需要写代码。以下三种情况才需要自己造：

### §2.1 一次性、带资源：`platform_device_register_full()`

```c
static const struct resource foo_res[] = {
	DEFINE_RES_MEM(0x10000000, 0x1000),
	DEFINE_RES_IRQ(42),
};

struct platform_device_info foo_info = {
	.parent		= parent_dev,
	.name		= "foo",
	.id		= PLATFORM_DEVID_AUTO,
	.res		= foo_res,
	.num_res	= ARRAY_SIZE(foo_res),
	.data		= &foo_pdata,
	.size_data	= sizeof(foo_pdata),
	.dma_mask	= DMA_BIT_MASK(32),
};

pdev = platform_device_register_full(&foo_info);
if (IS_ERR(pdev))
	return PTR_ERR(pdev);
```

`platform_device_register_full()` 在 `drivers/base/platform.c:806`，内部依次调 `platform_device_add_resources()`（`:604`）和 `platform_device_add_data()`（`:632`）。全树 50 个文件在用（`grep -rlw 'platform_device_register_full' --include=*.c . | wc -l`）。

### §2.2 分步组装：alloc / add_* / add

需要在注册前再改点什么时用三段式：

```c
pdev = platform_device_alloc("foo", PLATFORM_DEVID_AUTO);
if (!pdev)
	return -ENOMEM;

ret = platform_device_add_resources(pdev, foo_res, ARRAY_SIZE(foo_res));
if (ret)
	goto err_put;

pdev->dev.parent = parent_dev;

ret = platform_device_add(pdev);		/* 从这一行起，pdev 归核心管 */
if (ret)
	goto err_put;
return 0;

err_put:
	platform_device_put(pdev);		/* 注册失败只能 put，不能 kfree */
	return ret;
```

对应实现：`platform_device_alloc()` 在 `drivers/base/platform.c:576`，`platform_device_add()` 在 `:656`，`platform_device_put()` 在 `:548`。

⚠️ `platform_device_add()` 失败之后**不要 `kfree(pdev)`**，只能 `platform_device_put()`。这和 `device_register()` 的 kerneldoc 说的是同一条规则（`drivers/base/core.c:3772-3774` 那段注释原文写的是 "_Never_ directly free @dev after calling this function, even if it returned an error"）。

### §2.3 platform_data 的交接

`.data` / `.size_data` 会被复制一份挂到 `pdev->dev.platform_data`，驱动侧用 `dev_get_platdata(dev)`（`include/linux/device.h:1002`）读。两侧要用同一个结构定义，这个约定编译器查不出来——它是 `void *`。

⚠️ 走 DT 的驱动不该读 `platform_data`。DT 侧的等价物是 `of_device_get_match_data(dev)`（真实用法见 `drivers/soc/qcom/icc-bwmon.c:589`）。

---

## §3 角色 C：托管资源使用者

机制原理见 `03-internals.md` §B2，内部结构见 `02-datastructures.md` §8，现成接口清单见 `01-api.md` §7.3。本节只讲三种自己动手的写法。

### §3.1 一次性动作：`devm_add_action_or_reset()`

最常用的自定义托管形式——把"关掉它"包成一个函数：

```c
static void foo_disable(void *data)
{
	struct foo_priv *priv = data;

	foo_hw_disable(priv);
}

/* probe() 里 */
	ret = foo_hw_enable(priv);
	if (ret)
		return ret;

	ret = devm_add_action_or_reset(dev, foo_disable, priv);
	if (ret)
		return ret;		/* 注册失败时 foo_disable() 已被调过 */
```

`devm_add_action()` 在 `drivers/base/devres.c:738`；`devm_add_action_or_reset()` 是包在它外面的 `static inline`（`include/linux/device.h:250`）。

⚠️ 两者的差别就在失败路径上：`devm_add_action()` 返回 `-ENOMEM` 时**什么都没做**，调用者必须自己去 disable；`devm_add_action_or_reset()` 会在登记失败时替你调一次 `action`。写新代码一律用后者——它把"登记失败"和"已经回滚"合并成了一个返回值。全树 450 个文件在用后者（`grep -rlw 'devm_add_action_or_reset' --include=*.c drivers/ | wc -l`）。

### §3.2 自定义 release 与私有数据

需要在 release 里访问一块结构时，用 `devres_alloc()` 三段式：

```c
struct foo_res {
	struct clk	*clk;
	int		gpio;
};

static void foo_res_release(struct device *dev, void *res)
{
	struct foo_res *r = res;

	gpio_free(r->gpio);
	clk_put(r->clk);
}

/* probe() 里 */
	struct foo_res *r = devres_alloc(foo_res_release, sizeof(*r), GFP_KERNEL);
	if (!r)
		return -ENOMEM;

	r->clk = ...;
	r->gpio = ...;
	devres_add(dev, r);		/* 这一行之后 r 归核心管 */
```

`devres_alloc()` 是宏（`include/linux/device.h:172`），底下是 `__devres_alloc_node()`（`drivers/base/devres.c:159`）。`devres_add()` 在 `drivers/base/devres.c:240`。

⚠️ 在 `devres_add()` 之前失败要用 `devres_free()`（`drivers/base/devres.c:220`）而不是 `kfree()`——它跳过了 `devres_node` 头部。`devres_add()` 之后就不要再碰这个指针。

### §3.3 分组：让一批资源一起回滚

`probe()` 里分阶段申请、某一阶段失败要整段回滚时用分组：

```c
	void *gid = devres_open_group(dev, NULL, GFP_KERNEL);
	if (!gid)
		return -ENOMEM;

	ret = foo_stage_a(dev);		/* 内部全用 devm_* 申请 */
	if (ret)
		goto err_release;
	ret = foo_stage_b(dev);
	if (ret)
		goto err_release;

	devres_close_group(dev, gid);
	return 0;

err_release:
	devres_release_group(dev, gid);
	return ret;
```

`devres_open_group()` 在 `drivers/base/devres.c:549`，`devres_close_group()` 在 `:607`，`devres_release_group()` 在 `:666`。

真实用户里有一处 arm64 的：`drivers/firmware/arm_scmi/driver.c:1550`（`gid = devres_open_group(handle->dev, NULL, GFP_KERNEL)`）和 `drivers/firmware/arm_scmi/notify.c:1651`。全树 38 个调用点（`grep -rnw 'devres_open_group' --include=*.c . | grep -v EXPORT | wc -l`），说明这是个小众机制。

⚠️ 传 `NULL` 当 id 时，`devres_release_group(dev, NULL)` 释放的是**最近一个还没关闭的组**，不是所有组。想精确指定就传一个稳定的非空指针当 id——`drivers/base/component.c:245` 用聚合设备指针当 id 就是这个用法（细节见 `04-extensions.md` §1.3）。

---

## §4 角色 D：新总线实现者

### §4.1 需要写的四件东西

一条新总线的最小完整实现是四件东西，缺一不可：

| 要件 | 形式 | 缺了会怎样 |
| --- | --- | --- |
| 一个 `struct bus_type` | 全局变量，填 `.name` / `.match` / `.probe` / `.remove` | 没法 `bus_register()` |
| `bus_register()` / `bus_unregister()` 的调用点 | 通常在模块 init/exit 里 | 驱动注册时报 "bus was not initialized" |
| 设备侧包装 | `xxx_device_register()`：填 `dev->bus`、`dev->release`、`dev_set_name()`，再 `device_register()` | 设备进不了总线 |
| 驱动侧包装 | `xxx_driver_register()`：填 `driver.bus` / `.name` / `.owner`，再 `driver_register()` | 驱动挂不到总线上 |

### §4.2 参考实现：`drivers/firmware/arm_ffa/bus.c`

这是全树里最适合当模板的一个——233 行，arm64 原生，四件东西全齐、没有多余的历史包袱：

| 要件 | 位置 |
| --- | --- |
| `struct bus_type ffa_bus_type` | `drivers/firmware/arm_ffa/bus.c:95-102` |
| `.match` = `ffa_device_match()` | `drivers/firmware/arm_ffa/bus.c:20` |
| `.probe` = `ffa_device_probe()`（转发到驱动的 `probe()`） | `drivers/firmware/arm_ffa/bus.c:46` |
| `.remove` = `ffa_device_remove()`（`remove` 是可选的，先判空） | `drivers/firmware/arm_ffa/bus.c:54` |
| `.uevent` = `ffa_device_uevent()`（吐 `MODALIAS=`） | `drivers/firmware/arm_ffa/bus.c:62` |
| `.dev_groups` = `ffa_device_attributes_groups`（由 `ATTRIBUTE_GROUPS()` 生成） | `drivers/firmware/arm_ffa/bus.c:93` |
| `dev->release` = `ffa_release_device()` | `drivers/firmware/arm_ffa/bus.c:132` |
| 设备侧包装 `ffa_device_register()` | `drivers/firmware/arm_ffa/bus.c:174-211` |
| 驱动侧包装 `ffa_driver_register()` | `drivers/firmware/arm_ffa/bus.c:105-123` |
| `bus_register()` / `bus_unregister()` | `drivers/firmware/arm_ffa/bus.c:225` / `drivers/firmware/arm_ffa/bus.c:231` |
| 总线初始化被谁调 | `drivers/firmware/arm_ffa/driver.c:796`（失败跳 `drivers/firmware/arm_ffa/driver.c:850` 的 `arm_ffa_bus_exit()`） |

### §4.3 骨架

```c
static int foo_device_match(struct device *dev, struct device_driver *drv)
{
	const struct foo_device_id *id = to_foo_driver(drv)->id_table;
	struct foo_device *fdev = to_foo_dev(dev);

	for (; id->name[0]; id++)
		if (!strcmp(id->name, fdev->name))
			return 1;
	return 0;
}

static int foo_device_probe(struct device *dev)
{
	/* 把 struct device * 换算回自己的设备类型，再转发 */
	return to_foo_driver(dev->driver)->probe(to_foo_dev(dev));
}

static void foo_device_remove(struct device *dev)
{
	struct foo_driver *fdrv = to_foo_driver(dev->driver);

	if (fdrv->remove)			/* remove 是可选回调 */
		fdrv->remove(to_foo_dev(dev));
}

struct bus_type foo_bus_type = {
	.name	= "foo",
	.match	= foo_device_match,
	.probe	= foo_device_probe,
	.remove	= foo_device_remove,
	.uevent	= foo_device_uevent,
};

/* 设备侧：注意 release 与失败路径 */
struct foo_device *foo_device_register(const char *name)
{
	struct foo_device *fdev;
	int ret;

	fdev = kzalloc(sizeof(*fdev), GFP_KERNEL);
	if (!fdev)
		return NULL;

	fdev->dev.bus     = &foo_bus_type;
	fdev->dev.release = foo_release_device;	/* 必填，否则 device_add() 会 WARN */
	dev_set_name(&fdev->dev, "foo-%s", name);

	ret = device_register(&fdev->dev);
	if (ret) {
		put_device(&fdev->dev);		/* 不是 kfree */
		return NULL;
	}
	return fdev;
}

/* 驱动侧 */
int foo_driver_register(struct foo_driver *drv, struct module *owner)
{
	drv->driver.bus   = &foo_bus_type;
	drv->driver.name  = drv->name;
	drv->driver.owner = owner;
	return driver_register(&drv->driver);
}
```

### §4.4 四个必须知道的约束

⚠️ **`dev->release` 必填。** 三条兜底链（`dev->release` → `dev->type->release` → `dev->class->dev_release`）全空时，`device_release()` 打一条 WARN 就返回，什么都不释放（`drivers/base/core.c:2502-2510`）。⚠️ 注意这条 WARN 的触发时机是**最后一次 `put_device()`**，不是 `device_add()`——所以一个引用计数从没归零的设备，这个错误可以一直不暴露。`drivers/firmware/arm_ffa/bus.c:193` 是 `dev->release = ffa_release_device;`，release 函数本体（`drivers/firmware/arm_ffa/bus.c:132`）里做的是 `ida_free()` + `kfree(ffa_dev)`——释放的是包装结构，不是 `struct device` 本身。

⚠️ **`.probe` 和驱动的 `.probe` 不能同时用同一层。** `driver_register()`（`drivers/base/driver.c:222`）里有一段检查：`bus->probe && drv->probe` 同时非空时打印 "Driver '%s' needs updating - please use bus_type methods"（`drivers/base/driver.c:236-237`）。⚠️ 它只是 `pr_warn`，**不会失败**，所以很容易长期带着这条警告跑。正确形状是总线层填 `.probe`、驱动层用自己的私有回调（`foo_driver->probe`），像 `drivers/firmware/arm_ffa/bus.c:46-52` 那样转发。

⚠️ **`.remove` 的返回值是 `void`。** `include/linux/device/bus.h:96` 的原型是 `void (*remove)(struct device *dev)`；`struct device_driver` 的 `.remove` 也是 `void`（`include/linux/device/driver.h:111`）。⚠️ 只有 `struct platform_driver` 的 `.remove` 还是 `int`（`02-datastructures.md` §7.2），这个不一致是 6.1 时期的历史遗留。

⚠️ **名字重复会被拒。** `driver_register()` 里 `driver_find()` 命中就返回 `-EBUSY` 并打印 "Driver '%s' is already registered, aborting..."（`drivers/base/driver.c:240-244`）。

### §4.5 什么时候不该发明总线

如果你的"总线"只是想给一组设备一个统一的 sysfs 目录、并不需要 match/probe，那要的是 `class`（§5）而不是 `bus_type`。判据是一句话：**有没有"驱动"这个概念**。有 → `bus_type`；没有 → `class`。

---

## §5 角色 E：class 实现者

### §5.1 两种写法

| 写法 | 适用 | 位置 |
| --- | --- | --- |
| `class_register(&my_class)` + `device_register()` | 需要控制 `.dev_release` / `.dev_groups` / `.class_groups` | 宏在 `include/linux/device/class.h:93`，实现 `__class_register()` 在 `drivers/base/class.c:153` |
| `class_create(THIS_MODULE, "name")` + `device_create()` | 只要一个字符设备目录，不需要自定义字段 | 宏在 `include/linux/device/class.h:273`，实现 `__class_create()` 在 `drivers/base/class.c:231` |

⚠️ `class_register()` 和 `class_create()` 都是宏。前者的存在理由是要在调用点静态分配一个 `lock_class_key`（`include/linux/device/class.h:93-96`），所以 `System.map` 里只有 `__class_register`。

### §5.2 骨架（完整控制版）

参考实现：`drivers/base/devcoredump.c:244-250`（`devcd_class`），这是本目录内自带的一个。

```c
static void foo_dev_release(struct device *dev)
{
	kfree(to_foo(dev));
}

static struct class foo_class = {
	.name		= "foo",
	.owner		= THIS_MODULE,
	.dev_release	= foo_dev_release,	/* 相当于 bus 侧的 dev->release */
	.dev_groups	= foo_dev_groups,	/* 每个设备的属性 */
	.class_groups	= foo_class_groups,	/* 类自己的属性，落在 /sys/class/foo/ */
};

static int __init foo_init(void)
{
	return class_register(&foo_class);
}

/* 造一个成员设备 */
	struct foo *f = kzalloc(sizeof(*f), GFP_KERNEL);

	f->dev.class  = &foo_class;
	f->dev.parent = parent;
	dev_set_name(&f->dev, "foo%d", id);
	ret = device_register(&f->dev);
	if (ret) {
		put_device(&f->dev);
		return ret;
	}
```

⚠️ `.dev_release` 填在 `class` 上时，就不要再给每个设备填 `dev->release`——`device_release()` 的三级兜底是 `dev->release` → `dev->type->release` → `dev->class->dev_release`（`drivers/base/core.c:2502-2507`），两处都填等于上面那个把下面那个屏蔽掉了。

### §5.3 简化写法

```c
	foo_class = class_create(THIS_MODULE, "foo");
	if (IS_ERR(foo_class))
		return PTR_ERR(foo_class);

	dev = device_create(foo_class, parent, devt, drvdata, "foo%d", id);
	if (IS_ERR(dev)) { ... }

	/* 拆除 */
	device_destroy(foo_class, devt);
	class_destroy(foo_class);
```

`device_create()` 在 `drivers/base/core.c:4426`，`device_destroy()` 在 `:4492`，`class_destroy()` 在 `drivers/base/class.c:266`。

⚠️ `device_create()` 造出来的设备是核心 `kzalloc` 的，`release` 也由核心提供，所以只能用 `device_destroy()` 拆——它按 `devt` 反查再 `device_unregister()`。⚠️ 这条路径**没法附加自己的私有结构**，只能通过 `drvdata` 参数挂一个指针。需要私有结构就走 §5.2。

---

## §6 角色 F：sysfs 属性作者

### §6.1 三步

```c
/* 1. 定义 show/store */
static ssize_t temperature_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	struct foo *f = dev_get_drvdata(dev);

	return sysfs_emit(buf, "%d\n", foo_read_temp(f));
}
static DEVICE_ATTR_RO(temperature);		/* 名字必须和函数前缀一致 */

/* 2. 收进数组，再用宏生成 group / groups */
static struct attribute *foo_attrs[] = {
	&dev_attr_temperature.attr,
	NULL,
};
ATTRIBUTE_GROUPS(foo);				/* 生成 foo_group 和 foo_groups */

/* 3. 挂上去——三个挂点选一个 */
static struct bus_type foo_bus_type = {
	.dev_groups = foo_groups,		/* 该总线上所有设备 */
};
static struct device_driver foo_drv = {
	.dev_groups = foo_groups,		/* 该驱动绑上的设备 */
};
static struct class foo_class = {
	.dev_groups = foo_groups,		/* 该类的所有设备 */
};
```

宏的位置：`DEVICE_ATTR_RO` 在 `include/linux/device.h:136`（`DEVICE_ATTR_RW` 在 `:132`、裸的 `DEVICE_ATTR` 在 `:127`、只允许 root 读的 `DEVICE_ATTR_ADMIN_RO` 在 `:138`）；`ATTRIBUTE_GROUPS` 在 `include/linux/sysfs.h:159`，它先造一个 `_name##_group` 再展开 `__ATTRIBUTE_GROUPS`（`include/linux/sysfs.h:153`）造出 `_name##_groups` 数组。

真实例子：`drivers/firmware/arm_ffa/bus.c:88-93` 就是这个形状（两个属性 + `ATTRIBUTE_GROUPS(ffa_device_attributes)`），生成的 `ffa_device_attributes_groups` 填在 `drivers/firmware/arm_ffa/bus.c:101`。

### §6.2 挂点的选择

| 挂点 | 生效范围 | 什么时候建目录项 |
| --- | --- | --- |
| `bus_type.dev_groups`（`include/linux/device/bus.h:89`） | 该总线上每个设备 | `device_add()` 里 |
| `device_driver.dev_groups`（`include/linux/device/driver.h:116`） | 绑上这个驱动的设备 | 绑定成功后 |
| `class.dev_groups` | 该类每个设备 | `device_add()` 里 |
| `device.groups` | 单个设备 | `device_add()` 里 |
| `device_add_group()` / `devm_device_add_group()` | 运行期后补 | 调用时 |

⚠️ `device_add_group()` 是 `static inline`（`include/linux/device.h:1039`），底下是导出的 `device_add_groups()`（`drivers/base/core.c:2750`）。托管版本 `devm_device_add_group()` 是真函数（`drivers/base/core.c:2801`）。⚠️ 单数版是 inline、复数版是导出函数，这一对在 `System.map` 里只看得见复数版。

⚠️ 用 `sysfs_emit()` 而不是 `sprintf()`——后者不知道 PAGE_SIZE 边界。

### §6.3 属性可见性

需要按运行期条件隐藏某个文件时，填 `attribute_group.is_visible`：

```c
static umode_t foo_attr_visible(struct kobject *kobj, struct attribute *a, int n)
{
	struct device *dev = kobj_to_dev(kobj);

	if (a == &dev_attr_temperature.attr && !foo_has_sensor(dev))
		return 0;			/* 返回 0 = 这个文件不建 */
	return a->mode;
}
```

⚠️ 这时候不能用 `ATTRIBUTE_GROUPS()` 宏了——它只填 `.attrs`。得手写 `struct attribute_group` 并同时填 `.attrs` 和 `.is_visible`。

---

## §7 角色 G：依赖关系声明者

背景机制见 `01-api.md` §6、状态机见 `03-internals.md` §B3、fw_devlink 的自动建链见 `03-internals.md` §B4。本节只讲手工建链和 `sync_state()`。

### §7.1 什么时候需要手工建链

大部分依赖 fw_devlink 会从 DT 的 phandle 自动推出来，不需要写代码。手工 `device_link_add()` 只在两种情况下必要：

1. 依赖关系**不在固件描述里**——比如供方是运行期才确定的（`drivers/firmware/arm_scmi/optee.c:323`）
2. 需要非默认的语义（比如要带上 `DL_FLAG_PM_RUNTIME` 让 runtime PM 也跟着走）

全树 64 个文件调 `device_link_add()`（`grep -rlw 'device_link_add' --include=*.c drivers/ | wc -l`），相对 2573 个平台驱动来说是极少数。

### §7.2 骨架

```c
	struct device_link *link;

	link = device_link_add(consumer, supplier, DL_FLAG_AUTOREMOVE_CONSUMER);
	if (!link)
		return -EINVAL;		/* 失败只返回 NULL，不返回 ERR_PTR */
```

`device_link_add()` 在 `drivers/base/core.c:785`。arm64 上的真实用法：`drivers/firmware/arm_scmi/bus.c:222-229`（`scmi_device_link_add()`，用 `DL_FLAG_AUTOREMOVE_CONSUMER` 并 `WARN_ON(!link)`）、`drivers/firmware/arm_scmi/virtio.c:379`、`drivers/pci/controller/dwc/pci-imx6.c:503` 和 `drivers/pci/controller/dwc/pci-imx6.c:516`。

⚠️ **返回值是 NULL 而不是 ERR_PTR。** `01-api.md` §13.2 里讲的两派返回约定，`device_link_add()` 属于 NULL 派，所以 `IS_ERR()` 检查在这里是错的。

### §7.3 十个标志怎么选

`DL_FLAG_*` 一共十个（`include/linux/device.h:322-331`），逐个语义见 `01-api.md` §6.1。选择的判据只有两条：

| 我想要 | 用哪个 |
| --- | --- |
| 链接跟着消费方的驱动一起消失 | `DL_FLAG_AUTOREMOVE_CONSUMER`（`include/linux/device.h:323`） |
| 链接跟着供方的驱动一起消失 | `DL_FLAG_AUTOREMOVE_SUPPLIER`（`include/linux/device.h:326`） |
| 自己管生命周期，核心别插手 | `DL_FLAG_STATELESS`（`:322`）+ 手工 `device_link_del()` |
| runtime PM 也要跟着这条依赖走 | 加 `DL_FLAG_PM_RUNTIME`（`:324`） |

⚠️ `DL_FLAG_MANAGED`（`:328`）、`DL_FLAG_INFERRED`（`:330`）、`DL_FLAG_CYCLE`（`:331`）是核心内部用的，驱动不要传。

### §7.4 `sync_state()`：接管引导态的唯一钩子

场景：bootloader 已经把某个共享资源（时钟、interconnect 带宽、regulator）开到某个值，驱动起来之后不能立刻按自己的想法关掉——得等所有消费者都探测完。`sync_state()` 就是"所有消费者都到位了"这个时刻的回调。

```c
static struct platform_driver foo_driver = {
	.probe	= foo_probe,
	.driver	= {
		.name		= "foo",
		.of_match_table	= foo_of_match,
		.sync_state	= foo_sync_state,	/* 只会被调一次 */
	},
};
```

字段位置：`device_driver.sync_state` 在 `include/linux/device/driver.h:110`，`bus_type.sync_state` 在 `include/linux/device/bus.h:95`。语义细节见 `02-datastructures.md` §3.3。

真实用法：全树 36 个赋值点（`grep -rn '\.sync_state[[:space:]]*=' --include=*.c --include=*.h drivers/ include/ | wc -l`），其中绝大多数填的是同一个函数 `icc_sync_state`（`drivers/interconnect/core.c:1125`），例如 `drivers/interconnect/qcom/sdm845.c:692`、`drivers/interconnect/samsung/exynos.c:188`、`drivers/memory/tegra/tegra30-emc.c:1757`。⚠️ 这说明 `sync_state()` 在 6.1 里几乎是 interconnect 一家的专用机制——`drivers/memory/tegra/mc.c:947` 是少数填了自己函数（`tegra_mc_sync_state`）的例外。

⚠️ `sync_state()` 依赖 device_link 的存在：核心要先知道"谁是消费者"才能判断"都到位了"。没有链接（无论是 fw_devlink 自动建的还是手工建的）时，这个回调不会被调。

---

## §8 角色 H：延迟探测的正确写法

### §8.1 `-EPROBE_DEFER` 的纪律

`probe()` 里发现依赖还没就绪时返回 `-EPROBE_DEFER`，核心会把设备放回延迟队列稍后重试（循环细节见 `03-internals.md` §A9）。三条纪律：

| 纪律 | 原因 |
| --- | --- |
| 只在"迟早会好"的情况下返回它 | 永远不会好的依赖会让设备无限重试 |
| 返回它之前必须把已申请的非托管资源释放干净 | `probe()` 会被重新调用一次，不释放就泄漏 |
| 不要用 `dev_err()` 打日志 | 每一轮重试都会打一遍，刷屏 |

第三条有专门的工具：`dev_err_probe()`。

### §8.2 `dev_err_probe()` 替代那五行

`dev_err_probe()` 在 `drivers/base/core.c:5038`，它的 kerneldoc（`drivers/base/core.c:5020-5028`）明确说是用来替换这个形状的：

```c
	/* 旧写法 */
	if (err != -EPROBE_DEFER)
		dev_err(dev, ...);
	else
		dev_dbg(dev, ...);
	return err;

	/* 新写法 */
	return dev_err_probe(dev, err, ...);
```

它做三件事（`drivers/base/core.c:5047-5056`）：

1. `err != -EPROBE_DEFER` → `dev_err()`，格式固定为 `"error %pe: %pV"`
2. `err == -EPROBE_DEFER` → 先调 `device_set_deferred_probe_reason()` 把原因存进设备，再 `dev_dbg()`
3. 原样返回 `err`

第 2 条是真正的价值：存进去的原因可以从 `/sys/kernel/debug/devices_deferred` 读出来，比翻 dmesg 有用得多（调试手段见 `07-debug.md`）。

⚠️ kerneldoc 明确说了即使 `err` 不可能是 `-EPROBE_DEFER` 也可以用它（`drivers/base/core.c:5030-5033`）——好处是错误码的打印格式统一、且返回值直接可用。

⚠️ 返回值是 `err` 本身，所以 `return dev_err_probe(dev, ret, "...")` 是完整写法，不要再写 `return ret;`。

### §8.3 已经替你打过日志的函数

有几个查询函数内部就调了 `dev_err_probe()`，再包一层日志就是重复：

| 函数 | 内部行为 |
| --- | --- |
| `platform_get_irq()`（`drivers/base/platform.c:259`） | 失败时调 `dev_err_probe()`（`:265-266`），所以直接 `return priv->irq;` 就够 |
| `platform_get_irq_optional()`（`drivers/base/platform.c:171`） | ⚠️ **不打日志**，失败静默返回 |
| `devm_platform_ioremap_resource()`（`drivers/base/platform.c:122`） | 内部走 `devm_ioremap_resource()`，那一层会打 `dev_err()` |

⚠️ `platform_get_irq()` 和 `platform_get_irq_optional()` 的唯一差别就是这条日志：前者是后者加一个 `dev_err_probe()` 包装（`drivers/base/platform.c:259-268`）。选择判据是"这个中断是不是必需的"——可选中断用 `_optional` 版本，否则每次启动都会看到一条无害的报错。

---

## §9 角色 I：子框架消费者

两个子框架有明确的"两侧"角色，骨架和陷阱都写在 `04-extensions.md` 里，本节只给入口和判据。

### §9.1 component：多个设备凑齐了才能一起起来

典型场景：一个 DRM 显示管线由 CRTC、编码器、PHY 几个独立的 DT 节点组成，任何一个缺了整套就不能工作。

| 我是哪一侧 | 要调什么 | 细节在 |
| --- | --- | --- |
| 组件侧（子设备） | `component_add()`（`drivers/base/component.c:787`）/ `component_del()`（`:802`） | `04-extensions.md` §1.4 |
| 聚合侧（主设备） | `component_match_add()` 逐个登记 + `component_master_add_with_match()`（`:512`）| `04-extensions.md` §1.3 |

⚠️ 聚合侧的 `bind()` 回调里申请的资源会被包在一个 devres 分组里（`drivers/base/component.c:245`），所以组件解绑时会自动回滚——这是 component 与 §3.3 分组机制的唯一交汇点。

### §9.2 auxiliary bus：一个大设备拆成几块

典型场景：一张网卡既是网络设备又提供 RDMA 功能，想让两块功能各自是独立的驱动。

| 我是哪一侧 | 要调什么 | 细节在 |
| --- | --- | --- |
| 提供方（拆分者） | `auxiliary_device_init()`（`drivers/base/auxiliary.c:274`）+ `auxiliary_device_add()` | `04-extensions.md` §2.3 |
| 消费方（辅助驱动） | `auxiliary_driver_register()` + `.id_table` | `04-extensions.md` §2.2 |

⚠️ 匹配是**按名字前缀**做的，`id_table` 里写的是 `"foo_mod.foo_dev"` 这种形式（`drivers/base/auxiliary.c:140` 的示例），不是 compatible 字符串。⚠️ 用它还要在 Kconfig 里 `select AUXILIARY_BUS`。

### §9.3 判据：component 还是 auxiliary

| 问题 | component | auxiliary |
| --- | --- | --- |
| 方向 | 多个设备汇成一个 | 一个设备拆成多个 |
| 谁先存在 | 组件先注册，聚合方等 | 提供方先存在，辅助设备由它造 |
| 有没有 `bus_type` | 没有，纯回调 | 有（`auxiliary_bus_type`，`drivers/base/auxiliary.c:247`） |
| 编译开关 | 无，恒在 | `CONFIG_AUXILIARY_BUS` |

---

## §10 近邻符号区分

本节是独立的一节，因为下面每一组都是"名字只差几个字符、语义差一大截"，散在各节的陷阱表里等于没有入口。

### §10.1 `device_register()` 与 `device_initialize()` + `device_add()`

| 符号 | 位置 | 语义 |
| --- | --- | --- |
| `device_register()` | `drivers/base/core.c:3776` | 就是两行：`device_initialize()` 加 `device_add()` |
| 分开调 | `drivers/base/core.c:3156` / `:3578` | 需要在 `device_add()` 之前就持有引用（比如先 `dev_set_name()` 之外还要做别的） |

kerneldoc 原话是"only call the two helpers separately if have a clearly defined need to use and refcount the device before it is added to the hierarchy"（`drivers/base/core.c:3765-3767`）。⚠️ **错用会怎样**：分开调但忘了 `device_add()`，设备永远不在 sysfs 里也永远不会被匹配，而且没有任何报错。

### §10.2 `device_del()` 与 `device_unregister()`

| 符号 | 位置 | 语义 |
| --- | --- | --- |
| `device_del()` | `drivers/base/core.c:3840` | 只从各子系统里摘掉，**不减引用计数** |
| `device_unregister()` | `drivers/base/core.c:3925` | `device_del()` 加 `put_device()` |

⚠️ **错用会怎样**：只调 `device_del()` 就以为设备释放了 → 内存永久泄漏，且 sysfs 里已经看不见，很难发现。反过来，`device_unregister()` 之后再 `put_device()` → 引用计数下溢。

### §10.3 `devres_close_group()` 与 `devres_release_group()`

| 符号 | 位置 | 语义 |
| --- | --- | --- |
| `devres_close_group()` | `drivers/base/devres.c:607` | 只是"这个组登记完了"，**不释放任何东西** |
| `devres_release_group()` | `drivers/base/devres.c:666` | 立刻释放组内所有资源 |
| `devres_remove_group()` | `drivers/base/devres.c:633` | 拆掉分组标记但**不释放**资源，资源改由设备直接持有 |

⚠️ **错用会怎样**：成功路径上写了 `devres_release_group()` → 刚申请的资源立刻被释放，`probe()` 返回 0 但硬件没配好。这类 bug 不会崩，只会"不工作"。

### §10.4 `devm_add_action()` 与 `devm_add_action_or_reset()`

见 §3.1。一句话：⚠️ 前者登记失败时**不执行** action，后者执行。**错用会怎样**：用了前者但没在失败路径上手工回滚 → 在 `-ENOMEM` 这条极罕见路径上泄漏，几乎测不出来。

### §10.5 `device_link_del()` 与 `device_link_remove()`

| 符号 | 位置 | 参数 |
| --- | --- | --- |
| `device_link_del()` | `drivers/base/core.c:1017` | 拿 `struct device_link *` |
| `device_link_remove()` | `drivers/base/core.c:1033` | 拿 `void *consumer` 加 `struct device *supplier`，自己去查链接 |

⚠️ 两者都只对 `DL_FLAG_STATELESS` 建的链接合法。⚠️ `drivers/base/core.c:745` 那行注释还提到 `device_link_del()`，而 `:1015` 的注释说的是链接被自动删除之后就"no longer allowed"调它——所以自动管理的链接（`DL_FLAG_AUTOREMOVE_*`）不要手工删。

### §10.6 `platform_get_irq()` 与 `platform_get_irq_optional()`

见 §8.3。差别只在日志。⚠️ **错用会怎样**：可选中断用了非 `_optional` 版 → 每次启动一条 `IRQ index 0 not found` 的 error 级日志，看起来像故障。

### §10.7 `class_create()` / `class_register()` 与 `bus_register()`

| 符号 | 什么时候用 |
| --- | --- |
| `class_create()`（宏，`include/linux/device/class.h:273`） | 只要 `/sys/class/` 下一个目录，无 match/probe |
| `class_register()`（宏，`include/linux/device/class.h:93`） | 同上，但要自定义 `.dev_release` / `.dev_groups` |
| `bus_register()`（`drivers/base/bus.c:785`） | 需要"驱动"这个概念，要 match 和 probe |

判据见 §4.5。⚠️ **错用会怎样**：为一组没有驱动的设备发明了 `bus_type` → 多写几百行永远不会被调用的 match/probe 代码。

### §10.8 `bus_for_each_dev()` 与 `bus_find_device_by_name()`

| 符号 | 位置 | 引用计数 |
| --- | --- | --- |
| `bus_for_each_dev()` | `drivers/base/bus.c:290` | 回调里拿到的 `dev` 由 klist 迭代器持有，回调返回后失效 |
| `bus_find_device()` | `drivers/base/bus.c:324` | ⚠️ 返回的设备**已经加过引用**，调用者必须 `put_device()` |
| `bus_find_device_by_name()` | `static inline`，`include/linux/device/bus.h:176` | 同上，包在 `bus_find_device()` 外面 |

⚠️ **错用会怎样**：`bus_find_device*()` 之后忘了 `put_device()` → 引用计数泄漏，设备永远无法被卸载。这一族包括 `class_find_device()`（`drivers/base/class.c:404`）和 `driver_find_device()`（`drivers/base/driver.c:151`），语义一致。

### §10.9 `device_add_group()` 与 `devm_device_add_group()` 与 `device_add_groups()`

| 符号 | 形态 | 位置 |
| --- | --- | --- |
| `device_add_group()` | `static inline`，单数，转发到复数版 | `include/linux/device.h:1039` |
| `device_add_groups()` | 导出函数，复数 | `drivers/base/core.c:2750` |
| `devm_device_add_group()` | 导出函数，单数，托管 | `drivers/base/core.c:2801` |

⚠️ 单数 / 复数、托管 / 非托管两个维度交叉，四个名字里只有三个存在。**错用会怎样**：用了非托管版又忘了在 `remove()` 里 `device_remove_group()` → 设备重新绑定时 sysfs 报 duplicate filename。

### §10.10 `platform_driver_register()` 与 `driver_register()`

| 符号 | 形态 |
| --- | --- |
| `platform_driver_register()` | 宏（`include/linux/platform_device.h:244`）→ `__platform_driver_register()`（`drivers/base/platform.c:861`） |
| `driver_register()` | 导出函数（`drivers/base/driver.c:222`），任何总线通用 |

⚠️ 平台驱动别直接调 `driver_register()`：`__platform_driver_register()` 会替你填 `drv->driver.bus = &platform_bus_type` 以及 `.probe`/`.remove`/`.shutdown` 三个转发桩。**错用会怎样**：`drv->bus` 是 NULL，`driver_register()` 第一行就解引用 `drv->bus->p`（`drivers/base/driver.c:227`）→ 空指针。

---

## §11 符号存在性核对表

本篇正文里出现的每一个内核符号，逐个 grep 确认在 6.1.158 上存在，并按"读者能不能直接用"分类。判据是 `EXPORT_SYMBOL*` 加头文件声明，不是有没有 kerneldoc。

| 符号 | 形态 | 导出 | 读者可用 |
| --- | --- | --- | --- |
| `platform_get_resource` | 函数 `drivers/base/platform.c:55` | `:68` | 是 |
| `devm_platform_ioremap_resource` | 函数 `drivers/base/platform.c:122` | `:127` | 是 |
| `platform_get_irq` | 函数 `drivers/base/platform.c:259` | `:270` | 是 |
| `platform_get_irq_optional` | 函数 `drivers/base/platform.c:171` | `:240` | 是 |
| `platform_get_resource_byname` | 函数 `drivers/base/platform.c:419` | 有 | 是 |
| `platform_get_irq_byname` | 函数 `drivers/base/platform.c:469` | 有 | 是 |
| `platform_device_alloc` | 函数 `drivers/base/platform.c:576` | `:592` | 是 |
| `platform_device_add_resources` | 函数 `drivers/base/platform.c:604` | `:620` | 是 |
| `platform_device_add_data` | 函数 `drivers/base/platform.c:632` | `:647` | 是 |
| `platform_device_add` | 函数 `drivers/base/platform.c:656` | `:736` | 是 |
| `platform_device_register` | 函数 `drivers/base/platform.c:775` | `:781` | 是 |
| `platform_device_register_full` | 函数 `drivers/base/platform.c:806` | `:854` | 是 |
| `platform_device_unregister` | 函数 `drivers/base/platform.c:791` | `:796` | 是 |
| `platform_device_put` | 函数 `drivers/base/platform.c:548` | `:553` | 是 |
| `platform_driver_register` | 宏 `include/linux/platform_device.h:244` | 转发 | 是 |
| `__platform_driver_register` | 函数 `drivers/base/platform.c:861` | `:869` | 通过宏 |
| `platform_driver_unregister` | 函数 `drivers/base/platform.c:875` | 有 | 是 |
| `module_platform_driver` | 宏 `include/linux/platform_device.h:274` | 不适用 | 是 |
| `platform_set_drvdata` / `platform_get_drvdata` | inline `include/linux/platform_device.h:263` / `:258` | 不适用 | 是 |
| `dev_set_drvdata` / `dev_get_drvdata` | inline `include/linux/device.h:766` / `:761` | 不适用 | 是 |
| `dev_get_platdata` | inline `include/linux/device.h:1002` | 不适用 | 是 |
| `devm_kzalloc` | inline `include/linux/device.h:208` → `devm_kmalloc`（`drivers/base/devres.c:825`） | 有 | 是 |
| `devm_krealloc` | 函数 `drivers/base/devres.c:863` | 有 | 是 |
| `devm_kfree` | 函数 `drivers/base/devres.c:1058` | 有 | 是 |
| `devres_alloc` | 宏 `include/linux/device.h:172` → `__devres_alloc_node`（`drivers/base/devres.c:159`） | 有 | 是 |
| `devres_free` | 函数 `drivers/base/devres.c:220` | 有 | 是 |
| `devres_add` | 函数 `drivers/base/devres.c:240` | 有 | 是 |
| `devres_find` / `devres_destroy` / `devres_release` | 函数 `drivers/base/devres.c:283` / `:387` / `:417` | 有 | 是 |
| `devres_open_group` | 函数 `drivers/base/devres.c:549` | `:574` | 是 |
| `devres_close_group` | 函数 `drivers/base/devres.c:607` | `:622` | 是 |
| `devres_remove_group` | 函数 `drivers/base/devres.c:633` | 有 | 是 |
| `devres_release_group` | 函数 `drivers/base/devres.c:666` | `:701` | 是 |
| `devm_add_action` | 函数 `drivers/base/devres.c:738` | `:753` | 是 |
| `devm_add_action_or_reset` | inline `include/linux/device.h:250` | 不适用 | 是 |
| `devm_remove_action` / `devm_release_action` | 函数 `drivers/base/devres.c:764` / `:786` | 有 | 是 |
| `bus_register` / `bus_unregister` | 函数 `drivers/base/bus.c:785` / `:872` | `:863` / `:884` | 是 |
| `bus_for_each_dev` | 函数 `drivers/base/bus.c:290` | `:307` | 是 |
| `bus_find_device` | 函数 `drivers/base/bus.c:324` | 有 | 是 |
| `bus_find_device_by_name` | inline `include/linux/device/bus.h:176` | 不适用 | 是 |
| `bus_rescan_devices` | 函数 `drivers/base/bus.c:709` | 有 | 是 |
| `subsys_virtual_register` | 函数 `drivers/base/bus.c:1160` | 有 | 是（全树仅 4 个调用点） |
| `driver_register` / `driver_unregister` | 函数 `drivers/base/driver.c:222` / `:267` | `:259` / `:276` | 是 |
| `driver_find` | 函数 `drivers/base/driver.c:290` | 有 | 是 |
| `driver_for_each_device` / `driver_find_device` | 函数 `drivers/base/driver.c:117` / `:151` | 有 | 是 |
| `device_initialize` | 函数 `drivers/base/core.c:3156` | `:3180` | 是 |
| `device_add` | 函数 `drivers/base/core.c:3578` | `:3756` | 是 |
| `device_register` | 函数 `drivers/base/core.c:3776` | `:3781` | 是 |
| `device_del` / `device_unregister` | 函数 `drivers/base/core.c:3840` / `:3925` | `:3912` / `:3931` | 是 |
| `get_device` / `put_device` | 函数 `drivers/base/core.c:3791` / `:3801` | `:3795` / `:3807` | 是 |
| `dev_set_name` | 函数 `drivers/base/core.c:3479` | `:3489` | 是 |
| `device_create` / `device_destroy` | 函数 `drivers/base/core.c:4426` / `:4492` | `:4438` / `:4502` | 是 |
| `class_register` | 宏 `include/linux/device/class.h:93` → `__class_register`（`drivers/base/class.c:153`） | 有 | 是 |
| `class_unregister` | 函数 `drivers/base/class.c:204` | `:588` | 是 |
| `class_create` | 宏 `include/linux/device/class.h:273` → `__class_create`（`drivers/base/class.c:231`） | `:257` | 是 |
| `class_destroy` | 函数 `drivers/base/class.c:266` | `:589` | 是 |
| `class_find_device` / `class_for_each_device` | 函数 `drivers/base/class.c:404` / `:357` | 有 | 是 |
| `device_add_groups` | 函数 `drivers/base/core.c:2750` | 有 | 是 |
| `device_add_group` | inline `include/linux/device.h:1039` | 不适用 | 是 |
| `devm_device_add_group` | 函数 `drivers/base/core.c:2801` | `:2821` | 是 |
| `device_link_add` | 函数 `drivers/base/core.c:785` | `:983` | 是 |
| `device_link_del` / `device_link_remove` | 函数 `drivers/base/core.c:1017` / `:1033` | `:1023` / `:1051` | 是 |
| `dev_err_probe` | 函数 `drivers/base/core.c:5038` | `:5058` | 是 |
| `device_set_deferred_probe_reason` | 函数 `drivers/base/dd.c:227` | 无 | ⚠️ 不导出，只由 `dev_err_probe()` 内部调 |
| `driver_deferred_probe_check_state` | 函数 `drivers/base/dd.c:290` | `:304` | 是，但已被 fw_devlink 取代 |
| `DEVICE_ATTR` / `_RW` / `_RO` / `_ADMIN_RO` | 宏 `include/linux/device.h:127` / `:132` / `:136` / `:138` | 不适用 | 是 |
| `ATTRIBUTE_GROUPS` | 宏 `include/linux/sysfs.h:159` | 不适用 | 是 |
| `DL_FLAG_*`（十个） | 宏 `include/linux/device.h:322-331` | 不适用 | ⚠️ 其中三个仅核心内部用，见 §7.3 |
| `DEFINE_RES_MEM` / `DEFINE_RES_IRQ` | 宏 `include/linux/ioport.h:173` / `:183` | 不适用 | 是（不在 base 内） |
| `PLATFORM_DEVID_AUTO` / `PLATFORM_DEVID_NONE` | 宏 `include/linux/platform_device.h:16` / `:15` | 不适用 | 是 |
| `devm_request_threaded_irq` | `include/linux/interrupt.h:205` | 有 | 是（不在 base 内） |
| `of_device_get_match_data` | `include/linux/of_device.h:33` | 有 | 是（不在 base 内） |
| `device_get_match_data` | `include/linux/property.h:403` | 有 | 是（不在 base 内） |
| `component_add` / `component_del` | 函数 `drivers/base/component.c:787` / `:802` | 有 | 是 |
| `component_master_add_with_match` | 函数 `drivers/base/component.c:512` | 有 | 是 |
| `auxiliary_device_init` | 函数 `drivers/base/auxiliary.c:274` | 有 | 是 |
| `icc_sync_state` | 函数 `drivers/interconnect/core.c:1125` | 有 | 是（不在 base 内） |

⚠️ 表里唯一一个"存在但读者不能直接用"的是 `device_set_deferred_probe_reason()`（`drivers/base/dd.c:227`）：它写满了 kerneldoc（`drivers/base/dd.c:222-226`）却没有 `EXPORT_SYMBOL`，也没有在 `include/linux/` 下声明。要往 `/sys/kernel/debug/devices_deferred` 里写东西只能间接走 `dev_err_probe()`。

---

## §12 参考实现与统计

### §12.1 每个角色的 in-tree 参考实现

| 角色 | 参考实现 | 为什么选它 |
| --- | --- | --- |
| A 平台驱动 | `drivers/soc/qcom/icc-bwmon.c`（702 行） | arm64、纯 DT、`devm_*` 与 `dev_err_probe()` 用法齐全 |
| B 设备提供方 | `drivers/base/platform.c:806`（`platform_device_register_full()` 自身） | 三段式的标准组合就写在函数体里（`:827` / `:832`） |
| C 分组回滚 | `drivers/firmware/arm_scmi/driver.c:1550` | arm64、`devres_open_group(dev, NULL, ...)` 的典型形状 |
| D 新总线 | `drivers/firmware/arm_ffa/bus.c`（233 行） | arm64、四件要件齐全、无历史包袱 |
| E class | `drivers/base/devcoredump.c:244-250` | 本目录自带，`.dev_groups` 与 `.class_groups` 都填了 |
| F sysfs 属性 | `drivers/firmware/arm_ffa/bus.c:70-93` | 两个只读属性加 `ATTRIBUTE_GROUPS()` 的最小完整例 |
| G device_link | `drivers/firmware/arm_scmi/bus.c:222-229` | 五行，带 `WARN_ON(!link)` |
| G sync_state | `drivers/memory/tegra/mc.c:947` | 全树少数没有直接复用 `icc_sync_state` 的例子 |
| H 延迟探测 | `drivers/soc/qcom/icc-bwmon.c:562` / `:567` / `:601` / `:606` | 同一个 `probe()` 里四处 `dev_err_probe()` |
| I component | 见 `04-extensions.md` §1.4 | — |
| I auxiliary | 见 `04-extensions.md` §2.3 | — |

### §12.2 各角色的全树规模

| 角色 | 口径 | 数字 | 重跑命令 |
| --- | --- | --- | --- |
| A | 用 `module_platform_driver` 的文件数 | 2573 | `grep -rlw 'module_platform_driver' --include=*.c drivers/ \| wc -l` |
| B | 用 `platform_device_register_full` 的文件数 | 50 | `grep -rlw 'platform_device_register_full' --include=*.c . \| wc -l` |
| C | 用 `devm_kzalloc` 的文件数 | 4217 | `grep -rlw 'devm_kzalloc' --include=*.c drivers/ \| wc -l` |
| C | 用 `devm_add_action_or_reset` 的文件数 | 450 | `grep -rlw 'devm_add_action_or_reset' --include=*.c drivers/ \| wc -l` |
| C | `devres_open_group` 调用点 | 38 | `grep -rnw 'devres_open_group' --include=*.c . \| grep -v EXPORT \| wc -l` |
| D | 全树 `struct bus_type` 实例数 | 146 | `grep -rn 'struct bus_type [a-z_]* = {' --include=*.c . \| wc -l` |
| D | 调 `bus_register` 的文件数（base 之外） | 134 | `grep -rlw 'bus_register' --include=*.c . \| grep -v '^./drivers/base/' \| wc -l` |
| D | `subsys_virtual_register` 调用点 | 4 | `grep -rnw 'subsys_virtual_register' --include=*.c . \| grep -v EXPORT \| wc -l` |
| G | 调 `device_link_add` 的文件数 | 64 | `grep -rlw 'device_link_add' --include=*.c drivers/ \| wc -l` |
| G | `.sync_state` 赋值点 | 36 | `grep -rn '\.sync_state[[:space:]]*=' --include=*.c --include=*.h drivers/ include/ \| wc -l` |
| H | 用 `dev_err_probe` 的文件数 | 716 | `grep -rlw 'dev_err_probe' --include=*.c drivers/ \| wc -l` |

⚠️ A 与 D 的数量级差了近 20 倍（2573 对 146），这解释了为什么本篇 §1 写得最细而 §4 只给一个参考实现：绝大多数读者是角色 A。

⚠️ C 那两行的 4217 与 450 不能相减来推"多少驱动用了自定义 release"——`devm_kzalloc` 的使用者里大部分从不写自定义 release，两个口径不是父子集关系。

### §12.3 陈旧交叉引用回访

本篇引用了其他篇的以下位置，均已确认目标章节存在且讲的就是那件事：

| 引用 | 目标讲什么 |
| --- | --- |
| `01-api.md` §6 / §6.1 | device_link 的 API 与十个 `DL_FLAG_*` 逐个语义 |
| `01-api.md` §7.3 | 现成的托管接口清单 |
| `01-api.md` §13.2 | 返回 NULL 与返回 ERR_PTR 的两派 |
| `02-datastructures.md` §3.3 | `sync_state()` 只调一次的语义 |
| `02-datastructures.md` §7.2 | `struct platform_driver`（`.remove` 返回 `int` 的那一处不一致） |
| `02-datastructures.md` §8 | devres 的三个内部结构 |
| `03-internals.md` §A9 | 延迟探测循环 |
| `03-internals.md` §B2 | devres 的分组与释放 |
| `03-internals.md` §B3 | device_link 状态机 |
| `03-internals.md` §B4 | fw_devlink：从 fwnode_link 到 device_link |
| `04-extensions.md` §1.3 / §1.4 | component 的收敛机制与两侧 API |
| `04-extensions.md` §2.2 / §2.3 | auxiliary 的匹配规则与两段式注册 |
