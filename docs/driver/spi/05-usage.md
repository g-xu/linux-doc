---
title: 使用指南
---

# SPI 使用示例

> ⚠️ **本篇所有代码骨架都未经编译、未经运行。**
>
> 它们是从 in-tree 驱动提炼出来的**结构模板**，用途是「告诉你该填哪些字段、
> 按什么顺序调哪些函数」，不是可以直接 `make` 的代码。省略了错误处理细节、
> 头文件包含、Kconfig/Makefile 改动。
>
> **但每一个出现的函数名、宏名、字段名、标志名，都已在本树 6.1.158 上
> `grep` 确认存在**（取证命令见 `99-evidence.md` §3.6；符号普查的方法和这一轮逮到的东西见 §2.5.2）。
> 「符号存在」和「代码能编」是两件事——前者本篇保证，后者不保证。

---

## §0 本子系统有哪些角色

SPI 是硬件总线型子系统，一共**五个角色**。你至少属于其中一个：

| # | 角色 | 你要写什么 | 章节 | arm64 常见度 |
|---|-----|----------|------|-----------|
| 1 | **控制器驱动作者** | 让一颗 SoC 的 SPI 控制器能用 | §1 | SoC 厂商 / 移植新芯片 |
| 2 | **协议驱动作者** | 驱动一颗挂在 SPI 上的芯片 | §2 | **最常见** |
| 3 | **QSPI 控制器驱动作者** | 实现 `mem_ops` 让 flash 走硬件加速 | §3 | 存储相关 |
| 4 | **用户态程序作者** | 通过 `/dev/spidevB.C` 访问芯片 | §4 | 调试 / 原型 |
| 5 | **slave handler 作者** | SoC 当从机时的协议处理 | §5 | ✗ 极少 |

**先确认你是哪个角色**——五者要读的内容几乎不重叠。最常见的误判是
「我要驱动一颗 SPI 芯片」的人去读控制器驱动那一节，那一节和你无关。

---

## §1 控制器驱动

### §1.1 三个必答问题

写之前先回答，答案决定骨架长什么样：

| 问题 | 选项 | 影响 |
|-----|-----|------|
| **传输回调实现哪个？** | `transfer_one`（55 个驱动）/ `transfer_one_message`（29 个） | 决定核心替你做多少事 |
| **同步还是异步？** | `transfer_one` 返 0（同步，已完成）/ 返 1（异步，等 ISR 调 `spi_finalize_current_transfer`） | 决定 ISR 要不要写 |
| **要 DMA 吗？** | 实现 `can_dma`（24 个）/ 不实现 | 决定要不要处理 sg_table |

**绝大多数新驱动应该选 `transfer_one` + 异步 + 先不做 DMA**。理由：
`transfer_one` 让核心替你做片选、延时、参数校验、拆分（`03-internals.md` §0.1
那张表）；DMA 可以后加，PIO 版本先跑通。

### §1.2 最小骨架（`transfer_one`，PIO，异步）

```c
/* 未编译。结构参照 drivers/spi/spi-rockchip.c 与 drivers/spi/spi-oc-tiny.c */

struct myspi {
        struct spi_controller   *ctlr;
        void __iomem            *regs;
        struct clk              *clk;
        struct completion        xfer_done;   /* 若用 wait，本例用核心的 completion */
        const u8                *tx;
        u8                      *rx;
        unsigned int             len;
        unsigned int             pos;
};

/* ---- 1. 片选 ------------------------------------------------------ */
/* 只有「核心不替你管片选」时才需要，判据见 03 §B2.2 */
static void myspi_set_cs(struct spi_device *spi, bool enable)
{
        struct myspi *p = spi_controller_get_devdata(spi->controller);

        /* enable 是「逻辑激活」，不是电平。极性核心已经算进去了 */
        writel(enable ? MY_CS_ASSERT : MY_CS_DEASSERT, p->regs + MY_CS);
}

/* ---- 2. 每个 message 之前（可选） -------------------------------- */
static int myspi_prepare_message(struct spi_controller *ctlr,
                                 struct spi_message *msg)
{
        /* 整个 message 共用的硬件设置：模式、字节序 */
        return 0;
}

/* ---- 3. 传输一个 transfer（核心回调） ---------------------------- */
static int myspi_transfer_one(struct spi_controller *ctlr,
                              struct spi_device *spi,
                              struct spi_transfer *xfer)
{
        struct myspi *p = spi_controller_get_devdata(ctlr);

        /* 速率/位宽逐 transfer 生效，每次都要重设 */
        myspi_set_speed(p, xfer->speed_hz);
        myspi_set_bpw(p, xfer->bits_per_word);

        /* 回填实际速率——影响 SPI_DELAY_UNIT_SCK 的精度，见 03 §B5.1 */
        xfer->effective_speed_hz = myspi_actual_hz(p);

        p->tx  = xfer->tx_buf;    /* 可能为 NULL，除非声明了 SPI_CONTROLLER_MUST_TX */
        p->rx  = xfer->rx_buf;
        p->len = xfer->len;
        p->pos = 0;

        myspi_fill_fifo(p);
        writel(MY_IRQ_ALL, p->regs + MY_IRQ_EN);

        return 1;                 /* 1 = 异步，核心去等 */
}

/* ---- 4. 中断处理 ------------------------------------------------- */
static irqreturn_t myspi_irq(int irq, void *dev_id)
{
        struct myspi *p = dev_id;

        myspi_drain_fifo(p);
        if (p->pos < p->len) {
                myspi_fill_fifo(p);
                return IRQ_HANDLED;
        }

        writel(0, p->regs + MY_IRQ_EN);
        spi_finalize_current_transfer(p->ctlr);   /* 通知核心这一段完了 */
        return IRQ_HANDLED;
}

/* ---- 5. 错误恢复（可选，11 个驱动实现） -------------------------- */
static void myspi_handle_err(struct spi_controller *ctlr,
                             struct spi_message *msg)
{
        struct myspi *p = spi_controller_get_devdata(ctlr);

        writel(0, p->regs + MY_IRQ_EN);
        writel(MY_RESET, p->regs + MY_CTRL);      /* 复位 FIFO */
}

/* ---- 6. probe ---------------------------------------------------- */
static int myspi_probe(struct platform_device *pdev)
{
        struct spi_controller *ctlr;
        struct myspi *p;
        int ret;

        /* devm_ 版本：失败路径不需要手工 put */
        ctlr = devm_spi_alloc_master(&pdev->dev, sizeof(*p));
        if (!ctlr)
                return -ENOMEM;



        p = spi_controller_get_devdata(ctlr);
        p->ctlr = ctlr;
        platform_set_drvdata(pdev, ctlr);

        p->regs = devm_platform_ioremap_resource(pdev, 0);
        if (IS_ERR(p->regs))
                return PTR_ERR(p->regs);

        p->clk = devm_clk_get(&pdev->dev, NULL);
        if (IS_ERR(p->clk))
                return PTR_ERR(p->clk);

        /* ---- 能力声明：这几行决定核心怎么对待你 ---- */
        ctlr->dev.of_node        = pdev->dev.of_node;   /* 必须，否则 DT 子节点不会被枚举 */
        ctlr->bus_num            = -1;                 /* 让核心自己分配 */
        ctlr->num_chipselect     = 4;
        ctlr->mode_bits          = SPI_CPOL | SPI_CPHA | SPI_CS_HIGH | SPI_LSB_FIRST;
        ctlr->bits_per_word_mask = SPI_BPW_MASK(8) | SPI_BPW_MASK(16);
        ctlr->min_speed_hz       = 1000;
        ctlr->max_speed_hz       = 50000000;
        ctlr->use_gpio_descriptors = true;             /* 推荐，见下 */
        ctlr->auto_runtime_pm    = true;               /* 需要配 pm_runtime */

        ctlr->setup            = myspi_setup;          /* 可选 */
        ctlr->prepare_message  = myspi_prepare_message;
        ctlr->transfer_one     = myspi_transfer_one;
        ctlr->set_cs           = myspi_set_cs;
        ctlr->handle_err       = myspi_handle_err;

        ret = devm_request_irq(&pdev->dev, irq, myspi_irq, 0,
                               dev_name(&pdev->dev), p);
        if (ret)
                return ret;

        pm_runtime_enable(&pdev->dev);                 /* auto_runtime_pm 的前提 */

        /* devm_ 版本会在 remove 时自动 unregister */
        ret = devm_spi_register_controller(&pdev->dev, ctlr);
        if (ret) {
                pm_runtime_disable(&pdev->dev);
                return ret;
        }
        return 0;
}
```

