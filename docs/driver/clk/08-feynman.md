---
title: 费曼学习法
---

# 08 费曼解释：clk 里最难想通的九个问题

> 本篇不讲 API、不讲流程，只讲**"为什么是这样设计的"**。
> 每个问题四段式：先举个例子 → 回到代码 → 这个类比什么时候会误导你。
> 只讲 **arm64**。类比后面一律带源码行号——**类比不是免于取证的理由**。
>
> 选题原则：优先挑「读了 01–07 还是想不通」和「最容易被类比错」的那些。
> clk 里最容易被类比错的一组关系是 **`struct clk` / `clk_core` / `clk_hw` 三者**
> （§1、§2），以及 **clk 为什么不是一条总线**（§5）——这三节建议先读。

| # | 问题 | 难点类型 |
|---|---|---|
| §1 | `struct clk` 和 `struct clk_core` 为什么要分成两层？ | 最容易类比错 |
| §2 | 提供方为什么拿到的是 `clk_hw`，消费者拿到的是 `clk`？ | 最容易类比错 |
| §3 | `clk_prepare` 和 `clk_enable` 为什么非要分成两步？ | 全框架最核心的设计 |
| §4 | 为什么要两把锁，一把 mutex 一把 spinlock？ | 和 §3 是同一件事的另一面 |
| §5 | clk 为什么没有自己的 `bus_type`？ | 最容易类比错 |
| §6 | "孤儿时钟"是怎么产生的，内核为什么允许它存在？ | |
| §7 | `clk_set_rate` 为什么会返回 0 却什么都没做？ | 静默失败之王 |
| §8 | `clk_disable_unused` 为什么要存在？这不是给自己找麻烦吗？ | |
| §9 | 频率保护（`protect_count`）到底在保护什么？ | |

---

## §1 `struct clk` 和 `struct clk_core` 为什么要分成两层？

### 先举个例子

想想公司里那台**共用的打印机**。

打印机只有一台，它有唯一的型号、唯一的 IP、唯一的墨盒余量——这些属性**属于打印机本身**。

但每个人在自己电脑上都装了一个"打印机"图标。你的图标上设了"默认双面、A4"，
你同事的图标上设了"单面、A3"。这些设置**属于你和打印机之间的这条关系**，
不属于打印机。你改自己的默认纸张，不会改到同事的。

那么"打印机"这个词其实指两样东西：**那台机器**，和**你桌面上那个图标**。
如果只用一个数据结构表示，就会出现"我改了纸张，全公司都变了"的荒唐事。

clk 就是这么分的：

| 概念 | clk 里叫什么 | 一个硬件时钟对应几个 |
|---|---|---|
| 那台机器 | `struct clk_core` | **1 个** |
| 你桌面上的图标 | `struct clk` | **每个消费者一个** |

### 回到代码

`struct clk` 只有 8 个字段（`drivers/clk/clk.c:102-110`）：

```c
struct clk {
	struct clk_core	*core;
	struct device *dev;
	const char *dev_id;
	const char *con_id;
	unsigned long min_rate;
	unsigned long max_rate;
	unsigned int exclusive_count;
	struct hlist_node clks_node;
};
```

一眼就能看出它的性质：**除了第一个指针，其余全是"这一次 get 的私有信息"**——
是谁 get 的（`dev`/`dev_id`/`con_id`）、这个消费者要求的频率范围
（`min_rate`/`max_rate`）、这个消费者持有几份频率保护（`exclusive_count`）。

`struct clk_core`（`drivers/clk/clk.c:60`）才是那台机器：ops 表、父子链表、
真实频率、`enable_count`、`prepare_count`、flags 全在它里面。

最后一个字段 `clks_node` 是**回指**：所有 get 过同一个 `clk_core` 的
`struct clk` 挂在 `core->clks` 这条链表上。所以：

> `clk_summary` 最后两列（consumer / connection id）之所以能列出
> **多行**、每行一个消费者，就是遍历这条链表来的
> （`07-debug.md` §3.2 里那段 `hlist_for_each_entry(clk_user, &c->clks, clks_node)`）。

### 这个类比什么时候会误导你？

