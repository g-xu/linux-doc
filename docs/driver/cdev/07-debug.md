---
title: 调试方法
---

# cdev 07：调试手段

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径。统计限定在 **`fs/char_dev.c`（685 行）**
> + `drivers/base/map.c`（154 行）；边界（devtmpfs / misc / core.c）另行标注。
> 每条统计都给了可重跑的命令。

---

## §0 按症状找章节

| 症状 | 去哪一节 |
|---|---|
| 我想先把该开的选项开全 | §1 |
| dmesg 里什么都没打 | §2（答案是**这个子系统几乎不打日志**） |
| 我想用 ftrace / kprobe 看 open 路径 | §3（答案是**没有 tracepoint**） |
| 我要看设备号占用 / 节点 / 设备号反查 | §4 |
| **我要确认 `cdev_add()` 做了没有** | §4.3（本篇最重要的一节） |
| 我想手工制造一个字符设备来测试 | §5 |
| 拿到一个错误码，想知道是哪一行返回的 | §6 |
| 给我一页速查 | §7 |

---

## §1 编译期开关

### §1.1 没有任何调试选项

```bash
grep -rn 'DEBUG\|debug' fs/char_dev.c drivers/base/map.c include/linux/cdev.h   # → 无输出
grep -n '__setup(\|module_param' fs/char_dev.c                                   # → 无输出
```

**`fs/char_dev.c` 里零调试开关、零命令行参数、零 module_param。**
⇒ 这是本系列里**唯一一个完全没有自己调试设施的子系统**。

### §1.2 真正影响可观测性的四个（都在边界上）

| 选项 | 位置 | 影响 | 怎么确认 |
|---|---|---|---|
| `PROC_FS` | `fs/Kconfig` | `/proc/devices`（§4.1）—— **唯一能看到设备号占用的地方** | `ls /proc/devices` |
| `SYSFS` | `fs/Kconfig` | `/sys/dev/char/`（§4.2） | `ls /sys/dev/char/` |
| `DEVTMPFS` | `drivers/base/Kconfig:31` | dmesg 里那两句 devtmpfs 日志（§2.3） | `dmesg \| grep devtmpfs` |
| `DYNAMIC_DEBUG` | —— | ⚠️ **对本子系统没用** —— `fs/char_dev.c` 里**一处 `pr_debug` 都没有**（§2.1） |

⚠️ **`DYNAMIC_DEBUG` 对本子系统无效**这一条很反直觉，但它是可验证的（§2.1）。

### §1.3 调试字符设备驱动时真正该开的

| 选项 | 为什么 |
|---|---|
| `CONFIG_DEBUG_KOBJECT_RELEASE` | **故意延迟 kobject 的 release** ⇒ 能抓出"提前释放私有数据"的 bug（05 §1.3 那类） |
| `CONFIG_KASAN` | 抓 use-after-free —— 本子系统最常见的 bug 类型（03 §A8.1） |
| `CONFIG_DEBUG_KOBJECT` | 打印每次 kobject 的 get/put（**非常吵**） |
| `CONFIG_PROVE_LOCKING` | 抓 `chrdevs_lock`（mutex）和 `cdev_lock`（spinlock）的锁序问题（03 §B1.2） |
| `CONFIG_DEBUG_SHIRQ` | 和本子系统无关（那是 irq 的） |

**`DEBUG_KOBJECT_RELEASE` 是本子系统最有价值的一个调试选项** ——
因为 cdev 的全部生命周期难点都在 kobject 引用计数上（03 §B3），
而正常情况下"提前释放"这个 bug **只在有并发时才暴露**。

---

## §2 日志：这个子系统几乎不说话

### §2.1 全量普查

| 类别 | `fs/char_dev.c` | `drivers/base/map.c` |
|---|---|---|
| `pr_err` | **3** | **0** |
| `pr_warn` / `pr_info` / `pr_debug` | **各 0** | 各 0 |
| `WARN_ON` | **2** | 0 |
| `WARN(` / `WARN_ONCE` / `BUG_ON` | **各 0** | 各 0 |
| **合计输出点** | **5** | **0** |

