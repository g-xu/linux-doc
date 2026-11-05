---
title: 数据结构
---

# virtio 02：数据结构与关系

本篇先破歧义（§1.0），再画关系（§2、§3、§8），
**然后画跨边界共享内存的字节级布局（§5，这是本子系统特有的强制项）**，
最后逐个看结构体（§6）。

---

## §1.0 先分清六组关系

### §1.0.A `virtio_bus` 是真总线——但"总线的另一端"不是硬件

**最常见的错误类比**：把 virtio 当成 PCI/I2C 那样的硬件总线。

**先说清它**真的**是什么**：`drivers/virtio/virtio.c:343` 的 `virtio_bus`
是一条**完整的驱动模型总线**——有 `.match`、有 probe、有 25 个真正的
`device_driver` 绑在上面（01 §1.2）。

⚠️ **和 timer 那两个伪总线形成鲜明对照**：

| | virtio 的 `virtio_bus` | timer 的 `clocksource_subsys` |
|---|---|---|
| 有 driver 绑上去吗 | **有，25 个** | **一个都没有** |
| 有 `.match` 吗 | **有** | **没有** |
| 走 probe 流程吗 | **走** | **不走** |
| 落点 | `/sys/bus/virtio/` | `/sys/devices/system/clocksource/` |
| 判定结论 | **真总线** | **纯 sysfs 容器** |

⇒ **所以类型判定的门 1 在 virtio 上是对的，在 timer 上会误判**（99 §1.3）。

**但它和硬件总线的根本差别在于"另一端是什么"**：

| | PCI / I2C | virtio |
|---|---|---|
| 另一端 | **硬件**（有电气规范） | **另一个软件实体**（hypervisor / 远端固件 / 用户态进程 / 硬件卸载） |
| 协议定义在 | 硬件手册 | **树外的 OASIS VIRTIO 规范** |
| 通信介质 | 导线 | **共享内存 + 一个通知机制** |
| 字节序 | 硬件规定，固定 | ⚠️ **取决于 feature 协商结果**（§1.0.E、01 §4.4） |
| 本树能看到另一端吗 | 不需要 | ⚠️ **看不到**（99 §5.5 的第五层） |

### §1.0.B `virtio_device` / `virtio_driver` / `virtqueue` 三者关系

**错误类比**：以为 `virtqueue` 是 `virtio_device` 的一个子设备。

| | `struct virtio_device` | `struct virtio_driver` | `struct virtqueue` |
|---|---|---|---|
| 是 `struct device` 吗 | **是**（内嵌 `dev`，`include/linux/virtio.h:121`） | **不是**，内嵌 `device_driver`（`:176`） | **不是**——**它根本不在驱动模型里** |
| 谁创建 | **传输层** | 设备驱动（静态定义） | **核心，在 `find_vqs()` 里** |
| 数量关系 | 一个设备 | 一个驱动匹配 N 个设备 | **一个设备 N 个队列** |
| 挂在哪 | `virtio_bus` | 注册到 `virtio_bus` | **`vdev->vqs` 链表**（`include/linux/virtio.h:125`） |
| 结构体位置 | `include/linux/virtio.h:114-128` | `include/linux/virtio.h:175-191` | `include/linux/virtio.h:29-39` |

**为什么 `virtqueue` 不是设备**：它不需要被匹配、不需要被 probe，
它只是**一条数据通道**。⇒ **所以 sysfs 里看不到 virtqueue**，
只能靠 `/proc/interrupts` 里的中断名和 debugfs 间接观察（07 篇）。

⚠️ **两个 `priv` 字段是一组真实的同名陷阱**：

| 字段 | 位置 | 归谁 |
|---|---|---|
| `vdev->priv` | `include/linux/virtio.h:127` | **设备驱动的私有数据** |
| ⚠️`vq->priv` | `include/linux/virtio.h:37` | ⚠️ **传输层的私有数据** |

⇒ **设备驱动往 `vq->priv` 里写东西会踩坏传输层的状态。** 05 §7 有独立一节。

### §1.0.C `include/linux/virtio_*.h` vs `include/uapi/linux/virtio_*.h`

**这是本子系统最容易造成实质错误的一组区别**（01 §0.5 已列表，这里讲后果）。

| | `include/linux/` | `include/uapi/linux/` |
|---|---|---|
| 数量 | 12 个 | **27 个** |
| 内容 | Linux 内部辅助 | **跨边界 ABI（规范的 C 表达）** |
| 改它的后果 | 重新编译即可 | ⚠️ **和对端不兼容** |
| 谁定义 | 内核社区 | **OASIS 规范** |

⚠️ **四个名字在两边都存在**：`virtio_config.h` · `virtio_net.h` ·
`virtio_ring.h` · `virtio_console.h`。

**判据**：结构体里出现 `__virtio16` / `__virtio32` / `__virtio64` / `__le*`
这类**显式端序类型**的，一定是 uapi 侧；用 `u16`/`u32` 的是内核内部侧。

### §1.0.D split ring vs packed ring —— 两种共享内存布局

