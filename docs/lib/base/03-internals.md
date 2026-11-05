---
title: 内部实现
---

# base 内部实现

这一篇讲「代码实际按什么顺序做了什么」。查字段含义去 `02-datastructures.md`，查怎么调 API 去 `01-api.md`，查怎么写驱动去 `05-usage.md`。

## §0 导航

### §0.1 本篇只承诺一条主轴

主轴是 **Part A：时间线**——从内核启动到设备销毁，按代码实际的调用顺序走一遍。

其余内容分两区，章节号自带前缀：

| 分区 | 内容 | 什么时候看 |
| --- | --- | --- |
| **Part A** | 时间线：启动 → 注册 → 匹配 → 探测 → 解绑 → 销毁 | 想知道「这一步之后发生什么」 |
| **Part B** | 横切机制：锁、devres、device_link 状态机、fw_devlink、sysfs、uevent、异步探测 | 想知道「这个机制贯穿哪些阶段」 |
| **Part C** | 评价：源码自陈的设计债、分层是否干净、arm64 上真正会走到的路径 | 想知道「哪里不能全信」 |

⚠️ Part B 的机制**不是** Part A 的子步骤。它们各自横跨多个阶段，硬塞进时间线会让每个阶段都得讲一遍同一件事。

### §0.2 分层

```
   用户态                 udev / sysfs 读写 / bind、unbind 文件
─────────────────────────────────────────────────────────────
   驱动             drv->probe()      drv->remove()
─────────────────────────────────────────────────────────────
   总线             bus->match()  bus->probe()  bus->dma_configure()
─────────────────────────────────────────────────────────────
   驱动核心         dd.c        绑定与探测状态机
   （本子系统）      core.c      device 生命周期 + device_link + fw_devlink
                    bus.c       总线注册与遍历
                    class.c     类注册
                    driver.c    驱动注册
                    devres.c    托管资源
                    platform.c  platform 伪总线
─────────────────────────────────────────────────────────────
   基础设施         kobject / kset / klist / sysfs / kobj_uevent
─────────────────────────────────────────────────────────────
   固件描述         fwnode_operations → of（DT） / swnode
```

⚠️ 这张图里**没有硬件层**。本子系统不碰任何寄存器，`platform.c` 里那条「总线」只是个组织工具（`drivers/base/platform.c:3` 的文件头注释自己就写着 `platform 'pseudo' bus for legacy devices`）。

### §0.3 生命周期时间线

```
 启动
  │ driver_init()                       drivers/base/init.c:21-42
  │   devices_init / buses_init / classes_init / platform_bus_init …
  │
  ├─ 各总线注册     bus_register()      drivers/base/bus.c:785-862
  │
  ├─ 各驱动注册     driver_register()   drivers/base/driver.c:222-258
  │                   └─ bus_add_driver() → driver_attach()
  │                        └─ 对总线上已有的每个设备试一次匹配
  │
  ├─ 各设备出现     device_add()        drivers/base/core.c:3578-3755
  │                   └─ bus_probe_device() → __device_attach()
  │                        └─ 对总线上已有的每个驱动试一次匹配
  │
  │  ⚠️ 上面两条是对称的两个方向：谁后来谁负责去找对方
  │
  ├─ 匹配成功       really_probe()      drivers/base/dd.c:584-710
  │                   └─ 成功 → driver_bound()    drivers/base/dd.c:393-421
  │                      -EPROBE_DEFER → 进延迟队列，等下一次 trigger
  │
  ├─ late_initcall  deferred_probe_initcall()     drivers/base/dd.c:344-370
  │                 sync_state_resume_initcall()  drivers/base/core.c:1282-1286
  │
  ├─ 解绑           __device_release_driver()     drivers/base/dd.c:1226-1277
  │
  └─ 销毁           device_del()        drivers/base/core.c:3840-3911
                    device_release()（引用归零时）
```

### §0.4 哪些内容不在本篇

| 想查什么 | 去哪 |
| --- | --- |
| 某个字段是什么意思 | `02-datastructures.md` |
| 某个函数怎么调、返回什么 | `01-api.md` |
| component / auxiliary / devcoredump 这些小框架 | `04-extensions.md` |
| 自己写一个总线/驱动/类的骨架 | `05-usage.md` |
| DT 属性谁消费 | `06-devicetree.md` |
| 探测失败了怎么查 | `07-debug.md` |
| 电源管理的挂起/恢复流程 | ⚠️ 不在本文档集范围（`drivers/base/power/` 已裁掉） |
| regmap 的实现 | ⚠️ 不在范围（`drivers/base/regmap/` 已裁掉） |

## Part A 时间线

### §A1 启动：`driver_init()`（`drivers/base/init.c:21-42`）

`init/main.c` 很早就调它。函数体里的次序不是随意的，源码自己用注释分了两批（`drivers/base/init.c:23` 与 `:32-34`）：

第一批「核心件」：`bdi_init()`、`devtmpfs_init()`、`devices_init()`、`buses_init()`、`classes_init()`、`firmware_init()`、`hypervisor_init()`。

第二批「也是核心件，但必须排在核心的核心之后」：`of_core_init()`、`platform_bus_init()`、`auxiliary_bus_init()`、`cpu_dev_init()`、`memory_dev_init()`、`node_dev_init()`、`container_dev_init()`。

⚠️ **`buses_init()` 必须在 `platform_bus_init()` 之前**，因为后者要注册总线，而注册需要 `/sys/bus` 这个 kset 已经存在（`buses_init()` 在 `drivers/base/bus.c:1173-1184` 里建它）。这条依赖没有断言保护——顺序写错的表现是启动早期空指针。

⚠️ **`of_core_init()` 排在 `platform_bus_init()` 之前**：DT 必须先解析好，platform 设备才能从 DT 节点生成。

除了 `driver_init()`，本目录还有几个独立的 initcall：

| initcall | 位置 | 干什么 |
| --- | --- | --- |
| `postcore_initcall(devlink_class_init)` | `drivers/base/core.c:717` | 注册 device_link 用的那个类 |
| `postcore_initcall(software_node_init)` | `drivers/base/swnode.c:1170` | software node 的 kset |
| `postcore_initcall(isa_bus_init)` | `drivers/base/isa.c:182` | isa 伪总线 |
| `core_initcall(soc_bus_register)` | `drivers/base/soc.c:192` | soc 伪总线 |
| `late_initcall(deferred_probe_initcall)` | `drivers/base/dd.c:371` | 放行延迟探测，见 §A8 |
| `late_initcall(sync_state_resume_initcall)` | `drivers/base/core.c:1287` | 放行 `sync_state()`，见 §B3 |

⚠️ **device_link 需要一个类，而这个类在 `postcore_initcall` 才注册。** 所以比 `postcore_initcall` 更早的代码不能建 device_link。

### §A2 总线注册：`bus_register()`（`drivers/base/bus.c:785-862`）

要点四条：

- 分配 `subsys_private` 并双向挂钩（`priv->bus = bus; bus->p = priv;`）
- `priv->subsys.kobj.kset = bus_kset`（`drivers/base/bus.c:804`），所以总线目录出现在 `/sys/bus/` 下
- 建 `devices` 与 `drivers` 两个子 kset
- ⚠️ **`priv->drivers_autoprobe = 1`**：默认开自动探测

⚠️ **`bus_register()` 失败时 `bus->p` 会被清掉，但 `bus_type` 本体是调用者的静态变量，不会消失。** 所以「注册失败之后又拿这条总线注册驱动」会走到 `driver_register()` 里那句检查（`drivers/base/driver.c:227-231`），打印「the bus was not initialized」并返回 `-EINVAL`。这是本子系统里少见的、带明确诊断信息的错误路径。

