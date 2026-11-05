---
title: 调试方法
---

# IIO 调试与观测

> 基线：Linux 6.1.158（`f6e38ae624cf`），架构 arm64。
> 本篇的每个数字后面都跟了重跑命令，命令在定稿前逐条实跑过；若你的树版本不同，先重跑再引用。

---

## §0 读前必读：这个子系统的观测面比你预期的窄

多数子系统的调试篇是「先看 tracepoint，再看 debugfs，最后看日志」。IIO 不能这样读——它的前两项几乎是空的。三条负面结论必须先说，否则读者会花时间去找不存在的东西：

1. **IIO 核心一个 tracepoint 都没有。** 整个 `drivers/iio/` 只有 5 个 `TRACE_EVENT`，全部住在**一个驱动**里（`drivers/iio/imu/bno055/bno055_ser_trace.h`），只有插了那颗芯片才用得上。`include/trace/events/` 下没有任何 iio 文件。
2. **`drivers/iio/Kconfig` 里没有任何 DEBUG 开关。** 它一共 8 个选项，全是功能裁剪，没有一个是「打开调试」。
3. **debugfs 只有一个文件**，叫 `direct_reg_access`，而且只有主动实现了 `.debugfs_reg_access` 回调的驱动才有（51 个赋值点 / 50 个文件，全树 500+ 个 IIO 驱动里的十分之一）。

于是 IIO 的真实观测面是：**sysfs 是主战场，字符设备 + `tools/iio` 是数据面唯一的观测手段，日志少得可以逐条列完**。本篇按这个真实分布组织，而不是按通用调试篇的套路。

### §0.1 观测手段分诊表

| 手段 | 本子系统有没有 | 覆盖范围 | 打开需要什么 | 本篇章节 |
|---|---|---|---|---|
| tracepoint | 核心 0 个；全子系统 5 个 | 仅 `bno055_ser` 一个驱动的串口收发 | `CONFIG_BNO055_SERIAL` + `CONFIG_TRACING` | §4 |
| debugfs | 1 个文件 `direct_reg_access` | 仅实现了 `.debugfs_reg_access` 的 50 个驱动 | `CONFIG_DEBUG_FS` 且驱动填了回调 | §3 |
| sysfs | 完整 | 每个 IIO 设备的通道、缓冲区、触发器、事件 | `CONFIG_IIO` 本身；缓冲区节点还要 `IIO_BUFFER` | §2 |
| 字符设备 `/dev/iio:deviceN` | 有 | 缓冲区数据流 + 事件流 | `CONFIG_IIO_BUFFER` 或事件能力二者之一 | §5 |
| 内核日志 | 极少 | 核心九个文件加起来 28 条打印 | 默认；6 条 `dev_dbg` 要动态开 | §7 |
| 动态调试 | 有，但只 6 条 | 全部在 `industrialio-buffer.c` | `CONFIG_DYNAMIC_DEBUG` | §1.2、§4.3 |
| 正式故障注入框架 | **没有** | 不适用（覆盖范围为空） | 无开关可打开：`drivers/iio/` 与 `include/linux/iio/` 里 `FAULT_INJECTION` / `should_fail` / `fault_attr` 命中数为 0 | §6 |
| 故障注入替代物 | 有 | `dummy` 驱动的 `poke_evN` 造事件 | `CONFIG_IIO_DUMMY_EVGEN` | §6.2 |
| KUnit 单元测试 | 有，2 个 | `iio-rescale` 换算、值格式化 | `CONFIG_KUNIT` + 对应开关 | §6.3 |

重跑前三行：

```bash
# tracepoint 全普查（核心期望 0，全子系统期望 5 个 TRACE_EVENT）
grep -rn 'TRACE_EVENT\|DEFINE_EVENT\|DECLARE_EVENT_CLASS' --include=*.c --include=*.h drivers/iio/
ls include/trace/events/ | grep -i iio          # 期望：无输出

# Kconfig 里有没有 DEBUG 开关（期望：无输出）
grep -n 'config.*DEBUG' drivers/iio/Kconfig drivers/iio/*/Kconfig

# 提供 direct_reg_access 的驱动数
grep -rln '\.debugfs_reg_access' --include=*.c drivers/iio/ | wc -l    # 期望 50
grep -rn  '\.debugfs_reg_access' --include=*.c drivers/iio/ | wc -l    # 期望 51
```

两个数字不一样不是笔误：`drivers/iio/adc/ina2xx-adc.c` 一个文件里有两个赋值点（它注册两种 `iio_info`）。

### §0.2 按症状找章节

| 我遇到的现象 | 先看哪一节 |
|---|---|
| `/sys/bus/iio/devices/` 底下空的，设备根本没出现 | §7.4 注册期打印表 |
| 设备出现了，但 `in_voltage0_raw` 读出来是 `-EBUSY` / `-EINVAL` | §7.3 同码多源分组 |
| 想确认芯片寄存器里到底是什么值 | §3 `direct_reg_access` |
| 缓冲区 `enable` 写 1 失败，或者写成功但没数据 | §2.2 缓冲区属性 + §4.3 动态调试 |
| `/dev/iio:deviceN` 读出来是一堆二进制，不知道怎么解 | §5.3 + §5.4 `iio_generic_buffer` |
| 事件（阈值、跌落）不触发，想知道是芯片没报还是内核丢了 | §5.3 事件通道 + §6.2 造一个假事件试 |
| 想在没有硬件的机器上跑通整条链路 | §6.2 dummy 驱动 |
| dmesg 里有一条 IIO 的报错，想知道谁打的 | §7.4 映射表 |
| 想追一次采样从驱动到用户态的完整时序 | §4.1 —— 得先接受「没有 tracepoint 可用」 |

### §0.3 一句话结论

**IIO 的调试是「静态观察 + 用户态工具」型，不是「打开开关看 trace」型。** 你的三个主要动作是：读 sysfs 确认拓扑和参数是否如预期（§2）、用 `tools/iio` 里的工具把数据面跑通（§5.4）、在只有 50 个驱动支持的前提下用 `direct_reg_access` 落到寄存器（§3）。内核日志只在注册期说话，运行期几乎全静音（§7）。

---

## §1 编译期开关

### §1.1 `drivers/iio/Kconfig` 的全部 8 个选项

一个都不是调试开关。全表如下：

| 选项 | 位置 | 类型 | 缺省 | 作用 | 关掉会怎样 |
|---|---|---|---|---|---|
| `IIO` | `drivers/iio/Kconfig:6` | tristate | n；被具体传感器驱动 `depends on IIO` 带起来 | 整个子系统 | `/sys/bus/iio/` 不存在 |
| `IIO_BUFFER` | `:15` | bool | n；`drivers/iio/buffer/Kconfig` 整段在它的 `if` 里 | 缓冲区框架 | 无 `bufferN/` 目录，无缓冲区字符设备 |
| `IIO_CONFIGFS` | `:25` | tristate | n；被 `IIO_SW_DEVICE` 和 `IIO_SW_TRIGGER` `select` 拉起（`:51`、`:59`） | configfs 接口 | 无法在运行期造软件设备/触发器 |
| `IIO_TRIGGER` | `:33` | bool | n；被 `IIO_TRIGGERED_EVENT` `select` 拉起（`:67`） | 触发器框架 | 无 `trigger/` 目录、无 `current_trigger` |
| `IIO_CONSUMERS_PER_TRIGGER` | `:41` | int | 2（`:44` 显式 `default "2"`，全表唯一一条 `default`） | 每个触发器能挂几个消费者 | 见下面的注意 |
| `IIO_SW_DEVICE` | `:49` | tristate | n；没有谁 `select` 它，只能手工开 | 软件设备框架 | configfs 里无 `devices/` |
| `IIO_SW_TRIGGER` | `:57` | tristate | n；没有谁 `select` 它，只能手工开 | 软件触发器框架 | configfs 里无 `triggers/` |
| `IIO_TRIGGERED_EVENT` | `:65` | tristate | n；全树只有一处 `select` 它（`drivers/iio/adc/Kconfig:472`） | 触发器驱动的事件辅助 | 依赖它的驱动编不出来 |

「缺省 n」这一列不是抄来的：`drivers/iio/Kconfig` 里**只有 `:44` 一条 `default` 行**（`grep -n '^\tdefault' drivers/iio/Kconfig` → 只有这一行），其余七个选项都没写 `default`，Kconfig 对 bool / tristate 的无 `default` 缺省就是 n。所以真正决定它们开不开的是**别人的 `select` 和 `depends on`**，这一列填的就是那个来源。

`IIO_CONSUMERS_PER_TRIGGER` 是这张表里唯一一个**会以运行期报错的形式咬人**的选项。它缺省 2，超了就在 `industrialio-trigger.c:288` 打印：

```
Could not find an available irq for trigger %s, CONFIG_IIO_CONSUMERS_PER_TRIGGER=%d limit might be exceeded
```

这条消息把 Kconfig 名字直接写进了运行期日志——遇到它不要去查驱动，去调 Kconfig。

重跑：

```bash
grep -n '^config\|^\tint\|^\tbool\|^\ttristate' drivers/iio/Kconfig
```

### §1.2 真正影响「你能看到多少」的开关，都不在 IIO 自己的 Kconfig 里

