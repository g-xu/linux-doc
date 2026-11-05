---
title: 调试方法
---

# phy 07：调试与观测

## §0 分诊表：出问题先看哪个出口

⚠️⚠️ **先说最重要的一条**：**phy 框架的可观测性是本项目做过的子系统里最差的之一。**

| 出口 | phy | 参照（regmap） |
|---|---|---|
| **tracepoint** | **0**（连 `trace.h` 都没有） | 17 个事件 |
| **debugfs** | **0**（**整个 `drivers/phy/` 目录 0 个文件提到 `debugfs`**） | 692 行 |
| **框架级 sysfs 属性** | **0** | — |
| **故障注入** | **0** | 0（相同） |
| **`/sys/class/phy/`** | **有** ——**唯一的结构化观测面** | — |
| **dmesg** | **16 处打印** | 61 行打印 |

重跑：`grep -rl 'debugfs' drivers/phy/ | wc -l` ⇒ **0**；
`grep -rc 'trace_' drivers/phy/phy-core.c` ⇒ **0**。

### §0.1 按症状分诊

| 症状 | 第一个该看的出口 | 去 |
|---|---|---|
| **消费者 probe 返回 `-ENODEV`** | **DTS + 决策树**（框架无日志） | **06 §5** |
| **消费者永远 `-EPROBE_DEFER`** | **`/sys/kernel/debug/devices_deferred` + 查 `.owner`** | §2.3 / §5.2 |
| **PHY 实例造出来了吗** | **`ls /sys/class/phy/`** | §2.1 |
| **PHY 属于哪个 provider** | **`readlink /sys/class/phy/<name>/device`** | §2.2 |
| **上电/初始化失败** | **dmesg 里的四条 `phy … failed --> %d`** | §4.2 |
| **调用顺序错** | **dmesg：`phy_power_on was called before phy_init`** | §4.2 |
| **PHY 一直上电不掉** | **无出口** ——**只能读消费者代码看有没有 `phy_power_off`** | §3.2 |
| **regulator 报 unbalanced disables** | ⚠️ **线索指向 regulator，但根因在 phy 计数下溢** | §5.3 |
| **模式切换没生效** | **无出口** ——**`phy_get_mode()` 会骗你** | §3.2 |

---

## §1 编译期开关

### §1.1 Kconfig：没有任何调试专用选项

| 符号 | 类型 | 与调试的关系 |
|---|---|---|
| `GENERIC_PHY` | **`bool "PHY Core"`** | **关掉它 ⇒ 所有 API 变成空桩，返回 `-ENOSYS`**（§5.4） |
| `GENERIC_PHY_MIPI_DPHY` | `bool`（无 prompt） | — |

⚠️ **`drivers/phy/Kconfig` 里没有 `PHY_DEBUG`、没有 `DEBUG_FS` 相关的开关**
（重跑 `grep -in 'debug' drivers/phy/Kconfig` 无输出）。
⇒ **phy 框架完全没有"编译期打开更多诊断"这个选项。**

### §1.2 通用开关（不是 phy 特有，但对 phy 有用）

| 开关 | 作用 | 对 phy 的价值 |
|---|---|---|
| **`CONFIG_DYNAMIC_DEBUG`** | **打开 `dev_dbg` / `dev_vdbg`** | **phy 有 3 处 `dev_dbg` + 1 处 `dev_vdbg`**（§4.3） |
| **`initcall_debug` 启动参数** | **打印每个 initcall 的耗时** | **看 `phy_core_init` 与各驱动的先后**（03 §A1） |
| **`CONFIG_DEBUG_FS`** | **`/sys/kernel/debug/devices_deferred`** | **排 `-EPROBE_DEFER`**（§2.3） |
| **`CONFIG_PM_DEBUG` / `pm_runtime` 的 sysfs** | **`/sys/class/phy/<name>/power/`** | **看 phy 的 runtime 状态**（§2.2） |

⚠️ **`drivers/phy/` 里唯一的模块参数是一个厂商驱动的**：
**`drivers/phy/phy-xgene.c:548` 的 `module_param_named(preA3Chip, preA3Chip, int, 0444)`**
⇒ **框架本身 0 个模块参数。**

---

## §2 `/sys/class/phy/`：唯一的结构化出口

### §2.1 目录里有什么

