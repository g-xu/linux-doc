---
title: 扩展机制
---

# pinctrl 的五个子框架

> **本篇的组织轴：一节一个独立子框架。**
>
> pinctrl 核心（`core.c` + `pinmux.c` + `pinconf.c` + `devicetree.c`）之外，还有五块**可选**的代码。它们的共同特点是：
> - **不是分层关系**，而是并列的"你要不要用"；
> - 每一块都可以单独关掉/不用，核心照样工作；
> - 驱动可以只用其中任意几块的任意组合。
>
> **但"互不调用"这句话在 pinctrl 上是错的，别照搬别的子系统的说法**。五块里有**一条真实的依赖边**：§3（pinconf-generic）从头到尾在调 §4（pinctrl-utils）的四个函数，其中 `pinconf_generic_dt_free_map()` 的函数体只有一行——转调 `pinctrl_utils_free_map()`（`pinconf-generic.c:411-417`）。剩下四块之间确实没有调用关系。依赖边一览见 §7.3。
>
> 所以**不要按顺序读本篇**，按 §0 的分诊表挑你需要的那一节。

---

## §0 分诊表：arm64 上你真会碰到哪几个

| # | 子框架 | 代码位置 | Kconfig | arm64 上是否相关 | 你什么时候需要它 |
|---|---|---|---|---|---|
| §1 | 泛型 group 存储 | `core.c:526-715` | `GENERIC_PINCTRL_GROUPS` | **高度相关** | 写 pin controller 驱动、且 group 表是运行期构造（从 DT 或从固件读）而不是编译期静态表 |
| §2 | 泛型 function 存储 | `pinmux.c:789-956` | `GENERIC_PINMUX_FUNCTIONS` | **高度相关** | 同上，针对 function |
| §3 | pinconf-generic | `pinconf-generic.c`（419 行） | `GENERIC_PINCONF` | **几乎必用** | 想让 DTS 里直接写 `bias-pull-up;` `drive-strength = <8>;` 这类标准属性 |
| §4 | pinctrl-utils | `pinctrl-utils.c`（127 行） | **无独立 Kconfig**，无条件编入 `obj-y`（`drivers/pinctrl/Makefile:6`） | 高度相关 | 自己写 `dt_node_to_map()` 时用来拼 map 数组；用 §3 就等于间接用了它 |
| §5 | GPIO 桥接 | `core.c:267-524` + `core.c:749-920` | `GPIOLIB`（只影响 §5.5 的一个桩） | **必然碰到** | 同一批引脚既要当 GPIO 又要当外设功能 |
| §6 | `#pinctrl-cells` 风格 | `devicetree.c:293-434` | **无 Kconfig**，但符号只在私有头声明 | 低（只 3 个驱动） | 你在改 `pinctrl-single` 或 TI 的 iodelay 驱动 |

**本篇不覆盖的东西**：厂商驱动自己的私有框架（如 qcom 的 `msm_pinctrl`、mediatek 的 `moore`/`paris` 两套 pinctrl 抽象）。它们是**驱动层的代码复用**，不是 pinctrl 核心提供的机制。判据：`drivers/pinctrl/<vendor>/` 下的公共文件不导出到 `include/linux/pinctrl/`，也不出现在 `drivers/pinctrl/*.h` 里。

---

## §1 泛型 group 存储（`CONFIG_GENERIC_PINCTRL_GROUPS`）

### §1.1 它解决什么问题

`pinctrl_ops` 里有三个关于 group 的必填回调（见 `01-api.md` §6.1）：

```
get_groups_count(pctldev)                             → group 总数
get_group_name(pctldev, selector)                     → selector → 名字
get_group_pins(pctldev, selector, &pins, &num_pins)   → selector → pin 列表
```

如果驱动的 group 表是**编译期静态数组**，这三个回调各写三行就完了。但有一类驱动做不到：group 的数量和内容要在运行期才知道（从 DTS 子节点数、从固件表、从 SoC 版本寄存器推出来）。这时候驱动就得自己实现"动态增删 + selector 索引"的存储——泛型 group 存储就是把这份实现收进核心。

### §1.2 数据结构与存储

```
pctldev->pin_group_tree     radix tree（INIT_RADIX_TREE 在 core.c:2016，#ifdef 内）
pctldev->num_groups         group 计数

  selector（radix tree 的 index）  →  struct group_desc *
                                        ├─ const char *name
                                        ├─ int *pins        是 int *
                                        ├─ int num_pins
                                        └─ void *data       驱动私有
```

`struct group_desc` 定义在私有头 `drivers/pinctrl/core.h:185-220` 的 `#ifdef CONFIG_GENERIC_PINCTRL_GROUPS` 里。**注意它和 `struct pingroup`（公开 `include/linux/pinctrl/pinctrl.h`）不是一回事**——后者是"字面量描述"，前者是"泛型存储条目"。区分见 `02-datastructures.md` §1.4。

**`pins` 字段是 `int *`，但 `get_group_pins()` 回调的签名要求 `const unsigned **pins`**。`pinctrl_generic_get_group_pins()`（`core.c:568-583`）里做的就是这个强制转换。所以驱动往 `pinctrl_generic_add_group()` 里传的 pin 数组必须是 `int *`，不能是 `unsigned *`——类型不匹配编译器会报 warning，但很多人会想当然地用 `unsigned`。

### §1.3 API 与挂载点

| API | 行 | 挂到 03 时间线的哪一点 |
|---|---|---|
| `pinctrl_generic_get_group_count()` | `core.c:533-535` | 直接填进 `pctlops->get_groups_count`，在状态应用（`03-internals.md` §A11.1）和 debugfs 遍历时被调 |
| `pinctrl_generic_get_group_name()` | `core.c:545-554` | 同上，填 `get_group_name` |
| `pinctrl_generic_get_group_pins()` | `core.c:568-583` | 同上，填 `get_group_pins` |
| `pinctrl_generic_get_group()` | `core.c:593-602` | 驱动自己用（取 `group_desc` 里的 `data` 私有字段） |
| `pinctrl_generic_group_name_to_selector()` | `core.c:607-623` | **全树 0 个驱动调用**（见 §7.2） |
| `pinctrl_generic_add_group()` | `core.c:637-664` | **在 `pinctrl_register_and_init()` 之后、`pinctrl_enable()` 之前**（`03-internals.md` §A2→§A3 之间的那段窗口） |
| `pinctrl_generic_remove_group()` | `core.c:676-690` | 驱动 remove 路径 |
| `pinctrl_generic_free_groups()` | `core.c:701-709`；stub 在 `core.c:712-714` | `pinctrl_unregister()` 内部自动调（`03-internals.md` §A17），驱动**不要**自己调 |

**`pinctrl_generic_add_group()` 的内存策略**：它用 `devm_kzalloc(pctldev->dev, ...)` 分配 `group_desc`（`core.c:645`），所以驱动 remove 时不需要逐个释放。`pinctrl_generic_free_groups()` 的 kerneldoc 明确写了 `The pinctrl groups are allocated with devm_kzalloc() so no need to free them here`（`core.c:697-698`），它只是清空 radix tree。

**`pinctrl_generic_free_groups()` 的锁契约**：kerneldoc 写 `Note that the caller must take care of locking`（`core.c:697`）——它自己不加锁，`pinctrl_unregister()` 在持 `pctldev->mutex` 的情况下调它（`03-internals.md` §A17）。

### §1.4 两套实现不能混用

这是本节最重要的一条。ops 里那三个 group 回调是**一组三个，要么全部指向 `pinctrl_generic_*`，要么全部自己实现**：

```
   ✓ 正确 A（泛型）                   ✓ 正确 B（自实现）
   .get_groups_count = pinctrl_generic_get_group_count      = my_get_count
   .get_group_name   = pinctrl_generic_get_group_name       = my_get_name
   .get_group_pins   = pinctrl_generic_get_group_pins       = my_get_pins
   数据在 pin_group_tree 里                                 数据在驱动的静态表里

   ✗ 混用
   .get_groups_count = my_get_count            ← 读静态表，返回 N
   .get_group_name   = pinctrl_generic_get_group_name   ← 读 radix tree，是空的
   → 表现：group 数量对，名字全是 NULL，或 get_group_pins 返回 num_pins = 0
   → pinmux_enable_setting() 拿到 num_pins = 0 只会 dev_warn，不报错
     （03-internals.md §A11.1）→ 引脚占用登记被静默跳过
```

### §1.5 全树使用统计