| 开关 | 不开的后果 | 在 IIO 里的具体落点 |
|---|---|---|
| `CONFIG_DEBUG_FS` | `direct_reg_access` 整个消失 | `industrialio-core.c:482-489` 是 `#else` 分支下的两个空函数 |
| `CONFIG_DYNAMIC_DEBUG` | 6 条 `dev_dbg` 永远不输出 | 全部在 `industrialio-buffer.c`（§4.3 列全） |
| `CONFIG_TRACING` | 那 5 个 tracepoint 不注册 | 仅 `bno055_ser` 相关 |
| `CONFIG_IIO_BUFFER` | 数据面观测手段全没了 | 字符设备退化成只有事件 fops（`industrialio-core.c:1984-1986` 二选一） |

**`direct_reg_access` 要过三道门**，缺任何一道都是静默没有文件，不打印任何提示：

```
门 1  CONFIG_DEBUG_FS=y          否 → iio_device_register_debugfs() 是空函数（:483-485）
门 2  驱动填了 .debugfs_reg_access 否 → :466-467 直接 return
门 3  顶层 iio/ 目录创建成功       否 → :469-470 直接 return
```

门 3 平时总成立：顶层目录在 `iio_init()` 里建，`industrialio-core.c:362` 一行 `debugfs_create_dir("iio", NULL)`，卸载时 `:377` 删掉。门 3 的那句判空（`:469`）实际上是一道**永不生效的防御性检查**，原因见 §3.1——不要照抄这个写法去判断 debugfs 是否可用。

### §1.3 无硬件调试相关的开关

| 选项 | 位置 | 作用 |
|---|---|---|
| `IIO_DUMMY_EVGEN` | `drivers/iio/dummy/Kconfig:8` | 事件发生器，`select IRQ_SIM`（`:10`） |
| `IIO_SIMPLE_DUMMY` | `drivers/iio/dummy/Kconfig:12` | 假 IIO 设备，有通道有属性 |
| `IIO_SIMPLE_DUMMY_EVENTS` | `:22` | 给假设备加事件能力 |
| `IIO_SIMPLE_DUMMY_BUFFER` | `:32` | 给假设备加缓冲区能力 |
| `IIO_RESCALE_KUNIT_TEST` | `drivers/iio/test/Kconfig:7` | `iio-rescale` 换算的单元测试 |
| `IIO_FORMAT_KUNIT_TEST` | `drivers/iio/test/Kconfig:19` | 值格式化函数的单元测试 |

这六个是 §6 的全部素材。IIO 没有 `fault-inject` 集成，这一节里的 dummy 是最接近「故障注入」的东西。

---

## §2 sysfs：IIO 的主观测面

### §2.1 一个 IIO 设备的目录长什么样

```
/sys/bus/iio/devices/iio:device0/
├── name                     核心创建（industrialio-core.c:1424，只读）
├── label                    核心创建（:1434，只读；DT 的 label 属性，见 06-devicetree.md §3.5）
├── current_timestamp_clock  核心创建（:1532，可写）
├── in_voltage0_raw          核心按 channels[] + info_mask 拼名字创建（§2.3）
├── in_voltage_scale         同上，shared_by 决定有没有通道号
├── sampling_frequency_available   带 _available 后缀的一族（:1274 拼出来）
├── buffer0/                 industrialio-buffer.c:1693 命名（buffer%d）
│   ├── enable  length  watermark  data_available  direction
│   └── in_voltage0_en  in_voltage0_index  in_voltage0_type   （扫描元素）
├── buffer/                  兼容目录，只为第 0 个缓冲区创建（:1460 起名）
├── scan_elements/           兼容目录，同上（:1477 起名）
├── trigger/                 industrialio-trigger.c:500 起名
│   └── current_trigger
└── events/                  industrialio-event.c:514/:549 起名
    └── in_voltage0_thresh_rising_en …（驱动声明的事件属性）
```

`buffer/` 和 `scan_elements/` 这两个**兼容目录只在第 0 个缓冲区上创建**：`industrialio-buffer.c:1704-1705` 一句 `if (index > 0) return 0;` 就跳过了。多缓冲区设备上看不到 `buffer1` 对应的兼容目录不是坏了。

### §2.2 核心自己定义的 11 个属性

驱动声明的通道属性名字千变万化，但下面这 11 个是**核心固定创建的**，名字和语义在所有 IIO 设备上一致。调试时先看这些：

| 属性 | 定义位置 | 权限 | 所在目录 | 读它能确认什么 |
|---|---|---|---|---|
| `name` | `industrialio-core.c:1424` | R | 设备根 | 驱动认为这是什么芯片 |
| `label` | `:1434` | R | 设备根 | DT 里 `label` 属性的值；没写就没有这个文件 |
| `current_timestamp_clock` | `:1532` | RW | 设备根 | 缓冲区时间戳用的是哪个 clock 源 |
| `enable` | `industrialio-buffer.c:1395` | RW | `bufferN/` | 缓冲区开没开；写它是启动采集的动作 |
| `length` | `:1393` | RW | `bufferN/` | 缓冲区能存几个采样 |
| `length_ro` | `:1394` | R | `bufferN/` | 同上，但缓冲区实现不允许改时用这个名字 |
| `watermark` | `:1396` | RW | `bufferN/` | 攒到多少个采样才唤醒读者 |
| `watermark_ro` | `:1397` | R | `bufferN/` | 同上的只读版本 |
| `data_available` | `:1398` | R | `bufferN/` | 现在有多少字节可读——判断「没数据」还是「读者没读」的关键 |
| `direction` | `:1399` | R | `bufferN/` | 这个缓冲区是入还是出 |
| `current_trigger` | `industrialio-trigger.c:492` | RW | `trigger/` | 当前挂的触发器名；空 = 没挂 |

`length` / `length_ro` 和 `watermark` / `watermark_ro` 是**两个名字一个语义**，取决于缓冲区实现允不允许写。所以脚本里不能只 `cat length`——先判断哪个存在。

重跑：

```bash
grep -n 'static DEVICE_ATTR\|IIO_DEV_ATTR\|static IIO_DEVICE_ATTR' \
    drivers/iio/industrialio-core.c drivers/iio/industrialio-buffer.c \
    drivers/iio/industrialio-trigger.c
```

### §2.3 通道属性的名字是拼出来的——这决定了你该怎么找它

一个通道属性名由三张表拼成，全在 `industrialio-core.c` 里：

| 表 | 位置 | 条目数 | 贡献名字的哪一段 |
|---|---|---|---|
| `iio_chan_type_name_spec[]` | `:54-90` | 35 | 前缀，如 `voltage`、`accel`、`temp` |
| `iio_modifier_names[]` | `:92-143` | 50 | 修饰段，如 `x`、`y`、`co2` |
| `iio_chan_info_postfix[]` | `:146-178` | 29 | 后缀，如 `raw`、`scale`、`offset` |

三个数字各自对应一个 uapi 枚举，其中两个是严格一对一、一个不是：`iio_chan_type_name_spec[]` 的 35 项与 `enum iio_chan_type`（`include/uapi/linux/iio/types.h:14`，35 项）逐项对齐；`iio_chan_info_postfix[]` 的 29 项与 `enum iio_chan_info_enum`（`include/linux/iio/types.h:39`，29 项）逐项对齐；而 `iio_modifier_names[]` 只有 50 项，`enum iio_modifier`（`include/uapi/linux/iio/types.h:52`）有 51 项——**少的那一项是 `IIO_NO_MOD`**，它表示「没有修饰符」，本来就不该有名字。

拼装发生在 `__iio_add_chan_devattr()` 的调用者里，主要是 `:1069-1130` 那段 `kasprintf`。`_available` 后缀是**再套一层**拼出来的：`:1273-1275` 用 `kasprintf(GFP_KERNEL, "%s_available", iio_chan_info_postfix[i])`。

实际后果是三条调试规律：

1. **属性名里没有 `raw` 就说明驱动没在 `info_mask` 里置 `IIO_CHAN_INFO_RAW`** ——不是 sysfs 出错。回去看驱动的 `iio_chan_spec` 定义。
2. **有些属性带通道号、有些不带**，取决于 `shared_by`：`IIO_SEPARATE` 带号（`in_voltage0_scale`），`IIO_SHARED_BY_TYPE` 不带（`in_voltage_scale`）。这也是 `industrialio-core.c:1191` 那条 `"tried to double register : %s"` 的来源——见 §7.4。
3. **`_available` 文件存在但读出来空/报错**，说明驱动置了 available 位却没实现 `read_avail` 回调。

重跑三张表的条目数：

```bash
sed -n '54,90p'   drivers/iio/industrialio-core.c | grep -c 'IIO_'          # → 35
sed -n '92,143p'  drivers/iio/industrialio-core.c | grep -c 'IIO_MOD'       # → 50
sed -n '146,178p' drivers/iio/industrialio-core.c | grep -c 'IIO_CHAN_INFO_' # → 29
```

### §2.4 sysfs 上的三条现场命令

```bash
# 1. 全设备一览：名字 + label + 挂了哪个触发器
for d in /sys/bus/iio/devices/iio:device*; do
    echo "== $d"
    cat "$d/name" 2>/dev/null
    cat "$d/label" 2>/dev/null
    cat "$d/trigger/current_trigger" 2>/dev/null
done

# 2. 某个设备到底暴露了哪些通道属性
ls /sys/bus/iio/devices/iio:device0/ | grep -E '_raw$|_scale$|_offset$'

# 3. 缓冲区开着但读不到数据时，先看 data_available
cat /sys/bus/iio/devices/iio:device0/buffer0/enable
cat /sys/bus/iio/devices/iio:device0/buffer0/data_available
cat /sys/bus/iio/devices/iio:device0/buffer0/watermark
```

