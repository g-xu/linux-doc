---
title: API 参考
---

# virtio 01：代码地图与对外 API

本篇回答两件事：**代码在哪里**（§0）和**我该调哪个函数**（§1 之后）。

格式约定：
- 表格固定三列 `API / 说明 / 备注`；**签名只写函数名 + 形参名**，类型信息进「备注」
- **位置只精确到文件**，不逐 API 给行号（行号在 03 和 99 里维护）
- 标记重要度，⚠️ 标记陷阱

---

## §0 代码地图

### §0.1 先立边界：virtio 是一个**跨边界 ABI**，不是一条硬件总线

**这一条决定了整套文档怎么读。** virtio 有 `struct bus_type`
（`drivers/virtio/virtio.c:343`），看起来像 PCI/I2C 那样的硬件总线，
**但它没有电气层**——总线的另一端是**另一个软件实体**（hypervisor / 远端固件 / 用户态）。

**三个后果**：

| 后果 | 说明 |
|---|---|
| **协议由树外规范定义** | **OASIS VIRTIO 规范**。⚠️ **本树没有 `Documentation/virtio/`**（99 §1.4 三种变体全试过），所以"设备该怎么响应"这类问题**在本树里找不到答案** |
| **ABI 落在 `include/uapi/linux/`** | **27 个 `virtio_*.h`** —— 这些是**规范的 C 表达**，不是 Linux 的内部接口 |
| **字节序要显式处理** | 共享内存里的字段有自己的字节序约定，不能直接用宿主字节序读（§4.4） |

⚠️ **本套文档的覆盖边界**（99 §5 有第五层专门登记）：
**我们只覆盖 Linux 这一侧（driver 侧）。**
对端（device 侧，通常是 QEMU/vhost/硬件卸载）**不在本树里**，
凡是涉及"对端会怎么做"的论断，依据都是**规范文本或代码里的注释**，
**不是本树的代码**。

### §0.2 基线：树是干净的

```bash
git status --porcelain -- drivers/virtio include/linux/virtio*.h \
    include/uapi/linux/virtio_*.h include/linux/vringh.h
# → 无输出
```

**和 timer 那套不同**（timer 有 3 处 PREEMPT_RT 改动）——
**virtio 在这棵树里是未经改动的 6.1.158 上游**。
⇒ **所以本套不需要像 timer 那样处处标注"本树改动点"。**

### §0.3 文件与职责

`drivers/virtio/` 共 **15 个文件、11854 行**（逐文件行数见 99 §1.2）。按职责分四组：

| 组 | 文件 | 职责 |
|---|---|---|
| **核心** | `virtio.c`(577) | **总线、设备/驱动注册、feature 协商、probe 编排** |
| **数据通路** | `virtio_ring.c`(2876) | **vring 的全部实现**（split + packed 两种布局）、`virtqueue_*` 全套 |
| **传输层** | `virtio_pci_common.c`(654) · `virtio_pci_modern.c`(556) · `virtio_pci_modern_dev.c`(711) · `virtio_pci_legacy.c`(235) · `virtio_pci_legacy_dev.c`(222) · `virtio_pci_common.h`(141) · `virtio_mmio.c`(860) · `virtio_vdpa.c`(417) | **把抽象操作翻译成具体的 PCI / MMIO / vDPA 访问** |
| **设备驱动**（住在本目录的） | `virtio_balloon.c`(1132) · `virtio_mem.c`(2952) · `virtio_input.c`(414) | **只是恰好住在这里**，和住在 `drivers/net/` 的 `virtio_net` 地位相同 |
| **辅助** | `virtio_dma_buf.c`(89) · `virtio_anchor.c`(18) | dma-buf 集成 / DMA 限制锚点 |

⚠️ **两个"最大的文件"都不是核心**：`virtio_mem.c`(2952) 和
`virtio_balloon.c`(1132) 是**设备驱动**，不是框架。
**真正的框架是 `virtio.c`(577) + `virtio_ring.c`(2876)**，合计 3453 行。
⇒ **读代码时别被文件大小带偏。**

### §0.4 导出符号的分布很说明问题

| 文件 | `EXPORT_SYMBOL*` | 说明 |
|---|---|---|
| `virtio_ring.c` | **32** | **`virtqueue_*` 全套** ——数据通路是最大的对外面 |
| `virtio_pci_modern_dev.c` | 19 | **是个库**（`CONFIG_VIRTIO_PCI_LIB`），给 vDPA 等复用 |
| `virtio_pci_legacy_dev.c` | 12 | 同上（`CONFIG_VIRTIO_PCI_LIB_LEGACY`） |
| `virtio.c` | **11** | 总线与注册 |
| `virtio_dma_buf.c` · `virtio_anchor.c` | 4 · 2 | — |
| `virtio_pci_common.c` · `virtio_pci_modern.c` · `virtio_pci_legacy.c` · `virtio_mmio.c` · `virtio_vdpa.c` · `virtio_balloon.c` · `virtio_mem.c` · `virtio_input.c` | **全部 0** | **它们是叶子**：传输层驱动和设备驱动都不对外提供 API |
| **合计** | **80** | `grep -hE '^EXPORT_SYMBOL' drivers/virtio/*.c \| wc -l` |

**一眼能看出的分层**：
`virtio.c` + `virtio_ring.c` = **43 个导出符号**，是框架；
两个 `*_dev.c` = **31 个**，是给传输层复用的库；
其余全是 0，是叶子。

### §0.5 头文件

| 头文件 | 行数 | 给谁用 |
|---|---|---|
| `include/linux/virtio_config.h` | **623** | **最大的一个**：`virtio_config_ops`、feature 辅助、配置空间访问器 |
| `include/linux/virtio.h` | 209 | **`virtio_device` / `virtqueue` / `virtio_driver` 三个核心结构 + `virtqueue_*` 声明** |
| `include/linux/virtio_ring.h` | 104 | vring 的内核侧辅助 |
| `include/linux/virtio_byteorder.h` | 64 | **字节序转换的 6 个 inline**（§4.4） |
| `include/linux/virtio_pci_modern.h` · `virtio_pci_legacy.h` | 118 · 40 | 两个 PCI 库的接口 |
| `include/linux/virtio_net.h` · `virtio_vsock.h` | 229 · 253 | **具体设备类型的内核侧辅助**（不是 uapi） |
| `virtio_console.h` · `virtio_caif.h` · `virtio_dma_buf.h` · `virtio_anchor.h` | 38 · 24 · 37 · 19 | — |
| **合计** | **1758** | — |

