---
title: 数据结构
---

# Firmware Loader · 数据结构

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。

## §1.0 先分清四组关系

### 容易混淆的概念

| 概念 | 谁创建 | 生命周期 | 谁可见 |
|---|---|---|---|
| `struct firmware` | `_request_firmware_prepare` 分配 | 驱动调用 `release_firmware` 释放 | **驱动可见**——公开 API 的返回值 |
| `struct fw_priv` | `__allocate_fw_priv` 分配 | kref 管理，最后一个 `free_fw_priv` 释放 | **驱动不可见**——藏在 `firmware->priv` 里 |
| `struct fw_sysfs` | `fw_create_instance` 分配 | sysfs 设备存在期间 | **仅 sysfs 回退路径内部使用** |
| `struct fw_upload_priv` | `firmware_upload_register` 分配 | 上传设备注销时释放 | **仅上传路径内部使用** |

**关键区分**：`struct firmware` 是驱动拿到的"句柄"，`struct fw_priv` 是框架内部的"实体"。多个 `struct firmware` 可以共享同一个 `struct fw_priv`（批处理请求）。`struct fw_sysfs` 只在 sysfs 回退路径中存在——直接文件系统查找成功时根本不会创建。

### 本子系统有几层可插拔

| 层 | ops | 实现者 | arm64 实现数 |
|---|---|---|---|
| 固件加载 | 无 ops——纯函数调用链 | `main.c` 单一路径 | 1 |
| 解压 | `decompress(dev, fw_priv, in_size, in_buffer)` | XZ / ZSTD 两个实现 | 0-2（取决于 Kconfig） |
| 上传 | `struct fw_upload_ops` | 各设备驱动实现 | 取决于设备 |

---

## §1 核心结构体

### §1.1 `struct firmware`（公开）

头文件：`include/linux/firmware.h:12-18`

```c
struct firmware {
    size_t size;
    const u8 *data;
    void *priv;              /* → struct fw_priv */
};
```

- `data` 指向固件内容。来源可能是 `vmalloc`（文件系统加载）、分页缓冲区 vmap（sysfs 回退/压缩解压）、或内建固件段（直接指针，不可释放）
- `priv` 对驱动不透明；`release_firmware` 通过它找到 `fw_priv` 做释放
- 内建固件场景：`data` 直接指向 `__start_builtin_fw[]` 段中的数据，`priv` 为 NULL

### §1.2 `struct fw_priv`（私有）

头文件：`drivers/base/firmware_loader/firmware.h:66-87`

```c
struct fw_priv {
    struct kref ref;                  /* 引用计数 */
    struct list_head list;            /* 挂入 firmware_cache.head */
    struct firmware_cache *fwc;       /* 所属缓存 */
    struct fw_state fw_st;            /* 状态机 */
    void *data;                       /* 固件数据 */
    size_t size;                      /* 实际大小 */
    size_t allocated_size;            /* 预分配大小（into_buf 场景） */
    size_t offset;                    /* 部分读取偏移 */
    u32 opt_flags;                    /* enum fw_opt 标志组合 */
#ifdef CONFIG_FW_LOADER_PAGED_BUF
    bool is_paged_buf;
    struct page **pages;
    int nr_pages;
    int page_array_size;
#endif
#ifdef CONFIG_FW_LOADER_USER_HELPER
    bool need_uevent;
    struct list_head pending_list;    /* 挂入 pending_fw_head */
#endif
    const char *fw_name;              /* 固件名（kstrdup_const） */
};
```

**字段语义要点**：

- `data` 的三种来源：① `vmalloc` 分配（文件系统直接加载）；② `vmap(pages)` 映射（分页缓冲）；③ 调用者预分配（`into_buf` 场景，此时 `allocated_size > 0`）
- `allocated_size > 0` 表示缓冲区是调用者提供的，框架不负责分配/释放
- `opt_flags` 决定加载行为——8 个标志位（`firmware.h:38-47`）：

| 标志 | 位 | 含义 |
|---|---|---|
| `FW_OPT_UEVENT` | BIT(0) | 允许 uevent 通知 |
| `FW_OPT_NOWAIT` | BIT(1) | 异步请求 |
| `FW_OPT_USERHELPER` | BIT(2) | 允许 sysfs 回退（异步无 uevent 时） |
| `FW_OPT_NO_WARN` | BIT(3) | 抑制警告 |
| `FW_OPT_NOCACHE` | BIT(4) | 不缓存、不参与批处理合并 |
| `FW_OPT_NOFALLBACK_SYSFS` | BIT(5) | 禁止 sysfs 回退 |
| `FW_OPT_FALLBACK_PLATFORM` | BIT(6) | 允许 EFI 嵌入固件回退 |
| `FW_OPT_PARTIAL` | BIT(7) | 部分读取 |

