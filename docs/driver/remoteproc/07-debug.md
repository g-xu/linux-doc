---
title: 调试方法
---

# remoteproc 07：调试手段

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径。所有统计都限定在**核心七个 `.c`**
> （`remoteproc_core.c` / `remoteproc_virtio.c` / `remoteproc_elf_loader.c` /
> `remoteproc_coredump.c` / `remoteproc_sysfs.c` / `remoteproc_debugfs.c` /
> `remoteproc_cdev.c`），不含 34 个平台驱动。每条统计都给了可重跑的命令。

---

## §0 按症状找章节

| 症状 | 去哪一节 |
|---|---|
| 我想先把该开的编译选项开全 | §1 |
| dmesg 里什么都没打，想加日志 | §2（答案是打开动态调试） |
| 我想用 ftrace 看调用序列 | §3（答案是**没有 tracepoint**，只能用 function tracer） |
| 我要看当前状态 / 内存布局 / 资源表 | §4 |
| 我想验证崩溃恢复流程 | §5 |
| 拿到一个错误码，想知道是哪一行返回的 | §6 |
| 我想在用户态写个监控程序 | §7 |
| 给我一页速查 | §8 |

---

## §1 编译期开关

### §1.1 remoteproc 自己的选项

| 选项 | 位置 | 作用 | 调试期建议 |
|---|---|---|---|
| `REMOTEPROC` | `drivers/remoteproc/Kconfig:4` | 总开关 | 必开 |
| `REMOTEPROC_CDEV` | `drivers/remoteproc/Kconfig:17` | `/dev/remoteprocN` | 可选，想用 fd 绑生命周期才需要 |

`REMOTEPROC` **自动 select 四个东西**（`drivers/remoteproc/Kconfig:7-10`）：
`CRC32`、`FW_LOADER`、`VIRTIO`、`WANT_DEV_COREDUMP`。
⇒ 你不需要手工去开固件加载器和 virtio；但 **`WANT_DEV_COREDUMP` 只是"想要"**，
真正的开关是 `CONFIG_DEV_COREDUMP`（在 `drivers/base/Kconfig` 里），
它还依赖 `!PREVENT_FIRMWARE_BUILD` 之类的组合。
⇒ **`coredump` 文件存在 ≠ coredump 能出来**，见 §4.4。

`drivers/remoteproc/Kconfig` 一共 28 个 `config` 项，
其余 26 个都是具体平台驱动。可重跑：
```bash
grep -c '^config ' drivers/remoteproc/Kconfig       # → 28
```

### §1.2 真正决定"能不能看到东西"的三个外部选项

| 选项 | 没开的后果 | 怎么确认 |
|---|---|---|
| `CONFIG_DYNAMIC_DEBUG` | 核心的 **17 处 `dev_dbg`** 全部编译成空 | `ls /sys/kernel/debug/dynamic_debug/control` |
| `CONFIG_DEBUG_FS` | §4 那六个文件全部不存在 | `mount \| grep debugfs` |
| `CONFIG_DEV_COREDUMP` | `dev_coredumpv/m()` 变成空实现，coredump 静默丢弃 | `ls /sys/class/devcoredump/` |

「17 处 `dev_dbg`」的分布（可重跑，见 §2.2）：
`remoteproc_core.c` 10 处、`remoteproc_virtio.c` 5 处、`remoteproc_elf_loader.c` 2 处。

### §1.3 ⚠️ 没有专属的 DEBUG 选项

`drivers/remoteproc/Kconfig` 里**没有 `REMOTEPROC_DEBUG` 这种东西**。
和 i2c 的 `I2C_DEBUG_CORE`、clk 的 `COMMON_CLK_DEBUG` 不一样 ——
remoteproc 把可观测性全部押在 **debugfs + 动态调试**上。
⇒ 这也解释了为什么 debugfs 里的 `resource_table` / `carveout_memories` 那么重要（§4.3）。

---

## §2 日志：可见性等级与分布

### §2.1 全部是"每次都打"，没有一处限流

| 文件 | `dev_err`/`pr_err` | `dev_warn` | `dev_info` | `dev_dbg` | `_once` | `_ratelimited` |
|---|---|---|---|---|---|---|
| `drivers/remoteproc/remoteproc_core.c` | 66 | 5 | 9 | 10 | **0** | **0** |
| `drivers/remoteproc/remoteproc_virtio.c` | 7 | 3 | 1 | 5 | 0 | 0 |
| `drivers/remoteproc/remoteproc_elf_loader.c` | 19 | 0 | 0 | 2 | 0 | 0 |
| `drivers/remoteproc/remoteproc_coredump.c` | 3 | 0 | 1 | 0 | 0 | 0 |
| `drivers/remoteproc/remoteproc_sysfs.c` | 5 | 0 | 0 | 0 | 0 | 0 |
| `drivers/remoteproc/remoteproc_debugfs.c` | 2 | 0 | 0 | 0 | 0 | 0 |
| `drivers/remoteproc/remoteproc_cdev.c` | 4 | 0 | 0 | 0 | 0 | 0 |
| **合计** | **106** | **8** | **11** | **17** | **0** | **0** |

