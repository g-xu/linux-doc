---
title: 扩展机制
---

# I2C 子系统（五）扩展框架：mux / algos / slave / 用户态 / SMBus 扩展 / 枚举后端

> **本篇的组织轴**：**一节一个独立框架**，彼此之间没有调用关系，按需跳读即可，不必从头读到尾。
> 它们唯一的共同点是**接入点都是 `i2c_adapter` 或 `i2c_client`**——也就是 [`02-datastructures.md`](02-datastructures.md) 里那两个结构体，
> 核心（[`03-internals.md`](03-internals.md)）对它们的存在基本无感知。
>
> 裸写的 `:N` 指**本节正在讨论的那个文件**，跨文件一律写全名。

---

## 0. 七个框架，以及 arm64 上到底会碰到哪几个

这七节的重要性差得很远。先给一张分诊表，避免在 arm64 上永远用不到的东西上花时间：

| 节 | 框架 | 一句话 | arm64 相关度 |
|---|---|---|---|
| §1 | i2c-mux | 一个 mux 通道 = 一个独立 `i2c_adapter` | **高**，PCA9548 之类在板子上很常见；传感器/解串器/HDMI bridge 内部的旁路门也走这套（§1.8） |
| §2.1 | algo-bit | 两个 GPIO 变 I²C 主机 | **中**，`i2c-gpio` 和 DRM 的 DDC 都用它 |
| §2.2/2.3 | algo-pca / algo-pcf | 并行总线转 I²C 的外挂协处理器 | **极低**，需要 PCA9564/PCF8584 这类芯片，现代 arm64 SoC 自带控制器 |
| §3 | slave 框架 | 让本机响应对端寻址，模拟一颗芯片 | **中**，BMC 通信、硬件自测会用 |
| §4 | i2c-dev | `/dev/i2c-N` + ioctl | **高**，调试第一工具 |
| §5 | SMBus Alert / Host Notify / SPD | SMBus 规范里超出基础事务的三个机制 | **低**（Alert/Host Notify 要硬件配合）／**不适用**（SPD 是 x86 DMI 路径） |
| §6 | 枚举后端 | client 从哪里被建出来 | DT **必需**；ACPI 与板级信息 **不适用** |
| §7 | i2c-stub | 无硬件的虚拟 SMBus 适配器 | **中**，写芯片驱动时先用它跑通逻辑 |

**和核心的关系**：这七个框架都不在 `03` 的时间线主干上，而是挂在主干的某一点上——
§1/§2 挂在 `03` §A2（适配器注册）之前，由控制器/mux 驱动准备好 `i2c_adapter` 再交给核心；
§6 挂在 `03` §A2 内部（`i2c_register_adapter()` 里调枚举后端）；
§3/§5 挂在 `03` §A4 之后（client 已经存在，再加从机或 Alert 语义）；
§4/§7 是最外层，一个从上面开洞给用户态，一个从下面伪造一个适配器。

---

## 1. 多路复用器框架（i2c-mux）

### 1.1 问题与解法

一条物理 I²C 总线的 7 位地址空间只有 128 个编码，扣掉规范保留的部分，内核允许普通 client 使用的只有 **0x08–0x77 共 112 个**（判定见 `i2c-core-base.c:769-781` 的 `i2c_check_7bit_addr_validity_strict()`，注释里逐条列了保留地址的用途）。而且同地址的芯片不能共存。实际板子上常见的需求：

- 4 个相同型号的温度传感器（地址都是 0x48）要挂在同一控制器下；
- 某些芯片必须与其它芯片隔离（时序不兼容、或需要独占）；
- 两个 SoC 共用一条总线，需要仲裁。

解法：插一颗 mux 芯片（如 PCA9548，1 进 8 出），或用 GPIO/pinctrl 切换。内核的抽象是：**每个 mux 通道对上层表现为一个完整的、独立的 `i2c_adapter`**，从此上层芯片驱动完全不知道 mux 的存在。

```
    物理总线 i2c-0                    /dev/i2c-0
        │
   ┌────┴────┐
   │ PCA9548 │  地址 0x70
   └─┬─┬─┬─┬─┘
     │ │ │ └── 通道 3 → i2c-4 "i2c-0-mux (chan_id 3)"  /dev/i2c-4
     │ │ └──── 通道 2 → i2c-3
     │ └────── 通道 1 → i2c-2
     └──────── 通道 0 → i2c-1 ── 0x48 温度传感器
                                 （i2c-2/3/4 上也各有一个 0x48，互不冲突）
```

### 1.2 先破一个歧义：两个都叫 "mux priv" 的东西

读 mux 代码最容易被绕进去的地方是**有两个不相关的东西都叫 priv**：

| 名字 | 位置 | 是什么 | 谁分配 | 谁用 |
|---|---|---|---|---|
| `struct i2c_mux_priv` | `i2c-mux.c:32-37`，**框架私有**，头文件里看不到 | **每个通道一份**：内嵌的 `i2c_adapter` + 内嵌的 `i2c_algorithm` | `i2c_mux_add_adapter()` 里 `kzalloc(sizeof(*priv))`（`:297`） | 只有 i2c-mux 框架自己（通过 `adap->algo_data` 反查） |
| `muxc->priv` / `i2c_mux_priv(muxc)` | `include/linux/i2c-mux.h:25`、`:46-49`，**公开** | **每个 mux 器件一份**：mux 驱动自己的状态（regmap 句柄、GPIO 描述符……），大小由 `i2c_mux_alloc()` 的 `sizeof_priv` 参数决定 | `i2c_mux_alloc()` 里 `devm_kzalloc(struct_size(muxc, adapter, max_adapters) + sizeof_priv)`（`:248-249`），并让 `muxc->priv` 指向柔性数组 `adapter[]` **之后**那块（`:251-252`） | mux 驱动，通过 `i2c_mux_priv(muxc)` 取 |

**这两个 `priv` 的分配语句长得很像但完全是两回事**：`struct_size(...) + sizeof_priv` 那一句属于 `i2c_mux_alloc()`（`:239-269`），分配的是 `muxc`；`i2c_mux_add_adapter()` 里那一句只是 `kzalloc(sizeof(*priv))`，一次一个通道。

### 1.3 实现机制：内嵌适配器 + 运行时构造算法

`i2c_mux_add_adapter()`（`i2c-mux.c:283-438`）的核心：

```c
struct i2c_mux_priv {              /* i2c-mux.c:32-37 */
    struct i2c_adapter   adap;     /* 内嵌，不是指针 */
    struct i2c_algorithm algo;     /* 内嵌，运行时填 */
    struct i2c_mux_core *muxc;
    u32 chan_id;
};
```

```
i2c_mux_add_adapter(muxc, force_nr, chan_id, class)                :283
 ├─ 检查还有没有通道位置（num_adapters < max_adapters）             :292
 ├─ priv = kzalloc(sizeof(*priv), GFP_KERNEL)   ← 一个通道一份        :297
 │
 ├─ ── 按锁风格装 4 个包装函数 ──                             :308-326
 │     if (muxc->mux_locked) {              /* mux-locked */
 │         priv->algo.master_xfer = i2c_mux_master_xfer;   ← 用 i2c_transfer()
 │         priv->algo.smbus_xfer  = i2c_mux_smbus_xfer;
 │         （atomic 变体同理，:314-315）
 │     } else {                             /* parent-locked */
 │         priv->algo.master_xfer = __i2c_mux_master_xfer;  ← 用 __i2c_transfer()
 │         priv->algo.smbus_xfer  = __i2c_mux_smbus_xfer;
 │         （atomic 变体同理，:323-324）
 │     }
 │     priv->algo.functionality = i2c_mux_functionality;    ← 转发给父总线
 │
 ├─ snprintf(priv->adap.name, ..., "i2c-%d-mux (chan_id %d)",
 │           i2c_adapter_id(parent), chan_id);                :329-330
 ├─ priv->adap.algo      = &priv->algo;
 ├─ priv->adap.algo_data = priv;         ← 包装函数靠这个反查 priv
 ├─ 继承父总线属性：retries / timeout / quirks              :335-337
 ├─ lock_ops = mux_locked ? &i2c_mux_lock_ops
 │                        : &i2c_parent_lock_ops;           :338-341
 ├─ class 共享合理性检查（同一父总线上不能有两个通道声明同一 class）:344-347
 │
 ├─ ── DT 节点关联 ──                                       :355-392
 │     在 mux 的 of_node 下找子节点：
 │       muxc->arbitrator → 找名为 "i2c-arb"
 │       muxc->gate       → 找名为 "i2c-gate"
 │       否则             → 找名为 "i2c-mux"
 │     再在其下按 reg == chan_id 找通道节点（:380-388）；
 │     还兼容"老式"写法（通道节点直接是 mux 的子节点）：
 │       找不到分组节点时回退成 mux_node = 器件节点本身（:375-376）
 │     ⚠ 陷阱：分组节点上若自己写了 reg，会被判为"老式条目"而丢弃
 │       （:368-372 of_node_put + mux_node = NULL），随后走上面那条回退，
 │       于是在**错误的层级**上找 reg == chan_id：
 │         若分组节点那个假 reg 恰好等于 chan_id → 把分组节点当成通道节点；
 │         否则 → priv->adap.dev.of_node 为 NULL，通道上的从设备一个都不会被枚举。
 │       两种结果都不报错。DTS 写法与这个陷阱见 06 §4
 ├─ （:397-400 是 ACPI companion 关联，x86 路径，不展开）
 │
 ├─ ── 注册 ──                                              :402-419
 │     force_nr ? i2c_add_numbered_adapter(&priv->adap)
 │              : i2c_add_adapter(&priv->adap)
 │
 └─ ── sysfs 链接 ──                                        :421-428
       子适配器上建 "mux_device" → 指向 mux 设备
       mux 设备上建 "channel-<N>" → 指向子适配器
```

