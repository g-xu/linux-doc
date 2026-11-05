---
title: 总览
---

# virtio 00：总览（复习入口）

**这一篇的定位是"复习入口"**：隔几个月回来，**只读这一篇**就能恢复全貌。
它是对 01–08 的浓缩，不是引言。

⚠️ **先记住三件事**：
1. **`drivers/virtio` 在这棵树里是干净的**（99 §1.1）——和 timer 不同，
   不需要处处标注"本树改动点"
2. **virtio 有真总线，但另一端是软件**，协议在**树外的 OASIS 规范**里
3. **本套只覆盖 Linux 这一侧（driver 侧）** ——对端行为的论断都是引规范，
   不是引代码（99 §5.5）

---

## §1 定位：virtio 解决什么问题

**一句话**：让**一个** guest 驱动能高效地对接**任何** hypervisor
提供的**任何**厂商的同类虚拟设备。

**规模**：

| 项 | 值 |
|---|---|
| `drivers/virtio/` | **15 个文件、11854 行** |
| 真正的框架 | **`virtio.c`(577) + `virtio_ring.c`(2876) = 3453 行** |
| `include/linux/virtio*.h` | 12 个文件、1758 行 |
| **跨边界 ABI** | **27 个 `include/uapi/linux/virtio_*.h`** |
| 导出符号 | **80 个**（`virtio_ring.c` 独占 32） |
| 设备驱动实现 | **25 个，跨 22 个目录** |
| 传输层实现 | **9 个，跨 5 个目录** |
| 官方文档 | ⚠️ **本树没有**（99 §1.4）——规范在树外 |

**子系统类型：虚拟总线 / 跨边界 ABI 型**（99 §1.3）。
**两道门都有输出**：`virtio_bus`（`drivers/virtio/virtio.c:343`）
+ 27 个 uapi 规范头文件 ⇒ 和 skill 记的实例数字完全一致。

**两层可插拔**（这是全篇最要紧的一张表，02 §1.0.F）：

| 层 | ops | 谁实现 | 实现数 | 住在哪 |
|---|---|---|---|---|
| **1. 传输层** | `virtio_config_ops`（**17 个回调**） | 传输驱动 | **9** | **5 个目录** |
| **2. 设备驱动层** | `virtio_driver` | 具体设备驱动 | **25** | **22 个目录** |

⇒ **理论上 9 × 25 = 225 种组合，每种都要能工作** ——
这是 virtio 抽象必须做得这么干净的根本原因。

⚠️ **和 timer/of 的结构差别**：那两个都只有**一层**可插拔。

---

## §2 背景：规范、协商与边界

**这一节走"规范背景"路径** ——virtio 的形状不是由硬件决定的，
是由**一份树外规范的演化史**决定的。按时间顺序看就非常清楚。

### §2.1 起点：全虚拟化的设备模拟太慢

**最早的虚拟机怎么给 guest 提供网卡**：
hypervisor **逐个寄存器地模拟**一块真实网卡（比如 e1000）。

**流程**：guest 驱动写一个寄存器 → **CPU 陷入 hypervisor**（vmexit）
→ hypervisor 解析"这个寄存器的这个位表示什么" → 做相应动作 → 返回 guest。

⚠️ **代价**：发一个网络包要写十几个寄存器 ⇒ **十几次 vmexit**。
一次 vmexit 是几千个时钟周期 ⇒ **虚拟网卡比物理网卡慢一个数量级**。

**而且 hypervisor 要维护一大堆"某块真实硬件的行为模型"**，
每种硬件一套，改一个 bug 要改 N 处。

⇒ **这是 virtio 要解决的第一个问题**：
**既然 guest 知道自己在虚拟机里，为什么还要装作在操作真硬件？**

### §2.2 第一层解法：共享内存 + 通知

**核心洞见**：guest 和 hypervisor **在同一台物理机上**，
**它们可以共享内存** ——不需要通过"模拟寄存器"来传数据。

**新方案**：
- **数据放在一块双方都能读写的内存里（vring）**
- **只有"我放好了 / 我处理完了"这个信号需要陷入**
- **而且信号可以批量**：放十个包，只打一次招呼

⇒ **一个网络包从"十几次 vmexit"降到"零点几次"**（批量后）。
08 §2 用"储物柜 + 打电话"讲了这件事。

**代价是三条**：
① **guest 必须知道自己在虚拟机里**（不能装作在跑真硬件）；
② **需要一份双方都遵守的内存布局约定** ⇒ 规范；
③ ⚠️ **guest 必须防着对端** ——共享内存里的东西对端能改。

