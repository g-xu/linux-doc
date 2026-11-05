## GPIO

Linux内核的GPIO（General Purpose Input/Output）子系统是嵌入式Linux开发中最常用、最基础的模块之一。随着内核版本的演进，GPIO子系统经历了一次重大的架构变革：从早期的**基于整数（Integer-based）**的API，转向了现代的**基于描述符（Descriptor-based）**的API。

以下从架构、核心概念、驱动开发（消费者与提供者）、以及用户空间接口四个方面详细介绍。

---

### 1. 架构概览 (Architecture)

Linux GPIO 子系统采用了分层架构，旨在屏蔽底层硬件差异，为上层提供统一接口。

1.  **用户空间 (Userspace)**: 应用程序通过 `/dev/gpiochipN` (新) 或 `/sys/class/gpio` (旧) 控制GPIO。
2.  **内核消费者 (Kernel Consumers)**: 具体的设备驱动（如 LED驱动、按键驱动、传感器驱动），它们需要“使用”GPIO来控制外设。
3.  **GPIO 核心层 (GPIO Core / gpiolib)**: 负责管理所有的 GPIO 资源，处理冲突，管理访问权限，处理“高电平有效/低电平有效”的逻辑转换。
4.  **GPIO 控制器驱动 (GPIO Controller Drivers / Providers)**: 也就是“提供者”。直接操作 SoC 的寄存器或 I2C/SPI 扩展芯片，实现具体的设置电平、读取状态的操作。

---

### 2. 核心变革：从 "Legacy" 到 "Descriptor"

这是理解现代 Linux GPIO 的关键。

*   **Legacy API (旧接口 - `&lt;linux/gpio.h&gt;`)**:
    *   使用整数（int）来代表 GPIO（如 `gpio_request(105, "led")`）。
    *   **缺点**: 全局命名空间容易冲突；并不感知“低电平有效”；不知道哪个GPIO对应哪个设备。
    *   **状态**: **已弃用 (Deprecated)**，但仍在旧代码中存在。

*   **Descriptor API (新接口 - `&lt;linux/gpio/consumer.h&gt;`)**:
    *   使用 `struct gpio_desc *` 指针来代表 GPIO。
    *   **优点**: 绑定到具体的 `struct device`；自动处理 Active Low（逻辑值 vs 物理值）；类型安全。
    *   **状态**: **推荐标准**。

---

### 3. 核心 API 详解

#### A. 消费者 API (Consumer API)
这是驱动工程师最常使用的部分（比如你要写一个通过 GPIO 控制复位的 Wi-Fi 芯片驱动）。

| 功能 | API 函数 | 说明 |
| :--- | :--- | :--- |
| **获取 GPIO** | `gpiod_get(dev, con_id, flags)` | 根据设备树中的属性名获取 GPIO 描述符。 |
| **释放 GPIO** | `gpiod_put(desc)` | 释放资源（通常使用 devm_ 自动管理，无需手动释放）。 |
| **设置方向** | `gpiod_direction_output(desc, val)` | 设置为输出，并输出初始值 `val`。 |
| | `gpiod_direction_input(desc)` | 设置为输入。 |
| **读写数值** | `gpiod_set_value(desc, val)` | 设置**逻辑**电平（0或1）。内核会根据设备树的 `GPIO_ACTIVE_LOW` 自动翻转物理电平。 |
| | `gpiod_get_value(desc)` | 读取**逻辑**电平。 |
| **休眠上下文** | `gpiod_set_value_cansleep(...)` | 如果 GPIO 挂在 I2C/SPI 扩展器上，操作会导致休眠，必须用此函数。 |
| **映射中断** | `gpiod_to_irq(desc)` | 将 GPIO 转换为 Linux 中断号。 |

**代码示例 (Consumer):**