可重跑：
```bash
for f in core virtio debugfs sysfs elf_loader coredump cdev; do
  printf "%-12s err=%s warn=%s info=%s dbg=%s once=%s rl=%s\n" $f \
   $(grep -c 'dev_err\|pr_err'   drivers/remoteproc/remoteproc_$f.c) \
   $(grep -c 'dev_warn\|pr_warn' drivers/remoteproc/remoteproc_$f.c) \
   $(grep -c 'dev_info\|pr_info' drivers/remoteproc/remoteproc_$f.c) \
   $(grep -c 'dev_dbg\|pr_debug' drivers/remoteproc/remoteproc_$f.c) \
   $(grep -c '_once'             drivers/remoteproc/remoteproc_$f.c) \
   $(grep -c '_ratelimited'      drivers/remoteproc/remoteproc_$f.c)
done
```

**零 `_once` / 零 `_ratelimited`** 这件事有两个实际后果：

| 后果 | 说明 |
|---|---|
| 好的一面 | 你看到的日志条数**就是事件发生的次数**，可以直接数。不用担心"后面被压掉了" |
| ⚠️ 坏的一面 | 一个反复崩溃的远端（`recovery` 开着）会把 dmesg 刷爆 —— 每轮恢复都完整地打一遍 `crash detected` + 停机 + 重新加载的全套日志 |

⇒ **压不住的时候只能关 `recovery`**：`echo disabled > /sys/class/remoteproc/remoteprocN/recovery`。

对比其它子系统：regulator 那套的 07 篇 §2.0 记录了 `dev_*_once`/`_ratelimited` 混用的情况，
remoteproc 一处都没有。这不是遗漏 —— remoteproc 的事件频率本来就低
（启动/停止/崩溃都是"人为触发或异常"级别的），不需要限流。

### §2.2 三个 `WARN`/`BUG` 的位置

| 宏 | 位置 | 触发条件 |
|---|---|---|
| `WARN_ON(!virt_addr_valid(cpu_addr))` | `drivers/remoteproc/remoteproc_core.c:158` | `rproc_va_to_pa()` 收到一个非线性映射的地址 |
| `WARN_ON(!rproc->clean_table)` | `drivers/remoteproc/remoteproc_core.c:1508` | detach 时 `clean_table` 意外为空（03 §B3.2） |
| `BUG_ON((u32)vdev->features != vdev->features)` | `drivers/remoteproc/remoteproc_virtio.c:280` | virtio feature 位超过 32 位 —— **核心七个 `.c` 里唯一的 `BUG_ON`** |

可重跑：
```bash
grep -n 'WARN_ON\|WARN(\|BUG_ON\|WARN_ONCE' \
  drivers/remoteproc/remoteproc_{core,virtio,elf_loader,coredump,sysfs,debugfs,cdev}.c
```

⚠️`BUG_ON` 是**直接 panic**，不是警告。它在 `rproc_virtio_finalize_features()` 里，
所以"virtio 协商 feature 时内核挂掉"这个现象的第一嫌疑就是这一行 ——
固件声明了 bit 32 以上的 feature。

全核心**没有一处 `dev_err_probe()`**（可重跑：
`grep -c dev_err_probe drivers/remoteproc/remoteproc_core.c` → 0）。
⇒ 探测失败时不会自动带上 `-EPROBE_DEFER` 的静默处理，
所有失败都会实打实地打一条 `dev_err`。

### §2.3 打开动态调试

```sh
# 整个 remoteproc 核心
echo 'file remoteproc_core.c +p' > /sys/kernel/debug/dynamic_debug/control

# 只开 virtio 那部分
echo 'file remoteproc_virtio.c +p' > /sys/kernel/debug/dynamic_debug/control

# 只开资源表处理（最有用的一条）
echo 'func rproc_handle_resources +p' > /sys/kernel/debug/dynamic_debug/control

# 关掉
echo 'file remoteproc_core.c -p' > /sys/kernel/debug/dynamic_debug/control
```

**最值得开的 10 条 `dev_dbg`（全在 `remoteproc_core.c`）**：

| 行 | 打印什么 | 什么时候有用 |
|---|---|---|
| `drivers/remoteproc/remoteproc_core.c:107` | `iommu not present` | 怀疑 IOMMU 配置问题 |
| `drivers/remoteproc/remoteproc_core.c:393` | vring 的 `da` / `qsz` / `align` | **看固件声明了什么 vring** |
| `drivers/remoteproc/remoteproc_core.c:498` | vdev 的 `id` / `dfeatures` / `cfg len` / vring 数 | **看固件声明的 virtio 设备** |
| `drivers/remoteproc/remoteproc_core.c:585` | trace 缓冲的 `da` / `len` | 固件日志出不来时 |
| `drivers/remoteproc/remoteproc_core.c:663` | devmem 映射结果 | 用 `RSC_DEVMEM` 的平台 |
| `drivers/remoteproc/remoteproc_core.c:700` | carveout 的 `va` / `dma` / `len` | 内存分配环节 |
| `drivers/remoteproc/remoteproc_core.c:757` | carveout 的 IOMMU 映射结果 | 有 IOMMU 的平台 |
| `drivers/remoteproc/remoteproc_core.c:840` | carveout 资源项的全部字段 | **和 DTS 对照的最佳位置**（06 §7.3） |
| `drivers/remoteproc/remoteproc_core.c:1040` | 每条资源的 `type` | **看资源表一共有几条、都是什么** |
| `drivers/remoteproc/remoteproc_core.c:1867` | `enter rproc_crash_handler_work` | 崩溃处理工作项有没有真的被调度起来 |

