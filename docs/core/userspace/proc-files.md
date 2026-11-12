---
title: /proc 文件与字段详解
---

# Linux /proc 常见文件、字段含义与排查场景

`/proc` 是 procfs 的通常挂载点，是用户空间观察进程、CPU、内存、I/O 和内核状态的重要入口。本文侧重“读取哪个文件、输出如何解释、如何避免误判”，而不是 procfs 的创建 API。

本文以 **Linux 6.1 系列，结合本地 6.1.158 源码**为基准。不同体系结构、内核配置、发行版和权限设置下，字段及文件是否存在可能不同；示例数据用于说明格式，不代表当前机器的实测结果。内核实现参见 [procfs 实现与 API](misc-proc.md)，参数接口参见 [sysctl](sysctl.md)。

## 1. 先理解 /proc 的读取规则

### 1.1 目录分类与速查

| 类别 | 常见路径 | 主要用途 |
| --- | --- | --- |
| CPU 与系统负载 | `cpuinfo`、`stat`、`loadavg`、`uptime` | CPU 特征、执行时间、运行队列与启动时间 |
| 中断 | `interrupts`、`softirqs`、`irq/` | IRQ 分布、软中断活动、IRQ 亲和性 |
| 全局内存 | `meminfo`、`vmstat` | 内存构成、换页、回收、缺页与压缩活动 |
| 内存分配器 | `buddyinfo`、`pagetypeinfo`、`zoneinfo`、`slabinfo`、`vmallocinfo` | 碎片、内存水位、对象缓存和 vmalloc 分配 |
| 资源压力 | `pressure/cpu`、`pressure/memory`、`pressure/io` | 任务因资源不足而停顿的程度 |
| 块设备 | `partitions`、`diskstats`、`swaps` | 块设备容量、I/O 统计和 swap 使用 |
| 挂载与文件系统 | `filesystems`、`self/mountinfo`、`mounts`、`locks` | 文件系统支持、挂载拓扑、文件锁 |
| 网络 | `net/dev`、`net/snmp`、`net/netstat`、`net/sockstat`、`net/tcp`、`net/udp` | 接口流量、协议计数器、socket 状态 |
| 进程与线程 | `<pid>/status`、`stat`、`maps`、`smaps`、`fd/`、`task/` | 身份、内存、文件句柄、线程与调度 |
| 内核和硬件 | `version`、`cmdline`、`modules`、`devices`、`iomem`、`ioports` | 内核版本、启动参数、模块和资源分布 |
| 运行时参数 | `sys/` | sysctl 参数；部分文件可写，写入会改变系统行为 |

`/proc/<pid>/` 的数字目录对应当前 procfs 挂载关联的 PID 命名空间中可见的进程。`/proc/self` 指向**执行访问操作的进程**，`/proc/thread-self` 指向访问线程的目录。

例如，`cat /proc/self/status` 看到的是 `cat` 自己，而不是启动它的 shell。查看当前 shell 可用：

```bash
cat /proc/$$/status
```

### 1.2 虚拟文件不是普通磁盘文件

- 大部分内容在读取时由内核生成；`ls -l` 显示大小为 0 并不表示没有内容。
- 不同文件没有全局一致的快照保证；连续读取 `stat`、`meminfo` 和进程状态时，系统已经发生变化。
- 单个文件也可能在输出过程中遇到对象变化。`maps`、网络连接表等不能被当作事务性快照。
- 读取进程目录时，进程可能退出；出现 `ENOENT`、`ESRCH` 或权限错误不一定是系统故障。
- 不要对 `/proc` 做递归 `cat` 或无差别备份：这里包含二进制接口、特殊设备视图、敏感数据和可能阻塞的接口。

### 1.3 三种数值，三种读法

| 类型 | 例子 | 应怎样理解 |
| --- | --- | --- |
| 瞬时值 | `MemFree`、`procs_running`、进行中的 I/O 数 | 当前状态；允许升降 |
| 累计计数器 | `ctxt`、网卡字节数、磁盘完成请求数 | 通常需要两次采样取差值 |
| 平滑平均值 | `loadavg`、PSI 的 `avg10` | 历史趋势，不等同于当前瞬时值 |

对累计值，设两次采样为 `C1`、`C2`，实际间隔为 `dt` 秒：

```text
rate = (C2 - C1) / dt
```

采样程序应使用单调时钟测量间隔，避免系统校时干扰。重启、设备重建、计数器回绕、CPU 热插拔等可能破坏差分关系，应丢弃异常样本，而不是输出负速率。

### 1.4 单位不能一概而论

| 接口 | 单位 |
| --- | --- |
| `meminfo`、`status`、`smaps` 中标注 `kB` 的内存字段 | 通常为 1024 字节，即 KiB |
| `/proc/stat` 的 CPU 时间、`<pid>/stat` 的多数 CPU 时间字段 | `USER_HZ` 对应的 clock ticks |
| `<pid>/statm` 各内存字段、`<pid>/stat` 的 `rss` | 基础页数 |
| `<pid>/stat` 的 `vsize` | 字节 |
| `diskstats` 的扇区计数 | 每个统计扇区固定为 512 字节 |
| `diskstats` 的时间字段 | 毫秒 |
| PSI 的 `total` | 微秒 |
| `uptime` | 秒，可带小数 |

```bash
getconf CLK_TCK
getconf PAGESIZE
```

`CLK_TCK` 常为 100，但不要写死；它**不等于内核配置中的 `CONFIG_HZ`**。页大小也不一定为 4096 字节。

## 2. CPU、时间与系统负载

### 2.1 /proc/cpuinfo：CPU 特征，不是 CPU 使用率

```text
processor       : 0
vendor_id       : GenuineIntel
cpu family      : 6
model           : 85
model name      : Example CPU
stepping        : 7
microcode       : 0x5003605
cpu MHz         : 2400.000
cache size      : 16384 KB
physical id     : 0
siblings        : 16
core id         : 0
cpu cores       : 8
flags           : fpu ... sse2 ... avx ...
bugs            : ...
bogomips        : 4800.00
```

上面是 x86 风格示例，ARM/ARM64 不应按相同字段解析。

| 字段 | 含义 | 注意事项 |
| --- | --- | --- |
| `processor` | 逻辑 CPU 编号 | 不是物理插槽编号；SMT 线程也分别编号 |
| `vendor_id`、`model name` | 厂商与型号信息 | 虚拟机里可能被模拟或屏蔽 |
| `cpu family`、`model`、`stepping` | 处理器标识与修订信息 | 是体系结构相关信息，不是性能分数 |
| `microcode` | CPU 微码修订信息 | 可用于核对微码更新 |
| `cpu MHz` | 内核报告的频率信息 | 不保证是固定额定频率，也不是严格实时的频率遥测 |
| `cache size` | 体系结构实现报告的缓存容量 | 不能简单乘以逻辑 CPU 数作为总缓存 |
| `physical id` | 物理封装标识 | 在虚拟机或某些架构上可能缺失 |
| `core id` | 封装内的核心标识 | 判断唯一核心通常需组合 `physical id` 与 `core id` |
| `siblings` | 封装内报告的逻辑处理器数量 | 不代表进程实际可使用的 CPU 数量 |
| `cpu cores` | 封装内报告的核心数量 | 热插拔和虚拟化环境下应结合 sysfs 拓扑 |
| `flags` | 内核报告的 CPU 特征 | 不是所有原始硬件特征的无条件完整列表 |
| `bugs` | 内核识别的相关 CPU 漏洞类别 | 是否缓解应结合 `/sys/devices/system/cpu/vulnerabilities/` |
| `bogomips` | 内核延时循环校准衍生的数值 | 不适合比较 CPU 实际性能 |

ARM64 常见字段有 `Features`、`CPU implementer`、`CPU architecture`、`CPU variant`、`CPU part`、`CPU revision`。`CPU part` 等通常要结合实现者和处理器文档解码。

**适用场景**：核对 CPU 型号、指令集、虚拟机暴露能力、逻辑 CPU 分布。实际可运行 CPU 范围看进程 `Cpus_allowed_list` 和 cpuset；配额限制看 cgroup，而不是只数 `cpuinfo` 的条目。

### 2.2 /proc/stat：全局 CPU 时间和内核活动

```text
cpu  10000 200 3000 80000 1000 100 500 200 300 0
cpu0 5000 100 1500 40000 500 50 250 100 150 0
cpu1 5000 100 1500 40000 500 50 250 100 150 0
intr 123456 10 20 0 ...
ctxt 789012
btime 1700000000
processes 4567
procs_running 2
procs_blocked 1
softirq 100000 0 1000 5000 3000 0 0 100 20000 0 70900
```

`cpu` 汇总 CPU 时间，`cpuN` 为单个在线逻辑 CPU。汇总保留的历史统计涉及可能 CPU，CPU 热插拔后不应假设它总能精确等于当前显示的 `cpuN` 行之和。

CPU 行中，名称之后的十个数依次为：

| 序号 | 字段 | 含义 |
| --- | --- | --- |
| 1 | `user` | 普通用户态执行时间，包含 `guest` |
| 2 | `nice` | 被记入 nice 类别的用户态时间，包含 `guest_nice`；Linux 6.1 中正 nice 值的任务记入此类 |
| 3 | `system` | 内核态执行时间 |
| 4 | `idle` | 空闲时间，不包括另记的 `iowait` |
| 5 | `iowait` | 任务等待 I/O 时归入这一类别的 CPU 空闲统计 |
| 6 | `irq` | 硬中断处理时间 |
| 7 | `softirq` | 软中断处理时间 |
| 8 | `steal` | 虚拟 CPU 被宿主机用于其他执行而失去的时间 |
| 9 | `guest` | 执行虚拟 CPU 的时间，已计入 `user` |
| 10 | `guest_nice` | nice 类别的 guest 时间，已计入 `nice` |