```c
/* 在 probe 函数中 */
struct gpio_desc *reset_gpio;

/* 获取名为 "reset-gpios" 的 GPIO，初始化为低电平 */
reset_gpio = devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW);
if (IS_ERR(reset_gpio))
    return PTR_ERR(reset_gpio);

/* 拉高复位 (如果设备树定义了 ACTIVE_LOW，物理上这里可能是输出低电平) */
gpiod_set_value(reset_gpio, 1);
```

#### B. 提供者 API (Provider API)
这是 SoC 厂商或 GPIO 扩展芯片（如 PCF8574）驱动开发者使用的部分。核心结构体是 `struct gpio_chip`。

*   **核心结构体 `struct gpio_chip`**:
    *   `parent`: 父设备。
    *   `label`: 芯片名称。
    *   `ngpio`: GPIO 数量。
    *   `base`: 起始编号（现代驱动通常设为 -1，动态分配）。
    *   `set`: 回调函数，设置电平。
    *   `get`: 回调函数，读取电平。
    *   `direction_output/input`: 回调函数，设置方向。

**代码示例 (Provider):**

```c
static int my_gpio_set(struct gpio_chip *chip, unsigned int offset, int value)
{
    /* 操作硬件寄存器设置电平 */
    struct my_data *data = gpiochip_get_data(chip);
    writel(value, data->base + offset);
    return 0;
}

/* 在驱动 probe 中注册 */
struct gpio_chip *gc = &data->gc;
gc->label = "my-gpio";
gc->ngpio = 32;
gc->set = my_gpio_set;
gc->get = my_gpio_get;
/* ... 其他回调 ... */

devm_gpiochip_add_data(dev, gc, data);
```

---

### 4. 设备树集成 (Device Tree)

新的 GPIO API 强依赖设备树来建立连接。

**提供者 (Controller) 节点:**
```dts
gpio0: gpio@10000 {
    compatible = "vendor,soc-gpio";
    reg = <0x10000 0x100>;
    gpio-controller;
    #gpio-cells = <2>; /* 第1个cell是编号，第2个cell是标志位 */
};
```

**消费者 (Consumer) 节点:**
```dts
my_device {
    compatible = "my,device";
    /* 引用上面的 gpio0，编号为 5，标志位为 低电平有效 */
    reset-gpios = <&gpio0 5 GPIO_ACTIVE_LOW>;
};
```

**注意**: 驱动代码中的 `devm_gpiod_get(dev, "reset", ...)` 会自动匹配 DTS 中的 `reset-gpios` 属性。内核会自动去掉后缀 `-gpios` 进行匹配。

---

### 5. 用户空间接口 (User Space Interfaces)

#### A. Sysfs (旧接口 /sys/class/gpio)
*   **用法**: `echo 10 > /sys/class/gpio/export`，然后读写 `value` 文件。
*   **状态**: **Deprecated (不推荐使用)**。
*   **缺点**: 无法保证原子操作；权限管理混乱；无法批量操作；基于全局整数编号，不稳定。

#### B. 字符设备 (新接口 /dev/gpiochipN)
*   **用法**: 使用 `ioctl` 操作字符设备。通常不直接调用 `ioctl`，而是使用 **`libgpiod`** 库。
*   **优点**:
    *   支持原子操作。
    *   支持一次操作多个 GPIO (Bulk access)。
    *   支持事件监听（中断反馈到用户态）。
    *   资源生命周期绑定到文件描述符（进程退出自动释放 GPIO）。

**libgpiod 命令行工具示例:**
```bash
# 查看所有 GPIO 芯片
gpiodetect

# 查看 gpiochip0 的详细信息
gpioinfo gpiochip0

# 设置 gpiochip0 的第 3 号线为 1
gpioset gpiochip0 3=1

# 读取 gpiochip0 的第 3 号线
gpioget gpiochip0 3
```

### 总结