### §1.3 `struct fw_state`（状态机）

头文件：`drivers/base/firmware_loader/firmware.h:61-64`

```c
struct fw_state {
    struct completion completion;
    enum fw_status status;
};
```

状态转移（`enum fw_status`，`firmware.h:49-54`）：

```
UNKNOWN → LOADING → DONE
                  → ABORTED
```

- `__fw_state_set` 在 DONE/ABORTED 时调 `complete_all`，唤醒所有等待者
- 批处理请求：第二个及以后的请求者通过 `fw_state_wait` 等待第一个请求者完成
- 状态转换是单向的——一旦 DONE 或 ABORTED 不可回退（上传完成后 `fw_state_init` 重置是例外）

### §1.4 `struct firmware_cache`（全局缓存）

实现：`drivers/base/firmware_loader/main.c:51-71`

```c
struct firmware_cache {
    spinlock_t lock;              /* 保护 head 链表 */
    struct list_head head;        /* 所有活跃的 fw_priv */
    int state;                    /* FW_LOADER_NO_CACHE / FW_LOADER_START_CACHE */
#ifdef CONFIG_FW_CACHE
    spinlock_t name_lock;         /* 保护 fw_names 链表 */
    struct list_head fw_names;    /* 需要缓存的固件名列表 */
    struct delayed_work work;     /* 延迟释放缓存 */
    struct notifier_block pm_notify; /* PM 通知器 */
#endif
};
```

全局实例：`struct firmware_cache fw_cache`（`main.c:95`）。

- `head` 链表挂入所有非 `NOCACHE` 的 `fw_priv`——这就是批处理查找的基础
- `fw_names` 链表在 suspend 前由 `device_cache_fw_images` 填充，resume 后 10 秒延迟释放
- `state` 标记是否处于缓存模式——`FW_LOADER_START_CACHE` 时新请求会触发 `fw_cache_piggyback_on_request`

---

## §2 sysfs 与回退结构体

### §2.1 `struct fw_sysfs`

头文件：`drivers/base/firmware_loader/sysfs.h:76-82`

```c
struct fw_sysfs {
    bool nowait;
    struct device dev;            /* 嵌入设备——注册到 firmware_class */
    struct fw_priv *fw_priv;
    struct firmware *fw;
    void *fw_upload_priv;         /* → struct fw_upload_priv（可选） */
};
```

- `container_of(dev, struct fw_sysfs, dev)` 由 `to_fw_sysfs()` 完成
- `dev.class = &firmware_class`，`dev.parent = 请求设备`
- `fw_upload_priv` 非 NULL 时表示此设备同时是上传设备

### §2.2 `struct firmware_fallback_config`

头文件：`drivers/base/firmware_loader/sysfs.h:31-36`（仅 `CONFIG_FW_LOADER_USER_HELPER`）

```c
struct firmware_fallback_config {
    unsigned int force_sysfs_fallback;
    unsigned int ignore_sysfs_fallback;
    int old_timeout;
    int loading_timeout;
};
```

全局实例：`fw_fallback_config`（`fallback_table.c:20-24`），默认 `loading_timeout = 60`。

通过 sysctl（`/proc/sys/kernel/firmware_config/`）运行时修改。

---

## §3 上传结构体

### §3.1 `struct fw_upload`（公开）

头文件：`include/linux/firmware.h:44-47`

```c
struct fw_upload {
    void *dd_handle;    /* 父驱动私有数据 */
    void *priv;         /* → struct fw_sysfs */
};
```

### §3.2 `struct fw_upload_ops`

头文件：`include/linux/firmware.h:67-76`

```c
struct fw_upload_ops {
    enum fw_upload_err (*prepare)(struct fw_upload *, const u8 *data, u32 size);
    enum fw_upload_err (*write)(struct fw_upload *, const u8 *data,
                                u32 offset, u32 size, u32 *written);
    enum fw_upload_err (*poll_complete)(struct fw_upload *);
    void (*cancel)(struct fw_upload *);
    void (*cleanup)(struct fw_upload *);     /* 可选 */
};
```

