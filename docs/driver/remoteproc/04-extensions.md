---
title: 扩展机制
---

# remoteproc 04：挂在核心旁边的几个独立框架

> 内核版本：**v6.1.158**（`drivers/remoteproc` 与 `include/linux/remoteproc.h` 均无本地修改）
> 目标架构：**arm64**
> 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

## §0 本篇的组织轴

**一节一个独立框架，节与节之间互不调用**。
它们的共同点只有一个：都挂在 03 篇那条时间线的某一个点上，
但彼此之间没有依赖关系 —— 你可以只读需要的那一节。

⚠️ 这一点和 03 篇正好相反：03 篇是**一条链**，读者必须顺着读；
本篇是**并列的七块**，跳读是设计意图。

### §0.1 分诊表：arm64 上哪几个真会碰到

| § | 框架 | 文件 | 行数 | arm64 上会碰到吗 | 编译开关 |
|---|---|---|---|---|---|
| §1 | **virtio / rpmsg 侧** | `drivers/remoteproc/remoteproc_virtio.c` | 603 | **一定会** —— 只要固件里有 `RSC_VDEV` | 随 `CONFIG_REMOTEPROC` 一起编（`builtin_platform_driver`） |
| §2 | **ELF 加载器** | `drivers/remoteproc/remoteproc_elf_loader.c` | 395 | **一定会** —— 34 个驱动里绝大多数直接用默认实现 | 同上，无独立开关 |
| §3 | **coredump** | `drivers/remoteproc/remoteproc_coredump.c` | 469 | 看驱动 —— **默认什么都不做**（见 §3.1） | 需要 `CONFIG_DEV_COREDUMP` |
| §4 | **sysfs** | `drivers/remoteproc/remoteproc_sysfs.c` | 275 | **一定会** —— `rproc_class` 是无条件注册的 | 无 |
| §5 | **debugfs** | `drivers/remoteproc/remoteproc_debugfs.c` | 430 | 看 `CONFIG_DEBUG_FS` | `CONFIG_DEBUG_FS` |
| §6 | **字符设备（cdev）** | `drivers/remoteproc/remoteproc_cdev.c` | 126 | 只有开了才有 | `CONFIG_REMOTEPROC_CDEV` |
| §7 | **trace 缓冲** | 跨 `remoteproc_core.c` + `remoteproc_debugfs.c` | —— | 固件里有 `RSC_TRACE` 才有 | 依赖 `CONFIG_DEBUG_FS` |
| §8 | **panic 通知链** | `drivers/remoteproc/remoteproc_core.c` 末尾 | —— | **一定会** —— 模块 init 时无条件注册 | 无 |

### §0.2 挂载点：每个框架接在 03 时间线的哪一点

```
 rproc_alloc()
     └─ §2 ELF 加载器的五个默认回调在这里被填进 ops 副本
     └─ §4 sysfs 属性组随 device_add() 出现（在 rproc_add 里）
 rproc_add()
     ├─ §6 cdev 在 device_add 之前创建
     ├─ §5 debugfs 目录在 device_add 之后创建
     └─ §4 sysfs 三个可写文件从这一刻起可用
 rproc_boot() → rproc_fw_boot()
     ├─ §2 sanity_check / get_boot_addr / parse_fw 依次被调
     └─ rproc_handle_resources()
            ├─ RSC_VDEV  → §1 注册 "rproc-virtio" 平台设备
            └─ RSC_TRACE → §7 创建 debugfs trace 文件
 rproc_start()
     └─ rproc_start_subdevices() → §1 rvdev 的 subdev->start 真正建 virtio 设备
 崩溃 → rproc_boot_recovery()
     └─ §3 coredump 在停机之后、重新加载固件之前被调
 panic
     └─ §8 通知链遍历 rproc_list
```

| 挂载点 | 位置 |
|---|---|
| ELF 默认回调填入 | `drivers/remoteproc/remoteproc_core.c:2411` 起五行 |
| cdev 创建 | `drivers/remoteproc/remoteproc_core.c:2279` |
| debugfs 目录创建 | `drivers/remoteproc/remoteproc_core.c:2292` |
| `RSC_VDEV` 分派 | `drivers/remoteproc/remoteproc_core.c:1011` 起的 `rproc_loading_handlers[]` |
| rvdev subdev 的 start | `drivers/remoteproc/remoteproc_core.c:1306` → `drivers/remoteproc/remoteproc_virtio.c:476` |
| coredump 调用点 | `drivers/remoteproc/remoteproc_core.c:1801` |
| panic 通知链注册 | `drivers/remoteproc/remoteproc_core.c:2728` |

---

## §1 virtio / rpmsg 侧：一个"资源表项 → 平台设备 → virtio 设备"的三级跳

### §1.1 先把三级跳的形状说清楚

这是整个 remoteproc 里最容易搞混的一段。**固件资源表里的一条 `RSC_VDEV`，
最终会变成三个内核对象**：

```
 固件资源表里的 fw_rsc_vdev 一条
     │  rproc_handle_resources() 分派
     ▼
 ① platform_device  name="rproc-virtio"      ← 父设备是 &rproc->dev
     │  被 rproc_virtio_driver 的 probe 认领
     ▼
 ② struct rproc_vdev（devm 分配在 ① 上）
     │  它把自己的 subdev 挂进 rproc->subdevs
     │  等到 rproc_start_subdevices() 调 subdev->start
     ▼
 ③ struct virtio_device                      ← 父设备是 ① 的 dev
     │  被 virtio 总线匹配
     ▼
 ④ struct rpmsg_device（如果 id 是 VIRTIO_ID_RPMSG）
        由 drivers/rpmsg/virtio_rpmsg_bus.c 负责，已经不在本子系统里
```

| 对象 | 创建位置 | 何时创建 |
|---|---|---|
| ① 平台设备 | `drivers/remoteproc/remoteproc_core.c:518` | `rproc_handle_resources()` 扫到 `RSC_VDEV` 时，**在 `rproc_start()` 之前** |
| ② `rproc_vdev` | `drivers/remoteproc/remoteproc_virtio.c:506` | ① 的 probe 里 |
| ③ `virtio_device` | `drivers/remoteproc/remoteproc_virtio.c:446` | `rproc_start_subdevices()` 之后，**远端已经在跑** |

**①② 和 ③ 的时间差是关键**：①② 发生在远端**还没启动**的时候
（要在启动前把 vring 内存分配好、地址回写进资源表，远端才知道去哪找），
③ 必须等远端**已经在跑**（不然远端不会响应 kick）。
这个时间差正是 `rproc_subdev` 机制存在的理由。

