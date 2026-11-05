---
title: 设备树
---

# pinctrl 的设备树配置

> **本篇的定位**：DTS 里跟 pinctrl 有关的每一条属性，谁在哪一行读它，写错了会怎样，怎么定位。
>
> **arm64-only**：所有 DTS 例子取自 `arch/arm64/boot/dts/`，所有统计按 arm64 过滤。裁掉了什么见 `99-evidence.md` §2.7。
>
> **一条必须先说清的事**：pinctrl 的 DT 绑定分成**两层，标准化程度完全不同**。
>
> | 层 | 谁定义 | 标准化程度 |
> |---|---|---|
> | **消费者侧**（`pinctrl-<N>` / `pinctrl-names`） | pinctrl 核心（`drivers/pinctrl/devicetree.c`） | 完全标准，所有 SoC 一样 |
> | **pin 配置节点内部**（`pins` / `function` / `bias-*` / `rockchip,pins` / …） | **每个控制器驱动自己** | 无统一标准，只有一套*可选*的泛型帮助绑定 |
>
> 官方绑定文档把这句话说得很直白（`Documentation/devicetree/bindings/pinctrl/pinctrl.yaml:16-20`）：「The contents of each of those pin configuration child nodes is defined **entirely by the binding for the individual pin controller device**. There exists **no common standard** for this content. The pinctrl framework only provides generic helper bindings that the pin controller driver **can** use.」
>
> 所以本篇 §1/§2 是"到处都成立"的，§3 是"只在用了 pinconf-generic 的那 66 个驱动上成立"（见 `04-extensions.md` §3.7）。

---

## §0 发现路径分诊表

不要先问"pinctrl 有没有 DT 绑定"。pinctrl 的 mapping table 有**四条**来源，DT 只是其中一条（而且是 arm64 上唯一重要的那条）。

| 发现路径 | 谁扫描 | 走 DT | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|
| **① DT phandle 路径** | `pinctrl_dt_to_map()`（`drivers/pinctrl/devicetree.c:200-291`），由 `create_pinctrl()` 在 `core.c:1055` 调 | 是 | 唯一主路径 | **全篇**（§1–§8） |
| ② 板级静态 map 数组 | 驱动/板文件自己调 `pinctrl_register_mappings()` | 否 | arm64 基本绝迹（全树只 4 个使用者，2 个是 x86-only） | 只在 §0.2 交代，代码骨架见 `05-usage.md` §4 |
| ③ hog（控制器给自己配） | `pinctrl_claim_hogs()`（`core.c:2073-2109`），从已注册的 map 里挑 `dev_name == 控制器自己` 的条目 | 间接是——DT 路径注册的 map 同样会被 hog 逻辑扫到 | 少见 | §2.3 讲它和 DT 的交叉点 |
| ④ ACPI | — | 否 | **本文档集不覆盖**（arm64-only 约束） | 不覆盖，见 `99-evidence.md` §2.7 |

**路径 ② 和路径 ① 走的是同一个注册函数**：`devicetree.c:96` 就是 `pinctrl_register_mappings(map, num_maps)`。区别只在"map 数组是驱动写死的还是从 DT 解析出来的"。这一点在 `05-usage.md` §4.3 有完整讨论。

### §0.1 按任务找章节

| 你想干的事 | 去哪节 |
|---|---|
| 给一个新外设节点加 pinctrl 配置 | §1.2、§1.3、§4.1 |
| 加一个 sleep 态（挂起时改引脚） | §1.4、§4.2 |
| 看核心到底会读哪几个属性 | §1.1（总表） |
| 搞清 `pins` / `groups` / `function` 三者关系 | §3.1 |
| 查某个 `bias-*` / `drive-*` 属性名对不对 | §3.2（28 条标准属性总表） |
| 厂商私有属性（`mediatek,tdsel` 之类）从哪来 | §3.5 → `04-extensions.md` §3.8 |
| DTS 写了但没生效，一行日志都没有 | **§5**（静默失效决策树） |
| 看到一条 pinctrl 报错，想知道什么意思 | §7（错误消息映射表） |
| 症状是"某个外设完全不工作" | §8 |
| `#pinctrl-cells` 是什么 | §2.1 |

### §0.2 本篇不覆盖什么

| 内容 | 去哪 |
|---|---|
| 板级静态 map 数组的 13 个宏怎么用 | `05-usage.md` §4.1、§4.2 |
| `dt_node_to_map` 回调怎么实现 | `04-extensions.md` §3.4、`05-usage.md` §1.1 |
| map → setting → 硬件的完整应用流程 | `03-internals.md` §A5、§A11 |
| debugfs 怎么反查配置有没有生效 | `07-debug.md` §3 |
| GPIO 与 pinctrl 的 `gpio-ranges` | `04-extensions.md` §5.6（它是 **gpiolib** 的属性，不是 pinctrl 的） |

---

## §1 消费者侧：核心真正会读的属性

### §1.1 总表

**这张表是完整的**——`drivers/pinctrl/devicetree.c` 全文只有 4 处属性读取调用（`of_property_read_bool` / `of_find_property` / `of_property_read_string_index` / `of_get_property` + `of_property_read_u32`），全部列在下面：

| 属性 | 写在哪个节点 | 类型 | 读它的代码 | 必需性 | 不写会怎样 |
|---|---|---|---|---|---|
| `pinctrl-0` | **消费者**（用引脚的那个外设） | phandle 列表 | `devicetree.c:226`（`kasprintf("pinctrl-%d", 0)`）→ `:231` `of_find_property` | 有 `pinctrl-N` 就必须有 `pinctrl-0` | `state == 0` 时返回 `-ENODEV`（`:233-237`），整个设备没有任何 pinctrl 状态 |
| `pinctrl-1` … `pinctrl-N` | 同上 | phandle 列表 | 同上，`state` 递增循环 | 可选 | 循环在第一个缺号处 `break`（`:238`）——**编号必须连续**，跳号后面的全部丢弃 |
| `pinctrl-names` | 同上 | 字符串列表 | `devicetree.c:244-245` `of_property_read_string_index(np, "pinctrl-names", state, &statename)` | 可选 | 状态名退化成**数字字符串**（`:251-252`：`statename = prop->name + strlen("pinctrl-")`，即 `"0"` / `"1"`） |
| `pinctrl-use-default` | **控制器**节点，或 pin 配置节点及其任意祖先 | boolean | `devicetree.c:126-127` `of_property_read_bool(np_pctldev, "pinctrl-use-default")` | 可选 | 控制器没驱动时，消费者会被无限 `-EPROBE_DEFER` 而不是放行（见 §2.2） |
| `#pinctrl-cells` | **控制器**节点（父或祖父） | u32 | `devicetree.c:302` 查 `np->parent`，失败再查 `:304-305` `np->parent->parent` | 只有 `pinctrl-single` 家族要 | 用到 `pinctrl_parse_index_with_args()` 的驱动会拿到 `-ENOENT`（`:307`） |

注意 `pinctrl-N` 的属性名是**运行时拼出来的**（`:226` `kasprintf(GFP_KERNEL, "pinctrl-%d", state)`），所以 `grep '"pinctrl-0"'` 在内核源码里**找不到任何东西**。这是索引工具和 `grep` 都会漏掉的那类：属性名不是字面量。

### §1.2 `pinctrl-<N>`：状态 ID → phandle 列表

```dts
device {
        pinctrl-names = "default", "sleep";
        pinctrl-0 = <&sdc1_clk>, <&sdc1_cmd>, <&sdc1_data>;   /* 状态 0 = default */
        pinctrl-1 = <&sdc1_clk_sleep>, <&sdc1_cmd_sleep>;     /* 状态 1 = sleep */
};
```

解析过程（`pinctrl_dt_to_map()`，`devicetree.c:200-291`）：

```
state = 0, 1, 2, ...  无限循环，直到某个 pinctrl-<state> 不存在
  │
  ├─ :226  propname = kasprintf("pinctrl-%d", state)
  ├─ :231  prop = of_find_property(np, propname, &size)
  │        └─ 不存在 → state==0 ? return -ENODEV (:235) : break (:238)
  ├─ :240  list = prop->value;  size /= sizeof(__be32)   ← size 变成 phandle 个数
  ├─ :244  statename = pinctrl-names[state]，失败则用 "0"/"1"/… (:252)
  ├─ :255  for (config = 0; config < size; config++)      ← 逐个 phandle
  │          :256  phandle = be32_to_cpup(list++)
  │          :259  np_config = of_find_node_by_phandle(phandle)
  │          │     └─ 找不到 → dev_err "prop %s index %i invalid phandle" (:261-263) + -EINVAL
  │          :269  dt_to_map_one_config(p, pctldev, statename, np_config)
  │                └─ 返回 1（hog 自引用）→ :272-273 continue，不算错
  └─ :279  if (!size) → dt_remember_dummy_state()   ← 空状态，见 §1.5
```

三条容易踩的规则：

