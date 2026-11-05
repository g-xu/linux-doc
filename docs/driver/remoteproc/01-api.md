---
title: API 参考
---

# remoteproc 代码地图与对外 API

**这一篇回答两个问题**：这个子系统的代码在哪（§0）、我能调哪些函数（§1 起）。

- 版本：Linux **6.1.158**，本地树对 `drivers/remoteproc/` 与
  `include/linux/remoteproc.h` **零改动**（判定命令见 99 §1.1）。
- 架构范围：**arm64**。x86 无 remoteproc 驱动，ACPI 路线不涉及。
- 表格统一三列 **API / 说明 / 备注**；签名只写**函数名 + 形参名**，不带类型；
  位置只到**文件**，不到行号（行号集中在 03 和 99 篇维护）。

---

## §0 代码地图

### §0.1 核心文件与职责

`drivers/remoteproc/` 下 **9 个**文件构成核心（其余 34 个 `.c` 里的绝大多数是驱动）：

| 文件 | 职责 | 备注 |
|---|---|---|
| `remoteproc_core.c` | 主体：分配/注册/启停/资源表处理/carveout/恢复 | 全子系统最大的一个文件 |
| `remoteproc_virtio.c` | 把资源表里的 `RSC_VDEV` 变成真正的 virtio 设备 | **这是 remoteproc 和 virtio/rpmsg 的唯一缝合点** |
| `remoteproc_elf_loader.c` | ELF 固件的合法性检查、段加载、资源表定位 | 一组可以直接填进 `rproc_ops` 的默认实现 |
| `remoteproc_coredump.c` | 远端崩溃后把内存段导出成 ELF core | 走 devcoredump 框架 |
| `remoteproc_debugfs.c` | debugfs 界面（trace 缓冲、资源表、崩溃计数等） | 07 篇主战场之一 |
| `remoteproc_sysfs.c` | sysfs 界面 + **`struct class rproc_class`** | 5 个属性，见 §0.5 |
| `remoteproc_cdev.c` | `/dev/remoteprocN` 字符设备 | 需要 `REMOTEPROC_CDEV`，默认不开 |
| `remoteproc_internal.h` | 核心内部原型 + 一堆 `static inline` 回调包装 | 名字叫 internal，实际上 `drivers/remoteproc/` 下 **28 个 `.c` 都 include 它**，见 §11 |
| `remoteproc_elf_helpers.h` | ELF32/ELF64 字段的统一访问宏 | 纯宏，驱动一般不用 |

**`remoteproc_internal.h` 里的 `static inline` 包装是理解本子系统的钥匙**：
核心从不直接调 `rproc->ops->xxx`，而是调这些包装，由包装决定"驱动没实现时用哪个默认值"。
详见 03 篇。

### §0.2 头文件

| 头文件 | 内容 | 谁用 |
|---|---|---|
| `include/linux/remoteproc.h` | **唯一装在 `include/` 里的头文件**：`struct rproc`、`rproc_ops`、资源表结构、生命周期/启停/carveout/coredump/subdev API | 任何目录下的驱动和消费者 |
| `drivers/remoteproc/remoteproc_internal.h` | 核心内部原型 + ELF 默认实现 + 回调包装 | 核心 **和同目录驱动**（28 个 `.c`），目录外**零 include** |

⚠️ **所以"对外 API"其实分两层，这是本子系统一个容易误判的地方**：

| 层 | 声明在 | 谁能用 |
|---|---|---|
| 真正的公开层 | `include/linux/remoteproc.h` | 全树任何驱动 |
| 半公开层（`rproc_elf_*` / `rproc_va_to_pa` / `rproc_find_carveout_by_name`） | `remoteproc_internal.h` | **只有住在 `drivers/remoteproc/` 里的驱动** |

半公开层这几个函数**都带 `EXPORT_SYMBOL`**，但原型不在 `include/` 里
⇒ 出树驱动即使 `extern` 手写原型也能链上，却是在用一个没有承诺的接口。
详见 §11。

顺带一个统计：核心七个 `.c` 里 **`EXPORT_SYMBOL_GPL` 的个数是 0** ——
全部 33 个导出都是不带 `_GPL` 的 `EXPORT_SYMBOL`。

⚠️ `include/linux/remoteproc.h` 里同时住着两类完全不同的东西：

| 类别 | 例子 | 特点 |
|---|---|---|
| **内核侧结构** | `struct rproc`、`struct rproc_ops`、`struct rproc_mem_entry` | 想怎么改就怎么改 |
| **ABI 结构** | `struct resource_table`、`struct fw_rsc_hdr`、`struct fw_rsc_carveout` / `_devmem` / `_trace` / `_vdev` | **和固件二进制约定**，改了就对不上 |

⇒ 这条区分是本子系统最容易踩的坑之一，02 篇 §1.0 会专门摊开。

### §0.3 Kconfig 开关

`drivers/remoteproc/Kconfig` 共 **28 个** `config` 条目。本篇整理其中与 arm64 相关的：

