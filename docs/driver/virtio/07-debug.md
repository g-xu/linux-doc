---
title: 调试方法
---

# virtio 07：调试与可观测性

## §0 按症状找章节

| 症状 | 先看 | 再看 |
|---|---|---|
| 设备在但没绑驱动 | §3.2（`status` 只到 `0x1`） | 05 §7.7（设备编号写错） |
| probe 成功但数据不动 | §3.2（`status` 不是 `0xf`） | 05 §1.2（忘了 `virtio_device_ready()`） |
| feature 协商失败 | §2.2（三条日志） | 03 §A5 |
| 内核直接崩（`BUG`） | **§2.3 —— virtio 的 `BUG` 点异常多** | 05 §8 |
| 偶发数据错乱 / 怀疑并发 | **§4（`DEBUG` 模式）** | 02 §7.2 |
| 提交了但对端没反应 | §4.3（`LAST_ADD_TIME` 检测忘 kick） | 01 §3.2 |
| MMIO 设备起不来 | §2.4（魔数/版本日志） | 06 §9 |
| DT 子节点被忽略 | **06 §9.3** ——⚠️ 完全没有日志 | 06 §7.4 |
| 想看队列状态 | ⚠️§5：**几乎看不到** | — |

---

## §1 ⚠️ 先说结论：virtio 的可观测性有一个大洞

**本子系统的可观测性分布极不均匀**：

| 层 | 可观测性 | 手段 |
|---|---|---|
| **设备/协商层** | **好** | 5 个 sysfs 属性（§3）+ 一批明确的日志（§2） |
| **数据面（vring / virtqueue）** | ⚠️ **几乎为零** | **只有重新编译开 `DEBUG`**（§4） |
| **对端行为** | ⚠️ **完全为零** | **要去对端侧看**（99 §5.5） |

### §1.1 ⚠️ **一个 tracepoint 都没有**

```bash
grep -rn 'trace_' drivers/virtio/*.c | wc -l      # → 0
ls include/trace/events/ | grep -i virtio          # → 只有 vsock_virtio_transport_common.h
```

⇒ **`drivers/virtio/` 下零 tracepoint 调用，也没有 `include/trace/events/virtio*.h`。**
⚠️ **唯一带 virtio 字样的那个头文件（`vsock_virtio_transport_common.h`）
是 vsock 子系统的，不是 virtio 核心的。**

**和 timer 那套对比**（timer 有 17 个 tracepoint、0 个 `TP_CONDITION`）：
**virtio 的数据面完全没有 trace 出口** ——
⇒ **这是本子系统最大的可观测性缺口**，03 §C4 把它列为主要欠债。

**替代手段只有三条**（都不理想）：
① **重新编译开 `DEBUG`**（§4）；
② **`/proc/interrupts` 看中断计数**（§6）；
③ **在对端侧（QEMU/vhost）看** ——**不在本树里**。

---

## §2 日志

### §2.1 可见性等级

**`drivers/virtio/` 的日志只有三档**（比 timer 少两档——**没有 `pr_debug`**）：

| 档 | 变体 | 默认可见 | 说明 |
|---|---|---|---|
| 1 | `dev_err` / `dev_warn` | **可见** | 真正的故障 |
| 2 | `dev_warn_once` | **只出现一次** | ⚠️ 重启后才会再出现 ⇒ **抓到就留住 dmesg** |
| 3 | `BAD_RING()` 宏 | **可见**，但**行为随 `DEBUG` 变** | **`DEBUG` 下 `BUG()`，否则只标 broken**（§4.2） |

⚠️ **没有 `pr_debug`** ⇒ **不存在"打开动态调试就能看到更多"这条路**。
⇒ 这和 timer（113 处 `pr_debug`）形成鲜明反差。

### §2.2 核心的三条日志（`drivers/virtio/virtio.c`）