| 规则 | 依据 | 违反后果 |
|---|---|---|
| 编号必须从 0 开始、连续 | `:224` `for (state = 0; ; state++)` + `:238` `break` | 写了 `pinctrl-1` 没写 `pinctrl-0` → `-ENODEV`（`:235`），设备直接没有 pinctrl |
| 一个状态可以引多个 phandle | `:255-276` 逐个解析，maps 累加 | 无 |
| 多个 phandle **可以指向不同控制器** | `dt_to_map_one_config()` 每次独立向上找控制器（`:123-155`） | 无——这是官方鼓励的用法（`pinctrl-bindings.txt` 的 `pinctrl-0` 说明） |

**被引用的节点必须是那个控制器的后代**。`dt_to_map_one_config()` 从 `np_config` 开始一路 `of_get_next_parent()` 往上爬（`:129`），爬到根节点还没找到已注册的 pin controller 就失败（`:130-137`）。绑定文档的原话是「These referenced pin configuration nodes **must be child nodes of the pin controller** that they configure」——但代码实际允许**任意深度的后代**，`pinctrl.yaml:22-23` 也明确说「need not be direct children … they may be grandchildren」。

爬到根节点时的返回值**不是** `-ENODEV`，而是 `-EPROBE_DEFER`：

```c
/* devicetree.c:130-137 */
if (!np_pctldev || of_node_is_root(np_pctldev)) {
        of_node_put(np_pctldev);
        ret = -ENODEV;
        /* keep deferring if modules are enabled */
        if (IS_ENABLED(CONFIG_MODULES) && !allow_default && ret < 0)
                ret = -EPROBE_DEFER;
        return ret;
}
```

`CONFIG_MODULES=y`（arm64 defconfig 的常态）且没写 `pinctrl-use-default` 时 → **永久 `-EPROBE_DEFER`**。这是 §8 里"外设 probe 一直不完成、`/sys/kernel/debug/devices_deferred` 里躺着"这一类症状最常见的根因。

### §1.3 `pinctrl-names`：给状态起名

`pinctrl-names` 是**纯可选**的，但不写会让代码里的 `pinctrl_lookup_state(p, "default")` 找不到东西——因为状态名会退化成 `"0"`。

`:251-252` 的退化写法值得看一眼：

```c
if (ret < 0)
        statename = prop->name + strlen("pinctrl-");
```

它不分配内存，直接**指进属性名字符串的后半截**（`"pinctrl-0"` + 8 = `"0"`）。所以 `:221` 那句 `of_node_get(np)` 必须持住 node 引用直到 map 被释放——注释写在 `:220`：「We may store pointers to property names within the node」。

arm64 上的实际分布（`arch/arm64/boot/dts/`）：

| 属性/名字 | 出现次数 | 备注 |
|---|---|---|
| `pinctrl-0 =` | **4193** | |
| `pinctrl-names =` | **4135** | 比 `pinctrl-0` 少 58 —— 这 58 处是"只用数字状态 ID"的写法 |
| `pinctrl-1 =` | 465 | |
| `pinctrl-2 =` | 110 | |
| `pinctrl-3 =` | 7 | 最多用到 3 |
| 名字 `"default"` | 4102 | |
| 名字 `"sleep"` | 163 | |
| 名字 `"idle"` | 11 | |
| 名字 `"init"` | **5** | 极罕见，见 §1.4 |

重跑：
```bash
for p in pinctrl-names pinctrl-0 pinctrl-1 pinctrl-2 pinctrl-3; do
  echo -n "$p: "; grep -rho "$p[[:space:]]*=" arch/arm64/boot/dts/ | wc -l
done
grep -rho 'pinctrl-names[[:space:]]*=[^;]*' arch/arm64/boot/dts/ \
  | grep -o '"[a-z0-9_-]*"' | sort | uniq -c | sort -rn | head -20
```

### §1.4 四个有特殊含义的状态名

只有这四个名字被**内核代码**认识，其余名字纯粹靠驱动自己 `pinctrl_lookup_state()` 去查（宏定义在 `include/linux/pinctrl/pinctrl-state.h:33-36`）：

| DTS 里写的名字 | 宏 | 谁在什么时候用它 | arm64 实际用量 |
|---|---|---|---|
| `"default"` | `PINCTRL_STATE_DEFAULT` | `pinctrl_bind_pins()` 在 probe **之前**自动应用（`drivers/base/pinctrl.c:39-57`）；`pinctrl_init_done()` 在 probe **之后**再切一次（`core.c:1554`） | 4102 |
| `"init"` | `PINCTRL_STATE_INIT` | 有它时 probe 前先应用 `init` 而不是 `default`（`drivers/base/pinctrl.c:53-57`），probe 成功后由 `pinctrl_init_done()` 换成 `default` | 5 |
| `"sleep"` | `PINCTRL_STATE_SLEEP` | 只被 `pinctrl_pm_select_sleep_state()`（`core.c:1606-1612`）用，**必须驱动自己在 `.suspend()` 里调**——核心不会自动切 | 163 |
| `"idle"` | `PINCTRL_STATE_IDLE` | 同上，`pinctrl_pm_select_idle_state()`（`core.c:1620-1626`） | 11 |

**`sleep` / `idle` 只是"名字被预留"，不是"内核自动帮你切"**。`pinctrl_bind_pins()` 在 `CONFIG_PM` 下只是**查一下这两个状态存在不存在**并缓存到 `dev->pins->sleep_state` / `idle_state`（`drivers/base/pinctrl.c:64-81`），查不到就 `dev_dbg "no sleep pinctrl state"` 然后什么也不做。真正的切换必须驱动显式调三个 `pinctrl_pm_select_*` 之一。DTS 里写了 `sleep` 但驱动没调 → **完全静默，什么都不发生**。这是 §5 决策树的第 4 个分支。

`"init"` 的用途在 kerneldoc 里说得很具体（`pinctrl-state.h:16-22`）：「There are some drivers where that is not appropriate becausing doing so would **glitch the pins**」——先进 `init` 再进 `default`，避免探测过程中引脚抖动。arm64 上 5 处全在 rockchip：

```bash
grep -rn 'pinctrl-names = "init"' arch/arm64/boot/dts/
# rockchip/rk356x.dtsi:1513  rk3368.dtsi:469  px30.dtsi:761  rk3328.dtsi:556  rk3399.dtsi:889
```

### §1.5 空状态：`pinctrl-0 = <>`

绑定文档明确允许（`pinctrl-bindings.txt` 的 `pinctrl-0` 段）：「In some cases, it may be useful to define a state, but for it to be **empty**. … If the binding for that IP block requires certain pin states to exist, they must still be defined, but may be left empty.」

代码里的处理（`devicetree.c:279-283`）：

```c
/* No entries in DT? Generate a dummy state table entry */
if (!size) {
        ret = dt_remember_dummy_state(p, statename);
```

`dt_remember_dummy_state()`（`:186-198`）分配一个 `struct pinctrl_map`，只填 `map->type = PIN_MAP_TYPE_DUMMY_STATE`（`:195`），`pctldev` 传 `NULL`。释放路径要为这个特例分叉：`dt_free_map()` 里 `if (pctldev) … else kfree(map)`（`:40-47`），注释 `:45`「There is no pctldev for PIN_MAP_TYPE_DUMMY_STATE」。

应用这个状态时会打一条 `dev_dbg`（`core.c:1222`）：`"using pinctrl dummy state (%s)"`。

**arm64 树里一个都没有**：
```bash
grep -rn 'pinctrl-[0-9][[:space:]]*=[[:space:]]*<>' arch/arm64/boot/dts/ | wc -l   # → 0
```
所以这条路径在 arm64 上是纯粹的"机制存在但没人用"。写进来是因为读者在 `03-internals.md` 里会看到 `PIN_MAP_TYPE_DUMMY_STATE` 这个枚举值，需要知道它从哪来。

---

## §2 控制器侧的两个属性

### §2.1 `#pinctrl-cells`：只有 `pinctrl-single` 家族要

这个属性**不是 pinctrl 通用绑定的一部分**，它只服务一套特定的"索引 + N 个 u32"表达法。核心侧的消费代码在 `devicetree.c:297-311`：

```c
static int pinctrl_find_cells_size(const struct device_node *np)
{
        const char *cells_name = "#pinctrl-cells";
        int cells_size, error;

        error = of_property_read_u32(np->parent, cells_name, &cells_size);   /* :302 父 */
        if (error) {
                error = of_property_read_u32(np->parent->parent,             /* :304 祖父 */
                                             cells_name, &cells_size);
                if (error)
                        return -ENOENT;                                      /* :307 */
        }
        return cells_size;
}
```

三个细节：

