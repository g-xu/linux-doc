---
title: 总览
---

# phy 00：总览（复习入口）

> **这一篇的定位是"复习入口"**：隔几个月回来，只读这一篇就能恢复对通用 PHY 框架的全貌认识。
> 它是对 01–08 篇的**浓缩**，不引入任何新论断。
> **基线**：`linux-6.1.158`（`git log -1` = `f6e38ae624cf`），
> **arm64-only**，**`drivers/phy/` 与 `include/linux/phy/` 均未被本树补丁修改**（99 §1）。

---

## §0 ⚠️⚠️ 先确认你找的是哪个 "phy"

**内核里有两个完全不同的 "phy" 子系统，头文件名只差一层目录。**
**这是读 phy 相关代码时第一件要确认的事**（01 §0.1 / 02 §1.0.A / 08 §1）。

| | **本文档**（通用 PHY 框架） | **不是本文档**（PHYLIB / 网络 PHY） |
|---|---|---|
| 目录 | **`drivers/phy/`** | **`drivers/net/phy/`** |
| **头文件** | **`include/linux/phy/phy.h`**（带 `phy/`） | **`include/linux/phy.h`**（不带） |
| 实例结构 | **`struct phy`**（8 字段） | `struct phy_device` |
| 驱动 ops | **`struct phy_ops`**（12 字段） | `struct phy_driver` |
| Kconfig | **`GENERIC_PHY`** | `PHYLIB` |
| DT 属性 | **`phys` / `#phy-cells` / `phy-names`** | **`phy` / `phy-handle` / `phy-mode`** |
| 官方文档 | **`Documentation/driver-api/phy/phy.rst`** | `Documentation/networking/phy.rst` |
| 管什么 | **USB / PCIe / SATA / UFS / MIPI / DP 的物理层初始化与上电** | **以太网 PHY 芯片的链路协商** |

⚠️ **唯一的浅交叉**：**`enum phy_mode` 里有 `PHY_MODE_ETHERNET`**
（`include/linux/phy/phy.h:41`）——**表示"这条通用 PHY 当以太网串行链路用"（SerDes），
不是"这是以太网 PHY 芯片"。**
**真实例子：`drivers/phy/microchip/sparx5_serdes.c:2167` · `drivers/phy/freescale/phy-fsl-lynx-28g.c:388`。**

---

## §1 定位：它在内核里是什么

**一句话**：**通用 PHY 框架是"外置物理层芯片/模块的初始化与上电"这件事的公共抽象** ——
**USB / PCIe / SATA 控制器驱动只要说 `phy_init()` + `phy_power_on()`，
不用管那颗 PHY 是哪家的、寄存器怎么写。**

| 维度 | 结论 | 依据 |
|---|---|---|
| **子系统类型** | **框架型 + class 型**（不是总线型） | **门 1（`bus_type`）无输出、门 2（uapi）0 文件，但有 `class_create(…, "phy")`**（99 §1.2） |
| 代码位置 | **`drivers/phy/`（205 个 `.c`/`.h` / 103073 行）+ `include/linux/phy/`（9 个头 / 1125 行）** | 01 §0.2 |
| ⚠️ **核心有多小** | **`phy-core.c` 1244 行 + `phy-core-mipi-dphy.c` 164 行 = 1408 行，占目录的 1.4%** | 01 §0.2 |
| 对外符号 | **40**（38 + 2） | 01 §0.5 |
| **可插拔层数** | **一层**（`struct phy_ops`，12 字段） | 01 §1.2 / 02 §1.0.F |
| 官方概念文档 | **有！`Documentation/driver-api/phy/phy.rst`（197 行）** | ⚠️ **但有四处过时**（§2.6） |
| DT 绑定 | **159 个文件 + 一个 72 行的通用绑定** | 06 §0 |
| ⚠️ **可观测性** | ⚠️ **差**：**0 tracepoint / 0 debugfs / 0 框架级 sysfs 属性 / 0 故障注入** | 07 §0 |