| 日志（逐字） | 位置 | 含义 |
|---|---|---|
| `device must provide VIRTIO_F_VERSION_1` | `drivers/virtio/virtio.c:180` | **受限内存访问场景**下设备太老（03 §A5）；⚠️ **arm64 上一般不触发**（04 §9.2） |
| `device must provide VIRTIO_F_ACCESS_PLATFORM` | `drivers/virtio/virtio.c:186` | 同上 |
| `virtio: device refuses features: %x` | `drivers/virtio/virtio.c:197` | **设备拒绝了协商结果** ——写-回读握手失败（03 §A5） |

**第三条是本子系统最有诊断价值的一条日志**：
它说明**驱动和设备对 feature 组合谈不拢**，`%x` 打的是回读到的状态字节。
⇒ **看到它就去对比 `/sys/.../features`（§3.3）和驱动的 `feature_table`。**

⚠️ **前两条的措辞容易误导**：它们说 "device must provide"，
听起来像设备的问题，**但触发条件是"平台要求受限内存访问"**
（`drivers/virtio/virtio.c:177` 的 `if (virtio_check_mem_acc_cb(dev))`）
⇒ **在 arm64 上看到它反而应该怀疑配置**，因为 arm64 默认不走那条路（04 §9.2）。

### §2.3 ⚠️`BUG` / `WARN` 点：virtio 异常多

**`drivers/virtio/virtio.c` 8 处，`drivers/virtio/virtio_ring.c` 22 处。**

**核心那 8 处逐个列**：

| 位置 | 代码 | 你干了什么 |
|---|---|---|
| ⚠️`drivers/virtio/virtio.c:122` | `BUG()` | **查了一个没在 feature 表里声明的位**（§2.3.1） |
| ⚠️`drivers/virtio/virtio.c:256` | `BUG_ON(f >= 64)` | **`feature_table` 里写了 ≥64 的位号**（§2.3.2） |
| ⚠️`drivers/virtio/virtio.c:265` | `BUG_ON(f >= 64)` | 同上，**legacy 表** |
| `drivers/virtio/virtio.c:335` | `WARN_ON_ONCE(dev->config->get_status(dev))` | **`remove()` 里忘了 `virtio_reset_device()`**（03 §A9、05 §7.5） |
| ⚠️`drivers/virtio/virtio.c:355` | `BUG_ON(driver->feature_table_size && !driver->feature_table)` | **填了 size 却没填表指针** ——**在 `register_virtio_driver()` 里，注释是 `Catch this early.`** |
| `drivers/virtio/virtio.c:381` | `WARN_ON(count > 1)` | **DT 里写了超过一个子节点**（06 §7.4） |
| `drivers/virtio/virtio.c:385` | `WARN_ON(!np)` | 子节点取不到（内部一致性） |
| `drivers/virtio/virtio.c:389` | `BUG_ON(ret >= sizeof(compat))` | `compatible` 字符串溢出（实际不可能） |

#### §2.3.1 ⚠️`virtio_check_driver_offered_feature()` 的 `BUG()`

**完整逻辑**（`drivers/virtio/virtio.c:105-123`）：

```
  遍历 drv->feature_table，找到 fbit ⇒ return                :111-113
  如果有 legacy 表，再遍历 drv->feature_table_legacy ⇒ return  :115-119
  两张表都没有 ⇒ BUG()                                    :122
```

⚠️ **注意它查两张表**（`feature_table` **和** `feature_table_legacy`）——
**只在 legacy 表里列过也算声明过**。

**触发路径**：`virtio_has_feature()`（`include/linux/virtio_config.h:185`）
在 `CONFIG_DEBUG` 类配置下会调它。
⇒ **驱动作者最容易踩的一个"崩"**：加了新 feature 忘了改 `feature_table`（05 §8）。

#### §2.3.2 ⚠️ 两处 `BUG_ON(f >= 64)`：64 位上限的执行者

**`drivers/virtio/virtio.c:256` 和 `:265`** 分别在遍历
`feature_table` 和 `feature_table_legacy` 时检查。

⇒ **这就是 02 §1.0.E 讲的"feature 位号上限 63"的代码执行点**：
`vdev->features` 是 `u64`（`include/linux/virtio.h:126`），
`driver_features |= (1ULL << f)` 要求 `f < 64`。