三种 subsys 形态（`subsys_register()` `drivers/base/bus.c:1082-1122`、`subsys_system_register()` `:1142-1146`、`subsys_virtual_register()` `:1160-1170`）都是 `bus_register()` 的包装，差别只在根设备挂在哪：system 形态挂到 `system_kset`（`/sys/devices/system`），virtual 形态挂到虚拟目录。

### §A3 驱动注册：`driver_register()`（`drivers/base/driver.c:222-258`）

四步检查加两步动作：

1. `drv->bus->p` 为空 → `-EINVAL` 并打印「the bus was not initialized」（`drivers/base/driver.c:227-231`）
2. ⚠️ **总线和驱动同时实现了 `probe`/`remove`/`shutdown` 中任何一个** → 打印「Driver '%s' needs updating - please use bus_type methods」（`drivers/base/driver.c:233-237`）。注意**只是警告，不是错误**，注册照样成功
3. 同名驱动已存在 → `-EBUSY` 并打印「is already registered, aborting」（`drivers/base/driver.c:239-244`）
4. `bus_add_driver()`
5. `driver_add_groups(drv, drv->groups)`
6. 发 `KOBJ_ADD`，然后 `deferred_probe_extend_timeout()`

⚠️ 第 2 条那句警告对应的真实后果在 §A6：`call_driver_probe()` 里**总线的 `probe` 优先，驱动的 `probe` 就不会被核心调**。所以两个都填不是「都会跑」，是「驱动那个被忽略」——除非总线自己在它的 `probe` 里去调。

⚠️ 第 6 步的 `deferred_probe_extend_timeout()` 意味着**每注册一个驱动，延迟探测的超时计时就往后推一次**。所以「模块一个个加载」的场景下超时不会中途触发。

`bus_add_driver()`（`drivers/base/bus.c:592-658`）的关键动作：

- 分配 `driver_private`，`priv->kobj.kset = bus->p->drivers_kset`
- 上 `bus->p->klist_drivers`
- ⚠️ **`if (drv->bus->p->drivers_autoprobe) driver_attach(drv);`**（`drivers/base/bus.c:619-623`）——这是「新注册的驱动去找已有设备」的那条边
- `drv->suppress_bind_attrs` 为假时才建 `bind`/`unbind` 文件

### §A4 设备准备：`device_initialize()`（`drivers/base/core.c:3156-3179`）

它只做初始化，不注册任何东西。填的字段清单值得逐条对照 `02-datastructures.md`：

| 动作 | 说明 |
| --- | --- |
| `dev->kobj.kset = devices_kset` | 归到 `/sys/devices` |
| `kobject_init(&dev->kobj, &device_ktype)` | ktype 决定 release 与属性 ops |
| `INIT_LIST_HEAD(&dev->dma_pools)` | — |
| `mutex_init(&dev->mutex)` | 就是 `device_lock()` 那把 |
| ⚠️ `lockdep_set_novalidate_class(&dev->mutex)` | **把这把锁从 lockdep 校验里摘出去** |
| `spin_lock_init(&dev->devres_lock)` + `INIT_LIST_HEAD(&dev->devres_head)` | devres 就绪，所以 `device_initialize()` 之后就能 `devm_*` |
| `set_dev_node(dev, NUMA_NO_NODE)` | 后面在 `device_add()` 里可能改成父设备的节点 |
| `links.consumers` / `links.suppliers` / `links.defer_sync` 三条链表 + `status = DL_DEV_NO_DRIVER` | — |
| `dev->dma_coherent = dma_default_coherent` | 三个 `ARCH_HAS_SYNC_DMA_FOR_*` 任一开启时；arm64 上会走到 |
| `dev->dma_io_tlb_mem = &io_tlb_default_mem` | `CONFIG_SWIOTLB` 开启时 |

⚠️ **`lockdep_set_novalidate_class(&dev->mutex)`（`drivers/base/core.c:3162`）是一条重要的负面事实**：`device_lock()` 的加锁顺序**不受 lockdep 检查**。原因是设备树的层级深度不定、锁的嵌套模式无法静态描述。后果：**device 锁造成的死锁不会被 lockdep 提前抓到**，只能表现为挂死。这是排查 probe 卡住时必须知道的一条。

⚠️ **devres 在 `device_initialize()` 之后就可用，不需要等 `device_add()`**。但 device_link 要等 `device_initialize()`（consumer 侧）与 `device_add()`（supplier 侧），两者门槛不同（见 `01-api.md` §6.2）。

### §A5 设备注册：`device_add()`（`drivers/base/core.c:3578-3755`）

这是本子系统最长的一个函数（178 行），骨架是「一串带 goto 回滚的步骤」。按源码顺序：

| 步 | 动作 | 行 |
| --- | --- | --- |
| 1 | `get_device(dev)` 取一个引用，整个函数在这个引用的保护下跑 | `:3586` |
| 2 | `dev->p` 为空 → `device_private_init()` | `:3590-3594` |
| 3 | `dev->init_name` 非空 → `dev_set_name()` 后**把 `init_name` 置 NULL** | `:3601-3604` |
| 4 | 还没名字且总线有 `dev_name` → 拼 `"%s%u"`（bus->dev_name + dev->id） | `:3607-3608` |
| 5 | ⚠️ 仍然没名字 → `-EINVAL`，跳 `name_error` | `:3610-3613` |
| 6 | `get_device_parent()` 决定 kobj 挂哪（可能造 glue dir） | `:3617-3624` |
| 7 | 父设备有 NUMA 节点而自己没有 → 继承父的 | `:3627-3628` |
| 8 | `kobject_add()`——sysfs 目录到此才真正出现 | `:3632` |
| 9 | `device_platform_notify(dev)` | `:3639` |
| 10 | 建 `uevent` 属性文件 | `:3641` |
| 11 | `device_add_class_symlinks()` | `:3645` |
| 12 | ⚠️ `device_add_attrs()`——**`dev->groups` 的属性在这一步才出现** | `:3648` |
| 13 | `bus_add_device()`——上总线的设备 klist | `:3651` |
| 14 | `dpm_sysfs_add()` + `device_pm_add()` | `:3654-3657` |
| 15 | `MAJOR(dev->devt)` 非 0 → 建 `dev` 文件、`/sys/dev` 入口、devtmpfs 节点 | `:3659-3669` |
| 16 | 发 `BUS_NOTIFY_ADD_DEVICE` 通知 | `:3674-3676` |
| 17 | ⚠️ **`kobject_uevent(&dev->kobj, KOBJ_ADD)`** | `:3678` |
| 18 | `dev->fwnode` 有且还没绑设备 → 绑上，`fw_devlink_link_device()` | `:3692-3695` |
| 19 | ⚠️ **`bus_probe_device(dev)`——探测从这里开始** | `:3697` |
| 20 | 驱动注册已结束且这个设备匹配不上任何驱动 → `fw_devlink_unblock_consumers()` | `:3704-3705` |
| 21 | 有父设备 → 上父的 `klist_children` | `:3707-3709` |
| 22 | 有类 → 上类的 klist，并逐个调 `class_intf->add_dev()` | `:3711-3723` |

⚠️ **第 17 步（发 `KOBJ_ADD`）在第 12 步（建属性）之后、第 19 步（开始探测）之前。** 这个夹心结构解释了两件事：

- 为什么 `dev->groups` 的属性能被 udev 看到，而 `probe()` 里用 `device_create_file()` 建的看不到（`Documentation/driver-api/driver-model/device.rst:109-120`）
- 为什么第 16 步的注释（`drivers/base/core.c:3671-3673`）特别写明「必须在 `dpm_sysfs_add()` 之后、`kobject_uevent()` 之前」

