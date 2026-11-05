---
title: 设备树
---

# 网卡驱动：设备树与其他发现路径

基线 Linux 6.1.158（`f6e38ae624cf`），架构范围 arm64，ACPI 路径一律裁掉（见 `99-evidence.md` §2.7）。

## §0 本篇怎么读

### §0.1 发现路径分诊表

网卡这一层**不能先问「有没有 DT 绑定」**。一块网卡从「上电」到「`ip link` 里出现一个接口」，中间要落地的信息至少有四份互不相干：**MAC 控制器在哪**、**MAC 地址是多少**、**对面的 PHY 是谁**、**链路怎么协商**。这四份信息各有各的来源，其中只有一部分走设备树，而且同一块板子上四条路可以是四种不同来源。

| 发现路径 | 谁扫描 | 走不走 DT | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|
| MAC 控制器：platform 设备 | `of_platform` 遍历 DT 节点 | 走 | SoC 内置 MAC 全走这条 | §1.2 节点骨架、§8 实例 |
| MAC 控制器：PCI/PCIe 枚举 | PCI 层扫配置空间 | **不走** | 服务器网卡走这条 | 只说明它不走 DT（§0.1 本行 + §9.1） |
| MAC 地址：DT 属性 | `of_get_mac_address()` | 走 | 常见 | §2 完整四级回退 |
| MAC 地址：nvmem / efuse / OTP | `of_get_mac_addr_nvmem()` 或驱动私有 | **半走**（DT 里只写引用） | 常见（imx8mp、am65） | §2.2、§2.3 |
| MAC 地址：读 MAC 寄存器（bootloader 留下的） | 驱动私有 | **不走** | 常见（mvneta、mvpp2） | §2.4 |
| MAC 地址：随机兜底 | `eth_hw_addr_random()` | **不走** | 兜底一定会走到 | §2.4、§10.2 |
| PHY：`phy-handle` 指到 MDIO 子节点 | `fwnode_get_phy_node()` | 走 | **最常见** | §4.1 |
| PHY：MDIO 总线自动扫描（子节点无 `reg`） | `__of_mdiobus_register()` 第二轮 | 走（但故意不写地址） | 少见，内核会刷日志催你改 | §5.3 |
| 没有 PHY：`fixed-link` | `of_phy_is_fixed_link()` / `phylink_parse_mode()` | 走 | 交换机级联口、2500base-x 直连常见 | §4.2 |
| 没有 PHY：`managed = "in-band-status"` | 同上 | 走 | SerDes 带内协商 | §4.3 |
| PHY 是热插拔的：`sfp` | `sfp_bus_find_fwnode()` | 走 | 光口板子（mcbin、sparx5） | §6 |
| MDIO 设备不是 PHY（PCS、交换机） | `of_mdiobus_register_device()` | 走 | 少见 | §5.2 |
| 平台代码静态注册 MDIO 板级信息 | `mdio-boardinfo` | **不走** | 基本只剩非 DT 老平台 | 只提一句，不展开 |
| ACPI（`_DSD` 里写 `phy-handle`） | `fwnode_*` 同一套接口 | **不走 DT，走 fwnode** | **裁掉** | §9.4 说明为什么裁 |

两条要先记住的结论：

- 内核里读这些属性的代码**大部分已经是 `fwnode_*` 而不是 `of_*`**。`drivers/net/mdio/of_mdio.c` 现在基本是薄壳，真身在 `drivers/net/mdio/fwnode_mdio.c`：`of_mdiobus_phy_device_register()`（`drivers/net/mdio/of_mdio.c:36`）整个函数体就是一句转调 `fwnode_mdiobus_phy_device_register()`（`:39-41`），`of_get_phy_id()`（`:31`）同理转调 `fwnode_get_phy_id()`（`:33`）。**所以查一条属性的消费者时，只 grep `of_property_read` 会漏掉一半**。
- 「MAC 地址找不到」和「PHY 找不到」**不是同一类失败**。前者一定有兜底（随机地址），接口照样起来，症状是每次重启 MAC 都变；后者会让 `probe` 直接失败或者链路永远 down。§10 的症状清单就是按这个分的组。

### §0.2 按任务找章节

| 你想干的事 | 去哪节 |
|---|---|
| 从零写一个 SoC MAC 的 DT 节点 | §1.2 骨架 → §8 抄一个最接近的实例 |
| 搞清楚 `mac-address` 和 `local-mac-address` 到底谁优先 | §2.1 |
| MAC 地址想从 efuse/OTP 来 | §2.2、§2.3 |
| `phy-mode` 该写什么字符串 | §3.1 全表 |
| 不知道该用 `phy-handle` 还是 `fixed-link` 还是 `managed` | §4.4 决策树 |
| MDIO 子节点该叫 `mdio` 还是 `mdio-bus` | §5.1 |
| PHY 需要复位 GPIO / 复位延时 | §5.4 |
| 光模块热插拔 | §6 |
| 想加一个厂商私有属性 | §7.1 |
| 属性写了但看不出效果 | §7.2 死字清单 → §9 定位三层 |
| 接口起来了但发不出包 / 链路一直 down | §10 症状清单 |
| dmesg 里那句话是什么意思 | §11 错误消息映射表 |

## §1 核心真正会读的属性

### §1.1 总表

「核心」= `net/core/of_net.c` + `drivers/net/mdio/` + `drivers/net/phy/`，也就是**不管你用哪个厂商的 MAC 都会生效**的那些。厂商私有属性在 §7。

「写在哪个节点」这一列是本表最容易出错的地方——**同一个属性名写在不同节点上会被不同的代码读，甚至语义完全不同**（`max-speed` 就是例子，见 §5.5）。

| 属性 | 写在哪个节点 | 类型 | 谁消费（定位到行） | 缺了会怎样 |
|---|---|---|---|---|
| `mac-address` | MAC / port 节点 | 6 字节 | `net/core/of_net.c:131` | 落到 `local-mac-address` |
| `local-mac-address` | MAC / port 节点 | 6 字节 | `net/core/of_net.c:135` | 落到 `address` |
| `address` | MAC / port 节点 | 6 字节 | `net/core/of_net.c:139` | 落到 nvmem（已过时，见 §2.5） |
| `nvmem-cells` + `nvmem-cell-names` | MAC / port 节点 | phandle + 字符串 | `net/core/of_net.c:77`（cell 名必须是 `mac-address`） | MAC 地址全链路失败 → 驱动兜底 |
| `phy-mode` | MAC / port 节点 | 字符串 | `net/core/of_net.c:33` | 落到 `phy-connection-type` |
| `phy-connection-type` | MAC / port 节点 | 字符串 | `net/core/of_net.c:35` | `of_get_phy_mode()` 返回负值 |
| `phy-handle` | MAC / port 节点 | phandle | `drivers/net/phy/phy_device.c:3008`；老路径 `drivers/net/mdio/of_mdio.c:335` | 落到 `phy` → `phy-device`（§4.1） |
| `phy` | MAC / port 节点 | phandle | `drivers/net/phy/phy_device.c:3011` | — （legacy 别名） |
| `phy-device` | MAC / port 节点 | phandle | `drivers/net/phy/phy_device.c:3013` | — （legacy 别名） |
| `fixed-link`（子节点形式） | MAC / port 节点下的子节点 | 子节点 | `drivers/net/mdio/of_mdio.c:396`；phylink 侧 `drivers/net/phy/phylink.c:696` | 落到老式 5-cell 形式 |
| `fixed-link`（5-cell 形式） | MAC / port 节点 | 5 个 u32 | `drivers/net/mdio/of_mdio.c:415`；phylink 侧 `drivers/net/phy/phylink.c:732` | — （已过时，见 §4.2） |
| `speed` / `full-duplex` / `pause` / `asym-pause` | `fixed-link` 子节点内 | u32 / bool | `drivers/net/mdio/of_mdio.c:401 :399 :406 :407`；phylink 侧 `drivers/net/phy/phylink.c:698 :703 :709 :712` | `speed` 缺了直接 `-EINVAL`（`:401-405`） |
| `link-gpios` | `fixed-link` 子节点内 | GPIO | `drivers/net/phy/phylink.c:717`（**只有 phylink 读，phylib 不读**） | 链路状态恒为 up |
| `managed` | MAC / port 节点 | 字符串 | `drivers/net/mdio/of_mdio.c:369 :389`；phylink 侧 `drivers/net/phy/phylink.c:798` | 走 `phy-handle` 或 `fixed-link` |
| `reg` | MDIO 子节点 | u32（PHY 地址） | `drivers/net/mdio/of_mdio.c:183`（缺了 → `:208` 触发自动扫描） | 进第二轮扫描，日志刷 `scan phy` |
| `compatible = "ethernet-phy-idXXXX.XXXX"` | PHY 节点 | 字符串 | `drivers/net/phy/phy_device.c:885`（`sscanf`） | 走在线读 ID |
| `compatible = "ethernet-phy-ieee802.3-c45"` | PHY 节点 | 字符串 | `drivers/net/mdio/fwnode_mdio.c:139-140`；判定侧 `drivers/net/mdio/of_mdio.c:121` | 按 c22 访问 |
| `compatible = "ethernet-phy-ieee802.3-c22"` | PHY 节点 | 字符串 | `drivers/net/mdio/of_mdio.c:124` | 见 §5.2 五条判定 |
| `interrupts` | PHY 节点 | 中断 | `drivers/net/mdio/fwnode_mdio.c:73` | 退化为轮询 |
| `broken-turn-around` | PHY 节点 | bool | `drivers/net/mdio/fwnode_mdio.c:89` | — （arm64 DTS 里 0 处使用，见 §7.2） |
| `reset-gpios` | PHY 节点 | GPIO | `drivers/net/phy/mdio_bus.c:46`（consumer 名 `reset`） | PHY 不复位 |
| `reset-assert-us` | PHY 节点 | u32 | `drivers/net/mdio/fwnode_mdio.c:92` | 复位脉宽为 0 |
| `reset-deassert-us` | PHY 节点 | u32 | `drivers/net/mdio/fwnode_mdio.c:94` | 复位后不等待 |
| `resets` + `reset-names = "phy"` | PHY 节点 | reset 控制器 | `drivers/net/phy/mdio_bus.c:61` | 不走 reset 框架 |
| `reset-gpios` | **MDIO 总线节点** | GPIO | `drivers/net/phy/mdio_bus.c:569`（**这是总线级，和 PHY 级是两回事**） | 总线上所有 PHY 不复位 |
| `reset-delay-us` | MDIO 总线节点 | u32 | `drivers/net/mdio/of_mdio.c:172` | 用默认值 |
| `reset-post-delay-us` | MDIO 总线节点 | u32 | `drivers/net/mdio/of_mdio.c:174` | 默认 0，不等待（`:173`） |
| `max-speed` | PHY 节点 | u32（Mbps） | `drivers/net/phy/phy-core.c:368` | 不裁剪能力集 |
| `eee-broken-100tx` 等 6 个 | PHY 节点 | bool | `drivers/net/phy/phy-core.c:383-394` | EEE 按 PHY 自报能力 |
| `sfp` | MAC / port 节点 | phandle | `drivers/net/phy/sfp-bus.c:612` | 无热插拔支持 |
| `status = "disabled"` | MDIO 总线节点 | 字符串 | `drivers/net/mdio/of_mdio.c:161`（返回 `-ENODEV`） | — |
| `status = "disabled"` | MDIO 子节点 | 字符串 | `drivers/net/mdio/of_mdio.c:182`（`for_each_available_child_of_node`） | — |

