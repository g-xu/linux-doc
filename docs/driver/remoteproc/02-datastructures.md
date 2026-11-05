---
title: 数据结构
---

# remoteproc 核心数据结构

**这一篇回答**：这些结构体分别代表什么、谁指向谁、哪些字段是谁在写。

- 版本：Linux **6.1.158**，arm64。
- 主头文件：`include/linux/remoteproc.h`（708 行）。
- 行号全部写完整路径，不用裸 `:N`。

---

## §1.0 先分清四组关系

这一节的目的不是介绍结构，而是**先否掉四个错误类比**。
这四组关系里任何一组理解错了，后面所有的图都会被读歪。

### §1.0.A 内核结构 vs 固件 ABI 结构 —— 同一个头文件里的两个世界

⚠️`include/linux/remoteproc.h` 里住着两类结构，**改动代价完全不同**：

| | 内核侧结构 | 固件 ABI 结构 |
|---|---|---|
| 例子 | `struct rproc`、`struct rproc_ops`、`struct rproc_mem_entry`、`struct rproc_vdev`、`struct rproc_vring` | `struct resource_table`、`struct fw_rsc_hdr`、`struct fw_rsc_carveout`、`struct fw_rsc_devmem`、`struct fw_rsc_trace`、`struct fw_rsc_vdev`、`struct fw_rsc_vdev_vring` |
| 有 `__packed` 吗 | 没有 | **全部有** |
| 谁定义布局 | 内核自己 | **和固件的二进制约定** |
| 改一个字段的后果 | 重编内核即可 | **固件对不上，远端跑飞** |
| 数值可以随便改吗 | 可以 | `enum fw_resource_type` 的值**被当数组下标用** |

判据一眼可见 —— 七个 ABI 结构的定义行末尾都带 `__packed`：
`include/linux/remoteproc.h:73`（`resource_table`）、`:89`（`fw_rsc_hdr`）、
`:171`（`fw_rsc_carveout`）、`:209`（`fw_rsc_devmem`）、`:234`（`fw_rsc_trace`）、
`:258`（`fw_rsc_vdev_vring`）、`:302`（`fw_rsc_vdev`）。
而 `include/linux/remoteproc.h:549` 的 `struct rproc` 一个 `__packed` 都没有。

**"数值被当下标用"不是猜的**，头文件自己写在注释里
（`include/linux/remoteproc.h:112`：*"these values are used as indices to the
rproc_handle_rsc lookup table, so please keep them sane"*），
兑现处是 `drivers/remoteproc/remoteproc_core.c:1010` 的
`rproc_loading_handlers[RSC_LAST]` —— 一个用 `RSC_CARVEOUT`(0) / `RSC_DEVMEM`(1) /
`RSC_TRACE`(2) / `RSC_VDEV`(3) 做下标的函数指针数组
（`drivers/remoteproc/remoteproc_core.c:1011-1014`）。

⇒ **所以 `enum fw_resource_type` 里那几个 `= 0/1/2/3` 是硬约束**
（`include/linux/remoteproc.h:117-120`），不是"随便给的编号"。
同样的手法在 `enum rproc_state` 上也用了一次，见 §10。

### §1.0.B `rproc` / `rproc_vdev` / `virtio_device` / `rpmsg_device` —— 四层，不是一层

⚠️ **最容易犯的错**：以为 rproc 就是"跑固件的那个东西"，
rpmsg 就是"和它通信的 API"，中间没别的。真实层数是四层：

```
  struct rproc                     ← remoteproc 核心的设备对象
      │  rvdevs 链表
      ▼
  struct rproc_vdev                ← 资源表里每个 RSC_VDEV 项对应一个
      │  pdev（platform_device）
      ▼
  struct virtio_device             ← 由 remoteproc_virtio.c 注册进 virtio 总线
      │  virtio 的 driver 匹配
      ▼
  struct rpmsg_device              ← virtio_rpmsg_bus.c 才是它的创建者
```

关键的四条事实：

| 事实 | 依据 |
|---|---|
| `rproc_vdev` **内嵌一个 `rproc_subdev`**，所以它是靠 subdev 机制跟着 rproc 起停的 | `include/linux/remoteproc.h:646` |
| `rproc_vdev` 里挂的是一个 **`platform_device`**，不是 `virtio_device` | `include/linux/remoteproc.h:647` |
| **rpmsg 不属于 remoteproc**：`drivers/remoteproc/` 里没有任何 rpmsg 设备的创建代码 | rpmsg 设备由 `drivers/rpmsg/virtio_rpmsg_bus.c` 创建 |
| 一个 rproc 可以有多个 vdev，用 `index` 区分 | `include/linux/remoteproc.h:654`、计数字段 `nb_vdev` 在 `include/linux/remoteproc.h:583` |

⇒ **推论**：「远端起来了但 `/dev/rpmsg*` 没出现」这个症状，坏点可能在四层里的任何一层。
07 篇的分层排查表就是照这四层排的。08 篇会专门讲这个类比错在哪。

### §1.0.C `rproc_subdev` 不是 `struct device`