⚠️ **第 19 步之后才做第 21、22 步（上父与类的 klist）。** 也就是说 `probe()` 执行期间，这个设备**还没挂到父设备的 `klist_children` 上、也还没挂到类的设备表上**。在 `probe()` 里遍历父设备的子设备找不到自己，不是 bug。

⚠️ **第 18 步的注释（`drivers/base/core.c:3680-3691`）是整个函数里唯一解释「为什么必须在这个位置」的一段**，它同时给了上下两个边界：要在 `device_pm_add()` 之后（`device_link_add()` 要求 supplier 已注册），又要在 `bus_probe_device()` 之前（等着的 consumer 要能在驱动绑上、`sync_state()` 调用之前建好链接）。

回滚路径的标签顺序（`drivers/base/core.c:3727-3754`）是 `SysEntryError` → `DevAttrError` → `DPMError` → `BusError` → `AttrsError` → `SymlinkError` → `attrError` → `Error` → `parent_error` → `name_error`，正好是正向步骤的逆序。⚠️ 其中 `attrError` 分支会发一个 `KOBJ_REMOVE`（`drivers/base/core.c:3744`）——**`device_add()` 失败也可能让用户态收到 remove 事件**，前提是已经走到了 `kobject_add()` 之后。

### §A6 匹配：`__device_attach()`（`drivers/base/dd.c:985-1040`）

设备侧的入口是 `bus_probe_device()`（`drivers/base/bus.c:480-496`）。它做两件事：`bus->p->drivers_autoprobe` 为真时调 `device_initial_probe()`（`drivers/base/dd.c:1062-1065`，转 `__device_attach(dev, true)`）；然后不管探测结果如何，都遍历 `bus->p->interfaces` 逐个调 `sif->add_dev()`（`drivers/base/bus.c:491-495`）。

⚠️ **`subsys_interface` 的 `add_dev()` 与自动探测无关**：关掉 `drivers_autoprobe` 之后 interface 回调照样会调。

`__device_attach()` 的三条分支：

1. **`dev->driver` 已经有值** → 调 `device_is_bound()` 判断是否真绑上；绑上就直接返回 1，没绑上就走 `device_bind_driver()`（`drivers/base/dd.c:496-509`）
2. **允许异步且满足条件** → 见 §B7
3. **其余** → `bus_for_each_drv(dev->bus, NULL, &data, __device_attach_driver)`

`__device_attach_driver()`（`drivers/base/dd.c:906-947`）是逐个驱动试的那一层，它的返回值语义是 klist 遍历的约定：**返回 0 表示继续找下一个，返回非 0 表示停止**。三种情况：

| 情况 | 动作 |
| --- | --- |
| `driver_match_device()` 返回 `-EPROBE_DEFER` | 记 `data->have_async`/置 `deferred`，返回 0 继续找 |
| `driver_match_device()` 返回 0 或负值 | 返回 0 继续找 |
| 匹配上，且异步偏好不符 | 返回 0（留给另一遍） |
| 匹配上且偏好相符 | 调 `driver_probe_device()`，用它的返回值决定继续与否 |

⚠️ **`driver_match_device()` 也可能返回 `-EPROBE_DEFER`**：总线的 `match()` 可以说「暂时判断不了」（典型是 DT 里 supplier 还没就绪）。这个 defer 不是探测失败，是**匹配阶段的失败**，比 `probe()` 里的 defer 更早。

⚠️ `__device_attach()` 结束时如果一个驱动都没匹配上，会调 `device_links_no_driver(dev)`（`drivers/base/core.c:1522-1547`），把这个设备作为 supplier 的所有链接降级——见 §B3。

驱动侧的入口对称：`driver_attach()`（`drivers/base/dd.c:1216-1219`）→ `bus_for_each_dev(drv->bus, NULL, drv, __driver_attach)`。⚠️ **`__driver_attach()`（`drivers/base/dd.c:1141-1205`）的所有出口都返回 0**，源码注释（`:1147-1155`）说明了理由：「we need to keep trying to bind to devices and some drivers will return an error simply if it didn't support the device」。所以驱动侧这一遍**一定会走完整条总线**，不会中途停。

它的 defer 处理和设备侧不一样：`driver_match_device()` 返回 `-EPROBE_DEFER` 时，它置 `dev->can_match = true` 并调 `driver_deferred_probe_add(dev)` **把设备**加进延迟队列（`drivers/base/dd.c:1161-1169`），然后返回 0 继续。

⚠️ **延迟队列里放的是设备，不是「设备-驱动对」。** 所以下一次 trigger 时这个设备会重新对整条总线上的所有驱动过一遍匹配，不会记住上次差在哪个驱动上。

### §A7 探测：`really_probe()`（`drivers/base/dd.c:584-710`）

`driver_probe_device()`（`drivers/base/dd.c:809-829`）→ `__driver_probe_device()`（`:764-793`）→ `really_probe()`。中间那层做的是 `dev->p->dead` 检查与运行时电源引用。

`really_probe()` 的顺序（`drivers/base/dd.c:584-710`）：

| 步 | 动作 | 行 |
| --- | --- | --- |
| 0 | `test_remove` 取自 `CONFIG_DEBUG_TEST_DRIVER_REMOVE && !drv->suppress_bind_attrs` | `:586-587` |
| 1 | ⚠️ `defer_all_probes` 为真 → 直接 `-EPROBE_DEFER` | `:590-598` |
| 2 | `link_ret = device_links_check_suppliers(dev)`；⚠️ **只有等于 `-EPROBE_DEFER` 才立刻退出**，`-EAGAIN` 会带着往下走 | `:600-602` |
| 3 | ⚠️ `dev->devres_head` 非空 → `dev_crit(dev, "Resources present before probing\n")` 并返回 `-EBUSY` | `:606-610` |
| 4 | `re_probe:` 标签；`dev->driver = drv` | `:612-613` |
| 5 | `pinctrl_bind_pins(dev)` | `:616-618` |
| 6 | `bus->dma_configure(dev)` | `:620-624` |
| 7 | `driver_sysfs_add(dev)` | `:626-631` |
| 8 | `dev->pm_domain->activate(dev)` | `:633-637` |
| 9 | ⚠️ `call_driver_probe(dev, drv)` | `:639` |
| 10 | `link_ret == -EAGAIN` → 把探测错误改判成 `-EPROBE_DEFER` | `:647-648` |
| 11 | ⚠️ 探测失败 → `ret = -ret;` 然后跳 `probe_failed` | `:654-655` |
| 12 | `device_add_groups(dev, drv->dev_groups)` | `:658-662` |
| 13 | `dev_has_sync_state(dev)` → 建 `state_synced` 属性文件 | `:664-670` |
| 14 | `test_remove` 为真 → 拆一遍再跳回 `re_probe` 重做一遍 | `:672-682` |
| 15 | `pinctrl_init_done(dev)` + `dev->pm_domain->sync(dev)` | `:684-687` |
| 16 | `driver_bound(dev)` | `:689` |

⚠️ **第 11 步的 `ret = -ret;`（`drivers/base/dd.c:654`）是本子系统最反直觉的一行。** 源码注释（`:651-653`）写明理由：「Return probe errors as positive values so that the callers can distinguish them from other errors」。后果是 `__driver_probe_device()` 和 `__device_attach_driver()` 拿到的探测错误是**正数**，而它们自己产生的错误是负数。读这条链上任何一处返回值时都要先确认符号。

⚠️ **第 3 步是给「上一次探测失败没清干净」兜底的。** 正常路径下 `probe_failed` 分支会调 `device_unbind_cleanup()` 释放 devres，所以进到这里 `devres_head` 应该是空的。它非空只有一个来源：**有人在 probe 之外给这个设备挂了托管资源**。这条检查是 `dev_crit` 级别，比一般错误更响。

