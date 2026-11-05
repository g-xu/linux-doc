## Pinctrl

**Linux 内核 Pinmux（引脚复用）模块**是 Linux **Pinctrl (Pin Control)** 子系统中最核心的功能之一。

在现代嵌入式 SoC（System on Chip）中，引脚（Pin/Pad）是非常昂贵且有限的资源，而芯片内部的外设（如 UART, I2C, SPI, PWM 等）功能非常多。为了解决“僧多粥少”的问题，芯片设计者采用了**引脚复用**技术：同一个物理引脚可以通过寄存器配置，连接到内部不同的控制器上。

Linux 内核通过 Pinctrl 子系统中的 Pinmux 模块来统一管理这种复杂的映射关系。

以下是关于 Linux 内核 Pinmux 模块的详细介绍，涵盖架构、概念、数据结构、设备树配置及工作流程。

---
### 0.Pinctrl 解决的问题

在引入 Pinctrl 子系统之前（早期 ARM Linux 时代），引脚管理通常分散在 arch/arm/mach-xxx 下的板级代码中，存在以下痛点：

1. **引脚复用（Pin Multiplexing/Pinmux）冲突**：
    
    - 同一个物理引脚可能被设计为 UART_TX，也可以被复用为 GPIO 或 SPI_MOSI。
        
    - **解决**：Pinctrl 负责统一管理所有引脚的占用情况。如果两个驱动试图同时请求同一个引脚，Pinctrl 会报错，防止硬件冲突。
        
2. **电气配置（Pin Configuration/Pinconf）繁琐**：
    
    - 除了连接功能，引脚还需要配置电气属性：上拉/下拉（Pull-up/down）、驱动能力（Drive Strength）、转换速率（Slew Rate）等。
        
    - **解决**：Pinctrl 提供了一套通用的宏和接口，屏蔽了底层寄存器的操作细节。
        
3. **代码复用性差与 Device Tree 适配**：
    
    - 旧方法依赖特定的 C 代码宏操作寄存器。
        
    - **解决**：Pinctrl 与设备树（Device Tree）深度结合，使得通过修改 DTS 文件即可改变引脚配置，无需重新编译内核驱动代码。
        

---
### 1. 核心概念与架构

Pinctrl 子系统主要包含三个功能块：
1.  **Pinmux (Pin Multiplexing)**：决定引脚的功能（例如：是作为 UART_TX 还是 GPIO）。
2.  **Pinconf (Pin Configuration)**：决定引脚的电气特性（例如：上拉/下拉、驱动强度、开漏输出）。
3.  **GPIO 交互**：当引脚被用作 GPIO 时，如何与 GPIO 子系统协同工作。

**架构分层：**

```text
+---------------------------+
|      Consumer Drivers     |  (用户：UART, I2C, SPI 驱动)
| (调用 pinctrl_select_state)|
+---------------------------+
             |
             v
+---------------------------+
|     Pinctrl Core          |  (核心层：负责映射管理、状态切换)
| (drivers/pinctrl/core.c)  |
+---------------------------+
             |
             v
+---------------------------+
|    Pinmux / Pinconf       |  (功能层：处理复用和配置逻辑)
+---------------------------+
             |
             v
+---------------------------+
|    Pinctrl Driver         |  (驱动层：直接操作硬件寄存器)
| (厂商驱动: samsung, imx..)|
+---------------------------+
```

### 2. 关键术语

理解 Pinmux 必须掌握以下四个概念：

1.  **Pin (引脚)**：芯片上最小的物理焊盘单位。在内核中通常用一个整数 ID 表示。
2.  **Group (引脚组)**：
    *   因为很多外设需要多个引脚同时工作（例如 UART 需要 TX 和 RX），Pinmux 通常以“组”为单位进行管理。
    *   **Group 是 Pin 的集合**。
3.  **Function (功能)**：
    *   指芯片内部的功能模块，如 "uart0", "i2c1", "spi0"。
    *   **Function 必须与 Group 关联**。即：在这个 Group 上使能这个 Function。
