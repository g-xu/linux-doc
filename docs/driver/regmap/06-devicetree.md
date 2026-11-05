---
title: 设备树
---

# regmap 06：配置来源与设备树

## §0 发现路径分诊表

⚠️ **别先问"regmap 有没有 DT 绑定"。** regmap 是**框架型**子系统（99 §1），
**它自己不是"一类设备"，没有"regmap 节点"这种东西** ——
**一个 regmap 的绝大部分配置来自驱动写死的 `struct regmap_config`（54 个字段），DT 只能改动其中极少数几个。**

**但"regmap 完全不读 DT"也是错的**（skill 模板里的"regmap 没有 DT 绑定"这句话不成立）：
**`Documentation/devicetree/bindings/regmap/regmap.txt` 确实存在（29 行）**，
而且 `drivers/mfd/syscon.c` 这条路把 DT 变成了 regmap 的主要配置入口之一。

### §0.1 五条发现路径

| # | 路径 | 谁扫描 | 走不走 DT | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|---|
| 1 | **驱动自建 + config 写死** | **驱动自己**（`devm_regmap_init_*`） | **不走** | **主力**（pinctrl/clk/reset 全是） | **只说明它会压掉 DT**（§2.2） |
| 2 | **字节序三属性** | **核心 + 两个适配层**（§2.1，共 3 个消费点） | **走** | **用**（`big-endian` 7 个文件） | **完整**（§2） |
| 3 | **syscon** | `drivers/mfd/syscon.c` | **走**（3+2 个属性） | **重度使用**（99 个 DTS 文件有 `"syscon"`） | **完整**（§3） |
| 4 | ACPI / 通用固件节点 | **同一个 `fwnode_property_read_bool()`** | 走 ACPI 不走 DT | **本文档裁掉**（99 §2.5） | 只指出代码是共用的（§2.1） |
| 5 | **运行期改（debugfs）** | `regmap-debugfs.c` | **不走** | **调试用** | **在 07 篇** |

**一句话总结**：**DT 在 regmap 里只能配"字节序"和"访问宽度"这两类事，配不了寄存器布局。**

### §0.2 按任务找章节

| 我要… | 去 |
|---|---|
| 知道核心到底认哪几个属性 | §1 |
| 大端芯片读出来数值全错 | §2 |
| 写一个 syscon 节点 | §3 |
| **DTS 里写了 `big-endian` 但完全没反应** | **§4（静默失效决策树）** |
| 找 arm64 真实例子 | §3.3 |
| 属性配错了怎么定位 | §5 |
| 看到某条报错想知道原因 | §6 |
| 哪些属性是死字 | §7 |

---

## §1 核心真正会读的属性总表

**全表五条，一条不多**。**"写在哪个节点"这一列是本表的重点**——
**写错节点是最常见的失效原因**。

| 属性 | 写在哪个节点 | 谁消费（定位到行） | 落到 `regmap_config` 的哪个字段 |
|---|---|---|---|
| `big-endian` | **被访问的那个设备自己的节点** | `drivers/base/regmap/regmap.c:705`（核心）+ `drivers/mfd/syscon.c:69`（syscon） | `val_format_endian = REGMAP_ENDIAN_BIG` |
| `little-endian` | 同上 | `regmap.c:707` + `syscon.c:71` | `val_format_endian = REGMAP_ENDIAN_LITTLE` |
| `native-endian` | 同上 | `regmap.c:709` + `syscon.c:73` | `val_format_endian = REGMAP_ENDIAN_NATIVE` |
| `reg-io-width` | **只有 `syscon` 节点**（其他节点上是死字，§7） | `drivers/mfd/syscon.c:81` | **一次改三个**：`reg_stride` / `val_bits` / `max_register`（`syscon.c:108-110`） |
| `hwlocks` | **只有 `syscon` 节点** | `drivers/mfd/syscon.c:85` | `use_hwlock` / `hwlock_id` / `hwlock_mode`（`syscon.c:87-89`） |

⚠️ **三条必须先说清的事**：

1. **`reg-io-width` 和 `hwlocks` 不是 regmap 核心的属性，是 syscon 的属性。**
   官方绑定文档在 `Documentation/devicetree/bindings/mfd/syscon.yaml:83` 和 `:89`，
   **不在 `bindings/regmap/regmap.txt` 里**。写到普通设备节点上没有任何效果。
