---
title: 使用指南
---

# clk 子系统使用举例

> ⚠️ **本篇所有代码骨架都没有编译过、没有运行过。** 它们的用途是"把正确的 API 调用次序和参数形状固定下来"，不是可直接编译的驱动。每个符号都已在本树（Linux 6.1.158）全树 grep 确认存在，但**代码整体没过编译器**。抄进项目时请对照 §0.1 表和各节里点名的真实 in-tree 驱动。
>
> 未验证项的完整分层见 `99-evidence.md` §5。

## §0 本篇的组织：先认清你是哪个角色

clk 和 i2c/spi 那种"控制器 + 设备"两角色的子系统不一样，**它有六个角色，而且大多数人只会碰到第一个**。

### §0.1 六个角色 × 你要读哪几节

| # | 角色 | 你在做什么 | 本篇章节 | 谁属于这一类 |
|---|---|---|---|---|
| 1 | **消费者** | 我的驱动要用一路时钟 | §2 | 绝大多数设备驱动 |
| 2 | **提供方（平台驱动）** | 我在写一个时钟控制器驱动 | §3 | SoC 厂商 CCU 驱动 |
| 3 | **提供方（早期声明）** | 我的时钟在 timer/irqchip 之前就要能用 | §4 | 少数根时钟、参考时钟 |
| 4 | **提供方（自制 clk_ops）** | 基本类型不够用，我要自己实现回调 | §5 | arm64 大厂都在这一类（04 §0.2） |
| 5 | **频率变化观察者** | 时钟要变频了我得先做点什么 | §6 | cpufreq、串口、显示 |
| 6 | **频率约束方** | 我不改频率，但我不许它变到某个区间外 | §7 | 需要稳定时钟的外设 |

**角色 1 和角色 2-4 用的是两套完全不同的头文件**，这是 clk 最容易搞错的第一件事：

| | 头文件 | 拿到的类型 | 能调什么 |
|---|---|---|---|
| **消费者** | `include/linux/clk.h` | `struct clk *` | `clk_get` / `clk_prepare_enable` / `clk_set_rate` … |
| **提供方** | `include/linux/clk-provider.h` | `struct clk_hw *` | `clk_hw_register*` / `clk_hw_get_rate` / 填 `clk_ops` |

**提供方 include 了 `clk-provider.h` 就自动有 `clk.h`**（前者顶部 include 了后者），反之不成立。所以"我在提供方代码里调 `clk_get_rate(...)`"是能编过的——但**这是个坑**，见 §8.3。

### §0.2 按任务找章节

| 我想做的事 | 去 |
|---|---|
| 驱动 probe 里把时钟打开 | §2.1 |
| 可选时钟（有就用、没有也行） | §2.2 |
| 一次要好几路时钟 | §2.3 |
| 改频率 / 只读频率 | §2.4 |
| 写一个时钟控制器（DT provider） | §3 |
| 一个 DT 节点提供 N 路时钟 | §3.2 |
| 自己实现 `clk_ops` | §5 |
| 变频前后要做动作 | §6 |
| 不让别人把我的时钟改坏 | §7 |
| 两个名字很像的 API 该用哪个 | **§8（必读）** |
| 出错了怎么查 | `07-debug.md` |

---

## §2 角色 1：消费者

**头文件只需要 `#include <linux/clk.h>`。**

### §2.1 最常见的形态：devm + get + enable 一步到位

```c
#include <linux/clk.h>
#include <linux/platform_device.h>

struct my_priv {
        struct clk *clk;
        void __iomem *base;
};

static int my_probe(struct platform_device *pdev)
{
        struct device *dev = &pdev->dev;
        struct my_priv *priv;

        priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
        if (!priv)
                return -ENOMEM;

        /* 首选写法：get + prepare + enable + 失败回滚 + remove 时自动拆除 */
        priv->clk = devm_clk_get_enabled(dev, NULL);
        if (IS_ERR(priv->clk))
                return dev_err_probe(dev, PTR_ERR(priv->clk), "无法获取时钟\n");

        priv->base = devm_platform_ioremap_resource(pdev, 0);
        if (IS_ERR(priv->base))
                return PTR_ERR(priv->base);

        /* 到这里时钟已经在跑，可以安全读写寄存器 */
        platform_set_drvdata(pdev, priv);
        return 0;
}
```

三个要点：

1. **`devm_clk_get_enabled` 把五件事一次做完**：`clk_get` → `clk_prepare` → `clk_enable` → 任一步失败回滚 → remove/probe 失败时反向拆除。实现见 `drivers/clk/clk-devres.c:75`，走的是共同实现 `__devm_clk_get`（`:22`）。in-tree 例子：`drivers/i2c/busses/i2c-nomadik.c:1008`、`drivers/thermal/sun8i_thermal.c:356`。
2. **`dev_err_probe` 而不是 `dev_err` + return**：它会在 `-EPROBE_DEFER` 时降级为 debug 日志。clk 的 `-EPROBE_DEFER` 在 SCMI/SCPI 平台上是常态（04 §15.3），用 `dev_err` 会刷屏。
3. **第二个参数是 `con_id`，不是设备名**。传 `NULL` 表示"这个设备只有一路时钟，DT 里没写 `clock-names`"。DTS 侧写法见 `06-devicetree.md`。

**想要拿到时钟但先不开**（比如要先算频率再决定要不要开）：用 `devm_clk_get(dev, NULL)`，然后在真正要用的地方调 `clk_prepare_enable(priv->clk)`。**注意此时拆除要自己写** —— `devm_clk_get` 只管 `clk_put`，不管 `disable_unprepare`。这一对不对称是 §8.1 的内容。

### §2.2 可选时钟

```c
        priv->opt_clk = devm_clk_get_optional_enabled(dev, "aux");
        if (IS_ERR(priv->opt_clk))
                return dev_err_probe(dev, PTR_ERR(priv->opt_clk), "aux 时钟异常\n");
        /* DT 里没写 aux 时 opt_clk == NULL，不是错误 */
```

