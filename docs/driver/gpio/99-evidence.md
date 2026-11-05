---
title: 取证附录
---

# 99 · gpio 文档集 —— 取证、方法与未验证项

> 本篇不解释 gpio。它回答三个问题：**前面九篇的每条论断从哪来**、**哪些地方我没验证**、**读者怎么复核**。
>
> 判据：本篇里所有数字都必须能用一条命令重跑。跑出来对不上，整篇就从「取证」降级成「回忆」。

---

## §1 基线

### §1.1 树的状态

```bash
git log -1 --format='%H %s'
# f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158
```

这棵树**不是干净的 upstream 6.1.158** —— 工作区里有大量未提交修改（`git status` 输出 200+ 个文件）。所以必须单独确认 gpio 子系统有没有被动过：

```bash
git status --porcelain -- drivers/gpio include/linux/gpio include/linux/gpio.h \
    include/uapi/linux/gpio.h include/trace/events/gpio.h
# 无输出
```

**无输出 = gpio 子系统的全部源文件与 6.1.158 一致。** 本文档集因此可以按纯 6.1.158 定性。

这一步不能省。同一棵树上 `drivers/gpu/drm/i915/`、`arch/arm64/kernel/` 等目录是被改过的，如果目标子系统落在改动范围内，所有行号都会和 upstream 6.1.158 对不上。

### §1.2 子系统类型判定（skill 规则 3b）

```bash
grep -rn 'struct bus_type' drivers/gpio/gpiolib.c
# 61:static struct bus_type gpio_bus_type = {
```

有 `bus_type` —— 按规则 3b 的机械判据这是「硬件总线型」。**但实际判定为「框架型 + 退化总线」**，理由是这条总线上只有一个 stub driver：

```bash
grep -n 'gpio_stub_drv\|bus_register\|driver_register' drivers/gpio/gpiolib.c
# 4443:static struct gpio_stub_drv gpio_stub_drv = {
# 4454:	ret = bus_register(&gpio_bus_type);
# 4460:	ret = driver_register(&gpio_stub_drv.drv);
```

所以模板按框架型适配：02 画「核心抽象 ↔ 消费者」图而不是「device ↔ driver」图；03 的主轴是「注册 / 配置 / 回调」而不是「传输」；04 讲的是四个互不调用的子框架（cdev / sysfs / irqchip / mmio+regmap）。这条判定分歧本身写进了 08 §2。

**规则 3b 的机械判据在 gpio 上会给出错误答案。** 判定不能只看有没有 `bus_type`，还要看那条总线上有几个 driver、匹配后会不会真的接管设备。

### §1.3 官方文档登记（skill 规则 6，阻塞性前置）

写任何一篇之前读完的文档，共 14 个文件 3120 行：

```bash
wc -l Documentation/driver-api/gpio/*.rst Documentation/admin-guide/gpio/*.rst
```

| 文件 | 行 | 纳入情况 |
|---|---|---|
| `driver-api/gpio/index.rst` | 50 | 只用于确认文档集边界 |
| `driver-api/gpio/intro.rst` | 124 | 00 §1、00 §3 的概念框架 |
| `driver-api/gpio/using-gpio.rst` | 50 | 05 §1 的角色枚举 |
| `driver-api/gpio/consumer.rst` | 468 | 01 消费者 API 全节；**ACPI 章节裁掉**（§2.7） |
| `driver-api/gpio/driver.rst` | 778 | 03 Part B、04 §5（irqchip）；发现 2 处与代码冲突（§6 ③④） |
| `driver-api/gpio/board.rst` | 222 | 05 §6（platform lookup 表）；发现 1 处（§6 ⑤）；ACPI `_DSD` 例子裁掉 |
| `driver-api/gpio/legacy.rst` | 769 | 01 §6（legacy 整数 API）；发现 3 处（§6 ①②⑦） |
| `driver-api/gpio/drivers-on-gpio.rst` | 114 | 00 §7（子系统关系）—— 这一篇是"谁在消费 gpio"的权威清单 |
| `driver-api/gpio/bt8xxgpio.rst` | 62 | **未纳入**：单个 PCI 显卡的 GPIO 后门，与 arm64 无关 |
| `admin-guide/gpio/index.rst` | 20 | 只用于确认边界 |
| `admin-guide/gpio/sysfs.rst` | 167 | 07 §5 全节；发现 1 处（§6 ⑧）+ 1 条交叉验证素材（§4.3） |
| `admin-guide/gpio/gpio-sim.rst` | 134 | 07 §9.1 的 configfs 层级逐字引用 |
| `admin-guide/gpio/gpio-mockup.rst` | 51 | 07 §9.2 |
| `admin-guide/gpio/gpio-aggregator.rst` | 111 | 07 §9.3 的真实命令例子 |

绑定文档单独一组：

```bash
ls Documentation/devicetree/bindings/gpio/ | wc -l      # 100
```

100 个绑定文件里**只详读了 3 个**：`gpio.txt`（通用规则，06 §1 的依据）、`gpio-pca95xx.yaml`（I²C 扩展芯片的代表）、以及 06 举例所用的那个 arm64 SoC 的 gpio 绑定。其余 97 个只在需要确认某个属性名是否为通用属性时 grep。**这是二等证据，写在 §2.2。**

三等证据（`Documentation/`）的风险在本子系统上非常高 —— §6 里 8 条冲突有 6 条是官方文档与代码不符。**凡冲突处一律以代码为准并显式点出。**

---

## §2 方法

### §2.1 详读范围（一等证据的边界）

逐行读过、可以直接给行号的 19 个文件，共 **14170 行**：

```bash
wc -l drivers/gpio/gpiolib.c drivers/gpio/gpiolib.h \
      drivers/gpio/gpiolib-of.c drivers/gpio/gpiolib-of.h \
      drivers/gpio/gpiolib-cdev.c drivers/gpio/gpiolib-cdev.h \
      drivers/gpio/gpiolib-sysfs.c drivers/gpio/gpiolib-sysfs.h \
      drivers/gpio/gpiolib-devres.c drivers/gpio/gpiolib-legacy.c \
      drivers/gpio/gpio-mmio.c drivers/gpio/gpio-regmap.c \
      include/linux/gpio/driver.h include/linux/gpio/consumer.h \
      include/linux/gpio/machine.h include/linux/gpio.h \
      include/uapi/linux/gpio.h include/trace/events/gpio.h \
      include/asm-generic/gpio.h
# 14170 total
```

| 文件 | 行 | 在文档集里的分量 |
|---|---|---|
| `drivers/gpio/gpiolib.c` | 4611 | 主力。03 Part A/B 几乎全部、07 §7/§8 的清单 |
| `drivers/gpio/gpiolib-cdev.c` | 2906 | 04 §6、07 §10、08 §9 |
| `drivers/gpio/gpiolib-of.c` | 1032 | 06 全篇、08 §5 |
| `include/linux/gpio/driver.h` | 833 | 02 的 `gpio_chip` / `gpio_irq_chip` 字段表 |
| `drivers/gpio/gpio-mmio.c` | 822 | 04 §7 |
| `drivers/gpio/gpiolib-sysfs.c` | 820 | 07 §5 全节、08 §3 的绕过反转那条 |
| `include/linux/gpio/consumer.h` | 743 | 01 消费者 API 表；ACPI 块（`:653-713`）裁掉 |
| `include/uapi/linux/gpio.h` | 531 | 04 §6、07 §10.3 的 ioctl 表 |
| `drivers/gpio/gpiolib-devres.c` | 484 | 01 的 devm_* 面、05 §3 |
| `drivers/gpio/gpio-regmap.c` | 342 | 04 §8 |
| `drivers/gpio/gpiolib.h` | 250 | 02 的 `gpio_device` / `gpio_desc`、07 §2.1 的打印宏两族 |
| `include/linux/gpio.h` | 244 | 01 §6（legacy）、§6 ① 的 `irq_to_gpio` 桩 |
| `include/asm-generic/gpio.h` | 172 | 08 §4 的 `ARCH_NR_GPIOS` 512 |
| `include/linux/gpio/machine.h` | 128 | 05 §6 的 lookup 表结构、§6 ⑤ |
| `drivers/gpio/gpiolib-legacy.c` | 113 | 01 §6 |
| `include/trace/events/gpio.h` | 57 | 07 §6 全节 |
| `drivers/gpio/gpiolib-of.h` | 45 | 03 Part A 的 of 侧入口 |
| `include/linux/gpio/machine.h`、`gpiolib-sysfs.h`、`gpiolib-cdev.h` | 128/24/13 | 结构声明 |

### §2.2 只读符号表 / 只读片段的部分（二等证据）

**① 顶层控制器驱动。** 整个目录规模：

```bash
wc -l drivers/gpio/*.c drivers/gpio/*.h | tail -1      # 76313 total
ls drivers/gpio/gpio-*.c | wc -l                       # 171
```

171 个控制器驱动里**详读了 3 个**（skill 规则 4.1 的选法）：

| 驱动 | 为什么选它 | 详读了哪些行 |
|---|---|---|
| `gpio-mockup.c` | 最简实现、无硬件、可立即跑 | 全文 614 行 |
| `gpio-sim.c` | 全功能虚拟提供方（configfs + pull 注入 + hog） | `:35`、`:222-267`、`:326-349` 三段，其余读符号表 |
| `gpio-aggregator.c` | 唯一的"GPIO 之上再造 GPIO"结构 | 只读了 `new_device`/`delete_device` 两个 store 函数 |

**没有详读任何真实 SoC 的 GPIO 控制器驱动。** 04 §7/§8 对 `bgpio_init` / `gpio_regmap_register` 的描述来自这两个通用层自身的代码，不是来自某个具体驱动的验证。写进 §5.2。

**② `drivers/gpio/gpiolib-acpi.c`（1686 行）+ `gpiolib-acpi.h`（104 行）—— 完全未读。** 见 §2.7。

**③ 绑定文档 100 个里的 97 个** —— 只 grep，未读。

**④ 上游 pinctrl 侧的 `pinctrl_gpio_request()` / `pinctrl_find_and_add_gpio_range()` 实现** —— 只确认了符号存在和签名，函数体没读。08 §6 对"pinctrl 会不会拒绝"的描述是**基于接口语义的推断**，写进 §5.3。

**⑤ libgpiod（树外）** —— 07 §10 提到它，但它不在本树里，没有任何一行代码被读过。

### §2.3 可复现的统计命令

**本节所有命令都显式列出文件，不用 `drivers/gpio/*.c` 通配。** 通配会把 171 个控制器驱动一起扫进来，错误码数字会翻 5～10 倍，和 07 §8 的表完全不是一回事。

