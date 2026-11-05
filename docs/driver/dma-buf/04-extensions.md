---
title: 扩展机制
---

# dma-buf 04：扩展与周边

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

01–03 讲的是**核心四层**（`dma-buf.c` / `dma-fence.c` / `dma-resv.c` + 组合器）。
本篇讲挂在核心之上、**全部由 Kconfig 开关控制**的五个可选层。

## §0 先看 Makefile：谁是必编的，谁是可选的

`drivers/dma-buf/Makefile` 18 行，一眼看清依赖：

| 目标 | Makefile 行 | 条件 |
|---|---|---|
| `dma-buf.o dma-fence.o dma-fence-array.o dma-fence-chain.o dma-fence-unwrap.o dma-resv.o` | `:2-3` | ⚠️ **`obj-y`，无条件编入** |
| `dma-heap.o` + `heaps/` | `:4-5` | `CONFIG_DMABUF_HEAPS` |
| `sync_file.o` | `:6` | `CONFIG_SYNC_FILE` |
| `sw_sync.o sync_debug.o` | `:7` | ⚠️ `CONFIG_SW_SYNC` —— **两个文件同一个开关** |
| `udmabuf.o` | `:8` | `CONFIG_UDMABUF` |
| `dma-buf-sysfs-stats.o` | `:9` | `CONFIG_DMABUF_SYSFS_STATS`（DEPRECATED） |
| `dmabuf_selftests.o`（5 个 `.c`） | `:11-18` | `CONFIG_DMABUF_SELFTESTS` |

⚠️⚠️ **核心六个 `.c` 是 `obj-y`，没有 `CONFIG_DMA_SHARED_BUFFER` 门控**
——`dma-buf.rst:49-50` 却写着驱动 "must have a 'select DMA_SHARED_BUFFER'"。
⇒ 那个 Kconfig 符号在 6.1 里已经不控制编译，**select 它只是历史习惯**。
`dma_buf_*` 符号在任何配置下都存在。

⚠️ **`sync_debug.c` 跟着 `CONFIG_SW_SYNC` 走**（`:7`）
⇒ `/sys/kernel/debug/sync/info` 这个**只读的诊断文件也只在开了 SW_SYNC 时才有**，
而 SW_SYNC 的 help 自陈是 "test and debug only" 且能被用户态用来卡死驱动。
⇒ ⚠️ **想在生产内核上看 sync 状态是做不到的**（07 §2）。

---

## §1 dma-heap：用户态分配 dma-buf 的入口

`drivers/dma-buf/dma-heap.c`（326 行，⚠️ **0 个导出符号**）。

### §1.1 全局状态与设备号

| 项 | 行 |
|---|---|
| `struct dma_heap`（真正的定义） | `:37-44` |
| `heap_list` + `heap_list_lock` | `:46` / `:47` |
| `dma_heap_devt` | `:48` |
| `dma_heap_class` | `:49` |
| `dma_heap_minors`（xarray） | `:50` |

⚠️ `struct dma_heap` **只在 `.c` 里定义**，头文件里只有前向声明
（`include/linux/dma-heap.h:15`，02 §7）
⇒ **本子系统里唯一做了完整封装的类型**，heap 实现只能通过
`dma_heap_get_drvdata()`（`:202`）和 `dma_heap_get_name()`（`:214`）访问。

`dma_heap_init()`（`:309-324`，`subsys_initcall` 在 `:326`）：

```
313  alloc_chrdev_region(&dma_heap_devt, 0, NUM_HEAP_MINORS, DEVNAME)
317  class_create(THIS_MODULE, DEVNAME)
322  dma_heap_class->devnode = dma_heap_devnode;
```

⚠️ 关键在 `dma_heap_devnode()`（`:304-307`）：

```c
306:  return kasprintf(GFP_KERNEL, "dma_heap/%s", dev_name(dev));
```

⇒ **设备节点路径是 `/dev/dma_heap/<heap 名>`**，那个子目录是 `devnode` 回调
拼出来的，不是 class 名。**heap 名直接成为 uapi 路径**。

### §1.2 `dma_heap_add()` 的五道校验 + 一个竞态窗口

`:219-302`：

```
226-229  name 为 NULL 或空串 → pr_err + -EINVAL
231-234  ops 或 ops->allocate 为 NULL → pr_err + -EINVAL
236-238  kzalloc → -ENOMEM
245-246  xa_alloc 分配次设备号，上限 NUM_HEAP_MINORS
         cdev_init/cdev_add
264-268  device_create(dma_heap_class, …, heap->name)
275-289  持 heap_list_lock 查重名 → 重名 -EINVAL；否则挂链表
```

