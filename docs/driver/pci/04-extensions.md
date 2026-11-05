---
title: 扩展机制
---

# pci 04：扩展能力（`pcie/` 与 SR-IOV）

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

03 篇讲的是 19966 行核心；本篇讲挂在核心外面的那一层。
控制器驱动（`controller/`，57918 行）和热插拔（`hotplug/`，27753 行）不在本套范围（99 §1.3）。

---

## §1 边界：核心之外的东西是怎么组织的

`drivers/pci/` 总共 171 个 `.c` / 140042 行。核心只占 14%。剩下的按"怎么接入核心"分成三类：

| 接入方式 | 代表 | 特点 |
|---|---|---|
| ① 核心在枚举时直接调 `pci_xxx_init()` | `vpd.c` · `ats.c` · `ptm.c` · `rcec.c` · `iov.c` | ⚠️ 无独立驱动，是 `pci_init_capabilities()` 的一项 |
| ② 注册成 `pcie_port_service_driver` | `aer.c` · `pme.c` · `dpc.c` · 热插拔 | ⚠️⚠️ 走 `pcie_port_bus_type`，本篇 §2 |
| ③ 完全独立的库 | `p2pdma.c` · `doe.c` · `pci-bridge-emul.c` | 被别的子系统调用，与枚举无关 |

⚠️ `aspm.c` 是个例外：它 **既不是服务驱动，也不在 `pci_init_capabilities()` 里**，
而是由 `pci_bus_add_device()` 路径上的 `pcie_aspm_init_link_state()`（`drivers/pci/pcie/aspm.c:929`）驱动。
**看 ASPM 代码时先接受这一点，否则找不到入口。**

### §1.1 `pcie/` 的 12 个文件

| 文件 | 行数 | Kconfig | 作用 |
|---|---|---|---|
| `aer.c` | 1489 | `PCIEAER`（`pcie/Kconfig:24`） | 高级错误报告 |
| `aspm.c` | 1419 | `PCIEASPM`（`:63`，⚠️ `default y`） | 链路省电状态 |
| `portdrv_core.c` | 588 | `PCIEPORTBUS`（`:5`） | ⚠️⚠️ 服务模型的骨架 |
| `aer_inject.c` | 548 | `PCIEAER_INJECT`（`:33`） | 错误注入，测试用 |
| `pme.c` | 471 | `PCIE_PME`（`:113`） | 电源管理事件 |
| `dpc.c` | 409 | `PCIE_DPC`（`:117`） | 下游端口隔离 |
| `err.c` | 290 | 随 `PCIEAER` | ⚠️ 错误恢复流程的实现，§4 |
| `ptm.c` | 253 | `PCIE_PTM`（`:127`） | 精确时间测量 |
| `portdrv_pci.c` | 252 | `PCIEPORTBUS` | port 驱动本体 |
| `edr.c` | 246 | `PCIE_EDR`（`:136`） | ⚠️⚠️ 纯 ACPI，arm64 上裁掉（99 §2.7） |
| `rcec.c` | 190 | 随 `PCIEAER` | Root Complex Event Collector |
| `portdrv.h` | 145 | — | 服务号定义 |

⚠️ 官方文档在 `Documentation/PCI/` 下有对应的四篇：
`pciebus-howto.rst`（220 行，服务模型）·`pcieaer-howto.rst`（300 行）·
`pci-error-recovery.rst`（426 行）·`pci-iov-howto.rst`（171 行）。
**这四篇是本篇的一手依据，都读过（99 §3）。**

---

## §2 `portdrv`：一个端口拆成最多 5 个服务设备

### §2.1 为什么要拆

一个 PCIe Root Port 或 Switch Downstream Port 同时具备好几种能力：
PME、AER、原生热插拔、DPC、带宽通知。⚠️ **这些能力共享同一个 `pci_dev`，
但由完全不同的代码维护，而且往往共享同一根 MSI 中断。**

pci 的解法是再造一层总线：

```
pci_bus_type
  └─ pcie_portdriver（pci-driver，pci-driver 的普通用户）
       │  drivers/pci/pcie/portdrv_pci.c:195
       └─ pcie_port_bus_type          ⚠️⚠️ 第二条总线
            ├─ pcie_device(PME)   ← pcie_pme_driver     pme.c:454
            ├─ pcie_device(AER)   ← aerdriver           aer.c:1468
            ├─ pcie_device(DPC)   ← dpcdriver           dpc.c:398
            ├─ pcie_device(HP)    ← pciehp（hotplug/，不在范围）
            └─ pcie_device(BWNOTIF)
```

⚠️⚠️ **`pcie_port_bus_type` 是本树里第二个 `bus_type`**：
定义在 `drivers/pci/pci-driver.c:1724`，`EXPORT_SYMBOL_GPL` 在 `:1728`，
`bus_register()` 在 `pci_driver_init()` 里（`:1740`）。
02 §1 只讲了 `pci_bus_type`，这一个是本篇才出现的。

### §2.2 五个服务号