**同一个 `virtqueue` 抽象下有两套完全不同的内存布局**：

| | split ring（传统） | packed ring（1.1 新增） |
|---|---|---|
| 有几段内存 | **三段**：desc / avail / used | **一段**：一个环 |
| 描述符结构 | `struct vring_desc`（`include/uapi/linux/virtio_ring.h:107`） | `struct vring_packed_desc`（`:239`） |
| 描述符大小 | **16 字节** | **16 字节**（巧合，字段不同） |
| ⚠️ **字节序类型** | **`__virtio64/32/16`** ⇒ **取决于协商** | **`__le64/32/16`** ⇒ **无条件小端** |
| 缓存行为 | 差（三段分别读写） | **好**（一段，方向靠 flags 里的两个 bit 区分） |
| 代码里怎么区分 | `vring_virtqueue.packed_ring`（`drivers/virtio/virtio_ring.c:158`） | 同上 |
| 数据成员 | **一个 `union`**（`drivers/virtio/virtio_ring.c:191-197`） | 同上 |

**那个"字节序类型不同"是可以逐字验证的**：
`struct vring_desc` 的四个字段是 `__virtio64 addr` / `__virtio32 len` /
`__virtio16 flags` / `__virtio16 next`（`include/uapi/linux/virtio_ring.h:107-112`），
而 `struct vring_packed_desc` 是 `__le64 addr` / `__le32 len` /
`__le16 id` / `__le16 flags`（`include/uapi/linux/virtio_ring.h:239-248`）。

⇒ **packed ring 不支持 legacy 大端模式**——因为 packed 是 virtio 1.1 才有的，
而 1.1 已经强制小端。**这解释了为什么两套布局的类型不一样，
不是笔误。**

**`packed_ring` 是一个 `bool`，两套布局在一个 `union` 里**
（`drivers/virtio/virtio_ring.c:191-197`）⇒ **运行期二选一，不共存**。

### §1.0.E feature 位 vs 状态字节 —— 两套完全不同的握手

**错误类比**：以为它们是一回事（都是"设备和驱动互相通气"）。

| | feature 位 | 状态字节（status） |
|---|---|---|
| 回答什么问题 | **"你支持哪些能力"** | **"我们进行到哪一步了"** |
| 宽度 | **64 位**（`vdev->features`，`include/linux/virtio.h:126`） | **8 位** |
| 什么时候定 | **probe 之前一次性协商完，之后不变** | **整个生命周期不断变** |
| 谁写 | 双方各报一份，核心取交集 | **驱动写，设备读** |
| 访问方式 | `virtio_has_feature()` | `config->get_status()` / `set_status()` |
| 出错时 | — | **加 `VIRTIO_CONFIG_S_FAILED`**（`virtio_add_status()`） |

⚠️ **`features` 只有 64 位 ⇒ feature 位号上限是 63。**
这是规范层面的硬上限，后果是：**新能力越加越少位可用**，
所以规范后来倾向于把新功能放进配置空间而不是新开 feature 位。

### §1.0.F 本子系统有几层可插拔：**2 层**

| 层 | ops 集合 | 实现者住在哪 | 实现数 |
|---|---|---|---|
| **1. 传输层** | `struct virtio_config_ops`（17 个回调，`include/linux/virtio_config.h:95-121`） | **5 个目录** | **9 个** |
| **2. 设备驱动层** | `struct virtio_driver`（`include/linux/virtio.h:175-191`） | **22 个目录** | **25 个** |

⇒ **两层，这是 virtio 和 timer/of 最大的结构差别**（那两个都只有一层）。
**两层之间的交叉是 9 × 25 = 理论上 225 种组合**，
而且**每一种组合都要能工作** ——这就是为什么 virtio 的抽象必须做得这么干净。

**还有第 2.5 层**：两个 PCI 库（`virtio_pci_modern_dev.c` 19 个导出符号、
`virtio_pci_legacy_dev.c` 12 个）是**给传输层复用的**，
它们不是一层 ops，但承担了"PCI 侧的通用逻辑"（01 §0.4）。

---

## §2 核心抽象与三方的关系

