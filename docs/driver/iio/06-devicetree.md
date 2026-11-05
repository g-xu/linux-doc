---
title: 设备树
---

# iio 设备树：属性、消费者与故障定位

## §0 读前必读

### §0.1 发现路径分诊表

**别先问"IIO 有没有设备树绑定"。** IIO 里同时存在五条互不相同的发现路径，设备树只覆盖其中两条，而且覆盖得不对称——提供方几乎全走 DT，触发器几乎全不走。

| 发现路径 | 谁扫描 | 走 DT | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|
| **提供方设备**（ADC / 传感器） | platform / i2c / spi 总线的 OF 匹配 | ✅ | ✅ 主路径 | §2（`compatible` 之外的部分交给各驱动绑定文档） |
| **内核内消费者绑定** | IIO 核心的 `inkern.c` | ✅ | ✅ 主路径 | §1、§3、§4 全覆盖 |
| **板文件映射表**（`iio_map`） | IIO 核心的 `iio_map_list` | ❌ | ❌（本树 arm64 无用户） | §5.1 说明它的存在和为什么不用 |
| **触发器** | 各触发器自己 | 多数 ❌ | ✅ 但不经 DT | §5.2 逐个交代 |
| **configfs 软件设备/触发器** | 用户运行时创建 | ❌ | 需手工挂载 configfs | §5.3 |

**两条最关键的读法：**

- 第 1 行和第 2 行是**两件独立的事**。DT 里写了 ADC 节点，ADC 就会 probe 出来、sysfs 里就有设备；但要让另一个内核驱动读它，必须**另外**写第 2 行那套 `io-channels` 属性。很多"ADC 起来了但 thermal 读不到"的问题根源在这里。
- 第 4 行是最容易踩空的：`/sys/bus/iio/devices/` 下的 `trigger0` **不是 DT 造出来的**。DT 里找不到任何跟 sysfs 触发器、hrtimer 触发器有关的节点，因为它们由用户态在运行时创建（§5.2）。

### §0.2 按任务找章节

| 我要做的事 | 去 |
|---|---|
| 让我的驱动能读某个 ADC 通道 | §1.2、§3 |
| 我在写 ADC 提供方，DT 侧要准备什么 | §2 |
| 一个通道号在 DT 里写 `<&adc 3>`，这个 3 是什么 | §2.2 |
| 我的通道名字对不上（`io-channel-names`） | §3.2 |
| 加速度计的安装方向怎么在 DT 里描述 | §3.4 |
| 给设备起一个稳定的名字（不想依赖 `iio:device0`） | §3.5 |
| 触发器要在 DT 里写什么 | §5.2（**答案通常是"什么都不写"**） |
| 配错了怎么定位 | §6 |
| 看到某个具体错误消息想知道原因 | §6.4 |

### §0.3 一句话结论：核心只读六个属性

IIO 核心（不含任何具体驱动）在整棵树上只消费 **6 个 DT 属性**。这个数字很小是因为 IIO 把绝大部分配置留给了各驱动自己的绑定。重跑：

```bash
grep -rnE -A1 'of_property|fwnode_property|device_property' --include=*.c \
     drivers/iio/*.c | grep -oE '"[a-z#][a-z0-9#,-]*"' | sort -u
```

**`-A1` 不能省。** `#io-channel-cells` 是 `fwnode_property_get_reference_args()` 的第二个实参，因为换行落在了下一行上（`drivers/iio/inkern.c:152-153`、`:298-299`），不带 `-A1` 就只输出 5 个，漏掉的正好是提供方那一侧最重要的那个。这类"属性名和读它的函数不在同一行"的漏统计，是本篇所有普查命令的共同风险。

六个属性、消费者、行号全在 §1.1。

---

## §1 核心真正会读的属性

### §1.1 六个属性总表

| 属性 | 写在哪个节点 | 谁消费 | 消费代码 | 没写会怎样 |
|---|---|---|---|---|
| `io-channels` | **消费者**节点 | IIO 核心 | `drivers/iio/inkern.c:152`（按 index 取一个）、`:298`（数一共几个） | 消费者拿不到通道，`devm_iio_channel_get` 返回 `-ENODEV` |
| `#io-channel-cells` | **提供方**节点 | IIO 核心 | `drivers/iio/inkern.c:153`、`:299`（作为 cells 名传给 `fwnode_property_get_reference_args`） | phandle 解析失败，消费者侧报错 |
| `io-channel-names` | **消费者**节点 | IIO 核心 | `drivers/iio/inkern.c:219` | 按名字取通道失败；按下标取仍然可以 |
| `io-channel-ranges` | 消费者的**父**节点 | IIO 核心 | `drivers/iio/inkern.c:274` | 子节点不能继承父节点的通道（§3.3） |
| `mount-matrix` | 提供方节点 | IIO 核心 | `drivers/iio/industrialio-core.c:632` | 回落到单位矩阵（`:645`）并打一条 `dev_info`（`:593`），probe 不失败 |
| `label` | 提供方节点 | IIO 核心 | `drivers/iio/industrialio-core.c:1945` | sysfs 里没有 `label` 属性文件 |

**这张表之外的一切属性都属于某个具体驱动**，包括 `vref-supply`、`clock-names`、采样率、参考电压之类。它们的权威来源是 `Documentation/devicetree/bindings/iio/` 下对应的绑定文件，不是本篇。本篇只在 §2.3 举一个 arm64 上真实使用的例子说明形状。

### §1.2 最小可用的一对节点

```dts
	/* 提供方：一个 4 通道 ADC */
	saradc: saradc@ff100000 {
		compatible = "rockchip,rk3399-saradc";
		reg = <0x0 0xff100000 0x0 0x100>;
		#io-channel-cells = <1>;	/* ← 关键：一个 cell 表示通道号 */
		vref-supply = <&vcc18_ldo>;
		status = "okay";
	};

	/* 消费者：读它的第 1 通道 */
	adc-keys {
		compatible = "adc-keys";
		io-channels = <&saradc 1>;	/* phandle + 1 个 cell */
		io-channel-names = "buttons";	/* 可选，但推荐 */
		/* ... */
	};
```

**三件事必须同时成立**，缺一个就连不上：

1. 提供方有 `#io-channel-cells`
2. 消费者的 `io-channels` 里 cell 个数和它一致
3. 提供方的驱动真的 probe 成功了（否则消费者拿到 `-EPROBE_DEFER`，见 §6.2）