### §2.3 第二层解法：设备类型统一编号

⚠️ **只有共享内存还不够**。如果每个 hypervisor 厂商自己定义
"我的虚拟网卡的 vring 里放什么格式"，guest 还是要为每个厂商写一个驱动。

**解法**：**规范统一分配设备类型编号**
（`include/uapi/linux/virtio_ids.h`），并为每个类型规定
"配置空间的布局"和"每个队列的用途"。

⇒ **`virtio_dev_match()`（`drivers/virtio/virtio.c:85`）只比两个字段**：
`device`（类型）和 `vendor`。**驱动的 `id_table` 里通常写
`{ VIRTIO_ID_NET, VIRTIO_DEV_ANY_ID }` ——只认类型，不挑厂商**（03 §A3）。

**这就是 virtio 存在的意义**：**一个 `virtio_net` 驱动，
跑在 QEMU / Firecracker / Hyper-V / 硬件卸载网卡上都一样。**

### §2.4 第三层解法：feature 协商

⚠️ **新问题**：规范在演化，而**两端的版本可能不一致**。
新内核可能跑在老 hypervisor 上（云上的旧宿主机），也可能反过来
（新宿主机 + 老 guest 镜像）。

**不能用"版本号"解决**——因为能力不是线性累积的：
某个 hypervisor 可能实现了功能 C 但没实现 B。

**解法：feature 位 + 取交集 + 一次确认**
- **双方各报一份 64 位的能力清单**
- **核心取交集**（`drivers/virtio/virtio.c:273`）
- **写回设备，然后读回来确认**（`:194-198`）——**设备可以拒绝**

**为什么要多一步确认**：feature 位之间**有依赖**。
设备可能"单独支持 A、单独支持 B，但 A+B 一起不行" ——
**取交集算不出这种约束，只有设备自己知道**。
08 §5 用"外语能力清单"讲了这件事。

⚠️ **64 位是硬上限**（`vdev->features` 是 `u64`，
`include/linux/virtio.h:126`），执行点是两处
`BUG_ON(f >= 64)`（`drivers/virtio/virtio.c:256` / `:265`）
⇒ **位号超 63 直接崩机器**（07 §2.3.2）。

### §2.5 规范版本史：从 feature 位号就能读出来

**`include/uapi/linux/virtio_config.h` 里那串位号本身就是一部演化史**：

| 位号 | 名字 | 位置 | 属于哪一代 |
|---|---|---|---|
| 27 | `VIRTIO_F_ANY_LAYOUT` | `include/uapi/linux/virtio_config.h:63` | **legacy（0.9.X 草案）时代** |
| **32** | **`VIRTIO_F_VERSION_1`** | `include/uapi/linux/virtio_config.h:67` | **1.0 的分界线** ——协商上它就是"现代设备" |
| 33 | `VIRTIO_F_ACCESS_PLATFORM` | `include/uapi/linux/virtio_config.h:76` | 1.1 —— **走平台 DMA 规则**（IOMMU / 机密计算） |
| 34 | `VIRTIO_F_RING_PACKED` | `include/uapi/linux/virtio_config.h:83` | **1.1 的招牌特性**（08 §9） |
| 35 | `VIRTIO_F_IN_ORDER` | `include/uapi/linux/virtio_config.h:89` | 1.1 |
| 36 | `VIRTIO_F_ORDER_PLATFORM` | `include/uapi/linux/virtio_config.h:95` | 1.1 |
| 37 | `VIRTIO_F_SR_IOV` | `include/uapi/linux/virtio_config.h:100` | 1.1 |
| **40** | `VIRTIO_F_RING_RESET` | `include/uapi/linux/virtio_config.h:105` | **较新** ——`virtqueue_resize()` 要它（03 §B6） |

**两条能直接读出来的结论**：

1. **32 号位是分水岭**：`VIRTIO_F_VERSION_1` 之前的位号（27 等）
   属于 legacy 草案时代，之后的属于正式规范
2. **位号有空洞**（38、39 没用）⇒ **规范在预留/废弃位号**，
   **而只有 64 个位可用**（§2.4）⇒ 越用越紧张

⚠️ **还有一处改名**（skill 规则 24 的形式之一）：

```c
/* Legacy name for VIRTIO_F_ACCESS_PLATFORM (for compatibility with old userspace) */
#define VIRTIO_F_IOMMU_PLATFORM		VIRTIO_F_ACCESS_PLATFORM
```
—— `include/uapi/linux/virtio_config.h:78-79`

