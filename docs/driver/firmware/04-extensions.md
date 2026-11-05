---
title: 扩展机制
---

# Firmware Loader · 子框架

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 组织轴：每节一个独立子框架，互不调用。

## §0 分诊表

| 子框架 | Kconfig | 挂载点（在 03 时间线的哪一步） | arm64 相关 |
|---|---|---|---|
| 内建固件 | `CONFIG_EXTRA_FIRMWARE` | §A2（最先查） | **是** |
| 压缩固件 | `CONFIG_FW_LOADER_COMPRESS_XZ/ZSTD` | §A4 之后、§A6 之前 | **是** |
| sysfs 回退 | `CONFIG_FW_LOADER_USER_HELPER` | §A7（最后手段） | 是（但 udev 已不用） |
| 平台固件回退 | `CONFIG_EFI_EMBEDDED_FIRMWARE` | §A6 | **否**（仅 x86） |
| 固件上传 | `CONFIG_FW_UPLOAD` | 独立路径，不在请求管线中 | **是**（FPGA 等） |
| PM 缓存 | `CONFIG_FW_CACHE` | 横切——suspend/resume 时 | **是** |

---

## §1 内建固件（`CONFIG_EXTRA_FIRMWARE`）

### 机制

编译时把固件文件编入内核二进制。`builtin/main.c` 的 Makefile（`builtin/Makefile`）为每个固件生成一个 `.section .builtin_fw` 的 `.o` 文件，链接后落在 `__start_builtin_fw[]` ~ `__end_builtin_fw[]` 段。

```c
struct builtin_fw {
    char *name;
    void *data;
    unsigned long size;
};
```

查找是线性遍历（`builtin/main.c:54-60`）——按名字 `strcmp` 匹配，找到即返回指针。

### 挂载点

在 `_request_firmware_prepare()`（`main.c:743-746`）中，**先于** `alloc_lookup_fw_priv` 查找。命中时直接返回 0（已赋值），不走后续任何路径。

### 特殊行为

- `release_firmware` 通过 `firmware_is_builtin()`（`builtin/main.c:95-104`）检测内建固件——比较 `fw->data` 指针是否落在内建段。是则跳过 `firmware_free_data`，只 `kfree(fw)` 本身
- 不支持压缩文件（Kconfig 明确说明）
- `firmware_request_builtin()` 导出为 `EXPORT_SYMBOL_NS_GPL(TEST_FIRMWARE)`——仅测试模块可用
- `firmware_request_builtin_buf()` 不导出——仅供 `firmware_loader` 内部使用

### 全树使用统计

```bash
grep -rn 'CONFIG_EXTRA_FIRMWARE' --include='Kconfig*' arch/arm64/ | wc -l
```

arm64 的 Kconfig 不直接 select `EXTRA_FIRMWARE`——它由用户在顶层配置。

---

## §2 压缩固件（`CONFIG_FW_LOADER_COMPRESS`）

### 机制

文件系统加载的**备选**——原始文件返回 `-ENOENT` 后，追加 `.zst` / `.xz` 后缀再试一次。

两种解压实现独立编译：

| 格式 | Kconfig | 解压函数 | 位置 |
|---|---|---|---|
| XZ | `FW_LOADER_COMPRESS_XZ` | `fw_decompress_xz` | `main.c:458-466` |
| ZSTD | `FW_LOADER_COMPRESS_ZSTD` | `fw_decompress_zstd` | `main.c:310-367` |

### 挂载点

在 `_request_firmware()`（`main.c:865-874`）中：

```
直接加载("") → -ENOENT? → ZSTD(".zst") → -ENOENT? → XZ(".xz") → 平台回退 → sysfs 回退
```

**限制**：仅全读模式支持压缩——`FW_OPT_PARTIAL` 请求跳过解压（`main.c:862-863`）。

### XZ 双模式

