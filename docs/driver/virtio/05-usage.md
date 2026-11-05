---
title: 使用指南
---

# virtio 05：各角色的代码骨架

⚠️ **本篇所有代码骨架都没有编译过、没有运行过**（99 §5.1 有完整声明）。
它们是"形状正确"的示意：省略了部分头文件、`MODULE_*` 宏、完整的错误回滚标签。
**照抄前请对着 §0.2 点名的真实驱动 diff 一遍。**

**正文里出现的每个内核符号都做过全树存在性核对**（99 §7 有清单）。

---

## §0 本子系统有哪些角色

**四个角色，但规模极不对称**：

| # | 角色 | 你要做什么 | 人数 | 去哪一节 |
|---|---|---|---|---|
| 1 | **设备驱动作者** | 写一个新的 virtio 设备驱动 | **25 个（22 个目录）** | §1–§4 |
| 2 | **传输层作者** | 给一种新的虚拟化传输接上 virtio | **9 个（5 个目录）** | §5 |
| ⚠️ 3 | **对端（device 侧）作者** | 实现 virtio 设备 | — | ⚠️ **§6：本套不覆盖** |
| 4 | **调试者** | 设备不工作 / 数据错乱 | — | §7（+ 07 篇） |

### §0.2 本篇参照的真实驱动

| 用途 | 文件 | 行数 | 为什么选它 |
|---|---|---|---|
| **最简完整设备驱动** | `drivers/virtio/virtio_input.c` | **414** | **一个方向一个队列、配置空间读、`remove` 里正确复位**——五脏俱全且最短 |
| **arm64 上的传输层** | `drivers/virtio/virtio_mmio.c` | 860 | arm64 虚拟机的主路（04 §2） |
| **全功能传输层** | `drivers/virtio/virtio_pci_modern.c` + `virtio_pci_common.c` | 556 + 654 | 两份 ops、MSI-X 分配 |

---

## §1 设备驱动：完整骨架