**它不做什么**：

| 不做的事 | 归谁 |
|---|---|
| **不管设备枚举、不管驱动匹配** | **platform 总线 + 驱动模型**（phy 设备永远不绑 driver，02 §1.0.C） |
| **不服务"控制器内置的 PHY"** | **官方文档明确排除**：*"of use only to devices that use external PHY"*（01 §1.3） |
| **不管时钟 / 复位 / pinctrl** | **PHY 驱动自己管**（08 §6） |
| **不管 `phy-supply` 之外的电源** | **同上** |
| **不做故障注入、不提供 tracepoint** | **完全没有**（07 §3 / §6） |

---

## §2 背景：这个框架在解决什么问题

**本节走"问题背景"路径**（框架型适用）：**先说没有它时的样子，
再说它消掉了什么，最后说代价。**

### §2.1 官方文档给出的出发点

**`Documentation/driver-api/phy/phy.rst` 的 Introduction 段说了两件事**（三等证据，99 §1.3）：

1. **动机是"收拢"**：原文 *"The intention of creating this framework is to bring the PHY drivers
   spread all over the Linux kernel to drivers/phy to increase code re-use and for better code
   maintainability."*
   ⇒ **在这个框架之前，PHY 驱动散落在 `drivers/usb/`、`drivers/ata/`、各 SoC 目录里。**
2. ⚠️ **适用范围有硬边界**：原文 *"This framework will be of use only to devices that use
   external PHY (PHY functionality is not embedded within the controller)."*

**老框架的残留今天还能看到**：**`drivers/usb/phy/`（20 个 `.c` / 10895 行）仍然在树里**，
**而且新框架用一行硬编码把那类节点让给了它**（§2.4）。

### §2.2 问题一：同一个控制器要跑在几十种 PHY 上

**一个 dwc3 USB 控制器 IP 被几十家 SoC 厂商集成**，
**每家配的 PHY 完全不同**（寄存器布局、上电时序、要几个时钟、要不要外部 LDO）。

**没有抽象层时只有两条路**：

| 做法 | 后果 |
|---|---|
| **控制器驱动里写 `if (soc == A) … else if (soc == B) …`** | **控制器驱动变成厂商代码的垃圾场** |
| **每家 fork 一份控制器驱动** | **一个 bug 修几十处** |

⇒ **框架把边界画在"`phy_init` / `phy_power_on`"这两个动词上**：
**控制器只说动词，PHY 驱动实现动词。**

### §2.3 问题二：一个 IP 里有多条链路

**一个 SerDes IP 可能有 4 条 lane、一个 USB2 PHY 可能有 host 口和 otg 口。**
**它们共用一套寄存器，但要能被不同的控制器分别引用。**

⇒ **所以框架把"提供者"和"实例"分成两个概念**（02 §1.0.B / 08 §7）：
**`struct phy_provider`（1 个）+ `struct phy`（N 个）+ `of_xlate`（翻译表）。**

### §2.4 问题三：与老框架划清界限

**`drivers/usb/phy/` 那套老框架还在服务一部分节点。**
⇒ **新框架用一行硬编码显式拒绝其中最典型的一类**：

```c
/* drivers/phy/phy-core.c:581-584 —— 全框架唯一一处按 compatible 判断 */
	if (of_device_is_compatible(args.np, "usb-nop-xceiv")) {
		phy = ERR_PTR(-ENODEV);
		goto out_put_node;
	}
```

**重跑 `grep -c 'of_device_is_compatible' drivers/phy/phy-core.c` ⇒ 1。**

### §2.5 问题四：PHY 是可选的

**同一个控制器驱动，在 A 板上外接 PHY，在 B 板上 PHY 集成在 SoC 里（不需要这个框架）。**
⇒ **框架的答案是"发明一个空 PHY"**：
**`NULL` 是合法的 phy 引用，所有操作变成 NOP**（01 §4.3 / 08 §3）。