**`master` / `host` 两套名字在 6.1.158 里是并存的，而且不是别名关系**——
`include/linux/spi/spi.h:786-848` 里是**八个各自独立的 `static inline`**，
两两成对包同一个底层函数：

| 旧名 | 新名 | 底层 |
|-----|-----|-----|
| `spi_alloc_master` `:786` | `spi_alloc_host` `:801` | `__spi_alloc_controller(dev, size, false)` |
| `spi_alloc_slave` `:792` | `spi_alloc_target` `:807` | `__spi_alloc_controller(dev, size, true)` |
| `devm_spi_alloc_master` `:820` | `devm_spi_alloc_host` `:835` | `__devm_spi_alloc_controller(dev, size, false)` |
| `devm_spi_alloc_slave` `:826` | `devm_spi_alloc_target` `:841` | `__devm_spi_alloc_controller(dev, size, true)` |

**行为完全等价，写哪个都对。** 本篇骨架统一用 `master` 版，理由是本树里
`devm_spi_alloc_master` 有 41 个使用者、`devm_spi_alloc_host` 只有 3 个，
`spi_alloc_host` **0 个**——照多数派写，读者拿真实驱动对照时不会错位。
新代码建议用 `host`/`target` 版。计数命令见 `99-evidence.md` §5.4。

### §1.3 六个容易做错的地方

| 做错什么 | 后果 | 正确做法 | 依据 |
|---------|-----|---------|------|
| 忘了 `ctlr->dev.of_node = pdev->dev.of_node` | **DT 子节点一个都不会被枚举，也没有任何日志** | 必须赋值 | `of_register_spi_devices()` 在 `ctlr->dev.of_node` 为 NULL 时直接返回，`drivers/spi/spi.c:2390` 一带 |
| `transfer_one` 返 0 但实际是异步的 | 核心以为完了，立刻做下一段，**数据错乱** | 异步返 1 | `03-internals.md` §A10 |
| 不回填 `effective_speed_hz` | `SPI_DELAY_UNIT_SCK` 延时按请求速率一半估算，偏长一倍 | 回填 | `drivers/spi/spi.c:1442` |
| `mode_bits` 漏了实际支持的位 | `spi_setup()` 返 `-EINVAL`，设备 probe 失败 | 照实声明 | `spi_setup()` `drivers/spi/spi.c:3702` |
| DMA 失败只返 `-EIO`，不置 `SPI_TRANS_FAIL_NO_START` | **核心不会回退到 PIO** | 确认一个时钟都没摇时置这个标志 | `drivers/spi/spi.c:1542-1548` |
| `set_cs` 里当成电平处理，自己再翻一次极性 | 片选反了 | `enable` 已经是逻辑值 | `03-internals.md` §B2.2 |

关于 `use_gpio_descriptors = true`（42 个驱动这么做）：**强烈推荐**。
它让核心自动从 `cs-gpios` 拿 GPIO、自动加 `SPI_CS_HIGH` 到 `mode_bits`
（`drivers/spi/spi.c:3174`）、自动管 GPIO 片选。代价是你的 `set_cs` 回调
默认不会被调（除非设 `SPI_MASTER_GPIO_SS`）。

这个标志名**不是笔误**：`spi.h:549-556` 那一组 `flags` 里，`HALF_DUPLEX`/`NO_RX`/`NO_TX`/`MUST_RX`/`MUST_TX`/`SUSPENDED` 六个都已经改叫 `SPI_CONTROLLER_*`，只有 BIT(5) 这一个**至今仍只有 `SPI_MASTER_GPIO_SS` 一个名字**（`:555`），没有 `SPI_CONTROLLER_GPIO_SS`。改名那一轮漏了它，所以别照着别的标志去猜。

### §1.4 加 DMA

```c
static bool myspi_can_dma(struct spi_controller *ctlr, struct spi_device *spi,
                          struct spi_transfer *xfer)
{
        /* 逐 transfer 问的。短传输走 PIO 更快 */
        return xfer->len > MY_DMA_THRESHOLD;
}

static int myspi_probe(...)
{
        ...
        ctlr->can_dma       = myspi_can_dma;
        ctlr->dma_tx        = dma_request_chan(dev, "tx");
        ctlr->dma_rx        = dma_request_chan(dev, "rx");
        /* 声明这两个，核心才会补哑缓冲，见 03 §B3.1 */
        ctlr->flags         = SPI_CONTROLLER_MUST_TX | SPI_CONTROLLER_MUST_RX;
        ctlr->max_dma_len   = 65535;
        ...
}
```

在 `transfer_one` 里用 `ctlr->cur_msg_mapped` 判断这次要不要走 DMA
（核心已经映射好了，sg_table 在 `xfer->tx_sg` / `xfer->rx_sg`）。

**`max_dma_len` 和 `max_transfer_size` 是两回事**：前者是 DMA 引擎一次能
搬多少（核心据此切 sg），后者是控制器一次能传多少（核心据此**拆 transfer**，
`03-internals.md` §B4）。

### §1.5 suspend / resume

```c
static int __maybe_unused myspi_suspend(struct device *dev)
{
        struct spi_controller *ctlr = dev_get_drvdata(dev);
        int ret;

        ret = spi_controller_suspend(ctlr);   /* 先让核心停下来 */
        if (ret)
                return ret;

        clk_disable_unprepare(...);
        return 0;
}

static int __maybe_unused myspi_resume(struct device *dev)
{
        struct spi_controller *ctlr = dev_get_drvdata(dev);

        clk_prepare_enable(...);
        return spi_controller_resume(ctlr);   /* 最后才恢复核心 */
}
```

