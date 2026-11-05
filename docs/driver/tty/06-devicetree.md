---
title: 设备树
---

# tty 核心层与设备树

## §0 发现路径分诊表（先读这张表）

⚠️ **本篇最重要的结论放在最前面：tty 核心层一行 DT 代码都没有。**

**实测（严格口径）**：对核心层 12 个 `.c` 加 `n_tty.c`、`pty.c` 共 14 个文件
数 `of_[a-z_]+(` 的出现次数——

```
tty_io.c     → 0     tty_port.c     → 0     tty_ldisc.c     → 0
tty_buffer.c → 0     tty_ioctl.c    → 0     tty_jobctrl.c   → 0
tty_ldsem.c  → 0     tty_audit.c    → 0     tty_baudrate.c  → 0
tty_mutex.c  → 0     n_tty.c        → 0     pty.c           → 0
```

**14 个文件全部为 0。** 而且再查一遍 `of.h` / `device_node` / `of_node`
这三个关键词——**在这 14 个 `.c` 和 6 个 `include/linux/tty*.h` 里同样零命中。**

⇒ **`struct tty_driver`（21 个字段）和 `struct tty_port`（22 个字段）里
没有任何 DT 相关字段，注册 API 也不接受 `device_node` 参数。**

所以这一篇不是"tty 的 DT 绑定手册"，而是**"tty 设备到底从哪来"的路径图**。

| # | 发现路径 | 谁创建设备 | 走 DT 吗 | arm64 上用不用 | 本篇覆盖 |
|---|---|---|---|---|---|
| 1 | **串口控制器（UART）** | `serial/` 层的 platform driver → `uart_add_one_port()` → tty 核心 | **✔ 走**，但**读 DT 的是串口层，不是 tty 核心** | **最常用** | §2（只讲交接面，不讲串口层细节） |
| 2 | **serdev（串口从设备）** | `drivers/tty/serdev/core.c` 扫 UART 节点的**子节点** | **✔ 走** | 蓝牙/GNSS/NFC 模块常用 | **§3（本篇重点，唯一真正的 DT 章节）** |
| 3 | **console 选择** | `/chosen` 的 `stdout-path` → `of_console_check()` | **✔ 走** | **arm64 DTS 里 396 个文件在用** | §4 |
| 4 | **pty / devpts** | `drivers/tty/pty.c` 自己在 `init` 里注册两个 driver | **✘ 完全不走** | 总是可用 | §5（配置来自 mount 选项） |
| 5 | **其他虚拟 tty**（`ttynull`、`ttyprintk`、`hvc`…） | 各自 `module_init` 里硬编码注册 | **✘ 不走** | 视配置 | §5.2 |
| 6 | **ACPI** | ACPI 枚举 | ✘ | **本文档集裁掉（arm64-only 声明）** | 不覆盖，理由记在 `99-evidence.md` §2.7 |

⚠️ **读者最容易走错的一步**：想给自己的 UART 加个 DT 属性，就来 tty 核心找
消费代码——**找不到，因为消费者在 `drivers/tty/serial/`，而那一层被本文档集
显式排除了**（本树改过它，理由在 `99-evidence.md` §1.1）。§2 会把交接面说清楚，
好让你知道该去哪一层找。

### §0.1 按任务找章节

| 我想… | 去哪节 |
|---|---|
| 确认 tty 核心到底读不读 DT | **§1（一句话 + 取证）** |
| 知道 UART 的 DT 属性谁消费 | §2 |
| **在 UART 下挂一个蓝牙/GNSS 模块** | **§3** |
| 让内核 console 出在某个串口上 | §4 |
| 改 `/dev/pts` 的权限/上限 | §5.1 |
| DT 写错了但内核不报错，查原因 | **§6（静默失效）** |
| 从内核报错信息倒推 | §7 |
| overlay 能不能热插 serdev 设备 | §8 |

---

## §1 结论章：核心层 DT 参与度 = 0

### §1.1 取证（可复现）

```bash
# 口径 1：of_* 函数调用
grep -cE '\bof_[a-z_]+\(' drivers/tty/tty_*.c drivers/tty/n_tty.c drivers/tty/pty.c
#   ⇒ 14 个文件全部输出 0

# 口径 2：三个关键词（比口径 1 更宽，能抓到只声明不调用的情况）
grep -rn 'of\.h\|device_node\|of_node' \
     drivers/tty/tty_*.c drivers/tty/n_tty.c drivers/tty/pty.c include/linux/tty*.h
#   ⇒ 零命中
```