**不要把十项直接相加**，否则 guest 时间会重复计算。常用总时间定义为前八项之和：

```text
total = user + nice + system + idle + iowait + irq + softirq + steal
```

两次采样后，采用一种明确说明的口径：

```text
non_idle_percent = 100 * (delta_total - delta_idle - delta_iowait) / delta_total
iowait_percent   = 100 * delta_iowait / delta_total
steal_percent    = 100 * delta_steal / delta_total
```

这里 `non_idle_percent` 将 steal 归入非空闲部分；若要表示 guest 真正执行代码的比例，还需扣除 `delta_steal`。监控工具对“使用率”的定义可能不同，比较前应统一口径。

例如，`delta_total = 1000`，`delta_idle = 700`，`delta_iowait = 100`，上述非空闲比例为 20%。这是采样窗口的占比，不是从启动以来的平均值。

!!! warning "iowait 的边界"
    CPU 本身不会坐等某个进程的 I/O；此时它可以执行其他任务。多核场景中的归属并不精确，某些情况下 `iowait` 甚至会下降。高 iowait 是排查线索，不能单独证明磁盘损坏、磁盘已经饱和，或代表所有任务的 I/O 等待时间总和。

其他行：

| 行名 | 含义 | 常见用途 |
| --- | --- | --- |
| `intr` | 第一个数是累计中断总数，随后为编号 IRQ 的累计计数；总数也可能包括架构特定中断 | 观察中断增长，具体分布看 `interrupts` |
| `ctxt` | 累计上下文切换次数 | 两次差分得到切换速率 |
| `btime` | 启动时刻的 Unix epoch 秒数，受时间命名空间相关处理影响 | 区分重启、估算进程启动时刻 |
| `processes` | 累计创建任务数，包含 `fork`、`clone` 创建的任务 | 不是当前存活进程数 |
| `procs_running` | 正在运行或可运行的任务数 | 运行队列压力的瞬时线索 |
| `procs_blocked` | 当前记入 I/O 等待的任务数 | 不等同于所有 `D` 状态任务总数 |
| `softirq` | 第一个数是软中断累计总数，随后按类型给出累计计数 | 进一步用 `softirqs` 对应类型名称 |

### 2.3 /proc/loadavg：负载不等于 CPU 百分比

```text
0.61 0.55 0.42 2/828 22084
```

| 字段 | 含义 |
| --- | --- |
| `0.61`、`0.55`、`0.42` | 约对应过去 1、5、15 分钟趋势的指数平滑负载 |
| `2/828` 的分子 | 当前可运行的调度实体数，包括正在 CPU 上运行的任务 |
| `2/828` 的分母 | 当前系统任务数，包含线程，不是简单的进程组数量 |
| `22084` | 在读者可见 PID 命名空间中最近分配的 PID |

负载统计既包括可运行任务，也包括参与负载计算的不可中断睡眠任务。因此，**CPU 利用率不高，负载也可能很高**，例如大量任务阻塞在存储路径上。

8 个可用逻辑 CPU 上，持续负载 8 不等于“CPU 一定 100%”：还需区分 runnable 与阻塞任务，检查 CPU 亲和性、容器配额以及 CPU 是否分布不均。1 分钟值明显高于 15 分钟值通常提示近期上升，但不是严格的一分钟算术平均。

### 2.4 /proc/uptime：运行时长与累计 CPU 空闲

```text
3600.00 6200.00
```

第一个值是系统启动后的时长，包含挂起时间，并受时间命名空间偏移影响；第二个值是各 CPU 的累计空闲时间之和。多核机器上，第二个值大于第一个值是正常现象。

例如，两个 CPU 在 3600 秒中分别空闲 3100 秒，第二个值就是约 6200 秒。不要直接把两者相减作为“系统忙碌秒数”，也不要在挂起或热插拔场景中把它与 `/proc/stat` 的 CPU 时间强行视为相同口径。

## 3. 硬中断、软中断与 IRQ 分布

### 3.1 /proc/interrupts

```text
            CPU0       CPU1
 24:        1234       5678  IR-PCI-MSI 524288-edge eth0
 25:       10000         20  IR-PCI-MSI 524289-edge nvme0q1
NMI:           0          0  Non-maskable interrupts
LOC:       50000      50000  Local timer interrupts
RES:         800        900  Rescheduling interrupts
CAL:         100        120  Function call interrupts
TLB:          10         12  TLB shootdowns
```

- 左列通常是 Linux IRQ 编号；特殊行使用架构相关名称。
- 中间各列是中断在相应 CPU 上的累计计数。
- 后部显示中断控制器或 irqchip、触发类型及处理程序/设备名称，具体格式依赖架构与驱动。
- 共享 IRQ 可能有多个处理程序；MSI/MSI-X 设备也可能使用多条 IRQ。

**适用场景**：设备没有中断、某一 IRQ 增长异常、网卡多队列分布不均、单个 CPU 被中断压满。先做计数差分，再结合负载，不能只看历史累计数。

实时内核或线程化中断场景还需检查 `irq/<n>-...` 内核线程的调度与亲和性；中断次数不能直接代表处理耗时。

### 3.2 /proc/softirqs

```text
                 CPU0       CPU1
          HI:       0          0
       TIMER:   10000      11000
      NET_TX:     200        210
      NET_RX:   80000      70000
       BLOCK:     500        600
    IRQ_POLL:       0          0
     TASKLET:      30         40
       SCHED:   20000      19000
     HRTIMER:     900        950
         RCU:   15000      16000
```

| 类型 | 主要含义 |
| --- | --- |
| `HI` | 高优先级 tasklet 相关处理 |
| `TIMER` | 普通定时器相关处理 |
| `NET_TX`、`NET_RX` | 网络发送侧、接收侧相关处理 |
| `BLOCK`、`IRQ_POLL` | 块 I/O 完成、IRQ polling 相关处理 |
| `TASKLET` | 普通 tasklet 处理 |
| `SCHED` | 调度器相关处理 |
| `HRTIMER` | 高精度定时器相关处理，依赖内核执行路径 |
| `RCU` | RCU 相关处理 |

这些是软中断处理计数，不是耗时、数据包数或回调数。一次 `NET_RX` 执行可能处理多个包；`/proc/stat` 的 `softirq` CPU 时间也不是这里的计数。

### 3.3 `/proc/irq/<irq>/`

| 文件 | 含义 |
| --- | --- |
| `smp_affinity` | 请求允许处理此 IRQ 的 CPU 位图，十六进制表示 |
| `smp_affinity_list` | 同类范围的 CPU 列表，例如 `0-3,8` |
| `effective_affinity`、`effective_affinity_list` | 实际生效的 IRQ CPU 范围，取决于内核配置和 irqchip |
| `node` | 设备报告的 NUMA 节点；`-1` 通常表示未知 |
| `spurious` | 未处理或疑似伪中断等诊断统计 |

位图 `5` 即二进制 `0101`，对应 CPU 0 和 CPU 2。读取亲和性时应区分“允许集合”和“实际生效集合”；managed IRQ、控制器限制、CPU 在线状态和 irqbalance 都可能影响结果。本文不提供直接改写亲和性的命令。

## 4. 全局内存与回收行为

### 4.1 /proc/meminfo

```text
MemTotal:       16384000 kB
MemFree:          512000 kB
MemAvailable:    6144000 kB
Buffers:          128000 kB
Cached:          4096000 kB
SwapCached:         4096 kB
Active:          5000000 kB
Inactive:        4000000 kB
SwapTotal:       2097152 kB
SwapFree:        1835008 kB
Dirty:              8192 kB
Writeback:          1024 kB
Slab:            600000 kB
SReclaimable:    400000 kB
SUnreclaim:      200000 kB
```

#### 容量、缓存与可用内存

| 字段 | 含义与解读 |
| --- | --- |
| `MemTotal` | 内核可用的 RAM 总量，已扣除部分保留区域等；不一定等于安装的物理内存 |
| `MemFree` | 当前尚未使用的内存 |
| `MemAvailable` | 不进行 swap 的前提下，估计可供新应用使用的内存；结合空闲页、文件 LRU、可回收内核内存和水位等估算 |
| `Buffers` | 原始块相关的缓冲内存 |
| `Cached` | 文件页缓存，也包括 tmpfs/shmem 相关缓存；不包含 `SwapCached` |
| `SwapCached` | 已回到 RAM、同时仍保有有效 swap 副本的页 |
| `Active`、`Inactive` | 活跃/非活跃 LRU 分类；不是“正在用/已经没用” |
| `Active(anon)`、`Inactive(anon)` | 匿名类 LRU 页面 |
| `Active(file)`、`Inactive(file)` | 文件类 LRU 页面 |
| `Unevictable` | 无法按通常 LRU 回收方式驱逐的内存 |
| `Mlocked` | 被 `mlock` 等机制锁住的内存，与 `Unevictable` 有重叠 |
| `HighTotal`、`HighFree`、`LowTotal`、`LowFree` | 高端/低端内存统计，主要见于特定 32 位架构 |