- `write` 被反复调用直到 `remaining_size` 归零；返回 0 字节写入视为错误
- `cancel` 从不同内核线程调用——驱动必须处理竞争
- `cleanup` 在 prepare 成功后的成功/失败路径都会调用

### §3.3 `struct fw_upload_priv`（私有）

头文件：`drivers/base/firmware_loader/sysfs_upload.h:27-39`

```c
struct fw_upload_priv {
    struct fw_upload *fw_upload;
    struct module *module;
    const char *name;
    const struct fw_upload_ops *ops;
    struct mutex lock;
    struct work_struct work;          /* system_long_wq 工作队列 */
    const u8 *data;
    u32 remaining_size;
    enum fw_upload_prog progress;
    enum fw_upload_prog err_progress; /* 出错时的进度阶段 */
    enum fw_upload_err err_code;
};
```

进度状态（`enum fw_upload_prog`，`sysfs_upload.h:18-25`）：

```
IDLE → RECEIVING → PREPARING → TRANSFERRING → PROGRAMMING → IDLE
```

---

## §4 结构关系图

```
                        ┌─────────────────────────┐
                        │  struct firmware (公开)  │
                        │  size / data / priv──────┼──┐
                        └─────────────────────────┘  │
                                                     │
    ┌────────────────────────────────────────────────┘
    │
    ▼
    ┌──────────────────────────────────────────────────┐
    │  struct fw_priv (私有, kref 管理)                 │
    │  list → fw_cache.head (批处理查找)                │
    │  fw_st: completion + status (状态机)              │
    │  data / size / allocated_size                     │
    │  opt_flags (8 个标志位)                            │
    │  pages[] (分页缓冲, CONFIG_FW_LOADER_PAGED_BUF)   │
    │  pending_list (sysfs 回退, CONFIG_USER_HELPER)    │
    └──────────────────────┬───────────────────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
    ┌──────────────┐ ┌──────────┐ ┌──────────────────┐
    │ fw_sysfs     │ │ fw_cache │ │ builtin_fw[]     │
    │ (sysfs 回退) │ │ (全局)   │ │ (内建固件段)     │
    │ dev→class=   │ │ head:    │ │ __start_builtin  │
    │  firmware_   │ │  fw_priv │ │ _fw ~ __end_     │
    │  class       │ │  链表    │ │ builtin_fw       │
    │ fw_upload_   │ │ fw_names │ └──────────────────┘
    │  priv ──┐    │ │ :缓存名  │
    └─────────┼────┘ └──────────┘
              │
              ▼
    ┌──────────────────┐
    │ fw_upload_priv   │
    │ ops / work /     │
    │ progress / lock  │
    │ → fw_upload(公开)│
    └──────────────────┘
```

---

## §5 全局变量与锁

| 变量 | 文件 | 类型 | 保护 |
|---|---|---|---|
| `fw_lock` | `main.c:93` | `DEFINE_MUTEX` | 保护 `assign_fw`、`fw_abort_batch_reqs`、sysfs loading 写入 |
| `fw_cache` | `main.c:95` | `struct firmware_cache` | 内部 `lock`（spinlock）保护 `head`；`name_lock` 保护 `fw_names` |
| `fw_cache.state` | `main.c:55` | int | `fw_lock` 保护写入 |
| `pending_fw_head` | `fallback.c:47` | `LIST_HEAD` | `fw_lock` 保护 |
| `fw_fallback_config` | `fallback_table.c:20` | `struct firmware_fallback_config` | 无锁——单值读写，sysctl 串行化 |
| `firmware_class` | `sysfs.c:102` | `struct class` | 由 driver core 内部锁保护 |
| `fw_path_para` | `main.c:470` | `char[256]` | 模块参数，启动时设定 |
| `fw_path[]` | `main.c:471-477` | `const char *const[5]` | 只读 |

---

## §6 命名约定速查

| 前缀 | 含义 | 示例 |
|---|---|---|
| `fw_` | 框架内部函数/变量 | `fw_priv`、`fw_state_done`、`fw_get_filesystem_firmware` |
| `firmware_` | 公开 API 或全局对象 | `request_firmware`、`firmware_class`、`firmware_fallback_sysfs` |
| `__fw_` | 内部 helper（双下划线） | `__allocate_fw_priv`、`__fw_state_set`、`__fw_load_abort` |
| `fw_upload_` | 上传子系统 | `fw_upload_start`、`fw_upload_main` |
