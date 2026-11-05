---
title: API 参考
---

# 01 代码地图与对外 API：dmaengine

> 本篇是**查函数的入口**：先给"文件在哪"，再给"函数怎么用"。
> 表格固定三列 `API / 说明 / 备注`；签名只写**函数名 + 形参名**，不带类型；
> 位置只精确到**文件**，每节开头一行交代头文件与实现文件。
> 只讲 arm64；ACPI 相关接口一律不列（裁剪记录见 `99-evidence.md` §2.7）。

---

## §0 代码地图

### §0.1 核心文件

| 文件 | 职责 | 谁该读 |
|---|---|---|
| `include/linux/dmaengine.h` | **唯一的对外头文件**。全部数据结构 + 绝大多数消费者 API（大量是 `static inline` 包装） | 消费者、提供方都要读 |
| `drivers/dma/dmaengine.c` | 核心实现：通道分配/释放、能力查询、设备注册、percpu 通道表、sysfs class、debugfs summary | 想弄清"通道是怎么被挑出来的"必读 |
| `drivers/dma/dmaengine.h` | **提供方私有头**，不是对外 API。文件头第 3–4 行明确写着「private to DMA engine drivers」。cookie 管理 + 回调调用封装 | 只写控制器驱动的人读 |
| `drivers/dma/of-dma.c` | DT 侧：`dmas`/`dma-names` 解析、控制器与 router 注册、三个通用 xlate | 写控制器驱动或排 DTS 问题时读 |
| `drivers/dma/virt-dma.c` + `virt-dma.h` | **可选**的"虚拟通道"辅助层（软件描述符队列 + tasklet 完成回调）。全树 51 个文件 include 它 | 写控制器驱动强烈建议读 |
| `drivers/dma/dmatest.c` | 自带的 memcpy/memset/xor/pq 压测客户端，是本子系统的故障注入设施（见 `07-debug.md`） | 验板/怀疑控制器驱动有 bug 时读 |
| `drivers/dma/acpi-dma.c` | ACPI 侧 —— **arm64-only 范围内裁掉，本文档集不涉及** | — |

配套的**协议/硬件描述头**在 `include/linux/dma/` 下（19 个文件），全是特定厂商的
私有描述符格式（最大的是 `ti-cppi5.h`），不属于通用 API，本篇不展开。

### §0.2 Kconfig 开关

| 开关 | 类型 | 说明 |
|---|---|---|
| `CONFIG_DMADEVICES` | menuconfig | 总闸。`depends on HAS_DMA` |
| `CONFIG_DMA_ENGINE` | 无 prompt 的 `bool` | **不能手选**，由各控制器驱动 `select` 上来。关掉时 `include/linux/dmaengine.h` 提供一整套桩函数（见 §10.3） |
| `CONFIG_DMA_VIRTUAL_CHANNELS` | 无 prompt 的 `tristate` | virt-dma 辅助层，同样靠 `select` |
| `CONFIG_DMA_OF` | `def_bool y`，`depends on OF` | 它 **`select DMA_ENGINE`** —— arm64 + OF 的机器上 dmaengine 核心天然被编进去 |
| `CONFIG_DMADEVICES_DEBUG` | bool | 给整个 `drivers/dma/` 加 `-DDEBUG`（`drivers/dma/Makefile:3`），把 `dev_dbg` 变成真输出 |
| `CONFIG_DMADEVICES_VDEBUG` | bool | 再加 `-DVERBOSE_DEBUG`（`Makefile:4`），依赖上一项 |
| `CONFIG_DMATEST` | tristate | 压测客户端，`select DMA_ENGINE_RAID` |
| `CONFIG_DMA_ENGINE_RAID` | 无 prompt 的 `bool` | 只影响 `struct dmaengine_unmap_data` 里 `map_cnt` 是 u16 还是 u8 |
| `CONFIG_ASYNC_TX_ENABLE_CHANNEL_SWITCH` | 无 prompt 的 `bool` | **arm64 上无人 select**。它决定 `struct dma_async_tx_descriptor` 是否带 `next/parent/lock` 三个字段 |
| `CONFIG_DMA_ACPI` | `def_bool y`，`depends on ACPI` | 裁掉 |

### §0.3 三个代表驱动（后续各篇统一用这三个举例）

选法与理由登记在 `99-evidence.md` §2.2，这里只给结论：

| 角色 | 驱动 | 行数 | 选它的理由 |
|---|---|---|---|
| 最简实现 | `drivers/dma/mediatek/mtk-uart-apdma.c` | 656 | 只做 DMA_SLAVE，用 virt-dma，是"控制器驱动的最小完整形态" |
| 全功能实现 | `drivers/dma/pl330.c` | 3277 | MEMCPY + SLAVE + CYCLIC，arm64 DTS 里出现 27 次；**不用 virt-dma**，自己管描述符 —— 和上一个刚好构成对照 |
| arm64 上最常见 | `drivers/dma/qcom/bam_dma.c` | 1496 | `qcom,bam-v1.7.0` 在 arm64 DTS 里出现 **22** 次，是本树 arm64 DMA 控制器的头名 |

