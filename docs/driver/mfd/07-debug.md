---
title: 调试方法
---

# mfd 07：调试与观测

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇的前提必须先说清：**mfd 核心没有任何属于自己的观测设施。**
所有能看的东西都是**借**来的 —— platform bus 的 sysfs、regmap 的 debugfs、dmesg。

---

## §1 观测面盘点：五个"无"

| 设施 | 有无 | 取证 |
|---|---|---|
| debugfs | ⚠️ **无** | `mfd-core.c` / `core.h` / `syscon.c` / `syscon.h` 里 `debugfs` 零命中 |
| sysfs 属性 | ⚠️ **无** | 同上，`DEVICE_ATTR` / `ATTRIBUTE_GROUPS` 零命中 |
| tracepoint | ⚠️ **无** | `include/trace/events/` 下无 mfd 相关头 |
| uapi / 字符设备 / ioctl | ⚠️ **无** | `include/uapi/linux/` 下无 mfd 头（99 §1.2 门 2） |
| procfs | ⚠️ **无** | — |

⇒ ⚠️⚠️ **这与本套之前的子系统形成极端反差**：pwm 有 class + debugfs + 2 个 tracepoint，
dma-buf 有 debugfs + sysfs 统计。**mfd 一个都没有。**

⚠️ **原因不是疏漏，而是"没有对象可展示"**：mfd 核心运行期不持有任何状态
（唯一的全局链表 `mfd_of_node_list` 只在注册/注销的一瞬间被用，03 §3）。
**它做完 `platform_device_add()` 就退场了** ⇒ 想看的东西都在 platform bus 上（08 §3）。

⇒ ⚠️ **调 mfd 的正确姿势是"调 platform bus + 调 regmap"**，本篇剩下的部分就是这两件事怎么做。

---

## §2 核心一共只说四句话

**`drivers/mfd/mfd-core.c` 471 行里全部的日志语句就这四条**：

| 位置 | 语句 | 级别 | 什么时候出现 |
|---|---|---|---|
| `:41` | `"No .enable() call-back registered"` | `dev_dbg` | 调 `mfd_cell_enable()` 但 cell 没填 `enable`（01 §2.4） |
| `:54` | `"No .disable() call-back registered"` | `dev_dbg` | 同上，`disable` |
| `:242` | `"%s: Failed to locate of_node [id: %d]"` | ⚠️ `pr_warn` | cell 有 `of_compatible` 但 DT 里找不到匹配子节点 |
| `:275` | `WARN_ON(cell->resources[r].start != cell->resources[r].end)` | ⚠️ `WARN` | 传了 `domain` 却给了一段 IRQ **区间**而不是单个中断 |

⚠️⚠️ **两条硬结论**：

1. **只有一条默认可见的消息**（`:242` 的 `pr_warn`）—— 另两条要开 dynamic debug。
2. ⚠️⚠️ **所有真正的失败都是静默的**：`mfd_add_device()` 的九个 `goto` 标签
   （`:315-333`）**没有一条打日志**，只把错误码原样返回。

⇒ ⚠️ **`mfd_add_devices()` 返回负值时，dmesg 里通常什么都没有** ——
**日志得由父驱动自己打**（04 §2.3 的 axp20x 就打了）。
⚠️ **父驱动没打日志 + 核心不打日志 ⇒ probe 失败但 dmesg 干净**，这是最难查的一种。

⚠️ `:275` 那个 `WARN_ON` 值得单独说：它的注释是
`/* Unable to create mappings for IRQ ranges. */`（`:274`）
⇒ **传了 `domain` 时，一条 IRQ 资源只能表示一个中断**
（因为 `irq_create_mapping()` 一次只映射一个 hwirq，`:277-278`）。
⚠️⚠️ **`WARN_ON` 只是警告，代码照样往下走** ⇒ `res[r].start = res[r].end = 映射结果`
⇒ **区间的其余中断被静默丢弃**。看到这条栈回溯就去改 cell 的 `resources[]`（02 §2.7）。

---

## §3 第一步：确认这个设备到底是不是 mfd 建的