| API | 用它的驱动文件数 | 重跑命令 |
|---|---|---|
| `pinctrl_generic_get_group_count` | 17 | `grep -rln 'pinctrl_generic_get_group_count' drivers/ --include=*.c \| grep -v drivers/pinctrl/core.c \| wc -l` |
| `pinctrl_generic_get_group_name` | 17 | 同上换名字 |
| `pinctrl_generic_get_group_pins` | 17 | 同上 |
| `pinctrl_generic_get_group` | **14** | 同上 |
| `pinctrl_generic_add_group` | 16 | 同上 |
| `pinctrl_generic_remove_group` | 6 | 同上 |
| `pinctrl_generic_group_name_to_selector` | **0** | 同上 |

三个数字不同各有原因：**17**（三个 ops 读回调）> **16**（`add_group`）——有一个驱动填了读回调但自己往 radix tree 里塞；**14**（`get_group`）最少，因为它不是填 ops 用的，只有需要取 `group_desc->data` 私有字段的驱动才调。`Kconfig` 里 `select GENERIC_PINCTRL_GROUPS` 出现 10 次（`grep -c 'select GENERIC_PINCTRL_GROUPS' drivers/pinctrl/Kconfig`），比 17 少，因为部分驱动是通过 `depends on` 或父 Kconfig 间接打开的。

---

## §2 泛型 function 存储（`CONFIG_GENERIC_PINMUX_FUNCTIONS`）

与 §1 **结构完全同构**，只是对象换成 function。这里只列差异，不重复讲原理。

### §2.1 对照表

| | group 一族（§1） | function 一族 |
|---|---|---|
| Kconfig | `GENERIC_PINCTRL_GROUPS` | `GENERIC_PINMUX_FUNCTIONS` |
| radix tree | `pctldev->pin_group_tree` | `pctldev->pin_function_tree` |
| 计数字段 | `pctldev->num_groups` | `pctldev->num_functions` |
| 条目类型 | `struct group_desc`（`core.h:185-220`） | `struct function_desc`（`drivers/pinctrl/pinmux.h:121-168`） |
| 代码位置 | `core.c:526-715` | `pinmux.c:789-956` |
| 初始化 | `INIT_RADIX_TREE`，`core.c:2016` | `INIT_RADIX_TREE`，`core.c:2019` |
| stub 位置 | `core.c:712-714` | `drivers/pinctrl/pinmux.h` 的 `#else` 分支（`drivers/pinctrl/pinmux.h:121-168` 区间内） |

### §2.2 API 与行号

| API | 行 | 填进哪个 ops 字段 |
|---|---|---|
| `pinmux_generic_get_function_count()` | `pinmux.c:796-798` | `pmxops->get_functions_count` |
| `pinmux_generic_get_function_name()` | `pinmux.c:809-818` | `pmxops->get_function_name` |
| `pinmux_generic_get_function_groups()` | `pinmux.c:832-846` | `pmxops->get_function_groups` |
| `pinmux_generic_get_function()` | `pinmux.c:856-865` | 驱动自己用（取 `function_desc->data`） |
| `pinmux_generic_add_function()` | `pinmux.c:881-908` | 注册期调用 |
| `pinmux_generic_remove_function()` | `pinmux.c:920-934` | remove 期调用 |
| `pinmux_generic_free_functions()` | `pinmux.c:946-954` | `pinctrl_unregister()` 自动调 |

**`set_mux` 永远要驱动自己实现**——泛型存储只管"function 的元信息"（名字、包含哪些 group），不管"怎么写寄存器"。所以打开这个 Kconfig 之后 `pinmux_ops` 的四个必填回调里仍有一个（`set_mux`）必须自己写。必填清单见 `01-api.md` §6.2 与 `03-internals.md` §A2.1。

### §2.3 全树使用统计

| API | 驱动文件数 |
|---|---|
| `pinmux_generic_get_function_count` / `_name` / `_groups` | 各 **15**（三个 ops 读回调） |
| `pinmux_generic_get_function` | **11**（不填 ops，只用来取 `function_desc->data`） |
| `pinmux_generic_add_function` | 14 |
| `pinmux_generic_remove_function` | 3 |

重跑：`for f in pinmux_generic_get_function_count pinmux_generic_add_function pinmux_generic_remove_function; do echo -n "$f: "; grep -rln "$f" drivers/ --include=*.c | grep -v drivers/pinctrl/pinmux.c | wc -l; done`

**注意 §1 是 17/16/14、§2 是 15/14/11**：两套 Kconfig 虽然常一起 `select`，但**实际用它们的驱动集合不完全重合**。有驱动只用泛型 group 而自己实现 function 表（因为 function 表通常是静态的、group 表才动态）。

---

## §3 pinconf-generic（`CONFIG_GENERIC_PINCONF`）

### §3.1 它解决什么问题

没有 pinconf-generic 时，每个厂商在 DTS 里发明自己的引脚配置属性：

```
        没有它                                      有它
   fsl,pull = <1>;                          bias-pull-up;
   qcom,bias = <2>;                         drive-strength = <8>;
   samsung,pin-pud = <3>;                   input-schmitt-enable;
   → 每家一套，DTS 不可迁移               → 一套通用属性，跨厂商可读
   → 每家自己解析 DT                      → 核心统一解析成 configs[]
```

本节讲的是**属性名 → `configs[]` 数组**的翻译层。翻译出来的 `configs[]` 最终交给驱动的 `pinconf_ops->pin_config_set()` 去写寄存器——**寄存器怎么写永远是驱动的事**，pinconf-generic 一行都不管。这条分界线是理解本节的关键。

### §3.2 打包格式：一个 `unsigned long` 装两样东西

```
 unsigned long config
 ┌──────────────────────────────┬──────────┐
 │  bits 31..8（24 bit）        │ bits 7..0│
 │  argument（u32，截断到 24bit）│  param   │
 └──────────────────────────────┴──────────┘
      ↑                              ↑
   drive-strength 的 <8>         PIN_CONFIG_DRIVE_STRENGTH

 拆包/打包（都是 static inline，pinconf-generic.h）
   pinconf_to_config_param(config)     → config & 0xff
   pinconf_to_config_argument(config)  → (config >> 8) & 0xffffff
   pinconf_to_config_packed(p, a)      → PIN_CONF_PACKED(p, a)
```

**argument 只有 24 位**（`pinconf-generic.h:150-153` 的注释明写 `We put the parameter in the lower 8 bits and the argument in the upper 24 bits`）。所以 `input-debounce = <20000000>;`（20 秒，单位 usec）这种超过 16777215 的值会被**静默截断**——没有任何检查代码。写 debounce 或 `output-impedance-ohms` 大值时要注意。

`param` 只有 8 位，`PIN_CONFIG_END = 0x7F`、`PIN_CONFIG_MAX = 0xFF`（`pinconf-generic.h:140-141`）。驱动自定义参数从 `PIN_CONFIG_END + 1` 起编号——kerneldoc 在 `pinconf-generic.h:108-111` 明说了。

### §3.3 三张表，必须对上

pinconf-generic 里有三张平行的表，同一个参数在三处各出现一次：

| 表 | 位置 | 条目数 | 干什么 | 谁遍历它 |
|---|---|---|---|---|
| `enum pin_config_param` | `pinconf-generic.h:114-142` | **25** 个具名值 | 参数的编号 | — |
| `dt_params[]` | `pinconf-generic.c:159-189` | **28** 行 | DT 属性名 → (param, 默认值) | `parse_dt_cfg()` |
| `conf_items[]` | `pinconf-generic.c:29-54` | **23** 行 | param → debugfs 显示名 + 单位 | `pinconf_generic_dump_one()` |

**三个数字不一样，而且每一处不一样都有含义**：

- **28 > 23**：`dt_params[]` 里有 5 组「enable/disable 成对属性共用一个 enum」的写法：

  | 一对属性 | 共用的 param | value 列 |
  |---|---|---|
  | `input-enable` / `input-disable` | `PIN_CONFIG_INPUT_ENABLE` | 1 / **0** |
  | `input-schmitt-enable` / `input-schmitt-disable` | `PIN_CONFIG_INPUT_SCHMITT_ENABLE` | 1 / 0 |
  | `low-power-enable` / `low-power-disable` | `PIN_CONFIG_MODE_LOW_POWER` | 1 / 0 |
  | `output-enable` / `output-disable` | `PIN_CONFIG_OUTPUT_ENABLE` | 1 / 0 |
  | `output-high` / `output-low` | `PIN_CONFIG_OUTPUT` | 1 / 0 |

  所以驱动的 `pin_config_set()` **必须看 argument，不能只看 param**。只判断 `case PIN_CONFIG_INPUT_ENABLE:` 就去开输入的驱动，遇到 DTS 里写 `input-disable;` 会把输入**打开**——一个非常经典的 bug 形态。判断写法必须是 `arg = pinconf_to_config_argument(config); if (arg) 开 else 关;`。

