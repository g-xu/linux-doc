---
title: 内部实现
---

# remoteproc 实现剖析

> 内核版本 **v6.1.158**；`drivers/remoteproc/` 与 `include/linux/remoteproc.h` 在本树**未被打过补丁**。
> 目标架构 **arm64**。
> 本篇所有 `文件:行号` 都写**完整路径**，图里为了对齐不写行号 —— 行号一律在图下方的表里给。

---

## §0 导航

### §0.1 本篇只承诺一条主轴

| 分区 | 组织轴 | 章节号 |
|---|---|---|
| **Part A** | **时间线**：从 `rproc_alloc()` 到 `rproc_free()`，按代码实际执行顺序逐段读 | A1 … A10 |
| **Part B** | **横切**：锁、引用计数、资源表三态、地址转换、IOMMU、subdev、错误回滚 —— 这些贯穿整条时间线，抽出来单讲 | B1 … B7 |
| **Part C** | **评价**：源码里的设计债，以及读代码时发现的几处不对称/可疑点 | C1 … C6 |

引用时请带前缀（如「03 §A5」），这样一眼就知道是在时间线上还是在横切里。

### §0.2 分层图

```
 ┌───────────────────────────────────────────────────────────┐
 │  用户态：sysfs（state/firmware/recovery/coredump/name）    │
 │          debugfs（trace/rsc table/carveouts/crash/…）      │
 │          /dev/remoteprocN（write 固件名 + 2 个 ioctl）      │
 └───────────────────────────────────────────────────────────┘
            │                        │
            │  都不直接碰 rproc->lock，一律走下面这层 API
            ▼                        ▼
 ┌───────────────────────────────────────────────────────────┐
 │  核心 API 层：rproc_boot / rproc_shutdown / rproc_detach   │
 │              rproc_add / rproc_del / rproc_set_firmware    │
 │              状态机的临界区全封在这一层               │
 └───────────────────────────────────────────────────────────┘
            │
            ▼
 ┌───────────────────────────────────────────────────────────┐
 │  核心内部：rproc_fw_boot / rproc_start / rproc_attach      │
 │            rproc_stop / __rproc_detach                     │
 │            rproc_handle_resources（查表分派）              │
 │            rproc_alloc_registered_carveouts                │
 └───────────────────────────────────────────────────────────┘
            │  通过 rproc->ops（一份 kmemdup 副本）
            ▼
 ┌───────────────────────────────────────────────────────────┐
 │  平台驱动：qcom_q6v5_* / imx_rproc / stm32_rproc /         │
 │            ti_k3_r5_remoteproc / …（本树 34 个 .c）        │
 └───────────────────────────────────────────────────────────┘
            │
            ▼
        复位控制器 / 时钟 / IOMMU / 共享内存 / 门铃（mailbox / IPCC）
```

### §0.3 生命周期时间线（一张图先把 Part A 的骨架立住）

```
 驱动 probe
   │
   ├─ rproc_alloc()            A1   ── 分配 + device_initialize + 装 ELF 默认 ops
   │
   ├─ （可选）rproc->state = RPROC_DETACHED   ← 远端已经在跑
   │
   └─ rproc_add()              A2   ── validate → cdev → device_add → debugfs
                                        → auto_boot → 最后才挂进 rproc_list
                                             │
        ┌────────────────────────────────────┴──────────────┐
        │ 消费者 / sysfs / cdev 调 rproc_boot()        A3   │
        └────────┬─────────────────────────────┬────────────┘
                 │ state == OFFLINE            │ state == DETACHED
                 ▼                             ▼
        rproc_fw_boot()  A4              rproc_attach()  A6
            │                                 │
            └──► rproc_start()  A5            └──► __rproc_attach()
                     state = RUNNING                  state = ATTACHED
                 │                             │
                 ▼                             ▼
        rproc_shutdown()  A7             rproc_detach()  A8
            state = OFFLINE                  state = DETACHED

        任何时刻远端挂了 ──► rproc_report_crash()  A9
                              → work → state = CRASHED → 恢复

 驱动 remove
   ├─ rproc_del()               A10  ── shutdown → DELETED → 摘链 → synchronize_rcu
   └─ rproc_free() / rproc_put()     ── 放 kref
```

### §0.4 哪些内容不在本篇

| 你想找 | 去哪篇 |
|---|---|
| 每个 API 的签名、返回值、误用清单 | 01 篇 |
| 结构体字段逐个解释、`fw_rsc_*` 固件 ABI 布局 | 02 篇 |
| virtio / rpmsg 那一侧、coredump 子框架、cdev / debugfs / sysfs 三套用户接口的完整清单 | 04 篇 |
| 写一个 remoteproc 平台驱动的骨架 | 05 篇 |
| DTS 怎么写、`memory-region` / `firmware-name` 谁消费 | 06 篇 |
| dmesg 一行行对应到哪个失败点、错误码分诊 | 07 篇 |
| 「资源表到底是什么」这类概念类比 | 08 篇 |

---

# Part A：时间线

## §A1 `rproc_alloc()` —— 只做分配，不碰硬件

定义在 `drivers/remoteproc/remoteproc_core.c:2443`。做的事按顺序：

| # | 动作 | 备注 |
|---|---|---|
| 1 | 参数检查 `!dev \|\| !name \|\| !ops` | 返回 **NULL**，不是 `ERR_PTR` |
| 2 | `kzalloc(sizeof(struct rproc) + len)` | 私有数据紧跟在结构后面，`priv = &rproc[1]` |
| 3 | `auto_boot = true` | **默认是自动启动**；不想要就在 `rproc_add()` 前自己置 false |
| 4 | `elf_class = ELFCLASSNONE`、`elf_machine = EM_NONE` | 给 coredump 用，驱动可覆盖 |
| 5 | `device_initialize(&rproc->dev)` | 只 initialize，**不 add** —— add 在 `rproc_add()` |
| 6 | `dev.parent` / `dev.type` / `dev.class` / `driver_data` | 见 02 §1；`dev.type = &rproc_type` 是 `rproc_get_by_child()` 的判据 |
| 7 | `idr_init(&rproc->notifyids)` + `ida_alloc(&rproc_dev_index, …)` | `ida_alloc` 在 `drivers/remoteproc/remoteproc_core.c:2469` |
| 8 | `rproc_alloc_firmware()` | `drivers/remoteproc/remoteproc_core.c:2375`，见 §A1.1 |
| 9 | `rproc_alloc_ops()` | `drivers/remoteproc/remoteproc_core.c:2397`，见 §A1.2 |
| 10 | `dev_set_name(&rproc->dev, "remoteproc%d", rproc->index)` | 这就是 `/sys/class/remoteproc/remoteprocN` 里的 N |
| 11 | `atomic_set(&rproc->power, 0)` + `mutex_init(&rproc->lock)` | |
| 12 | **6 个** `INIT_LIST_HEAD` | carveouts / mappings / traces / rvdevs / subdevs / dump_segments |
| 13 | `INIT_WORK(&rproc->crash_handler, rproc_crash_handler_work)` | 崩溃恢复的落点，见 §A9 |
| 14 | `rproc->state = RPROC_OFFLINE` | `drivers/remoteproc/remoteproc_core.c:2500` |

出错时统一走 `put_device(&rproc->dev)` 然后 `return NULL` ——
因为第 5 步已经 `device_initialize` 过了，**不能直接 `kfree`**。
释放动作在 `rproc_type_release()`（`drivers/remoteproc/remoteproc_core.c:2353`）里，
它同时负责 `ida_free`（`drivers/remoteproc/remoteproc_core.c:2362`）。

### §A1.1 固件名可以先不给

`rproc_alloc_firmware()` 允许 `firmware == NULL`，这时它自己拼一个
`rproc-<name>-fw` 作为默认名。⇒ 驱动完全可以让 DT 的 `firmware-name`
或用户态往 `/sys/.../firmware` 写来决定加载哪份固件（06 篇 / 04 篇）。

### §A1.2 ops 是副本，而且 ELF 默认是"全有或全无"

`rproc_alloc_ops()` 的逻辑值得逐行看（`drivers/remoteproc/remoteproc_core.c:2397`）：

