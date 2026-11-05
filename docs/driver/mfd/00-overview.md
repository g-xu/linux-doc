---
title: 总览
---

# mfd 00：全景（三分钟版）

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇是**其余九篇的浓缩**，不引入任何新推导。每条结论后面标了详细去处。

---

## §1 一句话

**mfd = Multi-Function Device，把"一颗多功能芯片"拆成"多个 `platform_device`"的框架。**

⚠️ 存在的理由：一颗 PMIC 内部的 regulator / gpio / adc / 电源键分属六个不同子系统，
**每个子系统的注册接口都要一个自己的 `struct device`**
⇒ 一颗芯片必须变成十几个 device（08 §1）。

⚠️⚠️ **它不建 bus、不建 class，借 platform bus** ⇒ 核心只写"造 device"这一段（08 §2）。

---

## §2 规模：核心占全目录 0.56%

| 项 | 数字 |
|---|---|
| `drivers/mfd/` 总规模 | **237 个 `.c` / 112426 行** |
| ⚠️ **核心** | **1092 行** = `mfd-core.c`(471) + `core.h`(163) + `syscon.c`(378) + `syscon.h`(80) |
| 心脏函数 | `mfd_add_device()`（`drivers/mfd/mfd-core.c:170-334`，**165 行**） |
| 导出符号 | ⚠️ 核心 **6 个全是裸 `EXPORT_SYMBOL`** + syscon **7 个 `_GPL`** |
| 错误码种类 | ⚠️⚠️ **只有 2 种**：`-EAGAIN`(4) · `-ENOMEM`(3) |
| `Kconfig` | **2267 行 / 204 个 `config`** ⇒ 本套最大 |
| 观测设施 | ⚠️⚠️ debugfs / sysfs / tracepoint / uapi **全部为 0** |
| 官方框架文档 | ⚠️⚠️ **`Documentation/driver-api/mfd*` 不存在**；只有两篇 DT 绑定（`mfd.txt` 51 行 + `syscon.yaml` 118 行） |
| 设计债标注 | ⚠️ `FIXME`/`TODO`/`XXX` **0 处**（债的形态是"根本没写下来"，08 §7） |

⚠️ 头文件海：`include/linux/mfd/` 下 **231 个 `.h` / 72605 行**，
**其中 229 个是单芯片寄存器定义** ⇒ **只有 `core.h` 和 `syscon.h` 属于框架**（02 §1）。

---

## §3 三种形态，先分清自己在看哪一种

| 形态 | 谁决定有哪些子设备 | 要写驱动吗 | 详见 |
|---|---|---|---|
| ① `mfd_add_devices()` | ⚠️ **驱动里的 `mfd_cell[]` 数组** | 要 | 01 §2、05 §2 |
| ② `simple-mfd` | ⚠️ **DT 的子节点** | ⚠️ **不要** | 06 §3、08 §5 |
| ③ `syscon` | ⚠️ **不建子设备**，只交出一个共享 regmap | ⚠️ **不要** | 03 §6、05 §4 |

⚠️⚠️ **判据一句话**：
**子设备的寄存器在 CPU 物理地址空间里 ⇒ DT 描述得清 ⇒ `simple-mfd`（或 `syscon`）；
在芯片内部（要走 I2C/SPI 传输）⇒ DT 描述不出来 ⇒ 必须 `mfd_add_devices()`。**

⚠️ 数字上的痕迹：`drivers/mfd/` 里 **I2C 驱动 95 个，platform 驱动只有 48 个**
⇒ SoC 内部的多功能块大部分被 `simple-mfd` 吸收了（04 §1）。

⚠️⚠️ **形态 ③ 的使用面最大**：`syscon_regmap_lookup_by_phandle()` **198 个 `.c` 文件**，
超过 `mfd_add_devices()` 的 **134 个**
⇒ **这个目录里最常用的接口与"分家"无关**（04 §5、08 §6）。

---

