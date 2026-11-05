---
title: 取证附录
---

# cdev 99：取证、方法与未验证项

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> 本篇的定位：**让读者知道哪些结论可以直接用、哪些要自己再确认一遍**。
> 所有数字都配了可重跑的一条命令；每轮 review 后全部重跑。
>
> ⚠️ 提到别处的错误引用时**用文字描述，不写成 `文件:行号` 的形式**，
> 否则会污染本篇自己的引用集（§8.4）。

---

## §1 基线

### §1.1 树的状态：全部干净

| 项 | 值 |
|---|---|
| HEAD | `f6e38ae624cf` "Linux 6.1.158" |
| 分支 | `linux-6.1.158` |

```bash
git status --porcelain -- \
  fs/char_dev.c include/linux/cdev.h include/linux/kdev_t.h \
  drivers/base/map.c drivers/base/devtmpfs.c \
  drivers/char/misc.c include/linux/miscdevice.h \
  include/linux/fs.h drivers/base/core.c \
  Documentation/admin-guide/devices.txt
# → 无输出
```

**本套涉及的十条路径全部干净** ⇒ 描述的是**上游 6.1.158 的原样代码**。

⚠️ **对比 irq 那一轮**：那套的 `include/linux/interrupt.h` 有 29 行 PREEMPT_RT 本地补丁。
⇒ **本套没有这个问题**，`interrupt.h` 也不在本套的引用范围内。

顺带说清：整棵树有大量本地修改（`git status` 顶层输出上百行），
但**本套的十条路径一处未改**。

### §1.2 规模

| 文件 | 行数 | 本套的证据等级 |
|---|---|---|
| `fs/char_dev.c` | **685** | **一等（全文逐行读）** |
| `include/linux/cdev.h` | **39** | **一等（全文）** |
| `drivers/base/map.c` | **154** | **一等（全文逐行读）** |
| `include/linux/kdev_t.h` | 83 | 一等（宏定义部分逐字） |
| `include/uapi/linux/kdev_t.h` | **14** | **一等（全文）** |
| `drivers/base/devtmpfs.c` | 491 | 二等（读了 `struct req`、提交路径、线程循环、init；**没读 `handle_create` / `handle_remove` 的主体**） |
| `drivers/char/misc.c` | 293 | 二等（读了函数签名和关键行；**没读 `misc_open` 的主体**） |
| `include/linux/miscdevice.h` | 111 | 一等（`struct miscdevice` 逐字段 + 常量） |
| `include/linux/fs.h` | 3746 | 二等（只读了 `inode` 的四个相关字段、`def_chr_fops` 声明、`CHRDEV_MAJOR_*` 常量、`register_chrdev` inline） |
| `drivers/base/core.c` | —— | 三等（只 grep 了 `dev_uevent` / `device_create_sys_dev_entry` / `device_get_devnode` 的行号，**一个函数体都没读**） |
| `Documentation/admin-guide/devices.txt` | 3086 | 三等（只读了前 31 行看格式） |
| `fs/file_table.c` | —— | 二等（**读了 `__fput()` 的 319-325 行** —— §6.1 的关键） |
| `fs/inode.c` | —— | 三等（只确认 `cd_forget()` 的调用点） |
| `drivers/i2c/i2c-dev.c` | 782 | 三等（只 grep 了 cdev 相关的 15 行，作为 05 §1 的原型） |
| `drivers/counter/counter-core.c` | 282 | 三等（只 grep 了 5 行） |

| 项 | 值 | 命令 |
|---|---|---|
| **核心合计** | **961**（685 + 39 + 83 + 154） | `wc -l fs/char_dev.c include/linux/cdev.h include/linux/kdev_t.h drivers/base/map.c` |
| 边界合计 | 895（491 + 293 + 111） | `wc -l drivers/base/devtmpfs.c drivers/char/misc.c include/linux/miscdevice.h` |
| 导出符号（`fs/char_dev.c`） | **12**（全部是 `EXPORT_SYMBOL`，**零 `_GPL`**） | `grep -rho 'EXPORT_SYMBOL[_A-Z]*' fs/char_dev.c \| sort \| uniq -c` |
| `drivers/base/map.c` 导出 | **0** | `grep -c EXPORT_SYMBOL drivers/base/map.c` |
| `drivers/char/misc.c` 导出 | 2（`misc_register` / `misc_deregister`） | 同上换文件 |

**"12 个全是 `EXPORT_SYMBOL` 而不是 `_GPL`"是一个可验证的历史痕迹** ——
这套 API 早于 `EXPORT_SYMBOL_GPL` 的普及。

### §1.3 子系统类型判定

| 判据 | 命令 | 结果 |
|---|---|---|
| 有 `struct bus_type` 吗 | `grep -c 'struct bus_type' fs/char_dev.c include/linux/cdev.h drivers/base/map.c` | **全为 0** |
| 读 DT 吗 | `grep -rn 'of_property_read\|of_get_property\|of_parse_phandle\|device_node' fs/char_dev.c include/linux/cdev.h drivers/base/map.c` | **无输出** |
| 有 DT 绑定吗 | `ls Documentation/devicetree/bindings/ \| grep -i 'chrdev\|char_dev\|cdev'` | **无输出** |
| 有 tracepoint 吗 | `ls include/trace/events/ \| grep -iE 'char\|cdev\|chrdev'` | **无输出** |
| 有 devres 吗 | `grep -rn 'devm_.*cdev\|cdev.*devm_' include/linux/cdev.h include/linux/device.h` | **无输出** |

⇒ **判定为「框架型」子系统**，但它比 remoteproc / irq **更底层**：

| 层面 | 说明 |
|---|---|
| 它不抽象硬件 | 它抽象的是**"设备号 → fops"这个映射** |
| 它是 VFS 的一部分 | 代码在 `fs/` 下，不在 `drivers/` 下 |
| 它完全不知道设备树 | 06 §1.1 |

按 skill 的适配规则：
02 篇画"核心抽象 ↔ 三方（VFS / 驱动 / 用户态）"图；
04 篇按六个独立框架组织；
**06 篇改讲替代配置机制**（Kconfig / 命令行 / 设备号申请 / `devnode` 回调 / udev）。

