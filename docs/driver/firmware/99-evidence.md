---
title: 取证附录
---

# Firmware Loader · 取证附录

> 本篇记录文档集的取证过程：做了什么、裁了什么、为什么裁、怎么复核。
> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。

## §1 子系统类型判定

### §1.1 门 1：bus_type 扫描

```bash
grep -rn 'struct bus_type' drivers/base/firmware_loader/ include/linux/firmware.h
```

**结果：0 处命中。** firmware_loader 不定义自己的 `bus_type`。它注册的是 `struct class firmware_class`（`sysfs.c:102`）。

### §1.2 门 2：uapi 扫描

```bash
ls include/uapi/linux/firmware*
```

**结果：0 个文件。** firmware_loader 没有用户态 ABI 头文件。`include/linux/firmware.h` 是内核内部头文件。

### §1.3 类型判定

**框架型**。理由：

- 没有 `bus_type`（门 1 零命中）
- 没有 uapi 头（门 2 零命中）
- 通过 API（`request_firmware` 系列）被其他驱动消费
- 提供 sysfs 接口但不是设备类 sysfs——是加载辅助接口

---

## §2 裁掉的内容

### §2.1 `drivers/firmware/` 目录

`drivers/firmware/` 包含 ARM 固件接口（PSCI、SCMI、SCMI、TEE 等），与 `drivers/base/firmware_loader/` 是完全不同的子系统。本套文档只覆盖后者。

### §2.2 EFI 嵌入固件路径

`fallback_platform.c` 在 arm64 上编译但 `efi_get_embedded_fw()` 始终返回 `-ENOENT`——其他架构在 EFI embedded-fw 代码扫描前就释放了 `EFI_BOOT_SERVICES_CODE`。04 §4 记录了这个事实，不展开分析。

### §2.3 x86 特定代码

- `arch/x86/` 下的微码加载器等不在本套范围
- `drivers/firmware/efi/embedded-firmware.c` 是 x86-only

---

## §3 官方文档阅读记录

| 文档 | 行数 | 是否读完 | 备注 |
|---|---|---|---|
| `Documentation/driver-api/firmware/index.rst` | 17 | 是 | 目录索引 |
| `Documentation/driver-api/firmware/introduction.rst` | 28 | 是 | 概述：同步/异步两类请求 |
| `Documentation/driver-api/firmware/core.rst` | 17 | 是 | 子目录索引 |
| `Documentation/driver-api/firmware/request_firmware.rst` | 81 | 是 | API kerneldoc 引用 |
| `Documentation/driver-api/firmware/lookup-order.rst` | 21 | 是 | 查找顺序：内建→缓存→文件系统→平台→sysfs |
| `Documentation/driver-api/firmware/direct-fs-lookup.rst` | 31 | 是 | 文件系统查找 + initramfs 关系 |
| `Documentation/driver-api/firmware/fallback-mechanisms.rst` | 309 | 是 | sysfs 回退完整文档 |
| `Documentation/driver-api/firmware/fw_upload.rst` | 127 | 是 | 上传 API 文档 |
| `Documentation/driver-api/firmware/firmware_cache.rst` | 52 | 是 | PM 缓存机制 |
| `Documentation/driver-api/firmware/fw_search_path.rst` | 27 | 是 | 搜索路径 |
| `Documentation/driver-api/firmware/built-in-fw.rst` | 34 | 是 | 内建固件 |
| `Documentation/driver-api/firmware/firmware-usage-guidelines.rst` | 45 | 是 | 固件版本管理指南 |
| `Documentation/driver-api/firmware/other_interfaces.rst` | 52 | 部分 | DMI/EDD/Stratix10——不属于 firmware_loader |

---

## §4 导出符号清单

| 文件 | 符号 | 宏 |
|---|---|---|
| `main.c:938` | `request_firmware` | `EXPORT_SYMBOL`（裸） |
| `main.c:965` | `firmware_request_nowarn` | `EXPORT_SYMBOL_GPL` |
| `main.c:990` | `request_firmware_direct` | `EXPORT_SYMBOL_GPL` |
| `main.c:1014` | `firmware_request_platform` | `EXPORT_SYMBOL_GPL` |
| `main.c:1038` | `firmware_request_cache` | `EXPORT_SYMBOL_GPL` |
| `main.c:1070` | `request_firmware_into_buf` | `EXPORT_SYMBOL`（裸） |
| `main.c:1101` | `request_partial_firmware_into_buf` | `EXPORT_SYMBOL`（裸） |
| `main.c:1115` | `release_firmware` | `EXPORT_SYMBOL`（裸） |
| `main.c:1209` | `request_firmware_nowait` | `EXPORT_SYMBOL`（裸） |
| `sysfs_upload.c:379` | `firmware_upload_register` | `EXPORT_SYMBOL_GPL` |
| `sysfs_upload.c:407` | `firmware_upload_unregister` | `EXPORT_SYMBOL_GPL` |
| `builtin/main.c:64` | `firmware_request_builtin` | `EXPORT_SYMBOL_NS_GPL(TEST_FIRMWARE)` |
| `fallback_table.c:25` | `fw_fallback_config` | `EXPORT_SYMBOL_NS_GPL(FIRMWARE_LOADER_PRIVATE)` |
| `fallback_table.c:60` | `register_firmware_config_sysctl` | `EXPORT_SYMBOL_NS_GPL(FIRMWARE_LOADER_PRIVATE)` |
| `fallback_table.c:67` | `unregister_firmware_config_sysctl` | `EXPORT_SYMBOL_NS_GPL(FIRMWARE_LOADER_PRIVATE)` |