第 3 组的判读：`enable` 是 1 而 `data_available` 长期是 0，说明触发器没在触发（去看 §7.4 里 `industrialio-buffer.c:884` 那条 `dev_dbg`）；`data_available` 一直大于 0 而用户态 `poll` 不返回，去比对 `watermark`——攒不到水位是不唤醒的。

### §2.5 官方 ABI 文档在哪

`Documentation/ABI/testing/sysfs-bus-iio` 是通用属性的权威描述，另有 53 个 `sysfs-bus-iio-*` 驱动专属文件。这是三等证据（可能落后于代码），但它是唯一记录**属性单位和量纲约定**的地方——代码里读不出「`in_voltage_scale` 的单位是什么」。

```bash
ls Documentation/ABI/testing/ | grep -c '^sysfs-bus-iio'    # 54（含通用那一个）
```

---

## §3 debugfs：只有一个文件，而且语义反直觉

### §3.1 创建路径与三道门

创建函数是 `iio_device_register_debugfs()`（`industrialio-core.c:462-481`），在设备注册流程里被调用；对应的拆除是 `iio_device_unregister_debugfs()`（`:455-460`，一句 `debugfs_remove_recursive`）。

```
iio_device_register_debugfs(indio_dev)                    industrialio-core.c:462
  │
  ├── info->debugfs_reg_access == NULL ?  → return       :466-467   ← 门 2，静默
  ├── !iio_debugfs_dentry ?              → return       :469-470   ← 门 3，见下
  │
  ├── debugfs_create_dir(dev_name(&indio_dev->dev), iio_debugfs_dentry)   :474-476
  │        目录名 = iio:deviceN
  └── debugfs_create_file("direct_reg_access", 0644, …, &iio_debugfs_reg_fops)  :478-480
```

于是路径固定是 `/sys/kernel/debug/iio/iio:deviceN/direct_reg_access`，权限 0644（root 可读可写）。

**门 3 那句判空永不生效。** `debugfs_create_dir()` 失败时返回的是 `ERR_PTR(-ERROR)` 而不是 NULL（`fs/debugfs/inode.c` 的 kerneldoc 明写「If an error occurs, ERR_PTR(-ERROR) will be returned」；`CONFIG_DEBUG_FS=n` 时的桩函数在 `include/linux/debugfs.h:211-215` 返回 `ERR_PTR(-ENODEV)`）。所以 `!iio_debugfs_dentry` 只在指针恰好为 NULL 时成立，而这不是失败的表示形式。

这不是 bug：debugfs 的设计契约就是「后续调用能安全地接受一个错误指针」，kerneldoc 里那句 NOTE 明确说调用者应该忽略错误。真正的后果是——**顶层 debugfs 目录建失败时，你不会看到任何报错，`direct_reg_access` 只是不出现**。别照抄 `:469` 这个判空当作「debugfs 可用吗」的判据。

### §3.2 写语义：一个数是缓存，两个数才动硬件

这是本篇最容易踩的一处。写处理函数 `iio_debugfs_write_reg()`（`:411-447`）用一次 `sscanf(buf, "%i %i", &reg, &val)`（`:426`）分流，**按解析到几个数决定做什么**：

| 你写的内容 | `sscanf` 返回 | 内核做了什么 | 有没有访问硬件 |
|---|---|---|---|
| `0x12` | 1 | 只把 `0x12` 存进 `cached_reg_addr`（`:430`） | **没有** |
| `0x12 0x34` | 2 | 存地址（`:433`）**并且**调 `debugfs_reg_access(…, 0x34, NULL)` 写入（`:434-435`） | 有，写 |
| `hello` | 0 或 EOF | 返回 `-EINVAL`（`:443`） | 没有 |

所以标准用法是**两步**：

```bash
D=/sys/kernel/debug/iio/iio:device0
echo 0x12 > $D/direct_reg_access     # 第一步：只选中寄存器，不碰硬件
cat        $D/direct_reg_access      # 第二步：这一下才真读硬件，输出形如 0x34
```

读处理函数 `iio_debugfs_read_reg()`（`:381-409`）用的就是那个缓存地址（`:394-396` 把 `cached_reg_addr` 和 `val=0` 传进回调），格式固定 `"0x%X\n"`（`:404`）。三个容易忽略的细节：

- **`*ppos > 0` 时不重新读硬件**（`:389-392`），直接从上次的 `read_buf` 里续传。所以同一个 `open` 里反复读不会重复触发硬件访问；每次要新值就重新 `cat`。
- **`read_buf` 只有 20 字节**（`include/linux/iio/iio-opaque.h:72`），刚够放 `0x` + 8 位十六进制 + 换行。
- **写入缓冲区上限 80 字节**（`:417` 的 `char buf[80]`，`:420` 的 `min_t` 截断），超长部分被静默丢弃。

失败时的两条打印都用 `__func__`，所以 dmesg 里出现的是函数名而不是「debugfs」字样：

| 打印文本（逐字） | 位置 | 触发条件 | 可见性 |
|---|---|---|---|
| `iio_debugfs_read_reg: read failed` | `:398` | 驱动回调读失败 | `dev_err`，始终输出 |
| `iio_debugfs_write_reg: write failed` | `:437-438` | 驱动回调写失败 | `dev_err`，始终输出 |

两条都是 `dev_err(indio_dev->dev.parent, …)`——**打印挂在父设备上**（比如 SPI/I2C 从设备），不是 `iio:deviceN`。在 dmesg 里按 `iio:device0` 搜是搜不到的。

### §3.3 只有 50 个驱动提供这个文件

`.debugfs_reg_access` 全树 51 个赋值点、50 个文件，按目录分布：

| 目录 | 赋值点数 |
|---|---|
| `adc` | 16 |
| `frequency` | 7 |
| `accel` | 6 |
| `dac` | 5 |
| `imu` | 4 |
| `gyro` | 3 |
| `imu/inv_icm42600` | 2 |
| `temperature` / `pressure` / `magnetometer` / `light` / `imu/inv_mpu6050` / `imu/bno055` / `filter` / `amplifiers` | 各 1 |

合计 16+7+6+5+4+3+2 + 8×1 = 51。文件数少一个是因为 `drivers/iio/adc/ina2xx-adc.c` 一个文件里有两个赋值点。

```bash
grep -rn '\.debugfs_reg_access' --include=*.c drivers/iio/ \
  | sed 's|drivers/iio/||; s|/[^/]*$||' | sort | uniq -c | sort -rn
```

**先查你的驱动在不在这个名单里**，再决定要不要指望 debugfs。不在名单里时，落到寄存器的替代路径是父总线的调试手段（I2C 的 `i2c-dev` + `i2cget`、SPI 的 `spidev`），那已经出了 IIO 的边界。

---

## §4 tracepoint：核心没有，全子系统只有一个驱动有

### §4.1 先确认这个负面结论

```bash
# 期望输出：只有 bno055 的 6 行（5 个 TRACE_EVENT + 1 个 CREATE_TRACE_POINTS）
grep -rn 'TRACE_EVENT\|DEFINE_EVENT\|DECLARE_EVENT_CLASS\|CREATE_TRACE_POINTS' \
    --include=*.c --include=*.h drivers/iio/

# 期望输出：空
ls include/trace/events/ | grep -i iio
```

结果是：`drivers/iio/imu/bno055/bno055_ser_trace.h` 里 5 个 `TRACE_EVENT`（`:11`、`:28`、`:47`、`:63`、`:79`），`CREATE_TRACE_POINTS` 在 `drivers/iio/imu/bno055/bno055_ser_trace.c:13`。**没有任何 tracepoint 覆盖核心路径**——没有「一次采样开始/结束」、没有「触发器触发」、没有「缓冲区推入」。

这句话的实际含义：**你无法用 ftrace 追一次 IIO 采样的时序。** 想追时序只有三条路：

1. `function_graph` 追具体函数名（不是 tracepoint，开销大得多，且要自己找函数名——用 `03-internals.md` 里的时间线）
2. 缓冲区数据里的时间戳通道（`in_timestamp`，源由 `current_timestamp_clock` 选定，见 §2.2）
3. 那 6 条 `dev_dbg`（§4.3），但它们只覆盖「缓冲区启动失败」，不覆盖正常路径

### §4.2 唯一的 5 个 tracepoint（`bno055_ser`）

TRACE_SYSTEM 名是 `bno055_ser`（`drivers/iio/imu/bno055/bno055_ser_trace.h:9`），所以路径是 `/sys/kernel/debug/tracing/events/bno055_ser/`。

| tracepoint | 定义 | 字段 | 调用点 | 追的是什么 |
|---|---|---|---|---|
| `send_chunk` | `:11` | `len`、`chunk`（动态数组） | `drivers/iio/imu/bno055/bno055_ser_core.c:169` | 往串口写一段原始字节，写之前 |
| `cmd_retry` | `:28` | `read`、`addr`、`retry` | `:255` | 一次命令的第 N 次重试 |
| `write_reg` | `:47` | `addr`、`value` | `:295` | 寄存器写请求（发出前） |
| `read_reg` | `:63` | `addr`、`len` | `:314` | 寄存器读请求（发出前） |
| `recv` | `:79` | `len`、`buf`（动态数组） | `:391` | 从串口收到一段原始字节 |