---

## §1 消费者 API：申请与释放通道

> 头文件：`include/linux/dmaengine.h`；实现：`drivers/dma/dmaengine.c`（DT 部分在 `drivers/dma/of-dma.c`）

| API | 说明 | 备注 |
|---|---|---|
| `dma_request_chan(dev, name)` | **从 DTS 的 `dma-names` 里按名字要一条从设备通道**，是 arm64 上唯一推荐的申请方式 | 返回 `ERR_PTR`，**必须用 `IS_ERR()` 判断**。可能返回 `-EPROBE_DEFER`（控制器还没注册），驱动要原样往上传。成功后核心会把 `chan->slave` 指向 `dev`，并建两条 sysfs 符号链接 |
| `dma_request_chan_by_mask(mask)` | 按**能力位掩码**要一条通道，不经过 DTS | 给 memcpy/xor 这类"任意通道都行"的客户端用。返回 `ERR_PTR`；`mask` 为 NULL 时 `-ENODEV` |
| `dma_release_channel(chan)` | 归还通道 | 与上面两个**都**配对。内部 `WARN_ONCE(chan->client_count != 1, ...)` —— 重复释放或还有别人持有会打栈 |
| `dma_request_channel(mask, fn, fn_param)` | 宏，展开成 `__dma_request_channel(&mask, fn, fn_param, NULL)` | 老式接口。返回**裸 NULL** 而不是 `ERR_PTR`，判断方式和 `dma_request_chan` 不一样 |
| `dma_request_slave_channel(dev, name)` | `dma_request_chan` 的"失败返回 NULL"版 | 带 `__deprecated` 标记，**新代码不要用** |
| `dma_request_slave_channel_compat(mask, fn, fn_param, dev, name)` | 先试 DT，失败再退回掩码匹配 | `static inline`，为 DT/非 DT 混合平台留的过渡接口，arm64 上没有必要 |
| `dmaengine_get()` / `dmaengine_put()` | 声明/撤销"我要用**公共**（非 private）通道" | 只有走 `dma_find_channel()` 那条 async_tx 路线才需要。它维护核心里的 `dmaengine_ref_count` |
| `dma_find_channel(tx_type)` | 从 percpu 通道表里取一条能做 `tx_type` 的公共通道 | 一行 `this_cpu_read(...)`，**不加引用计数**，必须先 `dmaengine_get()` |
| `dma_issue_pending_all()` | 给所有公共通道下发 | 跳过带 `DMA_PRIVATE` 的设备 |
| `dmaengine_get_dma_device(chan)` | **做 DMA 映射时必须用它拿 `struct device *`**，不要用自己的 `dev` | 有些控制器（如经 IOMMU 的）需要用通道自己的 device 做映射：当 `chan->dev->chan_dma_dev` 为真时返回 `&chan->dev->device`，否则返回 `chan->device->dev`。`Documentation/driver-api/dmaengine/client.rst` 反复强调这一点 |

**关于 `-EPROBE_DEFER`**：核心有三处会产生它 —— `of_dma_request_slave_channel()`
在 DTS 里找到了 phandle 但目标控制器尚未注册；`find_candidate()` 在没有任何候选时
无条件返回它；`dma_request_chan_by_mask()` 在 `dma_device_list` 为空时返回它。
所以**在 arm64 上 `dma_request_chan` 返回 `-EPROBE_DEFER` 是常态**，不是错误。

---

## §2 消费者 API：准备描述符

> 头文件：`include/linux/dmaengine.h`（全部是 `static inline` 包装，直接转调 `chan->device->device_prep_*`）

| API | 说明 | 备注 |
|---|---|---|
| `dmaengine_prep_slave_single(chan, buf, len, dir, flags)` | 单段外设 ↔ 内存传输 | 内部临时拼一个 `struct scatterlist` 再转调 `dmaengine_prep_slave_sg` |
| `dmaengine_prep_slave_sg(chan, sgl, sg_len, dir, flags)` | 散列表外设 ↔ 内存传输，**最常用** | `sgl` 必须已经 `dma_map_sg` 过 |
| `dmaengine_prep_dma_cyclic(chan, buf_addr, buf_len, period_len, dir, flags)` | 环形缓冲，做完一圈自动重来 | 音频/串口 RX 的标配。回调按 `period_len` 触发 |
| `dmaengine_prep_interleaved_dma(chan, xt, flags)` | 二维/跨步传输（Frame / Chunk / ICG 模型） | 若 `flags` 带 `DMA_PREP_REPEAT` 而设备**没有** `DMA_REPEAT` 能力位，直接返回 NULL |
| `dmaengine_prep_dma_memcpy(chan, dest, src, len, flags)` | 内存到内存拷贝 | |
| `dmaengine_prep_dma_memset(chan, dest, value, len, flags)` | 内存填充 | |
| `dmaengine_prep_dma_imm_data(chan, dest, data, flags)` | 把 8 字节立即数写到目标地址 | 极少用；`data` 是 `u64` 值本身，不是地址 |

