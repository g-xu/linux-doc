---
title: 总览
---

# 杂项工具宏与辅助 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档链接 |
|------|-----------|---------|---------|
| **ERR_PTR/IS_ERR** | 将错误码编码为指针值 | 函数需同时返回指针和错误码时的标准方案 | [err-ptr.md](err-ptr.md) |
| **cleanup.h** | 基于作用域的资源自动释放 | 消除 goto 清理链，防止资源泄漏，6.1 新特性 | [cleanup.md](cleanup.md) |
| **container_of** | 从成员指针反推容器结构体 | 侵入式设计的基础，设备模型/链表/kobject 都依赖 | [misc-container.md](misc-container.md) |
| **likely/unlikely** | 分支预测提示 | 告诉编译器哪个分支更可能执行，优化指令缓存 | [misc-compiler.md](misc-compiler.md) |
| **BUILD_BUG_ON** | 编译期断言 | 条件为真时编译失败，在编译期捕获错误 | [misc-build.md](misc-build.md) |
| **ARRAY_SIZE** | 数组元素计数 | 对指针无效（`__must_be_array` 保护），防止误用 | [misc-build.md](misc-build.md) |
| **stringify** | 宏展开为字符串 | 两级间接展开，构造模块参数/寄存器名称字符串 | [misc-build.md](misc-build.md) |
| **min/max/clamp** | 类型安全的比较 | 拒绝混合有符号/无符号，编译期类型检查 | [misc-minmax.md](misc-minmax.md) |
| **static_key/jump_label** | 零开销条件分支 | 特性开关/tracepoint，arm64 用 NOP/JMP 替换 | [misc-static.md](misc-static.md) |
| **overflow** | 溢出检查算术 | 安全分配，`check_*` 返回 bool，`size_*` 饱和返回 | [misc-overflow.md](misc-overflow.md) |
| **scatterlist** | DMA 散射-聚集列表 | 描述不连续内存，DMA 引擎标准接口 | [misc-io.md](misc-io.md) |
| **iov_iter** | 多态 I/O 迭代器 | VFS 层统一描述数据来源/去向，支持多种底层类型 | [misc-io.md](misc-io.md) |
| **uuid/guid** | 128 位全局唯一标识符 | uuid_t 大端 vs guid_t 混合序，EFI/ACPI/设备树 | [misc-uuid.md](misc-uuid.md) |
| **crc32/crc16** | 循环冗余校验 | 文件系统元数据、网络 FCS、压缩校验 | [misc-crc.md](misc-crc.md) |
| **jiffies** | 内核节拍计数器 | 定时器超时、轮询等待，`time_after` 处理回绕 | [misc-time.md](misc-time.md) |
| **ktime** | 纳秒精度时间 | 高精度定时器和性能测量，不受 HZ 量化误差影响 | [misc-time.md](misc-time.md) |
| **random** | 内核随机数生成 | CRNG 初始化前不安全，`wait_for_random_bytes()` 等待就绪 | [misc-random.md](misc-random.md) |

---

## §2 选型决策

```
需要做什么？
│
├─ 返回指针或错误码 ──→ ERR_PTR / IS_ERR
│
├─ 管理资源生命周期
│   ├─ 简单释放（kfree/kvfree/put）──→ cleanup.h（__free）
│   ├─ 锁获取/释放 ──→ cleanup.h（guard / scoped_guard）
│   └─ 构造+析构 ──→ cleanup.h（CLASS）
│
├─ 从嵌入成员反推容器 ──→ container_of
│
├─ 编译期检查
│   ├─ 条件断言 ──→ BUILD_BUG_ON
│   ├─ 数组大小 ──→ ARRAY_SIZE
│   └─ 宏转字符串 ──→ __stringify
│
├─ 运行时性能优化
│   ├─ 分支提示（偶发）──→ likely / unlikely
│   └─ 特性开关（频繁检查）──→ static_key / jump_label
│
├─ 数值计算
│   ├─ 取大/小值 ──→ min / max / min_t / max_t
│   ├─ 范围限制 ──→ clamp / clamp_t
│   └─ 防溢出分配 ──→ struct_size / array_size / check_*_overflow
│
├─ 描述 I/O 数据
│   ├─ DMA 散射列表 ──→ scatterlist / sg_table
│   └─ VFS 多态迭代器 ──→ iov_iter
│
├─ 唯一标识符 ──→ uuid_t（大端）/ guid_t（EFI 混合序）
│
├─ 数据校验
│   ├─ 标准 CRC32 ──→ crc32()
│   ├─ iSCSI/btrfs CRC32C ──→ __crc32c_le()
│   └─ 16 位校验 ──→ crc16()
│
├─ 时间表示
│   ├─ ms 级超时/定时器 ──→ jiffies + time_after
│   └─ us/ns 级精度测量 ──→ ktime_get() + ktime_us_delta()
│
└─ 随机数
    ├─ 基础随机字节 ──→ get_random_bytes()
    ├─ 随机整数 ──→ get_random_u32() / get_random_u64()
    ├─ 范围内随机 ──→ get_random_u32_below()
    └─ 早期启动（CRNG 未就绪）──→ wait_for_random_bytes() + get_random_bytes_wait()
```