**class 由 `phy_core_init()` 建**（`drivers/phy/phy-core.c:1233`），
**每个 phy 实例通过 `phy->dev.class = phy_class`（`:967`）+ `device_add()`（`:987`）出现在里面。**

```bash
### 有哪些 phy 实例
ls /sys/class/phy/
```

⚠️ **名字的构成规则是硬编码的**：
```c
/* drivers/phy/phy-core.c:973 */
	ret = dev_set_name(&phy->dev, "phy-%s.%d", dev_name(dev), id);
```
⇒ **`phy-<provider设备名>.<全局id>`**，**例如 provider 是 `ff770000.syscon` 时
名字形如 `phy-ff770000.syscon.3`。**
⚠️ **`id` 来自全局 `phy_ida`（`:957`）** ⇒ **它是系统内全局递增的，不是"这个 provider 的第几个"。**
⇒ **同一个 provider 的多个实例，id 不一定连续**（如果别的 provider 插在中间注册）。

### §2.2 每个实例下面能看什么

⚠️ **phy 框架自己没有加任何 sysfs 属性**
（重跑 `grep -c 'DEVICE_ATTR\|ATTRIBUTE_GROUPS\|dev_groups\|class_groups' drivers/phy/phy-core.c` ⇒ **0**）。
⇒ **下面这些全部来自驱动核心（`struct device` 通用），不是 phy 提供的**：

| 路径 | 来自 | 能看出什么 |
|---|---|---|
| `/sys/class/phy/<name>/device` | **`dev.parent`（`drivers/phy/phy-core.c:968`）** | **provider 是谁** ——**`readlink` 它就知道是哪个 platform 设备** |
| `/sys/class/phy/<name>/of_node` | **`dev.of_node`（`:969`）** | **对应的 DT 节点** ——**布局 B 下能看出是父节点还是子节点**（06 §2.2） |
| `/sys/class/phy/<name>/power/` | **PM runtime 通用属性** | **`runtime_status` / `runtime_active_time`** ——**但只在 `pm_runtime_enable` 真的执行过时有意义**（03 §A2.1） |
| `/sys/class/phy/<name>/subsystem` | 通用 | 指回 `phy` class |
| `/sys/class/phy/<name>/uevent` | 通用 | — |

⚠️ **看不到的东西（本节最要紧的一条）**：

| 想看 | 有没有 |
|---|---|
| **`init_count` / `power_count`** | **没有** ——**框架不导出** |
| **当前 `phy_mode`** | **没有** |
| **`phy->pwr` 那个 regulator 是谁** | **没有**（只能从 DT 的 `phy-supply` 反推，06 §4） |
| **有哪些消费者在用它** | ⚠️ **只能靠 device link** ——**`ls /sys/class/phy/<name>/consumer*`**（由 `device_link_add` 产生，03 §B5） |

**`consumer:`/`supplier:` 那两组符号链接是 phy 唯一能看出"谁在用"的地方**
（**来自 `device_link_add(dev, &phy->dev, DL_FLAG_STATELESS)`，
`drivers/phy/phy-core.c:766` / `:879` / `:926`**）。
⚠️ **用裸 `of_phy_get()` 拿的 phy 不建 link**（03 §B5）⇒ **那种消费者看不见。**

### §2.3 排 `-EPROBE_DEFER` 的专用出口

```bash
### 谁还在等
cat /sys/kernel/debug/devices_deferred
```
**这个文件由驱动核心提供**（`drivers/base/dd.c:346`），**不是 phy 的。**

⇒ **消费者出现在里面 + `/sys/class/phy/` 是空的 ⇒ PHY 驱动没 probe 成功。**
⇒ **消费者出现在里面 + `/sys/class/phy/` 里有实例 ⇒ 大概率是 `phy_ops` 漏了 `.owner`**
（`try_module_get(NULL)` ⇒ `-EPROBE_DEFER`，`drivers/phy/phy-core.c:761-762`，05 §7.5）。

**这两步是本篇最有价值的分诊动作**，**因为这两种情况的 dmesg 完全一样（都没有输出）。**

### §2.4 三个厂商驱动自己加了 sysfs 属性

**框架没有，但个别驱动有**（重跑 `grep -rl 'DEVICE_ATTR' drivers/phy/`）：

