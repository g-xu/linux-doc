---
title: 内部实现
---

# Firmware Loader · 内部机制

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。

## §0 导航

本篇三条轴：

- **Part A**：时间线——一次固件请求从调用到返回的完整路径
- **Part B**：横切——批处理、PM 缓存、安全机制、异步请求
- **Part C**：评价——设计债与 REVISIT

```
Part A 时间线
─────────────────────────────────────────────────────
§A1 入口验证与准备    main.c:815-901  (_request_firmware)
§A2 内建固件查找      builtin/main.c:47-63
§A3 批处理查找/分配   main.c:167-200  (alloc_lookup_fw_priv)
§A4 文件系统直接加载  main.c:487-580  (fw_get_filesystem_firmware)
§A5 压缩固件解压      main.c:309-467
§A6 平台固件回退      fallback_platform.c:11-45
§A7 sysfs 回退        fallback.c:70-235
§A8 最终赋值          main.c:681-721  (assign_fw)
§A9 拆除路径          main.c:779-792  (fw_abort_batch_reqs)

Part B 横切
─────────────────────────────────────────────────────
§B1 批处理请求序列化
§B2 PM 缓存（suspend/resume）
§B3 安全机制
§B4 异步请求（request_firmware_nowait）
§B5 固件上传工作线程

Part C 评价
─────────────────────────────────────────────────────
§C1 设计债
```

不在本篇的内容：

| 内容 | 去处 |
|---|---|
| API 签名与参数 | `01-api.md` |
| 结构体字段详解 | `02-datastructures.md` |
| 子框架详解（压缩/上传/缓存） | `04-extensions.md` |
| 配置机制 | `06-devicetree.md` |
| 调试手段 | `07-debug.md` |

---

## Part A · 固件请求时间线

### §A1 入口验证与准备（`main.c:815-901`）

`_request_firmware()` 是所有同步/异步请求的汇聚点。六个公开 API 最终都调它。

**执行步骤**：

1. **参数校验**（`main.c:826-841`）：`firmware_p` 非 NULL、`name` 非空、不含 `..` 路径组件
2. **凭证切换**（`main.c:852-857`）：`prepare_kernel_cred(NULL)` + `override_creds()`——用内核凭证读文件，防止用户态凭证影响固件访问
3. **按序尝试加载**（`main.c:859-886`）：直接 → ZSTD → XZ → 平台回退 → sysfs 回退
4. **凭证恢复**（`main.c:889-890`）：`revert_creds()` + `put_cred()`
5. **错误清理**（`main.c:893-897`）：失败时 `fw_abort_batch_reqs` + `release_firmware`

`name_contains_dotdot()`（`main.c:805-812`）检查四种 `..` 形态：纯 `..`、`../` 开头、`/..//` 中间、`/..` 结尾。这是安全边界——防止设备提供的字符串引导内核读取任意文件。

### §A2 内建固件查找（`builtin/main.c:47-63`）

`_request_firmware_prepare()`（`main.c:728-768`）在分配 `fw_priv` 之前先查内建固件：

```c
if (firmware_request_builtin_buf(firmware, name, dbuf, size)) {
    dev_dbg(device, "using built-in %s\n", name);
    return 0; /* assigned */
}
```

`firmware_request_builtin()`（`builtin/main.c:47-63`）遍历 `__start_builtin_fw[]` ~ `__end_builtin_fw[]` 段，逐条 `strcmp` 匹配。匹配成功直接设置 `fw->data` 和 `fw->size` 指向内建数据——**零拷贝**。

内建固件由 `CONFIG_EXTRA_FIRMWARE` 在编译时通过 `builtin/main.c` 的 Makefile 规则生成 `.section .builtin_fw` 数据。

**返回值语义**：`_request_firmware_prepare` 返回 0 表示已赋值（内建或批处理命中），返回 1 表示需要加载，返回负值为错误。

### §A3 批处理查找/分配（`main.c:167-200`）

`alloc_lookup_fw_priv()` 在 `fw_cache.lock`（spinlock）保护下执行：

1. **查找**（`main.c:178-187`）：如果请求不带 `NOCACHE` 和 `PARTIAL` 标志，在 `fw_cache.head` 链表中按名字查找。命中则 `kref_get` 增加引用计数，返回 1（批处理命中）
2. **分配**（`main.c:189-199`）：未命中则 `__allocate_fw_priv` 创建新的 `fw_priv`，挂入链表（除非 `NOCACHE`）