⚠️ **为什么中间要插一个平台设备**（而不是直接建 virtio 设备）？
`drivers/remoteproc/remoteproc_virtio.c:494` 起的 probe 给出了答案 ——
需要一个能挂 DMA 属性的 `struct device`：
`copy_dma_range_map()`（`drivers/remoteproc/remoteproc_virtio.c:29`）、
`set_dma_ops()`、`dma_coerce_mask_and_coherent()`、
以及后面 `of_reserved_mem_device_init_by_idx()` / `dma_declare_coherent_memory()`
都要往这个设备上挂。virtio 设备本身不适合承担这些。

### §1.2 `rproc_handle_vdev()`：只做校验和登记

`drivers/remoteproc/remoteproc_core.c:475`。它**不建 virtio 设备**，只做四件事：

| # | 动作 | 位置 |
|---|---|---|
| 1 | 长度校验：`struct_size(rsc, vring, rsc->num_of_vrings) + rsc->config_len > avail` → `-EINVAL` | `drivers/remoteproc/remoteproc_core.c:487-490` |
| 2 | 保留字节必须为 0 → 否则 `-EINVAL` | `drivers/remoteproc/remoteproc_core.c:493-496` |
| 3 | vring 数不能超过 2 → `too many vrings: %d` | `drivers/remoteproc/remoteproc_core.c:502-505` |
| 4 | 填 `rproc_vdev_data` 后 `platform_device_register_data(dev, "rproc-virtio", PLATFORM_DEVID_AUTO, …)` | `drivers/remoteproc/remoteproc_core.c:518` |

第 1 步（`drivers/remoteproc/remoteproc_core.c:486-487`）用的是 `struct_size()` + `size_add()` —— 这是内核的溢出安全宏，
比手写 `a * b + c` 靠谱。这也是核心里少数几处认真做过溢出防护的地方。

第 4 步用 `PLATFORM_DEVID_AUTO` 而不是 `rvdev_data.index`，
源码注释说明了原因：多个远端处理器各自的 `nb_vdev` 都从 0 开始数，
用 index 当设备 id 会在 sysfs 里撞名。

⚠️ 那条长长的函数头注释里还留着两句"欠债自白"：
`da` 目前**没被正确处理** —— 核心用 DMA API 动态分配，
**忽略固件硬编码的地址**，也不做 IOMMU 编程；
需要固定地址的固件只能改用 `RSC_DEVMEM` 表项来变通（注释原话是 "ouch, hacky!"）。

### §1.3 vring 的三个函数

| 函数 | 位置 | 何时调 | 做什么 |
|---|---|---|---|
| `rproc_parse_vring()` | `drivers/remoteproc/remoteproc_core.c:386` | rvdev probe 里，**先** | 只做校验 + 把 `num` / `align` 抄进 `rproc_vring`；`!num \|\| !align` → `-EINVAL` |
| `rproc_alloc_vring()` | `drivers/remoteproc/remoteproc_core.c:325` | rvdev probe 里，**后** | 找/建 carveout + `idr_alloc` 拿 notifyid + **回写 `rsc->vring[i].notifyid`** |
| `rproc_free_vring()` | `drivers/remoteproc/remoteproc_core.c:410` | rvdev remove / find_vqs 失败 | `idr_remove` + 条件性地清资源表项 |

`rproc_alloc_vring()` 的核心动作是**回写**：
notifyid 是内核用 `idr_alloc` 分配的（`drivers/remoteproc/remoteproc_core.c:367`），
分配完必须写回资源表 `rsc->vring[i].notifyid`
（`drivers/remoteproc/remoteproc_core.c:381`），远端才知道该用哪个编号敲门。
顺便还会更新 `rproc->max_notifyid`（`drivers/remoteproc/remoteproc_core.c:375-376`），
这个值有些驱动会用来配置中断资源。

carveout 名字是格式化出来的：`"vdev%dvring%d"`，
先找（`rproc_find_carveout_by_name`），找到就用 `rproc_check_carveout_da()` 校验；
找不到就 `rproc_mem_entry_init()` 登记一个延迟分配的（`drivers/remoteproc/remoteproc_core.c:348`）。
⇒ 这就是 03 §A4.2 说的"注册后延迟分配"机制的最主要用户。

⚠️`rproc_free_vring()` 里那段长注释（`drivers/remoteproc/remoteproc_core.c:418-428`）交代了一个非显然的事实：
它被调用时 `rproc_stop()` 已经跑过了，**远端内存里那份资源表可能已经不可访问**，
所以 `rproc->table_ptr` 此刻指向的是 `cached_table`。
但 `cached_table` **只在"内核启动的"场景下存在** ——
于是这里必须 `if (rproc->table_ptr)` 判一次
（`drivers/remoteproc/remoteproc_core.c:429`）才敢清 `da` 和 `notifyid`。
⇒ 又一次印证 03 §B3.3 的那条钥匙：**`cached_table` 是否为空 = 这个远端是谁启动的**。

### §1.4 `virtio_config_ops` 九个回调：全部读写同一块内存

`drivers/remoteproc/remoteproc_virtio.c:327` 起的 `rproc_virtio_config_ops`：

| 回调 | 位置 | 实现方式 |
|---|---|---|
| `get_features` | `drivers/remoteproc/remoteproc_virtio.c:246` | 返回 `rsc->dfeatures` |
| `finalize_features` | `drivers/remoteproc/remoteproc_virtio.c:266` | 写 `rsc->gfeatures` |
| `find_vqs` | `drivers/remoteproc/remoteproc_virtio.c:183` | 逐个 `rp_find_vq()` |
| `del_vqs` | `drivers/remoteproc/remoteproc_virtio.c:178` | 转调 `__rproc_virtio_del_vqs()` |
| `reset` | `drivers/remoteproc/remoteproc_virtio.c:234` | `rsc->status = 0` |
| `set_status` | `drivers/remoteproc/remoteproc_virtio.c:223` | 写 `rsc->status` |
| `get_status` | `drivers/remoteproc/remoteproc_virtio.c:213` | 读 `rsc->status` |
| `get` | `drivers/remoteproc/remoteproc_virtio.c:291` | `memcpy` 出配置空间 |
| `set` | `drivers/remoteproc/remoteproc_virtio.c:309` | `memcpy` 进配置空间 |

**九个回调里有七个的第一行都是同一句话**：

```
 rsc = (void *)rvdev->rproc->table_ptr + rvdev->rsc_offset;
```

⇒ **virtio 的"配置空间"和"状态寄存器"就是资源表里那条 `fw_rsc_vdev`**。
不存在别的地方。这也解释了为什么 `rsc_offset`（`include/linux/remoteproc.h:653`）
必须一直留着 —— 它是访问这一切的唯一钥匙。

配置空间的位置是**算出来的**：`cfg = &rsc->vring[rsc->num_of_vrings]`
（`drivers/remoteproc/remoteproc_virtio.c:299`）——
也就是紧跟在可变长 vring 数组之后。这正是 02 §3.5 说的
`fw_rsc_vdev` 后面那段"隐式尾部"。