```bash
cd /sys/kernel/debug/tracing
echo 1 > events/bno055_ser/enable
cat trace_pipe
```

### §4.3 这五个点没有 `_complete` 变体——什么时候这是问题

按方法论的要求逐字核对过两件事，结论分两半：

**`cmd_retry` 的过滤在 C 里，不在 `TP_CONDITION` 里。** 全树 `drivers/iio/` 一处 `TP_CONDITION` 都没有（`grep -rn 'TP_CONDITION' drivers/iio/` 空）。`cmd_retry` 的「只在重试时才记」是调用点的 `if (retry != retry_max)`（`drivers/iio/imu/bno055/bno055_ser_core.c:254`）做的。后果：**第一次尝试永远不出现在 trace 里**，所以 trace 里看到 `cmd_retry #1` 意味着已经发生了两次发送。数重试次数时别把它当成第一次。

**`read_reg` / `write_reg` 有一条真实的「发了但没回」缺口，这是有诊断价值的。** 两个 tracepoint 都打在**发送之前**：

- `trace_read_reg(reg_addr, val_size)` 在 `:314`，随后才 `:316-317` 设置 `expected_data_len`、`response_buf`，再调 `bno055_ser_send_cmd()`。响应回来时走的是另一个 tracepoint `recv`（`:391`，在 serdev 的接收回调里）。
- `trace_write_reg(data[0], data[1])` 在 `:295`，紧接着 `:296` 就 `return bno055_ser_send_cmd(...)`。

于是判读规则是明确的：**`read_reg` 之后应当跟着 `recv`；只有 `read_reg` 没有 `recv`，说明命令发出去了但芯片没应答**——这条链上确实存在提前退出（`bno055_ser_send_cmd` 内部超时 100 ms，见 `:260-261` 的 `wait_for_completion_interruptible_timeout`），所以「缺少后半段」在这里是真信号，不是记录不全。

反过来，`send_chunk`（`:169`）打在 `serdev_device_write()` 之前，写失败会在 `:171-172` / `:174-175` 直接返回错误码而不再打点——所以 `send_chunk` 出现不代表字节真的上了线。

三个调用点所在的函数都返回 `int`（`bno055_ser_send_chunk` `:165`、`bno055_ser_write_reg`、`bno055_ser_read_reg`），没有「提交阶段函数是 void 导致返回值黑洞」的情况。

### §4.4 替代品：6 条 `dev_dbg`，全部只讲「缓冲区为什么没启动」

核心九个文件里的 `dev_dbg` 一共 6 条，全在 `industrialio-buffer.c`，全部在 `enable` 写 1 的那条路径上：

| 打印文本（逐字） | 位置 | 触发条件 | 返回值 |
|---|---|---|---|
| `Buffer not started: buffer parameter update failed (%d)` | `:796-798` | `access->request_update()` 失败 | 原错误码 |
| `At least one scan element must be enabled first` | `:836-837` | 一个扫描元素都没打开 | `-EINVAL` |
| `Buffer not started: no trigger` | `:884` | 模式落到既非 HARDWARE 也非 SOFTWARE 的分支 | `-EINVAL` |
| `Buffer not started: buffer preenable failed (%d)` | `:1077-1078` | `setup_ops->preenable()` 失败 | 原错误码 |
| `Buffer not started: update scan mode failed (%d)` | `:1088-1090` | `info->update_scan_mode()` 返回负值 | 原错误码 |
| `Buffer not started: postenable failed (%d)` | `:1117-1118` | `setup_ops->postenable()` 失败 | 原错误码 |

可见性等级：**六条都是 `dev_dbg`，没有 `_once` / `_ratelimited` 变体**（核心九文件里 `grep '_once\|_ratelimited'` 为空）。即：默认编译进内核但默认不输出，要一条条打开：

```bash
# 打开这个文件里全部 6 条
echo 'file industrialio-buffer.c +p' > /sys/kernel/debug/dynamic_debug/control
# 或只打开一条
echo 'file industrialio-buffer.c line 884 +p' > /sys/kernel/debug/dynamic_debug/control
```

**「`Buffer not started: no trigger`」这条有一个静默陷阱，值得单独记住。** `industrialio-buffer.c:881-885`：

```c
	} else {
		/* Can only occur on first buffer */
		if (indio_dev->modes & INDIO_BUFFER_TRIGGERED)
			dev_dbg(&indio_dev->dev, "Buffer not started: no trigger\n");
		return -EINVAL;
	}
```

`return -EINVAL` 在 `if` **外面**。所以驱动没有声明 `INDIO_BUFFER_TRIGGERED` 时，走到这里是「返回 `-EINVAL`，一个字都不打」——连动态调试打开了也没有。同一函数里 `:875-876` 那条（硬件模式下想挂第二个缓冲区）更彻底，连 `dev_dbg` 都没写。

所以 `echo 1 > bufferN/enable` 得到 `-EINVAL` 而动态调试全开还是没输出时，先查两件事：扫描元素开了吗（对应 `:836`，这条有打印）、驱动的 `modes` 里到底有哪几位（对应 `:881-885`，这条可能没打印）。

### §4.5 全子系统的 `_once` / `_ratelimited` 普查

方法论要求日志可见性细化到变体。IIO 全树只有 **7 处**限速日志，一处都不在核心：

| 位置 | 变体 | arm64 相关 |
|---|---|---|
| `drivers/iio/adc/ti-tsc2046.c:327` | `dev_err_ratelimited` | 是 |
| `drivers/iio/adc/ti-tsc2046.c:419` | `dev_err_ratelimited` | 是 |
| `drivers/iio/adc/ti-tsc2046.c:430` | `dev_err_ratelimited` | 是 |
| `drivers/iio/adc/ad_sigma_delta.c:481` | `dev_err_ratelimited` | 是 |
| `drivers/iio/adc/at91-sama5d2_adc.c:1505` | `pr_info_ratelimited` | 否（arm32 平台） |
| `drivers/iio/light/si1133.c:650` | `dev_err_ratelimited` | 是 |
| `drivers/iio/accel/sca3300.c:501` | `dev_err_ratelimited` | 是 |

重跑命令会给出 8 行，比这张表多一行——多出来的是 `drivers/iio/adc/ti_am335x_adc.c:477` 的 `am335x_tsc_se_set_once(...)`，那是个**函数名**里含 `_once(`，不是日志宏。这是子串匹配的典型误伤，计数时要排掉：

```bash
grep -rn '_once(\|_ratelimited(' --include=*.c drivers/iio/     # 8 行
grep -rn 'dev_[a-z]*_once(\|dev_[a-z]*_ratelimited(\|pr_[a-z]*_once(\|pr_[a-z]*_ratelimited(' \
     --include=*.c drivers/iio/ | wc -l                          # 7 行，这才是日志数
```

---

## §5 字符设备与用户态工具：数据面唯一的观测手段

### §5.1 `/dev/iio:deviceN` 是怎么来的，什么时候不存在

| 步骤 | 位置 | 说明 |
|---|---|---|
| 申请主设备号 | `industrialio-core.c:356` | `alloc_chrdev_region(&iio_devt, 0, IIO_DEV_MAX, "iio")` |
| 选 fops | `:1984` / `:1986` | 有缓冲区 → `iio_buffer_fileops`；否则有事件接口 → `iio_event_fileops` |
| 分配次设备号 | `:1989` | `MKDEV(MAJOR(iio_devt), iio_dev_opaque->id)`，次号就是 `iio:deviceN` 里那个 N |
| 挂上 | `:1996` | `cdev_device_add()` |
| 摘下 | `:2022` | `cdev_device_del()` |

`:1988` 那个条件是关键：**既没有缓冲区又没有事件接口的设备不设 `devt`，也就没有 `/dev/iio:deviceN`**。纯 sysfs 型的 ADC（只有 `in_voltageN_raw`，不带缓冲区）就是这种情况——`/dev/` 下找不到它不是坏了。

两套 fops 的差别只在数据面：

| fops | 位置 | `.read` | `.write` | `.poll` | `.unlocked_ioctl` |
|---|---|---|---|---|---|
| `iio_buffer_fileops` | `:1862-1872` | 有 | 有 | 有 | `iio_ioctl` |
| `iio_event_fileops` | `:1874-1881` | 无 | 无 | 无 | `iio_ioctl` |

事件 fops 没有 `.read`——**事件不是从主字符设备读的**，要先用 ioctl 换一个专门的 fd（§5.3）。直接 `cat /dev/iio:device0` 在一个纯事件设备上会失败，这是设计如此。

### §5.2 主设备节点是独占的——这条坑得最多

`iio_chrdev_open()`（`:1768-1793`）第一句实质动作就是：

```c
	if (test_and_set_bit(IIO_BUSY_BIT_POS, &iio_dev_opaque->flags))
		return -EBUSY;
```

在 `:1775-1776`（`IIO_BUSY_BIT_POS` 定义在 `drivers/iio/iio_core.h:62`）。**同一时刻只能有一个进程打开 `/dev/iio:deviceN`**，第二个拿到 `-EBUSY`。释放在 `iio_chrdev_release()` 的 `:1810`。

实践后果：调试时开着一个 `iio_generic_buffer` 就别再开第二个，也别同时跑别的采集程序。`iio_event_monitor` 特意避开了这个问题——它打开主设备（`tools/iio/iio_event_monitor.c:385`）、立刻 ioctl 换到事件 fd（`:392`）、然后**马上把主设备关掉**（`:406`），所以它和缓冲区读取可以并存。自己写工具时值得照抄这个顺序。