`__allocate_fw_priv()`（`main.c:112-153`）做以下初始化：
- `kstrdup_const(fw_name)` 拷贝名字
- `kref_init` 初始化引用计数为 1
- 设置 `data = dbuf`、`allocated_size = size`、`offset = offset`
- `fw_state_init` → 状态设为 `UNKNOWN`，初始化 completion
- `CONFIG_FW_LOADER_USER_HELPER` 时初始化 `pending_list`

**批处理的关键**：第二个请求者拿到同一个 `fw_priv` 后，`_request_firmware_prepare` 调 `fw_state_wait`（`main.c:758`）阻塞等待第一个请求者完成。完成后 `fw_set_page_data` 把数据指针设到第二个请求者的 `struct firmware` 里。

### §A4 文件系统直接加载（`main.c:487-580`）

`fw_get_filesystem_firmware()` 遍历 `fw_path[]`（`main.c:471-477`）：

```c
static const char * const fw_path[] = {
    fw_path_para,                                    /* 模块参数，默认空 */
    "/lib/firmware/updates/" UTS_RELEASE,
    "/lib/firmware/updates",
    "/lib/firmware/" UTS_RELEASE,
    "/lib/firmware"
};
```

每个路径拼接 `fw_name + suffix`（suffix 为空串 / `.zst` / `.xz`），调 `kernel_read_file_from_path_initns()`（`main.c:539-542`）读取。

**关键细节**：

- 使用 `kernel_read_file_from_path_initns`（不是 `kernel_read_file_from_path`）——从 init 的挂载命名空间读取，确保在 switch_root 之前也能找到 initramfs 中的固件
- `__getname()` / `__putname()` 获取临时路径缓冲区（`PATH_MAX` 大小）
- `wait_for_initramfs()`（`main.c:512`）确保 initramfs 已可用
- 成功时设置 `fw_priv->data` 和 `fw_priv->size`，调 `fw_state_done`
- 如果 `decompress` 回调非 NULL，解压后 `vfree(buffer)` 释放原始压缩数据

### §A5 压缩固件解压（`main.c:309-467`）

压缩是文件系统加载的**备选**——只在原始文件返回 `-ENOENT` 时尝试。

**ZSTD 路径**（`main.c:309-367`，需 `CONFIG_FW_LOADER_COMPRESS_ZSTD`）：

1. 如果 `allocated_size > 0`（into_buf 场景），直接解压到调用者缓冲区
2. 否则从 ZSTD 帧头读取 `frameContentSize`，`vzalloc` 分配输出缓冲区
3. `zstd_init_dctx` + `zstd_decompress_dctx` 一次性解压
4. 帧头缺失或内容大小未知时返回 `-EINVAL`

**XZ 路径**（`main.c:373-467`，需 `CONFIG_FW_LOADER_COMPRESS_XZ`）：

两种模式：
- **单发**（`fw_decompress_xz_single`，`main.c:385-408`）：`fw_priv->data` 已存在时，`XZ_SINGLE` 模式一次性解压
- **分页**（`fw_decompress_xz_pages`，`main.c:411-456`）：`fw_priv->data` 为空时，`XZ_DYNALLOC` 模式逐页解压，每页 `kmap_local_page` 映射后写入，最终 `vmap` 映射所有页面

分页模式的循环（`main.c:430-447`）：每次 `fw_grow_paged_buf` 确保有足够页面 → 解压到最后一页 → 如果该页没写满（`out_pos != PAGE_SIZE`）则解压完成 → 否则继续分配下一页。

### §A6 平台固件回退（`fallback_platform.c:11-45`）

仅在 `CONFIG_EFI_EMBEDDED_FIRMWARE` 且请求带 `FW_OPT_FALLBACK_PLATFORM` 时生效。

**arm64 相关性**：EFI 嵌入固件目前**仅 x86 可用**——其他架构在 EFI embedded-fw 代码扫描前就释放了 `EFI_BOOT_SERVICES_CODE`。`fallback_platform.c` 在 arm64 上编译但 `efi_get_embedded_fw()` 始终返回 `-ENOENT`。

流程：安全检查 → `efi_get_embedded_fw` 查找 → 大小校验 → `security_kernel_post_load_data` → 拷贝到 `fw_priv->data`（`vmalloc` 或直接使用预分配缓冲区）。

### §A7 sysfs 回退（`fallback.c:70-235`）

仅在 `CONFIG_FW_LOADER_USER_HELPER` 时可用。