### §1.3 六个属性在 arm64 DTS 里的实际用量

判断"这个属性我要不要关心"的依据是本树 arm64 有没有人用：

| 属性 | arm64 DTS 文件数 | 出现次数 | 结论 |
|---|---|---|---|
| `io-channels` | 63 | 131 | 主流 |
| `#io-channel-cells` | 49 | 99 | 主流 |
| `io-channel-names` | 43 | 46 | 常用（比 `io-channels` 少，说明有 20 个文件只按下标取） |
| `mount-matrix` | 14 | 15 | 少数传感器用 |
| `io-channel-ranges` | **0** | **0** | **arm64 上一个都没有**（§3.3） |
| `label` | — | — | 属性名太通用，无法用一条 grep 隔离出 IIO 的用法（§1.4） |

重跑（前五行）：

```bash
for p in 'io-channels' '#io-channel-cells' 'io-channel-names' 'mount-matrix' 'io-channel-ranges'; do
  printf '%-20s %s 文件 / %s 处\n' "$p" \
    "$(grep -rl "$p" arch/arm64/boot/dts/ | wc -l)" \
    "$(grep -rn "$p" arch/arm64/boot/dts/ | wc -l)"
done
```

**`io-channels` 有 131 处但 `io-channel-names` 只有 46 处**，这个差值本身是个有用的信号：多数消费者只有一个通道，直接按下标 0 取，不给名字。名字是给"一个消费者要多个通道"准备的（§3.2）。

### §1.4 `label` 的统计做不到，这是口径限制而不是遗漏

`label` 是内核里最通用的属性名之一——LED、GPIO、以太网 PHY、分区表都在用。`grep -rn 'label' arch/arm64/boot/dts/` 出来的结果里 IIO 相关的占比极小，而且**没有可靠的机械方法把它们分出来**：IIO 的 `label` 写在提供方节点上，而提供方节点的 `compatible` 有上百种。

所以本篇对 `label` 的处置是：**给出核心的消费点行号（`drivers/iio/industrialio-core.c:1945`）和语义，不给用量统计**。硬凑一个数字比不给更糟。

同类情况还有 `interrupts`（触发器可能用到，但它是所有子系统共用的属性）。

---

## §2 提供方侧：`#io-channel-cells` 和通道号的真正含义

### §2.1 提供方要做的只有一件事

```dts
	saradc: saradc@ff100000 {
		compatible = "rockchip,rk3399-saradc";
		reg = <0x0 0xff100000 0x0 0x100>;
		interrupts = <GIC_SPI 92 IRQ_TYPE_LEVEL_HIGH>;
		#io-channel-cells = <1>;
		clocks = <&cru SCLK_SARADC>, <&cru PCLK_SARADC>;
		clock-names = "saradc", "apb_pclk";
		resets = <&cru SRST_P_SARADC>;
		reset-names = "saradc-apb";
		vref-supply = <&vcc18_ldo>;
	};
```

除 `#io-channel-cells` 之外的每一行都由**别的**子系统消费：`clocks`/`clock-names` 由 clk 核心，`resets` 由 reset 核心，`vref-supply` 由 regulator 核心，`interrupts` 由 irqchip。IIO 核心一行都不看。驱动只是在 probe 里按名字取：

- `devm_clk_get(&pdev->dev, "saradc")` — `drivers/iio/adc/rockchip_saradc.c:390`
- `devm_regulator_get(&pdev->dev, "vref")` — `drivers/iio/adc/rockchip_saradc.c:395`

**这一段的实际含义**：如果你在提供方节点上遇到问题，先判断卡在哪个子系统上。"ADC probe 失败"里绝大多数是时钟名写错或 regulator 没起来，跟 IIO 没关系。

### §2.2 `<&saradc 1>` 里那个 `1` 默认是数组下标，不是通道号

这是本篇最容易错的一条。默认的解析函数：

```c
/* drivers/iio/inkern.c:129-142 */
static int __fwnode_iio_simple_xlate(struct iio_dev *indio_dev,
				     const struct fwnode_reference_args *iiospec)
{
	if (!iiospec->nargs)
		return 0;

	if (iiospec->args[0] >= indio_dev->num_channels) {
		dev_err(&indio_dev->dev, "invalid channel index %llu\n",
			iiospec->args[0]);
		return -EINVAL;
	}

	return iiospec->args[0];
}
```

它把 `args[0]` 原样当成返回值，而调用方拿这个返回值做的事是：

```c
	channel->channel = &indio_dev->channels[index];	/* drivers/iio/inkern.c:173 */
```

所以 DT 里的数字是 **`indio_dev->channels[]` 的下标**。

| 情况 | `<&provider N>` 里的 N 是 | 依据 |
|---|---|---|
| 驱动没实现 `fwnode_xlate` | `channels[]` 数组下标 | `inkern.c:141` → `:173` |
| 驱动实现了 `fwnode_xlate` | 由该驱动自己定义，通常是 `.channel` 值 | `inkern.c:166-167` |

`nargs == 0`（提供方写 `#io-channel-cells = <0>`）时直接返回 0，也就是**永远给第 0 个通道**（`inkern.c:132-133`）。单通道设备可以这么写，但消费者的 `io-channels` 里就不能带 cell。

**为什么大多数时候这两种解释看不出区别**：像 rockchip saradc 这样的驱动，通道表是按下标顺序密排的，`SARADC_CHANNEL(_index, ...)` 同时把 `.channel` 和 `.scan_index` 都设成 `_index`（`drivers/iio/adc/rockchip_saradc.c:137-152`），于是下标和通道号数值相等。**一旦通道表里有空洞、或者按硬件手册的非连续编号排列，两种解释就分叉了。**

### §2.3 `fwnode_xlate`：本树 7 个实现，全部在做同一件事

字段定义在 `include/linux/iio/iio.h:513`。本树的 7 个实现：

