---
title: 设备树
---

# dma-buf 06：设备树

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

---

## §0 结论先行：dma-buf 自己没有设备树绑定

⚠️ **`drivers/dma-buf/` 里没有一处 `of_*` 调用，没有 provider 绑定，没有 consumer
绑定，`Documentation/devicetree/bindings/` 下也没有任何 `dma-buf` 相关的 schema。**

这和本套文档覆盖过的其它子系统形态完全不同：

| 子系统 | DT 形态 |
|---|---|
| pwm / clk / gpio / pinctrl / phy / reset | provider 节点 + `#*-cells` + consumer 引用 → `of_xlate` 解析 |
| **dma-buf** | ⚠️ **无绑定**。缓冲区是**运行时**由某个导出方创建的，不是 DT 描述的硬件资源 |

**为什么**：dma-buf 描述的是"一块内存的共享方式"，不是一个硬件设备。
谁分配、分配多大，全部由运行时的用户态请求决定 —— 设备树没有可描述的东西。

⚠️ **但有一条真实的、必须理解的间接链路**：
**CMA heap 的那块内存来自设备树的 `/reserved-memory`。**
本篇余下全部内容就是这一条链路 —— 它是"改设备树会直接改变 dma-buf 行为"的唯一途径。

---

## §1 唯一真实链路：`/reserved-memory` → CMA → CMA heap → `/dev/dma_heap/<名字>`

### §1.1 完整链路

```
设备树 /reserved-memory 里的一个节点
   compatible = "shared-dma-pool";  reusable;  linux,cma-default;
        │
        │  ① 早期启动：RESERVEDMEM_OF_DECLARE 匹配 compatible
        ▼
kernel/dma/contiguous.c:440   RESERVEDMEM_OF_DECLARE(cma, "shared-dma-pool", rmem_cma_setup)
        │
        │  ② rmem_cma_setup()  kernel/dma/contiguous.c:399-439
        │     校验 reusable / no-map / 对齐 → cma_init_reserved_mem()
        ▼
mm/cma.c:173   cma_init_reserved_mem(base, size, 0, rmem->name, &cma)
        │        └─ cma->name = 设备树节点名（mm/cma.c:199-200）
        │
        │  ③ 有 linux,cma-default ⇒ 记成全局默认区
        ▼
kernel/dma/contiguous.c:430   dma_contiguous_default_area = cma
        │
        │  ④ module_init 阶段：CMA heap 注册
        ▼
drivers/dma-buf/heaps/cma_heap.c:397   dev_get_cma_area(NULL) 取到默认区
drivers/dma-buf/heaps/cma_heap.c:380   exp_info.name = cma_get_name(cma)
        │
        │  ⑤ dma_heap_add() → device_create()
        ▼
drivers/dma-buf/dma-heap.c:306   kasprintf(GFP_KERNEL, "dma_heap/%s", …)
        ▼
                 /dev/dma_heap/<设备树节点名>
```

⚠️⚠️ **设备树节点名就是最终的字符设备名** —— 这是本篇最重要的一条。
改了 DT 里的节点名，用户态 `open("/dev/dma_heap/...")` 的路径就变了。

### §1.2 节点怎么写

绑定文档：**`Documentation/devicetree/bindings/reserved-memory/shared-dma-pool.yaml`（97 行）**，
它在
`Documentation/devicetree/bindings/reserved-memory/shared-dma-pool.yaml:13`
处 `$ref` 到同目录的 `reserved-memory.yaml`。

官方示例（`shared-dma-pool.yaml:78-85`）：

```dts
reserved-memory {
        #address-cells = <1>;
        #size-cells = <1>;
        ranges;

        /* global autoconfigured region for contiguous allocations */
        linux,cma {
                compatible = "shared-dma-pool";
                reusable;
                size = <0x4000000>;          /* 64 MiB */
                alignment = <0x2000>;
                linux,cma-default;
        };
};
```

arm64 树里两个真实例子：

