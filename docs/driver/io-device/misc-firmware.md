# firmware loader（固件加载器）

### 定位

**功能**：为驱动提供统一的固件文件加载接口——从文件系统（`/lib/firmware/`）、内建固件表（`CONFIG_EXTRA_FIRMWARE`）、或用户态 helper 获取二进制固件数据，供驱动下载到硬件。驱动只需给出固件文件名，框架负责搜索、加载、缓存和释放的全部细节。

**实现方式**：核心数据结构是 `struct firmware`（`include/linux/firmware.h:12-18`），包含 `size`、`data` 指针和内部 `priv` 字段。同步加载路径 `request_firmware()`（`drivers/base/firmware_loader/`）按优先级搜索：内建固件表 → `/lib/firmware/<name>` → 用户态 helper。异步版本 `request_firmware_nowait()` 通过工作队列加载，完成后调用回调。还有 `request_firmware_into_buf()` 加载到预分配缓冲区，适用于 DMA 场景。`CONFIG_FW_UPLOAD` 额外提供反向的固件上传框架（`struct fw_upload_ops`），用于安全固件更新。

**应用场景**：`request_firmware` 全树 673 处调用。主要消费者包括 Wi-Fi 驱动（如 `drivers/net/wireless/` 下各芯片固件）、GPU 驱动（AMDGPU/Mediatek 等加载微码）、音频 DSP 固件、USB 设备固件（如 Cypress FX2）、以及各类 SoC 外设协处理器固件加载。

---

## 1. 核心结构

头文件：`include/linux/firmware.h`；实现：`drivers/base/firmware_loader/`

### 1.1 `struct firmware`

```c
struct firmware {                       // firmware.h:12-18
    size_t size;                        // 固件大小
    const u8 *data;                     // 固件数据指针
    void *priv;                         // 内部字段，驱动不要碰
};
```

驱动拿到 `struct firmware *` 后，通过 `fw->data` 和 `fw->size` 访问固件内容。

---

## 2. 加载 API

### 2.1 同步加载（probe 中使用）

| API | 说明 | 备注 |
|-----|------|------|
| `request_firmware(&fw, name, dev)` | 同步加载固件 | **最常用**，673 处。阻塞直到固件就绪或失败 |
| `firmware_request_nowarn(&fw, name, dev)` | 同上，失败不打印警告 | 可选固件用 |
| `firmware_request_platform(&fw, name, dev)` | 优先从平台固件（如 ACPI/EFI）加载 | |
| `request_firmware_direct(&fw, name, dev)` | 跳过 usermode helper 回退 | 更快失败 |
| `release_firmware(fw)` | 释放固件 | 与 `request_firmware` 配对 |

**搜索顺序**：
1. 内建固件表（`CONFIG_EXTRA_FIRMWARE`）
2. `/lib/firmware/<name>`（通过 kernel file read）
3. 用户态 helper（`CONFIG_UEVENT_HELPER`，已废弃）

### 2.2 异步加载

| API | 说明 | 备注 |
|-----|------|------|
| `request_firmware_nowait(module, uevent, name, dev, gfp, context, cont)` | 异步加载，完成后调 `cont` 回调 | 不阻塞 probe |

回调签名：`void cont(const struct firmware *fw, void *context)`。`fw` 为 NULL 表示加载失败。回调中**必须**调 `release_firmware()`。

### 2.3 加载到指定缓冲区

| API | 说明 | 备注 |
|-----|------|------|
| `request_firmware_into_buf(&fw, name, dev, buf, size)` | 加载到预分配缓冲区 | DMA 场景用 |
| `request_partial_firmware_into_buf` | 同上，支持偏移 | |

---

## 3. 固件上传（反向：内核 → 硬件）

`CONFIG_FW_UPLOAD` 提供 `struct fw_upload` 框架（`firmware.h:44-76`），用于安全固件更新：

```c
struct fw_upload_ops {
    enum fw_upload_err (*prepare)(struct fw_upload *, const u8 *data, u32 size);
    enum fw_upload_err (*write)(struct fw_upload *, const u8 *data, u32 offset, u32 size, u32 *written);
    enum fw_upload_err (*poll_complete)(struct fw_upload *);
    void (*cancel)(struct fw_upload *);
    void (*cleanup)(struct fw_upload *);
};
```

| API | 说明 |
|-----|------|
| `firmware_upload_register(module, parent, name, ops, dd_handle)` | 注册固件上传设备 |
| `firmware_upload_unregister(fw_upload)` | 注销 |

---

## 4. 典型用法

```c
// probe 中加载固件
const struct firmware *fw;
int ret;

ret = request_firmware(&fw, "my_device/firmware.bin", &pdev->dev);
if (ret) {
    dev_err(&pdev->dev, "firmware load failed: %d\n", ret);
    return ret;
}

// 使用固件数据
upload_to_hardware(fw->data, fw->size);

// 释放
release_firmware(fw);
```

---

## 5. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `request_firmware` | 673 | 最常用加载入口 |