`get` / `set` 都做了边界检查：
`offset + len > rsc->config_len || offset + len < len`
（`drivers/remoteproc/remoteproc_virtio.c:301`、
`drivers/remoteproc/remoteproc_virtio.c:319`）——
第二个条件是防 `offset + len` 整型回绕。越界只打
`access out of bounds` 然后**静默返回**（不报错给上层）。

### §1.5 双向门铃

```
 内核 → 远端                          远端 → 内核
 ─────────────                        ─────────────
 virtqueue_kick()                     驱动的中断处理函数
   → rproc_virtio_notify()              → rproc_vq_interrupt(rproc, notifyid)
       → rproc->ops->kick(rproc,            → idr_find(&rproc->notifyids, …)
                          notifyid)         → vring_interrupt(0, rvring->vq)
```

| 环节 | 位置 |
|---|---|
| `rproc_virtio_notify()` | `drivers/remoteproc/remoteproc_virtio.c:65` |
| `vq->priv = rvring` 的赋值（`notify` 靠它找到 notifyid） | `drivers/remoteproc/remoteproc_virtio.c:158` |
| `rproc_vq_interrupt()` | `drivers/remoteproc/remoteproc_virtio.c:89` |
| `idr_find` + 双重判空 | `drivers/remoteproc/remoteproc_virtio.c:95-97` |

`rproc_vq_interrupt()` 返回 `IRQ_NONE` 的两种情况：
`idr_find` 没找到，**或者** 找到了但 `rvring->vq` 是 NULL
（还没 `find_vqs` 或已经 `del_vqs`）。⇒ 这是它唯一的自我保护。

⚠️ **`ops->kick` 是必需回调，但检查点在很后面**：
`rproc_add_virtio_dev()` 的第一件事就是
`if (rproc->ops->kick == NULL) → -EINVAL` +
`".kick method not defined for %s"`（`drivers/remoteproc/remoteproc_virtio.c:378-382`）。
⇒ 一个不实现 `kick` 的驱动能顺利 `rproc_add()`、能顺利 `rproc_boot()`
（固件都加载完了），**只在 subdev start 那一步失败**，然后整个启动回滚。
错误信息在 dmesg 里离真正的原因（Kconfig / 驱动没写 kick）很远。

### §1.6 两处引用计数

| 位置 | 动作 | 配对释放 |
|---|---|---|
| `drivers/remoteproc/remoteproc_virtio.c:564` | `get_device(&rproc->dev)` —— rvdev 保存了 rproc 指针 | `drivers/remoteproc/remoteproc_virtio.c:590`（**platform remove** 里） |
| `drivers/remoteproc/remoteproc_virtio.c:444` | `get_device(dev)`（① 那个平台设备） | `drivers/remoteproc/remoteproc_virtio.c:357`（vdev 的 release 回调里） |

源码在 `drivers/remoteproc/remoteproc_virtio.c:556-563` 专门写了段注释解释第一处：
rvdev 间接持有 rproc 指针，所以必须加引用，
而且**只能在 platform remove 里减** —— 不能在 subdev stop 里减，
因为 stop/start 会反复发生。

`rproc_virtio_dev_release()`（`drivers/remoteproc/remoteproc_virtio.c:347`）
按逆序拆第二处：`kfree(vdev)` →
`of_reserved_mem_device_release()` → `dma_release_coherent_memory()` →
`put_device(&rvdev->pdev->dev)`。

### §1.7 vdev buffer：三条分支

`rproc_add_virtio_dev()`（`drivers/remoteproc/remoteproc_virtio.c:370`）
在建 virtio 设备之前要先决定"rpmsg 的数据缓冲从哪来"：

| 情况 | 做法 | 位置 |
|---|---|---|
| 有名为 `vdev%dbuffer` 的 carveout，且 `of_resm_idx != -1` | `of_reserved_mem_device_init_by_idx()` 把 DTS 里那块 reserved memory 绑到 ① 上 | `drivers/remoteproc/remoteproc_virtio.c:389-398` |
| 有该 carveout，但不是 reserved-memory 来的 | `dma_declare_coherent_memory(dev, pa, mem->da, mem->len)`；`mem->va` 已映射时先 `dev_warn("vdev %d buffer already mapped")`（`drivers/remoteproc/remoteproc_virtio.c:401`）再 `rproc_va_to_pa()` 反算物理地址；`dma_declare_coherent_memory` 在 `drivers/remoteproc/remoteproc_virtio.c:410` | `drivers/remoteproc/remoteproc_virtio.c:400-417` |
| **没有该 carveout** | 尝试从父节点的 `memory-region` **索引 0** 继承，且**不检查返回值** | `drivers/remoteproc/remoteproc_virtio.c:429` |

⚠️ 第三条分支的注释明确说"失败不致命，分配会回落到全局池"。
⇒ **DTS 里没配 `memory-region` 不会报错**，rpmsg 缓冲会从系统 CMA 里出，
远端很可能访问不到那块地址 —— 症状是远端启动了但 rpmsg 通道一条消息都收不到。
这条静默失效会在 06 篇里单独画决策树。

### §1.8 一个真实限制：不支持 packed ring

`rproc_transport_features()`（`drivers/remoteproc/remoteproc_virtio.c:256`）
无条件 `__virtio_clear_bit(vdev, VIRTIO_F_RING_PACKED)`。
注释给了理由：remoteproc 用的是 `vring_new_virtqueue()`，
在**预分配好的内存**上建环，packed ring 那套走不通。

同一个函数（`rproc_virtio_finalize_features`，
`drivers/remoteproc/remoteproc_virtio.c:266`）里还有一句
`BUG_ON((u32)vdev->features != vdev->features)`
（`drivers/remoteproc/remoteproc_virtio.c:280`）——
因为 `fw_rsc_vdev.gfeatures` 只有 **32 位**。
⇒ 又一处固件 ABI 的 32 位约束（03 §C5 那条主线）。
这是**核心七个 `.c` 里唯一一处 `BUG_ON`**（另有两处 `WARN_ON`：`drivers/remoteproc/remoteproc_core.c:158` 和 `drivers/remoteproc/remoteproc_core.c:1508`）。

---

## §2 ELF 加载器：五个默认回调

### §2.1 五个函数各管一段