```dts
/* arch/arm64/boot/dts/amlogic/meson-gx.dtsi:58-64 —— 256 MiB */
linux,cma {
        compatible = "shared-dma-pool";
        reusable;
        size = <0x0 0x10000000>;
        alignment = <0x0 0x400000>;
        linux,cma-default;
};

/* arch/arm64/boot/dts/hisilicon/hi6220-hikey.dts:63-68 —— 128 MiB，没写 alignment */
linux,cma {
        compatible = "shared-dma-pool";
        reusable;
        size = <0x00000000 0x08000000>;
        linux,cma-default;
};
```

⚠️ 两个例子的节点名都叫 **`linux,cma`** ⇒ 这两块板子上的字符设备是
**`/dev/dma_heap/linux,cma`**（名字里带逗号，合法但容易被脚本的分隔符切坏）。

### §1.3 五个属性，各自的硬校验

| 属性 | 必需性 | 校验在哪 | 写错的后果 |
|---|---|---|---|
| `compatible = "shared-dma-pool"` | 必需 | `kernel/dma/contiguous.c:440` 的 `RESERVEDMEM_OF_DECLARE` | 不匹配 ⇒ 这块内存只是被保留，不会变成 CMA 区 |
| `reusable` | ⚠️⚠️ **必需** | `kernel/dma/contiguous.c:412-414` | **漏了直接 `-EINVAL`**，CMA 区不建立 |
| `no-map` | ⚠️⚠️ **必须不写** | 同上 `:412-414` | **写了直接 `-EINVAL`** |
| `size` 或 `reg` | 二选一 | `reserved-memory.yaml` | `size` = 让内核自己挑地址；`reg` = 钉死物理地址 |
| `alignment` | 可选 | —— | 不写就只保证 `CMA_MIN_ALIGNMENT_BYTES` |
| `linux,cma-default` | ⚠️ **CMA heap 必需** | `kernel/dma/contiguous.c:402`、`:429-430` | 见 §1.5 |

⚠️⚠️ **`reusable` 与 `no-map` 是一对互斥语义，也是最常踩的一个坑**：

```c
/* kernel/dma/contiguous.c:412-414 */
if (!of_get_flat_dt_prop(node, "reusable", NULL) ||
    of_get_flat_dt_prop(node, "no-map", NULL))
        return -EINVAL;
```

- **`reusable`** = "这块内存平时可以给页分配器当可迁移页用，需要时再腾出来"
  ⇒ **这正是 CMA 的全部意义**。
- **`no-map`** = "内核不要建立线性映射" ⇒ 与 `reusable` 直接冲突。

⚠️ **这两个属性写错时，`dmesg` 里不会出现明确的报错**（`:412-414` 静默返回
`-EINVAL`）⇒ 症状是 `/dev/dma_heap/` 里少一个节点，得倒着查。
唯一相关的日志是成功时的那一条（`:435-436`）：
`Reserved memory: created CMA memory pool at 0x…, size N MiB`
—— **`dmesg | grep 'created CMA memory pool'` 是这条链路的第一个检查点。**

⚠️ 还有一条对齐校验会打印错误（`kernel/dma/contiguous.c:416-419`）：
`Reserved memory: incorrect alignment of CMA region` ⇒ `base | size` 必须满足
`CMA_MIN_ALIGNMENT_BYTES`（`mm/cma.c:190` 处同样校验一遍）。
**arm64 4K 页时这个下限通常是 4 MiB**（`pageblock_order` 决定）
⇒ **写 `size = <0x100000>`（1 MiB）这种小区会直接被拒。**

### §1.4 名字是怎么一路传下来的

