---
title: 费曼学习法
---

# remoteproc 08：用大白话讲清最难理解的几组关系

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> 本篇的写法：每个主题四段 —— **问题** / **先举个例子** / **回到代码** /
> **这个类比什么时候会误导你？**
> 最后那一段是重点：类比只是脚手架，知道它在哪里塌是真正的理解。
>
> ⚠️ 所有 `文件:行号` 都写完整路径。

---

## §1 remoteproc、virtio、rpmsg 是什么关系？

> 这是本子系统**最容易类比错的一组关系**，先讲它。

### 先举个例子

想象你在一栋楼里当物业经理。楼里有一台**独立的柴油发电机**（远端处理器），
平时不用，需要的时候要有人去启动它。你要做三件不同的事：

| 你做的事 | 对应 |
|---|---|
| ① 去机房，检查油、开总闸、按启动按钮 | **remoteproc** |
| ② 在机房和你办公室之间拉一条**气动传输管道**（塞纸条进去，那边能收到） | **virtio** |
| ③ 用这条管道**收发具体的业务单据**：报修单、抄表单、结算单 | **rpmsg** |

三件事的独立性：
- 只做 ①，发电机能转，但你和它之间没有任何沟通渠道；
- ①+② 之后，管道通了，但你还没约定"纸条上怎么写";
- ③ 是约定"纸条格式" —— 报修单走一个格式、抄表单走另一个格式，
  每种单据对应一个**服务名**。

**最关键的一点**：管道（②）不是你（①）的一部分，
业务单据（③）也不是管道的一部分。
三者是**叠在一起的三层**，不是"一个东西的三个面"。

### 回到代码

| 层 | 代码在哪 | 核心动作 |
|---|---|---|
| ① remoteproc | `drivers/remoteproc/remoteproc_core.c` | `rproc_boot()`（`drivers/remoteproc/remoteproc_core.c:1907`）：加载固件、放开复位 |
| ② virtio | `drivers/remoteproc/remoteproc_virtio.c` | `rproc_add_virtio_dev()`（`drivers/remoteproc/remoteproc_virtio.c:370`）：注册一个 virtio 设备 |
| ③ rpmsg | `drivers/rpmsg/virtio_rpmsg_bus.c` | 认领那个 virtio 设备，建立 rpmsg 总线和 channel |

三层的交接点：

| 交接 | 位置 |
|---|---|
| ① → ② | `rproc_handle_vdev()` 建一个平台设备（`drivers/remoteproc/remoteproc_core.c:518`），随后由 `rproc_vdev_do_start()` 触发（`drivers/remoteproc/remoteproc_virtio.c:476`） |
| ② → ③ | `register_virtio_device()`（`drivers/remoteproc/remoteproc_virtio.c:446`），之后 virtio 总线按 `vdev->id.device` 匹配到 `virtio_rpmsg_bus` |

你要写的驱动挂在哪一层，决定了你要用哪套 API：
- 管发电机（启停）→ 写 **platform_driver** + `rproc_ops`（05 §1）
- 收发单据 → 写 **rpmsg_driver**（05 §4，`include/linux/rpmsg.h:110`）
- **没有"remoteproc 设备驱动"这种东西** —— remoteproc 连 `struct bus_type` 都没有
（可重跑：`grep -c 'struct bus_type' drivers/remoteproc/*.c` 全部为 0）

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **气动管道是你事后拉的，virtio 的信息不是**。
   管道的规格（几个 vring、多大、地址在哪）**写在固件里**，
   是 remoteproc 从固件的资源表里读出来的（§2）。
   类比里"你决定拉多粗的管道"，代码里是"发电机的说明书规定了管道规格"。

2. **管道是双向对称的，virtio 不是**。
   vring 的地址由内核分配后**回写进资源表**
   （`drivers/remoteproc/remoteproc_core.c:381`），远端启动后才去读。
   ⇒ 顺序是"内核先定，远端后读"，有严格的先后。