### §1.4 官方文档登记：它没有框架文档

| 文件 | 行数 | 读了吗 | 内容纳入了哪里 |
|---|---|---|---|
| `Documentation/admin-guide/devices.txt` | **3086** | 只读了**前 31 行**看格式 | 06 §4.2 |
| `Documentation/filesystems/` 下有字符设备的框架文档吗 | —— | **没有** | —— |
| `Documentation/driver-api/` 下有吗 | —— | **没有** | —— |

```bash
grep -rl 'cdev_add\|cdev_device_add\|alloc_chrdev_region' Documentation/ 2>/dev/null
# → 只有零散提到的驱动文档（如 Documentation/misc-devices/uacce.rst），
#   没有一篇讲字符设备层本身
```

**这是本子系统最大的文档缺口**（03 §C5）：
三条决定驱动对错的语义（00 §1.5）**只存在于源码注释里**：

| # | 注释 | 位置 |
|---|---|---|
| ① | `cdev_del()` 之后 fops 仍可调 | `fs/char_dev.c:590-592` |
| ② | `cdev_device_add()` 失败也可能已被打开 | `fs/char_dev.c:534-535` |
| ③ | `cdev_set_parent()` 必须在 `cdev_add()` 之前 | `fs/char_dev.c:501-507` |

⇒ **本套 00 §1.5、03 §A8.1、05 §1.3、08 §4 §5 这几节，
完全是从这三条注释推出来的** ⇒ 列入 §5.3「推断而非观察」。

### §1.5 ⚠️ 文档与代码的冲突

| 主题 | 文档 | 代码现状 |
|---|---|---|
| `Documentation/admin-guide/devices.txt` 的动态区说明 | 它列的是 1–255 的静态分配 | 实际有**两个动态区**（234–254 + 384–511，`include/linux/fs.h:2898-2901`）—— 文档里没有这两个区间的说明 |
| uapi 的 `MAJOR`/`MINOR` 宏 | 注释说"These must be the externally visible ones" | **它是 8+8 位的老定义**（`include/uapi/linux/kdev_t.h:10-12`），和内核内部的 12+20 位**不一致**（§6.2） |

---

## §2 证据等级

### §2.1 一等证据：全文逐行读过

| 文件 | 说明 |
|---|---|
| `fs/char_dev.c`（685 行） | **全文** —— 每个函数、每个 `pr_err`、每个 `WARN_ON`、三条 `NOTE:` 注释都逐行读过 |
| `drivers/base/map.c`（154 行） | **全文** —— 含 `kobj_map` / `kobj_unmap` / `kobj_lookup` / `kobj_map_init` 四个函数体和文件头那条 20 年的注释 |
| `include/linux/cdev.h`（39 行） | **全文** |
| `include/uapi/linux/kdev_t.h`（14 行） | **全文** |
| `include/linux/kdev_t.h` | 宏定义部分（1-30）逐字；老式转换辅助只读了签名 |
| `include/linux/miscdevice.h` | `struct miscdevice`（79-89）逐字段 + `MISC_DYNAMIC_MINOR`(74) + `MODULE_ALIAS` 宏(109) |
| `fs/file_table.c` | **`__fput()` 的 310-325 行逐行**（§6.1 的关键证据） |
| `fs/inode.c` | `evict()` 里那两行（710-711） |

**961 行的核心里有 878 行（685+154+39）是"全文逐行"** ——
这是本系列里**一等证据比例最高的一套**。

### §2.2 二等证据：只读了片段

| 对象 | 读到什么程度 | 影响哪些论断 |
|---|---|---|
| `drivers/base/devtmpfs.c` | 读了：`DEVTMPFS_MFLAGS`(33/35) · `thread`(38) · `mount_dev`(40) · `req_lock`(42) · `struct req`(44-53) · `mount_param`(55-60) · `devtmpfs_submit_req`(104-119) · `devtmpfs_create_node`(121-146) · `devtmpfs_delete_node`(148) · `handle`(387-394) · `devtmpfs_work_loop`(396-417) · `devtmpfsd`(441) · `devtmpfs_init`(456-490)。**没读 `handle_create` / `handle_remove` / `dev_mkdir` 的主体** | 04 §1 · 00 §2.7 |
| `drivers/char/misc.c` | 读了全部函数签名 + 关键行（100/119/151/175/179/205/216/231/241/250/255/280/286）。**没读 `misc_open()` 的主体** —— 04 §3.2 那句"第二次 `replace_fops`"是从函数名和上下文推的（§5.3） | 04 §3 · 05 §2 · 08 §8 |
| `include/linux/fs.h`（3746 行） | 只读了：`i_mapping`(609) · `i_rdev`(628) · `i_data`(680) · `i_devices`(681) · union(682-687) · `def_chr_fops` 声明(2893) · `CHRDEV_MAJOR_*`(2896-2901) · 设备号 API 声明(2904-2921) · `noop_llseek`/`no_llseek`(3304-3305) · `nonseekable_open`/`stream_open`(3316-3317) | 01 §5 · 02 §5.4 |

### §2.3 三等证据：只 grep 了行号

| 对象 | 说明 | ⚠️ 风险 |
|---|---|---|
| `drivers/base/core.c` | **一个函数体都没读** —— 只 grep 了 `dev_uevent`(2566) · `MAJOR=`/`MINOR=`/`DEVNAME=`/`DEVMODE=`(2579-2585) · `sysfs_dev_char_kobj`(2283/4158) · `device_create_sys_dev_entry`(3514/3521) · `device_get_devnode`(3972/3981-3988) 等行号 | **高** —— 04 §2 §5 和 06 §5 的机制描述**建立在函数名和 grep 到的那几行上**（§5.2） |
| `Documentation/admin-guide/devices.txt` | 只读前 31 行 | 低（只用来说明格式） |
| `drivers/i2c/i2c-dev.c` | 只 grep 了 cdev 相关的 15 行（46/94-95/626-627/663-676/762/773） | 中 —— 作为 05 §1 的原型，**行号可信、上下文未读** |
| `drivers/counter/counter-core.c` | 只 grep 了 5 行（116/167/182/259） | 中 |
| `drivers/gpio/gpio-pca953x.c` 等 | 本套**没用到**（那是 irq 那一轮的） | —— |

