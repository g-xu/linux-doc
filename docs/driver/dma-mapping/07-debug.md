---
title: 调试方法
---

# dma-mapping 调试手段

⚠️ **本篇所有命令都没有在真机上跑过。** 文件路径、内核参数名、日志字符串都是从本树源码逐字取出来的，但示例输出一律没有编造——凡是没有真实输出的地方写的是「格式见源码第几行」而不是假造一段。

## §0 按症状选工具

| 症状 | 先看 | 需要重编内核？ | 本篇 |
|---|---|---|---|
| 映射 / 解映射不配对、大小方向不一致 | `CONFIG_DMA_API_DEBUG` 的 `WARN` | ⚠️ 要 | §2 §4 |
| 忘了 `dma_mapping_error()` | 同上（`kernel/dma/debug.c:1037-1043`） | ⚠️ 要 | §4 |
| 栈上 / vmalloc / rodata 缓冲做 DMA | 同上（`kernel/dma/debug.c:1068`/`:1078`/`:1088`/`:1203`） | ⚠️ 要 | §4 |
| 怀疑在走 bounce（性能突然掉） | `/sys/kernel/debug/swiotlb/io_tlb_used` | 不要（只要 `DEBUG_FS`） | §3.2 |
| 想看每一次 bounce 的地址 | `swiotlb_bounced` tracepoint | 不要 | §5 |
| `dma_alloc_coherent(GFP_ATOMIC)` 返回 `NULL` | `/sys/kernel/debug/dma_pools/` + `Failed to get suitable pool` | 不要 | §3.3 §6 |
| CMA 分配失败 / 碎片 | `/sys/kernel/debug/cma/<name>/` | ⚠️ 要开 `CMA_DEBUGFS` | §3.4 |
| dts 里的池没建起来 | 启动期 dmesg | 不要 | §8，详见 06 §9 |
| 想量 map/unmap 的开销 | `dma_map_benchmark` | ⚠️ 要 | §9 |
| 某个设备的掩码 / `dma_range_map` 到底是什么 | ⚠️ **没有 sysfs 出口**，只能 crash 工具或加打印 | — | §3.5 |

⚠️ **这个子系统的调试出口有一条很硬的分界线：`DMA_API_DEBUG` 是唯一能查出"驱动用错了 API"的手段，而它必须重编内核。** 生产内核上只有 §3 那几个计数器和 §5 那一个 tracepoint 可用，它们能告诉你"发生了什么"，不能告诉你"谁写错了"。

---

## §1 编译期开关

| Kconfig | 位置 | 作用 | 代价 |
|---|---|---|---|
| `DMA_API_DEBUG` | `kernel/dma/Kconfig:206-218` | 全套 API 使用检查，见 §2 | ⚠️ help 文本原文写 "This option causes a performance degradation"（`:215`） |
| `DMA_API_DEBUG_SG` | `kernel/dma/Kconfig:220-235` | 额外检查 sg 段长度 / 边界是否符合设备声明的限制 | `default y`（`:222`），但 `depends on DMA_API_DEBUG`（`:223`） |
| `DMA_MAP_BENCHMARK` | `kernel/dma/Kconfig:237-244` | `/sys/kernel/debug/dma_map_benchmark`，见 §9 | `depends on DEBUG_FS`（`:239`） |
| `DEBUG_FS` | `lib/Kconfig.debug` | §3 全部 debugfs 出口的总开关 | 小 |
| `CMA_DEBUGFS` | `mm/Kconfig:870-874` | `/sys/kernel/debug/cma/`，见 §3.4 | ⚠️ **不属于本子系统**，在 mm 下 |
| `STACKTRACE` | `lib/Kconfig.debug` | 决定 `DMA_API_DEBUG` 的报告里有没有 "Mapped at:" 调用栈 | 中 |

⚠️ **`STACKTRACE` 关掉的话，`DMA_API_DEBUG` 的报告只告诉你"哪个设备出错了"，不告诉你"这个映射是谁建的"。** 判据在 `kernel/dma/debug.c:166-174`：`dump_entry_trace()` 整个函数体包在 `#ifdef CONFIG_STACKTRACE` 里，关掉就是空函数。`struct dma_debug_entry` 里的 `stack_len` / `stack_entries` 两个字段也一样是条件编译的（`:78-81`）。**开 `DMA_API_DEBUG` 就顺手确认 `STACKTRACE` 也是开的**，否则这个功能只剩一半。

⚠️ **`DMA_API_DEBUG_SG` 是 `default y` 但依赖 `DMA_API_DEBUG`，所以它的实际默认值是"关"**——因为 `DMA_API_DEBUG` 自己 `If unsure, say N`（`:218`）。这一对的形状容易看错成"sg 检查默认开着"。

```bash
# 确认这三项的依赖关系（不是靠记忆）
sed -n '206,244p' kernel/dma/Kconfig
```

---

## §2 `DMA_API_DEBUG`：唯一的 API 使用检查器

**这是 05 §3.1 和 06 §8.3 承诺的"见 07 §2"。**

它的形状是：`kernel/dma/mapping.c` 里每个 API 在做完真正的工作后调一个 `debug_dma_*()` 钩子，钩子把这次映射记进一张哈希表；解映射时反查，对不上就 `WARN`。入口清单在 `kernel/dma/debug.h:12-56`（12 个 `extern`），关掉时同一个头文件的 `:58-129` 给出全部空 `static inline`——**所以关掉之后连一条指令都不剩**。

### §2.1 四道门，全部通过才会看到输出

**这是 06 §8.3 承诺的"见 07 §2.1"。** ⚠️ **开了 Kconfig 不等于会有输出。** 从上到下依次是：

| 门 | 判据 | 位置 | 关掉的表现 |
|---|---|---|---|
| 1. 编译期 | `CONFIG_DMA_API_DEBUG` | `kernel/dma/debug.h:11`/`:57` 的 `#ifdef`/`#else` | 钩子全是空函数 |
| 2. `global_disable` | 初始化失败或命令行关闭时置真 | `kernel/dma/debug.c:99` 定义，`:104-107` 的 `dma_debug_disabled()` | 静默不检查 |
| 3. `dma_debug_initialized` | `dma_debug_init()` 走到最后才置真 | `kernel/dma/debug.c:102` 定义，`:924` 赋值 | ⚠️ **`core_initcall` 之前的所有映射都不被记录** |
| 4. `driver_filter()` | 设了驱动过滤器时只放行该驱动 | `kernel/dma/debug.c:176-215` | 别的驱动的错误被吞掉 |
| 5. `show_num_errors` | 报告配额，默认 **1** | `kernel/dma/debug.c:115` 初值，`:226-227` 递减 | ⚠️ **第二条起就没有输出了** |

⚠️ **第 5 道门是最容易被误判成"没问题"的一道。** `show_num_errors` 初值是 `1`（`kernel/dma/debug.c:115`），而 `err_printk` 在 `:226-227` 里每报一条就减一：

```c
		if (!show_all_errors && show_num_errors > 0)		\
			show_num_errors -= 1;				\
```

**也就是说默认配置下整个系统生命周期只会打印一条 DMA-API 报告。** 想看全部必须先写 `all_errors`：

```bash
echo 1 > /sys/kernel/debug/dma-api/all_errors
```