⚠️⚠️ **重名检查在 `device_create()` 之后**（`:275` vs `:264`）
⇒ 注册一个重名 heap 会**先在 `/dev/dma_heap/` 下建出节点，再回滚删掉**
（错误路径 `:293-294`）。窗口极短且只在启动期，但意味着
**重名的代价不是"被拒绝"而是"建了又删"**。

⚠️ 错误路径 `:293-300` 是标准的四级 `goto` 阶梯（err3→err0），
**唯一没有清理的是 `heap->name`** ——因为它是**直接存的调用者指针**
（`:240` `heap->name = exp_info->name;`，没有 `kstrdup`）
⇒ ⚠️⚠️ **heap 实现必须保证 `exp_info.name` 指向的字符串在 heap 生命周期内一直有效**。
CMA heap 就靠这一点：`cma_get_name(cma)` 返回的是 CMA 区域的长期字符串
（`heaps/cma_heap.c:380`）。

⚠️ **没有 `dma_heap_remove()`** ——`dma_heap_add()` 是**单向的**。
这与 `dma-heap.c` 零导出符号一起说明：**heap 只能内建或由永不卸载的模块注册**
（01 §7 的结论）。

### §1.3 ioctl 的"未来兼容"外壳

`dma_heap_ioctl()`（`:126-184`）不是普通的 `switch`，它抄的是 DRM 的做法：

```
129   char stack_kdata[128];              ← 小参数用栈
133   nr = _IOC_NR(ucmd)
136-137  nr 越界 → -EINVAL
139   array_index_nospec(...)             ⚠️ 防 Spectre v1
141   kcmd = dma_heap_ioctl_cmds[nr]      ← 表在 :122-124，目前只有 1 项
144-151  drv_size / in_size / out_size → ksize = 三者最大
154-158  ksize > 128 → kmalloc，失败 -ENOMEM
160-163  copy_from_user(kdata, arg, in_size) → -EFAULT
166-167  ⚠️ ksize > in_size 时把多出来的部分 memset 0
169-176  switch (kcmd)：只有 DMA_HEAP_IOCTL_ALLOC；default -ENOTTY
178-179  copy_to_user(arg, kdata, out_size) → -EFAULT
```

⚠️⚠️ **`:144-151` + `:166-167` 是这套外壳的全部价值**：
**用户态结构体比内核的小时，内核把差额补零**
⇒ **老用户态程序在新内核上不用重编译就能工作**，新增字段自动是 0。
这是 dma-heap 唯一一个 ioctl 却写了 60 行外壳的原因。

⚠️ 代价：**`_IOC_SIZE` 不匹配不会报错，会被静默补零**
⇒ 用户态传错结构体大小的 bug 会表现成"参数莫名是 0"，而不是 `-EINVAL`。

⚠️ **`:139` 的 `array_index_nospec()`** 说明这条路径被当成
**用户可控索引的推测执行边界**来对待。

### §1.4 `DMA_HEAP_IOCTL_ALLOC` 的三道校验

`dma_heap_ioctl_allocate()`（`:96-120`）：

| 检查 | 行 | 含义 |
|---|---|---|
| `heap_allocation->fd` 必须为 0 | `:102-103` | ⚠️ **输出字段必须传 0** |
| `fd_flags & ~DMA_HEAP_VALID_FD_FLAGS` | `:105-106` | 只允许 `O_CLOEXEC \| O_ACCMODE`（`uapi/dma-heap.h`） |
| `heap_flags & ~DMA_HEAP_VALID_HEAP_FLAGS` | `:108-109` | ⚠️ **目前合法值只有 0** |

⚠️ 第一条容易踩：**用户态复用同一个 `struct dma_heap_allocation_data` 连续分配
第二次一定失败**（第一次的返回 fd 还在 `fd` 字段里）⇒ 每次要清零。

成功后 `:117` 把 fd 写回 `heap_allocation->fd`，由 §1.3 的 `copy_to_user` 带出去。

`dma_heap_open()`（`:79`）把 `file->private_data` 设成 heap 本身
⇒ **一个 fd 就绑定一个 heap，ioctl 不需要再指定 heap**。

---

## §2 两个内建 heap

### §2.1 system heap：按 1M/64K/4K 三档降级

`drivers/dma-buf/heaps/system_heap.c`（440 行）。

⚠️ 核心是两个数组的配对（`:43-56`）：

