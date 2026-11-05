# KASAN — 内核地址消毒器

> **定位**：KASAN（Kernel Address Sanitizer）是 Linux 内核的内存错误检测工具，能发现越界访问和释放后使用（use-after-free）bug。提供三种模式：Generic（影子内存）、SW_TAGS（软件标记）、HW_TAGS（硬件标记，arm64 MTE）。本文覆盖 KASAN 核心机制及 arm64 实现。

---

## 1. 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `mm/kasan/common.c` | 452 | 通用 slab/page alloc/free 钩子 |
| `mm/kasan/generic.c` | 519 | Generic 模式：影子检查、`__asan_load/store*` |
| `mm/kasan/shadow.c` | 598 | 影子内存管理：`kasan_poison`/`kasan_unpoison` |
| `mm/kasan/report.c` | 594 | 错误报告 |
| `mm/kasan/report_generic.c` | 369 | Generic 模式报告 |
| `mm/kasan/quarantine.c` | 438 | 隔离区：延迟 slab 对象重用 |
| `mm/kasan/init.c` | 491 | 影子内存页表初始化 |
| `mm/kasan/sw_tags.c` | 178 | SW_TAGS 核心 |
| `mm/kasan/hw_tags.c` | 336 | HW_TAGS 核心 |
| `mm/kasan/tags.c` | 144 | 标记模式通用代码 |
| `mm/kasan/kasan.h` | 635 | 内部头文件 |
| `include/linux/kasan.h` | 480 | 公开 API |
| `arch/arm64/mm/kasan_init.c` | 317 | arm64 影子内存页表设置 |
| `arch/arm64/include/asm/mte-kasan.h` | 178 | MTE 内联函数 |
| `arch/arm64/include/asm/kasan.h` | 49 | arm64 KASAN 宏 |

---

## 2. 三种模式

| 模式 | 配置 | 开销 | 机制 |
|------|------|------|------|
| **Generic** | `CONFIG_KASAN_GENERIC` | ~1/8 内存，~x3 减速 | 影子内存，1 字节/8 字节内存 |
| **SW_TAGS** | `CONFIG_KASAN_SW_TAGS` | ~1/16 内存，~20% 分配开销 | 软件标记，TBI，1 字节/16 字节 |
| **HW_TAGS** | `CONFIG_KASAN_HW_TAGS` | ~1/32 内存 | 硬件标记，arm64 MTE，4 位/16 字节 |

---

## 3. 核心数据结构

### 3.1 影子值常量（Generic 模式，`mm/kasan/kasan.h:109-149`）

| 常量 | 值 | 含义 |
|------|-----|------|
| `KASAN_PAGE_FREE` | `0xFF` | 已释放页 |
| `KASAN_SLAB_REDZONE` | `0xFC` | slab 对象红区 |
| `KASAN_SLAB_FREE` | `0xFB` | 已释放 slab 对象 |
| `KASAN_GLOBAL_REDZONE` | `0xF9` | 全局变量红区 |
| `KASAN_STACK_LEFT` | `0xF1` | 栈左红区 |
| `KASAN_STACK_MID` | `0xF2` | 栈中红区 |
| `KASAN_STACK_RIGHT` | `0xF3` | 栈右红区 |

### 3.2 标记常量（`include/linux/kasan-tags.h`）

| 常量 | 值 | 含义 |
|------|-----|------|
| `KASAN_TAG_KERNEL` | `0xFF` | 原生内核指针标记 |
| `KASAN_TAG_INVALID` | `0xFE` | 不可访问内存标记 |
| `KASAN_TAG_MAX` | `0xFD` | 最大随机标记值 |

### 3.3 元数据结构

```c
// mm/kasan/kasan.h:160-163
struct kasan_track {
    u32 pid;
    depot_stack_handle_t stack;
};

// mm/kasan/kasan.h:217-221
struct kasan_alloc_meta {
    struct kasan_track alloc_track;
    depot_stack_handle_t aux_stack[2];
};

// mm/kasan/kasan.h:238-241
struct kasan_free_meta {
    struct qlist_node quarantine_link;
    struct kasan_track free_track;
};
```

---

## 4. 核心 API

头文件：`include/linux/kasan.h`

### 4.1 Slab/Page 分配钩子