```bash
for k in pr_err pr_warn pr_info pr_debug WARN_ON 'WARN(' WARN_ONCE BUG_ON; do
  printf "%-12s char_dev=%-3s map=%s\n" "$k" \
    "$(grep -oh "$k" fs/char_dev.c | wc -l)" "$(grep -oh "$k" drivers/base/map.c | wc -l)"
done
```

**685 行只有 5 个输出点** —— 对比 irq 的 15044 行有 126 个 WARN + 14 个 `pr_err`。
⇒ **这是本系列里最"安静"的子系统。**

### §2.2 那 5 个输出点

| # | 内容 | 位置 | 什么时候出现 |
|---|---|---|---|
| ① | `CHRDEV "%s" major requested (%u) is greater than the maximum (%u)` | `fs/char_dev.c:105` | 静态主设备号 ≥ 512 |
| ② | `CHRDEV "%s" minor range requested (%u-%u) is out of range of maximum range (%u-%u) for a single major` | `fs/char_dev.c:111` | minor 区间越界 |
| ③ | `CHRDEV "%s" dynamic allocation region is full` | `fs/char_dev.c:125` | **动态区全满** |
| ④ | `WARN_ON(dev == WHITEOUT_DEV)` | `fs/char_dev.c:485` | `cdev_add()` 传了设备号 0 |
| ⑤ | `WARN_ON(!kobj->state_initialized)` | `fs/char_dev.c:510` | `cdev_set_parent()` 传了未初始化的 kobject |

**三条 `pr_err` 全部在 `__register_chrdev_region()` 里**（`fs/char_dev.c:97`）——
也就是说**只有"申请设备号"这一步会说话**。

⚠️ **`cdev_add()` / `cdev_device_add()` 成功与失败都几乎不说话**：
唯一的输出是 ④ 那个 `WARN_ON`。
⇒ **这就是"忘了 `cdev_add()`"这个 bug 没有任何日志线索的根本原因**（02 §1.0.A）。

### §2.3 边界上的日志（真正有用的那几条）

| 内容 | 位置 | 含义 |
|---|---|---|
| `devtmpfs: initialized` | `drivers/base/devtmpfs.c:489` | devtmpfs 实例建好、线程起来了 |
| `devtmpfs: mounted` | `drivers/base/devtmpfs.c:381` | `DEVTMPFS_MOUNT` 生效，`/dev` 挂上了 |
| `devtmpfs: error mounting %i` | `drivers/base/devtmpfs.c:379` | 挂 `/dev` 失败 |
| `devtmpfs: unable to create devtmpfs %i` | `drivers/base/devtmpfs.c:483` | **kdevtmpfs 线程起不来** ⇒ 之后所有建节点请求都在 `!thread` 那里**静默返回 0**（`drivers/base/devtmpfs.c:126-127`） |
| `devtmpfs: unable to create devtmpfs %ld` | `drivers/base/devtmpfs.c:463-464` | `vfs_kern_mount()` 失败 |
| `devtmpfs: unable to register devtmpfs type %i` | `drivers/base/devtmpfs.c:469-470` | `register_filesystem()` 失败 |
| `unable to get major %d for misc devices` | `drivers/char/misc.c:286` | 主设备号 10 被占（几乎不可能） |

**启动时第一件事就是确认前两句在不在**：
```sh
dmesg | grep devtmpfs
```
| 输出 | 说明 |
|---|---|
| 两句都有 | devtmpfs 正常工作，`/dev` 由内核维护 |
| 只有 `initialized` | `DEVTMPFS_MOUNT` 没开 ⇒ 得靠 initramfs 挂 |
| 一句都没有 | `CONFIG_DEVTMPFS` 没开 ⇒ 节点全靠 udev 或静态 `mknod` |
| 有 `unable to create devtmpfs %i` | **线程没起来 ⇒ 一个节点都不会出现，而且之后不再报错** |

⚠️ 最后一条是本子系统一个**真实的"报一次然后永远沉默"**：
`devtmpfs_create_node()` 的第一句就是 `if (!thread) return 0;`
（`drivers/base/devtmpfs.c:126-127`）—— **返回 0（成功）**。
⇒ 之后所有 `device_add()` 都以为节点建好了。

---

## §3 tracepoint：一个都没有

### §3.1 判据