| 行 | 做什么 |
|---|---|
| `drivers/remoteproc/remoteproc_core.c:2399` | `kmemdup` 复制驱动那份 ops |
| `drivers/remoteproc/remoteproc_core.c:2404-2405` | `->coredump` 为空就填 `rproc_coredump` ⇒ 这个回调**永远非空** |
| `drivers/remoteproc/remoteproc_core.c:2407-2408` | **`->load` 非空就直接 return 0** |
| `drivers/remoteproc/remoteproc_core.c:2411-2415` | 否则一次性填**五个** ELF 默认实现 |

⚠️ 第三行是最容易踩的坑：**只要你填了自己的 `->load`，那五个 ELF 默认就一个都不给你**。
想只换 `load` 而留着 ELF 的 `parse_fw` / `sanity_check` / `get_boot_addr` /
`find_loaded_rsc_table`，必须**手动把它们一个个写回 ops 里**。
本树 34 个驱动里显式写 `rproc_elf_*` 的次数（01 §7 那张表）就是这条规则的直接后果。

---

## §A2 `rproc_add()` —— 挂进内核的顺序很讲究

定义在 `drivers/remoteproc/remoteproc_core.c:2269`。**六步，顺序不能换**：

| # | 行 | 动作 | 失败怎么办 |
|---|---|---|---|
| 1 | `drivers/remoteproc/remoteproc_core.c:2274` | `rproc_validate()` | 直接 return，见 §A2.1 |
| 2 | `drivers/remoteproc/remoteproc_core.c:2279` | `rproc_char_device_add()` | return |
| 3 | `drivers/remoteproc/remoteproc_core.c:2283` | `device_add()` | `put_device()` 再拆 cdev |
| 4 | `drivers/remoteproc/remoteproc_core.c:2292` | `rproc_create_debug_dir()` | 无返回值 —— debugfs 建不起来不算错误 |
| 5 | `drivers/remoteproc/remoteproc_core.c:2296` | `auto_boot` 为真则 `rproc_trigger_auto_boot()` | 拆 debugfs + `device_del` + 拆 cdev |
| 6 | `drivers/remoteproc/remoteproc_core.c:2303` | `list_add_rcu(&rproc->node, &rproc_list)`（拿 `rproc_list_mutex`） | —— |

**第 6 步在最后**，源码注释写的是 *"expose to rproc_get_by_phandle users"*。
两条推论：

| 推论 | 说明 |
|---|---|
| ⚠️ 自动启动期间，别的驱动**找不到**这个 rproc | `rproc_get_by_phandle()` 走的就是 `rproc_list`。DETACHED 的分支里 `rproc_trigger_auto_boot()` 是**同步**跑完整个 attach 的（见 §A2.2），这段时间窗口是真实存在的 |
| sysfs / debugfs / cdev 却已经能访问了 | 它们靠的是第 2/3/4 步，不看 `rproc_list` |

### §A2.1 `rproc_validate()` 只放两种状态过

`drivers/remoteproc/remoteproc_core.c:2212`，一个三分支 switch：

| 入口 state | 要求 | 不满足 |
|---|---|---|
| `RPROC_OFFLINE` | 必须有 `->start` | `-EINVAL`（`drivers/remoteproc/remoteproc_core.c:2220`） |
| `RPROC_DETACHED` | 必须有 `->attach`（`drivers/remoteproc/remoteproc_core.c:2226`），**且 `cached_table` 必须为 NULL**（`drivers/remoteproc/remoteproc_core.c:2232`） |  `-EINVAL` |
| 其它 | —— | 一律 `-EINVAL` |

⇒ 这解释了 01 §9 那句「`start` 为空时 `rproc_add()` 当场失败」：
**不是启动时才报错，是注册时就报错**。

本树里在 `rproc_add()` 之前把 state 设成 `RPROC_DETACHED` 的驱动有
`drivers/remoteproc/imx_rproc.c`、`drivers/remoteproc/stm32_rproc.c`、
`drivers/remoteproc/ti_k3_dsp_remoteproc.c`、
`drivers/remoteproc/ti_k3_r5_remoteproc.c` —— 全是 arm/arm64 平台，
这条路径在 arm64 上是主流用法，不是边角料。

### §A2.2 auto_boot 有两条完全不同的路

`rproc_trigger_auto_boot()`（`drivers/remoteproc/remoteproc_core.c:1679`）：

| state | 做法 | 同步/异步 |
|---|---|---|
| `RPROC_DETACHED` | 直接 `return rproc_boot(rproc)` | **同步**，因为远端已经在跑，等固件毫无意义 |
| 其它 | `request_firmware_nowait(…, rproc_auto_boot_callback)`（`drivers/remoteproc/remoteproc_core.c:1696`） | **异步**，回调里再 `rproc_boot()` |

异步那条路的理由源码注释写得很直白：这样 remoteproc 可以编进内核而不卡住启动流程。
代价是 `rproc_add()` 返回时远端**还没起来**，而且
`rproc_auto_boot_callback()`（`drivers/remoteproc/remoteproc_core.c:1670`）
**丢掉了 `rproc_boot()` 的返回值** —— 自动启动失败只在 dmesg 里留痕，
`rproc_add()` 照样返回 0。

---

## §A3 `rproc_boot()` —— 分岔点

定义在 `drivers/remoteproc/remoteproc_core.c:1907`。

```
 rproc_boot()
   │
   ├─ mutex_lock_interruptible(&rproc->lock)   ← 被信号打断就返回
   │
   ├─ state == RPROC_DELETED ?  → -ENODEV
   │
   ├─ atomic_inc_return(&power) > 1 ?  → 已经开着，返回 0
   │
   ├─ state == RPROC_DETACHED ?
   │     是 → rproc_attach()                    §A6
   │     否 → request_firmware() → rproc_fw_boot() → release_firmware()   §A4
   │
   ├─ 失败则 atomic_dec(&power)
   └─ mutex_unlock
```

| 动作 | 行 |
|---|---|
| `RPROC_DELETED` 检查 | `drivers/remoteproc/remoteproc_core.c:1926` |
| `atomic_inc_return(&rproc->power) > 1` 提前返回 | `drivers/remoteproc/remoteproc_core.c:1933` |
| DETACHED 分岔 → `rproc_attach()` | `drivers/remoteproc/remoteproc_core.c:1941` |
| `request_firmware()` | `drivers/remoteproc/remoteproc_core.c:1946` |
| `rproc_fw_boot()` | `drivers/remoteproc/remoteproc_core.c:1952` |
| 失败回滚 `atomic_dec()` | `drivers/remoteproc/remoteproc_core.c:1959` |

**计数是先加后判**：`atomic_inc_return(...) > 1` 意味着第二个使用者进来时
计数已经是 2，直接返回 0 —— 这就是 01 §1 说的「引用计数式启动」。
⚠️ 注意固件是**每次 boot 都重新 `request_firmware()`**，
用完立刻 `release_firmware()`，核心**不缓存固件镜像**（缓存的是资源表，见 §B3）。

---

## §A4 `rproc_fw_boot()` —— 加载路径的主干

定义在 `drivers/remoteproc/remoteproc_core.c:1375`。**八步**，每一步失败都有对应的回滚标签：

| # | 动作 | 行 | 失败跳到 |
|---|---|---|---|
| 1 | `rproc_fw_sanity_check()` → `ops->sanity_check` | `drivers/remoteproc/remoteproc_core.c:1382` | 直接 return（还什么都没做） |
| 2 | `dev_info` 打一行 `Booting fw image %s, size %zd` | `drivers/remoteproc/remoteproc_core.c:1386` | —— |
| 3 | `rproc_enable_iommu()` | `drivers/remoteproc/remoteproc_core.c:1392` | return，见 §B5 |
| 4 | `rproc_prepare_device()` → `ops->prepare` | `drivers/remoteproc/remoteproc_core.c:1399` | `disable_iommu` |
| 5 | `rproc->bootaddr = rproc_get_boot_addr()` | `drivers/remoteproc/remoteproc_core.c:1405` | **无返回值检查**，见 §C5 |
| 6 | `rproc_parse_fw()` → `ops->parse_fw`（取资源表） | `drivers/remoteproc/remoteproc_core.c:1408` | `unprepare_rproc` |
| 7 | `max_notifyid = -1`、`nb_vdev = 0` 复位 | `drivers/remoteproc/remoteproc_core.c:1414`、`drivers/remoteproc/remoteproc_core.c:1417` | —— |
| 8 | `rproc_handle_resources(rproc, rproc_loading_handlers)` | `drivers/remoteproc/remoteproc_core.c:1418` | `clean_up_resources` |
| 9 | `rproc_alloc_registered_carveouts()` | `drivers/remoteproc/remoteproc_core.c:1425` | `clean_up_resources` |
| 10 | `rproc_start()` | `drivers/remoteproc/remoteproc_core.c:1432` | `clean_up_resources` |

