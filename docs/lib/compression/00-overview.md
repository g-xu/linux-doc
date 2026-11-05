---
title: 总览
---

# 压缩与加密 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档链接 |
|------|-----------|---------|---------|
| **Crypto API** | 加密算法统一框架 | 算法注册/查找/优先级自动选择，支持从软件到 arm64 硬件加速的全范围 | [crypto.md](crypto.md) |
| **zlib** | 通用压缩/解压 | 兼容性最广，squashfs/btrfs/jffs2/PPP 都支持 | [misc-compress.md](misc-compress.md) |
| **LZO** | 快速轻量压缩 | 最小内存占用（16 KB workspace，解压零 workspace） | [misc-compress.md](misc-compress.md) |
| **zstd** | 高压缩比可调压缩 | 级别 1-22 可调，压缩比最高 | [misc-compress.md](misc-compress.md) |
| **LZ4** | 极速压缩 | 速度最快，HC 模式压缩比接近 zstd | [misc-compress.md](misc-compress.md) |

---

## §2 选型决策

```
需要什么能力？
│
├─ 加密/解密/认证/哈希
│   ├─ 对称加密（AES/ChaCha20）──→ crypto_alloc_skcipher()
│   ├─ 认证加密（AES-GCM/ChaCha20-Poly1305）──→ crypto_alloc_aead()
│   ├─ 哈希（SHA-256/SHA-3/SM3）──→ crypto_alloc_shash() 或 crypto_alloc_ahash()
│   ├─ 非对称（RSA/ECDSA）──→ crypto_alloc_akcipher()
│   ├─ 密钥协商（ECDH/DH）──→ crypto_alloc_kpp()
│   └─ 随机数 ──→ crypto_alloc_rng()
│
└─ 数据压缩
    │
    ├─ 需要最高压缩比 ──→ zstd（级别 1-22 可调）
    │   └─ 消费者：btrfs、f2fs、squashfs、ubifs
    │
    ├─ 需要最快速度 ──→ LZ4
    │   ├─ 标准模式：速度最快，压缩比低
    │   └─ HC 模式：压缩比接近 zstd，速度仍快
    │   └─ 消费者：f2fs、erofs、squashfs
    │
    ├─ 需要平衡（兼容性最广）──→ zlib
    │   └─ 消费者：squashfs、btrfs、jffs2、PPP
    │
    ├─ 需要最小内存占用 ──→ LZO
    │   └─ 16 KB workspace，解压零 workspace
    │   └─ 消费者：squashfs、btrfs、f2fs
    │
    └─ 通过 crypto API 访问 ──→ crypto_alloc_comp()
        └─ 消费者：zram、pstore
        └─ 可用算法名："deflate"(zlib) / "lzo" / "lzo-rle" / "zstd" / "lz4" / "lz4hc"
```

---

## §3 核心对比表

### 3.1 压缩库快速对比

| 属性 | zlib | LZO | zstd | LZ4 |
|------|------|-----|------|-----|
| 库代码量 | 4258 行 | 835 行 | 18986 行 | 2675 行 |
| 压缩 workspace | ~256 KB | 16 KB | 可变（级别相关） | 16 KB（HC: 256 KB） |
| 解压 workspace | ~32 KB | 0 | 可变 | 0 |
| API 复杂度 | 流式多调用 | 单次调用 | 三层 API | 单次 + 流式 + HC |
| 压缩比 | 中 | 低 | 高 | 低（HC: 中） |
| 速度 | 中 | 快 | 可调 | 最快 |
| 支持文件系统 | squashfs/btrfs/jffs2 | squashfs/btrfs/f2fs | squashfs/btrfs/f2fs/ubifs | squashfs/f2fs/erofs |

### 3.2 Crypto API 架构

| 层次 | 功能 | 典型 API |
|------|------|---------|
| 算法层 | 注册具体实现（C/NEON/CE） | `crypto_register_alg()` |
| 前端层 | 按类型提供统一接口 | `crypto_alloc_skcipher()` 等 |
| 模板层 | 组合基础算法和模式 | `cbc(aes)` = `crypto_template` |

### 3.3 Crypto API 算法类型

