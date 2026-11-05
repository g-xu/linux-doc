---
title: 调试方法
---

# regmap 07：调试与观测

## §0 分诊表：出问题先看哪个出口

**regmap 的可观测性在框架型子系统里算好的**（对比 virtio 的 debugfs 和 tracepoint **全 0**）：
**`regmap-debugfs.c` 692 行 + `trace.h` 284 行 / 17 个事件**。

| 症状 | 第一个该看的出口 | 去 |
|---|---|---|
| **regmap 建都建不起来** | **dmesg**（但 MMIO 路径 0 条打印，§4.1） | §4 / 06 §6 |
| **读写返回错误码** | **错误码分诊表**（一码多因，§5） | §5 |
| **值不对（但不报错）** | **debugfs `registers`** 对照手册 | §2.2 |
| **读到的值不变** | **debugfs `cache_only` / `cache_dirty`** | §2.3 |
| **想看每一次寄存器访问** | **tracepoint `regmap_reg_read` / `regmap_reg_write`** | §3 |
| **resume 后部分功能失效** | **tracepoint `regcache_sync`** + `cache_dirty` | §3.3 |
| **地址被改写了（分页）** | **debugfs 的 `range` 文件 + `regmap_hw_*` tracepoint** | §2.2 / §3.2 |
| **超早期启动阶段，trace 还没起来** | **源码级 `LOG_DEVICE`**（要改源码重编译） | §1.2 |

⚠️ **一条要先说的**：**regmap 没有字符设备、没有 sysfs 接口、没有 ioctl。**
**用户态唯一的入口是 debugfs**（重跑：`grep -rn 'class_create\|cdev_add\|device_attr\|sysfs_create' drivers/base/regmap/` 无输出）。

---

## §1 编译期开关

### §1.1 Kconfig：16 个符号，全部不可见

**`drivers/base/regmap/Kconfig` 的注释第一句就说明了性质**：
> *"There are no user servicable options here, this is an API intended to be used by other
> kernel subsystems. These subsystems should select the appropriate symbols."*

⇒ **16 个符号全是 `bool` / `tristate` 但都没有 prompt 字符串**（重跑 `grep -c '^config ' drivers/base/regmap/Kconfig`）
⇒ **`make menuconfig` 里一个都搜不到，只能被别的驱动 `select`。**

| 符号 | 类型 | 依赖 | 说明 |
|---|---|---|---|
| `REGMAP` | `bool` | — | **`default y if` 后面列了 13 个子符号**；**另外 `select IRQ_DOMAIN if REGMAP_IRQ`、`select MDIO_BUS if REGMAP_MDIO`** |
| `REGCACHE_COMPRESSED` | `bool` | **`select LZO_COMPRESS` + `LZO_DECOMPRESS`** | **对应 `REGCACHE_COMPRESSED` 缓存类型**（04 §2） |
| `REGMAP_IRQ` | **`bool`（不是 tristate）** | — | **不能编成模块**，因为它 `select IRQ_DOMAIN` |
| `REGMAP_MMIO` / `REGMAP_MDIO` | `tristate` | **无 depends** | **任何架构都能开** |
| `REGMAP_I2C` / `REGMAP_SCCB` | `tristate` | `depends on I2C` | 一个 I2C 两个符号（04 §6） |
| `REGMAP_SPI` / `REGMAP_SPI_AVMM` | `tristate` | `depends on SPI` | |
| `REGMAP_SPMI` / `REGMAP_W1` / `REGMAP_SLIMBUS` / `REGMAP_I3C` / `REGMAP_AC97` | `tristate` | 各自总线 | **`REGMAP_AC97` 无 depends** |
| `REGMAP_SOUNDWIRE` / `REGMAP_SOUNDWIRE_MBQ` | `tristate` | `depends on SOUNDWIRE` | |

⚠️ **`REGMAP=n` 是一个真实会遇到的配置**（没有任何驱动 select 它时）：
**公共头里有一整套空桩，每个都 `WARN_ONCE` 后返回 `-EINVAL`**
（**`include/linux/regmap.h` 里共 45 处 `WARN_ONCE`**，重跑 `grep -c 'WARN_ONCE' include/linux/regmap.h`）
⇒ **症状是"所有 regmap 调用都返回 -EINVAL，dmesg 里一堆同样的栈"**（01 §9 / §5.1）。

### §1.2 ⚠️ 两个"必须改源码"的开关（本子系统最特殊的地方）

**regmap 有两个调试能力是用 `#undef` 硬关掉的，不是 Kconfig 选项。**
**想用就得改那一行源码重新编译。**

#### 开关 1：`LOG_DEVICE` —— 早期启动阶段的裸 printk