| 驱动 | 实现 | 挂到 `iio_info` | 做的事 |
|---|---|---|---|
| `drivers/iio/adc/qcom-spmi-adc5.c` | `:406`、`:419` | `:484`、`:489` | 遍历 `chan_props[]` 找 `.channel` 匹配 |
| `drivers/iio/adc/qcom-pm8xxx-xoadc.c` | `:697` | `:730` | 同上 |
| `drivers/iio/adc/qcom-spmi-vadc.c` | `:485` | `:500` | 同上 |
| `drivers/iio/adc/stm32-adc.c` | `:1525` | `:1580` | 遍历 `indio_dev->channels[]` 找 `.channel` 匹配 |
| `drivers/iio/adc/at91-sama5d2_adc.c` | `:779` | `:2242` | 同上 |
| `drivers/iio/adc/ingenic-adc.c` | `:722` | `:741` | 同上 |
| `drivers/iio/adc/ab8500-gpadc.c` | `:928` | `:941` | 同上 |

最短的一个（`drivers/iio/adc/stm32-adc.c:1525-1535`）就是这条语义转换的定义：

```c
static int stm32_adc_fwnode_xlate(struct iio_dev *indio_dev,
				  const struct fwnode_reference_args *iiospec)
{
	int i;

	for (i = 0; i < indio_dev->num_channels; i++)
		if (indio_dev->channels[i].channel == iiospec->args[0])
			return i;

	return -EINVAL;
}
```

**7 个实现全都是"把通道号翻译成下标"这一件事**，没有一个实现做别的（比如多 cell 编码、类型+编号联合寻址）。所以：

- 你要写的提供方如果通道表非密排，就照抄这 11 行
- 你要读的提供方如果实现了它，DT 里写的就是硬件通道号

重跑：

```bash
grep -rn 'fwnode_xlate' --include=*.c drivers/iio/ | grep -v 'info->fwnode_xlate'
```

### §2.4 kerneldoc 在这里是过期的，别照它写

`include/linux/iio/iio.h:427-431` 有一段：

```
 * @of_xlate:		function pointer to obtain channel specifier index.
 *			When #iio-cells is greater than '0', the driver could
 *			provide a custom of_xlate function that reads the
 *			*args* and returns the appropriate index in registered
 *			IIO channels array.
```

两处都不能信：

| kerneldoc 说 | 本版本实际 | 验证 |
|---|---|---|
| 有 `@of_xlate` 字段 | **`struct iio_info` 里已经没有这个字段了**，只剩 `fwnode_xlate`（`:513`） | `grep -n 'of_xlate' include/linux/iio/iio.h` 只有注释行 `:427`、`:429`、`:433`，没有声明行；`grep -rn 'of_xlate' drivers/iio/` 全树 0 处 |
| 属性名是 `#iio-cells` | 真名是 `#io-channel-cells`（`inkern.c:153`） | `grep -rn '#iio-cells' .` 全树只有 `iio.h:428` 这条注释本身 |

`:433` 的 `@fwnode_xlate` 描述里那句 "Functionally the same as @of_xlate" 指向的是一个已经不存在的字段——它是 of→fwnode 转换那次改动留下的残迹。**`#iio-cells` 这个名字在整棵树上只出现在这一行注释里，它是本篇唯一确认的"死字"级 kerneldoc 错误。**

---

## §3 消费者侧：四个属性和它们的静默失效

### §3.1 `io-channels`：唯一必须写的一条

```dts
	thermistor {
		compatible = "murata,ncp15wb473";
		io-channels = <&saradc 3>;
		pullup-uv = <1800000>;
		pullup-ohm = <47000>;
		pulldown-ohm = <0>;
	};
```

核心的两个消费点做的是两件不同的事：

| 行号 | 上下文 | 做什么 |
|---|---|---|
| `drivers/iio/inkern.c:152-154` | `__fwnode_iio_channel_get()` | 按 `index` 取**第 index 个** phandle+cell |
| `drivers/iio/inkern.c:298-300` | `fwnode_iio_channel_get_all()` | 循环递增 index，**数出一共有几个** |

第二处是 `devm_iio_channel_get_all()` 的实现基础，它的计数循环长这样（`inkern.c:297-303`）：一直调 `fwnode_property_get_reference_args`，直到返回错误为止；`nummaps == 0` 就返回 `-ENODEV`（`:305-306`）。返回的数组是 **NULL 结尾**的，`kcalloc(nummaps + 1, ...)`（`:308-311`）多分配的那一格就是哨兵——这一点和 `05-usage.md` §8.1 讲的 `iio_channel_release_all` 越界是同一件事的两端。

### §3.2 `io-channel-names`：只影响"按名字取"，且失败方式很吵

```dts
	io-channels = <&saradc 0>, <&saradc 3>;
	io-channel-names = "vbat", "temp";
```

它的整个作用就是一次字符串到下标的查表（`drivers/iio/inkern.c:218-220`）：

```c
	if (name)
		index = fwnode_property_match_string(fwnode, "io-channel-names",
						     name);
```

名字和 `io-channels` 里的**位置**对应，和 cell 里的数字无关。上面这个例子里 `"temp"` 是第 1 项，于是去取 `io-channels` 的第 1 项即 `<&saradc 3>`。

**这一条属性的失效是唯一会打印错误的**（`inkern.c:227-228`）：

```c
			pr_err("ERROR: could not get IIO channel %pfw:%s(%i)\n",
			       fwnode, name, index);
```

而且紧跟着的注释（`:229-236`）解释了为什么这里要显式返回 `-EINVAL` 而不是继续找：**名字在 `io-channel-names` 里找到了、但取通道还是失败，说明 DT 写得自相矛盾，不该再退回去做 `iio_map` 系统级查表**。也就是说：

| 症状 | 含义 |
|---|---|
| 有 `ERROR: could not get IIO channel ...(N)`，N ≥ 0 | 名字对上了，但 `io-channels` 的第 N 项本身有问题（cell 越界、phandle 指向非 IIO 设备） |
| 没有这条打印，但 `_get` 返回 `-ENODEV` | 名字**没**对上（`index < 0`），核心把它当成"这个节点没有该通道"，走 §5.1 的兜底路径 |

`:243` 那个 `-EINVAL`/`-ENOENT` 容忍分支是给 ACPI 留的（注释里写了 "ACPI case"），arm64 DT 路径上走不到。

### §3.3 `io-channel-ranges`：核心有消费者，但 arm64 上没有用户

**这一条要分成两件事说，因为它们的结论相反。**

| 问题 | 答案 | 依据 |
|---|---|---|
| 核心读它吗 | 读 | `drivers/iio/inkern.c:274` |
| 本树 arm64 有人写它吗 | **一个都没有** | `grep -rn 'io-channel-ranges' arch/arm64/boot/dts/` → 0 |

