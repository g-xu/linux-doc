# cgroup v2 深度解析

> 基于 Linux 6.1.158 内核源码，架构限定 ARM64

---

## 1. cgroup 是什么

cgroup (Control Groups) 是 Linux 内核提供的**资源隔离与限制机制**，能将进程分组并对每组施加资源约束。

```
┌─────────────────────────────────────────────────────────────────────┐
│  cgroup 解决的核心问题                                               │
│                                                                     │
│  没有 cgroup:                                                       │
│    所有进程共享 CPU/内存/IO，互相干扰，无法隔离                       │
│    一个进程 OOM 可能影响整个系统                                      │
│    无法限制某个服务只用 2 个 CPU、最多 1GB 内存                       │
│                                                                     │
│  有了 cgroup:                                                       │
│    ┌──────── 组A: 业务进程 ────────┐                                │
│    │ CPU: 最多 4 核               │                                │
│    │ 内存: 最多 4GB               │                                │
│    │ IO: 100MB/s                  │                                │
│    └──────────────────────────────┘                                │
│    ┌──────── 组B: 监控进程 ────────┐                                │
│    │ CPU: 最多 1 核               │                                │
│    │ 内存: 最多 512MB             │                                │
│    └──────────────────────────────┘                                │
│    互不干扰，各自有资源保障                                          │
└─────────────────────────────────────────────────────────────────────┘
```

### cgroup v1 vs v2

| 维度 | cgroup v1 | cgroup v2 |
|---|---|---|
| 层次结构 | 每个控制器可有独立层次树 | **统一层次树** (unified hierarchy) |
| 挂载 | 多个挂载点，每个挂控制器 | 单一挂载点 `/sys/fs/cgroup` |
| 控制器管理 | 分散，各控制器独立 | 自顶向下，父节点决定子节点可用的控制器 |
| 线程支持 | 不完善 | 支持线程模式 (threaded cgroup) |
| 资源分配模型 | 各控制器不一致 | 统一的 weight/max/min 模型 |
| 内核版本 | 2.6.24 引入 | 4.5 正式发布，5.x+ 推荐默认 |

**Linux 6.1 默认使用 cgroup v2。** 本文聚焦 v2。

---

## 2. cgroup v2 架构原理

### 2.1 统一层次树

```
/sys/fs/cgroup/                     ← 根 cgroup (root)
├── cgroup.controllers              ← 系统可用的控制器列表
├── cgroup.subtree_control          ← 根 cgroup 启用了哪些控制器
├── cgroup.procs                    ← 属于根 cgroup 的进程 PID
│
├── system.slice/                   ← 子 cgroup: 系统服务
│   ├── cgroup.controllers
│   ├── cgroup.procs
│   ├── cpu.weight                  ← CPU 控制器接口
│   ├── memory.max                  ← 内存控制器接口
│   └── sshd.service/              ← 更深的子 cgroup
│       ├── cgroup.procs
│       ├── cpu.weight
│       └── memory.max
│
├── user.slice/                     ← 子 cgroup: 用户会话
│   └── user-1000.slice/
│       └── session-1.scope/
│
└── workload.slice/                 ← 自定义子 cgroup
    ├── cgroup.procs
    ├── cpuset.cpus                 ← cpuset 控制器
    └── memory.max

规则:
  (1) 每个进程恰好属于一个 cgroup
  (2) 子 cgroup 可用的控制器由父 cgroup 的 subtree_control 决定
  (3) 资源限制自顶向下继承
  (4) "no internal process" 规则: 开启了控制器的 cgroup 不能直接
      包含进程，进程必须在叶子节点 (有例外: threaded cgroup)
```

### 2.2 内核数据结构关系