每个缓冲区的独立 fd 也是独占的：`industrialio-buffer.c:1543` 同样用 `test_and_set_bit(IIO_BUSY_BIT_POS, &buffer->flags)`，释放在 `:1583`（失败路径）和 `:1510`（正常关闭）。

### §5.3 两个 ioctl

ioctl 派发是链式的：`iio_ioctl()`（`industrialio-core.c:1829-1860`）遍历 `ioctl_handlers` 链表（`:1847-1851`），谁都不认就返回 `-ENODEV`（`:1853-1854`）。派发前先检查 `!indio_dev->info`（`:1844-1845`）——这是防设备被拔掉后用户态还持有 fd 的崩溃，注释在 `:1840-1843`。

| ioctl | 定义 | 编码 | 处理函数 | 干什么 |
|---|---|---|---|---|
| `IIO_GET_EVENT_FD_IOCTL` | `include/uapi/linux/iio/events.h:27` | `_IOR('i', 0x90, int)` | `industrialio-event.c:502-503` 派发到 `iio_event_getfd()`（`:192`） | 换一个只读事件的 fd |
| `IIO_BUFFER_GET_FD_IOCTL` | `include/uapi/linux/iio/buffer.h:8` | `_IOWR('i', 0x91, int)` | `industrialio-buffer.c:1593-1594` 派发到 `iio_device_buffer_getfd()`（`:1525`） | 换一个指定缓冲区的 fd（多缓冲区设备用） |

**`IIO_GET_EVENT_FD_IOCTL` 返回 `-ENODEV` 的含义是「这个设备没有事件接口」，不是「设备不见了」。** `tools/iio/iio_event_monitor.c:396-397` 就是专门把这个错误码翻译成 `This device does not support events` 的。

事件的数据格式固定 16 字节：

```c
struct iio_event_data {          /* include/uapi/linux/iio/events.h:22-25 */
	__u64	id;
	__s64	timestamp;
};
```

`id` 是位域打包的，用 `include/uapi/linux/iio/events.h:29-41` 那 7 个 `IIO_EVENT_CODE_EXTRACT_*` 宏拆：`TYPE`（`:29`）、`DIR`（`:31`）、`CHAN_TYPE`（`:33`）、`CHAN`（`:37`）、`CHAN2`（`:38`）、`MODIFIER`（`:40`）、`DIFF`（`:41`）。**手工位移拆解容易搞错次序，一定用宏。**

内核侧的事件通路是 kfifo：产生时 `kfifo_put()`（`industrialio-event.c:82`）紧跟一句 `wake_up_poll(&ev_int->wait, EPOLLIN)`（`:84`）；用户态 `poll` 走 `iio_event_poll()`（`:99`），`read` 走 `iio_event_chrdev_read()`（`:118`）里的 `kfifo_to_user()`（`:151`）。fd 打开时会 `kfifo_reset_out()`（`:218`）——**每次拿新的事件 fd 都会丢掉之前积压的事件**，这解释了「刚打开工具时看不到之前发生的事件」。设备注销时 `:598` 一句 `wake_up` 把阻塞的读者放出来。

### §5.4 `tools/iio` 的四个工具

| 工具 | 源文件 | 规模 | 用来干什么 |
|---|---|---|---|
| `lsiio` | `tools/iio/lsiio.c` | 188 行 | 列出所有 IIO 设备和触发器，最快的「有没有认出来」检查 |
| `iio_event_monitor` | `tools/iio/iio_event_monitor.c` | 444 行 | 监听事件流并解码成人话 |
| `iio_generic_buffer` | `tools/iio/iio_generic_buffer.c` | 782 行 | 配置扫描元素、启动缓冲区、按 `scan_elements` 里的 type 正确解码数据 |
| （公共库） | `tools/iio/iio_utils.c` + `.h` | 988 + 79 行 | sysfs 路径遍历、通道类型解析，被上面三个共用 |

```bash
cd /path/to/linux/tools/iio && make
./lsiio                                     # 先确认设备存在
./iio_event_monitor iio:device0             # 事件不来时用这个
./iio_generic_buffer -n <设备name> -c 10    # 抓 10 个采样，自动解码
```

`iio_generic_buffer` 的价值在于**它替你处理了数据解码**。缓冲区里的字节序、位宽、有无符号、右移几位，全在 `scan_elements/in_*_type` 里以形如 `le:s12/16>>4` 的字符串描述；自己解析这个格式极易出错。定位数据面问题时先跑它——如果它能出正确数据，问题在你自己的解码上；如果它也不行，问题在内核或硬件。

`iio_event_monitor` 遇到不认识的事件会打印 `Unknown event: time: %lld, id: %llx`（`tools/iio/iio_event_monitor.c:271`）——**看到这条不是工具坏了，是这个内核版本的枚举值比工具新**，`id` 是原始值，可以自己用 §5.3 的宏拆。

---

## §6 故障注入：IIO 没有正式框架，但有两样能用的替代物

### §6.1 先把负面结论钉住

**IIO 没有接入内核的 `fault-inject` 框架。** 没有 `should_fail()` 调用点、没有 `DECLARE_FAULT_ATTR`、没有 `/sys/kernel/debug/iio/fail_*` 之类的注入开关：

```bash
grep -rn 'fault_inject\|DECLARE_FAULT_ATTR\|should_fail' drivers/iio/      # 期望：空
```

所以「让 `read_raw` 随机失败一次看上层怎么处理」这种事，IIO 里没有现成办法。要做只能改驱动或者用 §6.2 的 dummy 设备。

### §6.2 `dummy` 驱动：无硬件造设备 + 手动造事件

这是 IIO 里最接近故障注入的东西，两个模块配合：

**`iio_dummy_evgen`（`CONFIG_IIO_DUMMY_EVGEN`，`drivers/iio/dummy/Kconfig:8`）** 模拟一个中断控制器，`select IRQ_SIM`（`:10`）。它注册一个挂在 `iio_bus_type` 上的裸 device，名字写死为 `iio_evgen`（`drivers/iio/dummy/iio_dummy_evgen.c:206` 的 `dev_set_name`，注册在 `:207`，初始化函数 `:199-211`），带 10 个只写属性：

| 属性 | 定义位置 | 权限 | 写进去发生什么 |
|---|---|---|---|
| `poke_ev0` … `poke_ev9` | `iio_dummy_evgen.c:159-168` | `S_IWUSR`（0200，只写） | 触发第 N 号模拟中断 |

处理函数 `iio_evgen_poke()`（`:135-157`）做三件事：`kstrtoul` 解析你写的十进制数（`:144`）、把它存进 `regs[N].reg_data`（`:148-149`）、然后 `irq_set_irqchip_state(irq, IRQCHIP_STATE_PENDING, true)`（`:152`）把模拟中断置为待处理。

**这些文件是只写的（0200），`cat` 会失败**——`IIO_DEVICE_ATTR` 的 show 函数传的是 NULL。写法：

```bash
# 属性挂在 iio 总线上那个叫 iio_evgen 的设备下
echo 1 > /sys/bus/iio/devices/iio_evgen/poke_ev0
```

**`iio_simple_dummy`（`CONFIG_IIO_SIMPLE_DUMMY`，`drivers/iio/dummy/Kconfig:12`）** 是假设备本身。它不是普通 platform 驱动，而是注册成**软件设备类型**：`module_iio_sw_device_driver(iio_dummy_device)`（`drivers/iio/dummy/iio_simple_dummy.c:718`），类型名 `"dummy"`（`:713`）。所以实例化走 configfs，不走 DT：

```bash
modprobe industrialio-configfs        # 提供 /sys/kernel/config/iio/
modprobe iio_dummy_evgen
modprobe iio-dummy                    # 或按你的构建产物名
mount -t configfs none /sys/kernel/config   # 若尚未挂载
mkdir /sys/kernel/config/iio/devices/dummy/my_dummy
ls /sys/bus/iio/devices/               # 应出现一个新的 iio:deviceN
```

configfs 的路径由三处拼成：subsystem 名 `"iio"`（`drivers/iio/industrialio-configfs.c:24`）、默认组 `"devices"`（`drivers/iio/industrialio-sw-device.c:164-165`）、类型名 `"dummy"`（上面的 `:713`）。触发器一侧同理，默认组是 `"triggers"`（`drivers/iio/industrialio-sw-trigger.c:168-169`）。

两个可选能力开关配合用：`IIO_SIMPLE_DUMMY_EVENTS`（`drivers/iio/dummy/Kconfig:22`，源码里 6 处 `#ifdef` 守卫，见 `drivers/iio/dummy/iio_simple_dummy.c:48` 起）给假设备加事件能力，`IIO_SIMPLE_DUMMY_BUFFER`（`drivers/iio/dummy/Kconfig:32`）加缓冲区能力。

**这套组合的调试价值**：它让你在没有硬件的机器上验证「事件从内核到用户态这条链路通不通」。做法是一边跑 `iio_event_monitor`，一边 `echo` 进 `poke_evN`——收到了说明链路没问题，问题在你的真实驱动；收不到说明是内核侧或者用户态工具的问题。这比在真硬件上反复试省事得多。

### §6.3 KUnit：两个套件，只覆盖纯函数