合计 **15 个**：5 个裸 `EXPORT_SYMBOL`，6 个 `EXPORT_SYMBOL_GPL`，1 个 `EXPORT_SYMBOL_NS_GPL(TEST_FIRMWARE)`，3 个 `EXPORT_SYMBOL_NS_GPL(FIRMWARE_LOADER_PRIVATE)`。

裸 `EXPORT_SYMBOL` 集中在公开 API（`request_firmware`、`release_firmware` 等）——允许非 GPL 模块使用固件加载。

---

## §5 initcall 复核

```bash
grep -n 'initcall\|module_init\|module_exit' drivers/base/firmware_loader/main.c
```

- `main.c:1628`：`fs_initcall(firmware_class_init)` — 非常早的 initcall（level 1），在 `rootfs_initcall` 之前
- `main.c:1629`：`module_exit(firmware_class_exit)`

`firmware_class_init()`（`main.c:1599-1619`）做三件事：
1. `fw_cache_init()` — 初始化缓存链表
2. `register_fw_pm_ops()` — 注册 PM 通知器 + syscore ops
3. `register_reboot_notifier()` — 注册关机通知器
4. `register_sysfs_loader()` — 注册 firmware_class + sysctl

---

## §6 跨篇引用登记

| 源篇 | 引用目标 | 内容 |
|---|---|---|
| 00 §4 | 02 | 数据结构速览引用详解 |
| 00 §5 | 03, 04 | 机制速览引用内部机制和子框架 |
| 00 §8 | 01-08, 99 | 导读导航 |
| 01 §2 | 02 | 数据结构引用 |
| 02 §1.0 | 01 | API 引用 |
| 03 §0 | 01, 02, 04, 06, 07 | 导航引用多篇 |
| 04 §0 | 03 | 挂载点引用时间线 |
| 05 §5 | 01 | API 对比引用 |
| 06 §4 | 07 | 错误消息引用调试 |
| 07 §4 | 03 | 错误码引用内部路径 |
| 08 全篇 | 02-04 | 费曼版简化引用 |

---

## §7 已知限制

1. **行号未逐条复核**：行号来自基线收集阶段的 grep 结果。如果源码有改动可能偏移。
2. **消费者统计为粗 grep**：`firmware_upload_register` 的消费者数需要全树 grep 确认。
3. **EFI 嵌入固件分析深度有限**：arm64 不可用，只记录了事实没有深入分析 x86 路径。
4. **无 tracepoint**：firmware_loader 没有 tracepoint——这不是遗漏而是确认结果。
5. **无故障注入**：同上——确认子系统没有内建故障注入机制。
6. **`builtin/Makefile` 未读**：内建固件的编译规则（`.section .builtin_fw` 生成）未详细分析。

---

## §8 逐篇取证表

| 篇 | 论断 | 出处 | 证据等级 |
|---|---|---|---|
| 01 | 9 个公开 API + 2 个上传 API | `grep EXPORT_SYMBOL` | 一等 |
| 01 | `fs_initcall` | `main.c:1628` | 一等 |
| 01 | 5 个搜索路径 | `main.c:471-477` | 一等 |
| 02 | `struct fw_priv` 字段 | `firmware.h:66-87` | 一等 |
| 02 | 8 个 opt_flags | `firmware.h:38-47` | 一等 |
| 03 | 加载管线顺序 | `main.c:859-886` | 一等 |
| 03 | `name_contains_dotdot` 四种形态 | `main.c:805-812` | 一等 |
| 03 | 凭证切换 | `main.c:852-857` | 一等 |
| 04 | EFI 嵌入固件 arm64 不可用 | `Documentation/driver-api/firmware/fallback-mechanisms.rst:264-266` | 三等（文档）+ 代码确认 |
| 04 | udev 2014 年移除固件支持 | `Kconfig:140-141` 引用 systemd commit | 三等 |
| 05 | 骨架未编译 | 声明 | N/A |
| 06 | 无 DT 绑定 | `ls Documentation/devicetree/bindings/firmware/` 无 firmware_loader 相关 | 一等 |
| 07 | 无 tracepoint | `grep -r TRACE_EVENT drivers/base/firmware_loader/` 零命中 | 一等 |
| 07 | 无故障注入 | `grep -r fail_ drivers/base/firmware_loader/` 零命中 | 一等 |
| 08 | 批处理共享内存 | `main.c:178-187` + `fw_set_page_data` | 一等 |

---

## §9 未验证项分层

### 第一层：未编译未运行

所有代码描述均为静态分析。未编译内核验证运行时行为。

### 第二层：只读了符号表

- `builtin/Makefile`：知道它生成 `.builtin_fw` 段但未读具体规则
- `kernel_read_file_from_path_initns`：只知其名和调用位置，未读实现
- `vmap` / `vunmap` 在分页缓冲中的行为：基于 API 契约推断

### 第三层：推断非观察

- "大多数发行版禁用 `CONFIG_FW_LOADER_USER_HELPER_FALLBACK`"——来自 Kconfig help 文字，非实际调查
- "两个驱动依赖 sysfs 回退"——来自 Kconfig help（`LEDS_LP55XX_COMMON`、`DELL_RBU`），未逐一验证

### 第四层：版本时效

- udev 固件加载移除时间（2014, systemd v217）——来自 Kconfig 引用
- `firmware_class` 类名不可改——来自 `Documentation/driver-api/firmware/fallback-mechanisms.rst:79-81`
