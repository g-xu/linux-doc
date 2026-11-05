---
title: API 参考
---

# Firmware Loader · API 与代码地图

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 子系统类型：**框架型**——无 `bus_type`，通过 API 被其他驱动消费。

## §0 代码地图

### 源文件

头文件：`include/linux/firmware.h`；实现：`drivers/base/firmware_loader/`。

| 文件 | 行数 | 职责 |
|---|---|---|
| `main.c` | 1629 | 核心：所有公开 API、加载管线、PM 缓存、压缩解压 |
| `sysfs.c` | 419 | `firmware_class` 类注册、sysfs 属性（loading/data）、uevent |
| `sysfs_upload.c` | 407 | 固件上传 sysfs 接口、工作线程、进度/错误/取消属性 |
| `fallback.c` | 235 | sysfs 回退机制：创建 sysfs 设备、等待用户空间写入 |
| `fallback_table.c` | 69 | 回退配置全局变量 + sysctl 注册 |
| `fallback_platform.c` | 45 | EFI 嵌入固件回退（`CONFIG_EFI_EMBEDDED_FIRMWARE`） |
| `builtin/main.c` | 106 | 内建固件查找（`CONFIG_EXTRA_FIRMWARE`） |

### 内部头文件

| 文件 | 行数 | 职责 |
|---|---|---|
| `firmware.h` | 199 | `enum fw_opt`、`enum fw_status`、`struct fw_priv`、状态机 inline 函数 |
| `sysfs.h` | 122 | `struct fw_sysfs`、`struct firmware_fallback_config`、sysfs 注册声明 |
| `sysfs_upload.h` | 41 | `enum fw_upload_prog`、`struct fw_upload_priv` |
| `fallback.h` | 45 | 回退函数声明 + `CONFIG_FW_LOADER_USER_HELPER` / `CONFIG_EFI_EMBEDDED_FIRMWARE` 的 inline 桩 |

### Kconfig 选项

| 选项 | 类型 | 依赖 | 作用 |
|---|---|---|---|
| `CONFIG_FW_LOADER` | tristate | — | 总开关，默认 y |
| `CONFIG_FW_LOADER_PAGED_BUF` | bool | FW_LOADER | 分页缓冲区支持（被 USER_HELPER / COMPRESS / UPLOAD select） |
| `CONFIG_FW_LOADER_SYSFS` | bool | FW_LOADER | sysfs 接口（被 USER_HELPER / UPLOAD select） |
| `CONFIG_EXTRA_FIRMWARE` | string | FW_LOADER | 编译进内核的固件列表（空格分隔） |
| `CONFIG_EXTRA_FIRMWARE_DIR` | string | EXTRA_FIRMWARE≠"" | 固件查找根目录，默认 `/lib/firmware` |
| `CONFIG_FW_LOADER_USER_HELPER` | bool | FW_LOADER | sysfs 回退机制 |
| `CONFIG_FW_LOADER_USER_HELPER_FALLBACK` | bool | USER_HELPER | 强制所有请求走 sysfs 回退 |
| `CONFIG_FW_LOADER_COMPRESS` | bool | FW_LOADER | 压缩固件总开关 |
| `CONFIG_FW_LOADER_COMPRESS_XZ` | bool | COMPRESS | XZ 解压，select `XZ_DEC` |
| `CONFIG_FW_LOADER_COMPRESS_ZSTD` | bool | COMPRESS | ZSTD 解压，select `ZSTD_DECOMPRESS` |
| `CONFIG_FW_CACHE` | bool | PM_SLEEP | 挂起期间固件缓存 |
| `CONFIG_FW_UPLOAD` | bool | FW_LOADER | 用户态发起的设备固件更新 |

### Makefile 编译组合

```
firmware_class-objs := main.o
firmware_class-$(CONFIG_FW_LOADER_USER_HELPER)     += fallback.o
firmware_class-$(CONFIG_EFI_EMBEDDED_FIRMWARE)     += fallback_platform.o
firmware_class-$(CONFIG_FW_LOADER_SYSFS)            += sysfs.o
firmware_class-$(CONFIG_FW_UPLOAD)                  += sysfs_upload.o
obj-$(CONFIG_FW_LOADER_USER_HELPER) += fallback_table.o
obj-y += builtin/
```