⚠️ 名字里有 "dev"，但它和驱动模型没有半点关系：

| 问题 | `struct rproc_subdev` | `struct device` |
|---|---|---|
| 内嵌 `struct device` 吗 | **没有** | 它本身就是 |
| 出现在 sysfs 里吗 | 不出现 | 出现 |
| 会被 driver 匹配吗 | **不会**，没有 bus，没有 driver | 会 |
| 有几个成员 | **只有 4 个回调 + 1 个链表节点** | 几十个 |

结构体全文只有 6 行（`include/linux/remoteproc.h:600-607`）：
`node` + `prepare` / `start` / `stop` / `unprepare`。
**为什么不会发生"subdev 被当设备注册"**：整个 `drivers/remoteproc/` 里
没有一处对 subdev 调 `device_add()` / `device_register()` ——
唯一的挂载动作是 `rproc_add_subdev()` 往链表里加一项。

真正长成设备的那个是 `rproc_vdev`，它**内嵌** subdev
（`include/linux/remoteproc.h:646`）**再另外**持有一个 platform_device
（`include/linux/remoteproc.h:647`）。⇒ subdev 只是它搭上 rproc 起停顺序的挂钩。

### §1.0.D 两套计数：`power` 和 `dev` 的 kref

⚠️`struct rproc` 上有两个独立的"引用计数"，作用完全不同：

| | `rproc->power` | `rproc->dev` 的 kref |
|---|---|---|
| 类型/位置 | `atomic_t`，`include/linux/remoteproc.h:557` | `struct device` 内部，`include/linux/remoteproc.h:556` |
| 数的是什么 | **有几个消费者需要远端在跑** | 有几个人还持有这个对象 |
| 谁加谁减 | `rproc_boot()` / `rproc_shutdown()` | `get_device()` / `put_device()` |
| 归零的后果 | 远端停机，**对象还在** | 对象被释放（`rproc_type_release`，`drivers/remoteproc/remoteproc_core.c:2353`） |

**`rproc_shutdown()` 的源码注释自己点明了这件事**：它说自己
"不减 rproc 的 refcount，只减 power refcount，所以 `@rproc` 句柄在
`rproc_shutdown()` 返回后依然有效"（`drivers/remoteproc/remoteproc_core.c:1987` 上方的注释块）。

⇒ 这是 01 §12 里 2 号误用（`rproc_free()` 当 `rproc_put()` 用）的根子。

---

## §1 remoteproc 没有 `bus_type`：它和消费者的关系

**判定结果：框架型子系统。** 依据：

| 检查 | 结果 |
|---|---|
| `drivers/remoteproc/` 下有 `struct bus_type` 吗 | **一处都没有** |
| 那么设备挂在哪 | 一个 **class**：`struct class rproc_class`，`drivers/remoteproc/remoteproc_sysfs.c:257`，注册在 `drivers/remoteproc/remoteproc_sysfs.c:265` |
| `rproc->dev` 的身份怎么定 | `drivers/remoteproc/remoteproc_core.c:2463-2464`：`dev.type = &rproc_type` + `dev.class = &rproc_class` |
| `rproc_type` 是什么 | `static const struct device_type`，`drivers/remoteproc/remoteproc_core.c:2370` |

⇒ 所以路径是 `/sys/class/remoteproc/remoteprocN/`，而不是 `/sys/bus/...`。

**`device_type` 在这里还兼职做了"身份标签"**：
`rproc_get_by_child()` 就是靠 `dev->type == &rproc_type` 这一条判断
"这个祖先是不是一个 rproc"（`drivers/remoteproc/remoteproc_core.c:2658`）。
⇒ 没有 bus 也能做类型判断，代价是**只能在内核里判，用户态看不到**。

### §1.1 三方关系图

```
 ┌────────────────────── platform 总线 ──────────────────────┐
 │                                                          │
 │   DT 节点 remoteproc@xxxxx                                │
 │        └─► platform_device ──match──► 你的 rproc 驱动     │
 │                    │ probe                               │
 └────────────────────┼─────────────────────────────────────┘
                      │ rproc_alloc(dev=&pdev->dev, ...)
                      ▼
              ┌───────────────────────────┐
              │  struct rproc             │  dev.parent = platform 侧的 dev
              │  dev.class = rproc_class  │  ← 只有 class，没有 bus
              │  dev.type  = rproc_type   │
              └───────────────────────────┘
                 │              │                │
      carveouts  │      rvdevs  │       subdevs  │
                 ▼              ▼                ▼
      rproc_mem_entry     rproc_vdev        rproc_subdev
                             │ pdev
                             ▼
                    ┌──────────────────┐
                    │ platform_device  │  name = "rproc-virtio"
                    └──────────────────┘
                             │ 由 remoteproc_virtio.c 的 platform driver 接管
                             ▼
                       virtio_device ──► virtio 总线 ──► virtio_rpmsg_bus
```