⚠️ **第 1 步的 `defer_all_probes` 只能由 `device_block_probing()` 置起，而那个函数的注释（`drivers/base/dd.c:590-598` 与 `01-api.md` §5）要求调用方紧接着调 `wait_for_device_probe()`「以避免竞争」。** 也就是说「挡住所有探测」这个能力本身不是原子的——挡住之后还得等已经在跑的那些跑完。

`call_driver_probe()`（`drivers/base/dd.c:553-582`）只有两行实质逻辑：

```c
if (dev->bus->probe)
        ret = dev->bus->probe(dev);
else if (drv->probe)
        ret = drv->probe(dev);
```

⚠️ **总线的 `probe` 存在时，驱动的 `probe` 不会被核心调用。** 这就是 §A3 第 2 条那句警告的实际后果。剩下的部分是把返回值翻成日志：

| 返回值 | 日志 |
| --- | --- |
| 0 | 不打印 |
| `-EPROBE_DEFER` | `dev_dbg`，只有开了动态调试才看得见 |
| `-ENODEV` / `-ENXIO` | `pr_debug`「probe of %s rejects match」 |
| 其他负值 | ⚠️ `pr_warn`「%s: probe of %s failed with error %d」 |

⚠️ **`-ENODEV` 和 `-ENXIO` 被当成「这个驱动看了之后觉得不该管」而不是失败**，所以只有 `pr_debug`。想让探测失败在 dmesg 里默认可见，就不能返回这两个码。

### §A8 绑定完成：`driver_bound()`（`drivers/base/dd.c:393-421`）

按顺序做七件事：

1. ⚠️ `device_is_bound(dev)` 已为真 → `pr_warn`「%s: device %s already bound」并直接返回。**这是个纯诊断分支，不回滚任何东西**
2. `klist_add_tail(&dev->p->knode_driver, &dev->driver->p->klist_devices)` —— 到这一步为止，`/sys/bus/*/drivers/*/` 下才能遍历到这个设备
3. `device_links_driver_bound(dev)` —— 见 §B3
4. `device_pm_check_callbacks(dev)`
5. ⚠️ `driver_deferred_probe_del(dev)` 把自己从延迟队列摘掉
6. ⚠️ `driver_deferred_probe_trigger()` —— **一个设备绑上驱动就会重跑整条延迟队列**
7. `BUS_NOTIFY_BOUND_DRIVER` 通知 + `kobject_uevent(&dev->kobj, KOBJ_BIND)`

⚠️ **第 6 步是延迟探测能收敛的唯一机制。** 它不是定时轮询，而是事件驱动：每一次成功绑定都可能解开别人的依赖，所以立刻把队列再过一遍。第 2 步在第 3 步之前，意味着 `device_links_driver_bound()` 里的回调能看到「设备已在驱动的设备表上」。

### §A9 延迟探测循环（`drivers/base/dd.c:81-129` / `:175-196` / `:344-370`）

三个部件：

- **两条队列**：`deferred_probe_pending_list`（`drivers/base/dd.c:55`）与 `deferred_probe_active_list`（`:56`），都由 `deferred_probe_mutex`（`:54`）保护
- **一个计数器**：`deferred_trigger_count`（`:57`，atomic）
- **一个 workqueue 函数**：`deferred_probe_work_func()`（`:81-129`）

`driver_deferred_probe_trigger()`（`:175-196`）的动作：`driver_deferred_probe_enable` 为假就直接返回；否则把 pending 整条 splice 到 active，然后 `queue_work()`。

`deferred_probe_work_func()`（`:81-129`）逐个从 active 队列取设备，**先 `device_pm_move_to_tail(dev)`**（`:96`，把设备在电源管理链表里移到尾部，保证挂起顺序正确），再 `device_lock` 判断 `dev->p->deferred_probe` 是否仍在队列上，然后 `bus_probe_device(dev)`。

⚠️ **`deferred_trigger_count` 的存在本身就是一条设计债的记号。** 源码注释（`drivers/base/dd.c:164-173`）自己描述了这个竞争：多线程探测时，「一个探测可以成功完成，而同时另一个即将 defer——后者会卡在那里」。计数器的作用是让 `really_probe()` 里的 defer 路径记下自己看到的 trigger 世代，如果绑定期间世代变了就重新入队。它是缓解，不是消除——见 §C1。

`deferred_probe_initcall()`（`:344-370`）在 `late_initcall` 跑，做四件事：置 `driver_deferred_probe_enable = true`、`driver_deferred_probe_trigger()`、`flush_work(&deferred_probe_work)`、然后再 `wait_for_device_probe()`。

⚠️ **`driver_deferred_probe_timeout` 在源码里有两个定义**（`drivers/base/dd.c:260` 与 `:262`，两个 `#ifdef` 分支），默认值一个是 10 秒一个是 0（不超时）。写文档和排查时一定要先看配置——见 `07-debug.md`。

### §A10 解绑：`__device_release_driver()`（`drivers/base/dd.c:1226-1277`）

调用者三条：`device_release_driver()`（`:1302-1310`，sysfs 的 `unbind` 文件）、`device_driver_detach()`（`:1320-1323`）、`driver_detach()`（`:1329-1352`，注销驱动时）。它们都经 `device_release_driver_internal()`（`:1279-1289`）拿锁。

函数体的第一段是本子系统里唯一的「放锁重试」循环（`drivers/base/dd.c:1234-1249`）：

```
while (device_links_busy(dev)) {
        __device_driver_unlock(dev, parent);
        device_links_unbind_consumers(dev);
        __device_driver_lock(dev, parent);
        if (dev->driver != drv) {
                pm_runtime_put(dev);
                return;          /* 别人已经把驱动换掉了 */
        }
}
```

⚠️ **解绑一个 supplier 会先递归解绑它的所有 consumer，而且这个过程要放开设备锁。** 放锁重进之后必须重新验证 `dev->driver` 还是当初那个——这是必要的，不是保险。

之后的顺序：`driver_sysfs_remove()` → `BUS_NOTIFY_UNBIND_DRIVER` → `pm_runtime_put_sync()` → `device_remove(dev)` → `bus->dma_cleanup()` → `device_unbind_cleanup(dev)` → `device_links_driver_cleanup(dev)` → `klist_remove(&dev->p->knode_driver)` → `device_pm_check_callbacks()` → `BUS_NOTIFY_UNBOUND_DRIVER` → `KOBJ_UNBIND`。

⚠️ **`device_remove()` 里调的是 `bus->remove` 或 `drv->remove`，取的是同一套「总线优先」规则**，和 `call_driver_probe()` 对称。⚠️ **`drv->remove` 的返回值被丢弃**（见 `02-datastructures.md` §4.3）：解绑不能失败。

⚠️ **devres 的释放在 `device_unbind_cleanup()` 里，排在 `remove()` 之后。** 所以 `remove()` 执行期间托管资源仍然有效，不需要（也不应该）手工释放它们。

### §A11 设备销毁：`device_del()`（`drivers/base/core.c:3840-3911`）

`device_unregister()`（`:3925-3930`）= `device_del()` + `put_device()`。真正的清理都在 `device_del()` 里，`device_release()` 只负责最后的内存回收。

顺序（对照 §A5 是逆序，但多了几步）：