```c
43:  #define LOW_ORDER_GFP  (GFP_HIGHUSER | __GFP_ZERO | __GFP_COMP)
44:  #define MID_ORDER_GFP  (LOW_ORDER_GFP | __GFP_NOWARN)
45:  #define HIGH_ORDER_GFP (((GFP_HIGHUSER | __GFP_ZERO | __GFP_NOWARN
46:                            | __GFP_NORETRY) & ~__GFP_RECLAIM) | __GFP_COMP)
48:  static gfp_t order_flags[] = {HIGH_ORDER_GFP, MID_ORDER_GFP, LOW_ORDER_GFP};
55:  static const unsigned int orders[] = {8, 4, 0};
```

⚠️⚠️ **GFP 标志是按 order 递减放宽的**：

| order | 大小（4K 页） | GFP | 语义 |
|---|---|---|---|
| 8 | **1 MB** | `HIGH_ORDER_GFP` | ⚠️ `__GFP_NORETRY` + **清掉 `__GFP_RECLAIM`** ⇒ **不回收、拿不到就走** |
| 4 | **64 KB** | `MID_ORDER_GFP` | 允许回收，但 `__GFP_NOWARN` |
| 0 | 4 KB | `LOW_ORDER_GFP` | ⚠️ **会回收、会 warn、可能触发 OOM** |

`:49-53` 的注释给了理由：**"designed to match with the sizes often found in
IOMMUs … can significantly improve the performance of many IOMMUs by reducing
TLB pressure and time spent updating page tables"**
⇒ **这三档不是内存效率优化，是 IOMMU TLB 优化。**

`alloc_largest_available()`（`:315-333`）的降级循环：
剩余大小不够就跳过这一档（`:322-323`），`max_order` 限制（`:324-325`），
分配失败也只是 `continue`（`:328-329`）⇒ **最终一定退到 order 0**。

⚠️ **arm64 上 order 8 的含义随页大小变**：4K 页 ⇒ 1 MB；
**64K 页配置下 order 8 = 16 MB**，`orders[]` 是硬编码的 order 而不是字节数
⇒ ⚠️ **64K 页内核上 system heap 首选块是 16 MB，几乎一定拿不到**，
实际总是退到 order 4（4 MB）或 0。这是一个**页大小相关的隐式行为差异**。

注册（`:425-438`，`module_init` 在 `:439`）：

```c
429:  exp_info.name = "system";       ⇒ /dev/dma_heap/system
```

⚠️ `:427` 是 `struct dma_heap_export_info exp_info;`
——**没有初始化器**，三个字段全部手填（`:429-431`）。
只有 3 个字段所以刚好填满；这个写法在字段增加时会出问题（02 §7）。

### §2.2 CMA heap：只包默认 CMA 区

`drivers/dma-buf/heaps/cma_heap.c`（407 行）。

注册路径（`:395-404`，`module_init` 在 `:405`）：

```c
397:  struct cma *default_cma = dev_get_cma_area(NULL);
400-401:  if (default_cma) ret = __add_cma_heap(default_cma, NULL);
```

⚠️⚠️ **只注册"默认 CMA 区"这一个 heap**，而且**没有默认 CMA 区时静默返回 0**
（`:398` `int ret = 0;` + `:400` 的 if）
⇒ **`/dev/dma_heap/` 下没有 CMA heap 不是错误，是"内核没配 CMA 区"**。
⚠️ **`__add_cma_heap()`（`:370`）是 static 的，没有任何其它调用者**
⇒ **无法为第二个 CMA 区注册 heap**（多 CMA 区的板子上只有默认区可用）。

⚠️ heap 名字来自 `cma_get_name(cma)`（`:380`）
⇒ **`/dev/dma_heap/` 下的节点名等于 DT 里那个 `reserved-memory` 子节点名**
（或 `cma` 这个默认名）——06 篇的主线就是这条链。

### §2.3 两个 heap 的 `dma_buf_ops` 对比

| 回调 | system heap | cma heap |
|---|---|---|
| `attach` / `detach` | `:83` / `:114` | `:50` / `:83` |
| `map_dma_buf` / `unmap` | `:129` / `:144` | `:97` / `:111` |
| `begin_cpu_access` / `end` | `:154` / `:175` | `:121` / `:142` |
| `mmap` | `:196` | `:181` ⚠️ 走 `vm_fault`（`:163`） |
| `vmap` / `vunmap` | `:244` / `:272` | `:205` / `:232` |
| `release` | `:285` | `:245` |
| ops 表 | `:302-313` | `:263-274` |

