---
title: 总览
---

# Firmware Loader · 总览

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 子系统类型：**框架型**——无 `bus_type`，通过 API 被其他驱动消费。

## §1 定位

firmware_loader 是内核的**固件加载框架**。它提供统一的 API 让设备驱动请求固件文件，处理搜索路径、缓存、压缩解压、sysfs 回退、安全检查和 PM 休眠恢复等复杂性。

核心 API 入口：`request_firmware()`。驱动传一个名字，框架返回一份数据。

代码住在 `drivers/base/firmware_loader/`（3516 行），公开头文件 `include/linux/firmware.h`（199 行）。

## §2 问题背景

设备驱动经常需要加载固件——CPU 微码、WiFi 校准数据、FPGA 比特流、DSP 代码。这些文件：

- 不能编进内核（版权、大小、可更新性）
- 存放在根文件系统的 `/lib/firmware/` 下
- 可能在 boot 早期就需要（initramfs 场景）
- 可能在 suspend/resume 时需要（根文件系统暂时不可用）
- 可能很大需要分页缓冲
- 可能被压缩以节省存储空间

firmware_loader 统一处理这些复杂性，让驱动只需调一个函数。

**历史**：最初固件加载完全靠 sysfs 回退——内核创建 sysfs 节点，用户空间（udev/hotplug）写入数据。2010 年代后改为文件系统直接查找为主、sysfs 回退为辅。udev 自 2014 年起不再监听固件 uevent，但 sysfs 回退机制因向后兼容无法移除。

## §3 概念模型

```
驱动                    firmware_loader                    数据源
─────                   ───────────────                    ──────
request_firmware()  ──→ 内建固件查找 ──→ __start_builtin_fw[]
                      批处理查找   ──→ fw_cache.head (已有请求)
                      文件系统查找 ──→ /lib/firmware/ (5 个路径)
                      ZSTD 解压    ──→ .zst 文件
                      XZ 解压      ──→ .xz 文件
                      平台回退     ──→ EFI 嵌入固件 (arm64 不可用)
                      sysfs 回退   ──→ 用户空间写入
                   ←── struct firmware { data, size }
release_firmware()  ──→ kref 递减 → 最后释放者释放内存
```

## §4 数据结构速览

| 结构体 | 可见性 | 职责 |
|---|---|---|
| `struct firmware` | 公开 | 驱动拿到的句柄：`size` + `data` + `priv` |
| `struct fw_priv` | 私有 | 框架内部实体：引用计数、状态机、数据、链表 |
| `struct fw_state` | 私有 | 状态机：UNKNOWN → LOADING → DONE/ABORTED |
| `struct firmware_cache` | 全局 | fw_priv 链表 + PM 缓存名链表 |
| `struct fw_sysfs` | 内部 | sysfs 回退设备 |
| `struct fw_upload` | 公开 | 上传设备句柄 |
| `struct fw_upload_ops` | 公开 | 上传回调：prepare/write/poll_complete/cancel/cleanup |

详见 `02-datastructures.md`。

## §5 机制速览

| 机制 | 触发条件 | 关键文件 |
|---|---|---|
| 文件系统直接查找 | 所有请求 | `main.c` `fw_get_filesystem_firmware()` |
| 压缩解压 | 原始文件不存在 | `main.c` `fw_decompress_xz/zstd()` |
| 批处理合并 | 同名并发请求 | `main.c` `alloc_lookup_fw_priv()` |
| sysfs 回退 | 所有查找失败 + 条件满足 | `fallback.c` `firmware_fallback_sysfs()` |
| PM 缓存 | suspend 前自动 | `main.c` `device_cache_fw_images()` |
| 固件上传 | 用户空间写 sysfs | `sysfs_upload.c` `fw_upload_main()` |
| 安全检查 | 所有路径 | `name_contains_dotdot` + LSM 钩子 + CAP |

详见 `03-internals.md`、`04-extensions.md`。

## §6 端到端调用路径

**同步请求（最常见）**：

```
request_firmware()                    main.c:925
  → _request_firmware()               main.c:815
    → name_contains_dotdot()          main.c:834
    → _request_firmware_prepare()     main.c:728
      → firmware_request_builtin_buf() builtin/main.c:86
      → alloc_lookup_fw_priv()        main.c:167
    → prepare_kernel_cred()           main.c:852
    → fw_get_filesystem_firmware()    main.c:859
      → kernel_read_file_from_path_initns()  main.c:539
    → [可选] fw_decompress_zstd/xz()  main.c:867-873
    → [可选] firmware_fallback_platform() main.c:876
    → [可选] firmware_fallback_sysfs() main.c:884
    → assign_fw()                     main.c:887
    → revert_creds()                  main.c:889
release_firmware()                    main.c:1107
  → firmware_free_data()              main.c:583
    → free_fw_priv()                  main.c:224
      → __free_fw_priv()              main.c:202
```

## §7 子系统关系

```
┌─────────────────────────────────────────────────┐
│                 设备驱动消费者                    │
│  (WiFi, GPU, USB, MMC, remoteproc, FPGA, ...)   │
└───────────────────────┬─────────────────────────┘
                        │ request_firmware()
                        ▼
┌─────────────────────────────────────────────────┐
│              firmware_loader 框架                │
│  main.c · fallback.c · sysfs.c · sysfs_upload.c │
│  builtin/main.c · fallback_platform.c           │
└───┬──────────┬──────────┬──────────┬────────────┘
    │          │          │          │
    ▼          ▼          ▼          ▼
 文件系统    压缩解压    sysfs     设备固件
 /lib/      XZ/ZSTD    回退接口   上传接口
 firmware/
```

与 PM 子系统的交互：通过 `register_pm_notifier` 在 suspend/resume 时缓存/释放固件。

与安全子系统的交互：`security_kernel_load_data` / `security_kernel_post_load_data` LSM 钩子。

## §8 导读

| 你想做什么 | 读哪篇 |
|---|---|
| 快速恢复全貌（隔了几个月回来） | 本篇（00） |
| 找 API 签名、代码地图 | `01-api.md` |
| 理解数据结构和结构关系 | `02-datastructures.md` |
| 理解加载管线和内部流程 | `03-internals.md` |
| 了解子框架（压缩/上传/缓存） | `04-extensions.md` |
| 写代码——驱动请求固件 / 提供上传 | `05-usage.md` |
| 配置搜索路径、sysctl、sysfs | `06-devicetree.md` |
| 调试固件加载失败 | `07-debug.md` |
| 用大白话理解某个概念 | `08-feynman.md` |
| 查证论断来源、复核方法 | `99-evidence.md` |