2. ⚠️ **字节序属性只管"数据"，不管"地址"。**
   核心里有两个平行函数：`regmap_get_reg_endian()`（`regmap.c:666`）和
   `regmap_get_val_endian()`（`regmap.c:690`），
   **只有后者读 fwnode**（`:705-710`）；**前者一个字都不读 DT**（`:672-687` 只看 config 和 bus）。
   ⇒ **寄存器地址的字节序无法通过设备树配置，只能由驱动的 `config.reg_format_endian` 定。**
3. **属性是"存在即为真"的布尔型**（`fwnode_property_read_bool`），
   **写 `big-endian;` 而不是 `big-endian = <1>;`**。**写成 `<0>` 也是真** ——
   **布尔属性没有"关掉"的写法**，要关就是删掉这一行。

---

## §2 字节序三属性

### §2.1 三个（不是一个）消费点

⚠️ **这是本篇最反直觉的一条**：**同一个 `big-endian` 属性在树里有三处独立的读取代码，走哪条路决定谁生效。**

| # | 消费点 | 什么时候被调 | API |
|---|---|---|---|
| 1 | `drivers/base/regmap/regmap.c:916` | **只在"总线同时提供 `read` 和 `write` 批量回调"时**（§2.3 分支 d） | `fwnode_property_read_bool` |
| 2 | `drivers/base/regmap/regmap-mmio.c:465` | **MMIO 路径总是调**（在适配层自己调，不依赖核心那条分支） | 同上 |
| 3 | `drivers/base/regmap/regmap-i2c.c:327` | ⚠️ **只在 `val_bits==16 && reg_bits==8 且适配器支持 SMBUS_WORD_DATA` 这一个条件下**（`regmap-i2c.c:324-327`） | 同上 |
| 4 | `drivers/mfd/syscon.c:69-74` | **syscon 节点，总是** | **`of_property_read_bool`（DT 专用版，不是 fwnode 版）** |

**消费点 1/2/3 用的都是 `fwnode_property_read_bool()`** ⇒
**同一行代码在 ACPI 平台上会去读 ACPI 的 `_DSD`**（本文档裁掉 ACPI，99 §2.5）。
**消费点 4 用 `of_property_read_bool()`，只认 DT。**

### §2.2 ⚠️ 优先级：config 压 DT，DT 压 bus，bus 压兜底

**`regmap_get_val_endian()` 是一条四级瀑布**（`drivers/base/regmap/regmap.c:690-725`）：

```
config->val_format_endian  非 DEFAULT ？ → 是：直接返回      (regmap.c:698-702)
             ↓ 否
fwnode 有 big/little/native-endian ？   → 是：直接返回      (regmap.c:705-714)
             ↓ 否
bus->val_format_endian_default 非 0 ？  → 是：直接返回      (regmap.c:717-722)
             ↓ 否
              返回 REGMAP_ENDIAN_BIG                        (regmap.c:725)
```

⚠️ **第一级是本篇第一号静默陷阱**：
**驱动在自己的 `regmap_config` 里写了 `.val_format_endian = REGMAP_ENDIAN_LITTLE`
⇒ 函数在第 702 行就返回了 ⇒ DTS 里那句 `big-endian;` 连读都不会被读到 ⇒ 没有任何警告、没有任何日志。**
**定位方法只有一条：去看那个驱动的 `regmap_config` 初始化式**（§5.1）。

**兜底值是 `REGMAP_ENDIAN_BIG`（`:725`），这和直觉相反。**
绑定文档说"Regmap defaults to little-endian on MMIO based devices"，
**这句话对，但要绕一层**：**MMIO 总线在第三级把默认值改成了小端**
（`drivers/base/regmap/regmap-mmio.c:428` 的 `.val_format_endian_default = REGMAP_ENDIAN_LITTLE`）。

⇒ **非 MMIO、config 不填、DT 不填 ⇒ 落到大端。**
**`regmap-i2c.c:327` 那条路正是这种情况**（它传 `NULL` 作为 bus，第三级直接跳过）
⇒ **I2C word-data 芯片默认按大端解释，选中 `regmap_smbus_word_swapped`**（`regmap-i2c.c:331-332`）。

### §2.3 ⚠️ 自定义 `regmap_bus` 只填最小集 ⇒ DT 字节序彻底失效