```c
#include <linux/virtio.h>
#include <linux/virtio_config.h>
#include <linux/virtio_ids.h>
#include <linux/scatterlist.h>
#include <uapi/linux/virtio_my.h>	/* 规范定义的结构，在 uapi 里 */

struct my_virtio_dev {
	struct virtio_device	*vdev;
	struct virtqueue	*rxvq;
	struct virtqueue	*txvq;
	spinlock_t		tx_lock;	/* 框架不加锁，你自己加（02 §7.2） */
	bool			ready;
};

/* ═══════ ① feature 表：必须列全你会查的每一位 ═══════ */
static unsigned int features[] = {
	VIRTIO_MY_F_FOO,
	VIRTIO_MY_F_BAR,
	/*
	 * ⚠️ 不要列 VIRTIO_F_VERSION_1 / VIRTIO_F_ACCESS_PLATFORM —
	 * 传输层的 feature 位由核心无条件保留（03 §A4.3）
	 */
};

/* ═══════ ② 收方向的回调 ═══════ */
static void my_rx_done(struct virtqueue *vq)
{
	struct my_virtio_dev *my = vq->vdev->priv;	/* vdev->priv，不是 vq->priv */
	unsigned int len;
	void *token;

	/* 循环取，直到返回 NULL —— NULL 只表示"暂时没有"（01 §9.2） */
	while ((token = virtqueue_get_buf(vq, &len)) != NULL)
		my_handle_one_rx(my, token, len);

	/* 补充新的接收缓冲区，然后 kick */
	my_refill_rx(my);
}

static void my_refill_rx(struct my_virtio_dev *my)
{
	struct scatterlist sg;
	bool kick = false;

	for (;;) {
		void *buf = my_alloc_rxbuf(my);
		int err;

		if (!buf)
			break;

		sg_init_one(&sg, buf, MY_RXBUF_SIZE);

		/* add_inbuf = "让对端写"；第 4 个参数是你的 token */
		err = virtqueue_add_inbuf(my->rxvq, &sg, 1, buf, GFP_ATOMIC);
		if (err) {
			/*
			 * ⚠️-ENOSPC 是队列满，属于正常背压，不是错误（01 §9.2）
			 * 千万不要 dev_err 或往上报
			 */
			my_free_rxbuf(my, buf);
			break;
		}
		kick = true;
	}

	if (kick)
		virtqueue_kick(my->rxvq);
}

/* ═══════ ③ 发方向 ═══════ */
static int my_send(struct my_virtio_dev *my, void *data, size_t len)
{
	struct scatterlist sg;
	unsigned long flags;
	int err;

	sg_init_one(&sg, data, len);

	spin_lock_irqsave(&my->tx_lock, flags);	/* 同一个 vq 的并发要自己挡 */

	/* add_outbuf = "给对端读" */
	err = virtqueue_add_outbuf(my->txvq, &sg, 1, data, GFP_ATOMIC);
	if (err) {
		spin_unlock_irqrestore(&my->tx_lock, flags);
		return err;			/* -ENOSPC ⇒ 上层停发，别报错 */
	}

	/*
	 * 拆开 kick 的两步：批量提交时只在最后判断一次
	 * kick_prepare() 返回 false ⇒ 对端说不用通知，别 notify（01 §3.2）
	 */
	if (virtqueue_kick_prepare(my->txvq)) {
		spin_unlock_irqrestore(&my->tx_lock, flags);
		virtqueue_notify(my->txvq);
	} else {
		spin_unlock_irqrestore(&my->tx_lock, flags);
	}

	return 0;
}

/* ═══════ ④ validate：唯一能改 feature 的地方（03 §A4.1）═══════ */
static int my_validate(struct virtio_device *vdev)
{
	/*
	 * 想否掉某个位就在这里 __virtio_clear_bit()
	 * 核心会检测到 features 变了并重新 finalize_features()
	 */
	if (my_platform_cannot_do_foo())
		__virtio_clear_bit(vdev, VIRTIO_MY_F_FOO);

	return 0;
}

/* ═══════ ⑤ probe ═══════ */
static int my_probe(struct virtio_device *vdev)
{
	struct virtqueue *vqs[2];
	vq_callback_t *cbs[] = { my_rx_done, my_tx_done };
	static const char * const names[] = { "rx", "tx" };  /* 会出现在中断名里 */
	struct my_virtio_dev *my;
	u32 max_len;
	int err;

	my = devm_kzalloc(&vdev->dev, sizeof(*my), GFP_KERNEL);
	if (!my)
		return -ENOMEM;

	my->vdev = vdev;
	vdev->priv = my;			/* 挂在 vdev->priv 上 */
	spin_lock_init(&my->tx_lock);

	/* 查 feature：只能查 features[] 里列过的，否则 BUG（01 §4.1） */
	if (virtio_has_feature(vdev, VIRTIO_MY_F_FOO))
		my->foo_enabled = true;

	/* 读配置空间：用结构化访问器，别按偏移硬算（01 §5.1） */
	virtio_cread_le(vdev, struct virtio_my_config, max_len, &max_len);

	/* 只在某个 feature 有效时才读的字段 */
	err = virtio_cread_feature(vdev, VIRTIO_MY_F_BAR,
				   struct virtio_my_config, bar, &my->bar);
	if (err == -ENOENT)
		my->bar = MY_BAR_DEFAULT;	/* -ENOENT = 没这个 feature */

	/* 建队列 */
	err = virtio_find_vqs(vdev, 2, vqs, cbs, names, NULL);
	if (err)
		return err;

	my->rxvq = vqs[0];
	my->txvq = vqs[1];

	/* 先填好接收缓冲区，再 ready —— 顺序很重要（§1.2） */
	my_refill_rx(my);

	/*
	 * 必须调！否则规范规定对端不得使用队列 ⇒ 数据永远不动
	 * 6.1 上核心有兜底（03 §A7），但不要依赖它
	 */
	virtio_device_ready(vdev);
	my->ready = true;

	return 0;
}

/* ═══════ ⑥ remove：⚠️ 必须自己复位 ═══════ */
static void my_remove(struct virtio_device *vdev)
{
	struct my_virtio_dev *my = vdev->priv;

	my->ready = false;

	/* 先 break，让所有正在进行的 kick/get_buf 变成无操作 */
	virtio_break_device(vdev);

	/* 然后 flush 掉自己的工作队列/定时器等其它上下文 */
	my_flush_all_contexts(my);

	/*
	 * ⚠️ 必须复位！virtio_dev_remove() 里有
	 * WARN_ON_ONCE(dev->config->get_status(dev))（03 §A9）
	 */
	virtio_reset_device(vdev);

	/* 把还没被处理的缓冲区全拿回来，否则泄漏 */
	my_reclaim_all(my);
}

static void my_reclaim_all(struct my_virtio_dev *my)
{
	void *buf;

	/* 循环到返回 NULL（01 §3.3） */
	while ((buf = virtqueue_detach_unused_buf(my->rxvq)) != NULL)
		my_free_rxbuf(my, buf);
	while ((buf = virtqueue_detach_unused_buf(my->txvq)) != NULL)
		my_free_txbuf(my, buf);
}

/* ═══════ ⑦ 配置变更 ═══════ */
static void my_config_changed(struct virtio_device *vdev)
{
	struct my_virtio_dev *my = vdev->priv;
	u32 new_len;

	/* 重新读配置空间；⚠️ 多字段要考虑 generation 重试（01 §5.2） */
	virtio_cread_le(vdev, struct virtio_my_config, max_len, &new_len);
	my_apply_new_len(my, new_len);
}

/* ═══════ ⑧ 注册 ═══════ */
static const struct virtio_device_id id_table[] = {
	{ VIRTIO_ID_MY, VIRTIO_DEV_ANY_ID },	/* 只认类型，不挑厂商（03 §A3） */
	{ 0 },
};

static struct virtio_driver my_virtio_driver = {
	.driver.name		= KBUILD_MODNAME,
	.driver.owner		= THIS_MODULE,
	.id_table		= id_table,
	.feature_table		= features,
	.feature_table_size	= ARRAY_SIZE(features),
	.validate		= my_validate,
	.probe			= my_probe,
	.remove			= my_remove,
	.config_changed		= my_config_changed,
};

module_virtio_driver(my_virtio_driver);		/* 一行搞定 init/exit */
MODULE_DEVICE_TABLE(virtio, id_table);
```