- **25 − 23 = 2**：有两个 enum 值**既没有 DT 属性名、也没有 debugfs 显示名**：

  | 孤儿 param | 谁在用 | 怎么用 |
  |---|---|---|
  | `PIN_CONFIG_MODE_PWM` | `drivers/pinctrl/pinctrl-cy8c95x0.c:717/804` | 驱动自己在 `pin_config_get/set` 里认，DTS 写不出来 |
  | `PIN_CONFIG_PERSIST_STATE` | `drivers/gpio/gpiolib.c:2228/2592`、`gpio-aspeed.c:973`、`gpio-aspeed-sgpio.c:492`、`sunplus/sppctl.c:492` | 来自 **GPIO 字符设备的 ioctl**（`gpiolib.c:2592` 打包后下发），不走 DT |

  这解释了一个容易困惑的现象：`PIN_CONFIG_PERSIST_STATE` 在 debugfs 的 `pinconf-pins` 里**永远不显示**（`conf_items[]` 里没有它 → `pinconf_generic_dump_one()` 匹配不到 → 跳过），但它确实生效了。别拿 debugfs 当"配置是否生效"的判据。

### §3.4 DT 解析的四层调用链

```
驱动的 pctlops->dt_node_to_map
   │  （多数驱动直接填 pinconf_generic_dt_node_to_map，或填三个 static inline 包装之一）
   ▼
pinconf_generic_dt_node_to_map()            pinconf-generic.c:378-409
   │  reserved_maps = 0; *map = NULL; *num_maps = 0
   ├─► 先处理 np_config 自己           ──┐
   ├─► for_each_available_child_of_node ─┼─► pinconf_generic_dt_subnode_to_map()
   │      每个子节点再处理一次           ─┘        pinconf-generic.c:294-376
   └─► 任一步 <0 → pinctrl_utils_free_map() 全量回滚（:407）
                                 ▼
        ① of_property_count_strings(np, "pins")        :306
           失败 → 再试 "groups"                        :308
           两者都没有 → return 0（静默跳过！注释：may contain config child nodes）
        ② of_property_read_string(np, "function")      :323
           -EINVAL（缺失）→ function = NULL，合法        :325-327
           其他错误 → dev_err "%pOF: could not parse property function"
        ③ pinconf_generic_parse_dt_config()            :334
           失败 → dev_err "%pOF: could not parse node property"
        ④ reserve = (function?1:0) + (num_configs?1:0)，再 × strings_count   :347-352
           pinctrl_utils_reserve_map()                  :354
        ⑤ of_property_for_each_string(np, subnode_target_type, ...)          :359
              有 function → pinctrl_utils_add_map_mux()      :361
              有 configs  → pinctrl_utils_add_map_configs()   :368
        ⑥ exit: kfree(configs)   configs 是临时数组，map 里存的是拷贝
```

**两个静默陷阱**（06 篇会画成决策树）：

1. **步骤 ① 的 `return 0`**：子节点既没有 `pins` 也没有 `groups` → **当成成功、生成 0 条 map**。DTS 里把 `pins` 拼写成 `pin`，表现是"完全没有报错，引脚也完全没配上"。
2. **步骤 ④ 的 `reserve` 可以是 0**：节点有 `pins` 但既没 `function` 也没有任何一条被 `dt_params[]` 认识的配置属性 → `reserve = 0` → 一条 map 都不产生，返回 0。DTS 里写了个厂商私有属性（如 `foo,pull-up;`）而驱动没扩展 `dt_params`，就是这个结局。

### §3.5 三个 `static inline` 包装

`include/linux/pinctrl/pinconf-generic.h` 里有三个包装，只是把最后那个 `type` 参数写死：

| 包装 | 位置 | 等价于 | 什么时候用 |
|---|---|---|---|
| `pinconf_generic_dt_node_to_map_group()` | `pinconf-generic.h:199` | `type = PIN_MAP_TYPE_CONFIGS_GROUP` | 配置的对象一定是 group |
| `pinconf_generic_dt_node_to_map_pin()` | `pinconf-generic.h:207` | `type = PIN_MAP_TYPE_CONFIGS_PIN` | 配置的对象一定是单个 pin |
| `pinconf_generic_dt_node_to_map_all()` | `pinconf-generic.h:215` | `type = PIN_MAP_TYPE_INVALID` | 让 `subnode_to_map` **按节点里写的是 `pins` 还是 `groups` 自动决定** |

`PIN_MAP_TYPE_INVALID` 在这里**不是错误值**，是"未指定、请自动推断"的哨兵——`pinconf-generic.c:313/319` 那两个 `if (type == PIN_MAP_TYPE_INVALID) type = ...` 就是推断逻辑。名字取得很有误导性，这是 `05-usage.md` §6 近邻符号节点名的一条。

### §3.6 三个 dump 辅助（`CONFIG_DEBUG_FS`）

整个 `pinconf-generic.c:28` 到 debugfs 相关代码都在 `#ifdef CONFIG_DEBUG_FS` 里。

| 函数 | 位置 | 导出 | 干什么 |
|---|---|---|---|
| `pinconf_generic_dump_one()` | `pinconf-generic.c:56` | **static，不导出** | 遍历 `conf_items[]`，对每个参数调驱动的 `pin_config_get()` 再格式化 |
| `pinconf_generic_dump_pins()` | `pinconf-generic.c:110` | 是 | 填 `confops->pin_dbg_show` |
| `pinconf_generic_dump_config()` | `pinconf-generic.c:131`，EXPORT 在 `:156` | 是 | 格式化**单条** config，给 debugfs 的 `pinconf-config` 用 |

### §3.7 全树使用统计

**这里必须分开数直接调用和三个包装**——`grep` 不加词边界时 `pinconf_generic_dt_node_to_map` 会把三个 `_group` / `_pin` / `_all` 包装的命中一起吃进来，得到一个虚高的合并数。真实分布：

| API | 驱动文件数（`grep -w`） | 备注 |
|---|---|---|
| `pinconf_generic_dt_node_to_map` **本体** | **2** | 只有两个驱动直接调、自己传 `type` |
| `pinconf_generic_dt_node_to_map_pin` | 27 | 包装，`type = CONFIGS_PIN` |
| `pinconf_generic_dt_node_to_map_all` | 21 | 包装，`type = INVALID`（自动推断） |
| `pinconf_generic_dt_node_to_map_group` | 16 | 包装，`type = CONFIGS_GROUP` |
| ↳ **四者合并去重后的文件数** | **66** | 这才是"用 pinconf-generic 解析 DT 的驱动数" |
| `pinconf_generic_dt_free_map` | 19 | 剩下的驱动直接填 `pinctrl_utils_free_map`（见 §7.3） |
| `pinconf_generic_parse_dt_config` | 15 | 自己写 `dt_node_to_map` 但复用属性解析 |
| `pinconf_generic_dump_config` | 10 | |
| `pinconf_generic_dump_pins` | **1** | 只有一个驱动用 |
| `pinconf_generic_dt_subnode_to_map` | **1** | 只有一个驱动直接调子节点级入口 |

66 > 27+21+16 = 64 说明**有两个文件同时用了不止一个包装**（去重后才是 66）；同时也意味着 2 个直接调用者里至少有一部分与包装使用者重叠。重跑（`-w` 不能省）：
```bash
for f in pinconf_generic_dt_node_to_map pinconf_generic_dt_node_to_map_pin \
         pinconf_generic_dt_node_to_map_all pinconf_generic_dt_node_to_map_group \
         pinconf_generic_dt_free_map pinconf_generic_parse_dt_config \
         pinconf_generic_dump_config pinconf_generic_dump_pins \
         pinconf_generic_dt_subnode_to_map; do
  echo -n "$f: "
  grep -rlnw "$f" drivers/ --include=*.c | grep -v drivers/pinctrl/pinconf-generic.c | wc -l
done
# 合并去重
{ for f in pinconf_generic_dt_node_to_map{,_pin,_all,_group}; do
    grep -rlnw "$f" drivers/ --include=*.c; done; } \
  | grep -v drivers/pinctrl/pinconf-generic.c | sort -u | wc -l
```

`select GENERIC_PINCONF` 在 `drivers/pinctrl/Kconfig` 里出现 **30** 次（`grep -c 'select GENERIC_PINCONF' drivers/pinctrl/Kconfig`）。30 vs 66：差值来自 `drivers/pinctrl/` 之外的目录（`drivers/gpio/`、SoC 各自的子 Kconfig）以及通过父项间接 select 的驱动。