```c
/* drivers/base/regmap/regmap.c:27-42 —— 注释原话就是它的用途 */
/*
 * Sometimes for failures during very early init the trace
 * infrastructure isn't available early enough to be used.  For this
 * sort of problem defining LOG_DEVICE will add printks for basic
 * register I/O on a specific device.
 */
#undef LOG_DEVICE			/* 就是这一行 */

#ifdef LOG_DEVICE
static inline bool regmap_should_log(struct regmap *map)
{
	return (map->dev && strcmp(dev_name(map->dev), LOG_DEVICE) == 0);
}
#else
static inline bool regmap_should_log(struct regmap *map) { return false; }
#endif
```

**打开方法**：把 `regmap.c:33` 改成 `#define LOG_DEVICE "1-004b"`（设备名字符串）。
**打开后多出三条 `dev_info`**：

| 输出格式 | 位置 | 时机 |
|---|---|---|
| `%x <= %x` | `drivers/base/regmap/regmap.c:2010` | **单寄存器写成功后** |
| `%x => %x` | `drivers/base/regmap/regmap.c:2905` | **单寄存器读成功后** |
| `%x <= [` / `%x => [` | `drivers/base/regmap/regmap.c:2213` | **raw 读写**（`write ? "<=" : "=>"`） |

⚠️ **`LOG_DEVICE` 是"按设备名精确匹配"**（`strcmp`，`:38`）
⇒ **写错一个字符就一条都不输出，而且没有任何提示。**
**设备名从 `ls /sys/kernel/debug/regmap/` 或 `dev_name()` 拿。**

#### ⚠️ 开关 2：`REGMAP_ALLOW_WRITE_DEBUGFS` —— debugfs 写寄存器

**全树只有三处提到它，没有任何一处 `#define`**（重跑
`grep -rn 'REGMAP_ALLOW_WRITE_DEBUGFS' --include=*.c --include=*.h --include=Kconfig .`）：

| 位置 | 内容 |
|---|---|
| `drivers/base/regmap/regmap-debugfs.c:293` | **`#undef REGMAP_ALLOW_WRITE_DEBUGFS`** —— 硬关 |
| `drivers/base/regmap/regmap-debugfs.c:294` | `#ifdef` 包住 `regmap_map_write_file()` 的实现 |
| `drivers/base/regmap/regmap-debugfs.c:617` | 决定 `registers` 文件的 mode |

⇒ **默认行为**：
- **`regmap_map_write_file` 被 `#define` 成 `NULL`**（`regmap-debugfs.c:334`）
- **`registers` 文件的 mode 是 `0400`（只读）**（`:620`）；**开了才是 `0600`**（`:618`）

**打开后的行为**（`regmap-debugfs.c:301-332`）：
- **输入格式是两个十六进制数：`echo "10 deadbeef" > registers`**（`:319-322` 的 `simple_strtoul` + `kstrtoul(…, 16, …)`）
- ⚠️ **会 `add_taint(TAINT_USER, LOCKDEP_STILL_OK)`**（`:326`）——**内核被标记污染，注释原话："Userspace has been fiddling around behind the kernel's back"**
- **实际执行的是 `regmap_write()`**（`:328`）⇒ **会走缓存、会被 `writeable_reg` 拦**

⚠️ **两条不要搞错**：
1. **`registers` 只读不是权限问题，`chmod` 也没用** ——**`.write` 指针本身是 NULL**
2. **但 `cache_only` / `cache_bypass` 两个文件默认就是 `0600` 可写**（§2.3）
   ⇒ **"regmap debugfs 默认只读"这句话是错的**

---

## §2 debugfs：692 行，本子系统最有用的出口

### §2.1 落点与八个文件

**根目录**：`/sys/kernel/debug/regmap/`（`drivers/base/regmap/regmap-debugfs.c:683`）
**每个 map 一个子目录**（`:606`），**目录名 = `regmap_config.name`**，
**没填 name 就用设备名**；**syscon 建的 map 名字是 `%pOFn@%pa` 格式**
（`drivers/mfd/syscon.c:103`）⇒ **形如 `syscon@ff770000`，能直接对上 DT 节点**（06 §5.2）。

| 文件 | mode | 建立位置 | 条件 | 内容 |
|---|---|---|---|---|
| `name` | `0400` | `regmap-debugfs.c:608` | 总是 | **map 的名字** |
| `range` | `0400` | `:611` | 总是 | **分页范围表**（04 §3） |
| `registers` | **`0400`**（默认） | `:623` | **`map->max_register \|\| regmap_readable(map, 0)`**（`:614`） | **寄存器全量转储** |
| `access` | `0400` | `:625` | 同上 | **每个寄存器的可读/可写/volatile/precious 四个属性** |
| `cache_only` | **`0600` 可写** | `:630` | **`map->cache_type` 非 0** | 当前是否只写缓存 |
| `cache_dirty` | **`0400` 只读** | `:632` | 同上 | **缓存脏标记**（`debugfs_create_bool`，不带自定义 fops） |
| `cache_bypass` | **`0600` 可写** | `:634` | 同上 | 当前是否绕过缓存 |
| **每个命名 range 一个文件** | `0400` | `:644` | **`range_node->name` 非空**（`:643`） | 该页的独立视图 |