### §1.2 节点骨架

四个层次，每层各管一部分属性。**把属性写错一层是最常见的失败**，而且大多数写错都不报错（§7.2）。

```
soc {
    ethernet@30be0000 {                 ← MAC 控制器节点
        compatible = "厂商,型号";           MAC 地址 / phy-mode / phy-handle
        reg = <...>;                        fixed-link / managed / sfp
        interrupts = <...>;                 nvmem-cells / 厂商私有属性
        clocks = <...>;

        mdio {                          ← MDIO 总线节点（名字不统一，见 §5.1）
            #address-cells = <1>;           reset-gpios（总线级）
            #size-cells = <0>;              reset-delay-us / reset-post-delay-us

            ethernet-phy@1 {            ← PHY 节点
                reg = <1>;                  compatible / interrupts
                ...                         reset-gpios（PHY 级）
            };                              reset-assert-us / reset-deassert-us
        };                                  max-speed / eee-broken-*
    };
};
```

多端口 SoC（TI CPSW、Marvell CP110）会在 MAC 节点下再插一层 `ethernet-ports { port@0 { ... } }`，此时**上表里标「MAC / port 节点」的属性全部下移到 `port@N`**。TI 侧的入口是 `drivers/net/ethernet/ti/am65-cpsw-nuss.c:1838` 取名为 `ethernet-ports` 的子节点，然后 `:1903` 在 `port_np` 上调 `of_get_phy_mode()`、`:1914` 在 `port_np` 上调 `of_get_mac_address()`。

## §2 MAC 地址：四级回退加驱动兜底

### §2.1 核心的三个属性名，顺序是写死的

`of_get_mac_address()`（`net/core/of_net.c:124`）按 `mac-address` → `local-mac-address` → `address` → nvmem 的顺序试（`:131` `:135` `:139` `:143`）。这个顺序**不是随便排的**，函数头上的注释（`:103-120`）给了理由：`mac-address` 被认为是「最新的」地址，`local-mac-address` 是默认地址；而某些老 U-Boot 只初始化 `local-mac-address`，会留下一个**全零的 `mac-address` 属性**——所以每一级都必须校验，不能只看属性存不存在。

校验在 `of_get_mac_addr()`（`net/core/of_net.c:49`）里，三个条件同时成立才算通过（`:53`）：属性存在、长度**恰好** `ETH_ALEN`、`is_valid_ether_addr()` 为真。任何一条不满足就返回 `-ENODEV`，继续下一级。

⚠️ **全零地址会被判为无效，然后静默跳到下一级**。这就是「DTS 里明明写了 `mac-address`，`ip link` 里却是别的地址」的成因：属性在，但值是零，或者长度写成了 4 字节。

### §2.2 nvmem 是第四级，而且有两条子路径

`of_get_mac_addr_nvmem()`（`net/core/of_net.c:60`）先试 `of_find_device_by_node()` 找到对应的 platform 设备（`:62`），找到就走 `nvmem_get_mac_address(&pdev->dev, addr)` 并直接返回（`:71-75`）；找不到才退回 `of_nvmem_cell_get(np, "mac-address")`（`:77`）。

⚠️ **cell 的名字必须**是 `mac-address`——`nvmem-cell-names` 写成别的字符串，这一级不报错，只是找不到。arm64 里的写法（`arch/arm64/boot/dts/freescale/imx8mp.dtsi:1026-1027`）：

```
nvmem-cells = <&eth_mac1>;
nvmem-cell-names = "mac-address";
```

nvmem 路径读回来以后还有第二道校验（`net/core/of_net.c:87`）：长度不等于 `ETH_ALEN` 或地址无效 → `-EINVAL`（注意这里是 `-EINVAL` 而不是前三级的 `-ENODEV`）。

### §2.3 厂商 efuse 是第五条路，不在核心里

TI CPSW 走的是自己的 efuse 读法：`am65_cpsw_am654_get_efuse_macid()`（`drivers/net/ethernet/ti/am65-cpsw-nuss.c:1758`）用 `syscon_regmap_lookup_by_phandle(of_node, "ti,syscon-efuse")`（`:1765`）拿 regmap，再用 `of_property_read_u32_index(of_node, "ti,syscon-efuse", 1, ...)`（`:1772`）取偏移。调用点在 `:1916`，**只有 `of_get_mac_address()` 失败时才走**（`:1914-1916`）。

也就是说 `ti,syscon-efuse` 是一个**只有 TI 驱动认识的属性**，写在别的 SoC 上是死字。

### §2.4 决策树：一个地址最终从哪来

```
of_get_ethdev_address(np, dev)          net/core/of_net.c:160
        │
        ├─ mac-address 有效？ ─────────── yes → eth_hw_addr_set()   :167
        ├─ local-mac-address 有效？ ───── yes → 同上
        ├─ address 有效？ ─────────────── yes → 同上（过时，§2.5）
        ├─ nvmem cell "mac-address" ──── yes → 同上
        │
        └─ 全部失败，返回负值 → 交给驱动，三种典型处置：
             ├─ mtk：err == -EPROBE_DEFER 时原样返回（延后重试）
             │        drivers/net/ethernet/mediatek/mtk_eth_soc.c:3841-3842
             │        其余错误 → eth_hw_addr_random()  :3846
             ├─ mvneta：先读 MAC 寄存器，有效就用「hardware」
             │        drivers/net/ethernet/marvell/mvneta.c:5532-5535
             │        无效 → eth_hw_addr_random()      :5537-5538
             └─ mvpp2：MVPP21 才读寄存器
                      drivers/net/ethernet/marvell/mvpp2/mvpp2_main.c:6128-6134
                      否则直接 random                  :6137-6138
```

⚠️ **`-EPROBE_DEFER` 必须原样往上返回，不能当成「没找到」**。nvmem cell 所在的 efuse 驱动可能还没 probe，此时 `of_get_ethdev_address()` 返回 `-EPROBE_DEFER`；mtk 的写法（`drivers/net/ethernet/mediatek/mtk_eth_soc.c:3841-3842`）是对的，把它当普通失败去随机化就永久丢掉了正确地址。

