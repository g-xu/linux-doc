---
title: 费曼学习法
---

# Firmware Loader · 费曼解释

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。

---

## 为什么 `struct firmware` 和 `struct fw_priv` 要分两个？

### 先举个例子

你去图书馆借书。你拿到的是一张**借阅卡**——上面写着书名、归还日期。书本身还在图书馆的系统里，可能被多人预约、有馆藏编号、有破损记录。你只关心借阅卡上的信息；图书馆管理员关心的是书本身的状态。

`struct firmware` 就是借阅卡——驱动拿到的"句柄"，只有 `size`、`data`、`priv` 三个字段。`struct fw_priv` 是图书馆系统里的完整记录——有引用计数、状态机、分页缓冲区、搜索路径信息、缓存链表节点。

### 回到代码

`include/linux/firmware.h:12-18` 定义公开结构体；`drivers/base/firmware_loader/firmware.h:66-87` 定义私有结构体。驱动通过 `fw->priv` 间接引用 `fw_priv`，但从不直接访问其字段——`release_firmware()`（`main.c:1107-1114`）通过 `priv` 找到 `fw_priv` 做 kref 递减。

分离的关键原因是**批处理**：两个驱动同时请求同一固件时，它们各自拿到独立的 `struct firmware`（借阅卡），但共享同一个 `struct fw_priv`（书）。`alloc_lookup_fw_priv()`（`main.c:167-200`）在链表中找到同名 `fw_priv` 后 `kref_get` 增加引用——两份借阅卡指向同一本书。

### 这个类比什么时候会误导你？

图书馆的书是物理实体，不能同时被两个人"使用"。但 `fw_priv` 的数据可以被多个 `firmware` 同时读取——因为 `data` 指针指向只读内存（vmalloc 区域或内建段）。更准确的类比是**数据库连接池**：多个客户端拿到各自的连接句柄，但底层共享同一个连接。

---

## 固件加载的"五级瀑布"是怎么回事？

### 先举个例子

你要买一种特殊调料。先去楼下超市（文件系统直接查找）→ 没有 → 去网上搜普通包装的（ZSTD 压缩）→ 没有 → 搜真空包装的（XZ 压缩）→ 没有 → 问邻居借一点（EFI 嵌入固件）→ 也没有 → 在小区群里发一条消息等人送（sysfs 回退）。

每一级都比上一级更慢、更复杂，但覆盖面更广。大多数时候第一级就解决了。

### 回到代码

`_request_firmware()`（`main.c:859-886`）按以下顺序尝试：

1. `fw_get_filesystem_firmware(device, fw->priv, "", NULL)` — 原始文件
2. `fw_get_filesystem_firmware(device, fw->priv, ".zst", fw_decompress_zstd)` — ZSTD
3. `fw_get_filesystem_firmware(device, fw->priv, ".xz", fw_decompress_xz)` — XZ
4. `firmware_fallback_platform(fw->priv)` — EFI 嵌入
5. `firmware_fallback_sysfs(fw, name, device, opt_flags, ret)` — sysfs 回退

每一步只在前一步返回 `-ENOENT` 时执行（`main.c:866-885` 的 `if (ret == -ENOENT && nondirect)` 模式）。

但在这一切之前，还有第零级：`firmware_request_builtin_buf()`（`main.c:743-746`）查内建固件——编译进内核的固件，零 I/O 开销。

### 这个类比什么时候会误导你？

超市和网店卖的是同一种东西，只是包装不同。但固件的"原始文件"和"压缩文件"是**不同文件名**（`fw.bin` vs `fw.bin.zst`）——框架自动追加后缀搜索。而且压缩解压可能失败（格式错误、内存不足），此时不会继续尝试下一级——直接返回错误。

---

## sysfs 回退为什么"事实上已死"但代码还在？

### 先举个例子

你家小区以前有个送奶工，每天早上送牛奶到门口。后来大家都改在网上订奶了，送奶工失业了。但小区的送奶箱还装在门口——因为合同没到期，而且万一哪天你想恢复送奶服务，拆掉再装很麻烦。

### 回到代码

udev 自 2014 年（systemd v217）起不再监听固件 uevent。大多数发行版禁用 `CONFIG_FW_LOADER_USER_HELPER_FALLBACK`。但 `fallback.c` 的 235 行代码、`sysfs.c` 的 419 行代码仍然在树里。

