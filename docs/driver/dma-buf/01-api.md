---
title: API 参考
---

# dma-buf 01：API 全景

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ **本篇讲的是 `drivers/dma-buf/`（缓冲区共享框架），不是 dmaengine（`drivers/dma/`）。**
两者名字近、目录近、毫无关系：dmaengine 搬数据，dma-buf 共享缓冲区的所有权与同步点。

---

## §0 先看清这个子系统由几层组成

dma-buf 不是一个框架，是**四个可以分开理解、又必须一起用的层**。
官方文档开头就把它拆成三块（`Documentation/driver-api/dma-buf.rst:11-15`），
加上后来长出来的 heap 分配器一共四块：

| 层 | 核心文件 | 行数 | 一句话 | 导出符号 |
|---|---|---|---|---|
| **缓冲区共享** | `drivers/dma-buf/dma-buf.c` | 1639 | 把一块显存/内存包成一个 `fd`，别的驱动拿 `fd` 就能 map 出 `sg_table` | 19 |
| **同步原语** | `drivers/dma-buf/dma-fence.c` | 965 | 一个"将来某时刻会完成"的一次性信号量，跨驱动可等 | 20 |
| **同步点容器** | `drivers/dma-buf/dma-resv.c` | 782 | 挂在缓冲区上的 fence 集合 + 一把 ww_mutex，隐式同步的载体 | 17 |
| **分配器** | `drivers/dma-buf/dma-heap.c` + `heaps/` | 326+847 | 从用户态直接申请可共享的缓冲区（`/dev/dma_heap/*`） | **0** |

外围还有五个小模块：`sync_file.c`(372) 把 fence 变成 fd、
`dma-fence-array.c`(279)/`dma-fence-chain.c`(265)/`dma-fence-unwrap.c`(172)
是 fence 的组合子、`udmabuf.c`(401) 从 memfd 造 dma-buf、
`sw_sync.c`(412)+`sync_debug.c`(190) 是纯软件的测试用 timeline。

**理解顺序建议**：先 §2（缓冲区），再 §3（fence），再 §4（resv 把前两者缝起来），
§5 §6 是外围。**不要先读 dma-resv**——它的 ww_mutex + RCU 是整个子系统里最难的一段。

---

## §1 ⚠️ 三种导出宏混用：能不能链接取决于你用哪一族

这是 dma-buf 与 pwm/tty/watchdog 最大的形态差别，**写模块前必须先看这一节**。

70 个导出符号里：

| 宏 | 个数 | 分布 | 对外部模块的要求 |
|---|---|---|---|
| `EXPORT_SYMBOL` | **41** | `dma-fence.c` 全部 20 个 · `dma-resv.c` 10 个 · `dma-fence-array.c` 5 · `dma-fence-chain.c` 4 · `sync_file.c` 2 | 无要求，**非 GPL 模块也能链接** |
| `EXPORT_SYMBOL_GPL` | **10** | `dma-resv.c` 7 个 · `dma-fence-unwrap.c` 3 个 | 模块必须声明 GPL 兼容许可 |
| `EXPORT_SYMBOL_NS_GPL` | **19** | **`dma-buf.c` 全部 19 个**，命名空间 `DMA_BUF` | GPL **且**源文件里要写 `MODULE_IMPORT_NS(DMA_BUF)` |

⇒ 三条实际后果：

1. **只用 fence 的模块**（比如自己实现一个 `dma_fence_ops`）可以完全不碰命名空间，
   也不受 GPL 约束——`dma_fence_init`(`dma-fence.c:965`)、`dma_fence_signal`(`:480`)、
   `dma_fence_wait_timeout`(`:522`) 全是裸 `EXPORT_SYMBOL`。
2. **一旦调用任何 `dma_buf_*`**（导出、attach、map、vmap……）就必须加
   `MODULE_IMPORT_NS(DMA_BUF)`，否则**链接期**报
   `module: module uses symbol dma_buf_export from namespace DMA_BUF, but does not import it`。
   全树已有 **24 个 `.c` 文件**这么做，包括
   `drivers/gpu/drm/drm_prime.c`、`drivers/media/common/videobuf2/videobuf2-core.c`、
   `drivers/infiniband/core/umem_dmabuf.c`、`drivers/misc/fastrpc.c`、
   `drivers/xen/gntdev-dmabuf.c`。抄样板时**连这一行一起抄**。
3. ⚠️ **`dma-resv.c` 内部 10 个裸 + 7 个 GPL 是历史遗留，不是设计**。
   同一族 API 里 `dma_resv_add_fence`(`:315`) 是裸的，
   `dma_resv_wait_timeout`(`:688`) 是 GPL 的——非 GPL 模块能加 fence 却不能等 fence。
   **不要从"这个函数用了哪个宏"反推它的重要性或稳定性。**

⚠️ **`dma-heap.c` 和 `dma-buf-sysfs-stats.c` 各 0 个导出**：
heap 的注册接口 `dma_heap_add()` 只在 `include/linux/dma-heap.h` 里声明，
**没有 `EXPORT_SYMBOL`** ⇒ **heap 只能编进内核（`bool` 型 Kconfig），不能做成外部模块**。
这一点在 05 §5 展开。

---

## §2 缓冲区族：`dma_buf_*`（19 个，全在 `DMA_BUF` 命名空间）

### §2.1 导出方（exporter）：造出一个缓冲区

| 函数 | 位置 | 语义 |
|---|---|---|
| `dma_buf_export()` | `dma-buf.c:692` | 把 `struct dma_buf_export_info` 变成 `struct dma_buf *`；**失败返回 `ERR_PTR`** |
| `dma_buf_fd()` | `dma-buf.c:716` | 给 `dma_buf` 装一个 fd 装进当前进程的 fd 表 |
| `dma_buf_get()` | `dma-buf.c:742` | 反向：fd → `struct dma_buf *`，带引用计数 |
| `dma_buf_put()` | `dma-buf.c:761` | 放掉 `dma_buf_get()` 拿到的引用 |

标准四步序列，官方 DOC 块 `dma-buf.c:560` 明确规定：