| 套件 | Kconfig | 源文件 | 用例 |
|---|---|---|---|
| `iio_format_test_suite` | `IIO_FORMAT_KUNIT_TEST`（`drivers/iio/test/Kconfig:19`） | `drivers/iio/test/iio-test-format.c`（271 行） | 6 个（`:253-260` 的 `KUNIT_CASE` 列表，注册在 `:267`） |
| `iio_rescale_test_suite` | `IIO_RESCALE_KUNIT_TEST`（`drivers/iio/test/Kconfig:7`） | `drivers/iio/test/iio-test-rescale.c`（715 行） | 2 个参数化用例（`:701-702` 的 `KUNIT_CASE_PARAM`，注册在 `:710`） |

覆盖面很窄，两个套件都只测**不接触硬件的纯计算**：`iio_format_value()` 的各种格式（整数、定点、分数、log2 分数、多值、64 位整数）和 `iio-rescale` 的 scale/offset 换算。**核心的注册流程、缓冲区、触发器、事件通路一行都没有单元测试覆盖。**

```bash
./tools/testing/kunit/kunit.py run --kunitconfig drivers/iio/test/
```

### §6.4 手工制造故障的三条现成路径

没有注入框架时，下面三件事是不改代码就能做的：

| 想验证什么 | 怎么做 | 预期观察 |
|---|---|---|
| 上层对「缓冲区启不起来」的处理 | 一个扫描元素都不开就 `echo 1 > bufferN/enable` | 得到 `-EINVAL`；动态调试开着能看到 `At least one scan element must be enabled first`（§4.4） |
| 上层对「设备被占用」的处理 | 开两个进程同时打开 `/dev/iio:deviceN` | 第二个拿到 `-EBUSY`（§5.2） |
| 上层对「设备不支持事件」的处理 | 对一个没有事件接口的设备发 `IIO_GET_EVENT_FD_IOCTL` | 拿到 `-ENODEV`（§5.3） |

三条都是走正常 API 触发确定错误码，不需要打补丁，适合写进回归脚本。

---

## §7 日志与错误码分诊

### §7.1 核心九个文件的全部 28 个打印点

统计范围**显式列出这九个文件**，不用通配符（`drivers/iio/*.c` 会把 `industrialio-triggered-buffer.c` 之类也算进来，数字会变）：

```bash
C="drivers/iio/industrialio-core.c drivers/iio/industrialio-buffer.c \
drivers/iio/industrialio-trigger.c drivers/iio/industrialio-event.c \
drivers/iio/inkern.c drivers/iio/industrialio-sw-device.c \
drivers/iio/industrialio-sw-trigger.c drivers/iio/industrialio-configfs.c \
drivers/iio/industrialio-triggered-event.c"
grep -c 'dev_err(' $C            # 逐文件计数
```

| 文件 | `dev_err` | `dev_info` | `dev_dbg` | `pr_err` | WARN 族 | 合计 |
|---|---|---|---|---|---|---|
| `industrialio-core.c` | 8 | 1 | 0 | 3 | 1 | 13 |
| `industrialio-buffer.c` | 1 | 0 | 6 | 0 | 1 | 8 |
| `industrialio-trigger.c` | 0 | 0 | 0 | 2 | 1 | 3 |
| `inkern.c` | 1 | 0 | 0 | 1 | 0 | 2 |
| `industrialio-sw-device.c` | 0 | 0 | 0 | 1 | 0 | 1 |
| `industrialio-sw-trigger.c` | 0 | 0 | 0 | 1 | 0 | 1 |
| `industrialio-event.c` | 0 | 0 | 0 | 0 | 0 | **0** |
| `industrialio-configfs.c` | 0 | 0 | 0 | 0 | 0 | **0** |
| `industrialio-triggered-event.c` | 0 | 0 | 0 | 0 | 0 | **0** |
| 合计 | 10 | 1 | 6 | 8 | 3 | **28** |

三条口径说明：

- **`dev_err_probe` 在核心里是 0 处**，所以 `grep -c 'dev_err'` 和 `grep -c 'dev_err('` 在这九个文件上给同一个数。（这个陷阱在别的子系统会咬人，`06-devicetree.md` §6.4 就踩到过。）
- **`industrialio-event.c` 一个打印都没有。** 整条事件通路完全静音——事件不来时没有任何内核日志可看，只能靠 §6.2 的 dummy 造一个来对照。
- **可见性等级：这 28 个点里没有一个 `_once` / `_ratelimited` 变体**（见 §4.5 的普查）。18 个 `dev_err`/`pr_err`/`dev_info` 始终输出，6 个 `dev_dbg` 默认不输出，3 个 WARN 族只在首次触发时带栈回溯（`WARN` / `WARN_ON` 自身有 taint 与去重行为，不是 `_ONCE` 变体）。

### §7.2 三个 WARN 族站点

只有 3 个，全都值得逐字记住——它们指的都是**驱动写错了**，不是运行期偶发：

| 站点 | 文本 / 表达式 | 含义 |
|---|---|---|
| `industrialio-core.c:1085` | `WARN(1, "Differential channels must be indexed\n")` | 通道声明了 `differential` 却没置 `indexed`；属性名拼不出来 |
| `industrialio-buffer.c:455` | `WARN(1, "Trying to set scanmask prior to registering buffer\n")` | 在缓冲区注册之前就动扫描掩码，调用顺序错了 |
| `industrialio-trigger.c:124` | `WARN_ON(iio_dev_opaque->trig_readonly)` | 重复设置只读触发器；紧接着 `:126-127` 仍会把 `trig_readonly` 置回 true |

第三条要注意：`WARN_ON` 只是报警，**代码继续往下走**（`:126` 的 `iio_trigger_get`、`:127` 的赋值都会执行）。所以看到这条回溯不等于操作被阻止了。

### §7.3 错误码分布与同码多源分组

九个文件里的错误码原始 grep 命中数：

| 错误码 | grep 命中 | 错误码 | grep 命中 |
|---|---|---|---|
| `-EINVAL` | 71 | `-ENOENT` | 4 |
| `-ENODEV` | 35 | `-EFAULT` | 4 |
| `-ENOMEM` | 30 | `-ERESTARTSYS` | 3 |
| `-EBUSY` | 23 | `-EPERM` | 3 |
| `-EAGAIN` | 5 | `-EFBIG` | 3 |
| `-EPROBE_DEFER` | 2 | `-EEXIST` | 1 |

```bash
for e in EINVAL ENODEV ENOMEM EBUSY EAGAIN ENOENT EFAULT ERESTARTSYS EPERM EFBIG EPROBE_DEFER EEXIST; do
  echo "-$e $(grep -o -- "-$e\b" $C | wc -l)"
done
```

**「grep 命中」不等于「产生这个错误码的地方」。** 命中里混了三类非产生点：kerneldoc 注释里提到错误码（如 `industrialio-core.c:1766`、`:2068`、`:2106`）、把错误码当条件比较（如 `:1251` 的 `if ((ret == -EBUSY) && …)`、`inkern.c:466` 的 `PTR_ERR(chans) != -ENODEV`）、以及 `int ret = -ENODEV` 这类初始化（`:1835`、`inkern.c:30`）。所以下面的分组按**实际产生点**计数，和上表的 grep 数不一致是正常的。

#### `-EBUSY`：23 处命中 → 16 个产生点，五组语义

| 组 | 产生点 | 语义 | 用户可见形式 |
|---|---|---|---|
| A 缓冲区在跑，参数不许改 | `industrialio-buffer.c:521`、`:569`、`:661`、`:1358`；`industrialio-core.c:294` | `iio_buffer_is_active()` 为真就拒绝 | 写 `_en` / `length` / `watermark` / `current_timestamp_clock` 返回 `-EBUSY` |
| B 独占资源已被持有 | `industrialio-core.c:1776`（主字符设备）；`industrialio-buffer.c:1544`（缓冲区 fd）；`industrialio-event.c:206`（事件 fd）；`industrialio-buffer.c:272`、`:285`（新 API 打开后老 read/write 被挡） | `test_and_set_bit(IIO_BUSY_BIT_POS, …)` 或对它的 `test_bit` | `open()` / `ioctl()` / `read()` 返回 `-EBUSY` |
| C 名字或属性重复 | `industrialio-core.c:1193`；`industrialio-sw-trigger.c:51`；`industrialio-sw-device.c:51` | 同名属性重复注册、同名 configfs 类型重复注册 | 注册失败，dmesg 里有 `tried to double register` |
| D 模式断言辅助 API | `industrialio-core.c:2076`、`:2116` | 一对**语义相反**的 API，见下 | 驱动内部返回值 |
| E 触发器切换被拒 | `industrialio-trigger.c:444` | 当前模式是 `INDIO_BUFFER_TRIGGERED` 时不许换触发器 | 写 `current_trigger` 返回 `-EBUSY` |

D 组那对反向 API 是最容易看错的地方：

- `iio_device_claim_direct_mode()`（`:2070-2079`）：**缓冲区开着就返回 `-EBUSY`**，即「我要求现在没人在跑缓冲区」。
- `iio_device_claim_buffer_mode()`（`:2108-2117`）：**缓冲区没开才返回 `-EBUSY`**，即「我要求缓冲区正在跑」。

两个函数名字对称、返回同一个错误码，但触发条件正好相反。看驱动代码时必须看清是哪一个。