⚠️ **两者都实现了全部 10 个可选回调** ⇒ 它们是**写导出方时最好的模板**
（05 §1 直接引用）。
⚠️ 唯一的实现差异在 `mmap`：**system heap 直接建映射，
CMA heap 用 `vm_fault` 惰性建**（`dma_heap_vm_ops` 在 `:177-179`）。
⚠️⚠️ 两个文件里**各自定义了一个同名的 `struct dma_heap_attachment`**
（`system_heap.c:36` / `cma_heap.c:43`）——**互不相干的私有结构，重名巧合**，
读代码时不要混。

---

## §3 udmabuf：把 memfd 变成 dma-buf

`drivers/dma-buf/udmabuf.c`（401 行，`CONFIG_UDMABUF`）。

用途：**虚拟化**。宿主机 QEMU 拿到客户机的 memfd，包成 dma-buf 交给
GPU/显示做零拷贝。`MODULE_AUTHOR` 是 Gerd Hoffmann（`:400`），virtio 显示的作者。

### §3.1 两个模块参数就是它的全部策略

```c
16:  static int list_limit = 1024;   module_param(..., 0644)   (:17)
20:  static int size_limit_mb = 64;  module_param(..., 0644)   (:21)
```

| 参数 | 默认 | 含义 | 校验点 |
|---|---|---|---|
| `list_limit` | 1024 | `UDMABUF_CREATE_LIST` 的 `count` 上限 | `udmabuf.c:317` 起 |
| `size_limit_mb` | ⚠️ **64** | **单个 dma-buf 的最大 MB 数** | `:188` 算成页数，`:195-196` 比较 |

⚠️⚠️ **默认单缓冲区上限只有 64 MB** ——一个 4K 显示 framebuffer
（3840×2160×4 ≈ 33 MB）勉强够，双缓冲就超了。
⇒ **`/sys/module/udmabuf/parameters/size_limit_mb` 是运行时可写的（0644）**，
这是 udmabuf 部署时几乎一定要调的第一个旋钮。

### §3.2 memfd 的封条（seal）要求

`:165-166`：

```c
165:  #define SEALS_WANTED (F_SEAL_SHRINK)
166:  #define SEALS_DENIED (F_SEAL_WRITE|F_SEAL_FUTURE_WRITE)
```

校验在 `:218-224`：

```
218  seals = memfd_fcntl(memfd, F_GET_SEALS, 0)
219-220  == -EINVAL（不支持封条）→ 失败
222-223  (seals & SEALS_WANTED) != SEALS_WANTED ||
         (seals & SEALS_DENIED) != 0   → -EINVAL
```

⚠️⚠️ **必须已经 `F_SEAL_SHRINK`，且必须没有任何写封条**：

| 要求 | 为什么 |
|---|---|
| **要有** `F_SEAL_SHRINK` | 保证 memfd **不会被截短** ⇒ 页不会在 dma-buf 存续期间消失 |
| **不能有** `F_SEAL_WRITE` / `F_SEAL_FUTURE_WRITE` | ⚠️ dma-buf 要能被**写**（GPU 渲染进去） |

⇒ 用户态的正确姿势：`memfd_create(…, MFD_ALLOW_SEALING)` →
`fcntl(fd, F_ADD_SEALS, F_SEAL_SHRINK)` → `UDMABUF_CREATE`。
**多加一个 `F_SEAL_WRITE` 就会 `-EINVAL`。**

⚠️ `:216-217` 还限定了后备类型：**只接受 shmem（`shmem_mapping()`）或
hugetlbfs（`is_file_hugepages()`）** ⇒ 普通文件 fd 一律拒绝。

### §3.3 `udmabuf_create()`：两遍扫描 + 页级 pin

`:168-296`。

**第一遍**（`:189-197`）只做校验和计数：
offset 和 size 都必须页对齐（`:190-193`），累计页数超 `pglimit` 就失败（`:195-196`）。
⚠️ **页数为 0 也失败**（`:199-200`）。

**第二遍**（`:210-269`）真正取页：

```
212  fget(list[i].memfd)  → 失败 -EBADFD (:211)
216-217  必须 shmem 或 hugetlb
218-224  封条校验（§3.2）
227-233  hugetlb 分支：换算 huge page 的 pgoff / subpgoff / maxsubpgs
234-262  逐页取：
           hugetlb → find_get_page_flags(FGP_ACCESSED) (:237) + 手工切子页 (:244-252)
           shmem   → shmem_read_mapping_page() (:254)
261      ubuf->pages[pgbuf++] = page;      ⚠️ 每页一个 struct page 指针
263  fput(memfd)
```

⚠️⚠️ **`ubuf->pages` 是一个逐页的 `struct page *` 数组**（`:26`，
`kmalloc_array` 在 `:202`）
⇒ **一个 64 MB 缓冲区在 4K 页下要 16384 个指针 = 128 KB 的 `kmalloc_array`**。
这是 `size_limit_mb` 存在的直接原因：**上限保护的是这个数组，不是缓冲区本身**。
⚠️ arm64 64K 页配置下页数少 16 倍，同样的 `size_limit_mb` 压力小得多。