1. **`np` 是 pin 配置节点，不是控制器节点**——所以属性写在 `np->parent`（控制器是直接父）或 `np->parent->parent`（控制器是祖父）。注释 `:293-295` 写明了这个意图。
2. **`np->parent->parent` 没有 NULL 检查**。第一次读 `np->parent`（`devicetree.c:303`）是安全的——`of_property_read_u32()` 容忍 NULL 节点；但紧接着的 `np->parent->parent`（`:303-304`）直接解引用了 `np->parent`，没有检查。实践中调用者传进来的都是控制器的子节点，父是控制器、祖父是 `/bus` 或根，所以正常 DT 走不到；我**没有构造出能真的触发的 DTS**，所以它只是一处"缺检查"，不是"已知可崩"。已登记在 `99-evidence.md` §6 的 ⑧。
3. **元素个数 = `cells_size + 1`**——第一个 cell 永远是"控制器内部索引"，不算进 `#pinctrl-cells`。`devicetree.c:343` 的算式是 `*nr_elements = (size / sizeof(**list)) / (*cells_size + 1);`，注释 `:342`「First element is always the index within the pinctrl device」。

arm64 上的分布：**17 个文件、41 处**，全部集中在两家：

```bash
grep -rl '#pinctrl-cells' arch/arm64/boot/dts/ | wc -l     # 17
grep -rho '#pinctrl-cells' arch/arm64/boot/dts/ | wc -l    # 41
```

| 家族 | 文件 | 典型值 |
|---|---|---|
| TI K3（14 个 dtsi） | `ti/k3-am64-main.dtsi`、`k3-am62-main.dtsi`、`k3-j721e-main.dtsi`、… | `#pinctrl-cells = <1>` |
| HiSilicon（3 个 dtsi） | `hisilicon/hi6220.dtsi`、`hikey960-pinctrl.dtsi`、`hikey970-pinctrl.dtsi` | — |

TI K3 的完整形态（`arch/arm64/boot/dts/ti/k3-am64-main.dtsi:198-204`）：

```dts
main_pmx0: pinctrl@f4000 {
        compatible = "pinctrl-single";
        reg = <0x00 0xf4000 0x00 0x2d0>;
        #pinctrl-cells = <1>;
        pinctrl-single,register-width = <32>;
        pinctrl-single,function-mask = <0xffffffff>;
};
```

**消费者只有 3 个文件**（`pinctrl_count_index_with_args` / `pinctrl_parse_index_with_args` 的调用点，见 `04-extensions.md` §6.3）：

| 文件 | 读哪个属性 | arm64 用量 |
|---|---|---|
| `drivers/pinctrl/pinctrl-single.c:1011`、`:1030` | `pinctrl-single,pins`（属性名在 `:1006`） | **371 处** |
| `drivers/pinctrl/pinctrl-single.c:1126`、`:1158` | `pinctrl-single,bits`（`:1120`） | **0 处**——arm64 上完全没人用 |
| `drivers/pinctrl/ti/pinctrl-ti-iodelay.c:498`、`:528` | `pinctrl-pin-array`（`:491`） | **0 处** |

所以 arm64 上 `#pinctrl-cells` 的实际含义就是"`pinctrl-single,pins` 每行除索引外还有几个 u32"。`pinctrl-single` 自己的四个控制器级属性（`pinctrl-single.c:1804`/`:1812`/`:1824`/`:1830`）另见 §3.5。

### §2.2 `pinctrl-use-default`：全树零个 DTS 使用者，但机制是活的

这是本篇唯一一个**"核心真的会读、但整棵树没有一个 DTS 写它"**的属性：

```bash
grep -rn 'pinctrl-use-default' --exclude-dir=output .
# drivers/pinctrl/devicetree.c:127                              ← 消费者
# Documentation/devicetree/bindings/pinctrl/pinctrl.yaml:37     ← 绑定定义
# （arch/ 下 0 处）
```

按 skill 的口径，"找不到消费者的属性是死字"——这条**反过来**：有消费者、有绑定定义，**没有使用者**。所以它不是死字，是一个**给树外/厂商 DTS 预留的逃生口**。绑定文档的说明（`pinctrl.yaml:40-43`）：「Indicates that the OS can use the boot default pin configuration. This allows using an OS that does **not have a driver** for the pin controller. This property can be set either **globally for the pin controller** or in **child nodes** for individual pin group control.」

它改变什么——完整因果链，四步：

```
① devicetree.c:124-128   向上爬父节点找控制器的循环里，每一跳先看一眼
                         of_property_read_bool(np_pctldev, "pinctrl-use-default")
                         起点是 np_config 自己（:123），所以"pin 配置节点 / 中间层 /
                           控制器节点"任意一层写上都算 —— 与绑定文档的两种写法对应

② devicetree.c:130-137   爬到根节点还没找到已注册的 pin controller：
                           ret = -ENODEV;
                           if (IS_ENABLED(CONFIG_MODULES) && !allow_default && ret < 0)
                                   ret = -EPROBE_DEFER;      ← 写了本属性 → 跳过这一步
                         所以：写了 → -ENODEV；没写且 CONFIG_MODULES=y → -EPROBE_DEFER

③ devicetree.c:274-275   pinctrl_dt_to_map() 里 ret < 0 → goto err → 整个 create_pinctrl 失败
   core.c:1055-1058      → create_pinctrl() 返回 ERR_PTR(ret)

④ drivers/base/pinctrl.c 
   :32-36                devm_pinctrl_get() 失败 → dev_dbg "no pinctrl handle" + ret = 该错误码
   :97-98                if (ret == -EPROBE_DEFER) return ret;    ← 没写属性走这里，probe 无限推迟
   :100-101              if (ret == -EINVAL) return ret;
   :104                  return 0;                                ← -ENODEV 落到这里，被吞掉
```

**结论表**：

| 情形 | `allow_default` | `pinctrl_dt_to_map()` 返回 | `pinctrl_bind_pins()` 返回 | 设备能不能 probe |
|---|---|---|---|---|
| 控制器驱动已注册 | 无关 | 0 | 0 | 能，引脚按 DTS 配置 |
| 控制器无驱动 + 写了 `pinctrl-use-default` | true | `-ENODEV` | **0**（被 `:102` 的注释「We ignore errors like -ENOENT」那一段吞掉） | 能，**引脚沿用 bootloader 留下的状态** |
| 控制器无驱动 + 没写（`CONFIG_MODULES=y`） | false | `-EPROBE_DEFER` | `-EPROBE_DEFER` | **不能，永久停在 deferred 队列** |
| 控制器无驱动 + 没写（`CONFIG_MODULES=n`） | false | `-ENODEV` | 0 | 能（`IS_ENABLED(CONFIG_MODULES)` 为假，`:134`） |

第三行是 arm64 上的默认组合（defconfig `CONFIG_MODULES=y`），也是 §8 里"外设一直不 probe、没有任何 error 级日志"这个症状的头号原因。查法见 §6.3。

### §2.3 hog：控制器给自己配引脚，和 DT 的交叉点

hog 不是一个 DTS 属性，而是一条**判据**：map 条目的 `dev_name` 等于 pin controller 自己的 `dev_name()`（kerneldoc 在 `include/linux/pinctrl/machine.h:52-55`）。DT 路径同样会产生 hog——只要一个 pin controller 节点自己写了 `pinctrl-0` 指向自己的子节点。

DT 侧为此有两处特判：

| 位置 | 代码 | 为什么 |
|---|---|---|
| `devicetree.c:139-142` | `if (hog_pctldev && (np_pctldev == p->dev->of_node)) { pctldev = hog_pctldev; break; }` | 建 hog 时控制器还没进全局链表，`get_pinctrl_dev_from_of_node()` 查不到，只能用调用者传进来的那个 |
| `devicetree.c:151-154` | `if (np_pctldev == p->dev->of_node) { of_node_put(...); return 1; }`，注释「Do not defer probing of hogs (circular loop)」 | 返回 **1**（不是负值）让 `:272-273` 的 `continue` 跳过这一条 phandle，避免控制器等自己 |

hog 的实际生效点是 `pinctrl_claim_hogs()`（`core.c:2073-2109`），由 `pinctrl_enable()` 在 `:2115` 调用。找不到任何 hog 时只打 `dev_dbg "no hogs found"`（`:2077`）。

arm64 上 hog 很少见。要判断某块板子有没有用，最直接的办法是看 `/sys/kernel/debug/pinctrl/*/pinctrl-handles` 里 `dev_name` 和控制器同名的条目（见 `07-debug.md` §3）。

---

## §3 pin 配置节点内部：泛型帮助绑定

**本节的适用范围**：只对 `dt_node_to_map` 填了 `pinconf_generic_dt_node_to_map*` 三兄弟之一的驱动成立。全树分布（`grep -rn '\.dt_node_to_map[[:space:]]*=' drivers/pinctrl/ --include=*.c`）：

| `.dt_node_to_map` 填的是 | 个数 | §3 是否适用 |
|---|---|---|
| `pinconf_generic_dt_node_to_map_pin` | 25 | 适用，`type = CONFIGS_PIN` |
| `pinconf_generic_dt_node_to_map_all` | 20 | 适用，`type = INVALID`（自动推断） |
| `pinconf_generic_dt_node_to_map_group` | 15 | 适用，`type = CONFIGS_GROUP` |
| 驱动自己写的函数 | **38** 个不同函数（`mtk_pctrl_dt_node_to_map` 被两个 SoC 家族共用，其余各 1） | ✗ **不适用**——属性名完全由驱动定，去看该驱动的绑定文档 |