```bash
# 核心 8 个 .c（后面统称 $C8）
C8="drivers/gpio/gpiolib.c drivers/gpio/gpiolib-of.c drivers/gpio/gpiolib-cdev.c \
    drivers/gpio/gpiolib-sysfs.c drivers/gpio/gpiolib-devres.c \
    drivers/gpio/gpiolib-legacy.c drivers/gpio/gpio-mmio.c drivers/gpio/gpio-regmap.c"
```

**① 对外 API 面（01 §0）**

```bash
for f in $C8; do printf "%-32s %s\n" "$f" "$(grep -c 'EXPORT_SYMBOL' $f)"; done
```

| 文件 | EXPORT_SYMBOL |
|---|---|
| `gpiolib.c` | 82 |
| `gpiolib-devres.c` | 14 |
| `gpiolib-legacy.c` | 5 |
| `gpiolib-of.c` | 4 |
| `gpio-regmap.c` | 4 |
| `gpiolib-sysfs.c` | 3 |
| `gpio-mmio.c` | 1 |
| **`gpiolib-cdev.c`** | **0** |

合计 **113**。**cdev 一个都没导出** —— 它整个是 uapi 面，对内核代码零 API。这个 0 本身是 04 §6 的一条结论。

**② 错误码普查（07 §8）**

两个计数口径必须分清，否则 07 和本篇会看起来矛盾：

```bash
# 口径 A：只数 gpiolib.c（07 §8 各小节用的口径）
grep -how -- '-EINVAL' drivers/gpio/gpiolib.c | wc -l     # 23
# 口径 B：数核心 8 个 .c（本篇 §2.3 表格用的口径）
grep -how -- '-EINVAL' $C8 | wc -l                        # 87
```

核心 8 文件口径的完整表：

| 错误码 | 出现次数 | 其中 gpiolib.c | 其中 cdev |
|---|---|---|---|
| `-EINVAL` | 87 | 23 | 36 |
| `-ENOMEM` | 37 | — | — |
| `-ENOENT` | 25 | — | — |
| `-EFAULT` | 25 | 0 | 25 |
| `-ENODEV` | 14 | 2 | 9 |
| `-EIO` | 12 | 8 | 3 |
| `-EPROBE_DEFER` | 10 | 6 | 0 |
| `-EBUSY` | 8 | 4 | 3 |
| `-ENOTSUPP` | 8 | 7 | 1 |
| `-ENXIO` | 6 | 4 | 2 |
| `-EPERM` | 6 | — | — |
| `-EOPNOTSUPP` | 3 | 0 | 1 |
| `-EAGAIN` | 3 | — | — |
| `-ENODATA` | 2 | 2 | 0 |
| **`-ENOSPC`** | **1** | **1** | 0 |
| **`-EEXIST`** | **1** | **1** | 0 |

三条只能靠这张表看出来的结论，全部落进了正文：

- **`-EFAULT` 25 处全在 cdev，`gpiolib.c` 里一个都没有。** 因为只有 cdev 碰用户态指针。→ 07 §8.0
- **`-ENOSPC` 和 `-EEXIST` 各只有 1 处**，都在 `gpiolib.c`（`:202` / `:1178`）。→ 07 §8.8、§8.9、08 §4
- **`-ENOTSUPP`(8) 和 `-EOPNOTSUPP`(3) 并存且分布不同**：内核内部用前者、面向用户态的 cdev 才用后者。→ 07 §8.3

`grep -how` 数的是**字符串出现次数**，注释和 `== -EINVAL` 比较也算，所以它 ≠「返回点数」。07 §8 各小节里逐行标出了哪几处不是返回点。

**③ WARN / BUG 普查（07 §7）**

```bash
grep -how 'WARN_ON_ONCE\|WARN_ONCE\|WARN_ON\|WARN\|BUG_ON\|BUG' $C8 | sort | uniq -c
#      2 WARN
#     21 WARN_ON
```

**零 `BUG_ON`、零 `BUG()`、零 `WARN_ONCE`、零 `WARN_ON_ONCE`。** 也就是说 gpio 核心从不主动 panic，而且**每一处告警都会重复打印**（没有 `_once` 抑制）。这两条都是 07 §7.4 的结论，而且直接决定了 07 §7.1 那条 的分量：8 处 `WARN_ON(can_sleep)` 会反复刷屏，不能当噪声忽略。

**④ 调试打印分布（07 §3）**

```bash
grep -how 'gpiod_dbg\|chip_dbg\|pr_debug_ratelimited\|pr_debug\|dev_dbg' $C8 | sort | uniq -c
#      6 chip_dbg
#     13 dev_dbg
#      4 gpiod_dbg
#      7 pr_debug
#      4 pr_debug_ratelimited
```

**口径对账（防止 07 §3 与本篇看起来矛盾）**：

| 分组 | 组成 | 数 |
|---|---|---|
| dev 族（可按文件/行过滤） | `chip_dbg` 6 + `dev_dbg` 13 | **19** |
| pr 族（普通） | `gpiod_dbg` 4 + `pr_debug` 7 | **11** |
| 07 §3 说的「总共 30 条」 | 19 + 11 | **30** |
| pr 族（限速变体，07 §2.3 单列） | `pr_debug_ratelimited` 4 | 4 |
| 本篇 grep 的原始总数 | 30 + 4 | **34** |

逐文件分布，以及 07 §3 那条「三个文件零调试打印」的依据：

```bash
for f in $C8; do printf "%-32s %s\n" "$f" \
  "$(grep -how 'gpiod_dbg\|chip_dbg\|pr_debug_ratelimited\|pr_debug\|dev_dbg' $f | wc -l)"; done
# gpiolib.c 17 · gpiolib-cdev.c 9 · gpiolib-sysfs.c 6 · gpiolib-of.c 2
# gpiolib-devres.c 0 · gpiolib-legacy.c 0 · gpio-mmio.c 0 · gpio-regmap.c 0
```

**⑤ 提供方能力普查（04）**

```bash
grep -rlw 'bgpio_init' drivers/ | wc -l                                  # 39
grep -rlw 'gpio_regmap_register\|devm_gpio_regmap_register' drivers/ | wc -l   # 4
grep -rlw 'GPIOCHIP_IRQ_RESOURCE_HELPERS' drivers/ | wc -l               # 41
grep -rn 'gpiochip_irqchip_add_domain' drivers/                          # 见下
```

**这四个数都要减掉"定义自己那一份"才是消费者数**（skill 规则 30）：

| 符号 | 含该词的文件 | 定义所在 | **真实使用者** |
|---|---|---|---|
| `bgpio_init` | 39 | `gpio-mmio.c:599` + `driver.h:681` 声明 | **38** 个驱动 |
| `gpio_regmap_register` 族 | 4 | `gpio-regmap.c` | **3** 个驱动 |
| `GPIOCHIP_IRQ_RESOURCE_HELPERS` | 41 | `driver.h:640`（唯一定义） | **40** 个驱动 |
| `gpiochip_irqchip_add_domain` | — | `gpiolib.c:1692`、`driver.h:707/710` | **1** 处：`gpio-regmap.c:279` |

最后一行是 04 §8 那条全称断言的全部依据：**整棵树只有 `gpio-regmap.c` 调用 `gpiochip_irqchip_add_domain()`**。

**⑥ pinctrl 桥接的使用面（08 §6）**

```bash
grep -rn 'add_pin_ranges[[:space:]]*=' --include=*.c --include=*.h . | grep -v '^./output'
```

10 个文件赋值 `add_pin_ranges`，**其中只有 2 个在 `drivers/gpio/`**（`gpio-tegra186.c`、`gpio-merrifield.c`），另外 8 个全在 `drivers/pinctrl/`。这条恰好印证 08 §6 的误导点三：**是 GPIO 侧把地盘登记进 pinctrl，而实现这个回调的主力反而住在 pinctrl 目录下。** 只在 `drivers/gpio/` 里 grep 会漏掉 80%。

**⑦ DTS 举例基数（06）**

```bash
grep -rl 'gpio-ranges'          arch/arm64/boot/dts/ | wc -l   # 104
grep -rl 'gpio-line-names'      arch/arm64/boot/dts/ | wc -l   #  94
grep -rl 'gpio-hog'             arch/arm64/boot/dts/ | wc -l   #  49
grep -rl 'gpio-reserved-ranges' arch/arm64/boot/dts/ | wc -l   #  41
```

06 的每个例子都取自这四组里的真实节点，读者可以直接 `diff`。

**⑧ tracepoint（07 §6）**

```bash
grep -c 'TRACE_EVENT' include/trace/events/gpio.h                    # 2
grep -rn 'trace_gpio_value\|trace_gpio_direction' drivers/gpio/ | wc -l   # 9
```

**恰好 2 个事件、恰好 9 个调用点，全部在 `gpiolib.c`。** 这是 07 §6.2 那张表和 08 §3 那条 的依据。

**⑨ 打印宏的唯一性（07 §2.5、08 §1）**

```bash
grep -rn 'dev_crit' drivers/gpio/
# gpiolib.h:240   ← 宏定义 chip_crit
# gpiolib.c:906   ← 全子系统唯一一处实际调用
```

**⑩ Kconfig / Makefile 规模（01 §0、07 §1）**

```bash
wc -l drivers/gpio/Kconfig drivers/gpio/Makefile      # 1715 / 189
```

07 §1.1 那张 11 行表覆盖的是 `Kconfig` 前 110 行（核心开关区），后面 1600 行全是 171 个控制器驱动的 `config GPIO_xxx`。

**⑪ 源码自陈设计债（03 Part C）**

```bash
grep -n 'FIXME\|TODO\|REVISIT\|XXX\|HACK' $C8 drivers/gpio/gpiolib.h include/linux/gpio/driver.h
```

**11 条**，全部落进了 03 Part C：

| 位置 | 类型 | 内容摘要 |
|---|---|---|
| `gpiolib.c:647` | TODO | `remove chip->owner` |
| `gpiolib.c:701` | TODO | 全局编号池本身被标为待去除（→ 08 §4） |
| `gpiolib.c:717` | TODO | 不该把分配到的 base 反写回 `gc->base` |
| `gpiolib.c:882` | FIXME | legacy sysfs 处理是否该搬到 `gpio_device` |
| `gpiolib.c:1368` | TODO | activate/deactivate 应移到层级 domain 下 |
| `gpiolib.c:2644` | REVISIT | 调试用的插桩建议 |
| `gpiolib.c:4136` | FIXME | `Make this more sane and safe` |
| `gpiolib-of.c:56` | FIXME | 摆脱外部 legacy 使用者 |
| `gpiolib-sysfs.c:179` | FIXME | 该在 `irq_request_resources` 回调里做（→ 07 §5.2.2 引用了这一条） |
| `gpiolib-sysfs.c:804` | TODO | 这里让出 `gpio_lock` 的原因 |
| `gpiolib-cdev.c:2397` | FIXME | 取这个信息的方式有 race |