导出（`:271-286`）：

```c
271-274:  exp_info.ops = &udmabuf_ops;  size = pagecount << PAGE_SHIFT;
          priv = ubuf;  flags = O_RDWR;
277:      buf = dma_buf_export(&exp_info);
283-285:  head->flags & UDMABUF_FLAGS_CLOEXEC → flags |= O_CLOEXEC
286:      return dma_buf_fd(buf, flags);
```

⚠️ **`O_CLOEXEC` 在 `dma_buf_fd()` 时就给**（`:286`）——正是
`dma-buf.rst:68-83` 论证的那条规则（03 §D3.2）：事后 `fcntl` 有竞态。
`UDMABUF_FLAGS_CLOEXEC` 定义在 `include/uapi/linux/udmabuf.h:8`。

⚠️ 错误路径 `:288-295` 用 `while (pgbuf > 0) put_page(ubuf->pages[--pgbuf]);`
**逐页回滚** ——这是"页级 pin"设计必须付的代价。

### §3.4 udmabuf 的 `dma_buf_ops`：一个"页可见"的导出方

`udmabuf_ops`（`:155-166`）。⚠️ 与两个 heap 不同，**udmabuf 是少数
真的按页工作的导出方**：

| 回调 | 行 | 特点 |
|---|---|---|
| `mmap` | `:48-58` | ⚠️ **必须 `MAP_SHARED`**（`:52-53` 检查 `VM_SHARED\|VM_MAYSHARE`，否则 -EINVAL） |
| `vm_fault` | `:31-42` | 越界 `VM_FAULT_SIGBUS`（`:37-38`），否则从 `pages[pgoff]` 取（`:39-40`） |
| `map_dma_buf` | `:94` | 转调 `get_sg_table()`（`:60`）——**每个 attachment 建一份新的 sg_table** |
| `begin/end_cpu_access` | `:122` / `:142` | 有实现 ⇒ SYNC ioctl 有效 |
| `release` | `:107` | 逐页 `put_page` |

⚠️⚠️ **`mmap` 强制 `MAP_SHARED`**：`mmap(…, MAP_PRIVATE, …)` 返回 `-EINVAL`。
因为 `MAP_PRIVATE` 会做 CoW，写进去的数据设备看不到——**这是把
"共享语义"编码进了参数校验**。

### §3.5 初始化里的 DMA 掩码

`udmabuf_dev_init()`（`:371-390`，`module_init` 在 `:397`）：

```
375  misc_register(&udmabuf_misc)    ⇒ /dev/udmabuf（MISC_DYNAMIC_MINOR, :366）
381-382  dma_coerce_mask_and_coherent(udmabuf_misc.this_device, DMA_BIT_MASK(64))
```

⚠️⚠️ **`dma_coerce_mask_and_coherent(… , 64)` 是强行宣称"这个设备能寻址 64 位"**。
udmabuf 自己不做 DMA，这个掩码是给 `dma_map_sgtable()` 用的
⇒ **udmabuf 假定所有导入方都能访问任意物理地址**。
⚠️ **在有寻址限制的 arm64 SoC 上（比如某些 DMA 只能访问低 32 位），
从 udmabuf 导入的缓冲区可能落在设备访问不到的地址上** ——
框架不会报错，表现为设备读到垃圾。

⚠️ `udmabuf_fops`（`:357-363`）**只有 `unlocked_ioctl` 和 `compat_ioctl`**
——没有 open/release/read/write。`/dev/udmabuf` 是纯 ioctl 设备。
`udmabuf_ioctl()`（`:338-355`）两个 case，`default` → `-ENOTTY`（`:351`）。

---

## §4 sync_file：把 fence 变成 fd

`drivers/dma-buf/sync_file.c`（372 行，`CONFIG_SYNC_FILE`，⚠️ **default n**）。

来源：`drivers/dma-buf/Kconfig:4-18` 明说是从 Android 移植过来的
（"ported from Android"），配套文档 `Documentation/driver-api/sync_file.rst`（86 行）。

### §4.1 三个导出点，两个内核可用

| 函数 | 行 | 导出 |
|---|---|---|
| `sync_file_create()` | `:65` | `EXPORT_SYMBOL` `:77` |
| `sync_file_get_fence()` | `:103` | `EXPORT_SYMBOL` `:117` |
| `sync_file_get_name()` | `:132` | ⚠️ **没有导出** |