⚠️ **`registers` 和 `access` 是有条件建立的**（`regmap-debugfs.c:614`）：
**`max_register == 0` 且寄存器 0 不可读 ⇒ 这两个文件不存在。**
⇒ **"debugfs 目录里没有 registers 文件"通常意味着驱动没填 `max_register`**（05 §1.2）。

⚠️ **缓存相关的三个文件只在 `cache_type != REGCACHE_NONE` 时建立**（`:629`）
⇒ **MMIO 类 map 通常看不到它们，这是正常的，不是出错。**

### §2.2 读侧怎么用

```bash
# 1. 有哪些 map
ls /sys/kernel/debug/regmap/

# 2. 全量转储（只读，安全）
cat /sys/kernel/debug/regmap/<name>/registers

# 3. 每个寄存器的属性 —— 排"为什么写不进去"的第一站
cat /sys/kernel/debug/regmap/<name>/access

# 4. 分页配置（04 §3）
cat /sys/kernel/debug/regmap/<name>/range
```

⚠️ **示例输出本文档不给**：**本套文档未上机运行**（99 §5 第一层）
⇒ **编造一段 `cat` 输出比不给更糟**。格式化代码在
`drivers/base/regmap/regmap-debugfs.c` 的 `regmap_map_read_file` / `regmap_access_read_file` 里，
需要精确格式时直接读那两个函数。

**`access` 文件的价值**：**它把 `writeable_reg` / `readable_reg` / `volatile_reg` /
`precious_reg` 四个回调的判断结果逐寄存器摊开**
⇒ **"写返回 `-EIO`"到底是 `max_register` 越界还是 `wr_table` 拦的，看这个文件最快**（§5.2）。

### §2.3 ⚠️ 两个可写文件的 setter 语义（不是简单赋值）

**这两个文件的写入行为和同名的内核 API 不完全一样，必须逐条看。**

#### `cache_only`（`regmap-debugfs.c:459-498`）

| 动作 | 实际发生的事 |
|---|---|
| 解析 | **`kstrtobool_from_user`** ⇒ 接受 `y/n/1/0/Y/N` |
| ⚠️ **非法输入** | **`return count` —— 静默吞掉，不报错**（`:470-471`，注释说是"Ignore malforned data like `debugfs_write_file_bool()`"） |
| **`Y`（0→1）** | **`dev_warn "debugfs cache_only=Y forced"` + `add_taint(TAINT_USER, …)`**（`:480-481`） |
| **`N`（1→0）** | **`dev_warn "…cache_only=N forced: syncing cache"` + 置 `require_sync`**（`:483-484`） |
| ⚠️ **关掉时的副作用** | **在锁外调 `regcache_sync(map)`**（`:491-492`）⇒ **会真的把整份缓存写回硬件**；失败时 `dev_err "Failed to sync cache %d"`（`:494`） |

⚠️ **`echo N > cache_only` 不是"恢复正常"，是"立刻批量回灌"。**
**在设备还没上电时这么做会打出一串总线错误**（05 §7.1）。

#### `cache_bypass`（`regmap-debugfs.c:506-538`）

| 动作 | 实际发生的事 |
|---|---|
| `Y`（0→1） | `dev_warn "debugfs cache_bypass=Y forced"` + `add_taint`（`:527-528`） |
| `N`（1→0） | `dev_warn "debugfs cache_bypass=N forced"`（`:530`）——**无 sync** |

⚠️ **本篇最重要的一条 debugfs 陷阱**：
**内核 API `regcache_cache_bypass()` 里有 `WARN_ON(map->cache_only && enable)`
（`drivers/base/regmap/regcache.c:583`），`regcache_cache_only()` 里有对称的
`WARN_ON(… map->cache_bypass && enable)`（`regcache.c:539-540`）
—— 这两个 debugfs writer 都直接赋值 `map->cache_bypass = new_val`（`regmap-debugfs.c:532`）
/ `map->cache_only = new_val`（`:486`），一个 `WARN_ON` 都没有。**
⇒ **通过 debugfs 可以把 `cache_only` 和 `cache_bypass` 同时置 Y，核心不会警告。**
**后果是"写既不进硬件也不进缓存"** ⇒ **调试时把这两个都开过就必须记得复位。**

### §2.4 两个盲区（都是刻意的）

| 盲区 | 为什么 |
|---|---|
| **`precious` 寄存器不出现在 `registers` 转储里** | **读它会有副作用**（如清中断标志）⇒ **转储时读一遍就把状态破坏了**（01 §2） |
| **写寄存器默认完全关闭** | §1.2 开关 2 |

**`access` 文件仍然会显示 precious 属性** ⇒ **"registers 里少了几行"可以在 access 里对上账。**

### §2.5 0 个导出符号

**`regmap-debugfs.c` 一个 `EXPORT_SYMBOL` 都没有**（04 §4.3）
⇒ **驱动无法主动往自己的 regmap debugfs 目录里加文件**，
**也无法在代码里触发一次转储。****它是纯粹的旁路观测面。**

