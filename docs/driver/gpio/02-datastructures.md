---
title: 数据结构
---

# GPIO 子系统 — 核心数据结构

> 本篇是 GPIO 子系统的「结构体地图」。API 清单在 `01-api.md`，流程剖析在 `03-internals.md`。
>
> 阅读顺序建议：先读 §1「先分清五组关系」把常见误解否掉，再看 §2 的全景关系图和 §3 的父总线定位图，然后按需查 §4 的逐结构体字段表。§5 是全局变量与锁，§6 是核心操作的数据流，§7 是命名约定速查。

---

## §1 先分清五组关系

GPIO 子系统里有五组名字长得像、职责完全不同的东西。**图画得再全，也补不上一个没被显式否掉的错误类比**，所以这一节先做否定。

### §1.1 `gpio_chip` 和 `gpio_device` 不是「driver 和 device」

这是最容易出错的一组。看名字很容易以为 `gpio_chip` 是驱动侧的东西、`gpio_device` 是设备侧的东西，两者像 `i2c_driver` / `i2c_client` 那样绑定。**不是。**

| 对比项 | `struct gpio_chip` | `struct gpio_device` |
|---|---|---|
| 定义在哪 | `include/linux/gpio/driver.h:415` — **公开头文件** | `drivers/gpio/gpiolib.h:53` — **私有头文件** |
| 谁分配 | **控制器驱动**（通常 `devm_kzalloc` 在自己的私有结构里） | **gpiolib 核心**（`gpiolib.c:600` 的 `kzalloc`） |
| 谁填字段 | 控制器驱动填 `label`/`ngpio`/回调 | 核心填 `id`/`base`/`descs`/`dev` |
| 生命周期 | 由驱动决定，**注销后可以立刻消失** | 由 `struct device` 的引用计数决定，**可以活到 chip 消失之后** |
| 是不是 `struct device` | 不是 | **内嵌**一个 `struct device dev`（`gpiolib.h:55`） |
| 会不会绑 driver | 不会 | 会绑上 `gpio_stub_drv`，但那是个空壳（见 §1.4） |

两者是**一对一互指**的：

```c
/* gpiolib.c:621-622 */
gdev->chip = gc;
gc->gpiodev = gdev;
```

真正的关系是「**驱动提供的操作表**」和「**核心持有的运行时状态容器**」。`gpiolib.h:47-52` 的 kerneldoc 把这层意思写得很明确：

> This state container holds most of the runtime variable data for a GPIO device and can hold references and live on after the GPIO chip has been removed, if it is still being used from userspace.

**为什么必须分成两个？** 因为热拔（或 `rmmod` 控制器驱动）时，`gpio_chip` 跟着驱动的私有内存一起走了，但用户态可能还开着 `/dev/gpiochip0` 的 fd。这时 `gpio_device` 必须还在，才能让那个 fd 上的 `ioctl()` 返回 `-ENODEV` 而不是踩空指针。这也是 `gdev->sem` 这把读写锁存在的唯一理由（`gpiolib.h:42-44` 的 kerneldoc 原话：「protects the structure from a NULL-pointer dereference of @chip by user-space operations when the device gets unregistered during a hot-unplug event」）。

**为什么不会发生「chip 没了、gdev 也没了、fd 还在」？** 因为 `gpiochip_remove()`（`gpiolib.c:874`）做的是「置空 `gdev->chip`、把 gdev 从链表摘掉、`put_device(&gdev->dev)`」——最后那个 `put_device` 只是**减引用**，cdev 打开时持有的引用还在，所以 `gdev` 的内存不会被释放。释放发生在 `gpiodevice_release()` 里，也就是最后一个引用消失的时候。

### §1.2 `gpio_desc` 和「GPIO 编号」不是一个东西的两种写法

| 对比项 | `struct gpio_desc *` | `unsigned int gpio`（全局编号） |
|---|---|---|
| 有效期 | **保证有效到这根线被 release**（`gpiolib.h:145-146` 的 kerneldoc 明文承诺） | 只在 chip 注册期间有意义；chip 重新注册后同一个号可能指向别的线 |
| 谁定义号段 | 不需要号 | `gdev->base` 起、长度 `ngpio`；`gc->base = -1` 时由核心动态分配，**分配是自底向上找空洞的** |
| 跨 chip 唯一 | 是（指针） | 是，但**不稳定** |
| 能不能塞进 DT / 用户态 ABI | 不能 | 能（这就是它至今没死的原因） |
| 互转 | `desc_to_gpio(desc)` | `gpio_to_desc(gpio)` |

`gpio_to_desc()` 的实现要遍历 `gpio_devices` 链表逐个比对号段——这是 O(chips) 的操作，也是「整数 API 慢」的原因之一。

⚠️ 别把「描述符」理解成「一根线的硬件寄存器位」。`gpio_desc` 里**没有任何寄存器地址**，它存的全是**软件状态**：谁占着它（`label`）、极性/开漏/中断等 20 个软件标志（`flags`）、debounce 周期。硬件访问一律经 `desc->gdev->chip` 的回调。

### §1.3 `gpio_chip` 和 `gpio_irq_chip` 不是「两个芯片」

`struct gpio_irq_chip irq` 是 `gpio_chip` 的**内嵌成员**（`driver.h:496`），不是并列的另一个对象。它的作用是「把这个 GPIO 控制器的中断能力描述清楚，让 gpiolib 代你建 irqdomain」。

而 `gpio_irq_chip` 里**又**有一个 `struct irq_chip *chip` 字段——这个才是 irq 子系统意义上的 irqchip（提供 `.irq_mask` / `.irq_unmask` 那一套）。所以是三层套娃：