> **这一族的返回值约定是全子系统最大的陷阱**：
> **失败一律返回 `NULL`，不是 `ERR_PTR`。** 而且"设备没实现对应的 `device_prep_*` 回调"
> 和"实现了但这次准备失败"**返回同一个 NULL**，消费者无法区分。
> 所以正确写法是 `if (!desc) return -EINVAL;`（自己编一个错误码），
> 用 `IS_ERR(desc)` 判断会把 NULL 当成成功。

**`flags` 取值**（`enum dma_ctrl_flags`，`include/linux/dmaengine.h`）：

| 标志 | 含义 |
|---|---|
| `DMA_PREP_INTERRUPT` | 传输完成后触发中断/回调。**不带这个就不会调 callback** |
| `DMA_CTRL_ACK` | 描述符可被复用 |
| `DMA_PREP_PQ_DISABLE_P` / `_Q` | RAID6 场景禁用 P/Q 校验 |
| `DMA_PREP_CONTINUE` | PQ 续算 |
| `DMA_PREP_FENCE` | 后续操作依赖本次结果 |
| `DMA_CTRL_REUSE` | 描述符可重复提交（配合 `dmaengine_desc_set_reuse`） |
| `DMA_PREP_CMD` | 本描述符是命令而非数据 |
| `DMA_PREP_REPEAT` | 传输自动重复（仅 interleaved） |
| `DMA_PREP_LOAD_EOT` | 头文件注释警告：REPEAT 事务之后的新事务**不带此标志就永远不会被处理，一直卡在 issued 队列里** |

---

## §3 消费者 API：提交、下发与查询

> 头文件：`include/linux/dmaengine.h`；`dma_sync_wait` / `dma_wait_for_async_tx` 实现在 `drivers/dma/dmaengine.c`

| API | 说明 | 备注 |
|---|---|---|
| `dmaengine_submit(desc)` | 把描述符交给引擎，返回 cookie | 实现是**裸调 `desc->tx_submit(desc)`，没有 NULL 检查** —— 前一步返回了 NULL 还往下走就是空指针解引用。返回后**描述符所有权归引擎**，消费者不得再碰 |
| `dma_submit_error(cookie)` | 判断 `dmaengine_submit` 的返回值是否是错误 | `static inline`，判据是 `cookie < 0` |
| `dma_async_issue_pending(chan)` | 真正启动传输 | 实现是**裸调 `chan->device->device_issue_pending(chan)`**，核心注册时已强制该回调必须存在。`dma_async_memcpy_issue_pending` 是它的旧名宏 |
| `dmaengine_tx_status(chan, cookie, state)` | 查一个 cookie 的状态 | `state` 可为 NULL（只要状态不要残留量）。`static inline` 转调 `device_tx_status` |
| `dma_async_is_tx_complete(chan, cookie, last, used)` | 同上，但额外回吐 last/used 两个 cookie | 老式接口；`dma_async_memcpy_complete` 是它的旧名宏 |
| `dma_async_is_complete(cookie, last_complete, last_used)` | **纯计算**，不碰硬件：给定三个 cookie 判断是否完成 | 内部有 cookie 回绕分支。给驱动自己实现 `device_tx_status` 时复用 |
| `dma_sync_wait(chan, cookie)` | 忙等到 cookie 完成 | **5000 ms 超时**，超时打 `dev_err(..., "%s: timeout!\n", __func__)`。中间用 `cpu_relax()` 空转，**不睡眠也不让出 CPU** |
| `dma_wait_for_async_tx(tx)` | 等一个还没 submit 的描述符（`cookie == -EBUSY`）被提交 | 5 s 超时，超时打 `"%s timeout waiting for descriptor submission\n"`。async_tx 内部用，普通从设备驱动不需要 |

**`enum dma_status` 的五个值**：`DMA_COMPLETE`、`DMA_IN_PROGRESS`、`DMA_PAUSED`、
`DMA_ERROR`、`DMA_OUT_OF_ORDER`。
头文件里那段 kerneldoc 只描述了**前四个**，`DMA_OUT_OF_ORDER` 无文档（记入 `99-evidence.md` §6）。

**残留量怎么读**：`struct dma_tx_state` 有 `last`/`used`/`residue`/`in_flight_bytes`
四个字段。`residue` 的**粒度**由控制器决定（`enum dma_residue_granularity`）：
`DESCRIPTOR`（只在描述符边界更新）/ `SEGMENT`（每段更新）/ `BURST`（每个 burst 更新）。
拿粒度要靠 `dma_get_slave_caps()`（§5）—— **不查粒度就相信 `residue`，是串口/音频驱动最常见的错误**。