**`gpiolib.c:701` 那条 TODO 是全篇最有分量的一条**：内核自己承认全局 GPIO 编号池是要去掉的东西。08 §4 的整节论证以它为锚。

### §2.4 时间线次序的验证方法（03 Part A 的依据）

03 Part A 排的每一步，判据只接受两种：

- **(a)** X 的函数体里有一行调用 Y；
- **(b)** X 和 Y 被同一个上层函数 Z 顺序调用，且 Z 里 X 那行在 Y 那行之前。

「按直觉应该先做 X」不是证据。gpio 上真实吃过的一个亏见 §4.2。

### §2.5 全称断言的验证方法（skill 规则 19）

文档里出现「全树唯一」「一个都没有」「只有 X 这么用」时，必须在**整棵树**上 grep，不能只在 `drivers/gpio/` 里。本文档集的全称断言清单及其验证命令：

| 断言 | 出处 | 验证命令要点 |
|---|---|---|
| `gpiochip_irqchip_add_domain()` 只有 1 个调用点 | 04 §8 | 全树 grep，排除定义和声明 |
| `GPIOCHIP_IRQ_RESOURCE_HELPERS` 只在 `driver.h:640` 定义 | 04 §5 | 全树 grep 该词，看有几处 `#define` |
| 恰好 2 个 tracepoint、9 个调用点、全在 raw 层 | 07 §6、08 §3 | `grep -c TRACE_EVENT` + 逐个调用点 `sed` 确认在 `*_commit` 侧 |
| `dev_crit` 全子系统 1 处 | 07 §2.5、08 §1 | `grep -rn dev_crit drivers/gpio/` |
| 零 `BUG_ON`/`BUG()`/`WARN_ONCE` | 07 §7.4 | `uniq -c` 那条命令的输出里没有这几个词 |
| `-ENOSPC` / `-EEXIST` 各 1 处 | 07 §8.8/§8.9 | 见 §2.3 ② |
| `gpiolib-devres.c` / `gpio-mmio.c` / `gpio-regmap.c` 零调试打印 | 07 §3 | 见 §2.3 ④ |
| `gpio-virtuser.c` 在 6.1 不存在 | 07 §9 | `ls drivers/gpio/gpio-virtuser.c` → 无此文件 |
| `gpio-controller` 属性在 `drivers/gpio/` 零读者 | 06 §2.2 | 全树 13 处，`drivers/gpio/` 0 处 |
| `of_mm_gpiochip_add_data()` 的 4 个使用者全是 powerpc/MIPS/nios2 | 04 §7 | 全树 grep 后逐个看路径 |

### §2.6 索引工具的定位

`codebase-memory-mcp`（cbm）只用于**导航和找候选**，一条论断都不靠它取证。gpio 上它的两个盲区都真实命中了：

- **宏改名链**：`CONFIG_DEBUG_GPIO` → `Makefile` 的 `-DDEBUG` → `gpiolib.c:50-54` 的 `extra_checks`。中间两跳不含 `CONFIG_` 字符串，索引里查 `DEBUG_GPIO` 完全看不到它在哪生效。详见 §4.1。
- **函数指针间接调用**：`gc->get` / `gc->set` / `gc->dbg_show` / `add_pin_ranges` 这些回调的实际实现分散在 171 个驱动里，索引给不出调用关系。04 的每张能力表都是 grep 出来的。

### §2.7 arm64-only 裁掉了什么

用户明确要求：只按 arm64 分析，ACPI 相关不写。**裁掉的内容显式列在这里，避免读者当成遗漏：**

| 裁掉的对象 | 行数 | 说明 |
|---|---|---|
| `drivers/gpio/gpiolib-acpi.c` | 1686 | 完全未读。ACPI 侧的 GPIO 查找、`_DSD` 解析、event handler 全在这里 |
| `drivers/gpio/gpiolib-acpi.h` | 104 | 同上 |
| `include/linux/gpio/consumer.h:653-713` 的 `acpi_gpio_*` 块 | 61 | 01 的 API 表不包含这一族 |
| `Documentation/firmware-guide/acpi/gpio-properties.rst` | — | 未读 |
| `Documentation/driver-api/gpio/consumer.rst` 的 ACPI 章节 | — | 01 未纳入 |
| `Documentation/driver-api/gpio/board.rst:66-101` 的 ACPI `_DSD` 例子 | 36 | 05 §6 只讲 DT 和 platform lookup 两条路 |
| `drivers/gpio/Kconfig:42` 的 `GPIO_ACPI` | 1 | 07 §1.1 的表里保留了这一行**但标注"本文档集不涉及"**，因为删掉会让表和真实 Kconfig 对不上 |
| x86 / powerpc / arm32 专有路径 | — | 08 §4 提到 `ARCH_NR_GPIOS` 时只按 arm64 的 512 默认值讲 |

**有一处 ACPI 痕迹故意留着**：`gpiolib.h:80-81` 那条注释 `/* gpio suffixes used for ACPI and device tree lookup */` 在 08 §5 里被逐字引用。理由是删掉注释会让读者以为 `gpio_suffixes[]` 是 DT 专用的，而它实际是两条路共用的 —— **这个共用关系本身是 DT 侧行为的一部分**（`gpiolib-acpi.c:922/1431` 用的是同一个数组）。

另一处：07 §2.2 的日志可见性表在数 `_once`/`_ratelimited` 变体时，命令显式加了 `grep -v gpiolib-acpi`，并在正文标注了这一点。**不标注的话，读者自己重跑会得到不同的数字。**

---

## §3 逐篇取证表

每篇列：**核心论断 → 出处**，以及**自审这一轮改掉了什么**。自审动作是把全篇的 `文件:行号` 逐个 `sed` 打开核对，不抽样（skill 规则 19）。

### §3.1 `00-overview.md`（723 行）

| 论断 | 出处 |
|---|---|
| gpio 是「框架型 + 退化总线」 | §1.2 的判定；`gpiolib.c:61`/`:4443-4447` |
| 硬件背景章（§2）：推挽 / 开漏 / 开源 / 上下拉 / 边沿 vs 电平触发 | 无源码依据 —— 这是**通用数字电路知识**，写进 §5.3 |
| 四层值模型 | `gpiolib.c:3114-3124` 与 `:2825-2836` 的反转位置差 |
| 三套坐标系（全局号 / chip+offset / 行名） | `:187`、`gpiolib-sysfs.c:388`、`gpiolib.c:4573-4575` |
| 端到端路径（DTS → `gpiod_get` → 寄存器） | 03 Part A 的压缩版，行号与 03 一致 |

**自审改动**：§2 硬件章原本写了 I²C 扩展芯片的时序，与 gpio 抽象无关，删掉；改为集中讲「开漏为什么在 gpio 层表现为改方向」，与 08 §3 的误导点二对齐。

### §3.2 `01-api.md`（626 行）

| 论断 | 出处 |
|---|---|
| 对外 API 合计 113 处 `EXPORT_SYMBOL` | §2.3 ① |
| cdev 零导出 | §2.3 ① |
| legacy 整数 API **全部是 raw 语义** | `gpiolib-legacy.c` 全文 + `include/linux/gpio.h` |
| `-ENOTSUPP` vs `-EOPNOTSUPP` 的分工 | `include/linux/errno.h:8-13` 逐字引用 |

**自审改动**：3 处裸 `linux/gpio.h:` 改为 `include/linux/gpio.h:`（`:514`、`:521`、`:528`）—— `check_line_refs.py` 报 AMBIGUOUS，因为树里有多个同名 `gpio.h`（`include/linux/gpio.h`、`include/asm-generic/gpio.h`、`arch/*/include/asm/gpio.h`）。**这正是 skill 规则 30「同名头文件必须写全路径」的一次真实命中。**

### §3.3 `02-datastructures.md`（619 行）

| 论断 | 出处 |
|---|---|
| §1 先分清五组关系，逐条否掉错误类比：`gpio_chip` ↔ `gpio_device` **不是** device ↔ driver | `gpiolib.c:61`、`:619`、`:4443-4447`（08 §2 展开） |
| `gpio_device` 内嵌 `struct device` 和 `struct cdev` | `gpiolib.h:55`、`:56` |
| `gpio_desc` 属于 `gpio_device` 而非 `gpio_chip` | `gpiolib.h:148` + 分配点 |
| 两条总线的唯一缝合点是 `dev.parent` | `gpiolib.c:620` |

**自审改动**：无事实改动。

### §3.4 `03-internals.md`（1660 行）

| 论断 | 出处 |
|---|---|
| Part A 注册时间线 12 步 | 每一步都按 §2.4 的 (a)/(b) 判据验证 |
| `gpiochip_add_pin_ranges()` 在 `:778` 被调用（注册中段） | `gpiolib.c:477`/`:778` |
| 拆除路径两处 `gpiochip_remove_pin_ranges()` | `:820`（失败回滚）/`:890`（正常 remove） |
| `gpiochip_remove()` 只 `dev_crit` 不阻拦 | `:906-907` → `:914` |
| Part C 设计债 11 条 | §2.3 ⑪ |

**自审改动**：`§A2.9` 这个章节号不存在（实际编号是 A2、A2.2、A2.4、A2.6、A2.8、A2.10、A2.11、A2.13）—— 由 `check_xrefs.py` 在 `05-usage.md:793` 处报出，见 §7.1。

### §3.5 `04-extensions.md`（904 行）

| 论断 | 出处 |
|---|---|
| 四个子框架互不调用（cdev / sysfs / irqchip / mmio+regmap） | 各自的文件边界 + 零交叉调用（grep 验证） |
| `gpiochip_irqchip_add_domain()` 全树 1 个调用点 | §2.3 ⑤ |
| `bgpio_init` 38 个使用者 | §2.3 ⑤（39 减去定义文件） |
| `of_mm_gpiochip_add_data()` 4 个使用者全非 arm64 | §2.5 |
| arm64 分诊表：cdev/irqchip/mmio 高度相关，sysfs 仅兼容，legacy 基本不碰 | 结合 §2.3 ⑦ 的 DTS 基数 |

**自审改动**：原先写「10 个驱动实现 `add_pin_ranges`」而未说明分布，改为「10 个里 8 个在 `drivers/pinctrl/`」—— 见 §2.3 ⑥，这条数字对但结论会误导。

### §3.6 `05-usage.md`（1032 行）

| 论断 | 出处 |
|---|---|
| 篇首声明「骨架未编译」 | 见 §5.1 |
| 五个角色：消费者 / 提供方 / irqchip 提供方 / 板级 lookup / 用户态 | `using-gpio.rst` 的角色枚举 + 代码验证 |
| `pinctrl_gpio_request()` 桥接用全局编号 | `gpiolib.c:1750` |
| pin range 名字拼错只有 `chip_err "could not create pin range"` | `:1880` |