**`MemFree` 很低不等于内存不足**。Linux 会用空闲 RAM 做缓存；判断压力优先结合 `MemAvailable`、回收行为和 PSI。也不能把 `Cached` 全部视为可立即释放，因为其中可能包括脏页和共享内存。

#### 匿名页、内核内存与写回

| 字段 | 含义与解读 |
| --- | --- |
| `AnonPages` | 用户空间匿名映射所使用的非文件后备内存 |
| `Mapped` | 被映射的文件页等，不是所有进程虚拟地址空间总量 |
| `Shmem` | tmpfs、共享内存和共享匿名映射等所用内存，与其他缓存类统计可能重叠 |
| `Slab` | slab/slub 对象缓存内存，约为 `SReclaimable + SUnreclaim` |
| `SReclaimable` | 可能通过 shrinker 等回收的 slab，不保证全部能立即回收 |
| `SUnreclaim` | 不可按普通 slab 回收路径回收的对象缓存 |
| `KReclaimable` | 可回收内核分配，包括 `SReclaimable` 及其他相关可回收内核内存 |
| `KernelStack` | 内核栈内存 |
| `PageTables` | 页表所用内存 |
| `SecPageTables` | 配置支持时的次级页表统计，例如部分虚拟化/IOMMU 页表 |
| `Percpu` | percpu 分配内存 |
| `Dirty` | 等待写回的脏内存 |
| `Writeback` | 正在写回的内存 |
| `WritebackTmp` | 临时写回缓冲，例如 FUSE 的部分路径 |
| `NFS_Unstable`、`Bounce` | 历史或特定路径统计；Linux 6.1 中部分此类字段固定为 0 |
| `VmallocTotal` | vmalloc 虚拟地址区域容量，不是物理 RAM |
| `VmallocUsed` | vmalloc 分配的相关内存统计，详情可查 `vmallocinfo` |
| `VmallocChunk` | Linux 6.1 中保留输出为 0，不应拿它诊断最大可分配 vmalloc 区间 |
| `HardwareCorrupted` | 被标记为硬件损坏的内存 |

持续增长的 `SUnreclaim` 可以提示内核对象增长，但不能单凭一个总量认定泄漏。需要进一步看 `slabinfo` 中哪些缓存增长，以及增长是否与连接数、inode 数或设备工作量相符。

#### Swap、承诺分配与大页

| 字段 | 含义与解读 |
| --- | --- |
| `SwapTotal`、`SwapFree` | 配置的 swap 总容量和空闲容量 |
| `Zswap` | 配置支持时，zswap 压缩池实际占用的 RAM |
| `Zswapped` | 进入 zswap 的数据按压缩前计算的容量 |
| `CommitLimit` | 承诺分配限制，计算取决于 swap、内存和 overcommit 配置 |
| `Committed_AS` | 当前承诺的虚拟内存量，不等于 RSS 或实际已触碰的物理内存 |
| `AnonHugePages` | 匿名透明大页相关内存 |
| `ShmemHugePages`、`ShmemPmdMapped` | shmem 的大页及 PMD 映射统计 |
| `FileHugePages`、`FilePmdMapped` | 文件大页及 PMD 映射统计 |
| `HugePages_Total`、`HugePages_Free` | 默认大小 HugeTLB 池总页数、空闲页数；这里的单位是大页“个数” |
| `HugePages_Rsvd` | 已承诺但尚未实际分配的大页数量，可与 Free 存在关系而非独立可相加分类 |
| `HugePages_Surp` | 超出配置池目标的 surplus 大页数量 |
| `Hugepagesize` | 默认 HugeTLB 页大小 |
| `Hugetlb` | HugeTLB 页占用的总内存，可涉及多种页大小 |
| `CmaTotal`、`CmaFree` | CMA 区域总容量和空闲容量 |
| `DirectMap4k`、`DirectMap2M`、`DirectMap1G` | 某些架构直接映射区域使用不同页大小的覆盖量，不是应用实际用量 |

**不要把 meminfo 全部行相加**：很多字段相互包含或重叠。`Committed_AS > CommitLimit` 是否异常，也必须结合 `vm.overcommit_memory` 的模式判断。Swap 已使用不意味着当前正在频繁换页，应看下一节的 `pswpin/pswpout` 差分。

### 4.2 /proc/vmstat：内存机制的计数器

该文件通常为 `名称 数值` 格式，但**并非所有行都是累计值，单位也不全相同**。

| 字段/字段族 | 类型与单位 | 含义 |
| --- | --- | --- |
| `nr_free_pages` | 当前页数 | 空闲基础页 |
| `nr_active_anon`、`nr_inactive_anon` | 当前页数 | 匿名 LRU 页 |
| `nr_active_file`、`nr_inactive_file` | 当前页数 | 文件 LRU 页 |
| `nr_anon_pages`、`nr_file_pages` | 当前页数 | 匿名页、文件类页统计 |
| `nr_dirty`、`nr_writeback` | 当前页数 | 脏页、写回中的页 |
| `nr_slab_reclaimable`、`nr_slab_unreclaimable` | 当前页数 | Linux 6.1 的 proc 输出已转换成页数；内部 `NR_SLAB_*_B` 按字节记账，不应与输出单位混淆 |
| `pgpgin`、`pgpgout` | 累计 KiB | 页面 I/O 输入/输出，Linux 6.1 输出时做了单位转换 |
| `pswpin`、`pswpout` | 累计页数 | swap 换入/换出 |
| `pgfault` | 累计次数 | 缺页事件总数，含不需要存储读取的次缺页 |
| `pgmajfault` | 累计次数 | 主缺页事件，通常涉及读取后备存储 |
| `pgscan_kswapd`、`pgscan_direct` | 累计扫描页数 | 后台回收、直接回收扫描的页面 |
| `pgsteal_kswapd`、`pgsteal_direct` | 累计回收页数 | 对应路径成功回收的页面 |
| `allocstall_*` | 累计事件数 | 分配因内存紧张而进入直接回收等停顿路径 |
| `compact_stall`、`compact_success`、`compact_fail` | 累计事件数 | 内存规整尝试与结果 |
| `workingset_refault_*` | 累计事件统计 | 被回收页面又被访问等工作集线索，需结合具体页类型 |
| `thp_fault_alloc`、`thp_fault_fallback`、`thp_collapse_alloc` | 累计事件数 | 透明大页分配、回退及合并活动 |
| `oom_kill` | 累计次数 | OOM 杀死任务的相关统计，具体受害者需查日志 |

`pgfault` 很高并不一定有磁盘压力：首次访问匿名页、写时复制等也会缺页。后台 `kswapd` 活动不一定异常；直接回收和 `allocstall` 持续增长更值得结合延迟检查。

可以粗看某回收路径的效率：

```text
reclaim_ratio = delta_pgsteal / delta_pgscan
```

分母为 0 时不计算。扫描很多但回收很少，可能是回收效率低；仍需结合工作集、脏页和内存类型分析。

### 4.3 /proc/buddyinfo：物理连续内存碎片

```text
Node 0, zone   Normal  120 80 30 10 4 2 1 0 0 0 0
```

每行是 NUMA 节点的一个 zone；数字从 order 0 开始，对应**空闲块数量**：

```text
order k 的每块容量 = 2^k * PAGE_SIZE
该列空闲容量       = 块数 * 2^k * PAGE_SIZE
```

例如 4 KiB 基础页时，order 9 的一块是 2 MiB。数字 `4` 的含义取决于它在哪一列，不能把所有列直接相加当成页数。

**适用场景**：还有不少空闲内存，但高阶连续分配失败。高阶块少可能提示外部碎片；真正能否分配还受 zone、水位、迁移类型、GFP 标志、NUMA 策略和 CMA 约束。

### 4.4 /proc/pagetypeinfo 与 /proc/zoneinfo

`pagetypeinfo` 按节点、zone、迁移类型展示不同 order 的空闲块，并报告 pageblock 大小和不同类型 pageblock 数量。

| 迁移类型 | 主要含义 |
| --- | --- |
| `Unmovable` | 不便迁移的分配 |
| `Movable` | 可迁移页面 |
| `Reclaimable` | 可回收分配 |
| `HighAtomic` | 为部分高阶原子分配保留的块，是否显示取决于配置 |
| `CMA` | CMA 区域相关页面，是否显示取决于配置 |
| `Isolate` | 被隔离的页面块 |

`zoneinfo` 更适合看水位和分配范围：

| 项目 | 含义 |
| --- | --- |
| `pages free` | zone 当前空闲页数 |
| `min`、`low`、`high` | 内存水位，用于分配和回收判断 |
| `boost` | 动态水位提升相关值 |
| `spanned` | zone 跨越的页框范围，可能包含空洞 |
| `present` | 实际存在的物理页 |
| `managed` | 纳入常规页分配器管理的页 |
| `protection:` | 跨 zone 分配时的低端内存保护/保留相关信息 |
| per-CPU pageset | 每 CPU 页缓存的计数和参数 |

页数换算要用本机 `PAGE_SIZE`。全局剩余内存很多，不代表特定 DMA/DMA32 zone 有足够资源。

### 4.5 /proc/slabinfo 与 /proc/vmallocinfo

`slabinfo` 常需要更高权限，应该先看文件首行的版本和列说明：

```text
slabinfo - version: 2.1
# name <active_objs> <num_objs> <objsize> <objperslab> <pagesperslab> ...
```