⚠️ **但 `error_count` 是无条件累加的**（`kernel/dma/debug.c:218`，在配额判断之前）。所以判断"到底有没有问题"要读 `error_count` 而不是数 dmesg 里的 `WARN` 条数——**dmesg 里只有一条、`error_count` 是 3000，这是常态而不是矛盾**。

⚠️ **门 2 和门 3 之间有一个真实的观测盲区。** `dma_debug_init()` 是 `core_initcall`（`kernel/dma/debug.c:929`），而 debugfs 目录是 `core_initcall_sync`（`:823`）建的。在 `core_initcall` 之前完成的 DMA 映射（早期架构代码、某些 `early_initcall` 里的设备）不进哈希表，后面解映射时会被报成"释放没有分配过的内存"——**这是一类假阳性**。

### §2.2 报告长什么样

`err_printk` 的展开在 `kernel/dma/debug.c:217-228`。要点：

| 元素 | 来自 | 说明 |
|---|---|---|
| 前缀 `DMA-API: ` | `kernel/dma/debug.c:8` 的 `#define pr_fmt(fmt) "DMA-API: " fmt` | ⚠️ **grep dmesg 就用这七个字符** |
| `<driver> <device>: ` | `:221-223` 的 `dev_driver_string(dev)` + `dev_name(dev)` | ⚠️ `dev` 为 `NULL` 时两个都打印字面量 `"NULL"` |
| 主体 | 各调用点的 format | 全表见 §4 |
| `Mapped at:` + 调用栈 | `:170-171` | ⚠️ 只在 `CONFIG_STACKTRACE` 下有（§1） |
| `WARN` 的栈 | `:221` 用的是 `WARN(1, ...)` | ⚠️ **不是 `WARN_ONCE`**——每次都打完整 taint 栈，所以配额机制（§2.1 门 5）才是必要的 |

### §2.3 驱动过滤器

**这是 06 §8.3 承诺的"见 07 §2.3"。** 两个入口：

```bash
# 引导参数（在 debugfs 挂载之前就生效）
dma_debug_driver=<驱动名>

# 运行期
echo <驱动名> > /sys/kernel/debug/dma-api/driver_filter
cat /sys/kernel/debug/dma-api/driver_filter      # 读回当前值
```

⚠️ **写入的语义有三条不直观的规则，都在 `filter_write()`（`kernel/dma/debug.c:710-772`）里**：

| 规则 | 代码 | 后果 |
|---|---|---|
| 只取第一个 token，空白字符（`' '`/`'\n'`/`'\t'`）截断 | `:757-761` | `echo "a b"` 只过滤 `a` |
| ⚠️ **首字符不是字母数字就等于关闭过滤器** | `:740-751` 的 `if (!isalnum(buf[0]))` | `echo "" >` / `echo "-" >` 都是"关"，不是"过滤这个名字" |
| 名字上限 63 字节 | `:724` 的 `NAME_MAX_LEN - 1`，`NAME_MAX_LEN` 在 `:126` 是 64 | 超长静默截断 |

关闭时会打印 `switching off dma-debug driver filter`（`kernel/dma/debug.c:747`）；开启时打印 `enable driver filter for driver [%s]`（`:765-766`，引导参数路径在 `:1602-1603` 有一份**逐字相同**的拷贝）。⚠️ **所以看到这条消息不能推断是命令行设的还是运行期写的**——两处字符串一样。

```bash
grep -n 'enable driver filter for driver' kernel/dma/debug.c   # 两行：765 和 1602
```

⚠️ **过滤器对 `dev == NULL` 的错误一律不放行**（`kernel/dma/debug.c:190-192` 直接 `return false`）。所以开了过滤器之后，`__init` 阶段那些没有 `struct device` 的错误会连带被吞掉。

### §2.4 引导参数

| 参数 | 位置 | 语义 |
|---|---|---|
| `dma_debug=off` | `kernel/dma/debug.c:931-942`，`__setup` 在 `:953` | ⚠️ **只认 `off` 三个字母**（`:936` 的 `strncmp(str, "off", 3)`），写别的值等于没写；命中时打印 `debugging disabled on kernel command line`（`:937`） |
| `dma_debug_entries=<n>` | `:944-951`，`__setup` 在 `:954` | 预分配条目数；解析失败回落到 `PREALLOC_DMA_DEBUG_ENTRIES`（`:949`） |
| `dma_debug_driver=<name>` | `:1591-1606`，`__setup` 在 `:1608` | 见 §2.3 |

⚠️ **`dma_debug=on` 不是有效写法。** 它默认就是开的（只要编进去），`dma_debug=` 这个参数只有"关"这一个方向。

### §2.5 条目池会自己长，但会长到耗尽

| 消息 | 位置 | 含义 |
|---|---|---|
| `preallocated %d debug entries` | `kernel/dma/debug.c:912` | 正常启动 |
| `%d debug entries requested but only %d allocated` | `:914-915` | 部分成功，仍然可用 |
| `debugging out of memory error - disabled` | `:917` | ⚠️ 初始化就失败，`global_disable = true`（`:918`）——**整个功能静默失效** |
| `dma_debug_entry pool grown to %u (%u00%%)` | `:619` | 运行期动态扩容，正常 |
| `debugging out of memory - disabling` | `:642` | ⚠️ 运行期扩容失败，从这一刻起不再检查 |
| `cacheline tracking ENOMEM, dma-debug disabled` | `:568`，⚠️ **`pr_err_once`** | 同上，另一条失效路径 |

⚠️ **上面三条"disabled/disabling"意味着后续所有报告都不再出现，但已经打出的报告仍然有效。** 排查长跑问题时先 `cat /sys/kernel/debug/dma-api/disabled` 确认功能还活着——它对应的就是 `global_disable`（`kernel/dma/debug.c:811`）。

⚠️ **`cacheline tracking EEXIST, overlapping mappings aren't supported`（`kernel/dma/debug.c:571-572`）不是失效，是一条真实的错误报告**：同一条 cacheline 被两个映射覆盖。它有一个合法的例外——`DMA_ATTR_SKIP_CPU_SYNC` 会让这个检查跳过（`:570` 的 `!(attrs & DMA_ATTR_SKIP_CPU_SYNC)`）。**dma-buf / V4L2 那类反复映射同一批页的场景看到它，先确认是不是漏了这个属性。**

默认条目数是 `PREALLOC_DMA_DEBUG_ENTRIES`（`kernel/dma/debug.c:33`），值为 `1 << 16` 即 65536；哈希桶数 `HASH_SIZE` 是 `16384ULL`（`:29`）。动态扩容一次的粒度是 `DMA_DEBUG_DYNAMIC_ENTRIES`（`:35`），等于一个 `PAGE_SIZE` 装得下的条目数。

---

## §3 每个框架各自的 debugfs 出口

**这是 06 §8.2 / §8.3 承诺的"见 07 §3"。** ⚠️ **它们是四个互不相干的目录，不在同一个父目录下**，别去找一个统一的 `dma/` 入口。

