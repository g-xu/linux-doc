---
title: 设备树
---

# IOMMU 子系统 — 设备树配置

> **定位**：DT 属性怎么写、谁消费、配错了怎么定位
>
> **目标架构**：arm64（x86/PowerPC 等架构特例不展开）
>
> **前置阅读**：`01-api.md`（API 速查）、`04-extensions.md`（子框架）

---

## §0 发现路径分诊表

| 发现路径 | 谁扫描 | 走不走 DT | arm64 用 | 本篇覆盖 |
|---------|--------|----------|---------|---------|
| DT `iommus` 属性 | `of_iommu.c` | ★ 是 | ★ 是 | §1-§3 |
| DT `iommu-map` 属性 | `of_iommu.c` | ★ 是 | ★ 是 | §4 |
| DT 控制器节点 | `arm-smmu-v3.c` | ★ 是 | ★ 是 | §2 |
| ACPI IORT | `arm-smmu-v3-acpi.c` | 否 | ★ 是 | 不提 |
| 模块参数 | `iommu.c` | 否 | ★ 是 | `01-api.md` §15 |

---

## §1 按任务找章节

| 你的任务 | 去读 |
|---------|------|
| 写 SMMUv3 控制器节点 | §2 |
| 给设备挂 IOMMU | §3 |
| 理解 `iommu-map` | §4 |
| 配错了怎么查 | §5 |
| 错误消息 → 原因 | §6 |

---

## §1.1 核心真正会读的属性总表

| 属性 | 写在哪个节点 | 谁消费 | 行号 |
|------|------------|--------|------|
| `compatible` | 控制器 | `arm_smmu_device_dt_probe` | `arm-smmu-v3.c:3740` |
| `reg` | 控制器 | `platform_get_resource` | `arm-smmu-v3.c:3835` |
| `interrupts` | 控制器 | `platform_get_irq_byname` | `arm-smmu-v3.c:3860` |
| `#iommu-cells` | 控制器 | `of_iommu.c` | `:78` |
| `iommus` | 设备 | `of_iommu.c` | `:60` |
| `iommu-map` | 父总线 | `of_iommu.c` | `:60` |
| `pasid-num-bits` | 设备 | `arm_smmu_probe_device` | `arm-smmu-v3.c:2704` |
| `dma-can-stall` | 设备 | `arm_smmu_probe_device` | `arm-smmu-v3.c:2722` |
| `dma-coherent` | 控制器 | `arm_smmu_device_dt_probe` | `arm-smmu-v3.c:94` |

---

## §2 SMMUv3 控制器节点

### §2.1 最小示例

```dts
iommu@40000000 {
    compatible = "arm,smmu-v3";
    reg = <0x40000000 0x20000>;
    interrupts = <GIC_SPI 12 IRQ_TYPE_LEVEL_HIGH>,
                 <GIC_SPI 13 IRQ_TYPE_LEVEL_HIGH>,
                 <GIC_SPI 14 IRQ_TYPE_LEVEL_HIGH>,
                 <GIC_SPI 15 IRQ_TYPE_LEVEL_HIGH>;
    interrupt-names = "eventq", "gerror", "cmdq-sync", "priq";
    #iommu-cells = <1>;
};
```

### §2.2 属性详解

#### `compatible`

**值**：`"arm,smmu-v3"`

**消费者**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:3944`

```c
static const struct of_device_id arm_smmu_of_match[] = {
    { .compatible = "arm,smmu-v3", },
    { },
};
```

**陷阱**：写错为 `"arm,smmu-v3.0"` 或 `"arm,smmu3"` 会导致 probe 失败。

#### `reg`

**值**：MMIO 基址和大小

**消费者**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:3835`

```c
res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
if (resource_size(res) < arm_smmu_resource_size(smmu)) {
    dev_err(dev, "MMIO region too small (%pr)\n", res);
    return -EINVAL;
}
```