| 列 | 含义 |
| --- | --- |
| `name` | 对象缓存名称，如 `dentry`、`inode_cache`、`kmalloc-*` |
| `active_objs` | 活跃对象数 |
| `num_objs` | 当前缓存中总对象数 |
| `objsize` | 对象大小，字节 |
| `objperslab` | 每个 slab 的对象容量 |
| `pagesperslab` | 每个 slab 占用的基础页数 |
| `slabdata` 后的 `num_slabs` 等 | slab 数量等统计，按实际表头解释 |

`active_objs * objsize` 可粗估活跃对象负载，但不是缓存全部物理占用；还存在空闲对象、对齐和元数据。slab 物理占用可按 `num_slabs * pagesperslab * PAGE_SIZE` 粗估。不同分配器的部分列语义和可用程度不同，SLUB 的统计也不是所有对象实时一致的事务快照。

`vmallocinfo` 展示 vmalloc/vmap 区间、大小、调用点和可能的 `pages=`、`phys=`、`vmalloc`、`vmap`、`ioremap` 等标签。它适合定位具体分配来源；虚拟区域大小不一定等于新占用的物理页容量，`vmap` 也可能只是映射已有页面。

## 5. /proc/pressure：PSI 资源压力

需要相应内核配置与运行时支持：

```bash
cat /proc/pressure/cpu
cat /proc/pressure/memory
cat /proc/pressure/io
```

```text
some avg10=12.50 avg60=4.20 avg300=1.10 total=12345678
full avg10=1.00 avg60=0.30 avg300=0.10 total=2345678
```

| 字段 | 含义 |
| --- | --- |
| `some` | 至少有部分任务因该资源而停顿的时间占比 |
| `full` | 所有非空闲任务同时因该资源而停顿的时间占比 |
| `avg10`、`avg60`、`avg300` | 10、60、300 秒尺度的平滑趋势，单位为百分比 |
| `total` | 累计停顿时间，微秒；不是所有任务各自等待时间的简单总和 |

PSI 回答的是“资源竞争造成了多少任务停顿”，不是“资源使用了多少”。`memory` PSI 高说明任务经历内存相关停顿，未必仅是物理内存总量偏小；`io` PSI 应结合存储延迟和回收路径分析。

**Linux 6.1 的系统级 CPU `full` 没有有意义的定义，兼容输出为 0**；不能由此判断 CPU 无压力。cgroup 级 PSI 语义和作用域需单独考虑。

自定义窗口的停顿比例可按：

```text
stall_percent = 100 * delta_total_us / (dt_seconds * 1000000)
```

不要用墙钟校时前后的差值计算。PSI 文件也支持监测触发器写入，但本文只演示读取，不改变监测状态。

## 6. 磁盘、块设备与 swap

### 6.1 /proc/partitions

```text
major minor  #blocks  name
 259     0  500107608 nvme0n1
 259     1     524288 nvme0n1p1
 259     2  499582272 nvme0n1p2
```

`major`、`minor` 是设备号；`#blocks` 是以 1024 字节为单位报告的容量；`name` 是块设备名称。这里包含整盘及分区等块设备条目，不是文件系统已用空间，查看后者应使用 `df`。

### 6.2 /proc/diskstats：逐列读懂 I/O 统计

```text
8 0 sda 1000 20 8000 3000 2000 30 16000 6000 0 4000 10000 0 0 0 0 10 50
```

前三列是 `major minor name`。下面的序号**从设备名之后开始**；Linux 6.1 常见为 17 个统计字段：

| 序号 | 字段 | 单位与含义 |
| --- | --- | --- |
| 1 | reads completed | 成功完成的读请求数 |
| 2 | reads merged | 合并的读请求数 |
| 3 | sectors read | 已读取的 512 字节统计扇区数 |
| 4 | time reading | 所有读请求累计耗时，毫秒，涉及等待和处理 |
| 5 | writes completed | 成功完成的写请求数 |
| 6 | writes merged | 合并的写请求数 |
| 7 | sectors written | 已写入的 512 字节统计扇区数 |
| 8 | time writing | 所有写请求累计耗时，毫秒 |
| 9 | I/Os in progress | 当前进行中的请求数，是瞬时值 |
| 10 | time doing I/Os | 设备 I/O 活动时间相关统计，毫秒；不是各请求耗时总和 |
| 11 | weighted time doing I/Os | 按进行中请求数加权的累计时间，毫秒 |
| 12 | discards completed | 完成的 discard 请求数 |
| 13 | discards merged | 合并的 discard 请求数 |
| 14 | sectors discarded | discard 的 512 字节统计扇区数 |
| 15 | time discarding | discard 累计耗时，毫秒 |
| 16 | flushes completed | 完成的 flush 请求数；分区不追踪此统计 |
| 17 | time flushing | flush 累计耗时，毫秒 |

除第 9 项外，通常是累计计数。字段增补有历史版本差异，应按运行内核及实际列数解释。

常见读写指标的近似计算：

```text
read_IOPS  = delta_reads_completed / dt
write_IOPS = delta_writes_completed / dt
read_Bps   = delta_sectors_read * 512 / dt
write_Bps  = delta_sectors_written * 512 / dt

read_await_ms  = delta_time_reading / delta_reads_completed
write_await_ms = delta_time_writing / delta_writes_completed
avg_inflight  = delta_weighted_time_ms / (dt * 1000)
io_active_pct = 100 * delta_io_time_ms / (dt * 1000)
```

分母为 0 时不计算。上面的 await 限定为读/写请求，不把 discard 和 flush 混进分母；它不是纯硬件服务时间，也不能表示 P99 尾延迟。

!!! warning "diskstats 的四个常见陷阱"
    1. 即使设备采用 4 KiB 逻辑扇区，统计扇区仍按 512 字节换算。
    2. 整盘、分区、device-mapper、RAID 和底层设备可能记录同一 I/O 链路，全部相加会重复。
    3. `io_active_pct` 不等于设备性能极限百分比，尤其不能凭 NVMe 的高活动率断言已经饱和。
    4. Linux 5.0 之后的活动时间统计有具体计时边界，并发长请求时可能漏计部分时间，不能视为精确忙碌墙钟。

### 6.3 /proc/swaps

```text
Filename                Type        Size       Used     Priority
/swapfile               file        2097148    262144    -2
/dev/zram0              partition   1048576     65536    100
```

`Size` 和 `Used` 通常为 KiB。`Priority` 越高，swap 分配优先级越高；相同优先级区域可分担使用。

zram 的 swap 逻辑使用量不是压缩后实际 RAM 占用；压缩统计应查对应 sysfs 接口。当前 Used 不为 0 也不能证明正在抖动，应结合 `vmstat` 的换入换出增量与 PSI。

## 7. 挂载、文件系统与锁

### 7.1 /proc/filesystems

```text
nodev   proc
nodev   sysfs
        ext4
        xfs
```

这是当前内核注册的文件系统类型，不是当前挂载列表，也不一定包含尚未加载模块提供的类型。`nodev` 表示该文件系统不依赖块设备作为常规后备存储，不是“禁止设备节点”的 mount 选项。

### 7.2 `/proc/mounts` 与 `/proc/<pid>/mountinfo`

`/proc/mounts` 在 Linux 上通常是 `/proc/self/mounts` 的符号链接，反映访问进程的挂载命名空间。常见列为：

```text
source target fstype options dump pass
```

若需要区分 bind mount、层叠挂载、传播关系或容器挂载拓扑，应优先读 `mountinfo`：

```text
36 25 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw
```

| 字段 | 含义 |
| --- | --- |
| `36` | mount ID |
| `25` | 父 mount ID |
| `8:1` | 该文件系统设备的 major:minor |
| `/`，第 4 项 | 此挂载对应的文件系统内部根，bind mount 时很重要 |
| `/`，第 5 项 | 挂载点 |
| `rw,relatime` | 本次挂载的 mount 选项 |
| `shared:1` 等可选项 | 挂载传播信息，如 `shared`、`master`、`propagate_from`、`unbindable` |
| `-` | 固定分隔标志 |
| `ext4` | 文件系统类型 |
| `/dev/sda1` | mount source |
| 最后 `rw` | superblock 选项 |

解析时先找到 ` - ` 分隔符，不要假定可选字段数量固定。路径中的空格、tab、换行和反斜杠可能以 `\040`、`\011`、`\012`、`\134` 转义。父挂载有时在进程 root 范围之外，不应要求其 ID 总能在同一列表中找到。

### 7.3 /proc/locks

```text
1: POSIX  ADVISORY  WRITE 1234 08:01:56789 0 EOF
2: FLOCK  ADVISORY  READ  2345 08:01:67890 0 EOF
```

常见内容包括锁编号、锁类型、访问性质、读写类型、持有者 PID、`major:minor:inode` 和范围。`EOF` 表示范围延续到文件末尾；等待中的锁可能有 `->` 标记。

`POSIX` 常对应 `fcntl` 记录锁，`FLOCK` 常对应 `flock`，`OFDLCK` 是 open-file-description 锁。OFD 锁的 PID 可为 `-1`，不能当作“进程不存在”。该文件也不是所有内核锁的列表，不包含所有 mutex、spinlock 或用户态锁。

## 8. /proc/net：网络接口、协议与 socket

### 8.1 命名空间范围

`/proc/net` 是访问进程网络命名空间的视图，通常链接到 `/proc/self/net`。`/proc/<pid>/net/` 展示**目标进程所在网络命名空间**，不是“只列目标进程拥有的 socket”。

