---
title: 内部实现
---

# virtio 03：内部机制逐段剖析

## §0 导航

**本篇只承诺一条主轴：Part A 是"一个 virtio 设备从出现到消失"的时间线。**
不按时间线组织的内容划到 Part B（横切机制）和 Part C（设计评价），
章节号带 `A`/`B`/`C` 前缀。

### §0.1 分层图

```
  ┌─ 设备驱动（25 个）───────────────────────────────────┐
  │  probe / remove / config_changed / validate / scan   │
  └──────────────────────┬───────────────────────────────┘
  ┌──────────────────────▼───────────────────────────────┐
  │  virtio_bus + virtio.c(577)                          │
  │  匹配 · feature 协商 · 状态字节编排                    │
  └──────────┬───────────────────────┬───────────────────┘
             │ virtio_config_ops      │ virtqueue_*
  ┌──────────▼──────────┐  ┌──────────▼───────────────────┐
  │ 9 个传输层           │  │ virtio_ring.c(2876)          │
  │ 控制面：寄存器访问    │  │ 数据面：vring + 屏障 + DMA     │
  └──────────┬──────────┘  └──────────┬───────────────────┘
             │ 通知                    │ 读写共享内存
  ═══════════▼════════════════════════▼═══════════════════
             对端（不在本树）
```

### §0.2 生命周期时间线（Part A 的骨架）

```
传输层探测到设备（PCI probe / DT / cmdline）        §A1
  │
register_virtio_device(dev)          virtio.c:418   §A2
  ├─ ida 分配 index，dev_set_name("virtio%u")
  ├─ virtio_device_of_init(dev)       virtio.c:366  DT 关联
  ├─ 初始化两把锁 + vqs 链表
  ├─ virtio_reset_device(dev)     ← 先复位
  ├─ 状态位 += ACKNOWLEDGE
  └─ device_add() ⇒ 总线开始找驱动
       │
§A3  virtio_dev_match()              （virtio.c:85）
       │
virtio_dev_probe()                   virtio.c:237   §A4
  ① 状态位 += DRIVER                            :247
  ② device_features = get_features()            :250
  ③ 组出 driver_features
  ④ dev->features = 交集                        :273 / :275
  ⑤ 传输层 feature 无条件保留                    :277
  ⑥ finalize_features()  ← 第一次           :282
  ⑦ drv->validate()                        :286 / :289
  ⑧ 改了就再 finalize_features()             :295
  ⑨ virtio_features_ok()                       :301   §A5
       └─ 状态位 += FEATURES_OK，回读确认      :194-198
  ⑩ drv->probe()                            :305
       └─ 驱动在里面 virtio_find_vqs()                   §A6
  ⑪ 状态位 += DRIVER_OK（virtio_device_ready）  :310-311  §A7
  ⑫ drv->scan()                                :313-314
  ✗ 失败 ⇒ 状态位 += FAILED                      :320
       │
    稳态运行                                            §A8
       │
virtio_dev_remove()                  virtio.c:325  §A9
  ├─ virtio_config_disable(dev)
  ├─ drv->remove(dev)
  ├─ ⚠️ WARN_ON_ONCE(get_status(dev))  ← 驱动必须自己复位过
  ├─ 状态位 += ACKNOWLEDGE（重新确认存在）
  └─ of_node_put()
```

### §0.3 哪些内容不在本篇

| 你想找 | 去哪 |
|---|---|
| 某个 API 怎么调 | 01 篇 |
| vring 的字节级布局 | **02 §5** |
| 结构体字段 | 02 §6 |
| 六组容易混的概念 | 02 §1.0 |
| 具体设备驱动（balloon / mem / input） | 04 篇 |
| 驱动代码骨架 | 05 篇 |
| DT / 发现路径 | 06 篇 |
| 怎么观测 | 07 篇 |
| 大白话解释 | 08 篇 |
| 哪些结论没验证过 | **99 §5**（含第五层「对端不在本树」） |

---

# Part A：一个设备从出现到消失

## §A1 传输层探测到设备

**这一步完全在传输层里，核心不参与。** 三种典型形态（06 篇展开）：

| 传输 | 怎么发现 |
|---|---|
| PCI | 普通 PCI probe（`vendor 0x1af4`） |
| MMIO | **DT 节点**（`virtio,mmio`）或 **内核命令行**（`CONFIG_VIRTIO_MMIO_CMDLINE_DEVICES`） |
| remoteproc | **远端固件的资源表里声明** |

**传输层要做的事**：填好 `vdev->id`（设备类型）和 `vdev->config`
（指向自己那份 `virtio_config_ops`），然后调 `register_virtio_device()`。