原因在 Kconfig help（`Kconfig:112-113`）里写得很清楚：

> Since this was the default firmware loading facility at one point, old userspace may exist which relies upon it, and as such this mechanism can never be removed from the kernel.

而且 `firmware_class` 这个类名（`sysfs.c:102`）与用户空间 ABI 绑定——改名会破坏旧用户空间的 `/sys/class/firmware/` 路径。

还有两个驱动依赖它：`CONFIG_LEDS_LP55XX_COMMON` 和 `CONFIG_DELL_RBU`。

### 这个类比什么时候会误导你？

送奶箱完全没用。但 sysfs 回退并非完全无用——它提供了一种**可编程的**固件加载通道。嵌入式系统可以用自定义用户空间程序监听 uevent 并从特殊位置（加密分区、网络）加载固件。`fw_upload.rst` 文档中提到的 provisioning 场景就是这种用法。

---

## 批处理请求到底在"批"什么？

### 先举个例子

五个同事同时需要同一份合同模板。与其每人各发一封邮件给行政部，行政部只准备一份、复印五份。第一个人提交申请后开始准备，其余四个人排队等着。准备完成后五个人各拿一份走。

### 回到代码

`alloc_lookup_fw_priv()`（`main.c:178-187`）在 `fw_cache.lock` 保护下按名字查找。第二个请求者命中同一个 `fw_priv` 后 `kref_get` 增加引用计数，返回 1。

`_request_firmware_prepare()`（`main.c:757-763`）看到返回值 > 0 后调 `fw_state_wait()`（`main.c:105-108`）——阻塞在 `completion` 上等待第一个请求者完成。

第一个请求者走完整个加载管线后调 `fw_state_done()`（`firmware.h:148-151`）→ `complete_all()` 唤醒所有等待者。每个等待者各自调 `fw_set_page_data()`（`main.c:594-603`）把数据指针设到自己的 `struct firmware` 里。

**不参与批处理的请求**：`FW_OPT_NOCACHE`（`into_buf`、`direct` 的部分场景）和 `FW_OPT_PARTIAL`（部分读取）——`main.c:178` 的 `if (!(opt_flags & (FW_OPT_NOCACHE | FW_OPT_PARTIAL)))` 判断。

### 这个类比什么时候会误导你？

合同模板复印后每人拿到的是一份独立副本。但批处理的 `fw_priv` 是**共享**的——五个驱动指向同一块内存。`release_firmware` 通过 kref 管理：每次释放减 1，最后一个释放者才真正释放内存。如果某个驱动修改了 `fw->data` 的内容（不应该这样做，因为是 `const u8 *`），其他驱动也会看到修改。

---

## 为什么 `request_firmware` 要切换内核凭证？

### 先举个例子

你是物业管理员，有业主打电话说"帮我从仓库拿个包裹"。仓库只认管理员的钥匙卡，不认业主的。所以你用**自己的**钥匙卡去仓库取包裹，然后交给业主。

### 回到代码

`_request_firmware()`（`main.c:852-857`）在读取固件文件前调 `prepare_kernel_cred(NULL)` + `override_creds()`。读完后 `revert_creds()` + `put_cred()`（`main.c:889-890`）。

原因在注释里（`main.c:847-851`）：

> We are about to try to access the firmware file. Because we may have been called by a driver when serving an unrelated request from userland, we use the kernel credentials to read the file.

场景：驱动正在为一个用户态进程服务（比如设备节点被打开），此时该进程的凭证可能没有读 `/lib/firmware/` 的权限。如果直接用该进程凭证读文件，固件加载会失败。切换到内核凭证（拥有完整文件系统访问权）确保固件总能被读到。

**安全边界**：`name_contains_dotdot()`（`main.c:805-812`）在凭证切换之前检查固件名——即使有内核凭证，也不允许路径穿越。

### 这个类比什么时候会误导你？

物业管理员用钥匙卡取包裹时，包裹的内容仍然受检查。类似地，`security_kernel_load_data()`（LSM 钩子）在 sysfs 回退路径上仍然检查——即使凭证是内核的，SELinux / IMA 策略仍然可以拒绝加载。凭证切换只解决文件访问权限问题，不绕过安全策略。

---

## 固件上传和固件加载的方向为什么相反？

### 先举个例子

**固件加载**像收快递：你（驱动）下单（`request_firmware`），快递员（框架）去找包裹（文件系统），找到了送到你手上。