| 开关 | 类型 | 作用 |
|---|---|---|
| `REMOTEPROC` | **bool** | 总开关。**不是 tristate ⇒ 核心不能编成模块** |
| `REMOTEPROC_CDEV` | bool | 开出 `/dev/remoteprocN`。**默认不开**，07 篇会讲少了它的后果 |
| `IMX_REMOTEPROC` / `IMX_DSP_REMOTEPROC` | tristate | i.MX 主核 / DSP |
| `MTK_SCP` | tristate | 联发科 SCP |
| `QCOM_Q6V5_PAS` | tristate | 高通最常见的一条路线（固件由 TZ 认证加载） |
| `QCOM_Q6V5_MSS` | tristate | 自认证 modem，功能最全、最复杂 |
| `QCOM_Q6V5_ADSP` / `QCOM_Q6V5_WCSS` | tristate | ADSP / WLAN 子系统 |
| `QCOM_Q6V5_COMMON` / `QCOM_RPROC_COMMON` / `QCOM_PIL_INFO` | 无提示符 | **被上面几个 `select` 进来的内部库**，用户选不了 |
| `QCOM_SYSMON` | tristate | 子系统间崩溃通告（走 rpmsg） |
| `QCOM_WCNSS_PIL` | tristate | 老式 WCNSS |
| `STM32_RPROC` | tristate | STM32MP1 的 Cortex-M4 |
| `TI_K3_R5_REMOTEPROC` / `TI_K3_DSP_REMOTEPROC` | tristate | K3 平台，R5F 有"lockstep / split"模式 |
| `RCAR_REMOTEPROC` | tristate | 瑞萨 R-Car Gen3 |
| `PRU_REMOTEPROC` | tristate | TI PRU，形态很特殊（不走 virtio） |
| `OMAP_REMOTEPROC` + `OMAP_REMOTEPROC_WATCHDOG` | tristate + bool | 唯一带独立看门狗子开关的驱动 |

剩余条目（`DA8XX` / `KEYSTONE` / `WKUP_M3` / `MESON_MX_AO_ARC` / `ST_REMOTEPROC` /
`ST_SLIM_REMOTEPROC` / `INGENIC_VPU_RPROC`）是 arm32 或 MIPS 平台，按 99 §2.7 裁掉。

### §0.4 驱动目录概况

| 指标 | 数字 |
|---|---|
| `drivers/remoteproc/*.c` 个数 | **34** |
| 全部 `.c` 行数合计 | **25252** |
| 其中核心 8 个 `.c` + 2 个 `.h` | 5878（含对外头文件 708） |

⇒ 驱动侧约 2 万行。**本文档集只详读 4 个代表驱动**，选法登记在 99 §2.2。

高通那一组值得单独说明：`qcom_q6v5*.c` 六个文件 + `qcom_common.c` +
`qcom_pil_info.c` + `qcom_sysmon.c` 是**一个共享库 + 四个具体驱动**的结构，
不是四个独立驱动。这在 04 篇讲。

### §0.5 用户界面一览（详见 07 篇）

| 界面 | 位置 | 数量 |
|---|---|---|
| sysfs | `/sys/class/remoteproc/remoteprocN/` | **5 个属性**：`state`（RW）· `firmware`（RW）· `recovery`（RW）· `coredump`（RW）· `name`（RO） |
| debugfs | `/sys/kernel/debug/remoteproc/remoteprocN/` | trace 文件（数量随资源表）+ 若干固定文件 |
| 字符设备 | `/dev/remoteprocN` | 需要 `REMOTEPROC_CDEV` |

**注意 sysfs 路径是 `/sys/class/remoteproc/`，不是 `/sys/bus/...`** ——
因为 remoteproc **没有自己的 `bus_type`，只有一个 `class`**
（`struct rproc_class` 在 `remoteproc_sysfs.c`）。这个判定的依据和后果见 02 篇。

---

## §1 生命周期 API

**头文件**：`include/linux/remoteproc.h`；**实现**：`drivers/remoteproc/remoteproc_core.c`。

**本子系统的生命周期是"两段式"的**：先 `rproc_alloc()` 拿到对象、
填字段、加 carveout，**再** `rproc_add()` 让它对外可见。
中间这段窗口是驱动唯一能改 `struct rproc` 的时机。

| API | 说明 | 备注 |
|---|---|---|
| `rproc_alloc(dev, name, ops, firmware, len)` | 分配并初始化一个 `struct rproc`，此时还**不对外可见** | 失败返回 **NULL**（不是 `ERR_PTR`）。`len` 是**驱动私有区大小**，用 `rproc->priv` 取。`firmware` 传 NULL 时核心按 `rproc-<name>-fw` 自动拼一个名字 |
| `rproc_add(rproc)` | 注册到 `rproc_class`、建 sysfs/debugfs/cdev，并**可能立即触发加载** | 见下面的 ⚠️ |
| `rproc_del(rproc)` | 反注册；若还在运行会先停 | 与 `rproc_add()` 配对 |
| `rproc_free(rproc)` | 释放 `rproc_alloc()` 的对象 | 只能在**没 `rproc_add()`** 或**已 `rproc_del()`** 之后调 |
| `rproc_put(rproc)` | 放掉一个由 `rproc_get_by_phandle()` / `_by_child()` 拿到的引用 | **不是 `rproc_free()` 的别名**，见 §12 |
| `rproc_resource_cleanup(rproc)` | 释放资源表带来的全部资源（carveout / devmem / trace / vdev） | 驱动一般不用，核心在停机路径上自己调；`->stop` 失败时的清理场景例外 |
| `devm_rproc_alloc(dev, name, ops, firmware, len)` | `rproc_alloc()` 的 devm 版 | 失败仍返回 NULL |
| `devm_rproc_add(dev, rproc)` | `rproc_add()` 的 devm 版 | 与 `devm_rproc_alloc()` 配合可以让驱动的 `remove` 变成空的 |