```
   ┌─────────────────────── Linux 侧（本树，本套覆盖）───────────────────────┐
   │                                                                        │
   │   25 个设备驱动                                                         │
   │   ┌──────────────────────────────────────────────────┐                 │
   │   │ virtio_net · virtio_blk · virtio_console · …      │  ← struct       │
   │   │ 只调 virtqueue_* / virtio_cread / virtio_has_feature│    virtio_driver│
   │   └────────────────────┬─────────────────────────────┘                 │
   │                        │ probe / remove / config_changed               │
   │   ┌────────────────────▼─────────────────────────────┐                 │
   │   │  virtio_bus（drivers/virtio/virtio.c:343）         │  真总线      │
   │   │  .match 按 virtio_device_id 匹配                   │                 │
   │   └────────────────────┬─────────────────────────────┘                 │
   │   ┌────────────────────▼─────────────────────────────┐                 │
   │   │  virtio 核心                                       │                 │
   │   │  virtio.c(577)      总线 · feature 协商 · probe 编排│                 │
   │   │  virtio_ring.c(2876) vring 实现 · virtqueue_* 全套 │                 │
   │   └───────┬────────────────────────────┬─────────────┘                 │
   │           │ virtio_config_ops           │ 直接读写共享内存               │
   │   ┌───────▼──────────────────┐          │                              │
   │   │ 9 个传输层实现            │          │                              │
   │   │ PCI / MMIO / CCW /       │          │                              │
   │   │ vDPA / remoteproc / UML  │          │                              │
   │   └───────┬──────────────────┘          │                              │
   └───────────┼─────────────────────────────┼──────────────────────────────┘
               │ 通知（写寄存器/发 IPI）        │
   ════════════▼═════════════════════════════▼══════════════════════════════
               │                    ┌────────────────────┐
               │                    │  vring         │  ← 共享内存
               │                    │  （§5 的字节级布局）│    双方都能读写
               │                    └────────────────────┘
               ▼
   ┌───────────────────────── 对端（不在本树）─────────────────────────┐
   │  hypervisor（QEMU/KVM · vhost）· 远端固件 · 用户态进程 · 硬件卸载       │
   │  行为由 OASIS VIRTIO 规范定义，本套只能引规范，不能引代码           │
   └───────────────────────────────────────────────────────────────────────┘
```

**图里最要紧的三件事**：

1. **有两条路通到对端**：一条是"通知"（走传输层，写寄存器），
   一条是"共享内存"（**不经传输层，直接读写 vring**）。
   ⇒ **这是 virtio 高性能的根本原因**：数据不走 hypervisor 陷入，只有通知走。
2. **`virtio_config_ops` 只管控制面**，数据面完全在 `virtio_ring.c` 里。
   ⇒ 这就是为什么 `virtio_ring.c` 有 32 个导出符号而所有传输层都是 0（01 §0.4）。
3. **双横线那条边是本套文档的覆盖边界**（99 §5.5）。

---

## §3 核心操作数据流

### §3.1 一次"提交 → 通知 → 回收"的完整循环

```
① 驱动准备数据                                    你的代码
     sg_init_table / sg_set_buf
     │
② virtqueue_add_outbuf(vq, sg, num, data, gfp)   virtio_ring.c
     │  data 是你的 token，核心只是保管
     ├─ 从 free_head 摘描述符（vring_virtqueue.free_head，:176）
     ├─ 填 desc[i]: addr / len / flags / next
     │    addr 要经过 DMA 映射（use_dma_api，:161）
     ├─ 用 VRING_DESC_F_NEXT(=1) 把多个 sg 串成链
     ├─ 把链头的 index 写进 avail->ring[avail->idx % num]
     └─ 写内存屏障，然后 avail->idx++
        （顺序很关键：先写 ring 再写 idx，对端才不会读到半个）
     │
③ virtqueue_kick(vq)
     ├─ kick_prepare(): 看 used->flags 有没有 VRING_USED_F_NO_NOTIFY(=1)
     │    或者用 EVENT_IDX 机制看 avail_event 到没到
     └─ notify(): 传输层写寄存器 / 发 IPI  ← 唯一一次陷入
     │
     ▼
   ════════ 对端处理（不在本树）════════
     对端读 avail->idx → 取描述符链 → 处理 → 写 used->ring[] → used->idx++
     → 按需要发中断
     ▼
④ 中断 → 传输层的中断处理 → vq->callback(vq)
     │
⑤ 驱动在回调里循环 virtqueue_get_buf(vq, &len)
     ├─ 比较 last_used_idx（:186）和 used->idx
     ├─ 相等 ⇒ 返回 NULL（没有新的）
     ├─ 取 used->ring[last_used_idx % num] 拿到 id 和 len
     ├─ 把描述符链还回 free_head
     └─ 返回当初的 data
```

⚠️ **三个必须记住的顺序约束**（都是共享内存特有的）：

| 约束 | 为什么 |
|---|---|
| **先写 `avail->ring[]`，再写 `avail->idx`** | 对端只看 `idx`；反了对端会读到未初始化的项 |
| **写 `idx` 前要有写屏障** | CPU 乱序会让对端看到 `idx` 更新但 `ring[]` 还是旧的 |
| **读 `used->idx` 后要有读屏障** | 同理，反向 |

**`weak_barriers`（`drivers/virtio/virtio_ring.c:164`）就是在选屏障强度**：
对端是同一台机器上的 hypervisor 时可以用弱屏障（`virt_*` 系列），
对端是真硬件时必须用强屏障（`*mb()`）。
⇒ **这个字段的存在本身就证明"对端是什么"会影响 Linux 侧的代码路径。**

### §3.2 为什么 `get_buf()` 返回的是你给的 token 而不是描述符

**因为描述符是共享内存里的，对端能改。**
如果 `get_buf()` 返回描述符指针，驱动就得相信对端写回来的 `addr`/`len` 是对的
⇒ **恶意或有 bug 的对端可以让驱动访问任意地址**。