**注意 `rproc_vdev` 里挂的是 platform_device，多绕了一跳**：
资源表里的 `RSC_VDEV` 先变成一个 platform 设备，
再由 `drivers/remoteproc/remoteproc_virtio.c` 里的 platform 驱动把它变成 virtio 设备。
⇒ 这一跳是 03 篇时间线上很容易看漏的一段，也是 08 篇的一个选题。

### §1.2 消费者怎么拿到服务

remoteproc 没有总线，所以消费者只有两条路（都在 01 §3）：

| 消费者身份 | 拿法 | 引用 |
|---|---|---|
| 独立驱动（DT 里写 phandle） | `rproc_get_by_phandle()` | 加两重，要 `rproc_put()` |
| 挂在 rproc 底下的子设备 | `rproc_get_by_child()` | 不加 |
| 用户态 | sysfs `state` / cdev ioctl | 不涉及 |

---

## §2 `struct rproc` —— 39 个字段按职责分组

定义在 `include/linux/remoteproc.h:549-589`，**共 39 个成员**。
按"谁在写它"分组比按声明顺序读省力得多。

### §2.1 驱动在 `rproc_alloc()` 和 `rproc_add()` 之间可以改的

| 字段 | 行 | 说明 |
|---|---|---|
| `priv` | `include/linux/remoteproc.h:554` | 驱动私有区。`rproc_alloc()` 的 `len` 参数决定它多大 |
| `auto_boot` | `:580` | `rproc_add()` 要不要立刻请求固件并启动。**默认 true** |
| `has_iommu` | `:579` | 要不要让核心接管 IOMMU 域 |
| `recovery_disabled` | `:573` | 崩溃后不自动恢复（对应 sysfs 的 `recovery`） |
| `dump_conf` | `:559` | 转储三态，默认 `RPROC_COREDUMP_DISABLED`，见 01 §5 |
| `elf_class` / `elf_machine` | `:584` / `:585` | coredump 生成的 ELF 头信息，由 `rproc_coredump_set_elf_info()` 写 |
| `sysfs_read_only` | `:581` | 把 `state` / `firmware` 两个属性变成只读 |
| `state` | `:558` | 驱动**只在一种情况下**该写它：远端已经在跑，填 `RPROC_DETACHED` 让核心走 attach |
| `features` | `:588` | 位图，目前只有一位 `RPROC_FEAT_ATTACH_ON_RECOVERY`（`include/linux/remoteproc.h:502`） |

⚠️`features` 位图不要直接改，用 `remoteproc_internal.h` 里的
`rproc_set_feature()` / `rproc_has_feature()`
（`drivers/remoteproc/remoteproc_internal.h:47` / `:42`）。

### §2.2 核心自己维护、驱动只读的

| 字段 | 行 | 说明 |
|---|---|---|
| `dev` | `:556` | 内嵌的 `struct device`（**不是指针**）。`dev.parent` 才是平台设备 |
| `power` | `:557` | `atomic_t`，boot/shutdown 计数，见 §1.0.D |
| `lock` | `:560` | 一把 mutex 保护整个状态机 |
| `index` | `:570` | 从 IDA 分配，决定 `remoteprocN` 里的 N |
| `bootaddr` | `:566` | 由 `->get_boot_addr` 填 |
| `crash_cnt` | `:572` | 崩溃累计次数，debugfs 里能看 |
| `crash_handler` | `:571` | `work_struct`，见 §7 |
| `ops` | `:555` | ⚠️ 类型是 **`struct rproc_ops *`（非 const）**，因为 `rproc_alloc_ops()`（`drivers/remoteproc/remoteproc_core.c:2399`）先 `kmemdup()` 一份**再往里补默认值**。⇒ 驱动那份 `static const struct rproc_ops` 不会被改，运行时生效的是副本 |
| `name` / `firmware` | `:552` / `:553` | 字符串，`firmware` 可被 `rproc_set_firmware()` 换掉 |

### §2.3 五个链表 + 一个 idr —— 这是 `struct rproc` 的主体

| 字段 | 行 | 元素类型 | 谁往里加 |
|---|---|---|---|
| `carveouts` | `:564` | `struct rproc_mem_entry` | 驱动（`rproc_add_carveout`）**或**核心处理 `RSC_CARVEOUT` 时 |
| `mappings` | `:565` | `struct rproc_mem_entry` | 只有核心，处理 `RSC_DEVMEM` 建 IOMMU 映射时 |
| `traces` | `:562` | `struct rproc_debug_trace` | 只有核心，处理 `RSC_TRACE` 时；计数在 `num_traces`（`:563`） |
| `rvdevs` | `:567` | `struct rproc_vdev` | 只有核心，处理 `RSC_VDEV` 时；计数在 `nb_vdev`（`:583`） |
| `subdevs` | `:568` | `struct rproc_subdev` | 驱动和核心都会加 |
| `dump_segments` | `:582` | `struct rproc_dump_segment` | 驱动 |
| `notifyids` | `:569` | `struct idr` | vring 的 notifyid 分配器；上界记在 `max_notifyid`（`:574`） |