### §1.1 对照真实驱动

**上面每一步在 `drivers/virtio/virtio_input.c` 里的对应位置**：

| 步 | 骨架里的 | `virtio_input.c` |
|---|---|---|
| 提交接收缓冲区 | `virtqueue_add_inbuf()` | `drivers/virtio/virtio_input.c:30` |
| 回收 | `virtqueue_get_buf()` | `drivers/virtio/virtio_input.c:42` · `:114` |
| 通知 | `virtqueue_kick()` | `drivers/virtio/virtio_input.c:51` · `:95` · `:214` |
| 读配置空间（结构化） | `virtio_cread_le()` | `drivers/virtio/virtio_input.c:134` · `:177-181` |
| 读配置空间（字节） | `virtio_cread_bytes()` | `drivers/virtio/virtio_input.c:159` · `:248` · `:252` |
| 建队列 | `virtio_find_vqs()` | `drivers/virtio/virtio_input.c:194`（**2 个队列**） |
| ready | `virtio_device_ready()` | `drivers/virtio/virtio_input.c:317`（probe）· `:380`（restore） |
| **remove 里复位** | `virtio_reset_device()` | `drivers/virtio/virtio_input.c:350` |
| 注册 | `id_table` + `virtio_driver` | `drivers/virtio/virtio_input.c:390` · `:395` |