⚠️ **`rproc_add()` 不只是"注册"** —— 它可能在返回之前就把固件加载起来。
两种情形：

| 情形 | 结果 |
|---|---|
| `rproc->auto_boot` 为真（`rproc_alloc()` 默认值） | 核心异步请求固件并 boot |
| 远端**已经在跑**（驱动把状态设成 `RPROC_DETACHED`） | 核心会走 attach 路径把它接管过来 |

⇒ **所以 `rproc_add()` 必须放在驱动初始化的最后一步**：在它返回之后，
`->start` / `->kick` 随时可能被调用。这是 05 篇 §1 骨架里顺序不能动的原因。

---

## §2 启停与固件 API

这一组是**消费者**（不是驱动）调的：谁需要远端跑起来，谁就调 `rproc_boot()`。

| API | 说明 | 备注 |
|---|---|---|
| `rproc_boot(rproc)` | 加载固件并启动远端；已在运行则只加引用计数 | 引用计数式：N 次 `boot` 要配 N 次 `shutdown`。**会睡**（要 `request_firmware`） |
| `rproc_shutdown(rproc)` | 引用计数减一，减到 0 才真正停机 | 返回 int；**6.1 之前是 void**，移植老代码要注意 |
| `rproc_detach(rproc)` | **不停远端**，只让内核撒手（`RPROC_RUNNING` → `RPROC_DETACHED`） | 与 `rproc_shutdown()` 语义完全不同，见 §12 |
| `rproc_set_firmware(rproc, fw_name)` | 换固件文件名 | 只有远端处于 `RPROC_OFFLINE` 时才允许，否则 `-EBUSY`；这也是 sysfs 的 `firmware` 属性背后的函数 |
| `rproc_of_parse_firmware(dev, index, fw_name)` | 从 DT 的 `firmware-name` 属性里读第 `index` 个名字 | 给驱动在 probe 里用，配合 `rproc_alloc()` 的 `firmware` 参数 |
| `rproc_report_crash(rproc, type)` | 告诉核心"远端崩了"，触发恢复流程 | **可以在中断上下文调**（核心内部转交 work）。`type` 取 `RPROC_MMUFAULT` / `RPROC_WATCHDOG` / `RPROC_FATAL_ERROR` |

**`rproc_boot()` / `rproc_shutdown()` 的引用计数不是 kref，是 `power` 计数**。
它的含义是"有几个消费者需要这个远端在跑"，和 `struct rproc` 本身的生命周期无关。
⇒ 两套计数并存，02 篇会摊开。

**谁在调 `rproc_boot()`？** 三类：
sysfs 的 `state` 属性（写 `start`）、字符设备的 ioctl、
以及**远端的消费者驱动**（例如某个 DSP 编解码驱动通过 `rproc_get_by_phandle()`
拿到 rproc 再 boot 它）。第三类是 06 篇 DT 里 `remoteproc` phandle 的用途。

---

## §3 查找 API

| API | 说明 | 备注 |
|---|---|---|
| `rproc_get_by_phandle(phandle)` | 按 DT phandle 找到已注册的 rproc，**并加两重引用** | 拿到之后必须 `rproc_put()`。失败一律返回 **NULL**（没有 `ERR_PTR`），所以调用者分不出"节点不存在""还没注册""模块正在卸载"三种情况 |
| `rproc_get_by_child(dev)` | 沿 `dev->parent` 链向上找，返回第一个 rproc 设备的 `driver_data` | **不加任何引用**，也**没有**配对的 put。找不到返回 NULL。给 subdev / 子设备驱动用 |

⚠️ **`rproc_get_by_phandle()` 有两个定义**：`CONFIG_OF` 打开时是真实现，
关掉时是一个直接返回错误的桩。两者都在 `remoteproc_core.c` 里。
⇒ 06 篇讲的"没有 DT 就没有 phandle 这条路"在代码上就是这个桩。

**两个函数的引用语义不一样，是本子系统最容易漏 `put` 的地方**：
名字都叫 `get_by_*`，一个加引用一个不加。判据是"它有没有对应的 put"。
详见 §12 和 08 篇。

**"两重引用"是什么意思**：`rproc_get_by_phandle()` 成功时做了**两件**事 ——
`get_device(&rproc->dev)` 加设备引用，以及 `try_module_get()` **把实现这个 rproc 的
驱动模块钉住**。对应地，`rproc_put()` 也做两件事：`module_put()` + `put_device()`。