**实现上靠两件事**：
**① 九个操作 API 各有一行 `if (!phy) return 0`；
② `phy_optional_get()` 把 `-ENODEV` 转成 NULL（`drivers/phy/phy-core.c:789-790`）。**

### §2.6 ⚠️ 它为此付出的代价（读 phy 一定要知道的五条）

#### ⚠️ 代价 1：官方文档有四处过时

**有真正的概念文档是好事（对比 regmap 的 0 篇），但它落后于代码。四处，全部以代码为准**（03 §C2 / 99 §1.3）：

| # | 文档说 | 代码是 | 后果 |
|---|---|---|---|
| 1 | **`phy_put(phy)` 一个参数** | **`phy_put(dev, phy)` 两个** | **编译失败（显式）** |
| ⚠️⚠️ **2** | **"所有 NULL phy 调用都是 NOP"** | ⚠️ **`phy_configure` / `phy_validate` 返回 `-EINVAL`**（`drivers/phy/phy-core.c:508-509` / `:543-544`） | ⚠️ **静默的运行期失败** |
| 3 | `devm_of_phy_provider_register_full` 展开到 `__devm_..._full` | **那个符号不存在** | 文档笔误 |
| 4 | **"`pm_runtime_enable` 无条件调用"** | **`if (pm_runtime_enabled(dev))`**（`:991`） | **误以为一定有 PM runtime** |

#### ⚠️ 代价 2：大量"ops 没实现就返回成功"

| API | 驱动没实现时 |
|---|---|
| `phy_init` / `phy_exit` / `phy_power_on` / `phy_power_off` | **返回 0**（合理，没什么要做的） |
| ⚠️`phy_set_mode_ext` | ⚠️ **返回 0 **且** 把 `attrs.mode` 写成新值**（`:407-410`）⇒ **`phy_get_mode()` 之后会骗你** |
| `phy_set_media` / `phy_set_speed` / `phy_reset` / `phy_calibrate` | 返回 0 |
| `phy_configure` / `phy_validate` | **返回 `-EOPNOTSUPP`**（唯一会报"没实现"的两个） |

#### ⚠️ 代价 3：两个计数没有下溢保护

**`phy_power_off()` 多调一次 ⇒ `power_count` 变 -1，还会多做一次
`phy_pm_runtime_put`（`:390`）和 `regulator_disable`（`:392-393`）。**
⇒ **警告出现在 regulator 子系统，根因在 phy 消费者**（02 §1.0.D / 07 §5.3）。

#### ⚠️ 代价 4：DT 错误被归一成 `-ENODEV`

**`_of_phy_get()` 把所有 DT 解析失败都变成 `-ENODEV`**（`drivers/phy/phy-core.c:577-578`）
⇒ **"故意没配 PHY"和"`phy-names` 拼错了"完全无法区分**
⇒ **配合 `optional_get` 会静默吞掉真实的 DT 错误**（06 §5 / 08 §3）。

#### ⚠️ 代价 5：可观测性接近于零

**0 tracepoint、0 debugfs、0 框架级 sysfs 属性、0 故障注入。**
**`init_count`/`power_count` 看不到、硬件实际模式看不到、`ops` 缺哪些回调看不到**（07 §3.2）。

### §2.7 本文档裁掉了什么（别当成遗漏）