```c
struct dma_buf *buf;
DEFINE_DMA_BUF_EXPORT_INFO(exp_info);     /* include/linux/dma-buf.h:559-561 */

exp_info.ops   = &my_dmabuf_ops;          /* 必填 */
exp_info.size  = obj->size;               /* 必填 */
exp_info.flags = O_RDWR | O_CLOEXEC;
exp_info.priv  = obj;                     /* 必填 */
/* exp_info.resv 留 NULL ⇒ dma_buf_export() 会分配一个内建的 */

buf = dma_buf_export(&exp_info);
if (IS_ERR(buf))
        return PTR_ERR(buf);
fd = dma_buf_fd(buf, O_CLOEXEC);          /* ⚠️ 失败要自己 dma_buf_put() */
```

⚠️ **`DEFINE_DMA_BUF_EXPORT_INFO(name)` 只填两个字段**
（`include/linux/dma-buf.h:559-561`：`.exp_name = KBUILD_MODNAME`、`.owner = THIS_MODULE`），
其余 5 个字段是零值。**不要手写 `struct dma_buf_export_info info = {};`**——
那样 `exp_name` 为 NULL，会在 sysfs 统计层触发
`pr_err("exporter name must not be empty if stats needed\n")`
（`dma-buf-sysfs-stats.c:177`），而且 debugfs 里那一行的导出者名字是空的。

⚠️⚠️ **`dma_buf_export()` 只校验 3 个回调，13 个里其余 10 个全可以是 NULL**
（`dma-buf.c:608-620`）：

```c
if (WARN_ON(!exp_info->priv || !exp_info->ops
            || !exp_info->ops->map_dma_buf
            || !exp_info->ops->unmap_dma_buf
            || !exp_info->ops->release))
        return ERR_PTR(-EINVAL);
```

⇒ 少写 `.mmap` 不会有任何提示，只是用户态 `mmap()` 那个 fd 时拿 `-EINVAL`；
少写 `.begin_cpu_access`/`.end_cpu_access` 则 `DMA_BUF_IOCTL_SYNC` 静默成功什么都不做。
**这是 dma-buf 的第一号"沉默失败"来源**，详见 08 §5。

### §2.2 导入方（importer）：attach + map

| 函数 | 位置 | 语义 |
|---|---|---|
| `dma_buf_attach()` | `dma-buf.c:919` | 静态 attach；内部就是 `dma_buf_dynamic_attach(dmabuf, dev, NULL, NULL)` |
| `dma_buf_dynamic_attach()` | `dma-buf.c:904` | 动态 attach，带 `struct dma_buf_attach_ops`（2 个成员）和 `importer_priv` |
| `dma_buf_detach()` | `dma-buf.c:965` | 拆 attachment |
| `dma_buf_map_attachment()` | `dma-buf.c:1106` | attachment → `struct sg_table *`；**要求调用者已持 `dma_resv_lock()`** |
| `dma_buf_map_attachment_unlocked()` | `dma-buf.c:1134` | 同上，函数内部自己加锁 |
| `dma_buf_unmap_attachment()` | `dma-buf.c:1170` | 持锁版 |
| `dma_buf_unmap_attachment_unlocked()` | `dma-buf.c:1195` | 自己加锁版 |
| `dma_buf_pin()` | `dma-buf.c:995` | 动态导入方明确要求"不要迁移这块内存" |
| `dma_buf_unpin()` | `dma-buf.c:1016` | 解除 |
| `dma_buf_move_notify()` | `dma-buf.c:1215` | **导出方调**，通知所有动态导入方"映射已失效" |

⚠️ **`_unlocked` 后缀是 6.1 时期正在进行的一次锁重构留下的双份 API**。
两组不是"快慢路径"，是"谁负责加 `dma_resv_lock()`"：

| 你的调用点 | 用哪个 |
|---|---|
| 已经持有 `dma_resv_lock(dmabuf->resv, ctx)` | `dma_buf_map_attachment()` / `dma_buf_unmap_attachment()` |
| 什么锁都没持 | `dma_buf_map_attachment_unlocked()` / `dma_buf_unmap_attachment_unlocked()` |

⚠️ **用错方向的后果不对称**：持锁时调 `_unlocked` 版 = **自死锁**（ww_mutex 递归）；
不持锁时调持锁版 = `dma_resv_assert_held()` 的 lockdep 断言炸出 WARN
（`include/linux/dma-resv.h:312`），但功能上可能"看起来能跑"。
⇒ **新代码统一用 `_unlocked` 版，除非你正在写的就是一段持锁的批量提交路径。**

### §2.3 CPU 访问族

| 函数 | 位置 | 语义 |
|---|---|---|
| `dma_buf_begin_cpu_access()` | `dma-buf.c:1360` | 通知导出方"CPU 要读/写了"，做 cache invalidate |
| `dma_buf_end_cpu_access()` | `dma-buf.c:1388` | CPU 访问结束，做 cache flush |
| `dma_buf_mmap()` | `dma-buf.c:1430` | 把缓冲区映射进用户态 vma |
| `dma_buf_vmap()` | `dma-buf.c:1484` | 内核态线性映射，输出 `struct iosys_map` |
| `dma_buf_vunmap()` | `dma-buf.c:1508` | 解除 |

⚠️ 官方 DOC 块 `dma-buf.c:1218` 的三条硬规定：

1. **`dma_buf_vmap()` 内部有引用计数**（`dmabuf->vmapping_counter`，
   `include/linux/dma-buf.h:344`），由 `dmabuf->lock` 这把 mutex 保护
   ⇒ **可以嵌套调用，但 vmap/vunmap 必须严格配对**。
2. **`DMA_BUF_IOCTL_SYNC` 可能返回 `-EAGAIN` 或 `-EINTR`，用户态必须重试**
   （`dma-buf.c:1253`）。这是 uapi 契约的一部分，不重试就是 bug。
3. ⚠️⚠️ **"Userspace cannot rely on coherent access, even when there are systems
   where it just works without calling these ioctls"**（`dma-buf.c:1272-1273`）。
   在某些 arm64 平台上不调 SYNC ioctl 也能看到正确数据，
   **换一块板子就花屏**——这类 bug 极难定位，见 07。