⇒ **`virtio_input.c` 用的是 `virtio_cread_le()` 而不是 `virtio_cread()`** ——
因为 `struct virtio_input_config` 的字段规范里明确是小端（01 §4.4 的例外）。
**抄骨架时要按你的设备类型的规范决定用哪个。**

### §1.2 ⚠️ 五个顺序约束

| 约束 | 为什么 |
|---|---|
| **`virtio_find_vqs()` 之后才能 `add_*()`** | 队列还不存在 |
| **填好接收缓冲区再 `virtio_device_ready()`** | **ready 之后对端立刻可能发数据**，没缓冲区就丢 |
| **`virtio_device_ready()` 必须在 `probe()` 返回前** | 规范：对端在 `DRIVER_OK` 前不得用队列（03 §A7） |
| **`remove()` 里先 `virtio_break_device()` 再 flush 别的上下文** | `virtio_reset_device()` 的 kerneldoc 明确建议这个顺序（03 §A9） |
| **`virtio_reset_device()` 之后才 `detach_unused_buf()`** | 复位保证对端不再动那些描述符 |

### §1.3 ⚠️ 锁：框架一点都不帮你

**`struct virtqueue` 没有任何锁**（02 §7.2、03 §B7）。
**哪些情况必须自己加**：

| 情况 | 要不要加锁 |
|---|---|
| 同一个 vq 上 `add_*()` 和 `get_buf()` 并发 | **要** |
| 多个 CPU 同时 `add_outbuf()` 到同一个 tx vq | **要** |
| 回调（软中断）和 `add_*()`（进程上下文）碰同一个 vq | **要**（用 `spin_lock_irqsave`） |
| 不同 vq 之间 | **不要** ——天然互不干扰 |
| 只有回调里访问某个 vq（单一上下文） | **不要** |

**网卡类驱动的常见做法**：收方向只在 NAPI 里碰（天然串行），
发方向靠上层的 tx lock ⇒ **两个方向都不用额外加锁**。

⚠️ **验证手段**：编译时定义 `DEBUG`，`struct vring_virtqueue.in_use`
（`drivers/virtio/virtio_ring.c:206-207`，注释 `They're supposed to lock for us.`）
会抓到重入（07 篇讲怎么开）。

---

## §2 数据通路的三种常见形态

### §2.1 形态 A：请求-响应（blk / scsi 型）

```c
	/* 一次提交里既有"给对端读"的请求头，又有"让对端写"的数据区 */
	struct scatterlist *sgs[2];

	sg_init_one(&hdr_sg, &req->hdr, sizeof(req->hdr));
	sg_init_one(&data_sg, req->buf, req->len);

	sgs[0] = &hdr_sg;	/* out */
	sgs[1] = &data_sg;	/* in  */

	/* ⚠️ out 必须排在 in 之前 —— 规范要求（01 §3.1） */
	err = virtqueue_add_sgs(vq, sgs, 1 /* out_sgs */, 1 /* in_sgs */,
				req, GFP_ATOMIC);
```

### §2.2 形态 B：单向流（console / input 型）

**收发各一个队列，各自只做一个方向** ⇒ §1 的骨架就是这个形态
（`virtio_input.c` 也是）。

### §2.3 形态 C：批量 + 延迟通知（net 型）

```c
	/* NAPI 轮询里：关掉通知，处理一批，再开 */
	virtqueue_disable_cb(vq);

	while (budget-- && (token = virtqueue_get_buf(vq, &len)))
		my_handle(token, len);

	/*
	 * enable_cb 返回 false ⇒ 开启期间又来了新数据
	 * ⇒ 必须立刻再轮一次，否则丢通知
	 */
	if (!virtqueue_enable_cb(vq))
		goto again;
```