## §A2 `register_virtio_device()`（`drivers/virtio/virtio.c:418`）

按顺序做六件事：

| 步 | 代码 | 说明 |
|---|---|---|
| ① | `dev->dev.bus = &virtio_bus` + `device_initialize()` | 挂到总线 |
| ② | `ida_alloc(&virtio_index_ida, ...)` → `dev_set_name(&dev->dev, "virtio%u", ...)` | **设备名就是 `virtio0` / `virtio1` …** |
| ③ | `virtio_device_of_init(dev)`（`drivers/virtio/virtio.c:366`） | **按 DT 关联子节点**（§A2.1） |
| ④ | `spin_lock_init(&dev->config_lock)` + `config_enabled = false` + `config_change_pending = false` | **配置变更先关掉** |
| ⑤ | `INIT_LIST_HEAD(&dev->vqs)` + `spin_lock_init(&dev->vqs_list_lock)` | 队列链表 |
| ⑥ | `virtio_reset_device(dev)` | **先复位** |
| ⑦ | `virtio_add_status(dev, VIRTIO_CONFIG_S_ACKNOWLEDGE)` | **"我看到你了"** |
| ⑧ | `device_add(&dev->dev)` | **触发匹配和 probe** |

**第 ⑥ 步的注释值得逐字读**：

> We always start by resetting the device, in case a previous
> driver messed it up.  This also tests that code path a little.

⇒ **两个理由**：① 前一个驱动可能留下烂摊子；
② **顺便让 reset 这条路径每次启动都被走一遍**（自测）。
这是很务实的工程判断——**reset 路径平时没人走，容易腐坏**。

⚠️ **失败路径也加状态位**：任何一步失败都会
`virtio_add_status(dev, VIRTIO_CONFIG_S_FAILED)`（`drivers/virtio/virtio.c:468`）
⇒ **对端能通过状态字节知道"驱动侧没起来"**。

### §A2.1 `virtio_device_of_init()`：一个容易忽略的 DT 关联

`drivers/virtio/virtio.c:366` 起。它做的事很特别：

```c
	char compat[] = "virtio,deviceXXXXXXXX";
```
—— `drivers/virtio/virtio.c:369`

**`XXXXXXXX` 是 `snprintf` 的占位**，真正的格式串在
`drivers/virtio/virtio.c:388`：

```c
	ret = snprintf(compat, sizeof(compat), "virtio,device%x", dev->id.device);
	BUG_ON(ret >= sizeof(compat));
```

⚠️ **是 `%x`——十六进制，不是十进制。**
举例：`VIRTIO_ID_I2C_ADAPTER` = **34**（`include/uapi/linux/virtio_ids.h:63`）
⇒ **DT 里要写 `compatible = "virtio,device22"`**（0x22 = 34）。
**绑定文档的例子用的正是 `virtio,device22`**
（`Documentation/devicetree/bindings/virtio/virtio-device.yaml` 的 `examples` 段）。

**完整逻辑**（`drivers/virtio/virtio.c:366-400`）：

| 步 | 代码 | 说明 |
|---|---|---|
| ① | `pnode = dev_of_node(dev->dev.parent)` | **看传输层设备有没有 DT 节点**；没有就直接返回 0 |
| ② | `count = of_get_available_child_count(pnode)` | 数子节点 |
| ③ | ⚠️`if (WARN_ON(count > 1)) return -EINVAL;` | **只允许一个子节点** ——注释就一句 `There can be only 1 child node` |
| ④ | `np = of_get_next_available_child(pnode, NULL)` | 取那唯一一个 |
| ⑤ | `snprintf(..., "virtio,device%x", dev->id.device)` | **十六进制** |
| ⑥ | `if (!of_device_is_compatible(np, compat)) { ret = 0; goto out; }` | **不匹配不是错误**，只是不关联 |

⇒ **这让 DT 能给某个 virtio 设备补充平台侧信息**（比如子设备的额外属性），
而设备本身是 hypervisor 提供的。06 篇会展开怎么写。

**代码里还有一条注释解释为什么"不匹配不算错"**（`drivers/virtio/virtio.c:392-395`）：
powerpc/pseries 上 virtio 设备是 PCI 设备，PCI 的 vendor/device id 起了
`compatible` 的作用 ⇒ 那种情况下不该初始化 `of_node`。

⚠️ **这个 `XXXXXXXX` 会让 `grep XXX` 误判成设计债** ——
99 §6 记了这条计数陷阱。

## §A3 匹配：`virtio_dev_match()`（`drivers/virtio/virtio.c:85`）

