# devres（设备资源管理）

### 定位

**功能**：把驱动 probe 阶段申请的资源（内存、IRQ、IO 映射等）挂到设备的链表上，驱动 detach 时自动逆序释放，消除 error path 和 remove 路径中的资源泄漏。驱动开发者不需要手写 goto err_out 清理链，devres 框架保证资源按 LIFO 顺序自动回收。

**实现方式**：每次 `devm_kzalloc` 等调用分配一个 `struct devres` 节点（`drivers/base/devres.c:26-36`），包含元数据（`devres_node`：release 回调、名字、大小）和紧跟其后的柔性数组数据区，一次 kmalloc 完成。节点通过 `entry` 链表头挂入 `dev->devres_head`，链表操作由 `dev->devres_lock`（spinlock）保护。设备释放时 `devres_release_all()` 逆序遍历链表逐个调用 release 回调。还支持资源组（`devres_open_group` / `devres_close_group`）实现批量释放。

**应用场景**：几乎所有现代驱动都依赖 devres 管理资源。`devm_kzalloc` 全树 6,336 处调用，是最常用的 devres API；`devm_request_irq`、`devm_ioremap`、`devm_regulator_get`、`devm_clk_get`、`devm_gpio_request` 等变体全部走同一套 devres 链表。`devm_add_action_or_reset` 作为万能胶水，可以把任意自定义清理函数纳入 devres 管理。

---

## 1. 核心机制

头文件：`include/linux/device.h`（API 声明）；实现：`drivers/base/devres.c`

### 1.1 内部数据结构

```c
struct devres_node {                    // devres.c:19-24
    struct list_head    entry;          // 挂入 dev->devres_head 链表
    dr_release_t        release;        // 释放回调
    const char          *name;          // 调试用名字
    size_t              size;           // 数据区大小
};

struct devres {                         // devres.c:26-36
    struct devres_node  node;
    u8 __aligned(ARCH_KMALLOC_MINALIGN) data[];  // 柔性数组，存放实际数据
};
```

每个 `devres` 节点 = 元数据（`devres_node`）+ 数据区（`data[]`）。数据区紧跟在元数据后面，一次 `kmalloc` 搞定。

### 1.2 工作原理

1. 驱动 probe 时调用 `devm_kzalloc()` 等 API
2. 内核分配 `struct devres`（含数据区），将 `release` 回调和节点挂到 `dev->devres_head`
3. 驱动 detach（或 probe 失败回退）时，`device_release()` → `devres_release_all()`
4. 逆序遍历链表，逐个调用 `release` 回调并释放内存

**关键特性**：
- 资源按 LIFO 顺序释放（后进先出），和 C 栈的清理顺序一致
- 支持资源组（`devres_open_group` / `devres_close_group`），可以批量释放一组资源
- 链表操作使用 `dev->devres_lock`（spinlock）保护

### 1.3 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `devm_kmalloc` | 分配内存，设备 detach 时自动 `kfree` | 底层实现 |
| `devm_kzalloc` | 同上，清零 | **最常用**，全树 6,336 处 |
| `devm_kfree` | 手动提前释放 devm 内存 | 通常不需要 |
| `devm_krealloc` | 重新分配，保持 devm 关联 | |
| `devm_add_action` | 注册自定义释放动作 | 万能胶水：把任意清理函数挂到 devres |
| `devm_add_action_or_reset` | 同上，注册失败立即执行清理 | 推荐用法 |

**`devm_add_action` 的用法**——把任意资源纳入 devres 管理：

```c
static void my_cleanup(void *data)
{
    struct my_struct *m = data;
    hardware_stop(m);
}

// probe 中：
devm_add_action_or_reset(dev, my_cleanup, my_data);
```

### 1.4 devres 变体系列

内核在 `devm_kzalloc` 基础上封装了大量专用 API，全部走 devres：

| API | 说明 |
|-----|------|
| `devm_request_irq` | 申请 IRQ，detach 时自动 `free_irq` |
| `devm_request_mem_region` | 申请内存区域 |
| `devm_ioremap` / `devm_ioremap_resource` | IO 映射 |
| `devm_regulator_get` | 获取 regulator |
| `devm_clk_get` | 获取 clk |
| `devm_gpio_request` | 申请 GPIO |
| `devm_kmalloc` 系列 | 各种内存分配 |

### 1.5 调试支持

`CONFIG_DEBUG_DEVRES`（`drivers/base/devres.c:52-65`）开启后：
- `log_devres` 模块参数控制是否打印每次 ADD/REMOVE
- 输出格式：`DEVRES ADD <ptr> <name> (<size> bytes)`

还有 tracepoint `devres_log`（`devres.c:67-72`），可以通过 ftrace 追踪。

---

## 2. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `devm_kzalloc` | 6,336 | 最常用 devres API |
| `devm_kmalloc` | 160 | 非清零版本 |
| `devres_*` 系列 | 663 | 底层 devres 操作 |

devres 是内核中使用最广泛的资源管理模式，几乎所有现代驱动都依赖它。