| API | 说明 | 备注 |
|-----|------|------|
| `kasan_slab_alloc(s, object, flags, init)` | slab 分配时消毒 | 分配标记、解除毒化、保存分配信息 |
| `kasan_slab_free(s, object, init)` | slab 释放时毒化 | 验证释放、毒化、保存释放信息、放入隔离区 |
| `kasan_poison_pages(page, order, init)` | 释放页毒化 | 用 `KASAN_PAGE_FREE` 填充影子 |
| `kasan_unpoison_pages(page, order, init)` | 分配页解毒 | 分配随机标记 |
| `kasan_init_slab_obj(cache, object)` | slab 对象初始化 | 初始化元数据、分配初始标记 |

### 4.2 毒化/解毒

| API | 说明 | 备注 |
|-----|------|------|
| `kasan_poison(addr, size, value, init)` | 写入影子字节 | Generic/SW：写影子内存；HW：设置 MTE 标记 |
| `kasan_unpoison(addr, size, init)` | 解除毒化 | Generic：设置影子为 0；标记模式：设置指针标记 |
| `kasan_poison_last_granule(addr, size)` | 尾部部分毒化 | 仅 Generic；设置部分影子字节 |

### 4.3 内存访问检查

| API | 说明 | 备注 |
|-----|------|------|
| `kasan_check_range(addr, size, write, ret_ip)` | 检查地址范围 | Generic：检查影子字节；SW_TAGS：比较指针标记 |
| `__kasan_check_read(p, size)` | 读检查包装 | |
| `__kasan_check_write(p, size)` | 写检查包装 | |
| `__asan_load*/__asan_store*` | 编译器插桩检查 | 大小 1,2,4,8,16 |

### 4.4 报告

| API | 说明 | 备注 |
|-----|------|------|
| `kasan_report(addr, size, is_write, ip)` | 访问违规报告 | 主报告入口 |
| `kasan_report_invalid_free(ptr, ip, type)` | 无效释放报告 | double-free / invalid-free |
| `kasan_report_async()` | HW_TAGS 异步故障报告 | 无地址详情 |

---

## 5. 机制剖析

### 5.1 影子内存概念

**Generic 模式**：每 8 字节内存对应 1 字节影子（`KASAN_SHADOW_SCALE_SHIFT=3`）。

**地址映射公式**（`include/linux/kasan.h:57-61`）：
```c
shadow_addr = (addr >> KASAN_SHADOW_SCALE_SHIFT) + KASAN_SHADOW_OFFSET
```

**SW_TAGS**：每 16 字节对应 1 字节影子（`KASAN_SHADOW_SCALE_SHIFT=4`）。

**HW_TAGS**：无影子内存；标记存储在硬件中（每 16 字节 4 位 = MTE 粒度）。

### 5.2 Slab 毒化/解毒流程

**slab 页分配**（`mm/kasan/common.c:125-134`）：`__kasan_poison_slab()` 用 `KASAN_SLAB_REDZONE` 毒化整个 slab 页。

**对象初始化**（`mm/kasan/common.c:187-198`）：`__kasan_init_slab_obj()` 初始化元数据并分配初始标记。

**slab_alloc**（`mm/kasan/common.c:298-331`）：`__kasan_slab_alloc()` 分配随机标记（标记模式）、解毒 `cache->object_size` 字节、保存分配信息。

**kmalloc**（`mm/kasan/common.c:333-378`）：`____kasan_kmalloc()` 毒化请求大小后的红区。Generic 模式下 `kasan_poison_last_granule()` 设置部分影子字节实现字节精度。

**slab_free**（`mm/kasan/common.c:200-239`）：`____kasan_slab_free()` 验证对象在 slab 边界内、检查 double-free、用 `KASAN_SLAB_FREE` 毒化、保存释放信息、放入隔离区（Generic）。

### 5.3 隔离区（Quarantine）

`mm/kasan/quarantine.c`（438 行）实现延迟重用机制：
- 释放的对象不立即返回给 slab，而是放入隔离区
- 隔离区按大小分桶，总大小受 `kasan_quarantine_size` 限制
- 当隔离区满时，最旧的对象被释放回 slab
- 这增加了 use-after-free 被检测到的概率

### 5.4 栈插桩

编译器为局部变量插入影子字节写入：
- `KASAN_STACK_LEFT`（0xF1）、`KASAN_STACK_MID`（0xF2）、`KASAN_STACK_RIGHT`（0xF3）
- 由 `CONFIG_KASAN_STACK` 控制

### 5.5 全局变量插桩