**两个口径都是零，可以放心下结论。**

### §1.2 对比：这在本文档集里是什么水平

和已经做过的几个子系统横向比一下，能看出 tty 的特殊：

| 子系统类型 | 典型代表 | 核心层读 DT 吗 |
|---|---|---|
| 硬件资源框架 | pinctrl / clk / regulator / phy / reset | **核心层自己就有 `of_xlate` / `of_parse_phandle` 这一层** |
| 消费型框架 | dmaengine / mailbox | 核心提供 `of_*_request` 之类的查表函数 |
| **tty** | — | **完全不读。DT 的活全在它下面那一层（`serial/`）或旁边那一层（`serdev/`）** |

**为什么会这样**：tty 核心的抽象边界画在 **"字符流 + 索引号"** 上——
它只认识 `driver` 和 `index`（`tty_port_link_device(port, driver, index)` 那个
`index`，`drivers/tty/tty_port.c:119`）。**"这个 index 对应哪块硬件、
硬件在什么地址、用哪个中断"，全部是下层的事。** 这也是它能同时承载真串口、
伪终端、虚拟控制台、USB 转串口的原因。

### §1.3 唯一的例外：`serdev` 那四个函数

**严格说，`drivers/tty/tty_port.c` 里有四个函数名带 `serdev`**，
它们是 tty 核心通向 DT 世界的唯一通道：

| 函数 | 定位 | 作用 |
|---|---|---|
| `tty_port_register_device_attr_serdev()` | `drivers/tty/tty_port.c:183`（EXPORT `:201`） | **实际逻辑都在这个** |
| `tty_port_register_device_serdev()` | `drivers/tty/tty_port.c:213`（EXPORT `:220`） | 上面那个的简化包装 |
| `tty_port_unregister_device()` | `drivers/tty/tty_port.c:232`（EXPORT `:243`） | 反向 |

⚠️ **但注意：这三个函数自己也不读 DT。** 它们做的是"转发"——
把 `port` 交给 `serdev_tty_port_register()`（声明 `include/linux/serdev.h:314`，
定义 `drivers/tty/serdev/serdev-ttyport.c:264`），**读 DT 的动作发生在
`drivers/tty/serdev/core.c` 里**。详见 §3。

---

## §2 路径 1：UART 的 DT 属性，消费者在哪一层

### §2.1 三层分工

```
DT:  serial@12340000 {                       ← 属性写在这里
         compatible = "…";  reg = <…>;
         interrupts = <…>;  clocks = <…>;
     }
         │
         │ 读 DT 的是这一层（本文档集不覆盖）
         ▼
     drivers/tty/serial/<厂商>.c            platform driver 的 probe
         │  of_property_read_u32() / devm_clk_get() / platform_get_irq() …
         │
         │ 交接面：uart_add_one_port()
         ▼
     drivers/tty/serial/serial_core.c        UART 层的通用壳
         │
         │ 交接面：tty_port_link_device() + tty_register_driver()
         ▼
     drivers/tty/tty_io.c / tty_port.c       本文档集覆盖的这一层
                                             到这里只剩 driver + index
```

**所以查 UART 的 DT 属性消费代码，正确的 grep 目标是
`drivers/tty/serial/`，不是 `drivers/tty/tty_*.c`。**

### §2.2 通用属性在哪个绑定文档

跨厂商的通用 UART 属性定义在
`Documentation/devicetree/bindings/serial/serial.yaml`（153 行）。
它自己就说明了定位：**"This document lists a set of generic properties…
Whether these properties apply to a particular device depends on the DT bindings
for the actual device."**（`:13-16`）

节点名有强制模式（`Documentation/devicetree/bindings/serial/serial.yaml:24-25`）：**`pattern: "^serial(@.*)?$"`**
—— 节点必须叫 `serial` 或 `serial@<地址>`。

该文件里的通用属性（都是**可选**的）：

| 属性 | 出处 | 作用 |
|---|---|---|
| `label` | `Documentation/devicetree/bindings/serial/serial.yaml:27` | 人可读标签 |
| `cts-gpios` / `dcd-gpios` / `dsr-gpios` / `dtr-gpios` / `rng-gpios` / `rts-gpios` | `Documentation/devicetree/bindings/serial/serial.yaml:29` 起 | 用 GPIO 模拟调制解调器控制线（硬件没有专门引脚时） |

