# shmem / tmpfs

> B 级文档 · `mm/shmem.c`（4,373 行）· 头文件 `include/linux/shmem_fs.h`（164 行）

## 1 定位

**功能**：shmem 是 tmpfs 的内核实现——基于页面（folio）的内存文件系统。它提供匿名内存共享（`MAP_SHARED`）、tmpfs 挂载点的文件存储后端、以及 dma-buf/memfd 等子系统的 backing store。支持大页（THP）、NUMA 内存策略、F_SEAL 密封和扩展属性。

**实现方式**：核心结构 `struct shmem_inode_info` 嵌入 VFS inode，通过 `spinlock_t lock` 保护分配计数（`alloced`/`swapped`）和密封标志。`struct shmem_sb_info` 管理超级块级别的配额（`max_blocks`/`max_inodes`）和默认内存策略。页面分配的核心路径是 `shmem_get_folio()`，通过 `enum sgp_type` 参数控制是否允许新分配（`SGP_CACHE`/`SGP_WRITE`/`SGP_FALLOC`）。`shmem_mapping()` 通过检查 `address_space` 的 `a_ops` 是否为 `shmem_aops` 来判断页面是否由 tmpfs 管理。

**应用场景**：`mm/memfd.c` 的 `memfd_create()` 系统调用底层用 shmem 分配匿名文件。`mm/mmap.c` 中 `shmem_zero_setup()` 为 `MAP_SHARED|MAP_ANONYMOUS` 映射提供 backing。GPU 驱动中 `drivers/gpu/drm/drm_gem.c`、`drivers/gpu/drm/ttm/ttm_tt.c` 和 `drivers/gpu/drm/i915/gem/i915_gem_shmem.c` 用 `shmem_file_setup()` 分配 GEM/TTM 对象的存储后端。`fs/kernfs/file.c` 用 shmem 支持 sysfs 文件的 mmap。`arch/x86/kernel/cpu/sgx/ioctl.c` 用 shmem 管理 SGX enclaves 的页面。全树共 8 个 C 文件调用 `shmem_file_setup()`。

## 2 核心数据结构

头文件：`include/linux/shmem_fs.h`

```c
struct shmem_inode_info {             /* :15 */
    spinlock_t        lock;
    unsigned int      seals;          /* F_SEAL_* 密封标志 */
    unsigned long     flags;
    unsigned long     alloced;        /* 已分配数据页 */
    unsigned long     swapped;        /* 交换出去的页 */
    struct list_head  shrinklist;     /* 可回收大页 inode 链表 */
    struct list_head  swaplist;       /* 可能在交换的链表 */
    struct shared_policy policy;      /* NUMA 内存策略 */
    struct simple_xattrs xattrs;      /* 扩展属性 */
    struct inode      vfs_inode;      /* 嵌入的 VFS inode */
};

struct shmem_sb_info {                /* :37 */
    unsigned long max_blocks;         /* 最大块数 */
    struct percpu_counter used_blocks;
    unsigned long max_inodes;
    unsigned long free_inodes;
    umode_t mode;                     /* 默认权限 */
    unsigned char huge;               /* 大页策略 */
    kuid_t uid; kgid_t gid;
    struct mempolicy *mpol;           /* 默认内存策略 */
};
```

`enum sgp_type`（`:108`）：`SGP_READ`、`SGP_NOALLOC`、`SGP_CACHE`、`SGP_WRITE`、`SGP_FALLOC` —— 控制 `shmem_get_folio` 的分配行为。

## 3 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `shmem_file_setup(name, size, flags)` | 创建 shmem 匿名文件 | `mm/shmem.c:4281`，返回 `struct file *` |
| `shmem_kernel_file_setup(name, size, flags)` | 内核用版本（不受用户 RLIMIT 限制） | |
| `shmem_read_mapping_page(mapping, index)` | 读取指定页 | inline，`shmem_fs.h:119` |
| `shmem_truncate_range(inode, start, end)` | 截断范围 | `mm/shmem.c:4211` |
| `shmem_zero_setup(vma)` | 设置 MAP_SHARED 匿名映射 | `mm/shmem.c:4305` |
| `shmem_get_folio(inode, index, foliop, sgp)` | 获取 folio | `mm/shmem.c:2069`，核心分配路径 |
| `shmem_mapping(mapping)` | 检查 address_space 是否为 shmem | inline，`shmem_fs.h:79` |
| `shmem_lock(file, lock, ucounts)` | 锁定/解锁（mlock 语义） | |

## 4 shmem 就是 tmpfs

当 `CONFIG_SHMEM=y` 时，tmpfs 文件系统完全由 `mm/shmem.c` 实现。`shmem_file_setup()` 在内部 `shm_mnt` 挂载点上创建匿名文件。`shmem_mapping()` 通过检查 `address_space` 的 `a_ops` 是否为 `shmem_aops` 来判断页面是否由 tmpfs 管理。

## 5 消费者

- **tmpfs 挂载**：`mount -t tmpfs` 直接使用
- **MAP_SHARED 匿名映射**：`shmem_zero_setup()` 为 `mmap(NULL, size, PROT_*, MAP_SHARED|MAP_ANONYMOUS, ...)` 提供 backing
- **dma-buf**：`dma-buf.c` 用 shmem 做系统内存 backing
- **memfd**：`memfd_create()` 系统调用底层用 shmem
- **GPU 驱动**：GEM 对象有时用 shmem_file_setup 分配

共 53 个文件引用 shmem。