```bash
ls include/trace/events/ | grep -iE 'char|cdev|chrdev'                    # → 无输出
grep -rn 'TRACE_EVENT\|CREATE_TRACE_POINTS\|trace_' fs/char_dev.c drivers/base/map.c   # → 无输出
```

**两条命令都空** ⇒ 字符设备层**零 tracepoint**。

相关的 tracepoint 在**别的子系统**里：

| 想看 | 用哪个 tracepoint |
|---|---|
| `open()` / `close()` 系统调用 | `syscalls:sys_enter_openat` / `sys_exit_openat`（需要 `CONFIG_FTRACE_SYSCALLS`） |
| 文件系统层的路径解析 | `filemap:*` / 各文件系统自己的 |
| kobject 的 get/put | **没有 tracepoint** —— 只有 `CONFIG_DEBUG_KOBJECT` 的 printk（§1.3） |

### §3.2 用 kprobe 看 `open` 路径（本子系统的主力手段）

```sh
cd /sys/kernel/tracing

# ① 看 chrdev_open 被调了没有、返回什么
echo 'p:copen chrdev_open' > kprobe_events
echo 'r:coret chrdev_open $retval' >> kprobe_events
echo 1 > events/kprobes/copen/enable
echo 1 > events/kprobes/coret/enable
cat trace_pipe &
cat /dev/mydev0
# → coret: (chrdev_open+0x0/0x...) arg1=0xfffffffffffffffa  ⇒ -ENXIO(-6)? 见下

# ② 直接看 kobj_lookup 有没有命中
echo 'r:klret kobj_lookup $retval' > kprobe_events
echo 1 > events/kprobes/klret/enable
cat /dev/mydev0
# → 返回 0 ⇒ cdev_map 里没有这个设备号（= 忘了 cdev_add）
```

**`kobj_lookup` 的返回值就是 §4.3 那个问题的直接答案**：
返回 NULL ⇒ `cdev_add()` 没做（或做的是别的设备号）。

⚠️`chrdev_open` 是 `static`（`fs/char_dev.c:373`），
但它被 `def_chr_fops.open` 引用（`fs/char_dev.c:453`）所以**不会被内联掉**，
kprobe 能挂上。先确认：`grep chrdev_open /proc/kallsyms`。

`kobj_lookup` 是导出的全局函数（`drivers/base/map.c:95`），一定能挂。

### §3.3 function_graph 看整条 open 链

```sh
cd /sys/kernel/tracing
echo 'chrdev_open' > set_graph_function
echo 'cdev_*'      > set_ftrace_filter
echo 'kobj_*'     >> set_ftrace_filter
echo function_graph > current_tracer
echo 1 > tracing_on
cat /dev/mydev0
echo 0 > tracing_on
cat trace | head -40
```

能看到 `chrdev_open → kobj_lookup → exact_lock → cdev_get` 这条链（03 §A5）。

---

## §4 状态查看

### §4.1 `/proc/devices`：设备号占用

```sh
cat /proc/devices
```

| 元素 | 位置 |
|---|---|
| `chrdev_show()` | `fs/char_dev.c:51` |
| `#ifdef CONFIG_PROC_FS` | `fs/char_dev.c:47` |
| 格式 `"%3d %s\n"` | `fs/char_dev.c:57` |

**三个已知局限**（04 §4.2、§4.3）：

| 局限 | 说明 |
|---|---|
| 它读 `chrdevs[]`，**不是** `cdev_map` | ⇒ **有你的 major ≠ `cdev_add()` 做了** |
| 同一个 major 可能出现多行 | 每个 `char_device_struct` 一行 |
| **不显示 minor 区间** | ⇒ "我的 minor 和谁冲突了"**没有直接答案** |

### §4.2 `/sys/dev/char/` 与 `/sys/class/`

```sh
# 设备号 → 设备
ls -l /sys/dev/char/243:0
# → ../../devices/platform/.../mydev0

# 设备 → 设备号
cat /sys/devices/platform/.../mydev0/dev
# → 243:0

# 这个 class 下有哪些设备
ls -l /sys/class/mydev/

# devtmpfs / udev 会用的那组变量（06 §8.4 的统一判据）
cat /sys/class/mydev/mydev0/uevent
# → MAJOR=243  MINOR=0  DEVNAME=mydev0  DEVMODE=0660
```