⚠️ **`include/linux/virtio_*.h` 和 `include/uapi/linux/virtio_*.h` 是两套东西**：

| | `include/linux/` | `include/uapi/linux/` |
|---|---|---|
| 内容 | **Linux 的内部辅助** | **规范的 C 表达（跨边界 ABI）** |
| 数量 | 12 个 | **27 个** |
| 能改吗 | 能（内部接口） | ⚠️ **不能**——改了就和对端不兼容 |

**有几个名字在两边都存在**（`virtio_config.h` · `virtio_net.h` · `virtio_ring.h` ·
`virtio_console.h`）⇒ **看代码时必须看清 `#include <linux/...>` 还是
`<uapi/linux/...>`**（05 §7 有独立一节讲这组近邻）。

### §0.6 Kconfig

| 选项 | 位置 | 作用 |
|---|---|---|
| `VIRTIO` | `drivers/virtio/Kconfig:5`（`tristate`） | **核心框架**；由传输层 `select` 进来，**不直接选** |
| `VIRTIO_ANCHOR` | `drivers/virtio/Kconfig:2` | 被 `VIRTIO` 自动 `select` |
| `VIRTIO_PCI_LIB` / `_LEGACY` | `drivers/virtio/Kconfig:13` / `:21` | **那两个 `*_dev.c` 库** |
| `VIRTIO_MENU` | `drivers/virtio/Kconfig:31`（`default y`） | 菜单开关 |
| ⚠️`VIRTIO_HARDEN_NOTIFICATION` | `drivers/virtio/Kconfig:36` | **加固选项**：限制设备在不该通知的时候通知 |
| `VIRTIO_PCI` | `drivers/virtio/Kconfig:50` | **PCI 传输层**（`depends on PCI`） |
| `VIRTIO_PCI_LEGACY` | `drivers/virtio/Kconfig:63`（`default y`） | **支持 0.9.X 及更老的草案设备** |
| `VIRTIO_VDPA` | `drivers/virtio/Kconfig:83` | vDPA 传输层 |
| `VIRTIO_BALLOON` | `drivers/virtio/Kconfig:107` | 气球设备驱动 |
| `VIRTIO_MEM` | `drivers/virtio/Kconfig:118` | ⚠️ **`depends on X86_64 \|\| ARM64`** ——arm64 上可用 |
| `VIRTIO_PMEM` | `drivers/virtio/Kconfig:96` | — |
| `VIRTIO_MMIO` | `drivers/virtio/Kconfig:148`（`tristate`） | **MMIO 传输层**——"Platform bus driver for memory mapped virtio devices"；`depends on HAS_IOMEM && HAS_DMA` |
| ⚠️`VIRTIO_MMIO_CMDLINE_DEVICES` | `drivers/virtio/Kconfig:158` | **第三条发现路径**：允许用内核命令行凭空实例化 virtio-mmio 设备。⚠️ **help 文本自己警告"参数写错会让系统崩溃"**（`:163-164`） |
| `VIRTIO_DMA_SHARED_BUFFER` | `drivers/virtio/Kconfig:169` | §8.1 的 dma-buf 集成 |

**arm64 虚拟机上最常见的两种组合是 `VIRTIO_MMIO`（DT 发现）和 `VIRTIO_PCI`**。
⚠️ **`VIRTIO_MMIO_CMDLINE_DEVICES` 是一条容易被忽略的发现路径**——
它让设备可以完全不出现在 DT 里，06 篇的分诊表会展开。

---

## §1 先分清：virtio 有**两层**可插拔

**这是本子系统最要紧的一张表**，也是它和 timer/of 最大的结构差别：

| 层 | ops 集合 | 谁实现 | 实现数 | 住在哪 |
|---|---|---|---|---|
| **1. 传输层** | `struct virtio_config_ops`（**17 个回调**，`include/linux/virtio_config.h:95-121`，§6.1 逐个列） | **传输驱动**（PCI / MMIO / CCW / vDPA / …） | **9 个** | **5 个目录**（§1.1） |
| **2. 设备驱动层** | `struct virtio_driver` | **具体设备的驱动**（net / blk / console / …） | **25 个** | **22 个目录**（§1.2） |

**两层的关系**：

```
    25 个设备驱动（virtio_net / virtio_blk / …）        ← 层 2
              │  只调 virtqueue_* 和 virtio_cread/cwrite
              ▼
    ┌───────────────────────────────────────┐
    │  virtio 核心（virtio.c + virtio_ring.c）│  ← 框架，3453 行
    │  总线 · feature 协商 · vring 实现       │
    └───────────────────────────────────────┘
              │  通过 virtio_config_ops 下调
              ▼
    9 个传输层实现（PCI / MMIO / CCW / vDPA / …）      ← 层 1
              │
              ▼
    ═══════════ 边界：对端不在本树 ═══════════
    hypervisor / 远端固件 / 硬件卸载
```

⇒ **绝大多数人只写层 2**（一个新的 virtio 设备驱动）。
写层 1 的场合只有"给一种新的虚拟化传输方式接上 virtio"。

### §1.1 9 个传输层实现的确切位置

```bash
grep -rn 'struct virtio_config_ops [a-z_]* = {' --include=*.c .
```

| 文件 | 行 | 说明 |
|---|---|---|
| `drivers/virtio/virtio_pci_modern.c` | **484 和 504** | ⚠️ **一个文件里两个**：`virtio_pci_config_nodev_ops`（`:484`，无配置空间的设备）和 `virtio_pci_config_ops`（`:504`） |
| `drivers/virtio/virtio_mmio.c` | 549 | **arm64 虚拟机常用** |
| `drivers/virtio/virtio_pci_legacy.c` | 189 | 0.9.X 草案设备 |
| `drivers/virtio/virtio_vdpa.c` | 334 | vDPA |
| `drivers/remoteproc/remoteproc_virtio.c` | 327 | **对端是远端固件**（不是 hypervisor） |
| `drivers/s390/virtio/virtio_ccw.c` | 1021 | s390 专属 |
| `drivers/platform/mellanox/mlxbf-tmfifo.c` | 1088 | BlueField 的 FIFO 传输 |
| `arch/um/drivers/virtio_uml.c` | 1094 | **对端是用户态进程** |

