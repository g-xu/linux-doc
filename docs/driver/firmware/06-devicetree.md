---
title: 设备树
---

# Firmware Loader · 配置机制

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。

## §0 发现路径分诊表

firmware_loader 子系统**没有** Device Tree 绑定。配置通过以下机制完成：

| 配置机制 | 谁消费 | 运行期可调 | 位置 |
|---|---|---|---|
| Kconfig | 编译期 | 否 | `drivers/base/firmware_loader/Kconfig` |
| 模块参数 | `main.c` | 启动时 / 模块加载时 | `firmware_class.path` |
| sysfs 类属性 | `sysfs.c` | 是 | `/sys/class/firmware/timeout` |
| sysfs 设备属性 | `sysfs.c` / `sysfs_upload.c` | 是 | `/sys/class/firmware/<name>/` |
| sysctl | `fallback_table.c` | 是 | `/proc/sys/kernel/firmware_config/` |
| sysfs 模块参数 | `main.c` | 是 | `/sys/module/firmware_class/parameters/path` |

---

## §1 编译期配置（Kconfig）

### §1.1 核心真正会读的选项

| 选项 | 默认 | 效果 |
|---|---|---|
| `CONFIG_FW_LOADER` | y | 总开关——关闭则所有 API 返回 `-EINVAL` |
| `CONFIG_FW_LOADER_USER_HELPER` | n | 启用 sysfs 回退机制 |
| `CONFIG_FW_LOADER_USER_HELPER_FALLBACK` | n | 强制所有请求走 sysfs 回退 |
| `CONFIG_FW_LOADER_COMPRESS_XZ` | y（如果 COMPRESS 开） | XZ 解压支持 |
| `CONFIG_FW_LOADER_COMPRESS_ZSTD` | n | ZSTD 解压支持 |
| `CONFIG_FW_CACHE` | y（如果 PM_SLEEP） | suspend 期间固件缓存 |
| `CONFIG_FW_UPLOAD` | n | 用户态固件上传接口 |
| `CONFIG_EXTRA_FIRMWARE` | "" | 内建固件列表 |

### §1.2 隐式 select 链

```
FW_LOADER_USER_HELPER       → select FW_LOADER_SYSFS + FW_LOADER_PAGED_BUF
FW_LOADER_COMPRESS_XZ      → select FW_LOADER_PAGED_BUF + XZ_DEC
FW_LOADER_COMPRESS_ZSTD    → select ZSTD_DECOMPRESS
FW_UPLOAD                  → select FW_LOADER_SYSFS + FW_LOADER_PAGED_BUF
```

`FW_LOADER_PAGED_BUF` 和 `FW_LOADER_SYSFS` 是内部选项——用户不直接选择，被上述选项自动 select。

---

## §2 运行期配置

### §2.1 模块参数：自定义搜索路径

```bash
# 启动时
firmware_class.path=/custom/firmware/path

# 运行期
echo /custom/firmware/path > /sys/module/firmware_class/parameters/path
```

实现：`main.c:470-485`。`fw_path_para[256]` 是 `fw_path[]` 数组的第一个元素，优先级最高。

**限制**：路径最长 256 字符。空字符串（默认）被跳过——不影响搜索顺序。

### §2.2 sysfs 类属性：超时

```bash
# 查看当前超时
cat /sys/class/firmware/timeout

# 设置超时（秒）；0 = 无限等待
echo 120 > /sys/class/firmware/timeout
```

实现：`sysfs.c:28-59`。修改 `fw_fallback_config.loading_timeout`。

**影响范围**：全局——影响所有后续的 sysfs 回退请求。

### §2.3 sysctl：回退控制

```bash
# 强制 sysfs 回退（等价于 CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y）
echo 1 > /proc/sys/kernel/firmware_config/force_sysfs_fallback

# 完全禁用 sysfs 回退
echo 1 > /proc/sys/kernel/firmware_config/ignore_sysfs_fallback
```

实现：`fallback_table.c:28-48`。需要 `CONFIG_FW_LOADER_USER_HELPER` + `CONFIG_SYSCTL`。

**优先级**：`ignore_sysfs_fallback` 优先于 `force_sysfs_fallback`——两个都设 1 时回退被禁用。

---

## §3 固件搜索路径

`fw_get_filesystem_firmware()`（`main.c:513-576`）按以下顺序搜索：