**形式是 `#define` 别名** ⇒ **不会分叉**（和 timer 的
`del_timer` → `timer_delete` 那种"平行 inline"不同，那种会分叉）。
**注释明确说保留是为了老用户态**。

### §2.6 ⚠️ 字节序：一条 2014 年留下的疤

**legacy 时代没有规定字节序** ——双方各按**宿主字节序**（guest-native）读写。
**1.0 起强制小端。**

⇒ **所以 `__virtio16` / `__virtio32` / `__virtio64` 这三个类型
不是"小端 N 位"，而是"端序取决于协商结果的 N 位"**。

| 设备 | 共享内存里的字节序 |
|---|---|
| **协商上 `VIRTIO_F_VERSION_1`** | **一律小端** |
| ⚠️ **legacy（0.9.X）** | ⚠️ **宿主字节序** ——大端机器上就是大端 |

**判断函数**：`virtio_is_little_endian(vdev)`
（`include/linux/virtio_config.h:333`，实现在 `:335`）。
**转换必须带 `vdev`**：`virtio16_to_cpu(vdev, val)`（`:340`）。

⚠️ **arm64 上这个坑不会暴露**（arm64 通常小端，现代设备也小端）
⇒ **`le16_to_cpu()` 和 `virtio16_to_cpu()` 结果相同**
⇒ **又一条"在你的平台上不会出错但仍必须遵守"的规则**（05 §4、08 §8）。

**packed ring 干脆不给选择**：它的描述符字段是 `__le64/32/16`
（`include/uapi/linux/virtio_ring.h:239-248`），
**而 split ring 是 `__virtio64/32/16`**（`:107-112`）——
**因为 packed 是 1.1 才有的，那时已经强制小端了**（02 §1.0.D）。

### §2.7 uapi 边界：哪些文件不能改

**两套同名头文件是本子系统最容易造成实质错误的一组区别**（02 §1.0.C）：

| | `include/linux/virtio_*.h` | `include/uapi/linux/virtio_*.h` |
|---|---|---|
| 数量 | 12 | **27** |
| 内容 | Linux 内部辅助 | **规范的 C 表达** |
| 谁定义 | 内核社区 | **OASIS 规范** |
| 改它的后果 | 重新编译 | ⚠️ **和对端不兼容** |

⚠️ **四个名字在两边都存在**：`virtio_config.h` · `virtio_net.h` ·
`virtio_ring.h` · `virtio_console.h`。

**判据**：结构体里出现 `__virtio16` / `__le16` 这类**显式端序类型**的
一定是 uapi 侧；用 `u16` 的是内核内部侧。

### §2.8 对端在哪，本套覆盖到哪为止

⚠️ **这是跨边界 ABI 型子系统最要紧的一条声明。**

**"对端"因传输层而异**（01 §1.1 的 9 个实现）：

| 传输层 | 对端是什么 | 在本树里吗 |
|---|---|---|
| PCI / MMIO | **hypervisor**（QEMU / Firecracker / …） | **不在** |
| CCW | s390 的 hypervisor | 不在 |
| vDPA | **硬件**（网卡固件） | **不在** |
| remoteproc | **远端固件**（协处理器上的 RTOS） | 不在 |
| UML | **用户态进程** | 不在 |
| ⚠️ vhost | **Linux 内核自己**（`drivers/vhost/`） | **在！**（04 §4） |

⇒ **"对端不在本树"有一个例外**：vhost 场景下对端就是
`drivers/vhost/vringh.c`(1511 行)。**但本套没有读它** ——
它实现的是 **device 角色**，超出本套"只覆盖 driver 侧"的边界。

**本套的覆盖边界**（99 §5.5 有第五层专门登记）：

| 内容 | 覆盖 |
|---|---|
| Linux 侧的 driver 角色 | **完整覆盖** |
| **共享内存布局** | **完整覆盖**（02 §5）——**这一节对两边都成立** |
| **协议时序**（对端何时该做什么） | ⚠️ **不覆盖** ——去读 OASIS 规范 |
| **对端的实现** | ⚠️ **不覆盖** |

**所以凡是本套写"对端会…"的地方，依据都是
规范文本或代码里的注释，不是本树的代码。**

### §2.9 两层可插拔是怎么来的

**不是设计出来的，是两个正交需求叠出来的**：

| 需求 | 产物 |
|---|---|
| **"一个驱动跑所有厂商"**（§2.3） | **层 2：`virtio_driver`** ——按设备类型匹配 |
| **"同一个设备类型能跑在不同虚拟化传输上"** | **层 1：`virtio_config_ops`** ——PCI / MMIO / CCW / vDPA / … |