| 函数 | 位置 | 对应 ops 成员 | 干什么 |
|---|---|---|---|
| `rproc_elf_sanity_check()` | `drivers/remoteproc/remoteproc_elf_loader.c:37` | `sanity_check` | 校验 ELF 头 |
| `rproc_elf_get_boot_addr()` | `drivers/remoteproc/remoteproc_elf_loader.c:128` | `get_boot_addr` | 返回 `e_entry` |
| `rproc_elf_load_segments()` | `drivers/remoteproc/remoteproc_elf_loader.c:160` | `load` | 把 `PT_LOAD` 段搬进远端内存 |
| `rproc_elf_load_rsc_table()` | `drivers/remoteproc/remoteproc_elf_loader.c:326` | `parse_fw` | 从 `.resource_table` 节里 kmemdup 出 `cached_table` |
| `rproc_elf_find_loaded_rsc_table()` | `drivers/remoteproc/remoteproc_elf_loader.c:372` | `find_loaded_rsc_table` | 算出**远端内存里**那份资源表的虚拟地址 |

内部还有一个不导出的 `find_table()`（`drivers/remoteproc/remoteproc_elf_loader.c:248`），
后两个函数共用它 —— 它负责在节头表里找名字为 `.resource_table` 的那一节，
并做一整串边界校验。

### §2.2 "全有或全无"的默认填充（再说一遍）

01 §7 和 03 §A1.2 都提过，这里给出确切的代码形状。
`rproc_alloc_ops()`（`drivers/remoteproc/remoteproc_core.c:2397`）：

| 行 | 内容 |
|---|---|
| `drivers/remoteproc/remoteproc_core.c:2404-2405` | `if (!rproc->ops->coredump) rproc->ops->coredump = rproc_coredump;` |
| `drivers/remoteproc/remoteproc_core.c:2407-2408` | `if (rproc->ops->load) return 0;` |
| `drivers/remoteproc/remoteproc_core.c:2411-2415` | 五个 ELF 默认值一次性全填 |

⇒ **判据只有 `ops->load` 一个**。驱动只要自己实现了 `load`，
另外四个（`parse_fw` / `find_loaded_rsc_table` / `sanity_check` / `get_boot_addr`）
就**一个都不会**被填默认值 —— 即使它们是 NULL。
这时靠 `remoteproc_internal.h` 里的包装函数兜底：
`rproc_parse_fw()` 返回 0（`drivers/remoteproc/remoteproc_internal.h:180`）、
`rproc_find_loaded_rsc_table()` 返回 NULL（`drivers/remoteproc/remoteproc_internal.h:200`）。
⇒ **"没有资源表"这件事不报错**，远端会被启动，但没有 vdev、没有 trace、没有 carveout。

反过来说：驱动想"用 ELF 加载但换掉 sanity_check"是**做不到**的
—— 只要它自己填了 `load`，就得把四个都自己填上。
本树里的做法是显式赋值：例如
`rproc_elf_sanity_check` 在 `drivers/remoteproc/*.c` 里被
（除 `remoteproc_elf_loader.c` 和 `remoteproc_core.c` 之外）引用 **7** 次，
`rproc_elf_get_boot_addr` **8** 次，`rproc_elf_load_segments` **7** 次，
`rproc_elf_load_rsc_table` **6** 次，`rproc_elf_find_loaded_rsc_table` **4** 次。
⇒ 数字不齐正说明驱动是**逐个挑**着复用的。

### §2.3 32/64 位双支持靠宏，不靠分支

`drivers/remoteproc/remoteproc_elf_helpers.h`（122 行）里全是
`elf_hdr_get_e_phnum(class, hdr)` 这种形状的访问器 ——
`class` 是运行时的 `ELFCLASS32` / `ELFCLASS64`，宏内部按它选结构体。

**这是索引工具的盲区**：这些访问器不是函数，`search_graph` 找不到调用关系。
读这一族代码必须直接看 `drivers/remoteproc/remoteproc_elf_helpers.h`。

一个真实的溢出防线：`rproc_u64_fit_in_size_t()`
（`drivers/remoteproc/remoteproc_internal.h:220`）在
`drivers/remoteproc/remoteproc_elf_loader.c:204` 和
`drivers/remoteproc/remoteproc_elf_loader.c:387` 被调用 ——
这两处是**真的返回错误**，不是像 03 §C5 那两处只打警告。

---

## §3 coredump：三种模式，但默认什么都不做

### §3.1 ⚠️ 先说结论：默认路径是"静默跳过"

`rproc_coredump()`（`drivers/remoteproc/remoteproc_coredump.c:234`）开头两道门：

| 门 | 条件 | 位置 |
|---|---|---|
| 1 | `list_empty(&rproc->dump_segments) \|\| dump_conf == RPROC_COREDUMP_DISABLED` → **直接 return** | `drivers/remoteproc/remoteproc_coredump.c:247-249` |
| 2 | `class == ELFCLASSNONE` → 打 `Elf class is not set` 后 return | `drivers/remoteproc/remoteproc_coredump.c:251-254` |

三个"默认值"合起来决定了这件事：

| 字段 | 初值 | 谁能改 |
|---|---|---|
| `dump_segments` | 空链表 | 只有驱动调 `rproc_coredump_add_segment()` / `add_custom_segment()` |
| `dump_conf` | `RPROC_COREDUMP_DISABLED`（= 枚举第 0 项，`include/linux/remoteproc.h:464-468`；`rproc_alloc()` 用 kzalloc，不显式赋值） | sysfs `coredump` 或 debugfs `coredump` |
| `elf_class` | `ELFCLASSNONE`（`drivers/remoteproc/remoteproc_core.c:2458`） | **只有** `rproc_coredump_set_elf_info()`（`drivers/remoteproc/remoteproc_coredump.c:118-119`） |

⇒ **三个条件全都要驱动主动配合**，缺任何一个，崩溃恢复时就一句日志都不出。
特别是 `elf_class`：ELF 加载器**并不会**顺手把固件的 class 记下来 ——
在 `drivers/remoteproc/remoteproc_elf_loader.c` 里 grep `elf_class` 结果是 **0** 次。

本树里主动调 `rproc_coredump_set_elf_info()` 的驱动共 **6 处 / 5 个文件**，
全部传 `ELFCLASS32, EM_NONE`：
`drivers/remoteproc/qcom_q6v5_adsp.c:598`、
`drivers/remoteproc/qcom_q6v5_mss.c:1600`、
`drivers/remoteproc/qcom_q6v5_mss.c:1955`、
`drivers/remoteproc/qcom_q6v5_pas.c:497`、
`drivers/remoteproc/qcom_wcnss.c:576`、
`drivers/remoteproc/stm32_rproc.c:818`。
⇒ **想在 arm64 上真正拿到 coredump，实际只有高通和 ST 这两家的平台开箱可用。**

### §3.2 三个登记 API 的区别

