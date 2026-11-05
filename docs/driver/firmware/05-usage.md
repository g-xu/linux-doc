---
title: 使用指南
---

# Firmware Loader · 使用示例

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> ⚠️ 以下骨架**未编译**，仅供理解 API 用法。

## §0 角色枚举

| 角色 | 说明 | 章节 |
|---|---|---|
| 驱动请求固件（同步） | 最常见的消费者 | §1 |
| 驱动请求固件（异步） | probe 中不能阻塞的场景 | §2 |
| 驱动加载到预分配缓冲区 | DMA 缓冲区等场景 | §3 |
| 驱动提供固件上传 | FPGA 等设备固件更新 | §4 |
| 近邻符号区分 | 容易混淆的 API 对 | §5 |

---

## §1 同步请求固件

```c
#include <linux/firmware.h>
#include <linux/device.h>
#include <linux/module.h>

static int my_driver_probe(struct device *dev)
{
    const struct firmware *fw;
    int ret;

    ret = request_firmware(&fw, "my_device_fw.bin", dev);
    if (ret) {
        dev_err(dev, "firmware request failed: %d\n", ret);
        return ret;
    }

    /* fw->data 指向固件内容，fw->size 是大小 */
    ret = upload_to_device(fw->data, fw->size);

    release_firmware(fw);
    return ret;
}
```

**要点**：
- `request_firmware` 可能睡眠——只能在进程上下文调用
- 失败时 `fw` 被设为 NULL，不要尝试释放
- 成功时必须调 `release_firmware`——即使你不需要数据了
- 固件名不含 `..` 路径组件——否则返回 `-EINVAL`

### 可选固件

```c
ret = request_firmware_direct(&fw, "my_optional.bin", dev);
if (ret == -ENOENT) {
    /* 固件不存在，功能降级但不报错 */
    return 0;
}
```

`request_firmware_direct` 不走 sysfs 回退——找不到就立即返回 `-ENOENT`，不会等 60 秒超时。

### 静默请求

```c
ret = firmware_request_nowarn(&fw, "my_maybe_fw.bin", dev);
```

与 `request_firmware` 相同但不输出 `dev_warn`——适合"有就用、没有也行"的场景。

---

## §2 异步请求固件

```c
static void my_fw_cb(const struct firmware *fw, void *context)
{
    struct my_device *mydev = context;

    if (!fw) {
        dev_err(mydev->dev, "firmware load failed\n");
        return;
    }

    upload_to_device(fw->data, fw->size);
    release_firmware(fw);
}

static int my_driver_probe(struct platform_device *pdev)
{
    struct my_device *mydev;
    int ret;

    mydev = devm_kzalloc(&pdev->dev, sizeof(*mydev), GFP_KERNEL);
    if (!mydev)
        return -ENOMEM;
    mydev->dev = &pdev->dev;

    ret = request_firmware_nowait(THIS_MODULE, true,
                                  "my_device_fw.bin",
                                  &pdev->dev, GFP_KERNEL,
                                  mydev, my_fw_cb);
    if (ret)
        return ret;

    /* probe 立即返回——回调在系统工作队列中执行 */
    return 0;
}
```

**要点**：
- 回调中 `fw` 可能为 NULL（加载失败）
- `uevent=true` 允许 sysfs 回退；`uevent=false` 不发 uevent
- `request_firmware_nowait` 不可在原子上下文调用（内部 `kzalloc` 用 `gfp` 参数）
- 回调中必须调 `release_firmware`

---

## §3 加载到预分配缓冲区

```c
static int my_driver_probe(struct device *dev)
{
    const struct firmware *fw;
    void *buf;
    int ret;

    buf = dma_alloc_coherent(dev, SZ_256K, &dma_addr, GFP_KERNEL);
    if (!buf)
        return -ENOMEM;

    ret = request_firmware_into_buf(&fw, "my_fw.bin", dev, buf, SZ_256K);
    if (ret) {
        dma_free_coherent(dev, SZ_256K, buf, dma_addr);
        return ret;
    }

    /* fw->data == buf，fw->size <= SZ_256K */
    /* buf 现在包含固件内容，可直接用于 DMA */

    release_firmware(fw);
    /* 注意：buf 的释放由驱动负责，release_firmware 不释放预分配缓冲区 */
    return 0;
}
```

### 部分读取

```c
ret = request_partial_firmware_into_buf(&fw, "my_fw.bin", dev,
                                        buf, SZ_64K, offset);
```

从文件 `offset` 处读取最多 `SZ_64K` 字节。不参与批处理合并。

**要点**：
- 如果该设备已设了固件缓存（`fw_cache_is_setup`），返回 `-EOPNOTSUPP`
- 不缓存——suspend/resume 时驱动需自行处理

---

## §4 驱动提供固件上传

