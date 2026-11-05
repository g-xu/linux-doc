---
title: 设备树
---

# reset 06 —— 设备树怎么写、谁在读、配错了怎么定位

arm64-only。所有实例节点都取自本树 `arch/arm64/boot/dts/`，可以直接 `git grep` 对照。

## §0 按任务找章节

| 你要做的事 | 去 |
|---|---|
| 我在写**控制器**节点，`#reset-cells` 该填几 | §2 |
| 我在写**消费者**节点，`resets` / `reset-names` 怎么排 | §3 |
| 我的控制器 cell 不止一个数字（offset + bit 之类） | §4 |
| 想看一个从控制器到消费者的完整实例 | §5 |
| 配了但驱动拿不到 / 拿到了但没生效 | §6（陷阱决策树）+ §8（定位） |
| 同一个 phandle 又出现在 `clocks` 又出现在 `resets`，这是不是写错了 | §7 |
| 内核报了一句话，想知道对应哪种配错 | §9 |

不在本篇：API 清单（`01-api.md`）、解析代码逐行（`03-internals.md` §A2）、
驱动骨架（`05-usage.md`）、tracepoint / debugfs（`07-debug.md`）。

---

## §1 核心真正会读的属性 —— 只有三个

这是整个 reset 框架 DT 契约的全部内容。**没有第四个属性**：
`drivers/reset/core.c` 里出现的 DT 属性名字符串一共就这三个。

| 属性 | 写在哪个节点 | 谁消费 | 消费代码 | 必选？ |
|---|---|---|---|---|
| `#reset-cells` | **控制器**（provider） | 通用 OF 层，被核心间接触发 | `drivers/reset/core.c:838-839` 传给 `of_parse_phandle_with_args()`；`:1154` 传给 `of_count_phandle_with_args()` | 控制器必选 |
| `resets` | **消费者**（consumer） | reset 核心 | `drivers/reset/core.c:838` | 消费者必选 |
| `reset-names` | **消费者** | reset 核心 | `drivers/reset/core.c:830-831` | 可选（见 §3.2） |

三个属性之外，控制器节点上还常见 `hisi,rst-syscon`、`ti,reset-bits`、
`rockchip,grf` 这类**厂商私有属性**——那些是**具体控制器驱动**自己读的，
和 reset 核心无关。分工线画在这里：

```
   通用 DT / OF 层        →  #reset-cells 的语法解析、phandle 求值
   reset 核心 (core.c)    →  resets / reset-names 的语义：第几项、叫什么名
   具体控制器驱动         →  cell 里那几个数字到底是什么意思（§4）
                             + 所有厂商私有属性
```

### 1.1 官方绑定文档在哪

| 文件 | 行数 | 内容 |
|---|---|---|
| `Documentation/devicetree/bindings/reset/reset.txt` | 75 | **通用绑定**，三个属性的权威定义 |
| `Documentation/devicetree/bindings/reset/` | 47 个文件 | 每个控制器一份，定义 cell 语义 |
| `Documentation/driver-api/reset.rst` | 221 | 面向驱动作者，不讲 DT |

⚠️ 通用绑定还是 `.txt`（没有 YAML schema），意味着
**`make dtbs_check` 不会校验 `resets` 的项数和 `reset-names` 的项数是否一致**。
这条是 §6 全部陷阱的根源。

---

## §2 控制器侧：`#reset-cells`

### 2.1 填几

官方定义（`Documentation/devicetree/bindings/reset/reset.txt:35-37`）：

> `#reset-cells`: Number of cells in a reset specifier; Typically 0 for nodes
> with a single reset output and 1 for nodes with multiple reset outputs.

本树 arm64 DTS 的实际分布（`grep -rhoE '#reset-cells = <[0-9]+>' arch/arm64/boot/dts/`）：