---

## §3 核心对比表

### 3.1 错误处理与资源管理

| 模块 | 功能 | 机制 | 典型用途 |
|------|------|------|---------|
| ERR_PTR/IS_ERR | 错误码编码为指针 | 地址空间顶部 4096 字节永不分配 | 函数返回指针或错误码 |
| cleanup.h | 作用域资源释放 | `__attribute__((__cleanup__))` | 消除 goto 清理链 |

### 3.2 编译期工具

| 模块 | 功能 | 实现方式 | 典型用途 |
|------|------|---------|---------|
| BUILD_BUG_ON | 编译期断言 | 零宽位域/负数组大小 | 结构体大小检查 |
| ARRAY_SIZE | 数组元素计数 | `sizeof(arr)/sizeof(arr[0])` + `__must_be_array` | 遍历静态数组 |
| stringify | 宏转字符串 | 两级间接展开 `__stringify(x)` | 构造模块参数 |
| container_of | 成员→容器反推 | 指针算术 + `static_assert` 类型检查 | 侵入式设计基础 |

### 3.3 运行时优化

| 模块 | 功能 | 开销 | 典型用途 |
|------|------|------|---------|
| likely/unlikely | 分支预测提示 | 零（编译器内建） | 错误路径标记 |
| static_key | 零开销条件分支 | 修改代码段（stop_machine） | 特性开关、tracepoint |
| min/max/clamp | 类型安全比较 | 零（内联） | 数值范围限制 |

### 3.4 安全计算

| 模块 | 功能 | 返回值 | 典型用途 |
|------|------|--------|---------|
| overflow（check_*） | 溢出检查 | bool（true=溢出） | 安全分配 |
| overflow（size_*） | 饱和算术 | SIZE_MAX（溢出时） | 缓冲区大小计算 |
| struct_size | 结构体+尾部数组大小 | 总大小或 SIZE_MAX | 变长结构体分配 |

### 3.5 I/O 描述

| 模块 | 功能 | 底层类型 | 典型用途 |
|------|------|---------|---------|
| scatterlist | DMA 散射列表 | 物理页 + 偏移 + 长度 | DMA 引擎 |
| iov_iter | VFS 多态迭代器 | iovec/kvec/bio_vec/pipe/xarray | 统一 I/O 接口 |

### 3.6 时间与随机数

| 模块 | 精度 | 回绕安全 | 典型用途 |
|------|------|---------|---------|
| jiffies | 1/HZ（通常 1-10 ms） | `time_after` 处理 32/64 位回绕 | 定时器超时 |
| ktime | 纳秒 | 无回绕（s64） | 高精度测量 |
| random | N/A | CRNG 初始化前不安全 | 安全协议、ASLR |

---

## §4 使用规模

以下数据来自对内核源码树的实际 grep 统计：