### §2.4 一张"我该调哪个"的对照表

| 你想干的事 | 调什么 | 前置条件 |
|---|---|---|
| 我有一块显存，想让别人用 | `dma_buf_export()` + `dma_buf_fd()` | 实现 `map_dma_buf`/`unmap_dma_buf`/`release` 三个回调 |
| 我从用户态收到一个 fd | `dma_buf_get()` → `dma_buf_attach()` → `dma_buf_map_attachment_unlocked()` | 结束时严格逆序释放 |
| 我要 CPU 读一下里面的内容（内核态） | `dma_buf_vmap()`，读完 `dma_buf_vunmap()` | 导出方实现了 `.vmap`，否则 `-EINVAL` |
| 我要让用户态 mmap 这块内存 | 用户态直接 `mmap(fd, …)`；导出方需实现 `.mmap` | 用户态前后要发 `DMA_BUF_IOCTL_SYNC` |
| 我的硬件不能容忍内存被搬走 | `dma_buf_pin()` | 只对动态 attach（传了 `importer_ops`）有意义 |
| 我是导出方，我要搬走这块内存 | `dma_buf_move_notify()` | 需 `CONFIG_DMABUF_MOVE_NOTIFY=y` 才有意义 |

---

## §3 fence 族：`dma_fence_*`（20 个，全部裸 `EXPORT_SYMBOL`）

### §3.1 fence 是什么

**一个一次性的、跨驱动可见的"完成信号"。** 三个不变式（DOC 块 `dma-fence.c:39`）：

- 只能从"未完成"翻到"已完成"，**永不回退**；
- 同一个 `context` 里的 fence 按 `seqno` 单调；
- **必须在有限时间内完成**——这是整个子系统的地基，
  官方文档为此专门写了一节论证（`dma-buf.rst:203-272`，见 08 §4）。

### §3.2 生产者侧（实现一个 fence）

| 函数 | 位置 | 语义 |
|---|---|---|
| `dma_fence_context_alloc()` | `dma-fence.c:192` | 申请 `num` 个连续的 context 号；⚠️ `WARN_ON(!num)` |
| `dma_fence_init()` | `dma-fence.c:965` | 初始化，装 `ops`/`lock`/`context`/`seqno`，refcount 置 1 |
| `dma_fence_signal()` | `dma-fence.c:480` | 标记完成并唤醒所有等待者 |
| `dma_fence_signal_locked()` | `dma-fence.c:446` | 调用者已持 `fence->lock` |
| `dma_fence_signal_timestamp()` | `dma-fence.c:424` | 指定完成时刻（硬件给的时间戳更准） |
| `dma_fence_signal_timestamp_locked()` | `dma-fence.c:393` | 持锁版 |
| `dma_fence_set_error()` | `include/linux/dma-fence.h:557`（inline） | 在 signal **之前**记一个负错误码 |
| `dma_fence_release()` | `dma-fence.c:564` | 给 `kref_put` 用的析构，一般不直接调 |
| `dma_fence_free()` | `dma-fence.c:577` | `ops->release` 的默认实现（RCU 延迟 kfree） |
| `dma_fence_begin_signalling()` | `dma-fence.c:317` | ⚠️ lockdep 标注，见 §3.5 |
| `dma_fence_end_signalling()` | `dma-fence.c:332` | 同上 |

### §3.3 消费者侧（等一个 fence）

| 函数 | 位置 | 语义 |
|---|---|---|
| `dma_fence_wait_timeout()` | `dma-fence.c:522` | 核心等待；返回**剩余 jiffies**，0 = 超时，负 = 错误 |
| `dma_fence_wait()` | `include/linux/dma-fence.h:607`（inline） | 无限等的包装 |
| `dma_fence_wait_any_timeout()` | `dma-fence.c:914` | 等一组 fence 里**任意一个** |
| `dma_fence_default_wait()` | `dma-fence.c:808` | `ops->wait` 不填时的默认实现 |
| `dma_fence_add_callback()` | `dma-fence.c:671` | 异步：完成时回调 |
| `dma_fence_remove_callback()` | `dma-fence.c:732` | 摘回调；**返回 `true` 表示真的摘掉了** |
| `dma_fence_get_status()` | `dma-fence.c:696` | 1=已完成 · 0=未完成 · 负=错误 |
| `dma_fence_enable_sw_signaling()` | `dma-fence.c:619` | 强制打开软件通知路径 |
| `dma_fence_describe()` | `dma-fence.c:930` | 往 `seq_file` 里打一行，debugfs 用 |

⚠️ **`dma_fence_wait_timeout()` 的返回值语义容易写错**：
它返回**剩余的 jiffies 数**，`0` 表示超时（不是成功！），负值是 `-ERESTARTSYS` 之类。
判断成功要写 `ret > 0`，写成 `if (!ret)` 会把超时当成功。

⚠️ **`dma_fence_remove_callback()` 返回 `false` 时 fence 已经在 signal 中**，
你的回调**可能正在别的 CPU 上跑**。要安全释放回调结构必须等它跑完——
这是 fence 回调最常见的 UAF 来源。

### §3.4 桩 fence：不需要真等的场合

| 函数 | 位置 | 用途 |
|---|---|---|
| `dma_fence_get_stub()` | `dma-fence.c:149` | 拿一个**全局共享的、已完成**的 fence |
| `dma_fence_allocate_private_stub()` | `dma-fence.c:177` | 拿一个**私有的、已完成**的 fence，可指定时间戳 |

⚠️ `dma_fence_get_stub()` 的 `timestamp` 是**内核启动后第一次调用这个函数的时刻**
（`dma-fence.c:131-148` 惰性初始化那唯一一个全局 `dma_fence_stub`）
⇒ **在 trace 里看到一个时间戳极早的 fence，通常就是它**，不代表你的任务那时完成了。
要有意义的时间戳就用 `dma_fence_allocate_private_stub(ktime_get())`。

`dma_fence_stub_ops`（`dma-fence.c:119-122`）**只填了两个 name 回调** ——
连 `enable_signaling` 都没有，因为它出生就是 signaled 的。

### §3.5 ⚠️ `dma_fence_begin/end_signalling()`：不是可选的