四个包装函数是**两两成对**的，`mux-locked` 与 `parent-locked` 各一套。以 master_xfer 这一对为例：

```c
/* i2c_mux_master_xfer()，:58-75，mux-locked 用这个 */
static int i2c_mux_master_xfer(struct i2c_adapter *adap,
                               struct i2c_msg msgs[], int num)
{
    struct i2c_mux_priv *priv = adap->algo_data;   /* ← 靠 algo_data 反查 */
    struct i2c_mux_core *muxc = priv->muxc;
    struct i2c_adapter *parent = muxc->parent;
    int ret;

    /* Switch to the right mux port and perform the transfer. */
    ret = muxc->select(muxc, priv->chan_id);       /* 切通道 */
    if (ret >= 0)
        ret = i2c_transfer(parent, msgs, num);     /* 加锁版本 */
    if (muxc->deselect)
        muxc->deselect(muxc, priv->chan_id);       /* 可选：切回默认 */

    return ret;
}
```

`__i2c_mux_master_xfer()`（`:39-56`，parent-locked 用这个）与上面**逐字相同，只差第 52 行那一处**：它调 `__i2c_transfer()`（不加锁版本）。smbus_xfer 那一对（`i2c_mux_smbus_xfer` `:99-120` / `__i2c_mux_smbus_xfer` `:77-97`）也是同样的关系。

> 这四个函数**不做任何 I²C 协议工作**，只干"select → 转发给父总线 → deselect"。`functionality` 也是直接转发（`i2c_mux_functionality()`，`:122-128`），所以 mux 子适配器的能力位图永远等于父总线的。

**拆除**：`i2c_mux_del_adapters()`（`:441-461`）从后往前遍历 `muxc->adapter[]`，每个通道依次删两个 sysfs 链接、`i2c_del_adapter()`、`of_node_put()`、`kfree(priv)`。注意**没有**"删单个通道"的 API——通道只能整批删。

### 1.4 两种锁风格的实际差异

| | **mux-locked**（`I2C_MUX_LOCKED`） | **parent-locked**（默认） |
|---|---|---|
| `lock_ops` | `i2c_mux_lock_ops`（`i2c-mux.c:271`） | `i2c_parent_lock_ops`（`:277`） |
| lock_bus 行为 | 只取 `parent->mux_lock`；仅当调用者要 `I2C_LOCK_ROOT_ADAPTER` 才额外取父总线锁（`:148-151`） | **总是**取 `parent->mux_lock` **和** 父总线锁（`:179-187`） |
| select/deselect 里必须用 | **普通** `i2c_transfer()`（会自己加父总线锁） | **不加锁的** `__i2c_transfer()`（否则死锁，父总线锁已被自己持有） |
| xfer 包装用 | `i2c_transfer()` | `__i2c_transfer()` |
| 父总线在 select→xfer→deselect 期间 | **可以**被其它传输插入 | **被独占** |
| 全树使用者数量 | 8 处 | 14 处（逐个列举见 §1.7 / §1.8） |

**判据不是"切换走不走 I²C"，而是"切换那条路径上能不能用 `__` 不加锁变体"**——这一点很容易搞反：

- `i2c-mux-pca954x.c` 的切换**确实走 I²C**（往 mux 芯片写一个字节），但它是 **parent-locked**（`i2c_mux_alloc(..., 0, ...)`，`:428`）。因为它自己直接调 `__i2c_smbus_xfer()`（`:220`），源码注释就写着 *"Write to mux register. Don't use i2c_transfer()/i2c_smbus_xfer()"*（`:213-214`）。能用不加锁变体，就选独占性更强的 parent-locked。`i2c-mux-pca9541.c` 同理（`:104`，注释在 `:96`）。
- `i2c-mux-ltc4306.c` 是 `drivers/i2c/muxes/` 里唯一的 **mux-locked**（`:229`）。它的切换经过 **regmap**（`regmap_update_bits()`，`:117`/`:127`/`:148`），而 regmap-i2c 没有"不加锁"入口，无法在持有父总线锁的情况下调用——只能退让成 mux-locked，代价就是 select→xfer→deselect 之间父总线不再独占。这正是 `i2c-topology.rst` 的 PL2 说的那种"追求干净方案时可能做不到"的情形。

换句话说：**mux-locked 是一个不得已的退让，不是一个优化选项。**

图示（引自 `Documentation/i2c/i2c-topology.rst:60-67` 的拓扑）：

```
                   .----------.     .--------.
    .--------.     |   mux-   |-----| dev D1 |
    |  root  |--+--|  locked  |     '--------'
    '--------'  |  |  mux M1  |--.  .--------.
                |  '----------'  '--| dev D2 |
                |  .--------.       '--------'
                '--| dev D3 |
                   '--------'
```

在 mux-locked 情形下，访问 D1 的时间线是：

```
   取 mux_lock
     ├─ select（一次完整的、自己加锁的父总线传输）
     │     ┈┈┈ 此刻父总线锁已释放，访问 D3 的传输可以插进来 ┈┈┈
     ├─ 数据传输（又一次自己加锁的父总线传输）
     │     ┈┈┈ 又可以插进来 ┈┈┈
     └─ deselect
   放 mux_lock
```

在 parent-locked 情形下，整个三步在父总线锁内完成，D3 的访问必须等待。

### 1.5 mux 的陷阱清单

`Documentation/i2c/i2c-topology.rst`（共 412 行）列举了各种拓扑组合下的问题。摘出最需要记住的：

**mux-locked 的四个限制（`i2c-topology.rst:88-122`，原文编号 ML1–ML4）**

1. **不能把 mux-locked mux 作为 parent-locked mux 的父节点** —— 会破坏子 mux "根适配器在整个事务期间被锁住"的假设。
2. **两个非兄弟的 mux-locked mux，若其子总线上有地址冲突，则拓扑不安全** —— 针对 mux-one 后 0x42 的 select-xfer-deselect 可能与针对 mux-two 后 0x42 的同类操作交错。
3. **不能用于自动关闭的门/mux**（用了一次传输就自动复位的硬件）—— 无关传输会插进来提前把门关掉。
4. **mux 驱动里任何非 I²C 的、会改变 mux 状态的操作，必须自己锁根适配器** —— 否则别人的传输进行中你改了 mux 状态，总线上会出垃圾数据。

**parent-locked 的两个限制（`i2c-topology.rst:169-187`，原文编号 PL1–PL2）**

1. **不能把 parent-locked mux 作为另一个 mux 的子节点** —— 破坏的是**这个 parent-locked mux 自己**的假设：它以为"从我的 select 到真正的数据传输之间，根适配器没有别人在用"。如果父 mux 的 select 本身要发 I²C 传输，这个假设就不成立（原文举的例子：子 mux 是自动关闭型，而父 mux 的 select 要发 I²C）。父是 mux-locked 时尤甚，但父是 parent-locked 也可能出问题。
2. **select/deselect 里调用 gpio / pinctrl / regmap / iio 等子系统时，这些子系统内部引发的任何 I²C 传输都必须是不加锁的** —— 这一点文档原文说得很悲观："这可能很难做到，甚至在追求干净方案时是不可能的（maybe even impossible if an acceptably clean solution is sought）"。§1.4 里 ltc4306 退让成 mux-locked，就是被这一条逼的。