**陷阱**：区域太小（< 128KB）会报错 `"MMIO region too small"`。

#### `interrupts` + `interrupt-names`

**值**：中断号和名称

**消费者**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:3860`

```c
irq = platform_get_irq_byname_optional(pdev, "combined");
if (irq > 0)
    smmu->combined_irq = irq;
else {
    irq = platform_get_irq_byname_optional(pdev, "eventq");
    if (irq > 0)
        smmu->evtq.q.irq = irq;
    // ... gerror, cmdq-sync, priq
}
```

**可选中断**：

- `combined`：组合中断（可选，替代其他四个）
- `eventq`：事件队列
- `gerror`：全局错误
- `cmdq-sync`：命令同步
- `priq`：PRI 队列

**陷阱**：不写 `interrupt-names` 会导致中断无法获取。

#### `#iommu-cells`

**值**：`1`（SMMUv3 固定）

**消费者**：`drivers/iommu/of_iommu.c:78`

```c
err = of_property_read_u32(iommu_spec.np, "#iommu-cells", &cells);
```

**含义**：`iommus` 属性的参数数量。SMMUv3 用 Stream ID，所以是 1。

#### `dma-coherent`

**值**：布尔（存在即 true）

**消费者**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:94`

```c
if (of_property_read_bool(smmu->dev->of_node, "dma-coherent"))
    smmu->features |= ARM_SMMU_FEAT_COHERENCY;
```

**含义**：SMMU 页表遍历与 CPU 缓存相干。

**陷阱**：只影响 SMMU 本身，不影响上游设备。

---

## §3 设备挂 IOMMU

### §3.1 `iommus` 属性

**写法**：

```dts
ethernet@10000 {
    compatible = "vendor,eth";
    reg = <0x10000 0x1000>;
    iommus = <&smmu 0x1234>;  // Stream ID = 0x1234
};
```

**消费者**：`drivers/iommu/of_iommu.c:60`

```c
err = of_map_id(master_np, *id, "iommu-map",
                "iommu-map-mask", &iommu_spec.np, &iommu_spec.args);
```

**流程**：

1. DT 解析器读取 `iommus` → `of_phandle_args`
2. 调用 `ops->of_xlate(dev, &args)` → `arm_smmu_of_xlate`
3. `iommu_fwspec_add_ids(dev, args->args, 1)` → 保存到 `dev->iommu->fwspec`

#### `of_xlate` 实现

**位置**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2778-2780`

```c
static int arm_smmu_of_xlate(struct device *dev, struct of_phandle_args *args)
{
    return iommu_fwspec_add_ids(dev, args->args, 1);
}
```

**含义**：把 Stream ID 加入 `fwspec->ids[]`。

### §3.2 设备特定属性

#### `pasid-num-bits`

**值**：Sub-Stream ID 位数（0-20）

**消费者**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2704`

```c
device_property_read_u32(dev, "pasid-num-bits", &master->ssid_bits);
master->ssid_bits = min(smmu->ssid_bits, master->ssid_bits);
```

**含义**：设备支持的 PASID 位数。SVA 场景用。

**默认**：0（不支持 SVA）

#### `dma-can-stall`

**值**：布尔

**消费者**：`drivers/iommu/arm/arm-smmu-v3/arm-smmu-v3.c:2722`

```c
if ((smmu->features & ARM_SMMU_FEAT_STALLS &&
     device_property_read_bool(dev, "dma-can-stall")) ||
    smmu->features & ARM_SMMU_FEAT_STALL_FORCE)
    master->stall_enabled = true;