⚠️ **`drivers/base/core.c` 是本套最大的证据薄弱点** ——
它承载了 04 §2（uevent）、04 §5（sysfs 三出口）、06 §5（`devnode` 回调）三节的机制。
⇒ 缓解：**这三节的每一条都给了行号**，读者可以自己 `sed` 打开核对。

### §2.4 ⚠️ `check_doc_coverage.sh` 的处置

**本子系统没有对应的 `Documentation/` 子目录**（§1.4）
⇒ 脚本的第一个参数无从填写。

| 尝试 | 结果 |
|---|---|
| `admin-guide` | 会把整个 `Documentation/admin-guide/` 当"必查"（几十篇无关文档）⇒ 大量 0 引用，全部是 **(c) 与本子系统无关** |
| `filesystems` | 同样的问题 |

⇒ **处置：不跑这个脚本，改为在 §1.4 手工登记** ——
理由是它按目录取名，而本子系统的文档是**一个孤立的 `.txt`**，
不在任何"属于它"的目录里（这一条已记进 §8.1）。

### §2.5 索引工具的使用与盲区

| 用途 | 用了吗 |
|---|---|
| 导航 | 用了 |
| 取证 | **没用** —— 所有行号都是 `sed -n` / `grep -n` 在真实树上取的 |

本子系统撞上的**两个盲区**：

| 盲区 | 本子系统的体现 |
|---|---|
| **函数指针间接调用** | `cdev->ops` 的每个回调、`probe->get`/`probe->lock`、`class->devnode`、`device_type->devnode` **全是间接调用** |
| **同名宏在两个头文件里定义不同** | `MAJOR`/`MINOR`/`MKDEV` 在 `include/linux/kdev_t.h:10-12`（12+20 位）和 `include/uapi/linux/kdev_t.h:10-12`（8+8 位）**各有一套**（§6.2）—— 索引会给出两个定义，必须自己判断 `__KERNEL__` 分支 |

### §2.6 全称断言的验证

| 论断 | 命令 | 结果 |
|---|---|---|
| 没有 `bus_type` | 见 §1.3 | 全 0 |
| 不读 DT、没有 DT 绑定 | 见 §1.3 | 无输出 |
| 零 tracepoint | 见 §1.3 + `grep -rn 'TRACE_EVENT\|trace_' fs/char_dev.c drivers/base/map.c` | 无输出 |
| 零 devres | 见 §1.3 | 无输出 |
| 零调试选项 / 零命令行参数 | `grep -rn 'DEBUG\|debug' fs/char_dev.c drivers/base/map.c include/linux/cdev.h` · `grep -n '__setup(\|module_param' fs/char_dev.c` | **都无输出** |
| 零 `pr_debug` / 零 `pr_warn` / 零 `pr_info` | 07 §2.1 的循环 | 全 0 |
| 零 `TODO`/`FIXME`/`XXX` | `grep -n 'TODO\|FIXME\|XXX\|REVISIT\|HACK' fs/char_dev.c drivers/base/map.c include/linux/cdev.h` | 无（只有 `/* will die */` 和一条 `NOTE:`） |
| `cdev_map` 只在一个文件里出现 | `grep -rn 'cdev_map' --include=*.c --include=*.h . \| grep -v '^./fs/char_dev.c'` | **无输出** |
| `cdev_put` 的唯一外部调用者 | `grep -rn 'cdev_put' --include=*.c .` | **`fs/file_table.c:323`**（§6.1） |
| `cd_forget` 的唯一调用者 | `grep -rn 'cd_forget' --include=*.c .` | `fs/inode.c:711` |
| 12 个导出符号且全是非 GPL | `grep -rho 'EXPORT_SYMBOL[_A-Z]*' fs/char_dev.c \| sort \| uniq -c` | `12 EXPORT_SYMBOL` |

### §2.7 裁掉了什么

| 裁掉的内容 | 为什么 | 影响哪一篇 |
|---|---|---|
| **块设备** | 虽然共享设备号编号习惯，但走 `blkdev` 那一套（另一个查找表、另一套 sysfs 出口） | 00 §7.4 · 05 §7 已标注 |
| **VFS 的其余部分** | 路径解析、dentry、inode 的产生 —— 只讲到 `def_chr_fops` 这个交接点 | 03 §0.4 已标注 |
| **udev 本身** | 在内核树外 | 04 §2 · 06 §6 只讲"内核提供了什么" |
| **`handle_create` / `handle_remove` 的实现** | devtmpfs 内部的文件系统操作 | 04 §1 已标注为二等证据 |
| **tty 层** | 它是和 misc 同构的第二个二级分配器，但代码量大得多 | 00 §7.2 只提一句 |
| **ACPI** | 本套 arm64-only；而且**字符设备层和 ACPI 也没关系**（不像 irq 有 MADT 路径） | 06 §10 已标注 |
| **`Documentation/admin-guide/devices.txt` 的 3086 行内容** | 它是一张分配表，不是机制文档 | 06 §4.2 只讲格式 |

⚠️ **本套的裁剪比 irq / remoteproc 少** ——
因为核心只有 961 行，几乎全读了（§2.1）。
⇒ 真正的薄弱点不是"裁掉的"，而是**边界的证据等级**（§2.3）。

---

## §3 统计数字总表（每条都能一条命令重跑）

### §3.1 规模类

| 数字 | 值 | 命令 |
|---|---|---|
| 核心合计 | **961** | `wc -l fs/char_dev.c include/linux/cdev.h include/linux/kdev_t.h drivers/base/map.c` |
| `fs/char_dev.c` | 685 | 同上 |
| `include/linux/cdev.h` | **39** | 同上 |
| `drivers/base/map.c` | 154 | 同上 |
| `include/linux/kdev_t.h` | 83 | 同上 |
| `include/uapi/linux/kdev_t.h` | **14** | `wc -l include/uapi/linux/kdev_t.h` |
| `drivers/base/devtmpfs.c` | 491 | `wc -l drivers/base/devtmpfs.c` |
| `drivers/char/misc.c` / `include/linux/miscdevice.h` | 293 / 111 | 同上 |
| `Documentation/admin-guide/devices.txt` | 3086 | 同上 |
| DT 绑定 | **0** | §1.3 |