要关联某个进程的 socket，需要同时检查 `<pid>/fd/` 中的 `socket:[inode]`。采样期间连接可能关闭，关联不是原子操作；更方便的工具通常是 `ss`。

### 8.2 /proc/net/dev

```text
Inter-|   Receive                                                |  Transmit
 face |bytes packets errs drop fifo frame compressed multicast   |bytes packets errs drop fifo colls carrier compressed
  eth0: 1000000 1000 0 2 0 0 0 10 2000000 1500 0 0 0 0 0 0
```

每个接口冒号后有 16 个数：

| 方向 | 字段顺序 | 含义 |
| --- | --- | --- |
| 接收 | `bytes packets errs drop fifo frame compressed multicast` | 字节、包、错误、丢弃、FIFO、帧相关错误、压缩包、多播包 |
| 发送 | `bytes packets errs drop fifo colls carrier compressed` | 字节、包、错误、丢弃、FIFO、冲突、载波相关错误、压缩包 |

计数通常自接口创建/统计重置以来累积。部分列合并了更细的底层统计，具体原因还应看 `ip -s link`、驱动统计和 `ethtool -S`。

```text
RX_Bps = delta_rx_bytes / dt
TX_Bps = delta_tx_bytes / dt
RX_bps = RX_Bps * 8
```

这是接口层统计，不等于应用有效载荷吞吐量。loopback、veth、bridge、bond、隧道和物理接口可能记录同一流量链路，不能不分层地全部求和。

### 8.3 /proc/net/snmp 与 /proc/net/netstat

输出常为**一行字段名、一行对应数值**：

```text
Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens AttemptFails EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts
Tcp: 1 200 120000 -1 100 200 3 4 8 10000 12000 50 0 20
```

| 字段 | 常见含义 |
| --- | --- |
| `Tcp.ActiveOpens`、`PassiveOpens` | 主动、被动打开连接累计次数 |
| `Tcp.AttemptFails`、`EstabResets` | 连接失败、已建立连接重置相关累计计数 |
| `Tcp.CurrEstab` | 当前处于 ESTABLISHED/CLOSE-WAIT 的连接数，是瞬时值 |
| `Tcp.InSegs`、`OutSegs`、`RetransSegs` | TCP 接收、发送、重传段统计 |
| `Tcp.InErrs`、`OutRsts` | 输入错误、发送 reset 统计 |
| `Udp.InDatagrams`、`OutDatagrams` | UDP 数据报统计 |
| `Udp.NoPorts` | 收到报文但无匹配监听端口 |
| `Udp.InErrors`、`RcvbufErrors`、`SndbufErrors` | UDP 错误与缓冲区不足相关统计 |
| `TcpExt.ListenOverflows`、`ListenDrops` | TCP 监听路径溢出、丢弃线索 |
| `TcpExt.TCPTimeouts` 等 | TCP 扩展诊断统计，需按实际表头匹配 |

`netstat` 文件不是 `netstat` 命令的文本输出。编写解析器应按名称匹配，不硬编码列下标；头行和数值行的协议前缀必须一致。配置项如 `RtoMin`、`MaxConn` 也不是累计值。

`RetransSegs` 增长可提示重传，但不是应用请求失败数，不能直接得出业务失败率。

### 8.4 /proc/net/sockstat、tcp、udp 与 unix

`sockstat` 的典型内容：

```text
sockets: used 120
TCP: inuse 20 orphan 0 tw 30 alloc 40 mem 10
UDP: inuse 8 mem 4
```

`inuse` 为使用中的协议 socket，`orphan` 为失去用户空间文件引用的 TCP socket 统计，`tw` 为 TIME_WAIT 统计，`alloc` 为分配数量。TCP/UDP 的 `mem` 以基础页计，不是字节。

**不要假定 sockstat 每个字段都严格按网络命名空间隔离**：Linux 6.1 中部分协议内存、分配和 orphan 计数来自全局统计，应结合 `net/ipv4/proc.c` 实现判断作用域。

`tcp`、`tcp6`、`udp`、`udp6` 通常包含：

| 项目 | 含义 |
| --- | --- |
| `sl` | 输出条目编号，不是稳定连接 ID |
| `local_address`、`rem_address` | 十六进制地址和端口 |
| `st` | 协议状态，TCP 是十六进制 TCP 状态码 |
| `tx_queue:rx_queue` | 发送/接收队列统计；语义与协议及状态有关，不应在 LISTEN 等状态下都按普通数据字节理解 |
| `tr`、`tm->when`、`retrnsmt` | 定时器和重传相关内部状态 |
| `uid` | socket 关联用户身份 |
| `inode` | socket inode，可与进程 `fd/` 关联 |

常见 TCP 状态：

| 十六进制 | 状态 | 十六进制 | 状态 |
| --- | --- | --- | --- |
| `01` | ESTABLISHED | `02` | SYN_SENT |
| `03` | SYN_RECV | `04` | FIN_WAIT1 |
| `05` | FIN_WAIT2 | `06` | TIME_WAIT |
| `07` | CLOSE | `08` | CLOSE_WAIT |
| `09` | LAST_ACK | `0A` | LISTEN |
| `0B` | CLOSING | | |

在常见小端机器的 IPv4 输出中，`0100007F:1F90` 对应 `127.0.0.1:8080`；端口按十六进制数转换，地址要按输出的架构字节序解释。IPv6 不能简单把整个字符串倒序。优先使用：

```bash
ss -lntp
ss -ant
ss -unap
```

`/proc/net/unix` 列出 Unix-domain socket，包含引用、标志、类型、状态、inode 及可能的路径；抽象命名空间地址常以 `@` 展示，不一定对应可访问的磁盘文件。

## 9. `/proc/<pid>`：进程与线程

以下命令中的 `pid` 应替换为目标 PID，或先设置：

```bash
pid=1234
```

### 9.1 status：易读的身份、内存与安全状态

```text
Name:   example
State:  S (sleeping)
Tgid:   1234
Pid:    1234
PPid:   1
TracerPid:      0
Uid:    1000    1000    1000    1000
Gid:    1000    1000    1000    1000
VmSize: 200000 kB
VmRSS:  50000 kB
Threads:        4
Cpus_allowed_list:      0-3
voluntary_ctxt_switches:        100
nonvoluntary_ctxt_switches:     20
```

| 字段 | 含义 |
| --- | --- |
| `Name` | task comm 名称，通常最多显示 15 字节有效内容；不是完整可执行路径 |
| `Umask` | 文件创建权限掩码，八进制 |
| `State` | 任务状态，见下表 |
| `Tgid`、`Pid` | 线程组 ID、当前 task ID；看线程目录时二者可能不同 |
| `PPid`、`TracerPid` | 父进程 ID、跟踪者 ID，0 通常表示未被跟踪 |
| `Uid`、`Gid` | real、effective、saved-set、filesystem 四种身份，按此顺序 |
| `Groups` | 附加组列表 |
| `FDSize` | 当前分配的文件描述符表槽位数，不是打开 FD 个数，也不是 RLIMIT_NOFILE |
| `NStgid`、`NSpid`、`NSpgid`、`NSsid` | 从该 procfs 可见命名空间到更内层命名空间的 ID 层级 |
| `VmPeak`、`VmSize` | 虚拟地址空间峰值、当前大小 |
| `VmHWM`、`VmRSS` | 常驻集峰值、当前常驻集；快速统计可能不精确 |
| `RssAnon`、`RssFile`、`RssShmem` | 常驻匿名、文件、共享内存组成；约构成 VmRSS |
| `VmData`、`VmStk`、`VmExe`、`VmLib` | 数据、栈、可执行代码、库代码相关虚拟内存统计 |
| `VmPTE` | 进程页表内存 |
| `VmLck`、`VmPin` | 锁定内存、内核记账的 pinned 内存；VmPin 不是所有 DMA/pinning 的完整度量 |
| `VmSwap` | 私有匿名内存的 swap 使用，不包括全部 shmem swap |
| `HugetlbPages` | HugeTLB 内存，需考虑其与 RSS/PSS 的统计差异 |
| `Threads` | 线程组中的线程数 |
| `SigQ` | 当前 real UID 的排队信号数/相应限制，不只是此进程独有的信号数 |
| `SigPnd`、`ShdPnd` | 线程、线程组的 pending signal 位图 |
| `SigBlk`、`SigIgn`、`SigCgt` | 屏蔽、忽略、捕获信号的位图 |
| `CapInh`、`CapPrm`、`CapEff`、`CapBnd`、`CapAmb` | capability 集合的十六进制位图 |
| `NoNewPrivs` | 是否设置 no_new_privs |
| `Seccomp` | 0 为未启用，1 为 strict，2 为 filter 模式 |
| `Seccomp_filters` | 安装的 seccomp filter 数量，取决于配置 |
| `Cpus_allowed`、`Cpus_allowed_list` | 此任务允许使用的 CPU 位图/列表 |
| `Mems_allowed`、`Mems_allowed_list` | 允许使用的 NUMA 内存节点 |
| `voluntary_ctxt_switches` | 当前任务主动让出 CPU 的累计次数 |
| `nonvoluntary_ctxt_switches` | 当前任务非自愿切换的累计次数，例如被抢占 |