⇒ 内核侧只有"**fence → sync_file**"和"**fd → fence**"两个方向的转换。
`sync_file_get_name()` 虽然在头文件里有原型（`include/linux/sync_file.h:60`），
**外部模块链接不上**（01 §11 已核实）。

⚠️ `sync_file_fdget()`（`:79`）是私有的：它**验证 `file->f_op == &sync_file_fops`**
⇒ 传一个普通 fd 给 `sync_file_get_fence()` 会得到 NULL，不会误解析。

### §4.2 poll 的实现：一个回调 + 一个 flag 位

`sync_file_poll()`（`:191-206`）配 `fence_check_cb_func()`（`:47-63`）。
`struct sync_file` 里的 `flags`（02 §6）只用一个位：
**POLL_ENABLED** —— 第一次 poll 时才 `dma_fence_add_callback()`，
避免每次 poll 都挂一个回调。

⚠️ 这与 dma-buf 的两槽方案（03 §A6）是同一思路的简化版：
**sync_file 只有一个 fence，所以只需要一个槽。**

### §4.3 `SYNC_IOC_MERGE`：合并两个 fence

`sync_file_ioctl_merge()`（`:207-260`）→ `sync_file_merge()`（`:159-177`）。
ioctl 号 `SYNC_IOC_MERGE` 在 `include/uapi/linux/sync_file.h:86`，
magic `SYNC_IOC_MAGIC '>'` 在 `:70`。

⚠️ 合并结果是一个 **`dma_fence_array`**（01 §5）
⇒ **合并 N 次会得到嵌套的容器**，用 `dma_fence_unwrap_for_each` 才能拆平。
⚠️⚠️ **只有合并出来的 sync_file 才有 `user_name`**（02 §6）
⇒ `SYNC_IOC_FILE_INFO` 报的名字对"原生"sync_file 和"合并"sync_file
来源不同：前者取 fence 的 timeline 名，后者取用户给的名。

### §4.4 `SYNC_IOC_FILE_INFO`：两遍调用协议

`sync_file_ioctl_fence_info()`（`:279-348`）+ `sync_fill_fence_info()`（`:262-277`）。
ioctl 号在 `include/uapi/linux/sync_file.h:96`。

⚠️ 协议：**先传 `num_fences = 0` 问个数，内核填回真实数量；
再按这个数量分配数组，第二次调用取内容。**
`:291`/`:331`/`:340` 三处 `-EFAULT`，`:294`/`:314` 两处 `-EINVAL`。

⚠️ `sync_file_ioctl()`（`:350-365`）的 `default` → `-ENOTTY`（`:363`）。

⚠️⚠️ **整个 sync_file 的 ABI 只在 `CONFIG_SYNC_FILE=y` 时存在，
而它是 `default n`** ⇒ 桌面/服务器发行版通常开着（DRM 需要），
**嵌入式 arm64 内核经常是关的**。配套地，
`DMA_BUF_IOCTL_{EXPORT,IMPORT}_SYNC_FILE` 也一起消失（03 §A5）。

---

## §5 sw_sync：一个能被用户态卡死内核的调试工具

`drivers/dma-buf/sw_sync.c`（412 行）+ `sync_debug.c`（190 行），
同受 `CONFIG_SW_SYNC` 控制（Makefile `:7`）。

### §5.1 先看 Kconfig 的自陈

`drivers/dma-buf/Kconfig:20-31`，help 原文的关键句：

> **"can result in deadlocking kernel drivers from userspace … test and debug only"**

⚠️⚠️ **这不是保守措辞，是准确描述**。原因见 §5.4。

### §5.2 用法：打开一个 debugfs 文件就是造一条时间线

文件头的 DOC 块（`sw_sync.c:20-48`）把用法写全了：

```
26-28   打开 <debugfs>/sync/sw_sync 就创建一条 sync timeline
30-31   在这个 fd 下创建的所有 fence 都属于这条 timeline
33-34   ⚠️ sw_sync 可以被打开多次，得到多条互不相干的 timeline
36-37   SW_SYNC_IOC_CREATE_FENCE 造 fence
39-42   SW_SYNC_IOC_INC 推进计数器，signal 所有 seqno ≤ 新值的 fence
```

⚠️ **"打开文件 = 创建对象"这个模式在内核里很少见**。
`sw_sync_debugfs_open()`（`:293-307`）里：

```c
298:  get_task_comm(task_comm, current);
300:  obj = sync_timeline_create(task_comm);   ⚠️ timeline 名字 = 进程名
304:  file->private_data = obj;
```

⇒ **`/sys/kernel/debug/sync/info` 里看到的 timeline 名就是打开它的进程名**。