---

## §4 消费者 API：暂停、恢复、终止、同步

> 头文件：`include/linux/dmaengine.h`

| API | 说明 | 备注 |
|---|---|---|
| `dmaengine_slave_config(chan, config)` | 设置从设备侧参数（地址、位宽、burst 长度） | 无 `device_config` 回调时返回 **`-ENOSYS`**。`config->direction` 字段头文件已标 **DEPRECATED**，方向应该在 prep 时传 |
| `dmaengine_pause(chan)` | 暂停 | 无回调返回 **`-ENOSYS`** |
| `dmaengine_resume(chan)` | 恢复 | 无回调返回 **`-ENOSYS`** |
| `dmaengine_terminate_async(chan)` | **异步**终止：只保证"不会再有新回调开始"，**不保证已在跑的回调已经结束** | 无回调返回 **`-EINVAL`**。可在原子上下文调用 |
| `dmaengine_synchronize(chan)` | 等所有回调真正结束 | 带 `might_sleep()`，**只能在可睡眠上下文调**。头文件警告：如果在 terminate 和 synchronize 之间调过 `dma_async_issue_pending()`，行为**未定义** |
| `dmaengine_terminate_sync(chan)` | 上面两个的组合：terminate_async + synchronize | **绝大多数驱动应该用这个**。会睡眠 |
| `dmaengine_terminate_all(chan)` | 老式同步终止 | 头文件明确标 **DEPRECATED**，无回调返回 **`-ENOSYS`** |

> 这里有一处**核心自己的不对称**，已登记在 `99-evidence.md` §6：
> 面对完全相同的"没有 `device_terminate_all` 回调"这一条件，
> `dmaengine_terminate_all()` 返回 `-ENOSYS`，而 `dmaengine_terminate_async()` 返回 `-EINVAL`。
> 换 API 时如果照抄错误码判断会出错。

**终止流程的正确形状**（`Documentation/driver-api/dmaengine/client.rst` 的要求）：

```
                  ┌─ 可以在原子上下文 ─┐
dmaengine_terminate_async(chan)          ← 停下硬件，阻止新回调开始
        │
        │  此刻可能还有一个回调正在另一个 CPU 上跑
        │
        ├─ 释放你的 buffer 之前必须先 ──┐
        ↓                               ↓
dmaengine_synchronize(chan)   ← might_sleep()，等回调跑完
        │
        ↓
   现在才可以 dma_unmap_* / kfree(buffer)
```

**跳过 synchronize 直接 free buffer，是 use-after-free 的经典来源。**
两步能合成一步的场合就直接用 `dmaengine_terminate_sync()`。

---

## §5 消费者 API：能力查询与对齐辅助

> 头文件：`include/linux/dmaengine.h`；`dma_get_slave_caps` 实现在 `drivers/dma/dmaengine.c`

| API | 说明 | 备注 |
|---|---|---|
| `dma_get_slave_caps(chan, caps)` | **查这条通道到底能干什么**：位宽集合、方向集合、burst 范围、残留粒度、能不能 pause/resume/terminate | 三种失败：`chan`/`caps` 为 NULL → `-EINVAL`；设备既没有 `DMA_SLAVE` 也没有 `DMA_CYCLIC` 能力位 → **`-ENXIO`**；`device->directions` 为 0 → **`-ENXIO`** |
| `dma_has_cap(tx_type, mask)` | 位掩码测试宏 | 配套还有 `dma_cap_set` / `dma_cap_clear` / `dma_cap_zero` / `for_each_dma_cap_mask` |
| `is_slave_direction(direction)` | 判断方向是否属于"外设相关" | `static inline`；DEV_TO_MEM / MEM_TO_DEV / DEV_TO_DEV 为真 |
| `dmaengine_get_direction_text(dir)` | 方向枚举 → 字符串 | 返回 `"DEV_TO_MEM"`/`"MEM_TO_DEV"`/`"MEM_TO_MEM"`/`"DEV_TO_DEV"`/`"invalid"`。打日志用 |
| `is_dma_copy_aligned(dev, off1, off2, len)` | 三个值是否都满足 memcpy 对齐要求 | 同族还有 `is_dma_xor_aligned` / `is_dma_pq_aligned` / `is_dma_fill_aligned`，分别读 `dev` 的 `copy_align`/`xor_align`/`pq_align`/`fill_align` |
| `dma_set_maxpq(dma, maxpq, has_continue)` / `dma_maxpq(dma, flags)` | RAID6 的 P+Q 源数量上下限 | `dma_maxpq()` 的最后一条路径是 **`BUG();`** —— 参数组合非法直接挂内核。RAID 场景专用，arm64 从设备驱动不涉及 |
| `dmaengine_get_icg(inc, sgl, icg, chunk_size)` | interleaved 传输里算 inter-chunk gap | 配套 `dmaengine_get_dst_icg` / `dmaengine_get_src_icg` |
| `dma_chan_name(chan)` | 通道的 sysfs 名 | 返回 `dev_name(&chan->dev->device)`，形如 `dma0chan1` |