| 取值 | 出现次数 | 含义 | 实例 |
|---|---:|---|---|
| `<1>` | 212 | 一个数字选一条线，最常见 | `arch/arm64/boot/dts/amlogic/meson-g12-common.dtsi:2171` |
| `<0>` | 127 | 这个节点只有一条复位输出，phandle 本身就够了 | `arch/arm64/boot/dts/apple/t8103-pmgr.dtsi:14` |
| `<2>` | 14 | 两个数字（典型：寄存器偏移 + 位号） | `arch/arm64/boot/dts/hisilicon/hi3660.dtsi:347` |

`<0>` 那 127 处**全部在一个文件里**（`arch/arm64/boot/dts/apple/t8103-pmgr.dtsi`）：
Apple M1 的每个电源域各是一个独立节点，节点本身就是"那一条线"。
消费者侧因此写成不带数字的形式：

```dts
	resets = <&ps_ans2>;			/* arch/arm64/boot/dts/apple/t8103.dtsi:412 */
```

这也是 `Documentation/devicetree/bindings/reset/reset.txt:50-52` 明确交代的
「`#reset-cells` 为 0 时 pair 里只出现 phandle」。

### 2.2 控制器驱动侧必须和它对上

`#reset-cells` 是 DTS 里的数字，`rcdev->of_reset_n_cells` 是驱动里的数字，
**核心逐项比对，不一致就 `WARN_ON` + `-EINVAL`**：

```c
	if (WARN_ON(args.args_count != rcdev->of_reset_n_cells)) {
		rstc = ERR_PTR(-EINVAL);
		goto out;
	}
```

（`drivers/reset/core.c:859-862`）

`of_reset_n_cells` 的默认值是 1（核心在注册时补的，见 `03-internals.md` §A1），
所以：

| DTS 写 | 驱动要做 | 不做的后果 |
|---|---|---|
| `#reset-cells = <1>` | 什么都不用做 | —— |
| `#reset-cells = <0>` | 驱动必须显式设 `of_reset_n_cells = 0` **并**提供 `of_xlate` | 每次 get 都 WARN + `-EINVAL` |
| `#reset-cells = <2>` | 驱动必须设 `of_reset_n_cells = 2` **并**提供 `of_xlate` | 同上 |

为什么"非 1 就必须自己写 `of_xlate`"：默认的 xlate 只看
`reset_spec->args[0]`（`drivers/reset/core.c:90` 一带），
它对 0 个 cell 无从取值、对 2 个 cell 会把第二个数字扔掉。
in-tree 正例见 `drivers/reset/hisilicon/reset-hi3660.c:99-100`
（两个字段紧挨着一起填），反面教材同一节点讨论见 `04-extensions.md` §2.3。

---

## §3 消费者侧：`resets` 和 `reset-names`

### 3.1 最小形态

```dts
	device@ff000000 {
		compatible = "vendor,widget";
		reg = <0x0 0xff000000 0x0 0x1000>;
		resets = <&cru SRST_WIDGET>;
	};
```

驱动侧对应 `devm_reset_control_get_exclusive(dev, NULL)` —— **`id` 传 `NULL`**。
只有一条线、又不打算起名字时，`reset-names` 可以整个省掉。

in-tree 实例：`arch/arm64/boot/dts/rockchip/rk3399.dtsi:282`
（`resets = <&cru SRST_A_GMAC>;`，GMAC 节点，无 `reset-names`）。

### 3.2 多条线：`reset-names` 变成必须的

```dts
			resets = <&reset RESET_PCIE_CTRL_A>,
				 <&reset RESET_PCIE_APB>;
			reset-names = "port",
				      "apb";
```

（`arch/arm64/boot/dts/amlogic/meson-g12-common.dtsi:158-161`）

核心的匹配就是一次字符串查表，拿到的是**下标**：

```c
	if (id) {
		index = of_property_match_string(node,
						 "reset-names", id);
		if (index == -EILSEQ)
			return ERR_PTR(index);
		if (index < 0)
			return optional ? NULL : ERR_PTR(-ENOENT);
	}
```

（`drivers/reset/core.c:829-836`）