```
struct gpio_chip
  └── struct gpio_irq_chip irq      ← gpiolib 的"中断能力描述"（配置 + 回调）
        └── struct irq_chip *chip   ← irq 子系统的标准 irqchip（操作表）
```

| 对比项 | `gpio_irq_chip` | `irq_chip` |
|---|---|---|
| 定义在哪 | `include/linux/gpio/driver.h:41` | `include/linux/irq.h` |
| 属于哪个子系统 | gpiolib 自己的概念 | irq 子系统的通用概念 |
| 谁读它 | 只有 `gpiolib.c` 的 `gpiochip_add_irqchip()` | irq 核心的 `irq_chip_*` 通用逻辑 |
| 里面装什么 | domain / 父 domain / 级联父中断号数组 / handler / valid_mask / 各种翻译回调 | `.irq_mask` / `.irq_unmask` / `.irq_ack` / `.irq_set_type` / `.flags` |
| 新写法下是不是 const | 不是（gpiolib 要往里写 domain 等） | **应该是 `const`**，配 `IRQCHIP_IMMUTABLE` |

### §1.4 `gpio_bus_type` 不是「GPIO 设备住的总线」

这是 GPIO 相对 i2c/spi 最反直觉的一点：

```c
/* gpiolib.c:61-64 */
static struct bus_type gpio_bus_type = {
	.name = "gpio",
	.match = gpio_bus_match,
};
```

看起来像 i2c 那样的「设备总线」，但它上面只有一个驱动：

```c
/* gpiolib.c:4443-4447 */
static struct device_driver gpio_stub_drv = {
	.name  = "gpio_stub_drv",
	.bus   = &gpio_bus_type,
	.probe = gpio_stub_drv_probe,   /* 直接 return 0 */
};
```

| 对比项 | i2c 的 `i2c_bus_type` | GPIO 的 `gpio_bus_type` |
|---|---|---|
| 上面挂什么设备 | 每个 I2C 从设备一个 `i2c_client` | 每个 **GPIO 控制器**一个 `gpio_device`（不是每根线！） |
| 上面挂什么驱动 | 各种 `i2c_driver` | 只有一个 `gpio_stub_drv`，`.probe` 返回 0 |
| match 做什么 | 比对 `of_node` / id_table，决定谁 probe 谁 | 只是让 stub 驱动能匹配上，**不做任何真实绑定决策** |
| 存在的目的 | 设备-驱动绑定 | **只为了满足 `fw_devlink=on`**——让 GPIO 消费者能在 devlink 里等到 GPIO 控制器「probe 完成」 |

也就是说：**GPIO 消费者不是通过总线匹配拿到 GPIO 的，而是通过 `gpiod_get()` 主动查找的。** 总线在这里退化成了一个「就绪状态广播器」。这也是把 GPIO 判定为「框架型子系统」而不是「硬件总线型」的关键依据（见 `00-overview.md` §1.1）。

**控制器自己住在哪条总线上？** 住在它的父设备所在的总线上——platform、amba、i2c、spi、mfd 都有。`gdev->dev.parent = gc->parent`（`gpiolib.c:620`）是两者唯一的缝合点。arm64 上 PL061 就是个 **amba** 设备（`gpio-pl061.c` 用的是 `amba_driver`，不是 `platform_driver`）。

### §1.5 `gpio_pin_range` 里的 range 和 `gpio-ranges` DT 属性不是一对一

`struct gpio_pin_range`（`driver.h:729`）是 gpiolib 侧的容器，里面**又**包了一个 `struct pinctrl_gpio_range range`——后者是 pinctrl 子系统的结构体。DT 里的一条 `gpio-ranges` 记录会变成一个 `gpio_pin_range`，但也可以完全不经 DT，由驱动在 `add_pin_ranges` 回调里手工 `gpiochip_add_pin_range()` 出来。

**关键：`pin_ranges` 链表挂在 `gpio_device` 上而不是 `gpio_chip` 上**（`gpiolib.h:75`，且整段被 `#ifdef CONFIG_PINCTRL` 包住）。这意味着关掉 `CONFIG_PINCTRL` 时这个字段根本不存在，相关代码全部编译掉。

---

## §2 结构体全景关系图