| 裁掉的 | 为什么 |
|---|---|
| **163 个 PHY 驱动的实现** | **只讲契约**；代表驱动的选法见 99 §2.4 |
| **MIPI D-PHY 的 25 个字段与规范的对应、时序公式** | **需要 MIPI 规范文本（树外资料）**（04 §1.3） |
| **`drivers/usb/phy/`（10895 行）老框架** | **独立子系统**；只交代边界在哪一行（04 §4.1） |
| **`drivers/net/phy/` PHYLIB** | **完全无关的同名子系统**（§0） |
| **`drivers/usb/common/ulpi.c`（380 行）** | **ULPI 总线属 USB 子系统**；只讲桥接那 32 行（04 §3） |
| **`phy_configure_opts_dp` / `_lvds` 的逐字段语义** | **需要 DP / LVDS 规范** |
| **`device_link_add` 的内部语义** | **属驱动模型**；只说"影响 suspend/shutdown 顺序"（03 §B5，99 §5.3 已登记为推断） |
| **arm64 DTS 里 149 个 `phy-supply` 的逐个分类** | ⚠️ **其中一部分属网络子系统**（06 §4.2）；**未逐个分类**（99 §5.3） |

---

## §3 概念模型：五个词讲完

| 词 | 是什么 | 一句话 |
|---|---|---|
| **`phy_provider`** | **实现者**（5 字段，不是 `struct device`） | **"我这个 IP 能提供 PHY"** |
| **`of_xlate`** | **翻译表回调** | **"DT 里那个数字是我第几条链路"** |
| **`struct phy`** | **实例**（8 字段，是 `struct device`） | **"一条具体的物理链路"** |
| **`phy_ops`** | **唯一一层可插拔**（12 字段） | **"这条链路怎么初始化/上电/切模式"** |
| **两个计数** | **`init_count` / `power_count`** | **"有几个人要它配好 / 有几个人要它供电"** |

```
   consumer（USB/PCIe/SATA 控制器驱动）
        │  devm_phy_get(dev, "usb2-phy")
        │  phy_init() → phy_set_mode() → phy_power_on()
        ▼
  ┌───────────────── phy-core.c（1244 行）─────────────────┐
  │                                                        │
  │  DT 解析（phys/#phy-cells/phy-names）                   │
  │  provider 表（phy_provider_list + 一把 mutex）          │
  │  引用计数（module ×2 / device / init+power ×2）          │
  │  电源（phy->pwr regulator + PM runtime 传导）            │
  │  devres（三个 release 回调）                            │
  │  class（/sys/class/phy/，唯一观测面）                 │
  └────────────────────────┬───────────────────────────────┘
                           │ 唯一一层：struct phy_ops
  ┌────────────────────────▼───────────────────────────────┐
  │ PHY 驱动（provider）—— 163 个 phy_ops 实例              │
  └────────────────────────────────────────────────────────┘
```

⚠️ **四个必须记住的"不是"**：
1. **不是总线**：**没有 `bus_type`，phy 设备永远不绑 driver**（02 §1.0.C / 08 §8）。
2. **provider 不是设备**：**它只持有 `struct device *`，不出现在 sysfs 里**（02 §1.0.B）。
3. **`attrs` 不是硬件状态**：**三个字段全是软状态，没有任何硬件读回**（02 §5.2）。
4. **`init` 和 `power_on` 不是一件事的两步**：**两个独立计数**（02 §1.0.D / 08 §2）。

---

## §4 数据结构速览

| 结构 | 位置 | 字段数 | 谁填 | 关键字段 |
|---|---|---|---|---|
| `struct phy` | `include/linux/phy/phy.h:152-161` | **8** | **框架** | `dev`（class=phy_class, parent=provider）· `ops` · `mutex` · `init_count` · `power_count` · `attrs` · `pwr` |
| `struct phy_ops` | `include/linux/phy/phy.h:84-127` | **12** | **PHY 驱动** | `init` `exit` `power_on` `power_off` `set_mode` `configure` `validate` + **`owner`（别漏）** |
| `struct phy_provider` | `include/linux/phy/phy.h:171-178` | **5** | **注册宏 + 驱动** | `dev` · **`children`（永不为 NULL）** · `owner` · `list` · `of_xlate` |
| `struct phy_attrs` | `include/linux/phy/phy.h:135-139` | **3** | **框架/驱动** | **`bus_width` `max_link_rate` `mode` ——全是软状态** |
| `struct phy_lookup` | `include/linux/phy/phy.h:187-192` | **4** | **非 DT 场景的驱动** | **`dev_id`/`con_id` 是裸指针，不复制字符串** |
| `union phy_configure_opts` | `include/linux/phy/phy.h:64-68` | **3 个成员** | 消费者 | ⚠️ **无 tag 字段**（04 §2.2 / 08 §5） |