| 元素 | 位置 |
|---|---|
| `sysfs_dev_char_kobj` | `drivers/base/core.c:2283`；创建于 `drivers/base/core.c:4158` |
| `device_create_sys_dev_entry()` | `drivers/base/core.c:3514` |
| 名字用 `format_dev_t()` | `drivers/base/core.c:3521` |
| `dev_uevent()`（`uevent` 文件的内容） | `drivers/base/core.c:2566` |

⚠️ **`/sys/dev/char/` 反映的是 `struct device`，不是 `struct cdev`**（04 §5.2）——
所以它**也回答不了** §4.3 那个问题。

### §4.3 怎么确认 `cdev_add()` 做了没有

**这是本子系统最大的可观测性缺口**：
`cdev_map`（`fs/char_dev.c:28`）**没有任何 procfs / sysfs / debugfs 出口**（03 §C5、04 §7）。

**五种绕法，按代价从低到高**：

| # | 办法 | 命令 | 判据 |
|---|---|---|---|
| ① | **直接 `open()`** | `cat /dev/mydev0` | `ENXIO` ⇒ **没做**；其它错误 ⇒ 做了 |
| ② | kprobe 看 `kobj_lookup` 返回值 | §3.2 的 ② | 返回 0 ⇒ 没做 |
| ③ | 用 `mknod` 手工造节点再 open | §5.1 | 排除"节点名/权限"的干扰 |
| ④ | kprobe 看 `cdev_add` 有没有被调 | `echo 'p:ca cdev_add' > kprobe_events` | 完全没触发 ⇒ 代码路径没走到 |
| ⑤ | crash / drgn 直接读 `cdev_map` | 需要调试符号 | 最直接但最重 |

**办法 ① 就够用了，而且是唯一不需要额外配置的**：

```sh
# 节点在、/proc/devices 里有、open 却 ENXIO ⇒ 一定是忘了 cdev_add
cat /dev/mydev0
# cat: /dev/mydev0: No such device or address     ← 这就是 ENXIO
```

⚠️ **`ENXIO` 的四个可能来源全在 `chrdev_open()` 里**（01 §8.1）：

| 来源 | 位置 | 含义 |
|---|---|---|
| `kobj_lookup` 返回 NULL | `fs/char_dev.c:388` | **`cdev_add()` 没做**（最常见） |
| `cdev_get()` 失败（缓存未命中路径） | `fs/char_dev.c:399` | cdev 正在销毁，或模块正在卸载 |
| `cdev_get()` 失败（缓存命中路径） | `fs/char_dev.c:401` | 同上 |
| `fops_get()` 失败 | `fs/char_dev.c:407-409` | **`fops->owner` 那个模块正在卸载** |

⇒ **用办法 ② 的 kprobe 能把这四者区分开**（看 `kobj_lookup` 是否返回 0）。

### §4.4 节点本身

```sh
ls -l /dev/mydev0
# crw-rw---- 1 root mygroup 243, 0 Aug 22 10:00 /dev/mydev0
#  ▲   ▲                     ▲    ▲
#  c   权限（06 §5.2）      major minor

stat -c '%F %t:%T %a' /dev/mydev0
# → character special file f3:0 660      注意 %t/%T 是十六进制
```

⚠️`stat` 的 `%t`/`%T` 是**十六进制** —— `243` 会显示成 `f3`。
用 `ls -l` 看十进制更直观。

---

## §5 手工制造一个字符设备来测试

### §5.1 `mknod`：排除节点侧的干扰

```sh
# 已知设备号，手工造一个节点（不依赖 devtmpfs/udev）
mknod /tmp/testnode c 243 0
cat /tmp/testnode
rm /tmp/testnode
```

**这一步能把问题一刀切成两半**：

| `mknod` 出来的节点能 open | 说明 |
|---|---|
| 能 | `cdev_add()` 做了 ⇒ 问题在**节点侧**（devtmpfs / udev / 名字 / 权限，06 §8） |
| 不能（`ENXIO`） | 问题在**驱动侧**（`cdev_add()` 没做，§4.3） |

⚠️`/tmp` 可能挂了 `nodev` —— 那就换 `/root/` 或一个 tmpfs 之外的目录。