```c
/* drivers/pci/pcie/portdrv.h:15-26 */
#define PCIE_PORT_SERVICE_PME		(1 << 0)   /* Power Management Event */
#define PCIE_PORT_SERVICE_AER		(1 << 1)   /* Advanced Error Reporting */
#define PCIE_PORT_SERVICE_HP		(1 << 2)   /* Native Hotplug */
#define PCIE_PORT_SERVICE_DPC		(1 << 3)   /* Downstream Port Containment */
#define PCIE_PORT_SERVICE_BWNOTIF	(1 << 4)   /* Bandwidth notification */

#define PCIE_PORT_DEVICE_MAXSERVICES   5           /* :26 */
```

⚠️ 服务设备的名字由 `get_descriptor_id()`（`portdrv.h:108`）拼出来，
形如 `pcie0000:00:01.0`，这就是 `/sys/bus/pci_express/devices/` 下看到的东西（07 §5）。

### §2.3 中断怎么分

`portdrv_core.c` 里三个函数处理这件最麻烦的事：

| 函数 | 位置 | 干什么 |
|---|---|---|
| `pcie_message_numbers()` | `:45` | ⚠️ 从能力寄存器读出 PME / AER / DPC 各自的 MSI 号 |
| `pcie_port_enable_irq_vec()` | `:101` | 申请向量，处理"几个服务共用一个号" |
| `pcie_init_service_irqs()` | `:165` | 上面失败时回退到 INTx |

⚠️⚠️ `PCIE_PORT_MAX_MSI_ENTRIES` 是 32（`portdrv.h:106`），
但实际能拿到的往往只有 1 个 —— **多个服务共享同一中断是常态，不是异常**。
这意味着每个服务的中断处理函数都必须先确认"这次真是我的事"，见 §3.4。

### §2.4 服务驱动的注册面

```c
struct pcie_port_service_driver {                  /* portdrv.h:78 */
        int  (*probe)(struct pcie_device *dev);            /* :80 */
        void (*remove)(struct pcie_device *dev);           /* :81 */
        int  (*suspend)(struct pcie_device *dev);          /* :82 */
        int  (*resume_noirq)(struct pcie_device *dev);     /* :83 */
        int  (*resume)(struct pcie_device *dev);           /* :84 */
        int  (*runtime_suspend)(struct pcie_device *dev);  /* :85 */
        int  (*runtime_resume)(struct pcie_device *dev);   /* :86 */
        int  (*slot_reset)(struct pcie_device *dev);       /* :88 */
        ...
};
int  pcie_port_service_register(struct pcie_port_service_driver *new);   /* :98 */
void pcie_port_service_unregister(struct pcie_port_service_driver *new); /* :99 */
```

⚠️⚠️ **这两个函数虽然 `EXPORT_SYMBOL`（`portdrv_core.c:578`/`:588`），
但 `portdrv.h` 在 `drivers/pci/pcie/` 下，不在 `include/linux/`
⇒ 外部模块拿不到声明，等于不可用**（判据见 99 §2.1）。
`pcie_port_find_device()` 同样（`portdrv_core.c:465`，导出在 `:478`）。

### §2.5 三个命令行开关

```c
/* drivers/pci/pcie/portdrv_pci.c:23-36 */
bool pcie_ports_disabled;      /* :23  pci=pcie_ports=off  */
bool pcie_ports_native;        /* :30  pci=pcie_ports=native */
bool pcie_ports_dpc_native;    /* :36  pci=pcie_ports=dpc-native */
```

解析在 `pcie_port_setup()`（`:38`）。
⚠️ `pcie_ports_native` 的含义是"不管固件说什么，OS 自己接管"。
⚠️⚠️ **arm64 + DT 上这三个开关基本不需要动** ——
`native_*` 那些位（02 §4）在 DT 系统上本来就全是 1。
它们是给 ACPI `_OSC` 协商结果不理想的机器准备的。

### §2.6 初始化时机

```c
static int __init pcie_portdrv_init(void)   /* portdrv_pci.c:242 */
device_initcall(pcie_portdrv_init);         /* :252 */
```

⚠️ `pcie_init_services()`（`:234`）在这之前把 AER / PME / DPC 三个服务驱动依次注册：
`pcie_aer_init()`（`aer.c:1484`）· `pcie_pme_init()`（`pme.c:468`）· `pcie_dpc_init()`（`dpc.c:406`）。
⚠️⚠️ 三者都是 `int __init`，**由 `portdrv` 显式调用，不是各自的 `module_init`**
⇒ 顺序是硬编码的，改不了。

---

## §3 AER：1489 行里真正要理解的四段

### §3.1 对外接口（外部驱动能用的）

| 接口 | 定义 | 导出 | 声明 |
|---|---|---|---|
| `pci_enable_pcie_error_reporting()` | `aer.c:230` | `:240` `_GPL` | `include/linux/aer.h` |
| `pci_disable_pcie_error_reporting()` | `aer.c:242` | `:252` `_GPL` | 同上 |
| `pci_aer_clear_nonfatal_status()` | `aer.c:254` | `:271` `_GPL` | 同上 |
| `aer_recover_queue()` | `aer.c:1027` | `:1045` `_GPL` | ⚠️ 给 APEI/GHES 用 |
| `cper_severity_to_aer()` | `aer.c:759` | `:770` `_GPL` | ⚠️ 同上，ACPI 侧 |