> **ML1 与 PL1 是同一个禁忌组合的两面**：都在说"mux-locked 父 + parent-locked 子"这个组合坏掉了，只是 ML1 从父的角度、PL1 从子的角度各写了一遍。记住这一个组合，两条就都记住了。

**为什么这些陷阱无法自动检查**：锁风格是数据（一个 bit），拓扑是运行时构造的，而"select 里会不会间接发起加锁传输"取决于跨子系统的调用链。lockdep 只能在死锁真的发生时报出来，不能静态拒绝一个危险拓扑。

### 1.6 `i2c_root_adapter()`（`i2c-mux.c:213-237`）

从任意 `struct device` 沿 `dev->parent` 链向上走，先找到第一个 `dev->type == &i2c_adapter_type` 的祖先（`:222-227`，注释说明这样才能穿过 mfd 这类中间设备），再沿 `i2c_parent_is_i2c_adapter()` 一路走到最顶（`:232-234`）。mux 驱动需要它来在"非 I²C 方式改 mux 状态"时锁住整条物理总线（对应上面 ML4）。

### 1.7 现有 mux 驱动一览（`drivers/i2c/muxes/`，2,852 行）

锁风格一栏全部从各驱动传给 `i2c_mux_alloc()` 的 flags 参数读出（`I2C_MUX_LOCKED` 定义在 `include/linux/i2c-mux.h:42`），不是推测：

| 驱动 | 硬件 / 机制 | `i2c_mux_alloc()` flags | 锁风格 |
|---|---|---|---|
| `i2c-mux-pca954x.c` | PCA9540/9542/9543/9544/9545/9546/9547/9548 等，通过 I²C 写控制寄存器切换 | `0`（`:428`） | parent-locked |
| `i2c-mux-pca9541.c` | PCA9541 主控切换器（两个 master 抢一条总线） | `I2C_MUX_ARBITRATOR`（`:307-308`） | parent-locked |
| `i2c-mux-gpio.c` | 用若干 GPIO 驱动模拟 mux 选择线 | `0`（`:157-158`） | parent-locked |
| `i2c-mux-reg.c` | 写一个 MMIO 寄存器切换 | `0`（`:201`） | parent-locked |
| `i2c-mux-pinctrl.c` | 用 pinctrl state 把引脚在多组总线间切换 | `0`（`:95-97`） | parent-locked |
| `i2c-mux-gpmux.c` | 通用 mux 框架（`drivers/mux/`）的 I²C 适配 | `0`（`:99`） | parent-locked |
| `i2c-mux-mlxcpld.c` | Mellanox CPLD | `0`（`:143-145`） | parent-locked |
| `i2c-arb-gpio-challenge.c` | GPIO "挑战-应答"式总线仲裁 | `I2C_MUX_ARBITRATOR`（`:116`） | parent-locked |
| `i2c-mux-ltc4306.c` | LTC4306/4305，经 regmap 切换 | **`I2C_MUX_LOCKED`**（`:229`） | **mux-locked**（本目录内唯一） |
| `i2c-demux-pinctrl.c` | 反向：一个从设备在多条总线间迁移 | 不调 `i2c_mux_alloc()` | **不适用**——它根本不走 i2c-mux 框架，自己 `i2c_add_adapter()`（`:114`）/`i2c_del_adapter()`（`:137`） |

一个细节：`i2c-arb-gpio-challenge.c` 调 `i2c_mux_alloc()` 时 parent 传的是 **NULL**（`:116`），随后才用 `of_get_i2c_adapter_by_node()` 从 `i2c-parent` phandle 解析出真正的父总线补上（`:157-162`）。所以看到 `muxc->parent` 为 NULL 不一定是 bug。

### 1.8 框架的使用者不止住在 `drivers/i2c/muxes/`

**这一节是本篇最容易被漏掉的一块**：`drivers/i2c/muxes/` 只是"专职 mux 芯片"的家，而 i2c-mux 框架真正的调用点散布全树。全树 `i2c_mux_alloc()` 调用点共 **22 处**：9 处在 `drivers/i2c/muxes/`（上表，`i2c-demux-pinctrl.c` 不算），另外 **13 处**在别的子系统：

| 调用点 | 通道数 | flags | 锁风格 | 做什么 |
|---|---|---|---|---|
| `drivers/iio/gyro/mpu3050-i2c.c:65-66` | 1 | `I2C_MUX_LOCKED \| I2C_MUX_GATE` | mux-locked | 陀螺仪的 bypass 门，放出挂在它下面的磁力计 |
| `drivers/iio/imu/inv_mpu6050/inv_mpu_i2c.c:139-140` | 1 | `I2C_MUX_LOCKED \| I2C_MUX_GATE` | mux-locked | 同上，MPU6050 的 aux 总线 |
| `drivers/gpu/drm/bridge/sii902x.c:1049-1050` | 1 | `I2C_MUX_GATE` | **parent-locked** | HDMI bridge 放出 DDC 通道读 EDID |
| `drivers/power/supply/sbs-manager.c:348-349` | 4 | `I2C_MUX_LOCKED` | mux-locked | 智能电池管理器，每个电池一个通道 |
| `drivers/media/i2c/max9286.c:311-312` | `nsources` | `I2C_MUX_LOCKED` | mux-locked | GMSL 解串器，每路摄像头一个通道 |
| `drivers/media/dvb-frontends/rtl2832.c:1079` | 1 | `I2C_MUX_LOCKED` | mux-locked | 解调器放出调谐器（tuner）总线 |
| `drivers/media/dvb-frontends/lgdt3306a.c:2204-2205` | 1 | `I2C_MUX_LOCKED` | mux-locked | 同上 |
| `drivers/media/dvb-frontends/si2168.c:740-741` | 1 | `I2C_MUX_LOCKED` | mux-locked | 同上 |
| `drivers/media/dvb-frontends/rtl2830.c:835` | 1 | `0` | parent-locked | 同上 |
| `drivers/media/dvb-frontends/af9013.c:1477` | 1 | `0` | parent-locked | 同上 |
| `drivers/media/dvb-frontends/m88ds3103.c:1862` | 1 | `0` | parent-locked | 同上 |
| `drivers/media/usb/cx231xx/cx231xx-i2c.c:560` | 2 | `0` | parent-locked | USB 采集卡 |
| `drivers/of/unittest.c:2713` | — | `0` | parent-locked | DT 单元测试的假 mux |

三点结论：

- **最常见的用法根本不是"mux 芯片"，而是"1 通道的门"**：一颗芯片（传感器、解串器、解调器、bridge）内部有一条次级 I²C 总线，平时被它自己占着，需要时打开旁路（bypass）把次级总线并到主总线上。这正是 `I2C_MUX_GATE`（`i2c-mux.h:44`）的用途：它让 `i2c_mux_add_adapter()` 在 DT 里去找名为 **`i2c-gate`** 的分组节点而不是 `i2c-mux`（`i2c-mux.c:362-363`），语义上"一扇门"确实不该叫"多路选择器"。全树只有上面三个驱动用它，但注意 **`I2C_MUX_GATE` 与锁风格无关**——sii902x 用了 GATE 却是 parent-locked。
- **mux-locked 在 `drivers/i2c/muxes/` 之外反而是主流**（13 处里 7 处），原因和 ltc4306 一样（§1.4）：这些驱动的 select 用的都是**本子系统自己的加锁读写封装**，拿不到 `__` 不加锁入口。逐个核对过：

  | 驱动 | select 里实际调什么 | 最终落到 |
  |---|---|---|
  | `sbs-manager` | `sbsm_write_word()`（`:76-84`） | `i2c_smbus_write_word_data()`（加锁） |
  | `max9286` | `max9286_i2c_mux_configure()`（`:251`）→ `max9286_write()`（`:234`） | `i2c_smbus_write_byte_data()`（加锁） |
  | `si2168` | `si2168_cmd_execute()`（`:606`） | `i2c_master_send()`（`:32`，加锁） |
  | `mpu3050-i2c` | 只有 `pm_runtime_get_sync()`（`:22`） | resume → `mpu3050_power_up()`（`mpu3050-core.c:859`）里的 `regmap_update_bits()`（加锁），而且中间还 `msleep(200)` |
  | `ltc4306` | `regmap_update_bits()` | regmap-i2c（加锁） |

  一个诚实的例外：**`inv_mpu6050` 的 `inv_mpu6050_select_bypass()` 是空的**（`inv_mpu_i2c.c:21-24`，直接 `return 0`），它不需要发任何传输，所以这里的 `I2C_MUX_LOCKED` 并不是被逼的——大概是沿用了同族驱动的写法。所以"mux-locked ⇒ select 走加锁路径"这个方向成立，反过来不成立。