- **单发模式**（`fw_decompress_xz_single`，`main.c:385-408`）：`fw_priv->data` 已存在（into_buf 场景），`XZ_SINGLE` 一次性解压到预分配缓冲区
- **分页模式**（`fw_decompress_xz_pages`，`main.c:411-456`）：逐页解压，每页 `kmap_local_page` 写入，最终 `vmap` 映射。这是默认模式

### ZSTD 特点

- 从帧头读取 `frameContentSize` 预分配输出缓冲区
- 未知大小时返回 `-EINVAL`（`main.c:325-328`）
- 解压工作区通过 `kvzalloc` 分配

### 压缩文件命名约定

固件文件 `example.bin` 的压缩版本为 `example.bin.zst` 或 `example.bin.xz`。压缩文件与原始文件同名但加后缀——框架自动尝试。

---

## §3 sysfs 回退（`CONFIG_FW_LOADER_USER_HELPER`）

### 机制

当所有直接查找失败后，在 `/sys/class/firmware/<fw_name>/` 下创建 `loading` + `data` 属性，通过 uevent 通知用户空间。用户空间写入固件数据后写 `loading=0` 完成加载。

### 挂载点

在 `_request_firmware()`（`main.c:883-885`）中，作为**最后手段**。仅在以下条件全部满足时执行：

1. `ignore_sysfs_fallback` sysctl 为 0
2. 请求未设 `FW_OPT_NOFALLBACK_SYSFS`
3. LSM `security_kernel_load_data` 允许
4. `force_sysfs_fallback` sysctl 为 1 或请求带 `FW_OPT_USERHELPER`

### 用户空间协议

```bash
# uevent 提供 $DEVPATH 和 $FIRMWARE
echo 1 > /sys/$DEVPATH/loading          # 开始加载
cat /lib/firmware/$FIRMWARE > /sys/$DEVPATH/data  # 写入数据
echo 0 > /sys/$DEVPATH/loading          # 完成
# 或 echo -1 中止
```

### 当前状态

⚠️ udev 自 2014 年（systemd v217）起不再监听固件 uevent。大多数发行版禁用 `CONFIG_FW_LOADER_USER_HELPER_FALLBACK`。此机制目前仅两个驱动依赖：`CONFIG_LEDS_LP55XX_COMMON` 和 `CONFIG_DELL_RBU`。

### 超时

| 场景 | 超时 |
|---|---|
| 默认 | 60 秒（`fw_fallback_config.loading_timeout`） |
| 缓存（suspend 期间） | 10 秒（`fw_fallback_set_cache_timeout`） |
| 无 uevent（custom fallback） | `MAX_JIFFY_OFFSET`（无限） |
| 用户配置 | `/sys/class/firmware/timeout` 或 sysctl |

---

## §4 平台固件回退（`CONFIG_EFI_EMBEDDED_FIRMWARE`）

### 机制

某些 x86 设备的 EFI 固件中包含嵌入的外设固件。`efi_get_embedded_fw()` 扫描 `EFI_BOOT_SERVICES_CODE` 段，按 8 字节前缀 + sha256 匹配。

### arm64 相关性

**不适用**。EFI embedded-fw 文档（`Documentation/driver-api/firmware/fallback-mechanisms.rst:264-266`）明确说明：

> At the moment the EFI embedded-fw code only works on x86 because other archs free EFI_BOOT_SERVICES_CODE before the EFI embedded-fw code gets a chance to scan it.

`fallback_platform.c` 在 arm64 上编译但 `efi_get_embedded_fw()` 始终返回 `-ENOENT`。

---

## §5 固件上传（`CONFIG_FW_UPLOAD`）

### 机制

设备驱动注册一个持久 sysfs 接口，允许用户空间发起设备固件更新。与 sysfs 回退共享 `loading` / `data` 属性，但增加 `status` / `error` / `cancel` / `remaining_size`。

### 与 sysfs 回退的区别