## §4 全部对外接口

**核心 6 个**（`drivers/mfd/mfd-core.c`）：

| 接口 | 位置 | 说明 |
|---|---|---|
| `mfd_add_devices()` | `:372` | ⚠️ 主入口，7 个参数 |
| `devm_mfd_add_devices()` | `:468` | ⚠️ **默认选它**；但删不掉 `level = HIGH` 的 cell |
| `mfd_remove_devices()` | `:420` | 只删 `level <= NORMAL` |
| `mfd_remove_devices_late()` | `:412` | ⚠️ 删全部；**必须父驱动自己调** |
| `mfd_cell_enable()` / `_disable()` | `:47` / `:60` | ⚠️ 近乎死 API（2 个文件用） |
| `mfd_add_hotplug_devices()` | `core.h:149-154`（inline） | USB 桥片形态，6 个使用者 |

**syscon 7 个**（`drivers/mfd/syscon.c:221 :227 :236 :252 :275 :304 :322`）：
`syscon_node_to_regmap` · `device_node_to_regmap` · `_by_compatible` ·
`_by_phandle` · `_by_phandle_args` · `_by_phandle_optional` ·
⚠️⚠️ `of_syscon_register_regmap()` —— **全树零调用者**（04 §5、99 §6）。

---

## §5 `mfd_cell` 的 20 个字段里真正要填的

`include/linux/mfd/core.h:66-124`。⚠️ **常用的只有六个**（02 §2）：

```c
static const struct mfd_cell my_cells[] = {     /* ⚠️ 必须 static const */
        {
                .name           = "mychip-gpio",          /* platform 匹配名 */
                .of_compatible  = "vendor,mychip-gpio",   /* 找 DT 子节点 */
                .resources      = my_res,                 /* 相对偏移 / hwirq */
                .num_resources  = ARRAY_SIZE(my_res),     /* ⚠️ 核心不校验 */
                .platform_data  = &my_pdata,
                .pdata_size     = sizeof(my_pdata),       /* ⚠️ 必须配对 */
        },
};
```

⚠️ 另有 `level`（只 madera 用）· `of_reg`/`use_of_reg`（只 ocelot 用）·
`parent_supplies` · `swnode` · `ignore_resource_conflicts` · `pm_runtime_no_callbacks`。
⚠️⚠️ **`suspend` / `resume` 是死字段**（`core.h:74-75`）—— 填了不生效，用 `driver.pm`。

---

## §6 一条主流程

```
父驱动 probe
  ① devm_gpiod_get(enable)         上电
  ② devm_regmap_init_i2c()         ⚠️ 必须先建 —— 子驱动第一件事就是来取它
  ③ regmap_read(ID) 校验           ⚠️ 必须在分家之前
  ④ regmap_add_irq_chip()（可选）   一根 INT 脚展开成 irq_domain
  ⑤ devm_mfd_add_devices()         ⚠️⚠️ 必须是 probe 的最后一步
        │
        └─ 对每个 cell：platform_device_alloc → 资源换算 → of_node 匹配
                        → platform_device_add → ⚠️ kfree(res) 后核心退场
```

⚠️⚠️ **⑤ 之后核心完全不参与任何事**（`kfree(res)` 在 `drivers/mfd/mfd-core.c:311`）
⇒ **这就是"没有 debugfs"的原因：没有对象可展示**（07 §1、08 §4.1）。

**子驱动侧就是普通 platform_driver**，差别只在四样东西从哪来（05 §3）：

| 要什么 | 怎么拿 |
|---|---|
| 寄存器访问 | ⚠️ `dev_get_regmap(pdev->dev.parent, NULL)`（多 regmap 时传名字） |
| 内存资源 | `platform_get_resource()` ⚠️ **不要读 cell 里的 `resources`** |
| 中断 | `platform_get_irq_byname()`；⚠️ 父驱动没传 `domain` 时还要 `regmap_irq_get_virq()` |
| 平台数据 | `dev_get_platdata()` |