三条推论，都是踩坑现场：

1. **`reset-names` 的顺序必须和 `resets` 的顺序一一对应**，因为查出来的下标
   直接喂给 `of_parse_phandle_with_args(..., index, ...)`（`drivers/reset/core.c:838-839`）。
   两个列表长度不一致**不会有人报错**——多出来的名字查得到下标但 phandle 取不到，
   少写的名字返回 `-ENOENT`。
2. **`id` 传 `NULL` 时 `reset-names` 被完全忽略**：`if (id)` 这一层跳过，
   `index` 保持调用者传进来的值（`get_exclusive` 系列传 0）。
   所以"DTS 里写了 `reset-names` 但驱动传 NULL"= 拿到第一条线，**不报错**。
3. **名字写错的症状是 `-ENOENT`，不是 `-EPROBE_DEFER`**。这一条对分诊很关键，
   见 §9。

### 3.3 一条线喂给多个名字，是合法的

`Documentation/devicetree/bindings/reset/reset.txt:68-71` 给的官方例子：

```dts
	bus {
		resets = <&rst 10> <&rst 11> <&rst 12> <&rst 11>;
		reset-names = "i2s1", "i2s2", "dma", "mixer";
	};
```

注意 **`<&rst 11>` 出现了两次**（"i2s2" 和 "mixer"）。
DTS 层面完全合法；到了核心里，两次 get 会命中
`__reset_control_get_internal()` 的"已存在同 id 句柄"分支
（`drivers/reset/core.c:749-791`，逐行在 `03-internals.md` §A3）。
所以：**这种写法只有配 `shared` 才成立**，用 `exclusive` 拿第二次会
`WARN_ON` + `-EBUSY`（`05-usage.md` §2.4 第一行）。

⚠️ 这是本篇最隐蔽的一处 DT/驱动耦合：**DTS 里两项指向同一条线，
就限定了驱动只能用 shared 变体**，而绑定文档不会提醒你。

### 3.4 属性放在哪个节点上，是个设计决定

`Documentation/devicetree/bindings/reset/reset.txt:20-30` 专门为此写了一段，
要点是：**一条复位线影响多个逻辑块时，不要在每个受影响的块里都写一遍**，
否则任何一个块 assert 都会连带复位其他块；应该写在"最适合控制它的那个节点"上
——如果整条总线的孩子都受影响，就写在总线节点上。

这解释了本树里为什么会有 `resets` 出现在 bus / soc 级节点上的情况。
从驱动角度看，这也是 `shared` 语义存在的 DT 侧动机。

---

## §4 cell 里的数字是什么意思？—— 由控制器绑定决定

核心**不解释** cell 内容，它只做两件事：数个数（§2.2）、把整个
`struct of_phandle_args` 原样交给控制器的 `of_xlate`（`drivers/reset/core.c:864`）。
所以"cell 里第一个数字是 3 表示什么"这个问题，答案永远在**控制器的绑定文档**里。

### 4.1 最常见：一个数字 = 一个宏

```dts
	resets = <&cru SRST_A_GMAC>;
```

`SRST_A_GMAC` 来自 `include/dt-bindings/clock/rk3399-cru.h` 之类的头文件。
这类控制器把"第几位"编码进宏，DTS 里读不出寄存器细节，也不需要。

### 4.2 两个数字：hi3660 的 offset + bit

```dts
		crg_rst: crg_rst_controller {
			compatible = "hisilicon,hi3660-reset";
			#reset-cells = <2>;
			hisi,rst-syscon = <&crg_ctrl>;
		};
```

（`arch/arm64/boot/dts/hisilicon/hi3660.dtsi:345-349`）

消费者侧就把寄存器坐标直接写进 DTS：

```dts
			resets = <&iomcu_rst 0x20 3>;		/* :417 */
			resets = <&crg_rst 0x78 7>;		/* :445 */
			/* offset: 0x84; bit: 12 */
			resets = <&crg_rst 0x84 12>;		/* :1050-1051 */
```