打印机类比在**一个关键点上是错的**：桌面图标坏了不影响打印机，
但 `struct clk` 上的 `min_rate`/`max_rate` **会真的影响硬件**。

`clk_set_rate_range` 写进的是**你这个 handle** 的范围，但核心会把
**所有** handle 的范围求交集，然后据此决定实际频率
（`04-extensions.md` 讲的范围合并）。所以更准确的说法是：

> `struct clk` 不是"只读的视图"，而是**一张投票权**。
> 你在自己的 handle 上设范围，等于往共享决策里投了一票。
> `clk_min_rate` / `clk_max_rate` 这两个 debugfs 文件读到的是**投票结果**，
> 不是任何单个消费者的设置（`07-debug.md` §3.6）。

第二个误导点：图标可以随便删，但 `clk_put` 之后你的那份 `enable_count`
**不会自动还回去**。handle 是引用计数的账本页，不是快捷方式。

---

## §2 提供方为什么拿到的是 `clk_hw`，消费者拿到的是 `clk`？

### 先举个例子

接着 §1 的打印机。除了"机器"和"图标"，其实还有第三个身份：
**打印机的维修口**。维修工打开侧盖，看到的是滚筒、齿轮、传感器——
他要的是能动手的那一面，而不是"双面打印"这种设置。

维修口和图标的区别不是权限大小，而是**朝向不同**：
图标朝用户，维修口朝硬件。

`clk_hw` 就是维修口。

### 回到代码

`struct clk_hw` 只有三个字段（`include/linux/clk-provider.h:324-328`）：

```c
struct clk_hw {
	struct clk_core *core;
	struct clk *clk;
	const struct clk_init_data *init;
};
```

它和 `struct clk` 一样都只是"通往 `clk_core` 的一个把手"，
区别在于**给谁用**：

| | `struct clk` | `struct clk_hw` |
|---|---|---|
| 谁持有 | 消费者驱动（`clk_get` 返回） | 提供方驱动（`clk_hw_register` 之后） |
| 典型用法 | `clk_prepare_enable`、`clk_set_rate` | **`container_of` 拿到自己的私有结构** |
| 能不能拿到对面 | 不能直接 | 能：`hw->clk` |

第二行是全部要点。提供方驱动的私有结构长这样：

```c
struct my_pll {
	struct clk_hw	hw;      /* 必须内嵌，不是指针 */
	void __iomem	*base;
	/* ... 私有字段 ... */
};
```

于是 `.set_rate` 回调只收到 `struct clk_hw *hw`，靠
`container_of(hw, struct my_pll, hw)` 就能拿回 `base` 去写寄存器
（`04-extensions.md` 和 `05-usage.md` §3 有完整骨架）。

> 一句话记住三者：**`clk_core` 是本体，`clk` 是消费者的把手，
> `clk_hw` 是提供方的把手**。三者一一对应到同一个硬件时钟。

### 这个类比什么时候会误导你？

维修口类比会让你以为"提供方权限更大、能绕过核心"。**不是。**

`clk_hw` 上能做的事一样要经过核心。举个具体的：提供方想知道自己当前的频率，
调 `clk_hw_get_rate(hw)`，核心照样读 `core->rate` 缓存——
**它并不比消费者更接近硬件**。真正"接近硬件"的是提供方自己的 `base` 指针，
那是它自己的私有字段，和 `clk_hw` 无关。

第二个误导点，也是实践中真会踩的：`hw->clk` 这个字段的存在会让人写出
"提供方直接拿 `hw->clk` 去 `clk_set_rate`"这种代码。别这么干——
那会在 `prepare_lock` 已经持有的情况下再进一次核心，直接死锁或触发
`WARN_ON_ONCE(prepare_owner != current)` 那一族断言
（`drivers/clk/clk.c:223`/`:231`，见 `07-debug.md` §5.2）。

---

## §3 `clk_prepare` 和 `clk_enable` 为什么非要分成两步？

### 先举个例子

想想开车去接人。

- **准备**：解锁、上车、点火、等发动机怠速稳定。这一步**慢**，可能十几秒，
  而且中间你可能要下车擦一下挡风玻璃——**允许被打断、允许等待**。