| 维度 | sysfs 回退 | 固件上传 |
|---|---|---|
| 设备生命周期 | 临时——加载完即删除 | 持久——驱动注销前一直存在 |
| 触发方式 | 内核请求固件失败后自动创建 | 驱动主动注册 |
| 数据流向 | 用户空间 → 内核 → 驱动 | 用户空间 → 内核 → 设备 |
| 工作线程 | 无——在请求者上下文完成 | `system_long_wq` 工作线程 |
| 进度可见 | 无 | `status` sysfs 属性 |

### 驱动契约

驱动实现 `struct fw_upload_ops` 的 4 个必须回调 + 1 个可选回调：

```c
static const struct fw_upload_ops my_ops = {
    .prepare      = my_prepare,       /* 准备设备 */
    .write        = my_write,         /* 写一块数据 */
    .poll_complete = my_poll,         /* 轮询完成 */
    .cancel       = my_cancel,        /* 取消 */
    .cleanup      = my_cleanup,       /* 清理（可选） */
};
```

probe 调 `firmware_upload_register()`，remove 调 `firmware_upload_unregister()`。

### 全树使用统计

```bash
grep -rn 'firmware_upload_register' --include='*.c' drivers/ | grep -v firmware_loader
```

消费者住在 `drivers/firmware/` 和 `drivers/` 下的设备驱动中。

---

## §6 PM 缓存（`CONFIG_FW_CACHE`）

### 机制

suspend 前把已成功加载的固件缓存在内存中，resume 后立即恢复——避免等待根文件系统重新挂载。

### 工作原理

1. **注册**：`assign_fw()`（`main.c:700-707`）中，带 `UEVENT` 且非 `NOCACHE` 的请求通过 `fw_add_devm_name` 把固件名加入设备 devres
2. **缓存触发**：`fw_pm_notify`（`main.c:1494-1507`）在 `PM_SUSPEND_PREPARE` 时调 `device_cache_fw_images`
3. **异步缓存**：`dev_cache_fw_image`（`main.c:1380-1409`）遍历所有设备的 devres，对每个注册的固件名异步调 `cache_firmware`（重新调 `request_firmware`）
4. **释放**：resume 后 10 秒延迟调 `device_uncache_fw_images`

### 不兼容的 API

| API | 是否缓存 | 原因 |
|---|---|---|
| `request_firmware` | **是** | 标准路径 |
| `firmware_request_nowarn` | **是** | 同上 |
| `request_firmware_direct` | **是** | 同上 |
| `firmware_request_platform` | **是** | 同上 |
| `request_firmware_into_buf` | **否** | 预分配缓冲区无法安全缓存 |
| `request_partial_firmware_into_buf` | **否** | 同上 |
| `request_firmware_nowait(uevent=false)` | **否** | 无 uevent = 不缓存 |

---

## §7 汇总表

| 子框架 | 职责 | 挂载点 | arm64 | 消费者数 | 重跑命令 |
|---|---|---|---|---|---|
| 内建固件 | 编译时嵌入 | §A2 最先 | ✅ | N/A（编译时配置） | — |
| 压缩固件 | 减小固件文件体积 | §A4 之后 | ✅ | N/A（透明） | — |
| sysfs 回退 | 用户空间加载 | §A7 最后 | ✅ | 2 驱动 | `grep -rn 'FW_OPT_USERHELPER' --include='*.c' drivers/ \| grep -v firmware_loader \| wc -l` |
| 平台回退 | EFI 嵌入固件 | §A6 | ❌ | 0（arm64） | — |
| 固件上传 | 用户态设备更新 | 独立 | ✅ | `grep -rn 'firmware_upload_register' --include='*.c' drivers/ \| grep -v firmware_loader \| wc -l` | 同左 |
| PM 缓存 | suspend/resume | 横切 | ✅ | 自动（所有 UEVENT 请求） | — |
