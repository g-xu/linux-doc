# 内核压缩库

> B 级文档 · zlib：`lib/zlib_deflate/` + `lib/zlib_inflate/`（4,258 行）· lzo：`lib/lzo/`（835 行）· zstd：`lib/zstd/`（18,986 行）· lz4：`lib/lz4/`（2,675 行）

## 1 定位

内核内置四个压缩库用于文件系统压缩（squashfs/btrfs/f2fs/erofs）、内存压缩（zram/zswap）、持久化存储（pstore）、内核启动解包和模块解包。所有库遵循同一模式：**调用者预分配 workspace，库内部不做动态分配**。

## 2 两种访问路径

| 路径 | 说明 | 消费者 |
|------|------|--------|
| **直接调用库函数** | 文件系统直接 `#include <linux/zlib.h>` 等 | squashfs、btrfs、f2fs、boot decompress |
| **通过 crypto API** | `crypto_comp_compress()`/`crypto_comp_decompress()` | zram、pstore |

**没有统一的压缩抽象层**——每个子系统有自己的 ops 结构（`squashfs_decompressor`、`btrfs_compress_op`、`f2fs_compress_ops`）。

## 3 zlib

头文件：`include/linux/zlib.h`（599 行）

### 定位

**功能**：zlib 是内核中最老牌的压缩库，提供 DEFLATE 算法的完整实现，支持流式压缩和解压。它是内核启动解包（bzImage 中的 `initrd` 解压）和模块解包的默认选择，也用于 PPP 链路的压缩协商和 AppArmor 策略文件的压缩存储。

**实现方式**：基于 `z_stream` 结构体（`include/linux/zlib.h`）管理压缩状态，调用者必须预分配 `strm.workspace`（大小由 `zlib_deflate_workspacesize()` 计算，默认约 256 KB）。压缩通过 `zlib_deflateInit2()` 初始化、`zlib_deflate()` 流式执行、`zlib_deflateEnd()` 结束；解压流程对称，workspace 仅需约 32 KB。库内部不做任何动态内存分配，所有缓冲区由调用者提供。

**应用场景**：直接调用者包括 squashfs（`fs/squashfs/zlib_wrapper.c`）、btrfs（`fs/btrfs/zlib.c`）、jffs2（`fs/jffs2/compr_zlib.c`）、PPP deflate（`drivers/net/ppp/ppp_deflate.c`）、boot decompress（`lib/decompress_inflate.c`）、模块解包（`kernel/module/decompress.c`）和 crypto 包装（`crypto/deflate.c`）。全树共 27 个文件使用 zlib 压缩函数。

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `zlib_deflate_workspacesize(windowBits, memLevel)` | 计算压缩 workspace 大小 | 默认约 256 KB |
| `zlib_inflate_workspacesize()` | 计算解压 workspace 大小 | 约 32 KB |
| `zlib_deflateInit2(strm, level, method, windowBits, memLevel, strategy)` | 初始化压缩 | 必须预分配 `strm->workspace` |
| `zlib_deflate(strm, flush)` | 压缩数据 | 可多次调用（流式） |
| `zlib_deflateEnd(strm)` | 结束压缩 | |
| `zlib_inflateInit2(strm, windowBits)` | 初始化解压 | |
| `zlib_inflate(strm, flush)` | 解压数据 | |
| `zlib_inflateEnd(strm)` | 结束解压 | |
| `deflateBound(s)` | 压缩最坏情况大小 | inline |

### 使用模式

```c
z_stream strm = {};
strm.workspace = vzalloc(zlib_deflate_workspacesize(MAX_WBITS, DEF_MEM_LEVEL));
zlib_deflateInit2(&strm, level, Z_DEFLATED, MAX_WBITS, DEF_MEM_LEVEL, Z_DEFAULT_STRATEGY);
strm.next_in = src; strm.avail_in = src_len;
strm.next_out = dst; strm.avail_out = dst_len;
zlib_deflate(&strm, Z_FINISH);
zlib_deflateEnd(&strm);
vfree(strm.workspace);
```