**`_optional` 系列在时钟不存在时返回 `NULL`，而 clk 的每一个消费者 API 都接受 `NULL` 并静默返回成功。** 这既是"可选时钟"能写得这么干净的原因，也是**最难查的一类静默失效**：DTS 里 `clock-names` 拼错一个字母 → `devm_clk_get_optional_enabled` 返回 NULL → 后面所有 `clk_set_rate` / `clk_get_rate` 都成功返回 → 设备就是不工作。

诊断办法：`clk_get_rate(NULL)` 返回 **0**。所以可选时钟拿到手后打一行 `dev_dbg(dev, "aux rate=%lu\n", clk_get_rate(priv->opt_clk));`，看到 0 就知道要么没配上、要么真是 0。详见 `07-debug.md`。

六个 `devm_clk_get*` 变体的完整 2×3 表见 `04-extensions.md` §12.1。

### §2.3 一次要好几路

```c
        /* 形态 A：我知道名字 */
        struct clk_bulk_data clks[] = {
                { .id = "core" },
                { .id = "bus"  },
                { .id = "ref"  },
        };
        int ret = devm_clk_bulk_get(dev, ARRAY_SIZE(clks), clks);
        if (ret)
                return dev_err_probe(dev, ret, "时钟组获取失败\n");
        /* 注意参数次序：路数在前、数组在后（clk.h:1090） */
        ret = clk_bulk_prepare_enable(ARRAY_SIZE(clks), clks);
        if (ret)
                return ret;
        /* clks[0].clk / clks[1].clk / ... 就是各路 struct clk * */

        /* 形态 B：我不知道有几路，全要 */
        struct clk_bulk_data *all;
        int num = devm_clk_bulk_get_all(dev, &all);
        if (num < 0)
                return dev_err_probe(dev, num, "时钟组获取失败\n");
        /* num 是路数，顺序 = DTS 里 clocks 属性的顺序，不带名字语义 */
```

三个陷阱：

| 陷阱 | 后果 |
|---|---|
| `clk_bulk_*` **不是原子的**，只是"带回滚的循环" | 开的过程中别人能看到中间状态 |
| `_get_all` 拿到的必须用 `clk_bulk_put_all` 释放 | 用 `clk_bulk_put` 会漏掉数组本身的内存 |
| `devm_clk_bulk_get_all` 返回**路数**（≥0），不是 0/错误码 | 写成 `if (ret)` 会把"成功拿到 3 路"当成失败 |

完整 API 表见 `04-extensions.md` §11.1。

### §2.4 频率：读、改、约束

```c
        unsigned long rate = clk_get_rate(priv->clk);         /* 读（可能是缓存值） */
        long target = clk_round_rate(priv->clk, 24000000);    /* 问："我要 24M，你能给多少？" */
        int ret = clk_set_rate(priv->clk, 24000000);          /* 改 */
```

**三条必须记住的语义**，每一条都能让人查半天：

1. **`clk_get_rate` 读的是缓存值**（`03-internals.md` §B3）。硬件被别人（另一个 OS、固件、bootloader 之后的某段代码）改了频率，Linux 侧不会知道。除非提供方设了 `CLK_GET_RATE_NOCACHE`。
2. **`clk_set_rate` 成功返回 0，不代表你拿到了你要的频率。** 实际频率是"硬件能凑出来的、按提供方的取整策略选的那一个"。`clk_divider_ops` 默认**向上取整分频比**，也就是**向下取整频率**——要 100 MHz 拿到 96 MHz 是正常的（`04-extensions.md` §3.3）。**要频率就必须回读**：
   ```c
           ret = clk_set_rate(priv->clk, 24000000);
           if (ret)
                   return ret;
           rate = clk_get_rate(priv->clk);         /* 必须回读 */
           if (rate < 23000000 || rate > 25000000)
                   return dev_err_probe(dev, -EINVAL, "只能给到 %lu Hz\n", rate);
   ```
3. **提供方既没有 `.set_rate` 回调、也没有 `CLK_SET_RATE_PARENT` 标志时，`clk_set_rate` 静默返回 0**（`drivers/clk/clk.c:2445-2447`）。**不报错、什么也没做。** 这是"我调了 set_rate，返回 0，频率没变"这个经典现象的唯一解释。同样只能靠回读发现。

**约束而不是设置**（角色 6）见 §7。

### §2.5 消费者陷阱表

| 现象 | 原因 | 对策 |
|---|---|---|
| `clk_set_rate` 返回 0 但频率没变 | 无 `.set_rate` 且无 `CLK_SET_RATE_PARENT`（`drivers/clk/clk.c:2445-2447`） | 回读 `clk_get_rate` |
| 拿到的频率比要的低 | 分频比向上取整 | 回读；或用 `clk_round_rate` 先问 |
| 所有 clk API 都成功但设备不动 | 时钟是 `NULL`（`_optional` + DTS 名字写错） | 打印 `clk_get_rate`，0 就是嫌疑 |
| `clk_set_parent` 返回 `-EPERM` | 提供方没有 `.set_parent` 回调（`drivers/clk/clk.c:2789-2790`） | 换用 `assigned-clock-parents` 或找提供方 |
| `clk_set_parent` 返回 `-EBUSY` | `CLK_SET_PARENT_GATE` 且 **prepare_count > 0**（`drivers/clk/clk.c:2793-2795`） | 先 `clk_disable_unprepare` 再换父 |
| `clk_enable` 返回 `-ESHUTDOWN` | 时钟已被标记为要销毁 | 提供方在 unregister，是生命周期 bug |
| 系统起不来，卡在某个设备之后 | `clk_disable_unused` 关掉了没人声明的时钟 | 命令行加 `clk_ignore_unused` 复现验证（`07-debug.md`） |
| `clk_prepare` 之后 `clk_set_rate` 返回 `-EBUSY` | `CLK_SET_RATE_GATE` 使 prepare 自动加了频率保护（`drivers/clk/clk.c:1072-1080`） | 这路时钟设计上不许边跑边改频 |

---

## §3 角色 2：提供方（平台驱动）