**token 机制把这个攻击面关掉了**：驱动记住"第 N 次提交对应我的哪个对象"，
对端只能告诉你"第 N 次完成了"，**不能告诉你"这是哪个对象"**。
⇒ **`used_elem.id` 会被核心校验**（越界的 id 直接标 broken）。

这也是 `struct vring_used_elem` 用 `__virtio32 id`
（`include/uapi/linux/virtio_ring.h:121-127`）而不是指针的原因——
**指针从对端传过来是不可接受的**。

---

## §4 命名约定速查

| 前缀 | 属于 | 例子 |
|---|---|---|
| `virtqueue_*` | **数据通路**（`virtio_ring.c`，32 个导出符号） | `virtqueue_add_outbuf()` · `virtqueue_kick()` · `virtqueue_get_buf()` |
| `virtio_c*`（`cread`/`cwrite`） | **配置空间访问** | `virtio_cread()` · `virtio_cread8()` |
| `virtio_has_*` | feature/能力查询 | `virtio_has_feature()` · `virtio_has_dma_quirk()` |
| `virtio_find_*` | 建立队列 | `virtio_find_vqs()` · `virtio_find_single_vq()` |
| `virtio_device_*` | 设备级动作 | `virtio_device_ready()` · `virtio_device_freeze()` |
| `register_virtio_*` / `unregister_virtio_*` | **注意是前缀不是后缀** | `register_virtio_driver()` · `register_virtio_device()` |
| `vring_*` | **共享内存布局层**（uapi） | `vring_init()` · `vring_size()` · `struct vring_desc` |
| `VRING_*` | **共享内存里的标志位/常量** | `VRING_DESC_F_NEXT` · `VRING_USED_F_NO_NOTIFY` |
| `VIRTIO_*_F_*` | **feature 位** | `VIRTIO_F_VERSION_1` · `VIRTIO_F_ACCESS_PLATFORM` |
| `VIRTIO_CONFIG_S_*` | **状态字节的位** | `VIRTIO_CONFIG_S_FAILED` |
| `VIRTIO_ID_*` | 设备类型编号 | `VIRTIO_ID_NET` · `VIRTIO_ID_BLOCK` |
| `__virtio*` | **端序不定的类型/内部辅助** | `__virtio16` · `__virtio_test_bit()` |
| `virtio_pci_*` / `virtio_mmio_*` | 具体传输层 | — |

⚠️ **三组"名字像但层级不同"的**：

| 容易混的 | 真实归属 |
|---|---|
| `VRING_*` vs `VIRTIO_*_F_*` | **前者是共享内存里的位**（对端直接读），**后者是协商用的 feature 位** ——两套完全不同的位空间 |
| `virtio_ring.h`（两个） | `include/uapi/` 那个是布局定义，`include/linux/` 那个是内核辅助（§1.0.C） |
| `register_virtio_driver()` vs `register_virtio_device()` | **前者设备驱动调，后者传输层调**（01 §2.1） |

**一条很有用的记忆规则**：
**`vring_`/`VRING_` 开头的东西对端都能看见；`virtio_` 开头的多半是 Linux 内部的。**

---

## §5 内存布局图：vring（本篇的核心）

**这一节是跨边界 ABI 型子系统的强制项**：
vring 是**驱动和对端共同读写的一块内存**，所以它的**字节偏移、对齐、
每个字段的字节序、谁写谁读**都必须写清楚——
**任何一处理解错，表现都是"数据莫名错乱"而不是报错。**

### §5.1 split ring 的整体布局

**官方布局定义就在头文件的注释里**
（`include/uapi/linux/virtio_ring.h:170-193`），下面这张图是照它画的：

```
  假设 num = N（规范要求 N 是 2 的幂）
  align = VRING_AVAIL_ALIGN_SIZE 一族，见 §5.2

  偏移            大小        内容                        谁写      谁读
  ────────────────────────────────────────────────────────────────────────
                                                    ┌─ 描述符表 ─┐
  0             16×N       struct vring_desc desc[N]  驱动     对端
                            ├ __virtio64 addr  (8B)
                            ├ __virtio32 len   (4B)
                            ├ __virtio16 flags (2B)
                            └ __virtio16 next  (2B)
  ────────────────────────────────────────────────────────────────────────
                                                    ┌─ available 环 ─┐
  16N           2          __virtio16 avail_flags     驱动     对端
  16N+2         2          __virtio16 avail_idx       驱动   对端
  16N+4         2×N        __virtio16 available[N]    驱动     对端
  16N+4+2N      2          __virtio16 used_event_idx  ⚠️ 对端  驱动
  ────────────────────────────────────────────────────────────────────────
  ...           pad        char pad[]  ← 对齐到下一个 align 边界
  ────────────────────────────────────────────────────────────────────────
                                                    ┌─ used 环 ─┐
  (aligned)     2          __virtio16 used_flags      对端     驱动
  +2            2          __virtio16 used_idx        对端   驱动
  +4            8×N        struct vring_used_elem used[N]  对端  驱动
                            ├ __virtio32 id   (4B)
                            └ __virtio32 len  (4B)
  +4+8N         2          __virtio16 avail_event_idx ⚠️ 驱动  对端
  ────────────────────────────────────────────────────────────────────────
```