B 组里 `industrialio-buffer.c:272`、`:285` 那两处也值得单记：一旦某个缓冲区通过 `IIO_BUFFER_GET_FD_IOCTL` 拿到了独立 fd，**主字符设备上的老 `read()` / `write()` 路径就一律返回 `-EBUSY`**（注释在 `:270`、`:283` 写着 `check if buffer was opened through new API`）。用老工具读一个被新工具打开过的设备就会撞上这个。

#### `-ENODEV`：35 处命中，按语义分三组

| 组 | 主要位置 | 占比 | 语义 |
|---|---|---|---|
| A 消费者取不到通道/属性 | `inkern.c` 全文（`:254`、`:286`、`:306`、`:336`、`:351`、`:480`、`:508`、`:600`、`:619`、`:718`、`:739`、`:765`、`:827`、`:911`、`:931`、`:962` 等） | 最大的一组 | 消费者 API 找不到目标：provider 未注册、`io-channel-names` 里没这个名字、map 表里没这项 |
| B 数据面接口不存在 | `industrialio-buffer.c:50`、`:118`、`:143`、`:186`、`:198`、`:1276`、`:1537`；`industrialio-event.c:130`、`:146`、`:199` | 10 处 | 对着一个没有缓冲区 / 没有事件接口的设备做数据面操作 |
| C ioctl 无人认领 | `industrialio-core.c:1835`（默认值）、`:1854`（链表走完没人处理） | 2 处 | 发了一个这个设备不支持的 ioctl |

**A 组和 C 组共用 `-ENODEV`，但排查方向完全相反**：A 组要去查 DT 和 provider 有没有 probe 成功（顺着 `06-devicetree.md` §6.3 的决策树走），C 组要去查用户态发的 ioctl 号对不对（§5.3）。B 组则是「设备能力不匹配」，查 `modes` 和有没有 `event_interface`。

`inkern.c` 里还有一个专门的模式要注意：`:265`、`:280`、`:390`、`:466` 这几处是**拿 `-ENODEV` 当「继续尝试下一条路径」的信号**（`if (!IS_ERR(chan) || PTR_ERR(chan) != -ENODEV)` 就停下），只有 `-ENODEV` 才继续往上走 `io-channel-ranges` 那条链。所以 provider 返回别的错误码会让整个查找立刻中止——这条语义在 `06-devicetree.md` §3.2 里已经展开过。

#### `-EINVAL`：71 处命中，按文件与语义分组

| 文件 | 命中 | 主要语义类 |
|---|---|---|
| `industrialio-core.c` | 24 | 通道声明自相矛盾（`:1085` 的 differential/indexed）、扫描索引重复（`:1899`）、labels 与 extend_name 冲突（`:1917`）、sysfs 写入解析失败 |
| `inkern.c` | 21 | 见下面四小类 |
| `industrialio-buffer.c` | 15 | 扫描元素一个都没开（`:838`）、`storagebits < realbits + shift`（`:1634`）、`watermark > length`（`:1353`）、模式不匹配（`:876`、`:884`） |
| `industrialio-event.c` | 6 | 事件规格解析 |
| `industrialio-trigger.c` | 3 | 触发器名/参数 |
| `industrialio-sw-device.c` / `-sw-trigger.c` | 各 1 | configfs 里创建了未注册的类型 |

`inkern.c` 那 21 处的四小类（这是消费者最常撞到的）：

1. **DT 索引越界** —— `:138`（`args[0] >= num_channels`，带打印 `invalid channel index %llu`）、`:189`（`index < 0`）。
2. **`io-channel-names` 命中了但取不到通道** —— `:236`，故意返回 `-EINVAL` 而不是 `-ENODEV`，目的就是让上层**停止**继续尝试（配合上面 A/C 组那个信号语义）。
3. **这个通道没有你要的那项 info** —— `:574`（`!iio_channel_has_info`）、`:810`（`!iio_channel_has_available`）、`:1018` 和 `:1032`（`ext_info` 为空）。**这一类是「配置不匹配」，不是「出错了」**：驱动没在 `info_mask` 里声明，消费者就取不到。
4. **驱动没实现对应回调** —— `:815`（`read_avail` 为 NULL 时的落底 return）、`:951`（`write_raw` 为 NULL 时的落底 return）。同一个 `-EINVAL`，但原因在 provider 驱动而不是在消费者。

第 3 和第 4 小类是最值得分清的一对：**都返回 `-EINVAL`，都发生在 provider 一侧，但一个是「没声明」、一个是「没实现」。** 判据是去看 provider 的 `iio_chan_spec.info_mask_*` 里有没有那一位——有位而取不到就是第 4 类（回调缺失），没位就是第 3 类。

#### 其余错误码的一句话语义

| 错误码 | 含义 | 典型位置 |
|---|---|---|
| `-ENOMEM` | 分配失败，30 处几乎全是 `kzalloc`/`kasprintf`/`bitmap_zalloc` 的直接返回 | 遍布 |
| `-EAGAIN` | 非阻塞读但暂时没数据 | 缓冲区/事件读路径 |
| `-EFAULT` | `copy_from_user` / `copy_to_user` 失败 | `industrialio-core.c:422` 等 |
| `-ERESTARTSYS` | 等待被信号打断 | `mutex_lock_interruptible` 的返回 |
| `-EPERM` | 触发器是只读的，不许改 | `industrialio-trigger.c:448` |
| `-EFBIG` | 写入的值超出字段能表示的范围 | 值解析路径 |
| `-EPROBE_DEFER` | provider 还没就绪，稍后重试 | 消费者取通道路径 |
| `-EEXIST` | 同名对象已存在 | 注册路径 |

`-EPROBE_DEFER` 只有 2 处但意义特殊：它不是错误，是「等一下再来」。dmesg 里看到消费者驱动反复 defer，去查 provider 为什么没 probe 成功，别去查消费者。

### §7.4 错误消息 → 原因映射表

九个核心文件里**始终输出**的 19 条打印（10 个 `dev_err` + 8 个 `pr_err` + 1 个 `dev_info`）全表如下。`dev_dbg` 那 6 条见 §4.4，WARN 族 3 条见 §7.2。

| 打印文本（逐字，`%` 占位符保留） | 位置 | 谁打的 | 一般原因 |
|---|---|---|---|
| `could not register bus type` | `industrialio-core.c:352` | `pr_err` | 子系统初始化失败，整个 IIO 起不来 |
| `failed to allocate char dev region` | `:358` | `pr_err` | 同上，字符设备号申请失败 |
| `%s: read failed`（`%s` = `iio_debugfs_read_reg`） | `:398` | `dev_err` 挂父设备 | debugfs 读寄存器时驱动回调失败 |
| `%s: write failed`（`%s` = `iio_debugfs_write_reg`） | `:437` | `dev_err` 挂父设备 | debugfs 写寄存器时驱动回调失败 |
| `mounting matrix not found: using identity...` | `:593` | `dev_info` | DT 里没写 `mount-matrix`，回落到单位矩阵。**这是提示不是错误**，详见 `06-devicetree.md` §3.4 |
| `tried to double register : %s` | `:1191` | `dev_err` | 同名 sysfs 属性注册两次；只在 `shared_by == IIO_SEPARATE` 时打，返回 `-EBUSY`（`:1193`） |
| `failed to get device id` | `:1694` | `pr_err` | ida 分配失败。这里**不能**用 `dev_err`——注释在 `:1693` 解释了原因（此时设备名还没定） |
| `Duplicate scan index %d` | `:1896-1898` | `dev_err` | 驱动的 `iio_chan_spec` 里两个通道用了同一个 `scan_index`，返回 `-EINVAL`（`:1899`） |
| `Cannot use labels and extend_name at the same time` | `:1915-1916` | `dev_err` | 驱动同时用了 `indio_dev->label` 和通道的 `extend_name`，返回 `-EINVAL`（`:1917`） |
| `Failed to create buffer sysfs interfaces` | `:1959-1960` | `dev_err` 挂父设备 | 缓冲区 sysfs 建不起来，注册中止 |
| `Failed to register sysfs interfaces` | `:1966-1967` | `dev_err` 挂父设备 | 通道属性建不起来，注册中止 |
| `Failed to register event set` | `:1972-1973` | `dev_err` 挂父设备 | 事件 sysfs 建不起来，注册中止 |
| `Channel %d storagebits (%d) < shifted realbits (%d + %d)` | `industrialio-buffer.c:1629-1633` | `dev_err` | 驱动的 `scan_type` 写错了：存储位宽装不下 `realbits + shift`，返回 `-EINVAL`（`:1634`） |
| `Duplicate trigger name '%s'` | `industrialio-trigger.c:85` | `pr_err` | 两个触发器同名 |
| `Could not find an available irq for trigger %s, CONFIG_IIO_CONSUMERS_PER_TRIGGER=%d limit might be exceeded` | `:288-289` | `pr_err` | 挂到同一触发器上的消费者超过 Kconfig 上限，见 §1.1 |
| `invalid channel index %llu` | `inkern.c:136-137` | `dev_err` | DT 的 `<&provider N>` 里 N 越界，返回 `-EINVAL`（`:138`） |
| `ERROR: could not get IIO channel %pfw:%s(%i)` | `inkern.c:227-228` | `pr_err` | `io-channel-names` 里找到了这个名字但取不到通道；`%pfw` 打的是 fwnode 路径 |
| `Invalid device type: %s` | `industrialio-sw-device.c:103` | `pr_err` | configfs 里 `mkdir` 了一个没注册的软件设备类型 |
| `Invalid trigger type: %s` | `industrialio-sw-trigger.c:107` | `pr_err` | configfs 里 `mkdir` 了一个没注册的软件触发器类型 |