**按 `struct virtio_device_id` 的两个字段匹配**：`device`（设备类型）和 `vendor`。
驱动的 `id_table` 里通常写 `{ VIRTIO_ID_NET, VIRTIO_DEV_ANY_ID }`
—— **只认类型，不挑厂商**。

⇒ **这是 virtio 一个刻意的简化**：设备类型编号由规范统一分配
（`include/uapi/linux/virtio_ids.h`），所以**同一个驱动能驱动任何厂商的
同类 virtio 设备**——这正是 virtio 存在的意义。

## §A4 `virtio_dev_probe()` 的 12 步

01 §4.3 已给出带行号的完整序列。这里只讲**三个容易看错的地方**：

### §A4.1 ⚠️`finalize_features()` 被调了两次

| 次 | 行 | 条件 |
|---|---|---|
| 第一次 | `drivers/virtio/virtio.c:282` | **无条件** |
| 第二次 | `drivers/virtio/virtio.c:295` | **只在 `validate()` 改了 `dev->features` 时** |

**代码显式比较**：`validate()` 前先存一份 `u64 features = dev->features`
（`:287`），回来比 `features != dev->features`（`:294`）。

⇒ **所以 `validate()` 是唯一"改了还会被写回设备"的钩子**（01 §4.3 的结论）。

### §A4.2 legacy 路径用的是**另一张** feature 表

```
  if (device_features & (1ULL << VIRTIO_F_VERSION_1))
      dev->features = driver_features & device_features;          :273
  else
      dev->features = driver_features_legacy & device_features;   :275
```

⇒ **设备提供 `VERSION_1` 就走现代路径，否则走 legacy 路径**，
两条路径**用驱动的两张不同的 feature 表**（01 §4.2）。
⚠️ **驱动只填了 `feature_table` 没填 `feature_table_legacy` 时**，
`drivers/virtio/virtio.c:269` 会把 legacy 表**回退为主表**
（`driver_features_legacy = driver_features`）
⇒ **所以不填第二张表是安全的默认行为，不是 bug。**

### §A4.3 传输层的 feature 位不参与取交集

```c
	/* Transport features always preserved to pass to finalize_features. */
```
—— `drivers/virtio/virtio.c:277`

⇒ **`VIRTIO_F_VERSION_1` / `VIRTIO_F_ACCESS_PLATFORM` 这类位
由核心直接保留**，不要求驱动在 `feature_table` 里列出来。
**这解释了为什么驱动的 feature 表里从来看不到它们。**

## §A5 `virtio_features_ok()`：写-回读握手

01 §4.3.1 已逐条列出。**这里补一张状态机图**，
因为它是本子系统"对端能说不"的唯一体现：

```
  驱动                              共享的状态字节            对端
  ────────────────────────────────────────────────────────────────
  add_status(FEATURES_OK)  ──►  [.. FEATURES_OK ..]  ──►  读到
       virtio.c:194                                       │
                                                          │ 检查
                                                          ▼
                                                     接受？
                              ┌───────────────────────┴──────┐
                              │ 接受                    不接受 │
                              ▼                              ▼
                        位保持置上                  把位清掉
                              │                              │
  get_status() ◄──────────────┴──────────────────────────────┘
    virtio.c:195
       │
       ├─ 位还在 ⇒ return 0        协商成功
       └─ 位没了 ⇒ dev_err("virtio: device refuses features: %x")
                    virtio.c:197-198
                  return -ENODEV
```

⚠️ **legacy 设备完全跳过这个握手**：
`drivers/virtio/virtio.c:191` 的
`if (!virtio_has_feature(dev, VIRTIO_F_VERSION_1)) return 0;`
⇒ **0.9.X 设备没有 `FEATURES_OK` 这个位**，所以只能"盲信"。

## §A6 建队列：`find_vqs` 到 vring

**驱动在 `probe()` 里调 `virtio_find_vqs()`**，核心转给传输层的
`config->find_vqs()`，传输层再回头调 `virtio_ring.c` 的创建函数：

```
  drv->probe()
    └─ virtio_find_vqs(vdev, nvqs, vqs, callbacks, names, desc)
         └─ vdev->config->find_vqs(...)        ← 传输层的实现
              │  （PCI 的还要分配 MSI-X 向量、建中断）
              └─ vring_create_virtqueue(...)   drivers/virtio/virtio_ring.c:2546
                   ├─ packed?
                   │   ├─ 是 ⇒ vring_create_virtqueue_packed()   :1996
                   │   └─ 否 ⇒ vring_create_virtqueue_split()    :1101
                   │
                   ├─ 分配共享内存（大小 = vring_size()，02 §5.3）
                   ├─ vring_init() 算出三段偏移（02 §5.3）
                   ├─ 建 desc_state / desc_extra（驱动侧私有状态，02 §6.4）
                   ├─ free_head 串成空闲链
                   └─ 挂进 vdev->vqs（持 vqs_list_lock）
```