mvneta 用一个 `mac_from` 字符串记住来源（`drivers/net/ethernet/marvell/mvneta.c:5530` 写 `"device tree"`、`:5534` 写 `"hardware"`、`:5537` 写 `"random"`），最后打进日志——**这是判断「地址到底从哪来」最快的手段**，见 §11。

### §2.5 `address` 属性：还活着，但别用

`address` 这一级仍在代码里（`net/core/of_net.c:139`），但注释（`:111-113`）说得很直白：这个属性**本来的语义是寄存器组的虚拟地址**，只是某些 DTS 把它重定义成了 MAC 地址。新写的 DTS 不要用它。

arm64 DTS 里三者的使用面（口径：`grep -rl` 数文件数，`arch/arm64/boot/dts/` 全树）：`mac-address` 43 个文件、`local-mac-address` 23 个、`address` 无法单独统计（属性名太通用，任何 grep 都会混进别的子系统）。

## §3 `phy-mode`：一个字符串，必须逐字对上表

### §3.1 合法取值全表

`of_get_phy_mode()`（`net/core/of_net.c:26`）读到字符串后，拿它去和 `phy_modes(i)` 的返回值逐个比（`:39-43`）。`phy_modes()` 的全表在 `include/linux/phy.h:211-281`，**这就是唯一的合法取值清单**，写在别处的任何字符串都会让函数返回 `-ENODEV`（`:45`）。

| 字符串 | 定义行 | 字符串 | 定义行 |
|---|---|---|---|
| `internal` | `include/linux/phy.h:217` | `trgmii` | `include/linux/phy.h:253` |
| `mii` | `:219` | `1000base-x` | `:255` |
| `gmii` | `:221` | `1000base-kx` | `:257` |
| `sgmii` | `:223` | `2500base-x` | `:259` |
| `tbi` | `:225` | `5gbase-r` | `:261` |
| `rev-mii` | `:227` | `rxaui` | `:263` |
| `rmii` | `:229` | `xaui` | `:265` |
| `rev-rmii` | `:231` | `10gbase-r` | `:267` |
| `rgmii` | `:233` | `25gbase-r` | `:269` |
| `rgmii-id` | `:235` | `usxgmii` | `:271` |
| `rgmii-rxid` | `:237` | `10gbase-kr` | `:273` |
| `rgmii-txid` | `:239` | `100base-x` | `:275` |
| `rtbi` | `:241` | `qusgmii` | `:277` |
| `smii` | `:243` | 空串（`PHY_INTERFACE_MODE_NA`） | `:215` |
| `xgmii` | `:245` | `unknown`（default 分支） | `:279` |
| `xlgmii` | `:247` | | |
| `moca` | `:249` | | |
| `qsgmii` | `:251` | | |

三个容易踩的点：

- **匹配用 `strcasecmp`**（`net/core/of_net.c:40`），所以 `"RGMII"` 和 `"rgmii"` 等价——大小写不是失败原因。
- ⚠️ **`unknown` 是 `default` 分支返回的兜底字符串，不是给 DTS 用的合法值**。它排在枚举最后，写进 DTS 会匹配上 `PHY_INTERFACE_MODE_MAX`，行为未定义。
- **四个 `rgmii*` 变体是延时补偿在哪一端**：`rgmii` 两端都不加、`rgmii-id` PHY 内部两端都加、`rgmii-rxid` 只加接收、`rgmii-txid` 只加发送。四个字符串对应四个 enum 值，PHY 驱动会据此配寄存器；写错的表现是**链路 up 但丢包或完全不通**，不会有任何报错。

### §3.2 `phy-connection-type` 是同一件事的旧名字

`of_get_phy_mode()` 先读 `phy-mode`（`net/core/of_net.c:33`），失败才读 `phy-connection-type`（`:35`）。两者取值表完全一样。arm64 DTS 里 `phy-mode` 266 个文件、`phy-connection-type` 17 个（后者集中在 freescale DPAA 平台）。新写用 `phy-mode`。

### §3.3 `mac-mode`：只有 stmmac 认，而且语义不同

`stmmac_of_get_mac_mode()`（`drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:389`）读一个叫 `mac-mode` 的属性（`:394`），取值表和 `phy-mode` 共用（它调的也是 `phy_modes` 那套字符串）。函数头上的注释（`:386-387`）说明了为什么要两个：**当 MAC 和 PHY 之间插了一个模式转换器时**（例如 GMII-to-RGMII 桥），`phy-mode` 描述的是「转换器到 PHY」那一段，`mac-mode` 描述的是「MAC 到转换器」那一段。

⚠️ **这是全篇最容易搞混的一对属性**：名字像、取值表相同、都写在 MAC 节点上，但只有 stmmac 读 `mac-mode`，其他驱动写了就是死字。

## §4 找 PHY 的三条路

### §4.1 `phy-handle`：三个合法名字，但两套代码只认不同的子集

现代路径是 `fwnode_get_phy_node()`（`drivers/net/phy/phy_device.c:3003`），它依次试三个属性名：`phy-handle`（`:3008`）→ `phy`（`:3011`）→ `phy-device`（`:3013`）。后两个是 legacy 别名，仍然生效。

⚠️ **老路径只认第一个。** `of_phy_get_and_connect()`（`drivers/net/mdio/of_mdio.c:315`）里写死了 `of_parse_phandle(np, "phy-handle", 0)`（`:335`），没有别名回退。所以一份只写了 `phy = <&phy0>` 的 DTS，在走 phylink 的驱动上能工作、在走 `of_phy_get_and_connect()` 的驱动上直接失败——**同一份 DTS 在两个驱动上表现不同，这不是 bug 而是两条独立代码路径**。

`fwnode_get_phy_node()` 的函数注释（`drivers/net/phy/phy_device.c:2995-3002`）还提到 ACPI 只支持 `phy-handle`。ACPI 在本文档集范围外（记入 `99-evidence.md` §2.7）。

arm64 DTS 里 `phy-handle` 238 个文件；`drivers/net/` 下读 `"phy-handle"` 的 `.c` 文件 47 个（口径：`grep -rl '"phy-handle"' drivers/net/ --include=*.c`）。

### §4.2 `fixed-link`：两种写法，新的用子节点

用在「对面没有 PHY」的场合——最典型的是 SoC MAC 直连一颗以太网交换机的 CPU 口。

**新写法（子节点）**，解析在 `of_phy_register_fixed_link()`（`drivers/net/mdio/of_mdio.c:382`）的 `:396-412` 段：

| 子属性 | 必选 | 消费行 |
|---|---|---|
| `speed` | 是，缺了返回 `-EINVAL` | `drivers/net/mdio/of_mdio.c:401-405` |
| `full-duplex` | 否（bool） | `:399` |
| `pause` | 否（bool） | `:406` |
| `asym-pause` | 否（bool） | `:407` |

**老写法（5 个 u32 数组）** 在 `:415-423`：依次是 phy 地址（被忽略）、duplex、speed、pause、asym-pause。phylink 侧的同一段（`drivers/net/phy/phylink.c:732-737`）会在 cell 数不等于 5 时打 `phylink_err(pl, "broken fixed-link?\n")` 并返回 `-EINVAL`。**老写法应视为将死**：新绑定表达力更强（子节点形式还能带 `link-gpios`），且老写法第一个 cell 完全无用。

两种都不存在时返回 `-ENODEV`（`drivers/net/mdio/of_mdio.c:425`），最后交给 `fixed_phy_register(PHY_POLL, &status, np)`（`:428`）造一个假 PHY。

⚠️ **phylink 和 phylib 各有一份 `fixed-link` 解析代码，两份不完全等价。** phylink 侧（`drivers/net/phy/phylink.c:687`）多读一个 `link` GPIO（`:717`，通过 `fwnode_gpiod_get_index(fixed_node, "link", 0, GPIOD_IN, "?")`，DTS 里写成 `link-gpios`），用来把真实链路状态引进来；phylib 侧完全没有这个能力，链路状态恒为 up。phylink 侧还多两条校验警告：速率高于 1000Mbps 却写了半双工会打 `"fixed link specifies half duplex for %dMbps link?"`（`:754-757`），speed/duplex 组合无法识别会打 `"fixed link %s duplex %dMbps not recognised"`（`:774`）。

arm64 DTS 里用 `fixed-link` 的有 18 个文件，MediaTek 和 Marvell 占大半。

### §4.3 `managed = "in-band-status"`：带内协商，也没有 MDIO PHY

`phylink_parse_mode()`（`drivers/net/phy/phylink.c:788`）读 `managed`（`:798`），值为 `in-band-status` 时进 `MLO_AN_INBAND`（`:798-813`）。驱动也可以用 `pl->config->ovr_an_inband` 强制这个模式，不依赖 DTS。

phylib 侧的对应逻辑在两处：`of_phy_is_fixed_link()`（`drivers/net/mdio/of_mdio.c:356`）把 `managed` 存在且不等于 `"auto"` 也算作 fixed-link（`:369-371`）；`of_phy_register_fixed_link()` 里 `managed == "in-band-status"` 会**短路返回，把 `status` 整个留成零**（`:389-392`）——也就是初始链路状态是 down，等带内协商结果填进来。