arm64 上要特别记住的三个"自己写"的大户：

| 驱动 | 属性形态 | arm64 用量 |
|---|---|---|
| `pinctrl-rockchip.c`（`rockchip_dt_node_to_map`） | `rockchip,pins = <bank pin func &pcfg_xxx>` | rockchip 全系 |
| `mediatek/pinctrl-paris.c:529`（`mtk_pctrl_dt_node_to_map`） | `pinmux = <PIN_FIELD(...)>` + `mediatek,*` 私有 config | mediatek 全系 |
| `pinctrl-single.c`（`pcs_dt_node_to_map`） | `pinctrl-single,pins = <offset value>` | 371 处，TI K3 |

反过来，**qcom TLMM 用的是泛型路径**（`drivers/pinctrl/qcom/pinctrl-msm.c:140` = `pinconf_generic_dt_node_to_map_group`），所以 §3 对 qcom 全系直接适用。

### §3.1 `pins` / `groups` / `function`：三者的判定顺序

消费代码 `pinconf_generic_dt_subnode_to_map()`（`drivers/pinctrl/pinconf-generic.c:294-377`）开头就是一个三层判定：

```c
const char *subnode_target_type = "pins";                  /* :307 默认 */

ret = of_property_count_strings(np, "pins");               /* :309 先试 pins */
if (ret < 0) {
        ret = of_property_count_strings(np, "groups");     /* :311 再试 groups */
        if (ret < 0)
                /* skip this node; may contain config child nodes */
                return 0;                                  /* :314 两个都没有 → 静默返回 0 */
        if (type == PIN_MAP_TYPE_INVALID)
                type = PIN_MAP_TYPE_CONFIGS_GROUP;         /* :316 */
        subnode_target_type = "groups";                    /* :317 */
} else {
        if (type == PIN_MAP_TYPE_INVALID)
                type = PIN_MAP_TYPE_CONFIGS_PIN;           /* :320 */
}
strings_count = ret;                                       /* :322 */
```

| 属性 | 类型 | 消费代码 | 语义 |
|---|---|---|---|
| `pins` | 字符串列表 | `pinconf-generic.c:309` 计数，`:353` `of_property_for_each_string` 逐个取 | 名字必须能被 `pin_get_from_name()` 匹配上（`core.c` 里遍历 `pctldev->desc->pins[].name`） |
| `groups` | 字符串列表 | `:311` 计数，`:353` 同一个循环 | 名字必须能被驱动的 `get_group_name` 回调返回 |
| `function` | 单个字符串 | `:324` `of_property_read_string(np, "function", &function)` | 有它才生成 mux map（`:355-360`）；**可选**，`:326-331` 明确「EINVAL=missing, which is fine since it's optional」 |

**`pins` 优先于 `groups`**：两个都写了，`groups` 被完全忽略（`:309` 成功就走 `else` 分支，`subnode_target_type` 保持 `"pins"`）。**没有任何警告**。

**`function` 读失败但不是 `-EINVAL` 时才报错**（`:327-330`）：

```c
if (ret != -EINVAL)
        dev_err(dev, "%pOF: could not parse property function\n", np);
function = NULL;
```

也就是说 `function = <5>;`（写成整数而不是字符串）会得到 `-EILSEQ` 或 `-ENODATA` → **打一条 dev_err，然后照样继续把 function 当成没写**。返回值不受影响。

### §3.2 28 条标准 config 属性总表

来源是 `dt_params[]`（`drivers/pinctrl/pinconf-generic.c:160-189`），共 **28** 条。解析函数是 `parse_dt_cfg()`（`:204-230`），对每条属性跑 `of_property_read_u32(np, par->property, &val)`。

| DTS 属性 | 映射到的 `PIN_CONFIG_*` | 无值时的默认参数 | 备注 |
|---|---|---|---|
| `bias-bus-hold` | `BIAS_BUS_HOLD` | 0 | |
| `bias-disable` | `BIAS_DISABLE` | 0 | |
| `bias-high-impedance` | `BIAS_HIGH_IMPEDANCE` | 0 | |
| `bias-pull-up` | `BIAS_PULL_UP` | **1** | 带值时值是上拉强度（单位由驱动定） |
| `bias-pull-pin-default` | `BIAS_PULL_PIN_DEFAULT` | **1** | |
| `bias-pull-down` | `BIAS_PULL_DOWN` | **1** | |
| `drive-open-drain` | `DRIVE_OPEN_DRAIN` | 0 | |
| `drive-open-source` | `DRIVE_OPEN_SOURCE` | 0 | |
| `drive-push-pull` | `DRIVE_PUSH_PULL` | 0 | |
| `drive-strength` | `DRIVE_STRENGTH` | 0 | 单位由驱动定（qcom 是 mA 档位） |
| `drive-strength-microamp` | `DRIVE_STRENGTH_UA` | 0 | 与上一条是**不同的参数号**，不能混用 |
| `input-debounce` | `INPUT_DEBOUNCE` | 0 | 单位 μs |
| `input-disable` | `INPUT_ENABLE` | **0** | 同一个参数号，靠 argument 区分 |
| `input-enable` | `INPUT_ENABLE` | **1** | 见 §3.4 |
| `input-schmitt` | `INPUT_SCHMITT` | 0 | 带值 = 触发阈值 |
| `input-schmitt-disable` | `INPUT_SCHMITT_ENABLE` | **0** | |
| `input-schmitt-enable` | `INPUT_SCHMITT_ENABLE` | **1** | |
| `low-power-disable` | `MODE_LOW_POWER` | **0** | |
| `low-power-enable` | `MODE_LOW_POWER` | **1** | |
| `output-disable` | `OUTPUT_ENABLE` | **0** | |
| `output-enable` | `OUTPUT_ENABLE` | **1** | |
| `output-high` | `OUTPUT` | **1** | |
| `output-impedance-ohms` | `OUTPUT_IMPEDANCE_OHMS` | 0 | |
| `output-low` | `OUTPUT` | **0** | |
| `power-source` | `POWER_SOURCE` | 0 | |
| `sleep-hardware-state` | `SLEEP_HARDWARE_STATE` | 0 | |
| `slew-rate` | `SLEW_RATE` | 0 | |
| `skew-delay` | `SKEW_DELAY` | 0 | |

重跑（表和条数）：
```bash
sed -n '160,189p' drivers/pinctrl/pinconf-generic.c
sed -n '160,189p' drivers/pinctrl/pinconf-generic.c | grep -c '^\s*{ "'   # 28
```

### §3.3 五对 enable/disable 属性：靠 argument 区分，不靠参数号

上表里标了 的 11 行凑成 **5 对 + 1 对特殊**：

| 参数号 | "开"属性 | "关"属性 |
|---|---|---|
| `PIN_CONFIG_INPUT_ENABLE` | `input-enable`（arg=1） | `input-disable`（arg=0） |
| `PIN_CONFIG_INPUT_SCHMITT_ENABLE` | `input-schmitt-enable`（arg=1） | `input-schmitt-disable`（arg=0） |
| `PIN_CONFIG_MODE_LOW_POWER` | `low-power-enable`（arg=1） | `low-power-disable`（arg=0） |
| `PIN_CONFIG_OUTPUT_ENABLE` | `output-enable`（arg=1） | `output-disable`（arg=0） |
| `PIN_CONFIG_OUTPUT` | `output-high`（arg=1） | `output-low`（arg=0） |

**这是一个真实的静默失效源**：驱动的 `pin_config_set` 如果只看 `param` 不看 `argument`，那么 `input-disable` 会被当成 `input-enable` 执行——DTS 写"关"，硬件被"开"，**没有任何日志**。判断某个驱动有没有这个毛病的办法：

```bash
# 在驱动里找 case PIN_CONFIG_INPUT_ENABLE:，看它有没有取 arg
sed -n '/case PIN_CONFIG_INPUT_ENABLE:/,/break;/p' drivers/pinctrl/<驱动>.c
# 取 arg 的正确写法长这样：
#   arg = pinconf_to_config_argument(configs[i]);
```

还有一条**不成对**的：`output-high` / `output-low` 用的是 `PIN_CONFIG_OUTPUT`，而 `output-enable` / `output-disable` 用的是 `PIN_CONFIG_OUTPUT_ENABLE`。两组是**不同参数**——`output-high` 隐含"使能输出并驱动为高"，`output-enable` 只是"使能输出方向"。写错这一对是另一类常见错误。

`PIN_CONFIG_PERSIST_STATE` 这个参数**不在 `dt_params[]` 里**，也不在 debugfs 的 `conf_items[]` 里，所以 DTS 无法设置它、debugfs 也看不到它。详见 `07-debug.md` §3。