⇒ 由此得到两条推论：

| 推论 | 后果 |
|---|---|
| 拿着一个 phandle 引用不放，会让**平台驱动无法 `rmmod`** | 07 篇排查"模块卸不掉"时第一个要看的地方 |
| `rproc_put()` 和 `rproc_free()` **都会 `put_device()`，但只有前者 `module_put()`** | ⇒ 两者绝对不能互换，见 §12 |

---

## §4 内存与地址转换 API

**头文件**：`include/linux/remoteproc.h`（`rproc_va_to_pa` 例外，见 §11）；
**实现**：`drivers/remoteproc/remoteproc_core.c`。

**先说清这一组为什么存在**：远端处理器看到的地址（device address，`da`）
和 CPU 看到的地址（物理地址 / 虚拟地址）**不是一回事**。
固件里写的每一个地址都是 `da`；内核这边要 memcpy 进去，就得先换算。
这一组 API 就是这套换算的全部入口。

| API | 说明 | 备注 |
|---|---|---|
| `rproc_mem_entry_init(dev, va, dma, len, da, alloc, release, name, ...)` | 造一个 `struct rproc_mem_entry` 描述一块内存区 | 返回指针，失败返回 NULL。`name` 是 **printf 风格可变参数**（所以能写 `"vdev%dbuffer"`）。`alloc` 传 NULL 表示"这块内存已经现成了" |
| `rproc_of_resm_mem_entry_init(dev, of_resm_idx, len, da, name, ...)` | 同上，但内存来自 DT 的 `memory-region` 第 `of_resm_idx` 项 | 这是 arm64 平台最常用的一条路：预留内存在 DTS 里声明，驱动只报索引。见 06 篇 |
| `rproc_add_carveout(rproc, mem)` | 把上面造好的 entry 挂进 `rproc->carveouts` | **返回 void，不会失败**；只能在 `rproc_add()` **之前**调 |
| `rproc_da_to_va(rproc, da, len, is_iomem)` | 把远端地址 `da` 翻译成内核虚拟地址 | 失败返回 **NULL**。`len` 参与校验（要求整段 `[da, da+len)` 都在同一区里）。`is_iomem` 是**出参**，可以传 NULL |
| `rproc_va_to_pa(cpu_addr)` | 内核虚拟地址 → 物理地址 | 半公开（见 §11）。会区分 vmalloc 地址和线性映射地址两种情形 |

**`rproc_da_to_va()` 的查找有两级**：先问驱动的 `->da_to_va` 回调，
驱动没实现或返回失败，再遍历 `rproc->carveouts` 逐个比对区间。
⇒ 所以**驱动可以完全不实现 `->da_to_va`**，只要把 carveout 都注册进来。
两级的先后和边界条件在 03 篇讲。

**`is_iomem` 出参不是可选的装饰**：它告诉调用者"这块地方能不能用普通
`memcpy`"。为真时必须走 `memcpy_toio()` 一类的访问器。
⇒ ELF 加载器就是靠这个参数决定用哪种 copy 的，这条链在 03 篇有完整走查。

⚠️ **三个 `rproc_mem_entry_init()` 参数容易配错的点**：

| 参数 | 配错的后果 |
|---|---|
| `da` 写成 CPU 侧地址 | 固件访问到别处，**通常不报错，直接跑飞** |
| `len` 比实际区小 | `rproc_da_to_va()` 对合法请求返回 NULL，表现为加载失败 |
| `name` 和资源表里的 carveout 名字不一致 | 核心按名字匹配资源表项，配不上就当成"要新分配"，于是分配了两块 |

---

## §5 崩溃转储（coredump）API

**头文件**：`include/linux/remoteproc.h`；**实现**：`drivers/remoteproc/remoteproc_coredump.c`。

这一组是**可选**的：驱动一行都不调，崩溃恢复也照样工作，只是拿不到现场。

| API | 说明 | 备注 |
|---|---|---|
| `rproc_coredump_add_segment(rproc, da, size)` | 声明"崩溃时把 `[da, da+size)` 这段导出来" | 内容由核心自己读（经 `rproc_da_to_va()`） |
| `rproc_coredump_add_custom_segment(rproc, da, size, dumpfn, priv)` | 同上，但**由驱动的 `dumpfn` 负责填内容** | 用于那种"内存必须经过特殊寄存器窗口才能读"的远端 |
| `rproc_coredump_set_elf_info(rproc, class, machine)` | 指定生成的 core 文件的 ELF class（32/64）和机器类型 | 不调的话核心用固件本身的 ELF 头信息 |
| `rproc_coredump_using_sections(rproc)` | 改用 ELF **section** 而不是 **program header** 组织 core | 一次性开关，返回 void |

**转储行为由 `rproc->dump_conf` 三态决定，不是由"有没有加 segment"决定**：

| 取值 | 含义 |
|---|---|
| `RPROC_COREDUMP_DISABLED` | **默认值**：一个字节都不导 |
| `RPROC_COREDUMP_ENABLED` | 先拷到独立缓冲区，然后继续恢复 |
| `RPROC_COREDUMP_INLINE` | 直接从设备内存读，**恢复流程被卡住直到读完** |