**五个文件级静态变量 + 一把 per-phy 锁**（02 §6）：
**`phy_class` · `phy_provider_mutex` · `phy_provider_list` · ⚠️`phys`（装的是 lookup，不是 phy）· `phy_ida`
+ `phy->mutex`。**

---

## §5 机制速览

### §5.1 provider 侧两步

| 步 | API | 位置 | 要点 |
|---|---|---|---|
| ① | `devm_phy_create(dev, node, ops)` | `drivers/phy/phy-core.c:1019` | **九步**：`WARN_ON(!dev)` → IDA → `device_initialize` → 三个赋值 → `dev_set_name("phy-%s.%d")` → **`regulator_get_optional(…, "phy")`** → `device_add` → **有条件启用 PM runtime**（03 §A2） |
| ② | `devm_of_phy_provider_register(dev, of_xlate)` | `:1151` | **`children` 为 NULL 时替换成 `dev->of_node`（`:1116-1118`）** → 挂链表 |

⚠️ **顺序不能反**：**`of_phy_simple_xlate()` 靠遍历 `phy_class` 找实例**（`:713-721`）。

### §5.2 consumer 侧四件套

```
devm_phy_get(dev, "usb2-phy")   →  phy_init(phy)  →  phy_set_mode(phy, M)  →  phy_power_on(phy)
                                                                                    ↓ 使用
                          devres 自动 phy_put ←  phy_exit(phy)  ←  phy_power_off(phy)
```
⚠️ **只有 `phy_put` 是自动的**（03 §B3）——**`power_off` / `exit` 必须自己写。**

### §5.3 `phy_power_on()` 的三层电源

**① `regulator_enable(phy->pwr)`（`:330`）→ ② `phy_pm_runtime_get_sync()`（`:335`）
→ ③ `ops->power_on()`（`:343`，仅 `power_count == 0` 时）**（02 §1.0.E / 08 §6）。
⚠️ **`-ENOTSUPP` 被显式吃掉三处**（`:253-255` / `:293-295` / `:336-339`，03 §B4.2）。

### §5.4 引用计数四层

**① provider 模块（查找期间）· ② PHY 驱动模块（消费者持引用期间）·
③ device 引用 · ④ `init_count`/`power_count`**（03 §B2）。
⚠️ **只有第 ④ 层没有下溢保护。**

### §5.5 可观测性

| 出口 | 有没有 |
|---|---|
| **`/sys/class/phy/`** | **有** ——**名字 `phy-<provider设备名>.<全局id>`**（07 §2.1） |
| **`consumer:`/`supplier:` 链接** | **有**（来自 `device_link_add`，裸 `of_phy_get` 不建） |
| **dmesg** | **16 处打印**，**只有 2 条与 DT 相关**（07 §4.2） |
| **tracepoint / debugfs / 框架 sysfs 属性 / 故障注入** | **全 0** |

---

## §6 端到端调用路径