⇒ **"对端是什么"因传输层而异**：hypervisor（PCI/MMIO/CCW）、
远端固件（remoteproc）、用户态进程（UML）、硬件（vDPA/BlueField）。
**这就是为什么本套不能笼统地说"对端是 QEMU"**（99 §5.5）。

### §1.2 25 个设备驱动，跨 22 个目录

```bash
grep -rln 'struct virtio_driver [a-z_0-9]* = {' --include=*.c . \
  | xargs -n1 dirname | sort | uniq -c | sort -rn
```

| 目录 | 数 | 目录 | 数 |
|---|---|---|---|
| `drivers/virtio` | **3**（balloon / mem / input） | `drivers/iommu` | 1 |
| `drivers/block` | 1（virtio_blk） | `drivers/i2c/busses` | 1 |
| `drivers/net` | 1（virtio_net） | `drivers/gpu/drm/virtio` | 1 |
| `drivers/scsi` | 1 | `drivers/gpio` | 1 |
| `drivers/char` | 1（console） | `drivers/firmware/arm_scmi` | 1 |
| `drivers/char/hw_random` | 1 | `drivers/crypto/virtio` | 1 |
| `drivers/bluetooth` | 1 | `drivers/rpmsg` | 1 |
| `drivers/net/caif` | 1 | `drivers/nvdimm` | 1 |
| `drivers/net/wireless` | 1 | `sound/virtio` | 1 |
| `fs/fuse` | 1（virtiofs） | `net/9p` | 1 |
| `net/vmw_vsock` | 1（vsock） | `arch/um/drivers` | 1 |

⚠️ **计数口径警告**（99 §6 记了这条）：**必须用 `[a-z_0-9]*`**。
用 `[a-z_]*` 会漏掉 2 个（`drivers/i2c/busses/i2c-virtio.c` 和
`net/9p/trans_virtio.c`，它们的变量名里带数字）⇒ **得到 23 而不是 25**。

**三种口径互相印证**：严格 pattern **25**、宽松 pattern **25**、
`register_virtio_driver`/`module_virtio_driver` 调用文件数 **25** ⇒ 可信。

### §1.3 按你要做的事跳转

| 我想… | 去 |
|---|---|
| 写一个新的 virtio 设备驱动 | §2（注册）+ §3（数据通路）+ §4（feature）+ §5（配置空间） |
| 收发数据 | §3 |
| 协商 feature 位 | §4 |
| 读设备的配置空间 | §5 |
| 给新的虚拟化传输接上 virtio | §6 |
| 直接碰 vring 布局 | §7（+ 02 §5 的内存布局图） |
| 查错误码 | §9 |

---

## §2 设备驱动侧：注册与生命周期

头文件：`include/linux/virtio.h`；实现：`drivers/virtio/virtio.c`（11 个导出符号）

### §2.1 注册

| API | 说明 | 备注 |
|---|---|---|
| `register_virtio_driver(driver)` | **注册一个 virtio 设备驱动** | `drivers/virtio/virtio.c:352`；通常用 `module_virtio_driver()` 宏包一层 |
| `unregister_virtio_driver(driver)` | 注销 | `drivers/virtio/virtio.c:361` |
| `module_virtio_driver(drv)` | **一行搞定 module_init/exit** | **25 个驱动基本都用它** |
| `register_virtio_device(dev)` | **传输层用的**，不是设备驱动用 | `include/linux/virtio.h:136` |
| `unregister_virtio_device(dev)` | 同上 | `include/linux/virtio.h:137` |
| `is_virtio_device(dev)` | 判断一个 `struct device` 是不是 virtio 设备 | `include/linux/virtio.h:138` |
| `dev_to_virtio(_dev)` | **`container_of` 包装** | `include/linux/virtio.h:130` |
| `drv_to_virtio(drv)` | 同上，driver 侧 | `include/linux/virtio.h:193` |

### §2.2 `struct virtio_driver` 的字段（`include/linux/virtio.h:175-191`）

| 字段 | 必填 | 说明 |
|---|---|---|
| `driver` | **是** | 内嵌的 `struct device_driver`（只需填 `.name` 和 `.owner`） |
| `id_table` | **是** | **匹配哪些设备**：`{ VIRTIO_ID_NET, VIRTIO_DEV_ANY_ID }` 之类 |
| `feature_table` / `feature_table_size` | 强烈建议 | **我这个驱动认识哪些 feature 位** ⇒ §4.2 |
| `feature_table_legacy` / `_size_legacy` | 否 | **只在 legacy（0.9.X）路径下生效** |
| `validate` | 否 | **在 feature 协商之前**被调，可以改 `dev->features`（§4.3） |
| `probe` | **是** | **feature 协商完成之后**被调 |
| `scan` | 否 | probe 之后、设备可用之后再调一次（virtio_blk 用它做分区扫描） |
| `remove` | **是** | 拆除 |
| `config_changed` | 否 | **设备通知"我的配置空间变了"** |
| `freeze` / `restore` | 否 | ⚠️ 只在 `CONFIG_PM` 下存在（`include/linux/virtio.h:187-190`） |

⚠️ **`validate` 和 `probe` 的次序是本子系统最容易搞错的一处**（§4.3、03 §A 会展开）：
`validate` 在**协商前**，`probe` 在**协商后**。
⇒ **想否掉某个 feature 位只能在 `validate` 里做**，`probe` 里改已经太晚。

### §2.3 `struct virtio_device` 的字段（`include/linux/virtio.h:114-128`）