⚠️ **它是 `BUG_ON` 而不是拒绝注册** ⇒ **写错位号直接崩机器**。

### §2.4 MMIO 传输层的日志（`drivers/virtio/virtio_mmio.c`）

**arm64 上 MMIO 是主路之一**，它的日志最该认得：

| 日志（逐字） | 位置 | 含义 |
|---|---|---|
| `Wrong magic value 0x%08lx!` | `drivers/virtio/virtio_mmio.c:625` | **读 `VIRTIO_MMIO_MAGIC_VALUE`(0x000) 不对** ⇒ **`reg` 基地址写错**（06 §6.2 那条 Kconfig 警告说的就是这个） |
| `Version %ld not supported!` | `drivers/virtio/virtio_mmio.c:633` | 读 `VIRTIO_MMIO_VERSION`(0x004) 不是 1 或 2 |
| `New virtio-mmio devices (version 2) must provide VIRTIO_F_VERSION_1 feature!` | `drivers/virtio/virtio_mmio.c:133` | **VERSION=2 却没提供 `VERSION_1`** ——设备/VMM 的 bug |
| `Failed to enable 64-bit or 32-bit DMA. Trying to continue, but this might not work.` | `drivers/virtio/virtio_mmio.c:667` | **`dma_set_mask_and_coherent()` 失败** ⇒ 后面可能出怪问题 |
| `Failed to register parent device!` | `drivers/virtio/virtio_mmio.c:741` | **命令行路径**的父设备注册失败（06 §6.1） |
| （`:419` 处的 `dev_err`） | `drivers/virtio/virtio_mmio.c:419` | 队列建立相关 |

**前两条是排查"MMIO 设备完全起不来"的第一站**：

```bash
dmesg | grep -iE 'virtio-mmio|Wrong magic|not supported'
```

⚠️ **`Wrong magic value` 的两个常见原因**：
① **DT/命令行里的 `reg` 基地址写错**；
② **那个地址上根本没有 virtio 设备**（VMM 没提供）。

### §2.5 `virtio_ring.c` 的两条

| 日志 | 位置 | 含义 |
|---|---|---|
| `Bad virtqueue length %u` | `drivers/virtio/virtio_ring.c:1064` | **设备报的队列长度不合法**（不是 2 的幂或为 0） |
| `virtio vring IRQ raised before DRIVER_OK` | `drivers/virtio/virtio_ring.c:2466` | **对端在 `DRIVER_OK` 之前就发中断** ⇒ 见 §4.4 |

---

## §3 sysfs：5 个属性，**全部只读**

**落点**：`/sys/bus/virtio/devices/virtioN/`

### §3.1 属性清单与格式

| 属性 | 模式 | 声明位置 | 格式串 | 例 |
|---|---|---|---|---|
| `device` | **RO** | `drivers/virtio/virtio.c:20` | `0x%04x`（`:18`） | `0x0001`（net） |
| `vendor` | **RO** | `drivers/virtio/virtio.c:28` | `0x%04x`（`:26`） | `0x1af4` |
| `status` | **RO** | `drivers/virtio/virtio.c:36` | `0x%08x`（`:34`） | `0x0000000f` |
| `modalias` | **RO** | `drivers/virtio/virtio.c:45` | `virtio:d%08Xv%08X`（`:42`） | `virtio:d00000001v00001AF4` |
| `features` | **RO** | `drivers/virtio/virtio.c:62` | **逐位打 `%c`**（`:57-59`） | `1100...` |

**数组在 `drivers/virtio/virtio.c:64-71`**（`virtio_dev_attrs[]`），
通过 `ATTRIBUTE_GROUPS(virtio_dev)` 挂到 `virtio_bus.dev_groups`（`:346` 一带）。

⚠️ **五个全是 RO —— 一个可写的都没有。**
⇒ **和 timer 对比**（timer 有 3 个可写：`current_clocksource`、
两个 `unbind_*`）：**virtio 完全不能从 sysfs 干预运行时行为**。
**要 unbind 只能用驱动核心的通用 `bind`/`unbind`**（`/sys/bus/virtio/drivers/<drv>/unbind`）。