1.  **内核态开发**：**必须**摒弃 `gpio_request` 等旧接口，全面转向 `gpiod_get` / `gpiod_set_value` 等描述符接口。这能让你的代码更通用，且能自动处理电平极性。
2.  **用户态开发**：**不要**再写脚本操作 `/sys/class/gpio` 了，请使用 **libgpiod** 库及其工具。
3.  **主要陷阱**：区分 `_cansleep` 函数。如果你的 GPIO 是来自 USB 或 I2C 扩展芯片的，控制它需要发 I2C 包（耗时且会休眠），此时绝对不能在中断上下文或自旋锁中使用普通的 `gpiod_set_value`，而要用 `gpiod_set_value_cansleep`。

## 内核实现
Linux内核的GPIO子系统实现框架非常精妙，它位于 `drivers/gpio/` 目录下，核心文件是 **`gpiolib.c`**。

为了深入理解其实现框架，我们需要剥开表层的 API，深入到**数据结构的关系**、**核心对象的生命周期**以及**内部处理流程**。

---

### 1. 总体架构分层

从内部实现的角度，GPIO 子系统可以分为三层：

1.  **接口适配层 (Interface Layer)**:
    *   向上提供给其他驱动的 API (`gpiod_*`)。
    *   向上提供给用户空间的字符设备接口 (`/dev/gpiochipN`) 和 Sysfs 接口。
    *   这一层负责参数检查、权限管理、逻辑电平转换。

2.  **核心管理层 (Core Framework - GPIOLIB)**:
    *   维护全局的 GPIO 视图。
    *   管理 `gpio_device`（设备实例）和 `gpio_desc`（描述符）的内存和状态。
    *   处理 Device Tree / ACPI 的资源解析与映射 (`gpiolib-of.c`, `gpiolib-acpi.c`)。

3.  **芯片驱动层 (Chip Driver Layer)**:
    *   实现 `struct gpio_chip` 中的回调函数。
    *   处理硬件寄存器读写、中断控制器配置。

---

### 2. 核心数据结构及其关系

这是理解框架的钥匙。内核通过三个主要结构体来组织 GPIO：

#### A. `struct gpio_desc` (原子单位：引脚)
这是内核内部代表**某一个具体 GPIO 引脚**的结构体。它是“基于描述符”API 的核心。

```c
struct gpio_desc {
    struct gpio_device  *gdev;   /* 反向指针：指向所属的 GPIO 设备 */
    unsigned long       flags;   /* 状态标志位 */
    const char          *label;  /* 申请者的标签 (用于 debug) */
    const char          *name;   /* 线路名称 (来自设备树 gpio-line-names) */
};
```
*   **`flags` 的关键作用**:
    *   `FLAG_REQUESTED`: 表示该 GPIO 是否被占用。
    *   `FLAG_ACTIVE_LOW`: **核心逻辑**。如果置位，`gpiod_set_value(1)` 实际上会输出低电平。
    *   `FLAG_OPEN_DRAIN` / `FLAG_OPEN_SOURCE`: 开漏/开集配置。
    *   `FLAG_IS_OUT`: 当前方向。

#### B. `struct gpio_chip` (驱动接口：操作方法)
这是底层驱动注册给核心层的“操作函数表”和“硬件属性表”。

```c
struct gpio_chip {
    const char          *label;
    struct device       *parent;
    struct module       *owner;

    /* 核心回调函数 */
    int (*request)(struct gpio_chip *chip, unsigned offset);
    void (*free)(struct gpio_chip *chip, unsigned offset);
    int (*get_direction)(struct gpio_chip *chip, unsigned offset);
    int (*direction_input)(struct gpio_chip *chip, unsigned offset);
    int (*direction_output)(struct gpio_chip *chip, unsigned offset, int value);
    int (*get)(struct gpio_chip *chip, unsigned offset);
    void (*set)(struct gpio_chip *chip, unsigned offset, int value);
    
    /* 关键属性 */
    u16                 base;    /* 全局编号基址 (现代驱动设为 -1) */
    u16                 ngpio;   /* GPIO 数量 */
    bool                can_sleep; /* 是否可能休眠 (I2C/SPI 扩展器必须设为 true) */
};
```