### §3.4 节点形态：平铺 / 一层子节点 / 混合

`pinconf_generic_dt_node_to_map()`（`pinconf-generic.c:378-409`）对**节点自己 + 每个可用子节点**各调一次 `dt_subnode_to_map()`：

```c
ret = pinconf_generic_dt_subnode_to_map(pctldev, np_config, map,
                                        &reserved_maps, num_maps, type);   /* :387 节点自己 */
if (ret < 0)
        goto exit;

for_each_available_child_of_node(np_config, np) {                          /* :395 每个子节点 */
        ret = pinconf_generic_dt_subnode_to_map(pctldev, np, map,
                                &reserved_maps, num_maps, type);
```

所以三种写法都合法：

```dts
/* 形态 A：平铺 —— 属性直接写在被 phandle 引用的节点上 */
serial_4_pins: serial4-pinmux {
        pins = "gpio23", "gpio24";
        function = "blsp4_uart1";
        drive-strength = <8>;
        bias-disable;
};

/* 形态 B：一层子节点 —— 引用的节点本身没有 pins/groups，只当容器 */
qup_i2c0_default: qup-i2c0-default {
        pinmux {
                pins = "gpio34", "gpio35";
                function = "qup00";
        };
};

/* 形态 C：混合 —— 节点自己有 pins，子节点还有更多组 */
```

形态 B 能工作的原因正是 `:314` 那个 `return 0`：容器节点自己没有 `pins` 也没有 `groups`，`dt_subnode_to_map` **静默返回 0**（注释 `:313`「skip this node; may contain config child nodes」），然后 `:395` 的循环去处理子节点。

**这个 `return 0` 也是最大的拼写错误陷阱**：把 `pins` 写成 `pin`、`pinsx`、`gpios`，节点就变成了"一个没有子节点的空容器" → 返回 0 → 一个 map 都不生成 → **没有任何错误**。见 §5 决策树。

`for_each_available_child_of_node` 还意味着 **`status = "disabled"` 的子节点被跳过**——这可以当特性用（在 board dts 里禁掉某个引脚组），也可能是意外。

### §3.5 厂商私有 config 属性从哪来

两条完全不同的来路，别搞混：

| 来路 | 属性名形态 | 机制 | 详见 |
|---|---|---|---|
| ① 注册进 `pinctrl_desc.custom_params` | 通常带厂商前缀（`mediatek,tdsel`、`starfive,strong-pull-up`、`qcom,*`） | 走**同一个** `parse_dt_cfg()`，行为与 §3.2 的标准属性完全一致 | `04-extensions.md` §3.8（含 16 个驱动的点名表） |
| ② 驱动自己写 `dt_node_to_map` 手工解析 | 什么形态都可能（`rockchip,pins`、`pinctrl-single,pins`、`brcm,pull`） | 与泛型路径无关，`grep` 驱动源码是唯一办法 | 各驱动绑定文档 |

`pinctrl-single` 的四个**控制器级**属性属于②，全部在 `pcs_probe()` 里读：

| 属性 | 消费代码 | 写在哪 |
|---|---|---|
| `pinctrl-single,register-width` | `pinctrl-single.c:1804-1805` | 控制器节点 |
| `pinctrl-single,function-mask` | `pinctrl-single.c:1812-1813` | 控制器节点 |
| `pinctrl-single,function-off` | `pinctrl-single.c:1824-1825` | 控制器节点 |
| `pinctrl-single,bit-per-mux` | `pinctrl-single.c:1830`（`of_property_read_bool`） | 控制器节点 |
| `pinctrl-single,gpio-range` | `pinctrl-single.c:1342`（属性名） | 控制器节点，arm64 上 **17** 处 |

另外 `pinctrl-single.c:924-936` 还有两张自己的 config 属性表（`prop2[]` 在 `:924-930`，`prop4[]` 在 `:931-936`）（`pinctrl-single,drive-strength` / `slew-rate` / `input-enable` / `input-schmitt` / `low-power-mode` / `bias-pullup` / `bias-pulldown` / `input-schmitt-enable`）——注意这些名字**带 `pinctrl-single,` 前缀**，与 §3.2 的标准名不是同一批，写成标准名不会生效。

---

## §4 arm64 上的五个真实例子

全部取自本树 `arch/arm64/boot/dts/`，可以直接 `sed -n` 打开对照。按"形态复杂度"递增排列。

### §4.1 最简：平铺泛型 pinconf（qcom ipq8074）

`arch/arm64/boot/dts/qcom/ipq8074.dtsi:311-316`：

```dts
serial_4_pins: serial4-state {
        pins = "gpio23", "gpio24";
        function = "blsp4_uart1";
        drive-strength = <8>;
        bias-disable;
};
```

消费者侧（同一棵树里引用它的节点）写 `pinctrl-0 = <&serial_4_pins>; pinctrl-names = "default";`。

生成的 map（`pinconf_generic_dt_subnode_to_map` 走完 `:353-365` 的循环后）：

| # | 类型 | 内容 |
|---|---|---|
| 1 | `PIN_MAP_TYPE_MUX_GROUP` | group=`gpio23`, function=`blsp4_uart1` |
| 2 | `PIN_MAP_TYPE_CONFIGS_GROUP` | group=`gpio23`, configs=[DRIVE_STRENGTH=8, BIAS_DISABLE=0] |
| 3 | `PIN_MAP_TYPE_MUX_GROUP` | group=`gpio24`, function=`blsp4_uart1` |
| 4 | `PIN_MAP_TYPE_CONFIGS_GROUP` | group=`gpio24`, configs=[同上] |

每个字符串生成 **2** 条 map（mux + configs），这正是 `:340-346` 那段 `reserve` 计算的来源。类型是 `CONFIGS_GROUP` 而不是 `CONFIGS_PIN`——尽管 DTS 写的是 `pins`：因为 qcom 的 `.dt_node_to_map` 是 `pinconf_generic_dt_node_to_map_group`（`qcom/pinctrl-msm.c:140`），`type` 进来时就是 `CONFIGS_GROUP`，`:320` 的推断分支不生效。qcom TLMM 把每个 pin 同时注册成一个同名单 pin group，所以两种写法在它上面等价。

### §4.2 一层子节点（qcom sc7180）

`arch/arm64/boot/dts/qcom/sc7180.dtsi:1531-1536`：

```dts
qup_i2c0_default: qup-i2c0-default-state {
        pinmux {
                pins = "gpio34", "gpio35";
                function = "qup00";
        };
};
```

消费者在 `:777-778`：

```dts
pinctrl-names = "default";
pinctrl-0 = <&qup_i2c0_default>;
```

子节点名 `pinmux` **没有任何语义**——核心不看名字，只看有没有 `pins`/`groups`（§3.4）。叫 `foo` 也一样工作。绑定文档里之所以约定用 `pinmux`/`pinconf` 这两个名字，纯粹是可读性。

### §4.3 default + sleep 两个状态，且 sleep 是空状态（qcom sc7280）

`arch/arm64/boot/dts/qcom/sc7280.dtsi:863-868`：

```dts
pinctrl-names = "default", "sleep";
pinctrl-0 = <&sdc1_clk>, <&sdc1_cmd>, <&sdc1_data>, <&sdc1_rclk>;
pinctrl-1 = <&sdc1_clk_sleep>, <&sdc1_cmd_sleep>, <&sdc1_data_sleep>,
            <&sdc1_rclk_sleep>;
```

而被引用的 `sdc1_clk`（`:5011-5013`）长这样：

```dts
sdc1_clk: sdc1-clk-state {
        pins = "sdc1_clk";
};
```

**只有 `pins`，没有 `function`，也没有任何 config 属性。** 走一遍 `pinconf_generic_dt_subnode_to_map()`：

```
:309  of_property_count_strings(np, "pins")   → 1，走 else 分支
:324  of_property_read_string(np, "function") → -EINVAL，function = NULL（不报错）
:332  pinconf_generic_parse_dt_config()       → num_configs = 0
:340  if (function != NULL) reserve++;        → 不成立，reserve 保持 0
:342  if (num_configs)      reserve++;        → 不成立，reserve 保持 0
:344  reserve *= strings_count;               → 0 * 1 = 0
:348  pinctrl_utils_reserve_map(..., 0)       → 什么都不分配
:353  循环里 reserve 用完了，add_map_* 一次都不调
      → *num_maps 保持 0
```

于是 `pinctrl_dt_to_map()` 走到 `:171-180`：

```c
if (num_maps == 0) {
        dev_info(p->dev, "there is not valid maps for state %s\n", statename);
        ...
        return 0;                    /* :179 */
}
```

结果：**一条 `dev_info`（不是 error），状态注册成 0 条 map，`pinctrl_select_state()` 会成功但什么都不做**。这是本树里一个**故意**的写法——`sdhc_1` 在这个 dtsi 里是 `status = "disabled"`，具体板子的 dts 才会填真正的 mux 配置；这些"只声明 pin 归属"的节点存在的意义是让板级 dts 能 override。