**对比本系列**：

| 子系统 | 核心行数 | 一等证据比例 |
|---|---|---|
| irq | 15044 | 关键路径逐段 |
| remoteproc | 5068 | 七个 `.c` 里五个全文 |
| **cdev** | **961** | **878/961 = 91% 全文逐行** |

### §3.2 API 类

| 数字 | 值 | 命令 |
|---|---|---|
| 导出符号 | **12**（全部 `EXPORT_SYMBOL`，零 `_GPL`） | `grep -rho 'EXPORT_SYMBOL[_A-Z]*' fs/char_dev.c \| sort \| uniq -c` |
| `struct cdev` 字段数 | **6** | `sed -n '14,21p' include/linux/cdev.h` |
| `cdev.h` 声明的函数 | **9** | `grep -c '^\(void\|int\|struct\)' include/linux/cdev.h` |
| 声明了但**不导出**的 | 3（`cdev_put` / `cd_forget` / `chrdev_show`） | 01 §0.3 |
| `struct miscdevice` 字段数 | **10** | `sed -n '79,89p' include/linux/miscdevice.h` |

### §3.3 常量类

| 常量 | 值 | 位置 |
|---|---|---|
| `MINORBITS` | 20 | `include/linux/kdev_t.h:7` |
| ⇒ major / minor 范围 | 0–4095 / 0–1048575 | —— |
| uapi 的 `MAJOR` 位移 | **8**（老定义！） | `include/uapi/linux/kdev_t.h:10` |
| `CHRDEV_MAJOR_MAX` | 512 | `include/linux/fs.h:2896` |
| `CHRDEV_MAJOR_DYN_END` | 234 | `include/linux/fs.h:2898` |
| `CHRDEV_MAJOR_DYN_EXT_START` / `_END` | 511 / 384 | `include/linux/fs.h:2900-2901` |
| `CHRDEV_MAJOR_HASH_SIZE` | **255** | `fs/char_dev.c:32` |
| `kobj_map` 的桶数 | **255** | `drivers/base/map.c:28` |
| `MISC_DYNAMIC_MINOR` | 255 | `include/linux/miscdevice.h:74` |
| `char_device_struct.name` 长度 | 64 | `fs/char_dev.c:39` |
| devtmpfs 默认权限 | **0600** | `drivers/base/devtmpfs.c:137` |

**`CHRDEV_MAJOR_HASH_SIZE`(255) < `CHRDEV_MAJOR_MAX`(512)** 是本子系统一个真实的复杂度来源
（03 §A2.1 的"两个动态区扫描方式不同"）。

### §3.4 错误码类

| 错误码 | 次数 | 位置 |
|---|---|---|
| `-ENXIO` | **4** | `fs/char_dev.c:388` · `:399` · `:401` · `:407` |
| `-EBUSY` | **3** | `fs/char_dev.c:85` · `:132` · `:487` |
| `-EINVAL` | 2 | `fs/char_dev.c:107` · `:113` |
| `-ENOMEM` | 2 | `fs/char_dev.c:118` · `:274` |
| **`fs/char_dev.c` 合计** | **11** | —— |
| `drivers/base/map.c` | **1**（`-ENOMEM`，`drivers/base/map.c:46`） | —— |

```bash
grep -on '\-E[A-Z_]*' fs/char_dev.c
grep -on '\-E[A-Z_]*' drivers/base/map.c
```

**11 个点、4 种码、语义完全不重叠** —— 本系列里信息量最高的一套
（对比 irq 的 `-EINVAL` 占 58%）。

### §3.5 可观测性类

| 项 | 值 |
|---|---|
| `pr_err` | **3**（全在 `__register_chrdev_region()` 里） |
| `pr_warn` / `pr_info` / `pr_debug` | **各 0** |
| `WARN_ON` | **2**（`fs/char_dev.c:485` · `:510`） |
| `WARN(` / `WARN_ONCE` / `BUG_ON` | **各 0** |
| **输出点合计** | **5** |
| `drivers/base/map.c` 输出点 | **0** |
| tracepoint | **0** |
| debugfs / sysfs / procfs 出口（`cdev_map`） | **0** |
| procfs 出口（`chrdevs[]`） | 1（`/proc/devices`） |

```bash
for k in pr_err pr_warn pr_info pr_debug WARN_ON 'WARN(' WARN_ONCE BUG_ON; do
  printf "%-12s char_dev=%-3s map=%s\n" "$k" \
    "$(grep -oh "$k" fs/char_dev.c | wc -l)" "$(grep -oh "$k" drivers/base/map.c | wc -l)"
done
```

**685 行只有 5 个输出点** —— 本系列里最"安静"的子系统
（对比 irq 的 126 个 WARN + 14 个 `pr_err`）。

### §3.6 源码设计债类

| 标记 | 个数 | 位置 |
|---|---|---|
| `TODO` / `FIXME` / `XXX` / `REVISIT` / `HACK` | **0** | —— |
| `/* will die */` | **1** | `fs/char_dev.c:40` |
| `NOTE: data structure needs to be changed` | **1** | `drivers/base/map.c:7` |
| `NOTE:`（文档性质） | 3 | `fs/char_dev.c:534` · `:567` · `:590` |

```bash
grep -n 'TODO\|FIXME\|XXX\|REVISIT\|HACK\|will die\|NOTE:' \
     fs/char_dev.c drivers/base/map.c include/linux/cdev.h
```

**零 `FIXME`，但有两条"换了说法的技术债"**（03 §C2 · §C3）。

---

## §4 工具版本

| 工具 | 版本 | ⚠️ 注意 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本纯 stdlib |
| GNU grep | 3.7（`/usr/bin/grep`） | 本套所有统计都用**绝对路径**调它 |
| 默认 `grep` | **ugrep 7.5.0** | ⚠️ **不认 `\s`**，**默认遵守 `.gitignore`** |
| ripgrep | 13.0.0 | 只用于粗筛 |
| git | 2.34.1 | —— |

⚠️ ugrep 那两条差异是本套踩过最多次的环境坑（沿用前几轮结论，§8.7）。

---

## §5 未验证项（四层分层）

### §5.1 第一层：未编译、未运行