4.  **State (状态)**：
    *   这是消费者（Consumer）视角的概念。一个设备可能有多个状态，最常见的是：
        *   **`default`**：设备正常工作时的引脚配置。
        *   **`sleep`**：设备休眠时的配置（通常设为高阻态或关闭上拉以省电）。
        *   **`idle`**：空闲状态。


Linux Pinctrl 采用了标准的 **Consumer (使用者) - Core (核心层) - Provider (提供者)** 三层架构。
#### Consumer Layer (使用者/客户端)
- **谁是 Consumer？** UART驱动、I2C驱动、网卡驱动等。
- **职责**：它们不需要知道底层寄存器怎么写，只需要在设备树里声明“我需要处于什么状态”。
- **交互方式**：
    - 在设备树（DTS）节点中添加 pinctrl-names 和 pinctrl-0 等属性。
    - 在驱动加载（Probe）时，Linux Driver Core 会自动帮它们向 Pinctrl Core 申请并激活 "default" 状态。
    - 如果需要动态切换（如进入休眠），驱动显式调用 pinctrl_select_state()。

#### Core Layer (核心层/框架层)
- **位置**：drivers/pinctrl/core.c, pinmux.c, pinconf.c。
- **职责**：
    - **资源管理**：维护一个全局的引脚数据库，记录哪个引脚被谁占用了。
    - **解析映射**：解析设备树，将 DTS 里的描述转换为内核中的 struct pinctrl_map 表。
    - **接口抽象**：向下定义了一套 ops 接口规范，向上提供统一 API。

#### Provider Layer (提供者/驱动层)
- **谁是 Provider？** 芯片厂商提供的 Pinctrl 驱动（如 pinctrl-samsung.c, pinctrl-rockchip.c）。
- **职责**：
    - 定义芯片具体有多少个 Pin，支持哪些 Group 和 Function。
    - 实现 Core 层定义的操作回调函数，直接读写硬件寄存器。
---

### 3. 数据结构 (Data Structures)

Pinctrl 驱动主要定义在 `include/linux/pinctrl/pinctrl.h` 和 `pinmux.h` 中。

#### 3.1 `struct pinctrl_desc` (控制器描述符)
这是 Pinctrl 驱动的总入口，注册时需要填充它。
```c
struct pinctrl_desc {
    const char *name;
    const struct pinctrl_ops *pctlops;  // 全局控制操作
    const struct pinmux_ops *pmxops;    // 复用操作 (Pinmux 核心)
    const struct pinconf_ops *confops;  // 配置操作 (Pinconf)
    struct pinctrl_pin_desc *pins;      // 支持的所有引脚列表
    unsigned int npins;
    // ...
};
```

#### 3.2 `struct pinmux_ops` (复用操作集)
这是实现 Pinmux 功能必须实现的回调函数集：
```c
struct pinmux_ops {
    // 检查某个 Function 是否可以应用到某个 Group 上
    int (*get_functions_count)(struct pinctrl_dev *pctldev);
    const char *(*get_function_name)(struct pinctrl_dev *pctldev, unsigned selector);
    int (*get_function_groups)(struct pinctrl_dev *pctldev, unsigned selector,
                               const char * const **groups, unsigned * const num_groups);

    // 【核心】将指定的 Function 应用到指定的 Group 上 (写硬件寄存器)
    int (*set_mux)(struct pinctrl_dev *pctldev, unsigned func_selector, unsigned group_selector);

    // 请求/释放 GPIO 时的回调 (处理 GPIO 与 Pinmux 的冲突或联动)
    int (*gpio_request_enable)(struct pinctrl_dev *pctldev, struct pinctrl_gpio_range *range, unsigned offset);
    void (*gpio_disable_free)(struct pinctrl_dev *pctldev, struct pinctrl_gpio_range *range, unsigned offset);
    // ...
};
```

---

### 4. 设备树 (Device Tree) 配置

在现代 Linux 中，Pinmux 的映射关系主要通过 DTS 描述。

