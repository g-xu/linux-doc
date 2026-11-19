# Linux 性能监控、分析与调试工具指南

> 核心工具：ftrace、perf、eBPF、GDB、coredump；补充基础监控、strace、内存检查与内核崩溃分析。
> 面向读者：Linux 应用开发、系统开发、SRE、运维及性能工程人员。
> 编写日期：2026-11-19。适用范围：以较新的 Linux 5.x/6.x、常见 x86-64/ARM64 环境为背景，不假设所有发行版具备相同功能。
> 本文是技术介绍和操作参考，不是对某台主机的实测报告。命令需在获授权的目标环境中按实际版本验证；本文编写过程中未对任何业务进程执行追踪、调试或配置变更。

## 目录

1. [建立整体认识：监控、剖析、追踪、调试](#s1)
2. [性能指标与证据链](#s2)
3. [工具选型及相互关系](#s3)
4. [环境准备与基础监控工具](#s4)
5. [ftrace：内核执行路径与时序追踪](#s5)
6. [perf：计数器、CPU 热点与事件分析](#s6)
7. [eBPF：可编程观测与内核侧聚合](#s7)
8. [GDB：运行状态与代码级调试](#s8)
9. [coredump：崩溃现场保留与离线分析](#s9)
10. [其他常用工具与边界](#s10)
11. [多工具联合排障实战](#s11)
12. [生产环境采集规范与常见误区](#s12)
13. [命令速查、术语与参考资料](#s13)

<a id="s1"></a>
## 1. 建立整体认识：监控、剖析、追踪、调试

### 1.1 四类问题，四类方法

| 方法 | 回答的问题 | 典型输出 | 常用工具 |
|---|---|---|---|
| Monitoring，监控 | 系统现在健康吗？哪个资源出现异常？ | CPU、内存、I/O、网络和业务时序指标 | top、vmstat、iostat、sar、Prometheus |
| Profiling，性能剖析 | 成本主要花在哪里？ | 函数热点、调用栈、事件计数、火焰图 | perf、eBPF profiler |
| Tracing，执行追踪 | 谁在什么时候做了什么？为什么出现等待？ | 带时间戳的事件、参数、调用路径、延迟分布 | ftrace、trace-cmd、eBPF、strace |
| Debugging，调试 | 当时变量、寄存器、线程和对象是什么状态？ | 断点现场、变量、线程栈、内存内容 | GDB、coredump + GDB |

**不要只根据“性能工具”的标签选型，要根据想回答的问题选型。** CPU 热点适合采样；线程为何迟迟未运行适合调度追踪；某指针为什么非法适合调试；历史崩溃适合 core 离线分析。

### 1.2 Linux 观测层次

```text
业务层：请求耗时、吞吐量、错误率、队列、业务阶段
    │  应用埋点 / OpenTelemetry / USDT / 用户态函数探针
用户态：函数、线程、锁、堆、系统调用
    │  perf 用户栈 / uprobes / GDB / strace / core
内核态：调度、内存管理、VFS、块设备、TCP、IRQ
    │  tracepoints / ftrace / kprobes / eBPF
硬件层：CPU 周期、指令、缓存、分支、内存访问
       PMU / perf；具体事件受 CPU 型号限制
```

Linux 内核提供观测基础设施，工具把这些能力组合起来。一个应用请求经过用户函数、系统调用、内核调度和设备处理，通常需要跨层建立证据链。

### 1.3 三组必须分清的概念

**计数 vs. 采样 vs. 逐事件追踪**

- **计数**：累计某事件发生次数，例如 10 秒内发生多少次上下文切换。适合比较总量，不能直接还原每次事件。
- **采样**：每隔一定周期或每累计一定数量的事件记录一个现场。适合找热点，但不是完整执行历史。
- **追踪**：在选定的事件触发时记录信息。适合时序和参数分析，但高频事件可能产生很大开销和数据量，也可能丢事件。

**on-CPU vs. off-CPU**

- **on-CPU**：线程正在 CPU 上运行，可能执行有效计算，也可能自旋、内存等待或做内核工作。
- **off-CPU**：线程没有运行。既包括睡眠等待，也包括已可运行但尚未获得 CPU 的时间。
- 普通 CPU 采样主要观察 on-CPU；它不会自动解释一次长时间睡眠。分析 off-CPU 要结合调度事件、等待栈或特定资源延迟。

**现场快照 vs. 时间历史**

- GDB 的一次停止现场、core 的崩溃现场，是状态快照。
- ftrace/eBPF 事件流，是观察窗口内的时间历史。
- 快照看到“正在等锁”，不等于证明“过去一直等锁”；一次崩溃栈看到 `free()`，也不等于证明错误最早发生在 `free()`。

### 1.4 推荐的学习和使用顺序

1. 学会 `top / pidstat / vmstat / iostat / ss`，先发现异常资源。
2. 学会 `perf stat / record / report`，找 CPU 成本和调用栈。
3. 学会 ftrace 事件追踪，理解内核时序、线程唤醒和切换。
4. 学会 bpftrace/BCC，根据假设自定义统计和延迟直方图。
5. 学会 GDB 和 core，在状态层面定位代码错误。
6. 在测试环境使用 sanitizers、Valgrind 等定位内存和并发问题。

---

<a id="s2"></a>
## 2. 性能指标与证据链

### 2.1 先定义“慢”的业务含义

开始采集之前至少记录：

- 是吞吐量下降、平均延迟上升、P99/P99.9 尾延迟上升，还是启动时间/定时任务超时？
- 正常基线、异常时间范围、输入规模、并发度、请求类型是什么？
- CPU 型号、核数、内核、应用版本、容器配额、NUMA/亲和性配置是否变化？
- 是所有实例都慢，还是仅某主机、某线程、某类请求慢？

没有业务基线，仅看到“CPU 80%”无法断定问题。优化后的验证也必须回到原始业务指标。

### 2.2 USE 与 RED

- **USE**：对每类资源检查 Utilization（利用率）、Saturation（饱和/排队）、Errors（错误）。
- **RED**：对服务检查 Rate（请求速率）、Errors（错误）、Duration（耗时）。
- 两者互补：RED 发现用户体验变化，USE 找资源层线索，再使用 profiling/tracing/debugging 解释机制。

| 资源/指标 | 初步观察 | 常见陷阱 | 深入工具 |
|---|---|---|---|
| CPU 利用率 | 用户态、内核态、单核/单线程占用 | 总 CPU 不高也可能单线程满核；高占用可能自旋 | perf、调度追踪 |
| 运行队列/调度等待 | vmstat 的 `r`、调度延迟 | 需结合可用 CPU、cpuset、quota，而非只看主机总核数 | perf sched、ftrace、runqlat |
| 内存 | RSS/PSS、可用内存、回收、swap、fault | RSS 不等于泄漏；minor fault 不等于磁盘 I/O | smaps_rollup、eBPF、sanitizers |
| 磁盘 | await、队列、吞吐、请求错误 | NVMe/RAID 的 `%util=100%` 不代表达到性能极限 | iostat、biolatency、block 事件 |
| 网络 | RTT、重传、连接状态、丢包 | 重传可来自拥塞/丢包/接收端问题，不能只归因网卡 | ss、sar、eBPF、tcpdump |
| 延迟 | 均值、分位数、直方图、最大值 | 均值掩盖长尾；聚合不同请求可失真 | 应用埋点、eBPF、ftrace |

### 2.3 load average、iowait、PSI 的正确解释

- **load average**：Linux 上反映可运行任务及不可中断睡眠任务的平均数量，不是 CPU 百分比。高 load 可能来自 CPU 排队，也可能来自 D 状态等待。
- **iowait**：CPU 记账中与等待 I/O 有关的空闲时间比例，不是设备延迟，也不是某个线程的 I/O 等待占比。在多核和并行 I/O 情况下解释尤其要谨慎。
- **PSI（Pressure Stall Information）**：观察 CPU、内存、I/O 资源压力造成的停顿。`some` 表示至少有任务因该资源而停顿，`full` 表示相应统计范围内所有非空闲任务同时停顿；文件、字段和 cgroup 支持依赖内核。`total` 通常以微秒累计，avg10/60/300 是窗口平均比例。

```bash
cat /proc/pressure/cpu
cat /proc/pressure/memory
cat /proc/pressure/io
```

PSI 有利于发现资源争用，但仍需进一步确定具体对象和原因。不要把某个 PSI 阈值当成所有系统通用的判定标准。

### 2.4 时间分解与关联

一个线程或一次同步请求的墙钟时间可概念性分解为：

```text
墙钟耗时 ≈ on-CPU 时间 + 可运行但未运行时间 + 阻塞/睡眠时间
```

这是分析模型，不是可直接相加的多线程服务公式：并行子任务、异步 I/O、流水线和不同请求之间会重叠。

建议用以下维度关联证据：`timestamp + host + PID/TID + cgroup + request/trace ID`。跨主机用时钟同步和业务 trace ID；内核事件通常使用单调时钟，不能未经换算就与日志的 UTC 时间直接比较。

---

<a id="s3"></a>
## 3. 工具选型及相互关系

### 3.1 核心工具对比

| 工具 | 核心机制 | 强项 | 不适合/局限 | 运行影响 |
|---|---|---|---|---|
| ftrace | 内核函数插桩、tracepoint、per-CPU buffer | 内核调用路径、调度、IRQ、时序 | 不以用户变量检查为目标；全函数追踪代价大 | 通常不停进程，但会改变执行时序 |
| perf | `perf_event_open()`，PMU/软件/tracepoint 事件 | 硬件计数、CPU 热点、调用栈、调度分析 | 普通 CPU profile 不解释全部 off-CPU；硬件能力有差异 | 计数常较轻；高频采样/取栈成本增加 |
| eBPF | 受 verifier 约束的内核程序，探针、map、事件通道 | 动态定制、延迟分布、跨事件关联、内核聚合 | 内核/权限/符号/版本限制；程序逻辑需验证 | 非零开销，高频打印和大 map 有风险 |
| GDB | Linux 本地调试常用 ptrace，断点与硬件调试寄存器 | 线程、变量、内存、寄存器、单步、观察点 | 不适合常态在线监控；优化代码有信息缺失 | attach 默认会停进程，命中断点也会停止 |
| coredump | 内核/收集器保留终止现场，通常为 ELF core | 偶发崩溃、离线分析、保留现场 | 不是性能历史；缺失页/符号无法凭空恢复 | 崩溃已终止；生成大 core 有 I/O 和空间成本 |

“开销较低”只表示某种配置下的相对特征，不能保证生产业务零影响。

### 3.2 它们不是相互替代的孤立工具

```text
同一个 sched_switch tracepoint
    ├── ftrace：写入 trace buffer，观察事件时间线
    ├── perf：记录为 perf.data，由 perf sched 等分析
    └── eBPF：触发程序，在内核中计算等待时间、更新直方图

内核函数入口
    ├── dynamic ftrace：编译期插桩点动态启用
    ├── kprobe：对支持的指令/函数位置动态探测
    └── eBPF fentry/fexit：在支持的 BTF/trampoline 路径挂载程序

崩溃进程
    └── core：保留现场 → GDB：解释现场
```

ftrace 是内核 tracing 基础设施及接口集合；perf 是性能事件工具集；eBPF 是可编程机制。三者可共享部分事件来源，不应简单解释成“新工具全面淘汰旧工具”。

### 3.3 从症状快速选工具

| 症状 | 首选 | 进一步确认 |
|---|---|---|
| CPU 高，不知道消耗在哪里 | pidstat → perf record/report | perf annotate、应用级基准 |
| CPU 不高，响应慢 | 应用阶段耗时、pidstat、PSI | perf sched、off-CPU tracing、资源延迟 |
| 内核某路径耗时异常 | ftrace function_graph | tracepoint/eBPF 分段统计 |
| 偶发调度长尾 | ftrace sched 事件、runqlat | IRQ、抢占、quota、CPU 亲和性 |
| 某次系统调用阻塞 | strace 或 eBPF syscall 延迟 | 文件/套接字/锁及下层事件 |
| 内存持续上涨 | smaps_rollup、应用分配统计 | memleak、LSan、Valgrind |
| 段错误或 abort | coredump + GDB | ASan/UBSan、观察点、复现测试 |
| 无响应、疑似死锁 | 多次线程栈快照 | 锁所有者、等待关系、TSan/代码分析 |
| 内核 panic/oops | 内核日志，必要时 kdump + crash | 内核符号、vmcore、ftrace 既有记录 |

---

<a id="s4"></a>
## 4. 环境准备与基础监控工具

### 4.1 命令约定与权限边界

本文示例中的 `/opt/demo`、`/var/tmp/linux-diag`、PID `12345` 是**示例值，不表示这些路径/进程真实存在**。使用前替换并确认对象；所有路径使用绝对路径。示例是独立片段，不是必须从头执行的自动化脚本。

```bash
# 替换为已确认的目标进程
PID=12345
ps -p "$PID" -o pid,ppid,user,comm,args

# 仅在获授权的目标主机上，为本次采集建立受限目录
umask 077
OUT="/var/tmp/linux-diag/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$OUT"
```

涉及 `sudo` 的命令需要权限；写 tracefs 的示例须在批准的 root shell 中执行。`sudo echo 1 > 文件` 通常不解决重定向权限，因为 `>` 由当前 shell 执行；可以使用 root shell 或 `printf ... | sudo tee 文件`。不要为了跑工具默认全局关闭安全限制。

### 4.2 先检查实际能力，再决定安装

```bash
uname -r
uname -m
cat /etc/os-release
lscpu
command -v perf trace-cmd bpftrace bpftool gdb coredumpctl
perf --version
bpftrace --version
gdb --version
systemd --version
```

常见软件包名仅作为线索，**包名与仓库可用性依发行版而异**：

| 工具 | Debian/Ubuntu 常见线索 | RHEL/Fedora 系常见线索 |
|---|---|---|
| top/ps、vmstat、sar/pidstat/iostat | procps、sysstat | procps-ng、sysstat |
| perf | linux-perf 或匹配内核的 linux-tools 包 | perf |
| ftrace 前端 | trace-cmd、kernelshark | trace-cmd、kernelshark |
| eBPF | bpftrace、bpfcc-tools、bpftool | bpftrace、bcc-tools、bpftool |
| 调试 | gdb、valgrind、systemd-coredump | gdb、valgrind、systemd 相关包 |

perf 优先选择发行版为运行内核提供的配套版本；并非要求所有版本号逐字相同，但新内核特性可能无法由旧工具解析。kernel debug symbols 和应用 debuginfo 通常需要单独安装/归档。

### 4.3 基础监控：先缩小范围

```bash
# 主机概况；交互工具按 q 退出
uptime
top
free -h
vmstat 1 10
mpstat -P ALL 1 10

# 进程 CPU、内存、I/O、线程及上下文切换
pidstat -u -r -d -w -p "$PID" 1 10
pidstat -t -u -p "$PID" 1 10
ps -L -p "$PID" -o pid,tid,psr,stat,pcpu,wchan:32,comm

# 磁盘与网络
iostat -xz 1 10
sar -n DEV,TCP,ETCP 1 10
ss -s
ss -tinp

# 进程地址空间与资源限制，访问可能受权限限制
cat "/proc/$PID/status"
cat "/proc/$PID/smaps_rollup"
cat "/proc/$PID/limits"
cat "/proc/$PID/cgroup"
```

解读要点：

- `top -H -p "$PID"` 可观察线程。部分工具把一个逻辑 CPU 的满载记为 100%，多线程进程可能超过 100%。
- `pidstat -w` 的自愿切换常与等待有关，非自愿切换常与调度抢占有关；它们都是线索，不直接等价于锁竞争或 CPU 不足。
- `vmstat` 的 `r` 是可运行任务数量，`b` 是阻塞任务；第一行常是自开机以来的平均统计，后续才是间隔数据。
- `iostat` 也常先输出开机以来统计；支持的版本可用 `-y` 跳过。`await` 含排队与处理时间，不是纯设备服务时间。
- `smaps_rollup` 的 PSS 把共享页按共享进程分摊，通常比直接相加 RSS 更适合估计物理内存归属。
- `ss -p` 的进程信息和某些 `/proc` 文件需要额外权限；不要把权限造成的缺失误当成没有连接/没有映射。

### 4.4 容器和 cgroup

```bash
# 先从 /proc/PID/cgroup 确定真实路径；以下只是 cgroup v2 示例
CG="/sys/fs/cgroup/example-service"
cat "$CG/cpu.max"
cat "$CG/cpu.stat"
cat "$CG/cpuset.cpus.effective"
cat "$CG/memory.current"
cat "$CG/memory.events"
```

- `cpu.max` 的 quota/period 定义 CPU 带宽限制；`cpu.stat` 中限流次数和时间的增量可揭示 throttling。
- 主机有很多空闲 CPU，不代表容器能使用它们。需要同时检查 quota 与 cpuset。
- cgroup v1 的路径与字段不同，不要把 v2 命令原样套用。
- 容器内 PID 与宿主机 PID 不一定相同；宿主机工具通常应使用宿主机可见的 PID/TID。
- 容器能看见 `/sys/kernel/tracing` 并不意味着有权限，更不意味着应把容器设置成 privileged。优先使用平台批准的宿主机采集机制。


---

<a id="s5"></a>
## 5. ftrace：内核执行路径与时序追踪

### 5.1 是什么，能回答什么

ftrace 最初以函数追踪为中心，现已包含事件追踪、函数图、延迟 tracer 等内核观测能力。典型问题包括：

- 一次读写进入了哪些内核函数？哪一段明显变慢？
- 某线程何时被唤醒，何时真正运行，中间等了多久？
- 某次 IRQ 或 softirq 的处理耗时是否异常？
- 关中断/禁止抢占的区间是否造成实时任务长尾？

ftrace 不需要修改被观测的应用源码，但依赖内核已编译相应配置。它不直接提供 GDB 式的任意用户变量检查。

### 5.2 原理机制

**（1）函数入口插桩与 dynamic ftrace**

支持函数追踪的内核在编译时为可追踪函数生成入口插桩位置，例如架构/编译器相关的 `mcount`、`__fentry__` 或其他机制。dynamic ftrace 可以把这些位置在禁用状态下变为 NOP 或使用相应的动态控制方式，需要追踪时再启用调用路径。

因此：

- 不是每个函数天然都可追踪；内联、`notrace`、特殊入口和架构限制都可能影响可见性。
- 禁用时可降低不必要成本，但启用大量高频函数后仍会有显著开销。
- 与 kprobe 在运行时对指令位置设置探测的方式不同，ftrace 利用已有函数插桩点。

**（2）function 与 function_graph**

- `function`：记录函数入口及调用者信息，主要回答“调用了谁”。
- `function_graph`：同时跟踪入口与返回，重建嵌套调用关系，并计算函数区间时长，主要回答“调用路径和持续时间”。
- 函数区间可能包含子函数、抢占、IRQ 以及睡眠等时间，受配置影响，不应直接当成该函数的独占 CPU 时间。
- `options/sleep-time` 控制是否把线程被调度出去的时间计入 function_graph 区间；分析时必须记录实际选项，而不是假定默认值。

**（3）tracepoint 事件**

内核在调度、块 I/O、内存和网络等语义位置定义结构化事件，如 `sched:sched_switch`。事件带有字段格式，可做事件级过滤。tracepoint 通常比内部函数名更适合稳定观测，但字段也要以目标内核为准，并非所有事件都是永久不变的 ABI。

**（4）缓冲区**

事件通常写入 per-CPU ring buffer，降低跨 CPU 争用。用户态通过 tracefs 读取文本，或由 trace-cmd 记录二进制数据。高速写入可能覆盖旧数据或出现丢失；时钟和缓冲容量决定时序分析的可信度。

### 5.3 tracefs 接口

现代系统通常使用 `/sys/kernel/tracing`；旧系统可能经 `/sys/kernel/debug/tracing` 暴露。先通过挂载信息确认，不要假设两者总存在。

```bash
findmnt -t tracefs,debugfs
ls /sys/kernel/tracing
cat /sys/kernel/tracing/available_tracers
cat /sys/kernel/tracing/available_events
cat /sys/kernel/tracing/events/sched/sched_switch/format
```

若 tracefs 尚未挂载，管理员可按发行版规定挂载；这属于系统变更，不能未经确认自动执行。

| 接口 | 作用与注意事项 |
|---|---|
| `current_tracer` | 当前 tracer；`nop` 表示不启用函数/延迟 tracer，但事件追踪仍可独立使用 |
| `tracing_on` | 控制是否写 tracing buffer；设为 0 不等于所有探针/插桩已经卸载 |
| `trace` | 读取当前 buffer 的文本视图；通常不消费数据，停止后读取更可控 |
| `trace_pipe` | 持续读取并消费事件；可能阻塞，多读者会影响各自看到的数据 |
| `buffer_size_kb` | 通常是每 CPU 的容量，不能当成全系统总容量 |
| `trace_clock` | 时间戳时钟；跨 CPU 关联应选择目标支持的可比较时钟，如 mono/global |
| `set_ftrace_filter` | function tracer 的函数白名单；空白名单不表示不追踪，可能表示追踪全部可追踪函数 |
| `set_ftrace_notrace` | 排除函数 |
| `set_graph_function` | function_graph 的根函数筛选，适合限制调用树范围 |
| `set_ftrace_pid` | 按 Linux task ID/TID 筛选函数追踪；多线程不能只写进程主线程 PID 就认为全部覆盖 |
| `set_event_pid` | 按事件执行上下文过滤；不等同于按事件 payload 中的任意 PID 字段过滤 |
| `events/<组>/<事件>/filter` | 使用该事件的实际字段表达式过滤 |
| `instances/<名称>` | 独立 tracing instance，尽量避免污染根 tracing 配置 |
| `per_cpu/cpu*/stats` | 查看缓冲区条目、overrun 等统计；字段依版本而异 |

**instances 的隔离不是资源隔离。** 它能隔离许多配置和 buffer，但启用的插桩仍会增加全机 CPU 成本。部分 tracer/选项不支持 instance，务必检查 instance 内的可用功能；不要在不支持时自动改写根实例。

### 5.4 实战一：观测一个线程的调度事件

以下是一个完整的短窗口示例。**在获授权的 root shell 中执行**，把 `TID` 改成实际线程号；例中目录和时间窗口也按实际调整。它使用新 instance，不覆盖已有根配置。

```bash
set -euo pipefail
TID=12345
TR=/sys/kernel/tracing
OUT="/var/tmp/linux-diag/ftrace-$(date +%Y%m%d-%H%M%S)-$$"
I="$TR/instances/diag-$$"
umask 077
mkdir -p "$OUT"
mkdir "$I"

cleanup() {
    printf '0\n' > "$I/tracing_on" || true
    printf '0\n' > "$I/events/enable" || true
    printf 'nop\n' > "$I/current_tracer" || true
}
trap cleanup EXIT

printf '0\n' > "$I/tracing_on"
printf 'nop\n' > "$I/current_tracer"
# mono 必须实际可用；不支持时停止并另选目标支持的跨 CPU 时钟
printf 'mono\n' > "$I/trace_clock"
# 每 CPU 4 MiB，CPU 数多时要重新评估总占用
printf '4096\n' > "$I/buffer_size_kb"

cat "$I/events/sched/sched_switch/format" > "$OUT/sched_switch.format"
cat "$I/events/sched/sched_wakeup/format" > "$OUT/sched_wakeup.format"
printf 'prev_pid == %s || next_pid == %s\n' "$TID" "$TID" \
    > "$I/events/sched/sched_switch/filter"
printf 'pid == %s\n' "$TID" > "$I/events/sched/sched_wakeup/filter"
printf '1\n' > "$I/events/sched/sched_switch/enable"
printf '1\n' > "$I/events/sched/sched_wakeup/enable"

printf '1\n' > "$I/tracing_on"
sleep 10
printf '0\n' > "$I/tracing_on"
cat "$I/trace" > "$OUT/sched-trace.txt"
cat "$I/trace_clock" > "$OUT/trace-clock.txt"
for f in "$I"/per_cpu/cpu*/stats; do
    printf '\n%s\n' "$f"
    cat "$f"
done > "$OUT/buffer-stats.txt"
cleanup
trap - EXIT
# 只移除本次新建的、已停止的 instance，不删除任何已有实例
rmdir "$I"
printf '结果目录：%s\n' "$OUT"
```

`sched_wakeup` 通常由唤醒者执行。只按 `common_pid == TID` 或简单设置 `set_event_pid=TID`，可能漏掉“别人唤醒目标线程”的关键事件。应按 payload 的 `pid` 筛选；`sched_switch` 则关注 `prev_pid`/`next_pid`。

解读事件：

1. 目标线程出现 `sched_wakeup`，表示它被唤醒进入可运行状态。
2. 后续 `sched_switch` 的 `next_pid` 为目标线程，表示它实际获得 CPU。
3. 这两个时间点的差值，可作为该次唤醒的调度等待线索。
4. 完整 run-queue 分析还要处理线程被抢占后仍可运行、迁核、窗口边界、重复唤醒及丢事件。不要直接把任意最近一次 wakeup 与 switch 做简单配对。

需要多线程时先枚举 `/proc/PID/task/` 下的 TID，构造字段条件；线程新建/退出和 PID 复用也要处理。上述示例只分析一个已知 TID。

### 5.5 实战二：追踪内核函数调用树

在另一个新 instance 中，只追踪某个线程从 `vfs_read` 进入的调用树。`vfs_read` 仅为示例，必须出现在目标的可追踪函数列表中。

```bash
# 需要批准的 root shell；此片段独立设置目标线程和输出目录
set -euo pipefail
TID=12345
TR=/sys/kernel/tracing
OUT="/var/tmp/linux-diag/graph-$(date +%Y%m%d-%H%M%S)-$$"
I="$TR/instances/graph-$$"
umask 077
mkdir -p "$OUT"
mkdir "$I"
cleanup_graph() {
    printf '0\n' > "$I/tracing_on" || true
    if test -e "$I/current_tracer"; then
        printf 'nop\n' > "$I/current_tracer" || true
    fi
    # 只清理本次创建的 instance；失败时保留并报告，勿碰已有实例
    rmdir "$I" || printf '需核对本次实例是否已清理：%s\n' "$I" >&2
}
trap cleanup_graph EXIT

# 旧内核可能不支持 instance 中的函数图；缺少能力就停止，不切到根实例
for f in current_tracer available_tracers set_ftrace_pid set_graph_function; do
    test -e "$I/$f" || { printf '缺少接口：%s\n' "$I/$f" >&2; exit 1; }
done
grep -qw function_graph "$I/available_tracers"
grep -w vfs_read "$TR/available_filter_functions"
printf '0\n' > "$I/tracing_on"
printf 'mono\n' > "$I/trace_clock"
printf '%s\n' "$TID" > "$I/set_ftrace_pid"
printf 'vfs_read\n' > "$I/set_graph_function"
printf 'function_graph\n' > "$I/current_tracer"
# 明确计入睡眠；不支持该选项时停止；设 0 也不是精确独占 CPU 时间
printf '1\n' > "$I/options/sleep-time"
printf '1\n' > "$I/tracing_on"
sleep 5
printf '0\n' > "$I/tracing_on"
cat "$I/trace" > "$OUT/function-graph.txt"
cat "$I/trace_options" > "$OUT/function-graph.options"
for f in "$I"/per_cpu/cpu*/stats; do
    printf '\n%s\n' "$f"
    cat "$f"
done > "$OUT/buffer-stats.txt"
# 正常退出和错误退出均由 trap 停止并清理本次实例
```

示例输出形态，不是实测：

```text
 vfs_read() {
   ... 子函数 ...
 } /* vfs_read，区间时长 */
```

这有助于发现长区间和异常路径，但不能只根据最外层时长就认定某个函数“自身执行很慢”。最好与调度/IRQ 事件一起判断是否被抢占或阻塞。

### 5.6 trace-cmd 与 KernelShark

直接写 tracefs 适合理解机制；日常工作中 trace-cmd 可简化记录，KernelShark 可可视化 CPU/任务时间线。

```bash
trace-cmd list -e
trace-cmd list -p
# 采集全系统调度事件，窗口内只运行 sleep 作为计时器
sudo trace-cmd record -o "$OUT/trace.dat" \
    -e sched:sched_switch -e sched:sched_wakeup -- sleep 10
trace-cmd report -i "$OUT/trace.dat" > "$OUT/trace-report.txt"
kernelshark "$OUT/trace.dat"
```

上述全系统采集是为了保留竞争者信息，不是仅采集 sleep。注意数据量、权限和已有 tracer 冲突；trace-cmd 是否支持指定 instance、PID 过滤及对应参数，以本机帮助为准。

### 5.7 常用场景、局限和风险

- **调度长尾**：sched 事件 + IRQ/softirq 事件，看目标可运行后为何迟迟未执行。
- **文件系统/驱动路径**：function_graph 限定根函数，找异常长区间。
- **实时抖动**：支持时使用 `irqsoff`、`preemptoff`、`preemptirqsoff`、wakeup 类 tracer；PREEMPT_RT 与普通内核行为不同。
- **短时故障现场**：snapshot/trigger 可保留故障前事件，适合低概率问题；配置语法和支持范围须核对目标文档。
- **hwlat** 等检测可能主动让 CPU 忙等并关闭中断，具有明显侵入性，不宜作为默认生产观测方式。
- 不要全系统开启“所有函数 + 全部事件 + 用户栈”后长时间无人管理。
- `tracing_on=0` 仅停止记录；结束时应关闭事件、恢复 tracer，并清理本次实例。若动过根配置，应恢复原始状态而非盲目写 `nop`。

---

<a id="s6"></a>
## 6. perf：计数器、CPU 热点与事件分析

### 6.1 原理机制

perf 用户态工具使用 Linux `perf_event_open()` 性能事件接口，事件来源主要包括：

| 来源 | 示例 | 回答的问题 |
|---|---|---|
| 硬件 PMU | cycles、instructions、branches、cache-misses | CPU 执行效率、微架构事件 |
| 软件事件 | task-clock、context-switches、page-faults | CPU 时间、调度和缺页总量 |
| tracepoint | sched:sched_switch、syscalls 事件 | 内核语义事件和时序 |
| 动态探针等 | perf probe 建立的 probe 事件 | 指定函数/位置的事件观测 |

**计数模式**累计事件数量，主要由 `perf stat` 使用。**采样模式**在计数溢出或软件时钟等条件下记录 IP、时间戳、线程等信息，按选项记录调用栈。采样数据通过 mmap ring buffer 交给用户态，保存为 `perf.data`。

对 PMU 事件的采样，不一定精确落在触发事件的原始指令上，可能有 **skid**。支持时可使用 precise 事件、PEBS/IBS 等能力，但事件和架构限制必须核对。

### 6.2 常用子命令

| 子命令 | 用途 |
|---|---|
| `perf list` | 查询目标支持的事件和指标 |
| `perf stat` | 计数、重复基准、区间统计 |
| `perf top` | 实时 CPU/事件热点 |
| `perf record` | 录制采样或事件数据 |
| `perf report` | 按函数、线程、DSO、调用栈汇总 |
| `perf annotate` | 将样本关联到源代码/汇编 |
| `perf script` | 导出逐样本或逐事件文本供后处理 |
| `perf sched` | 调度记录和延迟分析 |
| `perf trace` | 系统调用等事件追踪，具体能力依版本 |
| `perf probe` | 动态添加/管理探针事件，常需符号及调试信息 |
| `perf lock / mem / c2c` | 锁、内存访问、cache-to-cache 专项分析；平台支持各不相同 |

### 6.3 实战一：perf stat 检查整体成本

```bash
perf list
# 先用较通用的软件事件确认能力
perf stat -p "$PID" \
    -e task-clock,context-switches,cpu-migrations,page-faults -- sleep 10

# PMU 可用时，cycles/instructions 放在同一组，尽量同步计数
perf stat -p "$PID" -e '{cycles,instructions}' \
    -e branches,branch-misses,cache-references,cache-misses -- sleep 10

# 运行一个可重复的测试程序；不会附着到已有业务
perf stat -r 5 -e '{cycles,instructions}' -- /opt/demo/benchmark

# 区间趋势；毫秒单位，1 秒打印一次
perf stat -I 1000 -p "$PID" -e task-clock,context-switches -- sleep 10
```

事件不支持会出现 `<not supported>`、无法打开事件等错误；权限不足会出现 Permission denied。先去掉不支持事件、核对权限和 PMU，不要把它们当成计数为零。

关键指标解释：

- **IPC = instructions / cycles**：每周期完成的指令数。低 IPC 可能是数据依赖、内存访问、前端供给等原因，不能单凭它判断“代码差”；不同架构、工作负载和频率不能机械横比。
- **branch miss ratio = branch-misses / branches**：分支预测失败比例，但仍需定位到具体代码和确认事件口径。
- **cache miss ratio**：只能在分子/分母事件语义匹配时解释。通用 cache 事件通常不是所有层级缓存的统一定义，跨 CPU 型号比较要特别谨慎。
- **task-clock / wall time**：可估计被统计线程集合消耗了多少个 CPU 的平均运行能力；不等同于除以主机核数后的百分比。

**PMU multiplexing（复用）**：硬件计数器数量有限，事件太多时内核轮流调度事件。perf 常按 `time_enabled / time_running` 缩放计数，输出运行时间百分比。

- 百分比明显低于 100% 时，总数可能是估算；短暂且非平稳负载尤其易偏差。
- 用少量事件、同组事件、可重复的多轮测试改善可信度。
- 分组太大又可能因为计数器排不开而无法运行。不要为了消除警告默认关闭 NMI watchdog 或其他系统保护。

### 6.4 实战二：采集进程 CPU 热点

```bash
# 使用软件时钟作为较通用的 on-CPU 采样源；需要允许观察目标进程
perf record -o "$OUT/perf.data" -F 99 -e cpu-clock \
    -p "$PID" --call-graph dwarf,8192 -- sleep 20
perf report -i "$OUT/perf.data" --stdio > "$OUT/perf-report.txt"
perf report -i "$OUT/perf.data"
perf script -i "$OUT/perf.data" > "$OUT/perf-script.txt"

# 实时热点；观察完按 q 退出
perf top -p "$PID" -e cpu-clock
```

这里 `-p PID` 指定目标，`sleep 20` 只是限制观察窗口。未指定目标而只写 `perf record ... sleep 20`，通常会观察 sleep 本身，不会自动观察你想看的业务。

事件选择影响百分比含义：

- `cpu-clock`：按运行时钟采样，主要反映 CPU 时间分布。
- `cycles`：按 CPU 周期采样，主要反映周期成本；变频等可能影响解释。
- `cache-misses`：反映该事件的样本分布，不是 CPU 时间分布。
- 如果只采 `cycles:u`，只观察用户态，不能据此说内核没有成本。

**采样不是调用次数计数。** 某函数 30% 样本不表示它占 30% 请求，也不表示被调用 30% 次。需要解释样本分母、事件类型、采集范围和调用栈汇总方式。

### 6.5 调用栈：fp、DWARF、LBR

| 方式 | 原理 | 优点 | 局限 |
|---|---|---|---|
| `--call-graph fp` | 沿 frame pointer 链回溯用户栈 | 通常开销较小、数据较少 | 应用/库省略帧指针会断栈；优化、尾调用仍影响呈现 |
| `--call-graph dwarf,8192` | 采样用户寄存器/栈片段，依据 CFI 展开 | 可用于省略帧指针的代码 | 栈拷贝与离线展开成本较高；需相应 unwind 信息和 perf 构建支持；栈片段可能截断 |
| `--call-graph lbr` | 利用支持平台的硬件分支记录 | 在支持平台有特定优势 | 主要限特定 Intel 等实现，深度/能力有限，不是通用 ARM64 方案 |

`--call-graph` 选项主要控制**用户栈**。内核栈由目标内核的 unwinder/config 决定，例如 ORC 或 frame pointer；不能以为选择 DWARF 就自动修复内核栈。

构建建议：

```bash
# 面向性能测试的构建：保留真实优化，同时增加调试/回溯能力
cc -O2 -g -fno-omit-frame-pointer \
    -o /opt/demo/app /opt/demo/app.c
```

`-g` 与优化可以并存。`-g` 帮助源码/变量映射；CFI 帮助展开栈；frame pointer 是另一种回溯基础，它们不是同一个概念。生产制品可以采用分离 debug info，避免只留被 strip 的二进制却丢失配套符号。

### 6.6 report、annotate 与火焰图

报告中常见：

- **Self**：样本 IP 实际落在该符号上的占比。
- **Children/累计成本**：把子调用的样本累加到调用者；一个样本可归属于多层调用者，所以各项不能直接相加。
- `[unknown]`：可能缺符号、缺映射、栈展开失败或 JIT 代码未注册，并不是一个真的“unknown 函数”。

```bash
perf report -i "$OUT/perf.data" --stdio --sort comm,dso,symbol
# 替换实际热点函数，C++ 名称及内联定位可能需要进一步选项
perf annotate -i "$OUT/perf.data" --stdio -s hot_function
perf buildid-list -i "$OUT/perf.data"
```

火焰图通常是**聚合调用栈视图**：横向宽度表示样本/事件权重，纵向表示调用深度。**普通火焰图横轴不是时间线**；需要保留时间顺序的是 flame chart 或其他时序图。

`perf script` 导出 → stackcollapse → flamegraph 是常用流程，但 stackcollapse/flamegraph 并非所有发行版随 perf 安装。CPU 火焰图、off-CPU 火焰图和分配火焰图的宽度单位不同，必须在图上标注。

### 6.7 实战三：调度等待分析

```bash
# 有意采集全系统，保留调度上下文；数据量可能较大
sudo perf sched record -o "$OUT/perf-sched.data" -- sleep 10
perf sched latency -i "$OUT/perf-sched.data"
perf sched timehist -i "$OUT/perf-sched.data" -p "$PID"
```

不同 perf 版本的选项与输出列可能变化。timehist 中的 wait time、调度 delay、运行时长应按本机手册解释；不要把所有“未运行”时间都叫调度等待。仅按 PID 限制原始 sched 采集可能漏掉由其他线程发出的 wakeup，常见做法是短窗口全系统采集，再在报告阶段筛选目标。

### 6.8 常用场景与限制

- **计算/自旋热点**：record/report 找函数；annotate 看指令；再用业务基准确认优化效果。
- **微架构瓶颈**：stat、平台支持的 Top-down/内存事件；先确认问题确实 CPU-bound。
- **调度/锁/伪共享**：sched、lock、c2c 等专项工具；不要把 `perf lock` 视为所有用户态 pthread mutex 的通用统计器。
- **虚拟机**：PMU 可能未透传或事件支持有限；可退回软件事件，但不能伪造硬件结论。
- **JIT/托管语言**：Java/Go/Rust 等需要各自的符号、回溯和运行时配置；原生 perf 符号可能不完整。Go/Rust 常有原生函数符号，但优化与栈/运行时行为仍需注意，Java 还需要 JIT 符号辅助。
- 结束后检查 lost samples、采样降频、multiplexing、符号匹配和采样时长；“没有热点”也可能只是采集质量差。


---

<a id="s7"></a>
## 7. eBPF：可编程观测与内核侧聚合

### 7.1 eBPF 不是一个单独命令

eBPF 是 Linux 内核支持的受约束可编程执行机制。它不仅用于 tracing，还可用于网络数据路径、cgroup 控制和安全策略等。本文只讨论**以观测为目的的 eBPF**，不讨论修改返回值、拦截业务或更改网络转发行为。

常用工具/开发方式：

| 名称 | 定位 | 适合场景 |
|---|---|---|
| bpftrace | 高层 tracing DSL，快速编译并挂载探针 | 一行命令、现场验证假设、延迟直方图 |
| BCC | BPF 工具和开发框架，很多工具用 Python/C | 直接使用成熟的 I/O、网络、调度工具 |
| libbpf + CO-RE | C 库与预编译部署方式 | 产品化 agent、可维护的专用观测器 |
| bpftool | 内核 BPF 对象、BTF、功能检查等管理工具 | 检查程序、map、link、特性和加载问题 |

bpftrace/BCC 是“用 eBPF 的方式”，不是和 eBPF 并列的另一种内核机制。

### 7.2 程序生命周期与执行路径

```text
源程序/脚本
  → 编译为 BPF 字节码（常用 LLVM；CO-RE 可预编译）
  → 用户态 loader 创建 map、解析重定位
  → bpf() 等接口提交程序
  → verifier 检查类型、控制流、内存访问和资源规则
  → 支持时 JIT 为目标机器代码，否则按内核支持方式执行
  → attach 到 tracepoint / kprobe / uprobe / fentry 等 hook
  → 事件发生时执行程序，更新 map 或提交事件
  → 用户态定期读取聚合/消费 ring buffer
  → detach，并释放程序/map/link 等对象引用
```

**verifier 的作用**：静态分析寄存器与栈状态，检查初始化、指针类型/范围、内存边界、helper 参数及程序复杂度等。它不是“证明观测结论正确”的工具：时间戳配对、过滤条件、单位、map 键错误都可能通过验证却产生错误统计。

早期 eBPF 对循环限制很严格；较新内核支持可验证的 bounded loops，以及某些辅助迭代机制。不能把“eBPF 不允许任何循环”当成通用现状。具体循环、helper、kfunc 和复杂度限制依内核/程序类型而变。

### 7.3 maps、事件通道与并发

- **hash map**：存储 `TID → 开始时间`、对象指针 → 状态等关联信息。
- **array map**：固定索引统计和配置。
- **per-CPU map**：减少热点计数的跨 CPU 竞争，用户态需要合并。
- **stack trace map**：记录调用栈标识，方便按栈聚合。
- **LRU map**：限制容量，但淘汰可能导致关联信息丢失，不能把被淘汰的数据当成未发生。
- **perf buffer / BPF ring buffer**：把事件发送到用户态；它们有容量限制，用户态消费不及时可能丢数据。ring buffer、perf buffer 和 ftrace buffer 不是同一个对象。

观测程序随事件在不同 CPU 上运行，同一个 map 可能被并发更新。计数应优先使用工具提供的聚合原语，如 bpftrace 的 `count()`/`sum()`，或在产品化程序中设计正确的原子/per-CPU 更新方式，不要假设普通读-改-写天然无竞争。

性能上常用的策略是：**内核中先筛选、聚合，用户态低频取结果**。例如百万次系统调用只输出一个延迟直方图，通常比逐次打印更可控；但每个事件触发的程序执行成本仍然存在。

### 7.4 探针类型如何选择

| 类型 | 观测位置 | 优点 | 局限/注意事项 |
|---|---|---|---|
| tracepoint | 内核定义的语义事件 | 字段较清楚，通常优先选择 | 目标可能没编译该事件；字段需现场查询 |
| raw tracepoint | 更原始的 tracepoint 参数 | 某些场景路径更直接 | 参数解释更底层，类型和兼容性要求更高 |
| kprobe/kretprobe | 内核函数/指令及返回 | 灵活，不要求预定义事件 | 函数名/签名非稳定 ABI；部分位置不可探测；返回探针可漏实例 |
| fentry/fexit | 支持的函数入口/退出 tracing hook | BTF 类型支持，常有较低探针成本 | 需要内核、BTF、架构和具体函数支持 |
| uprobe/uretprobe | 用户态二进制/共享库函数及返回 | 不改源码观察用户函数 | 内联/优化/符号/ABI/路径问题；热点函数成本可能明显 |
| USDT | 应用预定义的用户态静态探针 | 语义更接近业务阶段/请求 | 应用必须提供相应探针，版本和参数需确认 |
| profile/perf event | 周期性运行现场或性能事件 | CPU 栈采样、事件采样 | 受 perf 权限、栈可回溯性和采样偏差影响 |

实践优先级通常是：**应用埋点/USDT或合适 tracepoint → 支持的 fentry/fexit → 必要时 kprobe/uprobe**。不是绝对性能排名；应按语义稳定性、可用性和测量开销选择。

### 7.5 BTF 与 CO-RE

**BTF（BPF Type Format）**是类型信息格式。内核可能在 `/sys/kernel/btf/vmlinux` 暴露自身 BTF，模块也可能提供 BTF。

**CO-RE（Compile Once – Run Everywhere）**使用编译期记录的类型/重定位信息，在加载时由 libbpf 根据目标 BTF 适配字段偏移等，使同一 BPF 对象更容易跨不同内核部署。

但它**不保证任意版本/架构/配置完全兼容**：目标仍必须具有所需 hook、helper/kfunc、程序类型、BTF 信息和语义；删除的字段、改变的函数语义、缺失的功能不能仅靠偏移重定位解决。跨架构通常还需相应构建策略。

BCC 的许多传统工具在目标机编译，可能依赖 LLVM 和内核头文件；libbpf CO-RE 常能减少目标侧运行时编译依赖，但二者都必须核对具体工具实现。

### 7.6 运行前检查

```bash
bpftrace --version
sudo bpftrace --info
sudo bpftool feature probe kernel
ls -l /sys/kernel/btf/vmlinux
cat /proc/sys/kernel/unprivileged_bpf_disabled

# 只列出或查看字段，不加载业务观测程序
sudo bpftrace -l 'tracepoint:syscalls:sys_*read'
sudo bpftrace -lv 'tracepoint:syscalls:sys_exit_read'
sudo bpftool prog show
sudo bpftool map show
sudo bpftool link show
```

- 缺 BTF 不等于所有 eBPF 都不能使用，但会限制 BTF/CO-RE/fentry 等方式。
- root 也可能被 lockdown、LSM、容器 capability 和 seccomp 等限制。
- 较新内核有 `CAP_BPF`、`CAP_PERFMON` 等细分权限，不同 attach 类型可能还需要其他能力；不能只授予一个 capability 就保证所有探针可用。
- 旧工具可能依赖 `RLIMIT_MEMLOCK`，较新内核可能使用 memcg 记账；报错时先识别实际机制，不要无条件设置无限 memlock。

### 7.7 bpftrace 的关键语义

典型语法：

```text
探针 /过滤条件/ {
    操作;
}
```

- `pid`：当前进程 TGID，通常是用户所说的进程 PID；`tid`：当前线程 TID。
- `comm`：线程名，可修改且可能截断，不是进程唯一标识。
- `$1`、`$2`：命令行位置参数。
- `args`：tracepoint 参数，字段由目标事件决定。
- `nsecs`：纳秒时间戳，时间基准/可选模式与版本有关；同一次测量保持相同基准，不直接作为墙钟时间打印。
- `@name[key]`：BPF map；`count()`、`sum()`、`hist()` 等用来聚合。
- `hist(x)`：通常是 log2 直方图；`lhist(x,min,max,step)` 是线性直方图。

**版本说明**：下列 tracepoint 示例采用较新 bpftrace 常用的 `args.field` 写法。较旧版本文档常用 `args->field`；如解析报错，先查 `--version` 和本机文档，再按支持语法调整，而不是更改事件名碰运气。探针不存在和语法不兼容是两类不同问题。

### 7.8 实战一：统计目标进程系统调用

```bash
# 观察 10 秒，按系统调用号计数；不输出每次调用的敏感参数
sudo bpftrace -e '
tracepoint:raw_syscalls:sys_enter /pid == $1/ {
    @syscalls[args.id] = count();
}
interval:s:10 { exit(); }
' "$PID"
```

这回答“调用次数最多的是什么”，不回答“最耗时的是什么”。系统调用号与架构 ABI 有关；高次数但每次极短的调用可能不是主要成本。

### 7.9 实战二：read 系统调用耗时与错误分布

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_read /pid == $1/ {
    @start[tid] = nsecs;
}
tracepoint:syscalls:sys_exit_read /pid == $1 && @start[tid]/ {
    @latency_us = hist((nsecs - @start[tid]) / 1000);
    if (args.ret > 0) {
        @read_bytes = sum(args.ret);
    }
    if (args.ret < 0) {
        @errors[-args.ret] = count();
    }
    delete(@start[tid]);
}
tracepoint:sched:sched_process_exit /pid == $1/ {
    delete(@start[tid]);
}
interval:s:10 { exit(); }
END { clear(@start); }
' "$PID"
```

为什么 map 按 `tid` 而不是 `pid`？多线程进程的 read 可以并发；按 TGID 只存一个开始时间会互相覆盖。

结果含义：

- `@latency_us` 的单位是微秒，因为纳秒差值除以了 1000。
- read 区间是**系统调用进入到退出的墙钟间隔**，可能包含缓存访问、等待、调度延迟和信号处理影响，**不是磁盘设备的纯服务时间**。
- `ret > 0` 才累计成功读取字节；`ret == 0` 通常表示 EOF；负值按 errno 计数。
- 不覆盖 `pread64`、`readv`、mmap 访问以及所有 io_uring 路径，不能把它当成进程总磁盘读量。
- 开始/结束窗口可能缺一侧事件，线程退出或 PID 复用也影响关联；成熟工具需要处理这些边界。
- syscall、用户函数和异步 I/O 的区间是不同测量对象，不能混为同一请求延迟。

对偶发长尾，还应加上“超过阈值才上报”的策略，但事件中涉及路径、参数、栈的采集需做数据安全评估。

### 7.10 实战三：CPU 用户栈采样

```bash
sudo bpftrace -e '
profile:hz:99 /pid == $1/ {
    @[ustack] = count();
}
interval:s:10 { exit(); }
' "$PID"
```

这是 on-CPU 采样，不会自动得到睡眠线程的等待栈。用户栈是否完整与 frame pointer、运行时和 bpftrace/内核支持有关，不要以为与 perf DWARF 展开能力完全相同。

### 7.11 用户态函数耗时：uprobe/uretprobe

```bash
# 先确认该符号确实存在；C++ 名称、共享库、内联可能需要特殊处理
sudo bpftrace -l 'uprobe:/opt/demo/app:handle_request'

sudo bpftrace -e '
uprobe:/opt/demo/app:handle_request /pid == $1/ {
    @begin[tid] = nsecs;
}
uretprobe:/opt/demo/app:handle_request /pid == $1 && @begin[tid]/ {
    @handler_us = hist((nsecs - @begin[tid]) / 1000);
    delete(@begin[tid]);
}
interval:s:10 { exit(); }
END { clear(@begin); }
' "$PID"
```

**这是教学简化版，仅适合同一 TID 上没有递归/重入、正常返回的同步函数。** 递归会覆盖开始时间；异常展开、longjmp、线程退出可能没有匹配返回；异步/协程请求不一定在同一线程上完成。产品化观测应按调用深度、请求 ID、USDT 或应用阶段埋点设计关联。

uprobe 通常按二进制映射的探针位置工作，再由 `/pid == .../` 做程序内筛选。共享库被很多进程调用时，探针触发本身可能影响其他映射该文件的进程，不能把过滤理解成对非目标绝对零成本。

### 7.12 BCC 常见工具

| 工具名 | 观测内容 | 适用症状 | 不能直接推出的结论 |
|---|---|---|---|
| execsnoop | 进程执行事件 | 异常频繁启动、短命进程 | 不等于完整进程审计日志 |
| opensnoop | 文件打开事件 | 配置/路径访问异常 | 参数可能敏感；不覆盖所有文件访问 |
| biolatency | 块 I/O 延迟直方图 | 磁盘长尾 | 是否包含排队依参数和工具实现 |
| biosnoop | 单个块 I/O 事件 | 找设备与慢请求 | 块 I/O 不一定能简单归属于原始应用 PID |
| runqlat | 可运行到获得 CPU 的延迟 | 调度长尾 | 不等于全部 off-CPU 时间 |
| offcputime | 调度出去到再次运行的时间及栈 | 阻塞和等待 | 会包含多种等待，通常也包含等待重新调度的时间 |
| tcpretrans | TCP 重传事件 | 重传增多 | 不单独证明重传原因 |
| tcpconnect | TCP 建连 | 连接异常/频繁短连接 | 不等于应用请求数 |
| memleak | 观测窗口内分配/释放关联 | 疑似未释放分配 | 不保证发现窗口前分配或所有 allocator；未释放不一定是 bug |

包可能把命令安装为 `biolatency-bpfcc`，或放在 `/usr/share/bcc/tools/` 等位置。先使用 `command -v`/包管理器确认位置和 `--help`，不要把名称后缀和参数当成跨发行版标准。

### 7.13 eBPF 的核心风险

1. verifier 拒绝是安全/类型/复杂度不满足，不应通过关闭安全机制解决。
2. 内核函数名和参数会变，kprobe 基于旧签名读取可能造成错误解释。
3. 高事件频率 + 高频 `printf()` + 用户栈会明显增加开销。
4. map key 过多造成内存增长；LRU 淘汰会影响完整性。
5. ring/perf buffer 丢事件时，延迟和关联结果可能偏差。
6. 用户态聚合、符号化和输出也占 CPU，不只是内核程序有成本。
7. 程序关闭后通常随引用释放卸载；**pinned 对象、持久 link 或其他引用可能让对象继续存在**。不要删除别人正在使用的 BPF 对象，只检查本次加载的对象。

---

<a id="s8"></a>
## 8. GDB：运行状态与代码级调试

### 8.1 原理机制

在常见 Linux 本地进程调试中，GDB 通过 ptrace 等接口控制目标、读取寄存器/内存、处理停止事件和信号。它结合 ELF 符号、DWARF 调试信息、unwind 信息，把机器状态映射成函数、变量和源码。

- **软件断点**：在目标代码位置设置架构特定 trap 指令；命中后控制权返回调试器，再处理原指令继续执行。
- **硬件断点/观察点**：利用 CPU 调试资源，在指定代码执行或内存访问时触发。数量、宽度、对齐与访问类型受架构限制。
- **单步**：逐指令执行，源码单步可能需要执行多个机器指令。
- **栈回溯**：根据寄存器、栈帧和 unwind/debug 信息重建调用链。

GDB 也能通过 remote protocol、gdbserver 或嵌入式目标接口调试，不是所有使用方式都由本地 ptrace 完成。

### 8.2 运行调试 vs. 离线调试

| 方式 | 命令形态 | 对象是否运行 | 适合问题 |
|---|---|---|---|
| 从 GDB 启动 | `gdb --args /path/app ...` | 可启动/暂停/继续 | 可复现错误、逻辑与变量问题 |
| 附着已有进程 | `gdb -p PID` | attach 通常立即停止目标 | 卡死、状态检查、有限在线诊断 |
| 加载 core | `gdb /path/app /path/core` | 进程已经终止 | 偶发崩溃现场，不影响当前服务 |
| 远程调试 | gdbserver + GDB | 按目标方式控制 | 远端主机、交叉架构、设备 |

**生产环境原则：优先离线 core；只有批准的窗口内才 attach。** 常见 all-stop 模式下命中停止事件会停所有被调试线程。non-stop 模式需要显式配置和目标支持，也不保证没有业务扰动。

### 8.3 符号与构建准备

```bash
# 复现和变量调试，便于理解的构建；不要据此代表生产性能
cc -Og -g3 -fno-omit-frame-pointer \
    -o /opt/demo/app-debug /opt/demo/app.c

# 在真实优化级别保留调试信息，用于性能问题复现
cc -O2 -g -fno-omit-frame-pointer \
    -o /opt/demo/app-perf /opt/demo/app.c
```

必须保留与现场一致的：可执行文件、共享库、debug files、Build ID、源码版本和构建参数。**同一源代码重新编译出来的二进制不保证能匹配旧 core**，地址布局和优化都可能变化。

`<optimized out>` 往往说明该变量未按源码形式保留，或者在当前 PC 没有可用位置；不是 GDB 随便坏了。内联和尾调用也会改变栈和单步表现。

### 8.4 启动与基础命令

```bash
gdb --args /opt/demo/app-debug --config /etc/demo.conf
```

在 GDB 中执行（代码块不含 `(gdb)` 提示符，可按需输入）：

```gdb
set pagination off
break main
run
info args
info locals
next
step
finish
bt
bt full
info registers
x/16gx $sp
x/8i $pc
continue
```

| 命令 | 含义 |
|---|---|
| `break main` / `break file.c:120` | 在函数或源码行设断点 |
| `tbreak ...` | 命中一次后删除的临时断点 |
| `condition N expr` | 为第 N 个断点设置条件 |
| `next` / `step` | 按源码行执行，前者通常跨过调用，后者进入调用 |
| `nexti` / `stepi` | 按机器指令执行 |
| `finish` | 运行到当前函数返回 |
| `continue` | 恢复运行直到下一停止事件 |
| `bt` / `bt full` | 当前线程调用栈；full 额外显示局部变量等 |
| `frame N` / `up` / `down` | 选择或切换栈帧 |
| `print expr` / `p/x expr` | 求值，后者按十六进制 |
| `ptype obj` | 查看类型 |
| `x/NFU address` | 按数量、格式、单位检查内存 |
| `disassemble /m func` | 混合源码与汇编，需信息支持 |
| `info breakpoints` / `delete N` | 查看/删除断点 |

`x/16gx $sp` 表示从栈指针起查看 16 个 8 字节单位、以十六进制显示。`$pc/$sp` 是 GDB 的通用寄存器别名，具体架构寄存器可用 `info registers` 查看。

### 8.5 附着一个运行中的进程

```bash
# 执行前确认 PID、服务影响和允许的最长暂停时间
sudo gdb -q -p "$PID"
```

```gdb
set pagination off
info threads
thread apply all bt
thread apply all bt full
detach
quit
```

- attach 后目标先停止；`detach` 解除控制并让被附着进程继续运行。
- 不要在 attach 会话中误输入 `run`：它可能终止原目标并重新运行程序，GDB 通常会确认。
- `thread apply all bt full` 会遍历线程和变量，线程很多时可能暂停很久，也可能输出敏感数据。
- 不要仅为缩短命令把 `sudo gdb` 当成完全无副作用的监控手段。

批准后可使用批处理收集简短栈：

```bash
sudo gdb -q -nx -batch -p "$PID" \
    -ex 'set pagination off' \
    -ex 'thread apply all bt' \
    -ex 'detach' > "$OUT/threads.txt" 2>&1
```

`-nx` 跳过初始化文件，减少个人配置干扰；它不完全等同于禁止所有 objfile auto-load。分析不可信制品应单独设置 auto-load 策略、隔离环境和网络权限。

### 8.6 多线程与死锁

```gdb
info threads
thread 3
bt
frame 2
info args
info locals
thread apply all bt
show scheduler-locking
```

GDB 线程编号 `thread 3` 不是 Linux TID。`info threads` 中的 LWP/线程标识才与系统线程号对应；需要把它与 pidstat/ftrace 的 TID 对上。

死锁分析不是“发现 futex 就完成”：

1. 多次有限窗口获取线程栈，确认是否长期停在同一位置。
2. 找哪些线程等哪些锁/条件/队列，锁所有者是谁。
3. 建立等待关系图，查是否有环或永远不可能满足的条件。
4. 区分正常闲置 worker、I/O 等待、线程池耗尽与真正锁循环。
5. pthread mutex 内部结构不是通用 ABI，直接解读内部字段需要对应 libc 版本和符号，不宜套用固定偏移。

`set scheduler-locking on/step` 会限制继续或单步时哪些线程可以运行，可能让依赖其他线程的代码无法推进。只在明确理解影响时使用，不能视作“修复竞争”的配置。

### 8.7 观察点：定位谁破坏了内存

```gdb
# 先运行到对象有效、已知地址的状态
break update_state
run
watch -l state.counter
continue
bt
```

- `watch expr` 在表达式被写且**值发生变化**时停住；不保证每次写相同值都报告。
- `watch -l expr` 按当前位置求出地址后观察这块内存，适合固定对象字段。
- `rwatch expr` 观察读；`awatch expr` 观察读/写；通常要求硬件支持。
- 硬件资源数量/宽度有限，超过限制可能直到恢复运行时才失败。
- 软件 watchpoint 可能以单步和比较实现，非常慢；多线程下还有可见性限制。
- 对已经释放/重新分配的地址，观察点命中的是该地址的新访问，不自动证明仍属于原对象。

### 8.8 信号、远程调试与安全

```gdb
info signals
catch signal SIGSEGV
catch signal SIGABRT
```

在可复现测试中捕获信号可在终止前检查现场。不要随意 `handle SIGSEGV nostop noprint pass` 或吞掉信号，这可能掩盖错误或改变应用行为。框架使用信号作为正常机制时，要结合运行时文档。

远程调试通常使用 gdbserver，**调试协议本身不应暴露到公网**。应绑定批准的受限接口，通过受控隧道或运维通道访问，并限制访问者；有调试权限的人通常可以读写目标内存。

GDB 不是只读工具：`call func()`、`print` 中的函数调用、赋值和修改寄存器会在目标上执行或改变状态，可能触发锁、I/O 或副作用。只读现场检查时避免这些操作。

### 8.9 GDB 的适用边界

- 适合“具体状态为什么错误”，不适合持续计算全机性能指标。
- 断点/单步改变调度，可能让并发问题消失，也可能引入新的超时。
- 一个现场不能自动证明 UAF、越界写或数据竞争的最初发生点；通常要配合 ASan、TSan、观察点和复现。
- 普通用户态 GDB attach 不等于可调试整个运行内核；内核调试有专门机制，如 KGDB 或 vmcore 离线分析。


---

<a id="s9"></a>
## 9. coredump：崩溃现场保留与离线分析

### 9.1 coredump、systemd-coredump、coredumpctl 的区别

- **core dump / coredump**：进程终止时保存的现场数据及转储机制。
- **systemd-coredump**：常见的用户态收集器，接收内核转交的 core，保存元数据、处理栈并按策略存储。
- **coredumpctl**：检索 systemd 已保存的崩溃记录、导出 core 或启动调试器的客户端。
- **GDB**：读取 core + 匹配二进制/符号，解释代码状态。

并非所有 Linux 都使用 systemd-coredump。Ubuntu 的 Apport、某些发行版的 ABRT、自定义收集器或直接文件模式都可能接管；最终以 `kernel.core_pattern` 为准。

### 9.2 原理机制与文件内容

某些信号的默认动作是终止并转储，例如 `SIGSEGV`、`SIGABRT`、`SIGBUS`、`SIGILL` 等。应用若自定义处理信号或改变终止方式，行为可能不同。

```text
错误/显式 abort 等
  → 收到具有 core 默认动作的信号
  → 内核检查 dumpable、配置及转储条件
  → 收集线程寄存器、映射及允许转储的内存等
  → 根据 core_pattern：直接写文件，或管道交给收集器
  → 元数据与 core 按存储/容量/保留策略处理
  → GDB 结合匹配制品离线分析
```

Linux 用户态 core 通常是 ELF core，包含：

- 各线程寄存器状态、线程信息及相关 ELF notes。
- 部分虚拟内存映射的内容，例如栈、匿名映射等，受过滤配置影响。
- 信号和文件映射等元信息，具体 notes 依架构/内核而不同。

**core 不一定包含全部内存、全部代码或完整符号**。文件映射可能需要从原二进制补充，某些页被过滤或文件被截断。debug info 通常仍需独立提供。

### 9.3 哪些退出不会自动产生 core

| 情况 | 常见行为 |
|---|---|
| 正常返回/exit | 不因正常退出而产生 core |
| SIGTERM | 默认终止，通常不产生 core |
| SIGKILL | 不可捕获的强制终止，不产生默认 core |
| OOM killer / cgroup OOM kill | 通常以 SIGKILL 终止，不能指望有用户态 core |
| SIGSEGV/SIGABRT 的默认终止动作 | 满足转储条件时可以产生 core |
| GDB 的 gcore | 主动生成运行进程快照，不是该进程已崩溃 |

看到“进程消失但没有 core”，应先确认退出原因，而不是立刻改 `ulimit`。应用自己的 crash handler 可能退出或上传另一种格式，也要核对。

### 9.4 先检查真实转储路径与限制

```bash
# 当前 shell 的限制，不代表一个早已运行的服务的限制
ulimit -Sc
ulimit -Hc
cat /proc/sys/kernel/core_pattern
cat /proc/sys/kernel/core_pipe_limit
cat /proc/sys/fs/suid_dumpable

# 存活进程的真实限制与过滤条件
cat "/proc/$PID/limits"
cat "/proc/$PID/coredump_filter"

# systemd 管理服务时，替换为真实 unit
systemctl show example.service -p LimitCORE -p MainPID
systemctl cat example.service
```

`core_pattern` 的两类形态：

```text
/var/lib/example-cores/core.%e.%p.%t
|/usr/lib/systemd/systemd-coredump ...
```

第一类是文件模板；第二类以 `|` 开头，表示把 core 通过标准输入交给指定用户态程序。路径仅展示形态，不能直接覆盖主机现有收集器。

常见模板字段：`%e` 是 comm，可能截断；`%p` 是进程所在 PID namespace 可见 PID；`%P` 是初始 PID namespace 可见 PID；`%t` 是 Unix 时间戳；`%s` 是信号号。直接文件路径还受崩溃进程的 mount namespace、root 和工作目录影响。

**关键例外：管道 handler 时，内核不按 RLIMIT_CORE 限制管道 core 内容大小。** 收集器可能根据传入限额和自身策略决定保存行为。因此：

- `ulimit -c 0` 不能被笼统地解释为“所有系统上绝对没有收集行为”。
- `ulimit -c unlimited` 也不能保证收集器无限保存。
- `ulimit` 通常只影响当前 shell 及随后启动的子进程，不改变已运行服务。

### 9.5 启用策略：按启动方式处理

**由 shell 启动的测试程序**：先确认使用哪一种收集机制，再按测试政策设置限制。

```bash
# 只影响当前 shell/随后启动的子进程；硬限制也必须允许
ulimit -c unlimited
/opt/demo/app-debug --config /etc/demo-test.conf
```

**systemd 服务**：管理员可通过 unit drop-in 设定，示意内容为：

```ini
[Service]
LimitCORE=infinity
```

实际变更需要按流程编辑目标 unit、daemon-reload，并在允许的重启窗口让新进程继承配置。**这里不提供自动重启业务的脚本，也没有对任何服务做变更。** 管道收集器的容量/处理限制仍需单独设置。

不要同时盲目改 `core_pattern`、unit limit、systemd-coredump 和容器权限。先知道当前链路，再只改必要环节。

### 9.6 使用 coredumpctl

```bash
# 时间和程序路径按真实故障调整
coredumpctl --no-pager --since '2026-11-19 00:00:00' list
coredumpctl --no-pager list /opt/demo/app
coredumpctl info 12345

# 导出最近一个匹配的 core；始终给 -o，避免二进制输出到终端
coredumpctl -o "$OUT/app.core" dump 12345

# 直接使用系统默认调试器分析最近匹配项
coredumpctl debug 12345
```

PID 会复用；只写 PID 可能匹配到其他历史事件。导出前核对**时间、EXE、unit、Build ID、boot ID**等元数据，并使用 `--since/--until` 或 journal match 收窄范围。`dump/debug` 通常选择最近匹配项，不是自动分析所有同名崩溃。

`COREFILE` 常见值：

| 值 | 解释 |
|---|---|
| `present` | core 可由当前用户访问 |
| `journal` | core 存储在 journal 中 |
| `missing` | 元数据还在，但外部 core 文件已被移除 |
| `truncated` | 文件可用但没有完整保存 |
| `none` | core 没有存储 |
| `error` | 无法访问，常见原因是权限问题 |
| `-` | 没有可用 core，例如并非由相关信号终止的记录 |

**有 journal 崩溃记录不等于 core 文件还在。** 元数据保留和外部 core 清理是不同生命周期。某些旧版使用 `gdb` 子命令而不是 `debug`，以本机 `coredumpctl --help` 为准。

### 9.7 systemd-coredump 的容量策略

以下是**管理员评审用示例，不是推荐所有主机照抄**：

```ini
[Coredump]
Storage=external
Compress=yes
ProcessSizeMax=2G
ExternalSizeMax=2G
MaxUse=10G
KeepFree=5G
```

| 选项 | 控制的是什么 |
|---|---|
| `Storage` | 是否/在哪里永久保留 core，external、journal 或 none |
| `Compress` | 外部存储压缩策略 |
| `ProcessSizeMax` | 多大 core 允许进行处理/生成栈；超过后仍可能保存而不生成栈 |
| `ExternalSizeMax` / `JournalSizeMax` | 单份 core 的存储大小策略 |
| `MaxUse` | 外部 core 的总空间清理目标 |
| `KeepFree` | 尽量保留的空闲空间；处理过程中仍可能临时超出目标 |

- `Storage=none` 主要是不永久保留；若仍进行处理，可能先写临时磁盘数据。
- 根据当前 systemd 文档，`Storage=none` 与 `ProcessSizeMax=0` 组合可关闭除日志记录外的处理；不能只改一个选项就认为所有 I/O 消失。
- 还可能有按时间的 systemd-tmpfiles 清理策略，单看 MaxUse 不够。
- 配置应使用本机支持的 drop-in，核对版本和实际生效值；某些版本可用 `systemd-analyze cat-config systemd/coredump.conf` 查看。

### 9.8 GDB 离线分析流程

```bash
# 先识别格式；.zst 等压缩 core 可通过 coredumpctl 导出为原始 core
file "$OUT/app.core"
readelf -n /opt/demo/app

# 二进制必须匹配崩溃现场，不要拿刚刚重编译的新版
# -nx 不读取用户初始化文件；对不可信制品另设 auto-load 安全策略
gdb -q -nx /opt/demo/app "$OUT/app.core"
```

```gdb
set pagination off
info files
info sharedlibrary
info threads
bt full
thread apply all bt
info registers
x/8i $pc
x/16gx $sp
# 若目标 core 提供信号信息，查看其具体结构
p $_siginfo
frame 1
info args
info locals
```

推荐顺序：

1. 核对程序版本/架构、core 是否完整、共享库是否匹配。
2. 确认终止信号与故障线程，而不是随机挑一个线程看。
3. 看崩溃指令，判断是读、写、跳转还是显式 abort 路径。
4. 分析参与指令的地址/寄存器和调用参数，检查空指针、越界或损坏。
5. 看调用者和其他线程，判断对象生命周期、锁状态、并发关系。
6. 得出可验证的假设，在复现环境用 sanitizer/watchpoint/日志进一步证明。

如果 core 位于跨主机或容器环境，提前准备归档 rootfs/库及符号：

```gdb
# 这些是示例路径；应指向已归档的目标制品
set sysroot /srv/incident/rootfs
set solib-search-path /srv/incident/libs
set debug-file-directory /srv/incident/debug
set substitute-path /build/workspace /srv/incident/src
file /srv/incident/bin/app
core-file /srv/incident/app.core
info sharedlibrary
```

- `sysroot`：按原目标目录结构查找二进制和共享库。
- `solib-search-path`：共享库搜索补充目录。
- `debug-file-directory`：独立 debug files 的搜索位置。
- `substitute-path`：把编译时源码路径映射到本地归档源码。
- 若架构不同，使用支持目标架构的 GDB，不要误读寄存器/指令。

core 是快照，不能 `continue` 让死去的进程从原现场恢复；`run` 会启动一个新的程序，不是在回放旧现场。

### 9.9 gcore：运行进程的主动快照

```bash
# 只有批准的暂停窗口才执行；文件名通常附加 .PID
sudo gcore -o "$OUT/live-core" "$PID"
```

gcore 一般借助调试器附着并短暂停止目标来生成快照，然后继续原进程。大进程可能停顿明显；工具/目标选项会影响转储哪些映射。

与崩溃 core 的区别：快照时线程不一定处于故障点，没有崩溃原因保证；适合卡死/状态检查和后续离线阅读。它也不是持续性能采样，更不保证捕获瞬间竞争。

### 9.10 没有 core 时的排查清单

1. **退出原因**：SIGKILL/OOM/正常退出还是具有 core 动作的信号？查看 unit 日志、内核日志和容器状态。
2. **链路**：core_pattern 是文件还是 handler？是否由 Apport/ABRT/自定义程序处理？
3. **限制**：实际进程的 limits、systemd unit、handler 大小策略，不能只看当前 shell。
4. **dumpable/security**：setuid/file capability、PR_SET_DUMPABLE、LSM/lockdown 和安全策略。
5. **路径/空间**：目录存在且可写吗？文件系统只读、空间/inode/配额是否耗尽？
6. **过滤/截断**：coredump_filter、MADV_DONTDUMP 或收集器是否排除必要数据？
7. **收集能力**：handler 是否安装/正常？core_pipe_limit 是否造成并发跳过？
8. **保留周期**：已被容量/时间清理？只是 journal 仍列出记录？
9. **容器映射**：PID/mount namespace 是否导致查错路径或 PID？
10. **内核配置**：是否支持 COREDUMP/对应格式？

`coredump_filter` 是内存映射类型位掩码，读取时通常显示十六进制。修改它影响取证完整性与数据暴露范围，不能为了“文件小一点”随意删掉关键匿名内存/栈。

### 9.11 敏感数据与核心限制

core 可能包含口令、token、私钥材料、用户数据、请求内容和业务机密。应限制访问、加密存储/传输、设置保留期并受控共享。不能把整个 core 公开上传；即使栈文本也可能包含敏感参数。

core 不能回答：完整历史、最早谁破坏内存、所有请求延迟、已经被过滤掉的页。崩溃在 libc 内部可能只是早前堆损坏的后果，而非 libc 本身错误。

---

<a id="s10"></a>
## 10. 其他常用工具与边界

### 10.1 strace：系统调用和信号视角

**机制**：传统 strace 通过 ptrace 在系统调用/信号边界观察进程；部分版本支持不同加速路径，但默认不能按“零侵入”理解。

```bash
# 统计调用次数、错误与耗时；-c 的时间口径要查本机文档
strace -f -c -p "$PID"

# 记录有限种类的调用；人工结束 strace 通常解除附着，不终止业务
strace -ff -tt -T -s 128 \
    -e trace=read,write,openat,close,futex,connect,recvfrom,sendto \
    -o "$OUT/strace" -p "$PID"
```

- `-f/-ff`：跟踪子任务，`-ff` 可拆分输出，具体线程行为以版本为准。
- `-tt`：较细墙钟时间戳；`-T`：单次系统调用的区间耗时。
- `-s`：字符串显示长度限制，不等于没有敏感数据。
- `-c` 中 time 统计与逐调用 `-T` 不一定是同一种口径；支持时 `-w` 可改变汇总为墙钟口径。不要直接把默认汇总百分比当成服务端请求墙钟占比。
- 高频系统调用场景中暂停/恢复成本明显，可能放大原问题。优先短窗口/少量调用类型，必要时用 eBPF 聚合替代。
- 适合“读哪个文件失败”“等待哪个系统调用”“返回哪个 errno”，不擅长用户态纯计算热点、纯用户态锁和内存破坏。

### 10.2 内存/并发检查：ASan、UBSan、LSan、TSan

| 工具 | 机制/作用 | 常用场景 | 局限 |
|---|---|---|---|
| ASan | 编译插桩、shadow memory、分配器辅助 | 越界、use-after-free 等 | 要重编译，有时空成本；不覆盖所有错误 |
| UBSan | 对选定未定义行为插桩检查 | 非法移位、溢出、类型/对齐等 | 检查集合依配置；不是所有 UB 全覆盖 |
| LSan | 按可达性等规则检查泄漏 | 退出/检查时未释放对象 | 可达缓存不一定报告；自定义机制可能漏检 |
| TSan | 编译/运行时追踪内存访问与同步关系 | 数据竞争 | 很大开销，平台/运行时限制；不是通用死锁证明器 |

```bash
# 测试环境编译及运行，不能用其耗时当成生产性能
cc -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined \
    -o /opt/demo/app-asan /opt/demo/app.c
/opt/demo/app-asan

# TSan 独立构建，通常不能与 ASan 同时启用
cc -O1 -g -fsanitize=thread \
    -o /opt/demo/app-tsan /opt/demo/app.c
/opt/demo/app-tsan
```

这类工具倾向于捕获**错误发生位置**，而 core 倾向于捕获**错误造成终止的位置**，因此二者互补。性能分析应先保留真实优化构建，不能把 instrumentation 的开销误认成业务瓶颈。

### 10.3 Valgrind / Massif / Callgrind

Valgrind 通过动态二进制插桩等方式分析程序，通常无需为所有模块重新编译，但保留 debug info 很有帮助。

```bash
valgrind --tool=memcheck --leak-check=full \
    --show-leak-kinds=all --log-file="$OUT/memcheck.txt" \
    /opt/demo/app-debug

valgrind --tool=massif --massif-out-file="$OUT/massif.out" \
    /opt/demo/app-debug
ms_print "$OUT/massif.out"

valgrind --tool=callgrind --callgrind-out-file="$OUT/callgrind.out" \
    /opt/demo/app-debug
```

- Memcheck：非法内存访问、未初始化值使用、泄漏等。
- Massif：堆占用随时间变化，帮助识别峰值和分配来源。
- Callgrind：调用关系/指令成本等；它不是在真实速度下采样硬件 PMU，结果不能当成真实墙钟延迟。
- 通常明显变慢，适合测试和复现，不宜默认附加到生产大流量服务。

### 10.4 日志、应用埋点和持续监控

- `journalctl`/内核日志：服务退出、OOM、设备错误、oops/panic。
- Prometheus + node_exporter：持续主机指标；应用指标和 trace ID 是关联请求链路的关键。
- OpenTelemetry/业务 span：区分请求排队、RPC、数据库、计算、序列化等阶段。
- pprof 等运行时 profiler：为 Go 等提供更贴近运行时的 CPU、heap、block/mutex 视图，需按实际语言配置。

```bash
journalctl -u example.service --since '2026-11-19 00:00:00'
journalctl -k --since '2026-11-19 00:00:00'
```

运行时 block/mutex profile、线程 off-CPU 和 Linux run queue delay 是不同视角，不能直接把它们的数值相加。

### 10.5 网络及块设备专项

- `ss`：TCP 状态、队列、RTT 等 socket 线索。
- `tcpdump`：包级验证，需要严格采集范围，pcap 可能含敏感载荷；不替代请求级 trace。
- `ethtool -S`：驱动/网卡错误计数，字段受驱动影响。
- `blktrace/blkparse`：块层请求事件；层级、设备和开销要明确。
- BCC 的 biolatency/biosnoop/tcpretrans：适合快速定位分布和事件。

### 10.6 内核崩溃：kdump、crash 与 KGDB

**不要把用户 core 与内核 vmcore 混淆。**

| 能力 | 对象 | 工作方式 |
|---|---|---|
| 用户态 core + GDB | 一个终止进程 | 保存该进程允许转储的状态 |
| kdump + vmcore + crash | 崩溃内核/全系统 | 预留 crash kernel，panic 后进入捕获内核收集内存 |
| KGDB 等 | 被控制的运行内核 | 调试入口/远程协议，常有明显停止和系统影响 |

kdump 需要提前配置预留内存、启动参数、捕获服务和存储，并在测试环境验证。vmcore 分析通常需要匹配的 `vmlinux` 调试符号；仅有压缩启动内核镜像通常不够。

不要为了测试收集功能在生产机器上触发 panic，也不要把在线 KGDB 当成无侵入分析。本文只介绍原理和选型，不执行或提供自动触发内核崩溃流程。


---

<a id="s11"></a>
## 11. 多工具联合排障实战

> 以下都是排障方法示例，不是实测结果。目标是建立“症状 → 假设 → 证据 → 验证”的闭环，避免看见一个异常数值就下结论。

### 11.1 场景一：CPU 很高，吞吐量下降

**第一步：确定谁在消耗 CPU。**

```bash
mpstat -P ALL 1 10
pidstat -t -u -p "$PID" 1 10
```

分支判断：

- 单线程满核：串行阶段、热点循环、锁自旋等。
- 多线程用户态满载：计算、序列化、压缩、运行时/GC 等。
- 内核态占比高：频繁 syscall、网络/文件系统、软中断、内核路径等。
- 看似 CPU 空闲但业务被限制：继续查 cpuset/quota 和频率，而不是只看总占用。

**第二步：用 perf 找事件热点和调用栈。**

```bash
perf record -o "$OUT/cpu.data" -F 99 -e cpu-clock \
    -p "$PID" --call-graph dwarf,8192 -- sleep 20
perf report -i "$OUT/cpu.data" --stdio
```

如果热点在业务循环，结合 annotate 与算法检查；如果在 `memcpy`/分配器，查看上层调用者，判断是否重复拷贝和小对象分配；如果在锁自旋，查竞争和持锁者。

**第三步：针对假设采集计数。**

```bash
perf stat -p "$PID" -e '{cycles,instructions}' \
    -e branches,branch-misses -- sleep 10
```

低 IPC 本身不是答案，需要与热点代码和平台事件匹配。若主要是内核路径，改用限定函数的 ftrace 或适当 eBPF 事件，别直接全函数追踪。

**验证**：在相同输入、并发、CPU/cgroup 和构建下比较吞吐量、P99、CPU/请求，以及计数/请求。吞吐上涨但每请求 CPU 更高也可能只是并发变化，不一定是算法更好。

### 11.2 场景二：CPU 不高，P99 很高

这类问题常在 off-CPU，但也可能是单核瓶颈、quota、上游等待或异步队列。

1. 从应用 span 区分排队、RPC、数据库、文件 I/O、锁等待。
2. `pidstat -w`、PSI、cgroup `cpu.stat` 看等待和限流线索。
3. 使用 `perf sched`/ftrace/runqlat 区分“已可运行但没拿到 CPU”与“正在睡眠”。
4. 使用 offcputime 或短时线程栈找等待入口。
5. 使用 syscall/function 延迟直方图，检查长尾是否集中在某一阶段。

| 证据组合 | 更合理的假设 | 还要核对 |
|---|---|---|
| run queue delay 高 | CPU 争用、亲和性、优先级或限流 | 竞争者、quota、IRQ、迁核 |
| off-CPU 高但 run queue delay 小 | 锁、I/O、网络或条件等待 | 等待对象和唤醒来源 |
| 主机空闲且容器 throttling 增长 | CPU bandwidth 配额不足或周期影响 | unit/cgroup 路径、实际 CPU 需求 |
| 单线程满核但总体占用低 | 串行热点 | 线程级 profile、队列长度 |
| 内核等待不明显但请求仍慢 | 应用/外部系统排队、异步链路 | request ID 关联、下游 trace |

**反例**：CPU profile 很干净不能证明程序没问题，因为等待期间没有 CPU 样本。off-CPU 栈落在 futex 也不能证明死锁，worker 正常等任务同样如此。

### 11.3 场景三：文件读写偶发卡顿

```bash
iostat -xz 1 10
pidstat -d -p "$PID" 1 10
```

随后按层次分析：

```text
应用 API/业务阶段耗时
  → syscall 或 uprobe 区间
  → VFS/文件系统路径，缓存/回写/锁/分配
  → 块请求排队和设备完成
  → 设备/阵列/远端存储
```

- eBPF read/write syscall 直方图：确认应用看到的系统调用长尾。
- biolatency/biosnoop：确认块设备层是否同时有慢请求。
- ftrace function_graph：定位文件系统/驱动区间，但受 off-CPU 影响。
- strace：确认 fd、errno、fsync/同步语义，注意参数敏感性和暂停开销。

**解读**：

- syscall 慢而块 I/O 不慢：可能等锁、调度、文件系统元数据、网络文件系统或缓存/回写限制。
- 设备延迟高：检查队列、错误、后台回写、竞争者和存储层。
- 小量逻辑写并不等于小量物理写；缓存和 writeback 可能使事件由内核线程执行。
- `read()` 可以读 socket、pipe 或缓存；不能看到 read 就断定正在读磁盘。
- io_uring、mmap、NFS 等需要对应观测点，不能照搬同步 read 配对。

### 11.4 场景四：内存持续增长

**先确认增长属于哪一类。**

```bash
cat "/proc/$PID/smaps_rollup"
cat "/proc/$PID/status"
cat "/proc/$PID/cgroup"
```

区分：匿名堆、线程栈、共享映射、文件映射、allocator 保留空间、缓存、运行时堆、cgroup 其他进程。

**再选择工具。**

- 原生应用：分配统计/heap profiler → eBPF memleak（窗口内）→ LSan/Valgrind 复现。
- Go/Java 等：优先对应运行时 heap profile/GC 证据，原生 malloc 探针未必覆盖主要堆。
- 只见 RSS 不降：检查 allocator 是否保留空闲 arena、是否存在碎片/缓存，不直接判断泄漏。
- malloc/free 配对未覆盖 realloc、自定义 allocator 或 mmap 时，统计可能不完整。

**验证**：使用固定负载和足够观察时间，检查增长斜率、活对象、分配栈与释放逻辑。短时间“尚未释放”的对象可能仍在正常生命周期内。

### 11.5 场景五：进程偶发 SIGSEGV / SIGABRT

1. 提前保留匹配制品、debug info 和 core 收集策略。
2. 故障后先确认 EXE/时间/信号，导出 core，不覆盖或更新原制品。
3. GDB 查故障线程、崩溃指令、相关寄存器、对象和调用者。
4. 若在 malloc/free、容器操作或解引用处终止，考虑早前堆损坏、UAF 或竞争，但不直接定论。
5. 用同类负载在 ASan/UBSan/TSan 构建中复现，或对已知对象设置有限观察点。

```bash
coredumpctl --no-pager list /opt/demo/app
coredumpctl info 12345
coredumpctl -o "$OUT/app.core" dump 12345
gdb -q -nx /srv/incident/bin/app "$OUT/app.core"
```

`SIGABRT` 常见于断言、显式 abort、运行时/堆检查发现错误；`SIGSEGV` 常见于非法访问。它们都只是终止机制，不是完整根因分类。

### 11.6 场景六：服务卡死，怀疑锁竞争或死锁

- 先看是否仍有 CPU 进展、系统调用进展、请求进展和日志变化。
- 获准后在多个时间点收集简短线程栈；大线程数优先避免 `bt full`。
- 所有线程都等任务队列可能是正常闲置；所有线程等待某任务但生产者消失，才是更有力的异常证据。
- 锁等待长：找持锁者和持锁区间。必要时在测试环境使用用户函数探针/运行时 mutex profile。
- CPU 高：可能是自旋/活锁，不一定是死锁；需要 CPU 样本证明持续执行的路径。
- TSan 检测数据竞争，不保证检测所有死锁；死锁需要等待关系或控制流证明。

**最终证据形式**：线程 A 持锁 X 等锁 Y，线程 B 持锁 Y 等锁 X，或某条件的唯一生产者不可达/已退出。单个 futex 栈不构成这种证明。

### 11.7 场景七：实时任务/周期任务抖动

优先测量：预期触发时间、实际唤醒时间、实际运行时间、任务完成时间。分解：

```text
触发 → 唤醒：定时器/IRQ 等路径延迟
唤醒 → 运行：调度延迟
运行 → 完成：计算、抢占、阻塞及设备处理
```

可结合：ftrace sched + IRQ/softirq + function_graph，perf sched，支持的 osnoise/timerlat/rtla，以及适当的测试基准。

排查因素包括 CPU 亲和性、优先级、IRQ 分布、抢占模式、cgroup、频率/idle state、page fault、内存回收、锁、硬件/固件中断。**不要为验证而自动设置最高实时优先级、关闭 watchdog/中断或修改系统关键策略**，这些必须在隔离测试环境评估。

---

<a id="s12"></a>
## 12. 生产环境采集规范与常见误区

### 12.1 一次采集的标准流程

**采集前**

1. 明确症状、目标 PID/TID/cgroup、业务时间窗和可接受影响。
2. 核对工具/内核版本、CPU、权限、符号、事件是否存在。
3. 检查已有 tracing/BPF 任务，避免覆盖别人配置。
4. 建立受限结果目录；估算缓冲和文件容量；记录默认配置以便恢复。
5. 设定退出方式、时长、停止条件和负责人。

**采集中**

1. 从轻量指标开始，再添加所需事件/栈/参数。
2. 优先目标筛选、低频采样、内核聚合和短窗口。
3. 同时观察采集器 CPU、业务延迟、磁盘与内存占用。
4. 若出现超出预期的延迟、丢样、输出爆炸或资源压力，停止采集。

**采集后**

1. 关闭本次事件/探针，解除 GDB，清理本次新建对象；恢复曾改动的配置。
2. 检查 lost samples、buffer overruns、缩放、符号和时间窗覆盖。
3. 归档原始数据、版本信息、命令、符号、分析假设和数据缺口。
4. 只共享必要数据，按保留策略处理 core、栈和 trace。
5. 改进后回到相同业务基准验证，不只比较一张热点图。

### 12.2 相对影响分级

| 级别 | 典型操作 | 原则 |
|---|---|---|
| 较轻 | 基础指标、有限 perf stat | 仍评估频率、目标和数据量 |
| 中等 | 低频 perf 取栈、少量 tracepoint/eBPF 聚合 | 短窗口，观察 lost/业务影响 |
| 较高 | 高频全机事件、全函数 graph、高频 uprobe/打印、strace | 有针对性地使用，不作为默认方案 |
| 明显暂停/改变运行 | GDB attach、断点、gcore | 只在明确批准的暂停窗口 |
| 显著执行扰动 | Valgrind、重 instrumentation、某些 latency 检测 | 优先测试/复现环境 |

这些是经验性分类，不是开销承诺。事件频率、核数、栈深度、符号化和 map 规模都可能让排序变化。

### 12.3 权限故障的正确处理

```bash
cat /proc/sys/kernel/perf_event_paranoid
cat /proc/sys/kernel/kptr_restrict
cat /proc/sys/kernel/yama/ptrace_scope
cat /proc/sys/kernel/unprivileged_bpf_disabled
```

某些文件可能未编译/未启用而不存在，这是正常差异。`perf_event_paranoid` 的上游语义与发行版扩展可能不同，不写一份“所有发行版统一等级表”。检查时按本机发行版安全文档理解。

- perf：权限、CAP_PERFMON、目标归属、事件范围、PMU 支持。
- ftrace：tracefs 挂载、写权限、lockdown、tracer/配置支持。
- eBPF：capability、LSM/lockdown、BTF/helper、verifier、资源记账。
- GDB/gcore：ptrace、Yama、dumpable、namespace、容器 seccomp。
- core：转储条件、收集器、空间、过滤和保留。

**原则：权限不够就走授权流程，不默认将 paranoid 设为 -1、关闭 Yama/SELinux 或使用 privileged 容器。** 即使能 root，也应按最小权限和批准范围操作。

### 12.4 十八个常见误区

| 误区 | 正确理解 |
|---|---|
| CPU 总占用低就没有 CPU 瓶颈 | 单线程、cpuset/quota、频率都可能限制 |
| load 就是 CPU 利用率 | 包含可运行和不可中断睡眠任务 |
| iowait 就是设备延迟 | 它是 CPU 记账指标，不是设备或请求时间 |
| 缺页都意味着磁盘读 | minor/major fault 含义不同，还要看映射和路径 |
| perf 百分比就是函数调用次数 | 百分比对应选定事件/样本权重 |
| perf CPU profile 能解释全部等待 | 普通 profile 主要观察 on-CPU |
| 火焰图横轴是时间 | 普通 flame graph 横轴是聚合权重，不是时间线 |
| function_graph 时间就是独占 CPU | 可包含子调用、调度/IRQ/睡眠，受选项影响 |
| tracing_on=0 就完全卸载 tracer | 只是停止写 buffer，仍需清理事件/tracer |
| PID 过滤自动覆盖所有线程/唤醒事件 | ftrace 常按 task/TID，wakeup 上下文属于唤醒者 |
| eBPF verifier 通过就统计一定正确 | verifier 不证明观测逻辑/时间配对/单位正确 |
| CO-RE 可兼容任意内核 | 仍要求必要类型、hook、helper 和语义 |
| eBPF 必然零开销且永不丢事件 | 探针/栈/map/输出均有成本和容量限制 |
| GDB attach 只读不会影响业务 | 默认先停止，断点和遍历线程会暂停 |
| 所有 futex 等待都代表死锁 | 可能是正常锁/条件/worker 等待 |
| core 包含一切并能恢复进程 | 它是部分现场快照，不是执行历史或恢复镜像 |
| ulimit unlimited 一定得到完整 core | handler 策略、限制、过滤、空间仍起作用 |
| 崩溃在 libc 就是 libc 出错 | 可能是更早的堆破坏/UAF/竞争造成后果 |

### 12.5 建议的结果记录模板

```text
问题：业务症状、基线、异常时间、影响范围
环境：host/内核/CPU/架构/应用 Build ID/容器及 cgroup
目标：PID、TID、EXE、进程启动时间、request ID
采集：工具版本、完整命令、事件、过滤、频率、时长、clock
质量：丢样/overrun、PMU scaling、符号完整度、窗口边界
证据：热点、等待链、延迟分布、故障指令、退出信号
假设：哪些事实支持，哪些反例或缺口尚未排除
验证：受控改动、相同负载、业务指标、置信度/波动
恢复：探针/实例/调试附着/配置是否已恢复
安全：数据等级、共享范围、保留周期
```

---

<a id="s13"></a>
## 13. 命令速查、术语与参考资料

### 13.1 命令速查

> 先阅读前文的目标、权限、数据量和暂停说明。以下仅用于查找，不是要全部执行的脚本；`$PID/$OUT` 需事先确认和创建。

| 目标 | 命令形态 |
|---|---|
| 线程 CPU | `pidstat -t -u -p "$PID" 1 10` |
| 上下文切换 | `pidstat -w -p "$PID" 1 10` |
| 磁盘概况 | `iostat -xz 1 10` |
| 进程内存 | `cat "/proc/$PID/smaps_rollup"` |
| CPU 计数 | `perf stat -p "$PID" -e '{cycles,instructions}' -- sleep 10` |
| 进程 CPU 样本 | `perf record -p "$PID" -F 99 -e cpu-clock --call-graph dwarf,8192 -o "$OUT/perf.data" -- sleep 20` |
| 热点报告 | `perf report -i "$OUT/perf.data"` |
| 指令热点 | `perf annotate -i "$OUT/perf.data" -s hot_function` |
| 调度录制 | `sudo perf sched record -o "$OUT/sched.data" -- sleep 10` |
| 调度时间线 | `perf sched timehist -i "$OUT/sched.data" -p "$PID"` |
| 列出 ftrace 事件 | `cat /sys/kernel/tracing/available_events` |
| ftrace 录制前端 | `sudo trace-cmd record -o "$OUT/trace.dat" -e sched:sched_switch -- sleep 10` |
| 列出 BPF 探针 | `sudo bpftrace -l 'tracepoint:syscalls:*'` |
| 查事件字段 | `sudo bpftrace -lv 'tracepoint:syscalls:sys_exit_read'` |
| 查 BPF 对象 | `sudo bpftool prog show` |
| GDB 启动 | `gdb --args /opt/demo/app-debug` |
| GDB 在线栈 | `sudo gdb -p "$PID"` 后 `thread apply all bt`，结束 `detach` |
| core 列表 | `coredumpctl list /opt/demo/app` |
| core 元数据 | `coredumpctl info 12345` |
| core 导出 | `coredumpctl -o "$OUT/app.core" dump 12345` |
| core 调试 | `gdb /srv/incident/bin/app "$OUT/app.core"` |

### 13.2 术语表

| 术语 | 含义 |
|---|---|
| PID / TGID / TID | 用户所说进程 PID 常对应线程组 TGID；每个 Linux task 有 TID |
| PMU | Performance Monitoring Unit，硬件性能监控单元 |
| IPC | Instructions Per Cycle，指令数/周期数，需匹配计数范围 |
| tracepoint | 预定义的结构化内核事件点 |
| kprobe / uprobe | 内核/用户态动态探针 |
| USDT | 应用定义的用户态静态探针 |
| ring buffer | 有限容量循环缓冲区，可能覆盖或丢数据 |
| BTF | BPF Type Format，BPF 相关类型信息 |
| CO-RE | 根据目标 BTF 等做加载期适配的可移植 BPF 机制 |
| DWARF / CFI | 调试信息格式 / Call Frame Information，帮助源码定位或栈展开 |
| Build ID | 标识二进制制品的构建标识，匹配符号/core 的重要依据 |
| ptrace | 进程调试/追踪相关 Linux 接口 |
| core / vmcore | 用户态进程现场 / 内核崩溃内存现场，不能混同 |
| on-CPU / off-CPU | 正在运行 / 没在 CPU 上运行，后者包含多类等待 |
| skid | 事件触发与记录到的指令位置之间可能存在偏移 |
| multiplexing | PMU 计数资源不足时的事件轮换与缩放 |

### 13.3 权威参考与版本核对入口

以下参考用于核对机制和参数。在线 upstream 文档可能比目标发行版新，**落地时以目标内核、工具帮助、man page 和发行版安全配置为准**；不能用新文档中的选项证明旧环境一定支持。

1. Linux 内核：ftrace 官方说明 — https://docs.kernel.org/trace/ftrace.html
2. Linux 内核：eBPF verifier — https://docs.kernel.org/bpf/verifier.html
3. Linux 内核：libbpf 生命周期与 CO-RE — https://docs.kernel.org/bpf/libbpf/libbpf_overview.html
4. Linux man-pages：perf_event_open(2) — https://man7.org/linux/man-pages/man2/perf_event_open.2.html
5. perf record 手册 — https://man7.org/linux/man-pages/man1/perf-record.1.html
6. perf stat 手册 — https://man7.org/linux/man-pages/man1/perf-stat.1.html
7. GDB：附着进程 — https://sourceware.org/gdb/current/onlinedocs/gdb.html/Attach.html
8. GDB：观察点 — https://sourceware.org/gdb/current/onlinedocs/gdb.html/Set-Watchpoints.html
9. GDB：文件、core 与共享库 — https://sourceware.org/gdb/current/onlinedocs/gdb.html/Files.html
10. Linux core(5) — https://man7.org/linux/man-pages/man5/core.5.html
11. systemd coredumpctl(1) — https://man7.org/linux/man-pages/man1/coredumpctl.1.html
12. systemd coredump.conf(5) — https://man7.org/linux/man-pages/man5/coredump.conf.5.html
13. bpftrace 0.24.2 项目说明镜像，帮助核对示例与语法差异 — https://pkg.go.dev/github.com/bpftrace/bpftrace@v0.24.2
14. eBPF 社区文档：循环与 verifier 的版本差异 — https://docs.ebpf.io/linux/concepts/loops/

bpftrace 官方站点本次访问未成功，因此未把不可访问页面当成已经核对的依据；语言示例结合项目公开说明与目标版本检查方式编写。部分 verifier 入门页含历史阶段描述，循环等演进特性应结合更新资料和实际 kernel 功能检查，不能逐句照搬为所有新内核的限制。

### 13.4 最后总结

- **先监控**，知道异常在哪里；**再 profiling**，知道成本在哪里。
- **用 tracing 建立时序和等待链**，验证“为什么慢”。
- **用 GDB/core 检查具体状态**，验证“为什么错”。
- **用复现和专门检查器追到错误最初发生点**，而不止停在崩溃后果。
- 所有结论都要带上目标、时间窗、事件口径、数据质量与版本约束；最终用业务指标验证改进。