第 7 步的两个复位是**幂等性保证**：崩溃恢复会把整条路再走一遍，
notifyid 和 vdev 计数必须从零开始，否则第二次启动会分配到不同的 notifyid。

`clean_up_resources` 标签（`drivers/remoteproc/remoteproc_core.c:1438-1442`）做四件事：
`rproc_resource_cleanup()` → `kfree(cached_table)` → `cached_table = NULL` →
`table_ptr = NULL`，然后继续往下 fall through 到 `unprepare_rproc` 和 `disable_iommu`。
**这是一个标准的 goto 阶梯**，回滚顺序恰好是初始化顺序的逆序 —— 见 §B7。

### §A4.1 `rproc_handle_resources()` —— 查表分派

`drivers/remoteproc/remoteproc_core.c:1018`。循环体里的判断顺序是：

```
 for i in 0 .. table_ptr->num-1:
     hdr   = table_ptr + offset[i]
     avail = table_sz - offset - sizeof(hdr)

     avail < 0                          → "rsc table is truncated"  -EINVAL
     RSC_VENDOR_START <= type <= END     → ops->handle_rsc（厂商自定义）
         返回 RSC_HANDLED  → continue
         返回 < 0          → break
         其它（RSC_IGNORED）→ "unsupported vendor resource %d" + continue
     type >= RSC_LAST                    → "unsupported resource %d" + continue
     handlers[type] == NULL              → continue
     否则                                → handlers[type](rproc, rsc, offset, avail)
                                            返回非 0 → break
```

| 关键点 | 行 |
|---|---|
| `table_ptr` 为 NULL 直接返回 0 | `drivers/remoteproc/remoteproc_core.c:1025` |
| 截断检查 | `drivers/remoteproc/remoteproc_core.c:1035` |
| 厂商资源分派 | `drivers/remoteproc/remoteproc_core.c:1044` |
| 未知类型只 warn 不 fail | `drivers/remoteproc/remoteproc_core.c:1055`、`drivers/remoteproc/remoteproc_core.c:1059` |
| 处理表本体 | `drivers/remoteproc/remoteproc_core.c:1010`，四个条目在 `drivers/remoteproc/remoteproc_core.c:1011-1014` |

**`avail` 这个参数是整个资源表解析的安全边界**：它等于"从这条资源项的
payload 开始，到资源表末尾还剩多少字节"。四个 handler 每一个都要拿它和
`sizeof(*rsc)` 比一次，否则一份恶意/损坏的固件就能读越界。
⇒ 这是 02 §3 说「资源表是不可信输入」的落地处。

⚠️ 这个循环有一个真实的返回值缺陷（厂商资源 + `RSC_IGNORED` + 恰好是最后一条），
详见 **§C2**。

### §A4.2 `rproc_alloc_registered_carveouts()` —— 延迟分配 + 回写资源表

`drivers/remoteproc/remoteproc_core.c:1152`。遍历 `rproc->carveouts`，对每个条目：

| 动作 | 行 | 说明 |
|---|---|---|
| `entry->alloc` 非空就调 | `drivers/remoteproc/remoteproc_core.c:1161` | 失败**一律改写成 `-ENOMEM`**（`drivers/remoteproc/remoteproc_core.c:1165`），驱动的原始错误码丢了 |
| `rsc_offset != FW_RSC_ADDR_ANY` 才回写 | `drivers/remoteproc/remoteproc_core.c:1170` | 只有真的来自资源表的 carveout 才回写 |
| `pa` 取值：`entry->va` 有就 `rproc_va_to_pa(va)`，否则用 `entry->dma` | `drivers/remoteproc/remoteproc_core.c:1193-1196` | |
| 高 32 位非零就 `dev_warn` | `drivers/remoteproc/remoteproc_core.c:1198` | ⚠️ arm64 相关，见 §C5 |
| 回写 `rsc->pa` / `rsc->da` / `rsc->len` | `drivers/remoteproc/remoteproc_core.c:1202-1204` | **往共享内存里写**，固件就是这样知道内核给它分了哪块内存的 |

源码里有一大段注释解释"为什么明知不安全还要把物理地址告诉远端"
（OMAP4 的 M3 要用 pa 去控制不在 IOMMU 后面的加速器）。
这段注释本身就是设计意图的一手记录，值得读一遍。

---

## §A5 `rproc_start()` —— 真正放复位的那一步

定义在 `drivers/remoteproc/remoteproc_core.c:1265`。**五步**：

| # | 动作 | 行 |
|---|---|---|
| 1 | `rproc_load_segments()` → `ops->load`（把固件搬进内存） | `drivers/remoteproc/remoteproc_core.c:1271` |
| 2 | `rproc_find_loaded_rsc_table()` 找到**远端内存里**那份资源表，把 `cached_table` **memcpy 过去**，然后 `table_ptr` 改指向它 | `drivers/remoteproc/remoteproc_core.c:1286-1290` |
| 3 | `rproc_prepare_subdevices()` → 每个 subdev 的 `prepare` | `drivers/remoteproc/remoteproc_core.c:1292` |
| 4 | `rproc->ops->start(rproc)` —— **远端从这一行开始跑** | `drivers/remoteproc/remoteproc_core.c:1300` |
| 5 | `rproc_start_subdevices()` → 每个 subdev 的 `start`（含注册 virtio 设备） | `drivers/remoteproc/remoteproc_core.c:1306` |
| 6 | `rproc->state = RPROC_RUNNING` + `dev_info` 打 `remote processor %s is now up` | `drivers/remoteproc/remoteproc_core.c:1314`、`drivers/remoteproc/remoteproc_core.c:1316` |

**第 2 步是整个 remoteproc 最关键的一次"交底"**。在它之前，
所有 carveout 地址、vring 地址都只写在内核自己的 `cached_table` 里；
第 2 步把这份表整体 memcpy 到远端能看见的内存，远端复位一放就能读到。
之后 `table_ptr` 指向的是**共享内存**，任何后续修改（如 virtio 的
`status` 回写）都直接落到远端眼前 —— 这就是 02 §2.4 三个表指针的分工由来。

第 2 步是**有条件的**：`rproc_find_loaded_rsc_table()` 返回 NULL 就整段跳过
（固件里没有资源表是合法的）。

回滚阶梯（`drivers/remoteproc/remoteproc_core.c:1320-1325`）：
`stop_rproc:` 调 `ops->stop` → `unprepare_subdevices:` → `reset_table_ptr:`
把 `table_ptr` 还原成 `cached_table`。
⚠️ 注意 `stop_rproc` 标签里是**无条件** `rproc->ops->stop(rproc)`，
没有查 NULL —— 但这里安全，因为能走到 `rproc_start()` 说明
`rproc_validate()` 已经确认过 `->start` 存在；⚠️ 只提供 `start` 而不提供 `stop`
的驱动在这条路上会 NULL 解引用。本树没有这样的驱动（01 §9 的"最小可用集合"
就包含 `stop`），但核心确实没有防线。

---

## §A6 attach 路径：`rproc_attach()`

定义在 `drivers/remoteproc/remoteproc_core.c:1593`。和 §A4 的 `rproc_fw_boot()`
**几乎一一对应，但少了所有和固件镜像有关的步骤**：

