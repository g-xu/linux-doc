# Linux 性能分析与调试实战手册

> 面向具备 Linux 内核基础的工程师：从现象、假设和证据出发，掌握 perf、ftrace、eBPF、GDB、用户态 coredump 和内核 vmcore 的使用方法。
>
> 编写日期：2026-11-19。本文不是特定发行版的安装手册；示例以 Bash、原生 Linux、C/C++ 用户态程序为主要环境。内核、工具版本和安全策略会改变可用功能，执行前应完成第 3 章的环境检查。
>
> 本文中的解释性输出均为教学示意，不代表某台机器的实测结果。配套程序及脚本的本机验证范围见附录 D。生产采集必须先确定授权、资源预算、数据保密要求和停止条件。

## 目录

1. [分析方法：先问问题，再选工具](#method)
2. [工具地图与选择依据](#tool-map)
3. [环境、权限、符号与公共基础](#environment)
4. [系统级初筛：建立最小证据集](#triage)
5. [perf：从计数到热点与调用栈](#perf)
6. [ftrace：内核事件、函数路径与时序](#ftrace)
7. [eBPF：构建针对具体问题的观测](#ebpf)
8. [GDB：在线执行状态与代码假设](#gdb)
9. [用户态 coredump：保存与分析崩溃现场](#coredump)
10. [内核故障：Oops、panic、kdump 与 vmcore](#kernel-debug)
11. [综合案例：把工具串成证据链](#cases)
12. [生产使用规范与分析报告](#production)
13. [附录：实验、速查、术语与验证范围](#appendices)
14. [官方资料与参考索引](#references)

<a id="method"></a>
## 1. 分析方法：先问问题，再选工具

### 1.1 性能问题与正确性问题

性能问题的核心是“某项工作消耗了多少资源，或者为什么没有及时完成”；正确性问题的核心是“程序为何进入了不应出现的状态”。两者可能交织，例如锁使用错误既可能导致崩溃，也可能导致长时间等待。

开始调查前，至少明确以下问题：

- **什么变差了**：吞吐、平均延迟、P99、CPU 使用率、内存占用，还是错误率？
- **比较对象是什么**：同一服务历史基线、另一台机器，还是某个版本？
- **问题发生在哪个范围**：整机、容器、进程、线程、单个请求、某段代码？
- **负载是否相同**：请求规模、数据大小、并发、输入分布和预热状态是否一致？
- **故障是否仍在发生**：能否在线采集？是否只有日志、core 或 vmcore？
- **允许什么操作**：能否提权、附加调试器、安装工具、重启或复现？

没有明确范围的“系统很慢”，不能直接转化成有意义的采集命令。

### 1.2 五种观测方式

| 方式 | 典型问题 | 数据形态 | 代表工具 |
|---|---|---|---|
| 监控 | 当前资源状态如何？变化趋势如何？ | 定期读取的指标 | top、vmstat、pidstat、iostat、PSI |
| 计数 | 一段时间内发生了多少次？ | 聚合计数与比率 | perf stat |
| 采样 | 执行成本大致集中在哪里？ | 部分时刻的 IP、调用栈、事件权重 | perf record、eBPF profile |
| 追踪 | 什么事件在什么时间发生？ | 带时间戳的事件或聚合结果 | ftrace、trace-cmd、eBPF |
| 调试 | 此刻变量、寄存器、线程是什么状态？ | 停止状态或保存的现场 | GDB、core、crash、drgn |

这不是互斥分类：perf 也能记录 tracepoint，eBPF 也能计数、采样和追踪。

### 1.3 用时间模型组织假设

对于一个简化的同步请求，可以用以下模型帮助思考：

```text
端到端墙钟时间
  = 用户态执行
  + 内核态执行
  + 已就绪但尚未获得 CPU 的时间
  + 阻塞等待时间
  + 其他阶段的排队与交互时间
```

这不是可以随意相加所有监控值的公式。并行任务、异步 I/O、跨进程与跨机器请求存在重叠，必须沿请求的关键路径计算。

重要区别：

- **on-CPU**：线程正在 CPU 上执行。普通 CPU 热点采样主要回答这一部分。
- **off-CPU**：线程不在 CPU 上，包括可运行但未调度、阻塞等待等不同状态。
- **调度延迟**：通常关注从就绪或成功唤醒到真正运行的间隔。
- **阻塞时长**：等待锁、I/O、条件变量、定时器或其他事件的时间。
- **用户可见延迟**：请求的端到端时间，不能仅由一个线程的 CPU 时间推导。

### 1.4 标准调查闭环

1. 明确症状和可比较的基线。
2. 用低开销指标定位资源与范围。
3. 列出两三个可被证伪的候选原因。
4. 选择能够区分这些原因的观测点。
5. 限定 PID/TID、CPU、时间窗与事件数量后采集。
6. 先检查数据质量，再解释输出。
7. 用第二类证据验证结论。
8. 在相同负载下复测，并记录副作用。

例如，“P99 高是因为 CPU 饱和”应能够被以下证据支持或否定：CPU 忙碌情况、运行队列、目标线程的就绪等待时间、容器 CPU 限额和请求阶段耗时。

<a id="tool-map"></a>
## 2. 工具地图与选择依据

### 2.1 每个核心工具回答什么

| 工具 | 最擅长的问题 | 核心机制 | 主要局限 |
|---|---|---|---|
| perf | 谁消耗 CPU？事件发生了多少次？ | perf_events、PMU、软件事件、采样缓冲区 | 普通 CPU profile 不能直接解释阻塞时间；硬件事件依赖平台 |
| ftrace | 内核执行路径、事件顺序、函数持续时间如何？ | tracepoint、函数插桩、每 CPU 环形缓冲区 | 大范围函数追踪可能扰动明显；函数名与可追踪范围随版本变化 |
| eBPF | 如何关联事件、统计分布并只输出目标数据？ | verifier、BPF 程序、挂载点、map、用户态输出 | 依赖内核功能与权限；探针语义、状态管理和丢失需要自己负责 |
| GDB | 线程为何停在这里？变量和寄存器是什么？ | ptrace/目标调试接口、断点、DWARF | 在线停止会改变程序行为；优化、缺少符号或栈损坏会限制分析 |
| coredump | 崩溃时进程现场如何？ | 内核保存 ELF core 或传给收集器 | 只有部分瞬时状态，没有完整历史；需要匹配的二进制和库 |
| kdump/vmcore | 内核崩溃时机器现场如何？ | kexec 捕获内核、预留内存、vmcore | 必须事先配置；捕获成功与完整性要通过演练验证 |

机制参考：perf [R1][R2]，ftrace [R3][R4]，eBPF/libbpf [R5]，GDB [R8][R9]，core [R10]，kdump [R13]。

### 2.2 按症状选入口

| 现象 | 第一轮 | 第二轮 | 需要避免的误判 |
|---|---|---|---|
| CPU 高、吞吐低 | mpstat、pidstat、perf stat | perf record/report/annotate | 把高 CPU 当作唯一原因；忽略输入规模与有效工作量 |
| CPU 不高、请求慢 | pidstat、线程状态、PSI | off-CPU、调度事件、应用阶段追踪 | 只做 on-CPU 火焰图 |
| 单线程接近满核 | 线程级 CPU、affinity | 目标线程热点、串行路径 | 用整机平均 CPU 掩盖单核瓶颈 |
| I/O 慢 | iostat、pidstat -d、应用延迟 | read/fsync 延迟、块 I/O 追踪 | 把 read 耗时等同于设备耗时 |
| RSS 持续增长 | smaps_rollup、映射分类 | 分配/释放追踪、泄漏工具 | RSS 增长直接等于泄漏 |
| 多线程扩展性差 | 单/多线程吞吐比较 | 锁等待、调度、缓存共享分析 | 所有锁都用 futex；所有扩展性问题都由锁造成 |
| 进程崩溃 | 日志、信号、core 是否存在 | GDB 离线分析 | 认为崩溃栈顶一定是最早犯错的位置 |
| 进程卡死 | 全线程栈、线程状态 | 锁对象、等待条件、连续快照 | 一个 futex 栈就证明死锁 |
| 内核 panic | 日志、kdump 状态 | crash/drgn 分析 vmcore | 用普通用户态 GDB 流程代替内核分析 |

<a id="environment"></a>
## 3. 环境、权限、符号与公共基础

### 3.1 先记录环境

以下命令只读取环境；某个文件不存在时，应记录为“不支持或未提供”，而不是立即修改系统。

```bash
uname -a
cat /etc/os-release
getconf LONG_BIT
lscpu
perf --version
gdb --version
bpftrace --version
sysctl kernel.perf_event_paranoid
sysctl kernel.kptr_restrict
sysctl kernel.yama.ptrace_scope
sysctl kernel.unprivileged_bpf_disabled
cat /sys/kernel/security/lockdown
```

同时记录容器/宿主机关系、CPU 配额、cpuset、应用版本、构建 ID、采集时间、负载和部署变更。

### 3.2 安装思路

Ubuntu/Debian 的基础包示例：

```bash
sudo apt update
sudo apt install build-essential gdb sysstat strace procps trace-cmd bpftrace
```

perf 包名因发行版而异。Debian 通常使用 `linux-perf`；Ubuntu 原生通用内核通常使用 `linux-tools-common` 和与运行内核对应的 `linux-tools-$(uname -r)`。**分别根据本机发行版确认后安装，不要把两套包名混在一起执行。**

Fedora/RHEL 系可查找 `perf`、`gdb`、`sysstat`、`trace-cmd`、`bpftrace`、`bcc-tools`；调试符号包通常需要额外仓库。自编译内核、云厂商内核和 WSL 内核不一定有同名工具包。

实践原则：

- 优先使用与运行内核配套的 perf；版本不同不一定完全不能使用，但事件、数据格式和新功能需要逐项检查。
- BCC 工具可能名为 `runqlat` 或 `runqlat-bpfcc`，可能位于 `/usr/share/bcc/tools/`。
- 不要为了让一个采集命令成功，直接关闭全机安全控制。
- 首次使用目标工具时运行 `--help`，确认参数是否与本文一致。

### 3.3 内核功能检查

```bash
if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz | grep -E \
      'CONFIG_(PERF_EVENTS|FTRACE|FUNCTION_TRACER|FUNCTION_GRAPH_TRACER|DYNAMIC_FTRACE|KPROBES|UPROBES|BPF|BPF_SYSCALL|BPF_JIT|DEBUG_INFO_BTF|COREDUMP|KEXEC|KEXEC_CORE|CRASH_DUMP|PROC_VMCORE)='
elif [ -r "/boot/config-$(uname -r)" ]; then
    grep -E \
      'CONFIG_(PERF_EVENTS|FTRACE|FUNCTION_TRACER|FUNCTION_GRAPH_TRACER|DYNAMIC_FTRACE|KPROBES|UPROBES|BPF|BPF_SYSCALL|BPF_JIT|DEBUG_INFO_BTF|COREDUMP|KEXEC|KEXEC_CORE|CRASH_DUMP|PROC_VMCORE)=' \
      "/boot/config-$(uname -r)"
else
    echo "Kernel config is not exposed; inspect runtime features separately."
fi
```

这是排查清单，不是“所有工具都必须同时开启以上配置”。例如不同 BPF 挂载点需要不同支持，BTF 对 CO-RE 很重要，但不是所有简单 BPF 程序的绝对前提。

运行时检查往往更直接：

```bash
perf list
ls /sys/kernel/tracing
cat /sys/kernel/tracing/available_tracers
test -r /sys/kernel/btf/vmlinux && echo "Kernel BTF is available"
sudo bpftrace --info
sudo bpftrace -l 'tracepoint:sched:*'
```

tracefs 也可能位于 `/sys/kernel/debug/tracing`。没有挂载时，由管理员确认后挂载；本文的脚本不自动挂载或修改全局配置。

### 3.4 权限不是单个 sysctl

perf 的访问控制受到 `perf_event_paranoid`、capability、目标范围、内核版本和发行版补丁影响。上游常见含义如下：[R1][R2]

| 值 | 上游常见限制 |
|---|---|
| -1 | 对非特权 perf 使用限制最少，并放宽相关锁页限制 |
| 0 | 允许较广范围，仍限制非特权 raw tracepoint/ftrace function 事件 |
| 1 | 进一步限制非特权系统级、CPU 范围观测 |
| 2 | 进一步限制非特权内核态观测，主要保留进程级用户态观测 |
| 3、4 或更高 | 一些发行版增加更严格策略；不能只用上游表格解释 |

`CAP_PERFMON` 自 Linux 5.8 引入，是 perf 权限管理的重要机制。不要因此认为它也自动授予所有 BPF、ptrace 或内核符号访问权限。[R1][R2]

GDB attach 还可能受到 Yama、进程 dumpable 状态、UID、`CAP_SYS_PTRACE`、PID namespace 和 seccomp/LSM 等因素限制。优先调试自己启动的实验程序，或申请明确的目标调试权限。[R8][R14]

### 3.5 符号、调试信息与调用栈

必须区分三件事：

| 内容 | 用途 | 常见来源 |
|---|---|---|
| 符号表 | 将地址映射为函数名 | ELF `.symtab`/`.dynsym`、内核符号 |
| DWARF 调试信息 | 源码行、变量、类型，部分展开信息 | 未 strip 二进制、独立 debug 文件 |
| 栈展开依据 | 找到调用者 | frame pointer、DWARF CFI、内核 ORC 等 |

`-g` 不等于“任意情况下都能得到完整栈”。某些 frame-pointer 模式需要编译时保留帧指针；DWARF 模式也有栈转储大小和展开能力限制。[R7][R9]

实验建议：

```bash
gcc -O2 -g3 -fno-omit-frame-pointer -fno-optimize-sibling-calls \
    -pthread -Wall -Wextra -o perf_debug_lab examples/perf_debug_lab.c
file ./perf_debug_lab
readelf -n ./perf_debug_lab
nm -an ./perf_debug_lab | grep -E 'burn_cpu|process_task|crash_now'
```

选择 `-O2 -g` 是为了观察接近优化程序的行为；逐语句调试可另编译 `-Og -g` 版本。**不要用改变优化等级后的性能结论直接替代线上结论。**

生产分析应保存匹配的可执行文件、动态库、独立调试信息和构建 ID。内核分析应保存匹配的 `vmlinux` 与模块调试信息；`vmlinuz` 压缩启动镜像通常不是分析工具需要的完整调试文件。

### 3.6 PID、TID、namespace 与 cgroup

Linux 用户态常说的 PID 通常是线程组 ID；每个线程另有 TID。调度事件中的 `prev_pid`、`next_pid`、唤醒事件的 `pid` 通常指被调度任务的 TID。

```bash
PID=12345  # 替换为已确认的目标进程
ps -L -p "$PID" -o pid,tid,psr,stat,pcpu,comm
ls "/proc/$PID/task"
cat "/proc/$PID/status"
cat "/proc/$PID/cgroup"
```

宿主机探针看到的任务标识与容器内 `ps` 显示的 PID 可能不同。不要把容器内数字原样代入宿主机采集而不核对；还应确认进程是否重启、PID 是否复用。

### 3.7 虚拟化与容器边界

- 容器共享宿主机内核；观测权限、内核事件和符号最终受宿主机控制。
- 虚拟机的 PMU 可能没有透传，某些硬件事件返回不支持。可以尝试软件事件，但它们不是硬件事件的等价替代。
- CPU steal、虚拟磁盘与宿主机调度会影响结果。
- WSL 适合编译和部分实验，不代表能完整复现原生服务器上的 PMU、kdump、设备 I/O 和内核调试行为。

<a id="triage"></a>
## 4. 系统级初筛：建立最小证据集

### 4.1 十秒钟观察窗口

```bash
uptime
vmstat 1 10
mpstat -P ALL 1 10
iostat -xz 1 10
pidstat -u -r -d -w -p "$PID" 1 10
pidstat -t -u -w -p "$PID" 1 10
```

部分工具第一次输出是启动以来的平均值，应重点看后续区间。`%CPU` 是否可超过 100%、是否按 CPU 数归一化，要看工具和模式；多线程进程与整机平均值不能直接比较。

观察重点：

| 维度 | 重点 | 初步假设 |
|---|---|---|
| CPU | user/system、单核分布、steal、运行队列 | 计算、内核工作、单核瓶颈或虚拟化影响 |
| 调度 | 上下文切换、线程状态、CPU 配额 | 等待、抢占、过度并发或限额 |
| 内存 | RSS、major fault、swap、reclaim 压力 | 工作集增长、换页或回收影响 |
| I/O | 吞吐、队列、await、应用 I/O | 同步写、设备排队或上层等待 |
| 网络 | 重传、连接状态、丢包与错误 | 网络、对端或应用消费能力 |

load average 包含可运行任务与某些不可中断等待任务，不能直接等同于 CPU 使用率。`iowait` 也不是某个目标进程的 I/O 等待时间。

### 4.2 PSI 与容器限额

PSI（Pressure Stall Information）反映任务因 CPU、内存或 I/O 资源争用而发生的停顿压力；它不是资源利用率的同义词。字段和支持范围按实际内核检查。[R15]

```bash
cat /proc/pressure/cpu
cat /proc/pressure/memory
cat /proc/pressure/io
```

cgroup v2 的目标 cgroup 中可进一步检查：

```bash
# 替换为目标 cgroup 目录；目录名可能由 systemd/容器运行时生成。
CG=/sys/fs/cgroup/your-target-cgroup
cat "$CG/cpu.max"
cat "$CG/cpu.stat"
cat "$CG/cpuset.cpus.effective"
cat "$CG/memory.current"
cat "$CG/memory.events"
```

比较同一时间窗内 `nr_throttled`、`throttled_usec` 等计数的增量，而不只看累计值。机器有空闲 CPU 不代表目标 cgroup 可以使用这些 CPU。

### 4.3 内存分类

```bash
cat "/proc/$PID/smaps_rollup"
grep -E 'VmRSS|VmSize|RssAnon|RssFile|RssShmem|Threads' "/proc/$PID/status"
pmap -x "$PID"
```

区分匿名内存、文件映射、共享内存、线程栈和内存分配器保留空间。持续增长可能来自正常缓存、碎片、未归还给 OS 的 arena、映射增长，也可能是真正泄漏。

### 4.4 网络补充

```bash
ss -s
ss -tinp
ip -s link
nstat -az
```

这些数据应与请求时间窗、连接和目标进程关联。机器级累计重传计数不能单独证明某个请求慢的原因；深入 TCP 路径时，再选择相应 tracepoint/eBPF 工具。

<a id="perf"></a>
## 5. perf：从计数到热点与调用栈

### 5.1 原理与数据路径

```text
perf 命令
  -> perf_event_open() 创建事件
  -> 硬件 PMU / 软件事件 / tracepoint 等事件源
  -> 计数器读取，或按采样条件写入 mmap 缓冲区
  -> perf.data
  -> report / script / annotate 等离线解析
```

硬件事件包括 cycles、instructions 等；软件事件包括 cpu-clock、task-clock、page-faults、context-switches 等；tracepoint 表示预定义事件。具体支持必须查 `perf list`。[R1][R6][R7]

计数模式关注总量；采样模式关注事件达到采样条件时的 IP、调用栈和附加数据。硬件采样可能有 skid，即记录的 IP 与真正触发事件的位置存在偏移；精确事件是否可用取决于 CPU、事件和配置，不应盲目给所有事件添加 `:p`。[R1]

### 5.2 perf stat：先测整体特征

对自己启动的实验进程：

```bash
perf stat -e task-clock,context-switches,cpu-migrations,page-faults \
    -- ./perf_debug_lab cpu 10 1

perf stat -e cycles,instructions,branches,branch-misses \
    -- ./perf_debug_lab cpu 10 1
```

对已有进程采集十秒：

```bash
perf stat -p "$PID" \
    -e task-clock,context-switches,cpu-migrations,page-faults \
    -- sleep 10
```

如权限不足，在获得授权后使用 `sudo perf ...`，或按本机策略配置最小所需权限。对自己程序的用户态采样可显式使用 `cycles:u`；软件事件并不保证在任意受限环境都可用。

核心判读：

| 指标 | 含义 | 不能单独得出的结论 |
|---|---|---|
| task-clock | 目标获得 CPU 执行的累计时间 | 端到端请求耗时 |
| context-switches | 范围内上下文切换次数 | 每次切换都是性能问题 |
| cpu-migrations | 任务在 CPU 间迁移次数 | 存在严重缓存损失 |
| page-faults | 缺页事件计数 | 都发生了磁盘 I/O |
| instructions/cycles | 在相应测量条件下计算的 IPC | 不同 CPU、不同程序可用同一阈值判优劣 |
| branch-misses/branches | 分支预测失败比例 | 唯一瓶颈是分支 |

例如 task-clock 为约 10 秒、墙钟也约 10 秒，可理解为目标在窗口中平均使用约一个逻辑 CPU；多线程的 task-clock 可以大于墙钟时间。

不要把 generic `cache-misses` 自动解释为某个指定层级缓存的 miss。事件映射和语义应查本机 CPU 的 PMU 文档。

### 5.3 事件复用与统计误差

硬件计数器数量有限。请求过多事件时，内核可能轮换运行事件，并根据 `time_enabled/time_running` 缩放计数。[R1]

概念上：

```text
估算总计数 ≈ 实际读取计数 × time_enabled / time_running
```

如果有效运行比例很低，短时或阶段性负载的结果可能不可靠。处理方法：

- 减少一次测量的事件数量。
- 对相同负载多轮测量。
- 在需要同时比较的事件间考虑 event group，但接受 group 可能无法调度。
- 使用足够长且稳定的采集窗口。
- 检查 `<not counted>`、`<not supported>` 和工具报告的运行比例。

对稳定实验可以重复测量：

```bash
perf stat -r 5 -e cycles,instructions -- ./perf_debug_lab cpu 5 1
```

### 5.4 perf record/report：定位 on-CPU 热点

```bash
perf record -o cpu.data -F 99 -e cycles:u -g --call-graph fp \
    -- ./perf_debug_lab cpu 10 1

perf report -i cpu.data --stdio --no-children
perf report -i cpu.data --stdio --children
```

如 PMU 不可用，可尝试：

```bash
perf record -o cpu-clock.data -F 99 -e cpu-clock -g --call-graph fp \
    -- ./perf_debug_lab cpu 10 1
```

软件 CPU 时钟采样与 cycles 采样的权重含义不同，不能把两个 profile 当成同一种度量直接比较。

对已有进程：

```bash
sudo perf record -o target.data -F 99 -e cycles -g --call-graph fp \
    -p "$PID" -- sleep 10
```

`99 Hz` 是一个保守实验起点，不是生产安全保证；实际样本量、线程数、事件与栈展开决定开销。

### 5.5 Self、Children、样本与分母

典型 report 示意：

```text
Self    Command          Shared Object      Symbol
72.00%  perf_debug_lab   perf_debug_lab     burn_cpu
18.00%  perf_debug_lab   libc.so.6          ...
```

这表示在当前事件、范围和聚合口径下，`burn_cpu` 承担较大事件权重。它不表示某个请求有 72% 的墙钟时间消耗在该函数。

- **Self**：样本落在该函数自身对应位置的贡献。
- **Children**：根据调用链累积子调用的贡献；同一条栈可贡献给多个祖先，不能把各行 Children 简单相加。
- 默认百分比通常依据事件 period 权重而非仅按样本条数计算；筛选方式和版本会改变分母或可用统计选项。[R16]

判断热点时，必须说明事件是 cycles、cpu-clock 还是其他事件，采集范围是否包括内核态，以及报告是否经过筛选。

### 5.6 调用栈选择

| 模式 | 适用情况 | 主要成本/限制 |
|---|---|---|
| `fp` | 目标保留帧指针 | 开销通常较低；无帧指针时可能不完整或错误 |
| `dwarf` | 有可靠展开信息且 perf 支持相应库 | 需采集用户栈并离线展开，数据量更大 |
| `lbr` | 支持且适合使用 LBR 的硬件平台 | 深度和平台限制，不能等同于通用完整栈 |

DWARF 示例：

```bash
sudo perf record -o dwarf.data -F 99 -e cycles:u \
    --call-graph dwarf,16384 -p "$PID" -- sleep 10
```

`16384` 是用户栈转储字节数，不是栈深度。增大它可能改善截断，也增加数据量；不是越大越好。内核栈展开由内核能力负责，不能简单认为用户态 `dwarf` 会解决所有内核栈问题。[R7]

### 5.7 annotate、script 与火焰图

```bash
perf annotate -i cpu.data --stdio --symbol burn_cpu
perf script -i cpu.data > cpu.perf-script.txt
perf buildid-list -i cpu.data
```

`annotate` 将采样位置与反汇编/源码关联；采样偏移、编译器重排与内联都可能影响逐行解释。

火焰图是一种调用栈聚合可视化：宽度通常表示所选度量的权重，不是时间轴，也不是调用先后。普通 on-CPU 火焰图不会把所有阻塞等待显示成宽条。可用 FlameGraph 工具链转换 `perf script`，但必须确认折叠脚本、事件类型和权重口径。[R17]

### 5.8 调度与锁分析扩展

```bash
sudo perf sched record -a -- sleep 5
sudo perf sched timehist
```

这是短时系统级采集示例，可能记录大量数据。执行前用 `perf sched --help` 和子命令帮助确认当前版本的过滤选项；应优先缩小范围。

`perf lock` 适用于对应内核锁事件或特定版本提供的 contention 观测能力，**不能当成任意 pthread mutex 的通用统计器**。用户态互斥锁应结合 pthread/futex 探针、应用埋点与全线程栈分析。

### 5.9 perf 常见故障

| 现象 | 检查方向 |
|---|---|
| Permission denied | paranoid、目标范围、capability、LSM、容器权限 |
| cycles 不支持 | PMU、虚拟化透传、事件名称、CPU 平台 |
| `[unknown]` | 符号、构建 ID、二进制/库路径、内核地址权限 |
| 栈只有一两层 | fp 编译选项、DWARF、栈大小、展开限制 |
| lost samples/records | 事件率、缓冲区、CPU 压力、采集器吞吐 |
| CPU 高但报告中无明显热点 | 时间窗、线程范围、内核/用户态选择、被采集事件 |
| CPU 不高但请求很慢 | 转向 off-CPU、调度和应用阶段，而不是盲目提高频率 |

普通 perf 命令退出后一般关闭对应事件，但 pinned BPF 对象等扩展功能应另外核对。文件应保存在受限目录，采集结束后检查大小和归属。

<a id="ftrace"></a>
## 6. ftrace：内核事件、函数路径与时序

### 6.1 原理与组成

ftrace 是内核 tracing 基础设施的一部分，不仅是一个“记录函数名”的工具。[R3][R4]

```text
静态 tracepoint / 可追踪函数入口与返回
  -> 内核 tracing 基础设施
  -> 当前 tracing instance 的每 CPU 环形缓冲区
  -> trace / trace_pipe / trace-cmd
```

动态 ftrace 在支持的架构上利用编译器生成的函数追踪位置，启停对应插桩路径。不是每个函数都可追踪：内联函数、`notrace` 函数和其他被排除范围可能不出现。

核心接口：

| 文件/目录 | 作用 |
|---|---|
| `available_tracers` | 当前支持的 tracer |
| `current_tracer` | 选择 nop、function、function_graph 等 |
| `tracing_on` | 控制缓冲记录开关；不等于撤销全部探针和过滤配置 |
| `events/` | 事件、字段格式、过滤与启用 |
| `set_ftrace_filter` | 限定函数追踪集合 |
| `set_ftrace_pid` | 函数追踪任务过滤 |
| `set_graph_function` | function_graph 的入口根函数 |
| `trace_clock` | 时间戳来源 |
| `buffer_size_kb` | 每 CPU 缓冲区大小 |
| `trace` | 非消费式文本读取；分析前通常先停止记录 |
| `trace_pipe` | 消费式实时读取；通常阻塞等待新数据 |
| `instances/` | 独立缓冲、部分配置与事件范围 |

instance 有助于避免覆盖别人正在进行的追踪，但不保证所有设置和资源完全隔离；不同内核的 tracer/option 支持仍要核对。

### 6.2 优先用 tracepoint 验证假设

```bash
TRACEFS=/sys/kernel/tracing
cat "$TRACEFS/events/sched/sched_switch/format"
cat "$TRACEFS/events/sched/sched_wakeup/format"
cat "$TRACEFS/available_tracers"
```

先看 `format`，确认字段，再写过滤表达式。事件字段和函数名不是靠猜出来的。tracepoint 通常比任意内核函数名更适合作为长期观测入口，但也不应假设所有事件字段跨版本永远不变。[R4]

### 6.3 最小调度实验：配套安全边界脚本

启动目标，再采集启动时已存在的全部线程：

```bash
./perf_debug_lab lock 30 4 &
PID=$!
sleep 1
sudo bash examples/capture_sched.sh "$PID" 5 "$PWD/sched.trace"
wait "$PID"
```

配套 [capture_sched.sh](examples/capture_sched.sh) 会：

1. 检查 root、目标进程、持续时间和输出文件。
2. 创建专用 instance，不清空全局 tracing 配置。
3. 选择可用的跨 CPU 时钟 `mono` 或 `global`。
4. 枚举当前 TID，对 `sched_switch` 的 `prev_pid/next_pid` 与 `sched_wakeup` 的目标 `pid` 分别过滤。
5. 采集结束后停止、保存 trace 和每 CPU stats。
6. 正常退出或收到可处理的中断时移除 instance。

**限制**：启动后新增线程不在过滤集合中；SIGKILL/断电不会执行清理；热插拔、退出竞争、缓冲覆盖、跨窗口事件都会影响完整性。PID 必须是当前采集内核视角下的标识。

### 6.4 set_event_pid 与事件字段过滤的区别

一般事件的任务过滤与“事件字段描述的目标任务”不是完全相同的概念；现代内核对 `sched_switch`、`sched_wakeup` 等调度事件还有特殊的 PID 匹配处理。因此，不能笼统断言 `set_event_pid` 一定会漏掉目标线程的唤醒，也不能把其行为推广到任意事件。[R3]

例如线程 A 唤醒线程 B，事件可能在 A 的上下文中记录，而事件的 `pid` 字段描述 B。使用前应查本机文档，并确认过滤依据是当前任务、目标字段，还是调度事件的特殊处理。

配套脚本直接使用事件字段，显式限定想观察的关系：

```text
sched_switch: prev_pid == TID 或 next_pid == TID
sched_wakeup: pid == TID
```

进程有多个线程时，应覆盖所需 TID；不能只填线程组主线程 PID 就声称捕获了整个进程。

### 6.5 调度事件如何判读

教学时间线：

```text
12.000000  sched_switch: target(4201) -> other, prev_state=S
12.050000  sched_wakeup: pid=4201
12.057000  sched_switch: other -> target(4201)
```

在事件完整、语义一致且任务状态符合模型时：

- 从切出到唤醒的约 50 ms 是一次候选阻塞阶段。
- 从成功唤醒到重新运行的约 7 ms 是一次候选调度等待。
- 两者合计约 57 ms 的 off-CPU，不是 57 ms 的 CPU 执行。

切出时若任务仍为 runnable，应从切出到切入考虑就绪等待，不能强行寻找一次唤醒。真实系统有状态竞争、迁移、重复事件和跨窗口问题，不能靠“找最近两行”写出通用可靠统计器。

检查 `sched.trace.stats` 中的 overrun 等计数。环形缓冲覆盖后，“没看到事件”不能等同于“事件没有发生”。

### 6.6 function 与 function_graph

- `function` 主要记录函数调用入口。
- `function_graph` 记录调用和返回，重建嵌套关系及持续时间。
- graph 中的持续时间是否包含睡眠/调度切出，取决于 `sleep-time` 等选项；中断相关选项也会改变观察口径。[R3]

以下示例只能在**授权的实验机**执行；它创建独立 instance，但仍会启用函数插桩。首先找到当前内核确实存在的目标函数：

```bash
grep -E '^vfs_read([[:space:]]|$)' /sys/kernel/tracing/available_filter_functions
```

如函数不存在，不要把另一个相似名称当成同义替代，应根据内核版本重新选择观测入口。

在一个 root Bash 中运行，替换 PID 和函数：

```bash
# 前提：tracefs 已挂载，目标进程仍运行，输出文件名未被占用。
# 此代码块作为同一个 root Bash 脚本执行。
set -e
TRACEFS=/sys/kernel/tracing
PID=12345
FUNC=vfs_read
OUT=/var/tmp/graph-lab-$$.trace
I="$TRACEFS/instances/graph-lab-$$"
mkdir "$I"
cleanup() {
    printf '0\n' > "$I/tracing_on"
    printf 'nop\n' > "$I/current_tracer"
    rmdir "$I"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '0\n' > "$I/tracing_on"
printf '%s\n' "$FUNC" > "$I/set_graph_function"
for T in /proc/"$PID"/task/[0-9]*; do
    test -d "$T" || continue
    printf '%s\n' "${T##*/}" >> "$I/set_ftrace_pid"
done
test -n "$(cat "$I/set_ftrace_pid")"
printf 'function_graph\n' > "$I/current_tracer"
test ! -e "$I/options/sleep-time" || cat "$I/options/sleep-time"
printf '1\n' > "$I/tracing_on"
sleep 3
printf '0\n' > "$I/tracing_on"
cat "$I/trace" > "$OUT"
printf 'Saved: %s\n' "$OUT"
```

`set_graph_function` 限定 graph 根入口；如果同时用 `set_ftrace_filter` 限得过窄，可能隐藏想观察的子调用。上例只展示窄时间窗，不应原样升级为高流量全机追踪。

若内核不支持 instance 中的 function_graph，需要在隔离实验环境重新设计；不要自动退回并重置全局 tracer。

### 6.7 trace-cmd 与离线分析

trace-cmd 封装了许多 tracefs 操作，可将数据保存为二进制并离线报告：[R18]

```bash
sudo trace-cmd record -o sched.dat \
    -e sched:sched_switch -e sched:sched_wakeup \
    -- sleep 3
trace-cmd report -i sched.dat
```

这是系统级示例，事件量可能很大，也可能涉及默认 tracing 配置。实际使用应查看本机 `trace-cmd record --help`，按支持情况选择实例和过滤。KernelShark 可用于观察 CPU、线程和事件时间线；可视化不能替代事件完整性检查。

### 6.8 ftrace 常见问题

| 问题 | 排查 |
|---|---|
| 没有 tracing 目录 | 配置、挂载、权限、当前 namespace |
| tracer 不存在 | 内核配置、实例支持、平台限制 |
| 过滤返回 EINVAL | 事件字段、表达式语法、函数是否可追踪 |
| 没有目标数据 | PID/TID、当前任务与事件目标的区别、负载是否覆盖目标路径 |
| 时序跨 CPU 不合理 | 时钟选择、缓冲覆盖、迁移、解析方法 |
| 函数持续时间与 CPU 时间不符 | graph 选项、睡眠、中断、嵌套、自身/包含耗时 |
| 实时读取卡住 | trace_pipe 等待新事件；停止读取进程，不要误判内核卡死 |

<a id="ebpf"></a>
## 7. eBPF：构建针对具体问题的观测

### 7.1 原理与数据路径

eBPF 是一种允许受约束程序在内核特定挂载点执行的机制，不是“所有内核位置都能任意执行代码”。[R5]

```text
用户态工具 / libbpf / bpftrace / BCC
  -> 构建或加载 BPF 字节码
  -> verifier 检查
  -> 解释执行或 JIT
  -> 挂载到指定事件/函数/其他 hook
  -> 读取上下文，更新 map，或提交事件
  -> 用户态读取聚合结果或消费事件
```

主要组成：

- **verifier**：检查程序类型允许的访问、指针使用、控制流与资源边界。不同内核能力不同；现代 BPF 并非简单“完全不允许循环”。
- **helper/kfunc**：程序可调用的受约束内核接口，受程序类型与内核功能限制。
- **map**：保存计数、直方图、关联状态等；类型、容量和并发模型影响正确性与成本。
- **事件输出**：perf buffer 或 BPF ring buffer 等机制将事件送到用户态。
- **BTF**：提供类型信息。
- **CO-RE**：配合 BTF 与重定位，适应一定范围的目标类型布局变化；不保证函数、字段语义和所有内核版本都兼容。[R5]

verifier 通过不代表观测逻辑正确，更不代表零开销或不会导致业务延迟。

### 7.2 挂载点如何选择

| 入口 | 适用情况 | 注意事项 |
|---|---|---|
| tracepoint | 预定义内核事件 | 先查字段，通常适合作为起点 |
| raw tracepoint | 需要较底层原始参数 | 类型和语义需更谨慎处理 |
| kprobe/kretprobe | 指定内核函数入口/返回 | 名称、ABI、可探测性与实现随版本变化 |
| fentry/fexit | 支持 BTF 的内核函数入口/退出 | 依赖对应内核/架构/函数支持，不是任意函数都可挂载 |
| uprobe/uretprobe | 用户态二进制或库函数 | 目标路径、符号、优化、调用 ABI、返回配对 |
| USDT | 应用主动暴露的静态探针 | 依赖应用构建与探针语义，适合业务阶段观测 |
| profile | 周期 CPU 栈采样 | 与其他采样一样，需要说明频率和范围 |

### 7.3 上手顺序：现成工具、脚本、libbpf

**第一步：现成工具。** 常见 BCC 工具的用途如下：[R19]

| 工具 | 主要用途 | 使用时必须确认 |
|---|---|---|
| execsnoop | 进程执行事件 | 是否记录敏感命令行、输出速率 |
| opensnoop | 打开文件事件 | 目标过滤、路径敏感性 |
| runqlat | 就绪等待分布 | PID/namespace 过滤与时间单位 |
| offcputime | off-CPU 栈聚合 | 是否包含用户栈、内核栈和哪些任务状态 |
| biolatency | 块 I/O 延迟分布 | 测量起点是否含排队、设备分层 |
| biosnoop | 逐项块 I/O 事件 | 事件量、层级和请求归属 |
| memleak | 分配/释放匹配分析 | 支持哪些 allocator、采样、遗漏与 attach 时机 |

安装后以本机 `--help` 为准。以下是常见 BCC 打包方式中的示例，而不是跨版本承诺：

```bash
runqlat-bpfcc --help
offcputime-bpfcc --help
sudo runqlat-bpfcc -p "$PID" 1 10
sudo offcputime-bpfcc -U -p "$PID" 10
```

**第二步：bpftrace。** 对具体问题快速写出小型观测。

**第三步：libbpf。** 当观测需要长期运行、稳定接口、严格资源控制、测试和部署时，编写版本化程序，而不是把临时脚本无限扩展。

### 7.4 bpftrace 版本与探针检查

本文脚本按 bpftrace 0.24 文档中的 `args.field` 语法编写。较旧版本示例常见 `args->field`；先看 `bpftrace --version`、本机文档和编译结果，不要只机械替换所有指针访问。[R20][R21]

```bash
sudo bpftrace -l 'tracepoint:syscalls:sys_enter_read'
sudo bpftrace -lv 'tracepoint:syscalls:sys_exit_read'
```

确认返回字段、事件存在以及目标是否真的执行 `read`。程序可能使用 `pread64`、`readv`、io_uring、mmap 或完全不同路径。

### 7.5 十秒系统调用计数

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_*
/pid == $1/
{
    @calls[probe] = count();
}
interval:s:10
{
    exit();
}
' "$PID"
```

这个例子说明“按目标过滤、在内核聚合、限时输出”的模式。它仍挂载了多个 syscall 探针；高流量环境应进一步只选候选 syscall。次数多不一定耗时多，次数少也不一定不重要。

### 7.6 read 延迟分布与慢事件

运行配套 [read_latency.bt](examples/read_latency.bt)：

```bash
sudo bpftrace examples/read_latency.bt "$PID"
```

核心关联逻辑：

```bpftrace
tracepoint:syscalls:sys_enter_read
/pid == $1/
{
    @start[tid] = nsecs;
}

tracepoint:syscalls:sys_exit_read
/pid == $1 && @start[tid]/
{
    @latency_us = hist((nsecs - @start[tid]) / 1000);
    delete(@start[tid]);
}
```

完整脚本还会统计成功返回的字节数、负返回值次数，输出超过 10 ms 的事件，在线程退出时删除残留状态，并在约十秒后退出。

判读：

- 测量的是 `read` 入口到退出的墙钟持续时间，包含期间的调度和阻塞。
- 包含成功、EOF、错误等不同返回结果；错误次数不能与成功字节数混为一谈。
- `read` 可能读文件、管道、socket、终端，不能仅凭函数名称定位为磁盘。
- 直方图桶表示范围，不是精确分位点；少量事件无法可靠推断 P99。
- 采集开始前已进入的调用、窗口结束时未返回的调用不构成完整样本。
- `printf` 走异步输出路径，可能丢事件；聚合与逐条输出的完整性要分别检查。

### 7.7 用户态函数耗时：观察应用阶段

配套程序的 `process_task` 被标记为不内联，可用于 uprobe 实验：

```bash
./perf_debug_lab sleep 30 1 &
PID=$!
BIN=$(readlink -f ./perf_debug_lab)

sudo bpftrace -e "
uprobe:$BIN:process_task
/pid == \$1/
{
    @begin[tid] = nsecs;
}
uretprobe:$BIN:process_task
/pid == \$1 && @begin[tid]/
{
    @task_us = hist((nsecs - @begin[tid]) / 1000);
    delete(@begin[tid]);
}
interval:s:10 { exit(); }
END { clear(@begin); }
" "$PID"
wait "$PID"
```

外层使用双引号是为了展开 `$BIN`，因此 bpftrace 的 `$1` 需要写为 `\$1`。路径中含特殊字符时应另外处理；示例假设常规实验路径。

该函数时长包含 `usleep`，所以会得到约 20 ms 与偶发约 200 ms 两类样本，而不是只看到函数自身 CPU 指令成本。

**单层 `@begin[tid]` 只适用于该示例没有递归/同线程重入的函数。** 通用函数追踪需要按 TID 和深度或调用实例建立配对，考虑异常返回、longjmp、线程退出、符号变化和返回探针遗漏。

### 7.8 off-CPU 不等于阻塞原因

off-CPU 栈告诉你“切出时在哪条路径”，通常能帮助定位等待点，但不自动告诉你“谁占着锁”或“哪个请求造成了等待”。

例如等待栈集中在 futex：

1. 确认是锁、条件变量、线程 join 还是其他 futex 用法。
2. 关联对象地址、操作类型、TID、时间。
3. 找到拥有者或发出唤醒者的证据。
4. 查看持有期间在执行还是在等待其他资源。
5. 改动后比较等待分布和吞吐。

某些采集工具默认只统计特定状态或最小时间；分析时必须记录这些过滤条件。

### 7.9 数据正确性与开销

设计 BPF 观测前应回答：

- 单个事件的执行频率与工作量是多少？
- map 最大 key 数是否受控？PID/TID/地址是否会不断增长？
- 入口和出口怎样配对？进程退出或标识复用怎样清理？
- 多 CPU 更新是否需要 per-CPU map、原子操作或其他同步？
- 采集器能否消费输出？缓冲满时如何统计丢失？
- 用户内存读取失败怎样处理？
- 有没有探针遗漏、内核函数变化或 attach 时机造成的偏差？
- 退出后是否存在 pinned map/link 或其他仍活动对象？

聚合通常比逐事件打印更节省输出资源，但并不意味着程序本身开销为零。

### 7.10 eBPF 常见故障

| 现象 | 检查 |
|---|---|
| 探针不存在 | 内核版本、事件、符号、内联、BTF、架构 |
| verifier 拒绝 | 完整日志、指针类型、边界、允许接口、资源限制 |
| Operation not permitted | BPF/性能/追踪权限、LSM、lockdown、容器策略 |
| 无数据 | 目标标识、路径、线程、负载是否执行该函数或 syscall |
| 用户栈不完整 | unwind 方式、目标优化、frame pointer、栈深度 |
| 时长异常巨大 | 入口出口配对、时钟、旧 map 状态、PID/TID 复用 |
| 内存不断增长 | 高基数 map、缺少删除、任务退出清理 |
| 输出不完整 | buffer 丢失、用户态消费、打印速率；不要只看屏幕是否持续刷新 |

<a id="gdb"></a>
## 8. GDB：在线执行状态与代码假设

### 8.1 原理

GDB 通过 ptrace 或目标调试接口控制进程，结合 ELF、符号和 DWARF，将地址、寄存器与源码状态联系起来。[R8][R9]

- 软件断点通常在支持的平台上修改目标指令，使执行到达时陷入调试器。
- 硬件断点/观察点依赖架构提供的有限资源。
- 逐步执行、断点和 attach 都可能改变线程调度、超时及竞争条件。
- 默认 all-stop 模式下，一个线程停止时其他线程也停止。non-stop 依赖目标支持，也不应被视为对业务“完全无影响”。[R22]

因此，GDB 是验证执行状态的工具，不是低扰动持续性能监控工具。

### 8.2 从自己启动的程序入门

```bash
gdb --args ./perf_debug_lab crash
```

GDB 内执行：

```gdb
set pagination off
break crash_now
run
list
next
continue
bt
frame 0
info locals
info registers
x/i $pc
disassemble /m crash_now
```

实验程序对一个 `PROT_NONE` 映射写入，制造真实的访问权限故障。崩溃后应结合当前指令、寄存器和映射解释，而不是看到 SIGSEGV 就自动判断是空指针。

`next` 在优化代码中可能跨过多行、重复同一行或看不到变量，这不是一定发生了 GDB 错误。

### 8.3 常用命令

| 命令 | 作用 |
|---|---|
| `break function` / `break file.c:line` | 函数/源码行断点 |
| `break function if expression` | 条件断点 |
| `info breakpoints` / `delete N` | 查看/删除断点 |
| `run` / `continue` | 启动/继续 |
| `step` / `next` / `finish` | 进入、跨过、运行到函数返回 |
| `bt` / `bt full` | 当前线程栈及局部变量 |
| `info threads` | 全部线程及当前位置 |
| `thread N` | 切换 GDB 内部线程编号 |
| `thread apply all bt` | 所有线程栈 |
| `frame N` / `info args` / `info locals` | 选择栈帧、参数、局部变量 |
| `p expression` / `ptype type` | 求值/查看类型 |
| `x/16gx ADDRESS` / `x/s ADDRESS` | 查看内存/字符串 |
| `info registers` / `x/i $pc` | 查看寄存器/当前指令 |
| `watch expression` | 数据写入观察点 |
| `catch syscall name` | 捕获系统调用，依目标支持 |
| `detach` | 从在线目标分离，通常允许目标继续 |

`thread N` 的 N 是 GDB 线程编号，不是 Linux TID；`info threads` 中的 LWP 标识帮助关联内核任务。[R9]

### 8.4 附加在线进程

获得授权并确认暂停影响后：

```bash
sudo gdb -p "$PID"
```

```gdb
set pagination off
info threads
thread apply all bt
info sharedlibrary
info proc mappings
detach
quit
```

一个有界的批处理快照示例：

```bash
sudo gdb -q -nx -batch -p "$PID" \
    -ex 'set pagination off' \
    -ex 'thread apply all bt' \
    -ex 'detach'
```

这里避免打印所有局部变量，减少日志和敏感信息暴露，但 attach 和栈展开仍可能暂停业务。无法把它承诺为固定毫秒开销。

退出前确认目标已分离并恢复运行。不要把 `kill` 当作 detach，也不要在不确定 inferior 状态时直接接受所有退出提示。

### 8.5 卡死与死锁分析

推荐过程：

1. 获取全线程栈，识别运行者、锁等待者、I/O 等待者和事件循环。
2. 关联 Linux TID 与调度/CPU 证据。
3. 查看等待条件与对象。
4. 构建“线程 A 等待线程 B 持有的资源”关系。
5. 必要时隔一段时间再获取快照，验证是否一直不前进。

glibc `pthread_mutex_t` 内部字段属于实现细节，随架构和版本变化；不能把某个 `__owner` 字段当作跨平台标准 API，也不能不核对锁类型就解释拥有者。

### 8.6 watchpoint 的适用与风险

```gdb
watch target_variable
continue
```

用于寻找“谁修改了某个状态”。但需要：

- 对象地址稳定且生命周期已确认。
- 了解变量是否被优化、移到寄存器或已释放。
- 考虑硬件观察点数量、长度与对齐限制。
- 警惕退化成软件观察点后产生很高开销。

对 heap corruption，破坏发生点可能早于最终崩溃很久。可在复现环境结合 ASan、UBSan、Valgrind；若怀疑数据竞争，可评估 TSan。它们会改变内存、时间和调度，不应无评估地用于生产。

### 8.7 优化代码与调用函数

`<optimized out>` 表示调试器无法从现有信息恢复变量，不代表变量没有影响程序。

其他常见限制：

- 内联与尾调用使源代码调用关系不直接对应物理栈。
- 源码不匹配使行号与指令关系错误。
- 栈损坏可能让 `bt` 的后续帧不可信。
- `p some_function()` 可能在被调试进程里真正调用函数，产生锁、I/O、分配或副作用。

线上排查优先只读取状态，不随意执行目标函数或修改变量。

<a id="coredump"></a>
## 9. 用户态 coredump：保存与分析崩溃现场

### 9.1 core 是什么，不是什么

用户态 core 通常是 ELF 格式的进程现场文件，含线程寄存器、信号相关信息及按策略选取的内存映射。[R10]

它不是：

- 进程从启动到崩溃的完整历史。
- 任意时刻均一致的整个系统快照。
- 永远包含全部内存的文件。
- 无须配套二进制即可可靠分析的独立证据。

正常退出一般不会产生 core；SIGKILL 不产生 core，OOM kill 常见情况下也不能指望留下 core。具体信号的默认处置决定是否走 core 路径。

### 9.2 两条收集路径

```text
致命信号
  -> 内核 core dump 流程
     -> core_pattern 为文件模板：写入指定文件
     -> core_pattern 以 "|" 开头：传给用户态收集器
        -> 例如 systemd-coredump 或发行版其他收集器
```

检查：

```bash
ulimit -Sc
ulimit -Hc
cat /proc/sys/kernel/core_pattern
cat /proc/sys/kernel/core_pipe_limit
cat "/proc/$PID/limits"
cat "/proc/$PID/coredump_filter"
```

`ulimit -c` 只影响当前 shell 及其后代，不会自动改变已有服务。管道模式下内核不按普通文件模式执行 `RLIMIT_CORE` 限制，收集器还可能有自己的处理与存储限制。[R10]

### 9.3 systemd-coredump 的常用流程

仅适用于机器确实使用 systemd-coredump 的情况：[R11][R12]

```bash
coredumpctl list
coredumpctl info "$PID"
coredumpctl debug "$PID"
coredumpctl --output=target.core dump "$PID"
```

历史记录可能存在 PID 复用。更可靠的定位还应结合时间、可执行文件和其他字段：

```bash
coredumpctl --since '2026-11-19 10:00:00' \
    --until '2026-11-19 10:10:00' list /absolute/path/to/app
```

不要把“不在当前目录”解释为“没有 core”。也不要把 journal 中仍有记录解释为实际 core 文件仍在：日志与文件的保留策略不同，`COREFILE` 状态可能显示缺失或截断。[R11]

systemd 服务可通过明确的 service drop-in 管理 `LimitCORE`；收集器还可通过 `coredump.conf` 管理 `Storage`、`ProcessSizeMax`、`ExternalSizeMax` 等。变更要由管理员核对本机版本、重载/重启要求和磁盘预算后执行，不应为了单个服务粗暴修改全机配置。[R12]

### 9.4 在隔离实验机使用直接文件模式

优先保留现有收集器。下面的直接文件模式仅用于**可接受全局 core_pattern 临时变更的隔离实验 VM**。

在包含 `perf_debug_lab` 的目录中，将以下代码块作为同一 Bash 脚本执行：

```bash
set -e
RUN=$(mktemp -d "$PWD/core-lab.XXXXXX")
BIN=$(readlink -f ./perf_debug_lab)
OLD_PATTERN=$(cat /proc/sys/kernel/core_pattern)
restore() {
    printf '%s\n' "$OLD_PATTERN" | sudo tee /proc/sys/kernel/core_pattern >/dev/null
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
sudo -v
printf '%s\n' "$RUN/core.%e.%p.%t" |
    sudo tee /proc/sys/kernel/core_pattern >/dev/null
(
    cd "$RUN"
    ulimit -c unlimited
    "$BIN" crash
) || true
printf 'Inspect core files in: %s\n' "$RUN"
```

即使有 trap，SIGKILL、断电、其他管理员并发修改或 sudo 授权失效也可能使恢复失败。运行后再次核对 `core_pattern`。硬限制较低时，普通用户不能提高 soft limit 到 unlimited，应先由管理员处理。

模板含义：`%e` 是可执行名称，`%p` 是目标 PID namespace 中的 PID，`%P` 是初始 PID namespace 中的 PID，`%t` 是时间戳。容器与收集器路径更要核对。[R10]

### 9.5 没有生成 core 时的排查顺序

1. 是否是会产生 core 的致命信号，而不是 SIGKILL、正常退出或被信号处理器接管？
2. 目标进程真实的资源限制是什么，而不是当前终端的限制？
3. `core_pattern` 是直接文件还是收集器？
4. 目录可写吗？磁盘、配额与文件大小限制是否足够？
5. 进程是否 dumpable，是否涉及特权程序或安全策略？
6. systemd/其他收集器是否只记录元数据、限制大小或已删除文件？
7. `coredump_filter`、`MADV_DONTDUMP` 是否排除了关键映射？
8. 目标 namespace、挂载与路径是否与预期一致？

### 9.6 用 GDB 分析 core

```bash
gdb -q ./perf_debug_lab /absolute/path/to/core.file
```

```gdb
set pagination off
info files
info threads
thread apply all bt
bt full
frame 0
info args
info locals
info registers
x/i $pc
disassemble /m
```

建议顺序：

1. 确认信号、时间、进程与二进制版本。
2. 确认符号和动态库是否正确加载。
3. 查看所有线程，不只看当前崩溃线程。
4. 找到故障指令及其访问的地址。
5. 判断该地址为何无效或权限不符。
6. 沿数据流找对象来源与生命周期。
7. 回到复现环境验证“最早的错误”，而非只修补崩溃栈顶。

### 9.7 跨机器分析与匹配

```gdb
set sysroot /analysis/rootfs
set solib-search-path /analysis/rootfs/usr/lib:/analysis/rootfs/lib
set debug-file-directory /analysis/debug
set substitute-path /build/source /analysis/source
```

这些路径都需替换为真实目录。`sysroot` 应提供与故障机器匹配的运行库；只让 GDB 找到“一个同名 libc”并不够。源码路径替换仅改变查找位置，不会把不同版本源码变成匹配版本。[R23]

### 9.8 gcore：对存活进程生成快照

```bash
sudo gcore -o snapshot "$PID"
```

典型输出名为 `snapshot.PID`；也可在 GDB 中使用 `generate-core-file`。它不等于向进程发送致命信号，但调试器会介入并暂停进程，可能消耗显著 I/O、空间并暴露敏感数据。[R24]

core 中可能有令牌、密钥、用户数据和完整请求内容。默认以敏感证据处理，不上传到公共服务。

<a id="kernel-debug"></a>
## 10. 内核故障：Oops、panic、kdump 与 vmcore

### 10.1 先区分故障层次

| 现象 | 含义 | 首要证据 |
|---|---|---|
| 用户态 SIGSEGV/SIGABRT | 进程异常终止 | 用户态日志、core |
| Oops | 内核报告异常；系统可能继续，也可能随后 panic | 完整日志、寄存器、调用链、taint |
| panic | 内核无法继续正常运行，进入 panic 路径 | panic 日志、捕获结果、vmcore |
| lockup/hang | 系统或 CPU 长时间无法正常前进 | watchdog、串口/远程控制台、预先配置的观测 |

出现 Oops 后继续运行的系统不应自动视为状态可靠。后续异常可能是第一次错误的连锁反应。

### 10.2 kdump 工作机制

```text
正常内核启动
  -> 为 crash kernel 预留内存
  -> 提前加载捕获内核与必要 initramfs
正常内核发生 panic
  -> 转入捕获内核
  -> 捕获内核通过 /proc/vmcore 访问故障内存镜像
  -> 保存或过滤为 vmcore
  -> 使用匹配 vmlinux + vmcore 离线分析
```

这样避免依赖已受损内核完整执行文件系统和网络保存流程。但捕获内核仍要具备必要驱动、存储或网络能力，并且预留内存足够。[R13]

`crashkernel=` 的大小取决于架构、驱动、initramfs、机器配置与发行版方案，不存在适用于所有系统的固定数字。

### 10.3 运行前检查与演练

```bash
cat /proc/cmdline
cat /sys/kernel/kexec_crash_loaded
cat /sys/kernel/kexec_crash_size
```

文件可能因配置而不存在。再按发行版检查：

- kdump 服务是否启用并已装载捕获内核？
- 预留内存是否成功？
- 保存目标是否可访问，空间是否足够？
- 捕获 initramfs 是否有必要设备驱动？
- Secure Boot、lockdown、签名和 kexec 策略是否允许该流程？
- 是否保存了匹配的 `vmlinux`、模块和调试信息？

**必须在可重启的测试机上做受控演练。** 仅看到服务 active 不能证明下一次 panic 一定能保存有效 vmcore。本文不提供可直接复制执行的强制 panic 命令，避免误用到生产。

### 10.4 crash 的最小入口

```bash
crash /analysis/vmlinux /analysis/vmcore
```

```text
sys
log
bt
ps
kmem -i
files
```

随后根据故障线程、锁、内存对象和模块逐步深入。命令参数与对象解释应以本机 crash 帮助为准。[R25]

调查报告需要记录：

- 第一次异常，而不仅是最后一次 panic。
- 内核版本、模块、taint、故障 CPU 和任务。
- 故障指令、寄存器、相关对象。
- vmcore 是否经过过滤，是否缺少用户页或其他数据。
- 分析工具与目标内核是否兼容。

### 10.5 drgn、KGDB 和辅助机制

- **drgn**：用 Python API 检查程序、内核及 core/vmcore 中的对象，适合按类型和数据结构探索。[R26]
- **KGDB/KDB**：在具备调试连接和内核支持时进行内核在线调试；停止内核会影响整个系统，适用于专门调试环境。[R27]
- **pstore/ramoops、串口、远程控制台**：可作为重启后日志保留或无法取得 vmcore 时的补充。
- **KASAN/KCSAN/lockdep 等**：适合在复现/验证环境定位内存错误、竞态与锁依赖问题；需要相应构建和开销评估。

GDB 可以用于某些原始内核 core 场景，但普通用户态 GDB 工作流通常不足以方便理解 Linux 内核任务、slab、锁和其他对象；优先使用专门内核分析工具。

<a id="cases"></a>
## 11. 综合案例：把工具串成证据链

以下案例使用配套程序，属于教学故障模型。真实服务还需要请求 ID、阶段埋点、负载控制和跨组件关联。

### 11.1 CPU 高、吞吐低

**实验**

```bash
perf stat -e task-clock,context-switches,page-faults \
    -- ./perf_debug_lab cpu 10 1
perf record -o case-cpu.data -F 99 -e cycles:u -g --call-graph fp \
    -- ./perf_debug_lab cpu 10 1
perf report -i case-cpu.data --stdio --no-children
perf annotate -i case-cpu.data --stdio --symbol burn_cpu
```

**候选原因**：计算量增加、某段算法变慢、系统调用占比上升、无效循环、输入变化。

**证据解释**：

- task-clock 与墙钟接近，说明平均约一个 CPU 执行。
- `burn_cpu` 热点说明事件权重集中在实验计算函数。
- annotate 帮助定位指令区域，但不能单独证明某条指令是微架构瓶颈。

**验证**：在相同输入和编译条件下修改算法或减少无效工作，比较 ops/s、每操作 CPU 时间与错误率。

**边界**：看到函数热点并不意味着改它一定收益最大；还应检查业务中该工作是否必要。

### 11.2 CPU 不高，但请求慢、尾延迟明显

**实验**

```bash
./perf_debug_lab sleep 30 1 &
PID=$!
pidstat -t -u -w -p "$PID" 1 5
# 再使用第 7.7 节的 process_task uprobe 实验。
wait "$PID"
```

**候选原因**：排队、等待锁、定时器、下游 I/O、CPU 限额。

**证据解释**：

- 程序故意在大多数 task 中等待约 20 ms，少量等待约 200 ms。
- on-CPU profile 不会把这两类等待直接当成 CPU 热点。
- 用户函数入口/退出的直方图能观察到两个延迟区域。
- 调度事件帮助区分睡眠等待与唤醒后未及时运行。

**验证**：改变等待逻辑后，比较相同 task 的延迟分布；真实服务应把结果关联到请求阶段。

**边界**：每 50 次才有一次慢 task，十秒可能只有少量尾部样本。不要把桶图直接当成精确 P99 报告。

### 11.3 多线程扩展性差：锁持有期间做了慢工作

**实验**

```bash
./perf_debug_lab lock 5 1
./perf_debug_lab lock 5 4
./perf_debug_lab lock 30 4 &
PID=$!
sleep 1
sudo bash examples/capture_sched.sh "$PID" 5 "$PWD/case-lock.trace"
# 已安装且参数匹配时，可补充 offcputime-bpfcc。
wait "$PID"
```

**候选原因**：锁串行化、CPU 配额、调度开销、缓存共享、单线程阶段。

**证据解释**：

- 四线程吞吐不按线程数增加，仅说明有共享瓶颈。
- 实验互斥锁内故意执行 `usleep` 和计算，导致等待者 off-CPU。
- 用户栈与源码定位锁等待/持有区域。
- 调度追踪区分等待与 runnable 竞争。

**验证**：在另一个实验版本中把非共享慢工作移出临界区，保持共享状态正确，再比较吞吐、等待分布和 CPU。

**边界**：futex 等待并不自动证明死锁；某次 pthread lock 快路径可能完全不进入内核。

### 11.4 I/O 慢：逐层区分应用、文件系统与设备

**实验**

```bash
# io 模式不覆盖已有 lab-io.data；在新的实验目录运行。
BIN=$(readlink -f ./perf_debug_lab)
RUN=$(mktemp -d "$PWD/io-lab.XXXXXX")
(
    cd "$RUN"
    "$BIN" io 10 1
)
```

程序循环写入一个最多约 1 MiB 的文件，并在每次写入后 `fsync`。可能产生很高同步 I/O 频率，**仅用于隔离测试存储**。

**观测顺序**：

1. 应用 ops/s 和单操作耗时。
2. `pidstat -d` 与 `iostat` 的对应窗口。
3. `pwrite64` 和 `fsync` 入口/退出持续时间，而不是本文的 read 脚本。
4. 必要时用 biolatency/biosnoop 观察块设备阶段。

**证据解释**：

- `fsync` 慢可能包含文件系统日志、写回与设备持久化等待。
- syscall 延迟和块请求延迟不构成简单一一对应。
- 多层设备、虚拟盘、网络文件系统和缓存使解释更加复杂。
- 对并行设备，`%util` 接近 100% 不等于证明设备绝对饱和。

**验证**：按业务持久化要求评估批量提交、并发或存储方案；不能为了性能直接删除数据可靠性要求。

### 11.5 RSS 增长：验证是否真的泄漏

**实验**

```bash
./perf_debug_lab leak 30 1 &
PID=$!
pidstat -r -p "$PID" 1 10
cat "/proc/$PID/smaps_rollup"
wait "$PID"
```

程序每次分配并触碰 64 KiB，不释放，最多累计约 64 MiB；仅允许一个线程。进程退出后 OS 回收其地址空间。

**候选原因**：真正泄漏、应用缓存、allocator 保留、线程栈、文件/共享映射。

**证据解释**：

- 匿名 RSS 增长支持“匿名内存相关”，但不足以证明泄漏。
- 对实验代码，分配未释放的源码和分配/释放匹配提供直接证据。
- 对真实程序，memleak 需确认覆盖的 allocator、attach 时机、采样与追踪遗漏。

**验证**：复现环境可用：

```bash
valgrind --leak-check=full --show-leak-kinds=all \
    ./perf_debug_lab leak 2 1
```

该命令需要另外安装 Valgrind，并可能明显降低速度。固定秒数的实验在检测器下会完成更少迭代，不能直接比较吞吐。

### 11.6 SIGSEGV：从现场到故障指令

**实验**：优先直接 `gdb --args ./perf_debug_lab crash`，或按第 9 章配置好的收集机制生成 core。

**调查步骤**：

1. 确认 SIGSEGV 及匹配程序。
2. `bt` 找到 `crash_now`。
3. `x/i $pc` 看当前写内存指令。
4. `info registers` 和局部变量定位被写地址。
5. 将地址与 `PROT_NONE` 映射联系。
6. 解释为访问权限故障，而不是空指针。

**验证**：在实验副本中改变映射权限后验证故障是否消失；真实 bug 则要调查地址来源、长度、生命周期或竞态。

**边界**：真实 UAF/越界往往在更早处破坏状态，崩溃位置只是发现错误的位置。

### 11.7 服务卡死：建立等待关系

**不额外制造死锁**；对真实授权目标：

1. 记录线程状态和线程级 CPU。
2. 获取全线程栈，之后分离调试器。
3. 关联锁/条件对象、拥有者与等待者。
4. 检查是否形成资源等待环，或是否只是下游长期阻塞。
5. 必要时用第二次栈快照及调度事件判断是否仍有进展。

最终结论应写成“线程 A 持有 X 等待 Y，线程 B 持有 Y 等待 X”，而不是“多个线程在 futex 所以死锁”。

<a id="production"></a>
## 12. 生产使用规范与分析报告

### 12.1 采集前

- 确认授权、目标、持续时间和负责人。
- 记录版本、符号、内核、负载、时间与时区。
- 选择专用受限目录，确认容量、文件权限和归属。
- 先小范围短时验证，再决定是否扩大。
- 记录采样频率、缓冲大小、探针集合、map 容量。
- 明确延迟/CPU/磁盘阈值与提前停止条件。
- 确认怎么停止、怎么恢复、异常退出怎么处理。

### 12.2 采集期间

- 观察目标与采集器 CPU、内存、输出速率和磁盘。
- 检查样本/事件丢失和 map 更新失败。
- 避免无界全机事件打印。
- 不同时启用多套高开销追踪而无法归因。
- 不在高流量路径频繁 attach、gcore 或断点停止。
- 负载改变、部署发生或目标重启时，标记时间窗变化。

### 12.3 采集结束

- 确认采集进程退出、调试目标恢复运行。
- 检查 ftrace instance、启用事件、tracer 和其他残留配置。
- 核对 BPF pinned 对象；不要删除不属于本次采集的对象。
- 恢复经过批准临时变更的配置，并与原值比较。
- 检查文件完整性、大小、权限、构建 ID 和元数据。
- 在解释前披露丢失、截断、缺少符号或窗口偏差。
- 按敏感数据策略存储、传输、脱敏和清理。

### 12.4 报告模板

```markdown
# 调查报告

## 问题与基线
- 现象、影响、开始时间：
- 比较基线、负载与指标：

## 环境与采集
- 应用/内核/工具版本、构建 ID：
- 时间窗、时区、PID/TID/cgroup：
- 命令、事件、频率、过滤、缓冲：
- 符号、数据丢失、采集扰动与其他限制：

## 假设与证据
- 假设 A：
- 支持/反驳的证据：
- 已排除的原因：
- 尚待验证的问题：

## 结论与验证
- 已证明的事实：
- 推断与置信度：
- 修改、复测、收益与副作用：

## 交付与清理
- 数据路径与校验：
- 配置恢复、目标状态、敏感数据处理：
```

<a id="appendices"></a>
## 13. 附录

### 附录 A：配套实验文件与运行边界

配套文件与 Markdown 同级的 `examples/` 中：

| 文件 | 用途 |
|---|---|
| [perf_debug_lab.c](examples/perf_debug_lab.c) | CPU、锁等待、延迟、内存增长、I/O 和 SIGSEGV 实验 |
| [read_latency.bt](examples/read_latency.bt) | 目标进程 read 延迟分布、错误次数与慢事件 |
| [capture_sched.sh](examples/capture_sched.sh) | 使用独立 ftrace instance 采集现有目标线程调度事件 |

在包含本文的目录执行：

```bash
gcc -O2 -g3 -fno-omit-frame-pointer -fno-optimize-sibling-calls \
    -pthread -Wall -Wextra -o perf_debug_lab examples/perf_debug_lab.c
./perf_debug_lab cpu 5 1
```

程序参数：

```text
perf_debug_lab cpu|lock|sleep|leak|io [seconds] [threads]
perf_debug_lab crash
```

- seconds 范围 1..300，默认 10。
- threads 范围 1..32，默认 1。
- leak 仅允许 1 线程，最多泄漏约 64 MiB，退出后释放整个进程地址空间。
- io 只在当前目录创建新的 `lab-io.data`，拒绝覆盖已有文件；文件逻辑大小最多约 1 MiB，但同步写频率可能很高。
- crash 会故意 SIGSEGV，只能在实验环境执行。
- lock 模式有意把慢工作放在锁内，展示串行化，不是生产编程范例。
- 输出 ops/s 是实验任务吞吐，不等同于生产请求吞吐或 SLA。

### 附录 B：最小命令速查

所有 PID、路径都必须替换；需要权限的命令应先确认授权。

| 任务 | 入口 |
|---|---|
| 线程 CPU 与切换 | `pidstat -t -u -w -p "$PID" 1 10` |
| 进程整体计数 | `perf stat -p "$PID" -e task-clock,context-switches -- sleep 10` |
| CPU 用户态热点 | `perf record -F 99 -e cycles:u -g --call-graph fp -p "$PID" -- sleep 10` |
| 文本热点报告 | `perf report -i perf.data --stdio --no-children` |
| 指令位置 | `perf annotate -i perf.data --stdio --symbol FUNCTION` |
| 构建 ID | `perf buildid-list -i perf.data` |
| tracepoint 字段 | `cat /sys/kernel/tracing/events/sched/sched_switch/format` |
| 窄范围调度事件 | `sudo bash examples/capture_sched.sh "$PID" 5 "$PWD/sched.trace"` |
| BPF 探针检查 | `sudo bpftrace -lv 'tracepoint:syscalls:sys_exit_read'` |
| read 延迟 | `sudo bpftrace examples/read_latency.bt "$PID"` |
| 在线线程栈 | 授权后 GDB attach，`thread apply all bt`，`detach` |
| 崩溃记录 | `coredumpctl info "$PID"` |
| 导出 core | `coredumpctl --output=target.core dump "$PID"` |
| core 分析 | `gdb MATCHED_BINARY CORE_FILE` |
| 存活进程快照 | 授权后 `gcore -o snapshot "$PID"` |
| 内核现场 | `crash MATCHED_VMLINUX VMCORE` |

### 附录 C：术语与常见误区

| 术语 | 简明解释 |
|---|---|
| PMU | CPU/平台的性能监控硬件 |
| IPC | 在指定计数条件下的 instructions/cycles |
| tracepoint | 内核或应用预定义事件位置 |
| kprobe/uprobe | 内核/用户态函数或指令位置的动态探针 |
| TID | Linux 单个线程的任务标识 |
| TGID | 线程组标识，常被用户态称为进程 PID |
| BTF | 类型元数据，支持 BPF 类型处理等 |
| CO-RE | 配合 BTF 的 BPF 构建一次、目标重定位机制 |
| on/off-CPU | 正在执行/不在 CPU 上执行 |
| core/vmcore | 用户态进程现场/内核故障内存现场 |
| build ID | 用于识别二进制构建的 ELF 元数据 |

应反复提醒的误区：

1. 热点百分比不是请求墙钟占比。
2. 系统负载不是 CPU 利用率。
3. off-CPU 不是单一阻塞类型。
4. futex 栈不是死锁证明。
5. RSS 增长不是泄漏证明。
6. read 延迟不是设备延迟。
7. 火焰图不是事件时间轴。
8. verifier 通过不是观测逻辑正确的证明。
9. BTF/CO-RE 不是任意内核兼容保证。
10. core/vmcore 栈顶不是一定的根因位置。

### 附录 D：本机验证范围

编写环境为 Windows + WSL Ubuntu 22.04。验证结果：

- 配套 C 程序使用 GCC，以 `-O2 -g3 -fno-omit-frame-pointer -fno-optimize-sibling-calls -pthread -Wall -Wextra -Werror` 编译。
- 检查 `burn_cpu`、`process_task`、`crash_now` 符号可见。
- 检查 CPU、lock、sleep、leak、io 模式的短时运行、参数拒绝与 I/O 不覆盖行为；crash 用独立 ptrace 测试拦截 SIGSEGV/SEGV_ACCERR，不触发全局 core 收集器。
- 对配套 Bash 脚本做 `bash -n` 语法检查，并检查 Markdown 目录、引用、代码围栏和配套文件链接。
- 当前 WSL 未安装 perf、bpftrace、GDB、BCC、crash；本文相应命令依据官方资料编写，**未在本机完成这些工具的运行验证**。
- ftrace root 采集、BPF verifier/挂载、GDB/core 分析和 kdump 演练须在具备条件的 Linux 测试环境进一步验证。

不要将上述部分验证解读成跨发行版、跨内核的完整兼容性承诺。

<a id="references"></a>
## 14. 官方资料与参考索引

本文按资料中的机制与接口整理，未复制长篇原文。官方 `latest`/`current` 页面会随时间变化；分析特定机器时优先查对应版本的内核文档和本机 man/help。bpftrace 示例明确参考 0.24 文档，不宣称它是最新版本。

- [R1] Linux man-pages：perf_event_open(2)，事件、权限、计数、采样和缓冲区。
- [R2] Linux kernel：Perf events and tool security，权限与资源控制。
- [R3] Linux kernel：ftrace，tracefs、tracer、instance 与 graph 选项。
- [R4] Linux kernel：Event tracing，事件字段与过滤。
- [R5] Linux kernel：libbpf overview，BPF 加载与 CO-RE。
- [R6] Linux perf：perf-stat 文档。
- [R7] Linux perf：perf-record 文档。
- [R8] GDB：Attaching to a Running Process。
- [R9] GDB：Debugging Programs with Multiple Threads。
- [R10] Linux man-pages：core(5)，生成条件、core_pattern 与映射过滤。
- [R11] systemd：coredumpctl，检索、导出和调试。
- [R12] systemd：coredump.conf，处理与存储限制。
- [R13] Linux kernel：kdump，捕获内核与 vmcore。
- [R14] Linux kernel：Yama，ptrace 访问约束。
- [R15] Linux kernel：PSI，资源停顿压力。
- [R16] Linux perf：perf-report 文档，聚合与百分比。
- [R17] FlameGraph 项目：采样栈折叠与可视化工具。
- [R18] trace-cmd：record 文档。
- [R19] BCC 项目：工具、示例和参考文档。
- [R20] bpftrace 0.24：语言、探针与内建变量。
- [R21] bpftrace 0.24：标准库、聚合与输出。
- [R22] GDB：Thread Stops，all-stop/non-stop。
- [R23] GDB：Source Path，源码位置处理。
- [R24] GDB：Core File Generation，存活进程现场生成。
- [R25] crash 项目：Linux 内核现场分析工具。
- [R26] drgn 项目：可编程调试器。
- [R27] Linux kernel：KGDB/KDB。

[R1]: https://man7.org/linux/man-pages/man2/perf_event_open.2.html
[R2]: https://www.kernel.org/doc/html/latest/admin-guide/perf-security.html
[R3]: https://www.kernel.org/doc/html/latest/trace/ftrace.html
[R4]: https://www.kernel.org/doc/html/latest/trace/events.html
[R5]: https://www.kernel.org/doc/html/latest/bpf/libbpf/libbpf_overview.html
[R6]: https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-stat.txt
[R7]: https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-record.txt
[R8]: https://sourceware.org/gdb/current/onlinedocs/gdb.html/Attach.html
[R9]: https://sourceware.org/gdb/current/onlinedocs/gdb.html/Threads.html
[R10]: https://man7.org/linux/man-pages/man5/core.5.html
[R11]: https://www.freedesktop.org/software/systemd/man/latest/coredumpctl.html
[R12]: https://www.freedesktop.org/software/systemd/man/latest/coredump.conf.html
[R13]: https://www.kernel.org/doc/html/latest/admin-guide/kdump/kdump.html
[R14]: https://www.kernel.org/doc/html/latest/admin-guide/LSM/Yama.html
[R15]: https://www.kernel.org/doc/html/latest/accounting/psi.html
[R16]: https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf-report.txt
[R17]: https://github.com/brendangregg/FlameGraph
[R18]: https://www.trace-cmd.org/Documentation/trace-cmd/trace-cmd-record.1.html
[R19]: https://github.com/iovisor/bcc
[R20]: https://bpftrace.org/docs/release_024/language
[R21]: https://bpftrace.org/docs/release_024/stdlib
[R22]: https://sourceware.org/gdb/current/onlinedocs/gdb.html/Thread-Stops.html
[R23]: https://sourceware.org/gdb/current/onlinedocs/gdb.html/Source-Path.html
[R24]: https://sourceware.org/gdb/current/onlinedocs/gdb.html/Core-File-Generation.html
[R25]: https://github.com/crash-utility/crash
[R26]: https://drgn.readthedocs.io/en/latest/
[R27]: https://www.kernel.org/doc/html/latest/process/debugging/kgdb.html