| 类型 | 分配函数 | 用途 |
|------|---------|------|
| skcipher | `crypto_alloc_skcipher()` | 对称加密（AES/ChaCha20） |
| aead | `crypto_alloc_aead()` | 认证加密（AES-GCM） |
| shash | `crypto_alloc_shash()` | 哈希（SHA-256） |
| ahash | `crypto_alloc_ahash()` | 异步哈希（硬件加速） |
| akcipher | `crypto_alloc_akcipher()` | 非对称加密（RSA） |
| kpp | `crypto_alloc_kpp()` | 密钥协商（ECDH） |
| rng | `crypto_alloc_rng()` | 随机数生成 |
| comp | `crypto_alloc_comp()` | 压缩（zlib/lzo/zstd/lz4） |

---

## §4 使用规模

以下数据来自对内核源码树的实际 grep 统计：

| 模块 | grep 命令 | 匹配数 |
|------|----------|--------|
| zlib | `grep -rw 'zlib\|zlib_deflate\|zlib_inflate\|ZLIB\|deflateInit\|deflate\|inflate\|z_stream' --include='*.c' --include='*.h' . \| wc -l` | 496 |
| LZO | `grep -rw 'lzo\|LZO\|lzo1x_compress\|lzo1x_decompress\|lzo1x_1_compress' --include='*.c' --include='*.h' . \| wc -l` | 131 |
| zstd | `grep -rw 'zstd\|ZSTD\|zstd_compress\|zstd_decompress\|ZSTD_CCtx\|ZSTD_DCtx' --include='*.c' --include='*.h' . \| wc -l` | 465 |
| LZ4 | `grep -rw 'lz4\|LZ4\|lz4_compress\|lz4_decompress\|lz4hc\|LZ4_compress_default\|LZ4_decompress_safe' --include='*.c' --include='*.h' . \| wc -l` | 127 |
| Crypto API | `grep -rw 'crypto_alloc_\|crypto_register\|crypto_skcipher\|crypto_aead\|crypto_shash\|crypto_ahash\|crypto_akcipher\|crypto_comp' --include='*.c' --include='*.h' . \| wc -l` | 2608 |

---

## §5 设计模式速览

### 5.1 预分配 workspace 模式

所有压缩库遵循同一设计：**调用者预分配 workspace，库内部不做动态分配**。这是内核内存管理的要求——压缩/解压可能在中断上下文或内存压力下执行，不允许失败。

```c
/* 通用模式：*/
size_t ws_size = xxx_workspacesize(...);
void *ws = vzalloc(ws_size);         /* 或 kvmalloc */
xxx_init(ctx, ws);
xxx_compress/decompress(...);
vfree(ws);
```

### 5.2 两种访问路径

压缩库有两种使用路径，没有统一抽象层：

| 路径 | 说明 | 典型消费者 |
|------|------|-----------|
| 直接调用库函数 | `#include <linux/zlib.h>` 等 | squashfs、btrfs、f2fs、boot decompress |
| 通过 crypto API | `crypto_comp_compress()` | zram、pstore |

每个文件系统有自己的 ops 结构（`squashfs_decompressor`、`btrfs_compress_op`、`f2fs_compress_ops`）。

### 5.3 Crypto API 优先级机制

Crypto API 通过优先级自动选择最优实现：
- 通用 C 实现：优先级 100
- arm64 NEON 加速：优先级 200
- arm64 CE 指令加速：优先级 200-300

`crypto_alloc_*()` 自动选择最高优先级的注册实现，调用者无需关心。

### 5.4 算法注册/查找模型

Crypto API 的核心模型适用于所有算法类型：

```
注册: crypto_register_alg() → 加入 crypto_alg_list 全局链表
查找: crypto_alloc_skcipher("aes") → 按名称+类型+优先级匹配
实例化: 返回 crypto_skcipher * → 设置密钥 → 加密/解密
```

模板（如 `cbc(aes)`）通过 `crypto_template` 组合基础算法和模式。

### 5.5 arm64 硬件加速

`arch/arm64/crypto/` 提供 43 个文件（22 .c + 21 .S），加速以下算法：

| 算法类别 | 加速方式 | 优先级范围 |
|----------|----------|-----------|
| AES | ARM CE 指令 | 200-300 |
| SHA-1/224/256/384/512 | CE / NEON | 200 |
| SHA-3 | CE | 200 |
| SM3 | CE / NEON | 200 |
| SM4 | CE / NEON | -- |
| ChaCha20 | NEON | 300 |
| GHASH | CE | 150-300 |
| Poly1305 | NEON | 200 |
| CRC-T10DIF | CE | 100-200 |