```
┌─────────────────────────────────────────────────────────────────────┐
│                    cgroup v2 核心数据结构                             │
│                                                                     │
│  task_struct (进程)                                                  │
│  ┌────────────────┐                                                 │
│  │ *cgroups ───────────► css_set                                    │
│  └────────────────┘     ┌──────────────────────────┐               │
│                         │ *subsys[cpu]  ──► css(cpu) │               │
│                         │ *subsys[mem]  ──► css(mem) │               │
│                         │ *subsys[io]   ──► css(io)  │               │
│                         │ *subsys[cpuset]──►css(cpus)│               │
│                         │ ...                        │               │
│                         │ *dfl_cgrp ──► cgroup       │               │
│                         │ tasks 链表 (属于此集合的进程)│               │
│                         └──────────────────────────┘               │
│                                    │                                │
│  css (cgroup_subsys_state)         │ 每个 subsys[] 指向:            │
│  ┌────────────────┐                ▼                                │
│  │ *cgroup ────────────► struct cgroup                              │
│  │ *ss ───────────────► cgroup_subsys (cpu/mem/io...)               │
│  │ id, flags            ┌──────────────────────┐                    │
│  │ refcnt               │ level = 2            │                    │
│  └────────────────┘     │ subtree_control      │                    │
│                         │ *kn (kernfs 节点)     │                    │
│                         │ *parent              │                    │
│                         │ children 链表         │                    │
│                         └──────────────────────┘                    │
│                                                                     │
│  关系总结:                                                           │
│  task_struct → css_set → css[] → cgroup                            │
│                                  ↕                                  │
│                          cgroup_subsys (控制器)                      │
│                                                                     │
│  css_set 的作用:                                                     │
│  多个进程如果属于完全相同的 cgroup 组合，共享同一个 css_set            │
│  避免每个进程单独存储所有 css 指针，节省内存                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 控制器启用机制

```
┌─────────────────────────────────────────────────────────────────────┐
│  subtree_control 的传递规则                                          │
│                                                                     │
│  root cgroup                                                        │
│  subtree_control = "+cpu +memory +io +cpuset"                       │
│  │                                                                  │
│  ├── system.slice                                                   │
│  │   cgroup.controllers = "cpu memory io cpuset"  ← 从父继承可用     │
│  │   subtree_control = "+cpu +memory"             ← 只给子启用部分   │
│  │   │                                                              │
│  │   └── sshd.service                                               │
│  │       cgroup.controllers = "cpu memory"        ← 只有父启用的     │
│  │       (这里无法使用 io 和 cpuset，因为父没在 subtree_control 启用) │
│  │                                                                  │
│  └── workload.slice                                                 │
│      cgroup.controllers = "cpu memory io cpuset"                    │
│      subtree_control = "+cpuset"                                    │
│      │                                                              │
│      └── task-group-1                                               │
│          cgroup.controllers = "cpuset"            ← 只有 cpuset     │
│                                                                     │
│  要在子 cgroup 使用某控制器，必须在父 cgroup 的 subtree_control       │
│  中显式启用: echo "+cpu +memory" > cgroup.subtree_control            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 各控制器详解

### 3.1 cpu 控制器

控制进程的 CPU 时间分配。

| 接口文件 | 类型 | 说明 |
|---|---|---|
| `cpu.weight` | 权重 | 范围 1~10000，默认 100。同级 cgroup 按权重比例分配 CPU |
| `cpu.weight.nice` | 权重 | 等价于 nice 值的权重设置 (-20~19) |
| `cpu.max` | 硬限制 | `quota period` 格式，如 `50000 100000` 表示每 100ms 最多用 50ms (50%) |
| `cpu.pressure` | 只读 | PSI (Pressure Stall Information)，显示 CPU 压力 |
| `cpu.stat` | 只读 | 使用统计：usage_usec, user_usec, system_usec 等 |

```bash
# 限制某 cgroup 最多使用 2 个 CPU 核心的时间
echo "200000 100000" > /sys/fs/cgroup/workload/cpu.max
# 含义: 每 100ms(period) 内最多运行 200ms(quota)
# 200ms/100ms = 2 核

# 设置相对权重 (与同级 cgroup 竞争时的比例)
echo 200 > /sys/fs/cgroup/workload/cpu.weight   # 默认100的2倍
```