| 模块 | grep 命令 | 匹配数 |
|------|----------|--------|
| ERR_PTR/IS_ERR | `grep -rw 'ERR_PTR\|IS_ERR\|PTR_ERR\|ERR_CAST\|IS_ERR_OR_NULL' --include='*.c' --include='*.h' . \| wc -l` | 66436 |
| cleanup.h | `grep -rw '__free\|DEFINE_FREE\|DEFINE_CLASS\|DEFINE_GUARD\|CLASS(\|__cleanup__\|scoped_guard\|guard(' --include='*.c' --include='*.h' . \| wc -l` | 90 |
| container_of | `grep -rw 'container_of' --include='*.c' --include='*.h' . \| wc -l` | 19420 |
| likely/unlikely | `grep -rw 'likely\|unlikely' --include='*.c' --include='*.h' . \| wc -l` | 26086 |
| BUILD_BUG_ON | `grep -rw 'BUILD_BUG_ON\|BUILD_BUG_ON_ZERO\|BUILD_BUG_ON_MSG\|BUILD_BUG' --include='*.c' --include='*.h' . \| wc -l` | 3777 |
| ARRAY_SIZE | `grep -rw 'ARRAY_SIZE' --include='*.c' --include='*.h' . \| wc -l` | 41985 |
| stringify | `grep -rw '__stringify\|__STRINGIFY\|stringify' --include='*.c' --include='*.h' . \| wc -l` | 1319 |
| min/max/clamp | `grep -rw 'min\|max\|clamp\|min_t\|max_t\|clamp_t\|min3\|max3' --include='*.c' --include='*.h' . \| wc -l` | 40896 |
| static_key/jump_label | `grep -rw 'static_key\|static_branch\|DEFINE_STATIC_KEY\|jump_label\|STATIC_KEY' --include='*.c' --include='*.h' . \| wc -l` | 353 |
| overflow | `grep -rw 'check_mul_overflow\|check_add_overflow\|size_mul\|size_add\|struct_size\|array_size\|array3_size' --include='*.c' --include='*.h' . \| wc -l` | 2064 |
| scatterlist | `grep -rw 'scatterlist\|sg_init\|sg_set\|sg_next\|sg_table\|for_each_sg\|sg_alloc' --include='*.c' --include='*.h' . \| wc -l` | 5859 |
| iov_iter | `grep -rw 'iov_iter\|IOV_ITER\|iov_iter_init\|iov_iter_copy\|copy_to_iter\|copy_from_iter\|iov_iter_advance' --include='*.c' --include='*.h' . \| wc -l` | 1000 |
| uuid/guid | `grep -rw 'uuid_t\|guid_t\|uuid_gen\|guid_gen\|uuid_is_null\|guid_is_null\|UUID\|GUID' --include='*.c' --include='*.h' . \| wc -l` | 1429 |
| crc32/crc16 | `grep -rw 'crc32\|crc32c\|crc16\|__crc32c_le\|__crc32c_le_base' --include='*.c' --include='*.h' . \| wc -l` | 1124 |
| jiffies | `grep -rw 'jiffies\|time_after\|time_before\|time_after_eq\|jiffies_to_msecs\|msecs_to_jiffies' --include='*.c' --include='*.h' . \| wc -l` | 14884 |
| ktime | `grep -rw 'ktime_get\|ktime_t\|ktime_sub\|ktime_add\|ktime_to_ns\|ktime_to_us\|ktime_to_ms\|ns_to_ktime\|ktime_compare\|ktime_before\|ktime_after' --include='*.c' --include='*.h' . \| wc -l` | 3845 |
| random | `grep -rw 'get_random_bytes\|get_random_u32\|get_random_u64\|get_random_long\|wait_for_random_bytes\|get_random_int\|random' --include='*.c' --include='*.h' . \| wc -l` | 2611 |

---

## §5 设计模式速览

### 5.1 头文件优先

绝大多数模块（ERR_PTR、container_of、likely/unlikely、BUILD_BUG_ON、ARRAY_SIZE、stringify、min/max/clamp、overflow、ktime、jiffies）完全在头文件中实现，无独立 `.c` 文件。这意味着它们的使用没有函数调用开销，编译器可以内联和优化。

### 5.2 编译器内建函数依赖