```c
/* drivers/mfd/mfd-core.c:32-34 */
static struct device_type mfd_dev_type = {
        .name   = "mfd_device",
};
```

`:202` 把它打在每个子设备上：`pdev->dev.type = &mfd_dev_type;`
`:381` 靠它认出"这是我建的"（`if (dev->type != &mfd_dev_type) return 0;`）。

⚠️⚠️ **而 `device_type.name` 会进 uevent**（`drivers/base/core.c:2595`
的 `add_uevent_var(env, "DEVTYPE=%s", dev->type->name)`）
⇒ **用户态可以直接看**：

```sh
# 这个 platform_device 是 mfd 核心创建的吗？
grep DEVTYPE /sys/bus/platform/devices/axp20x-gpio/uevent
# DEVTYPE=mfd_device   ⇒ 是

# 一次列出系统里所有 mfd 子设备
grep -l 'DEVTYPE=mfd_device' /sys/bus/platform/devices/*/uevent
```

⇒ ⚠️ **这是全篇唯一一条"mfd 专属"的用户态可见信息**，
而且**没有任何官方文档提到过它**（99 §1.4）。
⚠️ 它的价值在于**排除法**：设备存在但 `DEVTYPE` 不是 `mfd_device`
⇒ 它是 DT（`simple-mfd`/`of_platform_populate`）实例化的，**去查 DT，别查父驱动的 cell 数组**（06 §3）。

**紧跟着看三个链接**：

```sh
D=/sys/bus/platform/devices/axp20x-gpio
readlink -f $D/driver          # 绑上了哪个驱动；没有这个链接 ⇒ 无驱动匹配
readlink -f $D/../             # 父设备（应是 i2c-x/x-0034 之类）
ls $D/of_node                  # 有没有拿到 DT 子节点  ⇒ 对应 §5.1
cat $D/uevent | grep OF_       # OF_NAME / OF_COMPATIBLE，可核对匹配到了哪个节点
```

---

## §4 症状一：子设备根本不出现

⚠️ **"不出现"= `/sys/bus/platform/devices/` 里连目录都没有。** 按这个顺序查：

```
父驱动 probe 成功了吗？
   │  ls /sys/bus/i2c/devices/*/driver   （父设备有没有绑上驱动）
   │
   ├─ 没有 ──▶ ⚠️ 问题不在 mfd。查 DT 的父节点 status / compatible / I2C 地址
   │
   └─ 成功 ──▶ dmesg 里有 "failed to add MFD devices" 之类吗？
                 │
                 ├─ 有 ──▶ 父驱动自己打的 ⇒ 看它带的错误码（§4.3）
                 │
                 └─ 没有 ──▶ ⚠️⚠️ 最常见的一支：核心把它"成功地跳过了"
                              │
                              └─ 查 DT 子节点的 status（§4.1）
```

### §4.1 ⚠️⚠️ 头号原因：DT 子节点是 `disabled`

```c
/* drivers/mfd/mfd-core.c:234-238 */
if (disabled) {
        /* Ignore 'disabled' devices error free */
        ret = 0;
        goto fail_alias;
}
```

⚠️⚠️ **`ret = 0` 然后跳失败路径** ⇒ 子设备被销毁，
而 `mfd_add_device()` 返回 **0**，`mfd_add_devices()` 也返回 0，**父驱动 probe 成功**。
⇒ **没有任何一行日志**（03 §2.4 出口 B）。

**现场怎么确认**：

```sh
# 目标：找到父节点下那个 compatible 与 cell->of_compatible 相同的子节点
find /proc/device-tree -name 'compatible' | xargs grep -l 'axp803-ac-power-supply'
# 然后看它同级有没有 status
cat /proc/device-tree/soc/.../ac-power/status     # 输出 "disabled" ⇒ 就是它
```

⚠️ **`/proc/device-tree` 反映的是内核实际用的 DT**（含所有 overlay 与 `&label` 覆盖）
⇒ **比读 dts 源文件可靠**。板级 dts 里的 `&ac_power_supply { status = "okay"; }`
生效与否，只有这里说得准（06 §2.3）。

### §4.2 子设备存在但没有驱动绑定