**顺序不能反**：`spi_controller_suspend()`（`drivers/spi/spi.c:3358`）
会停掉消息泵并置 `SPI_CONTROLLER_SUSPENDED`，此后新的 `spi_sync()` 返
`-ESHUTDOWN`。先关时钟再停核心 = 正在进行的传输访问已停的硬件。

---

## §2 协议驱动（驱动一颗 SPI 芯片）

### §2.1 最小骨架

```c
/* 未编译 */

struct mysensor {
        struct spi_device *spi;
        struct mutex       lock;
        u8                 buf[4] ____cacheline_aligned;   /* DMA 安全 */
};

static int mysensor_probe(struct spi_device *spi)
{
        struct mysensor *s;

        s = devm_kzalloc(&spi->dev, sizeof(*s), GFP_KERNEL);
        if (!s)
                return -ENOMEM;

        s->spi = spi;
        mutex_init(&s->lock);
        spi_set_drvdata(spi, s);

        /* 注意：spi_setup() 已经在核心里调过了（03 §A5.2），
         *    这里改参数需要自己再调一次 */
        spi->mode          = SPI_MODE_0;
        spi->bits_per_word = 8;
        spi->max_speed_hz  = min(spi->max_speed_hz, 10000000U);
        if (spi_setup(spi))
                return -EIO;

        return mysensor_check_id(s);
}

static void mysensor_remove(struct spi_device *spi)   /* 返回 void，不是 int */
{
        /* devm_ 分配的东西不用手工释放 */
}

static const struct of_device_id mysensor_of_match[] = {
        { .compatible = "myvendor,mysensor" },
        { }
};
MODULE_DEVICE_TABLE(of, mysensor_of_match);

static const struct spi_device_id mysensor_spi_ids[] = {
        { "mysensor" },              /* 不带 vendor 前缀！见 §2.4 */
        { }
};
MODULE_DEVICE_TABLE(spi, mysensor_spi_ids);

static struct spi_driver mysensor_driver = {
        .driver = {
                .name           = "mysensor",
                .of_match_table = mysensor_of_match,
        },
        .id_table = mysensor_spi_ids,
        .probe    = mysensor_probe,
        .remove   = mysensor_remove,
};
module_spi_driver(mysensor_driver);
```

**`remove` 返回 `void`**（`include/linux/spi/spi.h:316`）。
这是 5.18 之后的变化，抄旧代码会编译失败。

### §2.2 五种读写方式，按简单度排

| 方式 | 什么时候用 | 上限 | 注意 |
|-----|----------|-----|------|
| `spi_w8r8(spi, cmd)` | 发 1 字节命令，收 1 字节 | 1+1 | 返回值 `>= 0` 是数据，`< 0` 是错误码 |
| `spi_w8r16(spi, cmd)` / `spi_w8r16be(spi, cmd)` | 发 1 字节，收 2 字节 | 1+2 | `be` 版本帮你转字节序 |
| `spi_write_then_read(spi, tx, n, rx, m)` | 先发后收（**半双工**） | **n+m ≤ 4096** | 用全局缓冲 + 全局 mutex，会串行化 |
| `spi_sync_transfer(spi, xfers, n)` | 需要多段或全双工 | 无 | 推荐的通用方式 |
| `spi_message_init` + `add_tail` + `spi_sync` | 需要 `cs_change` / 逐段改参数 | 无 | 最灵活 |

```c
/* 方式 1：最简 */
static int mysensor_read_id(struct mysensor *s)
{
        int ret = spi_w8r8(s->spi, MYSENSOR_CMD_RDID);

        if (ret < 0)
                return ret;
        return ret == MYSENSOR_ID ? 0 : -ENODEV;
}

/* 方式 3：先发后收，半双工 */
static int mysensor_read_reg(struct mysensor *s, u8 reg, u8 *val)
{
        u8 cmd = MYSENSOR_READ | reg;

        return spi_write_then_read(s->spi, &cmd, 1, val, 1);
}

/* 方式 4：全双工，一次完成 */
static int mysensor_read_burst(struct mysensor *s, u8 reg, u8 *out, size_t n)
{
        struct spi_transfer xfers[2] = {
                { .tx_buf = &reg,  .len = 1 },
                { .rx_buf = out,   .len = n },
        };

        return spi_sync_transfer(s->spi, xfers, ARRAY_SIZE(xfers));
}

/* 方式 5：需要保持片选跨两段 */
static int mysensor_atomic_pair(struct mysensor *s)
{
        struct spi_transfer x[2] = {
                { .tx_buf = s->buf,     .len = 2, .cs_change = 1 },
                { .rx_buf = s->buf + 2, .len = 2 },
        };
        struct spi_message m;

        spi_message_init(&m);
        spi_message_add_tail(&x[0], &m);
        spi_message_add_tail(&x[1], &m);

        return spi_sync(s->spi, &m);
}
```

**`spi_write_then_read()` 的 4096 上限是硬的**：超了返 `-EINVAL`
（`drivers/spi/spi.c:4371` 起，用的是 `SPI_BUFSIZ` 全局缓冲）。
而且它用**全局 mutex**——所有用它的驱动互相串行化。
**大数据量或高频访问不要用它。**

一个非常容易漏的点：**`cs_change` 在最后一个 transfer 上的语义是反的**。
中间的 transfer 上 `cs_change = 1` 表示「翻一下片选」；
最后一个上 `cs_change = 1` 表示「**别松片选**」，留给下一个 message。
kerneldoc 在 `include/linux/spi/spi.h:984-1000` 的注释里说明了这一点
（`:988` 起的 (i)/(ii) 两段就是这两种语义；`@cs_change` 的一句话摘要在 `:921`）。

### §2.3 缓冲对齐

```c
struct mysensor {
        ...
        u8 buf[4] ____cacheline_aligned;   /* */
};
```

**为什么需要 `____cacheline_aligned`**：如果 DMA 缓冲和别的数据共享一条
cache line，DMA 写回时会污染邻居。这正是 `spi-loopback-test` 用例 15
（`three tx+rx transfers with overlapping cache lines`，
`drivers/spi/spi-loopback-test.c:317`）在测的东西。

其它三条缓冲规则：

| 规则 | 原因 |
|-----|-----|
| **不要用栈上的缓冲** | 栈可能不是 DMA-able 的 |
| **不要用 `vmalloc` 的缓冲** | 物理不连续，DMA 会崩（loopback-test 的 `use_vmalloc=1` 就在测这个） |
| **传输期间不能改缓冲内容** | `include/linux/spi/spi.h:1082-1088` 的 kerneldoc 明确说了所有权归核心 |

### §2.4 匹配机制的两个坑

`03-internals.md` §A5.1 讲了四级匹配顺序。对写协议驱动的人，两个必须知道：

**坑 1：`spi_device_id` 里不带 vendor 前缀。**