⚠️ 核心内部用的还有 `pci_aer_init()`（`:375`）/ `pci_aer_exit()`（`:402`）/
`pci_save_aer_state()`（`:331`）/ `pci_restore_aer_state()`（`:353`）/
`pci_aer_clear_fatal_status()`（`:273`）/ `pci_aer_raw_clear_status()`（`:298`）/
`pci_aer_clear_status()`（`:323`）/ `pcie_aer_is_native()`（`:220`）。

⚠️⚠️ **`aer_recover_queue()` 和 `cper_severity_to_aer()` 的调用方在 ACPI 的 GHES 里
⇒ arm64 上如果没有 APEI 固件，这两个导出没有使用者**（99 §2.7）。

### §3.2 错误信息怎么变成那几行日志

五张字符串表决定了 `dmesg` 里 AER 消息的样子：

| 表 | 位置 |
|---|---|
| `aer_error_severity_string[]` | `aer.c:446` |
| `aer_error_layer[]` | `aer.c:452` |
| `aer_correctable_error_string[]` | `aer.c:458` |
| `aer_uncorrectable_error_string[]` | `aer.c:493` |
| `aer_agent_string[]` | `aer.c:528` |

打印路径：`aer_print_error()`（`:709`）→ `__aer_print_error()`（`:682`）
→ `__print_tlp_header()`（`:675`）。
⚠️ `aer_print_port_info()`（`:746`）打印的是"哪个端口报的"，
`cper_print_aer()`（`:772`）是 ACPI 侧的等价物。

⚠️⚠️ **排查时先看 severity 和 agent 两项**：
`agent_string` 区分是 Requester / Completer / Transmitter / Receiver，
这一项直接决定"是本设备的问题还是对端的问题"。

### §3.3 统计计数：sysfs 里那一堆文件的来源

`aer_stats` 结构在 `aer.c:53`，属性表在 `:589`（`__ro_after_init`）。
计数入口两处：`pci_dev_aer_stats_incr()`（`:623`）和 `pci_rootport_aer_stats_incr()`（`:656`）。

⚠️ 这就是 `/sys/bus/pci/devices/*/aer_dev_correctable` 等文件的实现（07 §5.3）。

### §3.4 中断到恢复：找错误源那一段

```
aer_irq()          aer.c:1205   ⚠️ 硬中断，只把错误源塞进环形缓冲
   ↓
aer_isr()          aer.c:1184   线程化处理
   ↓
aer_isr_one_error() aer.c:1131
   ↓
find_source_device()  aer.c:919  ⚠️⚠️ 关键：报错的端口 ≠ 出错的设备
   ├─ find_device_iter()  :888
   ├─ is_error_source()   :834
   └─ add_error_device()  :819
   ↓
handle_error_source()  aer.c:957
   ↓
pcie_do_recovery()     err.c:203    §4
```

⚠️⚠️ **`find_source_device()` 是 AER 里最容易出问题的一环**：
AER 中断由 Root Port 收到，但真正出错的可能是它下面任意一层的设备。
`is_error_source()`（`:834`）靠比对 Requester ID 和各设备的错误状态寄存器来判断。
**匹配不上时内核会打 `can't find device of ID%04x` 一类消息，
此时恢复流程不会跑。**

⚠️ 根端口的使能/禁用在 `aer_enable_rootport()`（`:1268`）/ `aer_disable_rootport()`（`:1309`），
下游设备的错误报告开关在 `set_device_error_reporting()`（`:1226`）和
`set_downstream_devices_error_reporting()`（`:1249`）。

### §3.5 ECRC：默认关着的一项

`enable_ecrc_checking()`（`:142`）/ `disable_ecrc_checking()`（`:166`）/
`pcie_set_ecrc_checking()`（`:185`）/ `pcie_ecrc_get_policy()`（`:205`），
策略字符串表在 `:130`，`Kconfig` 是 `PCIE_ECRC`（`pcie/Kconfig:51`）。
⚠️ 端到端 CRC 会占带宽，所以默认跟随固件（`ECRC_POLICY_DEFAULT`，`:128`）。

---

## §4 错误恢复：`err.c` 的 290 行是整套 `pci_error_handlers` 的唯一实现

02 §8 讲了那 6 个回调的声明，这里是它们被调用的地方。

### §4.1 五个广播阶段

```c
pci_ers_result_t pcie_do_recovery(struct pci_dev *dev,          /* err.c:203 */
                pci_channel_state_t state,
                pci_ers_result_t (*reset_subordinates)(struct pci_dev *pdev))
```