| 内容 | 说明 |
|---|---|
| **05 篇的全部四套代码骨架** | 篇首已声明，**没有 `make` 过一次** |
| 07 篇的**全部** shell / kprobe / ftrace 命令 | **没有在真机上跑过**。路径和函数名来自源码，语法来自内核文档 |
| 07 §3.2 的 kprobe 输出示例 | **那个 `arg1=0xffff…fa` 的返回值示例是构造的**，不是实测抓到的 |
| 06 §6 的 udev 规则语法 | **未实测** |
| 06 §4.3 的"静态主设备号申请流程" | **只记了文档做法（给 `devices.txt` 提补丁）**，没有实际走过 |
| 07 §5 的 `mknod` / `stat` 命令 | 未运行 |
| 00 §2.6 的"四代 `/dev` 方案"历史 | 前三代（静态 / devfs / udev）**不在本树里**，来自一般历史知识（§5.3） |

### §5.2 第二层：只读了片段

见 §2.2 和 §2.3。**受影响最大的三处**：

| 论断 | 依赖的阅读深度 | 风险 |
|---|---|---|
| 04 §2「uevent 里有 `MAJOR=`/`MINOR=`/`DEVNAME=`/`DEVMODE=`」 | **只 grep 了 `drivers/base/core.c:2579-2585` 那四行 `add_uevent_var`，没读 `dev_uevent()` 的完整逻辑** | 中 —— 四行是逐字读的，但**"什么条件下才加这几个变量"没核实** |
| 04 §5「`/sys/dev/char/<M>:<m>` 是符号链接」 | **只 grep 了 `device_create_sys_dev_entry` 的行号，没读函数体** | **高** —— "是符号链接"这一点来自一般认知，**不是本树取证** |
| 04 §3.2「misc_open 是第二次 `replace_fops`」 | **没读 `misc_open()` 的主体** | 中 —— 从"它是 `misc_fops` 唯一的回调"+ "misc 只占一个主设备号"推的，逻辑上必然，但**没看到那行代码** |

### §5.3 第三层：推断而非观察

| 论断 | 推断链 | 为什么没实测 |
|---|---|---|
| 03 §A8.1 / 08 §4「`cdev_del()` 之后 fops 仍可调」 | **直接引自 `fs/char_dev.c:590-592` 的注释** + `cdev_del()` 只做两件事（`fs/char_dev.c:596-597`）的代码事实 | **注释是官方陈述，代码事实确定**；但"会崩"这个后果没实测 |
| 05 §1.3 / 08 §5「不能用 `devm_kzalloc()`」 | 上一条 + devres 在 detach 时释放（设备模型规则） | **推断链短且每一步都可验**，但**没有构造一次 use-after-free** |
| 02 §1.0.A / 07 §4.3「忘了 `cdev_add()` ⇒ `open()` 返回 `ENXIO`」 | `chrdev_open()` 在 `fs/char_dev.c:387-388` 返回 `-ENXIO`；`cdev_map` 里没有条目就查不到 | 代码路径确定；**"表现为 ENXIO"没实测** |
| 06 §5.2「`devnode` 回调改一处，devtmpfs 和 udev 都生效」 | 两者都调 `device_get_devnode()`（`drivers/base/devtmpfs.c:132` 和 `drivers/base/core.c:2583`） | 两个调用点都是 grep 到的行；**"效果相同"是推断** |
| 07 §2.3「kdevtmpfs 线程起不来 ⇒ 之后全静默」 | `devtmpfs_create_node()` 的第一句 `if (!thread) return 0`（`drivers/base/devtmpfs.c:126-127`） | **代码事实极确定**（就那两行）；后果是推断 |
| 03 §B5 / 04 §6「`goto retry` 可能无限循环」 | `base_probe()` 总是 `return NULL`（`fs/char_dev.c:664`）+ `kobj_lookup()` 的 `goto retry`（`drivers/base/map.c:129`） | **本套明确标注"没有实测"**（03 §B5 末段） |
| 03 §C3「20 年前的数据结构注释在 arm64 嵌入式上不构成实际问题」 | 链表长度取决于"同一 major 下有多少独立区间" | **没有实测任何真实系统的链表长度** |
| 00 §2.4「块设备需要 page cache / bio / IO 调度」 | **一般架构知识，不是本树取证** | —— |
| 00 §2.6「devfs 已从内核移除」 | **一般历史知识，不是本树取证**（本树里确实没有 `fs/devfs/`，但"曾经有过"没验证） | —— |

**这一层是本篇最该被读者盯住的**：
上表里**代码事实都可核对**（都给了行号），但**"会导致什么后果"多数是推断**。

⚠️ **两条明确标为"非本树取证"**：00 §2.4 和 §2.6 的架构/历史说法。

### §5.4 第四层：版本时效性

| 项 | 说明 |
|---|---|
| 版本锚点 | 全部结论锚在 **6.1.158**（`f6e38ae624cf`） |
| 本套涉及的十条路径**全部干净** | §1.1 —— **不像 irq 那轮有本地补丁** |
| 上游变化最快的部分 | `class_create()` 的签名（6.4 之后去掉了 `owner` 参数）；`devnode` 回调的签名（部分版本加了 `const`） |
| 稳定得出奇的部分 | `fs/char_dev.c` 的核心逻辑 —— `/* will die */`（`fs/char_dev.c:40`）和那条 20 年的注释（`drivers/base/map.c:7`）都还在 |
| 已知会变的具体点 | `struct cdev` 字段数（本版本 **6**）；`struct miscdevice` 字段数（本版本 **10**） |

快速判断本套是否过期的三条命令：
```bash
sed -n '14,21p' include/linux/cdev.h            # 不再是 6 个字段 ⇒ 重读 02 §5.1
grep -c EXPORT_SYMBOL fs/char_dev.c             # 不再是 12 ⇒ 重读 01 §0.3
grep -n 'will die' fs/char_dev.c                # 没了 ⇒ 老式 API 可能被删了，重读 01 §4
```

---

## §6 交叉验证实例：初始假设被源码推翻的四条