### 消费者

squashfs、btrfs、jffs2、PPP deflate、boot decompress（`lib/decompress_inflate.c`）、模块解包（`kernel/module/decompress.c`）、AppArmor。共 27 个文件。

## 4 LZO

头文件：`include/linux/lzo.h`（58 行）

### 定位

**功能**：LZO 是内核中最简洁的压缩库，提供 LZO1x 算法的单次调用接口。它的设计目标是低延迟而非高压缩比——解压完全不需要 workspace，压缩仅需 16 KB workspace，是所有压缩库中资源开销最低的之一。内核还包含 RLE 变体（`lzorle1x_1_compress`），对含长串零的数据有更好的压缩效果。

**实现方式**：API 最为简单——没有 init/end 生命周期，直接调用 `lzo1x_1_compress()` 传入源、目标缓冲区和 16 KB workspace 即可完成压缩。解压通过 `lzo1x_decompress_safe()` 一步完成，不需要任何额外内存。`lzo1x_1_compress_safe()` 处理源/目标缓冲区重叠的情况。最坏情况输出大小由 `lzo1x_worst_compress(x)` 计算：`(x) + (x)/16 + 64 + 3 + 2`。

**应用场景**：主要用于对延迟敏感的场景。直接调用者包括 squashfs（`fs/squashfs/lzo_wrapper.c`）、btrfs（`fs/btrfs/lzo.c`）、f2fs（`fs/f2fs/compress.c`）、jffs2（`fs/jffs2/compr_lzo.c`）和 regmap LZO 缓存（`drivers/base/regmap/regcache-lzo.c`）。通过 crypto API 间接使用的包括 zram 和 pstore。内核启动解包也支持 LZO 格式（`lib/decompress_unlzo.c`）。全树共 12 个文件使用 LZO 函数。

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `lzo1x_1_compress(src, src_len, dst, dst_len, wrkmem)` | 压缩 | wrkmem = 16 KB |
| `lzo1x_1_compress_safe(src, src_len, dst, dst_len, wrkmem)` | 安全压缩 | 处理源/目标重叠 |
| `lzorle1x_1_compress(src, src_len, dst, dst_len, wrkmem)` | RLE 变体压缩 | 对含长串零的数据更好 |
| `lzo1x_decompress_safe(src, src_len, dst, dst_len)` | 解压 | 不需要 workspace |
| `lzo1x_worst_compress(x)` | 最坏情况大小 | `(x) + (x)/16 + 64 + 3 + 2` |

### 使用模式

```c
void *wrkmem = kvmalloc(LZO1X_1_MEM_COMPRESS, GFP_KERNEL);  /* 16 KB */
size_t dst_len;
lzo1x_1_compress(src, src_len, dst, &dst_len, wrkmem);
kvfree(wrkmem);

/* 解压：*/
size_t out_len;
lzo1x_decompress_safe(src, src_len, dst, &out_len);
```

### 特点

最简单的 API——无 init/end 生命周期。压缩需 16 KB workspace，解压不需要。

### 消费者

squashfs、btrfs、f2fs、pstore（via crypto）、zram（via crypto）、jffs2、boot decompress。共 12 个文件。

## 5 Zstandard (zstd)

头文件：`include/linux/zstd.h`（447 行）——内核包装 API

### 定位

**功能**：zstd 是内核中压缩比和速度可调范围最大的压缩库，提供从单次操作到流式处理的三层 API。它在压缩比上接近 xz 但速度快得多，在速度上接近 lz4 但压缩比高得多，因此成为需要灵活调节压缩策略的场景的首选——btrfs 和 f2fs 的压缩挂载选项都支持 zstd 级别调节。