⇒ 这三个值就是 sysfs `coredump` 属性能写的三个字符串，07 篇会给读写方法。
⚠️ 默认是 DISABLED ⇒ **驱动加了一堆 segment 但没人改过 `coredump` 属性时，
现场依然是空的**。这是排查"为什么没有 dump"的第一现场。

导出通路走的是内核的 **devcoredump** 框架（`/sys/class/devcoredump/`），
不是 remoteproc 自己发明的路径。

---

## §6 子设备（subdev）API

**头文件**：`include/linux/remoteproc.h`；**实现**：`drivers/remoteproc/remoteproc_core.c`。

| API | 说明 | 备注 |
|---|---|---|
| `rproc_add_subdev(rproc, subdev)` | 把一个 subdev 挂到 rproc 的启停流程上 | 返回 void；**调用者自己负责填好 `subdev` 里的四个回调**（可以都是 NULL） |
| `rproc_remove_subdev(rproc, subdev)` | 摘下来 | 返回 void |

`struct rproc_subdev` 只有四个回调，语义是"跟着 rproc 一起起停"：

| 回调 | 时机 | 备注 |
|---|---|---|
| `prepare(subdev)` | 远端**启动前** | 返回 int，失败会回滚已 prepare 的 |
| `start(subdev)` | 远端**启动后** | 返回 int |
| `stop(subdev, crashed)` | 远端**停止前** | `crashed` 参数告诉你"这次是崩溃恢复还是正常关机" |
| `unprepare(subdev)` | 远端**停止后** | 返回 void |

**`stop` 的 `crashed` 参数是这一组里最实用的设计**：崩溃时远端已经不响应了，
subdev 不能再往它发消息；正常关机时可以先礼貌地告别。
⇒ 典型使用者是 rpmsg/glink 通道和高通的 sysmon，04 篇会展开。

**subdev 不是 `struct device`**，也不出现在 sysfs 里。它是纯内核内部的
"启停挂钩点"，别按驱动模型的 device/driver 去理解——08 篇会专门破这个类比。

---

## §7 ELF 默认实现（可以直接填进 `rproc_ops`）

**头文件**：`drivers/remoteproc/remoteproc_internal.h`（半公开，见 §11）；
**实现**：`drivers/remoteproc/remoteproc_elf_loader.c`。

**这五个函数的特殊之处：它们的原型和 `rproc_ops` 里对应成员的原型一模一样**，
所以驱动的写法是直接把函数名填进 ops 里，不是"调用"它们。

| API | 对应 `rproc_ops` 成员 | 在 `drivers/remoteproc/` 里被几个驱动文件用到 |
|---|---|---|
| `rproc_elf_sanity_check(rproc, fw)` | `.sanity_check` | 7 |
| `rproc_elf_get_boot_addr(rproc, fw)` | `.get_boot_addr` | 8 |
| `rproc_elf_load_segments(rproc, fw)` | `.load` | 7 |
| `rproc_elf_load_rsc_table(rproc, fw)` | `.parse_fw` | 6 |
| `rproc_elf_find_loaded_rsc_table(rproc, fw)` | `.find_loaded_rsc_table` | 4 |

上面的计数**不含 `remoteproc_elf_loader.c` 和 `remoteproc_core.c` 自己**，
统计范围只在 `drivers/remoteproc/*.c`（复现命令见 99 §3）。

**核心会自动替驱动填上这五个** —— 但填法有一个很容易踩的条件。
动作发生在 `rproc_alloc_ops()`（`drivers/remoteproc/remoteproc_core.c:2397-2418`），
它先 `kmemdup()` 一份驱动传进来的 ops，然后：

```
   if (rproc->ops->load)
           return 0;            ← 驱动填了 .load 就直接返回
   rproc->ops->load                  = rproc_elf_load_segments;
   rproc->ops->parse_fw              = rproc_elf_load_rsc_table;
   rproc->ops->find_loaded_rsc_table = rproc_elf_find_loaded_rsc_table;
   rproc->ops->sanity_check          = rproc_elf_sanity_check;
   rproc->ops->get_boot_addr         = rproc_elf_get_boot_addr;
```

⇒ **两条推论**：

| 推论 | 后果 |
|---|---|
| 驱动这五个全留 NULL ⇒ 一个字不写就得到完整的 ELF 加载能力 | 05 篇的最简骨架靠的就是这个 |
| ⚠️ 这五个是**全有全无**，开关是 `.load`。驱动只想换掉 `.load`、其余想继续用 ELF 默认的 —— **拿不到**，另外四个仍是 NULL | 于是 `sanity_check` 静默变成"不检查"（包装返回 0），`parse_fw` 静默变成"资源表为空" |

因此**自己实现 `.load` 的驱动必须把另外四个也一并填上**（哪怕就填 ELF 版本的函数名）。
本树里那 7 个自己实现 `.load` 的驱动确实都是这么写的。