| 状态 | 含义 | 常见解读 |
| --- | --- | --- |
| `R` | running/runnable | 正在执行或等 CPU |
| `S` | interruptible sleeping | 可中断睡眠，许多正常等待属于此类 |
| `D` | uninterruptible sleep | 不可中断等待，常见于 I/O，也可能是其他内核等待 |
| `T` | stopped | 被停止，例如 job control |
| `t` | tracing stop | 跟踪停止 |
| `Z` | zombie | 已退出但尚未被父进程回收 |
| `X` | dead | 死亡状态，通常很短暂 |
| `I` | idle | 特定空闲内核线程状态 |

读取 `/proc/<pid>/status` 时，一些项目是线程组级，状态和上下文切换等则是被查询 task 的信息；要查热点线程，应访问 `task/<tid>/status`，不能把主线程切换数当作全进程总数。

### 9.2 stat：位置固定的进程统计

`/proc/stat` 是全局统计，`/proc/<pid>/stat` 是目标进程统计，二者格式完全不同。后者是一条位置编码记录。

!!! warning "不要直接 awk '{print $14}'"
    第 2 字段 `comm` 放在括号中，可以包含空格和括号。直接按空白拆整行会错位。稳健解析应读取完整内容，识别 PID 与括号名称区域，通常以最后一个右括号确定名称结束，再解析状态及后续数字；不要只取第一处右括号。

Linux 6.1 的 52 个字段：

| 位置 | 字段 | 含义/单位 |
| --- | --- | --- |
| 1 | `pid` | 任务 ID |
| 2 | `comm` | 括号内 task 名称，不是完整命令行 |
| 3 | `state` | 状态字符 |
| 4 | `ppid` | 父进程 ID |
| 5 | `pgrp` | 进程组 ID |
| 6 | `session` | session ID |
| 7 | `tty_nr` | 控制终端设备编码 |
| 8 | `tpgid` | 控制终端前台进程组 |
| 9 | `flags` | task 内核标志 |
| 10 | `minflt` | 次缺页次数，不需从存储装入页面 |
| 11 | `cminflt` | 已等待回收的子进程次缺页统计 |
| 12 | `majflt` | 主缺页次数 |
| 13 | `cmajflt` | 已等待回收的子进程主缺页统计 |
| 14 | `utime` | 用户态时间，clock ticks，包含 guest_time |
| 15 | `stime` | 内核态时间，clock ticks |
| 16 | `cutime` | 已等待回收的子进程用户态时间，clock ticks |
| 17 | `cstime` | 已等待回收的子进程内核态时间，clock ticks |
| 18 | `priority` | 内核导出的调度优先级；普通策略常为 nice+20，实时策略按特殊负数编码，不是直接的 nice 值 |
| 19 | `nice` | nice 值，通常 -20 到 19 |
| 20 | `num_threads` | 线程数 |
| 21 | `itrealvalue` | 历史字段，现代内核为 0 |
| 22 | `starttime` | 启动后开始此任务的时间，clock ticks；受时间命名空间影响 |
| 23 | `vsize` | 虚拟内存大小，字节 |
| 24 | `rss` | 常驻基础页数，快速统计可能不精确 |
| 25 | `rsslim` | RSS 限制，字节；不应当作现代 Linux 的有效内存隔离上限 |
| 26 | `startcode` | 代码区域起始地址 |
| 27 | `endcode` | 代码区域结束地址 |
| 28 | `startstack` | 主栈起始地址 |
| 29 | `kstkesp` | 历史栈指针字段，现代实现/架构下通常为 0，部分 core dump 路径例外 |
| 30 | `kstkeip` | 历史指令指针字段，同样不宜用作通用实时寄存器接口 |
| 31 | `signal` | 历史 pending signal 位图，优先用 status |
| 32 | `blocked` | 历史 blocked signal 位图 |
| 33 | `sigignore` | 历史 ignored signal 位图 |
| 34 | `sigcatch` | 历史 caught signal 位图 |
| 35 | `wchan` | Linux 6.1 为兼容/安全使用 0/1 指示，不是实际内核等待地址；符号另查 wchan 文件 |
| 36 | `nswap` | 历史字段，现代内核为 0 |
| 37 | `cnswap` | 历史字段，现代内核为 0 |
| 38 | `exit_signal` | 退出时通知父任务的信号 |
| 39 | `processor` | 当前或最近执行的逻辑 CPU 编号 |
| 40 | `rt_priority` | 实时优先级 |
| 41 | `policy` | 调度策略数字，例如 0 NORMAL、1 FIFO、2 RR、3 BATCH、5 IDLE、6 DEADLINE |
| 42 | `delayacct_blkio_ticks` | 块 I/O 等待累计 clock ticks，依赖 delay accounting 支持与启用 |
| 43 | `guest_time` | guest CPU 时间，clock ticks，已经包含在 utime |
| 44 | `cguest_time` | 子进程 guest 时间，已包含在相应用户态统计 |
| 45 | `start_data` | 数据区域起始地址 |
| 46 | `end_data` | 数据区域结束地址 |
| 47 | `start_brk` | 初始 brk 地址 |
| 48 | `arg_start` | 参数区起始地址 |
| 49 | `arg_end` | 参数区结束地址 |
| 50 | `env_start` | 环境变量区起始地址 |
| 51 | `env_end` | 环境变量区结束地址 |
| 52 | `exit_code` | wait 形式的退出状态编码，不是直接的 shell exit status |

部分敏感字段受 ptrace 类权限检查影响；值为 0 或占位值，不一定意味着目标真的没有相应地址或数据。

单 CPU 为 100% 口径的进程 CPU 利用率：

```text
process_cpu_percent = 100 * delta(utime + stime) / CLK_TCK / dt
```

多线程程序可超过 100%。如需全机容量占比，再按选定的 CPU 容量口径归一化；容器配额不可简单按宿主 CPU 个数代替。

`/proc/<pid>/stat` 的 CPU 时间和缺页等在 Linux 6.1 的线程组视图中会汇总相关线程；`/proc/<pid>/task/<tid>/stat` 为单线程视图，状态、processor 等则描述具体 task。

PID 会复用，长期监控应同时记录 `pid + starttime`，否则可能把新进程的计数与已退出进程相减。

### 9.3 statm：七个数，单位都是页

```text
50000 12500 3000 100 0 20000 0
```

| 位置 | 字段 | 含义 |
| --- | --- | --- |
| 1 | `size` | 总虚拟内存页数 |
| 2 | `resident` | 常驻页数 |
| 3 | `shared` | 常驻文件页和 shmem 等快速统计，不是所有“与其他进程实际共享”的页面 |
| 4 | `text` | 代码相关页数 |
| 5 | `lib` | 历史字段，现代内核为 0 |
| 6 | `data` | 数据与栈相关页数 |
| 7 | `dt` | 历史字段，现代内核为 0 |

换算：`bytes = pages * PAGE_SIZE`。RSS/shared 快速计数可能存在误差，不能用 `resident - shared` 当作精确 USS。

### 9.4 maps、smaps 与 smaps_rollup

`maps` 展示虚拟内存区域：

```text
00400000-00452000 r-xp 00000000 08:01 12345 /usr/bin/example
00652000-00653000 rw-p 00052000 08:01 12345 /usr/bin/example
00a00000-00b00000 rw-p 00000000 00:00 0     [heap]
7fff00000000-7fff00021000 rw-p 00000000 00:00 0 [stack]
```

| 列 | 含义 |
| --- | --- |
| `address` | 虚拟地址半开区间 `[start, end)` |
| `perms` | `r/w/x` 访问权限；`p` 为 private，`s` 为 shared |
| `offset` | 在映射后备对象中的偏移，十六进制 |
| `dev` | 文件设备 major:minor，十六进制 |
| `inode` | 后备文件 inode；匿名映射通常为 0 |
| `pathname` | 文件路径或 `[heap]`、`[stack]`、`[vdso]` 等标识，可为空 |

`p` 不表示所有页都已经私有占用：写时复制映射的物理页仍可能共享。区间大小也不表示所有页面已经常驻。`(deleted)` 表示后备路径已删除，但映射仍可能存活。

`smaps` 在每个 VMA 后增加统计：

| 字段 | 含义 |
| --- | --- |
| `Size` | VMA 虚拟容量 |
| `Rss` | 常驻容量 |
| `Pss` | 共享物理页按映射者分摊后的容量 |
| `Shared_Clean`、`Shared_Dirty` | 共享干净页/脏页 |
| `Private_Clean`、`Private_Dirty` | 私有干净页/脏页 |
| `Referenced` | 页面访问标记相关统计，不是严格固定时间窗的工作集 |
| `Anonymous` | 匿名页容量 |
| `AnonHugePages` | 匿名透明大页容量 |
| `Swap`、`SwapPss` | swap 容量、按共享关系分摊的 swap 容量 |
| `KernelPageSize`、`MMUPageSize` | 页大小相关信息，不应把一个值强行解释为 VMA 所有页的唯一粒度 |
| `Locked` | 锁定相关容量 |
| `Shared_Hugetlb`、`Private_Hugetlb` | HugeTLB 单列统计；历史原因通常不计入 Rss/Pss 及普通共享/私有清脏分类 |
| `THPeligible` | 映射是否满足相关透明大页资格条件，不表示已实际获得大页 |
| `VmFlags` | VMA 标志，如 `rd`、`wr`、`ex`、`sh`、`gd`、`lo`、`dd` 等 |

例如一个 4 KiB 页面被两个进程共享，每个进程 RSS 都记 4 KiB，PSS 通常各记约 2 KiB。粗估普通页面 USS 常取 `Private_Clean + Private_Dirty`；还需单独考虑 HugeTLB 等特殊页面。