**两个关键决定在这一步定下来，之后不变**：

| 决定 | 字段 | 依据 |
|---|---|---|
| split 还是 packed | `packed_ring`（`drivers/virtio/virtio_ring.c:158`） | **协商结果**（`VIRTIO_F_RING_PACKED`） |
| 要不要走 DMA API | `use_dma_api`（`:161`） | `vring_use_dma_api()`（`:268`），见 §B2 |
| 屏障强度 | `weak_barriers`（`:164`） | **传输层传进来的参数**，见 §B1 |

## §A7 状态字节的完整序列

**六个位，`include/uapi/linux/virtio_config.h:36-46`**：

| 位 | 值 | 位置 | 谁设 | 什么时候 |
|---|---|---|---|---|
| `VIRTIO_CONFIG_S_ACKNOWLEDGE` | **1** | `:36` | **核心** | `register_virtio_device()` 里（§A2 第 ⑦ 步）；**remove 后还会再设一次** |
| `VIRTIO_CONFIG_S_DRIVER` | **2** | `:38` | 核心 | `virtio_dev_probe()` 开头（`:247`） |
| `VIRTIO_CONFIG_S_DRIVER_OK` | **4** | `:40` | **驱动或核心** | `virtio_device_ready()`（`:310-311`） |
| `VIRTIO_CONFIG_S_FEATURES_OK` | **8** | `:42` | 核心 | `virtio_features_ok()`（`:194`），**要回读确认** |
| `VIRTIO_CONFIG_S_NEEDS_RESET` | **0x40** | `:44` | ⚠️ **对端** | **设备说"我坏了，重置我"** |
| `VIRTIO_CONFIG_S_FAILED` | **0x80** | `:46` | 核心/驱动 | 任何失败路径（`:320`） |

**正常启动的位累积过程**：

```
  0x00  复位后
  0x01  += ACKNOWLEDGE     register_virtio_device()
  0x03  += DRIVER          virtio_dev_probe() 开头
  0x0b  += FEATURES_OK     virtio_features_ok()（要回读）
  0x0f  += DRIVER_OK       virtio_device_ready()
```

⚠️ **`NEEDS_RESET`(0x40) 是唯一一个由对端设置的位** ——
**它是"设备主动报告自己出问题"的唯一通道**。
⇒ 驱动需要在 `config_changed()` 回调里检查它（07 篇讲怎么观测）。

⚠️ **`virtio_device_ready()` 忘了调的后果**：`DRIVER_OK` 不置上，
**规范规定对端在 `DRIVER_OK` 之前不得使用队列** ⇒ 数据永远不动。
**但核心有兜底**：`:310` 会检查驱动有没有自己设，没设就代设
⇒ **所以"忘了调"在 6.1 上不致命**——这是一个后来加的保护。

## §A8 稳态运行：三条独立的路

| 路 | 入口 | 频率 |
|---|---|---|
| **数据面** | `virtqueue_add_*()` → `kick()` → 回调 → `get_buf()` | **极高，不经传输层的控制路径** |
| **配置变更** | 对端通知 → 传输层 → `virtio_config_changed()`（`drivers/virtio/virtio.c:136`）→ `drv->config_changed()` | 少 |
| **电源管理** | `virtio_device_freeze()`（`:489`）/ `virtio_device_restore()`（`:510`） | 极少 |

**配置变更有一个延迟机制**（`__virtio_config_changed()`，
`drivers/virtio/virtio.c:126`）：
`config_enabled` 为假时只置 `config_change_pending`，**不调驱动的回调**。
⇒ **probe 期间来的配置变更被攒着**，等 `virtio_config_enable()`（`drivers/virtio/virtio.c:153`）之后一次放出——
**它在 `virtio_dev_probe()` 的最末尾被调**（`drivers/virtio/virtio.c:316`，在 `drv->scan()` 之后）。
这解决的是"probe 还没完成就收到变更通知"的竞态。

## §A9 ⚠️`virtio_dev_remove()`：驱动必须自己复位

`drivers/virtio/virtio.c:325-341`，四步：

```c
	virtio_config_disable(dev);          /* 先关掉配置变更通知 */

	drv->remove(dev);

	/* Driver should have reset device. */
	WARN_ON_ONCE(dev->config->get_status(dev));   /* ⚠️ */

	/* Acknowledge the device's existence again. */
	virtio_add_status(dev, VIRTIO_CONFIG_S_ACKNOWLEDGE);

	of_node_put(dev->dev.of_node);
```