**头文件 `#include <linux/clk-provider.h>`。** 这是 arm64 SoC 时钟控制器驱动的标准形态。

### §3.1 最小骨架（单路）

```c
#include <linux/clk-provider.h>
#include <linux/of.h>
#include <linux/platform_device.h>

static int my_clk_probe(struct platform_device *pdev)
{
        struct device *dev = &pdev->dev;
        struct clk_hw *hw;

        /* 用基本类型注册一路 100 MHz 定频时钟 */
        hw = devm_clk_hw_register_fixed_rate(dev, "my-ref", NULL, 0, 100000000);
        if (IS_ERR(hw))
                return PTR_ERR(hw);

        /* 让 DT 消费者能通过 phandle 找到它 */
        return devm_of_clk_add_hw_provider(dev, of_clk_hw_simple_get, hw);
}

static const struct of_device_id my_clk_of_match[] = {
        { .compatible = "vendor,my-clk" },
        { }
};
MODULE_DEVICE_TABLE(of, my_clk_of_match);

static struct platform_driver my_clk_driver = {
        .probe = my_clk_probe,
        .driver = {
                .name = "my-clk",
                .of_match_table = my_clk_of_match,
        },
};
module_platform_driver(my_clk_driver);
```

**注册和"让别人找得到"是两步。** `clk_hw_register*` 只是把节点挂进 clk 核心的链表；不调 `of_clk_add_hw_provider`（或 clkdev 那套，`04-extensions.md` §10），DT 消费者的 `clk_get` 一定失败。**"我明明注册了，消费者却 `-EPROBE_DEFER`"，九成是漏了这一步。**

### §3.2 一个节点提供 N 路：`clk_hw_onecell_data`

这是 arm64 CCU 驱动的实际形态。真实的最小 in-tree 例子是 `drivers/clk/mvebu/armada-37xx-tbg.c`（155 行）：

```c
struct clk_hw_onecell_data *hw_data;

hw_data = devm_kzalloc(dev, struct_size(hw_data, hws, NUM_CLKS), GFP_KERNEL);
if (!hw_data)
        return -ENOMEM;
hw_data->num = NUM_CLKS;

for (i = 0; i < NUM_CLKS; i++) {
        hw_data->hws[i] = clk_hw_register_fixed_factor(NULL, names[i],
                                                      parent, 0, mult, div);
        if (IS_ERR(hw_data->hws[i]))
                return PTR_ERR(hw_data->hws[i]);
}

return of_clk_add_hw_provider(np, of_clk_hw_onecell_get, hw_data);
```

**`of_clk_hw_onecell_get`（`drivers/clk/clk.c:4844`）做的事就是"拿 DTS 里 `<&ccu 7>` 的那个 7 当下标去查 `hws[]`"**。所以：

| DTS 里的 `#clock-cells` | 用哪个 `get` 回调 | 语义 |
|---|---|---|
| `0` | `of_clk_hw_simple_get` | 这个节点只有一路时钟，`<&node>` 不带参数 |
| `1` | `of_clk_hw_onecell_get` | `<&node N>` 里的 N 是 `hws[]` 下标 |
| 其他 | 自己写回调 | 罕见 |

**`hws[]` 里的下标就是对外 ABI。** 它通常来自 `include/dt-bindings/clock/<soc>.h` 里的宏。**一旦有 DTS 在用，下标就不能改**——改了等于换 ABI，老 DTB 全错位（而且不报错，只是拿到别的时钟）。这也是 CCU 驱动里那些看起来很啰嗦的 `#define XXX_CLK_FOO 37` 存在的原因。

### §3.3 父时钟怎么指定：三选一，只能选一

`struct clk_init_data`（`include/linux/clk-provider.h:297-306`）里有三个互斥的父指定字段，源码注释写得很直白：`/* Only one of the following three should be assigned */`（`:300`）。

| 字段 | 类型 | 什么时候用 | 缺点 |
|---|---|---|---|
| `parent_names` | `const char * const *` | 老写法，按**全局名字**找父亲 | 名字冲突、拼错不报错（变孤儿） |
| `parent_data` | `const struct clk_parent_data *` | **推荐**，可按 DT `fw_name`、全局 `name` 或 DT `index` 找 | 稍啰嗦 |
| `parent_hws` | `const struct clk_hw **` | 父亲和我在同一个驱动里、编译期就知道 | 只能同驱动内 |

`struct clk_parent_data`（`:276-281`）四个字段的优先级："有 `hw` 就直接用它；否则用 `fw_name` 去 DT 的 `clocks` 属性里按名字（`clock-names`）找；否则用 `index` 按下标找；`name` 是最后的全局名兜底"。

```c
/* 推荐形态：父亲来自 DTS，同时留一个全局名兜底 */
static const struct clk_parent_data my_parents[] = {
        { .fw_name = "xtal",  .name = "osc24M" },
        { .fw_name = "pll",   .name = "pll-periph" },
};

static struct my_clk my_mux = {
        .hw.init = &(struct clk_init_data){
                .name        = "my-mux",
                .ops         = &clk_mux_ops,
                .parent_data = my_parents,
                .num_parents = ARRAY_SIZE(my_parents),
                .flags       = CLK_SET_RATE_PARENT,
        },
};
```

**写错父亲名字的后果是"变成孤儿"，不是报错。** 节点注册成功、出现在 debugfs 里，但挂在孤儿链表上、`clk_get_rate` 返回 0。真正的父亲后来注册时会自动把它接回去（`03-internals.md` §B4 的孤儿自愈）——**所以"时钟频率是 0"要先怀疑孤儿，办法是看 `/sys/kernel/debug/clk/clk_orphan_summary`**（`07-debug.md`）。

### §3.4 拆除路径