⚠️ 这是**另一回事**：`/sys/bus/platform/devices/mychip-gpio` 有目录，但没有 `driver` 链接。

| 原因 | 确认方法 |
|---|---|
| ⚠️ `cell->name` 与子驱动的 `driver.name` 不一致 | `ls /sys/bus/platform/drivers/` 找真实驱动名，与 cell 名比 |
| 子驱动没编进内核 | `zcat /proc/config.gz \| grep <config>` 或 `modprobe` 试 |
| ⚠️ 子驱动有 `of_match_table` 但设备没 `of_node` | `ls $D/of_node` 为空 ⇒ 走 §5.1 |
| ⚠️ 用了 `PLATFORM_DEVID_AUTO`，名字带编号 | 设备叫 `mychip-gpio.3`，**platform 匹配用的是不带后缀的名字**，这条本身不影响匹配 |

⚠️⚠️ **`PLATFORM_DEVID_AUTO` 的真正副作用在脚本层**：编号由全局计数器给，
**换个内核版本、改个 probe 顺序，`.3` 就变成 `.5`**
⇒ **udev 规则、测试脚本、`/sys` 路径都不能硬编码这个数字**（01 §2.6）。
⚠️ `PLATFORM_DEVID_NONE`（`-1`）则完全没有后缀，代价是**同型芯片只能有一颗**（04 §2.3）。

### §4.3 `mfd_add_devices()` 返回了错误码，怎么定位是哪一步

⚠️ **核心只产生两种错误码**（基线表）：`-EAGAIN` 4 处（全是 OF 匹配的内部信号，
不会传出去）· `-ENOMEM` 3 处。
⇒ ⚠️⚠️ **`mfd_add_devices()` 返回的非 `-ENOMEM` 错误码，全部来自它调的别人**：

| 错误码 | 最可能来自 | 对应行 |
|---|---|---|
| `-ENOMEM` | `platform_device_alloc()` / `kcalloc(res)` / cell 链表项 | 核心自己 |
| `-EBUSY` | ⚠️ `acpi_check_resource_conflict()` 或 `platform_device_add()` 的资源冲突 | `:293`（arm64 上前者不成立，99 §2.7） |
| `-EEXIST` | ⚠️ **同名子设备已存在** ⇒ 两颗芯片都用了 `PLATFORM_DEVID_NONE` | `platform_device_add()` |
| 其它 | `platform_device_add_data()` / `device_add_software_node()` / regulator 别名注册 | `:249` · `:257` · `:207` |

⚠️ **部分失败时核心会把前面成功的全删掉**（`:366-368` 的 `if (i) mfd_remove_devices(parent)`）
⇒ ⚠️⚠️ **表面上"一个子设备都没有"，实际上是第 N 个失败拖垮了前 N-1 个**。
⇒ **想知道是第几个失败的，只能上 ftrace**（§8）——核心不打这条日志。

---

## §5 症状二：子设备出现了，但行为不对

### §5.1 ⚠️ `Failed to locate of_node` —— 唯一一条默认可见的核心日志

```c
/* drivers/mfd/mfd-core.c:241-243 */
if (!pdev->dev.of_node)
        pr_warn("%s: Failed to locate of_node [id: %d]\n",
                cell->name, platform_id);
```

⚠️⚠️ **这条 `pr_warn` 有两个坑**：

1. ⚠️ **它是 `pr_warn` 不是 `dev_warn`** ⇒ **日志里没有设备前缀**，
   只有 `cell->name` 和 platform id。想知道是哪颗芯片的，得自己按 cell 名反查。
2. ⚠️⚠️ **打了警告之后照样注册**（03 §2.4 出口 C）⇒ 子设备存在、能绑驱动，
   **只是 `dev->of_node == NULL`** ⇒ **症状出现在子驱动里**
   （`of_property_read_u32()` 全返回 `-EINVAL`、`device_property_*` 拿不到值）。

⚠️ **两种情况都会打它，必须分清**：

| 情况 | 是不是问题 |
|---|---|
| cell 填了 `of_compatible`，DT 里没有对应子节点 | ⚠️ **是问题** —— 少写了 DT 子节点，或 compatible 拼错 |
| cell **没有** `of_compatible`（只有 `name`） | ⚠️ **不是问题** —— 设计如此（如 `axp20x-regulator`，04 §2.1），**这条警告是噪声** |