⚠️ **那句 `WARN_ON_ONCE(dev->config->get_status(dev))` 是本篇最该记住的一条约束**：

**它要求 `remove()` 返回时状态字节必须是 0**，
也就是 **驱动的 `remove()` 里必须调 `virtio_reset_device()`**。
**注释就一句话**：`Driver should have reset device.`

⇒ **忘了复位不会崩，但会 `WARN_ON_ONCE`**（05 §1 的骨架里有这一步）。

**第四步"重新加 ACKNOWLEDGE"很有意思**：设备并没有消失，
只是没驱动了 ⇒ **状态回到"我看到你了但还没驱动"**，
和 `register_virtio_device()` 之后的状态一致 ⇒ **可以被重新 probe**。

**`virtio_reset_device()` 的 kerneldoc 值得逐字读**
（`drivers/virtio/virtio.c:204-218`）：它明确说了三件事——
① 阻止设备发中断和访问内存；② **调用者必须保证之后没有
`virtqueue_notify`/`virtqueue_kick` 在进行中**；
③ **它保证 vq 回调不在进行中，但"其它上下文（系统调用/工作队列/bh）"要调用者自己拦**，
建议的做法是先 `virtio_break_device()` 再 flush 那些上下文。

---

# Part B：横切机制

## §B1 内存屏障：四个原语，强度可切

**这是跨边界共享内存特有的一节** ——本地硬件驱动只需要考虑
"CPU 和设备"，virtio 要考虑"CPU 和另一个 CPU 上的软件"。

### §B1.1 四个原语

| 原语 | 位置 | 用在哪 |
|---|---|---|
| `virtio_wmb(weak_barriers)` | `include/linux/virtio_ring.h:41` | **写 `avail->idx` 之前**（`drivers/virtio/virtio_ring.c:659`、`:1308`、`:1473`） |
| `virtio_rmb(weak_barriers)` | `include/linux/virtio_ring.h:33` | **读 `used->idx` 之后**（`drivers/virtio/virtio_ring.c:810`） |
| `virtio_mb(weak_barriers)` | `include/linux/virtio_ring.h:25` | **全屏障**（`drivers/virtio/virtio_ring.c:709`、`:1522`） |
| `virtio_store_mb(weak_barriers, p, v)` | `include/linux/virtio_ring.h:49` | **写 + 屏障合成一步**（`drivers/virtio/virtio_ring.c:835`、`:924`） |

**`virtio_store_mb()` 是个宏而不是函数**——因为它要把"写哪个变量"
和"写什么值"都吃进去，才能在支持的架构上合成一条指令。

### §B1.2 `weak_barriers`：强度由传输层决定

**`vring_virtqueue.weak_barriers`**（`drivers/virtio/virtio_ring.c:164`，
注释 `Can we use weak barriers?`）**决定用哪一套**：

| `weak_barriers` | 用的屏障 | 什么时候 |
|---|---|---|
| **true** | **`virt_*mb()`**（编译器屏障 + 轻量） | **对端是同一台机器上的软件**（hypervisor / 另一个进程） |
| **false** | **`*mb()`**（真正的 CPU 屏障） | **对端可能是真硬件**（vDPA / 硬件卸载） |

⇒ **这个字段的存在本身就证明"对端是什么"会改变 Linux 侧的代码路径**
（02 §3.1 提过）。
**它由传输层在创建 virtqueue 时传进来**，不是协商出来的。

⚠️ **为什么同机器可以用弱屏障**：因为双方都在同一个缓存一致性域里，
只需要防止**编译器和 CPU 的重排**，不需要真的把写缓冲刷到内存。
**对硬件就不行** ——硬件可能通过不一致的路径看内存。

## §B2 DMA：`vring_use_dma_api()` 的判据

**一个反直觉的事实：virtio 有时候不用 DMA API，直接用物理地址。**

`vring_use_dma_api()`（`drivers/virtio/virtio_ring.c:268`）的判据：

| 判据 | 位置 | 结论 |
|---|---|---|
| 协商上了 `VIRTIO_F_ACCESS_PLATFORM` | 通过 `virtio_has_dma_quirk()`（`include/linux/virtio_config.h:198`） | **用 DMA API** |
| `xen_domain()` | `drivers/virtio/virtio_ring.c:282` | **必须用 DMA API**（Xen 下 guest 物理地址 ≠ 总线地址） |
| 都不满足 | — | **不用 DMA API，直接用 `virt_to_phys()`** |