```
DT:            compatible = "myvendor,mysensor";
                                     ↓ of_modalias_node() 砍掉 "myvendor,"
spi->modalias:                "mysensor"
                                     ↓ 和 id_table 比
spi_device_id: { "mysensor" }        ✓ 匹配
               { "myvendor,mysensor" }   ✗ 永远不会匹配
```

但 `of_match_table` 里**必须**带前缀。**两张表的字符串不一样**，
这是最常见的「驱动装了但不 probe」的原因。

**坑 2：`spi_device_id` 名字长度限制。**

`spi->modalias` 是 `char modalias[SPI_NAME_SIZE]`，`SPI_NAME_SIZE` = 32
（定义**不在** `spi.h` 里，在 `include/linux/mod_devicetable.h:498`；
`spi.h` 只是在 `:200` 和 `:1517` 用它开数组）。超长会被截断。

### §2.5 异步提交

需要在中断里发起传输，或者不想阻塞：

```c
static void mysensor_done(void *arg)
{
        struct mysensor *s = arg;

        /* 上下文由核心决定——可能是 ISR，所以这里不能睡 */
        if (s->msg.status)
                dev_err(&s->spi->dev, "xfer failed: %d\n", s->msg.status);
        else
                mysensor_process(s, s->buf);
}

static int mysensor_start(struct mysensor *s)
{
        spi_message_init(&s->msg);
        s->xfer.rx_buf = s->buf;
        s->xfer.len    = sizeof(s->buf);
        spi_message_add_tail(&s->xfer, &s->msg);

        s->msg.complete = mysensor_done;
        s->msg.context  = s;

        return spi_async(s->spi, &s->msg);   /* 可在原子上下文调 */
}
```

**三条硬规则**：

1. **`complete` 回调不能睡**——它可能在 ISR 里跑（`03-internals.md` §B1.4）。
2. **`msg` 和 `xfer` 必须在提交后仍然有效**，不能是栈上的。
3. **`spi_async()` 在 `spi_bus_lock()` 期间返 `-EBUSY`**，而 `spi_sync()`
   会阻塞等待——这两者行为不一致（`03-internals.md` §B1.2）。

### §2.6 五个容易做错的地方

| 做错什么 | 后果 | 依据 |
|---------|-----|------|
| `remove` 写成返回 `int` | 编译失败 | `include/linux/spi/spi.h:316` |
| `spi_device_id` 带了 vendor 前缀 | 只有 DT 匹配能工作，非 DT 路径失效 | `of_modalias_node()` |
| 以为 probe 里必须先调 `spi_setup()` | 冗余——核心在**注册设备时**就已经调过了 | `__spi_add_device()` `drivers/spi/spi.c:669`（不是 `spi_probe()`：`spi_probe()` `:410` 只解析 irq / of_clk_set_defaults，不碰 `spi_setup()`） |
| 用栈上/`vmalloc` 的缓冲 | DMA 控制器上偶发数据错或崩 | §2.3 |
| 在 `complete` 回调里调 `spi_sync()` | 死锁 | §2.5 规则 1 |

---

## §3 QSPI 控制器驱动（实现 `mem_ops`）

### §3.1 什么时候需要

**只有你的控制器有「flash 专用硬件加速器」时才需要。** 判据：控制器的
寄存器接口是「写 opcode + 地址 + 线宽，硬件自己产生完整时序」，而不是
「往数据寄存器塞字节」。典型是各家的 QSPI / FSPI / SFC 控制器。

**普通 SPI 控制器不要实现 `mem_ops`**——核心会自动把 `spi_mem_op` 翻译成
`spi_transfer`（`04-extensions.md` §1.4 路径 B），什么都不用做。

### §3.2 九个回调

`struct spi_controller_mem_ops`（`include/linux/spi/spi-mem.h:269-288`）：

| 回调 | 必需 | 说明 |
|-----|-----|------|
| `exec_op` | **是** | 执行一个 op。**不支持的 op 必须返 `-ENOTSUPP`** |
| `supports_op` | 否 | 判断能不能做。不实现则用 `spi_mem_default_supports_op()` |
| `adjust_op_size` | 否 | 把 op 削到硬件能做的大小。不实现则按 `max_transfer_size` 削 |
| `get_name` | 否 | 自定义设备名 |
| `dirmap_create` / `dirmap_destroy` | 否 | 建立/销毁 memory-mapped 映射 |
| `dirmap_read` / `dirmap_write` | 否 | 通过映射读写 |
| `poll_status` | 否 | 硬件轮询状态寄存器（省 CPU） |

另有 `ctlr->mem_caps`（`struct spi_controller_mem_caps`
`include/linux/spi/spi-mem.h:295-298`），两个能力位：`dtr`（双沿传输）
和 `ecc`。用 `spi_mem_controller_is_capable(ctlr, dtr)` 宏查询（`:300`）——
注意它是**宏**，展开成 `((ctlr)->mem_caps && (ctlr)->mem_caps->cap)`，
所以 `mem_caps` 为 `NULL` 时安全返假，不需要自己先判空。
本树只有 3 个驱动填了 `mem_caps`（见 `01-api.md` §1.3）。

### §3.3 骨架

```c
/* 未编译。结构参照 drivers/spi/spi-rockchip-sfc.c、spi-stm32-qspi.c */

static bool myqspi_supports_op(struct spi_mem *mem,
                               const struct spi_mem_op *op)
{
        /* 第一步一定要先调默认检查——它验线宽和 3WIRE 之类的通用约束 */
        if (!spi_mem_default_supports_op(mem, op))
                return false;

        /* 我的硬件只支持 1/2/4 线，不支持 8 线 */
        if (op->cmd.buswidth > 4 || op->addr.buswidth > 4 ||
            op->data.buswidth > 4)
                return false;

        /* 我的硬件地址最多 4 字节 */
        if (op->addr.nbytes > 4)
                return false;

        return true;
}

static int myqspi_adjust_op_size(struct spi_mem *mem, struct spi_mem_op *op)
{
        /* 上层会循环，所以削小是安全的 */
        if (op->data.nbytes > MYQSPI_MAX_DATA)
                op->data.nbytes = MYQSPI_MAX_DATA;
        return 0;
}

static int myqspi_exec_op(struct spi_mem *mem, const struct spi_mem_op *op)
{
        struct myqspi *q = spi_controller_get_devdata(mem->spi->controller);
        int ret;

        /* 进来时核心已经持了 bus_lock_mutex 和 io_mutex（04 §1.4）。
         *    所以这里绝对不能调 spi_sync() / spi_setup() —— 会自死锁 */

        myqspi_setup_clk(q, mem->spi->max_speed_hz);

        writel(op->cmd.opcode,   q->regs + MYQSPI_CMD);
        writel(op->addr.val,     q->regs + MYQSPI_ADDR);
        writel(MYQSPI_CTRL(op->addr.nbytes, op->dummy.nbytes,
                           op->cmd.buswidth, op->addr.buswidth,
                           op->data.buswidth),
               q->regs + MYQSPI_CTRL);

        if (op->data.nbytes == 0)
                ret = myqspi_run_no_data(q);
        else if (op->data.dir == SPI_MEM_DATA_IN)
                ret = myqspi_read(q, op->data.buf.in, op->data.nbytes);
        else
                ret = myqspi_write(q, op->data.buf.out, op->data.nbytes);

        return ret;
}

static const struct spi_controller_mem_ops myqspi_mem_ops = {
        .adjust_op_size = myqspi_adjust_op_size,
        .supports_op    = myqspi_supports_op,
        .exec_op        = myqspi_exec_op,
};

static int myqspi_probe(struct platform_device *pdev)
{
        struct spi_controller *ctlr;
        ...
        ctlr = devm_spi_alloc_master(&pdev->dev, sizeof(*q));
        ...
        ctlr->mem_ops   = &myqspi_mem_ops;
        ctlr->mode_bits = SPI_RX_DUAL | SPI_RX_QUAD | SPI_TX_DUAL | SPI_TX_QUAD;
        ctlr->max_speed_hz = 100000000;
        ctlr->dev.of_node  = pdev->dev.of_node;
        /* 纯 mem_ops 控制器不需要 transfer_one，核心不会建消息泵（03 §A2） */
        ...
        return devm_spi_register_controller(&pdev->dev, ctlr);
}
```