3. ⚠️ **"三层"这个说法本身会误导**：
   ③ 的实现（`virtio_rpmsg_bus.c`）**不在 `drivers/remoteproc/` 里**，
   它是一个独立子系统，只是恰好通过 virtio 和 remoteproc 对接。
   ⇒ rpmsg 有自己的 `bus_type`，remoteproc 没有。
   在 remoteproc 里找 rpmsg 的代码是找不到的（04 §1 开头就点明了这个边界）。

---

## §2 资源表到底是什么？为什么它不是配置文件？

### 先举个例子

你买了一台复杂的机器，包装箱里除了机器本体，还有**一张贴在机器外壳上的规格标签**：

```
   ┌────────────────────────────────┐
   │  型号：XJ-200                   │
   │  需要 220V 电源接口 ×1          │
   │  需要 6mm 气管接口 ×2           │
   │  自带一个状态显示窗（在左侧）    │
   └────────────────────────────────┘
```

这张标签有三个特点：

1. **它印在机器上，不是随机器附送的另一张纸** —— 撕不下来，改不了；
2. **它是"我需要什么"的清单**，不是"你该怎么配"的说明；
3. **安装工会在标签的空白处填字** ——
   "220V 接口已接在配电箱第 3 路"、"气管已接到东墙阀门"。

第 3 点是最反直觉的：**标签是可写的**。

### 回到代码

资源表就是这张标签：

| 特点 | 代码事实 | 位置 |
|---|---|---|
| 印在机器上 | 它是**固件 ELF 里的一个 section**（`.resource_table`），不是单独的文件 | `drivers/remoteproc/remoteproc_elf_loader.c:326` 的 `rproc_elf_load_rsc_table()` |
| "我需要什么"的清单 | 每条资源是一个请求：给我一块内存 / 给我映射一段寄存器 / 我要一个 virtio 设备 | `drivers/remoteproc/remoteproc_core.c:1018` 的 `rproc_handle_resources()` |
| 内核会往里写 | vring 的实际地址和 notifyid 都是内核算出来后**填回去**的 | `drivers/remoteproc/remoteproc_core.c:381`（notifyid）、`drivers/remoteproc/remoteproc_virtio.c:162`（vring da） |

看资源表的内容：
```sh
cat /sys/kernel/debug/remoteproc/remoteproc0/resource_table
```
（实现在 `drivers/remoteproc/remoteproc_debugfs.c:275`，只认四种类型，
见 07 §4.3）

**甚至 virtio 的"配置空间"也在资源表里**：
`rproc_virtio_get()` 读配置时算的是
`rsc = table_ptr + rsc_offset` 然后 `cfg = &rsc->vring[rsc->num_of_vrings]`
（`drivers/remoteproc/remoteproc_virtio.c:298-299`）——
也就是**紧跟在 vring 数组后面的那片内存**。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **标签是给人看的，资源表是给两边的代码看的**。
   固件里的那份是"请求"，内核回写后的那份是"结果"。
   ⇒ 所以"读资源表"这个动作**在不同时刻结果不同**（07 §4.3 那张状态表）。

2. ⚠️ **"标签只有一张"是错的，资源表在内核里有三份**。
   `table_ptr` / `cached_table` / `clean_table` —— 见 §4。
   类比里的"安装工在标签上填字"，代码里其实是
   "先在复印件上填，装好之后再往机器上那张填"。

3. ⚠️ **标签写错了会被发现，资源表写错了不一定**。
   `rproc_handle_resources()` 对每条资源都做了边界检查（07 §6.2 组 A），
   但对**厂商资源**（`RSC_VENDOR_START` 以上）只是转交给驱动 ——
   而且如果厂商资源是**最后一条**，`RSC_IGNORED`（= 正数 1）会被
   `drivers/remoteproc/remoteproc_core.c:1419` 误判成错误（03 §C2）。

---

## §3 为什么 virtio 设备要经过一个"平台设备"中转？

### 先举个例子

你要给一个外包团队派活。理论上你可以直接把活给他们，但公司规定：
**外包必须走采购流程，先立一个"项目"，再由项目去签合同。**

于是流程变成三级：

```
   你的部门  ──立项──▶   项目编号 PRJ-001  ──签约──▶  外包团队
   （远端）              （中间那一层）              （干活的）
```