`__regmap_init()` 里有一个四路分支（`drivers/base/regmap/regmap.c:883-917`），
**只有第四路会调 `regmap_get_val_endian()`**：

| 分支 | 条件（位置） | 字节序怎么定 | DT 有效？ |
|---|---|---|---|
| a | `config->read && config->write`（`regmap.c:883`） | **硬定为 `REGMAP_ENDIAN_NATIVE`**（`:892-893`） | **无效** |
| b | `!bus`（`:894`） | **`goto skip_format_initialization`**（`:900`），格式化层整个跳过 | **无效** |
| c | ⚠️ **`!bus->read \|\| !bus->write`**（`:901`） | **同样 `goto skip`**（`:907`） | **无效** |
| d | else（`:908`） | `regmap_get_val_endian(dev, bus, config)`（`:916`） | **有效** |

⚠️ **分支 c 正是 05 §4.1 那个"最小实现"（只填 `reg_read`/`reg_write`）落进去的地方。**
⇒ **自己写了个只有两个回调的 `regmap_bus`，然后在 DTS 里加 `big-endian;` ⇒ 一点效果都没有，也不报错。**
**原因：单寄存器接口传的是 `unsigned int`，字节序转换在总线回调内部做，核心的格式化层根本没参与。**

**正确做法**：**在自己的总线回调里自己处理字节序**（`regmap-i2c.c:14-49` 的 SMBus 实现就是范例，
它靠"提供两个不同的 `regmap_bus` 实例"来区分——`regmap_smbus_word` 与 `regmap_smbus_word_swapped`，
`regmap-i2c.c:82` / `:118`）。

---

## §3 syscon：DT 真正的用武之地

**这是 arm64 上 regmap 与 DT 打交道的主战场**：
**99 个 arm64 DTS 文件里出现 `"syscon"`**，**186 个驱动文件调用 `syscon_regmap_lookup_by_phandle` 家族**。

### §3.1 它做的事：把一个 DT 节点变成一个共享 regmap

核心函数 `of_syscon_register()`（`drivers/mfd/syscon.c:45`），378 行的文件，7 个导出符号：

| 导出符号 | 位置 | 用途 |
|---|---|---|
| `syscon_regmap_lookup_by_phandle` | `drivers/mfd/syscon.c:275` | **最常用**：按 phandle 属性名查 |
| `syscon_regmap_lookup_by_phandle_args` | `:304` | 带额外 cell 参数的版本 |
| `syscon_regmap_lookup_by_phandle_optional` | `:322` | **找不到返回 NULL 而不是错误** |
| `syscon_regmap_lookup_by_compatible` | `:252` | 按 compatible 全树找 |
| `syscon_node_to_regmap` | `:236` | **要求节点带 `"syscon"` compatible**（`:231` 校验） |
| `device_node_to_regmap` | `:227` | **不要求** compatible |
| `of_syscon_register_regmap` | `:221` | 驱动自己建好 regmap 后登记进来 |

⚠️ **`syscon_node_to_regmap` 和 `device_node_to_regmap` 的区别就是那一行校验**
（`drivers/mfd/syscon.c:231` 的 `if (!of_device_is_compatible(np, "syscon")) return ERR_PTR(-EINVAL);`）
⇒ **节点漏写 `"syscon"` ⇒ 前者返回 `-EINVAL`，后者照样能用。**

### §3.2 syscon 从 DT 读五个属性

```c
/* drivers/mfd/syscon.c:39-43 —— 出发点是这个三字段的默认 config */
static const struct regmap_config syscon_regmap_config = {
	.reg_bits = 32,
	.val_bits = 32,
	.reg_stride = 4,
};
```

然后 `of_syscon_register()` 按 DT 逐项覆盖：

| DT 属性 | 代码位置 | 效果 |
|---|---|---|
| `reg`（必需） | `drivers/mfd/syscon.c:61` `of_address_to_resource` + `:64` `of_iomap` | **决定映射范围**，并在 `:110` 算出 `max_register = resource_size(&res) - reg_io_width` |
| `big/little/native-endian` | `:69-74` | **写进 `syscon_config.val_format_endian`** ⇒ **走的是 §2.2 的第一级，压过一切** |
| `reg-io-width` | `:81-83`（**缺省 4**） | **一次改三个字段**：`reg_stride`（`:108`）、`val_bits = ×8`（`:109`）、`max_register`（`:110`） |
| `hwlocks` | `:85-89` | **拿到 hwspinlock id，置 `use_hwlock` + `hwlock_mode = HWLOCK_IRQSTATE`** |
| `clocks`（可选） | `:120-130` | **`regmap_mmio_attach_clk()`**（`:128`）——**只在 `check_clk` 为真时走** |

