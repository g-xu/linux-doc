---
title: 调试方法
---

# Firmware Loader · 调试

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。

## §1 编译期开关

| 开关 | 效果 | 调试价值 |
|---|---|---|
| `CONFIG_FW_LOADER` | 总开关 | 关闭后所有 API 返回 `-EINVAL`——用于确认问题在固件子系统 |
| `CONFIG_FW_LOADER_USER_HELPER` | sysfs 回退 | 开启后 `/sys/class/firmware/` 下出现 `timeout` 属性 |
| `CONFIG_FW_LOADER_USER_HELPER_FALLBACK` | 强制回退 | 所有请求（除 `request_firmware_direct`）走 sysfs |
| `CONFIG_FW_LOADER_COMPRESS_XZ` | XZ 解压 | 开启后可加载 `.xz` 后缀固件 |
| `CONFIG_FW_LOADER_COMPRESS_ZSTD` | ZSTD 解压 | 开启后可加载 `.zst` 后缀固件 |
| `CONFIG_FW_CACHE` | PM 缓存 | 开启后 suspend 前自动缓存固件 |
| `CONFIG_FW_UPLOAD` | 固件上传 | 开启后 `/sys/class/firmware/` 下出现上传属性 |
| `CONFIG_EXTRA_FIRMWARE` | 内建固件 | 编译时嵌入——运行时无法修改 |
| `CONFIG_DEBUG_FS` | debugfs | 本子系统不使用 debugfs |
| `CONFIG_DYNAMIC_DEBUG` | 动态调试 | 可对 `pr_debug` / `dev_dbg` 按需启用 |

### 动态调试示例

```bash
# 启用所有固件加载器的 pr_debug
echo 'module firmware_class +p' > /sys/kernel/debug/dynamic_debug/control

# 只启用特定函数的调试
echo 'file drivers/base/firmware_loader/main.c +p' > /sys/kernel/debug/dynamic_debug/control
```

启用后 `dev_dbg` / `pr_debug` 输出出现在 `dmesg`。关键调试点：

- `main.c:150`：`__allocate_fw_priv` 分配
- `main.c:184`：批处理命中
- `main.c:555`：文件系统加载成功
- `main.c:557-558`：解压开始
- `main.c:569`：直接加载（无解压）
- `main.c:744`：内建固件命中
- `fallback.c:100`：uevent 发送

---

## §2 日志消息分级

### §2.1 始终输出

| 级别 | 消息 | 位置 | 触发条件 |
|---|---|---|---|
| `dev_err` | `%s: device_register failed` | `fallback.c:84` | sysfs 设备注册失败 |
| `dev_err` | `%s: map pages failed` | `sysfs.c:185` | sysfs 加载完成但 vmap 失败 |
| `dev_err` | `Firmware load for '%s' refused, path contains '..' component` | `main.c:835-837` | 路径穿越被拒 |
| `dev_err` | `Attempt to register without all required ops` | `sysfs_upload.c:308` | 上传注册缺少必须 ops |
| `dev_err` | `firmware: %s: kmalloc(struct firmware) failed` | `main.c:738` | 内存分配失败 |
| `dev_warn` | `loading %s failed with error %d` | `main.c:545` | 文件系统读取失败（非 ENOENT） |
| `dev_warn` | `Direct firmware load for %s failed with error %d` | `main.c:880` | 所有直接查找失败 |
| `dev_warn` | `Falling back to sysfs fallback for: %s` | `fallback.c:229` | 进入 sysfs 回退 |
| `dev_warn` | `write-op wrote zero data` | `sysfs_upload.c:185` | 上传 write 返回 0 字节 |
| `pr_info_once` | `Ignoring firmware sysfs fallback due to sysctl knob` | `fallback.c:183` | sysctl 禁用回退（仅首次） |

### §2.2 条件输出

| 级别 | 消息 | 条件 | 位置 |
|---|---|---|---|
| `dev_warn` | 同 `Direct firmware load...` | `!(opt_flags & FW_OPT_NO_WARN)` | `main.c:879` |
| `dev_dbg` | `loading %s failed for no such file or directory` | ENOENT | `main.c:548` |
| `dev_dbg` | `Loading firmware from %s` | 成功 | `main.c:555` |
| `dev_dbg` | `f/w decompressing %s` | 解压 | `main.c:557` |
| `dev_dbg` | `direct-loading %s` | 直接加载 | `main.c:569` |
| `dev_dbg` | `using built-in %s` | 内建命中 | `main.c:744` |
| `dev_dbg` | `batched request - sharing...` | 批处理命中 | `main.c:184` |
| `dev_dbg` | `firmware: requesting %s` | uevent 发送 | `fallback.c:100` |
| `dev_dbg` | `firmware: %s loading timed out` | 异步超时 | `fallback.c:138` |

### §2.3 `dev_warn` 抑制

以下 API 设了 `FW_OPT_NO_WARN`，失败时**不输出** `dev_warn`：

- `firmware_request_nowarn`
- `request_firmware_direct`

`request_firmware` 和 `firmware_request_platform` 失败时始终输出警告。

---

## §3 sysfs 运行时观测

### §3.1 类级属性

```bash
# 当前超时值
cat /sys/class/firmware/timeout

# 模块参数（自定义搜索路径）
cat /sys/module/firmware_class/parameters/path
```

### §3.2 设备级属性（sysfs 回退激活时）