| 注册用的 | 拆除用的 | 备注 |
|---|---|---|
| `devm_clk_hw_register*` | 不用写 | 首选 |
| `clk_hw_register(dev, hw)` | `clk_hw_unregister(hw)` | `drivers/clk/clk.c:4331` / `:4484` |
| `clk_hw_register_gate` 等类型专用 | `clk_hw_unregister_gate` 等**同类型专用** | 必须配对，见 §8.5 |
| `of_clk_add_hw_provider(np, ...)` | `of_clk_del_provider(np)` | |
| `devm_of_clk_add_hw_provider` | 不用写 | 首选 |
| `clk_register(dev, hw)` | `clk_unregister(clk)` | **已废弃**，见 §8.4 |

**拆除顺序是注册的反序**：先 `of_clk_del_provider`（断掉新的 `clk_get`），再 unregister 各个 `clk_hw`。反了的话消费者可能在节点已销毁后还能查到它。

---

## §4 角色 3：提供方（早期声明 `CLK_OF_DECLARE`）

有些时钟必须在 platform 总线可用之前就注册——典型是给 timer / irqchip 用的参考时钟。这条路径走 `of_clk_init()`（`03-internals.md` §A1），不是 driver core。

```c
static void __init my_early_clk_setup(struct device_node *np)
{
        struct clk_hw *hw;

        hw = clk_hw_register_fixed_rate(NULL, np->name, NULL, 0, 24000000);
        if (IS_ERR(hw))
                return;                          /* 没有返回值可报错，只能静默失败 */
        of_clk_add_hw_provider(np, of_clk_hw_simple_get, hw);
}
CLK_OF_DECLARE(my_early, "vendor,my-early-clk", my_early_clk_setup);
```

**这条路径的三个硬约束**（都源自"driver core 还没起来"）：

1. **回调没有返回值**（`void (*)(struct device_node *)`），失败只能静默返回或 `pr_err`。**不能 `-EPROBE_DEFER`，也就意味着依赖必须已经就绪。**
2. **没有 `struct device`，所以一个 `devm_*` 都不能用。** 上面骨架里 `clk_hw_register_fixed_rate` 的第一个参数是 `NULL`。
3. **`CLK_OF_DECLARE` 会把节点标记成已初始化**（`include/linux/clk-provider.h:1365-1371`，宏体里调 `fwnode_dev_initialized(of_fwnode_handle(np), true)`），platform 层就不会再为这个节点建设备。

**两个变体，差别是"platform 层还要不要再 probe 一次"**：

| 宏 | 定义处 | 语义 |
|---|---|---|
| `CLK_OF_DECLARE` | `include/linux/clk-provider.h:1365` | 早期一次搞定，**platform 层不再管** |
| `CLK_OF_DECLARE_DRIVER` | `:1377` | 早期只做最小的一点（比如注册根时钟），**清掉 `OF_POPULATED` 标志**（`:1380`）让 platform 驱动之后再完整 probe 一次 |

`CLK_OF_DECLARE_DRIVER` 存在的原因就是"我有一部分工作必须早、剩下的需要 `struct device` 和 devm"。**两个宏的 `fn` 都是 `__init`，用完就被丢掉，不能留任何指针指向它。**

**能用平台驱动就别用这条路径。** 判据：只有当你的时钟被 timer / irqchip / 或另一个 `CLK_OF_DECLARE` 消费时才需要早期声明。

---

## §5 角色 4：提供方（自制 `clk_ops`）

**这才是 arm64 大厂的实际形态。** `04-extensions.md` §0.2 用统计钉住了这一点：qcom / sunxi-ng / tegra 的时钟目录里，引用 `clk_gate_ops` / `clk_divider_ops` / `clk_mux_ops` 的文件数都是 **0**。他们自己造了一层。

### §5.1 骨架：`container_of` 三段式

```c
#include <linux/clk-provider.h>

struct my_clk {
        struct clk_hw   hw;             /* 必须是第一层，且必须内嵌不是指针 */
        void __iomem    *reg;
        u32             mask;
        spinlock_t      *lock;
};

#define to_my_clk(_hw) container_of(_hw, struct my_clk, hw)

static int my_clk_enable(struct clk_hw *hw)
{
        struct my_clk *c = to_my_clk(hw);
        unsigned long flags = 0;
        u32 v;

        if (c->lock)
                spin_lock_irqsave(c->lock, flags);
        v = readl(c->reg);
        v |= c->mask;
        writel(v, c->reg);
        if (c->lock)
                spin_unlock_irqrestore(c->lock, flags);
        return 0;
}

static void my_clk_disable(struct clk_hw *hw)
{
        /* 对称实现，略 */
}

static int my_clk_is_enabled(struct clk_hw *hw)
{
        struct my_clk *c = to_my_clk(hw);

        return !!(readl(c->reg) & c->mask);
}

static unsigned long my_clk_recalc_rate(struct clk_hw *hw,
                                        unsigned long parent_rate)
{
        struct my_clk *c = to_my_clk(hw);
        u32 div = (readl(c->reg) >> 8) & 0xf;

        return DIV_ROUND_UP_ULL((u64)parent_rate, div + 1);
}

/* 必须 static const —— 见下面的 §5.3 */
static const struct clk_ops my_clk_ops = {
        .enable      = my_clk_enable,
        .disable     = my_clk_disable,
        .is_enabled  = my_clk_is_enabled,
        .recalc_rate = my_clk_recalc_rate,
};
```

### §5.2 四条上下文约束（写错了是死锁或崩溃，不是返回错误码）

| 回调 | 调用上下文 | 能不能睡眠 | 依据 |
|---|---|---|---|
| `.prepare` / `.unprepare` / `.is_prepared` | 持 `prepare_lock`（mutex） | **可以** | `03-internals.md` §B1 |
| `.enable` / `.disable` / `.is_enabled` | 持 `enable_lock`（spinlock，`irqsave`） | **绝对不行** | 同上 |
| `.recalc_rate` / `.round_rate` / `.determine_rate` / `.set_rate` / `.set_parent` / `.get_parent` | 持 `prepare_lock` | 可以 | 同上 |
| `.debug_init` | 只在 debugfs 建立时 | 可以 | `07-debug.md` |