### §6.1 `chrdev_open()` 里那次 `cdev_get()` 泄漏了吗？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 读完 `fs/char_dev.c` 全文后，我发现 `chrdev_open()` 成功路径上**没有配对的 `cdev_put()`**，而 `cd_forget()`（`fs/char_dev.c:426`）**只清指针不 `kobject_put()`** ⇒ **怀疑是引用泄漏** |
| **写进 03 §A7 的初稿** | 我当时写成了「⚠️ 严格说 `cd_forget()` 只清指针和摘链…… 详见 §C2（这是本篇发现的一处真实疑点）」 |
| **验证命令** | `grep -rn 'cdev_put' --include=*.c --include=*.h .` |
| **结果** | **`fs/file_table.c:323`** —— 在 VFS 的 `__fput()` 里！ |
| **进一步** | 读 `fs/file_table.c:310-325` 确认了完整顺序：① 你的 `release`(319-320) → ② `cdev_put(inode->i_cdev)`(321-324) → ③ `fops_put`(325) |
| **修正后的结论** | **配平的，一个 `open()` 对一个 `__fput()`**。03 §A7 整节重写成 §A7.1/§A7.2 两小节，并给出完整的配平表 |

**教训**：**"只看一个文件"会得出错误的引用计数结论** ——
字符设备层的引用配平**跨三个文件**（`fs/char_dev.c` / `fs/file_table.c` / `fs/inode.c`）。
⇒ 这也解释了为什么 `cdev_put()` **声明了但不导出**（01 §0.3）：它只给 VFS 用。

### §6.2 `MAJOR`/`MINOR` 宏只有一套吗？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 读了 `include/linux/kdev_t.h:10-12`（12+20 位），以为这就是全部 |
| **触发复核** | 写 §3.3 时想给 uapi 头文件也记一行行数，`cat` 了一下 |
| **结果** | `include/uapi/linux/kdev_t.h`（全文 14 行）里有**另一套同名宏**：`(dev)>>8` / `& 0xff` / `(ma)<<8 \| (mi)` —— **8+8 位的老定义** |
| **关键细节** | 它被 `#ifndef __KERNEL__` 保护（`include/uapi/linux/kdev_t.h:4`）⇒ **只对用户态生效**；`include/linux/kdev_t.h:5` include 它，但内核编译时那三个宏不生效 |
| **修正后的结论** | 01 §1.1 新增 §1.1.1 一整小节，明确警告**用户态不要用这套宏，要用 glibc 的 `major()`/`minor()`/`makedev()`** |

这一条的价值：它是一个**真实的用户态陷阱** ——
主设备号 ≥ 256 时（本树的动态区 384–511 就是！）那套宏会算错。
⇒ 也记进了 §1.5 的"文档与代码冲突"。

### §6.3 `drivers/base/map.c` 的注释是不是过时了？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 看到 `drivers/base/map.c:7-9` 说"data structure needs to be changed… too slow"，以为是一条**已经被解决但注释忘删**的旧话 |
| **验证** | 读 `struct kobj_map` 的定义（`drivers/base/map.c:19-30`）和 `kobj_lookup()` 的实现（`drivers/base/map.c:95`） |
| **结果** | **数据结构确实还是 255 桶哈希 + 单链表** —— 注释**没有过时** |
| **进一步** | 版权行是 "Al Viro 2002,2003"（`drivers/base/map.c:5`）⇒ **这条注释挂了 20 年** |
| **修正后的结论** | 03 §C3 把它记为一条**仍然有效的自陈技术债**，并补充了"为什么没人改"（它不是热路径，只在每个 inode 的第一次 open 时走） |

顺带发现：注释里的 "It is **isolated**" 解释了为什么 `cdev_map` 是 `static`、
**没有任何出口**（08 §9）—— 这个可观测性缺口一半是刻意的。

### §6.4 `struct miscdevice` 只能改权限吗？

| 阶段 | 内容 |
|---|---|
| **初始假设** | 写 06 §5.3 时我写了「misc 只能改 **mode**，改不了名字里的子目录」 |
| **验证** | `sed -n '79,95p' include/linux/miscdevice.h` |
| **结果** | 结构体里有 **`nodename`** 字段（`include/linux/miscdevice.h:87`）—— **能改名字，也能带子目录** |
| **顺带发现** | 还有 `this_device`（`include/linux/miscdevice.h:85`）—— misc 唯一给你的 `struct device *` |
| **修正后的结论** | 06 §5.3 整节重写成"两个专用字段"+ 三个可选字段的说明 |

**教训**：**"这个结构体只有我用到的那几个字段"是一个危险的默认假设** ——
写之前先 `sed` 打开整个结构体。

---

## §7 逐篇取证表

### §7.1 逐篇关键论断的出处

| 篇 | 关键论断 | 等级 |
|---|---|---|
| **00** | 核心 961 行 | 一等（§3.1） |
| 00 | §2 的背景（设备号设计 / 四代 `/dev` / 三层引用的必要性） | 混合 —— §2.2/§2.3/§2.5/§2.7/§2.8/§2.9 有行号支撑；⚠️ **§2.4 和 §2.6 明确标为非本树取证**（§5.3） |
| 00 | §1.5 三条决定对错的注释 | 一等（逐字引用） |
| **01** | 12 个导出符号 | 一等 |
| 01 | §1.1.1 的 uapi 宏陷阱 | 一等（§6.2） |
| 01 | 11 个错误码点 | 一等（§3.4） |
| 01 | `register_chrdev()` 的返回值语义 | 一等（`fs/char_dev.c:294` 逐字） |
| **02** | 五组关系 | 一等 —— 每组都给了"为什么不会发生"的行号 |
| 02 | 两张表独立 | 一等（`fs/char_dev.c:28` / `:41` / `:669`） |
| 02 | `i_cdev` 在 union 里 | 一等（`include/linux/fs.h:682-687`） |
| **03** | Part A 时间线 | 一等（全文逐行） |
| 03 | §A7 的引用配平 | 一等（§6.1 的交叉验证） |
| 03 | Part C 的四条 | 一等（§3.6 + §6.3） |
| **04** | §1 devtmpfs 的线程机制 | 二等（§2.2） |
| 04 | §2 uevent · §5 sysfs | **三等**（§2.3 —— 只有行号） |
| 04 | §3 misc | 二等（⚠️ `misc_open()` 主体未读，§5.2） |
| 04 | §4 `/proc/devices` | 一等 |
| **05** | 四套骨架 | 二等 —— 结构提炼自 `drivers/i2c/i2c-dev.c`（但那 15 行是 grep 的，§2.3） |
| 05 | 每个符号存在性 | 一等 —— **逐个 grep 确认**（含 `no_llseek` 是 `#define … NULL`，`include/linux/fs.h:3305`） |
| **06** | 没有 DT 绑定 | 一等（两条命令，§1.3） |
| 06 | 五类配置点 | 一等（每条有行号） |
| 06 | `devnode` 回调的优先级 | 二等（`drivers/base/core.c:3981-3988` 的行号，函数体未读） |
| **07** | 零调试选项 / 零 tracepoint / 5 个输出点 | 一等（三条命令，§2.6） |
| 07 | §4.3 的五种绕法 | 混合 —— 办法 ① 有代码支撑；**办法 ②–⑤ 未实测**（§5.1） |
| **08** | 九个主题 | 每个"回到代码"段都带行号 |
| **99** | 本篇全部数字 | §3 每行一条命令 |