```
① platform 总线 probe PHY 驱动
     ├─ devm_phy_create(&pdev->dev, NULL, &foo_ops)     phy-core.c:1019
     │    ├─ ida_simple_get(&phy_ida, …)                      :957
     │    ├─ dev.class = phy_class; dev.parent = dev;
     │    │  dev.of_node = node ?: dev->of_node                :967-969
     │    ├─ dev_set_name("phy-%s.%d", dev_name(dev), id)      :973
     │    ├─ regulator_get_optional(&phy->dev, "phy")          :978  ← DT 的 phy-supply
     │    ├─ device_add(&phy->dev)                             :987
     │    └─ if (pm_runtime_enabled(dev)) pm_runtime_enable()   :991
     ├─ phy_set_drvdata(phy, priv)
     └─ devm_of_phy_provider_register(&pdev->dev, of_phy_simple_xlate)  :1151
          └─ children = dev->of_node; list_add_tail(...)        :1117 / :1130

② platform 总线 probe 控制器驱动
     └─ devm_phy_get(&pdev->dev, "usb2-phy")                   :806
          └─ phy_get()                                          :738
               ├─ of_property_match_string(of_node, "phy-names", "usb2-phy")  :746
               ├─ _of_phy_get(of_node, index)                    :568
               │    ├─ of_parse_phandle_with_args(np,"phys","#phy-cells",…) :575
               │    │     失败 ⇒ -ENODEV                          :577-578
               │    ├─ compatible == "usb-nop-xceiv" ⇒ -ENODEV    :581-584
               │    ├─ of_phy_provider_lookup(args.np)            :587
               │    │     先比 of_node，再比 children 的子节点     :137-144
               │    ├─ !of_device_is_available ⇒ dev_warn + -ENODEV :593-596
               │    └─ provider->of_xlate(provider->dev, &args)    :599
               ├─ try_module_get(phy->ops->owner) 失败 ⇒ -EPROBE_DEFER :761-762
               ├─ get_device(&phy->dev)                            :764
               └─ device_link_add(dev, &phy->dev, DL_FLAG_STATELESS) :766

③ 控制器驱动使用
     ├─ phy_init(phy)         :245  ← pm_get_sync + mutex + init_count 0→1
     ├─ phy_set_mode(phy, M)  :399（宏 → phy_set_mode_ext）
     └─ phy_power_on(phy)     :322  ← regulator + pm_get_sync + power_count 0→1

④ 拆除（前两步必须手写）
     phy_power_off → phy_exit → [devres] phy_put → device_link_remove + of_phy_put
                                                          └─ ops->release + module_put + put_device
⑤ PHY 驱动 remove（devres 后进先出）
     [devres] of_phy_provider_unregister  →  [devres] phy_destroy
                                                  └─ pm_runtime_disable + device_unregister
                                                        └─ phy_release: regulator_put + ida_remove + kfree
```

⚠️ **这条链上有 6 处静默失败**（06 §5 决策树的 ①②③④⑥ + `.owner` 漏填）。

---

## §7 子系统关系

### §7.1 谁用 phy（消费者方向）

| 消费者 | 典型 API |
|---|---|
| **USB 控制器**（dwc3 / ehci / xhci / dwc2） | **`devm_phy_get` + 四件套**；**多 PHY 用 `devm_of_phy_get_by_index`** |
| **PCIe 控制器** | **常见多 lane，`phys = <&phy 0>, <&phy 1>, …`**（06 §3.1） |
| **SATA / AHCI** | **`libahci_platform` 自己也取 `phy-supply`**（06 §4.2） |
| **UFS 控制器** | `PHY_MODE_UFS_HS_A/B` |
| **DRM / MIPI DSI / CSI** | **`phy_configure()` + `union.mipi_dphy`**（04 §2） |
| **网口（SerDes）** | **`PHY_MODE_ETHERNET`**（§0） |

### §7.2 phy 用谁（依赖方向）

| 被依赖的 | 用在哪 |
|---|---|
| **regulator** | **`phy->pwr`**（`drivers/phy/phy-core.c:978` / `:330` / `:393` / `:1226`） |
| **PM runtime** | **`phy_pm_runtime_*` 六个包装 + 父子传导**（03 §B4） |
| **驱动模型（device / class / device link / devres）** | **框架的骨架就是这些**（03 §B2 / §B3 / §B5） |
| **OF** | **`of_parse_phandle_with_args` / `of_device_is_available` / `of_device_is_compatible`** |
| **IDA** | **`phy_ida`** |