---

### §3.8 厂商私有 config 参数（`custom_params`）

#### §3.8.1 它解决什么问题

`enum pin_config_param`（`include/linux/pinctrl/pinconf-generic.h`）是一张**封闭的通用参数表**，`dt_params[]`（`pinconf-generic.c:160-189`，共 **28** 条）把它逐条映射到标准 DT 属性名（`bias-pull-up`、`drive-strength`、…）。但厂商总有通用表覆盖不了的东西：mediatek 的 `mediatek,tdsel`、qcom 的 `qcom,pull-up-strength`、starfive 的 `starfive,strong-pull-up`。

`custom_params` 就是**让驱动往这张表后面接一段自己的**。三件事一次性搞定：DT 属性名被识别、参数值被打包进同一个 `unsigned long`、debugfs 能把它打印出来。

#### §3.8.2 三个字段 + 编号约定

`struct pinctrl_desc` 里的三个字段（`include/linux/pinctrl/pinctrl.h:159-161`，整段包在 `#ifdef CONFIG_GENERIC_PINCONF` 里 `:158-162`）：

| 字段 | 类型 | 谁读它 | 少填的后果 |
|---|---|---|---|
| `num_custom_params` | `unsigned int` | 三处核心代码都先查它非 0 | 填 0 → 另两个字段被完全忽略，DT 属性静默不识别 |
| `custom_params` | `const struct pinconf_generic_params *` | `pinconf_generic_parse_dt_config()` | 不填 → 同上，静默不识别（`:262-263` 双条件短路） |
| `custom_conf_items` | `const struct pin_config_item *` | 只有 debugfs dump 两处 | 不填 → **功能完全正常**，只是 debugfs 里这些参数不显示（`:123-124` / `:143-145` 都是"不填就 return"） |

两个数组的元素类型都在 `pinconf-generic.h`：

```c
/* pinconf-generic.h:183-187 —— 解析 DT 用 */
struct pinconf_generic_params {
	const char * const property;      /* DT 属性名，如 "mediatek,tdsel" */
	enum pin_config_param param;      /* 打包进 config 的参数编号 */
	u32 default_value;                /* 属性存在但没带值时用这个 */
};

/* pinconf-generic.h:176-181 —— debugfs 显示用 */
struct pin_config_item {
	const enum pin_config_param param;
	const char * const display;       /* debugfs 里印的名字 */
	const char * const format;        /* 单位后缀，通常 NULL */
	bool has_arg;                     /* false → 只印名字不印值 */
};
```

写 `custom_conf_items` 的固定手法是宏 `PCONFDUMP(param, display, format, has_arg)`（`pinconf-generic.h:172-174`，展开成一个指定初始化器）。

**参数编号必须从 `PIN_CONFIG_END + 1` 起**，这是 kerneldoc 里写死的约定（`pinconf-generic.h:108-110`：「if you need to pass in custom configurations to the pin controller, use PIN_CONFIG_END+1 as the base offset」）。典型写法（`drivers/pinctrl/pinctrl-k210.c:459-460`）：

```c
#define PIN_CONFIG_OUTPUT_INVERT	(PIN_CONFIG_END + 1)
#define PIN_CONFIG_INPUT_INVERT		(PIN_CONFIG_END + 2)

static const struct pinconf_generic_params k210_pinconf_custom_params[] = {
	{ "output-polarity-invert", PIN_CONFIG_OUTPUT_INVERT, 1 },
	{ "input-polarity-invert",  PIN_CONFIG_INPUT_INVERT, 1 },
};
```

编号上界由打包格式决定，不是无限的：`PIN_CONFIG_MAX` 与 8 位 param 字段的关系见 §3.2。

#### §3.8.3 核心一共只在四处读这三个字段

全部集中在 `drivers/pinctrl/pinconf-generic.c`，一处都不在别的文件里：

| 位置 | 上下文 | 读哪个字段 | 干什么 |
|---|---|---|---|
| `:254-256` | `pinconf_generic_parse_dt_config()`（`:241` 起） | `num_custom_params` | 临时数组扩容：`max_cfg = ARRAY_SIZE(dt_params)` 再 `+= num_custom_params` |
| `:261-265` | 同上 | `num_custom_params` + `custom_params` | 先跑通用表 `parse_dt_cfg(np, dt_params, …)`，**再**跑私有表 —— 两次调用共用同一个 `cfg` / `ncfg`，所以私有参数追加在通用参数**后面** |
| `:123-128` | `pinconf_generic_dump_pins()` | `num_custom_params` + `custom_conf_items` | debugfs 逐 pin dump：先通用表再私有表 |
| `:143-154` | `pinconf_generic_dump_config()` | `num_custom_params` + `custom_conf_items` | 打印单个 config 值，私有表里按 `param` 线性匹配，命中就 `seq_printf("%s: 0x%x", display, argument)` |

真正干活的是 `parse_dt_cfg()`（`pinconf-generic.c:204-230`）——通用表和私有表走的是**同一个函数**，这就是"私有参数和标准属性行为完全一致"的原因：

```c
ret = of_property_read_u32(np, par->property, &val);
if (ret == -EINVAL)          /* :219-220 属性不存在 → 跳过 */
	continue;
if (ret)                     /* :223-224 属性存在但没值 → 用 default_value */
	val = par->default_value;
cfg[*ncfg] = pinconf_to_config_packed(par->param, val);   /* :227 */
```

注意 `:219` 的判据是 **`ret == -EINVAL` 才算"属性不存在"**。`of_property_read_u32` 在"属性存在但长度不足 4 字节"时返回 `-EOVERFLOW`，落到 `:223` 分支 → **静默使用 `default_value`**。DTS 里写 `mediatek,tdsel;`（无值）和写 `mediatek,tdsel = <>;` 都会拿到默认值而不是报错。

#### §3.8.4 `custom_conf_items` 必须与 `custom_params` 同长度

kerneldoc 把这条写成了硬要求（`pinctrl.h:142-143`）：「Information how to print @params in debugfs, **must be the same size as the @custom_params, i.e. @num_custom_params**」。

理由在代码里：`:125-127` 和 `:147` 两处都拿 **`num_custom_params`** 去遍历 **`custom_conf_items`**。两个数组长度不一致时——

| 情况 | 后果 |
|---|---|
| `custom_conf_items` 比 `custom_params` **短** | 越界读 `custom_conf_items[i]`，debugfs 打印野指针字符串 |
| `custom_conf_items` 比 `custom_params` **长** | 多出来的条目永远不被遍历到，debugfs 少显示几个参数 |

全树只有一个驱动把这条约束**编译期钉住**了 —— starfive（`drivers/pinctrl/starfive/pinctrl-starfive-jh7100.c:732-733`）：

```c
static_assert(ARRAY_SIZE(starfive_pinconf_custom_conf_items) ==
	      ARRAY_SIZE(starfive_pinconf_custom_params));
```

同一处还有第二个值得抄的写法（`:735`）：`#else` 分支下 `#define starfive_pinconf_custom_conf_items NULL`，这样 `.custom_conf_items = starfive_pinconf_custom_conf_items` 在 `CONFIG_DEBUG_FS=n` 时不需要 `#ifdef` 包住赋值语句。其余驱动的通行做法是把赋值本身包进 `#ifdef CONFIG_DEBUG_FS`（如 `drivers/pinctrl/mediatek/pinctrl-paris.c:1075-1077`）。

#### §3.8.5 两种赋值形态

| 形态 | 代表 | 为什么这么写 |
|---|---|---|
| **静态初始化器**里直接填 | `pinctrl-k210.c:894-895`、`sprd/pinctrl-sprd.c:872-875` | 一个驱动对应一颗 SoC，参数表固定 |
| **probe 里运行时赋值** | `mediatek/pinctrl-paris.c:1073-1076`、`mediatek/pinctrl-moore.c:654-657`、`qcom/pinctrl-spmi-gpio.c:1103-1106` | 一个驱动覆盖多颗 SoC，`pinctrl_desc` 是可写的模板变量 |

只 grep 静态初始化器（`grep '\.custom_params[[:space:]]*='` 后再筛 `,$`）会漏掉 mediatek / qcom 这一整批——和 §3 里 `link_consumers` 一样的坑（见 05 §1.7）。

freescale 是第三种形态：`pinctrl-imx.c:857-858` 把表从**自己定义的 SoC info 结构**里搬过来（`drivers/pinctrl/freescale/pinctrl-imx.h:105-106` 定义了同名的两个字段），所以 grep `custom_params` 在 imx 上会命中两个文件。