| API | 位置 | `segment->dump` | `segment->priv` |
|---|---|---|---|
| `rproc_coredump_add_segment()` | `drivers/remoteproc/remoteproc_coredump.c:47` | NULL ⇒ 走通用拷贝 | 不设置 |
| `rproc_coredump_add_custom_segment()` | `drivers/remoteproc/remoteproc_coredump.c:78` | 驱动给的 `dumpfn` | 驱动给的 `priv` |
| `rproc_coredump_set_elf_info()` | `drivers/remoteproc/remoteproc_coredump.c:113` | —— | —— |

`priv` 有第二个用途：在 `rproc_coredump_using_sections()` 里它被当作**节名字符串**用
（`drivers/remoteproc/remoteproc_coredump.c:441` 把 `segment->priv` 传给 `elf_strtbl_add()`）。
⇒ 同一个字段在两条路径上语义不同：普通 coredump 里它是给 `dumpfn` 的上下文，
sections 版里它必须是一个可打印的名字。
这是一个真实的**字段复用陷阱**，`include/linux/remoteproc.h:480-490` 的注释没说清。

### §3.3 三种 `dump_conf` 的实际差别

| 模式 | vmalloc 多大 | 段数据什么时候读 | 恢复流程会不会被卡住 |
|---|---|---|---|
| `RPROC_COREDUMP_DISABLED` | 不分配 | 不读 | 不卡 |
| `RPROC_COREDUMP_ENABLED` | **ELF 头 + 全部段数据** | 立刻，在 `rproc_coredump()` 里同步拷完 | 不卡（`dev_coredumpv()` 之后直接 return，`drivers/remoteproc/remoteproc_coredump.c:312`） |
| `RPROC_COREDUMP_INLINE` | 只有 ELF 头 | **等用户态读 `/sys/class/devcoredump/…/data` 时才现读** | ⚠️ **会卡** —— `wait_for_completion()`（`drivers/remoteproc/remoteproc_coredump.c:328`） |

⚠️ INLINE 模式的代价写在源码注释里：
"Wait until the dump is read and free is called.
Data is freed by devcoredump framework automatically after 5 minutes."
⇒ **没人去读那个文件，恢复流程就在这里挂 5 分钟**。
在产品上开 INLINE 必须配一个自动收集的 udev 规则。

解锁靠 `rproc_coredump_free()`（`drivers/remoteproc/remoteproc_coredump.c:125`）里的
`complete()`（`drivers/remoteproc/remoteproc_coredump.c:130`），
它是 `dev_coredumpm()` 的 free 回调。

### §3.4 通用拷贝路径的一个细节

`rproc_copy_segment()`（`drivers/remoteproc/remoteproc_coredump.c:151`）：

| 情况 | 做法 |
|---|---|
| 有 `segment->dump` | 直接交给驱动的回调 |
| 没有 | `rproc_da_to_va()` → 成功则按 `is_iomem` 选 `memcpy_fromio()` 或 `memcpy()` |
| `rproc_da_to_va()` 返回 NULL | 打一行 `invalid copy request for segment …`，然后 **`memset(dest, 0xff, size)`**（`drivers/remoteproc/remoteproc_coredump.c:166`） |

最后这一条很实用：**dump 文件里一整片 `0xff` 就是"这一段根本没映射上"的信号**，
不是远端真的写了 0xff。排查时先去 dmesg 找那句 `invalid copy request`。

### §3.5 `rproc_coredump_using_sections()` 是另一个入口

`drivers/remoteproc/remoteproc_coredump.c:343`，和 `rproc_coredump()` **并列**，不是它的子函数。
区别只有一点：用 **section header** 而不是 program header 组织 ELF，
因此每段能带一个名字（`str_tbl = "STR_TBL"`，`drivers/remoteproc/remoteproc_coredump.c:357`）。
前两道门、三种 `dump_conf` 的语义、`wait_for_completion` 的行为全部一样
（`drivers/remoteproc/remoteproc_coredump.c:359`、
`drivers/remoteproc/remoteproc_coredump.c:452`、
`drivers/remoteproc/remoteproc_coredump.c:467`）。

本树里只有一个使用者：`drivers/remoteproc/qcom_common.c:177`。
⇒ 它是**为高通的 minidump 场景造的**，别的平台不用。

---

## §4 sysfs：五个属性，三个能写

`drivers/remoteproc/remoteproc_sysfs.c`。`rproc_class`（`drivers/remoteproc/remoteproc_sysfs.c:257`）
在模块 init 时无条件 `class_register()`（`drivers/remoteproc/remoteproc_sysfs.c:265`），
所以这五个文件在 arm64 上**一定存在**。

| 文件 | 权限 | 读什么 | 写什么 | 位置 |
|---|---|---|---|---|
| `name` | RO | `rproc->name` | —— | `drivers/remoteproc/remoteproc_sysfs.c:213` |
| `state` | RW | `rproc_state_string[]` | `start` / `stop` / `detach` | `drivers/remoteproc/remoteproc_sysfs.c:178`、`drivers/remoteproc/remoteproc_sysfs.c:189` |
| `recovery` | RW | `enabled` / `disabled` | `enabled` / `disabled` / `recover` | `drivers/remoteproc/remoteproc_sysfs.c:13`、`drivers/remoteproc/remoteproc_sysfs.c:45` |
| `coredump` | RW | `disabled` / `enabled` / `inline` | 同左三者 | `drivers/remoteproc/remoteproc_sysfs.c:80`、`drivers/remoteproc/remoteproc_sysfs.c:104` |
| `firmware` | RW | 当前固件名 | 新固件名 | `drivers/remoteproc/remoteproc_sysfs.c:131`、`drivers/remoteproc/remoteproc_sysfs.c:149` |

### §4.1 `state` 的写语义

`state_store()`（`drivers/remoteproc/remoteproc_sysfs.c:189`）三个动词：

| 写入 | 调用 | 位置 |
|---|---|---|
| `start` | `rproc_boot()`，失败时**额外打一行** `Boot failed: %d` | `drivers/remoteproc/remoteproc_sysfs.c:197-199` |
| `stop` | `rproc_shutdown()`，失败**不打日志** | `drivers/remoteproc/remoteproc_sysfs.c:201` |
| `detach` | `rproc_detach()`，失败**不打日志** | `drivers/remoteproc/remoteproc_sysfs.c:203` |
| 其它 | `Unrecognised option: %s` + `-EINVAL` | `drivers/remoteproc/remoteproc_sysfs.c:205-206` |

三个分支都用 `sysfs_streq()` —— 它自动忽略末尾换行，
所以 `echo start > state` 能正常工作。
⚠️ 对比 debugfs 和 cdev 用的是 `strncmp(buf, "start", count)`，
**必须自己处理换行**（见 §5.2、§6.1）。

### §4.2 `state_show` 的越界钳制

```
 state = rproc->state > RPROC_LAST ? RPROC_LAST : rproc->state;
 return sprintf(buf, "%s\n", rproc_state_string[state]);
```