`builtin/main.c` 始终编入（`obj-y`），但内部用 `#ifdef CONFIG_FW_LOADER` 守卫。

---

## §1 公开 API

头文件：`include/linux/firmware.h`；实现：`drivers/base/firmware_loader/main.c`。

### §1.1 同步请求

| API | 说明 | 备注 |
|---|---|---|
| `request_firmware(firmware_p, name, device)` | 同步请求固件，等待完成 | opt_flags = `FW_OPT_UEVENT`；裸 `EXPORT_SYMBOL` |
| `firmware_request_nowarn(firmware_p, name, device)` | 同上但不输出警告 | 额外 `FW_OPT_NO_WARN`；`EXPORT_SYMBOL_GPL` |
| `request_firmware_direct(firmware_p, name, device)` | 同上但禁止 sysfs 回退 | 额外 `FW_OPT_NOFALLBACK_SYSFS`；`EXPORT_SYMBOL_GPL` |
| `firmware_request_platform(firmware_p, name, device)` | 同上但增加 EFI 嵌入固件回退 | 额外 `FW_OPT_FALLBACK_PLATFORM`；`EXPORT_SYMBOL_GPL` |
| `request_firmware_into_buf(firmware_p, name, device, buf, size)` | 加载到预分配缓冲区 | 额外 `FW_OPT_NOCACHE`；不缓存；若已设缓存返回 `-EOPNOTSUPP`；裸 `EXPORT_SYMBOL` |
| `request_partial_firmware_into_buf(firmware_p, name, device, buf, size, offset)` | 同上但支持偏移 | 额外 `FW_OPT_PARTIAL`；不参与批处理合并；裸 `EXPORT_SYMBOL` |

### §1.2 异步请求

| API | 说明 | 备注 |
|---|---|---|
| `request_firmware_nowait(module, uevent, name, device, gfp, context, cont)` | 异步请求，完成后回调 | 不可在原子上下文调用；`uevent=false` 时设 `FW_OPT_USERHELPER`（不发 uevent 但走 sysfs 回退）；裸 `EXPORT_SYMBOL` |

### §1.3 释放与缓存

| API | 说明 | 备注 |
|---|---|---|
| `release_firmware(fw)` | 释放固件资源 | 内建固件不调 `firmware_free_data`；裸 `EXPORT_SYMBOL` |
| `firmware_request_cache(device, name)` | 为挂起缓存注册固件名 | 仅添加 devres 条目，不实际加载；`EXPORT_SYMBOL_GPL` |

### §1.4 固件上传（CONFIG_FW_UPLOAD）

头文件：`include/linux/firmware.h`；实现：`drivers/base/firmware_loader/sysfs_upload.c`。

| API | 说明 | 备注 |
|---|---|---|
| `firmware_upload_register(module, parent, name, ops, dd_handle)` | 注册固件上传 sysfs 接口 | `ops` 必须含 `prepare`/`write`/`poll_complete`/`cancel`；返回 `fw_upload *` 或 `ERR_PTR`；`EXPORT_SYMBOL_GPL` |
| `firmware_upload_unregister(fw_upload)` | 注销接口 | 若上传进行中先调 `cancel` 再 `flush_work`；`EXPORT_SYMBOL_GPL` |

### §1.5 内建固件

头文件：`include/linux/firmware.h`（`CONFIG_FW_LOADER` 时声明）；实现：`drivers/base/firmware_loader/builtin/main.c`。

| API | 说明 | 备注 |
|---|---|---|
| `firmware_request_builtin(fw, name)` | 查找内建固件 | 匹配成功直接指向内建数据，不拷贝；`EXPORT_SYMBOL_NS_GPL(TEST_FIRMWARE)`——仅测试模块可用 |
| `firmware_request_builtin_buf(fw, name, buf, size)` | 同上但可拷贝到预分配缓冲区 | 内部 API，仅 `firmware_loader` 自身使用；不导出 |
| `firmware_is_builtin(fw)` | 判断固件是否来自内建 | 比较 `fw->data` 指针与内建段；内部 API |

---

## §2 数据结构速览