为什么要多这一层？因为**预算科目、发票主体、验收责任**都挂在"项目"上。
你的部门自己的科目对不上外包发票，项目这一层是专门用来"承接科目"的。

### 回到代码

三级跳的实际形状：

```
   struct rproc            struct rproc_vdev            struct virtio_device
   （远端）        ──▶     （挂在平台设备上）    ──▶     （挂在 virtio 总线上）
```

| 对象 | 创建位置 |
|---|---|
| `struct rproc` | `drivers/remoteproc/remoteproc_core.c:2443`（`rproc_alloc()`） |
| 中间的平台设备 + `rproc_vdev` | `drivers/remoteproc/remoteproc_core.c:518`（`platform_device_register_data()`）→ `drivers/remoteproc/remoteproc_virtio.c:506`（probe 里 `devm_kzalloc`） |
| `struct virtio_device` | `drivers/remoteproc/remoteproc_virtio.c:446`（`register_virtio_device()`） |

**为什么要多这一层？答案就是"预算科目"—— DMA 属性**。
源码在 `rproc_virtio_probe()` 里做了三件跟 DMA 有关的事：

| 动作 | 位置 |
|---|---|
| 从父设备拷 DMA range map | `drivers/remoteproc/remoteproc_virtio.c:29` 的 `copy_dma_range_map()` |
| 设 dma_ops | `rproc_virtio_probe()` 里的 `set_dma_ops()` |
| 设 DMA mask | 同上的 `dma_coerce_mask_and_coherent()` |

⇒ virtio 的 vring 和 buffer 都要走 DMA 分配，
而 `struct rproc` 自己的 `dev`（`rproc->dev`）挂在 `rproc_class` 上，
**没有也不该有 DMA 属性**。
中间那个平台设备就是专门用来承接 DMA 属性的壳子。

源码里甚至有一句自白：`rproc_handle_vdev()` 的函数注释里写着
"address of their base CMA region (ouch, hacky!)"
（`drivers/remoteproc/remoteproc_core.c:471`），
作者自己也觉得这一带的设计不优雅（04 §1.2 展开了这句话的背景）。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **项目编号是你主动申请的，平台设备是"顺手就建了"的**。
   `rproc_handle_vdev()` 在解析资源表时就把平台设备建好了
   （`drivers/remoteproc/remoteproc_core.c:518`），
   但 **virtio 设备要等到 `rproc_start_subdevices()` 才建**
   （`drivers/remoteproc/remoteproc_virtio.c:476`）。
   ⇒ ①② 在**加载阶段**，③ 在**启动阶段**，中间隔着整个固件加载过程（04 §1.1）。

2. ⚠️ **"项目结束就撤销"这个直觉在这里是对的，但引用计数的方向反直觉**：
   平台设备 probe 时对 `&rproc->dev` 做 `get_device()`
   （`drivers/remoteproc/remoteproc_virtio.c:564`），
   remove 时 `put_device()`（`drivers/remoteproc/remoteproc_virtio.c:590`）——
   **是子（平台设备）持有父（rproc）的引用**，
   为了防止"平台设备还在、rproc 已经被释放"。
   ⇒ 类比里"项目依赖部门"，代码里也是这个方向，但**很多人会以为反了**。

---

## §4 为什么资源表在内核里有三份？

### 先举个例子

回到 §2 那台机器和那张标签。现在多了一个情况：**这台机器有时候是别人先装好的**。

于是安装工手上要有三样东西：

| 东西 | 用来干什么 |
|---|---|
| ① **机器外壳上的那张标签** | 唯一"生效"的那份，机器自己也照着它工作 |
| ② **一份复印件（带自己的笔记）** | 机器还没通电时，工作只能在复印件上做 |
| ③ **一份"出厂原样"的照片** | 将来要把机器交还给别人时，得把标签恢复成原样 |

第 ③ 样最容易被忽略。为什么需要它？
因为**如果你不恢复原样，下一个接手的人会以为那些是出厂标注**，
按着你填的内容去接线，全接错。

### 回到代码