**注意 `carveouts` 是唯一"驱动和核心都会往里加"的内存链表**，
这正是 01 §4 那条"名字要和资源表对得上"规则的原因：
核心处理 `RSC_CARVEOUT` 时会**先按名字在链表里找**，找到就复用驱动预注册的那块。
匹配函数是 `rproc_find_carveout_by_name()`
（原型 `drivers/remoteproc/remoteproc_internal.h:124`）。

### §2.4 三个资源表指针 —— 本结构最绕的一处

⚠️`table_ptr` / `clean_table` / `cached_table`
（`include/linux/remoteproc.h:575` / `:576` / `:577`）名字很像，含义完全不同：

| 字段 | 指向哪 | 什么时候有值 |
|---|---|---|
| `table_ptr` | **当前生效的那份**（可能在远端内存里，也可能是 cached 的那份） | 加载后一直有 |
| `cached_table` | 内核这边的一份副本（kmemdup 出来的） | 远端内存还没准备好时 |
| `clean_table` | **固件里原始那份的备份** | 用于崩溃**恢复时重置**——远端跑过一轮后资源表会被改写，恢复必须从干净的重来 |

⇒ `clean_table` 的存在本身就说明了一件事：**资源表是双方共享的可写内存**，
不是只读配置。这条在 08 篇会展开。表大小记在 `table_sz`（`:578`）。

### §2.5 字符设备相关

`cdev`（`:586`）内嵌一个 `struct cdev`，`cdev_put_on_release`（`:587`）
是一个"关闭 `/dev/remoteprocN` 时是否顺带 shutdown"的标志。
没开 `CONFIG_REMOTEPROC_CDEV` 时这两个字段仍然存在，只是没人用。

---

## §3 固件 ABI 结构族（资源表）

这一族的读法和内核结构不同：**你要把它当成一段二进制格式说明来读**。

### §3.1 整体布局

```
 固件 ELF 里的 .resource_table 段：

 ┌─────────────────────────────────────────┐
 │ struct resource_table                   │  h:73
 │   u32 ver;                              │
 │   u32 num;          ← 有几个资源项       │
 │   u32 reserved[2];                      │
 │   u32 offset[];     ← num 个偏移量       │  柔性数组
 ├─────────────────────────────────────────┤
 │ offset[0] ──► struct fw_rsc_hdr         │  h:89
 │                 u32 type;               │  ← RSC_CARVEOUT / DEVMEM / TRACE / VDEV
 │                 u8  data[];  ───────────┼──► struct fw_rsc_carveout ...
 ├─────────────────────────────────────────┤
 │ offset[1] ──► struct fw_rsc_hdr  ...    │
 │ ...                                     │
 └─────────────────────────────────────────┘
```

**两层柔性数组**：`resource_table.offset[]` 长度由 `num` 决定，
每个 `fw_rsc_hdr.data[]` 长度由 `type` 决定。
⇒ 所有的越界风险都在这里，03 篇会走一遍核心的校验代码。

### §3.2 四种标准资源项

| 类型 | 值 | 结构 | 行 | 语义 |
|---|---|---|---|---|
| `RSC_CARVEOUT` | 0 | `fw_rsc_carveout` | `include/linux/remoteproc.h:171` | "给我一块物理连续内存" |
| `RSC_DEVMEM` | 1 | `fw_rsc_devmem` | `:209` | "帮我把某个外设 iommu_map 进来" |
| `RSC_TRACE` | 2 | `fw_rsc_trace` | `:234` | "这块内存是我写日志用的"（于是 debugfs 出现 `trace0`…） |
| `RSC_VDEV` | 3 | `fw_rsc_vdev` | `:302` | "我支持一个 virtio 设备"，本身兼作 virtio 头 |

枚举值定义在 `include/linux/remoteproc.h:117-123`，
另有厂商区间 `RSC_VENDOR_START`=128 / `RSC_VENDOR_END`=512（`:122-123`）。

**`fw_rsc_carveout` 和 `fw_rsc_devmem` 字段完全一样**
（`da`/`pa`/`len`/`flags`/`reserved`/`name[32]`，
`include/linux/remoteproc.h:171-178` 与 `:209-216`），
区别只在处理函数：一个去分配内存（`rproc_handle_carveout`，
`drivers/remoteproc/remoteproc_core.c:822`），
一个去建 IOMMU 映射（`rproc_handle_devmem`，
`drivers/remoteproc/remoteproc_core.c:619`）。
⇒ 结构相同、语义不同，靠 `type` 区分——这是本子系统"名字很像"陷阱之一。

### §3.3 `FW_RSC_ADDR_ANY` —— 地址协商机制

`#define FW_RSC_ADDR_ANY (-1)`（`include/linux/remoteproc.h:126`）。

这个宏是资源表设计的精髓：固件在 `da`（或 vring 的 `da`）里填
`FW_RSC_ADDR_ANY`，意思是"我不挑地址，你分配完告诉我"。
头文件注释把这个流程叫 *negotiation*（`include/linux/remoteproc.h:60-63`）。

⇒ **推论**：资源表**不是单向的输入**，核心会**回写**它。
这也解释了 §2.4 里 `clean_table` 为什么必须存在。