`smaps_rollup` 聚合全进程统计，并可包含 `Pss_Anon`、`Pss_File`、`Pss_Shmem`。若只需总量，通常比逐 VMA 解析方便。

读取 smaps 系列要遍历页表，代价高于 status/statm，不适合对海量进程超高频轮询；它也不是冻结目标后取得的绝对一致快照。

### 9.5 cmdline、environ、comm 与符号链接

| 路径 | 内容与注意事项 |
| --- | --- |
| `cmdline` | 参数一般以 NUL 字节分隔，不是普通空格分隔字符串；进程可改写，内核线程/僵尸可为空 |
| `environ` | NUL 分隔的环境变量区，通常反映启动时区域，不保证完整展示运行后所有 `setenv` 修改 |
| `comm` | task 名称，可由进程修改并截断，不是唯一身份凭据 |
| `exe` | 可执行文件符号链接；部分权限/进程状态下不可访问，已删除文件可能带 `(deleted)` |
| `cwd` | 当前工作目录符号链接 |
| `root` | 进程文件系统根视图入口，受 chroot/命名空间和权限影响 |

```bash
tr '\0' '\n' < "/proc/$pid/cmdline"
readlink "/proc/$pid/exe"
readlink "/proc/$pid/cwd"
```

把 NUL 转成换行便于人读，但参数本身也可含换行，不能把这一展示格式当成无损解析格式。

!!! warning "敏感信息"
    命令行和环境变量可能包含 token、数据库密码、证书位置等。不要把 environ、完整 cmdline 或内存转储无筛选地上传到公开工单。

### 9.6 fd/ 与 fdinfo/

`fd/` 中每个数字是已打开的文件描述符：

```text
0 -> /dev/pts/0
3 -> /var/log/example.log
4 -> socket:[123456]
5 -> pipe:[234567]
6 -> anon_inode:[eventpoll]
```

`fdinfo/<fd>` 可提供：

```text
pos:    1024
flags:  0100002
mnt_id: 36
ino:    12345
```

- `pos` 是文件位置，但不是所有对象都支持常规文件偏移。
- `flags` 按八进制展示相关打开标志，不要按十进制解码。
- `mnt_id` 可关联相应 `mountinfo`。
- epoll、eventfd、signalfd、timerfd、inotify 等对象有各自的附加字段。

**适用场景**：FD 泄漏、socket 所属关联、日志文件被删除但磁盘空间未释放、epoll 对象诊断。FD 号可迅速复用，列表和 fdinfo 应允许竞争失败。

### 9.7 io：进程 I/O 统计

| 字段 | 含义 |
| --- | --- |
| `rchar` | 读取接口返回的数据量，包括缓存命中和某些非磁盘 I/O，不等于实际磁盘读取 |
| `wchar` | 写入接口处理的数据量，不等于设备已经完成的落盘量 |
| `syscr`、`syscw` | 内核记账的读/写 I/O 接口调用次数，不是单个 API 名称的纯函数调用计数 |
| `read_bytes` | 尝试统计任务真正导致从存储层读取的字节 |
| `write_bytes` | 尝试统计任务导致写向存储层的字节，通常在页面变脏阶段记账 |
| `cancelled_write_bytes` | 由于截断等使已记账脏数据不再需要写出的字节，可涉及别的任务弄脏的数据 |

`rchar` 很高但 `read_bytes` 很低可能是页缓存命中，不一定是漏统计。`write_bytes` 也不能拿来证明 `fsync` 已完成。简单计算 `write_bytes - cancelled_write_bytes` 不保证得到精确、非负的最终物理写入量。

Linux 6.1 的进程视图会汇总线程组和相关已回收子任务的 I/O 记账；单线程看 `task/<tid>/io`。是否有字段及精度还受内核配置和并发更新影响。

### 9.8 sched、schedstat、wchan、stack 与 syscall

| 文件 | 用途与边界 |
| --- | --- |
| `sched` | 调度实体统计，如执行时间、切换次数和部分调度器内部数据；依赖配置，字段不宜作为无版本差异的固定接口 |
| `schedstat` | 常见三个数为 CPU 运行时间 ns、运行队列等待时间 ns、调度时间片次数；等待统计需相应 schedstats 配置/启用 |
| `wchan` | 睡眠时所在内核等待位置的符号；0 可能是运行中、不可用或权限限制 |
| `stack` | 内核栈回溯，不是用户态调用栈；通常需要权限和栈追踪配置 |
| `syscall` | 当前系统调用号、参数以及相关寄存器值，依赖架构和权限；可能显示 `running` 或特殊状态 |

长时间 `D` 状态可从 stack/wchan 判断在块设备、文件系统还是其他内核等待路径。`wchan` 单个符号不能代替完整调用链；`kill -9` 也不保证让不可中断等待立即结束。

### 9.9 limits、cgroup、ns/ 与 task/

- `limits`：各资源 soft/hard limit 及单位，例如打开文件数、地址空间、锁定内存、core 大小；它不是 cgroup 上限列表。
- `cgroup`：进程所属 cgroup。v2 常见 `0::/路径`；v1 可有多条控制器记录。路径本身也受 cgroup 命名空间影响。
- `ns/`：`pid`、`mnt`、`net`、`user`、`uts`、`ipc`、`cgroup`、`time` 等命名空间句柄链接；例如 `net:[4026531993]`。
- `task/<tid>/`：线程目录，适合按线程检查 CPU 使用、等待栈、亲和性和切换次数。Linux 中线程也是 task。
- `numa_maps`：逐映射 NUMA 策略、驻留节点和页数等，用于判断内存落点、跨 NUMA 访问及策略效果。

cgroup v2 的实际资源数据通常在 cgroupfs，例如 `memory.current`、`memory.max`、`memory.events`、`cpu.max`、`cpu.stat` 和 `io.stat`，而不是直接写在 `/proc/<pid>/cgroup` 这一行中。

### 9.10 oom_score、oom_score_adj 与高级内存接口

`oom_score` 是当前 OOM 选择相关评分线索，`oom_score_adj` 调整范围为 -1000 到 1000；-1000 会排除正常 OOM victim 选择。它不是进程内存硬上限，也不能保证进程不会被人工、容器管理器或其他机制终止。

OOM 的候选集合可能受 cgroup、cpuset、NUMA 策略等约束。不能简单将所有进程按一个评分排序，断言最高者在任何 OOM 场景中都一定被杀。

| 接口 | 内容/风险 |
| --- | --- |
| `<pid>/pagemap` | 二进制逐虚拟页条目，包含 present、swapped、soft-dirty 等位；PFN 通常需要额外权限 |
| `kpagecount`、`kpageflags` | 按物理页框索引的全局二进制映射计数/页标志，需权限 |
| `<pid>/mem` | 目标虚拟地址空间访问接口，不是可直接整体 cat 的文本文件 |
| `<pid>/clear_refs` | 可写控制接口，清除访问/soft-dirty 等标记，会改变观察状态 |
| `<pid>/coredump_filter` | core dump 包含哪些映射类别的位图，可写且含敏感性影响 |

这些属于专门诊断接口，不能拿 `cat` 结果当文本解析；逐位格式应读相应内核文档，不在普通巡检中盲目遍历。

## 10. 内核、驱动、硬件与 sysctl

### 10.1 常见系统识别文件

| 文件 | 主要内容 | 注意事项 |
| --- | --- | --- |
| `version` | 内核 release、构建者/编译器和构建信息 | 不是发行版完整版本 |
| `sys/kernel/osrelease` | 内核 release，例如 `6.1.158` | 可与 `uname -r` 对照 |
| `cmdline` | 内核启动参数 | 与 `<pid>/cmdline` 完全不同 |
| `modules` | 当前加载模块、大小、引用、依赖、状态、地址等 | 不包含全部内建驱动；地址可受安全配置限制 |
| `devices` | 已注册字符/块设备 major 与名称 | 不是 `/dev` 中每个节点的清单 |
| `misc` | misc 设备的 minor 与名称 | 对应共享 misc 主设备号体系 |
| `iomem` | 物理地址资源范围和名称，可有嵌套 | 不是 CPU 虚拟地址映射；地址可能因安全设置被隐藏 |
| `ioports` | I/O port 资源范围 | 架构相关，ARM 平台不一定有同样的端口模型 |
| `consoles` | 注册的控制台及相关状态 | 与图形终端窗口不是一回事 |
| `tty/drivers` | TTY 驱动注册信息 | 不是所有终端当前流量 |
| `driver/rtc` | RTC 时间、闹钟等信息 | 内容取决于 RTC 驱动 |
| `sys/kernel/random/boot_id` | 本次启动的标识 UUID | 可帮助巡检程序识别重启 |

`modules` 的 size 也不一定等于模块导致的所有运行时内存；引用数不直接等于打开设备数。

### 10.2 /proc/sys：只读观察与修改必须区分

路径与 sysctl key 对应，例如 `/proc/sys/vm/swappiness` 对应 `vm.swappiness`：

```bash
cat /proc/sys/vm/swappiness
sysctl vm.swappiness
```

| 分类 | 示例 | 含义 |
| --- | --- | --- |
| `kernel/` | `pid_max`、`threads-max`、`hostname`、`dmesg_restrict`、`kptr_restrict` | 任务范围、系统身份、安全控制等 |
| `vm/` | `swappiness`、`overcommit_memory`、`dirty_ratio`、`dirty_bytes`、`min_free_kbytes` | swap 倾向、承诺分配、写回和水位策略 |
| `fs/` | `file-max`、`file-nr`、`inotify/max_user_watches` | 文件表和文件系统相关限制 |
| `net/` | `ipv4/ip_forward`、`core/somaxconn`、TCP 参数 | 网络转发、监听和协议行为 |

