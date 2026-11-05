---
title: 使用指南
---

# remoteproc 05：各角色的代码骨架

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ **篇首免责声明**：本篇所有代码骨架**都没有编译过、没有运行过**。
> 它们是从本树真实驱动里提炼的结构，用来说明"哪几个回调必须填、按什么顺序调 API"，
> **不是可以直接 `make` 的代码**。抄之前请对照 §0.2 点名的那几个真实驱动。
>
> ⚠️ 本篇出现的每个内核符号都已在 6.1.158 树上 grep 确认存在，
> 但**参数个数和类型请以头文件为准**（`include/linux/remoteproc.h`、`include/linux/rpmsg.h`）。

---

## §0 本子系统有哪些角色

### §0.1 角色清单

remoteproc 和 i2c/spi 那种"控制器 + 设备"的两角色模型不同，它有 **5 类使用者**：

| § | 角色 | 你要写的东西 | 主要头文件 |
|---|---|---|---|
| §1 | **平台驱动作者（最小实现）** | 一个 platform_driver + `rproc_ops` 的 3 个回调 | `include/linux/remoteproc.h` |
| §2 | **平台驱动作者（进阶）** | 再加 `attach`/`detach`/`kick`/`parse_fw` | 同上 + `include/linux/mailbox_client.h` |
| §3 | **平台驱动作者（内存布局）** | carveout 登记、资源表定制、厂商资源 | 同上 |
| §4 | **rpmsg 内核客户端作者** | 一个 `rpmsg_driver` | `include/linux/rpmsg.h` |
| §5 | **用户态运维 / 调试者** | shell 命令 + 一个 ioctl 小程序 | `include/uapi/linux/rpmsg.h` |

注意 **没有"remoteproc 设备驱动"这个角色**。
远端处理器不是一条总线上的从设备 —— 它是一个"被启动的东西"。
和它通信的驱动写成 **rpmsg 驱动**（§4），挂在 rpmsg 总线上，不挂在 remoteproc 上。
这一点是本子系统最容易搞错的地方，08 篇 §1 会用类比再讲一遍。

### §0.2 每个骨架的真实原型（抄之前先读这几个文件）

| 骨架 | 参考的真实驱动 | 行数 | 为什么选它 |
|---|---|---|---|
| §1 最小实现 | `drivers/remoteproc/meson_mx_ao_arc.c` | 261 | 本树里**最短的完整 remoteproc 驱动**，只填 3 个回调 |
| §2 进阶 | `drivers/remoteproc/stm32_rproc.c` | 932 | 回调填得最全（含 `attach`/`detach`/mailbox `kick`） |
| §3 内存布局 | `drivers/remoteproc/stm32_rproc.c` §parse_fw · `drivers/remoteproc/imx_rproc.c` | 932 / 894 | 两种 carveout 登记法各一例 |
| §4 rpmsg 客户端 | `samples/rpmsg/rpmsg_client_sample.c` | —— | 内核自带的官方范例，**可以直接编译成模块** |
| §5 用户态 | `drivers/remoteproc/remoteproc_cdev.c` · `drivers/rpmsg/rpmsg_ctrl.c` | 126 / —— | 两个 uapi 的实现 |

### §0.3 需要打开的 Kconfig

| 选项 | 位置 | 谁需要 |
|---|---|---|
| `REMOTEPROC` | `drivers/remoteproc/Kconfig:4` | 全部角色 |
| `REMOTEPROC_CDEV` | `drivers/remoteproc/Kconfig:17` | §5 想用 `/dev/remoteprocN` |
| `RPMSG_VIRTIO` | `drivers/rpmsg/Kconfig:77` | §4，以及任何要用 rpmsg 的场景 |
| `RPMSG_CHAR` | `drivers/rpmsg/Kconfig:9` | §5 想在用户态收发 rpmsg |
| `RPMSG_CTRL` | `drivers/rpmsg/Kconfig:18` | 同上（`RPMSG_CHAR` 的前置） |
| `RPMSG_NS` | `drivers/rpmsg/Kconfig:26` | 想让远端**动态**声明 channel（name service） |

⚠️`RPMSG_VIRTIO` 是 remoteproc 与 rpmsg 之间唯一的胶水。
不开它，04 §1 那条"三级跳"会走到第二跳就停下 ——
virtio 设备注册成功了，但没有 rpmsg 总线来认领它，
表现是 `/sys/bus/rpmsg/devices/` 空的、dmesg 里也不报错。**这是一个静默失效**。

---

## §1 最小平台驱动：只填三个回调

### §1.1 必须填哪几个

`struct rproc_ops`（`include/linux/remoteproc.h:385-406`）一共 17 个成员，
但**只有三个在最小场景下非填不可**：

| 回调 | 声明位置 | 为什么必需 |
|---|---|---|
| `start` | `include/linux/remoteproc.h:388` | 把远端从复位里放出来。没有它 `rproc_boot()` 一定失败 |
| `stop` | `include/linux/remoteproc.h:389` | 把远端按回复位 |
| `da_to_va` | `include/linux/remoteproc.h:393` | 只有在核心的 carveout 链表里查不到时才需要（03 §B4.1）。**如果你的内存全部通过 carveout 登记，可以不填** |

其余 14 个都可选。特别是：