### §3.4 `fw_rsc_vdev` 与 vring

```
 struct fw_rsc_vdev                     h:302
   u32 id;              ← virtio device id
   u32 notifyid;        ← 整个 rproc 范围内唯一
   u32 dfeatures;       ← 远端支持的 feature
   u32 gfeatures;       ← 内核这边协商后写回去的
   u32 config_len;
   u8  status;          ← virtio 状态字节，双方读写
   u8  num_of_vrings;
   u8  reserved[2];
   struct fw_rsc_vdev_vring vring[];    h:258
        u32 da; u32 align; u32 num; u32 notifyid; u32 pa;
```

**`gfeatures` 和 `status` 是内核往固件方向写的字段** ——
再一次说明资源表是共享可写内存。

⚠️ **内核侧最多支持 2 个 vring**：
`#define RVDEV_NUM_VRINGS 2`（`include/linux/remoteproc.h:611`），
`struct rproc_vdev` 里的数组按它定长（`include/linux/remoteproc.h:652`）。
而固件侧的 `vring[]` 是柔性数组、`num_of_vrings` 是 `u8`。
⇒ 固件声明 3 个 vring 时内核这边装不下，这是一个**格式合法但内核拒绝**的情形。

### §3.5 `enum rsc_handling_status`

`include/linux/remoteproc.h:355-358`：`RSC_HANDLED`=0 / `RSC_IGNORED`=1。
这是 `->handle_rsc` 回调**独有**的返回约定：返回 0 表示"我处理了"，
返回 1 表示"我不管这一项"，负值表示出错。
⇒ ⚠️ 和内核惯例（0=成功、非 0=失败）**不一样**，是本子系统一处局部约定。
`->handle_rsc` 的注释把这条写在 `include/linux/remoteproc.h:372-374`。

---

## §4 `struct rproc_mem_entry` —— 内核侧的内存描述符

`include/linux/remoteproc.h:332-346`，14 个成员。
它**不是** `fw_rsc_carveout`，两者的关系是"内核视角"对"固件视角"：

| | `fw_rsc_carveout`（固件侧） | `struct rproc_mem_entry`（内核侧） |
|---|---|---|
| `__packed` | 有 | 没有 |
| 有 `va` 吗 | **没有**（固件不关心内核虚拟地址） | 有（`:333`） |
| 有 `dma` 吗 | 没有，只有 `pa` | 有（`:335`） |
| 有回调吗 | 不可能有 | 有 `alloc` / `release`（`:344` / `:345`） |
| `name` | `u8 name[32]` | `char name[32]`（`:339`） |

关键字段：

| 字段 | 行 | 说明 |
|---|---|---|
| `va` | `:333` | 内核虚拟地址 |
| `is_iomem` | `:334` | 决定能不能用普通 `memcpy`，见 01 §4 |
| `dma` | `:335` | DMA 地址 |
| `len` / `da` | `:336` / `:337` | 长度、远端看到的地址 |
| `priv` | `:338` | 给 `alloc`/`release` 用 |
| `rsc_offset` | `:341` | 这块内存对应资源表里哪一项——**回写地址就靠它** |
| `flags` | `:342` | IOMMU 保护位 |
| `of_resm_idx` | `:343` | DT `memory-region` 的索引，`rproc_of_resm_mem_entry_init()` 填 |
| `alloc` / `release` | `:344` / `:345` | 延迟分配：挂链表时可以还没有内存，加载时才真分配 |

**`alloc` 回调是"两段式生命周期"在内存上的体现**：
驱动在 probe 里只描述"我要一块什么样的内存"，真正分配推迟到固件加载时，
因为那时才知道固件到底要不要这块。⇒ 这条设计在 08 篇有类比。

---

## §5 `struct rproc_ops` —— 结构上要注意的两件事

回调清单在 01 §9，这里只说结构本身的两个特点。

**一**：定义在 `include/linux/remoteproc.h:385-403`，17 个成员，**全是函数指针**。

**二**：**`rproc->ops` 指向的不是驱动那份，而是一份副本**。
`rproc_alloc_ops()`（`drivers/remoteproc/remoteproc_core.c:2397`）
用 `kmemdup()` 拷一份（`drivers/remoteproc/remoteproc_core.c:2399`），
再往副本里补默认值（`drivers/remoteproc/remoteproc_core.c:2404-2415`）。

⇒ 两条推论：

| 推论 | 说明 |
|---|---|
| 驱动可以放心写 `static const struct rproc_ops` | 核心不会去改驱动那份 |
| ⚠️ 想在运行时看"实际生效的 ops"，得看 `rproc->ops`，不是驱动的静态变量 | 补进去的 ELF 默认值只在副本里 |

也顺便解释了 `struct rproc` 里 `ops` 字段为什么是 **非 const 指针**
（`include/linux/remoteproc.h:555`）。

---

## §6 subdev / vdev / vring 三兄弟

### §6.1 `struct rproc_subdev`

`include/linux/remoteproc.h:600-607`，见 §1.0.C。只有 4 个回调 + 1 个链表节点。