这两个函数在 `CONFIG_LOCKDEP=n` 时是空桩
（`include/linux/dma-fence.h:362`/`:366`），所以很容易被当成"调试用的可以不写"。
**不是。** DOC 块 `dma-fence.c:195` 说明了为什么：

fence 的死锁模式是**跨线程**的——A 线程 `lock(X); dma_fence_wait(f)`，
B 线程 `lock(X); unlock(X); dma_fence_signal(f)`。
两个线程各自的锁序都合法，**lockdep 的常规机制看不到这种 cross-release 依赖**。
这一对函数的作用是把"signal 临界区"注册成一个虚拟锁
（`static struct lockdep_map dma_fence_lockdep_map = { .name = "dma_fence_map" }`，
`dma-fence.c:284-286`），让 lockdep 有东西可比。

正确写法：

```c
bool cookie = dma_fence_begin_signalling();
/* ... 这里面不能做任何可能等 fence 的事 ... */
dma_fence_signal(fence);
dma_fence_end_signalling(cookie);
```

⚠️ **临界区里最容易犯的错**（DOC 块 `dma-fence.c:195` 列的 5 条里最关键的两条）：

- 不能取任何**在别处会包住 `dma_fence_wait()` 的锁**；
- ⚠️⚠️ **`struct dma_resv` 的读者只靠 RCU 保护，所以 signal 临界区从
  "新 fence 装进 resv 的那一刻"就开始了，比 `dma_resv_unlock()` 更早。**
  ⇒ 在 `dma_resv_add_fence()` 之后、`dma_resv_unlock()` 之前做内存分配，
  就已经违约了。

### §3.6 ⚠️ 跨驱动契约：六条硬规定

DOC 块 `dma-fence.c:69` 标题就叫 "fence cross-driver contract"。
这六条是**实现 fence 的人必须满足的**，违反了不会编译报错，
只会在别人的驱动里造成挂死：

| # | 规定 | 出处 | 违反的后果 |
|---|---|---|---|
| 1 | fence 必须在**合理时间内**完成，要有超时和挂死恢复 | `dma-fence.c:69` DOC | 别的驱动的 `dma_fence_wait()` 永久挂住 |
| 2 | **不要去猜别的驱动的超时值**，也不要因为对方慢就自己超时 | 同上 | 破坏对方的挂死恢复逻辑 |
| 3 | signal 路径要用 `dma_fence_begin/end_signalling()` 标注 | §3.5 | lockdep 抓不到跨线程死锁 |
| 4 | ⚠️ **`dma_fence_wait()` 可能在持有 `dma_resv_lock()` 时被调用** ⇒ **fence 的完成路径不能去取任何 `dma_resv` 锁** | 同上 | AB-BA 死锁 |
| 5 | ⚠️ **可能从 `shrinker` 里被调用** ⇒ 完成路径**不能用 `GFP_KERNEL`** | 同上 | 内存回收路径递归 |
| 6 | ⚠️⚠️ **可能从 `mmu_notifier`/`mmu_interval_notifier` 里被调用** ⇒ 不能用 `GFP_NOFS`/`GFP_NOIO`，**只剩 `GFP_ATOMIC`，而它会失败** | 同上 | 同上，且无从降级 |

DOC 块最后一句是一条边界声明：
**"No driver outside of drivers/gpu should ever call dma_fence_wait() in such contexts."**
⇒ 第 4/5/6 条只对 GPU 驱动是硬约束；但**你实现的 fence 有可能被 GPU 驱动等**，
所以**完成路径的分配限制对所有人生效**。

⇒ 实践结论：**fence 的 `.release`/`enable_signaling`/中断处理里一行内存分配都不要写。**
需要的内存在创建 fence 时就分配好。

---

## §4 dma-resv 族：`dma_resv_*`（17 个，10 裸 + 7 GPL）

### §4.1 dma-resv 只有两个字段

`struct dma_resv`（`include/linux/dma-resv.h:155-179`）**一共 2 个成员**：
一把 `struct ww_mutex lock`（`:166`）和一个 `struct dma_resv_list __rcu *fences`（`:178`）。
DOC 块 `dma-resv.c:45` 叫它 "Reservation Object"：
**"一个缓冲区上所有 fence 的容器，每个 fence 带一个 usage 标记"**。

⚠️ **它是 dma-buf 隐式同步的全部**：`dmabuf->resv`（`include/linux/dma-buf.h:433`）
里挂着谁在读、谁在写这块缓冲区，导入方靠它知道"我要等谁"。

### §4.2 `enum dma_resv_usage`：6.1 的新语义

⚠️ **这是 6.1 相对老内核最重要的一处 API 变更**。老的
"exclusive fence + shared fences" 两分法在 6.1 里换成了四档
（`include/linux/dma-resv.h:71-117`）：

| 值 | 行 | 语义 | 谁必须等它 |
|---|---|---|---|
| `DMA_RESV_USAGE_KERNEL` | `:84` | 内核自己的内存管理操作（清页、迁移） | ⚠️ **所有人，无条件** —— 注释原文 "Drivers *always* must wait for those fences" |
| `DMA_RESV_USAGE_WRITE` | `:92` | 有人在写这块缓冲区 | 所有读者和写者 |
| `DMA_RESV_USAGE_READ` | `:100` | 有人在读 | 只有写者 |
| `DMA_RESV_USAGE_BOOKKEEP` | `:116` | **只记账，不参与隐式同步** | 没人 |

⚠️⚠️ **辅助函数 `dma_resv_usage_rw(bool write)` 的返回值是"反"的**
（`include/linux/dma-resv.h:126-136`，关键在 `:135`）：

```c
/* 传 write=true 时返回 READ，传 write=false 时返回 WRITE */
return write ? DMA_RESV_USAGE_READ : DMA_RESV_USAGE_WRITE;
```

这不是 bug。**参数问的是"我要做什么"，返回的是"我要等哪一档"**：
我要写 ⇒ 必须等所有读者（`READ` 档包含读和写）；我只读 ⇒ 只需等写者。
⚠️ 但**读代码时极易看成写错了**，改这一带的代码前先回来看这一段。