#### §3.8.6 arm64 上真会碰到的驱动

| 驱动 | 私有参数表定义处 | 赋值处 | 私有属性举例 |
|---|---|---|---|
| `mediatek/pinctrl-paris.c` | `:26-32` | `:1073-1076` | `mediatek,tdsel` / `rdsel` / `pull-up-adv` / `pull-down-adv` / `drive-strength-adv`（5 条） |
| `mediatek/pinctrl-moore.c` | 复用 paris 的 `mtk_custom_bindings` | `:654-657` | 同上 |
| `qcom/pinctrl-spmi-gpio.c` | `pmic_gpio_bindings` | `:1103-1106` | PMIC GPIO 私有 |
| `qcom/pinctrl-spmi-mpp.c` | — 见 `:876-879` 赋值处上文 | `:876-879` | PMIC MPP 私有 |
| `qcom/pinctrl-ssbi-gpio.c` | — 见 `:761-764` 赋值处上文 | `:761-764` | 同族老接口 |
| `qcom/pinctrl-ssbi-mpp.c` | — 见 `:845-848` 赋值处上文 | `:845-848` | 同族老接口 |
| `freescale/pinctrl-imx.c` | 由各 SoC 的 info 结构提供 | `:857-858` | i.MX 各代不同 |
| `sprd/pinctrl-sprd.c` | `sprd_dt_params` | `:872-875` | 展锐私有 |
| `pinctrl-at91-pio4.c` | — 见 `:1098-1099` 赋值处上文 | `:1098-1099` | at91 私有 |
| `pinctrl-max77620.c` | — 见 `:576-578` 赋值处上文 | `:576-578` | PMIC 私有 |

非 arm64（记录以免读者当成遗漏）：`pinctrl-zynq.c:1153-1156`（arm32）、`pinctrl-lpc18xx.c:1265-1268`（arm32）、`pinctrl-pic32.c:2189-2190`（MIPS）、`pinctrl-k210.c:894-895`（RISC-V）、`starfive/pinctrl-starfive-jh7100.c:911-913`（RISC-V，但 §3.8.4 的 `static_assert` 写法值得抄）。

#### §3.8.7 一个反例：用了编号约定，但没走 `custom_params`

`PIN_CONFIG_END + N` 这个编号约定和 `custom_params` 注册机制是**两件可以拆开的事**。`drivers/pinctrl/bcm/pinctrl-bcm2835.c` 就只用了前者：

- `:72` `#define BCM2835_PINCONF_PARAM_PULL (PIN_CONFIG_END + 1)`
- 全文**没有** `custom_params` / `num_custom_params` 赋值
- 它自己写 `dt_node_to_map`（`:812`，`.dt_node_to_map = bcm2835_pctl_dt_node_to_map` 在 `:921`），里面先调 `pinconf_generic_dt_node_to_map_all()`（`:824`）拿通用属性，再手工解析 `brcm,pull`（`:836`、`:895-899`），自己调 `pinconf_to_config_packed(BCM2835_PINCONF_PARAM_PULL, pull)`（`:801`）打包
- `pin_config_set` 里再按 `case BCM2835_PINCONF_PARAM_PULL:`（`:1044-1045`、`:1111-1112`）消费，两处注释都写着 `legacy`

所以：**看到 `PIN_CONFIG_END + N` 不等于这个驱动注册了 `custom_params`**；反过来，注册了 `custom_params` 的驱动一定用了这个编号约定（否则会和通用参数撞号）。

#### §3.8.8 全树使用统计

| 字段 | 文件数（`grep -rlnw`） | 备注 |
|---|---|---|
| `custom_params` | **16** | 15 个在 `drivers/pinctrl/` + 1 个在 `drivers/rtc/rtc-omap.c` |
| `num_custom_params` | **16** | 与上一行同集合（两者必须成对出现） |
| `custom_conf_items` | **11** | 16−11 = 5 个驱动不提供 debugfs 显示信息，功能不受影响（见 §3.8.2） |
| `PIN_CONFIG_END + N` 编号 | **16 个文件 / 42 处** | 与 `custom_params` 的 16 个文件**不是同一集合**：多了 bcm2835（见 §3.8.7），少了 pinctrl-imx（编号在各 SoC 驱动里定义） |

`drivers/rtc/rtc-omap.c:676-684` 是一个**不住在 `drivers/pinctrl/` 下的 pin controller**——RTC 芯片把自己的两根引脚注册成了 pinctrl 设备，`.custom_params = rtc_params` / `.num_custom_params` / `#ifdef CONFIG_DEBUG_FS .custom_conf_items = rtc_conf_items`。这印证了 skill 里那条：框架的使用者不一定住在框架自己的目录下。

重跑：
```bash
for f in custom_params num_custom_params custom_conf_items; do
  echo -n "$f: "
  grep -rlnw "$f" drivers/ --include=*.c | grep -v drivers/pinctrl/pinconf-generic.c | wc -l
done
# 编号约定的使用者（注意与上面不是同一集合）
grep -rln 'PIN_CONFIG_END + [0-9]' drivers/ --include=*.c --include=*.h | wc -l
grep -rn  'PIN_CONFIG_END + [0-9]' drivers/ --include=*.c --include=*.h | wc -l
# dt_params 通用表条数
sed -n '160,189p' drivers/pinctrl/pinconf-generic.c | grep -c '^\s*{ "'
```

---

## §4 pinctrl-utils（无 Kconfig，无条件编入）

### §4.1 它解决什么问题

`dt_node_to_map()` 回调的契约是"你分配一个 `struct pinctrl_map` 数组交给核心，核心用完后调你的 `dt_free_map()`"（`01-api.md` §6.1）。数组长度事先不知道（要边解析边加），所以每个驱动都要写一遍"按需扩容 + 逐条填字段 + 出错全量释放"。pinctrl-utils 就是这段样板，**127 行，5 个函数全部 `EXPORT_SYMBOL_GPL`**。

### §4.2 五个函数

| 函数 | 位置 | 干什么 | 关键细节 |
|---|---|---|---|
| `pinctrl_utils_reserve_map()` | `pinctrl-utils.c:18-40`，EXPORT `:41` | 把 map 数组扩到能再放 `reserve` 条 | `new_num = *num_maps + reserve`，**精确扩容、不做倍增也不 round up**（`:23`）；`old_num >= new_num` 直接 return 0（`:26`）；`*reserved_maps` 是"已分配容量"、`*num_maps` 是"已用条数"，**两个不是一回事**；新增部分 `memset` 清零（`:35`）；失败时 `dev_err "krealloc(map) failed"` 并返回 `-ENOMEM` |
| `pinctrl_utils_add_map_mux()` | `:43-57`，EXPORT `:58` | 追加一条 `PIN_MAP_TYPE_MUX_GROUP` | 填 `data.mux.group` / `data.mux.function`；**不拷贝字符串**，存的是 DT 里的指针；容量不够时 `WARN_ON(*num_maps == *reserved_maps)` + 返回 `-ENOSPC`（`:48-49`）——即"忘了先 reserve"会打栈回溯 |
| `pinctrl_utils_add_map_configs()` | `:60-83`，EXPORT `:84` | 追加一条 `CONFIGS_PIN` / `CONFIGS_GROUP` | **会 `kmemdup()` configs 数组**（`:71`，所以 §3.4 步骤 ⑥ 的 `kfree(configs)` 是安全的）；同样有 `WARN_ON` + `-ENOSPC`（`:68-69`）；`reserved_maps` 参数**只用于这个检查**，不参与扩容 |
| `pinctrl_utils_add_config()` | `:86-107`，EXPORT `:108` | 往一个临时 configs 数组里追加一个 `unsigned long` | 操作的是**配置值数组**，不是 map 数组。和上一个只差两个字母，见 `05-usage.md` §6；每次只 +1，用 `krealloc()`（不是 `krealloc_array`）；失败时 `dev_err "krealloc(configs) failed"` |
| `pinctrl_utils_free_map()` | `:110-126`，EXPORT `:127` | 遍历释放整个 map 数组 | 只 `kfree` `CONFIGS_GROUP` / `CONFIGS_PIN` 条目的 `data.configs.configs`（`:117-120`），其余走 `default: break`；最后 `kfree(map)`。MUX 条目里的字符串不释放（本来就没拷贝） |