**"我的时钟寄存器要走 I2C / SPI / mailbox 访问"** → 那些访问会睡眠 → **必须实现成 `.prepare`/`.unprepare` 而不是 `.enable`/`.disable`**。这不是风格问题：`.enable` 是在自旋锁里、关中断的情况下调的。in-tree 的现成范例是 `drivers/clk/clk-gpio.c` 的两张 ops 表（`04-extensions.md` §14.1）和 SCMI 的两张表（§15.1）。

代价：只实现 `prepare` 不实现 `enable` 时，`clk_is_enabled_when_prepared()`（`drivers/clk/clk.c:1359-1362`）对这个节点返回 true——意味着**消费者一 `clk_prepare` 时钟就真的开了**，`clk_enable` 只是记个计数。

### §5.3 `clk_ops` 必须 `static const`，因为核心只拷指针

`clk_hw_register` 把 `init->ops` 拷成一个**指针**存进 `clk_core`。所以：

- **表不能是栈上的局部变量**（函数返回后指针悬空）
- **表不能在注册后被改写**（改了会影响所有共用这张表的节点）
- **想要"每个节点一张表"就必须每个节点一份内存**——全树只有 `clk-composite` 这么干（`struct clk_composite` 里内嵌了非 const 的 `struct clk_ops ops`，`include/linux/clk-provider.h:1216-1227`），而它是运行期拼装的特例
- SCMI 的注释把这条讲得最透（`drivers/clk/clk-scmi.c:106-117`）：因为可能有多个 SCMI 实例、传输质量不同，所以准备了**两张 `static const` 表**，而不是一张表运行期改字段

### §5.4 vendor 层怎么加：以 qcom 的 `clk_regmap` 为例

qcom 在 `clk_hw` 之上又加了一层（`drivers/clk/qcom/clk-regmap.h:20-26`）：

```c
struct clk_regmap {
        struct clk_hw   hw;
        struct regmap   *regmap;
        unsigned int    enable_reg;
        unsigned int    enable_mask;
        bool            enable_is_inverted;
};
```

配套三个通用回调实现（`clk_is_enabled_regmap` / `clk_enable_regmap` / `clk_disable_regmap`，声明在同一头文件末尾）+ 一个注册函数 `devm_clk_register_regmap`。然后所有 qcom 时钟类型（`clk_branch2_ops`、`clk_rcg2_ops`、`clk_alpha_pll_*_ops`）都是 `clk_regmap` 的子类型。看一个具体的：

```c
const struct clk_ops clk_branch2_ops = {              /* drivers/clk/qcom/clk-branch.c:142-146 */
        .enable     = clk_branch2_enable,
        .disable    = clk_branch2_disable,
        .is_enabled = clk_is_enabled_regmap,          /* 复用 clk_regmap 层的通用实现 */
};
```

**这就是"三层 `container_of`"**：`clk_hw` → `clk_regmap` → `clk_branch`。想读懂任何一个 arm64 大厂时钟驱动，第一件事是找出它的中间层长什么样。**别指望能从 `clk_gate_ops` 之类的基本类型入手。**

顺带解释了 `04-extensions.md` §0.2 那个反直觉统计：他们不是不复用，只是复用的对象是**导出的算法函数**（`divider_recalc_rate`、`clk_mux_val_to_index`）而不是完整的类型。

---

## §6 角色 5：频率变化观察者（通知链）

**用途**：时钟要变频了，我得先把我的分频器/波特率/时序参数改好。典型使用者是 I2C 控制器（`drivers/i2c/busses/i2c-rk3x.c:1353`、`i2c-imx.c:1536`、`i2c-cadence.c:1313`）和 timer（`drivers/clocksource/timer-sun5i.c:213`）。

```c
#include <linux/clk.h>
#include <linux/notifier.h>

static int my_clk_notify(struct notifier_block *nb, unsigned long action,
                         void *data)
{
        struct clk_notifier_data *cnd = data;
        struct my_priv *priv = container_of(nb, struct my_priv, clk_nb);

        switch (action) {
        case PRE_RATE_CHANGE:
                /* 即将从 cnd->old_rate 变到 cnd->new_rate */
                if (cnd->new_rate > MY_MAX_INPUT)
                        return NOTIFY_BAD;      /* 否决，clk_set_rate 会返回 -EBUSY */
                my_quiesce(priv);
                break;
        case POST_RATE_CHANGE:
                /* 这里 old_rate == new_rate == 当前频率，不能用来判断变了多少 */
                my_recalc_timing(priv, clk_get_rate(cnd->clk));
                break;
        case ABORT_RATE_CHANGE:
                /* PRE 之后失败了，把 quiesce 撤回去 */
                my_unquiesce(priv);
                break;
        }
        return NOTIFY_OK;
}

/* probe 里 */
        priv->clk_nb.notifier_call = my_clk_notify;
        ret = devm_clk_notifier_register(dev, priv->clk, &priv->clk_nb);
```

**五条必须知道的语义**：

1. **三种 action**（`include/linux/clk.h:39-41`）：`PRE_RATE_CHANGE` / `POST_RATE_CHANGE` / `ABORT_RATE_CHANGE`。**`ABORT` 不能不处理**——PRE 里做的准备动作必须在 ABORT 里撤销，否则一次失败的 `clk_set_rate` 会把你的设备留在半准备状态。
2. **POST 通知里 `old_rate` 和 `new_rate` 是同一个值**。源码注释明说了这是为实现方便做的取舍（`include/linux/clk.h:66-69`：*"For a post-notifier, old_rate and new_rate are both set to the clk's current rate (this was done to optimize the implementation)"*）。**想知道"变了多少"，必须自己在 PRE 里把 `old_rate` 存下来。**
3. **返回 `NOTIFY_BAD` 就是否决**，`clk_set_rate` 会返回 **`-EBUSY`**（`drivers/clk/clk.c:2463-2470`），不是 `-EINVAL`。
4. **回调是在持 `prepare_lock` 的情况下调的**（`03-internals.md` §A6 的三阶段）。所以**回调里不能再调任何会取 `prepare_lock` 的 clk API**——`clk_set_rate`、`clk_prepare`、`clk_get_rate` 都在这个名单上。**这是通知回调最容易犯的死锁**。要读频率就用参数里给的 `cnd->old_rate` / `cnd->new_rate`。
5. **通知只对"这一路时钟自己的频率变化"触发**。父亲变频导致我的频率跟着变时，我的节点也会收到通知（核心会沿树下发），但**换父亲（`clk_set_parent`）不触发频率通知链**。