### §7.3 与本项目其他文档集的关系

| 相邻子系统 | 关系 | 去 |
|---|---|---|
| **regulator** | **`phy-supply` 那条线**；⚠️ **计数下溢的警告会打在 regulator 侧**（07 §5.3） | **`output/regulator/`** |
| **of** | **`phys` / `#phy-cells` 的解析** | `output/of/` |
| **clk / reset** | **PHY 驱动自己管，不经框架** | 各自文档集 |
| **regmap** | **对照用**：**regmap 三层可插拔 / 强可观测性 vs phy 一层 / 零可观测性** | `output/regmap/` |

---

## §8 导读：接下来读哪一篇

| 我现在… | 读 | 里面有什么 |
|---|---|---|
| **要写一个 PHY 驱动** | **`05-usage.md` §1** | **最小骨架 + 五个必看点（含 `.owner`）** |
| **要在控制器里用 PHY** | **`05-usage.md` §3 / §4** | **四件套 + 可选 PHY 的两个反例** |
| 要查 API 语义 | `01-api.md` | §0 代码地图 · §2 12 个回调 · §9 错误码 |
| 要看结构体关系 | `02-datastructures.md` | §1.0 六组易错关系 · §3 数据流 · §6 锁 |
| 要理解内部流程 | `03-internals.md` | Part A 时间线 · Part B 横切 · **Part C 四处文档不一致 + 三类静默失效** |
| 子框架与边界 | `04-extensions.md` | MIPI D-PHY · 三个参数族 · ULPI 桥 · **三个死字宏** |
| **DTS 写了没反应** | **`06-devicetree.md` §5** | **八层决策树**；§4.2 `phy-supply` 的 8 个消费者 |
| **出问题不知道看哪** | **`07-debug.md` §0 / §8** | **`/sys/class/phy/` + `devices_deferred` 两步分诊** |
| **看不懂"为什么这样设计"** | **`08-feynman.md`** | **八个主题，每个都有"这个类比什么时候会误导你"** |
| **哪些结论不能直接信** | **`99-evidence.md` §5** | **未验证项四层 + 逐篇取证表** |

### §8.1 第一次读 phy 的推荐顺序

1. **`08-feynman.md` §1** ——**先确认你读的不是网络 PHY。**
2. **`01-api.md` §2** ——**12 个回调看一遍，框架的全部能力就在这里。**
3. **`03-internals.md` §0.2** ——**一张生命周期时间线。**
4. **`05-usage.md` §1 + §3** ——**两端各抄一个骨架。**
5. **`03-internals.md` §C2 + §C3** ——**在踩坑之前先知道有哪些坑。**

### §8.2 ⚠️ 五条"最容易记错"的事（复习时优先重看）

| 事 | 正确的是 | 在哪 |
|---|---|---|
| ⚠️ **NULL phy 调所有 API 都安全吗** | ⚠️ **不**：**`phy_configure` / `phy_validate` 返回 `-EINVAL`** | 01 §4.3 / 05 §4.2 / 08 §3 |
| ⚠️ **`phy_put` 几个参数** | ⚠️ **两个 `(dev, phy)`**（文档写的是一个） | 01 §4.2 / 05 §7.4 |
| ⚠️ **`phy_ops` 里必填的是什么** | ⚠️ **`.owner`** ——**漏了会让消费者永远 `-EPROBE_DEFER` 且无日志** | 05 §7.5 / 07 §2.3 |
| ⚠️ **`phy_set_mode` 返回 0 就是成功了吗** | ⚠️ **不**：**驱动没实现也返回 0，且会写脏 `attrs.mode`** | 01 §5.1 / 08 §4 |
| ⚠️ **`devm_phy_get` 会帮你 `power_off` 吗** | ⚠️ **不会** ——**只帮你 `phy_put`** | 03 §B3 / 05 §3.1 |