---

## §6 锁模型对比

本分类不涉及锁模型。压缩库和 Crypto API 的并发保护由调用者负责——库函数本身是无状态的（workspace 由调用者分配和管理）。

---

## §7 组合使用模式

### 7.1 squashfs + 压缩库（文件系统压缩）

squashfs 通过 `squashfs_decompressor` ops 结构支持 zlib/LZO/zstd/LZ4 四种压缩。挂载时根据 superblock 中的压缩类型选择对应的解压库。

### 7.2 btrfs + 压缩库（透明压缩）

btrfs 支持 zlib/LZO/zstd 三种压缩，通过 `btrfs_compress_op` ops 结构统一接口。压缩级别可在挂载时指定。

### 7.3 zram + crypto API（内存压缩）

zram 通过 `crypto_alloc_comp()` 访问压缩算法，支持 deflate(zlib)/lzo/lzo-rle/zstd/lz4/lz4hc。crypto API 的优先级机制自动选择最优实现（如 arm64 NEON 加速版）。

### 7.4 pstore + 压缩库（持久化存储）

pstore（持久化存储）在崩溃时保存内核日志，使用压缩减少存储空间。支持 zlib/LZO/zstd/LZ4，通过 crypto API 或直接调用库函数。

### 7.5 boot decompress（启动解包）

内核启动时需要解压自身（compressed kernel），使用 zlib/LZO/LZ4/zstd。启动解包代码在 `arch/arm64/boot/compressed/`，要求极简环境（无动态分配）。

### 7.6 Crypto API + 网络（IPsec/WiFi）

网络子系统通过 Crypto API 使用加密算法。IPsec（`net/xfrm/`）用 `crypto_alloc_skcipher()`/`crypto_alloc_aead()` 实现 ESP/AH。WiFi（`net/mac80211/`）用 Crypto API 实现 WPA/CCMP 加密。

### 7.7 Crypto API + dm-crypt（磁盘加密）

dm-crypt 通过 Crypto API 使用 AES-XTS 等算法实现全盘加密。`crypto_alloc_skcipher("xts(aes)")` 分配算法实例，优先级机制自动选择 arm64 CE 加速版。

---

## §8 阅读顺序建议

### 8.1 入门路径（理解压缩库）

1. **LZO** → 最简单的 API（无 init/end），理解 workspace 模式
2. **zlib** → 流式 API，理解多调用模式
3. **LZ4** → 理解标准/HC 两种模式
4. **zstd** → 最复杂，理解三层 API

### 8.2 需要文件系统压缩 → zlib → zstd → LZ4

先学 zlib（兼容性最广），再看 zstd（最高压缩比），最后看 LZ4（最快速度）。LZO 在内存受限时使用。

### 8.3 需要加密 → Crypto API

Crypto API 是内核加密的唯一入口。先理解算法注册/查找模型，再学各类型前端（skcipher/aead/shash 等）。arm64 硬件加速通过优先级机制自动选择。

### 8.4 需要通过 crypto API 访问压缩 → zram 示例

zram 是通过 crypto API 使用压缩的典型例子。先理解 `crypto_alloc_comp()`，再看 zram 如何使用。

---

## §9 容易混淆的关系

| 容易混淆的对 | 区别要点 |
|-------------|----------|
| zlib vs deflate | zlib 是库，deflate 是算法；crypto API 中 "deflate" 指 zlib |
| LZO vs LZO-RLE | LZO-RLE 是 LZO 的变体，对重复数据更好；crypto API 中是两个不同算法名 |
| LZ4 vs LZ4HC | LZ4 标准模式速度最快；LZ4HC 压缩比更高，workspace 256 KB |
| zstd vs zlib | zstd 压缩比更高且可调；zlib 兼容性更广 |
| crypto_alloc_comp vs 直接调用 | crypto API 有优先级自动选择；直接调用无抽象层 |
| shash vs ahash | shash 同步（软件实现）；ahash 异步（可硬件加速） |

---

## §10 文档索引

| 序号 | 文档 | 覆盖模块 |
|------|------|----------|
| 1 | [crypto.md](crypto.md) | Crypto API 完整框架：算法注册、各类型前端、arm64 加速 |
| 2 | [misc-compress.md](misc-compress.md) | zlib / LZO / zstd / LZ4 四个压缩库 |