| 阶段 | 广播函数 | 位置 | 调的驱动回调 |
|---|---|---|---|
| 0 | `pci_pm_runtime_get_sync` | `:229` | ⚠️ 先把设备从 runtime suspend 里唤醒 |
| 1 | `report_frozen_detected` / `report_normal_detected` | `:233` / `:239` | `error_detected()` |
| 2 | `report_mmio_enabled` | `:245` | `mmio_enabled()` |
| 3 | `report_slot_reset` | `:256` | `slot_reset()` |
| 4 | `report_resume` | `:263` | `resume()` |

实现分别在 `err.c:101`（frozen）· `:106`（normal）· `:117`（mmio_enabled）·
`:138`（slot_reset）· `:159`（resume），公共部分在 `report_error_detected()`（`:49`）。

### §4.2 阶段之间的跳转规则

```c
if (status == PCI_ERS_RESULT_CAN_RECOVER) {        /* err.c:242 */
        status = PCI_ERS_RESULT_RECOVERED;
        pci_walk_bridge(bridge, report_mmio_enabled, &status);   /* :245 */
}
if (status == PCI_ERS_RESULT_NEED_RESET) {         /* :248 */
        status = PCI_ERS_RESULT_RECOVERED;
        pci_walk_bridge(bridge, report_slot_reset, &status);      /* :256 */
}
if (status != PCI_ERS_RESULT_RECOVERED)           /* :259 */
        goto failed;
pci_walk_bridge(bridge, report_resume, &status);   /* :263 */
```

⚠️⚠️ **每个阶段前都把 `status` 重置成 `RECOVERED`，再让各驱动往下压。**
这就是 `merge_result()`（`:24`）的用途：多个驱动返回不同结果时取"最悲观"的那个。
⚠️ 所以一个驱动返回 `PCI_ERS_RESULT_DISCONNECT`，整棵子树的恢复就失败。

### §4.3 恢复范围的确定

```c
/* err.c:221-227 */
if (type == PCI_EXP_TYPE_ROOT_PORT ||
    type == PCI_EXP_TYPE_DOWNSTREAM ||
    type == PCI_EXP_TYPE_RC_EC ||
    type == PCI_EXP_TYPE_RC_END)
        bridge = dev;
else
        bridge = pci_upstream_bridge(dev);
```

⚠️ 注释（`:212-220`）说清了规则：**端口自己报错 ⇒ 端口加它下面所有设备一起恢复；
端点报错 ⇒ 它的上游端口下面所有设备一起恢复。**
⚠️⚠️ 后果：**同一个端口下的无辜设备也会被 reset。**
这是 `pci-error-recovery.rst` 反复强调的一点，也是驱动必须实现 `slot_reset()` 的原因。

### §4.4 一处 TODO

```c
/* err.c:249-253 */
        /*
         * TODO: Should call platform-specific
         * functions to reset slot before calling
         * drivers' slot_reset callbacks?
         */
```

⚠️ 这是整个 `pcie/` 目录里少数明写的设计债：**slot reset 之前没有平台钩子。**
需要平台特定复位的 arm64 控制器只能在自己的 `reset_link` 里做。

### §4.5 使用面

⚠️ 全树 `err_handler` 出现在 **143 个文件**里 ——
相对于 362 个用 `pci_register_driver()` 的文件，
**大约 40% 的 PCI 驱动实现了错误恢复。** 这个比例比预期高，
原因是 `err_handler` 里只填一两个回调也算实现。

---

## §5 ASPM：1419 行，全部在算延迟

### §5.1 它管什么

链路空闲时把 PHY 关掉一部分省电，L0s / L1 / L1.1 / L1.2 四档。
⚠️⚠️ **代价是唤醒延迟**，而端点会在能力寄存器里声明"我最多能忍多少延迟"。
ASPM 的全部复杂度就来自这一条约束的计算。

```c
static void pcie_aspm_check_latency(struct pci_dev *endpoint)   /* aspm.c:409 */
```

上游每一跳的退出延迟都要累加，和端点声明的可接受值比。
计算函数四个：`calc_l0s_latency()`（`:319`）· `calc_l0s_acceptable()`（`:329`）·
`calc_l1_latency()`（`:337`）· `calc_l1_acceptable()`（`:347`）。
L1 子状态另有 `calc_l1ss_pwron()`（`:355`）和 `encode_l12_threshold()`（`:377`）。

### §5.2 入口和链路对象

| 函数 | 位置 | 时机 |
|---|---|---|
| `pcie_aspm_init_link_state()` | `aspm.c:929` | ⚠️ 枚举后，由 `pci_bus_add_device()` 路径触发 |
| `alloc_pcie_link_state()` | `:875` | 每条链路一个 `pcie_link_state`（`:45`） |
| `pcie_aspm_cap_init()` | `:638` | 读能力、算出可用状态 |
| `aspm_l1ss_init()` | `:581` | L1 子状态 |
| `pcie_aspm_exit_link_state()` | `:1018` | 设备移除时 |
| `free_link_state()` | `:834` | — |

⚠️ `pcie_link_state` 是按 **链路** 而不是按设备组织的，
`pci_function_0()`（`:99`）用来找链路上的代表设备 ——
**同一链路的多个 function 必须配成一致的 ASPM 状态，这是规范要求。**