### §5.2 用现成的字符设备验证工具链

```sh
# /dev/null 是主设备号 1、minor 3（06 §4.2）
ls -l /dev/null            # crw-rw-rw- 1 root root 1, 3
grep '^  1 ' /proc/devices  # 1 mem
ls -l /sys/dev/char/1:3     # → ../../devices/virtual/mem/null
```

这三条命令跑通说明 procfs / sysfs / devtmpfs 三条路都正常 ——
再去查你自己的设备就能排除环境问题。

### §5.3 用 misc 设备做最小复现

想验证"字符设备层本身没问题"，最省事的是写一个 20 行的 misc 模块（05 §2.1）：
它把设备号、cdev、class、节点四件事全包了（04 §3.3）。
⇒ 如果 misc 版能工作而你自己的不行，问题一定在你那四步里的某一步。

---

## §6 错误码分诊

### §6.1 全量普查（01 §8.1 的原始数据）

| 错误码 | 次数 | 位置 |
|---|---|---|
| `-ENXIO` | **4** | `fs/char_dev.c:388` · `:399` · `:401` · `:407` |
| `-EBUSY` | **3** | `fs/char_dev.c:85` · `:132` · `:487` |
| `-EINVAL` | 2 | `fs/char_dev.c:107` · `:113` |
| `-ENOMEM` | 2 | `fs/char_dev.c:118` · `:274` |
| **合计** | **11** | —— |
| `drivers/base/map.c` | **1**（`-ENOMEM`，`drivers/base/map.c:46`） | —— |

```bash
grep -on '\-E[A-Z_]*' fs/char_dev.c
grep -on '\-E[A-Z_]*' drivers/base/map.c
```

**11 个点、4 种码、语义完全不重叠** —— 这是本系列里错误码信息量最高的子系统。
⇒ **拿到错误码基本就知道是哪一行**（对比 irq 的 `-EINVAL` 占 58%）。

### §6.2 四个 `-ENXIO` 的区分

见 §4.3 那张表。用 kprobe 看 `kobj_lookup` 的返回值就能区分第一个和后三个。

### §6.3 三个 `-EBUSY` 的区分

| 位置 | 触发条件 | 有日志？ |
|---|---|---|
| `fs/char_dev.c:85` | **动态分配区全满**（`find_dynamic_major()` 返回） | **有** —— `fs/char_dev.c:125` |
| `fs/char_dev.c:132` | **你要的区间被占**（`__register_chrdev_region()` 的重叠判断） | **没有** |
| `fs/char_dev.c:487` | `cdev_add()` 传了设备号 0 | 有 `WARN_ON`（`fs/char_dev.c:485`） |

⇒ **`-EBUSY` + dmesg 干净 = 区间冲突** ⇒ `cat /proc/devices` 看是谁（§4.1）。

### §6.4 高频错误 → 第一嫌疑

| 拿到 | 第一嫌疑 | 怎么确认 |
|---|---|---|
| `open()` → `ENXIO` | **忘了 `cdev_add()` / `cdev_device_add()`** | §4.3 办法 ① |
| `open()` → `EACCES` | devtmpfs 默认 0600 | `ls -l /dev/…`（06 §5.2 给改法） |
| `open()` → `ENOENT` | 节点不存在 | `ls /sys/class/…/`（06 §8.2 第 3 步） |
| `alloc_chrdev_region()` → `-EBUSY` | 看 dmesg 分辨"区满"还是"冲突" | §6.3 |
| `-EINVAL` | 主设备号 ≥ 512 或 minor 区间越界 | **两条都有 `pr_err`** |
| `cdev_add()` → `-EBUSY` | 传了 `dev == 0` | 看 `WARN_ON` 的栈回溯 |
| `open()` → `ENODEV` | **不是本子系统返回的** —— 你的 `fops->open` 自己返回的 | grep 你自己的代码 |
| `ioctl()` → `ENOTTY` | 你的 ioctl 不认识那个 cmd（05 §1.4） | —— |
| 模块卸载后崩溃 | `fops.owner` 或 `cdev.owner` 没填 | 05 §1.4 · 03 §B3 |
| remove 后 use-after-free | 私有数据用了 devm，或没设 `device.release` | 开 `DEBUG_KOBJECT_RELEASE` + KASAN（§1.3） |