- 因此凡是"全树只有 X 一个这么用"这类断言，**必须在整棵树上 grep，不能只 grep 框架自己的目录**。本篇初稿就在这里错过两次（见 `99-evidence.md` §3）。

---

## 2. 算法层（algos）

`drivers/i2c/algos/` 里的三个模块提供**可复用的 `i2c_algorithm` 实现**。控制器驱动只要提供更低层的原语（拉引脚、读写芯片寄存器），就能免费得到完整的 I²C 协议实现。

### 2.1 `i2c-algo-bit` —— 位操作（bit-banging）

最常用的一个：任何有两个可双向 GPIO 的硬件都能变成 I²C 主机。`drivers/i2c/busses/i2c-gpio.c` 就是它的最主要使用者；另外 DRM 的 DDC/EDID 读取（`drivers/gpu/drm/drm_edid.c` 一路下去）也大量用它。

**接入方式**：填 `struct i2c_algo_bit_data`（见 [`02-datastructures.md`](02-datastructures.md) §2.10），放进 `adap->algo_data`，然后调 `i2c_bit_add_bus(adap)`。

**协议实现的层次**（`algos/i2c-algo-bit.c`）：

```
   最底层原语
     sdalo (:53)  sdahi (:59)  scllo (:65)   ← 这三个是 static inline，
        │                                      各自 setsda/setscl 后 udelay 半个周期
     sclhi (:75)                             ← 唯一不是 inline 的（static int）：
        └─ 释放 SCL 后要循环等它真的变高（时钟拉伸），带超时，所以有返回值
   ↓
   位形态
     i2c_start (:115)    i2c_repstart (:123)    i2c_stop (:134)
   ↓
   字节形态
     i2c_outb (:152) ── 发 8 位 + 读 ACK，返回 ACK 状态
     i2c_inb  (:198) ── 收 8 位
     acknak   (:400) ── 发 ACK 或 NAK
   ↓
   消息形态
     bit_doAddress (:477) ── 发地址（含 10 位地址的两字节形式）
                             内部调 try_address (:334) 做重试
     sendbytes (:359)  readbytes (:416)
                       └─ readbytes 处理 I2C_M_RECV_LEN：
                          第一个字节是长度，据此扩展 msg->len
   ↓
   事务形态
     bit_xfer (:531)  ── 见下
     bit_xfer_atomic (:612-621) ── 直接调 bit_xfer，只是在未声明
                                   can_do_atomic 时 dev_warn。为什么不
                                   在 probe 时按硬件能力改掉 .master_xfer_atomic？
                                   :606-611 的注释给了答案：algo 结构体是
                                   导出的 const，改不了
```

`bit_xfer()`（`:531-604`）的骨架：

```c
if (adap->pre_xfer) { ret = adap->pre_xfer(i2c_adap); ... }
i2c_start(adap);                                     /* :546 */
for (i = 0; i < num; i++) {
    nak_ok = pmsg->flags & I2C_M_IGNORE_NAK;
    if (!(pmsg->flags & I2C_M_NOSTART)) {
        if (i) {
            if (msgs[i-1].flags & I2C_M_STOP)  { i2c_stop(); i2c_start(); }  /* :552-556 */
            else                                 i2c_repstart(adap);         /* :560 */
        }
        ret = bit_doAddress(i2c_adap, pmsg);
        if (ret != 0 && !nak_ok) goto bailout;       /* NAK → 放弃 */
    }
    if (pmsg->flags & I2C_M_RD) ret = readbytes(i2c_adap, pmsg);
    else                        ret = sendbytes(i2c_adap, pmsg);
    if (ret < pmsg->len) { if (ret >= 0) ret = -EIO; goto bailout; }
}
ret = i;                                             /* 返回成功消息数 */
bailout:
i2c_stop(adap);                                      /* :599 无论成败都发 STOP */
if (adap->post_xfer) adap->post_xfer(i2c_adap);
```

`bit_func()`（`:623`）声明的能力很全：

```c
return I2C_FUNC_I2C | I2C_FUNC_NOSTART | I2C_FUNC_SMBUS_EMUL_ALL |
       I2C_FUNC_10BIT_ADDR | I2C_FUNC_PROTOCOL_MANGLING;
```

这是位操作的天然优势：因为每一位都是软件控制的，所有协议变体都能实现。

`__i2c_bit_add_bus()`（`:646-679`）里两个值得注意的行为：

- `adap->retries = 3`（`:660`）—— 位操作总线容易受干扰，默认给 3 次重试。
- `if (bit_adap->getscl == NULL)` → 装上 `I2C_AQ_NO_CLK_STRETCH` quirk（`:662`）并 `dev_warn("Not I2C compliant: can't read SCL")` + `"Bus may be unreliable"`（`:674-677`）。因为读不到 SCL 就无法检测时钟拉伸，只能盲发。
- `bit_test` 模块参数（`:35`）触发 `test_bus()`（`:229`）—— 上电自检引脚接线是否正常。`bit_test >= 2` 时自检失败直接 `-ENODEV`。
- `:664-667` 有条注释："我们曾尝试在这里把 SCL/SDA 强制到初始状态，但那导致了回归（Bugzilla #200045）"—— 又一个"看起来该做但不能做"的例子。

### 2.2 `i2c-algo-pca` —— PCA9564 / PCA9665

这类芯片是**并行总线转 I²C 的协处理器**：主机通过 4 个寄存器（STA / DAT / ADR / CON）驱动状态机，芯片自己产生总线时序。

`pca_xfer()`（`algos/i2c-algo-pca.c:182-356`）是一个**围绕硬件状态码的大 switch 状态机**，而不是像 algo-bit 那样的顺序过程。这是它最值得学习的地方：I²C 主机控制器的通用实现形态就是"等中断 → 读状态寄存器 → 按状态决定下一步"。

低层原语（`struct i2c_algo_pca_data`）：`write_byte` / `read_byte` / `wait_for_completion` / `reset_chip`。

`pca_init()`（`:391-527`）处理两代芯片的差异：

- **PCA9564**：时钟只能选 8 个预定义档位（`I2C_PCA_CON_330kHz` … `_36kHz`，`include/linux/i2c-algo-pca.h:13-20`）；
- **PCA9665**：可任意频率，通过 `ISCLL`/`ISCLH`（SCL 低/高周期）间接寄存器配置，配合 `I2C_PCA_OSC_PER`（`:10`）换算。

`pca_probe_chip()`（`:368`）在运行时区分两代芯片。

### 2.3 `i2c-algo-pcf` —— PCF8584

更老的同类芯片。`pcf_xfer()`（`algos/i2c-algo-pcf.c:294`）结构类似。特色是 `handle_lab()`（`:65`）—— 专门处理 **LAB（Lost Arbitration Bit）**，多主机总线上丢失仲裁后按 `lab_mdelay`（`include/linux/i2c-algo-pcf.h:32`）退避重试。这是内核里少见的、直接面对多主机 I²C 的代码。

### 2.4 三者对比

| | algo-bit | algo-pca | algo-pcf |
|---|---|---|---|
| 硬件要求 | 2 个双向 GPIO | PCA9564/9665 芯片 | PCF8584 芯片 |
| 实现形态 | 顺序过程（软件产生每一位） | 状态机（硬件产生时序） | 状态机 |
| CPU 开销 | 高（每位都要延时） | 低 | 低 |
| 能力 | 最全（含协议变体、10 位、NOSTART） | 基本 I²C | 基本 I²C |
| 时钟拉伸 | 需要 `getscl` 才支持 | 硬件支持 | 硬件支持 |
| 原子传输 | **提供** `.master_xfer_atomic`（`bit_xfer_atomic`，`:612`） | **不提供**（`pca_algo` 只有 `.master_xfer` + `.functionality`，`:363-366`） | **不提供**（`pcf_algo` 同上，`:390-393`） |
| 典型使用者 | `i2c-gpio`、DRM DDC | 嵌入式 PowerPC/ARM 板 | 老 x86 / 工控板 |