⇒ ⚠️⚠️ **`drivers/mfd/` 里大量驱动会在启动时刷出一串这种"正常的"警告**
⇒ **看到它先去看 cell 有没有 `of_compatible`，别急着改 DT。**

### §5.2 子驱动的寄存器地址不对

⚠️ 三种资源换算路径互相独立（03 §2.7），错了症状各不相同：

| cell 里的 flags | `mem_base` | 结果 | 症状 |
|---|---|---|---|
| `IORESOURCE_MEM` | 非 `NULL` | `mem_base->start + 偏移`（`:266-271`） | 正常 |
| ⚠️ `IORESOURCE_MEM` | ⚠️ `NULL` | ⚠️ **原样当绝对地址用** | ⚠️⚠️ **`ioremap` 到一个随机物理地址** —— 读回全 `0xff`，或直接总线异常 |
| `IORESOURCE_REG` | 任意 | 原样拷贝（`:285-289` 那一支） | 正常（ocelot 就靠这个，04 §4.2） |

**现场确认**：

```sh
cat /sys/bus/platform/devices/mychip-foo/resource     # 核心换算后的最终值
grep -i mychip /proc/iomem                            # 有没有真的落在父设备窗口里
```

⚠️ **`/sys/.../resource` 打印的就是 `res[]` 换算后的内容** ⇒
**它与你在 cell 里写的数不一样是正常的**，与 `/proc/iomem` 里父设备的窗口对不上才是 bug。

### §5.3 ⚠️⚠️ 中断不对：先确认父驱动传没传 `domain`

这是 mfd 上最容易错、也最难看出来的一类（04 §2.2、05 §3.3）。

```sh
cat /sys/bus/platform/devices/axp20x-pek/resource   # IRQ 资源里的数字
cat /proc/interrupts | head -40                     # 这个数字是不是一个真的 virq
```

⚠️ **判据**：`resource` 里的 IRQ 数字**在 `/proc/interrupts` 里查不到**
⇒ 它是芯片内部的 hwirq，**父驱动没传 `domain`**
⇒ 子驱动必须自己 `regmap_irq_get_virq()`（`drivers/input/misc/axp20x-pek.c:233-243`）。

⚠️⚠️ **弄错的两种症状**：

| 写法 | 父驱动传了 `domain` | 父驱动没传 |
|---|---|---|
| 直接 `platform_get_irq()` + `request_irq()` | 正常 | ⚠️⚠️ **`-EINVAL`，或更糟：申请到编号相同的另一个中断** |
| 再调 `regmap_irq_get_virq()` | ⚠️ **二次翻译，拿到垃圾** | 正常 |

⇒ ⚠️ **"申请到别人的中断"是本子系统最恶劣的一种故障**：不报错、
偶发地在别的设备触发时执行你的处理函数。**唯一的防线是先读父驱动那一行
`mfd_add_devices()` 的第七个参数。**

⚠️ 另外看到 `mfd-core.c:275` 的 `WARN_ON` 栈回溯 ⇒ 是 §2 那条：
**传了 `domain` 却在 cell 里写了 IRQ 区间**，区间里除第一个之外的中断被丢掉。

---

## §6 syscon 怎么观测

### §6.1 ⚠️⚠️ 它在 debugfs 里的名字前面有个 `dummy-`

这一节推导出来的结论**没有任何文档写过**，但它是查 syscon 的唯一入口，值得逐步看：

```c
/* drivers/mfd/syscon.c:103 · :112 */
syscon_config.name = kasprintf(GFP_KERNEL, "%pOFn@%pa", np, &res.start);
...
regmap = regmap_init_mmio(NULL, base, &syscon_config);
```

⚠️ 两个关键点：
- `:103` 把 regmap 的名字设成 **`<节点名>@<物理地址>`**（如 `pmu@ff310000`）；
- ⚠️⚠️ `:112` 的**第一个参数是 `NULL`** —— **这个 regmap 不属于任何 `struct device`。**