`:1050` 那行注释很说明问题：**这种绑定要求写 DTS 的人手里有寄存器手册**。
驱动侧把两个数字打包成一个 id（`drivers/reset/hisilicon/reset-hi3660.c:65-74`）。

⚠️ 这种绑定**没有任何上界校验**——offset 写成 `0x9999` 也会被原样写进 regmap
（分析见 `04-extensions.md` §2.3）。唯一的兜底是 regmap 的 `max_register`。

### 4.3 七个数字：ti-syscon 的静默截断陷阱

`Documentation/devicetree/bindings/reset/ti-syscon-reset.txt:30-47` 规定
`ti,reset-bits` **每条复位线占 7 个 cell**：

| Cell | 含义 |
|---|---|
| 1 | assert 控制寄存器相对 syscon 基址的偏移 |
| 2 | 该寄存器里的位号 |
| 3 | deassert 控制寄存器偏移 |
| 4 | 位号 |
| 5 | status 寄存器偏移 |
| 6 | 位号 |
| 7 | flags（`include/dt-bindings/reset/ti-syscon.h`） |

驱动就一行除法算出有多少条线：

```c
	nr_controls = (size / sizeof(*list)) / 7;
```

（`drivers/reset/reset-ti-syscon.c:183`）

**整数除法向下取整，而且没有余数检查。** 决策树：

```
   ti,reset-bits 的 cell 总数 % 7 == 0 ?
   ├── 是 → 正常，nr_controls = 总数 / 7
   └── 否 → ✗ 多写/少写的那几个 cell 被静默丢弃
            ├── 少写了 3 个（比如想写 3 条线只写了 18 个数）
            │   → nr_controls = 2，第 3 条线的 id 越界 → -EINVAL
            │      （这个还算好，至少有错误码）
            └── 多写了 3 个（写了 24 个数）
                → nr_controls = 3，最后 3 个数字**从来不会被读**
                   → 症状：某条线怎么按都没反应，日志全干净
```

第二种是真正难查的：`make dtbs_check` 不校验（`.txt` 绑定无 schema），
驱动不检查，核心看不见。**定位办法只有一条**：数一遍 `ti,reset-bits` 的
cell 总数，除 7 看余数。

另一个同族陷阱：flags 那一格里
`RESET_SET`/`ASSERT_CLEAR`/`DEASSERT_SET`/`STATUS_SET` 这几个宏
有一半的值字面上就是 `0 << n`，写了等于没写（`04-extensions.md` §2.1）。
所以"我明明加了 flag 却没变化"往往不是 bug，是那个 flag 本来就是默认值。

---

## §5 一个完整实例：Amlogic G12 的 PCIe

从控制器到消费者一条链，全部在 `arch/arm64/boot/dts/amlogic/meson-g12-common.dtsi`。

**控制器**（一个独立的 MMIO 复位控制器，`reset-simple` 家族）：

```dts
			reset: reset-controller@1004 {
				compatible = "amlogic,meson-axg-reset";
				reg = <0x0 0x1004 0x0 0x9c>;
				#reset-cells = <1>;
			};
```

（`:2168-2172`）

**消费者**（PCIe 控制器，两条线，起了名字）：

```dts
			clocks = <&clkc CLKID_PCIE_PHY
				  &clkc CLKID_PCIE_COMB
				  &clkc CLKID_PCIE_PLL>;
			clock-names = "general", "pclk", "port";
			resets = <&reset RESET_PCIE_CTRL_A>,
				 <&reset RESET_PCIE_APB>;
			reset-names = "port", "apb";
```

（`:152-161`，原文里 `clock-names` 和 `reset-names` 各占三行/两行）

三点值得注意：

1. **时钟和复位来自两个不同的 phandle**（`&clkc` / `&reset`）——
   这是 Amlogic 的形状。Rockchip 是另一种（§7）。