---

## §7 五条纪律（写驱动前背下来）

1. ⚠️⚠️ **`mfd_add_devices()` 放在 probe 最后一步** ——
   它返回时子设备可能已 probe 完，之后再校验芯片 ID 就来不及了（05 §2.2）。
2. ⚠️ **cell 数组写 `static const`** —— 核心只做浅拷贝，`resources` 指针指回原数组（03 §2.1）。
3. ⚠️ **`num_resources` 用 `ARRAY_SIZE`/`MFD_RES_SIZE`，`platform_data` 与 `pdata_size` 配对**
   —— 核心两者都不校验（01 §2.1.1、02 §8）。
4. ⚠️ **同 `of_compatible` 有多个子节点 ⇒ 必须 `use_of_reg = true` + `of_reg`**
   —— 否则身份随 DT 节点顺序互换（04 §4.1）。
5. ⚠️⚠️ **用了 `level = MFD_DEP_LEVEL_HIGH` ⇒ 不能用 devm 版本，
   remove 里手动调 `mfd_remove_devices()` → 自己的清理 → `mfd_remove_devices_late()`**（04 §3）。

---

## §8 五处硬伤（都没有任何注释或文档标注）

| # | 硬伤 | 后果 |
|---|---|---|
| 1 | ⚠️⚠️ `mfd_of_node_list` 全局链表**无锁**（`drivers/mfd/mfd-core.c:24`） | 并行 probe 下链表可能被并发改写 |
| 2 | ⚠️⚠️ `level` 机制是半成品 | devm 版永远删不掉 `HIGH`；部分失败回滚也不删；**唯一使用者 madera 自己就漏了**（04 §3.3） |
| 3 | ⚠️ disabled 子节点走失败路径却 `ret = 0`（`:234-238`） | **静默不注册** ⇒ "子设备不见了"的头号原因 |
| 4 | ⚠️ `mfd_cell.suspend`/`resume` 是死字段 | 填了不报错、不生效 |
| 5 | ⚠️ `mfd_cell_enable()` 的引用计数注释与实现不符（`core.h:126-131`） | 按注释写会重复触发回调 |

⚠️ syscon 侧另有两处：`of_syscon_register_regmap()` **零调用者**；
`syscon_probe()` 那条 platform 路径**在 DT 系统上从不执行**（07 §6.4）。

---

## §9 出问题了先看哪三样

⚠️⚠️ **mfd 的故障 80% 是静默的** ——
核心 471 行里只有 **4 条日志语句**，九条失败路径**一条都不打日志**（07 §2）。

```sh
# ① DT 的 status —— 头号原因
cat /proc/device-tree/<path>/status

# ② 子设备有没有被创建 + 是不是 mfd 建的
grep -l 'DEVTYPE=mfd_device' /sys/bus/platform/devices/*/uevent

# ③ 资源换算对不对（IRQ 数字能否在 /proc/interrupts 里查到）
cat /sys/bus/platform/devices/<dev>/resource; cat /proc/interrupts

# syscon：debugfs 目录名带 dummy- 前缀（因为 regmap 无宿主设备）
ls /sys/kernel/debug/regmap/ | grep '^dummy-'
```

---

## §10 十篇怎么读

| 你的身份 | 路线 |
|---|---|
| 只想知道这是什么 | **本篇** → 08-feynman.md |
| 要写一个 mfd 父驱动 | 05-usage.md → 02-datastructures.md → 06-devicetree.md |
| 要写一个 mfd 子驱动 | 05 §3 → 07 §5（先确认父驱动传没传 `domain`） |
| 只是要读写别人的 syscon | 05 §4 → 06 §4 |
| 要改 mfd 核心 | 03-internals.md → 01-api.md → 08 §7 |
| 现场故障排查 | 07-debug.md → 05 §6 |
| 要核对本套任何一个数字 | 99-evidence.md |