### §4.3 加解锁 API（全部是头文件里的 inline）

| 函数 | 位置 | 语义 |
|---|---|---|
| `dma_resv_lock()` | `include/linux/dma-resv.h:342` | 带 ww_acquire_ctx 的加锁；⚠️ **可能返回 `-EDEADLK`** |
| `dma_resv_lock_interruptible()` | `:369` | 可被信号打断，返回 `-ERESTARTSYS` |
| `dma_resv_lock_slow()` | `:386` | 收到 `-EDEADLK` 后重试用的慢路径 |
| `dma_resv_lock_slow_interruptible()` | `:402` | 同上，可打断 |
| `dma_resv_trylock()` | `:422` | 不阻塞 |
| `dma_resv_unlock()` | `:461` | 解锁 |
| `dma_resv_is_locked()` | `:433` | 查询 |
| `dma_resv_held()` / `dma_resv_assert_held()` | `:311` / `:312` | lockdep 断言 |
| `dma_resv_locking_ctx()` | `:450` | 取当前持锁的 ww context |

⚠️⚠️ **`-EDEADLK` 不是错误，是"请重试"**。这是 ww_mutex（wound/wait mutex）的
核心语义，也是 dma-resv 最反直觉的一点。标准重试骨架：

```c
struct ww_acquire_ctx ctx;
int ret;

ww_acquire_init(&ctx, &reservation_ww_class);   /* dma-resv.c:57-58 */
retry:
        ret = dma_resv_lock(obj_a->resv, &ctx);
        if (ret == -EDEADLK)
                goto backoff;
        ret = dma_resv_lock(obj_b->resv, &ctx);
        if (ret == -EDEADLK) {
                dma_resv_unlock(obj_a->resv);
                goto backoff;
        }
        /* ... 干活 ... */
        dma_resv_unlock(obj_b->resv);
        dma_resv_unlock(obj_a->resv);
        ww_acquire_fini(&ctx);
        return 0;
backoff:
        /* 用 _slow 版重新抢第一把，保证这次一定赢 */
        dma_resv_lock_slow(obj_a->resv, &ctx);
        goto retry;   /* 注意 retry 里 obj_a 会被再锁一次，实际代码要跳过 */
```

⚠️ 上面这段是**结构示意**，真实的多对象批量锁定要用
`drm_exec` 之类的辅助层（6.1 里 GPU 驱动各自实现）。
**核心里只提供 `reservation_ww_class`（`dma-resv.c:57`，导出在 `:58`）这一个共享的锁类**
——**所有 dma-resv 必须用同一个 class，否则 ww_mutex 的死锁避免机制失效**。

### §4.4 fence 的增删查

| 函数 | 位置 | 宏 | 语义 |
|---|---|---|---|
| `dma_resv_init()` | `dma-resv.c:139` | 裸 | 初始化 |
| `dma_resv_fini()` | `dma-resv.c:154` | 裸 | 析构；⚠️ 要求没人持锁 |
| `dma_resv_reserve_fences()` | `dma-resv.c:240` | 裸 | ⚠️ **必须先预留槽位再 add**，见下 |
| `dma_resv_add_fence()` | `dma-resv.c:315` | 裸 | 加一个 fence + usage |
| `dma_resv_replace_fences()` | `dma-resv.c:352` | 裸 | 按 context 替换 |
| `dma_resv_reset_max_fences()` | `dma-resv.c:261` | 裸 | 把预留计数清回实际数 |
| `dma_resv_copy_fences()` | `dma-resv.c:543` | 裸 | 整体拷贝到另一个 resv |
| `dma_resv_get_fences()` | `dma-resv.c:598` | GPL | 取出一个 fence 数组（调用者要 `kfree`） |
| `dma_resv_get_singleton()` | `dma-resv.c:651` | GPL | 把符合 usage 的 fence 合成**一个** fence |
| `dma_resv_wait_timeout()` | `dma-resv.c:688` | GPL | 等这块缓冲区上符合 usage 的全部 fence |
| `dma_resv_test_signaled()` | `dma-resv.c:717` | GPL | 非阻塞查询 |
| `dma_resv_describe()` | `dma-resv.c:739` | GPL | 往 `seq_file` 打，debugfs 用 |

⚠️⚠️ **`dma_resv_add_fence()` 不会分配内存 ⇒ 必须先 `dma_resv_reserve_fences()`。**
这是 §3.6 第 5/6 条的直接结果：add 可能发生在 signal 临界区里，那里不能分配。
正确顺序永远是：

```
dma_resv_lock()
  → dma_resv_reserve_fences(resv, n)      /* 这里可以失败，返回 -ENOMEM */
  → ... 干活 ...
  → dma_resv_add_fence(resv, f, usage)    /* 这里保证不失败 */
dma_resv_unlock()
```

漏掉 reserve 的后果是 `dma_resv_add_fence()` 里的 `BUG_ON`/越界——
**不是返回错误码**。

### §4.5 两组迭代器：锁的 vs 无锁的

| 宏/函数 | 位置 | 前提 |
|---|---|---|
| `dma_resv_iter_begin()` | `include/linux/dma-resv.h:230` | 两组共用 |
| `dma_resv_iter_end()` | `:246` | 两组共用 |
| `dma_resv_iter_usage()` | `:257` | 取当前 fence 的 usage 档 |
| `dma_resv_for_each_fence()` | `:306` | ⚠️ **要求持 `dma_resv_lock()`** |
| `dma_resv_for_each_fence_unlocked()` | `:289` | 只需 RCU |
| `dma_resv_iter_is_restarted()` | `:269` | ⚠️ **无锁版必须检查** |

⚠️⚠️ 头文件在 `:186-191` 明确警告：**无锁迭代器会重启**。
如果 fence 列表在遍历过程中被换掉，迭代器会从头开始，
**已经看过的 fence 会再来一遍**。所以：

```c
dma_resv_for_each_fence_unlocked(&iter, fence) {
        if (dma_resv_iter_is_restarted(&iter))
                my_count = 0;           /* ⚠️ 累加器必须清零重来 */
        my_count++;
}
```