### §7.2 三个自检脚本的最终结果

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/cdev
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
# check_doc_coverage.sh 不适用 —— 本子系统没有对应的 Documentation 子目录（§2.4）
```

| 脚本 | 结果 |
|---|---|
| `check_line_refs.py` | **干净** |
| `check_xrefs.py` | **0 条失效** |
| `check_doc_coverage.sh` | **不适用**，改为 §1.4 手工登记（§8.1） |
| `.xref-ignore` / `.lineref-ignore` | **两个都不存在 —— 零豁免** |

### §7.3 本套的自我修正记录（六条）

| # | 原先写的 | 实际是 | 怎么发现的 |
|---|---|---|---|
| 1 | 03 §A7 写成「`chrdev_open()` 的引用**疑似泄漏**」 | **配平的** —— `cdev_put()` 在 `fs/file_table.c:323` | §6.1 —— **本套最大的一处修正**，03 §A7 整节重写 |
| 2 | 01 §1.1 只讲了内核内部的 12+20 位宏 | uapi 头里还有一套 **8+8 位**的（`include/uapi/linux/kdev_t.h:10-12`） | §6.2 —— 新增 01 §1.1.1 |
| 3 | 06 §5.3 写「misc 只能改 mode」 | 还有 `nodename` 字段能改名字和子目录 | §6.4 |
| 4 | `/* will die */` 的行号写成了结构体起始行附近的那个数 | 实际在 `fs/char_dev.c:40` | 逐行 `sed` 打开结构体核对 |
| 5 | 若干处行号偏 1–2 行（`cdev_get` 里的 `try_module_get`、`__register_chrdev` 里的 `cdev->owner =`、链表插入段、`devtmpfs: initialized`、`devices.txt` 的块设备段起点） | 全部按 `grep -n` 的实测值更正 | 写完每篇后回头 `grep` 核对特征串 |
| 6 | `-ENXIO` 数成 3 个、`-ENOMEM` 数成 1 个 | 实际 `-ENXIO` **4** 个、`-ENOMEM` **2** 个（`grep -on` 全量列出后才发现） | 用 `grep -on` 而不是"按印象数" |

⚠️ **第 1 条是"方法性"错误**：**只在一个文件里找配对**。
⇒ 教训已写进 §6.1：引用计数的配平可能跨多个文件。

**第 6 条重复了 irq 那一轮的教训**（"统计个数不要人工数"，§8.7）——
说明这条纪律还不够牢。

### §7.4 ⚠️ 本轮 review 自己新产生的问题

| 新问题 | 处置 |
|---|---|
| 修正 §6.1 之后，03 §A7 里原来指向 Part C 的那句"详见 §C2（真实疑点）"成了陈旧引用 | 整节重写时一起去掉了；`check_xrefs.py` 也会抓（§C2 讲的是别的事） |
| 新增 01 §1.1.1 之后，01 §1.2 的"三个老式辅助"看起来和它重复 | 保留 —— 前者讲**用户态的宏**，后者讲**内核里的转换函数**，是两回事 |
| 本篇 §7.3 若照抄第 4–6 条的**具体错误行号** ⇒ 会污染本篇引用集 | 改成**文字描述**（"偏 1–2 行"、"数成 3 个"），不写出那些错误数字对应的 `文件:行号` 形式（§8.4 陷阱 ①） |

---

## §8 工具坑：三个自检脚本的已知行为

> 前六条沿用前几轮的结论，**§8.1 的"不适用"处置是本轮新增的**。

### §8.1 `check_doc_coverage.sh`：本轮**不适用**

| 行为 | 说明 |
|---|---|
| **它的第一个参数是相对 `Documentation/` 的路径** | 本子系统**没有属于它的子目录**（§1.4）⇒ 无从填写 |
| ⚠️ 硬填 `admin-guide` 或 `filesystems` | 会把几十篇无关文档当"必查"，全部 0 引用 ⇒ 噪声 |
| **本轮的处置** | **不跑这个脚本，改为在 §1.4 手工登记读了哪些文档** |

**这是一个新的处置类别**（前几轮都是 (a) 没覆盖 / (b) 覆盖没标 / (c) 无关文件）：
**(d) 脚本对这个子系统不适用** ——
因为它假定"每个子系统有一个 `Documentation/` 子目录"，
而字符设备层的文档是**一个孤立的 `.txt`**（`Documentation/admin-guide/devices.txt`）。

其它已知行为（沿用）：「引用次数」是 basename 子串匹配，**只有"是不是 0"可信**；
**只认 `.yaml`** DT 绑定。

### §8.2 `sed -n` 的多段规格按文件升序输出

```bash
sed -n '298p;283p' file.c        # 实际输出：283 → 298
```

⇒ **纪律：`sed` 的行号规格一律按升序列。**
本轮**没有再犯**（remoteproc 那轮因此错了 22 处行号）。

### §8.3 `check_line_refs.py` 会解析代码块和 ASCII 图里的 `path:NNN`

| 行为 | 规避 |
|---|---|
| 它抓**任何** `path:NNN` token（含围栏代码块、ASCII 图） | ASCII 图里**不写行号**，放到图下方的表里 |
| ⚠️ 错误的规避 | 加 `--ignore` |
| `--ignore` 的真实范围 | **只对"行号越界"生效**，对"文件名解析不到"无效 |

本套从 01 篇篇首就声明了这条纪律，**十篇零告警**。

⚠️ **本轮的一个新变体**：00 §6 的调用路径图里我**故意**在每行末尾写了
`文件:行号`（不是在图里而是右侧对齐的注释列）——
这些是**真实引用**，被脚本正常校验通过。
⇒ 结论：**"图里不写行号"这条纪律的本意是"别写简写形式"**，
写完整路径是安全的（脚本能解析）。

### §8.4 99 篇的三个自污染陷阱

| 陷阱 | 表现 | 正确做法 |
|---|---|---|
| ① 在"自审修正记录"里照抄那个**错误的** `文件:行号` | 会被当成真实引用去校验 | **用文字描述**（"偏 1–2 行"、"数成 3 个"） |
| ② 引用自检脚本自己的 `~/.claude/...` 路径 + 行号 | 不在源码树里 | 只写脚本名 |
| ③ 为了演示 `check_xrefs.py` 的假阳性而写出那个字面串 | 演示本身触发告警 | 描述形状，不写具体数字 |

**本轮陷阱 ① 生效了一次**（§7.3 第 4–6 条），已按文字描述处理。
**陷阱 ③ 本轮没踩** —— 因为本篇没有跨子系统的小节号引用。

### §8.5 `check_line_refs.py` 按逗号切基名

⚠️ 文件名含逗号的一律**不写行号**（DT 绑定常见）。
本轮**没有遇到** —— 因为本子系统没有 DT 绑定（§1.3）。

同类特例：顶层 **`Makefile`** 和 **`Kconfig`** 也一律不带行号。

### §8.6 `check_xrefs.py` 的三种假阳性

| 形态 | defuse |
|---|---|
| `NN-xxx.md` 后 60 字符内出现「本篇 §X」 | 调整语序 |
| 跨子系统引用（别套的篇号 + 小节号） | 写成「NN 篇 §X」——脚本不匹配 |
| 两个相邻小节号连写（前一个的两位数被当成篇号） | **在两者之间插入文字** |

**本轮三种都没触发**。

### §8.7 环境坑速查

| 坑 | 规避 |
|---|---|
| 默认 `grep` 是 **ugrep 7.5.0** | 统计一律用 `/usr/bin/grep` |
| ugrep 不认 `\s` | 用 POSIX `[[:space:]]` |
| ugrep 默认遵守 `.gitignore` | 同第 1 条 |
| Bash 的 cwd 跨调用保留、shell 变量不保留 | `cat >>` 一律用**绝对路径** |
| Python heredoc 里的替换串含 ASCII `"` | 一律用三引号字符串 |
| `assert` 失败时不要凭记忆猜原文 | 去 grep 那个特征串 |
| **统计"个数"用 `grep -c` / `grep -on`，不要凭印象数** | **本轮又因此错了一次**（§7.3 第 6 条） |
| **写结构体字段前先 `sed` 打开整个结构体** | 本轮新增（§6.4 的教训） |
| **找引用计数的配对不要只看一个文件** | 本轮新增（§6.1 的教训） |