**`dma_get_slave_caps` 里三个"派生"字段值得单独点出来**：`cmd_pause`、`cmd_resume`、
`cmd_terminate` 都不是控制器驱动直接填的，而是核心用 `!!device->device_pause` 这类
表达式**从回调指针是否为空推出来的**（`drivers/dma/dmaengine.c:608-610`）。
之后如果控制器提供了可选的 `device_caps` 回调，核心会让它**覆盖**前面所有字段
（`:614-615`）—— 所以同一个控制器的不同通道可以报不同能力。

---

## §6 消费者 API：描述符元数据

> 头文件：`include/linux/dmaengine.h`；实现在 `drivers/dma/dmaengine.c`

部分控制器（典型是 TI 的 UDMA/PSI-L）要求随描述符携带一段**带外元数据**。
有两种互斥的模式，由 `enum dma_desc_metadata_mode` 表示：

| 模式 | 谁提供 buffer | 用哪套 API |
|---|---|---|
| `DESC_METADATA_CLIENT` | **客户端**自己分配，交给引擎 | `dmaengine_desc_attach_metadata` |
| `DESC_METADATA_ENGINE` | **引擎**内部持有，客户端只借指针来读写 | `dmaengine_desc_get_metadata_ptr` + `dmaengine_desc_set_metadata_len` |

| API | 说明 | 备注 |
|---|---|---|
| `dmaengine_desc_attach_metadata(desc, data, len)` | CLIENT 模式：挂一段客户端 buffer | `!CONFIG_DMA_ENGINE` 时桩函数返回 `-EINVAL` |
| `dmaengine_desc_get_metadata_ptr(desc, payload_len, max_len)` | ENGINE 模式：借出引擎内部的 buffer 指针 | 返回 `ERR_PTR`；桩函数返回 `ERR_PTR(-EINVAL)` |
| `dmaengine_desc_set_metadata_len(desc, payload_len)` | ENGINE 模式：告诉引擎实际写了多少字节 | 桩函数返回 `-EINVAL` |

> **两种模式在同一个描述符上不能混用**。核心用一个内部函数
> `desc_check_and_set_metadata_mode()` 做门禁：第一次调用哪一族 API，
> 就把 `desc->desc_metadata_mode` 锁定成对应模式，之后调另一族直接 `-EINVAL`。
> 客户端应该先看 `chan->device->desc_metadata_modes` 支持哪一种。

头文件里还有一大段（`include/linux/dmaengine.h:232-281`）逐方向写清了
"发送前该做什么、收到完成回调后该做什么"的操作序列，写这类控制器的客户端必须逐字读。

---

## §7 消费者 API：描述符复用

> 头文件：`include/linux/dmaengine.h`

| API | 说明 | 备注 |
|---|---|---|
| `dmaengine_desc_set_reuse(tx)` | 把描述符标成"提交完还能再提交" | 内部先 `dma_get_slave_caps`，若控制器 `descriptor_reuse` 为假返回 **`-EPERM`** |
| `dmaengine_desc_clear_reuse(tx)` | 清掉复用标志 | `static inline`，无返回值 |
| `dmaengine_desc_free(tx)` | 主动释放一个可复用描述符 | 若该描述符**没有**带 `DMA_CTRL_REUSE`，返回 **`-EPERM`** |

复用的意义：环形/周期性场景下省掉每次重新 prep 的开销。代价是**描述符的生命周期
不再由引擎管**，消费者必须自己在最后调 `dmaengine_desc_free`。

---

## §8 提供方 API：注册控制器与通道

> 头文件：`include/linux/dmaengine.h`；实现：`drivers/dma/dmaengine.c`

| API | 说明 | 备注 |
|---|---|---|
| `dma_async_device_register(device)` | **控制器驱动 probe 的主入口**。做完整校验，分配 dev_id，注册每条通道到 sysfs，挂进全局链表，建 debugfs 目录 | 校验很严，失败点见下表。成功后核心才认这个控制器 |
| `dmaenginem_async_device_register(device)` | 上面的 **devres 托管版**，remove 时自动注销 | 新驱动优先用它，能少写一半错误处理 |
| `dma_async_device_unregister(device)` | 注销 | 第一件事是给设备打上 `DMA_PRIVATE`，源码注释说明目的是"确保正在拆除的设备不会再进 `channel_table`" |
| `dma_async_device_channel_register(device, chan)` | 运行期**热添加**一条通道 | 给通道数动态变化的控制器用 |
| `dma_async_device_channel_unregister(device, chan)` | 热移除一条通道 | 内部 `WARN_ONCE(!device->device_release && chan->client_count, "%s called while %d clients hold a reference\n", ...)` |
| `dma_async_tx_descriptor_init(tx, chan)` | 初始化一个描述符的公共部分 | 提供方在自己分配描述符后调 |
| `dma_run_dependencies(tx)` | 跑完本描述符后串联执行依赖链 | 只有 `CONFIG_ASYNC_TX_ENABLE_CHANNEL_SWITCH` 场景才有意义，arm64 上无人开 |