**三段的方向很整齐**：
- **desc + avail 段：驱动写，对端读**
- **used 段：对端写，驱动读**

⚠️ **但有两个字段是"插在对方段里的反向字段"**（图里标了 ⚠️）：

| 字段 | 物理位置 | 谁写 | 为什么在那 |
|---|---|---|---|
| ⚠️`used_event_idx` | **available 环的末尾** | **对端** | **向后兼容** |
| ⚠️`avail_event_idx` | **used 环的末尾** | **驱动** | 同上 |

**头文件的原话**（`include/uapi/linux/virtio_ring.h:194-195`）：

> We publish the used event index at the end of the available ring, and vice
> versa. They are at the end for backwards compatibility.

**两个访问宏**（`include/uapi/linux/virtio_ring.h:196-197`）：

```c
#define vring_used_event(vr)  ((vr)->avail->ring[(vr)->num])
#define vring_avail_event(vr) (*(__virtio16 *)&(vr)->used->ring[(vr)->num])
```

⇒ **注意 `[(vr)->num]` ——正好越过环的最后一项，落在那个额外的 `__virtio16` 上。**
**这就是为什么 `struct vring_avail` 的 `ring[]` 声明成柔性数组**
（`include/uapi/linux/virtio_ring.h:114-118`）——它实际比 `num` 项多一项。

⚠️ **这是本子系统最容易看错的一处布局**：
按结构体定义读，`ring[]` 就是 `num` 项；
**实际分配的内存多了一个 `__virtio16`，而且那一项归对方写。**

### §5.2 三个对齐常量

| 常量 | 值 | 位置 | 管什么 |
|---|---|---|---|
| `VRING_DESC_ALIGN_SIZE` | **16** | `include/uapi/linux/virtio_ring.h:94` | 描述符表 |
| `VRING_AVAIL_ALIGN_SIZE` | **2** | `include/uapi/linux/virtio_ring.h:92` | available 环 |
| `VRING_USED_ALIGN_SIZE` | **4** | `include/uapi/linux/virtio_ring.h:93` | used 环 |

**它们是通过 `typedef` + `__attribute__((aligned(...)))` 施加的**
（`include/uapi/linux/virtio_ring.h:151` / `:153` / `:155`）。

⚠️ **为什么用 `typedef` 而不是直接标在结构体上**——
头文件里有一段很长的注释解释这件事（`include/uapi/linux/virtio_ring.h:139-150`）：

> When used on a struct, or struct member, the aligned attribute can only
> increase the alignment; in order to decrease it, the packed attribute must
> be specified as well. When used as part of a typedef, the aligned attribute
> can both increase and decrease alignment

⇒ **因为它们需要"降低"编译器自动选的对齐**（`vring_used_elem` 里有 `__virtio32`，
编译器会想按 4 甚至更大对齐），**只有 `typedef` 形式的 `aligned` 能降**。
**这是一处纯粹的 ABI 兼容性妥协**——对齐必须和规范一致，不能听编译器的。

### §5.3 大小与偏移的计算

```c
static inline unsigned vring_size(unsigned int num, unsigned long align)
{
	return ((sizeof(struct vring_desc) * num + sizeof(__virtio16) * (3 + num)
		 + align - 1) & ~(align - 1))
		+ sizeof(__virtio16) * 3 + sizeof(struct vring_used_elem) * num;
}
```
—— `include/uapi/linux/virtio_ring.h:209-215`

**逐项对照 §5.1 的图**：

| 表达式 | 对应图里的 |
|---|---|
| `sizeof(struct vring_desc) * num` | **16×N** 的描述符表 |
| `sizeof(__virtio16) * (3 + num)` | `avail_flags` + `avail_idx` + `used_event_idx` = **3 个**，加 `available[N]` |
| `+ align-1) & ~(align-1)` | **pad 到对齐边界** |
| `sizeof(__virtio16) * 3` | `used_flags` + `used_idx` + `avail_event_idx` = **3 个** |
| `sizeof(struct vring_used_elem) * num` | **8×N** 的 used 环 |

**偏移计算在 `vring_init()`**（`include/uapi/linux/virtio_ring.h:199-207`）：

```c
	vr->desc = p;
	vr->avail = (struct vring_avail *)((char *)p + num * sizeof(struct vring_desc));
	vr->used = (void *)(((uintptr_t)&vr->avail->ring[num] + sizeof(__virtio16)
		+ align-1) & ~(align - 1));
```

⇒ **`&vr->avail->ring[num] + sizeof(__virtio16)` 这一句就是在跳过
那个"多出来的一项"（`used_event_idx`）** ——和 §5.1 的图完全对上。

### §5.4 描述符的 flags（对端直接读的位）