| 字段 | 谁填 | 说明 |
|---|---|---|
| `index` | 核心 | 设备编号 |
| `failed` | 核心 | **设备已被标记为 FAILED** |
| `config_enabled` / `config_change_pending` | 核心 | **配置变更通知的延迟机制**（probe 期间先攒着） |
| `config_lock` / `vqs_list_lock` | 核心 | 两把 `spinlock_t`（02 §6） |
| `dev` | **内嵌 `struct device`** | **它挂在 `virtio_bus` 上** |
| `id` | **传输层填** | `struct virtio_device_id`：`device` + `vendor` |
| `config` | **传输层填** | **指向那 9 个 `virtio_config_ops` 之一** |
| `vringh_config` | 传输层 | 主机侧 vring（vhost 场景） |
| `vqs` | 核心 | 该设备的所有 virtqueue 链表 |
| `features` | **核心（协商结果）** | **`u64` —— 协商后的最终 feature 位** |
| `priv` | 设备驱动 | **你自己的私有指针** |

⚠️ **`features` 只有 64 位** ⇒ **feature 位号不能超过 63**。
⇒ 这是规范层面的一个硬上限，02 §1.0 会讲它带来的后果。

---

## §3 virtqueue：数据通路（用得最多的一组）

头文件：`include/linux/virtio.h`；实现：`drivers/virtio/virtio_ring.c`（**32 个导出符号**）

**模型**：一个 virtqueue 是**一条单向或双向的缓冲区队列**。
你把 scatterlist 挂进去，通知对端，对端处理完再还给你。

### §3.1 提交缓冲区

| API | 说明 | 备注 |
|---|---|---|
| `virtqueue_add_outbuf(vq, sg, num, data, gfp)` | **提交"给对端读"的缓冲区** | `include/linux/virtio.h:41`；`data` 是你的 token，`get_buf()` 会还给你 |
| `virtqueue_add_inbuf(vq, sg, num, data, gfp)` | **提交"让对端写"的缓冲区** | `include/linux/virtio.h:46` |
| `virtqueue_add_inbuf_ctx(vq, sg, num, data, ctx, gfp)` | 带额外 context | `include/linux/virtio.h:51` |
| `virtqueue_add_sgs(vq, sgs, out_sgs, in_sgs, data, gfp)` | **同时有读和写的部分** | `include/linux/virtio.h:57`；**注意顺序：out 必须在 in 之前** |

⚠️ **三个坑**：
1. **`data` 不能是 NULL** ——它是核心用来认这次提交的 token
2. **out 段必须排在 in 段前面**（`virtqueue_add_sgs()`）——这是规范要求
3. **返回 `-ENOSPC` 是正常的**，不是错误 ⇒ 队列满了，等 `get_buf()` 腾出空间（§9.2）

### §3.2 通知对端

| API | 说明 | 备注 |
|---|---|---|
| `virtqueue_kick(vq)` | **通知对端"有新东西了"** | `include/linux/virtio.h:64`；**返回 false 表示队列已 broken** |
| `virtqueue_kick_prepare(vq)` | **只判断"需不需要通知"** | `include/linux/virtio.h:66`；和下面配对，用于批量提交后只 kick 一次 |
| `virtqueue_notify(vq)` | **真正执行通知** | `include/linux/virtio.h:68` |

**`kick` = `kick_prepare` + `notify`**。拆开的意义：
批量提交多个缓冲区时，可以只在最后判断一次要不要通知
⇒ **省掉大量 vmexit / 中断**，这是 virtio 性能优化的主要手段之一。

⚠️ **`kick_prepare()` 返回 false 时不要 `notify()`** ——
对端明确表示"暂时不用通知我"（通过 `used_event` / `EVENT_IDX` 机制）。

### §3.3 回收缓冲区

| API | 说明 | 备注 |
|---|---|---|
| `virtqueue_get_buf(vq, len)` | **取回一个已处理完的缓冲区** | `include/linux/virtio.h:70`；**返回的就是你当初给的 `data`**；NULL = 暂时没有 |
| `virtqueue_get_buf_ctx(vq, len, ctx)` | 带 context 的版本 | `include/linux/virtio.h:72` |
| `virtqueue_detach_unused_buf(vq)` | **拆除时把还没被处理的缓冲区都拿回来** | `include/linux/virtio.h:85`；**`remove()` 里必须循环调它直到返回 NULL，否则泄漏** |

⚠️ **`get_buf()` 返回 NULL 有两种含义**："对端还没处理完"和"队列空"，
**代码上分不开** ⇒ 一律当作"稍后再来"。

### §3.4 中断/回调控制

| API | 说明 | 备注 |
|---|---|---|
| `virtqueue_disable_cb(vq)` | **告诉对端"别通知我了"** | `include/linux/virtio.h:75`；⚠️ **只是提示，对端仍可能通知** |
| `virtqueue_enable_cb(vq)` | **重新开启通知** | `include/linux/virtio.h:77`；**返回 false 表示"开启期间已经有新数据了"** ⇒ 要立刻再 poll 一次 |
| `virtqueue_enable_cb_prepare(vq)` | 拆成两步的前半 | `include/linux/virtio.h:79` |
| `virtqueue_poll(vq, last_used_idx)` | **配合上面判断有没有新数据** | `include/linux/virtio.h:81` |
| `virtqueue_enable_cb_delayed(vq)` | **"攒够一批再通知我"** | `include/linux/virtio.h:83`；**网卡驱动的 NAPI 路径靠它降中断率** |

⚠️ **`disable_cb()` 不是关中断** ——它只是在共享内存里置一个标志位，
**对端可以无视**。⇒ **所以驱动的回调必须能承受"不该来的时候来了"**，
这也是 `CONFIG_VIRTIO_HARDEN_NOTIFICATION`（§0.6）存在的原因。

### §3.5 查询与状态

| API | 说明 | 备注 |
|---|---|---|
| `virtqueue_get_vring_size(vq)` | 队列有多少个描述符 | `include/linux/virtio.h:87` |
| `virtqueue_is_broken(vq)` | **这个队列还能用吗** | `include/linux/virtio.h:89`；**设备被标 FAILED 或热插拔后为真** |
| `virtqueue_get_vring(vq)` | 拿到底层 `struct vring` | `include/linux/virtio.h:91` |
| `virtqueue_get_desc_addr(vq)` / `_avail_addr()` / `_used_addr()` | 三段的 DMA 地址 | `include/linux/virtio.h:92-94`；**传输层用来告诉对端队列在哪** |
| `virtqueue_resize(vq, num, recycle)` | **运行期改队列大小** | `include/linux/virtio.h:96` |
| `__virtqueue_break(vq)` / `__virtqueue_unbreak(vq)` | 强制标记 broken | `include/linux/virtio.h:143-144`；带 `__` 前缀，内部用 |