#### C. `struct gpio_device` (内核对象：容器)
这是 gpiolib 内部用来管理一个 GPIO 控制器实例的容器。它将 `gpio_chip` 和 `gpio_desc` 数组封装在一起。

```c
struct gpio_device {
    int                 id;          /* ID 号 (gpiochip0, gpiochip1...) */
    struct device       dev;         /* 对应的 Linux device 模型对象 */
    struct cdev         chrdev;      /* 字符设备对象 (/dev/gpiochipN) */
    struct gpio_chip    *chip;       /* 指向驱动提供的 chip */
    struct gpio_desc    *descs;      /* 描述符数组，大小为 chip->ngpio */
    int                 ngpio;       /* 缓存 chip 的 ngpio */
};
```

**关系总结**:
*   系统中有多个 `gpio_device`。
*   一个 `gpio_device` 包含一个 `gpio_chip` 和一个 `gpio_desc` 数组。
*   `gpio_desc` 数组中的每一项代表物理引脚。

---

### 3. 核心实现流程解析

#### A. 注册流程 (`devm_gpiochip_add_data`)
当一个 GPIO 控制器驱动（如 SoC 的 pinctrl 驱动或 I2C 扩展器驱动）加载时：

1.  **分配 `gpio_device`**: 内核分配一个新的 `gpio_device` 实例。
2.  **分配描述符数组**: 根据 `chip->ngpio` 的大小，`kcalloc` 分配 `gpio_desc` 数组。
3.  **初始化描述符**: 遍历数组，设置默认状态，初始化 `desc->gdev` 指针。
4.  **注册字符设备**: 调用 `cdev_device_add`，生成 `/dev/gpiochipN` 节点。
5.  **添加到全局链表**: 将 `gpio_device` 挂入 gpiolib 的全局设备链表。

#### B. 查找与获取流程 (`gpiod_get`)
当消费者驱动调用 `reset = gpiod_get(dev, "reset", ...)` 时：

1.  **解析固件节点**:
    *   内核调用 `gpiolib-of.c` 中的函数，去解析 Device Tree。
    *   查找属性 `reset-gpios`。
    *   解析属性值 `&lt;&gpio0 5 GPIO_ACTIVE_LOW&gt;`。
    *   **Phandle 解析**: 通过 `&gpio0` 找到对应的 `gpio_device`。
    *   **Args 解析**: 获取偏移量 `5` 和标志位 `GPIO_ACTIVE_LOW`。
2.  **定位描述符**:
    *   在 `gpio_device->descs` 数组中找到索引为 5 的 `gpio_desc`。
3.  **更新标志位**:
    *   将 `GPIO_ACTIVE_LOW` 记录到 `desc->flags` 中。
4.  **占用检查**:
    *   检查 `FLAG_REQUESTED`。如果未被占用，置位该标志，返回描述符指针；否则返回 `-EBUSY`。

#### C. 操作流程 (设置电平)
当调用 `gpiod_set_value(desc, 1)` 时：

1.  **逻辑转换**:
    *   检查 `desc->flags` 中的 `FLAG_ACTIVE_LOW`。
    *   如果置位，逻辑 `1` 变为物理 `0`；否则保持 `1`。
2.  **上下文检查**:
    *   检查 `desc->gdev->chip->can_sleep`。
    *   如果在原子上下文（如中断中）调用了可能休眠的 GPIO（如 I2C 扩展器），内核会抛出 `WARN_ON` 警告栈。
3.  **调用驱动**:
    *   执行 `chip->set(chip, offset, value)`。
    *   驱动层读写寄存器完成操作。

---

### 4. 关键机制实现细节

#### A. 休眠与原子操作 (The `can_sleep` Trap)
这是 GPIO 框架为了适配 I2C/SPI 等慢速总线扩展器而设计的机制。