⚠️ **`virtqueue_enable_cb()` 返回 false 必须处理** ——
这是 virtio 驱动最经典的一类丢中断 bug。
**想让对端"攒一批再通知"用 `virtqueue_enable_cb_delayed()`**（01 §3.4）。

⚠️ **`virtqueue_disable_cb()` 只是提示，对端可以无视**（01 §3.4）
⇒ **回调必须能承受"不该来的时候来了"**。

---

## §3 电源管理

```c
#ifdef CONFIG_PM_SLEEP
static int my_freeze(struct virtio_device *vdev)
{
	struct my_virtio_dev *my = vdev->priv;

	my->ready = false;
	virtio_reset_device(vdev);		/* freeze 也要复位 */
	my_reclaim_all(my);
	vdev->config->del_vqs(vdev);
	return 0;
}

static int my_restore(struct virtio_device *vdev)
{
	int err = my_init_vqs(vdev);		/* 队列要重建 */

	if (err)
		return err;

	virtio_device_ready(vdev);		/* 别忘了再 ready 一次 */
	my_refill_rx(vdev->priv);
	return 0;
}
#endif

static struct virtio_driver my_virtio_driver = {
	...
#ifdef CONFIG_PM_SLEEP
	.freeze	= my_freeze,
	.restore = my_restore,
#endif
};
```

⚠️ **`freeze`/`restore` 两个字段本身就在 `#ifdef CONFIG_PM` 里**
（`include/linux/virtio.h:187-190`）⇒ **赋值处也要包 `#ifdef`**，否则编不过。

**`virtio_input.c:380` 的 `virtio_device_ready()` 就是 restore 路径的那一次。**

---

## §4 ⚠️ 字节序：arm64 上不会暴露的坑

```c
	/* ⚠️ 错的：直接用 le16_to_cpu() 读 __virtio16 字段 */
	u16 bad = le16_to_cpu(shared->some_field);

	/* 对的：用 vdev 感知的转换（01 §4.4） */
	u16 good = virtio16_to_cpu(vdev, shared->some_field);
```

**为什么 arm64 上看不出来**：arm64 通常是小端，
而 `__virtio16` 在现代设备上也是小端 ⇒ **两种写法结果一样**。
⚠️ **一旦跑在大端机器上并接了 legacy 设备，就全错**。

**例外**：规范明确写"这个字段就是小端"的地方，用 `virtio_cread_le()` /
`virtio_le_to_cpu()`（01 §4.4）。**`virtio_input.c` 全篇用的都是 `_le` 版本。**

⇒ **判据：看规范怎么写的，不看你的平台。**

---

## §5 传输层作者

**只有给新的虚拟化传输方式接 virtio 时才写这一层**（9 个实现，01 §1.1）。