⇒ **两个维度独立** ⇒ 必须是两层，不能合并。

**代价**（03 §C3）：
- 17 个回调里**只有 8 个必填** ⇒ 驱动要处理"这个传输层不支持某功能"
- **五个"环境探测结果"字段**：`use_dma_api` / `weak_barriers` /
  `event` / `indirect` / `packed_ring`（02 §6.3）
- **测试矩阵是 9 × 25**

### §2.10 累积的结果

```
问题                              解法                        产物
─────────────────────────────────────────────────────────────────────
设备模拟太慢（十几次 vmexit）  →  共享内存 + 通知           →  vring
                                                             + notify
每个厂商一个驱动               →  规范统一设备类型编号       →  virtio_ids.h
                                                             + 层 2（virtio_driver）
两端版本不对称                 →  feature 位取交集 + 确认    →  64 位 features
                                                             + 写-回读握手
一个设备类型要跑在多种虚拟化上  →  抽象控制面操作            →  层 1（virtio_config_ops）
对端不可信                     →  影子字段 + token + 校验    →  desc_state / shadow
                                                             / id 校验
legacy 兼容                    →  两张 feature 表           →  ⚠️ 字节序不确定
                                  + 两套 PCI 库                + 约 1000 行代码
三段内存的缓存不友好           →  一段环 + flags 里两个 bit  →  packed ring
```

⇒ **每一层都在解一个上一层解不了的问题；
唯一"纯负债"的是 legacy 兼容那一行**（03 §C5）。

---

## §3 概念模型

### §3.1 一张图看全

```
   ┌──────────────── Linux 侧（本树，本套覆盖）─────────────────┐
   │  25 个设备驱动（virtio_net / blk / console / …）  ← 层 2    │
   │       │ 只调 virtqueue_* / virtio_cread / virtio_has_feature│
   │  ┌────▼─────────────────────────────────────────┐         │
   │  │ virtio_bus（virtio.c:343）真总线            │         │
   │  │ virtio.c(577)      匹配 · feature 协商 · 状态编排│         │
   │  │ virtio_ring.c(2876) vring · virtqueue_* 全套    │         │
   │  └────┬──────────────────────────┬───────────────┘         │
   │       │ virtio_config_ops         │ 直接读写共享内存         │
   │  ┌────▼─────────────┐             │                        │
   │  │ 9 个传输层 ← 层 1 │             │                        │
   │  │ PCI/MMIO/CCW/vDPA│             │                        │
   │  └────┬─────────────┘             │                        │
   └───────┼───────────────────────────┼────────────────────────┘
           │ 通知（唯一的陷入）          │
   ════════▼═══════════════════════════▼═════════════════════════
                          ┌──────────────────┐
                          │  vring       │ ← 共享内存
                          │  （02 §5 字节布局）│   双方都读写
                          └──────────────────┘
           │
   ┌───────▼─────────────────────────────────────────────────────┐
   │  对端（不在本树，除 vhost）                              │
   │  hypervisor · 远端固件 · 用户态进程 · 硬件                    │
   └─────────────────────────────────────────────────────────────┘
```

### §3.2 三条要记住的边

| 边 | 含义 | 落点 |
|---|---|---|
| **数据面直连共享内存** | **不经传输层** ——这是 virtio 高性能的根本 | `virtio_ring.c` 直接读写 vring |
| **控制面走 `virtio_config_ops`** | 17 个回调，8 个必填 | `include/linux/virtio_config.h:95-121` |
| **通知是唯一的陷入** | `notify` 函数指针 | `drivers/virtio/virtio_ring.c:200`（注释带 `FIXME: commonalize hcalls!`） |

### §3.3 三个"不可信"的应对

| 层 | 机制 | 代码 |
|---|---|---|
| ① 影子字段 | "我上次写了什么"另存一份 | `avail_flags_shadow`(`:94`) / `avail_idx_shadow`(`:100`) |
| ② token 机制 | 对端只能说"第 N 项完成"，不能说"这是哪个对象" | `desc_state` / `desc_extra`（`:103-104`） |
| ③ id 校验 | 越界的 `used_elem.id` ⇒ 标 broken | `BAD_RING()`（`:57-62`） |

⚠️ **有一个 token 机制保护不了的地方**：`virtqueue_get_buf(vq, &len)`
的 `len` 是**对端写的** ⇒ **驱动必须自己检查它没超过提交时的缓冲区大小**
（08 §6 的误导点 3）。

---

## §4 数据结构速览

**七个结构体撑起整个子系统**（详见 02 §6）：