```
                        ┌──────────────────────────────────────┐
                        │  全局（drivers/gpio/gpiolib.c）      │
                        │                                      │
                        │  gpio_devices (LIST_HEAD)  :79       │
                        │  gpio_lock (spinlock)      :75       │
                        │  gpio_ida                  :57       │
                        │  gpio_lookup_list          :78       │
                        │  gpio_machine_hogs         :82       │
                        │  gpio_bus_type             :61       │
                        └───────────────┬──────────────────────┘
                                        │ 链表串起所有已注册控制器
        ┌───────────────────────────────┴────────────────────────────────┐
        │                                                                │
        ▼                                                                ▼
┌───────────────────────────────────┐                          ┌──────────────────┐
│ struct gpio_device  (核心私有)     │  ←── 一对一互指 ──→      │ struct gpio_chip │
│ drivers/gpio/gpiolib.h:53         │                          │ driver.h:415     │
│                                   │                          │ （驱动分配）      │
│  int              id              │                          │                  │
│  struct device    dev  ←──────────┼── .parent ──────────────→│ struct device    │
│                                   │                          │        *parent   │
│  struct cdev      chrdev          │                          │  const char      │
│  struct module   *owner           │                          │        *label    │
│  struct gpio_chip *chip  ─────────┼──────────────────────────┤  u16   ngpio     │
│  struct gpio_desc *descs ────┐    │                          │  int   base      │
│  int              base       │    │                          │  bool  can_sleep │
│  u16              ngpio      │    │                          │                  │
│  const char      *label      │    │                          │  ── 回调 ──      │
│  void            *data ──────┼────┼── gpiochip_get_data()    │  .request/.free  │
│  struct list_head list       │    │                          │  .get/.set       │
│  blocking_notifier_head      │    │                          │  .get_multiple   │
│                   notifier   │    │                          │  .set_multiple   │
│  struct rw_semaphore sem     │    │                          │  .direction_*    │
│  struct list_head pin_ranges │    │                          │  .get_direction  │
│         (#ifdef CONFIG_PINCTRL)   │                          │  .set_config     │
└───────────────────────────────────┘                          │  .to_irq         │
                               │                               │  .dbg_show       │
              descs[] 数组，长度 ngpio                          │  .init_valid_mask│
                               │                               │  .add_pin_ranges │
                               ▼                               │  .en/dis_hw_ts   │
        ┌──────────────────────────────────────┐               │                  │
        │ struct gpio_desc （每根线一个）       │               │  unsigned long   │
        │ drivers/gpio/gpiolib.h:148           │               │      *valid_mask │
        │                                      │               │                  │
        │  struct gpio_device *gdev  ──────────┼──── 反指      │  ┌─────────────┐ │
        │  unsigned long       flags （20 位） │               │  │gpio_irq_chip│ │
        │  const char         *label （消费者）│               │  │  irq  :496  │ │
        │  const char         *name  （线名）  │               │  │ （内嵌）    │ │
        │  struct device_node *hog             │               │  └──────┬──────┘ │
        │       (#ifdef CONFIG_OF_DYNAMIC)     │               │         │        │
        │  unsigned int debounce_period_us     │               │  ┌──────▼──────┐ │
        │       (#ifdef CONFIG_GPIO_CDEV)      │               │  │ #if CONFIG_ │ │
        └──────────────────────────────────────┘               │  │ GPIO_GENERIC│ │
                    ▲              ▲                           │  │ reg_dat/set/│ │
                    │              │                           │  │ clr/dir_*   │ │
      ┌─────────────┘              └──────────────┐            │  │ bgpio_lock  │ │
      │                                            │            │  │ bgpio_data  │ │
┌─────┴──────────┐                    ┌────────────┴───────┐   │  └─────────────┘ │
│ 内核消费者      │                    │ 用户态 cdev        │   │                  │
│                │                    │                    │   │  #if CONFIG_OF_  │
│ struct         │                    │ gpio_chardev_data  │   │  GPIO:           │
│  gpio_descs {  │                    │   :2458            │   │   of_node        │
│   struct       │                    │  ├ gdev            │   │   of_gpio_n_cells│
│    gpio_desc   │                    │  ├ kfifo(32)       │   │   of_xlate       │
│    **desc;     │                    │  ├ lineinfo_       │   └──────────────────┘
│   unsigned     │                    │  │  changed_nb ────┼──→ gdev->notifier
│    ndescs;     │                    │  └ watched_lines   │
│  }             │                    │                    │
│ consumer.h:22  │                    │ linereq  :598      │            │
│                │                    │  ├ gdev            │            ▼
│ struct         │                    │  ├ kfifo(events)   │   ┌──────────────────┐
│  gpio_array {  │                    │  ├ config_mutex    │   │ struct           │
│   **desc       │                    │  └ struct line[]   │   │  gpio_pin_range  │
│   size         │                    │       :495         │   │  driver.h:729    │
│   *chip        │                    │        ├ rb_node   │   │  ├ node          │
│   *get_mask    │                    │        ├ desc      │   │  ├ *pctldev      │
│   *set_mask    │                    │        ├ irq       │   │  └ pinctrl_gpio_ │
│   invert_mask[]│                    │        ├ edflags   │   │      range range │
│  }             │                    │        ├ timestamp │   └──────────────────┘
│ gpiolib.h:97   │                    │        └ work      │      挂在 gdev->
│ （快速路径用）  │                    │                    │      pin_ranges 上
└────────────────┘                    │ lineevent_state    │
                                      │   :1936  （v1）     │
                                      │ linehandle_state   │
                                      │   :118   （v1）     │
                                      └────────────────────┘
```

**图里最值得记住的三条边：**

1. `gdev->descs` 是一个**连续数组**（不是链表），下标就是 hwnum。所以 `gpiochip_get_desc(gc, hwnum)` 只是一次下标访问 + 范围检查。
2. `gc->parent` → 父总线上的 `struct device`；`gdev->dev.parent` = 同一个指针。这是 GPIO 子系统和 platform / amba / i2c 世界的**唯一接缝**。
3. 用户态的每个结构（`linereq` / `lineevent_state` / `linehandle_state` / `gpio_chardev_data`）都持有 `gdev` 而**不是** `gc`——正是 §1.1 说的那个热拔安全设计。

---

## §3 「本子系统 ↔ 父总线」定位图

GPIO 控制器不住在 GPIO 总线上，这一点在 §1.4 已经否掉了。下面是它真正的位置（以 arm64 上的 PL061 为例）：