| 目录 | 由谁建 | 开关 | 本节 |
|---|---|---|---|
| `/sys/kernel/debug/dma-api/` | `kernel/dma/debug.c:809` | `DMA_API_DEBUG` + `DEBUG_FS` | §3.1 |
| `/sys/kernel/debug/swiotlb/` | `kernel/dma/swiotlb.c:979`（默认池名来自 `:990`） | `SWIOTLB` + `DEBUG_FS` | §3.2 |
| `/sys/kernel/debug/dma_pools/` | `kernel/dma/pool.c:40` | `DMA_COHERENT_POOL` + `DEBUG_FS` | §3.3 |
| `/sys/kernel/debug/cma/` | `mm/cma_debug.c:190` | ⚠️ **`CMA_DEBUGFS`**（在 mm 下） | §3.4 |
| `/sys/kernel/debug/dma_map_benchmark` | `kernel/dma/map_benchmark.c:318` | `DMA_MAP_BENCHMARK` | §9 |

### §3.1 `dma-api/`

九个文件，全部建在 `dma_debug_fs_init()`（`kernel/dma/debug.c:807-822`）里：

| 文件 | 权限 | 对应变量 | 读法 |
|---|---|---|---|
| `disabled` | `0444` | `global_disable`（`:811`） | ⚠️ **为 1 就说明检查已经失效**，见 §2.5 |
| `error_count` | `0444` | `error_count`（`:812`） | ⚠️ **累计错误数，不受报告配额影响**（§2.1） |
| `all_errors` | `0644` | `show_all_errors`（`:813`） | ⚠️ **写 1 才能看到第二条以后的报告** |
| `num_errors` | `0644` | `show_num_errors`（`:814`） | 剩余报告配额，默认 1 |
| `num_free_entries` | `0444` | `num_free_entries`（`:815`） | 当前空闲条目 |
| `min_free_entries` | `0444` | `min_free_entries`（`:816`） | ⚠️ **历史最低水位**——逼近 0 说明该加 `dma_debug_entries=` |
| `nr_total_entries` | `0444` | `nr_total_entries`（`:817`） | 含动态扩容后的总数 |
| `driver_filter` | `0644` | `filter_fops`（`:818`） | 见 §2.3 |
| `dump` | `0444` | `dump_fops`（`:819`） | 见下 |

`dump` 的每行格式在 `dump_show()`（`kernel/dma/debug.c:780-804`）里，逐字是 `:792` 的：

```c
			"%s %s %s idx %d P=%llx N=%lx D=%llx L=%llx %s %s\n",
```

| 字段 | 含义 | 取值来源 |
|---|---|---|
| 第 1 个 `%s` | 设备名 | `dev_name()` |
| 第 2 个 `%s` | 驱动名 | `dev_driver_string()` |
| 第 3 个 `%s` | 映射类型 | `type2name[]`（`:139-144`）：`single` / `scather-gather` / `coherent` / `resource` |
| `idx` | 哈希桶下标 | — |
| `P=` | 物理地址 | `phys_addr(entry)` |
| `N=` | 页帧号 | `entry->pfn` |
| `D=` | ⚠️ **DMA 地址** | `entry->dev_addr` |
| `L=` | 长度 | `entry->size` |
| 第 4 个 `%s` | 方向 | `dir2name[]`（`:146-151`） |
| 第 5 个 `%s` | 有没有查过 `dma_mapping_error()` | `maperr2str[]`（`:133-137`） |

⚠️ **`type2name[]` 里的 `scather-gather` 是源码里的拼写错误**（正确拼法是 `scatter`）。grep 这个字符串时必须照抄错的那个：

```bash
grep -n 'scather-gather' kernel/dma/debug.c    # 唯一一行：142
```

⚠️ **`dump` 是全量遍历 16384 个哈希桶、逐桶持锁**（`:784-802`），在高负载机器上 `cat` 它会有可观的延迟抖动。别在性能测量的同时读它。

### §3.2 `swiotlb/`：判断有没有在 bounce

两个文件，建在 `swiotlb_create_debugfs_files()`（`kernel/dma/swiotlb.c:976-986`）：

| 文件 | 权限 | 含义 |
|---|---|---|
| `io_tlb_nslabs` | `0400` | 池的总槽位数（`:983`） |
| `io_tlb_used` | `0400` | ⚠️ **当前占用槽位数**（`:984-985`，取值函数 `io_tlb_used_get()` 在 `:967-973`，格式 `"%llu\n"` 见 `:974`） |

```bash
# 有没有在 bounce：跑负载前后各读一次
cat /sys/kernel/debug/swiotlb/io_tlb_used
cat /sys/kernel/debug/swiotlb/io_tlb_nslabs
```

⚠️ **`io_tlb_used` 是瞬时占用量，不是累计次数。** 短促的 bounce 峰值读不到——**要数"发生过多少次"必须用 §5 的 tracepoint**。

⚠️ **权限是 `0400`，只有 root 能读。** 而且 `:980-981` 有一道门：`if (!mem->nslabs) return;` ——**池为空时目录建了但里面是空的**，看到空目录不等于 debugfs 坏了。

⚠️ **受限池（`restricted-dma-pool`）的目录名是 dts 节点名而不是 `swiotlb`**：`:1076` 传的是 `rmem->name`，而且它是**建在默认池的目录下面**（`:979` 的父目录是 `io_tlb_default_mem.debugfs`）。06 §6 描述了这类池怎么来。

### §3.3 `dma_pools/`：原子池水位

三个文件，全部 `0400`，建在 `dma_atomic_pool_debugfs_init()`（`kernel/dma/pool.c:36-44`）：

| 文件 | 变量 | 说明 |
|---|---|---|
| `pool_size_dma` | `pool_size_dma`（`:41`） | `GFP_DMA` 池当前大小 |
| `pool_size_dma32` | `pool_size_dma32`（`:42`） | `GFP_DMA32` 池 |
| `pool_size_kernel` | `pool_size_kernel`（`:43`） | ⚠️ **arm64 普通设备用的就是这个** |

⚠️ **这三个是"池有多大"，不是"用了多少"。** 池会自己扩容（04 §4 讲了扩容路径），所以**数值只增不减，看它变大就说明原子分配压力在涨**。真正的失败信号是 §6 那条 `Failed to get suitable pool`。

⚠️ **`dma_atomic_pool_debugfs_init()` 在 `:219` 被调用，而它自己在 `:36` 的定义带 `__init`** ——所以池扩容之后这几个文件不会新增，只是数值变。

### §3.4 `cma/`：CMA 区的碎片情况

⚠️ **这一组不属于本子系统**，由 `mm/cma_debug.c` 提供，开关是 `CMA_DEBUGFS`（`mm/Kconfig:870-874`）。每个 CMA 区一个子目录（目录名是 `cma->name`，见 `mm/cma_debug.c:167`）：

| 文件 | 权限 | 说明 |
|---|---|---|
| `base_pfn` / `count` / `order_per_bit` | `0444` | 区的几何参数（`:171-175`） |
| `used` | `0444` | 已用页数（`:176`） |
| `maxchunk` | `0444` | ⚠️ **最大连续空闲块**——`used` 不高但 `maxchunk` 很小就是碎片化（`:177`） |
| `bitmap` | `0444` | 完整位图（`:182`） |
| `alloc` | ⚠️ `0200` | **只写**：写入页数直接从这个区分配（`:169`） |
| `free` | ⚠️ `0200` | **只写**：释放（`:170`） |

⚠️ **`alloc` / `free` 是写入型调试文件，会真的改变系统状态。** 它们的 `fops` 定义（`:161` 的 `DEFINE_DEBUGFS_ATTRIBUTE(cma_alloc_fops, NULL, cma_alloc_write, "%llu\n")`）里读回调是 `NULL` ——**读不出来，只能写**。在生产机器上写它等于人为制造 CMA 压力。