于是 regmap 的 debugfs 命名逻辑（`drivers/base/regmap/regmap-debugfs.c:546-606`）走成这样：

| 步骤 | 结果 |
|---|---|
| `:550` `devname = "dummy"` 初值 | — |
| `:581-582` `if (map->dev) devname = dev_name(map->dev)` | ⚠️ **`map->dev` 是 `NULL`，这一步被跳过** ⇒ 仍是 `"dummy"` |
| `:584-587` `name` 非空 ⇒ `debugfs_name = kasprintf("%s-%s", devname, name)` | ⚠️ **`"dummy-pmu@ff310000"`** |
| `:596` `strcmp(name, "dummy")` | 不相等（已经拼过了）⇒ 不再改名 |
| `:606` `debugfs_create_dir(name, regmap_debugfs_root)` | 目录落地 |

⇒ ⚠️⚠️ **每个 DT 路径创建的 syscon 在 debugfs 里都长这样**：

```sh
ls /sys/kernel/debug/regmap/
# dummy-pmu@ff310000  dummy-grf@ff770000  dummy-syscon@0 ...
```

⚠️ **`dummy-` 前缀不是出错，是 `dev == NULL` 的必然结果。**
⇒ ⚠️ **它同时是"这个 regmap 是 syscon 建的"的判据** ——
普通驱动的 regmap 目录名是 `dev_name()`（如 `1-0034`、`ff1a0000.spi`），不会有这个前缀。

**目录里能看什么**（`regmap-debugfs.c:608-625`）：

| 文件 | 内容 |
|---|---|
| `name`（`:608`） | ⚠️ 那个 `%pOFn@%pa` 名字 ⇒ **反查是哪个 DT 节点、哪个物理地址** |
| `range`（`:611`） | 寄存器范围 ⇒ **直接看到 `max_register`**（§6.3） |
| `registers`（`:623`） | ⚠️ **逐个寄存器的值** —— 最有用的一个 |
| `access`（`:625`） | 每个寄存器可读/可写/可易变 |

⚠️ **前置条件是 `CONFIG_DEBUG_FS=y`** —— `regmap-debugfs.o` 由它控制
（`drivers/base/regmap/Makefile:8`）。关掉就什么都没有。

### §6.2 ⚠️ 推论：`dev_get_regmap()` 永远找不到 syscon 的 regmap

⚠️⚠️ 因为 `map->dev == NULL`，而 `dev_get_regmap()` 是按 `dev` 匹配的
（`drivers/base/regmap/regmap.c:663`）
⇒ **不能用 `dev_get_regmap()` 去拿一个 syscon**，
**只能走 `syscon_regmap_lookup_by_*()`**（05 §4）。

⚠️ 第二个推论：`:112` 用的是 **`regmap_init_mmio()` 而不是 `devm_` 版本**，
而且没有设备可挂 ⇒ ⚠️ **这个 regmap 一旦建立就永不释放**，
与 02 §6.3 那条"`syscon_list` 表项永不移除"是同一件事的两面
⇒ **syscon 的对象生命周期 = 内核生命周期**，不用担心它被拔掉（08 §6）。

⚠️ 顺带确认一处**不是 bug 的可疑代码**：`:113` 在 `regmap_init_mmio()` 之后
立刻 `kfree(syscon_config.name)`。**安全** —— regmap 用
`kstrdup_const()` 复制了名字（`drivers/base/regmap/regmap.c:615`），不是存指针。

### §6.3 syscon 的两条错误信息

| 位置 | 语句 | 场合 |
|---|---|---|
| `drivers/mfd/syscon.c:96` | ⚠️ `pr_err("Failed to retrieve valid hwlock: %d\n", ret)` | `hwlocks` 解析失败（非 `-ENOENT`、非 `-EPROBE_DEFER`），06 §4.5 |
| `drivers/mfd/syscon.c:115` | `pr_err("regmap init failed\n")` | ⚠️ **`reg-io-width` 非法值走到这里** |