**忘了这一句 = 计数偏大、数组越界写**。这是 dma-resv 用错最多的地方。
底层实现是 `dma_resv_iter_first_unlocked()`(`dma-resv.c:419`) 和
`dma_resv_iter_next_unlocked()`(`:448`)，持锁版是 `dma_resv_iter_first()`(`:472`) /
`dma_resv_iter_next()`(`:500`)。

---

## §5 fence 组合子：array / chain / unwrap（12 个导出）

三个小模块解决同一类问题：**"等多个 fence" 怎么表达成"一个 fence"**。
它们本身都是 `dma_fence`，所以可以套娃。

### §5.1 `dma_fence_array`：等全部（AND）

| 符号 | 位置 | 说明 |
|---|---|---|
| `dma_fence_array_create()` | `dma-fence-array.c:223` | 传一个 `struct dma_fence **` 数组，得到一个 fence |
| `dma_fence_array_ops` | `dma-fence-array.c:159` | ⚠️ **ops 本身被导出**，用来做类型判断 |
| `dma_fence_match_context()` | `dma-fence-array.c:249` | 数组里是否全都属于某个 context |
| `dma_fence_array_first()` / `_next()` | `:267` / `:279` | 遍历成员 |
| `to_dma_fence_array()` | `include/linux/dma-fence-array.h:55-56` | inline 向下转型 |

⚠️ **`dma_fence_array_create()` 接管数组的所有权**：成功后数组本身由 array 释放，
调用者不能再 `kfree` 它，也不能再改它。失败时**调用者要自己放掉所有 fence 引用**。

### §5.2 `dma_fence_chain`：单调递增的时间线（syncobj timeline 的底座）

| 符号 | 位置 | 说明 |
|---|---|---|
| `dma_fence_chain_init()` | `dma-fence-chain.c:265` | 把一个新节点接到链尾 |
| `dma_fence_chain_walk()` | `dma-fence-chain.c:77` | ⚠️ 顺链回溯，**会顺路做 GC**（signal 掉的节点摘掉） |
| `dma_fence_chain_find_seqno()` | `dma-fence-chain.c:110` | 找到 ≥ 指定 seqno 的那个节点 |
| `dma_fence_chain_ops` | `dma-fence-chain.c:217` | 类型判断用 |
| `dma_fence_chain_alloc()` / `_free()` | `include/linux/dma-fence-chain.h:89` / `:103` | inline，配对使用 |
| `dma_fence_chain_contained()` | `include/linux/dma-fence-chain.h:76-77` | 取出链节点里包着的真 fence |

⚠️ **chain 的 `seqno` 是用户态可以指定的任意 64 位值**，不是核心分配的。
`use_64bit_seqno`（`include/linux/dma-fence.h:132`）就是给它用的
——普通 fence 的 seqno 是 32 位环绕比较，chain 必须是 64 位严格比较。
**自己实现 fence 时把这个 bool 填错，`dma_fence_is_later()` 的比较结果就是错的**
（`include/linux/dma-fence.h:451-470`，判断分支在 `:458`）。

### §5.3 `dma_fence_unwrap`：把套娃拆平

| 符号 | 位置 | 说明 |
|---|---|---|
| `dma_fence_unwrap_first()` | `dma-fence-unwrap.c:40` | GPL |
| `dma_fence_unwrap_next()` | `dma-fence-unwrap.c:61` | GPL |
| `__dma_fence_unwrap_merge()` | `dma-fence-unwrap.c:172` | GPL；⚠️ **不要直接调** |
| `dma_fence_unwrap_for_each()` | `include/linux/dma-fence-unwrap.h:47-49` | 遍历宏 |
| `dma_fence_unwrap_merge(...)` | `include/linux/dma-fence-unwrap.h:67-72` | ⚠️ **变参宏**，这才是对外接口 |

⚠️ `dma_fence_unwrap_merge()` 是个**变参宏**，在栈上开两个数组
（`__f[]` 和 `__c[ARRAY_SIZE(__f)]`，`:69-70`）再调 `__dma_fence_unwrap_merge()`
⇒ **传的 fence 个数直接决定栈用量**，不要在里面塞几十个。

三个类型判断 inline（`include/linux/dma-fence.h`）：
`dma_fence_is_array()` `:633` · `dma_fence_is_chain()` `:644` ·
`dma_fence_is_container()` `:657`（前两者的或）。
判断依据是**比较 `fence->ops` 指针**，这就是为什么那两个 ops 结构要导出。

---

## §6 sync_file：把 fence 交给用户态（2 个导出）

| 符号 | 位置 | 说明 |
|---|---|---|
| `sync_file_create()` | `sync_file.c:77` | fence → `struct sync_file *`（内含一个 `struct file`） |
| `sync_file_get_fence()` | `sync_file.c:117` | fd → fence（带引用） |
| `sync_file_get_name()` | `include/linux/sync_file.h:60` | ⚠️ **声明了但没导出** ⇒ 外部模块用不了 |

⚠️ `sync_file` 走的是**匿名 inode**（`anon_inode_getfile("sync_file", &sync_file_fops, …)`，
`sync_file.c:31`），不是字符设备 ⇒ `/dev` 下没有它的节点，
`ls -l /proc/<pid>/fd/` 里显示成 `anon_inode:sync_file`。

用户态侧只有两个 ioctl：`SYNC_IOC_MERGE`（`include/uapi/linux/sync_file.h:86`）
和 `SYNC_IOC_FILE_INFO`（`:96`），magic 是 `'>'`（`:70`）。
官方文档 `Documentation/driver-api/sync_file.rst`（86 行）整篇就讲这一层。

⚠️ **sync_file 是 Android 带过来的**：`CONFIG_SYNC_FILE` 的 help 原文就写
"ported from Android"（`drivers/dma-buf/Kconfig:4-18`），而且 **`default n`**
⇒ **不能假定目标内核开了它**。用之前先 `grep CONFIG_SYNC_FILE /proc/config.gz`。

---

## §7 dma-heap：唯一一个 0 导出的层

| 符号 | 位置 | ⚠️ |
|---|---|---|
| `dma_heap_add()` | `include/linux/dma-heap.h:66` | 注册一个 heap；**无 `EXPORT_SYMBOL`** |
| `dma_heap_get_drvdata()` | `include/linux/dma-heap.h:51` | 同上 |
| `dma_heap_get_name()` | `include/linux/dma-heap.h:60` | 同上 |