**固件上传**像开驿站：你（驱动）在小区里开个驿站（`firmware_upload_register`），居民（用户空间）随时可以把包裹（固件数据）送到驿站，然后你负责把包裹转交给最终收件人（设备硬件）。

### 回到代码

| 维度 | 固件加载 | 固件上传 |
|---|---|---|
| 发起者 | 驱动（`request_firmware`） | 用户空间（写 sysfs） |
| 数据流 | 文件系统 → 内核 → 驱动 | 用户空间 → 内核 → 设备 |
| API | `request_firmware()` 系列 | `firmware_upload_register()` |
| sysfs | 临时（加载完删除） | 持久（驱动注销前存在） |
| 工作线程 | 无（在请求者上下文） | `system_long_wq` |

`sysfs_upload.c:225-265` 的 `fw_upload_start()` 在用户写 `loading=0` 时被触发 → `queue_work(system_long_wq, &fwlp->work)` → `fw_upload_main()`（`sysfs_upload.c:158-219`）在工作线程中按序调 `prepare → write → poll_complete → cleanup`。

### 这个类比什么时候会误导你？

快递和驿站都涉及物理包裹。但固件上传的数据从用户空间到内核后，`write` 回调如何把数据送到硬件完全由驱动决定——可能是 SPI、I2C、PCIe MMIO、甚至网络。框架只负责"把数据交到驱动手上"，不关心数据怎么到设备。而且上传可能持续数分钟（FPGA 编程），框架通过 `remaining_size` 和 `progress` 提供进度可见性——这是收快递没有的。

---

## PM 缓存为什么在 suspend 前才缓存固件？

### 先举个例子

你明天要出差。今晚你把护照、机票、充电器都放进旅行包——因为你**知道**明天早上赶飞机时没时间翻箱倒柜。但平时这些东西散落在家里各处，因为你随时都能去找。

suspend 就是"出差"——resume 后短时间内根文件系统可能还没准备好（存储驱动还没恢复），但设备驱动可能立刻需要固件重新初始化。PM 缓存就是"旅行包"——suspend 前把固件提前装好。

### 回到代码

`fw_pm_notify()`（`main.c:1494-1507`）在 `PM_SUSPEND_PREPARE` 通知时调 `device_cache_fw_images()`（`main.c:1441-1462`）：

1. 缩短超时到 10 秒（`fw_fallback_set_cache_timeout`）
2. 设 `fwc->state = FW_LOADER_START_CACHE`
3. `dpm_for_each_dev` 遍历所有设备，对每个注册了固件名的设备异步调 `cache_firmware`
4. `async_synchronize_full_domain` 等待所有缓存完成
5. 恢复超时

`cache_firmware()`（`main.c:1228-1242`）本质是重新调 `request_firmware`——但此时文件系统还可用，所以能成功。加载完成后 `kfree(fw)` 释放 `struct firmware` 壳，但 `fw_priv` 因为 kref > 0 而留在内存中。resume 后驱动再调 `request_firmware` 时，`alloc_lookup_fw_priv` 在链表中找到这个 `fw_priv`——批处理命中，直接返回数据。

resume 后 10 秒延迟释放（`main.c:1520`）——给驱动足够时间在文件系统恢复前完成固件请求。

### 这个类比什么时候会误导你？

旅行包里的东西你拿出来用了就没了。但 PM 缓存的固件是**共享**的——resume 后驱动读取固件不会消耗缓存，因为 `fw_priv` 通过 kref 管理。只有当所有引用者都 `release_firmware` 后内存才释放。而且 10 秒延迟释放意味着即使没有驱动请求，缓存也会在 resume 后 10 秒自动清理——不会永久占用内存。

---

## 阅读导航

| 上一篇 | 下一篇 |
|---|---|
| `07-debug.md` | `00-overview.md` |

如果某个概念在本文中没讲清楚，回到对应的技术文档查行号和代码：

| 主题 | 技术文档 |
|---|---|
| `struct firmware` vs `fw_priv` | `02-datastructures.md` §1.0 |
| 五级瀑布 | `03-internals.md` §A1-§A7 |
| sysfs 回退 | `04-extensions.md` §3 |
| 批处理 | `03-internals.md` §B1 |
| 凭证切换 | `03-internals.md` §B3 |
| 上传 vs 加载 | `04-extensions.md` §5 |
| PM 缓存 | `04-extensions.md` §6 |