| 内容 | 位置 |
|---|---|
| 钳制 | `drivers/remoteproc/remoteproc_sysfs.c:184` |
| 输出 | `drivers/remoteproc/remoteproc_sysfs.c:185` |
| `rproc_state_string[]`（含 `[RPROC_LAST] = "invalid"`） | `drivers/remoteproc/remoteproc_sysfs.c:166-175` |

数组注释（`drivers/remoteproc/remoteproc_sysfs.c:164`）写着
"Always keep in sync with enum rproc_state" —— 这是一处**必须手工维护的同步点**，
枚举加了值而这里忘了加，`state` 文件就会读出 NULL 指针。
钳制只防了 `>` 的情况，防不了枚举中间有空洞。

02 §10 提过：`RPROC_SUSPENDED` 在本树的核心里没有任何写入点
（只有 `drivers/remoteproc/omap_remoteproc.c` 写它），
所以 arm64 上 `state` 永远不会读出 `suspended`。

### §4.3 `recovery` 的写语义：三选一，其中一个不改状态

| 写入 | `recovery_disabled` | 是否立刻触发恢复 | 位置 |
|---|---|---|---|
| `enabled` | ← false | **是** | `drivers/remoteproc/remoteproc_sysfs.c:51-54` |
| `disabled` | ← true | 否 | `drivers/remoteproc/remoteproc_sysfs.c:55-56` |
| `recover` | **不变** | 是 | `drivers/remoteproc/remoteproc_sysfs.c:57-59` |

`recover` 这个动词是为调试设计的：源码注释（debugfs 版在
`drivers/remoteproc/remoteproc_debugfs.c:200-207`）解释得更详细 ——
调试期间希望"恢复一次但保持 recovery 关着"，
这样下一次崩溃仍然会停在 CRASHED 让人去看现场。

⚠️ 写 `enabled` 会**顺手触发一次恢复**。
如果此刻远端状态不是 `RPROC_CRASHED`，`rproc_trigger_recovery()` 会在
`drivers/remoteproc/remoteproc_core.c:1840` 那个复查处安静地退出 ——
所以这个副作用通常无害，但它确实是个副作用。

### §4.4 `coredump` 写入的唯一硬限制

`coredump_store()` 第一件事：`state == RPROC_CRASHED` →
`can't change coredump configuration` + **`-EBUSY`**
（`drivers/remoteproc/remoteproc_sysfs.c:110-112`）。

⚠️ 更正一处措辞：`-EBUSY` 在核心七个 `.c` 里**一共三处**，不是一处 ——
`drivers/remoteproc/remoteproc_sysfs.c:112`（唯一以 `return -EBUSY` 形式出现的）、
`drivers/remoteproc/remoteproc_debugfs.c:92`（`err = -EBUSY`）、
`drivers/remoteproc/remoteproc_core.c:2186`（`ret = -EBUSY`，`rproc_set_firmware()` 里）。
01 §10.1 的统计按"return 形式 / 赋值形式"分开列，本身没错；本节先前的转述漏了赋值形式。
详见 07 §6.1。
理由是崩溃处理正在进行中，此刻改 `dump_conf` 会让 `rproc_coredump()` 半途换语义。

---

## §5 debugfs：sysfs 的超集，多两个只读视图和一个崩溃注入

`drivers/remoteproc/remoteproc_debugfs.c`。根目录在
`debugfs_create_dir(KBUILD_MODNAME, NULL)`（`drivers/remoteproc/remoteproc_debugfs.c:424`），
即 `/sys/kernel/debug/remoteproc/`；每个远端一个子目录
（`drivers/remoteproc/remoteproc_debugfs.c:405`）。

| 文件 | 权限 | 只有 debugfs 才有 | 位置 |
|---|---|---|---|
| `name` | 0400 | 否（sysfs 也有） | `drivers/remoteproc/remoteproc_debugfs.c:407` |
| `recovery` | 0600 | 否 | `drivers/remoteproc/remoteproc_debugfs.c:409` |
| `coredump` | 0600 | 否 | `drivers/remoteproc/remoteproc_debugfs.c:417` |
| **`crash`** | 0200（只写） | **是** | `drivers/remoteproc/remoteproc_debugfs.c:411` |
| **`resource_table`** | 0400 | **是** | `drivers/remoteproc/remoteproc_debugfs.c:413` |
| **`carveout_memories`** | 0400 | **是** | `drivers/remoteproc/remoteproc_debugfs.c:415` |
| `trace0` / `trace1` / … | 0400 | 是（固件有 `RSC_TRACE` 才出现） | `drivers/remoteproc/remoteproc_debugfs.c:389` |

### §5.1 `crash`：唯一的故障注入入口

`rproc_crash_write()`（`drivers/remoteproc/remoteproc_debugfs.c:252`）：
`kstrtouint_from_user()` 读一个整数
（`drivers/remoteproc/remoteproc_debugfs.c:259`），
然后**原封不动**交给 `rproc_report_crash(rproc, type)`
（`drivers/remoteproc/remoteproc_debugfs.c:263`）。

越界的 type 是安全的：`rproc_crash_to_string()`
（`drivers/remoteproc/remoteproc_core.c:69`）做了
`if (type < ARRAY_SIZE(rproc_crash_names))`，否则返回 `"unknown"`
（`drivers/remoteproc/remoteproc_core.c:70-73`）。
⇒ `echo 99 > crash` 会在 dmesg 里打 `crash detected in …: type unknown`，
然后照样走完整的恢复流程。**这是验证恢复路径最省事的办法**（07 篇会展开）。

| 合法 type | 名字 |
|---|---|
| 0 = `RPROC_MMUFAULT` | `mmufault` |
| 1 = `RPROC_WATCHDOG` | `watchdog` |
| 2 = `RPROC_FATAL_ERROR` | `fatal error` |

### §5.2 debugfs 的字符串比较没有 sysfs 那么宽容

`rproc_recovery_write()`（`drivers/remoteproc/remoteproc_debugfs.c:208`）和
`rproc_coredump_write()`（`drivers/remoteproc/remoteproc_debugfs.c:71`）
都是自己动手：

| 步骤 | recovery | coredump |
|---|---|---|
| 长度检查 | `count < 1 \|\| count > sizeof(buf)`（`buf[10]`） | 同（`buf[20]`），`drivers/remoteproc/remoteproc_debugfs.c:79-80` |
| `copy_from_user` 失败 | `-EFAULT` | `-EFAULT`，`drivers/remoteproc/remoteproc_debugfs.c:84` |
| 手工去掉末尾 `\n` | 有 | 有 |
| 状态门 | 无 | `RPROC_CRASHED` → `-EBUSY`（`drivers/remoteproc/remoteproc_debugfs.c:90-93`） |
| 分支 | `enabled` / `disabled` / `recover`（`drivers/remoteproc/remoteproc_debugfs.c:227-238`） | `disabled` / `enabled` / `inline`（`drivers/remoteproc/remoteproc_debugfs.c:96-105`） |