⚠️ **`fixed-link` 和 `managed = "in-band-status"` 不能同时写。** phylink 会明确报错 `phylink_err(pl, "can't use both fixed-link and in-band-status\n")`（`drivers/net/phy/phylink.c:802-804`）。

arm64 DTS 里 `managed = ` 出现 114 处，最密集的是 `arch/arm64/boot/dts/microchip/sparx5_pcb134_board.dtsi:723` 起的一长串 SFP 口。

### §4.4 决策树：该写哪一条

```
对面是什么？
  │
  ├─ 一颗挂在 MDIO 总线上的 PHY 芯片
  │     → MDIO 子节点写 ethernet-phy@N + MAC 节点写 phy-handle = <&phyN>
  │       phy-mode 按走线填（RGMII 记得选对 -id/-rxid/-txid 变体）
  │
  ├─ 一颗交换机 / 另一个 MAC，速率固定、无需协商
  │     → fixed-link { speed; full-duplex; } 子节点，不写 phy-handle
  │       ⚠️ 级联的两端都要各写一份（见 §8.2 的 mt7622 实例）
  │
  ├─ SerDes 链路，速率由带内协商决定（1000base-x / 2500base-x / SGMII）
  │     → managed = "in-band-status"，不写 fixed-link
  │
  ├─ 可插拔光模块
  │     → sfp = <&sfp_ethN> + managed = "in-band-status"（见 §6）
  │
  └─ 内部 MAC-to-MAC，SoC 里连死的
        → phy-mode = "internal"，通常配 fixed-link
```

⚠️ **最危险的失败是「什么都不写」。** `phylink_fwnode_phy_connect()`（`drivers/net/phy/phylink.c:1781`）有三条返回 0（成功）却没接上任何 PHY 的路径：

```
phylink_fwnode_phy_connect()                    drivers/net/phy/phylink.c:1781
  ├─ cfg_link_an_mode == MLO_AN_FIXED  → return 0   :1789-1793
  ├─ inband 且 8023z                    → return 0   :1789-1793
  ├─ fwnode_get_phy_node() 找不到节点   :1795
  │     ├─ cfg_link_an_mode == MLO_AN_PHY → return -ENODEV   :1797-1799
  │     └─ 其他模式                        → return 0        :1800  ⚠️
  └─ 找到了 → fwnode_phy_find_device :1802 → phy_attach_direct :1814
              → phylink_bringup_phy :1820
```

也就是说：`phy-handle` 里的 phandle 写错、指向的节点被 `status = "disabled"`、或者 label 拼错导致 phandle 落到别的节点上——只要 `cfg_link_an_mode` 不是 `MLO_AN_PHY`，probe 一路成功，`ip link` 里接口存在、可以 up，**链路永远不会 up，dmesg 里一个字都没有**。定位手段见 §9.2。

## §5 MDIO 总线与 PHY 子节点

### §5.1 子节点的名字不统一

内核不强制 MDIO 总线子节点叫什么，各驱动自己找：

| 驱动 | 找什么 | 代码位置 |
|---|---|---|
| TI am65-cpsw | 名为 `mdio` 的子节点 | `drivers/net/ethernet/ti/am65-cpsw-nuss.c:2766` |
| MediaTek | ⚠️ 名为 `mdio-bus` 的子节点 | `drivers/net/ethernet/mediatek/mtk_eth_soc.c:670` |
| stmmac | 两条：`compatible = "snps,dwc-qos-ethernet-4.10"` 时找名为 `mdio` 的子节点；否则找任意带 `compatible = "snps,dwmac-mdio"` 的子节点 | `drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:315-316` 和 `:322-326` |

`stmmac_of_get_mdio()` 的函数注释（`drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:296-306`）把这两条规则写清了。arm64 DTS 里 `snps,dwmac-mdio` 出现 42 次。

⚠️ **子节点名字写错 = 整条 MDIO 总线不注册**，然后所有 `phy-handle` 指向的节点都不会变成 PHY 设备。这是「总线级」故障（§10.1）。

### §5.2 「这个子节点是 PHY 还是别的 MDIO 设备」

MDIO 总线上可以挂非 PHY 设备（PCS、交换机、时间戳器）。判定在 `of_mdiobus_child_is_phy()`（`drivers/net/mdio/of_mdio.c:114`），五条依次判：

1. `compatible` 能被 `fwnode_get_phy_id()` 解析成 `ethernet-phy-idAAAA.BBBB` 格式 → 是 PHY（`:118-119`）
2. `compatible = "ethernet-phy-ieee802.3-c45"` → 是 PHY（`:121-122`）
3. `compatible = "ethernet-phy-ieee802.3-c22"` → 是 PHY（`:124-125`）
4. `compatible` 命中 `whitelist_phys[]` → 是 PHY，同时打一条 `pr_warn(FW_WARN "%pOF: Whitelisted compatible string. Please remove\n")`（`:127-130`）
5. ⚠️ **完全没有 `compatible` 属性 → 也算 PHY**（`:134-135`）

`whitelist_phys[]`（`:88-101`）是 11 个历史遗留 compatible：`brcm,40nm-ephy`、`broadcom,bcm5241`、`marvell,88E1111`、`marvell,88e1116`、`marvell,88e1118`、`marvell,88e1145`、`marvell,88e1149r`、`marvell,88e1310`、`marvell,88E1510`、`marvell,88E1514`、`moxa,moxart-rtl8201cp`。它们能工作但会刷警告，新 DTS 不要用。

第 5 条的后果：**你想挂一个非 PHY 的 MDIO 设备，就必须给它写 `compatible`**，否则它会被当 PHY 去 attach。反过来，PHY 节点**不写 `compatible` 是完全合法的**——这时内核在线读 ID。

### §5.3 `reg` 缺失会触发第二轮自动扫描

`__of_mdiobus_register()`（`drivers/net/mdio/of_mdio.c:150`）分两轮：

**第一轮**（`:182-200`）遍历所有 available 的子节点，用 `of_mdio_parse_addr()` 取 `reg`。取不到就跳过，并在这个子节点上记一笔。

**第二轮**（`:206-232`）只处理第一轮里没有 `reg` 的子节点：在 0..`PHY_MAX_ADDR-1`（`include/linux/phy.h:286`，值为 32）范围内逐个地址探测，每探到一个就打 `dev_info(&bus->dev, "scan phy %pOFn at address %i\n")`（`:217`）。代码注释（`:221-224`）说明 `-ENODEV` 在这里意味着「这个地址上没东西，继续扫」而不是失败。

⚠️ **`__of_mdiobus_register()` 会先设 `mdio->phy_mask = ~0`（`:166`），把 MDIO 核心的自动探测全部屏蔽掉**——所以 DT 路径下「总线上有什么」完全由子节点列表决定，第二轮扫描是唯一的例外。这也解释了为什么「PHY 明明在总线上，但 DTS 里没写子节点，内核就是不认」。

第一轮里若某个子节点有 `reg` 但注册失败，会打 `dev_err(&bus->dev, "MDIO device at address %d is missing.\n")`（`:194-197`）。

### §5.4 复位有三个层级，属性名撞车

这是本篇第二个重灾区：**`reset-gpios` 这个名字在两个不同层级各有一份消费代码，语义不同**。

| 层级 | 写在哪 | 谁读 | 时序 |
|---|---|---|---|
| 总线级 GPIO | MDIO 总线节点 | `drivers/net/phy/mdio_bus.c:569`（`devm_gpiod_get_optional(&bus->dev, "reset", GPIOD_OUT_HIGH)`） | 断言 → `fsleep(bus->reset_delay_us)`（`:578`）→ 解除（`:579`）→ `fsleep(bus->reset_post_delay_us)`（`:580-581`） |
| 总线级回调 | 无 DTS 属性，驱动填 `bus->reset` | `drivers/net/phy/mdio_bus.c:584-588` | GPIO 之后、扫 PHY 之前 |
| PHY 级 GPIO | PHY 节点 | `drivers/net/phy/mdio_bus.c:46`（`gpiod_get_optional(&mdiodev->dev, "reset", GPIOD_OUT_LOW)`） | `mdiobus_register_device()` 里断言（`:87`） |
| PHY 级 reset 控制器 | PHY 节点的 `resets` + `reset-names = "phy"` | `drivers/net/phy/mdio_bus.c:61`（`reset_control_get_optional_exclusive(&mdiodev->dev, "phy")`） | 同上 |

⚠️ **PHY 级的两条复位路径只在设备标了 `MDIO_DEVICE_FLAG_PHY` 时才走**（`drivers/net/phy/mdio_bus.c:77-88`）。挂在 MDIO 上的非 PHY 设备写了 `reset-gpios` 是死字。