它的语义是**允许子节点继承父节点上的 `io-channels`**。逻辑在 `fwnode_iio_channel_get_by_name()` （`drivers/iio/inkern.c:257-288`）里：

```
本节点上按名字找           ── 找到 → 返回
      │ -ENODEV
      ▼
向上遍历每一层父节点（:273）
      │
      ├─ 父节点没有 io-channel-ranges → 立即 -ENODEV，不再往上（:274-277）
      └─ 有 → 在父节点上按名字找（:279）── 找到 → 返回
```

关键是 `:274-277` 那个**提前中断**：父节点上没有这条属性，整个向上查找**立刻停止**，不会跳过这一层继续往上。所以它不是"允许继承"的开关，而是"逐层放行"的门票，每一层都要贴。

**arm64 上怎么定位**：因为本树 arm64 没有任何 DTS 用它，遇到"我在子节点里写了 `io-channels` 的父节点上，子节点取不到通道"这类问题时，先确认你要的不是这条路径——它需要你**自己在父节点上加 `io-channel-ranges`**，而参考本树 arm64 DTS 找不到范例（只能参考 `Documentation/devicetree/bindings/iio/` 或其他架构）。

### §3.4 `mount-matrix`：不写会回落，而且**有一条打印**

```dts
	accelerometer@68 {
		compatible = "invensense,mpu6050";
		reg = <0x68>;
		mount-matrix = "-1", "0",  "0",
			        "0", "1",  "0",
			        "0", "0", "-1";
	};
```

九个字符串，行主序，把传感器坐标系映射到设备坐标系。核心的消费点在 `drivers/iio/industrialio-core.c:632`：

```c
	size_t len = ARRAY_SIZE(iio_mount_idmatrix.rotation);	/* :629，len == 9 */
	int err;

	err = device_property_read_string_array(dev, "mount-matrix", matrix->rotation, len);
```

注意 `len` 是从**全局单位矩阵** `iio_mount_idmatrix` 取的（`:629`），不是从入参 `matrix` 取的——两者的 `rotation[]` 长度当然一样，但读代码时容易看成后者。

**`iio_read_mount_matrix()` 有四条出口**（`drivers/iio/industrialio-core.c:632-645`），行为各不相同：

```
device_property_read_string_array(dev, "mount-matrix", ...) 的返回值 err
      │
      ├─ err == 9 ─────────→ 正常，返回 0（:633-634）
      │
      ├─ err >= 0 但 != 9 ─→ 项数写错 → 返回 -EINVAL（:636-638）
      │                       结果：驱动 probe 失败，一眼能看出来
      │
      ├─ err == -EINVAL ───→ 属性根本不存在 → 回落到单位矩阵（:644-645）
      │                       打印 dev_info「mounting matrix not found: using identity...」
      │                       结果：驱动跑得好好的，方向是错的
      │
      └─ 其他负值 ─────────→ 原样返回（:640-642），注释叫它
                              「Invalid matrix declaration format」
```

**"不写 mount-matrix 完全静默"是错的**——回落路径走的是 `iio_setup_mount_idmatrix()`（`:589-595`），它在填完单位矩阵后打了一条：

```c
	dev_info(dev, "mounting matrix not found: using identity...\n");	/* :593 */
```

`dev_info` 是 `KERN_INFO` 等级，默认 loglevel 下会进 dmesg。所以这条失效**是可见的**，只是它长得像一条正常的信息而不是警告，容易被划过去。

| 出口 | 打印 | 可见性 | probe 结果 |
|---|---|---|---|
| 9 项，正确 | 无 | 不适用（无打印） | 成功 |
| 项数不对 | 无 | 靠返回值可见，不靠打印 | **失败**，`-EINVAL` |
| 属性不存在 | `mounting matrix not found: using identity...` | `dev_info`，默认可见 | 成功，方向可能是错的 |
| 格式非法 | 无 | 靠返回值可见，不靠打印 | 失败，错误码来自 property 层 |

也就是说：**写错个数比不写更容易发现，因为它直接让 probe 失败**。不写的那条路径是设计上的正常回落——kerneldoc 在 `:622-623` 明确写了 "a default 3x3 identity matrix will be filled in"，它是预期行为，不是错误处理。

定位办法两条：dmesg 里搜 `mounting matrix not found`；或者读 sysfs 里的 `mount_matrix` 属性文件看它是不是单位矩阵。后者更可靠，因为前者可能已经被日志轮转冲掉了。

### §3.5 `label`：给设备起稳定名字

`iio:device0` 这个编号取决于 probe 顺序，用户态不能依赖它。`label` 是解决办法：

```dts
	adc@48 {
		compatible = "ti,ads1015";
		reg = <0x48>;
		label = "battery-monitor";
	};
```

它在 `__iio_device_register()` 里被读（`drivers/iio/industrialio-core.c:1926` 起），读之前有一段 fwnode 继承逻辑（`:1937-1943`）——设备自己没有 fwnode 时从父设备取，实际读取在 `:1945`：

```c
	fwnode_property_read_string(fwnode, "label", &indio_dev->label);
```

**用户态该怎么用**：

```sh
# 按 label 找设备，不要写死 iio:device0
for d in /sys/bus/iio/devices/iio:device*; do
	[ "$(cat $d/label 2>/dev/null)" = "battery-monitor" ] && echo "$d"
done
```

没写 `label` 时 sysfs 里**不会有 `label` 这个文件**（不是空文件），所以上面的 `2>/dev/null` 不能省。

### §3.6 四种"取不到通道"的分诊

```
devm_iio_channel_get() 失败
      │
      ├─ -EPROBE_DEFER ──────→ 正常。提供方还没 probe，等它。
      │                        来源：inkern.c:158-161，bus_find_device_by_fwnode()
      │                        在 iio_bus_type 上找不到那个 fwnode。
      │                        如果一直 defer 到最后 → 提供方驱动没编进去 / status = "disabled"
      │
      ├─ -EINVAL ────────────→ 两种来源，看 dmesg 区分：
      │      ├─ 有 "invalid channel index N" → cell 越界（inkern.c:136-138）
      │      └─ 有 "ERROR: could not get IIO channel" → §3.2 那个自相矛盾的情况
      │
      ├─ -ENOENT ────────────→ 节点上根本没有 io-channels 属性
      │
      └─ -ENODEV ────────────→ 名字对不上（§3.2），或父节点继承路径断了（§3.3），
                               或走到了 iio_map 兜底但表里也没有（§5.1）
```