⚠️ 用的是 `strncmp(buf, "enabled", count)` —— **比较长度是用户写入的字节数**。
写 `en` 就能匹配上 `enabled`（`strncmp` 只比前 2 字节）。
这是 debugfs 的常见宽松写法，不是缺陷，但和 sysfs 的 `sysfs_streq` 语义确实不同。

### §5.3 两个只读视图

| 文件 | 实现 | 特点 |
|---|---|---|
| `resource_table` | `rproc_rsc_table_show()`（`drivers/remoteproc/remoteproc_debugfs.c:275`） | 读的是 `rproc->table_ptr`，所以**远端在跑的时候读到的是远端内存里那份**（含内核回写进去的 vring 地址）；`table_ptr` 为 NULL 时打 `No resource table found`（`drivers/remoteproc/remoteproc_debugfs.c:287`） |
| `carveout_memories` | `rproc_carveouts_show()`（`drivers/remoteproc/remoteproc_debugfs.c:362`） | 遍历 `rproc->carveouts`，逐项打 name / va / dma / da / len |

`resource_table` 是**验证"内核到底回写了什么"的唯一窗口**。
调 rpmsg 不通时的标准动作：启动后 `cat resource_table`，
看 `vdev` 那条里的 vring `da` 和 `notifyid` 是不是合理值
（`notifyid` 应该是 0 和 1，`da` 不该是 0）。

⚠️ 它只认四种类型（`types[] = {"carveout", "devmem", "trace", "vdev"}`，
`drivers/remoteproc/remoteproc_debugfs.c:277`）。
厂商资源（`RSC_VENDOR_START` 以上）会走 `switch` 的 default，
在这个文件里**看不到**。

---

## §6 字符设备：给"想拿 fd 管生命周期"的用户态

`drivers/remoteproc/remoteproc_cdev.c`，126 行，`CONFIG_REMOTEPROC_CDEV` 控制。
关掉时 `remoteproc_internal.h:89-106` 提供空实现，所以核心代码不用条件编译。

| 元素 | 位置 |
|---|---|
| 主设备号申请（最多 **64** 个远端，`NUM_RPROC_DEVICES`） | `drivers/remoteproc/remoteproc_cdev.c:18`、`drivers/remoteproc/remoteproc_cdev.c:123` |
| `rproc_fops`（write / ioctl / compat_ioctl / release） | `drivers/remoteproc/remoteproc_cdev.c:91-96` |
| 创建：`cdev_init` → `devt` → `cdev_set_parent` → `cdev_add` | `drivers/remoteproc/remoteproc_cdev.c:102`、`:105`、`:106`、`:107` |
| 销毁 | `drivers/remoteproc/remoteproc_cdev.c:116` |

`alloc_chrdev_region()` 失败时只 `pr_err`，**不返回错误**
（`drivers/remoteproc/remoteproc_cdev.c:123-125`，`rproc_init_cdev()` 是 `void`）。
⇒ 主设备号申请失败后，每个 `rproc_add()` 都会在 `cdev_add` 处失败，
错误信息是 `Failed to add char dev for %s`，离真正的原因很远。

### §6.1 `write` 接口：和 sysfs `state` 同样的三个动词

`rproc_cdev_write()`（`drivers/remoteproc/remoteproc_cdev.c:21`）：
`start` / `stop` / `detach`，分别调 `rproc_boot()` / `rproc_shutdown()` / `rproc_detach()`
（`drivers/remoteproc/remoteproc_cdev.c:34-43`）。

⚠️`char cmd[10]` + `if (!len || len > sizeof(cmd)) return -EINVAL`
（`drivers/remoteproc/remoteproc_cdev.c:27-28`）+ `strncmp(cmd, "start", len)`。
和 debugfs 一样是**前缀匹配**，而且**不去换行**
—— `echo start > /dev/remoteproc0` 写进来的是 `"start\n"`（6 字节），
`strncmp(cmd, "start", 6)` 比第 6 个字节时 `'\n'` vs `'\0'` **不相等** ⇒ 失败。
⇒ **用 `printf start > /dev/remoteproc0` 或 `echo -n`**，这是这个接口的必知用法。

### §6.2 两个 ioctl：把"关掉 fd"和"关掉远端"绑起来

| ioctl | 行为 | 位置 |
|---|---|---|
| `RPROC_SET_SHUTDOWN_ON_RELEASE` | `rproc->cdev_put_on_release = !!param` | `drivers/remoteproc/remoteproc_cdev.c:55-60` |
| `RPROC_GET_SHUTDOWN_ON_RELEASE` | 回读该标志 | `drivers/remoteproc/remoteproc_cdev.c:61-66` |
| 其它 | `Unsupported ioctl` + `-EINVAL` | `drivers/remoteproc/remoteproc_cdev.c:67-69` |

`rproc_cdev_release()`（`drivers/remoteproc/remoteproc_cdev.c:75`）：

| 条件 | 动作 | 位置 |
|---|---|---|
| `!cdev_put_on_release` | 直接返回 0（默认行为：关 fd 不影响远端） | `drivers/remoteproc/remoteproc_cdev.c:80-81` |
| `state == RPROC_RUNNING` | `rproc_shutdown()`，**返回值被丢弃** | `drivers/remoteproc/remoteproc_cdev.c:83-84` |
| `state == RPROC_ATTACHED` | `rproc_detach()`，返回值透传 | `drivers/remoteproc/remoteproc_cdev.c:85-86` |

这套机制的价值：**用户态进程崩溃时远端能被自动收拾干净**。
它是 sysfs 接口做不到的事（sysfs 没有"打开者"这个概念）。
⇒ 需要"守护进程管远端"的场景就开 `CONFIG_REMOTEPROC_CDEV`。

⚠️`RPROC_DETACHED` 状态**不在两个分支里** —— 已经 detach 过的远端，
关 fd 时什么都不做（正确，但要知道）。

---

## §7 trace 缓冲：一个最轻量的固件日志通道

### §7.1 三方分工

| 谁 | 做什么 | 位置 |
|---|---|---|
| 固件 | 在资源表里放一条 `RSC_TRACE`，指明 `da` 和 `len` | `include/linux/remoteproc.h:234` 的 `fw_rsc_trace` |
| 核心 | `rproc_handle_trace()` 建 `rproc_debug_trace` + 挂进 `rproc->traces` + 建 debugfs 文件 | `drivers/remoteproc/remoteproc_core.c:545` |
| debugfs | `rproc_trace_read()` 每次读都重新做地址转换 | `drivers/remoteproc/remoteproc_debugfs.c:126` |