`reset-assert-us` / `reset-deassert-us` 是 PHY 级的延时，读在 `drivers/net/mdio/fwnode_mdio.c:92` 和 `:94`；`reset-delay-us` / `reset-post-delay-us` 是总线级的，读在 `drivers/net/mdio/of_mdio.c:172` 和 `:174`（前者有默认值 `DEFAULT_GPIO_RESET_DELAY`，见 `:171`；后者默认 0，见 `:173`）。⚠️ **四个名字，两两一组，用错组等于没写**。

arm64 DTS 里的实际用量：`reset-assert-us` 57 个文件、`reset-deassert-us` 51 个、`reset-delay-us` **只有 2 个**。`reset-gpios` 全树 260 个文件，但这个数字**跨所有子系统**（regulator、USB hub、panel 都用同名属性），不能当成 PHY 复位的指标——想统计 PHY 复位就数 `reset-assert-us`。

实际断言/解除的动作在 `mdio_device_reset()`（`drivers/net/phy/mdio_device.c:118`），GPIO 部分是 `gpiod_set_value_cansleep(mdiodev->reset_gpio, value)`（`:126`），三个调用点在 `:156`、`:162`、`:179`。

### §5.5 PHY 节点上的其余属性

**`max-speed`**（`drivers/net/phy/phy-core.c:368`，在 `of_set_phy_supported()` 里）用来裁剪 PHY 的能力集，单位 Mbps。两道前置门：`IS_ENABLED(CONFIG_OF_MDIO)`（`:362`）和节点非空（`:365`）。

⚠️ **`max-speed` 是全篇最典型的「同名不同节点」陷阱**：`drivers/net/phy/phy-core.c:368` 在 **PHY 节点**上读它；`drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:454` 在 **MAC 节点**上也读一个 `max-speed`。两处代码互不相干，写在哪个节点上就由哪一处生效。更麻烦的是这个属性名在 arm64 DTS 里被 UART/蓝牙节点复用（`max-speed = <4000000>`，例见 `arch/arm64/boot/dts/renesas/beacon-renesom-som.dtsi:101` 和 `arch/arm64/boot/dts/amlogic/meson-gxm-minix-neo-u9h.dts:114`），所以「arm64 有 79 个文件用 `max-speed`」这个数字**不是网络专属的**，别拿它做结论（口径登记在 `99-evidence.md`）。

**六个 `eee-broken-*`**（`of_set_phy_eee_broken()`，`drivers/net/phy/phy-core.c:372`）逐个标记 PHY 自报支持但实际坏掉的 EEE 模式：`eee-broken-100tx`（`:383`）、`eee-broken-1000t`（`:385`）、`eee-broken-10gt`（`:387`）、`eee-broken-1000kx`（`:389`）、`eee-broken-10gkx4`（`:391`）、`eee-broken-10gkr`（`:393`）。arm64 DTS 里 `eee-broken` 13 个文件。

**`interrupts`** 走 `fwnode_irq_get()`（`drivers/net/mdio/fwnode_mdio.c:73`）。返回 `-EPROBE_DEFER` 时会走 `driver_deferred_probe_check_state()`（`:77-80`），最终**退化成轮询模式而不是失败**（`:74-87`）。所以「PHY 中断没配对，但链路仍然能检测到」是正常的——只是每秒轮询一次，链路变化有延迟。

**`broken-turn-around`**（`drivers/net/mdio/fwnode_mdio.c:89`）设置 `phy_ignore_ta_mask`。⚠️ **arm64 DTS 里 0 个文件使用它**——代码活着，绑定活着，实际是死字（§7.2）。

**`compatible = "ethernet-phy-idAAAA.BBBB"`** 让内核**不读总线**就知道 PHY 型号。解析在 `fwnode_get_phy_id()`（`drivers/net/phy/phy_device.c:875`）：读 `compatible`（`:881`）后 `sscanf(cp, "ethernet-phy-id%4x.%4x", &upper, &lower)`，返回值不等于 2 就 `-EINVAL`（`:885-886`），成功则组装出 32 位 ID（`:888`）。分支点在 `drivers/net/mdio/fwnode_mdio.c:144-147`：是 c45 或者能解析出 ID 就走 `get_phy_device()`（读总线）/ `phy_device_create()`（不读总线）中的对应一支。

⚠️ **写死 ID 的代价是「DTS 说的和芯片实际不一致时，内核信 DTS」**。板子换了 PHY 型号却没改 DTS，会加载错误的 PHY 驱动，症状是链路能 up 但速率/延时配置全错。arm64 DTS 里用得最多的是 `ethernet-phy-id0022.1622`（10 处）、`ethernet-phy-id004d.d072`（6 处）、`ethernet-phy-id0022.1640`（5 处）。

## §6 SFP：唯一的热插拔路径

`sfp` 属性由 `sfp_bus_find_fwnode()`（`drivers/net/phy/sfp-bus.c:606`）读，用的是 `fwnode_property_get_reference_args(fwnode, "sfp", NULL, 0, 0, &ref)`（`:612-613`）。挂接点在 `phylink_create()` 的 `phylink_register_sfp()`（`drivers/net/phy/phylink.c:1526`）。

⚠️ **两条静默返回 NULL 的路径**，都不是错误：

- 没有 `sfp` 属性（`-ENOENT`）→ 返回 NULL（`drivers/net/phy/sfp-bus.c:614-615`）。这是设计意图，绝大多数板子没有 SFP。
- ⚠️ **`sfp` 指向的节点存在但 `status = "disabled"`** → 也返回 NULL（`:619-622`），**不打任何日志**。这是「明明写了 `sfp = <&sfp_eth3>` 却没有热插拔行为」的成因。

只有 `-ENOMEM`（`:627`）是真错误。

被 `sfp` 指向的节点由 `drivers/net/phy/sfp.c:2852` 的 platform driver（`.name = "sfp"`）接管，节点本身的 `compatible` 是 `"sff,sfp"`。

一个真实的 arm64 SFP 组合（`arch/arm64/boot/dts/marvell/armada-8040-mcbin.dtsi`）：笼子节点在 `:64-75`，消费端在 `:305-309`：

```
sfp_eth3: sfp-eth3 {
        compatible = "sff,sfp";
        i2c-bus = <&sfpp0_i2c>;
        los-gpios = <...>;
        mod-def0-gpios = <...>;
        tx-disable-gpios = <...>;
        tx-fault-gpios = <...>;
        maximum-power-milliwatt = <2000>;
};
...
&cp1_eth0 {
        phy-mode = "2500base-x";
        managed = "in-band-status";
        phys = <&cp1_comphy5 2>;
        sfp = <&sfp_eth3>;
};
```

⚠️ **注意这里同时出现了 `phys` 和 `sfp`**。`phys` 是通用 PHY 框架（`drivers/phy/`，SerDes/COMPHY）的属性，和以太网 PHY（`drivers/net/phy/`）毫无关系，只是名字撞了。这是全篇最容易读错的一组属性名（`08-feynman.md` 有专题）。

arm64 DTS 里 `sfp = <` 出现在 17 个文件，主力是 microchip sparx5 和 marvell 系列。

## §7 厂商私有属性

### §7.1 stmmac 是私有属性最多的样本

`drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c` 一个文件里读了 30 多个 `snps,*` 属性，是全树最密的一处。按功能分组（全部在 MAC 节点或其子节点上）：

| 组 | 属性 | 消费行 |
|---|---|---|
| AXI 总线调优（子节点 `snps,axi-config`） | `snps,lpi_en` `snps,xit_frm` `snps,kbbe` `snps,fb` `snps,mb` `snps,rb` | `drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:110-115` |
| AXI 突发 | `snps,wr_osr_lmt` `snps,rd_osr_lmt` `snps,blen` | `:117` `:119` `:121` |
| MTL 队列（子节点 `snps,mtl-rx-config` / `snps,mtl-tx-config`） | `snps,rx-queues-to-use` `snps,tx-queues-to-use` | `:166` `:225` |
| RX 调度 | `snps,rx-sched-sp` `snps,rx-sched-wsp` | `:170-172` |
| TX 调度 | `snps,tx-sched-wrr` `snps,tx-sched-wfq` `snps,tx-sched-dwrr` | `:229-233` |
| 队列算法 | `snps,dcb-algorithm` `snps,avb-algorithm` | `:182-184` `:249-252` |
| 队列映射与优先级 | `snps,map-to-dma-channel` `snps,priority` | `:189` `:194` `:272` |
| 报文路由 | `snps,route-avcp` `snps,route-ptp` `snps,route-dcbcp` `snps,route-up` `snps,route-multi-broad` | `:203-211` |
| CBS 整形 | `snps,weight` `snps,send_slope` `snps,idle_slope` `snps,high_credit` `snps,low_credit` | `:245` `:256` `:259` `:262` `:265` |
| FIFO / 过滤 | `tx-fifo-depth` `rx-fifo-depth` `max-frame-size` `snps,multicast-filter-bins` `snps,perfect-filter-entries` | `:488` `:490` `:525` `:526` `:528` |
| 杂项开关 | `snps,force_sf_dma_mode` `snps,en-tx-lpi-clockgating` `snps,tso` | `:493` `:496` `:553` |
| CSR 时钟 | `snps,clk-csr`（老名 `clk_csr` 兜底） | `:473-474` |