1. `device_lock` → `kill_device(dev)`（置 `dev->p->dead`）→ `device_unlock`
2. `dev->fwnode->dev == dev` → 清掉 `fwnode->dev`
3. ⚠️ `memalloc_noio_save()` —— 整个拆除过程禁止发起 IO 的内存回收
4. `BUS_NOTIFY_DEL_DEVICE` 通知（注释写明必须在 `dpm_sysfs_remove()` 之前）
5. `dpm_sysfs_remove()`
6. `klist_del(&dev->p->knode_parent)`
7. devt 相关文件与 devtmpfs 节点
8. 类的符号链接、`class_intf->remove_dev()`、`klist_del(&dev->p->knode_class)`
9. `uevent` 文件 + `device_remove_attrs()` + `bus_remove_device()`
10. `device_pm_remove()` + `driver_deferred_probe_del()`
11. `device_platform_notify_remove()` + `device_links_purge()`
12. ⚠️ `devres_release_all(dev)`
13. `BUS_NOTIFY_REMOVED_DEVICE` + `kobject_uevent(KOBJ_REMOVE)`
14. `get_glue_dir()` → `kobject_del()` → `cleanup_glue_dir()`
15. `memalloc_noio_restore()` + `put_device(parent)`

⚠️ **第 12 步（`devres_release_all()`）在这里而不是在 `device_release()` 里，源码注释（`drivers/base/core.c:3891-3899`）解释了原因：如果放在 release 里，「一旦某个托管资源自己持有这个设备的引用，release 就永远不会被调（设备泄漏）」。** 放在 `device_del()` 里是打破这个环的手段。代价是**设备还活着（引用未归零）但托管资源已经全没了**这个中间状态确实存在。

⚠️ **`bus_remove_device()`（`drivers/base/bus.c:508-533`）内部会调 `device_release_driver()`**，所以第 9 步隐含了整个 §A10 的流程。`device_del()` 不需要调用者先解绑。

`driver_unregister()`（`drivers/base/driver.c:267-275`）和 `bus_unregister()`（`drivers/base/bus.c:872-883`）是对称的另一半：前者 `driver_remove_groups()` + `bus_remove_driver()`（`:668-683`，内含 `driver_detach()`），后者拆两个子 kset、`remove_probe_files()`、`bus_remove_groups()`、`kset_unregister()`，然后 `bus->p = NULL`。

⚠️ **`driver_unregister()` 开头判 `!drv || !drv->p` 就 `WARN(1, "Unexpected driver unregister!\n")` 并直接返回**（`drivers/base/driver.c:269-272`），也就是「注销一个没注册成功的驱动」会打 backtrace 而不是静默。

## Part B 横切机制

### §B1 锁

本子系统的锁清单在 `02-datastructures.md` §13.1，这里只讲**层次和获取顺序**。

由外到内的四层：

```
  device_hotplug_lock  drivers/base/core.c:2286   online/offline 与 hotplug 的总闸
    └─ dev->mutex (device_lock)              一个设备一把，probe/remove 全程持有
         ├─ 父设备的 dev->mutex              仅当 bus->need_parent_lock 为真
         ├─ device_links_lock  drivers/base/core.c:251    device_link 的写侧
         ├─ sp->mutex          base.h:44     总线/类的 interfaces 链表
         └─ dev->devres_lock                 自旋锁，最内层
```

⚠️ **`dev->mutex` 被 `lockdep_set_novalidate_class()` 摘出了 lockdep 校验**（§A4），所以这张表**不是 lockdep 能替你验证的**。它是从代码里读出来的，不是运行时保证的。

三条需要单独记的规则：

- ⚠️ **`bus->need_parent_lock` 决定要不要同时锁父设备**。`__device_driver_lock()` / `__device_driver_unlock()`（`drivers/base/dd.c` 里的两个静态函数）根据它决定锁一把还是两把。⚠️ USB 这类拓扑设备需要它，platform 不需要
- ⚠️ **`dev->devres_lock` 用 `spin_lock_irqsave()`**（`drivers/base/devres.c:245`），因此**托管资源可以在中断上下文里加**。这是本子系统里唯一允许在硬中断里调的一类接口
- ⚠️ **device_link 是「写用 mutex、读用 SRCU」的分离结构**。`device_links_read_lock()` 在源码里有**两份实现**（`drivers/base/core.c:264` 与 `:302`），按 `CONFIG_SRCU` 分支。读侧不阻塞写侧，所以遍历 `links.suppliers` 时看到的可能是稍旧的快照

### §B2 devres 的分组与释放

单个资源的加/找/删是平的（`devres_add()` `drivers/base/devres.c:240-248`、`devres_find()` `:283-296`、`devres_remove()` `:349-366`），真正需要读代码才懂的是**分组**。

分组的实现在 `02-datastructures.md` §8.3 已经交代了结构：一个组是**两个哨兵节点**（`devres_group.node[2]`），不是一个容器。所以：

- `devres_open_group()`（`drivers/base/devres.c:549-573`）把 `node[0]` 挂进链表，`node[1]` 留空 —— 这叫「开放的组」
- `devres_close_group()`（`:607-621`）把 `node[1]` 也挂进去 —— 这叫「闭合的组」
- `devres_remove_group()`（`:633-651`）把两个哨兵摘掉，组内的资源**留在链表上不动**
- `devres_release_group()`（`:666-700`）才真正释放组内资源

释放的核心是 `remove_nodes()`（`drivers/base/devres.c:432-491`），它用**两遍扫描加着色**：

第一遍：把普通 devres 节点搬到 `todo` 链表，同时把遇到的所有组哨兵的 `color` 清零。

第二遍：再扫一遍，每遇到一个组哨兵就 `grp->color++`；如果这个组是开放的（`node[1]` 不在链表上）就再 `++` 一次。⚠️ **`color == 2` 表示「这个组完整落在待释放区间内」**——闭合组要两个哨兵都在区间里，开放组只要开头在就够。只有 `color == 2` 的组才会被搬进 `todo`。

⚠️ **第二遍里有两个 `BUG_ON`**（`drivers/base/devres.c:474` 判「组哨兵存在且 `node[0]` 在链表上」、`:480` 判 `color` 落在 1..2 内）。着色算法的前提是「链表上的组哨兵一定成对或者是开放组」，前提破了就是内核 panic，不是返回错误。

`release_nodes()`（`:493-505`）拿到 `todo` 后**逆序**遍历调 `dr->node.release(dev, dr->data)` 并 kfree。

⚠️ **「逆序释放」这个保证只在同一个设备的 `devres_head` 内部成立。** 跨设备没有任何顺序保证——那要靠 device_link（§B3）。

`devres_release_all()`（`:514-534`）是设备销毁时走的那条（§A11 第 12 步），它把整条 `devres_head` 当成一个区间交给 `remove_nodes()`。

### §B3 device_link 状态机

五个状态定义在 `include/linux/device.h:300-307`。核心事实：**状态是由核心在四个时点改写的，驱动不该自己改**。

四个改写点：

| 函数 | 位置 | 改什么 |
| --- | --- | --- |
| `device_links_check_suppliers()` | `drivers/base/core.c:1108-1163` | consumer 开始 probe：把每条 supplier 链接从 `AVAILABLE` 推到 `CONSUMER_PROBE` |
| `device_links_driver_bound()` | `drivers/base/core.c:1363-1470` | 绑定成功：consumer 侧链接 `DORMANT`→`AVAILABLE`，supplier 侧链接 `CONSUMER_PROBE`→`ACTIVE` |
| `device_links_no_driver()` | `drivers/base/core.c:1522-1547` | 探测失败：consumer 侧的 `CONSUMER_PROBE`/`ACTIVE` 一律打回 `DORMANT` |
| `device_links_driver_cleanup()` | `drivers/base/core.c:1559-1588` | 驱动移除：consumer 侧链接一律置 `DORMANT` |