厂商专属属性各自在 `Documentation/devicetree/bindings/serial/` 下的
单独文件里（该目录条目很多，如 `8250.yaml`、`fsl-imx-uart.yaml`、
`amlogic,meson-uart.yaml`、`cdns,uart.yaml` 等）。

### §2.3 `serialN` 别名：`stdout-path` 能写 `serial0` 的原因

`Documentation/devicetree/bindings/serial/serial.yaml:18-20` 明说：**每个启用的 UART 可以在 `/aliases` 节点里
有一个 `serialN` 别名，N 是端口号**。

```dts
/ {
	aliases {
		serial0 = &scif0;         /* 这一行让下面那句能写 "serial0" */
	};

	chosen {
		stdout-path = "serial0:115200n8";
	};
};
```

**没有这个别名，`stdout-path` 就必须写完整路径**（如
`stdout-path = "/soc/serial@e6e88000:115200n8"`）。→ §4

---

## §3 路径 2：serdev —— 唯一真正的 DT 章节

**场景**：板子上有个蓝牙模块，通过 UART 连到 SoC。
它不是"一个串口"，而是"挂在串口上的一个设备"——不该由用户态去
`open("/dev/ttyS1")`，而该由内核里的蓝牙驱动直接接管这条线。
**serdev 就是干这个的总线。**

### §3.1 DT 结构：子节点，而不是 phandle

⚠️ **这是 serdev 和本文档集里其他子系统最大的写法差异**：
pinctrl / clk / dma 那些都用 **phandle 引用**（`clocks = <&osc>`），
而 serdev 用的是 **物理包含关系——从设备写成 UART 节点的子节点**。

规范在 `Documentation/devicetree/bindings/serial/serial.yaml:98-137`（`patternProperties` 段）：

> **"Serial attached devices shall be a child node of the host UART device
> the slave device is attached to. It is expected that the attached device is
> the only child node of the UART device. The slave device node name shall
> reflect the generic type of device for the node."**（`Documentation/devicetree/bindings/serial/serial.yaml:104-107`）

三条硬要求：

| # | 要求 | 出处 |
|---|---|---|
| 1 | **必须是 UART 节点的子节点** | `Documentation/devicetree/bindings/serial/serial.yaml:104-105` |
| 2 | **期望是唯一的子节点** | `Documentation/devicetree/bindings/serial/serial.yaml:105-106`（"expected… the only child node"） |
| 3 | **子节点必须有 `compatible`** | `Documentation/devicetree/bindings/serial/serial.yaml:135-137`（`required: - compatible`） |

节点名要反映设备的**通用类型**（不是型号）——所以叫 `bluetooth`、
`gnss`、`nfc`，而不是 `bcm4330` 或 `wl1837`。

### §3.2 in-tree 真实实例

绑定文档自带的例子（`Documentation/devicetree/bindings/serial/serial.yaml:141-153`）：

```dts
serial@1234 {
        compatible = "ns16550a";
        reg = <0x1234 0x20>;
        interrupts = <1>;

        bluetooth {
                compatible = "brcm,bcm4330-bt";
                interrupt-parent = <&gpio>;
                interrupts = <10>;
        };
};
```

**arm64 树里的真实节点**（`arch/arm64/boot/dts/renesas/hihope-rev4.dtsi:41-46`）：

```dts
&hscif0 {
	bluetooth {
		compatible = "ti,wl1837-st";
		enable-gpios = <&gpio3 13 GPIO_ACTIVE_HIGH>;
	};
};
```

用量（arm64 DTS 目录，按含该子节点的文件数计）：

| 子节点名 | 文件数 |
|---|---|
| `bluetooth` | **97** |
| `nfc` | 5 |
| `gnss` | 1 |

⇒ **serdev 在 arm64 上的绝对主力场景是蓝牙 UART 模块。**

### §3.3 属性总表（serdev 从设备节点）

| 属性 | 必填？ | 类型 | 谁消费 | 出处 |
|---|---|---|---|---|
| `compatible` | **必填** | string | **`drivers/tty/serdev/core.c:528` 先检查它存在，然后靠它匹配从设备驱动** | `Documentation/devicetree/bindings/serial/serial.yaml:135-137` |
| `max-speed` | 否 | u32 | 从设备驱动自己读 | `Documentation/devicetree/bindings/serial/serial.yaml:115-121` |
| `current-speed` | 否 | u32 | 从设备驱动自己读 | `Documentation/devicetree/bindings/serial/serial.yaml:123-134` |