**这张表里唯一不是配置错误的是 `-EPROBE_DEFER`**，`05-usage.md` §3.3 讲了消费者侧要怎么写才不会把它当错误报出去。

---

## §4 mux 和 rescale：DT 里的"虚拟 IIO 设备"

这两个驱动的特殊之处是：它们**既是消费者又是提供方**。DT 节点上同时有 `io-channels`（向上取）和 `#io-channel-cells`（向下供）。`04-extensions.md` 讲了它们的内部机制，本节只讲 DT 侧怎么写。

### §4.1 mux：一个 ADC 通道分时复用成多个

本树 arm64 只有一个 `io-channel-mux` 节点（`arch/arm64/boot/dts/rockchip/rk3566-anbernic-rgxx3.dtsi:79-87`），照抄它：

```dts
	adc_mux: adc-mux {
		compatible = "io-channel-mux";
		channels = "left_x", "right_x", "left_y", "right_y";
		#io-channel-cells = <1>;
		io-channels = <&saradc 3>;
		io-channel-names = "parent";
		mux-controls = <&gpio_mux>;
		settle-time-us = <100>;
	};
```

| 属性 | 谁消费 | 行号 |
|---|---|---|
| `io-channels` + `io-channel-names = "parent"` | mux 驱动，**名字必须是 `"parent"`** | `drivers/iio/multiplexer/iio-mux.c:343` |
| `channels` | mux 驱动，先数个数再读字符串数组 | `:354`（`device_property_string_array_count`）、`:362`（读） |
| `settle-time-us` | mux 驱动，可选 | `:395` |
| `mux-controls` | **mux 子系统**（`drivers/mux/`），不是 IIO | 见 `04-extensions.md` |
| `#io-channel-cells` | IIO 核心（mux 作为提供方） | `drivers/iio/inkern.c:153` |

三条硬约束：

1. **`io-channel-names` 必须写，而且必须是 `"parent"`**。`devm_iio_channel_get(dev, "parent")`（`:343`）按名字取，没有这个名字就 probe 失败并打印 `failed to get parent channel`（`:345-346`）。
2. **`channels` 里的空字符串是有意义的占位**。`:367-370` 的循环只统计非空项（`:368` 判空、`:369` 计数），空串表示"这个 mux 状态不接任何东西"。所以 `channels = "a", "", "c"` 是 3 个状态、2 个通道。
3. **一个都不剩会直接失败**：`children <= 0`（`:371`）时打印 `not even a single child`（`:372`）并返回 `-EINVAL`。

消费者引用 mux 的哪个通道，用的是 `channels` 里的**下标**（mux 自己按顺序建通道表，没有实现 `fwnode_xlate`）。

### §4.2 rescale：五个 compatible，arm64 上都没人用

```
drivers/iio/afe/iio-rescale.c:506-519
	"current-sense-amplifier"
	"current-sense-shunt"
	"voltage-divider"
	"temperature-sense-rtd"
	"temperature-transducer"
```

本树 DTS 用量：

| compatible | arm64 | arm32 |
|---|---|---|
| `current-sense-amplifier` | 0 | 0 |
| `current-sense-shunt` | 0 | 2 |
| `voltage-divider` | 0 | 4 |
| `temperature-sense-rtd` | 0 | 0 |
| `temperature-transducer` | 0 | 0 |

重跑：

```bash
for c in current-sense-amplifier current-sense-shunt voltage-divider \
         temperature-sense-rtd temperature-transducer io-channel-mux; do
  printf '%-28s arm64=%s arm32=%s\n' "$c" \
    "$(grep -rn "compatible = \"$c\"" arch/arm64/boot/dts/ | wc -l)" \
    "$(grep -rn "compatible = \"$c\"" arch/arm/boot/dts/  | wc -l)"
done
```

**和 mux 相反，rescale 节点上不需要 `io-channel-names`。** rescale 取上游通道用的是 `devm_iio_channel_get(dev, NULL)`（`drivers/iio/afe/iio-rescale.c:532`），名字传 `NULL`，走的是"按下标 0 取"那条路（回看 `drivers/iio/inkern.c:210`：`index` 初值就是 0，`if (name)` 不成立就不查表）。所以：

| | mux | rescale |
|---|---|---|
| 取上游通道的调用 | `devm_iio_channel_get(dev, "parent")`（`iio-mux.c:343`） | `devm_iio_channel_get(dev, NULL)`（`iio-rescale.c:532`） |
| DT 里要不要写 `io-channel-names` | **必须写，且必须是 `"parent"`** | 不需要 |
| 取不到时的打印 | `failed to get parent channel`（`:346`） | `failed to get source channel`（`:534`） |

这一对是"同一个子系统里两个功能相近的驱动、DT 写法不一样"的实例。别照着 mux 的样子给 rescale 加 `io-channel-names`——多写不报错，但也没用。

**arm64 上 rescale 的五个 compatible 全是 0**，所以本篇不给 rescale 的 DTS 范例——给了就是从别的架构搬，读者 diff 不到。要用的话，每种 compatible 各自读几个 `u32` 属性，位置集中在 `drivers/iio/afe/iio-rescale.c:331-460`（`sense-resistor-micro-ohms` `:331`、`shunt-resistor-micro-ohms` `:368`、`output-ohms` `:388`、`full-ohms` `:395` 等 11 处），权威定义在 `Documentation/devicetree/bindings/iio/afe/` 下。

**顺带一个统计口径的坑**：`grep -rn 'voltage-divider' arch/arm64/boot/dts/` 会命中一堆 `gw,voltage-divider-ohms`，那是 Gateworks 板上另一个驱动的属性，跟 IIO rescale 没关系。上面的重跑命令用 `compatible = "..."` 完整形式正是为了避开这个子串包含问题。

---

## §5 三条不走 DT 的路径

### §5.1 `iio_map`：板文件时代的映射表

`include/linux/iio/machine.h` 的文件头注释第 3 行写得很直白：

```
 * Industrial I/O in kernel access map definitions for board files.
```

结构体只有四个字段（`include/linux/iio/machine.h:21-26`），配套一个 `IIO_MAP` 宏（`:28-33`）：

```c
struct iio_map {
	const char *adc_channel_label;	/* 匹配提供方通道的 datasheet_name */
	const char *consumer_dev_name;	/* 消费者设备名 */
	const char *consumer_channel;	/* 消费者侧的通道名 */
	void *consumer_data;
};
```