### §3.6 `struct virtqueue` 的字段（`include/linux/virtio.h:29-39`）

| 字段 | 说明 |
|---|---|
| `list` | 挂在 `vdev->vqs` 上 |
| `callback` | **对端通知你时调的函数**（`void (*)(struct virtqueue *)`） |
| `name` | **调试用，会出现在中断名里** |
| `vdev` | 回指设备 |
| `index` | **第几个队列** ——规范给每个设备类型规定了队列编号的含义 |
| `num_free` | **还能提交多少个描述符** ⇒ 判断满不满 |
| `num_max` | 队列容量上限 |
| `priv` | 传输层的私有指针（**不是给设备驱动用的**） |
| `reset` | **正在被 reset**（配合 `virtqueue_resize()`） |

⚠️ **`priv` 是传输层的，不是你的** ——设备驱动的私有数据放
`vdev->priv`（§2.3）。**这是一组真实存在的同名陷阱**，05 §7 会列。

---

## §4 feature 协商（本子系统的灵魂）

头文件：`include/linux/virtio_config.h`；核心逻辑：`drivers/virtio/virtio.c`

**为什么它这么重要**：virtio 的两端**版本可能不一致**——
新内核可能跑在老 hypervisor 上，也可能反过来。
**feature 协商就是"双方各报一份能力清单，取交集"**。

### §4.1 查询与操作

| API | 说明 | 备注 |
|---|---|---|
| `virtio_has_feature(vdev, fbit)` | **设备驱动唯一该用的查询函数** | `include/linux/virtio_config.h:185`；**它会先 `virtio_check_driver_offered_feature()` 校验你有没有在 `feature_table` 里声明过这个位** |
| `__virtio_test_bit(vdev, fbit)` | **给传输层用的**，不做校验 | `include/linux/virtio_config.h:134`；⚠️ 头文件注释明说 `Devices should normally use virtio_has_feature`（`:128-129`） |
| `__virtio_set_bit(vdev, fbit)` / `__virtio_clear_bit()` | 传输层用 | `include/linux/virtio_config.h:151` / `:168` |
| `virtio_check_driver_offered_feature(vdev, fbit)` | **未声明就 `BUG`** | `include/linux/virtio_config.h:124` |
| `virtio_has_dma_quirk(vdev)` | **要不要绕过 DMA API** | `include/linux/virtio_config.h:198`；实现就是 `!virtio_has_feature(vdev, VIRTIO_F_ACCESS_PLATFORM)`（`:204`） |

⚠️ **`virtio_has_feature()` 会 `BUG` 的那条校验是本子系统一个很有个性的设计**：
**你查一个自己没在 `feature_table` 里声明的 feature 位，内核直接崩。**
理由是：没声明过的位**永远不会被协商上来**，所以查它必然是逻辑错误。
⇒ **加新 feature 时必须同时改 `feature_table`**，否则一查就崩（05 §8）。

### §4.2 `feature_table` 怎么填

```c
static unsigned int features[] = {
	VIRTIO_MY_F_FOO,
	VIRTIO_MY_F_BAR,
	VIRTIO_F_ANY_LAYOUT,		/* 通用位也要列 */
};

static struct virtio_driver my_driver = {
	.feature_table		= features,
	.feature_table_size	= ARRAY_SIZE(features),
	...
};
```

**语义**：这张表是"**我这个驱动认识哪些位**"。
核心在协商时把"设备提供的位"和"这张表"取交集
⇒ **表里没有的位一定不会出现在 `vdev->features` 里**。

**`feature_table_legacy` / `_size_legacy`**（`include/linux/virtio.h:180-181`）：
**只在 legacy（0.9.X 草案）路径上生效** ——如果你的驱动要兼容
`VIRTIO_PCI_LEGACY` 设备，某些位在 legacy 下含义不同，就用这张第二张表。
⚠️ **新驱动不需要它。**

### §4.3 ⚠️ 协商的时序：`validate` 在前，`probe` 在后

**`virtio_dev_probe()` 的实际次序**（`drivers/virtio/virtio.c:237-323`，
每一步都带行号；03 §A 会逐段展开）：

```
  传输层 register_virtio_device()          drivers/virtio/virtio.c:418
    ├─ virtio_reset_device(dev)            先复位（:447 一带）
    ├─ 加状态位 VIRTIO_CONFIG_S_ACKNOWLEDGE
    └─ device_add() ⇒ 触发匹配和 probe
         │
         ▼
  virtio_dev_probe(_d)                     drivers/virtio/virtio.c:237
    ① 加状态位 VIRTIO_CONFIG_S_DRIVER               :247
    ② device_features = config->get_features(dev)   :250
    ③ 从 driver->feature_table 组出 driver_features
    ④ dev->features = driver_features & device_features        :273
         （legacy 路径用 driver_features_legacy）              :275
    ⑤ 传输层的 feature 位无条件保留                  :277
    ⑥ config->finalize_features(dev)            :282   ← 第一次
    ⑦ if (drv->validate) drv->validate(dev)      :286 / :289
    ⑧ 若 validate 改了 features ⇒ 再调一次
           config->finalize_features(dev)            :295   ← 第二次
    ⑨ virtio_features_ok(dev)                        :301
    ⑩ drv->probe(dev)                            :305
    ⑪ probe 没自己设 DRIVER_OK ⇒ 核心代调
           virtio_device_ready(dev)                  :310-311
    ⑫ if (drv->scan) drv->scan(dev)                  :313-314
    ✗ 任何一步失败 ⇒ 加 VIRTIO_CONFIG_S_FAILED        :320
```

⚠️ **次序里最反直觉的两点**：