| 阶段 | 代码 | 值 |
|---|---|---|
| DT 解析 | `drivers/of/of_reserved_mem.c:73` `rmem->name = uname` | 节点名（含 unit-address，如果有） |
| 建 CMA 区 | `kernel/dma/contiguous.c:421` 把 `rmem->name` 传给 `cma_init_reserved_mem` | 同上 |
| 存进 CMA | `mm/cma.c:199-200` `snprintf(cma->name, CMA_MAX_NAME, name)` | ⚠️ 截断到 **64 字节**（`include/linux/cma.h:17`） |
| 取出来 | `mm/cma.c:52-55` `cma_get_name()` | 同上 |
| 变成 heap 名 | `drivers/dma-buf/heaps/cma_heap.c:380` `exp_info.name = cma_get_name(cma)` | 同上 |
| 变成设备节点 | `drivers/dma-buf/dma-heap.c:306` `kasprintf(…, "dma_heap/%s", …)` | `/dev/dma_heap/<名字>` |

⚠️⚠️ **`mm/cma.c:200` 把 `name` 当成了 `snprintf` 的格式串**
（`snprintf(cma->name, CMA_MAX_NAME, name)`，没有 `"%s"`）
⇒ **节点名里如果出现 `%`，会被当成转换说明符解释**。
设备树节点名正常不会带 `%`，但这是一处真实的、6.1 里存在的隐患，
排查"heap 名字莫名其妙"时值得记住。

⚠️ 另外 `mm/cma.c:202` 的 fallback 名是 `"cma%d\n"` —— **带一个换行符**。
这条路径只在 `name == NULL` 时走（CMA heap 这条链路永远不会走到），
但如果哪天有别的调用者走到了，heap 名里就会带换行 ⇒ `/dev/dma_heap/` 里出现怪名字。

⚠️ **`heap->name` 不做 `kstrdup`**（`drivers/dma-buf/dma-heap.c:240`，04 §1.2）
⇒ 这里正好是安全的：`cma->name` 是 `struct cma` 里的**内嵌数组**（`mm/cma.h:24`
的 `char name[CMA_MAX_NAME]`），而 `cma_areas[]` 是永久的全局数组 ⇒ 字符串永不失效。
**换成别的分配方式就会变成悬垂指针。**

### §1.5 ⚠️⚠️ 只有"默认 CMA 区"会变成 heap

```c
/* drivers/dma-buf/heaps/cma_heap.c:395-404 */
static int add_default_cma_heap(void)
{
        struct cma *default_cma = dev_get_cma_area(NULL);
        int ret = 0;

        if (default_cma)
                ret = __add_cma_heap(default_cma, NULL);

        return ret;
}
```

三个后果，都很重要：

1. ⚠️⚠️ **DT 里定义了多个 `shared-dma-pool` 区，也只有带 `linux,cma-default`
   的那一个能变成 `/dev/dma_heap/*`。** 其它区照样建立、照样能被绑到具体设备上
   （通过 consumer 的 `memory-region`），但用户态拿不到。
2. ⚠️ **`__add_cma_heap()` 是 `static` 且只有这一个调用者**（04 §2.2）
   ⇒ 6.1 里**没有任何办法**把第二块 CMA 区暴露成 heap，只能改内核。
3. ⚠️ **没有默认区时 `add_default_cma_heap()` 静默返回 0**（`:398` 的 `int ret = 0`）
   ⇒ **`CONFIG_DMABUF_HEAPS_CMA=y` 但 DT 里没写 `linux,cma-default`，
   开机毫无提示，`/dev/dma_heap/` 里就是只有 `system` 一个节点。**

`dev_get_cma_area()` 的实现（`include/linux/dma-map-ops.h:115-120`）：

```c
if (dev && dev->cma_area)
        return dev->cma_area;
return dma_contiguous_default_area;
```

传 `NULL` ⇒ 必然走第二条 ⇒ **就是那个被 `linux,cma-default` 标记的区**
（`kernel/dma/contiguous.c:429-430` 赋值处）。

⚠️ CMA 区总数上限是 `CONFIG_CMA_AREAS`，**arm64 UMA 下默认只有 7**
（`mm/Kconfig:883-887`）⇒ 超了 `cma_init_reserved_mem()` 报
`Not enough slots for CMA reserved regions!` 并返回 `-ENOSPC`（`mm/cma.c:181-184`）。