⇒ **调"启动不起来"的标准动作**：
```sh
echo 'func rproc_handle_resources +p'  > /sys/kernel/debug/dynamic_debug/control
echo 'func rproc_handle_carveout +p'   > /sys/kernel/debug/dynamic_debug/control
echo 'func rproc_handle_vdev +p'       > /sys/kernel/debug/dynamic_debug/control
echo 'func rproc_parse_vring +p'       > /sys/kernel/debug/dynamic_debug/control
echo start > /sys/class/remoteproc/remoteproc0/state
dmesg | tail -40
```
这四条覆盖了资源表的整个解析过程 —— 固件声明了什么、核心分配了什么，一目了然。

---

## §3 tracepoint：一个都没有

### §3.1 结论与证据

| 检查 | 结果 |
|---|---|
| `include/trace/events/` 下有 rproc / remoteproc 相关头文件吗 | **0 个** |
| `drivers/remoteproc/` 里有 `TRACE_EVENT` / `DECLARE_EVENT_CLASS` / `CREATE_TRACE_POINTS` 吗 | **0 处** |
| `include/linux/remoteproc.h` 里有吗 | **0 处** |

可重跑：
```bash
ls include/trace/events/ | grep -ci 'rproc\|remoteproc'          # → 0
grep -rn 'CREATE_TRACE_POINTS\|TRACE_EVENT\|DECLARE_EVENT_CLASS' \
     drivers/remoteproc/ include/linux/remoteproc.h              # → 无输出
```

⚠️ **注意区分两个"trace"**：`drivers/remoteproc/` 里确实到处是 `trace_`，
但那些全部是**固件的 trace 缓冲**（`trace_mem` / `trace_file` /
`rproc_create_trace_file`，04 §7），和 ftrace 的 tracepoint 毫无关系。
这是本子系统一个容易误判的命名撞车 ——
`grep trace_ drivers/remoteproc/` 会给出一堆假阳性。

### §3.2 没有 tracepoint 怎么办：用 function tracer

```sh
cd /sys/kernel/tracing

# 1. 只跟踪 remoteproc 的函数
echo 'rproc_*' > set_ftrace_filter
echo function_graph > current_tracer
echo 1 > tracing_on
echo start > /sys/class/remoteproc/remoteproc0/state
echo 0 > tracing_on
cat trace | head -100

# 2. 想看某个函数的调用者
echo rproc_boot > set_graph_function
echo 1 > options/funcgraph-proc
```

`function_graph` 在这里特别合适，因为 remoteproc 的启动流程是
一条很深的调用链（03 §A 的时间线），graph 能把嵌套关系直接画出来。

想连 mailbox 和 virtio 一起看：
```sh
echo 'rproc_*'  > set_ftrace_filter
echo 'mbox_*'  >> set_ftrace_filter
echo 'vring_*' >> set_ftrace_filter
```

### §3.3 kprobe：给关键点加临时探针

没有 tracepoint 的情况下，kprobe 是唯一能"看到参数值"的手段：

```sh
cd /sys/kernel/tracing

# 看每次 rproc_da_to_va 被查的地址（03 §B4.1）
echo 'p:da2va rproc_da_to_va da=%x1:u64 len=%x2:u64' > kprobe_events
echo 1 > events/kprobes/da2va/enable

# 看崩溃上报的类型（04 §8）
echo 'p:crash rproc_report_crash type=%x1:u32' >> kprobe_events
echo 1 > events/kprobes/crash/enable

cat trace_pipe
```

⚠️ kprobe 只能挂在**没有被内联**的函数上。
`rproc_da_to_va()` 是 `EXPORT_SYMBOL` 的（`drivers/remoteproc/remoteproc_core.c:230`），
所以一定存在符号；但很多 `static` 小函数（如 `rproc_check_carveout_da()`）
可能被内联掉，`echo` 时会报 `-EINVAL`。
⇒ 先 `grep <函数名> /proc/kallsyms` 确认。

---

## §4 sysfs 与 debugfs：11 个文件

### §4.1 全表