```c
#include <linux/firmware.h>

enum fw_upload_err my_prepare(struct fw_upload *fw_upload,
                              const u8 *data, u32 size)
{
    struct my_device *mydev = fw_upload->dd_handle;

    /* 验证固件大小、准备设备进入更新模式 */
    if (size > MY_MAX_FW_SIZE)
        return FW_UPLOAD_ERR_INVALID_SIZE;

    return mydev_enter_update_mode(mydev);
}

enum fw_upload_err my_write(struct fw_upload *fw_upload,
                            const u8 *data, u32 offset,
                            u32 size, u32 *written)
{
    struct my_device *mydev = fw_upload->dd_handle;

    /* 写一块数据到设备，*written 设为实际写入量 */
    return mydev_write_block(mydev, data, offset, size, written);
}

enum fw_upload_err my_poll_complete(struct fw_upload *fw_upload)
{
    struct my_device *mydev = fw_upload->dd_handle;

    /* 轮询设备直到固件更新完成 */
    return mydev_wait_programming_done(mydev, timeout_ms);
}

void my_cancel(struct fw_upload *fw_upload)
{
    struct my_device *mydev = fw_upload->dd_handle;

    /* 从不同线程调用——需要处理竞争 */
    mydev_cancel_update(mydev);
}

void my_cleanup(struct fw_upload *fw_upload)
{
    struct my_device *mydev = fw_upload->dd_handle;

    mydev_exit_update_mode(mydev);
}

static const struct fw_upload_ops my_fw_upload_ops = {
    .prepare       = my_prepare,
    .write         = my_write,
    .poll_complete = my_poll_complete,
    .cancel        = my_cancel,
    .cleanup       = my_cleanup,
};

static int my_driver_probe(struct platform_device *pdev)
{
    struct my_device *mydev;
    struct fw_upload *fwl;

    mydev = devm_kzalloc(&pdev->dev, sizeof(*mydev), GFP_KERNEL);
    if (!mydev)
        return -ENOMEM;

    fwl = firmware_upload_register(THIS_MODULE, &pdev->dev,
                                   dev_name(&pdev->dev),
                                   &my_fw_upload_ops, mydev);
    if (IS_ERR(fwl))
        return PTR_ERR(fwl);

    mydev->fwl = fwl;
    return 0;
}

static int my_driver_remove(struct platform_device *pdev)
{
    struct my_device *mydev = dev_get_drvdata(&pdev->dev);

    firmware_upload_unregister(mydev->fwl);
    return 0;
}
```

**要点**：
- `firmware_upload_register` 的 `name` 参数在所有上传用户中必须唯一
- `write` 返回 0 字节写入（`*written = 0`）被视为错误（`FW_UPLOAD_ERR_RW_ERROR`）
- `cancel` 从不同内核线程调用——必须用锁或原子操作保护共享状态
- `firmware_upload_unregister` 在上传进行中会先调 `cancel` 再 `flush_work`

---

## §5 近邻符号区分

### §5.1 `request_firmware` vs `firmware_request_nowarn` vs `request_firmware_direct`

| API | 警告 | sysfs 回退 | 适用场景 |
|---|---|---|---|
| `request_firmware` | 是 | 是 | 标准请求——固件必须存在 |
| `firmware_request_nowarn` | 否 | 是 | 固件可选但不想刷警告 |
| `request_firmware_direct` | 否 | **否** | 固件可选且不想等超时 |

**错用后果**：用 `request_firmware` 请求可选固件 → 找不到时刷 `dev_warn` + 等 60 秒 sysfs 超时。用 `request_firmware_direct` 请求必须固件 → 找不到时没有回退机会。

### §5.2 `request_firmware_into_buf` vs `request_firmware`

| 维度 | `request_firmware` | `request_firmware_into_buf` |
|---|---|---|
| 缓冲区 | 框架分配（vmalloc） | 调用者预分配 |
| 缓存 | 参与 PM 缓存 | 不缓存 |
| 批处理 | 参与 | 不参与 |
| 压缩 | 支持 | 支持 |
| 返回后 `fw->data` | 框架分配的地址 | 调用者传入的 `buf` |

**错用后果**：需要 DMA 缓冲区时用了 `request_firmware` → `fw->data` 在 vmalloc 区域，不能直接做 DMA。

### §5.3 `release_firmware` vs 不调

内建固件场景：`release_firmware` 只释放 `struct firmware` 壳——不释放数据（数据在内建段）。

非内建场景：`release_firmware` 通过 `fw_priv->ref`（kref）管理——最后一个释放者真正释放内存。

**错用后果**：不调 `release_firmware` → 内存泄漏 + `fw_priv` 留在 `fw_cache.head` 链表中永不释放。

### §5.4 `firmware_request_builtin` vs `firmware_request_builtin_buf`

| API | 导出 | 用途 |
|---|---|---|
| `firmware_request_builtin` | `TEST_FIRMWARE` 命名空间 | 零拷贝查找——仅测试模块 |
| `firmware_request_builtin_buf` | 不导出 | 内部 API——可拷贝到预分配缓冲区 |

**错用后果**：非测试模块调 `firmware_request_builtin` → 链接错误（命名空间不匹配）。

### §5.5 `firmware_upload_register` vs `request_firmware`

这两个 API 方向**完全相反**：

- `request_firmware`：驱动**请求**固件 → 框架去文件系统找 → 驱动拿到数据
- `firmware_upload_register`：驱动**提供**上传接口 → 用户空间通过 sysfs 推送固件 → 框架调驱动的 ops 写入设备

**错用后果**：想用 sysfs 让用户更新设备固件却调了 `request_firmware` → 方向反了，用户空间无法主动发起更新。