⚠️ **`device_links_check_suppliers()` 的返回值有三种，语义各不相同**（`drivers/base/core.c:1108-1163`）：

- `-EPROBE_DEFER`：有 supplier 没就绪，`dev_err_probe()` 打「supplier %s not ready」或「wait for supplier %pfwP」
- `-EAGAIN`：**best-effort 模式**下才可能返回（`dev_is_best_effort()` `drivers/base/core.c:1070`），意思是「按理该等，但我们选择让它先试试」
- `0`：全部就绪

⚠️ **`-EAGAIN` 这条路会让 `really_probe()` 在探测成功之后把结果改判成 `-EPROBE_DEFER`**（§A7 第 10 步）。也就是说 best-effort 模式下「驱动的 `probe()` 返回了 0，设备却没绑上」是可能的，而且 `probe()` 里申请的东西已经被 `probe_failed` 路径拆掉了。这是 arm64 上开了 `fw_devlink.strict=0` 之后最难排查的一类现象。

⚠️ **`device_links_driver_cleanup()` 里有两句 `WARN_ON`（`drivers/base/core.c:1569-1570`），期望所有 consumer 链接此时都处于 `SUPPLIER_UNBIND`。** 走到这里之前 `device_links_unbind_consumers()`（`:1644-1681`）已经把 consumer 全解绑过了，所以状态应当已经是 `SUPPLIER_UNBIND`。看到这两条 WARN 说明有人绕过了正常解绑路径。

`sync_state()` 的调度是另一条线。它不在上表的四个时点里，而是：

1. `device_links_driver_bound()` 里对自己和每个 supplier 各调一次 `__device_links_queue_sync_state()`（`drivers/base/core.c:1420`、`:1462`）
2. ⚠️ 如果 `defer_sync_state_count` 非零，就改成挂到 `deferred_sync` 链表上（`:1418`、`:1460`）
3. `defer_sync_state_count` 初值是 **1**，由 `sync_state_resume_initcall()`（`drivers/base/core.c:1282-1286`，`late_initcall`）减到 0

⚠️ **所以 `late_initcall` 之前不会有任何 `sync_state()` 被调用**，全部排队。这个「暂停计数器」还可以被 `device_links_supplier_sync_state_pause()`（`:1247-1252`）再加，配对的 resume 少一次就会在下一次 resume 里撞 `WARN(true, "Unmatched sync_state pause/resume!")`（`drivers/base/core.c:1261`）。

⚠️ **`sync_state()` 也遵循「总线优先」**：`device_links_flush_sync_list()`（`:1224-1245`）先看 `dev->bus->sync_state`，没有才看 `dev->driver->sync_state`。

### §B4 fw_devlink：从 fwnode_link 到 device_link

fw_devlink 解决的是「DT 里写了依赖，但 supplier 的 `struct device` 还不存在」。它分两层：

- **`fwnode_link`**：固件节点之间的依赖，`device_add()` 之前就能建
- **`device_link`**：设备之间的依赖，需要两边都有 `struct device`

转换的入口是 `fw_devlink_create_devlink()`（`drivers/base/core.c:2051-2144`），它的五条出口值得逐条记：

| 条件 | 位置 | 返回 |
| --- | --- | --- |
| `link->flags & FWLINK_FLAG_IGNORE` | `:2059` | 0（当作没这条依赖） |
| supplier 是 consumer 的祖先且带 `FWNODE_FLAG_NEEDS_CHILD_BOUND_ON_ADD` | `:2077-2079` | `-EINVAL` |
| supplier 设备存在但 `links.status == DL_DEV_NO_DRIVER` 且带 `FWNODE_FLAG_INITIALIZED` | `:2112-2117` | ⚠️ `-EINVAL`（「dev might never probe」） |
| supplier 设备存在，`device_link_add()` 成功 | `:2119-2124` | 0 |
| supplier 设备还不存在，且不属于「永远不会变成设备」 | `:2140` | `-EAGAIN`（保留 fwnode_link，下次再试） |

⚠️ **第三条是给「不走驱动模型绑定」的那类节点开的后门。** 有些固件节点对应的设备是手工创建、不绑驱动的（典型是某些 clock/regulator 的固定值节点）。如果不给它开这个口子，等它的 consumer 会永久 defer。

环检测在每次都跑，源码注释（`drivers/base/core.c:2081-2085`）明确拒绝优化：「Don't try to optimize by not calling the cycle detection logic under certain conditions. There's always some corner case that won't get detected.」

⚠️ **检测到环时不是报错，而是给这条 fwnode_link 打上 `FWLINK_FLAG_CYCLE`（`include/linux/fwnode.h:56`），然后 `pr_info("%pfwf: Fixed dependency cycle(s) with %pfwf\n")`（`drivers/base/core.c:2090`）。** 打了这个标志的链接后面会被当成「宽松」处理，不再阻塞探测。所以**dmesg 里出现「Fixed dependency cycle」不是错误，是 fw_devlink 自己把死锁解开了**——但它同时意味着 DT 里的依赖描述有环，值得回去看。

`__fw_devlink_relax_cycles()`（`drivers/base/core.c:1936-2029`）是环检测本体，用递归深度优先搜索。它的输入是 consumer 的 fwnode 和候选 supplier 的 fwnode，判断「从 supplier 出发能不能走回 consumer」。

两个方向的批量转换：`__fw_devlink_link_to_consumers()`（`:2162-2210`，一个设备出现时，把等它的那些 consumer 的 fwnode_link 转成 device_link）与 `__fw_devlink_link_to_suppliers()`（`:2235-2261`，反方向）。

### §B5 sysfs 目录与 glue dir

`get_device_parent()`（`drivers/base/core.c:3244-3302`）决定新设备的 kobject 挂在哪。五条分支：

| 条件 | 挂到哪 |
| --- | --- |
| `sysfs_deprecated && dev->class == &block_class` | `/sys/block`（`CONFIG_BLOCK` 下的特殊照顾，`:3252-3258`） |
| 有 class，无 parent | `virtual_device_parent(dev)` → `/sys/devices/virtual` |
| 有 class，parent 也有 class，且 `dev->class->ns_type` 为空 | 直接挂 parent 的 kobj |
| 有 class，其余 | ⚠️ **glue dir**：parent 下面的一个以类名命名的中间目录 |
| 无 class | `bus->dev_root` 的 kobj（若有）或 parent 的 kobj |

⚠️ **glue dir 的目的是防命名冲突**（源码注释 `:3262-3266`）：一个类设备挂在一个非类设备下面时，直接挂会让同名的类设备撞车。这个中间目录**不发 uevent**（`:3290`），所以用户态看不到它被创建。

glue dir 的查找与创建都在 `gdp_mutex`（`drivers/base/core.c:3242`）下，候选从 `dev->class->p->glue_dirs` 这个 kset 里线性查找（`:3276-3282`），命中就 `kobject_get()` 复用，没命中才 `class_dir_create_and_add()`（`:3289`）。

⚠️ **`cleanup_glue_dir()`（`drivers/base/core.c:3340-3402`）里有本子系统最长的一段竞争说明注释（`:3349-3362`），配了 CPU1/CPU2 交错图。** 竞争是「删除 glue dir」和「在同一个 glue dir 下加新设备」并发。处置手段是在 `gdp_mutex` 下重新检查 kobject 的引用计数。这段注释本身就是设计债的记号——见 §C1。

### §B6 uevent 与通知链

两套机制，容易混：

| 机制 | 谁收 | 调用点 |
| --- | --- | --- |
| **uevent**（`kobject_uevent()`） | 用户态（udev） | `KOBJ_ADD` / `KOBJ_REMOVE` / `KOBJ_BIND` / `KOBJ_UNBIND` |
| **总线通知链**（`bus_notifier`） | 内核内其他模块 | 9 处 `blocking_notifier_call_chain()` |