⚠️ 唯一需要驱动自己判断的是 `.parse_fw`：ELF 版本只做"把资源表拷进来"这一件事。
如果远端还需要 IOMMU 映射、carveout 预分配之类的额外准备，
就得自己写 `.parse_fw` 并在里面**手动调一次** `rproc_elf_load_rsc_table()`。
这是 05 篇骨架里出现频率最高的一段。

---

## §8 virtio 侧唯一的对外函数

**头文件**：`drivers/remoteproc/remoteproc_internal.h`；
**实现**：`drivers/remoteproc/remoteproc_virtio.c`。

| API | 说明 | 备注 |
|---|---|---|
| `rproc_vq_interrupt(rproc, vq_id)` | 告诉 virtio 层"`vq_id` 这条队列上有新东西了" | 返回 `irqreturn_t`，**设计上就是在中断处理函数里调的**。找不到该队列时返回 `IRQ_NONE` |

**这个函数是 remoteproc 和 virtio 之间的"上行"方向**。配套的"下行"方向是
`rproc_ops` 里的 `.kick` 回调。两个方向合起来才是完整的门铃：

```
   内核 → 远端 ：virtio 层 → 核心 → 驱动的 ->kick(rproc, vqid) → 写硬件寄存器/mailbox
   远端 → 内核 ：硬件中断 → 驱动的中断处理 → rproc_vq_interrupt(rproc, vqid) → virtio 层
```

⇒ **写一个 remoteproc 驱动，绕不开的就是这两个**：`.kick` 必须实现，
中断处理里必须调 `rproc_vq_interrupt()`。08 篇会用"门铃"这个类比展开，
并说明它在哪里会误导你。

`vq_id` 不是驱动自己编的号，是核心在处理 `RSC_VDEV` 资源时分配的 notifyid。
远端固件通过资源表拿到同一个号 ⇒ 两边对得上。这条链在 03 篇。

---

## §9 `rproc_ops` 回调清单（驱动要填的那一面）

**头文件**：`include/linux/remoteproc.h`。

**这张表是写驱动时最该先看的一张**：`struct rproc_ops` 共 **17 个成员**，
但真正"必须实现"的只有很少几个。「不实现会怎样」一列是核心里
`static inline` 包装的行为（`drivers/remoteproc/remoteproc_internal.h`）。

| 回调 | 说明 | 不实现会怎样 |
|---|---|---|
| `prepare(rproc)` | 上电/解复位之前的准备（时钟、电源域、IOMMU） | 返回 0，直接跳过 |
| `unprepare(rproc)` | `prepare` 的反面 | 跳过 |
| `start(rproc)` | **让远端真正开始执行**（放复位） | **`rproc_add()` 当场返回 `-EINVAL`**（`rproc_validate()` 就查这个） |
| `stop(rproc)` | 停住远端 | `rproc_shutdown()` 失败（核心显式查 NULL） |
| `attach(rproc)` | 接管一个"已经在跑"的远端 | 驱动把 state 设成 `RPROC_DETACHED` 时，**`rproc_add()` 当场返回 `-EINVAL`**；不走 attach 路径则无所谓 |
| `detach(rproc)` | 撒手不管但不停它 | `rproc_detach()` 失败 |
| `kick(rproc, vqid)` | 向远端发通知 | **virtio 设备注册直接失败**（`remoteproc_virtio.c` 显式查 `ops->kick == NULL`）。不用 virtio 才可以留空 |
| `da_to_va(rproc, da, len, is_iomem)` | 自定义地址翻译 | 退回遍历 carveouts，见 §4 |
| `parse_fw(rproc, fw)` | 从固件里取资源表 + 做加载前准备 | 自己没填 `.load` 时被自动填成 ELF 版本；**否则**返回 0（⇒ 资源表是空的） |
| `handle_rsc(rproc, rsc_type, rsc, offset, avail)` | 处理厂商自定义资源项 | 包装返回 `RSC_IGNORED`，核心跳过该项 |
| `find_loaded_rsc_table(rproc, fw)` | 找到已加载到远端内存里的资源表 | 返回 NULL |
| `get_loaded_rsc_table(rproc, size)` | attach 场景下取资源表（**没有 fw 参数**） | 返回 NULL ⇒ attach 时拿不到 vdev |
| `load(rproc, fw)` | 把固件段搬进远端内存 | **不会失败**：`rproc_alloc()` 自动填成 ELF 加载器，见 §7 |
| `sanity_check(rproc, fw)` | 固件合法性检查 | 同上：自动填 ELF 版本，**否则**返回 0（⇒ 等于不检查） |
| `get_boot_addr(rproc, fw)` | 取入口地址 | 同上：自动填 ELF 版本，**否则**返回 0 |
| `panic(rproc)` | 内核 panic 时给远端一个机会存盘；返回**还需要等多少毫秒** | 不调 |
| `coredump(rproc)` | 自定义转储 | 用核心的 `rproc_coredump()` |

⇒ **最小可用集合其实只有三个：`start` + `stop` + `kick`**（用 virtio 时）。
`load` 不在里面 —— 因为留空就自动得到 ELF 加载器。这个结论是 05 篇最简骨架的依据。