| 驱动 | 属性 | 位置 | 官方 ABI 文档 |
|---|---|---|---|
| `drivers/phy/renesas/phy-rcar-gen3-usb2.c` | **`role`（RW）** | `:397` | **有**：`Documentation/ABI/testing/sysfs-platform-phy-rcar-gen3-usb2` |
| `drivers/phy/ti/phy-twl4030-usb.c` | `vbus`（RO） | `:575` | 无 |
| `drivers/phy/broadcom/phy-brcm-usb.c` | **`dr_mode`（RO）+ `dual_select`（RW）** | `:238` / `:274` | 无 |

⚠️ **注意 ABI 文档给的路径是 `/sys/devices/platform/<phy-name>/role`** ——
**挂在 provider 的 platform 设备上，不在 `/sys/class/phy/` 下面。**
⇒ **驱动加的属性和框架的 class 目录是两个位置。**

---

## §3 tracepoint 与"没有出口"的地方

### §3.1 tracepoint：0 个

**`drivers/phy/` 下没有 `trace.h`，`phy-core.c` 里 0 处 `trace_*`。**
**本节即使结论是"没有"也必须写**（skill 规则 22 第 8 项的同类要求）。

⇒ **想追踪 phy 调用只能用通用 ftrace**：

```bash
cd /sys/kernel/debug/tracing

### 办法一：函数跟踪（能看到进了哪些框架函数）
echo 'phy_*' > set_ftrace_filter
echo function > current_tracer
echo 1 > tracing_on
cat trace_pipe

### 办法二：函数图（能看到分支和返回值路径）
echo phy_power_on > set_graph_function
echo function_graph > current_tracer

### 办法三：kprobe 抓返回值
echo 'r:phyget phy_get $retval' > kprobe_events
echo 1 > events/kprobes/phyget/enable
```

⚠️ **这三种办法本文档都没有实测**（99 §5.1）——**只是通用 ftrace 用法，不是 phy 特有的能力。**
⚠️ **`set_ftrace_filter` 写 `phy_*` 会同时命中网络 PHY 的函数**（那边也叫 `phy_*`，02 §1.0.A）
⇒ **需要用更精确的名字，比如 `phy_power_on` `phy_init` `_of_phy_get`。**

### §3.2 ⚠️ 三件事完全没有观测手段

| 想知道 | 为什么看不到 | 只能怎么办 |
|---|---|---|
| **`init_count` / `power_count` 现在是几** | **框架不导出到任何地方** | **加 kprobe 读 `struct phy` 字段，或改源码** |
| **硬件实际是什么模式** | **`phy_get_mode()` 读的是 `attrs.mode` 这个软状态**（`include/linux/phy/phy.h:238`），**而它在驱动没实现 `set_mode` 时也会被写**（`drivers/phy/phy-core.c:407-410`） | **读 PHY 寄存器**（厂商相关） |
| **`ops` 里哪些回调是 NULL** | **不导出** | **读驱动源码里的 `phy_ops` 初始化式** |

⇒ **"`phy_set_mode()` 返回 0 但没生效"这类问题，框架侧一点线索都给不了**（05 §8 / 08 §4）。

---

## §4 日志可见性等级

### §4.1 全部 16 处打印，按可见性分级

| 等级 | 数量 | 具体位置 | 默认可见？ |
|---|---|---|---|
| **`pr_err`** | **1** | `drivers/phy/phy-core.c:1235` | **是** |
| **`dev_err`** | **5** | `:264` `:301` `:345` `:383` `:959` | **是** |
| **`dev_warn`** | **2** | `:259` `:594` | **是** |
| **`dev_WARN`（带栈）** | **1** | `:753` | **是，且每次都打 + 带栈** |
| **`dev_WARN_ONCE`（带栈）** | **3** | `:693` `:1066` `:1209` | ⚠️ **每处只打一次** |
| **`dev_dbg`** | **3** | `:768` `:881` `:928`（三条字面相同） | **否，需 dyndbg** |
| **`dev_vdbg`** | **1** | `:1225` | **否，需 dyndbg + `-DVERBOSE_DEBUG`** |

重跑：`grep -n 'pr_err\|dev_err\|dev_warn\|dev_dbg\|dev_vdbg\|dev_WARN' drivers/phy/phy-core.c`
⇒ **16 行**。

⚠️ **`dev_WARN_ONCE` 的"once"是按代码位置算的，不是按设备**
⇒ **两个不同控制器犯同一个错，只有第一个会报**（与 regmap 07 §4.2 同一条规律）。