```c
static const struct virtio_config_ops my_transport_config_ops = {
	/* ═══ 必填的 8 个 ═══ */
	.get			= my_get_config,
	.set			= my_set_config,
	.get_status		= my_get_status,
	.set_status		= my_set_status,
	.reset			= my_reset,
	.find_vqs		= my_find_vqs,
	.del_vqs		= my_del_vqs,
	.get_features		= my_get_features,
	.finalize_features	= my_finalize_features,

	/* ═══ 可选的 ═══ */
	.generation		= my_generation,	/* 配置空间版本号 */
	.synchronize_cbs	= my_synchronize_cbs,
	.bus_name		= my_bus_name,
	.set_vq_affinity	= my_set_vq_affinity,
	.get_vq_affinity	= my_get_vq_affinity,
	.get_shm_region		= my_get_shm_region,
	.disable_vq_and_reset	= my_disable_vq_and_reset,  /* virtqueue_resize 要它 */
	.enable_vq_after_reset	= my_enable_vq_after_reset,
};

static int my_transport_probe(struct my_bus_device *bdev)
{
	struct my_vdev *mv = kzalloc(sizeof(*mv), GFP_KERNEL);

	if (!mv)
		return -ENOMEM;

	/* 三件事：填 id、填 config、调 register */
	mv->vdev.id.device	= my_read_device_id(bdev);
	mv->vdev.id.vendor	= my_read_vendor_id(bdev);
	mv->vdev.config		= &my_transport_config_ops;
	mv->vdev.dev.parent	= &bdev->dev;
	mv->vdev.dev.release	= my_vdev_release;

	/* 核心会先 reset、加 ACKNOWLEDGE、然后 device_add（03 §A2） */
	return register_virtio_device(&mv->vdev);
}

static bool my_notify(struct virtqueue *vq)
{
	struct my_vdev *mv = to_my_vdev(vq->vdev);

	/* 写一个寄存器，或发一个 IPI —— 这是唯一一次陷入（02 §2） */
	my_write_reg(mv, MY_QUEUE_NOTIFY, vq->index);
	return true;
}

static int my_find_vqs(struct virtio_device *vdev, unsigned int nvqs,
		       struct virtqueue *vqs[], vq_callback_t *callbacks[],
		       const char * const names[], const bool *ctx,
		       struct irq_affinity *desc)
{
	int i;

	for (i = 0; i < nvqs; i++) {
		u32 num = my_read_reg(mv, MY_QUEUE_NUM_MAX);

		/* weak_barriers：对端是同机软件 ⇒ true；是硬件 ⇒ false（03 §B1.2） */
		vqs[i] = vring_create_virtqueue(i, num, MY_VRING_ALIGN, vdev,
						true /* weak_barriers */,
						false /* may_reduce_num */,
						ctx ? ctx[i] : false,
						my_notify, callbacks[i], names[i]);
		if (!vqs[i])
			return -ENOMEM;

		/* 把三段的物理地址告诉对端（04 §2.2 的寄存器） */
		my_write_queue_addrs(mv, i,
				     virtqueue_get_desc_addr(vqs[i]),
				     virtqueue_get_avail_addr(vqs[i]),
				     virtqueue_get_used_addr(vqs[i]));
	}
	return 0;
}
```

### §5.1 ⚠️ 传输层作者的五个坑

| 坑 | 后果 |
|---|---|
| ⚠️ **忘了在 `register_virtio_device()` 前填 `vdev->config`** | 核心第一件事就是 `virtio_reset_device()`，会解引用 NULL |
| ⚠️ **`weak_barriers` 传错** | 对端是硬件却传 true ⇒ **偶发数据错乱，极难查**（03 §B1.2） |
| ⚠️ **`get_features` 只返回低 32 位** | MMIO 要分两次读（`DEVICE_FEATURES_SEL`，04 §2.2） ⇒ **漏掉高位就永远协商不上 `VERSION_1`** |
| ⚠️ **`finalize_features` 不写回设备** | 驱动和设备认知分叉（01 §4.3） |
| ⚠️ **不实现 `disable_vq_and_reset`/`enable_vq_after_reset`** | `virtqueue_resize()` 会失败 ⇒ 驱动必须能处理（03 §B6） |

---

## §6 ⚠️ 对端（device 侧）作者：**本套不覆盖**

**显式声明，不是遗漏**（01 §0.1、99 §5.5）：

| 你要做的 | 本套能帮你吗 |
|---|---|
| 实现一个 virtio 设备（QEMU / vhost / 硬件） | **不能** ——去读 OASIS VIRTIO 规范 |
| 理解共享内存布局 | **能** ——02 §5 的字节级布局对两边都成立 |
| 写 vhost 后端 | **部分** ——`drivers/vhost/vringh.c`(1511) 在本树里，但**本套没读它**（04 §4） |

**唯一的例外是 02 §5** ——那一节画的是**双方共同遵守的布局**，
所以对 device 侧作者同样有用。**但"什么时候该做什么"（协议时序）不在本套。**

---

## §7 近邻符号区分（本篇最该细读的一节）