注册入口两个：`iio_map_array_register()`（`drivers/iio/inkern.c:43`）和 devm 版 `devm_iio_map_array_register()`（`:92`）。查表在 `iio_channel_get_sys()`（`:328-380`），遍历全局链表（`:340-348`），找不到返回 `-ENODEV`（`:351`）。

**这条路径在 arm64 上用不到**，因为它要求有人在 C 代码里写死一张表——那是 DT 之前的板文件做法。但它在故障定位上有意义：**§3.6 里 `-ENODEV` 的最后一种来源就是"DT 没查到，退回来查 `iio_map` 全局表，表也是空的"**。看到 `-ENODEV` 而 DTS 看起来没问题时，要知道核心已经悄悄多试了一条路。

匹配依据是 `datasheet_name`（`machine.h:13-15` 的 kerneldoc 明说了），不是 DT 里的通道号——两条路径的寻址方式完全不同。

### §5.2 触发器：DT 里通常什么都不写

这是 §0.1 分诊表里最容易踩空的一行。`/sys/bus/iio/devices/trigger0` 从哪来？

| 触发器 | 怎么创建 | DT 里写什么 |
|---|---|---|
| sysfs 触发器 | 用户态 `echo` 到 `/sys/bus/iio/iio_sysfs_trigger/add_trigger` | 什么都不写 |
| hrtimer 触发器 | 用户态在 configfs 里 `mkdir` | 什么都不写 |
| 设备自带的数据就绪触发器 | 驱动 probe 时自己建（用 `interrupts` 那个中断） | 只写 `interrupts`，那是 irqchip 的属性 |
| stm32 lptimer / timer 触发器 | OF 匹配 | **有 compatible，见下** |

本树只有**两个**触发器驱动带 DT compatible：

| 文件 | compatible | 行号 |
|---|---|---|
| `drivers/iio/trigger/stm32-lptimer-trigger.c` | `"st,stm32-lptimer-trigger"` | `:100-101` |
| `drivers/iio/trigger/stm32-timer-trigger.c` | `"st,stm32-timer-trigger"`、`"st,stm32h7-timer-trigger"` | `:893-898` |

重跑：

```bash
grep -rn 'of_device_id\|compatible' drivers/iio/trigger/*.c
```

**stm32 是 arm32 平台**，所以 arm64 上"触发器有 DT 节点"这件事一次都不会遇到。定位上的含义：**在 DTS 里翻找触发器配置是纯粹的浪费时间**，去 §5.3 和 `07-debug.md` 看运行时怎么建。

### §5.3 configfs：软件设备和软件触发器

hrtimer 触发器和 loop 软件设备走 configfs，要先挂载：

```sh
mount -t configfs none /sys/kernel/config
mkdir /sys/kernel/config/iio/triggers/hrtimer/my_trig
echo 100 > /sys/bus/iio/devices/trigger0/sampling_frequency
```

`mkdir` 那一刻触发器才存在。相关内核配置和两级宏（`module_iio_sw_trigger_driver`）见 `05-usage.md` §8.4——那里同时解释了为什么 grep 找不到这些注册函数的调用者。

**configfs 没挂载时 `mkdir` 会失败，且失败信息跟 IIO 无关**（`No such file or directory`，因为 `/sys/kernel/config/iio/` 整个路径不存在）。这是最常见的"hrtimer 触发器建不出来"原因，不是内核缺功能。

---

## §6 配错了怎么定位

### §6.0 先接受一件事：IIO 核心的 DT 路径几乎不说话

```bash
grep -n 'dev_err\|pr_err\|dev_warn\|pr_warn' drivers/iio/inkern.c
```

整个 `drivers/iio/inkern.c`（本子系统所有 DT 消费逻辑所在的文件）**只有两条打印**：

- `:136` `"invalid channel index %llu"`
- `:227` `"ERROR: could not get IIO channel %pfw:%s(%i)"`

其余所有失败都只是返回一个错误码，**打不打印取决于你自己的驱动有没有 `dev_err_probe`**。这决定了定位策略：**不要指望 dmesg 里有 IIO 的抱怨**，多数时候你要主动去看 sysfs 和 `/proc/device-tree`。

### §6.1 三层排查顺序

| 层 | 查什么 | 手段 | 什么时候查 |
|---|---|---|---|
| **编译期** | 驱动/子框架有没有编进去 | `zcat /proc/config.gz`、`.config` | 设备**完全**不出现 |
| **运行期静态** | DTB 里的属性到底长什么样 | `/proc/device-tree/`、`dtc -I fs` | 设备出现了但连不上 |
| **运行期动态** | 通道值、方向、绑定关系 | `/sys/bus/iio/devices/` | 都连上了但数不对 |

**顺序不能颠倒**。最常见的浪费是拿着 DTS 源文件反复看——你看的是源码，跑的是 DTB，中间隔着 overlay、`status = "disabled"`、和 `/delete-property/`。

第一层要查的 Kconfig 项：

| 症状 | 要确认的 Kconfig | 位置 |
|---|---|---|
| `/sys/bus/iio/` 整个目录不存在 | `CONFIG_IIO` | `drivers/iio/Kconfig:6` |
| 有设备但没有 `buffer/` 子目录 | `CONFIG_IIO_BUFFER` | `drivers/iio/Kconfig:15` |
| 有设备但没有 `trigger/current_trigger` | `CONFIG_IIO_TRIGGER` | `drivers/iio/Kconfig:33` |
| `/sys/kernel/config/iio/` 不存在 | `CONFIG_IIO_CONFIGFS` | `drivers/iio/Kconfig:25` |
| mux 节点在 DT 里但没设备 | `CONFIG_IIO_MUX` | `drivers/iio/multiplexer/Kconfig:9` |
| rescale 节点在 DT 里但没设备 | `CONFIG_IIO_RESCALE` | `drivers/iio/afe/Kconfig:9` |

第二层的固定命令：

```sh
# 提供方到底有没有 #io-channel-cells，值是多少
hexdump -C /proc/device-tree/soc/saradc@ff100000/\#io-channel-cells

# 消费者的 io-channels 里 phandle 指向谁
hexdump -C /proc/device-tree/thermistor/io-channels

# 反编译回 DTS 看全貌（需要 dtc）
dtc -I fs -O dts /proc/device-tree 2>/dev/null | less
```