**`dma_async_device_register` 的失败清单**（这张表是排"控制器驱动 probe 失败"的第一手材料）：

| 条件 | 返回 | 打印 |
|---|---|---|
| `device` 为 NULL | `-ENODEV` | 无 |
| `device->dev` 为 NULL | `-EIO` | `pr_err("DMAdevice must have dev\n")` |
| 声明了 MEMCPY / XOR / XOR_VAL / PQ / PQ_VAL / MEMSET / INTERRUPT / CYCLIC / INTERLEAVE 中任一能力，但对应 `device_prep_*` 为空 | `-EIO` | `dev_err(..., "Device claims capability %s, but op is not defined\n", "<名字>")` |
| `device_tx_status` 为空 | `-EIO` | `dev_err(..., "Device tx_status is not defined\n")` |
| `device_issue_pending` 为空 | `-EIO` | `dev_err(..., "Device issue_pending is not defined\n")` |
| `device_release` 为空 | **不失败** | 只有 `dev_dbg`：「WARN: Device release is not defined so it is not safe to unbind this driver while in use」 |

> 上面那张表只覆盖了 **9 个**能力位。`DMA_MEMSET_SG`、`DMA_SLAVE`、`DMA_REPEAT`、
> `DMA_LOAD_EOT`、`DMA_COMPLETION_NO_ORDER` 这 5 个**声明了却不实现，注册照样成功**。
> 对 arm64 尤其要紧：从设备驱动最常用的就是 `DMA_SLAVE`，而它恰恰**没有**这道门。
> 已登记为 `99-evidence.md` §6 的第 3 条。

还有一个能力位是**核心自己加的**：`DMA_ASYNC_TX`。头文件明确写「不由驱动设置，
设备注册时自动置位」，判据函数是 `device_has_all_tx_types()`。

---

## §9 提供方 API：DT 与 router

> 头文件：`include/linux/of_dma.h`；实现：`drivers/dma/of-dma.c`

| API | 说明 | 备注 |
|---|---|---|
| `of_dma_controller_register(np, of_dma_xlate, data)` | 把控制器登记为"可被 `dmas` phandle 引用" | `np` 或 `of_dma_xlate` 为 NULL → `-EINVAL` + `pr_err("%s: not enough information provided\n")`。必须在 `dma_async_device_register` **之后**调 |
| `of_dma_controller_free(np)` | 注销 | remove 里配对调用 |
| `of_dma_router_register(np, of_dma_route_allocate, data)` | 登记一个 **DMA router**（请求线复用器） | 内部强制把 `of_dma_xlate` 设成 `of_dma_router_xlate`，驱动给的 xlate 不生效 |
| `of_dma_simple_xlate(dma_spec, ofdma)` | 通用 xlate #1：`#dma-cells = <1>`，把 `args[0]` 当 filter 参数 | 最常用。cells 不等于 1 直接返回 NULL |
| `of_dma_xlate_by_chan_id(dma_spec, ofdma)` | 通用 xlate #2：`args[0]` 直接就是 `chan_id` | 适合"通道号硬编码在 DTS 里"的控制器 |
| `of_dma_request_slave_channel(np, name)` | 核心内部用：解析 `dmas`/`dma-names` 拿通道 | 不是给驱动直接调的，`dma_request_chan()` 会调它 |

**`of_dma_request_slave_channel` 的三个行为值得单独记**（排 DTS 问题时全靠它们）：

1. 消费者节点**连 `dmas` 属性都没有**时，源码注释写着「Silently fail」，返回 `-ENODEV`，
   **一条日志都不打**。这是"DMA 明明配了却不生效"最常见的原因。
2. 有 `dmas` 但 `dma-names` 数量读不出来时，打
   `pr_err("%s: dma-names property of node '%pOF' missing or empty\n")`。
3. 多个 `dma-names` 条目**同名**时，核心用一个 `static atomic_t last_index` 做
   **轮转**（`(i + start) % count`），源码注释说目的是「approximate an average
   distribution across multiple entries with the same name」。
   也就是说**同名条目每次申请拿到的是不同的那一条**，顺序不可预期。

`of_dma_router_xlate` 里还有一条：目标控制器尚未注册时返回 `-EPROBE_DEFER`，
并且会把已经申请到的通道**释放掉再返回** —— router 场景的 defer 是干净的。

---