1. **`finalize_features()` 在 `validate()` 之前就调了一次**（第 ⑥ 步）——
   **所以 `validate()` 看到的是"已经写回设备一次"的状态**
2. **`validate()` 改了 features 会触发第二次 `finalize_features()`**（第 ⑧ 步）——
   **代码显式比较 `features != dev->features` 才决定要不要重写**

⇒ **结论仍然是"想否掉 feature 位只能在 `validate` 里做"**，
但机制不是"validate 在唯一一次 finalize 之前"，而是
**"validate 是唯一一个改了还会被重新写回设备的钩子"**。
`probe` 里改 `dev->features` **不会有第三次 `finalize_features()`**
⇒ 驱动和设备的认知分叉，表现为**数据格式错乱而不是报错**。

### §4.3.1 `virtio_features_ok()`：一次写-回读握手

`drivers/virtio/virtio.c:171-202`，做三件事：

| 步 | 代码 | 说明 |
|---|---|---|
| ① `might_sleep()` | `:175` | 声明这里可能睡 |
| ⚠️ ② **受限内存访问检查** | `:177` 的 `if (virtio_check_mem_acc_cb(dev))` | **只在这个回调为真时才检查**（§8.2 的 anchor）：要求 `VIRTIO_F_VERSION_1`（`:178`，失败打 `device must provide VIRTIO_F_VERSION_1`）**和** `VIRTIO_F_ACCESS_PLATFORM`（`:184`，打 `device must provide VIRTIO_F_ACCESS_PLATFORM`），都返回 `-ENODEV` |
| ③ **legacy 设备直接放行** | `:191` 的 `if (!virtio_has_feature(dev, VIRTIO_F_VERSION_1)) return 0;` | **没协商上 VERSION_1 就是 legacy 设备，后面的握手跳过** |
| ④ **写-回读握手** | `:194` 加 `VIRTIO_CONFIG_S_FEATURES_OK` → `:195` 读回 status → `:196` 没置上就失败 | **设备可以拒绝你的 feature 集合** |

**第 ④ 步是本子系统一个很漂亮的机制**：驱动把 `FEATURES_OK` 写进状态字节，
然后**读回来看设备有没有把它清掉**。清掉了就是"我不接受这个组合"，
核心打 `virtio: device refuses features: %x`（`:197-198`）并返回 `-ENODEV`。
⇒ **这是跨边界协议特有的"对方可以说不"** ——本地硬件不会这样。

⚠️ **注意第 ②③ 步的次序造成的一个反直觉后果**：
**`VIRTIO_F_VERSION_1` 在两个地方被检查，含义不同** ——
第 ② 步是"受限内存场景下必须有"（硬性），
第 ③ 步是"没有就说明是 legacy，走宽松路径"。

**另外**：`drivers/virtio/virtio.c:277` 那一行注释
`Transport features always preserved to pass to finalize_features`
说明**传输层自己的 feature 位不参与"和 driver feature_table 取交集"**
⇒ **驱动不需要在 `feature_table` 里列 `VIRTIO_F_VERSION_1` 这类传输层位。**

### §4.4 字节序：feature 位决定用哪套规则

**这是跨边界 ABI 型子系统特有的一节**（框架型子系统完全没有这个问题）。

| API | 说明 | 备注 |
|---|---|---|
| `virtio_is_little_endian(vdev)` | **本设备用小端还是宿主端序** | `include/linux/virtio_config.h:333`；实现是 `virtio_has_feature(vdev, VIRTIO_F_VERSION_1) \|\| ...`（`:335`） |
| `virtio16_to_cpu(vdev, val)` / `cpu_to_virtio16(vdev, val)` | 16 位转换 | `include/linux/virtio_config.h:340` / `:345` |
| `virtio32_to_cpu()` / `cpu_to_virtio32()` | 32 位 | `include/linux/virtio_config.h:350` / `:355` |
| `virtio64_to_cpu()` / `cpu_to_virtio64()` | 64 位 | `include/linux/virtio_config.h:360` / `:365` |
| `virtio_to_cpu(vdev, x)` / `cpu_to_virtio(vdev, x, m)` | **按类型自动分派的宏** | `include/linux/virtio_config.h:370` / `:378` |
| `virtio_le_to_cpu(x)` / `virtio_cpu_to_le(x, m)` | **无条件小端**（不看 feature） | `include/linux/virtio_config.h:437` / `:445` |
| `__virtio_native_type(structname, member)` | 取字段的宿主端类型 | `include/linux/virtio_config.h:386` |
| 底层 6 个 inline | `__virtio16_to_cpu()` 等 | `include/linux/virtio_byteorder.h:16` · `:24` · `:32` · `:40` · `:48` · `:56` |
| `virtio_legacy_is_little_endian()` | legacy 设备的端序判断 | `include/linux/virtio_byteorder.h:7` |

⚠️ **规则很反直觉**：

| 设备类型 | 共享内存里的字节序 |
|---|---|
| **协商上 `VIRTIO_F_VERSION_1`**（现代设备） | **一律小端** |
| ⚠️ **legacy（0.9.X）设备** | ⚠️ **宿主字节序**（guest-native）—— 大端机器上就是大端 |

⇒ **所以 `__virtio16` 这类类型不是"小端 16 位"，是"端序取决于协商结果的 16 位"**，
必须用 `virtio16_to_cpu(vdev, x)` 转，**不能用 `le16_to_cpu()`**。
**arm64 通常是小端，所以这个 bug 在 arm64 上不会暴露**——
⚠️ **这是又一条"在你的平台上不会出错但仍必须遵守"的规则**（05 §7）。

**两个"无条件小端"的例外**：`virtio_le_to_cpu()` / `virtio_cread_le()`
—— 规范里有些字段**明确规定就是小端**，不随协商变，用这一套。

---

## §5 配置空间访问

**模型**：每个 virtio 设备有一段"配置空间"，布局由**规范**按设备类型规定
（例如 `struct virtio_net_config` 在 `include/uapi/linux/virtio_net.h` 里）。
**读它必须通过访问器，不能直接 memcpy** ——因为传输层可能要做特殊访问
（PCI 的 BAR、MMIO 的寄存器、CCW 的通道命令各不相同）。