第三层：

```sh
ls /sys/bus/iio/devices/                      # 有几个设备、几个触发器
cat /sys/bus/iio/devices/iio:device0/name     # 是不是我想的那个
cat /sys/bus/iio/devices/iio:device0/label    # DT 里的 label 进来了吗
cat /sys/bus/iio/devices/iio:device0/mount_matrix   # 是不是单位矩阵（§3.4）
```

### §6.2 症状清单，按坏掉的范围分组

**A. 总线级——整个 IIO 都不在**

| 症状 | 原因 | 确认 |
|---|---|---|
| `/sys/bus/iio/` 不存在 | `CONFIG_IIO=n` | 第一层 |
| 目录存在但一个设备都没有 | 所有提供方驱动都没编进去，或全部 `status = "disabled"` | 第二层看 `status` |

**B. 单设备级——某个提供方不出现**

| 症状 | 原因 | 确认 |
|---|---|---|
| DT 里有节点，`/sys/bus/iio/devices/` 里没有 | 驱动没编进去 / compatible 拼错 / probe 失败 | 看 dmesg 里该驱动名的输出；`ls /sys/bus/platform/devices/` 看设备建出来了没 |
| probe 报时钟或 regulator 错误 | **不是 IIO 的问题**（§2.1） | 错误消息里的子系统名 |
| 设备出现了但一个 `in_*` 属性都没有 | 通道表为空 / `info_mask_*` 全 0 | 见 `05-usage.md` §1.4 |

**C. 参数级——设备在，但某个属性不对**

| 症状 | 原因 | 确认 |
|---|---|---|
| 没有 `label` 文件 | DT 里没写 `label`（§3.5） | 第二层 |
| `mount_matrix` 是单位矩阵 | DT 里没写 `mount-matrix`，回落且只打一条 `dev_info`（§3.4） | 第二层 |
| 读数的量级差好几个数量级 | 驱动自己的属性（`vref-supply`、增益、分压比）写错 | 各驱动绑定文档 |

**D. 拓扑级——设备都在，但连不起来**

这是本篇的主场，也是唯一会真的用到 §3.6 那棵树的一组。

| 症状 | 原因 | 确认 |
|---|---|---|
| 消费者一直 `-EPROBE_DEFER`，启动到最后还在 defer | 提供方没 probe 成功（回 B 组） | 在 dmesg 里搜 `defer` |
| 消费者报 `-ENOENT` | 消费者节点上没有 `io-channels` | 第二层 |
| 消费者报 `-EINVAL`，dmesg 有 `invalid channel index N` | cell 越界，或者你以为写的是通道号但驱动按下标解析（§2.2） | 数一下提供方的 `channels[]` 有几项 |
| 消费者报 `-EINVAL`，dmesg 有 `ERROR: could not get IIO channel` | `io-channel-names` 对上了但那一项本身坏（§3.2） | 括号里的 N 是第几项 |
| 消费者报 `-ENODEV`，dmesg 什么都没有 | 名字对不上；或父节点继承路径断（§3.3）；或退到 `iio_map` 也没有（§5.1） | 第二层核对 `io-channel-names` 的拼写和顺序 |
| **读到的值是别的通道的** | cell 的下标/通道号语义搞混了，而两者都在合法范围内（§2.2） | **不会报任何错**，只能对比 sysfs 里各通道的 `in_voltageN_raw` |

最后那一行是本篇最难查的一种：**两种解释都落在合法范围内，所以没有任何错误，只是读错了通道**。定位办法是绕开消费者，直接从 sysfs 逐通道读一遍，找出数值符合预期的是哪个下标，再回去改 DT。

### §6.3 静默失效决策树

三处会"不报错但结果不对"的地方汇总成一棵树：

```
现象：设备起来了，但行为不对
   │
   ├─ 读数方向/符号反了（加速度计、陀螺仪、磁力计）
   │     └─ cat mount_matrix
   │           ├─ 是单位矩阵 → DT 漏了 mount-matrix（§3.4）
   │           │              dmesg 里应能搜到 "mounting matrix not found"
   │           └─ 不是单位矩阵 → 矩阵内容写错，或驱动没调 iio_read_mount_matrix
   │
   ├─ 读到的是另一个通道的值
   │     └─ §2.2：DT 里的 cell 是下标还是通道号？
   │           ├─ 提供方驱动有 fwnode_xlate（查 §2.3 那张表）→ 是通道号
   │           └─ 没有 → 是数组下标，从 0 数起
   │
   └─ 用户态脚本偶尔找错设备
         └─ 有没有依赖 iio:deviceN 编号？→ 改用 label（§3.5）
```

三条的共同点：**核心的行为都是"有意的正常回落"，不是漏了错误处理**。`mount-matrix` 的回落有 kerneldoc 背书（`drivers/iio/industrialio-core.c:622-623`），下标解释是默认语义（`inkern.c:141`），设备编号本来就不保证稳定。所以不要指望以后的内核版本会给这些加警告。

三条里只有第一条留了痕迹（`:593` 那条 `dev_info`），另外两条真的什么都不打印。

### §6.4 错误消息 → 原因映射表

本表的范围是 **DT 属性消费路径上的打印**：`drivers/iio/inkern.c` 全文（2 条）、`iio_read_mount_matrix()` 的回落分支（1 条）、以及 mux / rescale 两个子框架的全部 `dev_err`（19 条），合计 **22 条**。逐条列全，并标出哪些是 DT 配置问题、哪些不是——只看消息本身分不出来：

**这不是"IIO 核心的全部打印"**。`drivers/iio/industrialio-core.c` 另有 9 条 `dev_err` + 3 条 `pr_err`，绝大多数与 DT 无关（注册流程、sysfs 建立、重名属性），归 `07-debug.md` 管。