两个观察：

- **`tx-fifo-depth` / `rx-fifo-depth` / `max-frame-size` 没有厂商前缀**，但它们只有 stmmac 读——**没有前缀不等于是通用属性**。判据只有一个：grep 出消费者。
- **接口编号来自 alias**：`of_alias_get_id(np, "ethernet")`（`:456`），失败时用 `of_alias_get_highest_id("ethernet")` 递增（`:459`）。所以 DTS 根节点的 `aliases { ethernet0 = &eqos; }` 会影响接口顺序。

其他 arm64 代表驱动的私有属性：

| 属性 | 驱动 | 消费行 | 语义 |
|---|---|---|---|
| `ti,mac-only` | am65-cpsw | `drivers/net/ethernet/ti/am65-cpsw-nuss.c:1899` | 端口不参与交换，纯 MAC |
| `label` | am65-cpsw | `:1873` | 端口名 |
| `ti,syscon-efuse` | am65-cpsw | `:1765` `:1772` | MAC 地址所在的 efuse（§2.3） |
| `tx-csum-limit` | mvneta | `drivers/net/ethernet/marvell/mvneta.c:5542` | TX 校验和卸载的报文长度上限，超出 `MVNETA_TX_CSUM_MAX_SIZE` 会被重置为默认值（`:5543-5545`） |
| `buffer-manager` | mvneta | `:5575` | 硬件缓冲管理器 phandle |
| `marvell,loopback` | mvpp2 | `drivers/net/ethernet/marvell/mvpp2/mvpp2_main.c:6781` | 端口环回 |
| `fsl,num-tx-queues` / `fsl,num-rx-queues` / `fsl,stop-mode` | fec | 见 `arch/arm64/boot/dts/freescale/imx8mp.dtsi:1024-1029` 的用法 | FEC 队列数与停机模式 |
| `intf_mode` | stmmac/imx | 见 `arch/arm64/boot/dts/freescale/imx8mp.dtsi:1053` | GPR 里的接口模式寄存器 |
| `ti,rx-internal-delay` / `ti,fifo-depth` | PHY 驱动（dp83867 等） | 见 `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:521-522` | ⚠️ 写在 **PHY 节点**上，由 PHY 驱动读，不是 MAC 驱动 |

### §7.2 死字与弃用清单

「死字」= 你写了、内核不报错、什么也不发生。四种成因各举一例：

| 属性 | 为什么是死字 | 依据 |
|---|---|---|
| `broken-turn-around` | 代码在读（`drivers/net/mdio/fwnode_mdio.c:89`），但 arm64 DTS 里 **0 个文件**使用；换句话说这条路径在 arm64 上从未被走过 | 口径：`grep -rl 'broken-turn-around' arch/arm64/boot/dts/` 无输出 |
| `nvmem-cells` 里 cell 名不叫 `mac-address` | `of_get_mac_addr_nvmem()` 硬编码了这个名字（`net/core/of_net.c:77`） | 见 §2.2 |
| `mac-mode` 写在非 stmmac 的 MAC 上 | 只有 `drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:394` 读它 | 见 §3.3 |
| PHY 级 `reset-gpios` 写在非 PHY 的 MDIO 设备上 | 复位代码被 `MDIO_DEVICE_FLAG_PHY` 门住（`drivers/net/phy/mdio_bus.c:77-88`） | 见 §5.4 |
| `link-gpios` 在 phylib 驱动上 | 只有 phylink 读（`drivers/net/phy/phylink.c:717`） | 见 §4.2 |

**已明确弃用但仍生效**的两个：

- `snps,phy-addr`：`drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:479-480` 会打 `dev_warn(&pdev->dev, "snps,phy-addr property is deprecated\n")`，上方注释（`:476-477`）说它「不是一个标准属性」。用 `phy-handle` 代替。
- `whitelist_phys[]` 里的 11 个厂商 compatible：命中会打 `pr_warn(FW_WARN "%pOF: Whitelisted compatible string. Please remove\n")`（`drivers/net/mdio/of_mdio.c:128-130`）。改成 `ethernet-phy-ieee802.3-c22` 或 `ethernet-phy-idAAAA.BBBB`。

**「不该由驱动直接读」的一个**：`nvmem-cells` 在 `drivers/net/` 全部 `.c` 里 **0 个消费者**（口径：`grep -rl '"nvmem-cells"' drivers/net/ --include=*.c` 无输出）。整条 nvmem 路径都封在 `net/core/of_net.c:60-96` 里——驱动只要调 `of_get_ethdev_address()` 就自动覆盖到了，自己再去读一遍是重复劳动。

## §8 arm64 实例四则

全部取自本树 `arch/arm64/boot/dts/`，可直接 diff。

### §8.1 最标准的一份：TI AM65（多端口 + PHY 私有调优）

`arch/arm64/boot/dts/ti/k3-am654-base-board.dts:513-529`：

```
&davinci_mdio {
        phy0: ethernet-phy@0 {
                reg = <0>;
                ti,rx-internal-delay = <DP83867_RGMIIDCTL_2_00_NS>;
                ti,fifo-depth = <DP83867_PHYCR_FIFO_DEPTH_4_B_NIB>;
        };
};

&cpsw_port1 {
        phy-mode = "rgmii-rxid";
        phy-handle = <&phy0>;
};
```

三个要点：`phy-mode` 和 `phy-handle` 都写在 **port 节点**（`cpsw_port1`）上而不是 MAC 节点上，因为 CPSW 是多端口的（§1.2）；PHY 的私有调优属性写在 **PHY 节点**上；`rgmii-rxid` 说明延时补偿只在接收端做。

### §8.2 级联交换机：MediaTek MT7622（`fixed-link` 要写两份）

`arch/arm64/boot/dts/mediatek/mt7622-rfb1.dts:105-121` MAC 一侧：

```
gmac0: mac@0 {
        compatible = "mediatek,eth-mac";
        reg = <0>;
        phy-mode = "2500base-x";

        fixed-link {
                speed = <2500>;
                full-duplex;
                pause;
        };
};

mdio-bus {
        #address-cells = <1>;
        ...
```

同一份文件 `:156-170` 交换机一侧：

```
port@6 {
        reg = <6>;
        label = "cpu";
        ethernet = <&gmac0>;
        phy-mode = "2500base-x";

        fixed-link {
                speed = <2500>;
                full-duplex;
                pause;
        };
};
```

⚠️ **两端各写一份 `fixed-link`，而且参数必须一致**。只写一端的表现是那一端起来了、另一端链路 down，整条通路不通。另外注意这份 DTS 里 MDIO 子节点叫 **`mdio-bus`** 而不是 `mdio`（§5.1）。

### §8.3 PHY 复位与中断：Rockchip RK3328

`arch/arm64/boot/dts/rockchip/rk3328-rock-pi-e.dts:166-175`：

```
rtl8211e: ethernet-phy@1 {
        reg = <1>;
        pinctrl-0 = <&eth_phy_int_pin>, <&eth_phy_reset_pin>;
        interrupt-parent = <&gpio1>;
        interrupts = <24 IRQ_TYPE_LEVEL_LOW>;
        reset-assert-us = <10000>;
        reset-deassert-us = <50000>;
        reset-gpios = <&gpio1 RK_PC2 GPIO_ACTIVE_LOW>;
};
```

这是 §5.4 里「PHY 级复位」的完整形态：GPIO + 两个延时，三者写在同一个 PHY 节点上。`interrupts` 走 §5.5 的 `fwnode_irq_get()` 路径，配不上时静默退化为轮询。

### §8.4 nvmem MAC 地址：i.MX8MP（两个 MAC 各引一个 cell）

`arch/arm64/boot/dts/freescale/imx8mp.dtsi:1024-1029`（FEC）：

```
fsl,num-tx-queues = <3>;
fsl,num-rx-queues = <3>;
nvmem-cells = <&eth_mac1>;
nvmem-cell-names = "mac-address";
fsl,stop-mode = <&gpr 0x10 3>;
```

同文件 `:1032-1053`（EQOS/stmmac）：

```
eqos: ethernet@30bf0000 {
        compatible = "nxp,imx8mp-dwmac-eqos", "snps,dwmac-5.10a";
        ...
        clock-names = "stmmaceth", "pclk", "ptp_ref", "tx";
        nvmem-cells = <&eth_mac2>;
        nvmem-cell-names = "mac-address";
        intf_mode = <&gpr 0x4>;
};
```

⚠️ **两个 MAC 必须引不同的 cell**。引同一个会让两个接口拿到相同的 MAC 地址——两个接口都能 up，同网段下 ARP 表会随机指向其中一个，症状是「时好时坏」。