**入口判断**（`fallback.c:178-196`）：`fw_run_sysfs_fallback()` 依次检查：
1. `ignore_sysfs_fallback` sysctl → 禁用则返回 false
2. `FW_OPT_NOFALLBACK_SYSFS` 标志 → 设置则返回 false
3. `security_kernel_load_data(LOADING_FIRMWARE, true)` → LSM 拒绝则返回 false
4. `force_sysfs_fallback` sysctl 或 `FW_OPT_USERHELPER` 标志 → 至少一个为真才返回 true

**加载流程**（`fw_load_sysfs_fallback`，`fallback.c:70-124`）：

1. `fw_create_instance` 创建 `fw_sysfs` 设备
2. `dev_set_uevent_suppress(f_dev, true)` 先抑制 uevent
3. `device_add(f_dev)` 注册到 sysfs
4. 检查状态是否已被 abort
5. 加入 `pending_fw_head` 链表
6. 如果 `FW_OPT_UEVENT`：取消 uevent 抑制 → `kobject_uevent(KOBJ_ADD)` 通知用户空间
7. `fw_sysfs_wait_timeout` 等待用户空间写入 `loading=0` 或超时
8. 成功则 `assign_fw`，失败则 `fw_load_abort`
9. `device_del` + `put_device` 清理

**超时管理**：默认 60 秒（`fw_fallback_config.loading_timeout`）；缓存场景缩短到 10 秒（`fw_fallback_set_cache_timeout`，`fallback.c:24-28`）；无 uevent 时用 `MAX_JIFFY_OFFSET`（无限等待）。

### §A8 最终赋值（`main.c:681-721`）

`assign_fw()` 在 `fw_lock`（mutex）保护下执行：

1. 检查 `fw_priv->size` 非零且状态非 ABORTED
2. `fw_add_devm_name`：如果带 `UEVENT` 且非 `NOCACHE`，把固件名加入设备 devres（为 PM 缓存做准备）
3. `fw_cache_piggyback_on_request`：如果处于缓存模式，把固件名加入 `fw_cache.fw_names`
4. `fw_set_page_data`：把 `fw_priv` 的数据指针设到 `struct firmware` 里

### §A9 拆除路径

**正常释放**：`release_firmware()`（`main.c:1107-1114`）→ `firmware_free_data()` → `free_fw_priv()` → `kref_put` → `__free_fw_priv()`（`main.c:202-222`）。

`__free_fw_priv` 在 `fwc->lock` 保护下从链表删除 `fw_priv`，然后：
- 分页缓冲 → `fw_free_paged_buf`（vunmap + 逐页释放）
- 非预分配 → `vfree(data)`
- 预分配 → 不释放（调用者负责）
- 释放 `fw_name` 和 `fw_priv` 本身

**异常中止**：`fw_abort_batch_reqs()`（`main.c:779-792`）在 `fw_lock` 保护下设 `FW_STATUS_ABORTED`，唤醒所有等待者。

**关机/挂起清理**：`kill_pending_fw_fallback_reqs()`（`fallback.c:49-61`）遍历 `pending_fw_head`，逐个 abort。关机时杀全部（`only_kill_custom=false`），挂起时只杀无 uevent 的（`only_kill_custom=true`）。

---

## Part B · 横切机制

### §B1 批处理请求序列化

同一固件名的并发请求共享一个 `fw_priv`。机制：

1. 第一个请求者 `alloc_lookup_fw_priv` 创建 `fw_priv` 并加入 `fw_cache.head`
2. 后续请求者查到同一个 `fw_priv`，`kref_get` 增加引用
3. 后续请求者在 `_request_firmware_prepare` 中调 `fw_state_wait`（`main.c:758`）阻塞
4. 第一个请求者完成后 `fw_state_done` → `complete_all` 唤醒所有等待者
5. 所有请求者共享同一份数据——`fw_set_page_data` 把相同指针设到各自的 `struct firmware`

**限制**：`FW_OPT_NOCACHE` 和 `FW_OPT_PARTIAL` 请求不参与批处理（`main.c:178`）。

### §B2 PM 缓存（`CONFIG_FW_CACHE`）

**挂起前**（`fw_pm_notify`，`main.c:1494-1525`，`PM_SUSPEND_PREPARE` 等）：

1. `kill_pending_fw_fallback_reqs(true)` 杀掉无 uevent 的回退请求
2. `device_cache_fw_images()`（`main.c:1441-1462`）：
   - 缩短超时到 10 秒
   - 设 `fwc->state = FW_LOADER_START_CACHE`
   - `dpm_for_each_dev` 遍历所有设备，对每个有 devres 固件名的设备异步调 `cache_firmware`
   - `async_synchronize_full_domain` 等待所有缓存完成
   - 恢复超时