⚠️ **`max-speed` 和 `current-speed` 不是一回事**，绑定文档把区别写得很细：

- `max-speed` = **"这条线最高能跑多快"**。`Documentation/devicetree/bindings/serial/serial.yaml:118-121` 特意说明
  它只在"**最高速率低于从设备本身的能力**"时才该写——比如
  "a particular board has some signal quality issue"（板子信号质量差）
  或 "the host processor can't support higher baud rates"
- `current-speed` = **"它现在正跑在多快"**。`Documentation/devicetree/bindings/serial/serial.yaml:125-134` 给了
  三个该写的场合：设备支持自动波特率、速率是 bootloader 设的且无法复位设备、
  速率由固件配置且无法查询

### §3.4 消费代码：DT 扫描发生在哪一行

**完整链条**（这是本篇唯一需要跨目录追的路径）：

```
串口驱动 probe
  └─ tty_port_register_device_attr_serdev()      drivers/tty/tty_port.c:183
       ├─ tty_port_link_device(port, driver, index)   先把 port 绑上
       └─ serdev_tty_port_register(port, …)      drivers/tty/serdev/serdev-ttyport.c:264
            └─ serdev_controller_add(ctrl)
                 ├─ ctrl->dev.of_node = parent->of_node;   drivers/tty/serdev/core.c:502
                 └─ of_serdev_register_devices(ctrl)       drivers/tty/serdev/core.c:520
                      └─ for_each_available_child_of_node(ctrl->dev.of_node, node)  :527
                           └─ if (!of_get_property(node, "compatible", NULL))  :528
                                  → 跳过这个子节点
```

三个关键点：

| # | 行 | 做什么 |
|---|---|---|
| 1 | `drivers/tty/serdev/core.c:502` | **把 UART 的 `of_node` 直接借给 serdev 控制器** —— 所以 serdev 看到的就是 UART 那个节点 |
| 2 | `drivers/tty/serdev/core.c:527` | 遍历**可用的**子节点（`status = "disabled"` 的会被 `for_each_available_child_of_node` 跳过） |
| 3 | `drivers/tty/serdev/core.c:528` | **没有 `compatible` 的子节点被静默跳过** → §6.1 |

匹配从设备驱动走的是 OF 的标准机制（`drivers/tty/serdev/core.c:95`
的 `of_driver_match_device()`），modalias 也走 OF
（`:35` 的 `of_device_modalias()`、`:55` 的 `of_device_uevent_modalias()`）。

### §3.5 ⚠️ 用了 serdev，`/dev/ttyXXX` 就消失了

**这是 serdev 最反直觉的后果**，代码写得很直白
（`drivers/tty/tty_port.c:190-196`）：

```c
dev = serdev_tty_port_register(port, device, driver, index);
if (PTR_ERR(dev) != -ENODEV) {
	/* Skip creating cdev if we registered a serdev device */
	return dev;
}
return tty_register_device_attr(driver, index, device, drvdata, attr_grp);
```

**判据是"serdev 注册的返回值是不是 `-ENODEV`"**：

| serdev 注册结果 | 后果 |
|---|---|
| **成功**（DT 里有合法子节点） | **直接 return，不建字符设备节点** ⇒ **`/dev/ttyS1` 不存在**，这条线归内核驱动 |
| 返回 `-ENODEV`（没有子节点，或子节点没 `compatible`） | 落到 `tty_register_device_attr()` ⇒ 正常建 `/dev/ttyXXX` |
| 返回其他错误 | **也直接 return 那个错误** ⇒ 既没有 serdev 设备也没有 tty 节点 |

⚠️ **所以"UART 节点下加了个 `bluetooth` 子节点，然后 `/dev/ttyS1` 不见了"
不是 bug，是设计。** 反过来也成立：**想让某条 UART 同时既给内核蓝牙栈
用、又暴露成 `/dev/ttyS1`，做不到**——二者互斥。

---

## §4 路径 3：console 从 DT 来

### §4.1 两个属性，一个解析点

内核 console 出在哪个串口上，DT 侧只有 `/chosen` 节点里的一个属性：

| 属性 | 位置 | 解析点 |
|---|---|---|
| `stdout-path` | `/chosen` | `drivers/of/base.c:1922` |
| `linux,stdout-path` | `/chosen` | `drivers/of/base.c:1923-1924`，仅为兼容老 DT 保留 |