| 指针 | 对应 | 什么时候有值 |
|---|---|---|
| `table_ptr` | ① 生效的那份 | 一直有（指向 ② 或指向远端内存里那份） |
| `cached_table` | ② 内核里的副本 | **只有"内核自己加载固件"时才有** |
| `clean_table` | ③ 出厂原样 | **只有"支持 detach"的驱动才有** |

四条路径的分配点：

| 场景 | `cached_table` | `clean_table` | 位置 |
|---|---|---|---|
| 没有资源表 | NULL | NULL | `rproc_set_rsc_table()` 直接 return 0，注释说 "Not having a resource table is acceptable" |
| 内核加载固件（正常启动） | kmemdup 出来的副本 | NULL | `drivers/remoteproc/remoteproc_core.c:1524-1531` |
| 接管已启动的远端（attach） | **NULL** | 有 `->detach` 时 kmemdup（`drivers/remoteproc/remoteproc_core.c:1481`），否则 NULL（`drivers/remoteproc/remoteproc_core.c:1485`） | `drivers/remoteproc/remoteproc_core.c:1488` 显式置 NULL |
| detach 之后 | NULL | 用完就 `kfree`（`drivers/remoteproc/remoteproc_core.c:1543`、`drivers/remoteproc/remoteproc_core.c:1578`） | —— |

**`cached_table` 是否为 NULL，就是"这个远端是谁启动的"的判据**（03 §B3.3）。
核心自己就是这么用的：`rproc_validate()` 在 `RPROC_DETACHED` 状态下
**要求 `cached_table` 必须为空**（`drivers/remoteproc/remoteproc_core.c:2236`），
否则返回 `-EINVAL`。

`clean_table` 的用法：detach 时把它 `memcpy` 回远端内存
（`drivers/remoteproc/remoteproc_core.c:1537`），
让远端的资源表回到"内核从没碰过"的状态。
这样 `DETACHED → ATTACHED → DETACHED → ATTACHED` 反复切换才不会累积污染。
配套还有一个 `WARN_ON(!clean_table)`（`drivers/remoteproc/remoteproc_core.c:1508`）——
说明作者认为"到这一步 `clean_table` 必须有值"。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"复印件"暗示两份内容一样，其实不是**。
   `cached_table` 里的 vring 地址是内核填的；
   固件加载完成后 `table_ptr` 切到远端内存那份，
   **内核会把 `cached_table` 的内容 `memcpy` 过去**，之后两份就分道扬镳了。

2. ⚠️ **"照片"暗示只读，`clean_table` 是会被 `kfree` 的**。
   而且 `kfree` 之后**指针没有被置 NULL**
   （`drivers/remoteproc/remoteproc_core.c:1543`、
   `drivers/remoteproc/remoteproc_core.c:1578`）——
   03 §C4 把这个记为一处清理不对称。

3. ⚠️ **类比暗示"三份都同时存在"，实际上从来不会**。
   看上面那张四路径表：**`cached_table` 和 `clean_table` 是互斥的**。
   要么是"内核启动的"（有 cached、无 clean），
   要么是"接管的"（无 cached、可能有 clean）。
   ⇒ 把它想成"三个槽位，同时最多填两个"更准确。

---

## §5 `start` 和 `attach` 差在哪？为什么需要 `detach`？

### 先举个例子

还是那台发电机。有两种到场方式：

| 方式 | 你要做的 |
|---|---|
| **冷启动**（start） | 检查油 → 合闸 → 按启动键 → 等它上转速 → 接上管道 |
| **接管**（attach） | 你到的时候**它已经在转了**（前一班的人开的）。你只需要**接上管道** |

还有第三种动作：

| **交班**（detach） | 你把管道拆掉走人，**但发电机继续转** |

第三种是关键：拆管道 ≠ 关机。

### 回到代码

| 动作 | 回调 | 会不会加载固件 | 会不会动复位 |
|---|---|---|---|
| `rproc_boot()` | `prepare` → `load` → `start` | 会 | 会（放开） |
| **attach**（在 `rproc_add()` 里自动触发） | **只有 `attach`**，`prepare` 不跑 | 不会 | **不会** |
| `rproc_detach()` | `detach` | —— | **不动**（远端继续跑） |
| `rproc_shutdown()` | `stop` → `unprepare` | —— | 会（按下） |