| `rproc_fw_boot()` | `rproc_attach()` | 差别 |
|---|---|---|
| `rproc_fw_sanity_check()` | **没有** | 没有固件镜像可查 |
| `rproc_enable_iommu()` | 有（`drivers/remoteproc/remoteproc_core.c:1602`） | 相同 |
| `rproc_prepare_device()` | 有（`drivers/remoteproc/remoteproc_core.c:1609`） | 相同 |
| `rproc_get_boot_addr()` | **没有** | 远端已经在跑 |
| `rproc_parse_fw()` | 换成 `rproc_set_rsc_table()`（`drivers/remoteproc/remoteproc_core.c:1615`） | 见 §B3 |
| `max_notifyid = -1`、`nb_vdev = 0` | 有（`drivers/remoteproc/remoteproc_core.c:1622`、`drivers/remoteproc/remoteproc_core.c:1625`） | 相同 |
| `rproc_handle_resources()` | 有（`drivers/remoteproc/remoteproc_core.c:1632`） | 注释明确要求平台驱动**自己把 `table_ptr` 设好** |
| `rproc_alloc_registered_carveouts()` | 有（`drivers/remoteproc/remoteproc_core.c:1639`） | 相同 |
| `rproc_start()` | 换成 `__rproc_attach()`（`drivers/remoteproc/remoteproc_core.c:1646`） | |

`__rproc_attach()`（`drivers/remoteproc/remoteproc_core.c:1330`）对比 `rproc_start()`：

| `rproc_start()` | `__rproc_attach()` |
|---|---|
| `rproc_load_segments()` | 无（不搬固件） |
| memcpy `cached_table` → 远端 | 无（远端那份表本来就是活的） |
| `rproc_prepare_subdevices()` | 有（`drivers/remoteproc/remoteproc_core.c:1334`） |
| `ops->start()` | 换成 `rproc_attach_device()` → `ops->attach`（`drivers/remoteproc/remoteproc_core.c:1341`） |
| `rproc_start_subdevices()` | 有（`drivers/remoteproc/remoteproc_core.c:1350`） |
| `state = RPROC_RUNNING` | `state = RPROC_ATTACHED`（`drivers/remoteproc/remoteproc_core.c:1358`） |

两者的回滚阶梯都用 `rproc->ops->stop(rproc)` 收尾
（`__rproc_attach()` 里在 `drivers/remoteproc/remoteproc_core.c:1364`）——
⚠️ 也就是说**只写 `attach`/`detach` 而不写 `stop` 的驱动，在 attach 失败的回滚路上会崩**。
这是 01 §9 里 `stop` 被划进"最小可用集合"的第二个理由。

---

## §A7 停止路径：`rproc_shutdown()` → `rproc_stop()`

`rproc_shutdown()`（`drivers/remoteproc/remoteproc_core.c:1987`）：

| # | 动作 | 行 |
|---|---|---|
| 1 | 拿 `rproc->lock` | —— |
| 2 | `state` 既不是 `RUNNING` 也不是 `ATTACHED` → `-EINVAL` | `drivers/remoteproc/remoteproc_core.c:1998-1999` |
| 3 | `atomic_dec_and_test(&power)` 不为真 → 还有别的使用者，直接返回 | `drivers/remoteproc/remoteproc_core.c:2005` |
| 4 | `rproc_stop(rproc, false)`；失败则 `atomic_inc()` 把计数补回来 | `drivers/remoteproc/remoteproc_core.c:2008-2011` |
| 5 | `rproc_resource_cleanup()` | `drivers/remoteproc/remoteproc_core.c:2015` |
| 6 | `rproc_unprepare_device()` → `ops->unprepare` | `drivers/remoteproc/remoteproc_core.c:2018` |
| 7 | `rproc_disable_iommu()` | `drivers/remoteproc/remoteproc_core.c:2020` |
| 8 | `kfree(cached_table)`，三个字段清零：`cached_table` / `table_ptr` / `table_sz` | `drivers/remoteproc/remoteproc_core.c:2023-2026` |

`rproc_stop()`（`drivers/remoteproc/remoteproc_core.c:1705`）：

| # | 动作 | 行 |
|---|---|---|
| 1 | `!rproc->ops->stop` → `-EINVAL`（**这里查了 NULL**） | `drivers/remoteproc/remoteproc_core.c:1711` |
| 2 | `rproc_stop_subdevices(rproc, crashed)` | `drivers/remoteproc/remoteproc_core.c:1715` |
| 3 | `rproc_reset_rsc_table_on_stop()` | `drivers/remoteproc/remoteproc_core.c:1718` |
| 4 | `rproc->ops->stop(rproc)` | `drivers/remoteproc/remoteproc_core.c:1726` |
| 5 | `rproc_unprepare_subdevices()` | `drivers/remoteproc/remoteproc_core.c:1732` |
| 6 | `state = RPROC_OFFLINE` + `dev_info` 打 `stopped remote processor %s` | `drivers/remoteproc/remoteproc_core.c:1734`、`drivers/remoteproc/remoteproc_core.c:1736` |

**`crashed` 这个布尔值只往下传到 `rproc_stop_subdevices()`**
（`drivers/remoteproc/remoteproc_core.c:1121`）—— subdev 的 `stop` 回调据此
知道"这次是正常关机还是善后"，正常关机可以和远端握手，崩溃后不能。
`rproc_shutdown()` 传 false，崩溃恢复传 true（§A9）。

⚠️ **停止顺序和启动顺序不完全镜像**：启动是
`prepare_subdevices → ops->start → start_subdevices`，
停止是 `stop_subdevices → ops->stop → unprepare_subdevices`。
subdev 的 prepare/unprepare 对称，start/stop 对称，
但 `ops->stop` 夹在 subdev 的 stop 和 unprepare 中间 —— **这是对的**：
subdev（virtio/rpmsg）必须在远端断电**之前**停掉，否则会去访问已经死掉的共享内存。

---

## §A8 detach 路径：`rproc_detach()` → `__rproc_detach()`

`rproc_detach()`（`drivers/remoteproc/remoteproc_core.c:2053`）和 `rproc_shutdown()`
结构完全一样，三处不同：

| | `rproc_shutdown()` | `rproc_detach()` |
|---|---|---|
| 允许的入口 state | `RUNNING` **或** `ATTACHED` | 只有 `ATTACHED`（`drivers/remoteproc/remoteproc_core.c:2064`） |
| 计数没归零时的返回值 | 直接 `goto out`（`ret` 保持进来时的值） | `ret = 0` 后 `goto out`（`drivers/remoteproc/remoteproc_core.c:2070-2071`） |
| 收尾清零的字段 | `cached_table` / `table_ptr` / **`table_sz`** | ⚠️ 只清前两个（`drivers/remoteproc/remoteproc_core.c:2091`），**`table_sz` 留着**，见 §C4 |

`__rproc_detach()`（`drivers/remoteproc/remoteproc_core.c:1744`）对比 `rproc_stop()`：

| `rproc_stop()` | `__rproc_detach()` |
|---|---|
| 查 `ops->stop` | 查 `ops->detach`（`drivers/remoteproc/remoteproc_core.c:1750`） |
| `rproc_stop_subdevices(rproc, crashed)` | `rproc_stop_subdevices(rproc, false)`（`drivers/remoteproc/remoteproc_core.c:1754`）**永远 false**，detach 不是崩溃 |
| `rproc_reset_rsc_table_on_stop()` | `rproc_reset_rsc_table_on_detach()`（`drivers/remoteproc/remoteproc_core.c:1757`），见 §B3 |
| `ops->stop()` | `ops->detach()`（`drivers/remoteproc/remoteproc_core.c:1764`） |
| `state = RPROC_OFFLINE` | `state = RPROC_DETACHED`（`drivers/remoteproc/remoteproc_core.c:1772`） |

**detach 的语义是"内核撒手，远端继续跑"**。源码在 `rproc_detach()`
的注释里说明了目的：detach 之后可以把平台驱动卸掉、甚至给应用处理器断电重启，
远端照样不受影响。⇒ 这是 02 §10 强调 `ATTACHED` 与 `RUNNING` 区别的实际用途。

---

## §A9 崩溃与恢复

### §A9.1 三条崩溃入口

| 入口 | 位置 | 崩溃类型 |
|---|---|---|
| IOMMU 页错误 | `drivers/remoteproc/remoteproc_core.c:84`，回调里 `rproc_report_crash(rproc, RPROC_MMUFAULT)` | `RPROC_MMUFAULT` |
| 驱动自己检测（看门狗、fatal 中断） | 驱动里调 `rproc_report_crash()` | `RPROC_WATCHDOG` / `RPROC_FATAL_ERROR` |
| 用户态手动注入 | debugfs 的 `crash` 文件 | 04 篇 |