```
        Device Tree                          Linux 设备模型
   ───────────────────────           ─────────────────────────────────

   / {
     amba {                          ┌─────────────────────────┐
       compatible = "simple-bus"; ──→│ platform_bus            │
                                     │  └ of_platform 下钻     │
       gpio@f8011000 {               │    (simple-bus 在        │
         compatible = "arm,pl061",   │     of_default_bus_      │
                      "arm,primecell│     match_table[] 里)     │
         reg = <0x0 0xf8011000 ...>; └───────────┬─────────────┘
         interrupts = <0 52 4>;                  │
         gpio-controller;                        ▼
         #gpio-cells = <2>;          ┌─────────────────────────┐
         interrupt-controller;       │ amba_bus                │
         #interrupt-cells = <2>;     │  ← primecell 被 amba    │
       };                            │    层认领（读 PID 寄存器│
     };                              │    做 id 匹配）          │
                                     │                         │
     leds {                          │  struct amba_device     │
       led@0 {                       │    &adev->dev  ◄────────┼──┐
         gpios = <&gpio0 3 0>;       └─────────────────────────┘  │
       };                                                          │
     };                                          pl061_probe()     │
   };                                                  │           │
                                                       │  gc->parent = &adev->dev
   ┌───────────────────────────────────────────────────▼──────────┴──┐
   │  devm_gpiochip_add_data(dev, gc, pl061)                          │
   │                                                                  │
   │  gpiolib 核心建一个 gpio_device：                                │
   │    gdev->dev.bus    = &gpio_bus_type    ← 退化总线（只为 devlink）│
   │    gdev->dev.parent = gc->parent        ← 唯一接缝，指向 amba dev │
   │    dev_set_name(&gdev->dev, "gpiochip0")                          │
   └──────────────────────────────────────────────────────────────────┘
                                    │
                                    │  消费者侧（完全不走总线匹配）
                                    ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  leds-gpio 的 probe 里：                                          │
   │    devm_gpiod_get(dev, NULL, GPIOD_OUT_LOW)                       │
   │      → of_find_gpio(): 读自己节点的 "gpios" 属性                  │
   │      → of_get_named_gpiod_flags(): 解 phandle 拿到 &gpio0 节点     │
   │      → of_find_gpiochip_by_xlate(): 在 gpio_devices 链表里找       │
   │        of_node 匹配的那个 gdev                                     │
   │      → gc->of_xlate() 把 <3 0> 翻成 hwnum=3 + flags               │
   │      → gpiochip_get_desc(gc, 3) → &gdev->descs[3]                 │
   └──────────────────────────────────────────────────────────────────┘
```

**三个必查点的答案**（模板要求的固定三问）：

| 问题 | GPIO 的答案 | 代码依据 |
|---|---|---|
| 控制器 DT 节点由谁变成设备？ | 不固定。PL061 是 amba 层（`simple-bus` 下钻后由 amba 认领）；大多数 SoC GPIO 是 platform 层；I2C 扩展器由 i2c 核心 | `drivers/gpio/gpio-pl061.c` 用 `amba_driver`；`gpio-mxc.c` 等用 `platform_driver` |
| 子节点会不会被 platform 层继续下钻？ | GPIO 控制器节点本身**不会**（`gpio-controller` 不在 `of_default_bus_match_table[]` 里）。但它的**父节点**如果是 `simple-bus` 就会 | `drivers/of/platform.c` 的 `of_default_bus_match_table[]` |
| 两条总线的连接点是哪一行？ | `gpiolib.c:620` 的 `gdev->dev.parent = gc->parent;` | 同左 |

**一个只有 GPIO 才有的形状**：很多 GPIO 控制器**根本不是独立设备**，而是 pinctrl 驱动的一部分。Qualcomm 的 `pinctrl-msm.c`、Renesas 的 `pinctrl-rzg2l.c` 都在自己的 probe 里调 `devm_gpiochip_add_data()`。这时「父总线」是 platform，但控制器驱动文件根本不在 `drivers/gpio/` 下。这是给 GPIO 做全树 grep 时必须记住的一点。

---

## §4 逐结构体字段表

### §4.1 `struct gpio_device`（`drivers/gpio/gpiolib.h:53`）

**这是私有结构体**——只有 `drivers/gpio/*.c` 能看见它。控制器驱动拿不到它的字段，消费者更拿不到。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `int` | 从 `gpio_ida` 分配，决定 `gpiochipN` 里的 N 和字符设备次设备号 |
| `dev` | `struct device` | **内嵌**（不是指针）。`bus = gpio_bus_type`，`parent = gc->parent` |
| `chrdev` | `struct cdev` | `/dev/gpiochipN` 的字符设备。`CONFIG_GPIO_CDEV=n` 时不注册但字段仍在 |
| `mockdev` | `struct device *` | sysfs 那套 `/sys/class/gpio/gpiochipN` 的伪设备。只有 `CONFIG_GPIO_SYSFS` 才用 |
| `owner` | `struct module *` | 从 `gc->owner` 或 `gc->parent->driver->owner` 推导。防止有线在用时卸模块 |
| `chip` | `struct gpio_chip *` | **`gpiochip_remove()` 后会被置 NULL**。所有用户态入口都要在 `sem` 保护下判空 |
| `descs` | `struct gpio_desc *` | 长度 `ngpio` 的**连续数组**，`gpiochip_add_data_with_key()` 里 `kcalloc` 出来 |
| `base` | `int` | 这个 chip 的全局编号起点。`gc->base = -1` 时由 `gpiochip_find_base()` 分配 |
| `ngpio` | `u16` | 从 `gc->ngpio` 拷来。**是 u16，所以单 chip 最多 65535 根线** |
| `label` | `const char *` | 从 `gc->label` **`kstrdup_const`** 出来的副本（不是共享指针） |
| `data` | `void *` | 驱动私有指针，`gpiochip_get_data()` 取回 |
| `list` | `struct list_head` | 挂在全局 `gpio_devices` 上 |
| `notifier` | `struct blocking_notifier_head` | 线状态变化通知链。cdev 的 `lineinfo_changed` watch 就挂在这里 |
| `sem` | `struct rw_semaphore` | 热拔保护。所有 cdev 的 poll/read/ioctl 都 `down_read()`；`gpiochip_remove()` 里 `down_write()` |
| `pin_ranges` | `struct list_head` | `#ifdef CONFIG_PINCTRL`。GPIO↔pin 映射表 |