⚠️⚠️ **两条都是 `pr_err`，都没有节点名** ⇒ 一块板上有十几个 syscon 时，
**看到这条日志不知道是哪个节点**。
⇒ ⚠️ **只能靠"哪个驱动 probe 失败了"反推**：syscon 是被 lookup 时才创建的（03 §6.1），
**失败会以错误码的形式返回给那个 lookup 的驱动**，那个驱动的 `dev_err` 才带得上设备名。

⚠️ **最隐蔽的一种：`regmap_read()` 返回 `-EIO`。**
它不打任何日志，原因是 `max_register` 卡住了（`syscon.c:110`，06 §4.2）：

```sh
cat /sys/kernel/debug/regmap/dummy-pmu@ff310000/range   # 看上界够不够
# 与 DT 对照
cat /proc/device-tree/.../pmu@ff310000/reg | xxd
```

### §6.4 `syscon_probe()` 那条路是死的

```c
/* drivers/mfd/syscon.c:361-372 —— 节选 */
static const struct platform_device_id syscon_ids[] = {
        { "syscon", },
        { }
};

static struct platform_driver syscon_driver = {
        .driver = { .name = "syscon", },
        .probe    = syscon_probe,
        .id_table = syscon_ids,
};
```

⚠️⚠️ **它靠 `id_table` 匹配，即靠 platform_device 的名字叫 `"syscon"`
—— 而 DT 实例化出来的设备名是 `<地址>.<节点名>`，永远匹配不上。**
⇒ **DT 系统上 `syscon_probe()`（`:325-359`）从不执行**（99 §6）。

⚠️ 三条可观察的差异（万一你真在非 DT 场景下用到它）：

| | `of_syscon_register()`（DT 路径） | `syscon_probe()`（platform 路径） |
|---|---|---|
| regmap 挂在谁身上 | ⚠️ `NULL` | `dev`（devm 管理） |
| debugfs 名字 | ⚠️ `dummy-<节点>@<地址>` | `<设备名>` 或 `pdata->label`（`:347`） |
| `max_register` | `resource_size - reg_io_width`（`:110`） | ⚠️ **硬编码 `- 4`**（`:345`） |
| endian / `reg-io-width` / `hwlocks` | ⚠️ **全部支持** | ⚠️ **全部不支持** |

⇒ ⚠️ **两条路的行为不一致，而且死的那条更弱。** 别照它写代码。

---

## §7 症状 → 原因速查

| # | 症状 | 最可能的原因 | 去 |
|---|---|---|---|
| 1 | 子设备目录不存在，dmesg 干净 | ⚠️⚠️ DT 子节点 `status = "disabled"` ⇒ **核心静默跳过并返回 0** | §4.1 |
| 2 | 父驱动 probe 失败但 dmesg 无信息 | ⚠️ 核心的九条失败路径**都不打日志** | §2 |
| 3 | 子设备有目录、无 `driver` 链接 | cell 名与驱动名不符 / 子驱动没编 | §4.2 |
| 4 | `Failed to locate of_node` 一串 | ⚠️ 大多是**正常噪声**（cell 无 `of_compatible`） | §5.1 |
| 5 | 子驱动 `of_property_read_*` 全失败 | 子设备的 `of_node` 是 `NULL` ⇒ `ls $D/of_node` 为空 | §5.1 |
| 6 | `request_irq()` 返回 `-EINVAL` | ⚠️ 父驱动没传 `domain`，资源里是 hwirq | §5.3 |
| 7 | ⚠️⚠️ 中断偶发地在别的设备触发时进来 | ⚠️ **把 hwirq 当 virq 用，申请到了别人的中断** | §5.3 |
| 8 | `WARN_ON` 栈回溯指向 `mfd-core.c:275` | 传了 `domain` 却写了 IRQ 区间，多余的被丢 | §2 |
| 9 | 子驱动读寄存器读到 `0xff` 或总线异常 | ⚠️ `mem_base` 传了 `NULL`，MEM 资源被当绝对地址 | §5.2 |
| 10 | 两个同 compatible 子节点身份互换 | ⚠️ 没设 `use_of_reg` + `of_reg` | 04 §4.1 |
| 11 | unbind 父驱动后某子设备还在 | ⚠️ 它 `level = HIGH` 而你用了 devm 版 | 05 §5.3 |
| 12 | `-EPROBE_DEFER` 重试后子设备越来越多 | ⚠️⚠️ 错误路径漏调 `mfd_remove_devices_late()`（madera 就漏了） | 04 §3.3 |
| 13 | syscon `regmap_read()` 返回 `-EIO` | ⚠️ DT 的 `reg` 长度短 ⇒ `max_register` 卡住，**无日志** | §6.3 |
| 14 | syscon 读回全 0 | ⚠️ 用了 `device_node_to_regmap()`，时钟没挂 | 05 §4.4 |
| 15 | 换 probe 顺序 syscon 行为就变 | ⚠️⚠️ regmap 惰性创建，**首个 lookup 者的 `check_clk` 定终身** | 05 §4.4 |
| 16 | 消费者拿到 `-EPROBE_DEFER` 不收敛 | ⚠️ syscon 的 `hwlocks` 指向的 hwspinlock 起不来 | 06 §4.5 |
| 17 | `dev_get_regmap()` 拿不到 syscon | ⚠️⚠️ **设计如此**，syscon 的 regmap 无宿主设备 | §6.2 |