### §3.2 `status` 是最有诊断价值的一个

**它直接读设备的状态字节**（`dev->config->get_status(dev)`，`:34`）
⇒ **能精确定位启动序列卡在哪一步**（03 §A7 的六个位）：

| 值（低 8 位） | 累积到 | 卡在哪 |
|---|---|---|
| `0x01` | ACKNOWLEDGE | **没匹配到驱动** ⇒ 查 §3.4 |
| `0x03` | + DRIVER | **feature 协商失败** ⇒ 查 §2.2 的三条日志 |
| `0x0b` | + FEATURES_OK | **`drv->probe()` 失败** |
| `0x0f` | + DRIVER_OK | **正常** |
| ⚠️ 带 `0x40` | **NEEDS_RESET** | **设备自己报告坏了** ——唯一由对端设置的位 |
| ⚠️ 带 `0x80` | **FAILED** | 任何失败路径（`drivers/virtio/virtio.c:320` 或 `:468`） |

```bash
# 一眼看全所有 virtio 设备的状态
for d in /sys/bus/virtio/devices/virtio*; do
	printf '%-10s device=%s status=%s driver=%s\n' "$(basename $d)" \
		"$(cat $d/device)" "$(cat $d/status)" \
		"$(basename $(readlink -f $d/driver 2>/dev/null) 2>/dev/null || echo NONE)"
done
```

⚠️ **`driver=NONE` + `status=0x00000001` 是最常见的一种失败**：
设备存在但没驱动 ⇒ **要么内核没编那个驱动，要么 `id_table` 的编号写错**（05 §7.7）。

### §3.3 `features`：逐位的 0/1 字符串

**生成代码**（`drivers/virtio/virtio.c:52-60` 一带）：
遍历 64 位，每位打一个 `'0'` 或 `'1'`（`:57-59` 的 `sprintf(buf+len, "%c", ...)`）。

⇒ **它是"协商后的最终结果"**（`vdev->features`），
**对比驱动的 `feature_table` 就能看出哪些位没谈上**。

```bash
cat /sys/bus/virtio/devices/virtio0/features
# 第 N 个字符对应 feature 位 N
```

⚠️ **它不告诉你"为什么没谈上"** ——可能是设备不支持，
也可能是驱动没在 `feature_table` 里列（03 §A4）。**两者从这里分不开。**

### §3.4 匹配相关的三个检查

```bash
# ① 设备类型编号（十进制看 modalias，十六进制看 device）
cat /sys/bus/virtio/devices/virtio0/device       # 0x%04x 格式
cat /sys/bus/virtio/devices/virtio0/modalias     # virtio:dXXXXXXXXvXXXXXXXX

# ② 有没有驱动能认它
ls /sys/bus/virtio/drivers/

# ③ DT 子节点关联成功了吗（06 §9.3 的那个静默坑）
ls -l /sys/bus/virtio/devices/virtio0/of_node
printf 'virtio,device%x\n' $((16#$(cat /sys/bus/virtio/devices/virtio0/device | sed 's/0x//')))
```

**第 ③ 条最后那行的作用**：直接算出 DT 子节点的 `compatible` 该写什么
（06 §7.2 的十六进制换算）。

---

## §4 `DEBUG` 模式：数据面唯一的观测手段

⚠️ **它不是 Kconfig 选项，是一个 C 宏** ——
**必须改 Makefile 或加 `-DDEBUG` 重新编译 `virtio_ring.c`**。

**`#ifdef DEBUG` 有两处**：
`drivers/virtio/virtio_ring.c:18`（宏定义）和 `:205`（结构体字段）。

### §4.1 打开的方法

```bash
# 在 drivers/virtio/Makefile 里加：
#   CFLAGS_virtio_ring.o := -DDEBUG
# 然后重新编译 virtio_ring.o
```

⇒ **代价是必须换内核** ——这就是为什么 03 §C4 说
"最关键的数据面几乎不可观测"。

### §4.2 `BAD_RING`：同一个名字，两种行为