| API | 什么时候用 |
|---|---|
| `devm_clk_notifier_register(dev, clk, nb)` | 首选，remove 自动注销 |
| `clk_notifier_register(clk, nb)` | 需要手工配 `clk_notifier_unregister` |

---

## §7 角色 6：频率约束方（不设置，只约束）

**用途**："我不管你把它设成多少，但不能低于 20 MHz、不能高于 50 MHz。" 和 `clk_set_rate` 是**两种不同的意图**，别混。

```c
        /* 设一个区间 */
        ret = clk_set_rate_range(priv->clk, 20000000, 50000000);

        /* 只设下界 / 只设上界 */
        ret = clk_set_min_rate(priv->clk, 20000000);
        ret = clk_set_max_rate(priv->clk, 50000000);

        /* 撤销自己设过的区间 */
        ret = clk_drop_range(priv->clk);           /* = clk_set_rate_range(clk, 0, ULONG_MAX) */
```

**四条语义**：

1. **区间是"每个 `struct clk` 句柄各记一份"，核心取所有句柄的交集。** 所以两个驱动各设一段不相交的区间 → 交集为空 → 后设的那个返回错误。
2. **设区间会立刻触发一次调频**：如果当前频率已经在区间外，核心会就地把它拉回区间内。**这意味着 `clk_set_rate_range` 可能改变频率**，尽管名字听起来只是"声明约束"。
3. **`clk_drop_range` 只撤销自己这个句柄的约束**（`include/linux/clk.h:1117-1120` 的 inline，就是 `clk_set_rate_range(clk, 0, ULONG_MAX)`），不影响别人。
4. **`clk_set_rate_range` 在 6.1 里有已知设计债**：源码里有 FIXME 注释说明当前实现在某些场景下的行为不理想（`drivers/clk/clk.c:2622-2629`，见 `03-internals.md` §C2）。而且 `clk_test.c` 12 个 KUnit suite 里有 3 个专测频率范围（`04-extensions.md` §16.1）——**内核自己知道这块容易错**。

### §7.1 更强的形态：独占

```c
        /* 设成这个频率，并且从现在起不许别人改 */
        ret = clk_set_rate_exclusive(priv->clk, 100000000);
        ...
        clk_rate_exclusive_put(priv->clk);         /* 用完必须还 */

        /* 或者：不改频率，只是从现在起锁住当前值 */
        ret = clk_rate_exclusive_get(priv->clk);
        ...
        clk_rate_exclusive_put(priv->clk);
```

**独占期间别人的 `clk_set_rate` 返回 `-EBUSY`。** 计数必须配平（`include/linux/clk.h:212-213` 的 kerneldoc 明说了 get/put 要成对）。in-tree 例子：`drivers/i2c/busses/i2c-bcm2835.c:448`、`drivers/phy/allwinner/phy-sun6i-mipi-dphy.c:135`。

**`clk_set_rate_exclusive` 和 `CLK_SET_RATE_GATE` 是两种不同的"不许改频"**：前者是**消费者主动声明**的运行期保护，后者是**提供方声明**的硬件属性（这路时钟在跑的时候物理上不能改频），后者由 `clk_prepare` 自动加上（`drivers/clk/clk.c:1072-1080`）。**看到 `-EBUSY` 要分清是哪一种**，办法见 `07-debug.md`。

---

## §8 近邻符号区分（本篇最该读的一节）

clk 是**近邻符号最密集**的子系统之一：几乎每个概念都有"消费者版 / 提供方版"、"带锁版 / 不带锁版"、"新版 / 废弃版"三条轴在同时命名。下面每组都写明**错用会怎样**。

### §8.1 `clk_prepare` / `clk_enable` / `clk_prepare_enable`

| 符号 | 上下文 | 干什么 |
|---|---|---|
| `clk_prepare` | **可睡眠**（取 `prepare_lock` mutex） | 让时钟"准备好"（PLL 上电、锁相、等起振） |
| `clk_enable` | **不可睡眠**（取 `enable_lock` spinlock） | 打开门控 |
| `clk_prepare_enable` | 可睡眠（因为包含 prepare） | 两个一起做 |
| `clk_disable_unprepare` | 可睡眠 | 反向两个一起做（`include/linux/clk.h:1083`） |

**错用会怎样**：

- 在中断/原子上下文里调 `clk_prepare` 或 `clk_prepare_enable` → **可能睡眠导致 BUG/死锁**。中断里只能调 `clk_enable`/`clk_disable`，而且必须**在别处已经 `clk_prepare` 过**
- 只 `clk_enable` 不 `clk_prepare` → 提供方的 `.prepare` 没跑过，PLL 可能没上电，**时钟看起来"开着"但没有波形**
- 反过来，**有些时钟只 `clk_prepare` 就已经在跑了**：提供方没实现 `.enable`/`.disable` 时，`clk_is_enabled_when_prepared()`（`drivers/clk/clk.c:1359-1362`）返回 true，最典型的就是 `clk_fixed_rate_ops`（`04-extensions.md` §5.2）

**不确定的时候一律用 `clk_prepare_enable` / `clk_disable_unprepare` 这一对**，除非你确实在原子上下文里。

### §8.2 `clk_get` / `devm_clk_get` / `devm_clk_get_enabled`

| 符号 | 自动 `clk_put`？ | 自动 prepare+enable？ | 自动 disable+unprepare？ |
|---|---|---|---|
| `clk_get` | ❌ | ❌ | ❌ |
| `devm_clk_get` | ✅ | ❌ | **❌** |
| `devm_clk_get_prepared` | ✅ | 只 prepare | ✅ 只 unprepare |
| `devm_clk_get_enabled` | ✅ | ✅ | ✅ |