**自审改动**：① 一处指向 03 篇「§A2.9」的引用 —— 该编号在 03 里并不存在，改为 §A2.10 并重写了那句话；② `lsgpio.c` / `gpio-hammer.c` / `gpio-event-mon.c` / `gpio-watch.c` 四个裸文件名改为 `tools/gpio/…` 全路径 —— `check_line_refs.py` 报 UNRESOLVED，见 §7.2。

### §3.7 `06-devicetree.md`（1021 行）

| 论断 | 出处 |
|---|---|
| 核心真正会读的属性总表（含"写在哪个节点"一列） | 每条都有 `gpiolib-of.c` 里定位到行的消费代码 |
| `gpio-controller` 在 `drivers/gpio/` **零读者** | §2.5（全树 13 处，本目录 0 处） |
| `gpio-map` 在 arm64 DTS **零使用者** | §2.5 |
| `reset-gpios` 的名字构造过程 | `gpiolib.h:81` + `gpiolib-of.c:513-520`（08 §5 展开） |
| 5 条 quirk | `gpiolib-of.c:495-502` |
| DTS 举例全部取自 in-tree 真实节点 | §2.3 ⑦ 的四组基数 |

**自审改动**：`char prop_name[32]` 早期记为 `:506`，`sed` 打开后是 **`:507`**。这是典型的"行号在范围内但差一行"错误，三个脚本都抓不到，只能逐个打开看。

### §3.8 `07-debug.md`（1328 行）

| 论断 | 出处 |
|---|---|
| §1.2 `CONFIG_DEBUG_GPIO` 四跳宏改名链 | §4.1（本篇最重要的一条交叉验证） |
| 两族打印宏前缀不同、可过滤性不同 | `gpiolib.h:222-224`（`gpiod_*`）/ `:241-242`（`chip_*`） |
| 30 条调试打印 + 4 条限速变体 | §2.3 ④ 的口径对账表 |
| debugfs `gpio` 是 **0444 只读**，且读它真的会碰硬件 | `gpiolib.c:4603-4609`、`:4500`、`:4502` |
| 值列的 `?` 是真实的负数读取结果 | `:4508` 的三目表达式 |
| sysfs 三个面 + `direction_store` 绕过极性 | `gpiolib-sysfs.c:77-98` |
| 2 个 tracepoint、无 `TP_CONDITION`、9 个调用点全在 raw 层 | §2.5 |
| 21 处 `WARN_ON` + 2 处 `WARN`，零 `BUG_ON`/`WARN_ONCE` | §2.3 ③ |
| 错误码分诊 10 组 | §2.3 ② |
| `gpio-virtuser.c` 在 6.1 不存在 | §2.5 |
| `tools/gpio/` 四个工具，`gpio-watch.c` 没有 usage 函数 | 逐个文件读过 |

**自审改动 5 处**（全部由逐行 `sed` 抓出）：

| 原写法 | 实际 |
|---|---|
| `gpio_sysfs_set_active_low` @ `:283` | **`:282`** |
| `gpiod_dbg` "unavailable" @ `:586-589`，块 `:584-591` | **`:587-590`**，块 **`:584-593`** |
| `gpiod_export()` 的 `-ENOMEM` @ `:595` | **`:598`**，块 `:596-599` |
| `gpiochip_enable_irq` 注释 @ `:3371-3374` | **`:3370-3373`**，`WARN_ON` 在 `:3374-3375` |
| §7.3 代码块标 `:3355-3379` 却只引了 disable 片段 | 改为完整函数体，标 **`:3354-3361`** |

**自审改动（语义类，比行号更严重）2 处**：

- `-EEXIST` @ `:1178` 原写成"返回的错误码"，实际是**被吞掉的**（`ret = 0`）。重写 §8.9，改为逐字引用 `:1172-1179` 并指向真正会说话的 `chip_err` @ `:1181-1183`。
- 一句引文被归给了 `sysfs.rst`，实际出自**源码注释** `gpiolib-sysfs.c:44-45`。重写整段，见 §4.3 —— 这次重写的结论比原来的更强。

### §3.9 `08-feynman.md`（610 行）

| 主题 | 类比 | 关键行号 |
|---|---|---|
| §1 三个结构体 | 写字楼门禁：厂商手册 / 墙上铭牌 / 每门登记条 | `driver.h:415`、`gpiolib.h:53`/`:148`、`gpiolib.c:619-620` |
| §2 退化总线 | 只发身份证的户籍系统 | `:61`、`:619`、`:4443-4447`、`:4454`、`:4460` |
| §3 `active_low` 反转层 | 说反话的传声筒 | `:3116-3117`（commit **前**）vs `:2833-2834`（commit **后**） |
| §4 全局编号 | 全城统一门牌号 | `:187`、`:201-202`、`include/asm-generic/gpio.h:27-33`、`:292` |
| §5 `"reset"` → `reset-gpios` | 只会拼后缀的档案员 | `gpiolib.h:81`、`gpiolib-of.c:507`/`:513-520`/`:495-502`/`:392` |
| §6 一个 pad 两个子系统 | 会议室的后勤部 vs 行政部 | `gpiolib.c:1750`、`:1855`、`:1876-1884` |
| §7 兼任中断控制器 | 兼着转接台的前台 | `:1572`/`:1574`、`:1419-1424`、`:1324-1328`、`:1609-1611` |
| §8 `_cansleep` 不能自动判断 | "这件事能不能现在办" | `:2825-2827`、`:3139-3141`、`:50-54`、`:3452` 等 8 处 |
| §9 关 fd 线就还回去 | 纸质登记本 vs 刷卡会话 | `gpiolib-cdev.c:1714-1723`/`:1730`/`:1754`，对比 `gpiolib-sysfs.c:475`/`:509` |

九个主题里 **§6 是「本子系统最容易被类比错的那组关系」**（skill 规则要求必含）。它的三个误导点分别否掉了：「每个控制器都要向 pinctrl 报备」（其实可选）、「两个子系统对等」（pinctrl 是被动方）、「gpio 依赖 pinctrl」（方向反了）。

**自审改动**（规则 23：费曼篇不豁免取证）：

| 原写法 | 实际 |
|---|---|
| `linereq_free()` 循环 `:1712-1722` | **`:1714-1723`** |
| `if (!line->desc) continue;` `:1715-1716` | **`:1716-1717`** |
| `gpiod_get_value()` 代码块标 `:2821-2827`（实际引的是 2825-2827） | **`:2825-2827`** |
| `gpiod_set_value()` 代码块标 `:3137-3141`（实际引的是 3139-3141） | **`:3139-3141`** |
| `gpiod_get_value()` 函数跨度 `:2821-2837` | **`:2821-2836`**（`:2837` 是 `EXPORT_SYMBOL_GPL`） |
| ASCII 图里 `gc->can_sheep` | 拼写错，改 `gc->can_sleep` |

---

## §4 交叉验证实例：初始假设被推翻的地方

### §4.1 `CONFIG_DEBUG_GPIO` ——「grep 不到就是死开关」是错的

**初始假设**：`grep -rn 'CONFIG_DEBUG_GPIO' drivers/gpio/` 只有 `Kconfig` 和 `Makefile` 两处命中，`.c` 文件里一处都没有 → 这是个**没人用的死开关**，07 应该点出来，99 §6 记一条「文档里还在提但代码里已失效」。

**验证过程**发现它是活的，只是中间隔了**两跳不含 `CONFIG_` 字符串的改名**：

```
第 1 跳   drivers/gpio/Kconfig:50    config DEBUG_GPIO
                                     depends on DEBUG_KERNEL   (:52)
              ↓
第 2 跳   drivers/gpio/Makefile:4    ccflags-$(CONFIG_DEBUG_GPIO) += -DDEBUG
              ↓                      名字在这里从 CONFIG_DEBUG_GPIO 变成了 DEBUG
第 3 跳   drivers/gpio/gpiolib.c:50  #ifdef DEBUG
                             :51     #define extra_checks 1
                             :53     #define extra_checks 0
              ↓                      名字在这里又从 DEBUG 变成了 extra_checks
第 4 跳   9 处使用点               :2090 WARN_ON(extra_checks) 等
                                     :3452/:3471/:3502/:3528/:3549/:3567/:3590/:3632
                                       might_sleep_if(extra_checks)
```

**结论翻转**：这条候选冲突（原编号 #11）**已撤销**。07 §1.2 现在把这条链完整画了出来。

而且验证过程还带出一条原本完全没想到的**副作用**：`-DDEBUG` 是加在 `ccflags-` 上的，也就是**整个 `drivers/gpio/` 目录级别**。而 `-DDEBUG` 会让 `pr_debug`/`dev_dbg` 编译成无条件打印，**从而绕过 dynamic debug 的动态开关**。所以：

> **`CONFIG_DEBUG_GPIO=y` 和 dynamic debug 是互斥的两种调试模式，不能叠加使用。**

这条写进了 07 §1.2 的 和 07 §11.4 的最小 Kconfig 组合表。**如果当初按初始假设把它当死开关处理，这两条都不会存在。**

**方法论收获**：「grep 不到使用点」有两种可能 —— 真的没人用，或者**名字在中间被改过**。判据是先看 `Makefile` 有没有把它变成 `ccflags`/`-D` 宏。这一条已回写进 skill 的索引盲区说明。

### §4.2 全局编号是**从高往低**分配的 —— 直觉的方向是反的

**初始假设**：GPIO 全局编号池从 0 开始往上分配，第一个注册的控制器拿 0..ngpio-1。这符合"编号池"的直觉，也符合 `gpiochip0` 这个命名给人的印象。

**实际代码**（`gpiolib.c:187` 起）：

```c
	int base = ARCH_NR_GPIOS - ngpio;

	list_for_each_entry_reverse(gdev, &gpio_devices, list) {
		/* found a free space? */
		if (gdev->base + gdev->ngpio <= base)
			break;
		/* nope, check the space right before the chip */
		base = gdev->base - ngpio;
	}
```

**起点是 `ARCH_NR_GPIOS - ngpio`（arm64 上是 `512 - ngpio`），然后往下走。**

**结论翻转**：**最早注册的控制器拿到的是最高的编号**。一个有两个控制器（各 32 线）的板子，先注册的那个是 448..479，后注册的是 416..447。08 §4 的误导点二就是这一条，如果按初始假设写会把整节的因果讲反。

连带修正一处：失败时先 `pr_err("%s: cannot find free range\n", __func__)`（`:201`）再 `return -ENOSPC`（`:202`）。也就是说 **`-ENOSPC` 这条全树唯一的错误码是"会说话"的**，日志里能看到，不用靠返回值推。07 §8.8 据此从"静默失败"改成了"有明确日志"。

而且 `:187` 这个函数上方 `:701` 那条 TODO（§2.3 ⑪）明确写着这套全局 base 分配是要去掉的东西 —— **内核自己的自陈债成了 08 §4「这是个错误的抽象」这一论断的最硬证据**，比任何外部评论都强。