---

## §9 rule-29 对照：本套没有对照集

| 项 | 情况 |
|---|---|
| `output/qoder/cdev/` | **不存在** |
| `output/qoder/` 下有什么 | 只有 `spi` 一套 |
| ⇒ 处置 | **无对照套**。质量控制依赖两个自检脚本 + §7.3 的六条自我修正 |

本套沿用了 spi 那轮吸收进 skill 的全部规则，在 cdev 上体现最明显的四条：

| 规则 | 在本套的体现 |
|---|---|
| 02 篇开篇先立「先分清 N 组关系」 | 02 §1.0 立了**五组**，其中 §1.0.A「三件独立的事」是全套的主线 |
| 04 篇要有"哪几个在目标架构上真会碰到"的分诊表 | 04 §0.2 的六行分诊表 |
| 06 篇没有 DT 绑定时改讲替代配置机制 | **本套是这条规则第一次真正用上** —— 06 篇整篇改讲 Kconfig / 命令行 / 设备号申请 / `devnode` 回调 / udev |
| 08 篇必含"最容易被类比错的那组关系" | 08 §1「三件独立的事」用"开门店办三件事"的类比 |

相比 irq / remoteproc 那两轮，本套多做了两件事：

| 多做的 | 为什么 |
|---|---|
| 00 §2 讲的是**软件设计背景**而不是硬件 | 它是纯软件子系统 —— §2 篇首明确声明了这个替换（skill 的适配规则） |
| 07 §4.3 专门给了"**没有出口时怎么绕**"的五种办法 | 因为 `cdev_map` 的可观测性缺口是本子系统的定性特征（03 §C5） |

---

## §10 这套文档怎么用

| 你的处境 | 建议 |
|---|---|
| 想快速上手 | 00 §3.1（三件独立的事）→ 08 §1 → 05 §1。**§5.1 提醒你 05 的代码没编译过** |
| 在排查"节点/打开"问题 | 07 §7 速查 → 07 §4.3 → 06 §8 |
| 在排查 use-after-free | 08 §4 §5 → 03 §A8.1 · §B3 → 07 §1.3（开 `DEBUG_KOBJECT_RELEASE`） |
| 要基于本套做设计决策 | **先读 §5.3** —— 那九条是"代码事实确定、后果是推断"的；末两条更是"非本树取证" |
| 要引用 04 §2 / §5 或 06 §5 的机制 | **先读 §2.3 和 §5.2** —— 那几节的证据只到"行号"级别，`drivers/base/core.c` 的函数体本套一行没读 |
| 要写用户态程序处理设备号 | **先读 01 §1.1.1** —— uapi 的宏是 8+8 位的老定义（§6.2） |
| 要把本套移到别的内核版本 | 先跑 §5.4 那三条命令 |
| 想复核某个数字 | §3 每一行都配了命令 |
| 要处理块设备 | **本套不覆盖**（§2.7）—— 只有设备号编号习惯是共通的 |