| 结构体 | 位置 | 一句话 | 关键字段 |
|---|---|---|---|
| `struct virtio_device` | `include/linux/virtio.h:114-128` | **12 个字段，很小** | `dev`（内嵌 device）· `id` · `config`（指向 9 个 ops 之一）· `features`（**u64，上限 63 位**）· `priv`（**驱动的**） |
| `struct virtio_driver` | `include/linux/virtio.h:175-191` | 层 2 的 ops | `id_table` · `feature_table` · `validate`（**唯一能改 feature 的钩子**）· `probe` · `remove` |
| `struct virtqueue` | `include/linux/virtio.h:29-39` | **9 个字段，⚠️ 无锁** | `callback` · `name`（**会出现在中断名里**）· `num_free` · `priv`（⚠️ **传输层的**） |
| `struct vring_virtqueue` | `drivers/virtio/virtio_ring.c:154` | **`virtqueue` 的真身** | `packed_ring`(`:158`) · `use_dma_api`(`:161`) · `weak_barriers`(`:164`) · `broken`(`:167`) · `free_head`(`:176`) · `last_used_idx`(`:186`) · `union { split; packed; }`(`:191-197`) |
| `struct virtio_config_ops` | `include/linux/virtio_config.h:95-121` | **层 1 的 ops，17 个回调** | `get`/`set` · `get_status`/`set_status` · `reset` · `find_vqs`/`del_vqs` · `get_features`/`finalize_features`（**这 9 个必填**） |
| `struct vring_desc` | `include/uapi/linux/virtio_ring.h:107-112` | **共享内存，16 字节** | `__virtio64 addr` · `__virtio32 len` · `__virtio16 flags` · `__virtio16 next` |
| `struct vring_used_elem` | `include/uapi/linux/virtio_ring.h:121-127` | **共享内存，8 字节** | `__virtio32 id` · `__virtio32 len`（两者都是**对端写的**） |

### §4.1 vring 的字节布局（02 §5 的浓缩）

```
  num = N（必须是 2 的幂）

  0              16×N     struct vring_desc desc[N]         驱动写 / 对端读
  16N            2        __virtio16 avail_flags            驱动写
  16N+2          2        __virtio16 avail_idx              驱动写
  16N+4          2×N      __virtio16 available[N]           驱动写
  16N+4+2N       2        __virtio16 used_event_idx         ⚠️ 对端写！
  ── pad 到 align 边界 ──
  (aligned)      2        __virtio16 used_flags             对端写
  +2             2        __virtio16 used_idx               对端写
  +4             8×N      struct vring_used_elem used[N]    对端写 / 驱动读
  +4+8N          2        __virtio16 avail_event_idx        ⚠️ 驱动写！
```

**两个 ⚠️ 标记的字段是"插在对方段末尾"的**，
头文件原话（`include/uapi/linux/virtio_ring.h:194-195`）：
`They are at the end for backwards compatibility.`
⇒ **访问宏是 `vring_used_event(vr)`（`:196`）和
`vring_avail_event(vr)`（`:197`），都用 `[(vr)->num]` 越过环末尾**（08 §4）。

**三个对齐常量**：`VRING_DESC_ALIGN_SIZE`=**16**（`:94`）·
`VRING_AVAIL_ALIGN_SIZE`=**2**（`:92`）· `VRING_USED_ALIGN_SIZE`=**4**（`:93`），
**用 `typedef` + `aligned` 施加**（`:151`/`:153`/`:155`）——
因为**只有 typedef 形式能"降低"对齐**（02 §5.2）。

### §4.2 关键的标志位

| 标志 | 值 | 位置 | 谁写 |
|---|---|---|---|
| `VRING_DESC_F_NEXT` | 1 | `include/uapi/linux/virtio_ring.h:41` | 驱动 |
| `VRING_DESC_F_WRITE` | 2 | `:43` | 驱动（`add_inbuf` 会设） |
| `VRING_DESC_F_INDIRECT` | 4 | `:45` | 驱动 |
| `VRING_USED_F_NO_NOTIFY` | 1 | `:57` | **对端**（写在 used 段） |
| `VRING_AVAIL_F_NO_INTERRUPT` | 1 | `:61` | **驱动**（写在 avail 段） |
| ⚠️`VRING_PACKED_DESC_F_AVAIL` / `_USED` | **7 / 15** | `:51` / `:52` | ⚠️ **是 bit 号不是掩码** |

**规律**：**每一方在"自己的段"里声明"我不想被打扰"**（02 §5.4）。