### §4.3 `active_low` 的官方描述「准确但不够用」—— 一次比原结论更强的重写

**初始写法**（07 §5.2 早期稿）：断言官方文档和源码注释都**没有说清** `active_low` 的作用范围，读者因此会踩坑。

**验证过程**先抓到一个归属错误：我把 "may also be written as high or low, initializing output value as specified" 归给了 `Documentation/admin-guide/gpio/sysfs.rst`，实际它出自**源码注释** `gpiolib-sysfs.c:44-45`；rst 的措辞完全不同（`sysfs.rst:77-79`）。

**修正后逐字读两边，结论反转成一个更精确的版本**：

| 来源 | 原文范围 | 措辞是否准确 |
|---|---|---|
| `gpiolib-sysfs.c:53-54` | `/active_low` → `* configures polarity of /value` | ✅ 准确 —— 严格限定在 `value` 上 |
| `sysfs.rst:109-112` | "Write any nonzero value to invert **the value attribute** both for reading and writing" | ✅ 准确 —— 同样严格限定 |
| `gpiolib-sysfs.c:56-57` | `* also affects existing and subsequent "falling" and "rising" /edge configuration` | ✅ 而且**主动**说了 edge 的联动 |
| `sysfs.rst:112-115` | 同样写了 edge 的联动 | ✅ |

**所以两边都不是"写错"，也不是"漏写 edge"** —— 它们是**准确但不足以让读者推出后果**：既然 `active_low` 只管 `value`，那么 `echo high > direction`（走 `gpiod_direction_output_raw()`，`gpiolib-sysfs.c:87`/`:89`）就**绕过了极性反转**，而 `echo 1 > value`（走 `gpiod_set_value_cansleep()`，`:135`）不绕。**同一个目录下两个 setter 处在不同的语义层，而这一点没有任何文档提到。**

**结论**：07 §5.2.1 从"文档没说清"改成"文档准确但推不出后果"，并新增一张 5 行 PAD 结果表把四种写法的实际电平列出来。**这是本文档集里唯一一处"自审把结论改强了而不是改弱了"的地方。**

### §4.4 `Documentation/devicetree/bindings/gpio/gpio.txt` 的引用是**有效的** —— 一条被证伪的冲突候选

**初始假设**：`gpiochip_add_pin_range()` 的 kerneldoc（`gpiolib.c:1852-1795` 两处）指向 `Documentation/devicetree/bindings/gpio/gpio.txt` 的 "Section 2.1"。6.1 时代绑定文档大批转 YAML，`.txt` 大概率已被删除或改名 → 记一条"悬空文档引用"。

**验证**：

```bash
ls Documentation/devicetree/bindings/gpio/gpio.txt        # 文件存在
grep -n '^2\.' Documentation/devicetree/bindings/gpio/gpio.txt
# 231:2.1) gpio- and pin-controller interaction
# 239:2.2) Ordinary (numerical) GPIO ranges
# 284:2.3) GPIO ranges from named pin groups
```

**文件在、Section 2.1 在、而且讲的正是 `gpio-ranges`** —— 和 kerneldoc 的描述完全对得上。

**结论**：候选冲突 #10 **撤销**。这条留在这里是因为它示范了一个反例（skill 规则 19：反例要老实留着）—— **"6.1 的绑定文档都转 YAML 了"这个先验在 gpio 上不成立**，`Documentation/devicetree/bindings/gpio/` 下 100 个文件里 `.txt` 仍然存在且被代码引用。

### §4.5 `of_convert_gpio_flags()` 的注释确实写错了类型 —— 唯一一条被确认的注释级冲突

```bash
sed -n '292,294p' drivers/gpio/gpiolib-of.c
# /* Converts gpio_lookup_flags into bitmask of GPIO_* values */
# static unsigned long of_convert_gpio_flags(enum of_gpio_flags flags)
```

注释说输入是 `gpio_lookup_flags`，函数签名说输入是 `enum of_gpio_flags`。**两者是不同的枚举**（前者在 `include/linux/gpio/machine.h:8-21`，是板级 lookup 表用的；后者是 DT 侧的）。函数体做的是 `of_gpio_flags` → `gpio_lookup_flags` 的转换，**注释把方向写反了**。

精确定性：这是一条**普通行注释**（`/* … */`），不是 kerneldoc（没有 `/**`），所以不会进 `htmldocs`，影响面只限于读代码的人。§6 ⑩ 按这个定性记录。

---

## §5 明确未验证项（四层分层）

> 这一节的价值不低于前面九篇 —— 它决定读者该在哪里加倍小心。

### §5.1 第一层：未编译、未运行

**整套文档集没有编译过一行代码，没有在任何硬件或虚拟机上运行过任何命令。** 具体含义：

| 内容 | 状态 |
|---|---|
| 05 的全部代码骨架（消费者 / 提供方 / irqchip 提供方 / 板级 lookup） | **未编译**。篇首已声明。语义按源码推导，但拼写、头文件缺漏、参数顺序都可能有错 |
| 07 的全部 shell 命令（`echo … > export`、`cat /sys/kernel/debug/gpio`、`trace-cmd`、gpio-sim 的 configfs 序列） | **未运行**。命令形式取自官方文档的真实例子或源码里的格式串 |
| 07 §4 的 debugfs 输出样例 | **不是真实输出**，是按 `gpiolib.c:4505-4510` 和 `:4568-4585` 的格式串**手工推导**的。列宽、`?` 的出现位置、`, can sleep` 的位置都按格式串对齐，但没有一行是从真机 `cat` 出来的 |
| 07 §6 的 tracepoint 输出样例 | 同上，按 `include/trace/events/gpio.h:28-29`/`:50-51` 的 `TP_printk` 推导 |
| 07 §10 的 `lsgpio` 输出格式 | 按 `tools/gpio/lsgpio.c:109-155` 的 `printf` 推导 |
| 06 的 DTS 片段 | 节点取自 in-tree 真实 DTS（可 `diff`），但**没有编译过 dtb**，也没有启动过 |
| 三个虚拟提供方（gpio-sim / gpio-mockup / gpio-aggregator） | **一个都没跑过**。07 §9 的 configfs 层级逐字取自 `gpio-sim.rst:20-70` |

**凡是"示例输出"，读者第一次在真机上跑出来和文档不一致时，请相信真机。**

### §5.2 第二层：只读了符号表 / 只读了片段

| 对象 | 只读到什么程度 | 影响哪些论断 |
|---|---|---|
| 171 个控制器驱动中的 168 个 | 只 grep 符号，未读函数体 | 04 §7/§8 的能力表是"通用层提供什么"，不是"某个驱动实际怎么用" |
| `pinctrl_gpio_request()` / `pinctrl_gpio_free()` / `pinctrl_find_and_add_gpio_range()` 的实现 | 只确认符号存在和签名 | 08 §6「pinctrl 会不会拒绝」是接口语义推断 → §5.3 |
| `irq_domain_*` 系列（`irq_domain_is_hierarchy`、`irq_domain_is_msi`、`irq_set_lockdep_class` 等） | 只确认符号存在 | 04 §5、08 §7 对两条 irqchip 路径的描述，止步于 gpio 侧的调用点 |
| `Documentation/devicetree/bindings/gpio/` 100 个文件中的 97 个 | 只 grep | 06 §1 的三方分工判定 |
| `drivers/pinctrl/` 里 8 个实现 `add_pin_ranges` 的驱动 | 只知道文件名 | §2.3 ⑥ 的分布结论成立，但没看它们各自怎么实现 |
| libgpiod | **零** —— 树外项目 | 07 §10.2 只说"它在树外、版本自带工具不等于 libgpiod" |

### §5.3 第三层：推断而非观察

以下结论是从代码结构**推出来的**，不是观察到的。它们最可能出错：

| 推断 | 依据 | 为什么只能算推断 |
|---|---|---|
| 「pinctrl 不会主动阻止 gpio 操作，只在 `pinctrl_gpio_request()` 被调用时才有机会拒绝」（08 §6 误导点二） | gpio 侧只有这一个调用点 | 没读 pinctrl 侧实现；pinctrl 内部可能有别的拦截机制 |
| 「`gpio-ranges` 没写全时 gpio 层会照样驱动一个仍复用给 UART 的 pad」（08 §6） | 缺 range 时 `pinctrl_gpio_request()` 拿不到映射 | **没有实机验证过**。真实 SoC 上可能被 pinmux 硬件本身挡住 |
| 「`CONFIG_DEBUG_GPIO=y` 会让 `drivers/gpio/` 的 dynamic debug 失效」（07 §1.2、§4.1） | `-DDEBUG` 加在 `ccflags-` 上是目录级的；`pr_debug` 在 `DEBUG` 定义时走无条件路径 | 推自 `Makefile:4` + `include/linux/printk.h` 的宏结构，**没有实际编译两种配置对比过** |
| 00 §2 的硬件背景（推挽/开漏/上下拉/触发方式） | 通用数字电路知识 | 与本树源码无关，属外部知识 |
| 「最早注册的控制器拿最高编号」（08 §4，§4.2） | `gpiolib.c:187` 的 `ARCH_NR_GPIOS - ngpio` 起点 + 反向遍历 | 逻辑推导可靠，但**没在真机上 `cat /sys/kernel/debug/gpio` 核对过顺序** |
| 「`gpio_stub_drv` 的存在是为了让 driver core 的绑定流程闭合」（08 §2 误导点一） | stub 的形状 + driver core 的一般行为 | **没有读 driver core 的匹配循环代码**来确认"没有 driver 会怎样" |
| arm64 分诊表里「sysfs 仅兼容、legacy 基本不碰」（04 篇末） | `Kconfig:64` 的 `select GPIO_CDEV` 注释 + legacy API 的使用面 | 基于代码倾向的判断，不是对 arm64 defconfig 的统计 |

### §5.4 第四层：版本时效

- **所有行号只对 `f6e38ae624cf`（6.1.158）有效。** gpio 核心在 6.2+ 有较大改动（`gpio_device` 的字段、cdev 的 `supinfo` 机制等），跨版本套用行号一定错。
- `gpio-virtuser.c` 在 6.1 **不存在**，它是更新的内核才有的（07 §9 已标注）。读到别处教程提它，先确认版本。
- `Documentation/devicetree/bindings/gpio/gpio.txt` 在 6.1 **仍是 `.txt`**（§4.4）。更高版本可能已转 YAML，届时 `gpiolib.c:1794`/`:1852` 的引用会变成悬空。
- `tools/gpio/` 四个工具的输出格式随版本变化。07 §10 的格式表只对本版本有效。
- §2.3 的每个数字都绑在这个 commit 上。**换 commit 就必须整节重跑**（skill 规则 25）。

### §5.5 不适用项