**实现方式**：内核包装将上游 zstd 库适配为三种使用模式。单次模式通过 `zstd_init_cctx()` 初始化压缩上下文后调用 `zstd_compress_cctx()` 一步完成；流式模式通过 `zstd_init_cstream()`/`zstd_compress_stream()`/`zstd_end_stream()` 分步执行。所有操作需要调用者预分配 workspace（大小由 `zstd_cctx_workspace_bound()` 计算，取决于压缩级别和窗口大小）。参数通过 `zstd_get_params(level, estimated_src_size)` 获取，压缩级别范围由 `zstd_min_clevel()`/`zstd_max_clevel()` 界定。

**应用场景**：直接调用者包括 squashfs（`fs/squashfs/zstd_wrapper.c`）、btrfs（`fs/btrfs/zstd.c`）、f2fs（`fs/f2fs/compress.c`）、UBIFS（`fs/ubifs/`）、boot decompress（`lib/decompress_unzstd.c`）和 firmware loader（`drivers/base/firmware_loader/main.c`）。通过 crypto API 间接使用的包括 zram 和 pstore（`fs/pstore/platform.c`）。全树共 10 个文件直接包含 `<linux/zstd.h>`。

### 核心 API

**辅助函数**：

| API | 说明 |
|-----|------|
| `zstd_compress_bound(src_size)` | 压缩最坏情况大小 |
| `zstd_is_error(code)` | 检查返回值是否错误 |
| `zstd_get_params(level, estimated_src_size)` | 获取压缩参数 |
| `zstd_min_clevel()` / `zstd_max_clevel()` | 压缩级别范围 |

**单次压缩/解压**：

| API | 说明 | 备注 |
|-----|------|------|
| `zstd_cctx_workspace_bound(cparams)` | CCtx workspace 大小 | |
| `zstd_init_cctx(workspace, size)` | 初始化压缩上下文 | |
| `zstd_compress_cctx(cctx, dst, dst_cap, src, src_size, params)` | 单次压缩 | |
| `zstd_dctx_workspace_bound()` | DCtx workspace 大小 | |
| `zstd_init_dctx(workspace, size)` | 初始化解压上下文 | |
| `zstd_decompress_dctx(dctx, dst, dst_cap, src, src_size)` | 单次解压 | |

**流式压缩/解压**：

| API | 说明 |
|-----|------|
| `zstd_init_cstream(params, pledged_src_size, workspace, size)` | 初始化流式压缩 |
| `zstd_compress_stream(cstream, output, input)` | 流式压缩 |
| `zstd_flush_stream(cstream, output)` | 刷新 |
| `zstd_end_stream(cstream, output)` | 结束 |
| `zstd_init_dstream(max_window_size, workspace, size)` | 初始化流式解压 |
| `zstd_decompress_stream(dstream, output, input)` | 流式解压 |

### 使用模式

```c
/* 单次压缩：*/
zstd_parameters params = zstd_get_params(level, src_size);
size_t ws_size = zstd_cctx_workspace_bound(&params.cParams);
void *ws = vzalloc(ws_size);
zstd_cctx *cctx = zstd_init_cctx(ws, ws_size);
size_t result = zstd_compress_cctx(cctx, dst, dst_cap, src, src_size, &params);
vfree(ws);
```

### 特点

最复杂的 API——三层（单次/流式/帧检查），所有操作需要预分配 workspace。压缩比和速度可调范围最大。

### 消费者

squashfs、btrfs、f2fs、ubifs、pstore（via crypto）、zram（via crypto）、boot decompress、firmware loader。共 12 个文件。

## 6 LZ4

头文件：`include/linux/lz4.h`（648 行）

### 定位

**功能**：LZ4 是内核中速度最快的压缩库，解压不需要任何 workspace，压缩仅需 16 KB workspace（HC 模式需 256 KB）。它提供标准模式、HC（高压缩比）模式和流式模式三种接口，还支持字典模式以提升小数据的压缩效率。erofs 文件系统选择 LZ4 作为默认压缩算法，正是因为其解压速度对文件系统性能至关重要。