| 路径 | 权限 | 读 | 写 | 实现 |
|---|---|---|---|---|
| `/sys/class/remoteproc/remoteprocN/name` | 0444 | 远端名字 | —— | `drivers/remoteproc/remoteproc_sysfs.c:213` |
| `…/state` | RW | `offline`/`running`/`attached`/… | `start`/`stop`/`detach` | `drivers/remoteproc/remoteproc_sysfs.c:178`、`:189` |
| `…/recovery` | RW | `enabled`/`disabled` | `enabled`/`disabled`/`recover` | `drivers/remoteproc/remoteproc_sysfs.c:13`、`:45` |
| `…/coredump` | RW | `disabled`/`enabled`/`inline` | 同左 | `drivers/remoteproc/remoteproc_sysfs.c:80`、`:104` |
| `…/firmware` | RW | 当前固件名 | 新固件名 | `drivers/remoteproc/remoteproc_sysfs.c:131`、`:149` |
| `/sys/kernel/debug/remoteproc/remoteprocN/name` | 0400 | 同 sysfs | —— | `drivers/remoteproc/remoteproc_debugfs.c:407` |
| `…/recovery` | 0600 | 同 sysfs | 同 sysfs | `drivers/remoteproc/remoteproc_debugfs.c:409` |
| `…/coredump` | 0600 | 同 sysfs | 同 sysfs | `drivers/remoteproc/remoteproc_debugfs.c:417` |
| `…/crash` | 0200 | —— | 一个整数（崩溃类型） | `drivers/remoteproc/remoteproc_debugfs.c:411` |
| `…/resource_table` | 0400 | 资源表全文 | —— | `drivers/remoteproc/remoteproc_debugfs.c:413` |
| `…/carveout_memories` | 0400 | carveout 列表 | —— | `drivers/remoteproc/remoteproc_debugfs.c:415` |
| `…/traceN` | 0400 | 固件日志 | —— | `drivers/remoteproc/remoteproc_debugfs.c:389`（固件带 `RSC_TRACE` 才有） |

三个重名文件（`name` / `recovery` / `coredump`）在两边**语义完全一致，但写入解析不同**：
sysfs 用 `sysfs_streq()`（忽略换行、必须整串相等），
debugfs 用 `strncmp(buf, "…", count)`（**前缀匹配**，04 §5.2）。
⇒ 脚本里统一用 sysfs 那一套更稳。

### §4.2 可写文件的 setter 语义（逐个核过）

| 文件 | 写 `enabled` 的副作用 | 状态门 |
|---|---|---|
| sysfs `recovery` | **顺带触发一次恢复**（`drivers/remoteproc/remoteproc_sysfs.c:54`） | 无 |
| debugfs `recovery` | 同样触发（`drivers/remoteproc/remoteproc_debugfs.c:230`） | 无 |
| sysfs `coredump` | 无副作用 | `RPROC_CRASHED` → `-EBUSY`（`drivers/remoteproc/remoteproc_sysfs.c:110-112`） |
| debugfs `coredump` | 无副作用 | 同样 `-EBUSY`（`drivers/remoteproc/remoteproc_debugfs.c:90-93`） |
| sysfs `state` | 直接调 `rproc_boot()` / `rproc_shutdown()` / `rproc_detach()` | 由那三个函数各自判 |
| sysfs `firmware` | 调 `rproc_set_firmware()` | 远端非 offline 时失败 |

⚠️ **`recovery` 写 `enabled` 的副作用容易踩**：
以为只是"打开开关"，结果立刻触发了一次恢复。
如果此刻状态不是 `RPROC_CRASHED`，`rproc_trigger_recovery()` 会在
`drivers/remoteproc/remoteproc_core.c:1840` 那个复查处安静退出（无害），
但**如果恰好是 CRASHED，远端就被重启了**。
⇒ 只想"打开开关不动远端"？做不到 —— 这是源码写死的（03 §A9）。

### §4.3 两个只读视图：把配置和现实对起来

```sh
cd /sys/kernel/debug/remoteproc/remoteproc0/

cat carveout_memories
# 逐项打 name / va / dma / da / len
#   ← dma 列 == DTS reserved-memory 里的物理地址？（06 §7.3）
#   ← da  列 == 固件链接脚本里的地址？

cat resource_table
# 按 type 打印每条资源
#   ← vdev 那条的 vring da 不该是 0
#   ← notifyid 该是 0 和 1（04 §1.3）
```

| 实现 | 位置 | 认识哪些类型 |
|---|---|---|
| `rproc_carveouts_show()` | `drivers/remoteproc/remoteproc_debugfs.c:362` | —— |
| `rproc_rsc_table_show()` | `drivers/remoteproc/remoteproc_debugfs.c:275` | 只认四种：`carveout`/`devmem`/`trace`/`vdev`（`drivers/remoteproc/remoteproc_debugfs.c:277`） |

⚠️`resource_table` **看不到厂商资源**（`RSC_VENDOR_START` 以上走 `switch` 的 default）。
⇒ 用厂商资源的平台（高通、TI 的某些型号）在这个文件里会"少几条"，不是文件坏了。

`resource_table` 读的是 `rproc->table_ptr`（`drivers/remoteproc/remoteproc_debugfs.c:279`），
也就是 03 §B3 那三个指针里的"当前生效的那一份"：