读者要记住的判据：`dev_info "there is not valid maps for state ..."` **既可能是故意的、也可能是拼错了 `function`/config 属性名**。区分办法见 §5 决策树。

### §4.4 三个状态 + 厂商私有绑定（rockchip rk3399）

`arch/arm64/boot/dts/rockchip/rk3399.dtsi:889-892`：

```dts
pinctrl-names = "init", "default", "sleep";
pinctrl-0 = <&sdmmc_pwr>;              /* init */
pinctrl-1 = <&sdmmc_pwr>;              /* default */
pinctrl-2 = <&sdmmc_pwr_sleep>;        /* sleep */
```

被引用的节点（`:2709-2715` 附近）用的是 rockchip 私有格式：

```dts
sdmmc_pwr: sdmmc-pwr {
        rockchip,pins = <0 RK_PA1 RK_FUNC_GPIO &pcfg_pull_none>;
};
```

`rockchip,pins` 的四个 cell 是 `<bank pin func &pcfg_phandle>`，由 `rockchip_dt_node_to_map()` 手工解析——§3 的 28 条标准属性**在这里一条都不适用**。配置来自最后那个 phandle 指向的 `pcfg_*` 节点（`pcfg_pull_none` / `pcfg_pull_up` / `pcfg_output_high` …），这些节点里写的**才是** §3.2 的标准属性名。所以 rockchip 是一种"两级"结构：mux 走私有格式，config 走标准格式。

`init` 状态：全 arm64 只有 **5** 处使用，全部是 rockchip。它由 `really_probe()` 在调用驱动 `.probe` **之前**应用（见 §1.4），目的是避免"先用 default 配置、驱动再改"造成的引脚抖动（`include/linux/pinctrl/pinctrl-state.h:16-22` 的 kerneldoc 明说是为了不 "glitch the pins"）。

### §4.5 `pinctrl-single` + `#pinctrl-cells`（TI K3）

`arch/arm64/boot/dts/ti/k3-am64-main.dtsi:198-204` 是控制器侧（已在 §2.1 引过）。消费者侧的引脚节点形如：

```dts
main_uart0_pins_default: main-uart0-pins-default {
        pinctrl-single,pins = <
                AM64X_IOPAD(0x0230, PIN_INPUT, 0)   /* (D15) UART0_RXD */
                AM64X_IOPAD(0x0234, PIN_OUTPUT, 0)  /* (C16) UART0_TXD */
        >;
};
```

`AM64X_IOPAD(pa, val, muxmode)` 是 `arch/arm64/boot/dts/ti/k3-pinctrl.h` 里的宏，展开成 **2 个 cell**：偏移 + 值。这就是为什么 `#pinctrl-cells = <1>`——第一个 cell 是索引（偏移），剩 1 个 cell 是数据（值）。

这条路径完全绕开 pinconf-generic：没有 `pins`、没有 `function`、没有 `bias-*`。所有信息压在那个 32 位值里（`PIN_INPUT`/`PIN_OUTPUT`/`PIN_PULLUP` 等宏按位或）。**不要在 `pinctrl-single,pins` 节点里写 `drive-strength`——它会被完全忽略且没有警告。**

---

## §5 静默失效决策树

pinctrl 的 DT 路径**大量使用"返回 0 表示什么都没做"**，所以"配了但没生效"是最常见的故障形态，而且**日志里通常一个 error 都没有**。下面这棵树按"从最外层往里"排，每一步都给了判据命令。

```
症状：DTS 写了引脚配置，但硬件行为不对（信号不出来 / 上下拉不对 / 驱动能力不对）

├─① 外设驱动 probe 了吗？
│   判据：ls /sys/bus/platform/drivers/<驱动>/ | grep <设备名>
│         或 grep <设备名> /sys/kernel/debug/devices_deferred
│   └─ 没 probe，且在 devices_deferred 里
│       → 大概率是 pin controller 驱动没编进内核 → -EPROBE_DEFER
│          （§2.2 结论表第 3 行）。日志里 **没有** error，因为
│          drivers/base/pinctrl.c:32-36 只打 dev_dbg
│       判据：dmesg | grep -i 'pinctrl\|tlmm\|pinmux' 看控制器有没有注册
│             ls /sys/kernel/debug/pinctrl/    ← 目录为空就是控制器没起来
│
├─② 状态被应用了吗？
│   判据：cat /sys/kernel/debug/pinctrl/pinctrl-handles | grep -A3 <设备名>
│         看有没有你的设备，以及 "state:" 后面是不是你期望的那个名字
│   ├─ 设备完全不在 pinctrl-handles 里
│   │   → devm_pinctrl_get() 就没成功，回到 ①
│   ├─ 在，但 state 是空 / 不是 default
│   │   ├─ 你用的是 sleep / idle？
│   │   │   → 这两个名字核心**只缓存不应用**（§1.4）。
│   │   │      pinctrl_bind_pins() 只在 drivers/base/pinctrl.c:64-81 查了一下
│   │   │      存不存在，真正切换要驱动自己调 pinctrl_pm_select_sleep_state()。
│   │   │      判据：grep -n 'pinctrl_pm_select' drivers/<你的驱动>.c
│   │   │            空结果 = 这个状态永远不会被应用
│   │   └─ 你用的是自定义状态名？
│   │       → 必须驱动自己 pinctrl_lookup_state() + pinctrl_select_state()
│   └─ 在，且 state = default
│       → 继续 ③
│
├─③ 这个状态里有几条 map？
│   判据：cat /sys/kernel/debug/pinctrl/pinctrl-maps | grep -B2 -A6 <设备名>
│   ├─ 0 条（同时 dmesg 里有 "there is not valid maps for state default"）
│   │   → 四种可能，按概率排：
│   │      a) 属性名拼错（pins 写成 pin/gpios、function 写成 func）
│   │         → pinconf-generic.c:314 静默 return 0
│   │         判据：把节点 dump 出来逐字比对
│   │              cat /sys/firmware/devicetree/base/.../<节点名>/pins | tr -d '\0'
│   │      b) 只写了 pins、没写 function 也没写 config
│   │         → reserve = 0（§4.3 完整推演）。可能是**故意的**
│   │      c) 节点是纯容器但子节点全被 status="disabled" 掉了
│   │         → pinconf-generic.c:395 的 for_each_available_child_of_node 跳过
│   │      d) 驱动的 .dt_node_to_map 是自己写的，属性名不是 pins/function
│   │         → 判据：grep -n 'dt_node_to_map' drivers/pinctrl/<你的驱动>.c
│   │                看它填的是 pinconf_generic_* 还是自己的函数（§3 开篇表）
│   └─ 有 map
│       → 继续 ④
│
├─④ mux 生效了吗？
│   判据：cat /sys/kernel/debug/pinctrl/<控制器>/pinmux-pins | grep <pin 名>
│         输出格式取决于驱动的 pmxops->strict（29 个驱动填 true，qcom/rockchip/
│           mediatek-paris/pinctrl-single 都是 false），两种形态见 07-debug.md §3.5：
│           非 strict：pin 34 (gpio34): 1f00000.qup_i2c (GPIO UNCLAIMED) function qup00 group gpio34
│           strict  ：pin 34 (gpio34): device 1f00000.qup_i2c function qup00 group gpio34
│   ├─ 尾部没有 "function ... group ..."
│   │   → desc->mux_setting == NULL → map 里没有 MUX_GROUP 条目
│   │      → 你没写 function（回到 ③b）
│   └─ mux owner 是另一个设备名
│       → 引脚冲突。pin_request() 会拒绝并打：
│          dmesg | grep 'already requested'
│          （`pinmux.c:130`/`:137`，机制见 03-internals.md §A11.2）
│
└─⑤ config 生效了吗？
    判据：cat /sys/kernel/debug/pinctrl/<控制器>/pinconf-pins | grep <pin 名>
    注意这一步的 oracle 有三重不可靠，看 07-debug.md §3：
       - 显示的是**驱动 pin_config_get 回读的硬件值**，不是 DTS 写的值；
         驱动没实现 pin_config_get → 显示 "N/A"，不代表没配上
       - PIN_CONFIG_PERSIST_STATE 不在 conf_items[] 里，永远不显示
       - 厂商私有参数只有注册了 custom_conf_items 才显示名字（16 个驱动里只有 11 个填了，
         见 04-extensions.md §3.8.4）
    ├─ 值不对，方向反了（该关的开了 / 该开的关了）
    │   → §3.3：input-disable / output-disable / low-power-disable /
    │      input-schmitt-disable 这四个和它们的 enable 兄弟**共用同一个参数号**，
    │      只差 argument。驱动没读 argument 就会反。
    │      判据：sed -n '/case PIN_CONFIG_INPUT_ENABLE:/,/break;/p' \
    │              drivers/pinctrl/<驱动>.c | grep -c pinconf_to_config_argument
    │            结果 0 = 这个驱动确实不看 argument
    ├─ 值被截断了（写 <0x2000000> 只生效了低位）
    │   → argument 字段只有 24 位（PIN_CONF_PACKED，见 01-api.md §3）
    ├─ 属性写了但完全没反应
    │   ├─ 用的是标准名，但驱动只认厂商私有名？
    │   │   → 判据：grep -n 'custom_params' drivers/pinctrl/<驱动>.c
    │   │          再看它的绑定文档 Documentation/devicetree/bindings/pinctrl/<驱动>*
    │   ├─ 用的是 drive-strength，驱动只支持 drive-strength-microamp？
    │   │   → 这是两个不同参数号（§3.2），互不替代
    │   └─ 属性值写成了非 u32（如字符串、64 位）？
    │       → of_property_read_u32 返回 -EOVERFLOW 而不是 -EINVAL，
    │          parse_dt_cfg()（pinconf-generic.c:219-224）只把 -EINVAL 当"属性不存在"，
    │          其他错误码会**落到 default_value 分支**——静默用了默认值
    └─ 值对，硬件还是不对
        → 出 pinctrl 范围了。查 pin 到 SoC 引脚的映射（datasheet）、
           查有没有 bootloader/TF-A 在更早阶段锁了寄存器
```