**与调度器的关系**：cpu 控制器通过 CFS bandwidth throttling 实现硬限制 (`cpu.max`)，通过 CFS 的 `task_group` 和 `sched_entity` 权重实现 `cpu.weight`。每个 cgroup 在 CFS 中表现为一个调度实体层级，与进程的调度实体并列参与 vruntime 竞争。

### 3.2 cpuset 控制器

绑定进程到指定的 CPU 和 NUMA 内存节点。

| 接口文件 | 说明 |
|---|---|
| `cpuset.cpus` | 允许使用的 CPU 列表，如 `0-3,5` |
| `cpuset.cpus.effective` | 实际生效的 CPU（考虑父 cgroup 和 offline CPU） |
| `cpuset.mems` | 允许使用的 NUMA 内存节点，如 `0-1` |
| `cpuset.mems.effective` | 实际生效的内存节点 |
| `cpuset.cpus.partition` | 分区模式：root / member / isolated |

```bash
# 将 workload 限制在 CPU 4-7
echo "4-7" > /sys/fs/cgroup/workload/cpuset.cpus
echo "0"   > /sys/fs/cgroup/workload/cpuset.mems

# 创建独占 CPU 分区 (从父 cgroup 中"拿走" CPU)
echo "root" > /sys/fs/cgroup/workload/cpuset.cpus.partition
# workload 的 CPU 4-7 从父 cgroup 的 effective cpus 中移除
# 其他 cgroup 的进程不会被调度到 CPU 4-7
```

**cpuset.cpus.partition 模式**（v2 新增）：

| 模式 | 含义 |
|---|---|
| `member` (默认) | 普通成员，CPU 与同级 cgroup 共享 |
| `root` | 分区根，独占指定 CPU，从父的 effective 中移除 |
| `isolated` | 独占 + 隔离，等效于 `isolcpus`，不参与负载均衡 |

### 3.3 memory 控制器

控制进程的内存使用。

| 接口文件 | 说明 |
|---|---|
| `memory.current` | 当前内存使用量 (字节) |
| `memory.min` | 内存保障下限（绝对保护，回收时不会低于此值） |
| `memory.low` | 内存保障下限（尽力保护，只在全局内存压力下才可能突破） |
| `memory.high` | 内存使用软限制（超过后进程被节流，触发回收） |
| `memory.max` | 内存使用硬限制（超过后触发 OOM killer） |
| `memory.swap.max` | swap 使用硬限制 |
| `memory.swap.current` | 当前 swap 使用量 |
| `memory.stat` | 详细内存统计 |
| `memory.pressure` | PSI 内存压力信息 |
| `memory.oom.group` | OOM 时是否杀整个 cgroup (0/1) |
| `memory.events` | 事件计数器（low/high/max/oom 触发次数） |

```bash
# 限制最多使用 1GB 内存
echo 1073741824 > /sys/fs/cgroup/workload/memory.max
# 或使用人类可读格式
echo 1G > /sys/fs/cgroup/workload/memory.max

# 保障至少 512MB 内存不被回收
echo 512M > /sys/fs/cgroup/workload/memory.min

# 超过 800MB 开始节流 (触发回收但不 OOM)
echo 800M > /sys/fs/cgroup/workload/memory.high

# OOM 时杀整个 cgroup 而不是单个进程
echo 1 > /sys/fs/cgroup/workload/memory.oom.group
```

**内存限制层次模型**：

```
                    memory.min (硬保障, 绝对不回收)
                        ↑
                    memory.low (软保障, 全局压力下可突破)
                        ↑
  正常使用区域           │
                        ↓
                    memory.high (软限制, 超过则节流)
                        ↓
                    memory.max (硬限制, 超过则 OOM)
```

### 3.4 io 控制器

控制块设备 I/O 带宽。

| 接口文件 | 说明 |
|---|---|
| `io.weight` | I/O 权重，范围 1~10000，默认 100 |
| `io.max` | 按设备的硬限制：`MAJ:MIN rbps=X wbps=Y riops=Z wiops=W` |
| `io.stat` | 每设备的 I/O 统计 |
| `io.pressure` | PSI I/O 压力信息 |
| `io.latency` | 延迟目标（用于 I/O 延迟保障） |