---

## §8 命令速查

**看 mfd 子设备全景**：

```sh
# 系统里所有 mfd 核心创建的子设备
grep -l 'DEVTYPE=mfd_device' /sys/bus/platform/devices/*/uevent

# 某个子设备的全部信息
D=/sys/bus/platform/devices/axp20x-pek
cat $D/uevent; cat $D/resource; readlink -f $D/driver; readlink -f $D/../
```

**打开核心那两条 `dev_dbg`**（`mfd-core.c:41` `:54`，只与 `mfd_cell_enable()` 有关）：

```sh
echo 'file drivers/mfd/mfd-core.c +p' > /sys/kernel/debug/dynamic_debug/control
```

**看 mfd 分家的实时过程**（核心不打日志，只能靠 ftrace）：

```sh
cd /sys/kernel/debug/tracing
echo 'mfd_add_device' > set_ftrace_filter
echo 'platform_device_add' >> set_ftrace_filter
echo function_graph > current_tracer
echo 1 > tracing_on
# 然后 unbind/bind 父驱动触发
echo <parent-dev> > /sys/bus/i2c/drivers/<parent-drv>/unbind
echo <parent-dev> > /sys/bus/i2c/drivers/<parent-drv>/bind
cat trace
```

⚠️ **`function_graph` 是定位"第几个 cell 失败"的唯一手段**（§4.3）——
`mfd_add_device` 每调一次对应一个 cell，看最后一次的返回值。

**syscon**：

```sh
ls /sys/kernel/debug/regmap/ | grep '^dummy-'      # 所有 syscon
R=/sys/kernel/debug/regmap/dummy-pmu@ff310000
cat $R/name; cat $R/range; cat $R/registers | head
```

**DT 侧核对**（比读 dts 可靠）：

```sh
cat /proc/device-tree/<path>/status         # §4.1 的头号排查项
cat /proc/device-tree/<path>/compatible | tr '\0' '\n'
cat /proc/device-tree/<path>/reg | xxd
```

⚠️⚠️ **最后一条纪律**：mfd 的故障 80% 是**静默**的（§2）。
⇒ **不要指望 dmesg 给线索** —— 排查顺序永远是
**① `/proc/device-tree` 的 `status` → ② `/sys/bus/platform/devices/` 有没有目录
→ ③ `uevent` 的 `DEVTYPE` → ④ `resource` 与 `/proc/interrupts` 对照。**

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 那两条错误码从哪来 | 01 §2、01 §4 |
| `resource` 里的数字是怎么算的 | 03 §2.7 |
| `of_node` 匹配的完整流程 | 03 §2.4、§2.5 |
| syscon 的 regmap 何时创建 | 03 §6.1 |
| hwirq / virq 那两种写法的现场 | 04 §2.2、05 §3.3 |
| DT 该怎么写才不踩这些坑 | 06-devicetree.md |
| 为什么 mfd 敢什么观测面都不做 | 08-feynman.md |
| 本篇结论的取证与推导 | 99-evidence.md |