**"不实现会怎样"其实有两层机制，别混起来**：

| 层 | 在哪 | 干什么 |
|---|---|---|
| 第一层：**替换成 ELF 实现** | `rproc_alloc_ops()`，`drivers/remoteproc/remoteproc_core.c:2397` | 把 5 个 ELF 成员 + `coredump` 真的写进 ops，**全有全无**，见 §7 |
| 第二层：**NULL 就走良性默认** | `remoteproc_internal.h` 里的 9 个 `static inline` 包装 | 返回 0 / NULL / `RSC_IGNORED`，见 §11 |

由此还得到一个有趣的结论：`rproc_load_segments()` 包装里那句
`return -EINVAL`（`->load` 为 NULL 的分支）在正常路径上**不可达** ——
第一层已经保证 `->load` 非 NULL 了。

**`find_loaded_rsc_table` 和 `get_loaded_rsc_table` 名字只差一个词，
用途完全不同**：前者在 **boot** 路径上、有 `fw` 可参考；后者在 **attach**
路径上、只有 `size` 出参，因为那时根本没有固件文件。
⇒ 这是本子系统"名字很像"陷阱里最典型的一对，02 篇 §1.0 和 08 篇都会点名。

---

## §10 错误码语义

**先说最重要的一条：本子系统的对外 API 不用 `ERR_PTR`。**
四个返回指针的函数（`rproc_alloc` / `devm_rproc_alloc` / `rproc_get_by_phandle` /
`rproc_get_by_child` / `rproc_mem_entry_init` / `rproc_of_resm_mem_entry_init`）
**失败一律返回 NULL**，`IS_ERR()` 在这里是错的用法。

唯一有 `ERR_PTR` 的核心文件是 `remoteproc_virtio.c`，而且它是**给 virtio 层用的**
（`->find_vqs` 那条路要求 `ERR_PTR` 约定），不是 remoteproc 自己的对外 API。

### §10.1 直接 `return -E常量` 的分布（核心七个 `.c`，共 74 处）

| 错误码 | 次数 | 语义 |
|---|---|---|
| `-EINVAL` | **46** | 压倒性多数：参数不合法、资源表格式不对、状态不允许 |
| `-ENOMEM` | 21 | 分配失败 |
| `-EFAULT` | 5 | 全部来自**用户态拷贝失败**（cdev 的 ioctl、debugfs 的 write） |
| `-ENOSYS` | 1 | 特殊：IOMMU 缺页处理函数**故意**返回它，意思是"我没真处理这个 fault，只是拿它当崩溃触发器" |
| `-EBUSY` | 1 | sysfs 的 `coredump` 属性：远端处于 `RPROC_CRASHED` 时不许改配置 |

另有赋值形式（`ret = -EXXX`）17 处：`-EINVAL` 10、`-ENOMEM` 3、`-EBUSY` 2、
`-EOVERFLOW` 1、`-ENODEV` 1。上表只统计 `return -E` 直接返回的形式，
两个统计**不要相加**（同一个错误可能先赋值后 `return ret`）。复现命令见 99 §3。

### §10.2 由此得到的排错含义

**有一处会主动"吞掉"真实错误码**：`rproc_set_firmware()` 里
`mutex_lock_interruptible()` 失败时，函数把拿到的 `ret` 打进 dmesg，
然后 **`return -EINVAL`** 而不是返回原码。
⇒ 于是"被信号打断"和"参数不合法"在调用者眼里长得一样。

⚠️ **`-EINVAL` 占了三分之二 ⇒ 光看返回码几乎定不了位。**
本子系统的实际排错入口是 **`dev_err()` 的文字**，不是错误码。
⇒ 这是 07 篇把「dmesg 文本 → 原因」表做成主表、而不是做错误码表的原因。

| 你看到 | 大概率原因 |
|---|---|
| `rproc_boot()` 返回 `-EINVAL` | 状态不对（已经在跑 / 已 detach），或资源表校验失败 |
| `rproc_add()` 返回 `-EINVAL` | 通常不是 `rproc_add()` 自己的错，是它内部触发的固件加载失败 |
| `rproc_set_firmware()` 返回 `-EBUSY` | 远端不在 `RPROC_OFFLINE` 状态。dmesg 里配套一行 `can't change firmware while running` |
| `rproc_set_firmware()` 返回 `-EINVAL` | ⚠️ 三种情形挤在一个码上：参数为 NULL、**拿锁被信号打断**、固件名是空串。只有后两种有 dmesg 提示 |

---

## §11 `remoteproc_internal.h`：为什么它算"半公开"

前面 §7、§8 里那几个函数（`rproc_elf_*`、`rproc_vq_interrupt`、
`rproc_va_to_pa`、`rproc_find_carveout_by_name`）都声明在
`drivers/remoteproc/remoteproc_internal.h` 里，不在 `include/` 下。事实是：