| 打印内容 | 出处 | DT 配置问题？ | 含义 | 去哪节 |
|---|---|---|---|---|
| `invalid channel index %llu` | `drivers/iio/inkern.c:136-137` | 是 | `io-channels` 的 cell 值 ≥ 提供方 `num_channels` | §2.2 |
| `ERROR: could not get IIO channel %pfw:%s(%i)` | `drivers/iio/inkern.c:227-228` | 是 | 名字在 `io-channel-names` 里找到了（括号里是下标），但取那一项失败 | §3.2 |
| `mounting matrix not found: using identity...` | `drivers/iio/industrialio-core.c:593` | 是 | DT 里没写 `mount-matrix`。**唯一一条 `dev_info` 等级**，而且**不是错误**——回落成功，probe 继续 | §3.4 |
| `failed to get parent channel` | `drivers/iio/multiplexer/iio-mux.c:346` | 是 | mux 节点缺 `io-channel-names = "parent"`，或父通道自身取不到 | §4.1 |
| `not even a single child` | `drivers/iio/multiplexer/iio-mux.c:372` | 是 | mux 的 `channels` 里全是空串，或该属性为空 | §4.1 |
| `too many channels` | `drivers/iio/multiplexer/iio-mux.c:272` | 是 | `channels` 的项数超过 `mux-controls` 那个控制器能表示的状态数（`:271` 用 `mux_control_states()` 判） | §4.1 |
| `failed to get control-mux` | `drivers/iio/multiplexer/iio-mux.c:421` | 是 | `mux-controls` 属性缺失或指向不存在的控制器。**`-EPROBE_DEFER` 时故意不打印**（`:420`） | §4.1 |
| `failed to get source channel` | `drivers/iio/afe/iio-rescale.c:534` | 是 | rescale 节点缺 `io-channels`，或上游还没 probe | §4.2 |
| `source channel is not supported` | `drivers/iio/afe/iio-rescale.c:300` | 是 | rescale 的上游通道类型不在支持列表里——**DT 连对了但连错了对象** | §4.2 |
| `failed to read output-ohms: %d` | `drivers/iio/afe/iio-rescale.c:391` | 是 | `voltage-divider` 缺 `output-ohms` | §4.2 |
| `failed to read full-ohms: %d` | `drivers/iio/afe/iio-rescale.c:398` | 是 | `voltage-divider` 缺 `full-ohms` | §4.2 |
| `failed to read the sense resistance: %d` | `drivers/iio/afe/iio-rescale.c:334` | 是 | `current-sense-amplifier` 缺 `sense-resistor-micro-ohms` | §4.2 |
| `failed to read the shunt resistance: %d` | `drivers/iio/afe/iio-rescale.c:371` | 是 | `current-sense-shunt` 缺 `shunt-resistor-micro-ohms` | §4.2 |
| `failed to read excitation-current-microamp: %d` | `drivers/iio/afe/iio-rescale.c:422` | 是 | `temperature-sense-rtd` 缺该属性 | §4.2 |
| `failed to read alpha-ppm-per-celsius: %d` | `drivers/iio/afe/iio-rescale.c:429`、`:462` | 是 | **同一条消息两个出处**：`:429` 属 RTD 路径，`:462` 属 transducer 路径 | §4.2 |
| `failed to read r-naught-ohms: %d` | `drivers/iio/afe/iio-rescale.c:436` | 是 | `temperature-sense-rtd` 缺该属性 | §4.2 |
| `invalid scaling factor.` | `drivers/iio/afe/iio-rescale.c:561` | 是（间接） | 算出来的分子或分母为 0（`:560` 判），根源是 DT 里的电阻值写成 0 | §4.2 |
| `failed to get parent channel type` | `drivers/iio/multiplexer/iio-mux.c:259` | **否** | 父通道取到了但读不出类型，运行期问题 | `04-extensions.md` |
| `failed to get ext_info '%s'` | `drivers/iio/multiplexer/iio-mux.c:303` | **否** | 缓存父通道的 ext_info 时读失败 | `04-extensions.md` |
| `too large ext_info '%s'` | `drivers/iio/multiplexer/iio-mux.c:308` | **否** | ext_info 内容超过一页（`:307` 判 `ret >= PAGE_SIZE`） | `04-extensions.md` |
| `failed to register iio device` | `drivers/iio/multiplexer/iio-mux.c:437` | **否** | `devm_iio_device_register` 失败 | `03-internals.md` |

重跑（数字对账：2 + 1 + 19 = 22）：

```bash
grep -n 'dev_err\|pr_err' drivers/iio/inkern.c                      # → 2 行
grep -n 'mounting matrix not found' drivers/iio/industrialio-core.c  # → 1 行
grep -n 'dev_err' drivers/iio/multiplexer/iio-mux.c drivers/iio/afe/iio-rescale.c   # → 19 行
```

三条口径说明，否则数字对不上：

- **第二条命令的 19 行里 `dev_err_probe` 占 2 行**（`iio-mux.c:345`、`iio-rescale.c:534`），因为 `grep 'dev_err'` 是子串匹配。表里 mux 那条按消息实际所在行记成 `:346`（消息是续行），rescale 那条记成 `:534`。这就是规则里说的"子串包含会把两类算成一类"。
- **`failed to read alpha-ppm-per-celsius` 逐字重复两次**（`:429`、`:462`），所以 21 条打印只对应 20 条不同的消息文本。看到这条消息不能唯一确定是哪条路径，得看上下文里还有没有 `r-naught-ohms` 相关的报错。

- **表里有一条不是 `dev_err`**（`industrialio-core.c:593` 是 `dev_info`），所以它不出现在第三条命令里，得单独 grep。这条恰好是最容易被忽略的一条——见 §3.4。

表外的任何错误消息都来自你自己的驱动、提供方驱动，或别的子系统（clk / regulator / mux / irqchip），DT 无关的核心打印见 `07-debug.md`。

### §6.5 篇末速查

| 一句话 | 依据 |
|---|---|
| 核心只读 6 个属性 | §1.1 |
| DT 里的 cell 默认是数组下标 | `inkern.c:141` → `:173` |
| 7 个驱动实现 `fwnode_xlate` 把它改成通道号 | §2.3 |
| `io-channel-ranges` 核心读但 arm64 无人写 | §3.3 |
| `mount-matrix` 缺失回落到单位矩阵，并打一条 `dev_info` | `industrialio-core.c:645` → `:593` |
| 触发器在 arm64 上不走 DT | §5.2 |
| `iio_map` 只给板文件，arm64 用不到 | §5.1 |
| `inkern.c` 全文只有 2 条打印；DT 路径上共 22 条 | §6.0、§6.4 |
| kerneldoc 里的 `@of_xlate` 和 `#iio-cells` 都不存在 | §2.4 |

本篇 §6.1 第三层只列了跟 DT 配置直接相关的那几个 sysfs 文件。

运行期的完整观测手段（tracepoint、debugfs、`direct_reg_access`）在下一篇 `07-debug.md`。