| 回调 | 不填的后果 |
|---|---|
| `load` / `sanity_check` / `get_boot_addr` / `find_loaded_rsc_table` / `parse_fw` | `rproc_alloc()` 会自动填上 ELF 默认实现（04 §2.2）—— **判据只看 `ops->load` 一个，全有或全无** |
| `kick` | ⚠️ 能 `rproc_boot()` 成功，但一旦固件带 `RSC_VDEV`，virtio 设备注册会失败（04 §1.5） |
| `prepare` / `unprepare` | 不做启动前的时钟/电源准备（你可以放到 `start` 里，但 attach 场景就没机会跑了，见 §2.3） |
| `panic` | 系统 panic 时不给远端刷 cache 的时间（04 §8） |
| `coredump` | 用默认的，而默认在多数情况下什么都不做（04 §3.1） |

### §1.2 骨架

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/clk.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/remoteproc.h>
#include <linux/reset.h>

struct my_rproc {
	struct device		*dev;
	struct rproc		*rproc;
	struct clk		*clk;
	struct reset_control	*rst;
	void __iomem		*sram;		/* 远端的指令 RAM */
	phys_addr_t		sram_pa;
	size_t			sram_size;
};

/* ---- 回调 1：放出复位 ---- */
static int my_rproc_start(struct rproc *rproc)
{
	struct my_rproc *priv = rproc->priv;	/* 就是 rproc_alloc 里那块尾部空间 */
	int ret;

	ret = clk_prepare_enable(priv->clk);
	if (ret)
		return ret;

	/* 有的硬件还需要写一个"启动地址"寄存器：rproc->bootaddr */
	ret = reset_control_deassert(priv->rst);
	if (ret) {
		clk_disable_unprepare(priv->clk);
		return ret;
	}

	return 0;
}

/* ---- 回调 2：按回复位 ---- */
static int my_rproc_stop(struct rproc *rproc)
{
	struct my_rproc *priv = rproc->priv;

	reset_control_assert(priv->rst);
	clk_disable_unprepare(priv->clk);

	return 0;
}

/* ---- 回调 3：设备地址 → 内核虚拟地址 ---- */
static void *my_rproc_da_to_va(struct rproc *rproc, u64 da, size_t len,
			       bool *is_iomem)
{
	struct my_rproc *priv = rproc->priv;

	if (da + len > priv->sram_size)		/* 上界必须查 */
		return NULL;

	if (is_iomem)				/* 必须判空，核心允许传 NULL */
		*is_iomem = true;

	return (void *)(priv->sram + da);
}

static struct rproc_ops my_rproc_ops = {
	.start		= my_rproc_start,
	.stop		= my_rproc_stop,
	.da_to_va	= my_rproc_da_to_va,
	/* 不填 .load —— rproc_alloc() 会把五个 ELF 回调一起补上 */
};

static int my_rproc_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct my_rproc *priv;
	struct rproc *rproc;
	const char *fw_name = "my-firmware.elf";
	struct resource *res;
	int ret;

	/* 优先支持 DT 里的 firmware-name 覆盖默认值 */
	ret = rproc_of_parse_firmware(dev, 0, &fw_name);
	if (ret && ret != -EINVAL)
		return ret;

	/* devm 版：driver detach 时自动 rproc_free() */
	rproc = devm_rproc_alloc(dev, dev_name(dev), &my_rproc_ops,
				 fw_name, sizeof(*priv));
	if (!rproc)
		return -ENOMEM;

	priv = rproc->priv;
	priv->dev   = dev;
	priv->rproc = rproc;

	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "sram");
	priv->sram = devm_ioremap_resource(dev, res);
	if (IS_ERR(priv->sram))
		return PTR_ERR(priv->sram);
	priv->sram_pa   = res->start;
	priv->sram_size = resource_size(res);

	priv->clk = devm_clk_get(dev, NULL);
	if (IS_ERR(priv->clk))
		return PTR_ERR(priv->clk);

	priv->rst = devm_reset_control_get_exclusive(dev, NULL);
	if (IS_ERR(priv->rst))
		return PTR_ERR(priv->rst);

	platform_set_drvdata(pdev, rproc);

	/* devm 版：driver detach 时自动 rproc_del() */
	return devm_rproc_add(dev, rproc);
}