**`devm_clk_get` 那一行的"两个 ❌ 一个 ✅"是最常见的泄漏源**：它只管把 `clk_put` 挂到 devres 上，**你自己 `clk_prepare_enable` 的那一次它不管**。remove 里忘了 `clk_disable_unprepare` → 引用计数永远不归零 → 这路时钟以后再也关不掉（连 `clk_disable_unused` 也关不掉，因为计数不为 0）。

**错用会怎样**：写成 `devm_clk_get` + 自己 `clk_prepare_enable` + 忘了在 remove 里关 → 功耗回归，而且**没有任何报错**。改用 `devm_clk_get_enabled` 就结构性地消除了这个 bug。

### §8.3 `clk_get_rate` vs `clk_hw_get_rate`——提供方误用会死锁

| 符号 | 头文件 | 内部行为 |
|---|---|---|
| `clk_get_rate(clk)` | `clk.h` | **自己取 `prepare_lock`**（`drivers/clk/clk.c:1923-1925`） |
| `clk_hw_get_rate(hw)` | `clk-provider.h` | **不取锁**，假设调用者已经持有（`drivers/clk/clk.c:574-577`，走 `clk_core_get_rate_nolock`） |

**错用会怎样**：在 `clk_ops` 回调（`.recalc_rate` / `.set_rate` / `.round_rate` / `.determine_rate`）里调 `clk_get_rate()` → 这些回调**已经在 `prepare_lock` 里面**了（`03-internals.md` §B1）→ **递归取 mutex → 死锁**。

因为 `clk-provider.h` 顶部 include 了 `clk.h`，这段代码**能正常编译通过**，问题只在运行时才炸。同一条陷阱在通知回调里也成立（§6 第 4 条）。

同类的"带锁 / 不带锁"配对：

| 消费者版（取锁） | 提供方版（不取锁） |
|---|---|
| `clk_get_rate` | `clk_hw_get_rate` |
| `clk_get_parent` | `clk_hw_get_parent` |
| `clk_round_rate`（`drivers/clk/clk.c:1726` 取锁） | `clk_hw_round_rate` |
| `clk_is_enabled` | `clk_hw_is_enabled` |
| `clk_set_rate_range` | `clk_hw_set_rate_range` |

**记法**：**函数名里带 `_hw`、参数是 `struct clk_hw *` 的，一律是"提供方内部用、不取锁"版。**

### §8.4 `clk_register` vs `clk_hw_register`——一个已废弃

源码 kerneldoc 里原话是 *"clk_register is the **deprecated** interface for populating the clock tree with new clock nodes. Use clk_hw_register() instead."*（`drivers/clk/clk.c` 里 `clk_register` 的注释块）。

| 符号 | 返回 | 状态 |
|---|---|---|
| `clk_register(dev, hw)` | `struct clk *`（要 `IS_ERR` 判断） | **已废弃** |
| `clk_hw_register(dev, hw)` | `int`（0 或负错误码） | 现代接口（`drivers/clk/clk.c:4331`） |
| `devm_clk_hw_register(dev, hw)` | `int` | 首选（`:4540`） |
| `of_clk_hw_register(node, hw)` | `int` | 只有 `device_node` 没有 `struct device` 时用（`:4349`） |

**错用会怎样**：不会立刻坏，但 `clk_register` 返回的 `struct clk *` 是**给消费者用的句柄**——提供方拿着它会诱使你去调消费者 API（→ 回到 §8.3 的死锁）。**这就是它被废弃的实质原因**：类型本身在鼓励你越界。

每个基本类型都有 `clk_register_xxx`（废弃）/ `clk_hw_register_xxx`（现代）/ `devm_clk_hw_register_xxx`（首选）三套，完整清单见 `04-extensions.md` 各节。

### §8.5 通用 unregister vs 类型专用 unregister

```c
clk_hw_unregister(hw);              /* 通用：只从 clk 核心摘掉，不 free 你的结构体 */
clk_hw_unregister_gate(hw);         /* 类型专用：摘掉 + kfree(to_clk_gate(hw)) */
```

**错用会怎样**：

- 用 `clk_hw_register_gate` 注册（**内部 kzalloc 了 `struct clk_gate`**）却用 `clk_hw_unregister` 拆除 → **内存泄漏**，无声无息
- 反过来，自己 kzalloc 的 `struct my_clk` 用 `clk_hw_unregister_gate` 拆 → `to_clk_gate(hw)` 算出错误的基址 → **free 了错的指针**

**判据很简单：注册函数带类型名，unregister 就必须带同一个类型名。** 用 `devm_*` 的话两边都不用写，这也是首选 devm 的第三个理由。

### §8.6 `clk_round_rate` / `clk_hw_round_rate` / `.round_rate` / `.determine_rate`

四个东西名字很像，是四层不同的概念：

| 符号 | 是什么 | 谁调 |
|---|---|---|
| `clk_round_rate(clk, r)` | 消费者 API：**问**"我要 r，你能给多少"，返回频率（不改硬件） | 消费者 |
| `clk_hw_round_rate(hw, r)` | 提供方内部版（不取锁） | 提供方回调内部 |
| `.round_rate` 回调 | 老式协商回调：只能回答频率，**不能改父亲** | 核心调 |
| `.determine_rate` 回调 | 新式协商回调：通过 `struct clk_rate_request` 可以**建议换父亲、报告约束** | 核心调 |

**错用会怎样**：

- 提供方**只实现 `.round_rate`** → 这路时钟**不能在协商中换父亲**，多父 mux 上就永远选不到更合适的父时钟
- 提供方**只实现 `.determine_rate`** → 完全够用（核心优先用它）。`clk_divider_ops` 同时提供两个是**故意的冗余**（`04-extensions.md` §3.5）
- 消费者误以为 `clk_round_rate` 会改频率 → 它不改，只回答

核心里判断"这个节点能不能参与频率协商"的函数是 `clk_core_can_round()`（`drivers/clk/clk.c:1623-1626`），判据是**两个回调至少有一个**。`clk_mux_ro_ops` 两个都没有，所以只读 mux 不参与协商（`04-extensions.md` §4.3）。