- **起步**：松手刹、踩油门。这一步**快**，而且必须一气呵成。

如果只有一个"开车"动作，那么每次红灯起步你都得重新点火等怠速——荒唐。
所以现实里也是分两步：**点火一次，起步无数次**。

时钟也是这样。一个 PLL 从关到出稳定频率要几十微秒到毫秒级
（要锁相、要等 lock 位），这个过程**必须能睡眠**。
但一个简单的门控位（gate）翻转只要几个时钟周期，
而它**经常在中断上下文里被翻**——比如中断处理里要开某个外设时钟。

于是：

| 动作 | 对应现实 | 能睡眠吗 | 典型硬件操作 |
|---|---|---|---|
| `clk_prepare` | 点火等怠速 | **能** | PLL 上电、等 lock、走 I2C/SPI 去访问 PMIC 里的时钟 |
| `clk_enable` | 松手刹起步 | **不能** | 翻一个门控位 |

### 回到代码

两个动作用**两把不同的锁**（`drivers/clk/clk.c:27-28`）：

```c
static DEFINE_SPINLOCK(enable_lock);
static DEFINE_MUTEX(prepare_lock);
```

`prepare_lock` 是 mutex（能睡），`enable_lock` 是 spinlock（不能睡）。
这一行代码就是整个 prepare/enable 分离设计的**根**——§4 接着讲。

顺序是强制的：先 prepare 再 enable。反过来会撞上
`WARN(core->prepare_count == 0, ...)`，而且 `clk_core_enable`
**直接返回 `-ESHUTDOWN`**（`drivers/clk/clk.c:1188-1190`）。
这是 `clk.c` 里 `-ESHUTDOWN` 的唯一出处（`07-debug.md` §6.1）。

有意思的是**反向也被检查**：unprepare 时如果 `enable_count > 0`，
会打 `WARN(core->enable_count > 0, "Unpreparing enabled %s\n", ...)`
（`drivers/clk/clk.c:1002`）。所以正确写法永远是
`clk_prepare_enable` / `clk_disable_unprepare` 这两个配对宏，
**不要手写四个调用**。

### 这个类比什么时候会误导你？

开车类比暗示"prepare 一定慢、enable 一定快"。**很多时钟两者都是空的。**

一个纯粹的分频器既不需要 prepare 也不需要 enable。核心对这种情况有个
专门的判据函数 `clk_is_enabled_when_prepared`，它的条件是
**"没有 `.enable` 也没有 `.disable` 回调"**（`drivers/clk/clk.c:1359-1362`）。

这个判据的下游后果直接影响你看 debugfs：`clk_summary` 的
`hardware enable` 列，对这种时钟会打一个**编出来的 `Y`**
（`drivers/clk/clk.c:3228-3229`，`07-debug.md` §3.2 那张三值表）。
也就是说"prepare/enable 都是空操作"这件事，会在你完全没预期的地方冒出来。

第二个误导点：类比让人以为"prepare 慢所以放在 probe 里做一次就行"。
但 `CLK_SET_RATE_GATE` 时钟在 `clk_prepare` 里会**顺手加一份频率保护**
（`drivers/clk/clk.c:1072-1080`）。所以 prepare 不只是"点火"，
它还可能悄悄改变了别人能不能调这个时钟的频率——见 §9。

---

## §4 为什么要两把锁，一把 mutex 一把 spinlock？

### 先举个例子

想想一栋楼的两种"占用"：

- **会议室要预约**：你在系统里订下 14:00–15:00。别人想订就得等，
  等的时候可以去干别的事、可以喝咖啡。这是 **mutex**。
- **电梯按钮只有一个**：两个人同时按，硬件层面必须有个先后。
  你不能"等一会儿再按"——你就站在那儿，手指按下去的那一瞬间必须独占。
  这是 **spinlock**。

会议室预约系统不能用来管电梯按钮：等待时"去干别的事"这个动作本身
在按按钮的那一瞬间是做不到的。反过来，用电梯按钮的方式管会议室，
意味着所有人站在门口干等一小时——CPU 就白烧了。

### 回到代码

两把锁各自有一套 owner 追踪（`drivers/clk/clk.c:30-31`）：