多个模块依赖 GCC/Clang 内建函数：
- `likely`/`unlikely` → `__builtin_expect`
- `overflow` → `__builtin_*_overflow`
- `cleanup.h` → `__attribute__((__cleanup__))`
- `BUILD_BUG_ON` → 零宽位域 / 负数组大小

### 5.3 类型安全设计

内核在这些工具中贯彻类型安全：
- `container_of` 用 `static_assert` 检查指针类型匹配
- `min`/`max` 拒绝混合有符号/无符号比较
- `ARRAY_SIZE` 用 `__must_be_array` 拒绝指针参数
- `overflow` 的 `check_*` 系列通过 `__must_check` 强制检查返回值

### 5.4 回绕/溢出安全

时间（jiffies）和数值（overflow）模块都提供回绕安全的操作：
- jiffies：`time_after(a, b)` 用有符号差值比较，正确处理 32/64 位回绕
- overflow：`size_*` 饱和返回 SIZE_MAX，`check_*` 返回 bool 错误

### 5.5 无全局状态 / 单例状态

- 无状态：ERR_PTR、container_of、likely、BUILD_BUG_ON、min/max、overflow、CRC、UUID
- 全局单例：jiffies（`jiffies_64` 原子计数器）、random（CRNG 全局状态）
- per-CPU：static_key 的 key 是全局数据

---

## §6 锁模型对比

| 模块 | 锁模型 | 说明 |
|------|--------|------|
| ERR_PTR/IS_ERR | 无锁 | 纯指针算术，无状态 |
| cleanup.h | 无锁 | 编译器属性，作用域结束自动调用 |
| container_of | 无锁 | 纯指针算术 |
| likely/unlikely | 无锁 | 编译器提示 |
| BUILD_BUG_ON | 无锁 | 编译期检查 |
| ARRAY_SIZE | 无锁 | 编译期计算 |
| min/max/clamp | 无锁 | 内联比较 |
| static_key | 代码修改锁 | `stop_machine()` 修改代码段 |
| overflow | 无锁 | 纯算术 |
| scatterlist | 需外部锁 | 链表操作需锁保护 |
| iov_iter | 需外部锁 | 迭代器操作需锁保护 |
| uuid/guid | 无锁 | 纯数据操作 |
| crc32/crc16 | 无锁 | 纯计算函数 |
| jiffies | 原子读 | `jiffies_64` 原子计数器 |
| ktime | 无锁 | 读硬件时钟 |
| random | 内部锁 | CRNG 全局状态有锁保护 |

---

## §7 组合使用模式

### 7.1 ERR_PTR + 设备模型

设备模型中大量使用 ERR_PTR 返回错误。`device_register()` 失败时返回 `ERR_PTR(-ENOMEM)`，调用者用 `IS_ERR()` 检查。

### 7.2 container_of + list_head（侵入式链表）

list_head 嵌入用户结构体，遍历时用 `container_of` 反查外层结构。这是内核最经典的侵入式设计模式。

### 7.3 BUILD_BUG_ON + 结构体检查

驱动初始化时用 `BUILD_BUG_ON(sizeof(struct xxx) != expected_size)` 检查结构体布局，在编译期捕获错误。

### 7.4 ARRAY_SIZE + 遍历静态数组

驱动中遍历静态数组时用 `for (i = 0; i < ARRAY_SIZE(table); i++)` 保证不越界。`__must_be_array` 防止传入指针。

### 7.5 likely/unlikely + 错误路径

错误路径用 `unlikely()` 标记，正常路径用 `likely()` 标记。例如 `if (unlikely(err)) return err;`。

### 7.6 min/max + 缓冲区计算

计算缓冲区大小时用 `min(needed, available)` 防止越界。`min_t(size_t, a, b)` 强制类型避免编译错误。

### 7.7 overflow + 安全分配

分配变长结构体时用 `struct_size(ptr, member, count)` 计算总大小，溢出时返回 SIZE_MAX 防止分配失败。

### 7.8 scatterlist + DMA 引擎

DMA 引擎用 scatterlist 描述不连续内存。`sg_init_table()` 初始化，`sg_set_page()` 设置每段，`for_each_sg()` 遍历。

### 7.9 jiffies + time_after（超时等待）