### §4.2 消息 → 含义（逐字取自源码）

| 消息 | 位置 | 含义 | 与 DT 有关吗 |
|---|---|---|---|
| `phy_power_on was called before phy_init` | `:259` | **调用顺序错**（`power_count > init_count`） | **无关** |
| `phy init failed --> %d` | `:264` | **`ops->init` 返回错误** | 无关 |
| `phy exit failed --> %d` | `:301` | `ops->exit` 返回错误 | 无关 |
| `phy poweron failed --> %d` | `:345` | `ops->power_on` 返回错误 | 无关 |
| `phy poweroff failed --> %d` | `:383` | `ops->power_off` 返回错误 | 无关 |
| `Requested PHY is disabled` | `:594` | **PHY 节点 `status = "disabled"`** | **有关**（06 §5 第 ⑤ 层） |
| `couldn't find PHY resource` | `:693` / `:1066` | **devm 与非 devm 混用** ——⚠️ **两条字面完全相同，只能靠栈区分** | 无关 |
| `missing string` | `:753` | **非 DT 路径下 `phy_get(dev, NULL)`** | **有关**（06 §0.2） |
| `failed to create device link to %s` | `:768` / `:881` / `:928` | **device link 建失败** ——**不算错误** | 无关 |
| `unable to get id` | `:959` | **`phy_ida` 分配失败** | 无关 |
| `couldn't find PHY provider device resource` | `:1209` | **provider 的 devm 混用** | 无关 |
| `releasing '%s'` | `:1225`（`dev_vdbg`） | **phy 设备最终释放** | 无关 |
| `failed to create phy class --> %ld` | `:1235`（`pr_err`） | **框架初始化失败** | 无关 |

⚠️ **只有两条消息与 DT 相关**（`Requested PHY is disabled` / `missing string`）
⇒ **06 §5 决策树里 5 层完全静默的结论就是从这张表得出的。**

### §4.3 打开隐藏的四条

```bash
### 三条 dev_dbg（device link 建失败）
echo 'file phy-core.c +p' > /sys/kernel/debug/dynamic_debug/control

### 一条 dev_vdbg 需要额外的 VERBOSE_DEBUG（要改 Makefile 重编译）
### drivers/phy/Makefile 里加 ccflags-y += -DVERBOSE_DEBUG
```

⚠️ **`dev_vdbg` 那条（`:1225` 的 `releasing '%s'`）在 dyndbg 下也不一定出来** ——
**它要求编译时定义 `VERBOSE_DEBUG`**（内核通用规则，本文档未实测，99 §5.3）。

### §4.4 一个 `WARN_ON`

**`drivers/phy/phy-core.c:950` 的 `if (WARN_ON(!dev)) return ERR_PTR(-EINVAL)`**
⇒ **`phy_create(NULL, …)` 会打栈。**
⚠️ **口径**：**`grep -c 'WARN_ON' drivers/phy/phy-core.c` 返回 4**，
**因为 `dev_WARN_ONCE` 里含同一个子串**（99 §6）——**裸 `WARN_ON(` 只有 1 处。**

⚠️ **框架里 0 个 `BUG_ON`**（重跑 `grep -c 'BUG_ON' drivers/phy/phy-core.c` ⇒ 0）
⇒ **和 regmap 的 12 个形成反差**：**phy 不会因为参数错而 panic。**

---

## §5 错误码分诊

### §5.1 普查（`phy-core.c` + `phy-core-mipi-dphy.c`）

| 码 | 次数 | 主要语义 | 拿到它先看什么 |
|---|---|---|---|
| `-EINVAL` | **31** | **参数非法 + NULL phy 传给 `configure`/`validate`** | **§5.2 表** |
| `-ENODEV` | **11** | **找不到 PHY** | **06 §5 决策树** |
| `-ENOTSUPP` | **11** | **PM runtime 没启用** | **基本见不到，框架吃掉了**（03 §B4.2） |
| `-ENOMEM` | 8 | 分配失败 | — |
| `-EPROBE_DEFER` | **7** | **provider 未就绪 / 模块引用拿不到** | **§2.3 的两步** |
| `-EOPNOTSUPP` | **2** | **驱动没实现 `configure`/`validate`** | **正常情况，跳过即可** |
| `-ENOSYS` | **0 在 `.c` 里，14+11 在头文件空桩里** | **`CONFIG_GENERIC_PHY=n`** | §5.4 |