*   **Fast Path**: 直接读写内存映射寄存器（SoC 内部 GPIO）。`can_sleep = false`。可以使用 `spinlock` 保护。
*   **Slow Path**: 需要发起 I2C/SPI 传输。`can_sleep = true`。只能使用 `mutex` 保护。
*   **实现**: gpiolib 提供了两套 API (`gpiod_set_value` vs `gpiod_set_value_cansleep`)。`_cansleep` 版本允许在函数内部调用 `mutex_lock` 或 `schedule`。

#### B. 中断处理 (GPIO as IRQ)
很多 GPIO 可以被配置为中断源。
*   **`struct gpio_irq_chip`**: 在 `gpio_chip` 中集成了中断控制器的功能。
*   **IRQ Domain**: GPIO 子系统利用内核的 `irq_domain` 机制，将 GPIO 编号（0-31）映射为 Linux 虚拟中断号（Virtual IRQ Number）。
*   **流程**:
    1.  驱动调用 `gpiod_to_irq(desc)`。
    2.  gpiolib 调用 `irq_create_mapping(domain, offset)`。
    3.  返回一个 IRQ 号，驱动随后调用 `request_irq`。
    4.  当硬件触发中断，GPIO 驱动的中断处理函数读取状态寄存器，通过 `generic_handle_irq` 分发给注册的处理函数。

#### C. 用户空间字符设备 (`/dev/gpiochipN`)
这是新版 ABI 的实现，位于 `drivers/gpio/gpiolib-cdev.c`。

*   **ioctl 机制**: 它不使用 read/write，而是完全依赖 `ioctl`。
*   **Line Handle**: 用户通过 `GPIO_GET_LINEHANDLE_IOCTL` 请求一组 GPIO。内核会返回一个新的**匿名文件描述符 (Anonymous FD)**。
    *   这个 FD 代表了对那几根 GPIO 线的所有权。
    *   只要这个 FD 不关闭，内核就认为 GPIO 被占用了。
    *   当进程崩溃或退出，FD 被系统自动关闭，触发 `release` 回调，自动释放 GPIO 资源。**这是比 sysfs 先进得多的地方。**

### 总结

Linux GPIO 模块的实现框架是一个典型的**“面向对象”C语言设计**：
1.  **封装**: 用 `gpio_desc` 封装状态，用 `gpio_device` 封装实例。
2.  **抽象**: 用 `gpio_chip` 定义统一的接口标准。
3.  **分离**: 将逻辑电平（Active Low）在核心层处理，将物理电平在驱动层处理。

这种架构使得上层驱动可以编写出完全不依赖具体硬件（SoC无关、极性无关）的代码，极大地提高了可移植性。

## APIs

Linux内核 GPIO 子系统目前推荐使用 **基于描述符 (Descriptor-based, `gpiod_`)** 的 API，旧的基于整数 (Legacy, `gpio_`) 的 API 已被弃用。

以下是按功能分类的详细 API 汇总表：

### 1. 消费者接口 (Consumer API)
**场景**：编写普通设备驱动（如 LED、按键、传感器），需要使用 GPIO 控制外设。
**头文件**：`#include &lt;linux/gpio/consumer.h&gt;`

| API 函数名 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| **`gpiod_get`** | 获取一个 GPIO 描述符。 | `dev`: 设备指针<br>`con_id`: 属性名 (如 "reset" 对应 dts 中的 "reset-gpios")<br>**返回**: `struct gpio_desc *` |
| **`devm_gpiod_get`** | **[推荐]** 资源托管版本的获取函数。 | 驱动卸载时自动释放，无需手动调用 `put`。<br>常用变体: `devm_gpiod_get_optional` (允许返回 NULL)。 |
| **`gpiod_put`** | 释放 GPIO 描述符。 | 仅在使用非 `devm_` 版本获取时需要调用。 |
| **`gpiod_direction_input`** | 将 GPIO 配置为输入模式。 | |
| **`gpiod_direction_output`** | 将 GPIO 配置为输出模式，并设置初始值。 | `value`: 初始逻辑电平 (0 或 1)。 |
| **`gpiod_get_value`** | 读取 GPIO 的**逻辑电平**。 | **自动处理极性**：如果 DTS 中配置了 `GPIO_ACTIVE_LOW`，物理低电平会返回 1。 |
| **`gpiod_set_value`** | 设置 GPIO 的**逻辑电平**。 | **自动处理极性**：写 1 可能对应物理低电平 (如果配置了 Active Low)。 |
| **`gpiod_to_irq`** | 获取与该 GPIO 对应的中断号 (IRQ Number)。 | 用于随后调用 `request_irq` 申请中断。 |
| **`gpiod_set_consumer_name`** | 修改 GPIO 的所有者名称。 | 显示在 `/sys/kernel/debug/gpio` 中，便于调试。 |