⚠️⚠️ **`drivers/dma-buf/dma-heap.c` 一个符号都没导出**（严格口径
`grep -cE '^EXPORT_SYMBOL' ⇒ 0`）⇒ **heap 驱动只能是内建的**。
对应地，`CONFIG_DMABUF_HEAPS` 及两个 heap 都是 `bool` 而非 `tristate`
（`drivers/dma-buf/Kconfig:68-75`、`heaps/Kconfig:1-6`、`:8-14`）。
⇒ **要加一个自定义 heap，必须改内核树重编**，这是它和其他所有层的根本差别，05 §5 展开。

`struct dma_heap_ops` 只有 **1 个成员** `allocate`（`include/linux/dma-heap.h:23-28`）
⇒ heap 的实现负担极小，难点全在返回的那个 dma-buf 怎么实现
（参考 `heaps/system_heap.c` 440 行 / `heaps/cma_heap.c` 407 行）。

用户态入口：`/dev/dma_heap/<name>`（`dma-heap.c:313` 的 `alloc_chrdev_region()` +
`:317` 的 `class_create(THIS_MODULE, DEVNAME)`），
唯一 ioctl `DMA_HEAP_IOCTL_ALLOC`（`include/uapi/linux/dma-heap.h:50`，magic `'H'` 在 `:42`）。

---

## §8 错误码语义

5 个核心 `.c` 的普查（`-EINVAL` 47 · `-ENOMEM` 18 · `-EFAULT` 11 ·
`-ERESTARTSYS` 7 · `-ENOENT` 6 · `-ENOTTY`/`-EBUSY` 各 3 ·
`-EINTR`/`-EDEADLK`/`-EBADF` 各 2 · `-EOVERFLOW`/`-ENODEV`/`-EAGAIN` 各 1）。
真正需要区分语义的几个：

| 错误码 | 产生点举例 | 真实含义 |
|---|---|---|
| `-EINVAL` | `dma-buf.c:620`（export 缺必填回调）· `:706`（`dma_buf_fd()` 收到无效 dmabuf）· `:1413`（**导出方没实现 `.mmap`**）· `:1459` | 用了个不存在的能力，或参数不合法。⚠️ **47 处共用，几乎无诊断价值** |
| ⚠️ `-EDEADLK` | `dma_resv_lock()`（ww_mutex 内部）· `dma-resv.c:759` | **不是错误，是"退避重试"信号**（§4.3） |
| ⚠️ `-EDEADLK` | `dma-fence.c:554` | **完全不同的含义**：一个 fence 被销毁时还没 signal（`:539-543` 的 WARN "released with pending signals!"），核心强行给它填这个错误码再 signal 掉，避免挂死链上的等待者 ⇒ **看到这个 = 有人的 fence 引用计数写错了** |
| `-ENOENT` | `dma-fence.c:654`（fence 已 signal）· `:664` | `dma_fence_add_callback()` 对一个已完成的 fence ⇒ **不是失败，是"不用等了"**，kerneldoc 在 `:635` 明说 |
| `-ERESTARTSYS` | `dma-fence.c:773` · `:797` · `:900` | 等待被信号打断。⚠️ **必须往上传，不能吞**，否则 Ctrl-C 杀不掉进程 |
| `-EOVERFLOW` | `dma-buf.c:1417` | `dma_buf_mmap()` 的 `pgoff + vma_pages(vma)` 回绕；⚠️ **全核心唯一一处**，看到它就是这里 |
| `-EAGAIN` / `-EINTR` | `DMA_BUF_IOCTL_SYNC` 路径 | ⚠️ **uapi 契约要求用户态重试**（`dma-buf.c:1253`），不是致命错误 |
| `-ENOTTY` | `dma-buf.c:491` · `sync_file.c:363` · `dma-heap.c:174` | 未知 ioctl 号；⚠️ **也是"这个内核没这个特性"的探测手段** |
| `-ENOMEM` | `dma-resv.c:195`（`dma_resv_reserve_fences()`）等 18 处 | ⚠️ **`dma_resv_reserve_fences()` 是整条链上唯一允许失败的一步**（§4.4） |
| `-ESPIPE` | `llseek` 路径 | ⚠️ 见 §9 |

---

## §9 uapi 上两个容易踩的 ABI 细节

官方文档 `Documentation/driver-api/dma-buf.rst` 里有两段**只在文档里、代码里看不出的 ABI 规定**：

### §9.1 `llseek` 被当成特性探测手段（`dma-buf.rst:59-66`）

dma-buf 的 fd **只接受 `offset == 0` 且 `whence` 为 `SEEK_END` 或 `SEEK_SET`**，
其余组合返回 `-EINVAL`；如果内核根本不支持则返回 `-ESPIPE`。
⇒ **`lseek(fd, 0, SEEK_END)` 拿到的就是缓冲区大小**，
而 `-ESPIPE` 可以用来判断"这个内核太老"。
⚠️ 这是**唯一一个不用 ioctl 就能读到缓冲区大小的手段**。

### §9.2 `O_CLOEXEC` 必须在 `dma_buf_fd()` 时给（`dma-buf.rst:68-83`）

文档专门论证了为什么**不能**"先建 fd 再 `fcntl(F_SETFD, FD_CLOEXEC)`"：
两步之间有窗口，别的线程 `fork`+`exec` 就会把 fd 泄漏出去。
⇒ **导出 fd 时一律写 `dma_buf_fd(buf, O_CLOEXEC)`**，
用户态 ioctl 也一律带上对应的 CLOEXEC 标志
（如 `UDMABUF_FLAGS_CLOEXEC`，`include/uapi/linux/udmabuf.h:8`）。

---

## §10 近邻符号区分表

dma-buf 的命名密度很高，下面这些**名字差一两个字符、语义完全不同**：