⚠️ **`dma_alloc_coherent()` 走 CMA 时用的是哪个区，这里看不出来。** 需要对照 06 §5 的 `linux,cma-default` 判定和 `cma->name`。

CMA 还有**第二个**出口，在 sysfs 而不是 debugfs 下，开关也是另一个（`CMA_SYSFS`，`mm/Kconfig:876-881`，实现 `mm/cma_sysfs.c`）。目录名同样是 `cma->name`（`mm/cma_sysfs.c:94-95`）：

| 文件 | 权限 | 说明 |
|---|---|---|
| `/sys/kernel/mm/cma/<name>/alloc_pages_success` | `0444` | 累计成功分配的**页数**（`mm/cma_sysfs.c:32-40`） |
| `/sys/kernel/mm/cma/<name>/alloc_pages_fail` | `0444` | 累计失败的页数（`mm/cma_sysfs.c:42-49`） |

⚠️ **这两个数是 debugfs 那一组给不出来的信息。** `used` / `maxchunk` 是**瞬时**水位，而失败是个历史事件——一次失败之后 CMA 区可能又空回去了，`used` 看不出任何异常。`alloc_pages_fail` 非零就是「这个区曾经供不上」的确证，计数点在 `mm/cma.c:516`（成功侧在 `mm/cma.c:512`）。

⚠️ **单位是页不是次数。** 一次失败的 4 MiB 请求会让计数涨 1024（arm64 4K 页），不要当成「失败了 1024 次」。

### §3.5 没有出口的那部分

⚠️ **`struct device` 的 DMA 字段没有任何 sysfs / debugfs 出口。** 判断动作：

```bash
# 全树找不到给这些字段建 sysfs 属性的地方
grep -rn "dma_mask\|bus_dma_limit\|dma_range_map" --include=*.c drivers/base/ | grep -i "attr\|sysfs"
```

想看某个设备的 `dev->dma_mask` / `coherent_dma_mask` / `bus_dma_limit` / `dma_range_map` / `dma_coherent`，只有三条路：**crash / kgdb 读结构体**、**在驱动里临时 `dev_info()`**、或者 **`DMA_API_DEBUG` 的报告里间接读到**（`swiotlb_bounced` tracepoint 会打印 `dma_mask`，见 §5）。02 §2 列了这些字段各自被谁写入。

---

## §4 `DMA_API_DEBUG` 报告全表

**这是 05 §3.1 承诺的"见 07 §4"。** 下面每一条都是 `err_printk` 的一个调用点，消息按语义分组而不是按行号排。⚠️ **消息文本在源码里是跨行拼接的字符串常量，dmesg 里是连成一行的**——grep 时不能整句搜，要挑一个不跨行的片段。

### §4.1 解映射不配对（最常见的一组）

| dmesg 片段 | 位置 | 真正的原因 |
|---|---|---|
| `device driver tries to free an invalid DMA memory address` | `kernel/dma/debug.c:970-972` | 传给 unmap 的地址本身就是 `DMA_MAPPING_ERROR` ——⚠️ **漏了 `dma_mapping_error()` 检查、失败路径继续往下走** |
| `DMA memory it has not allocated` | `:974-978` | 地址查不到对应映射：**重复 unmap**、**地址算错**、或者 §2.1 那个初始化盲区的假阳性 |
| `frees DMA memory with different size` | `:983-988` | ⚠️ **unmap 的 `size` 和 map 时不一致**（05 §3.1 的检查点） |
| `frees DMA memory with wrong function` | `:991-997` | ⚠️ `dma_map_page()` 配 `dma_unmap_single()` 这类混用；报告里的 `[mapped as %s] [unmapped as %s]` 用 §3.1 那张 `type2name[]` |
| `frees DMA memory with different CPU address` | `:998-1007` | 只对 `coherent` 类型判（`:998` 的 `entry->type == dma_debug_coherent`）：⚠️ **`dma_free_coherent()` 传的 `cpu_addr` 不是当初 alloc 返回的那个** |
| `frees DMA sg list with different entry count` | `:1010-1015` | ⚠️ unmap 用了 `nents` 而 map 记的是 `orig_nents`（05 §4.3 那组混用） |
| `frees DMA memory with different direction` | `:1022-1029` | unmap 的 `dir` 和 map 不一致。⚠️ **源码在 `:1018-1021` 自陈这"可能不是真 bug"**：注释原文说 most implementations 处理不好，所以还是要报 |
| `device driver failed to check map error` | `:1037-1043` | ⚠️ **整套检查里唯一一条"你没做某件事"而不是"你做错了某件事"**——映射成功但从没调 `dma_mapping_error()`。判据是 `entry->map_err_type == MAP_ERR_NOT_CHECKED`（`:1037`） |

### §4.2 缓冲来源非法

| dmesg 片段 | 位置 | 说明 |
|---|---|---|
| `maps memory from stack [addr=%p]` | `:1068` | ⚠️ 栈上缓冲，**确定判定** |
| `maps memory from stack [probable addr=%p]` | `:1078` | ⚠️ **`probable` = 启发式判定**（可能是别的任务的栈），仍然要查 |
| `maps memory from kernel text or rodata` | `:1088` | 映射了 `.text` / `.rodata` ——通常是把常量数组当缓冲 |
| `maps memory from invalid area` | `:1199` | 页不在有效内存范围内 |
| `maps memory from vmalloc area` | `:1203` | ⚠️ 和 05 §3.1 那条 `dev_WARN_ONCE` 是**两道独立的门**：`include/linux/dma-mapping.h:332-334` 那道只挡 `dma_map_single()`，这一道挡所有路径 |

### §4.3 sync 用错（开 `DMA_API_DEBUG` 才看得见）

| dmesg 片段 | 位置 | 说明 |
|---|---|---|
| `tries to sync DMA memory it has not allocated` | `:1104-1107` | 地址查不到映射 |
| `syncs DMA memory outside allocated range` | `:1111-1118` | ⚠️ `offset + size` 越出映射范围 |
| `syncs DMA memory with different direction` | `:1124-1131` | ⚠️ **但 `:1121-1122` 先短路了**：`entry->direction == DMA_BIDIRECTIONAL` 时整组方向检查全部跳过。这就是 05 §3.3 说"map 时写 `DMA_BIDIRECTIONAL`、每次 sync 写实际方向"合法的代码依据 |
| `syncs device read-only DMA memory for cpu` | `:1136-1142` | ⚠️ 判据是 `:1134-1135`：`to_cpu` 且映射不是 `DMA_FROM_DEVICE` 且这次 sync 不是 `DMA_TO_DEVICE` |
| `syncs device write-only DMA memory to device` | `:1146-1152` | 对称的另一半，判据在 `:1144-1145` |
| `syncs DMA sg list with different entry count` | `:1156-1159` | 同 §4.1 最后一条的 sg 版本 |

### §4.4 生命周期与 sg 约束