⚠️ **`reg-io-width` 的合法值只有 4 个：`[1, 2, 4, 8]`**
（`Documentation/devicetree/bindings/mfd/syscon.yaml:87`）。
**写别的值 syscon 不校验**（`syscon.c:77-80` 的注释明说"regmap_init_mmio will return an error …
so there is no need to check them here"）⇒ **错误会晚一步在 regmap 里报出来**（§6）。

⚠️ **`hwlocks` 的错误处理有一个不对称**（`drivers/mfd/syscon.c:90-101`）：
**`-ENOENT` 被当成"没配，正常"直接忽略**（`:92-94`）；
**其他错误先 `pr_err("Failed to retrieve valid hwlock: %d\n", ret)` 再 `fallthrough` 到 `-EPROBE_DEFER` 的处理，一律 `goto err_regmap`**（`:95-99`）
⇒ **hwspinlock 驱动还没探测到会导致 syscon 整体 defer，而不是退化成无锁。**

### §3.3 arm64 真实节点（可直接 diff）

**例 1 —— 最普通的 syscon（Rockchip GRF）**：
```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:1483-1487 */
grf: syscon@ff770000 {
	compatible = "rockchip,rk3399-grf", "syscon", "simple-mfd";
	reg = <0x0 0xff770000 0x0 0x10000>;
	#address-cells = <1>;
	#size-cells = <1>;
	...
};
```
**没写 `reg-io-width` ⇒ 走缺省 4 ⇒ `val_bits = 32`、`reg_stride = 4`**。
**消费者**：`drivers/pinctrl/pinctrl-rockchip.c` 用 `syscon_regmap_lookup_by_phandle()` 拿它。

**例 2 —— 大端 syscon（NXP Layerscape）**：
```dts
/* arch/arm64/boot/dts/freescale/fsl-ls1043a.dtsi:313-317 */
scfg: scfg@1570000 {
	compatible = "fsl,ls1043a-scfg", "syscon";
	reg = <0x0 0x1570000 0x0 0x10000>;
	big-endian;			/* syscon.c:69 读到，写进 val_format_endian */
	...
};
```
**这是 arm64 上 `big-endian` 真实生效的地方**（**arm64 DTS 里共 7 个文件用 `big-endian`**）。

**例 3 —— 显式 `reg-io-width`（Realtek）**：
```dts
/* arch/arm64/boot/dts/realtek/rtd16xx.dtsi:140-146 */
crt: syscon@0 {
	compatible = "syscon", "simple-mfd";
	reg = <0x0 0x1000>;
	reg-io-width = <4>;		/* 与缺省值相同，写出来只是显式化 */
	#address-cells = <1>;
	#size-cells = <1>;
	ranges = <0x0 0x0 0x1000>;
};
```

**arm64 DTS 统计（每条可重跑）**：

| 属性 | arm64 DTS 文件数 | 重跑命令 |
|---|---|---|
| `reg-io-width` | **32** | `grep -rl 'reg-io-width' arch/arm64/boot/dts/ \| wc -l` |
| `big-endian` | **7** | `grep -rl 'big-endian' arch/arm64/boot/dts/ \| wc -l` |
| `little-endian` | **5** | `grep -rl 'little-endian' arch/arm64/boot/dts/ \| wc -l` |
| ⚠️`native-endian` | **0** | `grep -rl 'native-endian' arch/arm64/boot/dts/ \| wc -l` |
| `"syscon"` | **99** | `grep -rl '"syscon"' arch/arm64/boot/dts/ \| wc -l` |

⚠️ **`native-endian` 在 arm64 上是 0** ——它是为"CPU 和 MMIO 字节序由同一个硬件开关控制"的
平台设计的（绑定文档点名了 Broadcom MIPS）⇒ **arm64 上见到它就是抄错了**（§7）。