```bash
# 限制 /dev/sda (8:0) 的写带宽为 50MB/s
echo "8:0 wbps=52428800" > /sys/fs/cgroup/workload/io.max

# 限制读 IOPS 为 1000
echo "8:0 riops=1000" > /sys/fs/cgroup/workload/io.max

# 设置 I/O 延迟目标 (保障延迟敏感型负载)
echo "8:0 target=10000" > /sys/fs/cgroup/workload/io.latency
# 10000 微秒 = 10ms
```

### 3.5 pids 控制器

限制 cgroup 内的进程/线程总数，防止 fork bomb。

| 接口文件 | 说明 |
|---|---|
| `pids.max` | 最大进程数，`max` 表示不限制 |
| `pids.current` | 当前进程数 |
| `pids.events` | 被限制拒绝的 fork 次数 |

```bash
echo 100 > /sys/fs/cgroup/workload/pids.max
```

### 3.6 freezer (cgroup.freeze)

v2 中 freezer 不再是独立控制器，而是 cgroup 核心功能。

```bash
# 冻结 cgroup 中所有进程 (SIGSTOP 效果)
echo 1 > /sys/fs/cgroup/workload/cgroup.freeze

# 解冻
echo 0 > /sys/fs/cgroup/workload/cgroup.freeze

# 检查状态
cat /sys/fs/cgroup/workload/cgroup.events
# frozen 1
```

### 3.7 hugetlb 控制器

限制大页内存的使用。

```bash
# 限制 2MB 大页使用量
echo 512M > /sys/fs/cgroup/workload/hugetlb.2MB.max
```

### 3.8 rdma / misc 控制器

- **rdma**：限制 RDMA 资源（如 InfiniBand）
- **misc**：通用杂项资源限制（如 SEV 加密虚拟机数量）

---

## 4. cgroup v2 核心接口文件

每个 cgroup 目录下都有的通用文件：

| 文件 | 说明 |
|---|---|
| `cgroup.type` | cgroup 类型：`domain` (默认) / `threaded` |
| `cgroup.procs` | 读：列出所有进程 PID；写：迁移进程到此 cgroup |
| `cgroup.threads` | 读：列出所有线程 TID；写：迁移线程（threaded 模式） |
| `cgroup.controllers` | 此 cgroup 可用的控制器列表（由父决定） |
| `cgroup.subtree_control` | 为子 cgroup 启用的控制器 |
| `cgroup.events` | 事件：populated (是否有进程)、frozen 等 |
| `cgroup.freeze` | 冻结/解冻此 cgroup |
| `cgroup.kill` | 写 1 杀死此 cgroup 的所有进程 |
| `cgroup.stat` | 统计：nr_descendants, nr_dying_descendants |
| `cgroup.max.descendants` | 允许的最大子 cgroup 数 |
| `cgroup.max.depth` | 允许的最大嵌套深度 |
| `cgroup.pressure` | PSI 压力监控接口 |

---

## 5. 常见使用场景

### 5.1 CPU 隔离 (你的 Thor 平台场景)

```bash
# 创建层次结构
mkdir -p /sys/fs/cgroup/safe.slice
mkdir -p /sys/fs/cgroup/qm.slice

# 启用 cpuset 控制器
echo "+cpuset" > /sys/fs/cgroup/cgroup.subtree_control

# 隔离 CPU 5 给安全关键任务
echo "5" > /sys/fs/cgroup/safe.slice/cpuset.cpus
echo "0" > /sys/fs/cgroup/safe.slice/cpuset.mems

# 其余 CPU 给普通任务
echo "0-4,6-11" > /sys/fs/cgroup/qm.slice/cpuset.cpus
echo "0" > /sys/fs/cgroup/qm.slice/cpuset.mems

# 将进程放入对应 cgroup
echo $PID > /sys/fs/cgroup/safe.slice/cgroup.procs
```