| 容易混的 | 区别 |
|---|---|
| `dma-buf` / `dmaengine` | 本子系统 `drivers/dma-buf/` 共享缓冲区 / `drivers/dma/` 搬数据。⚠️ **两者无任何关系** |
| `dma_buf_attach()` / `dma_buf_dynamic_attach()` | 前者是后者传两个 NULL 的包装（`dma-buf.c:919` vs `:904`）；**给了 `importer_ops` 才叫动态** |
| `dma_buf_map_attachment()` / `_unlocked()` | ⚠️ **谁加 `dma_resv_lock()` 的区别**，不是快慢路径（§2.2） |
| `dma_buf_pin()` / `dma_resv_lock()` | 前者"别搬内存" / 后者"别改 fence 列表"；**两件完全不同的事** |
| `dma_fence_signal()` / `dma_fence_enable_sw_signaling()` | 前者"我完成了" / 后者"请打开软件通知路径"，**是消费者调的** |
| `dma_fence_wait()` / `dma_resv_wait_timeout()` | 等一个 fence / 等一块缓冲区上符合 usage 的**全部** fence |
| `dma_fence_get_status()` / `dma_fence_get_status_locked()` | `include/linux/dma-fence.h:544` / `:536`（inline） |
| `dma_fence_is_later()` / `dma_fence_is_later_or_same()` | `:472` / `:490`；⚠️ 两者都依赖 `use_64bit_seqno` 填对 |
| `dma_resv_usage_rw(true)` | ⚠️ **返回 `DMA_RESV_USAGE_READ`**，不是 WRITE（§4.2） |
| `dma_resv_for_each_fence()` / `_unlocked()` | ⚠️ 后者**会重启**，累加器必须查 `dma_resv_iter_is_restarted()`（§4.5） |
| `dma_resv_get_fences()` / `dma_resv_get_singleton()` | 拿一个数组（要 `kfree`） / 合成一个 fence（要 `dma_fence_put`） |
| `dma_fence_get_stub()` / `dma_fence_allocate_private_stub()` | 全局共享、时间戳是首次调用时刻 / 私有、时间戳自己给（§3.4） |
| `dma_fence_unwrap_merge()` / `__dma_fence_unwrap_merge()` | 变参宏（用这个） / 底层函数（别直接调） |
| `sync_file` / `dma_fence` | fd 化的一个 fence / fence 本身；**一个 sync_file 里只有一个 fence 字段**（可以是 array） |
| `/dev/dma_heap/*` / `/dev/udmabuf` | heap 分配器（`dma-heap.c`）/ 从 memfd 造 dma-buf（`udmabuf.c`）；**两个独立的字符设备** |
| `sw_sync` / `sync_file` | 纯软件测试 timeline（debugfs，⚠️ 能让内核死锁）/ 生产用的 fence fd |
| `DMA_BUF_SET_NAME` / `_A` / `_B` | ⚠️ **同一个 ioctl 号的三个别名**（`include/uapi/linux/dma-buf.h:176-178`），compat 用 |

---

## §11 符号存在性核实

按"grep 命中 = 存在"、"有 `EXPORT_SYMBOL*` 且在 `include/linux/` 头里声明 = 外部驱动可用"的口径：

| 符号 | 存在 | 外部可用 | 附加条件 |
|---|---|---|---|
| `dma_buf_export` / `_fd` / `_get` / `_put` | ✅ | ✅ | ⚠️ 需 `MODULE_IMPORT_NS(DMA_BUF)` |
| `dma_buf_attach` / `_detach` / `_map_attachment*` | ✅ | ✅ | 同上 |
| `dma_buf_vmap` / `_vunmap` / `_mmap` | ✅ | ✅ | 同上 |
| `dma_fence_init` / `_signal*` / `_wait*` / `_add_callback` | ✅ | ✅ | 无（裸 `EXPORT_SYMBOL`） |
| `dma_resv_init` / `_add_fence` / `_reserve_fences` | ✅ | ✅ | 无 |
| `dma_resv_wait_timeout` / `_get_fences` / `_get_singleton` | ✅ | ✅ | 需 GPL 兼容许可 |
| `dma_fence_unwrap_*` | ✅ | ✅ | 需 GPL |
| `sync_file_create` / `sync_file_get_fence` | ✅ | ✅ | 需 `CONFIG_SYNC_FILE=y`（⚠️ `default n`） |
| `sync_file_get_name` | ✅ | ❌ | ⚠️ **声明了但没导出** |
| `dma_heap_add` / `_get_name` / `_get_drvdata` | ✅ | ❌ | ⚠️ **一个都没导出 ⇒ heap 必须内建** |
| `dma_buf_stats_setup` / `_teardown` | ✅ | ❌ | 核心私有（`dma-buf-sysfs-stats.c:171` / `:121`） |
| `dma_buf_move_notify` | ✅ | ✅ | ⚠️ 只在 `CONFIG_DMABUF_MOVE_NOTIFY=y` 下有实际效果 |
| `dma_fence_begin_signalling` / `_end_signalling` | ✅ | ✅ | ⚠️ `CONFIG_LOCKDEP=n` 时是空桩（`include/linux/dma-fence.h:362`/`:366`） |
| `dma_buf_try_map_attachment` | ❌ | ❌ | ⚠️ **不存在**；`include/linux/dma-buf.h:193` 有一条 `TODO` 说想加 |

⚠️ 最后一行是本子系统**全核心唯一一条 `FIXME/TODO/XXX`**
（`include/linux/dma-buf.h:193`：`TODO: Add try_map_dma_buf version, to return immed with -EBUSY`）
⇒ **没有非阻塞的 map 接口**，`dma_buf_map_attachment()` 一定可能睡。

---

## §12 接着读

- 上面每个结构体的逐字段解释 → [02-datastructures.md](02-datastructures.md)
- `dma_resv` 的 ww_mutex + RCU 实现、fence 状态机 → [03-internals.md](03-internals.md)
- heap / udmabuf / sw_sync / sysfs 统计层 → [04-extensions.md](04-extensions.md)
- 导出方 / 导入方 / fence 实现方的可编译骨架 → [05-usage.md](05-usage.md)
- 两个 debugfs 文件和 8 个 tracepoint → [07-debug.md](07-debug.md)
- 为什么 fence 必须有限时间完成 → [08-feynman.md](08-feynman.md)