### §3.4 ⚠️`reg-io-width = <8>` 是"绑定合法但必然失败"的值

**绑定文档允许 `[1, 2, 4, 8]`**（`Documentation/devicetree/bindings/mfd/syscon.yaml:87`），
**但本版本的 MMIO 适配层不接受 8**：

```c
/* drivers/base/regmap/regmap-mmio.c:43-63 —— 本版本只有 8/16/32 三个 case */
static int regmap_mmio_get_min_stride(size_t val_bits)
{
	switch (val_bits) {
	case 8:  min_stride = 0; break;	/* 注释原话："The core treats 0 as 1" */
	case 16: min_stride = 2; break;
	case 32: min_stride = 4; break;
	default: return -EINVAL;		/* val_bits = 64 落在这里 */
	}
	return min_stride;
}
```

调用点在 `drivers/base/regmap/regmap-mmio.c:447-449`（`min_stride < 0` 就 `return ERR_PTR(min_stride)`）。
⇒ **`reg-io-width = <8>` ⇒ `syscon.c:109` 算出 `val_bits = 64` ⇒ `regmap_init_mmio` 返回 `-EINVAL`
⇒ syscon 打印 `regmap init failed`（`drivers/mfd/syscon.c:115`）。**

⚠️ **矛盾点**：**同一个文件的批量读写路径确实实现了 64 位**
（`regmap-mmio.c:205-213` 的 `writeq`/`swab64`、`:230-234` 的 `writesq`，都在 `#ifdef CONFIG_64BIT` 里）
⇒ **但 `ctx->val_bytes` 永远到不了 8，因为初始化就被上面那个 `default` 拦掉了。**
**这几段是本版本里到不了的代码**（99 §5 已登记为"推断非观察"）。

**arm64 DTS 的实际取值分布（可重跑）**：

| 取值 | 出现次数 | 说明 |
|---|---|---|
| `<4>` | **120** | **绝对主力**，和缺省值相同 |
| `<1>` | **14** | 字节访问设备 |
| `<2>` | **3** | |
| ⚠️`<8>` | **0** | **全树（含 `arch/`、`drivers/`、`Documentation/`）一个都没有** |

重跑：`grep -rhoE 'reg-io-width = <[0-9]+>' arch/arm64/boot/dts/ | sort | uniq -c | sort -rn`

---

## §4 ⚠️ 静默失效决策树（本篇核心）

**"DTS 里写了属性但完全没反应"是 regmap 最常见的 DT 问题，而且没有任何日志。**
**照下面这棵树逐层排除，每层都给了验证命令。**

```
DTS 里写了 big-endian / little-endian，读出来的值还是错的
│
├─ ① 属性写在哪个节点上？
│   └─ 必须写在"被 regmap 访问的那个设备自己的节点"上
│      不是父节点、不是控制器节点、不是消费者节点
│      验证：确认 devm_regmap_init_* 的第一个参数 dev 对应的就是这个节点
│      写错节点 ⇒ 静默无效
│
├─ ② 驱动的 regmap_config 里填了 val_format_endian 吗？
│   └─ 填了 ⇒ regmap.c:698-702 在第一级就返回，DT 根本不读
│      验证：grep -n 'val_format_endian' <驱动.c>
│      这是第一号静默陷阱（§2.2）
│
├─ ③ 走的是哪条总线？
│   ├─ MMIO ⇒ regmap-mmio.c:465 会读，正常
│   ├─ syscon ⇒ syscon.c:69-74 会读，正常
│   ├─ I2C ⇒ ⚠️ 只有 val_bits==16 && reg_bits==8 && 适配器支持 SMBUS_WORD_DATA
│   │        这一条路会读（regmap-i2c.c:324-327）；其他 I2C 形态完全不读
│   └─ 自定义 regmap_bus ⇒ 见 ④
│
├─ ④ 自定义 bus 填了 read / write（批量回调）吗？
│   └─ 只填了 reg_read / reg_write ⇒ 落进 regmap.c:901 的分支 c
│      ⇒ goto skip_format_initialization（:907）⇒ DT 彻底不读，也不报错
│      验证：看自己那个 struct regmap_bus 初始化式有没有 .read / .write
│      这是第二号静默陷阱（§2.3）
│
├─ ⑤ config 里填了 read / write 吗？
│   └─ 填了 ⇒ regmap.c:883 分支 a ⇒ 字节序硬定为 NATIVE（:892-893）⇒ DT 无效
│
└─ ⑥ 以上都排除了，但改的是"地址"的字节序？
    └─ ⚠️ 地址字节序无法用 DT 配（§1 第 2 条）
       regmap_get_reg_endian()（regmap.c:666-687）不读 fwnode
       只能改驱动的 config.reg_format_endian
```