```

**含义**：设备可以暂停 DMA 事务等待页故障处理。SVA 场景用。

**陷阱**：不写此属性，即使 SMMU 支持 stall，设备也不会启用。

---

## §4 `iommu-map` 属性

### §4.1 定位

PCI 总线用 `iommu-map` 自动映射 Device ID 到 Stream ID，无需每个设备写 `iommus`。

### §4.2 语法

```dts
pci@20000000 {
    compatible = "pci-host-generic";
    reg = <0x20000000 0x100000>;
    
    // 映射 PCI BDF 到 Stream ID
    iommu-map = <0x0 &smmu 0x0 0x100>,    // BDF 0x0000 → SID 0x0
                <0x100 &smmu 0x100 0x100>; // BDF 0x0100 → SID 0x100
    
    iommu-map-mask = <0xffff>;             // 掩码
};
```

**格式**：`<bus_id iommu iommu_id count>`

- `bus_id`：PCI BDF（Bus/Device/Function）
- `iommu`：IOMMU phandle
- `iommu_id`：Stream ID
- `count`：映射数量

### §4.3 消费者

**位置**：`drivers/iommu/of_iommu.c:60`

```c
err = of_map_id(master_np, *id, "iommu-map",
                "iommu-map-mask", &iommu_spec.np, &iommu_spec.args);
```

**流程**：

1. PCI 设备 probe 时，读取 BDF
2. `of_map_id()` 查找 `iommu-map` 匹配项
3. 计算 Stream ID = `iommu_id + (BDF & mask)`
4. 调用 `of_xlate` 保存

### §4.4 陷阱

**写错 `iommu-map`**：

- `count` 不对齐 → 部分设备无法映射
- `iommu-map-mask` 缺失 → 默认 0，所有设备映射到同一 SID

---

## §5 配错了怎么定位

### §5.1 编译期

- **DT 语法错误**：`dtc` 编译时报错
- **属性类型错误**：`dt-binding` 校验报错

### §5.2 运行期静态

- **`compatible` 不匹配**：probe 失败，`dmesg` 无 SMMU 日志
- **`reg` 太小**：`"MMIO region too small"` 错误
- **`interrupts` 缺失**：中断无法获取，功能降级

### §5.3 运行期动态

- **`iommus` 缺失**：设备无 IOMMU，DMA 直接走物理地址
- **`#iommu-cells` 错误**：`of_xlate` 失败，设备 probe 失败
- **`iommu-map` 错误**：PCI 设备无法映射，`dmesg` 有 `"failed to map iommu"` 错误

### §5.4 调试命令

```bash
# 检查 IOMMU 是否 probe
dmesg | grep arm-smmu

# 检查设备是否挂 IOMMU
cat /sys/kernel/iommu_groups/*/devices

# 检查 Stream ID
cat /sys/kernel/iommu_groups/*/type
```

---

## §6 错误消息 → 原因

| 错误消息 | 原因 | 解决 |
|---------|------|------|
| `"MMIO region too small"` | `reg` 属性太小 | 增大 `reg` 到至少 128KB |
| `"failed to allocate queue"` | 内存不足 | 检查系统内存 |
| `"failed to map iommu"` | `iommu-map` 错误 | 检查映射表 |
| `"device is not in any iommu group"` | `iommus` 缺失 | 添加 `iommus` 属性 |
| `"of_xlate failed"` | `#iommu-cells` 错误 | 改为 `<1>` |

---

## §7 取证摘要

### §7.1 属性消费者

所有属性经 `grep -n 'of_property_read\|device_property_read'` 验证消费者存在。

### §7.2 DT 绑定文档

基于 `Documentation/devicetree/bindings/iommu/arm,smmu-v3.yaml`。

### §7.3 未验证项

- ACPI IORT 路径未覆盖（arm64 也用，但本篇以 DT 为主线）
- 其他 IOMMU 驱动（apple-dart、mtk_iommu）的 DT 属性未展开

---

## §8 阅读下一篇

| 如果你想知道… | 去读 |
|--------------|------|
| 怎么调试 IOMMU 问题 | `07-debug.md` |
| 用大白话理解这些概念 | `08-feynman.md` |
| 证据和取证方法 | `99-evidence.md` |