关于"原子传输"这一行的后果：内核只在 `system_state > SYSTEM_RUNNING` 且不可抢占时（即 reboot/poweroff 末期要关 PMIC 那种场合）走原子路径，判据是 `i2c_in_atomic_xfer_mode()`（`i2c-core.h:31-35`，注释原话是 *"Atomic transfers are a corner case and not for generic use!"*）。此时 `__i2c_lock_bus_helper()`（`:37-50`）会 `WARN("No atomic I2C transfer handler for '%s'")` 然后改用 `i2c_trylock_bus()`，抢不到锁就返回 `-EAGAIN`。所以**用 algo-pca / algo-pcf 的控制器不能挂关机路径上的 PMIC**；而 algo-bit 虽然提供了入口，也只是"愿意试"——它自己在 `bit_xfer_atomic()` 里还要再 warn 一次（见 §2.1）。

---

## 3. 从机框架（slave）

`CONFIG_I2C_SLAVE`。让本机的 I²C 控制器**响应对端 master 的寻址**，从而模拟一颗芯片。典型用途：BMC 与主板之间的通信、模拟 EEPROM 让对端读取板级信息、硬件测试。

### 3.1 三个 API 与一个回调（`drivers/i2c/i2c-core-slave.c`，仅 127 行）

```c
typedef int (*i2c_slave_cb_t)(struct i2c_client *client,
                              enum i2c_slave_event event, u8 *val);
```

**`i2c_slave_register()`（`:20-57`）**：

```
 ├─ WARN 参数非法（!client || !slave_cb）→ -EINVAL                    :24
 ├─ if (!(client->flags & I2C_CLIENT_SLAVE))
 │      dev_warn("%s: client slave flag not set. You might see "
 │               "address collisions\n", ...)                      :27-29
 │      ─── 只警告不阻止：允许"临时把普通 client 转成 slave"
 ├─ if (!(client->flags & I2C_CLIENT_TEN))                          :31-38
 │      i2c_check_7bit_addr_validity_strict(client->addr) → -EINVAL
 │      ─── 只对 7 位地址做严格检查；声明为 10 位从机的直接跳过
 ├─ if (!client->adapter->algo->reg_slave) → -EOPNOTSUPP              :40
 ├─ client->slave_cb = slave_cb;                                      :45
 ├─ i2c_lock_bus(adapter, I2C_LOCK_ROOT_ADAPTER);                  :47-49
 │  ret = adapter->algo->reg_slave(client);
 │  i2c_unlock_bus(adapter, I2C_LOCK_ROOT_ADAPTER);
 └─ 失败则 client->slave_cb = NULL
```

注意锁的粒度是 **`I2C_LOCK_ROOT_ADAPTER`** 而不是 SEGMENT —— 因为注册从机地址改变的是整个物理控制器的状态，必须独占整条总线。`i2c_slave_unregister()`（`:60-83`）同样。

**`i2c_slave_event()`（`:85-95`）** 是控制器驱动向上分发事件的唯一入口：

```c
int i2c_slave_event(struct i2c_client *client,
                    enum i2c_slave_event event, u8 *val)
{
    int ret = client->slave_cb(client, event, val);

    if (trace_i2c_slave_enabled())
        trace_i2c_slave(client, event, val, ret);     /* :91 */

    return ret;
}
```

**`i2c_detect_slave_mode()`（`:109-127`）**：扫 DT 子节点的 `reg` 属性，若有任何一个带 `I2C_OWN_SLAVE_ADDRESS` 标志就返回 true。控制器驱动用它决定是否需要初始化从机硬件 —— **也就是说从机模式完全由 DTS 里那个标志位开启**（写法见 [`06-devicetree.md`](06-devicetree.md) §5.2）。

两个实现细节，读源码时会觉得别扭：

- 判据是 `IS_BUILTIN(CONFIG_OF)` 而不是 `IS_ENABLED`。`CONFIG_OF` 在 arm64 上是 `y`（不可为 `m`），两者等价，所以实际没有影响；写成 `IS_BUILTIN` 只是为了让编译器在 `CONFIG_OF=n` 时把整段折叠掉。
- `u32 reg;` 声明时未初始化（`:113`），而 `of_property_read_u32(child, "reg", &reg)` 的返回值被丢弃（`:116`）。子节点若没有 `reg` 属性，下一行 `reg & I2C_OWN_SLAVE_ADDRESS`（`:117`）读的就是**未初始化的栈值**。实践中 I²C 子节点几乎必然有 `reg`，所以这个洞很难触发，但它确实是个洞。

### 3.2 事件语义（`enum i2c_slave_event`，`include/linux/i2c.h:386-392`）

| 事件 | 触发时机 | 回调该做什么 | `*val` 方向 |
|---|---|---|---|
| `I2C_SLAVE_WRITE_REQUESTED` | 对端寻址成功、方向为写，**还没有数据** | 复位内部写状态 | 不用 |
| `I2C_SLAVE_WRITE_RECEIVED` | 收到一个字节 | 处理这个字节 | **入**（读 `*val`） |
| `I2C_SLAVE_READ_REQUESTED` | 对端寻址成功、方向为读，要**第一个**字节 | 填第一个字节 | **出**（写 `*val`） |
| `I2C_SLAVE_READ_PROCESSED` | 上一个字节**已经上了总线**，要下一个 | 填下一个字节 | **出** |
| `I2C_SLAVE_STOP` | 收到 STOP，事务结束 | 收尾 | 不用 |

**`READ_REQUESTED` 与 `READ_PROCESSED` 的区别是这套 API 最容易出错的地方**。`i2c-slave-eeprom.c:66-79` 的注释解释得很清楚：

```c
case I2C_SLAVE_READ_PROCESSED:
    /* The previous byte made it to the bus, get next one */
    eeprom->buffer_idx++;
    fallthrough;
case I2C_SLAVE_READ_REQUESTED:
    spin_lock(&eeprom->buffer_lock);
    *val = eeprom->buffer[eeprom->buffer_idx & eeprom->address_mask];
    spin_unlock(&eeprom->buffer_lock);
    /*
     * Do not increment buffer_idx here, because we don't know if
     * this byte will be actually used. Read Linux I2C slave docs
     * for details.
     */
    break;
```

即：**索引只在 `READ_PROCESSED` 时递增，不在填数据时递增**。因为 `READ_REQUESTED` 填的那个字节可能永远不会被对端取走（对端可能立刻发 STOP），如果填的时候就递增，下次读就会跳字节。

### 3.3 后端一：`i2c-slave-eeprom`（217 行）

模拟 24Cxx 系列 EEPROM。

- 内存映像 `eeprom->buffer[]` 柔性数组（`i2c-slave-eeprom.c:37`），容量由 `id->driver_data` 编码：`I2C_SLAVE_DEVICE_MAGIC(len, flags)`（`:43`），支持 24c02 / 24c32 / 24c64 / 24c512 以及各自的 `ro` 只读变体（`:192-202`）。
- 地址字节数：`I2C_SLAVE_FLAG_ADDR16`（`:41`）决定是 1 字节还是 2 字节地址。写事务的前 1~2 个字节被当作地址（`:52-56`）。
- 初始内容：若 DT 里有 `firmware-name` 属性，用 `request_firmware_into_buf()` 加载（`:131`）；否则填 0xff（"空 EEPROM 的典型状态"，`:138`）。
- 主机侧访问：sysfs 二进制文件 `slave-eeprom`（`:165`），读写都用 `spin_lock_irqsave` 与回调互斥（`:101`/`:116`）。
- 文件头有一条重要限制说明（`:8-11`）：因为多数从机 IP 只能识别一个地址，本驱动**不支持需要多个地址的 EEPROM 型号**。
- `:13-17` 还有个 FIXME：如果对端只发了 16 位地址的高 8 位怎么办？ST-M24C64 会回 0xff，当前实现把这 8 位当有效地址用。

### 3.4 后端二：`i2c-slave-testunit`（192 行）

这个后端很有意思：它是一个**能被远端命令去主动发起 I²C 传输的测试单元**，用于测试对端 master 的实现（尤其是从机中断、总线仲裁这些难以构造的场景）。