**`add_map_mux` 不拷贝字符串、`add_map_configs` 拷贝数组**——这个不对称是本节最容易出错的地方。含义：map 里的 group/function 名字**生命周期挂在 DT 节点上**，而 DT 节点在整个内核生命周期内不会消失，所以安全；但如果你在自己的 `dt_node_to_map` 里用栈上或临时 buffer 拼了个名字传进去，就是悬垂指针。

### §4.3 挂在 03 时间线的哪一点

全部在 **`03-internals.md` §A5**（`pinctrl_dt_to_map()` → 驱动 `dt_node_to_map` 回调）那一点，也就是消费者第一次 `devm_pinctrl_get()` 的时候。`pinctrl_utils_free_map()` 多一个挂点：**`03-internals.md` §B1.2** 提到的 `dt_free_map` 路径——那条路径上**持着全局 `pinctrl_list_mutex`**（`core.c:1168`），所以这五个函数里不能有任何回到 pinctrl 核心的调用。看代码确认：它们只调 `krealloc_array` / `kmemdup` / `kfree`，没有一个碰核心 API。

### §4.4 全树使用统计

| 函数 | 驱动文件数 |
|---|---|
| `pinctrl_utils_free_map` | **62** |
| `pinctrl_utils_reserve_map` | 15 |
| `pinctrl_utils_add_map_mux` | 10 |
| `pinctrl_utils_add_map_configs` | 10 |
| `pinctrl_utils_add_config` | **2** |

62 远大于 15：多数驱动**只用 `free_map` 而不用 `reserve_map`**——因为构造那一半交给了 §3 的 `pinconf_generic_dt_node_to_map()`，只有释放要自己填 `dt_free_map`（填 `pinctrl_utils_free_map` 而不是 `pinconf_generic_dt_free_map`，两者行为完全一样，见 §7.3）。

---

## §5 GPIO 桥接（`core.c:267-524` + `core.c:749-920`）

两段代码之间夹着一个和 GPIO 无关的函数 `pinctrl_get_group_pins()`（`core.c:452-467`），别被行号区间误导。

### §5.1 它解决什么问题

同一颗 SoC 引脚，既可以当 GPIO，也可以当 UART TX。**两个子系统各有自己的编号空间**：

```
   GPIO 子系统编号空间                pinctrl 编号空间
   （全局 gpio number）              （每个控制器自己的 pin number）
   ┌──────────────────┐             ┌────────────────────┐
   │ gpio 456         │             │ pctldev "gpio@fd60" │
   │ gpio 457         │  ──range──► │   pin 0             │
   │ ...              │             │   pin 1             │
   └──────────────────┘             └────────────────────┘

   桥接的两件事：
   ① 编号翻译：gpio 456 → 哪个 pctldev 的哪个 pin
   ② 占用互斥：一个 pin 被 mux 成 UART 之后，还能不能当 GPIO？
```

**本节讲的全部是"pinctrl 侧"**。GPIO 子系统内部怎么管 `gpio_chip`、`gpio_desc` 不在本文档集范围内（那是 gpio 子系统的文档）。分界线：`drivers/gpio/` 里的代码只作为"调用者"出现。

### §5.2 编号翻译：`struct pinctrl_gpio_range` 与两种模式

```
 struct pinctrl_gpio_range（include/linux/pinctrl/pinctrl.h）
   ├─ name
   ├─ id, base, pin_base, npins
   ├─ unsigned const *pins     可选
   └─ struct gpio_chip *gc

 翻译函数 gpio_to_pin()   core.c:280-288（static inline）
   offset = gpio - range->base;
   if (range->pins)  return range->pins[offset];   ← 模式 B：任意映射表
   else              return range->pin_base + offset; ← 模式 A：线性偏移
```

| 模式 | 判据 | 用法 | 谁用 |
|---|---|---|---|
| **A 线性** | `range->pins == NULL` | 连续 gpio 对应连续 pin，只给 `base` / `pin_base` / `npins` | 绝大多数，`gpio-ranges` DT 属性只能产生这种 |
| **B 表映射** | `range->pins != NULL` | `pins[]` 逐个列出 pin 号，`pin_base` 被忽略 | 引脚编号跳跃的控制器，只能驱动代码里手填 |

`gpio_to_pin()` 的 kerneldoc 明写 `This function assumes the gpio is part of the specified GPIO range, use only after making sure this is the case`（`core.c:276-278`）——**它不做任何边界检查**。所以必须先 `pinctrl_get_device_gpio_range()` 拿到 range 再翻译，顺序不能颠倒。

### §5.3 六个 `pinctrl_gpio_*` 函数：统一形状 + 三种失败处理

六个对外函数的骨架完全一样：

```
   pinctrl_get_device_gpio_range(gpio, &pctldev, &range)   ← 遍历所有控制器
        │  内部：mutex_lock(&pinctrldev_list_mutex)  core.c:383
        │        找不到 → return -EPROBE_DEFER       core.c:400
        ▼
   mutex_lock(&pctldev->mutex)
   pin = gpio_to_pin(range, gpio)
   <做事：pinmux_request_gpio / pinmux_free_gpio / pinmux_gpio_direction / pinconf_set_config>
   mutex_unlock(&pctldev->mutex)
```

**区别只在"找不到 range 时怎么办"，而且三种处理各不相同**——这是本节最值得记的一张表：

| 函数 | 位置 | 找不到 range 时 | 后果 |
|---|---|---|---|
| `pinctrl_gpio_can_use_line()` | `core.c:749-774` | **`return true`**（`:762`） | 没有 pinctrl 撑腰的纯 GPIO 控制器，一律"可用" |
| `pinctrl_gpio_request()` | `core.c:785-809` | `if (pinctrl_ready_for_gpio_range(gpio)) ret = 0;`（`:794-795`） | **可能静默变成成功**，详见 §5.5 |
| `pinctrl_gpio_free()` | `core.c:820-839` | `return;`（`:829`）——函数返回 `void` | 释放失败无法上报，天然静默 |
| `pinctrl_gpio_direction()`（static） | `core.c:842-866` | `return ret;`（即 `-EPROBE_DEFER`） | 直接把 `-EPROBE_DEFER` 传给 GPIO 驱动 |
| `pinctrl_gpio_direction_input()` | `core.c:873-876` | 转调上面那个 | 同上 |
| `pinctrl_gpio_direction_output()` | `core.c:887-890` | 转调上面那个 | 同上 |
| `pinctrl_gpio_set_config()` | `core.c:902-919` | `return ret;`（`-EPROBE_DEFER`） | 把 `config` 包成单元素数组 `configs[]`（`:904`）再调 `pinconf_set_config()` |

`pinctrl_gpio_direction_input/output` 只是把 `input` 实参写死的两行包装（`core.c:873-876` / `887-890`），真正干活的 `pinctrl_gpio_direction()` 是 `static`。

六个函数的 kerneldoc 全都带同一句警告：`This function should *ONLY* be used from gpiolib-based GPIO drivers ... platforms and individual drivers shall *NOT* request GPIO pins to be muxed in`（如 `core.c:781-783`）。**消费者驱动想让某个引脚变 GPIO，正确做法是在 DTS 里写一个 pinctrl state，不是调这些函数。**

### §5.4 唯一一条跨模块调用：桥接会调 `pinmux_*`

§0 说过五块子框架之间只有 §3→§4 一条依赖边。GPIO 桥接是另一种情况：它**不调其他子框架，但调核心内部的 `pinmux_*` 私有函数**（都声明在私有头 `drivers/pinctrl/pinmux.h`）：

| 桥接函数 | 调用的核心内部函数 | 在 `03-internals.md` 的位置 |
|---|---|---|
| `pinctrl_gpio_can_use_line()` | `pinmux_can_be_used_for_gpio()` | §B2 |
| `pinctrl_gpio_request()` | `pinmux_request_gpio()` → `pin_request(gpio_range≠NULL)` | §A11.2 的 GPIO 分支 |
| `pinctrl_gpio_free()` | `pinmux_free_gpio()` → `pin_free(gpio_range≠NULL)` | §A15.2 |
| `pinctrl_gpio_direction()` | `pinmux_gpio_direction()` → `ops->gpio_set_direction` | §B6 |
| `pinctrl_gpio_set_config()` | `pinconf_set_config()` | §A12 的单条版本 |

所以 GPIO 桥接**共用 §A11.2 那张 `strict` 真值表**——`pin_request()` 的 `gpio_range` 参数非空就走 GPIO 分支，占的是 `desc->gpio_owner` 而不是 `desc->mux_owner`。这也解释了为什么 `strict` 只影响"跨类别冲突"：非 `strict` 控制器允许一个 pin 同时有 `mux_owner` 和 `gpio_owner`。

### §5.5 `pinctrl_gpio_request()` 的静默成功