```c
static struct task_struct *prepare_owner;
static struct task_struct *enable_owner;
```

为什么要记 owner？因为 clk 的操作**天然是递归的**：
改一个时钟的频率要先改父的，改父的要先改祖父的。所以
`clk_prepare_lock()`（`drivers/clk/clk.c:214`）第一件事就是判断
"这把锁是不是我自己已经持有的"（`:217` 的 `if (prepare_owner == current)`），
是的话只加引用计数、不再真的去 lock。

这就是所谓**可重入锁**。代价是要自己维护 owner 和 refcnt，
所以才有那 8 条 `WARN_ON_ONCE` 断言：
prepare 侧 `drivers/clk/clk.c:223`/`:224`/`:231`/`:232`，
enable 侧 `:261`/`:262`/`:271`/`:272`（`07-debug.md` §5.2）。

> 这 8 条断言是 `_ONCE` 的——**同一个 bug 只报一次**。
> 所以在 dmesg 里看到一条就要当真，不能因为"只出现一次"而忽略。

### 这个类比什么时候会误导你？

会议室/电梯的类比暗示这两把锁**管的是不同的资源**。
实际上它们管的是**同一棵时钟树**，只是**不同的操作**：

| 锁 | 保护的操作 | 为什么必须是这种锁 |
|---|---|---|
| `prepare_lock`（mutex） | prepare/unprepare、set_rate、set_parent、set_phase、注册/注销 | 这些操作里的回调**可能睡眠**（如走 I2C 访问 PMIC） |
| `enable_lock`（spinlock） | enable/disable | 这两个可能在**中断上下文/idle 路径**被调用 |

第二行那个"idle 路径"不是理论——它在 tracepoint 上留下了痕迹：
enable/disable 的打点必须用 `_rcuidle` 变体
（`drivers/clk/clk.c:1139`/`:1144`/`:1198`/`:1203`），
因为 CPU 已经进 idle、RCU 不再观测它了（`07-debug.md` §4.3）。
**一个"锁的选择"最终以"tracepoint 要用特殊变体"的形式露出来**，
这是内核里典型的设计约束传导。

第三个误导点：类比让人以为"拿了 mutex 就不能拿 spinlock"。
恰恰相反——`clk_set_rate` 全程持 `prepare_lock`，
中间提交频率变更时还会去拿 `enable_lock`。**嵌套是允许的，
方向是固定的：先 prepare_lock 再 enable_lock，不能反。**

---

## §5 clk 为什么没有自己的 `bus_type`？

### 先举个例子

想想一栋写字楼里的两种"服务"：

- **快递柜**：它是个**看得见的实体**。每个柜子有编号，你可以站在它前面，
  它会坏、会被搬走、会有维修记录。管理处有一张"本楼快递柜清单"。
- **电力**：它**没有实体清单**。你不会说"三层的第 5 个电"。
  你只知道墙上有插座，插上就有电。管理处不维护"电的清单"，
  它维护的是**变电室和线路**——而线路是**树状**的，一路分下去。

i2c / spi / usb 是快递柜：设备是实体，每个都有身份，值得建一张清单
（一条总线 + 一堆 device + 匹配 driver）。

clk 是电力：**时钟不是设备，它是一种被引出去的能力**，
而且天然是树状的（PLL → 分频 → 门控 → 外设）。
给"电"建一条总线没有意义，你需要的是**线路图**。

### 回到代码

一手证据（重跑：`/usr/bin/grep -rn 'struct bus_type' drivers/clk/` → **无输出**）：

> **`drivers/clk/` 下没有任何 `struct bus_type`。**

对照 i2c：`struct bus_type i2c_bus_type` 明确定义在
`drivers/i2c/i2c-core-base.c:699`。这就是"硬件总线型"和"框架型"的分界
（`99-evidence.md` §1 登记的子系统类型判定）。

那么 clk 用什么代替总线？**两条链表 + 树**（`drivers/clk/clk.c:36-37`）：

```c
static HLIST_HEAD(clk_root_list);
static HLIST_HEAD(clk_orphan_list);
```