### §7.1 ⚠️`vdev->priv` vs `vq->priv`

| 字段 | 位置 | 归谁 |
|---|---|---|
| `vdev->priv` | `include/linux/virtio.h:127` | **设备驱动的** |
| ⚠️`vq->priv` | `include/linux/virtio.h:37` | ⚠️ **传输层的** |

**错用会怎样**：往 `vq->priv` 里写自己的指针 ⇒
**踩坏传输层保存的状态**（PCI 传输层用它存 MSI-X 向量之类）
⇒ 表现是**中断错乱或崩溃**，而且**只在某些传输层上出现**。

### §7.2 ⚠️`virtio_cread()` vs `virtio_cread_le()`

| | 端序 |
|---|---|
| `virtio_cread()` | **按 feature 协商结果**（`virtio_is_little_endian()`） |
| `virtio_cread_le()` | **无条件小端** |

**错用会怎样**：规范说"小端"的字段用了 `virtio_cread()`
⇒ **接 legacy 设备且跑在大端机器上时读出错误值**。
**arm64 小端上两者结果相同 ⇒ 测不出来**（§4）。

⇒ **判据：查规范怎么写的。** `virtio_input.c` 全用 `_le`。

### §7.3 ⚠️`virtio_has_feature()` vs `__virtio_test_bit()`

| | 校验 | 给谁用 |
|---|---|---|
| `virtio_has_feature()` | **会校验你在 `feature_table` 里声明过** | **设备驱动** |
| ⚠️`__virtio_test_bit()` | **不校验** | **传输层**（头文件注释明说，`include/linux/virtio_config.h:128-129`） |

**错用会怎样**：驱动用 `__virtio_test_bit()` 查一个没声明的位
⇒ **绕过了那个保护，读到一个永远为 0 的位**，静默走错分支。
用 `virtio_has_feature()` 会直接 `BUG` ⇒ **崩得早比错得静默好**。

### §7.4 ⚠️`virtqueue_add_outbuf()` vs `_inbuf()`

| | 方向 | 描述符 flag |
|---|---|---|
| `add_outbuf` | **给对端读**（我写的数据） | 不设 `VRING_DESC_F_WRITE` |
| `add_inbuf` | **让对端写**（我提供的空缓冲区） | 设 `VRING_DESC_F_WRITE`（02 §5.4） |

**错用会怎样**：把接收缓冲区用 `add_outbuf()` 提交
⇒ **对端认为那是要读的数据，不会往里写** ⇒ 永远收不到东西。

### §7.5 ⚠️`virtio_break_device()` vs `virtio_reset_device()`

| | 做什么 | 之后 |
|---|---|---|
| `virtio_break_device()` | **把所有队列标 broken** | `kick()` 返回 false、`get_buf()` 返回 NULL |
| `virtio_reset_device()` | **真的复位设备** | **状态字节归 0** |

**错用会怎样**：`remove()` 里只 `break` 不 `reset`
⇒ **`virtio_dev_remove()` 的 `WARN_ON_ONCE(get_status())` 会报**（03 §A9）。
**正确顺序是 break → flush 其它上下文 → reset**（§1.2）。

### §7.6 ⚠️`include/linux/virtio_*.h` vs `include/uapi/linux/virtio_*.h`

**四个名字在两边都有**：`virtio_config.h` · `virtio_net.h` ·
`virtio_ring.h` · `virtio_console.h`（02 §1.0.C）。

**错用会怎样**：想用 `struct virtio_net_config`（规范结构）
却 `#include <linux/virtio_net.h>`（内核辅助）⇒ **编译报找不到类型**。
**这个错会在编译期暴露**，所以危害有限。

### §7.7 ⚠️ 内核 `VIRTIO_ID_BALLOON`(5) vs `VIRTIO_ID_MEMORY_BALLOON`(13)