---

### 2. 休眠上下文接口 (Sleeping / Can-Sleep API)
**场景**：当 GPIO 控制器位于慢速总线（如 I2C/SPI 扩展器）上时，读写寄存器会导致休眠，**必须**使用这组 API。
**注意**：这组 API 不能在中断上下文或自旋锁 (Spinlock) 保护的区域调用。

| API 函数名 | 功能描述 | 区别与备注 |
| :--- | :--- | :--- |
| **`gpiod_get_value_cansleep`** | 读取 GPIO 值（允许休眠）。 | 内部可能调用 `mutex_lock`，如果用于 SoC 内部 GPIO，效果等同于普通版。 |
| **`gpiod_set_value_cansleep`** | 设置 GPIO 值（允许休眠）。 | 用于控制如 PCA9555 等 I2C 扩展芯片下的 GPIO。 |
| **`gpiod_direction_output_cansleep`** | 配置输出（允许休眠）。 | |
| **`gpiod_direction_input_cansleep`** | 配置输入（允许休眠）。 | |

---

### 3. 提供者接口 (Provider / Controller API)
**场景**：编写 GPIO 控制器驱动（SoC 厂商或 GPIO 扩展芯片驱动）。
**头文件**：`#include &lt;linux/gpio/driver.h&gt;`

| API 函数名 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| **`devm_gpiochip_add_data`** | **[推荐]** 注册一个新的 GPIO 控制器 (Chip)。 | `gc`: `struct gpio_chip` 指针，包含操作函数表 (`set`, `get`, `dir_out` 等)。<br>`data`: 驱动私有数据指针。 |
| **`gpiochip_add_data`** | 注册 GPIO 控制器（非托管）。 | 需手动在卸载时调用 `gpiochip_remove`。 |
| **`gpiochip_remove`** | 注销 GPIO 控制器。 | |
| **`gpiochip_generic_request`** | 通用的请求回调实现。 | 如果硬件没有特殊的请求逻辑，可直接赋值给 `struct gpio_chip` 的 `.request`。 |
| **`gpiochip_generic_free`** | 通用的释放回调实现。 | 赋值给 `.free`。 |
| **`gpiochip_get_data`** | 从 `struct gpio_chip` 中取回私有数据。 | 在 `set`/`get` 回调函数中常用于获取寄存器基地址。 |

---

### 4. 批量操作接口 (Array / Batch API)
**场景**：一次性获取或操作多个 GPIO，效率更高。

| API 函数名 | 功能描述 | 备注 |
| :--- | :--- | :--- |
| **`devm_gpiod_get_array`** | 获取一组 GPIO 描述符。 | 返回 `struct gpio_descs *` (包含数组和数量)。<br>对应 DTS 属性如 `data-gpios` (包含多个引脚)。 |
| **`gpiod_set_array_value`** | 同时设置多个 GPIO 的值。 | 适用于并行总线操作。 |
| **`gpiod_get_array_value`** | 同时读取多个 GPIO 的值。 | |

---

### 5. 旧接口 (Legacy API - 已弃用)
**场景**：维护旧代码。**新开发严禁使用**。
**头文件**：`#include &lt;linux/gpio.h&gt;`