- **对端 / 树外实现**：gpio 不是虚拟总线型子系统，没有"对端驱动"这一层。唯一的树外相关物是 libgpiod（用户态库），已在 §5.2 登记为零阅读。
- **多架构对照**：按用户要求裁掉，见 §2.7。
- **ACPI 路径**：同上。

---

## §6 已发现的问题清单

分三组：**代码层面的坑**、**官方文档与代码不符**、**已撤销的候选**。

### §6.1 官方文档 vs 代码（①–⑧）

| # | 位置 | 冲突内容 | 严重度 |
|---|---|---|---|
| ① | `legacy.rst:391` + `:408` | 文档给出 `int irq_to_gpio(unsigned irq);` 并在 `:408` 讨论"从 `irq_to_gpio()` 返回的非错误值最常用来…"。**但 6.1 里它是两个硬编码失败桩**：`include/linux/gpio.h:84`（GPIOLIB 路径）直接 `return -EINVAL;`；`:221`（!GPIOLIB 路径）先 `WARN_ON(1)` 再 `return -EINVAL;`。全树 `.c` 文件**零调用者**。**"非错误返回值"在本版本永远不会出现。** | **高** —— 文档描述的用法完全不可实现 |
| ② | `legacy.rst` 全篇 | legacy 整数 API（`gpio_get_value` / `gpio_set_value` 等）**全部是 raw 语义**（不做 `active_low` 反转），文档从未点明。读者从 `gpiod_*` 迁移过来会踩极性坑 | **高** |
| ③ | `driver.rst:766-769` | 文档写 `gpiochip_request_own_desc(struct gpio_desc *desc, u16 hwnum, const char *label, enum gpiod_flags flags)` —— **4 个参数、第一个是 `gpio_desc *`**。实际是 `gpiolib.c:2142-2146`：`gpiochip_request_own_desc(struct gpio_chip *gc, unsigned int hwnum, const char *label, enum gpio_lookup_flags lflags, enum gpiod_flags dflags)` —— **5 个参数、第一个是 `gpio_chip *`**。一处错两样（首参类型 + 漏掉 `lflags`） | **高** —— 照抄编译不过 |
| ④ | `driver.rst:593`、`:609` | 示例代码调用 `irq_mask_mask_parent(d)` 和 `irq_mask_unmask_parent(d)`。**这两个函数全树不存在。** 真实名字是 `irq_chip_mask_parent()`（`include/linux/irq.h:669`）和 `irq_chip_unmask_parent()`（`:671`） | **高** —— 照抄编译不过 |
| ⑤ | `board.rst:126-133` | 只列了 6 个 lookup flag（`GPIO_ACTIVE_HIGH`/`_LOW`、`GPIO_OPEN_DRAIN`、`GPIO_OPEN_SOURCE`、`GPIO_PERSISTENT`、`GPIO_TRANSITORY`）。`include/linux/gpio/machine.h:8-21` 实际有 **9 个 + 1 个默认组合**，漏掉 `GPIO_PULL_UP`、`GPIO_PULL_DOWN`、`GPIO_PULL_DISABLE` 和 `GPIO_LOOKUP_FLAGS_DEFAULT`。文档末尾还写着"In the future, these flags might be extended" —— **未来已经到了，文档没跟上** | 中 |
| ⑥ | `consumer.rst` | 遗漏若干 API 族（数组批量操作的部分变体、`devm_` 的部分变体）。01 的 API 表以头文件为准 | 中 |
| ⑦ | `legacy.rst:311`/`:315` | 签名里丢掉了 `const` 限定符。`:572-574` 把 `gpio_get_value` 说成 `#define`，实际在 GPIOLIB 路径下是 `static inline` 函数 | 低 |
| ⑧ | `legacy.rst:738-756` vs `admin-guide/gpio/sysfs.rst:146-164` | **同一套 sysfs 导出 API 在两处文档里名字不同**：`legacy.rst:738` 是 `gpio_export(unsigned gpio, …)`，`sysfs.rst:146` 是 `gpiod_export(struct gpio_desc *desc, …)`。更微妙的是 `sysfs.rst:148` 的注释仍写 `/* reverse gpio_export() */` —— **新文档里留着旧名字**。读者无法判断哪个是当前 API（答案：`gpiod_export`，`gpiolib-sysfs.c` 导出的是它） | 中 |

### §6.2 代码层面（⑨–⑩）

| # | 位置 | 内容 |
|---|---|---|
| ⑨ | `gpiolib.c:871` kerneldoc vs `:906-914` | kerneldoc 写 **"A gpio_chip with any GPIOs still requested may not be removed."** —— 读起来像是"会被拒绝"。实际代码：发现还有线被占用时只打一条 `dev_crit`（`:906-907`，全子系统唯一一处 `crit`），然后**继续执行到 `gcdev_unregister(gdev)`（`:914`）**。也就是说**没有任何机制阻止移除**，文档承诺的保护不存在。这条同时写进了 08 §1 的误导点二 |
| ⑩ | `gpiolib-of.c:292` | 注释 `/* Converts gpio_lookup_flags into bitmask of GPIO_* values */` 与函数签名 `of_convert_gpio_flags(enum of_gpio_flags flags)` 矛盾，**转换方向写反了**。定性见 §4.5：普通行注释，不进 htmldocs |

外加 11 条源码自陈债（`FIXME`/`TODO`/`REVISIT`）见 §2.3 ⑪，其中 `gpiolib.c:701` 那条最有分量。

### §6.3 已撤销的候选（老实留着）

| 原编号 | 候选内容 | 撤销理由 |
|---|---|---|
| **#10** | `gpiochip_add_pin_range()` 的 kerneldoc（`gpiolib.c:1793-1795`、`:1851-1853`）指向 `Documentation/devicetree/bindings/gpio/gpio.txt` "Section 2.1"，怀疑是悬空引用 | **文件在、Section 2.1 在、内容对得上。** 详见 §4.4 |
| **#11** | `CONFIG_DEBUG_GPIO` 在 `.c` 文件里 grep 不到，怀疑是死开关 | **它是活的**，中间隔了两跳不含 `CONFIG_` 的宏改名。详见 §4.1。07 §1.2 已正确记录整条链。**不要在任何篇目里重新引入"死开关"的说法** |

两条撤销都留在这里是因为它们各自否掉了一个看起来很靠得住的先验（「6.1 的绑定文档都转 YAML 了」、「grep 不到使用点就是没人用」）。**反例把结论的方向性钉准了**（skill 规则 19）。

---

## §7 批量自检脚本的结果

三个脚本（skill `scripts/` 下，纯 stdlib）在**本文档集定稿时**的实际输出。命令：

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/gpio

python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh gpio $D .
```

三条命令都**没有** `--ignore` 参数：`output/gpio/` 目录下既没有 `.xref-ignore` 也没有 `.lineref-ignore`——本文档集没有登记任何一条假阳性豁免。这一点和 pinctrl 那套不同（那套有 2 条 xref 豁免），下面 §7.1 会说明为什么这次不需要。

### §7.1 `check_xrefs.py`（跨篇 `0X §Y` 引用）

```
文档 10 篇；跨篇引用命中 325 条，失效 0 条（忽略项 0 条）
干净：所有跨篇引用都指向真实存在的章节。
```

- **325 条里含本篇自己发出的引用**（§3 逐篇取证表、§4 交叉验证、§9 对照表都在引用别篇），所以改本篇正文这个数字就会动——定稿时重跑到收敛为止
- **自指陷阱**：本节如果照抄脚本报出的失效行原文，下一轮脚本会把这段引述本身再报一次。所以下表只描述位置和性质，不照搬脚本的输出格式

全部轮次真抓到的 4 条（已修）：

| 位置 | 问题 | 修法 |
|---|---|---|
| 00 篇 `:58`（§8 导读表） | 指向 02 篇一个不存在的小节。02 里讲"退化总线"那一节实际是 `§1.4 gpio_bus_type 不是「GPIO 设备住的总线」` | 改成 `§1.4` |
| 本篇 §3 两处（`:376`、`:690`） | 指向 06 篇一个不存在的小节。`gpio-controller` 属性"零读者"那段材料实际在 **06 §2.2** | 一处改指 `§2.2`；另一处改写成不带编号的说法（「06 §1 的三方分工判定」） |
| 本篇 §4（`:478`） | **假阳性**：那句话正在**描述**一个已经修掉的坏引用（原本指向 03 的一个不存在的小节），脚本把"被描述的编号"当成了真引用 | **没有登记进 `.xref-ignore`**。改写成 `03 篇「§A2.9」` 的形式——插入的「篇」字打断了脚本赖以识别的「两位数字 + §」相邻模式，句意一字未改 |
| 本篇 §3.3 | 抄了 02 的**旧标题**「§1.0 先否掉的错误类比」，而 02 现在的标题是「§1 先分清五组关系，逐条否掉错误类比」 | 按 02 的现标题改写 |

前两条是同一个根因：**我写引用时用的是"我记得那一节叫什么"，不是"我刚打开看过"。** 02 和 06 都在中途按规则 21 重排过，被重排掉的旧编号只活在我的记忆里。规则 21 结尾那句「重排轮结束必须跑 `check_xrefs.py`」就是为这件事写的。

第三条值得单独记：**它是本项目第一次遇到"文档在讨论引用本身"导致的假阳性，而处理办法是改写句子而不是登记豁免。** 判据是——豁免文件是给"读者需要这种写法、脚本理解不了"的情形用的（pinctrl 那 2 条就是），而这一句改写之后读者读起来更清楚（多了「篇」字，明确了是对方的章节号），那就没有理由去动脚本的配置。

### §7.2 `check_line_refs.py`（`路径:行号` 是否越界）

```
扫描 564 条唯一 file:line 引用（消歧表 71 个基名 / 57 个前缀目录，忽略项 0 条）

干净：没有解析不到的文件，也没有越界行号。
```

三档全空：UNRESOLVED 0、OUT-OF-RANGE 0，而且**连 AMBIGUOUS 段都没有打印**。

**但"没有 AMBIGUOUS"绝不等于"本子系统没有同名文件歧义"。** 读一遍脚本的 `resolve()` 就知道它的判据是什么：AMBIGUOUS 只在**本文档集自己写过两个以上不同全路径**的同一基名上触发，它**不扫全树**。而 gpio 恰好是内核里同名头文件的重灾区：

| 基名 | 树里同名文件数 | 本文档集的写法 |
|---|---|---|
| `gpio.h` | **18** | 从不裸写。`include/uapi/linux/gpio.h` 19 处、`include/linux/gpio.h` 16 处、`include/asm-generic/gpio.h` 14 处、`include/trace/events/gpio.h` 2 处，全部带全路径 |
| `driver.h` | **11** | 32 处全路径（`include/linux/gpio/driver.h`）+ 20 处裸写 |
| `machine.h` | 7 | 6 处全路径 + 4 处裸写 |
| `consumer.h` | 5 | 6 处全路径 + 6 处裸写 |
| `gpiolib.c` | 2（另一个是 `arch/mips/alchemy/common/gpiolib.c`） | 全篇 200+ 处裸写，全路径只写过 68 处 |
| `gpiolib.h` | 1 | 全树唯一，裸写没有风险 |

```bash
for n in gpio.h driver.h machine.h consumer.h gpiolib.c gpiolib.h; do
  printf "%-12s %s\n" "$n" "$(find . -name "$n" | wc -l)"; done