`rproc_report_crash()`（`drivers/remoteproc/remoteproc_core.c:2677`）只做三件事：

| # | 动作 | 行 |
|---|---|---|
| 1 | `rproc` 为 NULL 就 `pr_err("NULL rproc pointer")` 后返回 | —— |
| 2 | `pm_stay_awake(rproc->dev.parent)` —— 恢复期间禁止系统挂起 | `drivers/remoteproc/remoteproc_core.c:2685` |
| 3 | `queue_work(rproc_recovery_wq, &rproc->crash_handler)` | `drivers/remoteproc/remoteproc_core.c:2690` |

函数注释明确写了 *"can be called from atomic/interrupt context"* ——
这就是为什么必须转成 work：恢复要 `request_firmware()`，会睡。

### §A9.2 `rproc_crash_handler_work()`

`drivers/remoteproc/remoteproc_core.c:1862`：

```
 拿 rproc->lock
   state == RPROC_CRASHED ?  → 解锁，直接 return   只处理第一次崩溃
   state == RPROC_OFFLINE ?  → 解锁，goto out      已经关机了就别救
   state = RPROC_CRASHED
   dev_err("handling crash #%u in %s", ++crash_cnt, name)
 解锁
   recovery_disabled 为假 → rproc_trigger_recovery()
 out:
   pm_relax(dev.parent)
```

| 动作 | 行 |
|---|---|
| 已是 CRASHED 的早退 | `drivers/remoteproc/remoteproc_core.c:1871` |
| 置 CRASHED + 计数递增 | `drivers/remoteproc/remoteproc_core.c:1883-1884` |
| `recovery_disabled` 判断 | `drivers/remoteproc/remoteproc_core.c:1889` |
| `pm_relax` | `drivers/remoteproc/remoteproc_core.c:1893` |

⚠️ 第一个早退分支是 `return`，**不是 `goto out`** ⇒ 那一次的 `pm_relax()`
不会执行。详见 §C3。

`recovery_disabled` 为真时**状态就停在 `CRASHED`** —— 这正是
`/sys/.../recovery` 写 `recover` 能救回来的原理：`rproc_trigger_recovery()`
一进门就查 `state != RPROC_CRASHED` 则直接放行退出
（`drivers/remoteproc/remoteproc_core.c:1840`）。

### §A9.3 `rproc_trigger_recovery()` 的两条恢复路

`drivers/remoteproc/remoteproc_core.c:1830`。拿 `rproc->lock` 后按 feature 位分岔：

| 条件 | 走哪条 | 做什么 |
|---|---|---|
| 有 `RPROC_FEAT_ATTACH_ON_RECOVERY` | `rproc_attach_recovery()`（`drivers/remoteproc/remoteproc_core.c:1779`） | `__rproc_detach()` 然后 `__rproc_attach()` —— **不重新加载固件，也不出 coredump** |
| 否则 | `rproc_boot_recovery()`（`drivers/remoteproc/remoteproc_core.c:1790`） | `rproc_stop(rproc, **true**)` → `ops->coredump()` → `request_firmware()` → `rproc_start()` |

| 动作 | 行 |
|---|---|
| `state != RPROC_CRASHED` 就放行退出 | `drivers/remoteproc/remoteproc_core.c:1840` |
| feature 位判断 | `drivers/remoteproc/remoteproc_core.c:1845` |
| coredump 调用 | `drivers/remoteproc/remoteproc_core.c:1801` |
| 重新 `request_firmware()` | `drivers/remoteproc/remoteproc_core.c:1804` |

**coredump 只在 `rproc_boot_recovery()` 这一条路上出**，而且是
`rproc_stop()` **之后**、重新加载**之前** —— 时机很讲究：远端已经停住（内存不再变化），
但固件还没被覆盖，这时候抓的转储才有意义。
⇒ `ops->coredump` 由 §A1.2 保证非空，所以这里没查 NULL 是安全的。

⚠️`rproc_trigger_recovery()` 用的是 `mutex_lock_interruptible()`，
**恢复动作可以被信号打断**（当它是从 sysfs/debugfs 的写入路径进来时）。
从 work 里进来时没有信号，不受影响。

---

## §A10 拆除：`rproc_del()` 与 `rproc_free()`

`rproc_del()`（`drivers/remoteproc/remoteproc_core.c:2556`）：

| # | 动作 | 行 | 备注 |
|---|---|---|---|
| 1 | `!rproc` → `-EINVAL` | —— | |
| 2 | `rproc_shutdown(rproc)` | `drivers/remoteproc/remoteproc_core.c:2562` | ⚠️ **返回值被丢掉**，而且上面一行就是源码自己的 `TODO`（§C1） |
| 3 | 拿 `rproc->lock`，`state = RPROC_DELETED` | `drivers/remoteproc/remoteproc_core.c:2565` | 之后任何 `rproc_boot()` 都会拿到 `-ENODEV`（§A3） |
| 4 | `rproc_delete_debug_dir()` | —— | |
| 5 | 拿 `rproc_list_mutex`，`list_del_rcu()` | `drivers/remoteproc/remoteproc_core.c:2571-2573` | |
| 6 | `synchronize_rcu()` | `drivers/remoteproc/remoteproc_core.c:2576` | 等所有 RCU 读者退出，见 §B1 |
| 7 | `device_del(&rproc->dev)` | —— | 只是 del，**不 put** |
| 8 | `rproc_char_device_remove()` | —— | |

**`rproc_del()` 不释放内存**。释放要靠 `rproc_free()` 或 `rproc_put()`
把最后一个 kref 放掉，然后 `rproc_type_release()`
（`drivers/remoteproc/remoteproc_core.c:2353`）才跑。
⇒ 这就是 01 §3 那张两重引用表的另一半：**驱动 remove 里必须
`rproc_del()` + `rproc_free()` 成对出现**（或者用 `devm_rproc_add()` /
`devm_rproc_alloc()` 让 devres 替你做）。

⚠️ 第 2 步的隐患是真实的：如果一个驱动只实现 `attach`/`detach` 而没有 `stop`，
`rproc_shutdown()` → `rproc_stop()` 在 `drivers/remoteproc/remoteproc_core.c:1711`
返回 `-EINVAL`，`rproc_del()` 照样往下走，**远端就一直跑着**，
而内核这边的 rproc 已经进入 `RPROC_DELETED`。
这不算 bug（detach 的语义本来就是"让远端继续跑"），但读代码时要知道
**`rproc_del()` 不保证远端停下来**。

---

# Part B：横切

## §B1 两把锁 + 一个 RCU

| 锁 | 范围 | 位置 |
|---|---|---|
| `rproc->lock`（mutex） | 单个远端的**状态机** | 声明 `include/linux/remoteproc.h:560`；加锁动作**只出现在** `drivers/remoteproc/remoteproc_core.c` 里，共 7 处 |
| `rproc_list_mutex` | 全局 `rproc_list` 的**写**端 | `drivers/remoteproc/remoteproc_core.c:46` |
| RCU 读侧 | `rproc_list` 的**读**端 | `drivers/remoteproc/remoteproc_core.c:2122`（`rproc_get_by_phandle`）、`drivers/remoteproc/remoteproc_core.c:2702`（panic 处理） |

### §B1.1 为什么 `rproc_list` 非 RCU 不可

panic 通知回调必须遍历这条链表（要给每个远端一次 flush cache 的机会），
而 panic 上下文**不能睡** ⇒ 拿不了 mutex ⇒ 读端必须无锁。
`rproc_del()` 因此必须在摘链后 `synchronize_rcu()`
（`drivers/remoteproc/remoteproc_core.c:2576`），源码注释就一句
*"Ensure that no readers of rproc_list are still active"*。

⚠️ RCU 只保证"节点在读临界区内不被释放"，**不保证对象逻辑上还有效**。
所以 `rproc_get_by_phandle()` 在 RCU 临界区里还要额外做
`try_module_get()` + `get_device()`（01 §3）—— 少任何一个都不安全。

### §B1.2 `rproc->lock` 的三个特点