解析逻辑（`drivers/of/base.c:1918-1931`）很短，三步：

1. 先读 `stdout-path`，读不到再读 `linux,stdout-path`（`:1922-1924`）。
   注释原文就写着这是 legacy compatibility（`:1919`）。
2. 把字符串按路径解析成节点，冒号后面的部分存进 `of_stdout_options`
   （`:1928` 的 `of_find_node_opts_by_path()`）——所以 `serial0:115200n8`
   里的 `115200n8` 是 options，不是路径的一部分。
3. 给这个节点打上 `FWNODE_FLAG_BEST_EFFORT`（`:1930`）。
   该 flag 的唯一消费者是 `dev_is_best_effort()`（`drivers/base/core.c:1071-1075`），
   作用是在 fw_devlink 的依赖排序里放宽这个设备等待 supplier 的要求，
   好让 console 尽早探测出来。

`of_stdout` 是个全局变量：整棵树只有一个 stdout 节点。

### §4.2 `of_console_check()` 的三个否决条件

串口驱动 probe 时调 `of_console_check()`（`drivers/of/base.c:2040`，
kerneldoc 在 `:2029-2039`）问「我是不是那个 console」。它先做三重否决
（`:2042-2043`）：

```c
if (!dn || dn != of_stdout || console_set_on_cmdline)
	return false;
```

| 条件 | 含义 | 常见触发场景 |
|---|---|---|
| `!dn` | 设备根本没有 `of_node` | 非 DT 枚举的串口 |
| `dn != of_stdout` | 不是 `stdout-path` 指的那个节点 | 属性写错、别名指错 |
| `console_set_on_cmdline` | 命令行上已经给了 `console=` | bootargs 里有 `console=ttyAMA0,115200` |

⚠️ 第三条就是「DT 和命令行谁赢」的答案：**命令行赢**。
只要 bootargs 里出现过 `console=`，`stdout-path` 就整体失效——
不是"合并"，也不是"追加为第二个 console"，而是 `of_console_check()`
对每一个串口都直接返回 false。

通过三重否决后，它调 `add_preferred_console(name, index, of_stdout_options)`
并把返回值取反（`:2049`）——即 kerneldoc 说的 "Return: TRUE if console
successfully setup"。

### §4.3 arm64 上怎么写

实测（`arch/arm64/boot/dts/`，按含该属性的文件数计）：**396 个文件**用了
`stdout-path`。典型写法（`arch/arm64/boot/dts/realtek/rtd1295-zidoo-x9s.dts:25`）：

```dts
chosen {
	stdout-path = "serial0:115200n8";
};
```

`serial0` 依赖 `/aliases` 里的别名，见 §2.3。options 串的格式是
`<波特率><奇偶><数据位><流控>`，`115200n8` = 115200 波特、无校验、8 数据位。

---

## §5 完全不走 DT 的那些 tty

### §5.1 pty / devpts：配置来自 mount 选项和 sysctl

`drivers/tty/pty.c` 在 `unix98_pty_init()` 里硬编码注册两个 driver
（`ptm_driver` 和 `pts_driver`），没有任何 DT 参与。它的可配置项分两处：

**一处是 devpts 的 mount 选项**（`fs/devpts/inode.c:109-117` 的 token 表）：

| 选项 | 默认值 | 解析点 |
|---|---|---|
| `uid=%u` | 不设 | `fs/devpts/inode.c:278-286` |
| `gid=%u` | 不设 | `fs/devpts/inode.c:287-295` |
| `mode=%o` | `0600`（`inode.c:28`） | `fs/devpts/inode.c:296-300` |
| `ptmxmode=%o` | `0000`（`inode.c:35`） | `fs/devpts/inode.c:301-305` |
| `max=%d` | `NR_UNIX98_PTY_MAX` = `1 << MINORBITS`（`inode.c:258`） | `fs/devpts/inode.c:306-311` |
| `newinstance` | — | 保留项 |

**另一处是 sysctl**（`fs/devpts/inode.c:48-90` 的 `pty_table`，
在 `:633` 由 `register_sysctl_table()` 注册到 `/proc/sys/kernel/pty/`）：

| 路径 | 变量 | 默认值 |
|---|---|---|
| `/proc/sys/kernel/pty/max` | `pty_limit` | `NR_UNIX98_PTY_DEFAULT` = 4096（`include/linux/tty.h:24`） |
| `/proc/sys/kernel/pty/reserve` | `pty_reserve` | `NR_UNIX98_PTY_RESERVE` = 1024（`include/linux/tty.h:25`） |
| `/proc/sys/kernel/pty/nr` | 只读计数 | — |