### §5.1 结构化访问（推荐）

| API | 说明 | 备注 |
|---|---|---|
| `virtio_cread(vdev, structname, member, ptr)` | **按字段名读**，自动处理字节序和宽度 | `include/linux/virtio_config.h:390`；**首选** |
| `virtio_cwrite(vdev, structname, member, ptr)` | 按字段名写 | `include/linux/virtio_config.h:419` |
| `virtio_cread_le(...)` / `virtio_cwrite_le(...)` | **无条件小端的版本** | `include/linux/virtio_config.h:454` / `:482`（§4.4 的例外） |
| `virtio_cread_feature(vdev, fbit, structname, member, ptr)` | **只在 feature 位有效时才读** | `include/linux/virtio_config.h:602`；**没有该 feature 时返回 `-ENOENT` 且不动 `ptr`** |
| `virtio_cread_le_feature(...)` | 上面的小端版 | `include/linux/virtio_config.h:613` |

**用法**：
```c
	u16 mtu;

	/* structname 是 uapi 里规范定义的结构，member 是字段名 */
	virtio_cread(vdev, struct virtio_net_config, mtu, &mtu);
```

**它做了三件事**：算 `offsetof`、按字段宽度选 8/16/32/64 的读法、
按 §4.4 的规则转字节序。⇒ **这就是为什么强烈推荐结构化访问而不是按偏移读。**

### §5.2 按宽度访问（次选）

| API | 说明 | 备注 |
|---|---|---|
| `virtio_cread8(vdev, offset)` / `virtio_cwrite8()` | 8 位 | `include/linux/virtio_config.h:526` / `:535`；8 位无字节序问题 |
| `virtio_cread16()` / `virtio_cwrite16()` | 16 位 | `include/linux/virtio_config.h:542` / `:552` |
| `virtio_cread32()` / `virtio_cwrite32()` | 32 位 | `include/linux/virtio_config.h:562` / `:572` |
| `virtio_cread64()` / `virtio_cwrite64()` | 64 位 | `include/linux/virtio_config.h:582` / `:591` |
| `virtio_cread_bytes(vdev, offset, buf, len)` | **读一段字节**（不做字节序转换） | `include/linux/virtio_config.h:519` |
| `__virtio_cread_many()` | **带 generation 重试的多字段读** | `include/linux/virtio_config.h:498` |

⚠️ **`__virtio_cread_many()` 存在的原因值得记**：配置空间**可能在你读的中途被设备改掉**。
规范给了一个 `generation` 计数器（`config_ops->generation`，§6.1），
读多个字段时要"读 generation → 读字段 → 再读 generation，不一致就重来"。
⇒ **这是跨边界共享状态特有的问题**，本地硬件寄存器不会这样。

---

## §6 传输层侧：`virtio_config_ops`

**只有给新的虚拟化传输方式接 virtio 时才写这一层**（9 个实现见 §1.1）。

### §6.1 17 个回调（`include/linux/virtio_config.h:95-121`）

| 回调 | 必填 | 干什么 |
|---|---|---|
| `get` / `set` | **是** | **读写配置空间**（`:96` / `:98`） |
| `generation` | 否 | **配置空间的版本号**（`:100`）⇒ §5.2 的重试机制 |
| `get_status` / `set_status` | **是** | **设备状态字节**（`:101` / `:102`） |
| `reset` | **是** | 复位设备（`:103`） |
| `find_vqs` | **是** | **建立所有 virtqueue**（`:104`）——最复杂的一个 |
| `del_vqs` | **是** | 销毁（`:108`） |
| `synchronize_cbs` | 否 | **等所有回调跑完**（`:109`） |
| `get_features` / `finalize_features` | **是** | **feature 协商的两端**（`:110` / `:111`，§4.3） |
| `bus_name` | 否 | 显示名（`:112`） |
| `set_vq_affinity` / `get_vq_affinity` | 否 | 中断亲和性（`:113` / `:115`） |
| `get_shm_region` | 否 | 共享内存区（`:117`）——virtiofs / virtio-gpu 用 |
| `disable_vq_and_reset` / `enable_vq_after_reset` | 否 | **单队列 reset**（`:119` / `:120`）⇒ 配合 `virtqueue_resize()` |

### §6.2 核心提供给传输层/驱动的便利函数

| API | 说明 | 备注 |
|---|---|---|
| `virtio_find_vqs(vdev, nvqs, vqs, callbacks, names, desc)` | **设备驱动建队列的标准入口** | `include/linux/virtio_config.h:222`；它转调 `config->find_vqs()` |
| `virtio_find_vqs_ctx(...)` | 带 ctx 的版本 | `include/linux/virtio_config.h:231` |
| `virtio_find_single_vq(vdev, callback, name)` | **只有一个队列时的简化版** | `include/linux/virtio_config.h:208` |
| `virtio_device_ready(dev)` | **告诉设备"我准备好了，可以开始通知我"** | `include/linux/virtio_config.h:268`；⚠️ **`probe` 末尾必须调**，否则收不到通知 |
| `virtio_synchronize_cbs(dev)` | 等回调跑完 | `include/linux/virtio_config.h:245` |
| `virtio_bus_name(vdev)` | 显示名 | `include/linux/virtio_config.h:299` |
| `virtqueue_set_affinity(vq, cpu_mask)` | 设队列中断亲和性 | `include/linux/virtio_config.h:316` |
| `virtio_get_shm_region(vdev, region, id)` | 取共享内存区 | `include/linux/virtio_config.h:325` |

⚠️ **`virtio_device_ready()` 忘了调是新手最常见的 bug**：
probe 成功、队列建好了，但设备**永远不通知你** ⇒ 看起来像"设备不工作"。
**判据**：`virtqueue_get_buf()` 一直返回 NULL 而对端明明在处理（07 篇会给观测手段）。

---

## §7 设备级操作