---

## §6 定位手段：编译期 / 运行期静态 / 运行期动态

### §6.1 编译期（不开机就能查）

| 想确认什么 | 命令 | 判据 |
|---|---|---|
| 我的属性有没有消费者 | `grep -rn '"<属性名>"' drivers/pinctrl/ include/linux/pinctrl/` | 空结果 = 死字，DTS 里写了也没人读 |
| 我的驱动走不走泛型路径 | `grep -n '\.dt_node_to_map' drivers/pinctrl/<驱动>.c` | `pinconf_generic_*` = §3 适用；自己的函数 = 不适用 |
| 驱动认哪些厂商私有属性 | `grep -n 'custom_params\|pinconf_generic_params' drivers/pinctrl/<驱动>.c` | 顺着数组定义读属性名 |
| 驱动实现了哪些 config 参数 | `sed -n '/pin_config_set/,/^}/p' drivers/pinctrl/<驱动>.c \| grep 'case PIN_CONFIG'` | 不在这张列表里的参数 = 驱动会返回 `-ENOTSUPP` 或静默忽略 |
| DTS 编译后属性到底是什么 | `dtc -I dtb -O dts <你的>.dtb \| sed -n '/<节点名>/,/};/p'` | 排除 include/宏展开导致的意外 |
| `#pinctrl-cells` 要几个 | `grep -n 'pinctrl-single,pins\|#pinctrl-cells' <你的>.dts` | 见 §2.1 的 `cells_size + 1` 规则 |

最高性价比的一条：**属性名的 `grep` 反查**。pinctrl 的 DT 层没有任何"未知属性"告警，拼错和用了不支持的属性完全等价于"没写"。

### §6.1.1 用 DT schema 做编译期校验（`make dt_binding_check` / `dtbs_check`）

上面那张表全是"手工 grep"。本树还提供**三份 YAML schema**，可以让 `dtc` 帮你查一部分语法错误。三者分工：

| schema 文件（`Documentation/devicetree/bindings/pinctrl/` 下） | 管哪一类节点 | 实际定义了什么 |
|---|---|---|
| `pinctrl.yaml`（45 行） | **控制器**节点 | 只有 3 条：节点名必须匹配 `^(pinctrl\|pinmux)(@[0-9a-f]+)?$`（`:29-30`）、`#pinctrl-cells`（`:32-35`）、`pinctrl-use-default`（`:37-43`）。`:45` 是 `additionalProperties: true`——**控制器节点上写任何别的属性都不会被 schema 拦住** |
| `pinmux-node.yaml`（133 行） | **复用**节点 | 5 个属性：`function`（`:104`）、`pins`（`:108`）、`groups`（`:117`）、`pinmux`（`:124`）、`pinctrl-pin-array`（`:130`）。`:12-101` 的 description 里有三种写法的官方示例（子节点式、`pinctrl-pin-array` 式、`pinmux` 式） |
| `pincfg-node.yaml`（153 行） | **配置**节点 | 27 个通用电气属性（`bias-*` 6 个、`drive-*` 5 个、`input-*` 5 个、`output-*` 5 个，加 `power-source`/`low-power-*`/`sleep-hardware-state`/`slew-rate`/`skew-delay`），逐个见 `:21-151` |

跑法：

```bash
# 校验 binding schema 自身的写法（改了绑定文档时用）
make dt_binding_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/pinctrl/

# 用 schema 校验真实的板级 DTS/DTB
make dtbs_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/pinctrl/
```

**schema 能查什么、不能查什么**——这一条比命令本身重要：

| schema 会拦 | schema 不会拦（本篇 §5 那棵决策树覆盖的全是这一列） |
|---|---|
| 属性类型错（该是数组写成字符串） | 属性名**拼错**——多数 pinctrl 绑定都是 `additionalProperties: true`，多写的属性被当成"控制器私有"放过 |
| 控制器节点名不符合 `pinctrl`/`pinmux` 命名 | `function = "xxx"` 的 `xxx` 是不是控制器真的注册过的功能名 |
| 明确枚举了取值范围的属性写越界 | `groups`/`pins` 的名字对不对（写错会静默回退到 `groups[0]`，见 §5） |
| 缺必填属性（当 binding 里写了 `required:`） | `drive-strength` 的数值硬件支不支持——驱动不检查就静默接受 |

**结论：schema 是语法层的第一道门，不是语义层的门。** pinctrl 的绝大多数配错都是语义错，只能靠 §6.2 的 debugfs 和 §5 的决策树抓。

### §6.2 运行期静态（开机后一次性看）

| 文件 | 看什么 | 关键判据 |
|---|---|---|
| `/sys/kernel/debug/pinctrl/pinctrl-maps` | 所有 map 条目（DT + 板级静态合起来） | 你的设备一条都没有 → §5 ③ |
| `/sys/kernel/debug/pinctrl/pinctrl-handles` | 每个设备持有的 `struct pinctrl` 和当前 state | state 名对不上 → §5 ② |
| `/sys/kernel/debug/pinctrl/pinctrl-devices` | 已注册的控制器，及各自支持 pinmux / pinconf | 控制器不在这里 = 驱动没起来（§5 ①） |
| `/sys/kernel/debug/pinctrl/<控制器>/pins` | 控制器声明的所有 pin 及名字 | 你 DTS 里写的 pin 名必须在这里出现，否则 `pin_get_from_name()` 失败 |
| `/sys/kernel/debug/pinctrl/<控制器>/pingroups` | 所有 group 及成员 pin | 用 `groups=` 写法时必须在这里能找到 |
| `/sys/kernel/debug/pinctrl/<控制器>/pinmux-functions` | 所有 function 及可用 group | `function=` 的值必须在这里 |
| `/sys/firmware/devicetree/base/…` | 内核**实际看到的** DT，不是你以为的那份 | `tr -d '\0'` 后逐字比对属性名 |
| `/sys/kernel/debug/devices_deferred` | 卡在 `-EPROBE_DEFER` 的设备 | 命中 = §5 ① |

完整的 debugfs 语义、每个文件的 seq_file 实现位置、以及"哪些文件可写"，见 `07-debug.md` §3。

### §6.3 运行期动态（改状态 / 追踪）

| 手段 | 做法 | 用途 |
|---|---|---|
| 提高 pinctrl 日志级别 | `echo 8 > /proc/sys/kernel/printk` + `echo 'file drivers/pinctrl/* +p' > /sys/kernel/debug/dynamic_debug/control` | 打开所有 `dev_dbg`——`core.c:1222` 的 "using pinctrl dummy state"、`:1136` 的 "obtain a copy of previously claimed pinctrl"、`drivers/base/pinctrl.c:34` 的 "no pinctrl handle" 全在 dbg 级 |
| 只开 DT 层 | `echo 'file drivers/pinctrl/devicetree.c +p' > /sys/kernel/debug/dynamic_debug/control` | 定位 §5 ③ |
| ftrace 函数图 | `echo pinctrl_dt_to_map > set_graph_function; echo function_graph > current_tracer` | 看爬父节点的循环实际走了几跳 |
| kprobe 看返回值 | `perf probe 'pinctrl_dt_to_map%return $retval'` | 区分 `-ENODEV` / `-EPROBE_DEFER`（§2.2） |
| 手工切状态 | 没有通用 sysfs 接口 | pinctrl **不提供**"从用户态切换某个设备的 state"的口子；只能改 DTS 重启，或用 `pinctrl-single` 的 debugfs（若驱动提供） |

pinctrl 核心**没有 tracepoint**（六个核心文件里 `grep -n 'TRACE_EVENT\|trace_'` 一处都没有；全 `drivers/pinctrl/` 只有 2 个驱动命中，全是 `mips_trace_clk` / `arm_trace_pins` 这种 JTAG/ETM「trace 引脚组」的名字，与 ftrace 无关），所以动态追踪只能靠 dynamic_debug + ftrace/kprobe。这一点和 i2c/spi 不同，详见 `07-debug.md` §2。