### §5.3 配置的三层

```
pcie_config_aspm_path()   aspm.c:826   ⚠️ 从根到端点整条路径
   ↓
pcie_config_aspm_link()   aspm.c:775   一条链路
   ↓
pcie_config_aspm_dev()    aspm.c:769   一个设备的寄存器
pcie_config_aspm_l1ss()   aspm.c:716   L1 子状态寄存器
```

⚠️⚠️ **必须整条路径一起改**：中间某一跳的 L1 没开，端点开了也没用；
反过来中间开了而端点没开，会出现延迟超标。这是 `_path` 这一层存在的唯一原因。

### §5.4 公共时钟与重训练

`pcie_aspm_configure_common_clock()`（`:250`）会改链路配置，
之后必须重训练：`pcie_retrain_link()`（`:213`）→ `pcie_wait_for_retrain()`（`:196`）。

⚠️⚠️ **重训练是会失败的**，而且失败时链路可能降速或彻底不通。
arm64 板子上"插上卡就掉链接"的一类问题，
先用 `pcie_aspm=off` 排除这条路径，再看控制器驱动。

### §5.5 策略与开关

| 面 | 位置 |
|---|---|
| `aspm_policy` 默认值 | ⚠️ 由 `CONFIG_PCIEASPM_*` 决定（`aspm.c:77`–`:83`） |
| `policy_str[]` | `:86` |
| 运行时改策略 | `module_param_call(policy, ...)`（`:1222`），读写在 `:1184`/`:1210` |
| `pcie_aspm=off` | `pcie_aspm_disable()`（`:1386`） |
| 驱动强制关某状态 | `pci_disable_link_state()` `:1178`（导出 `:1182`） |
| 已持锁版本 | `pci_disable_link_state_locked()` `:1163`（导出 `:1167`） |
| 查询 | `pcie_aspm_enabled()` `:1234`（导出 `:1243` `_GPL`） |
| 核心内部禁用 | `pcie_no_aspm()` `:1402`，`pcie_aspm_support_enabled()` `:1416` |
| sysfs 属性组 | `aspm_ctrl_attr_group` `:1380`，可见性判定 `:1356` |

⚠️ 四个 `Kconfig` 默认策略互斥：`PCIEASPM_DEFAULT`（`pcie/Kconfig:85`）·
`POWERSAVE`（`:91`）· `POWER_SUPERSAVE`（`:98`）· `PERFORMANCE`（`:106`）。
⚠️⚠️ `PCIEASPM` 本身是 `default y`（`pcie/Kconfig:65`），
**arm64 defconfig 上 ASPM 是开着的** —— 这与很多人的直觉相反。

### §5.6 一个只有 12 行但很重要的函数

```c
static void pci_clear_and_set_dword(struct pci_dev *pdev, int pos,
                                    u32 clear, u32 set)      /* aspm.c:477 */
```

⚠️ ASPM 全程用它做读-改-写。
它 **不加锁** ——依赖的是 `aspm_lock`（`aspm.c:68`）在更外层持有。
改这个文件时这一点最容易漏。

---

## §6 DPC 与 EDR

### §6.1 DPC 做什么

下游端口检测到不可纠正错误时，**硬件立刻把链路断开**，
防止坏数据继续往上游流。之后由软件决定要不要恢复。

| 函数 | 位置 | 说明 |
|---|---|---|
| `dpc_irq()` | `dpc.c:308` | 硬中断 |
| `dpc_handler()` | `dpc.c:296` | 线程化，调 `dpc_process_error()` |
| `dpc_process_error()` | `dpc.c:263` | ⚠️ 读原因、打日志 |
| `dpc_process_rp_pio_error()` | `dpc.c:187` | RP PIO 类错误的细分（字符串表 `:21`） |
| `dpc_reset_link()` | `dpc.c:145` | ⚠️⚠️ 作为 `reset_subordinates` 传给 `pcie_do_recovery()` |
| `dpc_wait_rp_inactive()` | `dpc.c:127` | 等硬件退出 DPC 状态 |
| `dpc_completed()` | `dpc.c:78` | 判定 |
| `pci_dpc_recovered()` | `dpc.c:100` | ⚠️ 给复位路径查询"是否已恢复" |
| `pci_dpc_init()` | `dpc.c:325` | 枚举期初始化 |
| `dpc_probe()` / `dpc_remove()` | `dpc.c:352` / `:388` | 服务驱动 |
| `dpcdriver` | `dpc.c:398` | — |
| 状态保存 | `pci_save_dpc_state()` `:43` / `pci_restore_dpc_state()` `:59` | — |

⚠️⚠️ **DPC 与 AER 的关系**：DPC 触发后链路已断，
AER 读不到对端的错误详情。所以 `dpc_get_aer_uncorrect_severity()`（`dpc.c:241`）
要在复位前把 AER 状态抢先读出来。**顺序反了就丢失错误信息。**