### §2.6 ⚠️ 目录名的构成规则（找不到目录时看这里）

**目录名不是 `regmap_config.name`，是拼出来的**（`drivers/base/regmap/regmap-debugfs.c:582-602`）：

| 情形 | 目录名 | 位置 |
|---|---|---|
| **`config.name` 非空** | **`<设备名>-<config.name>`** | `:586-587` 的 `kasprintf("%s-%s", devname, name)` |
| **`config.name` 为空** | **`<设备名>`** | `:593` |
| ⚠️ **设备名恰好是 `"dummy"`** | **`dummy<N>`（带自增序号）** | `:596-602` |

**所以 05 §1.1 那个 `.name = "foo"` 的例子，在一个叫 `1-004b` 的 I2C 设备上，
目录名是 `1-004b-foo`，不是 `foo`。**

---

## §3 tracepoint：17 个事件

### §3.1 全清单（六个类 + 两个独立事件）

**口径警告（04 §8.2 同一条）**：**数事件要数 `TRACE_EVENT` + `DEFINE_EVENT`，
不能数 `DECLARE_EVENT_CLASS`** ——**后者是模板，不产生 tracepoint。**
重跑：`grep -cE '^(TRACE_EVENT|DEFINE_EVENT)\(' drivers/base/regmap/trace.h` ⇒ **17**。

| 事件 | 所属类（位置） | 字段 | 打点位置 |
|---|---|---|---|
| `regmap_reg_write` | `regmap_reg`（`trace.h:16`） | `name` `reg` `val` | `regmap.c:2012` |
| `regmap_reg_read` | 同上（`trace.h:46`） | 同上 | 单寄存器读成功后 |
| `regmap_reg_read_cache` | 同上（`trace.h:54`） | 同上 | **命中缓存的读** ——**它和 `regmap_reg_read` 互斥，是判断"这次读有没有真的碰硬件"的唯一手段** |
| `regmap_bulk_write` | `regmap_bulk`（`trace.h:62`） | `name` `reg` **`buf`（动态数组）** `val_len` | |
| `regmap_bulk_read` | 同上（`trace.h:95`） | 同上 | |
| `regmap_hw_read_start` | `regmap_block`（`trace.h:103`） | `name` `reg` `count` | **真正的总线传输开始** |
| `regmap_hw_read_done` | 同上（`trace.h:131`） | 同上 | **配对完整** |
| `regmap_hw_write_start` | 同上（`trace.h:138`） | 同上 | |
| `regmap_hw_write_done` | 同上（`trace.h:145`） | 同上 | **配对完整** |
| `regcache_sync` | **独立 `TRACE_EVENT`**（`trace.h:152`） | **`name` `type` `status` 三个都是字符串** | **4 个打点**，见 §3.3 |
| `regmap_cache_only` | `regmap_bool`（`trace.h:175`） | `name` **`flag`（int，不是 bool）** | `regcache.c:542` |
| `regmap_cache_bypass` | 同上（`trace.h:201`） | 同上 | `regcache.c:585` |
| `regmap_async_write_start` | ⚠️ **`regmap_block` 类**（`trace.h:225`）——**不是 `regmap_async` 类** | `name` `reg` `count` | `regmap.c:1791` |
| `regmap_async_io_complete` | `regmap_async`（`trace.h:232`） | **只有 `name`** | `regmap.c:3347` |
| `regmap_async_complete_start` | 同上（`trace.h:239`） | 同上 | |
| `regmap_async_complete_done` | 同上（`trace.h:246`） | 同上 | **配对完整** |
| `regcache_drop_region` | **独立 `TRACE_EVENT`**（`trace.h:253`） | `name` `from` `to` | `regcache.c:514` |

### §3.2 ⚠️`name` 字段是设备名，不是 config 名（本篇最大的观测盲区）

```c
/* drivers/base/regmap/internal.h:287-293 —— 所有 tracepoint 的 name 都来自这里 */
static inline const char *regmap_name(const struct regmap *map)
{
	if (map->dev)
		return dev_name(map->dev);	/* 优先设备名 */

	return map->name;			/* 只有无 dev 时才用 config.name */
}
```

⚠️ **后果**：
- **debugfs 目录名是 `<设备名>-<config.name>`（§2.6），tracepoint 的 `name` 只有 `<设备名>`**
  ⇒ **两个出口的标识符不一样，不能直接互相 grep。**
- **同一个设备上建了多个 regmap（不同 `config.name`）⇒ tracepoint 里 `name` 完全相同
  ⇒ 无法区分是哪个 map 在动。**这种情形在 MFD 里真实存在（05 §3.2）。

**绕开办法**：**按 `reg` 字段过滤**（不同 map 的寄存器区间通常不重叠），
**或临时把要观察的那个 map 的 `dev` 设成 NULL**（改源码，不推荐）。

### §3.3 `regcache_sync` 的四个打点与配对性