`nvmem-cell-names = "mac-address"` 在 arm64 里的其他用户：`arch/arm64/boot/dts/broadcom/bcmbca/bcm4908-asus-gt-ac5300.dts:50`、`arch/arm64/boot/dts/broadcom/bcmbca/bcm4906-netgear-r8000p.dts:79`、`arch/arm64/boot/dts/freescale/imx8dxl-evk.dts:101` 和 `:141`。

### §8.5 交换机 + 多 PHY：Marvell Espressobin

`arch/arm64/boot/dts/marvell/armada-3720-espressobin.dtsi` 是「一个 MAC + 一颗交换机 + 三个外部 PHY」的完整样本：CPU 口 `:163-165`（`ethernet = <&eth0>; phy-mode = "rgmii-id"; fixed-link {`），三个用户口各自 `phy-handle = <&switch0phyN>`（`:174`、`:180`、`:186`），MAC 侧 `:211-214`（`phy-mode = "rgmii-id"; fixed-link {`）。**同一份 DTS 里 `fixed-link` 和 `phy-handle` 并存**——前者用于内部级联，后者用于外接 PHY，这不矛盾。

## §9 定位：编译期 / 运行期静态 / 运行期动态

三层的顺序不能反。属性写对了但代码没编进来，后两层看到的现象和「属性写错」一模一样。

### §9.1 编译期：先确认这几段代码在不在

| 开关 | 定义位置 | 关掉的后果 |
|---|---|---|
| `CONFIG_OF_MDIO` | `drivers/net/mdio/Kconfig:29` | `__of_mdiobus_register()` 整条路径不在，MDIO 子节点全部不被枚举；`drivers/net/phy/of_mdio.h` 提供的桩函数返回 `-ENOSYS`（桩在 `include/linux/of_mdio.h:81` 和 `:91`）。⚠️ **`of_set_phy_supported()` 里的 `max-speed` 也被这个开关门住**（`drivers/net/phy/phy-core.c:362`） |
| `CONFIG_PHYLINK` | `drivers/net/phy/Kconfig:6` | phylink 侧的 `fixed-link` / `managed` / `link-gpios` 解析全部不在；只走 phylib 的那份（能力更弱，见 §4.2） |
| `CONFIG_FIXED_PHY` | `drivers/net/phy/Kconfig:48` | `fixed_phy_register()` 不在，`fixed-link` 写了也造不出假 PHY |
| `CONFIG_SFP` | `drivers/net/phy/Kconfig:58` | `sfp` 属性无人接管，热插拔完全不工作（`sfp_bus_find_fwnode()` 仍会返回一个 bus，但没有驱动） |

判断方法：`zcat /proc/config.gz | grep -E 'CONFIG_(OF_MDIO|PHYLINK|FIXED_PHY|SFP)='`，或者看构建目录里的 `.config`。**不要靠「代码在树里」推断「代码在内核里」**——本子系统的四个开关全都是可选的。

**PCI/PCIe 网卡完全不经过这一层**：设备来自 PCI 枚举，没有 DT 节点，所有 §1.1 的属性对它无效（`04-extensions.md` §0.2 的分诊表标了哪些框架和 DT 无关）。判断一块卡走哪条路：`readlink /sys/class/net/eth0/device` 指向 `/sys/devices/platform/...` 是 DT 路径，指向 `/sys/devices/pci.../...` 是 PCI 路径。

### §9.2 运行期静态：DT 真的被解析成了什么

四个观察点，从「内核看到的 DT」一路走到「内核建出来的对象」：

1. **`/proc/device-tree/`** —— 内核实际使用的 DT，不是你 build 的那份 DTS。属性名就是目录里的文件名，`cat` 出来是二进制。⚠️ **确认属性名拼写和节点层级只能看这里**，DTS 源码里的宏和 `/include/` 已经展开完了。
2. **`/sys/bus/mdio_bus/devices/`** —— MDIO 总线上的设备列表。总线名 `mdio_bus` 来自 `drivers/net/phy/mdio_bus.c:1046`。⚠️ **这个目录为空 = §5.1 或 §5.3 的问题**：总线根本没注册，或者子节点没被认成 PHY。目录非空但少一个 = 那一个的 `reg` / `status` 有问题。
3. **`/sys/class/net/<if>/phydev`** —— 有这个符号链接说明接口成功 attach 了一个 PHY，链接目标就是 `mdio_bus` 里的那个设备。⚠️ **`phydev` 不存在但接口存在 = §4.4 的静默失败**：这是区分「没找到 PHY」和「PHY 找到了但链路不通」的最快判据。
4. **dmesg 里 `attached PHY driver`** —— 格式串在 `drivers/net/phy/phy_device.c:1239`（`ATTACHED_FMT`），由 `phy_attached_info()`（`:1233`）/ `phy_attached_print()`（`:1262`）打出，内容包含 PHY 驱动名、`mii_bus:phy_addr` 和 irq。⚠️ **`irq=POLL` 说明 §5.5 的 `interrupts` 没生效**，退化成轮询了。

### §9.3 运行期动态：链路层面

- `ip link show <if>` —— 接口存不存在、MAC 地址是多少、up/down 状态。⚠️ **MAC 地址每次重启都变 = §2.4 走到了随机兜底**。
- `ethtool <if>` —— 协商结果、当前速率/双工、支持的模式集。⚠️ **`Supported link modes` 被裁掉了高速档 = §5.5 的 `max-speed` 生效了**；`Link detected: no` 且 `phydev` 链接存在 = 硬件/走线问题而不是 DT 问题。
- `ethtool --show-eee <if>` —— 对照 §5.5 的六个 `eee-broken-*` 是否生效。
- `cat /sys/class/net/<if>/carrier` —— 比 `ip link` 更直接的链路状态，`fixed-link` 场景下应恒为 1（除非配了 `link-gpios`）。

⚠️ **`ethtool` 报「no link」和 `phydev` 不存在是两种完全不同的故障**，一个是链路层、一个是发现层。先看 §9.2 的第 3 点，再看这一层。

### §9.4 为什么不讲 ACPI

本文档集范围是 arm64 + DT。ACPI 路径用的是**同一套 `fwnode_*` 接口**，所以 §1.1 里绝大多数属性在 ACPI 下也能通过 `_DSD` 表达，但有三处行为不同，本篇一律不展开、只登记在 `99-evidence.md` §2.7：

- `fwnode_get_phy_node()` 在 ACPI 下**只支持 `phy-handle`**，两个 legacy 别名不生效（`drivers/net/phy/phy_device.c:2995-3002` 的注释）。
- `fwnode_mdiobus_register_phy()` 有一个 ACPI 专属分支（`drivers/net/mdio/fwnode_mdio.c:153-160`）。
- mvpp2 有一个 `mvpp2_use_acpi_compat_mode()`（`drivers/net/ethernet/marvell/mvpp2/mvpp2_main.c:6674`），当 `phy-handle`（`:6679`）、`managed`（`:6680`）、`fixed-link` 子节点（`:6681`）三者**全都不存在**时返回 true，进入一个 ACPI 兼容模式。DT 平台上这三者至少有一个存在，所以永远不会命中。

## §10 症状清单

按「坏掉的范围」分组——这是定位时第一个要判断的维度，因为它直接决定去看 §9 的哪一层。

### §10.1 总线级：整条 MDIO 总线没了

现象：`/sys/bus/mdio_bus/devices/` 为空，所有依赖 PHY 的接口都不通。

| 症状 | 最可能的原因 | 确认动作 |
|---|---|---|
| MDIO 总线完全不出现 | MDIO 子节点名字不对（`mdio` vs `mdio-bus` vs `snps,dwmac-mdio`） | §5.1 表，对照你的驱动 |
| 同上 | MDIO 总线节点 `status = "disabled"` | `__of_mdiobus_register()` 会直接返回 `-ENODEV`（`drivers/net/mdio/of_mdio.c:161-162`） |
| dmesg 有 `mii_bus ... failed to register` | `device_register()` 失败（`drivers/net/phy/mdio_bus.c:559-562`），通常是同名总线冲突 | 查 `bus->id` 的来源，stmmac 走 alias（§7.1） |
| dmesg 有 `mii_bus ... couldn't get reset GPIO` | 总线级 `reset-gpios` 引用了不存在或被占用的 GPIO（`drivers/net/phy/mdio_bus.c:569-573`） | 这是 `dev_err_probe()`，会带 errno；`-EPROBE_DEFER` 是正常的重试 |
| 总线在，但一个 PHY 都没有 | 子节点全都没有 `reg`，进了第二轮扫描但硬件也没响应 | 看 dmesg 有没有 `scan phy`（§5.3） |

### §10.2 单设备级：某一个接口的 PHY 没找到

现象：`/sys/class/net/<if>/phydev` 不存在，但接口本身在。