### §4.2 `struct gpio_desc`（`drivers/gpio/gpiolib.h:148`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `gdev` | `struct gpio_device *` | 反指所属 chip。**永不为 NULL**（描述符和 gdev 同生共死） |
| `flags` | `unsigned long` | 20 个位标志，见 §4.3 |
| `label` | `const char *` | **占用者名字**。非 NULL 就意味着这根线被 request 了 |
| `name` | `const char *` | **线名**，来自 DT `gpio-line-names` 或 `gc->names[]`。和 `label` 是两回事 |
| `hog` | `struct device_node *` | `#ifdef CONFIG_OF_DYNAMIC`。hog 这根线的 DT 节点 |
| `debounce_period_us` | `unsigned int` | `#ifdef CONFIG_GPIO_CDEV`。**是 cdev 的软件去抖周期，不是硬件去抖**（硬件那个走 `gc->set_config`，值不存在这里） |

**`label` vs `name` 是最容易混的一对**：
- `name` 是「这根线在板子上叫什么」（如 `"USB-HUB-RESET"`），由**板级/DTS**给，开机就定死；
- `label` 是「现在谁在用它」（如 `"reset"`、`"leds-gpio"`），由**消费者**给，request 时写、free 时清。

`debugfs/gpio` 里同时打印两者，格式串是 `" gpio-%-3d (%-20.20s|%-20.20s) %s %s %s%s\n"`（`gpiolib.c:4505`），即 `gpio-42 (USB-HUB-RESET |reset ) out hi`；未被 request 的线走另一条更短的格式（`gpiolib.c:4512`），只打印 `name`。

### §4.3 `flags` 位表（`gpiolib.h:151-170`）

| 位 | 名字 | 谁设 | 语义 |
|---|---|---|---|
| 0 | `FLAG_REQUESTED` | `gpiod_request_commit()` | 被占用。用 `test_and_set_bit()` 做原子加锁，这就是 GPIO 的互斥机制 |
| 1 | `FLAG_IS_OUT` | `gpiod_direction_*()` | 当前是输出 |
| 2 | `FLAG_EXPORT` | `gpiod_export()` | 已导出到 sysfs。源码注释明确写了「protected by sysfs_lock」 |
| 3 | `FLAG_SYSFS` | sysfs `export` 属性 | 是通过 `/sys/class/gpio/export` 申请的（区别于内核驱动申请） |
| 4, 5 | — | — | **空洞**。历史上是 `FLAG_RESERVED` 之类，删掉后位号没有回收 |
| 6 | `FLAG_ACTIVE_LOW` | 查找 flags / `gpiod_toggle_active_low()` | 低电平有效。**只在两处被读**（见 `00-overview.md` §3.3） |
| 7 | `FLAG_OPEN_DRAIN` | 查找 flags | open-drain，软件模拟时靠切方向 |
| 8 | `FLAG_OPEN_SOURCE` | 查找 flags | open-source |
| 9 | `FLAG_USED_AS_IRQ` | `gpiochip_lock_as_irq()` | 这根线被 irq 占了 |
| 10 | `FLAG_IRQ_IS_ENABLED` | `gpiochip_enable/disable_irq()` | irq 当前是 unmask 的。只有 IMMUTABLE 写法才会正确维护 |
| 11 | `FLAG_IS_HOGGED` | `gpiod_hog()` | 由 hog 机制占着，没有真实消费者 |
| 12 | `FLAG_TRANSITORY` | 查找 flags | 睡眠/复位会丢值。注意 `gpiochip_line_is_persistent()` 返回的是它的**反** |
| 13 | `FLAG_PULL_UP` | `gpiod_configure_flags()` | bias 上拉（记录用，实际生效靠 `gc->set_config`） |
| 14 | `FLAG_PULL_DOWN` | 同上 | bias 下拉 |
| 15 | `FLAG_BIAS_DISABLE` | 同上 | bias 断开 |
| 16 | `FLAG_EDGE_RISING` | cdev | cdev 在检测上升沿 |
| 17 | `FLAG_EDGE_FALLING` | cdev | cdev 在检测下降沿 |
| 18 | `FLAG_EVENT_CLOCK_REALTIME` | cdev | 事件时间戳用 `CLOCK_REALTIME`（默认是 MONOTONIC） |
| 19 | `FLAG_EVENT_CLOCK_HTE` | cdev | 事件时间戳来自硬件时间戳引擎（HTE） |

**16–19 这四位是「用户态状态被存进内核核心结构」的例子**：它们只被 cdev 用，但住在 `gpio_desc` 里而不是 `struct line` 里。原因是 `GPIO_V2_GET_LINEINFO_IOCTL` 要能报告**别人**持有的线的边沿检测配置，而它拿不到别人的 `struct line`。

### §4.4 `struct gpio_chip`（`include/linux/gpio/driver.h:415`）

回调清单和「哪些必填」见 `01-api.md` §5。这里只列**非回调字段**和条件编译块：