重跑：`for e in EINVAL ENODEV ENOTSUPP ENOMEM EPROBE_DEFER EOPNOTSUPP ENOSYS; do echo -n "-$e "; grep -rho -- "-$e" drivers/phy/phy-core.c drivers/phy/phy-core-mipi-dphy.c | wc -l; done`

### §5.2 ⚠️`-EINVAL` 的三类来源

| 类别 | 出处 | 有日志吗 | 怎么认 |
|---|---|---|---|
| **① NULL phy 传给 `configure` / `validate`** | `drivers/phy/phy-core.c:508-509` / `:543-544` | **无** | ⚠️ **用了 `optional_get` 且板子上没 PHY**（01 §4.3 / 05 §4.2） |
| **② 非 DT 路径下 `string == NULL`** | `:753-754` | **有！`dev_WARN "missing string"` + 栈** | **看 dmesg** |
| **③ `phy_create(NULL, …)`** | `:950-951` | **有！`WARN_ON` 栈** | 看 dmesg |
| **④ `_full` 注册时 `children` 不是后代** | `:1112-1113` | **无** | **只有 Tegra 会碰到**（04 §5） |
| **⑤ 驱动自己返回的** | **`ops->set_mode` 等回调** | **视驱动** | **读驱动源码** |

⇒ **分诊第一步：dmesg 里有没有栈。**
**有栈 ⇒ 类别 ②③；没栈 ⇒ 类别 ①④⑤。**

### §5.3 ⚠️ 一个"线索指错子系统"的经典场景

**现象**：**dmesg 里 regulator 子系统报 unbalanced disables（或类似的引用计数警告）。**
**根因可能在 phy**：

```
消费者多调了一次 phy_power_off()
   │
   ├─ power_count 已经是 0 ⇒ if (power_count == 1) 不成立 ⇒ 不调 ops->power_off
   ├─ 但 --power_count 照样执行 ⇒ 变成 -1        (drivers/phy/phy-core.c:388)
   ├─ phy_pm_runtime_put(phy) 照样执行           (:390)
   └─ regulator_disable(phy->pwr) 照样执行       (:392-393)
         │
         ▼
   regulator 子系统发现 disable 次数超过 enable 次数 ⇒ 它自己打 WARN
```

⇒ **警告出现在 regulator，问题在 phy 消费者。**
**验证方法**：**在消费者驱动里数 `phy_power_on` / `phy_power_off` 的调用点，
包括错误路径和 suspend/resume。**
**框架不会替你查**（02 §1.0.D / 03 §C3）。

### §5.4 `CONFIG_GENERIC_PHY=n`：唯一"一眼认出"的错误码

| 特征 | 说明 |
|---|---|
| **错误码是 `-ENOSYS`** | **空桩段 `include/linux/phy/phy.h:286-535` 里 14 处 `return -ENOSYS` + 11 处 `ERR_PTR(-ENOSYS)`** |
| ⚠️ **dmesg 里一个字都没有** | **空桩 0 处 `WARN`**（与 regmap 的 45 处 `WARN_ONCE` 完全相反，01 §9.3） |
| **NULL phy 仍然返回 0** | **空桩保留了 NOP 语义**（`:288-290`） |

**一条命令确认**：`zcat /proc/config.gz | grep '^CONFIG_GENERIC_PHY'`
**或看 `/sys/class/phy/` 目录存不存在** ——**`CONFIG_GENERIC_PHY=n` 时它根本不会被创建。**

---

## §6 故障注入：本子系统没有

**结论先说**：**phy 框架不提供任何故障注入设施，连间接手段都没有。**

| 检查项 | 结果 | 重跑 |
|---|---|---|
| `fault_inject` / `should_fail` / `DECLARE_FAULT_ATTR` | **0** | `grep -rniE 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR' drivers/phy/` |
| debugfs 里的注入开关 | **0**（**整个目录 0 个 debugfs**） | `grep -rl 'debugfs' drivers/phy/` |
| 框架级模块参数 | **0** | `grep -rn 'module_param' drivers/phy/` ⇒ 只有 `phy-xgene.c:548` 一个厂商参数 |
| 可写的框架 sysfs 属性 | **0** | §2.2 |