#### 4.1 服务端 (Pinctrl Controller Node)
定义引脚的**配置节点**。这里告诉控制器：“把 Pin 1 和 Pin 2 组合成 UART0 模式”。

```dts
/* SoC dtsi 文件 */
pinctrl: pinctrl@10000000 {
    compatible = "vendor,soc-pinctrl";
    reg = <0x10000000 0x1000>;
    
    /* 定义一个状态节点 */
    uart0_default: uart0_default {
        /* 厂商自定义属性，通常包含引脚ID和复用值 */
        pins = "PA0", "PA1"; 
        function = "uart0";
        bias-pull-up; /* 属于 Pinconf */
    };
};
```

#### 4.2 客户端 (Consumer Device Node)
外设驱动通过标准属性引用上面的配置。

```dts
/* 板级 dts 文件 */
&uart0 {
    status = "okay";
    /* 核心：引用 pinctrl 定义的状态 */
    pinctrl-names = "default", "sleep";
    pinctrl-0 = <&uart0_default>; /* 对应 default */
    pinctrl-1 = <&uart0_sleep>;   /* 对应 sleep */
};
```

---

### 5. 工作流程与调用链

当 Linux 启动并加载一个外设驱动（例如 UART 驱动）时，Pinmux 是如何生效的？

#### 步骤 1: 驱动 Probe
UART 驱动调用 `probe` 函数。Linux 驱动核心（Driver Core）在调用 `probe` **之前**，会自动处理 Pinctrl。

#### 步骤 2: 获取 Pinctrl 句柄
内核调用 `pinctrl_bind_pins()`：
1.  解析设备树中的 `pinctrl-0`。
2.  在 Pinctrl Core 中查找对应的 Handle。

#### 步骤 3: 状态选择 (`default` state)
内核默认会尝试将设备切换到 `"default"` 状态：
1.  调用 `pinctrl_select_state(p, default_state)`。
2.  Pinctrl Core 找到该状态对应的 **Map**（映射表）。
3.  **核心动作**：Core 调用底层驱动的 `ops->set_mux(func, group)`。
4.  **硬件操作**：SoC 厂商驱动在 `set_mux` 中操作具体的寄存器，将 PA0/PA1 的多路复用器切到 UART 模式。

#### 步骤 4: 运行时切换 (可选)
如果 UART 驱动支持电源管理，当系统休眠时，驱动可能会调用：
`pinctrl_select_state(p, sleep_state)`。
此时底层驱动会将引脚切到省电模式。

---

### 6. 常见 API 汇总

#### 消费者 API (Consumer API)
驱动工程师在编写外设驱动时使用，但在有了设备树后，通常不需要显式调用（Core 会自动处理 default 状态），除非做动态切换。

*   `devm_pinctrl_get(dev)`: 获取设备的 pinctrl 句柄。
*   `pinctrl_lookup_state(p, name)`: 查找状态（如 "sleep"）。
*   `pinctrl_select_state(p, state)`: 切换到指定状态。

#### 生产者 API (Provider API)
SoC 厂商编写 Pinctrl 驱动时使用。

*   `pinctrl_register(desc, dev, driver_data)`: 注册 Pinctrl 控制器。
*   `pinctrl_add_gpio_range()`: 注册 GPIO 范围（用于 GPIO 和 Pinmux 的映射）。

---

### 7. Pinmux 与 GPIO 的关系 (易混淆点)

这是一个非常重要的概念。

*   **物理上**：一个 Pad 既可以做 GPIO，也可以做 UART。
*   **软件上**：
    *   当该 Pad 用作 UART 时，由 **Pinmux** 负责，GPIO 模块通常无法控制电平。
    *   当该 Pad 用作 GPIO 时，**Pinmux** 需要先将引脚复用功能切到 "GPIO 模式"，然后控制权转交给 **GPIO 子系统**（通过 `gpiod_set_value` 等 API）。

**内核机制**：
当我们在代码中调用 `gpiod_request()` 申请一个 GPIO 时，GPIO 子系统后端通常会调用 `pinctrl_gpio_request()`。这会触发 Pinmux 驱动的 `gpio_request_enable` 回调，确保引脚被自动切到 GPIO 模式，防止冲突。