| 关键位置 | 行号 |
|---|---|
| `rproc_boot()` | `drivers/remoteproc/remoteproc_core.c:1907` |
| `rproc_detach()` | `drivers/remoteproc/remoteproc_core.c:2053` |
| `rproc_validate()` 要求 OFFLINE 有 `start`、DETACHED 有 `attach` | `drivers/remoteproc/remoteproc_core.c:2221`、`drivers/remoteproc/remoteproc_core.c:2229` |
| 参考实现（回调填得最全的） | `drivers/remoteproc/stm32_rproc.c:651`（attach）、`drivers/remoteproc/stm32_rproc.c:652`（detach）、`drivers/remoteproc/stm32_rproc.c:657`（get_loaded_rsc_table） |

**attach 场景的资源表来源不同**：不是从固件解析，
而是驱动通过 `.get_loaded_rsc_table` 告诉核心"远端内存里已经有一份，在这个地址"
（`include/linux/remoteproc.h:399-400`）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"接管只需要接管道"漏掉了准备工作**。
   ⚠️ **attach 路径不会调 `.prepare`**。
   如果你把时钟使能写在 `.prepare` 里，接管时钟引用计数就是 0，
   之后任何人调一次 `clk_disable_unprepare()` 都可能把远端的时钟关掉（05 §2.3）。
   ⇒ 类比里"发电机已经在转，油和闸都不用管"是对的；
   代码里"内核的时钟引用计数"这个账**没人替你记**。

2. ⚠️ **"交班"暗示对称，但 detach 之后不能再 attach 回来（自动地）**。
   detach 会把状态设成 `RPROC_DETACHED`，
   要重新接管得走 sysfs（`echo start`）或者重新 probe。
   而且 `clean_table` 在 detach 时被 `kfree` 了（§4），
   下一次接管要重新 kmemdup。

3. ⚠️ **arm64 上"接管"的实际支持面很窄**。
   本树只有 stm32（armv7）和少数 TI/高通型号实现了 `.attach`/`.detach`。
   ⇒ 读到"remoteproc 支持接管 bootloader 启动的远端"时要加一句
   "**如果你的驱动实现了那两个回调**"。

---

## §6 `da_to_va` 为什么要遍历链表？carveout 是什么？

### 先举个例子

远端处理器看到的地址和内核看到的地址**是两套编号**。
就像一栋楼有两套门牌：

```
   物业内部编号            住户看到的门牌
   ──────────            ─────────────
   物理地址 0xa0000000  ←→  远端地址 0x00000000
   物理地址 0xa0100000  ←→  远端地址 0x80000000
```

你手里有一本**对照表**，每一行是"一段连续区间的两套编号"。
有人报"远端地址 0x80001000 出问题了"，你就翻这本表：
找到第二行（起点 0x80000000），算出偏移 0x1000，得到物理地址 0xa0101000。

**carveout 就是这本对照表里的一行。**
名字来自 "carve out"（从 DDR 里"切出来"一块）。

### 回到代码

`rproc_da_to_va()` 的实际逻辑（`drivers/remoteproc/remoteproc_core.c:193-229`）：

| 步骤 | 位置 |
|---|---|
| 1. 驱动有 `ops->da_to_va` 就先问驱动 | `drivers/remoteproc/remoteproc_core.c:198-199` |
| 2. 驱动答不上（返回 NULL）就自己遍历 `rproc->carveouts` | —— |
| 3. 跳过没分配的（`!carveout->va`） | `drivers/remoteproc/remoteproc_core.c:207` |
| 4. `offset < 0` → 这块起点太高，试下一块 | `drivers/remoteproc/remoteproc_core.c:211` |
| 5. `offset + len > carveout->len` → 这块装不下，试下一块 | `drivers/remoteproc/remoteproc_core.c:215` |
| 6. 命中：`ptr = carveout->va + offset` | `drivers/remoteproc/remoteproc_core.c:218` |
| 7. 顺手回报"这块是不是 iomem" | `drivers/remoteproc/remoteproc_core.c:220-221` |