### §3.4 五个容易做错的地方

| 做错什么 | 后果 | 依据 |
|---------|-----|------|
| 不支持的 op 返 `-EIO` 而不是 `-ENOTSUPP` | **得不到软件回退**，flash 探测失败 | `drivers/spi/spi-mem.c:342` |
| `supports_op` 里不先调 `spi_mem_default_supports_op()` | 漏掉通用约束检查 | `:164` |
| 在 `exec_op` 里调 `spi_sync()` 或 `spi_setup()` | **自死锁**（`io_mutex` 已被持有） | `:283-284` |
| 以为 `dummy.nbytes` 是时钟周期数 | 高速读时序不对，读出乱数据 | `04-extensions.md` §1.3 |
| DT 里给节点配了 `cs-gpios` | **`mem_ops` 被完全跳过**，静默退化成 PIO | `:328` |

最后一条值得重复：**给 QSPI 节点配 `cs-gpios` 会让硬件加速失效，
而且没有任何日志。** 症状是 flash 读速度只有预期的十分之一。
`06-devicetree.md` 的症状清单里有这一条。

---

## §4 用户态程序（spidev）

### §4.1 前置条件

三件事都要满足，缺一个就没有 `/dev/spidevB.C`：

1. `CONFIG_SPI_SPIDEV=y`（或 `=m` 并 `modprobe spidev`）
2. DT 里有一个 SPI 子节点，`compatible` **在 spidev 的白名单里**
   （`04-extensions.md` §2.6 那 11 项）
3. 该节点的 `reg`（片选号）不与别的设备冲突

白名单是硬编码的，写 `compatible = "spidev"` 会得到
`spidev listed directly in DT is not supported`。实践中借用
`rohm,dh2228fv`。

### §4.2 最小程序

```c
/* 未编译。API 参照 tools/spi/spidev_test.c */
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/spi/spidev.h>

int main(void)
{
        int      fd;
        uint8_t  mode  = SPI_MODE_0;
        uint8_t  bits  = 8;
        uint32_t speed = 1000000;
        uint8_t  tx[4] = { 0x9f, 0, 0, 0 };
        uint8_t  rx[4] = { 0 };

        fd = open("/dev/spidev0.0", O_RDWR);
        if (fd < 0)
                return 1;

        /* 三个设置都要检查返回值——失败说明控制器不支持 */
        if (ioctl(fd, SPI_IOC_WR_MODE, &mode) < 0)          return 1;
        if (ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0) return 1;
        if (ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) return 1;

        struct spi_ioc_transfer xfer = {
                .tx_buf        = (unsigned long)tx,
                .rx_buf        = (unsigned long)rx,
                .len           = sizeof(tx),
                .speed_hz      = speed,
                .bits_per_word = bits,
                .delay_usecs   = 0,
                .cs_change     = 0,
        };

        /* 参数是 transfer 的个数，不是字节数 */
        if (ioctl(fd, SPI_IOC_MESSAGE(1), &xfer) < 1)
                return 1;

        /* rx 里是收到的 4 字节 */
        close(fd);
        return 0;
}
```

### §4.3 `struct spi_ioc_transfer` 全字段

`include/uapi/linux/spi/spidev.h:70-92`：

| 字段 | 类型 | 说明 |
|-----|------|------|
| `tx_buf` / `rx_buf` | `__u64` | **指针要强转成整数**。NULL 表示这个方向不用 |
| `len` | `__u32` | 字节数 |
| `speed_hz` | `__u32` | **这一段的速率**。0 表示用设备默认 |
| `delay_usecs` | `__u16` | 这一段之后的延时 |
| `bits_per_word` | `__u8` | 这一段的位宽。0 表示用设备默认 |
| `cs_change` | `__u8` | 见 §2.2 的语义警告 |
| `tx_nbits` / `rx_nbits` | `__u8` | 线宽（1/2/4/8）。0 表示单线 |
| `word_delay_usecs` | `__u8` | 字间延时 |
| `pad` | `__u8` | 必须为 0 |

**结构布局在 32 位和 64 位用户态下相同**（`:90` 的注释明说了），
所以不需要 compat 处理。

### §4.4 多段传输

```c
struct spi_ioc_transfer xfers[2] = {
        { .tx_buf = (unsigned long)cmd, .len = 1, .cs_change = 1 },
        { .rx_buf = (unsigned long)buf, .len = 256 },
};

/* SPI_IOC_MESSAGE(2)——编译期常量，不能是变量 */
ioctl(fd, SPI_IOC_MESSAGE(2), xfers);
```

**`SPI_IOC_MESSAGE(N)` 的 N 必须是编译期常量**——它参与 ioctl 号的计算
（`SPI_MSGSIZE(N)`，`include/uapi/linux/spi/spidev.h:95-98`）。
段数运行时才知道的话，只能用 `SPI_IOC_MESSAGE(1)` 的 `_IOC_NR` 加上手工
计算的大小——`spidev_test.c` 里就是这么做的。

### §4.5 三条限制

| 限制 | 值 | 依据 |
|-----|---|------|
| 一次 message 的总字节数 | `bufsiz`，默认 **4096** | `drivers/spi/spidev.c:86` |
| 同时存在的 spidev 实例 | **32** | `:44` |
| `bufsiz` 能运行期改吗 | **不能**，只能 `modprobe spidev bufsiz=65536` | `:87` 权限是 `S_IRUGO` |

### §4.6 现成工具

内核自带两个，源码在 `tools/spi/`：

| 工具 | 源码 | 用途 |
|-----|------|------|
| `spidev_test` | `tools/spi/spidev_test.c` | 通用收发测试，支持指定模式/速率/线宽/回环 |
| `spidev_fdx` | `tools/spi/spidev_fdx.c` | 全双工示例 |

编译：`make -C tools/spi`。用法见 `07-debug.md`。

**调试时优先用 `spidev_test` 而不是自己写程序**——它已经处理了
`SPI_IOC_MESSAGE(N)` 的可变段数、字节序、`--verbose` 输出格式。