| 标志 | 值 | 位置 | 含义 |
|---|---|---|---|
| `VRING_DESC_F_NEXT` | **1** | `include/uapi/linux/virtio_ring.h:41` | **`next` 字段有效** ⇒ 描述符链 |
| `VRING_DESC_F_WRITE` | **2** | `include/uapi/linux/virtio_ring.h:43` | **这段缓冲区是"让对端写"的**（对应 `add_inbuf`） |
| `VRING_DESC_F_INDIRECT` | **4** | `include/uapi/linux/virtio_ring.h:45` | **这个描述符指向另一张描述符表** ⇒ 一次提交能挂很多段 |
| `VRING_USED_F_NO_NOTIFY` | **1** | `include/uapi/linux/virtio_ring.h:57` | **对端写在 `used_flags` 里**："别通知我" |
| `VRING_AVAIL_F_NO_INTERRUPT` | **1** | `include/uapi/linux/virtio_ring.h:61` | **驱动写在 `avail_flags` 里**："别给我发中断" |

⚠️ **`VRING_USED_F_NO_NOTIFY` 和 `VRING_AVAIL_F_NO_INTERRUPT` 是对称的一对**，
而且**方向和名字容易搞反**：

| 标志 | 写在哪一段 | 谁写 | 影响谁 |
|---|---|---|---|
| `VRING_USED_F_NO_NOTIFY` | **used 段**（对端的段） | **对端** | **驱动**：`kick_prepare()` 看它决定要不要 notify |
| `VRING_AVAIL_F_NO_INTERRUPT` | **avail 段**（驱动的段） | **驱动** | **对端**：决定要不要发中断 |

⇒ **规律：每一方在"自己的段"里声明"我不想被打扰"。**
这也是 `virtqueue_disable_cb()` 的实现方式（01 §3.4）。

### §5.5 packed ring：一段内存，方向靠 flags 里两个 bit

| | split ring | packed ring |
|---|---|---|
| 段数 | 3 | **1** |
| 描述符 | `struct vring_desc`（`:107`） | `struct vring_packed_desc`（`:239-248`） |
| 字段 | `addr` / `len` / `flags` / `next` | `addr` / `len` / **`id`** / `flags` |
| ⚠️ 端序类型 | **`__virtio64/32/16`**（随协商） | **`__le64/32/16`**（无条件小端） |
| 事件结构 | 两个 `__virtio16` 插在环末尾 | `struct vring_packed_desc_event`（`:232-237`）：`off_wrap` + `flags`，都是 `__le16` |
| 怎么判断"这一项是谁的" | 靠 `avail_idx` / `used_idx` 两个游标 | **靠 flags 里的两个 bit**：`VRING_PACKED_DESC_F_AVAIL`(=**7**，`:51`) 和 `VRING_PACKED_DESC_F_USED`(=**15**，`:52`) |

**packed ring 的核心技巧**：不用两个环，而是**在同一个环里用 flags 的
第 7 位和第 15 位加上一个"翻转计数器"** 来表示"这一项现在归谁"。
⇒ **一次缓存行访问就能同时读到描述符和它的归属**，
而 split ring 需要访问 desc 段 + avail/used 段两处。

⚠️ **`VRING_PACKED_DESC_F_AVAIL` 的值是 7，`_USED` 是 15
——这是 bit 号，不是掩码**（对比 §5.4 那几个都是掩码值）。
⇒ **这是本子系统一处真实的不一致命名**：同一个头文件里
`VRING_DESC_F_*` 是掩码（1/2/4），`VRING_PACKED_DESC_F_*` 是 bit 号（7/15）。
**看代码时必须确认是 `& FLAG` 还是 `& (1 << FLAG)`。**

---

## §6 结构体逐个看

### §6.1 `struct virtio_device`（`include/linux/virtio.h:114-128`）

01 §2.3 已列全部字段。这里只补两点结构性观察：

| 观察 | 说明 |
|---|---|
| **只有 12 个字段** | 非常小——因为所有"能力"都在 `config` 指向的 ops 里 |
| **两把 `spinlock_t`** | `config_lock`（`:119`）和 `vqs_list_lock`（`:120`，注释写着 `Protects VQs list access`） |
| `config_enabled` + `config_change_pending`（`:117-118`） | **一个延迟机制**：probe 期间来的配置变更通知先攒着，`virtio_device_ready()` 之后再放出去 |

### §6.2 `struct virtqueue`（`include/linux/virtio.h:29-39`）

**9 个字段，而且没有锁**（01 §3.6 列过字段）。
⚠️ **没有锁这件事是刻意的**，见 §7.2。

### §6.3 `struct vring_virtqueue`（`drivers/virtio/virtio_ring.c:154` 起）

**这是 `virtqueue` 的真身**——`virtqueue` 只是它的第一个字段
（`struct virtqueue vq`，`:155`），通过 `to_vvq()` 宏（`drivers/virtio/virtio_ring.c:229`）
反查。