### §5.3 私有 uapi：不在 `include/uapi` 里

⚠️⚠️ **sw_sync 的 ioctl 定义在 `.c` 文件里，不在 uapi 头文件里**：

| 项 | 位置 |
|---|---|
| `struct sw_sync_create_fence_data` | `sw_sync.c:49-53` |
| `SW_SYNC_IOC_MAGIC 'W'` | `sw_sync.c:55` |
| `SW_SYNC_IOC_CREATE_FENCE` | `sw_sync.c:57-58` |
| `SW_SYNC_IOC_INC` | `sw_sync.c:60` |

⇒ **用户态测试程序必须自己抄一份结构体定义**（内核自己的
`tools/testing/selftests/sync/` 就是这么干的）。
⚠️ 这是**故意的**：把它放进 uapi 就等于承诺 ABI 稳定，而这是个调试接口。

### §5.4 为什么它能卡死内核

`sync_timeline_signal()`（`:192-221`）：

```c
197:  trace_sync_timeline(obj);           ← 唯一的 tracepoint 触发点
199:  spin_lock_irq(&obj->lock);
201:  obj->value += inc;
203-213:  遍历 pt_list，凡是 seqno ≤ value 的：
              dma_fence_get() (:207) → 移到局部链表 (:209)
              → rb_erase (:210) → dma_fence_signal_locked() (:212)
205:      ⚠️ 遇到第一个未完成的就 break（链表是有序的）
215:  spin_unlock_irq
217-220:  在锁外逐个 dma_fence_put()
```

⚠️ 实现本身是正确的（**先搬到局部链表再在锁外 put**，避免 release 回调
在持锁时被调用）。危险不在这里，在**语义**：

⚠️⚠️ **fence 什么时候完成，完全由用户态决定** ——
用户态创建一个 seqno 很大的 fence，交给某个内核驱动去等，然后
**永远不调 `SW_SYNC_IOC_INC`**。驱动就永远等在那里。
这直接违反 01 §3.1 的第一条 fence 不变量（**必须在有界时间内完成**）
⇒ **fence 契约在这里被交给了不可信方**。

⚠️ `sw_sync_ioctl_inc()`（`:373-388`）有个细节：

```c
380-383:  while (value > INT_MAX) { sync_timeline_signal(obj, INT_MAX); value -= INT_MAX; }
```

⇒ **一次 INC 传超过 `INT_MAX` 的值会被拆成多次调用** ——防止 `int` 溢出。

### §5.5 关闭 fd 的救场：`-ENOENT`

`sw_sync_debugfs_release()`（`:309-325`）：

```c
316-319:  遍历所有未完成的 pt：
              dma_fence_set_error(&pt->base, -ENOENT);   ⚠️
              dma_fence_signal_locked(&pt->base);
```

⚠️⚠️ **关掉 `sw_sync` fd 会把所有挂着的 fence 强制 signal 成 `-ENOENT`**
⇒ 这是 §5.4 那个死锁的**唯一逃生口**：杀掉持有 fd 的进程，内核就解开了。
⚠️ 与 03 §B5 的 `-EDEADLK` 是同一类设计（**发现要出事就强制唤醒并留错误码**），
但这里 errno 不同：**`-ENOENT` = "造这个 fence 的人跑了"**。

### §5.6 `timeline_fence_ops`：sw_sync 的 fence 实现

`:174-190`，是学习 `dma_fence_ops` 的最佳样例（9 个成员填了 7 个）：

| 回调 | 行 |
|---|---|
| `get_driver_name` | `:119` |
| `get_timeline_name` | `:124` |
| `signaled` | `:148` |
| `enable_signaling` | `:155` ⚠️ **直接返回 true**（纯软件，无需开中断） |
| `release` | `:131` |
| `fence_value_str` | `:160` |
| `timeline_value_str` | `:166` |

⚠️ **`enable_signaling` 恒返回 `true`** ——对照 03 §B3，返回 `false`
才意味着"已完成"。纯软件 fence 没有"开中断"这一步，所以什么都不用做。

⚠️ 后两个 `*_value_str` 回调**只被 `sync_debug.c` 和
`dma_fence_describe()` 用来打印**（03 §D3.4）
⇒ 不实现它们不影响功能，只影响 debugfs 输出可读性。

### §5.7 `sync_debug.c`：两条链表 + 两个 debugfs 文件