| 配置 | 位置 | 行为 |
|---|---|---|
| **`DEBUG`** | `drivers/virtio/virtio_ring.c:20-25` | `dev_err` **然后 `BUG()`** ——注释是 `For development, we want to crash whenever the ring is screwed.` |
| **默认** | `drivers/virtio/virtio_ring.c:57-62` | `dev_err` **然后 `(_vq)->broken = true`** |

⇒ **这解释了 03 §B5 的 broken 状态是怎么来的**：
**默认配置下"环被搞坏了"不会崩，只会把队列标死。**
**开 `DEBUG` 就变成立刻崩** ⇒ 开发期能拿到第一现场的栈。

### §4.3 三个只在 `DEBUG` 下存在的检测

| 宏 | 位置 | 抓什么 | 怎么报 |
|---|---|---|---|
| `START_USE(_vq)` | `drivers/virtio/virtio_ring.c:27-33` | **同一个 vq 的并发重入** | **`panic("%s:in_use = %i\n", ...)`** ——⚠️ **直接 panic，不是 WARN** |
| `END_USE(_vq)` | `drivers/virtio/virtio_ring.c:34-35` | 配对检查 | `BUG_ON(!(_vq)->in_use)` |
| `LAST_ADD_TIME_UPDATE/_CHECK` | `drivers/virtio/virtio_ring.c:36-53` | **提交了但 100ms 内没 kick / 没 get** | `WARN_ON(...> 100)` |

**`START_USE` 是本子系统唯一的并发违规检测**（02 §7.2、03 §B7）：
**`in_use` 记的是 `__LINE__`**（`drivers/virtio/virtio_ring.c:32`）
⇒ **panic 消息里的数字直接告诉你"上一次进入是在哪一行"**。

⚠️ **`in_use` 的注释是 `Caller is supposed to guarantee no reentry.`**
（`drivers/virtio/virtio_ring.c:26`）⇒ **框架把锁的责任明确推给了调用者**。

**`LAST_ADD_TIME` 的注释逐字**（`drivers/virtio/virtio_ring.c:39`）：
`No kick or get, with .1 second between?  Warn.`
⇒ **专门抓"`virtqueue_add_*()` 之后忘了 `virtqueue_kick()`"** ——
**这是 virtio 驱动的一类经典 bug**，而且**默认配置下完全没有提示**
（表现是"数据发不出去但不报错"，05 §8）。

### §4.4 `CONFIG_VIRTIO_HARDEN_NOTIFICATION`：一个真正的 Kconfig

**这个是 Kconfig 选项**（`drivers/virtio/Kconfig:36`），和 `DEBUG` 不同。

**7 个使用点**：

```bash
grep -rn 'CONFIG_VIRTIO_HARDEN_NOTIFICATION' drivers/virtio/*.c include/linux/virtio_config.h
```

| 位置 | 作用 |
|---|---|
| `drivers/virtio/virtio.c:222` | reset 路径 |
| `include/linux/virtio_config.h:274` | `virtio_device_ready()` 里 |
| `drivers/virtio/virtio_pci_modern.c:226` · `:276` | PCI 侧 |
| `drivers/virtio/virtio_ring.c:2027` · `:2465` · `:2515` | **vring 侧** |

**它做什么**：把"对端在 `DRIVER_OK` 之前就发通知"从**默默忽略**变成
**打一条 `dev_warn_once` 并返回 `IRQ_NONE`**（`drivers/virtio/virtio_ring.c:2465-2468`）：

```c
	if (unlikely(vq->broken)) {
#ifdef CONFIG_VIRTIO_HARDEN_NOTIFICATION
		dev_warn_once(&vq->vq.vdev->dev,
			      "virtio vring IRQ raised before DRIVER_OK");
		return IRQ_NONE;
#else
		return IRQ_HANDLED;
#endif
	}
```

⇒ **不开这个选项时那种情况返回 `IRQ_HANDLED`，静默吃掉。**
**开了才能发现"对端行为不守规矩"** ——
⚠️ **而且是 `_once`**，只报一次（§2.1 第 2 档）。