### §6.2 `struct rproc_vdev`

`include/linux/remoteproc.h:644-655`，8 个成员：

| 字段 | 行 | 说明 |
|---|---|---|
| `subdev` | `:646` | **内嵌**（不是指针）⇒ `container_of` 从 subdev 回到 vdev |
| `pdev` | `:647` | `struct platform_device *`，见 §1.1 那多绕的一跳 |
| `id` | `:649` | virtio device id（`virtio_ids.h` 里那些） |
| `node` | `:650` | 挂在 `rproc->rvdevs` 上 |
| `rproc` | `:651` | 反向指针 |
| `vring[]` | `:652` | 定长 `RVDEV_NUM_VRINGS`=2（`include/linux/remoteproc.h:611`） |
| `rsc_offset` | `:653` | 资源表里对应那一项的偏移 —— 回写 `gfeatures`/`status` 靠它 |
| `index` | `:654` | 第几个 vdev |

### §6.3 `struct rproc_vring`

`include/linux/remoteproc.h:623-631`，7 个成员：
`va` / `num` / `da` / `align` / `notifyid` / `rvdev`（反向指针）/ `vq`。

**`notifyid` 是这一族的枢纽**：它由 `rproc->notifyids`（一个 `struct idr`，
`include/linux/remoteproc.h:569`）分配，写回资源表让固件知道，
中断进来时又用它去反查是哪条队列 —— 这就是 01 §8 说的
"`vq_id` 不是驱动自己编的号"。上界记在 `rproc->max_notifyid`（`:574`）。

### §6.4 `struct rproc_dump_segment`

`include/linux/remoteproc.h:480-490`：`node` / `da` / `size` / `priv` /
`dump`（自定义转储回调）/ `offset`。
`dump` 为 NULL 时由核心自己读，非 NULL 时走
`rproc_coredump_add_custom_segment()` 那条路（01 §5）。

### §6.5 三个枚举

| 枚举 | 行 | 值 |
|---|---|---|
| `enum rproc_crash_type` | `include/linux/remoteproc.h:450-454` | `RPROC_MMUFAULT` / `RPROC_WATCHDOG` / `RPROC_FATAL_ERROR` |
| `enum rproc_dump_mechanism` | `:464-468` | `DISABLED` / `ENABLED` / `INLINE`（01 §5） |
| `enum rproc_features` | `:501-504` | 目前只有 `RPROC_FEAT_ATTACH_ON_RECOVERY` 一项 + `RPROC_MAX_FEATURES` |

---

## §7 全局变量与锁

### §7.1 全局变量（全在 `drivers/remoteproc/remoteproc_core.c` 顶部）

| 变量 | 行 | 作用 |
|---|---|---|
| `rproc_list` | `:47` | 所有已注册 rproc 的链表 |
| `rproc_list_mutex` | `:46` | 保护上面那条链表的**写**端 |
| `rproc_dev_index` | `:59` | `DEFINE_IDA`，分配 `remoteprocN` 的 N |
| `rproc_recovery_wq` | `:60` | 专用工作队列，崩溃恢复都在它上面跑 |
| `rproc_panic_nb` | `:48` | panic 通知块，见 §7.4 |
| `rproc_crash_names[]` | `:62-66` | `"mmufault"` / `"watchdog"` / `"fatal error"` |

另有一个不在 core 里的：`struct class rproc_class`
（`drivers/remoteproc/remoteproc_sysfs.c:257`）。

### §7.2 `rproc_list` 用的是 **mutex + RCU 组合**

这是本子系统锁设计里最值得看的一处：

| 操作 | 怎么做 | 行 |
|---|---|---|
| 加入 | `mutex_lock(&rproc_list_mutex)` + `list_add_rcu()` | `drivers/remoteproc/remoteproc_core.c:2302-2304` |
| 删除 | `mutex_lock` + 删除 + **等一个 grace period** | `drivers/remoteproc/remoteproc_core.c:2571-2575` |
| 遍历（`rproc_get_by_phandle`） | `rcu_read_lock()` + `list_for_each_entry_rcu()`，**不拿 mutex** | `drivers/remoteproc/remoteproc_core.c:2122` |
| 遍历（panic 处理） | 同上 | `drivers/remoteproc/remoteproc_core.c:2702` |

⇒ **为什么要 RCU**：panic 通知回调里必须能遍历这条链表，
而那个上下文**不能睡**（拿不了 mutex）。⇒ 读端必须无锁。
`drivers/remoteproc/remoteproc_core.c:2575` 那行注释写得很直白：
*"Ensure that no readers of rproc_list are still active"*。

⚠️ RCU 读端只保证"链表节点不会被 free"，**不保证对象还有效** ——
所以 `rproc_get_by_phandle()` 在 RCU 临界区里还必须做
`try_module_get()` + `get_device()`（01 §3）。这两件事缺一不可。

### §7.3 `rproc->lock` —— 一把 mutex 管整个状态机