### §1.6 内核命令行 `cma=` 会**顶掉**设备树

两条路径都能产生默认 CMA 区，冲突时命令行赢：

```c
/* kernel/dma/contiguous.c:406-410 —— rmem_cma_setup() 的第一件事 */
if (size_cmdline != -1 && default_cma) {
        pr_info("Reserved memory: bypass %s node, using cmdline CMA params instead\n",
                rmem->name);
        return -EBUSY;
}
```

⚠️⚠️ **只要 bootargs 里出现 `cma=`，DT 里那个 `linux,cma-default` 节点就整个被跳过。**
`size_cmdline` 由 `early_cma()` 设置（`kernel/dma/contiguous.c:77-96`，
`early_param("cma", early_cma)` 在 `:96`），语法是 `cma=<size>[@<base>[-<limit>]]`。

⇒ **排查"heap 大小和设备树不一致"时，第一件事是看 `/proc/cmdline`。**
日志里那条 `bypass … using cmdline CMA params instead` 是确证。

⚠️ 没有 DT 节点也没有 `cma=` 时，还有第三条路：`CONFIG_CMA_SIZE_MBYTES` 等
Kconfig 默认值（`kernel/dma/Kconfig:146-181` 的一组 `CMA_SIZE_*`），
由 `dma_contiguous_reserve()` 使用（`kernel/dma/contiguous.c:168-199`）。
**三条路的优先级：`cma=` 命令行 > DT 的 `linux,cma-default` > Kconfig 默认值。**

### §1.7 对齐：三个不同的"对齐"别搞混

| 谁 | 在哪 | 管什么 |
|---|---|---|
| DT 的 `alignment` 属性 | `shared-dma-pool.yaml:83` 示例 | ⚠️ **整块 CMA 区的起始地址对齐** |
| `CMA_MIN_ALIGNMENT_BYTES` | 校验在 `kernel/dma/contiguous.c:416`、`mm/cma.c:190` | 区的 base/size 的**硬性下限**（由 `pageblock_order` 决定） |
| **`CONFIG_CMA_ALIGNMENT`** | `kernel/dma/Kconfig:186-203`，**默认 8，range 2-12** | ⚠️⚠️ **单次分配的对齐上限** |

⚠️⚠️ `CONFIG_CMA_ALIGNMENT` 是 CMA heap 里那个夹取的来源
（`drivers/dma-buf/heaps/cma_heap.c:300-301`，05 §1.4）：

```c
align = get_order(size);                                /* :286 */
if (align > CONFIG_CMA_ALIGNMENT)  align = CONFIG_CMA_ALIGNMENT;   /* :300-301 */
```

**默认值 8 ⇒ 4K 页时对齐上限 1 MiB**（Kconfig help `:199-200` 原文举的就是这个例子）。
⇒ **申请一块 8 MiB 的 CMA 缓冲区，拿到的地址只保证 1 MiB 对齐，不是 8 MiB 对齐。**
需要更强对齐的硬件（某些视频编解码器要求整块自然对齐）**必须自己检查返回地址**，
或者把 `CONFIG_CMA_ALIGNMENT` 调到 12（= 4K 页时 16 MiB）。

⚠️ 这个选项是**全局**的，调大意味着所有 CMA 分配都更浪费。
Kconfig 自己的建议是"If unsure, leave the default value 8"（`kernel/dma/Kconfig:202`）。

---

## §2 arm64 树里的现状普查

| 统计 | 数字 | 含义 |
|---|---|---|
| `arch/arm64/boot/dts/` 里含 `shared-dma-pool` 的文件 | **40** | 定义了 CMA 或 DMA 池的板子 |
| 其中含 **`linux,cma-default`** 的 | ⚠️ **只有 9** | **只有这 9 个能得到 `/dev/dma_heap/<name>`** |
| 含 `memory-region`（消费者引用保留区） | **54** | 见 §3，与 dma-buf 无关 |