九个通知点的完整清单：

| 事件 | 位置 |
| --- | --- |
| `BUS_NOTIFY_BOUND_DRIVER` | `drivers/base/dd.c:417` |
| `BUS_NOTIFY_BIND_DRIVER` | `drivers/base/dd.c:439` |
| `BUS_NOTIFY_DRIVER_NOT_BOUND`（`device_bind_driver()` 失败） | `drivers/base/dd.c:506` |
| `BUS_NOTIFY_DRIVER_NOT_BOUND`（`really_probe()` 失败） | `drivers/base/dd.c:701` |
| `BUS_NOTIFY_UNBIND_DRIVER` | `drivers/base/dd.c:1254` |
| `BUS_NOTIFY_UNBOUND_DRIVER` | `drivers/base/dd.c:1271` |
| `BUS_NOTIFY_ADD_DEVICE` | `drivers/base/core.c:3675` |
| `BUS_NOTIFY_DEL_DEVICE` | `drivers/base/core.c:3859` |
| `BUS_NOTIFY_REMOVED_DEVICE` | `drivers/base/core.c:3903` |

⚠️ **`BUS_NOTIFY_DRIVER_NOT_BOUND` 有两个发出点，语义不同**：`dd.c:506` 是 `device_bind_driver()`（手工绑定）失败，`dd.c:701` 是正常探测失败。收这个通知的代码分不出来是哪一种。

⚠️ **通知链是 `blocking_notifier`，回调在调用者的上下文里同步执行，而且此时 `dev->mutex` 是持有的。** 所以通知回调里不能再去锁同一个设备。

设备自己的 uevent 内容由 `dev_uevent()`（`drivers/base/core.c:2566-2630`）填，顺序固定：

1. 有 devt 时的 `MAJOR`/`MINOR`/`DEVNAME`/`DEVMODE`/`DEVUID`/`DEVGID`（`:2572-2592`）
2. `DEVTYPE`（取 `dev->type->name`，`:2594-2595`）
3. `DRIVER`（`:2597-2598`）
4. ⚠️ `of_device_uevent(dev, env)`（`:2601`）—— **DT 相关的变量在这里加，不由本子系统产生**
5. `dev->bus->uevent()`（`:2604`）
6. `dev->class->dev_uevent()`（`:2612`）
7. `dev->type->uevent()`（`:2621`）

⚠️ **三个回调的次序是「总线 → 类 → 类型」，和 `device_add()` 里那些回调的次序不一样**，别按记忆推。三者都是往同一个 env 上追加，后来的能覆盖同名变量；⚠️ 三者返回负值时只 `pr_debug` 一条，**不会让 uevent 发送失败**。

### §B7 异步探测

两条独立的异步路径，入口不同：

- **设备侧**：`__device_attach()` → 匹配到的驱动允许异步 → `async_schedule_dev(__device_attach_async_helper, dev)`。helper 在 `drivers/base/dd.c:949-983`
- **驱动侧**：`__driver_attach()` → `driver_allows_async_probing(drv)` → 在 `device_lock` 下检查 `!dev->driver && !dev->p->async_driver`，置 `dev->p->async_driver = drv` 并 `get_device()`，然后 `async_schedule_dev(__driver_attach_async_helper, dev)`（`drivers/base/dd.c:1179-1198`）。helper 在 `:1124-1139`

⚠️ **`dev->p->async_driver` 这个字段的作用是防止同一个设备被两次异步排队。** 检查和赋值都在 `device_lock` 里，是这条路径上唯一的同步点。

是否异步由 `driver_allows_async_probing()`（`drivers/base/dd.c:853-871`）一个 switch 决定：

| `drv->probe_type` | 结果 |
| --- | --- |
| `PROBE_PREFER_ASYNCHRONOUS` | 直接 true |
| `PROBE_FORCE_SYNCHRONOUS` | 直接 false |
| 其余（含 `PROBE_DEFAULT_STRATEGY`） | ⚠️ 再看 `cmdline_requested_async_probing(drv->name)`，再看 `module_requested_async_probing(drv->owner)`，两个都不同意才 false |

⚠️ **`PROBE_DEFAULT_STRATEGY` 不等于同步**（见 `02-datastructures.md` §4.4）：它意味着「听命令行和模块参数」。命令行那道门按**驱动名**匹配，所以可以只把某一个驱动改成异步。

⚠️ **`wait_for_device_probe()`（`drivers/base/dd.c:753-761`）等的是两样东西**：先 `async_synchronize_full_domain()` 等所有异步探测，再 `wait_event(probe_waitqueue, atomic_read(&probe_count) == 0)` 等同步探测。`probe_count`（`:512`）由 `driver_probe_device()` 在调 `__driver_probe_device()` 前后一增一减（`:814`、`:826`）。

## Part C 评价

### §C1 源码自陈的设计债

本子系统里没有一个 `REVISIT` 或 `TODO` 标记，但有十几处注释在自陈问题。它们比标记更有价值，因为写得很具体。按严重程度排：

| 位置 | 自陈的问题 |
| --- | --- |
| `drivers/base/dd.c:164-173` | ⚠️ **多线程探测里有一个竞争**：「it is possible for one probe to complete successfully while another is about to defer… will be stuck there」 |
| `drivers/base/core.c:3349` 起 | ⚠️ **删 glue dir 与在同一个 glue dir 下加设备的竞争**，CPU1/CPU2 交错图一直画到 `:3382` |
| `drivers/base/core.c:3891-3899` | ⚠️ **托管资源持有设备引用时会泄漏设备**：「it's never called (and we leak the device) if a managed resource holds a reference to the device」 |
| `drivers/base/core.c:2489-2497` | ⚠️ **「possible memory leak」**：驱动可以在设备已删除但还活着的时候继续加资源，所以 `device_release()` 里得再释放一次 devres |
| `drivers/base/core.c:4514-4541` | ⚠️ **`device_rename()` 的整段劝退**（Kay Sievers 执笔）：「Renaming devices is racy at many levels」「never rename kernel devices later, it's a complete mess」，开头一句是 **「Note: Don't call this function.」** |
| `drivers/base/bus.c:910-916` | ⚠️ **「Yes, this forcibly breaks the klist abstraction temporarily」**（`device_insertion_sort_klist()` 在 `:917`） |
| `drivers/base/bus.c:633` | ⚠️ `/* How the hell do we get out of this pickle? Give up */` —— `bus_add_driver()` 里 `driver_add_groups()` 失败后无路可退 |
| `drivers/base/platform.c:933`、`:939-940` | ⚠️ 「temporary section violation during probe()」/「Fixup that section violation, being paranoid about code scanning」 |
| `drivers/base/attribute_container.c:179`、`:540` | 两处 FIXME：都是「不能在 klist 迭代中间 break，要先退出迭代器」 |
| `drivers/base/devres.c:877-882` | `WARN_ON(is_kernel_rodata(...))`：「We cannot reliably realloc a const string returned by `devm_kstrdup_const()`」 |
| `drivers/base/class.c:179` | `sysfs_deprecated` 对 `block_class` 的特殊照顾 |
| `drivers/base/class.c:499-500` | class_compat 自称「a temporary user-space compatibility workaround」 |
| `drivers/base/platform.c:1091`、`:1103` | `platform_legacy_suspend()` / `platform_legacy_resume()`，名字里就写着 legacy |

四条要点：

⚠️ **前两条是真竞争，而且都只有缓解手段没有消除手段。** `deferred_trigger_count` 让 defer 路径能发现「我 defer 期间世代变了」，`gdp_mutex` 下重查引用计数让 glue dir 少一点机会被误删。两者都是「把窗口缩小」而不是「关掉窗口」。