### §6.2 EDR 在 arm64 上裁掉

`edr.c`（246 行）全部依赖 ACPI：
`acpi_enable_dpc()`（`:28`）· `acpi_dpc_port_get()`（`:76`）·
`acpi_send_edr_status()`（`:132`）· `edr_handle_event()`（`:151`）·
`pci_acpi_add_edr_notifier()`（`:212`）· `pci_acpi_remove_edr_notifier()`（`:236`）。

⚠️⚠️ **EDR 的整个机制是"固件先接管 DPC，出事后通过 ACPI 通知 OS"。
DT 系统没有这条通路 ⇒ 本篇不再展开，记入 99 §2.7。**

---

## §7 PME：唤醒事件

| 函数 | 位置 | 说明 |
|---|---|---|
| `pcie_pme_irq()` | `pme.c:264` | 硬中断 |
| `pcie_pme_work_fn()` | `pme.c:213` | ⚠️ 工作队列，真正处理 |
| `pcie_pme_handle_request()` | `pme.c:129` | 按 Requester ID 找设备 |
| `pcie_pme_walk_bus()` | `pme.c:69` | ⚠️ ID 找不到时逐个走总线 |
| `pcie_pme_from_pci_bridge()` | `pme.c:101` | 桥后面的老式 PCI 设备 |
| `pcie_pme_interrupt_enable()` | `pme.c:53` | 开关根端口的 PME 中断 |
| `pcie_pme_mark_devices()` | `pme.c:310` → `pcie_pme_can_wakeup()` `:296` | 标记可唤醒 |
| `pcie_pme_probe()` / `_remove()` | `pme.c:324` / `:444` | 服务驱动 |
| `pcie_pme_suspend()` / `_resume()` | `pme.c:390` / `:421` | ⚠️ 挂起时要判断是否留着当唤醒源 |
| `pcie_pme_check_wakeup()` | `pme.c:361` | — |
| `pcie_pme_driver` | `pme.c:454` | — |

⚠️⚠️ **`pcie_pme_walk_bus()` 的存在说明 Requester ID 经常是不可靠的**：
规范允许设备报 0，或者桥后面的老式 PCI 设备根本没有 PCIe ID。
代码只能退化成"把整条总线上标了可唤醒的设备都通知一遍"。

⚠️ `pcie_pme_msi_disabled`（`pme.c:30`）+ `pcie_pme_setup()`（`:32`）
是 `pcie_pme=nomsi` 开关；`portdrv_pci.c:210` 的
`dmi_pcie_pme_disable_msi()` 是 DMI 黑名单 ——
**这个黑名单机制在 arm64 上不生效（没有 DMI）。**

---

## §8 PTM 与 RCEC

### §8.1 PTM：唯一一组"给普通驱动用"的扩展接口

| 接口 | 定义 | 导出 |
|---|---|---|
| `pci_enable_ptm()` | `ptm.c:168` | `:199` ⚠️ 裸 `EXPORT_SYMBOL` |
| `pci_disable_ptm()` | `ptm.c:220` | `:227` 裸 |
| `pcie_ptm_enabled()` | `ptm.c:246` | `:253` 裸 |

内部：`pci_ptm_init()`（`:39`）· `__pci_enable_ptm()`（`:122`）·
`__pci_disable_ptm()`（`:201`）· `pci_upstream_ptm()`（`:16`）·
状态保存 `pci_save_ptm_state()`（`:87`）/ `pci_restore_ptm_state()`（`:104`）·
`pci_suspend_ptm()`（`:233`）/ `pci_resume_ptm()`（`:240`）。

⚠️⚠️ **PTM 有层级要求**：上游必须先使能，`pci_upstream_ptm()`（`:16`）就是查这个。
使用者主要是网卡（时间同步）。

### §8.2 RCEC：只在 Root Complex Integrated Endpoint 场景出现

`rcec.c` 190 行，核心是 `walk_rcec()`（`:68`）和它的两个 helper
（`link_rcec_helper()` `:41`、`walk_rcec_helper()` `:56`），
对外是 `pcie_link_rcec()`（`:110`）和 `pcie_walk_rcec()`（`:134`），
初始化/退出 `pci_rcec_init()`（`:149`）/ `pci_rcec_exit()`（`:186`）。
关联判定在 `rcec_assoc_rciep()`（`:24`）。

⚠️ 它解决的问题：RCiEP（集成在根复合体里的端点）没有上游端口，
AER 事件由一个单独的 RCEC 设备收集。**arm64 SoC 上内置的 PCIe 设备属于这一类，
但绝大多数平台没有实现 RCEC 能力 ⇒ 这 190 行在多数板子上不执行。**

---

## §9 SR-IOV：`iov.c` 1234 行

### §9.1 对外的 10 个导出