那 9 个文件：`amlogic/meson-gx.dtsi` · `amlogic/meson-a1.dtsi` ·
`amlogic/meson-g12-common.dtsi` · `freescale/imx8mp-tqma8mpql-mba8mpxl.dts` ·
`freescale/imx8mn-tqma8mqnl.dtsi` · `freescale/imx8mq-tqma8mq.dtsi` ·
`freescale/imx8mm-tqma8mqml.dtsi` · `freescale/imx8dxl-evk.dts` ·
`hisilicon/hi6220-hikey.dts`。

⚠️⚠️ **40 比 9 这个差距就是本篇的实用结论**：
**绝大多数 arm64 板子有 `shared-dma-pool` 却没有 `linux,cma-default`**
⇒ 这些板子哪怕开了 `CONFIG_DMABUF_HEAPS_CMA=y`，
**`/dev/dma_heap/` 里也只有 `system` 一个节点**，而且开机没有任何提示（§1.5 第 3 条）。

⇒ **想让一块板子提供 CMA heap，唯一的动作就是给它的 CMA 节点加上
`linux,cma-default;` 这一行。**

---

## §3 别混淆：`memory-region` 与 dma-buf 无关

54 个 arm64 文件里出现的 `memory-region = <&some_reserved>;` 是**另一条链路**：

| 属性 | 作用 | 与 dma-buf 的关系 |
|---|---|---|
| `memory-region` | 把某个保留区绑定到**某一个设备**（`dev->cma_area` 或 `dev->dma_mem`） | ⚠️ **无关**。这块内存只有那个设备的驱动能用，不会出现在 `/dev/dma_heap/` |
| `dma-coherent` | 声明该设备的 DMA 是一致的 | ⚠️ 无关，但会影响导入方 `begin/end_cpu_access` 是否真的要刷 cache |
| `iommus` / `dma-ranges` | 该设备的地址转换与可寻址范围 | ⚠️ **间接相关**：决定导入方 `dma_map_sgtable()` 能不能成功、能拿到什么 DMA 地址 |

⚠️ 消费者侧的绑定文档是
`Documentation/devicetree/bindings/reserved-memory/memory-region.yaml`
（与 `shared-dma-pool.yaml` 是同一目录的两个不同 schema，别互相抄）。

⚠️⚠️ **`dma-ranges` 是 arm64 上最容易被忽略的一条**：
udmabuf 无条件声称自己能寻址 64 位（`drivers/dma-buf/udmabuf.c:381-382`，05 §4.5）
⇒ 如果导入设备的 `dma-ranges` 限制在 32 位以内，**数据会静默损坏**。
这条链路上设备树确实参与了，但参与的是**导入方**那一侧，不是 dma-buf 核心。

---

## §4 `restricted-dma-pool`：同一份 schema 里的另一个 compatible，不是 CMA

`shared-dma-pool.yaml:25-45` 定义了第二个 compatible 值 `restricted-dma-pool`。

⚠️⚠️ **它和 CMA heap 没有任何关系**：
`RESERVEDMEM_OF_DECLARE` 只匹配 `"shared-dma-pool"`（`kernel/dma/contiguous.c:440`）
⇒ **`restricted-dma-pool` 永远不会变成 CMA 区，也永远不会变成 dma-heap**。

它的用途是"给没有 IOMMU 的系统做一层 DMA 访问隔离"
（`shared-dma-pool.yaml:32-38` 原文：mitigate the lack of DMA access control on
systems without an IOMMU … possibly leading to data leakage or corruption）。

⚠️ schema 里还有一条条件约束（`shared-dma-pool.yaml:59-67`）：
`restricted-dma-pool` 时 **`no-map` 和 `reusable` 都必须为 false**
—— 和 CMA 的要求（**必须有 `reusable`**）**正好相反**。
⇒ **两个 compatible 的属性要求互斥，绝不能照抄。**