```bash
# 查看是否有等待中的固件请求
ls /sys/class/firmware/

# 查看特定请求的状态
cat /sys/class/firmware/<fw_name>/loading
# 1 = 加载中, 0 = 已完成

# 手动加载固件（调试用）
echo 1 > /sys/class/firmware/<fw_name>/loading
cat /lib/firmware/<fw_name> > /sys/class/firmware/<fw_name>/data
echo 0 > /sys/class/firmware/<fw_name>/loading
```

### §3.3 上传状态监控

```bash
# 上传进度
cat /sys/class/firmware/<name>/status
# idle / receiving / preparing / transferring / programming

# 错误信息
cat /sys/class/firmware/<name>/error
# <progress>:<error> 或 0（无错）或 -EBUSY（进行中）

# 剩余大小
cat /sys/class/firmware/<name>/remaining_size

# 取消上传
echo 1 > /sys/class/firmware/<name>/cancel
```

---

## §4 错误码分诊

### §4.1 `-ENOENT`（固件未找到）

同码多源——以下位置都可能返回：

| 位置 | 含义 |
|---|---|
| `main.c:543`（`fw_get_filesystem_firmware` 循环后） | 所有搜索路径均未找到文件 |
| `fallback_platform.c:26` | EFI 嵌入固件未找到 |
| `fallback.c` 通过 `firmware_fallback_sysfs` 传递 | 直接查找失败后的原始错误 |
| `assign_fw`（`main.c:689`） | `fw_priv->size` 为零——加载完成但数据为空 |

**排查步骤**：
1. `dmesg | grep "firmware"` 看搜索路径日志
2. 确认文件存在于 `/lib/firmware/` 下
3. 检查文件名拼写（区分大小写）
4. 检查 `fw_path_para` 是否覆盖了默认路径

### §4.2 `-EINVAL`（参数无效）

| 位置 | 含义 |
|---|---|
| `main.c:827` | `firmware_p` 为 NULL |
| `main.c:830` | `name` 为空 |
| `main.c:838` | `name` 含 `..` |
| `sysfs_upload.c:304` | 上传 `name` 为空 |
| `sysfs_upload.c:309` | 上传 ops 不完整 |

### §4.3 `-ENOMEM`（内存不足）

高频出现——`kzalloc`、`vmalloc`、`vmap`、`kstrdup_const` 的失败路径均返回此值。排查：`dmesg | grep -i "out of memory"` 或检查系统内存压力。

### §4.4 `-EOPNOTSUPP`（操作不支持）

| 位置 | 含义 |
|---|---|
| `main.c:1062` | `request_firmware_into_buf` 时已有缓存 |
| `main.c:1092` | `request_partial_firmware_into_buf` 同上 |
| `main.c:1195` | `request_firmware_nowait(uevent=false)` 时已有缓存 |

**排查**：同一设备不要混用 `into_buf` 和普通 `request_firmware`。

### §4.5 `-EBUSY`（资源忙）

| 位置 | 含义 |
|---|---|
| `fallback.c:140` | 异步请求时 usermode helper 锁超时 |
| `sysfs_upload.c:246` | 上传进行中再次发起 |
| `sysfs_upload.c:77` | 上传进行中读 error 属性 |

---

## §5 tracepoint

firmware_loader 子系统**没有定义 tracepoint**。没有 `include/trace/events/firmware.h`，没有 `TRACE_EVENT` 或 `DEFINE_EVENT` 宏。

调试手段限于：`dev_dbg` / `pr_debug`（需动态调试启用）+ sysfs 属性观测 + `dmesg` 日志。

---

## §6 故障注入

firmware_loader 子系统**没有**内建的故障注入机制。

没有 `fail_*` 注入点、没有 `error_injection` 列表、没有 debugfs 注入接口。

如需测试错误路径，可用以下间接方法：

| 目标路径 | 方法 |
|---|---|
| `-ENOENT` | 请求不存在的固件名 |
| `-EINVAL` | 传含 `..` 的固件名 |
| sysfs 回退超时 | 设 `timeout=1` 后请求固件但不写入 sysfs |
| 上传错误 | 驱动 ops 返回非 `FW_UPLOAD_ERR_NONE` |
| 内存压力 | 通过 `/sys/kernel/debug/failslab/` 或 `/proc/sys/vm/` 施压 |

---

## §7 速查页

```
# 1. 确认固件子系统已启用
zcat /proc/config.gz | grep CONFIG_FW_LOADER

# 2. 启用调试日志
echo 'module firmware_class +p' > /sys/kernel/debug/dynamic_debug/control

# 3. 查看搜索路径
cat /sys/module/firmware_class/parameters/path

# 4. 查看等待中的请求
ls /sys/class/firmware/

# 5. 手动加载固件
echo 1 > /sys/class/firmware/MY_FW/loading
cat /lib/firmware/MY_FW > /sys/class/firmware/MY_FW/data
echo 0 > /sys/class/firmware/MY_FW/loading

# 6. 查看上传状态
cat /sys/class/firmware/MY_DEV/status
cat /sys/class/firmware/MY_DEV/error

# 7. 检查 sysctl
cat /proc/sys/kernel/firmware_config/force_sysfs_fallback
cat /proc/sys/kernel/firmware_config/ignore_sysfs_fallback

# 8. 关闭调试日志
echo 'module firmware_class -p' > /sys/kernel/debug/dynamic_debug/control
```