看这本对照表：
```sh
cat /sys/kernel/debug/remoteproc/remoteproc0/carveout_memories
```
（实现在 `drivers/remoteproc/remoteproc_debugfs.c:362`）

### 这个类比什么时候会误导你？

⚠️ **四处**：

1. ⚠️ **"翻表"暗示表是排好序的、能二分查找 —— 它不是**。
   `rproc->carveouts` 是一个**普通链表**，`rproc_da_to_va()` 是**线性扫描**，
   而且**没有任何锁保护**。
   ⇒ carveout 多的时候（几十块）每次地址转换都是 O(n)。
   trace 缓冲每次 read 都要走一遍（04 §7.2），这个开销是实打实的。

2. ⚠️ **`int offset` 会溢出**。
   `drivers/remoteproc/remoteproc_core.c:204` 那一句是
   `int offset = da - carveout->da;` —— `da` 是 `u64`，
   差值超过 2 GB 时 `int` 就装不下了。
   ⇒ 03 §B4.1 记录了这个隐患。**类比里"算偏移"是个纯算术动作，代码里它有位宽**。

3. ⚠️ **"每个门牌只对应一个内部编号"是错的**。
   两块 carveout 的 `da` 区间**可以重叠** —— 代码不检查。
   命中的是**链表里第一块能装下的**，顺序依赖登记顺序。
   ⇒ 这也是 06 §3.2 里"`memory-region` 索引顺序有语义"的深层原因。

4. ⚠️ **按名字找 carveout 有一个 32 字节的隐形上限**。
   `rproc_find_carveout_by_name()`（`drivers/remoteproc/remoteproc_core.c:252`）
   用的是一个 32 字节的栈缓冲，和 `fw_rsc_carveout.name[32]` 对齐。
   名字超长会被**静默截断**（05 §3.1）。

---

## §7 panic 的时候为什么只能"忙等"？

### 先举个例子

大楼失火，警报响了，所有人要立刻撤离。
撤离前你想给发电机房那边喊一句"赶紧把日志刷到硬盘上"。

问题是：**此刻电话系统已经断了**（panic 时调度器不可用），
你只能对着传声筒喊一句，然后**站在原地掐着表数秒**，
数到发电机房说的那个秒数，然后走。

你**收不到任何回话**。你只能相信它听到了、并且在那个秒数内做完了。

### 回到代码

`rproc_panic_handler()`（`drivers/remoteproc/remoteproc_core.c:2694`）：

| 步骤 | 位置 |
|---|---|
| 1. `rcu_read_lock()` + `list_for_each_entry_rcu()` 遍历所有远端 | `drivers/remoteproc/remoteproc_core.c:2701-2702` |
| 2. 跳过没实现 `ops->panic` 的 | `drivers/remoteproc/remoteproc_core.c:2703-2704` |
| 3. 跳过状态不是 RUNNING / ATTACHED 的 | `drivers/remoteproc/remoteproc_core.c:2706-2708` |
| 4. 回调返回"请给我多少毫秒"，取**所有远端的最大值** | `drivers/remoteproc/remoteproc_core.c:2710-2711` |
| 5. `mdelay(longest)` —— **忙等** | `drivers/remoteproc/remoteproc_core.c:2721` |
| 6. `return NOTIFY_DONE` | `drivers/remoteproc/remoteproc_core.c:2723` |

三个设计点连在一起才说得通：

| 设计 | 为什么必须这样 |
|---|---|
| 用 **RCU** 而不是 mutex | panic 上下文不能睡，拿不到 `rproc_list_mutex`（03 §B1.1） |
| 用 **`mdelay`** 而不是 `msleep` | 调度器已经不可用 |
| 回调返回**毫秒数**而不是"完成/未完成" | 因为**没有回话的通道** |

这也解释了 03 §B1.1 里那句 `synchronize_rcu()`
（`drivers/remoteproc/remoteproc_core.c:2576`）为什么必须有 ——
`rproc_del()` 要确保没有 panic 处理器正在遍历这个 rproc。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"掐表数秒"暗示等的时间是够的 —— 代码不保证**。
   `mdelay(longest)` 只是等了驱动**声称**需要的时间。
   远端要是慢了，日志就是丢了。⇒ 这是一个**best-effort 机制**，不是保证。