---

## §7 错误消息 → 原因映射表

所有字符串都可以在源码里 `grep` 到，按"看到这条日志该怀疑什么"排序。

| 日志（原文片段） | 级别 | 出处 | 真实原因 |
|---|---|---|---|
| `there is not valid maps for state %s` | **info** | `devicetree.c:174` | 该状态生成了 0 条 map。故意的空状态，或属性名拼错。见 §5 ③ |
| `pctldev %s doesn't support DT` | error | `devicetree.c:164` | 控制器驱动没填 `.dt_node_to_map`，纯板级静态 map 驱动。返回 `-ENODEV` |
| `%pOF: could not parse property function` | error | `pinconf-generic.c:328` | `function` 写成了非字符串（整数/数组）。**不影响返回值**，只是 function 被当成没写 |
| `%pOF: could not parse node property` | error | `pinconf-generic.c:335` | `pinconf_generic_parse_dt_config()` 失败——通常是 `kcalloc` 失败，属性值格式错**不**走这里（见 §5 ⑤ 最后一条） |
| `no pinctrl handle` | **debug** | `drivers/base/pinctrl.c:34` | `devm_pinctrl_get()` 失败。**默认看不到**，要开 dynamic_debug |
| `no default pinctrl state` | **debug** | `drivers/base/pinctrl.c:44` | 没有名为 `default` 的状态。**不是错误**——设备照样 probe |
| `failed to activate default pinctrl state` | error | `core.c:1557` | 状态存在但应用失败——真正的硬件/冲突错误 |
| `failed to activate pinctrl state %s` | error | `core.c:1572` | 同上，非 default 状态 |
| `Error applying setting, reverse things back` | error | `core.c:1321` | 一条 setting 应用失败，核心正在回滚已应用的部分。前面通常还有一条更具体的错误 |
| `unknown pinctrl device %s in map entry, deferring probe` | info | `core.c:987` | map 里的 `ctrl_dev_name` 找不到对应控制器 → `-EPROBE_DEFER`。板级静态 map 路径专属 |
| `does not have pin group %s` | error | `core.c:743` | DTS 里的 `groups=` 值不在控制器的 pingroups 里。查 §6.2 的 `pingroups` |
| `%s could not find pingroup%i` | error | `core.c:574` | 同类，出现在 pin 名解析路径 |
| `using pinctrl dummy state (%s)` | **debug** | `core.c:1222` | 空 `pinctrl-N = <>`（§1.5）。arm64 上 0 个使用者，看到它说明你在用树外 DTS |
| `no hogs found` | **debug** | `core.c:2077` | 控制器自己没有 hog。正常情况 |
| `pin %s already requested by %s; cannot claim for %s` | error | `pinmux.c:130` 与 `:137`（两处同串：`:130` 是 `mux_owner` 冲突，`:137` 是 `gpio_owner` 冲突） | 引脚冲突，两个设备抢同一个 pin。本篇 §5 第 ④ 步；机制见 `03-internals.md` §A11.2 |

**三条 debug 级的日志（`no pinctrl handle` / `no default pinctrl state` / `using pinctrl dummy state`）是排查 DT 问题时最有价值的，但默认全部不可见**。所以定位 pinctrl 的 DT 问题，**第一个动作就应该是 §6.3 的第一行**——不然你面对的是一片安静。

---

## §8 症状清单：按"坏掉的范围"分组

### §8.1 控制器级（整个 pin controller 都不工作）

| 症状 | 首查 | 常见原因 |
|---|---|---|
| `/sys/kernel/debug/pinctrl/` 是空的 | `dmesg \| grep -i pinctrl` | 控制器驱动没编进内核 / `compatible` 不匹配 |
| 一堆外设全卡在 `devices_deferred` | §5 ① | 同上——引用这个控制器的所有设备都被 `-EPROBE_DEFER` 拖住（§2.2） |
| 控制器注册了但 `pinctrl-devices` 显示不支持 pinconf | `grep -n 'confops' drivers/pinctrl/<驱动>.c` | 驱动没填 `pinctrl_desc.confops`，所有 config 属性都是死字 |
| 控制器注册但 `pins` 文件为空 | `grep -n '\.npins\|\.pins' drivers/pinctrl/<驱动>.c` | `pinctrl_desc.npins == 0` |

### §8.2 单设备级（一个外设的所有引脚都不对）

| 症状 | 首查 | 常见原因 |
|---|---|---|
| 设备在 `devices_deferred` 里，日志无 error | §5 ① | 控制器未就绪；`pinctrl-use-default` 可以让它降级为"沿用 bootloader 配置"（§2.2） |
| 设备 probe 成功，引脚完全没配 | §5 ② → `pinctrl-handles` | 没写 `pinctrl-names = "default"`（只写 `pinctrl-0` 时 `:244` 读不到名字，状态名变成 `"0"`——**不是** `default`，`pinctrl_bind_pins()` 找不到） |
| 引脚沿用了 bootloader 的配置 | 同上 | 状态注册成功但 0 条 map（§4.3） |
| 休眠唤醒后引脚配置丢了 | `grep -n 'pinctrl_pm_select' drivers/<驱动>.c` | 驱动没在 resume 里调 `pinctrl_pm_select_default_state()`；写了 `pinctrl-names = "sleep"` **不会**自动生效（§1.4） |
| 两个设备抢一个引脚，后 probe 的失败 | §5 ④ | 两份 DTS 片段都写了同一个 pin |

### §8.3 参数级（mux 对了，某个 config 不对）

| 症状 | 首查 | 常见原因 |
|---|---|---|
| 上下拉方向反了 | §5 ⑤ 第一格 | enable/disable 共用参数号，驱动不读 argument（§3.3） |
| `drive-strength` 无效 | `grep 'case PIN_CONFIG_DRIVE_STRENGTH' <驱动>.c` | 驱动只支持 `DRIVE_STRENGTH_UA`（不同参数号），或压根没实现 |
| 厂商属性无效 | `grep -n custom_params <驱动>.c` | 驱动没注册 `custom_params`（`04-extensions.md` §3.8） |
| 大数值被截断 | 算一下是不是 > 2^24 | `argument` 只有 24 位（`01-api.md` §3） |
| 属性值写成非 u32 后静默用了默认值 | `dtc -I dtb -O dts` 看实际类型 | `-EOVERFLOW` 落到 `default_value` 分支（`pinconf-generic.c:219-224`） |
| debugfs `pinconf-pins` 显示 N/A | `grep 'pin_config_get' <驱动>.c` | 驱动没实现回读，**不代表没配上**——oracle 不可靠（`07-debug.md` §3） |

### §8.4 拓扑级（引脚组 / 名字层面）

| 症状 | 首查 | 常见原因 |
|---|---|---|
| `does not have pin group X` | `cat .../pingroups` | `groups=` 的值拼错，或驱动没注册该 group |
| `pins=` 里的名字找不到 | `cat .../pins` | 控制器的 pin 名和 datasheet 名不一致（常见：`gpio34` vs `GPIO_34`） |
| 写了 `pins` 和 `groups` 两个，只有 `pins` 生效 | §3.1 | `pins` 优先，`groups` 被静默忽略 |
| 子节点里的配置没生效 | `status` 属性 | `for_each_available_child_of_node`（`pinconf-generic.c:395`）跳过 `status="disabled"` 的子节点 |
| GPIO 借用引脚时报 `-EPROBE_DEFER` 或不生效 | 不是本篇范围 | `gpio-ranges` 是 gpiolib 的属性，见 `05-usage.md` §3 |

---

## 下一步读什么

| 想知道什么 | 去哪 |
|---|---|
| map 是什么、`PIN_MAP_TYPE_*` 五种值各自的含义 | `01-api.md` §3 |
| `pinctrl_map` / `pinctrl_setting` / `pinctrl_state` 的结构关系 | `02-datastructures.md` §1、§3 |
| map 变成 setting、setting 被应用的完整时间线 | `03-internals.md` Part A |
| `pinconf-generic` 这个子框架的全貌（含 `custom_params`） | `04-extensions.md` §3、§3.8 |
| 厂商私有 config 参数怎么注册、16 个驱动分别是谁 | `04-extensions.md` §3.8 |
| 消费者驱动的代码骨架（什么时候该自己调 `pinctrl_select_state`） | `05-usage.md` §2 |
| `sleep` / `idle` 状态的电源管理写法 | `05-usage.md` §2.4 |
| debugfs 每个文件的完整语义、哪些是可靠 oracle | `07-debug.md` §3 |
| dynamic_debug 怎么开、pinctrl 为什么没有 tracepoint | `07-debug.md` §2 |
| "为什么 DTS 写错了却一点报错都没有" 的大白话解释 | `08-feynman.md` |
| 本篇所有数字的重跑命令、绑定文档读了哪些 | `99-evidence.md` §1、§3 |