**实现方式**：单次操作通过 `LZ4_compress_default()` 或 `LZ4_compress_fast()`（可调节加速比）完成压缩，`LZ4_decompress_safe()` 完成安全解压。流式操作通过 `LZ4_resetStream()` 初始化、`LZ4_compress_fast_continue()`/`LZ4_decompress_safe_continue()` 分步执行。HC 模式通过 `LZ4_compress_HC()` 提供更高压缩比，workspace 增大到 256 KB。字典模式通过 `LZ4_loadDict()` 加载预定义字典，适合反复压缩相似结构的小数据块。

**应用场景**：直接调用者包括 squashfs（`fs/squashfs/lz4_wrapper.c`）、f2fs（`fs/f2fs/compress.c`）、erofs（`fs/erofs/decompressor.c`）和 DRM GPU 驱动（`drivers/gpu/drm/gud/gud_pipe.c`，用于压缩显示帧传输）。通过 crypto API 间接使用的包括 zram 和 pstore。内核启动解包也支持 LZ4 格式（`lib/decompress_unlz4.c`）。全树共 7 个文件直接调用 `LZ4_decompress_safe`。

### 核心 API

**单次操作**：

| API | 说明 | 备注 |
|-----|------|------|
| `LZ4_compress_default(src, dst, inputSize, maxOutput, wrkmem)` | 标准压缩 | wrkmem = 16 KB |
| `LZ4_compress_fast(src, dst, inputSize, maxOutput, acceleration, wrkmem)` | 可调加速比 | |
| `LZ4_decompress_safe(src, dst, compSize, maxDecomp)` | 安全解压 | 不需要 workspace |
| `LZ4_decompress_safe_partial(src, dst, compSize, targetOut, maxDecomp)` | 部分解压 | |

**HC（高压缩比）模式**：

| API | 说明 | 备注 |
|-----|------|------|
| `LZ4_compress_HC(src, dst, srcSize, dstCap, level, wrkmem)` | HC 压缩 | wrkmem = 256 KB |
| `LZ4_compress_HC_continue(streamHC, src, dst, srcSize, maxDst)` | HC 流式压缩 | |

**流式操作**：

| API | 说明 |
|-----|------|
| `LZ4_resetStream(stream)` | 重置流 |
| `LZ4_loadDict(stream, dict, dictSize)` | 加载字典 |
| `LZ4_compress_fast_continue(stream, src, dst, srcSize, maxDst, accel)` | 流式压缩 |
| `LZ4_decompress_safe_continue(streamDecode, src, dst, compSize, maxDecomp)` | 流式解压 |

### 特点

速度最快的压缩库。解压不需要 workspace。支持字典模式和 HC 高压缩比模式。

### 消费者

squashfs、f2fs、erofs、pstore（via crypto）、zram（via crypto）、boot decompress。共 11 个文件。

## 7 对比汇总

| 属性 | zlib | lzo | zstd | lz4 |
|------|------|-----|------|-----|
| 库大小 | 4,258 行 | 835 行 | 18,986 行 | 2,675 行 |
| 压缩 workspace | ~256 KB | 16 KB | 可变（级别相关） | 16 KB（HC: 256 KB） |
| 解压 workspace | ~32 KB | 0 | 可变 | 0 |
| API 复杂度 | 流式多调用 | 单次调用 | 三层 API | 单次 + 流式 + HC |
| 压缩比 | 中 | 低 | 高 | 低 |
| 速度 | 中 | 快 | 可调 | 最快 |
| 文件系统 | squashfs/btrfs/jffs2 | squashfs/btrfs/f2fs | squashfs/btrfs/f2fs/ubifs | squashfs/f2fs/erofs |
| crypto 包装 | "deflate" | "lzo"/"lzo-rle" | "zstd" | "lz4"/"lz4hc" |