**它一个事件承载了四种 `status` 字符串**（逐字取自源码）：

| `status` 字符串 | 位置 | 谁 |
|---|---|---|
| `start` | `drivers/base/regmap/regcache.c:369` | `regcache_sync()` |
| `stop` | `drivers/base/regmap/regcache.c:428` | 同上 |
| `start region` | `drivers/base/regmap/regcache.c:466` | `regcache_sync_region()` |
| `stop region` | `drivers/base/regmap/regcache.c:487` | 同上 |

**`type` 字段 = `map->cache_ops->name`**（`regcache.c:368`）
⇒ **取值是缓存后端名（`rbtree` / `flat` / `lzo`，见 04 §2.1）** ——
**不是"同步类型"**，名字容易误解。

**配对性核对（逐字确认，不是猜）**：

| 检查 | 结论 |
|---|---|
| **`start` 之后有提前退出吗** | **有**：`regcache.c:371-372` 的 `if (!map->cache_dirty) goto out;` |
| **提前退出会不会漏掉 `stop`** | **不会**：**`out:` 标签在 `regcache.c:396`，而 `stop` 在 `:428`**（`out:` 之后）⇒ **必然配对** |
| **`start` 之前有提前 return 吗** | **有**：`regcache.c:358-359` 的 `if (WARN_ON(map->cache_type == REGCACHE_NONE)) return -EINVAL;` ⇒ **但它在 `start` 打点之前，不产生孤立事件** |
| `sync_region` 同样检查 | **`out:` 在 `regcache.c:478`，`stop region` 在 `:487`** ⇒ **配对完整** |

⇒ **看到 `start` 却看不到 `stop` 不能解释成"提前退出"** ——
**只能是 trace 缓冲区丢事件，或者真的卡在同步里出不来。**

### §3.4 ⚠️ 一个真实的"缺 done"事件

**`regmap_async_write_start`（`trace.h:225`）没有对应的 `regmap_async_write_done`。**
**但这不是缺陷**，因为异步写的完成通知走的是另一个事件：

```c
/* drivers/base/regmap/regmap.c:3342-3347 —— 完成回调里打 io_complete */
void regmap_async_complete_cb(struct regmap_async *async, int ret)
{
	struct regmap *map = async->map;
	bool wake;

	trace_regmap_async_io_complete(map);	/* 就是它对应 write_start */
	...
}
```

⚠️ **但配对是"多对多"的，不是一对一**：
**`regmap_async_write_start` 带 `reg` 和 `count`（`regmap_block` 类），
`regmap_async_io_complete` 只带 `name`（`regmap_async` 类，`trace.h:208-216` 只有一个字段）**
⇒ **无法把某一次完成对应回某一次提交。**
**多笔异步写并发在途时，只能数个数，不能配对。**

⚠️ **返回值黑洞检查**（第 22 条第 7 项）：
**`regmap_async_complete_cb()` 是 `void`**（`drivers/base/regmap/regmap.c:3342`）——
**它把错误码存进 `map->async_ret`（`:3353-3354`）而不是返回**
⇒ **单看 tracepoint 看不出这笔异步写成功还是失败，得靠 `regmap_async_complete()` 的返回值**（01 §6）。

### §3.5 没有 `TP_CONDITION`

**`trace.h` 里 0 处 `TP_CONDITION`**（重跑 `grep -c 'TP_CONDITION' drivers/base/regmap/trace.h`）
⇒ **打点没有内建过滤，开了就是全量。**
**高频寄存器访问的场景（如 pinctrl 遍历所有引脚）会瞬间灌满 trace 缓冲区**
⇒ **必须用 ftrace 自己的 filter 收窄。**

### §3.6 怎么用

```bash
cd /sys/kernel/debug/tracing

# 1. 只看某个设备的寄存器写（name 是设备名，不是 config 名 —— §3.2）
echo 'name == "1-004b"' > events/regmap/regmap_reg_write/filter
echo 1 > events/regmap/regmap_reg_write/enable

# 2. 区分"读了硬件"和"读了缓存"—— 同时开这两个
echo 1 > events/regmap/regmap_reg_read/enable
echo 1 > events/regmap/regmap_reg_read_cache/enable

# 3. 看真正的总线传输（分页改写后的最终地址）
echo 1 > events/regmap/regmap_hw_write_start/enable
echo 1 > events/regmap/regmap_hw_write_done/enable

# 4. 缓存同步过程
echo 1 > events/regmap/regcache_sync/enable

# 5. 全开（慎用，见 §3.5）
echo 1 > events/regmap/enable

cat trace_pipe
```

**本篇不给 `cat trace_pipe` 的输出示例**（未上机运行，99 §5）。
**格式可以从 `TP_printk` 直接推**：
`regmap_reg` 类是 `"%s reg=%x val=%x"`（`trace.h:35`），
`regmap_block` 类是 `"%s reg=%x count=%d"`（`trace.h:121`），
`regcache_sync` 是 `"%s type=%s status=%s"`（`trace.h:171-172`）。