### §4.3 状态字节的六个位

| 位 | 值 | 位置 | 谁设 |
|---|---|---|---|
| `ACKNOWLEDGE` | 1 | `include/uapi/linux/virtio_config.h:36` | 核心 |
| `DRIVER` | 2 | `:38` | 核心 |
| `DRIVER_OK` | 4 | `:40` | 驱动/核心（`virtio_device_ready()`） |
| `FEATURES_OK` | 8 | `:42` | 核心（**要回读确认**） |
| ⚠️`NEEDS_RESET` | 0x40 | `:44` | ⚠️ **对端** ——唯一由对端设的位 |
| `FAILED` | 0x80 | `:46` | 核心/驱动 |

**正常累积**：`0x00 → 0x01 → 0x03 → 0x0b → 0x0f`
⇒ **`/sys/bus/virtio/devices/virtioN/status` 不是 `0x0000000f` 就说明卡住了**（07 §3.2）。

---

## §5 机制速览

### §5.1 一次数据往返

```
① virtqueue_add_outbuf(vq, sg, num, data, gfp)     data 是你的 token
     ├─ 从 free_head 摘描述符
     ├─ 填 desc[i]，用 VRING_DESC_F_NEXT 串链
     ├─ 写 avail->ring[avail->idx % num]
     └─ 写屏障（virtio_wmb），然后 avail->idx++
② virtqueue_kick(vq)
     ├─ kick_prepare(): 看 NO_NOTIFY / EVENT_IDX  ← 可能不用通知
     └─ notify(): 写寄存器  ← 唯一的陷入
   ══════ 对端处理（不在本树）══════
③ 中断 → 传输层 → vq->callback(vq)
④ virtqueue_get_buf(vq, &len) 循环到 NULL
     ├─ 比 last_used_idx 和 used->idx（读屏障）
     ├─ 取 used->ring[] 的 id，校验不越界
     ├─ 描述符还回 free_head
     └─ 返回当初的 data
```

⚠️ **三个顺序约束**（03 §B1）：先写 `ring[]` 再写 `idx`；
写 `idx` 前要写屏障；读 `idx` 后要读屏障。

### §5.2 屏障强度由传输层决定

| `weak_barriers` | 用什么 | 什么时候 |
|---|---|---|
| **true** | `virt_*mb()`（轻量） | **对端是同机软件** |
| **false** | `*mb()`（真屏障） | **对端可能是硬件**（vDPA） |

**四个原语**：`virtio_wmb`（`include/linux/virtio_ring.h:41`）·
`virtio_rmb`（`:33`）· `virtio_mb`（`:25`）· `virtio_store_mb`（`:49`）。

⇒ **这个字段的存在证明"对端是什么"会改变 Linux 侧的代码路径**（03 §B1.2）。

### §5.3 DMA：有时候不走 DMA API

`vring_use_dma_api()`（`drivers/virtio/virtio_ring.c:268`）：

| 条件 | 结论 |
|---|---|
| 协商上 `VIRTIO_F_ACCESS_PLATFORM` | **走 DMA API** |
| `xen_domain()`（`:282`） | **必须走** |
| 都不满足 | **不走，直接用物理地址** |

⚠️ **`virtio_has_dma_quirk()` 的命名是反的**：返回真 = **有 quirk** =
**不走** DMA API 的老行为（`include/linux/virtio_config.h:204`）。

### §5.4 probe 的 12 步（03 §A4 的浓缩）

```
  register_virtio_device()   virtio.c:418  → reset → +ACKNOWLEDGE → device_add()
  virtio_dev_probe()         virtio.c:237
    +DRIVER(:247) → get_features(:250) → 取交集(:273/:275)
    → finalize_features 第一次(:282)
    → validate(:289) → 改了就再 finalize(:295)
    → features_ok(:301) → probe(:305) → +DRIVER_OK(:310) → scan(:313)
    ✗ 失败 ⇒ +FAILED(:320)
```

⚠️ **`finalize_features()` 调两次**，`validate()` 夹在中间 ⇒
**`validate()` 是唯一"改了还会被写回设备"的钩子**（01 §4.3）。

### §5.5 写-回读握手

`virtio_features_ok()`（`drivers/virtio/virtio.c:171-202`）：
写 `FEATURES_OK`（`:194`）→ 读回（`:195`）→ 位没了就
`dev_err("virtio: device refuses features: %x")`（`:197`）+ `-ENODEV`。

⚠️ **legacy 设备跳过这个握手**（`:191` 直接 `return 0`）⇒ **只能盲信**。