| 字段组 | 字段 | 说明 |
|---|---|---|
| **布局选择** | `packed_ring`（`:158`） | **split 还是 packed** |
| **环境标志** | `use_dma_api`（`:161`） | **要不要走 DMA API**（对应 `virtio_has_dma_quirk()`） |
| | `weak_barriers`（`:164`） | **屏障强度** ——见 §3.1 |
| | `broken`（`:167`） | 注释是 `Other side has made a mess, don't try any more` |
| **协商结果缓存** | `indirect`（`:170`） | 对端支持 indirect 描述符 |
| | `event`（`:173`） | **对端发布 `avail_event_idx`** ⇒ 能用 EVENT_IDX 机制 |
| **游标** | `free_head`（`:176`） | **空闲描述符链表头** |
| | `num_added`（`:178`） | 上次同步以来加了几个 |
| | `last_used_idx`（`:186`） | **驱动自己记的"我读到哪了"** ——⚠️ packed 模式下高位还塞了 wrap counter（`:180-185` 的注释） |
| | `event_triggered`（`:189`） | 已经触发过，不用再 disable |
| **布局数据** | `union { split; packed; }`（`:191-197`） | **二选一，不共存** |
| **通知** | `notify`（`:200`） | ⚠️ **注释里带 `FIXME: commonalize hcalls!`** ⇒ 03 Part C 的设计债 |
| | `we_own_ring` | 这块 vring 内存是不是我们分配的 |
| ⚠️ **仅 DEBUG** | `in_use` / `last_add_time_valid` / `last_add_time` | **只在 `#ifdef DEBUG` 下存在**（`:205-212`），见 §7.2 |

### §6.4 `struct vring_virtqueue_split`（`drivers/virtio/virtio_ring.c:89` 起）

| 字段 | 说明 |
|---|---|
| `vring` | **`struct vring` ——指向共享内存的三段**（§5.1） |
| `avail_flags_shadow`（`:94`） | ⚠️ **"上次写进 `avail->flags` 的值"的本地副本** |
| `avail_idx_shadow`（`:100`） | 同理，注释写着 `in guest byte order` |
| `desc_state` / `desc_extra`（`:103-104`） | **每个描述符的驱动侧私有状态**（token 就存这里） |
| `queue_dma_addr` / `queue_size_in_bytes`（`:107-108`） | DMA 信息 |

⚠️ **那两个 `*_shadow` 字段是本子系统一个重要的安全模式**：
**共享内存里的值对端能改，所以驱动自己再存一份影子。**
需要"我上次写了什么"时读影子，**不读共享内存**。
⇒ **这和 §3.2 讲的 token 机制是同一个思路：不信任对端写回来的东西。**

**`avail_idx_shadow` 的注释特别点明 `in guest byte order`**——
共享内存里那份是 `__virtio16`（端序随协商），影子是宿主端序的 `u16`。

### §6.5 三个共享内存结构体的字节级定义

| 结构体 | 位置 | 大小 | 字段 |
|---|---|---|---|
| `struct vring_desc` | `include/uapi/linux/virtio_ring.h:107-112` | **16 字节** | `__virtio64 addr`(8) + `__virtio32 len`(4) + `__virtio16 flags`(2) + `__virtio16 next`(2) |
| `struct vring_avail` | `include/uapi/linux/virtio_ring.h:114-118` | 4 + 2N + 2 | `__virtio16 flags` + `__virtio16 idx` + `__virtio16 ring[]`（**实际比 N 多一项**，§5.1） |
| `struct vring_used_elem` | `include/uapi/linux/virtio_ring.h:121-127` | **8 字节** | `__virtio32 id` + `__virtio32 len` |
| `struct vring_used` | `include/uapi/linux/virtio_ring.h:131-135` | 4 + 8N + 2 | `__virtio16 flags` + `__virtio16 idx` + `vring_used_elem_t ring[]`（同样多一项） |
| `struct vring` | `include/uapi/linux/virtio_ring.h:158-166` | 只是三个指针 + num | **它不是共享内存的一部分**，是驱动侧的"三段在哪"的记录 |

⚠️ **`struct vring_used_elem` 的 `id` 用 `__virtio32` 而不是 `__virtio16`**，
头文件注释解释了原因（`include/uapi/linux/virtio_ring.h:120`）：
`u32 is used here for ids for padding reasons`。
⇒ **纯粹为了让这个结构是 8 字节整数** ——又一处 ABI 妥协。

---

## §7 全局变量与锁

### §7.1 全局变量：只有两个

| 变量 | 位置 | 作用 |
|---|---|---|
| `virtio_index_ida` | `drivers/virtio/virtio.c:12` | **分配 `vdev->index`** |
| `virtio_bus` | `drivers/virtio/virtio.c:343` | **总线本体** |
| `virtio_dev_attrs[]` | `drivers/virtio/virtio.c:64` | sysfs 属性数组 |
| `virtio_check_mem_acc_cb` | `drivers/virtio/virtio_anchor.c:18` | **一个可覆盖的全局函数指针**（01 §8.2） |

**`virtio_ring.c`（2876 行）里一个全局变量都没有** ——
所有状态都在 `struct vring_virtqueue` 里。
⇒ **这是本子系统一个明确的正面设计**：数据通路完全无全局状态，
所以天然支持多设备多队列并行。