**`regmap_bulk_*` 的 `val` 用 `__print_hex()` 打印动态数组**（`trace.h:83-84`）
⇒ **批量读写的完整数据在 trace 里能看到**，**这是 debugfs 做不到的**。

---

## §4 日志可见性等级

### §4.1 ⚠️ 先说结论：核心几乎不说话

| 文件 | `dev_err`/`pr_err`/`dev_warn`/`dev_info` 条数 | 说明 |
|---|---|---|
| `regmap-mmio.c` | **0** | **MMIO 初始化失败完全静默，只返回错误码**（06 §6） |
| `regmap.c` | **13**（**含 3 条 `dev_info`，被 `LOG_DEVICE` 门控**） | **10 条非 info 里有 7 条讲 `ranges`**（06 §6） |
| `regmap-irq.c` | **30** | **全目录最"话多"的文件**，**含 6 条弃用警告**（§4.3） |
| `regcache.c` | **11** | 同步失败为主 |
| `regmap-debugfs.c` | **5** | **全是"用户强制改了状态"的告警**（§2.3） |
| `regmap-spi-avmm.c` | 2 | |

重跑：`grep -rc 'dev_warn\|pr_warn\|dev_err\|pr_err\|dev_info\|pr_info' drivers/base/regmap/*.c`
⚠️ **口径**：**`grep -c` 数的是「含该串的行数」，不是打印语句数**（99 §6）——
一行写两个打印会少算；**本目录里没有这种写法，已逐行看过。**

### §4.2 按可见性分级（不是"始终输出"）

| 等级 | 数量 | 分布 | 触发条件 |
|---|---|---|---|
| **`WARN_ONCE`（每处只报一次）** | **47** | **45 在 `include/linux/regmap.h`（`CONFIG_REGMAP=n` 空桩）+ 2 在 `regmap.c`（`:1314` `:3431`）** | **头文件那 45 处 = 编译时没开 REGMAP**（§1.1） |
| **`WARN_ON`（每次都报）** | **13** | `regcache.c` / `regmap.c` 等 | **驱动用法错**，如 `regcache.c:358` 的"对 `REGCACHE_NONE` 的 map 调 `regcache_sync`" |
| ⚠️ **`BUG_ON`（直接 panic）** | **12** | **`regcache.c` 5 处 + `regmap-spmi.c` 6 处 + `regmap-irq.c` 1 处** | 见 §4.4 |
| `dev_err` / `dev_warn` | **58**（§4.1 合计 61 行减去 3 条 `dev_info`） | 见 §4.1 | 运行期失败 |
| **`dev_dbg` / `pr_debug`（需 dyndbg）** | **10** | **`regcache.c` 7 · `regmap.c` 1 · `regmap-debugfs.c` 1 · `regcache-lzo.c` 1** | **默认不输出**，要 `echo 'file regcache.c +p' > /sys/kernel/debug/dynamic_debug/control` |
| **`dev_info`（需改源码）** | **3** | **全在 `regmap.c`（`:2010` `:2213` `:2905`），被 `LOG_DEVICE` 门控** | §1.2 |

⚠️ **`WARN_ONCE` 的"once"是按代码位置算的，不是按设备。**
**两个不同芯片犯同一个错，只有第一个会报**
⇒ **"只看到一条警告"不等于"只有一个设备有问题"。**

**`regmap.c` 那两条 `WARN_ONCE` 都是"驱动传了明显非法的参数"**：
`:1314` 的 `"invalid empty mask defined\n"`（`reg_field` 的 mask 算出来是 0，05 §5）·
`:3431` 的 `"invalid registers number (%d)\n"`（`regmap_register_patch` 的 `num_regs <= 0`）。

### §4.3 六条弃用警告（`regmap-irq.c`，逐字取自源码）

**这是 regmap-irq 正在改接口的证据**（04 §1.5 的设计债同源）：

| 消息（逐字） | 位置 |
|---|---|
| `not_fixed_stride is deprecated; use ->get_irq_reg() instead` | `drivers/base/regmap/regmap-irq.c:747` |
| `type registers are deprecated; use config registers instead` | `:755` |
| `virtual registers are deprecated; use config registers instead` | `:758` |
| `mask_base and unmask_base are inverted, please fix it` | `:879` |
| `mask_invert=true ignored` | `:883` |
| `mask_invert=true is deprecated; please switch to unmask_base` | `:893` |

**它们都是 `dev_warn`（不带 `_once`）** ⇒ **每次 `probe` 都会打一遍。**
**看到这些就说明驱动的 `regmap_irq_chip` 用了旧字段**（04 §1.2 组 3）。

### §4.4 ⚠️ 12 个 `BUG_ON`：哪些是驱动能触发的