| 症状 | 最可能的原因 | 确认动作 |
|---|---|---|
| ⚠️ 接口能 up，链路永不 up，**dmesg 干净** | `phy-handle` 指错节点 / 目标 `status = "disabled"`，走了 §4.4 的静默返回 0 | 看 `phydev` 链接存不存在；`/proc/device-tree` 里核对 phandle 目标 |
| 同上，且用的是 `phy = <...>` 写法 | 驱动走 `of_phy_get_and_connect()`，它只认 `phy-handle`（§4.1） | grep 驱动里用的是 `phylink_*` 还是 `of_phy_get_and_connect` |
| dmesg 有 `MDIO device at address N is missing.` | 子节点写了 `reg = <N>`，但那个地址上没有设备响应 | 核对 `reg` 值；确认 PHY 复位是否完成（§5.4） |
| dmesg 有 `scan phy ... at address N` | 子节点没写 `reg`，进了自动扫描（能工作但不该这么写） | 把扫到的地址填进 `reg` |
| dmesg 有 `Whitelisted compatible string. Please remove` | 用了 11 个历史 compatible 之一（§5.2） | 改成 `ethernet-phy-ieee802.3-c22` |
| 接口存在，MAC 地址每次重启都不同 | MAC 地址四级回退全失败，落到 `eth_hw_addr_random()`（§2.4） | 找 `generated random MAC address` 或 mvneta 的来源日志 |
| MAC 地址不是 DTS 里写的那个 | 属性长度不对 / 全零 / 校验失败，静默跳到下一级（§2.1） | `/proc/device-tree/<node>/mac-address` 用 `hexdump` 看实际字节 |

### §10.3 参数级：找到了 PHY，但参数不对

现象：`phydev` 存在，链路可能 up，但性能/稳定性有问题。

| 症状 | 最可能的原因 | 确认动作 |
|---|---|---|
| 链路 up 但完全不通，或大量丢包 | `phy-mode` 的 RGMII 变体选错（`rgmii` / `-id` / `-rxid` / `-txid`，§3.1） | 四个都试一遍是最快的办法；正确值取决于 PCB 走线是否已加延时 |
| 协商不出千兆 | `max-speed` 写在了 PHY 节点上并生效（§5.5） | `ethtool` 看 `Supported link modes` 被裁到哪 |
| 同上，但 `max-speed` 是写在 MAC 节点上的 | stmmac 有自己的一份 `max-speed`（`drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:454`） | 确认你的驱动是不是 stmmac |
| dmesg 有 `snps,phy-addr property is deprecated` | 用了弃用属性（§7.2） | 改用 `phy-handle` |
| 加载了错误的 PHY 驱动 | `compatible = "ethernet-phy-idXXXX.XXXX"` 和实际芯片不符（§5.5） | 删掉 `compatible` 让内核在线读 ID，对比 `ethtool -i` 的 driver 字段 |
| PHY 偶发不响应 / 首次 probe 失败 | 复位时序不够（`reset-assert-us` / `reset-deassert-us` 缺失或太短，§5.4） | 参照 §8.3 的实例值（10ms / 50ms） |
| EEE 导致链路抖动 | 该标 `eee-broken-*` 没标（§5.5） | `ethtool --show-eee` 确认 EEE 是否开启 |
| ⚠️ 属性写了完全无反应 | 属性写错了层级，或本来就是死字 | 先查 §7.2 清单，再回 §1.1 核对「写在哪个节点」 |

### §10.4 拓扑级：多设备互相影响

| 症状 | 最可能的原因 | 确认动作 |
|---|---|---|
| 两个接口 MAC 地址相同 | `nvmem-cells` 引了同一个 cell（§8.4） | `ip link` 对比两个接口 |
| 接口编号（`eth0`/`eth1`）和预期相反 | stmmac 用 `aliases` 里的 `ethernetN` 定序（§7.1） | 看根节点 `aliases`；或者干脆用 udev 规则按 MAC 命名 |
| 级联口一端 up 一端 down | 只写了一端的 `fixed-link`（§8.2） | 两端的 `speed` / `full-duplex` / `pause` 必须逐字一致 |
| dmesg 有 `can't use both fixed-link and in-band-status` | `fixed-link` 和 `managed = "in-band-status"` 同时写了（§4.3） | 二选一 |
| SFP 插拔无反应 | `sfp` 指向的节点 `status = "disabled"`，静默返回 NULL（§6） | `/proc/device-tree` 里核对目标节点 status |
| SFP 口在但速率协商不对 | 缺 `managed = "in-band-status"`，走了固定模式 | 参照 §6 的 mcbin 实例 |
| ⚠️ 一个非 PHY 的 MDIO 设备被当成 PHY | 它没写 `compatible`（§5.2 第 5 条） | 给它加 `compatible` |

## §11 错误消息 → 原因

全部逐字取自源码，可直接 grep 定位。⚠️ **前四条是 `dev_info` / `pr_warn` 级别，不影响功能**，别把它们当故障。

| 消息（逐字） | 打印位置 | 含义 |
|---|---|---|
| `scan phy %pOFn at address %i` | `drivers/net/mdio/of_mdio.c:217` | 子节点缺 `reg`，走了自动扫描并扫到了。能工作，但应把地址补进 DTS |
| `Whitelisted compatible string. Please remove` | `drivers/net/mdio/of_mdio.c:128-130` | 用了 11 个历史 PHY compatible 之一（§5.2） |
| `snps,phy-addr property is deprecated` | `drivers/net/ethernet/stmicro/stmmac/stmmac_platform.c:479-480` | stmmac 弃用属性，换 `phy-handle` |
| `generated random MAC address %pM` | `drivers/net/ethernet/mediatek/mtk_eth_soc.c:3847` | MAC 地址四级回退全失败（§2.4）。mtk 专属，其他驱动的措辞不同 |
| `MDIO device at address %d is missing.` | `drivers/net/mdio/of_mdio.c:195-197` | 子节点 `reg` 指的地址上没有设备响应 |
| `broken fixed-link specification` | `drivers/net/mdio/of_mdio.c:330` | phylib 路径：`of_phy_register_fixed_link()` 失败。检查 `speed` 是否缺失 |
| `broken fixed-link?` | `drivers/net/phy/phylink.c:735` | phylink 路径：老式 5-cell 形式的 cell 数不等于 5 |
| `can't use both fixed-link and in-band-status` | `drivers/net/phy/phylink.c:802-804` | 两种「无 PHY」写法冲突（§4.3） |
| `fixed link specifies half duplex for %dMbps link?` | `drivers/net/phy/phylink.c:756` | `fixed-link` 里速率 >1000 却没写 `full-duplex`。只是警告，但几乎肯定是笔误 |
| `fixed link %s duplex %dMbps not recognised` | `drivers/net/phy/phylink.c:774` | `speed` / duplex 组合不是合法的以太网速率档 |
| `mii_bus %s failed to register` | `drivers/net/phy/mdio_bus.c:561` | MDIO 总线 `device_register()` 失败，返回 `-EINVAL`（`:562`）。多为总线 id 冲突 |
| `mii_bus %s couldn't get reset GPIO` | `drivers/net/phy/mdio_bus.c:571-573` | 总线级 `reset-gpios` 拿不到。这是 `dev_err_probe()`，`-EPROBE_DEFER` 属正常重试 |
| `attached PHY driver %s(mii_bus:phy_addr=%s, irq=%s)` | `drivers/net/phy/phy_device.c:1239` | ⚠️ **不是错误，是成功标志**。`irq=POLL` 说明 `interrupts` 没生效（§5.5） |

**没有对应错误消息的三种失败**，只能靠 §9.2 的观察点发现：

- `phy-handle` 指错 / 目标 disabled，且模式不是 `MLO_AN_PHY` → 静默返回 0（`drivers/net/phy/phylink.c:1800`）
- `sfp` 目标节点 disabled → 静默返回 NULL（`drivers/net/phy/sfp-bus.c:619-622`）
- MAC 地址属性存在但值全零/长度错 → 静默跳到下一级（`net/core/of_net.c:53`）

## §12 读下一篇

| 你接下来想知道 | 去哪篇 |
|---|---|
| 这些属性读进来之后，驱动代码怎么用 | `05-usage.md` §1（probe 七步）、§3（MDIO 总线）、§4（PHY 驱动） |
| `phylink` / `phylib` / `sfp` 三层的分工 | `04-extensions.md` 的 PHY/MDIO/PCS 一节 |
| `net_device` 和 `struct device` 的关系（为什么网卡没有自己的 bus_type） | `02-datastructures.md` §1.0 |
| 注册流程的完整时间线（DT 解析在哪一步） | `03-internals.md` Part A |
| 出问题时还有哪些调试手段（tracepoint / debugfs / 错误码） | `07-debug.md` |
| 为什么「PHY」这个词在内核里指两种完全不同的东西 | `08-feynman.md` |
| 本篇每个数字的口径和重跑命令 | `99-evidence.md` §3 |