| 特点 | 后果 |
|---|---|
| 只有核心的公开 API 会拿它 | sysfs / cdev / debugfs 一律通过 `rproc_boot()` 等 API 间接加锁，**不自己碰** |
| 用的是 `mutex_lock_interruptible()` | 可被信号打断；`rproc_set_firmware()` 把这个错误码压成 `-EINVAL`（01 §10.2） |
| `rproc_crash_handler_work()` 在置 `CRASHED` 后**先解锁**再去恢复 | 恢复函数 `rproc_trigger_recovery()` 自己再拿一次锁（`drivers/remoteproc/remoteproc_core.c:1830`），所以中间存在一个"状态是 CRASHED 但没人持锁"的窗口 —— 这正是 `state != RPROC_CRASHED` 复查（`drivers/remoteproc/remoteproc_core.c:1840`）存在的原因 |

## §B2 两套计数

| 计数 | 类型 | 语义 | 谁加 / 谁减 |
|---|---|---|---|
| `rproc->power` | `atomic_t`（`include/linux/remoteproc.h:557`） | **有几个使用者要求远端在跑** | `rproc_boot()` 加，`rproc_shutdown()` / `rproc_detach()` 减 |
| `rproc->dev` 的 kref | `struct device`（`include/linux/remoteproc.h:556`） | **rproc 这个对象还能不能被访问** | `get_device()` / `put_device()`；`rproc_get_by_phandle()` 加，`rproc_put()` / `rproc_free()` 减 |

两者**完全独立**：`rproc_shutdown()` 的注释专门说明"我们只减 power，不减对象引用，
所以 handle 在 shutdown 之后仍然有效，还能再 `rproc_boot()`"。

⚠️ **失败回滚必须手动补计数**：
`rproc_boot()` 在 `drivers/remoteproc/remoteproc_core.c:1959` 做 `atomic_dec()`，
`rproc_shutdown()` 在 `drivers/remoteproc/remoteproc_core.c:2010`、
`rproc_detach()` 在 `drivers/remoteproc/remoteproc_core.c:2077` 做 `atomic_inc()`。
这三处是"先动计数后干活"的必然代价。

## §B3 资源表的三个指针 —— 本子系统最难的一块

三个字段（02 §2.4）在四条路径上的取值：

| 路径 | `table_ptr` 指向 | `cached_table` | `clean_table` |
|---|---|---|---|
| **boot**：`ops->parse_fw` 之后 | 内核自己 kmalloc 的那份（= `cached_table`） | 非 NULL | NULL |
| **boot**：`rproc_start()` memcpy 之后 | **远端共享内存里的那份** | 还留着（原样） | NULL |
| **attach**：`rproc_set_rsc_table()` 之后 | **远端共享内存里那份（本来就存在）** | **NULL** | 有 `->detach` 才 kmemdup 一份，否则 NULL |
| **stop / detach**：reset 之后 | 指回内核那份副本 | 非 NULL | 已 `kfree` |

### §B3.1 `rproc_set_rsc_table()`（attach 用）

`drivers/remoteproc/remoteproc_core.c:1451`：

| 情况 | 做法 |
|---|---|
| `rproc_get_loaded_rsc_table()` 返回 NULL | **直接 return 0** —— 源码注释：*"Not having a resource table is acceptable"* |
| 返回 `ERR_PTR` | 打 `can't load resource table: %d` 后返回该错误码 |
| 驱动有 `->detach` | `clean_table = kmemdup(table_ptr, table_sz)`（`drivers/remoteproc/remoteproc_core.c:1481`） |
| 驱动没有 `->detach` | `clean_table = NULL`（`drivers/remoteproc/remoteproc_core.c:1485`） |
| 收尾 | `cached_table = NULL`（`drivers/remoteproc/remoteproc_core.c:1488`）、`table_ptr = table_ptr`、`table_sz = table_sz` |

**`clean_table` 只为"可以反复 detach/attach"而存在**。
源码里画了目标状态序列 `DETACHED -> ATTACHED -> DETACHED -> ATTACHED`：
每次 attach 都要看到一份**未被上一轮改过**的资源表，所以必须在第一次 attach 时
就把原始内容留一份底。

### §B3.2 `rproc_reset_rsc_table_on_detach()`

`drivers/remoteproc/remoteproc_core.c:1495`，四步：

| # | 动作 | 行 |
|---|---|---|
| 1 | `table_ptr` 为 NULL → 返回 0（从来没取到过表） | `drivers/remoteproc/remoteproc_core.c:1500` |
| 2 | `WARN_ON(!clean_table)` → `-EINVAL`（走到这里 `clean_table` **必须**存在） | `drivers/remoteproc/remoteproc_core.c:1508` |
| 3 | `cached_table = kmemdup(table_ptr, table_sz)`，然后 `table_ptr = cached_table` | `drivers/remoteproc/remoteproc_core.c:1524-1531` |
| 4 | `memcpy(远端那份, clean_table, table_sz)` —— **把远端内存恢复原样**，然后 `kfree(clean_table)` | `drivers/remoteproc/remoteproc_core.c:1537`、`drivers/remoteproc/remoteproc_core.c:1543` |

第 4 步是这一族函数存在的全部理由：**detach 之后远端还在跑**，
它眼前那份资源表必须回到"内核从没插手过"的状态，
否则下一次 attach 时内核会把自己上一轮写进去的 vring 地址当成固件给的初值。

### §B3.3 `rproc_reset_rsc_table_on_stop()`

`drivers/remoteproc/remoteproc_core.c:1548`。比 detach 版**简单得多**，因为远端要断电了，
它眼前的内存内容无所谓：

| 情况 | 做法 |
|---|---|
| `table_ptr` 为 NULL | 返回 0 |
| `cached_table` 已存在（说明是**内核启动**的） | `drivers/remoteproc/remoteproc_core.c:1560` 直接 `goto out` |
| `cached_table` 不存在（说明是**别人启动**的，走 attach 进来的） | kmemdup 一份（`drivers/remoteproc/remoteproc_core.c:1570`）+ `kfree(clean_table)`（`drivers/remoteproc/remoteproc_core.c:1578`） |
| `out:` | `table_ptr = cached_table` |

⇒ **`cached_table` 是否为空，就是"这个远端是谁启动的"的判据**。
这条判据在源码注释里被反复使用，是读这一族函数的钥匙。

## §B4 地址转换与 carveout

### §B4.1 `rproc_da_to_va()` 是"先问驱动，再查表"

`drivers/remoteproc/remoteproc_core.c:193`：

```
 ops->da_to_va 存在 ?
     → 调用它；返回非 NULL 就用它的结果（goto out）
     → 返回 NULL 则继续往下

 遍历 rproc->carveouts：
     carveout->va 为 NULL        → 跳过（还没分配）
     offset = da - carveout->da
     offset < 0                   → 跳过
     offset + len > carveout->len → 跳过
     命中 → ptr = carveout->va + offset，顺便回填 *is_iomem
```

| 关键点 | 行 |
|---|---|
| 驱动回调优先 | `drivers/remoteproc/remoteproc_core.c:198-199` |
| 未分配的 carveout 跳过 | `drivers/remoteproc/remoteproc_core.c:207` |
| 上下界都查 | `drivers/remoteproc/remoteproc_core.c:211`、`drivers/remoteproc/remoteproc_core.c:215` |
| `is_iomem` 回填 | `drivers/remoteproc/remoteproc_core.c:220-221` |

**"先问驱动"这个顺序很重要**：驱动的 `da_to_va` 通常处理的是
"远端内部 TCM / SRAM"这类不在 carveout 列表里的地址；
carveout 列表兜的是"内核替固件分配的那些 DDR 块"。
⚠️ 驱动返回 NULL **不是错误**，只是"我不管这个地址"，核心会继续查列表。

⚠️`int offset = da - carveout->da;` —— `da` 是 `u64` 而 `offset` 是 `int`。
超过 2 GiB 的偏移会溢出。实际不会触发（单个 carveout 没这么大），但这行代码
本身是脆的；同一行的 `offset + len > carveout->len` 还混用了 `int` 和 `size_t`。

### §B4.2 `rproc_find_carveout_by_name()` / `rproc_check_carveout_da()`

| 函数 | 位置 | 做什么 |
|---|---|---|
| `rproc_find_carveout_by_name()` | `drivers/remoteproc/remoteproc_core.c:252` | 支持格式化字符串（`__printf(2,3)`），内部 `vsnprintf` 到一个 **32 字节**的栈缓冲后 `strcmp` |
| `rproc_check_carveout_da()` | `drivers/remoteproc/remoteproc_core.c:290` | 校验（da, len）落在某个已注册 carveout 内 |