| 远端状态 | `table_ptr` 指向 | 你在文件里看到的 |
|---|---|---|
| offline | `cached_table`（内核里的副本） | 固件原始声明 + 部分回写 |
| running | **远端内存里那一份** | 内核已经完整回写过的值 |
| 无资源表 | NULL | `No resource table found`（`drivers/remoteproc/remoteproc_debugfs.c:287`） |

⇒ **必须在 running 状态下读**才能看到回写后的 vring 地址。offline 时读到的是半成品。

### §4.4 ⚠️ coredump：文件在、不一定能出 dump

`coredump` 文件由 `rproc_create_debug_dir()` / `rproc_attrs[]` 无条件创建，
但真正产生 dump 需要四个条件同时满足：

| 条件 | 检查点 |
|---|---|
| 1. `CONFIG_DEV_COREDUMP` 开着 | §1.2 |
| 2. `dump_conf != RPROC_COREDUMP_DISABLED` | `drivers/remoteproc/remoteproc_coredump.c:247-249` |
| 3. `dump_segments` 非空（驱动调过 `rproc_coredump_add_segment()`） | 同上 |
| 4. `elf_class != ELFCLASSNONE`（驱动调过 `rproc_coredump_set_elf_info()`） | `drivers/remoteproc/remoteproc_coredump.c:251-254` |

条件 3、4 **都要驱动配合**，本树只有高通和 ST 两家做全了（04 §3.1）。
⇒ `cat coredump` 显示 `enabled` 但崩溃后 `/sys/class/devcoredump/` 里什么都没有，
**先看 dmesg 有没有 `Elf class is not set`**，那就是条件 4 没满足；
一句话都没有则是条件 3 没满足（`list_empty` 那条门不打日志）。

---

## §5 故障注入：验证恢复流程

### §5.1 `crash` 文件是唯一入口

```sh
cd /sys/kernel/debug/remoteproc/remoteproc0/

echo 0 > crash    # mmufault
echo 1 > crash    # watchdog
echo 2 > crash    # fatal error
echo 99 > crash   # 越界也安全，dmesg 打 "type unknown"
```

| 事实 | 位置 |
|---|---|
| 写入解析 `kstrtouint_from_user()` | `drivers/remoteproc/remoteproc_debugfs.c:259` |
| 原样交给 `rproc_report_crash()`，**不校验** | `drivers/remoteproc/remoteproc_debugfs.c:263` |
| 校验在打印侧：`type < ARRAY_SIZE(rproc_crash_names)` | `drivers/remoteproc/remoteproc_core.c:71` |
| 越界时返回 `"unknown"` | `drivers/remoteproc/remoteproc_core.c:72` |
| 三个名字 `mmufault` / `watchdog` / `fatal error` | `drivers/remoteproc/remoteproc_core.c:62-66` |

### §5.2 一次注入之后应该看到的日志序列

```
rproc: crash detected in <name>: type fatal error     ← remoteproc_core.c:2687-2688
rproc: handling crash #1 in <name>                    ← remoteproc_core.c:1884-1885
rproc: recovering <name>                              ← rproc_boot_recovery()
rproc: stopped remote processor <name>
rproc: powering up <name>
rproc: Booting fw image <fw>, size <n>
rproc: remote processor <name> is now up
```

| 关键行 | 位置 |
|---|---|
| `crash detected in %s: type %s` | `drivers/remoteproc/remoteproc_core.c:2687-2688` |
| `handling crash #%u in %s` | `drivers/remoteproc/remoteproc_core.c:1884-1885` |
| 计数器 `rproc->crash_cnt` 就在这里自增 | `drivers/remoteproc/remoteproc_core.c:1884` |

**`crash_cnt` 没有对应的 sysfs 文件** —— 只能从 dmesg 里读那个 `#N`。
想在监控里统计崩溃次数，只能解析日志（§7.2）。

### §5.3 ⚠️ 两个"注入了但没恢复"的情况

| 情况 | 原因 | 判据 |
|---|---|---|
| `recovery` 是 `disabled` | `drivers/remoteproc/remoteproc_core.c:1889` 的 `if (!rproc->recovery_disabled)` | dmesg 有 `handling crash` 但没有 `recovering` |
| 远端已经是 `RPROC_CRASHED` | `drivers/remoteproc/remoteproc_core.c:1871-1874` 直接 `return`（只处理第一次崩溃） | 第二次 `echo 2 > crash` 完全没反应 |

⚠️ 第二种情况还带一个副作用：那条 `return` 路径**跳过了 `pm_relax()`**
（对比 `RPROC_OFFLINE` 分支用的是 `goto out`，
`drivers/remoteproc/remoteproc_core.c:1880`；`pm_relax()` 在
`drivers/remoteproc/remoteproc_core.c:1893`）。
⇒ 每次"重复崩溃上报"都会**泄漏一次 wakeup source 引用**
（`pm_stay_awake()` 在 `drivers/remoteproc/remoteproc_core.c:2685`）。
表现是系统之后再也进不了 suspend。这是 03 §C3 记录的设计债。

想连续注入两次，中间必须先让远端回到 running：
```sh
echo 2 > crash
sleep 2                     # 等恢复完成
cat /sys/class/remoteproc/remoteproc0/state   # 确认是 running
echo 2 > crash
```