**为什么可以不用**：传统 virtio 的假设是
"**hypervisor 能直接访问 guest 的物理内存**"，所以描述符里放 guest 物理地址就行。
⇒ **`VIRTIO_F_ACCESS_PLATFORM` 这个 feature 位就是在说
"不，我要走平台的 DMA 规则"** ——IOMMU、SWIOTLB、机密计算场景都需要它。

⚠️ **`virtio_has_dma_quirk()` 的命名是反的**：
它返回**真**表示"**有 quirk**（不走 DMA API 的老行为）"，
实现是 `!virtio_has_feature(vdev, VIRTIO_F_ACCESS_PLATFORM)`
（`include/linux/virtio_config.h:204`）。
⇒ **"quirk"指的是老行为，不是新行为** ——读代码时容易看反。

**代码里的设计债**：`drivers/virtio/virtio_ring.c:263` 有一条
`TODO: install a per-device DMA ops structure that does the right thing`
⇒ **作者自己也觉得"到处 `if (use_dma_api)`"不够干净**（§C1）。

⚠️ **`use_dma_api` 在数据面被检查很多次**：
`drivers/virtio/virtio_ring.c:356` · `:380` · `:390` · `:424` · `:442` · `:617` 都有
`if (!vq->use_dma_api)` 之类的分支 ⇒ **这就是那条 TODO 想消灭的东西。**

## §B3 不信任对端：三层防御

**这是本子系统区别于所有本地子系统的核心特征**：
**共享内存里的任何值都可能是恶意或错误的。** 三层防御：

| 层 | 机制 | 代码 |
|---|---|---|
| ① **影子字段** | **"我上次写了什么"另存一份，不读共享内存** | `avail_flags_shadow`（`drivers/virtio/virtio_ring.c:94`）· `avail_idx_shadow`（`:100`） |
| ② **token 机制** | **对端只能说"第 N 项完成了"，不能告诉你"这是哪个对象"** | `desc_state` / `desc_extra`（`:103-104`），02 §3.2 |
| ③ **id 校验** | **`used_elem.id` 越界就标 broken** | `get_buf` 路径 |

**为什么影子字段是必要的**：假如驱动要判断"我之前有没有关过中断"，
读共享内存里的 `avail->flags` ⇒ **对端可以改它**，
驱动就会得出错误结论。读影子就安全。

**`avail_idx_shadow` 的注释特别点明 `in guest byte order`**
（`drivers/virtio/virtio_ring.c:97-99`）——共享内存里那份是 `__virtio16`
（端序随协商），影子是宿主端序的 `u16`
⇒ **省掉了每次读都要做一次字节序转换。**

## §B4 通知抑制：两套机制并存

**virtio 性能的关键是"少通知"**，为此有两套机制：

| 机制 | 谁用 | 怎么表达 | 精度 |
|---|---|---|---|
| **flag 式**（老） | 双方 | `VRING_USED_F_NO_NOTIFY` / `VRING_AVAIL_F_NO_INTERRUPT`（02 §5.4） | **全有全无** |
| **EVENT_IDX 式**（新） | 双方 | **`used_event_idx` / `avail_event_idx`**（02 §5.1 那两个插在对方段末尾的字段） | **"到第 N 项再通知我"** |

**用哪套由 feature 位决定**：`vring_virtqueue.event`
（`drivers/virtio/virtio_ring.c:173`，注释 `Host publishes avail event idx`）
为真时用 EVENT_IDX。

**EVENT_IDX 好在哪**：flag 式只能说"别通知我"，
然后驱动必须**主动轮询**才知道有没有新数据；
EVENT_IDX 能说"**攒到第 N 项再通知我**" ⇒
**一次通知处理一批**，这就是 `virtqueue_enable_cb_delayed()`（01 §3.4）的底层。

⚠️ **`drivers/virtio/virtio_ring.c:572` 有一条 FIXME 和这个机制有关**：
`for historical reasons, we force a notify here if ...`
⇒ **有一处历史包袱导致必须强制通知**，属于已知的性能欠债（§C1）。

## §B5 broken 状态：一条不可逆的路（几乎）

| 触发 | 后果 |
|---|---|
| `virtio_break_device(vdev)`（01 §7） | **该设备所有队列的 `broken` 置真** |
| 对端写了非法的 `used_elem.id` | 核心自己标 broken |
| 热插拔移除 | 传输层调 `virtio_break_device()` |

**broken 之后**：`virtqueue_kick()` 返回 false、`get_buf()` 返回 NULL、
`virtqueue_is_broken()` 返回真。
⇒ **驱动的清理路径必须能在"什么都不动"的情况下走完**。

`struct vring_virtqueue.broken` 的注释是
`Other side has made a mess, don't try any more`（`drivers/virtio/virtio_ring.c:166-167`）
⇒ **措辞很直白：对端搞砸了，别再试了。**