### 总结

Linux Pinmux 模块是连接**物理引脚**与**内部控制器**的桥梁。
1.  **分层清晰**：Consumer (DTS引用) -> Core (映射管理) -> Driver (寄存器操作)。
2.  **State 驱动**：通过 `default`、`sleep` 等状态实现引脚功能的生命周期管理。
3.  **DTS 为主**：绝大部分配置工作都在设备树中完成，驱动代码本身只需关注业务逻辑。



## APIs

Linux 内核 Pinctrl 子系统的 API 主要分为两类：一类是给**普通外设驱动（Consumer）** 使用的，用于请求引脚状态；另一类是给 **SoC Pinctrl 驱动（Provider）** 使用的，用于注册控制器和处理 GPIO 交互。

以下是详细的 API 汇总表格：

### 1. 消费者 API (Consumer APIs)
**适用对象**：UART, I2C, SPI, Network 等外设驱动的开发者。
**主要头文件**：`&lt;linux/pinctrl/consumer.h&gt;`

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`devm_pinctrl_get`** | 获取句柄 | 获取指定设备（Device）关联的 Pinctrl 句柄（`struct pinctrl *`）。 | **最常用**。资源托管型，驱动卸载时自动释放。内核 Probe 时通常会自动调用，无需手动调用。 |
| **`pinctrl_get`** | 获取句柄 | 同上，但非托管资源。 | 需要手动调用 `pinctrl_put` 释放。 |
| **`pinctrl_put`** | 释放句柄 | 释放 Pinctrl 句柄。 | 对应 `pinctrl_get`。 |
| **`pinctrl_lookup_state`** | 查找状态 | 在获取的 Pinctrl 句柄中，查找指定名称的状态（如 "default", "sleep"）。 | 返回 `struct pinctrl_state *`。如果找不到会返回 ERR_PTR。 |
| **`pinctrl_select_state`** | **切换状态** | **(核心)** 将引脚切换到指定的状态。这会触发底层写寄存器操作。 | 驱动中最常调用的函数，用于运行时改变引脚模式（如休眠时切到 sleep）。 |
| **`pinctrl_pm_select_default_state`**| PM 辅助 | 快捷函数：查找并切换到 "default" 状态。 | 常用于 PM Resume 阶段。 |
| **`pinctrl_pm_select_sleep_state`** | PM 辅助 | 快捷函数：查找并切换到 "sleep" 状态。 | 常用于 PM Suspend 阶段。 |
| **`pinctrl_pm_select_idle_state`** | PM 辅助 | 快捷函数：查找并切换到 "idle" 状态。 | |
| **`devm_pinctrl_get_select_default`**| 组合操作 | 获取句柄 + 查找 default + 切换状态 的“三合一”函数。 | 极简驱动初始化使用。 |

---

### 2. 提供者 API (Provider/Driver APIs)
**适用对象**：SoC 厂商（如 Samsung, Rockchip, STM32）编写 Pinctrl 控制器驱动的开发者。
**主要头文件**：`&lt;linux/pinctrl/pinctrl.h&gt;`, `&lt;linux/pinctrl/pinmux.h&gt;`, `&lt;linux/pinctrl/pinconf.h&gt;`