`rproc_check_carveout_da()` 的三分支逻辑值得记：

| 请求 `da` | carveout 的 `mem->da` | 结果 |
|---|---|---|
| 任意 | —— | 先查 `len > mem->len` → `-EINVAL`（`drivers/remoteproc/remoteproc_core.c:298`） |
| `FW_RSC_ADDR_ANY` | 任意 | **只查长度**，地址不管 |
| 具体值 | `FW_RSC_ADDR_ANY` | ⚠️`-EINVAL`，而且**一句 dev_err 都不打**（`drivers/remoteproc/remoteproc_core.c:307`）—— 07 篇要专门提这个静默失败 |
| 具体值 | 具体值 | 查 `delta = da - mem->da` 的上下界 |

32 字节的名字长度不是随便定的：它对应 `fw_rsc_carveout.name[32]`
（`include/linux/remoteproc.h:171` 那个结构里的字段），也就是**固件 ABI 定的宽度**。

## §B5 IOMMU 是可选的，而且只是"崩溃触发器"

| 函数 | 位置 | 说明 |
|---|---|---|
| `rproc_enable_iommu()` | `drivers/remoteproc/remoteproc_core.c:100` | `!rproc->has_iommu` 就打一行 dev_dbg 直接返回 0 |
| `rproc_disable_iommu()` | `drivers/remoteproc/remoteproc_core.c:134` | `domain` 为 NULL 就返回 |
| `rproc_iommu_fault()` | `drivers/remoteproc/remoteproc_core.c:84` | 打一行 `iommu fault: da 0x%lx flags 0x%x`，然后 `rproc_report_crash(rproc, RPROC_MMUFAULT)` |

页错误回调**故意返回 `-ENOSYS`**，源码注释解释得很清楚：
*"Let the iommu core know we're not really handling this fault;
we just used it as a recovery trigger."*
⇒ 这是全核心唯一一处 `-ENOSYS`（01 §10.1），语义不是"没实现"，而是"我不处理，你继续报错"。

`iommu_domain_alloc(dev->bus)` 用的是**父设备**（平台设备）的总线，
`iommu_attach_device()` 也是把**父设备**挂进 domain ——
不是那个 `struct rproc` 自己的 `dev`。理由是 IOMMU 拓扑认的是真实的平台设备。

## §B6 subdev 四个回调的调用矩阵

| 回调 | 调用者 | 遍历方向 | 位置 |
|---|---|---|---|
| `prepare` | `rproc_prepare_subdevices()` | 正序 | `drivers/remoteproc/remoteproc_core.c:1078` |
| `start` | `rproc_start_subdevices()` | 正序 | `drivers/remoteproc/remoteproc_core.c:1102` |
| `stop` | `rproc_stop_subdevices()` | **逆序** | `drivers/remoteproc/remoteproc_core.c:1125` |
| `unprepare` | `rproc_unprepare_subdevices()` | **逆序** | `drivers/remoteproc/remoteproc_core.c:1135` |

两个失败回滚都用 `list_for_each_entry_continue_reverse`：
prepare 失败时从**当前节点往前**逐个 `unprepare`
（`drivers/remoteproc/remoteproc_core.c:1089`），
start 失败时同样往前逐个 `stop`（`drivers/remoteproc/remoteproc_core.c:1113`）。
⇒ 已经成功的那些会被正确回滚，**当前失败的那个不会**被回滚 —— 这是内核的标准写法。

⚠️`unroll_registration` 里调的是 `subdev->stop(subdev, **true**)`
（`drivers/remoteproc/remoteproc_core.c:1115`）—— 也就是说
**"start 失败"被当成"崩溃"传给 subdev**。对 virtio subdev 而言这意味着
不去和远端握手直接拆掉，是保守且正确的选择，但参数名 `crashed` 在这里名不副实。

调用点汇总：

| 时机 | prepare | start | stop | unprepare |
|---|---|---|---|---|
| `rproc_start()` | `drivers/remoteproc/remoteproc_core.c:1292` | `drivers/remoteproc/remoteproc_core.c:1306` | —— | —— |
| `__rproc_attach()` | `drivers/remoteproc/remoteproc_core.c:1334` | `drivers/remoteproc/remoteproc_core.c:1350` | —— | —— |
| `rproc_stop()` | —— | —— | `drivers/remoteproc/remoteproc_core.c:1715` | `drivers/remoteproc/remoteproc_core.c:1732` |
| `__rproc_detach()` | —— | —— | `drivers/remoteproc/remoteproc_core.c:1754` | 在 `drivers/remoteproc/remoteproc_core.c:1772` 置 `DETACHED` 之前 |

## §B7 错误回滚的对称性

`rproc_fw_boot()` 的 goto 阶梯（§A4）和 `rproc_shutdown()` 的正常路径应当互为逆序。
逐项对齐：

| 启动做的事 | 回滚 / 关机做的事 | 对称? |
|---|---|---|
| `rproc_enable_iommu()` | `rproc_disable_iommu()` | ✅ |
| `rproc_prepare_device()` | `rproc_unprepare_device()` | ✅ |
| `rproc_parse_fw()` 分配 `cached_table` | `kfree(cached_table)` | ✅ |
| `rproc_handle_resources()` 建各种链表项 | `rproc_resource_cleanup()` | ✅ |
| `rproc_alloc_registered_carveouts()` | 由 `rproc_resource_cleanup()` 里每个 entry 的 `release` 回调负责 | ✅ |
| `rproc_prepare_subdevices()` | `rproc_unprepare_subdevices()` | ✅ |
| `ops->start` | `ops->stop` | ✅ |
| `rproc_start_subdevices()` | `rproc_stop_subdevices()` | ✅ |
| `rproc_load_segments()` 把固件搬进内存 | **没有对应回滚** | ⚠️ 不需要 —— 内存本来就要被下一份固件覆盖 |
| `table_ptr` 指向共享内存 | `reset_table_ptr` / `rproc_reset_rsc_table_on_stop()` | ✅ |

结论：**这一套回滚是完整的**。唯一"不对称"的两项（`load_segments`、
`get_boot_addr`）本身没有需要释放的资源。
剩下的三处真实不对称在 Part C 里单列。

---

# Part C：评价

## §C1 源码自陈的设计债

在**九个核心文件**（七个核心 `.c` + `remoteproc_internal.h` + `include/linux/remoteproc.h`）
里 grep `FIXME` / `TODO` / `XXX` / `REVISIT` / `HACK`，**一共只有 3 条，全是 `TODO`，全在
`remoteproc_core.c`**：

| 位置 | 内容 | 影响 |
|---|---|---|
| `drivers/remoteproc/remoteproc_core.c:364` | *assign a notifyid for rvdev updates as well* | 目前 notifyid 只给 vring 分配，vdev 本身的配置变更通知没有 id 可用 |
| `drivers/remoteproc/remoteproc_core.c:365` | *support predefined notifyids (via resource table)* | 固件不能指定自己想用的 notifyid，只能接受内核分配的 |
| `drivers/remoteproc/remoteproc_core.c:2561` | *make sure this works with rproc->power > 1* | ⚠️`rproc_del()` 里那句无条件 `rproc_shutdown()`，在有多个使用者时只减了一次计数 |

三条 `TODO` 全部集中在 **notifyid 分配** 和 **拆除时的计数** 两件事上 ——
这也正好是本子系统两个最容易出问题的地方。

## §C2 ⚠️ 厂商资源 + `RSC_IGNORED` + 恰好是最后一条 ⇒ 启动失败

`rproc_handle_resources()`（`drivers/remoteproc/remoteproc_core.c:1018`）里，
厂商资源分支的写法是：

| 步骤 | 行 |
|---|---|
| `ret = rproc_handle_rsc(...)` | `drivers/remoteproc/remoteproc_core.c:1044` |
| `ret == RSC_HANDLED` → `continue` | `drivers/remoteproc/remoteproc_core.c:1046` |
| `ret < 0` → `break` | `drivers/remoteproc/remoteproc_core.c:1047` |
| 否则打 `unsupported vendor resource %d` 后 `continue` | `drivers/remoteproc/remoteproc_core.c:1049-1052` |
| 循环结束后 `return ret` | 函数末尾 |