---

## §5 slave handler

### §5.1 前置条件

**先确认控制器支持从机模式**：驱动里要能找到 `devm_spi_alloc_slave()` /
`spi_alloc_slave()`，或者 `__spi_alloc_controller(..., true)`。
找不到就说明这颗控制器只能当主机，本节与你无关。

然后 `CONFIG_SPI_SLAVE=y`。

### §5.2 骨架

```c
/* 未编译。结构参照 drivers/spi/spi-slave-time.c */

struct myslave {
        struct spi_device  *spi;
        struct completion   finished;
        struct spi_transfer xfer;
        struct spi_message  msg;
        u8                  buf[8];
};

static int myslave_submit(struct myslave *p);

static void myslave_complete(void *arg)
{
        struct myslave *p = arg;

        if (p->msg.status)
                goto terminate;

        /* 处理收到的数据，准备下一轮的回送内容 */
        myslave_prepare_response(p);

        /* 关键：立刻再挂一个 message 上去等下一次主机访问 */
        if (myslave_submit(p))
                goto terminate;
        return;

terminate:
        dev_info(&p->spi->dev, "Terminating\n");
        complete(&p->finished);
}

static int myslave_submit(struct myslave *p)
{
        p->xfer.tx_buf = p->buf;
        p->xfer.rx_buf = p->buf;
        p->xfer.len    = sizeof(p->buf);

        spi_message_init_with_transfers(&p->msg, &p->xfer, 1);
        p->msg.complete = myslave_complete;
        p->msg.context  = p;

        /* 必须是 async——从机不知道主机什么时候来，不能阻塞 */
        return spi_async(p->spi, &p->msg);
}

static int myslave_probe(struct spi_device *spi)
{
        struct myslave *p;

        p = devm_kzalloc(&spi->dev, sizeof(*p), GFP_KERNEL);
        if (!p)
                return -ENOMEM;

        p->spi = spi;
        init_completion(&p->finished);
        spi_set_drvdata(spi, p);

        return myslave_submit(p);      /* probe 里就挂上去 */
}

static void myslave_remove(struct spi_device *spi)
{
        struct myslave *p = spi_get_drvdata(spi);

        /* 必须 abort——否则 message 永远挂着，remove 挂死 */
        spi_slave_abort(spi);
        wait_for_completion(&p->finished);
}
```

### §5.3 绑定 handler

从机 handler **不通过 DT compatible 匹配**（DT 里只写一个名叫 `slave` 的
子节点，`03-internals.md` §A4.3）。绑定靠 sysfs：

```bash
# 查看当前绑的 handler
cat /sys/class/spi_slave/spi0/slave

# 绑一个
echo myslave > /sys/class/spi_slave/spi0/slave

# 解绑
echo '(null)' > /sys/class/spi_slave/spi0/slave
```

**写入会先删掉现有的从设备再加新的**（`slave_store()`
`drivers/spi/spi.c:2811`）——所以一个控制器同时只能有一个 handler。

### §5.4 四个容易做错的地方

| 做错什么 | 后果 | 依据 |
|---------|-----|------|
| 用 `spi_sync()` 而不是 `spi_async()` | 主机不来就永久阻塞 | §5.2 |
| `remove` 里不调 `spi_slave_abort()` | **`rmmod` 挂死** | §5.2 |
| 用 `spi_target_abort()` | 6.1 里必然返 `-ENOTSUPP` | `drivers/spi/spi.c:2795` |
| DT 子节点没命名为 `slave` | 静默不枚举 | `:2306-2313` |

---

## §6 五个角色的速查

| 你要做的事 | 关键 API | 最容易踩的坑 |
|----------|---------|-----------|
| **写控制器驱动** | `devm_spi_alloc_master` → 填能力 + `transfer_one` → `devm_spi_register_controller`；ISR 里 `spi_finalize_current_transfer` | 忘了 `ctlr->dev.of_node`（子节点静默不枚举）；异步却返 0 |
| **写协议驱动** | `spi_w8r8` / `spi_write_then_read` / `spi_sync_transfer` | `id_table` 带了 vendor 前缀；`remove` 写成返 `int` |
| **写 QSPI 驱动** | `ctlr->mem_ops = &...`，实现 `exec_op` | 不支持的 op 没返 `-ENOTSUPP`；DT 里配了 `cs-gpios` |
| **写用户态程序** | `SPI_IOC_WR_MODE` / `SPI_IOC_MESSAGE(N)` | 4096 字节上限；DT compatible 不在白名单 |
| **写 slave handler** | `spi_async` 自循环 + `spi_slave_abort` | `remove` 里忘了 abort 导致挂死 |

### §6.1 一条通用建议

**新写的驱动先用 `spi-loopback-test` 跑一遍**（`04-extensions.md` §6）。
它的 15 个用例覆盖了跨页、哑缓冲、cache line 重叠、逐段改参数——这四类
恰好是自己测试时最容易漏的。

```bash
modprobe spi-loopback-test loopback=1
# 全过了再接真实外设
```

---

## §7 近邻符号区分：名字很像，行为不一样

本节是**常备对照表**，不是陷阱清单。
上面 §1~§5 每节末尾的"容易做错的地方"讲的是**用法**错了；这一节讲的是
**挑错了符号**——编译能过、名字念起来一样、行为不同。SPI 这种"一套 API
演进了二十年 + 中途改了一次命名"的子系统，这类坑密度特别高。

判断标准：**如果两个符号的名字差异小于它们行为的差异，就必须成对写出来。**

---

### §7.1 提交路径：`spi_sync` / `spi_async` / `spi_sync_locked`

| 符号 | 位置 | 阻塞？ | 能在中断里调？ | 需要先拿总线锁？ |
|-----|------|-------|-------------|--------------|
| `spi_async` | `drivers/spi/spi.c:4060`，声明 `spi.h:1207` | 不阻塞，靠 `msg->complete` 回调 | **能** | 不需要 |
| `spi_sync` | `spi.c:4258`，声明 `spi.h:1307` | 阻塞到传输完成 | **不能** | 不需要 |
| `spi_sync_locked` | 声明 `spi.h:1308` | 阻塞 | **不能** | **必须**，见下 |

三点实证：

1. **`spi_async` 是对外 API 里唯一能在中断上下文调的**。依据是它自己的
   kerneldoc：`Context: any (irqs may be blocked, etc)`（`spi.c:4033`），
   正文还补了一句 "This call may be used in_irq"；而 `spi_sync` 一族的
   kerneldoc 统一写 `Context: can sleep`（`spi.h:1317`/`1341`/`1364`/
   `1391`/`1413`/`1438` 六处，见 §7.4）。
   grep 时会撞见第四个名字 **`spi_async_locked`**（`spi.c:4114`，
   kerneldoc 在 `:4083-4113`，同样标 `Context: any`）——它是
   **`static`，没有 `EXPORT_SYMBOL`，也不在 `spi.h` 里**，
   全树只有 `spi.c:4226` 一个调用者（`__spi_sync()` 内部）。
   **驱动用不了它**，别照着 kerneldoc 抄。