这是 pinctrl 里最隐蔽的一处，`03-internals.md` §B6 提过机制，这里给完整决策树：

```
gpiolib 调 pinctrl_gpio_request(gpio)             drivers/gpio/gpiolib.c:1750
   │
   ├─ pinctrl_get_device_gpio_range() 成功
   │     → 正常走 pin_request()，占用登记生效 ✓
   │
   └─ 失败（-EPROBE_DEFER）
         │
         ├─ CONFIG_GPIOLIB=y  → pinctrl_ready_for_gpio_range(gpio)   core.c:331-361
         │     遍历所有 pctldev 的所有 range，问：
         │     "有没有哪个 range 的 gc 覆盖了这个 gpio 所在 chip 的 [base, base+ngpio-1]？"
         │        │
         │        ├─ 有 → return true  → ret = 0，pinctrl_gpio_request 报告成功
         │        │        含义："这颗 chip 是有 pinctrl 撑腰的，只是这一根引脚
         │        │               没被登记进 range" → 当成"该引脚不需要 pinctrl 参与"
         │        │        后果：pin_request() 没跑，占用登记没做，
         │        │           debugfs 的 pinmux-pins 里看不到这根引脚被 GPIO 占用
         │        │
         │        └─ 没有 → return false → ret 保持 -EPROBE_DEFER
         │                 gpiolib 会把 gpio_request 也失败掉，等 pinctrl 先 probe
         │
         └─ CONFIG_GPIOLIB=n → core.c:363 的桩函数 `return true`
                 → 无条件 ret = 0，永远静默成功
```

定位方法：`cat /sys/kernel/debug/pinctrl/*/gpio-ranges` 看这根引脚在不在某条 range 里。在 → 正常；不在但同 chip 的其他引脚在 → 撞上了上面那个 `true` 分支。详见 `07-debug.md`。

### §5.6 range 怎么注册进来：四条路，只有一条推荐

| 方式 | 入口 | 谁调 | 状态 |
|---|---|---|---|
| **DT `gpio-ranges`** | `of_gpiochip_add_pin_range()`，`drivers/gpio/gpiolib-of.c:893-978` | `gpiolib-of.c:1000` 自动调 | **推荐**，arm64 上的标准做法 |
| `gpiochip_add_pin_range()` | `drivers/gpio/gpiolib.c:1855-1893` | GPIO 驱动手工调 | 非 DT 平台 / 模式 B 表映射 |
| `gpiochip_add_pingroup_range()` | `drivers/gpio/gpiolib.c:1797-1836` | GPIO 驱动手工调 | **kerneldoc 自称已废弃**：`Calling this function directly from a DeviceTree-supported pinctrl driver is DEPRECATED` |
| `pinctrl_add_gpio_range()` / `_ranges()` | `core.c:411-417` / `core.c:420-428` | **pinctrl 驱动**自己调 | 反方向注册（由 pin controller 而非 gpio chip 发起），14 个驱动仍在用；`_ranges()` 只是循环调 `_range()`（`:426-427`），全树 0 个调用者 |
| `pinctrl_find_and_add_gpio_range()` | `core.c:431-449` | 按**控制器 devname 字符串**找 pctldev 再挂 range | 找不到时返回 `ERR_PTR(-EPROBE_DEFER)`（`:444`），注释解释为「假定它还没 probe」；全树 1 个调用者 |

`gpio-ranges` 的 DT 语法与解析：

```dts
gpio1: gpio@fd6f0000 {
    gpio-controller;
    #gpio-cells = <2>;
    gpio-ranges = <&pinctrl 0 32 8>;
    /*                ^     ^  ^ ^
     *                |     |  | └─ npins：8 根
     *                |     |  └─── pinctrl_base：pinctrl 侧起始 pin 号
     *                |     └────── gpio_offset：本 chip 内的起始偏移
     *                └──────────── pinctrl 控制器的 phandle
     */
};
```

解析代码 `gpiolib-of.c:914` 用的是 `of_parse_phandle_with_fixed_args(np, "gpio-ranges", 3, ...)`——**固定 3 个 cell**，写少写多都不行。还有一个配套属性 `gpio-ranges-group-names`（`gpiolib-of.c:900`），用来按 group 名而不是 pin 号指定范围。详见 `06-devicetree.md`。

### §5.7 全树使用统计

| API | 驱动文件数 | 关键调用点 |
|---|---|---|
| `pinctrl_gpio_direction_output` | 22 | |
| `pinctrl_gpio_direction_input` | 21 | |
| `pinctrl_gpio_request` | 13 | 含 `drivers/gpio/gpiolib.c:1750` —— gpiolib 的通用路径 |
| `pinctrl_gpio_free` | 13 | 含 `drivers/gpio/gpiolib.c:1766` |
| `pinctrl_gpio_set_config` | 2 | 含 `drivers/gpio/gpiolib.c:1779` |
| `pinctrl_gpio_can_use_line` | **1** | 唯一调用者 `drivers/gpio/gpiolib-cdev.c:2401`（GPIO 字符设备查询引脚是否可用） |
| `pinctrl_add_gpio_range` | 14 | pinctrl 驱动侧反向注册 |
| `pinctrl_remove_gpio_range` | 3 | |
| `pinctrl_find_and_add_gpio_range` | 1 | |
| `pinctrl_add_gpio_ranges` | **0** | **全树没有任何调用者**，只有定义和 EXPORT（`core.c:420-429`） |

重跑（`pinctrl_add_gpio_ranges` 那条要用词边界，否则会被 `pinctrl_add_gpio_range` 的命中污染）：
```bash
for f in pinctrl_gpio_direction_output pinctrl_gpio_request pinctrl_gpio_can_use_line \
         pinctrl_add_gpio_range pinctrl_add_gpio_ranges; do
  echo -n "$f: "
  grep -rlnw "$f" drivers/ --include=*.c | grep -v drivers/pinctrl/core.c | wc -l
done
```

13 vs 22：`pinctrl_gpio_request/free` 的调用者少，是因为**多数 GPIO 驱动根本不用自己调**——`gpiolib.c:1750/1766` 已经在通用路径上代劳了。会自己调的是那些不走 gpiolib 通用 request 的驱动。

---

## §6 `#pinctrl-cells` 风格的辅助函数（`devicetree.c:293-434`）

### §6.1 它解决什么问题

标准 pinctrl DT 绑定的形状是"pinctrl 节点下挂一堆子节点，每个子节点用 `pins` / `groups` / `function` 描述一个状态"（§3.4）。但有一类硬件（TI 的 `pinctrl-single`、iodelay）的寄存器是**一根引脚一个寄存器**，DTS 里更自然的写法是**直接引用 phandle + 若干个 cell**：

```dts
&pinctrl {
    #pinctrl-cells = <2>;      ← 声明每次引用带 2 个 cell
};

some_pins: pinmux {
    pinctrl-single,pins = <&pinctrl 0x1a0 0x6>;
    /*                              ^^^^^ ^^^ 两个 cell */
};
```

这一套是 `pinctrl-single` 家族的私有约定，**不是通用 pinctrl 绑定**。放在 `devicetree.c` 里是为了让两三个驱动共用，不是给所有人用的。

### §6.2 五个函数，全部只在私有头声明

| 函数 | 位置 | 声明在 | 干什么 |
|---|---|---|---|
| `pinctrl_find_cells_size()` | `devicetree.c:297-311` | **static，不出文件** | 读 phandle 目标节点的 `#pinctrl-cells`（属性名字面量在 `devicetree.c:299`），换算出每条记录几个 cell |
| `pinctrl_get_list_and_count()` | `devicetree.c:323-346` | **static，不出文件** | 取出 `list` 属性的指针和 u32 个数；被下面两个公共函数各调一次（`:363`、`:423`） |
| `pinctrl_count_index_with_args()` | `devicetree.c:357-369` | `drivers/pinctrl/devicetree.h:15-16` | 算出一共有几条记录 |
| `pinctrl_copy_args()` | `devicetree.c:383-403` | **static，不出文件** | 把第 `index` 条记录的 cell 拷进 `out_args` |
| `pinctrl_parse_index_with_args()` | `devicetree.c:416-434` | `devicetree.h:18-20` | 解析第 `index` 条记录 |

**五个里只有两个是"函数"意义上对外的，而这两个也只在私有头里声明**：`devicetree.h` 住在 `drivers/pinctrl/` 下，不在 `include/linux/` 下；两者也都**没有 `EXPORT_SYMBOL`**。按 skill 的判据（`EXPORT_SYMBOL*` **且** 公开头文件有声明），它们**不是对外 API**——树外模块既链接不到也 include 不到。这是 `05-usage.md` §6「符号存在≠读者能用」的一个实例。