| 字段 | 说明 |
|---|---|
| `label` / `parent` / `fwnode` / `owner` | 身份信息。`fwnode` 是给「设备节点不是自己的 of_node」的场合用的（如 MFD 子设备） |
| `base` / `ngpio` / `offset` | 编号。`offset` 是「同一个 device 下多个 gpiochip」时用的**bank 内偏移**，不是全局编号 |
| `names` | `const char *const *`，长度必须 `ngpio`。kerneldoc 说名字里可以带**一个** `%u` printk 格式符 |
| `can_sleep` | 慢总线标志 |
| `valid_mask` | 位图；NULL 表示全部有效 |
| `#if IS_ENABLED(CONFIG_GPIO_GENERIC)` 块（`:469-483`） | `read_reg`/`write_reg`/`be_bits`/`reg_dat`/`reg_set`/`reg_clr`/`reg_dir_out`/`reg_dir_in`/`bgpio_dir_unreadable`/`bgpio_bits`/`bgpio_lock`/`bgpio_data`/`bgpio_dir`。**全部由 `bgpio_init()` 填**，驱动不该手改 |
| `#ifdef CONFIG_GPIOLIB_IRQCHIP` 块 | 就一个 `struct gpio_irq_chip irq` |
| `#if defined(CONFIG_OF_GPIO)` 块 | `of_node` / `of_gpio_n_cells` / `of_xlate` |

**`bgpio_lock` 是 `raw_spinlock_t` 而不是 `spinlock_t`**——这样 `PREEMPT_RT` 下它不会变成可睡眠的 rtmutex，MMIO GPIO 才能在硬中断上下文里用。

### §4.5 `struct gpio_irq_chip`（`include/linux/gpio/driver.h:41`）

按「哪种 irqchip 模型用得到」分组，这比按声明顺序列更有用：

**两种模型都要填：**

| 字段 | 说明 |
|---|---|
| `chip` | `struct irq_chip *`。新代码用 `gpio_irq_chip_set_chip()` 赋值，别直接写 |
| `handler` | 流控 handler。级联用 `handle_bad_irq`（由核心改写）/ `handle_simple_irq`；层级模型也要填 |
| `default_type` | 初始触发类型，一般 `IRQ_TYPE_NONE` |
| `valid_mask` / `init_valid_mask` / `first` | 哪些 offset 能当中断；`first` 是静态 irq 号分配的起点（不用动态 domain 时） |
| `lock_key` / `request_key` | lockdep class，由 `gpiochip_add_data()` 宏自动传 |
| `initialized` | **核心自己维护的状态位**，驱动不要写。`gpiochip_to_irq()` 靠它判断 domain 建好了没 |
| `domain` / `domain_ops` | 建好的 irqdomain。核心填 |
| `domain_is_allocated_externally` | 走 `gpiochip_irqchip_add_domain()` 时置位，remove 时就不去释放 domain |
| `irq_enable` / `irq_disable` / `irq_mask` / `irq_unmask` | **不是给驱动填的**——核心把驱动 `irq_chip` 里的同名回调**搬到这里存档**，再用自己的包装替换掉原位（老写法）。IMMUTABLE 写法下这四个保持 NULL |

**级联模型（cascaded）专用：**

| 字段 | 说明 |
|---|---|
| `parent_handler` | 父中断的 handler。NULL 表示是 nested（线程化）而非 chained |
| `parent_handler_data` / `parent_handler_data_array` | 是个 **union**——`per_parent_data` 决定用哪个 |
| `num_parents` / `parents` | 父中断号数组 |
| `map` | 每根 GPIO 对应哪个父中断（一对多映射时用） |
| `threaded` | true 表示用线程化 handler |
| `per_parent_data` | true 时每个父中断有自己的 data |
| `init_hw` | 建 domain 前初始化硬件的钩子（一般是把所有中断先 mask 掉） |

**层级模型（hierarchical，`#ifdef CONFIG_IRQ_DOMAIN_HIERARCHY`）专用：**

| 字段 | 说明 |
|---|---|
| `fwnode` | 建 domain 需要的 fwnode |
| `parent_domain` | **非 NULL 就自动激活层级模型**，这是两种模型的判定开关 |
| `child_to_parent_hwirq` | 必填的翻译回调。kerneldoc 说：某段 GPIO 没有对应父 hwirq 时返回 `-EINVAL`，**并且要在 `valid_mask` 里把它们标掉** |
| `populate_parent_alloc_arg` | 不填时默认用 `gpiochip_populate_parent_fwspec_twocell` |
| `child_offset_to_irq` | 不填时默认返回 offset 本身 |
| `child_irq_domain_ops` | 是**内嵌结构体**不是指针；不填时核心填默认值 |

---

## §5 全局变量与锁

### §5.1 全局变量清单

| 变量 | 类型 | 位置 | 作用 |
|---|---|---|---|
| `gpio_devices` | `LIST_HEAD` | `gpiolib.c:79` | 所有已注册 `gpio_device`。**非 static**，`gpiolib-of.c`/`-sysfs.c`/`-cdev.c` 都要遍历它 |
| `gpio_lock` | `DEFINE_SPINLOCK` | `gpiolib.c:75` | 保护上表 + descs 表更新。**非 static**，同上 |
| `gpio_ida` | `DEFINE_IDA` | `gpiolib.c:57` | 分配 `gpiochipN` 的 N |
| `gpio_devt` | `dev_t` | `gpiolib.c:58` | 字符设备主设备号，`GPIO_DEV_MAX` = 256 |
| `gpio_lookup_list` | `LIST_HEAD` | `gpiolib.c:78` | 板级查找表链表 |
| `gpio_lookup_lock` | `DEFINE_MUTEX` | `gpiolib.c:77` | 保护上一个 |
| `gpio_machine_hogs` | `LIST_HEAD` | `gpiolib.c:82` | 板级 hog 链表 |
| `gpio_machine_hogs_mutex` | `DEFINE_MUTEX` | `gpiolib.c:81` | 保护上一个 |
| `gpio_bus_type` | `struct bus_type` | `gpiolib.c:61` | 退化总线，见 §1.4 |
| `gpio_stub_drv` | `struct device_driver` | `gpiolib.c:4443` | 空壳驱动 |
| `sysfs_lock` | `DEFINE_MUTEX` | `gpiolib-sysfs.c:37` | 保护 sysfs 那套导出状态 + `FLAG_EXPORT` |
| `supinfo_tree` | `struct rb_root` | `gpiolib-cdev.c:578` | cdev 的「补充信息」红黑树（存 debounce 周期等） |
| `supinfo_lock` | `DEFINE_SPINLOCK` | `gpiolib-cdev.c:580` | 保护上一个 |
| `gpio_suffixes[]` | `const char *const []` | `gpiolib.h:81` | `{ "gpios", "gpio" }`——查找 DT 属性名时的后缀表 |