| 接口 | 定义 | 导出 |
|---|---|---|
| `pci_enable_sriov()` | `iov.c:1071` | `:1080` `_GPL` |
| `pci_disable_sriov()` | `iov.c:1086` | `:1095` `_GPL` |
| `pci_iov_virtfn_devfn()` | `iov.c:27` | `:34` `_GPL` |
| `pci_iov_vf_id()` | `iov.c:36` | `:48` `_GPL` |
| `pci_iov_get_pf_drvdata()` | `iov.c:66` | `:77` `_GPL` |
| `pci_num_vf()` | — | `:1110` `_GPL` |
| `pci_vfs_assigned()` | — | `:1151` `_GPL` |
| `pci_sriov_set_totalvfs()` | — | `:1181` `_GPL` |
| `pci_sriov_get_totalvfs()` | — | `:1198` `_GPL` |
| `pci_sriov_configure_simple()` | — | `:1234` `_GPL` |

⚠️ 共 10 个，**全部 `_GPL`**。
⚠️⚠️ `pci_sriov_configure_simple()`（导出 `:1234`）是给"没有特殊需求"的驱动直接填进
`pci_driver.sriov_configure` 的现成实现 —— **写 PF 驱动时先考虑它，别自己写一遍。**

### §9.2 使能一次 VF 发生了什么

```
sriov_numvfs_store()   iov.c:416     ⚠️ 入口是 sysfs，不是驱动 API
   ↓
pci_driver.sriov_configure()          ⚠️⚠️ 驱动自己的回调（02 §5）
   ↓
pci_enable_sriov()     iov.c:1071
   ↓
sriov_enable()         iov.c:601
   ├─ 写 NumVFs 寄存器
   ├─ virtfn_add_bus()      iov.c:123   ⚠️ VF 可能需要额外的总线号
   └─ sriov_add_vfs()       iov.c:577
        └─ pci_iov_add_virtfn()  iov.c:289
             ├─ pci_alloc_dev / pci_setup_device 之类的等价流程
             ├─ pci_read_vf_config_common()  iov.c:157
             └─ pci_iov_sysfs_link()         iov.c:180
```

⚠️⚠️ **入口是 sysfs 的 `sriov_numvfs`，不是内核 API。**
驱动只提供 `sriov_configure` 回调（全树 76 个文件实现）。
`pci_enable_sriov()` 由驱动在这个回调里调 —— **顺序是 用户态 → 核心 → 驱动 → 核心。**

### §9.3 资源：VF 的 BAR 是一段连续区间

| 函数 | 位置 | 说明 |
|---|---|---|
| `sriov_init()` | `iov.c:745` | ⚠️ 枚举期读 VF BAR，算总需求 |
| `pci_iov_resource_size()` | `iov.c:149` | 单个 VF 的 BAR 大小 |
| `pci_iov_update_resource()` | `iov.c:949` | 资源分配后回写 |
| `pci_sriov_resource_alignment()` | `iov.c:1016` | 对齐要求 |
| `pci_iov_bus_range()` | `iov.c:1049` | ⚠️⚠️ 03 §2.2 里预留总线号用的就是这个 |
| `compute_max_vf_buses()` | `iov.c:101` | — |

⚠️⚠️ **PF 的 `resource[]` 数组会因为 `CONFIG_PCI_IOV` 变长**（02 §2.6 那条警告）：
`PCI_IOV_RESOURCES`（`include/linux/pci.h:105`）到 `PCI_IOV_RESOURCE_END`（`:106`）
这一段槽位就是 VF BAR 的总需求。
**这意味着开了 SR-IOV 的内核里，桥窗口需求会明显变大。**

### §9.4 两个 `__weak` 钩子

`pcibios_sriov_enable()`（`iov.c:567`）· `pcibios_sriov_disable()`（`:572`）·
`pcibios_iov_resource_alignment()`（`:1000`）。
⚠️ arm64 上三个都是默认实现（前两个返回 0）。**powerpc 才需要覆盖它们。**

### §9.5 VF 侧

`pci_vf_drivers_autoprobe()`（`iov.c:1036`）对应 sysfs 的
`sriov_drivers_autoprobe`（读写在 `:507`/`:516`）。
⚠️ 关掉它可以"创建了 VF 但不自动绑驱动"，这是给 VFIO 直通准备的常规做法。

⚠️ VF 的 MSI-X 数量可以由 PF 分配：`sriov_vf_msix_count_store()`（`iov.c:223`）
和 `sriov_vf_total_msix_show()`（`:205`），属性组在 `:284`。

---

## §10 其余扩展：一表带过

| 文件 | 行数 | 导出数 | 做什么 | 本套处理 |
|---|---|---|---|---|
| `ats.c` | 508 | 9 | ATS / PRI / PASID 三组能力 | ⚠️ 见下 |
| `p2pdma.c` | 989 | 12 | 设备间直接 DMA | 不展开（使用者是 nvme / rdma） |
| `vpd.c` | 596 | 8 | Vital Product Data | 07 §5 有 sysfs 面 |
| `doe.c` | 554 | 3 | Data Object Exchange（CXL 用） | 不展开 |
| `vc.c` | 428 | ⚠️⚠️ **0** | Virtual Channel | ⚠️ 只被核心内部调 |
| `ecam.c` | 233 | — | ⚠️ 通用 ECAM 配置访问 | ⚠️⚠️ arm64 关键，放 06 §5 |
| `slot.c` | 383 | — | `pci_slot` 对象 | 07 §5 |
| `rom.c` | 197 | — | Option ROM 读取 | 07 §5 |
| `quirks.c` | 6191 | — | 设备特例修补 | ⚠️ 不在范围（99 §1.3） |
| `pci-acpi.c` | 1518 | — | ACPI 集成 | ⚠️⚠️ 裁掉（99 §2.7） |