⚠️ `max=`（mount 选项，每个 devpts 实例）和 `kernel/pty/max`（sysctl，全局）
是两套独立的上限，**两个都要过**。全局那道门的判断在
`fs/devpts/inode.c:534-535`：非 `reserve` 挂载的实例，可用额度是
`pty_limit - pty_reserve`，也就是默认 `4096 - 1024 = 3072`——
预留的 1024 个留给主 devpts 实例。

### §5.2 其他虚拟 tty

| 驱动 | 注册方式 | DT |
|---|---|---|
| `ttynull` | `drivers/tty/ttynull.c`：`module_init(ttynull_init)`（`:106`）里硬编码注册 1 个设备（`tty_register_driver` 在 `:85`） | 无 |
| `ttyprintk` | `drivers/char/ttyprintk.c`（注意不在 `drivers/tty/` 下）：`device_initcall(ttyprintk_init)`（`:229`），同样 1 个设备（`:185` 分配、`:205` 注册） | 无 |
| `hvc` 系列 | 由 hypervisor 后端探测 | 无（不走 UART 那套） |

`pty` 自己也是这一类：`unix98_pty_init()`（`drivers/tty/pty.c:873`）里
硬编码分配并注册 `ptm_driver`（`:875`，名字 `ptm`、`:893`）和
`pts_driver`（`:883`），两者都是 `NR_UNIX98_PTY_MAX` 条线。

这几个驱动是最小 tty 驱动骨架的现成参照物，骨架见 `05-usage.md` §1。

---

## §7 三层定位与报错信息倒推

### §7.1 症状落在哪一层

DT 相关的故障几乎都能靠「dmesg 里出现了什么」一步定位到层：

| 层 | 谁负责 | 症状特征 | 典型证据 |
|---|---|---|---|
| 层 1 DT / 绑定 | DTS 作者 | **完全没有相关输出**；`/proc/device-tree/` 里节点或属性缺失 | `of_serdev_register_devices()` 的 `continue`（`drivers/tty/serdev/core.c:528-529`） |
| 层 2 框架（serdev / tty 核心） | 内核框架 | dmesg 有明确 `dev_err`，或 `/sys/bus/serdev/` 下结构不对 | `"controller busy"`（`drivers/tty/serdev/core.c:112`） |
| 层 3 从设备 / 串口驱动 | 驱动作者 | 设备已 bind，报错来自驱动自己的 probe | 各驱动自有信息，不在本文档集范围 |

层 1 的判定手法：**先看 `/proc/device-tree/`，不要先看 dmesg**。
因为层 1 的失效是静默的（§6.1），dmesg 里没有任何"缺了什么"的线索。

三个 sysfs 落点（这是层 1/层 2 的分界证据）：

| 路径 | 出现说明什么 | 命名依据 |
|---|---|---|
| `/sys/bus/serdev/devices/serialN` | serdev 控制器建成了（层 2 通过一半） | `dev_set_name(&ctrl->dev, "serial%d", id)`，`drivers/tty/serdev/core.c:505` |
| `/sys/bus/serdev/devices/serialN-M` | 从设备也建成了（层 1 完全通过） | `dev_set_name(&serdev->dev, "%s-%d", dev_name(parent), serdev->nr)`，`drivers/tty/serdev/core.c:108` |
| 上述目录里的 `driver` 符号链接 | 从设备驱动 bind 成功（层 3 也过了） | driver core 通用行为 |

### §7.2 报错信息→原因表（逐条 grep 核对过）