| API 函数接口                                                            | 功能分类    | 详细功能描述                                       | 核心注意事项                                        |
| :------------------------------------------------------------------ | :------ | :------------------------------------------- | :-------------------------------------------- |
| **`devm_pinctrl_register`**<br>**`devm_pinctrl_register_and_init`** | **注册**  | 向内核注册一个 Pinctrl 控制器。需要传入 `pinctrl_desc` 描述符。 | 资源托管型。成功后核心层接管该硬件。                            |
| **`pinctrl_register`**                                              | 注册      | 同上，非托管。                                      | 需手动调用 `pinctrl_unregister`。                   |
| **`pinctrl_unregister`**                                            | 注销      | 注销 Pinctrl 控制器。                              |                                               |
| **`pinctrl_add_gpio_range`**                                        | GPIO 映射 | 注册 GPIO 编号范围到 Pinctrl 引脚 ID 的映射关系。           | **非常重要**。没有这个，GPIO 子系统无法通过 Pinctrl 控制引脚方向或复用。 |
| **`pinctrl_remove_gpio_range`**                                     | GPIO 映射 | 移除映射关系。                                      |                                               |
| **`pinctrl_utils_reserve_map`**                                     | 辅助工具    | 为 Pinctrl 映射表分配内存。                           | 编写驱动时的通用辅助函数。                                 |
| **`pinctrl_utils_add_map_mux`**                                     | 辅助工具    | 向映射表中添加一个复用（Mux）配置。                          | 用于在没有设备树的旧架构中硬编码配置。                           |
| **`pinctrl_utils_add_map_configs`**                                 | 辅助工具    | 向映射表中添加一个配置（Conf）。                           | 同上。                                           |

---

### 3. GPIO 子系统交互 API
**适用对象**：GPIO 驱动（`drivers/gpio/`）或者 GPIO 核心层，用于在请求 GPIO 时自动调整 Pinmux。
**主要头文件**：`&lt;linux/pinctrl/consumer.h&gt;` (虽然叫 consumer，但这部分主要由 GPIO Lib 调用)

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`pinctrl_gpio_request`** | 请求 GPIO | 当用户申请 GPIO (`gpiod_get`) 时调用。通知 Pinctrl 该引脚被用作 GPIO。 | 这会触发底层驱动的 `gpio_request_enable` 回调，将引脚复用切到 GPIO 模式。 |
| **`pinctrl_gpio_free`** | 释放 GPIO | 当用户释放 GPIO 时调用。 | |
| **`pinctrl_gpio_direction_input`** | 方向控制 | 设置 GPIO 为输入方向。 | 有些硬件在 Pinctrl 层配置输入/输出比在 GPIO 模块更底层。 |
| **`pinctrl_gpio_direction_output`**| 方向控制 | 设置 GPIO 为输出方向。 | |

---

### 4. 通用 Pinconf 解析 API (Generic Pinconf)
**适用对象**：Pinctrl 驱动开发者。用于解析设备树中的标准属性（如 `bias-pull-up`, `drive-strength`）。
**主要头文件**：`&lt;linux/pinctrl/pinconf-generic.h&gt;`

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`pinconf_generic_parse_dt_config`**| DT 解析 | 解析设备树节点中的标准 Pinconf 属性，并生成配置数组。 | 极大地简化了驱动代码，不需要手动解析 `bias-pull-up` 等字符串。 |
| **`pinconf_to_config_param`** | 数据转换 | 从 `unsigned long` 类型的 config 数据中提取参数类型（Param）。 | Pinctrl 核心使用 packed 格式存储配置，需此函数解包。 |
| **`pinconf_to_config_argument`** | 数据转换 | 从 `unsigned long` 类型的 config 数据中提取参数值（Argument）。 | 例如提取驱动强度的具体数值。 |

---

### 💡 核心结构体补充

在使用上述 API 时，不可避免地会接触到以下结构体：

| 结构体名称 | 描述 |
| :--- | :--- |
| **`struct pinctrl_desc`** | **总描述符**。注册驱动时必须填充，包含 `name`, `pins`, `pctlops`, `pmxops`, `confops`。 |
| **`struct pinctrl_ops`** | **全局操作**。如 `get_groups_count`, `get_group_name`。 |
| **`struct pinmux_ops`** | **复用操作**。如 `set_mux` (核心回调，写寄存器切换功能), `gpio_request_enable`。 |
| **`struct pinconf_ops`** | **配置操作**。如 `pin_config_set` (写寄存器配置电气属性)。 |
| **`struct pinctrl_state`** | **状态对象**。代表 "default" 或 "sleep" 等状态的句柄。 |

### 📝 最佳实践总结