4 个寄存器（`:26-32`）：`TU_REG_CMD` / `TU_REG_DATAL` / `TU_REG_DATAH` / `TU_REG_DELAY`。对端连续写 4 个字节，STOP 时若正好写满 4 个（`:116`），就派一个延时 work：

```c
case I2C_SLAVE_STOP:
    if (tu->reg_idx == TU_NUM_REGS) {
        set_bit(TU_FLAG_IN_PROCESS, &tu->flags);
        queue_delayed_work(system_long_wq, &tu->worker,
                           msecs_to_jiffies(10 * tu->regs[TU_REG_DELAY]));
    }
    tu->reg_idx = 0;      /* :127 注释：避免后续读消息的 STOP 再次派工作 */
    break;
```

3 个命令（`:19-24`）：

| 命令 | 值 | 效果 |
|---|---|---|
| `TU_CMD_READ_BYTES` | 1 | 向 `DATAL` 指定的地址读 `DATAH` 个字节 |
| `TU_CMD_HOST_NOTIFY` | 2 | 向地址 0x08（SMBus host）发一个 3 字节 Host Notify |
| `TU_CMD_SMBUS_BLOCK_PROC_CALL` | 3 | 配合读事务模拟 Block Process Call |

**为什么必须用 workqueue？** 因为 `slave_cb` 通常在中断上下文，而 `i2c_transfer()`（`:77`）会睡眠。`TU_FLAG_IN_PROCESS`（`:34-36`）位保证在 work 完成前拒绝新命令（回 `-EBUSY`，`:99`、`:132`）。文件顶部还留了个 `/* FIXME: is system_long_wq the best choice? */`（`:15`）。

这份 192 行的代码是**"如何在 slave 回调里安全地做需要睡眠的事"的标准范式**。

### 3.5 控制器侧要做什么

一个支持 slave 的控制器驱动需要：

1. 实现 `algo->reg_slave(client)`：把 `client->addr` 写进硬件的从机地址寄存器，使能从机中断；
2. 实现 `algo->unreg_slave(client)`：反向操作；
3. 在 `functionality()` 里返回 `I2C_FUNC_SLAVE`；
4. 在中断处理函数里解析硬件状态，翻译成 5 个事件之一，调 `i2c_slave_event()`。

---

## 4. 用户态接口：`i2c-dev`（782 行）

### 4.1 设备节点的建立

```
i2c_dev_init()                                              i2c-dev.c:732
 ├─ register_chrdev_region(MKDEV(I2C_MAJOR, 0), I2C_MINORS,
 │                         "i2c")                                   :738
 │     I2C_MINORS = MINORMASK + 1（:49）
 ├─ i2c_dev_class = class_create(THIS_MODULE, "i2c-dev")            :742
 ├─ bus_register_notifier(&i2c_bus_type, &i2cdev_notifier)          :750
 │     └─ i2cdev_notifier_call（:707）响应
 │        BUS_NOTIFY_ADD_DEVICE  → i2cdev_attach_adapter
 │        BUS_NOTIFY_DEL_DEVICE  → i2cdev_detach_adapter
 └─ i2c_for_each_dev(NULL, i2cdev_attach_adapter)                   :755
       └─ 补上 i2c-dev 加载之前就已存在的适配器

i2cdev_attach_adapter(dev, dummy)                                   :649
 ├─ if (dev->type != &i2c_adapter_type) return 0                     :655
 ├─ i2c_dev = get_free_i2c_dev(adap)                                 :659
 │     （函数体在 :68-89，作用是占用 minor = adap->nr）
 ├─ cdev_init(&i2c_dev->cdev, &i2cdev_fops)
 ├─ i2c_dev->dev.devt = MKDEV(I2C_MAJOR, adap->nr)                  :667
 └─ cdev_device_add(&i2c_dev->cdev, &i2c_dev->dev)                  :676
```

**minor 号 = 适配器编号**，所以 `/dev/i2c-3` 一定对应 `i2c-3`。

### 4.2 匿名 client 的设计

`i2c-dev.c:117-132` 有一段关键注释，翻译要点：

> 打开设备文件时，得到的 fd 只绑定到一个适配器，**还没有绑定到任何从设备地址**。因此：
> - `I2C_RDWR` 立即可用（消息里自带地址）；**但只支持 SMBus 的适配器不提供这个接口**（`:124-125` 原文 *"However, SMBus-only adapters don't support that interface"*）；
> - `read()` / `write()` / SMBus 系列必须先用 `I2C_SLAVE` ioctl 设置地址，该 ioctl 会配置一个**匿名的、永不注册到设备模型的 `i2c_client`**。

第一条里那个"只支持 SMBus 的适配器"不是理论情形：`i2c-stub` 就是（只填 `.smbus_xfer`，`i2c-stub.c:304-307`，见 §7），所以在 stub 上做实验时 `I2C_RDWR` 会失败而 SMBus 系列正常——这个坑很容易被误判成 stub 没工作。

```c
/* i2cdev_open()，:585-613 */
adap = i2c_get_adapter(minor);                    /* :591  加引用 */
client = kzalloc(sizeof(*client), GFP_KERNEL);    /* :602 */
snprintf(client->name, I2C_NAME_SIZE, "i2c-dev %d", adap->nr);  /* :607 */
client->adapter = adap;
file->private_data = client;
```

这个 client 从不 `device_register()`。它存在的唯一目的是给 `i2c_master_send/recv` 和 SMBus helper 提供一个"载体"，携带 `addr` / `flags`。

### 4.3 主要 ioctl 的实现要点

**`I2C_RDWR`（`i2cdev_ioctl` `:434-459` + `i2cdev_ioctl_rdwr` `:235-309`）**

```
 ├─ 拷入 i2c_rdwr_ioctl_data，检查 nmsgs <= I2C_RDWR_IOCTL_MAX_MSGS(42)  :450
 ├─ memdup_array_user 拷入消息数组                                     :453
 ├─ 逐条消息：
 │    ├─ len > 8192 → -EINVAL                                         :250
 │    ├─ memdup_user 把用户缓冲拷进内核，并置 I2C_M_DMA_SAFE       :256-262
 │    │    ─── 核心保证：从用户态来的缓冲一定是 DMA 安全的
 │    └─ I2C_M_RECV_LEN 校验：要求 len >= buf[0] + I2C_SMBUS_BLOCK_MAX
 │       否则 -EINVAL                                             :275-286
 │       ─── 因为从设备可能返回最多 32 字节，用户缓冲必须预留够
 ├─ i2c_transfer(client->adapter, msgs, nmsgs)                         :297
 └─ 读消息的数据 copy_to_user 回去                                 :299-303
```

**`I2C_SMBUS`（`i2cdev_ioctl_smbus` `:311-390`）**

```
 ├─ size 白名单（只允许 9 种合法事务类型）                        :318-331
 ├─ read_write 与 size 的组合合法性检查                                :334
 ├─ QUICK 和 BYTE-写 走快捷路径（没有 data 参数）                  :344-350
 ├─ 按 size 算出 datasize（byte / word / block 三档）              :358-365
 ├─ 只有写方向、PROC_CALL、BLOCK_PROC_CALL、I2C_BLOCK_DATA
 │  这几种才真的 copy_from_user(datasize)                          :367-373
 │     ─── 纯读事务不拷入，省一次用户态访问
 ├─ I2C_SMBUS_I2C_BLOCK_BROKEN → 转成 I2C_BLOCK_DATA           :374-380
 │     ─── 兼容老用户态：老的 i2c-tools 用 BROKEN 这个值
 └─ i2c_smbus_xfer(...)                                               :381
```

**`I2C_SLAVE` / `I2C_SLAVE_FORCE`（`:401-410`）**

```c
if ((arg > 0x3ff) || (((client->flags & I2C_M_TEN) == 0) && arg > 0x7f))
    return -EINVAL;
if (cmd == I2C_SLAVE && i2cdev_check_addr(client->adapter, arg))
    return -EBUSY;
client->addr = arg;
```

`i2cdev_check_addr()`（`:220-233`）→ `i2cdev_check()`（`:180`）的判定与核心不同：

```c
/* i2c-dev.c:180-190，注释在 :217-219 */
static int i2cdev_check(struct device *dev, void *addrp)
{
    struct i2c_client *client = i2c_verify_client(dev);

    if (!client || client->addr != *(unsigned int *)addrp)
        return 0;

    return dev->driver ? -EBUSY : 0;   /* 只有绑定了驱动才算占用 */
}
```