⚠️ **对比 regmap**：**regmap 至少有两个可写 debugfs 文件能当"准注入"用
（`cache_only` / `cache_bypass`）；phy 一个都没有。**

**要注入 phy 相关故障，只有三条路（都要改代码或用 kprobe）**：

| 手段 | 做法 | 代价 |
|---|---|---|
| **DT 侧注入** | **把 PHY 节点改成 `status = "disabled"`** ⇒ **消费者拿到 `-ENODEV` + 一条 `dev_warn`** | **最干净**，且有日志（§4.2） |
| **DT 侧注入 2** | **删掉 `phys` 属性** ⇒ `-ENODEV`，无日志 | 简单但静默 |
| **kprobe 改返回值** | **`kretprobe` 让 `phy_power_on` 返回错误** | **本文档未实测**（99 §5.1） |

**第一条是本子系统最实用的一条"注入"**：**它同时覆盖了"PHY 不存在"这个真实场景。**

---

## §7 用户态工具

⚠️ **phy 没有字符设备、没有 ioctl、没有专用工具。**

| 工具 | 用途 | 注意 |
|---|---|---|
| **`ls` / `readlink` on `/sys/class/phy/`** | §2 | **唯一的结构化出口** |
| **`dtc -I fs /proc/device-tree/...`** | **确认属性真的进了 DTB** | 06 §6.2 |
| **`cat /sys/kernel/debug/devices_deferred`** | **排 `-EPROBE_DEFER`** | §2.3 |
| **`dmesg \| grep -i phy`** | §4 | ⚠️ **会同时命中网络 PHY 的消息**（02 §1.0.A） |
| **通用 ftrace / kprobe** | §3.1 | **未实测** |
| **`devmem2` 读 PHY 寄存器** | **绕过框架直接看硬件** | **唯一能验证"模式真的切了没"的办法**（§3.2） |
| **`lsmod` / `modinfo`** | **确认 PHY 驱动模块在不在** | **排 `.owner` 与模块引用问题**（§2.3） |

**"用 `devmem2` 读 PHY 寄存器验证"是本子系统最有价值的交叉验证手段**：
**因为 `phy_get_mode()` 和 `attrs` 全是软状态，框架侧无法证伪**（§3.2 / 02 §5.2）。

---

## §8 一页速查

```bash
### ① PHY 实例造出来了吗（名字 = phy-<provider设备名>.<全局id>）
ls /sys/class/phy/

### ② 这个 phy 属于哪个 provider / 哪个 DT 节点
readlink /sys/class/phy/<name>/device
readlink /sys/class/phy/<name>/of_node

### ③ 谁在用它（只有走 phy_get/devm_of_phy_get* 的消费者才有 link）
ls /sys/class/phy/<name>/ | grep consumer

### ④ PM runtime 状态（仅在 provider 启用了 PM runtime 时有意义）
cat /sys/class/phy/<name>/power/runtime_status

### ⑤ 谁还在等 provider（排 -EPROBE_DEFER）
cat /sys/kernel/debug/devices_deferred

### ⑥ dmesg 里的关键字（会混进网络 PHY 的消息）
dmesg | grep -iE 'phy (init|exit|poweron|poweroff) failed|Requested PHY is disabled|before phy_init|couldn.t find PHY'

### ⑦ 打开三条 dev_dbg
echo 'file phy-core.c +p' > /sys/kernel/debug/dynamic_debug/control

### ⑧ 确认框架编进去了
zcat /proc/config.gz | grep '^CONFIG_GENERIC_PHY'

### ⑨ DT 侧确认（属性真的进了 DTB 吗）
cat /proc/device-tree/<消费者节点>/phy-names | tr '\0' '\n'
ls /proc/device-tree/<PHY节点>/ | grep phy-cells
```

**本篇不给任何命令的输出示例** ——**本套文档未上机运行**（99 §5.1）。

⚠️ **三个"没有出口"要记住**（§3.2）：
**`init_count`/`power_count` 看不到 · 硬件实际模式看不到 · `ops` 缺哪些回调看不到。**

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| 为什么设计成这样 | `08-feynman.md` |
| DT 失效决策树 | `06-devicetree.md` §5 |
| 代码骨架与近邻符号 | `05-usage.md` |
| 错误码完整语义 | `01-api.md` §9 |