1.  **Consumer (外设驱动)**：
    *   **不要手动调用** `pinctrl_get` 和 `select_state("default")`。Linux 驱动框架（Driver Core）在调用你的 `probe` 之前已经帮你做好了。
    *   **仅在** 需要动态切换（如休眠、特定模式）时，才手动调用 `pinctrl_select_state`。

2.  **Provider (Pinctrl 驱动)**：
    *   尽量使用 **`devm_pinctrl_register`**。
    *   尽量使用 **Generic Pinconf** (`pinconf_generic_parse_dt_config`) 来解析设备树，不要发明私有的设备树属性。
    *   务必正确实现 **`gpio_request_enable`**，否则 GPIO 和特定功能（如 UART）同时竞争一个引脚时会出错。

## pin/group/function 概念解惑
在 Linux 内核的 **Pinctrl (Pin Control)** 子系统中，Pinmux（引脚复用）是核心功能之一。现代 SoC（片上系统）内部集成了成百上千个控制器（如 UART、I2C、SPI、USB 等），但芯片封装周围的物理引脚（Pins）数量是有限的。

为了解决这个问题，芯片设计者采用了**复用（Multiplexing）**技术：一个物理引脚可以在不同时间或不同配置下，连接到芯片内部不同的控制器上。

为了管理这种复杂的映射关系，Linux 定义了三个核心概念：**Pin（引脚）**、**Group（引脚组）** 和 **Function（功能）**。

---

### 1. Pin (引脚)

**定义**：
Pin 是 Pinctrl 子系统管理的**最小物理单元**。它对应芯片封装上的一个金属触点（Pad、Finger 或 Ball）。

**特点**：
*   **唯一标识**：在内核驱动中，每个 Pin 都有一个唯一的 ID（通常是一个整数索引）和一个名称（字符串，如 `"PAD_GPIO19"` 或 `"ball_A5"`）。
*   **物理属性**：Pin 本身承载电气特性，如上拉/下拉电阻（Pull-up/down）、驱动能力（Drive Strength）等（这部分称为 Pinconf，与 Pinmux 相辅相成）。
*   **资源有限**：这是最稀缺的资源，所有的内部功能都要争夺这些物理出口。

### 2. Group (引脚组 / Pingroup)

**定义**：
Group 是**一组 Pin 的集合**。

**为什么需要 Group？**
大多数外设功能（Function）通常不仅仅需要一个引脚。例如：
*   **UART** 至少需要 TX 和 RX 两个引脚。
*   **I2C** 需要 SDA 和 SCL 两个引脚。
*   **SPI** 可能需要 CLK, MOSI, MISO, CS 四个引脚。

**特点**：
*   **原子性**：在 Pinmux 的概念里，我们通常不单独把一个 Function 映射到一个 Pin 上，而是把一个 Function 映射到一个 Group 上。
*   **灵活性**：
    *   一个 Group 可以只包含 **1个 Pin**（例如用于 GPIO 功能，或者某些单线协议）。
    *   一个 Pin 可以属于**多个不同的 Group**（这是复用的本质）。
*   **命名**：通常以功能或位置命名，例如 `uart0_grp`（包含 pin 10, pin 11）。

### 3. Function (功能)

**定义**：
Function 代表芯片内部的**外设控制器逻辑**（IP Block），它产生信号并希望通过引脚传输到外部。

**示例**：
*   `uart0`（串口0）
*   `i2c1`（I2C控制器1）
*   `spi0`（SPI控制器0）
*   `gpio`（通用输入输出）

大多数碰到的芯片，一个功能只有一个选择，比如uart0 只能从pin 10/11两个引脚引出，但是复杂的芯片，有可能为了布线方便，可以有多种选择，比如uart0 能从pin 10/11两个引脚引出，也可以从 100/101两个引脚引出，功能还是同一个，但是需要在两个group里面选择一个。

**特点**：
*   **抽象性**：Function 是纯逻辑的概念，它不知道数据最终从哪个物理引脚出去，它只知道“我要发数据”。
*   **多选一**：一个 Function 可能有多种“出路”。例如，SoC 设计时可能允许 `UART0` 从芯片左边的引脚出来，也可以从芯片右边的引脚出来（为了方便布线）。这就意味着一个 Function 可以关联多个可选的 Group。