### 5.2 内存限制 (容器场景)

```bash
mkdir /sys/fs/cgroup/container-A
echo "+memory +pids" > /sys/fs/cgroup/cgroup.subtree_control

# 硬限制 2GB 内存 + 1GB swap
echo 2G > /sys/fs/cgroup/container-A/memory.max
echo 1G > /sys/fs/cgroup/container-A/memory.swap.max

# 保障至少 512MB 不被回收
echo 512M > /sys/fs/cgroup/container-A/memory.min

# 超过 1.5G 开始节流
echo 1536M > /sys/fs/cgroup/container-A/memory.high

# 限制最多 500 个进程
echo 500 > /sys/fs/cgroup/container-A/pids.max

# OOM 时杀整个容器
echo 1 > /sys/fs/cgroup/container-A/memory.oom.group
```

### 5.3 I/O 带宽限制

```bash
echo "+io" > /sys/fs/cgroup/cgroup.subtree_control
mkdir /sys/fs/cgroup/background-jobs

# 限制后台任务对 /dev/nvme0n1 (259:0) 的写入为 10MB/s
echo "259:0 wbps=10485760" > /sys/fs/cgroup/background-jobs/io.max

# 设置低权重，让前台任务优先
echo "default 10" > /sys/fs/cgroup/background-jobs/io.weight
```

### 5.4 CPU 带宽限制 (多租户)

```bash
echo "+cpu" > /sys/fs/cgroup/cgroup.subtree_control

mkdir /sys/fs/cgroup/tenant-A
mkdir /sys/fs/cgroup/tenant-B

# A 可用 4 核时间 (每 100ms 给 400ms)
echo "400000 100000" > /sys/fs/cgroup/tenant-A/cpu.max

# B 可用 2 核时间
echo "200000 100000" > /sys/fs/cgroup/tenant-B/cpu.max

# 当 CPU 有空闲时，按 3:1 分配
echo 300 > /sys/fs/cgroup/tenant-A/cpu.weight
echo 100 > /sys/fs/cgroup/tenant-B/cpu.weight
```

### 5.5 组合使用 (systemd 集成)

systemd 原生使用 cgroup v2 管理服务：

```bash
# 查看 systemd 创建的 cgroup 层次
systemd-cgls

# 限制某个 service 的资源
systemctl set-property httpd.service \
    CPUQuota=200%  \
    MemoryMax=2G   \
    MemoryHigh=1.5G \
    IOWriteBandwidthMax="/dev/sda 50M"

# 上述命令等价于写 cgroup 接口文件
# systemd 持久化到 /etc/systemd/system/httpd.service.d/
```

---

## 6. 实现原理 (内核视角)

### 6.1 cgroup 文件系统

cgroup v2 通过 kernfs (基于 sysfs 的虚拟文件系统) 暴露接口：

```
挂载: mount -t cgroup2 none /sys/fs/cgroup

内核实现:
  cgroup_init() 
    → cgroup_init_subsys() × N 个控制器
    → cgroup_setup_root() 
      → kernfs_create_root() 创建 kernfs 根
      → css_populate_dir() 创建各控制器的接口文件

每个接口文件的读写由 struct cftype 定义:
  struct cftype {
      char name[64];           // 文件名如 "cpu.max"
      ssize_t (*write)(...);   // 写处理函数
      int (*seq_show)(...);    // 读处理函数
      unsigned int flags;
      struct cgroup_subsys *ss; // 所属控制器
  };
```

### 6.2 进程与 cgroup 的关联

```
进程迁移到 cgroup 的过程:
  echo $PID > cgroup.procs
    → cgroup_procs_write()
      → cgroup_attach_task()
        → 1. 找到目标 cgroup
          2. 分配或查找匹配的 css_set
          3. 调用每个控制器的 css->attach() 回调
             cpu:    更新 task_group, 重新入队
             memory: 更新 memcg 计费
             cpuset: 更新 cpus_allowed, 可能触发迁移
          4. task->cgroups = new_css_set
          5. 将 task 从旧 css_set->tasks 移到新的
```