```

结论：`gpiolib.c:187` 这种裸写之所以能被正确解析，是因为本文档集从头到尾只给这个基名写过一个全路径（`drivers/gpio/gpiolib.c`），脚本采信了它。**万一哪天有人在同一套文档里引用 arch/mips 那个 `gpiolib.c`，脚本才会开始报警——在那之前它不会替你发现这个风险。** `gpio.h` 的 18 个候选之所以一次都没出事，靠的是"从不裸写"这条纪律，不是靠脚本。

本轮顺手统一了一处写法：有 8 处把 `include/asm-generic/gpio.h` 写成了少一层前缀的 `asm-generic/gpio.h`。脚本靠"前缀目录拼接"这一级兜住了，**一条都没报错**——但**脚本兜得住 ≠ 写法对**：读者照那个路径 `cat` 是打不开文件的。全部已改为全路径（现共 14 处）。

这一轮真正的行号错误，**三个脚本一条都抓不到**——它们全部是第三类"行号在文件范围内但指向无关代码"，只能 `sed` 逐个打开看。08 篇 §5–§9 写完后自审出 6 处：

| 篇/节 | 我写成 | 实际 | 性质 |
|---|---|---|---|
| 08 §5 | `char prop_name[32]` 在 `gpiolib-of.c:506` | **`:507`** | 差一行，`:506` 是另一个声明 |
| 08 §9 | `linereq_free()` 归还循环 `gpiolib-cdev.c:1712-1722` | **`:1714-1723`** | 整段平移两行 |
| 08 §9 | `if (!line->desc) continue;` `:1715-1716` | **`:1716-1717`** | 同上 |
| 08 §8 | 代码块标签 `gpiolib.c:2821-2827` | **`:2825-2827`** | 贴出来的是 `WARN_ON` 那三行，不是函数头 |
| 08 §8 | 代码块标签 `gpiolib.c:3137-3141` | **`:3139-3141`** | 同上 |
| 08 §8 | `gpiod_get_value()` 函数范围 `:2821-2837` | **`:2821-2836`** | `:2837` 是 `EXPORT_SYMBOL_GPL`，不在函数体内 |

六条全部落在文件行数之内，脚本判定"干净"。**这就是规则 19 说"逐个 `sed` 打开核对，不抽样"的全部理由**——脚本清掉的是机械错误，语义错误只能人看。另外注意这六条的分布：**全部集中在 08 篇，也就是本轮新写的那一篇**，前九篇早前的自审轮已经清过。这印证规则 15 的第二条：**每一轮 review 自己会产生新的错误，自审的对象必须包含自审这一轮新写的文字。**

### §7.3 `check_doc_coverage.sh`（官方文档反向覆盖率）

```
=== 【必查】概念/框架文档 ===
  board 18 次   bt8xxgpio 3 次   consumer 65 次   driver 555 次
  drivers-on-gpio 4 次   intro 4 次   legacy 68 次   using-gpio 5 次
=== 【参考】DT 绑定：共 47 篇，被引用 2 篇 ===
  brcm,bcm6345-gpio（2 次）   gpio-pca95xx（2 次）