| API | 说明 | 备注 |
|---|---|---|
| `virtio_reset_device(vdev)` | **复位设备** | `include/linux/virtio.h:151` |
| `virtio_add_status(vdev, status)` | **往状态字节里加位** | `include/linux/virtio.h:135`；出错时加 `VIRTIO_CONFIG_S_FAILED` |
| `virtio_break_device(vdev)` | **把设备和所有队列标为 broken** | `include/linux/virtio.h:140`；**热插拔移除时用** |
| `__virtio_unbreak_device(vdev)` | 取消 broken | `include/linux/virtio.h:141`；带 `__`，内部用 |
| `virtio_config_changed(vdev)` | **传输层通知核心"配置变了"** | `include/linux/virtio.h:146`；核心再转给驱动的 `config_changed()` |
| `virtio_device_freeze(vdev)` / `virtio_device_restore(vdev)` | 电源管理 | `include/linux/virtio.h:148` / `:149` |
| `virtio_max_dma_size(vdev)` | **单次 DMA 的最大长度** | `include/linux/virtio.h:153`；**建 scatterlist 前要看它** |

⚠️ **`virtio_break_device()` 和 `virtqueue_is_broken()` 是一对**：
前者置位，后者查询。**设备被 break 之后所有 `virtqueue_kick()` 返回 false、
`get_buf()` 返回 NULL** ⇒ 驱动的清理路径要能处理这个状态。

---

## §8 辅助模块

### §8.1 dma-buf 集成（`drivers/virtio/virtio_dma_buf.c`，4 个导出符号）

| API | 位置 |
|---|---|
| `virtio_dma_buf_export()` | `drivers/virtio/virtio_dma_buf.c:35` |
| `virtio_dma_buf_attach()` | `drivers/virtio/virtio_dma_buf.c:55` |
| `is_virtio_dma_buf()` | `drivers/virtio/virtio_dma_buf.c:65` |
| `virtio_dma_buf_get_uuid()` | `drivers/virtio/virtio_dma_buf.c:86` |

**主要使用者是 virtio-gpu** ——把 GPU 对象导出成 dma-buf 并带上 virtio 侧的 UUID。

### §8.2 anchor：受限内存访问（`drivers/virtio/virtio_anchor.c`，18 行 / 2 个导出符号）

| API | 位置 | 作用 |
|---|---|---|
| `virtio_require_restricted_mem_acc()` | `drivers/virtio/virtio_anchor.c:9` | **要求设备必须走受限内存访问** |
| `virtio_check_mem_acc_cb` | `drivers/virtio/virtio_anchor.c:18` | **一个可被覆盖的全局函数指针** |

**它只有 18 行，但意义不小**：这是**机密计算 / 受保护虚拟机**的钩子——
在那类环境里，hypervisor **不能随意访问 guest 内存**，
所以 virtio 必须强制协商上 `VIRTIO_F_ACCESS_PLATFORM`（§4.3 的第二条硬检查）。
⇒ **这解释了 `virtio_features_ok()` 里那条看起来多余的检查**（`drivers/virtio/virtio.c:184`）。

---

## §9 错误码语义

### §9.1 全量普查

统计范围：`drivers/virtio/` 下 **14 个 `.c` 文件**（显式列文件，命令见 99 §3.3）。

| 错误码 | 次数 | 主要语义 |
|---|---|---|
| `-ENOMEM` | **46** | **排第一** ——建队列、分配 vring、分配描述符都可能失败 |
| `-EINVAL` | **27** | 参数不合法 / 配置空间偏移越界 |
| `-EBUSY` | **21** | 设备/队列正在被使用 |
| `-ENODEV` | **14** | 设备不存在或已被移除 |
| `-ENOSPC` | **13** | **队列满** —— ⚠️ **这是正常状态，不是错误**（§9.2） |
| `-ENOENT` | **11** | **feature 位不存在**（`virtio_cread_feature()`，§5.1） |
| `-EAGAIN` | 5 | 稍后重试 |
| `-EIO` | 4 | 传输层 I/O 失败 |
| `-E2BIG` | 4 | **请求太大**（超过 `virtio_max_dma_size()` 之类） |
| `-EOPNOTSUPP` · `-ENOSYS` · `-EFAULT` · `-EEXIST` · `-ERANGE` | **全部 0** | 本子系统不返回这五个 |

**`-ENOMEM` 排第一是本子系统的特征**：virtio 的每个队列都要分配一段
**物理连续或 DMA 可达**的 vring 内存，而队列数量由设备决定
⇒ **内存分配是最主要的失败来源**，和 timer（`-EINVAL`/`-EFAULT` 为主）
、of（`-EINVAL` 为主）都不同。

### §9.2 ⚠️ 三个"不是错误"的返回值

| 返回 | 出现在 | 真实含义 |
|---|---|---|
| `-ENOSPC` | `virtqueue_add_*()` | **队列满了** ⇒ **正常背压**，等 `get_buf()` 腾空间。**不要当错误报** |
| `NULL` | `virtqueue_get_buf()` | **暂时没有已完成的缓冲区** ⇒ 稍后再来 |
| `false` | `virtqueue_kick_prepare()` | **对端说不用通知** ⇒ **别 `notify()`** |

⚠️ **把 `-ENOSPC` 当致命错误往上报是 virtio 驱动最常见的一类 bug**：
高负载下队列必然会满，报错会让上层以为设备坏了。
⇒ **正确做法是停止收包/停 queue，等回收后再继续**（05 §1 有骨架）。

### §9.3 驱动作者实际会拿到的

| 场景 | 返回 | 怎么办 |
|---|---|---|
| `virtio_find_vqs()` 失败 | `-ENOMEM` / `-EINVAL` | 直接失败 probe |
| `virtqueue_add_outbuf()` | **`-ENOSPC` 是正常的** | §9.2 |
| `virtio_cread_feature()` | **`-ENOENT` = 没这个 feature** | 用默认值 |
| 查了未声明的 feature 位 | **不返回错误，直接 `BUG`** | 改 `feature_table`（§4.1） |
| `virtqueue_kick()` 返回 false | **队列 broken** | 走清理路径 |

⚠️ **和 timer 一样，本子系统也有一大类 API 不返回错误码**：
`virtqueue_disable_cb()`、`virtio_add_status()`、`virtio_break_device()`、
`virtio_device_ready()` 全是 `void`。
⇒ **`virtio_device_ready()` 忘调没有任何反馈**（§6.2），这是 03 Part C 的一项。