- 编译器发出 `struct kasan_global` 描述符
- `__asan_register_globals()`（`mm/kasan/generic.c:226-233`）为每个全局变量设置 `KASAN_GLOBAL_REDZONE`（0xF9）红区

---

## 6. arm64 实现

### 6.1 MTE（Memory Tagging Extension）用于 HW_TAGS

**文件**：`arch/arm64/include/asm/mte-kasan.h`

| 函数 | 行号 | 说明 |
|------|------|------|
| `mte_get_ptr_tag(ptr)` | 25 | 从指针顶部位提取 4 位标记 |
| `mte_get_mem_tag(addr)` | 34 | 使用 `ldg` 指令读取内存分配标记 |
| `mte_get_random_tag()` | 43 | 使用 `irg` 指令生成随机标记 |
| `mte_set_mem_tag_range(addr, size, tag, init)` | 86 | 使用 `stg`/`stzg` + `dc gva` 设置标记范围 |

**内核 MTE 启用**（`arch/arm64/kernel/mte.c`）：
- `mte_enable_kernel_sync()`：设置 `SCTLR_EL1.TCF = Sync`
- `mte_enable_kernel_async()`：设置 `SCTLR_EL1.TCF = Async`
- `mte_enable_kernel_asymm()`：设置 `SCTLR_EL1.TCF = Asymm`

### 6.2 影子内存映射

**文件**：`arch/arm64/mm/kasan_init.c`（317 行）

- `kasan_early_init()`（行 169）：映射早期影子（单个零页）到整个影子区域
- `kasan_init_shadow()`（行 214）：完整影子设置——清除早期映射，为内核映像、模块、vmalloc、物理内存范围填充影子
- `kasan_init()`（行 307）：启动入口

**KASAN_SHADOW_SCALE_SHIFT**：
- Generic 模式：**3**（1 字节/8 字节）
- SW_TAGS 模式：**4**（1 字节/16 字节）

### 6.3 TBI（Top Byte Ignore）用于标记模式

**文件**：`arch/arm64/include/asm/memory.h:236-250`

```c
#define __tag_shifted(tag)  ((u64)(tag) << 56)
#define __tag_reset(addr)   __untagged_addr(addr)  // 剥离顶部字节
#define __tag_get(addr)     (__u8)((u64)(addr) >> 56)  // 提取顶部字节
```

标记存储在指针的顶部字节（位 56-63）。arm64 TBI 硬件在地址转换时忽略此字节，允许内核嵌入元数据而不影响内存访问。

---

## 7. 配置选项

**文件**：`lib/Kconfig.kasan`

| 选项 | 说明 |
|------|------|
| `CONFIG_KASAN` | 主开关 |
| `CONFIG_KASAN_GENERIC` | Generic 模式 |
| `CONFIG_KASAN_SW_TAGS` | 软件标记模式（仅 arm64） |
| `CONFIG_KASAN_HW_TAGS` | 硬件标记模式（arm64 ARMv8.5+ MTE） |
| `CONFIG_KASAN_INLINE` | 内联插桩（更快但更大） |
| `CONFIG_KASAN_OUTLINE` | 轮廓插桩（更小但更慢） |
| `CONFIG_KASAN_STACK` | 栈插桩 |
| `CONFIG_KASAN_VMALLOC` | 检查 vmalloc 访问 |

**内核命令行参数**：
- `kasan=off/on`
- `kasan.mode=sync/async/asymm`（HW_TAGS）
- `kasan.vmalloc=off/on`
- `kasan.stacktrace=off/on`
- `kasan.fault=report/panic`
- `kasan_multi_shot`

---

## 8. 错误报告格式

报告结构：
1. `BUG: KASAN: <bug_type> in <address>`
2. `Read/Write of size N at addr PX by task comm/pid`
3. 标记信息（标记模式）：`Pointer tag: [XX], memory tag: [XX]`
4. 栈回溯
5. 对象描述：cache 名、大小、相对于对象的位置
6. 影子字节转储，`^` 标记错误位置
7. 分配/释放跟踪栈

**bug 类型**：`out-of-bounds`、`slab-out-of-bounds`、`global-out-of-bounds`、`stack-out-of-bounds`、`use-after-free`、`vmalloc-out-of-bounds`、`null-ptr-deref`、`user-memory-access`、`wild-memory-access`

---

## 9. SLUB 集成点