| 位置 | 条件 | 谁的错 |
|---|---|---|
| `drivers/base/regmap/regcache.c:217` `:247` `:276` `:361` `:456` | **`BUG_ON(!map->cache_ops)`（5 处）** | **核心内部一致性检查**；正常路径上 `cache_type != NONE` 就必有 `cache_ops`，**驱动碰不到** |
| `drivers/base/regmap/regmap-spmi.c:23` `:39` `:113` `:157` | **`BUG_ON(reg_size != 1)` / `(reg_size != 2)`** | ⚠️ **驱动能触发**：**SPMI 的 `reg_bits` 配错就 panic 而不是返回错误码** |
| `drivers/base/regmap/regmap-spmi.c:72` `:192` | `BUG_ON(count < 1)` / `(count < 2)` | 同上 |
| `drivers/base/regmap/regmap-irq.c:495` | `BUG_ON(!data->status_reg_buf)` | 核心内部 |

⚠️ **SPMI 那 6 个是本子系统唯一"配置错误直接 panic"的地方。**
**arm64 上 SPMI 主要出现在 Qualcomm PMIC 上**，
**写 SPMI regmap 时地址宽度必须与所选变体匹配**（base 版 1 字节 / ext 版 2 字节），
否则不是 `-EINVAL` 而是 panic。

---

## §5 错误码分诊

### §5.1 普查（20 个 `.c` 文件，口径见 99 §6）

| 错误码 | 出现次数 | 占比 | 主要语义 |
|---|---|---|---|
| `-EINVAL` | **102** | **52%** | **三大类，见 §5.2** |
| `-ENOMEM` | **49** | 25% | 分配失败，无歧义 |
| `-ENOTSUPP` | **16** | 8% | **能力不支持**（如 bus 没提供对应回调） |
| `-EFAULT` | **12** | 6% | **几乎全在 `regmap-debugfs.c`**（`copy_from_user` 失败） |
| `-ENODEV` | 8 | 4% | **`dev_get_regmap` 找不到等**（05 §3.1） |
| ⚠️`-EIO` | **7** | 3.5% | **两个来源，见 §5.3** |
| `-EBUSY` | 2 | 1% | |
| `-EOPNOTSUPP` | 2 | 1% | **和 `-ENOTSUPP` 并存，语义重叠** |
| `-ETIMEDOUT` | 1 | 0.5% | **`regmap_read_poll_timeout` 宏**（05 §1.3） |
| `-EPERM` / `-EAGAIN` / `-ERANGE` / `-ENOSPC` | **全 0** | — | **本子系统不用这几个** |

重跑：`for e in EINVAL ENOMEM ENOTSUPP EFAULT ENODEV EIO EBUSY EOPNOTSUPP ETIMEDOUT; do echo -n "-$e "; grep -rho -- "-$e" drivers/base/regmap/*.c | wc -l; done`

### §5.2 ⚠️`-EINVAL` 的三类来源（102 处，必须按语义分组）

| 类别 | 典型出处 | 怎么区分 |
|---|---|---|
| **① config 校验不过**（初始化期） | `regmap-mmio.c:445`（`pad_bits` 非 0）· `:452`（`reg_stride < min_stride`）· `:455`（`use_relaxed_mmio` 与 `io_port` 同开）· `regmap-mmio.c:39`（`reg_bits` 不是 8/16/32）· `:59`（`val_bits` 不是 8/16/32） | **只在 `devm_regmap_init_*` 返回值里出现** ⇒ **regmap 根本没建起来** |
| **② 运行期参数非法** | 地址不对齐（`reg % reg_stride`）、`count` 为 0、debugfs 输入解析失败（`regmap-debugfs.c:323`） | **map 是好的，某一次调用参数错** |
| ⚠️ **③ `CONFIG_REGMAP=n` 空桩** | `include/linux/regmap.h` 的 45 处 `WARN_ONCE` 后 `return -EINVAL` | **唯一判据：dmesg 里有 `WARN_ONCE` 栈，且栈顶在 `include/linux/regmap.h`** |

⚠️ **类别 ③ 最容易误诊成"寄存器号写错"**（01 §9 / §1.1）。
**一条命令排除**：`zcat /proc/config.gz | grep '^CONFIG_REGMAP='`
（**或看 `/sys/kernel/debug/regmap/` 是不是空目录 / 根本不存在**）。

### §5.3 ⚠️`-EIO` 的两个来源（7 处，一码两因）

| 来源 | 位置 | 现象 |
|---|---|---|
| **① 底层总线失败** | 各适配层透传 | **真的硬件/总线问题** |
| ⚠️ **② 写了不可写寄存器** | `drivers/base/regmap/regmap.c:1994-1995` 的 `if (!regmap_writeable(map, reg)) return -EIO;` | **纯配置错误，但码长得像硬件故障** |

**`regmap_writeable()` 在 `drivers/base/regmap/regmap.c:90-102`，三种情况返回假**：
**`max_register` 越界 / `wr_table` 拦下 / `writeable_reg()` 回调返假**。