问题在最后一条：`RSC_IGNORED` 的值是 **1**（`include/linux/remoteproc.h:357`），
`continue` 之后 **`ret` 还是 1**。如果这条厂商资源恰好是资源表的**最后一项**，
循环结束时 `ret == 1`，函数就返回 1。
而两个调用者都是 `if (ret) { dev_err("Failed to process resources: %d"); goto … }`
（`drivers/remoteproc/remoteproc_core.c:1419`、
`drivers/remoteproc/remoteproc_core.c:1633`）——
**正数被当成失败**，启动/attach 直接失败并回滚。

触发条件（三条同时成立）：

1. 固件资源表里有一条 `type` 落在 `RSC_VENDOR_START`…`RSC_VENDOR_END` 区间；
2. 驱动**没有**实现 `ops->handle_rsc`（这时包装函数返回 `RSC_IGNORED`，
   `drivers/remoteproc/remoteproc_internal.h:189`），或者实现了但对这一条返回 `RSC_IGNORED`；
3. 这条资源**是表里最后一条**。

⇒ 如果它不是最后一条，下一轮循环的赋值会把 `ret` 冲掉，症状就消失 ——
**这也是这个缺陷不容易被发现的原因**。

⚠️ 本条是**静态代码阅读得出的结论，未在硬件/QEMU 上实测**（记入 99 §5）。
判断依据是三处返回值语义的组合，每一处都已逐行核对。

## §C3 ⚠️`pm_relax()` 在一条路上被跳过

`rproc_report_crash()` 每次都 `pm_stay_awake(rproc->dev.parent)`
（`drivers/remoteproc/remoteproc_core.c:2685`）。
配对的 `pm_relax()` 在 `rproc_crash_handler_work()` 的 `out:` 标签
（`drivers/remoteproc/remoteproc_core.c:1893`）。

但那个函数的第一个早退分支用的是 **`return`** 而不是 `goto out`
（`drivers/remoteproc/remoteproc_core.c:1871-1875`）：

```
 if (rproc->state == RPROC_CRASHED) {
         mutex_unlock(&rproc->lock);
         return;                      ← 不经过 out:，pm_relax 不执行
 }
```

第二个早退分支（`RPROC_OFFLINE`）用的是 `goto out`，是配对的。

⇒ 触发条件：第一次崩溃的 work 已经开始跑（state 已置 `CRASHED`），
此时驱动**再报一次**崩溃 ⇒ 又一次 `pm_stay_awake` + 又排一次 work ⇒
第二个 work 走进第一个早退分支 ⇒ 那一次的 `pm_stay_awake` 没有配对的 `pm_relax`。
后果是父设备的 wakeup 计数不归零，**系统在这之后可能无法进入挂起**。

⚠️ 同样是静态阅读结论，未实测。看门狗+fatal 中断同时上报是现实中会发生的组合。

## §C4 ⚠️ 三处清理不对称（都不构成故障，但读代码时会绕）

| # | 现象 | 位置 | 为什么不出事 |
|---|---|---|---|
| 1 | `rproc_shutdown()` 清 `table_sz = 0`，`rproc_detach()` **不清** | `drivers/remoteproc/remoteproc_core.c:2026` 有，`drivers/remoteproc/remoteproc_core.c:2091` 后没有 | 下次 attach 时 `rproc_set_rsc_table()` 会重新赋值 |
| 2 | `kfree(rproc->clean_table)` 之后**不置 NULL**（两处） | `drivers/remoteproc/remoteproc_core.c:1543`、`drivers/remoteproc/remoteproc_core.c:1578` | 唯一的读点是 `WARN_ON(!clean_table)`（`drivers/remoteproc/remoteproc_core.c:1508`）和 `memcpy`（`drivers/remoteproc/remoteproc_core.c:1537`），而这两处之前必然先经过 `rproc_set_rsc_table()` 重新赋值 |
| 3 | `rproc_del()` 丢掉 `rproc_shutdown()` 的返回值 | `drivers/remoteproc/remoteproc_core.c:2562` | 见 §A10 —— 对只有 `attach`/`detach` 的驱动，远端会继续跑（语义上可接受） |

第 2 条是**悬空指针**，只是恰好永远不被解引用。
它同时也让 `drivers/remoteproc/remoteproc_core.c:1508` 那个
`WARN_ON(!rproc->clean_table)` 的保护价值大打折扣 ——
它想抓的是"该有却没有"，但悬空的非 NULL 一样能通过检查。

## §C5 ⚠️ 32 位截断：资源表 ABI 与 arm64 的天然张力

`fw_rsc_carveout` 的 `da` / `pa` / `len` 全是 **`u32`**（`include/linux/remoteproc.h:171`）——
这是固件 ABI，改不了。arm64 上物理地址常常超过 4 GiB，于是核心有两处
"截断了但只是警告"的地方：

| 位置 | 场景 | 行为 |
|---|---|---|
| `drivers/remoteproc/remoteproc_core.c:763` | `rproc_alloc_carveout()` 里 DMA 分配回来的地址 | `(u64)dma & HIGH_BITS_MASK` 非零就 `dev_warn` |
| `drivers/remoteproc/remoteproc_core.c:1198` | 回写资源表前 | 同样只 `dev_warn`：`Physical address cast in 32bit to fit resource table format`，然后 `rsc->pa = (u32)pa`（`drivers/remoteproc/remoteproc_core.c:1202`） |

`HIGH_BITS_MASK` 定义在 `drivers/remoteproc/remoteproc_core.c:44`，就是
`0xFFFFFFFF00000000ULL`。

⇒ **arm64 上这条警告必须当错误看**：一旦出现，固件读到的 `pa` 是错的，
远端会去访问一个不存在的物理地址，症状通常是 IOMMU fault 或远端直接死。
正确做法是在 DTS 里用 `memory-region` 把 carveout 钉在 4 GiB 以下（06 篇）。

另有一处相关的防线：ELF 加载器用 `rproc_u64_fit_in_size_t()`
（`drivers/remoteproc/remoteproc_internal.h:220`）在
`drivers/remoteproc/remoteproc_elf_loader.c:204` 和
`drivers/remoteproc/remoteproc_elf_loader.c:387` 拦截过大的段 ——
这一处是**真的返回错误**，不是警告。

顺带记一处"检查缺失但无害"的地方：`rproc_fw_boot()` 里
`rproc->bootaddr = rproc_get_boot_addr(rproc, fw)`
（`drivers/remoteproc/remoteproc_core.c:1405`）**不检查返回值** ——
因为回调没实现时包装函数返回 0，而 ELF 版本读的是 `e_entry`，本身不会失败。

## §C6 整体评价

| 方面 | 评价 |
|---|---|
| 分层 | 干净。状态机的临界区完全封在 `remoteproc_core.c` 里，三套用户接口都只调公开 API |
| 回滚 | goto 阶梯完整，subdev 回滚用 `continue_reverse` 精确到失败点 |
| ops 默认值 | ⚠️`rproc_alloc_ops()` 的"全有或全无"（§A1.2）是最容易踩的一个坑，而且**没有任何编译期或运行期提示** |
| 资源表三态 | ⚠️ 本子系统最难读的一块。三个指针 + 两个 reset 函数 + "`cached_table` 是否为空 = 谁启动的"这条隐式约定，全靠注释串起来 |
| 错误码 | ⚠️`-EINVAL` 占核心全部 `return -E` 的 62%（01 §10）；`rproc_alloc_registered_carveouts()` 还会把驱动的错误码改写成 `-ENOMEM`（`drivers/remoteproc/remoteproc_core.c:1165`）。⇒ **调试必须看 dmesg 文本，不能只看错误码**（07 篇的主线） |
| 32 位 ABI | ⚠️ 固件 ABI 全是 `u32`，在 arm64 上是结构性风险（§C5），核心只能警告 |
| 设计债 | 只有 3 条 `TODO`，说明代码整体是被认真维护的 |

---

**下一篇**：04 篇讲挂在这条时间线旁边的几个独立框架 ——
virtio/rpmsg 那一侧、coredump、以及 sysfs / debugfs / cdev 三套用户接口。