### 6.3 资源限制的执行点

```
cpu.max 的执行:
  CFS 调度器的 bandwidth throttling
  scheduler_tick() → update_curr() → account_cfs_rq_runtime()
    → 检查 cfs_bandwidth.quota 是否耗尽
    → 耗尽则 throttle_cfs_rq() 将运行队列摘除
    → 下一个 period 开始时 unthrottle

memory.max 的执行:
  缺页异常 / kmalloc / page cache 分配
    → charge 到 memcg: mem_cgroup_charge()
    → 超过 max → 触发回收 mem_cgroup_reclaim()
    → 回收失败 → OOM killer: mem_cgroup_oom()

cpuset.cpus 的执行:
  cpuset_attach() → set_cpus_allowed_ptr(task, cpuset->cpus_allowed)
    → 修改 task->cpus_mask
    → 如果当前 CPU 不在允许列表 → 触发迁移
  
  调度器 select_task_rq() 只会选择 cpus_mask 允许的 CPU

io.max 的执行:
  块设备层 blk-throttle
  submit_bio() → blk_throtl_bio()
    → 检查当前 cgroup 的 IOPS/BPS 是否超限
    → 超限则将 bio 放入等待队列，延迟发送
```

---

## 7. PSI (Pressure Stall Information)

cgroup v2 集成了 PSI，用于监控资源压力。

```bash
# 查看 CPU 压力
cat /sys/fs/cgroup/workload/cpu.pressure
# some avg10=4.56 avg60=2.34 avg300=1.23 total=567890
# full avg10=0.12 avg60=0.05 avg300=0.02 total=12345

# some: 至少有一个任务因该资源而阻塞的时间百分比
# full: 所有任务都因该资源而阻塞的时间百分比

# 设置 PSI 触发器 (当压力超过阈值时通知)
# 在 500ms 窗口内 some 压力超过 10% 时触发
echo "some 100000 500000" > /sys/fs/cgroup/workload/cpu.pressure
# 然后 poll() 此文件描述符等待通知
```

---

## 8. Delegation (委派)

cgroup v2 支持将子树委派给非 root 用户管理：

```bash
# root 创建 cgroup 并设置控制器
mkdir /sys/fs/cgroup/user-workload
echo "+cpu +memory +pids" > /sys/fs/cgroup/cgroup.subtree_control

# 将 cgroup 目录权限委派给普通用户
chown -R user:user /sys/fs/cgroup/user-workload

# 设置上限 (root 设置，用户不能突破)
echo 2G > /sys/fs/cgroup/user-workload/memory.max
echo 500 > /sys/fs/cgroup/user-workload/pids.max

# 现在普通用户可以在这个子树内自由创建子 cgroup
# 并分配进程，但总量不能超过 root 设置的上限
```

**安全模型**：用户可以在委派的子树内自由操作，但无法：
- 突破父 cgroup 的资源限制
- 启用父 cgroup 未启用的控制器
- 将进程移出委派的子树

---

## 9. threaded 模式

v2 默认一个 cgroup 管理整个线程组（所有线程一起移动）。threaded 模式允许按**单个线程**分配资源：

```bash
# 创建 threaded 子树
mkdir /sys/fs/cgroup/workload/thread-pool
echo "threaded" > /sys/fs/cgroup/workload/thread-pool/cgroup.type

mkdir /sys/fs/cgroup/workload/thread-pool/worker-1
mkdir /sys/fs/cgroup/workload/thread-pool/worker-2

# 将不同线程分配到不同子 cgroup
echo $TID1 > /sys/fs/cgroup/workload/thread-pool/worker-1/cgroup.threads
echo $TID2 > /sys/fs/cgroup/workload/thread-pool/worker-2/cgroup.threads

# 注意: threaded 模式下只有部分控制器可用 (cpu, cpuset)
# memory/io 等 "domain" 控制器不支持线程粒度
```

---

## 10. cgroup 与其他子系统的关联

### 10.1 与调度器