`clk_root_list` 是所有**根时钟**（没有父的），从它们顺着
`core->children` 往下走就是整棵树。`clk_orphan_list` 是**还没找到父**的
（见 §6）。**没有"所有时钟的平表"**——因为树本身就是索引。

那么时钟控制器这个**硬件**呢？它照样是个 platform device，
提供方驱动照样是 `platform_driver`。所以完整的图是：

```
platform 总线
   └── 时钟控制器 platform_device（有 DT 节点、有 reg、有 driver）
          └─ probe 里调 clk_hw_register / of_clk_add_hw_provider
                 └─ 往 clk 核心的【树】里插入 N 个 clk_core
                        （这些 clk_core 【不是】device，不在任何总线上）
```

一句话记住：**控制器在 platform 总线上，时钟本身不在任何总线上。**

### 这个类比什么时候会误导你？

电力类比暗示"时钟是纯被动的、不需要注册"。**恰恰相反，注册次序是 clk 最大的坑。**

插座后面的电线是施工时就接好的；而 clk 的父子关系是**运行期按名字/phandle
一个个解析出来的**，父还没注册时子只能先当孤儿（§6）。
这就是 `-EPROBE_DEFER` 在 clk 世界里如此常见的根本原因
（`drivers/clk/clk.c:5126` 那个"初值即 defer"的写法，`07-debug.md` §6.5）。

第二个误导点：既然没有总线，也就**没有 `bind`/`unbind`、没有 sysfs 设备节点、
没有 `driver_override`**。所有你在 i2c/spi 上习惯的调试手段在 clk 上都不存在——
clk 的可观测面只有 debugfs + tracepoint + dmesg 三样
（`07-debug.md` §8 已用 grep 证过没有字符设备、没有 sysfs 类）。

---

## §6 "孤儿时钟"是怎么产生的，内核为什么允许它存在？

### 先举个例子

想想公司入职。新人 A 周一到岗，系统里要填"直属上级"。
但他的上级 B 是外部招聘、周三才入职。

两种处理方式：

1. **严格**：A 的入职流程卡住，等 B 来了再办。
   → 如果 B 的上级 C 也还没来，整条链都卡住；万一 C 永远不来，全公司招不进人。
2. **宽松**：先给 A 建档，"直属上级"栏暂时挂在"待定"部门下。
   B 入职时，系统自动把 A 挪到 B 名下。

内核选了第二种。"待定部门"就是 `clk_orphan_list`。

### 回到代码

时钟注册时，`__clk_core_init` 分三种情况挂链
（`drivers/clk/clk.c:3871-3880`）：

```c
		hlist_add_head(&core->child_node, &parent->children);
		core->orphan = parent->orphan;
	} else if (!core->num_parents) {
		hlist_add_head(&core->child_node, &clk_root_list);
		core->orphan = false;
	} else {
		hlist_add_head(&core->child_node, &clk_orphan_list);
		core->orphan = true;
	}
```

三个分支逐字读就是三种身份：**父已就位**（挂到父的 children 下，
注意 `core->orphan = parent->orphan`——**父是孤儿则我也是孤儿**，
孤儿性质会沿树传染）、**声明了没有父**（根时钟）、
**声明了有父但没找到**（孤儿）。

"B 入职自动挪 A"发生在 `__clk_core_init` 的末尾：
`clk_core_reparent_orphans_nolock()`（`drivers/clk/clk.c:3952`，
函数本体在 `:3730`）。**每注册一个新时钟，就把整个孤儿链表扫一遍**，
看有没有谁的父正好是我。

### 这个类比什么时候会误导你？

入职类比暗示"孤儿只是暂时的、迟早会被认领"。**很多孤儿是永久的，而且不报错。**

关键差别：如果 DTS 或提供方驱动里的父名**拼错了**，
那个父永远不会出现，这个时钟就永久是孤儿——而**内核一条日志都不打**
（`06-devicetree.md` §7.2）。你只能靠
`cat /sys/kernel/debug/clk/clk_orphan_summary` 自己去看。

第二个误导点，也是最反直觉的一条：**孤儿并不豁免
`clk_disable_unused`**。看 `clk_disable_unused` 的循环
（`drivers/clk/clk.c:1481-1491`）：