【必查】共 8 篇，其中 0 篇 0 引用。
必查档全部有引用。
```

- **和 pinctrl 那次形成明确对照**：pinctrl 那套的「必查共 0 篇」意味着脚本**一篇官方文档都没匹配到**（主文档叫 `pin-control.rst`，中间有连字符，`*pinctrl*` 匹配不上），"全部有引用"是假通过。gpio 的命名规整（目录直接叫 `driver-api/gpio/` 和 `admin-guide/gpio/`），8 篇全部匹配到，**这次的"必查档全部有引用"是真的**
- 但 `driver` 那 **556 次是假数**：脚本按去掉后缀的基名做子串匹配，中文正文里每出现一个 "driver" 字样都给它记一次。同理 `consumer` 65 次、`board` 18 次也偏高。真正可信的是那些不会作为普通英文词出现的名字：末尾四篇各 3～5 次
- **还有一层自指污染**：本节（§7.3）为了讨论覆盖率本身，把这些篇名又各写了一两遍，脚本把这些"讨论"也算成了引用。所以末尾四篇的数字里有 1～3 次来自本节的元讨论，**扣掉后真实的内容引用只有 2～3 次**。这也是为什么本节刻意不再重复写出那些篇名——否则每改一次数字，数字自己又变了
- 这四个低引用篇必须老实交代，因为**脚本区分不了"被提到"和"被采信"**：
  - 未纳入的那篇 bt8xx 平台文档（62 行）见 §1.3。它的引用全部出现在"说明为什么不纳入"的句子里——在脚本眼里这和"引用了它的技术内容"完全一样
  - `intro.rst`（124 行）、`using-gpio.rst`（50 行）、`drivers-on-gpio.rst`（114 行）三篇都很短，内容已经浓缩进 00 §1/§3/§7 和 05 §1。引用次数低是因为它们贡献的是**框架和边界**，不是可以逐条落行号的具体断言
- 「DT 绑定共 47 篇」和 §1.3 说的「100 个绑定文件」不矛盾：脚本只统计 `*.yaml`，那个目录实际是 47 个 yaml + 53 个 txt

```bash
ls Documentation/devicetree/bindings/gpio/ | wc -l          # 100
ls Documentation/devicetree/bindings/gpio/*.yaml | wc -l    # 47
ls Documentation/devicetree/bindings/gpio/*.txt  | wc -l    # 53
```

  **6.1 的 gpio 绑定目录里 txt 比 yaml 还多**——这直接否掉了"6.1 的绑定文档都已转 YAML"这个先验（同一个先验在 §4.4 上也栽过一次）。而 06 篇最依赖的那篇通用规则文档 `gpio.txt` 恰好就在这 53 个 txt 里，**脚本的 47 篇统计从来没看见过它**
- 「被引用 2 篇」不构成问题：06 篇的硬要求是**每条属性都有定位到行的消费者**，不是每篇绑定都被引用（脚本自己也在输出里提醒了这一点）

### §7.4 工具与复现环境（本节决定你重跑我的命令能不能得到同一个数）

| 工具 | 本文档集实际用的版本 | 复现命令 |
|---|---|---|
| 内核树 | Linux 6.1.158（`f6e38ae624cf`） | `git log -1 --oneline` |
| git | 2.34.1 | `git --version` |
| python3（跑两个自检脚本） | 3.10.12 | `python3 -V` |
| ripgrep | 13.0.0 | `rg --version \| head -1` |
| **`grep`** | **ugrep 7.5.0，不是 GNU grep** | `grep --version \| head -1` |

**最后一行是本文档集最容易让你重跑不出同样结果的地方，必须说清楚**：我这个 shell 里的 `grep` 是一个转到 **ugrep 7.5.0** 的函数（`type grep` 可见），而这台机器上 `/usr/bin/grep` 是 **GNU grep 3.7**。两者在两个地方不等价：

1. **正则方言**。那个包装以 `-G`（基本正则）调用 ugrep，**不支持 GNU 的 `\s` 扩展**。实测：

   ```bash
   grep      -rln 'add_pin_ranges\s*=' drivers/pinctrl/   # ugrep 包装：0 个文件
   /usr/bin/grep -rln 'add_pin_ranges\s*=' drivers/pinctrl/   # GNU grep：8 个文件
   ```

   这不是假想的风险——§2.3 ⑥ 那条「10 个文件赋值 `add_pin_ranges`，8 个在 `drivers/pinctrl/`」我第一次就是被这个坑住的，当时拿到的是 2 个文件。**本文档集里所有命令因此一律改用 POSIX 字符类 `[[:space:]]` 而不是 `\s`**，这样两边都对。
2. **默认过滤**。那个包装带 `--ignore-files --hidden -I --exclude-dir=.git`，即**尊重 `.gitignore` 并跳过 `.git/` 和二进制文件**；GNU grep 不会。对干净的内核树，这只影响"要不要去 `.git/` 里捞垃圾匹配"——但如果你的树里有编译产物（`.o`、`.cmd`、`Module.symvers`），**GNU grep 会把它们算进计数，我的数字里没有**。

所以复核 §2.3 的数字时，如果你用 GNU grep 得到的数比我的大，先怀疑这两条，再怀疑版本差异。**结论：凡是要写进文档的计数命令，只用 POSIX 语法，并且显式限定 `--include=*.c` 之类的范围**——不要依赖任何一边的默认行为。

---

## §8 本文档集的读法建议

按可靠性排序，读者可以据此决定"要不要自己再验一遍"：

| 可以直接用 | 建议自己再确认 | 别当结论用 |
|---|---|---|
| 贴了逐字源码的段落（全文最可靠的部分，判据见 §3） | 所有 `文件:行号`——小版本升级就会漂，用 §5.4 的字符串保底 | §5.3 那七条推断（尤其"编号分配方向导致的实际混乱"和"移除时的悬空风险"） |
| §2.3 十一组带命令的数字（定稿前逐格重跑过） | 05 的代码骨架（未编译，§5.1） | 07 里 debugfs / tracepoint / lsgpio 的**具体输出内容**——格式串是真的（来自 `seq_printf`/`TP_printk`），**值是我按格式串手推的，不是跑出来的**（§5.1） |
| §6 的 ①③④⑤⑧（官方文档与代码不符，两边都给了行号，可直接 diff） | 关于"控制器驱动通常怎么做"的群体特征——只做过 grep 统计，**没有详读任何一个真实 SoC 控制器驱动**（§2.2） | 任何关于 6.2+ 或主线的推测——**本文档集没做过任何跨版本比对** |
| §4 五条交叉验证（每条都记了初始假设怎么被推翻） | 02 §1 那五组关系的**否定性论断**（"为什么不会发生"也给了行号，但否定性论断最难穷尽） | §6.3 那两条**已撤销**的候选——留着是当反例看的，不是结论 |

**四条针对 gpio 的特别提醒**：

1. **看到"GPIO 编号"先分清是哪一层。** 本文档集全程区分四层值（PAD 电平 / 寄存器值 / raw / logical）和两套编号（全局整数 vs `gpio_desc` 指针）。任何一句没交代层次的"GPIO 值"都请当成我的疏漏，去 08 §3、08 §4 查该在哪一层。
2. **`CONFIG_DEBUG_GPIO` 是活的。** 它在 `.c` 里 grep 不到只是因为中间隔了两跳不含 `CONFIG_` 字样的宏改名（§4.1、07 §1.2）。不要因为 grep 不到就得出"死开关"的结论——我自己先犯过这个错（§6.3 #11）。
3. **官方文档在这个子系统上的可信度偏低。** §6 里 8 条冲突有 6 条是 `Documentation/driver-api/gpio/` 与代码不符，其中 ③④ 照抄会直接编译不过。**凡冲突一律以代码为准。**
4. **arm64-only 和 ACPI 裁剪是有意的，不是遗漏。** 裁掉了哪些文件、哪几节、为什么，逐项记在 §2.7；其中两处 ACPI 痕迹是**故意留下**的（`gpiolib.h:80-81` 的注释、07 §2.2 的 `grep -v` 过滤），理由同样在 §2.7。

**发现错误时的最小复现路径**：先跑 §2.3 里对应那组命令；数字对不上就说明本文档集相对你的树已经过期（多半是行号漂了），此时用 §5.4 说的"拿字符串重新定位"；如果连字符串都找不到，那就是真的版本差异，以你的代码为准。

---

## §9 与另一套文档集的对照（skill 规则 29）

`output/qoder/gpio/`（10 篇 **8506** 行）是另一个工具就同一个子系统、同一棵树独立生成的文档集；本套是 10 篇 **9518** 行。

**次序声明**：本套 10 篇先自己写完、逐篇自审、三个脚本全绿（§7），**然后才打开对方**。这是硬要求——先看别人的会污染自己的取证链，之后再也分不清"这条是我验的还是我抄的"。

**引用约定**：本节引用对方章节一律写成「**对方 NN 篇 §X**」。这不是排版偏好——`check_xrefs.py` 会把 `NN §X` 当成本套的跨篇引用去校验，写成"对方 NN 篇"既能让读者分清是谁的章节号，又不会产生假失效项。这条约定是上一个子系统（pinctrl）那轮对照写坏之后补出来的：当时这一节直接写了对方的章节号，脚本拿去本套里找，报了 4 条失效——**那不是脚本的假阳性，是写法有歧义，读者也会以为那是本套的章节**。

**对照方法**：比的是**事实清单，不是章节名**。两套的分篇骨架同源，章节名相似度很高，但那不说明任何问题；有意义的比较只有三种结果——(a) 对方有、我方没有的**事实**；(b) 我方有、对方没有的事实；(c) 同一处两套结论**不一致**——(c) 最有价值，因为必然有一套是错的。

### §9.1 吸收的 3 条（都已落进正文，并做了升级）

| # | 对方出处 | 吸收了什么 | 落在本套哪里 + 我做的升级 |
|---|---|---|---|
| ① | 对方 99 篇 §9「工具版本」 | **登记工具版本**这个动作本身。skill 规则 25 把"工具版本"列进了每轮要重跑的东西，但我这套前九篇写完时确实没登记 | 落在 §7.4。**升级**：对方只登记了 git / rg / cbm 三项，我补了 `python3`（两个自检脚本靠它）；更重要的是查出**这个 shell 里的 `grep` 是转到 ugrep 7.5.0 的函数，不是 `/usr/bin/grep` 的 GNU grep 3.7**，并给出了两者结果不同的实测例子（`\s` 在一边匹配 8 个文件、另一边 0 个）。这条不是"补一张版本表"，是**本文档集所有计数命令的可复现性前提** |
| ② | 对方 02 篇 §7.3 | `struct bgpio_pdata`（`include/linux/gpio/driver.h:662-666`）。我方 04 §6.6 讲了 bgpio 附带的 platform 驱动，但漏了它的平台数据结构 | 落在 04 §6.6。**升级**：补出对方没有的三件事——`bgpio_parse_dt()` 有**两份定义**（`gpio-mmio.c:693` 的 `CONFIG_OF` 版 / `:717` 的空壳版）、DT 路强制 `pdata->base = -1`（`:706`，DTS 无法指定 base）、全树只有 3 个文件手填过它且**一个 arm64 平台都没有** |
| ③ | 对方 05 篇 §1.3（I²C 扩展器骨架）+ §5（PM 回调）+ §6（remove 回调） | 提供方侧的三个空白：**慢总线扩展器**这个提供方形态、**PM**、**remove**。我方 05 的八个角色里，控制器骨架只有 MMIO 型（pl061）和 bgpio 捷径 | 落在 05 §3.5 / §3.6。**升级**：对方是"贴一份 pca953x 骨架"，我改成**三件必须多做的事 + 每件的因果**（`can_sleep = true` 改变的是哪些 API 合法而不是快慢；`threaded = true` 为什么排除了级联 handler；`irq_bus_lock`/`irq_bus_sync_unlock` 为什么是慢总线专用口子）。PM 拆成两个不同问题（寄存器丢内容 vs 线丢电平），并挖出一条对方没有的事实：**`gpiochip_line_is_persistent()` 是导出符号，但全树只有 `gpio-arizona.c:31`/`:101` 一个驱动在用** |

### §9.2 明确不吸收的 5 条（附理由）

| # | 对方内容 | 不吸收的理由 |
|---|---|---|
| ① | 对方 01 篇 §2.8「ACPI 驱动辅助」、对方 04 篇 §4.5「gpiolib-acpi 整节」 | **用户明确要求不写 ACPI**。裁剪范围和两处故意保留的 ACPI 痕迹见 §2.7。这不是内容优劣问题 |
| ② | 对方 01 篇 §6「核心结构体」（把 4 个结构体的字段表放在 API 篇） | 与 skill 规则 8 冲突：**读者的一次提问只应落在一篇里**。本套结构体字段统一在 02，01 只放 API 三列表。两处都维护字段表 = 改一处漏一处（规则 17） |
| ③ | 对方每篇篇末各带一个「取证附录」（02/03/06 篇都有） | 同上：同一个数字在两处维护必然漂。本套所有取证集中在 99，前九篇只留 `文件:行号` |
| ④ | 对方 06 篇 §7 的六个 SoC DTS 实例（ZynqMP / imx8mm Venice overlay / MT8192 / UniPhier LD11 / SM8450 / HiKey960） | **不是覆盖面差距**：两套都引了 7 个 arm64 厂商目录（我方 amlogic/freescale/hisilicon/qcom/renesas/rockchip/socionext，共 9 个 dts 文件；对方 freescale/hisilicon/mediatek/nvidia/qcom/socionext/xilinx），交集 4 个。换 SoC 不增加新的属性消费点，而本套的实例是贴在**各属性节内**、篇末 §11 只做汇总——组织方式不同，内容不缺 |
| ⑤ | 对方 08 篇每个主题下另设一个「代码在哪」小节 | 会让同一批行号在同一篇里出现两次（正文一次、小节一次）。本套把行号直接嵌在"回到代码"的叙述里。理由同 ③：**重复的行号就是必然漂的行号** |

### §9.3 对照真正的产出：在对方文档集里发现的 2 处错误

这是本轮对照最有价值的部分——两处都是**一条 grep 就能证伪**的。

**① 对方 02 篇 §1.0.3 给 pinctrl 编了一个不存在的 `pinctrl_bus_type`。**

原文写「pinctrl 有自己的 `pinctrl_bus_type`，两者互不干扰」，并把这句话当成了整节的落点（该节标题就是「GPIO 的 bus_type vs pinctrl 的 bus_type」）。实际：

```bash
grep -rn 'pinctrl_bus_type' --include=*.c --include=*.h .          # 0 行
grep -rn 'struct bus_type' drivers/pinctrl/ include/linux/pinctrl/ # 0 行
```

**全树没有这个符号，`drivers/pinctrl/` 和 `include/linux/pinctrl/` 里也没有任何 `struct bus_type`。** pinctrl 是纯框架型子系统，`pinctrl_dev` 甚至不是 `struct device`——这一点对方**自己 00 篇 §2(d) 的对比表里写对了**（那张表里 pinctrl 一栏明确写着"无 bus_type"、"`pinctrl_dev` 不是 `struct device`"）。**所以这是对方文档集内部两篇自相矛盾，而错的是 02 篇那一篇。**

值得注意的是**错误的方向**：它不是把 gpio 写错，而是为了做"对比"给对照物**编了一个对称的符号**。这类错误在"横向对比表"里最容易出现，因为表格的形状本身在诱导你把每一格填满（skill 规则 19 说的"表格里的 `—` 是欠债的记号"，反面就是"为了不留空格而编内容"）。

**② 对方 00 篇 §2(d) 把可选桥接说成了必然路径。**

原文：「每次 GPIO request/free/config 都会回调 pinctrl（`drivers/gpio/gpiolib.c:1750`）」。`:1750` 这个行号是对的——它就是 `gpiochip_generic_request()` 里那句 `return pinctrl_gpio_request(gc->gpiodev->base + offset);`。但这个函数**只有在提供方驱动把它赋给 `gc->request` 时才会被调用**：

```bash
grep -rl '\.request[[:space:]]*=[[:space:]]*gpiochip_generic_request' --include=*.c . | wc -l   # 38 个文件
grep -rl 'gpiochip_add_data\|devm_gpiochip_add_data\|gpiochip_add(' --include=*.c . | wc -l     # 309 个文件
```

**38 / 309。** 绝大多数提供方根本没有这条桥接，`gpiod_get()` 也就完全不会碰到 pinctrl。正确说法是「**可选**桥接，由提供方自己选」——我方 08 §6 的"误导点一"和 01 §7 都是按这个口径写的。

这条错误的性质和 ① 是一类：**把"框架提供了这个能力"读成了"框架总是这样做"**。判据很机械——凡是函数指针字段（`gc->request`、`gc->to_irq`、`gc->add_pin_ranges`）上的行为，都必须先数一遍"有多少提供方真的赋了值"，再决定用"会"还是"可以"。

### §9.4 这轮对照本身产生的两条方法论

1. **对照的价值主要不在"补内容"，在"同一处两套结论不一致"。** 本轮 3 条吸收都是小补丁（一个结构体、一张版本表、一类提供方骨架），而 2 条不一致直接指向对方的事实错误。所以对照时**应该优先去找结论冲突，而不是逐节比目录**——比目录只能发现组织差异，那恰好是最不重要的一类差异。
2. **两处错误的根因可以固化成对照时的第一批检查动作**（值得写回 skill）：
   - **对比表里的对照物是否被编造**：凡是"X 有 A，Y 也有 A′"的句式，`grep` 一次 A′ 的符号名。表格形状会诱导人把格子填满
   - **函数指针字段上的"会/总是"**：先数赋值者个数，再写"会"还是"可以"。gpio 这个子系统里至少有三个字段踩得到（`request`/`to_irq`/`add_pin_ranges`），本套 §2.3 ⑥ 就是为这件事专门做的普查

---