### §5.6 ⚠️ 锁：数据面完全不加

| 有锁的 | 无锁的 |
|---|---|
| `vdev->config_lock` · `vdev->vqs_list_lock`（`include/linux/virtio.h:119-120`） | ⚠️ **`virtqueue` / `vring_virtqueue` 一把都没有** |

**代码原话**：`Caller is supposed to guarantee no reentry.`
（`drivers/virtio/virtio_ring.c:26`）
**DEBUG 下 `START_USE()` 重入就 `panic()`**（`:27-33`，`in_use` 记 `__LINE__`）。

⇒ **同一个 vq 的并发要驱动自己挡**（05 §1.3 列了五种情况）。

### §5.7 两套内存布局

| | split ring | packed ring |
|---|---|---|
| 段数 | 3 | **1** |
| 归属 | 两个游标 | **flags 的 bit 7 / 15** |
| 端序类型 | `__virtio*`（随协商） | **`__le*`（无条件小端）** |
| 创建 | `vring_create_virtqueue_split()`（`:1101`） | `vring_create_virtqueue_packed()`（`:1996`） |
| 怎么选 | `virtio_has_feature(vdev, VIRTIO_F_RING_PACKED)`（`:2559` 一带） | 同上 |

### §5.8 自我保护

| 机制 | 抓什么 | 默认行为 | DEBUG 行为 |
|---|---|---|---|
| `BAD_RING()` | 环被搞坏 | **标 broken**（`:57-62`） | **`BUG()`**（`:20-25`） |
| `START_USE()` | 并发重入 | **无**（空宏） | **`panic()`** |
| `LAST_ADD_TIME` | **提交了 100ms 内没 kick** | **无** | `WARN_ON` |
| `CONFIG_VIRTIO_HARDEN_NOTIFICATION` | 对端在 `DRIVER_OK` 前发中断 | **静默 `IRQ_HANDLED`** | — |

⚠️ **默认配置下前三个几乎什么都不做** ⇒ **这是本子系统最大的
可观测性缺口**（§7、07 §1）。

---

## §6 端到端调用路径

### §6.1 一个网络包发出去（概念路径）

```
  上层 → virtio_net 的 xmit
    ├─ sg_init_table / skb_to_sgvec
    ├─ virtqueue_add_outbuf(sq->vq, sg, num, skb, GFP_ATOMIC)
    │    └─ 返回 -ENOSPC ⇒ 停 queue，等回收（不是错误）
    ├─ virtqueue_kick_prepare(sq->vq)
    │    └─ 为假 ⇒ 不通知，直接返回
    └─ virtqueue_notify(sq->vq)
         └─ 传输层写寄存器（MMIO: VIRTIO_MMIO_QUEUE_NOTIFY = 0x050）
  ══════ 对端取走并发送 ══════
  中断 → 传输层中断处理 → sq->vq->callback()
    └─ while (skb = virtqueue_get_buf(sq->vq, &len))
         └─ dev_kfree_skb(skb)   拿回的就是当初给的 skb
```

### §6.2 probe 到能收发（arm64 + MMIO）

```
  DT: compatible = "virtio,mmio"（06 §4 的真实节点）
    └─ platform probe → virtio_mmio_driver（virtio_mmio.c:831）
         ├─ ioremap reg
         ├─ 读 MAGIC(0x000) 校验 ─ 失败打 "Wrong magic value 0x%08lx!"（:625）
         ├─ 读 VERSION(0x004) ─ 不是 1/2 打 "Version %ld not supported!"（:633）
         ├─ 读 DEVICE_ID(0x008) → 填 vdev->id.device
         ├─ vdev->config = &virtio_mmio_config_ops（:549）
         └─ register_virtio_device(&vdev)
              └─ §5.4 的 12 步
                   └─ drv->probe() → virtio_find_vqs()
                        └─ config->find_vqs() → vring_create_virtqueue()
                             ├─ 分配 vring_size() 字节
                             ├─ vring_init() 算三段偏移
                             └─ 写 QUEUE_DESC/AVAIL/USED_LOW/HIGH(0x080-0x0a4)
                   └─ virtio_device_ready() → +DRIVER_OK
```

⚠️ **MMIO 只有一个中断**（06 §2.2）⇒ 中断处理要读
`INTERRUPT_STATUS`(0x060) 判断是队列事件还是配置变更。

---

## §7 子系统关系