| 项 | 行 |
|---|---|
| `dbgfs` | `:11` |
| `sync_timeline_list_head` + 锁 | `:13` / `:14` |
| `sync_file_list_head` + 锁 | `:15` / `:16` |
| `sync_timeline_debug_add` / `_remove` | `:18` / `:27` |
| `sync_file_debug_add` / `_remove` | `:36` / `:45` |
| `sync_status_str()` | `:54` |
| `sync_print_fence` / `_obj` / `_sync_file` | `:65` / `:107` / `:121` |
| `sync_info_debugfs_show()` | `:141` |
| `sync_debugfs_init()` | `:174`（`__init`） |

⚠️ **`struct sync_file` 里那个 `#ifdef CONFIG_DEBUG_FS` 的
`sync_file_list` 字段（02 §6）就是挂在这里的 `sync_file_list_head` 上**
⇒ 这是"条件编译字段"的完整闭环：`CONFIG_SW_SYNC=n` 时字段和链表一起消失。

---

## §6 selftests：2184 行，全在 fence 层

`CONFIG_DMABUF_SELFTESTS`（`drivers/dma-buf/Kconfig:63-66`），Makefile `:11-18`。

| 文件 | 行数 | 测什么 |
|---|---|---|
| `st-dma-fence-chain.c` | **710** | `dma_fence_chain` 的 walk / find_seqno |
| `st-dma-fence.c` | **593** | fence 生命周期、signal、回调、wait |
| `st-dma-fence-unwrap.c` | 382 | 容器展开 |
| `st-dma-resv.c` | 316 | dma-resv 的 reserve/add/iterate |
| `selftest.c` | 167 | 框架（子测试注册与运行） |
| `selftests.h` | 16 | 子测试清单宏 |

⚠️⚠️ **没有一个 selftest 测 `dma_buf_export/attach/map`** ——
**缓冲区共享层完全没有自测**，覆盖的全是 fence/resv 这三层。
⇒ 改 `dma-buf.c` 时 `CONFIG_DMABUF_SELFTESTS` **帮不上忙**，
回归要靠 DRM 的 IGT 测试套（内核树外）。

⚠️ 行数分布本身是个信号：**`dma_fence_chain` 用了 710 行来测**，
是最大的一块 ⇒ 03 §C 之外，chain 的 `walk`/`find_seqno` 语义
是这套代码里最容易写错的部分。

⚠️ selftest 是**模块形式**（`dmabuf_selftests.o`，Makefile `:18`）
⇒ 运行方式是 `modprobe dmabuf_selftests`，结果看 dmesg。

---

## §7 六个可选层的取舍建议

| 层 | Kconfig | 默认 | 生产内核该不该开 |
|---|---|---|---|
| dma-heap + 两个 heap | `DMABUF_HEAPS` | n | ⚠️ **用户态要直接分配 dma-buf 就必须开**；只在内核内共享则不需要 |
| sync_file | `SYNC_FILE` | n | ⚠️ **有 DRM/V4L2 显式同步需求就必须开**；否则两个 dma-buf ioctl 也一起没有 |
| udmabuf | `UDMABUF` | n | 只有虚拟化宿主机需要；⚠️ 记得调 `size_limit_mb` |
| **sw_sync** | `SW_SYNC` | n | ⚠️⚠️ **绝对不要开** ——用户态可卡死内核驱动（§5.4） |
| sysfs stats | `DMABUF_SYSFS_STATS` | n | ⚠️ **不要开** ——自标 DEPRECATED 且有性能问题（03 §A8） |
| selftests | `DMABUF_SELFTESTS` | n | 不开；调试内核上按需 modprobe |
| `DMABUF_DEBUG` | — | ⚠️ `default y if DMA_API_DEBUG` | 调试内核开（能抓导入方偷看 page，03 §A3.2） |
| **`DMABUF_MOVE_NOTIFY`** | — | ⚠️ n | ⚠️⚠️ **关着就有"用户态 pin 无限内存"的安全问题**（03 §A3.1），但标着 EXPERIMENTAL |

⚠️⚠️ 最后一行是本篇最需要记住的一条：**这是一个"开也有风险、关也有风险"的选项**。
Kconfig help 自己承认它 EXPERIMENTAL，同时又承认关掉它就留着一个安全问题。
选择取决于**是否有不可信用户态能拿到 dma-buf fd**（有 → 倾向开）。

---

## 下一步读什么

| 想知道 | 去哪 |
|---|---|
| 照两个 heap 的样子写自己的导出方 | **05** §1 |
| CMA heap 的内存从哪来、DT 怎么配 | **06** |
| debugfs / tracepoint 实操，包括 sync 那两个文件 | **07** |
| 为什么 sw_sync 这种"用户态控制 fence"的设计从根上就危险 | **08** |
| 本篇每条结论的取证命令 | **99** |