2. ⚠️ **"所有远端一起喊"漏掉了一个细节：`mdelay` 只等一次**。
   如果有三个远端分别要 10ms / 50ms / 20ms，
   代码取 `max` = 50ms，**串行地等 50ms 一次**，不是 10+50+20。
   ⇒ 这是对的（三个远端并行在刷），但**很容易读成"逐个等"**。
   `longest = max(longest, d)` 那一行（`drivers/remoteproc/remoteproc_core.c:2711`）
   是这个语义的唯一证据。

---

## §8 为什么"开机自动启动"默认是开的？

### 先举个例子

你买了一台带内置控制器的洗衣机。插上电，控制器就自己启动了 ——
**厂家默认认为"这台机器插电就该工作"**。

但如果控制器的固件文件放在一张**你还没插进去的 SD 卡上**，
插电那一刻控制器找不到固件，会亮一下红灯，然后待机等你。

### 回到代码

| 事实 | 位置 |
|---|---|
| `rproc_alloc()` 里 `rproc->auto_boot = true` | `drivers/remoteproc/remoteproc_core.c:2457` |
| DT 属性（**只有两家有**） | `drivers/remoteproc/imx_rproc.c:831`（`fsl,auto-boot`）、`drivers/remoteproc/stm32_rproc.c:754`（`st,auto-boot`） |
| 驱动按 SoC 变体查表 | `drivers/remoteproc/qcom_q6v5_pas.c:496` |
| 驱动硬编码关掉 | `drivers/remoteproc/pru_rproc.c:811`、`drivers/remoteproc/imx_dsp_rproc.c:1121` |

**"红灯亮一下然后待机"就是真实行为**：
`rproc_add()` 时固件还在 `/lib/firmware`，而 rootfs 可能还没挂上，
于是 dmesg 里出现一句 `request_firmware` 相关的失败 ——
**但远端后面还能通过 sysfs 手工启动**（06 §5.4）。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"厂家默认"暗示这是一个可以在 DT 里改的选项 —— 大多数平台不行**。
   ⚠️ remoteproc **没有通用的 DT auto-boot 开关**（06 §9）。
   只有 i.MX 和 STM32 定义了私有属性。
   其它平台想关，只能改驱动代码。

2. ⚠️ **"插电就启动"暗示是同步的 —— 它是异步的**。
   `auto_boot` 走的是 `request_firmware_nowait()`
   （失败时打 `request_firmware_nowait err: %d`，
   `drivers/remoteproc/remoteproc_core.c:1700`），
   所以 `rproc_add()` **返回 0 不代表远端启动成功**。
   ⇒ 想知道结果只能之后 `cat state`（07 §7.3）。

---

## §9 阅读下一篇

| 你现在想知道什么 | 去哪 |
|---|---|
| 这些函数的完整签名和返回值 | 01 篇 |
| §4 那三个指针的完整状态机 | 02 篇 §5 · 03 篇 §B3 |
| §5 的 attach/detach 在时间线上的确切位置 | 03 篇 Part A |
| §1 三层关系的代码级细节 | 04 篇 §1 |
| §3 那个平台设备中转的全部代码 | 04 篇 §1.1–§1.2 |
| 我要动手写驱动 | 05 篇 |
| §6 的 carveout 在 DTS 里怎么配 | 06 篇 §3 |
| §7 §8 提到的现象怎么观察 | 07 篇 |
| 本套文档的取证方法和未验证项 | 99 篇 |

### §9.1 本篇八个主题里最该记住的三条

1. **remoteproc / virtio / rpmsg 是叠起来的三层，不是一个东西的三个面**（§1）。
   ⇒ 你要写的驱动挂在哪一层，决定了用哪套 API。
2. **资源表是固件 ELF 里的一个 section，而且内核会往里写**（§2）。
   ⇒ 它既是"固件的需求清单"，也是"内核的回复单"。
3. **`cached_table` 是否为空 = 这个远端是谁启动的**（§4）。
   ⇒ 这是整个 attach/detach 机制的判据基石。