### §5.4 其它可以注入的失败

| 想验证什么 | 怎么造 |
|---|---|
| 固件不存在 | `echo nonexistent.elf > …/firmware` 然后 `echo start > …/state` |
| 固件损坏 | 给一个非 ELF 文件 —— 会命中 `Image is corrupted (bad magic)`（`drivers/remoteproc/remoteproc_elf_loader.c:65`） |
| 固件 class 不支持 | 32 位内核喂 64 位 ELF —— `Unsupported class: %d`（`drivers/remoteproc/remoteproc_elf_loader.c:71`） |
| 资源表坏 | 改固件里的资源表版本号 —— `unsupported fw ver: %d`（`drivers/remoteproc/remoteproc_elf_loader.c:294`） |
| vring 参数为 0 | 改资源表里的 `num`/`align` —— `invalid qsz (%d) or alignment (%d)`（`drivers/remoteproc/remoteproc_core.c:396-400`） |

ELF 加载器是**校验最严的一环**：19 处 `dev_err`（§2.1），
几乎每一种畸形都有专属报错。⇒ 固件层面的问题基本都能从 dmesg 一句话定位。

---

## §6 错误码分诊

### §6.1 全量普查

核心七个 `.c` 里 `-E*` 一共出现 **96 次**，其中 **2 次在函数文档注释里**
（`drivers/remoteproc/remoteproc_core.c:2554` 和
`drivers/remoteproc/remoteproc_elf_loader.c:35`），
所以**实际的返回/赋值点是 94 处**。

| 错误码 | 出现次数 | 其中注释 | 实际点数 |
|---|---|---|---|
| `-EINVAL` | 59 | 2 | **57** |
| `-ENOMEM` | 26 | 0 | 26 |
| `-EFAULT` | 5 | 0 | 5 |
| `-EBUSY` | 3 | 0 | 3 |
| `-EOVERFLOW` | 1 | 0 | 1 |
| `-ENOSYS` | 1 | 0 | 1 |
| `-ENODEV` | 1 | 0 | 1 |
| **合计** | **96** | **2** | **94** |

可重跑：
```bash
F="drivers/remoteproc/remoteproc_core.c drivers/remoteproc/remoteproc_virtio.c
   drivers/remoteproc/remoteproc_elf_loader.c drivers/remoteproc/remoteproc_coredump.c
   drivers/remoteproc/remoteproc_sysfs.c drivers/remoteproc/remoteproc_debugfs.c
   drivers/remoteproc/remoteproc_cdev.c"
grep -oh '\-E[A-Z_]*' $F | sort | uniq -c | sort -rn
grep -n '^[[:space:]]*\*.*-E[A-Z]' $F      # 找出注释里的那两处
```

按文件分布：

| 文件 | 总数 | `-EINVAL` | `-ENOMEM` | `-EFAULT` | 其它 |
|---|---|---|---|---|---|
| `drivers/remoteproc/remoteproc_core.c` | 52 | 31 | 18 | 0 | `-EBUSY` 1 · `-ENOSYS` 1 · `-ENODEV` 1 |
| `drivers/remoteproc/remoteproc_elf_loader.c` | 16 | 14 | 1 | 0 | `-EOVERFLOW` 1 |
| `drivers/remoteproc/remoteproc_virtio.c` | 8 | 3 | 5 | 0 | —— |
| `drivers/remoteproc/remoteproc_debugfs.c` | 7 | 4 | 0 | 2 | `-EBUSY` 1 |
| `drivers/remoteproc/remoteproc_cdev.c` | 6 | 3 | 0 | 3 | —— |
| `drivers/remoteproc/remoteproc_sysfs.c` | 4 | 3 | 0 | 0 | `-EBUSY` 1 |
| `drivers/remoteproc/remoteproc_coredump.c` | 3 | 1 | 2 | 0 | —— |

⚠️ **`-EINVAL` 占 61%**（57/94）。
这意味着"拿到 `-EINVAL`"这个信息量几乎为零 —— 必须靠**配套的 dmesg 一句话**定位。
下面 §6.2 把 `remoteproc_core.c` 那 30 个实际 `-EINVAL` 点按语义分了组。

### §6.2 `remoteproc_core.c` 的 30 处 `-EINVAL` 按语义分组

（31 次出现减去 `drivers/remoteproc/remoteproc_core.c:2554` 那处注释 = 30 个实际点）

**组 A：资源表条目的边界检查（14 处）** —— 都在 `rproc_handle_*()` 家族里

| 函数 | 行号 | 检查什么 | 有日志？ |
|---|---|---|---|
| `rproc_check_carveout_da()` | `:299`、`:304`、`:312`、`:318` | carveout 的 `da` 是否落在已有区间内 | **`:307` 那条静默**（03 §B4.2） |
| `rproc_parse_vring()` | `:400` | `num` / `align` 为 0 | 有：`invalid qsz (%d) or alignment (%d)` |
| `rproc_handle_vdev()` | `:489`、`:495`、`:504` | 长度截断 / 保留字节 / vring 数超限 | 三条都有 |
| `rproc_handle_trace()` | `:555`、`:561` | 长度截断 / 保留字节 | 有 |
| `rproc_handle_devmem()` | `:629`、`:633`、`:639` | 无 IOMMU / 长度截断 / 保留字节 | 有 |
| `rproc_handle_carveout()` | `:831`、`:837` | 长度截断 / 保留字节 | 有 |