```c
	hlist_for_each_entry(core, &clk_root_list, child_node)
		clk_disable_unused_subtree(core);

	hlist_for_each_entry(core, &clk_orphan_list, child_node)
		clk_disable_unused_subtree(core);
```

**根链表和孤儿链表都被扫了**（disable 一遍、unprepare 再一遍，共四个循环）。
所以"父名写错 → 时钟变孤儿 → 启动末期被关掉 → 挂死"是一条完整的、
全程无日志的故障链。这也解释了为什么 §8 那个参数是排查启动挂死的第一手段。

第三个误导点：`core->orphan` 的传染性（上面 `:3872`）意味着
**一个拼错的父名会让它下面整棵子树全部变成孤儿**。
所以在 `clk_orphan_summary` 里看到几十个孤儿时，
**别一个个查——找那个最上面的、缩进最浅的**，通常只有一个真错。

---

## §7 `clk_set_rate` 为什么会返回 0 却什么都没做？

### 先举个例子

想想你对出租车司机说"开快点"。

- 如果司机能加速，他加速，你满意。
- 如果这车已经到极速了，司机会说"到顶了"。
- 但还有第三种情况：**你坐的是地铁**。你对着车厢喊"开快点"，
  没有人报错、没有人拒绝你，**列车按原速继续开**。
  因为"车厢"这个东西根本没有油门。

`clk_set_rate` 的静默返回 0 就是第三种。

### 回到代码

`clk_core_set_rate_nolock` 开头四行（`drivers/clk/clk.c:2443-2447`）：

```c
	rate = clk_core_req_round_rate_nolock(core, req_rate);

	/* bail early if nothing to do */
	if (rate == clk_core_get_rate_nolock(core))
		return 0;
```

关键在第一行的**协商**：`clk_core_req_round_rate_nolock` 问的是
"你能做到多少？"。而一个**既没有 `.set_rate`、又没有 `CLK_SET_RATE_PARENT`**
的时钟，能做到的只有**当前这个频率**。于是：

1. 协商结果 = 当前频率
2. `rate == clk_core_get_rate_nolock(core)` 成立
3. 走进 `/* bail early if nothing to do */`，**`return 0`**

从核心的角度看这完全合理：**"没有变化"确实不是错误**。
但从驱动作者的角度看，"我要求 200MHz，返回 0，实际还是 24MHz"
是纯粹的陷阱。

诊断方法只有一条（`07-debug.md` §3.4）：

```bash
cat /sys/kernel/debug/clk/<名>/clk_flags   # 找 CLK_SET_RATE_PARENT
cat /sys/kernel/debug/clk/<名>/clk_rate    # 看实际值
```

而正确的**编码**习惯是：`clk_set_rate` 之后**永远回读**
`clk_get_rate`，不要只判返回值（`05-usage.md` §2）。

### 这个类比什么时候会误导你？

地铁类比暗示"这是个 bug、应该报错"。但把它改成报错**会破坏一大批正常代码**：

很多驱动是这样写的——`clk_set_rate(clk, 我想要的)`，
拿不到就用当前频率算分频。对**固定频率时钟**来说，
返回 0 让这套代码"什么都不做地成功"，正是设计意图。

第二个误导点，也是这一节真正想让你记住的：**这不是唯一的静默返回 0**。
同一族至少还有三处（`01-api.md` 已定稿的错误码事实）：

| API | 静默返回 0 的条件 | 出处 |
|---|---|---|
| `clk_set_rate` | 无 `.set_rate` 且无 `CLK_SET_RATE_PARENT` | `drivers/clk/clk.c:2445-2447` |
| `clk_set_phase` | 角度先被 `%= 360` **归一化**，越界不报错 | `:2939-2942` |
| `clk_set_duty_cycle` | 无回调、且父不满足透传条件 | `:3061-3062`、`:3078-3081` |

> 归纳成一句可迁移的经验：**clk 的 setter 家族普遍把
> "我做不到"翻译成"没有变化"，而"没有变化"被当成成功。**
> 所以在 clk 上，**返回值 0 的信息量远低于你的直觉**——
> 只有回读才算验证。