**有一个取消的接口**：`__virtio_unbreak_device()`（`include/linux/virtio.h:141`），
带 `__` 前缀 ⇒ **内部用，主要给 restore 路径**。

## §B6 队列 resize / reset：6.1 才有的能力

`virtqueue_resize()`（`drivers/virtio/virtio_ring.c:2594`）
配合两个 `config_ops` 回调（01 §6.1）：

| 回调 | 位置 |
|---|---|
| `disable_vq_and_reset` | `include/linux/virtio_config.h:119` |
| `enable_vq_after_reset` | `include/linux/virtio_config.h:120` |

**`struct virtqueue.reset` 字段**（`include/linux/virtio.h:38`）标记
"正在 reset"。

⚠️ **不是所有传输层都支持** ——那两个回调是可选的，
不实现就 `virtqueue_resize()` 失败。
⇒ **驱动必须处理"resize 不支持"的情况**（05 篇）。

## §B7 ⚠️ 锁的缺席：框架不加，驱动负责

02 §7.2 已讲机制。**这里补一条实际后果**：

**`struct vring_virtqueue` 在 `#ifdef DEBUG` 下有 `in_use` 计数器**
（`drivers/virtio/virtio_ring.c:205-207`），注释是
`They're supposed to lock for us.`

⇒ **"They" 是驱动。** DEBUG 模式下每个 `virtqueue_*` 入口会
检查/递增 `in_use`，发现重入就报。
**这是本子系统唯一的并发违规检测手段**（07 篇会讲怎么开）。

⚠️ **`last_add_time_valid` / `last_add_time`**（`:209-211`）也是 DEBUG 专属：
注释 `Figure out if their kicks are too delayed`
⇒ **它在测"驱动 add 完之后多久才 kick"** ——
提交了不 kick 是另一类常见 bug。

---

# Part C：设计评价

## §C1 源码自陈的设计债：13 条

```bash
CORE="<drivers/virtio 下 14 个 .c，见 99 §3.3>"
grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' $CORE | grep -vE '"[^"]*XXX'
```

⚠️ **必须排掉字符串里的 `XXX`** ——`drivers/virtio/virtio.c:369` 的
`char compat[] = "virtio,deviceXXXXXXXX"` 是 `sprintf` 占位符（§A2.1），
不排掉会数成 14（99 §6 记了这条）。

**13 条的分布**：

| 文件 | 条数 |
|---|---|
| `drivers/virtio/virtio_ring.c` | **8** |
| `drivers/virtio/virtio_mem.c` | **5** |
| **`drivers/virtio/virtio.c`（总线核心，577 行）** | **0** |
| 其余 11 个文件 | **0** |

**`virtio_ring.c` 那 8 条**：

| 位置 | 内容 | 性质 |
|---|---|---|
| `:199` | `How to notify other side. FIXME: commonalize hcalls!` | **抽象欠债**：每个传输层各自实现通知 |
| `:237` | `buffers, then go indirect. FIXME: tune this threshold` | **魔法阈值** |
| `:263` | `TODO: install a per-device DMA ops structure that does the right thing` | **§B2 那一堆 `if (use_dma_api)` 的根源** |
| `:572` | `FIXME: for historical reasons, we force a notify here if ...` | **历史包袱导致的多余通知**（§B4） |
| `:860` | `TODO: this is a hack. Figure out a cleaner value to write.` | **自称 hack** |
| `:921` · `:1770` | `TODO: tune this threshold` ×2 | 又两个魔法阈值 |
| `:1068` | `TODO: allocate each queue chunk individually` | 内存分配粒度 |

**三条明确的结论**：

1. **总线核心（`virtio.c`）零自陈债** —— 和 of（全目录 1 条）、
   timer（核心 0 条、债全在 posix 层）是同一类现象：
   **越靠近"编排"的代码越干净，越靠近"数据面/具体设备"的越脏**
2. **`virtio_ring.c` 有 4 个"tune this threshold"类的魔法阈值**
   （`:237` · `:921` · `:1770` 加 `:860` 的 hack）
   ⇒ **数据面的性能参数是调出来的，不是推导出来的**
3. **`:199` 的 `commonalize hcalls` 是最有分量的一条**：
   它说明"通知对端"这件事**本该有一个统一抽象但没有** ——
   9 个传输层各写一遍（01 §1.1）

## §C2 一大类 API 不返回错误