这一组的共同形状：`if (sizeof(*rsc) > avail)` + `if (rsc->reserved)`。
⇒ **"保留字节非零"是最常见的固件端错误** ——
固件的资源表结构体定义和内核的 `include/linux/remoteproc.h` 版本不一致就会命中。

**组 B：资源表整体遍历（1 处）**

| 行号 | 检查什么 |
|---|---|
| `:1037` | `avail < 0` —— 资源表长度算下来是负的 |

**组 C：回调缺失（3 处）** —— 最值得记住的一组

| 行号 | 缺哪个回调 | 什么时候撞上 |
|---|---|---|
| `:1712` | `ops->stop` | `rproc_shutdown()` 或恢复流程 |
| `:1751` | `ops->detach` | `rproc_detach()` |
| `:2221` | `ops->start`（`RPROC_OFFLINE` 状态下） | `rproc_add()` 直接失败 |
| `:2229` | `ops->attach`（`RPROC_DETACHED` 状态下） | `rproc_add()` 直接失败 |

⇒ **这四处都不打日志**（`rproc_validate()` 全程静默，
`rproc_stop()` / `__rproc_detach()` 也只是 `return -EINVAL`）。
⇒ **`rproc_add()` 返回 `-EINVAL` 且 dmesg 一句话都没有 ⇒ 就是回调没填**（05 §1.1）。
这是本子系统最高频的新手错误，而且**它没有任何日志线索**。

**组 D：`rproc_validate()` 的状态检查（另 2 处）**

| 行号 | 检查什么 |
|---|---|
| `:2236` | `RPROC_DETACHED` 状态下 `cached_table` 不为空 —— attach 时不该有缓存副本（03 §B3.3） |
| `:2243` | 状态既不是 `OFFLINE` 也不是 `DETACHED` |

**组 E：空指针防御（3 处）**

| 行号 | 函数 |
|---|---|
| `:1915` | `rproc_boot()` 的 `!rproc` |
| `:2000` | `rproc_shutdown()` |
| `:2065` | `rproc_detach()` |
| `:2559` | `rproc_del()` 的 `!rproc` |

**组 F：`rproc_set_firmware()`（3 处）**

| 行号 | 检查什么 | 配套日志 |
|---|---|---|
| `:2174` | `!rproc \|\| !fw_name` | 有 |
| `:2181` | 名字为空串 | 有 |
| `:2193` | kstrdup 之后的处理 | 有 |

**组 G：detach 时的一致性（1 处）**

| 行号 | 检查什么 |
|---|---|
| `:1509` | `WARN_ON(!rproc->clean_table)` 之后返回（03 §B3.2） |

### §6.3 三个"只有一处"的错误码

| 错误码 | 位置 | 语义 | ⚠️ 陷阱 |
|---|---|---|---|
| `-ENOSYS` | `drivers/remoteproc/remoteproc_core.c:97` | IOMMU 域分配失败 | 源码注释说它是"恢复触发器"—— 03 §B5 |
| `-ENODEV` | `drivers/remoteproc/remoteproc_core.c:1927` | `rproc_boot()` 里 `try_module_get()` 失败 | 驱动模块正在被卸载 |
| `-EOVERFLOW` | `drivers/remoteproc/remoteproc_elf_loader.c:207` | 段大小放不进 `size_t` | 这是 32 位内核加载大固件时的真实保护（03 §C5） |

### §6.4 `-EFAULT` 全部来自用户态拷贝（5 处）

| 位置 | 哪个接口 |
|---|---|
| `drivers/remoteproc/remoteproc_debugfs.c:84` | debugfs `coredump` 写 |
| `drivers/remoteproc/remoteproc_debugfs.c`（另一处） | debugfs `recovery` 写 |
| `drivers/remoteproc/remoteproc_cdev.c:32` | cdev `write` |
| `drivers/remoteproc/remoteproc_cdev.c:57` | cdev `SET_SHUTDOWN_ON_RELEASE` |
| `drivers/remoteproc/remoteproc_cdev.c:64` | cdev `GET_SHUTDOWN_ON_RELEASE` |

看到 `-EFAULT` 就是**你的用户态程序传了坏指针**，和远端无关。

---

## §7 用户态工具

### §7.1 没有官方工具

⚠️ remoteproc **没有 `remoteproctl` 这类官方 CLI**（对比 i2c 的 `i2c-tools`、
gpio 的 `gpiod`）。所有操作都是 `echo` / `cat` / 一个自己写的 ioctl 小程序（05 §5）。

相关的用户态生态在**远端那一侧**：OpenAMP 项目提供固件侧的 RPMsg / virtio 实现，
但那不在内核树里。