**恢复后**（`PM_POST_SUSPEND` 等）：

1. 重置 `fwc->state = FW_LOADER_NO_CACHE`
2. 10 秒延迟后 `device_uncache_fw_images` 释放缓存

**syscore_suspend**（`main.c:1528-1532`）：在 syscore 阶段再次设 `FW_LOADER_NO_CACHE`——这是最后的安全网。

**缓存生效条件**：
- 同步请求（除 `into_buf`）自动通过 `assign_fw` 中的 `fw_add_devm_name` 注册
- 异步请求仅在 `uevent=true` 时注册
- `into_buf` 和 `nowait(uevent=false)` 不缓存——驱动需自行处理

### §B3 安全机制

| 检查点 | 位置 | 说明 |
|---|---|---|
| `name_contains_dotdot` | `main.c:805-812` | 拒绝含 `..` 的固件名 |
| `prepare_kernel_cred` | `main.c:852-857` | 用内核凭证读文件 |
| `security_kernel_load_data` | `fallback.c:191`、`fallback_platform.c:20` | LSM 检查（IMA 等） |
| `security_kernel_post_load_data` | `sysfs.c:188-191`、`fallback_platform.c:31-33` | 数据加载后 LSM 检查 |
| `CAP_SYS_RAWIO` | `sysfs.c:327` | sysfs data 写入需 root 能力 |

### §B4 异步请求（`main.c:1117-1209`）

`request_firmware_nowait()` 分配 `struct firmware_work`，填入参数后 `schedule_work` 到系统工作队列。

`request_firmware_work_func()`（`main.c:1128-1143`）在工作队列上下文中调 `_request_firmware`，完成后调用户回调 `cont(fw, context)`。

**注意**：
- `try_module_get(module)` 防止模块在异步请求期间被卸载
- `get_device(device)` 防止设备在异步请求期间被移除
- `uevent=false` 时设 `FW_OPT_USERHELPER`（不发 uevent 但走 sysfs 回退）——此时如果已有缓存返回 `-EOPNOTSUPP`

### §B5 固件上传工作线程（`sysfs_upload.c:158-219`）

用户写 `loading=0` 触发 `fw_upload_start()`（`sysfs_upload.c:225-265`）→ `queue_work(system_long_wq, &fwlp->work)` → `fw_upload_main()`。

`fw_upload_main` 流程：

1. `PREPARING` → `ops->prepare(fwl, data, remaining_size)`
2. `TRANSFERRING` → 循环调 `ops->write(fwl, data, offset, remaining_size, &written)` 直到 `remaining_size` 归零
3. `PROGRAMMING` → `ops->poll_complete(fwl)` 等待硬件完成
4. `ops->cleanup(fwl)`（如果提供）
5. `put_device(fw_dev->parent)` 释放引用
6. 清理 `fw_priv`、重置状态、设 `progress = IDLE`

**错误处理**：任何步骤返回非 `FW_UPLOAD_ERR_NONE` 时记录 `err_progress` + `err_code`，跳到 cleanup。`remaining_size` 保留不修改以便诊断。

**竞争保护**：`fwlp->lock`（mutex）保护 `progress` 状态转换。`fw_upload_start` 检查 `progress != IDLE` 时返回 `-EBUSY`。

---

## Part C · 评价

### §C1 设计债

| 问题 | 位置 | 说明 |
|---|---|---|
| 模块名不可改 | `sysfs.c:102` | `firmware_class` 类名与 ABI 绑定，历史原因无法重命名 |
| sysfs 回退已过时 | Kconfig help | udev 自 2014 年起不再监听固件 uevent，但机制无法移除 |
| `fw_path_para` 长度硬编码 | `main.c:470` | 256 字节固定缓冲区，无溢出风险但限制灵活性 |
| 缓存超时硬编码 | `fallback.c:27` | 缓存场景 10 秒超时写死在代码里 |
| `force_sysfs_fallback` 与 `CONFIG_FW_LOADER_USER_HELPER_FALLBACK` 功能重复 | `fallback_table.c:21` | sysctl 运行时开关与编译时开关做同一件事 |
| 内建固件不支持压缩 | Kconfig help | `CONFIG_EXTRA_FIRMWARE` 明确不支持压缩文件 |