- `cpu.weight` → CFS task_group 权重 → 影响 vruntime 计算
- `cpu.max` → CFS bandwidth throttling → quota/period 控制
- `cpuset.cpus` → task->cpus_mask → select_task_rq 选核范围
- `cpuset.cpus.partition=isolated` → 等效 isolcpus，从调度域移除

### 10.2 与内存管理

- `memory.max` → memcg 计费 → 缺页时 mem_cgroup_charge() 检查
- `memory.min/low` → 页面回收路径 shrink_node() 中保护
- OOM Killer → mem_cgroup_oom() → 在 cgroup 范围内选择 victim
- page cache 也受 memcg 计费控制

### 10.3 与块 I/O

- `io.max` → blk-throttle → submit_bio() 路径中限流
- `io.weight` → BFQ/CFQ I/O 调度器的权重
- writeback → memcg 感知的回写，脏页限制按 cgroup 隔离

### 10.4 与网络

- cgroup v2 没有独立的网络控制器（v1 的 net_cls/net_prio 已废弃）
- 网络限制通过 **eBPF** 实现：
  ```bash
  # 通过 BPF 程序实现 cgroup 级别的网络限速
  bpftool prog attach <prog_id> cgroup /sys/fs/cgroup/workload
  ```

### 10.5 与容器运行时

```
Docker / containerd / CRI-O
    │
    └── 创建容器时:
        1. mkdir /sys/fs/cgroup/system.slice/docker-<id>.scope
        2. 写入资源限制 (memory.max, cpu.max, pids.max...)
        3. 将容器 init 进程写入 cgroup.procs
        4. 容器内 fork 的所有子进程自动继承此 cgroup

Kubernetes Pod 的 resources:
    limits.cpu: "2"      → cpu.max = "200000 100000"
    limits.memory: "1Gi" → memory.max = 1073741824
    requests.cpu: "500m" → cpu.weight (按比例计算)
    requests.memory: "256Mi" → memory.min/low
```

---

## 11. 调试与排查

```bash
# 查看进程所在的 cgroup
cat /proc/$PID/cgroup
# 0::/system.slice/sshd.service

# 查看 cgroup 的内存使用
cat /sys/fs/cgroup/workload/memory.stat

# 查看 cgroup 事件 (OOM 次数等)
cat /sys/fs/cgroup/workload/memory.events

# 查看 cgroup 树
systemd-cgls
# 或
find /sys/fs/cgroup -name cgroup.procs -exec sh -c \
  'echo "--- $(dirname {}) ---"; cat {}' \;

# 查看控制器是否启用
cat /sys/fs/cgroup/cgroup.controllers
cat /sys/fs/cgroup/cgroup.subtree_control

# PSI 监控
cat /sys/fs/cgroup/workload/cpu.pressure
cat /sys/fs/cgroup/workload/memory.pressure
cat /sys/fs/cgroup/workload/io.pressure

# 检查是否使用 v2
mount | grep cgroup
# cgroup2 on /sys/fs/cgroup type cgroup2 (rw,nosuid,nodev,noexec,relatime)
```

---

## 12. 常见注意事项

| 问题 | 说明 |
|---|---|
| No internal process 规则 | 启用了 subtree_control 的 cgroup 不能直接包含进程，进程必须在叶子 cgroup |
| 控制器可用性 | 子 cgroup 的 controllers 由父的 subtree_control 决定，不能凭空使用 |
| 迁移开销 | 将进程写入 cgroup.procs 会触发所有控制器的 attach 回调，有性能开销 |
| memory.max 设太小 | 可能导致 OOM 频繁触发，建议配合 memory.high 做软限制 |
| cpuset.cpus 为空 | 进程无法被调度，进入 D 状态 |
| cgroup 删除 | rmdir 只在 cgroup 无进程且无子 cgroup 时成功，否则需先迁移进程 |
| swap 计费 | 默认 memory.swap.max = max，需显式限制 swap |
| v1/v2 混用 | 同一控制器不能同时在 v1 和 v2 中使用，systemd 默认用 v2 |