| 类别 | 例子 | 参数错了会怎样 |
|---|---|---|
| 通知控制 | `virtqueue_disable_cb()` | `void` |
| 状态操作 | `virtio_add_status()` · `virtio_break_device()` · `virtio_device_ready()` | **全是 `void`** |
| 复位 | `virtio_reset_device()` | `void` |
| ⚠️ 查了未声明的 feature 位 | `virtio_has_feature()` | **不返回错误，直接 `BUG`**（01 §4.1） |

⚠️ **`virtio_has_feature()` 的 `BUG` 是个有争议的选择**：
它把"驱动作者忘了改 `feature_table`"从一个安静的逻辑错误
变成了一次**内核崩溃**。
**正面看**：这类错误在测试期就会暴露，不会带到生产；
⚠️ **负面看**：一个只在罕见 feature 组合下走到的分支，可能在生产里第一次崩。

## §C3 两层可插拔的代价

**9 个传输层 × 25 个设备驱动 = 理论上 225 种组合**（02 §1.0.F），
而且**每种都要能工作**。**好处很明显**（一个 virtio_net 驱动能跑在
PCI / MMIO / CCW / vDPA / UML 上），⚠️ **代价有三条**：

| 代价 | 表现 |
|---|---|
| **可选回调多** | `virtio_config_ops` 17 个回调里**只有 8 个必填**（01 §6.1）⇒ 驱动要处理"这个传输层不支持某功能" |
| **能力探测散在各处** | `use_dma_api` / `weak_barriers` / `event` / `indirect` / `packed_ring` 五个字段都是"环境探测结果"（§A6、02 §6.3） |
| **测试矩阵大** | **本套无法验证任何一种组合**（99 §5.1）—— 没编译没运行 |

## §C4 可观测性：比 timer 差，比 of 好

| 手段 | 有没有 | 说明 |
|---|---|---|
| tracepoint | **没有专用的** | ⇒ 07 篇会给替代手段 |
| sysfs | **有**（`/sys/bus/virtio/devices/virtioN/`） | **`features` / `status` / `device` / `vendor` 可读** |
| DEBUG 模式的并发检测 | **有**（§B7） | **需要重新编译** |
| 队列级别的观测 | ⚠️ **几乎没有** | **`virtqueue` 不在驱动模型里**（02 §1.0.B）⇒ sysfs 看不到 |
| 中断计数 | 间接 | `/proc/interrupts` 里能看到 vq 的名字 |

⚠️ **最要紧的数据面（vring）几乎不可观测** ——
这和 timer 的"最要紧的 timekeeping 最不可观测"是同一类问题。
⇒ **性能问题只能靠对端（QEMU/vhost）侧的工具看**，
而那不在本树里（99 §5.5）。

## §C5 legacy 包袱：三处

| 包袱 | 表现 |
|---|---|
| **两张 feature 表** | `feature_table` + `feature_table_legacy`（01 §4.2、§A4.2） |
| ⚠️ **字节序不确定** | legacy 设备用宿主端序，现代设备用小端（01 §4.4）⇒ **`__virtio16` 这类类型必须用 `virtio16_to_cpu()` 转** |
| **两个 PCI 库** | `virtio_pci_modern_dev.c`(711) + `virtio_pci_legacy_dev.c`(222)，`CONFIG_VIRTIO_PCI_LEGACY` 还是 `default y`（01 §0.6） |

⇒ **legacy 支持的成本大约是 1000 行代码 + 一套额外的字节序规则。**
**规范早已强制小端**（`VERSION_1`），但内核为了兼容 2014 年前的
hypervisor 保留了这条路。

## §C6 整体评价

**做得好的四点**：

1. **数据面完全无全局状态**（02 §7.1：`virtio_ring.c` 2876 行零全局变量）
   ⇒ 天然支持多设备多队列并行
2. **"不信任对端"贯彻得很彻底**（§B3 的三层防御）——
   影子字段、token 机制、id 校验，每一层都有明确的攻击面对应
3. **写-回读握手**（§A5）让"对端能拒绝"成为协议的一部分，
   而不是靠约定
4. **`register_virtio_device()` 里那句"先复位，顺便测一下这条路径"**（§A2）
   是很务实的工程判断

⚠️ **代价和欠债四点**：

1. **通知这件事没有统一抽象**（§C1 的 `:199`）——9 个传输层各写一遍
2. **数据面有 4 个"调出来的"魔法阈值**（§C1）
3. **锁完全由驱动负责**（§B7）——省了性能，但把并发正确性推给了 25 个驱动
4. ⚠️ **最关键的数据面几乎不可观测**（§C4）

**本子系统最特殊的一点**（和 of / timer 的根本差别）：
**它的"另一半"不在本树里。** 所有"设备应该怎么响应"的问题，
本树给不出答案 ⇒ **99 §5.5 专门为此设了第五层未验证项。**