| 关键动作 | 位置 |
|---|---|
| 保留字节校验 | `drivers/remoteproc/remoteproc_core.c:558-561` |
| 文件名 `trace%d`（`rproc->num_traces` 递增） | `drivers/remoteproc/remoteproc_core.c:578` |
| 建 debugfs 文件 | `drivers/remoteproc/remoteproc_core.c:581` |
| 挂进链表 + 计数 | `drivers/remoteproc/remoteproc_core.c:583`、`drivers/remoteproc/remoteproc_core.c:585` |
| 清理（在 `rproc_resource_cleanup()` 里） | `drivers/remoteproc/remoteproc_core.c:1219` 起 |

### §7.2 每次读都重新解析地址

`rproc_trace_read()` 的第一句就是
`rproc_da_to_va(data->rproc, trace->da, trace->len, NULL)`
（`drivers/remoteproc/remoteproc_debugfs.c:135`）。

| 结果 | 输出 |
|---|---|
| NULL | `Trace %s not available`（`drivers/remoteproc/remoteproc_debugfs.c:138`）—— 不是错误码，是把这句话当内容返回 |
| 非 NULL | `strnlen(va, trace->len)` 后 `simple_read_from_buffer()`（`drivers/remoteproc/remoteproc_debugfs.c:142`、`drivers/remoteproc/remoteproc_debugfs.c:145`） |

**"每次读都重新转换"**是有意的：远端可能在两次读之间被停掉又启动，
carveout 的映射会变。代价是每次 read 都要走一遍 carveout 链表。

⚠️ 两个真实的粗糙点：

1. **`is_iomem` 传的是 NULL**（`rproc_da_to_va()` 在
   `drivers/remoteproc/remoteproc_core.c:221-222` 允许 NULL）。
   之后 `simple_read_from_buffer()` 走的是普通 `memcpy` ——
   如果那块 trace 缓冲是 `ioremap` 来的，这里**没有用 `memcpy_fromio()`**。
   对比 §3.4 的 `rproc_copy_segment()` 就认真区分了。
2. `strnlen(va, trace->len)` 意味着 **trace 缓冲必须是 NUL 结尾的字符串**。
   固件写了一个环形缓冲、中间有 `\0`，读出来就被截断。

---

## §8 panic 通知链：给远端最后一次刷 cache 的机会

`rproc_panic_handler()`（`drivers/remoteproc/remoteproc_core.c:2694`），
在模块 init 时注册进 `panic_notifier_list`
（`drivers/remoteproc/remoteproc_core.c:2726-2729`），exit 时注销
（`drivers/remoteproc/remoteproc_core.c:2734`）。

| 步骤 | 内容 | 位置 |
|---|---|---|
| 1 | `rcu_read_lock()` + `list_for_each_entry_rcu(rproc, &rproc_list, node)` | `drivers/remoteproc/remoteproc_core.c:2701-2702` |
| 2 | 跳过没实现 `ops->panic` 的 | `drivers/remoteproc/remoteproc_core.c:2703-2704` |
| 3 | 跳过状态不是 `RUNNING` 也不是 `ATTACHED` 的 | `drivers/remoteproc/remoteproc_core.c:2706-2708` |
| 4 | `d = ops->panic(rproc)`，取所有返回值的**最大值** | `drivers/remoteproc/remoteproc_core.c:2710-2711` |
| 5 | `rcu_read_unlock()` | `drivers/remoteproc/remoteproc_core.c:2713` |
| 6 | `mdelay(longest)` —— **忙等**，不是 sleep | `drivers/remoteproc/remoteproc_core.c:2721` |
| 7 | `return NOTIFY_DONE` | `drivers/remoteproc/remoteproc_core.c:2723` |

三个设计点串在一起才说得通：

1. **必须用 RCU**：panic 上下文不能睡，拿不了 `rproc_list_mutex`（03 §B1.1）。
2. **必须用 `mdelay` 而不是 `msleep`**：panic 时调度器已经不可用。
3. **回调返回的是"请给我多少毫秒"**：源码注释说明用途是
   "give the remote processor time to perform any requested operations
   (such as flush caches), when it's not possible to signal the Linux side"。
   ⇒ 这是一个**单向的、无确认的**协议：内核只能干等，收不到远端的回复。

`ops->panic` 是完全可选的 —— 不实现就整个跳过，`longest` 保持 0，
`mdelay(0)` 等于不等。

---

## §9 汇总表

| § | 框架 | 职责 | 挂在 03 时间线的哪一点 | arm64 相关 | 最该记住的一条 |
|---|---|---|---|---|---|
| §1 | virtio / rpmsg 侧 | 把 `RSC_VDEV` 变成 virtio 设备 | `rproc_handle_resources()` 建平台设备；`rproc_start_subdevices()` 建 virtio 设备 | | 三级跳；virtio 的配置空间就是资源表里那条 `fw_rsc_vdev` |
| §2 | ELF 加载器 | 五个默认 ops 回调 | `rproc_alloc()` 里填入 | | 判据只有 `ops->load` 一个，全有或全无 |
| §3 | coredump | 崩溃现场导出 | `rproc_boot_recovery()` 里，停机之后 | | 三个默认值合起来 = 默认静默跳过；`elf_class` 必须驱动自己设 |
| §4 | sysfs | 五个属性文件 | `rproc_add()` 的 `device_add()` | | `sysfs_streq` 宽容；`coredump` 在 CRASHED 时 `-EBUSY` |
| §5 | debugfs | sysfs 超集 + `crash` + 两个只读视图 | `rproc_add()` 的 `rproc_create_debug_dir()` | | `crash` 是唯一故障注入口；`resource_table` 是验证回写的唯一窗口 |
| §6 | cdev | fd 生命周期绑远端生命周期 | `rproc_add()`，在 `device_add()` **之前** | | 必须 `printf` 不能 `echo`（不去换行）；`SHUTDOWN_ON_RELEASE` 是它的存在理由 |
| §7 | trace 缓冲 | 固件的裸文本日志 | `rproc_handle_resources()` 扫到 `RSC_TRACE` | | 每次读都重新做地址转换；缓冲必须 NUL 结尾 |
| §8 | panic 通知链 | 给远端刷 cache 的时间 | 与生命周期无关，模块 init 时注册 | | 单向无确认；`mdelay` 忙等 |

七块里**只有 §1 和 §2 参与"能不能启动起来"**，其余五块都是可观测性 / 可控制性。
调"启动不了"只看 §1 §2；调"启动了但行为不对"从 §5 的 `resource_table` 入手。

---

**下一篇**：05 篇给出各个角色（平台驱动作者 / rpmsg 客户端 / 用户态运维）的代码骨架。