⇒ **分诊动作（按代价从小到大）**：
1. **`cat /sys/kernel/debug/regmap/<name>/access`** ——**直接看这个寄存器可不可写**（§2.2）
2. **`regmap_get_max_register()` 对一下地址**（01 §7）
3. **开 `regmap_hw_write_start` tracepoint** ——**有事件说明请求到了总线（来源①），没事件说明被核心拦了（来源②）**

**第 3 步是区分两个来源最干脆的办法**，值得先做。

---

## §6 故障注入：本子系统没有

**结论先说**：**regmap 不提供任何故障注入设施。**

**核对过的四条线索全为空**（重跑
`grep -rniE 'fault_inject|should_fail|CONFIG_FAULT|DECLARE_FAULT_ATTR' drivers/base/regmap/`）：

| 检查项 | 结果 |
|---|---|
| `fault_inject` / `should_fail` | **0** |
| `CONFIG_FAULT_INJECTION` 相关 | **0** |
| debugfs 里的注入开关 | **0** ——**8 个文件全是观测或状态切换**（§2.1） |
| 模块参数 | **0**（`grep -rn 'module_param' drivers/base/regmap/` 无输出） |

**最接近故障注入的能力是 §2.3 的两个可写 debugfs 文件**：
**`echo Y > cache_only` 可以模拟"设备不可达"**（写只进缓存），
**`echo Y > cache_bypass` 可以模拟"缓存失效"。**
⚠️ **但它们改的是真实状态、会污染内核（`add_taint`）、而且退出时有副作用（`cache_only=N` 会触发 sync）**
⇒ **不是干净的注入手段，只能在开发板上用。**

**要注入总线错误，得去对应的总线子系统**（I2C / SPI 各自有自己的手段）——
**regmap 只是透传错误码。**

---

## §7 用户态工具

⚠️ **regmap 没有字符设备、没有 sysfs 属性、没有 ioctl。**
**可用的全是通用工具**：

| 工具 | 用途 | 注意 |
|---|---|---|
| `cat` / `echo` on debugfs | §2 | **`registers` 默认只读**（§1.2） |
| `trace-cmd` / `perf` / 裸 ftrace | §3 | **`name` 字段是设备名**（§3.2） |
| **`devmem2` / `busybox devmem`** | **绕过 regmap 直接读物理地址** | **只对 MMIO 有效**；**用来判断"是 regmap 的问题还是硬件的问题"** |
| `i2cget` / `i2cset`（i2c-tools） | **绕过 regmap 直接访问 I2C 芯片** | ⚠️ **和内核驱动抢总线**，只在驱动未绑定时用 |
| `dynamic_debug` | §4.2 的 10 处 `dev_dbg` | `echo 'file regcache.c +p' > /sys/kernel/debug/dynamic_debug/control` |

**"绕过 regmap 直接访问"是本子系统最有价值的一条交叉验证手段**：
**同一个寄存器，debugfs 读到 A、devmem 读到 B ⇒ 问题在 regmap 的配置里
（字节序 / 分页 / 缓存），不在硬件上。**

---

## §8 一页速查

```bash
### 有哪些 regmap（目录名 = <设备名>-<config.name>，§2.6）
ls /sys/kernel/debug/regmap/

### 全量转储 + 逐寄存器属性（排"写不进去"）
cat /sys/kernel/debug/regmap/<name>/registers
cat /sys/kernel/debug/regmap/<name>/access
cat /sys/kernel/debug/regmap/<name>/range     # 分页

### 缓存状态（只在 cache_type != NONE 时存在）
cat /sys/kernel/debug/regmap/<name>/cache_dirty      # 只读
echo Y > /sys/kernel/debug/regmap/<name>/cache_only  # 会 taint 内核
echo N > /sys/kernel/debug/regmap/<name>/cache_only  # 会触发 regcache_sync()

### tracepoint（name 字段 = 设备名，不含 config.name）
cd /sys/kernel/debug/tracing
echo 1 > events/regmap/regmap_reg_write/enable
echo 1 > events/regmap/regmap_reg_read/enable
echo 1 > events/regmap/regmap_reg_read_cache/enable   # 区分缓存命中
echo 1 > events/regmap/regmap_hw_write_start/enable   # 真正的总线传输
echo 1 > events/regmap/regcache_sync/enable
cat trace_pipe

### dev_dbg（默认不输出）
echo 'file regcache.c +p' > /sys/kernel/debug/dynamic_debug/control

### 确认 CONFIG_REGMAP 开了（排 §5.2 类别 ③）
zcat /proc/config.gz | grep '^CONFIG_REGMAP'
```

**必须改源码重编译的两项**（§1.2）：
`drivers/base/regmap/regmap.c:33` 的 `LOG_DEVICE` ·
`drivers/base/regmap/regmap-debugfs.c:293` 的 `REGMAP_ALLOW_WRITE_DEBUGFS`

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| 为什么设计成这样 | `08-feynman.md` |
| 错误码的完整语义 | `01-api.md` §9 |
| 缓存机制 | `04-extensions.md` §2 |
| DT 属性静默失效 | `06-devicetree.md` §4 |
