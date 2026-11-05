# iomap（IO 内存映射）

### 定位

**功能**：把设备寄存器物理地址映射到内核虚拟地址空间，使驱动能通过 `void __iomem *` 指针读写硬件寄存器。这是所有 MMIO 设备驱动访问硬件的基础——没有 iomap，驱动无法与设备寄存器交互。

**实现方式**：底层通过 `ioremap()`（`kernel/ioremap.c`）建立物理地址到虚拟地址的映射，返回带 `__iomem` 标注的指针。`devm_ioremap_resource()` 把三步操作（`request_mem_region` + `devm_ioremap` + 错误检查）合一。最常用的 `devm_platform_ioremap_resource()`（`lib/devres.c`）进一步封装，直接从 `platform_device` 的第 N 个 MEM 资源取物理地址和大小，一步完成映射。映射后通过 `readl`/`writel` 等函数访问寄存器，arm64 上这些函数自带 `dma_mb()` 屏障保证弱序处理器上的访问顺序；`_relaxed` 变体不带屏障，性能更好但不保证顺序。

**应用场景**：`devm_platform_ioremap_resource` 全树 1,231 处调用，`devm_ioremap_resource` 798 处——几乎所有需要访问硬件寄存器的 platform 驱动都在 probe 中使用它们。涵盖 UART、SPI 控制器、I2C 控制器、GPIO 控制器、网络 MAC、USB 控制器等全部 SoC 片上外设。

---

## 1. 核心 API

头文件：`include/linux/io.h`；实现：`kernel/ioremap.c` + `lib/devres.c`

### 1.1 基础映射函数

| API | 说明 | 备注 |
|-----|------|------|
| `ioremap(phys, size)` | 物理地址 → 虚拟地址 | 返回 `void __iomem *` |
| `iounmap(addr)` | 解除映射 | 与 `ioremap` 配对 |
| `devm_ioremap(dev, phys, size)` | 设备管理版，detach 自动释放 | 推荐 |
| `devm_ioremap_resource(dev, res)` | **一步完成**：从 resource 取物理地址 + 大小 → `request_mem_region` → `devm_ioremap` | **最常用**，798 处 |

### 1.2 platform_device 专用

| API | 说明 | 备注 |
|-----|------|------|
| `devm_platform_ioremap_resource(pdev, index)` | 从 platform_device 的第 index 个 MEM 资源映射 | **1,231 处**，驱动 probe 标配 |
| `devm_platform_ioremap_resource_byname(pdev, name)` | 按资源名字映射 | |
| `devm_platform_get_and_ioremap_resource(pdev, idx, &res)` | 映射并返回 resource 指针 | 需要 resource 信息时用 |

`devm_platform_ioremap_resource` 内部做了三件事：
1. `platform_get_resource(pdev, IORESOURCE_MEM, index)` — 取资源
2. `devm_request_mem_region()` — 申请地址空间
3. `devm_ioremap()` — 映射

任何一步失败返回 `ERR_PTR`，驱动用 `IS_ERR()` 检查。

### 1.3 读写寄存器

映射后通过专用读写函数访问（`include/asm-generic/io.h`）：

| API | 说明 |
|-----|------|
| `readl(addr)` / `readw(addr)` / `readb(addr)` | 读 32/16/8 位寄存器 |
| `writel(val, addr)` / `writew(val, addr)` / `writeb(val, addr)` | 写 32/16/8 位寄存器 |
| `readl_relaxed(addr)` | 不强制排序的读（性能更好） |
| `writel_relaxed(val, addr)` | 不强制排序的写 |
| `ioread32(addr)` / `iowrite32(val, addr)` | 统一接口，兼容 PIO 和 MMIO |

arm64 上 `readl` / `writel` 自带 `dma_mb()` 屏障，保证弱序处理器上的访问顺序。`_relaxed` 变体不带屏障。

---

## 2. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `devm_platform_ioremap_resource` | 1,231 | 最常用映射入口 |
| `devm_ioremap_resource` | 798 | 通用版本 |