轮询等待时用 `time_after(jiffies, timeout)` 判断是否超时。`time_after` 正确处理 32 位回绕。

### 7.10 ktime + 性能测量

性能测量用 `ktime_get()` 获取时间点，`ktime_us_delta()` 计算差值。纳秒精度不受 HZ 影响。

---

## §8 阅读顺序建议

### 8.1 入门路径（理解内核基础工具）

1. **ERR_PTR/IS_ERR** → 理解内核错误返回机制
2. **container_of** → 理解侵入式设计基础
3. **BUILD_BUG_ON / ARRAY_SIZE** → 理解编译期工具
4. **likely/unlikely** → 理解分支优化

### 8.2 需要资源管理 → cleanup.h

cleanup.h 是 6.1 新特性，用 `__free` 属性自动释放资源。先理解 `DEFINE_FREE`，再看 `DEFINE_GUARD` 和 `scoped_guard`。

### 8.3 需要数值计算 → min/max → overflow

先学 `min`/`max`/`clamp` 系列，再看 `overflow` 的安全分配。`struct_size` 在分配变长结构体时很实用。

### 8.4 需要时间处理 → jiffies → ktime

先学 `jiffies`（ms 级超时），再看 `ktime`（ns 级精度）。`time_after` 处理回绕是关键。

### 8.5 需要 I/O 描述 → scatterlist → iov_iter

先学 `scatterlist`（DMA 散射列表），再看 `iov_iter`（VFS 多态迭代器）。两者服务于不同层次。

### 8.6 需要唯一标识 → uuid/guid

`uuid_t` 大端，`guid_t` EFI 混合序。EFI 分区用 GUID，设备树用 UUID。不可混用。

---

## §9 容易混淆的关系

| 容易混淆的对 | 区别要点 |
|-------------|----------|
| ERR_PTR vs NULL 返回 | ERR_PTR 编码错误码；NULL 只表示失败无错误信息 |
| cleanup.h vs goto 清理 | cleanup.h 自动释放；goto 手动释放（易遗漏） |
| likely/unlikely vs static_key | likely/unlikely 是编译器提示；static_key 是运行时修改代码 |
| min vs min_t | min 要求类型相同；min_t 强制转换类型 |
| jiffies vs ktime | jiffies 精度 1/HZ；ktime 纳秒精度 |
| uuid_t vs guid_t | uuid_t 大端；guid_t EFI 混合序（前 3 字段小端） |
| crc32 vs crc32c | 不同多项式，不可互换；iSCSI/btrfs 用 crc32c |
| scatterlist vs iov_iter | scatterlist 描述物理页（DMA）；iov_iter 描述用户/内核缓冲（VFS） |

---

## §10 文档索引

| 序号 | 文档 | 覆盖模块 |
|------|------|----------|
| 1 | [err-ptr.md](err-ptr.md) | ERR_PTR / IS_ERR / PTR_ERR / ERR_CAST |
| 2 | [cleanup.md](cleanup.md) | cleanup.h 三层 API（DEFINE_FREE / DEFINE_CLASS / DEFINE_GUARD） |
| 3 | [misc-container.md](misc-container.md) | container_of / container_of_safe |
| 4 | [misc-compiler.md](misc-compiler.md) | likely / unlikely |
| 5 | [misc-build.md](misc-build.md) | BUILD_BUG_ON / ARRAY_SIZE / stringify |
| 6 | [misc-minmax.md](misc-minmax.md) | min / max / clamp 全系列 |
| 7 | [misc-static.md](misc-static.md) | static_key / jump_label |
| 8 | [misc-overflow.md](misc-overflow.md) | check_*_overflow / size_* / struct_size |
| 9 | [misc-io.md](misc-io.md) | scatterlist / sg_table / iov_iter |
| 10 | [misc-uuid.md](misc-uuid.md) | uuid_t / guid_t 全套 API |
| 11 | [misc-crc.md](misc-crc.md) | crc32 / crc32c / crc16 |
| 12 | [misc-time.md](misc-time.md) | jiffies / ktime_t |
| 13 | [misc-random.md](misc-random.md) | get_random_* / CRNG / 熵源收集 |