### §8.7 四个 `of_clk_*_get` 回调

| 符号 | 返回类型 | `#clock-cells` | 状态 |
|---|---|---|---|
| `of_clk_hw_simple_get` | `struct clk_hw *` | 0 | 现代 |
| `of_clk_hw_onecell_get` | `struct clk_hw *` | 1 | 现代 |
| `of_clk_src_simple_get` | `struct clk *` | 0 | 老式（配 `of_clk_add_provider`） |
| `of_clk_src_onecell_get` | `struct clk *` | 1 | 老式 |

**`_hw_` 的配 `of_clk_add_hw_provider`，不带 `_hw_` 的配 `of_clk_add_provider`。混搭编译不过**（回调签名不同），所以这一组反而是最安全的——编译器会帮你抓。

**错用会怎样**：真正的错误是**回调和 DTS 里的 `#clock-cells` 不匹配**。用 `of_clk_hw_simple_get` 但 DTS 写了 `#clock-cells = <1>` → 消费者 `<&node 3>` 里的 3 被忽略，**永远拿到同一路时钟**，不报错。

### §8.8 `clk_set_rate` / `clk_set_rate_range` / `clk_set_rate_exclusive`

| 符号 | 意图 | 副作用 |
|---|---|---|
| `clk_set_rate(clk, r)` | 我要这个频率 | 改硬件；无 `.set_rate` 且无 `CLK_SET_RATE_PARENT` 时**静默返回 0** |
| `clk_set_rate_range(clk, min, max)` | 我要求它待在这个区间 | 当前值在区间外时**会就地改频** |
| `clk_set_rate_exclusive(clk, r)` | 设成这个频率并锁住 | 别人的 `clk_set_rate` 返回 `-EBUSY`；**必须配 `clk_rate_exclusive_put`** |
| `clk_rate_exclusive_get(clk)` | 只锁住当前值，不改 | 同上，必须配 put |
| `clk_drop_range(clk)` | 撤销我设过的区间 | 只影响自己的句柄 |

**错用会怎样**：`clk_set_rate_exclusive` 之后忘了 `clk_rate_exclusive_put` → **这路时钟从此谁也改不了频率**，而且没有任何日志。查法见 `07-debug.md`（debugfs 里有保护计数）。

### §8.9 `__clk_get_hw` / `clk_hw_get_clk` / `devm_clk_hw_get_clk`

两个方向的转换：

| 符号 | 方向 | 用途 |
|---|---|---|
| `__clk_get_hw(clk)` | `clk` → `clk_hw` | 提供方拿到消费者句柄后，转成自己能用的形式（`drivers/clk/clk.c:357-360`） |
| `clk_hw_get_clk(hw, con_id)` | `clk_hw` → `clk` | 提供方**自己想当消费者**时用（`drivers/clk/clk.c:4078-4084`，会新建一个句柄） |
| `devm_clk_hw_get_clk(dev, hw, con_id)` | 同上 | devm 版 |

**`clk_hw_get_clk` 不是"取出已有的 clk"，它是"新建一个消费者句柄"**——名字里的 `get` 是引用计数意义上的 get，用完要 `clk_put`（devm 版自动）。名字读起来像 accessor，实际是 constructor，**这是本子系统里最容易误读的一个函数名**。

---

## §9 全篇陷阱汇总

按"错了以后表现成什么样"分组，因为这才是你实际遇到问题时的入口。

### §9.1 完全没有报错的（最难查）

| 现象 | 原因 | 章节 |
|---|---|---|
| 一切 API 成功但设备不工作 | 时钟是 `NULL`（`_optional` + DTS 名字写错） | §2.2 |
| `clk_set_rate` 返回 0 但频率没变 | 无 `.set_rate` 且无 `CLK_SET_RATE_PARENT` | §2.4 |
| 拿到的频率不是要的 | 取整策略 | §2.4 |
| 频率一直是 0 | 节点是孤儿（父亲名字写错） | §3.3 |
| 消费者永远 `<&node 0>` | `#clock-cells` 和 get 回调不匹配 | §8.7 |
| 功耗回归 / 时钟关不掉 | `devm_clk_get` + 手工 enable，remove 忘了关 | §8.2 |
| 内存泄漏 | 类型专用注册 + 通用 unregister | §8.5 |
| 别人的 `clk_set_rate` 全返回 `-EBUSY` | 独占计数没还 | §8.8 |

### §9.2 会崩会挂的

| 现象 | 原因 | 章节 |
|---|---|---|
| 死锁（软锁死/hung task） | 在 `clk_ops` 回调或通知回调里调消费者 API | §8.3、§6 |
| 原子上下文 BUG | 中断里调 `clk_prepare*` | §8.1 |
| 睡眠告警 | `.enable` 回调里访问会睡眠的总线 | §5.2 |
| free 了错的指针 | unregister 类型不匹配 | §8.5 |
| 启动卡死 | `clk_disable_unused` 关掉了在用但没声明的时钟 | §2.5、`07-debug.md` |

### §9.3 有错误码的（好查）

`-EPERM` / `-EBUSY` / `-ESHUTDOWN` / `-EPROBE_DEFER` 的完整语义和出处见 `01-api.md` 的错误码节 + §2.5 的陷阱表。

---

## §10 读完本篇之后

| 下一步想知道的 | 去哪篇 |
|---|---|
| 这些 API 的完整签名、参数、错误码 | `01-api.md` |
| `clk_ops` 每个回调的完整约束 | `02-datastructures.md` §4 |
| 核心内部到底怎么走的 | `03-internals.md` |
| 基本类型和子框架的细节 | `04-extensions.md` |
| DTS 怎么写、属性谁消费 | `06-devicetree.md` |
| 出问题了怎么查（debugfs / tracepoint / 内核参数） | `07-debug.md` |
| 为什么要分 prepare 和 enable、孤儿是什么 | `08-feynman.md` |
| 本篇每个符号的存在性核查记录 | `99-evidence.md` §7 |