**这棵树里 ②③④⑤⑥ 五层全部是"无日志"的**。
**唯一有输出的失效方式是 §6 那几条**——也就是说：**看不到报错不等于配置生效了。**

---

## §5 怎么定位（三层）

### §5.1 编译期 / 静态代码层（最有效）

**regmap 的 DT 问题九成靠读驱动源码定位，而不是靠日志。** 三条命令：

| 想确认 | 命令 |
|---|---|
| **驱动有没有在 config 里写死字节序** | `grep -n 'val_format_endian\|reg_format_endian' <驱动.c>` |
| **自定义 bus 有没有批量回调** | `grep -n 'struct regmap_bus' -A 20 <驱动.c>` 看有无 `.read`/`.write` |
| **节点到底走 syscon 还是自建** | `grep -n 'syscon_regmap_lookup\|devm_regmap_init' <驱动.c>` |

### §5.2 运行期静态层（配置是否落地）

⚠️ **regmap 不提供"这个 map 当前的字节序是什么"的可用查询接口。**
公共头 `include/linux/regmap.h:1219-1225` 一共给了五个 getter
（`regmap_get_val_bytes` / `regmap_get_max_register` / `regmap_get_reg_stride` /
`regmap_get_raw_read_max` / `regmap_get_raw_write_max`），**没有一个是查字节序的。**

⚠️ **一个必须点明的陷阱**：**`regmap_get_val_endian()` 确实带 `EXPORT_SYMBOL_GPL`
（`drivers/base/regmap/regmap.c:727`），但它的声明只在 `drivers/base/regmap/internal.h:279`，
公共头里没有** ⇒ **树外驱动包含 `<linux/regmap.h>` 拿不到它的原型。**
而且它的参数是 `(dev, bus, config)` 而不是 `(map)` ⇒
**它是给适配层在初始化时算字节序用的，不是"查一个已建好的 map"的接口。**
**"有 EXPORT_SYMBOL 不等于能用"，判据是"公共头里有没有声明"**（05 §0 同一条判据）。

**可用的间接办法两个**：

| 办法 | 怎么做 | 局限 |
|---|---|---|
| **debugfs 看 `range`/`registers`** | `cat /sys/kernel/debug/regmap/<name>/registers` | **值就是按当前字节序解释的** ⇒ 值明显翻转就是字节序错（04 §4） |
| **确认 regmap 存不存在、叫什么名** | `ls /sys/kernel/debug/regmap/` | **syscon 建的 map 名字是 `%pOFn@%pa` 格式**（`drivers/mfd/syscon.c:103`）⇒ **能直接对上 DT 节点名和地址**，这是本子系统最好用的一条线索 |

**`reg_stride` / `val_bytes` / `max_register` 三个能查**（01 §7），
**所以 `reg-io-width` 是否生效可以间接验证**：
**`reg-io-width = <1>` ⇒ `regmap_get_val_bytes()` 返回 1**。

### §5.3 运行期动态层

| 手段 | 做法 | 去 |
|---|---|---|
| tracepoint | `regmap_reg_read` / `regmap_reg_write` 看实际访问的地址和值 | 07 §3（17 个事件） |
| debugfs 直接读写 | **绕过驱动逻辑，直接验证"regmap 本身通不通"** | 07 §4 |
| **对比同一寄存器的 debugfs 值与手册值** | **字节序错会看到 `0x12000000` vs `0x12` 这种整体翻转** | §5.2 |

---

## §6 错误消息 → 原因映射表

⚠️ **先说最重要的一条**：**`drivers/base/regmap/regmap-mmio.c` 里有 0 条
`dev_err` / `pr_err`**（重跑：`grep -c 'dev_err\|pr_err\|dev_warn' drivers/base/regmap/regmap-mmio.c`）
⇒ **MMIO regmap 初始化失败时完全不打印，只返回错误码。**
**能看到消息的都是调用方打的**（syscon 就是这样）。