详见 `02-datastructures.md`。此处只列核心：

```c
struct firmware {            /* 公开——驱动拿到的就是这个 */
    size_t size;
    const u8 *data;
    void *priv;              /* → struct fw_priv（对驱动不透明） */
};
```

---

## §3 错误码语义

| 错误码 | 含义 | 触发场景 |
|---|---|---|
| `-ENOENT` | 固件未找到 | 所有搜索路径均未命中 + 回退失败 |
| `-EINVAL` | 参数无效 | `firmware_p` 为 NULL、`name` 为空或含 `..`、上传 ops 不完整 |
| `-ENOMEM` | 内存分配失败 | `kzalloc` / `vmalloc` / `vmap` 失败 |
| `-EOPNOTSUPP` | 操作不支持 | `request_firmware_into_buf` 时已有缓存；`request_firmware_nowait` 无 uevent 且已有缓存 |
| `-EFAULT` | 模块引用失败 | `try_module_get` 失败 |
| `-EBUSY` | 资源忙 | 异步请求时 usermode helper 锁超时；上传进行中再次发起 |
| `-EINTR` | 被中断 | sysfs 回退中状态已 abort |
| `-ETIMEDOUT` | 超时 | sysfs 回退等待超时 |
| `-EPERM` | 权限不足 | `firmware_data_write` 无 `CAP_SYS_RAWIO` |
| `-ENODEV` | 设备不存在 | sysfs 读写时 fw_priv 已释放或已完成 |

---

## §4 sysfs 接口

### §4.1 类属性

路径：`/sys/class/firmware/`

| 属性 | 权限 | 说明 | 备注 |
|---|---|---|---|
| `timeout` | RW | 回退等待超时（秒）；0 = 无限等待 | 仅 `CONFIG_FW_LOADER_USER_HELPER` |

### §4.2 设备属性

路径：`/sys/class/firmware/<fw_name>/`（仅在 sysfs 回退激活时创建）

| 属性 | 权限 | 说明 | 备注 |
|---|---|---|---|
| `loading` | RW | 1=开始加载 / 0=完成 / -1=中止 | 写 0 触发 `security_kernel_post_load_data` + `fw_upload_start` |
| `data` | RW (bin) | 固件数据缓冲区 | 写需 `CAP_SYS_RAWIO`；按页增长 |
| `status` | RO | 上传进度：idle/receiving/preparing/transferring/programming | 仅 `CONFIG_FW_UPLOAD` |
| `error` | RO | 错误信息 `<progress>:<error>`；空闲且无错返回 0；进行中返回 `-EBUSY` | 仅 `CONFIG_FW_UPLOAD` |
| `cancel` | WO | 写 1 取消上传 | 仅 `CONFIG_FW_UPLOAD` |
| `remaining_size` | RO | 上传剩余字节数 | 仅 `CONFIG_FW_UPLOAD` |

`status`/`error`/`cancel`/`remaining_size` 通过 `fw_upload_is_visible` 控制可见性——只有注册了 `fw_upload_priv` 的设备才显示。

### §4.3 sysctl

路径：`/proc/sys/kernel/firmware_config/`（需 `CONFIG_SYSCTL` + `CONFIG_FW_LOADER_USER_HELPER`）

| 键 | 类型 | 说明 |
|---|---|---|
| `force_sysfs_fallback` | uint (0/1) | 强制所有请求走 sysfs 回退（等价于 `CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y`） |
| `ignore_sysfs_fallback` | uint (0/1) | 完全禁用 sysfs 回退 |

### §4.4 模块参数

| 参数 | 路径 | 说明 |
|---|---|---|
| `path` | `/sys/module/firmware_class/parameters/path` | 自定义固件搜索路径（优先级最高），最长 256 字符 |

---

## §5 uevent 变量

sysfs 回退创建的设备触发 `KOBJ_ADD` uevent，携带以下环境变量：

| 变量 | 值 |
|---|---|
| `FIRMWARE` | 固件名称 |
| `TIMEOUT` | 当前超时值（秒） |
| `ASYNC` | 0 或 1（是否异步请求） |
| `DEVPATH` | 由 kobject 基础设施自动设置 |