⚠️ 还有一个 `linux,dma-default`（`shared-dma-pool.yaml:53-57`）——
它标记的是"一致性 DMA 分配器的默认池"，**又是另一条路**，
与 `linux,cma-default`（`:47-51`）不是同一个东西。三个名字很像，别看错。

---

## §5 排查清单：`/dev/dma_heap/<cma>` 没出现，按序查

| # | 检查 | 命令 / 检查点 |
|---|---|---|
| 1 | 内核配置 | `CONFIG_DMABUF_HEAPS=y` + `CONFIG_DMABUF_HEAPS_CMA=y` + `CONFIG_DMA_CMA=y`（`drivers/dma-buf/heaps/Kconfig:8-10` 的 `depends on DMABUF_HEAPS && DMA_CMA`） |
| 2 | ⚠️ **是否 bool** | `DMABUF_HEAPS_CMA` 是 **bool 不是 tristate**（`drivers/dma-buf/heaps/Kconfig:9`）⇒ **只能 `=y`，写 `=m` 无效** |
| 3 | CMA 区建起来了吗 | `dmesg \| grep 'created CMA memory pool'`（日志出自 `kernel/dma/contiguous.c:435-436`） |
| 4 | 被命令行顶掉了吗 | `dmesg \| grep 'bypass.*cmdline CMA'` + `cat /proc/cmdline \| grep cma=`（§1.6） |
| 5 | 对齐被拒了吗 | `dmesg \| grep 'incorrect alignment of CMA region'`（`kernel/dma/contiguous.c:417`） |
| 6 | 槽位用满了吗 | `dmesg \| grep 'Not enough slots for CMA'`（`mm/cma.c:182`）⇒ 调大 `CONFIG_CMA_AREAS`（`mm/Kconfig:883`） |
| 7 | ⚠️ **有 `linux,cma-default` 吗** | `grep -r 'linux,cma-default' <你的 dts>`；**没有就一定没有 heap，且无任何日志**（§1.5） |
| 8 | `reusable` 写了吗 / `no-map` 写了吗 | `kernel/dma/contiguous.c:412-414` 的静默 `-EINVAL` |
| 9 | 名字对不对 | `ls /dev/dma_heap/` —— 名字是**节点名**，不是 `cma`（§1.4） |
| 10 | CMA 总量 | `cat /proc/meminfo \| grep Cma`（`CmaTotal` / `CmaFree`） |

⚠️ **第 7 条是实际最高频的原因**（§2 的 40 vs 9），
而它恰恰是**唯一一条内核完全不打日志的**。

⚠️ 运行期还有一个只读检查点：CMA heap 分配失败时的返回码是 `-ENOMEM`
（`cma_alloc()` 失败，`drivers/dma-buf/heaps/cma_heap.c:303`）
⇒ **`-ENOMEM` 不区分"CMA 区太小"和"CMA 区被不可迁移页占住了"**。
后者要看 `CmaFree` 明显大于请求量却仍然失败 —— 那就是碎片/pin 住的问题，
不是设备树的问题。

---

## §6 与本套其它篇的关系

| 你的问题 | 去 |
|---|---|
| `/dev/dma_heap/*` 的 ioctl 怎么调 | 05 §4.1 |
| CMA heap 的分配路径细节（清零、对齐夹取） | 04 §2.2、05 §1.4 |
| `dma_heap_add()` 为什么不 `kstrdup` 名字 | 04 §1.2 |
| `system` heap 为什么在 arm64 64K 页上退化 | 04 §2.1 |
| CMA 分配失败之后怎么继续查 | 07-debug.md |
| 为什么这个框架不需要设备树 | 08-feynman.md |

⚠️ **本篇不覆盖**：`reserved-memory` 的通用语义（属 of/内存管理子系统）、
CMA 自身的迁移算法（属 `mm/cma.c`）、`restricted-dma-pool` 的实现（属 swiotlb）。
本篇只覆盖"设备树如何影响 dma-buf 的可见行为"这一条边界。