### §7.2 一个可用的监控脚本

```sh
#!/bin/sh
# 监控所有远端的状态和崩溃次数
for d in /sys/class/remoteproc/remoteproc*; do
	n=$(basename "$d")
	printf "%-14s name=%-20s state=%-10s recovery=%-9s coredump=%s\n" \
		"$n" "$(cat "$d/name")" "$(cat "$d/state")" \
		"$(cat "$d/recovery")" "$(cat "$d/coredump")"
done

# 崩溃次数只能从 dmesg 里数（没有 sysfs 计数器，§5.2）
echo "--- crash history ---"
dmesg | grep -E 'handling crash #' | tail -20
```

### §7.3 判断"远端到底活着没有"的三层判据

| 层 | 命令 | 说明 |
|---|---|---|
| 1. 内核认为它在跑 | `cat /sys/class/remoteproc/remoteprocN/state` | `running` 只说明 `ops->start` 成功返回了 |
| 2. 通信通道建起来了 | `ls /sys/bus/rpmsg/devices/` | 有条目说明远端回应了 virtio 协商 |
| 3. 远端真的在执行代码 | `cat /sys/kernel/debug/remoteproc/remoteprocN/trace0` | **唯一的直接证据** —— 固件自己打的日志 |

⚠️ 第 1 层最容易误判：`state` 显示 `running` **完全不代表固件在跑**。
`ops->start` 只是"放开复位"，固件死在第一条指令上，内核这边照样是 `running`。
⇒ **有 `trace0` 就一定先看 `trace0`**；没有的话看第 2 层。

---

## §8 一页速查

```
┌─ 先看状态 ────────────────────────────────────────────────────┐
│ cat /sys/class/remoteproc/remoteproc0/state                   │
│ cat /sys/class/remoteproc/remoteproc0/name                    │
└───────────────────────────────────────────────────────────────┘

┌─ 启停 ────────────────────────────────────────────────────────┐
│ echo start  > /sys/class/remoteproc/remoteproc0/state         │
│ echo stop   > /sys/class/remoteproc/remoteproc0/state         │
│ echo detach > /sys/class/remoteproc/remoteproc0/state         │
│ echo fw.elf > /sys/class/remoteproc/remoteproc0/firmware      │
└───────────────────────────────────────────────────────────────┘

┌─ 三个只在 debugfs 的文件 ─────────────────────────────────────┐
│ cd /sys/kernel/debug/remoteproc/remoteproc0/                  │
│ cat resource_table         ← 内核回写了什么（必须 running 时读）│
│ cat carveout_memories      ← DTS 的地址生效了吗                │
│ cat trace0                 ← 固件自己的日志（最直接的证据）    │
│ echo 2 > crash             ← 注入一次崩溃                      │
└───────────────────────────────────────────────────────────────┘

┌─ 调"启动不起来"的四条动态调试 ────────────────────────────────┐
│ C=/sys/kernel/debug/dynamic_debug/control                     │
│ echo 'func rproc_handle_resources +p' > $C                    │
│ echo 'func rproc_handle_carveout  +p' > $C                    │
│ echo 'func rproc_handle_vdev      +p' > $C                    │
│ echo 'func rproc_parse_vring      +p' > $C                    │
└───────────────────────────────────────────────────────────────┘

┌─ 没有 tracepoint，用 function_graph ──────────────────────────┐
│ cd /sys/kernel/tracing                                        │
│ echo 'rproc_*' > set_ftrace_filter                            │
│ echo function_graph > current_tracer                          │
└───────────────────────────────────────────────────────────────┘
```

### §8.1 错误码 → 第一嫌疑

| 拿到 | 先查 |
|---|---|
| `rproc_add()` → `-EINVAL`，**无日志** | `ops->start`（或 attach 场景的 `ops->attach`）没填（§6.2 组 C） |
| `-EINVAL` + `non zero reserved bytes` | 固件资源表结构体版本和内核不一致（§6.2 组 A） |
| `-EINVAL` + `Image is corrupted (bad magic)` | 固件不是 ELF |
| `-EINVAL` + `.kick method not defined` | `ops->kick` 没填（04 §1.5） |
| `-ENOMEM` | carveout 分配失败 —— 看 reserved-memory 够不够大 |
| `-EBUSY` | 远端状态不对（`firmware` 要 offline、`coredump` 不能在 CRASHED） |
| `-ENOSYS` | IOMMU 域分配失败（`drivers/remoteproc/remoteproc_core.c:97`） |
| `-ENODEV` | 驱动模块正在卸载（`drivers/remoteproc/remoteproc_core.c:1927`） |
| `-EOVERFLOW` | 32 位内核 + 大固件（`drivers/remoteproc/remoteproc_elf_loader.c:207`） |
| `-EFAULT` | 你的用户态程序传了坏指针，与远端无关 |
| **什么都没报但 rpmsg 不通** | vdev buffer 落错了内存（06 §3.3 分支 C） |

---

**下一篇**：08 篇用大白话和类比解释这个子系统里最难理解的几组关系。