| 位置 | 钩子 | 上下文 |
|------|------|--------|
| `mm/slub.c:4273` | `kasan_cache_create()` | cache 创建时添加元数据+红区 |
| `mm/slub.c:1779` | `kasan_init_slab_obj()` | 分配新 slab 对象时 |
| `mm/slub.c:1966` | `kasan_poison_slab()` | 分配 slab 页时 |
| `mm/slub.c:1729` | `kasan_slab_free()` | 对象释放时 |
| `mm/slub.c:652-662` | `kasan_disable_current()` | 元数据访问周围抑制误报 |

`kasan_has_integrated_init()`（`include/linux/kasan.h:93-96`）仅对 HW_TAGS 返回 true，表示架构在硬件中处理初始化。

---

## §4 常见陷阱

### 陷阱 1：在标记模式下期望 per-object 元数据

**错误写法**：
```c
/* 假设在 HW_TAGS 模式下也能拿到 alloc_meta */
struct kasan_alloc_meta *meta = kasan_get_alloc_meta(cache, object);
/* 用 meta->alloc_track.stack 做自定义分析 */
```

**正确写法**：
```c
/* 先检查当前模式是否提供元数据 */
if (IS_ENABLED(CONFIG_KASAN_GENERIC)) {
    struct kasan_alloc_meta *meta = kasan_get_alloc_meta(cache, object);
    /* 仅在 Generic 模式下使用 */
}
```

**原因**：`kasan_metadata_size()` 在标记模式下返回 0（`include/linux/kasan.h:318-321`），`kasan_requires_meta()` 返回 false。标记模式（SW_TAGS/HW_TAGS）不使用 `kasan_alloc_meta`/`kasan_free_meta` 结构——它们只依赖指针标记和影子字节，没有空间存储分配/释放栈信息。`kasan_record_aux_stack()` 也仅在 Generic 模式下有效（`include/linux/kasan.h:334`，标记模式下为空函数）。

**源码依据**：`include/linux/kasan.h:317-335` — 标记模式的 stub 实现全部返回空值。

### 陷阱 2：对 vmalloc 区域使用 KASAN 但未开启 CONFIG_KASAN_VMALLOC

**错误写法**：
```c
void *p = vmalloc(4096);
/* 假设 KASAN 会自动检测 vmalloc 区域的越界 */
p[4096] = 0xFF;  /* 越界写入——在 Generic 模式下不会触发 KASAN 报告 */
```

**正确写法**：
```c
/* 确保内核配置了 CONFIG_KASAN_VMALLOC=y */
/* 或在代码中手动解毒/毒化 vmalloc 区域 */
void *p = vmalloc(4096);
kasan_unpoison_vmalloc(p, 4096, KASAN_VMALLOC_VM_ALLOC);
/* 使用完毕后 */
kasan_poison_vmalloc(p, 4096);
vfree(p);
```

**原因**：没有 `CONFIG_KASAN_VMALLOC` 时，vmalloc 区域的影子内存不会被自动管理。`kasan_populate_vmalloc()` 和 `kasan_release_vmalloc()` 在 `!CONFIG_KASAN_VMALLOC` 时是空函数（`include/linux/kasan.h:435-443`）。vmalloc 越界访问在 Generic 模式下会静默通过，因为影子内存未初始化（全零 = 可访问）。

**源码依据**：`include/linux/kasan.h:431-454` — `!CONFIG_KASAN_VMALLOC` 分支的所有函数为空操作。

### 陷阱 3：对 vmalloc 地址调用 dma_map_single

**错误写法**：
```c
void *buf = vmalloc(PAGE_SIZE);
dma_addr_t dma = dma_map_single(dev, buf, PAGE_SIZE, DMA_TO_DEVICE);
/* 返回 DMA_MAPPING_ERROR——映射失败 */
```

**正确写法**：
```c
void *buf = kmalloc(PAGE_SIZE, GFP_KERNEL);
dma_addr_t dma = dma_map_single(dev, buf, PAGE_SIZE, DMA_TO_DEVICE);
```

**原因**：`dma_map_single_attrs()` 显式检查 `is_vmalloc_addr(ptr)` 并拒绝映射（`include/linux/dma-mapping.h:332-334`）。vmalloc 内存不是物理连续的，DMA 设备无法通过单一总线地址访问。这个检查在 KASAN 环境下尤为重要——如果绕过此检查，KASAN 的影子内存检查会对 vmalloc 区域产生误报。