2. `reg = <... 0x9c>` 只有 156 字节 ⇒ 39 个 32 位寄存器 ⇒
   `reset-simple` 的 `nr_resets = resource_size(res) * BITS_PER_BYTE` 会算出
   1248 条线（`04-extensions.md` §1.4）。**上界是从 `reg` 长度推出来的，不是绑定里写的。**
3. `RESET_PCIE_CTRL_A` 这类宏在 `include/dt-bindings/reset/amlogic,meson-axg-reset.h`
   一类的头文件里，DTS 和驱动共用同一份定义。

---

## §6 静默失效陷阱汇总

按"症状"排，每条都给出立即可做的检查动作。

| 症状 | 可能的 DT 原因 | 怎么确认 |
|---|---|---|
| 驱动 probe 一直 defer，永不成功 | 控制器节点 `status = "disabled"`，或它的驱动没编进内核 | 看 `/sys/kernel/debug/devices_deferred`（`07-debug.md` §4） |
| `-ENOENT` | `reset-names` 里没有驱动要的那个名字（拼写/大小写） | 逐字符对比 DTS 与驱动里的字符串 |
| `-EINVAL` + 一条 `WARN_ON` 栈回溯 | `#reset-cells` 和驱动的 `of_reset_n_cells` 不一致 | `drivers/reset/core.c:859` |
| 拿到句柄了但硬件没反应 | cell 里的数字错（选中了另一条线，或越界后被静默吞掉） | §4；hi3660/ti-syscon 尤其危险 |
| **驱动拿到的不是它想要的那条线** | `resets` 和 `reset-names` 顺序错位 | §3.2 第 1 条 |
| 明明写了 `reset-names` 却总是拿到第一条 | 驱动 `id` 传的是 `NULL` | §3.2 第 2 条 |
| 第二个驱动 probe 失败 `-EBUSY` | 两个节点引用同一条线，但至少一边用了 exclusive | §3.3 |
| 某条线永远无效，日志全干净 | `ti,reset-bits` 的 cell 数不是 7 的倍数 | §4.3 |
| `_optional_` 的驱动完全跳过了复位 | `resets` 属性根本没写（或写在了别的节点上） | `of_count_phandle_with_args` 返回 0：`drivers/reset/core.c:1154` |

这张表里**只有三行会在内核日志留下痕迹**（defer、WARN_ON、`-EBUSY` 的 WARN）。
其余六行全是静默的——这就是 §1.1 那句「通用绑定没有 YAML schema」的实际代价。

---

## §7 同一个 phandle 出现在 `clocks` 和 `resets` 里

这**不是写错**，而是最常见的 SoC 形状：一个 CRU/CCU 硬件块同时是时钟提供者和复位提供者。

**提供者一侧同时声明两个 cell 数**：

```dts
	cru: clock-controller@ff760000 {
		compatible = "rockchip,rk3399-cru";
		reg = <0x0 0xff760000 0x0 0x1000>;
		clocks = <&xin24m>;
		clock-names = "xin24m";
		rockchip,grf = <&grf>;
		#clock-cells = <1>;
		#reset-cells = <1>;
```

（`arch/arm64/boot/dts/rockchip/rk3399.dtsi:1449-1456`）

**消费者一侧同一个 phandle 用两次**（GMAC 节点）：

```dts
		clocks = <&cru SCLK_MAC>, ... , <&cru PCLK_GMAC>;
		clock-names = "stmmaceth", "mac_clk_rx", ... , "pclk_mac";
		power-domains = <&power RK3399_PD_GMAC>;
		resets = <&cru SRST_A_GMAC>;
```

（`arch/arm64/boot/dts/rockchip/rk3399.dtsi:276-282`）

### 7.1 为什么这样能工作

两个框架各自维护自己的索引，**互不知道对方存在**：