| dmesg 片段 | 位置 | 说明 |
|---|---|---|
| `device driver has pending DMA allocations while released from device` | `:859-866` | ⚠️ **泄漏检测**：驱动解绑时还有未解映射的条目，报告里带 `[count=%d]` 和一条泄漏样本 |
| `cacheline tracking EEXIST, overlapping mappings aren't supported` | `:571-572` | 见 §2.5 |
| `mapping sg segment longer than device claims to support` | `:1177` | ⚠️ 只在 `DMA_API_DEBUG_SG` 下有（§1）；超出 `dma_get_max_seg_size()` |
| `mapping sg segment across boundary` | `:1187` | 同上；跨过 `dma_get_seg_boundary()` |

⚠️ **`:859` 那条泄漏检测在 arm64 platform 设备上不会触发。** 它靠 `BUS_NOTIFY_UNBOUND_DRIVER` 通知（`:854-855`），通知器由 `dma_debug_add_bus()`（`:875-891`）注册，而全树只有两个调用点：

```bash
grep -rn "dma_debug_add_bus(" --include=*.c .
# drivers/pci/pci-driver.c:1744  → pci_bus_type
# arch/powerpc/platforms/pseries/vio.c:1726  → vio_bus_type（非 arm64）
```

⚠️ **所以在 arm64 上这条检查只覆盖 PCI 设备。** platform / spi / i2c 设备的 DMA 泄漏**不会**在解绑时被报出来——这是本子系统调试能力上一个实打实的缺口，`kernel/dma/` 自己并不给 platform 总线挂通知器。

---

## §5 tracepoint：全子系统只有一个

**这是 06 §8.3 承诺的"见 07 §5"。**

```bash
grep -rn "trace_" kernel/dma/*.c        # 唯一一条：swiotlb.c:920
ls include/trace/events/ | grep -c swiotlb   # 1
```

| tracepoint | 定义 | 触发点 |
|---|---|---|
| `swiotlb:swiotlb_bounced` | `include/trace/events/swiotlb.h:10-36` | `kernel/dma/swiotlb.c:920` |

字段清单（`include/trace/events/swiotlb.h:14-20`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `dev_name` | `__string` | 设备名 |
| `dma_mask` | `u64` | ⚠️ **这是唯一能在运行期不改内核读到 `dev->dma_mask` 的地方**（`:24`，`dev->dma_mask ? *dev->dma_mask : 0`）；见 §3.5 |
| `dev_addr` | `dma_addr_t` | bounce 后的 DMA 地址 |
| `size` | `size_t` | 长度 |
| `force` | `bool` | ⚠️ `is_swiotlb_force_bounce(dev)`（`:27`）——区分"因为掩码不够才 bounce"和"被强制 bounce" |

输出格式在 `:30-35`，末尾的 `%s` 打印 `FORCE` 或 `NORMAL`（`:35`）。

```bash
echo 1 > /sys/kernel/debug/tracing/events/swiotlb/swiotlb_bounced/enable
cat /sys/kernel/debug/tracing/trace_pipe
```

⚠️ **这个 tracepoint 没有配对的 `_complete` 事件，而这不是缺陷。** 它记录的是"这一次映射走了 bounce"这个事实本身，不是一段有开始和结束的操作——`kernel/dma/swiotlb.c:920` 位于 `swiotlb_map()` 里、拿到 `dma_addr` 之后，`:940` 就 return 了。**不要因为看不到 `_complete` 而怀疑 bounce 没完成。**

⚠️ **它也没有 `TP_CONDITION`**（`include/trace/events/swiotlb.h:10-36` 通篇没有），所以每一次 bounce 都会记一条。高吞吐设备上开它会淹掉 ring buffer——**先用 §3.2 的 `io_tlb_used` 确认真的在 bounce，再开这个看细节。**

⚠️ **本子系统在 `kernel/dma/mapping.c` 这一层完全没有 tracepoint。** 想跟踪 `dma_map_single()` / `dma_alloc_coherent()` 的每次调用，只能用 kprobe / ftrace function tracer：

```bash
echo 'dma_alloc_attrs dma_map_page_attrs' > /sys/kernel/debug/tracing/set_ftrace_filter
echo function > /sys/kernel/debug/tracing/current_tracer
```

⚠️ **`dma_map_single()` / `dma_map_page()` 都是 `static inline`（`include/linux/dma-mapping.h:328-338` 等），ftrace 抓不到它们**——要抓的是它们背后的真实函数 `dma_map_page_attrs()`（定义在 `kernel/dma/mapping.c:143`，`EXPORT_SYMBOL` 在 `:165`）。这是本子系统用 ftrace 时最容易踩空的一点。

---

## §6 不需要开任何开关的日志

这一节的所有消息在**生产内核**上就会出现。⚠️ **它们是排查 DMA 问题的第一手材料，而且比 §4 那套更常见**——因为 §4 需要重编内核。

### §6.1 三条"地址超出寻址能力"消息，语义各不相同

三处用了几乎一样的措辞，但**发生的时机完全不同**。看到哪一条决定你该改什么：

| dmesg 片段 | 位置 | 可见性 | 发生在 |
|---|---|---|---|
| `DMA addr %pad+%zu overflow (mask %llx, bus limit %llx).` | `kernel/dma/direct.h:103-105` | ⚠️ `dev_WARN_ONCE` | ⚠️ **流式映射，且平台上没有可用的 swiotlb**（`:100` 的 `is_swiotlb_active(dev)` 为假）——**这是最坏的一条：没有退路，映射直接失败** |
| 同上文本 | `kernel/dma/direct.c:527-529` | ⚠️ `dev_err_once` + 紧跟 `WARN_ON_ONCE(1)`（`:530`） | `dma_direct_map_resource()`（`:521`）——⚠️ **映射的是别人的 MMIO（05 §3.4），本来就没有 bounce 这条退路** |
| `swiotlb addr %pad+%zu overflow (mask %llx, bus limit %llx).` | `kernel/dma/swiotlb.c:932-934` | ⚠️ `dev_WARN_ONCE` | ⚠️ **已经 bounce 过了，bounce 缓冲自己的地址还是超限**——说明 swiotlb 池所在的物理位置对这个设备来说太高 |

```bash
# 三处是三个独立的判定点，不是同一条消息被打三次
grep -rn "overflow (mask %llx, bus limit %llx)" kernel/dma/
```

⚠️ **三条都是 `_ONCE` 变体，所以每个设备只报一次。** 反复失败在 dmesg 里看不出频度——**要看频度只能读 §3.2 的计数器或用 §5 的 tracepoint**。

⚠️ **第一条和第三条的修法方向相反**：第一条是"该开 swiotlb 或该降掩码"，第三条是"swiotlb 已经开了但位置不对，该调 `swiotlb=` 的大小/位置或者给设备一个受限池"。**把它们当成同一个问题会改错方向。**

### §6.2 swiotlb 自己的越界检查

| dmesg 片段 | 位置 | 含义 |
|---|---|---|
| `Access before mapping start detected. orig offset %u, requested offset %u.` | `kernel/dma/swiotlb.c:568-570` | ⚠️ sync 的偏移在映射起点之前 |
| `Buffer overflow detected. Allocation size: %zu. Mapping size: %zu+%u.` | `:576-578` | 偏移加起来超出分配大小 |
| `Buffer overflow detected. Allocation size: %zu. Mapping size: %zu.` | `:586-588` | ⚠️ **只有这一条不 return，而是 `size = alloc_size` 截断后继续**（`:589`） |

⚠️ **前两条会 `return`、这一次的数据搬运不做，第三条会截断继续。** 所以第三条之后设备看到的是**部分数据**而不是错误——比前两条更难查。三条全是 `dev_WARN_ONCE`。

