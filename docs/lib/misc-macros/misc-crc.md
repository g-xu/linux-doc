# CRC32 / CRC16

> B 级文档 · CRC32：`include/linux/crc32.h`（79 行）+ `lib/crc32.c`（344 行）· CRC16：`include/linux/crc16.h`（28 行）+ `lib/crc16.c`（79 行）

## 1 CRC32

### 定位

**功能**：计算 CRC32 循环冗余校验值，用于检测数据传输和存储中的意外变更。支持四种多项式变体：标准 IEEE 802.3（小端/大端）和 Castagnoli（CRC32C），以及以太网专用的取反变体。

**实现方式**：`lib/crc32.c` 在编译期根据 `CONFIG_CRC32_BIT` 等选项选择实现策略——按位计算、按字节查表、Sarwate 查表、切片-by-4/8 等。`crc32_le`/`crc32_be` 标记为 `__pure` 纯函数，相同输入编译器可去重。arm64 平台可通过 CRC 指令扩展（`arch/arm64/crypto/crct10dif-ce-core.S`）硬件加速，通过 crypto API 注册。`crc32_le_combine` 支持合并两段独立计算的 CRC 值，无需原始数据。

**应用场景**：文件系统元数据校验（ext4、btrfs）、网络协议（以太网 FCS、iSCSI 用 CRC32C）、压缩算法（zlib/gzip 校验）、网络设备驱动（网卡校验和卸载的后备路径）等。

### 核心 API

头文件：`include/linux/crc32.h`

| API | 说明 | 备注 |
|-----|------|------|
| `crc32(seed, data, length)` | 计算 CRC32（小端序） | 宏，展开为 `crc32_le` |
| `crc32_le(seed, data, length)` | CRC32 小端序计算 | `__pure`，可被编译器优化 |
| `crc32_be(seed, data, length)` | CRC32 大端序计算 | 同上 |
| `__crc32c_le(seed, data, length)` | CRC32C（Castagnoli 多项式） | iSCSI / btrfs 使用 |
| `crc32_le_combine(crc1, crc2, len2)` | 合并两段 CRC32 | 不需要原始数据，只需两段 CRC 值和第二段长度 |
| `__crc32c_le_combine(crc1, crc2, len2)` | 合并两段 CRC32C | 同上 |
| `ether_crc(length, data)` | 以太网 CRC32（大端序，取反） | `bitrev32(crc32_le(~0, data, length))` |
| `ether_crc_le(length, data)` | 以太网 CRC32（小端序，取反） | `crc32_le(~0, data, length)` |

### 使用模式

```c
/* 基本用法：*/
u32 crc = crc32(~0, buffer, length);        /* seed 通常传 ~0 (0xFFFFFFFF) */

/* 增量计算（数据分多次喂入）：*/
u32 crc = crc32(~0, chunk1, len1);
crc = crc32(crc, chunk2, len2);             /* 后续 seed 传上次的返回值 */

/* 合并（并行计算后合并）：*/
u32 full = crc32_le_combine(crc_part1, crc_part2, len_part2);
```

### 实现要点

- **四种多项式变体**：`lib/crc32.c` 在编译期根据 `CONFIG_CRC32_BIT` 等选项选择实现（按位 / 按字节 / Sarwate 查表 / 切片-by-4/8）
- **arm64 加速**：`arch/arm64/crypto/crct10dif-ce-core.S` 等提供硬件加速（CRC 指令扩展），通过 crypto API 注册
- **`__pure` 标记**：`crc32_le` / `crc32_be` 标记为纯函数，相同输入编译器可去重

## 2 CRC16

### 定位

**功能**：计算 CRC-16 循环冗余校验值（多项式 0x8005），用于较老的低速协议数据完整性检查。相比 CRC32 开销更低，适用于带宽/计算资源受限的嵌入式场景。

**实现方式**：`lib/crc16.c` 使用 256 项查找表（`crc16_table[256]`），编译期生成。`crc16_byte` 是 inline 单字节版本，直接查表。实现简单直接，无多种策略选择——CRC16 数据量小，不需要 CRC32 那样的切片优化。

**应用场景**：1-Wire 总线通信校验、某些嵌入式串行协议、老旧硬件设备的固件接口。相比 CRC32 使用范围窄得多，主要在特定的嵌入式驱动子系统中出现。

### 核心 API

头文件：`include/linux/crc16.h`

| API | 说明 | 备注 |
|-----|------|------|
| `crc16(crc, buffer, len)` | 计算 CRC-16（多项式 0x8005） | 实现 `lib/crc16.c` |
| `crc16_byte(crc, data)` | 单字节 CRC16 | inline，查表 `crc16_table[256]` |

### 使用模式

```c
u16 crc = crc16(0, buffer, length);       /* seed 通常传 0 */
```

### CRC16 参数

- 宽度：16 位
- 多项式：0x8005（x^16 + x^15 + x^2 + 1）
- 初始值：0
- 查表法：`crc16_table[256]` 在编译期生成

## 3 注意事项

- **seed 值**：CRC32 惯用 `~0`（0xFFFFFFFF），CRC16 惯用 `0`——传错 seed 会得到完全不同的校验值
- **大小端**：`crc32_le` 和 `crc32_be` 对同一数据产生不同的校验值，必须与对端约定一致
- **CRC32 vs CRC32C**：两者多项式不同（IEEE 802.3 vs Castagnoli），不可互换。iSCSI 和 btrfs 用 CRC32C（`__crc32c_le`）