**对不信任 hypervisor 的场景（机密计算）应该开它。**

---

## §5 ⚠️ 队列级别的观测：几乎没有

**`struct virtqueue` 不在驱动模型里**（02 §1.0.B）
⇒ **sysfs 里看不到任何 virtqueue**。

**能间接看到的只有两样**：

| 手段 | 能看到什么 | 局限 |
|---|---|---|
| `/proc/interrupts` | **队列的 `name`**（01 §3.6，`virtio_find_vqs()` 传进去的那些） | **只有中断计数，没有队列深度/占用** |
| `virtqueue_get_vring_size(vq)` | 队列容量 | **只能在驱动代码里调，没有导出到用户态** |

⚠️ **想知道的这些全看不到**：
`num_free`（还能提交多少）· `last_used_idx`（读到哪了）·
`broken`（队列死没死）· 实际的 `avail->idx` / `used->idx`。

⇒ **这些字段都在 `struct vring_virtqueue`（02 §6.3）里，
但没有任何 procfs/debugfs/sysfs 出口。**
**只能靠 crash/kgdb 挖内存，或者开 `DEBUG` 重编译。**

---

## §6 `/proc/interrupts`：唯一的运行时数据面信号

```bash
# 队列名会出现在中断名里
grep -i virtio /proc/interrupts
```

**名字来自 `virtio_find_vqs()` 的 `names[]` 参数**（01 §6.2、05 §1）
⇒ **这就是为什么 05 §1 强调"`names` 会出现在中断名里"** ——
**给队列起个有意义的名字是唯一能让它在运行时被看见的办法。**

| 传输 | 中断形态 |
|---|---|
| PCI + MSI-X | **每个队列一个向量** ⇒ 能分别看到每队列的计数 |
| ⚠️ MMIO | **只有一个中断**（06 §2.2） ⇒ **所有队列的计数混在一起** |

⇒ **arm64 上如果走 MMIO，连"哪个队列忙"都看不出来。**

---

## §7 错误码分诊

### §7.1 全量普查（01 §9.1 的原始数据）

统计范围：`drivers/virtio/` 下 **14 个 `.c`**（命令见 99 §3.3）。

| 错误码 | 次数 | 含义 |
|---|---|---|
| `-ENOMEM` | **46** | **排第一** ——建队列/分配 vring 失败 |
| `-EINVAL` | **27** | 参数不合法 |
| `-EBUSY` | **21** | 设备/队列正在被使用 |
| `-ENODEV` | **14** | 设备不存在或已移除；**也是 feature 协商失败的返回值**（`drivers/virtio/virtio.c:181` · `:187` · `:199`） |
| `-ENOSPC` | **13** | ⚠️ **队列满 —— 正常背压，不是错误** |
| `-ENOENT` | **11** | **feature 位不存在**（`virtio_cread_feature()`） |
| `-EAGAIN` · `-EIO` · `-E2BIG` | 5 · 4 · 4 | — |
| `-EOPNOTSUPP` · `-ENOSYS` · `-EFAULT` · `-EEXIST` · `-ERANGE` | **全部 0** | 本子系统不返回 |

### §7.2 拿到某个码先查什么

| 拿到 | 先查 |
|---|---|
| `-ENODEV`（probe 阶段） | **§2.2 的三条日志** ——协商失败的三种原因 |
| `-ENOMEM`（建队列） | 队列数 × 队列长度是否过大；`virtqueue_get_vring_size()` |
| `-ENOSPC` | **别当错误** ——停止提交，等 `get_buf()`（05 §1） |
| `-ENOENT`（`virtio_cread_feature`） | **正常** ——那个 feature 没协商上，用默认值 |
| `-EBUSY` | 设备是不是已经被别的驱动绑了 |
| **内核直接崩** | **§2.3 那 8 个 `BUG` 点** ——virtio 用 `BUG` 比大多数子系统激进 |