重跑（会输出 19 行，与上表逐行对应）：

```bash
grep -n 'dev_err(\|dev_info(\|pr_err(' $C
```

三条判读要点：

- **五条 `dev_err(indio_dev->dev.parent, …)` 打在父设备上**（`:398`、`:437`、`:1959`、`:1966`、`:1972`），dmesg 里的前缀是 SPI/I2C 从设备名，**不是 `iio:deviceN`**。按 `iio:device` 搜会漏掉它们。
- **`:1694` 用 `pr_err` 而不是 `dev_err` 是刻意的**，注释在 `:1693`。所以这一条在 dmesg 里没有任何设备前缀，看起来像是无主日志。
- **注册期三连（`:1959` / `:1966` / `:1972`）出现任意一条，都意味着设备最终不会出现在 `/sys/bus/iio/devices/` 下。** 先看这三条，再往上追具体子原因。

---

## §8 一页速查

### §8.1 先跑这五条

```bash
# 1. 有哪些 IIO 设备，各是什么
for d in /sys/bus/iio/devices/iio:device*; do
  echo "$d  name=$(cat $d/name 2>/dev/null)  label=$(cat $d/label 2>/dev/null)"
done

# 2. 这个设备暴露了什么通道、什么属性
ls /sys/bus/iio/devices/iio:device0/

# 3. 直接读一个值（不经缓冲区，最短路径）
cat /sys/bus/iio/devices/iio:device0/in_voltage0_raw

# 4. 有哪些触发器
ls /sys/bus/iio/devices/ | grep '^trigger'
cat /sys/bus/iio/devices/trigger0/name

# 5. 内核有没有抱怨过
dmesg | grep -iE 'iio|industrialio'
```

第 5 条要配合 §7.4 的判读要点：五条注册期错误打在**父设备**上（SPI/I2C 从设备名），`grep iio` 会漏。改用 `dmesg | grep -iE 'iio|Failed to (create|register)'` 或直接按驱动名搜。

### §8.2 各观测面的入口

| 想看什么 | 去哪 | 前提 | 本篇 |
|---|---|---|---|
| 设备/通道/属性清单 | `/sys/bus/iio/devices/iio:deviceN/` | 无 | §2 |
| 单次采样值 | `in_*_raw` + `in_*_scale` + `in_*_offset` | 无 | §2.4 |
| 属性名怎么拼出来的 | `industrialio-core.c:1069-1130` 三张表 | 无 | §2.3 |
| 缓冲区状态 | `bufferN/{enable,length,watermark,data_available}` | `CONFIG_IIO_BUFFER` | §2.2 |
| 扫描元素开关 | `bufferN/in_*_en`、`_index`、`_type` | 同上 | §2.2 |
| 触发器绑定 | `trigger/current_trigger` | `CONFIG_IIO_TRIGGER` | §2.2 |
| 事件使能 | `events/in_*_thresh_*_en` | 驱动有 event_spec | §2.2 |
| 寄存器裸读写 | `<debugfs>/iio/iio:deviceN/direct_reg_access` | 三道门，见 §1.2 | §3 |
| 缓冲区启动为什么失败 | `dev_dbg`，需动态调试 | `CONFIG_DYNAMIC_DEBUG` | §4.4 |
| tracepoint | **核心没有**；只有 bno055_ser 驱动有 5 个 | `CONFIG_TRACING` | §4.1 §4.2 |
| 事件流 | `/dev/iio:deviceN` + `IIO_GET_EVENT_FD_IOCTL` | 有 event_interface | §5.3 |
| 数据流 | `/dev/iio:deviceN` 或 per-buffer fd | 有缓冲区 | §5.1 |
| 现成用户态工具 | `tools/iio/` 四个程序 | 需自行编译 | §5.4 |
| 故障注入 | **本子系统没有**；用 dummy 代替 | 见 §6 | §6 |

### §8.3 一行命令打开动态调试

```bash
# 打开缓冲区启动失败的全部 6 条 dev_dbg
echo 'file drivers/iio/industrialio-buffer.c +p' > /sys/kernel/debug/dynamic_debug/control
# 关掉
echo 'file drivers/iio/industrialio-buffer.c -p' > /sys/kernel/debug/dynamic_debug/control
```

注意 §4.4 讲过的静默陷阱：`industrialio-buffer.c:881-885` 那个 `-EINVAL` **无论怎么开动态调试都没有日志**——非触发式设备去挂触发器时，`return -EINVAL` 在 `if (indio_dev->modes & INDIO_BUFFER_TRIGGERED)` 守卫之外。`:875-876`（硬件模式下开第二个缓冲区）同样一句话都不打。写 `enable` 得到 `-EINVAL` 而 dmesg 全空，先怀疑这两处。

### §8.4 症状 → 第一步

| 症状 | 第一步 | 章节 |
|---|---|---|
| `/sys/bus/iio/devices/` 是空的 | 查 provider 驱动有没有 probe；`dmesg` 搜 `Failed to register` 三连 | §7.4 |
| 设备在但没有 `in_*` 属性 | 驱动的 `iio_chan_spec.info_mask_*` 没置位 | §2.3 |
| 读 `in_*_raw` 返回 `-EINVAL` | 分清「没声明 info」和「回调没实现」两类 | §7.3 |
| 读 `in_*_raw` 返回 `-EBUSY` | 缓冲区在跑，A 组语义 | §7.3 |
| `open("/dev/iio:device0")` 返回 `-EBUSY` | 主字符设备是独占的，已有进程持有 | §5.2 |
| 老工具 `read()` 突然返回 `-EBUSY` | 有人用新 per-buffer fd API 打开过，B 组语义 | §7.3 |
| 写 `enable` 返回 `-EINVAL`，dmesg 全空 | 上面 §8.3 的两个静默点 | §4.4 |
| 写 `current_trigger` 返回 `-EPERM` | 触发器是只读的（`trig_readonly`） | §7.2 §7.3 |
| 挂触发器时 dmesg 说 `Could not find an available irq` | `CONFIG_IIO_CONSUMERS_PER_TRIGGER` 上限 | §1.1 |
| 事件一个都不来，也没有日志 | `industrialio-event.c` 零打印，用 §6.2 的 dummy 造事件对照 | §7.1 §6.2 |
| `iio_event_monitor` 说 `does not support events` | `-ENODEV`：这个设备没有 event_interface | §5.4 |
| `iio_event_monitor` 打 `Unknown event` | 工具比内核旧（或反之），枚举对不上 | §5.4 |
| 没有 `direct_reg_access` 文件 | 三道门逐个查，最常见是驱动没填回调（只有 50/500+ 填了） | §1.2 §3.3 |
| 传感器方向不对但一切正常 | `mount-matrix` 没写；这是 `dev_info` 不是错误 | §7.4 |
| 消费者驱动反复 `-EPROBE_DEFER` | 去查 provider，不是消费者的问题 | §7.3 |
| DTS 里的通道索引报 `invalid channel index` | `<&provider N>` 的 N 越界 | §7.4 |

### §8.5 本篇给出的重跑命令清单

下面每条都在本篇正文里出现过，并且都实跑过（规则 30）。集中放在这里便于一次性复核：

```bash
C="drivers/iio/industrialio-core.c drivers/iio/industrialio-buffer.c \
drivers/iio/industrialio-trigger.c drivers/iio/industrialio-event.c \
drivers/iio/inkern.c drivers/iio/industrialio-sw-device.c \
drivers/iio/industrialio-sw-trigger.c drivers/iio/industrialio-configfs.c \
drivers/iio/industrialio-triggered-event.c"

grep -rn 'TRACE_EVENT\|DECLARE_EVENT_CLASS' drivers/iio/          # → 5，全在 bno055
grep -rn 'TP_CONDITION' drivers/iio/ | wc -l                      # → 0
grep -n 'DEBUG' drivers/iio/Kconfig                               # → 空
grep -rn 'debugfs_create' drivers/iio/*.c                         # → 只有 core 里 2 处
grep -rln '\.debugfs_reg_access' drivers/iio/ | wc -l             # → 50 个文件
grep -rn '\.debugfs_reg_access\s*=' drivers/iio/ | wc -l         # → 51 个赋值点
grep -rn 'fault_inject\|DECLARE_FAULT_ATTR\|should_fail' drivers/iio/  # → 空
grep -rnE '(dev|pr)_(err|warn|info|dbg|notice)_(once|ratelimited)' drivers/iio/ | wc -l   # → 8（含 1 个假阳性）
grep -n 'dev_err(\|dev_info(\|pr_err(' $C                         # → 19 行，对应 §7.4
grep -c 'dev_dbg(' $C                                             # → 只有 buffer 是 6
grep -rn 'KUNIT_CASE' drivers/iio/test/                           # → 6 + 2
wc -l tools/iio/*.c tools/iio/*.h                                 # → 188/444/782/988/79
```

`grep -c` 的原生输出是 `文件名:数字`，看起来像行号引用——上表故意写成注释里的 `→ 数字` 形式，原因见 `99-evidence.md` 里的自指陷阱一节。

---

**下一篇**：`08-feynman.md` —— 用大白话解释 IIO 里最难理解的几组概念（通道 vs 扫描元素、触发器为什么是独立设备、`_raw` 到物理量的三段换算、缓冲区的两套 fd）。