---

### 4. 三者之间的关系（核心逻辑）

理解 Pinmux 的关键在于理解这三者是如何“配对”的。

**公式：**
> **Pinmux 映射 = 选择一个 Function + 选择该 Function 支持的一个 Group**

**图解逻辑：**

1.  **多对多关系**：
    *   一个 **Function** (如 UART0) 可以选择 **Group A** 或 **Group B**。
    *   一个 **Pin** (如 Pin 5) 可以属于 **Group A** (用于 UART)，也可以属于 **Group C** (用于 SPI)。

2.  **冲突检测**：
    *   如果你配置 UART0 使用 Group A (包含 Pin 1, Pin 2)，同时又配置 SPI0 使用 Group C (包含 Pin 2, Pin 3)。
    *   内核会报错，因为 **Pin 2** 被冲突占用了。Pinctrl 子系统会自动处理这种互斥锁。

**生活类比：**
*   **Pin** = 火车站的**铁轨**（物理通道）。
*   **Function** = **火车班次**（如“复兴号 G123”，这是逻辑上的运输服务）。
*   **Group** = **站台组合**（如“1号和2号站台”）。
    *   *场景*：G123 次列车（Function）进站，它可以停靠在 1/2 号站台（Group A），也可以停靠在 3/4 号站台（Group B）。一旦选定，铁轨（Pin）就被占用了。

---

### 5. 在设备树（Device Tree）中的体现

在实际的 Linux 开发中，开发者主要通过设备树（DTS）来配置这些关系。

假设我们有一个 SoC，它的 UART0 可以从两组引脚出来：
*   Group A: Pin 10 (TX), Pin 11 (RX)
*   Group B: Pin 20 (TX), Pin 21 (RX)

**SoC 厂商的 Pinctrl 驱动代码（定义可选列表）：**
*   Function: `uart0`
*   Groups supported by uart0: `["uart0_grp_a", "uart0_grp_b"]`

**用户的设备树配置（DTS）：**

```dts
/* 定义节点 */
&pinctrl {
    /* 配置1：我想让 UART0 从 Pin 10/11 出来 */
    uart0_state_default: uart0_default {
        pinmux {
            function = "uart0";      /* 选功能 */
            groups = "uart0_grp_a";  /* 选组 (这组包含了 Pin 10, 11) */
        };
        /* 这里还可以配置 Pinconf (电气属性) */
        bias-pull-up;
    };
};

/* 应用节点 */
&uart0 {
    pinctrl-names = "default";
    pinctrl-0 = <&uart0_state_default>; /* 引用上面的配置 */
    status = "okay";
};
```

### 6. 总结

*   **Pin**: 物理的针脚，是资源争夺的对象。
*   **Group**: 为了完成特定任务而捆绑在一起的 Pin 集合。
*   **Function**: 芯片内部想要使用引脚的逻辑模块。

**Pinmux 的工作流就是**：驱动告诉内核，“我要用 **Function X**”，并且我要让它走 **Group Y** 的路径。内核检查 Group Y 里的 Pins 没被别人占用，然后修改硬件寄存器，接通内部线路。

## 现实差异
在实际开发中（特别是做嵌入式 Linux 或 Android BSP 时）之所以感觉 **Function 和 Group 往往是一一对应的**，主要有以下三个原因。

---

### 原因一：Linux 框架的“强行统一” (Wrapper)

Linux 的 Pinctrl 子系统是一个**“通用的抽象层”**。它为了兼容世界上最复杂的芯片（比如手机芯片，引脚复用极度复杂），设计了一套“大而全”的标准流程：
> **必须选一个 Function，然后必须选一个 Group。**

但是，对于很多结构简单的芯片（比如一些低成本的工业级 SoC 或早期的 ARM 芯片）：
*   **硬件现实**：UART0 **只能**从 Pin 1 和 Pin 2 出来，硬件上根本没有第二条路。
*   **软件妥协**：驱动工程师为了适配 Linux 的这套标准，不得不“脱裤子放屁”。
    *   定义 Function：`uart0`
    *   硬凑一个 Group：`uart0_0_grp` (仅包含 Pin 1, 2)
    *   **结果**：可选列表里只有这一个 Group。