**`gpio_lock` 和 `gpio_devices` 都不是 static**，声明在 `gpiolib.h:128-129`。这是 gpiolib 内部各文件共享状态的方式——一个「模块内部全局」的设计，代价是任何一个 `gpiolib-*.c` 都能改核心链表。

### §5.2 锁的层级和适用范围

```
   最外层：驱动模型的锁（device_lock 等）——gpiolib 不直接碰
      │
      ├── gpio_lookup_lock (mutex)          板级查找表增删/查找
      ├── gpio_machine_hogs_mutex (mutex)   hog 表增删
      ├── sysfs_lock (mutex)                sysfs 导出/取消导出
      │
      ├── gdev->sem (rw_semaphore)          热拔屏障
      │      down_read():  所有 cdev 的 read/poll/ioctl 入口
      │      down_write(): gpiochip_remove()
      │
      ├── linereq->config_mutex (mutex)     串行化同一 fd 上的 ioctl
      │
      └── gpio_lock (spinlock)              最内层，可在原子上下文取
             保护：gpio_devices 链表、descs 表更新、
                   FLAG_* 的读改写（部分路径）
                │
                └── gc->bgpio_lock (raw_spinlock)  MMIO 影子寄存器
```

**三条必须记住的规则：**

1. **`gpio_lock` 是 spinlock，所以持有它时不能调 `gc->` 回调**——I2C/SPI 扩展器的回调会睡眠。核心的做法是「在锁里改标志、出锁后调回调」。
2. **`gdev->sem` 只在用户态路径上取**。内核消费者（`gpiod_get_value()` 等）**完全不走这把锁**——这就是 §1.1 结尾提到的「chip 移除后内核消费者静默返回 0」的成因。
3. **`FLAG_REQUESTED` 的 `test_and_set_bit()` 本身就是锁**（`gpiolib.c:75` 上方的注释原话：「each GPIO's "requested" flag serves as a lock and refcount」）。所以「一根线只能有一个消费者」这个约束不是靠某把 mutex 实现的，是靠一个原子位。

---

## §6 核心操作数据流图

GPIO 的「核心操作」是**一次值读写**。下面是从消费者到硬件的完整路径，两条分支（单线 vs 数组）都画出来：

```
  内核消费者                        gpiolib 核心                        驱动 / 硬件
 ─────────────                   ──────────────────                  ───────────────

 gpiod_set_value(desc, 1)
        │
        ├─ VALIDATE_DESC(desc) ──→ desc/gdev/chip 判空
        │                          chip == NULL 时 return 0（静默！）
        │
        ├─ WARN_ON(desc->gdev->chip->can_sleep)
        │
        └─ gpiod_set_value_nocheck(desc, 1)
                 │
                 ├─ if (test_bit(FLAG_ACTIVE_LOW, &desc->flags))
                 │        value = !value;          ← gpiolib.c:3116-3117
                 │                                   全子系统写侧唯一的极性翻转点
                 │
                 └─ gpiod_set_raw_value_commit(desc, value)
                          │
                          ├─ trace_gpio_value(gpio, 0, value)
                          │
                          ├─ if (test_bit(FLAG_OPEN_DRAIN, …))
                          │     gpio_set_open_drain_value_commit()
                          │        → value ? gc->direction_input()      ← 靠切方向模拟
                          │                : gc->direction_output(…, 0)
                          │
                          ├─ else if (test_bit(FLAG_OPEN_SOURCE, …))
                          │     gpio_set_open_source_value_commit()
                          │
                          └─ else
                                gc->set(gc, offset, value)  ─────────→  写寄存器
                                返回 void，错误无法上报

 ─────────────────────────────────────────────────────────────────────────────────

 gpiod_set_array_value(n, descs, info, bitmap)
        │
        └─ gpiod_set_array_value_complex(raw=false, can_sleep=false, …)
                 │
                 ├─ 快速路径判定：
                 │    array_info != NULL
                 │    && array_info->desc == desc_array
                 │    && !raw
                 │    && !bitmap_empty(array_info->set_mask, …)
                 │    && array_size <= FASTPATH_NGPIO
                 │    且这批线里没有 OD/OS（gpiolib.c:3057-3060 排除）
                 │         │
                 │         ├── 命中 ──→ 一次 bitmap 运算（含 invert_mask 批量翻极性）
                 │         │            gc->set_multiple(gc, mask, bits) ──→ 一次寄存器写
                 │         │
                 │         └── 未命中 ──→ 按 chip 分组，逐组 set_multiple()
                 │                        或逐根 gc->set()
                 │
                 └─ 返回 int（和单线版不同，这个有返回值）

 ─────────────────────────────────────────────────────────────────────────────────

 用户态（一次边沿事件，反向数据流）

  硬件边沿 ──→ GPIO 控制器中断 ──→ 父 irqchip ──→ Linux irq 核心
                                                        │
                                     ┌──────────────────┴────────────────┐
                                     │ edge_irq_handler()  【硬中断】     │
                                     │   line->timestamp_ns = 取时间戳    │
                                     │   return IRQ_WAKE_THREAD           │
                                     └──────────────────┬────────────────┘
                                                        │
                                     ┌──────────────────▼────────────────┐
                                     │ edge_irq_thread()   【线程】       │
                                     │   组 gpio_v2_line_event            │
                                     │   kfifo_put(&req->events, …)       │
                                     │   wake_up_poll(&req->wait, EPOLLIN)│
                                     └──────────────────┬────────────────┘
                                                        │
                                     ┌──────────────────▼────────────────┐
                                     │ linereq_read()      【用户态 read】│
                                     │   down_read(&gdev->sem)  │
                                     │   kfifo_get → copy_to_user         │
                                     └────────────────────────────────────┘
                                                        │
                                                        ▼
                                            用户程序拿到 64 字节事件

  开了软件去抖时，硬中断改走 debounce_irq_handler()，
    事件由 system_wq 上的 debounce_work_func() 延迟产生。
```