| 事实 | 数字 |
|---|---|
| 出现位置 | **只在 `drivers/remoteproc/remoteproc_core.c` 里**（其余六个核心 `.c` 全是 0 次） |
| 该文件里的引用次数 | 17 |
| 其中加锁动作 | 7 |
| 声明 | `include/linux/remoteproc.h:560` |

⇒ **推论**：sysfs / cdev / debugfs 都**不直接**碰这把锁，它们一律通过
`rproc_boot()` / `rproc_shutdown()` / `rproc_set_firmware()` 这些 API 间接加锁。
这是一个很干净的设计：**状态机的临界区完全封在 core 里**。

加锁用的是 `mutex_lock_interruptible()` 而不是 `mutex_lock()`
（例如 `rproc_set_firmware()` 里，`drivers/remoteproc/remoteproc_core.c:2178`）。
代价见 01 §10.2：错误码被压成 `-EINVAL`。

### §7.4 崩溃路径上的两个异步机制

| 机制 | 结构 | 为什么需要 |
|---|---|---|
| `rproc->crash_handler` | `work_struct`，`include/linux/remoteproc.h:571` | `rproc_report_crash()` 可能在中断里被调，恢复要睡 ⇒ 必须转 work |
| `rproc_recovery_wq` | 专用 workqueue，`drivers/remoteproc/remoteproc_core.c:60` | 不用 system_wq，避免和别的工作互相拖 |

提交点是 `drivers/remoteproc/remoteproc_core.c:2690`
（`queue_work(rproc_recovery_wq, &rproc->crash_handler)`）；
工作队列在模块初始化时创建（`drivers/remoteproc/remoteproc_core.c:2739`）。

panic 那条路是另一套：`rproc_panic_nb` 注册进
`panic_notifier_list`（`drivers/remoteproc/remoteproc_core.c:2728-2729`），
回调里遍历 `rproc_list`，对每个有 `->panic` 的 rproc 调一次
（`drivers/remoteproc/remoteproc_core.c:2703-2710`），
然后按返回的毫秒数**延迟 panic**。
⇒ 这就是 01 §9 里 `panic` 回调"返回还需要等多少毫秒"的用处。

---

## §8 核心操作数据流图

remoteproc 的"核心操作"是**加载并启动固件**。从消费者到硬件的完整链条：

```
 消费者（驱动 / sysfs state / cdev ioctl）
    │
    │ rproc_boot(rproc)                       core.c 行1907
    ▼
 ┌──────────────────────────────────────────────────────────┐
 │ 拿 rproc->lock，power 计数 +1                             │
 │   已经 >0 ⇒ 直接返回                                      │
 └──────────────────────────────────────────────────────────┘
    │
    │ request_firmware()                      ← 会睡
    ▼
 ┌──────────────────────────────────────────────────────────┐
 │ rproc_fw_sanity_check()  → ops->sanity_check             │
 │ rproc_prepare_device()   → ops->prepare    （时钟/电源）  │
 │ rproc_get_boot_addr()    → ops->get_boot_addr            │
 │ rproc_parse_fw()         → ops->parse_fw   （取资源表）    │
 └──────────────────────────────────────────────────────────┘
    │
    │ rproc_handle_resources(rproc, rproc_loading_handlers)
    │                                         core.c 行1418
    ▼
 ┌──────────────────────────────────────────────────────────┐
 │ 按 fw_rsc_hdr.type 查表分派：             core.c 行1010    │
 │   RSC_CARVEOUT → rproc_handle_carveout    core.c 行822     │
 │   RSC_DEVMEM   → rproc_handle_devmem      core.c 行619     │
 │   RSC_TRACE    → rproc_handle_trace       core.c 行545     │
 │   RSC_VDEV     → rproc_handle_vdev        core.c 行475     │
 │   其它         → ops->handle_rsc（厂商项） core.c 行1044    │
 └──────────────────────────────────────────────────────────┘
    │
    │ rproc_load_segments() → ops->load       （搬固件进内存）
    │      内部用 rproc_da_to_va() 换算地址    core.c 行193
    ▼
 ┌──────────────────────────────────────────────────────────┐
 │ ops->start(rproc)                          core.c 行1300   │
 │   ← 这一行才是"放复位、远端开始跑"                     │
 └──────────────────────────────────────────────────────────┘
    │
    │ subdev 的 start 回调（含 rproc_vdev ⇒ 注册 virtio 设备）
    ▼
 virtio 总线 ──► virtio_rpmsg_bus ──► /dev/rpmsg*
```

图中 `core.c 行NNN` 指 `drivers/remoteproc/remoteproc_core.c` 的该行
（图里为了对齐用了简写，正文和其它篇一律写完整路径）。

**运行期的双向门铃**（01 §8）：

```
 内核 → 远端 ： virtio 层 → remoteproc_virtio.c:73 → ops->kick(rproc, notifyid)
 远端 → 内核 ： 硬件中断 → 驱动的 handler → rproc_vq_interrupt(rproc, vqid)
                                            → remoteproc_virtio.c:101
```

---

## §9 命名约定速查