**结论**：你在 DTS 里写的 `function="uart0"; groups="uart0_0_grp";`，虽然形式上是“选择”，但实际上是“单选题”。这就是为什么你觉得 Function 和 Group 好像锁死了一样。

---

### 原因二：硬件设计的成本考量 (Silicon Cost)

制造芯片时，**“灵活性” = “晶体管数量” = “钱”**。

如果设计一个芯片，允许 UART0 从芯片的东南西北四个方向随便哪个引脚出来：
1.  芯片内部需要布设大量的线路（走线拥堵）。
2.  需要大量的多路选择器（MUX 逻辑电路）。
3.  这会增加芯片面积和功耗。

**现实情况**：
绝大多数芯片设计者会做**“局部最优解”**。
*   他们会把 UART0 固定在芯片左上角，最多给两组引脚选择（为了方便 PCB 走线避开障碍）。
*   极少会有 Function 拥有 3 个以上的 Group 选项。
*   甚至很多次要的 Function（比如 PWM、特定的 I2C），为了省钱，硬件上就只画了一条路。

所以，你碰到的很多情况确实是：**硬件上就是一对一的，Group 的概念在这里仅仅是为了满足 Linux 驱动的数据结构要求。**

---

### 原因三：引脚控制器（Pin Controller）的两种流派

这是最容易让人困惑的技术细节。硬件实现 Pinmux 其实有两种流派，而 **Group** 这个概念在第二种流派里才显眼，在第一种流派里非常别扭。

#### 流派 A：基于引脚的复用 (Per-Pin Muxing) —— 如 TI OMAP, NXP i.MX, ST STM32
这是最常见的。
*   **硬件逻辑**：每个 Pin 都有一个寄存器，里面有个字段叫 `MUX_MODE`。
    *   Pin 1 寄存器写 0 -> GPIO
    *   Pin 1 寄存器写 1 -> UART_TX
*   **Linux 里的别扭**：
    在这种硬件里，其实根本没有“Group”的概念。但是 Linux Pinctrl 强行要有 Group。
    于是驱动通常会**把“每一个 Pin”定义为一个“Group”**。
    *   Group: `pin1_grp` (这就叫 Group 了)
    *   Function: `uart0_tx`
    *   **这种情况下，Group 退化成了 Pin，你自然感觉不到 Group 的存在。**

#### 流派 B：基于组的复用 (Group Muxing) —— 如 MediaTek, 部分 Qualcomm
这种常见于复杂的手机 SoC。
*   **硬件逻辑**：有一个全局寄存器，或者一个模块寄存器。
    *   设置 `UART0_SEL = 1`：立刻同时把 Pin 5,6,7,8 切换给 UART0。
*   **Linux 里的契合**：
    这时候 Group 概念非常有意义。因为硬件真的是**打包切换**的。你不能单独把 Pin 5 切走，必须一组一组切。

---

### 总结：为什么很少见到“多选一”？

1.  **硬件太简单**：很多控制器只有唯一的物理出口，没有备胎。
2.  **驱动在“伪装”**：驱动为了迎合 Linux 框架，把“一对一”的关系硬写成了“Function -> Group”的格式。
3.  **技术流派不同**：如果是类似 STM32 或 i.MX 这种每个引脚单独配置的芯片，Group 的概念被弱化了（Group 大小通常为 1）。

**什么时候你会真正用到 Group 的多选一？**
当你画板子（PCB Layout）发现：“糟糕，默认的 SPI 引脚和旁边的 DDR 线冲突了，没法走线！”
这时你查 Datasheet 发现：“谢天谢地，这个 SPI 控制器还支持切换到芯片背面的另一组引脚（Group B）。”
只有在这个时刻，`groups` 的选择才展现出它的光辉。其他 90% 的时间，它确实只是个例行公事的配置项。