第三个误导点，也是本节最硬的一条：地铁类比让你以为"至少车厢确实在动"。
但 clk 连**"提交阶段失败"都没有回传通道**：真正去调 `.set_rate` 的那个函数
`clk_change_rate` 的签名是 `static void`（`drivers/clk/clk.c:2318`），
`.set_rate` 的返回值在 `:2367-2368` **根本没有被接住**
（那一行是裸调用，不是 `ret = ...`），上层 `clk_core_set_rate_nolock`
在 `:2473` 也不检查。所以完整的图是：

```
clk_set_rate(clk, 200MHz)
   ├─ 协商阶段：算得出来吗？ ──否──> 返回错误码，或"没变化"→ return 0   ← 这一半有返回值
   └─ 提交阶段：clk_change_rate()  【void，无返回值】
          └─ core->ops->set_rate(...)  ← 返回值被丢弃
                 提供方写寄存器失败 → 【没有任何人知道】
```

也就是说 **`clk_set_rate()` 返回 0 只证明"核心算出了一条可行路径"，
不证明硬件真的被改了。** 这不是 bug 报告——这是你必须回读 `clk_get_rate`
的第二个、也是更硬的理由（`07-debug.md` §4.2.1 把 tracepoint 侧的
同一个洞也补上了：连打点都看不出提交失败）。

---

## §8 `clk_disable_unused` 为什么要存在？这不是给自己找麻烦吗？

### 先举个例子

想想你接手了一间**别人装修好的办公室**。

进门时所有灯都亮着、所有空调都开着。你不知道哪些是必须的
（服务器机柜要散热）、哪些是上一任忘了关的。

两种做法：

1. **全都不动**：安全，但电费白烧。手机上就是**待机耗电翻倍**。
2. **只留下"有人明确说要开"的**：先让每个部门来登记"我需要哪些灯"，
   登记截止后，**没登记的全关**。

内核选了第二种。"登记"就是驱动调 `clk_prepare_enable`，
"截止时刻"就是 `late_initcall_sync`。

### 回到代码

`clk_disable_unused`（`drivers/clk/clk.c:1460`）注册为
`late_initcall_sync(clk_disable_unused)`（`:1499`）——
**内核初始化的最后一个级别**。

它先打一条 `pr_info("clk: Disabling unused clocks\n")`（`:1470`），
然后在 `prepare_lock` 下（`:1479`，源码注释说明是"保持树的形状稳定"）
跑**四个循环**（`:1481-1491`）：根链表 disable、孤儿链表 disable、
根链表 unprepare、孤儿链表 unprepare。

**为什么这个动作对 arm64 特别重要**：bootloader 为了启动会把一大堆时钟打开，
而 Linux 只有一部分驱动会去 `clk_prepare_enable`。省电就靠这一刀。

代价是：**任何一个驱动漏了 `clk_prepare_enable`，都在这一刻暴雷**。
而且暴雷方式极其难查——挂死在 `late_initcall_sync`，
此前所有日志都正常，串口可能同时死掉。
所以才有 `clk_ignore_unused` 这个参数（`07-debug.md` §2.1）。

### 这个类比什么时候会误导你？

"关灯"类比暗示 `clk_ignore_unused` 是个**修复手段**。**它不是。**

`Documentation/admin-guide/kernel-parameters.txt:594` 起的说明写得很清楚：
这个参数**不会把时钟变成 always-on，也不会给它加引用计数**，
它只是让这一次批量关闭不发生。所以：

| 你的目的 | 该用什么 |
|---|---|
| 确认"是不是被 disable_unused 关掉的" | `clk_ignore_unused`（**诊断**） |
| 真的要这个时钟一直开着 | 给它加 `CLK_IS_CRITICAL` 或 `CLK_IGNORE_UNUSED`（**提供方驱动改代码**） |
| 让某个设备用上这个时钟 | 给漏掉的驱动补 `clk_prepare_enable`（**正解**） |

第二个误导点：类比暗示"登记就安全了"。但 `CLK_IS_CRITICAL` 时钟
在注册时就被 prepare+enable，**如果这一步失败，只打 `pr_warn`、
注册照样成功**（`drivers/clk/clk.c:3938`/`:3945`，
`06-devicetree.md` §9 已逐字列出）。
也就是说"我标了 critical"并不保证它真的开着——
还是得回 `clk_summary` 看第 2 列和第 9 列（`07-debug.md` §3.2）。