### §7.2 ⚠️ 锁：`virtqueue` **不自带锁**

| 锁 | 位置 | 保护什么 |
|---|---|---|
| `vdev->config_lock` | `include/linux/virtio.h:119` | 配置变更通知的串行化 |
| `vdev->vqs_list_lock` | `include/linux/virtio.h:120` | **`vdev->vqs` 链表**（注释：`Protects VQs list access`） |
| ⚠️ **`struct virtqueue` / `struct vring_virtqueue`** | — | **没有任何锁** |

**"没有锁"是刻意的，而且代码里写明了**：
`struct vring_virtqueue` 在 `#ifdef DEBUG` 下有一个 `in_use` 字段
（`drivers/virtio/virtio_ring.c:206-207`），它上面的注释就是：

> They're supposed to lock for us.

⇒ **"They" 指的是驱动。** 意思是：**同一个 virtqueue 上的并发访问由驱动负责串行化**，
框架不加锁；DEBUG 模式下用 `in_use` 计数器抓违规。

**为什么这么设计**：数据通路是性能最敏感的地方，
而**不同队列天然互不干扰**（收/发各一个队列是常见形态），
⇒ **让框架加锁会给"本来不需要锁"的场景付代价**。

⚠️ **对驱动作者的直接后果**（05 §1 会给骨架）：
- **同一个 vq 上 `add_*()` 和 `get_buf()` 并发 ⇒ 必须自己加锁**
- **网卡这类驱动通常每个方向一个 vq，靠"发送路径持 tx 锁、
  NAPI 路径持 rx 的天然串行"来避免加锁**
- **开 `DEBUG` 跑一遍能抓到漏加锁的地方**（07 篇会讲）

---

## §8 结构关系总图

```
  virtio_bus (drivers/virtio/virtio.c:343)   真总线，25 个 driver 绑在上面
      │  .match 按 virtio_device_id
      │
      ├── struct virtio_driver ×25          include/linux/virtio.h:175-191
      │      ├─ driver (device_driver)
      │      ├─ id_table                    匹配哪些设备
      │      ├─ feature_table               我认识哪些 feature 位
      │      ├─ validate  ← 协商前
      │      └─ probe     ← 协商后
      │
      └── struct virtio_device              include/linux/virtio.h:114-128
             ├─ dev (struct device)         挂在 virtio_bus 上
             ├─ id (virtio_device_id)       ← 传输层填
             ├─ config ─────────────────►  struct virtio_config_ops ×9
             │                                include/linux/virtio_config.h:95-121
             │                                17 个回调（get/set/find_vqs/…）
             ├─ features (u64)              协商结果，上限 64 位
             ├─ config_lock / vqs_list_lock 两把 spinlock
             ├─ priv                        设备驱动的私有数据
             └─ vqs ──┐
                      │
                      ▼
        struct virtqueue ×N                 include/linux/virtio.h:29-39
             ├─ callback                    对端通知你时调
             ├─ index / num_free / num_max
             ├─ priv                        ⚠️ 传输层的，不是你的
             └─（它的真身）
                      │  to_vvq()
                      ▼
        struct vring_virtqueue              drivers/virtio/virtio_ring.c:154
             ├─ vq (struct virtqueue)       第一个字段
             ├─ packed_ring / use_dma_api / weak_barriers / broken
             ├─ free_head / last_used_idx   驱动侧游标
             ├─ notify                      ⚠️ FIXME: commonalize hcalls!
             └─ union {
                  split  ──► struct vring ──┐   指向共享内存
                                            │
                  packed ──► （另一套）      │
                }                           │
        ⚠️ 没有任何锁 —— "They're supposed to lock for us"
                                            │
  ══════════════════════════════════════════▼══════════════════════════════
        共享内存（vring）—— 对端也在读写，布局见 §5
                include/uapi/linux/virtio_ring.h
             ┌──────────────────────────────────────────┐
             │ desc[N]      16×N   驱动写 / 对端读        │
             │ avail_flags,idx,ring[N]  驱动写 / 对端读   │
             │   + used_event_idx  ⚠️ 对端写（插在这段末尾）│
             │ ── pad 到对齐边界 ──                       │
             │ used_flags,idx,ring[N]   对端写 / 驱动读   │
             │   + avail_event_idx ⚠️ 驱动写（插在这段末尾）│
             └──────────────────────────────────────────┘
  ══════════════════════════════════════════════════════════════════════════
        对端（不在本树）
```

**这张图最该记住的四件事**：

1. **`virtqueue` 和 `vring_virtqueue` 是同一个对象的两个面**——
   前者是对驱动暴露的，后者是实现；靠 `to_vvq()` 转换
2. **`config` 指针是"两层可插拔"的层 1 接口**，`virtio_driver` 是层 2
3. ⚠️ **没有锁**——`virtqueue` 的并发保护是驱动的责任（§7.2）
4. **双横线下面的东西对端也在写** ——所有从那里读出来的值都要校验，
   驱动侧另存影子（§6.4）