## §10 提供方私有 API：cookie 与 virt-dma

### §10.1 cookie 管理

> 头文件：`drivers/dma/dmaengine.h` —— **不是对外 API**，只有控制器驱动能 include

| API | 说明 | 备注 |
|---|---|---|
| `dma_cookie_init(chan)` | 初始化通道的 cookie 计数 | alloc_chan_resources 里调 |
| `dma_cookie_assign(tx)` | 给描述符分配下一个 cookie | 到 `INT_MAX` 后回绕到 `DMA_MIN_COOKIE`（值为 1）。kerneldoc 写明「caller is expected to hold a lock」 |
| `dma_cookie_complete(tx)` | 标记完成 | 内部 `BUG_ON(tx->cookie < DMA_MIN_COOKIE)` —— 对同一描述符调两次直接挂内核。之后把 `tx->cookie` 清零 |
| `dma_cookie_status(chan, cookie, state)` | 实现 `device_tx_status` 的标准骨架 | 先把 `residue` 和 `in_flight_bytes` 清零，再转调 `dma_async_is_complete` |
| `dma_set_residue(state, residue)` / `dma_set_in_flight_bytes(state, bytes)` | 往 `dma_tx_state` 里填数（`state` 允许为 NULL） | 配合上一个用 |
| `dmaengine_desc_get_callback(tx, cb)` | 把描述符上的回调**取出到一个局部结构** | 目的是"取的时候持锁、调的时候不持锁" |
| `dmaengine_desc_callback_invoke(cb, result)` | 调用取出来的回调 | 优先用 `callback_result`；`result` 为 NULL 时内部临时造一个 `dummy_result` |
| `dmaengine_desc_get_callback_invoke(tx, result)` | 上面两个合一 | |
| `dmaengine_desc_callback_valid(cb)` | 有没有回调可调 | |
| `dma_get_slave_channel(chan)` / `dma_get_any_slave_channel(device)` | 仅供 xlate 回调使用的通道获取 | 原型声明在私有头里。前者失败返回 **NULL 而不是 ERR_PTR** |

### §10.2 virt-dma 辅助层

> 头文件：`drivers/dma/virt-dma.h`；实现：`drivers/dma/virt-dma.c`

virt-dma 给控制器驱动提供一套**软件描述符队列 + tasklet 完成回调**的现成实现。
一条虚拟通道有**五条链表**：`desc_allocated` → `desc_submitted` → `desc_issued` →
`desc_completed` →（终止时）`desc_terminated`。

| API | 说明 | 备注 |
|---|---|---|
| `vchan_init(vc, dmadev)` | 初始化虚拟通道 | 初始化 cookie、spinlock、五条链表，`tasklet_setup(&vc->task, vchan_complete)`，并把通道挂进 `dmadev->channels` |
| `vchan_tx_prep(vc, vd, tx_flags)` | prep 回调的收尾动作 | 它把 `tx_submit` 设为 `vchan_tx_submit`、`desc_free` 设为 `vchan_tx_desc_free`，描述符入 `desc_allocated` |
| `vchan_issue_pending(vc)` | 把 submitted 链表整段挪到 issued | 返回 issued 是否非空，让驱动决定要不要启动硬件 |
| `vchan_cookie_complete(vd)` | 单次传输完成 | 入 `desc_completed` + `tasklet_schedule` |
| `vchan_cyclic_callback(vd)` | 周期传输完成一圈 | 设 `vc->cyclic = vd` + `tasklet_schedule` |
| `vchan_next_desc(vc)` | 取 issued 链表头 | 驱动在中断里推进队列用 |
| `vchan_terminate_vdesc(vd)` | 把当前描述符移到 `desc_terminated` | 配合 `device_terminate_all` |
| `vchan_synchronize(vc)` | **实现 `device_synchronize` 就靠它**：`tasklet_kill` 之后释放 `desc_terminated` | 没实现 `device_synchronize` 的驱动，消费者的 `dmaengine_synchronize` 就是空操作 |
| `vchan_get_all_descriptors(vc, head)` | 把五条链表全部摘下来 | free_chan_resources 用 |
| `vchan_free_chan_resources(vc)` / `vchan_dma_desc_free_list(vc, head)` | 释放 | |
| `vchan_find_desc(vc, cookie)` | 按 cookie 找描述符 | **只搜 `desc_issued`** —— 实现 `device_tx_status` 报残留量时要注意这个范围 |

> virt-dma 是**可选**的。`drivers/dma/pl330.c` 就完全不用它，自己管描述符池。
> 用不用它是控制器驱动的第一个架构选择，`03-internals.md` Part B 有对照。

### §10.3 `!CONFIG_DMA_ENGINE` 时的桩函数

关掉核心时，`include/linux/dmaengine.h` 提供一整套桩，**返回值语义和真实现不一样**：