| API 函数名 | 功能描述 | 缺点/迁移指南 |
| :--- | :--- | :--- |
| **`gpio_request`** | 申请一个 GPIO 号 (整数)。 | 必须硬编码整数编号，容易冲突。请迁移到 `gpiod_get`。 |
| **`gpio_free`** | 释放 GPIO。 | |
| **`gpio_direction_output`** | 设置输出。 | 不支持 Active Low 自动转换。 |
| **`gpio_set_value`** | 设置物理电平。 | 这里的 1 永远代表高电平，驱动需自己处理极性逻辑。 |
| **`gpio_to_irq`** | 整数编号转中断号。 | |

### 核心数据结构简述

1.  **`struct gpio_desc`**: **(核心)** 代表一个具体的 GPIO 引脚。对用户不透明，驱动持有指针。
2.  **`struct gpio_chip`**: 代表一个 GPIO 控制器实例（硬件层面的芯片）。
3.  **`struct gpio_device`**: 内核内部使用的容器，包含 `gpio_chip` 和 `gpio_desc` 数组。

### 常用开发流程示例
1.  **DTS 配置**:
    ```dts
    reset-gpios = <&gpio1 5 GPIO_ACTIVE_LOW>;
    ```
2.  **驱动代码**:
    ```c
    struct gpio_desc *reset;
    /* 1. 获取 (自动识别 active low) */
    reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW);
    /* 2. 操作 (逻辑 1 = 复位 = 物理低电平) */
    gpiod_set_value(reset, 1);
    /* 3. 延时 */
    msleep(10);
    /* 4. 释放复位 */
    gpiod_set_value(reset, 0);
    ```

## qemu 验证
如果想要在qemu上验证gpio，可以通过实验 gpio-mockup模块来进行实验。
首先在 /arch/arm64/configs/defconfig里面增加 `CONFIG_GPIO_MOCKUP=m`，这样可以编译为模块，然后在qemu里面通过`insmod gpio-mockup.ko gpio_mockup_ranges=-1,32,-1,32` 来进行加载。顺利加载后，就可以从 /sys/ 里面找到 gpio-mocker模块。
- 如果需要使用`/sys/class/gpio/export`来操作，需要在defconfig里面打开 `CONFIG_GPIO_SYSFS` `CONFIG_EXPERT` 两项，否则是找不到的
- 但是liinux5以后的版本，默认是关闭了通过  `/sys/class/gpio/export` 来导出并操作的方式，现在这种方式是不推荐的，需要自行编译libgpiod模块来使用，下面是安装步骤
	- git clone代码，并且切换到1.6分支，这个目前依赖最小
	-  安装工具
	  ```sudo apt update; sudo apt install autoconf automake libtool pkg-config autoconf-archive```
	- 执行 ./autogen.sh  //有可能需要安装工具：apt install autoconf-archive
	- mkdir _install
	-  执行	  
		
		  
```
		./configure \
		    --host=aarch64-linux-gnu \
		    --prefix=$(pwd)/_install \
		    --enable-tools=yes \
		    --enable-static=yes \
		    --disable-shared \
		    --enable-bindings-cxx=no \
		    --enable-bindings-python=no \
		    LDFLAGS="-static" \
		    CFLAGS="-static"
```

		  
	- make LDFLAGS="-all-static" //强制编译为静态链接库
	- make install
- 将上面生成的_install/bin/gpioxxx等一系列工具，复制到rootfs里面的bin目录下，然后qemu启动以后，加载了gpio模块之后，就可以使用gpio工具来操作了
	- `gpioset -m signal gpiochip1 0=1 &`
	- `gpioset -m signal gpiochip1 2=1 &`
	- `cat /sys/kernel/debug/gpio`  ->应该可以看到相应的输出
- 注意：如果qemu上面同时使用 gpioset， 然后gpioget，会提示访问冲突，需要使用sysfs写，gpio读：`echo  1 /sys/kernel/debug/gpio-mockup/gpiochip0/2` 然后`gpioget giochip1 2` 或者相反的方式来验证
- 