⚠️ **这三条只在数据真的过 bounce 缓冲时才可能出现。** 没有 swiotlb 的 arm64 平台上永远看不到，同样的越界会变成静默的内存踩踏。

### §6.3 核心层的 `WARN`

| 判据 | 位置 | 变体 | 触发条件 |
|---|---|---|---|
| `!dev->dma_mask` | `kernel/dma/mapping.c:152`（map_page）/ `:190`（map_sg）/ `:303`（map_resource） | `WARN_ON_ONCE` | ⚠️ **驱动没设过掩码就开始映射**——05 §1 那一步漏了 |
| `!dev->coherent_dma_mask` | `:499`（`dma_alloc_attrs`）/ `:551`（`__dma_alloc_pages`） | `WARN_ON_ONCE` | 同上，一致性侧 |
| `irqs_disabled()` | `:533`（`dma_free_attrs`） | ⚠️ **`WARN`，不是 `_ONCE`** | 在原子上下文释放一致性内存。`:526-532` 的注释自陈了原因（03 §A9） |
| `gfp & (__GFP_DMA \| __GFP_DMA32 \| __GFP_HIGHMEM)` | `:553` | `WARN_ON_ONCE` | ⚠️ `dma_alloc_pages()` 传了区域标志；`dma_alloc_attrs()` 走的是 `:505` **静默清掉**这三个标志，两个 API 在这一点上不一致 |
| `attrs & ~DMA_ATTR_ALLOC_SINGLE_PAGES` | `:638` | `WARN_ON_ONCE` | `dma_alloc_noncontiguous()` 只认这一个属性 |
| 返回码不在允许集合里 | `:202-204` | `WARN_ON_ONCE` | ⚠️ **只有 `dma_map_ops` 实现者会撞到**（05 §9.2） |
| devres 记录对不上 | `:45-46` / `:66` | ⚠️ **`WARN`，不是 `_ONCE`** | 05 §10.4 那组 `dmam_` 混用 |
| `!PAGE_ALIGNED(size)` | `kernel/dma/direct.c:124` | `WARN_ON_ONCE` | 内部一致性检查，正常不该出现 |
| `!IS_ENABLED(CONFIG_DMA_COHERENT_POOL)` | `kernel/dma/direct.c:178` | `WARN_ON_ONCE` | 走到原子池路径但没编进池（04 §4） |
| 全局池释放失败 | `kernel/dma/direct.c:347-348` | `WARN_ON_ONCE(1)` | ⚠️ **arm64 不 select `DMA_GLOBAL_POOL`**，所以 `:345` 的 `IS_ENABLED` 为假，这条在 arm64 上是死代码 |

⚠️ **`:533` 和 `:66` 两处是不带 `_ONCE` 的 `WARN`**——**它们会在每次错误调用时刷一次完整栈**。日志被这两条淹掉时，看到的是同一个 bug 被反复报，不是很多个 bug。

⚠️ **`WARN_ON_ONCE(!dev->dma_mask)` 只是警告，函数并不返回错误。** 判据：`kernel/dma/mapping.c:152-153` 之后紧接着 `return DMA_MAPPING_ERROR`——这一条确实返回了；但 `:499` 那条 `WARN_ON_ONCE(!dev->coherent_dma_mask)` **单独一行、没有 `if`，警告完继续往下分配**。**同一类检查在两个 API 上的严厉程度不同**，别按其中一个推另一个。

### §6.4 原子池与 CMA 的运行期失败

| dmesg 片段 | 位置 | 变体 | 含义 |
|---|---|---|---|
| `Failed to get suitable pool for <设备名>` | `kernel/dma/pool.c:279` | ⚠️ **`WARN(1, ...)`**，每次都报 | ⚠️ **`GFP_ATOMIC` 的 `dma_alloc_coherent()` 找不到匹配的池**（05 §5.1 最后那条陷阱） |
| `DMA: preallocated %zu KiB %pGg pool for atomic allocations` | `:182-183` | `pr_info` | 启动期正常，见 §8 |
| `DMA: failed to allocate %zu KiB %pGg pool for atomic allocation` | `:177-178` | `pr_err` | ⚠️ 池根本没建起来 |
| 池扩容失败 | `:131` 的 `WARN_ON_ONCE(ret)` | `WARN_ON_ONCE` | 扩容路径出错 |
| `leaking DMA memory that can't be re-encrypted` | `kernel/dma/direct.c:93` | ⚠️ **`pr_warn_ratelimited`** | 内存加密相关；arm64 无 CoCo 配置下不会出现 |
| `Memory encryption is active and system is using DMA bounce buffers` | `kernel/dma/swiotlb.c:791` | ⚠️ **`pr_warn_once`** | 同上 |

⚠️ **`pool.c:279` 那条是本子系统里少数不带 `_ONCE` 的 `WARN`。** 原子池压力大时它会持续刷。它的正确处置不是加 `coherent_pool=` 就完事——04 §4.3 说明了池会自己扩容，**持续报警意味着分配速率超过了扩容速率**，通常真正的问题是驱动在中断上下文里做了本该在 probe 里做的分配。

⚠️ **`%pGg` 是 gfp 标志的格式符**，打印出来是 `GFP_KERNEL` 这样的文字而不是数字。所以启动日志里能直接看出建了哪几个池（§8）。

---

## §7 故障注入

⚠️ **本子系统没有任何专用的故障注入点。** 判据：

```bash
grep -rn "should_fail\|fault_attr\|DECLARE_FAULT_ATTR\|ALLOW_ERROR_INJECTION" kernel/dma/    # 空
```

⚠️ **这不代表不能注入故障，而是意味着注入必须从外围做。** 可用的替代手段：

| 想模拟 | 手段 | 说明 |
|---|---|---|
| 掩码设置失败 | 引导参数把设备的可用内存挪到高位 / 改 dts 的 `dma-ranges` | 06 §2 |
| 强制走 bounce | `swiotlb=force` | `kernel/dma/swiotlb.c:174-175` 解析 `force`；⚠️ **全局生效，所有设备都变慢** |
| 完全关掉 bounce | `swiotlb=noforce` | `:176-177` 置 `swiotlb_force_disable` ——⚠️ **原本靠 bounce 才能工作的设备会开始报 §6.1 第一条** |
| 原子池耗尽 | `coherent_pool=<很小的值>` | `kernel/dma/pool.c:29-34` 的 `early_param`，用 `memparse()` 解析（`:31`），所以能写 `coherent_pool=4K` |
| CMA 耗尽 | 往 `/sys/kernel/debug/cma/<name>/alloc` 写 | §3.4，⚠️ **需要 `CMA_DEBUGFS`** |
| CMA 完全不存在 | `cma=0` | `kernel/dma/Kconfig:126-127` 的 help 文本明确写了这个用法 |
| 一般内存分配失败 | `failslab` / `fail_page_alloc`（mm 的通用注入框架） | ⚠️ **不属于本子系统**，但 `dma_alloc_coherent()` 的失败路径靠它才能覆盖到 |

⚠️ **`swiotlb=force` 是这一组里最有用的一个。** arm64 上大多数设备平时不走 bounce，所以 bounce 路径上的 bug（§6.2 那三条越界检查、方向搞反导致的数据不同步）在正常测试里完全覆盖不到。**打开它跑一轮回归，是本子系统唯一成本可控的负向测试。**

