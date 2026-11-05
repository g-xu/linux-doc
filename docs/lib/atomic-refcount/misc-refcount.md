# kref — 引用计数包装

### 定位

**功能**：在 `refcount_t` 之上增加释放回调约定。当引用计数降到 0 时，自动调用调用者提供的 `release` 函数。把"递减 → 到 0 → 释放"这一模式标准化，避免每个子系统重复实现引用计数释放逻辑。

**实现方式**：`include/linux/kref.h:19-21` 定义 `struct kref { refcount_t refcount; };`。纯头文件实现（113 行，全部 `static inline`）。核心 `kref_put()`（`:62-69`）调 `refcount_dec_and_test()`，返回 true 时执行 `release(kref)`。变体：`kref_put_mutex()`（`:71`）到 0 时先加 mutex 再调 release；`kref_put_lock()`（`:82`）到 0 时先加 spinlock；`kref_get_unless_zero()`（`:109`）非 0 时加 1，防止并发释放-获取竞争（典型用于从查找表获取对象时避免 TOCTOU）。与 `refcount_t` 的区别：`refcount_t` 需要调用者自己检查 `dec_and_test` 返回值，`kref` 自动调用 release 回调。

**应用场景**：2,886 处 API 调用、1,210 处结构体引用。广泛用于设备模型（`struct device` 内嵌 `kref`）、网络子系统（Bluetooth L2CAP `net/bluetooth/l2cap_core.c`、RFCOMM `net/bluetooth/rfcomm/tty.c`、NFC LLCP `net/nfc/llcp_core.c`、CAN J1939 `net/can/j1939/transport.c`）、RPC（`net/sunrpc/auth_gss/auth_gss.c`）、Ceph（`net/ceph/mon_client.c`、`net/ceph/osd_client.c`）、DSA 交换（`net/dsa/dsa2.c`）、音频 DSP（`sound/soc/qcom/qdsp6/`）和 AppArmor（`security/apparmor/apparmorfs.c`）。

### 实现方式

`include/linux/kref.h:19-21`：

```c
struct kref {
    refcount_t refcount;
};
```

纯头文件实现（113 行，全部 `static inline`）。核心是 `kref_put`（`:62-69`）：

```c
static inline int kref_put(struct kref *kref,
                           void (*release)(struct kref *kref))
{
    if (refcount_dec_and_test(&kref->refcount)) {
        release(kref);
        return 1;
    }
    return 0;
}
```

### 与 refcount_t 的区别

| 特性 | refcount_t | kref |
|------|-----------|------|
| 底层 | `atomic_t` 包装 | `refcount_t` 包装 |
| 释放机制 | 调用者检查 `dec_and_test` 返回值 | `kref_put` 自动调用 release 回调 |
| 安全获取 | `refcount_inc_not_zero` | `kref_get_unless_zero` |
| 到 0 加锁 | `refcount_dec_and_lock` | `kref_put_lock` / `kref_put_mutex` |

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `KREF_INIT(n)` | 静态初始化 | `kref.h:23` |
| `kref_init(kref)` | 动态初始化 | 设为 1，`kref.h:29` |
| `kref_read(kref)` | 读取当前值 | `kref.h:34` |
| `kref_get(kref)` | 加 1 | `kref.h:43` |
| `kref_put(kref, release)` | 减 1，到 0 调 release | 返回 1=已释放，`kref.h:62` |
| `kref_put_mutex(kref, release, lock)` | 到 0 时加 mutex 再调 release | `kref.h:71` |
| `kref_put_lock(kref, release, lock)` | 到 0 时加 spinlock 再调 release | `kref.h:82` |
| `kref_get_unless_zero(kref)` | 非 0 时加 1 | 返回 bool，`kref.h:109` |

### 使用规模

2,886 处 API 调用，1,210 处结构体引用。广泛用于设备模型（`struct device`）、文件系统对象、网络子系统。