---

## §7 一页速查

```
┌─ 三步定位（按顺序做）─────────────────────────────────┐
│ ① cat /proc/devices | grep <你的名字>                       │
│      有 ⇒ alloc_chrdev_region 成功                          │
│      ⚠️ 但不代表 cdev_add 做了！                             │
│ ② ls -l /dev/<你的节点>  +  ls /sys/class/<你的class>/       │
│      都有 ⇒ class_create/device_create 成功                  │
│ ③ cat /dev/<你的节点>                                        │
│      ENXIO  ⇒ 忘了 cdev_add()                           │
│      EACCES ⇒ 权限（devtmpfs 默认 0600）                     │
│      ENOENT ⇒ 节点不存在（回到 ②）                            │
└────────────────────────────────────────────────────────────┘

┌─ 设备号 ↔ 设备的反查 ──────────────────────────────────────┐
│ ls -l /sys/dev/char/<major>:<minor>                         │
│ cat  /sys/devices/.../<name>/dev                            │
│ cat  /sys/class/<class>/<name>/uevent   ← DEVNAME / DEVMODE  │
└────────────────────────────────────────────────────────────┘

┌─ devtmpfs 在工作吗 ────────────────────────────────────────┐
│ dmesg | grep devtmpfs                                       │
│   "initialized" + "mounted" ⇒ 正常                          │
│   只有 initialized          ⇒ DEVTMPFS_MOUNT 没开            │
│   一句都没有                ⇒ CONFIG_DEVTMPFS 没开           │
│   "unable to create devtmpfs %i" ⇒ 线程没起来，之后全静默 │
└────────────────────────────────────────────────────────────┘

┌─ 没有 tracepoint，用 kprobe ──────────────────────────┐
│ cd /sys/kernel/tracing                                      │
│ echo 'r:klret kobj_lookup $retval' > kprobe_events          │
│ echo 1 > events/kprobes/klret/enable                        │
│ cat /dev/<你的节点> ; cat trace_pipe                         │
│   返回 0 ⇒ cdev_map 里没有这个设备号                     │
└────────────────────────────────────────────────────────────┘

┌─ 手工造节点（排除节点侧干扰）──────────────────────────────┐
│ mknod /root/tn c <major> <minor> ; cat /root/tn ; rm /root/tn │
│   能开 ⇒ 问题在节点侧（devtmpfs/udev/名字/权限）             │
│   ENXIO ⇒ 问题在驱动侧（cdev_add）                           │
└────────────────────────────────────────────────────────────┘

┌─ 调生命周期 bug 时该开的选项 ─────────────────────────────┐
│ CONFIG_DEBUG_KOBJECT_RELEASE  故意延迟 release          │
│ CONFIG_KASAN                  抓 use-after-free             │
│ CONFIG_PROVE_LOCKING          抓锁序                        │
└────────────────────────────────────────────────────────────┘
```

### §7.1 错误码 → 第一嫌疑（压缩版）

| 拿到 | 先查 |
|---|---|
| `ENXIO` | **`cdev_add()` 忘了**（4 个来源全在 `chrdev_open()`，§6.2） |
| `EACCES` | devtmpfs 的 0600 |
| `ENOENT` | 节点不存在 |
| `-EBUSY` + **有** dmesg | 动态区满 |
| `-EBUSY` + **无** dmesg | 区间冲突 ⇒ `/proc/devices` |
| `-EINVAL` | major ≥ 512 或 minor 越界（**都有日志**） |
| `ENODEV` / `ENOTTY` | 你自己的代码返回的 |

### §7.2 这个子系统调试的三条纪律

| 纪律 | 为什么 |
|---|---|
| **别指望日志** | 685 行只有 5 个输出点，而且全在"申请设备号"那一步（§2.2） |
| **`cdev_map` 没有出口，用 `open()` 的返回值反推** | §4.3 |
| **生命周期 bug 只在并发下暴露，必须开 `DEBUG_KOBJECT_RELEASE`** | §1.3 |

---

**下一篇**：08 篇用大白话和类比解释这个子系统里最难理解的几组关系。