| 优先级 | 路径 | 来源 |
|---|---|---|
| 1 | `fw_path_para`（模块参数） | 用户配置 |
| 2 | `/lib/firmware/updates/<UTS_RELEASE>/` | 硬编码 |
| 3 | `/lib/firmware/updates/` | 硬编码 |
| 4 | `/lib/firmware/<UTS_RELEASE>/` | 硬编码 |
| 5 | `/lib/firmware/` | 硬编码 |

`<UTS_RELEASE>` 是内核版本号（如 `6.1.158`），由 `<generated/utsrelease.h>` 提供。

**搜索规则**：
- 每个路径尝试原始文件名 → `.zst` → `.xz`（如果启用压缩）
- 第一个命中的路径返回——不继续搜索
- 使用 `kernel_read_file_from_path_initns`——从 init 的挂载命名空间读取
- 路径总长不超过 `PATH_MAX`（4096 字符）

---

## §4 配置错误分诊

### §4.1 固件找不到

| 症状 | 可能原因 | 排查 |
|---|---|---|
| `Direct firmware load for X failed with error -2` | 文件不存在于任何搜索路径 | `find /lib/firmware -name X` |
| 同上 + 无 sysfs 回退 | `request_firmware_direct` 或 `NOFALLBACK_SYSFS` | 检查 API 调用 |
| `Falling back to sysfs fallback for: X` | 直接查找失败，正在等用户空间 | 检查 udev 或手动写入 sysfs |
| 超时后返回 `-ETIMEDOUT` | sysfs 回退无人响应 | 检查 `CONFIG_FW_LOADER_USER_HELPER` + uevent 监听 |

### §4.2 安全拒绝

| 症状 | 可能原因 | 排查 |
|---|---|---|
| `path contains '..' component` | 固件名含路径穿越 | 检查驱动构造名字的方式 |
| LSM 拒绝 | IMA / Lockdown 策略 | `dmesg | grep -i ima` |
| `CAP_SYS_RAWIO` 拒绝 | sysfs data 写入无 root | 确保以 root 写入 |

### §4.3 缓存问题

| 症状 | 可能原因 | 排查 |
|---|---|---|
| resume 后固件加载失败 | `CONFIG_FW_CACHE` 未启用 | `zcat /proc/config.gz \| grep FW_CACHE` |
| `request_firmware_into_buf` 返回 `-EOPNOTSUPP` | 该设备已有缓存注册 | 不要对同一设备混用 into_buf 和普通请求 |

### §4.4 上传问题

| 症状 | 可能原因 | 排查 |
|---|---|---|
| `/sys/class/firmware/X/status` 不存在 | `CONFIG_FW_UPLOAD` 未启用 | `zcat /proc/config.gz \| grep FW_UPLOAD` |
| 写 cancel 返回 `-ENODEV` | 上传已完成或空闲 | 检查 `status` 属性 |
| 写 cancel 后仍完成 | 驱动 cancel ops 未生效 | 检查驱动 cancel 实现 |

---

## §5 错误消息 → 原因映射

| 内核日志 | 触发位置 | 原因 |
|---|---|---|
| `loading %s failed with error %d` | `main.c:545-546` | 文件系统读取失败（非 `-ENOENT`） |
| `loading %s failed for no such file or directory` | `main.c:548-549` | 文件不存在（`-ENOENT`） |
| `Direct firmware load for %s failed with error %d` | `main.c:880-882` | 所有直接查找失败 |
| `Falling back to sysfs fallback for: %s` | `fallback.c:229` | 进入 sysfs 回退 |
| `firmware: requesting %s` | `fallback.c:100` | uevent 已发送 |
| `%s: loading timed out` | `fallback.c:138-139` | 异步请求超时 |
| `%s: will not be loaded` | `fallback.c:145-146` | 同步请求时 usermode helper 锁失败 |
| `Firmware load for '%s' refused, path contains '..' component` | `main.c:835-837` | 路径穿越被拒 |
| `Attempt to register without all required ops` | `sysfs_upload.c:308-309` | 上传注册缺少必须 ops |
| `write-op wrote zero data` | `sysfs_upload.c:185-186` | 上传 write 回调返回 0 字节 |
| `Invalid status during secure update: %d` | `sysfs_upload.c:40` | 非法进度码 |
| `Invalid error code during secure update: %d` | `sysfs_upload.c:53-54` | 非法错误码 |