| 子系统 | 关系 | 落点 |
|---|---|---|
| **PCI** | **最主要的传输层** | `virtio_pci_*.c`（5 个文件 2418 行） |
| **of（设备树）** | **MMIO 的发现路径 + `virtio,deviceNN` 子节点** | `virtio_mmio_match[]`(`:817`) · `virtio_device_of_init()`(`virtio.c:366`) |
| **DMA / IOMMU** | **`VIRTIO_F_ACCESS_PLATFORM` 决定走不走 DMA API** | `vring_use_dma_api()`(`:268`) |
| **中断** | PCI 用 MSI-X（每队列一个向量），MMIO 只有一根线 | `virtio_pci_common.c` · 06 §2.2 |
| **vhost** | ⚠️ **对端可以是 Linux 自己** | `drivers/vhost/vringh.c`(1511)，本套未读（04 §4） |
| **vDPA** | 对端是硬件 ⇒ **强屏障 + 必须 DMA API** | `virtio_vdpa.c`(417) |
| **remoteproc** | **对端是远端固件** | `drivers/remoteproc/remoteproc_virtio.c:327` |
| **网络 / 块 / 文件系统 …** | **22 个目录里的 25 个驱动** | 01 §1.2 |
| **机密计算**（AMD SEV / s390 PV） | **强制 `ACCESS_PLATFORM`** | `virtio_anchor.c`(18 行)；⚠️ **arm64 上不走**（04 §9.2） |
| **Xen** | 通过 anchor 的另一条路（改 DMA ops） | `drivers/xen/grant-dma-ops.c` |

**最紧的三条**：PCI/MMIO（传输）、of（发现）、DMA（地址转换）。

---

## §8 导读：我该看哪一篇

| 你的处境 | 先读 | 再读 |
|---|---|---|
| **完全不懂 virtio** | **08 篇**（九个话题） | §2（背景）→ 02 §1.0 |
| **要写一个 virtio 设备驱动** | **05 §1** | 01 §2–§5 → 02 §5 |
| **要看共享内存怎么排** | **02 §5** | 08 §3 §4 |
| **协商失败 / 设备起不来** | **07 §3.2**（看 `status`） | 07 §2.2 → 03 §A5 |
| **数据不动** | **05 §8** | 07 §9 |
| **偶发数据错乱** | **07 §4**（开 DEBUG） | 02 §7.2 → 05 §1.3 |
| **写 DT** | **06 §4**（真实节点）+ **06 §7**（子节点） | 06 §1 |
| **要写传输层** | **05 §5** | 01 §6 → 04 §2 |
| **要改 virtio 核心** | 02 → 03 → 04 | **99 §5** |
| **隔几个月回来复习** | **只读本篇** | — |

### §8.1 全篇最该记住的七条

1. **两层可插拔**：传输层（9 个 `virtio_config_ops`）+ 设备驱动层
   （25 个 `virtio_driver`）——**这是 virtio 和 timer/of 最大的结构差别**（§1）
2. **数据面零陷入，只有通知会陷入**（§2.2、08 §2）——virtio 存在的全部理由
3. **共享内存里的一切都不可信**：影子字段 + token + id 校验（§3.3）；
   ⚠️ **但 `len` 要你自己检查**（08 §6）
4. **`validate()` 是唯一"改了 feature 还会被写回设备"的钩子**（§5.4）
5. ⚠️ **`remove()` 里必须 `virtio_reset_device()`**，否则 `WARN_ON_ONCE`
   （`drivers/virtio/virtio.c:335`、05 §7.5）
6. ⚠️ **框架不给 virtqueue 加锁** ——同一个 vq 的并发是你的事（§5.6）
7. ⚠️ **字节序在 arm64 上不会暴露** ——判据是查规范不是查平台（§2.6、08 §8）

### §8.2 ⚠️ 三条容易被忽略的硬约束

| 约束 | 后果 |
|---|---|
| **feature 位号 ≤ 63** | `BUG_ON(f >= 64)` ×2（`drivers/virtio/virtio.c:256` / `:265`）⇒ **直接崩** |
| **查 feature 前必须在 `feature_table` 里声明** | `BUG()`（`drivers/virtio/virtio.c:122`）⇒ **直接崩** |
| **DT 子节点只能有一个，且 `compatible` 用十六进制** | 多了 `WARN`（`:381`）；**写错十进制则完全静默**（06 §7.4、§9.3） |

### §8.3 ⚠️ 本套的覆盖边界（再强调一次）

**本套只覆盖 Linux 的 driver 侧。**
**共享内存布局（02 §5）对两边都成立**，
**但"对端何时该做什么"不在本套** ——去读 OASIS VIRTIO 规范。
⇒ **99 §5.5 有专门的第五层未验证项登记这条边界。**