`fs/file-nr` 常有三个值：已分配文件句柄数、已分配但未使用数量、上限。现代内核第二项通常为 0。它们不是某一个进程的 FD 数，文件对象和描述符也不是一对一关系。

本文不提供“一键优化参数”。`dirty_ratio` 与 `dirty_bytes` 等有相互作用，修改应依据负载和内核说明；写 `/proc/sys` 的设置一般不是自动持久化的，持久化配置由发行版 sysctl 加载机制管理。

### 10.3 不宜直接读取或写入的特殊文件

| 路径 | 边界 |
| --- | --- |
| `kmsg` | 内核日志接口；可能阻塞并影响日志读取者，不应与日志服务争用；通常用 `dmesg` 或 journal |
| `kcore` | 内核虚拟内存的 ELF core 视图，大小可非常大，不是 RAM 的普通文本汇总 |
| `vmcore` | crash kernel 场景下的崩溃转储视图，不是普通运行状态下必定存在的文件 |
| `kallsyms` | 内核符号，地址和可见性受权限与安全设置约束 |
| `sysrq-trigger` | 写入可能触发紧急操作，包括终止任务或重启，禁止作为普通排查动作 |
| `sys/vm/drop_caches` | 写入会改变缓存状态、影响性能，不是日常“释放内存”的必要操作 |

## 11. 容器、权限与采样的统一注意事项

### 11.1 /proc 并非全部都是“容器自己的”

- PID 目录可见性由 procfs 关联的 PID 命名空间等决定。
- 挂载信息是目标进程挂载视图，网络目录是目标网络命名空间视图。
- 原生全局 `meminfo`、`stat`、`loadavg`、PSI 等通常仍是系统级观察，不能假设自动变成容器配额。
- 容器运行时或 LXCFS 等可替换/虚拟化部分文件，应检查实际挂载方式。
- 进程 CPU 亲和性、cpuset 和 CPU quota 是不同限制，不能只看逻辑 CPU 数。
- 时间命名空间可影响 uptime、btime、starttime 等，应保证被比较的值处于兼容视图。

在容器里看到 `MemAvailable` 很多，仍可能因 `memory.max` 触发 cgroup OOM。此时看 `memory.current`、`memory.events` 和相应日志，比仅看宿主式 meminfo 更直接。

### 11.2 权限问题不是“文件内容为零”

进程文件访问可能涉及 UID/GID、ptrace 访问模式、capability、LSM、Yama、hidepid 挂载选项和 namespace。`subset=pid` 等挂载配置还可能隐藏全局条目。

应区分：文件不存在、没有权限、内核未配置、字段被隐藏/置零、对象已经退出。不能把“读取失败”统计成“资源使用为零”。

### 11.3 监控程序的基本规则

1. 对文本字段按名称或稳定 ABI 解析，允许增加字段和缺失可选字段。
2. 单位在采集时明确记录，避免页数、KiB、字节混用。
3. 对累计计数器使用差分，对 gauge 保留瞬时值，对平均值保留时间尺度。
4. 记录 boot ID，检测 PID/starttime 和设备身份变化，避免跨生命周期差分。
5. 用单调时钟记录真实采样间隔，处理计数回绕和异常负增量。
6. 允许读不到已退出进程、已删除 FD 或已关闭 socket。
7. 控制昂贵接口的频率，尤其是全进程 smaps、页表和大型网络连接表。
8. 采集报告默认脱敏，不随意公开环境变量、参数、地址和内存内容。

## 12. 按问题选择文件

| 现象 | 优先观察 | 判断方向 |
| --- | --- | --- |
| CPU 使用高 | `stat`，目标 `stat`，`task/*/stat` | 用户态、内核态、IRQ/softirq，还是虚拟机 steal |
| 单核很忙、整体不高 | `stat` 的 cpuN、`interrupts`、`softirqs`、线程亲和性 | 热点线程、IRQ 集中或限制范围 |
| 负载高但 CPU 不高 | `loadavg`、线程 State、`wchan`、`stack`、`pressure/io` | runnable 堆积还是不可中断等待 |
| 内存“快满了” | `meminfo`、`vmstat`、`pressure/memory` | 低 MemFree 是缓存，还是回收停顿 |
| 用户进程内存持续增长 | `status`、`smaps_rollup`、`smaps` | RSS/PSS、匿名页、共享页和映射类型 |
| 内核内存持续增长 | `meminfo`、`slabinfo`、`vmallocinfo` | 哪类 slab/vmalloc 分配在增长 |
| 大块 DMA/连续分配失败 | `buddyinfo`、`pagetypeinfo`、`zoneinfo`、CMA 字段 | 碎片、水位、zone/CMA/GFP 约束 |
| I/O 延迟高 | `diskstats`、`pressure/io`、进程 `io` 与 stack | 设备耗时、应用路径、回收/写回压力 |
| 网络丢包或连接不稳 | `net/dev`、`snmp`、`netstat`、`softirqs` | 驱动丢弃、协议错误、重传或监听压力 |
| 连接大量 CLOSE_WAIT | `net/tcp`、进程 `fd/`，结合 `ss` | 应用是否未及时关闭 socket |
| 磁盘空间删除后未释放 | 进程 `fd/` 链接、挂载信息 | 已删除但仍持有的文件 |
| FD 达到上限 | `fd/`、`limits`、`sys/fs/file-nr` | 单进程限制还是全局文件表 |
| 容器 OOM、宿主内存看似充足 | `<pid>/cgroup`、cgroupfs 的内存字段 | 容器限制及 OOM 事件 |
| 设备无响应 | `interrupts`、`devices`、`iomem`，配合日志/sysfs | IRQ 活动、资源注册和驱动状态 |

### 12.1 只读初步巡检

以下命令逐项执行，输出并不是同一时刻的原子快照；在 Linux shell 中使用：

```bash
cat /proc/version
cat /proc/uptime
cat /proc/loadavg
head -n 12 /proc/stat
cat /proc/meminfo
cat /proc/pressure/cpu
cat /proc/pressure/memory
cat /proc/pressure/io
cat /proc/diskstats
cat /proc/net/dev
```

针对已知进程：

```bash
pid=1234
cat "/proc/$pid/status"
cat "/proc/$pid/stat"
cat "/proc/$pid/limits"
cat "/proc/$pid/io"
cat "/proc/$pid/cgroup"
ls -l "/proc/$pid/fd"
cat "/proc/$pid/smaps_rollup"
```

权限不足、进程退出或内核未提供某接口时，应记录原因。以上命令不修改内核参数，但 smaps_rollup 仍有读取开销。

## 13. 字段有疑问时如何核对源码

下面的路径均相对于 **Linux 6.1.158 内核源码根目录**，不是本站 Markdown 的相对链接。先读内核说明，再沿输出函数检查单位转换与作用域，比仅凭字段名猜测可靠。

| 主题 | 内核文档/源码 |
| --- | --- |
| procfs 综合说明、内存、进程、mountinfo、io、fdinfo | `Documentation/filesystems/proc.rst` |
| CPU 累计字段、USER_HZ 转换 | `fs/proc/stat.c`、`kernel/sched/cputime.c` |
| cpuinfo 架构差异 | `fs/proc/cpuinfo.c`、`arch/x86/kernel/cpu/proc.c`、`arch/arm64/kernel/cpuinfo.c` |
| loadavg、uptime | `fs/proc/loadavg.c`、`fs/proc/uptime.c`、`kernel/sched/loadavg.c` |
| meminfo、可用内存估计 | `fs/proc/meminfo.c`、`mm/page_alloc.c` |
| vmstat、buddyinfo、pagetypeinfo、zoneinfo | `mm/vmstat.c` |
| diskstats 字段及计时规则 | `Documentation/admin-guide/iostats.rst`、`block/genhd.c`、`block/blk-core.c` |
| PSI 格式及 CPU full 边界 | `Documentation/accounting/psi.rst`、`kernel/sched/psi.c` |
| 进程 stat/status/statm | `fs/proc/array.c` |
| 进程目录、cmdline、io 等 | `fs/proc/base.c` |
| maps/smaps/smaps_rollup/numa_maps | `fs/proc/task_mmu.c` |
| pagemap、kpageflags | `Documentation/admin-guide/mm/pagemap.rst` |
| 网络接口统计 | `Documentation/networking/statistics.rst`、`net/core/net-procfs.c` |
| TCP 表及格式 | `Documentation/networking/proc_net_tcp.rst`、`net/ipv4/tcp_ipv4.c` |
| sockstat、协议统计作用域 | `net/ipv4/proc.c` |
| IRQ 亲和性 | `Documentation/core-api/irq/irq-affinity.rst`、`kernel/irq/proc.c` |
| 内存/网络等 sysctl 参数 | `Documentation/admin-guide/sysctl/` |
| cgroup v2 资源统计 | `Documentation/admin-guide/cgroup-v2.rst` |

这篇文档与 [用户空间接口总览](00-overview.md)、[procfs 实现与 API](misc-proc.md)、[sysfs](sysfs.md) 和 [sysctl](sysctl.md) 配合使用：先从观察现象确定接口，再检查字段含义，最后需要时追溯实现。