| 事实 | 数字/依据 |
|---|---|
| `drivers/remoteproc/*.c` 里 include 它的文件数 | **28**（共 34 个 `.c`） |
| `drivers/remoteproc/` **以外**有多少文件 include 它 | **0** |
| 这些函数带 `EXPORT_SYMBOL` 吗 | 带（`rproc_elf_*` 五个 + `rproc_va_to_pa` + `rproc_vq_interrupt`） |
| 带 `EXPORT_SYMBOL_GPL` 的有几个 | **0** |

⇒ **结论**：它们在**链接层面**是全内核可见的，在**头文件层面**只对住在
`drivers/remoteproc/` 里的驱动可见。所以"要不要把远端驱动放进这个目录"
不只是代码风格问题，它决定了你能不能白用 ELF 加载器。

这个头文件里还有一类东西**只有核心用得上**、驱动碰不得：
`rproc_release`（kref 释放）、`rproc_trigger_recovery`、
debugfs/sysfs/cdev 的 init/exit、`rproc_alloc_vring` / `rproc_parse_vring` /
`rproc_free_vring`、`rproc_add_rvdev` / `rproc_remove_rvdev`。
判据很简单：**这些函数没有一个出现在任何驱动文件里**。

还有第三类，也是本子系统最有代码风味的一处设计 ——
那九个 `static inline` **回调包装**（`rproc_prepare_device` / `rproc_unprepare_device` /
`rproc_attach_device` / `rproc_fw_sanity_check` / `rproc_get_boot_addr` /
`rproc_load_segments` / `rproc_parse_fw` / `rproc_handle_rsc` /
`rproc_find_loaded_rsc_table`）。核心**从不写 `rproc->ops->start()` 这种直接调用**，
一律经过包装；§9 那张表最后一列的"不实现会怎样"，答案全部藏在这九个函数体里。
逐个函数体的走查在 03 篇。

`remoteproc_cdev.c` 相关的四个原型还额外演示了一个技巧：
`CONFIG_REMOTEPROC_CDEV` 关掉时提供 `static inline` 空实现，而且
`rproc_char_device_add()` 的空实现**返回 0 而不是错误码**——
源码注释明确写了"这是可选特性，关掉时不该报错"。
⇒ 于是 `rproc_add()` 在没开 cdev 的内核上一样成功，只是没有 `/dev/remoteprocN`。

---

## §12 常见误用

按"会不会立刻炸"排序，越靠前越隐蔽。

| # | 误用 | 后果 | 正确做法 |
|---|---|---|---|
| 1 | 用 `IS_ERR()` 判断 `rproc_alloc()` / `rproc_get_by_phandle()` 的返回值 | **NULL 会被当成成功**，随后空指针解引用 | 判 `if (!rproc)`，见 §10 |
| 2 | 拿 `rproc_get_by_phandle()` 的引用后调 `rproc_free()` | 少了 `module_put()`，**目标驱动永远卸不掉**；而且 `rproc_free()` 语义上是给分配者用的 | 用 `rproc_put()`，见 §3 |
| 3 | 在 `rproc_add()` **之后**再 `rproc_add_carveout()` | carveout 可能没赶上加载 —— 而 `rproc_add()` 可能已经在返回前就 boot 完了 | 全部 carveout 在 `rproc_add()` 前挂好，见 §1 |
| 4 | 把 `rproc_detach()` 当 `rproc_shutdown()` 用 | **远端还在跑**，内核只是不管了；下次 boot 会撞上"已经在运行" | 按语义选，见 §2 |
| 5 | `rproc_boot()` 和 `rproc_shutdown()` 次数不配对 | 多 boot ⇒ 远端永不停；多 shutdown ⇒ 源码注释直接称之为 bug | 一一配对 |
| 6 | 在原子上下文调 `rproc_boot()` | 它会 `request_firmware()`，**会睡** | 放到 workqueue 里 |
| 7 | 用 `rproc_report_crash()` 之外的方式触发恢复 | 恢复流程的串行化全靠核心的 work | 无论什么上下文都直接调 `rproc_report_crash()`，它自己转交 work，见 §2 |
| 8 | 加了 coredump segment 就以为能拿到 dump | `dump_conf` 默认 **DISABLED** | 先写 sysfs 的 `coredump` 属性，见 §5 |
| 9 | 把 `find_loaded_rsc_table` 和 `get_loaded_rsc_table` 填反 | attach 路径拿不到资源表，远端起来了但没有 rpmsg 通道 | 见 §9 |
| 10 | 实现了 `.kick` 但中断里忘了调 `rproc_vq_interrupt()` | 单向通信：内核发得出去，收不回来。表现为 rpmsg 发送成功但永远没有回复 | 见 §8 |
| 11 | `rproc_mem_entry_init()` 的 `da` 填了 CPU 地址 | 通常**不报错**，远端直接跑飞 | 见 §4 |
| 12 | 出树驱动想用 `rproc_elf_load_segments()` | 原型不在 `include/` 里 | 见 §11 |

---

**下一篇**：02 篇讲这些 API 背后的数据结构，第一节先分清本子系统最容易混的几组关系
（rproc ↔ virtio ↔ rpmsg、内核结构 ↔ 固件 ABI 结构、subdev ↔ device）。