| 信息原文 | 位置 | 级别 | 含义与动作 |
|---|---|---|---|
| `tty port <名><序号> registered` | `drivers/tty/serdev/serdev-ttyport.c:293` | info | **成功标志**。看到它说明这条 UART 已交给 serdev，`/dev/ttyXXX` 按设计不会出现（§3.5） |
| `controller busy` | `drivers/tty/serdev/core.c:112` | err | UART 节点下写了第 2 个子节点。代码只支持 1 个（注释在 `:110`）。删掉多余子节点 |
| `Can't add <名>, status <错误>` | `drivers/tty/serdev/core.c:119-120` | err | `device_add()` 本身失败，通常是重名或内存不足，不是 DT 写法问题 |
| `failure adding device. status <错误>` | `drivers/tty/serdev/core.c:541-543` | err | 某个子节点注册失败，但**循环会继续处理后面的子节点**（`:544` 只做 `serdev_device_put`）。最常见的底层错误就是上面那条 `-EBUSY` |
| `unable to allocate serdev controller identifier.` | `drivers/tty/serdev/core.c:491-492` | err | ida 耗尽，与 DT 无关 |
| `no devices registered: of:<错误> acpi:<错误>` | `drivers/tty/serdev/core.c:787-788` | **dbg** | OF 和 ACPI 两路都没注册出从设备 ⇒ 返回 `-ENODEV` ⇒ 上层回退去建 `/dev/ttyXXX`。**默认看不见**，要开动态调试 |
| `adding child <节点>` | `drivers/tty/serdev/core.c:531` | **dbg** | 排查 §6.1 的关键一行：有它说明 `compatible` 检查过了 |
| `failed to allocate serdev device for <名>` | `drivers/tty/serdev/core.c:679` | err | ACPI 枚举路径专有，arm64 未开 ACPI 时不会出现 |
| `failed to enumerate serdev slaves` | `drivers/tty/serdev/core.c:749` | warn | 同上，ACPI 路径专有 |
| `<名>: Attempt to register invalid tty line number (<N>)` | `drivers/tty/tty_io.c:3225-3226` | err | `index >= driver->num`（判断在 `:3225`）。串口层传下来的 index 越界，多半是 `tty_alloc_driver()` 的线数给小了 |
| `Falling back ldisc for <名>.` | `drivers/tty/tty_ldisc.c:498` | warn | 与 DT 无关，行规程切换失败回退 |

⚠️ 这张表里**三条最关键的信息都是 `dev_dbg` 级别**
（`drivers/tty/serdev/core.c:531`、`:787`）。也就是说 serdev 的 DT 路径**默认是一个哑框架**：
配对了不说话（只有 ttyport 那条 info），配错了也不说话。
排查前先开动态调试：

```sh
echo 'file drivers/tty/serdev/core.c +p' > /sys/kernel/debug/dynamic_debug/control
```

---

## §8 overlay 与 OF_DYNAMIC：serdev 不支持运行时热插

结论：**在运行时用 overlay 往已经 probe 完的 UART 节点下加一个 serdev
子节点，不会创建出 serdev 设备。**

两道门都过了：

1. **grep 源码树**：`of_reconfig_notifier_register` / `OF_RECONFIG` /
   `of_overlay` 在 `drivers/tty/` **整个目录零命中**（含 `serdev/`、
   `serial/`、核心层）。
2. **grep 本文档集**：没有任何一篇声明过 tty 侧支持 overlay，不存在自相矛盾。

对照组——**同样从 DT 子节点枚举设备的总线，都注册了 reconfig 通知**：

| 总线 | 注册点所在文件 |
|---|---|
| platform | `drivers/of/platform.c` |
| i2c | `drivers/i2c/i2c-core-base.c` |
| spi | `drivers/spi/spi.c` |
| gpio | `drivers/gpio/gpiolib.c` |
| **serdev** | **无** |

机制上的原因：serdev 的 DT 扫描只有一个入口——
`serdev_controller_add()` → `of_serdev_register_devices()`
（`drivers/tty/serdev/core.c:784`），而 `serdev_controller_add()` 只在
串口驱动 probe 时被调一次（经 `serdev_tty_port_register()`，
`drivers/tty/serdev/serdev-ttyport.c:289`）。没有通知链就没有第二次扫描。

可行的替代做法：**在串口驱动 probe 之前就让 overlay 生效**——
即把 overlay 连同 UART 节点一起加载，让串口 probe 时看到的就是完整的树。
或者 unbind/rebind 串口驱动，逼它重走一遍 probe。

⚠️ 反过来的方向（overlay 移除）同样没有清理通道：`tty_port_unregister_device()`
（`drivers/tty/tty_port.c:232`）要由串口驱动自己在 remove 里调，
不会被 OF 的 detach 事件触发。

---

## §9 延伸阅读

| 想继续了解 | 去哪篇 |
|---|---|
| tty 四大对象和注册 API 全清单 | `01-api.md` |
| `tty_driver` / `tty_port` 的字段逐个解释 | `02-datastructures.md` |
| 注册流程在时间线上的位置 | `03-internals.md` |
| serdev 作为子框架的完整定位 | `04-extensions.md` |
| 写一个 tty 驱动 / 行规程 / 用户态程序 | `05-usage.md` |
| 动态调试、sysfs、常见故障 | `07-debug.md` |
| 为什么 tty 是这个结构（第一性解释） | `08-feynman.md` |
| 本篇每条断言的取证命令 | `99-evidence.md` |