---

## §9 频率保护（`protect_count`）到底在保护什么？

### 先举个例子

想想会议室的**投影仪分辨率**。

平时谁都可以调。但如果有人正在做**现场直播**，直播软件锁定了 1080p——
这时别人过来改分辨率，不是"改了但直播画面变糊"，而是**直接改不了**，
系统提示"正在直播，设置已锁定"。

关键点：**锁的不是"投影仪"这个设备，而是"分辨率"这一个参数**。
音量、亮度照样能调。

`clk_rate_exclusive_get` 就是这个"直播锁定"。

### 回到代码

`clk_rate_exclusive_get`（`drivers/clk/clk.c:967`）调
`clk_core_rate_protect`（`:922`），后者的核心逻辑是**向上递归**：

```c
	if (core->protect_count == 0)
		clk_core_rate_protect(core->parent);

	core->protect_count++;
```

`:930` 那一行递归是本节的要点：**锁住一个时钟的频率，
必须连它的整条父链一起锁住**——否则别人改父的频率，
你这个时钟的频率照样会变。这和投影仪类比对不上的地方（见下）。

保护生效后，`clk_set_rate` 走到
`if (clk_core_rate_is_protected(core)) return -EBUSY;`
（`drivers/clk/clk.c:2450-2451`）。这就是 `07-debug.md` §6.4
那张表里 4/6 处 `-EBUSY` 的来源。

解锁是 `clk_rate_exclusive_put`（`drivers/clk/clk.c:901`），
计数记在**你自己的 handle** 上（`struct clk` 的 `exclusive_count` 字段，
`drivers/clk/clk.c:108`）——回到 §1 说的"handle 是账本页"。

还有一处**你没主动要求、但自动发生**的保护：
`CLK_SET_RATE_GATE` 时钟的 `clk_prepare` 会顺手调
`clk_core_rate_protect`（`drivers/clk/clk.c:1072-1080`）。
所以一个你从没调过 `clk_rate_exclusive_get` 的系统，
照样可能出现 `-EBUSY`。

### 这个类比什么时候会误导你？

投影仪类比在**两个点上会骗你**：

1. **它不只锁住这一个时钟**。上面 `:930` 的递归意味着
   你锁住一个叶子时钟，**整条父链都被锁了**。
   所以你锁了一个 UART 时钟，可能导致别人调不了 CPU 的频率。
   排查时看到一个八竿子打不着的时钟返回 `-EBUSY`，
   第一动作是 `cat <名>/clk_protect_count`，然后**顺着树往下找是谁锁的**。
2. **它不阻止时钟被关**。`protect_count` 只管**频率**。
   一个被频率保护的时钟照样可以被 `clk_disable`，
   甚至照样可能被 `clk_disable_unused` 关掉（§8）——
   保护频率和保护开关状态是两件完全不同的事。

第三个误导点：类比暗示"锁定期间频率一定不变"。
但 `-EBUSY` 只挡**直接的** `clk_set_rate`。
提供方驱动自己去写寄存器改 PLL，核心一无所知——
`clk_summary` 里的 `rate` 列还是那个**缓存值**
（除非带 `CLK_GET_RATE_NOCACHE`，`07-debug.md` §3.2 第 5 列）。

---

## §10 读到这里之后去哪

| 想继续了解 | 去哪篇 |
|---|---|
| 每个 API 的签名和错误码 | `01-api.md` |
| `clk_core` 的完整字段、两把锁的完整清单 | `02-datastructures.md` |
| 注册/调频/批量关闭的逐段剖析 | `03-internals.md` |
| gate/divider/mux 这些基础类型怎么实现 | `04-extensions.md` |
| 消费者和提供方的代码骨架 | `05-usage.md` |
| DTS 属性和配错了怎么定位 | `06-devicetree.md` |
| debugfs / tracepoint / 错误码分诊 | `07-debug.md` |
| 本篇每条论断的取证范围 | `99-evidence.md` |