2. **`_locked` 后缀不是"这个函数会帮你上锁"，而是"你必须已经上好锁"**。
   配对用法是 `spi_bus_lock()`（`spi.c:4307`）→ 若干次
   `spi_sync_locked()` → `spi_bus_unlock()`（`spi.c:4336`）。
   用途是把多条 message 拼成一个不可被别的设备插队的原子序列
   （典型场景：先写地址寄存器再读数据，中间不能被抢）。
3. **单发一条 message 不要用 `_locked` 版本**——忘了配 `spi_bus_lock()`
   不会报错，只会在真的有第二个设备共享这条总线时才偶发出错。

### §7.2 完成通知：`spi_finalize_current_transfer` vs `spi_finalize_current_message`

**这是本子系统里最容易挑错、代价最大的一对。**挑错的表现是"传输永久挂死"
或"message 被重复回收"，而不是编译错误。

| 符号 | 尾巴 | 位置 | 谁该调 | 干什么 |
|-----|-----|------|-------|-------|
| `spi_finalize_current_transfer` | **transfer** | `spi.c:1627` | 实现了 **`transfer_one`** 的驱动 | 只做一件事：`complete(&ctlr->xfer_completion)` |
| `spi_finalize_current_message` | **message** | `spi.c:2026` | 自己实现了 **`transfer_one_message`** 的驱动 | 把队首 message 摘掉、跑 `msg->complete`、调度下一条 |

kerneldoc 原文把分工写得很死：

- `_transfer`（`spi.c:1619-1621`）："Called by SPI drivers using the core
  `transfer_one_message()` implementation to notify it that the current
  interrupt driven transfer has finished"——**前提是你用了核心版的
  `transfer_one_message`**。
- `_message`（`spi.c:2020-2022`）："Called by the driver to notify the core
  that the message in the front of the queue is complete"。

记法：**你实现了哪一层，就调另一层的 finalize。**
实现 `transfer_one`（细粒度，一段一段来）→ 调 `_transfer`；
自己接管 `transfer_one_message`（粗粒度，整条 message 自己搞完）→ 调 `_message`。
两个都调 = message 被 finalize 两次；都不调 = 挂死在
`wait_for_completion` 上。§1 的骨架用的是 `transfer_one` 路线，所以
ISR 里写的是 `spi_finalize_current_transfer`。

### §7.3 三个 `transfer` 回调：`transfer` / `transfer_one_message` / `transfer_one`

同一个 `struct spi_controller` 里有三个名字都以 `transfer` 开头的函数指针，
抽象层级从粗到细：

| 回调 | 位置 | 抽象层级 | 新驱动该不该用 |
|-----|------|--------|------------|
| `transfer` | `spi.h:625` | 最老的接口，驱动自己管队列、自己管一切 | **不要用**。注释（`spi.h:606-624`，开头就是 "Bidirectional bulk transfers"）描述的是无队列时代的契约：`spi.h:608` 明写 "The transfer() method may not sleep"，`:610` 明写 "there's no remove-from-queue operation" |
| `transfer_one_message` | `spi.h:672` | 核心管队列，驱动整条 message 自己跑完 | 只在硬件天生按 message 工作（如有硬件描述符链）时用 |
| `transfer_one` | `spi.h:689` | 核心管队列 + 管 message 拆解 + 管 CS，驱动只管一段 | **默认选这个** |

判据在头文件自己的分组注释里：`spi.h:685-687` 写着
"These hooks are for drivers that use a generic implementation of
`transfer_one_message()` provided by the core"，`set_cs`（`spi.h:688`）、
`transfer_one`（`:689`）、`handle_err`（`:690`）是同一组，
只有走核心版 `transfer_one_message` 时才有意义——这也正是 §7.2 那条
"实现了哪一层就调另一层 finalize"的结构依据。

### §7.4 便捷读写：`spi_w8r16` vs `spi_w8r16be`（一个字节序之差）

| 符号 | 位置 | 返回的 16 位数是什么字节序 |
|-----|------|----------------------|
| `spi_w8r16` | `spi.h:1423` | **线上原序**（wire-order），kerneldoc 原话 "which is at least sometimes big-endian"。**没做任何转换** |
| `spi_w8r16be` | `spi.h:1448` | CPU 字节序，内部走了 `be16_to_cpu(result)`（`spi.h:1458`） |

绝大多数 SPI 外设的 16 位寄存器是大端上线的，所以在 arm64（小端）上
**想要正确数值应该用 `spi_w8r16be`**；用了 `spi_w8r16` 会拿到字节颠倒的值。
这个错误在寄存器值恰好对称（如 `0x0000`、`0xffff`、`0x3c3c`）时不会暴露。

同族的完整清单（都是 `static inline`，都封 `spi_write_then_read`）：

| 符号 | 位置 | 写 / 读 |
|-----|------|--------|
| `spi_write` | `spi.h:1349` | 只写 |
| `spi_read` | `spi.h:1372` | 只读 |
| `spi_write_then_read` | 声明 `spi.h:1383`，实现 `spi.c:4371` | 先写后读，**半双工**，两段共用一个内核 bounce buffer |
| `spi_w8r8` | `spi.h:1398` | 写 1 字节命令 → 读 1 字节 |
| `spi_w8r16` | `spi.h:1423` | 写 1 → 读 2，线上原序 |
| `spi_w8r16be` | `spi.h:1448` | 写 1 → 读 2，转成 CPU 序 |

全部 6 个的 kerneldoc 都带 `Context: can sleep`——**一个都不能在中断里用**。

### §7.5 私有数据两套：`spi_get_drvdata` vs `spi_controller_get_devdata`

| 符号 | 位置 | 挂在谁身上 | 谁用 |
|-----|------|----------|-----|
| `spi_set_drvdata` / `spi_get_drvdata` | `spi.h:256` / `:261` | `struct spi_device` | **协议驱动**（§2） |
| `spi_controller_set_devdata` / `spi_controller_get_devdata` | `spi.h:736` / `:731` | `struct spi_controller` 尾部的柔性空间 | **控制器驱动**（§1） |

三点区别：

1. **一个是 `drvdata`，一个是 `devdata`**——中间少一个字母，含义完全不同。
   `drvdata` 是驱动模型通用的 `dev->driver_data`；`devdata` 是
   `spi_alloc_master(dev, size)` 里那个 `size` 分配出来的**紧跟结构体尾部
   的私有区**，不经过驱动模型。
2. **控制器驱动一般不需要 `set`**：`__spi_alloc_controller()` 已经把
   私有区分配好了，直接 `spi_controller_get_devdata()` 取即可（§1.1 骨架
   就是这么写的）。`spi_controller_set_devdata()` 只在自己另外分配了内存、
   想改指向时才用。
3. 旧名 `spi_master_get_devdata` / `spi_master_set_devdata` 是纯 `#define`
   兼容宏（`spi.h:1601` / `:1602-1603`），和新名完全等价——**这一对是真别名**，
   和 §7.8 那种"看起来像别名其实不是"的情况要分开看。