---

## §8 启动期 dmesg 判读

按出现次序，本子系统会打这些：

| 消息 | 位置 | 说明 |
|---|---|---|
| `SWIOTLB bounce buffer size adjusted to %luMB` | `kernel/dma/swiotlb.c:210` | 大小被内核调整过 |
| `SWIOTLB bounce buffer size roundup to %luMB` | `:139` | 因为 area 数量对齐而上调 |
| `area num %d.` | `:137` | ⚠️ **消息本身没有 `swiotlb` 字样**，靠 `pr_fmt` 加前缀，见 §8.2 |
| `mapped [mem %pa-%pa] (%luMB)` | `:222` | swiotlb 池的物理区间 |
| `SWIOTLB bounce buffer size adjusted %lu -> %lu slabs` | `:390` | 晚期重分配时的调整 |
| `only able to allocate %ld MB` | `:474` | ⚠️ 池比要求的小 |
| `No low mem` | `:218` | ⚠️ `pr_warn`，池没建起来 |
| `Failed to map the unencrypted memory %pa size %lx.` | `:251` | ⚠️ `pr_err`，加密相关 |
| `tearing down default memory pool` | `:518` | 默认池被拆掉（有受限池或不需要时） |
| `DMA: preallocated %zu KiB %pGg pool for atomic allocations` | `kernel/dma/pool.c:182-183` | ⚠️ **arm64 上最多三行**，见下面的 §8.1 |
| `preallocated %d debug entries` | `kernel/dma/debug.c:912` | 只在开了 `DMA_API_DEBUG` 时 |
| `debugging enabled by kernel config` | `:926` | 同上，⚠️ **看到这一行才说明 §2 那套真的在跑** |
| `Reserved memory: created CMA memory pool at ...` | `kernel/dma/contiguous.c:435` | dts 的 CMA 区 |
| `Reserved memory: created DMA memory pool at ...` | `kernel/dma/coherent.c:386` | dts 的 per-device 一致性池 |
| `Reserved memory: created restricted DMA pool at %pa, size %ld MiB` | `kernel/dma/swiotlb.c:1106-1107` | 受限池 |
| `Reserved memory: bypass %s node, using cmdline CMA params instead` | `kernel/dma/contiguous.c:407` | ⚠️ **命令行赢了 dts**，见 06 §5.2.1 |
| `DMA: default coherent area is set` | `kernel/dma/coherent.c:319` | ⚠️ arm64 上不会出现（`DMA_GLOBAL_POOL` 未选） |

⚠️ **三条 `created ... pool` 措辞高度相似，对应三种完全不同的池。** 06 §9 那张表把它们和 dts 属性组合一一对上了——**排查"池没建起来"时先确认你在找的是哪一条**。

⚠️ **`debugging enabled by kernel config`（`kernel/dma/debug.c:926`）是判断 `DMA_API_DEBUG` 是否真的生效的最可靠单点。** 它在 `dma_debug_init()` 的最后一行，紧跟 `dma_debug_initialized = true`（`:924`）。看不到它就说明走了 `:917` 或 `:900` 那两条提前返回。

### §8.1 原子池到底会打几行

`dma_atomic_pool_init()`（`kernel/dma/pool.c:187-221`）依次建三个池，每个建成打一行：

| 池 | 建它的代码 | 门 | arm64 上 |
|---|---|---|---|
| `atomic_pool_kernel` | `:202-203`（`GFP_KERNEL`） | ⚠️ **无条件** | 一定有 |
| `atomic_pool_dma` | `:207-208`（`GFP_KERNEL \| GFP_DMA`） | ⚠️ **`has_managed_dma()`**（`:206`）——**运行期判定，不是 Kconfig** | ⚠️ 看该机器 `ZONE_DMA` 里有没有可管理页 |
| `atomic_pool_dma32` | `:213-214`（`GFP_KERNEL \| GFP_DMA32`） | `IS_ENABLED(CONFIG_ZONE_DMA32)`（`:212`） | 一定有——`mm/Kconfig:983` 是 `default y if ARM64` |

⚠️ **所以 arm64 上看到两行还是三行取决于内存布局，不取决于配置**（`ZONE_DMA` 自己在 `mm/Kconfig:978` 也是 `default y if ARM64`，但 `has_managed_dma()` 判的是这个 zone 里实际有没有页）。**看到只有两行不要去找 Kconfig 问题。**

⚠️ **每行末尾的 `%pGg` 打印的是 gfp 标志文字**，所以三行是靠 `GFP_KERNEL` / `GFP_KERNEL|GFP_DMA` / `GFP_KERNEL|GFP_DMA32` 区分的，不是靠出现次序。

⚠️ **池大小的默认值不是常数**：`:195-199` 按内存量算——每 1 GiB 给 128 KiB，下限 128 KiB，上限 `MAX_ORDER_NR_PAGES`。给了 `coherent_pool=` 就直接用命令行的值（`:195` 的 `if (!atomic_pool_size)`）。

### §8.2 grep dmesg 要带上各文件自己的前缀

⚠️ **本子系统四个会打日志的文件用了四种不同的 `pr_fmt`**，同一条 grep 抓不全：

| 文件 | `pr_fmt` | 位置 | dmesg 里长什么样 |
|---|---|---|---|
| `kernel/dma/debug.c` | `"DMA-API: "` | `:8` | `DMA-API: foo_drv 0.foo: ...` |
| `kernel/dma/swiotlb.c` | ⚠️ `"software IO TLB: "` | `:21` | ⚠️ **前缀里没有 `swiotlb` 这个词** |
| `kernel/dma/contiguous.c` | ⚠️ **前后不同**：`:38` 是 `"cma: "`，`:379-380` 把它 `#undef` 回无前缀 | `:38` / `:380` | ⚠️ **`:435` 那条 `created CMA memory pool` 在 `:380` 之后，所以没有 `cma: ` 前缀** |
| `kernel/dma/coherent.c` | 没有定义 | — | 消息自带 `DMA: ` 或 `Reserved memory: ` 字面量 |
| `kernel/dma/pool.c` | 没有定义 | — | 消息自带 `DMA: ` 字面量 |

```bash
# 确认 contiguous.c 的 pr_fmt 在文件中段被换掉了
grep -n "define pr_fmt\|undef pr_fmt" kernel/dma/contiguous.c    # 38 / 379 / 380
```

⚠️ **这是本子系统日志排查里最实际的一个坑**：按 `grep -i swiotlb` 找 bounce 相关的启动信息会漏掉一半（`area num`、`mapped [mem ...]`、`No low mem`、`tearing down default memory pool` 这几条正文里都没有 `SWIOTLB` 字样，只有前缀 `software IO TLB: `）。

---

## §9 `dma_map_benchmark`：量 map/unmap 的开销

⚠️ **它是一个内核模块 + 一个 ioctl 接口，不是 sysfs 里读几个数就完事。** 用户态工具在 `tools/testing/selftests/dma/dma_map_benchmark.c`（Kconfig help 文本在 `kernel/dma/Kconfig:243` 直接点了这个路径）。

ABI 在 `include/linux/map_benchmark.h`：