| 函数 | 桩返回 |
|---|---|
| `dma_request_chan` / `dma_request_chan_by_mask` | `ERR_PTR(-ENODEV)` |
| `dma_get_slave_caps` | `-ENXIO` |
| `dmaengine_desc_attach_metadata` / `dmaengine_desc_set_metadata_len` | `-EINVAL` |
| `dmaengine_desc_get_metadata_ptr` | `ERR_PTR(-EINVAL)` |
| `dma_find_channel` | `NULL` |
| `dma_async_is_tx_complete` | `DMA_COMPLETE` |

最后一行是陷阱：**没有 DMA 引擎时"查询状态"返回的是"已完成"**。
写成"可选用 DMA"的驱动如果只判 `dma_async_is_tx_complete`，会以为传输成功了。

---

## §11 错误码语义总表

同一个错误码在不同 API 上含义不同，这张表按"你拿到了什么码"反查：

| 错误码 | 出自哪些 API | 语义 |
|---|---|---|
| **NULL**（不是 ERR_PTR） | 全部 `dmaengine_prep_*`；`dma_request_channel` 宏；`dma_request_slave_channel`；`__dma_request_channel`；`of_dma_simple_xlate` / `of_dma_xlate_by_chan_id`；`dma_get_slave_channel` | 这一族**不能用 `IS_ERR` 判断** |
| `-EPROBE_DEFER` | `dma_request_chan`、`dma_request_chan_by_mask`、`of_dma_request_slave_channel`、`of_dma_router_xlate` | 控制器还没注册，**不是错误**，原样返回给驱动模型 |
| `-ENODEV` | `dma_request_chan_by_mask`（`mask` 为 NULL）；`of_dma_request_slave_channel`（无 `dmas` 属性 / `dma-names` 不匹配）；`of_dma_match_channel`；`dma_async_device_register`（`device` 为 NULL） | 语义**分裂**：既表示"参数不对"，也表示"DTS 里根本没配" |
| `-ENOSYS` | `dmaengine_slave_config`、`dmaengine_pause`、`dmaengine_resume`、`dmaengine_terminate_all` | 统一含义：**控制器没实现这个回调**。不是"参数错" |
| `-EINVAL` | `dmaengine_terminate_async`（与 `-ENOSYS` 同条件，见 §4）；三个元数据 API 的模式冲突和桩函数；`of_dma_controller_register`（信息不全） | |
| `-ENXIO` | `dma_get_slave_caps`：无 `DMA_SLAVE`/`DMA_CYCLIC` 能力位，或 `device->directions` 为 0 | 含义是"这条通道不是从设备通道" |
| `-EPERM` | `dmaengine_desc_set_reuse`（控制器不支持复用）、`dmaengine_desc_free`（描述符没标复用） | 只出现在复用这一族 |
| `-EIO` | `dma_async_device_register` 的全部校验失败 | 只有控制器驱动作者会看到 |
| **cookie < 0** | `dmaengine_submit` | 用 `dma_submit_error()` 判断，不要直接和 0 比 |
| **`BUG()` / `BUG_ON`** | `dma_maxpq()`（参数组合非法）；`dma_cookie_complete()`（重复完成）；`txd_chain()`（未开 CHANNEL_SWITCH 时被调用） | 不返回，**直接挂内核** |

---

## §12 上下文约束速查

`Documentation/driver-api/dmaengine/client.rst` 和头文件里的 `might_sleep()` 共同定下的规则：

| API | 能否在原子上下文（中断/spinlock 内） | 依据 |
|---|---|---|
| `dma_request_chan` / `dma_release_channel` | **不能** | 会分配内存、注册 sysfs |
| 全部 `dmaengine_prep_*` | 取决于控制器实现，**保守认为不能** | 多数驱动在里面 `kzalloc(GFP_NOWAIT)` |
| `dmaengine_submit` | 能 | 只是链表操作 |
| `dma_async_issue_pending` | **能**，而且经常在中断里调 | 写寄存器 |
| `dmaengine_tx_status` | 能 | |
| `dmaengine_terminate_async` | **能** —— 这就是它存在的理由 | |
| `dmaengine_synchronize` / `dmaengine_terminate_sync` | **不能** | 显式 `might_sleep()` |
| `dma_sync_wait` | 名义上"能"，但会 `cpu_relax()` 空转最多 5 秒 | 实际上任何路径都不该这么用 |
| **回调函数本身** | **永远从 tasklet（软中断）上下文调用，绝不从硬中断** | `client.rst` 明确保证；virt-dma 的 `vchan_complete` 就是 tasklet |

> 最后一行的推论很重要：**回调里不能睡眠**（tasklet 上下文），
> 但**可以调 `spin_lock`**（不需要 `_irqsave` 那么重，不过 virt-dma 自己用的是
> `spin_lock_irq`）。想睡眠就在回调里 `complete()` 一个 completion，
> 把活儿交给工作队列。