static const struct of_device_id my_rproc_match[] = {
	{ .compatible = "myvendor,my-rproc" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, my_rproc_match);

static struct platform_driver my_rproc_driver = {
	.probe	= my_rproc_probe,
	.driver	= {
		.name		= "my-rproc",
		.of_match_table	= my_rproc_match,
	},
	/* 用了 devm_rproc_alloc + devm_rproc_add 就不需要 .remove */
};
module_platform_driver(my_rproc_driver);

MODULE_LICENSE("GPL v2");
```

### §1.3 骨架里五个必须理解的点

| 点 | 说明 | 出处 |
|---|---|---|
| `rproc->priv` 就是 `rproc_alloc` 的第 5 个参数 | `rproc_alloc(dev, name, ops, firmware, len)` 里 `len` 是**私有数据字节数**，核心把它分配在 `struct rproc` 尾部 | `drivers/remoteproc/remoteproc_core.c:2443-2445` |
| `!dev \|\| !name \|\| !ops` 三者缺一返回 NULL | 所以 `ops` 不能传 NULL | `drivers/remoteproc/remoteproc_core.c:2449` |
| `devm_rproc_alloc()` 用 `devres_alloc` + `devres_add` | 释放动作是 `devm_rproc_free()`（`drivers/remoteproc/remoteproc_core.c:2585`） | `drivers/remoteproc/remoteproc_core.c:2603-2623` |
| `devm_rproc_add()` 用 `devm_add_action_or_reset` | 释放动作是 `devm_rproc_remove()`（`drivers/remoteproc/remoteproc_core.c:2317`） | `drivers/remoteproc/remoteproc_core.c:2332-2342` |
| `rproc_of_parse_firmware()` 读 DT 的 `firmware-name` | 属性缺失时返回 `-EINVAL`，所以上面那句 `ret != -EINVAL` 是必须的 | `drivers/remoteproc/remoteproc_core.c:996-1004` |

⚠️ **两个 devm 包装的粒度不同，别混用**：
`devm_rproc_alloc()` 挂的是 `devres`（会在 `devres_release_all()` 时按逆序释放），
`devm_rproc_add()` 挂的是 `devm_add_action_or_reset()`（它在**注册失败时立刻执行一次**释放动作）。
两个都用 devm 版是最省事的，但**不能"alloc 用 devm、add 用裸 `rproc_add()`"** ——
那样 detach 时 `rproc_free()` 会在还没 `rproc_del()` 的情况下被调用。

反过来（alloc 用裸版、add 用 devm 版）也不行，理由同上但方向相反：
`rproc_del()` 已经被自动执行，你的 `.remove` 里再手工调一次就是二次删除。
本树里 `drivers/remoteproc/meson_mx_ao_arc.c:154` 和 `:220` 就是
**`devm_rproc_alloc()` + 裸 `rproc_add()` + 显式 `.remove`** 这个组合
（`drivers/remoteproc/meson_mx_ao_arc.c:231`）—— 也是自洽的，只是要自己写 remove。

---

## §2 进阶：mailbox 门铃 + attach/detach

### §2.1 要多填哪几个

参考 `drivers/remoteproc/stm32_rproc.c:647-660` 的 `st_rproc_ops`，
它把 12 个成员填满了，其中 **4 个用了核心提供的默认实现**：

| 成员 | stm32 填的是 | 位置 |
|---|---|---|
| `.prepare` | 自己的 | `drivers/remoteproc/stm32_rproc.c:648` |
| `.start` / `.stop` | 自己的 | `drivers/remoteproc/stm32_rproc.c:649-650` |
| `.attach` / `.detach` | 自己的 | `drivers/remoteproc/stm32_rproc.c:651-652` |
| `.kick` | 自己的（mailbox） | `drivers/remoteproc/stm32_rproc.c:653` |
| `.load` | `rproc_elf_load_segments` | `drivers/remoteproc/stm32_rproc.c:654` |
| `.parse_fw` | 自己的（登记 carveout） | `drivers/remoteproc/stm32_rproc.c:655` |
| `.find_loaded_rsc_table` | `rproc_elf_find_loaded_rsc_table` | `drivers/remoteproc/stm32_rproc.c:656` |
| `.get_loaded_rsc_table` | 自己的（attach 场景用） | `drivers/remoteproc/stm32_rproc.c:657` |
| `.sanity_check` | `rproc_elf_sanity_check` | `drivers/remoteproc/stm32_rproc.c:658` |
| `.get_boot_addr` | `rproc_elf_get_boot_addr` | `drivers/remoteproc/stm32_rproc.c:659` |

**这是"部分复用 ELF 默认"的正确写法**：把四个 ELF 回调显式写进 ops 表。
因为 `rproc_alloc()` 的自动补全是**全有或全无**（04 §2.2）——
一旦你填了 `.load`，剩下四个就不会被自动补，必须手写。
⇒ 想只替换 `.load` 而保留其他四个默认？**做不到**，只能像 stm32 这样四个都写上。

### §2.2 mailbox 门铃：两个方向

```c
#include <linux/mailbox_client.h>

struct my_rproc {
	/* … */
	struct mbox_chan	*tx_chan;	/* 内核 → 远端 */
	struct mbox_chan	*rx_chan;	/* 远端 → 内核 */
	struct mbox_client	tx_cl;
	struct mbox_client	rx_cl;
	int			rx_vq_id;	/* 这个通道对应哪个 vring */
};

/* ---- 方向 1：远端 → 内核。mailbox 回调里唤醒 virtqueue ---- */
static void my_rproc_rx_callback(struct mbox_client *cl, void *data)
{
	struct device *dev = cl->dev;
	struct rproc *rproc = dev_get_drvdata(dev);
	struct my_rproc *priv = rproc->priv;

	/* 这一句是 remoteproc 与 virtio 的汇合点 */
	if (rproc_vq_interrupt(rproc, priv->rx_vq_id) == IRQ_NONE)
		dev_dbg(dev, "no message found in vq%d\n", priv->rx_vq_id);
}

/* ---- 方向 2：内核 → 远端。virtio 层要发通知时核心调这里 ---- */
static void my_rproc_kick(struct rproc *rproc, int vqid)
{
	struct my_rproc *priv = rproc->priv;
	int err;

	if (!priv->tx_chan)
		return;

	/* 消息内容由具体 mailbox 控制器决定，很多平台只用它做"敲一下" */
	err = mbox_send_message(priv->tx_chan, "kick");
	if (err < 0)
		dev_err(&rproc->dev, "kick failed (%d)\n", err);
}
```

| 关键调用 | stm32 里的位置 |
|---|---|
| `rproc_vq_interrupt()` 在 mailbox rx 回调里 | `drivers/remoteproc/stm32_rproc.c:299` |
| `mbox_request_channel_byname()` 按名字取通道 | `drivers/remoteproc/stm32_rproc.c:382` |
| `.kick` 里 `mbox_send_message()` | `drivers/remoteproc/stm32_rproc.c:567` |
| stop 时也发 mailbox（让远端优雅退出） | `drivers/remoteproc/stm32_rproc.c:494` |
| detach 时也发 mailbox | `drivers/remoteproc/stm32_rproc.c:512` |

`rproc_vq_interrupt()`（`drivers/remoteproc/remoteproc_virtio.c:89`）的返回值
是 **`irqreturn_t`**，`IRQ_NONE` 表示"那个 notifyid 上没有 virtqueue"
（`drivers/remoteproc/remoteproc_virtio.c:95-97` 的 `idr_find` + 双重判空）。
⇒ 调 rpmsg 不通时，**如果这里一直返回 `IRQ_NONE`，说明 `vqid` 传错了**，
而不是远端没发消息。正确的 `vqid` 就是资源表里回写的 `notifyid`
（04 §1.3），可以从 debugfs 的 `resource_table` 读出来。

⚠️ 上面骨架里 `dev_get_drvdata(dev)` 能拿到 `rproc` 的前提是
probe 里做过 `platform_set_drvdata(pdev, rproc)`。
`mbox_client.dev` 指向的是**你的 platform 设备**，不是 `&rproc->dev`。

### §2.3 attach/detach：接管一个已经在跑的远端

这是 remoteproc 一个容易被忽略的能力：**bootloader 已经把远端启动了**，
Linux 起来之后只是"接管"它，不重新加载固件。

```c
/* ---- attach：不加载固件，只建立通信 ---- */
static int my_rproc_attach(struct rproc *rproc)
{
	struct my_rproc *priv = rproc->priv;

	/* 不要 deassert reset —— 远端已经在跑了 */
	return my_rproc_request_mbox(priv);
}

/* ---- detach：断开通信，但让远端继续跑 ---- */
static int my_rproc_detach(struct rproc *rproc)
{
	struct my_rproc *priv = rproc->priv;

	/* 不要 assert reset */
	my_rproc_free_mbox(priv);
	return 0;
}

/* ---- 资源表不是从固件里解析的，是"远端内存里已经有一份" ---- */
static struct resource_table *
my_rproc_get_loaded_rsc_table(struct rproc *rproc, size_t *table_sz)
{
	struct my_rproc *priv = rproc->priv;

	*table_sz = priv->rsc_size;	/* 通常从 DT 或固定偏移得到 */
	return (struct resource_table *)priv->rsc_va;
}
```

probe 里还要告诉核心"这个远端已经在跑了"：

```c
	if (bootloader_already_started(priv))
		rproc->state = RPROC_DETACHED;	/* 在 rproc_add() 之前设 */
```

| 事实 | 出处 |
|---|---|
| `rproc_add()` 看到 `state == RPROC_DETACHED` 会走 attach 分支 | 03 §A5 |
| `.get_loaded_rsc_table` 声明 | `include/linux/remoteproc.h:399-400` |
| 是否走过 attach，可以用 `cached_table` 是否为 NULL 判断 | 03 §B3.3 |
| stm32 的实现 | `drivers/remoteproc/stm32_rproc.c:651`、`:652`、`:657` |

⚠️ **attach 场景下 `.prepare` 不会被调用**。
如果你把时钟使能放在 `.prepare` 里，接管一个 bootloader 启动的远端时时钟引用计数就是 0 ——
后面任何人调一次 `clk_disable_unprepare()` 都可能把远端的时钟关掉。
⇒ attach 路径需要的准备工作要放在 `.attach` 里自己做一遍。


---

## §3 内存布局：carveout 与资源表定制

### §3.1 三个 API 的分工

| API | 声明 | 定义 | 用在什么场景 |
|---|---|---|---|
| `rproc_mem_entry_init()` | `include/linux/remoteproc.h:676-681` | `drivers/remoteproc/remoteproc_core.c:913` | 通用：自己提供 `alloc`/`release` 回调 |
| `rproc_of_resm_mem_entry_init()` | `include/linux/remoteproc.h:683-685` | `drivers/remoteproc/remoteproc_core.c:959` | 内存来自 DT 的 `memory-region`（reserved-memory） |
| `rproc_add_carveout()` | `include/linux/remoteproc.h:674` | `drivers/remoteproc/remoteproc_core.c:890` | 把上面两者的产物挂进链表（一行 `list_add_tail`） |

两个 init 函数都是 `__printf` 变参
（`drivers/remoteproc/remoteproc_core.c:912` 和 `drivers/remoteproc/remoteproc_core.c:958`），
**最后一个具名参数 `name` 是格式串**，后面可以跟参数。
核心自己就是这么用的：`"vdev%dvring%d"`（04 §1.3）。

⚠️ **名字必须 ≤ 31 字符**。`rproc_find_carveout_by_name()`
用的是 32 字节栈缓冲（03 §B4.2），而 `fw_rsc_carveout.name[32]` 也是 32 字节。
名字被截断的表现是"carveout 明明登记了却查不到"，且**没有任何日志**。

### §3.2 骨架：在 `.parse_fw` 里登记 carveout

```c
static int my_rproc_parse_fw(struct rproc *rproc, const struct firmware *fw)
{
	struct device *dev = rproc->dev.parent;
	struct device_node *np = dev->of_node;
	struct of_phandle_iterator it;
	struct rproc_mem_entry *mem;
	struct reserved_mem *rmem;
	int index = 0;
	u64 da;

	of_phandle_iterator_init(&it, np, "memory-region", NULL, 0);
	while (of_phandle_iterator_next(&it) == 0) {
		rmem = of_reserved_mem_lookup(it.node);
		if (!rmem) {
			of_node_put(it.node);
			return -EINVAL;
		}

		/* 物理地址 → 远端看到的设备地址，映射关系由你的硬件决定 */
		if (my_rproc_pa_to_da(rproc, rmem->base, &da) < 0) {
			of_node_put(it.node);
			return -EINVAL;
		}

		if (strcmp(it.node->name, "vdev0buffer")) {
			/* 普通 carveout：自己管映射 */
			mem = rproc_mem_entry_init(dev, NULL,
						   (dma_addr_t)rmem->base,
						   rmem->size, da,
						   my_rproc_mem_alloc,
						   my_rproc_mem_release,
						   it.node->name);
			if (mem)
				rproc_coredump_add_segment(rproc, da, rmem->size);
		} else {
			/* vdev buffer：交给 reserved-memory 框架 */
			mem = rproc_of_resm_mem_entry_init(dev, index,
							   rmem->size,
							   rmem->base,
							   it.node->name);
		}

		if (!mem) {
			of_node_put(it.node);
			return -ENOMEM;
		}

		rproc_add_carveout(rproc, mem);
		index++;
	}

	/* 别忘了这一句 —— 否则核心拿不到资源表。
	 * ⚠️ 它声明在 drivers/remoteproc/remoteproc_internal.h 里（不是公开头文件），
	 * 所以这个骨架只能放在 drivers/remoteproc/ 目录下编译。
	 */
	return rproc_elf_load_rsc_table(rproc, fw);
}
```

| 对应的真实代码 | 位置 |
|---|---|
| `of_phandle_iterator_init(..., "memory-region", ...)` | `drivers/remoteproc/stm32_rproc.c:222` |
| `of_reserved_mem_lookup()` | `drivers/remoteproc/stm32_rproc.c:224` |
| 普通 carveout 分支 | `drivers/remoteproc/stm32_rproc.c:241-246` |
| 顺手登记 coredump 段 | `drivers/remoteproc/stm32_rproc.c:249` |
| vdev buffer 分支 | `drivers/remoteproc/stm32_rproc.c:253-256` |
| `rproc_add_carveout()` | `drivers/remoteproc/stm32_rproc.c:264` |

**`vdev0buffer` 这个名字不是随便起的**。
04 §1.7 说过，`rproc_add_virtio_dev()` 会去找名为 `"vdev%dbuffer"` 的 carveout；
找到了、并且 `of_resm_idx != -1`（即用 `rproc_of_resm_mem_entry_init()` 建的），
才会走 `of_reserved_mem_device_init_by_idx()` 那条正路
（`drivers/remoteproc/remoteproc_virtio.c:389-398`）。
⇒ **名字写错一个字母，virtio 的 buffer 就会静默地从父节点的 `memory-region` 索引 0 继承**
（`drivers/remoteproc/remoteproc_virtio.c:429`，返回值都不检查）。这是本子系统最阴的一个坑，06 篇会给决策树。

### §3.3 两种 mem_entry 的差别

| | `rproc_mem_entry_init` | `rproc_of_resm_mem_entry_init` |
|---|---|---|
| `of_resm_idx` | 保持 0？**不是** —— 由 `kzalloc` 得 0，但核心用 `-1` 表示"不是 resm" | 显式设成你传的 `of_resm_idx`（`drivers/remoteproc/remoteproc_core.c:973`） |
| `alloc` / `release` 回调 | 你提供 | **NULL** —— 内存由 reserved-memory 框架管 |
| `rsc_offset` | 由核心在处理资源表时回填 | 初始化为 `FW_RSC_ADDR_ANY`（`drivers/remoteproc/remoteproc_core.c:972`） |

⚠️ 上面那句"`kzalloc` 得 0"要小心：
`rproc_mem_entry_init()` 里 `mem = kzalloc(...)`（`drivers/remoteproc/remoteproc_core.c:923-925`）
之后**并没有把 `of_resm_idx` 设成 -1**。
真正的 -1 是在 `rproc_handle_carveout()` 建自动 carveout 时设的。
⇒ 用 `rproc_mem_entry_init()` 手工建的 entry，`of_resm_idx` 是 **0**，
恰好和"reserved-memory 索引 0"撞在一起。
所以 §3.2 骨架里 **`vdev0buffer` 必须用 `rproc_of_resm_mem_entry_init()`**，
不能用通用版凑 —— 这不是风格问题，是正确性问题。

### §3.4 厂商资源：`.handle_rsc`

固件想传一个 remoteproc 核心不认识的资源类型（`RSC_VENDOR_START` 以上）时：

```c
static int my_rproc_handle_rsc(struct rproc *rproc, u32 rsc_type, void *rsc,
			       int offset, int avail)
{
	switch (rsc_type) {
	case MY_RSC_FOO:
		return my_handle_foo(rproc, rsc, offset, avail);	/* 返回 RSC_HANDLED */
	default:
		return RSC_IGNORED;
	}
}
```

| 事实 | 出处 |
|---|---|
| 返回值语义：`RSC_HANDLED` / `RSC_IGNORED` / 负值 | `include/linux/remoteproc.h:371-373` 的注释 |
| ⚠️ **`RSC_IGNORED` 是正数 1** | 03 §C2 |

⚠️ **这是本子系统一个真实的设计债**：如果厂商资源是资源表里的**最后一条**，
`rproc_handle_resources()` 会把 `RSC_IGNORED`（= 1）当成返回值往上传，
调用者（`drivers/remoteproc/remoteproc_core.c:1419` 和
`drivers/remoteproc/remoteproc_core.c:1633`）用 `if (ret)` 判断，就会**误认为出错**。
⇒ **规避办法：不要让厂商资源排在资源表最后一条**，或者始终返回 `RSC_HANDLED`。
（详细推导和触发条件见 03 §C2，含"未在硬件上实测"的说明。）

---

## §4 rpmsg 内核客户端：一个 `rpmsg_driver`

### §4.1 骨架（对照 `samples/rpmsg/rpmsg_client_sample.c`）

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/module.h>
#include <linux/rpmsg.h>

struct my_rpmsg_priv {
	int rx_count;
};

/* ---- 收：由 rpmsg 总线在收到消息时调用 ---- */
static int my_rpmsg_cb(struct rpmsg_device *rpdev, void *data, int len,
		       void *priv, u32 src)
{
	struct my_rpmsg_priv *p = dev_get_drvdata(&rpdev->dev);

	dev_info(&rpdev->dev, "msg #%d from 0x%x, %d bytes\n",
		 ++p->rx_count, src, len);

	/* 返回 0 表示"处理了"；rpmsg 核心不看这个值做重试 */
	return 0;
}

static int my_rpmsg_probe(struct rpmsg_device *rpdev)
{
	struct my_rpmsg_priv *p;

	dev_info(&rpdev->dev, "new channel: 0x%x -> 0x%x\n",
		 rpdev->src, rpdev->dst);

	p = devm_kzalloc(&rpdev->dev, sizeof(*p), GFP_KERNEL);
	if (!p)
		return -ENOMEM;
	dev_set_drvdata(&rpdev->dev, p);

	/* rpdev->ept 是核心已经建好的默认端点，直接能发 */
	return rpmsg_send(rpdev->ept, "hello", 5);
}

static void my_rpmsg_remove(struct rpmsg_device *rpdev)	/* 返回 void，不是 int */
{
	dev_info(&rpdev->dev, "removed\n");
}

static struct rpmsg_device_id my_rpmsg_id_table[] = {
	{ .name = "my-service" },	/* 必须和远端声明的 channel 名字一致 */
	{ },
};
MODULE_DEVICE_TABLE(rpmsg, my_rpmsg_id_table);

static struct rpmsg_driver my_rpmsg_driver = {
	.drv.name	= KBUILD_MODNAME,
	.id_table	= my_rpmsg_id_table,
	.probe		= my_rpmsg_probe,
	.callback	= my_rpmsg_cb,
	.remove		= my_rpmsg_remove,
};
module_rpmsg_driver(my_rpmsg_driver);

MODULE_LICENSE("GPL v2");
```

| 骨架元素 | 官方范例对应行 | 头文件声明 |
|---|---|---|
| `rpmsg_rx_cb_t` 五参数原型 | `samples/rpmsg/rpmsg_client_sample.c:25-26` | `include/linux/rpmsg.h:66` |
| `probe` | `samples/rpmsg/rpmsg_client_sample.c:51` | `include/linux/rpmsg.h:113` |
| `remove` 返回 void | `samples/rpmsg/rpmsg_client_sample.c:75` | `include/linux/rpmsg.h:114` |
| `rpmsg_send(rpdev->ept, …)` | `samples/rpmsg/rpmsg_client_sample.c:44`、`:66` | `include/linux/rpmsg.h:180` |
| `rpmsg_device_id` 表 + sentinel | `samples/rpmsg/rpmsg_client_sample.c:80-83` | `include/linux/mod_devicetable.h:454` |
| `MODULE_DEVICE_TABLE(rpmsg, …)` | `samples/rpmsg/rpmsg_client_sample.c:84` | —— |
| `struct rpmsg_driver` 五个成员 | `samples/rpmsg/rpmsg_client_sample.c:86-92` | `include/linux/rpmsg.h:110-116` |
| `module_rpmsg_driver()` | `samples/rpmsg/rpmsg_client_sample.c:93` | `include/linux/rpmsg.h:333-335` |

### §4.2 发送 API 全家：六个函数，两个维度

| | 阻塞（缓冲区满时等） | 非阻塞（满时立刻返回 `-ENOMEM`） |
|---|---|---|
| 发到默认目标 | `rpmsg_send()`（`include/linux/rpmsg.h:180`） | `rpmsg_trysend()`（`include/linux/rpmsg.h:185`） |
| 发到指定 dst | `rpmsg_sendto()`（`include/linux/rpmsg.h:181`） | `rpmsg_trysendto()`（`include/linux/rpmsg.h:186`） |
| 指定 src 和 dst | `rpmsg_send_offchannel()`（`include/linux/rpmsg.h:182`） | `rpmsg_trysend_offchannel()`（`include/linux/rpmsg.h:187`） |

还有一个 `rpmsg_get_mtu()`（`include/linux/rpmsg.h:193`）——
**先查 MTU 再决定分片**，别假设固定 496 字节。

⚠️ **`rpmsg_send()` 不能在原子上下文里调**（它可能睡）。
在 `.callback` 里回发是可以的（范例就这么干，`samples/rpmsg/rpmsg_client_sample.c:44`），
因为 rpmsg 的 rx 回调运行在可睡眠上下文里 —— 但**不要在 mailbox 的 rx 回调里直接调**，
那里是 §2.2 的 `rproc_vq_interrupt()` 上下文。

### §4.3 需要多个端点时才用 `rpmsg_create_ept()`

`include/linux/rpmsg.h:176-178`，配合 `struct rpmsg_channel_info`
（`include/linux/rpmsg.h:34-38`：`name[RPMSG_NAME_SIZE]` + `src` + `dst`）。
头文件注释（`include/linux/rpmsg.h:86-89`）把定位说得很清楚：
简单驱动直接用 `rpdev->ept`，只有需要"多个地址绑不同回调"的才自己建。

端点结构 `struct rpmsg_endpoint`（`include/linux/rpmsg.h:91`）带 `kref`，
销毁走 `rpmsg_destroy_ept()`（`include/linux/rpmsg.h:175`）。

⚠️ 地址用 `RPMSG_ADDR_ANY`（`include/uapi/linux/rpmsg.h:12`，值 `0xFFFFFFFF`）
表示"让核心分配 / 不限制"。

---

## §5 用户态：shell 能做的事和必须写程序的事

### §5.1 纯 shell（sysfs，一定存在）

```sh
# 有哪些远端
ls /sys/class/remoteproc/
cat /sys/class/remoteproc/remoteproc0/name
cat /sys/class/remoteproc/remoteproc0/state      # offline / running / attached / …

# 换固件（必须在 offline 状态下）
echo my-other-fw.elf > /sys/class/remoteproc/remoteproc0/firmware

# 启停
echo start  > /sys/class/remoteproc/remoteproc0/state
echo stop   > /sys/class/remoteproc/remoteproc0/state
echo detach > /sys/class/remoteproc/remoteproc0/state   # 让远端继续跑，Linux 撒手

# 崩溃后要不要自动恢复
cat  /sys/class/remoteproc/remoteproc0/recovery         # enabled / disabled
echo disabled > /sys/class/remoteproc/remoteproc0/recovery
echo recover  > /sys/class/remoteproc/remoteproc0/recovery   # 恢复一次但不改开关

# coredump 模式
echo enabled > /sys/class/remoteproc/remoteproc0/coredump    # disabled/enabled/inline
```

sysfs 这五个文件用 `sysfs_streq()`，**`echo` 带的换行会被忽略**（04 §4.1）。

### §5.2 纯 shell（debugfs，需要挂载 debugfs）

```sh
cd /sys/kernel/debug/remoteproc/remoteproc0/

cat resource_table        # 看内核往固件的资源表里回写了什么
cat carveout_memories     # 每块 carveout 的 va/dma/da/len
cat trace0                # 固件的裸文本日志（固件带 RSC_TRACE 才有）

echo 2 > crash            # 注入一次 "fatal error" 崩溃，验证恢复路径
```

| 三个 debugfs 专属文件的用途 | 出处 |
|---|---|
| `resource_table` —— 调 rpmsg 不通时第一站，看 vring 的 `da` / `notifyid` | 04 §5.3 |
| `crash` —— 唯一的故障注入入口，越界 type 也安全 | 04 §5.1 |
| `trace0` —— 每次读都重新做地址转换 | 04 §7.2 |

### §5.3 字符设备（需要 `CONFIG_REMOTEPROC_CDEV`）

```sh
# ⚠️ 必须用 printf 或 echo -n —— cdev 的 strncmp 不去换行（04 §6.1）
printf start  > /dev/remoteproc0
printf stop   > /dev/remoteproc0
printf detach > /dev/remoteproc0
```

想让"关掉 fd 就自动停远端"，得写个小程序：

```c
#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>

/* 这两个 ioctl 号在 include/uapi/linux/remoteproc_cdev.h */
#define RPROC_SET_SHUTDOWN_ON_RELEASE _IOW(0xB7, 1, __s32)
#define RPROC_GET_SHUTDOWN_ON_RELEASE _IOR(0xB7, 2, __s32)

int main(void)
{
	int fd = open("/dev/remoteproc0", O_RDWR);
	__s32 on = 1;

	ioctl(fd, RPROC_SET_SHUTDOWN_ON_RELEASE, &on);
	write(fd, "start", 5);		/* 5，不带换行 */

	/* … 守护进程主体 … */

	close(fd);			/* 到这里远端被自动 shutdown */
	return 0;
}
```

⚠️ 上面两个 `_IOW`/`_IOR` 宏定义**请以 `include/uapi/linux/remoteproc_cdev.h` 为准**，
本篇没有逐字抄。实现侧在 `drivers/remoteproc/remoteproc_cdev.c:55` 和
`drivers/remoteproc/remoteproc_cdev.c:61`，release 侧逻辑在
`drivers/remoteproc/remoteproc_cdev.c:75-86`。

默认值是 0（关 fd 不影响远端，`drivers/remoteproc/remoteproc_cdev.c:80-81`）。

### §5.4 用户态收发 rpmsg（需要 `RPMSG_CHAR` + `RPMSG_CTRL`）

两个设备节点：

| 节点 | 谁创建 | 用途 |
|---|---|---|
| `/dev/rpmsg_ctrl0` | `drivers/rpmsg/rpmsg_ctrl.c:168`（`dev_set_name(..., "rpmsg_ctrl%d", ...)`） | 控制口：用 ioctl 造/销端点 |
| `/dev/rpmsgN` | `drivers/rpmsg/rpmsg_char.c:403`（`dev_set_name(dev, "rpmsg%d", ret)`） | 数据口：`read()` / `write()` 收发 |

```c
#include <linux/rpmsg.h>	/* uapi */
#include <fcntl.h>
#include <sys/ioctl.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
	struct rpmsg_endpoint_info info = {
		.src = RPMSG_ADDR_ANY,
		.dst = RPMSG_ADDR_ANY,
	};
	int ctrl, data;
	char buf[512];

	strcpy(info.name, "my-service");	/* name[32]，别超 */

	ctrl = open("/dev/rpmsg_ctrl0", O_RDWR);
	ioctl(ctrl, RPMSG_CREATE_EPT_IOCTL, &info);	/* → 生成 /dev/rpmsgN */

	data = open("/dev/rpmsg0", O_RDWR);
	write(data, "hello", 5);
	read(data, buf, sizeof(buf));

	ioctl(data, RPMSG_DESTROY_EPT_IOCTL);
	close(data);
	close(ctrl);
	return 0;
}
```

| uapi 元素 | 位置 |
|---|---|
| `struct rpmsg_endpoint_info`（`name[32]` / `src` / `dst`） | `include/uapi/linux/rpmsg.h:21-25` |
| `RPMSG_ADDR_ANY` = `0xFFFFFFFF` | `include/uapi/linux/rpmsg.h:12` |
| `RPMSG_CREATE_EPT_IOCTL` | `include/uapi/linux/rpmsg.h:29` |
| `RPMSG_DESTROY_EPT_IOCTL` | `include/uapi/linux/rpmsg.h:34` |
| `RPMSG_CREATE_DEV_IOCTL` | `include/uapi/linux/rpmsg.h:39` |
| `RPMSG_RELEASE_DEV_IOCTL` | `include/uapi/linux/rpmsg.h:44` |

后两个 ioctl（`CREATE_DEV` / `RELEASE_DEV`）造的是**rpmsg 设备**而不是端点 ——
也就是让内核态的 `rpmsg_driver`（§4）去 probe。
⇒ 「用户态造端点」和「让内核驱动接管」是两条不同的路，别混。

---

## §6 常见错误速查

| 症状 | 最可能的原因 | 去哪一节 |
|---|---|---|
| `rproc_boot()` 返回 `-EINVAL`，dmesg 无更多信息 | 没填 `.start` | §1.1 |
| dmesg: `.kick method not defined for …` | 固件带 `RSC_VDEV` 但 ops 没填 `.kick` | §1.1 · 04 §1.5 |
| 固件加载成功但 `/sys/bus/rpmsg/devices/` 空 | 没开 `CONFIG_RPMSG_VIRTIO` | §0.3 |
| rpmsg 通道建起来了但收不到消息 | `rproc_vq_interrupt()` 的 `vqid` 传错，一直 `IRQ_NONE` | §2.2 |
| attach 场景下远端跑一会儿就死 | 时钟准备放在了 `.prepare` 里，attach 不走那条路 | §2.3 |
| virtio buffer 落在了一块意料之外的内存上 | carveout 名字不是 `vdev0buffer`，或者用了通用版 init | §3.2 · §3.3 |
| 厂商资源处理正常但 `rproc_boot()` 报错 | 厂商资源排在资源表最后一条，`RSC_IGNORED` 被当成错误 | §3.4 · 03 §C2 |
| `printf start > /dev/remoteproc0` 有效，`echo start` 无效 | cdev 不去换行 | §5.3 |
| `cat coredump` 是 `enabled` 但崩溃后没有 dump 文件 | 驱动没调 `rproc_coredump_set_elf_info()`，或没登记任何段 | 04 §3.1 |
| coredump 里一整片 `0xff` | 那一段的 `rproc_da_to_va()` 返回 NULL | 04 §3.4 |
| 恢复流程卡住 5 分钟 | `coredump` 设成了 `inline` 却没人去读 dump 文件 | 04 §3.3 |

---

## §7 本篇覆盖了哪些角色、漏了哪些

| 角色 | 本篇给了什么 | 没给什么 |
|---|---|---|
| 平台驱动作者 | 三个必填回调 + 进阶 12 个 + carveout 登记 | 具体硬件的复位/时钟序列（因平台而异） |
| rpmsg 客户端作者 | 完整骨架 + 六个发送 API | 流控与分片策略（属于 rpmsg 子系统，不在本套范围） |
| 用户态运维 | sysfs / debugfs / cdev / rpmsg char 四条路 | 固件打包与签名（属于厂商工具链） |
| **固件作者** | **本篇没有单独成节** —— 资源表的构造在 02 §4 和 06 §2 讲 | 远端侧的 OpenAMP / RTOS 配置 |

⚠️ 再强调一次篇首的免责声明：**本篇代码全部未编译未运行**。
每个骨架都点名了它提炼自哪个真实驱动（§0.2），落地时请以那个驱动为准。

---

**下一篇**：06 篇讲 DTS 怎么写、每条属性由谁消费、配错了怎么定位。