**源码依据**：`include/linux/dma-mapping.h:332-334` — `dev_WARN_ONCE(dev, is_vmalloc_addr(ptr), ...)`。

### 陷阱 4：kasan_disable_current 嵌套不匹配导致检测被永久关闭

**错误写法**：
```c
/* 在 SLUB 元数据访问中 */
kasan_disable_current();  /* kasan_depth++ */
/* ... 访问元数据 ... */
/* 忘记调用 kasan_enable_current() */
/* 此后当前任务的所有 KASAN 检测被静默禁用 */
```

**正确写法**：
```c
kasan_disable_current();   /* kasan_depth++ → 1 */
/* ... 访问元数据 ... */
kasan_enable_current();    /* kasan_depth-- → 0，恢复检测 */
```

**原因**：`kasan_disable_current()` 和 `kasan_enable_current()` 操作 `current->kasan_depth` 计数器（`mm/kasan/common.c:56-66`）。这是一个简单的递增/递减计数器——每次 `disable` 必须对应一次 `enable`。如果在持有禁用状态时发生睡眠、调度或异常退出，计数器不会自动归零，当前任务后续的所有内存访问都不会被 KASAN 检查。SLUB 中的使用模式（`mm/slub.c:652-662`）总是成对调用。

**源码依据**：`mm/kasan/common.c:56-66` — `kasan_enable_current` / `kasan_disable_current` 实现。

### 陷阱 5：HW_TAGS 异步模式下故障报告无地址信息

**错误写法**：
```c
/* 在 kasan.mode=async 下运行 */
/* 依赖 dmesg 中的精确故障地址来定位 bug */
/* 但异步模式只报告 "async fault detected"，无具体地址 */
```

**正确写法**：
```c
/* 调试阶段使用 kasan.mode=sync 获取精确地址 */
/* 或调用 kasan_report_async() 后检查 MTE 故障寄存器 */
/* 生产环境可用 async 但需配合 kasan.fault=panic 立即停机 */
```

**原因**：HW_TAGS 异步模式下，硬件在检测到标记不匹配时不立即触发异常，而是将故障信息写入 `TFSR_EL1` 寄存器。内核通过 `kasan_report_async()`（`include/linux/kasan.h:367`）报告，但该函数没有地址参数——只知道"某处发生了标记故障"，无法精确定位。同步模式（`kasan.mode=sync`）在每次访问时立即捕获，能给出精确地址和栈回溯。

**源码依据**：`include/linux/kasan.h:367` — `kasan_report_async()` 无地址参数。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/kasan.h` — 公开 API 定义、影子内存映射公式、Generic/标记模式条件编译分支、`kasan_has_integrated_init()` 仅 HW_TAGS 返回 true
- `mm/kasan/common.c` — slab/page 钩子实现、`kasan_enable_current`/`kasan_disable_current` 计数器机制、`assign_tag()` 的三种标记分配策略
- `mm/kasan/generic.c:469-497` — `kasan_record_aux_stack()` 仅对 slab 对象有效，标记模式下为 stub
- `mm/kasan/hw_tags.c:1-100` — HW_TAGS 初始化参数解析、`kasan_flag_enabled` static key、sync/async/asymm 模式选择
- `mm/kasan/shadow.c:1-50` — 影子内存检查函数、`__kasan_check_read`/`__kasan_check_write` 导出符号

**关键论断出处**：
- "标记模式不使用 per-object 元数据" — 来源：`include/linux/kasan.h:317-335`（`kasan_metadata_size` 返回 0，`kasan_record_aux_stack` 为空函数）
- "`kasan_has_integrated_init()` 仅 HW_TAGS 返回 true" — 来源：`include/linux/kasan.h:93-96`
- "`kasan_enable/disable_current` 操作 `kasan_depth` 计数器" — 来源：`mm/kasan/common.c:56-66`
- "vmalloc 影子内存在 `!CONFIG_KASAN_VMALLOC` 时不管理" — 来源：`include/linux/kasan.h:431-454`
- "DMA 拒绝 vmalloc 地址" — 来源：`include/linux/dma-mapping.h:332-334`

**未验证项**：
- `kasan_quarantine_put()` 的完整隔离区回收逻辑（仅在 `mm/kasan/quarantine.c` 中浏览，未逐行核对）
- arm64 MTE `stg`/`ldg` 指令的硬件行为细节（仅参考 `arch/arm64/include/asm/mte-kasan.h` 的 inline 函数签名）