**这张图里三个「和直觉不符」的点：**

1. **极性翻转只在一个地方**。你可能以为 `gc->set()` 之前会有个统一的极性处理层——没有，翻转发生在**更上面**的 `gpiod_set_value_nocheck()`，所以 `gpiod_set_raw_value()` 直接跳过了它。
2. **open-drain 不是「配置一次」，而是「每次写值都切方向」**。这也解释了为什么 OD 线被排除在数组快速路径之外——它没法用一次 bitmap 写完成。
3. **数组快速路径的极性翻转是批量做的**（`invert_mask`），单线路径是逐个判断的。两条路径的极性处理代码完全独立——这是一个潜在的不一致点。

---

## §7 命名约定速查

### §7.1 结构体命名

| 前缀 | 含义 | 例子 |
|---|---|---|
| `gpio_*` | gpiolib 的核心概念 | `gpio_chip`、`gpio_device`、`gpio_desc`、`gpio_array`、`gpio_irq_chip`、`gpio_pin_range` |
| `gpiod_*` | 「描述符复数」容器 | `gpiod_lookup`、`gpiod_lookup_table`、`gpiod_hog` |
| `gpio_descs` | 例外：这个是**消费者拿到的数组容器**，不是 `gpio_desc` 的复数内部结构 | `consumer.h:22` |
| `line*` | 只属于 cdev（用户态）的结构 | `line`、`linereq`、`lineevent_state`、`linehandle_state` |
| `gpio_v2_*` | 用户态 ABI v2 结构（在 `include/uapi/linux/gpio.h`） | `gpio_v2_line_request`、`gpio_v2_line_event` |
| `gpio*`（无下划线，v1） | 用户态 ABI v1 结构 | `gpiohandle_request`、`gpioevent_data`、`gpiochip_info` |

### §7.2 「哪个结构体归谁」一页速查

```
        公开（驱动/消费者可见）              私有（只有 drivers/gpio/*.c 可见）
   ─────────────────────────────         ──────────────────────────────────
   include/linux/gpio/driver.h            drivers/gpio/gpiolib.h
     struct gpio_chip                       struct gpio_device
     struct gpio_irq_chip                   struct gpio_desc      ← 消费者只能
     struct gpio_pin_range                  struct gpio_array        拿指针，不能
     union gpio_irq_fwspec                                           访问字段

   include/linux/gpio/consumer.h          drivers/gpio/gpiolib-cdev.c
     struct gpio_descs                      struct line
     enum gpiod_flags                       struct linereq
                                            struct lineevent_state
   include/linux/gpio/machine.h             struct linehandle_state
     struct gpiod_lookup                    struct gpio_chardev_data
     struct gpiod_lookup_table
     struct gpiod_hog                     drivers/gpio/gpiolib-sysfs.c
     enum gpio_lookup_flags                 struct gpiod_data

   include/linux/gpio.h                   drivers/gpio/gpiolib-of.c
     struct gpio  （整数 API 用）            （无自有结构体）

   include/linux/of_gpio.h                include/uapi/linux/gpio.h
     struct of_mm_gpio_chip                 struct gpio_v2_line_request 等
     enum of_gpio_flags                     唯一的 ABI 稳定承诺区
```

**判断一个结构体能不能碰的规则：**在 `include/` 下 = 能碰；在 `drivers/gpio/` 下 = 不能碰，只能拿指针传给 API。`gpio_desc` 就是典型——`01-api.md` 里那几十个 `gpiod_*` 函数存在的全部理由，就是让你不用碰它的字段。

---

## §8 下一步读什么

| 想知道什么 | 去哪 |
|---|---|
| 这些结构体是怎么被填起来的（注册流程） | `03-internals.md` Part A |
| 锁在实际路径上怎么配合的 | `03-internals.md` Part B |
| 这套设计有什么问题 | `03-internals.md` Part C |
| cdev 的那几个结构体怎么用 | `04-extensions.md` §1 |
| 怎么写一个填 `gpio_chip` 的驱动 | `05-usage.md` §2 |
| `gpio_desc` 里的 `name` 从哪来 | `06-devicetree.md` §4（`gpio-line-names`） |
| 怎么在运行时看这些结构体的内容 | `07-debug.md` §3（debugfs） |
| `gpio_chip` / `gpio_device` 为什么分两个（大白话版） | `08-feynman.md` §1 |