⚠️ **第三、四条合起来说明 devres 的生命周期是设计上没完全收敛的。** 同一个 `devres_release_all()` 在两个地方各调一次（`device_del()` 的 `drivers/base/core.c:3900` 与 `device_release()` 的 `:2498`），第二次纯粹是给「设备已删但还活着」这段窗口兜底。

⚠️ **`device_rename()` 那段注释的价值是它给出了「不该用」的完整理由链**，而函数仍然导出、仍然被网络子系统调。这是本子系统里「文档说不要用、代码留着能用」最典型的一处。写驱动时看到导出符号别当成推荐用法。

⚠️ **`drivers/base/bus.c:633` 那句脏话对应的是真实的资源泄漏**：属性组加不上时，前面 `kobject_init_and_add()` 建的目录和 klist 上的挂钩已经没法干净回滚了。

### §C2 分层是否干净

三处不干净，都会直接影响写驱动的人：

**一、总线回调与驱动回调是「二选一」而不是「都调」。**

`call_driver_probe()`（`drivers/base/dd.c:553-582`）与 `device_remove()` 都遵循「`bus->probe` 优先，有就不调 `drv->probe`」。`driver_register()`（`drivers/base/driver.c:233-237`）只在两个都填时打一句 `pr_warn`，注册照样成功。

⚠️ **后果**：写一条新总线时如果实现了 `bus->probe`，挂在它上面的所有驱动的 `probe` 都会被跳过，除非总线自己去调。这一层没有任何断言保护，只有一句可能被淹没的 warn。arm64 上 platform 总线**没有** `bus->probe`（`platform_bus_type` 只填了 10 个字段，见 `02-datastructures.md` §7.3），所以 platform 驱动不会撞这个坑；写自定义总线的人会。

**二、探测错误被取负号返回。**

`really_probe()` 的 `ret = -ret;`（`drivers/base/dd.c:654-655`）让「驱动返回的错误」和「核心自己产生的错误」符号相反。注释说这是为了让调用者能区分二者。

⚠️ **代价是这条调用链上每一处返回值判断都得先想清楚符号。** `__driver_probe_device()`（`:764-793`）、`__device_attach_driver()`（`:906-947`）、`__driver_attach()`（`:1141-1205`）三层都要处理这个约定。这是本子系统里最容易读错的一处，也是「用类型系统本来能表达、结果用符号约定表达」的典型。

**三、`device` 既是抽象基类又是具体实例。**

`platform_bus`（`drivers/base/platform.c:42`）本身是一个 `struct device`，在 `platform_bus_init()`（`:1513-1529`）里被 `device_register()`。`struct device_link` 里也嵌了一个完整的 `struct device link_dev`（见 `02-datastructures.md` §6.2）。

⚠️ **后果**：`/sys/devices/platform` 这个「目录」其实是一个真的设备，`/sys/class/devlink/*` 下每条依赖关系也是一个真的设备。遍历设备时会碰到它们，它们没有驱动也永远不会绑驱动。

一处**确实干净**的地方值得记下来作对照：**devres 与设备生命周期的耦合只通过 `dev->devres_head` 和 `dev->devres_lock` 两个字段**，`devres.c` 里没有一处去读 `dev->driver` 或 `dev->bus`。所以托管资源机制可以在完全不涉及驱动绑定的设备上使用（§A4）。

### §C3 arm64 上真正会走到的路径

**八条伪总线里在 arm64 上真会出现的**：

| 伪总线 | 编译条件 | arm64 defconfig |
| --- | --- | --- |
| `platform_bus_type`（`drivers/base/platform.c:1478`） | 无条件（`drivers/base/Makefile:6` 的 `obj-y`） | 一定在 |
| `cpu_subsys`（`drivers/base/cpu.c:128`） | 无条件 | 一定在 |
| `container_subsys`（`drivers/base/container.c:27`） | 无条件 | 一定在 |
| `node_subsys`（`drivers/base/node.c:25`） | `CONFIG_NUMA` | 在（`arch/arm64/configs/defconfig:75`） |
| `memory_subsys`（`drivers/base/memory.c:71`） | `CONFIG_MEMORY_HOTPLUG` | 在（`arch/arm64/configs/defconfig:131`） |
| `soc_bus_type`（`drivers/base/soc.c:30`） | `CONFIG_SOC_BUS`，33 处 `select` | 通常在（被 SoC 驱动带进来） |
| `auxiliary_bus_type`（`drivers/base/auxiliary.c:247`） | `CONFIG_AUXILIARY_BUS`，13 处 `select` | 看有没有装那 13 类驱动 |
| `isa_bus_type`（`drivers/base/isa.c:85`） | `CONFIG_ISA_BUS_API` | ⚠️ **实际上碰不到**：`select` 它的都是 x86 ISA 板卡驱动 |

重跑上面两个计数：`grep -rn 'select SOC_BUS' --include=Kconfig* .` 与 `grep -rn 'select AUXILIARY_BUS' --include=Kconfig* .`。

**主导路径是 platform。** 全树 `struct platform_driver` 的实例数是 **2555**（分布在 2525 个 .c 文件里），而所有 `struct bus_type` 实例一共 **146** 个。重跑：

```
grep -rn 'struct platform_driver [a-zA-Z_]* = {' --include=*.c . | wc -l
grep -rn 'struct bus_type [a-zA-Z_]* = {' --include=*.c . | wc -l
```

⚠️ **所以 arm64 上 SoC 内部外设的路径几乎全是同一条**：DT 节点 → `of_platform_populate()` 造 `platform_device` → `platform_match()`（`drivers/base/platform.c:1331-1354`）四道匹配 → `platform_probe()`（`:1375-1412`）→ 驱动的 `probe()`。§A5 到 §A8 讲的那一串，在 arm64 上的实际形态就是这条。

三条 arm64 特有的分支：

- ⚠️ **`platform_dma_configure()`（`drivers/base/platform.c:1443-1463`）在 arm64 上一定会走到**：`platform_bus_type` 填了 `dma_configure`，而 arm64 需要它去读 `dma-ranges` / `dma-coherent` 并挂 IOMMU。DT 路径走 `of_dma_configure()`，ACPI 路径已裁掉（记在 `99-evidence.md` §2.7）
- ⚠️ **`device_initialize()` 里 `dma_coherent = dma_default_coherent` 这一行在 arm64 上生效**（§A4），因为 arm64 定义了 `ARCH_HAS_SYNC_DMA_FOR_DEVICE`
- ⚠️ **fw_devlink 在 arm64 上是主力**：SoC 的 clock/regulator/pinctrl/power-domain 依赖全在 DT 里，`__fw_devlink_relax_cycles()`（`drivers/base/core.c:1936-2029`）每次匹配都在跑。x86 上这条路径的负载小得多

**明确不会走到的**：`sysfs_deprecated` 相关的两条特殊分支（`drivers/base/core.c:3252-3258` 与 `drivers/base/class.c:179`）需要 `CONFIG_SYSFS_DEPRECATED`，arm64 defconfig 没开；`platform_legacy_suspend()` / `platform_legacy_resume()`（`drivers/base/platform.c:1091`、`:1103`）只在驱动填了老式 `suspend`/`resume` 回调时走，现代 arm64 驱动都用 `dev_pm_ops`。

**读完这一篇之后**：子框架（component / auxiliary / devcoredump / class_compat / attribute_container）去 `04-extensions.md`；把上面这些流程翻成可抄的代码骨架去 `05-usage.md`；每一步失败时看什么输出去 `07-debug.md`。