### §10.1 `ats.c` 的三组接口

IOMMU 相关，`arm-smmu-v3` 会用到：

| 组 | 主要接口 | 定义 |
|---|---|---|
| ATS | `pci_ats_supported()` `:40`（导出 `:47`）· `pci_enable_ats()` `:56`（`:88`）· `pci_disable_ats()` `:94`（`:107`） | `pci_ats_init()` `:19` |
| PRI | `pci_enable_pri()` `:196` · `pci_disable_pri()` `:242`（导出 `:263`）· `pci_pri_supported()` `:336`（`:343`） | `pci_pri_init()` `:175` |
| PASID | `pci_enable_pasid()` `:361`（导出 `:401`）· `pci_disable_pasid()` `:407`（`:426`）· `pci_pasid_features()` `:460`（`:478`）· `pci_max_pasids()` `:489`（`:507`） | `pci_pasid_init()` `:347` |

⚠️ 三组都有 `restore_state`：`pci_restore_ats_state()`（`:109`）·
`pci_restore_pri_state()`（`:269`）· `pci_restore_pasid_state()`（`:432`）。
⚠️⚠️ **`pci_ats_disabled` 这个全局量在 `pci.c:161`**（01 §9.3），
命令行 `pci=noats` 会把 ATS 全局关掉 —— 排查 SMMU 性能问题时先确认它。

### §10.2 `vc.c` 是全树唯一一个零导出的扩展文件

428 行，`EXPORT_SYMBOL` 数为 0。
⚠️ 它只被 `pci_save_state()` / `pci_restore_state()` 路径调用，
用来保存恢复 VC 配置。**没有任何驱动需要碰它** —— 这是一个"纯核心内部扩展"的样本。

---

## §11 一页速查

### §11.1 排查入口

| 现象 | 先看 |
|---|---|
| `dmesg` 里一堆 AER correctable 消息 | §3.2 的 agent 字段判断是本端还是对端 |
| AER 报了但看不出是哪个设备 | §3.4 `find_source_device()`（`aer.c:919`） |
| 链路突然断开 + `DPC: containment event` | §6.1，链路已被硬件断开 |
| 插卡后链路不通 / 降速 | ⚠️ 先 `pcie_aspm=off`（§5.4 的重训练） |
| 驱动的 `error_detected()` 被调了但没恢复 | §4.2 `merge_result()`，有驱动返回了更悲观的值 |
| `sriov_numvfs` 写不进去 | §9.2，驱动没实现 `sriov_configure` |
| SMMU 下 ATS 不生效 | §10.1，`pci=noats` 和 `pci_ats_supported()` |

### §11.2 arm64 上不生效的部分（省得白读）

| 内容 | 原因 |
|---|---|
| ⚠️⚠️ `edr.c` 全部 246 行 | 纯 ACPI |
| `aer_recover_queue()` / `cper_severity_to_aer()` 的调用方 | 在 ACPI GHES 里 |
| `dmi_pcie_pme_disable_msi()`（`portdrv_pci.c:210`） | arm64 无 DMI |
| `pcie_ports=native` 一类开关 | DT 系统 `native_*` 位本来就是 1 |
| `rcec.c` 的大部分 | 多数平台没有 RCEC 能力 |
| `pcibios_sriov_*`（`iov.c:567`/`:572`） | powerpc 专用钩子 |

### §11.3 三条容易记反的结论

1. ⚠️⚠️ **`PCIEASPM` 是 `default y`**（`pcie/Kconfig:65`），
   arm64 上 ASPM 默认开着，不是默认关着。
2. ⚠️⚠️ **`portdrv.h` 不在 `include/linux/`**
   ⇒ `pcie_port_service_register()` 虽然导出了，外部模块用不了（§2.4）。
3. ⚠️⚠️ **SR-IOV 的使能入口在 sysfs**，不是内核 API；
   驱动侧只提供 `sriov_configure` 回调（§9.2）。

---

## 下一步读什么

| 想知道 | 去 |
|---|---|
| 写一个 PCI 设备驱动的完整步骤（含 `err_handler` 怎么填） | 05-usage.md |
| DT 怎么写、`ecam.c` 和 arm64 主机桥驱动 | 06-devicetree.md |
| AER 统计、`pci_express` 总线、sysfs / proc 现场 | 07-debug.md |
| 枚举、资源分配、锁 | 03-internals.md |
| 为什么 pci 要建两条总线 | 08-feynman.md |
| 本篇每个数字的来源 | 99-evidence.md |