| 常量 | 值 | 位置 | 谁用 |
|---|---|---|---|
| `VIRTIO_ID_BALLOON` | **5** | `include/uapi/linux/virtio_ids.h:36` | `drivers/virtio/virtio_balloon.c` |
| ⚠️`VIRTIO_ID_MEMORY_BALLOON` | **13** | `include/uapi/linux/virtio_ids.h:44` | **不是那个驱动**（04 §5） |

**错用会怎样**：`id_table` 里写错编号 ⇒ **驱动永远不被匹配**，
而且**没有任何日志** ⇒ 表现为"设备在 `/sys/bus/virtio/devices/` 里但没绑驱动"。

### §7.8 ⚠️`virtio_has_dma_quirk()` 的命名是反的

**返回真 = "有 quirk" = 不走 DMA API 的老行为**
（实现是 `!virtio_has_feature(vdev, VIRTIO_F_ACCESS_PLATFORM)`，
`include/linux/virtio_config.h:204`）。

**错用会怎样**：以为"有 quirk"是新行为 ⇒ 判断写反（03 §B2）。

---

## §8 常见错误速查

| 症状 | 最可能的原因 | 去哪一节 |
|---|---|---|
| **probe 成功但数据永远不动** | **忘了 `virtio_device_ready()`** | §1.2 · 03 §A7 |
| **`get_buf()` 一直返回 NULL** | 同上；或对端还没处理完 | §1.2 |
| **收不到任何数据** | **接收缓冲区用了 `add_outbuf()`** | §7.4 |
| **驱动完全不被匹配** | **`id_table` 里设备编号写错**（无日志） | §7.7 |
| **查 feature 时内核崩** | **忘了把那个位加进 `feature_table`** | §1 的 ① · 01 §4.1 |
| **`remove()` 后 `WARN_ON_ONCE`** | **忘了 `virtio_reset_device()`** | §7.5 · 03 §A9 |
| **高负载下报一堆错** | **把 `-ENOSPC` 当致命错误** | §1 的 ② · 01 §9.2 |
| **偶发丢中断/卡住** | **`virtqueue_enable_cb()` 返回 false 没处理** | §2.3 |
| **释放对象后崩溃** | **没 `detach_unused_buf()` 到 NULL** | §1 的 ⑥ |
| **偶发数据错乱（多 CPU）** | **同一个 vq 上没加锁** | §1.3 · 02 §7.2 |
| **中断错乱/崩溃，且换传输层就变** | **往 `vq->priv` 里写了东西** | §7.1 |
| **大端机器上全错** | **字节序转换用错**（`le16_to_cpu` 而非 `virtio16_to_cpu`） | §4 · §7.2 |
| **`virtqueue_resize()` 失败** | **传输层没实现那两个可选回调** | §5.1 · 03 §B6 |
| **`freeze`/`restore` 编不过** | 字段在 `#ifdef CONFIG_PM` 里 | §3 |

---

## §9 本篇覆盖了哪些角色、漏了哪些

| 角色 | 覆盖情况 |
|---|---|
| 设备驱动作者 | **完整**：feature 表 / 双向数据通路 / validate / probe / remove / PM / 三种数据形态 |
| 传输层作者 | **骨架完整**（ops 表 + probe + notify + find_vqs），⚠️ **中断分配（MSI-X）没展开** |
| ⚠️ 对端（device 侧）作者 | ⚠️ **显式不覆盖**（§6） |
| 调试者 | ⚠️ **本篇只给速查表，具体操作全在 07 篇** |

⚠️ **本篇没有覆盖的三块**（显式声明）：

| 没覆盖的 | 为什么 |
|---|---|
| **中断分配的细节** | PCI 的 MSI-X 向量分配在 `virtio_pci_common.c`(654) 里，**和 virtio 抽象无关**，是 PCI 的事 |
| **对端侧的时序** | 规范在树外（§6、99 §5.5） |
| **vringh 的用法** | 它是 device 侧的库（04 §4），本套没读 |