| 定义 | 位置 | 值 |
|---|---|---|
| `DMA_MAP_BENCHMARK` | `include/linux/map_benchmark.h:9` | `_IOWR('d', 1, struct map_benchmark)` |
| `DMA_MAP_MAX_THREADS` | `:10` | 1024 |
| `DMA_MAP_MAX_SECONDS` | `:11` | 300 |
| `DMA_MAP_MAX_TRANS_DELAY` | `:12` | `10 * NSEC_PER_MSEC` |
| 方向常量 | `:14-16` | ⚠️ **`DMA_MAP_BIDIRECTIONAL` = 0 / `TO_DEVICE` = 1 / `FROM_DEVICE` = 2，和 `enum dma_data_direction` 的取值不是同一套编号** |
| `struct map_benchmark` | `:18-30` | 输出四个字段（`avg_map_100ns` / `map_stddev` / `avg_unmap_100ns` / `unmap_stddev`），输入七个 |

⚠️ **这个头文件在 `include/linux/` 而不是 `include/uapi/linux/`，但用户态工具确实 include 它。** 判据：`tools/testing/selftests/dma/dma_map_benchmark.c` 存在且 Kconfig 指向它。**这是一处 ABI 摆放位置和用途不一致的地方，不是本篇能改的。**

参数校验全在 `map_benchmark_ioctl()`（`kernel/dma/map_benchmark.c:206-260`），每条失败都有自己的 `pr_err`：

| 出错消息 | 位置 | 判据 |
|---|---|---|
| `invalid thread number` | `:210` | `threads == 0 \|\| > 1024`（`:208-209`） |
| `invalid duration seconds` | `:216` | `seconds == 0 \|\| > 300`（`:214-215`） |
| `invalid transmission delay` | `:221` | `> DMA_MAP_MAX_TRANS_DELAY`（`:220`） |
| `invalid numa node` | `:228` | `:225-227` |
| `invalid granule size` | `:233` | ⚠️ **`< 1 \|\| > 1024`**（`:232`），单位是页数 |
| `invalid DMA direction` | `:248` | `:237-250` 的 switch 落到 `default` |
| `failed to set dma_mask on device %s` | `:257-258` | ⚠️ `dma_set_mask(dev, DMA_BIT_MASK(dma_bits))` 失败（`:254-255`） |
| `create dma_map thread failed` | `:134` | 线程创建失败 |
| `dma_map_single failed on %s` | `:64` | ⚠️ **测试跑起来之后的映射失败**——说明掩码/池确实有问题，不是工具的问题 |

⚠️ **它会临时改设备的掩码，跑完再改回来。** `:252` 先存 `old_dma_mask = dma_get_mask(map->dev)`，`:264-265` 的注释说明了为什么要恢复。**在生产设备上跑它有真实副作用**，别对着正在服务的设备用。

### §9.1 ⚠️ 默认情况下这个 debugfs 文件根本不存在

`/sys/kernel/debug/dma_map_benchmark` 是在 `__map_benchmark_probe()`（`kernel/dma/map_benchmark.c:297-325`）里建的（`:318`），也就是说**它要等一个设备绑上这个驱动才出现**。而两个驱动的匹配方式是：

```bash
grep -n "of_match_table\|id_table" kernel/dma/map_benchmark.c    # 空
```

| 驱动 | 位置 | 匹配方式 |
|---|---|---|
| `map_benchmark_platform_driver` | `:332-337` | ⚠️ **只有 `.driver.name = "dma_map_benchmark"`**（`:334`），靠名字匹配 |
| `map_benchmark_pci_driver` | `:345-348` | ⚠️ **没有 `.id_table`**，所以 PCI 核心不会给它匹配任何设备 |

⚠️ **所以在 arm64 DT 平台上，开了 `DMA_MAP_BENCHMARK` 之后这个文件仍然不会自动出现**——必须先有一个名字恰好是 `dma_map_benchmark` 的 platform 设备。这是"Kconfig 开了但功能看不见"的一个真实例子，和 §2.1 那五道门是同一类问题的不同形态。

⚠️ **`:314-317` 的注释自陈"we only permit a device bound with this driver, 2nd probe will fail"**——第二个设备绑上来时 `debugfs_create_file()` 会失败并被 `:320-321` 返回错误。所以这个功能一次只能测一个设备。

---

## §10 一页速查

```bash
### 生产内核上能做的（不用重编）
cat /sys/kernel/debug/swiotlb/io_tlb_used          # 有没有在 bounce（瞬时值）
cat /sys/kernel/debug/swiotlb/io_tlb_nslabs        # 池总容量
ls  /sys/kernel/debug/dma_pools/                   # 原子池大小（三个文件）
echo 1 > /sys/kernel/debug/tracing/events/swiotlb/swiotlb_bounced/enable
dmesg | grep -E "DMA-API|software IO TLB|SWIOTLB|Reserved memory|DMA: |overflow \(mask|CMA memory pool"

### 需要 CONFIG_DMA_API_DEBUG（重编）
cat /sys/kernel/debug/dma-api/disabled             # 1 = 检查已失效，先解决这个
cat /sys/kernel/debug/dma-api/error_count          # 真实错误总数（不受配额限制）
echo 1 > /sys/kernel/debug/dma-api/all_errors      # 必须先做，否则只报第一条
cat /sys/kernel/debug/dma-api/min_free_entries     # 逼近 0 就加 dma_debug_entries=
echo foo_driver > /sys/kernel/debug/dma-api/driver_filter
cat /sys/kernel/debug/dma-api/dump                 # 当前全部活映射（会持锁遍历）

### 引导参数
dma_debug=off                  # 只认 "off" 三个字母
dma_debug_entries=<n>          # 默认 65536
dma_debug_driver=<name>
swiotlb=<slabs>,<areas>,force  # force / noforce，逗号分隔，见 swiotlb.c:160-181
coherent_pool=<size>           # memparse 语法，可写 4K / 2M
cma=<size>                     # 0 = 完全关掉 CMA
cma_pernuma=<size>             # 仅 DMA_PERNUMA_CMA

### 需要 CONFIG_CMA_DEBUGFS（重编，且不属于本子系统）
cat /sys/kernel/debug/cma/<name>/used
cat /sys/kernel/debug/cma/<name>/maxchunk         # used 不高但这个很小 = 碎片化
```

⚠️ **三条最容易被跳过的前置动作**：① 开 `DMA_API_DEBUG` 时确认 `STACKTRACE` 也开了（§1）；② 读报告之前先 `echo 1 > all_errors`（§2.1）；③ 判断有没有问题看 `error_count` 而不是数 dmesg（§2.1）。

---

## §11 导航

| 想知道 | 去 |
|---|---|
| 每个 API 的完整签名和错误码 | 01 |
| `dma_debug_entry` 里各字段是什么 | `kernel/dma/debug.c:66-82`（本篇 §3.1 用到 `type`/`dev_addr`/`map_err_type`） |
| `struct device` 的 DMA 字段被谁写入 | 02 §2 |
| 为什么在原子上下文 free 会 `WARN` | 03 §A9 |
| 原子池为什么会满、怎么扩容 | 04 §4 |
| 每个 API 的正确调用骨架 | 05 |
| 名字很像的 API 怎么区分 | 05 §10 |
| dts 写错了怎么定位 | 06 §8 §9 |
| 为什么一致性内存慢、sync 为什么不对称 | 08 §2 / 08 §4 |
| 哪些结论没有验证 | 99 §5 |