| 消息 | 出处（定位到行） | 原因 |
|---|---|---|
| `regmap init failed` | `drivers/mfd/syscon.c:115`（`of_syscon_register` 路径） | **`regmap_init_mmio()` 返回错误** ⇒ 最常见是 `reg-io-width` 取值不被支持（§3.4）或 `reg` 长度小于一个寄存器 |
| `regmap init failed` | `drivers/mfd/syscon.c:350`（**platform driver 路径，用 `dev_err` 带设备名**） | 同上，**但这条能看出是哪个设备** |
| `Failed to retrieve valid hwlock: <n>` | `drivers/mfd/syscon.c:96` | **`hwlocks` 属性有问题**；⚠️ **紧接着 `fallthrough` 到 `-EPROBE_DEFER` 分支一律失败**（`:97-99`） |
| `Invalid range %d: %u < %u` | `drivers/base/regmap/regmap.c:1158` | **`ranges[]` 里 `range_max < range_min`** ——**这是驱动 config 的问题，不是 DT**（04 §3） |
| `Invalid range %d: %u > %u` | `regmap.c:1164` | 范围超过 `max_register` |
| `Invalid range %d: window_len 0` | `regmap.c:1176` | `window_len` 忘填 |
| `Failed to add range %d` | `regmap.c:1227` | 分页范围互相重叠 |
| `Failed to schedule write: %d` | `regmap.c:1838` | 异步写提交失败（01 §6） |

⚠️ **`regmap.c` 全文只有 10 条打印，其中 7 条都在讲 `ranges`**
（`:1158` `:1164` `:1170` `:1176` `:1195` `:1203` `:1227`）
⇒ **regmap 核心唯一"话多"的地方是分页配置**，其余一律靠错误码（07 §5）。

---

## §7 死字属性与不成立的期待

**这一节是本篇价值最高的部分之一**：**绑定文档不会告诉你哪些写了没用。**

| 期待 | 实际 | 依据 |
|---|---|---|
| **"`reg-io-width` 是通用属性，任何节点都能写"** | ⚠️ **错**：**只有 syscon 读它**（`drivers/mfd/syscon.c:81`） | 普通驱动自建 regmap 时不会去读，**写了完全无效** |
| **"`hwlocks` 能给任何 regmap 加硬件自旋锁"** | ⚠️ **错**：**只有 syscon 路径会把它变成 `use_hwlock`**（`syscon.c:85-89`） | 自建 regmap 要用 hwspinlock 得自己填 `config.use_hwlock`（`include/linux/regmap.h:434`） |
| **"`native-endian` 在 arm64 上有用"** | ⚠️ **arm64 DTS 里 0 处使用** | 它是给"CPU 与 MMIO 字节序同一开关"的平台设计的（绑定文档 §Note） |
| **"`reg-io-width = <8>` 合法就能用"** | ⚠️ **绑定允许，代码拒绝** | `regmap-mmio.c:43-60` 无 `case 64`（§3.4）；**全树 0 处使用** |
| **"DT 能配寄存器地址的字节序"** | ⚠️ **不能** | `regmap_get_reg_endian()`（`regmap.c:666-687`）不读 fwnode（§1 第 2 条） |
| **"`big-endian` 写了总会生效"** | ⚠️ **五种情形下静默无效** | §4 的决策树 ②③④⑤⑥ |
| **"regmap 有自己的节点类型"** | ⚠️ **没有** | regmap 是框架，不是设备类；`bindings/regmap/regmap.txt` 只定义了三个可附加到任意节点的布尔属性 |

**反例（说明结论的方向）**：**`big-endian` 在 arm64 上确实有 7 个文件在用，
而且 `fsl-ls1043a.dtsi:317` 那个 syscon 节点是真生效的**（§3.3 例 2）
⇒ **不能反过来说"regmap 的 DT 属性都是死字"。**

---

## §8 阅读下一篇

| 想知道 | 去 |
|---|---|
| debugfs / tracepoint 具体怎么用 | `07-debug.md` |
| `regmap_config` 全部 54 个字段 | `01-api.md` §2 |
| 分页（`ranges`）机制 | `04-extensions.md` §3 |
| 代码骨架 | `05-usage.md` |
| 为什么字节序要分 reg 和 val 两套 | `08-feynman.md` |