⚠️ **本子系统一大类 API 不返回错误码**（03 §C2）：
`virtqueue_disable_cb()` · `virtio_add_status()` · `virtio_break_device()` ·
`virtio_device_ready()` · `virtio_reset_device()` 全是 `void`。
⇒ **这些路径上的问题只能靠 §3 的 sysfs 和 §2.3 的 `WARN` 发现。**

---

## §8 故障注入：本子系统**没有**

```bash
grep -rn 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR\|CONFIG_FAIL' drivers/virtio/*.c
# → 无输出
```

**`drivers/virtio/` 下没有任何故障注入框架的接入点。**

**替代手段有三个**（都在对端侧或需要重编译）：

| 手段 | 怎么做 | 能模拟什么 |
|---|---|---|
| **开 `DEBUG`** | §4.1 | **把"环被搞坏"从静默变成立刻崩**（§4.2） |
| **开 `CONFIG_VIRTIO_HARDEN_NOTIFICATION`** | Kconfig | **让"对端不守规矩"可见**（§4.4） |
| **在对端侧注入** | ⚠️ **不在本树**（QEMU 的 fault injection、恶意 vhost） | **恶意/错误的对端行为** |

⇒ **这是跨边界子系统的一个固有困难**：
**最该注入故障的地方（对端）不在你的代码库里。**
⇒ 99 §5.5 把这一点登记为第五层未验证项。

---

## §9 一页速查

```bash
# ═══ 状态确认（先跑这三条）═══
for d in /sys/bus/virtio/devices/virtio*; do
	printf '%-10s device=%s status=%s driver=%s\n' "$(basename $d)" \
		"$(cat $d/device)" "$(cat $d/status)" \
		"$(basename $(readlink -f $d/driver 2>/dev/null) 2>/dev/null || echo NONE)"
done
dmesg | grep -iE 'virtio|Wrong magic|refuses features'
grep -i virtio /proc/interrupts

# ═══ DT 子节点有没有关联上（06 §9.3 的静默坑）═══
ls -l /sys/bus/virtio/devices/virtio0/of_node

# ═══ 协商结果 ═══
cat /sys/bus/virtio/devices/virtio0/features
```

| 拿到 | 第一嫌疑 | 去 |
|---|---|---|
| `status=0x00000001` + `driver=NONE` | **没匹配到驱动**（设备编号写错 / 驱动没编） | §3.2 · 05 §7.7 |
| `status=0x00000003` | **feature 协商失败** | §2.2 |
| `status=0x0000000b` | **`drv->probe()` 失败** | §3.2 |
| `status` 带 `0x40` | **设备自己报告坏了（NEEDS_RESET）** | 03 §A7 |
| `status` 带 `0x80` | **FAILED** | §3.2 |
| `virtio: device refuses features` | **写-回读握手失败** | §2.2 · 03 §A5 |
| `Wrong magic value` | **`reg` 基地址错**（MMIO） | §2.4 · 06 §6.2 |
| `remove` 后 `WARN_ON_ONCE` | **忘了 `virtio_reset_device()`** | §2.3 · 03 §A9 |
| **内核崩在 `virtio.c:122`** | **查了没声明的 feature 位** | §2.3.1 |
| **内核崩在 `virtio.c:256`/`:265`** | **feature 位号 ≥ 64** | §2.3.2 |
| **数据发不出去但不报错** | **忘了 `virtqueue_kick()`** ⇒ 只能开 `DEBUG` 抓 | §4.3 |
| **偶发数据错乱（多 CPU）** | **同一个 vq 没加锁** ⇒ 开 `DEBUG` 看 `START_USE` panic | §4.3 · 02 §7.2 |
| **`of_node` 符号链接不存在** | **`virtio,deviceNN` 写成十进制了** | 06 §9.3 |

⚠️ **本篇所有命令的输出都没有实测**（99 §5.1）——
路径和属性名是从 `DEVICE_ATTR` / `proc_create` / `sprintf` 格式串反推的，
**唯一逐字确认的是那 5 个 sysfs 属性的格式串**
（`drivers/virtio/virtio.c:18` · `:26` · `:34` · `:42` · `:57-59`）
**和 §2 那批日志的原文。**