---

## §6 静默失效：DT 写了但什么都没发生

### §6.1 加了 `bluetooth` 子节点，但从设备驱动没起来

沿 §3.4 的链条从下往上排查：

```
子节点有 compatible 吗？
├─ 没有 → drivers/tty/serdev/core.c:528 直接 continue，静默跳过
│         症状：dmesg 里连 "adding child" 都没有
│         注意：这是本篇最容易踩的一条，因为 DT 编译不报错
└─ 有 → status 是 "disabled" 吗？
    ├─ 是 → for_each_available_child_of_node（:527）跳过，同样静默
    └─ 否 → 它是 UART 节点的第 2 个子节点吗？
        ├─ 是 → serdev_device_add() 报 -EBUSY
        │       dmesg: "controller busy"（drivers/tty/serdev/core.c:112）
        │       原因：drivers/tty/serdev/core.c:110 注释写明 "Only a single slave
        │       device is currently supported."
        └─ 否 → 有匹配 compatible 的从设备驱动被编进内核吗？
            ├─ 没有 → 设备建出来了但没 bind
            │         症状：/sys/bus/serdev/devices/ 下有节点，
            │         但 driver 符号链接不存在
            └─ 有 → 看该从设备驱动自己的 probe 报错
```

⚠️ 前两条分支（缺 `compatible`、`status = "disabled"`）都是
**dev_dbg 级别或干脆无输出**，默认内核配置下 dmesg 一个字都不会多。
要看见得开 `CONFIG_DYNAMIC_DEBUG` 并打开 `drivers/tty/serdev/core.c` 的
动态调试开关。

### §6.2 串口本身也不见了

`serdev_controller_add()`（`drivers/tty/serdev/core.c:770`）的返回值决定一切
（`:784-791`）：

```c
ret_of   = of_serdev_register_devices(ctrl);
ret_acpi = acpi_serdev_register_devices(ctrl);
if (ret_of && ret_acpi) {
	dev_dbg(&ctrl->dev, "no devices registered: of:%pe acpi:%pe\n", ...);
	ret = -ENODEV;
	goto err_rpm_disable;
}
```

arm64 且未开 `CONFIG_ACPI` 时，`acpi_serdev_register_devices()` 是个
恒返回 `-ENODEV` 的空壳（`:757-760`），所以**结果完全由 OF 那一路决定**：

| OF 那路 | serdev_controller_add | 上层 tty_port 行为（§3.5） |
|---|---|---|
| 至少注册成 1 个从设备 | 0 | 不建 `/dev/ttyXXX`，线归内核 |
| 一个都没有 | `-ENODEV` | 回退，正常建 `/dev/ttyXXX` |

⚠️ 所以「串口和从设备都不见了」几乎只有一种成因：
**子节点合法、serdev 注册成功了，但从设备驱动没编进内核**。
此时 `/dev/ttyS1` 已经按设计被跳过，而接管它的驱动又不存在——线彻底哑掉。
排查第一步是 `ls /sys/bus/serdev/devices/`，不是去翻串口驱动。

### §6.3 console 没出来

```
bootargs 里有 console= 吗？
├─ 有 → console_set_on_cmdline 置位，stdout-path 整体失效（§4.2）
│        这是"改了 DT 不生效"最常见的原因
└─ 没有 → stdout-path 指的节点存在吗？
    ├─ 别名指错/节点被 status="disabled" → of_stdout 为空或对不上
    │   症状：dn != of_stdout 恒成立，没有任何串口成为 preferred console
    └─ 对得上 → 该串口驱动调 of_console_check() 了吗？
        └─ 没调就不会成为 console —— 这是串口驱动侧的事，
           不在本文档集覆盖范围（见 §2.1 的分层图）
```

### §6.4 `/dev/pts` 开不出新的

两道门都要查（§5.1）：先看 `cat /proc/sys/kernel/pty/nr` 是否贴近
`kernel/pty/max`（默认 4096），再看该 devpts 实例的 `max=` 挂载选项。
非 `reserve` 实例的实际额度是 `max - reserve`（默认 3072），
判断在 `fs/devpts/inode.c:534-535`。此路径与 DT 无关。