另外三个是 `static`，连 `drivers/pinctrl/` 内部的其他文件都用不了。所以**这一整块只对 `drivers/pinctrl/` 里的少数几个驱动开放**（§6.3 的 2 个文件）。

`devicetree.h:34-46` 给这两个公共函数配了 `!CONFIG_OF` 的 `static inline` 桩（都 `return -ENODEV`），所以驱动可以无条件调用而不用自己包 `#ifdef CONFIG_OF`。

### §6.3 全树使用统计：只有 2 个消费者文件

| 文件 | 调用点 |
|---|---|
| `drivers/pinctrl/pinctrl-single.c` | `:1011`、`:1030`、`:1126`、`:1158` |
| `drivers/pinctrl/ti/pinctrl-ti-iodelay.c` | `:498`、`:528` |

`pinctrl_find_cells_size()` 全树 **0 个外部引用**（它是 `static`，只被同文件的 `pinctrl_count_index_with_args` / `pinctrl_parse_index_with_args` 调）。

重跑：
```bash
grep -rn 'pinctrl_count_index_with_args\|pinctrl_parse_index_with_args\|pinctrl_get_list_and_count' \
     drivers/ --include=*.c | grep -v drivers/pinctrl/devicetree.c
```

**arm64 相关性：低。** `pinctrl-single` 主要服务 TI OMAP/AM33xx（arm32）与 AM6x（arm64）。如果你不碰 TI 平台，本节可以整节跳过。

---

## §7 汇总

### §7.1 框架 × 职责 × 挂载点 × arm64

| 子框架 | 职责一句话 | 挂在 `03-internals.md` 时间线的哪一点 | Kconfig | arm64 相关 |
|---|---|---|---|---|
| §1 泛型 group 存储 | 运行期动态增删 group，替驱动实现三个读回调 | **注册之后、`pinctrl_enable()` 之前**（§A2 → §A3 之间）写入；状态应用时（§A11.1）被读；`pinctrl_unregister()`（§A17）自动清空 | `GENERIC_PINCTRL_GROUPS` | 高 |
| §2 泛型 function 存储 | 同上，对象是 function | 同上（§A2 → §A3 写入，§A11.1 读） | `GENERIC_PINMUX_FUNCTIONS` | 高 |
| §3 pinconf-generic | DT 标准属性名 → `configs[]` 打包值 | **§A5**（`pinctrl_dt_to_map()` 调驱动的 `dt_node_to_map`） | `GENERIC_PINCONF` | 极高 |
| §4 pinctrl-utils | map 数组的扩容/填充/释放样板 | 构造在 **§A5**；释放在 **§A16 / §B1.2**（持 `pinctrl_list_mutex`） | 无（`obj-y`） | 高（多为间接使用） |
| §5 GPIO 桥接 | gpio 号 ↔ pin 号翻译 + GPIO/mux 占用互斥 | **不在时间线上**——由 GPIO 子系统在运行期任意时刻发起，走 §A11.2 / §A15.2 的 GPIO 分支；range 注册发生在 gpiochip 注册时 | `GPIOLIB`（影响 §5.5 的桩） | 极高 |
| §6 `#pinctrl-cells` | `pinctrl-single` 家族的 phandle+cells 解析 | **§A5** 之内，由驱动的 `dt_node_to_map` 自行调用 | 无 | 低（只 TI） |

### §7.2 全树使用统计总表

| 子框架 | 代表 API | 消费者文件数 | 0 引用的符号 |
|---|---|---|---|
| §1 泛型 group | `pinctrl_generic_get_group_count` | 17 | `pinctrl_generic_group_name_to_selector`（0） |
| §2 泛型 function | `pinmux_generic_get_function_count` | 15 | 无 |
| §3 pinconf-generic | `pinconf_generic_dt_node_to_map` + 三个包装（合并去重） | **66** | 无 |
| §3.8 `custom_params`（§3 的可选加挂点） | `custom_params` / `num_custom_params` | **16**（其中 `custom_conf_items` 只 11） | 无 |
| §4 pinctrl-utils | `pinctrl_utils_free_map` | **62** | 无 |
| §5 GPIO 桥接 | `pinctrl_gpio_direction_output` | 22 | `pinctrl_add_gpio_ranges`（0） |
| §6 `#pinctrl-cells` | `pinctrl_parse_index_with_args` | **2** | `pinctrl_find_cells_size` / `pinctrl_get_list_and_count` / `pinctrl_copy_args`（三个都是 static，0 外部引用） |

统一重跑口径（"消费者文件数" = 出现该符号的 `.c` 文件数，排除定义所在文件；用 `-w` 避免前缀污染）：
```bash
count() { echo -n "$1: "; grep -rlnw "$1" drivers/ --include=*.c | grep -v "$2" | wc -l; }
count pinctrl_generic_get_group_count  drivers/pinctrl/core.c            # 17
count pinmux_generic_get_function_count drivers/pinctrl/pinmux.c         # 15
count pinctrl_utils_free_map           drivers/pinctrl/pinctrl-utils.c   # 62
count pinctrl_gpio_direction_output    drivers/pinctrl/core.c            # 22
count pinctrl_parse_index_with_args    drivers/pinctrl/devicetree.c      # 2
count custom_params                    drivers/pinctrl/pinconf-generic.c # 16
# §3 要合并四个名字去重，不能用上面这个单名口径（见 §3.7）
{ for f in pinconf_generic_dt_node_to_map{,_pin,_all,_group}; do
    grep -rlnw "$f" drivers/ --include=*.c; done; } \
  | grep -v drivers/pinctrl/pinconf-generic.c | sort -u | wc -l              # 66
```

**0 引用符号的处置**：都**留在文档里并显式标注**，不删。理由——它们是公开 EXPORT（`pinctrl_add_gpio_ranges`、`pinctrl_generic_group_name_to_selector`）或有完整 kerneldoc 的私有函数，读者搜到它们时需要知道"没人用"这个事实，否则会以为是自己漏了什么标准做法。

### §7.3 依赖边一览（打破"互不调用"的地方）

```
  §3 pinconf-generic ──调──► §4 pinctrl-utils     唯一的子框架间依赖
       reserve_map / add_map_mux / add_map_configs / free_map
       其中 pinconf_generic_dt_free_map() 的函数体只有一行转调（pinconf-generic.c:411-417）

  §5 GPIO 桥接 ──调──► 核心内部（不是子框架）
       pinmux_can_be_used_for_gpio / pinmux_request_gpio / pinmux_free_gpio
       pinmux_gpio_direction / pinconf_set_config      ← 都在私有头 pinmux.h / pinconf.h

  §1 ↔ §2 ↔ §6：互不调用，也不被其他子框架调用
```

由 §7.3 推出的一条实用结论：**`dt_free_map` 该填 `pinconf_generic_dt_free_map` 还是 `pinctrl_utils_free_map`？** 行为**完全一样**（前者只是转调后者）。选后者可以少一个 `CONFIG_GENERIC_PINCONF` 依赖——这正是 62 vs 19 这个差距的来源（§4.4 / §3.7）。文档和驱动里两种写法都能看到，**不是有一种是错的**。

### §7.4 一个"没有的东西"：pinctrl 没有 `struct bus_type`

本篇没有"子系统总线扩展"那一节，因为 pinctrl **不是总线型子系统**。两道门的实际输出登记在 `99-evidence.md` §1，结论见 `00-overview.md` §2（走路径 B）和 `02-datastructures.md` §1.2。

否定性断言的两道门：
```bash
grep -rn 'struct bus_type' drivers/pinctrl/ include/linux/pinctrl/    # → 无输出
grep -rn 'bus_type' output/pinctrl/                                   # → 只有"没有 bus_type"这类说明
```

---

## 下一步读什么

| 你想干的事 | 去哪篇 |
|---|---|
| 拿这些子框架写一个 pin controller 驱动 | `05-usage.md` §1、§3 |
| 搞清近邻符号（`add_map_configs` vs `add_config` 等） | `05-usage.md` §5 |
| 哪些符号"存在但读者用不了" | `05-usage.md` §6 |
| DTS 里那些标准属性怎么写、写错怎么定位 | `06-devicetree.md` |
| 从 debugfs 反查配置有没有生效 | `07-debug.md` §3 |
| 这些子框架挂在哪条时间线上的完整上下文 | `03-internals.md` Part A |
| 为什么 pinctrl 要设计成"核心抽象 + 消费者"而不是总线 | `08-feynman.md` |