**这是有意的设计**：用户态应该被允许访问"已实例化但没有驱动"的地址（例如通过 sysfs 手工建的、或 dummy 占位的）。只有真的有内核驱动在管的地址才拒绝，否则会出现两方同时操作硬件。

**而且检查范围是整棵 mux 树，不只是当前这一段总线**——这一点在 §1 之后才好理解：

```
 i2cdev_check_addr(adapter, addr)                                :220-233
  ├─ 若 adapter 是某个 mux 的通道 → i2cdev_check_mux_parents(parent)  :226-227
  │     └─ 向上：查父适配器上的直属 client，再递归到爷爷      :191-201
  └─ 向下：device_for_each_child(adapter, i2cdev_check_mux_children)  :230-231
        └─ 遇到 dev->type == &i2c_adapter_type 的子设备就再往下钻   :204-215
```

理由是物理的：mux 各通道和父总线**共享同一条物理总线的地址空间**（§1.1），一个地址只要在这棵树的任何一个节点上被驱动占着，用户态就不该去动它。注意这个遍历只向上和向下走，**不横向走到兄弟通道**——兄弟通道之间靠 mux 硬件隔离，同一时刻只有一条接通，所以不冲突。

**`I2C_TIMEOUT`（`:478-486`）** 的单位是 **10 ms**：

```c
case I2C_TIMEOUT:
    ...
    client->adapter->timeout = msecs_to_jiffies(arg * 10);   /* :485 */
```

这是从早期 i2c-tools 沿袭下来的接口约定，容易踩坑。

### 4.4 compat 层（`:498-583`）

32 位用户态在 64 位内核上运行时，`struct i2c_msg` 里的 `__u8 *buf` 指针宽度不同，所以需要一整套镜像结构：`struct i2c_smbus_ioctl_data32`（`:500`）、`struct i2c_msg32`（`:507`）、`struct i2c_rdwr_ioctl_data32`（`:514`），以及 `compat_i2cdev_ioctl()`（`:519`）做翻译。

这就是 [`03-internals.md`](03-internals.md) Part C 提到的"`i2c_msg` 在 uapi 里"的代价。

---

## 5. SMBus 扩展（`i2c-smbus.c`，`CONFIG_I2C_SMBUS`）

这个文件实现了 SMBus 规范里三个"超出基础事务"的机制。

### 5.1 SMBus Alert（ARA）

SMBus 有一根可选的 `SMBALERT#` 线。从设备把它拉低表示"我有事"，主机随后向**保留地址 0x0c（Alert Response Address, ARA）**发一次读，谁把自己的地址回出来就是谁在报警。

```
   SMBALERT# 拉低
        │
        ▼
   smbus_alert() irq handler                        i2c-smbus.c:98
        └─ 循环：向 0x0c 读一个字节 → 得到报警设备的地址
             └─ device_for_each_child(&adapter->dev, &data, smbus_do_alert)
                                                          :32
                  └─ 匹配 addr 的 client → client->driver->alert(client,
                                            I2C_PROTOCOL_SMBUS_ALERT, data)
```

- `smbus_do_alert()`（`:32`）按地址找 client 并调其驱动的 `alert()` 回调。
- `smbus_do_alert_force()`（`:70`）是强制版本，用于 client 没有绑定驱动的情形。
- 若适配器没有中断，可以用 `smbalert_work()`（`:154`）轮询。
- `i2c_handle_smbus_alert(ara)`（`:240`）供控制器驱动在自己检测到 alert 时主动调用。

ARA 设备的创建有两条路：显式调 `i2c_new_smbus_alert_device()`（`i2c-core-smbus.c:693`，用 board_info 名 `"smbus_alert"` 地址 `0x0c`）；或者由核心自动 —— `i2c_setup_smbus_alert()`（`i2c-core-smbus.c:706-722`，从 `i2c_register_adapter()` 的 `i2c-core-base.c:1564` 调进来）。

自动这条路有一处**很容易看错的地方**：`interrupt-names` 不是在适配器节点上找，而是在 **`adapter->dev.parent`（也就是控制器设备）**上找：

```c
struct device *parent = adapter->dev.parent;          /* :708 */
if (!parent)                                          /* :711-713 */
	return 0;    /* 没有父设备的适配器（如 i2c-stub）直接跳过 */
irq = device_property_match_string(parent, "interrupt-names", "smbus_alert");
                                                      /* :715 */
if (irq == -EINVAL || irq == -ENODATA)
	return 0;    /* 没写这个属性不算错 */
```

原因是 DTS 里 `interrupts` / `interrupt-names` 写在**控制器节点**上（`&i2c0 { ... }`），而 `adapter->dev` 是核心为这条总线新建的设备，它的 `of_node` 虽然通常也指向控制器节点，但属性查询走的是 `device_property_*` 这一套（同时支持 DT 和 ACPI），必须显式指到 parent。DTS 写法、以及"binding 里那个 `smbus-alert` 布尔属性核心根本不看"这个陷阱，见 [`06-devicetree.md`](06-devicetree.md) §2.5。

### 5.2 Host Notify

反方向的机制：从设备**主动向主机（地址 0x08）写 3 个字节**（自己的地址 + 2 字节数据）来通知。这需要本机控制器工作在 slave 模式下监听 0x08。

```
   i2c_new_slave_host_notify_device(adapter)         i2c-smbus.c:303
     └─ 建一个 slave client（地址 0x08），
        回调 i2c_slave_host_notify_cb（:257）
             └─ 收满 SMBUS_HOST_NOTIFY_LEN(3) 字节后
                → i2c_handle_smbus_host_notify(adapter, addr)
                                             i2c-core-base.c:1496
                   └─ generic_handle_irq_safe(irq)              :1507
                      （irq 来自 adapter->host_notify_domain）
```

核心侧的 irq domain 由 **`i2c_setup_host_notify_irq_domain()`（`i2c-core-base.c:1468-1484`）** 建立，调用点在 `i2c_register_adapter()`（`:1546`）；它第一句就是 `if (!i2c_check_functionality(adap, I2C_FUNC_SMBUS_HOST_NOTIFY)) return 0`，所以不声明这个能力的控制器不会有 domain。反向的拆除是 **`i2c_host_notify_irq_teardown()`（`:1440-1458`）**，遍历 `I2C_ADDR_7BITS_COUNT` 个 hwirq 逐个 `irq_dispose_mapping()` 再 `irq_domain_remove()`。**这两个函数在源码里挨着，但一个是建一个是拆，别当成一段。**

client 通过 `I2C_CLIENT_HOST_NOTIFY` 标志（或 DT 的 `host-notify` 属性，`i2c-core-of.c:55`）声明"我的中断走 Host Notify"，`i2c_device_probe()` 就会用 `i2c_smbus_host_notify_to_irq()`（`:472`）给它分配一个虚拟 irq —— 从驱动角度看，它就是个普通中断。

`i2c_handle_smbus_host_notify()` 的注释明写 **"Context: can't sleep"**，用 `generic_handle_irq_safe()` 是为了能在任何上下文安全触发。

### 5.3 SPD 自动注册（x86 专属，不展开）

`i2c_register_spd(adap)`（`i2c-smbus.c:366-455`）靠 DMI 表得知内存条数量与 SPD 类型，自动实例化 `spd` / `ee1004` 设备（内存条上的 SPD EEPROM）。函数体整个包在 `#if IS_ENABLED(CONFIG_DMI)` 里（`:365`），`CONFIG_DMI=n` 时头文件给出空实现（`include/linux/i2c-smbus.h:49`）。

**"arm64 上不适用"这句有硬证据**：全树唯一的调用点是 `drivers/i2c/busses/i2c-i801.c:1300` —— Intel 南桥的 SMBus 控制器驱动。也就是说这个机制只服务 x86 的 DMI + 内存控制器拓扑，本文档集只记它的存在。

---

## 6. 枚举后端

### 6.1 设备树（`i2c-core-of.c`，220 行）