```
            DTS: cru 节点（一个 of_node）
                        │
        ┌───────────────┴───────────────┐
        │                               │
   #clock-cells=<1>                #reset-cells=<1>
   clk 框架的 of_clk_provider       reset_controller_list
   （匹配 np == provider->node）    （匹配 args.np == r->of_node）
                                    drivers/reset/core.c:847-852
```

匹配的判据都是**同一个 `struct device_node *` 指针相等**，两条链表各查一次，
谁也不会看另一条。驱动侧一次 probe 里注册两回即可（`05-usage.md` §8），
in-tree 统计（哪些目录在注册 reset 控制器）见 `04-extensions.md` §6。

### 7.2 一个实际后果

因为两个框架完全独立，**时钟和复位的 `-EPROBE_DEFER` 是分别发生的**：
CRU 驱动只有一个 probe，注册时钟和注册复位在同一个函数里前后脚，
所以实践中不会出现"时钟拿到了但复位还没注册"。
但如果一个平台把时钟和复位拆成两个 DT 节点两个驱动（Amlogic 就是，§5），
那就真的是两次独立的 defer——**这是同一个逻辑功能在不同平台上的 DT 布局差异，
和 reset 框架无关**。

---

## §8 定位：三层

### 8.1 编译期能查出什么（几乎什么都查不出）

| 检查手段 | 能抓到 | 抓不到 |
|---|---|---|
| `dtc` 语法检查 | 括号、分号、未定义的 label | 一切语义问题 |
| `make dtbs_check` | 有 YAML schema 的控制器（47 个绑定里的 `.yaml` 那些） | **通用属性**——`reset.txt` 是 `.txt`，没有 schema |
| 编译内核 | `resets` 属性根本不参与编译 | —— |

**结论：`resets` 和 `reset-names` 的项数是否一致、名字是否和驱动对得上，
编译期一律查不出来。** 这不是工具没装好，是本版本的绑定形态决定的（§1.1）。

⚠️ 还有一个编译期开关会让整套东西凭空消失：`CONFIG_RESET_CONTROLLER`
（`drivers/reset/Kconfig:5-7`，`default y if ARCH_HAS_RESET_CONTROLLER`）。
关掉之后 `include/linux/reset.h` 里的一整套函数变成返回 0 / NULL 的空壳
（存根清单见 `01-api.md` §7），**所有 `resets` 属性被完全忽略且不报错**。
arm64 上 `ARCH_HAS_RESET_CONTROLLER` 通常被平台 Kconfig 选中，但这一条值得先确认。

### 8.2 运行期静态：确认内核看到的 DT 是你写的那份

```sh
# 看某个消费者节点上的 resets 属性（二进制，需要 hexdump）
hexdump -C /proc/device-tree/soc/ethernet@fe300000/resets

# 更可读：把整棵运行时 DT 反编译回 dts
dtc -I fs -O dts /proc/device-tree > /tmp/running.dts
```

三个要看的点：

1. **`resets` 的字节数 / 4 = phandle + cell 的总格数**。
   `#reset-cells = <1>` 时应该是偶数格；不是偶数就写错了。
2. **`reset-names` 是一串以 `\0` 分隔的字符串**，数一下有几个，
   和第 1 点算出的线数比对——这就是编译期不做、只能手工做的那个检查（§8.1）。
3. **控制器节点的 `status`**：`disabled` 的控制器不会有驱动 probe，
   消费者会永远 defer。

### 8.3 运行期动态：只有一条路

**`drivers/reset/` 里没有任何 debugfs、sysfs 或 tracepoint。**
（验证：`grep -rn 'debugfs\|sysfs\|DEVICE_ATTR' drivers/reset/` 无输出；
tracepoint 的统计见 `03-internals.md` §C3。）

所以运行期只能靠这四样，按性价比排：