### §7.6 两组 `NO_TX` / `NO_RX`：字段不同、位不同、含义不同

| 符号 | 位置 | 住在哪个字段 | 位 | 含义 |
|-----|------|-----------|---|-----|
| `SPI_NO_TX` | `spi.h:184` | `spi->mode` | BIT(31) | **这颗设备**没接 MOSI 线 |
| `SPI_NO_RX` | `spi.h:185` | `spi->mode` | BIT(30) | **这颗设备**没接 MISO 线 |
| `SPI_CONTROLLER_NO_RX` | `spi.h:550` | `ctlr->flags` | BIT(1) | **这个控制器**做不了纯读 |
| `SPI_CONTROLLER_NO_TX` | `spi.h:551` | `ctlr->flags` | BIT(2) | **这个控制器**做不了纯写 |

**两组既不是新旧名，也不是同一个东西**：一组描述"板子怎么连线"（设备属性，
写在 `spi->mode` 里，可由 DT 的 `spi-no-tx` 之类属性带出来），另一组描述
"控制器有什么做不到"（控制器能力，驱动自己在 probe 里填 `ctlr->flags`）。
名字里少了 `CONTROLLER_` 这一段，位号也刚好一个从高位数、一个从低位数
（`spi.h:186-190` 的注释专门解释了 mode 位"from bit 31 downwards"这个方向）。

写控制器驱动时想声明"我不支持纯读"，填的是 `SPI_CONTROLLER_NO_RX`；
填成 `SPI_NO_RX` 会置上 `ctlr->flags` 的 BIT(30)——那一位在 `flags`
里根本没定义，**不会报错，只是什么也不会发生**。

### §7.7 message 初始化三个：`spi_message_init` / `_no_memset` / `_with_transfers`

| 符号 | 位置 | 会不会先清零 | 用在哪 |
|-----|------|-----------|------|
| `spi_message_init` | `spi.h:1135` | **会**（先 `memset` 再调 `_no_memset`，见 `spi.h:1137-1138`） | 默认选它 |
| `spi_message_init_no_memset` | `spi.h:1129` | 不会，只初始化两个链表头 | 结构体已经是零（如 `kzalloc`/静态变量）时省一次 memset |
| `spi_message_init_with_transfers` | `spi.h:1169` | 会（内部调 `spi_message_init`，`spi.h:1174`） | 已经有一个 `spi_transfer[]` 数组，一次性全挂上（`spi.h:1175-1176` 循环调 `spi_message_add_tail`） |

**栈上的 `struct spi_message` 用 `_no_memset` 是 bug**——栈内存是脏的，
`msg->complete`、`msg->context`、`msg->status` 会带着垃圾值进核心。
省的那次 memset（一个几十字节的结构体）在任何真实场景里都不值得。

### §7.8 `slave` / `target`：**union 同址**，不是别名也不是重复字段

6.1.158 处在 slave→target 改名的中途，这一对的机制**和 master→host 那三种
形式都不一样**（那三种见 `01-api.md` §1.1）。这里是第四种：**`union`**。

```c
/* include/linux/spi/spi.h:561-566 —— 同一个 bool，两个名字 */
	union {
		/* Flag indicating this is an SPI slave controller */
		bool			slave;
		/* Flag indicating this is an SPI target controller */
		bool			target;
	};

/* include/linux/spi/spi.h:679-682 —— 同一个函数指针，两个名字 */
	union {
		int (*slave_abort)(struct spi_controller *ctlr);
		int (*target_abort)(struct spi_controller *ctlr);
	};
```

后果分三层，逐层不一样：

| 层 | 符号 | 关系 |
|---|-----|------|
| **字段** | `ctlr->slave` / `ctlr->target`；`ctlr->slave_abort` / `ctlr->target_abort` | **同一块存储**（union）。填哪个名字都一样，读哪个名字也都一样 |
| **判定 inline** | `spi_controller_is_slave()`（`spi.h:755`）/ `spi_controller_is_target()`（`spi.h:760`） | **两个独立函数**，函数体逐字相同（都是 `IS_ENABLED(CONFIG_SPI_SLAVE) && ctlr->{slave,target}`），**互不调用** |
| **导出 API** | `spi_slave_abort()`（`spi.c:2777`）/ `spi_target_abort()`（`spi.c:2788`） | **两个独立函数，各自 `EXPORT_SYMBOL_GPL`**（`spi.c:2786` / `:2797`）。**`spi_target_abort` 不是 `spi_slave_abort` 的别名** |

因为底下是 union、上面两条路又逐字等价，所以**混用不会出错**——
`ctlr->target_abort = my_abort;` 配 `spi_slave_abort()` 照样能跑通。
这解释了为什么这类改名能安全地分批做，也解释了为什么全树能同时看到两套写法。

但**不要在文档或注释里把它们写成"别名"**：`#define` 别名（§7.5 的
`spi_master_get_devdata`）改一个另一个自动跟着变；这里是两份独立代码，
将来只删一份的时候行为就会分叉。§1 的骨架统一用 `slave` 一套，
理由和 `master` 一样——本版本里它才是全树多数派（`04-extensions.md` §5）。

### §7.9 delay 两个：`spi_delay_exec` vs `spi_transfer_delay_exec`

| 符号 | 位置 | 参数 | 用在哪 |
|-----|------|-----|------|
| `spi_delay_exec` | 声明 `spi.h:121`，实现 `spi.c:1457` | `(struct spi_delay *, struct spi_transfer *)` | 执行**任意一个** `spi_delay`（核心用它跑 `cs_setup`/`cs_hold`/`cs_inactive`，见 `spi.c:994`/`1027`/`1029`） |
| `spi_transfer_delay_exec` | `spi.h:1154` | `(struct spi_transfer *)` | 便捷封装，等价于 `spi_delay_exec(&t->delay, t)`（`spi.h:1156`）——**只跑这一段的段间延时** |

控制器驱动在 `transfer_one` 结尾要落的是**段间延时**，用
`spi_transfer_delay_exec(xfer)`；写成 `spi_delay_exec(&xfer->delay, xfer)`
效果一样但啰嗦。第二个参数不是可选的：`spi_delay_to_ns()`（`spi.c:1418`）
在单位是 `SPI_DELAY_UNIT_SCK` 时需要从 `xfer->speed_hz` 换算，传 `NULL`
会拿不到时钟频率——这也是 `cs_setup` 那几处传 `NULL` 时**不能**用 SCK
单位的原因。

---

## 相关章节

| 想知道 | 去哪 |
|-------|------|
| 这些 API 的完整签名和返回值 | `01-api.md` |
| 骨架里每个字段的含义 | `02-datastructures.md` |
| 核心在这些调用背后做了什么 | `03-internals.md` |
| `spi-mem` / `spidev` / slave 框架的内部实现 | `04-extensions.md` |
| DT 节点怎么写 | `06-devicetree.md` |
| 跑起来不对怎么查 | `07-debug.md` |
| 本篇符号存在性的取证命令 | `99-evidence.md` §3.6 |