| 前缀/后缀 | 含义 | 例子 |
|---|---|---|
| `rproc_*` | 核心 API 或核心内部函数 | `rproc_boot` |
| `fw_rsc_*` | **固件 ABI 结构**，一定带 `__packed` | `fw_rsc_carveout` |
| `RSC_*` | 资源类型枚举值 **或** `handle_rsc` 的返回状态 | ⚠️ `RSC_VDEV` 是类型；`RSC_HANDLED` 是返回值，两族**同前缀不同枚举** |
| `RPROC_*` | 状态 / 崩溃类型 / 转储方式 / feature 位 | `RPROC_RUNNING` |
| `rvdev` | 变量名，指 `struct rproc_vdev` | `rproc->rvdevs` |
| `rvring` | 变量名，指 `struct rproc_vring` | |
| `da` | **device address**，远端视角的地址 | 到处都是 |
| `pa` / `va` | 物理地址 / 内核虚拟地址 | |
| `_device` 后缀的 `static inline` | 核心内部的回调包装 | `rproc_prepare_device` |
| `rproc_handle_*` | 资源项处理函数（查表分派的目标） | `rproc_handle_carveout` |
| `rproc_elf_*` | 可直接填进 ops 的 ELF 默认实现 | `rproc_elf_load_segments` |

⚠️ **最容易看错的一组前缀是 `RSC_`**：
`RSC_CARVEOUT`/`DEVMEM`/`TRACE`/`VDEV`/`LAST`/`VENDOR_*` 属于
`enum fw_resource_type`（`include/linux/remoteproc.h:116-124`），
而 `RSC_HANDLED`/`RSC_IGNORED` 属于 `enum rsc_handling_status`
（`include/linux/remoteproc.h:355-358`）。
⇒ 前者是**输入的类型标签**，后者是**回调的返回值**，别混。

---

## §10 状态机

`enum rproc_state`（`include/linux/remoteproc.h:428-437`）**七个状态 + 一个哨兵**：

| 值 | 状态 | sysfs 里显示 | 含义 |
|---|---|---|---|
| 0 | `RPROC_OFFLINE` | `offline` | `rproc_alloc()` 的初值 |
| 1 | `RPROC_SUSPENDED` | `suspended` | ⚠️ **arm64 上遇不到**，见下方说明 |
| 2 | `RPROC_RUNNING` | `running` | 内核加载并启动的 |
| 3 | `RPROC_CRASHED` | `crashed` | 等恢复 |
| 4 | `RPROC_DELETED` | `deleted` | 正在拆 |
| 5 | `RPROC_ATTACHED` | `attached` | **别人启动的，内核已接管** |
| 6 | `RPROC_DETACHED` | `detached` | 别人启动的，内核**还没**接管 |
| 7 | `RPROC_LAST` | `invalid` | 哨兵，也是越界时的显示值 |

⚠️ **`RPROC_SUSPENDED` 在本树里只有一个写入点**：
`drivers/remoteproc/omap_remoteproc.c:931` 和
`drivers/remoteproc/omap_remoteproc.c:1008`（OMAP 的 runtime PM 回调）。
核心七个 `.c` 里**一次都没写过**它。
⇒ 对 arm64 来说这个状态等于不存在 —— remoteproc 核心**没有**通用的挂起/恢复语义，
谁想要就得自己在驱动里写。

字符串表在 `drivers/remoteproc/remoteproc_sysfs.c:166-175`，
源码注释要求"永远和 `enum rproc_state` 保持同步"
（`drivers/remoteproc/remoteproc_sysfs.c:164`）；
读取处做了钳位：`state > RPROC_LAST` 就取 `RPROC_LAST`
（`drivers/remoteproc/remoteproc_sysfs.c:184-185`）。

**`RUNNING` 和 `ATTACHED` 的区别是"谁加载的固件"**，不是"跑没跑"。
两者都在跑；区别在于 `RUNNING` 的那份固件是内核搬进去的、内核能重新加载，
`ATTACHED` 的不是。⇒ 这直接决定崩溃恢复能不能重新 load，08 篇会展开。

### §10.1 `rproc_add()` 只接受两个入口状态

`rproc_validate()`（`drivers/remoteproc/remoteproc_core.c:2212`）
是一个很值得读的三行 switch：

| 入口状态 | 额外要求 |
|---|---|
| `RPROC_OFFLINE` | 必须有 `->start`，否则 `-EINVAL`（`drivers/remoteproc/remoteproc_core.c:2220`） |
| `RPROC_DETACHED` | 必须有 `->attach`（`drivers/remoteproc/remoteproc_core.c:2226`），**且 `cached_table` 必须为空**（`drivers/remoteproc/remoteproc_core.c:2232`） |
| 其它任何状态 | 一律 `-EINVAL` |

第二条的理由源码注释写得很清楚：attach 的时候远端内存已经可用了，
不需要 cached table。⇒ 这也解释了 §2.4 里三个表指针的分工。

---

**下一篇**：03 篇按时间线走一遍注册 → 加载 → 启动 → 崩溃 → 恢复 → 拆除，
把本篇的字段和上面这张数据流图逐段对上代码。