```
of_i2c_register_devices(adap)                        i2c-core-of.c:85
 ├─ 无 adap->dev.of_node → 直接返回                                 :91
 ├─ bus = of_get_child_by_name(of_node, "i2c-bus") ?: of_node    :96-98
 │     ─── 兼容两种 DT 写法：设备直接是控制器子节点，
 │        或包在一个 "i2c-bus" 子节点里
 └─ for_each_available_child_of_node(bus, node):
      ├─ of_node_test_and_set_flag(node, OF_POPULATED) 已置 → 跳过 :101
      └─ of_i2c_register_device(adap, node)                        :65
           ├─ of_i2c_get_board_info(dev, node, &info)              :22
           │     ├─ of_modalias_node(node, info->type, ...)  ← compatible
           │     │     失败 → "of_i2c: modalias failure" + -EINVAL
           │     ├─ of_property_read_u32(node, "reg", &addr)
           │     │     失败 → "of_i2c: invalid reg" + 返回错误
           │     ├─ addr & I2C_TEN_BIT_ADDRESS  → I2C_CLIENT_TEN  :41-44
           │     ├─ addr & I2C_OWN_SLAVE_ADDRESS→ I2C_CLIENT_SLAVE:46-49
           │     ├─ "host-notify"   → I2C_CLIENT_HOST_NOTIFY       :55
           │     └─ "wakeup-source" → I2C_CLIENT_WAKE              :58
           └─ i2c_new_client_device(adap, &info)                   :78
```

`I2C_TEN_BIT_ADDRESS` / `I2C_OWN_SLAVE_ADDRESS` 定义在 `include/dt-bindings/i2c/i2c.h`，是叠加在 `reg` 值高位上的标志。

**动态增删**（`CONFIG_OF_DYNAMIC`，`:162-220`）：`of_i2c_notify()` 响应 DT overlay 的 `OF_RECONFIG_CHANGE_ADD` / `_REMOVE`。`:181-185` 有个细节：添加前先清 `FWNODE_FLAG_NOT_DEVICE`，否则 `fw_devlink` 会跳过给这个设备加 consumer 依赖。

### 6.2 ACPI（`i2c-core-acpi.c`，847 行）—— 本文档集不展开

核心里除 DT 之外还有第二个枚举后端：`i2c_acpi_register_devices()`，从 ACPI 的 `I2cSerialBus` 资源描述符里建 client，另外还实现了 OpRegion（让 AML 字节码直接读写 I²C 设备）。

**这是 x86 固件路径，本文档集按 arm64-only 的范围不展开。**要看它，起点是 `i2c-core-acpi.c` 的 `i2c_acpi_register_devices()`；核心侧的两个调用点在 `i2c-core-base.c:1589-1590`。

需要知道的只有一点：**它与 DT 是互斥的两条路**，同一个 client 不会两边都建 —— `i2c_acpi_register_devices()` 的第一句就是 `if (!has_acpi_companion(&adap->dev)) return;`（`i2c-core-acpi.c:325-326`）。arm64 + DT 的板子上这条路径完全不参与。

### 6.3 旧式板级信息（`i2c-boardinfo.c`，93 行）

```c
DECLARE_RWSEM(__i2c_board_lock);              /* :19 */
LIST_HEAD(__i2c_board_list);                  /* :22 */
int __i2c_first_dynamic_bus_num;              /* :25 */
```

三者都 `EXPORT_SYMBOL_GPL`，但 `:16-18` 的注释写着 **"ONLY FOR the i2c core"** —— 导出只是为了让核心的多个 .o 能共享，不是给外部用的。

`i2c_register_board_info()`（`:51-93`）：`__init` 函数，把板级描述拷进链表；顶高 `__i2c_first_dynamic_bus_num`（`:58-59`）；`kmemdup` 资源数组（`:75`）。

现在只有少数老 arm32 板文件和 x86 平台代码还用它。**arm64 上没有板文件，这条路径不存在** —— 提到它只是为了解释 `i2c-core-base.c:1593` 那个 `i2c_scan_static_board_info()` 调用为什么还在。

---

## 7. 测试设施：`i2c-stub`（413 行）

一个**纯软件的虚拟 SMBus 适配器**，不需要任何硬件。用于开发/测试芯片驱动。

```
modprobe i2c-stub chip_addr=0x50,0x68
```

| 模块参数 | 位置 | 作用 |
|---|---|---|
| `chip_addr[]` | `i2c-stub.c:35` | 要模拟的芯片地址（最多 `MAX_CHIPS` = 10，`:20`），必须在 0x03..0x77（`:364`） |
| `functionality` | `:40` | 覆盖能力位图。默认 `STUB_FUNC_DEFAULT`（`:27`），上限 `STUB_FUNC_ALL`（`:32`）。**可运行时写**（`S_IWUSR`） |
| `bank_reg[]` / `bank_mask[]` / `bank_start[]` / `bank_end[]` | `:46`/`:50`/`:54`/`:58` | 模拟"分页寄存器"（bank switching）的芯片 |

实现：`stub_xfer()`（`:123`）就是一个按地址找到 `struct stub_chip`（`:69-84`，核心是 `u16 words[256]`——SMBus 的字节操作只用其中的低字节，注释在 `:70-71`）、在内存数组里读写的函数。`stub_func()`（`:299`）返回 `STUB_FUNC_ALL & functionality`。`stub_adapter`（`:309`）是一个静态的 `struct i2c_adapter`。

它的 `smbus_algorithm`（`:304-307`）**只填了 `.functionality` 和 `.smbus_xfer`，没有 `.master_xfer`** —— 这就是 §4.2 说的"只支持 SMBus 的适配器"：在 `/dev/i2c-N` 上对 stub 用 `I2C_RDWR` 会直接失败（核心在 `__i2c_transfer()` 开头查 `adap->algo->master_xfer` 为空即 `dev_dbg("I2C level transfers not supported")` 并返回 `-EOPNOTSUPP`，`i2c-core-base.c:2242-2245`），必须用 SMBus 系列 ioctl 或 `i2cget`/`i2cset` 的默认模式。

用法见 [`05-usage.md`](05-usage.md) §8 和 `Documentation/i2c/i2c-stub.rst`。局限：它只模拟"寄存器就是内存"的行为，不能模拟时序、NAK、中断。

---

## 8. 各扩展的启用条件汇总

| 扩展 | Kconfig | 运行时依赖 |
|---|---|---|
| mux | `CONFIG_I2C_MUX` + 具体 mux 驱动 | DT 描述或平台代码。注意 `CONFIG_I2C_MUX` 常被**别的子系统 `select` 进来**（`drivers/iio/gyro/Kconfig:132`、`drivers/iio/imu/inv_mpu6050/Kconfig:14`、`drivers/media/Kconfig:44`、`drivers/gpu/drm/bridge/Kconfig:248`），或被 `depends on`（`drivers/power/supply/Kconfig:252`）—— 见 §1.8 |
| algo-bit | `CONFIG_I2C_ALGOBIT` | 控制器驱动填 `i2c_algo_bit_data` |
| algo-pca / pcf | `CONFIG_I2C_ALGOPCA` / `_ALGOPCF` | 对应芯片 |
| slave 框架 | `CONFIG_I2C_SLAVE` | 控制器实现 `reg_slave`/`unreg_slave` |
| slave EEPROM / testunit | `CONFIG_I2C_SLAVE_EEPROM` / `_TESTUNIT` | 上面 + DT 里 `reg = <0x64 I2C_OWN_SLAVE_ADDRESS>` |
| `/dev/i2c-N` | `CONFIG_I2C_CHARDEV` | — |
| SMBus Alert | `CONFIG_I2C_SMBUS` | **控制器节点**上 `interrupt-names` 含 `"smbus_alert"`（不是 adapter，见 §5.1），或显式创建 ARA |
| Host Notify | `CONFIG_I2C_SMBUS` + `CONFIG_I2C_SLAVE` | 控制器支持 slave |
| SPD | `CONFIG_I2C_SMBUS` + `CONFIG_DMI` | **x86 专属**：全树唯一调用点是 `i2c-i801.c:1300`（见 §5.3） |
| DT 枚举 | 随 `CONFIG_OF` | — （arm64 主线，见 `06`） |
| DT 动态增删 | `CONFIG_OF_DYNAMIC` | overlay（见 `06` §6） |
| ACPI 枚举 / OpRegion | `CONFIG_ACPI` / `CONFIG_ACPI_I2C_OPREGION` | **x86 固件路径，不展开** |
| 板级信息 | `CONFIG_I2C_BOARDINFO` | 平台代码调 `i2c_register_board_info()`；arm64 无 |
| i2c-stub | `CONFIG_I2C_STUB` | — |