| 手段 | 看什么 | 备注 |
|---|---|---|
| `/sys/kernel/debug/devices_deferred` | 哪个设备在 defer、defer 的原因字符串 | **最有用的一个**，直接指向 §6 第一行 |
| `dmesg` 里的 `WARN` 栈回溯 | `#reset-cells` 不匹配、shared/exclusive 冲突、未 acquire | §9 |
| `dev_err_probe()` 打的驱动自己的话 | 消费者驱动的 `-ENOENT` / `-EINVAL` | 前提是驱动作者写了（`05-usage.md` §2.1） |
| 直接读控制器寄存器（`devmem`）| 那一位到底是 0 还是 1 | 最后手段；MMIO 控制器才可行 |

一个实用技巧：`reset_control_status()` 能告诉你线的真实状态，但
**很多控制器根本没实现 `.status`**（SCMI、bpmp 都没有，见 `04-extensions.md` §3），
这时它返回 `-ENOTSUPP` 而不是"未复位"。这个坑在 `07-debug.md` §5 展开。

---

## §9 错误消息 / 错误码 → 原因映射

### 9.1 内核会打出来的话

`drivers/reset/core.c` 里能变成日志的一共三处（其余 WARN 都是不带消息的
`WARN_ON`，只会给一条栈回溯）：

| 日志文本 | 位置 | 原因 |
|---|---|---|
| `reset %s (ID: %u) is not acquired` | `drivers/reset/core.c:479`（deassert 路径）、`:549`（reset 路径） | 用了 `_released` 变体拿句柄，却没先 `reset_control_acquire()`（`05-usage.md` §5.1） |
| `%s(): reset lookup entry badly specified, skipping` | `drivers/reset/core.c:183` | 非 DT 的 lookup 表项字段不全。arm64 上不会遇到（`05-usage.md` §9） |

注意 `is not acquired` 这条**和 DT 无关**，是驱动的调用顺序问题。
放在这里是因为它是最容易被误判成"DTS 配错了"的一条。

### 9.2 错误码 → 该查哪里

| 错误码 | 最可能的 DT 原因 | 第一步 |
|---|---|---|
| `-EPROBE_DEFER` | 控制器还没注册（`status = "disabled"` / 驱动未编入 / 初始化次序） | `devices_deferred` |
| `-ENOENT` | `reset-names` 里没这个名字，**或** `resets` 属性完全不存在 | 数一遍 `reset-names`（§8.2）；注意 `of_reset_control_get_count` 把 count==0 也翻成 `-ENOENT`（`drivers/reset/core.c:1155-1156`） |
| `-EINVAL` + WARN | `#reset-cells` 与驱动的 `of_reset_n_cells` 不一致 | `drivers/reset/core.c:859` |
| `-EINVAL`（无 WARN） | 控制器的 `of_xlate` 判定 cell 内容越界（如 ti-syscon 的 id ≥ nr_controls） | §4.3 |
| `-EBUSY` + WARN | 两个消费者引用同一条线，至少一个用了 exclusive | §3.3 |
| `-ENOTSUPP` | 控制器没实现你调的那个回调（不是 DT 问题） | `04-extensions.md` §7 那张回调完整性表 |
| `-EPERM` + `is not acquired` | 调用顺序问题（不是 DT 问题） | §9.1 |
| **成功但硬件没反应** | cell 里的数字选错了线 / ti-syscon 静默截断 | §4，然后 `devmem` 验寄存器 |

分诊的第一刀是**「有没有 WARN 栈回溯」**：有 = 框架主动拦下了，按上表定位；
没有 = 要么是纯错误码返回（查 DTS 字符串和项数），要么是静默失效（§6 那六行）。

---

## §10 接下来看哪一篇

| 你接着想做的事 | 去处 |
|---|---|
| 这些属性被解析的完整代码路径 | `03-internals.md` §A2 |
| 驱动侧怎么写（消费者 / 控制器 / 双身份） | `05-usage.md` |
| `-ENOTSUPP`、`status()` 失败之类的运行期分诊 | `07-debug.md` §5 |
| 为什么 shared 和 exclusive 会和 DTS 写法耦合 | `08-feynman.md` §2 |
| 控制器驱动家族各自读哪些私有属性 | `04-extensions.md` |
